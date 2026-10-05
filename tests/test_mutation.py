"""Tests for skills/sigma-loop/scripts/mutation.py (issue #1935).

The two judged_when controls are here: the worked example from the issue is seen to FAIL the gate
and its strengthened version to pass it, and the "not measured" path is exercised by deliberately
breaking the tool rather than being asserted.
"""
import importlib.util
import pathlib
import subprocess
import tempfile

S = pathlib.Path(__file__).parent.parent / "skills" / "sigma-loop" / "scripts"


def _m():
    spec = importlib.util.spec_from_file_location("mutation", S / "mutation.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _tool(present=True):
    return (lambda _n: "/usr/bin/mutmut" if present else None)


def _runner(text, code=0):
    return lambda root, argv, timeout: (code, text)


# --- scope dimension 1: which lines the diff changed ------------------------------------------

def test_changed_lines_reads_the_NEW_file_numbering_from_the_hunk_header():
    diff = ("diff --git a/src/x.py b/src/x.py\n"
            "--- a/src/x.py\n+++ b/src/x.py\n"
            "@@ -10,3 +10,4 @@\n"
            " context\n"
            "+added_one\n"
            "+added_two\n"
            " context2\n")
    assert _m().changed_lines(diff) == {"src/x.py": {11, 12}}


def test_a_deleted_line_does_not_advance_the_new_file_counter():
    """A line that no longer exists cannot be mutated, and counting it would shift every later
    line number -- pointing the mutator at the wrong code entirely."""
    diff = ("diff --git a/src/x.py b/src/x.py\n--- a/src/x.py\n+++ b/src/x.py\n"
            "@@ -1,3 +1,2 @@\n context\n-removed\n+added\n")
    assert _m().changed_lines(diff) == {"src/x.py": {2}}


def test_changed_lines_is_empty_for_an_empty_diff():
    assert _m().changed_lines("") == {}


# --- the three states -------------------------------------------------------------------------

def test_a_kill_rate_at_or_above_the_threshold_is_verified():
    m = _m()
    r = m.run("/r", ["src/x.py"], min_kill_rate=0.8,
              run_cmd=_runner("8 killed, 2 survived"), which=_tool())
    assert r["verdict"] == m.VERIFIED and r["kill_rate"] == 0.8


def test_CONTROL_the_issues_own_weak_test_FAILS_the_gate_and_the_strong_one_passes():
    """judged_when 1, using the issue's own worked example.

    `assert is_valid_age(25) == True` leaves `0 <= age <= 1200`, `0 < age <= 120` and `return True`
    all alive -- 1 killed of 4. The four-assertion version kills all four."""
    m = _m()
    weak = m.run("/r", ["src/x.py"], min_kill_rate=0.8,
                 run_cmd=_runner("1 killed, 3 survived"), which=_tool())
    assert weak["verdict"] == m.UNVERIFIED
    assert weak["kill_rate"] == 0.25
    assert "3 mutant(s) survived" in weak["reason"]

    strong = m.run("/r", ["src/x.py"], min_kill_rate=0.8,
                   run_cmd=_runner("4 killed, 0 survived"), which=_tool())
    assert strong["verdict"] == m.VERIFIED and strong["kill_rate"] == 1.0


def test_CONTROL_a_missing_tool_is_absent_never_a_silent_pass():
    """judged_when 2, and the state this machine is actually in today: mutmut is not installed.

    #1935 requires "neither fail-open (silent pass, the exact ABSENT != PASS bug this product
    exists to surface) nor fail-closed (tool outage = org-wide merge freeze)"."""
    m = _m()
    r = m.run("/r", ["src/x.py"], which=_tool(present=False))
    assert r["verdict"] == m.ABSENT
    assert r["verdict"] != m.VERIFIED
    assert "not installed" in r["reason"]
    assert r["kill_rate"] is None, "a fabricated 0.0 would read as 'every mutant survived'"


def test_CONTROL_a_crashing_tool_is_absent_not_a_pass():
    """judged_when 2: the not-measured path exercised by deliberately breaking the tool."""
    m = _m()

    def boom(root, argv, timeout):
        raise OSError("mutmut exploded")

    r = m.run("/r", ["src/x.py"], run_cmd=boom, which=_tool())
    assert r["verdict"] == m.ABSENT and "could not run" in r["reason"] and r["code"] == "crash"


def test_CONTROL_a_timeout_is_absent_not_a_pass_and_not_a_merge_freeze():
    m = _m()

    def slow(root, argv, timeout):
        raise subprocess.TimeoutExpired(cmd="mutmut", timeout=timeout)

    r = m.run("/r", ["src/x.py"], run_cmd=slow, which=_tool())
    assert r["verdict"] == m.ABSENT and "timed out" in r["reason"]
    assert r["code"] == "timeout", "callers branch on the code, not on the wording"


def test_a_run_producing_no_mutants_is_absent_not_a_perfect_score():
    """0 killed of 0 is not a 100% kill rate. Dividing there would manufacture the best possible
    number out of no measurement at all."""
    m = _m()
    r = m.run("/r", ["src/x.py"], run_cmd=_runner("no mutants"), which=_tool())
    assert r["verdict"] == m.ABSENT and r["kill_rate"] is None


def test_nothing_changed_is_absent():
    assert _m().run("/r", [], which=_tool())["verdict"] == _m().ABSENT


# --- double scoping + waivers -----------------------------------------------------------------

def test_the_run_is_scoped_on_BOTH_dimensions_not_just_one():
    """#1935: "Scope BOTH dimensions: mutate only lines changed in the diff, AND run only the tests
    that cover each mutated line". One without the other still takes hours and gets switched off."""
    m = _m(); seen = {}

    def capture(root, argv, timeout):
        seen["argv"] = argv
        return (0, "1 killed, 0 survived")

    m.run("/r", ["src/x.py"], run_cmd=capture, which=_tool())
    assert "--paths-to-mutate" in seen["argv"], "not scoped to the changed paths"
    assert "--use-coverage" in seen["argv"], "not scoped to the covering tests"


def test_a_waiver_without_a_reason_is_refused():
    """The reason IS the artefact -- it is what turns a waiver into provenance rather than a silent
    exemption. A waiver without one is not recorded at all."""
    m = _m()
    class _W:
        calls = []
        @staticmethod
        def record(*a, **k): _W.calls.append((a, k)); return {"ok": True}
    assert m.waive("/s", "42", "mutant-7", "", _W) is None
    assert m.waive("/s", "42", "mutant-7", "   ", _W) is None
    assert _W.calls == []


def test_a_waiver_with_a_reason_is_recorded_as_a_queryable_witness():
    m = _m()
    class _W:
        calls = []
        @staticmethod
        def record(*a, **k): _W.calls.append((a, k)); return {"ok": True}
    m.waive("/s", "42", "mutant-7", "x < 10 -> x <= 9 is equivalent on integers", _W)
    assert len(_W.calls) == 1
    assert "equivalent-mutant waiver" in _W.calls[0][1]["detail"]
    assert "equivalent on integers" in _W.calls[0][1]["detail"]


def test_the_verdict_vocabulary_is_the_shared_one():
    m = _m()
    assert (m.VERIFIED, m.UNVERIFIED, m.ABSENT) == ("verified", "unverified", "absent")


# --- the parser against REAL mutmut output (#360) -----------------------------------------------
#
# The fixtures are the stdout of a real `mutmut run` and `mutmut results`, mutmut 2.5.1 on Python 3.12,
# captured on a one-function project (`def is_valid_age(age): return 0 <= age <= 120`, a test asserting
# is_valid_age(0), is_valid_age(120) and not is_valid_age(-1)). Commands, run in that project:
#   mutmut run --paths-to-mutate age.py --runner "<py> -m pytest -x -q tests/test_age.py"      -> 2.5.1-run.txt
#   mutmut run --simple-output (same arguments)                                                -> 2.5.1-run-simple.txt
#   mutmut results                                                                             -> 2.5.1-results.txt
#   mutmut results, after a run whose test asserted only is_valid_age(25)                      -> 2.5.1-results-ranges.txt
# Real outcome: 4 mutants, 3 killed, 1 survived. mutmut prints the emoji BEFORE its count and redraws
# one progress line with carriage returns, so only the LAST redraw is the answer.

FIX = pathlib.Path(__file__).parent / "fixtures" / "mutmut"


def _fx(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_the_real_default_output_parses_to_the_real_counts():
    assert _m()._parse_results(_fx("2.5.1-run.txt")) == (3, 1)


def test_the_real_simple_output_parses_to_the_real_counts():
    assert _m()._parse_results(_fx("2.5.1-run-simple.txt")) == (3, 1)


def test_the_parser_source_carries_no_mis_encoded_bytes():
    """The old pattern was written with mojibake instead of the emoji, which matched nothing mutmut
    prints. Plain ASCII source, emoji spelled as escapes, cannot be re-corrupted by an editor."""
    import inspect
    m = _m()
    for name in ("_parse_results", "_parse_counts", "parse_survivor_ids"):
        fn = getattr(m, name, None)
        assert fn is not None, name + " is missing"
        assert inspect.getsource(fn).isascii(), name + " carries non-ASCII source"


def test_run_turns_the_real_output_into_a_kill_rate():
    m = _m()
    r = m.run("/r", ["age.py"], min_kill_rate=0.8,
              run_cmd=_runner(_fx("2.5.1-run.txt"), 2), which=_tool())
    assert (r["killed"], r["survived"], r["kill_rate"]) == (3, 1, 0.75)
    assert r["verdict"] == m.UNVERIFIED


def test_survivor_ids_are_read_from_the_real_results_output():
    parse = getattr(_m(), "parse_survivor_ids", None)
    assert parse is not None, "parse_survivor_ids is missing"
    assert parse(_fx("2.5.1-results.txt")) == [4]
    assert parse(_fx("2.5.1-results-ranges.txt")) == [1, 2, 3, 4]


def test_the_argv_names_the_runner_the_tool_and_plain_output():
    m = _m(); seen = {}

    def capture(root, argv, timeout):
        seen["argv"] = argv
        return (0, "KILLED 1  TIMEOUT 0  SUSPICIOUS 0  SURVIVED 0  SKIPPED 0")

    try:
        r = m.run("/r", ["src/x.py"], run_cmd=capture, which=_tool(),
                  tool="/v/bin/mutmut", runner="/v/bin/python -m pytest -x -q tests/t.py")
    except TypeError as exc:
        r = None
        why = str(exc)
    assert r is not None, "run() does not accept tool= and runner=: " + (why if r is None else "")
    assert seen["argv"][0] == "/v/bin/mutmut"
    assert seen["argv"][seen["argv"].index("--runner") + 1] == "/v/bin/python -m pytest -x -q tests/t.py"
    assert "--simple-output" in seen["argv"]
    assert r["killed"] == 1


def test_timed_out_suspicious_and_skipped_mutants_are_counted_but_not_in_the_rate():
    m = _m()
    text = "4/9  KILLED 2  TIMEOUT 5  SUSPICIOUS 1  SURVIVED 2  SKIPPED 1"
    r = m.run("/r", ["x.py"], run_cmd=_runner(text), which=_tool())
    assert r["kill_rate"] == 0.5
    assert r.get("counts") == {"killed": 2, "timeout": 5, "suspicious": 1, "survived": 2, "skipped": 1}


def test_a_timeout_kills_the_whole_process_group_not_just_the_direct_child(tmp_path):
    """mutmut spawns the test runner through a shell. Killing only mutmut would leave that runner
    (and a mutated source file) behind, so the overrun kill must reach the whole group."""
    import os, signal, time
    pidfile = tmp_path / "child.pid"
    argv = ["sh", "-c", "sleep 30 & echo $! > %s; wait" % pidfile]
    try:
        began = time.time()
        try:
            _m()._default_run(str(tmp_path), argv, 1)
            raise AssertionError("expected a timeout")
        except subprocess.TimeoutExpired:
            pass
        assert time.time() - began < 10, "the kill waited for the grandchild to finish on its own"
        child = int(pidfile.read_text())
        deadline = time.time() + 5
        alive = True
        while alive and time.time() < deadline:
            try:
                os.kill(child, 0)
                time.sleep(0.1)
            except OSError:
                alive = False
        assert not alive, "the grandchild survived the timeout"
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except OSError:
                pass


def test_a_truncated_real_tally_is_absent_not_a_guess_from_the_legacy_wording():
    """`4/4  KILLED 3` with no survivor count must not be read as '4 killed': a half tally is nothing."""
    m = _m()
    r = m.run("/r", ["x.py"], run_cmd=_runner("4/4  KILLED 3  TIMEOUT 0"), which=_tool())
    assert r["verdict"] == m.ABSENT and r["kill_rate"] is None
    assert m._parse_results("4/4  KILLED 3") == (0, 0)


def test_an_interrupt_during_the_run_also_kills_the_whole_process_group(tmp_path):
    """Ctrl-C or SIGTERM mid-run must not leave mutmut (and its test runner) mutating the clone."""
    import os, signal, time
    pidfile = tmp_path / "child.pid"
    argv = ["sh", "-c", "sleep 30 & echo $! > %s; wait" % pidfile]

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    old = signal.signal(signal.SIGALRM, interrupt)
    try:
        signal.setitimer(signal.ITIMER_REAL, 1.0)
        try:
            _m()._default_run(str(tmp_path), argv, 60)
            raise AssertionError("expected the interrupt")
        except KeyboardInterrupt:
            pass
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        child = int(pidfile.read_text())
        deadline = time.time() + 5
        alive = True
        while alive and time.time() < deadline:
            try:
                os.kill(child, 0)
                time.sleep(0.1)
            except OSError:
                alive = False
        assert not alive, "the grandchild survived the interrupt"
    finally:
        signal.signal(signal.SIGALRM, old)
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except OSError:
                pass

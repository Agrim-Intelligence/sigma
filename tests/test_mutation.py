"""Tests for skills/agrim-loop/scripts/mutation.py (issue #1935).

The two judged_when controls are here: the worked example from the issue is seen to FAIL the gate
and its strengthened version to pass it, and the "not measured" path is exercised by deliberately
breaking the tool rather than being asserted.
"""
import importlib.util
import pathlib
import subprocess
import tempfile

S = pathlib.Path(__file__).parent.parent / "skills" / "agrim-loop" / "scripts"


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
    assert r["verdict"] == m.ABSENT and "could not run" in r["reason"]


def test_CONTROL_a_timeout_is_absent_not_a_pass_and_not_a_merge_freeze():
    m = _m()

    def slow(root, argv, timeout):
        raise subprocess.TimeoutExpired(cmd="mutmut", timeout=timeout)

    r = m.run("/r", ["src/x.py"], run_cmd=slow, which=_tool())
    assert r["verdict"] == m.ABSENT and "timed out" in r["reason"]


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

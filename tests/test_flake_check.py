"""Tests for skills/agrim-loop/scripts/flake_check.py (issue #1933).

The three controls the issue names are here, and the ORDER-DEPENDENCE one is the load-bearing one:
it proves the design choice (varied ordering) rather than the feature, because a naive 3x-same-order
loop agrees three times and credits a broken test.
"""
import importlib.util
import pathlib

S = pathlib.Path(__file__).parent.parent / "skills" / "agrim-loop" / "scripts"


def _fc():
    spec = importlib.util.spec_from_file_location("flake_check", S / "flake_check.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _fake(collect=(), results=()):
    """A runner that answers --collect-only with `collect`, then walks `results` per real run."""
    state = {"i": 0}

    def run(cwd, argv):
        if "--collect-only" in argv:
            return (0, "\n".join(collect) + ("\n" if collect else ""))
        r = results[state["i"]] if state["i"] < len(results) else (0, "")
        state["i"] += 1
        return r
    return run


def test_a_stable_test_passes_all_three_runs_and_is_credited():
    """CONTROL A (judged_when): a genuinely stable test is seen to pass all three and be credited."""
    r = _fc().check("/repo", ["tests/test_x.py"],
                    run=_fake(["tests/test_x.py::test_a"], [(0, ""), (0, ""), (0, "")]))
    assert r["verdict"] == "verified"
    assert r["disagreement"] is None
    assert len(r["runs"]) == 3


def test_a_coinflip_test_is_caught_and_marked_unverified():
    """CONTROL B (judged_when): passes ~50% of the time, so the runs disagree."""
    r = _fc().check("/repo", ["tests/test_x.py"],
                    run=_fake(["tests/test_x.py::test_a"],
                              [(0, ""), (1, "FAILED tests/test_x.py::test_a"), (0, "")]))
    assert r["verdict"] == "unverified"
    assert "test_a" in r["disagreement"]


def test_an_order_dependent_test_is_caught_BY_THE_SHUFFLE_specifically():
    """CONTROL C, and the one that proves the DESIGN rather than the feature.

    A naive 3x-same-order loop misses this entirely: the test passes alone and fails after a
    sibling, so three identical runs agree three times and credit a broken test. Only varied
    ordering catches it. The fake runner fails test_b iff test_a precedes it."""
    fc = _fc()
    seen = []

    def run(cwd, argv):
        if "--collect-only" in argv:
            return (0, "tests/test_x.py::test_a\ntests/test_x.py::test_b\n")
        ids = [a for a in argv if "::" in a]
        seen.append(tuple(ids))
        if ids.index("tests/test_x.py::test_a") < ids.index("tests/test_x.py::test_b"):
            return (1, "FAILED tests/test_x.py::test_b")
        return (0, "")

    r = fc.check("/repo", ["tests/test_x.py"], runs=8, run=run, seed=1)
    assert r["verdict"] == "unverified"
    assert len(set(seen)) > 1, "the runs never actually varied the order -- the shuffle is inert"


def test_every_run_gets_its_OWN_basetemp():
    """The tmp_path class of flake this repo already has an instance of -- an assertion that matched
    the TEST FUNCTION'S OWN NAME -- only reproduces when the temp dir differs between runs."""
    temps = []

    def run(cwd, argv):
        if "--collect-only" in argv:
            return (0, "tests/test_x.py::test_a\n")
        temps.append([a for a in argv if a.startswith("--basetemp=")][0])
        return (0, "")

    _fc().check("/repo", ["tests/test_x.py"], run=run)
    assert len(set(temps)) == 3, f"basetemp reused across runs: {temps}"


def test_no_changed_tests_is_absent_not_verified():
    """An honest absence. Crediting "verified" to a goal that changed no test would make the state
    meaningless -- the same ABSENT-vs-PASS distinction a downstream gap evaluator already draws."""
    assert _fc().check("/repo", [], run=_fake())["verdict"] == "absent"


def test_a_module_that_cannot_be_collected_is_absent_not_verified():
    """A collection error prints `ERROR test_x.py` with no `::`, so zero ids are found. Returning
    `verified` there would credit a suite that never ran a single test."""
    assert _fc().check("/repo", ["tests/test_x.py"],
                       run=_fake([], []))["verdict"] == "absent"


def test_a_consistently_FAILING_test_is_verified_not_called_flaky():
    """DISAGREEMENT is the signal, not failure. Three consistent failures are consistent -- a
    deterministically red test is not flaky, and marking it `unverified` would hide a real red
    behind a flakiness verdict, which is precisely how a check trains people to ignore it."""
    r = _fc().check("/repo", ["tests/test_x.py"],
                    run=_fake(["tests/test_x.py::test_a"],
                              [(1, "FAILED tests/test_x.py::test_a")] * 3))
    assert r["verdict"] == "verified"
    assert all(run_["exit"] == 1 for run_ in r["runs"])



def test_node_ids_are_rebuilt_against_the_path_we_asked_for_not_pytests_rootdir():
    """REGRESSION for a bug that made this whole check inert on any goal touching a sub-package.

    `--collect-only -q` prints ids relative to pytest's ROOTDIR, resolved per invocation from the
    nearest config file -- so `pkg/tests/test_x.py` comes back as `tests/test_x.py::test_a`.
    Fed back from the repo root that is a usage error (exit 4) on every run, and because all three
    runs AGREED on exit 4, the old agreement rule reported `verified`."""
    fc = _fc()
    asked = []

    def run(cwd, argv):
        if "--collect-only" in argv:
            return (0, "tests/test_x.py::test_a\n")        # rootdir-relative, missing pkg/
        asked.extend(a for a in argv if "::" in a)
        return (0, "")

    fc.check("/repo", ["pkg/tests/test_x.py"], runs=1, run=run)
    assert asked == ["pkg/tests/test_x.py::test_a"], asked


def test_runs_that_all_end_in_a_usage_error_are_absent_not_verified():
    """THE STRUCTURAL GUARD. Three runs agreeing on exit 4 agree that the harness is broken, not
    that the suite is deterministic. Reporting `verified` there is a check that measured nothing
    and said success -- which is precisely what happened on a real goal before this existed."""
    r = _fc().check("/repo", ["tests/test_x.py"],
                    run=_fake(["tests/test_x.py::test_a"], [(4, ""), (4, ""), (4, "")]))
    assert r["verdict"] == "absent", r


# ---------------------------------------- a killed run is NOT a disagreement (#1984 follow-up)

def test_a_run_KILLED_BY_A_SIGNAL_is_absent_not_a_disagreement():
    """A killed process is not evidence of nondeterminism.

    Found live on 2026-09-01. A verify whose command PASSED (exit 0, 4059 tests green) was reported
    `unverified` because the flake check's runs 2 and 3 exited -15 (SIGTERM) after being killed at a
    timeout. Run 1 exited 0, so the observations differed, so the agreement rule called the suite
    non-deterministic -- and `record done` was refused on a signal, not on a test.

    `_NOT_A_RESULT` already existed for exactly this class ("nothing was measured") but held only
    pytest's own 2/3/4/5. A negative exit is a signal, which is the most definitive "nothing was
    measured" there is."""
    r = _fc().check("/repo", ["tests/test_x.py"],
                    run=_fake(["tests/test_x.py::test_a"], [(0, ""), (-15, ""), (-15, "")]))
    assert r["verdict"] == "absent", r
    assert r["verdict"] != "unverified"


def test_a_single_killed_run_among_passes_is_still_absent_not_unverified():
    """The asymmetry that matters: one killed run poisons the comparison, because there is nothing
    to compare it WITH. Reporting `unverified` there blames the suite for the harness."""
    r = _fc().check("/repo", ["tests/test_x.py"],
                    run=_fake(["tests/test_x.py::test_a"], [(0, ""), (0, ""), (-9, "")]))
    assert r["verdict"] == "absent", r


def test_a_genuine_disagreement_is_STILL_unverified_the_control_against_over_widening():
    """THE CONTROL that stops the fix above from becoming a loophole. Real pass/fail disagreement
    between two COMPLETED runs must still be `unverified` -- otherwise this "fix" would have
    silently disabled the whole goal."""
    r = _fc().check("/repo", ["tests/test_x.py"],
                    run=_fake(["tests/test_x.py::test_a"],
                              [(0, ""), (1, "FAILED tests/test_x.py::test_a"), (0, "")]))
    assert r["verdict"] == "unverified", r


# ---------------------------------------- the scope is bounded, and says so when it bounds

def test_the_flake_check_refuses_an_unbounded_scope_and_SAYS_SO(capsys):
    """#1933 required "scoped to new/changed tests; unchanged tests run once" -- but nothing
    enforced a ceiling when the caller's diff was wrong. Live, a work record whose `base` pointed at
    a STALE local branch produced 94 changed test files: the check ran essentially the whole suite
    three times, for 32.8 MINUTES, and then reported a false verdict.

    NO SILENT CAP. AGENTS.md requires a per-item cost to state its behaviour at scale; a check that
    quietly truncated its own input would report on a subset while looking complete."""
    fc = _fc()
    many = ["tests/test_%d.py" % i for i in range(fc.MAX_SCOPED_FILES + 5)]
    r = fc.check("/repo", many, run=_fake(["tests/test_0.py::test_a"], [(0, ""), (0, ""), (0, "")]))
    assert r["verdict"] == "absent", r
    assert "scope" in (r["reason"] or "").lower(), r
    err = capsys.readouterr().err
    assert str(len(many)) in err, err


def test_a_scope_within_the_bound_runs_normally():
    """The control: the bound must not fire on ordinary goals."""
    fc = _fc()
    r = fc.check("/repo", ["tests/test_a.py", "tests/test_b.py"],
                 run=_fake(["tests/test_a.py::test_a"], [(0, ""), (0, ""), (0, "")]))
    assert r["verdict"] == "verified", r

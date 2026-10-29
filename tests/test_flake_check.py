"""Tests for skills/sigma-loop/scripts/flake_check.py (issue #1933).

The three controls the issue names are here, and the ORDER-DEPENDENCE one is the load-bearing one:
it proves the design choice (varied ordering) rather than the feature, because a naive 3x-same-order
loop agrees three times and credits a broken test.
"""
import concurrent.futures
import importlib.util
import inspect
import os
import pathlib
import random
import re
import subprocess
import threading
import time

import pytest

S = pathlib.Path(__file__).parent.parent / "skills" / "sigma-loop" / "scripts"
NODE = "tests/test_x.py::test_a"


def _fc():
    spec = importlib.util.spec_from_file_location("flake_check", S / "flake_check.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


@pytest.fixture(autouse=True)
def _pinned_machine(monkeypatch):
    """12 idle cores and no affinity on every host, so the derived bound is 3 for runs=3."""
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    monkeypatch.setattr(os, "getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)


def _run_index(argv):
    return int(re.search(r"--basetemp=\S*?sigma-flake-(\d+)-", " ".join(argv)).group(1))


def _poll(cond, cap=5.0):
    end = time.monotonic() + cap
    while not cond() and time.monotonic() < end:
        time.sleep(0.001)


def _fake(collect=(), results=()):
    """A runner that answers --collect-only with `collect`, then hands run i its own `results[i]`."""
    def run(cwd, argv):
        if "--collect-only" in argv:
            return (0, "\n".join(collect) + ("\n" if collect else ""))
        i = _run_index(argv)
        return results[i] if i < len(results) else (0, "")
    return run


class _RecordingPool(concurrent.futures.ThreadPoolExecutor):
    """Logs ("submit", i) in the CALLING thread, then keeps each future where a fake can read it."""
    events, futures = [], []

    def submit(self, *a, **k):
        self.events.append(("submit", len(self.futures)))
        self.futures.append(super().submit(*a, **k))
        return self.futures[-1]


def _record_pool(monkeypatch, events):
    monkeypatch.setattr(_RecordingPool, "events", events)
    monkeypatch.setattr(_RecordingPool, "futures", [])
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", _RecordingPool)
    return _RecordingPool.futures


def _gate_on_readback(monkeypatch):
    """An Event set by the first `Future.result` or after `as_completed` installs its waiter."""
    gate, base = threading.Event(), concurrent.futures._base
    real_result, real_install = base.Future.result, base._create_and_install_waiters

    def result(self, timeout=None):
        gate.set()
        return real_result(self, timeout)

    def install(*a, **k):
        waiter = real_install(*a, **k)
        gate.set()
        return waiter
    monkeypatch.setattr(concurrent.futures.Future, "result", result)
    monkeypatch.setattr(base, "_create_and_install_waiters", install)
    return gate


class FakePopen:
    made, log = [], []

    def __init__(self, argv, **kw):
        self.argv, self.returncode, self.stopped = argv, None, threading.Event()
        self.made.append(self)

    def terminate(self):
        self.log.append(("terminate", self))
        self.stopped.set()

    def kill(self):
        self.log.append(("kill", self))
        self.stopped.set()


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


# ---------------------------------------- #1571: the verdict is judged apart from how runs ran

def _j(*pairs):
    return _fc()._judge([{"exit": code, "failed": list(failed)} for code, failed in pairs])


def test_judge_one_red_run_in_three_is_unverified():
    """A red run in the MIDDLE: first and last agree, so only an all-runs comparison catches it."""
    assert callable(getattr(_fc(), "_judge", None))
    assert _j((0, []), (1, [NODE]), (0, [])) == ("unverified", "runs disagreed on: " + NODE)


def test_judge_an_unmeasured_run_is_absent_even_when_the_others_agree():
    """One killed run among two passes has nothing to be compared with: absent, not a verdict."""
    assert callable(getattr(_fc(), "_judge", None))
    assert _j((0, []), (0, []), (-15, []))[0] == "absent"


def test_judge_consistent_failure_is_verified():
    """Three identical reds are deterministic: verified, never hidden behind a flakiness verdict."""
    assert callable(getattr(_fc(), "_judge", None))
    assert _j((1, [NODE]), (1, [NODE]), (1, [NODE])) == ("verified", None)


def test_judge_compares_the_failed_sets_not_only_pass_fail():
    """All three runs fail, but not on the same test: that is a disagreement."""
    assert callable(getattr(_fc(), "_judge", None))
    assert _j((1, [NODE]), (1, ["tests/test_x.py::test_b"]), (1, [NODE]))[0] == "unverified"


def test_judge_an_exit_code_disagreement_alone_is_unverified():
    """No run names a failed test, yet one exited 1: the exit code alone is a disagreement."""
    assert callable(getattr(_fc(), "_judge", None))
    assert _j((0, []), (1, []), (0, [])) == ("unverified", "runs disagreed on: pass/fail status")


# ---------------------------------------- #1571: the bound is derived from the machine

def test_workers_collapse_to_serial_on_a_loaded_machine():
    """No free cores means one run at a time, never zero or a negative pool."""
    fc = _fc()
    assert callable(getattr(fc, "_workers", None))
    assert fc._workers(3, cpus=12, load1=21.8) == 1
    assert fc._workers(3, cpus=4, load1=3.2) == 1
    assert fc._workers(3, cpus=12, load1=10.0) == 2


def test_workers_never_exceed_runs_or_free_cores(monkeypatch):
    """Capped by runs, free cores and affinity; loaded is serial, never 0; explicit 0.0 load is real."""
    fc = _fc()
    assert callable(getattr(fc, "_workers", None))
    assert fc._workers(3, cpus=64, load1=0.0) == 3
    assert fc._workers(8, cpus=4, load1=0.0) == 4
    assert fc._workers(3, cpus=12, load1=9.5) == 2
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0, 1}, raising=False)
    assert fc._workers(3) == 2
    monkeypatch.setattr(os, "getloadavg", lambda: (20.0, 0.0, 0.0))
    assert fc._workers(3, cpus=12, load1=0.0) == 3


def test_workers_fail_toward_serial_when_cpu_or_load_is_unknown(monkeypatch):
    """An unknown cpu count or load average gives the old serial behaviour, never `runs`."""
    fc = _fc()
    assert callable(getattr(fc, "_workers", None))
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert fc._workers(3) == 1

    def no_load():
        raise OSError("no load average")
    monkeypatch.setattr(os, "getloadavg", no_load)
    assert fc._workers(3, cpus=12) == 1
    monkeypatch.delattr(os, "getloadavg")
    assert fc._workers(3, cpus=12) == 1


def test_explicit_workers_cannot_exceed_the_derived_bound(monkeypatch):
    """A caller can only LOWER the bound, and never below one."""
    fc = _fc()
    assert "workers" in inspect.signature(fc.check).parameters

    def used(workers):
        return fc.check("/repo", ["tests/test_x.py"], run=_fake(), node_ids=[NODE],
                        workers=workers).get("workers")
    monkeypatch.setattr(os, "getloadavg", lambda: (11.0, 0.0, 0.0))
    assert used(3) == 1
    monkeypatch.setattr(os, "getloadavg", lambda: (0.0, 0.0, 0.0))
    assert used(99) == 3
    assert used(0) == 1


# ---------------------------------------- #1571: concurrent runs keep their own identity

def test_results_are_keyed_by_run_index_not_completion_order(monkeypatch):
    """Runs are gated to finish 2, 1, 0, yet run 0's red must land in slot 0."""
    fc = _fc()
    assert "workers" in inspect.signature(fc.check).parameters
    futures = _record_pool(monkeypatch, [])
    gate = _gate_on_readback(monkeypatch)

    def finished(*ks):
        return lambda: len(futures) > 2 and all(futures[k].done() for k in ks)

    def run(cwd, argv):
        i = _run_index(argv)
        if i == 2:
            gate.wait(5)
        else:
            _poll(finished(*range(i + 1, 3)))
        return (1, "FAILED " + NODE) if i == 0 else (0, "")
    r = fc.check("/repo", ["tests/test_x.py"], run=run, node_ids=[NODE])
    assert r.get("workers") == 3
    assert [x["exit"] for x in r["runs"]] == [1, 0, 0]
    assert (r["verdict"], r["disagreement"]) == ("unverified", "runs disagreed on: " + NODE)


def test_runs_actually_overlap_when_workers_allow():
    """All three runs must be in flight at once: a 3-party barrier breaks if they are serial."""
    fc = _fc()
    assert "workers" in inspect.signature(fc.check).parameters
    barrier, lock, broken, flight = threading.Barrier(3, timeout=2), threading.Lock(), [], [0, 0]

    def run(cwd, argv):
        with lock:
            flight[0] += 1
            flight[1] = max(flight[1], flight[0])
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            broken.append(_run_index(argv))
        finally:
            with lock:
                flight[0] -= 1
        return (0, "")
    r = fc.check("/repo", ["tests/test_x.py"], run=run, node_ids=[NODE])
    assert r.get("workers") == 3
    assert broken == [] and flight[1] == 3, (broken, flight)


def test_orders_are_drawn_in_the_calling_thread_before_any_run_starts(monkeypatch):
    """Every shuffle happens in the caller's thread before the first submit, so a seed is a seed."""
    fc = _fc()
    assert "workers" in inspect.signature(fc.check).parameters
    events = []
    _record_pool(monkeypatch, events)

    class Recorded(random.Random):
        def shuffle(self, x, *a):
            events.append(("shuffle", threading.get_ident()))
            return super().shuffle(x, *a)
    monkeypatch.setattr(random, "Random", Recorded)
    r = fc.check("/repo", ["tests/test_x.py"], run=_fake(), seed=7,
                 node_ids=[NODE, "tests/test_x.py::test_b"])
    assert r.get("workers") == 3
    shuffles = [k for k, e in enumerate(events) if e[0] == "shuffle"]
    assert len(shuffles) == 3
    assert all(events[k][1] == threading.get_ident() for k in shuffles)
    assert ("submit", 0) in events
    assert max(shuffles) < min(k for k, e in enumerate(events) if e[0] == "submit")


def test_argv_isolates_cache_and_blocks_xdist():
    """Per-run cache_dir and basetemp; `-p xdist -n 0` iff xdist is present; no other `-p`."""
    fc = _fc()
    assert "xdist" in inspect.signature(fc.check).parameters
    for xdist, argvs in ((True, []), (False, [])):   # list.append is atomic under the GIL
        fc.check("/repo", ["tests/test_x.py"], run=lambda cwd, argv: argvs.append(argv) or (0, ""),
                 node_ids=[NODE], xdist=xdist)
        assert len(argvs) == 3 and all(a.count("-p") == a.count("-n") == xdist for a in argvs), xdist
        caches = {a[a.index("-o") + 1] for a in argvs if "-o" in a}
        assert len(caches) == 3 and all(c.startswith("cache_dir=") for c in caches)
        assert all(not xdist or a[a.index("-p"):a.index("-p") + 4] == ["-p", "xdist", "-n", "0"]
                   for a in argvs), xdist
        assert len({[x for x in a if x.startswith("--basetemp=")][0] for a in argvs}) == 3


def test_xdist_is_registered_before_n_0_so_a_plugin_off_repo_is_measured():
    """`-n 0` without `-p xdist` first is exit 4 under addopts `-p no:xdist` or autoload off."""
    fc = _fc()
    assert "xdist" in inspect.signature(fc.check).parameters

    def run(cwd, argv):
        k = argv.index("-n") if "-n" in argv else 0
        return (4, "") if k and argv[k - 2:k] != ["-p", "xdist"] else (0, "")
    r = fc.check("/repo", ["tests/test_x.py"], run=run, node_ids=[NODE], xdist=True)
    assert r["verdict"] != "absent", r


def test_has_xdist_probes_the_run_interpreter_and_a_fake_run_never_probes(monkeypatch):
    """`python3 -c "import xdist"` in root: rc 0 present, rc 1 or an unrunnable probe absent."""
    fc, calls = _fc(), []
    assert callable(getattr(fc, "_has_xdist", None))
    for outcome, want in ((0, True), (1, False), (OSError("no python3"), False)):
        def probe(argv, **kw):
            calls.append((argv, kw.get("cwd")))
            if isinstance(outcome, OSError):
                raise outcome
            return subprocess.CompletedProcess(argv, outcome)
        monkeypatch.setattr(fc.subprocess, "run", probe)
        assert fc._has_xdist("/repo") is want, outcome
    assert calls == [(["python3", "-c", "import xdist"], "/repo")] * 3
    fc.check("/repo", ["tests/test_x.py"], run=_fake(), node_ids=[NODE])
    assert len(calls) == 3


def test_a_real_repo_with_cache_addopts_is_still_measured(tmp_path, monkeypatch):
    """REAL processes: `addopts = --ff` needs the cache plugin registered, or every run exits 4."""
    fc, probes = _fc(), []
    monkeypatch.setattr(fc, "_has_xdist", lambda root, python="python3": probes.append(root) or False)
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --ff\n")
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    r = fc.check(str(tmp_path), ["test_ok.py"], workers=1)
    assert r["verdict"] == "verified", r
    assert probes == [str(tmp_path)]


# ---------------------------------------- #1571: an interrupt never waits out its siblings

def test_an_interrupt_cancels_the_pool_and_terminates_every_child_promptly(monkeypatch):
    """Ctrl-C (or SystemExit) in one run cancels the pool and terminates its siblings at once."""
    fc = _fc()
    assert "workers" in inspect.signature(fc.check).parameters
    for exc in (KeyboardInterrupt, SystemExit):
        class Child(FakePopen):
            made, log, boom = [], [], exc

            def communicate(self, input=None, timeout=None):
                if _run_index(self.argv) == 0:
                    _poll(lambda: len(self.made) >= 3)
                    raise self.boom
                self.wait()
                return ("", "")

            def wait(self, timeout=None):
                self.stopped.wait(10)
                self.returncode = -15
                return -15
        monkeypatch.setattr(subprocess, "Popen", Child)
        started = time.monotonic()
        with pytest.raises(exc):
            fc.check("/repo", ["tests/test_x.py"], node_ids=[NODE], xdist=False)
        assert time.monotonic() - started < 5, exc
        assert {1, 2} <= {_run_index(p.argv) for event, p in Child.log if event == "terminate"}, exc


def test_live_terminates_a_late_child_and_shares_one_grace_deadline(monkeypatch):
    """A child born after close() is terminated; every child is terminated before any wait."""
    fc = _fc()
    assert hasattr(fc, "_Live") and "live" in inspect.signature(fc._run).parameters
    live = fc._Live()

    class Late(FakePopen):
        made, log = [], []

        def __init__(self, argv, **kw):
            super().__init__(argv, **kw)
            live.close()

        def communicate(self, input=None, timeout=None):
            return ("", "")

        def wait(self, timeout=None):
            return self.returncode
    monkeypatch.setattr(subprocess, "Popen", Late)
    assert fc._run("/repo", ["python3", "-m", "pytest"], live=live) == (-15, "")
    assert len(Late.made) == 1 and ("terminate", Late.made[0]) in Late.log

    class Stubborn(FakePopen):
        made, log = [], []

        def terminate(self):
            self.log.append(("terminate", self))

        def wait(self, timeout=None):
            if timeout is None:
                return -9
            self.log.append(("wait", self, timeout))
            time.sleep(0.3)
            raise subprocess.TimeoutExpired(self.argv, timeout)
    live = fc._Live()
    a, b = Stubborn(["a"]), Stubborn(["b"])
    assert live.register(a) and live.register(b)
    live.close()
    kinds = [e[0] for e in Stubborn.log]
    assert kinds.count("terminate") == 2 and "wait" in kinds
    assert max(k for k, e in enumerate(kinds) if e == "terminate") < kinds.index("wait")
    assert {e[1] for e in Stubborn.log if e[0] == "kill"} == {a, b}
    timed = [e[2] for e in Stubborn.log if e[0] == "wait"]
    assert len(timed) == 2 and timed[1] < 4.9

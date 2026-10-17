"""#1650: the shared call-receipt helper `skills/sigma-loop/scripts/run_receipt.py`, extracted from the
#1551 reviewer receipt and used by `reviewer.py`, `loop.py verify` and `work.py merge`.

Acceptance clauses proved here (`.sdlc/acceptance/1650.md`):
  clause 2 -- ONE classifier reads a `running` receipt as live only while its pid is alive and before its
              derived deadline (`started_at + timeout_seconds + grace`); a dead pid, a past deadline or a
              malformed receipt reads `failed/not_completed`; the final write is nonce-guarded.
  clause 4 -- the concurrency seam: a kernel `flock` second acquirer is refused (deterministic, in-process).

The module is looked up with a guard, so while `run_receipt.py` does not exist every test fails by
AssertionError (never ImportError). The plan's control table (C4, C5, C7-C9, C14, C15, C16, C31) names the
single-clause break that must turn each test red.

Interface this file pins (the implementer's single target):
  receipt_path(sdlc_dir, goal, family, tag) -> <sdlc>/state/<family>/<goal>.<tag>.json (resolved)
  write(path, **fields)                  atomic; creates parent dirs
  classify(got, grace, now=None, alive=pid_alive) -> got | {**got, outcome:"failed", failure:"not_completed",
                                          detail:<names the dead pid / the deadline / "malformed">}
  read(path, grace) -> ("missing"|"unreadable"|"ok", dict|None|why)
  finish(path, run_id, label, **fields) -> bool wrote; calls the MODULE-LEVEL `write` (so a spy on
                                          `run_receipt.write` counts it); stderr text names `label` and says
                                          "not recorded" when it refuses
  poll(path, max_seconds, grace, run_id=None, sleep=, clock=) -> (state, got)
  try_lock(path) -> fd (non-inheritable) | None (held) | UNAVAILABLE ; calls `fcntl.flock` via the module
  clear(paths)                           removes each path, tolerant of missing ones
  constants: MAX_PID, CLOCK_SKEW, MAX_RUN_SECONDS, WAIT_DEFAULT_SECONDS (20), MISSING_RETRY (5), UNAVAILABLE
"""
import fcntl
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
GRACE = 60
_DROP = object()


def _rr():
    """The helper module loaded by path, or None while the file does not exist yet."""
    path = S / "run_receipt.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("run_receipt_1650", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _need():
    rr = _rr()
    assert rr is not None, "skills/sigma-loop/scripts/run_receipt.py does not exist yet (#1650)"
    return rr


def _dead_pid():
    q = subprocess.Popen([sys.executable, "-c", "pass"])
    q.wait()                                    # reaped: not a zombie, so kill(pid, 0) says no such process
    return q.pid


def _running(**over):
    fields = {"outcome": "running", "pid": os.getpid(), "started_at": int(time.time()),
              "timeout_seconds": 900, "run_id": "r1"}
    fields.update(over)
    return {k: v for k, v in fields.items() if v is not _DROP}


class _Clock:
    """A fake monotonic clock whose sleep advances it; `limit` turns a broken bound into a FAILURE, not a hang."""

    def __init__(self, limit, on_sleep=None):
        self.t, self.calls, self.limit, self.on_sleep = 0.0, 0, limit, on_sleep

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.calls += 1
        assert self.calls <= self.limit, "poll ignored its bound (%d sleeps)" % self.calls
        self.t += seconds
        if self.on_sleep:
            self.on_sleep(self.calls)


# ---- classifier ---------------------------------------------------------------------------------------

def test_classify_dead_pid_reads_failed():
    """Clause 2, pid clause (C7). The receipt is UNEXPIRED, so only the pid check can fail it. The injected
    `alive` is what the classifier asks (C31): True keeps a dead pid running, False kills a live one."""
    rr = _need()
    dead = _dead_pid()
    got = _running(pid=dead)
    out = rr.classify(got, GRACE)
    assert out["outcome"] == "failed" and out.get("failure") == "not_completed", out
    assert str(dead) in str(out.get("detail")), out                       # the detail names the dead pid
    assert out.get("run_id") == "r1" and out.get("pid") == dead, out       # every other field is kept
    assert rr.classify(got, GRACE, alive=lambda pid: True)["outcome"] == "running"
    live = _running()
    out = rr.classify(live, GRACE, alive=lambda pid: False)
    assert out["outcome"] == "failed" and str(os.getpid()) in str(out.get("detail")), out


def test_classify_past_deadline_reads_failed():
    """Clause 2, deadline clause (C8). The test's OWN live pid, so only the deadline clause can fail it; the
    boundary is exact: `now == started_at + timeout_seconds + grace` is already expired (a `<` -> `<=` break
    goes red), one second earlier is still running."""
    rr = _need()
    started, span = 1_700_000_000, 900
    got = _running(started_at=started, timeout_seconds=span)
    edge = started + span + GRACE
    assert rr.classify(got, GRACE, now=edge - 1)["outcome"] == "running"
    for now in (edge, edge + 1, edge + 10 ** 5):
        out = rr.classify(got, GRACE, now=now)
        assert out["outcome"] == "failed" and out.get("failure") == "not_completed", (now, out)
        assert "deadline" in str(out.get("detail")), out                    # the detail names the deadline
    assert rr.classify(got, GRACE + 5, now=edge)["outcome"] == "running"    # the grace is a parameter


def test_classify_live_reads_running_and_malformed_reads_failed():
    """Clause 2: a live, unexpired receipt stays `running` untouched; every malformed one fails closed and
    never raises, whatever the pid (C9: the clock-skew term, and the bool check on integers)."""
    rr = _need()
    now = int(time.time())
    live = _running(started_at=now)
    assert rr.classify(live, GRACE, now=now) == live
    final = {"outcome": "ok", "run_id": "r1", "result": "x"}
    assert rr.classify(final, GRACE) == final                              # any other outcome is returned as written
    max_run = getattr(rr, "MAX_RUN_SECONDS", None)
    skew = getattr(rr, "CLOCK_SKEW", None)
    assert isinstance(max_run, int) and isinstance(skew, int), (max_run, skew)
    malformed = {
        "pid_missing": {"pid": _DROP}, "pid_bool": {"pid": True}, "pid_zero": {"pid": 0},
        "pid_huge": {"pid": 2 ** 31},                                      # os.kill raises OverflowError, not OSError
        "started_missing": {"started_at": _DROP}, "started_bool": {"started_at": True},
        "timeout_missing": {"timeout_seconds": _DROP}, "timeout_bool": {"timeout_seconds": True},
        "timeout_over_cap": {"timeout_seconds": max_run + 1},
        "started_in_future": {"started_at": now + skew + 1000},
    }
    for name, over in sorted(malformed.items()):
        out = rr.classify(_running(**{"started_at": now, **over}), GRACE, now=now)
        assert out["outcome"] == "failed" and out.get("failure") == "not_completed", (name, out)


# ---- the nonce-guarded final write ----------------------------------------------------------------------

def test_finish_nonce_mismatch_is_not_recorded(tmp_path, monkeypatch, capsys):
    """Clause 1/2 (C4, C5): a final write lands ONLY when the on-disk receipt is still this run's; another
    run's receipt is not clobbered, a cleared one is not resurrected, an unreadable one is not overwritten.
    A spy on `write` counts it: exactly one call on success, none on any refusal."""
    rr = _need()
    path = tmp_path / "s" / "0001.verify.json"
    rr.write(path, **_running(run_id="mine"))
    writes = []
    real = rr.write

    def spy(p, **fields):
        writes.append(dict(fields))
        return real(p, **fields)
    monkeypatch.setattr(rr, "write", spy)
    capsys.readouterr()

    before = path.read_bytes()
    assert rr.finish(path, "theirs", "verify call", outcome="ok", result="x") is False
    assert path.read_bytes() == before and writes == []
    err = capsys.readouterr().err
    assert "not recorded" in err and "verify call" in err, err

    path.write_text("{ not json")                                          # unreadable is not this run's either
    assert rr.finish(path, "mine", "verify call", outcome="ok") is False
    assert path.read_text() == "{ not json" and writes == []
    rr.write(path, **_running(run_id="mine"))
    writes.clear()

    assert rr.finish(path, "mine", "verify call", outcome="ok", result="done") is True
    assert len(writes) == 1, writes                                         # exactly one write, through the module's `write`
    raw = json.loads(path.read_text())
    assert raw.get("outcome") == "ok" and raw.get("run_id") == "mine" and raw.get("result") == "done", raw

    path.unlink()                                                           # `work.py finish` cleared it meanwhile
    writes.clear()
    capsys.readouterr()
    assert rr.finish(path, "mine", "verify call", outcome="ok") is False
    assert not path.exists() and writes == []
    err = capsys.readouterr().err
    assert "cleared" in err and "not recorded" in err, err


# ---- poll ---------------------------------------------------------------------------------------------

def test_poll_states_final_failed_running_missing(tmp_path):
    """Clause 2/3 (C16): the wait loop's closed state set. `final` before `failed` before `running` is an
    ORDER, so each state is asserted on its own receipt; a nonce that is not the caller's is `other_run`."""
    rr = _need()
    sd = tmp_path / ".sdlc"
    path = rr.receipt_path(sd, "0001", "call-runs", "verify")
    assert pathlib.Path(path) == (sd / "state" / "call-runs" / "0001.verify.json").resolve(), path
    assert rr.WAIT_DEFAULT_SECONDS == 20 and rr.MISSING_RETRY == 5

    c = _Clock(limit=30)
    state, got = rr.poll(path, 20, GRACE, sleep=c.sleep, clock=c.clock)     # nothing written: a short retry, then missing
    assert state == "missing", state
    assert c.calls <= rr.MISSING_RETRY + 1, c.calls

    rr.write(path, outcome="ok", run_id="r1", result="VERIFIED exit=0")
    c = _Clock(limit=5)
    state, got = rr.poll(path, 5, GRACE, sleep=c.sleep, clock=c.clock)
    assert state == "final" and got.get("result") == "VERIFIED exit=0", (state, got)
    assert rr.poll(path, 5, GRACE, run_id="r1", sleep=c.sleep, clock=c.clock)[0] == "final"
    assert rr.poll(path, 5, GRACE, run_id="zzz", sleep=c.sleep, clock=c.clock)[0] == "other_run"

    rr.write(path, outcome="failed", failure="not_completed", run_id="r1")
    assert rr.poll(path, 5, GRACE, sleep=c.sleep, clock=c.clock)[0] == "failed"
    rr.write(path, **_running(pid=_dead_pid()))                              # a dead runner reads failed, never running
    assert rr.poll(path, 5, GRACE, sleep=c.sleep, clock=c.clock)[0] == "failed"

    rr.write(path, **_running())
    c = _Clock(limit=3 + 1)
    state, got = rr.poll(path, 3, GRACE, sleep=c.sleep, clock=c.clock)       # still running at the bound
    assert state == "running" and got.get("pid") == os.getpid(), (state, got)
    assert 1 <= c.calls <= 4, c.calls
    assert rr.poll(path, 3, GRACE, run_id="zzz", sleep=_Clock(limit=5).sleep, clock=c.clock)[0] == "other_run"

    def finish_meanwhile(calls):
        if calls == 2:
            rr.write(path, outcome="ok", run_id="r1", result="late")
    c = _Clock(limit=10 + 1, on_sleep=finish_meanwhile)
    state, got = rr.poll(path, 10, GRACE, sleep=c.sleep, clock=c.clock)     # a run that ends while we wait
    assert state == "final" and got.get("result") == "late", (state, got)

    path.write_text("{ not json")
    assert rr.poll(path, 2, GRACE, sleep=_Clock(limit=5).sleep, clock=_Clock(limit=5).clock)[0] == "unreadable"


# ---- the kernel lock ------------------------------------------------------------------------------------

def test_try_lock_second_acquirer_refused(tmp_path):
    """Clause 4, the deterministic seam (C14): flock is per open file description, so a second acquirer in the
    SAME process conflicts -- no race, no subprocess. Also: the fd is non-inheritable (R4: the verify child
    must not inherit the lock), UNAVAILABLE is a distinct value, and releasing lets the next acquirer win."""
    rr = _need()
    unavailable = getattr(rr, "UNAVAILABLE", None)
    assert unavailable is not None and not isinstance(unavailable, int), unavailable
    path = tmp_path / "call-runs" / "0001.verify.lock"                       # the parent does not exist yet
    first = rr.try_lock(path)
    try:
        assert isinstance(first, int) and not isinstance(first, bool), first
        assert os.get_inheritable(first) is False
        assert path.exists()
        assert rr.try_lock(path) is None                                      # held: refused, not "unavailable"
        assert rr.try_lock(path) is None
    finally:
        if isinstance(first, int):
            os.close(first)
    third = rr.try_lock(path)                                                # the kernel dropped it with the fd
    try:
        assert isinstance(third, int) and third is not unavailable, third
    finally:
        if isinstance(third, int):
            os.close(third)


def test_try_lock_retries_when_inode_swapped(tmp_path, monkeypatch):
    """Clause 4 (C15): a lock won on an inode that was unlinked between `open` and `flock` is no lock. The fd
    must still be the inode AT the path, else it is closed and the open+flock retried; bounded retries that
    keep losing the race never read as a win."""
    rr = _need()
    path = tmp_path / "call-runs" / "0001.verify.lock"
    path.parent.mkdir(parents=True)
    real = fcntl.flock
    calls = []

    def swap_once(fd, op):
        real(fd, op)
        calls.append(1)
        if len(calls) == 1:
            os.unlink(path)                                                   # a sweep unlinked it under us...
            path.write_text("")                                               # ...and a new file took its place
    monkeypatch.setattr(fcntl, "flock", swap_once)
    got = rr.try_lock(path)
    try:
        assert isinstance(got, int) and not isinstance(got, bool), got
        assert os.fstat(got).st_ino == os.stat(path).st_ino                   # the fd IS the inode at the path
        assert len(calls) >= 2, calls
    finally:
        if isinstance(got, int):
            os.close(got)

    always = []

    def swap_always(fd, op):
        real(fd, op)
        always.append(1)
        os.unlink(path)
        path.write_text("")
    monkeypatch.setattr(fcntl, "flock", swap_always)
    got = rr.try_lock(path)
    if isinstance(got, int):
        os.close(got)
    assert got is None or got is rr.UNAVAILABLE, got                          # never a win
    assert 2 <= len(always) <= 5, always                                      # bounded

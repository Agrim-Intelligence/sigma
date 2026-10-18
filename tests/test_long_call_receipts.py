"""#1650: `loop.py verify` and `work.py merge` write a `running` receipt before their long child work starts,
replace it with exactly one nonce-guarded final write, and a bounded `loop.py wait-call` verb lets a
yielding host (Codex `exec_command`, 30.2 s) find out instead of re-running. A live verify receipt refuses a
second concurrent verify (exit 6).

Acceptance clauses (`.sdlc/acceptance/1650.md`), by test:
  1 running receipt before the child, one nonce-guarded final write ... verify / merge receipt nodes
  2 one shared classifier, also used by reviewer.py ................... test_reviewer_runs_through_the_shared_helper
  3 `wait-call` exits 0 / 1 / 3 (+2) and both calls print the gesture .. wait_call nodes, launch gesture,
                                                                         merge first-word node
  4 a live verify receipt refuses a second verify .................... test_verify_refused_while_live_*
  5 landing.md REQUIRED PATTERN names the gesture ..................... test_landing_md_names_wait_call_*
  6 each clause has a red test ........................................ every node starts by asserting the new
                                                                         seam exists, so it is red by
                                                                         AssertionError, never ImportError.

No test launches a real model, suite, `gh` or the network: the verify child is a delegating `subprocess.run`
spy, the merge body a fake, every concurrency case deterministic and in-process (a lock fd held by the test).
Spies target `loop._rr` / `work._rr` / `rv._rr`, the module object the code under test actually uses (R2).

Interface decisions beyond the plan are listed in the final report of the test-writing phase; the ones a
reader needs here: the verify receipt is `state/call-runs/<goal>.verify.json` (+ `.verify.lock`), merge's is
`.merge.json`; final fields are `outcome`, `exit` (verify), `result`; the refusal is exit 6 with
`REFUSED exit=6`, `already running`, the holder's pid and run, the gesture, and `holder unknown` when the
receipt cannot name it; wait-call prints `RUNNING pid=.. run_id=.. elapsed=..s deadline_in=..s` at exit 3.
"""
import fcntl
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import types

import pytest

from test_goal_state_prune import _done, _files, _sweep, sdlc  # noqa: F401  (`sdlc` is the fixture)
from test_reviewer_failure_parks import GOAL as RV_GOAL, _project as _rv_project, _rv

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
ROOT = S.parent.parent.parent
CHILD = "echo sigma-1650-child"
_DROP = object()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
              "SIGMA_RUN_ID", "CLAUDE_CODE_REMOTE", "SIGMA_GH_GRAPHQL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SIGMA_ALLOW_COEXIST", "1")


def _mod(name):
    spec = importlib.util.spec_from_file_location(name + "_1650", S / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _loop():
    lp = _mod("loop")
    assert getattr(lp, "_rr", None) is not None, "loop.py does not bind the shared run_receipt module `_rr` yet (#1650)"
    return lp


def _work():
    wk = _mod("work")
    assert getattr(wk, "_rr", None) is not None, "work.py does not bind the shared run_receipt module `_rr` yet (#1650)"
    return wk


def _dead_pid():
    q = subprocess.Popen([sys.executable, "-c", "pass"])
    q.wait()
    return q.pid


# ---- fixtures: a verify-able project, receipts, probes ----------------------------------------------------

def _project(tmp_path, command=CHILD, work=False, enforce=False, with_record=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local",
                    "sigma.allowRepositoryShellCommands", "true"], check=True)
    sd = tmp_path / ".sdlc"
    (sd / "goals").mkdir(parents=True)
    (sd / "state").mkdir()
    cfg = {"verify": {"command": command, "enforce": enforce}, "work": {"enabled": work}}
    (sd / "config.json").write_text(json.dumps(cfg))
    (sd / "goals" / "0001.md").write_text("---\nid: 0001\nstatus: pending\n---\nx\n")
    if with_record:
        (sd / "acceptance").mkdir()
        (sd / "acceptance" / "0001.md").write_text(
            '---\nkind: "acceptance"\ngoal: "0001"\nverify_command: null\n---\n'
            "## Done when\n- [ ] it works\n- [ ] it is tested\n- [ ] it is documented\n")
    return str(sd)


def _runs(sd):
    return pathlib.Path(sd) / "state" / "call-runs"


def _rpath(sd, kind="verify", goal="0001"):
    return _runs(sd) / ("%s.%s.json" % (goal, kind))


def _lpath(sd, goal="0001"):
    return _runs(sd) / ("%s.verify.lock" % goal)


def _raw(sd, kind="verify"):
    """The receipt exactly as written, bypassing every classifier: None when absent."""
    try:
        return json.loads(_rpath(sd, kind).read_text())
    except FileNotFoundError:
        return None


def _plant(sd, kind="verify", **over):
    fields = {"outcome": "running", "pid": os.getpid(), "started_at": int(time.time()),
              "timeout_seconds": 14400, "run_id": "holder", "run": "runH", "kind": kind, "goal": "0001"}
    fields.update(over)
    path = _rpath(sd, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v for k, v in fields.items() if v is not _DROP}))
    return path


def _lock_held(sd):
    """Whether some OTHER open file description holds the verify lock right now."""
    p = _lpath(sd)
    if not p.exists():
        return False
    fd = os.open(str(p), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def _arm(m, lp, sd, code=0, exc=None):
    """Replace ONLY the verify child (the shell=True proving command) and `work.ensure_fresh`; every git call
    stays real. Each spy records what a reader would see at that instant: (receipt, lock held)."""
    seen = {"fresh": [], "child": []}

    def fresh(*a, **k):
        seen["fresh"].append((_raw(sd), _lock_held(sd)))
        return None
    m.setattr(lp.work, "ensure_fresh", fresh)
    real = subprocess.run

    def run(cmd, *a, **k):
        if k.get("shell") and cmd == CHILD:
            seen["child"].append((_raw(sd), _lock_held(sd)))
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(cmd, code, stdout="", stderr="")
        return real(cmd, *a, **k)
    m.setattr(subprocess, "run", run)
    return seen


# ---- verify: receipt, single final write, exception -------------------------------------------------------

def test_verify_receipt_written_before_ensure_fresh_and_child(tmp_path, monkeypatch, capsys):
    """Clause 1 (C1, C2): the running receipt, with the held lock, exists BEFORE `work.ensure_fresh` (which may
    auto-rebase for minutes) and before the child starts. Both gestures print the wait-call hint."""
    lp = _loop()
    sd = _project(tmp_path)
    monkeypatch.setenv("SIGMA_RUN_ID", "run-A")
    seen = _arm(monkeypatch, lp, sd)
    assert lp.verify_goal(sd, "0001") == 0
    assert len(seen["fresh"]) == 1 and len(seen["child"]) == 1, seen
    expect = getattr(lp, "VERIFY_RECEIPT_SECONDS", None)
    assert expect == 14400, expect
    run_ids = set()
    for where in ("fresh", "child"):
        raw, held = seen[where][0]
        assert isinstance(raw, dict) and raw.get("outcome") == "running", (where, raw)
        assert raw.get("pid") == os.getpid() and raw.get("kind") == "verify", (where, raw)
        assert isinstance(raw.get("started_at"), int) and not isinstance(raw.get("started_at"), bool), raw
        assert raw.get("timeout_seconds") == expect and raw.get("run") == "run-A", (where, raw)
        assert isinstance(raw.get("run_id"), str) and raw["run_id"], raw
        assert held is True, (where, "the verify lock is not held while the call runs")
        run_ids.add(raw["run_id"])
    assert len(run_ids) == 1, run_ids
    assert "wait-call" in capsys.readouterr().err
    assert _lock_held(sd) is False                                          # released once the call returned


def test_verify_final_write_is_single_and_carries_exit(tmp_path, monkeypatch):
    """Clause 1 (C3): exactly ONE running write and ONE final write per call, the final under the same nonce,
    carrying `exit` and a `result` whose first word is the verdict. A finished-but-red verify is still a
    `ok` (finished) receipt: wait-call routes on the stored exit, never on the receipt's outcome."""
    lp = _loop()
    rr = lp._rr
    real = rr.write
    for i, (code, word) in enumerate(((0, "VERIFIED"), (1, "FAILED"))):
        sd = _project(tmp_path / ("p%d" % i))
        _arm(monkeypatch, lp, sd, code=code)
        writes = []

        def spy(path, **fields):
            if str(path).endswith(".verify.json"):
                writes.append(dict(fields))
            return real(path, **fields)
        monkeypatch.setattr(rr, "write", spy)
        assert lp.verify_goal(sd, "0001") == code
        assert [w.get("outcome") for w in writes] == ["running", "ok"], writes
        assert writes[0].get("run_id") == writes[1].get("run_id"), writes
        final = _raw(sd)
        assert final.get("outcome") == "ok" and final.get("exit") == code, final
        assert final.get("run_id") == writes[0]["run_id"], final
        assert str(final.get("result", "")).split(" ")[0] == word and ("exit=%d" % code) in final["result"], final
        assert len(final["result"]) <= 500
        monkeypatch.undo()
        monkeypatch.setenv("SIGMA_ALLOW_COEXIST", "1")
        monkeypatch.setenv("SIGMA_RESOURCE_MODE", "off")   # undo() also reverts conftest's #1600 default


def test_verify_runner_exception_finalises_not_completed(tmp_path, monkeypatch, capsys):
    """Clause 1/2 (C6): anything raised after the running write finalises `failed/not_completed` under the
    same nonce and the exception propagates; the lock is released. R3: receipt I/O failures never replace the
    verify exit code or the in-flight exception."""
    lp = _loop()
    rr = lp._rr

    sd = _project(tmp_path / "a")
    _arm(monkeypatch, lp, sd, exc=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        lp.verify_goal(sd, "0001")
    raw = _raw(sd)
    assert raw and raw.get("outcome") == "failed" and raw.get("failure") == "not_completed", raw
    assert isinstance(raw.get("run_id"), str) and raw["run_id"], raw
    assert _lock_held(sd) is False
    monkeypatch.undo()
    monkeypatch.setenv("SIGMA_ALLOW_COEXIST", "1")
    monkeypatch.setenv("SIGMA_RESOURCE_MODE", "off")   # undo() also reverts conftest's #1600 default

    real = rr.write

    def fail_running(path, **fields):                                       # R3: the running write raises OSError
        if fields.get("outcome") == "running":
            raise OSError("disk full")
        return real(path, **fields)

    def fail_final(path, **fields):                                         # R3: the final write raises OSError
        if fields.get("outcome") != "running":
            raise OSError("disk full")
        return real(path, **fields)

    sd = _project(tmp_path / "b")
    seen = _arm(monkeypatch, lp, sd)
    monkeypatch.setattr(rr, "write", fail_running)
    capsys.readouterr()
    assert lp.verify_goal(sd, "0001") == 0                                  # proceeds like UNAVAILABLE
    assert len(seen["child"]) == 1
    assert "receipt" in capsys.readouterr().err.lower()                     # ...with a stderr advisory
    monkeypatch.undo()
    monkeypatch.setenv("SIGMA_ALLOW_COEXIST", "1")
    monkeypatch.setenv("SIGMA_RESOURCE_MODE", "off")   # undo() also reverts conftest's #1600 default

    sd = _project(tmp_path / "c")
    _arm(monkeypatch, lp, sd, code=0)
    monkeypatch.setattr(rr, "write", fail_final)
    capsys.readouterr()
    assert lp.verify_goal(sd, "0001") == 0                                  # the exit code is not replaced
    assert "receipt" in capsys.readouterr().err.lower()
    assert _lock_held(sd) is False
    monkeypatch.undo()
    monkeypatch.setenv("SIGMA_ALLOW_COEXIST", "1")
    monkeypatch.setenv("SIGMA_RESOURCE_MODE", "off")   # undo() also reverts conftest's #1600 default

    sd = _project(tmp_path / "d")
    _arm(monkeypatch, lp, sd, exc=RuntimeError("boom"))
    monkeypatch.setattr(rr, "write", fail_final)
    with pytest.raises(RuntimeError):                                       # not OSError: the in-flight exception wins
        lp.verify_goal(sd, "0001")
    assert _lock_held(sd) is False


# ---- verify: the concurrency refusal ----------------------------------------------------------------------

def test_verify_refused_while_live_regardless_of_run_id_then_proceeds_when_holder_dead(tmp_path, monkeypatch, capsys):
    """Clause 4 (C10-C13b, C14): the KERNEL lock is the liveness authority; the receipt only names the holder.
    (a) lock held + live receipt -> exit 6 whatever SIGMA_RUN_ID is (unset / same / other), nothing run;
    (b) lock held + NO or garbage receipt -> still 6, `holder unknown`; (c) lock FREE + a `running` receipt with
    the test's own LIVE pid (pid reuse) -> proceeds; (d) lock free + dead pid -> proceeds; (e) a refused verify
    leaves the holder's receipt byte-identical."""
    lp = _loop()
    rr = lp._rr
    sd = _project(tmp_path)
    seen = _arm(monkeypatch, lp, sd)
    lock = _lpath(sd)
    fd = rr.try_lock(lock)
    assert isinstance(fd, int), "the test could not take the verify lock itself: %r" % (fd,)
    ev = pathlib.Path(lp.state.evidence_path(sd, "0001"))
    try:
        planted = _plant(sd)
        before = planted.read_bytes()
        for run_id in (None, "runH", "other-run"):                          # (a)
            if run_id is None:
                monkeypatch.delenv("SIGMA_RUN_ID", raising=False)
            else:
                monkeypatch.setenv("SIGMA_RUN_ID", run_id)
            capsys.readouterr()
            assert lp.verify_goal(sd, "0001") == 6, run_id
            err = capsys.readouterr().err
            assert "REFUSED exit=6" in err and "already running" in err, err
            assert str(os.getpid()) in err and "runH" in err and "wait-call" in err, err   # names the holder
            assert planted.read_bytes() == before, "a refused verify touched the holder's receipt"   # (e)
            assert seen["fresh"] == [] and seen["child"] == [], seen
            assert not ev.exists()
        monkeypatch.delenv("SIGMA_RUN_ID", raising=False)
        planted.unlink()                                                    # (b) lock held, no receipt at all
        capsys.readouterr()
        assert lp.verify_goal(sd, "0001") == 6
        err = capsys.readouterr().err
        assert "REFUSED exit=6" in err and "holder unknown" in err, err
        assert not _rpath(sd).exists(), "a refused verify wrote a receipt"
        _rpath(sd).write_text("{ not json")                                 # (b) lock held, garbage receipt
        capsys.readouterr()
        assert lp.verify_goal(sd, "0001") == 6
        err = capsys.readouterr().err
        assert "holder unknown" in err, err
        assert _rpath(sd).read_text() == "{ not json"
        assert seen["fresh"] == [] and seen["child"] == []
    finally:
        os.close(fd)
    _plant(sd)                                                              # (c) free lock + live own pid (pid reuse)
    assert lp.verify_goal(sd, "0001") == 0
    assert len(seen["child"]) == 1, seen
    assert _raw(sd).get("run_id") != "holder" and _raw(sd).get("outcome") == "ok", _raw(sd)
    _plant(sd, pid=_dead_pid())                                             # (d) free lock + dead holder
    assert lp.verify_goal(sd, "0001") == 0
    assert len(seen["child"]) == 2, seen


# ---- verify: early returns and the no-lock platform ---------------------------------------------------------

@pytest.mark.parametrize("case", ["unsafe_goal", "invalid_acceptance", "no_command", "enforce_needs_record",
                                  "normal_positive_control"])
def test_verify_early_returns_leave_no_receipt_and_no_lock(tmp_path, monkeypatch, case):
    """Clause 1 (C26, order not membership): every return BEFORE the guard begins (exit 2 unsafe goal, exit 2
    invalid acceptance, exit 3 NO-COMMAND, exit 4 enforce-needs-record) leaves `state/call-runs/` without a
    receipt or a lock; the positive-control param proves the guard exists and writes a FINAL receipt, so the
    four absences are not vacuous."""
    lp = _loop()                                                            # also: the guard exists at all
    sd = _project(tmp_path,
                  command="" if case == "no_command" else CHILD,
                  work=(case == "enforce_needs_record"), enforce=(case == "enforce_needs_record"))
    seen = _arm(monkeypatch, lp, sd)
    goal = "0001"
    if case == "unsafe_goal":
        goal = "../escape"
    elif case == "invalid_acceptance":
        def bad_digest(*a, **k):
            raise ValueError("bad acceptance")
        monkeypatch.setattr(lp.state, "acceptance_module", lambda: types.SimpleNamespace(digest=bad_digest))
    code = lp.verify_goal(sd, goal)
    names = sorted(p.name for p in _runs(sd).glob("*")) if _runs(sd).exists() else []
    if case == "normal_positive_control":
        assert code == 0 and "0001.verify.json" in names, (code, names)
        assert _raw(sd).get("outcome") == "ok", _raw(sd)
        return
    assert code == {"unsafe_goal": 2, "invalid_acceptance": 2, "no_command": 3, "enforce_needs_record": 4}[case], code
    assert names == [], names                                               # no receipt, no .lock
    assert seen["fresh"] == [] and seen["child"] == []


def test_verify_without_fcntl_runs_unrefused_with_stderr_advisory(tmp_path, monkeypatch, capsys):
    """Clause 4 (C27): no lock, no refusal. Where the kernel lock is unavailable (`try_lock` -> UNAVAILABLE)
    verify runs exactly as before, still writes its running receipt, never consults a live-pid receipt to
    refuse, and says so loudly on stderr."""
    lp = _loop()
    rr = lp._rr
    unavailable = getattr(rr, "UNAVAILABLE", None)
    assert unavailable is not None, "run_receipt.UNAVAILABLE does not exist yet"
    sd = _project(tmp_path)
    seen = _arm(monkeypatch, lp, sd)
    monkeypatch.setattr(rr, "try_lock", lambda path: unavailable)
    _plant(sd)                                                              # a live-pid receipt must NOT refuse
    capsys.readouterr()
    assert lp.verify_goal(sd, "0001") == 0
    err = capsys.readouterr().err
    assert "concurrency guard unavailable" in err, err
    assert len(seen["child"]) == 1, seen
    raw, _held = seen["child"][0]
    assert raw and raw.get("outcome") == "running" and raw.get("run_id") != "holder", raw   # it still wrote one
    assert _raw(sd).get("outcome") == "ok"


# ---- merge --------------------------------------------------------------------------------------------------

def _msdlc(tmp_path, work=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    sd = tmp_path / ".sdlc"
    (sd / "state").mkdir(parents=True)
    (sd / "config.json").write_text(json.dumps({"work": {"enabled": work}}))
    return str(sd)


def _gesture(err, kind):
    m = re.search(r"(\S+loop\.py) wait-call (\S+) (\S+) (verify|merge)\b", err)
    assert m, "no wait-call gesture on stderr: %r" % err
    assert m.group(4) == kind, m.group(0)
    return m


def test_merge_receipt_written_before_merge_body_and_final_carries_result(tmp_path, monkeypatch):
    """Clause 1 (C17, C19): the running merge receipt is on disk when `merge()` starts; exactly one final
    write follows under the same nonce carrying `result=` (capped at 500); an exception finalises
    `failed/not_completed` and propagates."""
    wk = _work()
    merge_with_receipt = getattr(wk, "merge_with_receipt", None)
    assert merge_with_receipt is not None, "work.merge_with_receipt does not exist yet (#1650)"
    rr = wk._rr
    sd = _msdlc(tmp_path)
    seen = []

    def fake_merge(sdlc_dir, config, goal, run=None, sleep=None):
        seen.append(_raw(sd, "merge"))
        return "PR #7 merged (squash)"
    monkeypatch.setattr(wk, "merge", fake_merge)
    writes, real = [], rr.write

    def spy(path, **fields):
        if str(path).endswith(".merge.json"):
            writes.append(dict(fields))
        return real(path, **fields)
    monkeypatch.setattr(rr, "write", spy)
    out = merge_with_receipt(sd, {}, "0001", sleep=lambda s: None)
    assert out == "PR #7 merged (squash)"
    raw = seen[0]
    budget = wk.merge_budget_seconds()
    assert isinstance(raw, dict) and raw.get("outcome") == "running" and raw.get("kind") == "merge", raw
    assert raw.get("pid") == os.getpid() and raw.get("timeout_seconds") == budget, raw
    assert isinstance(raw.get("started_at"), int) and isinstance(raw.get("run_id"), str) and raw["run_id"], raw
    assert [w.get("outcome") for w in writes] == ["running", "ok"], writes
    final = _raw(sd, "merge")
    assert final.get("outcome") == "ok" and final.get("result") == "PR #7 merged (squash)", final
    assert final.get("run_id") == raw["run_id"], (final, raw)

    long_park = "PARK: " + "x" * 700                                         # the receipt copy is capped, the return is not
    monkeypatch.setattr(wk, "merge", lambda *a, **k: long_park)
    assert merge_with_receipt(sd, {}, "0001") == long_park
    assert 0 < len(_raw(sd, "merge")["result"]) <= 500

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(wk, "merge", boom)
    with pytest.raises(RuntimeError):
        merge_with_receipt(sd, {}, "0001")
    raw = _raw(sd, "merge")
    assert raw.get("outcome") == "failed" and raw.get("failure") == "not_completed", raw


def test_merge_first_word_unchanged_hint_stderr_only_and_live_receipt_warns(tmp_path, monkeypatch, capsys):
    """Clause 1/3 (C18, C29): the string `merge()` returns, and `main` prints on stdout, is byte-identical (its
    first word is the router); the wait-call hint rides stderr only; a pre-planted LIVE merge receipt produces
    one stderr warning and the merge STILL runs (merge is idempotent)."""
    wk = _work()
    merge_with_receipt = getattr(wk, "merge_with_receipt", None)
    assert merge_with_receipt is not None, "work.merge_with_receipt does not exist yet (#1650)"
    sd = _msdlc(tmp_path)
    calls = []
    monkeypatch.setattr(wk, "merge", lambda *a, **k: calls.append(1) or "PARK: needs a human")
    capsys.readouterr()
    out = merge_with_receipt(sd, {}, "0001")
    cap = capsys.readouterr()
    assert out == "PARK: needs a human" and cap.out == "", (out, cap.out)
    m = _gesture(cap.err, "merge")
    assert pathlib.Path(m.group(1)).is_absolute() and pathlib.Path(m.group(1)).resolve() == (S / "loop.py").resolve()
    assert m.group(2) == str(pathlib.Path(sd).resolve()) and m.group(3) == "0001", m.group(0)

    # The CLI: stdout is exactly the returned string; the hint stays on stderr.
    sd2 = _msdlc(tmp_path / "cli", work=True)
    real_load = wk._load

    def no_loop(name):
        if name == "loop":
            raise RuntimeError("no watcher in this test")
        return real_load(name)
    monkeypatch.setattr(wk, "_load", no_loop)
    monkeypatch.setattr(wk, "merge", lambda *a, **k: "PR #7 merged (squash)")
    capsys.readouterr()
    assert wk.main(["work.py", "merge", sd2, "0001"]) == 0
    cap = capsys.readouterr()
    assert cap.out == "PR #7 merged (squash)\n", cap.out
    assert "wait-call" in cap.err and "PR #7 merged" not in cap.err
    assert _raw(sd2, "merge").get("outcome") == "ok", _raw(sd2, "merge")

    # A live receipt: one warning, the merge still runs.
    calls.clear()
    monkeypatch.setattr(wk, "merge", lambda *a, **k: calls.append(1) or "PR #7 merged (squash)")
    _plant(sd, "merge", run_id="old")
    capsys.readouterr()
    assert merge_with_receipt(sd, {}, "0001") == "PR #7 merged (squash)"
    err = capsys.readouterr().err
    assert calls == [1] and "live receipt" in err and str(os.getpid()) in err, (calls, err)
    assert _raw(sd, "merge").get("run_id") not in (None, "old"), _raw(sd, "merge")


def test_merge_deadline_derived_from_constants(monkeypatch):
    """Clause 1 (C20): the merge receipt's deadline is DERIVED from `gate()`'s retry constants, read at call
    time -- never a hard-coded copy of the documented figure. The expected floor is computed here, independently,
    for each patched `BEHIND_REBASES`; every extra rebase must add at least one full gate worst case, and
    doubling `PENDING_INTERVAL` must move the value."""
    wk = _work()
    budget = getattr(wk, "merge_budget_seconds", None)
    assert budget is not None, "work.merge_budget_seconds does not exist yet (#1650)"

    def gate():
        waits = sum(wk.UNKNOWN_BACKOFF * 2 ** i for i in range(wk.UNKNOWN_ATTEMPTS - 1))
        return waits + wk.PENDING_ATTEMPTS * wk.PENDING_INTERVAL

    values = {}
    for k in (0, 3, 5):
        monkeypatch.setattr(wk, "BEHIND_REBASES", k)
        values[k] = budget()
        assert isinstance(values[k], int) and not isinstance(values[k], bool), values[k]
        assert (k + 1) * gate() <= values[k] <= 86400, (k, values[k], (k + 1) * gate())
        assert values[k] not in (1350, 1350000), values[k]                  # the documented figure, hard-coded
    assert values[3] - values[0] >= 3 * gate() and values[5] - values[3] >= 2 * gate(), values
    monkeypatch.setattr(wk, "BEHIND_REBASES", 3)
    before = budget()
    monkeypatch.setattr(wk, "PENDING_INTERVAL", wk.PENDING_INTERVAL * 2)
    assert budget() - before >= 4 * wk.PENDING_ATTEMPTS * (wk.PENDING_INTERVAL // 2), (before, budget())


# ---- wait-call ----------------------------------------------------------------------------------------------

def _wc(lp, sd, kind, *extra, goal="0001", capsys=None):
    capsys.readouterr()
    code = lp.main(["loop.py", "wait-call", sd, goal, kind, *extra])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_wait_call_exit_0_on_final_prints_result(tmp_path, capsys):
    """Clause 3: a FINAL receipt exits 0 and prints the stored result so the caller routes exactly as on a
    direct call -- including a red verify (finished is not passed) and a merge PARK. A matching `--run-id`
    reads the same."""
    lp = _loop()
    sd = _project(tmp_path)
    _plant(sd, outcome="ok", exit=0, result="VERIFIED exit=0 evidence=/x/0001.json", pid=_DROP, started_at=_DROP,
           timeout_seconds=_DROP)
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", capsys=capsys)
    assert code == 0 and "VERIFIED exit=0" in out, (code, out, err)
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", "--run-id", "holder", capsys=capsys)
    assert code == 0, (code, out, err)
    _plant(sd, outcome="ok", exit=1, result="FAILED exit=1 evidence=/x/0001.json", pid=_DROP, started_at=_DROP,
           timeout_seconds=_DROP)
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", capsys=capsys)
    assert code == 0 and "FAILED" in out and "exit=1" in out, (code, out, err)
    _plant(sd, "merge", outcome="ok", result="PARK: needs a human", pid=_DROP, started_at=_DROP,
           timeout_seconds=_DROP)
    code, out, err = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 0 and "PARK: needs a human" in out, (code, out, err)


def test_wait_call_exit_1_on_dead_pid_expired_and_nonce_mismatch(tmp_path, capsys):
    """Clause 2/3 (C16): a dead pid, a past deadline, a malformed running receipt, or a runner that finalised
    `failed/not_completed` all exit 1 with the reason on screen and what is safe to do next (verify may be
    re-run; a merge points at landing.md's distinction, R8). The nonce guard itself (a stale final write is not
    recorded) is proved at the helper in `test_finish_nonce_mismatch_is_not_recorded`; an explicit `--run-id`
    that is not the receipt's is exit 2, below."""
    lp = _loop()
    sd = _project(tmp_path)
    dead = _dead_pid()
    _plant(sd, pid=dead)
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and str(dead) in out + err, (code, out, err)
    assert "re-run" in (out + err), out + err
    _plant(sd, started_at=int(time.time()) - 14400 - 3600)
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and "deadline" in out + err, (code, out, err)
    _plant(sd, pid=_DROP)                                                   # malformed running receipt
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", capsys=capsys)
    assert code == 1, (code, out, err)
    _plant(sd, outcome="failed", failure="not_completed", detail="the runner raised", pid=_DROP)
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and "not_completed" in out + err, (code, out, err)
    _plant(sd, "merge", pid=dead)
    code, out, err = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and "landing.md" in out + err and "re-run" in out + err, (code, out, err)


def test_wait_call_exit_3_while_running(tmp_path, capsys):
    """Clause 3 (C16): a live, unexpired receipt at the bound is exit 3 with `RUNNING pid=.. run_id=..
    elapsed=..s deadline_in=..s` -- the reviewer's shape -- and the call returns at the bound, not later."""
    lp = _loop()
    sd = _project(tmp_path)
    for kind in ("verify", "merge"):
        _plant(sd, kind, started_at=int(time.time()) - 5)
        started = time.monotonic()
        code, out, err = _wc(lp, sd, kind, "--max-seconds", "1", capsys=capsys)
        assert code == 3 and time.monotonic() - started < 10, (kind, code, out, err)
        assert re.match(r"RUNNING pid=%d run_id=holder elapsed=\d+s deadline_in=\d+s" % os.getpid(), out), out


def test_wait_call_exit_2_on_missing_unsafe_and_usage(tmp_path, capsys):
    """Clause 3: exit 2 is "no answer to give": no receipt (its text says to CHECK THE PR / goal status before
    acting, never to re-run blindly -- R7b, C30), an unsafe goal, bad usage, or a receipt of another run."""
    lp = _loop()
    sd = _project(tmp_path)
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", capsys=capsys)
    assert code == 2 and "Check the PR" in out + err, (code, out, err)
    code, out, err = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 2 and "Check the PR" in out + err, (code, out, err)
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", goal="../escape", capsys=capsys)
    assert code == 2, (code, out, err)
    capsys.readouterr()
    assert lp.main(["loop.py", "wait-call", sd, "0001"]) == 2                  # too few arguments
    assert lp.main(["loop.py", "wait-call", sd, "0001", "bogus"]) == 2         # not verify|merge
    assert lp.main(["loop.py", "wait-call", sd, "0001", "verify", "--max-seconds", "abc"]) == 2
    capsys.readouterr()
    _plant(sd)                                                              # a live receipt of ANOTHER run
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", "--run-id", "someone-else", capsys=capsys)
    assert code == 2 and "different run" in out + err, (code, out, err)


def test_launch_gesture_is_absolute_for_a_relative_sdlc(tmp_path, monkeypatch, capsys):
    """Clause 3 (C21): verify prints the wait gesture on launch, with the ABSOLUTE `loop.py` path and the
    RESOLVED sdlc dir even when the caller typed a relative `.sdlc`, so it works from any cwd and reads the
    files the writer wrote. The printed gesture is then run from elsewhere."""
    lp = _loop()
    sd = _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    _arm(monkeypatch, lp, sd)
    capsys.readouterr()
    assert lp.verify_goal(".sdlc", "0001") == 0
    err = capsys.readouterr().err
    m = _gesture(err, "verify")
    assert pathlib.Path(m.group(1)).is_absolute() and pathlib.Path(m.group(1)).resolve() == (S / "loop.py").resolve(), m.group(0)
    assert pathlib.Path(m.group(2)).is_absolute() and m.group(2) == str((tmp_path / ".sdlc").resolve()), m.group(0)
    assert m.group(3) == "0001", m.group(0)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    capsys.readouterr()
    assert lp.main(["loop.py", "wait-call", m.group(2), m.group(3), m.group(4), "--max-seconds", "1"]) == 0
    assert "VERIFIED" in capsys.readouterr().out


# ---- lifetime: finish, prune, clear ------------------------------------------------------------------------

@pytest.mark.parametrize("case", ["finish_clears", "finish_spares_live", "prune_sweeps", "prune_spares_held_lock"])
def test_finish_and_prune_clear_call_run_files(sdlc, tmp_path, case):
    """Lifetime (C22, C28, R7): `work.finish` clears a goal's call-run receipts and lock with its record --
    EXCEPT a receipt that still classifies `running` with a live pid (a merge's own post-land steps can reach
    `finish`; deleting it would drop the final write and wait-call would exit 2 after a SUCCESSFUL merge) --
    `goal_state_prune` sweeps all three families, `run_receipt.clear` removes receipt and lock, and prune
    never unlinks a lock that is held (a verify is running)."""
    wk = _work()
    rr = wk._rr
    runs = sdlc / "state" / "call-runs"
    runs.mkdir(parents=True, exist_ok=True)
    vjson, mjson, vlock = runs / "0001.verify.json", runs / "0001.merge.json", runs / "0001.verify.lock"
    now = int(time.time())

    def running(pid):
        return json.dumps({"outcome": "running", "pid": pid, "started_at": now, "timeout_seconds": 14400,
                           "run_id": "r1", "kind": "verify"})

    if case.startswith("finish"):
        wt = tmp_path / "wt"
        wt.mkdir()
        wk._save(str(sdlc), "0001", {"worktree": str(wt), "branch": "sdlc/0001", "base": "main", "remote": "origin"})
        fake_run = lambda cwd, argv, **k: ""                                # noqa: E731
        mjson.write_text(json.dumps({"outcome": "ok", "run_id": "r2", "result": "PR #7 merged"}))
        if case == "finish_clears":
            vjson.write_text(running(_dead_pid()))                          # a dead runner's receipt
            vlock.write_text("")
            assert wk.finish(str(sdlc), {}, "0001", run=fake_run).startswith("removed")
            assert not vjson.exists() and not mjson.exists() and not vlock.exists(), sorted(p.name for p in runs.iterdir())
            return
        vjson.write_text(running(os.getpid()))                              # a LIVE runner (this process)
        fd = rr.try_lock(vlock)
        assert isinstance(fd, int), fd
        try:
            kept = vjson.read_bytes()
            assert wk.finish(str(sdlc), {}, "0001", run=fake_run).startswith("removed")
            assert vjson.exists() and vjson.read_bytes() == kept, "finish deleted a live receipt"
            assert vlock.exists(), "finish unlinked a held lock"
            assert not mjson.exists(), "finish left a final merge receipt behind"
        finally:
            os.close(fd)
        return

    _done(sdlc, "10")
    names = ("call-runs/{g}.verify.json", "call-runs/{g}.merge.json", "call-runs/{g}.verify.lock")
    if case == "prune_sweeps":
        made = _files(sdlc, "10", families=names)
        out = _sweep(tmp_path)
        assert not any(p.exists() for p in made), [p.name for p in made if p.exists()]
        assert len(out["removed"]) == len(made), out
        a, b = tmp_path / "x.verify.json", tmp_path / "x.verify.lock"
        a.write_text("{}")
        b.write_text("")
        rr.clear([a, b, tmp_path / "never-existed.json"])                   # missing paths are tolerated
        assert not a.exists() and not b.exists()
        return
    made = _files(sdlc, "10", families=names)                               # prune_spares_held_lock
    p10 = sdlc / "state" / "call-runs"
    (p10 / "10.verify.json").write_text(running(os.getpid()))
    os.utime(p10 / "10.verify.json", (time.time() - 10 * 86400,) * 2)
    fd = rr.try_lock(p10 / "10.verify.lock")
    assert isinstance(fd, int), fd
    try:
        _sweep(tmp_path)
        assert all(p.exists() for p in made), [p.name for p in made if not p.exists()]
    finally:
        os.close(fd)
    out = _sweep(tmp_path)                                                  # lock released: now they go
    assert not any(p.exists() for p in made), out


# ---- reviewer reuse and docs ----------------------------------------------------------------------------------

def test_reviewer_runs_through_the_shared_helper(tmp_path, monkeypatch):
    """Clause 2 (C23): `reviewer.py` is a thin wrapper over the shared module. A spy on `rv._rr.classify`
    (the module object reviewer uses) must see every reviewer read -- with the reviewer's own grace and its own
    `_pid_alive` injected -- and a spy on `rv._rr.write` must see the reviewer's receipt write."""
    rv = _rv()
    rr = getattr(rv, "_rr", None)
    assert rr is not None, "reviewer.py does not bind the shared run_receipt module `_rr` yet (#1650)"
    p = _rv_project(tmp_path)
    sd = str(p / ".sdlc")
    classified, wrote = [], []
    real_write = rr.write

    def write_spy(path, **fields):
        wrote.append(str(path))
        return real_write(path, **fields)

    def classify_spy(*a, **k):
        classified.append((a, k))
        return {**a[0], "outcome": "failed", "failure": "not_completed", "detail": "via-shared-helper"}
    monkeypatch.setattr(rr, "write", write_spy)
    monkeypatch.setattr(rr, "classify", classify_spy)
    rv._write_receipt(sd, RV_GOAL, "review", outcome="running", pid=os.getpid(), started_at=int(time.time()),
                      timeout_seconds=900, run_id="r1", route=rv._route_tag(rv.resolve(sd)))
    assert wrote and wrote[0].replace(os.sep, "/").endswith("reviewer-runs/0001-x.review.json"), wrote
    state, got = rv._read_receipt(sd, RV_GOAL, "review")
    assert state == "ok" and got.get("detail") == "via-shared-helper", (state, got)
    assert classified, "the reviewer classified a receipt without the shared classifier"
    args, kwargs = classified[0]
    grace = args[1] if len(args) > 1 else kwargs.get("grace")
    alive = kwargs.get("alive", args[3] if len(args) > 3 else None)
    assert grace == rv._RUN_GRACE, (grace, rv._RUN_GRACE)
    assert alive is rv._pid_alive, alive                                    # injected, so a non-POSIX reader never probes


def test_landing_md_names_wait_call_gesture_for_verify_and_merge():
    """Clause 5 (C25): landing.md names the `wait-call` gesture for BOTH calls in BOTH places -- the REQUIRED
    PATTERN block and the `work.py merge` paragraph (each deletion is its own break) -- and no longer claims
    `loop.py verify` has no concurrency guard."""
    text = (ROOT / "skills" / "sigma-loop" / "references" / "landing.md").read_text(encoding="utf-8")
    assert "no concurrency guard" not in text, "landing.md still says verify has no concurrency guard"
    head = text.index("REQUIRED PATTERN")
    block = text[head:text.index("**Honest limit**", head)]
    start = text.index("Then `work.py merge")
    paragraph = text[start:text.index("The gate is clean", start)]

    def windows(where):
        return [where[max(0, m.start() - 250):m.end() + 250] for m in re.finditer(r"wait-call", where)]

    assert windows(block), "the REQUIRED PATTERN block does not name `wait-call`"
    assert any("verify" in w for w in windows(block)), "the REQUIRED PATTERN block names no verify gesture"
    assert any("merge" in w for w in windows(block)), "the REQUIRED PATTERN block names no merge gesture"
    assert any("merge" in w for w in windows(paragraph)), "the `work.py merge` paragraph does not name `wait-call`"

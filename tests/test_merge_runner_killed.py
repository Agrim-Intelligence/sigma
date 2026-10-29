"""#2086: a `work.py merge` runner killed mid-merge (a cloud worker restart) used to read as a bare `FAILED`.

`wait-call ... merge` now reads the goal's PR ONCE when the runner is gone and, while the PR is open and unmerged,
says so and names the two gestures (re-register the agent, re-run `work.py merge`). The unit tests below break the
guard one clause at a time (the kind, the failed state, the runner-gone detail, the PR on record, the PR's state, the
one-read count, the bound on the read); the end to end test SIGKILLs a real merge process during pending-check
polling and runs the recovery the docs give. Every GitHub answer is a fake (`merge_world.py`); NOT measured: a real
cloud container, a real GitHub, a real worker restart.
"""
import json
import os
import pathlib
import re
import shlex
import signal
import subprocess
import sys
import time

import pytest

import merge_world as mw
import prfake
import test_work as tw
from test_long_call_receipts import _DROP, _dead_pid, _loop, _plant, _rpath, _wc

ROOT = pathlib.Path(__file__).resolve().parent.parent
LANDING = (ROOT / "skills" / "sigma-loop" / "references" / "landing.md").read_text(encoding="utf-8")
LAND = (ROOT / "skills" / "sigma-goal" / "references" / "land.md").read_text(encoding="utf-8")
CLOUD = (ROOT / "docs" / "cloud-sessions.md").read_text(encoding="utf-8")
KILLED = (ROOT / "skills" / "sigma-loop" / "references" / "killed-merge.md").read_text(encoding="utf-8")


# ---- the guard, one clause at a time -----------------------------------------------------------------------

def _world(tmp_path, monkeypatch, answer, pr="7"):
    """A project with a work record naming PR `pr` (or none), and `work._run` replaced by `answer(cwd, argv)`."""
    d = tw._sdlc(tmp_path, {"work": {"enabled": True}})
    tw._started(d, goal="0001", pr=pr)
    lp = _loop()
    calls = []

    def run(cwd, argv):
        calls.append(" ".join(map(str, argv)))
        return answer(cwd, argv)
    monkeypatch.setattr(lp.work, "_run", run)
    return lp, d, calls


def _open(cwd, argv):
    return tw._rest_pr()


def _gone(sd, kind="merge"):
    _plant(sd, kind, pid=_dead_pid())


def test_an_open_pr_is_reported_with_both_recovery_gestures(tmp_path, monkeypatch, capsys):
    lp, sd, calls = _world(tmp_path, monkeypatch, _open)
    _gone(sd)
    code, out, err = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    lines = out.splitlines()
    assert code == 1 and lines[0].startswith("FAILED not_completed"), (code, out)
    assert len(lines) == 2, out
    pr_line = lines[1]
    assert "PR #7 is open and unmerged at this read" in pr_line, pr_line
    assert re.search(r"python3 \S*loop\.py agent-start \S+ 0001 --pid \"\$PPID\"", pr_line), pr_line
    assert re.search(r"python3 \S*work\.py merge \S+ 0001", pr_line), pr_line
    assert pr_line.index("agent-start") < pr_line.index("work.py"), "register first, then merge"
    assert "Do not record the goal failed" in pr_line and "killed-merge.md" in pr_line, pr_line
    assert "inside the merge call" in pr_line, "the window the read cannot close is named"
    assert len(calls) == 1 and "pulls/7" in calls[0], calls
    assert "recovery on the line above" in err and "Do not re-run blindly" not in err, err


@pytest.mark.parametrize("answer,needle", [
    (lambda c, a: tw._rest_pr(state="closed", merged=True), "already merged"),
    (lambda c, a: tw._rest_pr(state="closed"), "closed without merging"),
    (lambda c, a: (_ for _ in ()).throw(RuntimeError("HTTP 502: bad gateway")), "could not read"),
], ids=["merged", "closed", "unreadable"])
def test_a_pr_that_is_not_open_gets_no_rerun_advice(tmp_path, monkeypatch, capsys, answer, needle):
    lp, sd, calls = _world(tmp_path, monkeypatch, answer)
    _gone(sd)
    code, out, err = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and needle in out, (code, out)
    assert "agent-start" not in out and "work.py merge" not in out, out
    assert "landing.md" in err and "re-run" in err, "the unchanged stderr line stays for the other states"
    assert len(calls) == 1, calls


def test_a_merged_pr_points_at_record_done(tmp_path, monkeypatch, capsys):
    lp, sd, _ = _world(tmp_path, monkeypatch, lambda c, a: tw._rest_pr(state="closed", merged=True))
    _gone(sd)
    _, out, _ = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert re.search(r"loop\.py record \S+ 0001 done", out), out


@pytest.mark.parametrize("over", [
    {"started_at": int(time.time()) - 14400 - 3600},      # deadline passed: the runner may still be live
    {"pid": _DROP},                                       # malformed running receipt
], ids=["deadline", "malformed"])
def test_only_a_gone_runner_earns_the_rerun_advice(tmp_path, monkeypatch, capsys, over):
    lp, sd, calls = _world(tmp_path, monkeypatch, _open)
    _plant(sd, "merge", **over)
    code, out, err = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and "PR #7 is open and unmerged" in out, (code, out)
    assert "agent-start" not in out and "work.py merge" not in out, out
    assert "check" in out and "runner" in out, out
    assert len(calls) == 1, calls


def test_a_runner_that_raised_gets_no_rerun_advice(tmp_path, monkeypatch, capsys):
    lp, sd, _ = _world(tmp_path, monkeypatch, _open)
    _plant(sd, "merge", outcome="failed", failure="not_completed", detail="the runner raised", pid=_DROP)
    code, out, _ = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and "agent-start" not in out and "work.py merge" not in out, out


def test_no_pr_on_record_reads_nothing(tmp_path, monkeypatch, capsys):
    lp, sd, calls = _world(tmp_path, monkeypatch, _open, pr="")
    _gone(sd)
    code, out, err = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and len(out.splitlines()) == 1 and calls == [], (code, out, calls)
    assert "landing.md" in err


def test_no_work_record_reads_nothing(tmp_path, monkeypatch, capsys):
    lp, sd, calls = _world(tmp_path, monkeypatch, _open)
    pathlib.Path(sd, "state", "work", "0001.json").unlink()
    _gone(sd)
    code, out, _ = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and len(out.splitlines()) == 1 and calls == [], (code, out, calls)


def test_a_dead_verify_runner_reads_nothing(tmp_path, monkeypatch, capsys):
    lp, sd, calls = _world(tmp_path, monkeypatch, _open)
    _gone(sd, "verify")
    code, out, err = _wc(lp, sd, "verify", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and calls == [] and "PR #" not in out + err, (code, out, calls)


def test_a_finished_or_live_merge_reads_nothing(tmp_path, monkeypatch, capsys):
    lp, sd, calls = _world(tmp_path, monkeypatch, _open)
    _plant(sd, "merge", outcome="ok", result="PR #7 merged (squash)", pid=_DROP, started_at=_DROP,
           timeout_seconds=_DROP)
    code, out, _ = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 0 and "PR #7 merged" in out and calls == []
    _plant(sd, "merge", started_at=int(time.time()) - 5)
    code, out, _ = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 3 and out.startswith("RUNNING") and calls == []


def test_a_read_that_hangs_is_bounded_and_reads_unknown(tmp_path, monkeypatch, capsys):
    lp, sd, _ = _world(tmp_path, monkeypatch, lambda c, a: time.sleep(30))
    monkeypatch.setattr(lp, "_PR_READ_SECONDS", 1)
    _gone(sd)
    started = time.monotonic()
    code, out, _ = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert time.monotonic() - started < 10, "the hung read was not bounded"
    assert code == 1 and "could not read" in out and "agent-start" not in out, (code, out)


def test_the_read_restores_the_callers_alarm_handler_and_timer(tmp_path, monkeypatch, capsys):
    import signal
    lp, sd, _ = _world(tmp_path, monkeypatch, _open)
    _gone(sd)
    mine = lambda signum, frame: None                           # noqa: E731
    previous = signal.signal(signal.SIGALRM, mine)
    signal.setitimer(signal.ITIMER_REAL, 600)
    try:
        _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
        assert signal.getsignal(signal.SIGALRM) is mine, "the caller's handler was not restored"
        left = signal.getitimer(signal.ITIMER_REAL)[0]
        assert 500 < left <= 600, "the caller's timer was not re-armed (%r)" % (left,)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_a_failure_building_the_line_never_replaces_failed(tmp_path, monkeypatch, capsys):
    lp, sd, _ = _world(tmp_path, monkeypatch, _open)
    _gone(sd)
    monkeypatch.setattr(lp, "_merge_pr_line", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    code, out, err = _wc(lp, sd, "merge", "--max-seconds", "1", capsys=capsys)
    assert code == 1 and out.startswith("FAILED not_completed") and "could not read the PR" in out, (code, out)
    assert "agent-start" not in out


def test_the_gestures_use_the_resolved_sdlc_dir_and_absolute_scripts(tmp_path, monkeypatch, capsys):
    lp, sd, _ = _world(tmp_path, monkeypatch, _open)
    _gone(sd)
    monkeypatch.chdir(tmp_path)
    _, out, _ = _wc(lp, os.path.relpath(sd, tmp_path), "merge", "--max-seconds", "1", capsys=capsys)
    m = re.search(r"python3 (\S+loop\.py) agent-start (\S+) 0001", out)
    assert m and os.path.isabs(m.group(1)) and os.path.isabs(m.group(2)), out


# ---- the docs name the recovery ----------------------------------------------------------------------------

def test_landing_names_the_recovery_for_a_killed_merge_runner():
    bullet = LANDING[LANDING.index("A merge `runner pid N gone`"):].split("\n\n")[0]
    for needle in ("runner pid N gone", "PR open", "not a failure", "agent-start", '--pid "$PPID"', "work.py merge",
                   "killed-merge.md"):
        assert needle in bullet, needle
    assert bullet.index("agent-start") < bullet.index("work.py merge"), "register first, then merge"
    steps = _doc_gestures()
    assert [s.split()[2] for s in steps] == ["wait-call", "agent-start", "merge", "record"], steps


def test_the_detail_file_names_the_rest():
    for needle in ("agent-reclaim", "inside the merge call", "already merged", "closed without", "could not read",
                   "session id", "UNMEASURED", "runner pid N gone", "Do not record the goal failed"):
        assert needle in KILLED, needle
    assert "killed-merge.md" in LANDING


def test_land_and_cloud_docs_point_at_it():
    assert "killed-merge.md" in LAND and "killed-merge.md" in CLOUD


# ---- the end to end run: SIGKILL a real merge during pending-check polling ---------------------------------

def _doc_gestures():
    """The recovery commands exactly as landing.md's section prints them, with its placeholders filled in."""
    block = re.search(r"```\n(.*?)```", KILLED, re.S).group(1)
    return [l for l in block.splitlines() if l.startswith("python3 ")]


def _fill(line, sdlc):
    line = line.replace("${CLAUDE_SKILL_DIR}", str(ROOT / "skills" / "sigma-loop"))
    line = line.replace('"<abs .sdlc>"', shlex.quote(sdlc))
    return line.replace("$goal", mw.GOAL)


def _sh(line, env, cwd, timeout=120):
    return subprocess.run(line, shell=True, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _has(world, token):
    return [c for c in mw.calls(world) if token in c]


def test_a_killed_merge_runner_is_recovered_by_the_documented_gestures(tmp_path):
    cfg = {"work": {"enabled": True, "auto_merge": "always"},
           "discovery": {"source": "github", "github": {"repo": "acme/app"}}}
    sd = tw._sdlc(tmp_path, cfg)
    goal = tw._started(sd, goal=mw.GOAL, pr=mw.PR)
    tw._evidence(sd, goal)
    wt = pathlib.Path(tw.work._record(sd, goal)["worktree"])
    for cmd in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"],
                ["commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", "-C", str(wt), *cmd], check=True)
    head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()
    world, binp = tmp_path / "world", tmp_path / "bin"
    mw.install(world, binp, head)
    env = {"PATH": os.pathsep.join([str(binp), str(pathlib.Path(sys.executable).parent), "/usr/bin", "/bin"]),
           "HOME": str(tmp_path / "home"), "CLAUDE_CODE_REMOTE": "true", "PYTHONDONTWRITEBYTECODE": "1",
           "LANG": "en_US.UTF-8"}
    (tmp_path / "home").mkdir()
    work_py = ROOT / "skills" / "sigma-loop" / "scripts" / "work.py"

    # the old session registered itself as the agent for this goal; its pid is the runner's, dead after the kill
    config = tw.state.load_config(sd)
    marker_path = None

    def register(pid):
        nonlocal marker_path
        import importlib.util
        spec = importlib.util.spec_from_file_location("loop_for_marker", ROOT / "skills" / "sigma-loop" / "scripts" / "loop.py")
        lp = importlib.util.module_from_spec(spec); spec.loader.exec_module(lp)
        assert lp.agent_start(sd, goal, pid, config) is True
        marker_path = lp._agent_marker_path(sd, goal, "main")

    # 1. a real merge runner, required check still pending
    out_f, err_f = tmp_path / "merge.out", tmp_path / "merge.err"
    with open(out_f, "w") as so, open(err_f, "w") as se:
        runner = subprocess.Popen([sys.executable, str(work_py), "merge", sd, goal], env=env, cwd=tmp_path,
                                  stdout=so, stderr=se)
        try:
            deadline = time.monotonic() + 90
            while not _has(world, "/status --method GET"):
                assert runner.poll() is None, ("the runner exited early", err_f.read_text(), out_f.read_text())
                assert time.monotonic() < deadline, ("the runner never reached the poll", mw.calls(world))
                time.sleep(0.2)
            assert not _has(world, "/merge --method PUT"), "the check was pending: nothing may have merged yet"
            os.kill(runner.pid, signal.SIGKILL)
        finally:
            if runner.poll() is None:
                os.kill(runner.pid, signal.SIGKILL)
            runner.wait()
    assert runner.returncode == -signal.SIGKILL
    register(runner.pid)                                              # the old session's marker, now naming a dead pid
    assert marker_path.read_text().split()[0] == str(runner.pid)
    assert not _has(world, "/merge --method PUT"), "the kill must land before any merge"
    receipt = json.loads(_rpath(sd, "merge", goal).read_text())
    assert receipt["outcome"] == "running" and receipt["pid"] == runner.pid, receipt

    # 2. the gesture the merge printed on launch
    hint = re.search(r"wait with `(python3 .*? wait-call [^`]*)`", err_f.read_text())
    assert hint, err_f.read_text()
    waited = _sh(hint.group(1), env, tmp_path)
    assert waited.returncode == 1, (waited.stdout, waited.stderr)
    first, second = waited.stdout.splitlines()[:2]
    assert first.startswith("FAILED not_completed: runner pid %d gone" % runner.pid), first
    assert "PR #7 is open and unmerged at this read" in second and "agent-start" in second, second
    reads = _has(world, "pulls/7")
    assert not _has(world, "/merge --method PUT")

    # 3. the recovery as landing.md gives it: the same wait-call, register, merge, record
    gestures = [_fill(g, sd) for g in _doc_gestures()]
    wait_g, reg_g, merge_g, record_g = gestures
    assert " wait-call " in wait_g and " agent-start " in reg_g and " merge " in merge_g and " record " in record_g
    waited = _sh(wait_g, env, tmp_path)
    assert waited.returncode == 1 and "open and unmerged" in waited.stdout, (waited.stdout, waited.stderr)
    registered = _sh(reg_g, env, tmp_path)
    assert registered.returncode == 0, registered.stderr
    assert marker_path.read_text().split()[0].isdigit()
    assert marker_path.read_text().split()[0] != str(runner.pid), "the dead runner's pid is still the marker"

    mw.flag(world, "green")                                            # the pending check has finished
    merged = _sh(merge_g, env, tmp_path)
    assert merged.returncode == 0 and merged.stdout.startswith("PR #7 merged"), (merged.stdout, merged.stderr)
    assert len(_has(world, "/merge --method PUT")) == 1, "the PR must land exactly once"

    done = _sh(record_g, env, tmp_path)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert len([c for c in mw.calls(world) if "state=closed" in c]) == 1, "the issue is closed exactly once"
    final = _sh(wait_g, env, tmp_path)
    assert final.returncode == 0 and "PR #7 merged" in final.stdout, (final.stdout, final.stderr)
    assert len(_has(world, "/merge --method PUT")) == 1
    assert len(reads) >= 1

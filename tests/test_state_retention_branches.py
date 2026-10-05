"""A closed goal whose `sdlc/<goal>` branch still exists keeps its log (#457).

`work.start` reads a restarted goal's original base from the action log when the branch outlived its
work record, and refuses in a feature-unit repo when nothing says what it was cut from. So the log of
a goal whose branch survives is still needed, and retention must not prune it. Driven through the
documented `loop.py prune-state` gesture on a scratch git repository.
"""
import json
import os
import pathlib
import subprocess
import sys
import time

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
OLD = time.time() - 200 * 86400


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def _repo(tmp_path, work=True, git=True, **work_extra):
    if git:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=a@b", "-c", "user.name=x",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    d = tmp_path / ".sdlc"
    (d / "state" / "log").mkdir(parents=True)
    (d / "state" / "witness").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"work": {"enabled": work, **work_extra}}))
    return d


def _goal(d, stem):
    log = d / "state" / "log" / f"{stem}.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in (
        {"kind": "claimed", "ts": _iso(OLD - 5)},
        {"kind": "recorded", "result": "done", "ts": _iso(OLD)})))
    os.utime(log, (OLD, OLD))
    return log


def _run(d):
    return subprocess.run([sys.executable, str(S / "loop.py"), "prune-state", str(d)],
                          capture_output=True, text=True, timeout=120)


def test_goal_with_no_branch_is_pruned(tmp_path):
    d = _repo(tmp_path)
    log = _goal(d, "601")
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "removed 601" in r.stdout and not log.exists()


def test_goal_whose_local_branch_survives_is_kept(tmp_path):
    d = _repo(tmp_path)
    log = _goal(d, "602")
    subprocess.run(["git", "-C", str(tmp_path), "branch", "sdlc/602"], check=True)
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 602" in r.stdout and "branch" in r.stdout and log.exists()


def test_goal_whose_remote_branch_survives_is_kept(tmp_path):
    d = _repo(tmp_path)
    log = _goal(d, "603")
    subprocess.run(["git", "-C", str(tmp_path), "update-ref", "refs/remotes/origin/sdlc/603", "HEAD"],
                   check=True)
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 603" in r.stdout and log.exists()


def test_unreadable_branch_state_prunes_nothing(tmp_path):
    d = _repo(tmp_path, git=False)                          # work is on but there is no repository
    log = _goal(d, "604")
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 604" in r.stdout and "branch state" in r.stdout and log.exists()


def test_work_off_has_no_branch_to_wait_for(tmp_path):
    d = _repo(tmp_path, work=False, git=False)
    log = _goal(d, "605")
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "removed 605" in r.stdout and not log.exists()


def test_custom_prefix_without_a_slash_still_protects_the_log(tmp_path):
    """`git for-each-ref refs/heads/goal-` matches nothing, so the prefix must be filtered in Python."""
    d = _repo(tmp_path, branch_prefix="goal-")
    log = _goal(d, "606")
    subprocess.run(["git", "-C", str(tmp_path), "branch", "goal-606"], check=True)
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 606" in r.stdout and log.exists()


def test_custom_remote_is_the_one_consulted(tmp_path):
    d = _repo(tmp_path, remote="upstream")
    log = _goal(d, "607")
    subprocess.run(["git", "-C", str(tmp_path), "update-ref", "refs/remotes/upstream/sdlc/607", "HEAD"],
                   check=True)
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 607" in r.stdout and log.exists()

"""Retention of closed-goal action-log and witness streams (#457, B6 #419).

Every test drives the documented gesture, `loop.py prune-state <sdlc>`, as a real subprocess over a
scratch `.sdlc` with back-dated files, and asserts the exit status AND the printed result, so on
code without the verb every node is red by assertion (a "kept" case also asserts `returncode == 0`
and the `kept` line; a bare "file still exists" would pass on old code and prove nothing).
"""
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"
LOOP = S / "loop.py"
DAY = 86400
OLD = time.time() - 200 * DAY          # past the 90-day default window
RECENT = time.time() - 5 * DAY


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def _sdlc(tmp_path, config=None):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config or {"action_log": {"enabled": True}}))
    (d / "state" / "STATE.md").write_text("# Loop State\niteration: 0\nrun_iteration: 0\nlast_run: none\n")
    return d


def _rows(t, *kinds):
    out = []
    for i, k in enumerate(kinds):
        kind, _, result = k.partition(":")
        row = {"actor": "loop", "goal": "g", "kind": kind, "thread": "main", "ts": _iso(t + i)}
        if result:
            row["result"] = result
        out.append(json.dumps(row, sort_keys=True))
    return "\n".join(out) + "\n"


def _goal(d, stem, kinds=("claimed", "verify_run", "recorded:done"), age=OLD, witness=True):
    log = d / "state" / "log" / f"{stem}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(_rows(age, *kinds))
    os.utime(log, (age, age))
    wit = d / "state" / "witness" / f"{stem}.jsonl"
    if witness:
        wit.parent.mkdir(parents=True, exist_ok=True)
        wit.write_text(json.dumps({"test": "t", "kind": "assertion", "ts": _iso(age)}) + "\n")
        os.utime(wit, (age, age))
    return log, wit


def _run(d, *extra):
    return subprocess.run([sys.executable, str(LOOP), "prune-state", str(d), *extra],
                          capture_output=True, text=True, timeout=120)


def test_closed_old_goal_loses_both_streams(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "101")
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "removed 101" in r.stdout
    assert not log.exists() and not wit.exists()


def test_recent_closed_goal_is_kept(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "102", age=RECENT)
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 102" in r.stdout and "removed" not in r.stdout
    assert log.exists() and wit.exists()


@pytest.mark.parametrize("kinds", [
    ("claimed", "verify_run"),                              # live: never recorded
    ("claimed", "recorded:done", "claimed"),                # reopened after a done
    ("claimed", "recorded:review"),                         # awaiting merge
    ("claimed", "recorded:parked"),
    ("claimed", "recorded:failed"),
    ("claimed", "recorded:done", "gate"),                   # newest internal row is not recorded
])
def test_only_a_newest_done_row_is_a_candidate(tmp_path, kinds):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "103", kinds=kinds)
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 103" in r.stdout and "removed" not in r.stdout
    assert log.exists() and wit.exists()


def test_agent_rows_after_done_do_not_hide_it(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "104", kinds=("claimed", "recorded:done", "agent_done"))
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "removed 104" in r.stdout and not log.exists()


@pytest.mark.parametrize("marker", ["claims/105.claimed", "agents/105/main.json", "work/105.json",
                                    "phase/105.json"])
def test_fresh_live_owner_marker_keeps_the_goal(tmp_path, marker):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "105")
    p = d / "state" / marker
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")                                      # mtime = now: a live owner
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 105" in r.stdout and "removed" not in r.stdout
    assert log.exists() and wit.exists()


def test_fresh_worktree_dir_keeps_the_goal(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "106")
    (d / "work" / "106").mkdir(parents=True)
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 106" in r.stdout and log.exists() and wit.exists()


def test_stale_marker_does_not_block_a_closed_goal(tmp_path):
    """`.claimed` is written once and never removed (loop.py `_ensure_claimed`), so an old one must
    not make a closed goal unprunable forever."""
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "107")
    m = d / "state" / "claims" / "107.claimed"
    m.parent.mkdir(parents=True)
    m.write_text("{}")
    os.utime(m, (OLD, OLD))
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "removed 107" in r.stdout and not log.exists() and not wit.exists()
    assert m.exists()                                       # markers are not ours to delete


def test_witness_only_stem_is_never_pruned(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "108")
    log.unlink()
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 108" in r.stdout and wit.exists()


def test_symlinked_stream_is_never_followed(tmp_path):
    d = _sdlc(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_text(_rows(OLD, "claimed", "recorded:done"))
    os.utime(outside, (OLD, OLD))
    link = d / "state" / "log" / "109.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    os.utime(link, (OLD, OLD), follow_symlinks=False)        # old link: only the symlink guard can save it
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 109" in r.stdout
    assert outside.exists() and link.is_symlink()


def test_symlinked_log_directory_is_never_followed(tmp_path):
    d = _sdlc(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    victim = real / "110.jsonl"
    victim.write_text(_rows(OLD, "claimed", "recorded:done"))
    os.utime(victim, (OLD, OLD))
    (d / "state" / "log").symlink_to(real, target_is_directory=True)
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "removed" not in r.stdout
    assert victim.exists()


def test_dry_run_removes_nothing_but_names_the_goal(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "111")
    r = _run(d, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "would remove 111" in r.stdout
    assert log.exists() and wit.exists()


def test_limit_bounds_one_sweep_oldest_first(tmp_path):
    d = _sdlc(tmp_path)
    older, _ = _goal(d, "201", age=time.time() - 400 * DAY)
    newer, _ = _goal(d, "202", age=time.time() - 300 * DAY)
    r = _run(d, "--limit", "1")
    assert r.returncode == 0, r.stderr
    assert "removed 201" in r.stdout and "removed 202" not in r.stdout
    assert not older.exists() and newer.exists()
    assert _run(d, "--limit", "1").returncode == 0
    assert not newer.exists()                               # the next sweep finishes the job


def test_partial_prune_heals_on_the_next_sweep(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "112")
    wit.unlink()                                            # crash after the witness unlink
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "removed 112" in r.stdout and not log.exists()


def test_retention_can_be_disabled(tmp_path):
    d = _sdlc(tmp_path, {"action_log": {"enabled": True, "retention_days": False}})
    log, wit = _goal(d, "113")
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "disabled" in r.stdout
    assert log.exists() and wit.exists()


def test_window_is_floored_at_thirty_days(tmp_path):
    d = _sdlc(tmp_path, {"action_log": {"enabled": True, "retention_days": 1}})
    log, wit = _goal(d, "114", age=time.time() - 10 * DAY)
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 114" in r.stdout and log.exists()


def test_only_the_two_stream_files_are_touched(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "115")
    bystander = d / "state" / "log" / "115.jsonl.bak"
    bystander.write_text("x")
    other = d / "state" / "claims" / "115.lock"
    other.parent.mkdir(parents=True)
    other.write_text("x")
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "removed 115" in r.stdout
    assert bystander.exists() and other.exists()


def test_record_done_sweeps_an_old_closed_goal(tmp_path):
    """The automatic trigger: finishing any goal sweeps, host-agnostically, from Sigma's own Python."""
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "301")
    import importlib.util
    spec = importlib.util.spec_from_file_location("loop_under_test", LOOP)
    loop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loop)

    class Source:
        def complete(self, goal):
            return None

        def __getattr__(self, name):
            return lambda *a, **k: None

    loop._record(str(d), Source(), "302", "done", "ok")
    assert not log.exists() and not wit.exists()


def test_symlinked_witness_directory_is_never_followed(tmp_path):
    d = _sdlc(tmp_path)
    log, _ = _goal(d, "401", witness=False)
    real = tmp_path / "elsewhere"
    real.mkdir()
    victim = real / "401.jsonl"
    victim.write_text("{}\n")
    os.utime(victim, (OLD, OLD))
    (d / "state" / "witness").symlink_to(real, target_is_directory=True)
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 401" in r.stdout and "removed" not in r.stdout
    assert victim.exists() and log.exists()


def test_fresh_witness_keeps_an_old_closed_log(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "402")
    os.utime(wit, (RECENT, RECENT))
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 402" in r.stdout and log.exists() and wit.exists()


def test_big_log_is_judged_from_its_tail(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "403", kinds=("claimed",) + ("note",) * 400 + ("recorded:done",))
    assert log.stat().st_size > 8192
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "removed 403" in r.stdout and not log.exists()


def test_file_order_not_timestamp_decides_the_newest_row(tmp_path):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "404")
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    reopened = dict(rows[0], ts=_iso(OLD - 10 * DAY))       # appended last, but a skewed clock
    log.write_text(log.read_text() + json.dumps(reopened, sort_keys=True) + "\n")
    os.utime(log, (OLD, OLD))
    r = _run(d)
    assert r.returncode == 0, r.stderr
    assert "kept 404" in r.stdout and log.exists()


@pytest.mark.parametrize("args", [["--dryrun"], ["--keep-days"], ["--keep-days", "x"], ["--limit", "0"]])
def test_bad_arguments_refuse_and_delete_nothing(tmp_path, args):
    d = _sdlc(tmp_path)
    log, wit = _goal(d, "405")
    r = _run(d, *args)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert log.exists() and wit.exists()

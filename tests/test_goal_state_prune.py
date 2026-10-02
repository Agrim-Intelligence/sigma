"""#458 (B6): per-goal state records are removed once their goal is terminal -- and only then.

Every test drives the documented gestures: `goal_state_prune.py sweep .sdlc` (the operator lever)
and `loop.py record .sdlc <goal> done` (the in-line trigger), over a scratch `.sdlc`. A candidate is
a goal whose action log's newest internal row is `recorded result=done`; a reopened goal appends
`claimed`, a parked/failed/awaiting-merge goal never reached `done`, a goal with a work record or a
live agent marker still has an owner. Time is real: files and log rows are back-dated past the
seven-day window with os.utime and explicit ts values.
"""
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"
OLD = time.time() - 10 * 86400       # comfortably past the 7-day window


def _run(script, *args, cwd):
    return subprocess.run([sys.executable, str(S / script), *args], cwd=cwd, capture_output=True,
                          text=True, timeout=120)


def _sweep(tmp_path, *extra):
    proc = _run("goal_state_prune.py", "sweep", ".sdlc", *extra, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _ts(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


@pytest.fixture
def sdlc(tmp_path):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({
        "action_log": {"enabled": True}, "discovery": {"source": "local"},
        "verify": {"enforce": False}}))
    return d


FAMILY_FILES = ("verify/{g}.json", "landing/{g}.json", "propagation/{g}.json",
                "escalation/{g}.json", "unit-tracking/{g}.attempt", "phase/{g}.json")


def _log(sdlc, goal, *rows, age=OLD):
    path = sdlc / "state" / "log" / f"{goal}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for i, (kind, extra) in enumerate(rows):
            row = {"actor": "loop", "goal": goal, "kind": kind, "thread": "main",
                   "ts": _ts(age + i)}
            row.update(extra)
            handle.write(json.dumps(row) + "\n")
    os.utime(path, (age, age))


def _files(sdlc, goal, age=OLD, families=FAMILY_FILES):
    made = []
    for pattern in families:
        path = sdlc / "state" / pattern.format(g=goal)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        os.utime(path, (age, age))
        made.append(path)
    return made


def _done(sdlc, goal, **kw):
    _log(sdlc, goal, ("claimed", {}), ("recorded", {"result": "done"}), **kw)


def test_terminal_goal_every_family_removed(sdlc, tmp_path):
    _done(sdlc, "10")
    made = _files(sdlc, "10")
    out = _sweep(tmp_path)
    assert not any(p.exists() for p in made)
    assert len(out["removed"]) == len(made)
    assert (sdlc / "state" / "log" / "10.jsonl").exists()        # the proof of closure stays


@pytest.mark.parametrize("rows", [
    [("claimed", {})],                                                        # live
    [("claimed", {}), ("recorded", {"result": "parked"})],                    # parked
    [("claimed", {}), ("recorded", {"result": "failed"})],                    # failed
    [("claimed", {}), ("recorded", {"result": "review"})],                    # awaiting merge
    [("claimed", {}), ("recorded", {"result": "done"}), ("claimed", {})],     # reopened
], ids=["live", "parked", "failed", "awaiting-merge", "reopened"])
def test_non_terminal_goal_kept(sdlc, tmp_path, rows):
    _log(sdlc, "11", *rows)
    made = _files(sdlc, "11")
    _sweep(tmp_path)
    assert all(p.exists() for p in made)


def test_goal_without_a_log_is_never_a_candidate(sdlc, tmp_path):
    made = _files(sdlc, "12")
    _sweep(tmp_path)
    assert all(p.exists() for p in made)


def test_work_record_means_an_owner_remains(sdlc, tmp_path):
    _done(sdlc, "13")
    made = _files(sdlc, "13")
    rec = sdlc / "state" / "work" / "13.json"
    rec.parent.mkdir(parents=True)
    rec.write_text("{}")
    _sweep(tmp_path)
    assert all(p.exists() for p in made) and rec.exists()


@pytest.mark.parametrize("age_s", [60, 3 * 86400], ids=["a-minute", "three-days"])
def test_inside_the_window_kept_and_outside_removed_by_the_documented_flag(sdlc, tmp_path, age_s):
    _done(sdlc, "14", age=time.time() - age_s)
    made = _files(sdlc, "14", age=time.time() - age_s)
    _sweep(tmp_path)
    assert all(p.exists() for p in made)
    _sweep(tmp_path, "--min-age-days", "0.0001")      # operator lever: a shorter window
    assert (not any(p.exists() for p in made)) == (age_s >= 60)


def test_fresh_file_of_a_terminal_goal_kept_until_it_ages(sdlc, tmp_path):
    _done(sdlc, "15")
    old, fresh = _files(sdlc, "15")[0], _files(sdlc, "15", age=time.time(), families=("verify/15.json",))[0]
    assert old == fresh
    _sweep(tmp_path)
    assert old.exists()


def _agent(sdlc, tmp_path, goal, pid):
    proc = _run("loop.py", "agent-start", ".sdlc", goal, "--pid", str(pid), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    # not back-dated: a marker older than the lease TTL counts as dead whatever its pid
    return sdlc / "state" / "agents" / goal


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_alive_agent_marker_keeps_everything(sdlc, tmp_path):
    _done(sdlc, "16")
    made = _files(sdlc, "16")
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        d = _agent(sdlc, tmp_path, "16", holder.pid)
        _sweep(tmp_path)
        assert d.exists() and all(p.exists() for p in made)
    finally:
        holder.kill()
        holder.wait()


def test_dead_agent_marker_removed_with_the_goal(sdlc, tmp_path):
    _done(sdlc, "17")
    _files(sdlc, "17")
    d = _agent(sdlc, tmp_path, "17", _dead_pid())
    _sweep(tmp_path)
    assert not d.exists()


def test_symlinks_are_never_followed(sdlc, tmp_path):
    _done(sdlc, "18")
    outside = tmp_path / "outside"
    outside.mkdir()
    keep_me = outside / "keep.json"
    keep_me.write_text("precious")
    (sdlc / "state" / "verify").mkdir()
    link = sdlc / "state" / "verify" / "18.json"
    link.symlink_to(keep_me)
    linked_dir = sdlc / "state" / "landing"
    linked_dir.symlink_to(outside, target_is_directory=True)
    (outside / "18.json").write_text("precious too")
    for target in (keep_me, outside / "18.json"):
        os.utime(target, (OLD, OLD))          # so a follower would see an eligible, aged file
    _sweep(tmp_path)
    assert keep_me.exists() and (outside / "18.json").exists()          # a follower deletes these
    assert keep_me.read_text() == "precious" and (outside / "18.json").read_text() == "precious too"
    assert link.is_symlink() and linked_dir.is_symlink()


def test_only_owned_paths_are_touched(sdlc, tmp_path):
    _done(sdlc, "19")
    _files(sdlc, "19")
    neighbours = [sdlc / "state" / "log" / "19.jsonl", sdlc / "state" / "witness" / "19.jsonl",
                  sdlc / "state" / "verify" / "190.json", sdlc / "state" / "verify" / "notes.txt",
                  sdlc / "state" / "work" / "other.json", sdlc / "config.json",
                  sdlc / "state" / "ledger-cursor.json", sdlc / "plans" / "19.md",
                  sdlc / "state" / "withheld" / "19.md", sdlc / "state" / "goal-review" / "19.plan.json"]
    for p in neighbours:
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("x")
        os.utime(p, (OLD, OLD))
    before = {p: p.read_text() for p in neighbours}
    _sweep(tmp_path)
    assert all(p.exists() for p in neighbours)
    assert {p: p.read_text() for p in neighbours} == before


def test_unsafe_names_are_ignored(sdlc, tmp_path):
    (sdlc / "state" / "verify").mkdir()
    odd = sdlc / "state" / "verify" / "..hidden.json"
    odd.write_text("{}")
    out = _sweep(tmp_path)
    assert odd.exists() and out["removed"] == []


def test_dry_run_removes_nothing_and_reports(sdlc, tmp_path):
    _done(sdlc, "20")
    made = _files(sdlc, "20")
    out = _sweep(tmp_path, "--dry-run")
    assert all(p.exists() for p in made) and len(out["removed"]) == len(made)


def test_limit_bounds_one_sweep_and_the_next_finishes(sdlc, tmp_path):
    for g in ("30", "31", "32"):
        _done(sdlc, g)
    made = _files(sdlc, "30") + _files(sdlc, "31") + _files(sdlc, "32")
    first = _sweep(tmp_path, "--limit", "2")
    assert sum(not p.exists() for p in made) == 2 * len(FAMILY_FILES)
    assert first["kept"].get("limit", 0) == 1
    _sweep(tmp_path, "--limit", "2")
    assert not any(p.exists() for p in made)


def test_partial_prune_heals_on_the_next_sweep(sdlc, tmp_path):
    _done(sdlc, "21")
    made = _files(sdlc, "21")
    made[0].unlink()
    made[2].unlink()                    # a crash left a subset behind
    _sweep(tmp_path)
    assert not any(p.exists() for p in made)
    assert _sweep(tmp_path)["removed"] == []          # idempotent


def test_run_stop_markers_pruned_by_age_only(sdlc, tmp_path):
    d = sdlc / "state" / "run_stop"
    d.mkdir()
    old, fresh = d / "run-old.json", d / "run-new.json"
    old.write_text("{}")
    fresh.write_text("{}")
    ancient = time.time() - 40 * 86400
    os.utime(old, (ancient, ancient))
    _sweep(tmp_path)
    assert not old.exists() and fresh.exists()


def test_record_done_keeps_its_own_evidence_and_sweeps_an_older_terminal_goal(sdlc, tmp_path):
    _done(sdlc, "old1")
    older = _files(sdlc, "old1")
    goal = tmp_path / ".sdlc" / "goals" / "g1.md"
    goal.parent.mkdir(parents=True)
    goal.write_text("---\nstatus: in_progress\n---\n# g1\n")
    # phase/ is excluded: `_phase_marker_end` (pre-existing) unlinks it at every `done`
    mine = _files(sdlc, "g1", age=time.time(), families=FAMILY_FILES[:-1])
    proc = _run("loop.py", "record", ".sdlc", str(goal), "done", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert all(p.exists() for p in mine)              # read after `done` by the onboarding control
    assert not any(p.exists() for p in older)


def test_record_parked_keeps_the_files(sdlc, tmp_path):
    goal = tmp_path / ".sdlc" / "goals" / "g2.md"
    goal.parent.mkdir(parents=True)
    goal.write_text("---\nstatus: in_progress\n---\n# g2\n")
    made = _files(sdlc, "g2", age=time.time(), families=("verify/g2.json", "landing/g2.json"))
    proc = _run("loop.py", "record", ".sdlc", str(goal), "parked", "why", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert all(p.exists() for p in made)


def test_action_log_disabled_means_the_sweep_has_no_terminal_signal(sdlc, tmp_path):
    cfg = json.loads((sdlc / "config.json").read_text())
    cfg["action_log"] = {"enabled": False}
    (sdlc / "config.json").write_text(json.dumps(cfg))
    made = _files(sdlc, "40")                 # no log can exist: append() is a no-op when off
    _sweep(tmp_path)
    assert all(p.exists() for p in made)


def test_a_dir_with_no_log_row_such_as_a_slack_key_is_kept(sdlc, tmp_path):
    d = _agent(sdlc, tmp_path, "unit-x", _dead_pid())
    _sweep(tmp_path)
    assert d.exists()


def test_kept_stems_do_not_starve_prunable_ones_under_limit(sdlc, tmp_path):
    for i in range(5):                        # older than the prunable goal, never prunable
        g = f"5{i}"
        _log(sdlc, g, ("claimed", {}), age=OLD - 86400)
        _files(sdlc, g, age=OLD - 86400)
    _done(sdlc, "59")
    made = _files(sdlc, "59")
    _sweep(tmp_path, "--limit", "1")
    assert not any(p.exists() for p in made)


def test_work_record_kept_then_sweep_removes_once_finish_ran(sdlc, tmp_path):
    _done(sdlc, "g3")
    made = _files(sdlc, "g3")
    rec = sdlc / "state" / "work" / "g3.json"
    rec.parent.mkdir(parents=True)
    rec.write_text("{}")
    _sweep(tmp_path)
    assert all(p.exists() for p in made)
    rec.unlink()                                      # `work.py finish` ran later
    _sweep(tmp_path)
    assert not any(p.exists() for p in made)


def _module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("goal_state_prune", S / "goal_state_prune.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_reopen_between_scan_and_unlink_is_caught_by_the_reread(sdlc, monkeypatch):
    """Deterministic in-process control at the seam: the CLI cannot land a row inside the window."""
    mod = _module()
    _done(sdlc, "60")
    made = _files(sdlc, "60")
    real, calls = mod._terminal_stamp, []

    def reopened_after_scan(path, kinds):
        calls.append(path)
        return real(path, kinds) if len(calls) == 1 else None      # second read: no longer `done`
    monkeypatch.setattr(mod, "_terminal_stamp", reopened_after_scan)
    out = mod.sweep(sdlc, agent_alive=lambda *a, **k: ("dead", None))
    assert all(p.exists() for p in made) and out["kept"].get("reopened") == 1


def test_an_agent_registered_between_scan_and_unlink_is_caught_by_the_recheck(sdlc, tmp_path, monkeypatch):
    mod = _module()
    _done(sdlc, "61")
    made = _files(sdlc, "61")
    d = sdlc / "state" / "agents" / "61"
    d.mkdir(parents=True)
    (d / "main.active").write_text("1")
    verdicts = iter(["dead", "alive", "alive"])
    out = mod.sweep(sdlc, agent_alive=lambda *a, **k: (next(verdicts), None))
    assert all(p.exists() for p in made) and d.exists() and out["kept"].get("live-agent") == 1


def test_a_marker_recreated_late_on_a_done_goal_is_swept_after_the_grace(sdlc, tmp_path):
    _done(sdlc, "41")
    late = _files(sdlc, "41", age=time.time(), families=("unit-tracking/41.attempt",))[0]
    _sweep(tmp_path)
    assert late.exists()                      # inside the grace
    os.utime(late, (OLD, OLD))
    _sweep(tmp_path)
    assert not late.exists()


def test_a_symlinked_state_directory_is_never_swept(tmp_path):
    real = tmp_path / "elsewhere"
    (real / "verify").mkdir(parents=True)
    (real / "log").mkdir()
    mod = _module()
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "state").symlink_to(real, target_is_directory=True)
    (sdlc / "config.json").write_text(json.dumps({"action_log": {"enabled": True}}))
    _done(sdlc, "70")
    made = _files(sdlc, "70")
    assert mod.sweep(sdlc, agent_alive=lambda *a, **k: ("dead", None))["removed"] == []
    assert all(p.exists() for p in made)


def test_an_agent_check_that_raises_counts_as_alive(sdlc):
    mod = _module()
    _done(sdlc, "71")
    made = _files(sdlc, "71")
    d = sdlc / "state" / "agents" / "71"
    d.mkdir(parents=True)
    (d / "main.active").write_text("1")

    def boom(*a, **k):
        raise RuntimeError("cannot tell")
    out = mod.sweep(sdlc, agent_alive=boom)
    assert all(p.exists() for p in made) and out["kept"].get("live-agent") == 1

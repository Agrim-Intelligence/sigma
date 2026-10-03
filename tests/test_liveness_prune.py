"""#464 (B6): claim locks and session markers whose owner is provably dead are swept, nothing else.

Drives the documented gestures over a scratch `.sdlc`: the operator lever `liveness_prune.py sweep
.sdlc`, `goal_state_prune.py sweep .sdlc`, and the in-line triggers `loop.py record ... done` and
`loop.py start`. Owners are REAL processes: a lock holder is a subprocess that holds a kernel
`flock` and is later SIGKILLed; a live session is a process that is still running. The only
in-process test is the deterministic seam control for the acquire-vs-unlink race, which is not a
timing race test: the unlink is performed exactly between the acquirer's `open` and its `flock`.
"""
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"
STALE = time.time() - 2 * 86400          # far past the 12 h claim-lease TTL
OLD_DONE = time.time() - 10 * 86400      # past goal_state_prune's seven-day window

_HOLD = ("import sys, time; sys.path.insert(0, %r); import loop; "
         "fd = loop._try_acquire_claim_lock(sys.argv[1], sys.argv[2]); "
         "print('won' if fd not in (None, -1) else 'lost', flush=True); time.sleep(600)" % str(S))


def _run(script, *args, cwd):
    return subprocess.run([sys.executable, str(S / script), *args], cwd=cwd, capture_output=True,
                          text=True, timeout=120)


def _sweep(tmp_path, *extra):
    proc = _run("liveness_prune.py", "sweep", ".sdlc", *extra, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _ts(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


@pytest.fixture
def sdlc(tmp_path):
    d = tmp_path / ".sdlc"
    (d / "state" / "claims").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({
        "action_log": {"enabled": True}, "discovery": {"source": "local"},
        "verify": {"enforce": False}}))
    return d


def _lock(sdlc, name, age=STALE, text="4242\n"):
    path = sdlc / "state" / "claims" / f"{name}.lock"
    path.write_text(text)
    os.utime(path, (age, age))
    return path


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _names(out):
    return sorted(pathlib.Path(p).name for p in out["removed"])


_HOLDERS = []


@pytest.fixture(autouse=True)
def _reap_holders():
    yield
    for proc in _HOLDERS:                      # never leave a lock holder sleeping if a test fails early
        _kill9(proc)
    _HOLDERS.clear()


def _holder(sdlc, goal):
    proc = subprocess.Popen([sys.executable, "-c", _HOLD, str(sdlc), goal],
                            stdout=subprocess.PIPE, text=True)
    _HOLDERS.append(proc)
    assert proc.stdout.readline().strip() == "won"
    return proc


def _kill9(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGKILL)
    proc.wait()


def _agent_marker(sdlc, goal, content, thread="main"):
    path = sdlc / "state" / "agents" / goal / f"{thread}.active"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_dead_owner_claim_lock_is_removed(sdlc, tmp_path):
    lock = _lock(sdlc, "21")
    slack = _lock(sdlc, "slack-cmd-drift")
    out = _sweep(tmp_path)
    assert not lock.exists() and not slack.exists()
    assert _names(out) == ["21.lock", "slack-cmd-drift.lock"]


def test_fresh_claim_lock_is_kept(sdlc, tmp_path):
    fresh = _lock(sdlc, "22", age=time.time())
    six_hours = _lock(sdlc, "23", age=time.time() - 6 * 3600)         # inside the 12 h lease TTL
    out = _sweep(tmp_path)
    assert fresh.exists() and six_hours.exists()
    assert out["removed"] == [] and out["kept"].get("fresh") == 2


def test_lock_held_by_a_live_flock_holder_is_kept_even_when_old(sdlc, tmp_path):
    holder = _holder(sdlc, "24")
    try:
        lock = sdlc / "state" / "claims" / "24.lock"
        os.utime(lock, (STALE, STALE))                  # old on disk, yet a live process holds it
        inode = lock.stat().st_ino
        out = _sweep(tmp_path)
        assert lock.exists() and lock.stat().st_ino == inode
        assert out["removed"] == [] and out["kept"].get("held") == 1
    finally:
        _kill9(holder)
    assert _names(_sweep(tmp_path)) == ["24.lock"]


def test_agent_marker_state_decides_the_lock(sdlc, tmp_path):
    alive_lock, unknown_lock, dead_lock = _lock(sdlc, "25"), _lock(sdlc, "26"), _lock(sdlc, "27")
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        _agent_marker(sdlc, "25", f"{holder.pid}\n")                  # live pid, fresh marker
        _agent_marker(sdlc, "26", "not a pid at all")                 # unreadable standing
        _agent_marker(sdlc, "27", f"{_dead_pid()}\n")                 # provably dead
        out = _sweep(tmp_path)
        assert alive_lock.exists() and unknown_lock.exists()
        assert not dead_lock.exists()
        assert out["kept"].get("live-agent") == 2
    finally:
        holder.kill()
        holder.wait()


def test_goal_in_a_live_session_is_kept(sdlc, tmp_path):
    live, gone = _lock(sdlc, "31"), _lock(sdlc, "32")
    sessions = sdlc / "state" / "sessions"
    sessions.mkdir()
    hashed = _lock(sdlc, "33")
    (sessions / f"{os.getpid()}.active").write_text(
        json.dumps({"in_flight": ["31", "#33"], "settled_admissions": 1}))     # raw refs, `#` allowed
    (sessions / f"{_dead_pid()}.active").write_text(json.dumps({"in_flight": ["32"], "settled_admissions": 1}))
    out = _sweep(tmp_path)
    assert live.exists() and hashed.exists() and not gone.exists()
    assert out["kept"].get("in-flight") == 2


def test_without_flock_the_lock_family_refuses_loudly(sdlc, tmp_path):
    lock = _lock(sdlc, "34")
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "fcntl.py").write_text("raise ImportError('no flock on this platform')\n")
    env = dict(os.environ, PYTHONPATH=str(shim))
    proc = subprocess.run([sys.executable, str(S / "liveness_prune.py"), "sweep", ".sdlc"], cwd=tmp_path,
                          capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 2 and "not supported on this platform" in proc.stderr
    assert lock.exists()


def test_symlinks_and_neighbours_are_never_touched(sdlc, tmp_path):
    state = sdlc / "state"
    target = tmp_path / "outside.lock"
    target.write_text("x")
    os.utime(target, (STALE, STALE))
    link = state / "claims" / "41.lock"
    link.symlink_to(target)
    os.utime(link, (STALE, STALE), follow_symlinks=False)       # the link itself is old, so only the
    claimed_link = state / "claims" / "46.claimed"              # symlink refusal can save it
    claimed_link.symlink_to(target)
    os.utime(claimed_link, (STALE - 40 * 86400,) * 2, follow_symlinks=False)
    neighbours = [state / "claims" / "note.txt", state / "claims" / "42.claimed",
                  state / "claims" / ".lock", state / "claims" / "a..b.lock",
                  state / "STATE.md.lock", state / "merge-reconcile.lock",
                  state / "phase-end-aa.lock", state / "sessions" / "locks" / "1.lock",
                  state / "log" / "43.jsonl"]
    for path in neighbours:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        os.utime(path, (STALE, STALE))
    out = _sweep(tmp_path)
    assert target.exists() and link.is_symlink() and claimed_link.is_symlink()
    assert all(p.exists() for p in neighbours)
    assert out["removed"] == []
    # a symlinked claims directory is not followed either
    real = tmp_path / "elsewhere"
    real.mkdir()
    victim = real / "44.lock"
    victim.write_text("")
    os.utime(victim, (STALE, STALE))
    (state / "claims" / "note.txt").unlink()
    for entry in (state / "claims").iterdir():
        entry.unlink()
    (state / "claims").rmdir()
    (state / "claims").symlink_to(real)
    _sweep(tmp_path)
    assert victim.exists()


def test_dry_run_limit_and_rerun(sdlc, tmp_path):
    oldest, middle, newest = (_lock(sdlc, "51", age=STALE - 300), _lock(sdlc, "52", age=STALE - 200),
                              _lock(sdlc, "53", age=STALE - 100))     # scanned in name order
    dry = _sweep(tmp_path, "--dry-run")
    assert len(dry["removed"]) == 3 and all(p.exists() for p in (oldest, middle, newest))
    first = _sweep(tmp_path, "--limit", "2")
    assert len(first["removed"]) == 2
    assert not oldest.exists() and not middle.exists() and newest.exists()   # the first two scanned
    assert first["kept"].get("limit") == 1
    assert len(_sweep(tmp_path)["removed"]) == 1 and not newest.exists()
    assert _sweep(tmp_path)["removed"] == []                                  # idempotent


def test_kill9_slot_lock_reclaimed_and_live_sibling_kept(sdlc, tmp_path):
    crashed, sibling = _holder(sdlc, "61"), _holder(sdlc, "62")
    dead_lock = sdlc / "state" / "claims" / "61.lock"
    live_lock = sdlc / "state" / "claims" / "62.lock"
    try:
        for lock in (dead_lock, live_lock):
            os.utime(lock, (STALE, STALE))
        assert _sweep(tmp_path)["removed"] == []          # both holders alive: nothing is judged dead
        _kill9(crashed)                                   # the crashed goal slot
        out = _sweep(tmp_path)
        assert not dead_lock.exists() and live_lock.exists()
        assert _names(out) == ["61.lock"]
    finally:
        _kill9(sibling)
    assert _names(_sweep(tmp_path)) == ["62.lock"]        # its owner died too: now reclaimed


def test_dead_session_entries_removed_live_kept(sdlc, tmp_path):
    sessions = sdlc / "state" / "sessions"
    sessions.mkdir()
    dead, live = _dead_pid(), os.getpid()
    body = json.dumps({"in_flight": [], "settled_admissions": 1})
    codex = "01a0f113-bf96-7e90-b117-01af30afa7e7"
    entries = {"dead": sessions / f"{dead}.active", "dead-codex": sessions / f"{dead}-{codex}.active",
               "live": sessions / f"{live}.active",
               "temp-dead": sessions / f"{live}.active.{dead}.abc123",
               "temp-live": sessions / f"{live}.active.{live}.abc123"}
    for path in entries.values():
        path.write_text(body)
    dry = _sweep(tmp_path, "--dry-run")
    assert all(p.exists() for p in entries.values())
    assert {pathlib.Path(p).name for p in dry["removed"]} == {
        entries["dead"].name, entries["dead-codex"].name, entries["temp-dead"].name}
    _sweep(tmp_path)
    assert not entries["dead"].exists() and not entries["dead-codex"].exists()
    assert not entries["temp-dead"].exists()
    assert entries["live"].exists() and entries["temp-live"].exists()
    assert _sweep(tmp_path)["removed"] == []


def _log(sdlc, goal, *rows):
    path = sdlc / "state" / "log" / f"{goal}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for i, (kind, extra) in enumerate(rows):
            row = {"actor": "loop", "goal": goal, "kind": kind, "thread": "main", "ts": _ts(OLD_DONE + i)}
            row.update(extra)
            handle.write(json.dumps(row) + "\n")
    os.utime(path, (OLD_DONE, OLD_DONE))


def test_claimed_marker_is_pruned_by_age_only_and_owners_keep_it(sdlc, tmp_path):
    claims = sdlc / "state" / "claims"
    month_ago = time.time() - 31 * 86400
    names = {"old": "71", "young": "72", "worked": "73", "agent": "74", "unknown": "75"}
    for stem in names.values():
        (claims / f"{stem}.claimed").write_text("")
        os.utime(claims / f"{stem}.claimed", (month_ago, month_ago))
    os.utime(claims / "72.claimed", (time.time() - 5 * 86400,) * 2)             # inside 30 days
    (sdlc / "state" / "work").mkdir()
    (sdlc / "state" / "work" / "73.json").write_text("{}")                      # worktree record
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        _agent_marker(sdlc, "74", f"{holder.pid}\n")
        _agent_marker(sdlc, "75", "not a pid at all")
        out = _sweep(tmp_path)
    finally:
        holder.kill()
        holder.wait()
    assert not (claims / "71.claimed").exists()
    assert all((claims / f"{n}.claimed").exists() for n in ("72", "73", "74", "75"))
    assert _names(out) == ["71.claimed"]


def test_acquire_survives_an_unlink_between_open_and_flock(sdlc, monkeypatch):
    """Deterministic control at the exact seam (not a timing race): the file is unlinked (first
    scenario) or unlinked and recreated (second) after the acquirer's `open` and before its `flock`.
    Without the inode re-check the acquirer 'wins' a lock on an orphaned inode and a second acquirer,
    holding the file now at the path, ALSO wins."""
    sys.path.insert(0, str(S))
    import loop as lp
    real = lp.fcntl

    def run(name, recreate):
        lock = _lock(sdlc, name, text="")
        fired = []

        class Seam:
            def __getattr__(self, attr):
                return getattr(real, attr)

            def flock(self, fd, op):
                if not fired:
                    fired.append(True)
                    lock.unlink()               # what a sweep does the instant after our open()
                    if recreate:
                        lock.write_text("")     # a successor's fresh file, a different inode
                return real.flock(fd, op)

        monkeypatch.setattr(lp, "fcntl", Seam())
        first = lp._try_acquire_claim_lock(str(sdlc), name)
        try:
            assert fired and first not in (None, lp._LOCK_UNAVAILABLE)
            assert lock.exists(), "the winner must hold the lock on the file now at the path"
            assert os.fstat(first).st_ino == lock.stat().st_ino
            second = lp._try_acquire_claim_lock(str(sdlc), name)
            assert second is None, "a second acquirer must be refused while the first holds the lock"
        finally:
            if first not in (None, lp._LOCK_UNAVAILABLE):
                os.close(first)

    run("81", recreate=False)
    run("82", recreate=True)


def test_record_done_and_session_start_run_the_sweep(sdlc, tmp_path):
    goal = sdlc / "goals" / "g1.md"
    goal.parent.mkdir(parents=True)
    goal.write_text("---\nstatus: in_progress\n---\n# g1\n")
    stale_a, stale_b = _lock(sdlc, "91"), _lock(sdlc, "92")   # `start` sweeps both
    proc = _run("loop.py", "start", ".sdlc", "--session-pid", str(os.getpid()), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert not stale_a.exists() and not stale_b.exists()
    stale_c = _lock(sdlc, "93")
    proc = _run("loop.py", "record", ".sdlc", str(goal), "done", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert not stale_c.exists()


def test_hook_budget_bounds_examination_not_only_removal(sdlc):
    """Extra (not in the plan's red-proven list): a spent wall-clock budget stops the scan itself."""
    sys.path.insert(0, str(S))
    import liveness_prune
    locks = [_lock(sdlc, f"{n}") for n in range(100, 110)]
    out = liveness_prune.sweep(sdlc, budget_seconds=0)
    assert out["removed"] == [] and out["kept"].get("budget") == 1 and all(p.exists() for p in locks)
    done = liveness_prune.sweep(sdlc, budget_seconds=60)
    assert len(done["removed"]) == 10


def test_sweeper_does_not_unlink_a_file_swapped_in_after_its_flock(sdlc, monkeypatch):
    """Extra, deterministic seam control for the sweeper side: after the sweep wins the flock the
    path is replaced by a NEW file (a successor's lock). The sweep must see the inode changed and
    leave the successor alone."""
    sys.path.insert(0, str(S))
    import liveness_prune as lpr
    lock = _lock(sdlc, "120")
    real = lpr.fcntl
    swapped = []

    class Seam:
        def __getattr__(self, name):
            return getattr(real, name)

        def flock(self, fd, op):
            real.flock(fd, op)
            if not swapped:
                swapped.append(True)
                lock.unlink()
                lock.write_text("successor")        # a different inode, fresh mtime

    monkeypatch.setattr(lpr, "fcntl", Seam())
    out = lpr.sweep(sdlc)
    assert swapped and out["removed"] == [] and out["kept"].get("swapped") == 1
    assert lock.read_text() == "successor"


def test_sweeper_rechecks_the_mtime_after_winning_the_flock(sdlc, monkeypatch):
    """Extra seam control: a lock re-stamped (a fresh acquisition that already released) between the
    scan and the flock is fresh again and must be kept, even though the scan judged it old."""
    sys.path.insert(0, str(S))
    import liveness_prune as lpr
    lock = _lock(sdlc, "121")
    real = lpr.fcntl

    class Seam:
        def __getattr__(self, name):
            return getattr(real, name)

        def flock(self, fd, op):
            real.flock(fd, op)
            os.utime(lock, None)                  # re-stamped just now, same inode

    monkeypatch.setattr(lpr, "fcntl", Seam())
    out = lpr.sweep(sdlc)
    assert lock.exists() and out["removed"] == [] and out["kept"].get("fresh") == 1

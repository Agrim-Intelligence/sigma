"""#464: the kernel-lock marker families never read as held after their owner dies.

The singleton and stripe locks (`STATE.md.lock`, `merge-reconcile.lock`, `knowledge-sync.lock`,
`feature-judge-spend.lock`, `phase-end-<stripe>.lock`, `sessions/locks/<slot>.lock`) are `flock`
files that Sigma never deletes (deleting one admits a second holder), so their cap is their file
count. What must hold instead is the LIVENESS property: while a real process holds one it is held,
and the instant that process is SIGKILLed (a reboot is the same) the very next acquire succeeds
with no sweep and no human. These tests pin the existing behaviour, so they are green on the old
code by design; their control (remove one `flock` call and watch the 'held while alive' assertion
go red) is recorded in `docs/launch/b6-claims-sessions-locks.md`.

Also pins the safety claim the `.claimed` prune relies on: `_ensure_claimed` on a missing marker
re-reads the ledger, finds the existing claim, appends nothing and only re-touches.
"""
import fcntl
import os
import pathlib
import signal
import subprocess
import sys

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"

_PRE = "import sys, os, time; sys.path.insert(0, %r); sd = sys.argv[1]\n" % str(S)

#: name -> (lock-file glob under state/, code that HOLDS it until killed, code that ENTERS it and
#: exits 0 only when it was acquired). Blocking locks are entered with a subprocess timeout.
CASES = {
    "claim": ("claims/7.lock",
              "import loop\nfd = loop._try_acquire_claim_lock(sd, '7')\nassert fd not in (None, -1)\n"
              "print('held', flush=True); time.sleep(600)\n",
              "import loop\nfd = loop._try_acquire_claim_lock(sd, '7')\nsys.exit(0 if fd not in (None, -1) else 1)\n"),
    "merge-reconcile": ("merge-reconcile.lock",
              "import loop\nwith loop._merge_reconcile_lock(sd) as held:\n    assert held is True\n"
              "    print('held', flush=True); time.sleep(600)\n",
              "import loop\nwith loop._merge_reconcile_lock(sd) as held:\n    sys.exit(0 if held is True else 1)\n"),
    "knowledge-sync": ("knowledge-sync.lock",
              "import sync\nf = sync._knowledge_lock(sd)\nassert f not in (None, True)\n"
              "print('held', flush=True); time.sleep(600)\n",
              "import sync\nf = sync._knowledge_lock(sd)\nsys.exit(0 if f not in (None, True) else 1)\n"),
    "feature-judge-spend": ("feature-judge-spend.lock",
              "import feature_judge as fj\nfd = fj._acquire_spend_lock(sd, timeout_s=5)\nassert fd not in (None, -1)\n"
              "print('held', flush=True); time.sleep(600)\n",
              "import feature_judge as fj\nfd = fj._acquire_spend_lock(sd, timeout_s=5)\nsys.exit(0 if fd not in (None, -1) else 1)\n"),
    "state-cursor": ("STATE.md.lock",
              "import state\nwith state._cursor_lock(sd):\n    print('held', flush=True); time.sleep(600)\n",
              "import state\nwith state._cursor_lock(sd):\n    sys.exit(0)\n"),
    "phase-end-stripe": ("phase-end-*.lock",
              "import state\nwith state.phase_end_lock(sd, 'attempt-1'):\n    print('held', flush=True); time.sleep(600)\n",
              "import state\nwith state.phase_end_lock(sd, 'attempt-1'):\n    sys.exit(0)\n"),
    "session-stripe": ("sessions/locks/*.lock",
              "import loop\nloop._session_locked(sd, 4242, lambda: (print('held', flush=True), time.sleep(600)),"
              " require_lock=True)\n",
              "import loop\nloop._session_locked(sd, 4242, lambda: None, require_lock=True)\nsys.exit(0)\n"),
}


def _flocked_elsewhere(path):
    """True when some other open file description holds `path` (a non-blocking probe)."""
    fd = os.open(str(path), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


@pytest.mark.parametrize("name", sorted(CASES))
def test_lock_is_held_while_the_owner_lives_and_free_the_instant_it_is_killed(name, tmp_path):
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    glob, hold, enter = CASES[name]
    owner = subprocess.Popen([sys.executable, "-c", _PRE + hold, str(sdlc)], stdout=subprocess.PIPE,
                             text=True)
    try:
        assert owner.stdout.readline().strip() == "held"
        files = sorted((sdlc / "state").glob(glob))
        assert files, glob
        assert all(_flocked_elsewhere(p) for p in files), "a lock a live process holds must read held"
    finally:
        owner.send_signal(signal.SIGKILL)         # a crash; a reboot is the same
        owner.wait()
    assert not any(_flocked_elsewhere(p) for p in files), "a dead owner's lock must read free"
    entered = subprocess.run([sys.executable, "-c", _PRE + enter, str(sdlc)], capture_output=True,
                             text=True, timeout=60)
    assert entered.returncode == 0, entered.stderr
    assert all(p.exists() for p in files)          # the file stays: capped, never deleted


def test_ensure_claimed_after_a_marker_prune_appends_no_second_claim(tmp_path, monkeypatch):
    """The `.claimed` prune is safe because a missing marker only costs one ledger read."""
    sys.path.insert(0, str(S))
    import loop as lp
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    claims, relabels = [], []
    monkeypatch.setattr(lp.state, "load_config", lambda _d: {"ledger": {"enabled": True}})
    monkeypatch.setattr(lp.ledger, "enabled", lambda _c: True)
    monkeypatch.setattr(lp.ledger, "read_all", lambda _d: [{"kind": "claimed", "goal": "9"}])
    monkeypatch.setattr(lp, "_claim", lambda *a, **k: claims.append(a))
    monkeypatch.setattr(lp.sources, "get_source", lambda *a, **k: relabels.append(a) or object())
    marker = lp._claim_marker_path(str(sdlc), "9")
    assert not marker.exists()                      # as after a prune
    lp._ensure_claimed(str(sdlc), "9")
    assert claims == [], "no second claim may be written for an already-claimed goal"
    assert marker.exists(), "the marker is re-touched so later verbs stay O(1)"

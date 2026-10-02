#!/usr/bin/env python3
"""Sweep of liveness markers whose owner is provably dead (#464, B6).

    python3 skills/agrim-loop/scripts/liveness_prune.py sweep .sdlc [--dry-run] [--limit N]

Owned and removed (repo-local, gitignored, written by Sigma Python; nothing under a host
configuration root):

* `state/claims/<stem>.lock` (and `slack-cmd-<name>.lock`): the per-goal pick mutex. The pick's
  own CLI exits within moments, so the file is residue by design; production code never deleted it
  (`loop.reclaim_stale_claim_lock` has no caller). Removed only when ALL hold: a regular file in a
  real `claims/` directory; mtime older than max(claim lease TTL, 1 h) (so a lock inside its acquire
  and stamp window is never judged); no alive or unknown `agents/<stem>/*.active` marker; the goal in no
  LIVE session's `in_flight` list; and this process WINS a non-blocking `flock` on it (a live holder
  is therefore never touched), still holds the same inode as the path, and the mtime is still old.
  Without `fcntl` nothing is removed.
* `state/claims/<stem>.claimed`: the durable "claim established here" cache marker, removed when
  older than CLAIMED_KEEP_DAYS with no work record and no alive or unknown agent marker. Safe:
  `loop._ensure_claimed` on a missing marker re-reads the ledger, finds the claim, re-touches.
* `state/sessions/<pid>[-<thread>].active` and `*.active.<pid>.*` temp files of dead writers (the
  operator lever only; `loop.py start` already prunes them): the existing
  `loop._prune_dead_session_entries` (pid liveness through `ledger.pid_alive`, lease TTL as the
  pid-reuse backstop, per-entry stripe lock). A machine that never starts another session can still
  be swept by the lever.

Deliberately NOT touched: the 256-stripe locks (`sessions/locks`, `phase-end-*`) and the singleton
flock files (`STATE.md.lock`, `merge-reconcile.lock`, `knowledge-sync.lock`,
`feature-judge-spend.lock`) -- deleting a flock file admits a second holder; `slack-commands.lock`
(a directory with its own reclaim); `state/heartbeat`. Unlink only, no recursion, symlinks never followed,
`missing_ok`; idempotent, so a crash mid-sweep leaves a subset the next run finishes. Never raises.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import stat
import sys
import time

try:
    import fcntl
except ImportError:                                  # Windows: no flock, so nothing is provable
    fcntl = None

_HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_LIMIT = 200
_DEFAULT_TTL_SECONDS = 12 * 3600                     # ledger.DEFAULT_LEASE_TTL_HOURS, if config says none
_MIN_TTL_SECONDS = 3600                              # floor: a tiny configured TTL must not shrink the
#                                                      "never judge a lock mid-acquire" window
CLAIMED_KEEP_DAYS = 30
HOOK_BUDGET_SECONDS = 5.0


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_dir(path):
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def _regular(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _live_in_flight(loop, sdlc_dir, config):
    """Stems of every goal registered in flight by a LIVE session."""
    work = _load("work")
    stems = set()
    for pid, path, in_flight in loop._session_entries(sdlc_dir):
        if loop._session_pid_live(pid, path, config):
            stems.update(work.stem(str(g)).lstrip("#") for g in in_flight)
    return stems


def _agents_dead(loop, agents_dir, sdlc_dir, stem, config):
    """True when no thread marker under state/agents/<stem>/ is alive or of unknown standing."""
    d = agents_dir / stem
    if d.is_symlink():
        return False
    threads = sorted(p.stem for p in d.glob("*.active")) if _real_dir(d) else []
    for thread in threads:
        try:
            verdict = loop.agent_alive(sdlc_dir, stem, config, thread=thread)[0]
        except Exception:                            # noqa: BLE001 - fail toward inaction
            verdict = "alive"
        if verdict != "dead":
            return False
    return True


def _try_remove_lock(path, cutoff):
    """Unlink `path` only after winning its flock; -> 'removed' | 'held' | 'fresh' | 'gone' | 'swapped'."""
    try:
        fd = os.open(str(path), os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return "gone"
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "held"                            # a live holder: never raced
        try:
            held, here = os.fstat(fd), os.lstat(path)
        except OSError:
            return "gone"
        if (held.st_dev, held.st_ino) != (here.st_dev, here.st_ino) or not stat.S_ISREG(here.st_mode):
            return "swapped"                         # a successor already replaced the file
        if held.st_mtime >= cutoff:
            return "fresh"                           # re-stamped since the scan
        path.unlink(missing_ok=True)
        return "removed"
    finally:
        os.close(fd)                                 # releases the flock


def sweep(sdlc_dir, config=None, limit=DEFAULT_LIMIT, now=None, dry_run=False, sessions=True,
          budget_seconds=None):
    """-> {"removed": [paths], "kept": {reason: count}}. `limit` caps removals, never examinations
    (0 or less = no cap); `budget_seconds` stops removing once that much wall clock has passed."""
    result = {"removed": [], "kept": {}}

    def keep(reason, n=1):
        result["kept"][reason] = result["kept"].get(reason, 0) + n

    try:
        started = time.monotonic()
        loop, state, work, ledger = _load("loop"), _load("state"), _load("work"), _load("ledger")
        now = time.time() if now is None else now
        sdlc_dir = pathlib.Path(sdlc_dir)
        state_dir = sdlc_dir / "state"
        if state_dir.is_symlink() or not state_dir.is_dir():
            return result
        if config is None:
            config = state.load_config(sdlc_dir)
        if sessions:
            _sweep_sessions(loop, sdlc_dir, config, result, dry_run)
        claims = state_dir / "claims"
        if not _real_dir(claims):
            return result
        agents_dir = state_dir / "agents"
        cap = int(limit) if int(limit) > 0 else None
        ttl = max(ledger.lease_ttl_seconds(config) or _DEFAULT_TTL_SECONDS, _MIN_TTL_SECONDS)
        cutoff = now - ttl
        claimed_cutoff = now - CLAIMED_KEEP_DAYS * 86400
        in_flight = None
        refused_flock = False
        removed = 0
        # One pass, removing as it goes: the cap and the wall-clock budget bound EXAMINATION too,
        # so a hook never pays for a huge backlog; what is left is reached by the next call.
        for path in sorted(claims.iterdir()):
            if cap is not None and removed >= cap:
                keep("limit")
                break
            if budget_seconds is not None and time.monotonic() - started > budget_seconds:
                keep("budget")
                break
            kind = ".lock" if path.name.endswith(".lock") else ".claimed" if path.name.endswith(".claimed") else None
            if kind is None:
                continue
            stem = path.name[:-len(kind)]
            if not stem or state.unsafe_goal_reason(stem) or work.stem(stem) != stem or not _regular(path):
                keep("not-owned")
                continue
            try:
                mtime = path.lstat().st_mtime
            except OSError:
                continue
            if mtime >= (cutoff if kind == ".lock" else claimed_cutoff):
                keep("fresh")
                continue
            if (state_dir / "work" / f"{stem}.json").exists() and kind == ".claimed":
                keep("work-record")                  # bounded by the worktree's own lifecycle (#458)
                continue
            if not _agents_dead(loop, agents_dir, sdlc_dir, stem, config):
                keep("live-agent")
                continue
            if kind == ".claimed":
                if not dry_run:
                    path.unlink(missing_ok=True)
                removed += 1
                result["removed"].append(str(path))
                continue
            if in_flight is None:
                in_flight = _live_in_flight(loop, sdlc_dir, config)
            if stem in in_flight:
                keep("in-flight")
                continue
            if fcntl is None:
                if not refused_flock:
                    print("liveness_prune: claim-lock sweep is not supported on this platform "
                          "(no flock); nothing removed", file=sys.stderr)
                    refused_flock = True
                keep("no-flock")
                continue
            outcome = "removed" if dry_run else _try_remove_lock(path, cutoff)
            if outcome == "removed":
                removed += 1
                result["removed"].append(str(path))
            else:
                keep(outcome if outcome in ("held", "fresh", "swapped") else "gone")
    except Exception as exc:                         # noqa: BLE001 - a sweep never raises into a caller
        print(f"liveness_prune: sweep stopped ({exc})", file=sys.stderr)
    return result


def _sweep_sessions(loop, sdlc_dir, config, result, dry_run):
    session_dir = loop._session_dir(sdlc_dir)
    if session_dir.is_symlink() or not session_dir.is_dir():
        return
    dead = []
    now = time.time()
    for temp in session_dir.glob("*.active.*"):
        try:
            writer = temp.name.split(".active.", 1)[1].split(".", 1)[0]
            if (writer.isdigit() and not loop.ledger.pid_alive(int(writer))) or \
                    now - temp.stat().st_mtime >= 24 * 3600:
                dead.append(temp)
        except (OSError, IndexError, ValueError):
            pass
    for pid, path, _in_flight in loop._session_entries(sdlc_dir):
        if not loop._session_pid_live(pid, path, config):
            dead.append(path)
    if not dry_run:
        loop._prune_dead_session_entries(sdlc_dir, config)
        dead = [p for p in dead if not p.exists()]   # report only what the shared pruner removed
    result["removed"].extend(str(p) for p in dead)


def after_start_or_done(sdlc_dir, config=None):
    """The in-line trigger (`loop.py start`, `record ... done`): a bounded sweep, fail-open. Sessions
    are left to `loop.py start`'s own prune (see the module docstring)."""
    return sweep(sdlc_dir, config=config, sessions=False, budget_seconds=HOOK_BUDGET_SECONDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", choices=["sweep"])
    parser.add_argument("sdlc_dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="removals per run (default %d; 0 = no cap)" % DEFAULT_LIMIT)
    args = parser.parse_args(argv)
    out = sweep(args.sdlc_dir, limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(out, indent=2))
    return 2 if out["kept"].get("no-flock") else 0


if __name__ == "__main__":
    sys.exit(main())

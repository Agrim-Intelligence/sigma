#!/usr/bin/env python3
"""Bounded retention for the per-goal action log and witness streams (#457, B6 #419).

Owns exactly two paths, both written by Sigma's own Python and both repo-local (gitignored):

  `.sdlc/state/log/<goal>.jsonl`      written by `actionlog.append`
  `.sdlc/state/witness/<goal>.jsonl`  written by `witness.record`

Neither lives under a host configuration root, and this module never reaches one: it only unlinks
those two direct children of those two directories. Nothing else, no `rmtree`, no glob beyond the
log directory's own `*.jsonl`.

A goal's streams are removed only when ALL of these hold (otherwise the goal is kept, with a reason):

  1. its action log's newest code-written row (`actionlog.INTERNAL_KINDS`, which an agent cannot
     forge from the CLI) is `recorded` with `result == "done"`. Parked, failed, awaiting-merge
     (`result == "review"`), reopened (a later `claimed`/`gate`/`verify_run`) and never-recorded
     goals are therefore never candidates, and a stream with no action log at all (witness only)
     cannot prove it is closed, so it is kept;
  2. the log and the witness file are older than the window, by mtime AND by their last row's `ts`;
  3. no owner marker is FRESH (touched inside the window): `claims/<g>.claimed`, `agents/<g>/`,
     `work/<g>.json`, `phase/<g>.json`, or a `.sdlc/work/<g>` worktree directory. `.claimed` is
     written once and never removed, and an agent marker survives a crashed goal, so presence alone
     would make most closed goals unprunable; staleness is what separates a leftover from an owner.
     Short-lived `claims/<g>.lock` mutexes are not markers.

Window: `action_log.retention_days`, default 90, floored at 30 (the doctor's dispatch-compliance
window reads these logs for 30 days). `false` disables. One sweep removes at most `limit` goals
(default 200), oldest first, so the first run on a large backlog is polite; the next sweep continues.

Recovery without a human: the witness file is unlinked first and the log (the proof of closure)
last, so a crash between the two leaves a still-eligible log and the next sweep finishes. A restored
backup is re-pruned by the same rule. Size and mtime are re-checked immediately before each unlink;
a writer that appends after that check recreates a fresh file holding only its own rows (history of
an already-merged goal, never state a reader needs). Never raises, never `mkdir`s, never pauses a
process.

Not covered, deliberately: a log with more than 8 KiB of agent rows after its `recorded` row (kept, never guessed at); local-mode witness files, which `witness.path` names from the raw goal
path (`goals_x.md.jsonl`) rather than the goal stem; a live goal's own growth (see the doc).
"""
import calendar
import importlib.util
import json
import pathlib
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:                                   # noqa: BLE001
    pass

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state = _load("state")
actionlog = _load("actionlog")

DEFAULT_KEEP_DAYS = 90
MIN_KEEP_DAYS = 30
DEFAULT_LIMIT = 200
#: Bytes read from the end of a log to find its newest code-written row. A line is at most ~660
#: bytes, so this covers a dozen rows; a log with no internal row in its tail is kept, not guessed at.
TAIL_BYTES = 8192


def keep_days(config):
    """-> days, or None when disabled. Anything but an int (or `false`) is the default."""
    raw = (config.get("action_log") or {}).get("retention_days") if isinstance(config, dict) else None
    if raw is False:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return max(raw, MIN_KEEP_DAYS)
    return DEFAULT_KEEP_DAYS


def _epoch(ts):
    try:
        return calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None


def _plain_dir(path):
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def _plain_file(path):
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _tail_rows(path):
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - TAIL_BYTES))
        data = handle.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]                           # first line is cut mid-row
    rows = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("kind"):
            rows.append(item)
    return rows                                     # file (append) order, not ts: no clock dependence


def _fresh(path, cutoff):
    """True when `path` (a file, or a directory plus its direct children) was touched after cutoff."""
    try:
        times = [path.lstat().st_mtime]
        if path.is_dir() and not path.is_symlink():
            times += [c.lstat().st_mtime for c in path.iterdir()]
    except FileNotFoundError:
        return False
    except OSError:
        return True                                 # cannot tell -> treat as an owner
    return max(times) >= cutoff


def _live_owner(sdlc, stem, cutoff):
    st = sdlc / "state"
    for marker in (st / "claims" / f"{stem}.claimed", st / "agents" / stem,
                   st / "work" / f"{stem}.json", st / "phase" / f"{stem}.json",
                   sdlc / "work" / stem):
        if _fresh(marker, cutoff):
            return marker.relative_to(sdlc).as_posix()
    return None


def _judge(sdlc, stem, cutoff):
    """-> (None, fingerprint) when prunable, else (reason, None)."""
    log = sdlc / "state" / "log" / f"{stem}.jsonl"
    wit = sdlc / "state" / "witness" / f"{stem}.jsonl"
    if state.unsafe_goal_reason(stem):
        return "unsafe goal name", None
    if not _plain_file(log):
        return ("no action log (closure unprovable)" if _plain_file(wit)
                else "stream is not a regular file"), None
    if (wit.exists() or wit.is_symlink()) and not _plain_dir(wit.parent):
        return "witness directory is not a plain directory", None
    streams = [log] + ([wit] if wit.exists() or wit.is_symlink() else [])
    if len(streams) == 2 and not _plain_file(wit):
        return "stream is not a regular file", None
    try:
        rows = _tail_rows(log)
        prints = [(p, p.stat().st_mtime, p.stat().st_size) for p in streams]
    except OSError:
        return "unreadable", None
    if any(m >= cutoff for _p, m, _s in prints):
        return "inside the retention window", None
    last = _epoch(rows[-1].get("ts")) if rows else None
    if last is None or last >= cutoff:
        return "inside the retention window", None
    newest = next((r for r in reversed(rows) if r.get("kind") in actionlog.INTERNAL_KINDS), None)
    if not (newest and newest.get("kind") == "recorded" and newest.get("result") == "done"):
        return "goal is not closed as done", None
    owner = _live_owner(sdlc, stem, cutoff)
    if owner:
        return f"fresh owner marker {owner}", None
    return None, prints


def prune_closed_goal_streams(sdlc_dir, keep=None, now=None, dry_run=False, limit=DEFAULT_LIMIT):
    """-> {"removed": [stem...], "kept": {stem: reason}, "disabled": bool}. Never raises."""
    result = {"removed": [], "kept": {}, "disabled": False}
    try:
        sdlc = pathlib.Path(sdlc_dir)
        days = keep if keep is not None else keep_days(state.load_config(sdlc_dir))
        if days is None:
            result["disabled"] = True
            return result
        days = max(int(days), MIN_KEEP_DAYS)
        cutoff = (now if now is not None else time.time()) - days * 86400
        logs = sdlc / "state" / "log"
        wits = sdlc / "state" / "witness"
        if not _plain_dir(sdlc / "state") or not _plain_dir(logs):
            return result
        stems = {p.stem for p in logs.glob("*.jsonl")}
        if _plain_dir(wits):
            for p in wits.glob("*.jsonl"):
                if p.stem not in stems:
                    result["kept"][p.stem] = "no action log (closure unprovable)"
        order = []
        for stem in sorted(stems):
            try:
                mtime = (logs / f"{stem}.jsonl").lstat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                result["kept"][stem] = "inside the retention window"
            else:
                order.append((mtime, stem))
        for _mtime, stem in sorted(order):
            reason, prints = _judge(sdlc, stem, cutoff)
            if reason:
                result["kept"][stem] = reason
                continue
            if len(result["removed"]) >= limit:
                result["kept"][stem] = "over this sweep's limit (next sweep continues)"
                continue
            if not dry_run and not _unlink_streams(sdlc, stem, prints, cutoff):
                result["kept"][stem] = "changed during the sweep"
                continue
            result["removed"].append(stem)
    except Exception as exc:                        # noqa: BLE001 - retention must never cost a goal
        print(f"retention: sweep skipped (non-fatal): {exc}", file=sys.stderr)
    return result


def _unlink_streams(sdlc, stem, prints, cutoff):
    """Re-check right before deleting; witness first, the log (proof of closure) last."""
    try:
        for path, mtime, size in prints:
            st = path.lstat()
            if st.st_mtime != mtime or st.st_size != size:
                return False
        if _live_owner(sdlc, stem, cutoff):
            return False
        for path, _m, _s in reversed(prints):       # prints = [log, witness?]
            path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def cli(argv):
    """`loop.py prune-state <sdlc> [--dry-run] [--keep-days N] [--limit N]`"""
    usage = "usage: loop.py prune-state <dir> [--dry-run] [--keep-days N] [--limit N]"
    if argv and argv[0] in ("-h", "--help"):
        print(usage)
        return 0
    if not argv:
        print(usage, file=sys.stderr)
        return 2
    sdlc_dir, flags = argv[0], argv[1:]
    dry = "--dry-run" in flags
    known = {"--dry-run", "--keep-days", "--limit"}
    values = {flags[i + 1] for i, f in enumerate(flags[:-1]) if f in ("--keep-days", "--limit")}
    stray = [f for f in flags if f not in known and f not in values]
    if stray:
        print(f"loop.py prune-state: unknown argument {stray[0]!r} "
              "(expected --dry-run, --keep-days N, --limit N)", file=sys.stderr)
        return 2
    keep = limit = None
    try:
        if "--keep-days" in flags:
            keep = int(flags[flags.index("--keep-days") + 1])
        if "--limit" in flags:
            limit = int(flags[flags.index("--limit") + 1])
    except (ValueError, IndexError):
        print("loop.py prune-state: --keep-days and --limit take an integer", file=sys.stderr)
        return 2
    if limit is not None and limit < 1:
        print("loop.py prune-state: --limit must be at least 1", file=sys.stderr)
        return 2
    res = prune_closed_goal_streams(sdlc_dir, keep=keep, dry_run=dry,
                                    limit=DEFAULT_LIMIT if limit is None else limit)
    if res["disabled"]:
        print("prune-state: disabled (action_log.retention_days is false)")
        return 0
    for stem in res["removed"]:
        print(f"{'would remove' if dry else 'removed'} {stem}")
    for stem, why in sorted(res["kept"].items()):
        print(f"kept {stem}: {why}")
    print(f"prune-state: {len(res['removed'])} goal(s) {'would be pruned' if dry else 'pruned'}, "
          f"{len(res['kept'])} kept")
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))

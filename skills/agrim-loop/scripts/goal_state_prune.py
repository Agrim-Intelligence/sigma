#!/usr/bin/env python3
"""Bounded removal of per-goal state records once their goal is terminal and old (#458, B6).

    python3 skills/agrim-loop/scripts/goal_state_prune.py sweep .sdlc [--dry-run] [--limit N]

Owned and removed (repo-local, gitignored, written by Sigma Python; nothing under a host
configuration root): `state/{verify,landing,propagation,escalation,phase}/<goal>.json`,
`state/unit-tracking/<goal>.attempt`, a dead `state/agents/<goal>/`, and `state/run_stop/*.json`
older than RUN_STOP_KEEP_DAYS. Deliberately NOT touched: `state/work` (the worktree's own record,
removed by `work.finish`), `state/withheld`, `state/goal-review`, the action log and witness streams.

A goal is terminal when the newest `actionlog.INTERNAL_KINDS` row of its log is `recorded
result=done` (a reopened goal appends `claimed`; parked, failed and awaiting-merge never wrote
`done`). That signal needs `action_log.enabled`; without it no goal is ever terminal and only
`run_stop` ageing runs. Beyond terminal, a goal is kept while it has a work record, a live or unknown agent marker, or
anything younger than MIN_AGE_SECONDS (7 days). The window is not a race guard only: the verify
record is a frozen contract kind (contract/README.md) that a downstream ingester and
tools/onboarding_control.py read AFTER `record done`, so nothing is removed at `done` itself.
Unlink only, no recursion, symlinks never followed. Idempotent: a crash mid-prune leaves a subset
and the next sweep finishes it. Never raises into a caller.
"""
import argparse
import calendar
import importlib.util
import json
import os
import pathlib
import stat
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent

#: (state subdirectory, file suffix) of every per-goal file removed once the goal is done.
FILE_FAMILIES = (("verify", ".json"), ("landing", ".json"), ("propagation", ".json"),
                 ("escalation", ".json"), ("unit-tracking", ".attempt"), ("phase", ".json"))
MIN_AGE_SECONDS = 7 * 86400
RUN_STOP_KEEP_DAYS = 30
DEFAULT_LIMIT = 200
_LOG_TAIL_BYTES = 262144


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _regular(path):
    """True for a real regular file that is not a symlink, inside a real (non-symlink) directory."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode) and not path.parent.is_symlink()
    except OSError:
        return False


def _real_dir(path):
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def _stem_files(state_dir, stem):
    """Existing owned files for one stem (never a symlink, never inside a symlinked directory)."""
    found = []
    for family, suffix in FILE_FAMILIES:
        path = state_dir / family / (stem + suffix)
        if _regular(path):
            found.append(path)
    return found


def _parse_ts(text):
    try:
        return calendar.timegm(time.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def _terminal_stamp(log_path, internal_kinds):
    """Epoch of the log's newest internal row when it is `recorded result=done`, else None."""
    try:
        if log_path.is_symlink() or not log_path.is_file():
            return None
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _LOG_TAIL_BYTES))
            lines = handle.read().decode("utf-8", "replace").splitlines()
        mtime = log_path.stat().st_mtime
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("kind") not in internal_kinds:
            continue
        if row.get("kind") != "recorded" or row.get("result") != "done":
            return None
        stamp = _parse_ts(row.get("ts"))
        return max(mtime, stamp) if stamp is not None else None
    return None


def _agents_dir_dead(sdlc_dir, stem, config, agent_alive):
    """True when no thread marker under state/agents/<stem>/ is alive or of unknown standing."""
    if (pathlib.Path(sdlc_dir) / "state" / "agents").is_symlink():
        return False                              # a redirected markers root cannot be trusted
    d = pathlib.Path(sdlc_dir) / "state" / "agents" / stem
    threads = sorted(p.stem for p in d.glob("*.active")) if _real_dir(d) else []
    for thread in threads:
        try:
            verdict = agent_alive(sdlc_dir, stem, config, thread=thread)[0]
        except Exception:                        # noqa: BLE001 - fail toward inaction
            verdict = "alive"
        if verdict != "dead":
            return False
    return True


def sweep(sdlc_dir, limit=DEFAULT_LIMIT, now=None, dry_run=False, agent_alive=None, config=None,
          min_age_seconds=MIN_AGE_SECONDS):
    """Prune terminal goals' owned files. -> {"removed": [paths], "kept": {reason: count}}.

    `limit` caps the GOALS PRUNED per call, never the goals examined, so permanently kept stems
    (a surviving work record, a slack-keyed agent dir) cannot starve the prunable ones behind them.
    The scan costs one stat or one bounded log-tail read per stem that still has an owned file."""
    result = {"removed": [], "kept": {}}

    def keep(reason):
        result["kept"][reason] = result["kept"].get(reason, 0) + 1

    try:
        work, state, actionlog = _load("work"), _load("state"), _load("actionlog")
        now = time.time() if now is None else now
        sdlc_dir = pathlib.Path(sdlc_dir)
        state_dir = sdlc_dir / "state"
        if state_dir.is_symlink() or not state_dir.is_dir():
            return result
        if config is None:
            config = state.load_config(sdlc_dir)
        if agent_alive is None:
            agent_alive = _load("loop").agent_alive
        stems = set()
        for family, suffix in FILE_FAMILIES:
            d = state_dir / family
            if _real_dir(d):
                stems.update(p.name[:-len(suffix)] for p in d.iterdir() if p.name.endswith(suffix))
        agents = state_dir / "agents"
        if _real_dir(agents):
            stems.update(p.name for p in agents.iterdir() if _real_dir(p))
        ranked = []
        for stem in sorted(stems):
            if not stem or state.unsafe_goal_reason(stem) or work.stem(stem) != stem:
                keep("unsafe-name")
                continue
            if (state_dir / "work" / f"{stem}.json").exists():
                keep("work-record")                   # cheap stat first: no log read for these
                continue
            if not _agents_dir_dead(sdlc_dir, stem, config, agent_alive):
                keep("live-agent")
                continue
            if (state_dir / "log").is_symlink():
                keep("not-terminal")
                continue
            stamp = _terminal_stamp(state_dir / "log" / f"{stem}.jsonl", actionlog.INTERNAL_KINDS)
            if stamp is None:
                keep("not-terminal")
                continue
            files = _stem_files(state_dir, stem)
            try:
                young = stamp > now - min_age_seconds or any(
                    p.stat().st_mtime > now - min_age_seconds for p in files)
            except OSError:
                young = True
            if young:
                keep("grace")
                continue
            ranked.append((stamp, stem, files))
        ranked.sort()
        for stamp, stem, files in ranked[:max(0, int(limit))]:
            # re-read the newest row at the last moment: a reopen since the scan keeps the goal
            if _terminal_stamp(state_dir / "log" / f"{stem}.jsonl", actionlog.INTERNAL_KINDS) != stamp:
                keep("reopened")
                continue
            if not _agents_dir_dead(sdlc_dir, stem, config, agent_alive):
                keep("live-agent")                 # registered since the scan
                continue
            for path in files:
                if not dry_run:
                    path.unlink(missing_ok=True)
                result["removed"].append(str(path))
            marker_dir = agents / stem
            if _real_dir(marker_dir):
                if not dry_run:
                    for entry in marker_dir.iterdir():       # non-recursive: files, then the dir
                        if _regular(entry):
                            entry.unlink(missing_ok=True)
                    try:
                        marker_dir.rmdir()
                    except OSError:
                        pass                                  # re-registered meanwhile: keep it
                result["removed"].append(str(marker_dir))
        for _ in ranked[max(0, int(limit)):]:
            keep("limit")
        run_stop = state_dir / "run_stop"
        if _real_dir(run_stop):
            cutoff = now - RUN_STOP_KEEP_DAYS * 86400
            for path in sorted(run_stop.glob("*.json")):
                if _regular(path) and path.stat().st_mtime < cutoff:
                    if not dry_run:
                        path.unlink(missing_ok=True)
                    result["removed"].append(str(path))
    except Exception as exc:                     # noqa: BLE001 - a sweep never raises into a caller
        print(f"goal_state_prune: sweep stopped ({exc})", file=sys.stderr)
    return result


def after_done(sdlc_dir, agent_alive=None, config=None):
    """The `record done` hook: a bounded sweep. The goal that just finished is itself inside the
    window, so its own evidence stays readable; it is pruned by a later sweep."""
    return sweep(sdlc_dir, agent_alive=agent_alive, config=config)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", choices=["sweep"])
    parser.add_argument("sdlc_dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--min-age-days", type=float, default=MIN_AGE_SECONDS / 86400,
                        help="keep anything younger than this (default 7)")
    args = parser.parse_args(argv)
    print(json.dumps(sweep(args.sdlc_dir, limit=args.limit, dry_run=args.dry_run,
                           min_age_seconds=max(0.0, args.min_age_days) * 86400), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Reclaim the checkout of a goal worktree whose work provably landed (#465, B6).

    python3 skills/sigma-loop/scripts/worktree_prune.py sweep .sdlc [--dry-run] [--limit N]
                                                         [--max-examine N] [--max-pr-reads N]
    python3 skills/sigma-loop/scripts/worktree_prune.py list-removable .sdlc

Owned (repo-local, written by `work.start`): `<work.worktree_dir>/<goal>` (default `.sdlc/work/<goal>`),
described by `state/work/<goal>.json`. Nothing under a host configuration root is touched.

Removes ONLY the checkout directory, with a plain `git worktree remove` (never `--force`) and
`git worktree prune`. It never deletes a branch, a remote ref, the work record, a ledger entry or a
log row, and never calls `work.finish`: the record is the only place a goal's PR number lives, and a
goal that survives with a merged PR is one whose `record done` never ran. A tree is removed only when
EVERY proof holds, and any unreadable or unprovable fact keeps it and names the reason for a human:

  1. a safe goal stem, not in `exclude`;
  2. the recorded path is exactly `<project root>/<worktree_dir>/<stem>`, a real directory, not a symlink;
  3. the sweeping process is not inside it;
  4. the record has no `awaiting_merge` flag (the merge-reconcile pass owns that goal);
  5. no alive or unknown agent marker and the goal in no live session's `in_flight` (re-run just before
     removal);
  6. `git rev-parse --show-toplevel` is the path (a tree with no `.git` file resolves to the MAIN repo)
     and the checked-out branch is the recorded branch (a detached HEAD or `wip/<n>-paused` is WIP);
  7. no uncommitted or untracked file, no assume-unchanged / skip-worktree entry, and every ignored
     file is regenerable (caches, `.pyc`, `.DS_Store`, `.sdlc/state/time/`); anything else ignored
     (`.env.local`, a database, notes) is user data;
  8. the record has a numeric `pr`;
  9. one REST read (`gh api repos/{owner}/{repo}/pulls/<n>`, never GraphQL): positively merged, head
     ref equal to the recorded branch, head repo equal to the base repo;
 10. `git merge-base --is-ancestor HEAD <PR head sha>`: every commit in the tree was in the merged PR.

One sweep at a time (a non-blocking `flock` on `state/worktree-prune.lock`, never deleted). A removal
writes a journal first (`state/worktree-prune/<goal>.json`); a sweep that dies, or a removal that fails
midway, leaves it, and the next sweep heals: `git worktree prune`; at most ONE restore of tracked files
the interrupted removal deleted (then `heal-gave-up`, left for a human); unregistered debris removed only
when every remaining entry is regenerable or byte-identical to the journalled HEAD's blob. Candidates are
examined least-recently-examined first so tolerably permanent survivors rotate instead of starving a
newer removable tree. Without `fcntl` the sweep REFUSES loudly (the lever exits 2) and removes nothing.
Never raises into a caller. The automatic triggers (`loop.py record ... done`, `loop.py start`) run only
when `work.reclaim_merged_worktrees` is `true` (default `false`: the REST reads are quota); the lever
always works.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:                                  # Windows: no flock, so nothing is provable
    fcntl = None

_HERE = pathlib.Path(__file__).resolve().parent
HOOK_LIMIT = 3
HOOK_MAX_EXAMINE = 20
HOOK_MAX_PR_READS = 8
HOOK_BUDGET_SECONDS = 20.0
LEVER_LIMIT = 50
LEVER_MAX_PR_READS = 50
AUTO_MIN_INTERVAL_SECONDS = 900
REMOVE_TIMEOUT_CAP = 60
IGNORED_LISTING_CAP = 5000
_REGENERABLE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_REGENERABLE_PREFIXES = (".sdlc/state/time/",)
_REGENERABLE_NAMES = {".DS_Store"}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_timeout():
    try:
        return max(5, int(_load("legacy").getenv("SIGMA_WATCH_CALL_TIMEOUT") or "120"))
    except ValueError:
        return 120


def default_run(cwd, argv, timeout=None, stdin=None):
    """-> (returncode, stdout). A timeout kills the child and reads as (124, '')."""
    try:
        proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), capture_output=True,
                              input=stdin, timeout=timeout,
                              env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))   # checks never write an index
    except subprocess.TimeoutExpired:
        return 124, b""
    except OSError as exc:                           # no gh, no git, cwd vanished
        return 127, str(exc).encode()
    return proc.returncode, proc.stdout


class _Ctx:
    """One sweep's shared state: modules, clock, runner and the knobs."""

    def __init__(self, sdlc_dir, config, run, started, budget_seconds, exclude):
        self.sdlc = pathlib.Path(sdlc_dir)
        self.config = config
        self.run = run or default_run
        self.started = started
        self.budget = budget_seconds
        self.loop, self.state, self.work = _load("loop"), _load("state"), _load("work")
        self.exclude = {self.work.stem(str(e)).lstrip("#") for e in exclude}
        self.live = _load("liveness_prune")
        self.root = self.work.project_root(self.sdlc)
        self.work_root = (self.root / self.work.settings(config)["worktree_dir"]).resolve()
        self.state_dir = self.sdlc / "state"
        self.journal_dir = self.state_dir / "worktree-prune"
        self.in_flight = None

    def remaining(self):
        if self.budget is None:
            return None
        return self.budget - (time.monotonic() - self.started)

    def over_budget(self):
        left = self.remaining()
        return left is not None and left <= 0

    def call(self, cwd, argv, cap=None, stdin=None):
        """Run through the injected runner, bounded by the call timeout and the budget left."""
        timeout = _call_timeout() if cap is None else cap
        left = self.remaining()
        if cap is None and left is not None:
            timeout = min(timeout, max(5, left))
        rc, out = self.run(cwd, argv, timeout=timeout, stdin=stdin)
        return rc, (out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or ""))

    def record(self, stem):
        path = self.state_dir / "work" / f"{stem}.json"
        try:
            raw = path.read_bytes()
            rec = json.loads(raw)
        except (OSError, ValueError):
            return None, None
        return (rec if isinstance(rec, dict) else None), raw


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _owned_path(ctx, stem, rec):
    """The recorded worktree as a resolved Path iff it is exactly `<work root>/<stem>`, else a reason."""
    raw = rec.get("worktree")
    if not isinstance(raw, str) or not raw:
        return None, "outside-work-root"
    path = pathlib.Path(raw)
    try:
        if path.is_symlink() or path.resolve() != ctx.work_root / stem:
            return None, "outside-work-root"
        if not path.exists():
            return None, "worktree-missing"
        if not path.is_dir():
            return None, "outside-work-root"
    except OSError:
        return None, "outside-work-root"
    return path.resolve(), None


def _toplevel_ok(ctx, path):
    rc, out = ctx.call(path, ["git", "rev-parse", "--show-toplevel"])
    try:
        return rc == 0 and pathlib.Path(out.strip()).resolve() == path.resolve()
    except OSError:
        return False


def _head(ctx, path):
    rc, out = ctx.call(path, ["git", "rev-parse", "HEAD"])
    return out.strip() if rc == 0 else None


def _regenerable(rel):
    parts = rel.split("/")
    return (any(p in _REGENERABLE_DIRS for p in parts) or rel.endswith(".pyc")
            or parts[-1] in _REGENERABLE_NAMES or rel.startswith(_REGENERABLE_PREFIXES))


def _liveness_reason(ctx, stem):
    """None when nothing alive owns the goal, else 'live-agent' | 'live-session'."""
    agents = ctx.state_dir / "agents"
    if not ctx.live._agents_dead(ctx.loop, agents, ctx.sdlc, stem, ctx.config):
        return "live-agent"
    if ctx.in_flight is None:
        ctx.in_flight = ctx.live._live_in_flight(ctx.loop, ctx.sdlc, ctx.config)
    return "live-session" if stem in ctx.in_flight else None


def _local_reason(ctx, stem, rec, raw):
    """Checks 1-7 -> (reason|None, path, head, branch)."""
    if not stem or ctx.state.unsafe_goal_reason(stem) or ctx.work.stem(stem) != stem:
        return "not-owned", None, None, None
    if stem in ctx.exclude:
        return "excluded", None, None, None
    path, why = _owned_path(ctx, stem, rec)
    if why:
        return why, None, None, None
    try:
        here = pathlib.Path(os.getcwd()).resolve()
        if here == path or path in here.parents:
            return "current", path, None, None
    except OSError:
        pass
    if isinstance(rec.get("awaiting_merge"), dict):
        return "awaiting-merge", path, None, None
    why = _liveness_reason(ctx, stem)
    if why:
        return why, path, None, None
    if not _toplevel_ok(ctx, path):
        return "not-a-worktree", path, None, None
    branch = rec.get("branch")
    rc, out = ctx.call(path, ["git", "symbolic-ref", "--short", "-q", "HEAD"])
    if rc != 0 or not branch or out.strip() != branch:
        return "branch-mismatch", path, None, None
    rc, out = ctx.call(path, ["git", "status", "--porcelain", "--untracked-files=all"])
    if rc != 0:
        return "unreadable", path, None, None
    if out.strip():
        return "dirty", path, None, None
    rc, out = ctx.call(path, ["git", "ls-files", "-v"])
    if rc != 0:
        return "unreadable", path, None, None
    if any(line[:1].islower() or line[:1] == "S" for line in out.splitlines()):
        return "hidden-changes", path, None, None
    rc, out = ctx.call(path, ["git", "ls-files", "--others", "--ignored", "--exclude-standard"])
    if rc != 0:
        return "unreadable", path, None, None
    ignored = [line for line in out.splitlines() if line]
    if len(ignored) > IGNORED_LISTING_CAP or any(not _regenerable(line) for line in ignored):
        return "ignored-content", path, None, None
    head = _head(ctx, path)
    if not head:
        return "unreadable", path, None, None
    return None, path, head, branch


def _pr_reason(ctx, rec, path, head):
    """Checks 8-10 -> reason|None (one REST read already counted by the caller)."""
    pr = str(rec.get("pr") or "")
    if not pr.isdigit():
        return "no-pr"
    rc, out = ctx.call(ctx.root, ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr}"])
    try:
        data = json.loads(out) if rc == 0 else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return "pr-unknown"
    if not (data.get("merged") is True or data.get("merged_at")):
        state_now = str(data.get("state") or "").lower()
        return "pr-open" if state_now == "open" else \
            "pr-closed-unmerged" if state_now == "closed" else "pr-unknown"
    h, b = data.get("head"), data.get("base")
    if not isinstance(h, dict) or not isinstance(b, dict):
        return "pr-mismatch"
    hrepo, brepo = h.get("repo"), b.get("repo")
    if (h.get("ref") != rec.get("branch") or not isinstance(hrepo, dict) or not isinstance(brepo, dict)
            or not hrepo.get("full_name") or hrepo.get("full_name") != brepo.get("full_name")):
        return "pr-mismatch"
    sha = str(h.get("sha") or "")
    if len(sha) not in (40, 64) or any(c not in "0123456789abcdef" for c in sha):
        return "pr-mismatch"
    rc, _ = ctx.call(path, ["git", "merge-base", "--is-ancestor", "HEAD", sha])
    return None if rc == 0 else "unpushed" if rc == 1 else "unprovable"


def _journal_path(ctx, stem):
    return ctx.journal_dir / f"{stem}.json"


def _read_journal(ctx, stem):
    try:
        data = json.loads(_journal_path(ctx, stem).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _drop_journal(ctx, stem):
    _journal_path(ctx, stem).unlink(missing_ok=True)


def _write_journal(ctx, stem, path, head, branch, record_sha, heals=0):
    try:
        _atomic_write(_journal_path(ctx, stem), json.dumps(
            {"worktree": str(path), "head": head, "branch": branch,
             "record_sha256": record_sha, "heals": heals}, sort_keys=True))
    except OSError:
        return False
    return True


def _intact(ctx, path, head):
    if not path.is_dir() or not _toplevel_ok(ctx, path) or _head(ctx, path) != head:
        return False
    rc, out = ctx.call(path, ["git", "status", "--porcelain", "--untracked-files=all"])
    return rc == 0 and not out.strip()


def _remove(ctx, stem, rec, raw, path, head, branch):
    """-> ('removed' | reason). Rechecks, journals, then a plain non-force `git worktree remove`."""
    ctx.in_flight = None                             # a session may have registered the goal since the first look
    why = _liveness_reason(ctx, stem)
    if why:
        return why
    now_rec, now_raw = ctx.record(stem)
    if now_raw is None or _sha(now_raw) != _sha(raw) or _head(ctx, path) != head:
        return "moved"
    prior = _read_journal(ctx, stem) or {}
    heals = prior.get("heals", 0) if prior.get("head") == head and isinstance(prior.get("heals"), int) else 0
    if not _write_journal(ctx, stem, path, head, branch, _sha(raw), heals):
        return "journal-failed"
    cap = min(_call_timeout(), REMOVE_TIMEOUT_CAP)
    rc, _ = ctx.call(ctx.root, ["git", "worktree", "remove", str(path)], cap=cap)
    ctx.call(ctx.root, ["git", "worktree", "prune"])
    if rc == 0 and not path.exists():
        _drop_journal(ctx, stem)
        return "removed"
    if _intact(ctx, path, head):
        _drop_journal(ctx, stem)                     # git refused before deleting: nothing to heal
    return "remove-failed"


def _registered(ctx, path):
    rc, out = ctx.call(ctx.root, ["git", "worktree", "list", "--porcelain"])
    if rc != 0:
        return None
    here = path.resolve()
    for line in out.splitlines():
        if line.startswith("worktree "):
            try:
                if pathlib.Path(line[len("worktree "):]).resolve() == here:
                    return True
            except OSError:
                pass
    return False


def _debris_verified(ctx, path, head):
    """True only when every entry left in a half-deleted tree is regenerable or byte-identical to the
    blob at that path in the journalled HEAD (read in the MAIN repo; never `check-ignore`)."""
    rc, out = ctx.call(ctx.root, ["git", "ls-tree", "-r", "-z", head])
    if rc != 0:
        return False
    tracked = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, name = entry.partition("\t")
        parts = meta.split()
        if len(parts) == 3:
            tracked[name] = (parts[0], parts[2])
    regular = []
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in list(dirnames) + filenames:
            full = pathlib.Path(dirpath) / name
            rel = full.relative_to(path).as_posix()
            try:
                mode = os.lstat(full).st_mode
            except OSError:
                return False
            if stat.S_ISDIR(mode):
                continue
            if _regenerable(rel):
                continue
            if rel not in tracked:
                return False
            if stat.S_ISLNK(mode):
                try:
                    target = os.readlink(full).encode()
                except OSError:
                    return False
                rc, blob = ctx.call(ctx.root, ["git", "hash-object", "--no-filters", "--stdin"],
                                    stdin=target)
                if rc != 0 or blob.strip() != tracked[rel][1]:
                    return False
            elif stat.S_ISREG(mode):
                regular.append((rel, full))
            else:
                return False
    if regular:
        listing = "\n".join(str(full) for _rel, full in regular).encode() + b"\n"
        rc, out = ctx.call(ctx.root, ["git", "hash-object", "--no-filters", "--stdin-paths"],
                           stdin=listing)
        hashes = out.split()
        if rc != 0 or len(hashes) != len(regular):
            return False
        if any(h != tracked[rel][1] for (rel, _full), h in zip(regular, hashes)):
            return False
    return True


def _heal(ctx, result):
    """Finish what a dead sweep or a failed removal left. Under the sweep lock: no live owner."""
    try:
        journals = sorted(ctx.journal_dir.glob("*.json"))
    except OSError:
        return
    pruned = False
    for jpath in journals:
        stem = jpath.stem
        journal = _read_journal(ctx, stem)
        rec, raw = ctx.record(stem)
        if (journal is None or rec is None or raw is None or journal.get("branch") != rec.get("branch")
                or journal.get("worktree") != rec.get("worktree")
                or journal.get("record_sha256") != _sha(raw)):
            _drop_journal(ctx, stem)                 # a hint, never an authority
            continue
        if not pruned:
            ctx.call(ctx.root, ["git", "worktree", "prune"])
            pruned = True
        path = pathlib.Path(journal["worktree"])
        if path.is_symlink() or path.resolve() != ctx.work_root / stem:
            _drop_journal(ctx, stem)
            continue
        if not path.exists():
            _drop_journal(ctx, stem)
            continue
        head = journal.get("head")
        registered = _registered(ctx, path)
        if registered is None:
            continue
        if registered:
            if not _toplevel_ok(ctx, path) or _head(ctx, path) != head:
                _drop_journal(ctx, stem)
                continue
            rc, out = ctx.call(path, ["git", "status", "--porcelain", "--untracked-files=all"])
            lines = [ln for ln in out.splitlines() if ln]
            if rc != 0 or not lines:
                _drop_journal(ctx, stem)
            elif all(ln.startswith(" D") for ln in lines):
                heals = journal.get("heals", 0) if isinstance(journal.get("heals"), int) else 0
                if heals >= 1:
                    result["kept"].append({"goal": stem, "worktree": str(path), "reason": "heal-gave-up"})
                    continue
                if _write_journal(ctx, stem, path, head, journal.get("branch"),
                                  journal.get("record_sha256"), heals + 1):
                    ctx.call(path, ["git", "checkout", "--", "."])
                    result["healed"].append(stem)
            else:
                _drop_journal(ctx, stem)             # real changes: the normal checks call it dirty
            continue
        # present but unregistered: debris of our own interrupted removal
        if not isinstance(head, str) or not _debris_verified(ctx, path, head):
            result["kept"].append({"goal": stem, "worktree": str(path), "reason": "debris-unverified"})
            continue
        rec2, raw2 = ctx.record(stem)
        if (raw2 is None or _sha(raw2) != journal.get("record_sha256")
                or _registered(ctx, path) is not False or path.is_symlink()):
            continue                                 # something changed since the proof: leave it
        shutil.rmtree(path)
        ctx.call(ctx.root, ["git", "worktree", "prune"])
        _drop_journal(ctx, stem)
        result["healed"].append(stem)


def _tree_bytes(path):
    total = 0
    for dirpath, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def sweep(sdlc_dir, config=None, limit=HOOK_LIMIT, max_examine=HOOK_MAX_EXAMINE,
          max_pr_reads=HOOK_MAX_PR_READS, budget_seconds=None, dry_run=False, run=None, exclude=(),
          quiet_unsupported=False, respect_interval=False, with_bytes=False):
    """-> {"removed": [goal], "kept": [{goal, worktree, reason}], "deferred": {reason: n}, "busy": bool}.

    `limit` caps removals and `max_examine` examinations (0 or less = no cap; the CLI only: hooks pass
    explicit values); `budget_seconds` stops both. Never raises."""
    result = {"removed": [], "healed": [], "kept": [], "deferred": {}, "busy": False}
    if dry_run:
        result["removable"] = []

    def defer(reason, n=1):
        result["deferred"][reason] = result["deferred"].get(reason, 0) + n

    lock_fd = None
    try:
        started = time.monotonic()
        sdlc = pathlib.Path(sdlc_dir)
        state_dir = sdlc / "state"
        records_dir = state_dir / "work"
        if state_dir.is_symlink() or not state_dir.is_dir():
            return result
        has_records = records_dir.is_dir() and any(records_dir.glob("*.json"))
        has_journals = (state_dir / "worktree-prune").is_dir()
        if not has_records and not has_journals:
            return result
        if fcntl is None:
            result["unsupported"] = True
            if not quiet_unsupported:
                print("worktree_prune: not supported on this platform (no flock); nothing removed",
                      file=sys.stderr)
            return result
        seen_path = state_dir / "worktree-prune-seen.json"
        if respect_interval:
            try:
                if time.time() - seen_path.stat().st_mtime < AUTO_MIN_INTERVAL_SECONDS:
                    result["deferred"]["interval"] = 1
                    return result
            except OSError:
                pass
        lock_path = state_dir / "worktree-prune.lock"
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            result["busy"] = True
            return result
        state_mod = _load("state")
        if config is None:
            config = state_mod.load_config(sdlc)
        ctx = _Ctx(sdlc, config, run, started, budget_seconds, exclude)
        if not dry_run:
            _heal(ctx, result)
        try:
            seen = json.loads(seen_path.read_text())
            seen = seen if isinstance(seen, dict) else {}
        except (OSError, ValueError):
            seen = {}
        stems = sorted(p.stem for p in records_dir.glob("*.json")) if records_dir.is_dir() else []
        seen = {k: v for k, v in seen.items() if k in stems and isinstance(v, (int, float))}
        stems.sort(key=lambda s: (seen.get(s, 0), s))
        examined = removed = reads = 0
        for stem in stems:
            if ctx.over_budget():
                defer("budget", len(stems) - examined)
                break
            if max_examine > 0 and examined >= max_examine:
                defer("examine-cap", len(stems) - examined)
                break
            if limit > 0 and removed >= limit:
                defer("limit", len(stems) - examined)
                break
            examined += 1
            seen[stem] = time.time()
            rec, raw = ctx.record(stem)
            if rec is None:
                result["kept"].append({"goal": stem, "worktree": None, "reason": "unreadable"})
                continue
            reason, path, head, branch = _local_reason(ctx, stem, rec, raw)
            if reason is None:
                if max_pr_reads > 0 and reads >= max_pr_reads:
                    reason = "deferred"
                else:
                    reads += 1 if str(rec.get("pr") or "").isdigit() else 0
                    reason = _pr_reason(ctx, rec, path, head)
            if reason == "worktree-missing":
                defer("worktree-missing")
                examined -= 1                        # a stat, not an examination: never spends the cap
                continue
            if reason is None:
                if dry_run:
                    entry = {"goal": stem, "worktree": str(path)}
                    if with_bytes:
                        entry["bytes"] = _tree_bytes(path)
                    result["removable"].append(entry)
                    continue
                outcome = _remove(ctx, stem, rec, raw, path, head, branch)
                if outcome == "removed":
                    removed += 1
                    result["removed"].append(stem)
                    continue
                reason = outcome
            result["kept"].append({"goal": stem, "worktree": str(path) if path else rec.get("worktree"),
                                   "reason": reason})
        if not dry_run:
            try:
                _atomic_write(seen_path, json.dumps(seen, sort_keys=True))
            except OSError:
                pass
    except Exception as exc:                         # noqa: BLE001 - a sweep never raises into a caller
        print(f"worktree_prune: sweep stopped ({exc})", file=sys.stderr)
        result["error"] = str(exc)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
    return result


def after_start_or_done(sdlc_dir, config=None, exclude=()):
    """The in-line trigger (`loop.py start`, `record ... done`): opt-in, bounded, fail-open."""
    try:
        if fcntl is None:
            return None
        if config is None:
            config = _load("state").load_config(pathlib.Path(sdlc_dir))
        work = (config.get("work") or {})
        if work.get("enabled") is not True or work.get("reclaim_merged_worktrees") is not True:
            return None
        return sweep(sdlc_dir, config=config, limit=HOOK_LIMIT, max_examine=HOOK_MAX_EXAMINE,
                     max_pr_reads=HOOK_MAX_PR_READS, budget_seconds=HOOK_BUDGET_SECONDS,
                     exclude=exclude, quiet_unsupported=True, respect_interval=True)
    except Exception as exc:                         # noqa: BLE001 - housekeeping never blocks a caller
        print(f"worktree_prune: trigger skipped ({exc})", file=sys.stderr)
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", choices=["sweep", "list-removable"])
    parser.add_argument("sdlc_dir")
    parser.add_argument("--dry-run", action="store_true", help="list, remove nothing (sweep only)")
    parser.add_argument("--limit", type=int, default=LEVER_LIMIT,
                        help="removals per run (default %d; 0 = no cap)" % LEVER_LIMIT)
    parser.add_argument("--max-examine", type=int, default=0,
                        help="trees examined per run (default 0 = all)")
    parser.add_argument("--max-pr-reads", type=int, default=LEVER_MAX_PR_READS,
                        help="REST reads per run (default %d; 0 = no cap)" % LEVER_MAX_PR_READS)
    args = parser.parse_args(argv)
    listing = args.verb == "list-removable"
    out = sweep(args.sdlc_dir, limit=args.limit, max_examine=args.max_examine,
                max_pr_reads=args.max_pr_reads, dry_run=args.dry_run or listing, with_bytes=listing)
    if listing:
        out["reclaimable_bytes"] = sum(e.get("bytes", 0) for e in out.get("removable", []))
    print(json.dumps(out, indent=2))
    if out.get("unsupported"):
        return 2
    if out.get("error"):
        return 1                                     # an empty result must not read as "nothing to do"
    if out.get("busy"):
        print("worktree_prune: another sweep holds the lock; nothing was examined", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Share the ledger over a dedicated ops branch, without ever touching your working tree.

THE PROBLEM. A shared ledger has to be pulled often — that is the whole point. But pulling the
integration branch mid-task is exactly how people lose work to a surprise rebase, and committing the
ledger alongside code means every append goes through review on a protected branch.

THE SHAPE. `.sdlc/ledger/` is itself a **git worktree** checked out to a dedicated ops branch
(default `sdlc-ledger`), and the main branch gitignores that path. Consequences, all of them the
point:

  * fetching and rebasing the ledger touches ONLY that worktree — your code checkout never moves;
  * the ops branch is never merged into the integration branch, so it needs no review and can stay
    unprotected while the code branch stays locked down;
  * `ledger.py` needs no change at all — it already writes to `.sdlc/ledger/entries/`, which is now
    the worktree;
  * the branch starts from the EMPTY TREE, so it carries the ledger and nothing else.

Because each WRITING PROCESS only ever appends to its own `entries/<actor>-<host>.<pid>.jsonl`
(ledger.py's F10 fix plus #540's host token — two loops can share one login, and two machines can
share one bot account), a rebase onto someone else's push cannot conflict — different files, every
time. The corollary is that the branch accumulates MANY files per actor and never fewer; that is the
designed shape, not drift. Push is fast-forward only with a bounded fetch-rebase-retry, so a race
is resolved by replaying, never by forcing. Zero deps.
"""
import collections
import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

try:                    # #2698: the knowledge channel's non-blocking lock is `fcntl.flock`, POSIX-only;
    import fcntl        # without it (Windows) the lock fails OPEN, exactly as `loop._try_acquire_claim_lock`
except ImportError:     # documents for the same mechanism
    fcntl = None

try:                    # portable output: force UTF-8 so the plugin's own non-ASCII (arrows, em-dashes)
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")   # doesn't garble to '?' or
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")   # crash on a non-UTF-8 console
except Exception:       # (the Windows cp1252 default); a stream without reconfigure is left as-is
    pass

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _load("ledger")

DEFAULT_BRANCH = "sdlc-ledger"
DEFAULT_REMOTE = "origin"
PUSH_ATTEMPTS = 5

#: #2574/S1-G3: ENTRIES ONLY. `{EVENTS}/*.jsonl merge=union` was dropped when the journal stopped
#: being published — nothing writes `ledger/events/` any more, so a union-merge attribute for it
#: would describe a path this branch never gains another line in. Existing clones keep the stale
#: line in their own `.gitattributes`; that is harmless (it matches nothing new) and deliberately
#: NOT cleaned up — see `BRANCH_README` and the CHANGELOG. Removing a line here is not retroactive:
#: `_ensure_gitattributes` below is additive by design and never deletes.
GITATTRIBUTES_LINES = (f"{ledger.ENTRIES}/*.jsonl merge=union",)
BRANCH_README = """\
# SDLC ledger — ops branch

Machine-written. **Never merged into the integration branch** and never runs CI: this is a
coordination ledger, not code.

`entries/<actor>-<host>.<pid>.jsonl` is append-only and single-writer: one file per writing process
— your handle, a hash of the machine, the pid — so two loops sharing one login never collide, and
neither do two machines sharing one bot account.

**Many files per actor is normal, and there is no upper bound.** Every run of the loop adds a file
and nothing here compacts them, so a single actor legitimately owns hundreds of these. Nothing is
read back by filename: the team view is the union of every `*.jsonl` in the directory, with each
line attributed by the `actor` field inside it. The surprising state is the opposite one: exactly
ONE file for a given actor means that actor's writes stopped.

`TEAM.md` is generated; regenerate it rather than resolving it by hand.

`events/` is FROZEN. The event journal is local-only from Sigma 1.4 (#2574): it writes
`.sdlc/events/`, which is gitignored and never staged here. Whatever is already under `events/` on
this branch stays — it is still read for one release and deleting it would take a teammate's
history with it — but nothing adds to it. A clone that predates this still carries an
`events/*.jsonl merge=union` line in `.gitattributes`; it matches nothing new and is left alone.

Checked out as a worktree at `.sdlc/ledger/` in each person's clone, so pulling it never disturbs
their code checkout.
"""


def branch(config):
    return ledger.settings(config).get("branch") or DEFAULT_BRANCH


def remote(config):
    return ledger.settings(config).get("remote") or DEFAULT_REMOTE


def project_root(sdlc_dir):
    return pathlib.Path(sdlc_dir).resolve().parent


def worktree(sdlc_dir):
    """ABSOLUTE on purpose. Every git call here runs with `-C <some other directory>`, so a relative
    path would be resolved against THAT directory, not the caller's cwd — `git worktree add` would
    silently create the worktree in the wrong place and the next write would land nowhere. Callers
    routinely pass a relative `.sdlc`."""
    return ledger.ledger_dir(sdlc_dir).resolve()


# --------------------------------------------------------------- the second channel: knowledge notes (#2698)
#
# `.sdlc/knowledge/analysis/` — the notes `agrim-retro` §4 writes through `kg.py note` — has the
# ledger's exact problem: written AFTER the goal's PR merges (measured: `merged` → 11-17 min → `retro`
# + `done`, note mtime at the retro), so there is no PR for it to ride and no pre-push guard can
# demand a file that does not exist yet. Same answer: a linked worktree on its own ops branch,
# gitignored on every code branch, never merged. ONLY `analysis/` — `research/web/` holds
# best-effort-scrubbed captures that are gitignored for that reason, and `gaps.md` is one append-only
# file two machines would conflict on. Notes are goal-numbered and one goal has one worker, so two
# machines never write the same file; `*.md merge=union` is the backstop that turns a violation into
# a concatenated note (`kg.py maintain` flags duplicates) instead of a wedged rebase.
#
# A SEPARATE branch, not the ledger's: a note conflict must never be able to wedge the ledger, and
# the knowledge graph is its own opt-in. Every verb below takes `channel=LEDGER`; the ledger
# channel's PUBLIC BEHAVIOUR is unchanged (its one-arg `is_worktree`, `-X theirs` retry, TEAM.md
# regeneration, "next tick" deferral wording — `tests/test_ledger_publish.py`, `tests/test_watch.py`),
# though `init` gained `worktree prune` + rename-back and `publish`'s retry loop is now shared.

LEDGER, KNOWLEDGE = "ledger", "knowledge"
DEFAULT_KNOWLEDGE_BRANCH = "sdlc-knowledge"
KNOWLEDGE_GITATTRIBUTES = ("*.md merge=union",)
#: ONLY `*.md` LEAVES THE MACHINE, and git enforces it rather than prose: everything is ignored,
#: then directories (so a nested note is still reachable), notes, and the two scaffold files are
#: re-included; a hand-run builder's output (`graphify extract <corpus>` without `--out` writes
#: `<corpus>/graphify-out/` — this repo carries an empty one) is re-ignored last so `!*/` cannot
#: rescue it. A stray `creds.json` beside the notes therefore never reaches `git add -A`, whatever
#: it holds (review round 1, B2). Last matching line wins, so the ORDER is load-bearing.
#: CHANGING THIS TUPLE IS A FLEET-WIDE MIGRATION: `_write_knowledge_gitignore` rewrites the file on
#: every machine's next publish, so a change ships as one identical commit from each machine (git
#: replays identical changes cleanly) and every clone converges — but only after each has published.
KNOWLEDGE_GITIGNORE = ("*", "!*/", "!*.md", "!.gitattributes", "!.gitignore", "graphify-out/", "*-out/")
#: The outcomes `loop._sync_knowledge_notes` stays silent on; anything else reaches stderr.
KNOWLEDGE_QUIET_OUTCOMES = frozenset({"published", "nothing to publish", "pulled"})
_Channel = collections.namedtuple("_Channel", "name path branch remote")


def knowledge_settings(config):
    """`knowledge_graph.sync`, or `{}`; isinstance-guarded on both levels like `ledger.journal_settings`."""
    cfg = config if isinstance(config, dict) else {}
    kg = cfg.get("knowledge_graph")
    block = kg.get("sync") if isinstance(kg, dict) else None
    return block if isinstance(block, dict) else {}


def knowledge_enabled(config):
    """Strict `is True` on BOTH `knowledge_graph.enabled` and `knowledge_graph.sync.enabled` — this
    pushes to a remote, so a quoted "true" or a stray 1 must not switch it on (SAFETY)."""
    cfg = config if isinstance(config, dict) else {}
    kg = cfg.get("knowledge_graph")
    return isinstance(kg, dict) and kg.get("enabled") is True \
        and knowledge_settings(config).get("enabled") is True


def knowledge_branch(config):
    return knowledge_settings(config).get("branch") or DEFAULT_KNOWLEDGE_BRANCH


def knowledge_remote(config):
    return knowledge_settings(config).get("remote") or DEFAULT_REMOTE


def knowledge_worktree(sdlc_dir):
    """`.sdlc/knowledge/analysis`, absolute for the reason `worktree()` gives. The corpus directory
    `kg.py` writes notes into IS the worktree, so `kg.py` needs no change."""
    return (pathlib.Path(sdlc_dir) / "knowledge" / "analysis").resolve()


def knowledge_actor(config):
    """The commit subject's author: the CONFIGURED `ledger.actor` only, else the shell user. Never
    `ledger.actor()` unconfigured — that runs `gh api user`, a network call on a record path that
    must stay bounded and offline-safe."""
    configured = (ledger.settings(config).get("actor") or "").strip()
    if configured:
        return ledger._safe_name(configured)
    return ledger._safe_name(os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown")


def knowledge_lock_path(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / "knowledge-sync.lock"


def _knowledge_lock(sdlc_dir):
    """A NON-BLOCKING, kernel-mediated `flock` so two `record`s on one machine (two loop slots ending
    goals in the same minute) never both drive the worktree's index. `loop._try_acquire_claim_lock`
    documents why flock and not a create/rename scheme: no read-then-act gap, released the instant
    the holder dies. Returns the open file (keep it referenced; closing releases), `None` when a
    sibling holds it, or `True` when the mechanism is unavailable (no `fcntl`) — fail-open."""
    if fcntl is None:
        return True
    path = knowledge_lock_path(sdlc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(path, "a+")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


def _channel(name, sdlc_dir, config):
    if name == KNOWLEDGE:
        return _Channel(KNOWLEDGE, knowledge_worktree(sdlc_dir), knowledge_branch(config),
                        knowledge_remote(config))
    if name == LEDGER:
        return _Channel(LEDGER, worktree(sdlc_dir), branch(config), remote(config))
    raise ValueError(f"unknown sync channel {name!r} (expected {LEDGER!r} or {KNOWLEDGE!r})")


def _no_hooks_dir(cwd):
    """A path that is guaranteed not to exist, so `core.hooksPath` set to it makes git find no
    hook file and silently proceed — ordinary "file not found" semantics, not a platform-specific
    device file (`/dev/null` becomes the Windows-reserved `nul` via `os.devnull`, unverified on
    that platform; this avoids the question entirely, #2449 plan-review round 1)."""
    return str(pathlib.Path(str(cwd)) / ".agrim-ledger-hooks-disabled")


def _run_git(cwd, args):
    # GIT_EDITOR=true for every call: nothing here ever wants an editor, and the knowledge channel's
    # `rebase --continue` after a resolved conflict DOES open one (merge backend) — an exported
    # GIT_EDITOR outranks `-c core.editor`, so an operator's `GIT_EDITOR=vim` would hang a captured
    # subprocess on `record`'s path (round-4 review S2, measured). Harmless everywhere else.
    proc = subprocess.run(
        ["git", "-C", str(cwd), "-c", f"core.hooksPath={_no_hooks_dir(cwd)}", *args],
        capture_output=True, text=True, env=dict(os.environ, GIT_EDITOR="true"))
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def is_worktree(sdlc_dir, channel=LEDGER):
    """True once the channel's directory is a git worktree rather than a plain directory. `.git` is
    a FILE in a linked worktree, which is the cheapest reliable signal and needs no subprocess.
    The one-argument form is the ledger's and is what nine `tests/test_watch.py` stubs replace."""
    path = knowledge_worktree(sdlc_dir) if channel == KNOWLEDGE else worktree(sdlc_dir)
    return (path / ".git").exists()


def _carry(staging, dest, on_collision=None):
    """Move everything under `staging` into `dest` that is not already there, RECURSING into any
    directory that exists on both sides. Returns the list of FILES that existed on both sides.

    This used to be a flat loop that skipped any top-level name already present on the branch, and
    that was silently destructive: the ops branch always ships an `entries/` directory, so a clone
    whose ledger had been running LOCALLY (files under `entries/`, never published) hit the collision
    on `entries` itself, skipped the whole directory, and then `_prune(staging)` deleted it -- every
    unpublished entry gone, with the command reporting success. Reproduced on a real clone: 202
    files / 263 entries destroyed in one `bootstrap`.

    The rule is now: a name that does not exist at the destination MOVES; a directory that exists on
    both sides is MERGED, one level deeper; a FILE that exists on both sides is left alone, because
    the branch's published copy must never be clobbered by a local one -- unless the caller passes
    `on_collision(local, published)`, which the knowledge channel does to UNION a note (#2698: a
    silently dropped local note is a lost lesson). Either way the collision is returned, never
    swallowed."""
    collisions = []
    for item in staging.iterdir():
        target = dest / item.name
        if not target.exists():
            item.rename(target)
        elif item.is_dir() and target.is_dir():
            collisions += _carry(item, target, on_collision)
        elif item.is_file() and target.is_file():
            if on_collision is not None:
                on_collision(item, target)
            collisions.append(target)
    return collisions


def _union_note(local, published):
    """Append to the branch's copy every line the local note has that it lacks — the same result
    `merge=union` would have produced had both been committed, done by hand because the local one
    was never in git."""
    have = published.read_text(encoding="utf-8", errors="replace").splitlines()
    extra = [line for line in local.read_text(encoding="utf-8", errors="replace").splitlines()
             if line not in have]
    if extra:
        published.write_text("\n".join(have + extra) + "\n", encoding="utf-8")


def _write_knowledge_gitignore(path):
    """SCAFFOLD-OWNED, not additive: `KNOWLEDGE_GITIGNORE`'s order is load-bearing (last match wins),
    so appending to an older shape would put `!*.md` after `graphify-out/` and re-include a graph
    build (review round 2, S1: measured `graphify-out/README.md` shipping). The file is rewritten
    whenever it differs, from `init` and from every knowledge `publish`, so a worktree born under an
    earlier scaffold migrates on its next publish. Returns True iff it wrote."""
    dest = path / ".gitignore"
    canonical = "\n".join(KNOWLEDGE_GITIGNORE) + "\n"
    if dest.exists() and dest.read_text(encoding="utf-8") == canonical:
        return False
    dest.write_text(canonical, encoding="utf-8")
    return True


def _ensure_lines(dest, lines):
    """Idempotent, additive: append whichever of `lines` is missing. Returns True iff it wrote."""
    have = dest.read_text(encoding="utf-8").splitlines() if dest.exists() else []
    missing = [line for line in lines if line not in have]
    if missing:
        dest.write_text("\n".join(have + missing) + "\n", encoding="utf-8")
    return bool(missing)


def init(sdlc_dir, config, run=None, channel=LEDGER):
    """Create the ops branch (from the empty tree, so it carries no code) and check it out as the
    channel's worktree. Idempotent: an existing worktree is left alone."""
    git = run or _run_git
    root = project_root(sdlc_dir)
    ch = _channel(channel, sdlc_dir, config)
    path, name, rem = ch.path, ch.branch, ch.remote
    # one-arg on the ledger path: nine `tests/test_watch.py` stubs replace it with `lambda _d: True`
    if is_worktree(sdlc_dir) if channel == LEDGER else is_worktree(sdlc_dir, channel=channel):
        return f"already a worktree: {path}"

    try:                                        # a colleague may have pushed the branch already
        git(root, ["fetch", rem, name])
        git(root, ["branch", "--force", name, f"{rem}/{name}"])
    except Exception:
        empty = git(root, ["hash-object", "-t", "tree", "/dev/null"])
        commit = git(root, ["commit-tree", empty, "-m", f"{channel}: start the ops branch"])
        git(root, ["branch", name, commit])     # plumbing only — the main index is never written

    try:                                        # a worktree that was `rm -rf`ed still has a registry
        git(root, ["worktree", "prune"])        # entry, and `add` refuses the path until it is pruned
    except Exception:
        pass
    collisions = []
    if path.exists() and any(path.iterdir()):   # plain files were written here first; keep them
        staging = path.parent / f"_{channel}-carry"
        path.rename(staging)
        try:
            git(root, ["worktree", "add", str(path), name])
        except Exception:
            staging.rename(path)                # never leave the operator's notes under a carry dir
            raise
        collisions = _carry(staging, path, _union_note if channel == KNOWLEDGE else None)
        _prune(staging)
    else:
        git(root, ["worktree", "add", str(path), name])

    if channel == KNOWLEDGE:
        _ensure_lines(path / ".gitattributes", KNOWLEDGE_GITATTRIBUTES)
        _write_knowledge_gitignore(path)                            # no README: it would be graphed
        # SCAFFOLD ONLY. The carried-in notes stay untracked here so that `bootstrap`'s `publish`
        # runs them through the secret gate like any later note — an `add -A` at this point shipped
        # a pre-existing secret-shaped note with no gate at all (review round 1, B1), on the one
        # gesture every existing user runs over their whole corpus.
        git(path, ["add", "--", ".gitattributes", ".gitignore"])
    else:
        _ensure_gitattributes(path)
        if not (path / "README.md").exists():
            (path / "README.md").write_text(BRANCH_README, encoding="utf-8")
        (path / ledger.ENTRIES).mkdir(exist_ok=True)
        git(path, ["add", "-A"])
    try:
        git(path, ["commit", "-m", f"{channel}: scaffold the ops branch"])
    except Exception:
        pass                                    # nothing to commit is a fine outcome -- and with
                                                  # hooks now disabled for this call too (#2449), a
                                                  # real hook rejection can no longer land here either
    merged = (f"; {len(collisions)} local note(s) merged with the branch copy"
              if collisions and channel == KNOWLEDGE else "")
    return f"{channel} worktree ready at {path} on {name}{merged}"


def _prune(directory):
    for child in sorted(directory.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    directory.rmdir()


def _ensure_gitattributes(path):
    """Idempotent, additive: adds whichever union-merge line is missing without disturbing
    anything else already in the file. Called from BOTH init() (fresh worktree) and publish()
    (repairs a worktree that predates events/*.jsonl merge=union — init() alone can't reach it,
    since it early-returns once a worktree already exists). Returns True iff it wrote."""
    dest = path / ".gitattributes"
    lines = dest.read_text(encoding="utf-8").splitlines() if dest.exists() else []
    changed = False
    for line in GITATTRIBUTES_LINES:
        if line not in lines:
            lines.append(line)
            changed = True
    if changed:
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def pull(sdlc_dir, config, run=None, channel=LEDGER):
    """Fetch the ops branch and replay my commits on top. Touches the channel's worktree ONLY."""
    git = run or _run_git
    if channel == KNOWLEDGE:
        return _pull_knowledge(sdlc_dir, config, git)
    path, name, rem = worktree(sdlc_dir), branch(config), remote(config)
    if not is_worktree(sdlc_dir):
        return "not a worktree — run `sync.py init` first (nothing fetched)"
    git(path, ["fetch", rem, name])
    try:
        git(path, ["rebase", f"{rem}/{name}"])
    except Exception as exc:                    # noqa: BLE001 - a watcher must never wedge
        try:
            git(path, ["rebase", "--abort"])
        except Exception:
            pass
        return f"pull deferred: {exc}"
    return "pulled"


def _pull_knowledge(sdlc_dir, config, git):
    """The knowledge channel's pull: same fetch-then-rebase, but the FETCH is inside the guard too
    (a dead remote yields a status string, never a traceback — this runs on `record`'s path), and it
    takes the non-blocking lock so it never contends with a publish on this machine. A dirty tree
    (a note publish refused) makes the rebase decline; the deferral names it, and the refusal that
    caused it already named the note."""
    ch = _channel(KNOWLEDGE, sdlc_dir, config)
    if not is_worktree(sdlc_dir, channel=KNOWLEDGE):
        return "not a worktree — run `sync.py bootstrap .sdlc --channel knowledge` first (nothing fetched)"
    lock = _knowledge_lock(sdlc_dir)
    if lock is None:
        return "pull deferred: another knowledge sync holds the lock"
    try:
        try:
            git(ch.path, ["fetch", ch.remote, ch.branch])
            try:
                git(ch.path, ["rebase", f"{ch.remote}/{ch.branch}"])
            except Exception:
                stuck = _resolve_modify_delete(git, ch.path)     # keep the edit (round-3 F1)
                if stuck is None:                                # refused, not conflicted: the
                    raise                                        # original error is the report
                if stuck:
                    git(ch.path, ["rebase", "--abort"])
                    return _CONFLICT_DEFERRAL.format(verb="pull", file=stuck)
        except Exception as exc:                # noqa: BLE001 - a record must never wedge on a pull
            try:
                git(ch.path, ["rebase", "--abort"])
            except Exception:
                pass
            return f"pull deferred: {exc}"
        return "pulled"
    finally:
        if lock is not True:
            lock.close()


def _knowledge_changed_paths(git, path):
    """Every path `git status` reports as new or modified, relative to the worktree root. Untracked
    files are listed INDIVIDUALLY (`--untracked-files=all`): the default collapses a new directory to
    one `??` line, which would hide every note inside it from the secret gate. `-z` gives NUL-separated,
    UNQUOTED names: the line format C-quotes anything non-ASCII (`"issue-\\303\\251.md"`), and a gate
    that then cannot open the quoted path would fail OPEN (review round 1, B3). A rename entry carries
    its old name as a second NUL field, skipped."""
    out = git(path, ["status", "--porcelain", "-z", "--untracked-files=all"])
    names, chunks = [], [c for c in out.split("\0") if c]
    i = 0
    while i < len(chunks):
        entry = chunks[i]
        parts = entry.split(None, 1)            # `_run_git` strips the FIRST entry's leading status
        # A DELETION (` D`, `D `) has no content to leak and no file to read: skipping it is what
        # keeps a curated-away stale note from reading as "unreadable, therefore secret" and
        # wedging every later publish on the machine (review round 2, BL-1).
        if len(parts) == 2 and "D" not in parts[0]:  # padding, so a fixed offset would mis-slice it
            names.append(parts[1])
        if entry[:1] in ("R", "C"):             # rename/copy: the next chunk is the source path
            i += 1
        i += 1
    return names


def _secret_shaped(path, relative):
    """True when the shared recogniser (`scrub.py`, the table #2718 widens) would redact anything in
    the file — OR when the reported path cannot be read at all, because a gate that shrugs at a file
    it cannot inspect is no gate. Returns the verdict only; the match itself is never surfaced."""
    target = path / relative
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return not target.is_dir()              # a directory entry is not a file to scan
    return _load("scrub").scrub(text) != text


def _ahead(git, path, rem, name):
    """Commits on HEAD the remote-tracking ref lacks, or `None` when that ref does not exist (never
    fetched or pushed) — which a publisher must read as "push", not as "nothing to do"."""
    try:
        return int(git(path, ["rev-list", "--count", f"{rem}/{name}..HEAD"]))
    except Exception:                           # noqa: BLE001 - no such ref
        return None


def _push_with_retry(git, path, rem, name, attempts, rebase_args=(), after_rebase=None,
                     retry_hint="next record", on_conflict=None):
    """Fast-forward push with a bounded fetch-rebase-retry; never forces. `rebase_args` and
    `after_rebase` are the ledger's `-X theirs` + TEAM.md regeneration; the knowledge channel passes
    neither (its notes union-merge through `.gitattributes`). `retry_hint` names when the caller's
    own next attempt comes (the ledger's is a watcher tick). Returns a status string."""
    last = ""
    for attempt in range(attempts):
        try:
            git(path, ["push", rem, f"HEAD:{name}"])
            return "published"
        except Exception as exc:                # noqa: BLE001 - contention is expected, not fatal
            last = str(exc)
            if attempt == attempts - 1:
                break
            try:
                git(path, ["fetch", rem, name])
                try:
                    git(path, ["rebase", *rebase_args, f"{rem}/{name}"])
                except Exception:
                    if on_conflict is None:
                        raise
                    stuck = on_conflict(git, path)  # knowledge: keep the edit on modify/delete
                    if stuck is None:               # the rebase never started: the original
                        raise                       # error stands (round-4 B1)
                    if stuck:
                        git(path, ["rebase", "--abort"])
                        return _CONFLICT_DEFERRAL.format(verb="publish", file=stuck)
                if after_rebase is not None:
                    after_rebase()
            except Exception:
                try:
                    git(path, ["rebase", "--abort"])
                except Exception:
                    pass
                break
    return f"publish deferred (will retry {retry_hint}): {last}"


#: Round-3 review F1: a conflict the resolver cannot settle is a PERMANENT state and must not wear
#: the transient "[rejected] (fetch first)" message forever — name the file and the lever.
_CONFLICT_DEFERRAL = ("{verb} deferred: note conflict in {file} could not be resolved automatically — "
                      "see \"When a note conflicts\" in skills/agrim-kg/SKILL.md (the rebase was aborted; "
                      "nothing is lost)")


def _resolve_modify_delete(git, path, rounds=200):
    """Drive a conflicted knowledge rebase to completion by KEEPING THE EDIT on every modify/delete
    conflict, and return "" — or the first path whose conflict is not that shape (the caller aborts
    and names it). `*.md merge=union` cannot touch modify/delete: it is a tree-level `DU`/`UD`, not a
    content merge, so before this a note deleted on machine A and edited on machine B left B's
    rebase aborting on every publish AND pull, forever, taking every later note with it (round-3
    review F1, measured against real bare origins).

    Returns `None` when NO rebase is in progress on entry — the rebase was refused outright (dirty
    tree, say) rather than conflicted, and the caller must report the ORIGINAL error, not a quiet
    success (round-4 review B1: this once turned "cannot rebase: unstaged changes" into `pulled`).

    Sides, MEASURED during a rebase (not assumed — they invert relative to a merge): a modify/delete
    where the REMOTE deleted (`DU`) leaves only stage 3, "theirs" = our replayed commit, holding the
    edit → `checkout --theirs`; the mirror (`UD`) leaves only stage 2 → `--ours`. Read from
    `ls-files -u -z` (unmerged index entries, NUL-separated, unquoted), never from porcelain, whose
    rename entries carry a second path chunk that reads as a bogus status code (round-4 S1). Both
    stages present is a content conflict — the one shape not settled here. Bounded: each replayed
    commit can conflict once, so `rounds` caps a rebase over an absurd history."""
    def in_progress():
        return any(pathlib.Path(git(path, ["rev-parse", "--git-path", d])).exists()
                   for d in ("rebase-merge", "rebase-apply"))

    if not in_progress():
        return None
    last = ""
    for _ in range(rounds):
        stages = {}
        for entry in git(path, ["ls-files", "-u", "-z"]).split("\0"):
            if "\t" in entry:
                meta, name = entry.split("\t", 1)
                stages.setdefault(name, set()).add(meta.split()[-1])
        if not stages:
            if not in_progress():
                return ""
            git(path, ["rebase", "--continue"])
            continue
        for name, present in stages.items():
            last = name
            if "2" in present and "3" in present:
                return name                     # both sides have content: not ours to guess at
            git(path, ["checkout", "--theirs" if "3" in present else "--ours", "--", name])
            git(path, ["add", "--", name])
        try:
            git(path, ["rebase", "--continue"])
        except Exception:                       # noqa: BLE001 - the next replayed commit conflicted;
            continue                            # loop reads the new unmerged set
    return last


def _staged_gitlinks(git, path):
    """Paths staged as mode 160000 (an embedded repository), from `ls-files -s -z` — NUL-separated
    and UNQUOTED, because the line format C-quotes a non-ASCII path and the quoted string then
    matches no pathspec while `rm` exits 0 (round-4 review B2: `vendör` shipped as a gitlink under
    an outcome that said it was skipped — the same quoting defect round 1 fixed in `_knowledge_changed_paths`)."""
    return [entry.split("\t", 1)[1] for entry in git(path, ["ls-files", "-s", "-z"]).split("\0")
            if entry.startswith("160000 ") and "\t" in entry]


def _publish_knowledge(sdlc_dir, config, git, attempts):
    """Stage every note, commit, and push whenever the branch is AHEAD of the remote (or the remote
    ref is absent) — not only when this call staged something, or a push that failed once would never
    be retried and a note-less bootstrap would never push (P4 finding B1). Fails CLOSED on a
    secret-shaped note BEFORE staging, naming the file and never the match (#2718)."""
    ch = _channel(KNOWLEDGE, sdlc_dir, config)
    path, name, rem = ch.path, ch.branch, ch.remote
    if not is_worktree(sdlc_dir, channel=KNOWLEDGE):
        return "not a worktree — run `sync.py bootstrap .sdlc --channel knowledge` first (nothing published)"
    lock = _knowledge_lock(sdlc_dir)
    if lock is None:
        return "publish deferred: another knowledge sync holds the lock"
    try:
        try:
            _write_knowledge_gitignore(path)            # migrate an older scaffold before staging
            _ensure_lines(path / ".gitattributes", KNOWLEDGE_GITATTRIBUTES)   # as the ledger's publish does
            for relative in _knowledge_changed_paths(git, path):  # every changed path; the worktree's own
                if _secret_shaped(path, relative):      # .gitignore already keeps non-notes out
                    return (f"publish refused: {relative} contains a secret-shaped string or cannot be "
                            f"read — edit the note and re-run `sync.py publish .sdlc --channel knowledge` "
                            f"(nothing staged)")
            git(path, ["add", "-A"])
            # Round-3 review F2: an embedded repository WITH a commit under analysis/ is re-included by
            # `!*/`, reads as a directory to the gate, and `add -A` stages it as a 160000 GITLINK — a
            # non-note leaving the machine. Unstaged and named, so the notes beside it still ship and
            # the outcome is not a quiet one (the loop relays it on every record until it is moved).
            gitlinks = _staged_gitlinks(git, path)
            for link in gitlinks:               # `-f`: the index entry differs from HEAD (a new link)
                git(path, ["rm", "--cached", "-q", "-f", "--", link])   # or IS HEAD's (drops it)
            if gitlinks and _staged_gitlinks(git, path):        # never claim a skip that did not
                return ("publish deferred: an embedded repository under analysis/ is still staged "
                        f"after unstaging ({', '.join(gitlinks)}) — move it out of the corpus")
            if git(path, ["diff", "--cached", "--name-only"]):
                git(path, ["commit", "-m", f"knowledge: {knowledge_actor(config)}"])
            if _ahead(git, path, rem, name) == 0:
                return "nothing to publish"
        except Exception as exc:                # noqa: BLE001 - publish() "always returns a status
            return f"publish deferred: {exc}"   # string, never raises" (#2449), on this channel too
        out = _push_with_retry(git, path, rem, name, attempts, on_conflict=_resolve_modify_delete)
        if out == "published" and gitlinks:
            return f"published (embedded repository not published, never a note: {', '.join(gitlinks)})"
        return out
    finally:
        if lock is not True:
            lock.close()


def _staged_paths(path, stream, who):
    """`ledger.files_for()`'s results, as strings relative to the worktree root — what `git add`
    and `git diff --cached --name-only` want, and what stays stable if `path` is later re-resolved."""
    return [str(p.relative_to(path)) for p in ledger.files_for(path / stream, who)]


_RECEIPT_PATH = re.compile(r"receipts/v1/([0-9a-f]{64})/(?:parent|merges/[0-9a-f]{40}(?:[0-9a-f]{24})?)\.json\Z")


def receipt_trusted_signers(config):
    """Return only the exact full fingerprints trusted to introduce shared receipts."""
    ledger_cfg = config.get("ledger") if isinstance(config, dict) else None
    authority = ledger_cfg.get("receipt_authority") if isinstance(ledger_cfg, dict) else None
    values = authority.get("trusted_signers") if isinstance(authority, dict) else None
    if not isinstance(values, (list, tuple, set)):
        return ()
    normalized = tuple(str(value).strip().lower() for value in values)
    if not normalized or any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in normalized):
        return ()
    return normalized


def verify_receipt_commit(path, relative, config, git):
    """Require the immutable receipt's own introducing commit to have an allowed signature."""
    trusted = receipt_trusted_signers(config)
    if not trusted:
        raise ValueError("receipt authority has no full trusted signer fingerprint")
    commit = git(path, ["log", "-1", "--format=%H", "--", relative])
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commit):
        raise ValueError("receipt object has no introducing commit")
    status = git(path, ["verify-commit", "--raw", commit])
    if not _load("merge_observation").trusted_signature(status, trusted):
        raise ValueError("receipt introducing signer is not trusted")
    return commit


def receipt_relative_path(value):
    """Accept only one canonical receipt object path, never a general staging path."""
    if not isinstance(value, str) or not _RECEIPT_PATH.fullmatch(value):
        raise ValueError("invalid receipt path")
    if pathlib.PurePosixPath(value).is_absolute() or ".." in pathlib.PurePosixPath(value).parts:
        raise ValueError("invalid receipt path")
    return value


def publish_receipt(sdlc_dir, receipt_path, config=None, run=None, attempts=PUSH_ATTEMPTS):
    """Publish precisely one immutable receipt and acknowledge its remote blob.

    Unlike normal ledger publication this never stages entries or TEAM.md.  A caller can therefore
    treat a successful return as the shared-authority acknowledgement required before mergeability.
    """
    git = run or _run_git
    cfg = config or ledger._config(sdlc_dir)
    relative = receipt_relative_path(receipt_path)
    path, name, rem = worktree(sdlc_dir), branch(cfg), remote(cfg)
    target = path / relative
    if not is_worktree(sdlc_dir) or not target.is_file() or target.is_symlink():
        return "receipt pending: receipt worktree/object unavailable"
    try:
        local_blob = git(path, ["hash-object", str(target)])
        git(path, ["add", "--", relative])
        staged = [item for item in git(path, ["diff", "--cached", "--name-only"]).splitlines() if item]
        if staged not in ([], [relative]):
            return "receipt pending: unrelated staged files"
        if staged:
            key = _RECEIPT_PATH.fullmatch(relative).group(1)
            git(path, ["commit", "-S", "-m", f"ledger: publish receipt {key}", "--", relative])
        local_commit = git(path, ["rev-parse", "HEAD"])
        verify_receipt_commit(path, relative, cfg, git)
    except Exception as exc:
        return f"receipt pending: {exc}"
    for _attempt in range(attempts):
        try:
            git(path, ["push", rem, f"HEAD:{name}"])
            git(path, ["fetch", rem, name])
            tip = git(path, ["rev-parse", f"{rem}/{name}"])
            git(path, ["merge-base", "--is-ancestor", local_commit, tip])
            if git(path, ["rev-parse", f"{tip}:{relative}"]) != local_blob:
                return "receipt pending: remote acknowledgement blob mismatch"
            return "receipt published"
        except Exception:
            try:
                git(path, ["fetch", rem, name])
                git(path, ["rebase", f"{rem}/{name}"])
            except Exception:
                try: git(path, ["rebase", "--abort"])
                except Exception: pass
                break
    return "receipt pending: publish acknowledgement failed"


def publish(sdlc_dir, config, run=None, attempts=PUSH_ATTEMPTS, channel=LEDGER):
    """Render TEAM.md, commit MY entries file(s) + the rolled-up view, and fast-forward both onto
    the ops branch — so a lead can read one file on the branch instead of five.

    Only my entries file(s) and TEAM.md ever change. Entries never conflict (single-writer per
    file, with `merge=union` as a backstop). TEAM.md is a pure function of the entries, so on a race
    we rebase (taking the winner's TEAM.md), regenerate it from the now-merged entries, and retry —
    the conflict is resolved by re-running, never by hand. Always returns a status string
    describing the outcome; never raises (#2449). `channel=KNOWLEDGE` is `_publish_knowledge`."""
    git = run or _run_git
    if channel == KNOWLEDGE:
        return _publish_knowledge(sdlc_dir, config, git, attempts)
    path, name, rem = worktree(sdlc_dir), branch(config), remote(config)
    if not is_worktree(sdlc_dir):
        return "not a worktree — run `sync.py init` first (nothing published)"
    who = ledger.actor(config)
    mine = _staged_paths(path, ledger.ENTRIES, who)
    if not mine:
        return "nothing to publish"
    # #2574/S1-G3: THE EVENTS STREAM IS NO LONGER STAGED. It used to be
    # `to_stage += _staged_paths(path, ledger.EVENTS, who)`, which published a machine's events to
    # the ops branch whenever the old `share` setting was not explicitly false. PRD §6.2 deletes
    # publishing: the journal is local-only, at `.sdlc/events/`, which is not in this worktree at
    # all. Nothing already on the branch is removed — a teammate's clone still pulls it, and it is
    # still read for one release.
    to_stage = list(mine)
    if _ensure_gitattributes(path):
        to_stage.append(".gitattributes")
    _write_team(sdlc_dir, path)
    to_stage.append("TEAM.md")
    git(path, ["add", *to_stage])
    if not git(path, ["diff", "--cached", "--name-only"]):
        return "nothing to publish"
    try:
        git(path, ["commit", "-m", f"ledger: {ledger.actor(config)}"])
    except Exception as exc:                     # noqa: BLE001 - honour the "always returns a status
        return f"publish deferred: {exc}"        # string, never raises" contract every other branch keeps

    def regenerate_team():                      # entries union-merge; TEAM.md -> theirs, then
        _write_team(sdlc_dir, path)             # regenerate from the merged entries
        if git(path, ["diff", "--name-only"]):
            git(path, ["add", "TEAM.md"])
            git(path, ["commit", "--amend", "--no-edit"])

    return _push_with_retry(git, path, rem, name, attempts, rebase_args=("-X", "theirs"),
                            after_rebase=regenerate_team, retry_hint="next tick")


def _write_team(sdlc_dir, path):
    """Regenerate the rolled-up team view from every author's entries. A pure function of the
    entries on disk, so it is always safe to overwrite (that is what makes a race resolvable)."""
    (path / "TEAM.md").write_text(ledger.render(ledger.read_all(sdlc_dir)), encoding="utf-8")


def bootstrap(sdlc_dir, config, run=None, channel=LEDGER):
    """One command to stand the ledger up for the whole team. `init` creates the ops branch LOCALLY,
    and `publish` only pushes once you own an entries file — so before this, the branch reached the
    remote only after your first goal wrote a claim, and a teammate who ran `init` saw nothing to
    fetch. bootstrap = init + seed YOUR (empty) entries file + publish, so the branch, your file, and
    the rolled-up TEAM.md all land on the remote the moment the ledger is switched on. Idempotent:
    re-running is a no-op ("already a worktree; nothing to publish"), and each teammate runs it once
    in their own clone to join.

    `channel=KNOWLEDGE` is init + publish with no seed file: the scaffold commit is ahead of an absent
    remote ref, so even a clone with no notes yet pushes the branch (#2698, P4 finding B1)."""
    git = run or _run_git
    if channel == KNOWLEDGE:
        first = init(sdlc_dir, config, run=git, channel=KNOWLEDGE)
        return f"{first}; {publish(sdlc_dir, config, run=git, channel=KNOWLEDGE)}"
    first = init(sdlc_dir, config, run=git)
    # the exact path a real append() would use right now — same function, so files_for() is
    # guaranteed to find it (same actor, same os.getpid() within this one process).
    mine = ledger.entry_file(sdlc_dir, ledger.actor(config))
    mine.parent.mkdir(parents=True, exist_ok=True)
    if not mine.exists():
        mine.touch()            # an empty file is enough for publish to land your presence + the branch
    return f"{first}; {publish(sdlc_dir, config, run=git)}"


# --------------------------------------------------------------- is anyone actually publishing? (#1599)
#
# Until #1599 the ONLY automatic publish was the watcher's: `watch_daemon.py` runs `publish` once a tick,
# and `loop.py` starts that watcher on every loop trigger (`_ensure_watcher`). That covers the loop
# and nothing else. The commands `/agrim-ledger` tells a person to type -- `ledger.py append`,
# `handoff.py open`/`track`/`ack` -- wrote locally, published nothing, started nothing, and said
# nothing about it. A machine that was not concurrently looping withheld every note, hand-off and
# ack from the team for as long as that stayed true: one adopter's held 67% of its ledger history
# back for two and a half weeks with every surface reporting the feature as working.
#
# The three functions below are the shared answer, used by BOTH the write path (`publish_after_write`,
# called from those CLI verbs) and the read path (`/agrim-doctor`'s ledger row, which cross-loads
# them rather than keeping a second copy of the rules -- `_secret_file_coverage`'s own precedent).

DEFAULT_WATCH_INTERVAL_SECONDS = 900
#: The floor for `stale_after_seconds` below, which IS the ledger watcher's staleness bound -- since
#: #2490 watch_daemon.py no longer holds a bound of its own, it calls that function. Same units, same
#: reason: at a small (or test) interval, "3 ticks" is not long enough for one genuinely slow call to
#: finish, and calling a live watcher dead is the more damaging error of the two.
MIN_STALE_AFTER_SECONDS = 180
#: #2499. `ledger.watch.log_max_bytes`: the size at which the tick owner rolls `state/watch.log` to
#: its ONE predecessor `watch.log.1`. 1 MiB: at the measured idle tick of 76 B and a 900 s interval
#: that is ~13,800 ticks ~= 144 days per file (~288 d with `.1`); a broken tick (~2 KB) ~5 days.
DEFAULT_WATCH_LOG_MAX_BYTES = 1048576
#: #2499. The disk-derived clamp in `watch_log_cap_bytes` never pushes the cap BELOW this (64 KiB):
#: a nearly-full disk must not turn the log into a file that rolls every few ticks and keeps no
#: history. Applies to the disk term only -- an explicit smaller config value is honoured as written.
MIN_WATCH_LOG_CAP_BYTES = 65536


def publish_on_write(config):
    """`ledger.publish_on_write` -- whether a HAND-TYPED ledger write pushes itself. Default ON;
    only a literal `False` declines it.

    THE OPPOSITE DEFAULT FROM `_reconcile_mode`, and for that function's own stated reason. It
    ships `"off"` because "a typo must never switch on a mechanism that WRITES" -- unattended, from
    inside the loop, onto a board. Nothing here is unattended: this fires only from a CLI verb a
    person typed, and the write it performs is the one that verb's own SKILL.md says it is for. A
    typo in THIS key can only make a typed command do its job, so it fails open, exactly as
    `discovery.blocking_priority_override` does for the same shape of reason.

    IT EXISTS AT ALL BECAUSE THE OPERATOR MUST KEEP THE CHOICE. That is the property both
    precedents this design is argued against are really protecting, and an adopter on a slow or
    offline remote has a real reason to want the write local and the push batched to the watcher."""
    return ledger.settings(config).get("publish_on_write") is not False


def watch_interval_seconds(config):
    """`ledger.watch.interval_seconds`, read the way `watch_daemon.py` reads it (default 900) but with the
    defensiveness a shell `or 900` cannot express: a non-dict `watch` block, a non-numeric value and
    a non-positive one all degrade to the default rather than raising, matching every other config
    reader in the kit. Non-positive is a real case, not a hypothetical -- `0` would otherwise make
    the staleness bound below collapse to its floor for a reason the operator never intended."""
    block = ledger.settings(config).get("watch")
    raw = block.get("interval_seconds") if isinstance(block, dict) else None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WATCH_INTERVAL_SECONDS
    return value if value > 0 else DEFAULT_WATCH_INTERVAL_SECONDS


def stale_after_seconds(interval):
    """THE ledger watcher's staleness rule (#1227), and the only place it is written: `3 x
    interval`, floored at `MIN_STALE_AFTER_SECONDS`.

    TAKES AN ALREADY-RESOLVED INTERVAL, AND THAT SIGNATURE IS LOAD-BEARING (#2490). Each caller
    resolves the interval its own way and they do not agree, on purpose: `watch_daemon.py:238`
    derives the EFFECTIVE one, i.e. after `SIGMA_WATCH_INTERVAL` has had its say, while
    `watcher_stale_after_seconds` below reads config ONLY. A config-taking single source would
    have silently dropped the env var for the watcher -- config 3600 + `SIGMA_WATCH_INTERVAL=1`
    is 10800 seconds from config and 180 from the env, a 60x error in the direction that makes a
    LIVE watcher read dead. `tests/test_watch.py:1181` pins that 180 end to end through a real
    daemon run, and the control that swaps this for the config-taking version reds it.

    The ARITHMETIC is shared; the INTERVAL READ is not, and this function makes no claim about the
    latter. `doctor.py::_ledger_watcher_state` keeps its own stricter `isinstance` guards before
    calling here (a quoted or boolean `interval_seconds` answers differently there than it does
    through `watch_interval_seconds`); converging those readers is a real behaviour change and its
    own goal, not this function's claim to make."""
    return max(interval * 3, MIN_STALE_AFTER_SECONDS)


def watcher_stale_after_seconds(config):
    """The CONFIG-READING wrapper over `stale_after_seconds` -- `3 x interval, floored`, with the
    interval taken from `ledger.watch.interval_seconds` alone.

    NOT the watcher's own call, and that is the point of the split (#2490). Since #2488 the watcher
    is Python (`watch_daemon.py`) and DOES import from this module; since #2490 it calls
    `stale_after_seconds` above, so the arithmetic has exactly one home and `doctor.py::
    _ledger_watcher_state` reads that same home. What `watch_daemon.py` still does NOT import is
    THIS function, deliberately: it derives the same bound from the EFFECTIVE interval, i.e. after
    `SIGMA_WATCH_INTERVAL` has had its say, and that env-first precedence is the watcher's own
    and belongs at the watcher. This function reads config ONLY, which is right for every caller
    here (doctor's ledger-delivery row, `publish_after_write`) and wrong for the watcher.
    `hooks/session_start.sh` keeps a residual copy of the arithmetic by design (`.sdlc/design/
    2417.md` X-2: a Claude Code hook stays host-specific and unported)."""
    return stale_after_seconds(watch_interval_seconds(config))


def watch_log_max_bytes(config):
    """`ledger.watch.log_max_bytes` (#2499), the CONFIGURED cap on `state/watch.log`, default 1 MiB.

    Accepted iff it is a non-bool `int` > 0. Anything else -- missing, a non-dict `ledger` or `watch`
    block, a bool, a float, a numeric string, zero, a negative -- degrades to the default, never to
    'unbounded' and never to a raise: the cap exists to bound growth, so a typo in the key that sets
    it must not be the thing that removes it. Stricter than `watch_interval_seconds` above on
    purpose (that one `int()`s a string): a size is copied from a doc, not typed from a shell, and
    `True` reading as 1 byte would roll the log on every single tick."""
    settings = ledger.settings(config)
    block = settings.get("watch") if isinstance(settings, dict) else None
    raw = block.get("log_max_bytes") if isinstance(block, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return DEFAULT_WATCH_LOG_MAX_BYTES
    return raw


def watch_log_cap_bytes(config, state_dir):
    """THE effective cap (#2499): `min(configured, max(free_disk // 100, 64 KiB))`, i.e. the log
    never takes more than ~1% of the state volume's free space (x2 with its predecessor), but the
    disk clamp never drives the cap below `MIN_WATCH_LOG_CAP_BYTES`. The floor applies to the disk
    term ONLY, so an explicit small config value (tests set 1024) is honoured as written.

    DERIVED FROM THE MACHINE, NOT A LAPTOP CONSTANT (AGENTS.md SAFETY) -- one `statfs` per call, and
    the daemon calls it once per tick. Both users of this rule -- `watch_daemon.rotate_log` and
    `doctor.py::_ledger_watcher_state` -- call this one function, so the daemon rolls at exactly the
    size doctor judges by; neither keeps a copy. `disk_usage` failing (a state dir on a vanished
    mount, an OSError from a weird FS) falls back to the configured value ALONE: fail-open to the
    config, never to 'no cap'."""
    configured = watch_log_max_bytes(config)
    try:
        free = shutil.disk_usage(str(state_dir)).free
    except OSError:
        return configured
    return min(configured, max(free // 100, MIN_WATCH_LOG_CAP_BYTES))


def heartbeat_path(sdlc_dir):
    """The file `watch_daemon.py` touches before every sub-call of every tick (#1227)."""
    return pathlib.Path(sdlc_dir) / "state" / "watch.heartbeat"


def watcher_liveness(sdlc_dir, config, now=None):
    """`("live"|"stale"|"absent", age_seconds|None)` for this repo's ledger watcher.

    AGE, NOT EXISTENCE -- the lesson #1227 already paid for once, and the rule every
    daemon-liveness row in this project follows: a watcher that died leaves its pidfile behind, and the kernel will happily hand that
    PID number to some unrelated process, so `kill -0` reads "alive" forever. Only the heartbeat's
    age can tell the difference. A MISSING heartbeat is reported as its own third state rather than
    folded into "stale": absent means no watcher has ever run here, which has a different remedy
    (start one) from a watcher that stopped, and the caller says so.

    Never raises -- an unreadable state dir is "absent", the same fail-open direction every other
    reader on this path takes."""
    try:
        mtime = heartbeat_path(sdlc_dir).stat().st_mtime
    except OSError:
        return ("absent", None)
    age = (time.time() if now is None else now) - mtime
    return (("live" if age < watcher_stale_after_seconds(config) else "stale"), age)


def unpublished(sdlc_dir, config, run=None, now=None):
    """What this clone is holding that the ops branch has never been shown, or `None` when git
    could not answer -- which is NOT "nothing unpublished" and must never be reported as one.

    `None` IS A THIRD ANSWER, following `_secret_file_coverage` exactly: a green row that means "we
    did not look" is the worst of the three outcomes, so every caller has to handle it separately
    from a measured zero.

    TWO SIGNALS, BECAUSE THERE ARE TWO WAYS TO BE UNPUBLISHED and either alone is a false
    all-clear. A watcher that never ran leaves entry files that were never even `git add`ed --
    that is the field case, and `status` finds it. A push that FAILED leaves a local commit and a
    clean working tree -- `publish()` commits before it pushes and returns "publish deferred" on
    the way out -- and only a comparison against the remote-tracking ref finds that one.

    LOCAL ONLY: no fetch, ever. The comparison is against `<remote>/<branch>` as this clone last
    saw it, so a diagnostic can never become a network call. The cost of that honesty is that a
    clone which has never fetched has no such ref, `rev-list` fails, and `unpushed_commits` comes
    back `None` -- the same "could not look" answer, scoped to that half."""
    git = run or _run_git
    path = worktree(sdlc_dir)
    if not is_worktree(sdlc_dir):
        return None
    try:
        status = git(path, ["status", "--porcelain", "--", ledger.ENTRIES])
    except Exception:                       # noqa: BLE001 - a diagnostic never raises at a caller
        return None
    pending, oldest = [], None
    for line in status.splitlines():
        # SPLIT, never a fixed `line[3:]` offset. Porcelain v1 pads the status to two columns, so an
        # unstaged modification arrives as `" M entries/…"` -- but `_run_git` returns `.strip()`ed
        # output, which eats the leading space of the FIRST line only. A fixed offset therefore
        # silently mis-slices exactly one filename per call and reports it as nothing to publish:
        # measured, not theorised. Splitting on whitespace is correct for every XY combination.
        parts = line.split(None, 1)
        name = parts[1].strip('"') if len(parts) == 2 else ""
        candidate = path / name if name else None
        if candidate is not None and candidate.is_file():
            pending.append(candidate)
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                continue
            oldest = mtime if oldest is None else min(oldest, mtime)
    try:
        ahead = int(git(path, ["rev-list", "--count",
                               f"{remote(config)}/{branch(config)}..HEAD"]))
    except Exception:                       # noqa: BLE001 - no remote-tracking ref == cannot tell
        ahead = None
    return {"files": len(pending),
            "oldest_age_seconds": None if oldest is None
            else max(0.0, (time.time() if now is None else now) - oldest),
            "unpushed_commits": ahead}


def _jsonl_line_count(path):
    """Real entries in one pending file: non-blank JSONL lines, fail-open on read/decode trouble.

    `errors="replace"` matters for the same reason `doctor._count_jsonl_lines` documents its own
    copy: a process killed mid-append truncates a multi-byte UTF-8 sequence, and the resulting
    UnicodeDecodeError is a ValueError, not an OSError -- an `except OSError` alone would let it
    escape and cost `pending_entry_count` its WHOLE answer over one half-written file.

    DELIBERATELY DUPLICATED from doctor.py's own copy rather than shared -- the same trade-off
    `hooks/session_start.sh` takes for its own copy of the watcher staleness rule (`.sdlc/design/
    2417.md` X-2, the one copy #2490 deliberately left standing): doctor.py is a different skill
    directory, and this function sits on the loop's hot pick-time path, where pulling in a whole
    sibling skill module for one helper is exactly the cross-skill coupling this codebase's
    lazy-loading conventions exist to avoid. Note the direction this cuts BOTH ways: #2490 spent
    that cost the other way for the staleness rule, where three copies had already measurably
    drifted -- an accepted duplicate is a judgement per site, not a blanket rule."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def _numstat_added(git, path, diff_range, scope):
    """Sum of the "added" column of `git diff --numstat <diff_range> -- <scope>`, as
    `{filename: added_lines}`. Binary/unreadable rows report `-` for both columns; `isdigit()`
    already excludes those without a special case. Returns `{}` on any git failure (an absent ref
    being the expected one) -- the caller decides what an empty answer means."""
    try:
        out = git(path, ["diff", "--numstat", diff_range, "--", scope])
    except Exception:                       # noqa: BLE001 - e.g. no such ref -- "cannot count this half"
        return {}
    result = {}
    for line in out.splitlines():
        cells = line.split("\t")
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        result[cells[2]] = result.get(cells[2], 0) + int(cells[0])
    return result


def pending_entry_count(sdlc_dir, config, run=None):
    """How many ledger ENTRY LINES this clone is holding that the team cannot see yet -- the real
    unit `_ensure_ledger_delivery` escalates on, where `unpublished()`'s own `files` count is the
    wrong one for a THRESHOLD: one file can hold one entry or five hundred, so two clones with
    identical `files` counts can be nowhere near equally behind.

    SUMS TWO DIMENSIONS, both real ways an entry can be stuck (plan-review finding 1):
      * NOT YET COMMITTED (staged, unstaged, or both) -- lines added to a pending file since HEAD,
        via `git diff --numstat HEAD -- ledger.ENTRIES`. A file `git diff` cannot compare against
        HEAD at all (never `git add`ed even once) is invisible to that call by construction, so
        every SUCH path found by `git status --porcelain -- ledger.ENTRIES` -- the same file list
        `unpublished()` finds -- is instead read directly and counted in full: every line on disk
        is new. Branching this way, rather than reading every dirty file's on-disk line count
        outright, matters the moment a file is PARTIALLY committed: append-only means a file can
        carry old, already-committed lines below newly-appended dirty ones, and a raw on-disk
        count would double-count the committed lines once the dimension below finds that same
        commit again.
      * COMMITTED BUT UNPUSHED -- `publish()` commits before it pushes, so a push that fails
        leaves a local commit and a (possibly fully) CLEAN working tree, invisible to the
        dimension above. Found via `git diff --numstat <remote>/<branch>..HEAD -- ledger.ENTRIES`,
        summing the "added" column -- the same ref comparison `unpublished()`'s own
        `unpushed_commits` already makes, one level more granular (lines, not commits).
      Disjoint by construction (one is "not committed yet", the other "committed, not pushed
      yet"), so nothing here can double-count between them.

    Returns `None` in exactly the two cases `unpublished()` itself returns bare `None` -- not a
    worktree, or the status call itself could not answer -- the same "could not look" contract,
    never conflated with a measured zero. A missing remote-tracking ref (this clone has never
    fetched or pushed the ops branch) makes the second dimension specifically uncountable; that
    half then contributes 0 rather than turning the WHOLE function dark, so a real backlog in the
    first dimension -- the commoner field case -- is still counted and can still trip the
    threshold. A pending file that cannot be read contributes 0 to the sum rather than raising
    (fail-open, matching every other reader on this path)."""
    git = run or _run_git
    path = worktree(sdlc_dir)
    if not is_worktree(sdlc_dir):
        return None
    try:
        status = git(path, ["status", "--porcelain", "--", ledger.ENTRIES])
    except Exception:                       # noqa: BLE001 - "could not look", mirrors unpublished()
        return None
    pending = []
    for line in status.splitlines():
        # Same whitespace split `unpublished()` uses, for the same reason: `_run_git` strips only
        # the FIRST line's leading padding, so a fixed offset mis-slices every other filename --
        # and, here, would also mis-attribute a leading-space status code.
        parts = line.split(None, 1)
        name = parts[1].strip('"') if len(parts) == 2 else ""
        candidate = path / name if name else None
        if candidate is not None and candidate.is_file():
            pending.append((name, candidate))
    since_head = _numstat_added(git, path, "HEAD", ledger.ENTRIES)
    total = sum(since_head.values())
    for name, candidate in pending:
        if name not in since_head:          # not diffable against HEAD == never `git add`ed
            total += _jsonl_line_count(candidate)
    total += sum(_numstat_added(git, path, f"{remote(config)}/{branch(config)}..HEAD",
                                ledger.ENTRIES).values())
    return total


def _ago(seconds):
    if seconds is None:
        return "unknown"
    if seconds < 90:
        return "%.0fs ago" % seconds
    if seconds < 5400:
        return "%.0f min ago" % (seconds / 60.0)
    return "%.1fh ago" % (seconds / 3600.0)


def publish_after_write(sdlc_dir, config, run=None, now=None, publish_fn=None):
    """The one line a hand-typed ledger write says about whether the team can see it yet. Returns
    the text for stderr, or `None` when there is genuinely nothing to say. NEVER raises.

    WHY PUBLISH AT ALL, given the kit's care about unrequested network writes. The rule the kit
    actually holds is not "an interactive command must not touch the network" -- `handoff.py open`
    creates a GitHub issue on exactly that path, so it plainly does not hold that. The two
    precedents draw sharper lines. `cheap_only` (setup_wizard.py) draws its line at CONSENT: the
    wizard's UNCONDITIONAL SessionStart hook takes the cheap subset, while `/agrim-doctor` -- "which
    the user typed on purpose" -- keeps the full sweep, network included. `discovery.reconcile.mode`
    draws its line at UNATTENDED writes, off by default so a typo cannot start one while nobody is
    watching. A `/agrim-ledger` write is on the typed-on-purpose side of the first line and the
    attended side of the second, and being seen by the team is the entire point of the entry.

    WHY NOT START THE WATCHER INSTEAD, which is the other repair the issue offers. It fails both of
    those lines at once. `_ensure_watcher` spawns a daemon that then fetches, runs `gh` reads
    (agent_watch, comment_watch) and posts to a webhook every 15 minutes, indefinitely, off one
    typed command -- the recurring, unattended, unconsented cost both precedents exist to prevent.
    A single push is bounded and finishes. Enrolling someone's machine in a background job is not
    something a one-line note should be able to do.

    AND WHY A LIVE WATCHER STILL WINS. Not politeness -- correctness. `watch_daemon.py` guards its own
    publish behind a mutex specifically so two publishers never contend on the ledger worktree's
    git index lock. A CLI publish fired behind a live watcher's back is precisely that second
    publisher, so a FRESH heartbeat means name the tick that will carry the entry rather than race
    it. A stale or absent one means nothing else will, and this write is on its own.

    IT NEVER CHANGES WHAT THE COMMAND RETURNS. The entry is already on disk before this runs; the
    push is a courtesy on top of a write that already succeeded. So every failure is swallowed into
    text, stdout stays the bare entry id that scripted callers parse, and exit codes (#1203's, in
    particular) are untouched. Same fail-open posture as `_ensure_watcher` and `safe_append`."""
    try:
        if not ledger.enabled(config) or not publish_on_write(config):
            return None
        if not is_worktree(sdlc_dir):
            # The #1391 LOCAL-ONLY shape: there is no ops branch to push to, so attempting one
            # would fail confusingly. Name the one command that fixes it instead -- this is the
            # "merely say you have not published" behaviour, kept for the one case where it is the
            # only true thing that can be said.
            return ("ledger: written LOCALLY ONLY -- .sdlc/ledger is not a git worktree, so "
                    "nothing here can publish and no other machine can see this entry. "
                    "Run /agrim-ledger (`sync.py bootstrap .sdlc`) once in this clone.")
        state, age = watcher_liveness(sdlc_dir, config, now=now)
        if state == "live":
            return ("ledger: not published yet -- the ledger watcher is live (last tick %s) and "
                    "publishes once per tick, so it carries this one." % _ago(age))
        result = (publish_fn or publish)(sdlc_dir, config, run=run)
        if result == "published":
            return "ledger: published -- the team can see this entry now."
        return ("ledger: NOT published (%s) -- no other machine can see this entry yet; a loop "
                "trigger or `sync.py publish .sdlc` will retry it." % result)
    except Exception as exc:                # noqa: BLE001 - the write already succeeded; report, never raise
        return ("ledger: NOT published (%s) -- the entry is safely on disk, but no other machine "
                "can see it yet; retry with `sync.py publish .sdlc`." % exc)


_VERBS = {"init": init, "pull": pull, "publish": publish, "bootstrap": bootstrap}


USAGE = ("usage: sync.py bootstrap|init|pull|publish <sdlc_dir> [--channel knowledge]   "
         "(bootstrap = one-shot create+seed+push; run it once per clone; --channel knowledge shares "
         ".sdlc/knowledge/analysis notes instead of the ledger)")


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 3 and argv[1] in _VERBS:
        sdlc_dir = argv[2]
        channel = argv[argv.index("--channel") + 1] if "--channel" in argv[3:-1] else LEDGER
        if channel not in (LEDGER, KNOWLEDGE):
            print(f"sync: unknown channel {channel!r} (expected {LEDGER} or {KNOWLEDGE})", file=sys.stderr)
            return 2
        config = ledger._config(sdlc_dir)
        if channel == KNOWLEDGE:
            if not knowledge_enabled(config):
                print('knowledge sync is off (config: "knowledge_graph": {"enabled": true, '
                      '"sync": {"enabled": true}})', file=sys.stderr)
                return 1
        elif not ledger.enabled(config):
            print('ledger is off (config: "ledger": {"enabled": true})', file=sys.stderr)
            return 1
        try:
            print(_VERBS[argv[1]](sdlc_dir, config, channel=channel))
        except Exception as exc:                # noqa: BLE001 - report, never traceback at a user
            print(f"sync: {exc}", file=sys.stderr)
            return 1
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

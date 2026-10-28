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
import errno
import fnmatch
import importlib.util
import os
import pathlib
import random
import re
import shutil
import signal
import subprocess
import sys
import time

try:                    # #2698: the knowledge channel's non-blocking lock is `fcntl.flock`, POSIX-only;
    import fcntl        # without it (Windows) THAT lock fails OPEN, exactly as `loop._try_acquire_claim_lock`
except ImportError:     # documents for the same mechanism. The LEDGER lock (#2081) does the opposite: it
    fcntl = None        # REFUSES by default without `fcntl` (see `_ledger_lock`)

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
state = _load("state")                          # #2081/#708: `safe_state_open`, `refuse_symlinks`

DEFAULT_BRANCH = "sdlc-ledger"
DEFAULT_REMOTE = "origin"
PUSH_ATTEMPTS = 5

#: #2574/S1-G3: ENTRIES ONLY. `{EVENTS}/*.jsonl merge=union` was dropped when the journal stopped
#: being published — nothing writes `ledger/events/` any more, so a union-merge attribute for it
#: would describe a path this branch never gains another line in. Existing clones keep the stale
#: line in their own `.gitattributes`; that is harmless (it matches nothing new) and deliberately
#: NOT cleaned up — see `BRANCH_README` and the CHANGELOG. Removing a line here is not retroactive:
#: `_ensure_gitattributes` below is additive by design and never deletes.
GITATTRIBUTES_LINES = (f"{ledger.ENTRIES}/*.jsonl merge=union", f"{ledger.DAG}/**/*.jsonl merge=union")
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


def _ref_value(key, value):
    """#710 (TM-04): a repository-supplied remote/branch reaches `git fetch|push|worktree` as a
    positional arg; a leading `-` makes it an OPTION (`--upload-pack=<cmd>` runs <cmd>). Refuse
    loudly. Lever: fix the value in .sdlc/config.json (not overridable)."""
    if not isinstance(value, str) or value.startswith("-") or any(c.isspace() or ord(c) < 32
                                                                 for c in value):
        raise ValueError(f"REFUSED: {key} {value!r} would reach git as an option (#710); "
                         f"set a plain remote/branch name in .sdlc/config.json")
    return value


def branch(config):
    return _ref_value("ledger.branch", ledger.settings(config).get("branch") or DEFAULT_BRANCH)


def remote(config):
    return _ref_value("ledger.remote", ledger.settings(config).get("remote") or DEFAULT_REMOTE)


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
# `.sdlc/knowledge/analysis/` — the notes `sigma-retro` §4 writes through `kg.py note` — has the
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
    return _ref_value("knowledge.branch",
                      knowledge_settings(config).get("branch") or DEFAULT_KNOWLEDGE_BRANCH)


def knowledge_remote(config):
    return _ref_value("knowledge.remote", knowledge_settings(config).get("remote") or DEFAULT_REMOTE)


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


# --------------------------------------------------------------- one ledger publisher at a time (#2081)
#
# Every path that rebases, resets, aborts or creates the LEDGER worktree takes this lock: `publish`, `pull`,
# `publish_receipt`, `_reroot_guarded` and `publish_entry`. Before it, two processes of one checkout could
# race: `publish_entry` aborts "a stale rebase" first, so a second publisher aborted the first one's LIVE
# rebase. The lock is a kernel `flock` (released the instant its holder dies, a reboot included: no clock, no
# sweep), on a file under the gitignored `state/` (never in the root checkout). An `flock` belongs to an OPEN
# FILE DESCRIPTION, so a second acquire in the same process is refused: a locked verb never calls another
# locked verb (`publish_entry` calls the unlocked `_publish_ledger` and `_reroot`).
#
#   * tick callers (the watcher's `sync.py publish`/`pull`, the Slack listener, `dag_lease`, `bootstrap`) take
#     it NON-blocking and report a deferral: the next tick retries;
#   * `publish_entry` WAITS for it, bounded by the same deadline as its git calls, then publishes.
#
# The file carries one line, `pid=<n> verb=<v> since=<epoch>`, written after acquiring and cleared before
# releasing, so a waiter can name the holder and its age. That record is information, never the lock.
#
# WHERE THE LOCK CANNOT BE MADE (no `fcntl`, or a filesystem whose `flock` is unsupported) this lock REFUSES by
# default (AGENTS.md SAFETY: refuse loudly rather than proceed weakly): `_ledger_lock` raises `LedgerLockRefused`,
# every locked verb defers with a line that names the lever, and an operator who accepts the race sets
# `SIGMA_LEDGER_ALLOW_UNSERIALISED=1` to publish unserialised. The older locks in this module and in `loop.py`
# still fail OPEN there; this change does not touch them.

LEDGER_LOCK_NAME = "ledger-sync.lock"
LEDGER_LOCK_REL = pathlib.Path("state") / LEDGER_LOCK_NAME      # below `.sdlc`, which is what #708 walks
#: the operator's opt-in to publishing without a lock where none can be made; `1`, `true` or `yes`, any case
LEDGER_ALLOW_UNSERIALISED_ENV = "SIGMA_LEDGER_ALLOW_UNSERIALISED"
#: how long a holder may take before a waiter calls it wedged (several times the 90 s publish limit and the
#: watcher's 120 s call limit); the waiter's printed line is capped at 240 characters, so the lever itself
#: lives in docs/cloud-sessions.md
LEDGER_LOCK_WEDGED_AFTER = 300
#: the notes this module says once per LOADED COPY of it on stderr, normally once per command (`loop._load("sync")`
#: is uncached, so a process that loads a second copy says each note again); a test resets it (module state is
#: shared by every test file that loads this module through the script cache)
_LOCK_NOTES_SAID = set()


def ledger_lock_path(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / LEDGER_LOCK_NAME


class LedgerLockRefused(OSError):
    """The ledger lock cannot be made here and the operator has not opted in to publishing without it. An
    `OSError` on purpose: `_with_ledger_lock` and `publish_entry` already turn one into a deferral."""


def _unserialised_allowed():
    return os.environ.get(LEDGER_ALLOW_UNSERIALISED_ENV, "").strip().lower() in ("1", "true", "yes")


def _no_lock_mechanism(key, why):
    """No way to take the ledger lock here (`why`). REFUSES: raises `LedgerLockRefused` and says so on stderr once
    per loaded copy of this module (normally once per command), naming the lever. With the operator's opt-in it
    says once that publishes are not serialised and returns `True` (the caller then runs unlocked)."""
    if _unserialised_allowed():
        _say_once(key + "-allowed",
                  f"ledger publishes are not serialised here: {why}; {LEDGER_ALLOW_UNSERIALISED_ENV} is set, "
                  "so concurrent publishers in one checkout can collide")
        return True
    text = (f"ledger publishes cannot be serialised here: {why}; "
            f"set {LEDGER_ALLOW_UNSERIALISED_ENV}=1 to publish without the lock")
    _say_once(key + "-refused", text)
    raise LedgerLockRefused(text)


def _say_once(key, text):
    if key in _LOCK_NOTES_SAID:
        return
    _LOCK_NOTES_SAID.add(key)
    try:
        print(text, file=sys.stderr)
    except Exception:                           # noqa: BLE001 - a note must never be what breaks a verb
        pass


def _ledger_lock(sdlc_dir, wait_until=None, verb="publish"):
    """Take the ledger worktree lock. Returns the open file (keep it referenced; `_release_ledger_lock`
    releases) or `None` when another process holds it.

    `wait_until` is a `time.monotonic()` value: the call polls (0.05 to 0.1 s, jittered) until then; `None`
    tries once. No `fcntl` (Windows), or a filesystem whose `flock` is unsupported (any errno other than
    EWOULDBLOCK/EAGAIN), REFUSES: it raises `LedgerLockRefused`, an `OSError`, with one stderr line per loaded
    copy of this module (normally once per command) that names the lever (AGENTS.md SAFETY: refuse loudly rather
    than proceed weakly). With `SIGMA_LEDGER_ALLOW_UNSERIALISED=1` (or `true`, `yes`) it returns `True` instead,
    `_release_ledger_lock` ignores it, and the caller runs UNSERIALISED, with one stderr line saying so. Opening the
    file can also raise `OSError`, and a symlink at the lock file or at `state/` raises `state.UnsafeStatePath`
    (#708, below). The cost of refusing: on such a platform every ledger publish defers until the operator opts
    in; the older locks in this module and `loop.py` still fail open there."""
    if fcntl is None:
        return _no_lock_mechanism("no-fcntl", "no fcntl on this platform")
    # #708: Sigma never writes through a symlink under .sdlc/. A repository can COMMIT one at the lock file or at
    # `state/`, and `ledger.py`, `assign.py` and `liveness_prune.py` reach this lock without the entry guard that
    # `loop.py` and `work.py` run. `safe_state_open` lstat-walks the path (creating `state/` itself), opens with
    # O_NOFOLLOW and takes only a regular file; a refusal is an `OSError` that names the symlink and the lever
    # (`git rm` it), so every locked verb defers. NOT overridable: the opt-in below is for a platform with no lock.
    fd = state.safe_state_open(sdlc_dir, LEDGER_LOCK_REL, "a")
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                fd.close()
                return _no_lock_mechanism("flock-unsupported",
                                          f"flock is unsupported on this filesystem ({exc.strerror or exc.errno})")
            left = None if wait_until is None else wait_until - time.monotonic()
            if left is None or left <= 0:
                fd.close()
                return None
            time.sleep(min(left, 0.05 + 0.05 * random.random()))
    try:
        fd.seek(0)
        fd.truncate()
        fd.write("pid=%d verb=%s since=%d\n" % (os.getpid(), verb, int(time.time())))
        fd.flush()
    except (OSError, ValueError):               # the record is information, not the lock
        pass
    return fd


def _release_ledger_lock(lock):
    """Clear the holder record, THEN close (closing releases the lock): the next holder's record is never
    erased by the one before it. A no-op for `True` and `None`."""
    if lock is None or lock is True:
        return
    try:
        lock.seek(0)
        lock.truncate()
    except (OSError, ValueError):
        pass
    try:
        lock.close()
    except OSError:
        pass


def _ledger_lock_holder(sdlc_dir):
    """Who holds the lock, for a waiter's message: `pid N (verb), held Ns`, longer than
    `LEDGER_LOCK_WEDGED_AFTER` seconds adds a pointer to the lever. `""` when the lock is free. The lock is
    probed FIRST, on a separate open file description, so a dead holder's stale record is never reported as a
    holder. Never raises."""
    unknown = "a holder that has not identified itself yet"
    try:
        if fcntl is None:
            return ""
        try:
            # #708: a probe looks at a lock file that is there. It never creates one, and never opens through a
            # symlink (the walk for `state/`, O_NOFOLLOW for the file): a link is answered like a missing file. It
            # also never blocks on a planted FIFO (O_NONBLOCK) and reads the record from the descriptor it locked.
            state.refuse_symlinks(sdlc_dir, LEDGER_LOCK_REL)
            fd = os.open(ledger_lock_path(sdlc_dir),
                         os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            return ""
        with os.fdopen(fd, "r", encoding="utf-8") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    return ""
            else:
                fcntl.flock(probe, fcntl.LOCK_UN)
                return ""
            record = probe.read(200)
        fields = dict(part.split("=", 1) for part in record.split() if "=" in part)
        pid, since = int(fields["pid"]), int(fields["since"])
        if not ledger.pid_alive(pid):
            return unknown
        held = max(0, int(time.time()) - since)
        wedged = (", looks wedged (docs/cloud-sessions.md)" if held > LEDGER_LOCK_WEDGED_AFTER else "")
        return f"pid {pid} ({fields.get('verb', '?')}), held {held}s{wedged}"
    except Exception:                           # noqa: BLE001 - naming the holder must never raise
        return unknown


LOCK_RELEASED_WHILE_CHECKING = "the ledger worktree lock was released while this step checked who held it"


def _with_ledger_lock(sdlc_dir, verb, body, busy, wait_until=None):
    """Run `body()` holding the ledger lock. When another process holds it, the lock file cannot be opened, or
    the lock cannot be made here (`LedgerLockRefused`) return `busy(<why>)` instead, so a tick caller defers
    rather than waits and the string names the reason. Never nest: see above."""
    try:
        lock = _ledger_lock(sdlc_dir, wait_until=wait_until, verb=verb)
    except OSError as exc:
        return busy(f"cannot take the lock: {exc}")
    if lock is None:
        holder = _ledger_lock_holder(sdlc_dir)
        return busy(f"another ledger publisher holds the worktree lock ({holder})" if holder
                    else f"{LOCK_RELEASED_WHILE_CHECKING}; the next step retries")
    try:
        return body()
    finally:
        _release_ledger_lock(lock)


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
    return str(pathlib.Path(str(cwd)) / ".sigma-ledger-hooks-disabled")


def _run_git(cwd, args, timeout=None):
    """Run git. With `timeout` (seconds) the call is bounded (#2076): git runs in its own process
    group with stdin closed and no credential prompt, and an overrun is sent SIGTERM (so git removes its
    own lock files), then SIGKILL two seconds later, and raises `TimeoutError`."""
    if timeout is not None:
        return _run_git_bounded(cwd, args, timeout)
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


def _run_git_bounded(cwd, args, timeout):
    env = dict(os.environ, GIT_EDITOR="true", GIT_TERMINAL_PROMPT="0")
    proc = subprocess.Popen(
        ["git", "-C", str(cwd), "-c", f"core.hooksPath={_no_hooks_dir(cwd)}", *args],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        start_new_session=True)
    try:
        out, err = proc.communicate(timeout=max(timeout, 0.01))
    except subprocess.TimeoutExpired:
        _end_group(proc)
        raise TimeoutError(f"git {args[0]} did not finish within {timeout:.0f}s") from None
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(err or out).strip()}")
    return out.strip()


def _end_group(proc):
    """SIGTERM the child's process group, wait two seconds, then SIGKILL it. Only a group Sigma started."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.communicate(timeout=2)
            return
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate(timeout=5)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired, OSError):
        pass


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


def _local_branch_exists(git, root, name):
    try:
        git(root, ["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"])
        return True
    except Exception:                           # noqa: BLE001 - a missing ref is a non-zero exit
        return False


def _local_ahead(git, root, name, rem):
    """True when the local branch `name` exists and is not provably at-or-behind `<rem>/<name>`: it
    has commits the remote lacks, or the count could not be read (fail closed). Never raises. A
    missing local branch is False, so the caller's force simply creates it."""
    if not _local_branch_exists(git, root, name):
        return False
    try:
        return int(git(root, ["rev-list", "--count", f"{rem}/{name}..{name}"])) > 0
    except Exception:                           # noqa: BLE001 - unreadable/unparseable: fail closed
        return True


# --------------------------------------------------------------- one history, never two (#1142)
#
# A bare `git fetch <rem> <name>` writes `refs/remotes/<rem>/<name>` ONLY when the clone's
# `remote.<rem>.fetch` maps that branch. `git clone --single-branch` -- and `--depth N`, which implies
# it -- writes a refspec for `main` alone, so in such a clone the fetch wrote FETCH_HEAD and nothing
# else, every `<rem>/<name>` read below failed, and `init` took the failure for "nobody has pushed the
# branch yet": it started a SECOND history from the empty tree beside the team's. Field case: a
# 325-commit `sdlc-ledger` branch on origin, an orphan here, and a publish rejected non-fast-forward every
# tick while saying it would retry. Every fetch in this module now names its destination.

#: Characters that would make `+refs/heads/<name>:<tracking>` mean something else -- a glob that
#: fetches every head, a second `:`. `_ref_value` only stops a value from being an OPTION.
_REFSPEC_META = frozenset("*?[\\^~:")


def _tracking_ref(git, cwd, rem, name):
    """`refs/remotes/<rem>/<name>`, the ref every comparison here reads, or a loud REFUSED when it
    cannot exist: a remote-tracking ref needs a NAMED remote (a URL or path makes the refspec
    invalid -- and before #1142 such a config forked on the second machine anyway). `git remote` is
    consulted only for a value that looks like a URL or path, since a name may legally hold a `/`."""
    if any(c in _REFSPEC_META for c in name):
        raise ValueError(f"REFUSED: branch {name!r} is not a plain branch name, so it cannot be "
                         "fetched by name (#1142); set a plain branch in .sdlc/config.json")
    if any(c in "/:\\" for c in rem) and rem not in git(cwd, ["remote"]).split():
        raise ValueError(f"REFUSED: remote {rem!r} is a URL or path, and the ops branch needs a named "
                         "remote to track (#1142); `git remote add <name> <url>` and set that name "
                         "in .sdlc/config.json")
    return f"refs/remotes/{rem}/{name}"


def _fetch(git, cwd, rem, name):
    """Fetch `<rem>`'s `<name>` INTO `refs/remotes/<rem>/<name>`, whatever `remote.<rem>.fetch`
    says (see the block comment above). `+`, as git's own default refspec has it, so a branch someone
    force-pushed still updates the ref instead of failing the fetch. Returns the tracking ref."""
    tracking = _tracking_ref(git, cwd, rem, name)
    git(cwd, ["fetch", rem, f"+refs/heads/{name}:{tracking}"])
    return tracking


def _remote_branch_state(git, root, rem, name):
    """What the remote says about `refs/heads/<name>` once a fetch of it has failed: "present",
    "absent", "unreadable" (a configured remote that did not answer), or "no-remote" (`rem` is not
    configured here at all -- a local-only setup, with nothing to diverge from). `ls-remote` is given
    the full ref as its pattern (no `--heads`, renamed in git 2.46) and its answer is matched exactly,
    because a pattern matches any ref that merely ENDS with it."""
    try:
        out = git(root, ["ls-remote", rem, f"refs/heads/{name}"])
    except Exception:                           # noqa: BLE001 - unreachable, or not a remote at all
        try:
            configured = rem in git(root, ["remote"]).split()
        except Exception:                       # noqa: BLE001 - cannot tell: fail closed
            configured = True
        return "unreadable" if configured else "no-remote"
    refs = {line.split("\t", 1)[1].strip() for line in out.splitlines() if "\t" in line}
    return "present" if f"refs/heads/{name}" in refs else "absent"


def _shallow_commits(git, cwd):
    """The commits this repository's history is CUT at, which `rev-list --max-parents=0` reports as
    roots although they are not. `--git-path` names the common directory's file even from a linked
    worktree (probed), and prints it relative to the `-C` directory in a main checkout."""
    if git(cwd, ["rev-parse", "--is-shallow-repository"]) != "true":
        return set()
    shallow = pathlib.Path(git(cwd, ["rev-parse", "--git-path", "shallow"]))
    if not shallow.is_absolute():
        shallow = pathlib.Path(cwd) / shallow
    return set(shallow.read_text(encoding="utf-8").split())


def _shares_history(git, cwd, rem, name):
    """True when HEAD and `<rem>/<name>` have a common ancestor, False when they PROVABLY have none,
    None when it cannot be told -- and None is never read as False, because False is what makes a
    caller call the state permanent.

    `merge-base` answers the common case in O(commits since the base): 11.7 ms on a 32,500-commit
    branch (`.sdlc/research/1142-measure.py history`; git 2.46.2, one laptop, load ~3, best of 5).
    It exits 1 for "no base" and 128 for a missing ref, which the injected `git` cannot tell apart,
    so a failure is settled by root sets instead: two histories share an ancestor exactly when they
    share a root (115.6 ms for the same branch, and only ever on this path). A root that is really a
    shallow cut proves nothing, so that is None."""
    tracking = f"refs/remotes/{rem}/{name}"
    try:
        git(cwd, ["merge-base", "HEAD", tracking])
        return True
    except Exception:                           # noqa: BLE001 - no base, or no ref: settled below
        pass
    try:
        mine = set(git(cwd, ["rev-list", "--max-parents=0", "HEAD"]).split())
        theirs = set(git(cwd, ["rev-list", "--max-parents=0", tracking]).split())
        cut = _shallow_commits(git, cwd)
    except Exception:                           # noqa: BLE001 - unreadable: cannot tell
        return None
    if not mine or not theirs or (mine | theirs) & cut:
        return None
    return bool(mine & theirs)


#: PERMANENT, so it must never wear the transient "will retry" wording, and it names both ways to get
#: here: the #1142 fork, and a remote branch someone deleted and recreated -- where re-rooting
#: republishes this clone's copy of whatever was removed, which is the operator's call to make.
_UNRELATED = ("{verb} refused: {name} here shares no history with {rem}/{name} (it was started on its "
              "own -- #1142 -- or {rem}/{name} was deleted and recreated), so no retry can land it; "
              "`sync.py bootstrap .sdlc{flag}` re-roots it onto {rem}/{name} and republishes this "
              "clone's own {what}")
#: Not provably unrelated, but no base is visible either: rebasing would replay every local commit
#: onto the remote all the same, which is the graft. So nothing is rebased; this one can heal.
_NO_BASE_VISIBLE = ("{verb} deferred: no common ancestor with {rem}/{name} is visible in this clone (a "
                    "shallow cut or an unreadable repository), so it is not rebasing onto it -- deepen "
                    "the history (`git fetch --deepen`) or run /sigma-doctor")


def _diverged(git, path, rem, name, verb, channel):
    """`None` when it is safe to rebase onto `<rem>/<name>`, else the status that says why not.
    Called BEFORE every rebase, never after one fails: the ledger's `rebase -X theirs` SUCCEEDS on an
    unrelated history -- its add/add conflicts on README.md and TEAM.md resolve to this clone's
    copies -- and would graft the empty-tree root onto everyone's branch."""
    shared = _shares_history(git, path, rem, name)
    if shared is True:
        return None
    if shared is False:
        return _UNRELATED.format(verb=verb, name=name, rem=rem, what="notes" if channel == KNOWLEDGE
                                 else "entries", flag=" --channel knowledge" if channel == KNOWLEDGE else "")
    return _NO_BASE_VISIBLE.format(verb=verb, rem=rem, name=name)


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

    tracking = _tracking_ref(git, root, rem, name)  # a REFUSED here is the operator's to see (#1142)
    try:                                        # a colleague may have pushed the branch already
        _fetch(git, root, rem, name)
        if _local_ahead(git, root, name, rem):
            # #592: a local ops branch holding commits the remote lacks (a ledger entry not yet
            # published) is never moved: `branch --force` would drop them. Left as it is, the next
            # publish pushes them. NOT an exception, so the create-from-empty-tree fallback below,
            # which cannot succeed on an existing branch, is not entered either.
            print(f"sigma: sync init: local branch {name} has commits {rem}/{name} lacks (or they "
                  "could not be counted); leaving it where it is", file=sys.stderr)
        else:
            git(root, ["branch", "--force", name, tracking])
    except Exception as exc:
        if _local_branch_exists(git, root, name):
            print(f"sigma: sync init: could not move local branch {name} onto {rem}/{name} "
                  f"({_scrub(exc, 200)}); keeping it as it is", file=sys.stderr)
        else:
            # #1142: the empty tree ONLY when the remote provably has no such branch, or there is no
            # remote at all. A failed fetch is not that answer -- it is how a single-branch clone
            # used to fork the team's ledger -- so any other state refuses and creates nothing;
            # entries written meanwhile stay in the plain directory and are carried in later.
            state = _remote_branch_state(git, root, rem, name)
            if state == "present":
                raise RuntimeError(f"REFUSED: {rem} has {name} but it could not be fetched "
                                   f"({_scrub(exc, 200)}); a second {name} started here could never be "
                                   "published (#1142) -- fix the fetch, then re-run "
                                   "`sync.py bootstrap .sdlc`") from exc
            if state == "unreadable":
                raise RuntimeError(f"REFUSED: could not read {rem} to see whether it already has {name} "
                                   f"({_scrub(exc, 200)}); not starting a history that could fork the "
                                   f"team's (#1142) -- re-run when {rem} is reachable") from exc
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
    return _with_ledger_lock(sdlc_dir, "pull", lambda: _pull_ledger(git, path, name, rem),
                             busy=lambda why: f"pull deferred: {why}")


def _pull_ledger(git, path, name, rem):
    """`pull`'s body, called with the ledger lock held."""
    _fetch(git, path, rem, name)
    stop = _diverged(git, path, rem, name, "pull", LEDGER)
    if stop is not None:
        return stop
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
            _fetch(git, ch.path, ch.remote, ch.branch)
            stop = _diverged(git, ch.path, ch.remote, ch.branch, "pull", KNOWLEDGE)
            if stop is not None:
                return stop
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
    """Commits on HEAD the remote-tracking ref lacks, or `None` when `rev-list` cannot answer —
    usually because that ref does not exist (never fetched or pushed). `None` is never "nothing to
    do": the knowledge channel reads it as "push", the ledger's `_stranded` as "push if `rem` is a
    configured remote name"."""
    try:
        return int(git(path, ["rev-list", "--count", f"{rem}/{name}..HEAD"]))
    except Exception:                           # noqa: BLE001 - no such ref
        return None


def _stranded(git, path, rem, name):
    """True when HEAD holds commits a push should still carry although this call committed nothing:
    it is ahead of `<rem>/<name>`, or there is no such ref and `rem` is a configured remote NAME --
    the ref is then absent only because no fetch or push of the branch has succeeded yet (a first
    push that failed). For a remote that is not configured, or a URL or path where a name belongs,
    the ref can never exist (`_record_push` cannot write it), so "absent" carries no information
    there, and reading it as "push" would push on every tick, forever (#1210 plan review, B1).
    Never raises: an unanswerable `git remote` reads as "no", which costs latency, never a write."""
    ahead = _ahead(git, path, rem, name)
    if ahead is not None:
        return ahead > 0
    try:
        return rem in git(path, ["remote"]).split()
    except Exception:                           # noqa: BLE001 - cannot tell: no new network write
        return False


def _record_push(git, path, rem, name, before):
    """#1142 review, finding 1: a push moves `refs/remotes/<rem>/<name>` only when the fetch refspec
    maps the branch. In a single-branch clone it never does, so the ref `_fetch` created would lag
    every successful publish, and `unpublished()`, the doctor and `_ensure_ledger_delivery` would
    count every published commit as unpushed.

    `before` is HEAD as read just before the push. It is recorded ONLY if HEAD still names it now:
    another publisher in this worktree can commit while the push runs, and recording the newer HEAD
    would call that commit published while the remote lacks it -- a false all-clear (review round 2).
    When HEAD moved, nothing is recorded: the ref stays stale, which over-reports until the next
    fetch -- the safe direction. The push itself keeps the fixed-literal `HEAD:` source, which can
    never be empty, so it can never delete the branch (docs/branching-model.md 13b). Best-effort:
    a failure only leaves the ref as stale as git itself would have."""
    try:
        if re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", before) and git(path, ["rev-parse", "HEAD"]) == before:
            git(path, ["update-ref", f"refs/remotes/{rem}/{name}", before])
    except Exception:                           # noqa: BLE001 - see docstring
        pass


def _jitter_sleep(attempt, base=0.15, cap=3.0, rng=None, sleep=None, deadline_at=None, clock=None):
    """Full-jitter exponential backoff (#2076): a uniform pause in [0, min(cap, base * 2**attempt)].
    Contenders that were rejected together do not retry together. Returns the pause (for tests).

    #2081: with `deadline_at` (a `time.monotonic()` value) the pause is cut to the time left, never below
    zero (a negative value would make `time.sleep` raise inside `_push_with_retry`'s `except` block and
    escape `publish`), so a pause can no longer carry a bounded step past its deadline. `rng`, `sleep` and
    `clock` are looked up when the function runs, not when it is defined, so a test can patch them."""
    rng = rng or random.random
    sleep = sleep or time.sleep
    pause = rng() * min(cap, base * (2 ** attempt))
    if deadline_at is not None:
        pause = max(0.0, min(pause, deadline_at - (clock or time.monotonic)()))
    sleep(pause)
    return pause


def _push_with_retry(git, path, rem, name, attempts, rebase_args=(), after_rebase=None,
                     retry_hint="next record", on_conflict=None, channel=LEDGER, sleep=None):
    """Fast-forward push with a bounded fetch-rebase-retry; never forces. `rebase_args` and
    `after_rebase` are the ledger's `-X theirs` + TEAM.md regeneration; the knowledge channel passes
    neither (its notes union-merge through `.gitattributes`). `retry_hint` names when the caller's
    own next attempt comes (the ledger's is a watcher tick). A history the remote's shares nothing
    with is never rebased and never retried (#1142: `_diverged`). Returns a status string."""
    last = ""
    for attempt in range(attempts):
        try:
            before = git(path, ["rev-parse", "HEAD"])
            git(path, ["push", rem, f"HEAD:{name}"])
            _record_push(git, path, rem, name, before)
            return "published"
        except Exception as exc:                # noqa: BLE001 - contention is expected, not fatal
            last = str(exc)
            if attempt == attempts - 1:
                break
            if sleep is not None:               # #2076: only a caller that opts in pauses between attempts
                sleep(attempt)
            try:
                _fetch(git, path, rem, name)
                stop = _diverged(git, path, rem, name, "publish", channel)
                if stop is not None:
                    return stop
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
                      "see \"When a note conflicts\" in skills/sigma-kg/SKILL.md (the rebase was aborted; "
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
        out = _push_with_retry(git, path, rem, name, attempts, on_conflict=_resolve_modify_delete,
                               channel=KNOWLEDGE)
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
    return _with_ledger_lock(
        sdlc_dir, "receipt", lambda: _push_receipt(git, path, name, rem, cfg, relative, target, attempts),
        busy=lambda why: f"receipt pending: {why}")


def _push_receipt(git, path, name, rem, cfg, relative, target, attempts):
    """`publish_receipt`'s body, called with the ledger lock held."""
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
            tip = git(path, ["rev-parse", _fetch(git, path, rem, name)])
            git(path, ["merge-base", "--is-ancestor", local_commit, tip])
            if git(path, ["rev-parse", f"{tip}:{relative}"]) != local_blob:
                return "receipt pending: remote acknowledgement blob mismatch"
            return "receipt published"
        except Exception:
            try:
                _fetch(git, path, rem, name)
                if _shares_history(git, path, rem, name) is not True:
                    break                       # #1142: never graft a receipt onto another history
                git(path, ["rebase", f"{rem}/{name}"])
            except Exception:
                try: git(path, ["rebase", "--abort"])
                except Exception: pass
                break
    return "receipt pending: publish acknowledgement failed"


def _dead_pid_paths(path, who, config):
    """#1932: entries files whose writer has stopped, as strings relative to the worktree root.

    `ledger.files_for(who)` only claims the files of THIS actor, and a file left by a stopped
    process is staged by nobody once its pid is gone: it sat untracked and the next `pull` was
    deferred ("You have unstaged changes"). A file is swept when its name is
    `<actor>-<host>.<pid>.jsonl` with THIS machine's host token (any actor name, the `_this_clones`
    precedent: the actor can resolve differently between runs), and a
    pid `ledger.pid_alive` says is gone (so never this process). A live pid's file, another host's file (its pid means
    nothing here) and a legacy pid-only name are never swept. Best effort: any error sweeps nothing."""
    try:
        host = ledger._host_token(config)
        found = []
        for p in sorted((path / ledger.ENTRIES).glob("*.jsonl")):
            stem = p.name[:-len(".jsonl")]
            actor, _, instance = stem.rpartition("-")
            token, dot, pid = instance.rpartition(".")
            if not (actor and dot and pid.isdigit() and token == host):
                continue
            if ledger.pid_alive(int(pid)):     # this process is alive too, so its own file is never swept
                continue
            found.append(str(p.relative_to(path)))
        return found
    except Exception:                            # noqa: BLE001 - a sweep must never break publish
        return []


def publish(sdlc_dir, config, run=None, attempts=PUSH_ATTEMPTS, channel=LEDGER, backoff=None):
    """Render TEAM.md, commit MY entries file(s) + the rolled-up view, and fast-forward both onto
    the ops branch — so a lead can read one file on the branch instead of five.

    Only my entries file(s) and TEAM.md ever change. Entries never conflict (single-writer per
    file, with `merge=union` as a backstop). TEAM.md is a pure function of the entries, so on a race
    we rebase (taking the winner's TEAM.md), regenerate it from the now-merged entries, and retry —
    the conflict is resolved by re-running, never by hand. Always returns a status string
    describing the outcome; never raises (#2449), staging included (#1210) — except #710's REFUSED
    for a remote/branch value that would reach git as an option, which stays loud on purpose.
    `channel=KNOWLEDGE` is `_publish_knowledge`.

    A call that stages nothing, made by an actor that owns an entries file, still pushes a STRANDED
    commit (`_stranded`): a push that failed leaves its commit local, and before #1210 the next call
    returned "nothing to publish" because it had nothing new to stage, so "will retry next tick" was
    false until a later entry carried the commit. The knowledge channel already pushed whenever it
    was ahead (its P4 finding B1)."""
    git = run or _run_git
    if channel == KNOWLEDGE:
        return _publish_knowledge(sdlc_dir, config, git, attempts)
    worktree(sdlc_dir), branch(config), remote(config)       # a REFUSED value stays loud (#710), lock or no lock
    if not is_worktree(sdlc_dir):
        return "not a worktree — run `sync.py init` first (nothing published)"
    return _with_ledger_lock(
        sdlc_dir, "publish", lambda: _publish_ledger(sdlc_dir, config, run, attempts, backoff),
        busy=lambda why: f"publish deferred (will retry next tick): {why}")


def _publish_ledger(sdlc_dir, config, run=None, attempts=PUSH_ATTEMPTS, backoff=None):
    """`publish`'s ledger body, called with the ledger lock held (`publish_entry` holds it across its whole
    step and calls this directly: a nested acquire would be refused by its own lock)."""
    git = run or _run_git
    path, name, rem = worktree(sdlc_dir), branch(config), remote(config)
    if not is_worktree(sdlc_dir):
        return "not a worktree — run `sync.py init` first (nothing published)"
    try:
        who = ledger.actor(config)
        mine = _staged_paths(path, ledger.ENTRIES, who)
        mine += [r for r in _dead_pid_paths(path, who, config) if r not in mine]      # #1932
        # #1623: the dependency map's history stream rides along. A change that is only there must
        # still push, so the early return waits for both.
        dag_mine = [str(p.relative_to(path)) for p in ledger.dag_files_for(path, who)]
        snap = ["dag/map.json"] if (path / "dag" / "map.json").is_file() else []     # #1956
        lease = _load("dag_lease").mine(path, config)                                # #1957
        if not mine and not dag_mine and not snap and not lease:
            return "nothing to publish"
        # #2574/S1-G3: THE EVENTS STREAM IS NO LONGER STAGED. It used to be
        # `to_stage += _staged_paths(path, ledger.EVENTS, who)`, which published a machine's events to
        # the ops branch whenever the old `share` setting was not explicitly false. PRD §6.2 deletes
        # publishing: the journal is local-only, at `.sdlc/events/`, which is not in this worktree at
        # all. Nothing already on the branch is removed — a teammate's clone still pulls it, and it is
        # still read for one release.
        to_stage = list(mine) + dag_mine + snap + lease
        if _ensure_gitattributes(path):
            to_stage.append(".gitattributes")
        _write_team(sdlc_dir, path)
        to_stage.append("TEAM.md")
        if _write_dag(path, config):                # #1624: only when the ledger and the map are on
            to_stage.append("DAG.md")
        git(path, ["add", *to_stage])
        if git(path, ["diff", "--cached", "--name-only"]):
            git(path, ["commit", "-m", f"ledger: {who}"])      # a new commit: pushed, as always
        elif not _stranded(git, path, rem, name):
            return "nothing to publish"
    except Exception as exc:                     # noqa: BLE001 - honour the "always returns a status
        return f"publish deferred: {exc}"        # string, never raises" contract every other branch keeps

    def regenerate_team():                      # entries union-merge; TEAM.md -> theirs, then
        _write_team(sdlc_dir, path)             # regenerate from the merged entries
        regen = ["TEAM.md"] + (["DAG.md"] if _write_dag(path, config) else [])
        if git(path, ["diff", "--name-only"]):
            git(path, ["add", *regen])
            git(path, ["commit", "--amend", "--no-edit"])

    # #2076: real git (no injected runner) pauses with jitter between attempts, so clones rejected
    # together do not retry together; an injected runner (every stub-driven test) never sleeps.
    # #2081: a runner built by `_bounded_git` carries its deadline, and a pause never outlasts it.
    deadline_at = getattr(git, "deadline_at", None)
    deadline_at = deadline_at if isinstance(deadline_at, (int, float)) else None
    pause = ((lambda attempt: _jitter_sleep(attempt, deadline_at=deadline_at))
             if (backoff if backoff is not None else run is None) else None)
    return _push_with_retry(git, path, rem, name, attempts, rebase_args=("-X", "theirs"),
                            after_rebase=regenerate_team, retry_hint="next tick", channel=LEDGER,
                            sleep=pause)


def _write_team(sdlc_dir, path):
    """Regenerate the rolled-up team view from every author's entries. A pure function of the
    entries on disk, so it is always safe to overwrite (that is what makes a race resolvable)."""
    (path / "TEAM.md").write_text(ledger.render(ledger.read_all(sdlc_dir)), encoding="utf-8")


def _write_dag(path, config):
    """#1624: regenerate DAG.md at the root of the ledger worktree `path` from every writer's dag history
    (a pure function of those files, so overwriting is always safe and a race is resolved by re-running).
    Returns True when it wrote. Nothing, and False, when `ledger.enabled` is not true, `discovery.dag.mode`
    is `off`, or the worktree holds no dag history record (no empty file is ever created). Never raises."""
    try:
        if not ledger.enabled(config):
            return False
        dag_history, dag_render = _load("dag_history"), _load("dag_render")
        if dag_history._load("dag_store").mode(config) == "off":
            return False
        recs = dag_history.read_all(path)
        snap, built_by = dag_history._load("dag_store").read_snapshot_at(path)      # #1956
        if not recs and snap is None:
            return False
        text = dag_render.render(recs) if snap is None else dag_render.render(recs, snap, built_by, time.time())
        (pathlib.Path(path) / "DAG.md").write_text(text, encoding="utf-8")
        return True
    except Exception:                            # noqa: BLE001 - DAG.md is a view; it never fails a publish
        return False


def _append_missing_lines(local, published_text):
    """Append to `local` every line of the branch's copy it lacks; returns how many. APPEND-ONLY
    (O_APPEND, never a rewrite), so a concurrent `ledger.append` to the same file is never truncated,
    and idempotent, so a re-root interrupted after this runs converges when re-run. Line order inside
    an entries file does not matter: `ledger.read_all` sorts by `(ts, actor, seq)`."""
    text = local.read_text(encoding="utf-8", errors="replace")
    have = set(text.splitlines())
    extra = list(dict.fromkeys(line for line in published_text.splitlines()
                               if line.strip() and line not in have))
    if extra:
        with open(local, "a", encoding="utf-8") as fh:
            fh.write(("" if not text or text.endswith("\n") else "\n") + "\n".join(extra) + "\n")
    return len(extra)


def _this_clones(rel, who, host):
    """Is the entries file at `rel` this clone's own write? Its `<actor>-<host>.<pid>` name
    (`ledger.entry_file`) carries THIS machine's host token, whatever actor it names -- #488 shows the
    actor can resolve differently between runs. A name with no host -- a pre-#540 `<actor>-<pid>`, or
    a pre-#337 bare `<actor>` -- is owned exactly when `ledger.files_for(who)` would claim it, and by
    the same rules, so `bo-notes.jsonl` (actor "bo-notes") is never taken for bo's."""
    stem = pathlib.PurePosixPath(rel).name[:-len(".jsonl")]
    actor, _, instance = stem.rpartition("-")
    if actor and ledger._is_instance_token(instance) and "." in instance:
        return instance.rpartition(".")[0] == host
    if not who:
        return False
    mine = ledger._safe_name(who).lower()       # `files_for`'s own two legacy shapes, and only those:
    return (actor.lower() == mine and ledger._is_instance_token(instance)) or stem == ledger._safe_name(who)


def _aside(sdlc_dir, stamp, channel, rel, unplaced=False):
    """Where a re-root puts a file it will not publish: `<sdlc>/state/orphaned/<stamp>/<channel>/`
    (`unplaced/` beneath it for a copy that exists NOWHERE else). Never clobbers an earlier copy."""
    base = pathlib.Path(sdlc_dir).resolve() / "state" / "orphaned" / stamp / channel
    dest = (base / "unplaced" if unplaced else base) / rel
    n = 0
    while dest.exists():
        n += 1
        dest = dest.with_name(f"{dest.name}.{n}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _save_uncommitted(git, sdlc_dir, path, paths, stamp, channel):
    """Before the re-root's checkout overwrites `paths` with the branch's copies: COPY aside every one
    whose local content is not what the old tip committed (an uncommitted edit, or an untracked file
    at a path the branch has) -- the backup ref cannot hold those. Returns them, for the output to
    name. TEAM.md is skipped: it is generated, and publish regenerates it from the entries."""
    saved = []
    for i in range(0, len(paths), 100):
        chunk = paths[i:i + 100]
        wanted = set(chunk)
        out = git(path, ["--literal-pathspecs", "status", "--porcelain", "-z", "--untracked-files=all",
                         "--", *chunk])
        for entry in (e for e in out.split("\0") if e):
            parts = entry.split(None, 1)        # `_run_git` strips the first entry's leading space
            rel = parts[1] if len(parts) == 2 else ""
            if rel in wanted and rel != "TEAM.md" and (path / rel).is_file():
                shutil.copy2(path / rel, _aside(sdlc_dir, stamp, channel, rel, unplaced=True))
                saved.append(rel)
    return saved


def _settle_ledger(git, sdlc_dir, path, backup, stamp, who):
    """After a ledger re-root's reset: stage what is this clone's, move out of `entries/` what is not,
    and NAME anything left over -- so no change remains that publish will not commit (a dirty tracked
    file makes every later `git rebase` refuse), and no file stays where `unpublished()` counts it,
    `read_all()` reads it, and TEAM.md renders it. IDEMPOTENT: it recomputes everything from the
    worktree, so `_resume_reroot` can run it again after a failure part-way through."""
    host, notes = ledger._host_token(ledger._config_for(sdlc_dir)), []
    loose = [p for p in git(path, ["ls-files", "--others", "-z", "--", ledger.ENTRIES]).split("\0")
             if p.endswith(".jsonl")]
    changed = [p for p in git(path, ["diff", "--name-only", "-z", "--", ledger.ENTRIES]).split("\0")
               if p.endswith(".jsonl")]
    stage = [p for p in loose + changed if _this_clones(p, who, host)]
    others = [p for p in loose if not _this_clones(p, who, host)]
    if others:
        # Another machine's file: the old branch's copy, byte for byte, is in `backup`, so moving it
        # aside loses nothing; one that differs exists only here and is named as well as moved.
        old = {}
        for row in git(path, ["ls-tree", "-r", "-z", backup, "--", ledger.ENTRIES]).split("\0"):
            meta, _, rel = row.partition("\t")
            if rel:
                old[rel] = meta.split()[-1]
        blobs = []
        for i in range(0, len(others), 100):
            blobs += git(path, ["hash-object", "--", *others[i:i + 100]]).split()
        for rel, blob in zip(others, blobs):
            kept = old.get(rel) == blob
            (path / rel).rename(_aside(sdlc_dir, stamp, LEDGER, rel, unplaced=not kept))
        notes.append(f"{len(others)} other machine's file(s) moved to "
                     f"{pathlib.Path(sdlc_dir).resolve() / 'state' / 'orphaned' / stamp / LEDGER}, "
                     "not republished")
    # Named from the directory, not from this run's moves, so a resumed settle still names a copy an
    # interrupted one moved; `_save_uncommitted`'s copies land here too.
    unplaced_dir = pathlib.Path(sdlc_dir).resolve() / "state" / "orphaned" / stamp / LEDGER / "unplaced"
    only = sorted(str(f.relative_to(unplaced_dir)) for f in unplaced_dir.rglob("*") if f.is_file())
    if only:
        notes.append(f"kept ONLY under {unplaced_dir} (no commit holds them, nothing republished them): "
                     f"{', '.join(only)}")
    for i in range(0, len(stage), 100):
        git(path, ["add", "--", *stage[i:i + 100]])
    stray = [p for p in git(path, ["diff", "--name-only", "-z"]).split("\0")
             if p and not (p.startswith(f"{ledger.ENTRIES}/") and _this_clones(p, who, host))]
    if stray:                                   # a defect if it ever prints: say it, never ignore it
        notes.append(f"tracked file(s) still differ from {backup} and publish will not stage them, so "
                     f"a pull will refuse until they are resolved: {', '.join(stray)}")
    return notes


def _pending(sdlc_dir, channel):
    """A re-root whose reset landed but whose settle step has not finished (#1142 review, finding 2):
    after the reset the histories are related, so nothing else could tell the next `bootstrap` that
    anything is left to do. `state/orphaned/<stamp>/<channel>/PENDING`, holding the OLD TIP's hash --
    so resuming needs no backup ref an operator may have tidied away (review round 2) -- and a FILE,
    not a ref, so no `update-ref -d` is ever needed to clear it (docs/branching-model.md 13b)."""
    return sorted((pathlib.Path(sdlc_dir).resolve() / "state" / "orphaned").glob(f"*/{channel}/PENDING"))


def _resume_reroot(git, sdlc_dir, ch, who):
    """Finish a ledger re-root that stopped between its reset and the end of its settle step."""
    notes = []
    for marker in _pending(sdlc_dir, ch.name):
        stamp = marker.parent.parent.name
        notes.append(f"finished the re-root started at {stamp}")
        notes += _settle_ledger(git, sdlc_dir, ch.path, marker.read_text(encoding="utf-8").strip(), stamp, who)
        marker.unlink()
    return "; " + "; ".join(notes) if notes else ""


def _reroot(git, sdlc_dir, ch, who=None):
    """#1142's lever: move a channel worktree whose branch shares NO history with `<rem>/<name>`
    onto it, keeping what this clone wrote. `""` when there is nothing to do (the histories are
    related, or the remote cannot be read -- publish then says so itself).

    Every write to the worktree happens BEFORE the one `reset`, so a crash before it leaves HEAD on
    the old branch and a re-run starts over; a ledger crash AFTER it leaves a PENDING marker (`_pending`), and the next
    `bootstrap` finishes the settle step. The old tip is kept at `refs/sigma/orphaned/<channel>/<UTC
    stamp>` (outside `refs/sigma/claims/`, never pushed). Per path the branch has:
      * ledger: an `entries/*.jsonl` that is this clone's own (`_this_clones`) and exists here is
        UNIONED (append-only) and staged; every other path -- a teammate's file, README.md, TEAM.md,
        .gitattributes, receipts -- takes the branch's copy, the orphan's committed one staying in
        the backup ref and an uncommitted one copied aside first (`_save_uncommitted`). Splitting by
        OWNER, not by "did the union change it", is what keeps a recreated (perhaps deliberately
        purged) branch from getting a teammate's lines back through this clone.
      * knowledge: a note (`*.md`) that exists here is unioned; scaffold takes the branch's copy.
        Notes carry no host, so local-only notes go out through publish's secret gate -- the
        refusal message's "republishes this clone's own notes" is the warning.
    Operator-run only (`bootstrap`): an unrelated history is also what a deleted-and-recreated
    remote branch looks like, and republishing onto that is a human's call, not a watcher tick's."""
    path, name, rem = ch.path, ch.branch, ch.remote
    # A pending settle is LOCAL work, so it never waits on the network (review round 2); and it runs
    # only once the reset it follows has landed -- a marker beside an unrelated HEAD is superseded.
    if ch.name == LEDGER and _shares_history(git, path, rem, name) is True:
        return _resume_reroot(git, sdlc_dir, ch, who)
    try:
        tracking = _fetch(git, path, rem, name)
    except Exception:                           # noqa: BLE001 - nothing visible to re-root onto
        return ""
    shared = _shares_history(git, path, rem, name)
    if shared is True and ch.name == LEDGER:
        return _resume_reroot(git, sdlc_dir, ch, who)
    if shared is not False:
        return ""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = f"refs/sigma/orphaned/{ch.name}/{stamp}"
    old_tip = git(path, ["rev-parse", "HEAD"])
    git(path, ["update-ref", backup, old_tip])
    for stale in _pending(sdlc_dir, ch.name):
        stale.unlink()                          # superseded: HEAD never left the old branch
    host = ledger._host_token(ledger._config_for(sdlc_dir))
    checkout, appended = [], 0
    for rel in [p for p in git(path, ["ls-tree", "-r", "-z", "--name-only", tracking]).split("\0") if p]:
        mine = (rel.endswith(".md") if ch.name == KNOWLEDGE
                else rel.startswith(f"{ledger.ENTRIES}/") and rel.endswith(".jsonl")
                and _this_clones(rel, who, host))
        local = path / rel
        if mine and local.is_file():
            if _append_missing_lines(local, git(path, ["show", f"{tracking}:{rel}"])):
                appended += 1
        else:
            checkout.append(rel)
    saved = _save_uncommitted(git, sdlc_dir, path, checkout, stamp, ch.name)
    for i in range(0, len(checkout), 100):      # chunked: no file-count bound on the branch
        git(path, ["--literal-pathspecs", "checkout", tracking, "--", *checkout[i:i + 100]])
    marker = None
    if ch.name == LEDGER:
        marker = _aside(sdlc_dir, stamp, ch.name, "PENDING")
        marker.write_text(old_tip + "\n", encoding="utf-8")
    git(path, ["reset", "-q", "--mixed", tracking])
    notes = [f"re-rooted {name} onto {rem}/{name} (old tip kept at {backup})"]
    if appended:
        notes.append(f"{appended} file(s) merged")
    if saved and ch.name == KNOWLEDGE:      # the ledger's settle step names every unplaced copy
        notes.append(f"uncommitted local copies replaced by the branch's, saved under "
                     f"{pathlib.Path(sdlc_dir).resolve() / 'state' / 'orphaned' / stamp}: {', '.join(saved)}")
    if marker is not None:
        notes += _settle_ledger(git, sdlc_dir, path, old_tip, stamp, who)
        marker.unlink()
    return "; " + "; ".join(notes)


def _reroot_guarded(git, sdlc_dir, ch, who=None):
    """`_reroot`, holding the channel's own lock for it -- the ledger worktree lock for the ledger (#2081),
    the knowledge lock for the knowledge channel -- released before the caller's own `publish`, which takes
    it again (an flock is per open file description, so a second acquire in this process would refuse).
    A caller that already holds the ledger lock (`publish_entry`) calls `_reroot` instead."""
    if ch.name != KNOWLEDGE:
        return _with_ledger_lock(
            sdlc_dir, "reroot", lambda: _reroot(git, sdlc_dir, ch, who),
            busy=lambda why: f"; re-root deferred: {why}")
    lock = _knowledge_lock(sdlc_dir)
    if lock is None:
        return "; re-root deferred: another knowledge sync holds the lock"
    try:
        return _reroot(git, sdlc_dir, ch, who)
    finally:
        if lock is not True:
            lock.close()


def bootstrap(sdlc_dir, config, run=None, channel=LEDGER):
    """One command to stand the ledger up for the whole team. `init` creates the ops branch LOCALLY,
    and `publish` only pushes once you own an entries file — so before this, the branch reached the
    remote only after your first goal wrote a claim, and a teammate who ran `init` saw nothing to
    fetch. bootstrap = init + seed YOUR (empty) entries file + publish, so the branch, your file, and
    the rolled-up TEAM.md all land on the remote the moment the ledger is switched on. Idempotent:
    re-running is a no-op ("already a worktree; nothing to publish"), and each teammate runs it once
    in their own clone to join.

    `channel=KNOWLEDGE` is init + publish with no seed file: the scaffold commit is ahead of an absent
    remote ref, so even a clone with no notes yet pushes the branch (#2698, P4 finding B1).

    #1142: it is also the LEVER for a worktree whose branch shares no history with the remote's
    (`_reroot`), and re-roots once more if its own publish comes back `refused` -- two first-ever
    bootstraps racing an empty remote both start a branch, and the loser's needs exactly this. Once,
    never a loop."""
    git = run or _run_git
    first = init(sdlc_dir, config, run=git, channel=channel)
    if channel == LEDGER:
        try:                                    # #2076: an explicit enrolment, so the watcher may start
            _self_created_marker(sdlc_dir).unlink()
        except OSError:
            pass
    ch = _channel(channel, sdlc_dir, config)
    who = ledger.actor(config) if channel == LEDGER else None
    rerooted = _reroot_guarded(git, sdlc_dir, ch, who)
    if channel == LEDGER:
        # the exact path a real append() would use right now — same function, so files_for() is
        # guaranteed to find it (same actor, same os.getpid() within this one process).
        mine = ledger.entry_file(sdlc_dir, who)
        mine.parent.mkdir(parents=True, exist_ok=True)
        if not mine.exists():
            mine.touch()        # an empty file is enough for publish to land your presence + the branch
    out = publish(sdlc_dir, config, run=git, channel=channel)
    if out.startswith("publish refused"):
        again = _reroot_guarded(git, sdlc_dir, ch, who)
        if again:
            rerooted += again
            out = publish(sdlc_dir, config, run=git, channel=channel)
    return f"{first}{rerooted}; {out}"


# --------------------------------------------------------------- is anyone actually publishing? (#1599)
#
# Until #1599 the ONLY automatic publish was the watcher's: `watch_daemon.py` runs `publish` once a tick,
# and `loop.py` starts that watcher on every loop trigger (`_ensure_watcher`). That covers the loop
# and nothing else. The commands `/sigma-ledger` tells a person to type -- `ledger.py append`,
# `handoff.py open`/`track`/`ack` -- wrote locally, published nothing, started nothing, and said
# nothing about it. A machine that was not concurrently looping withheld every note, hand-off and
# ack from the team for as long as that stayed true: one adopter's held 67% of its ledger history
# back for two and a half weeks with every surface reporting the feature as working.
#
# The three functions below are the shared answer, used by BOTH the write path (`publish_after_write`,
# called from those CLI verbs) and the read path (`/sigma-doctor`'s ledger row, which cross-loads
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
    `hooks/session_start.sh` keeps a residual copy of the arithmetic by design (design
    #2417 X-2: a Claude Code hook stays host-specific and unported)."""
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


def history_shared(sdlc_dir, config, run=None):
    """#1142, for `/sigma-doctor`: does this clone's ledger branch share any history with
    `<remote>/<branch>` as last fetched? `_shares_history`'s three answers, None also when there is
    no worktree. LOCAL ONLY, like `unpublished()`: it never fetches, so a clone whose tracking ref was
    never written answers None -- `fetch_refspec_covers` says why that can be."""
    git = run or _run_git
    if not is_worktree(sdlc_dir):
        return None
    try:
        return _shares_history(git, worktree(sdlc_dir), remote(config), branch(config))
    except Exception:                           # noqa: BLE001 - a diagnostic never raises at a caller
        return None


def fetch_refspec_covers(sdlc_dir, config, run=None):
    """#1142, for `/sigma-doctor`: does `remote.<remote>.fetch` map `refs/heads/<branch>`? False is
    the `--single-branch`/`--depth` clone, where only a fetch that NAMES the branch (every fetch in
    this module) ever writes `<remote>/<branch>`. Exact sources and single-`*` globs are matched;
    a negative (`^`) entry is skipped; anything else unparseable reads as covered, never as an alarm.
    None when no fetch refspec is configured at all. Reads local config only."""
    git = run or _run_git
    rem, name = remote(config), branch(config)
    try:
        specs = git(project_root(sdlc_dir), ["config", "--get-all", f"remote.{rem}.fetch"]).split()
    except Exception:                           # noqa: BLE001 - unset (exit 1): nothing to judge
        return None
    want = f"refs/heads/{name}"
    for spec in specs:
        if spec.startswith("^"):
            continue
        source = spec.lstrip("+").split(":", 1)[0]
        if source == want or source.count("*") > 1 or ("*" in source and fnmatch.fnmatchcase(want, source)):
            return True
    return False


def _jsonl_line_count(path):
    """Real entries in one pending file: non-blank JSONL lines, fail-open on read/decode trouble.

    `errors="replace"` matters for the same reason `doctor._count_jsonl_lines` documents its own
    copy: a process killed mid-append truncates a multi-byte UTF-8 sequence, and the resulting
    UnicodeDecodeError is a ValueError, not an OSError -- an `except OSError` alone would let it
    escape and cost `pending_entry_count` its WHOLE answer over one half-written file.

    DELIBERATELY DUPLICATED from doctor.py's own copy rather than shared -- the same trade-off
    `hooks/session_start.sh` takes for its own copy of the watcher staleness rule (design
    #2417 X-2, the one copy #2490 deliberately left standing): doctor.py is a different skill
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
    wizard's UNCONDITIONAL SessionStart hook takes the cheap subset, while `/sigma-doctor` -- "which
    the user typed on purpose" -- keeps the full sweep, network included. `discovery.reconcile.mode`
    draws its line at UNATTENDED writes, off by default so a typo cannot start one while nobody is
    watching. A `/sigma-ledger` write is on the typed-on-purpose side of the first line and the
    attended side of the second, and being seen by the team is the entire point of the entry.

    WHY NOT START THE WATCHER INSTEAD, which is the other repair the issue offers. It fails both of
    those lines at once. `_ensure_watcher` spawns a daemon that then fetches, runs `gh` reads
    (agent_watch, comment_watch) and posts to a webhook every 15 minutes, indefinitely, off one
    typed command -- the recurring, unattended, unconsented cost both precedents exist to prevent.
    A single push is bounded and finishes. Enrolling someone's machine in a background job is not
    something a one-line note should be able to do.

    AND WHY A LIVE WATCHER STILL WINS. The watcher is one process, and what serialises publishers is the
    ledger worktree lock (`state/ledger-sync.lock`, #2081): there is no mutex inside the watcher around its
    publish (there never was one; the decision mutex in `watch_daemon.py` only picks the tick's owner). A CLI
    publish fired behind a live watcher's back would only QUEUE behind it for no gain, or defer, so a FRESH
    heartbeat means name the tick that will carry the entry rather than race it. A stale or absent one
    means nothing else will, and this write is on its own.

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
                    "Run /sigma-ledger (`sync.py bootstrap .sdlc`) once in this clone.")
        state, age = watcher_liveness(sdlc_dir, config, now=now)
        if state == "live":
            return ("ledger: not published yet -- the ledger watcher is live (last tick %s) and "
                    "publishes once per tick, so it carries this one." % _ago(age))
        result = (publish_fn or publish)(sdlc_dir, config, run=run)
        if result == "published":
            return "ledger: published -- the team can see this entry now."
        if result.startswith("publish refused"):   # #1142: permanent, so no "will retry" tail
            return ("ledger: NOT published (%s) -- no other machine can see this entry, and no "
                    "retry will change that until the branch is re-rooted." % result)
        return ("ledger: NOT published (%s) -- no other machine can see this entry yet; a loop "
                "trigger or `sync.py publish .sdlc` will retry it." % result)
    except Exception as exc:                # noqa: BLE001 - the write already succeeded; report, never raise
        return ("ledger: NOT published (%s) -- the entry is safely on disk, but no other machine "
                "can see it yet; retry with `sync.py publish .sdlc`." % exc)


# --------------------------------------------------------------- a run's own publish, without a daemon (#2076)
#
# A Claude Code cloud container has no `.sdlc/ledger` worktree and no watcher, and may be gone minutes
# after it opens its PR. `publish_entry` is how the loop's claim, keep-alive and outcome reach the team
# without either: create the worktree from origin, publish, and report. It is bounded in wall time and
# fails open: a push that is refused or times out is PRINTED as not published and the run goes on.
# UNMEASURED live: no real cloud container has pushed the ledger branch under this code (see
# docs/cloud-sessions.md).

DEFAULT_PUBLISH_DEADLINE_SECONDS = 90.0
PUBLISH_ENTRY_ATTEMPTS = 12
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@")


def publish_deadline_seconds():
    """`SIGMA_LEDGER_PUBLISH_DEADLINE` (seconds, > 0) or the default. One deadline covers the whole step."""
    try:
        value = float(os.environ.get("SIGMA_LEDGER_PUBLISH_DEADLINE", ""))
        return value if value > 0 else DEFAULT_PUBLISH_DEADLINE_SECONDS
    except ValueError:
        return DEFAULT_PUBLISH_DEADLINE_SECONDS


def _scrub(text, cap=240):
    """One line, URL credentials removed, capped: git's stderr can carry a tokenised remote URL."""
    flat = " ".join(str(text).split())
    flat = _URL_USERINFO.sub(r"\1***@", flat)
    return flat if len(flat) <= cap else flat[:cap - 1] + "..."


def _bounded_git(deadline_at, base=None):
    """A git runner that stops at the shared deadline. `base` is an injected runner (tests)."""
    def run(cwd, args):
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("the step's wall-time limit was reached")
        return base(cwd, args) if base is not None else _run_git(cwd, args, timeout=remaining)
    run.deadline_at = deadline_at               # #2081: `publish` caps its retry pauses at what is left
    return run


def _exclude_from_status(sdlc_dir, git):
    """Keep `.sdlc/ledger` out of the code checkout's `git status` when the repository does not ignore
    it, through `info/exclude` (never a tracked file). Best effort."""
    root = project_root(sdlc_dir)
    try:
        rel = worktree(sdlc_dir).relative_to(root.resolve()).as_posix()
        try:
            git(root, ["check-ignore", "-q", rel + "/"])
            return
        except Exception:                       # noqa: BLE001 - not ignored: add it
            pass
        exclude = pathlib.Path(git(root, ["rev-parse", "--git-path", "info/exclude"]))
        exclude = exclude if exclude.is_absolute() else root / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        have = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        line = f"/{rel}/"
        if line not in have.splitlines():
            exclude.write_text(have + ("" if have.endswith("\n") or not have else "\n") + line + "\n",
                               encoding="utf-8")
    except Exception:                           # noqa: BLE001 - never what stops a publish
        pass


def publish_entry(sdlc_dir, config, what, run=None, publish_fn=None, deadline_seconds=None, now=None):
    """Publish this run's pending ledger entries and return the one stderr line to print, or None when
    there is nothing to say. NEVER raises. `what` names the entry for the message ("claim of #7").

    None (nothing published, nothing to print) when the ledger is off, when `ledger.publish_on_write` is
    false (the operator keeps the choice and batches to the watcher), and, on a LAPTOP only, when a live
    watcher will carry it (two publishers would contend for the worktree's index lock). A cloud session
    never defers: the container may be gone before the next tick, and the loop never starts a watcher
    there. When `.sdlc/ledger` is not a worktree it is created from origin through `init`, never touching
    the code checkout's HEAD, index or working tree. The whole step shares one wall-time deadline."""
    try:
        if not ledger.enabled(config) or not publish_on_write(config):
            return None
        if not ledger.cloud_session() and is_worktree(sdlc_dir):
            if watcher_liveness(sdlc_dir, config, now=now)[0] == "live":
                return None
        limit = deadline_seconds if deadline_seconds else publish_deadline_seconds()
        deadline_at = time.monotonic() + limit
        git = _bounded_git(deadline_at, run)
        # #2081: ONE publisher at a time in this checkout. The wait shares the step's deadline; the stale-rebase
        # abort, the worktree creation, the re-root and the publish all run only while the lock is held, so a
        # live rebase of another process is never aborted. `_abort_stale_rebase` keeps its own small budgets
        # (it ran on them before): after a long wait the step can end up to ~45 s (three 15 s budgets) plus up
        # to 7 s of kill grace per git call that times out (about 73 s counting the three and the one running at
        # the deadline) past its limit, worst case. Arithmetic, not a measurement: docs/cloud-sessions.md.
        # An OSError from the acquire (no lock file, a symlink at it, or `LedgerLockRefused`) falls to the handler
        # below: it is reported as NOT published, never raised, and no git has run.
        lock = _ledger_lock(sdlc_dir, wait_until=deadline_at, verb="publish_entry")
        if lock is None:
            holder = _ledger_lock_holder(sdlc_dir)
            return _not_published(what, "another publisher holds the ledger worktree: " + holder if holder
                                  else LOCK_RELEASED_WHILE_CHECKING, limit)
        try:
            pub = publish_fn or _publish_ledger
            if is_worktree(sdlc_dir) and run is None and _abort_stale_rebase(sdlc_dir):
                return _not_published(what, STUCK_REBASE, limit)       # never publish from the middle of a rebase
            if not is_worktree(sdlc_dir):
                init(sdlc_dir, config, run=git)
                _mark_self_created(sdlc_dir)
                _exclude_from_status(sdlc_dir, git)
                _reroot(git, sdlc_dir, _channel(LEDGER, sdlc_dir, config), ledger.actor(config))
            result = pub(sdlc_dir, config, run=git, attempts=PUBLISH_ENTRY_ATTEMPTS, backoff=True) \
                if publish_fn is None else pub(sdlc_dir, config, run=git)
            if result.startswith("publish refused") and publish_fn is None:      # a first-ever race: once
                _reroot(git, sdlc_dir, _channel(LEDGER, sdlc_dir, config), ledger.actor(config))
                result = pub(sdlc_dir, config, run=git, attempts=PUBLISH_ENTRY_ATTEMPTS, backoff=True)
        finally:
            _release_ledger_lock(lock)
        if result in ("published", "nothing to publish"):
            return f"ledger: published ({what}) -- the team can see it now." if result == "published" else None
        return _not_published(what, result, limit)
    except Exception as exc:                    # noqa: BLE001 - the entry is on disk; report, never raise
        return _not_published(what, exc, deadline_seconds or publish_deadline_seconds())


SELF_CREATED_REL = pathlib.Path("state") / "ledger-self-created"


def _self_created_marker(sdlc_dir):
    return pathlib.Path(sdlc_dir) / SELF_CREATED_REL


def self_created(sdlc_dir):
    """True when THIS loop made the ledger worktree for itself (`publish_entry`), rather than an operator
    enrolling the clone with `bootstrap`. `_ensure_watcher` starts no daemon for it: creating a worktree
    to publish a claim must not enrol the machine in a recurring background job."""
    return _self_created_marker(sdlc_dir).exists()


def _mark_self_created(sdlc_dir):
    """Best effort. #708: through `safe_state_open`, so a symlink planted at the marker (or at `state/`) is never
    written through; a refusal is an `OSError` and the marker is simply not written."""
    try:
        with state.safe_state_open(sdlc_dir, SELF_CREATED_REL, "w") as marker:
            marker.write("created by the loop's own publish (#2076); `sync.py bootstrap` clears this\n")
    except OSError:
        pass


#: `publish_entry`'s reason when `_abort_stale_rebase` could not clear a rebase; the lever is in the docs
STUCK_REBASE = ("a rebase is still in progress in the ledger worktree and `git rebase --abort` could not clear it "
                "(docs/cloud-sessions.md, residual (h))")


def _abort_stale_rebase(sdlc_dir):
    """A git killed at the step's limit can leave the ledger worktree mid-rebase, which would defer every
    later publish. Abort it first, on its own small budget, never on the exhausted one. Best effort, never
    raises. Returns True when a rebase is STILL in progress after the attempt (`git rebase --abort` can refuse,
    for one: an untracked file it would overwrite), so the caller does not publish from the middle of it; False
    when there was none, when it was cleared, and when this could not tell."""
    try:
        path = worktree(sdlc_dir)
        for name in ("rebase-merge", "rebase-apply"):
            marker = pathlib.Path(_run_git(path, ["rev-parse", "--git-path", name], timeout=15))
            marker = marker if marker.is_absolute() else path / marker
            if marker.exists():
                try:
                    _run_git(path, ["rebase", "--abort"], timeout=15)
                except Exception:               # noqa: BLE001 - whether it worked is read off the marker below
                    pass
                return marker.exists()
    except Exception:                           # noqa: BLE001 - the publish that follows reports what it can
        pass
    return False


def _not_published(what, why, limit):
    """The one stderr line for an entry that did not reach origin. A lock REFUSAL, an unusable lock file (a
    symlink) and a STUCK rebase fail the same way on every later step until someone acts on the reason, which
    already names the lever, so for those the tail says the entry will publish once that is cleared; every other
    reason is transient and keeps the line that says the next loop step retries."""
    waits_for_a_person = isinstance(why, (LedgerLockRefused, state.UnsafeStatePath)) or why == STUCK_REBASE
    tail = ("will publish once that is cleared" if waits_for_a_person else "the next loop step retries it")
    return (f"ledger: NOT published ({_scrub(why)}) -- no other machine can see this {what} yet. "
            f"The entry is safely on disk and {tail} "
            f"(this step is limited to {limit:.0f}s of wall time).")


def remote_expired(sdlc_dir, config, ttl_seconds, now=None, run=None, deadline_seconds=None):
    """`(expired, run_ids, why)`: the claims a FRESH read of the ledger branch on `<remote>` shows held
    and expired, as `ledger.expired_claims` / `expired_claim_run_ids` return them (so a caller releases
    the claim origin shows, not the stale local one), or `(None, None, why)` when the read failed.
    #2076, AC-6: a claim written on another machine looks stale here whenever this ledger copy is old,
    so releasing it needs the remote's own word.

    Read-only: it fetches the branch into its remote-tracking ref and reads the entries FROM that ref
    (`git grep`), so it never rebases, never moves the ledger worktree, and never contends with a
    watcher's index lock. Every git call goes through the bounded runner. A failed or timed-out fetch, a
    missing branch or an unreadable ref is a failure, and a failure releases nothing."""
    try:
        if not ledger.enabled(config):
            return None, None, "the ledger is off"
        limit = deadline_seconds if deadline_seconds else publish_deadline_seconds()
        git = _bounded_git(time.monotonic() + limit, run)
        root, rem, name = project_root(sdlc_dir), remote(config), branch(config)
        tracking = _fetch(git, root, rem, name)
        text = git(root, ["grep", "-h", "-I", "-e", ".", tracking, "--", f"{ledger.ENTRIES}/"])
        entries = ledger.entries_from_text(text)
        return (ledger.expired_claims(entries, now=now, ttl_seconds=ttl_seconds),
                ledger.expired_claim_run_ids(entries, now=now, ttl_seconds=ttl_seconds), "")
    except Exception as exc:                    # noqa: BLE001 - cannot confirm: do not release
        return None, None, f"could not read {remote_name_for_message(config)}: {_scrub(exc)}"


def remote_name_for_message(config):
    try:
        return f"{remote(config)}/{branch(config)}"
    except Exception:                           # noqa: BLE001
        return "the ledger branch"


def confirm_expired(sdlc_dir, config, goal, ttl_seconds, now=None, run=None, deadline_seconds=None):
    """`(True, "")` only when a fresh read of origin's ledger branch still shows `goal` held and expired;
    otherwise `(False, why)`. See `remote_expired`."""
    expired, _runs, why = remote_expired(sdlc_dir, config, ttl_seconds, now=now, run=run,
                                         deadline_seconds=deadline_seconds)
    if expired is None:
        return False, why
    if str(goal) in expired:
        return True, ""
    return False, f"{remote_name_for_message(config)} shows #{goal} renewed, finished or released"


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
                      '"sync": {"enabled": true}}) -- set both in .sdlc/config.json, then re-run '
                      f'`sync.py {argv[1]} {sdlc_dir} --channel knowledge`', file=sys.stderr)
                return 1
        elif not ledger.enabled(config):
            print('ledger is off (config: "ledger": {"enabled": true}) -- run `/sigma-init --ledger yes` '
                  f'(github mode) or set that key in .sdlc/config.json, then re-run `sync.py {argv[1]} '
                  f'{sdlc_dir}`', file=sys.stderr)
            return 1
        try:
            print(_VERBS[argv[1]](sdlc_dir, config, channel=channel))
        except Exception as exc:                # noqa: BLE001 - report, never traceback at a user
            print(f"sync: {exc}", file=sys.stderr)
            return 1
        return 0
    print(USAGE, file=sys.stderr)
    return 2


def _symlink_guard(argv):
    """#708: refuse a committed symlink under .sdlc/state or .sdlc/journey before any write."""
    import importlib.util as _u
    import pathlib as _p
    spec = _u.spec_from_file_location("_guard_state", _p.Path(__file__).resolve().parent / "state.py")
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.guard_argv(argv, _p.Path(__file__).name)


if __name__ == "__main__":
    sys.exit(_symlink_guard(sys.argv) or main(sys.argv))

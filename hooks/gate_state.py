"""Fail-open, session-scoped policy output for the Claude UserPromptSubmit gate.

The full policy is marked delivered only AFTER it is written to stdout. A crash
or timeout before that write leaves no marker, so the next prompt retries it.
Sixty-four fixed lock stripes serialize same-session calls without accumulating
per-session lock files. Markers contain no prompt or raw session identifier.
"""

from contextlib import contextmanager, ExitStack
import errno
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # no safe local lock on this host: keep the full policy
    fcntl = None


RETENTION_SECONDS = 30 * 24 * 60 * 60
PRUNE_INTERVAL_SECONDS = 24 * 60 * 60
REMINDER = "GOAL-BASED SDLC still applies to non-trivial work; continue the current phase and its gates."
LOCK_STRIPES = 64
LOCK_WAIT_SECONDS = 2
LEASE_STALE_SECONDS = 10


def notification_only(prompt):
    """Recognize one or more standalone Claude task-notification XML blocks."""
    if not isinstance(prompt, str):
        return False
    remaining = prompt.strip()
    if not remaining:
        return False
    opening = "<task-notification"
    closing = "</task-notification>"
    while remaining:
        if not remaining.startswith(opening):
            return False
        opening_end = remaining.find(">")
        if opening_end < 0:
            return False
        suffix = remaining[len(opening):opening_end]
        if suffix and not suffix[0].isspace():
            return False
        close_at = remaining.find(closing, opening_end + 1)
        if close_at < 0:
            return False
        remaining = remaining[close_at + len(closing):].strip()
    return True


def _open_child(parent_fd, name, create=False):
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    return os.open(name, flags, dir_fd=parent_fd)


@contextmanager
def _store(project_dir):
    """Open each state ancestor with O_NOFOLLOW; operations stay relative to its fd."""
    if fcntl is None or not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
        raise OSError(errno.ENOTSUP, "safe gate state is unsupported on this host")
    with ExitStack() as stack:
        project_fd = os.open(project_dir, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, project_fd)
        sdlc_fd = _open_child(project_fd, ".sdlc")
        stack.callback(os.close, sdlc_fd)
        state_fd = _open_child(sdlc_fd, "state", create=True)
        stack.callback(os.close, state_fd)
        gate_fd = _open_child(state_fd, "gate-sessions", create=True)
        stack.callback(os.close, gate_fd)
        yield gate_fd


def _regular_or_none(directory_fd, name):
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise OSError(errno.ELOOP, "unsafe gate state entry")
    return info


def _open_regular(directory_fd, name):
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(errno.EINVAL, "gate state entry is not regular")
    return fd


@contextmanager
def _stripe_lock(directory_fd, name):
    """Acquire a short-lived directory lease without a first-file creation race.

    `mkdir` is atomic even when the state directory has just been created. A
    dead hook can leave a lease behind, so wait only briefly and then fail open
    with the standing policy rather than blocking a user prompt indefinitely.
    """
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            os.mkdir(name, mode=0o700, dir_fd=directory_fd)
            break
        except FileExistsError:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise OSError(errno.ELOOP, "unsafe gate lock entry")
            # A killed hook cannot run its finally block. Reclaim only a lease
            # that is far older than a normal synchronous hook invocation.
            if time.time() - info.st_mtime > LEASE_STALE_SECONDS:
                try:
                    os.rmdir(name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("gate lock lease did not clear")
            time.sleep(0.01)
    try:
        yield
    finally:
        try:
            os.rmdir(name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _prune(directory_fd, now):
    """One bounded-retention pass a day; a corrupt stamp disables deduplication."""
    stamp = _regular_or_none(directory_fd, ".last-prune")
    if stamp and now - stamp.st_mtime < PRUNE_INTERVAL_SECONDS:
        return
    lock_fd = _open_regular(directory_fd, ".prune-lock")
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # another prompt is already pruning
        stamp = _regular_or_none(directory_fd, ".last-prune")
        if stamp and now - stamp.st_mtime < PRUNE_INTERVAL_SECONDS:
            return
        cutoff = now - RETENTION_SECONDS
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if not entry.name.endswith(".mark"):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                        os.unlink(entry.name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass  # a concurrent prune already removed it
        stamp_fd = _open_regular(directory_fd, ".last-prune")
        try:
            os.utime(stamp_fd, None)
        finally:
            os.close(stamp_fd)
    finally:
        os.close(lock_fd)


def emit_policy(payload, full_context, project_dir, emit):
    """Emit exactly one context; failed delivery cannot consume a first-policy marker.

    `emit` must return only after its output is written. A host can still discard
    a successful hook result for reasons outside this process; no host ack exists.
    """
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if notification_only(prompt):
        emit("")
        return
    session = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session, str) or not session or len(session) > 512:
        emit(full_context)
        return
    digest = hashlib.sha256(session.encode("utf-8")).hexdigest()
    marker = digest + ".mark"
    stripe = f".lock-{int(digest[:2], 16) % LOCK_STRIPES:02d}"
    attempted = False
    try:
        with _store(project_dir) as directory_fd:
            with _stripe_lock(directory_fd, stripe):
                _prune(directory_fd, time.time())
                previous = _regular_or_none(directory_fd, marker)
                context = REMINDER if previous else full_context
                attempted = True
                emit(context)
                if previous:
                    try:
                        os.utime(marker, None, dir_fd=directory_fd, follow_symlinks=False)
                    except OSError:
                        pass  # expiry causes a safe full-policy retry
                else:
                    try:
                        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                        os.close(os.open(marker, flags, 0o600, dir_fd=directory_fd))
                    except OSError:
                        pass  # no marker means the next prompt retries the full policy
    except Exception:
        if attempted:
            raise  # output failed: leave marker absent and do not double-emit
        emit(full_context)  # unsafe/unavailable store: never lose the standing policy


def _emit_json(context):
    envelope = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
    sys.stdout.flush()


ADOPTION_MARKER = os.path.join(".sdlc", "config.json")


def adopted_root(start=None):
    """The ONE definition of "adopted" for the deny/block hooks (#2737): the nearest directory, from
    `start` upward, holding `.sdlc/config.json` — or None.

    `start` defaults to `$CLAUDE_PROJECT_DIR`, then the process cwd, byte-for-byte the
    `${CLAUDE_PROJECT_DIR:-$PWD}` every shell hook already resolves.

    The spec words it as "<git toplevel>/.sdlc/config.json, falling back to the nearest ancestor".
    This walk is that definition without the `git rev-parse` spawn: the toplevel of a repository is
    always an ancestor of (or equal to) a start directory inside it, so an ancestor walk visits the
    toplevel too, and a marker at the toplevel is found either way. As a boolean the two agree on
    every input; the only difference would be WHICH adopted directory is returned when a nested one
    also carries a marker, and no caller reads the path — the hooks only ask "adopted or not". What
    the walk saves is a subprocess on every Bash / Edit / Stop hook call, on every host.

    Never raises: any filesystem surprise (a symlink loop, an unreadable component) reads as
    "not adopted", which for every caller means allow — the same fail-open direction as `_py.sh`.
    """
    try:
        base = start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        here = os.path.realpath(base)
        while True:
            if os.path.isfile(os.path.join(here, ADOPTION_MARKER)):
                return Path(here)
            parent = os.path.dirname(here)
            if parent == here:
                return None
            here = parent
    except Exception:
        return None


def _adopted_cli(argv):
    """`gate_state.py --adopted [dir]` prints exactly `adopted` iff adopted, else nothing; exit 0.

    Shell contract: adopted iff stdout == `adopted`. `_py.sh` exits 0 with EMPTY stdout when the
    interpreter is broken, so an exit-code protocol would read a broken python as adopted; the
    stdout literal reads it as allow.
    """
    if adopted_root(argv[2] if len(argv) > 2 else None) is not None:
        sys.stdout.write("adopted\n")
        sys.stdout.flush()
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--adopted":
        sys.exit(_adopted_cli(sys.argv))     # before the stdin/policy path: never reads stdin
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    full_context = sys.argv[1] if len(sys.argv) > 1 else ""
    emit_policy(payload, full_context, os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(), _emit_json)


if __name__ == "__main__":
    main()

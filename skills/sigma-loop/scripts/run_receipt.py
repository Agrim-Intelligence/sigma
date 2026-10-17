#!/usr/bin/env python3
"""Running/final call receipts and the kernel lock behind them (#1650, extracted from #1551's reviewer).

A long call (a reviewer run, `loop.py verify`, `work.py merge`) writes ONE small JSON receipt BEFORE its
child work starts -- `{"outcome": "running", pid, started_at, timeout_seconds, run_id}` -- and replaces it
with ONE nonce-guarded final write. A caller whose host yielded the call (Codex `exec_command` returns
after 30.2 s) reads the receipt instead of re-running the call.

ONE classifier (`classify`) decides what a reader sees: a `running` receipt stays running only while its
pid is alive AND before the DERIVED deadline `started_at + timeout_seconds + grace` (never stored, so never
NaN, inf or forged larger); a dead pid, a past deadline or a malformed receipt reads
`failed/not_completed`, keeping every other field. Any other outcome is returned as written.

`try_lock` is the second half: a non-blocking `flock` held for the call's whole life, so a SECOND call for
the same goal can be refused by the kernel -- a reused pid can never hold a goal refused, because the
kernel drops the lock on any death. Where `fcntl` is missing or the lock file cannot be opened it returns
`UNAVAILABLE`, and the caller must then NOT refuse: no lock, no refusal (a receipt alone cannot tell a live
holder from a reused pid, and off POSIX `pid_alive` cannot probe at all). That is a weaker guarantee on
those hosts and the caller says so loudly; this module never pretends otherwise.

Pure stdlib; imported by `reviewer.py`, `loop.py` and `work.py` through one module-level binding each.
"""
import json
import os
import pathlib
import shlex
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:                      # Windows: no kernel lock here
    fcntl = None

MAX_PID = 2 ** 31 - 1                    # os.kill raises OverflowError (not OSError) above this
CLOCK_SKEW = 60
MAX_RUN_SECONDS = 86400
#: Below the one measured Codex `exec_command` yield (30.2 s), so a wait call is not itself yielded.
WAIT_DEFAULT_SECONDS = 20
MISSING_RETRY = 5                        # a wait issued at launch may arrive before the running write
#: Grace added to a verify / merge receipt's own `timeout_seconds` (the reviewer passes its own).
CALL_GRACE = 60


class _Unavailable:
    def __repr__(self):
        return "run_receipt.UNAVAILABLE"


#: `try_lock` result meaning "the mechanism could not be used here" -- distinct from `None` ("held").
UNAVAILABLE = _Unavailable()


def pid_alive(pid):
    """Whether `pid` exists on this machine. Off POSIX nothing is probed (the deadline bounds it). Fails
    toward ALIVE on anything but "no such process": the derived deadline is the backstop. An unreaped
    zombie runner reads alive until that deadline."""
    if os.name != "posix":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def receipt_path(sdlc_dir, goal, family, tag):
    """`<sdlc>/state/<family>/<goal>.<tag>.json`, resolved. The goal is validated first: an unsafe one
    raises ValueError and nothing is ever joined into a path."""
    p = pathlib.Path(str(goal))
    stem = p.stem if p.suffix == ".md" else str(goal)
    import importlib.util
    spec = importlib.util.spec_from_file_location("state", pathlib.Path(__file__).resolve().with_name("state.py"))
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    bad = state.unsafe_goal_reason(stem)
    if bad:
        raise ValueError("unsafe goal %r for a call receipt: %s" % (goal, bad))
    return (pathlib.Path(sdlc_dir).resolve() / "state" / family / ("%s.%s.json" % (stem, tag)))


def write(path, **fields):
    """Atomically replace the receipt at `path` (mkstemp in the same dir, fsync, `os.replace`). ONE file per
    goal+kind: the LATEST call's outcome is the only one that counts. Bounded by construction (no log)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    fd, tmp = tempfile.mkstemp(prefix=".run-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(fields, handle, sort_keys=True); handle.write("\n"); handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def classify(got, grace, now=None, alive=pid_alive):
    """A `running` receipt stays running only while its runner is provably live; otherwise it reads
    `failed/not_completed`, KEEPING every other field, so every existing fail-closed path applies
    unchanged. `alive` is injected so a caller with its own platform gate (the reviewer's non-POSIX
    reader) keeps it. Any other outcome is returned as written."""
    if got.get("outcome") != "running":
        return got
    now = time.time() if now is None else now
    pid, started, span = got.get("pid"), got.get("started_at"), got.get("timeout_seconds")
    if not (_int(pid) and 0 < pid <= MAX_PID and _int(started) and _int(span) and span <= MAX_RUN_SECONDS
            and started <= now + CLOCK_SKEW):
        detail = "malformed running receipt"
    elif not now < started + span + grace:
        detail = "deadline passed"
    elif not alive(pid):
        detail = "runner pid %d gone" % pid
    else:
        return got
    return {**got, "outcome": "failed", "failure": "not_completed", "detail": detail}


def read(path, grace, now=None, alive=pid_alive):
    """-> ("missing", None) | ("unreadable", why) | ("ok", dict) -- `ok` meaning a readable object,
    whatever its outcome. The ONE door every reader uses, so a dead or expired `running` receipt reads
    `failed/not_completed` everywhere."""
    try:
        got = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing", None
    except (OSError, ValueError) as exc:
        return "unreadable", str(exc)
    if not isinstance(got, dict):
        return "unreadable", "not a JSON object"
    return "ok", classify(got, grace, now=now, alive=alive)


def finish(path, run_id, label, **fields):
    """Write a call's final receipt ONLY if the on-disk receipt is still this run's (`run_id`): a cleared
    receipt (`work.py finish`) is not resurrected, and a newer run's is not clobbered. Reads the RAW file,
    not `classify`. -> whether it wrote. `label` only words the stderr text. The check-then-replace window
    is one atomic rename, unlocked."""
    path = pathlib.Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("%s: the receipt was cleared while the call ran; this outcome is not recorded" % label,
              file=sys.stderr)
        return False
    except (OSError, ValueError):
        raw = None
    if not isinstance(raw, dict) or raw.get("run_id") != run_id:
        print("%s: a different run (%s) is on record; this outcome is not recorded"
              % (label, raw.get("run_id") if isinstance(raw, dict) else "unreadable"), file=sys.stderr)
        return False
    write(path, run_id=run_id, **fields)
    return True


def poll(path, max_seconds, grace, run_id=None, sleep=time.sleep, clock=time.monotonic, alive=pid_alive):
    """Poll the receipt once a second for at most `max_seconds` -> (state, got) with state one of
    `final` (a call that COMPLETED and returned, whatever its result), `failed` (not completed: dead pid,
    past deadline, malformed, runner raised), `running` (still live at the bound), `missing` (no receipt
    after a short retry), `unreadable`, `other_run` (a receipt of a run that is not `run_id`)."""
    start = clock()
    while True:
        state, got = read(path, grace, alive=alive)
        elapsed = clock() - start
        if state == "unreadable":
            return "unreadable", got
        if state == "missing":
            if elapsed >= min(MISSING_RETRY, max_seconds):
                return "missing", None
        elif run_id and got.get("run_id") != run_id:
            return "other_run", got
        elif got.get("outcome") == "ok":
            return "final", got
        elif got.get("outcome") != "running":
            return "failed", got
        elif elapsed >= max_seconds:
            return "running", got
        sleep(1)


def try_lock(path):
    """The open fd THIS process holds an exclusive non-blocking `flock` on, iff it is the winner right
    now; `None` if another open file description holds it; `UNAVAILABLE` when the mechanism cannot be
    used (no `fcntl`, an unopenable file, an `flock` error other than "held", or a lock file that kept
    being unlinked under us). The fd is non-inheritable, so a verify's child never holds the lock past
    the verify itself. A lock won on an inode unlinked between `open` and `flock` is no lock: the fd must
    still be the inode AT the path, else it is closed and the attempt retried (bounded). Exhausted retries
    read as UNAVAILABLE -- never as a win."""
    if fcntl is None:
        return UNAVAILABLE
    path = pathlib.Path(path)
    for _attempt in range(3):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
            os.set_inheritable(fd, False)
        except OSError:
            return UNAVAILABLE
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        except OSError:
            os.close(fd)
            return UNAVAILABLE
        try:
            held, here = os.fstat(fd), os.lstat(str(path))
            same = (held.st_dev, held.st_ino) == (here.st_dev, here.st_ino)
        except OSError:
            same = False
        if same:
            return fd
        os.close(fd)
    return UNAVAILABLE


def wait_call_gesture(sdlc_dir, goal, kind, run_id=None):
    """The exact `loop.py wait-call` line a yielded caller runs: the ABSOLUTE `loop.py` and the RESOLVED
    sdlc dir (not what the caller typed), so it works from any cwd and reads the files the writer wrote."""
    loop_py = pathlib.Path(__file__).resolve().with_name("loop.py")
    line = "python3 %s wait-call %s %s %s --max-seconds %d" % (
        shlex.quote(str(loop_py)), shlex.quote(str(pathlib.Path(sdlc_dir).resolve())), goal, kind,
        WAIT_DEFAULT_SECONDS)
    return line + (" --run-id %s" % run_id if run_id else "")


def clear(paths):
    """Remove each path, tolerant of missing ones and of OS errors."""
    for p in paths:
        try:
            pathlib.Path(p).unlink(missing_ok=True)
        except OSError:
            pass

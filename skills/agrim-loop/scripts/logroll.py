"""Size-capped rotation for Sigma's own append-only text logs (#460, B6).

`slack-commands.log` and `supervisor.log` are appended to for as long as their daemon lives, so
without this they grow without bound. `append(path, text)` is a drop-in for `open(path, "a")` +
`write`: it first rolls the file to `<path>.1` ... `<path>.<generations>` when it is at or over its
cap, then appends. It is the same idea as `watch_daemon.rotate_log` (#2499), generalised to N
predecessors and made safe for several writers.

THE GUARANTEES, each with the test that fails without it (`tests/test_log_rotation.py`):
  * The current line is never lost. Rotation happens BEFORE the append, so the line lands in the
    fresh file; a writer that opened the old inode before the rename writes into `.1`; a writer
    that opens after it creates the new file. No path leaves the writer logging nowhere.
  * Atomic per step: every move is one `os.replace` inside one directory. A crash between steps
    leaves the live file in place (the next write rotates again) or already moved (the next write
    recreates it). The only thing a crash can cost is the OLDEST generation, evicted a round early.
  * One rotator at a time, in the ordinary case: a `<path>.rotating` lock taken with O_CREAT|O_EXCL. A rotator that finds
    it held does not wait; it appends and leaves rotation to the holder. A lock older than
    `LOCK_STALE_SECONDS` was left by a crashed rotator and is taken over, so there is no state a
    human has to clear. The size is checked again under the lock, so a rival that already rotated
    is not rotated a second time. Two rotators racing to take over the SAME stale lock can still
    overlap once; the re-check and the atomic moves bound that to evicting the oldest generation early.
  * `rotate` never raises (a log we cannot roll must not stop the daemon logging); a failed roll is
    retried on the next write. On Windows `os.replace` fails while another process holds the file
    open, so rotation there can keep failing silently: that platform is a documented ceiling, not
    a guarantee (docs/launch/b6-slack-supervisor-logs.md).
  * The cap is derived from the machine (`cap_bytes`): never more than ~1% of the state volume's
    free space, floored at 64 KiB, at most `DEFAULT_CAP_BYTES`. Disk used per log is bounded by
    (generations + 1) x cap, plus what one write adds after the check.

This module never deletes a log and never touches a file it was not handed; it does not know
about the stdout/stderr file a host service manager writes for the listener.
"""
import os
import pathlib
import shutil
import time

DEFAULT_CAP_BYTES = 1048576
MIN_CAP_BYTES = 65536
GENERATIONS = 3
LOCK_STALE_SECONDS = 60


def cap_bytes(state_dir):
    """`min(DEFAULT_CAP_BYTES, max(free_disk // 100, MIN_CAP_BYTES))`; a failing `disk_usage`
    (vanished mount) falls back to the default, never to 'no cap'."""
    try:
        free = shutil.disk_usage(str(state_dir)).free
    except OSError:
        return DEFAULT_CAP_BYTES
    return min(DEFAULT_CAP_BYTES, max(free // 100, MIN_CAP_BYTES))


def _size(path):
    return os.stat(path).st_size


def _move(src, dst):
    os.replace(src, dst)


def _acquire(lock):
    """True iff this caller now holds `lock`. A live lock is not ours; a stale one is taken over."""
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return True
    except FileExistsError:
        pass
    except OSError:
        return False
    try:
        if time.time() - os.stat(lock).st_mtime < LOCK_STALE_SECONDS:
            return False
        os.remove(lock)
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return True
    except OSError:
        return False


def rotate(path, cap=None, generations=GENERATIONS):
    """Roll `path` to `.1` (shifting `.1`->`.2` ... oldest evicted) when it is at or over `cap`.
    True iff this call rolled it. Never raises."""
    try:
        path = pathlib.Path(path)
        cap = cap if cap else cap_bytes(path.parent)
        if _size(path) < cap:
            return False
        lock = path.with_name(path.name + ".rotating")
        if not _acquire(lock):
            return False
        try:
            if _size(path) < cap:
                return False
            for n in range(generations, 0, -1):
                src = path if n == 1 else path.with_name("%s.%d" % (path.name, n - 1))
                if src.exists():
                    _move(src, path.with_name("%s.%d" % (path.name, n)))
            return True
        finally:
            try:
                os.remove(lock)
            except OSError:
                pass
    except Exception:            # noqa: BLE001 - a log we cannot roll must never stop its writer
        return False


def append(path, text, cap=None, generations=GENERATIONS):
    """Rotate if due, then append `text`. An OSError from the append itself propagates, exactly as
    the bare `open(path, "a")` it replaces."""
    rotate(path, cap, generations)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)

"""Provenance for the registry files Sigma writes in the ROOT checkout (#954).

THE PROBLEM. Every pick's registry sync rewrites `.sdlc/features/units/<unit>.json` (a shard) and
`.sdlc/features/<unit>.md` (a page) in the root checkout, and once a human has committed the registry
(branching-model §14) those writes are tracked, uncommitted edits on the base branch -- exactly what
`work._dirty_root_refusal` refuses a start over. Nothing commits the registry (§15), so the next pick
refused on Sigma's own write. A blanket exemption would hide a human's edit to the same files, which
§12 invites; this module is what lets the guard tell the two apart.

THE RECORD. For each shard and page this module keeps a CHAIN: an ordered list of git blob ids
(`blob_id`), one per content state, in `<sdlc>/state/features/provenance/<units/x.json|x.md>.chain.json`
-- gitignored state, never a tracked file. Each consecutive pair is one Sigma write: the bytes it
replaced, then the bytes it wrote. A write whose pre-image P is in the chain truncates the chain after
P's LAST occurrence and appends its post-image W; a P the chain does not hold (a human's bytes, a lost
record) resets it to `[P, W]`; an absent target starts it at `[W]`.

THE INVARIANT `proven` ENFORCES. A file's current bytes D are exempt only when D is a post-image in the
chain AND the committed bytes H (git's HEAD blob id) occur EARLIER in that same chain. So D is reachable
from H by a run of Sigma's own writes with no foreign bytes on disk between them. Order, not a set:
with a set, a human's hand-revert to an older Sigma state followed by a Sigma write would vouch for a
HEAD the revert silently undid (tests/test_dirty_root_registry.py, the hand-revert test). One
consequence is stated rather than hidden: a human action whose result is byte-identical to a state
Sigma itself produced cannot be told apart from Sigma -- and adds no human-authored bytes.

THE TWO ORDERS THAT MAKE IT HOLD UNDER CONCURRENCY (AC-4):
  - the WRITER records before it replaces: `recorded` is a context manager whose body is the caller's
    own `os.replace`, run under this file's lock after the record is stored. A checker that sees the
    new bytes therefore always finds them in the chain it reads afterwards;
  - the READER reads the bytes before the chain: `proven` reads D first, then the record. Old bytes are
    still in the chain (a write only truncates AFTER its own pre-image, which is the disk's bytes), and
    new bytes were recorded before they landed.
Lock order is always the unit lock (`feature_sync.amend`), then this file's lock -- never the reverse.

FAILURE MODES. Fail OPEN for the write, fail CLOSED for trust. Nothing here may ever fail a registry
write: a record that cannot be kept costs one stderr line naming it, and that file then refuses the next
start until a human commits it (the refusal prints the gesture). With no `fcntl` (Windows), a filesystem
that cannot flock, or a holder wedged past `LOCK_TIMEOUT`, the record's read-modify-write runs
unserialised and can lose an entry -- the cost is a SPURIOUS refusal, never a hidden edit, because every
entry is a post-image Sigma wrote or a pre-image read from disk. A crash between the record and the
replace leaves a recorded post-image that never landed; the disk still holds the pre-image, which is
still proven, and the next write truncates back to it.

LIMITS. The anchor comparison is against git's blob id of the WORKING-TREE bytes, so `core.autocrlf`,
clean filters and SHA-256 repositories never match and get no exemption (today's refusal). `.sdlc`
below the git toplevel gets none either: porcelain paths are toplevel-relative.

BOUNDS. The chain is capped at `CAP` entries keeping its first (the anchor a never-again-committed
first write leaves as HEAD); `proven(..., compact=True)` moves the anchor to HEAD once a check has seen
a mid-chain commit, so the cap only evicts a HEAD after more than `CAP` writes with no check between.

Nothing is recorded unless `<sdlc>/state/` already exists as a real directory, and nothing is read or
written through a link anywhere on the record path (the `_refresh_mirror` idiom, #708): this never
initialises a repository and a link cannot steer the record, or the trust, elsewhere.

LIBRARY ONLY: no `__main__`, nothing at import time. Standard library only, and no `_load` of a sibling
-- `feature_registry` loads this module, so loading it back would be a cycle."""
import contextlib
import hashlib
import json
import os
import pathlib
import stat
import sys
import tempfile
import time

try:
    import fcntl                          # POSIX only -- see `_acquire`
except ImportError:                       # pragma: no cover - exercised on Windows only
    fcntl = None

SCHEMA = "sigma/feature-provenance@1"
#: Long enough that a file written hundreds of times between human commits keeps its anchor;
#: short enough that a record stays one small read (256 x ~44 B = ~11 KB, measured in the plan).
CAP = 256
#: `feature_sync.LOCK_TIMEOUT`'s bound, for its reason: a wedged holder costs serialisation, never
#: a pick that never returns.
LOCK_TIMEOUT = 10.0
#: `feature_sync.LOCK_POLL`'s spin interval: one record write takes well under a millisecond.
LOCK_POLL = 0.005
#: A record is at most CAP 43-byte entries plus its envelope; anything far larger is not ours.
MAX_RECORD = 64 * 1024
#: Registry files are kilobytes; a target this large is not a registry file and is never hashed.
MAX_TARGET = 16 * 1024 * 1024

_RECORD_SUFFIX = ".chain.json"
_LOCK_SUFFIX = ".lock"
_SHARDS = "units"
_SHARD_SUFFIX = ".json"
_PAGE_SUFFIX = ".md"
_HEX = frozenset("0123456789abcdef")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _note(message):
    """One stderr line, never an exception: a diagnostic must never be what breaks a registry write."""
    try:
        sys.stderr.write(message)
    except Exception:                     # noqa: BLE001 - a diagnostic must never break a write
        pass


def blob_id(data):
    """git's object id for `data` stored as a blob -- what `git hash-object` prints, and what porcelain
    v2 reports as a path's HEAD id, so a record entry compares with HEAD without reading HEAD."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _is_oid(value):
    return isinstance(value, str) and len(value) == 40 and set(value) <= _HEX


def _key(features_dir, target):
    """THE ONE NORMALISATION, used by the writer and the reader alike. -> (real `.sdlc`, the target's
    name relative to `features/`), or None when `target` is not a shard or page directly under it.

    The CLI hands every writer the RELATIVE `.sdlc/features` (`work.py start .sdlc <goal>`), `loop.py`
    an absolute one, and a root may be reached through a link: each spelling must name ONE record, or
    the exemption silently never matches. So the `.sdlc` is real-pathed once, and the TARGET'S PARENT
    is real-pathed and must equal `features/units` or `features/` under it. A `units` that is itself a
    link fails that equality and is never recorded."""
    try:
        features = os.path.abspath(os.fspath(features_dir))
        sdlc_real = os.path.realpath(os.path.dirname(features))
        features_real = os.path.join(sdlc_real, os.path.basename(features))
        target = os.path.abspath(os.fspath(target))
        name = os.path.basename(target)
        parent = os.path.realpath(os.path.dirname(target))
    except (TypeError, ValueError, OSError):
        return None
    if parent == os.path.join(features_real, _SHARDS) and name.endswith(_SHARD_SUFFIX):
        return sdlc_real, (_SHARDS, name)
    if parent == features_real and name.endswith(_PAGE_SUFFIX):
        return sdlc_real, (name,)
    return None


def record_path(features_dir, target):
    """-> the chain record for `target`, or None when there must be none: `target` is not a registry
    shard or page (`_key`), `<sdlc>/state` is not already a real directory, or a link sits anywhere on
    the record's path."""
    key = _key(features_dir, target)
    if key is None:
        return None
    sdlc_real, parts = key
    state = os.path.join(sdlc_real, "state")
    if os.path.islink(state) or not os.path.isdir(state):
        return None
    probe = state
    for part in ("features", "provenance") + parts[:-1]:
        probe = os.path.join(probe, part)
        if os.path.islink(probe):
            return None
    record = os.path.join(probe, parts[-1] + _RECORD_SUFFIX)
    if os.path.islink(record):
        return None
    return pathlib.Path(record)


def _acquire(path, timeout=LOCK_TIMEOUT, blocking=True):
    """An exclusive `flock` on `path` -> the open fd, or None if it could not be taken.

    `feature_sync._acquire`'s idiom, written here rather than borrowed: `feature_sync` loads
    `feature_registry`, which loads this module, so borrowing it would be a load cycle. FAIL-OPEN like
    it: no `fcntl`, an unlockable filesystem, or a directory that cannot be made costs serialisation,
    never the write. BOUNDED like it, and `blocking=False` gives up at once -- compaction is an
    optimisation that must never wait on a writer. The fd is the lock; the file is left behind for the
    next acquisition, and is opened without following a link."""
    if fcntl is None:
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | _NOFOLLOW, 0o644)
    except (OSError, ValueError):
        return None
    deadline = time.monotonic() + (max(0.0, float(timeout)) if blocking else 0.0)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(LOCK_POLL)


def _release(fd):
    """Best-effort: closing the fd releases the lock, and must never raise into a write."""
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:                       # noqa: S110 - a failed close is not worth a failed write
        pass


def _read_regular(path, limit=MAX_TARGET):
    """-> the bytes of a regular, non-link file of at most `limit` bytes, else None. Opened without
    following a link and checked on the open descriptor, so the bytes judged are the bytes read."""
    try:
        fd = os.open(os.fspath(path), os.O_RDONLY | _NOFOLLOW)
    except (OSError, ValueError, TypeError):
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        chunks, size = [], 0
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def _load_chain(record):
    """-> the chain the record holds, or [] for anything else: absent, a link, oversize, unreadable,
    not JSON, another schema, or an entry that is not a 40-hex id. An empty chain trusts nothing, so
    every corruption costs a refusal, never an exemption. Read with `Path.read_text` on purpose: it is
    the seam the reader-order test hooks."""
    try:
        if os.path.islink(record) or os.path.getsize(record) > MAX_RECORD:
            return []
        doc = json.loads(pathlib.Path(record).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    chain = doc.get("chain") if isinstance(doc, dict) and doc.get("schema") == SCHEMA else None
    if not isinstance(chain, list) or not all(_is_oid(entry) for entry in chain):
        return []
    return chain


def _store_chain(record, chain):
    """Write the record via a temp file in the same directory, then `os.replace` -- a reader sees the
    whole old record or the whole new one. Raises; `recorded` turns that into one stderr line."""
    directory = os.path.dirname(record)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(record) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema": SCHEMA, "chain": chain}, handle)
        os.replace(tmp, record)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:                   # noqa: S110 - cleanup must not mask the original failure
            pass
        raise


def _last(chain, entry, before=None):
    """-> the last index of `entry` in `chain[:before]`, or -1."""
    end = len(chain) if before is None else before
    for index in range(end - 1, -1, -1):
        if chain[index] == entry:
            return index
    return -1


def _extended(chain, pre, post):
    """The chain after one Sigma write that replaced `pre` (None: no file) with `post`."""
    if pre is None:
        new = [post]
    elif _last(chain, pre) >= 0:
        new = chain[:_last(chain, pre) + 1]
        if new[-1] != post:
            new.append(post)
    else:
        new = [pre] if pre == post else [pre, post]
    if len(new) > CAP:
        # The ANCHOR stays: §14's normal flow commits a unit's first write once and never again,
        # so HEAD is the chain's first entry for as long as nobody commits it again.
        new = [new[0]] + new[-(CAP - 1):]
    return new


@contextlib.contextmanager
def recorded(features_dir, target, tmp):
    """Record one Sigma write of `tmp` over `target`, then run the caller's body -- its own
    `os.replace(tmp, target)` -- under the same lock.

    The chokepoints keep their own replace on purpose: it stays the one write every reader of those
    functions expects, and the record is the only new write this adds. A target with no record (see
    `record_path`) runs the body with no lock and nothing recorded. A record that cannot be kept is
    one stderr line and never an exception; a failing body propagates unchanged, so the caller's own
    cleanup and re-raise still hold."""
    record = record_path(features_dir, target)
    if record is None:
        yield
        return
    fd = _acquire(str(record) + _LOCK_SUFFIX)
    try:
        try:
            written = _read_regular(tmp)
            if written is None:
                raise OSError("the bytes about to be written could not be read back")
            before = _read_regular(target)
            chain = _extended(_load_chain(record), None if before is None else blob_id(before),
                              blob_id(written))
            _store_chain(str(record), chain)
        except Exception as exc:          # noqa: BLE001 - fail open for the write, see the docstring
            _note("sigma: feature provenance: could not record %s (%s); the registry write goes on, "
                  "and the next `work.py start` refuses this file until it is committed\n"
                  % (record, exc))
        yield
    finally:
        _release(fd)


def proven(features_dir, target, head_oid, compact=False):
    """Is `target`'s CURRENT content reachable from `head_oid` (porcelain v2's HEAD blob id) by a run
    of Sigma's own writes? -> bool, never raises.

    The bytes are read FIRST and the chain SECOND; see the module docstring for why that order is
    what makes a concurrent writer harmless. True needs some i < j with chain[i] == HEAD and
    chain[j] == the bytes' id. With `compact`, a proven chain is trimmed to start at that HEAD, under
    the record's lock taken WITHOUT waiting -- skipped when it is held or there is no `fcntl`."""
    try:
        if not _is_oid(head_oid):
            return False
        record = record_path(features_dir, target)
        if record is None:
            return False
        data = _read_regular(target)
        if data is None:
            return False
        current = blob_id(data)
        chain = _load_chain(record)
        at = _last(chain, current)
        if at <= 0 or _last(chain, head_oid, before=at) < 0:
            return False
        if compact:
            _compact(record, head_oid, current)
        return True
    except Exception:                     # noqa: BLE001 - an unprovable file is simply not proven
        return False


def _compact(record, head_oid, current):
    """Drop the entries before HEAD, so the cap can only ever evict history older than the last commit
    a check has seen. Re-reads the chain under the lock; never raises, never waits."""
    fd = _acquire(str(record) + _LOCK_SUFFIX, blocking=False)
    if fd is None:
        return
    try:
        chain = _load_chain(record)
        at = _last(chain, current)
        start = _last(chain, head_oid, before=at) if at > 0 else -1
        if start > 0:
            _store_chain(str(record), chain[start:])
    except Exception:                     # noqa: BLE001 - compaction is an optimisation only
        pass
    finally:
        _release(fd)

"""Spend ledger for the regression eval: reserve a dollar belt, settle it, survive being killed (#886).

Slice 2 of story #810. Standalone and stdlib-only: it imports nothing from `record.py`, `check.py` or any
other slice, starts no process and no model, makes no network call, reads no environment variable and
no credential. Importing it has no side effect (not even on a platform without `fcntl`).

API
---
    ledger = Ledger(ledger_dir, cap, lock_timeout_s=5.0)      # cap in DOLLARS, per ledger home
    res = ledger.reserve(requested_belt, run_id=None, now=None)
        -> Reservation(id, month, belt)   the open row is fsynced BEFORE this returns
        -> NotRun(code, counted, cap, reports)   code is "budget", "ledger-corrupt" or "lock-timeout"
        raises LedgerRefusal(code) for things that must not be papered over: bad-amount, duplicate-run-id,
        naive-datetime, unsupported-platform, lock-unavailable, dir-unavailable, read-failed, write-failed
    ledger.settle(res, spent, reason=None, now=None)          # appends a settle row to res.month's file
    with ledger.reserved(requested_belt) as res: ...          # signal-safe wrapper, see "Signals"
    settle_orphan(ledger_dir, month, run_id, spent, now=None) # operator lever, see "Recovery"
    count_spend(raw, month, cap, name)                        # the pure reader (unit-testable)
    LedgerSignal(BaseException)                               # raised by the SIGTERM/SIGHUP handler

Cap, belt and spent are DOLLARS, accepted as str, int or Decimal with at most 15 integer and 9 fractional
digits (exactly what the reader accepts; anything wider is refused `bad-amount` BEFORE anything is written,
never rounded); a caller-supplied run_id already in the month (open or settled) is refused `duplicate-run-id`
inside the lock; float, bool, NaN, infinity, negative
values, a zero cap and a zero belt are refused. Rows carry amounts as decimal strings ("0.40"), never
JSON floats, so sums are exact and a ledger reads the same on every host.

Files
-----
`<ledger_dir>/spend-YYYY-MM.jsonl`, one per UTC month of the `open` row's timestamp, append-only, mode 0600
(directory 0700 when this module creates it). `<ledger_dir>/spend.lock` is the lock file. One JSON object
per line, compact, sorted keys, UTF-8, one newline terminator:
    {"kind":"open","id":"<16 hex>","belt":"<dollars>","t":"<YYYY-MM-DDTHH:MM:SSZ>","pid":<int>}
    {"kind":"settle","id":"<16 hex>","spent":"<dollars>","t":"<...Z>"}  plus optional "reason": signal|orphan|exit
`pid` is diagnostic only. The module never deletes, truncates, renames or replaces a ledger file, and
never reads an old month: nothing prunes, so about 1 MB a month at 100x the planned volume (retention is
an operator matter). A `settle` goes to the SAME month file as its `open` (the Reservation carries the
month), so a run that crosses midnight UTC on the 1st never strands an open in one file and a settle in
another. `now` must be an aware datetime (a naive one is refused, `naive-datetime`) and is converted to UTC.

Counted spend (pure, `count_spend`)
-----------------------------------
Per id: an `open` with no `settle` counts its FULL belt; an `open` with a `settle` counts `spent` as
written (an overshoot above the belt counts as `spent`: overshoot is real; a lower `spent` frees budget).
A settle replaces the count of its own open only. The first settle for an id wins; a duplicate settle and
an orphan settle (no open) contribute nothing and are reported. The next belt is
`min(requested, cap - counted)`; zero or below is NOT RUN (code "budget"), nothing is written.

Corrupt line = full cap
-----------------------
Any line that is not valid UTF-8 JSON, is not an object, has an unknown kind, a missing or ill-typed
field, a malformed amount, id or timestamp, an `open` whose month differs from the file name, a repeated
open id, a blank interior line, or is a final line with no newline (a torn append) sets that month's counted
spend to the FULL cap. The result is NotRun("ledger-corrupt") with reports naming file, line number and
reason, never the line's content. Blast radius: the whole month stops. Lever: HAND REPAIR ONLY. Delete or
fix the named line in the month file. `settle_orphan` does not repair corruption (it refuses on a corrupt
month), and a torn tail left by a crash mid-append stops that month with NO self-heal. That is the
fail-closed price; skipping a bad line was rejected because it would silently under-count spend.

Lock, platform and durability
-----------------------------
`fcntl.flock(LOCK_EX | LOCK_NB)` polled against a monotonic deadline (`lock_timeout_s`, default 5.0: the
critical section is a read plus one fsynced append, milliseconds, so seconds is generous). A timeout is
NotRun("lock-timeout"), never "proceed unlocked". Unlike the idiom it is copied from
(`feature_judge.py`), this module fails CLOSED: with no `fcntl` (Windows) every use raises
LedgerRefusal("unsupported-platform") (tested by blocking `fcntl`, not on a real Windows host); an
unopenable lock file raises "lock-unavailable". The kernel drops a flock when its holder dies, so a dead
holder leaves no stale lock. flock belongs to the open file description, so a SECOND open of the lock file
in the SAME process conflicts with the first: nothing here ever takes the lock from a signal handler.
The row is written with one `os.write` (O_APPEND) and made durable before return: `fcntl.F_FULLFSYNC` where
the platform has it (macOS; a plain fsync does not reach the platter there), else `os.fsync`; a newly
created file also gets its directory fsynced. If the write or fsync fails no handle is returned
(LedgerRefusal "write-failed"); a row that did reach the page cache stays and counts at its full belt.

Signals
-------
`reserved()` installs, on the MAIN thread only, SIGTERM and SIGHUP handlers that do nothing but raise
LedgerSignal, and restores the previous handlers on the way out. The exception unwinds out of any locked
section (the lock is released there) and only then does the context manager settle the run at the FULL
belt with reason "signal" (real spend is unknown and a killed `claude` may outlive its parent, so fail
closed). Cases: before the open is written, nothing to settle; after the fsync but before `reserve`
returned, `reserve` settles the id itself and re-raises; inside `settle`'s locked section, the settle is
retried once. SIGTERM/SIGHUP are blocked from the flock until the lock's try is entered and again around
its release (pthread_sigmask; a held signal is delivered right after), so a signal cannot leak the flock'd
descriptor or the retry's own lock conflict. A signal landing after the else-path settle still exits
128 + signum; while handlers are restored the signal waits and meets the restored handler. Residual: a
signal during the `except` block of a failing body replaces that exception with the exit, unsettled, which
fails closed at the full belt. A run that leaves the block without a caller `settle` settles at the full belt, reason "exit".
Exit status: after settling, a LedgerSignal becomes `SystemExit(128 + signum)` (143 for SIGTERM, 129 for
SIGHUP), the shell convention. A second signal while settling interrupts that attempt too; if both
attempts are interrupted nothing is written and the open stays counted at the full belt (fail closed). Any
other BaseException, Ctrl-C included, takes the "exit" path; that is not tested for Ctrl-C. SIGKILL cannot
be caught: the open row stays and counts at its full belt on the next start (proven with a real child).
`_hook_in_critical_section` is a TEST-ONLY seam, called with a stage name inside locked sections and at
lock-acquired, lock-releasing and reserved-exit.

Recovery
--------
Accepted fail-closed residuals (no test; both leave the ledger OVER-counting, never under-counting): (1) a
signal arriving just before the signal mask is set around the lock release can raise out of `_locked`
with the lock still held, so it stays held until process exit and the open stays counted at its full belt
(exit status 128 + signum); (2) a signal between `signal.signal()` returning and the previous handler being
stored in `prev` can leave the custom handler installed until exit, because `_restore_handlers` does not
know to restore it.
    SIGKILL / crash / reboot  open stays, counted at full belt; the over-count lasts the month unless an
                              operator releases it with settle_orphan(ledger_dir, month, run_id, spent)
    torn or corrupt line      the month is NOT RUN until a human repairs the line (see above)
    lock holder died          nothing to do; lock timeout is NOT RUN, retry later
    disk full / read-only     no handle, LedgerRefusal; nothing is launched
    restored older month file under-counts: rows written since are gone. Residual, no mitigation here.
Residuals this module does not and cannot close: A lost hosted runner is defeated by this local file: the
VM's disk is gone and the ledger forgets the belt. Job-level reservation belongs to the workflow goal
(D-4 of story #810, carried open to #819); this module does not and cannot close it. The cap is per ledger
home: two machines each believe they hold the whole cap.

Scale (one run, macOS, Python 3.12, one machine; other platforms unmeasured): each reserve reads the
current month file in full under the lock, about 87 bytes a row, two rows a run. Measured: reserve about
4 ms and settle about 4 ms on a near-empty month (the fsync dominates the critical section); with a
10,060-line, 870 KB month file (100x the planned volume) the parse is about 25 ms and a reserve about 29 ms.
The lock serialises ledger access on one machine, so the ceiling is one reserve or settle per few ms.

Controls (each broken on purpose and seen red on exactly these gestures; any Python 3.10+ with pytest):
    python3.12 -m pytest tests/test_regression_ledger.py -q
    python3.12 -m pytest tests/test_regression_ledger.py::test_sigkill_between_reserve_and_settle_counts_full_belt -q
    python3.12 -m pytest tests/test_regression_ledger.py::test_corrupt_line_fails_closed -q
    python3.12 -m pytest tests/test_regression_ledger.py::test_month_rollover_starts_fresh_file -q
    python3.12 -m pytest tests/test_regression_ledger.py::test_signal_inside_critical_section_does_not_deadlock -q
    python3.12 -m pytest tests/test_regression_ledger.py::test_every_write_under_evals_regression_is_documented -q
    python3.12 -m pytest tests/test_regression_ledger.py::test_whatever_reserve_accepts_the_next_reserve_reads_back_valid -q
    python3.12 -m pytest tests/test_regression_ledger.py::test_duplicate_or_settled_run_id_refused_before_writing -q
    python3.12 -m pytest tests/test_regression_ledger.py::test_signal_around_lock_acquire_and_release_does_not_leak_the_lock -q
    python3.12 -m pytest tests/test_regression_ledger.py::test_signal_after_settle_before_handlers_restored_still_exits_128_plus_signum -q
"""
import contextlib
import errno
import json
import os
import re
import secrets
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

try:                                    # a non-POSIX platform still imports; every use then refuses
    import fcntl
except ImportError:
    fcntl = None

_AMOUNT = re.compile(r"[0-9]{1,15}(?:\.[0-9]{1,9})?")
_ID = re.compile(r"[0-9a-f]{16}")
_STAMP = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
_REASONS = ("signal", "orphan", "exit")
_MAX_REPORTS = 50

_hook_in_critical_section = None        # TEST ONLY: called with a stage name inside locked sections


class LedgerRefusal(Exception):
    """A condition that must not be papered over; `code` says which."""

    def __init__(self, code, detail=""):
        super().__init__("%s%s" % (code, ": " + detail if detail else ""))
        self.code = code
        self.detail = detail


class LedgerSignal(BaseException):
    """Raised by the SIGTERM/SIGHUP handler. A BaseException so `except Exception` never swallows it."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


@dataclass
class Reservation:
    id: str
    month: str
    belt: Decimal
    settled: bool = False


@dataclass
class NotRun:
    code: str
    counted: object = None
    cap: object = None
    reports: list = field(default_factory=list)


@dataclass
class Counted:
    counted: Decimal
    corrupt: bool
    reports: list
    opens: dict
    settled: dict


def _block_signals():
    """Block SIGTERM/SIGHUP on this thread; returns the old mask for `_restore_mask` (None: unsupported)."""
    mask = getattr(signal, "pthread_sigmask", None)
    if mask is None:
        return None
    sigs = {getattr(signal, n) for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n)}
    return mask(signal.SIG_BLOCK, sigs)


def _restore_mask(old):
    """Unblock; a signal that arrived while blocked is delivered here, to whatever handler is current."""
    if old is not None:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def _stage(name):
    hook = _hook_in_critical_section
    if hook is not None:
        hook(name)


def _money(value, what, positive):
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise LedgerRefusal("bad-amount", "%s must be dollars as str, int or Decimal" % what)
    if isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, Decimal):
        dec = value
    elif isinstance(value, str) and _AMOUNT.fullmatch(value):
        dec = Decimal(value)
    else:
        raise LedgerRefusal("bad-amount", "%s is not a plain non-negative decimal" % what)
    if not dec.is_finite() or dec < 0 or (positive and dec == 0):
        raise LedgerRefusal("bad-amount", "%s must be %s" % (what, "above zero" if positive else "zero or more"))
    # the writer accepts exactly what the reader's _AMOUNT accepts (<= 15 integer, <= 9 fractional digits):
    # a row the reader would call corrupt must never be written, or it would fail the whole month closed
    if dec.adjusted() >= 15 or dec.as_tuple().exponent < -9:
        raise LedgerRefusal("bad-amount", "%s must have at most 15 integer and 9 fractional digits" % what)
    return dec.copy_abs()


def _fmt(dec):
    return format(dec, "f")


def _utc(now):
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise LedgerRefusal("naive-datetime", "now must be a timezone-aware datetime")
    return now.astimezone(timezone.utc)


def _stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _no_const(name):
    raise ValueError("non-finite JSON constant")


def _check_row(obj, month):
    """Return (reason, None) for a bad row, or (None, normalised dict)."""
    if not isinstance(obj, dict):
        return "not-an-object", None
    kind = obj.get("kind")
    if kind not in ("open", "settle"):
        return "unknown-kind", None
    amount = "belt" if kind == "open" else "spent"
    for key in ("id", amount, "t"):
        if key not in obj:
            return "missing-field:" + key, None
    if not (isinstance(obj["id"], str) and _ID.fullmatch(obj["id"])):
        return "bad-field:id", None
    if not (isinstance(obj[amount], str) and _AMOUNT.fullmatch(obj[amount])):
        return "bad-field:" + amount, None
    if not (isinstance(obj["t"], str) and _STAMP.fullmatch(obj["t"])):
        return "bad-field:t", None
    if "pid" in obj and (isinstance(obj["pid"], bool) or not isinstance(obj["pid"], int)):
        return "bad-field:pid", None
    if kind == "settle" and "reason" in obj and obj["reason"] not in _REASONS:
        return "bad-field:reason", None
    if kind == "open" and obj["t"][:7] != month:
        return "wrong-month", None
    return None, {"kind": kind, "id": obj["id"], "amount": Decimal(obj[amount])}


def count_spend(raw, month, cap, name):
    """The pure reader: bytes of one month file -> Counted. See the module docstring for the rules."""
    reports, corrupt = [], False
    opens, settled = {}, {}
    lines = raw.split(b"\n") if raw else []
    torn = bool(lines) and lines[-1] != b""
    if lines and not torn:
        lines.pop()

    def bad(number, reason):
        nonlocal corrupt
        corrupt = True
        if len(reports) < _MAX_REPORTS:
            reports.append("%s:%d: %s" % (name, number, reason))

    for number, line in enumerate(lines, 1):
        if torn and number == len(lines):
            bad(number, "torn-final-line")
            continue
        if line == b"":
            bad(number, "blank-line")
            continue
        try:
            obj = json.loads(line.decode("utf-8"), parse_constant=_no_const)
        except UnicodeDecodeError:
            bad(number, "not-utf8")
            continue
        except (ValueError, RecursionError):        # RecursionError: a line of 200,000 "[" is corrupt, not fatal
            bad(number, "not-json")
            continue
        reason, row = _check_row(obj, month)
        if reason:
            bad(number, reason)
        elif row["kind"] == "open":
            if row["id"] in opens:
                bad(number, "duplicate-open-id")
            opens[row["id"]] = row["amount"]
        elif row["id"] in settled:
            reports.append("%s:%d: duplicate-settle (ignored)" % (name, number))
        else:
            settled[row["id"]] = (row["amount"], number)
    for rid, (_, number) in sorted(settled.items(), key=lambda kv: kv[1][1]):
        if rid not in opens:
            reports.append("%s:%d: orphan-settle (counts nothing)" % (name, number))
    total = sum((settled[i][0] if i in settled else belt for i, belt in opens.items()), Decimal(0))
    return Counted(cap if corrupt else total, corrupt, reports, opens, {i: v[0] for i, v in settled.items()})


def _fsync(fd):
    """Make fd durable: F_FULLFSYNC where the platform has it, else os.fsync. Raises OSError on failure."""
    full = getattr(fcntl, "F_FULLFSYNC", None) if fcntl is not None else None
    if full is not None:
        try:
            fcntl.fcntl(fd, full)
            return
        except OSError:
            pass                        # some filesystems refuse it; the plain fsync is the fallback
    os.fsync(fd)


def _append(path, data, dirpath):
    """ONE write of the whole line under the lock, then fsync; a new file also gets its directory fsynced."""
    try:
        existed = os.path.exists(path)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            view = memoryview(data)
            while len(view):
                view = view[os.write(fd, view):]
            _fsync(fd)
        finally:
            os.close(fd)
        if not existed:
            dfd = os.open(dirpath, os.O_RDONLY)
            try:
                _fsync(dfd)
            finally:
                os.close(dfd)
    except OSError as exc:
        raise LedgerRefusal("write-failed", "%s: %s" % (type(exc).__name__, exc.strerror or exc))


def _encode(row):
    return json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _read(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return b""
    except OSError as exc:
        raise LedgerRefusal("read-failed", "%s" % (exc.strerror or type(exc).__name__))


def _require_posix():
    if fcntl is None:
        raise LedgerRefusal("unsupported-platform", "the spend ledger needs fcntl.flock and refuses without it")


def _install_handlers(prev):
    if threading.current_thread() is not threading.main_thread():
        return

    def handler(signum, frame):
        raise LedgerSignal(signum)

    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            prev[sig] = signal.signal(sig, handler)


def _restore_handlers(prev):
    held = _block_signals()                     # a signal now waits, then meets the RESTORED handler
    try:
        for sig, old in prev.items():
            signal.signal(sig, old if old is not None else signal.SIG_DFL)
    finally:
        _restore_mask(held)


class Ledger:
    def __init__(self, ledger_dir, cap, lock_timeout_s=5.0):
        self.dir = os.fspath(ledger_dir)
        self.cap = _money(cap, "cap", True)
        if isinstance(lock_timeout_s, bool) or not isinstance(lock_timeout_s, (int, float)) or lock_timeout_s <= 0:
            raise LedgerRefusal("bad-amount", "lock_timeout_s must be a positive number")
        self.lock_timeout_s = float(lock_timeout_s)
        self.last_reports = []

    # ---- directory and lock

    def _ensure_dir(self):
        try:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
        except OSError as exc:
            raise LedgerRefusal("dir-unavailable", exc.strerror or type(exc).__name__)

    def _acquire(self):
        try:
            fd = os.open(os.path.join(self.dir, "spend.lock"), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise LedgerRefusal("lock-unavailable", exc.strerror or type(exc).__name__)
        try:
            deadline = time.monotonic() + self.lock_timeout_s
            while True:
                old = _block_signals()          # held from the flock until `_locked` is inside its try
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    _restore_mask(old)
                    if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                        raise LedgerRefusal("lock-unavailable", exc.strerror or type(exc).__name__)
                else:
                    return fd, old
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return None, None
                time.sleep(0.05)
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise

    @contextlib.contextmanager
    def _locked(self):
        """Yield True holding the exclusive lock, or False on timeout. Never proceeds unlocked."""
        _require_posix()
        self._ensure_dir()
        fd, mask = self._acquire()
        _stage("lock-acquired")                 # the window a signal could leak the descriptor in
        try:
            _restore_mask(mask)                 # a signal held since the flock lands here, inside the try
            yield fd is not None
        finally:
            if fd is not None:
                old = _block_signals()          # so a signal cannot land between here and the close
                try:
                    _stage("lock-releasing")
                    with contextlib.suppress(OSError):
                        os.close(fd)            # closing the only descriptor drops the flock
                finally:
                    _restore_mask(old)

    def _month_path(self, month):
        return os.path.join(self.dir, "spend-%s.jsonl" % month)

    # ---- reserve / settle

    def reserve(self, requested_belt, run_id=None, now=None):
        want = _money(requested_belt, "belt", True)
        when = _utc(now)
        month = when.strftime("%Y-%m")
        path = self._month_path(month)
        rid = run_id if run_id is not None else secrets.token_hex(8)
        if not (isinstance(rid, str) and _ID.fullmatch(rid)):
            raise LedgerRefusal("bad-run-id", "run_id must be 16 lowercase hex characters")
        res = None
        try:
            with self._locked() as got:
                if not got:
                    return NotRun("lock-timeout", None, self.cap)
                _stage("reserve-locked")
                state = count_spend(_read(path), month, self.cap, os.path.basename(path))
                self.last_reports = state.reports
                if state.corrupt:
                    return NotRun("ledger-corrupt", state.counted, self.cap, state.reports)
                if rid in state.opens or rid in state.settled:
                    raise LedgerRefusal("duplicate-run-id", "that run_id is already in this month's ledger")
                belt = min(want, self.cap - state.counted)
                if belt <= 0:
                    return NotRun("budget", state.counted, self.cap, state.reports)
                row = {"kind": "open", "id": rid, "belt": _fmt(belt), "t": _stamp(when), "pid": os.getpid()}
                _append(path, _encode(row), self.dir)
                res = Reservation(rid, month, belt)
                _stage("reserve-written")
        except BaseException as exc:
            # a signal after the fsync but before return: the open is durable, so settle it (full belt)
            # now that the lock is released. A signal between the fsync and `res =` leaves a bare open,
            # which still counts at its full belt: fail closed.
            if res is not None:
                self._settle_best_effort(res, res.belt, "signal" if isinstance(exc, LedgerSignal) else "exit")
            raise
        return res

    def _append_row(self, month, row):
        if not _MONTH.fullmatch(month):
            raise LedgerRefusal("bad-month", "month must be YYYY-MM")
        with self._locked() as got:
            if not got:
                raise LedgerRefusal("lock-timeout", "could not take the ledger lock to settle")
            _stage("settle-locked")
            _append(self._month_path(month), _encode(row), self.dir)

    def settle(self, reservation, spent, reason=None, now=None):
        amount = _money(spent, "spent", False)
        when = _utc(now)
        if not (isinstance(reservation.id, str) and _ID.fullmatch(reservation.id)):
            raise LedgerRefusal("bad-reservation", "reservation id must be 16 lowercase hex digits")
        if not (isinstance(reservation.month, str) and _MONTH.fullmatch(reservation.month)):
            raise LedgerRefusal("bad-month", "month must be YYYY-MM")
        if reason is not None and reason not in _REASONS:
            raise LedgerRefusal("bad-reason", "reason must be one of %s" % (_REASONS,))
        row = {"kind": "settle", "id": reservation.id, "spent": _fmt(amount), "t": _stamp(when)}
        if reason is not None:
            row["reason"] = reason
        self._append_row(reservation.month, row)
        reservation.settled = True

    def _settle_best_effort(self, res, spent, reason):
        """Settle once, retry once if a signal interrupts the locked section. Never raises for those."""
        for _ in range(2):
            try:
                self.settle(res, spent, reason)
                return True
            except LedgerSignal:
                continue
            except LedgerRefusal:
                return False            # cannot record it: the open stays counted at its full belt
        return False

    @contextlib.contextmanager
    def reserved(self, requested_belt, run_id=None, now=None):
        """Reserve, yield the Reservation (or a NotRun, nothing to settle), settle on the way out."""
        prev = {}
        res = None
        try:
            _install_handlers(prev)
            try:
                res = self.reserve(requested_belt, run_id, now)
                yield res
            except BaseException as exc:
                signalled = isinstance(exc, LedgerSignal)
                self._settle_unsettled(res, "signal" if signalled else "exit")
                if signalled:
                    raise SystemExit(128 + exc.signum) from None
                raise
            else:
                self._settle_unsettled(res, "exit")
                _stage("reserved-exit")
        except LedgerSignal as exc:
            # a signal outside the inner handler (after the else-path settle, or before the reserve):
            # nothing unsettled is left that the inner path could have settled, so only the exit status
            raise SystemExit(128 + exc.signum) from None
        finally:
            _restore_handlers(prev)

    def _settle_unsettled(self, res, reason):
        if isinstance(res, Reservation) and not res.settled:
            self._settle_best_effort(res, res.belt, reason)


def settle_orphan(ledger_dir, month, run_id, spent, now=None, lock_timeout_s=5.0):
    """Operator lever: release a known-dead unsettled open (a SIGKILLed run) without hand-editing JSON.

    Refuses if the id is not an unsettled open in that month, and refuses on a corrupt month: it does not
    repair corruption (hand repair only, see the module docstring)."""
    if not (isinstance(month, str) and _MONTH.fullmatch(month)):
        raise LedgerRefusal("bad-month", "month must be YYYY-MM")
    amount = _money(spent, "spent", False)
    when = _utc(now)
    led = Ledger(ledger_dir, 1, lock_timeout_s)                 # the cap is not used by this lever
    path = led._month_path(month)
    with led._locked() as got:
        if not got:
            raise LedgerRefusal("lock-timeout", "could not take the ledger lock")
        state = count_spend(_read(path), month, led.cap, os.path.basename(path))
        if state.corrupt:
            raise LedgerRefusal("ledger-corrupt", "; ".join(state.reports))
        if run_id not in state.opens or run_id in state.settled:
            raise LedgerRefusal("not-an-unsettled-open", "no unsettled open with that id in " + month)
        row = {"kind": "settle", "id": run_id, "spent": _fmt(amount), "t": _stamp(when), "reason": "orphan"}
        _append(path, _encode(row), led.dir)

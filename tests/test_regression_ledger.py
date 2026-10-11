"""`evals/regression/spend_ledger.py` -- the spend ledger (#886, slice 2 of story #810).

Everything here uses temp directories and fake dollar amounts. No test starts a model, a
network call or anything but Python children that load `spend_ledger.py` by path and run ITS code.

Kill and signal tests use REAL child processes. Every wait on a child is bounded and a hang FAILS
(the child is killed and the test calls `pytest.fail`) instead of hanging the suite.

`test_two_process_race_smoke` is a SMOKE test, not the system of record for the lock: the in-process
window it races is tiny, so a removed lock may not turn it red. The deterministic control is
`test_lock_timeout_is_not_run_deterministic` (a lock held on purpose, a child that must time out).
"""
import ast
import importlib.util
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER = ROOT / "evals" / "regression" / "spend_ledger.py"

_spec = importlib.util.spec_from_file_location("regression_ledger", LEDGER)
ledger = importlib.util.module_from_spec(_spec)
sys.modules["regression_ledger"] = ledger
_spec.loader.exec_module(ledger)

MAR = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
MARCH_FILE = "spend-2026-03.jsonl"
SENTINEL = "SENTINELTEXT"
ID_A, ID_B = "a" * 16, "b" * 16


# --------------------------------------------------------------------------- helpers


def _row(**kw):
    return (json.dumps(kw, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _open(id_=ID_A, belt="4.00", t="2026-03-10T00:00:00Z"):
    return _row(kind="open", id=id_, belt=belt, t=t, pid=1)


def _settle(id_=ID_A, spent="1.00", t="2026-03-10T00:01:00Z"):
    return _row(kind="settle", id=id_, spent=spent, t=t)


def _count(raw, cap="10.00"):
    return ledger.count_spend(raw, "2026-03", Decimal(cap), MARCH_FILE)


def _led(tmp_path, cap="10.00", timeout=2.0):
    return ledger.Ledger(tmp_path / "ledger", cap, lock_timeout_s=timeout)


def _rows(tmp_path, name=MARCH_FILE):
    p = tmp_path / "ledger" / name
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines()]


def _counted(tmp_path, cap="10.00"):
    raw = (tmp_path / "ledger" / MARCH_FILE).read_bytes()
    return _count(raw, cap).counted


# --------------------------------------------------------------------------- Task A: pure reader


def test_counted_unsettled_open_is_full_belt():
    got = _count(_open(belt="4.00") + _open(ID_B, "2.50"))
    assert got.counted == Decimal("6.50") and not got.corrupt


def test_counted_settle_replaces_its_open():
    got = _count(_open(belt="4.00") + _settle(spent="1.25") + _open(ID_B, "2.00"))
    assert got.counted == Decimal("3.25")


def test_counted_overshoot_counts_spent():
    assert _count(_open(belt="4.00") + _settle(spent="5.00")).counted == Decimal("5.00")


def test_counted_orphan_and_duplicate_settle_reported():
    raw = _open(belt="4.00") + _settle(spent="1.00") + _settle(spent="0.10") + _settle(ID_B, "9.00")
    got = _count(raw)
    assert got.counted == Decimal("1.00")                       # first settle wins; orphan adds nothing
    assert any(MARCH_FILE + ":3" in r and "duplicate-settle" in r for r in got.reports)
    assert any(MARCH_FILE + ":4" in r and "orphan-settle" in r for r in got.reports)
    assert not got.corrupt


def test_clamp_reduces_belt_then_not_run_at_zero(tmp_path):
    led = _led(tmp_path, cap="10.00")
    first = led.reserve("4", now=MAR)
    assert isinstance(first, ledger.Reservation) and first.belt == Decimal("4")
    assert led.reserve(Decimal("4"), now=MAR).belt == Decimal("4")
    third = led.reserve("4", now=MAR)
    assert third.belt == Decimal("2.00")                        # clamped to cap - counted
    nr = led.reserve("4", now=MAR)
    assert isinstance(nr, ledger.NotRun) and nr.code == "budget"
    assert nr.counted == Decimal("10.00") and nr.cap == Decimal("10.00")
    led.settle(first, "1.00", now=MAR)                          # lower spend frees budget
    assert led.reserve("4", now=MAR).belt == Decimal("3.00")
    # exact-zero is NOT RUN, not a zero belt
    other = _led(tmp_path / "z", cap="4")
    assert other.reserve("4", now=MAR).belt == Decimal("4")
    assert other.reserve("1", now=MAR).code == "budget"


@pytest.mark.parametrize("bad", [-1, 0, "0", "0.00", "-1", "abc", "nan", "NaN", "inf", "1e3", " 1", "1 ", "",
                                 1.5, True, None, Decimal("NaN"), Decimal("Infinity"), Decimal("-2")])
def test_decimal_strictness_refuses_bad_amounts(tmp_path, bad):
    with pytest.raises(ledger.LedgerRefusal) as exc:
        ledger.Ledger(tmp_path / "c", bad)
    assert exc.value.code == "bad-amount"
    led = _led(tmp_path)
    with pytest.raises(ledger.LedgerRefusal) as exc:
        led.reserve(bad, now=MAR)
    assert exc.value.code == "bad-amount"
    assert not (tmp_path / "ledger").exists()                   # refused before anything was created


def test_settle_refuses_negative_and_bad_spent_but_allows_zero(tmp_path):
    led = _led(tmp_path)
    res = led.reserve("4", now=MAR)
    for bad in ("-0.01", "x", 1.5, None, True):
        with pytest.raises(ledger.LedgerRefusal):
            led.settle(res, bad, now=MAR)
    led.settle(res, "0", now=MAR)
    assert _counted(tmp_path) == Decimal("0")


# ---- writer and reader agree on what an amount is (review finding 1)

_TOO_PRECISE = [Decimal("1.0000000001"), Decimal("0.1234567891"), Decimal("1.50000000000"), Decimal("1E-10")]
_TOO_BIG = [10 ** 15, 10 ** 40, Decimal("1E+15"), Decimal("1E+1000000"), Decimal("1" + "0" * 15)]


@pytest.mark.parametrize("bad", _TOO_PRECISE + _TOO_BIG)
def test_over_precision_or_oversize_belt_refused_before_anything_is_written(tmp_path, bad):
    led = _led(tmp_path, cap="10.00")
    with pytest.raises(ledger.LedgerRefusal) as exc:
        led.reserve(bad, now=MAR)
    assert exc.value.code == "bad-amount"
    assert not (tmp_path / "ledger").exists()


@pytest.mark.parametrize("bad", _TOO_PRECISE + _TOO_BIG)
def test_over_precision_or_oversize_spent_refused_and_month_stays_readable(tmp_path, bad):
    led = _led(tmp_path)
    res = led.reserve("4", now=MAR)
    before = (tmp_path / "ledger" / MARCH_FILE).read_bytes()
    with pytest.raises(ledger.LedgerRefusal) as exc:
        led.settle(res, bad, now=MAR)
    assert exc.value.code == "bad-amount" and not res.settled
    assert (tmp_path / "ledger" / MARCH_FILE).read_bytes() == before
    assert isinstance(led.reserve("1", now=MAR), ledger.Reservation)


@pytest.mark.parametrize("bad", _TOO_PRECISE + _TOO_BIG + ["1" + "0" * 15, "0." + "1" * 10])
def test_over_precision_or_oversize_cap_refused(tmp_path, bad):
    with pytest.raises(ledger.LedgerRefusal) as exc:
        ledger.Ledger(tmp_path / "c", bad)
    assert exc.value.code == "bad-amount"


def test_amount_limits_are_exactly_the_readers_limits(tmp_path):
    top = "9" * 15 + "." + "9" * 9
    led = ledger.Ledger(tmp_path / "ledger", top, lock_timeout_s=2.0)
    res = led.reserve(top, now=MAR)
    assert res.belt == Decimal(top)
    led.settle(res, top, now=MAR)
    assert not _count((tmp_path / "ledger" / MARCH_FILE).read_bytes(), top).corrupt


def test_whatever_reserve_accepts_the_next_reserve_reads_back_valid(tmp_path):
    import random
    rng = random.Random(886)
    accepted = 0
    for n in range(150):
        digits = "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 30)))
        exp = rng.randint(-14, 8)
        value = Decimal(digits).scaleb(exp)
        for make in (lambda v: v, str, lambda v: format(v, "f")):
            home = tmp_path / ("h%d" % n)
            try:
                led = ledger.Ledger(home, make(value), lock_timeout_s=2.0)
                res = led.reserve(make(value), now=MAR)
            except ledger.LedgerRefusal as exc:
                assert exc.code == "bad-amount"
                continue
            if not isinstance(res, ledger.Reservation):
                continue
            try:
                led.settle(res, make(value), now=MAR)
            except ledger.LedgerRefusal as exc:
                assert exc.code == "bad-amount"
            accepted += 1
            raw = (home / MARCH_FILE).read_bytes()
            assert not ledger.count_spend(raw, "2026-03", led.cap, MARCH_FILE).corrupt, (value, raw)
            nxt = led.reserve("0.5", now=MAR)
            assert getattr(nxt, "code", None) != "ledger-corrupt", (value, raw)
    assert accepted > 20                                        # the property was exercised, not vacuous


# ---- a caller-supplied run_id is never reused (review finding 2)


def test_duplicate_or_settled_run_id_refused_before_writing(tmp_path):
    led = _led(tmp_path)
    first = led.reserve("1", run_id=ID_A, now=MAR)
    assert first.id == ID_A
    before = (tmp_path / "ledger" / MARCH_FILE).read_bytes()
    with pytest.raises(ledger.LedgerRefusal) as exc:
        led.reserve("1", run_id=ID_A, now=MAR)                  # retry of a still-open id
    assert exc.value.code == "duplicate-run-id"
    led.settle(first, "0.5", now=MAR)
    settled = (tmp_path / "ledger" / MARCH_FILE).read_bytes()
    with pytest.raises(ledger.LedgerRefusal) as exc:
        led.reserve("1", run_id=ID_A, now=MAR)                  # retry of an already settled id
    assert exc.value.code == "duplicate-run-id"
    assert (tmp_path / "ledger" / MARCH_FILE).read_bytes() == settled
    assert settled.startswith(before)
    assert isinstance(led.reserve("1", run_id=ID_B, now=MAR), ledger.Reservation)   # the month still works


# ---- the lock fd is never leaked by a signal (review finding 3)


def _lock_is_free(tmp_path):
    import fcntl
    fd = os.open(str(tmp_path / "ledger" / "spend.lock"), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _mask_now():
    return signal.pthread_sigmask(signal.SIG_BLOCK, [])         # an empty block set only READS the mask


def _with_hook(hook):
    ledger._hook_in_critical_section = hook


def test_signals_are_blocked_and_lock_held_at_the_acquire_and_release_seams(tmp_path):
    """System of record (deterministic, no signal is sent): at `lock-acquired` and `lock-releasing`
    SIGTERM and SIGHUP are blocked on this thread and the flock is still held; at `reserve-locked` the
    caller's mask is back; after reserve the prior mask (a non-default one) is restored and the lock free."""
    prior = {signal.SIGUSR1}
    old = signal.pthread_sigmask(signal.SIG_SETMASK, prior)
    seen = {}

    def hook(name):
        seen[name] = (_mask_now(), _lock_is_free(tmp_path))
    _with_hook(hook)
    try:
        res = _led(tmp_path).reserve("1", now=MAR)
        after = _mask_now()
    finally:
        _with_hook(None)
        signal.pthread_sigmask(signal.SIG_SETMASK, old)
    assert isinstance(res, ledger.Reservation)
    both = {signal.SIGTERM, signal.SIGHUP}
    for stage in ("lock-acquired", "lock-releasing"):
        mask, free = seen[stage]
        assert both <= set(mask), stage                         # blocked: a signal cannot land mid-window
        assert free is False, stage                             # and the lock really is held right now
    mask, free = seen["reserve-locked"]
    assert not (both & set(mask)) and free is False             # unblocked inside the body, lock held
    assert set(after) == prior                                  # restored to the PRIOR mask, not cleared
    assert _lock_is_free(tmp_path)


@pytest.mark.parametrize("stage", ["reserve-locked", "reserve-written"])
def test_handler_running_inside_the_critical_section_releases_the_lock(tmp_path, stage):
    """System of record (deterministic): the hook raises LedgerSignal at the stage, which is exactly what
    the installed handler does when a signal lands there. No signal is sent."""
    old = signal.pthread_sigmask(signal.SIG_SETMASK, set())

    def hook(name):
        if name == stage:
            raise ledger.LedgerSignal(signal.SIGTERM)
    _with_hook(hook)
    try:
        with pytest.raises(ledger.LedgerSignal):
            _led(tmp_path).reserve("1", run_id=ID_A, now=MAR)
        after = _mask_now()
    finally:
        _with_hook(None)
        signal.pthread_sigmask(signal.SIG_SETMASK, old)
    assert _lock_is_free(tmp_path)                              # the fd was closed on the way out
    assert not ({signal.SIGTERM, signal.SIGHUP} & set(after))   # and the mask is not left blocked
    kinds = [(r["kind"], r.get("reason")) for r in _rows(tmp_path)]
    if stage == "reserve-locked":
        assert kinds == []                                      # nothing was written before the signal
    else:                                                       # durable open is settled at full belt
        assert kinds == [("open", None), ("settle", "signal")]
        assert Decimal(_rows(tmp_path)[1]["spent"]) == Decimal("1")


@pytest.mark.parametrize("stage", ["lock-acquired", "lock-releasing"])
def test_smoke_signal_around_lock_acquire_and_release_does_not_leak_the_lock(tmp_path, stage):
    """SMOKE test: timing-dependent real-signal delivery; the deterministic seam tests
    (test_signals_are_blocked_and_lock_held_at_the_acquire_and_release_seams and
    test_handler_running_inside_the_critical_section_releases_the_lock) are the system of record."""
    def raiser(signum, frame):
        raise ledger.LedgerSignal(signum)
    old = signal.signal(signal.SIGTERM, raiser)
    sent = []

    def hook(name):
        if name == stage and not sent:
            sent.append(1)
            # thread-directed: a process-directed kill may land on another thread (xdist workers have
            # several), whose pending flag lets the handler run on the main thread despite its mask
            signal.pthread_kill(threading.get_ident(), signal.SIGTERM)
    ledger._hook_in_critical_section = hook
    try:
        try:
            _led(tmp_path).reserve("1", now=MAR)
        except ledger.LedgerSignal:
            pass
        assert sent
    finally:
        ledger._hook_in_critical_section = None
        signal.signal(signal.SIGTERM, old)
    assert _lock_is_free(tmp_path)                              # a leaked fd would still hold the flock


# ---- exit status holds even if the signal lands after the settle (review finding 4)


def test_signal_after_settle_before_handlers_restored_still_exits_128_plus_signum(tmp_path):
    proc, _ = _spawn(tmp_path, "seam-exit", lock_timeout="1.0")
    rc, _, err = _finish(proc, timeout=15)
    assert rc == 128 + signal.SIGTERM, err                      # not a raw LedgerSignal traceback (rc 1)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    assert [r["kind"] for r in _rows(tmp_path, "spend-%s.jsonl" % month)] == ["open", "settle"]


# --------------------------------------------------------------------------- Task B: lock, reserve, settle


def test_reserve_writes_open_row_before_return(tmp_path, monkeypatch):
    events = []
    real_write, real_fsync = os.write, ledger._fsync
    monkeypatch.setattr(ledger.os, "write", lambda fd, b: (events.append("write"), real_write(fd, b))[1])
    monkeypatch.setattr(ledger, "_fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    led = _led(tmp_path)
    res = led.reserve("4.00", now=MAR)
    # the row is on disk and durable (write then fsync) by the time the handle exists
    assert events[0] == "write" and "fsync" in events
    assert events.count("fsync") == 2                           # the file, plus the directory entry (new file)
    rows = _rows(tmp_path)
    assert len(rows) == 1 and rows[0]["kind"] == "open" and rows[0]["id"] == res.id
    assert rows[0]["belt"] == "4.00" and isinstance(rows[0]["belt"], str)
    assert re.fullmatch(r"[0-9a-f]{16}", rows[0]["id"])
    assert re.fullmatch(r"2026-03-15T12:00:00Z", rows[0]["t"]) and isinstance(rows[0]["pid"], int)
    assert (tmp_path / "ledger" / MARCH_FILE).stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "ledger").stat().st_mode & 0o777 == 0o700
    events.clear()
    led.reserve("1", now=MAR)
    assert events.count("fsync") == 1                           # existing file: no directory fsync


def test_fsync_prefers_full_fsync_where_the_platform_has_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ledger.os, "fsync", lambda fd: calls.append("os.fsync"))
    monkeypatch.setattr(ledger.fcntl, "fcntl", lambda fd, op, *a: calls.append("fcntl"))
    fd = os.open(str(tmp_path), os.O_RDONLY)
    try:
        ledger._fsync(fd)
    finally:
        os.close(fd)
    if hasattr(ledger.fcntl, "F_FULLFSYNC"):
        assert calls == ["fcntl"]
        calls.clear()

        def boom(fd, op, *a):
            raise OSError("not supported here")
        monkeypatch.setattr(ledger.fcntl, "fcntl", boom)
        fd = os.open(str(tmp_path), os.O_RDONLY)
        try:
            ledger._fsync(fd)
        finally:
            os.close(fd)
        assert calls == ["os.fsync"]                            # falls back rather than skipping the sync
    else:
        assert calls == ["os.fsync"]


def test_settle_appends_to_reservation_month(tmp_path):
    led = _led(tmp_path)
    res = led.reserve("4.00", now=MAR)
    led.settle(res, "1.25", now=MAR + timedelta(minutes=5))
    rows = _rows(tmp_path)
    assert [r["kind"] for r in rows] == ["open", "settle"]
    assert rows[1]["id"] == res.id and rows[1]["spent"] == "1.25" and rows[1]["t"] == "2026-03-15T12:05:00Z"
    assert "reason" not in rows[1] and res.settled


CHILD = r"""
import importlib.util, json, os, signal, sys, time
spec = importlib.util.spec_from_file_location("ledger_child", sys.argv[1])
m = importlib.util.module_from_spec(spec); sys.modules["ledger_child"] = m; spec.loader.exec_module(m)
ledger_dir, marker, mode, cap, belt, lt = sys.argv[2:8]
led = m.Ledger(ledger_dir, cap, lock_timeout_s=float(lt))

def ready(r):
    with open(marker, "w") as fh:
        fh.write(json.dumps({"id": r.id, "month": r.month, "belt": str(r.belt)}))

def wait_forever():
    end = time.monotonic() + 20
    while time.monotonic() < end:
        time.sleep(0.05)

def hook_on(stage, sig, every):
    state = {"n": 0}
    def h(s):
        if s == stage and (every or state["n"] == 0):
            state["n"] += 1
            os.kill(os.getpid(), sig)
    m._hook_in_critical_section = h

if mode == "plain":
    r = led.reserve(belt); ready(r); wait_forever()
elif mode == "held":
    with led.reserved(belt) as r:
        ready(r); wait_forever()
elif mode == "try":
    r = led.reserve(belt)
    print(json.dumps({"type": type(r).__name__, "code": getattr(r, "code", None)}))
elif mode == "race":
    while not os.path.exists(marker):
        time.sleep(0.005)
    r = led.reserve(belt)
    print(json.dumps({"type": type(r).__name__, "code": getattr(r, "code", None)}))
elif mode == "seam-reserve-locked":
    hook_on("reserve-locked", signal.SIGTERM, False)
    with led.reserved(belt) as r:
        wait_forever()
elif mode == "seam-reserve-written":
    hook_on("reserve-written", signal.SIGTERM, False)
    with led.reserved(belt) as r:
        wait_forever()
elif mode in ("seam-settle-once", "seam-settle-every"):
    hook_on("settle-locked", signal.SIGTERM, mode == "seam-settle-every")
    with led.reserved(belt) as r:
        led.settle(r, "1.00")
        wait_forever()
elif mode == "seam-exit":
    hook_on("reserved-exit", signal.SIGTERM, False)
    with led.reserved(belt) as r:
        led.settle(r, "1.00")
elif mode == "no-fcntl":
    sys.exit(3)
"""


def _spawn(tmp_path, mode, cap="10.00", belt="4.00", lock_timeout="1.0"):
    marker = tmp_path / ("marker-%s" % mode)
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", CHILD, str(LEDGER), str(tmp_path / "ledger"), str(marker), mode, cap, belt,
         lock_timeout], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc, marker


def _finish(proc, timeout=20):
    """Bounded wait: a hung child FAILS the test; it never hangs the suite."""
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("child did not exit within %ss (hang or deadlock)" % timeout)
    return proc.returncode, out, err


def _wait_marker(proc, marker, timeout=20):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if marker.exists() and marker.read_text():
            return json.loads(marker.read_text())
        if proc.poll() is not None:
            break
        time.sleep(0.02)
    proc.kill()
    out, err = proc.communicate()
    pytest.fail("child never reported ready: rc=%s err=%s" % (proc.returncode, err[-400:]))


def test_lock_timeout_is_not_run_deterministic(tmp_path):
    led = _led(tmp_path)
    with led._locked() as got:                                  # this process holds the lock on purpose
        assert got is True
        other = ledger.Ledger(tmp_path / "ledger", "10.00", lock_timeout_s=0.2)
        with other._locked() as again:                          # a second open file description conflicts
            assert again is False
        proc, _ = _spawn(tmp_path, "try", lock_timeout="0.3")
        rc, out, err = _finish(proc)
    assert rc == 0, err
    assert json.loads(out) == {"type": "NotRun", "code": "lock-timeout"}
    assert _rows(tmp_path) == []                                # nothing written without the lock


def test_two_process_race_smoke(tmp_path):
    """SMOKE test only (see the module docstring): cap fits one belt, two racers, exactly one wins."""
    procs = [_spawn(tmp_path, "race", cap="4.00", belt="4.00", lock_timeout="5.0") for _ in range(2)]
    pathlib.Path(procs[0][1]).write_text("go")
    for _, marker in procs:
        pathlib.Path(marker).write_text("go")
    results = []
    for proc, _ in procs:
        rc, out, err = _finish(proc)
        assert rc == 0, err
        results.append(json.loads(out))
    assert sorted(r["type"] for r in results) == ["NotRun", "Reservation"]
    assert [r["code"] for r in results if r["type"] == "NotRun"] == ["budget"]
    files = list((tmp_path / "ledger").glob("spend-*.jsonl"))
    assert len(files) == 1 and len(files[0].read_text().splitlines()) == 1


def test_nonposix_refuses_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "fcntl", None)
    led = _led(tmp_path)
    with pytest.raises(ledger.LedgerRefusal) as exc:
        led.reserve("1", now=MAR)
    assert exc.value.code == "unsupported-platform"
    with pytest.raises(ledger.LedgerRefusal) as exc:
        ledger.settle_orphan(tmp_path / "ledger", "2026-03", ID_A, "0")
    assert exc.value.code == "unsupported-platform"
    assert not (tmp_path / "ledger").exists()


def test_import_succeeds_without_fcntl(tmp_path):
    code = ("import importlib.util, sys; sys.modules['fcntl'] = None\n"
            "spec = importlib.util.spec_from_file_location('l', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
            "sys.modules['l'] = m; spec.loader.exec_module(m)\n"
            "try:\n    m.Ledger(sys.argv[2], '5').reserve('1')\nexcept m.LedgerRefusal as e:\n    print(e.code)\n")
    done = subprocess.run([sys.executable, "-I", "-c", code, str(LEDGER), str(tmp_path / "d")],
                          capture_output=True, text=True, timeout=30)
    assert done.stdout.strip() == "unsupported-platform", done.stderr
    assert not (tmp_path / "d").exists()


def test_lock_file_unopenable_refuses(tmp_path):
    (tmp_path / "ledger" / "spend.lock").mkdir(parents=True)    # a directory where the lock file should be
    with pytest.raises(ledger.LedgerRefusal) as exc:
        _led(tmp_path).reserve("1", now=MAR)
    assert exc.value.code == "lock-unavailable"
    assert not list((tmp_path / "ledger").glob("spend-*.jsonl"))


def test_fsync_failure_returns_no_handle(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full (fake)")
    monkeypatch.setattr(ledger.os, "fsync", boom)
    if hasattr(ledger.fcntl, "F_FULLFSYNC"):
        monkeypatch.setattr(ledger.fcntl, "fcntl", boom)
    led = _led(tmp_path)
    with pytest.raises(ledger.LedgerRefusal) as exc:
        led.reserve("4.00", now=MAR)
    assert exc.value.code == "write-failed"
    monkeypatch.undo()
    # fail closed: a row may have reached the page cache without a durable ack, so it counts at full belt
    assert _counted(tmp_path) == Decimal("4.00")


def test_settle_orphan_releases_belt(tmp_path):
    led = _led(tmp_path, cap="5.00")
    dead = led.reserve("5.00", now=MAR)                         # handle dropped: as if its process was killed
    assert led.reserve("1", now=MAR).code == "budget"
    ledger.settle_orphan(tmp_path / "ledger", dead.month, dead.id, "0.00", now=MAR)
    rows = _rows(tmp_path)
    assert rows[-1]["kind"] == "settle" and rows[-1]["reason"] == "orphan" and rows[-1]["spent"] == "0.00"
    assert led.reserve("5.00", now=MAR).belt == Decimal("5.00")
    for bad_id in (ID_B, dead.id):                              # unknown id; already settled
        with pytest.raises(ledger.LedgerRefusal) as exc:
            ledger.settle_orphan(tmp_path / "ledger", dead.month, bad_id, "0")
        assert exc.value.code == "not-an-unsettled-open"
    with pytest.raises(ledger.LedgerRefusal):
        ledger.settle_orphan(tmp_path / "ledger", "2026-13", dead.id, "0")


def test_settle_orphan_does_not_repair_corruption(tmp_path):
    led = _led(tmp_path)
    dead = led.reserve("4.00", now=MAR)
    with open(tmp_path / "ledger" / MARCH_FILE, "ab") as fh:
        fh.write(b'{"kind":"open","id":"' + ID_B.encode())      # torn tail from a crash mid-append, no newline
    with pytest.raises(ledger.LedgerRefusal) as exc:
        ledger.settle_orphan(tmp_path / "ledger", dead.month, dead.id, "0")
    assert exc.value.code == "ledger-corrupt"
    assert led.reserve("1", now=MAR).code == "ledger-corrupt"   # no self-heal: hand repair only


# --------------------------------------------------------------------------- Task C: kill, signals, corruption, rollover


def test_sigkill_between_reserve_and_settle_counts_full_belt(tmp_path):
    proc, marker = _spawn(tmp_path, "plain")
    try:
        info = _wait_marker(proc, marker)                       # written only after reserve returned
        proc.send_signal(signal.SIGKILL)
        rc, _, _ = _finish(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert rc == -signal.SIGKILL
    month_file = "spend-%s.jsonl" % info["month"]
    rows = _rows(tmp_path, month_file)
    assert [r["kind"] for r in rows] == ["open"] and rows[0]["id"] == info["id"]
    nxt = ledger.Ledger(tmp_path / "ledger", "10.00", lock_timeout_s=2.0).reserve("10.00")
    assert isinstance(nxt, ledger.Reservation) and nxt.belt == Decimal("10.00") - Decimal(info["belt"])


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_sigterm_and_sighup_settle_before_exit(tmp_path, sig):
    proc, marker = _spawn(tmp_path, "held")
    try:
        info = _wait_marker(proc, marker)
        proc.send_signal(sig)
        rc, _, err = _finish(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert rc == 128 + sig, err                                 # documented: exit status is 128 + signal number
    rows = _rows(tmp_path, "spend-%s.jsonl" % info["month"])
    assert [r["kind"] for r in rows] == ["open", "settle"]
    assert rows[1]["id"] == info["id"] and rows[1]["reason"] == "signal"
    assert Decimal(rows[1]["spent"]) == Decimal(info["belt"])   # unknown real spend: settled at the FULL belt


def test_signal_inside_critical_section_does_not_deadlock(tmp_path):
    """SIGTERM arrives while this process holds the lock. A handler that took the lock would conflict with the
    first open file description and wait out the timeout; the contract is: the handler only raises."""
    proc, _ = _spawn(tmp_path, "seam-reserve-locked", lock_timeout="1.0")
    rc, _, err = _finish(proc, timeout=15)
    assert rc == 128 + signal.SIGTERM, err
    assert _rows(tmp_path, "spend-%s.jsonl" % datetime.now(timezone.utc).strftime("%Y-%m")) == []
    led = ledger.Ledger(tmp_path / "ledger", "10.00", lock_timeout_s=0.5)
    assert isinstance(led.reserve("4.00"), ledger.Reservation)  # the lock was released


def test_signal_inside_settle_critical_section_retries_once(tmp_path):
    proc, _ = _spawn(tmp_path, "seam-settle-once", lock_timeout="1.0")
    rc, _, err = _finish(proc, timeout=15)
    assert rc == 128 + signal.SIGTERM, err
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    rows = _rows(tmp_path, "spend-%s.jsonl" % month)
    assert [r["kind"] for r in rows] == ["open", "settle"]      # exactly one settle landed
    assert rows[1]["reason"] == "signal" and Decimal(rows[1]["spent"]) == Decimal("4.00")


def test_second_signal_during_settle_fails_closed(tmp_path):
    proc, _ = _spawn(tmp_path, "seam-settle-every", lock_timeout="1.0")
    rc, _, err = _finish(proc, timeout=15)
    assert rc == 128 + signal.SIGTERM, err
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    rows = _rows(tmp_path, "spend-%s.jsonl" % month)
    assert [r["kind"] for r in rows] == ["open"]                # never settled: counts at the full belt
    raw = (tmp_path / "ledger" / ("spend-%s.jsonl" % month)).read_bytes()
    assert ledger.count_spend(raw, month, Decimal("10.00"), "x").counted == Decimal("4.00")


def test_signal_after_fsync_before_return_settles(tmp_path):
    proc, _ = _spawn(tmp_path, "seam-reserve-written", lock_timeout="1.0")
    rc, _, err = _finish(proc, timeout=15)
    assert rc == 128 + signal.SIGTERM, err
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    rows = _rows(tmp_path, "spend-%s.jsonl" % month)
    assert [r["kind"] for r in rows] == ["open", "settle"]
    assert rows[1]["id"] == rows[0]["id"] and rows[1]["reason"] == "signal"
    assert rows[1]["spent"] == rows[0]["belt"]


def test_handler_restores_previous_signal_handlers(tmp_path):
    def mine(signum, frame):
        pass
    old_term = signal.signal(signal.SIGTERM, mine)
    old_hup = signal.signal(signal.SIGHUP, mine)
    try:
        with _led(tmp_path).reserved("1", now=MAR) as res:
            assert signal.getsignal(signal.SIGTERM) is not mine
            assert signal.getsignal(signal.SIGHUP) is not mine
            _led(tmp_path).settle(res, "0.5", now=MAR)
        assert signal.getsignal(signal.SIGTERM) is mine and signal.getsignal(signal.SIGHUP) is mine
        with pytest.raises(ValueError):
            with _led(tmp_path).reserved("1", now=MAR):
                raise ValueError("boom")
        assert signal.getsignal(signal.SIGTERM) is mine and signal.getsignal(signal.SIGHUP) is mine
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGHUP, old_hup)


def test_reserved_is_noop_handler_off_main_thread(tmp_path):
    seen = {}

    def work():
        before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
        with _led(tmp_path).reserved("1", now=MAR) as res:
            seen["inside"] = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
            seen["res"] = res
        seen["before"] = before

    t = threading.Thread(target=work)
    t.start()
    t.join(20)
    assert not t.is_alive()
    assert seen["inside"] == seen["before"]                     # no handler installed off the main thread
    assert isinstance(seen["res"], ledger.Reservation)


def test_reserved_settles_unsettled_run_at_full_belt_on_any_exit(tmp_path):
    led = _led(tmp_path)
    with led.reserved("4.00", now=MAR) as res:                  # normal exit with no caller settle
        pass
    assert res.settled
    with pytest.raises(ValueError):
        with led.reserved("3.00", now=MAR):
            raise ValueError("boom")
    rows = _rows(tmp_path)
    assert [r["kind"] for r in rows] == ["open", "settle", "open", "settle"]
    assert rows[1]["reason"] == "exit" and rows[1]["spent"] == "4.00"
    assert rows[3]["reason"] == "exit" and rows[3]["spent"] == "3.00"
    with led.reserved("1.00", now=MAR) as res:                  # caller settles: no second row
        led.settle(res, "0.40", now=MAR)
    assert [r["kind"] for r in _rows(tmp_path)][-2:] == ["open", "settle"]
    nr = _led(tmp_path, cap="0.5").reserved("1", now=MAR)       # NOT RUN comes back as a value
    with nr as got:
        assert isinstance(got, ledger.NotRun) and got.code == "budget"


def _bad_cases():
    ok = _open()
    return [
        ("garbage-middle", ok + SENTINEL.encode() + b"{{\n" + _open(ID_B), 2),
        ("torn-final-line", ok + b'{"kind":"open","id":"' + SENTINEL.encode(), 2),
        ("json-non-object", ok + b'["' + SENTINEL.encode() + b'"]\n', 2),
        ("unknown-kind", ok + _row(kind=SENTINEL, id=ID_B, belt="1", t="2026-03-10T00:00:00Z"), 2),
        ("missing-field", ok + _row(kind="open", id=ID_B, t="2026-03-10T00:00:00Z"), 2),
        ("bad-amount", ok + _open(ID_B, SENTINEL), 2),
        ("nan-amount", ok + _open(ID_B, "NaN"), 2),
        ("negative-amount", ok + _open(ID_B, "-1.00"), 2),
        ("float-amount", ok + _row(kind="open", id=ID_B, belt=1.5, t="2026-03-10T00:00:00Z", pid=1), 2),
        ("interior-blank-line", ok + b"\n" + _open(ID_B), 2),
        ("wrong-month-open", ok + _open(ID_B, "1.00", "2025-01-10T00:00:00Z"), 2),
        ("bad-timestamp", ok + _open(ID_B, "1.00", SENTINEL), 2),
        ("bad-id", ok + _open(SENTINEL, "1.00"), 2),
        ("duplicate-open-id", ok + _open(ID_A, "1.00"), 2),
        ("bad-settle-amount", ok + _settle(spent=SENTINEL), 2),
        ("bad-reason", ok + _row(kind="settle", id=ID_A, spent="1", t="2026-03-10T00:00:00Z", reason=SENTINEL), 2),
        ("invalid-utf8", ok + b"\xff\xfe\n", 2),
        ("deeply-nested-json", ok + b"[" * 200000 + b"\n", 2),
        ("nan-literal", ok + b'{"kind":"open","id":"%s","belt":"1","t":"2026-03-10T00:00:00Z","pid":NaN}\n' % ID_B.encode(), 2),
    ]


@pytest.mark.parametrize("name,content,line", _bad_cases(), ids=[c[0] for c in _bad_cases()])
def test_corrupt_line_fails_closed(tmp_path, name, content, line):
    d = tmp_path / "ledger"
    d.mkdir()
    (d / MARCH_FILE).write_bytes(content)
    led = _led(tmp_path, cap="10.00")
    got = _count(content)
    assert got.corrupt and got.counted == Decimal("10.00")      # the full cap, not the sum
    nr = led.reserve("1.00", now=MAR)
    assert isinstance(nr, ledger.NotRun) and nr.code == "ledger-corrupt"
    assert nr.counted == Decimal("10.00") and nr.cap == Decimal("10.00")
    assert any(("%s:%d" % (MARCH_FILE, line)) in r for r in nr.reports), nr.reports
    assert not any(SENTINEL in r for r in nr.reports)           # names file, line and reason; never the content
    assert (d / MARCH_FILE).read_bytes() == content             # nothing written, nothing repaired


def test_month_rollover_starts_fresh_file(tmp_path):
    led = _led(tmp_path, cap="5.00")
    end_mar = datetime(2026, 3, 31, 23, 59, 30, tzinfo=timezone.utc)
    start_apr = datetime(2026, 4, 1, 0, 0, 1, tzinfo=timezone.utc)
    old = led.reserve("4.00", now=end_mar)
    assert old.month == "2026-03"
    assert led.reserve("1", now=end_mar).belt == Decimal("1")   # March is clamped: 4 + 1 = cap
    new = led.reserve("5.00", now=start_apr)
    assert new.month == "2026-04" and new.belt == Decimal("5.00")   # unsettled March open does not count in April
    files = sorted(p.name for p in (tmp_path / "ledger").glob("spend-*.jsonl"))
    assert files == ["spend-2026-03.jsonl", "spend-2026-04.jsonl"]
    led.settle(old, "1.00", now=start_apr)                      # settled after midnight, lands in ITS month
    assert [r["kind"] for r in _rows(tmp_path, "spend-2026-04.jsonl")] == ["open"]
    assert [r["kind"] for r in _rows(tmp_path, "spend-2026-03.jsonl")] == ["open", "open", "settle"]
    assert _rows(tmp_path, "spend-2026-03.jsonl")[2]["t"] == "2026-04-01T00:00:01Z"
    # a corrupt old month never blocks the new one
    (tmp_path / "ledger" / "spend-2026-02.jsonl").write_bytes(b"garbage\n")
    assert isinstance(_led(tmp_path, cap="5.00").reserve("1", now=datetime(2026, 5, 1, tzinfo=timezone.utc)),
                      ledger.Reservation)


def test_now_must_be_aware_and_is_converted_to_utc(tmp_path):
    led = _led(tmp_path)
    with pytest.raises(ledger.LedgerRefusal) as exc:
        led.reserve("1", now=datetime(2026, 3, 15, 12, 0, 0))
    assert exc.value.code == "naive-datetime"
    assert not (tmp_path / "ledger").exists()
    res = led.reserve("1", now=datetime(2026, 4, 1, 2, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))))
    assert res.month == "2026-03"                               # 20:30 UTC on 31 March
    with pytest.raises(ledger.LedgerRefusal) as exc:
        led.settle(res, "0", now=datetime(2026, 4, 1))
    assert exc.value.code == "naive-datetime"


# --------------------------------------------------------------------------- Task C5: write surface and no-model


WRITE_OS = {"write", "fsync", "fdatasync", "replace", "rename", "makedirs", "mkdir", "unlink", "remove", "rmdir",
            "truncate", "ftruncate", "symlink", "link", "chmod"}
WRITE_PATH = {"write_text", "write_bytes", "mkdir", "unlink", "touch", "rmdir", "rename", "symlink_to", "chmod"}

# Every write site under evals/regression that tools/readiness/write_surface.py cannot see (it is blind to
# open(...), os.open, os.write and os.fsync), keyed (file, function, call) -> (count, why it is allowed).
DOCUMENTED_WRITES = {
    ("evals/regression/record.py", "write_atomic", ".mkdir"): (1, "creates the output directory of the record"),
    ("evals/regression/record.py", "write_atomic", ".write_text"): (1, "writes the temp file of the atomic record write"),
    ("evals/regression/record.py", "write_atomic", "os.replace"): (1, "atomic rename of the temp file onto the record"),
    ("evals/regression/record.py", "write_atomic", ".unlink"): (1, "removes its own leftover temp file"),
    ("evals/regression/live.py", "write_row", ".mkdir"): (1, "creates the results directory for one NOT RUN row"),
    ("evals/regression/live.py", "write_row", ".write_text"): (1, "writes the temp file of the atomic row write"),
    ("evals/regression/live.py", "write_row", "os.replace"): (1, "atomic rename of the temp file onto the row"),
    ("evals/regression/live.py", "write_row", ".unlink"): (1, "removes its own leftover temp file"),
    ("evals/regression/mutators.py", "_write_text", ".write_text"): (1, "the one write of a mutated file, only under the --root copy it is told"),
    ("evals/regression/spend_ledger.py", "Ledger._acquire", "os.open"): (1, "opens or creates the lock file (0600), never data"),
    ("evals/regression/spend_ledger.py", "Ledger._ensure_dir", "os.makedirs"): (1, "the ONE place the ledger directory is created (0700)"),
    ("evals/regression/spend_ledger.py", "_append", "os.open"): (1, "opens the month file O_APPEND|O_CREAT (0600) for the one row"),
    ("evals/regression/spend_ledger.py", "_append", "os.write"): (1, "the single write of one whole row, under the lock"),
    ("evals/regression/spend_ledger.py", "_fsync", "os.fsync"): (1, "makes the row durable before reserve returns"),
}


def _call_label(node):
    f = node.func
    if isinstance(f, ast.Name) and f.id == "open":
        return "open" if _write_mode(node, 1) else None
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else None
        if base == "os" and f.attr == "open":
            flags = node.args[1] if len(node.args) > 1 else None
            read_only = isinstance(flags, ast.Attribute) and flags.attr == "O_RDONLY"
            return None if read_only else "os.open"
        if base == "os" and f.attr in WRITE_OS:
            return "os." + f.attr
        if base == "shutil":
            return "shutil." + f.attr
        if base == "io" and f.attr == "open":
            return "io.open" if _write_mode(node, 1) else None
        if f.attr == "open" and base != "os":
            return ".open" if _write_mode(node, 0) else None
        if f.attr in WRITE_PATH and base != "os":
            return "." + f.attr
    return None


def _write_mode(node, pos):
    mode = None
    if len(node.args) > pos:
        mode = node.args[pos]
    for kw in node.keywords:
        if kw.arg == "mode":
            mode = kw.value
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return any(c in mode.value for c in "wax+")
    return True                                                  # a mode we cannot read is treated as a write


def find_writes(root):
    root = pathlib.Path(root)
    found = {}
    for path in sorted((root / "evals" / "regression").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def walk(node, scope):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.Call):
                    label = _call_label(child)
                    if label:
                        key = (rel, ".".join(scope) or "<module>", label)
                        found[key] = found.get(key, 0) + 1
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    walk(child, scope + [child.name])
                else:
                    walk(child, scope)
        walk(tree, [])
    return found


def undocumented_writes(root, documented):
    found = find_writes(root)
    problems = []
    for key, n in sorted(found.items()):
        want = documented.get(key, (0, ""))[0]
        if n != want:
            problems.append("undocumented write: %s %s %s x%d (documented x%d): add it to DOCUMENTED_WRITES with a "
                            "reason" % (key + (n, want)))
    for key in sorted(set(documented) - set(found)):
        problems.append("stale DOCUMENTED_WRITES row (no such write any more): %s" % (key,))
    return problems


def test_every_write_under_evals_regression_is_documented():
    assert undocumented_writes(ROOT, DOCUMENTED_WRITES) == []
    assert all(reason for _, reason in DOCUMENTED_WRITES.values())


def test_write_detector_sees_what_write_surface_py_cannot(tmp_path):
    reg = tmp_path / "evals" / "regression"
    reg.mkdir(parents=True)
    (reg / "x.py").write_text(
        "import os\n"
        "def a(p):\n    with open(p, 'a') as fh:\n        fh.write('x')\n"
        "def b(fd):\n    os.write(fd, b'x')\n    os.fsync(fd)\n    os.open(p, os.O_RDONLY)\n"
        "def c(p):\n    open(p)\n    open(p, 'rb')\n    os.open(p, os.O_WRONLY)\n")
    found = find_writes(tmp_path)
    assert found == {("evals/regression/x.py", "a", "open"): 1, ("evals/regression/x.py", "b", "os.write"): 1,
                     ("evals/regression/x.py", "b", "os.fsync"): 1, ("evals/regression/x.py", "c", "os.open"): 1}
    msgs = undocumented_writes(tmp_path, {})
    assert len(msgs) == 4 and all("undocumented write" in m for m in msgs)


def test_ledger_writes_are_all_documented_by_name():
    names = {k[1] for k in DOCUMENTED_WRITES if k[0] == "evals/regression/spend_ledger.py"}
    assert names >= {"_append", "Ledger._ensure_dir", "_fsync"}


def _ledger_tree():
    return ast.parse(LEDGER.read_text(encoding="utf-8"))


def test_ledger_starts_no_model_and_no_network():
    banned_mods = {"subprocess", "socket", "urllib", "http", "ssl", "multiprocessing", "asyncio", "requests",
                   "shutil", "ctypes"}
    for node in ast.walk(_ledger_tree()):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & banned_mods, ast.dump(node)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned_mods, ast.dump(node)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            who, attr = node.value.id, node.attr
            assert not (who == "os" and (attr in {"system", "popen", "fork", "forkpty", "environ", "getenv",
                                                  "environb"} or attr.startswith(("exec", "spawn")))), (who, attr)
            assert not (who == "signal" and attr in {"SIGSTOP", "SIGCONT"}), (who, attr)


def test_ledger_imports_only_stdlib_and_nothing_from_slice_one():
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(_ledger_tree()):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import"
            mods = [(node.module or "").split(".")[0]]
        for mod in mods:
            assert mod in stdlib, "non-stdlib import %s" % mod
            assert mod not in {"record", "check", "live"}


def test_docstring_gestures_name_real_tests():
    doc = ledger.__doc__
    assert "python3.12 -m pytest tests/test_regression_ledger.py -q" in doc
    nodes = re.findall(r"python3\.12 -m pytest tests/test_regression_ledger\.py::(\w+) -q", doc)
    assert len(nodes) >= 5
    for node in nodes:
        assert callable(globals().get(node)), "docstring gesture names a test that does not exist: " + node


def test_settle_refuses_malformed_reservation_before_writing(tmp_path):
    led = _led(tmp_path)
    assert isinstance(led.reserve("1.00", now=MAR), ledger.Reservation)
    before = (tmp_path / "ledger" / MARCH_FILE).read_bytes()
    for bad in (ledger.Reservation("XYZ", "2026-03", Decimal("1")),
                ledger.Reservation(ID_B, "2026-13", Decimal("1"))):
        with pytest.raises(ledger.LedgerRefusal):
            led.settle(bad, "0.50", now=MAR)
        assert not bad.settled
    assert (tmp_path / "ledger" / MARCH_FILE).read_bytes() == before     # nothing written
    assert not _count(before).corrupt

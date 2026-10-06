"""#425: a timed-out autowatch drive must take its WHOLE process tree down, not just the direct child.

The documented invocation is ``python3 -m pytest tests/test_autowatch_process_group.py -q``.

Deterministic subprocess-tree controls with REAL processes (no mocked Popen): the driven command is
a small Python "model" that starts a grandchild, waits until the grandchild has written its own pid
to a file, then sleeps far past the drive's timeout. The pre-#425 `_run_drive` called
`proc.kill()` on the direct child only, so in the timeout cases the grandchild survived (or, when it
held the stdout pipe, the drain after the kill blocked until it exited). Every test fails against
that code or against a mutant removing the mechanism it guards (PR body for #425); two --
`[tick-group]` SIGKILL and the normal-finish case -- pass against the old code by design, because
they guard behaviour it already had and the new code must keep. The grandchild is confirmed alive
BEFORE the timeout fires (its pid file exists, asserted, never skipped), so the only question
asked is whether the timeout handling reaches it.

Every test kills whatever it recorded in a `finally`, so a red run leaks no process.
"""
import importlib.util
import os
import pathlib
import shlex
import signal
import subprocess
import sys
import threading
import time

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"

pytestmark = pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups only")


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


autowatch = _mod("autowatch")


@pytest.fixture(autouse=True)
def _operator_trusts_the_harness(monkeypatch):
    """#707: a non-default drive command needs the Git-local opt-in; these controls ARE the operator."""
    monkeypatch.setattr(autowatch.shell_policy, "repository_shell_commands_allowed", lambda p: True)

#: The driven "model". argv: <dir> <grandchild-mode> [ignored prompt]. Writes `child.pid`, starts a
#: grandchild that writes `grandchild.pid`, waits for that file, then sleeps past any timeout.
CHILD = r'''
import os, subprocess, sys, time
d, mode = sys.argv[1], sys.argv[2]
open(os.path.join(d, "child.pid.tmp"), "w").write(str(os.getpid()))
os.replace(os.path.join(d, "child.pid.tmp"), os.path.join(d, "child.pid"))
gc = (
    "import os, signal, sys, time\n"
    "d, mode = sys.argv[1], sys.argv[2]\n"
    "if mode == 'ignore_term':\n"
    "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "if mode == 'term_marker':\n"
    "    def on_term(*_):\n"
    "        open(os.path.join(d, 'grandchild.sigterm'), 'w').write('1')\n"
    "        os._exit(0)\n"
    "    signal.signal(signal.SIGTERM, on_term)\n"
    "open(os.path.join(d, 'grandchild.pid.tmp'), 'w').write(str(os.getpid()))\n"
    "os.replace(os.path.join(d, 'grandchild.pid.tmp'), os.path.join(d, 'grandchild.pid'))\n"
    "time.sleep(30)\n"
)
quiet = {} if mode == "pipe" else {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                                    "stderr": subprocess.DEVNULL}
if mode == "setsid_pipe":       # escapes the group AND holds the stdout pipe: only a bound helps
    quiet = {"start_new_session": True}
subprocess.Popen([sys.executable, "-c", gc, d, mode], **quiet)
deadline = time.time() + 20
while not os.path.exists(os.path.join(d, "grandchild.pid")) and time.time() < deadline:
    time.sleep(0.01)
sys.stdout.write("ready\n"); sys.stdout.flush()
time.sleep(30)
'''

#: A tick-like caller: loads autowatch and drives the CHILD with a long timeout, so only a signal
#: delivered to THIS process can end the drive early.
#: argv: <autowatch.py> <drive cmd> [grace seconds] [handler: none|flag|catchint]. `flag` installs the
#: caller's OWN SIGTERM handler, one that only records the signal and does not exit (the shape of a
#: graceful daemon); the tick then writes `returned` next to its own script and exits 0.
TICK = r'''
import importlib.util, os, pathlib, signal, sys
spec = importlib.util.spec_from_file_location("autowatch", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.shell_policy.repository_shell_commands_allowed = lambda p: True   # #707: the harness is the operator
if len(sys.argv) > 3:
    m.DRIVE_TERM_GRACE_SECONDS = float(sys.argv[3])
    m.DRIVE_REAP_SECONDS = 0.5      # the tick may not sit out the lifeline sentinel's own sweep
here = pathlib.Path(__file__).parent
# Python leaves SIGINT ignored when it inherits SIG_IGN (any non-interactive background launch,
# CI included), which would make the SIGINT below a no-op and the second-signal test vacuous.
signal.signal(signal.SIGINT, signal.default_int_handler)
if len(sys.argv) > 4 and sys.argv[4] == "flag":
    signal.signal(signal.SIGTERM, lambda *a: (here / "handler-ran").write_text("1"))
if len(sys.argv) > 4 and sys.argv[4] == "catchint":
    try:
        m._run_drive(sys.argv[2], "", ".", dict(os.environ), 60)
    except KeyboardInterrupt:
        (here / "caught-interrupt").write_text("1")
    import time; time.sleep(5)
    (here / "survived").write_text("1")
    sys.exit(0)
m._run_drive(sys.argv[2], "", ".", dict(os.environ), 60)
(here / "returned").write_text("1")
'''

TIMEOUT = 3


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                          capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith("Z")       # a zombie is dead, just not yet reaped


def _gone_within(pid, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _read_pid(path):
    return int(path.read_text()) if path.exists() else None


def _cleanup(*pids):
    for pid in pids:
        if pid and _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _exit_code(proc, seconds):
    """The tick's exit code, or a failing ASSERTION if it has not exited in time -- a tick that
    never returns is this file's red, and it must read as one, not as a TimeoutExpired error."""
    try:
        return proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        raise AssertionError(f"the tick did not exit within {seconds}s") from None


def _cmd(tmp_path, mode):
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    return " ".join(shlex.quote(a) for a in (sys.executable, str(script), str(tmp_path), mode))


@pytest.fixture
def short_grace(monkeypatch):
    monkeypatch.setattr(autowatch, "DRIVE_TERM_GRACE_SECONDS", 1, raising=False)
    monkeypatch.setattr(autowatch, "DRIVE_REAP_SECONDS", 2, raising=False)


@pytest.mark.parametrize("mode", ["plain", "ignore_term"])
def test_timeout_kills_the_grandchild_not_just_the_direct_child(tmp_path, mode, short_grace):
    """`plain`: SIGTERM to the group suffices. `ignore_term`: the grandchild ignores SIGTERM, so
    it takes the SIGKILL escalation -- or, if that were missing, the lifeline sentinel's own sweep
    when the call returns, which is why removing only the in-process SIGKILL does not turn this
    red (the second-signal test, with its short reap bound, catches that)."""
    gc = child = None
    try:
        start = time.time()
        code, out = autowatch._run_drive(_cmd(tmp_path, mode), "", str(tmp_path),
                                         dict(os.environ), TIMEOUT)
        elapsed = time.time() - start
        gc, child = _read_pid(tmp_path / "grandchild.pid"), _read_pid(tmp_path / "child.pid")
        assert gc is not None, "precondition: the grandchild never started before the timeout"
        assert code == 124 and "timed out" in out
        assert _gone_within(child), "the direct child survived the timeout"
        assert _gone_within(gc), "the grandchild survived the timeout (direct-child-only kill)"
        # bounded: timeout + grace + reap, plus generous scheduling slack
        assert elapsed < TIMEOUT + 1 + 2 + 5
    finally:
        _cleanup(gc, child)


def test_timeout_returns_even_when_a_grandchild_holds_the_stdout_pipe(tmp_path, short_grace):
    """The pre-#425 drain after `proc.kill()` blocked until every holder of the pipe exited, so a
    grandchild that inherited stdout made the timeout unbounded. Run in a thread with a deadline so
    the red run fails instead of hanging the suite."""
    result = {}

    def drive():
        result["r"] = autowatch._run_drive(_cmd(tmp_path, "pipe"), "", str(tmp_path),
                                           dict(os.environ), TIMEOUT)

    t = threading.Thread(target=drive, daemon=True)
    gc = child = None
    try:
        t.start()
        t.join(TIMEOUT + 1 + 2 + 5)
        gc, child = _read_pid(tmp_path / "grandchild.pid"), _read_pid(tmp_path / "child.pid")
        assert gc is not None, "precondition: the grandchild never started before the timeout"
        assert not t.is_alive(), "_run_drive did not return: the drain waited on the grandchild"
        assert result["r"][0] == 124
        assert _gone_within(gc)
    finally:
        _cleanup(gc, child)
        t.join(10)


def test_sigterm_to_the_tick_mid_drive_takes_the_model_tree_down(tmp_path):
    """The drive runs in its own session, so a signal addressed to the tick no longer reaches the
    model through a shared process group. The tick must terminate the model's group itself, then
    still die by the signal it received."""
    tick = tmp_path / "tick.py"
    tick.write_text(TICK)
    proc = subprocess.Popen([sys.executable, str(tick), str(S / "autowatch.py"),
                             _cmd(tmp_path, "plain")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    gc = child = None
    try:
        deadline = time.time() + 20
        while not (tmp_path / "grandchild.pid").exists() and time.time() < deadline:
            time.sleep(0.02)
        gc, child = _read_pid(tmp_path / "grandchild.pid"), _read_pid(tmp_path / "child.pid")
        assert gc is not None, "precondition: the grandchild never started"
        proc.send_signal(signal.SIGTERM)
        rc = _exit_code(proc, 30)
        assert rc == -signal.SIGTERM, "the tick must still die by the signal it received"
        assert _gone_within(child), "the direct child outlived the SIGTERMed tick"
        assert _gone_within(gc), "the grandchild outlived the SIGTERMed tick"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        _cleanup(gc, child)


@pytest.mark.parametrize("how", ["no-killpg", "win32"])
def test_refuses_loudly_where_the_group_guarantee_cannot_be_made(tmp_path, monkeypatch, capsys, how):
    marker = tmp_path / "spawned"
    if how == "no-killpg":
        monkeypatch.delattr(autowatch.os, "killpg")
    else:
        monkeypatch.setattr(autowatch.sys, "platform", "win32")
    cmd = " ".join(shlex.quote(a) for a in (
        sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"))
    seen = []
    code, out = autowatch._run_drive(cmd, "", str(tmp_path), dict(os.environ), 5,
                                     on_spawn=seen.append)
    assert code == 2
    assert "REFUSED" in out and "REFUSED" in capsys.readouterr().err
    assert not marker.exists() and seen == [], "nothing may be spawned on a refusing host"



def _start_tick(tmp_path, mode, *extra, **popen_kwargs):
    tick = tmp_path / "tick.py"
    tick.write_text(TICK)
    proc = subprocess.Popen([sys.executable, str(tick), str(S / "autowatch.py"),
                             _cmd(tmp_path, mode), *extra],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **popen_kwargs)
    deadline = time.time() + 20
    while not (tmp_path / "grandchild.pid").exists() and time.time() < deadline:
        time.sleep(0.02)
    return proc, _read_pid(tmp_path / "grandchild.pid"), _read_pid(tmp_path / "child.pid")


@pytest.mark.parametrize("how", ["tick-only", "tick-group"])
def test_sigkill_to_the_tick_still_takes_the_model_tree_down(tmp_path, how):
    """SIGKILL cannot be trapped, and the model runs in a session of its own, so neither a SIGKILL
    to the tick nor a `killpg(<tick's group>, SIGKILL)` (what `run_with_timeout.py` does to an
    overrunning call) can reach it directly. The lifeline does: its write end lives only in the
    tick, the kernel closes it when the tick dies, and the sentinel watching it kills the model's
    group. Grace 1s, so the whole tree must be gone well inside the poll window."""
    proc, gc, child = _start_tick(tmp_path, "ignore_term", "1", start_new_session=True)
    try:
        assert gc is not None, "precondition: the grandchild never started"
        if how == "tick-only":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        _exit_code(proc, 10)
        assert _gone_within(child, 10), "the direct child outlived a SIGKILLed tick"
        assert _gone_within(gc, 10), "the grandchild outlived a SIGKILLed tick"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        _cleanup(gc, child)


def test_a_second_signal_during_termination_does_not_abort_the_escalation(tmp_path):
    """SIGTERM starts the group termination; a SIGINT landing inside the grace period must not
    cut the SIGKILL escalation short -- the trap raises once per drive and absorbs the rest.
    Grace 3s, SIGINT at +1s: squarely inside the grace wait.
    The tick itself must finish the escalation BEFORE it exits -- the grandchild (which ignores
    SIGTERM) is gone within 0.5s of the tick's exit. The tick's reap bound is 0.5s here, so it
    cannot sit out the lifeline sentinel's own later sweep (another 3s grace), which would
    otherwise mask an aborted escalation; that backstop is covered by the SIGKILL test above."""
    proc, gc, child = _start_tick(tmp_path, "ignore_term", "3")
    try:
        assert gc is not None, "precondition: the grandchild never started"
        proc.send_signal(signal.SIGTERM)
        time.sleep(1)
        proc.send_signal(signal.SIGINT)
        _exit_code(proc, 30)
        assert _gone_within(gc, 0.5), "the tick exited with its escalation cut short"
        assert _gone_within(child, 0.5), "the tick exited with its escalation cut short"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        _cleanup(gc, child)


def test_a_callers_own_sigterm_handler_still_stops_the_model_and_still_runs(tmp_path):
    """A caller that installed its own SIGTERM handler (one that records and does not exit) must
    get both: the model's group terminated, and its own handler run. Before, the trap stood aside
    and the drive ran on to its 60s timeout."""
    proc, gc, child = _start_tick(tmp_path, "plain", "1", "flag")
    try:
        assert gc is not None, "precondition: the grandchild never started"
        proc.send_signal(signal.SIGTERM)
        rc = _exit_code(proc, 15)
        assert rc == 0 and (tmp_path / "returned").exists(), "the drive did not return promptly"
        assert (tmp_path / "handler-ran").exists(), "the caller's own handler never ran"
        assert _gone_within(child) and _gone_within(gc), "the model tree outlived the SIGTERM"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        _cleanup(gc, child)


#: Race injection at the two seams a signal used to slip through: right after the model's Popen
#: returns (before its group id reaches the lifeline sentinel), and inside `_run_drive`'s final
#: cleanup. A REAL `os.kill(self, SIGTERM)` is fired from inside the seam, so the window is hit
#: every time rather than by luck. argv: <autowatch.py> <drive cmd> <spawn|restore> <out dir>.
RACE_TICK = r'''
import importlib.util, os, pathlib, signal, subprocess, sys
spec = importlib.util.spec_from_file_location("autowatch", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.shell_policy.repository_shell_commands_allowed = lambda p: True   # #707: the harness is the operator
m.DRIVE_TERM_GRACE_SECONDS = 1
seam, out = sys.argv[3], pathlib.Path(sys.argv[4])
if seam == "dead-sentinel-sigpipe":
    # A caller that restored SIGPIPE's default (a common CLI idiom); the sentinel is dead before
    # the handoff, so the write to it hits a closed pipe.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    real_start = m._start_lifeline
    def dead_start():
        sentinel, fd = real_start()
        sentinel.kill(); sentinel.wait()
        return sentinel, fd
    m._start_lifeline = dead_start
    real = subprocess.Popen
    class Recording(real):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            if kw.get("stdout") is subprocess.PIPE:
                (out / "model.pid").write_text(str(self.pid))
    m.subprocess.Popen = Recording
    result = m._run_drive(sys.argv[2], "", ".", dict(os.environ), 60)
    (out / "result").write_text(str(result[0]))
    (out / "sigpipe-restored").write_text(str(signal.getsignal(signal.SIGPIPE) is signal.SIG_DFL))
    sys.exit(0)
if seam == "spawn-dead-sentinel":
    real_start = m._start_lifeline
    def dead_start():
        sentinel, fd = real_start()
        sentinel.kill(); sentinel.wait()
        return sentinel, fd
    m._start_lifeline = dead_start
if seam == "pre-spawn":
    real_start = m._start_lifeline
    def signalled_start():
        got = real_start()
        os.kill(os.getpid(), signal.SIGTERM)          # recorded: the model must never be spawned
        return got
    m._start_lifeline = signalled_start
if seam == "restore-window":
    # Caller: SIGTERM at SIG_DFL, Ctrl-C caught. A SIGTERM is recorded mid-drive (at lifeline
    # release), and a Ctrl-C lands the instant SIGINT's handler is restored -- before delivery.
    real_end, real_signal = m._end_lifeline, signal.signal
    def end_with_term(*a, **kw):
        os.kill(os.getpid(), signal.SIGTERM)
        return real_end(*a, **kw)
    def signal_then_interrupt(signum, handler):
        previous = real_signal(signum, handler)
        if signum == signal.SIGINT and handler is signal.default_int_handler:
            os.kill(os.getpid(), signal.SIGINT)
        return previous
    m._end_lifeline = end_with_term
    m.signal.signal = signal_then_interrupt
    try:
        m._run_drive(sys.argv[2], "", ".", dict(os.environ), 60)
    except KeyboardInterrupt:
        (out / "caught-interrupt").write_text("1")
    signal.signal = real_signal
    import time; time.sleep(3)
    (out / "survived").write_text("1")
    sys.exit(0)
if seam in ("spawn", "spawn-dead-sentinel", "pre-spawn"):
    real = subprocess.Popen
    class Racing(real):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            if kw.get("stdout") is subprocess.PIPE:                  # the model, not the sentinel
                (out / "model.pid").write_text(str(self.pid))
                if seam != "pre-spawn":
                    os.kill(os.getpid(), signal.SIGTERM)
    m.subprocess.Popen = Racing
elif seam not in ("pre-spawn",):
    if seam.endswith("-threaded"):
        # An idle second thread: the kernel may now deliver a process-directed signal to it, so a
        # per-thread signal mask on the main thread cannot hold the signal off.
        import threading, time
        threading.Thread(target=time.sleep, args=(60,), daemon=True).start()
        seam = seam[:-len("-threaded")]
    calls = []
    signal.signal(signal.SIGTERM, lambda *a: calls.append(1))
    mine = signal.getsignal(signal.SIGTERM)
    target = {"restore": "_restore_and_redeliver", "release": "_end_lifeline"}[seam]
    real_fn = getattr(m, target)
    def racing(*a, **kw):
        os.kill(os.getpid(), signal.SIGTERM)
        return real_fn(*a, **kw)
    setattr(m, target, racing)
fds_before = len(os.listdir("/dev/fd"))
try:
    m._run_drive(sys.argv[2], "", ".", dict(os.environ), 60)
except BaseException as exc:
    (out / "escaped").write_text(type(exc).__name__)
    raise
if seam in ("restore", "release"):
    (out / "handler-calls").write_text(str(len(calls)))
    (out / "handler-restored").write_text(str(signal.getsignal(signal.SIGTERM) is mine))
    (out / "fds-leaked").write_text(str(len(os.listdir("/dev/fd")) - fds_before))
'''


def _run_race(tmp_path, seam, cmd):
    tick = tmp_path / "race_tick.py"
    tick.write_text(RACE_TICK)
    proc = subprocess.Popen([sys.executable, str(tick), str(S / "autowatch.py"), cmd, seam,
                             str(tmp_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        return _exit_code(proc, 30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_signal_right_after_the_model_spawns_does_not_orphan_it(tmp_path):
    """The model exists but the sentinel does not yet know its group: a SIGTERM there must not
    end the tick with the model left running in its own session."""
    model = None
    # A model that never writes: one that did would die of the closed pipe on its own and pass
    # this test vacuously (the pipe, not the fix, would have killed it).
    silent = " ".join(shlex.quote(a) for a in (sys.executable, "-c", "import time; time.sleep(30)"))
    try:
        rc = _run_race(tmp_path, "spawn", silent)
        model = _read_pid(tmp_path / "model.pid")
        assert model is not None, "precondition: the race seam never fired"
        assert rc == -signal.SIGTERM, "the tick must still die by the signal it received"
        assert _gone_within(model, 10), "the model outlived a SIGTERM that landed just after spawn"
    finally:
        _cleanup(model)


def test_a_signal_during_final_cleanup_neither_escapes_nor_disables_the_callers_handler(tmp_path):
    """A SIGTERM inside `_run_drive`'s own cleanup must not escape as an internal exception, must
    reach the caller's handler, and must leave that handler installed."""
    cmd = " ".join(shlex.quote(a) for a in (sys.executable, "-c", "pass"))
    rc = _run_race(tmp_path, "restore", cmd)
    assert not (tmp_path / "escaped").exists(), (tmp_path / "escaped").read_text()
    assert rc == 0
    assert (tmp_path / "handler-calls").read_text() == "1", "the caller's handler never ran"
    assert (tmp_path / "handler-restored").read_text() == "True", "the caller's handler was lost"


def test_a_signal_during_final_cleanup_in_a_multithreaded_caller(tmp_path):
    """The same cleanup seam with a second thread alive: the signal must neither escape nor
    leave the caller's handler replaced, whichever thread the kernel delivers it to."""
    cmd = " ".join(shlex.quote(a) for a in (sys.executable, "-c", "pass"))
    rc = _run_race(tmp_path, "restore-threaded", cmd)
    assert not (tmp_path / "escaped").exists(), (tmp_path / "escaped").read_text()
    assert rc == 0
    assert (tmp_path / "handler-calls").read_text() == "1", "the caller's handler never ran"
    assert (tmp_path / "handler-restored").read_text() == "True", "the caller's handler was lost"


def test_every_signal_received_mid_drive_is_redelivered_not_just_the_first(tmp_path):
    """SIGTERM (caller's own, non-exiting handler) and then Ctrl-C during the grace period: the
    caller must see BOTH -- its handler runs, and Ctrl-C still raises KeyboardInterrupt (so the
    tick does not return normally)."""
    proc, gc, child = _start_tick(tmp_path, "ignore_term", "2", "flag")
    try:
        assert gc is not None, "precondition: the grandchild never started"
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.7)
        proc.send_signal(signal.SIGINT)
        rc = _exit_code(proc, 30)
        assert (tmp_path / "handler-ran").exists(), "the caller's SIGTERM handler never ran"
        assert not (tmp_path / "returned").exists(), "the Ctrl-C was swallowed"
        assert rc != 0
        assert _gone_within(gc) and _gone_within(child)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        _cleanup(gc, child)


def test_a_signal_while_the_lifeline_is_released_does_not_leak_it(tmp_path):
    """A SIGTERM landing as the lifeline is being released must not leave its write end open: a
    leaked write end keeps the sentinel alive for the caller's whole life, and it then sweeps a
    long-reaped process-group id when the caller finally exits."""
    cmd = " ".join(shlex.quote(a) for a in (sys.executable, "-c", "pass"))
    rc = _run_race(tmp_path, "release", cmd)
    assert not (tmp_path / "escaped").exists(), (tmp_path / "escaped").read_text()
    assert rc == 0
    assert (tmp_path / "handler-calls").read_text() == "1", "the caller's handler never ran"
    assert (tmp_path / "fds-leaked").read_text() == "0", "the lifeline write end leaked"


def test_a_held_signal_is_not_swallowed_when_the_sentinel_is_already_gone(tmp_path):
    """The sentinel died before the handoff and a SIGTERM was held during the spawn window: the
    drive is refused, the model terminated, and the caller must still die by that SIGTERM."""
    model = None
    silent = " ".join(shlex.quote(a) for a in (sys.executable, "-c", "import time; time.sleep(30)"))
    try:
        rc = _run_race(tmp_path, "spawn-dead-sentinel", silent)
        model = _read_pid(tmp_path / "model.pid")
        assert model is not None, "precondition: the race seam never fired"
        assert rc == -signal.SIGTERM, "the held SIGTERM was swallowed"
        assert _gone_within(model, 10), "the model outlived the refused drive"
    finally:
        _cleanup(model)


def test_the_group_gets_a_graceful_sigterm_before_any_sigkill(tmp_path, short_grace):
    """The escalation starts with SIGTERM to the whole group, so a descendant that cleans up on
    SIGTERM gets to. A kill that reached the grandchild only by SIGKILL would pass every
    liveness test above; this one needs the grandchild's own SIGTERM handler to have run."""
    gc = child = None
    try:
        code, _ = autowatch._run_drive(_cmd(tmp_path, "term_marker"), "", str(tmp_path),
                                       dict(os.environ), TIMEOUT)
        gc, child = _read_pid(tmp_path / "grandchild.pid"), _read_pid(tmp_path / "child.pid")
        assert gc is not None, "precondition: the grandchild never started before the timeout"
        assert code == 124
        assert (tmp_path / "grandchild.sigterm").exists(), "the grandchild never received SIGTERM"
    finally:
        _cleanup(gc, child)


def test_a_normal_finish_tells_the_sentinel_done_so_it_sweeps_nothing(tmp_path):
    """After a model exits normally the sentinel must stand down, not sweep the group: its id
    belongs to a reaped leader, and pre-#425 behaviour for a normal finish (anything the model
    deliberately left running is left alone) is unchanged. A model that leaves a detached
    grandchild behind and exits 0; the grandchild must still be alive after the sentinel's grace."""
    gc = None
    leave = (
        "import os, subprocess, sys, time\n"
        "d = sys.argv[1]\n"
        "gc = 'import os, sys, time; open(os.path.join(sys.argv[1], \"grandchild.pid\"), \"w\").write(str(os.getpid())); time.sleep(30)'\n"
        "subprocess.Popen([sys.executable, '-c', gc, d], stdin=subprocess.DEVNULL,\n"
        "                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "deadline = time.time() + 20\n"
        "while not os.path.exists(os.path.join(d, 'grandchild.pid')) and time.time() < deadline:\n"
        "    time.sleep(0.01)\n"
    )
    script = tmp_path / "leave.py"
    script.write_text(leave)
    cmd = " ".join(shlex.quote(a) for a in (sys.executable, str(script), str(tmp_path)))
    try:
        code, _ = autowatch._run_drive(cmd, "", str(tmp_path), dict(os.environ), 30)
        gc = _read_pid(tmp_path / "grandchild.pid")
        assert gc is not None, "precondition: the grandchild never started"
        assert code == 0
        time.sleep(1.5)       # a sweeping sentinel SIGTERMs at once on EOF; this one is quiet
        assert _alive(gc), "the sentinel swept the group of a model that finished normally"
    finally:
        _cleanup(gc)



def test_a_signal_that_raises_on_redelivery_does_not_drop_the_ones_after_it(tmp_path):
    """Ctrl-C then SIGTERM (SIG_DFL) mid-drive, in a caller that catches KeyboardInterrupt and
    carries on: re-delivering SIGINT raises, and the SIGTERM after it must still be delivered --
    the caller must die by it, not survive."""
    proc, gc, child = _start_tick(tmp_path, "plain", "1", "catchint")
    try:
        assert gc is not None, "precondition: the grandchild never started"
        proc.send_signal(signal.SIGINT)
        time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)
        rc = _exit_code(proc, 30)
        assert not (tmp_path / "survived").exists(), "the SIGTERM after the Ctrl-C was dropped"
        assert rc == -signal.SIGTERM
        assert _gone_within(gc) and _gone_within(child)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        _cleanup(gc, child)



def test_a_signal_recorded_before_the_spawn_means_the_model_is_never_spawned(tmp_path):
    silent = " ".join(shlex.quote(a) for a in (sys.executable, "-c", "import time; time.sleep(30)"))
    model = None
    try:
        rc = _run_race(tmp_path, "pre-spawn", silent)
        model = _read_pid(tmp_path / "model.pid")
        assert rc == -signal.SIGTERM, "the tick must still die by the signal it received"
        assert model is None, "a model was spawned after the drive had already been signalled"
    finally:
        _cleanup(model)


def test_a_signal_while_handlers_are_restored_drops_nothing(tmp_path):
    """A recorded SIGTERM must still be re-delivered when a Ctrl-C lands mid-restore and the
    caller catches the KeyboardInterrupt: the caller dies by the SIGTERM, not survives."""
    cmd = " ".join(shlex.quote(a) for a in (sys.executable, "-c", "pass"))
    rc = _run_race(tmp_path, "restore-window", cmd)
    assert not (tmp_path / "survived").exists(), "the recorded SIGTERM was dropped"
    assert rc == -signal.SIGTERM


def test_a_setsid_escapee_holding_the_pipe_cannot_hang_the_call(tmp_path, short_grace):
    """The documented limit: a descendant that calls setsid escapes the group signal. What must
    still hold is the bound -- the drain after SIGKILL stops waiting for the pipe after
    DRIVE_REAP_SECONDS, so the call returns within timeout + grace + reap."""
    result = {}

    def drive():
        start = time.time()
        result["r"] = autowatch._run_drive(_cmd(tmp_path, "setsid_pipe"), "", str(tmp_path),
                                           dict(os.environ), TIMEOUT)
        result["elapsed"] = time.time() - start

    t = threading.Thread(target=drive, daemon=True)
    gc = child = None
    try:
        t.start()
        t.join(TIMEOUT + 1 + 2 + 6)
        gc, child = _read_pid(tmp_path / "grandchild.pid"), _read_pid(tmp_path / "child.pid")
        assert gc is not None, "precondition: the escapee never started"
        assert not t.is_alive(), "the drain waited on a setsid escapee holding the pipe"
        assert result["r"][0] == 124
    finally:
        _cleanup(gc, child)
        t.join(35)



def test_a_dead_sentinel_does_not_kill_a_caller_with_default_sigpipe(tmp_path):
    """Writing the handoff to a dead sentinel must surface as the documented refusal, not as a
    SIGPIPE that kills a caller running with SIGPIPE at SIG_DFL and orphans the model."""
    model = None
    silent = " ".join(shlex.quote(a) for a in (sys.executable, "-c", "import time; time.sleep(30)"))
    try:
        rc = _run_race(tmp_path, "dead-sentinel-sigpipe", silent)
        model = _read_pid(tmp_path / "model.pid")
        assert model is not None, "precondition: the model was never spawned"
        assert rc == 0, "the caller died writing to the dead sentinel"
        assert (tmp_path / "result").read_text() == "1"
        assert (tmp_path / "sigpipe-restored").read_text() == "True"
        assert _gone_within(model, 15), "the model outlived the refused drive"
    finally:
        _cleanup(model)

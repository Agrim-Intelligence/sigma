"""REAL Windows validation (issue #2494) -- no mocks, no fakes. `test_watch.py`'s win32 tests
prove the DISPATCH LOGIC on any OS via a fake kernel32-shaped object; `test_supervise.py` and
`test_run_with_timeout.py`'s win32 tests do the same via monkeypatched `sys.platform`/`subprocess`.
This file proves the OS-LEVEL GUARANTEES those all assumed: that `_win32_pid_alive` genuinely opens
a real process handle and reads real Windows kernel exit-code state, that `taskkill /T /F`
genuinely reaches a real grandchild process, and that `supervise_daemon.py`'s
`SIGMA_CLAUDE_CMD` word-splitting survives a real Windows executable path and a real quoted
argument, end to end through a real `supervise_daemon.py` subprocess launching a real fake-session
child (twice, to prove the relaunch path).

Skips itself everywhere except real Windows. Run by `.github/workflows/windows.yml`'s
`watchers-windows` job (`workflow_dispatch` only -- see that workflow's own header for
why). If this file has never run green in that job, this repo's
"validated on Windows" claim for the ported watchers is WRITTEN, not MEASURED -- the distinction
AGENTS.md's RELIABILITY property insists on.

Every "fake session command" here is `[sys.executable, "-c", <python source>]` -- never a `.sh`
fixture (unlike `test_supervise.py`'s existing bash-based fakes, which do not run on
`windows-latest`) -- python and pytest are the only things this file assumes exist on the runner.
"""
import os
import pathlib
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real Windows only")

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"

_TEST_TIMEOUT = 60


# --------------------------------------------------------------------------------------- helpers

def _write_fake_session(tmp_path, run_id_out, argv_out, n_out, flip_at=2):
    """Write a standalone fake `claude` session to a real FILE under `tmp_path` (never inline
    `-c` source -- see the note below) and return its path. On invocation N < flip_at it records
    the SIGMA_RUN_ID it inherited and its own sys.argv[1:] (so the test can check both run-id
    inheritance AND that a quoted multi-word argument survived as ONE token), then prints a
    supervise_classify.py-recognized "usage limit" line (classified backoff/sleep, never done) so
    the daemon relaunches; on invocation N >= flip_at it prints the real "LOOP STOP: backlog-empty"
    sentinel (classified done) so the daemon exits cleanly. A per-run counter file (`n_out`)
    persists across the daemon's own relaunches, since each is a genuinely separate subprocess.

    A real FILE, not `python -c "<source>"`: under `-c`, CPython sets `sys.argv[0]` to the literal
    string `'-c'` and does NOT include the interpreter path in argv at all -- so a trailing quoted
    CLI argument (the actual thing #2494's quoting fix needs to prove) has nowhere to land in
    `sys.argv` unless the fake session is invoked as `python <script.py> <args...>`, where
    `sys.argv[0]` is the script path and `sys.argv[1:]` are the real, quoting-sensitive arguments.
    (An earlier version of this test used `-c` and asserted `sys.argv[1] == "-c"`, which is not
    even the CORRECT shape for a `-c` invocation -- a real bug in the TEST, not in
    `_split_claude_cmd`, caught live on `windows-latest`: `IndexError: list index out of range`,
    since `sys.argv` under `-c` with no further args is just `['-c']`.)"""
    script = tmp_path / "fake_session.py"
    script.write_text(
        "import os, sys\n"
        f"n_path = {str(n_out)!r}\n"
        "n = int(open(n_path).read()) + 1 if os.path.exists(n_path) else 1\n"
        "open(n_path, 'w').write(str(n))\n"
        f"if n < {flip_at}:\n"
        f"    open({str(run_id_out)!r}, 'w').write(os.environ.get('SIGMA_RUN_ID', ''))\n"
        f"    open({str(argv_out)!r}, 'w').write(repr(sys.argv[1:]))\n"
        "    print('usage limit reached, try again later')\n"
        "else:\n"
        "    print('LOOP STOP: backlog-empty')\n"
    )
    return script


def _run_supervisor(base, cmd, max_runs="5", extra_env=None):
    env = {**os.environ, "SIGMA_CLAUDE_CMD": cmd,
           "SIGMA_SUPERVISE_MAX_RUNS": max_runs, "SIGMA_SUPERVISE_SLEEP_SCALE": "0"}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(S / "supervise_daemon.py"), str(base)],
        capture_output=True, text=True, env=env, timeout=_TEST_TIMEOUT,
    )


# ------------------------------------------------------------------ #2494 checks 1/2/3/4 (combined)

def test_supervise_relaunch_stop_file_and_run_id_through_a_quoted_windows_command(tmp_path):
    """The combined real-Windows proof for a fake session launch + relaunch, SIGMA_RUN_ID
    inheritance, and a quoted SIGMA_CLAUDE_CMD.

    #2494's real finding (see supervise_daemon.py's module docstring and CHANGELOG.md): plain
    POSIX-mode `shlex.split` silently EATS every backslash in a Windows path -- not a quoting
    mismatch, outright corruption of the executable name itself. This test's own
    `SIGMA_CLAUDE_CMD` is deliberately built from `sys.executable`, which on a real
    `windows-latest` runner IS a real backslash-separated path (e.g.
    C:\\hostedtoolcache\\windows\\Python\\3.12.x\\x64\\python.exe) -- so this test trips that exact
    bug on its very first launch if `_split_claude_cmd`'s win32 fix is broken or reverted, and the
    daemon would report a manufactured "command not found" crash instead of ever reaching a real
    fake session. The fake session's OWN path (also a real, backslash-separated tmp_path under
    Windows) is followed by a `"quoted, multi-word argument"`, to prove multi-word quoting survives
    too, not just the backslash fix alone -- checked against the fake session's real `sys.argv[1:]`,
    which must arrive as ONE element, not split on the internal space."""
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    run_id_out = tmp_path / "run-id.txt"
    argv_out = tmp_path / "argv.txt"
    n_out = tmp_path / "n.txt"
    fake = _write_fake_session(tmp_path, run_id_out, argv_out, n_out, flip_at=2)
    quoted_arg = "hello there, windows"
    # Both the interpreter path AND the fake session's own script path are real, backslash-separated
    # Windows paths -- the quoted trailing argument is the one thing that must survive as ONE token.
    cmd = f'{sys.executable} {fake} "{quoted_arg}"'
    env = {k: v for k, v in os.environ.items() if k != "SIGMA_RUN_ID"}
    proc = _run_supervisor(base, cmd, max_runs="3", extra_env=env)

    assert "Traceback (most recent call last):" not in proc.stderr, (
        f"the daemon crashed instead of launching the fake session for real -- almost certainly "
        f"the backslash-eating shlex bug reappearing:\nstderr:\n{proc.stderr}\nstdout:\n{proc.stdout}")
    assert proc.returncode == 0, f"expected a clean 'done' exit, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"

    log = (base / "state" / "supervisor.log").read_text()
    assert log.count("supervisor: run #") == 2, (
        f"expected exactly 2 runs (relaunch then done), got:\n{log}")
    assert "action=done" in log

    assert run_id_out.exists(), "fake session never ran for real (SIGMA_CLAUDE_CMD launch failed)"
    seen_run_id = run_id_out.read_text().strip()
    assert seen_run_id, "supervise_daemon.py did not hand the session a non-empty SIGMA_RUN_ID"

    assert argv_out.exists(), "fake session never reached its own argv-recording line"
    seen_argv = eval(argv_out.read_text())  # noqa: S307 - our own fixture's repr(sys.argv[1:]), trusted
    assert seen_argv == [quoted_arg], (
        f"the quoted trailing argument did not survive _split_claude_cmd as ONE token:\n"
        f"expected {[quoted_arg]!r}, got {seen_argv!r}")


def test_supervise_stop_file_halts_on_windows(tmp_path):
    """#2494 check 2 (supervise half): the direct Windows-real port of
    test_supervise.py::test_wrapper_stop_file_halts_cleanly."""
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    (base / "state" / "supervisor.stop").write_text("")
    proc = _run_supervisor(base, f"{sys.executable} -c pass")
    assert proc.returncode == 0 and "stop-file" in proc.stdout, proc.stdout + proc.stderr


def test_watch_daemon_stop_file_halts_on_windows(tmp_path):
    """#2494 check 2 (watch half): watch_daemon.py's stop-file check is the very first thing in
    its main loop (before any of the 8 tick-loop sub-calls), so this needs no further stubbing --
    a real subprocess launch against a scratch .sdlc dir with the stop-file already present."""
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    (base / "state" / "watch.stop").write_text("")
    proc = subprocess.run(
        [sys.executable, str(S / "watch_daemon.py"), str(base)],
        capture_output=True, text=True, timeout=_TEST_TIMEOUT,
    )
    assert proc.returncode == 0 and "stop-file present" in proc.stdout, proc.stdout + proc.stderr


# --------------------------------------------------------------------------------- #2494 check 5

def _win32_watch_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("watch_daemon", S / "watch_daemon.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_win32_pid_alive_against_real_processes():
    """#2498's `_win32_pid_alive`, proven here with ZERO mocking of `_win32_kernel32` -- a real
    `ctypes.WinDLL('kernel32')`, a real `OpenProcess`, a real `GetExitCodeProcess`. This is the one
    thing no mocked unit test can prove: that the ABI declarations (HANDLE as a real 64-bit
    pointer-sized return, not ctypes' default truncating 32-bit c_int -- see `_win32_kernel32`'s
    own docstring) are actually correct against the real Windows kernel32.dll, and that the
    function is non-vacuous: it must read a genuinely-alive PID as alive AND a genuinely-dead PID
    as dead, so a constant-True (or constant-False) implementation cannot pass both assertions."""
    wd = _win32_watch_mod()
    assert wd._win32_pid_alive(os.getpid()) is True

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=10)
    dead_pid = child.pid
    # Poll briefly: Windows PID reuse/reaping timing is not instantaneous either.
    deadline = time.time() + 5
    alive = True
    while time.time() < deadline:
        alive = wd._win32_pid_alive(dead_pid)
        if not alive:
            break
        time.sleep(0.1)
    assert alive is False, f"a real, exited child pid {dead_pid} still read as alive"


# --------------------------------------------------------------------------------- #2494 check 6

def test_run_with_timeout_taskkill_actually_kills_the_real_tree(tmp_path):
    """#2498's win32 `taskkill /T /F` kill path, proven for real -- the direct Windows analogue of
    test_run_with_timeout.py::test_a_hung_commands_grandchild_process_is_also_terminated_not_orphaned.
    The target spawns a REAL grandchild (another python.exe that just sleeps), writes the
    grandchild's pid to disk BEFORE hanging itself (so the pid survives on disk even once the
    whole tree is killed -- printing it to a pipe would not survive, since the pipe dies with the
    tree), and hangs. After the wrapper reports 124, this polls (not a single instant check --
    Windows kill/reap timing is not guaranteed instantaneous) for the grandchild to actually be
    gone, checked via watch_daemon's own real `_win32_pid_alive` rather than trusting the 124
    return code alone -- exactly the "run the control" shape AGENTS.md requires: a naive
    implementation that killed only the direct child would return 124 here too, while leaving this
    assertion red."""
    target = tmp_path / "target.py"
    pid_file = tmp_path / "grandchild.pid"
    target.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    proc = subprocess.run(
        [sys.executable, str(S / "run_with_timeout.py"), "2", str(target)],
        capture_output=True, text=True, timeout=_TEST_TIMEOUT,
    )
    assert proc.returncode == 124, proc.stdout + proc.stderr

    deadline = time.time() + 10
    grandchild_pid = None
    while time.time() < deadline and grandchild_pid is None:
        if pid_file.exists():
            try:
                grandchild_pid = int(pid_file.read_text())
            except ValueError:
                pass
        else:
            time.sleep(0.1)
    assert grandchild_pid is not None, "the target never got far enough to record its grandchild's pid"

    wd = _win32_watch_mod()
    deadline = time.time() + 5
    alive = True
    while time.time() < deadline:
        alive = wd._win32_pid_alive(grandchild_pid)
        if not alive:
            break
        time.sleep(0.2)
    assert alive is False, (
        f"grandchild pid {grandchild_pid} was NOT reached by taskkill /T /F -- the tree-kill left "
        f"an orphan running")

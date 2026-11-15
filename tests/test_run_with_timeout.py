import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

import unattended_support as support

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _rwt():
    """Load run_with_timeout.py as an importable module (not a subprocess) so the #2498 win32
    tests below can monkeypatch its sys.platform/subprocess/os references directly -- mirrors
    test_watch.py's own lazy-load-and-cache pattern for watch_daemon.py."""
    spec = importlib.util.spec_from_file_location("run_with_timeout", S / "run_with_timeout.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

# Generous safety-net timeout for every subprocess.run below: if run_with_timeout.py regresses
# and stops actually killing a hung child, this fails the TEST with a clear TimeoutExpired
# instead of hanging the whole suite indefinitely.
_TEST_TIMEOUT = 15


def test_normal_command_passes_through_exit_code_and_output(tmp_path):
    """A well-behaved, fast command's exit code and stdout must reach the caller unmodified --
    proves the wrapper is a pass-through, not just a killer."""
    target = tmp_path / "target.py"
    target.write_text("print('hi')\nimport sys\nsys.exit(3)\n")
    proc = subprocess.run(
        ["python3", str(S / "run_with_timeout.py"), "10", str(target)],
        capture_output=True, text=True, timeout=_TEST_TIMEOUT,
    )
    assert proc.returncode == 3
    assert "hi" in proc.stdout


def test_a_hung_command_is_killed_and_exits_124(tmp_path):
    """The core behavior this whole slice exists to add: a command that outlives its bound gets
    killed and reported as 124 (this repo's own established timeout convention -- cross_repo.py's
    `_run_gh` and autowatch.py's `_run_drive` both already use it), with a stderr message naming
    both the command that timed out and the bound it was given."""
    target = tmp_path / "target.py"
    target.write_text("import time\ntime.sleep(30)\n")
    proc = subprocess.run(
        ["python3", str(S / "run_with_timeout.py"), "1", str(target)],
        capture_output=True, text=True, timeout=_TEST_TIMEOUT,
    )
    assert proc.returncode == 124
    assert "timed out after 1s" in proc.stderr
    assert target.name in proc.stderr


def test_a_failing_non_timeout_command_still_propagates_its_real_exit_code(tmp_path):
    """A non-timeout failure must not be swallowed or clobbered to 124 or 1 -- only an actual
    timeout gets 124."""
    target = tmp_path / "target.py"
    target.write_text("import sys\nsys.exit(7)\n")
    proc = subprocess.run(
        ["python3", str(S / "run_with_timeout.py"), "10", str(target)],
        capture_output=True, text=True, timeout=_TEST_TIMEOUT,
    )
    assert proc.returncode == 7


def test_invokes_non_executable_target_scripts_via_the_interpreter(tmp_path):
    """The 7 real sub-scripts this wraps (sync.py, watch.py, agent_watch.py, comment_watch.py,
    reconcile_tick.py, channel_notify.py, drift_tick.py) all carry a shebang but NO execute bit
    (confirmed -rw-r--r--, .sdlc/research/2443.md §6) -- run_with_timeout.py must prepend the
    interpreter itself rather than exec the target path directly, or every real call fails with
    PermissionError/OSError on the FIRST invocation, not specifically the timeout path."""
    target = tmp_path / "target.py"
    target.write_text("print('MARKER-OK')\n")   # write_text only -- no os.chmod, so this has no
    # execute bit, matching the real sub-scripts' actual mode exactly.
    assert not os.access(target, os.X_OK)
    proc = subprocess.run(
        ["python3", str(S / "run_with_timeout.py"), "10", str(target)],
        capture_output=True, text=True, timeout=_TEST_TIMEOUT,
    )
    assert proc.returncode == 0
    assert "MARKER-OK" in proc.stdout


def test_target_script_argv_passes_through_unmodified(tmp_path):
    """Proves arg quoting/splitting isn't mangled between run_with_timeout.py's own argv and the
    child's -- a space-containing arg must survive as ONE arg, not be re-split."""
    target = tmp_path / "target.py"
    target.write_text("import sys\nprint(sys.argv[1:])\n")
    proc = subprocess.run(
        ["python3", str(S / "run_with_timeout.py"), "10", str(target), "a", "b c"],
        capture_output=True, text=True, timeout=_TEST_TIMEOUT,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == repr(["a", "b c"])


def test_a_hung_commands_grandchild_process_is_also_terminated_not_orphaned(tmp_path):
    """Plan-Review round 1, blocking issue 1: a plain kill of run_with_timeout.py's own DIRECT
    child only reaps that one process on POSIX -- a grandchild the target itself spawns (e.g.
    sync.py:99's untimed `subprocess.run(["git", ...])`) would survive as an orphan under a naive
    implementation. This is the test that bug is about: the target here spawns a REAL child
    (`sleep 300`), writes its pid to a file so this test can check on it after the wrapper
    returns, and hangs itself. Per AGENTS.md's "run the control" rule, this test must be run
    against a deliberately naive (no process-group) implementation FIRST and confirmed to fail
    there, before the real process-group-aware implementation replaces it -- see the implementation
    notes in run_with_timeout.py / the PR description for that control result."""
    target = tmp_path / "target.py"
    target.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen(['sleep', '300'])\n"
        "with open(sys.argv[1], 'w') as f:\n"
        "    f.write(str(child.pid))\n"
        "time.sleep(300)\n"
    )
    pid_file = tmp_path / "child.pid"
    proc = subprocess.run(
        ["python3", str(S / "run_with_timeout.py"), "1", str(target), str(pid_file)],
        capture_output=True, text=True, timeout=_TEST_TIMEOUT,
    )
    assert proc.returncode == 124

    child_pid = int(pid_file.read_text())
    # `support.gone` (not a bare os.kill poll): a killed orphan is a zombie until its reaper comes, and
    # `os.kill(pid, 0)` succeeds on a zombie -- see `unattended_support.alive()` (#2225, #2228).
    assert support.gone(child_pid, limit=2.0), f"grandchild pid {child_pid} was not terminated within 2s of the wrapper returning"


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="PR_SET_CHILD_SUBREAPER (prctl 36) exists only on Linux; no other platform has an equivalent")
def test_a_killed_grandchild_that_stays_a_zombie_of_the_test_process_still_counts_as_terminated(tmp_path):
    """Deterministic control for the test above (#2228). Linux-only: the platform-skip is for a platform
    without `PR_SET_CHILD_SUBREAPER`, where this orphan-stays-a-zombie condition cannot be constructed.

    The test above passes or fails on how fast PID 1 reaps the killed orphan, so a raw `os.kill(pid, 0)`
    poll is green on a host with a quick init and red where init reaps on a ~2 s poll. Here this pytest
    process is made the child subreaper, so the orphaned grandchild is re-parented to US and stays a
    zombie until we `waitpid` it: `os.kill(pid, 0)` then succeeds for the whole 2 s, deterministically.
    Only a zombie-aware check passes. The flag is cleared in `finally` and the zombie is reaped, so
    nothing leaks into other tests."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        pytest.skip("prctl(PR_SET_CHILD_SUBREAPER) refused on this host")
    child_pid = None
    try:
        target = tmp_path / "target.py"
        target.write_text(
            "import subprocess, sys, time\n"
            "child = subprocess.Popen(['sleep', '300'])\n"
            "with open(sys.argv[1], 'w') as f:\n"
            "    f.write(str(child.pid))\n"
            "time.sleep(300)\n"
        )
        pid_file = tmp_path / "child.pid"
        proc = subprocess.run(
            ["python3", str(S / "run_with_timeout.py"), "1", str(target), str(pid_file)],
            capture_output=True, text=True, timeout=_TEST_TIMEOUT,
        )
        assert proc.returncode == 124
        child_pid = int(pid_file.read_text())
        assert support.gone(child_pid, limit=2.0), f"grandchild pid {child_pid} was not terminated within 2s of the wrapper returning"
    finally:
        libc.prctl(36, 0, 0, 0, 0)
        if child_pid is not None:
            support.reap(child_pid)
            try:
                os.waitpid(child_pid, 0)
            except ChildProcessError:
                pass


# --------------------------------------------------------------------------------------------
# #2498: Windows-only paths. No real Windows host is available -- proven here only by mocking
# subprocess.Popen/subprocess.run and forcing sys.platform, never by a real win32 execution (see
# #2494 for real-host validation). The POSIX tests above are UNCHANGED and stay the system of
# record for POSIX behaviour -- these are purely additive.


class _FakeProc:
    """A fake subprocess.Popen return value: .wait(timeout=...) raises TimeoutExpired exactly
    once (simulating a hung child), then a bare .wait() (the reap call) succeeds."""

    def __init__(self, pid=4242):
        self.pid = pid
        self._waited_with_timeout = False

    def wait(self, timeout=None):
        if timeout is not None and not self._waited_with_timeout:
            self._waited_with_timeout = True
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return 0


def test_win32_spawns_with_a_new_process_group_not_start_new_session(monkeypatch):
    """#2498 step 2: start_new_session=True raises on win32 (no setsid there) -- the win32 branch
    must pass creationflags=CREATE_NEW_PROCESS_GROUP instead, and must NOT pass
    start_new_session at all."""
    rwt = _rwt()
    monkeypatch.setattr(rwt.sys, "platform", "win32")
    recorded = {}

    def _fake_popen(cmd, **kwargs):
        recorded.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(rwt.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(rwt.subprocess, "run", lambda *a, **kw: None)  # the taskkill call
    rwt.main(["run_with_timeout.py", "1", "target.py"])
    assert recorded.get("creationflags") == rwt.CREATE_NEW_PROCESS_GROUP
    assert "start_new_session" not in recorded


def test_win32_timeout_runs_taskkill_tree_kill_and_never_touches_posix_process_group_calls(monkeypatch):
    """#2498 step 2: on a win32 timeout, the kill path must be `taskkill /T /F /PID <pid>` --
    never os.killpg/os.getpgid, which don't exist on win32 at all. Both POSIX calls are bombed so
    this test would fail LOUDLY (not silently pass) if the win32 branch ever fell through to them."""
    rwt = _rwt()
    monkeypatch.setattr(rwt.sys, "platform", "win32")
    monkeypatch.setattr(rwt.subprocess, "Popen", lambda cmd, **kw: _FakeProc(pid=9999))

    def _boom_killpg(*a, **kw):
        raise AssertionError("os.killpg must never be called on win32")

    def _boom_getpgid(*a, **kw):
        raise AssertionError("os.getpgid must never be called on win32")

    monkeypatch.setattr(rwt.os, "killpg", _boom_killpg)
    monkeypatch.setattr(rwt.os, "getpgid", _boom_getpgid)

    run_calls = []
    monkeypatch.setattr(rwt.subprocess, "run", lambda cmd, **kw: run_calls.append(cmd))

    rc = rwt.main(["run_with_timeout.py", "1", "target.py"])
    assert rc == 124
    assert run_calls == [["taskkill", "/T", "/F", "/PID", "9999"]]

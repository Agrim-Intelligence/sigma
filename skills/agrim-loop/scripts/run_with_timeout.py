#!/usr/bin/env python3
"""Run a target Python script with a real, enforced wall-clock timeout, killing the WHOLE
process group (not just the direct child) if it overruns.

CLI contract: `run_with_timeout.py <seconds> <target-script> <target-args...>`
Exit codes: the target's own exit code on a normal finish; 124 on timeout (matching this repo's
own established convention -- cross_repo.py's `_run_gh` and autowatch.py's `_run_drive` both
already use 124 for the same thing); 1 if the target could not even be launched.

Written for watch_daemon.py's 8 tick-loop subprocess calls (#2416/#2443): a single hung call there used
to be able to make a live watcher read as dead (or mask a genuinely dead one) for an unbounded
time. Wrapping each call here bounds that exposure to <seconds>.

Windows (#2498): `start_new_session=True` and `os.killpg`/`os.getpgid` are POSIX-only --
`start_new_session=True` raises on win32, and `os.getpgid`/`os.killpg` don't exist there at all.
On win32 this spawns with `creationflags=CREATE_NEW_PROCESS_GROUP` instead, and on timeout runs
`taskkill /T /F /PID <pid>` instead of `os.killpg(...)` -- `/T` walks Windows' own parent-PID
bookkeeping to kill the whole process tree (the tree-kill equivalent of POSIX process-group
SIGKILL; Windows has no process-group concept for an arbitrary spawned tree), `/F` forces it.
`CREATE_NEW_PROCESS_GROUP` is NOT what enables the tree-kill -- `taskkill /T` doesn't depend on
process-group membership -- it only replaces the invalid `start_new_session=True` kwarg and
isolates the child from console CTRL_C/CTRL_BREAK forwarding. The POSIX branch below is completely
unchanged: this only ADDS a win32-only branch alongside it, proven by real subprocess.run calls
against this script (unmodified pre-existing tests) on POSIX, and by mocked Popen/subprocess.run
calls on win32 for the dispatch logic.

#2494 validated the win32 branch against a real windows-latest GitHub Actions runner (workflow run
34949212549): `taskkill /T /F` genuinely reaches and kills a real grandchild process the timed-out
target spawned, not just the direct child -- checked by polling the grandchild's own liveness (via
`watch_daemon._win32_pid_alive`, itself independently proven real on the same run) after the
wrapper reports 124, rather than trusting the exit code alone. See
`tests/test_windows_real.py::test_run_with_timeout_taskkill_actually_kills_the_real_tree`.
"""
import os
import signal
import subprocess
import sys

#: #2498. `subprocess.CREATE_NEW_PROCESS_GROUP` is only DEFINED by the stdlib on an actual Windows
#: interpreter (confirmed: `hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")` is False on this
#: darwin host) -- a bare `subprocess.CREATE_NEW_PROCESS_GROUP` reference would raise
#: AttributeError the instant sys.platform is "win32" here, whether that's a real Windows run or a
#: test that monkeypatches sys.platform on a non-Windows host. The literal fallback is the real,
#: stable Win32 constant value (0x00000200), not a guess.
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


USAGE = "usage: run_with_timeout.py <seconds> <script> [args...]"


def main(argv):
    # Exact match only: `run_with_timeout.py 60 child.py --help` must hand `--help` to the child.
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) < 3:
        print("run_with_timeout: " + USAGE, file=sys.stderr)
        return 2
    seconds = float(argv[1])
    target = argv[2]
    target_args = argv[3:]
    # The 7 real sub-scripts this wraps (sync.py, watch.py, agent_watch.py, comment_watch.py,
    # reconcile_tick.py, channel_notify.py, drift_tick.py) carry a shebang but no execute bit
    # (confirmed -rw-r--r--, .sdlc/research/2443.md §6) -- prepend the interpreter ourselves
    # rather than exec the target path directly, or every call fails with PermissionError/OSError
    # on the FIRST real invocation, not specifically the timeout path.
    cmd = [sys.executable, target, *target_args]
    # #2498: on win32, `start_new_session=True` raises (it's a POSIX-only kwarg -- setsid has no
    # Windows equivalent). `creationflags=CREATE_NEW_PROCESS_GROUP` is the win32 substitute at
    # spawn time; the actual tree-kill at timeout is `taskkill /T /F`, below, which does not depend
    # on this flag. Everything else about the call (no stdout=/stderr= override, same cmd) is
    # identical on both branches.
    popen_kwargs = (
        {"creationflags": CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
        else {"start_new_session": True}
    )
    try:
        # start_new_session=True (POSIX) puts the child in a NEW process group (pgid == its own
        # pid) instead of inheriting ours. Without this, killing only the direct child on timeout
        # leaves anything IT spawns (e.g. sync.py:99's untimed `subprocess.run(["git", ...])`)
        # running as an orphan -- SIGKILL does not propagate to a child outside the signalled
        # process's own group. Plan-Review round 1, blocking issue 1: proved live against a
        # naive `subprocess.run(cmd, timeout=seconds)` implementation, which left an orphaned
        # grandchild `sleep 300` holding the stdout/stderr pipe open and hanging the CALLER's own
        # `subprocess.run` well past this process's own exit -- see 1f in
        # tests/test_run_with_timeout.py.
        #
        # No stdout=/stderr= override: the child inherits THIS process's own stdio, which in turn
        # inherits whatever the caller already pointed our fds at. That coupling is load-bearing for
        # watch_daemon.py's tick, which passes REAL file descriptors to this script rather than
        # capturing: a `"log"` call (sync.py pull/publish) gets stdout AND stderr on one append-mode
        # `state/watch.log` handle, and a `"summary"` call (the six tick scripts) gets
        # stdout=PIPE with stderr on that same log handle. A real capture-and-reprint HERE would work
        # most of the time but risks reordering stdout/stderr interleaving
        # (.sdlc/research/2443.md §2c), which is why this level deliberately sets neither.
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except OSError as exc:
        # Never let a launch failure (bad path, permissions) surface as a raw traceback into the
        # log -- one clear line instead, distinguishable from both a timeout (124) and a normal
        # non-zero exit.
        print(f"run_with_timeout: failed to launch {cmd!r}: {exc}", file=sys.stderr)
        return 1
    try:
        proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        # Kill the WHOLE process tree, not just the direct child -- on POSIX, os.killpg reaches
        # every process sharing this pgid, which is every process the target (or anything it
        # forked) has spawned since start_new_session=True above, because a forked child inherits
        # its parent's pgid unless it explicitly calls setsid/setpgrp itself. This is the fix for
        # 1f / Plan-Review round 1 fix 1 -- a plain proc.kill() here would reproduce the bug. On
        # win32 (#2498) there is no process-group equivalent for an arbitrary spawned tree, so
        # `taskkill /T /F /PID <pid>` does the same job by walking Windows' own parent-PID
        # bookkeeping instead -- proven against a real Windows host (#2494, workflow run
        # 34949212549: a real grandchild process was genuinely killed, checked by polling its own
        # liveness afterward, not just the 124 return code), in addition to the mocked
        # subprocess.run calls in tests/test_run_with_timeout.py that prove the dispatch logic.
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                            capture_output=True)
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass  # already gone between TimeoutExpired and here -- nothing left to kill
        proc.wait()  # reap -- don't leave a zombie behind for OUR own exit
        print(f"run_with_timeout: {' '.join(cmd)!r} timed out after {seconds:g}s (killed)",
              file=sys.stderr)
        return 124
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""The shared test seams in `tests/unattended_support.py` proved on their own (#2225).

A seam the other tests lean on is only worth something if it can be seen to do its one job: these
tests run each seam against a case where the naive approach is known to fail on this very host.
"""
import os
import subprocess
import sys
import time

import unattended_support as support


def _run(argv, cwd):
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=120)


def test_without_module_really_hides_a_builtin_fcntl(tmp_path):
    """A `PYTHONPATH` `fcntl.py` shim is never consulted where the interpreter BUILDS `fcntl` in
    (`sys.builtin_module_names`, as this container's Python 3.13 does): the builtin finder wins, the
    script gets real flock and a "refuses without flock" test silently tests nothing. The driver puts
    `None` in `sys.modules` first, which halts the import whether `fcntl` is builtin or an extension."""
    script = tmp_path / "uses_fcntl.py"
    script.write_text("import fcntl\nprint('IMPORTED', fcntl)\n")
    plain = _run([sys.executable, str(script)], tmp_path)
    hidden = _run(support.without_module("fcntl", str(script)), tmp_path)
    if sys.platform != "win32":                       # the plain run proves fcntl is really importable here
        assert plain.returncode == 0 and "IMPORTED" in plain.stdout, plain
    assert hidden.returncode != 0 and "IMPORTED" not in hidden.stdout, hidden
    assert "ModuleNotFoundError" in hidden.stderr and "fcntl" in hidden.stderr, hidden.stderr


def test_without_module_hides_a_file_module_and_otherwise_runs_the_script_as_itself(tmp_path):
    """The inverse case (a module that exists only as a file beside the script), and the rest of the
    driver's contract: the script runs as `__main__`, sees only its own arguments, and imports its
    siblings as it would when run directly."""
    (tmp_path / "onlyfile.py").write_text("VALUE = 7\n")
    script = tmp_path / "main_script.py"
    script.write_text("import sys\nimport onlyfile\nprint('RAN', __name__, sys.argv[1:], onlyfile.VALUE)\n")
    hidden = _run(support.without_module("onlyfile", str(script), "a1"), tmp_path.parent)
    assert hidden.returncode != 0 and "ModuleNotFoundError" in hidden.stderr, hidden
    other = _run(support.without_module("not_imported_here", str(script), "a1", "--flag"), tmp_path.parent)
    assert other.returncode == 0, other.stderr
    assert other.stdout.strip() == "RAN __main__ ['a1', '--flag'] 7", other.stdout


def _unreaped_zombie():
    """A child that has exited and that nobody has reaped: we are its parent and we wait WITHOUT reaping
    (`os.waitid(..., WEXITED | WNOWAIT)`), so it stays a zombie until the `p.wait()` in the caller's `finally`.
    Deterministic on any POSIX host, with no `/proc` polling and no dependence on how fast PID 1 reaps."""
    p = subprocess.Popen(["true"])
    if hasattr(os, "waitid"):
        os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOWAIT)
        return p
    # macOS builds of Python have no os.waitid (#2255): poll `ps` until the child reads as a zombie (bounded).
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        stat = subprocess.run(["ps", "-o", "stat=", "-p", str(p.pid)], capture_output=True, text=True).stdout
        if stat.strip().startswith("Z"):
            return p
        time.sleep(0.05)
    raise AssertionError("the exited child never read as a zombie")


def _assert_zombie_reads_gone(p):
    os.kill(p.pid, 0)                                 # the zombie-blind probe: does not raise, "alive"
    assert support.alive(p.pid) is False, "alive() read an exited, unreaped child as running"
    assert support.gone(p.pid, 0.0) is True


def test_alive_reads_an_unreaped_zombie_as_gone():
    """`os.kill(pid, 0)` succeeds on a zombie, so a "the group kill worked" test that probes with it reads a
    killed grandchild as alive until PID 1 reaps the orphan. This container's PID 1 (`process_api`) reaps on
    a ~2 s poll, longer than those tests' deadlines (#2225). `alive()` must say "not running" for a zombie,
    on whichever source this host has: `/proc/<pid>/stat` (Linux) or `ps -o stat=` (macOS, no `/proc`)."""
    p = _unreaped_zombie()
    try:
        _assert_zombie_reads_gone(p)
        assert support.alive(os.getpid()) is True     # and a running process still reads as running
    finally:
        p.wait()
    assert support.alive(p.pid) is False              # reaped: gone by the plain probe too


def test_alive_without_proc_asks_ps(monkeypatch, tmp_path):
    """The macOS form of the test above, run on every host: with no `/proc` (pointed at an empty folder),
    `alive()` falls back to `ps -o stat=` and still reads the zombie as gone and this process as running.
    Without the fallback, a host with no `/proc` would read every zombie as alive."""
    monkeypatch.setattr(support, "PROC", str(tmp_path / "no-proc"))
    p = _unreaped_zombie()
    try:
        _assert_zombie_reads_gone(p)
        assert support.alive(os.getpid()) is True
    finally:
        p.wait()


def test_hermetic_drops_git_config_injected_through_the_environment(tmp_path, monkeypatch):
    """git reads configuration from the environment as well as from files: `GIT_CONFIG_COUNT` with
    `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>`, and `GIT_CONFIG_PARAMETERS` (what `git -c` exports to
    its children). This container injects `credential.interactive=false` that way, so git never runs
    an askpass helper and the "a hung credential helper stalls git" control could not hang (#2225).
    `hermetic()` promises to ignore the machine's configuration, so it must drop these too. The test
    sets its own injection, so it proves the same on a host that injects nothing."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.interactive")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "sigma.injected")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "yes")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'sigma.parameter'='yes'")

    def env_config():
        listed = subprocess.run(["git", "config", "--show-origin", "--list"], cwd=str(tmp_path),
                                capture_output=True, text=True, timeout=60)
        return [line for line in listed.stdout.splitlines() if line.startswith("command line:")]

    assert len(env_config()) == 3, env_config()      # the injection is really seen before hermetic()
    support.hermetic(tmp_path, monkeypatch)
    assert env_config() == []
    for name in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
                 "GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1"):
        assert name not in os.environ, name

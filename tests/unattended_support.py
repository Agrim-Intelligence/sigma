"""Support for the #920 tests (the shared verify runner and the unattended git runner). No test lives here, and nothing
here asserts at import time.

It loads the new modules by path inside the test body (never by import, so a missing module is an assertion inside a
test and never a collection error), rebuilds a module with a source substitution for the mutation controls, and holds
the hermetic-git, shim and process-liveness helpers the planned tests share. The code under test never lives here."""
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"
NAMES = ("bounded_run", "unattended_git")
_MEMO = {}


# ------------------------------------------------------------------------------------------ loading

def source(stem):
    path = SCRIPTS / (stem + ".py")
    assert path.is_file(), "%s.py does not exist yet: the module has not been written" % stem
    return path.read_text(encoding="utf-8")


def build(stem, src):
    path = SCRIPTS / (stem + ".py")
    module = types.ModuleType(stem + "_under_test")
    module.__file__ = str(path)
    exec(compile(src, str(path), "exec"), module.__dict__)    # noqa: S102 - test-only
    return module


def load(stem):
    """The real module, built once per process."""
    if stem not in _MEMO:
        _MEMO[stem] = build(stem, source(stem))
    return _MEMO[stem]


def real(stem):
    """An existing shipped script, loaded by path once per process (the engine the new runner is injected into)."""
    key = "existing:" + stem
    if key not in _MEMO:
        spec = importlib.util.spec_from_file_location(stem, SCRIPTS / (stem + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MEMO[key] = module
    return _MEMO[key]


def edited(stem, edits):
    src = source(stem)
    for old, new in edits:
        assert old in src, "mutation target has drifted out of %s: %r" % (stem, old)
        src = src.replace(old, new)
    return src


def variant(stem, *edits):
    """The module rebuilt with every occurrence of each (old, new) pair replaced; a target must exist (the lesson
    recorded at tests/test_feature_registry.py `_mod_with`). Each caller still asserts the mutant behaves differently."""
    return build(stem, edited(stem, edits))


def variant_file(tmp_path, stem, *edits):
    """A mutated copy of `stem` written next to a copy of its siblings, for a child process to run -> its path."""
    src = edited(stem, edits)
    folder = tmp_path / ("variant-" + stem)
    folder.mkdir()
    for sibling in ("shell_policy.py", "bounded_run.py"):
        shutil.copy(str(SCRIPTS / sibling), str(folder / sibling))
    (folder / (stem + ".py")).write_text(src, encoding="utf-8")
    return folder / (stem + ".py")


# ------------------------------------------------------------------------------------------ hermetic git

def hermetic(tmp_path, monkeypatch, extra=""):
    """Make every git this test runs ignore the machine's own configuration. `extra` is appended to the global file,
    so a test can make that configuration hostile on purpose.

    "The machine's configuration" includes what the environment injects, not only the files: `GIT_CONFIG_COUNT` with
    `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>`, and `GIT_CONFIG_PARAMETERS`. A cloud container sets
    `credential.interactive=false` that way, which kept git from ever running a hostile askpass (#2225)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = t\n\temail = t@example.com\n[init]\n\tdefaultBranch = main\n" + extra,
                      encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_EDITOR", "GIT_ASKPASS",
                 "SSH_ASKPASS", "GIT_TRACE2_EVENT", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"):
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            monkeypatch.delenv(name)
    return config


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, "git %s failed: %s" % (" ".join(args), (done.stderr or done.stdout).strip())
    return done.stdout.strip()


def commit_file(repo, name, body, subject):
    path = pathlib.Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path, name="project"):
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q")
    commit_file(repo, "seed.txt", "seed\n", "seed")
    return repo


def add_worktree(repo, path):
    git(repo, "worktree", "add", "-q", "--detach", str(path), "HEAD")
    return pathlib.Path(path)


def trust(repo, value="true"):
    git(repo, "config", "--local", "sigma.allowRepositoryShellCommands", value)


def verify_scratch(tmp_path, monkeypatch):
    """A hermetic trusted project and a linked scratch worktree of it, for the documented `run_verify` gesture
    -> (project, scratch worktree)."""
    hermetic(tmp_path, monkeypatch)
    main = make_repo(tmp_path)
    trust(main)
    return main, add_worktree(main, tmp_path / "scratch")


class World:
    """A bare remote, a checkout of it with a feature branch of three commits, and an integration branch that moved."""

    def __init__(self, tmp_path):
        self.remote = tmp_path / "remote.git"
        self.local = tmp_path / "local"
        self.work = tmp_path / "replay"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True, capture_output=True)
        self.local.mkdir()
        git(self.local, "init", "-q")
        git(self.local, "remote", "add", "origin", str(self.remote))
        commit_file(self.local, "seed.txt", "seed\n", "seed")
        git(self.local, "push", "-q", "-u", "origin", "main")
        git(self.local, "checkout", "-q", "-b", "feature/x")
        for n in (1, 2, 3):
            self.tip = commit_file(self.local, "f%d.txt" % n, "f%d\n" % n, "feat: land goal %d (#%d)" % (n, n))
            if n == 2:
                git(self.local, "branch", "inner", "HEAD")
        git(self.local, "push", "-q", "origin", "feature/x")
        git(self.local, "checkout", "-q", "main")
        commit_file(self.local, "m.txt", "m\n", "integration moves")
        git(self.local, "push", "-q", "origin", "main")
        self.inner = git(self.local, "rev-parse", "inner")

    def remote_tip(self):
        return git(self.remote, "rev-parse", "feature/x")


# ------------------------------------------------------------------------------------------ scripts and processes

def script(path, text):
    path = pathlib.Path(path)
    path.write_text("#!/bin/sh\n" + text, encoding="utf-8")
    path.chmod(0o755)
    return path


def shim_git(tmp_path, monkeypatch, text):
    """Put a fake `git` first on PATH; the fake is the given shell text. -> the folder it lives in."""
    folder = tmp_path / "shim-bin"
    folder.mkdir(exist_ok=True)
    script(folder / "git", text)
    monkeypatch.setenv("PATH", str(folder) + os.pathsep + os.environ.get("PATH", ""))
    return folder


#: Run argv[2] as `__main__` with argv[1] unimportable. `sys.modules[name] = None` halts the import
#: ("import of X halted; None in sys.modules") whether X is a builtin, an extension or a file; a
#: `PYTHONPATH` shim cannot hide a builtin (Python 3.13 here builds `fcntl` in), and a
#: `sitecustomize.py` shim would shadow the interpreter's own (#2225).
WITHOUT_MODULE_DRIVER = (
    "import os, runpy, sys\n"
    "sys.modules[sys.argv[1]] = None\n"
    "sys.argv = sys.argv[2:]\n"
    "sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


def without_module(name, script_path, *args):
    """argv that runs `script_path *args` with module `name` hidden, as a platform without it would.

    Guards the "refuses without `flock`" tests: they must really run without `fcntl` on every interpreter,
    including one that builds it in. `tests/test_unattended_support_shims.py` proves it on this host."""
    return [sys.executable, "-c", WITHOUT_MODULE_DRIVER, name, str(script_path), *map(str, args)]


def pid_in(path, limit=10.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        try:
            text = pathlib.Path(path).read_text().strip()
            if text:
                return int(text)
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    raise AssertionError("no process id was written to %s within %gs" % (path, limit))


#: Where `alive()` reads a process's state. A host with no such folder (macOS) is asked through `ps` instead.
PROC = "/proc"


def alive(pid):
    """Running, not merely a zombie awaiting a reaper that is slow to come.

    Guards every "the group kill worked" check: `os.kill(pid, 0)` succeeds on a zombie, and a killed orphan stays one
    until PID 1 reaps it, which in the cloud container takes up to ~2 s (#2225). Reads `/proc/<pid>/stat`, or
    `ps -o stat=` on a host with no `/proc`."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if not os.path.isdir(PROC):
        return _ps_state(pid) != "Z"
    try:
        fields = pathlib.Path(PROC, "%d" % pid, "stat").read_text().rsplit(")", 1)[1].split()
        return fields[0] != "Z"
    except (OSError, IndexError):
        return True


def _ps_state(pid):
    """The first letter of `ps -o stat=` for `pid` ("Z" for a zombie), or "" when ps cannot say."""
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()[:1]


def gone(pid, limit=8.0):
    """True once `pid` is not running (see `alive()`: a zombie counts as gone), polling for up to `limit` seconds."""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


def reap(pid):
    """Clean up after a control that is MEANT to leak: stop the process group of `pid` if it is still there. A process
    that shares THIS process's own group is signalled alone: the group is the test runner's."""
    if pid and alive(pid):
        try:
            group = os.getpgid(pid)
            if group == os.getpgrp():
                os.kill(pid, signal.SIGKILL)
            else:
                os.killpg(group, signal.SIGKILL)
        except OSError:
            pass


def spy_popen(monkeypatch):
    """Record every subprocess.Popen call (argv) and let it through. -> the list."""
    real, seen = subprocess.Popen, []

    class Spy(real):
        def __init__(self, args, *a, **kw):
            seen.append(args)
            super().__init__(args, *a, **kw)
    monkeypatch.setattr(subprocess, "Popen", Spy)
    return seen


def non_git(seen):
    """The recorded launches that are not a plain git read."""
    return [a for a in seen if os.path.basename(str(a[0] if isinstance(a, (list, tuple)) else a)) != "git"]


def python_command(code):
    """A shell command line that runs `code` with this interpreter."""
    import shlex
    return "%s -c %s" % (shlex.quote(sys.executable), shlex.quote(code))


def auto_children(trace_file):
    """How many background maintenance or gc children a git trace2 event file shows git starting (`--auto`)."""
    found = 0
    try:
        lines = pathlib.Path(trace_file).read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "child_start" and "--auto" in (event.get("argv") or []):
            found += 1
    return found

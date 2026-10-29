"""Which Python runs a goal's planned tests (#2079).

`red_green` (planned-test proof and `loop.py red`), `flake_check` (verify's advisory flake pass) and `diff_revert`
(its advisory kill control) used to run `python3 -m pytest` with whatever `python3` came first on PATH. In a cloud
container that is a system interpreter with no pytest and none of the project's dependencies, and a wrong
interpreter can also MISLEAD: `diff_revert` credits a collection error on a new test as a kill, so an import
failure caused by the interpreter could be written as a STRONG `mutation` witness.

ORDER, decided from the filesystem and PATH only (no tracked setting chooses an executable; this module reads no
config and imports no trust gate):
  1. `<root>/.venv` with a `pyvenv.cfg`: that venv's interpreter.
  2. nothing at `<root>/.venv`, `uv.lock` and `pyproject.toml` present, `uv` on PATH, `UV_PROJECT_ENVIRONMENT`
     unset, no enclosing `[tool.uv.workspace]`: build the venv ONCE with `uv run --frozen --project <root>`, the
     project environment pinned to `<root>/.venv` in the child's environment only, in a process group of its own
     under a budget. pytest never runs under uv, `uv.lock` is never rewritten (`--frozen`), and uv never touches a
     venv it did not create (step 1 already took any `.venv`, and a `.venv` without `pyvenv.cfg` keeps uv out).
  3. otherwise `python3`, as before.
A build marker and an exclusive lock live in the worktree's git directory (never in the tree, so `git status` stays
clean): a process killed mid-build leaves the marker, and the next run, holding the lock, removes the half-made
venv it proves is its own and rebuilds; two processes never build at once. Every chosen interpreter is probed once with `-c "import pytest"`. A failure raises `InterpreterError` naming the
interpreter and how it was chosen; there is NO fallback to another interpreter. A failed or timed-out uv build
removes the venv it made, and the failure is cached for the process, so uv is run at most once per root.

CEILINGS, stated rather than discovered: the first call in a process can take up to `budget` seconds (the
build); callers wait on one lock, so concurrent callers in one process build once; two PROCESSES racing on one
worktree rely on uv's own lock on the environment (not measured); the budget is the caller's (`loop.py` passes
`verify.timeout_minutes`), never a constant here. A real uv build and a real container were NOT measured: every
test drives a fake `uv` and fake interpreters.
"""
import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import threading
import time

try:
    import fcntl
except ImportError:                      # no flock: uv builds are refused below, python3 is used
    fcntl = None

_LOCK = threading.Lock()
_CACHE = {}
_WORKSPACE = re.compile(r"^\s*\[tool\.uv\.workspace\]", re.M)
MARKER, LOCK = "sigma-uv-build.marker", "sigma-uv-build.lock"


class InterpreterError(ValueError):
    """The chosen interpreter cannot run pytest. The message names it and how it was chosen."""


class Resolution:
    def __init__(self, path, how, notes=()):
        self.path, self.how, self.notes, self.reported = path, how, list(notes), False

    def __repr__(self):
        return "Resolution(path=%r, how=%r, notes=%r)" % (self.path, self.how, self.notes)

    def report(self, emit):
        """Say once per process which interpreter is used when it is not the plain `python3`, and anything the
        build left behind. `emit(line)` is the caller's stderr writer."""
        if self.reported:
            return
        self.reported = True
        if self.path != "python3" or self.notes:
            emit("planned tests run with %s (%s)" % (self.path, self.how))
        for note in self.notes:
            emit(note)


def resolve(root, env=None, budget=3600):
    """-> `Resolution(path, how, notes)`; raises `InterpreterError`. Cached per (root, PATH, UV_PROJECT_ENVIRONMENT
    set?) for the life of the process, so a failure is not retried and a build happens once."""
    env = os.environ if env is None else env
    root = pathlib.Path(root)
    key = (str(root), env.get("PATH", ""), "UV_PROJECT_ENVIRONMENT" in env)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is None:
            try:
                hit = _resolve(root, dict(env), budget)
            except InterpreterError as exc:
                hit = exc
            _CACHE[key] = hit
    if isinstance(hit, InterpreterError):
        raise InterpreterError(str(hit))
    return hit


def _resolve(root, env, budget):
    venv = root / ".venv"
    gitdir = _git_dir(root)
    marker = gitdir / MARKER if gitdir else None
    if os.path.lexists(venv) and not (marker and marker.exists()):
        return _existing(root, env, budget)
    blockers = _uv_blockers(root, env, gitdir)
    if blockers and not os.path.lexists(venv):
        return _system(env, budget, "no .venv; uv was not used: " + "; ".join(blockers))
    # Either nothing is at .venv and uv applies, or a marker says an earlier build was cut short. Under the lock:
    # the lock is held only while a build runs, so a marker seen WITH the lock is a dead builder's.
    with _locked(gitdir / LOCK, budget):
        if marker.exists():
            _remove(venv)
            marker.unlink()
        elif os.path.lexists(venv):          # another process finished the build while we waited
            return _existing(root, env, budget)
        if blockers:
            return _system(env, budget, "the venv left by a cut-short uv build was removed; uv was not used now: "
                                        + "; ".join(blockers))
        return _build(root, env, budget, marker)


def _existing(root, env, budget):
    venv = root / ".venv"
    if (venv / "pyvenv.cfg").is_file():
        py = str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        how = "the goal worktree's .venv (pyvenv.cfg present)"
        _probe(py, how, env, budget)
        return Resolution(py, how)
    return _system(env, budget, ".venv exists at %s without a pyvenv.cfg, so it is not a virtualenv; uv was "
                                "not run because it never touches a venv it did not create" % venv)


def _git_dir(root):
    try:
        proc = subprocess.run(["git", "-C", str(root), "rev-parse", "--absolute-git-dir"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return pathlib.Path(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else None


class _locked:
    """An exclusive flock on `path`, polled until `budget` seconds; raises `InterpreterError` when it stays held."""

    def __init__(self, path, budget):
        self.path, self.budget, self.fd = path, budget, None

    def __enter__(self):
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        end = time.monotonic() + self.budget
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= end:
                    os.close(self.fd)
                    raise InterpreterError("another process is building this worktree's venv (lock %s held for "
                                           "%ss); nothing was run" % (self.path, self.budget)) from None
                time.sleep(0.05)

    def __exit__(self, *exc):
        os.close(self.fd)


def _system(env, budget, why):
    how = "python3 on PATH (%s)" % why
    _probe("python3", how, env, budget)
    return Resolution("python3", how)


def _uv_blockers(root, env, gitdir):
    out = []
    if os.name == "nt" or fcntl is None:
        out.append("uv builds need a process group and a file lock, which this platform cannot give")
    if gitdir is None:
        out.append("not a git worktree, so there is nowhere to keep the build marker")
    if not (root / "uv.lock").is_file():
        out.append("no uv.lock")
    if not (root / "pyproject.toml").is_file():
        out.append("no pyproject.toml")
    if not shutil.which("uv", path=env.get("PATH")):
        out.append("uv is not on PATH")
    if "UV_PROJECT_ENVIRONMENT" in env:
        out.append("UV_PROJECT_ENVIRONMENT is set")
    claimed = _workspace_claim(root)
    if claimed:
        out.append("an enclosing uv workspace at %s may claim this worktree" % claimed)
    return out


def _workspace_claim(root):
    """Any ancestor `pyproject.toml` with a `[tool.uv.workspace]` table. Deliberately conservative: it does not
    evaluate `members`/`exclude`, because guessing wrong would let uv replace an enclosing project's venv."""
    try:
        parents = list(pathlib.Path(root).resolve().parents)
    except OSError:
        return None
    for directory in parents:
        try:
            text = (directory / "pyproject.toml").read_text(errors="replace")
        except OSError:
            continue
        if _WORKSPACE.search(text):
            return directory
    return None


def _named(py):
    found = shutil.which(py) if os.sep not in py else None
    return "%s (%s)" % (py, found) if found else py


def _probe(py, how, env, budget):
    """Fail closed unless `py -c "import pytest"` exits 0."""
    try:
        proc = subprocess.run([py, "-c", "import pytest"], capture_output=True, text=True, env=env,
                              timeout=budget)
    except subprocess.TimeoutExpired:
        raise InterpreterError("interpreter %s (%s) did not answer `import pytest` within %ss"
                               % (_named(py), how, budget)) from None
    except OSError as exc:
        raise InterpreterError("interpreter %s (%s) cannot run: %s" % (_named(py), how, exc)) from None
    if proc.returncode:
        tail = " | ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-2:])
        raise InterpreterError("interpreter %s (%s) cannot run pytest (`-c \"import pytest\"` exit %s%s)"
                               % (_named(py), how, proc.returncode, ": " + tail if tail else ""))


def _porcelain(root):
    """The set of `git status --porcelain -uall` lines, or None when git cannot say."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "-uall"], capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return set(proc.stdout.splitlines()) if proc.returncode == 0 else None


def _remove(venv):
    """Only ever called for a venv this build created (the path was empty when the build started)."""
    if venv.is_symlink() or venv.is_file():
        venv.unlink()
    elif venv.is_dir():
        shutil.rmtree(venv, ignore_errors=True)


def _left_behind(root, before):
    if before is None:
        return ["git status was unavailable, so what the uv build left behind is unchecked"]
    after = _porcelain(root)
    if after is None:
        return ["git status was unavailable after the build, so what it left behind is unchecked"]
    extra = sorted(after - before)
    return ["the uv build left paths git does not ignore: " + ", ".join(line.strip() for line in extra)] if extra else []


def _bounded_run():
    spec = importlib.util.spec_from_file_location("bounded_run", pathlib.Path(__file__).with_name("bounded_run.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build(root, env, budget, marker):
    uv = shutil.which("uv", path=env.get("PATH"))
    venv = root / ".venv"
    how = ("built once from uv.lock by `uv run --frozen --project %s` with UV_PROJECT_ENVIRONMENT=%s"
           % (root, venv))
    py = str(venv / "bin/python")
    before = _porcelain(root)
    argv = [uv, "run", "--frozen", "--project", str(root), "python", "-c", "pass"]
    marker.write_text("pid %s\n" % os.getpid())          # a kill from here on leaves the marker, and the next run cleans up
    bounded = _bounded_run()
    done = bounded.run_group(argv, root, budget, env=dict(env, UV_PROJECT_ENVIRONMENT=str(venv)))
    problem = None
    if done.outcome != bounded.OK:
        tail = " | ".join((done.out or "").strip().splitlines()[-3:])
        problem = "uv %s%s%s" % (done.outcome, " (exit %s)" % done.code if done.code is not None else "",
                                 ": " + (done.detail or tail) if (done.detail or tail) else "")
        if done.outcome == bounded.TIMEOUT:
            problem += " (budget %ss)" % budget
    elif not (venv / "pyvenv.cfg").is_file():
        problem = "uv exited 0 but made no %s" % (venv / "pyvenv.cfg")
    if problem is None:
        try:
            _probe(py, how, env, budget)
        except InterpreterError as exc:
            problem = str(exc)
    if problem is not None:
        _remove(venv)
        marker.unlink()
        notes = _left_behind(root, before)
        raise InterpreterError("interpreter %s (%s) was not usable: %s; the venv it made was removed%s"
                               % (py, how, problem, "; " + "; ".join(notes) if notes else ""))
    marker.unlink()
    return Resolution(py, how, _left_behind(root, before))

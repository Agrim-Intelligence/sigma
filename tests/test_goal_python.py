"""#2079: a goal's planned tests run with the project's interpreter, not the first `python3` on PATH.

The resolver (`skills/sigma-loop/scripts/goal_python.py`) picks, in order: the goal worktree's own `.venv`
(`pyvenv.cfg` present), a venv built ONCE with `uv run --frozen --project <worktree>` under the exact AC-2
conditions, then `python3`. Every one of these tests uses fake interpreters and a fake `uv` on a temporary PATH
that holds no other `uv`: no network, no real `uv`, no real build. Fake runs are NOT evidence that a real uv build
or a real container behaves the same; the docs say so.

How this file is built (the red/green gate binds it byte for byte):
- Modules are loaded by path through `load`, which ASSERTS the file exists, so a missing module is an assertion
  failure and never an import error.
- Each guard clause has its own parametrised case, so breaking one clause on its own turns exactly its case red.
- Nothing prints, skips, or touches the network.
"""
import ast
import importlib.util
import inspect
import json
import os
import pathlib
import re
import shutil
import subprocess
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
S = REPO / "skills/sigma-loop/scripts"
NODE = "tests/test_x.py::test_a"
PLAN_TESTS = "# Plan\n\n## Tests\n- `%s`\n" % NODE
FAILING = "from pathlib import Path\ndef test_a():\n    assert Path('ready').exists()\n"


def load(name):
    assert (S / f"{name}.py").exists(), f"{name}.py must exist"
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- fakes ------------------------------------------------------------------------------------------------

PY_TEMPLATE = """#!/bin/sh
echo "$0 $*" >> "{log}"
case "$*" in
  *"import pytest"*) exit {probe_rc};;
  *"import xdist"*) exit 1;;
  *"--collect-only"*) echo "tests/test_x.py::test_a"; exit 0;;
esac
echo "=== short test summary info ==="
{summary}
exit {rc}
"""

UV_TEMPLATE = """#!/bin/sh
echo "args=$*" >> "{log}"
echo "env=$UV_PROJECT_ENVIRONMENT" >> "{log}"
echo "ids=$$ $(ps -o pgid= -p $$ | tr -d ' ')" >> "{log}"
case "{mode}" in
  fail) echo "uv: boom" >&2; exit 1;;
  hang) sleep 60 & echo $! > "{log}.child"; wait; exit 0;;
esac
[ "{mode}" = leave ] && echo stray > stray.txt
[ "{mode}" = relock ] && echo changed >> uv.lock
mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
echo "home = fake" > "$UV_PROJECT_ENVIRONMENT/pyvenv.cfg"
cp "{template}" "$UV_PROJECT_ENVIRONMENT/bin/python"
chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"
exit 0
"""

TOOLS = ("git", "sh", "env", "ps", "cp", "sleep", "mkdir", "cat", "tr", "dirname", "chmod", "uname", "true")


class Env:
    """A temp PATH that holds only symlinks to the few system tools the fakes need, plus the fakes themselves."""

    def __init__(self, tmp_path, monkeypatch):
        self.bin = tmp_path / "fakebin"
        self.bin.mkdir()
        for tool in TOOLS:
            found = shutil.which(tool)
            if found:
                (self.bin / tool).symlink_to(found)
        monkeypatch.setenv("PATH", str(self.bin))
        monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        self.n = 0

    def py(self, path, *, probe_rc=0, rc=0, summary="echo PASSED %s" % NODE):
        self.n += 1
        log = self.logs / f"py{self.n}.log"
        log.write_text("")
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PY_TEMPLATE.format(log=log, probe_rc=probe_rc, rc=rc, summary=summary))
        path.chmod(0o755)
        return log

    def uv(self, mode="ok", venv_probe_rc=0):
        template = self.bin.parent / "uv-made-python"
        tlog = self.py(template, probe_rc=venv_probe_rc)
        log = self.logs / "uv.log"
        log.write_text("")
        uv = self.bin / "uv"
        uv.write_text(UV_TEMPLATE.format(log=log, mode=mode, template=template))
        uv.chmod(0o755)
        return log, tlog

    @staticmethod
    def calls(log):
        return [line for line in log.read_text().splitlines() if line]


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


def project(tmp_path, *, lock=True, pyproject=True, ignore_venv=True, name="wt"):
    """A git repo that is a uv project, committed, so `git status` is clean before any build."""
    root = tmp_path / name
    (root / "tests").mkdir(parents=True)
    (root / "tests/test_x.py").write_text(FAILING)
    plan = root / ".sdlc/plans/42.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(PLAN_TESTS)
    if pyproject:
        (root / "pyproject.toml").write_text("[project]\nname = 'p'\nversion = '0'\n")
    if lock:
        (root / "uv.lock").write_text("version = 1\n")
    if ignore_venv:
        (root / ".gitignore").write_text(".venv/\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def venv(env, root, **kw):
    """A venv at <root>/.venv with a fake interpreter; returns (interpreter path, its log)."""
    (root / ".venv").mkdir(exist_ok=True)
    (root / ".venv/pyvenv.cfg").write_text("home = fake\n")
    py = root / ".venv/bin/python"
    return py, env.py(py, **kw)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith("Z")


# --- AC-1: a venv at .venv is the interpreter --------------------------------------------------------------

def test_a_venv_with_pyvenv_cfg_is_the_interpreter_and_nothing_else_runs(tmp_path, env):
    root = project(tmp_path)
    py, pylog = venv(env, root)
    p3log = env.py(env.bin / "python3")
    uvlog, _ = env.uv()
    got = load("goal_python").resolve(root)
    assert got.path == str(py), got
    assert ".venv" in got.how and "pyvenv.cfg" in got.how, got.how
    assert env.calls(p3log) == [] and env.calls(uvlog) == []
    assert any("import pytest" in c for c in env.calls(pylog))


def test_every_pytest_argv_in_the_three_modules_starts_with_the_interpreter_it_was_handed(tmp_path):
    py = "/opt/fake/venv/bin/python"
    rg, fc, dr = load("red_green"), load("flake_check"), load("diff_revert")
    for fn in (rg.observe, fc.check, fc._collect, dr.run):
        assert "python" in inspect.signature(fn).parameters, fn.__name__
    plan = tmp_path / "plan.md"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text("def test_a():\n    pass\n")
    plan.write_text(PLAN_TESTS)
    seen = []

    def run(root, argv):
        seen.append(("rg", argv[0]))
        if "--collect-only" in argv:
            return subprocess.CompletedProcess(argv, 0, NODE + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "=== short test summary info ===\nPASSED %s\n" % NODE, "")

    proof = rg.observe(tmp_path / ".sdlc", "42", tmp_path, plan, run=run, python=py)
    assert proof.get("passed") is True, proof
    assert seen and {a for _, a in seen} == {py}, seen

    seen.clear()

    def frun(cwd, argv):
        seen.append(("fc", argv[0]))
        return 0, ""

    fc.check(tmp_path, ["tests/test_x.py"], run=frun, node_ids=[NODE], xdist=False, python=py)
    fc._collect(tmp_path, ["tests/test_x.py"], frun, python=py)
    assert len(seen) == 4 and {a for _, a in seen} == {py}, seen

    seen.clear()

    def drun(cwd, argv, timeout):
        seen.append(("dr", argv[0]))
        return 0, ""

    def dgit(root, argv, timeout=None):
        return (0, "deadbeef") if argv[0] == "merge-base" else (0, "")

    dr.run(str(tmp_path / ".sdlc"), "42", str(tmp_path), "origin/main", [NODE], git_cmd=dgit, run_cmd=drun,
           python=py)
    assert seen == [("dr", py)], seen


def test_the_xdist_probe_asks_the_interpreter_it_was_handed(tmp_path, env):
    py = tmp_path / "venv-python"
    log = env.py(py)
    assert "python" in inspect.signature(load("flake_check")._has_xdist).parameters
    load("flake_check")._has_xdist(tmp_path, python=str(py))
    assert any("import xdist" in c for c in env.calls(log)), env.calls(log)


def test_without_a_python_argument_the_three_modules_still_say_python3():
    """AC-3's `exactly as today` at the module seam: the default is the literal name."""
    rg, fc, dr = load("red_green"), load("flake_check"), load("diff_revert")
    for fn in (rg.collect, rg.observe, fc.check, fc._collect, fc._has_xdist, dr.run):
        param = inspect.signature(fn).parameters.get("python")
        assert param is not None and param.default == "python3", fn.__name__
    seen = []

    def run(root, argv):
        seen.append(argv[0])
        return subprocess.CompletedProcess(argv, 0, NODE + "\n", "")

    rg.collect("/r", [NODE], run)
    fc._collect("/r", ["tests/test_x.py"], lambda cwd, argv: seen.append(argv[0]) or (0, ""))
    assert seen == ["python3", "python3"], seen


def _modules_ast(name):
    return ast.parse((S / f"{name}.py").read_text())


def test_no_pytest_argv_in_the_three_modules_hardcodes_python3():
    """The only `python3` string allowed is the DEFAULT of a `python` parameter."""
    for name in ("red_green", "flake_check", "diff_revert"):
        tree = _modules_ast(name)
        allowed = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.arguments):
                allowed.update(id(d) for d in node.defaults + node.kw_defaults if d is not None)
        bad = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and n.value == "python3" and id(n) not in allowed]
        assert not bad, f"{name}.py hardcodes 'python3' at lines {bad}"


def test_every_loop_call_site_of_the_three_modules_passes_python():
    tree = _modules_ast("loop")
    sites, missing = [], []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr, base = node.func.attr, node.func.value
        basename = base.id if isinstance(base, ast.Name) else ""
        hit = (attr in ("observe", "collect")
               or (basename == "flake_check" and attr in ("check", "_collect"))
               or (basename == "diff_revert" and attr == "run"))
        if not hit:
            continue
        sites.append(node.lineno)
        if "python" not in {k.arg for k in node.keywords}:
            missing.append(node.lineno)
    assert len(sites) >= 7, sites
    assert not missing, f"loop.py call sites without python=: {missing}"


def test_loop_hands_the_resolved_interpreter_to_flake_and_diff_revert(tmp_path, env, monkeypatch):
    root = project(tmp_path)
    py, _ = venv(env, root)
    lp = load("loop")
    seen = {}

    def check(r, files, **kw):
        seen["flake"] = kw.get("python")
        return {"verdict": "verified"}

    monkeypatch.setattr(lp.flake_check, "check", check)
    monkeypatch.setattr(lp, "_planned_scope", lambda *a, **k: ([NODE], [NODE]))
    assert lp._flake_verdict(root / ".sdlc", "42", root, True)["verdict"] == "verified"
    assert seen["flake"] == str(py), seen


# --- AC-2: a uv project without a venv ----------------------------------------------------------------------

def test_a_uv_project_without_a_venv_builds_it_once_pinned_and_in_its_own_group(tmp_path, env):
    root = project(tmp_path)
    uvlog, tlog = env.uv()
    gp = load("goal_python")
    got = gp.resolve(root)
    assert got.path == str(root / ".venv/bin/python"), got
    assert "uv" in got.how and "--frozen" in got.how, got.how
    calls = env.calls(uvlog)
    args = [c for c in calls if c.startswith("args=")]
    assert len(args) == 1, calls
    assert args[0].startswith("args=run --frozen --project %s" % root), args
    assert "pytest" not in args[0].replace(str(root), ""), args
    assert os.path.realpath(calls[1].split("=", 1)[1]) == os.path.realpath(str(root / ".venv")), calls
    pid, pgid = calls[2].split("=", 1)[1].split()
    assert pid == pgid and int(pgid) != os.getpgrp(), calls
    assert (root / "uv.lock").read_text() == "version = 1\n"
    assert any("import pytest" in c for c in env.calls(tlog))
    again = gp.resolve(root)
    assert again.path == got.path and len([c for c in env.calls(uvlog) if c.startswith("args=")]) == 1


def _clause_venv_dir_without_cfg(root, env, mp):
    (root / ".venv").mkdir()


def _clause_no_lock(root, env, mp):
    (root / "uv.lock").unlink()


def _clause_no_pyproject(root, env, mp):
    (root / "pyproject.toml").unlink()


def _clause_no_uv_on_path(root, env, mp):
    (env.bin / "uv").unlink()


def _clause_project_env_set(root, env, mp):
    mp.setenv("UV_PROJECT_ENVIRONMENT", str(root.parent / "elsewhere"))


def _clause_enclosing_workspace(root, env, mp):
    (root.parent / "pyproject.toml").write_text("[tool.uv.workspace]\nmembers = ['*']\n")


CLAUSES = [_clause_venv_dir_without_cfg, _clause_no_lock, _clause_no_pyproject, _clause_no_uv_on_path,
           _clause_project_env_set, _clause_enclosing_workspace]


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda c: c.__name__[len("_clause_"):])
def test_uv_is_never_run_when_any_single_condition_is_missing(tmp_path, env, monkeypatch, clause):
    root = project(tmp_path, name="sub/wt" if clause is _clause_enclosing_workspace else "wt")
    uvlog, _ = env.uv()
    p3log = env.py(env.bin / "python3")
    clause(root, env, monkeypatch)
    got = load("goal_python").resolve(root)
    assert env.calls(uvlog) == [], env.calls(uvlog)
    assert got.path == "python3", got
    assert any("import pytest" in c for c in env.calls(p3log))


# --- AC-3: neither ------------------------------------------------------------------------------------------

def test_with_neither_the_interpreter_is_python3_as_today_and_says_why_uv_was_not_used(tmp_path, env):
    root = project(tmp_path, lock=False, pyproject=False)
    p3log = env.py(env.bin / "python3")
    got = load("goal_python").resolve(root)
    assert got.path == "python3", got
    assert "python3" in got.how and "uv.lock" in got.how, got.how
    assert any("import pytest" in c for c in env.calls(p3log))


# --- AC-4: a broken interpreter refuses, names itself, and never falls back --------------------------------------

def test_a_venv_whose_interpreter_is_missing_refuses_naming_it_and_runs_nothing_else(tmp_path, env):
    root = project(tmp_path)
    (root / ".venv").mkdir()
    (root / ".venv/pyvenv.cfg").write_text("home = fake\n")
    p3log = env.py(env.bin / "python3")
    uvlog, _ = env.uv()
    gp = load("goal_python")
    with pytest.raises(gp.InterpreterError) as exc:
        gp.resolve(root)
    msg = str(exc.value)
    assert str(root / ".venv/bin/python") in msg and "pyvenv.cfg" in msg, msg
    assert env.calls(p3log) == [] and env.calls(uvlog) == []
    assert isinstance(exc.value, ValueError)


def test_an_interpreter_that_cannot_import_pytest_refuses_and_never_falls_back_to_python3(tmp_path, env):
    root = project(tmp_path)
    py, _ = venv(env, root, probe_rc=1)
    p3log = env.py(env.bin / "python3")
    gp = load("goal_python")
    with pytest.raises(gp.InterpreterError) as exc:
        gp.resolve(root)
    assert str(py) in str(exc.value) and "pytest" in str(exc.value) and ".venv" in str(exc.value), exc.value
    assert env.calls(p3log) == []


def test_a_python3_that_cannot_import_pytest_refuses_naming_python3(tmp_path, env):
    root = project(tmp_path, lock=False, pyproject=False)
    env.py(env.bin / "python3", probe_rc=1)
    gp = load("goal_python")
    with pytest.raises(gp.InterpreterError) as exc:
        gp.resolve(root)
    assert "python3" in str(exc.value) and "pytest" in str(exc.value), exc.value


def test_a_failed_uv_build_removes_the_venv_it_made_and_is_not_retried_in_the_process(tmp_path, env):
    root = project(tmp_path)
    uvlog, _ = env.uv(mode="fail")
    gp = load("goal_python")
    with pytest.raises(gp.InterpreterError) as first:
        gp.resolve(root)
    assert "uv" in str(first.value) and "boom" in str(first.value), first.value
    assert not (root / ".venv").exists()
    with pytest.raises(gp.InterpreterError):
        gp.resolve(root)
    assert len([c for c in env.calls(uvlog) if c.startswith("args=")]) == 1


def test_a_uv_build_that_overruns_its_budget_is_killed_as_a_group_and_cleaned_up(tmp_path, env):
    root = project(tmp_path)
    uvlog, _ = env.uv(mode="hang")
    gp = load("goal_python")
    started = time.time()
    with pytest.raises(gp.InterpreterError) as exc:
        gp.resolve(root, budget=1)
    assert time.time() - started < 20
    assert "budget" in str(exc.value) or "timed out" in str(exc.value), exc.value
    pid = int(env.calls(uvlog)[2].split("=", 1)[1].split()[0])
    child = int(pathlib.Path(str(uvlog) + ".child").read_text())
    deadline = time.time() + 5
    while time.time() < deadline and (alive(pid) or alive(child)):
        time.sleep(0.05)
    assert not alive(pid) and not alive(child), "the build's whole process group must be gone"
    assert not (root / ".venv").exists()


def test_a_uv_built_interpreter_that_cannot_import_pytest_refuses_and_removes_the_venv(tmp_path, env):
    root = project(tmp_path)
    env.uv(venv_probe_rc=1)
    gp = load("goal_python")
    with pytest.raises(gp.InterpreterError) as exc:
        gp.resolve(root)
    assert str(root / ".venv/bin/python") in str(exc.value) and "pytest" in str(exc.value), exc.value
    assert not (root / ".venv").exists()


def test_what_the_build_leaves_that_git_does_not_ignore_is_named(tmp_path, env):
    root = project(tmp_path, ignore_venv=False)
    env.uv(mode="leave")
    got = load("goal_python").resolve(root)
    named = " ".join(got.notes)
    assert "stray.txt" in named and ".venv" in named, got.notes


def test_a_rewritten_uv_lock_is_named_even_when_the_build_fails_afterwards(tmp_path, env):
    root = project(tmp_path)
    env.uv(mode="relock", venv_probe_rc=1)
    gp = load("goal_python")
    with pytest.raises(gp.InterpreterError) as exc:
        gp.resolve(root)
    assert "uv.lock" in str(exc.value), exc.value


def test_loop_red_refuses_with_exit_2_naming_interpreter_and_how_and_records_nothing(tmp_path, env, monkeypatch,
                                                                                    capsys):
    root = project(tmp_path)
    py, pylog = venv(env, root, probe_rc=1)
    p3log = env.py(env.bin / "python3", rc=1, summary="echo FAILED %s - assert False" % NODE)
    lp = _red_loop(root, monkeypatch)
    assert lp.red_goal(root / ".sdlc", "42") == 2
    err = capsys.readouterr().err
    assert str(py) in err and ".venv" in err and "pytest" in err, err
    assert env.calls(p3log) == []
    assert not [c for c in env.calls(pylog) if "-m pytest" in c]
    assert load("witness").witnesses(root / ".sdlc", "42") == []


def _red_loop(root, monkeypatch, advisory_stubs=True):
    git(root, "config", "--local", "sigma.allowRepositoryShellCommands", "true")
    (root / ".sdlc/config.json").write_text(json.dumps({"verify": {"command": "true"}}))
    lp = load("loop")
    monkeypatch.setattr(lp.work, "ensure_fresh", lambda *a: "")
    monkeypatch.setattr(lp, "_verified_tree", lambda *a: (str(root), "head", False))
    if advisory_stubs:
        monkeypatch.setattr(lp, "_flake_verdict", lambda *a: {"verdict": "absent"})
        monkeypatch.setattr(lp, "_diff_revert_verdict", lambda *a: {"verdict": "absent"})
    return lp


def test_loop_red_records_the_red_under_the_venv_interpreter_and_never_runs_python3(tmp_path, env, monkeypatch):
    root = project(tmp_path)
    py, pylog = venv(env, root, rc=1, summary="echo FAILED %s - assert False" % NODE)
    p3log = env.py(env.bin / "python3", rc=1, summary="echo FAILED %s - assert False" % NODE)
    lp = _red_loop(root, monkeypatch)
    assert lp.red_goal(root / ".sdlc", "42") == 0
    assert env.calls(p3log) == []
    assert [c for c in env.calls(pylog) if "--collect-only" in c] and [c for c in env.calls(pylog) if "-rA" in c]
    assert len(load("witness").witnesses(root / ".sdlc", "42")) == 1


def test_verify_with_a_broken_interpreter_records_no_planned_test_proof_and_no_red(tmp_path, env, monkeypatch,
                                                                                  capsys):
    root = project(tmp_path)
    py, pylog = venv(env, root, probe_rc=1)
    lp = _red_loop(root, monkeypatch)
    lp.verify_goal(root / ".sdlc", "42")
    err = capsys.readouterr().err
    assert str(py) in err and ".venv" in err and "planned tests not observed" in err, err
    data = json.loads(lp.state.evidence_path(root / ".sdlc", "42").read_text())
    first = data["test_first"]
    assert "provenance" not in first and first.get("passed") is False, first
    assert str(py) in first.get("error", "") and ".venv" in first.get("error", ""), first
    assert load("red_green").refusal(root / ".sdlc", "42", root, root / ".sdlc/plans/42.md", first)
    assert load("witness").witnesses(root / ".sdlc", "42") == []
    assert not [c for c in env.calls(pylog) if "-m pytest" in c]


def test_a_build_cut_short_is_cleaned_up_and_rebuilt_by_the_next_run(tmp_path, env):
    """A killed builder leaves its marker (in the git dir, outside the tree) and a half-made venv. The next run
    holds the lock, so the marker is proof the venv is its own: it is removed and built again."""
    root = project(tmp_path)
    gp = load("goal_python")
    venv(env, root, probe_rc=1)
    (root / ".git" / gp.MARKER).write_text("pid 1\n")
    uvlog, _ = env.uv()
    got = gp.resolve(root)
    assert got.path == str(root / ".venv/bin/python") and "uv" in got.how, got
    assert len([c for c in env.calls(uvlog) if c.startswith("args=")]) == 1
    assert not (root / ".git" / gp.MARKER).exists()


def test_a_cut_short_build_with_uv_gone_removes_the_partial_venv_and_says_so(tmp_path, env):
    root = project(tmp_path)
    gp = load("goal_python")
    venv(env, root, probe_rc=1)
    (root / ".git" / gp.MARKER).write_text("pid 1\n")
    p3log = env.py(env.bin / "python3")
    got = gp.resolve(root)
    assert got.path == "python3" and "cut-short" in got.how, got
    assert not (root / ".venv").exists()


def test_a_build_lock_held_by_another_process_stops_the_run_and_runs_nothing(tmp_path, env):
    import fcntl
    root = project(tmp_path)
    gp = load("goal_python")
    uvlog, _ = env.uv()
    fd = os.open(str(root / ".git" / gp.LOCK), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with pytest.raises(gp.InterpreterError) as exc:
            gp.resolve(root, budget=1)
    finally:
        os.close(fd)
    assert "lock" in str(exc.value), exc.value
    assert env.calls(uvlog) == [] and not (root / ".venv").exists()


def test_verify_runs_the_collect_the_run_the_flake_passes_and_the_kill_control_under_the_venv(tmp_path, env,
                                                                                            monkeypatch):
    """Behavioural, through the real loop.py functions: reverting any one `python=` at a call site sends that
    step to the `python3` shim, which logs and fails."""
    root = project(tmp_path)
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    (root / "tests/test_y.py").write_text("def test_b():\n    pass\n")
    py, pylog = venv(env, root)
    p3log = env.py(env.bin / "python3", probe_rc=1, rc=1, summary="echo FAILED %s - assert False" % NODE)
    lp = _red_loop(root, monkeypatch, advisory_stubs=False)
    monkeypatch.setattr(lp.work, "_record", lambda *a, **k: {"remote": "origin", "base": "main"})
    lp.verify_goal(root / ".sdlc", "42")
    ran = [c for c in env.calls(pylog) if "-m pytest" in c]
    assert [c for c in ran if "--collect-only" in c], ran
    assert [c for c in ran if "-rA" in c], ran
    assert [c for c in ran if "cache_dir=" in c], ran
    assert [c for c in ran if "-q --color=no" in c and "-rA" not in c and "--collect-only" not in c], ran
    assert env.calls(p3log) == [], env.calls(p3log)


# --- AC-5: the trust gate and the settings are untouched ------------------------------------------------------

def test_the_resolver_reads_no_tracked_setting_and_imports_no_trust_gate(tmp_path, env):
    root = project(tmp_path)
    p3log = env.py(env.bin / "python3")
    (root / ".sdlc/config.json").write_text(json.dumps({"python": "/evil/python", "test_python": "/evil/python",
                                                         "verify": {"python": "/evil/python"}}))
    got = load("goal_python").resolve(root)
    assert got.path == "python3" or "evil" not in got.path, got
    source = (S / "goal_python.py").read_text()
    assert "shell_policy" not in source and "config.json" not in source and "load_config" not in source


def test_the_resolver_runs_only_after_the_trust_gate_has_accepted(tmp_path, env, monkeypatch, capsys):
    """No `sigma.allowRepositoryShellCommands` and no environment approval: `red` refuses at the gate and
    the resolver is never reached, so a hostile `.venv` cannot run before the operator's consent."""
    root = project(tmp_path)
    venv(env, root)
    (root / ".sdlc/config.json").write_text(json.dumps({"verify": {"command": "true"}}))
    monkeypatch.delenv("SIGMA_TRUSTED_VERIFY_SHA256", raising=False)
    lp = load("loop")
    gp = load("goal_python")
    asked = []
    monkeypatch.setattr(gp, "resolve", lambda *a, **k: asked.append(a) or pytest.fail("resolver ran"))
    monkeypatch.setattr(lp, "goal_python", gp)
    monkeypatch.setattr(lp, "_verified_tree", lambda *a: (str(root), "head", False))
    assert lp.red_goal(root / ".sdlc", "42") == 2
    assert not asked


# --- docs -----------------------------------------------------------------------------------------------------

def test_the_docs_state_the_order_and_what_was_not_measured():
    config = (REPO / "docs/configuration.md").read_text()
    assert re.search(r"#2079", config) and "pyvenv.cfg" in config and "uv run --frozen" in config, "configuration.md"
    assert "python3" in config and re.search(r"not measured|NOT measured|unmeasured|UNMEASURED", config)
    cloud = (REPO / "docs/cloud-sessions.md").read_text()
    assert "#2079" in cloud and "/usr/bin/python3" in cloud and "uv run --frozen" in cloud, "cloud-sessions.md"
    log = (REPO / "CHANGELOG.md").read_text()
    unreleased = log.split("## Unreleased", 1)[1].split("\n## ", 1)[0]
    assert "#2079" in unreleased and "uv" in unreleased and "interpreter" in unreleased, "CHANGELOG.md"

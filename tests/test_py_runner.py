"""The python-hook interpreter guard. Sigma's hooks shell out to a bare `python3` from PATH, which
on a multi-python machine can be a BROKEN shim (a pyenv version that isn't installed, a half-broken conda
base) — and since the hooks run on every edit/search, that failed the session on first use for a real
adopter. `hooks/_py.sh` (for the .py hooks) and `agrim_gate.sh`'s preflight (for the prompt hook) must
degrade to a no-op instead of erroring. These tests drive both with a controlled PATH."""
import os
import pathlib
import shutil
import stat
import subprocess

HOOKS = pathlib.Path(__file__).resolve().parent.parent / "hooks"
PY_SH = HOOKS / "_py.sh"
GATE = HOOKS / "agrim_gate.sh"
import sys

REAL_PY = sys.executable


def _bindir(tmp_path, name, python3="real"):
    """A PATH dir with only `bash` and (optionally) a `python3`. python3='real' symlinks the working
    interpreter; 'broken' installs a shim that exits non-zero; None leaves it absent."""
    d = tmp_path / name
    d.mkdir()
    (d / "bash").symlink_to(shutil.which("bash"))
    if python3 == "real":
        (d / "python3").symlink_to(REAL_PY)
    elif python3 == "broken":
        shim = d / "python3"
        shim.write_text("#!/bin/sh\nexit 1\n")                # exists, but never runs a program
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(d)


def _run(cmd, pathdir, stdin="", **env_extra):
    return subprocess.run(cmd, capture_output=True, text=True, input=stdin,
                          env={"PATH": pathdir, **env_extra})


# ------------------------------------------------------------------ _py.sh


def test_py_runner_runs_a_working_interpreter(tmp_path):
    (tmp_path / "ok.py").write_text("print('ran')\n")
    r = _run(["bash", str(PY_SH), str(tmp_path / "ok.py")], _bindir(tmp_path, "b", "real"))
    assert r.returncode == 0 and "ran" in r.stdout


def test_py_runner_passes_through_the_exit_code(tmp_path):
    (tmp_path / "boom.py").write_text("import sys; sys.exit(2)\n")
    r = _run(["bash", str(PY_SH), str(tmp_path / "boom.py")], _bindir(tmp_path, "b", "real"))
    assert r.returncode == 2                                   # a hook can still deny via its exit code


def test_py_runner_fails_open_on_a_broken_interpreter(tmp_path):
    (tmp_path / "never.py").write_text("raise SystemExit(3)\n")
    r = _run(["bash", str(PY_SH), str(tmp_path / "never.py")], _bindir(tmp_path, "b", "broken"))
    assert r.returncode == 0                                   # broken python3 -> no-op, not a failed hook


def test_py_runner_fails_open_when_python_is_absent(tmp_path):
    r = _run(["bash", str(PY_SH), str(tmp_path / "x.py")], _bindir(tmp_path, "b", None))
    assert r.returncode == 0


# ------------------------------------------------------------------ agrim_gate.sh preflight


def test_prompt_gate_survives_a_broken_interpreter(tmp_path):
    """A broken python3 must not fail the UserPromptSubmit hook on every prompt — it falls back to the
    static policy and exits 0."""
    # SIGMA_GATE_GLOBAL=1 bypasses the per-repo scoping guard so we actually reach the interpreter
    # preflight (the scoping guard would otherwise no-op first, since the temp env has no .sdlc/).
    r = _run(["bash", str(GATE)], _bindir(tmp_path, "b", "broken"),
             stdin='{"prompt": "implement the parser"}', SIGMA_GATE_GLOBAL="1")
    assert r.returncode == 0 and "GOAL-BASED SDLC" in r.stdout

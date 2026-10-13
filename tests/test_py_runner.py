"""The python-hook interpreter guard. Sigma's hooks shell out to a bare `python3` from PATH, which
on a multi-python machine can be a BROKEN shim (a pyenv version that isn't installed, a half-broken conda
base) — and since the hooks run on every edit/search, that failed the session on first use for a real
adopter. `hooks/_py.sh` (for the .py hooks) and `sigma_gate.sh`'s preflight (for the prompt hook) must
degrade to a no-op instead of erroring. These tests drive both with a controlled PATH."""
import os
import pathlib
import shutil
import stat
import subprocess

HOOKS = pathlib.Path(__file__).resolve().parent.parent / "hooks"
PY_SH = HOOKS / "_py.sh"
GATE = HOOKS / "sigma_gate.sh"
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


# ------------------------------------------------------------------ sigma_gate.sh preflight


def test_prompt_gate_survives_a_broken_interpreter(tmp_path):
    """A broken python3 must not fail the UserPromptSubmit hook on every prompt — it falls back to the
    static policy and exits 0."""
    # SIGMA_GATE_GLOBAL=1 bypasses the per-repo scoping guard so we actually reach the interpreter
    # preflight (the scoping guard would otherwise no-op first, since the temp env has no .sdlc/).
    r = _run(["bash", str(GATE)], _bindir(tmp_path, "b", "broken"),
             stdin='{"prompt": "implement the parser"}', SIGMA_GATE_GLOBAL="1")
    assert r.returncode == 0 and "GOAL-BASED SDLC" in r.stdout


# ------------------------------------------------------------------ the failure is LOUD (#1282)
# Policy: allow-with-notice. docs/enforcement.md documents issue_field_gate and decision_gate as
# "fails open", so a broken python3 must not block tool calls -- but it must never be silent either.

import json
import re

HOOKS_JSON = HOOKS / "hooks.json"


def _commands():
    """Every hooks.json command that goes through _py.sh -- the exact string the HOST runs."""
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    return [h["command"] for groups in data["hooks"].values() for g in groups for h in g["hooks"]
            if "_py.sh" in h["command"]]


def _host_run(tmp_path, command, pathdir, adopted=True, name="proj", stdin="{}"):
    proj = tmp_path / name
    (proj / ".sdlc").mkdir(parents=True, exist_ok=True)
    if adopted:
        (proj / ".sdlc" / "config.json").write_text("{}")
    tmpdir = tmp_path / f"tmp-{name}"          # one notice marker dir per simulated session
    tmpdir.mkdir(exist_ok=True)
    return subprocess.run(["bash", "-c", command], capture_output=True, text=True, cwd=proj,
                          input=stdin, env={"PATH": pathdir, "CLAUDE_PLUGIN_ROOT": str(HOOKS.parent),
                                            "TMPDIR": str(tmpdir)})


def test_there_are_python_backed_hooks_to_cover():
    assert len(_commands()) >= 5


def test_every_py_hook_is_loud_when_python_is_broken_or_absent(tmp_path):
    for i, command in enumerate(_commands()):
        for kind in ("broken", None):
            r = _host_run(tmp_path, command, _bindir(tmp_path, f"b{i}{kind}", kind),
                          name=f"p{i}{kind}")
            script = re.search(r"hooks/(\w+)\.py", command).group(1)
            assert r.returncode == 0, (command, kind, r.stderr)        # allow: never wedge the host
            msg = json.loads(r.stdout)["systemMessage"]                # a form the host surfaces
            assert script in msg and "python3" in msg and "/sigma-doctor" in msg, msg


def test_notice_names_the_missing_guarantee(tmp_path):
    cmd = next(c for c in _commands() if "issue_field_gate" in c)
    r = _host_run(tmp_path, cmd, _bindir(tmp_path, "b", "broken"))
    assert "priority" in json.loads(r.stdout)["systemMessage"]
    cmd = next(c for c in _commands() if "decision_gate" in c)
    r = _host_run(tmp_path, cmd, _bindir(tmp_path, "b2", "broken"), name="p2")
    assert "decision" in json.loads(r.stdout)["systemMessage"]


def test_notice_is_once_per_session_not_per_call(tmp_path):
    cmd = _commands()[0]
    bindir = _bindir(tmp_path, "b", "broken")
    assert _host_run(tmp_path, cmd, bindir).stdout
    second = _host_run(tmp_path, cmd, bindir)
    assert second.returncode == 0 and second.stdout == ""     # marker keys on the parent pid


def test_no_notice_outside_an_adopted_repo(tmp_path):
    r = _host_run(tmp_path, _commands()[0], _bindir(tmp_path, "b", "broken"), adopted=False)
    assert r.returncode == 0 and r.stdout == ""


def test_working_python_prints_no_notice(tmp_path):
    cmd = next(c for c in _commands() if "time_track" in c)
    r = _host_run(tmp_path, cmd, _bindir(tmp_path, "b", "real"))
    assert "systemMessage" not in r.stdout

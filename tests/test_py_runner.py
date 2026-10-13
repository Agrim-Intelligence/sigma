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
    # /bin/bash first: on macOS that is the 3.2.57 the host's `bash hooks/_py.sh` really gets, and the
    # place the quadratic stdin read bites; a Homebrew bash 5 would hide it.
    (d / "bash").symlink_to("/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash"))
    (d / "mkdir").symlink_to(shutil.which("mkdir"))      # the only external the notice marker needs
    if python3 == "real":
        (d / "python3").symlink_to(REAL_PY)
    elif python3 == "broken":
        shim = d / "python3"
        shim.write_text("#!/bin/sh\nexit 1\n")                # exists, but never runs a program
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(d)


def _group_run(cmd, stdin="", timeout=20, **kw):
    """subprocess.run that cannot hang the suite: the child gets its OWN process group and on timeout the
    WHOLE group is SIGKILLed before the pipes are drained (`subprocess.run(timeout=)` kills only the direct
    child, and a surviving grandchild keeps the pipes open so the suite hangs instead of failing)."""
    import signal
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True, **kw)
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)       # leave no straggler behind a passing run either
        except ProcessLookupError:
            pass
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _run(cmd, pathdir, stdin="", **env_extra):
    return _group_run(cmd, stdin, env={"PATH": pathdir, **env_extra})


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


def _host_run(tmp_path, command, pathdir, adopted=True, name="proj", stdin="{}", shell=None,
              env_extra=None, cwd=None, tmpname=None):
    proj = tmp_path / name
    (proj / ".sdlc").mkdir(parents=True, exist_ok=True)
    if adopted:
        (proj / ".sdlc" / "config.json").write_text("{}")
    tmpdir = tmp_path / f"tmp-{tmpname or name}"   # one notice marker dir per simulated session
    tmpdir.mkdir(exist_ok=True)
    env = {"PATH": pathdir, "CLAUDE_PLUGIN_ROOT": str(HOOKS.parent), "TMPDIR": str(tmpdir)}
    env.update(env_extra or {})
    # timeout: a regression that hangs must FAIL (TimeoutExpired), never hang the suite (#1282 review)
    return _group_run(shell or ["bash", "-c", command], stdin, cwd=cwd or proj, env=env)


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


# ------------------------------------------------------------------ review fixes on #1322


def _decision_cmd():
    return next(c for c in _commands() if "decision_gate" in c)


def _noticed(r):
    return r.returncode == 0 and "systemMessage" in r.stdout


def test_relative_empty_and_odd_project_dirs_terminate(tmp_path):
    """The adoption walk-up used to spin forever on a relative CLAUDE_PROJECT_DIR."""
    cmd, bindir = _decision_cmd(), _bindir(tmp_path, "b", "broken")
    proj = tmp_path / "proj"
    adopted_values = ["proj", ".", "", "proj/", str(proj) + "///", "./proj/."]
    for i, value in enumerate(adopted_values + ["nope/deeper"]):
        # relative values resolve against the hook's cwd, so run from tmp_path for the `proj`-relative ones
        cwd = proj if value in (".", "") or value.startswith("/") else tmp_path
        r = _host_run(tmp_path, cmd, bindir, cwd=cwd, env_extra={"CLAUDE_PROJECT_DIR": value},
                      tmpname=f"rel{i}")
        assert r.returncode == 0, (value, r.stderr)
        if value in adopted_values:
            assert _noticed(r), value
        else:
            assert r.stdout == "", value                # not adopted: silent, and it ended


def test_adoption_is_found_at_an_ancestor_and_deep_paths_terminate(tmp_path):
    deep = tmp_path / "proj" / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    r = _host_run(tmp_path, _decision_cmd(), _bindir(tmp_path, "b", "broken"), cwd=deep,
                  env_extra={"CLAUDE_PROJECT_DIR": str(deep)})
    assert _noticed(r)                                  # .sdlc/config.json five levels up
    r = _host_run(tmp_path, _decision_cmd(), _bindir(tmp_path, "b2", "broken"), adopted=False,
                  name="bare", cwd=tmp_path, env_extra={"CLAUDE_PROJECT_DIR": "/"})
    assert r.returncode == 0 and r.stdout == ""         # the root itself terminates too


def test_gate_state_is_exempt_from_the_notice(tmp_path):
    cmd = 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/_py.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/gate_state.py" --adopted .'
    r = _host_run(tmp_path, cmd, _bindir(tmp_path, "b", "broken"))
    assert r.returncode == 0 and r.stdout == ""


def _markers(tmp_path, name):
    return tmp_path / f"tmp-{name}" / f"sigma-hooks-{os.getuid()}"


def test_marker_never_follows_a_symlink_and_never_suppresses_the_notice(tmp_path):
    cmd, bindir = _decision_cmd(), _bindir(tmp_path, "b", "broken")
    sid = '{"session_id": "abc123"}'
    # the per-user dir itself is a symlink to an attacker-chosen directory: not used, notice shown,
    # nothing created inside the target
    target = tmp_path / "target"
    target.mkdir()
    tmpdir = tmp_path / "tmp-sym"
    tmpdir.mkdir()
    (tmpdir / f"sigma-hooks-{os.getuid()}").symlink_to(target)
    r = _host_run(tmp_path, cmd, bindir, name="sym", stdin=sid)
    assert _noticed(r) and list(target.iterdir()) == []
    # a dangling symlink at the marker path and a symlink to an existing file
    for kind in ("dangling", "existing", "dirlink"):
        n = f"m{kind}"
        d = _markers(tmp_path, n)
        d.mkdir(parents=True, mode=0o700)
        link = d / "py-notice-sabc123-decision_gate.py"
        dest = tmp_path / f"{kind}-dest"
        if kind == "existing":
            dest.write_text("keep")
        if kind == "dirlink":
            dest.mkdir()                                # a symlink to a real directory must not count as a marker
        link.symlink_to(dest)
        r = _host_run(tmp_path, cmd, bindir, name=n, stdin=sid)
        assert _noticed(r), kind                        # repeat, never skip
        assert dest.exists() == (kind != "dangling")  # a dangling target was not created
        if kind == "existing":
            assert dest.read_text() == "keep"


def test_a_file_at_the_marker_path_does_not_suppress(tmp_path):
    d = _markers(tmp_path, "f")
    d.mkdir(parents=True, mode=0o700)
    (d / "py-notice-sabc123-decision_gate.py").write_text("stale")
    r = _host_run(tmp_path, _decision_cmd(), _bindir(tmp_path, "b", "broken"), name="f",
                  stdin='{"session_id": "abc123"}')
    assert _noticed(r)


def test_marker_dir_is_private_and_a_second_event_is_quiet(tmp_path):
    cmd, bindir = _decision_cmd(), _bindir(tmp_path, "b", "broken")
    assert _noticed(_host_run(tmp_path, cmd, bindir, stdin='{"session_id": "abc123"}'))
    assert stat.S_IMODE(_markers(tmp_path, "proj").stat().st_mode) == 0o700
    again = _host_run(tmp_path, cmd, bindir, stdin='{"session_id": "abc123"}')
    assert again.returncode == 0 and again.stdout == ""


def test_no_mkdir_means_the_notice_repeats_not_skips(tmp_path):
    bindir = _bindir(tmp_path, "b", "broken")
    os.unlink(os.path.join(bindir, "mkdir"))
    for _ in range(2):
        assert _noticed(_host_run(tmp_path, _decision_cmd(), bindir, stdin='{"session_id": "abc123"}'))


def test_session_id_keys_the_marker_even_through_a_fresh_non_exec_shell(tmp_path):
    """The host may put a shell between itself and the hook; `sh -c '...; true'` does not exec, so the
    hook's PPID differs on every event. With a session_id on stdin the notice is still once per session."""
    cmd, bindir = _decision_cmd(), _bindir(tmp_path, "b", "broken")
    shell = ["/bin/sh", "-c", cmd + "; true"]
    sid = '{"session_id": "sess-1"}'
    assert _noticed(_host_run(tmp_path, cmd, bindir, stdin=sid, shell=shell))
    assert _host_run(tmp_path, cmd, bindir, stdin=sid, shell=shell).stdout == ""
    other = _host_run(tmp_path, cmd, bindir, stdin='{"session_id": "sess-2"}', shell=shell)
    assert _noticed(other)                                   # a different session is told again


def test_without_a_session_id_a_fresh_shell_per_event_repeats_the_notice(tmp_path):
    """The honest fallback: no session_id -> keyed on PPID, which a non-exec wrapper changes per event."""
    cmd, bindir = _decision_cmd(), _bindir(tmp_path, "b", "broken")
    shell = ["/bin/sh", "-c", cmd + "; true"]
    assert _noticed(_host_run(tmp_path, cmd, bindir, shell=shell))
    assert _noticed(_host_run(tmp_path, cmd, bindir, shell=shell))


def test_an_unsafe_session_id_falls_back_rather_than_naming_a_path(tmp_path):
    cmd, bindir = _decision_cmd(), _bindir(tmp_path, "b", "broken")
    r = _host_run(tmp_path, cmd, bindir, stdin='{"session_id": "../../etc/x"}')
    assert _noticed(r)
    assert all(".." not in p.name for p in _markers(tmp_path, "proj").iterdir())


def test_a_slashed_session_id_cannot_place_a_marker_outside_the_marker_dir(tmp_path):
    d = _markers(tmp_path, "proj")
    (d / "py-notice-sfoo").mkdir(parents=True, mode=0o700)
    r = _host_run(tmp_path, _decision_cmd(), _bindir(tmp_path, "b", "broken"),
                  stdin='{"session_id": "foo/bar"}')
    assert _noticed(r)
    assert list((d / "py-notice-sfoo").iterdir()) == []      # the unsanitised key would have landed here


def test_a_huge_stdin_payload_is_read_in_bounded_time(tmp_path):
    """The session_id probe once read ALL of stdin with `read -d ''`, which is quadratic on bash 3.2 (300 KB
    = 33 s, 5 MB = minutes): a fail-open hook freezing a tool call. It now reads a 4 KB prefix."""
    import time
    big = '{"session_id": "big-1", "tool_response": "' + "x" * (5 * 1024 * 1024) + '"}'
    t = time.time()
    r = _host_run(tmp_path, _decision_cmd(), _bindir(tmp_path, "b", "broken"), stdin=big)
    assert _noticed(r) and time.time() - t < 10
    # and the session_id inside the prefix was still honoured
    assert (_markers(tmp_path, "proj") / "py-notice-sbig-1-decision_gate.py").is_dir()


def test_a_never_closing_stdin_does_not_wedge_the_hook(tmp_path):
    import signal
    import time
    bindir = _bindir(tmp_path, "b", "broken")
    proj = tmp_path / "proj"
    (proj / ".sdlc").mkdir(parents=True)
    (proj / ".sdlc" / "config.json").write_text("{}")
    (tmp_path / "t").mkdir()
    env = {"PATH": bindir, "CLAUDE_PLUGIN_ROOT": str(HOOKS.parent), "TMPDIR": str(tmp_path / "t")}
    p = subprocess.Popen(["bash", "-c", _decision_cmd()], cwd=proj, env=env, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, start_new_session=True)
    t = time.time()
    try:
        p.stdin.write('{"session_id": "open-1"')       # never closed, never finished
        p.stdin.flush()
        out = p.stdout.read()
        assert p.wait(timeout=10) == 0 and "systemMessage" in out and time.time() - t < 10
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

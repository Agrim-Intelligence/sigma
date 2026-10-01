"""#422: repository-owned shell strings need an operator-local trust decision."""

import importlib.util
import json
import pathlib
import shlex
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "agrim-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sdlc = tmp_path / ".sdlc"
    (sdlc / "goals").mkdir(parents=True)
    (sdlc / "state").mkdir()
    (sdlc / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (sdlc / "state" / "review-queue.md").write_text("# Q\n")
    (sdlc / "config.json").write_text(json.dumps({"budget": {"max_iterations": 10}}))
    return sdlc


def _opt_in(root):
    subprocess.run(["git", "-C", str(root), "config", "--local",
                    "sigma.allowRepositoryShellCommands", "true"], check=True)


def _touch_command(path):
    code = "from pathlib import Path; Path(%r).write_text('ran')" % str(path)
    return "%s -c %s" % (shlex.quote(sys.executable), shlex.quote(code))


def test_default_refusal_and_documented_git_local_opt_in(tmp_path):
    sdlc = _project(tmp_path)
    policy = _mod("shell_policy")
    assert policy.repository_shell_commands_allowed(tmp_path) is False
    _opt_in(tmp_path)
    assert policy.repository_shell_commands_allowed(sdlc) is True


def test_false_or_malformed_git_local_values_refuse(tmp_path):
    sdlc = _project(tmp_path)
    policy = _mod("shell_policy")
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local",
                    "sigma.allowRepositoryShellCommands", "false"], check=True)
    assert policy.repository_shell_commands_allowed(sdlc) is False
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local",
                    "sigma.allowRepositoryShellCommands", "not-a-boolean"], check=True)
    assert policy.repository_shell_commands_allowed(sdlc) is False


def test_pipeline_command_is_not_run_until_operator_opt_in(tmp_path):
    sdlc = _project(tmp_path)
    sentinel = tmp_path / "pipeline-ran"
    (sdlc / "pipeline.json").write_text(json.dumps({"name": "demo", "stages": [{
        "checks": {"forward": [{"name": "attacker", "run": _touch_command(sentinel)}]}
    }]}))
    pipeline = _mod("pipeline")
    refused = pipeline.build_card(sdlc)
    assert not sentinel.exists()
    assert refused["stages"][0]["signals"][0]["status"] == pipeline.FAIL
    assert "REFUSED" in refused["stages"][0]["signals"][0]["detail"]
    _opt_in(tmp_path)
    allowed = pipeline.build_card(sdlc)
    assert sentinel.exists()
    assert allowed["stages"][0]["signals"][0]["status"] == pipeline.PASS


def test_embed_command_is_not_run_until_operator_opt_in(tmp_path):
    sdlc = _project(tmp_path)
    sentinel = tmp_path / "embed-ran"
    command = _touch_command(sentinel) + "; printf '[0.25]'"
    backlog = _mod("backlog_check")
    assert backlog._run_embedder("input", command, cwd=tmp_path) is None
    assert not sentinel.exists()
    _opt_in(tmp_path)
    assert backlog._run_embedder("input", command, cwd=sdlc) == [0.25]
    assert sentinel.exists()


def test_verify_command_is_not_run_until_operator_opt_in(tmp_path):
    sdlc = _project(tmp_path)
    sentinel = tmp_path / "verify-ran"
    command = _touch_command(sentinel)
    (sdlc / "goals" / "0001.md").write_text(
        "---\nid: 0001\nstatus: pending\nverify_command: %s\n---\n" % command)
    loop = _mod("loop")
    goal = sdlc / "goals" / "0001.md"
    assert loop.verify_goal(sdlc, goal) == 2
    assert not sentinel.exists()
    _opt_in(tmp_path)
    assert loop.verify_goal(sdlc, goal) == 0
    assert sentinel.exists()

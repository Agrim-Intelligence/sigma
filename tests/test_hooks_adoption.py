"""#2737: the four deny/block hooks act only inside an adopted repository.

One definition of "adopted" (`hooks/gate_state.py:adopted_root`): the nearest ancestor of the start
directory holding `.sdlc/config.json`. Outside it, `issue_field_gate.py`, `decision_gate.py`,
`plan_gate.sh` and `completion_gate.sh` return the host's allow shape (no stdout, exit 0). Inside
it they keep denying (the control half — a fail-open direction of "not adopted means allow" means a
helper bug silently disarms every gate, so each hook's deny is pinned from an adopted temp repo).

Worktree hazard: pytest runs from `<main>/.sdlc/work/<n>`, whose ancestor walk reaches main's
`.sdlc/config.json`. Every subprocess therefore sets BOTH `cwd=tmp` and `CLAUDE_PROJECT_DIR=tmp`;
`tmp_path` lives under the system temp root, which has no `.sdlc` ancestor.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"
GATE_STATE = HOOKS / "gate_state.py"
ISSUE_GATE = HOOKS / "issue_field_gate.py"
DECISION_GATE = HOOKS / "decision_gate.py"
PLAN_GATE = HOOKS / "plan_gate.sh"
COMPLETION_GATE = HOOKS / "completion_gate.sh"

# The one deny-provoking input per hook — the real gesture, not a synthetic one.
ISSUE_CREATE = {"tool_name": "Bash", "tool_input": {"command": "gh issue create --title x"}}
TIMEOUT_EDIT = {"tool_name": "Edit", "tool_input": {"file_path": "src/a.py", "new_string": "timeout = 120"}}
INV = {"version": 1, "decisions": [{
    "id": "INV-001", "title": "Bounded timeouts", "class": "invariant", "status": "active",
    "statement": "No call may set a timeout above 30s.", "rationale": "one slow dep = outage",
    "protected_paths": ["src/**/*.py"],
    "protected_params": [{"name": "timeout", "op": "le", "value": 30}]}]}


def _gate_state():
    spec = importlib.util.spec_from_file_location("gate_state", GATE_STATE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _env(project):
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project)}


def _run(argv, project, payload):
    return subprocess.run(argv, cwd=str(project), input=json.dumps(payload),
                          capture_output=True, text=True, env=_env(project))


def _py(script, project, payload):
    return _run([sys.executable, str(script)], project, payload)


def _sh(script, project, payload):
    return _run(["bash", str(script)], project, payload)


def _adopt(project, cfg=None):
    (project / ".sdlc").mkdir(exist_ok=True)
    (project / ".sdlc" / "config.json").write_text(json.dumps(cfg if cfg is not None else {}))


def _git_init(project):
    subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)


def _assert_allow(out):
    assert out.returncode == 0, out.stderr
    assert out.stdout == "", out.stdout


def _hook_specific(out):
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip(), "adopted control printed nothing — the gate has been disarmed"
    return json.loads(out.stdout)["hookSpecificOutput"]


# ---------------------------------------------------------------- the helper
def test_adopted_root_git_toplevel_from_subdir(tmp_path):
    _git_init(tmp_path)
    _adopt(tmp_path)
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert _gate_state().adopted_root(tmp_path / "a" / "b") == tmp_path.resolve()


def test_adopted_root_ancestor_walk_for_non_git_subdir(tmp_path):
    _adopt(tmp_path)
    (tmp_path / "x" / "y").mkdir(parents=True)
    assert _gate_state().adopted_root(tmp_path / "x" / "y") == tmp_path.resolve()


def test_adopted_root_none_for_bare_tmp_dir(tmp_path):
    assert _gate_state().adopted_root(tmp_path) is None


def test_adopted_root_sdlc_dir_without_config_is_not_adopted(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    assert _gate_state().adopted_root(tmp_path) is None


def test_adopted_root_uses_claude_project_dir_when_no_start(tmp_path, monkeypatch):
    """The env var and the cwd DISAGREE, so the answer says which one was consulted. A fixture
    where both give the same answer would prove nothing about the precedence."""
    env_dir = tmp_path / "env"
    cwd_dir = tmp_path / "cwd"
    env_dir.mkdir()
    cwd_dir.mkdir()
    _adopt(cwd_dir)
    monkeypatch.chdir(cwd_dir)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(env_dir))
    assert _gate_state().adopted_root() is None                  # env (bare) wins over cwd (adopted)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(cwd_dir))
    monkeypatch.chdir(env_dir)
    assert _gate_state().adopted_root() == cwd_dir.resolve()     # and the reverse


def test_adopted_cli_prints_exactly_adopted(tmp_path):
    _adopt(tmp_path)
    out = subprocess.run([sys.executable, str(GATE_STATE), "--adopted", str(tmp_path)],
                         capture_output=True, text=True, env=_env(tmp_path), cwd=str(tmp_path))
    assert out.returncode == 0 and out.stdout == "adopted\n", out


def test_adopted_cli_prints_nothing_when_not_adopted(tmp_path):
    out = subprocess.run([sys.executable, str(GATE_STATE), "--adopted", str(tmp_path)],
                         capture_output=True, text=True, env=_env(tmp_path), cwd=str(tmp_path))
    assert out.returncode == 0 and out.stdout == "", out


# ---------------------------------------------------------------- not adopted -> allow (silent)
def test_issue_field_gate_allows_outside_adopted_repo(tmp_path):
    _assert_allow(_py(ISSUE_GATE, tmp_path, ISSUE_CREATE))


def test_decision_gate_allows_outside_adopted_repo(tmp_path):
    """A registry alone used to gate; adoption now requires config.json (accepted behaviour change)."""
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "decisions.json").write_text(json.dumps(INV))
    _assert_allow(_py(DECISION_GATE, tmp_path, TIMEOUT_EDIT))


def test_plan_gate_allows_outside_adopted_repo(tmp_path):
    """Regression pin: the existing `[ -f "$CFG" ]` check already made this green before #2737."""
    _assert_allow(_sh(PLAN_GATE, tmp_path,
                      {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "app.py")}}))


def test_completion_gate_allows_outside_adopted_repo(tmp_path):
    """Regression pin: the existing `[ -f "$CFG" ]` check already made this green before #2737."""
    _git_init(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    _assert_allow(_sh(COMPLETION_GATE, tmp_path, {}))


# ---------------------------------------------------------------- adopted -> still denies (control)
def test_issue_field_gate_still_denies_in_adopted_repo(tmp_path):
    _adopt(tmp_path)
    h = _hook_specific(_py(ISSUE_GATE, tmp_path, ISSUE_CREATE))
    assert h["permissionDecision"] == "deny"
    assert "handoff.py track" in h["permissionDecisionReason"]
    assert "create_tracked_issue" not in h["permissionDecisionReason"]


def test_decision_gate_still_denies_in_adopted_repo(tmp_path):
    _adopt(tmp_path)
    (tmp_path / ".sdlc" / "decisions.json").write_text(json.dumps(INV))
    assert _hook_specific(_py(DECISION_GATE, tmp_path, TIMEOUT_EDIT))["permissionDecision"] == "deny"


def test_plan_gate_still_denies_in_adopted_repo(tmp_path):
    _adopt(tmp_path, {"gates": {"hard_plan_gate": {"enabled": True}}})
    out = _sh(PLAN_GATE, tmp_path,
              {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "app.py")}})
    assert _hook_specific(out)["permissionDecision"] == "deny"


def test_completion_gate_still_blocks_in_adopted_repo(tmp_path):
    _git_init(tmp_path)
    _adopt(tmp_path, {"gates": {"stop_gate": {"enabled": True}}})
    (tmp_path / "a.py").write_text("x = 1\n")
    out = _sh(COMPLETION_GATE, tmp_path, {})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip(), "adopted control printed nothing — the gate has been disarmed"
    assert json.loads(out.stdout)["decision"] == "block"

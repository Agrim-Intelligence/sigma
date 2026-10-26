"""#2028: the compaction safety net's window rule, shared by supervise_daemon.py and autowatch.py.

The rule (plan D-A): with `handoff.after_context_tokens` (a) unset, set nothing -- the host's own
threshold is the net. With a set, the window is max(2a, PHASE_SUBAGENT_FLOOR), set only below
900,000. The floor exists because subagents and nested `claude -p` INHERIT the variable."""
import importlib.util
import json
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "compact_window", ROOT / "skills" / "sigma-loop" / "scripts" / "compact_window.py")
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)

KEY = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"


def _cfg(a):
    return {"handoff": {"after_context_tokens": a}}


@pytest.mark.parametrize("cfg, expected", [
    (None, None), ({}, None), ({"handoff": {}}, None), ({"handoff": None}, None),
    # absent unless a positive int -- the same rule as slice 2's `_explicit_context_tokens` (#2027)
    (_cfg(0), None), (_cfg(-5), None), (_cfg(True), None), (_cfg(False), None),
    (_cfg("300000"), None), (_cfg(300000.5), None), (_cfg(float("nan")), None),
    (_cfg(-300000.0), None), (_cfg([300000]), None),
    (_cfg(300000.0), 600_000),         # an integral float is that int (JSON tools emit them)
    (_cfg(100_000), 420_000),          # the subagent floor
    (_cfg(210_000), 420_000),
    (_cfg(300_000), 600_000),
    (_cfg(440_000), 880_000),
    (_cfg(450_000), None),             # 900,000 is not below 900,000: the host's ~967k is sooner
    (_cfg(600_000), None),
    (_cfg(1_000_000), None),
])
def test_window(cfg, expected):
    assert cw.window(cfg) == expected


def test_window_property_sweep():
    """Whatever a is, a set window is above the hand-off point, at or above the subagent floor,
    before the host's own compaction point, and inside the documented 100k..1M range."""
    for a in range(1, 1_200_001, 997):
        w = cw.window(_cfg(a))
        if w is None:
            continue
        assert w > a and w >= cw.PHASE_SUBAGENT_FLOOR and w < 900_000, (a, w)
        assert 100_000 <= w <= 1_000_000, (a, w)


def test_subagent_inheritance_pin():
    """The floor is the measured p99 subagent peak (411,359 tokens over 6,906 transcripts,
    `.sdlc/research/2028-subpeaks.py`) because subagents inherit the variable; both the module
    and the operator docs must say so, so nobody lowers the floor thinking it only binds the
    orchestrator."""
    assert cw.PHASE_SUBAGENT_FLOOR >= 411_359
    for text in ((ROOT / "skills" / "sigma-loop" / "scripts" / "compact_window.py").read_text(),
                 (ROOT / "docs" / "running-the-loop.md").read_text()):
        assert KEY in text and "subagent" in text.lower() and "inherit" in text.lower()


# --------------------------------------------------------------------------- operator_set / apply


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo dir, an empty CLAUDE_CONFIG_DIR and managed paths that do not exist."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv(KEY, raising=False)     # a developer's own window must not leak in
    monkeypatch.setattr(cw, "MANAGED_SETTINGS_PATHS",
                        (tmp_path / "managed-mac.json", tmp_path / "managed-linux.json"))
    (tmp_path / "repo").mkdir()
    return tmp_path / "repo"


def _apply(env, repo, cmd="claude -p /sigma-loop", a=300_000):
    return cw.apply(env, _cfg(a), cmd, repo)


def test_control_nothing_operator_set_sets_the_window(repo):
    env = {}
    value, why = _apply(env, repo)
    assert env[KEY] == "600000" and value == 600_000 and "after_context_tokens" in why


def test_absent_threshold_sets_nothing_and_says_why(repo):
    env = {}
    value, why = cw.apply(env, {}, "claude -p", repo)
    assert KEY not in env and value is None and "host" in why


@pytest.mark.parametrize("preset", ["250000", ""])
def test_operator_env_value_wins(repo, preset):
    env = {KEY: preset}
    value, why = _apply(env, repo)
    assert env == {KEY: preset} and value is None and "operator" in why


def test_autocompact_flag_in_command_wins(repo):
    env = {}
    value, _ = _apply(env, repo, cmd="claude --autocompact 300k -p /sigma-loop")
    assert KEY not in env and value is None


@pytest.mark.parametrize("where", ["user", "project", "local", "managed-mac", "managed-linux"])
def test_window_in_a_settings_file_wins(repo, where):
    path = {"user": pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json",
            "project": repo / ".claude" / "settings.json",
            "local": repo / ".claude" / "settings.local.json",
            "managed-mac": cw.MANAGED_SETTINGS_PATHS[0],
            "managed-linux": cw.MANAGED_SETTINGS_PATHS[1]}[where]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"autoCompactWindow": 300000}))
    env = {"CLAUDE_CONFIG_DIR": os.environ["CLAUDE_CONFIG_DIR"]}   # the child's env names the dir it reads
    value, why = _apply(env, repo)
    assert KEY not in env and value is None and str(path) in why


def test_settings_env_block_naming_the_variable_wins(repo):
    path = repo / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"env": {KEY: "300000"}}))
    env = {}
    assert _apply(env, repo)[0] is None and KEY not in env


def test_apply_never_touches_os_environ(repo):
    before = dict(os.environ)
    _apply({}, repo)
    assert dict(os.environ) == before


@pytest.mark.parametrize("bad", [0, True, "300000", -5, 300000.5])
def test_a_malformed_threshold_is_absent_and_says_so(repo, bad):
    env = {}
    value, why = cw.apply(env, _cfg(bad), "claude -p", repo)
    assert KEY not in env and value is None and "not a positive integer" in why


def test_an_integral_float_threshold_sets_the_window(repo):
    env = {}
    assert cw.apply(env, _cfg(300000.0), "claude -p", repo)[0] == 600_000 and env[KEY] == "600000"

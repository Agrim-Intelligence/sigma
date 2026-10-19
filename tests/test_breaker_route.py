"""#853 (slice 4A of #558): the breaker's own host table and resolver, `reviewer.resolve_breaker`.

The review table `_HOST_COMMANDS` is untouched; the breaker has a SEPARATE table with no `claude` key, so a
Claude Code host is refused until #537 adds one (the injected-table test shows that is data, not a rewrite).
Each control below names the break that turns its test red."""
import importlib.util
import json
import pathlib

S = pathlib.Path(__file__).resolve().parents[1] / "skills/sigma-loop/scripts"


def load(name):
    path = S / (name + ".py")
    assert path.exists(), "missing module %s" % path.name          # a missing module is a FAILURE, not an error
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rv = load("reviewer")
CLAUDE = {"CLAUDECODE": "1", "PATH": "/nonexistent"}


def sdlc(tmp_path, review=None):
    d = tmp_path / ".sdlc"
    d.mkdir(exist_ok=True)
    (d / "config.json").write_text(json.dumps({"review": review or {}}))
    return d


def fake_which(name):
    return "/fake/bin/" + name


def test_real_table_has_no_claude_key_and_review_table_is_unchanged():
    # break: add a "claude" entry to the real _BREAKER_COMMANDS -> this goes red
    assert "claude" not in rv._BREAKER_COMMANDS
    assert sorted(rv._HOST_COMMANDS) == ["codex", "cursor"]
    assert "--skip-git-repo-check" not in rv._HOST_COMMANDS["codex"][0]


def test_claude_code_host_is_refused_with_a_named_reason(tmp_path):
    got = rv.resolve_breaker(sdlc(tmp_path), env=dict(CLAUDE), which=fake_which)
    assert got["host"] == "claude" and got["mechanism"] == "refused" and got["mechanism"] != "process"
    assert "no breaker route" in got["reason"] and "claude" in got["reason"]
    assert "command" not in got


def test_an_injected_claude_entry_flips_the_same_code_to_process(tmp_path):
    table = {"claude": (["claude", "-p"], False)}
    got = rv.resolve_breaker(sdlc(tmp_path), env=dict(CLAUDE), which=fake_which, table=table)
    assert got["mechanism"] == "process" and got["host"] == "claude" and got["command"] == ["claude", "-p"]


def test_cursor_and_codex_resolve_to_process_with_a_fake_which(tmp_path):
    cursor = rv.resolve_breaker(sdlc(tmp_path, {"host": "cursor"}), env={}, which=fake_which)
    assert cursor["mechanism"] == "process" and cursor["command"][0] == "cursor-agent"
    assert cursor["command"] == rv._BREAKER_COMMANDS["cursor"][0]
    codex = rv.resolve_breaker(sdlc(tmp_path, {"host": "codex"}), env={}, which=fake_which)
    assert codex["mechanism"] == "process" and codex["host"] == "codex"
    assert codex["command"][1:] == rv._BREAKER_COMMANDS["codex"][0][1:]


def test_a_missing_binary_is_a_refusal_naming_it(tmp_path):
    got = rv.resolve_breaker(sdlc(tmp_path, {"host": "cursor"}), env={}, which=lambda name: None)
    assert got["mechanism"] == "refused" and "cursor-agent" in got["reason"] and "PATH" in got["reason"]


def test_review_command_is_ignored_so_a_command_host_refuses(tmp_path):
    # break: let mechanism "command" fall through to process -> this goes red
    got = rv.resolve_breaker(sdlc(tmp_path, {"host": "command", "command": "echo hi"}), env={}, which=fake_which)
    assert got["mechanism"] == "refused" and "not a breaker route" in got["reason"]


def test_codex_argv_carries_skip_git_repo_check():
    # break: drop the flag from the codex entry -> this goes red (measured necessary for an empty cwd)
    assert "--skip-git-repo-check" in rv._BREAKER_COMMANDS["codex"][0]
    assert rv._BREAKER_COMMANDS["codex"][0][-1] == "-"          # the brief arrives on stdin

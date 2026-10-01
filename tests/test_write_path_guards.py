"""#358 documented-gesture controls for the three newly guarded write paths."""
import importlib.util
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agrim-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


work = _load("work")
channel_notify = _load("channel_notify")
ledger = _load("ledger")


def _runner():
    calls = []

    def run(cwd, argv):
        calls.append(list(argv))
        if argv[:3] == ["git", "symbolic-ref", "--short"]:
            return "origin/main\n"
        return ""

    run.calls = calls
    return run


def _notify_config(url, **overrides):
    settings = {"enabled": True, "channel_webhook_url": url}
    settings.update(overrides)
    return {"ledger": {"enabled": True, "actor": "watcher", "autowatch": settings}}


def _candidate(sdlc_dir):
    ledger.entries_dir(sdlc_dir).mkdir(parents=True, exist_ok=True)
    ledger.entry_file(sdlc_dir, "other").write_text(json.dumps({
        "id": "other:1", "ts": ledger._stamp(), "actor": "other", "kind": "note",
        "goal": "42", "to": "watcher"}) + "\n", encoding="utf-8")


def _design_row():
    return json.dumps([{
        "number": 42, "url": "https://example.test/42", "isCrossRepository": False,
        "headRefName": "sdlc/9", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
        "files": [{"path": ".sdlc/design/9.md"}, {"path": ".sdlc/design/9-in-brief.md"}],
        "changedFiles": 2,
    }])


def test_documented_remote_branch_cleanup_refuses_main_before_delete():
    run = _runner()
    assert work._delete_remote_branch("/repo", "main", run) is False
    assert not any(call[:4] == ["gh", "api", "-X", "DELETE"] for call in run.calls)


def test_documented_goal_branch_cleanup_remains_allowed():
    run = _runner()
    assert work._delete_remote_branch("/repo", "sdlc/42", run) is True
    assert any(call[:4] == ["gh", "api", "-X", "DELETE"] for call in run.calls)


def test_documented_empty_prefix_refuses_even_sdlc_named_branch():
    run = _runner()
    assert work._delete_remote_branch("/repo", "sdlc/42", run,
                                      config={"work": {"branch_prefix": ""}}) is False
    assert not any(call[:4] == ["gh", "api", "-X", "DELETE"] for call in run.calls)


def test_configured_and_origin_default_branches_are_refused_before_delete():
    configured = _runner()
    assert work._delete_remote_branch("/repo", "goal/base", configured,
                                      config={"work": {"branch_prefix": "goal/", "base": "goal/base"}}) is False
    assert not any(call[:4] == ["gh", "api", "-X", "DELETE"] for call in configured.calls)

    calls = []
    def origin_goal(cwd, argv):
        calls.append(list(argv))
        return "origin/sdlc/42\n" if argv[:3] == ["git", "symbolic-ref", "--short"] else ""

    assert work._delete_remote_branch("/repo", "sdlc/42", origin_goal) is False
    assert not any(call[:4] == ["gh", "api", "-X", "DELETE"] for call in calls)


def test_delete_refusal_reaches_the_goal_action_log_when_enabled(tmp_path):
    sdlc = tmp_path / ".sdlc"; (sdlc / "state").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps({"action_log": {"enabled": True}}), encoding="utf-8")
    assert work._delete_remote_branch("/repo", "main", _runner(),
                                      sdlc_dir=sdlc, goal="42") is False
    rows = json.loads((sdlc / "state" / "log" / "42.jsonl").read_text().strip())
    assert rows["kind"] == "gate" and rows["gate"] == "merge" and rows["verdict"] == "refused"


def test_documented_remote_webhook_is_not_posted(tmp_path):
    config = _notify_config("https://example.com/hook")
    sdlc = tmp_path / ".sdlc"; (sdlc / "state").mkdir(parents=True)
    _candidate(sdlc)
    posts = []
    assert channel_notify.tick(sdlc, config=config,
                               run_post=lambda url, payload: posts.append((url, payload)) or True) == ""
    assert posts == []


def test_documented_loopback_webhook_is_posted(tmp_path):
    config = _notify_config("http://127.0.0.1:8790/")
    sdlc = tmp_path / ".sdlc"; (sdlc / "state").mkdir(parents=True)
    _candidate(sdlc)
    posts = []
    channel_notify.tick(sdlc, config=config,
                        run_post=lambda url, payload: posts.append((url, payload)) or True)
    assert [url for url, _ in posts] == ["http://127.0.0.1:8790/"]


def test_documented_remote_webhook_opt_in_is_exact_boolean(tmp_path):
    config = _notify_config("https://example.com/hook", allow_remote_webhook=True)
    sdlc = tmp_path / ".sdlc"; (sdlc / "state").mkdir(parents=True)
    _candidate(sdlc)
    posts = []
    channel_notify.tick(sdlc, config=config,
                        run_post=lambda url, payload: posts.append((url, payload)) or True)
    assert [url for url, _ in posts] == ["https://example.com/hook"]


def test_non_http_webhook_scheme_is_refused_without_a_send(tmp_path):
    config = _notify_config("file:///tmp/channel")
    sdlc = tmp_path / ".sdlc"; (sdlc / "state").mkdir(parents=True)
    _candidate(sdlc)
    posts = []
    assert channel_notify.tick(sdlc, config=config,
                               run_post=lambda url, payload: posts.append((url, payload)) or True) == ""
    assert posts == []


def test_remote_webhook_opt_in_does_not_bypass_the_http_scheme_rule(tmp_path):
    config = _notify_config("file:///tmp/channel", allow_remote_webhook=True)
    sdlc = tmp_path / ".sdlc"; (sdlc / "state").mkdir(parents=True)
    _candidate(sdlc)
    posts = []
    assert channel_notify.tick(sdlc, config=config,
                               run_post=lambda url, payload: posts.append((url, payload)) or True) == ""
    assert posts == []


def test_documented_design_merge_obeys_auto_merge_off():
    calls = []

    def run(cwd, argv):
        calls.append(list(argv))
        return _design_row() if argv[:3] == ["gh", "pr", "list"] else ""

    result = work.merge_design(".sdlc", {"work": {"enabled": True, "auto_merge": "off"}}, "9", run=run)
    assert "auto_merge" in result and "by hand" in result
    assert not any(call[:3] == ["gh", "pr", "merge"] for call in calls)

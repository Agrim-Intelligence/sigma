"""Supporting control for #524 (not a planned node): the doctor must not advise removing a marketplace
that another installed plugin still uses, and the printed marketplace-add source is the one the install
recorded. The old id is spelled from fragments, as in `test_plugin_rename.py`."""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD = "sig" + "ma"


def _doctor():
    spec = importlib.util.spec_from_file_location("doctor_rename_mkt", ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_shared_marketplace_is_kept_not_removed(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    (cfg / "plugins").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    for var in ("CODEX_SESSION_ID", "CODEX_THREAD_ID", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    (cfg / "plugins" / "known_marketplaces.json").write_text(json.dumps(
        {OLD: {"source": {"source": "github", "repo": "Agrim-Intelligence/" + OLD}}}))
    path = tmp_path / "installed_plugins.json"
    path.write_text(json.dumps({"version": 2, "plugins": {
        OLD + "@" + OLD: [{"scope": "user", "version": "1.0.0"}],
        "extra@" + OLD: [{"scope": "user", "version": "2.0.0"}]}}))
    base = tmp_path / "repo" / ".sdlc"
    base.mkdir(parents=True)
    (base / "config.json").write_text("{}")
    d = _doctor()
    row = next((c for c in d.check(str(base), run=lambda a: "", installed_plugins_path=str(path))
                if "pre-launch id" in c["name"]), None)
    assert row is not None
    assert "claude plugin marketplace remove" not in row["fix"], row["fix"]
    assert "keep marketplace " + OLD in row["fix"], row["fix"]
    # a shared marketplace is kept, so no add/install pair that would fail under the old marketplace name
    assert "claude plugin marketplace add" not in row["fix"] and "claude plugin install" not in row["fix"], row["fix"]
    assert "The marketplace is shared" in row["fix"]


def test_a_codex_entry_that_is_not_installed_raises_no_row(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_THREAD_ID", "t-1")
    for var in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    old_id = OLD + "@" + OLD
    listing = json.dumps({"installed": [{"pluginId": old_id, "installed": False, "enabled": False, "version": "1.0.0",
                                         "marketplaceSource": {"sourceType": "git",
                                                               "source": "https://github.com/Agrim-Intelligence/" + OLD + ".git"}}]})
    base = tmp_path / "repo" / ".sdlc"
    base.mkdir(parents=True)
    (base / "config.json").write_text("{}")
    d = _doctor()
    run = lambda a: listing if a[:3] == ["codex", "plugin", "list"] else ""
    assert not [c for c in d.check(str(base), run=run) if "pre-launch id" in c["name"]]


def _claude_row(monkeypatch, tmp_path, record):
    cfg = tmp_path / "cfg"
    (cfg / "plugins").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    for var in ("CODEX_SESSION_ID", "CODEX_THREAD_ID", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    if record is not None:
        (cfg / "plugins" / "known_marketplaces.json").write_text(json.dumps({OLD: {"source": record}}))
    path = tmp_path / "installed_plugins.json"
    path.write_text(json.dumps({"version": 2, "plugins": {OLD + "@" + OLD: [{"scope": "user", "version": "1.0.0"}]}}))
    base = tmp_path / "repo" / ".sdlc"
    base.mkdir(parents=True, exist_ok=True)
    (base / "config.json").write_text("{}")
    return next((c for c in _doctor().check(str(base), run=lambda a: "", installed_plugins_path=str(path))
                 if "pre-launch id" in c["name"]), None)


def test_a_checkout_or_git_url_source_is_printed_as_recorded(monkeypatch, tmp_path):
    row = _claude_row(monkeypatch, tmp_path / "a", {"source": "directory", "path": "/work/my-checkout"})
    assert row is not None and "claude plugin marketplace add /work/my-checkout" in row["fix"], row
    row = _claude_row(monkeypatch, tmp_path / "b", {"source": "git", "url": "https://example.invalid/x/y.git"})
    assert row is not None and "claude plugin marketplace add https://example.invalid/x/y.git" in row["fix"], row


def test_an_unreadable_source_prints_a_placeholder_never_the_public_slug(monkeypatch, tmp_path):
    row = _claude_row(monkeypatch, tmp_path, None)
    assert row is not None
    assert "marketplace add <the repository or checkout your install came from>" in row["fix"], row["fix"]
    assert "Agrim-Intelligence/sigmaloop" not in row["fix"], row["fix"]


def test_the_markdown_escaped_old_id_is_flagged_by_the_check(tmp_path):
    import subprocess
    import sys
    pkg = tmp_path / "r"
    pkg.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=pkg, check=True)
    (pkg / "a.md").write_text("install " + OLD + "\\@" + OLD + " now\n")
    subprocess.run(["git", "add", "-A"], cwd=pkg, check=True)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "rename_check.py")], cwd=pkg, text=True, capture_output=True)
    assert r.returncode == 1 and "a.md:1:" in r.stdout, (r.returncode, r.stdout)


def _records(monkeypatch, tmp_path, plugins, known=None):
    cfg = tmp_path / "cfg"
    (cfg / "plugins").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    for var in ("CODEX_SESSION_ID", "CODEX_THREAD_ID", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    if known is not None:
        (cfg / "plugins" / "known_marketplaces.json").write_text(json.dumps(known))
    path = tmp_path / "installed_plugins.json"
    path.write_text(json.dumps({"version": 2, "plugins": plugins}))
    base = tmp_path / "repo" / ".sdlc"
    base.mkdir(parents=True, exist_ok=True)
    (base / "config.json").write_text("{}")
    return next((c for c in _doctor().check(str(base), run=lambda a: "", installed_plugins_path=str(path))
                 if "pre-launch id" in c["name"]), None)


def test_with_the_new_id_already_installed_only_the_removal_is_printed(monkeypatch, tmp_path):
    one = [{"scope": "user", "version": "1.0.0"}]
    row = _records(monkeypatch, tmp_path, {OLD + "@" + OLD: one, "sigmaloop@sigmaloop": one},
                   {OLD: {"source": {"source": "github", "repo": "Agrim-Intelligence/" + OLD}}})
    assert row is not None
    assert "claude plugin uninstall " + OLD + "@" + OLD in row["fix"]
    assert "marketplace add" not in row["fix"] and "claude plugin install" not in row["fix"], row["fix"]
    assert "already installed here" in row["fix"]


def test_values_from_the_record_are_shell_quoted_and_managed_scope_is_not_a_flag(monkeypatch, tmp_path):
    entries = [{"scope": "project", "version": "1.0.0", "projectPath": "/work/a b; rm -rf ~"},
               {"scope": "managed", "version": "1.0.0"}]
    row = _records(monkeypatch, tmp_path, {OLD + "@" + OLD: entries},
                   {OLD: {"source": {"source": "directory", "path": "/work/my checkout"}}})
    assert row is not None
    assert "'/work/a b; rm -rf ~'" in row["fix"] and "'/work/my checkout'" in row["fix"], row["fix"]
    assert "--scope managed" not in row["fix"], row["fix"]


def test_a_fork_added_by_git_url_is_not_ours(monkeypatch, tmp_path):
    row = _records(monkeypatch, tmp_path, {OLD + "@" + OLD: [{"scope": "user", "version": "1.0.0"}]},
                   {OLD: {"source": {"source": "git", "url": "https://github.com/acme/fork.git"}}})
    assert row is None

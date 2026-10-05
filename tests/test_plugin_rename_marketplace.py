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
    assert "claude plugin marketplace add Agrim-Intelligence/" + OLD in row["fix"]


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

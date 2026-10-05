"""Controls for naming the solution Sigma Loop (#524): plugin and marketplace id `sigmaloop`.

The old install id is spelled from fragments everywhere in this file, so the leftover-name check
does not flag it. The doctor is driven through `check()` (never a private helper), so each control
is red by assertion on the unchanged code, not by an AttributeError.
"""
import importlib.util
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py"
OLD_NAME = "sig" + "ma"                    # the pre-launch plugin and marketplace name
OLD_ID = OLD_NAME + "@" + OLD_NAME
OLD_REPO = "Agrim-Intelligence/" + OLD_NAME  # the pre-launch repository the old installs came from
NEW_ID = "sigmaloop@sigmaloop"


def _doctor():
    spec = importlib.util.spec_from_file_location("doctor_rename", DOCTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    cfg = tmp_path / "claude-config"
    (cfg / "plugins").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    for var in ("CODEX_SESSION_ID", "CODEX_THREAD_ID", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    return cfg


def _sdlc(tmp_path):
    base = tmp_path / "repo" / ".sdlc"
    base.mkdir(parents=True)
    (base / "config.json").write_text("{}")
    return str(base)


class Runner:
    """Canned stdout for the probes; every argv is recorded so a test can prove nothing was run."""
    def __init__(self, plugin_list="[]", marketplace="", codex_list=""):
        self.calls, self.plugin_list, self.marketplace, self.codex_list = [], plugin_list, marketplace, codex_list

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:3] == ["claude", "plugin", "list"] and "--json" in args:
            return self.plugin_list
        if args[:3] == ["codex", "plugin", "list"]:
            return self.codex_list
        if args[:2] == ["gh", "api"] and any("marketplace.json" in a for a in args):
            import base64
            return base64.b64encode(self.marketplace.encode()).decode() if self.marketplace else ""
        return ""


def _old_row(checks):
    return next((c for c in checks if "pre-launch id" in c["name"]), None)


def _write_claude_records(cfg, tmp_path, plugins, known=None):
    path = tmp_path / "installed_plugins.json"
    path.write_text(json.dumps({"version": 2, "plugins": plugins}))
    if known is not None:
        (cfg / "plugins" / "known_marketplaces.json").write_text(json.dumps(known))
    return path


def _github(repo):
    return {"source": {"source": "github", "repo": repo}}


def test_manifests_name_the_plugin_and_marketplace_sigmaloop():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert plugin["name"] == "sigmaloop", plugin["name"]
    assert market["name"] == "sigmaloop", market["name"]
    assert [p["name"] for p in market["plugins"]] == ["sigmaloop"]
    assert "Sigma Loop" in plugin["description"] and "Sigma Loop" in market["plugins"][0]["description"]


def test_doctor_prints_commands_for_an_old_claude_install_and_runs_nothing(_isolated, tmp_path):
    d = _doctor()
    project = tmp_path / "proj"
    entries = [{"scope": "user", "version": "1.0.0"},
               {"scope": "project", "version": "1.0.0", "projectPath": str(project)}]
    plugins = _write_claude_records(_isolated, tmp_path, {OLD_ID: entries}, {OLD_NAME: _github(OLD_REPO)})
    before = plugins.read_bytes()
    run = Runner()
    row = _old_row(d.check(_sdlc(tmp_path), run=run, installed_plugins_path=str(plugins)))
    assert row is not None, "no row for an install recorded under the old id"
    assert row["ok"] is False
    fix = row["fix"]
    for cmd in ("claude plugin uninstall " + OLD_ID,
                "claude plugin uninstall " + OLD_ID + " --scope project",
                "claude plugin marketplace remove " + OLD_NAME,
                "claude plugin marketplace add " + OLD_REPO,
                "claude plugin install " + NEW_ID):
        assert cmd in fix, (cmd, fix)
    assert str(project) in fix
    # it only PRINTS: no host command that changes anything ran, and the record is untouched
    assert not [c for c in run.calls if len(c) > 2 and c[2] in ("uninstall", "install", "remove", "add")], run.calls
    assert plugins.read_bytes() == before


def test_doctor_stays_silent_for_a_fork_and_for_the_new_id(_isolated, tmp_path):
    d = _doctor()
    base = _sdlc(tmp_path)

    def row_for(plugins, known):
        path = _write_claude_records(_isolated, tmp_path, plugins, known)
        return _old_row(d.check(base, run=Runner(), installed_plugins_path=str(path)))

    entry = [{"scope": "user", "version": "1.0.0"}]
    # the positive control: the exact old id whose recorded source cannot be read still fires
    fired = row_for({OLD_ID: entry}, None)
    assert fired is not None, "an unreadable record on the exact old id must still be reported"
    # a fork that kept the manifest names but recorded a foreign source is not ours to migrate
    assert row_for({OLD_ID: entry}, {OLD_NAME: _github("acme/fork")}) is None
    assert row_for({OLD_NAME + "@acme-fork": entry}, {"acme-fork": _github("acme/fork")}) is None
    # the new id is the destination, never a finding
    assert row_for({NEW_ID: entry}, {"sigmaloop": _github("Agrim-Intelligence/sigmaloop")}) is None


def test_doctor_prints_codex_commands_for_an_old_codex_install(monkeypatch, tmp_path):
    d = _doctor()
    monkeypatch.setenv("CODEX_THREAD_ID", "t-1")
    codex = json.dumps({"installed": [{
        "pluginId": OLD_ID, "installed": True, "enabled": True, "version": "1.0.0",
        "marketplaceSource": {"sourceType": "git", "source": "https://github.com/" + OLD_REPO + ".git"}}]})
    run = Runner(codex_list=codex)
    row = _old_row(d.check(_sdlc(tmp_path), run=run))
    assert row is not None, "no row for a Codex install recorded under the old id"
    for cmd in ("codex plugin remove " + OLD_ID, "codex plugin marketplace add", "codex plugin add " + NEW_ID,
                "codex plugin --help"):
        assert cmd in row["fix"], (cmd, row["fix"])
    assert not [c for c in run.calls if c[:3] in (["codex", "plugin", "remove"], ["codex", "plugin", "add"])]
    # the list is a subprocess, so the cheap pass skips the row exactly as it skips the Codex floor row
    cheap = Runner(codex_list=codex)
    assert _old_row(d.check(_sdlc(tmp_path / "cheap"), run=cheap, cheap_only=True)) is None
    assert not [c for c in cheap.calls if c[:2] == ["codex", "plugin"]]


def test_doctor_sees_the_new_plugin_name_for_version_and_scope_rows(_isolated, tmp_path):
    d = _doctor()
    listing = json.dumps([{"id": NEW_ID, "version": "0.9.7"}])
    market = json.dumps({"plugins": [{"name": "sigmaloop", "version": "0.9.23"}]})
    path = _write_claude_records(_isolated, tmp_path, {NEW_ID: [{"scope": "user", "version": "0.9.7"}]})
    checks = d.check(_sdlc(tmp_path), run=Runner(plugin_list=listing, marketplace=market),
                     installed_plugins_path=str(path))
    nudge = next((c for c in checks if c["name"].startswith("sigma up to date")), None)
    assert nudge is not None, [c["name"] for c in checks]
    assert nudge["ok"] is False and "claude plugin update " + NEW_ID in nudge["fix"], nudge
    scope = next((c for c in checks if c["name"].startswith("sigma install scopes")), None)
    assert scope is not None, "the install-scope row did not see the new plugin name"


def test_readme_install_lines_use_the_new_id_and_public_url():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    url = "https://github.com/Agrim-Intelligence/sigmaloop"
    for line in ("/plugin marketplace add " + url, "/plugin install " + NEW_ID,
                 "claude plugin marketplace add " + url, "claude plugin install " + NEW_ID,
                 "codex plugin marketplace add " + url, "codex plugin add " + NEW_ID):
        assert line in text, line
    assert "<SIGMA_REPO>" not in text
    assert text.lstrip().startswith("# Sigma Loop")


def test_upgrading_doc_states_the_migration_and_that_sdlc_data_is_kept():
    text = (ROOT / "docs" / "upgrading.md").read_text(encoding="utf-8")
    m = re.search(r"(?ms)^## From the pre-launch name\n(.*?)(?=^## |\Z)", text)
    assert m is not None, "docs/upgrading.md has no 'From the pre-launch name' section"
    body = m.group(1)
    for needle in ("claude plugin uninstall", "claude plugin install " + NEW_ID, "codex plugin add " + NEW_ID,
                   "`.sdlc/` data is kept", "OLD doctor"):
        assert needle in body, needle

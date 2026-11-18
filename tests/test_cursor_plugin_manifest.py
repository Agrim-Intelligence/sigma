"""The Cursor plugin package: `.cursor-plugin/plugin.json`, its marketplace and its hooks file.

Cursor reads `.cursor-plugin/plugin.json` (a Cursor Plugin) in preference to the Claude Code
manifest it can also import. These tests pin the shape Cursor's published schemas require
(github.com/cursor/plugins, `schemas/plugin.schema.json` and `schemas/marketplace.schema.json`:
both set `additionalProperties: false`, so an unknown key is a rejected manifest, not a warning),
and pin the package to the Claude Code manifest so one release cannot ship two versions.

The hooks rule is deliberate. With no `hooks` field Cursor scans `hooks/hooks.json`, which is the
Claude Code hooks file (`UserPromptSubmit`, `${CLAUDE_PLUGIN_ROOT}`): Cursor would parse another
host's events and paths. The manifest points `hooks` at an empty Cursor hooks file instead, so a
Cursor install runs no hook until one is written for Cursor and run there (AGENTS.md: a hook may be
an accelerator, never load-bearing).
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAME = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

#: Top-level keys Cursor's plugin schema allows (its `properties`, verbatim).
PLUGIN_KEYS = {
    "name", "displayName", "description", "version", "minClientVersions", "author", "publisher",
    "homepage", "repository", "license", "logo", "keywords", "category", "tags", "commands",
    "agents", "skills", "rules", "hooks", "variables", "mcpServers",
}
#: Keys Cursor's marketplace schema allows at the top level and on each plugin entry.
MARKETPLACE_KEYS = {"name", "owner", "metadata", "plugins"}
ENTRY_KEYS = {"name", "source", "description", "minClientVersions"}


def _load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _rel(path_value):
    return (ROOT / path_value).resolve()


def test_plugin_manifest_uses_only_keys_cursor_accepts():
    m = _load(".cursor-plugin/plugin.json")
    assert set(m) <= PLUGIN_KEYS, sorted(set(m) - PLUGIN_KEYS)
    assert NAME.fullmatch(m["name"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["version"])


def test_plugin_matches_the_claude_code_manifest():
    """Same id, version, licence and description as the Claude Code plugin: one product, one
    release. Pinned to each other, not to a literal, so a release bump edits both or fails here."""
    cursor = _load(".cursor-plugin/plugin.json")
    claude = _load(".claude-plugin/plugin.json")
    for key in ("name", "version", "license", "description"):
        assert cursor[key] == claude[key], key


def test_plugin_links_point_at_the_public_repository():
    """`homepage` and `repository` name the repository the published plugin is installed from:
    `public_repo` in docs/launch/definition.json, the one slug the public-tree builder, the doctor's
    marketplace check and the README's install commands already share. It is created at the first
    public release, so it can 404 before then; this source repository is private and must not be
    what a public install links to. Read from the definition rather than restated, so a rename
    there fails here instead of leaving the Cursor pane on the old name."""
    public = _load("docs/launch/definition.json")["public_repo"]
    m = _load(".cursor-plugin/plugin.json")
    for key in ("homepage", "repository"):
        assert m[key] == "https://github.com/%s" % public, key


def test_skills_path_holds_every_shipped_skill():
    m = _load(".cursor-plugin/plugin.json")
    skills = _rel(m["skills"])
    assert skills == (ROOT / "skills").resolve()
    found = sorted(p.parent.name for p in skills.glob("*/SKILL.md"))
    assert "sigma-init" in found and "sigma-loop" in found
    assert len(found) == len([d for d in skills.iterdir() if d.is_dir()])


def test_hooks_point_at_an_empty_cursor_hooks_file_not_the_claude_one():
    m = _load(".cursor-plugin/plugin.json")
    assert isinstance(m.get("hooks"), str), "without a hooks field Cursor scans hooks/hooks.json"
    path = _rel(m["hooks"])
    assert path != (ROOT / "hooks" / "hooks.json").resolve()
    assert path.is_relative_to(ROOT)
    hooks = json.loads(path.read_text(encoding="utf-8"))
    assert hooks == {"version": 1, "hooks": {}}


def test_no_default_component_folder_brings_in_unintended_files():
    """Cursor discovers rules/, agents/, commands/ and mcp.json at the plugin root when the manifest
    names no path. Sigma's Cursor rules are written per repository by `/sigma-init --cursor`, not
    applied to every workspace, so none of those may exist at the root unnoticed."""
    m = _load(".cursor-plugin/plugin.json")
    for folder, key in (("rules", "rules"), ("agents", "agents"), ("commands", "commands")):
        assert key in m or not (ROOT / folder).exists(), folder
    assert "mcpServers" in m or not (ROOT / "mcp.json").exists()


def test_marketplace_lists_the_plugin_from_the_root():
    m = _load(".cursor-plugin/marketplace.json")
    assert set(m) <= MARKETPLACE_KEYS, sorted(set(m) - MARKETPLACE_KEYS)
    assert set(m["owner"]) <= {"name", "email"}
    [entry] = m["plugins"]
    assert set(entry) <= ENTRY_KEYS, sorted(set(entry) - ENTRY_KEYS)
    assert entry["name"] == _load(".cursor-plugin/plugin.json")["name"]
    assert entry["source"] == "./"
    assert _load(".claude-plugin/marketplace.json")["name"] == m["name"]

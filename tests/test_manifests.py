import json, pathlib, re, shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(rel):
    return json.loads((ROOT / rel).read_text())


def test_plugin_manifest_valid():
    m = _load(".claude-plugin/plugin.json")
    assert m["name"] == "sigma"
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["version"])   # semver shape, not a frozen value
    assert m["license"] == "MIT"
    assert m["description"]


def test_marketplace_lists_plugin_from_root():
    m = _load(".claude-plugin/marketplace.json")
    names = [p["name"] for p in m["plugins"]]
    assert "sigma" in names
    assert m["plugins"][0]["source"] == "./"


def test_the_two_manifests_agree_on_the_version():
    """A release bumps the version in BOTH files by hand, and nothing enforced that they match — so
    a bump that touched one would ship a plugin advertising one version and installing another, and
    `/sigma-doctor`'s update check (installed vs marketplace `latest`) would compare across the drift.
    Neither file is the obvious source of truth, so pin them to each other rather than to a literal:
    this must not need editing at every release, only when they disagree."""
    plugin = _load(".claude-plugin/plugin.json")["version"]
    entry = next(p for p in _load(".claude-plugin/marketplace.json")["plugins"]
                 if p["name"] == "sigma")
    assert entry["version"] == plugin, (
        "marketplace.json says %s, plugin.json says %s" % (entry["version"], plugin))


def _changelog_path(root):
    """The changelog the released version must have a section in. In the private tree that is
    `public-overrides/CHANGELOG.md`, the file the public snapshot ships as its root `CHANGELOG.md`;
    the snapshot itself carries no `public-overrides/`, so there the root `CHANGELOG.md` IS that
    override and is read directly. Override first, root as the fallback -- the same bytes either way."""
    root = pathlib.Path(root)
    override = root / "public-overrides" / "CHANGELOG.md"
    return override if override.is_file() else root / "CHANGELOG.md"


def _first_release_heading(text):
    """The first `## ` heading that is not `## Unreleased`, or None. The newest release is the FIRST
    release heading (newest first), so the pin reads that one and not "some heading, anywhere"."""
    for line in text.splitlines():
        if line.startswith("## ") and line.rstrip() != "## Unreleased":
            return line.rstrip()
    return None


def test_the_released_version_has_a_changelog_section():
    """A version bump with no CHANGELOG entry is how 1.0.10's own release note describes the failure
    that preceded it: 24 commits landed with the version untouched and Unreleased empty, so none of
    the work was recorded anywhere a user would look. Pin the other half of that.

    Restored to the core (it had moved to a private-only test when the public tree was split, and
    so stopped guarding the plugin that actually ships). It reads the override first because the
    snapshot's root `CHANGELOG.md` IS the override; and it reads the FIRST release heading, not any
    heading anywhere, because the private root changelog holds an older `## 1.0.0` release from a
    previous major -- an anywhere-match would pass there with no new section written at all."""
    version = _load(".claude-plugin/plugin.json")["version"]
    path = _changelog_path(ROOT)
    text = path.read_text(encoding="utf-8")
    assert text.strip(), "%s is empty: the release pin would be vacuous" % path
    assert text.startswith("# Changelog"), "%s does not start with '# Changelog'" % path
    heading = _first_release_heading(text)
    assert heading is not None, "%s has no release heading at all" % path
    assert re.match(r"## %s\b" % re.escape(version), heading), (
        "%s: the newest release heading is %r, but plugin.json says %s -- write the '## %s' section"
        % (path.name, heading, version, version))


def test_the_changelog_rule_reads_the_override_first_else_the_root(tmp_path):
    """Both shapes the pin runs in: the private tree (override present) and the snapshot (no
    `public-overrides/`, root changelog is the override). The two planted files differ so a swapped
    branch fails, and the parser skips `## Unreleased` on its way to the first release heading."""
    (tmp_path / "public-overrides").mkdir()
    (tmp_path / "public-overrides" / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n## 2.0.0 — public\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 9.9.9 — root\n", encoding="utf-8")
    picked = _changelog_path(tmp_path)
    assert picked == tmp_path / "public-overrides" / "CHANGELOG.md"
    assert _first_release_heading(picked.read_text()) == "## 2.0.0 — public"
    shutil.rmtree(tmp_path / "public-overrides")
    picked = _changelog_path(tmp_path)
    assert picked == tmp_path / "CHANGELOG.md"
    assert _first_release_heading(picked.read_text()) == "## 9.9.9 — root"
    assert _first_release_heading("# Changelog\n\n## Unreleased\n") is None

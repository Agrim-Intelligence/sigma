"""Known launch-readiness documentation claims stay evidence-backed (#340)."""
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_does_not_claim_unenforced_coverage_floor():
    readme = (ROOT / "README.md").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "85% coverage floor" not in readme or "--cov-fail-under" in workflow


def test_readme_hook_count_matches_registry():
    readme = (ROOT / "README.md").read_text()
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
    count = sum(len(group["hooks"]) for groups in hooks.values() for group in groups)
    assert f"registers {count} hook commands" in readme


def test_readme_has_no_predecessor_version_claim():
    assert "1.0.9" not in (ROOT / "README.md").read_text()


def test_branching_model_names_human_merge_exception():
    text = (ROOT / "docs/branching-model.md").read_text()
    assert "never merges a feature branch anywhere" not in text or "verify_merge.py" in text


def test_channel_webhook_documents_its_enforced_remote_opt_in():
    text = (ROOT / "skills/agrim-loop/scripts/channel_notify.py").read_text()
    assert "127.0.0.1-only" not in text
    assert "allow_remote_webhook" in text


def test_changelog_versions_are_dated():
    headings = re.findall(r"^## \[?([0-9]+\.[0-9]+\.[0-9]+)\]?\s*(.*)$", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert headings and all(re.search(r"\b\d{4}-\d{2}-\d{2}\b", suffix) for _version, suffix in headings)


def test_readme_ci_python_claim_lists_every_ci_matrix_version():
    """#434: the README's CI line named only 3.10 + 3.12 while ci.yml runs four Linux versions."""
    readme = (ROOT / "README.md").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    versions = set(re.findall(r'python:\s*"(3\.\d+)"', workflow))
    assert versions
    start = readme.index("**CI** (GitHub Actions)")
    claim = readme[start:start + 600]
    for version in versions:
        assert f"Python {version}" in claim or version in claim, version
    assert "3.10 + 3.12" not in readme
    assert "Linux, Python 3.10 and 3.12" not in (ROOT / "docs/onboarding-control.md").read_text()


def test_blocker_docs_say_merged_pull_request_not_closed():
    """#434: a PR closed WITHOUT merging does not resolve a blocker (blocker_scan.closed_state)."""
    import sys
    sys.path.insert(0, str(ROOT / "skills/agrim-loop/scripts"))
    import blocker_scan
    assert blocker_scan.closed_state("CLOSED", "") is False
    assert blocker_scan.closed_state("MERGED", "") is True
    assert "closed issue or pull request" not in (ROOT / "README.md").read_text()


def test_readme_managed_settings_says_it_is_advisory():
    text = (ROOT / "README.md").read_text()
    section = text[text.index("## Managed settings"):]
    section = section[:section.index("\n## ", 5)]
    assert "advisory" in section


def test_leak_scan_docs_carry_no_stale_measurements_or_symlink_claim():
    """#434: 614 files / 4.1-4.3 s was stale (798 files); `secret-file` does not flag symlinks."""
    doc = (ROOT / "tools/leak_scan.py").read_text()
    assert "614 tracked files" not in doc
    assert "614 tracked files" not in (ROOT / "CHANGELOG.md").read_text()
    assert "whatever it holds" not in doc or "symlink" in doc[doc.index("secret-file:"):][:700]

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



def _ci_matrix():
    """{os family: set of python versions} parsed from ci.yml's `include:` cells."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    cells = re.findall(r'-\s*os:\s*(\w+)-latest\s*\n\s*python:\s*"(3\.\d+)"', workflow)
    out = {}
    for os_name, version in cells:
        out.setdefault(os_name, set()).add(version)
    return out


def _versions(text):
    return set(re.findall(r"3\.\d+", text))


def test_ci_python_claims_equal_the_ci_matrix_exactly():
    """#434: the README's CI line said 3.10 + 3.12 while ci.yml runs four Linux versions + macOS 3.12."""
    matrix = _ci_matrix()
    assert matrix.get("ubuntu") and matrix.get("macos")
    pattern = re.compile(r"on Linux with Python ([\d., and]+?),? and on macOS with Python (3\.\d+)")
    readme = " ".join((ROOT / "README.md").read_text().split())
    start = readme.index("**CI** (GitHub Actions)")
    sites = [readme[:readme.index("Windows verification")], readme[start:start + 400]]
    for site in sites:
        m = pattern.search(site)
        assert m, "CI sentence must state both the Linux and the macOS Python versions"
        assert _versions(m.group(1)) == matrix["ubuntu"]
        assert _versions(m.group(2)) == matrix["macos"]
    doc = " ".join((ROOT / "docs/onboarding-control.md").read_text().split())
    m = re.search(r"\(`tests/test_onboarding_control.py`, Linux, Python ([\d., and]+)\)", doc)
    assert m and _versions(m.group(1)) == matrix["ubuntu"]


def test_blocker_docs_say_merged_pull_request_not_closed():
    """#434: a PR closed WITHOUT merging does not resolve a blocker (blocker_scan.closed_state)."""
    import sys
    sys.path.insert(0, str(ROOT / "skills/agrim-loop/scripts"))
    import blocker_scan
    assert blocker_scan.closed_state("CLOSED", "") is False
    assert blocker_scan.closed_state("MERGED", "") is True
    assert blocker_scan.closed_state("CLOSED", "COMPLETED") is True
    readme = " ".join((ROOT / "README.md").read_text().split())
    sweep = readme[readme.index("`discovery.auto_unpark.mode` is"):][:1800]
    assert re.search(r"points at a closed issue or a merged pull request \(a pull request closed "
                     r"without merging does not resolve it\), it drops `sdlc:blocked`", sweep)
    allowed = "a pull request closed without merging does not resolve it"
    stripped = sweep.replace(allowed, "")
    assert not re.search(r"closed (?:issue or )?(?:pull request|PR)|closed or merged", stripped, re.I)


def test_readme_managed_settings_says_it_is_advisory():
    text = " ".join((ROOT / "README.md").read_text().split())
    section = text[text.index("## Managed settings"):]
    section = section[:section.index(" ## ", 5)]
    assert ("The managed-settings file is advisory against anyone with write access to the checkout: "
            "they can edit or delete it, and deleting it falls back to the local config.") in section
    assert not re.search(r"not advisory|tamper|cannot be (?:edited|bypassed|deleted)", section, re.I)

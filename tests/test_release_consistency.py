"""Release-runbook controls for #359.

The release document is a safety boundary: an undated change log section or a
fallback update lookup aimed at a different public repository would make an
owner follow a plausible but wrong release procedure.
"""

import importlib.util
import json
import os
import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parent.parent
RELEASE = ROOT / "docs" / "release.md"
EVIDENCE = ROOT / "docs" / "launch" / "evidence" / "pin-rollback-2026-10-02.md"
DATE_HEADING = re.compile(r"^## (?!Unreleased$).+ — \d{4}-\d{2}-\d{2}(?:$| — )")
#: The ONE undated form the shipped changelog may carry (owner decision 2026-10-06: no release date is
#: invented; the owner sets it at release). Only as the newest release heading. `tools/release_notes.py`
#: refuses it, so the documented extraction step cannot publish it.
PLACEHOLDER = re.compile(r"^## \d+\.\d+\.\d+ — DATE-PENDING — the first public release$")


def heading_problems(headings):
    """Headings that are neither ISO-dated nor the single DATE-PENDING placeholder on the newest one."""
    return [line for i, line in enumerate(headings)
            if not DATE_HEADING.match(line) and not (i == 0 and PLACEHOLDER.match(line))]


def _doctor():
    path = ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py"
    spec = importlib.util.spec_from_file_location("doctor_359", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_runbook_and_pin_rollback_evidence_are_shipped():
    """The documented owner gesture and its measured evidence travel together."""
    text = RELEASE.read_text(encoding="utf-8")
    assert "## Versioning" in text
    assert "## Release checklist" in text
    assert "## Pin and rollback" in text
    assert "## Incident response" in text
    assert "stop every loop and watcher" in text.lower()
    evidence = pathlib.Path(os.environ.get("SIGMA_RELEASE_EVIDENCE", EVIDENCE)).read_text(encoding="utf-8")
    assert re.search(r"`HOME`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and `XDG_CONFIG_HOME`", evidence)
    hashes = re.findall(r"`[0-9a-f]{16}`", evidence)
    assert len(hashes) >= 2 and hashes[0] == hashes[1], hashes
    assert re.search(r"Measured commit: `[0-9a-f]{40}`", evidence)
    for gesture in (
        "claude plugin marketplace add Agrim-Intelligence/sigma@v1.0.0 --scope user",
        "claude plugin marketplace add Agrim-Intelligence/sigma#v1.0.0 --scope user",
        "git clone --branch v1.0.0 --depth 1 https://github.com/Agrim-Intelligence/sigma.git",
    ):
        assert gesture in evidence
    assert "SSH authentication failed" in evidence
    assert "Remote branch v1.0.0 not found" in evidence
    assert re.search(r"not\s+supported|works", evidence, re.I)


def test_every_published_changelog_release_heading_has_an_iso_date():
    """Copy the release gesture: a heading without a date must stop the check."""
    override = os.environ.get("SIGMA_RELEASE_CHANGELOG")
    changelog = pathlib.Path(override) if override else ROOT / "public-overrides" / "CHANGELOG.md"
    if not changelog.is_file():
        changelog = ROOT / "CHANGELOG.md"
    headings = [line for line in changelog.read_text(encoding="utf-8").splitlines()
                if line.startswith("## ") and line != "## Unreleased"]
    assert headings, "the shipped changelog has no release heading"
    assert not heading_problems(headings), heading_problems(headings)


def test_placeholder_is_accepted_only_as_the_newest_exact_heading():
    """Control: the placeholder is not a general escape from the dated-heading rule."""
    ok = "## 1.0.0 — DATE-PENDING — the first public release"
    older = "## 0.9.0 — 2026-01-02"
    assert heading_problems([ok, older]) == []
    assert heading_problems([older, ok]) == [ok]
    assert heading_problems([ok, ok]) == [ok]
    assert heading_problems(["## 1.0.0 — DATE-PENDING"]) == ["## 1.0.0 — DATE-PENDING"]
    assert heading_problems(["## 1.0.0 — TBD — the first public release"]) != []


def test_public_tree_names_the_public_repo_not_the_working_repo():
    """Public-facing references to the PUBLIC repository carry its slug (Agrim-Intelligence/sigmaloop)."""
    old = "Agrim-Intelligence/" + "sigma"
    for rel in ("README.md", ".github/ISSUE_TEMPLATE/config.yml", "contract/golden/config.json", "docs/board.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(re.escape(old) + r"(?![\w-])", text), rel + " still names the working repository slug"
    assert "Agrim-Intelligence/sigmaloop/security/advisories/new" in (
        ROOT / ".github/ISSUE_TEMPLATE/config.yml").read_text(encoding="utf-8")


def test_release_docs_describe_the_new_repository_model():
    release = " ".join((ROOT / "docs" / "release.md").read_text(encoding="utf-8").split())
    assert "is not present in this snapshot" not in release and "currently parked" not in release
    assert "tools/readiness/decide.py" in release
    assert "Pre-rename" not in release and "is renamed" not in release
    assert "NOT renamed" in release and "Agrim-Intelligence/sigmaloop" in release
    assert "release_notes.py" in release and "DATE-PENDING" in release
    runbook = " ".join((ROOT / "docs" / "publish-runbook.md").read_text(encoding="utf-8").split())
    assert "visibility flip" not in runbook


def test_signed_launch_definition_pins_doctor_fallback_to_public_repo():
    """The fallback is constrained only once the owner has signed the definition."""
    definition = json.loads((ROOT / "docs" / "launch" / "definition.json").read_text())
    if definition["status"] == "signed":
        assert _doctor()._MARKETPLACE_REPO == definition["public_repo"]

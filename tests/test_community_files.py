"""Public-project community files remain complete and safe (#341)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMMUNITY_FILES = ("SECURITY.md", "CONTRIBUTING.md", "SUPPORT.md", "CODE_OF_CONDUCT.md",
                   ".github/pull_request_template.md")
TEMPLATES = ("bug_report.yml", "feature_request.yml", "config.yml")


def test_community_files_exist():
    assert all((ROOT / path).is_file() for path in COMMUNITY_FILES)


def test_issue_templates_parse_and_bug_report_collects_environment():
    yaml = pytest.importorskip("yaml")
    parsed = {name: yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / name).read_text()) for name in TEMPLATES}
    assert all(isinstance(value, dict) for value in parsed.values())
    fields = {item.get("id") for item in parsed["bug_report.yml"]["body"]}
    assert {"sigma-version", "host", "operating-system", "python-version", "discovery-source",
            "steps", "expected", "actual", "state-log"} <= fields


def test_security_owner_placeholder_cannot_ship_after_launch_definition_is_signed():
    definition = ROOT / "docs" / "launch" / "definition.json"
    signed = definition.is_file() and json.loads(definition.read_text()).get("status") == "signed"
    assert not signed or "<OWNER: security contact e-mail>" not in (ROOT / "SECURITY.md").read_text()

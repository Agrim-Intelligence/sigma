"""Structural controls for the published threat model.

The documented invocation is ``python -m pytest tests/test_threat_model.py -q``.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "docs" / "threat-model.md"
REQUIRED_BOUNDARIES = tuple(f"T{number}" for number in range(1, 8))
CITATION = re.compile(r"`([^`:\n]+):(\d+)`")


def _table_rows(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("| ") and "---" not in line]


def test_threat_model_covers_all_prescribed_boundaries() -> None:
    text = MODEL.read_text(encoding="utf-8")
    assert "| boundary | crossing and entry points |" in text
    assert "| id | boundary | threat (STRIDE letter) |" in text
    for boundary in REQUIRED_BOUNDARIES:
        assert f"| {boundary} |" in text


def test_every_file_line_citation_resolves_in_this_checkout() -> None:
    text = MODEL.read_text(encoding="utf-8")
    citations = CITATION.findall(text)
    assert citations, "the threat model must make its source locations inspectable"
    for relative, raw_line in citations:
        path = ROOT / relative
        assert path.is_file(), relative
        assert int(raw_line) <= len(path.read_text(encoding="utf-8").splitlines()), (
            f"{relative}:{raw_line} is beyond the end of the file"
        )


def test_every_unmitigated_threat_has_a_follow_up_issue() -> None:
    for row in _table_rows(MODEL.read_text(encoding="utf-8")):
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) != 7 or cells[4] != "none":
            continue
        assert re.fullmatch(r"#\d+", cells[6]), row

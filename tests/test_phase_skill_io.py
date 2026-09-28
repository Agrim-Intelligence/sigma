"""Phase guidance keeps exploratory I/O out of a long model context."""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PHASE_FILES = [
    *(ROOT / "skills" / name / "SKILL.md" for name in (
        "agrim-brainstorm", "agrim-research", "agrim-plan", "agrim-plan-review",
        "agrim-implement", "agrim-review", "agrim-verify", "agrim-retro",
    )),
    ROOT / "skills/agrim-loop/references/running.md",
    ROOT / "skills/agrim-loop/references/landing.md",
]


@pytest.mark.parametrize("path", PHASE_FILES, ids=lambda p: p.parent.name)
def test_phase_instructions_locate_then_read_narrowly_and_bound_output(path):
    text = path.read_text(encoding="utf-8")
    assert "rg --files" in text and "rg -n" in text
    assert "relevant lines" in text
    assert "scratch file" in text

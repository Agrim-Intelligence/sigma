"""Phase guidance keeps exploratory I/O out of a long model context."""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PHASE_FILES = [
    *(ROOT / "skills" / name / "SKILL.md" for name in (
        "sigma-brainstorm", "sigma-research", "sigma-plan", "sigma-plan-review",
        "sigma-implement", "sigma-review", "sigma-verify", "sigma-retro",
    )),
    ROOT / "skills/sigma-loop/references/running.md",
    ROOT / "skills/sigma-loop/references/landing.md",
]


@pytest.mark.parametrize("path", PHASE_FILES, ids=lambda p: p.parent.name)
def test_phase_instructions_locate_then_read_narrowly_and_bound_output(path):
    text = path.read_text(encoding="utf-8")
    assert "rg --files" in text and "rg -n" in text
    assert "relevant lines" in text
    assert "scratch file" in text

"""Pure move from tests/test_orchestrate_docs.py at 4042e97 (#2224 amendment 2): the wholly assertion-red nodes; test bodies byte-identical.

The sigma-orchestrate skill, its references and docs/orchestrate.md stay consistent (#2145).
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "sigma-orchestrate"

DECISION_OPEN = "Standing decision by the person who started this batch"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_the_launch_prompt_carries_no_follow_up_decision_and_launch_points_at_follow_ups():
    follow = _text(SKILL_DIR / "references" / "follow-ups.md")
    assert DECISION_OPEN not in follow
    assert "follow-ups.md" in _text(SKILL_DIR / "references" / "launch.md")


def test_follow_ups_names_the_add_slice_gesture_and_both_reasons():
    follow = _text(SKILL_DIR / "references" / "follow-ups.md")
    assert "work.py" in follow and "add-slice" in follow
    assert "different-feature" in follow and "slice-cap" in follow

"""Cheap pins for the cold-read rewordings that are backed by code (docs/launch/evidence/
review-s5-cold-read-a0d4ac7ac9cc.md). Each asserts the skill text carries the words the code
accepts, so prose cannot drift back to a gesture the code refuses. They are text greps, not
behaviour: a skill that says the right words can still be ignored by an agent."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
sys.path.insert(0, str(SKILLS / "sigma-loop" / "scripts"))


def _skill(name):
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def test_research_skip_names_the_waiver_the_phase_gate_requires():
    import phase_gate
    assert "research" in phase_gate.WAIVABLE
    text = _skill("sigma-research")
    # the exact verb, the research phase, the mandatory --reason, and the consequence
    assert "loop.py waive-phases" in text and "research --reason" in text
    assert "`record done` is refused without it" in text


def test_review_verdict_words_are_the_ones_record_review_accepts():
    import phase_gate
    text = _skill("sigma-review")
    for word in phase_gate.REVIEW_VERDICTS:           # APPROVE, SEND-BACK, BLOCK
        assert word in text, word
    # plan-review's word is not a review verdict and record-review would refuse it
    assert "FIX-FIRST" not in text


def test_implement_requires_an_approving_plan_review_before_starting():
    text = _skill("sigma-implement")
    assert "must already carry an approving `sigma-plan-review` verdict" in text
    assert "it should already have passed" not in text


def test_verify_prefers_the_projects_verify_command_over_a_weaker_check():
    assert "**Verify command** if set" in _skill("sigma-verify")


def test_plan_review_fix_first_blocks_implement():
    assert "Implement waits for a fresh approving verdict" in _skill("sigma-plan-review")

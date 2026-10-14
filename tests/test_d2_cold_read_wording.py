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


# --- independent Codex read, docs/launch/evidence/review-s5-codex-read-45626aa65b73.md ---

def test_brainstorm_hands_off_to_research_not_straight_to_plan():          # F01
    text = _skill("sigma-brainstorm")
    assert "The terminal step is **`sigma-research`**" in text
    assert "recorded\nwaiver" in text or "recorded waiver" in text.replace("\n", " ")
    assert "The terminal step is **`sigma-plan`**" not in text


def test_verify_names_the_source_of_done_when():                           # F05
    assert "the P1 acceptance record; if the issue changed since, the recorded criteria" in _skill("sigma-verify")
    assert "The grade never substitutes for verified `done_when`" in _skill("sigma-loop")


def test_implement_mutation_control_separates_clauses_and_orders():        # F06
    text = " ".join(_skill("sigma-implement").split())
    assert "break each clause on its own" in text
    assert "weaken the order to plain membership" in text


def test_loop_model_fallback_pipes_issue_text_via_stdin():                 # F07
    text = " ".join((SKILLS / "sigma-loop" / "references" / "running.md").read_text(encoding="utf-8").split())
    assert 'pipe that text to `predict.py resolve - .sdlc "$goal"`' in text
    assert "pass that text as the argument" not in text


def test_only_owners_waive_a_review_finding():
    """Owner ruling 2026-10-14 (D2 RV1): a reviewer never waives a finding."""
    text = _skill("sigma-review")
    flat = " ".join(text.split())
    assert "can be waived with a stated reason" not in flat
    assert "Rules are lessons, not laws" not in flat
    assert "Only the owners named in the repository's CODEOWNERS may waive a finding, of any class" in flat
    assert "you report it and never waive it" in flat
    assert "recorded with their stated reason" in flat
    assert "Unattended, a finding that would need one parks for a human" in flat


# --- second independent Codex read, docs/launch/evidence/review-s5-codex-read-round2-f47c0a57f689.md ---

def _ref(name):
    return " ".join((SKILLS / "sigma-loop" / "references" / name).read_text(encoding="utf-8").split())


def test_retro_required_unless_the_recorded_waiver():                       # F04
    import phase_gate
    assert "retro" in phase_gate.WAIVABLE
    loop = " ".join(_skill("sigma-loop").split())
    assert "the phase is required unless the owner's recorded trivial-goal `waive-phases` call waived it" in loop
    assert "the phase is required)" not in loop


def test_no_pr_done_is_for_a_goal_that_changed_nothing():                   # F05 (code: work.done_refusal)
    landing = _ref("landing.md")
    assert "a docs-only / no-diff goal" not in landing
    assert "only if `work.enabled` is off or it changed nothing" in landing
    assert "(docs-only too)" in landing and "is REFUSED" in landing


def test_goal_concurrency_needs_work_enabled():                             # F06 (code: loop.next_batch)
    assert "and `work.enabled` is on (else `next-batch` clamps to one)" in _ref("picking.md")
    assert "(goals then run one at a time)" in _ref("running.md")


def test_start_run_is_the_users_call():                                     # F07
    picking = _ref("picking.md")
    assert "only when the user says so this session, never to continue past a stop" in picking
    assert "for a deliberate fresh push" not in picking


def test_pr_repair_that_changes_behaviour_reruns_retro():                   # F08
    assert "re-run `sigma-retro` on the final diff and pass that grade" in _ref("landing.md")
    assert "again on the final diff if a PR repair" in _skill("sigma-retro")


def test_record_plan_review_gestures_carry_agent_id():                      # F09
    for text in (_skill("sigma-plan-review"), _ref("running.md")):
        text = " ".join(text.split())
        assert "[--agent-id <id>]" in text
        assert "required on the `subagent` route only" in text


def test_followup_is_never_a_project_rule_violation():                      # F10
    review = " ".join(_skill("sigma-review").split())
    assert "except for a project-rule violation: that blocks unless the owners waive it" in review
    assert "or a project-rule violation (quote the rule)" in review

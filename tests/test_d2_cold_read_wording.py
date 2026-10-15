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
    # round 3 F04: the configured command is the goal's `verify_command` / config `verify.command` (what the code reads)
    assert "`verify.command`" in _skill("sigma-verify") and "else repo tests/build/lint" in " ".join(_skill("sigma-verify").split())


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


def test_unreachable_project_rules_are_recorded_and_flagged_not_held():      # D2 round-3 F02
    review = " ".join(_skill("sigma-review").split())
    assert "The approval is not held: `work.py record-review` records `project_rules: not-judged (<why>)`" in review
    assert "doctor and session start flag it" in review


# --- third independent Codex read, docs/launch/evidence/review-s5-codex-read-round3-9065e4aa5076.md ---

def _flat(path):
    return " ".join((SKILLS / path).read_text(encoding="utf-8").split())


def test_failing_case_rule_states_the_risk_review_exception():              # F03
    review = _flat("sigma-review/SKILL.md")
    assert "The failing-case rule binds this general review's findings; a dedicated risk review's `critical`/`high` blocks without one" in review
    assert "Reporting one you could not write a failing case for, as blocking" not in review
    assert "(a risk review's `critical`/`high` excepted)" in _flat("sigma-review/references/axes.md")


def test_verify_command_precedence_is_the_codes():                          # F04 (code: state.declared_verify_commands)
    assert "goal frontmatter `verify_command`, else config `verify.command` (as `loop.py verify`; `project.md` is not read)" in _flat("sigma-verify/SKILL.md")
    assert "Verify command** if set" not in _flat("sigma-verify/SKILL.md")
    assert "frontmatter `verify_command`, else config `verify.command`; `project.md` is not read" in _ref("landing.md")
    assert "(frontmatter `verify_command`, else `verify.command`)" in _ref("running.md")


def test_autonomous_retro_writes_the_kg_note_too():                         # F05
    for text in (_flat("sigma-loop/SKILL.md"), _ref("progress.md")):
        assert "write only the audit-trail notes and the KG corpus note when `knowledge_graph.enabled`" in text


def test_secret_path_exception_is_a_humans_to_add():                        # F08
    landing = _ref("landing.md")
    assert "which a human adds: never add or edit it (or any secret exception) yourself" in landing
    assert "the answer is the EXACT path" not in landing


def test_pr_review_verdict_words_map_to_evidence_results():                 # F09
    review = _flat("sigma-review/SKILL.md")
    assert "APPROVE→`approve`, SEND-BACK or BLOCK→`block`" in review
    assert "you return `unblock` only when clearing a prior loop block, never inferred from APPROVE" in review


def test_unblock_is_documented_as_needing_an_earlier_block():              # F09 code check
    assert "refuses it unless this PR has an earlier confirmed block (post `approve`" in _ref("landing.md")
    assert "refused without an earlier confirmed block (post `approve` if clean)" in _ref("running.md")
    assert "`post-review` refuses it with no earlier block" in _flat("sigma-review/SKILL.md")


def test_ceiling_branch_records_failed_not_parked():                        # F14
    running = _ref("running.md")
    assert "record it `failed` (`parked` only for a human decision)" in running
    assert "park or fail it rather than cycling" not in running


def test_ambiguous_review_post_parks_never_reposts():                       # round 3 F07
    landing, running = " ".join(_ref("landing.md").split()), " ".join(_ref("running.md").split())
    assert "that is not proof it never landed" in landing
    assert "publish no other generation" in landing and "--qkind review_post_ambiguous" in landing
    assert "work.py abandon-review-post" in landing and "then `/sigma-unpark`" in landing
    assert "genuinely never reached GitHub" not in landing and "Publish a **fresh** generation for the same, unchanged revision" not in landing
    assert "still ambiguous, park (`--qkind review_post_ambiguous`), never post again" in running
    assert "never by posting again" not in running

"""#140: the five orchestrator/gate SKILL.md files instruct the agent to call `loop.py emit` at
the right moments. These tests are structural greps, mirroring `tests/test_config_discoverability.py`'s
model — they catch the prose being deleted or reworded so it silently stops instructing the
command it names.

BE HONEST ABOUT WHAT THIS PROVES. A literal-substring match proves the command line is still
present in the file; it proves NOTHING about whether the agent actually reads and follows it, or
whether the flags/values in the line are still valid against `loop.py`'s real vocabulary. Only
`test_the_idiom_still_matches_loop_pys_actual_emit_verb` below is independent of the prose files
themselves — it re-derives the verb from `loop.py`'s own source, so a rename of the `emit` verb
fails this suite loudly instead of five prose tests silently passing forever against a dead
command."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP_PY = (ROOT / "skills" / "sigma-loop" / "scripts" / "loop.py").read_text(encoding="utf-8")

SDLC_LOOP = (ROOT / "skills" / "sigma-loop" / "SKILL.md").read_text(encoding="utf-8")
SDLC_GOAL = (ROOT / "skills" / "sigma-goal" / "SKILL.md").read_text(encoding="utf-8")
SDLC_PLAN_REVIEW = (ROOT / "skills" / "sigma-plan-review" / "SKILL.md").read_text(encoding="utf-8")
SDLC_ALIGN = (ROOT / "skills" / "sigma-align" / "SKILL.md").read_text(encoding="utf-8")
SDLC_RETRO = (ROOT / "skills" / "sigma-retro" / "SKILL.md").read_text(encoding="utf-8")


def test_the_idiom_still_matches_loop_pys_actual_emit_verb():
    """If `loop.py`'s verb dispatch is ever renamed or restructured, this fails loudly instead of
    the five prose tests below silently passing forever against a command that no longer exists."""
    assert 'argv[1] == "emit"' in LOOP_PY, (
        "loop.py's emit-verb dispatch idiom changed — the five SKILL.md prose tests below are "
        "now checking for a command that may no longer exist; update this staleness check AND "
        "verify the prose is still accurate.")


def test_sdlc_loop_no_longer_instructs_the_bare_phase_emit():
    """#1626: the bare, unmeasured `loop.py emit ... phase --phase ... --state start/end` idiom is
    retired from the per-goal loop prose — the OLD text admitted, in the very next sentence, "the
    loop cannot measure per-phase timing or spend from prose", which is exactly the invisibility
    issue #1626 fixes. `phase_report.py start`/`end` replaces it: a real script that prices a
    phase's own transcript and writes the SAME `phase` ledger event, now carrying real
    `tokens_in`/`tokens_out` instead of none at all (see tests/test_vocabulary_coverage.py's own
    `phase` allowlist removal for the ledger-kind-coverage half of this same change). INVERTED
    from the old presence-only pin, mirroring test_sdlc_loop_no_longer_instructs_token_self_
    report's own precedent (#955)."""
    assert 'emit .sdlc "$goal" phase --phase' not in SDLC_LOOP
    assert 'phase_report.py" start .sdlc' in SDLC_LOOP
    assert 'phase_report.py" end .sdlc' in SDLC_LOOP


def test_sdlc_loop_no_longer_instructs_token_self_report():
    """#955: the per-goal/per-phase token *self-report* prose is RETIRED. The passive transcript
    observer (#635–#639) captures cost automatically; spec §8 records that the SKILL.md self-report
    instruction "has never produced a single record". This pin is INVERTED from the old
    `test_sdlc_loop_instructs_per_phase_spend_attribution` — it now guards the removal, so the dead
    instruction cannot silently creep back into the per-goal loop prose.

    It does NOT assert on `loop.py spend .sdlc <tokens>` / `budget.max_tokens`: those strings lived
    ONLY inside the removed block, so asserting their presence would fail. The `spend` verb,
    `state.add_tokens`, and the `max_tokens` enforcement mechanism remain untouched in `loop.py`
    itself — only the loop-prose instruction to self-report is gone."""
    # The self-report guidance is gone.
    assert 'surfaces token usage' not in SDLC_LOOP
    assert '--phase <phase> --tokens_in' not in SDLC_LOOP
    # ...but the per-run token ceiling stays documented in the DONE/BUDGET stop sentence, so an
    # adopter still knows `budget.max_tokens` is one of the three ceilings that can trip a stop.
    assert 'reported tokens' in SDLC_LOOP


def test_sdlc_goal_no_longer_instructs_the_bare_phase_emit():
    """#1626, the single-goal counterpart of test_sdlc_loop_no_longer_instructs_the_bare_phase_
    emit above — same retirement, same replacement, same reasoning; kept as a separate pin because
    this file's own convention is one test per orchestrator/gate SKILL.md, never a shared assertion
    across two."""
    assert 'emit .sdlc "<goal>" phase --phase' not in SDLC_GOAL
    assert 'phase_report.py" start .sdlc' in SDLC_GOAL
    assert 'phase_report.py" end' in SDLC_GOAL


def test_sdlc_plan_review_no_longer_instructs_the_gate_emit():
    """#258: the plan-review verdict is now RECORDED by `work.py record-plan-review`, against the
    `Plan sha256:` line of the brief written at dispatch, and that verb is the one emitter of the
    `plan_review` journal gate. Leaving the old hand-typed `loop.py emit` in place too would
    double-emit one verdict as two events. INVERTED from the old
    test_sdlc_plan_review_instructs_gate_emit, following the #1626/#1013 precedent
    (test_sdlc_retro_no_longer_instructs_a_standalone_retro_emit below) -- it guards the removal.
    And the record reads the dispatch-time brief file; it never rebuilds the brief (a rebuild hashes
    the edited plan, so the check could never fail)."""
    assert 'gate --gate plan_review' not in SDLC_PLAN_REVIEW
    assert 'work.py" record-plan-review .sdlc "<goal>"' in SDLC_PLAN_REVIEW
    assert "--plan-sha256" in SDLC_PLAN_REVIEW
    verdict = SDLC_PLAN_REVIEW.split("## Verdict", 1)[1].split("## 5.", 1)[0]
    assert 'review_context.py" brief' not in verdict


def test_sdlc_plan_review_verdict_mapping_is_present():
    assert "SOUND-WITH-REFINEMENTS" in SDLC_PLAN_REVIEW and "`warn`" in SDLC_PLAN_REVIEW
    assert "FIX-FIRST" in SDLC_PLAN_REVIEW and "`block`" in SDLC_PLAN_REVIEW


def test_sdlc_align_instructs_gate_emit():
    assert 'emit .sdlc "(alignment)" gate --gate alignment' in SDLC_ALIGN


def test_sdlc_align_never_instructs_a_block_verdict():
    """Align is read-only/advisory and never blocks a merge — the prose must say `block` is never
    used here rather than silently omitting a value an agent might otherwise guess belongs."""
    assert "--verdict pass|warn" in SDLC_ALIGN
    assert "pass|warn|block" not in SDLC_ALIGN


def test_sdlc_retro_no_longer_instructs_a_standalone_retro_emit():
    """issue #1013: the standalone `loop.py emit ... retro --grade ...` prose line is retired -- the
    grade is now captured structurally via `_record`'s own `retro_grade` parameter, reached through
    the `record` verb's new `--retro-grade` flag (skills/sigma-loop/SKILL.md step 6, skills/sigma-goal/
    SKILL.md step 4). Leaving the old standalone command in place too would double-emit the grade as
    two separate retro events for one retrospective. INVERTED from the old
    test_sdlc_retro_instructs_retro_emit, mirroring test_sdlc_loop_no_longer_instructs_token_self_
    report's own precedent (#955) -- guards the removal, not the presence.

    The achieved|partial|diverged enum text stays pinned by
    test_sdlc_retro_grade_values_match_ledger_retro_grades below, UNEDITED -- it still appears in
    this file's replacement prose, now describing the --retro-grade flag."""
    assert 'emit .sdlc "$goal" retro' not in SDLC_RETRO
    assert '--retro-grade' in SDLC_RETRO


def test_sdlc_retro_grade_values_match_ledger_retro_grades():
    assert "achieved|partial|diverged" in SDLC_RETRO

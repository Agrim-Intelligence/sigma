"""Pin SKILL.md step 1/1a's rewording (#2521): on hosts with subagents every goal, one line
or many, is dispatched; on hosts without them `next` and the handoff bound run one goal inline.
The pre-#2521 ">1 line" gate on dispatch (not batch size) must be gone.
Same "stable anchor only" discipline as test_sdlc_loop_skill_session_pid.py."""
import pathlib
import re

SKILL = (
    pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "SKILL.md"
).read_text()
_FLAT = re.sub(r"\s+", " ", SKILL)


def test_the_old_batch_of_one_runs_inline_gate_is_gone():
    assert "byte-identical to plain `next`, and everything below" not in _FLAT, (
        "step 1 still says a one-goal batch runs inline -- this plan requires every goal, including "
        "a batch of one, to be dispatched to its own subagent (see Task 7 of .sdlc/plans/2521.md)")


def test_step_1a_no_longer_gates_dispatch_on_more_than_one_line():
    assert "More than one line? Dispatch the batch" not in _FLAT, (
        "step 1a's heading still gates DISPATCH on batch size >1 -- parallel.goals.enabled should "
        "control only CONCURRENCY now, never whether a goal is dispatched at all")


def test_step_1a_documents_unconditional_dispatch():
    assert re.search(r"every goal.{0,40}one line or many", _FLAT), (
        "expected step 1a to say every goal -- one line or many -- is dispatched; if the exact "
        "wording changed, update this pin to match, but confirm the unconditional claim is still "
        "made somewhere in step 1/1a")


def test_the_handoff_verdict_is_documented_at_step_2():
    assert "HANDOFF" in SKILL, (
        "step 2 must recognise the new HANDOFF terminal kind alongside DONE/BUDGET (#2521)")


def test_the_stop_block_documents_the_handoff_stop_line():
    assert "LOOP STOP: handoff" in SKILL, (
        "the STOP paragraph must name the third machine-readable line, LOOP STOP: handoff (#2521)")


def test_no_subagent_host_has_single_goal_inline_path_and_fresh_session_bound():
    assert "Without subagents" in SKILL
    assert "use `next` with the same arguments" in _FLAT
    assert "goal's steps 2–7 inline" in _FLAT
    assert "HANDOFF" in SKILL and "fresh session" in SKILL


def test_codex_goal_slot_requires_a_fresh_child_context():
    assert 'fork_turns="none"' in SKILL
    picking = (pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" /
               "references" / "picking.md").read_text()
    assert 'fork_turns="none"' in picking

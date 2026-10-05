"""Issue #2543 — a review-gate block or a failed `loop.py verify` must redispatch the fix (and the
re-review that follows) to a FRESH subagent, never resume the one that got blocked or the one that
blocked it. Before this change, `references/landing.md`'s block->fix->re-review paragraph (and
SKILL.md's condensed copy of it) said only "fix them in the worktree (back to Implement)" and
"re-review" -- ambiguous about whether "back to Implement" meant a new dispatch or continuing in
whatever subagent already held the blocked context. Left ambiguous, the cheapest reading for an
agent already holding a large blocked-review context is to keep going in the same turn, which is
exactly the stale-context growth #2543 exists to stop.

Two test shapes, matching tests/skill_corpus.py's own documented rule (see its module docstring):
a general instruction that must survive anywhere in the corpus (references/running.md's general
fix/re-review rule, references/landing.md's worked post-PR example) is read through
`skill_corpus()`; a pin about the always-attached SKILL.md BODY surviving a mid-run compaction
reads SKILL.md directly instead, the same shape tests/test_sdlc_loop_skill_session_pid.py uses.

Every anchor below was confirmed ABSENT from the pre-fix tree before this file was written
(AGENTS.md's "run the control" rule) -- each assertion is a real red->green pin, not a tautology
against text that was already there."""
import pathlib
import re

from skill_corpus import skill_corpus

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_MD = ROOT / "skills" / "sigma-loop" / "SKILL.md"

CORPUS = skill_corpus("sigma-loop")
_CORPUS_FLAT = re.sub(r"\s+", " ", CORPUS)

_SKILL_BODY = SKILL_MD.read_text(encoding="utf-8")
_SKILL_FLAT = re.sub(r"\s+", " ", _SKILL_BODY)


# --- references/running.md: the general fix/re-review fresh-redispatch rule --------------------

def test_running_md_states_a_fix_round_never_resumes_the_blocked_agent():
    assert "never resume the one that got blocked" in _CORPUS_FLAT, (
        "expected the sigma-loop corpus (references/running.md) to state that a review-gate block "
        "or a failed verify redispatches the fix to a FRESH subagent, never a resume of the one "
        "that got blocked -- #2543's core rule"
    )


def test_running_md_states_the_fix_handoff_excludes_the_prior_transcript():
    assert "never the maker's prior transcript" in _CORPUS_FLAT, (
        "expected references/running.md to name the fix dispatch's hand-off shape (blocking "
        "findings + plan path + worktree path) and explicitly exclude the maker's prior "
        "transcript -- without this an agent could still paste history into a 'fresh' subagent"
    )


def test_running_md_states_the_rereview_gets_its_own_prior_verdict_only():
    assert "only its own prior verdict" in _CORPUS_FLAT, (
        "expected references/running.md to say a re-review subagent is handed only its OWN "
        "prior verdict, not the maker's context or the earlier reviewer's"
    )


def test_running_md_excludes_pasted_review_history_from_the_rereview_handoff():
    assert "never the earlier review's transcript pasted in by hand" in _CORPUS_FLAT, (
        "expected references/running.md to explicitly rule out pasting the earlier review's "
        "transcript into a re-review dispatch -- the live diff/brief re-run is the only "
        "'what changed since' pointer allowed"
    )


def test_running_md_gives_codex_its_own_fork_turns_none_rule_for_a_redispatch():
    assert 'fork_turns="none"' in CORPUS, (
        "expected references/running.md to tell Codex hosts to set fork_turns=\"none\" when "
        "redispatching a fix or re-review, matching this repo's host-agnostic convention "
        "(AGENTS.md: 'Host-agnostic or it does not ship') -- without it, Codex's default carries "
        "the blocked agent's own conversation into the child"
    )


# --- references/landing.md: the worked post-PR block->fix->re-review example -------------------

def test_landing_md_dispatches_the_post_pr_fix_to_a_fresh_subagent():
    assert "dispatched to a fresh subagent" in _CORPUS_FLAT, (
        "expected references/landing.md's block->fix->re-review paragraph (step 6) to say the "
        "fix is DISPATCHED to a fresh subagent, not merely 'fix them in the worktree' with no "
        "statement of whether that continues the blocked context"
    )


def test_landing_md_names_the_post_pr_rereview_as_equally_fresh():
    assert "equally fresh reviewer subagent" in _CORPUS_FLAT, (
        "expected references/landing.md's re-review step to say the reviewer is equally fresh -- "
        "otherwise the paragraph reads as the SAME reviewer subagent being asked to look again"
    )


# --- SKILL.md's own condensed step-6 body: this must survive as the ALWAYS-ATTACHED copy -------
# Direct SKILL.md read (not skill_corpus), matching test_sdlc_loop_skill_session_pid.py's own
# rule: a pin about the always-attached body needs the stronger, file-specific assertion.

def test_skill_md_condensed_body_also_says_never_resume_the_blocked_one():
    assert "fresh, never resumed" in _SKILL_FLAT, (
        "SKILL.md's condensed step-6 'Blocking issues' bullet is the copy that survives a "
        "mid-run compaction -- it must carry the fresh-redispatch rule too, not just "
        "references/running.md, or a compacted session loses it entirely"
    )


def test_skill_md_condensed_body_scopes_the_rereview_handoff_too():
    assert "re-review, also fresh" in _SKILL_FLAT, (
        "SKILL.md's condensed step-6 body must also say the re-review is equally fresh -- the "
        "always-attached copy of the rule, kept this terse to stay inside the token budget"
    )

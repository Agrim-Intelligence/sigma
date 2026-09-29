"""#1212: `merge()` in skills/agrim-loop/scripts/work.py has no in-repo Python caller at all --
the sole consumer of its return string is the autonomous agent reading skills/agrim-loop/SKILL.md's
prose and following its documented decision table. That makes SKILL.md the operational contract for
merge(), not just a description of it -- when merge()'s real behavior changes, SKILL.md has to
change with it (the project's own convention: commit ec19e3e / #254/#759 touched SKILL.md in the
same commit that changed this exact function's contract).

#1212 made a direct `gh pr merge` the common landing path (reserving `--auto` for the one case it
exists for: a required check still pending) and introduced a new terminal-success return shape,
`PR #N merged (<method>) - ...`, for it. These anchors guard SKILL.md against drifting from that
contract again -- not just patching today's gap.

#1611 split that prose into SKILL.md plus references/*.md, so merge()'s decision table now
lives in references/landing.md, which SKILL.md points at and tells the agent to read before
its first merge of a run. The contract is the SKILL, not the one file it used to fit in, so
`SKILL` below is the whole corpus (tests/skill_corpus.py explains why that is not a weaker
assertion, and evals/skill_structure.py is what keeps every part of it reachable)."""
import pathlib
import re

from skill_corpus import skill_corpus

SKILL = skill_corpus("agrim-loop")


def _flat(text):
    """Whitespace-normalised, so a hand-wrapped sentence compares by its words —
    tests/test_goal_scope.py's own convention. The anchors below are about what the
    contract SAYS; re-wrapping a paragraph (as #1611's split did) must not read as the
    contract having changed, and deleting the sentence still fails."""
    return re.sub(r"\s+", " ", text)


def test_documents_the_direct_merge_terminal_success_shape():
    # merge()'s decision table must have a branch for the return shape a direct, common-case
    # landing now actually produces -- work.py:1060 `f"PR #{rec['pr']} merged ({method}) - ..."`.
    assert "PR #N merged" in SKILL, (
        "SKILL.md's merge() decision table omits the `PR #N merged (<method>) - ...` shape that "
        "a direct landing (the new common case since #1212) actually returns"
    )


def test_does_not_describe_arming_as_the_general_landing_behavior():
    # Pre-#1212, arming `--auto` was the ONLY merge call merge() ever made, so describing it as
    # what merge() does in general was accurate. #1212 changed that: a direct `gh pr merge` is now
    # the default, and arming is reserved for the one case a direct merge would be refused right
    # now (a required check still pending). The old blanket phrasing must not survive unchanged.
    assert "arms GitHub's own" not in SKILL, (
        "SKILL.md still describes arming --auto as merge()'s general behavior; #1212 made a "
        "direct `gh pr merge` the default and reserved --auto for the pending-check case only"
    )


def test_pr_review_docs_require_the_generation_bound_evidence_gesture():
    """The documented post-review call must carry evidence the CLI actually accepts."""
    required = (
        'review_context.py" brief .sdlc "$goal" --for pr-review',
        '--output ".sdlc/state/review-manifests/$goal.json"',
        'work.py" review-paths .sdlc "$goal" --manifest ".sdlc/state/review-manifests/$goal.json" --format sh',
        'work.py" run-resolved-review .sdlc "$goal" --manifest "$REVIEW_MANIFEST" --resolution "$REVIEW_RESOLUTION"',
        'work.py" record-subagent-review .sdlc "$goal" --manifest "$REVIEW_MANIFEST" --resolution "$REVIEW_RESOLUTION" --verdict approve|block|unblock --reason',
        'work.py" review-evidence .sdlc "$goal" --manifest "$REVIEW_MANIFEST" --review-result "$REVIEW_RESULT"',
        'work.py post-review .sdlc "$goal" --evidence "$REVIEW_EVIDENCE" --verdict approve',
    )
    for path in ("references/landing.md", "references/running.md"):
        text = _flat((pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / path).read_text())
        for gesture in required:
            assert gesture in text, "%s omits required PR-review gesture: %s" % (path, gesture)


# --- #1649: merge() now closes an issue, which is a behaviour change, not a wording change --------

def test_documents_that_a_landing_on_a_non_default_base_closes_the_issue_itself():
    # #2615 gave merge() its current tail: not "which base did this land on", but "is the issue
    # still open right after the merge". By this file's own rule (see the module docstring)
    # SKILL.md is merge()'s operational contract, so the agent reading it has to know that a
    # still-open issue gets a close request sent against it -- otherwise the only mention of
    # closing anywhere in the flow is the `Closes #N` that #1649 REMOVED, and an agent told the
    # keyword is gone has been told nothing about what replaces it.
    assert "was still open after the merge" in SKILL and "sent a close request" in SKILL, (
        "SKILL.md's merge() decision table does not say that a still-open issue gets a close "
        "request sent against it right after the merge (#2615) -- the tail merge() now actually "
        "returns"
    )


def test_documents_that_a_close_that_failed_is_still_done_but_owes_a_human_one_action():
    # The half that costs something if it is missing. A failed close leaves a MERGED PR and an OPEN
    # issue, which is exactly the deadlock #1649 exists to fix -- and the outcome is still `record
    # done`, whose own `source.complete()` retries this exact close and parks the goal, rather than
    # silently losing the record, if that retry fails too (#2615's plan-review round 3 corrected an
    # earlier claim here that nothing else would ever surface it). The line naming the failure is
    # still the only handover there is until that retry either succeeds or exhausts itself.
    assert "but could not close #N" in _flat(SKILL), (
        "the skill does not cover merge()'s `… — but could not close #N (…)` tail (#1649): the merge "
        "landed, `record done` is still right, and retries the close itself before a human ever "
        "needs to"
    )


# --- #258: the plan-review verdict is recorded against the brief written at DISPATCH time ---------

def test_plan_review_docs_record_the_verdict_gesture():
    """#258 B-new: the brief a reviewer is handed is written to a file at DISPATCH time, and the
    record gesture only READS that file's first `Plan sha256:` line. A record-time rebuild of the
    brief would hash the edited plan, so the sha check could never fail (rev 1 of the plan did
    exactly that). SKILL.md carries a second copy of the dispatch gesture, so it is read DIRECTLY:
    `skill_corpus` concatenates the references and would pass on running.md's copy alone."""
    scripts = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop"
    brief_file = '/tmp/brief-$(basename "$goal" .md).md'
    dispatch = '[--artifact <path|PR#>] > "' + brief_file + '"'
    running = (scripts / "references" / "running.md").read_text(encoding="utf-8")
    skill = (scripts / "SKILL.md").read_text(encoding="utf-8")
    assert dispatch in _flat(running), "running.md's dispatch gesture does not write the brief file"
    assert dispatch in _flat(skill), "SKILL.md's copy of the dispatch gesture lost the redirect"
    assert "re-run the dispatch gesture above for that gate, redirect included" in _flat(running)
    record = _flat(running.split("**Record the plan-review verdict before Implement**", 1)[1]
                   .split("\n\n**", 1)[0])
    assert 'work.py" record-plan-review .sdlc "$goal" --verdict' in record, record
    assert "awk '/^Plan sha256: /{print $3; exit}' \"" + brief_file + "\"" in record, record
    assert 'review_context.py" brief' not in record, record

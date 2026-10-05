"""#2163 (epic #2161, slice 2 of `.sdlc/design/2154.md`) wired the feature-priority ask into
`/sigma-define`'s own `declare` step as pure SKILL.md prose -- no new Python of this skill's own.
#2266 (epic #2260, slice 6 of `.sdlc/design/2253.md`) changed WHAT that ask does: once priority is
data on the unit, stamping every member's own `priority:` label is actively harmful (it erases the
per-issue tiers the comparator reads), so the ask now records one value on the unit's OWN registry
entry through `define.py set-priority` -- never a write to a member issue.

The verb it calls (`define.py set-priority`, wired in the same commit) has its own test coverage
where it is implemented (`tests/test_define.py`); this file's job, exactly like
`tests/test_sdlc_goal_review_skill.py`'s for that skill's own feature-ification step, is pinning the
OPERATIONAL CONTRACT this prose states -- that the question exists, is asked at the right point
(after `declare`, consistent with `sigma-goal-review`'s own placement), uses the one
AskUserQuestion/plain-conversation convention this pipeline already settled on (`sigma-dossier`'s own
terminal question), and that skipping it is byte-identical to this skill's behavior before the
question existed, by construction: no other sentence in this file may call `set-priority`. Stable
anchors only (structural claims, the exact CLI shape, ordering) -- not prose wording that could
reasonably be rephrased.
"""
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = (_ROOT / "skills" / "sigma-define" / "SKILL.md").read_text(encoding="utf-8")


def test_documents_the_priority_question():
    assert "Give `feature:<name>` a priority?" in SKILL
    assert "P0-P4, or skip" in SKILL


def test_priority_question_follows_the_ask_user_question_convention():
    # The pipeline's one settled convention for an optional ask, named explicitly rather than
    # re-described -- exactly how `sigma-goal-review`'s own feature-ification step cites it.
    assert "AskUserQuestion" in SKILL
    assert "sigma-dossier" in SKILL
    assert "terminal question" in SKILL


def test_priority_question_sits_after_declare_and_before_assignment():
    # Ordering matches `sigma-goal-review`'s own placement of the identical question, so a user sees
    # one consistent ask regardless of which path created the feature -- not because `set-priority`
    # itself needs the member issues `declare` just filed (it records data on the unit, and reads
    # none of them), so the ask must not be reachable before that call, and must not bleed into
    # steps 8-9's own questions.
    declare_call_at = SKILL.index(
        'python3 "${CLAUDE_SKILL_DIR}/scripts/define.py" declare .sdlc --unit <name> --issues <n,n,n>'
    )
    priority_heading_at = SKILL.index("priority (optional)")
    steps_89_at = SKILL.index("### Steps 8")
    assert declare_call_at < priority_heading_at < steps_89_at


def test_documents_the_set_priority_call():
    assert 'define.py" set-priority .sdlc' in SKILL
    assert "--unit <name> --priority <P>" in SKILL


def test_the_priority_ask_never_writes_a_member_issues_own_label():
    # #2266's whole point: the prose must not describe stamping/bumping member issues as what
    # answering this question does -- that is the defect being retired.
    block = SKILL.split("### After `declare` succeeds: priority (optional)", 1)[-1]
    block = block.split("### Steps 8", 1)[0]
    assert "never on any member" in block or "never touches a member issue" in block


def test_bump_priority_is_not_called_from_the_priority_ask_but_may_be_named_as_a_different_tool():
    # The verb itself is allowed to survive in this file as a NAMED, separate tool (#2266: "the verb
    # itself can stay... it just stops being the answer to 'prioritise this feature'") -- what must
    # not exist is an invocation of it from the priority-ask block.
    block = SKILL.split("### After `declare` succeeds: priority (optional)", 1)[-1]
    block = block.split("### Steps 8", 1)[0]
    assert 'scripts/define.py" bump-priority' not in block


def test_documents_skip_is_byte_identical_by_construction():
    assert "byte-identical" in SKILL
    assert "no other code path" in SKILL
    # The construction argument, checked structurally rather than just asserted in prose: the
    # invocation itself appears exactly once in the whole skill, so there genuinely is only the one
    # call site a real answer can reach -- a skip cannot silently trip a second, undocumented path.
    assert SKILL.count('scripts/define.py" set-priority .sdlc') == 1

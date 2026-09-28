"""Pin `--session-pid "$PPID"` onto /agrim-loop's own `work.py start` instruction (#1687).

WHY A DOC TEST IS THE RIGHT SHAPE HERE. #1687's fix is a CLI flag, chosen over inferring the
caller from the process tree because a flag is text every host executes verbatim while a
process-tree walk is a claim about one host's topology (AGENTS.md: "host-agnostic or it does not
ship"). The cost of that choice is that the whole fix hangs on one markdown token: delete
`--session-pid "$PPID"` from SKILL.md and `work.py` silently returns to reading the goal's own
agent as a second session, skipping rebase upkeep on every pick — and the entire Python suite
stays green, because nothing else in the tree names that token. Measured: with the flag removed
from the instruction, `pytest tests/ -q` reported 5549 passed.

This is the same failure #1199 shipped with and `tests/test_triage_skill_docs.py` was written to
stop — a correct mechanism reached by an instruction that forgot to use it — so this is
deliberately that file's structure, applied to the other half of the same identity contract.
Stable anchor only (the literal invocation text), never the surrounding prose."""
import pathlib
import re

SKILL = (
    pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "SKILL.md"
).read_text()

# Whitespace-collapsed before matching, for the reason test_triage_skill_docs.py gives: this
# repo's prose hand-wraps `work.py" start .sdlc "$goal" --session-pid "$PPID"` across physical
# lines, and a per-line check would false-fail on correctly-wrapped, correctly-fixed prose.
_FLAT = re.sub(r"\s+", " ", SKILL)

#: Every `work.py start .sdlc "$goal"` invocation, bare or flagged. Anchored on the `.sdlc "$goal"`
#: argument pair so the prose mention at step 3b ("beyond what `work.py start` already does") — a
#: reference, not an invocation — cannot be counted as one.
_INVOCATION = re.compile(r"work\.py[\"']?\s+start\s+\.sdlc\s+\"\$goal\"")

#: The same invocation, IMMEDIATELY followed by the required flag — "immediately" so a mention of
#: the flag elsewhere in the same paragraph cannot falsely satisfy this.
_FLAGGED = re.compile(r"work\.py[\"']?\s+start\s+\.sdlc\s+\"\$goal\"\s+--session-pid\s+\"\$PPID\"")


def test_the_work_start_invocation_exists_in_the_doc():
    """Guards this test against silently matching nothing if the doc is ever restructured."""
    assert len(_INVOCATION.findall(_FLAT)) >= 1, (
        "expected a `work.py start .sdlc \"$goal\"` invocation in /agrim-loop's SKILL.md — if step "
        "3a no longer cuts the worktree this way, this test is stale, not passing"
    )


def test_every_work_start_invocation_passes_session_pid():
    total = len(_INVOCATION.findall(_FLAT))
    flagged = len(_FLAGGED.findall(_FLAT))
    assert flagged == total, (
        f"found {total} `work.py start .sdlc \"$goal\"` invocation(s) in /agrim-loop's SKILL.md but "
        f"only {flagged} pass --session-pid \"$PPID\" immediately after the goal — a bare "
        f"invocation is #1687 exactly: the agent registered by `agent-start --pid $PPID` one "
        f"paragraph above reads as a DIFFERENT live process, so the resume refuses and rebase "
        f"upkeep is skipped silently on every pick"
    )


def test_agent_start_still_supplies_the_pid_the_flag_must_match():
    """The two halves are one contract: `--session-pid` is only meaningful because step 3a
    registered THAT SAME `$PPID`. If the registration line ever stops using `$PPID`, the flag above
    is pinned to a value nothing writes, and this pins the other end."""
    registrations = re.findall(r"loop\.py[\"']?\s+agent-start\s+\.sdlc\s+\"\$goal\"", _FLAT)
    flagged = re.findall(r"loop\.py[\"']?\s+agent-start\s+\.sdlc\s+\"\$goal\"\s+--pid\s+\$PPID", _FLAT)
    assert registrations and len(flagged) == len(registrations), (
        "expected `loop.py agent-start .sdlc \"$goal\" --pid $PPID` in /agrim-loop's SKILL.md — "
        "`work.py start --session-pid \"$PPID\"` is only correct because this line registers the "
        "same pid"
    )

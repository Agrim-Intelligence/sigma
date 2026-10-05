"""Lock sigma-triage's own shipped `/sigma-loop` hand-off: #1239 review round 2, finding 1 -- the
skill's "Start now" prose (step 5) and its "Internal flow" example both issued bare
`loop.py start .sdlc` / `loop.py next .sdlc`, never `--session-pid`, even after #1199 required
`--session-pid "$PPID"` on every such call in /sigma-loop's own SKILL.md/README. Reproduced directly
(see tests/test_loop.py::test_shipped_skill_start_then_next_via_real_separate_bash_dispatch_never_
redispatches and its sibling test_os_getppid_is_unstable_across_genuinely_separate_forked_dispatch
for the underlying mechanism): two bare, separately-dispatched `loop.py start`/`next .sdlc` calls
followed by a third, correctly-`--session-pid`-flagged `loop.py next .sdlc --session-pid "$PPID"`
call -- the shape a subsequent, doc-compliant `/sigma-loop` continuation would issue -- re-dispatches
the SAME goal, because the bare calls' own internal `os.getppid()` fallback registers the claim
under a pid that is already dead by the time anything else checks the registry. Stable anchor only
(the literal invocation text) -- not surrounding prose wording."""
import pathlib
import re

TRIAGE_SKILL = (
    pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-triage" / "SKILL.md"
).read_text()

# Whitespace-collapsed to one line before matching: this repo's own prose hand-wraps an
# invocation like `loop.py start .sdlc --session-pid "$PPID"` across physical lines (see
# /sigma-loop's own SKILL.md, which wraps `loop.py ... start .sdlc` / `--session-pid "$PPID"`
# the same way) -- a literal per-physical-line check would false-fail on correctly-wrapped,
# correctly-fixed prose. Collapsing to single spaces reads it the way a reader actually does;
# inside the fenced ```bash block each command is already its own single physical line, so
# collapsing changes nothing there.
_FLAT = re.sub(r"\s+", " ", TRIAGE_SKILL)

# Every `loop.py ... start .sdlc` / `loop.py ... next .sdlc` invocation, bare or flagged.
_INVOCATION = re.compile(r"loop\.py[\"']?\s+(?:start|next)\s+\.sdlc\b")

# The same invocation, immediately followed (only whitespace between) by the required flag --
# "immediately" so a flag mentioned elsewhere in the same paragraph can't falsely satisfy this.
_FLAGGED_INVOCATION = re.compile(
    r"loop\.py[\"']?\s+(?:start|next)\s+\.sdlc\s+--session-pid\s+\"\$PPID\""
)


def test_start_and_next_invocations_exist_in_the_doc():
    # Guards the test itself against silently matching nothing if the doc is rewritten later.
    count = len(_INVOCATION.findall(_FLAT))
    assert count >= 2, (
        "expected to find loop.py start/next .sdlc invocations in sigma-triage's SKILL.md -- if "
        "the doc no longer hands off to /sigma-loop this way, this test is stale, not passing"
    )


def test_every_start_and_next_invocation_passes_session_pid():
    total = len(_INVOCATION.findall(_FLAT))
    flagged = len(_FLAGGED_INVOCATION.findall(_FLAT))
    assert flagged == total, (
        f"found {total} loop.py start/next .sdlc invocation(s) in sigma-triage's SKILL.md but "
        f"only {flagged} pass --session-pid \"$PPID\" immediately after .sdlc -- a bare "
        f"invocation is the exact double-dispatch bug #1199 exists to close, still reachable "
        f"through sigma-triage's own shipped /sigma-loop hand-off"
    )


# --------------------------------------------------------------------------------------------
# #2296 (design .sdlc/design/2287.md, BR-18/BR-19/BR-20/BR-25, slice 3): step 2's "Question
# round" used to ask three sub-questions unconditionally even though each already had something
# to resolve from. Pins the corrected prose -- the SILENT-by-default half AND the residual
# ask each sub-question still keeps -- against the shipped SKILL.md text directly, the same
# "stable anchor, not surrounding prose wording" posture the session-pid tests above already use.

_QUESTION_ROUND = re.search(
    r"2\. \*\*Question round\*\*.*?(?=\n3\. \*\*Compile\*\*)", TRIAGE_SKILL, re.DOTALL)
assert _QUESTION_ROUND, (
    "could not locate step 2's 'Question round' block in sigma-triage's SKILL.md by its own "
    "numbered-step anchor -- if the step numbering or heading changed, this test is stale, not "
    "passing"
)
_QUESTION_ROUND_TEXT = _QUESTION_ROUND.group(0)


def test_wave_capacity_applies_the_config_default_silently():
    """#2296: no longer an unconditional confirm -- the config default applies without asking."""
    assert "parallel.goals.max_concurrent" in _QUESTION_ROUND_TEXT
    assert "silently" in _QUESTION_ROUND_TEXT.lower()
    assert "confirm the wave capacity" not in _QUESTION_ROUND_TEXT.lower(), (
        "the old unconditional 'confirm the wave capacity' prose is still present -- the "
        "silent-default rewrite did not actually replace it"
    )


def test_wave_capacity_override_still_surfaces_an_ask():
    """The residual exception survives: an operator who explicitly wants a different cap for this
    run is still asked -- silence by default does not mean the option disappeared."""
    lowered = _QUESTION_ROUND_TEXT.lower()
    assert "override" in lowered
    assert "ask" in lowered


def test_detected_dependency_edges_apply_without_reconfirmation():
    """#2296: a mechanically-detected edge is no longer re-confirmed -- it auto-applies, matching
    how `dependency_gate`/`auto_unpark`/`_promote_blockers` already trust the same extraction
    (`blocker_scan`) unattended elsewhere in this codebase."""
    lowered = _QUESTION_ROUND_TEXT.lower()
    assert "auto-apply" in lowered or "auto apply" in lowered
    assert "without re-confirmation" in lowered
    assert "dependency_gate" in _QUESTION_ROUND_TEXT
    assert "auto_unpark" in _QUESTION_ROUND_TEXT
    assert "_promote_blockers" in _QUESTION_ROUND_TEXT


def test_a_genuinely_missing_edge_is_still_asked_about():
    """The residual exception for edges: the text does not establish EVERY edge, and a genuinely
    missing one is still worth asking about -- not silently dropped along with the reconfirmation
    step that used to cover it."""
    lowered = _QUESTION_ROUND_TEXT.lower()
    assert "missing edge" in lowered


def test_priority_fill_is_mechanical_not_a_blanket_ask():
    """#2296 (BR-19/BR-20/BR-25): the priority-fill sub-ask now names the mechanical rubric
    application (`_bucket_hygiene`/`priority_hint`) rather than describing an unconditional
    human fill, and still names the one residual ask -- genuine ambiguity between two adjacent
    tiers, the same shape sigma-scope's own SKILL.md already treats as a real question."""
    assert "_bucket_hygiene" in _QUESTION_ROUND_TEXT
    assert "priority_hint" in _QUESTION_ROUND_TEXT
    assert "ambiguous" in _QUESTION_ROUND_TEXT.lower()
    assert "adjacent tier" in _QUESTION_ROUND_TEXT.lower()


def test_priority_criteria_section_documents_the_mechanical_reader():
    """The rubric's own section (BR-19) now says something reads it mechanically, not just that a
    human should -- otherwise the rubric and its application drift apart the moment either is
    edited without the other."""
    assert "_priority_hint" in TRIAGE_SKILL
    assert "#2296" in TRIAGE_SKILL

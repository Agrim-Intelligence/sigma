"""`/agrim-goal --feature <unit>` — the unit-scoping contract (#1663, epic #1464, story #1427).

`skills/agrim-goal/` is SKILL.md AND NOTHING ELSE: no script, no argument parser, no code path to
compile. Its "goal selection" is step 1 of its own prose, which the host agent reads and follows.
So the flag is a PROSE CONTRACT — and that is this repo's established shape for a skill-level flag
rather than a corner cut: `/agrim-audit --file` is exactly the same thing, a flag no script parses
whose whole implementation is the skill's text, and `tests/test_audit_skill.py` holds it exactly
the way this file holds this one.

BE HONEST ABOUT WHAT A SUBSTRING PROVES. It proves a sentence is still in the file. It proves
nothing at all about an agent obeying it. So half of the tests below never read the prose: they
re-derive the things the prose CITES from the source those things actually live in, so a rename
lands as a red test instead of a document that quietly starts lying. That is not a hypothetical
failure mode here — `tests/test_docs.py`'s own docstring records this repo shipping a falsifiable
doc claim that was, in fact, false, for as long as it took someone to notice.

WORDING IS COMPARED WHITESPACE-NORMALISED, NEVER BYTE-FOR-BYTE. The one sentence this file requires
two skills to share is wrapped by hand in both, at whatever column each file's paragraph happens to
land on; a byte comparison would therefore fail on a reflow that changed nothing anybody reads, and
the first person to hit that would "fix" it by deleting the assertion. What must not drift is the
WORDS.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
SCRIPTS = SKILLS / "agrim-loop" / "scripts"

SDLC_GOAL = (SKILLS / "agrim-goal" / "SKILL.md").read_text(encoding="utf-8")
SDLC_LOOP = (SKILLS / "agrim-loop" / "SKILL.md").read_text(encoding="utf-8")

#: Read as TEXT, not imported. These tests ask whether the prose still matches the source, so the
#: source is a thing to be measured rather than a dependency to be executed — `tests/test_docs.py`'s
#: own convention, for its own reason.
FEATURES = (SCRIPTS / "features.py").read_text(encoding="utf-8")
UNIT_COMPLETION = (SCRIPTS / "unit_completion.py").read_text(encoding="utf-8")
LOOP_PY = (SCRIPTS / "loop.py").read_text(encoding="utf-8")

#: THE ONE WORDING, AND IT IS NOT THIS FILE'S TO CHOOSE. `docs/branching-model.md` §14 settled it
#: (#1660); `tests/test_docs.py::_SCOPE_GUARANTEE` is the same constant, asserted over every entry
#: point that takes the flag — `loop.py` and this SKILL — so the two skills and the contract cannot
#: state one guarantee in three voices. Reproduced here, byte for byte, rather than imported: a test
#: whose subject is "did the wording drift" must not read the wording from the thing it is checking
#: against, or a drift on BOTH sides passes silently.
#:
#: Anything that softens it — "prefers", "unless nothing is available", "by default" — is the
#: feature deleted: the flag exists precisely so that a focused run cannot wander, and a preference
#: cannot promise that.
EXCLUSIVITY = "While a `--feature` run is active, no goal outside that unit may be picked."


def _flat(text):
    """Whitespace-normalised, so a hand-wrapped sentence compares by its words. See the module
    docstring for why this is the comparison and a byte one is not."""
    return re.sub(r"\s+", " ", text)


# --------------------------------------------------------------------------- the flag itself

def test_the_flag_is_documented_with_the_loops_spelling():
    """`--feature <name>`, one spelling across both orchestrators and the adopter doc (#1660)."""
    assert "--feature" in SDLC_GOAL, "the flag is not documented at all"
    assert "`--feature <unit>`" in SDLC_GOAL, "the flag must be documented with its argument"
    assert "/agrim-goal --feature voice-interview" in SDLC_GOAL, "a worked invocation must be shown"


def test_step_one_routes_to_the_section():
    """The constraint has to be visible WHERE selection happens. A reader who follows step 1 and
    never scrolls is the reader this whole contract is addressed to."""
    step_one = SDLC_GOAL.split("2. **Recall first**")[0]
    assert "1. Identify the goal" in step_one, "the step-1 anchor moved; this test is now vacuous"
    assert _flat("**If the invocation carried `--feature <unit>`, selection is confined to that "
                 "unit**") in _flat(step_one), (
        "step 1 — the step that selects — must carry the constraint itself, not only a heading "
        "further down the file that a reader following the numbered list never reaches")


def test_the_exclusivity_is_the_contracts_sentence_verbatim():
    """BYTE-FOR-BYTE, not merely whitespace-equal, and on one line. `tests/test_docs.py` compares it
    normalised, which is right for a hard-wrapped document — but this sentence is short enough to
    never need wrapping, so keeping it unwrapped removes the only way a future reflow could make the
    two files disagree about something nobody reads."""
    assert EXCLUSIVITY in SDLC_GOAL, (
        "the settled guarantee is missing or has been reworded — #1660 fixed the wording and "
        "tests/test_docs.py asserts it over this file; quote it, do not paraphrase it")
    assert any(EXCLUSIVITY in line for line in SDLC_GOAL.split("\n")), (
        "the guarantee has been hard-wrapped; keep it on one line so no reflow can split it")


def test_the_guarantee_is_marked_as_quoted_not_authored_here():
    """The reason there is one wording at all. A skill that reads as having WRITTEN this sentence
    invites the next editor to improve it."""
    flat = _flat(SDLC_GOAL)
    assert "**quote it, never reword it.**" in flat
    assert "docs/branching-model.md" in flat, "the sentence's home must be cited"


def test_the_three_commitments_ride_with_the_guarantee():
    """The sentence alone is a slogan. These are the three things it costs, and each is the one a
    tired agent would trade away first."""
    flat = _flat(SDLC_GOAL)
    assert "Exclusive, not preferential — so the run STOPS when the unit drains." in flat, (
        "the reading that makes the feature pointless is the one a reader arrives with; it has to "
        "be closed explicitly")
    assert "Membership is the §4 declaration pair, never the label alone" in flat
    assert "An excluded goal is left exactly as it was found" in flat
    assert "not labelled, not commented on, not parked" in flat, (
        "a refusal that writes to the goal it refused is not a refusal")


def test_the_two_skills_state_the_exclusivity_in_one_wording():
    """The sibling lane adds `--feature` to `/agrim-loop`. THE MOMENT its SKILL.md names the flag,
    the two contracts must make the same promise in the same words: one guarantee stated twice, in
    two wordings, is two guarantees, and the softer one is the one an agent will follow.

    Vacuous until that lands, and it says so rather than pretending to check something."""
    assert EXCLUSIVITY in SDLC_GOAL
    if "--feature" in SDLC_LOOP:
        assert _flat(EXCLUSIVITY) in _flat(SDLC_LOOP), (
            "/agrim-loop documents `--feature` but does not carry the agreed exclusivity wording — "
            "reconcile the two SKILL.md files to ONE sentence (this file's EXCLUSIVITY), do not "
            "loosen either")


def test_the_absent_flag_changes_nothing():
    """The other half of the contract, and the easier one to lose: a scoping feature that also
    quietly alters the unscoped run is a regression wearing a feature's clothes."""
    flat = _flat(SDLC_GOAL)
    assert "**Without the flag none of this section applies**" in flat
    assert "Everything after step 1 is unchanged" in flat


# --------------------------------------------------------------------------- the citations

def test_name_legality_is_cited_and_never_restated():
    """ONE definition of a legal unit name. `features._is_unit_name` is measured against
    `git check-ref-format` and is deliberately case-SENSITIVE about one of its clauses; a prose
    paraphrase of it would be a second definition, free to be subtly wrong in a way nobody
    re-measures."""
    assert "features._is_unit_name" in SDLC_GOAL, "the validator must be named"
    assert "skills/agrim-loop/scripts/features.py" in SDLC_GOAL, "and its home must be cited"
    for restatement in ("check-ref-format", ".lock", "[A-Za-z0-9"):
        assert restatement not in SDLC_GOAL, (
            "the SKILL is restating the unit-name rule (%r) instead of citing it — one definition, "
            "and it lives in features.py" % restatement)


def test_the_cited_validator_still_exists():
    """Independent of the prose: a rename of `_is_unit_name` fails HERE, loudly, instead of leaving
    every prose test above passing forever against a function that no longer exists."""
    assert "def _is_unit_name(" in FEATURES, (
        "features._is_unit_name was renamed or removed — skills/agrim-goal/SKILL.md cites it by "
        "name as the one definition of a legal unit name")


def test_membership_is_the_dual_read_with_the_body_winning():
    """Both declarations, body wins — `features.read`'s answer, not a cheaper one."""
    flat = _flat(SDLC_GOAL)
    assert "features.read" in flat, "membership must be answered by the one reader"
    assert "**the body wins**" in flat, "the conflict rule must be stated"
    assert "the label is attached at pick" in flat, (
        "the label-alone shortcut must be named and refused — an unpicked member carries no label")
    assert "unit_completion._declared_open" in flat and "#1570" in flat, (
        "the shortcut is a defect this repo actually shipped; cite the read that fixed it")


def test_the_cited_membership_readers_still_exist():
    assert "def read(issue):" in FEATURES, (
        "features.read was renamed or removed — the scoping contract cites it as the single "
        "answer to 'is this goal in the unit'")
    assert "def _declared_open(" in UNIT_COMPLETION, (
        "unit_completion._declared_open was renamed or removed — the contract cites it as the read "
        "that measures a unit's open backlog without the label shortcut (#1570)")


def test_a_self_contradicting_issue_is_never_guessed_at():
    assert "AmbiguousUnit" in SDLC_GOAL, "the one state with no honest verdict must be handled"
    assert "class AmbiguousUnit(" in FEATURES, (
        "features.AmbiguousUnit was renamed or removed — skills/agrim-goal/SKILL.md names it")


# --------------------------------------------------------------------------- the two outcomes

def test_the_scoped_pick_delegates_to_the_loops_own_picker():
    """One implementation of the guarantee, not two. A second backlog read living in prose is
    exactly the thing that drifts from the picker it is supposed to agree with."""
    assert _flat('loop.py" next .sdlc --feature <unit> --session-pid') in _flat(SDLC_GOAL), (
        "the no-goal-named path must ask the loop's own scoped picker, with --session-pid, rather "
        "than describe a second way to read the backlog")


def test_the_drained_unit_stops_the_run_in_the_loops_own_words():
    """`nothing pickable` is not a phrase invented here — it is what `loop.py` already prints when
    it has a backlog it may not claim from. Reusing it means an operator reads one vocabulary."""
    assert "nothing pickable" in SDLC_GOAL
    assert "nothing pickable" in LOOP_PY, (
        "loop.py no longer prints 'nothing pickable' — skills/agrim-goal/SKILL.md tells the agent "
        "to reuse that exact phrase, so either restore it or agree a new one in BOTH places")
    flat = _flat(SDLC_GOAL)
    assert "NAME THE UNIT" in flat, (
        "a drained UNIT and a drained BOARD are different facts; the refusal has to name which")
    assert "does not widen the search" in flat and "does not fall back" in flat, (
        "the drained case is the one where exclusivity actually costs something; the refusal to "
        "widen has to be explicit")


def test_the_named_non_member_is_refused_outright():
    flat = _flat(SDLC_GOAL)
    assert "no worktree, no phases, no partial run" in flat, (
        "a refusal that still cuts a worktree or runs a phase is not a refusal")
    assert "no label, no comment, no park" in flat, (
        "nor is one that writes to the goal it just declined to run")
    assert "Offering to run it anyway" in flat, (
        "the near-miss — asking the user whether to run it just this once — must be closed too; it "
        "is the same wandering with a question mark in front of it")


def test_the_flag_never_writes_a_declaration():
    """A selection gate that can make a candidate pass its own filter is not a gate. Both halves of
    a declaration are written at pick, by the stamper and the labeller, under the model's rules."""
    flat = _flat(SDLC_GOAL)
    assert "never MAKES a goal a member" in flat
    assert "defeats the filter" in flat

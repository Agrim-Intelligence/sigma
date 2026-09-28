#!/usr/bin/env python3
"""One module constructs every status line Sigma emits (#2111): facts in, a contract-shaped
block out, for Blocks A (STATUS), B (EVENT) and C (DECISION) of `docs/output-contract.md`.

`docs/output-contract.md` is the specification. Until this module, it was prose a model was asked
to imitate, and imitation is all it was -- every status line the system ever produced was written
freehand. Here the line is CONSTRUCTED: both axes come from closed sets pinned to the contract's
own §2 tables (`tests/test_render.py::test_marker_set_matches_the_contract_table` and its phase
sibling), the ` · ` separator is written by this code and refused inside any field that sits beside
it, the goal ref is bold and linked when a URL exists, and the caps and the six-slot ceiling are
enforced rather than requested.

THE MODULE REFUSES TO CONSTRUCT A MALFORMED LINE RATHER THAN EMITTING ONE. A refusal is loud and
typed -- `REFUSED [<code>]  at <path>: <detail>` on stderr, exit 2, and NOTHING on stdout -- never a
silently degraded string. `REFUSAL_CODES` is the closed set of codes, pinned against every
`Refusal(...)` this file raises. That refusal is only definable because Block A's accepted fact set
is now closed (design `.sdlc/design/2030.md`, D-1 and D-8, both resolved by the human): six fields
-- marker, goal ref, phase token, title, description and a labelled PREDICTED model tier -- and no
cost field. Cost lives at the phase boundary in Block B, where `phase_report.py` measures it.

THE PREDICTED TIER CARRIES TWO LABELS AND THEY MUST NOT BE COLLAPSED.

  - `TIER_LABEL_PREDICTED` -- *predicted*, i.e. not observed. The tier is what a phase INTENDS to
    run on. The observed model is derived from the phase's own transcript at the boundary and is
    not knowable mid-phase (design PC-7; `phase_report.end_lines` is where the observed one
    appears).
  - `TIER_LABEL_AGENT_SOURCED` -- *agent-set*, i.e. not code-derived. `model_choice` sits in
    `actionlog.AGENT_KINDS`, so this value was written by an agent. Code-written facts (a goal was
    claimed, a gate ran) cannot be forged; this one can.

Each is ONE WORD as of #2113, and the same word `phase_report.py` already prints -- one vocabulary
for one fact, across every surface. See `TIER_LABEL_PREDICTED` for why the long form went.

They are two different claims. Rendering only the first says "a machine predicted this"; rendering
only the second says "an agent chose the model that ran". Either alone launders a guess into a
measurement, which is the single thing this whole mechanism exists to prevent -- the same defect
class as the `✅` on a verdict-free phase boundary that #2100 caught and removed. `check_tier_labels`
therefore inspects the field `tier_field` just built and refuses `tier-labels-collapsed` at RUNTIME
if an edit ever drops one; it is a real branch, not an `assert` (which `-O` strips), and it is its
own function so the control that proves it runs the same code path the renderer does.

WHERE THE LINE IS WRITTEN -- and the ceiling that comes with it (design D-5, the strongest doubt in
that design and unchallenged by three reviews). This module writes to STDOUT and nowhere else: no
file, no issue comment, no network call. Raw tool output is the only surface Sigma controls
on every host without a host-specific mechanism, and `docs/output-contract.md` is host-agnostic --
Cursor has no hooks at all. So the guarantee is exact and it is narrow: **the constructed line is
unforgeable in shape; it is not guaranteed to be what the user sees.** Anywhere the reply is read
rather than the raw tool output -- a dispatched subagent, a headless run, Cursor's chat -- it can
still be paraphrased or dropped. "No drift" is true of formatting and false of relay. Do not
describe this module as guaranteeing what reaches a console.

ZERO DEPENDENCIES, AND INVOKED BY SHELL-OUT, NEVER IMPORTED (design D-6). Two import bans meet
here: `log.py` must not import `actionlog.py`, and `tests/test_import_boundary.py` forbids
the private side importing `skills/`. Shelling out satisfies both, needs no `sys.path` surgery in any
caller, and matches how SKILL.md already invokes `phase_report.py`. stdlib only (North Star Rule
1); nothing here imports the private side (Rule 3). This module also loads NO sibling script -- not even
by `phase_report.py`'s `spec_from_file_location` idiom -- so it can be copied to any host or repo
and still run.

    render.py status   < facts.json      # Block A
    render.py event    < facts.json      # Block B
    render.py decision < facts.json      # Block C
    render.py status --json '{"headline": ..., "slots": [...], "tail": ...}'

EVERY KEY IS REQUIRED AND NO OTHER KEY IS ACCEPTED -- that is what "the fact set is closed" means
in code. Some required keys are NULLABLE, and the distinction matters: `url`, `model_tier` and
Block B's `title` and `next_action` may be `null`, which renders an explicit, honest absence (an
unlinked bold ref; `model tier unrecorded`; the bare ref with no title after it; a Block B that
simply ends after its fact).
A missing key is refused. You must SAY something about the tier and about the link; "I do not know"
is a legal thing to say, and forgetting is not. That is the same manner as `phase_report.py`'s
`cost: unavailable on this host (<reason>)` and `elapsed: unavailable`: state what is known, state
plainly where nothing is known, and never fabricate the difference.

TWO THINGS THE CONTRACT SAYS THAT THIS MODULE DELIBERATELY DOES NOT ENFORCE, both because
enforcing them would refuse the contract's OWN reference output or its own callers:

  - §3 asks a Block A description to end `→ <what unblocks next>` "whenever there is a next thing
    to name", and names the slots that have none: `✅` is finished, `⏸️` says why it is deferred,
    and `⏳` may name its blocker instead. Two of those appear arrow-less in the contract's own
    samples -- "seam analysis queued; blocked behind 1c (same file)" in §3's Block A sample, and
    "merged and recorded; no follow-up debt" inside §7, which the contract itself titles "Worked
    example (the reference output)". Which of the two exceptions applies is a judgement about the
    slot's meaning, so the arrow is not enforced here; #2124 settled the rule to say so, rather than
    leaving a guard that would refuse its own specification's reference output.
    `test_a_description_from_the_contract_s_own_reference_output_is_accepted` pulls those two
    strings OUT OF the contract file (never a line number, which goes stale) and runs them through
    this module, so the finding is executed rather than asserted in prose.
  - Nothing here checks a title "describes the goal, not the current step", that a description is
    "the current step", or that a Block B carries "exactly one substantive fact". Those are content
    rules no renderer can check, and a checkable proxy for them would be decoration.

WHAT *IS* ENFORCED AND IS WORTH KNOWING BEFORE YOU CALL IT: the §3 caps are hard. A title over
`TITLE_MAX_CHARS` (88, from §3) is REFUSED, not truncated -- truncating a title changes what it
says, and "Extract coherence validator and revert the 2019 schema" cut short is a different goal.
Block A's `title` field and Block B's go through the SAME `check_title`, so one cap governs both
and #2112's producer cannot satisfy one block while breaching the other.

That cap was 8 WORDS when this module shipped, and #2124 settled it to 88 characters because the
two halves of one pipeline were enforcing two different rules on one field: `phase_report.py`
fills this field from a raw issue title and elides it at 88 CHARACTERS, while this module refused
at 8 words -- which, measured over all 316 open issues in this repo on 2026-09-03, refused 75% of
them (and still 72% of them AFTER `clean_title` had run). 88 is not a looser 8: it is the
producer's own bound, so `clean_title` output is accepted here by construction and no refusal is
reachable between the two. A caller assembling a title by hand still gets the refusal, and it is a
handoff rather than a surprise -- the message carries the count and the cap.
"""
import json
import re
import sys

try:                    # portable output: matches every other script in this repo
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass


# --------------------------------------------------------------------------- the two axes

#: §2 Axis 1 -- the closed liveness-marker set, in the contract's own table order, as
#: `(glyph, state)`. A caller may pass EITHER form: the glyph (`"🔵"`) or the state name
#: (`"running"`). The state name exists so a caller never has to put an emoji into a shell
#: argument or a JSON file on a host whose console encoding is unknown; the glyph is what is
#: rendered, always, because §2's first character of a line is the glyph.
#:
#: `⏸️` is U+23F8 U+FE0F -- the base codepoint plus a variation selector, exactly as the contract
#: writes it. `_MARKER_CHARS` drops the selector so a bare `⏸` in a field is caught too.
#:
#: Pinned to `docs/output-contract.md`'s §2 Axis 1 table by
#: `tests/test_render.py::test_marker_set_matches_the_contract_table`: this module's vocabulary and
#: the document's cannot drift apart, in either direction.
MARKERS = (
    ("⚪", "queued"),
    ("🔵", "running"),
    ("🟣", "review"),
    ("🟢", "merging"),
    ("✅", "done"),
    ("🔴", "blocked"),
    ("⏸️", "parked"),
    ("⏳", "waiting"),
)

#: §2 Axis 2 -- the closed SDLC phase-token set as `(kind, token)`. The kinds are
#: `ledger.PHASE_KINDS` verbatim and the tokens are the contract's §2 table verbatim; both halves
#: are pinned (`test_phase_kinds_match_the_ledger_vocabulary`,
#: `test_phase_token_set_matches_the_contract_table`). Duplicated rather than imported from
#: `ledger.py` on purpose -- see this module's docstring on loading no sibling script at all, which
#: is what lets this file be shelled out from `skills/agrim-log/` without a path dance.
#:
#: A caller may pass either form, `"plan_review"` or `"P4 PLAN-REVIEW"`.
PHASE_TOKENS = (
    ("goal", "P1 GOAL"),
    ("research", "P2 RESEARCH"),
    ("plan", "P3 PLAN"),
    ("plan_review", "P4 PLAN-REVIEW"),
    ("implement", "P5 IMPLEMENT"),
    ("review", "P6 REVIEW"),
    ("retro", "P7 RETRO"),
)

#: `to_phase` values that are not phases. Both are ENUMERATED and typed; anything else that is not
#: a phase is still refused, so these are two named exits, not a soft mode. `from_phase` takes
#: NEITHER -- the phase a boundary just left is always known to whoever is at it.
#:
#: `"unknown"` (#2111) is for a caller that genuinely cannot name the phase that follows. #2112 was
#: its intended first consumer and, on review of the shipped line, is NOT: `phase_report.py end`
#: does know what the standing SDLC puts next, and printing "next phase unknown" there declared an
#: ignorance the very same line then resolved. It remains available and refuses nothing; it is
#: simply not what a phase boundary should say.
#:
#: `"last"` (#2112) is the honest end of the walk -- `P7 RETRO` has no successor, and inventing a
#: `P8` or falling back to `unknown` would both say something untrue about a list that has simply
#: ended. It renders `phase_report.py`'s own pre-#2112 wording, verbatim.
#:
#: WHAT DOES NOT LIVE HERE, AND WHY (#2112, review). An earlier draft of this change let
#: `from_phase` take `"unknown"` too, so `phase_report.py start` could be a Block B. Every start of
#: every phase then opened with `previous phase unknown (no verdict at this boundary)` -- 51
#: characters, in the line's most prominent position, carrying nothing. A phase START is not a
#: transition: it has no "from", and forcing one into `A → B` manufactures a permanent blank shaped
#: like a fact. The rule that came out of it is worth more than the field: **do not narrate the
#: not-knowing.** Absence is not a fact worth a clause -- the same principle that removed #2100's
#: `✅`, applied to a word instead of a glyph.
PHASE_UNKNOWN = "unknown"
UNKNOWN_NEXT_PHASE = "next phase unknown (no verdict at this boundary)"
PHASE_LAST = "last"
LAST_PHASE = "last phase"

#: The two, as `phase_token`'s `enumerated` mapping. Only `to_phase` is given it.
TO_PHASE_ENUMERATED = {PHASE_UNKNOWN: UNKNOWN_NEXT_PHASE, PHASE_LAST: LAST_PHASE}


# --------------------------------------------------------------------------- the tier's two labels

#: Label 1, from design D-1: the tier is what a phase INTENDS to run on. The observed model is
#: derived at the phase boundary from that phase's own transcript and is unknowable while the
#: phase is running, so a live slot line can only ever carry the prediction.
#:
#: ONE WORD, since #2113. It read `predicted, not observed` when #2111 shipped, and #2112 then
#: shortened its OWN copy for the free-text start banner -- leaving the system saying one fact two
#: ways, 53 characters in a Block A slot and 9 in the banner beside it. One renderer with two
#: caveat forms is exactly the inconsistency this module exists to prevent, so the short form wins
#: HERE too and there is now a single vocabulary. The dropped half is not lost: `not observed` is
#: what this comment is for, and `predicted` standing next to a tier value already makes the claim
#: a reader acts on. `check_tier_labels` still refuses at runtime if either token goes missing.
TIER_LABEL_PREDICTED = "predicted"

#: Label 2, from design D-2: `model_choice` is in `actionlog.AGENT_KINDS`. An agent wrote this
#: value; no code derived or verified it. `gate`, `verify_run`, `recorded` and `decompose_check`
#: are code-written and cannot be forged -- this one can, and a line must not present the two as
#: equally sourced. One word since #2113, for the reason above; `not code-derived` is this comment.
TIER_LABEL_AGENT_SOURCED = "agent-set"

#: The honest absence for a nullable `model_tier`. Not "unknown", not blank, and never a guess at
#: a default tier -- the same manner as `cost: unavailable on this host (<reason>)`.
TIER_UNRECORDED = "model tier unrecorded"


# --------------------------------------------------------------------------- shape constants

#: §3: "Separator between fields is ` · ` -- nothing else." This module writes it, and refuses it
#: inside any field that sits BESIDE another field on the same line, because a value carrying the
#: separator forges a field boundary that was never passed in. It is allowed in a field that has
#: no neighbour on its line (a `↳` description, a headline, a tail, any Block B field) -- there is
#: nothing there to forge.
SEPARATOR = " · "

#: §3 Block A: "Title ≤ 88 characters" / "Description ≤ 20 words" / "Max 6 slots." The description
#: cap is in words -- whitespace-separated tokens (`str.split()`), which is how a reader counts a
#: sentence they wrote. The TITLE cap is in CHARACTERS and its value is not a taste (#2124): the
#: title field is filled from an issue title, and the one thing in this repo that normalises those,
#: `phase_report.clean_title`, elides at 88 characters. Matching it makes the composition total --
#: every title the producer emits, this module accepts, with no refusal possible in between.
#: A word cap cannot do that: measured over all 316 open issues in this repo on 2026-09-03, 8 words
#: accepted 25% of raw titles and still only 28% after `clean_title`, while 88 characters accepts
#: 100% of `clean_title` output by construction.
#: `tests/test_render.py::test_the_title_cap_is_the_one_phase_report_already_elides_at` pins the two
#: constants together so they cannot drift apart again, which is the defect #2124 was filed for.
TITLE_MAX_CHARS = 88
DESCRIPTION_MAX_WORDS = 20
MAX_SLOTS = 6

#: §3 Block B: "≤ 3 sentences." §3 Block C: "Present ≤ 3 options." A decision with one option is
#: not a decision -- §3's own shape is `<Option A> / <Option B>`, so two is the floor.
EVENT_MAX_SENTENCES = 3
MIN_OPTIONS = 2
MAX_OPTIONS = 3

#: Options are joined with ` / ` (§3 Block C), so an option containing it forges an option.
OPTION_SEPARATOR = " / "

#: Block C's marker is fixed by §3 -- the block exists only for `blocked`. It is written by this
#: code, never passed in, which is why `marker` is not among `DECISION_FIELDS` and passing one is
#: refused as an unknown field.
DECISION_MARKER = "🔴"

_MARKER_BY_STATE = {state: glyph for glyph, state in MARKERS}
_MARKER_GLYPHS = frozenset(glyph for glyph, _ in MARKERS)
#: Every codepoint any marker is built from, minus the U+FE0F variation selector (written as an
#: escape because the literal is invisible in an editor), so a bare `⏸` is caught as readily
#: as `⏸️`.
_MARKER_CHARS = frozenset(ch for glyph, _ in MARKERS for ch in glyph if ch != "\ufe0f")

_TOKEN_BY_KIND = {kind: token for kind, token in PHASE_TOKENS}
_PHASE_TOKEN_SET = frozenset(token for _, token in PHASE_TOKENS)

#: C0 controls plus DEL. A field is one line; a newline in one would break the two-line-per-slot
#: shape and let a caller inject an entire extra slot through a title.
_CONTROL_CHARS = frozenset([chr(c) for c in range(0x20)] + ["\x7f"])

#: `#2628` (a GitHub issue) or a bare local goal stem -- the two things `phase_report.goal_ref`
#: produces and the only two that exist. Anything else could break the `**[...](...)** ` markdown.
_REF_RE = re.compile(r"\A(?:#[0-9]+|[A-Za-z0-9][A-Za-z0-9._-]*)\Z")

#: A sentence ends at `.`/`!`/`?` followed by whitespace or the end of the string. `$0.31`,
#: `claude-sonnet-5` and `.sdlc/design/2030.md` are therefore not sentence ends, which is what
#: makes this usable on the exact Block B lines §7 gives as reference output.
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|\Z)")


# --------------------------------------------------------------------------- the closed fact sets

#: Block A, per slot. SEVEN keys expressing the design's SIX fields: `ref` and `url` together are
#: the one "goal ref" field (§3: bold and linked when a URL exists, bold-only when none does).
#: Every key is required. `url` and `model_tier` are the two that may be `null`.
SLOT_FIELDS = ("marker", "ref", "url", "phase", "title", "description", "model_tier")
SLOT_NULLABLE = frozenset({"url", "model_tier"})

#: Block A, top level. §3: a lead line, the slots, and "Tail is exactly one line".
STATUS_FIELDS = ("headline", "slots", "tail")

#: Block B. §3's shape is
#: `**<ref> <title>** P<n> A → P<n> B — <artifact link>, <the one substantive fact>. <Next action>.`
#: `artifact` is nullable because not every boundary produces one; `url` as everywhere else.
#:
#: `title` is nullable and was added by #2112, whose producer (`phase_report.py`) has carried a
#: goal title on its banner since #2100 -- a bare `#1983` names the goal only to a reader who
#: already knows what 1983 is, which is the legibility gap #2100 was filed for and which §3's
#: own machine-half sample shows closed. Nullable because `resolve_title` returns "" when no source
#: ON THIS MACHINE knows it, and §3 names that absence as the honest degradation rather than a cue
#: to fetch one; `null` renders the bare ref, and the §7 reference output is what comes out.
#:
#: `next_action` is nullable for the same reason and by the same ruling (#2112, review): a machine
#: emitting a Block B at a phase boundary has no next action of its own, and the honest output for
#: something you do not have is silence, not a sentence about not having it.
EVENT_FIELDS = ("ref", "url", "title", "from_phase", "to_phase", "artifact", "fact", "next_action")
EVENT_NULLABLE = frozenset({"url", "artifact", "title", "next_action"})

#: Block C. No `marker`: §3 fixes it at `🔴` and this code writes it.
DECISION_FIELDS = ("ref", "url", "phase", "blocked", "tradeoff", "options", "recommendation")
DECISION_NULLABLE = frozenset({"url"})

#: Keys a caller may plausibly reach for that are DECLINED BY RULING rather than merely unknown.
#: Each gets its own refusal naming the ruling, because "unknown field 'cost'" would read as an
#: oversight in this module when it is in fact a decision taken deliberately and by a human.
DECLINED_SLOT_FIELDS = {
    "cost": "Block A carries no cost field (design D-8, resolved by the human): cost is resolved "
            "post-hoc and reads absent for 438 of 465 goals, so it lives at the phase boundary in "
            "Block B, where phase_report.py measures it",
    "cost_usd": "see 'cost' -- Block A carries no cost field (design D-8)",
    "spend": "see 'cost' -- Block A carries no cost field (design D-8)",
    "tokens": "a token count is a measurement taken at the phase boundary; it belongs to Block B, "
              "not to a live slot line (design D-8's same ground)",
    "observed_model": "the observed model is derived from a phase's own transcript AT THE BOUNDARY "
                      "and is not knowable while the phase runs (design PC-7); a live slot line "
                      "carries only the labelled prediction, in 'model_tier'",
    "model": "ambiguous between the predicted tier and the observed model, which is exactly the "
             "collapse this module exists to prevent -- pass 'model_tier', which renders labelled "
             "as a prediction and as agent-set",
}

#: Every refusal code this module can raise, closed.
#: `test_every_refusal_code_this_module_raises_is_declared` walks
#: this file's AST and pins the two together, so a new refusal cannot be added without naming it
#: here and a code cannot be renamed in one place only.
REFUSAL_CODES = frozenset({
    "unknown-block",
    "not-an-object",
    "missing-field",
    "unknown-field",
    "declined-field",
    "field-not-a-string",
    "field-empty",
    "field-not-one-line",
    "marker-glyph-in-field",
    "separator-in-field",
    "bad-marker",
    "bad-phase",
    "bad-ref",
    "bad-url",
    "bad-slot-count",
    "slots-not-a-list",
    "title-too-long",
    "description-too-long",
    "too-many-sentences",
    "options-not-a-list",
    "bad-option-count",
    "recommendation-not-an-option",
    "tier-labels-collapsed",
})


class Refusal(Exception):
    """A typed refusal to construct a line. `code` is drawn from `REFUSAL_CODES`, `where` is the
    JSON path of the offending value so the caller can fix it without guessing, and `detail` says
    what would have been wrong with the line. Never a degraded string: the caller gets an empty
    stdout and a non-zero exit."""

    def __init__(self, code, detail, where=""):
        self.code = code
        self.detail = detail
        self.where = where
        super().__init__(self.message())

    def message(self):
        at = " at {0}".format(self.where) if self.where else ""
        return "REFUSED [{0}]{1}: {2}".format(self.code, at, self.detail)


# --------------------------------------------------------------------------- field validation

def check_text(value, where, allow_separator=False):
    """One line of text, fit to sit in a constructed block. Returns the stripped value.

    Refuses a non-string, an empty/whitespace-only value, any C0 control or DEL (a newline in a
    title would inject a whole extra slot), any §2 marker codepoint (the liveness axis is written
    by this code and a glyph inside a field forges it -- the #2100 defect class), and, where the
    field has a neighbour on its line, the ` · ` separator itself."""
    if not isinstance(value, str):
        raise Refusal("field-not-a-string", "expected a string, got {0}".format(
            type(value).__name__), where)
    if not value.strip():
        raise Refusal("field-empty", "empty or whitespace-only", where)
    bad = sorted(_CONTROL_CHARS & set(value))
    if bad:
        raise Refusal("field-not-one-line", "contains a control character ({0}); a field is one "
                      "line".format(", ".join(repr(c) for c in bad)), where)
    glyphs = sorted(_MARKER_CHARS & set(value))
    if glyphs:
        raise Refusal("marker-glyph-in-field", "contains the liveness-marker codepoint {0}; the "
                      "§2 marker is written by this module and a glyph inside a field forges "
                      "it".format(" ".join(repr(g) for g in glyphs)), where)
    if not allow_separator and SEPARATOR in value:
        raise Refusal("separator-in-field", "contains the field separator {0!r}, which would forge "
                      "a field boundary on a line that has neighbours".format(SEPARATOR), where)
    return value.strip()


#: §3's caps refuse rather than truncate: a truncated line says something the caller did not.
#: `check_title` and `check_description` are separate functions carrying LITERAL refusal codes
#: rather than one helper taking the code as an argument, so that every code in this file is a
#: string constant at its own `raise` -- which is what makes `REFUSAL_CODES`' AST pin ungameable.
#: The unit is a parameter because the two caps count different things (§3: characters for the
#: title, words for the description) and a message naming the wrong unit misdirects the caller.
_TOO_LONG = ("{0} {1}, cap is {2} (docs/output-contract.md §3); shorten it -- this module will "
             "not truncate, because a truncated line says something the caller did not")


def check_title(text, where):
    """§3 Block A: "Title ≤ 88 characters". CHARACTERS, not words, and 88 is
    `phase_report.TITLE_MAX` -- see `TITLE_MAX_CHARS`. Refusal, not truncation, stands: the
    producer already elides with a visible `…`, so anything arriving here over the cap did not come
    through it and shortening it silently would put words in the caller's mouth."""
    count = len(text)
    if count > TITLE_MAX_CHARS:
        raise Refusal("title-too-long", _TOO_LONG.format(count, "characters", TITLE_MAX_CHARS),
                      where)
    return text


def check_description(text, where):
    """§3 Block A: "Description ≤ 20 words". Words here, unlike the title -- the description is
    prose the caller composes, and a word is how its author counts it."""
    count = len(text.split())
    if count > DESCRIPTION_MAX_WORDS:
        raise Refusal("description-too-long",
                      _TOO_LONG.format(count, "words", DESCRIPTION_MAX_WORDS), where)
    return text


def marker_glyph(value, where):
    """A §2 marker from either the glyph or the state name. Refuses anything else -- §2: "Never
    invent a marker."""
    if not isinstance(value, str):
        raise Refusal("field-not-a-string", "expected a string, got {0}".format(
            type(value).__name__), where)
    text = value.strip()
    if text in _MARKER_GLYPHS:
        return text
    if text in _MARKER_BY_STATE:
        return _MARKER_BY_STATE[text]
    raise Refusal("bad-marker", "{0!r} is not a §2 liveness marker; expected a glyph ({1}) or a "
                  "state name ({2})".format(
                      value, " ".join(sorted(_MARKER_GLYPHS)),
                      ", ".join(state for _, state in MARKERS)), where)


def phase_token(value, where, enumerated=None):
    """A §2 phase token from either the ledger kind (`plan_review`) or the token itself
    (`P4 PLAN-REVIEW`). `enumerated` is a mapping of the non-phase values this position also
    accepts, to the text each renders -- `TO_PHASE_ENUMERATED` for Block B's `to_phase`, and
    nothing at all anywhere else, so neither a Block A slot line nor a Block B's `from_phase` can
    go without a real phase. See `PHASE_UNKNOWN` for what is in that mapping and why."""
    if not isinstance(value, str):
        raise Refusal("field-not-a-string", "expected a string, got {0}".format(
            type(value).__name__), where)
    text = value.strip()
    if enumerated and text in enumerated:
        return enumerated[text]
    if text in _TOKEN_BY_KIND:
        return _TOKEN_BY_KIND[text]
    if text in _PHASE_TOKEN_SET:
        return text
    extra = " or {0}".format(", ".join(repr(k) for k in sorted(enumerated))) if enumerated else ""
    raise Refusal("bad-phase", "{0!r} is not a §2 phase token; expected a kind ({1}) or a token "
                  "({2}){3}".format(value, ", ".join(kind for kind, _ in PHASE_TOKENS),
                                    ", ".join(token for _, token in PHASE_TOKENS), extra), where)


def _at(prefix, key):
    """`slots[2]` + `title` -> `slots[2].title`, and `""` + `title` -> `title`. One helper so the
    JSON path in a refusal is right at every nesting depth."""
    return "{0}.{1}".format(prefix, key) if prefix else key


def goal_ref(ref, url, where="", title=None):
    """§3: "Goal ref is **bold** and a **markdown link** -- `**[#2628](url)**`, never bare `#123`.
    Bold-without-link only when no URL exists yet." A `null` url is that second case, stated
    rather than forgotten; a malformed one is refused, because a broken markdown link is a broken
    line, not a degraded one.

    `title` (Block B only -- Block A carries the title as its own ` · ` field) rides INSIDE the
    bold, beside the ref: `**#1983 Resolve reviewer independence per host**`, and linked,
    `**[#1983](url) Resolve reviewer independence per host**`. Two reasons for inside rather than
    beside. Block B's shape (§3) has no ` · ` in it at all -- that separator is a Block A rule --
    so a title added as a separate field would have to invent a delimiter Block B does not use;
    and `phase_report.py` has named the goal exactly this way, `#1983 <title>`, since #2100, so
    the pairing survives the reshaping verbatim. `None` renders NOTHING here, not an empty
    position: the §7 reference output is what a title-less Block B produces, byte for byte."""
    ref_at, url_at = _at(where, "ref"), _at(where, "url")
    text = check_text(ref, ref_at)
    if not _REF_RE.match(text):
        raise Refusal("bad-ref", "{0!r} is not a goal ref; expected '#<number>' for an issue or a "
                      "plain goal stem".format(ref), ref_at)
    if url is None:
        inner = text
    else:
        link = check_text(url, url_at, allow_separator=True)
        if not (link.startswith("http://") or link.startswith("https://")):
            raise Refusal("bad-url", "{0!r} is not an http(s) URL; pass null when no URL exists "
                          "yet".format(url), url_at)
        if any(ch in link for ch in " \t()<>"):
            raise Refusal("bad-url", "contains a character that would break the markdown link "
                          "(whitespace, parenthesis or angle bracket)", url_at)
        inner = "[{0}]({1})".format(text, link)
    if title is not None:
        title_at = _at(where, "title")
        # `allow_separator=True`, and ONLY here. `check_text` refuses ` · ` in a field that sits
        # beside another on its line, because there the separator forges a field boundary that was
        # never passed in. Block B has no ` · `-delimited field list to forge -- §3's shape for it
        # is a space, a `→` and a `—`, and its `fact` routinely CONTAINS ` · ` for that very reason
        # -- and this title sits inside the bold, which bounds it. Block A's title keeps the strict
        # check, because there ` · ` IS the field boundary.
        #
        # Measured, not assumed: of the 328 open issues in this repo on 2026-09-03, `clean_title`
        # output was accepted 326 times under the strict check and 328 under this one; the two
        # refusals were real live issues titled "L6 · Branch protection on the integration branch
        # (human-run)" and its sibling. Without this the phase boundary of any goal with a ` · ` in
        # its title falls back to the unconstructed banner on all fourteen calls, which is loud and
        # lossless but is not the totality #2124 set the cap to buy.
        inner = "{0} {1}".format(
            inner, check_title(check_text(title, title_at, allow_separator=True), title_at))
    return "**{0}**".format(inner)


def check_tier_labels(field, where):
    """The structural half of "the two labels must not be collapsed", as a REAL branch on the
    constructed field rather than an `assert` (`-O` strips asserts, and this guard has to hold in
    production). If an edit to `tier_field` ever drops one of the two labels, this refuses at
    runtime instead of quietly shipping a laundered claim.

    Its own function so the control that proves it can call the SAME code path the renderer runs,
    with no test-only parameter threaded through `tier_field` to make the guard reachable -- a
    guard only reachable through a seam the product never uses is decoration."""
    missing = [label for label in (TIER_LABEL_PREDICTED, TIER_LABEL_AGENT_SOURCED)
               if label not in field]
    if missing:
        raise Refusal("tier-labels-collapsed", "the predicted model tier must carry BOTH of its "
                      "labels and this field is missing {0}; rendering one alone launders an "
                      "agent-supplied guess into a measurement".format(
                          " and ".join(repr(m) for m in missing)), where)
    return field


def tier_field(tier, where):
    """The Block A model-tier field, carrying BOTH of its labels.

    `null` (or blank) renders `TIER_UNRECORDED` -- an explicit absence, never a default tier."""
    if tier is None or (isinstance(tier, str) and not tier.strip()):
        return TIER_UNRECORDED
    value = check_text(tier, where)
    return check_tier_labels(
        "model {0} ({1}; {2})".format(value, TIER_LABEL_PREDICTED, TIER_LABEL_AGENT_SOURCED),
        where)


def check_fields(facts, required, nullable, where, declined=None):
    """The closed-fact-set check, both directions: every required key present, and NO other key
    accepted. A `declined` key gets its own refusal naming the ruling that declined it."""
    if not isinstance(facts, dict):
        raise Refusal("not-an-object", "expected a JSON object, got {0}".format(
            type(facts).__name__), where or "<root>")
    for key in required:
        if key not in facts:
            raise Refusal("missing-field", "required key {0!r} is absent; every key in the closed "
                          "set must be present, and {1} may be null".format(
                              key, " / ".join(sorted(nullable)) or "none of them"), where)
    for key in sorted(facts):
        if key in required:
            continue
        if declined and key in declined:
            raise Refusal("declined-field", "{0!r} is not accepted here -- {1}".format(
                key, declined[key]), where)
        raise Refusal("unknown-field", "{0!r} is not in the closed fact set ({1}); the set is "
                      "closed on purpose, so an unrecognised key is refused rather than "
                      "ignored".format(key, ", ".join(required)), where)
    for key in required:
        if facts[key] is None and key not in nullable:
            raise Refusal("field-empty", "{0!r} may not be null (nullable here: {1})".format(
                key, ", ".join(sorted(nullable)) or "nothing"), where)


# --------------------------------------------------------------------------- Block A -- STATUS

def render_slot(slot, where):
    """§3 Block A's two lines for one slot: the title line carrying both axes, the goal ref, the
    title and the labelled tier, and the indented `↳` description line beneath it.

    `title` and `model_tier` sit beside neighbours on the title line, so the separator is refused
    inside them; `description` has the `↳` line to itself, so it is not."""
    check_fields(slot, SLOT_FIELDS, SLOT_NULLABLE, where, declined=DECLINED_SLOT_FIELDS)
    marker = marker_glyph(slot["marker"], _at(where, "marker"))
    ref = goal_ref(slot["ref"], slot["url"], where)
    token = phase_token(slot["phase"], _at(where, "phase"))
    title = check_title(check_text(slot["title"], _at(where, "title")), _at(where, "title"))
    description = check_description(
        check_text(slot["description"], _at(where, "description"), allow_separator=True),
        _at(where, "description"))
    tier = tier_field(slot["model_tier"], _at(where, "model_tier"))
    head = SEPARATOR.join([ref, token, title, tier])
    return ["* {0} {1}".format(marker, head), "  ↳ {0}".format(description)]


def render_status(facts):
    """Block A. §3: at most `MAX_SLOTS` slots -- "More than 6 means collapse, not scroll" -- and a
    tail that is "exactly one line". A block with no slots is refused: it states nothing, and §5's
    "End of turn with nothing in flight" trigger still has slots to show or it is not Block A."""
    check_fields(facts, STATUS_FIELDS, frozenset(), "")
    headline = check_text(facts["headline"], "headline", allow_separator=True)
    if not headline.endswith(":"):
        headline += ":"
    slots = facts["slots"]
    if not isinstance(slots, list):
        raise Refusal("slots-not-a-list", "expected a list of slots, got {0}".format(
            type(slots).__name__), "slots")
    if not slots or len(slots) > MAX_SLOTS:
        raise Refusal("bad-slot-count", "{0} slots; §3 allows 1 to {1} -- more than {1} means "
                      "collapse, not scroll".format(len(slots), MAX_SLOTS), "slots")
    tail = check_text(facts["tail"], "tail", allow_separator=True)
    lines = [headline, ""]
    for index, slot in enumerate(slots):
        lines.extend(render_slot(slot, "slots[{0}]".format(index)))
    lines.extend(["", tail])
    return "\n".join(lines)


# --------------------------------------------------------------------------- Block B -- EVENT

def render_event(facts):
    """Block B. §3: "One paragraph, no bullets, no headers. Must name the goal, the phase it just
    left, and the phase it enters", "≤ 3 sentences", and it carries NO §2 liveness marker -- there
    is no `marker` key here, and passing one is refused as an unknown field. That is #2100's
    finding kept: a boundary that receives no verdict must not stamp one.

    The final `.` on `fact` and on `next_action` is written here rather than required of the
    caller, so the sentence count is over the shape this module actually emits.

    `next_action` is NULLABLE (#2112, review), and that is the same ruling as `title`'s rather than
    a relaxation: state what you know, state plainly where you know nothing, and never manufacture
    the difference. §3 asks a Block B to end with what you are doing next -- a machine-emitted one
    at a phase boundary has no next action of its own, and the drafts that filled the slot anyway
    ("Running the phase", "The standing SDLC puts P3 PLAN after this one") were narration, in one
    case restating what the same line's `→` had already said. `null` writes nothing at all; a
    caller with a real next action still passes it and still gets §3's sentence."""
    check_fields(facts, EVENT_FIELDS, EVENT_NULLABLE, "")
    ref = goal_ref(facts["ref"], facts["url"], "", title=facts["title"])
    left = phase_token(facts["from_phase"], "from_phase")
    entered = phase_token(facts["to_phase"], "to_phase", enumerated=TO_PHASE_ENUMERATED)
    fact = check_text(facts["fact"], "fact", allow_separator=True).rstrip(".")
    body = fact
    if facts["artifact"] is not None:
        artifact = check_text(facts["artifact"], "artifact", allow_separator=True).rstrip(",")
        body = "{0}, {1}".format(artifact, fact)
    line = "{0} {1} → {2} — {3}.".format(ref, left, entered, body)
    if facts["next_action"] is not None:
        action = check_text(facts["next_action"], "next_action", allow_separator=True).rstrip(".")
        line = "{0} {1}.".format(line, action)
    sentences = len(_SENTENCE_END_RE.findall(line))
    if sentences > EVENT_MAX_SENTENCES:
        raise Refusal("too-many-sentences", "{0} sentences, cap is {1} (docs/output-contract.md "
                      "§3 Block B)".format(sentences, EVENT_MAX_SENTENCES), "fact/next_action")
    return line


# --------------------------------------------------------------------------- Block C -- DECISION

def render_decision(facts):
    """Block C. §3: "Present ≤ 3 options. Always carry a recommendation." The recommendation must
    be one of the options passed -- a decision block a human acts on that recommends something it
    did not offer is malformed, and it is the one thing here a machine can actually check."""
    check_fields(facts, DECISION_FIELDS, DECISION_NULLABLE, "")
    ref = goal_ref(facts["ref"], facts["url"], "")
    token = phase_token(facts["phase"], "phase")
    blocked = check_text(facts["blocked"], "blocked")
    tradeoff = check_text(facts["tradeoff"], "tradeoff", allow_separator=True)
    options = facts["options"]
    if not isinstance(options, list):
        raise Refusal("options-not-a-list", "expected a list of options, got {0}".format(
            type(options).__name__), "options")
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise Refusal("bad-option-count", "{0} options; §3 allows {1} to {2} -- one option is not "
                      "a decision".format(len(options), MIN_OPTIONS, MAX_OPTIONS), "options")
    rendered = []
    for index, option in enumerate(options):
        text = check_text(option, "options[{0}]".format(index), allow_separator=True)
        if OPTION_SEPARATOR in text:
            raise Refusal("separator-in-field", "contains the option separator {0!r}, which would "
                          "forge an option".format(OPTION_SEPARATOR), "options[{0}]".format(index))
        rendered.append(text)
    recommendation = check_text(facts["recommendation"], "recommendation", allow_separator=True)
    if recommendation not in rendered:
        raise Refusal("recommendation-not-an-option", "{0!r} is not one of the options offered "
                      "({1}); a decision block must recommend something it presented".format(
                          recommendation, ", ".join(repr(o) for o in rendered)), "recommendation")
    head = SEPARATOR.join([ref, token, blocked])
    return "\n".join([
        "{0} {1}".format(DECISION_MARKER, head),
        "  ↳ {0}".format(tradeoff),
        "{0}. Recommendation: {1}.".format(OPTION_SEPARATOR.join(rendered), recommendation),
    ])


# --------------------------------------------------------------------------- CLI

BLOCKS = (
    ("status", render_status),
    ("event", render_event),
    ("decision", render_decision),
)

USAGE = ("usage: render.py status|event|decision [--json '<facts>']\n"
         "       facts are a JSON object, read from stdin when --json is absent.\n"
         "       stdout carries the block; a refusal goes to stderr with exit 2 and stdout stays "
         "empty.")


def main(argv):
    """Exit 0 with the block on stdout, or exit 2 with a typed refusal on stderr and stdout
    UNTOUCHED. Nothing is printed until the whole block is constructed, so a caller piping stdout
    into a console can never receive half a line."""
    args = argv[1:]
    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if not args:
        print(USAGE, file=sys.stderr)
        return 2
    verb = args[0]
    renderer = dict(BLOCKS).get(verb)
    if renderer is None:
        print("render.py: {0}".format(Refusal(
            "unknown-block", "{0!r} is not a block; expected {1}".format(
                verb, ", ".join(name for name, _ in BLOCKS))).message()), file=sys.stderr)
        return 2
    rest = args[1:]
    if rest and rest[0] == "--json":
        if len(rest) < 2:
            print("render.py: --json needs a value\n" + USAGE, file=sys.stderr)
            return 2
        raw = rest[1]
    elif rest and rest[0].startswith("--json="):
        raw = rest[0][len("--json="):]
    elif rest:
        print("render.py: unexpected argument {0!r}\n{1}".format(rest[0], USAGE), file=sys.stderr)
        return 2
    else:
        raw = sys.stdin.read()
    try:
        facts = json.loads(raw)
    except ValueError as exc:
        print("render.py: {0}".format(Refusal(
            "not-an-object", "facts are not valid JSON ({0})".format(exc)).message()),
            file=sys.stderr)
        return 2
    try:
        block = renderer(facts)
    except Refusal as refusal:
        print("render.py: {0}".format(refusal.message()), file=sys.stderr)
        return 2
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

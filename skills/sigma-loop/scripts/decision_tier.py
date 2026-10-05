#!/usr/bin/env python3
"""Decision-tier categorizer -- classify a decision's text into the level of seniority it needs,
per #818's L0/L1/L2 escalation pyramid ("decisions form a pyramid, and the product's job is to
filter which decision reaches which level"). This is the sigma-core slice of that story
(#951/#952): the pure classifier. #953 wired it into `loop.py`'s single park chokepoint
(`_record()`); #1185 widened WHICH parks reach it (see that module's `_DECISION_TIER_REASON_CLASSES`)
after finding the #953 wiring gated on `reason_class == "needs_decision"` alone, which is
structurally unreachable for the free-text decision prose this vocabulary was written for -- every
agent-typed `loop.py record ... parked "<free text>"` falls into `unknown`, not `needs_decision`.

ADVISORY ONLY -- STATED PLAINLY, NOT LEFT IMPLICIT (#1185). A resolved tier changes what a human
SEES (the ledger `park` event, the `- tier:` line in `review-queue.md`, the `[decision tier: <t>]`
suffix on the park comment) and nothing else: `_offboard()` runs the identical label swap, board
column, and (lack of) notification regardless of tier, and the loop's own control flow never reads
a tier back. That is a deliberate, in-scope call for #1185, not an oversight left over from #953:
the bottom-up half of #818's own routing story -- actually suppressing an `autonomous`-tier park
instead of still queuing it for a human, i.e. making the tier DO something beyond labeling -- is
real, wanted, and explicitly tracked as its own separate follow-up, not this issue. Building it here
too would smuggle a second, larger, unreviewed feature into a "give the existing classifier a real
consumer" fix. Until that follow-up ships, treat every tier value as a triage hint for whoever reads
the queue, never as a routing decision the loop has already made for you.

SAME SHAPE, FOURTH TIME. `skills/sigma-model/scripts/predict.py` ships this exact architecture for
a different axis (model tier, not decision tier): an ordered list of `(tier, regex)` pairs,
`\\b`-anchored, high-stakes-first, first-`re.search`-match-wins, a `resolve(text, sdlc_dir)`
wrapper gated on an opt-in `config.json` flag, upward conflict resolution (a mixed signal never
silently resolves to the lower-stakes tier). It is already independently cloned twice more in this
codebase -- `sigma-loop/scripts/loop.py`'s `_reason_class` (plain substring match, a park-detail
classifier) and `sigma-loop/scripts/goal_size.py`'s `classify` (numeric-threshold based) -- as a
deliberate, named convention. `goal_size.py`'s own docstring states outright why a shared helper
was never factored out: `loop.py`'s own module loader (`_load()`, loop.py:26-28) only resolves
scripts from its OWN directory, so cross-skill-directory sharing isn't the established pattern
here. This module clones the same proven shape a fourth time, for a fourth, genuinely different
axis -- not a refactor target, a sibling.

TIER NAMES, AND WHY. Three GitHub-safe, unambiguous string constants: `autonomous` (handle it,
never surface it -- #818's L2), `escalate_l1` (send it to a mid-senior engineer -- #818's L1),
`escalate_l0` (send it to CEO/CTO -- #818's L0). Named directly after #818's own vocabulary rather
than an "intuitive" low-to-high scheme, so a reader who already knows the parent story maps them on
sight. NOTE THE INVERSION this carries over from #818 unmodified: L0 is the TOP of the pyramid
(most senior, least common) and L2 is the BOTTOM (most junior, most common) -- `escalate_l0` is the
tier this module is MOST conservative about assigning, not the least. Kept exactly as #818 named it
rather than "fixed", because consistency with the parent story's already-established vocabulary
matters more here than a scheme that reads as intuitively ascending.

VOCABULARY PROVENANCE -- NO CALIBRATION CORPUS. `predict.py` had 269 real issues to fit its
thresholds against (goal_size.py's own docstring records the same discipline for ITS thresholds);
#818/#952 explicitly have none -- there is no corpus of real "decision text" yet, because nothing
has ever recorded a decision's category before this module exists. `_PATTERNS` below is therefore
judgment, not measurement: a genuinely reasoned starting vocabulary, grounded in the source text
that DOES exist --
  - `escalate_l0`'s six terms (`revert`, `pricing`, `contract`, `security incident`,
    `irreversible`, `budget`) are #952's own worked examples of what "strategy-nature, rare" (#818)
    concretely looks like in a park detail, taken verbatim.
  - `autonomous`'s three anchor terms (`which library`, `naming`, `formatting`) are likewise
    #952's own worked examples of "trivial/mechanical questions [that] must never surface" (#818).
    The remaining `autonomous` terms (`typo`, `renam`, `reformat`, `whitespace`, `indentation`,
    `spelling`, `docstring`, `lint`, `dead code`) are NOT independently re-derived -- they are
    borrowed from `predict.py`'s own already-reasoned haiku (trivial) list on purpose: the trivial
    end of "decision text" and "goal text" overlap by nature (both describe mechanical, low-
    judgment work), so reusing an adjacent axis's already-defensible trivial vocabulary is a
    sounder starting point than inventing a parallel one from nothing. `escalate_l1` has no such
    adjacent-axis list to borrow from (predict.py has no "medium stakes" tier -- sonnet is a bare
    default, not a positive signal list) and is reasoned directly from #818's own word for this
    tier, "tricky", plus its obvious synonyms (ambiguous, trade-off, edge case, judgment call,
    an explicit architecture/design decision, not being sure which option to take).
This is a heuristic with a known ceiling, calibrated by judgment and #818/#952's own text, not a
corpus -- exactly the honesty `predict.py`'s own docstring states about itself ("ponytail:
heuristic with a known ceiling"). It is expected to need real tuning once real park data exists to
tune it against; nothing here should be read as a settled vocabulary.

THE DEFAULT TIER, AND WHY IT IS NOT `autonomous`. #818 states "trivial/mechanical questions must
NEVER surface" -- but #952 itself names that the AGGRESSIVE claim, not the safe default, for
exactly the reason `predict.py`'s own module docstring gives for its upward-conflict bias:
escalating one tier too high costs a human a couple of minutes reading something they didn't
strictly need to; failing to escalate a genuinely strategic call is the expensive failure mode.
Defaulting unmatched text to `autonomous` would silently hand every decision this vocabulary
doesn't recognize straight to the loop with zero human in the loop at all -- the single worst
place to be wrong, given #818's own escalation chain lets a human bump a wrongly-low call back UP
(the dashboard "carries the escalation chain" per #818), but nothing bumps a call back down once
the loop has already acted on it autonomously. Defaulting to `escalate_l0` (the opposite extreme)
would be safe in the same narrow sense but defeats #818's own stated purpose -- "filter which
decision reaches which level" -- by routing every unrecognized decision straight to the CEO/CTO,
which is exactly the noise the categorizer exists to prevent. `_DEFAULT = "escalate_l1"` is the
middle ground: unmatched text always reaches a human, at the lowest tier that still guarantees one,
and that human's own judgment (not a keyword list) decides whether it needs to go further up #818's
chain.

CLI mirrors `predict.py main()`'s own `resolve`/`why` subcommand shape, minus a `resolve-step`
analog: `predict.py` has two axes (model tier, effort) and two granularities (per-goal ceiling,
per-step), so a mechanical step inside a hard goal can run cheaper than the goal's own ceiling.
There is no equivalent second granularity here -- a park `detail` is one decision, classified once,
not decomposed into steps -- so only `resolve` (goal-shaped: config-gated tier or "off") and `why`
(ungated: tier plus the literal signal, for a human or a future caller to audit) are needed."""
import re
import sys
import pathlib

# Ordered highest-seniority-needed -> lowest; first tier whose signal appears wins, so a strategic
# signal beats a trivial one on mixed text (see test_conflict_resolves_upward_to_l0). Every bare
# term is `\b`-anchored at its START ONLY (predict.py's own convention) so a suffixed inflection
# ("budgetary", "renaming") still counts, but a PREFIXED form starting mid-word never does --
# "unambiguous" cannot match `ambiguous` because there is no word boundary between "un" and
# "ambiguous" (see test_no_false_trigger_on_meaning_inverting_prefix). `contract` is the one
# deliberate exception: it carries its OWN trailing `\b` because "contractor" extends it with a
# real, unrelated suffix ("-or", a different noun entirely) rather than an on-topic inflection --
# see test_no_false_trigger_on_contractor_substring for the guard and why "subcontractor" needed
# no separate fix (already blocked by the ordinary leading boundary).
_PATTERNS = [
    ("escalate_l0", r"\b(?:irreversible|budget|pricing|security incident|contract\b|revert)"),
    ("escalate_l1", r"\b(?:tricky|ambiguous|trade-?off|edge case|architecture decision|"
                     r"design decision|which approach|not sure which|unclear requirement|"
                     r"judgment call)"),
    ("autonomous", r"\b(?:typo|renam|naming|reformat|formatting|whitespace|indentation|spelling|"
                    r"docstring|lint|dead code|which variable name|"
                    r"which of these two equivalent|which library)"),
]
_DEFAULT = "escalate_l1"   # the safe middle -- see module docstring's "THE DEFAULT TIER" section


def classify(detail_text):
    """Return `(tier, signal)` for one decision's text (typically a park `detail`), mirroring
    `predict.py`'s `predict_with_reason()` contract exactly: `signal` is the LITERAL matched
    substring (`match.group(0)`), never a paraphrase of the rule, so a human (or a future caller)
    can audit the classification against the source text themselves. `signal` is `None` only when
    nothing matched and `_DEFAULT` applies -- manufacturing a reason for a defaulted tier would be
    worse than recording none.

    `_PATTERNS` is ordered highest-tier-first, so on mixed text ("fix the typo in the budget
    forecast" -> escalate_l0) the signal returned is the one that WON; reporting a losing signal
    beside the winning tier would be actively misleading, which is why a test pins exactly that
    case (test_conflict_resolves_upward_to_l0)."""
    text = (detail_text or "").lower()
    for tier, pat in _PATTERNS:
        m = re.search(pat, text)
        if m:
            return tier, m.group(0)
    return _DEFAULT, None


def resolve(detail_text, sdlc_dir=".sdlc"):
    """The tier for this decision text, honoring config. Returns a tier only when the `decision_tier`
    top-level config key is `"auto"` (see `test_config_key_ships_in_the_template_with_the_documented_off_default`
    -- shipped in the scaffolded template as `"off"`, #1185); otherwise `None`, so `loop.py`'s
    `_record()` (the caller, wired by #953, widened by #1185) stays completely inert until someone
    deliberately opts in, exactly like `predict.resolve()`'s own `model_selection` gate."""
    import json
    try:
        cfg = json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())
    except Exception:
        cfg = {}
    if (cfg.get("decision_tier") or "off") != "auto":
        return None
    return classify(detail_text)[0]


def _read(arg):
    p = pathlib.Path(arg)
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else arg


USAGE = ("usage: decision_tier.py '<detail>' | resolve '<detail>' [sdlc_dir] | "
         "why '<detail>'")


def main(argv):
    # resolve: config-gated tier for a future caller ("off" when decision_tier isn't auto).
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 3 and argv[1] == "resolve":
        print(resolve(_read(argv[2]), argv[3] if len(argv) > 3 else ".sdlc") or "off")
        return 0
    # why: the tier AND the literal text that triggered it. Ungated by `decision_tier` on purpose
    # -- this answers "what would be chosen and why", worth asking whether or not auto is on, and
    # it writes nothing, so an exploratory call has no side effect (mirrors predict.py's `why`).
    if len(argv) >= 3 and argv[1] == "why":
        tier, signal = classify(_read(argv[2]))
        print(f"tier={tier} signal={signal}" if signal else f"tier={tier} signal=")
        return 0
    if len(argv) < 2 or argv[1] in ("resolve", "why"):
        print(USAGE, file=sys.stderr)
        return 2
    print(classify(_read(argv[1]))[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

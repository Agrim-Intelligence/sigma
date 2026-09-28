#!/usr/bin/env python3
"""What to DO about a declared unit, at the moment a goal is picked (#1468, epic #1464, story #1427).

`features.py` is the read half: it answers WHICH unit an issue declares and says so in five
verdicts. It mutates nothing, deliberately -- its own docstring hands the deciding to this module.
This is that decision, taken on one verdict:

    features.BODY_ONLY -- the BODY declares a unit and the LABEL is missing.

THE ACTION IS SPLIT IN TWO, AND THE SPLIT IS THE WHOLE POINT OF THIS MODULE:

    the `feature:<name>` label EXISTS, this issue lacks it  ->  ATTACH it        (automatic)
    the label DOES NOT EXIST                                ->  REFUSE the pick  (flagged; a human acts)

Attaching is additive, reversible, and adjudicates nothing: a human wrote the unit in the body, and
the label is the mechanical projection of that declaration. CREATING a label is a different kind of
act -- it mutates the REPOSITORY's namespace rather than one issue, it is permanent (feature labels
are never deleted, `open: false` is how a finished unit is marked), and a single typo in a body
marker would mint junk that outlives the unit. That is precisely the class `docs/label-model.md`
§5's FLAGGED tier exists for, and carving an exception around it would reintroduce the untrusted
automatic label mutation that tiering was learned from -- on a repo where a live census once found
126 of 605 issues in a state the system could not name.

The refusal costs nothing in practice: the first label of a unit is created by hand anyway, so a
missing label means either a typo or a unit nobody has opened -- and both genuinely want a human.

WHY THE NEVER-CREATE GUARANTEE IS ENFORCED AT A CHOKEPOINT, AND WHICH CALL SITE ACTUALLY JUSTIFIES
IT. `creates_a_feature_label` below is read by `sources.GitHubSource._run` -- the single place every
`gh` call in that class passes through (`_raw_run` has exactly two callers, both inside `_run`) --
rather than by each `gh label create` call site. THE SITE THAT MAKES THE CHOKEPOINT LOAD-BEARING IS
`triage._ensure_arbitrary_labels`: it lives in ANOTHER MODULE and reaches for `source._run` itself,
so a guard written into `sources.py`'s own two sites would leak there, silently. (An earlier version
of this paragraph named `create_dependency` as the justification. That was wrong and is corrected
here rather than quietly dropped: `create_dependency` is one of the enumerated sites, so a
per-call-site rule WOULD have covered it -- the counterfactual was measured, and only the triage
site survives it. A rationale naming the wrong cause misleads the next author.)
`create_dependency` is still the site where this matters TODAY rather than only under L3, because
`handoff.py` passes a free-text `--label` list straight into it: `handoff track --label feature:voice`
reaches its per-label `gh label create` loop right now, and did mint the label before this.

THE GUARANTEE IS CLI-VERB-SHAPED, AND SAYING SO IS THE POINT. What is refused is the `label create`
verb. Four other routes could still mint a label and are NOT covered -- `gh label clone` (which
copies EVERY label of another repo, `feature:*` included), `gh label edit --name`, `gh label delete`
plus a recreate, and the REST form `gh api -X POST repos/{o}/{r}/labels`. None appears anywhere in
this tree, so this is a bound on the claim, not a hole in it; it is written down so a future author
does not read "never creates one" as wider than the code makes it.

WHY A REFUSAL IS AN OVERLAY UNDER A LABEL OF ITS OWN, NOT A PARK, NOT SILENCE, AND NOT
`sdlc:blocked`. Four options, and the last is the one this ships:

  - PARK (`sdlc:parked`) gives up `sdlc:goal` -- the MEMBERSHIP every sweep, census, mirror and
    reclaim path queries by -- and `docs/label-model.md` §3 is explicit that only a human undoes
    one. That would demand TWO human gestures (create the label, then `/agrim-unpark`) where one
    suffices, for a condition that is very often momentary. Rejected.
  - SILENCE (skip the pick, say nothing durable) was the first version of this module, and it was
    wrong for a reason worth recording: pickability then had a second axis the LABEL SET could not
    express, so `sources.not_eligible_labels`' "pickable iff it carries goal_label and none of
    these" became false, and `/agrim-triage` went on reporting a permanently-refused goal as READY TO
    PICK -- the exact failure #1393 was filed about. It also left the goal in the pickable queue, so
    `next_batch` re-evaluated it from scratch on every slot: measured at 2 `gh issue view` calls per
    slot per poisoned issue, unbounded in time.
  - REUSING `sdlc:blocked` was tried and is WRONG, and the measurement is worth keeping because the
    shape is right and only the label was not. That label conflates a STATE (member, unpickable,
    self-healing) with a REASON (a blocker issue closes), and `auto_unpark.compute_unpark_actions`
    reasonably assumes it owns every instance: it strips `sdlc:blocked` from any goal whose body
    names a `blocked by #N` whose refs have all closed, whoever set it and whyever. A goal held for
    a missing label that ALSO names an already-closed dependency therefore FLAPS -- that sweep
    clears it, this one re-sets it, forever, at two label swaps, two board moves and one FALSE
    "blocker closed" comment per cycle. Not exotic: `compile_plan`, `handoff` and `triage` all write
    that marker into goal bodies themselves, `auto_unpark.mode` is "on" in both live adopter
    configs, and six open issues on this repo's own board -- including THIS ONE, which carries a
    closed `Blocked by: #1465` -- would have fired. It is strictly worse than silence: silence cost
    two reads per slot and wrote nothing; that writes.

  - OVERLAY UNDER ITS OWN LABEL -- what ships. `sdlc:goal` KEPT, `sdlc:needs-label` added, board
    card moved. It is the shape `docs/label-model.md` §2 already defines as "visible to everything
    and picked by nothing", additive and reversible -- the same trust tier as the attach and the
    comment -- with a reason no other machinery claims. `auto_unpark`, `compute_blocking_actions`,
    `blockers._act` and `unpark.brief` all stay correct with no change to any of them.

AND IT STILL SELF-HEALS, which is the property the park was rejected for lacking. `resume_needs_label`
(below) removes the overlay the moment the missing label exists, so the human still performs exactly
ONE gesture -- create the label.

THE LABEL IS ALSO THE ATTRIBUTION, and that is not a convenience, it is the correctness property. An
earlier version keyed the resume off a LEDGER line, which made the state's own recovery depend on an
OPTIONAL feature: `ledger.enabled` ships FALSE in `/agrim-init`'s template, so on a stock adopter the
overlay landed, nothing was recorded, and the goal sat permanently unpickable behind a comment
promising it would resume by itself -- converging on exactly the "issue nobody can find" this module
says it never converges on. Asking GitHub which issues carry the label cannot fail that way: the
state and the record of who set it are the SAME OBJECT, readable by any machine and any clone. It
also answers the question the ledger answered badly -- "is this OUR block?" -- without needing to,
because no other producer writes this label at all.

FAILURE DIRECTIONS, each chosen rather than fallen into:

  - the issue could not be READ            -> PROCEED (`UNREADABLE`). A read we could not make is
                                              not evidence of anything; degrading to today's
                                              single-base behaviour is the direction that cannot
                                              stop a queue. `unit` IS THEREFORE None WITHOUT MEANING
                                              "no unit", and #1567 is what that distinction cost:
                                              `loop._next` handed the None to gates that read it as
                                              an answer while base resolution -- a different reader
                                              on a different transport -- still saw the unit. The
                                              outcome is what makes the two tellable apart, and
                                              `loop._unit_at_pick` re-resolves on this row alone.
  - the label lookup could not ANSWER      -> REFUSE, do NOT flag, do NOT overlay
                                              (`REFUSED_UNRESOLVED`). A network blip is not evidence
                                              the label is absent, so it must not summon a human or
                                              write a state -- but it also must not be read as
                                              "exists" and let the goal start against the wrong base.
  - the attach WRITE did not land          -> REFUSE, do NOT flag, do NOT overlay
                                              (`REFUSED_WRITE_FAILED`). SAME rule as the line above,
                                              and it is stated twice on purpose: these are the two
                                              TRANSIENT directions, and an earlier version enforced
                                              the no-flag rule on one of them and left the other
                                              unpinned. The comment it would have posted is the
                                              missing-label text, which here would be FALSE -- the
                                              label exists; the write failed.
  - the issue CONTRADICTS ITSELF           -> REFUSE, flag, overlay (`REFUSED_AMBIGUOUS`).
                                              `features.read` raises `AmbiguousUnit` here and its
                                              docstring puts the obligation on every caller: catch it
                                              PER ISSUE. A raise escaping the pick path turns one
                                              hand-edited issue into a total outage of the queue, and
                                              a stray hand-added label is an observed failure mode on
                                              this repo.
  - both halves declare and DISAGREE       -> PROCEED, write no label, but SAY SO
                                              (`CONFLICT_DECLARED`). Writing nothing is right --
                                              swapping the label removes something a human put there,
                                              and this level was not asked to adjudicate. Saying
                                              nothing was a correctness bug: the goal proceeds while
                                              base resolution (#1467) reads the BODY and everything
                                              else reads the LABEL, so the worktree is cut from one
                                              unit and the work recorded under the other, silently.
                                              Folding it into `NOTHING_TO_DO` also made a conflict
                                              indistinguishable from `agree`, so the level that owns
                                              unit ownership (L5) could not be handed the deferral at
                                              all. Deferring a decision means handing the next level
                                              something to decide on.

Every refusal converges on redoing a little work next pass, or on a named, queryable state that
self-heals; none of them converges on an issue nobody can find. That is this repo's governing
principle for label writes, applied here unchanged.

IS ANY OF THE ABOVE GATED ON `.sdlc/features/` EXISTING, THE WAY `feature_sync` AND `cross_repo`
ARE? NO -- AND THAT IS A DECISION, NOT AN OMISSION (#1543). Both of those open with
`feature_registry.registry_dir(sdlc_dir).is_dir()` and answer `not-adopted` before spending
anything; nothing in this module ever makes that call -- `test_the_label_half_never_reads_the_
features_directory` (tests/test_feature_labels.py) pins the absence structurally, not as a claim
this docstring alone stands behind. Three shapes were weighed, not just the one that shipped:

  - GATE ON THE DIRECTORY, matching the other two. Rejected: `docs/branching-model.md` §3's marker
    contract already requires a bare, unindented, unfenced `Feature: <name>` line, so nobody
    reaches `BODY_ONLY` by accident -- the directory would not be screening out an accidental
    declaration, only whether a SEPARATE, optional artifact happens to exist yet. Base resolution
    already gates on that same directory (§6 line 0), so a repository that writes the marker before
    running `mkdir -p .sdlc/features` is cut from `main` regardless of what this module does; gating
    the attach too would make it depend on an artifact the attach itself has no use for -- the
    registry and the label are answers to different questions, and coupling them buys nothing this
    module needs.
  - GATE ON "does this repo carry ANY `feature:*` label at all" -- the human act of creating one, as
    the opt-in signal, rather than the directory. Rejected for what it would have to ADD, not for
    being wrong in principle: a repo-wide label enumeration this module does not otherwise make, a
    THIRD network outcome on top of the two `label_exists` already owns
    (`REFUSED_UNRESOLVED`'s pair -- "no labels yet" and "could not tell" would need telling apart),
    and a staleness question the directory check never has to answer, because a directory does not
    go stale between two reads of it the way a label LIST fetched once and cached can.
  - LEAVE IT UNGATED, documented -- what ships. The write it produces on an unadopted repo
    (`sdlc:needs-label`, one comment) is the SAME overlay an adopted repo gets for the identical
    mistake, is reversible in the one gesture `resume_needs_label` already exists to perform, and
    never blocks the goal's membership -- `sdlc:goal` stays throughout. A repository that never
    intended any of this and never writes the marker pays nothing under any of the three shapes; the
    difference between them is only what happens to the repository that HALF-adopted -- wrote the
    marker, has not yet created the label or the directory -- and half-writing a specific,
    documented, bare marker is not the accident a directory check would exist to catch.

`docs/branching-model.md` §14 carries the measured transcript this reasons about, and §15 item 11
carries the same decision for the adopter reading the gaps list rather than this module.

#2263 ADDS ONE DELIBERATE EXCEPTION TO THE "NEVER GATED" CLAIM ABOVE, AND IT DOES NOT WEAKEN IT.
Everything above this paragraph describes `attach_at_pick`'s `BODY_ONLY` handling and
`resume_needs_label` -- the ORIGINAL machinery #1543 is about, and it is STILL ungated, unchanged.
The no-dangling-goal pair this module also carries now (`_handle_no_unit_at_pick`,
`_set_aside_no_unit`, `resume_needs_unit`) answers a DIFFERENT question -- what to do about a goal
declaring NO unit at all, not one whose label is missing -- and D-7 of `.sdlc/design/2253.md`
requires it to gate on `.sdlc/features/` existing, on purpose: applying EITHER new behaviour (the
`sdlc:needs-unit` set-aside, or classification via `feature_classify`) to a repository that never
adopted the branching model would set aside, or silently re-attribute, its entire backlog -- every
issue declares no unit on a repo that has never heard of one. So this file now contains exactly ONE
adoption-gate call, inside `_handle_no_unit_at_pick`, registered in `tests/test_feature_labels.py`'s
own `_KNOWN_ADOPTION_GATED_CALL_SITES` census as a decision written down on purpose, not a side
effect nobody reviewed -- see that test file's own updated docstring.

#2363 REPLACES THE `core` SENTINEL WITH A REAL CLASSIFICATION CHAIN, AND REVERSES D-6 OF
`.sdlc/design/2253.md` ON PURPOSE. `core` used to be a STRING nothing in the registry ever checked
-- `_attribute_to_core` (deleted) wrote a comment naming it and touched no label, specifically
because a real `feature:core` unit did not exist yet and D-6 worried about colliding with one a
repo might already have. #2260's own completion work bootstraps `core` as a REAL registered unit
(branch, label, registry entry) ahead of this slice, once, deliberately -- so `_handle_no_unit_at_
pick`'s "core configured" branch now redirects to `feature_classify.classify_at_pick`, which
ATTACHES the real `feature:<core>` label (or one of three other tiers) rather than merely
commenting about a name. `_set_aside_no_unit`, `resume_needs_unit` and the "core NOT configured"
branch are UNCHANGED -- see `feature_classify.py` for the tier chain itself.

Module shape follows `auto_unpark.py`: siblings loaded by file path (`_load`), every ledger write
fail-open, one stderr line for anything a human might otherwise never see.
"""
import collections
import importlib.util
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


features = _load("features")     # the read half (#1465): zero-dep, mutates nothing
ledger = _load("ledger")         # every automatic action is recorded; every call here is fail-open
feature_registry = _load("feature_registry")   # #2263: `.sdlc/features/` existence gate (D-7)

#: #2363: `feature_classify` is loaded LAZILY, on first use of the "core configured" branch, never
#: at module import -- the same shape and the same reason as `handoff._FEATURE_OWNER`/
#: `_FEATURE_REGISTRY`. `feature_classify.py` loads THIS module eagerly (for `Decision`, `_flag`,
#: `_record`, `label_for`, `_set_aside_no_unit`, the marker/text helpers), so an eager load in BOTH
#: directions would have `_load("feature_classify")` re-execute this file, which would re-execute
#: `_load("feature_classify")` again -- an infinite recursion of fresh module copies, not a cycle
#: Python's own import cache would catch, because `_load` never touches `sys.modules`. Every caller
#: of `_handle_no_unit_at_pick` that never reaches the "core configured" branch (`no_dangling_goal`
#: off, the default; on with no `core`) pays nothing for this at all.
_FEATURE_CLASSIFY = None


def _feature_classify():
    global _FEATURE_CLASSIFY
    if _FEATURE_CLASSIFY is None:
        _FEATURE_CLASSIFY = _load("feature_classify")
    return _FEATURE_CLASSIFY


#: #2380: `feature_judge` is loaded LAZILY too, for the identical reason `feature_classify` above
#: is -- `feature_judge.py` itself eagerly loads `feature_classify` (for `ABSTAIN`/`make_judgment`),
#: which eagerly loads THIS module; an eager load of `feature_judge` here, at module scope, in
#: EITHER direction would recurse (see `feature_judge.py`'s own module docstring for the full
#: chain). Every caller of `_handle_no_unit_at_pick` that never turns
#: `no_dangling_goal_live_judge_enabled` on pays nothing for this at all.
_FEATURE_JUDGE = None


def _feature_judge():
    global _FEATURE_JUDGE
    if _FEATURE_JUDGE is None:
        _FEATURE_JUDGE = _load("feature_judge")
    return _FEATURE_JUDGE


#: #2435: `feature_sync` is loaded lazily too, for the same reason as its siblings above -- it is
#: only ever needed by `ensure_unit_tracking`'s repair path, so a caller that never reaches a gap to
#: repair (the ordinary, healthy case) pays nothing for it. No cycle risk: `feature_sync.py` loads
#: `feature_registry`, `feature_doc`, `features` and `ledger`, never this module.
_FEATURE_SYNC = None


def _feature_sync():
    global _FEATURE_SYNC
    if _FEATURE_SYNC is None:
        _FEATURE_SYNC = _load("feature_sync")
    return _FEATURE_SYNC

# --- outcomes -------------------------------------------------------------------------------------
#: The label was missing and has been attached. `proceed` is True.
ATTACHED = "attached"
#: Nothing to do -- no declaration, both halves already agree, or the label alone declares.
#: `proceed` is True.
NOTHING_TO_DO = "nothing-to-do"
#: Both halves declare and they DISAGREE. `proceed` is True and no label is written -- but this is
#: deliberately NOT `NOTHING_TO_DO`, so a caller (and L5) can tell a conflict from an agreement.
CONFLICT_DECLARED = "conflict-declared"
#: The issue's body/labels could not be read at all. `proceed` is True -- see the failure table.
UNREADABLE = "unreadable"
#: The declared label does not exist on this repository. `proceed` is False; overlaid and flagged.
REFUSED_NO_LABEL = "refused-no-label"
#: The issue contradicts itself. `proceed` is False; overlaid and flagged for a human to edit.
REFUSED_AMBIGUOUS = "refused-ambiguous"
#: The label lookup could not answer. `proceed` is False; NOT flagged, NOT overlaid -- transient.
REFUSED_UNRESOLVED = "refused-unresolved"
#: The attach write did not land. `proceed` is False; NOT flagged, NOT overlaid -- transient.
REFUSED_WRITE_FAILED = "refused-write-failed"
#: #2263: no unit declared anywhere, `no_dangling_goal` opted in (no `core`), registry adopted --
#: set aside under `sdlc:needs-unit`. `proceed` is False; overlaid and flagged for a human to edit.
SET_ASIDE_NO_UNIT = "set-aside-no-unit"

#: `proceed` False means REFUSE THE PICK: do not claim this goal, do not start work on it.
#: `outcome` is one of the constants above; `unit` is the declared unit, or None.
Decision = collections.namedtuple("Decision", "proceed outcome unit")

#: The methods a source must have before this module will touch it. `LocalSource` -- whose goals are
#: files and which has no repository label namespace at all -- has NONE of them, and `all(...)` is
#: what makes a PARTIAL surface (the shape the next level produces the moment it adds a method) fail
#: as a clean no-op rather than as an `AttributeError` raised inside the pick loop.
REQUIRED_SOURCE_METHODS = ("fetch_body_labels", "label_exists", "attach_label", "mark_needs_label",
                           "clear_needs_label", "list_needs_label")

#: #2263: the SEPARATE method surface `_handle_no_unit_at_pick`/`resume_needs_unit` need. Kept
#: apart from `REQUIRED_SOURCE_METHODS` deliberately: coupling the two would mean a source (or a
#: test double) that has not yet grown `mark_needs_unit`/`clear_needs_unit`/`list_needs_unit`
#: degrades the EXISTING `sdlc:needs-label` machinery to `NOTHING_TO_DO` too, which is a regression
#: this feature must not cause by merely existing. A source missing this surface simply never
#: reaches the no-dangling-goal behaviour -- the same clean no-op `_has_surface` already gives the
#: sibling surface, one level up.
NO_DANGLING_GOAL_METHODS = ("mark_needs_unit", "clear_needs_unit", "list_needs_unit")

#: The idempotency markers. HTML comments, so they are invisible in the rendered issue and are not
#: something a human reproduces by accident -- the two properties a "have I already said this?"
#: probe needs. They are DISTINCT per reason: one shared marker would mean an issue flagged for a
#: missing label and later hand-edited into a self-contradiction is silently refused with no second
#: flag, so the human is told about one problem and never about the one that replaced it.
#:
#: The flag TEXT quotes the very marker it is complaining about (`Feature: <unit>`), and that is
#: safe for a reason worth stating rather than assuming: `features.parse_body` reads an issue's
#: BODY, never its comments -- and even if it ever did, rule 1 anchors a declaration at the START of
#: its line, while every mention below sits mid-sentence inside backticks.
MISSING_LABEL_MARKER = "<!-- sigma:feature-label-missing -->"
AMBIGUOUS_MARKER = "<!-- sigma:feature-unit-ambiguous -->"
#: #2263: same idempotency contract as the two markers above, one per reason.
NEEDS_UNIT_MARKER = "<!-- sigma:feature-unit-missing -->"

#: Ledger `why` tokens. FIRST word of the field on purpose: `ledger._sanitize_free_text` caps at
#: `FREE_TEXT_CAP` from the END, so a leading token can never be truncated away.
#:
#: EVERY ONE OF THESE IS AUDIT ONLY. Nothing in this module reads them back, deliberately: the
#: ledger is off by default, lives on a separate branch that must be bootstrapped per clone, and is
#: gitignored on the code branch -- three independent ways for it to be empty on a machine that is
#: otherwise working fine. A state whose recovery depended on it would be stuck on all three.
BLOCK_TOKEN = "feature-unit-held"
RESUME_TOKEN = "feature-unit-released"
ATTACH_TOKEN = "feature-unit-attached"
CONFLICT_TOKEN = "feature-unit-conflict"
#: #2263: the same audit-only contract, for the no-dangling-goal pair.
NO_UNIT_BLOCK_TOKEN = "feature-no-unit-held"
NO_UNIT_RESUME_TOKEN = "feature-no-unit-released"


def label_for(unit):
    """The label a unit name projects to. One definition, built from `features.LABEL_PREFIX`, so the
    attach half and the read half cannot drift on what a unit's label is called."""
    return features.LABEL_PREFIX + unit


def is_feature_label(name):
    """Is `name` a unit label? Case-INSENSITIVE, because GitHub label names are case-insensitively
    unique -- `Feature:Voice` and `feature:voice` cannot both exist on a repo, so a case-sensitive
    guard would be bypassed by typing a capital. The prefix INCLUDES the colon: `featureflag` is an
    ordinary label an adopter may legitimately own, and refusing it would be this module quietly
    deciding what a repository is allowed to name things."""
    return str(name or "").lower().startswith(features.LABEL_PREFIX)


def creates_a_feature_label(args):
    """Is `args` a `gh label create` invocation carrying a `feature:*` argument?

    Read by `sources.GitHubSource._run`, the single chokepoint every `gh` call in that class passes
    through -- see this module's docstring for which call site makes that placement load-bearing,
    and for the four label-creating routes this verb-shaped guard does NOT cover. ANY argument is
    checked, not just the positional name: the requirement is "never invoked WITH a `feature:*`
    argument", and `gh` accepts flags before the name."""
    if len(args) < 3:
        return False
    if str(args[0]) != "label" or str(args[1]) != "create":
        return False
    return any(is_feature_label(a) for a in args)


def _note(message):
    """One stderr line, never an exception -- same shape and same reason as `features._note`: a
    diagnostic must never be the thing that breaks a pick."""
    try:
        sys.stderr.write(message)
    except Exception:                     # noqa: BLE001 - a diagnostic must never break a pick
        pass


def _record(sdlc_dir, goal, config, why):
    """One ledger line for an automatic action. Fail-open by construction (`safe_append` swallows),
    and `why` is kept well inside `ledger.FREE_TEXT_CAP` so nothing is silently truncated."""
    ledger.safe_append(sdlc_dir, "note", str(goal), config=config, why=why)


def _has_surface(source):
    return all(callable(getattr(source, m, None)) for m in REQUIRED_SOURCE_METHODS)


def _flag(source, goal, marker, text):
    """Post the flag comment, once per pass per timeline read. Returns True iff this call posted it.

    Idempotent against the issue's own timeline rather than against local state, because the state
    that matters is the one a human reads. When the timeline cannot be READ, nothing is posted -- a
    duplicate comment is a worse failure than a late one, and the stderr note has already fired.

    HONEST BOUND, stated because "posted once" over-claims: the probe is a read followed by a write
    with nothing serialising them, so two machines whose windows overlap can each post one comment.
    That sits inside `docs/label-model.md` §11's already-accepted two-machines limitation and costs a
    duplicate comment, not a wrong state."""
    try:
        seen = source.fetch_comments_strict(goal)
    except Exception as exc:              # noqa: BLE001 - see docstring; a read we cannot trust
        _note("sigma: features: could not read #%s's comments (%s) — the flag comment was not "
              "posted this pass; it will be retried on the next pick\n" % (goal, exc))
        return False
    for comment in seen.get("comments") or []:
        if marker in ((comment or {}).get("body") or ""):
            return False
    try:
        source.note(goal, text)
        return True
    except Exception as exc:              # noqa: BLE001 - flagging must never break the pick
        _note("sigma: features: could not comment on #%s (%s) — the refusal stands and is "
              "recorded, but nobody has been told on the issue itself\n" % (goal, exc))
        return False


def _missing_label_text(unit):
    return (
        "%s\n"
        "**Sigma has set this goal aside.** Its body declares `%s: %s`, but the label "
        "`%s` does not exist on this repository — and Sigma never creates one.\n\n"
        "Attaching an existing label is additive and reversible. *Creating* one mutates the "
        "repository's label namespace permanently, so a typo in a body marker would mint a label "
        "that outlives the unit. That is a human's call (`docs/label-model.md` §5).\n\n"
        "So one of two things is true, and only you can say which:\n\n"
        "- **a typo** — correct the `%s:` line in the body; or\n"
        "- **a unit nobody has opened yet** — create the label `%s` (and the branch `%s%s`).\n\n"
        "**One gesture is enough.** This issue keeps `sdlc:goal` and carries `sdlc:needs-label` "
        "while it waits, so nothing reports it as ready to pick and no later slot re-reads it. As "
        "soon as the label exists, Sigma removes `sdlc:needs-label` itself and picks the goal "
        "up — there is nothing to un-park, and nothing else to do."
        % (MISSING_LABEL_MARKER, features.BODY_KEY, unit, label_for(unit),
           features.BODY_KEY, label_for(unit), features.BRANCH_PREFIX, unit))


def _ambiguous_text(detail):
    return (
        "%s\n"
        "**Sigma has set this goal aside.** This issue declares its unit of work more than "
        "once, and the declarations disagree:\n\n> %s\n\n"
        "Nothing automatic may pick between two declarations a human wrote — a first-wins guess "
        "would base the goal on a branch nobody chose. Edit the issue so one unit is declared and "
        "Sigma removes `sdlc:needs-label` itself on the next pass. Only this issue is affected; "
        "the rest of the queue is unaffected."
        % (AMBIGUOUS_MARKER, detail))


def _needs_unit_text(suggested=None):
    """#2263: the flag comment for `SET_ASIDE_NO_UNIT`. Mirrors `_missing_label_text` in shape --
    marker, plain statement of what is missing, the one gesture that clears it, the self-heal
    promise -- but names no unit, because none was declared: there is nothing to quote.

    `suggested` (#2363, optional): a `(name, evidence)` pair from tier 3 of `feature_classify`'s
    classification chain -- an identifiable but UNREGISTERED component, named only because concrete
    evidence (a real path in this repository) backs it. Threaded into the comment, never the body:
    a suggestion is not a declaration, and writing it into the body would risk being mistaken for
    one on the very next read (`features.parse_body` reads the body literally). `None` (every
    caller before #2363, and the "core not configured" call site today) reproduces the exact text
    this function always returned."""
    hint = ""
    if suggested:
        name, evidence = suggested
        hint = (
            " Sigma's own classifier looked and could not match this to any single existing "
            "unit, but found `%s` in this repository and suggests the name `%s` — that is a "
            "suggestion, not a declaration; nothing in the registry names that unit until a human "
            "declares it for real." % (evidence, name))
    return (
        "%s\n"
        "**Sigma has set this goal aside.** This issue declares no unit of work anywhere: no "
        "bare `%s:` line in the body and no `%s*` label — and this repository requires one before "
        "a goal is picked (`docs/label-model.md` §2b-iv).%s\n\n"
        "**One gesture is enough.** Declare the unit this goal belongs to:\n\n"
        "    %s: <unit-name>\n"
        "    %s: %s<unit-name>\n\n"
        "on their own lines in the body, or attach an existing `%s<unit-name>` label directly. "
        "This issue keeps `sdlc:goal` and carries `sdlc:needs-unit` while it waits, so nothing "
        "reports it as ready to pick and no later slot re-reads it. As soon as a unit is declared, "
        "Sigma removes `sdlc:needs-unit` itself and picks the goal up — there is nothing to "
        "un-park, and nothing else to do."
        % (NEEDS_UNIT_MARKER, features.BODY_KEY, features.LABEL_PREFIX, hint,
           features.BODY_KEY, features.BRANCH_KEY, features.BRANCH_PREFIX, features.LABEL_PREFIX))


_RESUME_COMMENT = ("Auto-resumed by Sigma — {what}; removed `{label}` so this goal is pickable "
                   "again.")

#: What the stderr notes call the overlay. One spelling, so a rename in config cannot leave the
#: diagnostics naming a label that no longer exists.
NEEDS_LABEL_NOTE = "the needs-label overlay"
#: #2263: same convention, sibling overlay.
NEEDS_UNIT_NOTE = "the needs-unit overlay"


def _refuse(sdlc_dir, source, goal, config, outcome, unit, marker, text):
    """The shared tail of the two refusals that need a HUMAN: overlay, flag, audit -- in that order.

    OVERLAY FIRST, and the order matters. The overlay is what takes the goal out of the pickable
    queue AND what records that we are the ones holding it, so doing it before the slower,
    best-effort comment means a crash between the two costs one missing comment on an
    already-correct state -- never a goal left in the queue, and never a held goal nothing can
    attribute.

    IF THE OVERLAY DOES NOT LAND, NOTHING ELSE HAPPENS AND THAT IS SAFE. The pick is still refused,
    so no work starts against the wrong base; the goal simply stays in the queue and is refused
    again next pass -- the ORIGINAL silence behaviour, which was never unsafe, only noisy. There is
    no half-state to clean up, because the label is the only state there is.

    The ledger line is audit, not machinery. `resume_needs_label` never reads it."""
    landed = source.mark_needs_label(goal) is True
    if not landed:
        _note("sigma: features: the %s overlay on #%s did not land — the pick is still refused "
              "and the goal stays in the queue, exactly as it did before this feature existed; the "
              "next pass retries it\n" % (NEEDS_LABEL_NOTE, goal))
        return Decision(False, outcome, unit)
    _flag(source, goal, marker, text)
    _record(sdlc_dir, goal, config,
            "%s %s — %s" % (BLOCK_TOKEN, label_for(unit) if unit else "(ambiguous)",
                            "no such label on this repo" if unit else "issue declares two units"))
    return Decision(False, outcome, unit)


# --- #2263: no unit declared anywhere -- the opt-in "no work sits outside the structure" pair ------

def _has_no_dangling_surface(source):
    """Same duck-typing discipline as `_has_surface`, over `NO_DANGLING_GOAL_METHODS` -- kept as its
    own function (rather than parameterising `_has_surface`) so each call site reads as a plain
    boolean check, not `_has_surface(source, methods=...)`, at its one call site below."""
    return all(callable(getattr(source, m, None)) for m in NO_DANGLING_GOAL_METHODS)


def _set_aside_no_unit(sdlc_dir, source, goal, config, suggested=None):
    """The SET-ASIDE half of `_handle_no_unit_at_pick`: no `core` catch-all configured, so the goal
    is held under `sdlc:needs-unit` instead. Same OVERLAY-FIRST ordering `_refuse` uses and for the
    identical reason -- see its docstring, which this does not repeat. A dedicated function rather
    than a generalised `_refuse` call: the ledger `why` text this writes has nothing in common with
    `_refuse`'s (there is no unit and no label to name, only an absence), so sharing the tail would
    trade one clear function for one branchier one without saving anything real.

    `suggested` (#2363, optional): a `(name, evidence)` pair, threaded straight into
    `_needs_unit_text` -- see its own docstring. Tier 3 of `feature_classify`'s classification chain
    calls this SAME function, unchanged in every other respect, rather than a second copy of the
    set-aside mechanism, so the overlay, the ledger record and the self-heal all stay ONE
    definition regardless of which caller reached it."""
    landed = source.mark_needs_unit(goal) is True
    if not landed:
        _note("sigma: features: the %s overlay on #%s did not land — the pick is still refused "
              "and the goal stays in the queue, exactly as it did before this feature existed; the "
              "next pass retries it\n" % (NEEDS_UNIT_NOTE, goal))
        return Decision(False, SET_ASIDE_NO_UNIT, None)
    _flag(source, goal, NEEDS_UNIT_MARKER, _needs_unit_text(suggested))
    why = "%s — no unit declared anywhere on the issue" % NO_UNIT_BLOCK_TOKEN
    if suggested:
        why += " (classifier suggested %r)" % suggested[0]
    _record(sdlc_dir, goal, config, why)
    return Decision(False, SET_ASIDE_NO_UNIT, None)


def _handle_no_unit_at_pick(sdlc_dir, source, goal, config):
    """-> `Decision`, or `None` when the feature does not apply here. Called from `attach_at_pick`
    only when `features.read` returned `NONE` -- no declaration anywhere on the issue.

    THE GATE IS TWO CONDITIONS, BOTH REQUIRED, PER D-7 OF `.sdlc/design/2253.md`: opted in
    (`source.no_dangling_goal_enabled`, resolved once by the Source from `discovery.no_dangling_goal
    .enabled`, the identical "the Source resolves config, this module reads attributes" split
    `needs_label_label` already establishes) AND `.sdlc/features/` existing. Returning `None` on
    either failing is what makes the byte-identical-when-off promise true structurally rather than
    by convention: `attach_at_pick`'s caller falls through to the SAME `Decision(True, NOTHING_TO_DO,
    None)` a `NONE` verdict has always produced, so an adopter who never sets the config key -- the
    default -- pays nothing beyond one cheap attribute read, and a project with no registry pays
    nothing beyond one cheap read plus one `is_dir()` stat, same as `_unit_at_pick`'s own gate
    (`loop.py`) already costs for the identical directory check on its own path.

    `getattr(..., False)` and `_has_no_dangling_surface`, not a bare attribute access: a `LocalSource`
    or a test double that has never heard of this feature must degrade to `None` here exactly as
    `_has_surface` already degrades the sibling machinery elsewhere in this module, never raise
    inside the pick path."""
    if not getattr(source, "no_dangling_goal_enabled", False):
        return None
    if not _has_no_dangling_surface(source):
        return None
    try:
        adopted = feature_registry.registry_dir(sdlc_dir).is_dir()
    except Exception as exc:              # noqa: BLE001 - a filesystem check must never break a pick
        _note("sigma: features: the %s directory check for #%s did not run (%s) — proceeding "
              "with no unit, exactly as before this feature existed\n"
              % (feature_registry.REGISTRY_DIRNAME, goal, exc))
        return None
    if not adopted:
        return None
    core = getattr(source, "no_dangling_goal_core", None)
    if core:
        # #2363: `core` is now a REAL registered unit (bootstrapped once, ahead of this slice --
        # see the module docstring's D-6 reversal), so the "core configured" branch redirects to
        # the 4-tier classification chain rather than writing a sentinel comment. Loaded lazily,
        # the same shape `handoff._feature_owner`/`_feature_registry` already use, because
        # `feature_classify` itself loads THIS module (for `Decision`, `_flag`, `_record`,
        # `label_for`, `_set_aside_no_unit`) -- a module-level `_load` in both directions would
        # recurse forever loading fresh copies of each other.
        #
        # #2380: a FURTHER, independent opt-in (`no_dangling_goal_live_judge_enabled`, resolved by
        # the Source the same way `no_dangling_goal_core` already is) routes this through a REAL,
        # metered live model call (`feature_judge.live_judge`) instead of the judge-less default
        # that always abstains. `getattr(..., False)`, not a bare attribute access: a source that
        # predates this slice (or a test double that has not grown the attribute) must degrade to
        # `False` here exactly like every other new attribute in this family, never raise inside
        # the pick path. OFF (the default) passes `judge=None`, byte-identical to omitting the
        # keyword entirely -- `classify_at_pick`'s own `(judge or _default_judge)` already treats
        # the two identically.
        judge = (_feature_judge().live_judge
                 if getattr(source, "no_dangling_goal_live_judge_enabled", False) else None)
        return _feature_classify().classify_at_pick(sdlc_dir, source, goal, config, core,
                                                     judge=judge)
    return _set_aside_no_unit(sdlc_dir, source, goal, config)


def attach_at_pick(sdlc_dir, source, goal, config=None):
    """-> `Decision`. **`proceed` False means REFUSE THE PICK.**

    Called from `loop._next()` after the claim lock is won AND after the budget gate, and before
    `mark_in_progress`. All three positions are load-bearing:

      - after the LOCK, so only the winner of a race writes anything;
      - after the BUDGET gate, because a run that reports it did nothing must not have mutated the
        repository -- this used to run first, and a `('BUDGET', ...)` return had already sent an
        `issue edit --add-label` and written a ledger line;
      - before `mark_in_progress`, because every downstream step -- base resolution (#1467), the
        registry (#1469), sibling propagation -- reads the LABEL, so a goal that started work before
        the attach would resolve against the wrong base and record itself under nothing.

    `source` is duck-typed on `REQUIRED_SOURCE_METHODS`; a source missing any of them degrades to
    `NOTHING_TO_DO` rather than raising inside the pick path."""
    if not _has_surface(source):
        return Decision(True, NOTHING_TO_DO, None)
    try:
        issue = source.fetch_body_labels(goal)
    except Exception as exc:              # noqa: BLE001 - see the module docstring's failure table
        _note("sigma: features: could not read #%s (%s) — proceeding with no declared unit, "
              "exactly as before this feature existed\n" % (goal, exc))
        return Decision(True, UNREADABLE, None)
    try:
        verdict = features.read(issue)
    except features.AmbiguousUnit as exc:
        _note("sigma: features: #%s contradicts itself (%s) — setting it aside and flagging it; "
              "the rest of the queue is unaffected\n" % (goal, exc))
        return _refuse(sdlc_dir, source, goal, config, REFUSED_AMBIGUOUS, None,
                       AMBIGUOUS_MARKER, _ambiguous_text(exc))
    if verdict.state == features.CONFLICT:
        _note("sigma: features: #%s declares unit %r in its body but carries the label %s — the "
              "body wins for reading, and NO label is written here; downstream steps that read the "
              "LABEL will disagree with base resolution, which reads the body\n"
              % (goal, verdict.body, label_for(verdict.label)))
        _record(sdlc_dir, goal, config, "%s body=%s label=%s — no label written"
                % (CONFLICT_TOKEN, verdict.body, verdict.label))
        return Decision(True, CONFLICT_DECLARED, verdict.unit)
    if verdict.state == features.NONE:
        # #2263: `verdict.unit` is always `None` on `NONE` (see `features.read`'s own tail), so
        # falling through to the plain `NOTHING_TO_DO` case is byte-identical to what this branch
        # produced before this feature existed -- `_handle_no_unit_at_pick` returns `None` on
        # every gate it fails (feature off, no registry, source missing the surface), and only
        # returns a real `Decision` once genuinely opted in.
        decision = _handle_no_unit_at_pick(sdlc_dir, source, goal, config)
        return decision if decision is not None else Decision(True, NOTHING_TO_DO, None)
    if verdict.state != features.BODY_ONLY:
        return Decision(True, NOTHING_TO_DO, verdict.unit)

    unit = verdict.unit
    label = label_for(unit)
    exists = source.label_exists(label)
    if exists is None:
        _note("sigma: features: could not determine whether %s exists — refusing #%s's pick "
              "this pass rather than starting it against the wrong base. No overlay and no flag: a "
              "lookup that could not answer is not evidence the label is absent\n" % (label, goal))
        return Decision(False, REFUSED_UNRESOLVED, unit)
    if not exists:
        _note("sigma: features: #%s declares unit %r but the label %s does not exist on this "
              "repository — setting it aside. Sigma never creates a feature label; a human "
              "creates it, or corrects the typo\n" % (goal, unit, label))
        return _refuse(sdlc_dir, source, goal, config, REFUSED_NO_LABEL, unit,
                       MISSING_LABEL_MARKER, _missing_label_text(unit))
    if not source.attach_label(goal, label):
        _note("sigma: features: the attach of %s to #%s did not land — refusing the pick; the "
              "next pass retries it. No overlay and no flag: the label exists, so this is the write "
              "that failed, not a human's problem to solve\n" % (label, goal))
        return Decision(False, REFUSED_WRITE_FAILED, unit)
    _record(sdlc_dir, goal, config, "%s %s — declared by the issue body" % (ATTACH_TOKEN, label))
    return Decision(True, ATTACHED, unit)


# --- the self-healing half -------------------------------------------------------------------------

def _still_held(source, issue):
    """Is the reason we overlaid this issue still true? `(held, what_changed)`.

    `issue` is the payload `list_needs_label` already fetched -- number, body and labels -- so the
    common case costs NO issue read at all. Only the label lookup is live, and only for a unit whose
    label is still the open question.

    THE THREE-VALUED LOOKUP IS OBEYED HERE TOO, and this is the side where it decides whether to
    UNDO a state. `label_exists` answers True / False / None-for-could-not-tell, and only True
    releases: a lookup that could not answer is not evidence the label exists, and treating it as
    one would clear the overlay and post an audit comment claiming "the label now exists" when it
    may not. The identical rule is enforced on the block side; enforcing it on one side only was the
    same asymmetry twice."""
    try:
        verdict = features.read(issue)
    except features.AmbiguousUnit:
        return True, None                 # still contradicts itself; a human still has to edit it
    if verdict.state != features.BODY_ONLY:
        return False, "the issue no longer declares a unit whose label is missing"
    label = label_for(verdict.unit)
    if source.label_exists(label) is not True:
        return True, None
    return False, "the label `%s` now exists" % label


def resume_needs_label(sdlc_dir, source, config=None):
    """Clear the `sdlc:needs-label` overlay from every goal whose missing-label problem is solved.
    Returns the sorted list of goals actually released.

    This is what makes the overlay cost the human exactly ONE gesture -- create the label (or fix
    the marker), and nothing else. Called from `loop._next()` alongside the other sweeps.

    ONE `gh issue list` PER SWEEP, and that is the whole cost: the query carries every held issue's
    body and labels, so releasing N goals costs no per-issue reads and a held goal costs no REST
    read at all -- only one live GraphQL label lookup while its label is still absent (`label_exists`
    caches names it FOUND, never names it did not, which is precisely what lets a label created a
    moment ago be seen). Compare the shape this replaced: the goal stayed in the pickable queue and
    every slot of every batch re-read it, twice.

    NO ATTRIBUTION STORE, because the label IS the attribution -- nothing else writes it, so every
    issue this query returns is one we hold. That is what removes the earlier ledger dependency, and
    with it a failure mode that had the state permanently stuck on the shipped default config.

    NO COOLDOWN, unlike `auto_unpark`'s sweep, and the difference is principled. That sweep holds a
    just-unparked goal back for one `_next()` call so a human has a window to re-park a checkpoint
    the machine may have misjudged. Here nothing human is being overridden: the overlay is the
    loop's OWN state and clearing it restores exactly what the human asked for by creating the
    label. Picking it up in the same call is the intent, not a race with anybody.

    HONEST BOUND, the same one `_flag` states: read-then-write with nothing serialising two machines
    (or two `parallel.goals` slots), so overlapping windows can each post one audit comment. The
    label write is idempotent and the outcome is identical either way; the cost is a duplicate
    comment, never a wrong state."""
    if not _has_surface(source):
        return []
    released = []
    for issue in source.list_needs_label() or []:
        goal = str((issue or {}).get("number") or "")
        if not goal:
            continue
        try:
            held, what = _still_held(source, issue)
        except Exception as exc:          # noqa: BLE001 - one bad issue must not stop the sweep
            _note("sigma: features: could not re-check #%s (%s) — leaving it held\n"
                  % (goal, exc))
            continue
        if held:
            continue
        if not source.clear_needs_label(goal):
            _note("sigma: features: %s for #%s, but clearing %s did not land — the next pass "
                  "retries it\n" % (what, goal, NEEDS_LABEL_NOTE))
            continue
        _record(sdlc_dir, goal, config, "%s — %s" % (RESUME_TOKEN, what))
        try:
            source.note(goal, _RESUME_COMMENT.format(what=what, label=source.needs_label_label))
        except Exception:                 # noqa: BLE001 - best-effort audit trail, never the gate
            pass
        released.append(goal)
    return sorted(released, key=lambda g: (len(g), g))


# --- #2263: the self-healing half of the no-dangling-goal pair -------------------------------------

def _still_held_no_unit(source, issue):
    """Is the reason we set this goal aside still true? `(held, what_changed)` -- the `sdlc:needs-
    unit` sibling of `_still_held` above, with ONE deliberate divergence worth stating rather than
    silently copying: `_still_held` keeps an `AmbiguousUnit` issue HELD, because that overlay's own
    condition (a body declaration whose label is missing) is neither fixed nor contradicted by an
    edit that only adds a second, disagreeing declaration. This overlay's condition is the OPPOSITE
    -- "nothing is declared anywhere" -- and an `AmbiguousUnit` edit is proof a declaration now
    exists, even a broken one. So this function RELEASES on it: the very next pick this same
    `_next()` call takes (this sweep always runs before the pick, `loop.py`'s `_next()`) reaches
    `attach_at_pick` again, which already has its own correct, louder handling for a self-
    contradicting issue (`REFUSED_AMBIGUOUS`, `AMBIGUOUS_MARKER`) -- re-triaging into the RIGHT
    overlay is better than parking the goal behind a comment describing a problem ("no unit at all")
    the issue no longer actually has."""
    try:
        verdict = features.read(issue)
    except features.AmbiguousUnit:
        return False, "the issue now declares a unit, ambiguously -- a human's edit is a real " \
                      "declaration even where it disagrees with itself"
    if verdict.state != features.NONE:
        return False, "the issue now declares a unit"
    return True, None


def resume_needs_unit(sdlc_dir, source, config=None):
    """Clear the `sdlc:needs-unit` overlay from every goal whose no-declaration problem is solved.
    Returns the sorted list of goals actually released. Mirrors `resume_needs_label` exactly --
    same one-`gh issue list`-per-sweep cost shape, same "the label IS the attribution, no ledger
    dependency" reasoning, same no-cooldown rationale -- see that function's own docstring, which
    this one does not repeat.

    GATED ONLY ON THE SOURCE HAVING THE METHOD SURFACE, DELIBERATELY NOT ON
    `no_dangling_goal_enabled` (#2426, a real bug found live and fixed here). An earlier version
    gated on BOTH, matching `_handle_no_unit_at_pick`'s own gate -- but that function decides
    whether to SET a goal aside, while this one only ever CLEARS a label something else already
    wrote. The two are not the same question, and gating the clear on a flag that can change after
    the label was written strands a goal permanently: the pick-side exclusion
    (`overlay_labels`/`not_eligible_labels`) never reads this attribute at all, so turning the
    feature off after it has held at least one goal removes the ONLY sweep that could ever release
    it, with no recovery but a manual label edit.

    THE COST THIS BRINGS IS NOT NEW -- it is `resume_needs_label`'s own, already-accepted shape:
    that sweep has NEVER gated on a feature flag, paying one `gh issue list` per `_next()` call on
    every github-mode repo regardless of whether it has ever used `feature:` labels at all. This
    fix makes the two overlays' sweeps cost the identical, already-established amount rather than
    leaving an inconsistency between two functions #2263 introduced as deliberate siblings.

    There is deliberately no separate `.sdlc/features/` re-check either: an issue cannot carry
    `sdlc:needs-unit` at all unless `_handle_no_unit_at_pick` set it while the registry was
    adopted, and a registry, once created, is not a thing that un-adopts itself mid-sweep --
    re-checking `is_dir()` on every release would be cost with no scenario it protects against."""
    if not _has_no_dangling_surface(source):
        return []
    released = []
    for issue in source.list_needs_unit() or []:
        goal = str((issue or {}).get("number") or "")
        if not goal:
            continue
        try:
            held, what = _still_held_no_unit(source, issue)
        except Exception as exc:          # noqa: BLE001 - one bad issue must not stop the sweep
            _note("sigma: features: could not re-check #%s (%s) — leaving it held\n"
                  % (goal, exc))
            continue
        if held:
            continue
        if not source.clear_needs_unit(goal):
            _note("sigma: features: %s for #%s, but clearing %s did not land — the next pass "
                  "retries it\n" % (what, goal, NEEDS_UNIT_NOTE))
            continue
        _record(sdlc_dir, goal, config, "%s — %s" % (NO_UNIT_RESUME_TOKEN, what))
        try:
            source.note(goal, _RESUME_COMMENT.format(what=what, label=source.needs_unit_label))
        except Exception:                 # noqa: BLE001 - best-effort audit trail, never the gate
            pass
        released.append(goal)
    return sorted(released, key=lambda g: (len(g), g))


# --- #2435: the side-job unit-registry ensure -------------------------------------------------
#
# Everything above this line decides what to do the MOMENT a goal is picked. This answers a
# different question, on a different clock: for the goal the loop is ALREADY working, does its
# registry membership still agree with its own label, on every later trigger -- not just the first
# one? `loop._ensure_unit_tracking` is the fail-open, cooldown-gated wrapper that calls
# `ensure_unit_tracking` below from the same trigger set `_ensure_claimed` already uses
# (`note`/`verify`/`agent-start`); see that function's own docstring for the full design.

#: The one method this needs beyond `REQUIRED_SOURCE_METHODS`/`NO_DANGLING_GOAL_METHODS` -- its own
#: tuple, for the same reason `NO_DANGLING_GOAL_METHODS` is kept apart from `REQUIRED_SOURCE_METHODS`:
#: a source (or a test double) that has not grown `fetch_body_labels_rest` yet must not regress
#: either of the two EXISTING surfaces by merely lacking a method neither of them declares.
UNIT_TRACKING_METHODS = ("fetch_body_labels_rest",)

#: #2435: same idempotency contract as every marker above -- one per reason, an HTML comment so it
#: never renders and is never something a human reproduces by hand.
UNIT_TRACKING_MARKER = "<!-- sigma:unit-tracking-warning -->"

#: `ensure_unit_tracking`'s own outcomes -- returned, never raised, so a caller (today only
#: `loop._ensure_unit_tracking`) or a test can read what happened without parsing free text.
UNIT_TRACKING_OK = "ok"                # nothing applies, or the goal is already recorded correctly
UNIT_TRACKING_REPAIRED = "repaired"    # the goal was missing and this pass added it
UNIT_TRACKING_WARNED = "warned"        # a gap was found and could not be (or was not) repaired


def _has_unit_tracking_surface(source):
    return all(callable(getattr(source, m, None)) for m in UNIT_TRACKING_METHODS)


def _configured_repo(config):
    """`discovery.github.repo`, or None. Never a `git remote` fallback -- see `ensure_unit_tracking`'s
    own docstring for why this side job does not spend a subprocess on a question the real pick-time
    sync (`feature_sync.repo_slug`) already answers authoritatively. The `{owner}/{repo}` literal is
    `gh`'s own placeholder for an unset repo (`work._issue_repo`'s identical guard), never a real
    slug, so it is rejected here the same way `feature_sync._SLUG_RE` rejects it there."""
    discovery = config.get("discovery") if isinstance(config, dict) else None
    github = discovery.get("github") if isinstance(discovery, dict) else None
    repo = github.get("repo") if isinstance(github, dict) else None
    return (repo.strip() if isinstance(repo, str) and repo.strip() and "{owner}" not in repo
            else None)


def _repo_goals(entry, repo):
    """The normalised goal list `entry` records for `repo`, matched case-insensitively (GitHub repo
    names are case-insensitively unique -- `feature_sync.same_repo`'s own rule, applied here since
    this module does not load that one eagerly). `None` when `repo` is not one the unit names at
    all -- distinct from `[]`, which means it IS named and simply has nothing recorded yet."""
    repos = entry.get("repos") or {}
    for key, value in repos.items():
        if str(key).lower() == str(repo).lower():
            return (value or {}).get("goals") or []
    return None


def _goal_number(goal):
    """`goal` coerced the one way the registry ever coerces a goal number -- through
    `feature_registry.normalise_entry`, never a second opinion about what a goal number is. Mirrors
    `feature_sync._goal_number` exactly, duplicated rather than imported because it is four lines
    behind a private name in a module this one only loads lazily and only on the repair path."""
    got = feature_registry.normalise_entry(
        {"repos": {"x/y": {"goals": [goal]}}})["repos"]["x/y"]["goals"]
    return got[0] if got else None


def _resolve_registry_key(registry_now, unit):
    """The registry's OWN spelling of `unit`, matched case-insensitively -- the same manual scan
    `sources.GitHubSource._unit_priority_rank` already runs over this exact dict, for the reason
    `feature_registry.resolve_open_unit` gives: a unit name is human-typed on one side (a label) and
    registered on the other, and a casing difference is not a different unit. NOT `resolve_open_unit`
    itself: that answers None for a CLOSED unit too, and a closed unit's membership is still this
    function's business (`_record_goal` keeps recording goals onto one, `PICKED_WHILE_CLOSED` rather
    than refused) -- only an UNREGISTERED unit is the "nothing to reconcile against" case here."""
    lowered = str(unit).lower()
    for name in registry_now:
        if str(name).lower() == lowered:
            return name
    return None


def _unit_tracking_warning_text(unit, reason):
    return (
        "%s\n"
        "**Sigma's unit-registry check found a gap on this goal.** It declares unit `%s`, but "
        "%s\n\n"
        "This is a side check, not a gate — the goal keeps running unchanged, and nothing about "
        "this issue's own label was touched. Run `/agrim-doctor`, or amend "
        "`.sdlc/features/units/%s.json` by hand (`docs/branching-model.md`), to close the gap."
        % (UNIT_TRACKING_MARKER, unit, reason, unit))


def _warn_unit_tracking(sdlc_dir, source, goal, config, unit, reason):
    """The shared tail of every gap `ensure_unit_tracking` cannot repair itself: one stderr line
    always (the "local log" half), one idempotent issue comment via `_flag` (posts at most once per
    issue, per that function's own timeline-read contract), and one ledger line ONLY when the
    comment is the one that actually landed -- `_record_goal`'s own "ledgered iff a state change was
    made" rule, applied here to "flagged iff this is the first time"."""
    _note("sigma: features: unit-tracking #%s — %s\n" % (goal, reason))
    if _flag(source, goal, UNIT_TRACKING_MARKER, _unit_tracking_warning_text(unit, reason)):
        _record(sdlc_dir, goal, config, "unit-tracking-warned %s — %s" % (unit, reason))
    return UNIT_TRACKING_WARNED


def ensure_unit_tracking(sdlc_dir, source, goal, config):
    """The real check-and-repair pass behind `loop._ensure_unit_tracking`'s cooldown wrapper (#2435).
    -> one of the `UNIT_TRACKING_*` outcomes. NEVER RAISES -- every exception resolves to `warned`,
    the same "a diagnostic must never break a pick" posture every sibling in this module already
    takes (`_note`, `_flag`), because this function's entire reason to exist is a side job that must
    never cost the goal it is checking.

    WHAT THIS IS, IN ONE SENTENCE: does the goal named appear in the registry's own goals list for
    the unit its ISSUE currently declares -- and if not, and the repo is one the unit already names,
    append it there. Nothing else. `feature_sync.sync_at_pick` (`work.start()`, #1473) remains the
    one place a FULL sync (live-branch reconciliation, `BRANCH_ABSENT`/`BRANCH_MISSING` judged,
    `<name>.md` regenerated) happens, and that module's own `main()` docstring is explicit that a
    second way to run IT would be a second answer. This is not that: it asks a single, narrower,
    LOCAL question a full sync never revisits once a goal's own pick is behind it
    (`_sync_registry`'s own docstring: "NOT REACHED ON A RESUME... the next PICK reconciles it") --
    so a goal whose label was attached or corrected AFTER its own pick (by the classifier on a later
    trigger, or by a human editing the issue) would otherwise sit unrecorded until some OTHER goal on
    the same unit happens to be picked next. This closes exactly that gap, and only that gap.

    THE REST READ, NOT THE GRAPHQL ONE. `fetch_body_labels_rest` (`sources.py`, #2435), never
    `fetch_body_labels` -- this runs on every goal-scoped verb, a far higher frequency than the
    once-per-pick `attach_at_pick` call that owns the GraphQL read, and GitHub's GraphQL budget is
    separately and more easily exhausted (#1209, #1808). Duck-typed on `UNIT_TRACKING_METHODS`, so
    `LocalSource` and any source predating this slice degrade to `ok` with no read at all.

    NO REPO, NO CHECK. `_configured_repo` reads `discovery.github.repo` directly -- never `git
    remote get-url`, which would spend a subprocess this side job has no call to make on a question
    `feature_sync.repo_slug` already answers authoritatively at the real pick. An adopter running
    `.sdlc/features/` without ever setting this key cannot be checked against (there is no address
    to look up in `repos{}`); degrading to `ok` here is honest, not silent, because the sync proper
    still reports `NO_REPO` loudly on the path that owns it.

    NEVER WIDENS A UNIT'S REPOS. If the unit is registered but this repo is not one its `repos{}`
    names, that is `feature_sync.is_scope_expansion`'s exact question, and the same one-directional
    rule applies here: warn, never write. The one write this function may EVER make is appending a
    goal number to a `repos[repo]` entry that ALREADY EXISTS -- structurally incapable of the
    expansion `_record_goal` itself refuses, because `_append` below looks the repo up by name
    rather than ever calling `setdefault` on it."""
    try:
        if not getattr(source, "no_dangling_goal_enabled", False):
            return UNIT_TRACKING_OK
        if not _has_unit_tracking_surface(source):
            return UNIT_TRACKING_OK
        try:
            adopted = feature_registry.registry_dir(sdlc_dir).is_dir()
        except Exception as exc:              # noqa: BLE001 - a filesystem check must never warn
            _note("sigma: features: unit-tracking's %s directory check for #%s did not run "
                  "(%s)\n" % (feature_registry.REGISTRY_DIRNAME, goal, exc))
            return UNIT_TRACKING_OK
        if not adopted:
            return UNIT_TRACKING_OK
        repo = _configured_repo(config)
        if not repo:
            return UNIT_TRACKING_OK
        try:
            issue = source.fetch_body_labels_rest(goal)
        except Exception as exc:              # noqa: BLE001 - a transient read is not evidence
            _note("sigma: features: unit-tracking could not read #%s (%s)\n" % (goal, exc))
            return UNIT_TRACKING_OK
        try:
            unit = features.read(issue).unit
        except features.AmbiguousUnit:
            return UNIT_TRACKING_OK           # already flagged elsewhere; not this function's job
        if not unit:
            return UNIT_TRACKING_OK           # nothing declared; not this function's job either
        features_dir = feature_registry.registry_dir(sdlc_dir)
        registry_now = feature_registry.read(features_dir)
        key = _resolve_registry_key(registry_now, unit)
        if key is None:
            return _warn_unit_tracking(sdlc_dir, source, goal, config, unit,
                "the label `%s` names a unit `.sdlc/features/` has no record of at all."
                % label_for(unit))
        entry = feature_registry.normalise_entry(registry_now[key])
        number = _goal_number(goal)
        current = _repo_goals(entry, repo)
        if current is not None and number is not None and number in current:
            return UNIT_TRACKING_OK           # already recorded -- the common, steady-state case
        if current is None:
            return _warn_unit_tracking(sdlc_dir, source, goal, config, key,
                "unit `%s` is registered, but its own `repos` block does not name `%s` -- widening "
                "it is the unit's owner's decision, not an automatic one, so nothing was written."
                % (key, repo))

        def _append(mutable, _repo=repo, _goal=goal):
            repos = mutable.get("repos") or {}
            match = next((k for k in repos if str(k).lower() == _repo.lower()), None)
            if match is None:
                return                        # the repo vanished between the two reads (nothing in
                                               # this codebase ever removes one) -- a no-op is correct
            mine = repos[match]
            mine["goals"] = list(mine.get("goals") or []) + [_goal]

        amended = _feature_sync().amend(sdlc_dir, key, _append)
        if amended.get("refused") or not amended.get("written"):
            return _warn_unit_tracking(sdlc_dir, source, goal, config, key,
                "its registry entry could not be repaired (%s)."
                % (amended.get("refused") or "the write did not land"))
        if not amended.get("changed"):
            return UNIT_TRACKING_OK           # a concurrent sync_at_pick recorded it first; fine
        _record(sdlc_dir, goal, config,
                "unit-tracking-repaired %s — goal added to repos.%s.goals" % (key, repo))
        return UNIT_TRACKING_REPAIRED
    except Exception as exc:                  # noqa: BLE001 - a side job must never raise
        _note("sigma: features: unit-tracking failed for #%s (%s)\n" % (goal, exc))
        return UNIT_TRACKING_WARNED

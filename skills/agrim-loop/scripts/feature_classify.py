#!/usr/bin/env python3
"""The 4-tier AI-judgment classifier, replacing #2263's `core` sentinel (#2363, slice B of epic
#2260's completion work, `.sdlc/plans/2260-dangling-completion.md`).

WHAT THIS REPLACES. `feature_labels._attribute_to_core` (deleted) wrote a COMMENT naming `core`
and touched no label, because `core` used to be a sentinel string the registry never checked (D-6
of `.sdlc/design/2253.md`). Slice A of this epic's completion work bootstrapped `core` as a REAL
registered unit -- a real branch, a real `feature:core` label, a real registry entry -- once,
deliberately, ahead of this slice, specifically so a classifier could exist that ATTACHES a real
label instead of narrating a name. That is what this module is for.

THE FOUR TIERS, STRICT ORDER, FIRST MATCH WINS, AND THE ONE RULE THAT GOVERNS ALL FOUR: this
module NEVER creates a new `feature:*` label, `feature/*` branch, or registry entry, at ANY tier.
Every write below is either an ATTACH of a label that is already real (routed through
`feature_labels.label_for` + `source.attach_label`, which structurally cannot create -- see
`sources._swap_labels`'s own docstring: it resolves every name to a node id first and RAISES
`unknown label(s) on this repo` rather than minting one), or an OVERLAY this codebase already had
before this module existed (`sdlc:needs-unit` via `feature_labels.mark_needs_unit`) or gains here
for the first time in the SAME shape (`sdlc:needs-triage` via `sources.mark_needs_triage`, itself
routed through `_ensure_labels`'s existing, `--force`-free, colour-preserving creation path -- see
that method's own docstring for why no separate `gh label create` call exists anywhere in this
tree for it).

  1. a SINGLE existing unit, open or closed (`feature_registry.resolve_any_unit`) -> attach it. A
     match on a CLOSED unit REOPENS it -- read-modify-write of the FULL entry, only `open` flips --
     the ONE deliberate, narrow exception `feature_registry.resolve_open_unit`'s own docstring
     already names and defers to this module.
  2. MULTIPLE plausible units, or root/base-level work -> attach to the CONFIGURED catch-all
     (`source.no_dangling_goal_core`, resolved via `resolve_open_unit` -- never the hard-coded
     string `"core"`, and never reopened: the catch-all is expected to already be open).
  3. an identifiable but UNREGISTERED component, with CONCRETE EVIDENCE (a real path this
     repository actually has) -> set aside under the EXISTING `sdlc:needs-unit` mechanism
     (`feature_labels._set_aside_no_unit`, extended -- not duplicated -- to thread a suggested
     name into the comment), never the issue body.
  4. genuinely unknown, expected RARE -> the NEW `sdlc:needs-triage` overlay
     (`sources.mark_needs_triage`), which makes the issue as unpickable as `sdlc:needs-unit`
     already is (`GitHubSource.overlay_labels`/`not_eligible_labels`), with an explanatory comment
     naming why every earlier tier failed.

NOTHING IS EVER WRITTEN TO THE ISSUE BODY. `features.parse_body` reads the body literally, so
writing anything there risks being mistaken for a genuine `Feature:` declaration on the very next
read -- exactly the hazard `_attribute_to_core`'s own (deleted) docstring warned about, and every
tier here inherits that same discipline: attach the LABEL, say what happened in a COMMENT.

THE JUDGMENT ITSELF IS INJECTED, NOT COMPUTED HERE. "Is this issue about unit X" is a
language-understanding question this module deliberately does not attempt in Python -- doing so
in string-match code would be a worse, more brittle design than delegating it. `Judgment` is the
shape a caller's answer takes; `classify()` is the deterministic tier chain given one, and
VALIDATES every part of it against the real registry and the real filesystem before acting on it
-- a judgment naming a unit that does not resolve, or evidence that does not exist on disk, is
simply not trusted for the tier it would otherwise satisfy, and the chain falls through to the
next tier rather than acting on an unverified claim.

THE TWO CALLERS, AND THE ONE DEFAULT THEY SHARE. `classify_at_pick` (called from
`feature_labels._handle_no_unit_at_pick`, itself invoked deep inside the deterministic,
non-interactive pick path with no live model connection available at that exact call) and
`classify_for_filing` (called from `handoff.create_tracked_issue`, the one place the kit opens an
issue on its own behalf) both default to `_default_judge` when no real judge is supplied. That
default NEVER guesses a specific unit or invents evidence -- the one thing this module's own
purpose forbids it from doing on its own -- it ABSTAINS (`ABSTAIN`, `multiple=True`), which is the
one tier answerable with zero semantic reasoning: attach to the configured catch-all, the same
safe, additive, always-reversible outcome `_attribute_to_core` used to describe in prose. A future
caller with a real judge (a driving Claude session that has actually read the issue and the
registry context `registry_context()` hands it) passes one explicitly and unlocks tiers 1 and 3.

Module shape follows `feature_stamp.py`: sibling modules loaded by file path, pure functions plus
fail-open stderr notes, reusing `feature_labels`' own `Decision`, `_flag`, `_record`, `label_for`
and `_set_aside_no_unit` rather than a second copy of any of them.
"""
import collections
import importlib.util
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


feature_registry = _load("feature_registry")   # resolve_any_unit / resolve_open_unit / read
#: #327 review block #2: the reopen is a registry WRITE, so it goes through `feature_sync.amend` --
#: the unit's lock and the one legacy-delta rule every Sigma write uses -- never `write_unit`
#: directly. No cycle: `feature_sync` loads neither this module nor `feature_labels`.
feature_sync = _load("feature_sync")
#: Eager, safe in this direction: `feature_labels.py` loads THIS module lazily (see its own
#: `_feature_classify` accessor), so this module loading `feature_labels` eagerly cannot recurse --
#: it never loads `feature_classify` itself at module scope, only inside a function.
feature_labels = _load("feature_labels")

Decision = feature_labels.Decision           # same shape every caller of `attach_at_pick` already reads

# --- outcomes ---------------------------------------------------------------------------------------
#: Tier 1: attached to a single existing OPEN unit. `proceed` is True.
TIER1_ATTACHED = "tier1-attached"
#: Tier 1: attached to a single existing unit that was CLOSED, and is now reopened. `proceed` True.
TIER1_REOPENED = "tier1-reopened"
#: Tier 1: the matched unit is CLOSED and reopening it was REFUSED (#327 review block #2): its
#: record is one the previous plugin wrote after the conversion, newer than `index.json`, and the
#: write would discard a value of it -- or the record is unreadable (#1565). Nothing was attached
#: and nothing written; `proceed` is False; the stderr line names the recovery.
TIER1_REOPEN_REFUSED = "tier1-reopen-refused"
#: Tier 1: the label exists and resolved, but the attach write itself did not land. Transient,
#: exactly like `feature_labels.REFUSED_WRITE_FAILED` -- no flag, no overlay, retried next pass.
TIER1_WRITE_FAILED = "tier1-write-failed"
#: Tier 2: attached to the configured catch-all unit. `proceed` is True.
TIER2_ATTACHED = "tier2-catchall-attached"
#: Tier 2: the catch-all's attach write did not land. Transient, same shape as `TIER1_WRITE_FAILED`.
TIER2_WRITE_FAILED = "tier2-catchall-write-failed"
#: Tier 3: set aside under `sdlc:needs-unit` with a suggested (unregistered) unit name. `proceed`
#: is False -- see `feature_labels.SET_ASIDE_NO_UNIT`, which this reuses verbatim as the outcome.
TIER3_SET_ASIDE = feature_labels.SET_ASIDE_NO_UNIT
#: Tier 4: every earlier tier failed; set aside under the NEW `sdlc:needs-triage` overlay.
#: `proceed` is False.
TIER4_NEEDS_TRIAGE = "tier4-needs-triage"

#: Idempotency markers for the flag comments this module posts directly (tier 1 and tier 2; tier 3
#: reuses `feature_labels.NEEDS_UNIT_MARKER` via `_set_aside_no_unit`, tier 4 gets its own below).
#: Same HTML-comment shape and the same reason as every marker in `feature_labels.py`: invisible in
#: the rendered issue, and `features.parse_body` never reads a comment at all.
TIER1_MARKER = "<!-- sigma:feature-classified-tier1 -->"
TIER2_MARKER = "<!-- sigma:feature-classified-tier2 -->"
TIER4_MARKER = "<!-- sigma:feature-classified-tier4 -->"

#: Ledger `why` tokens -- audit only, exactly like every token in `feature_labels.py`; nothing here
#: reads them back. FIRST word of the field, for the identical truncation reason `feature_labels.py`
#: documents on its own tokens.
TIER1_TOKEN = "feature-classify-tier1"
TIER2_TOKEN = "feature-classify-tier2"
TIER4_TOKEN = "feature-classify-tier4"


def _note(message):
    """One stderr line, never an exception -- the same shape and the same reason as every `_note`
    in this family: a diagnostic must never be the thing that breaks a pick or a filing."""
    try:
        sys.stderr.write(message)
    except Exception:                     # noqa: BLE001 - a diagnostic must never break a pick
        pass


# --- the judgment: injected, not computed here -------------------------------------------------------

#: The shape a caller's classification decision takes. `unit`: the single existing unit name the
#: judgment is confident this issue belongs to (open or closed; validated against the real registry
#: before anything acts on it -- see `classify`), or `None`. `multiple`: True when the judgment
#: found more than one plausible existing unit, OR judged this root/base-level (cross-cutting) work
#: -- either reading selects tier 2, and `unit` is ignored when this is True (tier 1 requires a
#: SINGLE match). `evidence_name`/`evidence_path`: a suggested NEW unit name paired with a real
#: path/directory in this repository that backs it -- tier 3 requires BOTH, and `classify` verifies
#: `evidence_path` actually exists on disk before trusting either.
Judgment = collections.namedtuple("Judgment", "unit multiple evidence_name evidence_path")


def make_judgment(unit=None, multiple=False, evidence_name=None, evidence_path=None):
    """Construct a `Judgment`. A thin convenience, not a requirement -- any caller (a test, a future
    live judge) may build the namedtuple directly; this just spells the common case."""
    return Judgment(unit, multiple, evidence_name, evidence_path)


#: The conservative default: no unit named, `multiple=True`. This is the ONE tier answerable with
#: zero language understanding -- "attach to the configured catch-all" -- so a caller with no real
#: judgment available abstains into it rather than fabricating a tier-1 guess (a specific unit) or
#: a tier-3 guess (evidence for a component nobody looked for). See the module docstring's "THE TWO
#: CALLERS" section for where this is actually used.
ABSTAIN = Judgment(None, True, None, None)


def _default_judge(sdlc_dir, source, goal, config, context):
    """The classification performed when no real judge is supplied. See `ABSTAIN`'s own docstring:
    this never guesses, it abstains -- the caller-visible contract is that classification without a
    real judge behaves exactly like the old `core` sentinel did (undeclared work lands on the
    catch-all), except now via a REAL, reversible label attach rather than a comment alone."""
    return ABSTAIN


def registry_context(sdlc_dir):
    """-> `{name: {"title": ..., "open": ...}}` for every REGISTERED unit, open or closed. The
    context a real judge reviews before deciding -- "every registered unit's title/description",
    per this slice's own issue -- and nothing more: never touches the filesystem beyond the
    registry itself, never raises (inherits `feature_registry.read`'s own total read side)."""
    registry = feature_registry.read(feature_registry.registry_dir(sdlc_dir))
    return {name: {"title": entry.get("title") or "", "open": entry.get("open") is True}
            for name, entry in registry.items()}


# --- tier 1: a single existing unit, open or closed --------------------------------------------------

def _tier1_text(unit, reopened):
    label = feature_labels.label_for(unit)
    reopen_clause = ""
    if reopened:
        reopen_clause = (
            " That unit was previously CLOSED and has been reopened as part of this "
            "classification — every other field on its registry entry (owner, repos, goals, "
            "priority, parent, tracking issue) was preserved exactly as it was; only `open` "
            "changed.")
    return (
        "%s\n"
        "**Sigma classified this goal under `%s`.** No unit of work was declared anywhere on "
        "this issue; Sigma's own judgment matched it to a single existing unit and attached "
        "`%s` — attaching an existing label is additive and reversible, and Sigma never "
        "creates one.%s"
        % (TIER1_MARKER, unit, label, reopen_clause))


def _tier1(sdlc_dir, source, goal, config, judgment):
    """-> `Decision`, or `None` when tier 1's preconditions are not met (falls through to tier 2).

    VALIDATES the judgment against the real registry before trusting any part of it:
    `resolve_any_unit` (open or closed) is the ONE lookup this uses, exactly as this slice's own
    issue specifies -- a judgment naming a unit that does not actually resolve is not a tier-1
    match, it falls through, it is never treated as evidence for any other tier either."""
    if judgment.multiple or not judgment.unit:
        return None
    resolved = feature_registry.resolve_any_unit(sdlc_dir, str(judgment.unit))
    if not resolved:
        return None
    label = feature_labels.label_for(resolved)
    reopened = False
    entry = feature_registry.read(feature_registry.registry_dir(sdlc_dir)).get(resolved)
    if isinstance(entry, dict) and entry.get("open") is not True:
        # THE ONE DELIBERATE REOPEN, per `feature_registry.resolve_open_unit`'s own docstring (the
        # exception it names and defers to this module). Through `feature_sync.amend` (#327 review
        # block #2): a read-modify-write of the FULL entry under the unit's lock -- only `open`
        # flips -- and the one legacy-delta rule, so a record the previous plugin wrote after the
        # conversion is never silently replaced. BEFORE the attach: a refused reopen attaches
        # nothing, so the goal is never left labelled onto a unit that stayed closed.
        try:
            amended = feature_sync.amend(sdlc_dir, resolved,
                                         lambda e: e.__setitem__("open", True))
        except (OSError, ValueError) as exc:   # `InvalidUnitName`, or a write that failed
            amended = {"refused": "%s: %s" % (type(exc).__name__, exc), "written": False}
        if amended.get("refused") or not amended.get("written"):
            _note("sigma: features: tier 1 classification matched #%s to %s, which is CLOSED, "
                  "and reopening it was refused -- nothing attached, nothing written; the pick is "
                  "refused this pass and retried on the next: %s\n"
                  % (goal, resolved, amended.get("refused") or "the write did not land"))
            return Decision(False, TIER1_REOPEN_REFUSED, resolved)
        reopened = True
    if not source.attach_label(goal, label):
        _note("sigma: features: tier 1 classification matched #%s to %s but the attach did "
              "not land — refusing this pass; the next pass retries it%s. No overlay and no flag: "
              "the label exists, so this is the write that failed, not a human's problem to "
              "solve\n" % (goal, label, " (the unit was reopened and stays open)" if reopened
                             else ""))
        return Decision(False, TIER1_WRITE_FAILED, resolved)
    feature_labels._flag(source, goal, TIER1_MARKER, _tier1_text(resolved, reopened))
    feature_labels._record(
        sdlc_dir, goal, config,
        "%s %s%s — classified by AI judgment, single confident match"
        % (TIER1_TOKEN, label, " reopened" if reopened else ""))
    return Decision(True, TIER1_REOPENED if reopened else TIER1_ATTACHED, resolved)


# --- tier 2: multiple plausible units, or root/base-level work -> the configured catch-all -----------

def _tier2_text(unit):
    label = feature_labels.label_for(unit)
    return (
        "%s\n"
        "**Sigma classified this goal under the `%s` catch-all.** No unit of work is declared "
        "anywhere on this issue, and Sigma's own judgment found either more than one plausible "
        "existing unit, or judged this root/base-level (cross-cutting) work — rather than a single "
        "confident match, so it was attached to the configured catch-all `%s` "
        "(`discovery.no_dangling_goal.core`) instead. Attaching an existing label is additive and "
        "reversible; Sigma never creates one. To put this goal under a more specific unit "
        "instead, declare one before its next pick."
        % (TIER2_MARKER, unit, label))


def _tier2(sdlc_dir, source, goal, config, core):
    """-> `Decision`, or `None` when tier 2's preconditions are not met (falls through to tier 3).

    `core` is resolved via `resolve_open_unit` -- NEVER `resolve_any_unit` -- deliberately: the
    catch-all is bootstrapped once, ahead of this feature, specifically to stay open; reopening it
    automatically the way tier 1 reopens an ORDINARY matched unit is not this tier's job, and
    `resolve_open_unit` answering `None` for a closed name is exactly the refusal this tier wants
    if the catch-all was ever closed by hand."""
    if not core:
        return None
    resolved = feature_registry.resolve_open_unit(sdlc_dir, str(core))
    if not resolved:
        _note("sigma: features: the configured catch-all %r is not a known OPEN unit in this "
              "repository's registry — tier 2 cannot attach #%s to it; falling through to the "
              "remaining tiers\n" % (core, goal))
        return None
    label = feature_labels.label_for(resolved)
    if not source.attach_label(goal, label):
        _note("sigma: features: tier 2 classification would attach #%s to the catch-all %s but "
              "the attach did not land — refusing this pass; the next pass retries it\n"
              % (goal, label))
        return Decision(False, TIER2_WRITE_FAILED, resolved)
    feature_labels._flag(source, goal, TIER2_MARKER, _tier2_text(resolved))
    feature_labels._record(sdlc_dir, goal, config,
                           "%s %s — classified by AI judgment, catch-all (multiple or root-level)"
                           % (TIER2_TOKEN, label))
    return Decision(True, TIER2_ATTACHED, resolved)


# --- tier 3: an identifiable but unregistered component, with concrete evidence ----------------------

def _evidence_exists(sdlc_dir, evidence_path):
    """Is `evidence_path` a REAL path THIS REPOSITORY actually has -- not merely a real path
    somewhere on the host? The one deterministic check tier 3 performs -- "concrete evidence", per
    this slice's own issue, means something code can verify, not merely something a judgment
    asserts. Resolved against the PROJECT ROOT (the directory `.sdlc` sits in,
    `handoff.project_root`'s own definition) when relative; an absolute path is used as given.

    issue #2402 follow-up (live-judge review): existence alone is not containment. A relative
    `../../etc/passwd` and an absolute `/etc/passwd` both used to pass this check -- the model only
    gets asked, in the prompt, to name a real path IN the repo, and a hallucinated or adversarially
    injected path outside it would have confirmed tier 3 and then been quoted verbatim in a public
    GitHub comment (`feature_labels._needs_unit_text`), turning this into a file-existence oracle
    for the whole host. `.resolve()` collapses `..`/symlinks BEFORE the containment check (not
    after `.exists()`, which would let a not-yet-created traversal path slip through unresolved),
    and `is_relative_to` is exact-prefix, not a substring match a sibling directory sharing a
    prefix could spoof. Never raises: a malformed path (embedded NUL, too long) degrades to "no
    evidence" exactly like every other filesystem check in this family."""
    try:
        project_root = pathlib.Path(sdlc_dir).resolve().parent
        candidate = pathlib.Path(str(evidence_path))
        if not candidate.is_absolute():
            candidate = project_root / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(project_root):
            return False
        return candidate.exists()
    except (OSError, ValueError):
        return False


def _tier3(sdlc_dir, source, goal, config, judgment):
    """-> `Decision`, or `None` when tier 3's preconditions are not met (falls through to tier 4).

    Requires BOTH `evidence_name` and `evidence_path`, and the path must actually EXIST in this
    repository -- absent either, this is not "identifiable but unregistered", it is unknown, and
    the chain falls through to tier 4 exactly as this slice's own issue specifies. Reuses
    `feature_labels._set_aside_no_unit` UNCHANGED as the mechanism (the overlay, the ledger record,
    the self-heal all stay one definition), threading the suggestion into its comment only --
    never the issue body."""
    if not judgment.evidence_name or not judgment.evidence_path:
        return None
    if not _evidence_exists(sdlc_dir, judgment.evidence_path):
        return None
    return feature_labels._set_aside_no_unit(
        sdlc_dir, source, goal, config,
        suggested=(str(judgment.evidence_name), str(judgment.evidence_path)))


# --- tier 4: genuinely unknown, expected rare ---------------------------------------------------------

def _tier4_text():
    return (
        "%s\n"
        "**Sigma could not classify this goal and has set it aside.** No unit of work is "
        "declared anywhere on this issue, and every tier of automatic classification was tried:\n\n"
        "- **tier 1** (a single existing unit) — no confident match to one registered unit.\n"
        "- **tier 2** (the configured catch-all) — the judgment found no clear single-or-root-level "
        "reading to defer, or the catch-all itself could not be resolved.\n"
        "- **tier 3** (an identifiable but unregistered component) — no concrete evidence (a real "
        "path in this repository) was found to back a suggested new unit name.\n\n"
        "This is expected to be rare. This issue keeps `sdlc:goal` and carries `sdlc:needs-triage` "
        "while it waits, so nothing reports it as ready to pick and no later slot re-reads it. "
        "Declare a unit by hand, or attach an existing `feature:<name>` label, to make it pickable "
        "again."
        % TIER4_MARKER)


def _tier4(sdlc_dir, source, goal, config):
    """The final tier: every earlier one failed. `proceed` is False.

    Degrades to the tier-3 mechanism (`_set_aside_no_unit`, no suggestion) when `source` has no
    `mark_needs_triage` -- a source that predates #2363 (or a test double that has not grown the
    method) must not leave the issue silently pickable; the existing `sdlc:needs-unit` overlay is
    the closest available fallback, the same "duck-type, degrade, never raise inside the pick path"
    discipline every sibling function in this family already follows."""
    if not hasattr(source, "mark_needs_triage"):
        _note("sigma: features: tier 4 classification for #%s has no mark_needs_triage on this "
              "source — falling back to the sdlc:needs-unit overlay instead\n" % goal)
        return feature_labels._set_aside_no_unit(sdlc_dir, source, goal, config)
    landed = source.mark_needs_triage(goal) is True
    if not landed:
        _note("sigma: features: the sdlc:needs-triage overlay on #%s did not land — the pick "
              "is still refused and the goal stays in the queue; the next pass retries it\n" % goal)
        return Decision(False, TIER4_NEEDS_TRIAGE, None)
    feature_labels._flag(source, goal, TIER4_MARKER, _tier4_text())
    feature_labels._record(sdlc_dir, goal, config,
                           "%s — every classification tier tried and failed" % TIER4_TOKEN)
    return Decision(False, TIER4_NEEDS_TRIAGE, None)


# --- the chain itself, and the two integration points -------------------------------------------------

def classify(sdlc_dir, source, goal, config, core, judgment=None):
    """The deterministic 4-tier chain, given an ALREADY-DECIDED `judgment` (or `ABSTAIN` when
    `None`). Pure scaffolding: every tier VALIDATES its part of `judgment` against the real
    registry or the real filesystem before acting, and never creates a `feature:*` label, a
    `feature/*` branch, or a registry entry at any tier -- see the module docstring."""
    judgment = judgment if judgment is not None else ABSTAIN
    decision = _tier1(sdlc_dir, source, goal, config, judgment)
    if decision is not None:
        return decision
    decision = _tier2(sdlc_dir, source, goal, config, core)
    if decision is not None:
        return decision
    decision = _tier3(sdlc_dir, source, goal, config, judgment)
    if decision is not None:
        return decision
    return _tier4(sdlc_dir, source, goal, config)


def classify_at_pick(sdlc_dir, source, goal, config, core, judge=None):
    """The pick-time integration point: `feature_labels._handle_no_unit_at_pick`'s "core
    configured" branch. `judge` is an optional `(sdlc_dir, source, goal, config, context) ->
    Judgment` callable; `None` (every caller today) uses `_default_judge`, which abstains -- see
    the module docstring's "THE TWO CALLERS" section."""
    context = registry_context(sdlc_dir)
    judgment = (judge or _default_judge)(sdlc_dir, source, goal, config, context)
    return classify(sdlc_dir, source, goal, config, core, judgment)


def classify_for_filing(sdlc_dir, source, config, core, judge=None, issue_title=None,
                         issue_body=None):
    """-> a unit NAME to target a brand-new issue with, or `None`. The filing-time integration
    point (`handoff.create_tracked_issue`), and deliberately narrower than `classify_at_pick`:

    TIER 1/2 EQUIVALENT ONLY. An issue that does not exist yet cannot be "set aside" (tier 3) or
    flagged unpickable (tier 4) — there is nothing on the board for either overlay to attach to,
    and inventing a pre-filing version of either would be a materially bigger step than this slice
    takes. A `None` return here means the filing proceeds exactly as it already does when nothing
    is inherited or targeted: with no unit.

    NEVER REOPENS. Filing-time targeting resolves through `resolve_open_unit`, the SAME function
    `target_unit`'s own long-standing contract (#1820) already uses -- a closed unit is never
    retargeted, explicitly or automatically, and extending the pick-time tier-1 reopen carve-out to
    a filing nobody has reviewed yet would widen that contract, not merely reuse it.

    `issue_title`/`issue_body` (#2383, slice H of epic #2260's completion work): the new issue's
    OWN title/body text -- the one thing filing time genuinely lacks that pick time always has (a
    real issue on the board to fetch via `source`). Both default to `None`, and when BOTH are
    `None` (every caller before this slice, and any caller today that genuinely has nothing to
    offer) `context` is built EXACTLY as `registry_context()` alone already produces it -- no extra
    key, byte-identical to before this slice, pinned by an explicit regression test. When EITHER is
    given, `context["_pending_issue"] = {"title": issue_title, "body": issue_body}` is added
    alongside the registry entries `registry_context()` already supplies -- a documented,
    internal-use key that `feature_judge.live_judge` reads to classify this pre-filing content
    directly instead of abstaining outright on `goal is None` (see that function's own docstring
    for the read side of this contract, including why it strips the key back out before building
    its own prompt). `judge`'s own call signature is UNCHANGED by this -- still exactly
    `(sdlc_dir, source, goal, config, context)`, and `goal` is still always `None` here; only
    `context` gains this one extra key. `classify_at_pick` is a completely separate function that
    never builds this key at all, so pick-time classification is untouched by this parameter's
    mere existence -- an explicit regression test pins that too."""
    context = registry_context(sdlc_dir)
    if issue_title is not None or issue_body is not None:
        context["_pending_issue"] = {"title": issue_title, "body": issue_body}
    judgment = (judge or _default_judge)(sdlc_dir, source, None, config, context)
    if judgment.unit and not judgment.multiple:
        resolved = feature_registry.resolve_open_unit(sdlc_dir, str(judgment.unit))
        if resolved:
            return resolved
    if core:
        resolved = feature_registry.resolve_open_unit(sdlc_dir, str(core))
        if resolved:
            return resolved
    return None

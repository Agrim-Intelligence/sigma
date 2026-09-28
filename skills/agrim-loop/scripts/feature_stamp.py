#!/usr/bin/env python3
"""Stamp the unit onto every issue Sigma files itself (#1471, L3, epic #1464, story #1427).

`features.py` (L0) READS a declaration and mutates nothing. `feature_labels.py` (L1) acts on one of
its verdicts at pick time. This is the third half of the same model, and it is the one the chain
actually depends on: a unit is only real if it survives the work NOBODY WROTE BY HAND. A follow-up
finding, a cross-area hand-off, a decomposition meta-issue -- each is opened by Sigma from a goal
that belongs to a unit, and each that is filed without that unit ends the chain silently.

THE CHOKEPOINT IS INHERITED, NOT REBUILT. `handoff.create_tracked_issue` is documented as "the one
real place the kit ever opens an issue on its own behalf", and that sentence is the only reason
`sdlc:followup` is reliably present rather than an optional `--label` a caller has to remember. The
unit is resolved and stamped THERE, from the filing goal, for exactly the same reason: an inherited
unit each caller has to remember to pass is an inherited unit that goes missing. Callers pass
nothing; this module answers "which unit is the goal I am filing FROM in?" itself.

THE TWO HALVES ARE WRITTEN BY DIFFERENT MEANS, AND THE ASYMMETRY IS DELIBERATE:

  - THE BODY MARKER is composed INTO the new issue's body before it is created, so it is atomic with
    the issue existing at all. There is no window in which a machine-filed issue is live on the
    board declaring nothing.
  - THE LABEL is attached AFTER creation, through `sources.attach_label` (#1468), never handed to
    `create_dependency`'s `labels=`. That looks like the weaker choice and is the safer one:
    `gh issue create --label feature:x` FAILS THE WHOLE CREATE when the label does not exist on the
    repository, so the atomic-looking route loses the entire follow-up in exactly the case this
    module has to survive -- a unit whose label a human has not made yet, or a conflicted parent
    whose body names a unit the label side never had. Attaching afterwards degrades to `body_only`
    instead: the issue exists, it declares its unit where a human reads it, and `feature_labels.
    attach_at_pick` attaches the label when that follow-up is itself picked. The failure is
    self-healing rather than lossy.

    That the create really does abort before the mutation was read out of `gh`'s own source rather
    than assumed (v2.97.0): `pkg/cmd/issue/create/create.go:404` resolves metadata via
    `pkg/cmd/pr/shared/params.go:106` -> `LabelsToIDs`, which returns
    `api/queries_repo.go:818`'s `'%s' not found`, and only `create.go:410` issues `IssueCreate`.
    Label resolution genuinely precedes the create mutation, so an absent label loses the whole
    follow-up with nothing to retry against. It is also why `reconcile_labels` DROPS a
    caller-supplied label that merely restates the inherited unit instead of passing it through.

    It also inherits #1468's never-create guarantee BY CONSTRUCTION rather than by a second rule:
    `attach_label` routes through `_swap_labels`, which resolves every name to a node id first and
    RAISES `unknown label(s) on this repo` rather than minting one, and `GitHubSource._run` refuses
    `gh label create` for a `feature:*` argument on every path regardless. Nothing here needs to
    know that; it simply never asks for a label to be created.

WHY THE WRITER VERIFIES ITS OWN OUTPUT THROUGH THE READER. `features.py` rule 5 does not parse
fenced or four-space-indented content AT ALL, because every issue in this epic -- and every doc that
teaches the format -- DISPLAYS the marker inside a fence in order to show it, and a parser that read
hidden content would make each of them declare a unit and demand a branch that does not exist. The
consequence for a WRITER is direct and silent: a marker that lands inside a fence, an indented block
or an HTML comment is invisible, the issue reads as declaring nothing, and there is no error
anywhere. That is not hypothetical here -- `_visible` blanks an UNTERMINATED fence or comment TO
EOF, so a body that ends inside one (a caller-supplied template showing an example, most obviously)
swallows anything appended after it.

So `stamp_body` does not trust its own placement. It composes a candidate, feeds it back through
`features.parse_body`, and only returns it if the reader genuinely reads the unit out again. If the
natural placement (appended, after the prose) does not read back, it tries the top of the body --
where no construct can yet be open -- and if THAT does not read back either it returns the body
UNSTAMPED with a warning. Shipping an issue whose marker nothing can see, while reporting success,
is the one outcome worth more than a lost marker.

THE FOUR THINGS THIS DELIBERATELY DOES NOT DO:

  1. IT NEVER STAMPS AN ISSUE SIGMA DID NOT JUST OPEN. `create_tracked_issue`'s duplicate-reuse
     path resolves to a PRE-EXISTING issue; it may already belong to another unit, and a second
     declaration would make it raise `AmbiguousUnit` for every reader afterwards. The reuse is
     reported instead.
  2. IT NEVER OVERRULES A DECLARATION THE CALLER ALREADY MADE -- IN EITHER OF THE TWO PLACES ONE
     CAN BE MADE. In the BODY (`stamp_body`): same unit -> nothing to do; a RIVAL unit -> the body
     is left exactly as the caller wrote it AND the label is withheld too. In the LABELS
     (`reconcile_labels`): a caller-supplied `feature:*` naming a different unit withholds both
     inherited halves the same way. Checking only the body was the first version's real bug --
     `handoff track --label feature:billing` filed an issue carrying two rival labels and a marker,
     which is precisely the unresolvable state this rule exists to never manufacture, and it did it
     with an empty warnings list. Inheritance is a default; an explicitly passed label is an
     instruction, and an instruction beats a default.
  3. IT NEVER FAILS A FILING. Every path degrades to "filed without a unit", which is precisely the
     pre-#1471 behaviour, and says so through `create_tracked_issue`'s existing `warnings` channel.
     SAID PRECISELY, because "exactly as before" is true of one thing and not of another: the ISSUE
     a unit-less goal produces is identical -- same title, same body, same labels, and no label
     write of any kind -- while the gh CALL LIST gains exactly one entry, the `issue view <goal>
     --json body,labels` that `unit_of` performs on every filing whether or not a unit comes back.
     That one read is the entire cost of this feature. It is paid on the filing path rather than
     the pick path, and it is paid by every adopter today, since no repo has units yet.
  4. IT DOES NOT COVER `agrim-scope`'s `compile_plan`. That module opens issues through
     `sources.create_dependency` directly and deliberately (see its own docstring: there is no
     "calling goal" for `create_tracked_issue`'s markers to be about), so it has no unit to inherit
     FROM -- it is a human turning an idea into a plan, not Sigma filing from work in flight.
     Stated rather than silently assumed away.

Module shape follows `feature_labels.py`: sibling modules loaded by file path, pure functions plus
one fail-open stderr note, no dependency the loop does not already carry.
"""
import collections
import importlib.util
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


features = _load("features")             # the read half (#1465): the constants AND the verifier
feature_labels = _load("feature_labels")  # the attach half (#1468): `label_for`, and never-create

#: `body` is what to file; `unit` is the unit the issue may be LABELLED with -- None when it must not
#: be (see rule 2 above), which is not the same thing as the body having no marker; `warnings` are
#: `create_tracked_issue`'s own channel, already printed by both CLI verbs.
Stamp = collections.namedtuple("Stamp", "body unit warnings")

#: `reconcile_labels`' answer: the label list to actually file with, the unit that survived the
#: caller's own labels, and anything a human needs told.
Reconciled = collections.namedtuple("Reconciled", "labels unit warnings")


def _note(message):
    """One stderr line, never an exception -- the same shape and the same reason as
    `features._note` and `feature_labels._note`: a diagnostic must never be the thing that breaks a
    filing."""
    try:
        sys.stderr.write(message)
    except Exception:                     # noqa: BLE001 - a diagnostic must never break a filing
        pass


def marker(unit):
    """The two BARE lines an issue declares its unit with.

    Built from `features.BODY_KEY` / `features.BRANCH_KEY` / `features.BRANCH_PREFIX`, never from
    literals: the reader builds its own regexes from those same constants precisely so that the
    writing half and the reading half are one definition. A literal here would be a second one, and
    this is exactly where the two would drift apart without anything failing.

    BARE, and that word is the whole specification. No fence, no indentation, no list marker, no
    block quote -- `features.py` rules 1 and 5 make every one of those unreadable."""
    return "%s: %s\n%s: %s%s" % (features.BODY_KEY, unit,
                                 features.BRANCH_KEY, features.BRANCH_PREFIX, unit)


def _reads_as(text, unit):
    """Does `features.parse_body` genuinely read `unit` back out of `text`? The verification step,
    run against the REAL reader rather than against a second opinion about what it does.

    `AmbiguousUnit` counts as "no" rather than propagating: a candidate placement that makes the
    body contradict itself is a placement to reject, not an error to raise at a caller who only
    asked for an issue to be filed."""
    try:
        return features.parse_body(text) == unit
    except features.AmbiguousUnit:
        return False


def _appended(text, block):
    """The natural placement: after whatever the caller wrote, separated by a blank line.

    The blank line is not cosmetic -- without it the marker joins the preceding paragraph as a lazy
    continuation line when rendered, so a human reading the issue sees a sentence rather than a
    declaration, which is the entire reason the body half exists."""
    return (text.rstrip() + "\n\n" + block + "\n") if text.strip() else block + "\n"


def _prepended(text, block):
    """The fallback placement: the very top, where no fence and no comment can yet be open, so
    nothing above it can hide it. Used only when `_appended` did not read back."""
    return (block + "\n\n" + text.lstrip("\n")) if text.strip() else block + "\n"


def stamp_body(body, unit):
    """-> `Stamp`. Put `unit`'s marker into a body Sigma is about to file. Never raises.

    `Stamp.unit` is the unit the caller may now also LABEL the issue with, and it is None in the one
    case where the body already names a different unit: a label that disagreed with the body would
    manufacture the conflict state on an issue Sigma itself just opened. `Stamp.body` may come
    back unchanged with a non-empty `warnings` -- that is the honest outcome for a body no placement
    can make readable, and it is strictly better than filing a marker nothing can see."""
    text = body if isinstance(body, str) else ""
    unit = str(unit)
    try:
        already = features.parse_body(text)
    except features.AmbiguousUnit as exc:
        return Stamp(text, None, [
            "not stamping unit %r onto this issue: the body it was filed with already contradicts "
            "itself (%s), and neither half may be written onto an issue no reader can resolve"
            % (unit, exc)])
    if already is not None:
        if already.lower() == unit.lower():
            return Stamp(text, unit, [])          # a caller already said it; say it once, not twice
        return Stamp(text, None, [
            "not stamping unit %r onto this issue: the body it was filed with already declares %r, "
            "and a second declaration -- or a label disagreeing with it -- is the one state nothing "
            "downstream can resolve" % (unit, already)])
    block = marker(unit)
    for candidate in (_appended(text, block), _prepended(text, block)):
        if _reads_as(candidate, unit):
            return Stamp(candidate, unit, [])
    _note("sigma: features: could not place a readable %s: marker in this issue's body\n"
          % features.BODY_KEY)
    return Stamp(text, unit, [
        "filed without the body marker for unit %r: no placement of it reads back as a declaration "
        "(an unterminated code fence or a contradicting `%s:` line in the body, most likely) -- "
        "filing it with no marker rather than with one nothing can read"
        % (unit, features.BRANCH_KEY)])


def _declared_by(label):
    """The unit ONE label declares, or None -- answered by `features.parse_labels` itself rather
    than by a second copy of its filter.

    Called one label at a time on purpose: `parse_labels` raises `AmbiguousUnit` for two DISTINCT
    units in one list, and a caller who typed two rival labels by hand is not a question this
    function is asking. Per-label it cannot raise, and it applies the reader's exact rule -- the
    `feature:` prefix case-insensitively, and a suffix that is a legal unit name -- so a label the
    reader would ignore is ignored here too."""
    return features.parse_labels([label])


def reconcile_labels(labels, unit):
    """-> `Reconciled(labels, unit, warnings)`. Settle a CALLER-SUPPLIED `feature:*` label against
    the unit inherited from the filing goal. Never raises.

    THE SECOND PLACE A RIVAL COMES FROM, and the one the first version of this module missed.
    `stamp_body` looks for a rival declaration in the BODY it was handed; a caller can just as
    easily hand one in as a LABEL -- `handoff track --label feature:billing` reaches
    `create_dependency(labels=...)` directly. Filing that alongside an inherited `voice-interview`
    produced an issue carrying two rival `feature:` labels AND a body marker, which is exactly the
    state `features.read` raises `AmbiguousUnit` on, silently and with an empty `warnings`. Worse
    than a no-unit filing, and a REGRESSION: before any of this existed the same command filed a
    perfectly readable `label_only` issue on `billing`.

    So the same rule the body rival already gets, applied one level out:

      - THE CALLER DISAGREES -> the caller wins, and BOTH inherited halves are withheld. `unit`
        comes back None (no marker, no attach), `labels` is returned untouched, and the warning
        names both sides. This restores the pre-inheritance outcome exactly: the issue declares
        whatever the caller asked for, and nothing else. Inheritance is a default, and an explicit
        instruction beats a default -- overruling the caller here would be this module deciding it
        knows better than the person who typed the label.
      - THE CALLER RESTATES THE SAME UNIT -> the label is DROPPED from the list, silently, because
        the stamp already owns both halves and the drop is what makes it safe. Handing it to
        `create_dependency` puts it on `gh issue create --label`, which resolves every label BEFORE
        the create mutation and aborts the whole call on one it cannot find (`gh` v2.97.0:
        `create.go:404` -> `params.go:106` -> `queries_repo.go:818` `'%s' not found`) -- so a
        redundant label would reintroduce the exact lossy route this module exists to avoid, for
        no gain. The label still lands, via `attach` after creation.
      - NO UNIT WAS INHERITED -> nothing is touched at all. A caller filing into a unit the goal
        does not belong to is making a deliberate choice, and it is the pre-#1471 behaviour."""
    if not unit:
        return Reconciled(list(labels), unit, [])
    rivals, restatements = [], []
    for label in labels:
        declared = _declared_by(label)
        if declared is None:
            continue
        (restatements if declared.lower() == str(unit).lower() else rivals).append(label)
    if rivals:
        return Reconciled(list(labels), None, [
            "not stamping unit %r onto this issue: it was filed with %s, which names a different "
            "unit -- an explicitly passed label beats an inherited one, so the issue declares what "
            "was asked for and neither inherited half is written" % (unit, ", ".join(
                "`%s`" % r for r in rivals))])
    kept = [l for l in labels if l not in restatements]
    return Reconciled(kept, unit, [])


def declared_units(labels, inherited):
    """-> the units the FILED ISSUE will actually declare, in order, deduplicated case-insensitively.

    #1479. `reconcile_labels` answers "which unit does this filing INHERIT"; this answers "which
    unit will the issue on the board CARRY", and the two are not the same question on the one path
    that matters. Both of that function's `unit=None` returns leave the caller's `feature:<name>`
    label sitting in `labels` -- deliberately, because an explicit label beats an inherited default
    -- so an issue is created declaring a unit while `unit` is None. A gate asked about `unit`
    therefore sees nothing to gate, and `handoff track --label feature:<x>` filed directly
    actionable work under somebody else's unit with no check, no warning and nobody told. Measured
    on the recorded `create_dependency` call before this existed.

    THE INHERITED UNIT WINS OUTRIGHT when there is one, and that is not a shortcut: in that case
    `reconcile_labels` has already dropped any restatement from `labels` (a redundant label aborts
    the whole `gh issue create`), so the labels no longer name it and `attach()` adds it after
    creation. Reading the list would answer "no unit" about an issue that certainly has one.

    MORE THAN ONE IS RETURNED RATHER THAN RESOLVED. Two rival `feature:` labels is a state
    `features.read` refuses outright (`AmbiguousUnit`), so there is no right answer to pick; a
    caller gating on this should apply its rule to EVERY one and let the most restrictive win,
    which is the only reading that cannot be walked past by adding a second label. Per-label
    parsing via `_declared_by`, so this cannot raise on exactly that input."""
    if inherited:
        return [inherited]
    out, seen = [], set()
    for label in labels or ():
        declared = _declared_by(label)
        if declared is None or declared.lower() in seen:
            continue
        seen.add(declared.lower())
        out.append(declared)
    return out


def unit_of(source, goal):
    """-> `(unit, warnings)`. Which unit does the goal this issue is being filed FROM belong to?

    None -- today's behaviour, exactly -- for a goal that declares nothing, a source with no unit
    surface at all (`LocalSource`), a read that failed, and a goal that contradicts itself. Every
    one of those is a filing that proceeds without a unit rather than a filing that does not happen.

    ONE `gh issue view` PER FILING, and no cache. That is the whole cost, and it is paid on a path
    that is already opening an issue over the network, not on the pick path. The alternative --
    threading the unit down from `loop._next()`, which already resolved it at pick -- was rejected
    for the reason this module exists at all: `create_tracked_issue` has six callers, and a unit
    that travels as an optional argument is a unit that arrives as None from whichever caller was
    written next.

    A CONFLICTED GOAL HANDS DOWN ITS BODY'S UNIT, because that is what `features.read` resolves a
    conflict to and L3 must not be the one level of the model that resolves it differently. Said out
    loud in a warning, because the child then agrees with one half of a parent that disagrees with
    itself."""
    if source is None or not hasattr(source, "fetch_body_labels"):
        return None, []
    try:
        issue = source.fetch_body_labels(goal)
    except Exception as exc:              # noqa: BLE001 - a failed read is not a failed filing
        return None, ["could not read goal %s to inherit the unit it belongs to (%s) -- filing "
                      "this issue with no unit, exactly as before units existed" % (goal, exc)]
    try:
        verdict = features.read(issue)
    except features.AmbiguousUnit as exc:
        return None, ["goal %s contradicts itself about which unit it belongs to (%s) -- filing "
                      "this issue with no unit; the goal's own issue needs a human edit before "
                      "anything can inherit from it" % (goal, exc)]
    if verdict.state == features.CONFLICT:
        return verdict.unit, [
            "goal %s declares unit %r in its body but carries the label %s -- the issue filed from "
            "it inherits the BODY's unit, which is what a conflict resolves to everywhere else"
            % (goal, verdict.body, feature_labels.label_for(verdict.label))]
    return verdict.unit, []


def attach(source, issue, unit):
    """-> `warnings`. Attach `unit`'s label to an issue Sigma has just filed. Never raises.

    Deliberately thin: `sources.attach_label` (#1468) already owns the retries, the loud-but-non-
    raising failure, and the structural never-create guarantee. There is nothing to add except the
    one sentence a human needs when it does not land -- which is that NOTHING IS LOST, because the
    body marker is the authoritative half and `feature_labels.attach_at_pick` attaches the label the
    first time this follow-up is itself picked."""
    label = feature_labels.label_for(unit)
    if not hasattr(source, "attach_label"):
        return ["filed #%s declaring unit %r in its body, but this backlog source cannot attach "
                "%s" % (issue, unit, label)]
    try:
        landed = source.attach_label(str(issue), label)
    except Exception as exc:              # noqa: BLE001 - the issue already exists; never undo that
        landed, detail = False, " (%s)" % exc
    else:
        detail = ""
    if landed:
        return []
    return ["filed #%s but could not attach %s%s -- the issue still declares the unit in its body, "
            "and the label is attached the first time it is picked. Sigma never creates a "
            "feature label; a human creates the first label of a unit" % (issue, label, detail)]

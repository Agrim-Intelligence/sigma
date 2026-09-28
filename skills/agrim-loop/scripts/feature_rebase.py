#!/usr/bin/env python3
"""Rebase upkeep for a long-lived feature branch -- integration -> feature, then the feature's goal
branches onto the rebased feature (#1476, epic #1464, story #1427).

L5 of the design's dependency order, L3 of the epic. `feature_sync` (#1473) reconciles what the
registry CLAIMS against the branches that exist; this moves the branches themselves. Without it a
feature branch is never brought up to date at all: anything landing on the integration branch is
simply not reflected in an in-flight unit, and the divergence is discovered as a wall of conflicts
at the very end, when it is most expensive.

WHAT IS ACTUALLY NEW HERE, because most of the replay already exists. `work.rebase()` handles a
GOAL branch and is reused verbatim for that half -- fetch, `rebase --autostash`, `--force-with-lease`,
abort on any failure, with the narrow CHANGELOG union rescue. It cannot handle the FEATURE branch,
because every line of it starts from a work RECORD (`_record`) and therefore from a worktree, and a
feature branch has neither. So the new machinery is exactly one thing: replaying a branch nobody has
checked out, in an EPHEMERAL detached worktree that is removed in a `finally` whatever happens.

--------------------------------------------------------------------------------------------------
THE RULE THIS DEPENDS ON, AND WHY IT IS CHECKED RATHER THAN ASSUMED

    Nobody commits directly to a feature branch. All work reaches it through `sdlc/*` goal branches.

Rebasing rewrites published history, which normally forces every developer holding that branch to
hard-reset and puts their uncommitted work at risk. That risk disappears entirely when no human is
ever holding commits there -- Sigma owns the `sdlc/*` worktrees and re-bases them itself. The
discipline therefore buys clean history at no cost, PROVIDED THE RULE HOLDS, and #1481 states it in
every participating repo's agent instructions.

An instruction is not an enforcement. The failure mode the rule exists to prevent is specific and
quiet -- somebody works once without Sigma, commits straight onto the feature branch, and the
next force-push rewrites the branch under them. So this module does not TRUST the rule: before it
force-pushes anything it MEASURES whether every commit on the branch arrived through a pull request,
and a branch that fails that check is surfaced as work and left exactly as it was. The commits are
not in danger of being lost (a rebase replays them); what is in danger is the person holding them,
and that is not a risk this pass may take on their behalf.

`arrived_through_a_pull_request` is the whole classifier, and its evidence is the SUBJECT LINE of
each commit on the branch's own first-parent line:

  * `merge_method: squash` (the kit's default) lands each goal as ONE commit titled `<PR title>
    (#N)` -- GitHub writes the reference itself;
  * `merge_method: merge` lands each goal as a merge commit titled `Merge pull request #N from ...`;
  * `merge_method: rebase` preserves the goal branch's ORIGINAL commit messages, so nothing on the
    branch carries a reference and there is NO LOCAL EVIDENCE. That is reported as `UNVERIFIABLE`
    and the branch is not touched -- see `_verifiable`. "No local evidence" rather than "no
    evidence": `gh pr list --base feature/<unit> --state merged` knows which pull requests landed
    there, and this module already resolves the slug. Matching rewritten shas back to those pull
    requests is genuinely hard, which is why declining is still the right answer here -- but it is
    a hard follow-up, not an impossibility, and saying the stronger thing would foreclose it.

`--first-parent` is load-bearing rather than an optimisation. A `merge_method: merge` landing brings
the goal branch's own `sdlc: <n>` commits along as SECOND parents; walking every commit would report
each of them as a direct commit and disable upkeep on every repo that merges rather than squashes.
The first-parent line is exactly "what was put ON this branch", which is exactly the question.

The classifier's honest limits, stated rather than discovered later: a human whose commit message
happens to end in `(#123)` is accepted, and a repo that squashes with a custom title template that
drops the reference reads as if every landing were direct. The first is a false negative on a
deliberate imitation; the second fails towards REFUSING, which costs an upkeep pass and never data.

--------------------------------------------------------------------------------------------------
THE OTHER DIRECTION OF THE SAME HAZARD: OTHER PEOPLE'S GOAL BRANCHES

Replaying the unit's `sdlc/*` branches rewrites published history too, and the set is not history:
`finish()` unlinks a goal's work record and keeps its branch, so a record EXISTING means the goal
has not finished. The goals this pass can see are the unit's LIVE FLEET, and on a parallel drain
that is every sibling agent working the same unit -- each pick would force-push k-1 of their remote
tips and move their worktree HEADs underneath them.

A CLEAN WORKING TREE IS NOT AN ANSWER TO THAT QUESTION. An agent that has just committed and is
about to open its PR has a clean tree, and the check is a TOCTOU besides. The question is liveness,
this repo already has markers for it, and `goal_liveness` reads exactly those -- the same
`agent_alive`/`claim_lock_alive` pair `work._blocked_by_a_live_foreign_agent` is built on.

The rule is POSITIVE EVIDENCE OF IDLENESS, not absence of evidence of life: `agent_watch` and the
ledger are both opt-in, so "no marker" is the ordinary state of a stock config and reading it as
"nobody is there" is precisely how a replay walks into a live worktree. A goal with no evidence is
skipped and SAID. That costs less than it sounds: `work.merge()` already rebases a goal's own
branch reactively when GitHub reports it BEHIND, driven by the process that OWNS it, so a skipped
goal is deferred to its owner rather than neglected.

--------------------------------------------------------------------------------------------------
CONFLICTS ARE WORK, NOT AN ERROR STATE

A conflict at either level files a tracked issue naming the two branches, through
`handoff.create_tracked_issue` -- "the one place the kit ever opens an issue on its own behalf", so
this inherits its labels, its de-duplication and (via #1471) its unit stamping rather than opening a
bare `gh issue create` nobody can find. It is filed `blocks_goal=False`: a conflict on some OTHER
unit's branch is not a reason to park the goal that happened to trigger the pass, and
`create_tracked_issue`'s own docstring names that exact false-blocking bug. And
`immediately_actionable=False`: resolving two divergent histories is a judgement about intent, which
is what the `sdlc:needs-confirmation` tier is for.

AND THE TREE IS LEFT CLEAN, WHICH IS THE HARDER HALF. A half-applied rebase poisons every later goal
in the run, and an unattended loop has nobody to notice. `work.rebase()`'s abort-on-any-failure
discipline is preserved exactly for the goal level; the feature level adds the two failures a
worktree-less replay can have that a worktree-full one cannot -- a stranded worktree and a leftover
directory -- and closes both in a `finally`, with a `prune` behind the `remove` so even a `remove`
that fails cannot leave an admin record behind.

--------------------------------------------------------------------------------------------------
WHY THE LOCK IS SEPARATE FROM `feature_sync`'s, AND WHY IT FAILS THE OTHER WAY

Two picks on the same unit at once is not exotic -- it is what a parallel drain produces by design.
`feature_sync` answers that with a per-unit `flock` and fails OPEN, because the worst case there is
a lost goal number in a record. Here the worst case is two concurrent rebases of one branch, so this
fails CLOSED: no lock, no pass. The lock file is a different one -- `state/features/rebase-locks/
<name>.lock`, its own directory since #1577 -- because the two are held for very different durations:
sharing it would let a seconds-long rebase time out `feature_sync`'s 10s registry write and push it
onto its own unserialised path. See `LOCK_DIRNAME` for why the separation is a directory rather than
the distinguishing suffix it used to be.

The one exception is a platform with no `fcntl` at all (Windows), where refusing outright would
disable the feature rather than protect anything. There the pass runs, reports `serialised: False`
IN THE CLAUSE the operator reads, and is bounded by two things -- one inherited, one built for this
case:

  * `--force-with-lease` still refuses a second writer's push, because it is a property of the push
    rather than of the lock;
  * a worktree already sitting at the ephemeral path STOPS the pass (`OCCUPIED`) instead of being
    deleted. That is a real refusal, and it replaces a claim this module used to make and not keep:
    it said `git worktree add` refuses an existing path, which never got the chance because the
    pre-add drop deleted the path first -- measured, a second unserialised pass destroyed a first
    pass's live worktree. With a lock held, an existing worktree cannot be another pass's and is
    dropped; without one, nothing here can tell the two apart, so it is left alone.

A PROCESS KILLED OUTRIGHT RUNS NO `finally`, which is why the stale-worktree drop lives at the top
of `_upkeep`, under the lock, rather than inside `_rebase_feature`. Every early return -- `current`
above all, which is the overwhelmingly common outcome -- would otherwise return before the recovery
and leave a registered worktree with a half-applied rebase in the adopter's repo forever.

Three paths still return above it, and all three are "this pass cannot act at all": `not-adopted`
and `no-unit` have no unit name to key the path on, so the recovery is not merely skipped but
unaddressable; `busy` is positively right, because a pass holding the lock is the one thing that
CAN be inside that worktree. `disabled` is the one an operator could reach on purpose -- turning
`rebase_upkeep: off` immediately after a crash keeps the strand -- and it is left that way
deliberately: an off switch that still runs git is not off. The config's own `_rebase_upkeep` note
says so, with the one command that clears it.

--------------------------------------------------------------------------------------------------
WHERE THIS RUNS

`work.start()`, immediately after `_sync_registry` and BEFORE the `git fetch` that cuts the
worktree. The order is the whole point: the goal is about to be cut from `<remote>/feature/<unit>`,
so bringing the feature forward first is what makes the new goal start current instead of inheriting
the staleness this module exists to remove. Advisory in both directions -- it never raises, and
`work` guards the call besides, because losing the GOAL over a branch-maintenance pass would be a
much worse trade than a stale branch.

Module shape follows its siblings: zero third-party dependencies, module-level constants, loaded via
`_load("feature_rebase")`.
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


features = _load("features")
registry = _load("feature_registry")
sync = _load("feature_sync")

#: `work` imports THIS module (lazily, for the same reason), so a module-level load here would be a
#: cycle -- the shape `handoff._load_sibling` already uses for `blockers`. Cached after the first
#: call; tests substitute it directly.
_WORK = None
#: `handoff` pulls in `sources`, `mirror` and `dedup`. Loading that on every pick, on the chance that
#: a rebase might conflict, is a real cost for a path that is a no-op almost every time.
_HANDOFF = None


def _work():
    global _WORK
    if _WORK is None:
        _WORK = _load("work")
    return _WORK


def _handoff():
    global _HANDOFF
    if _HANDOFF is None:
        _HANDOFF = _load("handoff")
    return _HANDOFF


# --------------------------------------------------------------------------- constants

#: `.sdlc/state/rebase/<unit>` -- the ephemeral detached worktree. Under `state/` because
#: `setup.RUNTIME_IGNORES` already covers it, and because `.sdlc/features/` is deliberately NOT
#: gitignored (a gitignored backup is not a backup), so a scratch checkout there would be untracked
#: litter in every adopter's `git status`.
WORKTREE_DIRNAME = "rebase"
#: #1577: THE REBASE LOCK HAS A DIRECTORY OF ITS OWN, `state/features/rebase-locks/<unit>.lock`, and
#: the directory is the fix rather than a cleverer suffix. It used to sit directly in
#: `state/features/` as `<unit>.rebase.lock`, told apart from `feature_sync`'s `<unit>.lock` by that
#: suffix alone -- but `.rebase` is a perfectly legal way to end a unit name, so unit `alpha.rebase`'s
#: SYNC lock and unit `alpha`'s REBASE lock were the same file, byte for byte. Two unrelated units
#: serialised against each other, and since this lock fails CLOSED, a sync write on one of them
#: reported the other as `BUSY` -- "another pass is already rebasing" about a pass that never existed.
#:
#: A sentinel character outside `features._UNIT_RE` would separate the two namespaces today, but only
#: for as long as that character class stays narrow -- and it is documented as a DELIBERATE narrowing
#: of what git accepts as a branch segment, so widening it later is a real edit somebody will make. A
#: directory does not depend on the charset at all. It is also the precedent already set one module
#: along: `feature_registry` gave the shards `units/` for exactly this reason, because `index` is a
#: legal unit name.
LOCK_DIRNAME = "rebase-locks"
LOCK_SUFFIX = ".lock"
#: ONE non-blocking attempt. A pick that WAITS on another process's rebase is a pick that stalls the
#: whole loop for a git operation it does not need; the other process is already doing the work.
LOCK_TIMEOUT = 0.0

DEFAULT_REMOTE = "origin"
DEFAULT_MERGE_METHOD = "squash"
#: The one merge method that leaves no trace of the pull request on the landed commits.
REBASE_MERGE = "rebase"

ON = "on"
OFF = "off"

#: `goal_liveness`'s three answers. `UNKNOWN` is NOT `IDLE`: see that function.
LIVE = "live"
IDLE = "idle"
UNKNOWN = "unknown"

#: What this pass did about the finding it made. A CLOSED SET, and a field rather than an
#: inference, because the clause used to ASSERT a filing unconditionally ("filed as work", "see the
#: filed issue") -- and `handoff.create_tracked_issue` never raises, so a `gh` outage came back as
#: `issue: None` and the assertion became a lie an operator went looking for.
NO_FILING = "none"                # nothing to file
FILED = "filed"                   # an issue exists, and `report["issues"]` names it
ALREADY_FILED = "already-filed"   # this exact finding was filed on an earlier pick; see `_told_before`
FILING_FAILED = "not-filed"       # it was attempted and did not land -- it will be attempted again
#: Worst-first. With several findings in one pass a single field has to report the one a human has
#: to act on, and a partial failure must never be hidden behind a sibling's success.
_FILING_RANK = (FILING_FAILED, FILED, ALREADY_FILED, NO_FILING)

WHY_LIVE = "a live agent or claim is working it"
WHY_NO_EVIDENCE = "no evidence it is idle -- its own merge path will rebase it when GitHub says BEHIND"

#: Outcomes. Exactly one is reported, and everything except `REBASED` means the branches were not
#: touched at all.
DISABLED = "disabled"                 # `work.rebase_upkeep: "off"`
NO_UNIT = "no-unit"                   # the goal declares none -- today's behaviour, unchanged
NOT_ADOPTED = "not-adopted"           # no `.sdlc/features/`; this project has not adopted the model
NO_BASE = "no-base"                   # no integration branch configured, or it IS this branch
NO_BRANCH = "no-branch"               # the unit has no feature branch yet -- the definition-of-done no-op
OCCUPIED = "occupied"                 # a worktree sits at the ephemeral path and nothing can prove it stale
REMOTE_UNREADABLE = "remote-unreadable"   # the branch list could not be read; nothing was judged
CURRENT = "current"                   # the feature already carries the integration branch
BUSY = "busy"                         # another pass holds this unit's rebase lock
UNVERIFIABLE = "unverifiable"         # the no-direct-commits rule cannot be checked here
DIRECT_COMMITS = "direct-commits"     # it CAN be checked, and the rule is broken
CONFLICT = "conflict"                 # the feature branch would not replay
LEASE_REFUSED = "lease-refused"       # the remote moved under us; the other writer keeps the branch
REBASED = "rebased"                   # the feature moved (its goal branches are in `replayed`)
FAILED = "failed"                     # something went wrong; nothing here is claimed
OUTCOMES = (DISABLED, NO_UNIT, NOT_ADOPTED, NO_BASE, NO_BRANCH, OCCUPIED, REMOTE_UNREADABLE,
            CURRENT, BUSY, UNVERIFIABLE, DIRECT_COMMITS, CONFLICT, LEASE_REFUSED, REBASED, FAILED)

#: The outcomes worth a clause on `work.start()`'s own one-line result -- the ONLY channel any of
#: this reaches a person through on a normal run.
#:
#: `FAILED` IS IN THIS LIST, and its absence was a real defect rather than a style choice. The old
#: comment here claimed the outcomes outside the list were "either the ordinary world or already on
#: stderr (`FAILED`)"; only the RAISED path ever wrote to stderr, so every `FAILED` that
#: `_rebase_pass` RETURNED wrote nothing anywhere -- and "upkeep force-pushed a shared branch and
#: the remote refused" was therefore indistinguishable from "upkeep did nothing".
#:
#: `REMOTE_UNREADABLE` is deliberately still out: `feature_sync` files its own `remote-unreadable`
#: divergence from the SAME `live_branches` call on the SAME pick and puts it in ITS clause, so
#: repeating it here would report one unreachable remote twice on one line.
IN_CLAUSE = (BUSY, OCCUPIED, UNVERIFIABLE, DIRECT_COMMITS, CONFLICT, LEASE_REFUSED, REBASED, FAILED)

#: The area label every issue this module files carries. Not a CODEOWNERS lookup -- these are filed
#: `same_area=True`, so `area` is a label and nothing else.
AREA = "work"

#: GitHub's own two traces of a pull request on a landed commit. `\Z`-anchored for the squash form
#: because `(#12)` in the MIDDLE of a subject is a reference to an issue, not the merge that made
#: this commit; `\A`-anchored for the merge form because that is where GitHub writes it.
_SQUASH_PR_RE = re.compile(r"\(#[0-9]+\)[ \t]*\Z")
_MERGE_PR_RE = re.compile(r"\AMerge pull request #[0-9]+\b")

#: How far a failed git call's own error text is quoted into `report["why"]`. Widened from 200
#: because NOTHING BRANCHES ON `why` ANY MORE -- it is a report field and issue-body quote, never a
#: decision input. It used to be both, and the combination was a live bug: `LEASE_REFUSED` was
#: decided by `"stale info" in why`, inside this truncation, with a measured 27 characters of
#: headroom against a 131-character remote URL. A longer remote URL demoted a lease refusal -- the
#: one outcome that means somebody else's work was just saved -- into the silent bucket. The
#: refusal is now decided by re-reading the remote tip (`_pushed`), which is a measurement.
_REASON_CHARS = 600

#: How far `why` is quoted into the ONE-LINE clause, which has a different job from the report.
_CLAUSE_WHY_CHARS = 140


def _note(message):
    """One stderr line, never an exception -- the shape every sibling on this epic holds, for the
    same reason: a diagnostic must never be the thing that breaks a pick."""
    try:
        sys.stderr.write(message)
    except Exception:                     # noqa: BLE001 - a diagnostic must never break a pick
        pass


#: `feature_sync._run`'s exact `(cwd, argv) -> stdout` contract, raising on a non-zero exit, which is
#: also `work._run`'s -- so a runner injected here can be handed straight to `work.rebase()`.
_run = sync._run
_acquire = sync._acquire
_release = sync._release


def _flat(exc):
    return " ".join(str(exc).split())[:_REASON_CHARS] or exc.__class__.__name__


# --------------------------------------------------------------------------- paths


def lock_path(sdlc_dir, name):
    """`.sdlc/state/features/rebase-locks/<name>.lock` -- the one place a unit NAME becomes a REBASE
    LOCK path, and a namespace of its own. See `LOCK_DIRNAME` for why the separation is a directory.

    Refuses the same names `feature_registry.unit_path` refuses, through the same predicate and with
    the same exception type -- this path is derived from the same string, so an escape here would be
    an escape there.

    #1577: FOLDED, by #1566's rule and for a sharper version of #1566's reason -- same string in,
    same address out. `feature_sync`'s lock fails OPEN, so losing it costs serialisation on a write;
    this one fails CLOSED and guards a force-push of a SHARED branch, so `Voice` and `voice` holding
    two different files means two concurrent rebases of one feature branch. AFTER the guard, never
    before, for #1566's reason: `is_unit_name` rejects `voice.lock` but accepts `voice.LOCK`, so
    folding first would turn an accepted name into the spelling of a rejected one."""
    if not (isinstance(name, str) and registry.is_unit_name(name)):
        raise registry.InvalidUnitName(
            "%r is not a unit name, so it cannot be locked -- a unit name is one git branch segment"
            % (name,))
    return (pathlib.Path(sdlc_dir) / "state" / sync.LOCK_DIRNAME / LOCK_DIRNAME
            / (name.lower() + LOCK_SUFFIX))


def worktree_path(sdlc_dir, name):
    """`.sdlc/state/rebase/<unit>` -- where the feature branch is replayed. Same refusal, same
    reason: this one becomes a REAL checkout, so a name that escapes would put a working tree
    outside `.sdlc/` entirely rather than merely misplacing a file.

    #1673: FOLDED, through `feature_registry.unit_key` rather than through a local `.lower()`, by
    #1566's rule. Unfolded, `Voice` and `voice` were two throwaway CHECKOUTS for one unit -- which
    costs more than a misplaced file: `_upkeep` clears a stale worktree on entry by asking
    `worktree_path` where it is, so a leftover left under the other spelling is one git still has
    registered and no pass will ever come back for.

    AFTER the guard, never before, and the refusal stays this site's own: `is_unit_name` rejects
    `voice.lock` but accepts `voice.LOCK`, so folding first would turn an accepted name into the
    spelling of a rejected one."""
    if not (isinstance(name, str) and registry.is_unit_name(name)):
        raise registry.InvalidUnitName(
            "%r is not a unit name, so it cannot be given a rebase worktree" % (name,))
    return pathlib.Path(sdlc_dir) / "state" / WORKTREE_DIRNAME / registry.unit_key(name)


# --------------------------------------------------------------------------- config


def _settings(config):
    got = config.get("work") if isinstance(config, dict) else None
    return got if isinstance(got, dict) else {}


def _remote(config):
    remote = _settings(config).get("remote")
    return remote if isinstance(remote, str) and remote.strip() else DEFAULT_REMOTE


def merge_method(config):
    got = _settings(config).get("merge_method")
    return (got.strip().lower() if isinstance(got, str) and got.strip() else DEFAULT_MERGE_METHOD)


def switch(config):
    """`work.rebase_upkeep` as on | off. Anything unrecognised is ON, which is deliberate and is the
    opposite of `work.review_mode`'s rule for `require_review`.

    The two defaults differ because the two mistakes differ. An unrecognised REVIEW setting must not
    block a merge the user never asked to gate. An unrecognised UPKEEP setting must not silently
    stop maintaining a branch: the branch then rots invisibly, which is the exact failure this whole
    goal exists to fix, and the design already rules that feature branches stay force-pushable
    precisely so Sigma can rebase them (§13). A typo should cost a surprise, not a silence --
    and every destructive step below is independently guarded anyway.

    That cite used to read §9, which is the MANAGED BLOCK and says nothing about protection (#1481).
    §13 is where the rule now lives, together with the cost of ignoring it: protecting `feature/*`
    against non-fast-forward pushes stops every pass here at the push, on every pick, forever."""
    value = _settings(config).get("rebase_upkeep")
    if value is False:
        return OFF
    if isinstance(value, str) and value.strip().lower() == OFF:
        return OFF
    return ON


# --------------------------------------------------------------------------- the rule's classifier


def arrived_through_a_pull_request(subject):
    """Does this commit's SUBJECT carry GitHub's own trace of the pull request that landed it?

    See the module docstring for the evidence model and its two named limits. Returns a bool and
    never raises -- a non-string subject is simply no evidence."""
    if not isinstance(subject, str):
        return False
    text = subject.strip()
    return bool(_SQUASH_PR_RE.search(text) or _MERGE_PR_RE.match(text))


def landed_commits(run, cwd, integration_ref, feature_ref):
    """Every commit put ON the feature branch since the integration branch -> `[(sha, subject)]`.

    `--first-parent` is the whole of the correctness here; see the module docstring. `%H %s` rather
    than a separator character because a sha is fixed-width and a subject cannot contain a newline,
    so `partition(" ")` is exact."""
    out = run(cwd, ["git", "log", "--first-parent", "--format=%H %s",
                    "%s..%s" % (integration_ref, feature_ref)])
    found = []
    for line in str(out or "").splitlines():
        sha, _, subject = line.strip().partition(" ")
        if sha:
            found.append((sha, subject))
    return found


def direct_commits(run, cwd, integration_ref, feature_ref):
    """The commits on the branch that no pull request accounts for -> `[{"sha", "subject"}]`."""
    return [{"sha": sha, "subject": subject}
            for sha, subject in landed_commits(run, cwd, integration_ref, feature_ref)
            if not arrived_through_a_pull_request(subject)]


def _verifiable(config):
    """Can the no-direct-commits rule be checked at all in this repo?

    False for `merge_method: rebase` ONLY, and the narrowness matters: that method rewrites the
    goal branch's commits onto the base preserving their ORIGINAL messages, so a landed goal and a
    hand-typed commit are byte-for-byte indistinguishable to any LOCAL check. Refusing there is the
    same posture `feature_sync.reconcile` takes when the remote could not be read -- an unanswered
    question is not an answer, and the answer this one is standing in for authorises a force-push.

    "No local evidence" is the honest phrasing, and it is not the same as "no evidence": the remote
    knows which pull requests landed on this branch (`gh pr list --base <branch> --state merged`).
    Reconciling rewritten shas back to them is hard enough that declining is still right today --
    but it is a follow-up somebody could do, and claiming impossibility would close that door."""
    return merge_method(config) != REBASE_MERGE


# --------------------------------------------------------------------------- measurements


def remote_tip(run, cwd, remote, branch):
    """What `branch` points at ON THE REMOTE right now, or None if that could not be read.

    Small, and load-bearing twice. It is how a refused push is told apart from a refused LEASE
    (`_pushed`), and it is what lets a filed issue REPORT the tip rather than assert it. `None` is
    "could not read", never "no such branch" -- the same distinction `live_branches` makes, for the
    same reason."""
    try:
        out = run(cwd, ["git", "ls-remote", "--heads", remote, branch])
    except Exception:                     # noqa: BLE001 - not knowing is never an answer
        return None
    for line in str(out or "").splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == sync.REF_PREFIX + branch:
            return sha.strip()
    return None


def rebase_stopped(run, path):
    """Did a rebase STOP in this tree, as opposed to a rebase command that failed to run?

    Asked of git, structurally: `rev-parse --git-path rebase-merge` names the directory git itself
    keeps a stopped rebase in, and it exists exactly while one is stopped. `rebase-apply` is the
    same answer for the `am` backend.

    THIS IS THE DIFFERENCE BETWEEN "CONFLICT" AND "FAILED", and guessing it from the exception text
    was wrong in a way a person felt: every exception out of `git rebase` used to be filed as an
    issue titled "Rebase conflict", including `[Errno 2] No such file or directory`. Fails CLOSED --
    a measurement that could not be made is not a conflict."""
    for name in ("rebase-merge", "rebase-apply"):
        try:
            got = str(run(str(path), ["git", "rev-parse", "--git-path", name]) or "").strip()
        except Exception:                 # noqa: BLE001 - unmeasurable is not "conflicted"
            return False
        if not got:
            continue
        here = pathlib.Path(got)
        if not here.is_absolute():
            here = pathlib.Path(str(path)) / here
        if here.exists():
            return True
    return False


def _same_path(a, b):
    try:
        return os.path.realpath(str(a)) == os.path.realpath(str(b))
    except OSError:
        return str(a) == str(b)


def leftovers(run, cwd, path):
    """What this pass MEASURABLY failed to clean up -> a list of sentences, empty when clean.

    The point is the empty list. The filed issue used to assert "no half-applied rebase, no
    stranded worktree" unconditionally, and a cleanup that refused -- a permission problem, the
    exact case `_drop_worktree`'s docstring claims to cover -- produced an issue asserting the
    opposite of what was on disk. An assertion nobody checked is worse than no assertion, because a
    human acts on it.

    Both halves are measured: the directory on disk, and whether git still has a worktree
    REGISTERED there (the two come apart -- a directory somebody deleted by hand leaves the
    registration behind, and a `remove` that refused leaves both)."""
    found = []
    if pathlib.Path(path).exists():
        found.append("the throwaway worktree at `%s` is still on disk" % path)
    try:
        listing = run(cwd, ["git", "worktree", "list", "--porcelain"])
    except Exception as exc:              # noqa: BLE001
        found.append("`git worktree list` could not be read (%s), so nothing could be verified"
                     % _flat(exc))
        return found
    for line in str(listing or "").splitlines():
        if line.startswith("worktree ") and _same_path(line[len("worktree "):].strip(), path):
            found.append("git still has a worktree registered at `%s`" % path)
            break
    return found


# --------------------------------------------------------------------------- who is working what


def goal_liveness(sdlc_dir, config, goal):
    """Is somebody working `goal` right now? -> `LIVE` | `IDLE` | `UNKNOWN`.

    NOT A SECOND OPINION. `LIVE` is decided by this repo's own purpose-built gate --
    `work._blocked_by_a_live_foreign_agent` over `loop.agent_threads`/`loop.agent_alive`, whose own
    refusal text says two agents in one worktree is unsafe -- plus `loop.claim_lock_alive`. Those
    are the two markers that track a genuine long-lived pid for exactly this question.

    `UNKNOWN` IS NOT `IDLE`, AND THAT IS THE WHOLE DESIGN. `agent_watch` and the ledger are both
    opt-in, so "no marker" is the ordinary state of a stock config, and reading it as "nobody is
    there" is how a replay walks into a live agent's worktree. The caller therefore requires
    POSITIVE EVIDENCE of idleness -- a marker that resolves DEAD, or a claim past its lease -- and
    treats everything else as not-to-be-touched. Fails towards inaction, like `pid_alive`'s own
    docstring argues for, and like every other refusal in this module.

    Never raises: an unreadable marker must not break a pick, and `UNKNOWN` is the correct answer
    when the question could not be asked."""
    work = _work()
    try:
        loop = _load("loop")
    except Exception:                     # noqa: BLE001 - no loop module, no evidence
        return UNKNOWN
    try:
        if work._blocked_by_a_live_foreign_agent(sdlc_dir, config, goal):
            return LIVE
    except Exception:                     # noqa: BLE001
        return UNKNOWN
    evidence = False
    try:
        for thread in loop.agent_threads(sdlc_dir, goal):
            state, _pid = loop.agent_alive(sdlc_dir, goal, config, thread=thread)
            if state == "alive":
                # Reachable only when the live pid is OUR OWN, which the gate above excludes by
                # design. For THIS question that is still `live`: a replay is unsafe whoever is in
                # the tree, and "it is me" is not a licence to rebase underneath myself.
                return LIVE
            if state == "dead":
                evidence = True
    except Exception:                     # noqa: BLE001
        return UNKNOWN
    try:
        claim, _pid = loop.claim_lock_alive(sdlc_dir, goal, config)
    except Exception:                     # noqa: BLE001
        claim = UNKNOWN
    if claim == "alive":
        return LIVE
    if claim == "dead":
        evidence = True
    return IDLE if evidence else UNKNOWN


# --------------------------------------------------------------------------- the ephemeral worktree


def _drop_worktree(run, cwd, path):
    """Remove the ephemeral worktree and every trace of it. Best-effort at every step, in the one
    order that is total: `remove` is the clean path; `rmtree` covers a `remove` that refused (a
    conflicted tree, a permission problem); `prune` then drops the admin record `rmtree` cannot
    reach. Skipping the prune is how a `remove` failure becomes a worktree git still believes in."""
    try:
        run(cwd, ["git", "worktree", "remove", "--force", str(path)])
    except Exception:                     # noqa: BLE001 - cleanup must never raise into a pick
        pass
    shutil.rmtree(str(path), ignore_errors=True)
    try:
        run(cwd, ["git", "worktree", "prune"])
    except Exception:                     # noqa: BLE001
        pass


def _rebase_feature(run, cwd, path, branch, base_ref, sha, remote, report):
    """Replay `sha` (the feature tip) onto `base_ref` in a throwaway detached worktree and push it.

    Returns the outcome. The `finally` is the point of the whole function: whatever happens -- a
    conflict, a refused lease, a raise from anywhere -- the worktree is gone before this returns.
    It is NOT the only place that cleans up any more: a process killed outright runs no `finally`
    at all, so `_upkeep` drops a stale worktree on entry, under the lock, before any early return.

    THE LEASE IS EXPLICIT, NOT IMPLICIT. `--force-with-lease` with no value compares against the
    remote-tracking ref, which is a cache of the last fetch and is exactly as stale as the fetch
    was -- and any concurrent `git fetch` in the same `.git` re-arms it against the racer's own new
    sha. `--force-with-lease=<branch>:<sha>` names the sha this replay was actually built on, so
    the lease asserts the one thing that makes the force-push safe: nothing landed between the read
    and the write.

    AND WHAT THE REFUSAL MEANS IS MEASURED, NOT READ OUT OF THE ERROR TEXT -- see `_pushed`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(cwd, ["git", "worktree", "add", "--detach", str(path), sha])
    except Exception as first:            # noqa: BLE001
        # ONE retry, behind a full drop. The shape this recovers is a REGISTRATION without a
        # directory -- a worktree somebody deleted by hand, which `_upkeep`'s entry drop cannot see
        # because it keys on the path existing. `prune` inside the drop is what clears it, and
        # without the retry that recovery would cost a whole extra pass.
        _drop_worktree(run, cwd, path)
        try:
            run(cwd, ["git", "worktree", "add", "--detach", str(path), sha])
        except Exception as exc:          # noqa: BLE001
            report["why"] = "%s (after retry: %s)" % (_flat(first), _flat(exc))
            _drop_worktree(run, cwd, path)
            return FAILED
    try:
        try:
            run(str(path), ["git", "rebase", base_ref])
        except Exception as exc:          # noqa: BLE001 - a conflict is an outcome to report
            # NO `rebase --abort` HERE, AND THAT IS MEASURED RATHER THAN OVERLOOKED. `work.rebase()`
            # aborts because its tree is the goal's real worktree and has to survive the failure;
            # this tree is thrown away by the `finally` below, and `git worktree remove --force`
            # deletes the in-progress rebase state along with everything else -- `prune` behind it
            # clears the admin directory the state actually lives in even if the remove refuses. A
            # mutation run proved the abort dead: removing it changed no observable behaviour on a
            # real conflict (tree clean, no `rebase-merge` anywhere, remote tip unchanged). One
            # mechanism that is exercised beats two where the second cannot be.
            report["why"] = _flat(exc)
            # A CONFLICT IS A THING GIT SAYS, not a thing an exception string suggests: every
            # failure out of this command used to be filed as an issue titled "Rebase conflict".
            return CONFLICT if rebase_stopped(run, path) else FAILED
        try:
            after = run(str(path), ["git", "rev-parse", "HEAD"]).strip()
        except Exception as exc:          # noqa: BLE001
            report["why"] = _flat(exc)
            return FAILED
        outcome = _pushed(run, cwd, path, branch, sha, remote, report)
        if outcome == REBASED:
            report["after"] = after
        return outcome
    finally:
        _drop_worktree(run, cwd, path)


def _pushed(run, cwd, path, branch, sha, remote, report):
    """Push the replayed head under an explicit lease -> `REBASED` | `LEASE_REFUSED` | `FAILED`.

    WHICH OF THE TWO FAILURES IT WAS IS A MEASUREMENT. The old test was `"stale info" in
    report["why"]` -- git's own wording, searched for inside a 600-character (then 200-character)
    truncation of the exception. That made the classification depend on the LENGTH OF SOMEBODY'S
    REMOTE URL: measured against a 131-character remote, `stale info` landed at index 173 of a
    200-character field, so a URL 28 characters longer demoted a lease refusal into `FAILED`.

    The question the classification is really asking -- "did the branch move under us?" -- has an
    answer on the remote, so ask the remote. A tip that is no longer the sha we leased is a refusal
    doing its job and the other writer keeping their commits; a tip that never moved is a push
    refused for some other reason entirely (branch protection, credentials, the network), which is
    a `FAILED` a human has to hear about. An unreadable tip is not evidence of a refusal, so it
    falls to `FAILED` -- which is now reported rather than silent."""
    try:
        run(str(path), ["git", "push", "--force-with-lease=%s:%s" % (branch, sha),
                        remote, "HEAD:refs/heads/%s" % branch])
        return REBASED
    except Exception as exc:              # noqa: BLE001
        report["why"] = _flat(exc)
        now = remote_tip(run, cwd, remote, branch)
        report["tip"] = now
        return LEASE_REFUSED if (now is not None and now != sha) else FAILED


# --------------------------------------------------------------------------- the goal branches


def _dirty(run, path):
    """Is there uncommitted work in this goal's worktree? Unreadable counts as dirty -- the question
    exists to protect somebody's in-flight edits, and a question that could not be answered is not
    permission to move their files."""
    try:
        return bool(run(str(path), ["git", "status", "--porcelain"]).strip())
    except Exception:                     # noqa: BLE001
        return True


def _goal_state(run, path, goal_branch, cwd, remote, before):
    """What is ACTUALLY true about a goal after its replay failed -> sentences for the issue body.

    `work.rebase()` aborts a CONFLICT, so "the tree is clean and the branch untouched" is true for
    that case -- but it is NOT true for every non-`rebased` return: its own post-rebase force-push
    can fail (work.py:1252), where nothing aborted and the local branch has already moved. Asserting
    the abort story for both is the same unmeasured claim `leftovers` exists to stop."""
    said = []
    try:
        dirt = str(run(str(path), ["git", "status", "--porcelain"]) or "").strip()
    except Exception as exc:              # noqa: BLE001
        said.append("the goal's worktree could not be read (%s)" % _flat(exc))
        dirt = None
    if dirt:
        said.append("its worktree has uncommitted or unmerged paths")
    elif dirt == "":
        said.append("its worktree is clean")
    if rebase_stopped(run, path):
        said.append("**a rebase is still stopped in it** -- finish or `git rebase --abort` it")
    now = remote_tip(run, str(cwd), remote, goal_branch)
    if now is None:
        said.append("its remote tip could not be read")
    elif before is not None and now != before:
        said.append("its remote tip HAS moved (`%s` -> `%s`)" % (before[:12], now[:12]))
    else:
        said.append("its remote tip is unchanged at `%s`" % (now[:12] if now else "?"))
    return said


def _replay_goals(sdlc_dir, config, goal, unit, branch, goals, run, report, cwd, remote):
    """Replay each of the unit's goal branches onto the freshly-rebased feature branch.

    `work.rebase()` DOES THE REPLAY, unchanged -- it already fetches, rebases `--autostash`,
    force-pushes with a lease and ABORTS on any failure, and it already targets `rec["base"]`, which
    for a goal that declared this unit IS `feature/<unit>`. Writing a second replay here would be a
    second opinion about what a safe rebase is, and the two would drift.

    WHAT THIS ADDS IS THE PRECONDITION, AND IT IS LIVENESS RATHER THAN TIDINESS. A work record
    exists exactly while a goal is UNFINISHED -- `finish()` unlinks it and keeps the branch -- so
    the set iterated here is the unit's LIVE FLEET, not its history, and on a parallel drain that
    is k-1 other agents per pick. Replaying one of their branches force-pushes their remote tip and
    moves their worktree HEAD underneath them: the same rewriting-under-a-holder hazard the
    no-direct-commits rule exists to prevent, arriving from the other side.

    So a goal is replayed only on POSITIVE EVIDENCE that nobody is working it -- `goal_liveness`
    over this repo's own `agent_alive`/`claim_lock_alive` markers. `UNKNOWN` is treated as
    not-to-be-touched, which on a stock config (both markers opt-in) means most goals are skipped.

    THAT COSTS ALMOST NOTHING, and the reason is worth stating rather than assuming: a skipped goal
    is not a neglected one. `work.merge()` already rebases a goal's own branch reactively when
    GitHub reports the PR BEHIND, driven by the process that OWNS that goal -- the safe direction.
    The replay here is an optimisation for branches whose owner has gone away, which is exactly the
    set `goal_liveness` can prove.

    A CLEAN TREE IS STILL REQUIRED, and it is still not sufficient. `--autostash` would stash
    somebody's uncommitted work, move every file and unstash; but an agent that has just committed
    and is about to open its PR has a clean tree, so this check is a necessary extra guard and a
    racy one, never the gate.

    A GOAL WITH NO WORK RECORD IS NOT "SKIPPED", IT IS OUT OF SCOPE, and the distinction is not
    pedantry. The registry records goal NUMBERS and `finish()` unlinks the record while keeping the
    branch, so on a mature unit most recorded numbers have no worktree at all -- counting each of
    them as a deferral would grow `skipped` without bound and drown the one entry that means
    something ("uncommitted work in progress"). The definition of done says goal WORKTREES, so a
    goal without one is passed over in silence. A goal that HAS a record and still was not replayed
    is a real deferral and is always said."""
    work = _work()
    for number in goals:
        name = str(number)
        if name == str(goal):
            continue                      # the pick that triggered this pass has no worktree yet
        try:
            rec = work._record(sdlc_dir, name)
        except Exception:                 # noqa: BLE001 - an unsafe goal id is simply not ours
            rec = None
        if not rec:
            continue                      # no worktree -- out of scope, not deferred; see above
        # The RECORD's own branch name, never one rebuilt from the prefix: `pr()` and `rebase()`
        # both act on `rec["branch"]`, so a report naming anything else would name a branch nothing
        # in this pass ever touched.
        goal_branch = rec.get("branch") or "%s%s" % (
            _settings(config).get("branch_prefix") or "sdlc/", name)
        if rec.get("base") != branch:
            report["skipped"].append({"branch": goal_branch,
                                      "why": "based on %r, not %s" % (rec.get("base"), branch)})
            continue
        path = pathlib.Path(rec.get("worktree") or "")
        if not path.is_dir():
            report["skipped"].append({"branch": goal_branch, "why": "no worktree on disk"})
            continue
        liveness = goal_liveness(sdlc_dir, config, name)
        if liveness != IDLE:
            report["skipped"].append({"branch": goal_branch,
                                      "why": WHY_LIVE if liveness == LIVE else WHY_NO_EVIDENCE})
            continue
        if _dirty(run, path):
            report["skipped"].append({"branch": goal_branch, "why": "uncommitted work in progress"})
            continue
        before = remote_tip(run, cwd, remote, goal_branch)
        try:
            # `work.rebase()` pushes with a BARE `--force-with-lease` (work.py:1245/1252) -- the
            # form this module argues against for the feature branch. Left as it is on purpose: an
            # `sdlc/*` branch has a single writer by construction, and changing that push would
            # change the reactive BEHIND path every goal in the repo already depends on. Named here
            # so the difference is a decision on the record rather than an omission.
            outcome = work.rebase(sdlc_dir, config, name, run=run)
        except Exception as exc:          # noqa: BLE001 - `rebase` is total, but a load is not
            outcome = "rebase deferred: %s" % _flat(exc)
        if str(outcome).startswith("rebased"):
            report["replayed"].append(goal_branch)
            continue
        report["conflicts"].append(goal_branch)
        _file_issue(sdlc_dir, config, goal, report,
                    slot="goal-conflict:%s" % goal_branch,
                    key="%s@%s" % (goal_branch, before or "?"),
                    title="Rebase conflict: %s onto %s" % (goal_branch, branch),
                    why="%s could not be replayed onto %s" % (goal_branch, branch),
                    body=_goal_conflict_body(unit, goal_branch, branch, outcome,
                                             _goal_state(run, path, goal_branch, cwd, remote,
                                                         before)))


# --------------------------------------------------------------------------- surfacing


FILED_SUFFIX = ".rebase-filed.json"


def filed_path(sdlc_dir, name):
    """`.sdlc/state/features/<unit>.rebase-filed.json` -- what this unit has already been told
    about, and at what fingerprint. Under `state/` because `setup.RUNTIME_IGNORES` covers it, and
    beside the SYNC lock for the same reason: it is runtime bookkeeping, not a record.

    It needs no directory of its own the way the rebase lock does (#1577): `.rebase-filed.json` and
    `.lock` cannot end the same string, so no unit name can make this path collide with either lock.
    `test_the_filed_store_can_never_be_a_lock_of_either_kind` asserts that rather than trusting it,
    because the argument is about suffixes and the suffixes are constants somebody can edit.

    #1577: FOLDED, by #1566's rule -- two stores here would mean the same conflict filed twice,
    once under each spelling. Do NOT read that as "two casings are one unit everywhere else", which
    is what this said and what turned out to be untrue three separate times (#1577, then #1638,
    then two more found while reconciling these docs). `worktree_path` in this very module folds too
    as of #1673, and with `feature_propagate.sibling_path` folded by #1672 the inventory's
    unfolded set is currently empty -- which is a MEASUREMENT of today, not a property of tomorrow.
    The measurement lives in `tests/test_feature_registry.py`; this docstring is not the count."""
    if not (isinstance(name, str) and registry.is_unit_name(name)):
        raise registry.InvalidUnitName("%r is not a unit name" % (name,))
    return pathlib.Path(sdlc_dir) / "state" / sync.LOCK_DIRNAME / (name.lower() + FILED_SUFFIX)


def _read_filed(sdlc_dir, unit):
    """The per-unit "already told them" store -> `{slot: fingerprint}`. Never raises."""
    try:
        path = filed_path(sdlc_dir, unit)
    except ValueError:
        return {}
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                     # noqa: BLE001 - absent or unreadable both mean "not told"
        return {}
    return seen if isinstance(seen, dict) else {}


def _told_before(sdlc_dir, unit, slot, fingerprint):
    """Has this exact finding ALREADY BEEN FILED SUCCESSFULLY? -> bool.

    A CONFLICT OR A DIRECT COMMIT PERSISTS UNTIL SOMEBODY FIXES IT, and this pass runs on every
    pick. `handoff`'s duplicate search stops a second ISSUE, correctly -- but it then posts a
    duplicate-context COMMENT on the existing issue and writes a ledger entry, every time. A
    finding that has not changed is not news, and telling a human the same thing forty times is how
    they stop reading.

    THE SLOT IS PASSED IN, NEVER DERIVED FROM DISPLAY TEXT. It used to be `title.split(":")[0]`, so
    every `Rebase conflict: ...` finding shared one slot -- measured, with two conflicting goal
    branches they overwrote each other's fingerprint on every pass and neither was ever suppressed
    (filings per pick `2, 2, 2` instead of `2, 0, 0`), leaving the mechanism inert in exactly the
    multi-goal drain that motivates it. A slot identifies the FINDING (its kind and its branch); a
    title is what a human reads, and re-deriving one from the other is how they drift.

    The fingerprint is the thing that would have to change for the finding to be different, so a
    branch that grows a second direct commit IS news and is filed again. Best-effort in both
    directions: an unreadable store degrades to filing, because under-reporting a force-push hazard
    is the worse failure -- and an UNWRITABLE one degrades to exactly the pre-suppressor behaviour
    (a duplicate-context comment per pick), which is noise rather than damage.

    NO READ-MODIFY-WRITE RACE TO GUARD. The whole pass runs under this unit's rebase lock, so the
    read here and the write in `_remember` sit inside one critical section by construction -- unlike
    `feature_registry`, whose write surface is reachable from passes holding no lock at all.

    THE TWO CONFLICT SLOTS ARE PREFIXED DISTINCTLY (`feature-conflict:` / `goal-conflict:`) rather
    than distinguished by the branch name alone: `branch_prefix` is configurable, so a project that
    set it to `feature/` could spell a goal branch and a unit branch identically."""
    return _read_filed(sdlc_dir, unit).get(slot) == fingerprint


def _remember(sdlc_dir, unit, slot, fingerprint):
    """Record that this finding WAS filed -- called ONLY once an issue actually exists.

    THE ORDER IS THE WHOLE FIX. The predecessor recorded and persisted the fingerprint and THEN
    returned "this is news", leaving `_file_issue` to attempt the filing afterwards. But
    `create_tracked_issue` never raises: a `gh` failure comes back as `issue: None`, so a filing
    that never happened still burned the fingerprint. Measured -- one failed filing, `gh` recovers,
    and zero further attempts are ever made at that tip while every later pick reports the finding
    as filed. Suppression must be a consequence of having told somebody, never of having tried."""
    seen = _read_filed(sdlc_dir, unit)
    seen[slot] = fingerprint
    try:
        path = filed_path(sdlc_dir, unit)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen, indent=2, sort_keys=True), encoding="utf-8")
    except (OSError, ValueError):         # noqa: S110 - bookkeeping must not cost the filing
        pass


def _rank(status):
    """Total by construction: an unrecognised status ranks last rather than raising. This is called
    from inside `_file_issue`, which is NOT individually guarded -- a `ValueError` here would
    surface as the whole pass reporting `FAILED` and losing the issue it had just filed."""
    return _FILING_RANK.index(status) if status in _FILING_RANK else len(_FILING_RANK)


def _set_filing(report, status):
    """Worst-first, so a partial failure across several findings is never hidden."""
    report["filing"] = min((report.get("filing", NO_FILING), status), key=_rank)


def _file_issue(sdlc_dir, config, goal, report, title, why, body, slot="", key=""):
    """File the finding as tracked work. Never raises, and never blocks the goal that found it.

    `handoff.create_tracked_issue` is the one place the kit opens an issue on its own behalf, so
    going through it is what makes this finding carry the kit's labels, #1471's unit stamp (the
    `feature:<name>` label and the body marker, inherited from the goal this was filed FROM) and its
    duplicate search -- which matters here more than usual: this pass runs on every pick and a
    conflict persists until somebody resolves it. Re-checked against the base rather than assumed:
    #1471 landed as `a6dc5771` under this goal, and `handoff` stamps at filing time only -- a REUSED
    duplicate is deliberately not stamped, and says so in its own warnings, which this function
    forwards to stderr.

    `why` IS THE ONE-LINE SUMMARY AND `body` IS THE MARKDOWN, and they are separate arguments
    because they have different readers: `why` is what reaches the ledger entry and the
    duplicate-context comment, both of which are one line, while `body` is the issue text. Passing
    the whole body as both would put a page of markdown into a ledger field that truncates.

    `blocks_goal=False`: the goal that triggered the pass is not the goal that conflicted, and
    `create_tracked_issue`'s own docstring names false-blocking as the bug that axis exists to
    prevent. `immediately_actionable=False`: reconciling two divergent histories is a judgement
    about intent, which is what `sdlc:needs-confirmation` is for.

    `slot` IDENTIFIES THE FINDING and `key` FINGERPRINTS ITS STATE, so an unchanged finding is
    filed once rather than every pick -- see `_told_before`. The clause still reports the finding on
    every pass; only the FILING is suppressed, and only once one has actually landed."""
    if key and slot and _told_before(sdlc_dir, report["unit"], slot, key):
        _set_filing(report, ALREADY_FILED)
        return
    try:
        result = _handoff().create_tracked_issue(
            sdlc_dir, config, goal, AREA, why,
            same_area=True, immediately_actionable=False, blocks_goal=False,
            title=title, body=body)
    except Exception as exc:              # noqa: BLE001 - a filing must never break a pick
        _set_filing(report, FILING_FAILED)
        _note("sigma: rebase upkeep: %s was not filed as an issue (%s); the finding is above "
              "and will be attempted again on the next pick.\n" % (title, _flat(exc)))
        return
    for warning in (result or {}).get("warnings") or []:
        _note("sigma: rebase upkeep: %s\n" % warning)
    issue = (result or {}).get("issue")
    if not issue:
        # NOT AN EXCEPTION, AND THAT IS THE POINT. `create_tracked_issue` never raises: no `gh`, an
        # outage, a repo it cannot write all come back as `issue: None`. Treating that as success is
        # what made the clause assert a filing nobody could find.
        _set_filing(report, FILING_FAILED)
        _note("sigma: rebase upkeep: %s could not be filed as an issue; the finding is above "
              "and will be attempted again on the next pick.\n" % title)
        return
    report["issues"].append(str(issue))
    _set_filing(report, FILED)
    if key and slot:
        _remember(sdlc_dir, report["unit"], slot, key)


def _aftermath(left, branch, tip, before):
    """The paragraph a human acts on: what was MEASURED after the failure, never what was assumed.

    Two branches, and the whole point is that both are earned. Clean is reported only when
    `leftovers` came back empty AND the tip read back at the sha we started from; anything else is
    reported as the thing that is actually there, with the command that clears it."""
    if left:
        return ("**This pass could not leave the checkout clean.** Measured afterwards:\n\n%s\n\n"
                "Clear it with `git worktree remove --force <path> && git worktree prune`.\n"
                % "\n".join("- %s" % one for one in left))
    if tip is None:
        return ("Measured afterwards: no worktree was left behind. `%s`'s tip on the remote could "
                "not be re-read, so whether it moved is unknown.\n" % branch)
    if before is not None and tip != before:
        return ("Measured afterwards: no worktree was left behind, but `%s` on the remote is now "
                "`%s`, not the `%s` this pass started from -- somebody else moved it.\n"
                % (branch, tip, before))
    return ("Measured afterwards: no worktree was left behind and `%s` still points at `%s` on the "
            "remote.\n" % (branch, tip))


def _feature_conflict_body(unit, branch, base, before, why, left, tip):
    return (
        "`%s` could not be replayed onto `%s`.\n\n"
        "%s\n"
        "Reproduce and resolve:\n\n"
        "```\ngit fetch origin %s %s\ngit switch %s\ngit rebase origin/%s\n```\n\n"
        "git said:\n\n```\n%s\n```\n\n"
        "Filed by Sigma's rebase upkeep for unit `%s`. Until this is resolved the unit's feature "
        "branch stays behind `%s` and its goal branches are not replayed.\n"
        % (branch, base, _aftermath(left, branch, tip, before),
           base, branch, branch, base, why, unit, base))


def _goal_conflict_body(unit, goal_branch, branch, why, state):
    return (
        "`%s` could not be replayed onto `%s` after that feature branch was brought forward.\n\n"
        "Measured afterwards:\n\n%s\n\n"
        "Reproduce and resolve:\n\n"
        "```\ngit switch %s\ngit rebase origin/%s\n```\n\n"
        "it reported:\n\n```\n%s\n```\n\n"
        "Filed by Sigma's rebase upkeep for unit `%s`.\n"
        % (goal_branch, branch, "\n".join("- %s" % one for one in state),
           goal_branch, branch, why, unit))


def _direct_body(unit, branch, base, found):
    listed = "\n".join("- `%s` %s" % (d["sha"][:12], d["subject"]) for d in found)
    return (
        "`%s` carries %d commit(s) that no pull request accounts for, so it was **not rebased**.\n\n"
        "%s\n\n"
        "> Nobody commits directly to a feature branch. All work reaches it through `sdlc/*` goal "
        "branches.\n\n"
        "That rule is the entire reason rebasing a shared feature branch is safe: rebase rewrites "
        "published history and normally forces every holder to hard-reset, and that risk disappears "
        "only because no human ever holds commits there. A commit that did not arrive through a goal "
        "branch is evidence that somebody does — so upkeep stopped rather than rewriting the branch "
        "underneath them.\n\n"
        "**To resolve:** move the work onto an `sdlc/*` branch and land it through a pull request, "
        "or confirm the commits are safe to rewrite and re-run upkeep. Until then `%s` stays behind "
        "`%s`.\n\n"
        "Filed by Sigma's rebase upkeep for unit `%s`.\n"
        % (branch, len(found), listed, branch, base, unit))


#: One sentence per reportable outcome, for the clause `work.start()` appends to its own result. The
#: table is TOTAL over `IN_CLAUSE` so widening that tuple raises `KeyError` rather than silently
#: saying nothing -- a mutant that removes a kind from the clause must break something visible.
_WORDING = {
    BUSY: ("could not take %(branch)s's rebase lock -- another pass holds it, or the lock file "
           "could not be opened"),
    #: `%(path)s`, NOT `%(why)s`: the clause truncates `why` at `_CLAUSE_WHY_CHARS`, and the one
    #: thing an operator needs from this line is the path to clear -- which a deep `tmp`/CI path
    #: pushes past that limit. A path is one line by construction, so quoting it whole is safe.
    #: THE GESTURE IS IN THE LINE, because nothing else clears this: a later pass repeats
    #: `occupied` forever (it still cannot prove the worktree stale), so the operator is the only
    #: mechanism and telling them "it is left alone" without saying what to do is half a message.
    OCCUPIED: ("%(branch)s was not rebased: a worktree already sits at %(path)s and, with no file "
               "locking available, nothing here can prove it is stale rather than another pass's. "
               "If no other pass is running, clear it with `git worktree remove --force %(path)s "
               "&& git worktree prune`"),
    FAILED: "%(branch)s was NOT rebased: %(why)s",
    UNVERIFIABLE: ("%(branch)s was not rebased: with merge_method %(merge_method)r a landed goal and "
                   "a hand-typed commit are indistinguishable, so the no-direct-commits rule cannot "
                   "be checked"),
    #: NEITHER OF THESE TWO ASSERTS A FILING ANY MORE. They said "see the filed issue" and "filed
    #: as work" unconditionally, and `create_tracked_issue` never raises -- so on a `gh` outage the
    #: clause sent an operator looking for an issue that did not exist. What happened to the filing
    #: is appended by `clause` from `report["filing"]`, which is a measurement.
    DIRECT_COMMITS: ("%(branch)s carries %(direct)d commit(s) no pull request accounts for and was "
                     "NOT rebased"),
    CONFLICT: ("%(branch)s conflicts with %(base)s; the rebase was aborted and both repositories "
               "were left as they were"),
    LEASE_REFUSED: ("%(branch)s moved on the remote while it was being rebased, so the push was "
                    "refused rather than forced -- the other writer's commits are intact"),
    REBASED: "%(branch)s brought forward onto %(base)s (%(replayed)d replayed, %(conflicts)d "
             "conflicted, %(skipped)d skipped)",
}


def clause(report):
    """The one-line clause `work.start()` appends to its own result, or "".

    THE ONLY CHANNEL ANY OF THIS HAS on a normal run, which is why `FAILED` had to join `IN_CLAUSE`
    and why `serialised` is appended here: a report field with no consumer is decoration, and this
    module's own docstring says so about somebody else's."""
    if report["outcome"] not in IN_CLAUSE:
        return ""
    said = _WORDING[report["outcome"]] % {
        "branch": report["branch"], "base": report["base"],
        "merge_method": report["merge_method"], "direct": len(report["direct"]),
        "replayed": len(report["replayed"]), "conflicts": len(report["conflicts"]),
        "skipped": len(report["skipped"]),
        "why": " ".join(str(report["why"]).split())[:_CLAUSE_WHY_CHARS] or "no reason recorded",
        "path": " ".join(str(report["why"]).split()) or "this unit's rebase path"}
    # WHAT HAPPENED TO THE FILING, from the measurement rather than from the wording. `issues` had
    # no consumer on the pick path either, so a finding whose issue number lived only in a report
    # field made the operator go looking for the issue the line had just told them about.
    if report["issues"]:
        said += " -- filed as %s" % ", ".join("#%s" % one for one in report["issues"])
    if report["filing"] == ALREADY_FILED:
        said += " -- already filed on an earlier pick"
    elif report["filing"] == FILING_FAILED:
        said += (" -- the follow-up issue could not be filed (see stderr); it is attempted again "
                 "on the next pick")
    if not report["serialised"]:
        said += " (unserialised -- no file locking available, so a concurrent pass is not excluded)"
    return " — upkeep: %s" % said


# --------------------------------------------------------------------------- the pass


def _report(goal, unit, config):
    return {"outcome": FAILED, "goal": str(goal), "unit": unit, "branch": None, "base": None,
            "before": None, "after": None, "tip": None, "replayed": [], "conflicts": [],
            "skipped": [], "direct": [], "issues": [], "leftovers": [], "filing": NO_FILING,
            "serialised": True, "merge_method": merge_method(config), "why": "", "note": ""}


def upkeep(sdlc_dir, config, goal, unit, run=None, cwd=None, remote=None):
    """THE PASS. Integration branch -> feature branch, then the feature's goal branches onto it.

    NEVER RAISES. Everything resolves to a report, and every outcome except `REBASED` means nothing
    was moved. The same posture `feature_sync.sync_at_pick` and `cross_repo.check_at_pick` take, for
    the same reason: this is maintenance, and maintenance must never cost the goal.

    `unit` IS HANDED IN, ALREADY RESOLVED, exactly as `feature_sync.sync_at_pick` takes it -- this
    function has no code path that reads an issue, so "no second network read" is structural rather
    than a promise.

    ONLY THE PICKED UNIT, WHICH IS THE ONE PLACE THIS DELIBERATELY DIVERGES FROM `feature_sync`.
    That pass sweeps every unit in the registry every time, because reconciling a record is a read
    and a rare write. This one force-pushes, replays worktrees and can open issues, so sweeping
    every unit on every pick would multiply the epic's most destructive operation by the size of the
    registry to maintain branches nobody is working on. Each unit is maintained when it is picked."""
    report = _report(goal, unit, config)
    try:
        _upkeep(sdlc_dir, config, goal, unit, run, cwd, remote, report)
    except Exception as exc:              # noqa: BLE001 - "never raises" has to be total
        report["outcome"] = FAILED
        report["why"] = _flat(exc)
        _note("sigma: rebase upkeep: the pass for %s did not run (%s); the pick carries on and "
              "no branch was moved.\n" % (unit or goal, report["why"]))
    # THE ONE PLACE THE CLAUSE IS BUILT, on the single exit, rather than at each of `_upkeep`'s
    # own returns. `_upkeep` mutates the very object built here and every one of its
    # paths returns it, so there is no path -- including the one that raised -- on which
    # `work.start()` can be handed a report whose `note` was never filled in. A finding in a field
    # nobody reads is decoration; this is what gives it a consumer.
    report["note"] = clause(report)
    return report


def _upkeep(sdlc_dir, config, goal, unit, run, cwd, remote, report):
    if switch(config) == OFF:
        report["outcome"] = DISABLED
        return report
    if not unit:
        # A goal declaring no unit is today's behaviour exactly, which is what makes adopting the
        # branching model break nothing that already exists. Checked before any disk access.
        report["outcome"] = NO_UNIT
        return report
    features_dir = registry.registry_dir(sdlc_dir)
    if not features_dir.is_dir():
        report["outcome"] = NOT_ADOPTED
        return report

    run = run or _run
    cwd = str(cwd or pathlib.Path(sdlc_dir).parent)
    remote = remote or _remote(config)
    branch = features.BRANCH_PREFIX + unit
    base = _settings(config).get("base") or ""
    report["branch"], report["base"] = branch, base

    # THE LOCK IS TAKEN BEFORE ANY OTHER DECISION, and the reordering is the fix for a strand that
    # was permanent. A process killed outright (`os._exit`, SIGKILL, a laptop lid) runs no
    # `finally`, so `_rebase_feature`'s cleanup never happens and its ephemeral worktree stays
    # REGISTERED with a half-applied rebase in it. Recovering only inside `_rebase_feature` meant
    # every early return below -- `current` above all, which is the overwhelmingly common outcome --
    # returned before the recovery and left it there forever.
    lock = None
    if sync.fcntl is not None:
        try:
            lock_path(sdlc_dir, unit).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # NOT `BUSY`. `_acquire` fails open on contention AND on a state directory it cannot
            # write, and reporting the second as the first tells an operator "another pass is
            # already rebasing" forever, which is false and unactionable.
            report["why"] = "the rebase lock directory could not be created: %s" % _flat(exc)
            return report
        lock = _acquire(lock_path(sdlc_dir, unit), timeout=LOCK_TIMEOUT)
        if lock is None:
            # FAILS CLOSED, unlike `feature_sync`'s write lock: the worst case there is a lost goal
            # number, and here it is two concurrent rebases of one shared branch.
            report["outcome"] = BUSY
            return report
    else:
        report["serialised"] = False
    try:
        stale = worktree_path(sdlc_dir, unit)
        if stale.exists():
            if lock is None:
                # THE DEFENCE THIS MODULE USED TO CLAIM AND NOT HAVE. It said `git worktree add`
                # refuses an existing path -- which never got the chance, because the pre-add drop
                # deleted it first, so a second unserialised pass destroyed a first pass's LIVE
                # worktree. With no lock there is no way to tell a stale worktree from a live one,
                # and deleting on a guess is the one thing that must not happen here.
                report["outcome"] = OCCUPIED
                report["why"] = str(stale)
                return report
            # WITH the lock held, an existing worktree cannot be another pass's: no other pass can
            # be inside this section. So it is stale by construction, and dropping it is safe.
            _drop_worktree(run, cwd, stale)
        if not base or base == branch:
            # No integration branch to come from -- or the config names THIS branch as it, which is
            # what a repo mid-epic looks like (`work.base` pointed at the epic's own feature
            # branch). A branch cannot be brought forward onto itself.
            report["outcome"] = NO_BASE
            return report
        live, why = sync.live_branches(run, cwd, remote)
        if live is None:
            # `None` is "the remote could not be read", never "there are no branches" -- reading the
            # first as the second would report every unit as having lost its branch. `feature_sync`
            # makes the same distinction on the same call for the same reason.
            report["outcome"] = REMOTE_UNREADABLE
            report["why"] = why
            return report
        if branch not in live:
            report["outcome"] = NO_BRANCH
            return report
        return _rebase_pass(sdlc_dir, config, goal, unit, run, cwd, remote, branch, base, report)
    finally:
        _release(lock)


def _rebase_pass(sdlc_dir, config, goal, unit, run, cwd, remote, branch, base, report):
    run(cwd, ["git", "fetch", remote, base, branch])
    base_ref, feature_ref = "%s/%s" % (remote, base), "%s/%s" % (remote, branch)
    before = str(run(cwd, ["git", "rev-parse", feature_ref]) or "").strip()
    if not before:
        report["why"] = "%s read back empty" % feature_ref
        return report                     # outcome stays FAILED -- a tip we could not read is not a
                                          # tip we may force-push over
    report["before"] = before

    behind = str(run(cwd, ["git", "rev-list", "--count",
                           "%s..%s" % (feature_ref, base_ref)]) or "").strip()
    # `rev-list --count` answers "how many commits are on the integration branch that this branch
    # does not have". Zero means already current, which is the overwhelmingly common case and costs
    # one fetch and two reads -- deliberately cheaper than everything below it.
    if behind == "0":
        report["outcome"] = CURRENT
        return report

    if not _verifiable(config):
        report["outcome"] = UNVERIFIABLE
        return report
    found = direct_commits(run, cwd, base_ref, feature_ref)
    report["direct"] = found
    if found:
        report["outcome"] = DIRECT_COMMITS
        _file_issue(sdlc_dir, config, goal, report,
                    slot="direct:%s" % branch, key=before,
                    title="Direct commits on %s block rebase upkeep" % branch,
                    why="%s carries %d commit(s) no pull request accounts for, so it was not "
                        "rebased onto %s" % (branch, len(found), base),
                    body=_direct_body(unit, branch, base, found))
        return report

    path = worktree_path(sdlc_dir, unit)
    outcome = _rebase_feature(run, cwd, path, branch, base_ref, before, remote, report)
    report["outcome"] = outcome
    if outcome in (CONFLICT, FAILED):
        # MEASURED, not asserted. The body used to state "no half-applied rebase, no stranded
        # worktree" unconditionally, and a cleanup that refused produced an issue asserting the
        # opposite of what was on disk -- for a human to act on.
        report["leftovers"] = leftovers(run, cwd, path)
        if report["tip"] is None:
            report["tip"] = remote_tip(run, cwd, remote, branch)
    if outcome == CONFLICT:
        _file_issue(sdlc_dir, config, goal, report,
                    slot="feature-conflict:%s" % branch, key=before,
                    title="Rebase conflict: %s onto %s" % (branch, base),
                    why="%s could not be replayed onto %s" % (branch, base),
                    body=_feature_conflict_body(unit, branch, base, before, report["why"],
                                                report["leftovers"], report["tip"]))
        return report
    if outcome != REBASED:
        return report

    entry = registry.read(registry.registry_dir(sdlc_dir)).get(unit) or {}
    repo = sync.repo_slug(config, run, cwd, remote)
    if repo is None:
        # `repos.<slug>.goals` is the only place a goal number lives, so with no slug there is no
        # list to replay from. The feature branch moved and its goal branches did not, which is a
        # half-done pass and must not read as a complete one.
        report["skipped"].append({"branch": None, "why": "no owner/name resolved, so the unit's "
                                                        "goal branches could not be listed"})
        return report
    # `sync.repo_key`, NEVER a bare `repos.get(repo)`. #1477 stopped `_record_goal` widening `repos`
    # via `setdefault`, so every key after the first is HUMAN-TYPED and matched against a slug
    # derived from config or from a git remote -- and `owner/name` is case-insensitively unique on
    # GitHub, so two casings were never two repos. An exact lookup here is a second opinion about a
    # question that module already answers: against a hand-written `Org/Repo` it finds no goals and
    # the whole replay half goes inert while still reporting `rebased`.
    #
    # A repo the entry does not name AT ALL is a different case and is left alone deliberately: that
    # is #1477's scope expansion, and `feature_propagate.gate_at_pick` refuses the pick before
    # `start()` is ever reached, while `_record_goal` refuses the write on the bare-`work.py start`
    # path. Either way there is nothing recorded for this repo and nothing here to replay.
    repos = registry.normalise_entry(entry).get("repos") or {}
    goals = (repos.get(sync.repo_key(repos, repo)) or {}).get("goals") or []
    _replay_goals(sdlc_dir, config, goal, unit, branch, goals, run, report, cwd, remote)
    return report


# --------------------------------------------------------------------------- CLI


USAGE = ("usage: feature_rebase.py upkeep <sdlc_dir> <unit> [goal] | "
         "feature_rebase.py show <sdlc_dir> <unit>")


def main(argv):
    """`feature_rebase.py upkeep <sdlc_dir> <unit> [goal]` | `feature_rebase.py show <sdlc_dir> <unit>`.

    `upkeep` exists as a verb, unlike `feature_sync`'s sync, because this one has a use OFF the pick
    path: a unit whose goals are all finished still wants its branch kept current, and nobody is
    picking it. `show` reports what the pass WOULD find without moving anything."""
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    state = _load("state")
    if len(argv) >= 4 and argv[1] == "upkeep":
        report = upkeep(argv[2], state.load_config(argv[2]), argv[4] if len(argv) >= 5 else "-",
                        argv[3])
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["outcome"] != FAILED else 1
    if len(argv) >= 4 and argv[1] == "show":
        config = state.load_config(argv[2])
        remote, branch = _remote(config), features.BRANCH_PREFIX + argv[3]
        cwd = str(pathlib.Path(argv[2]).parent)
        base = _settings(config).get("base") or ""
        print(json.dumps({
            "unit": argv[3], "branch": branch, "base": base,
            "verifiable": _verifiable(config), "merge_method": merge_method(config),
            "direct": direct_commits(_run, cwd, "%s/%s" % (remote, base),
                                     "%s/%s" % (remote, branch)) if base else []},
            indent=2, sort_keys=True))
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

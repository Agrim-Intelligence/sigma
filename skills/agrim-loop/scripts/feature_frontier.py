#!/usr/bin/env python3
"""The feature-level DAG (#2265, D-1/D-10/BR-3/BR-4/BR-35 of `.sdlc/design/2253.md`): a unit-scoped
frontier over edges that already exist -- a VIEW, never new machinery.

TWO THINGS ALREADY EXIST, AND THIS MODULE ONLY POINTS AT THEM.

  - The EDGES: `blocker_scan.extract_refs` runs against a WHOLE issue body at fetch time, before the
    500-character excerpt `mirror.py` stores is ever taken, and the result is already persisted per
    issue as `blocker_refs` on the local board mirror (`mirror.read_mirror`). Sound as-is, regardless
    of how membership is resolved (BR-35) -- this module never re-scans a body for a ref, it only
    ever reads what `mirror.normalize_issue` already wrote.
  - The ALGORITHMS: `slices.py`'s `_cycles` / `frontier` / `fan_out` already walk exactly this shape
    of graph (`{"id", "needs", "status"}`) for one goal's own slices. Reused here verbatim, scoped to
    one unit's members, never reimplemented (BR-3).

MEMBERSHIP IS THE ONE THING THAT IS NOT SOUND OFF THE MIRROR (D-10). `mirror.py` stores only a
500-character `body_excerpt`, and a unit's `Feature:` marker lands at the END of a body on the
common path (`feature_stamp.stamp_body`'s `_appended` case) -- so a member whose body runs past the
cap has its declaration fall outside what the mirror kept, invisible to `features.read`, which needs
the full `body` key. This module resolves membership the same way #2262's `_feature_rank` and
#1661's `_issue_in_feature` already do: ONE live REST fetch of the open, goal-labelled issues (which
returns the FULL body -- REST has no field-selection to opt out of it), tested per issue against
`features.read(issue).unit`. `body_excerpt` is never read by this module at all.

RECOMPUTED FRESH AT EVERY CALL, NEVER COMPILED. `compute()` does its own REST fetch and its own
mirror read on every invocation and caches nothing across calls -- the design's own stated reason is
that a newly-filed P0 must slot into the graph for free, with no replan step, the moment its edges
land on the mirror and its own open/goal-labelled fetch picks it up.

THE FAIL-CLOSED DIVERGENCE (BR-4), STATED HERE SO IT IS NOT SILENT. `loop.py`'s pick-time dependency
gate (`_dependency_gate_mode`, `_pick_key`'s cousin, not this module) FAILS OPEN on an undeterminable
prerequisite -- its own docstring says so in so many words -- because its mirror read is already
known partial (open half `sdlc:goal`-filtered, closed half windowed) and a stalled fleet is worse
than an occasional early claim. This module does the opposite ON PURPOSE: a blocker ref this module
cannot resolve -- not a fellow open member, not found closed in the local mirror, not found open in
it either -- keeps its dependent OUT of the ready set. Three things make that the right call here and
not there: (1) this is advisory, read-only output a human or a skill CONSULTS, never a gate that
blocks a pick outright; (2) unlike the pick-time gate, this module was built AFTER the mirror's own
partiality was named (BR-4's own correction), so it has no excuse to inherit it silently; (3) a
missed edge here costs one item staying off a "ready now" list for one more refresh -- recoverable
and cheap -- while an unresolved ref treated as satisfied would print "ready" for a member whose real
prerequisite might still be open, which is the more expensive kind of wrong for a scheduling view to
be. A blocked member's `"unresolved"` list (in `compute()`'s own return shape, below) is that ref's
own tag when it takes this branch, kept separate from the ordinary `"waiting_on"` list.

CYCLE DETECTION, PHANTOM-EDGE REJECTION, AND AN EDGE TO A CLOSED-OR-NONEXISTENT ISSUE ARE FIRST-CLASS
HERE, NOT HYGIENE, because this graph is inferred from human-typed `#N` references rather than
correct by construction:

  - cycles: `slices._cycles` over the unit-scoped graph, reported by membership (the fix is to break
    one edge, so the members have to be named).
  - phantom edges: rejected structurally, by construction, never by a second filter. This module
    NEVER re-scans a body for a ref -- it only ever reads a member's already-persisted
    `blocker_refs`, which `mirror.normalize_issue` computed with `blocker_scan.extract_refs`'s
    narrow `EXPLICIT_TRIGGERS` vocabulary (measured 21 of 21 genuine edges, zero phantom, on this
    repo's own board -- see `blocker_scan.TRIGGERS`'s own docstring). A weak-trigger phantom
    ("waiting on #999") was never written to the mirror in the first place, so there is nothing for
    this module to filter back out.
  - closed: an external ref found in the local mirror with `state == "closed"` is treated as
    satisfied and dropped from `needs` -- it can never re-open, so nothing tracks it further.
  - nonexistent (or simply outside the mirror's own closed-issue window): indistinguishable from
    local data alone, and this module does not pretend otherwise -- both surface identically as
    UNRESOLVED and both fail closed, for the same reason (see above).

Read-only. Never writes to GitHub, the mirror, the ledger, or any goal/issue -- `compute()` takes no
`source` and calls no mutation, and its only side effect is the REST GET `sources.fetch_issues_rest`
already documents as safe. Exposed as `loop.py feature-frontier <sdlc_dir> <unit>`.
"""
import importlib.util
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


mirror = _load("mirror")                    # is_github_mode, read_mirror, age_seconds
slices = _load("slices")                    # _cycles, frontier, fan_out -- reused verbatim
blocker_scan = _load("blocker_scan")        # read_refs -- the persisted-edges reader
features = _load("features")                # read, AmbiguousUnit -- the live-body membership test
feature_registry = _load("feature_registry")  # resolve_open_unit
scrub = _load("scrub").scrub                # defense in depth: a live-fetched title is unscrubbed

#: Mirrors `mirror._OPEN_LIMIT` / `next_pending`'s own 200-issue cap -- the same bound the rest of
#: the pick path applies to "every open goal-labelled issue", so a unit-scoped view never claims to
#: see further into the backlog than an ordinary pick already does.
_OPEN_LIMIT = 200

#: The two degrade reasons this module can return instead of a computed frontier. Each is a REFUSAL
#: (SAFETY: "the code REFUSES loudly rather than proceeding weakly"), never a silently-empty result
#: that would read the same as "checked, nothing is ready".
NOT_GITHUB = "not-github-mode"
UNKNOWN_UNIT = "unknown-or-closed-unit"

#: Never a legal issue number (`blocker_scan.read_refs` only ever yields digit strings) and never a
#: legal slice id (`str(issue["number"])`, also always digits) -- so this can be pushed into a
#: member's `needs` list and trusted to NEVER be satisfied by `slices.frontier`'s own `done` check,
#: without inventing a second readiness mechanism next to the one `slices.py` already has. Used for
#: exactly one case: a member this module cannot even find a mirror record for at all, so its own
#: blockers are simply unknown -- not "no blockers", which would be the dangerous, optimistic misread.
_NO_RECORD_SENTINEL = "no-local-mirror-record"


def _config(sdlc_dir):
    """Tolerant read, same posture as `slices._config`: this is advisory output, and an unreadable
    or half-written config reads as "no opinion" (falls through to the ordinary degrade checks)
    rather than raising."""
    try:
        return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def members(open_issues, unit):
    """-> `(member_issues, ambiguous_numbers)`. Filters already-fetched, FULL-BODY issue payloads to
    the ones that DECLARE `unit`, via `features.read` -- never the mirror's `body_excerpt` (D-10).

    AN ISSUE THAT CONTRADICTS ITSELF IS NOT A MEMBER, the same rule `sources._issue_in_feature`
    already states and for the same reason: `features.read` raises `AmbiguousUnit` rather than
    guess between two rival declarations, and admitting such an issue here would only move that
    refusal to a later, more expensive place. Its number is still returned, separately, so a caller
    can say why it is missing rather than silently vanishing it."""
    out, ambiguous = [], []
    lowered = str(unit).lower()
    for issue in open_issues:
        try:
            verdict = features.read(issue)
        except features.AmbiguousUnit:
            number = issue.get("number") if isinstance(issue, dict) else None
            if number is not None:
                ambiguous.append(number)
            continue
        if verdict.unit and str(verdict.unit).lower() == lowered:
            out.append(issue)
    return out, ambiguous


def _safe_int(ref):
    """`int(ref)`, or `None` on anything that doesn't actually parse -- `blocker_scan.read_refs`
    only guarantees `ref.isdigit()`, and `isdigit()` is true for some Unicode digit characters
    (e.g. superscripts) that `int()` still rejects. A ref shaped like that can only reach here via
    a hand-built or corrupted mirror record (the regex-driven fetch path can't produce one), but
    this module DEGRADES, never raises, on exactly that kind of input everywhere else -- this
    closes the one spot that didn't."""
    try:
        return int(ref)
    except ValueError:
        return None


def _is_closed(record):
    return str((record or {}).get("state") or "").lower() == "closed"


def _member_needs(number, refs, member_ids, by_number):
    """One member's `needs` list (fed to `slices.py` unmodified) plus `waiting_on` (a real, still-
    open dependency -- ordinary, not a failure) and `unresolved` (fails closed -- see the module
    docstring's BR-4 section).

    Every ref lands in exactly one of three buckets:

      - a FELLOW OPEN MEMBER -> a normal intra-unit edge. `slices.frontier`'s own "a `needs` naming
        nothing real is never satisfied" rule (already fail-closed, unmodified, see `slices.py`)
        does the rest -- this module adds nothing here.
      - found in the local mirror and CLOSED -> satisfied. Dropped from `needs` entirely: a closed
        issue can never re-open, so there is nothing left to track.
      - anything else -- found in the mirror and still OPEN, or not found in the mirror at all --
        kept in `needs` with no slice in this run's list ever able to satisfy it, so the member can
        never be marked ready on this ref's account. The "not found at all" half is additionally
        reported as UNRESOLVED: this module cannot tell "closed outside the mirror's own window"
        from "never a real issue" from local data alone, and does not try -- both fail closed."""
    needs, waiting_on, unresolved = [], [], []
    for ref in refs:
        if ref in member_ids:
            needs.append(ref)
            waiting_on.append(ref)
            continue
        n = _safe_int(ref)
        record = by_number.get(n) if n is not None else None
        state = str((record or {}).get("state") or "").lower()
        if state == "closed":
            continue                              # satisfied -- resolved, no-longer-blocking; drop it
        needs.append(ref)
        if state == "open":
            waiting_on.append(ref)                # a real, unfinished dependency -- correctly blocked
        else:
            unresolved.append(ref)                # not in the mirror at all -- fails closed (BR-4)
    return needs, waiting_on, unresolved


def _cross_feature_cycles(member_ids, by_number, member_nodes=()):
    """Every dependency cycle that touches this unit's own members but is NOT fully contained
    within them -- the shape `slices._cycles(slice_list)` structurally cannot see, because
    `compute()`'s own `slice_list` only ever contains this unit's declared members (#2283). Reuses
    `slices._cycles` UNMODIFIED over a WIDER graph built from every open record the local mirror
    already holds (`by_number`, already loaded for `_member_needs`'s own open/closed check -- no
    new fetch), then keeps only the cycles crossing the unit boundary: a cycle fully inside
    `member_ids` is already reported by the caller's own intra-unit `cycles` field, and reporting
    it twice under a different name would be noise, not new information.

    `member_nodes` (`compute()`'s own `slice_list`) seeds the graph for THIS unit's own members
    first, so their CLOSED/OPEN state comes from the same live-reconciled data `cycles`/`ready`/
    `blocked` already use -- never from the passively-synced mirror alone. Without this, a member
    that is live-open but mirror-stale-closed would silently drop out of this graph as a node while
    still showing up as ready/blocked in the very same result, which is worse than merely stale: it
    would disagree with `compute()`'s own live answer for the unit running the check, not just for
    a foreign one it never fetched. A member with no mirror record at all still contributes no real
    edge either way -- its own blockers are genuinely unknown from local data alone, same as the
    `no_mirror_record`/`unresolved` reporting elsewhere in this module -- seeding it as an inert
    node (`needs=[_NO_RECORD_SENTINEL]`, never satisfiable, never a source of a real edge) is
    correct but not a new capability.

    Same closed-is-satisfied resolution `_member_needs` already applies, so a ref through a CLOSED
    issue never creates a phantom edge here either. A ref to an issue absent from the mirror
    entirely has no node of its own and so contributes no edge -- `slices._cycles`'s own `known`
    filter already drops it, the identical, pre-existing limitation intra-unit detection has always
    had, not a new one this introduces.

    NOT full parity with `_member_needs`: an unknown/missing state is kept as a live edge here (the
    safe direction -- it can only add a finding to check, never hide a real one), where
    `_member_needs` additionally REPORTS it as `unresolved`. This function has no equivalent
    reporting channel; a genuinely malformed mirror record degrades silently rather than loudly.

    KNOWN, NAMED LIMITATION, NARROWED TO THE EXTERNAL SIDE ONLY (member_nodes above closes the gap
    for THIS unit's own side): the OTHER unit's nodes still come entirely from the local mirror
    (`by_number`), independently, asynchronously refreshed on its own TTL
    (`mirror._DEFAULT_TTL_MINUTES`, default 60) and capped (`mirror._OPEN_LIMIT`, 200). If the
    OTHER side of a cross-feature cycle was never synced into the mirror, or fell outside its
    window, that node never enters this graph and the cycle silently degrades back to an ordinary
    `waiting_on` entry -- exactly the pre-fix behaviour, for that one case. THE SAME GAP IS
    PERMANENT, NOT MERELY STALE, when `discovery.github.assignee` is configured: `mirror.py`'s own
    fetch scopes the open half of the mirror to that one assignee's issues, so another assignee's
    open issue never enters `by_number` no matter how fresh the sync is. Not solved here: a live
    fetch of every external ref's own blockers would need one more REST round-trip per foreign
    node, real cost this issue's P0 scope does not ask for. Surfaced by `render()` pointing at the
    existing mirror-age line, which speaks to staleness but not to assignee-scoping -- an install
    running with a configured assignee has strictly less cross-feature coverage than this line
    implies, and that is not yet named anywhere a reader of the rendered report would see it."""
    graph = list(member_nodes)
    seen = {n["id"] for n in graph}
    for number, record in by_number.items():
        sid = str(number)
        if sid in seen or _is_closed(record):
            continue
        refs = [ref for ref, _phrase in blocker_scan.read_refs(record, self_ref=number)]
        # A ref TARGETING one of this unit's own members is resolved the same live-confirmed way
        # `_member_needs` resolves it for the intra-unit graph: always open (member_ids can only
        # ever hold live-open issues), never re-checked against that member's own possibly-stale
        # mirror record -- the same reconciliation `member_nodes` above applies to the member as a
        # SOURCE node, applied here to the member as an edge TARGET.
        open_refs = [ref for ref in refs
                     if ref in member_ids
                     or (n := _safe_int(ref)) is None or not _is_closed(by_number.get(n))]
        graph.append({"id": sid, "needs": open_refs, "status": slices.DEFAULT_STATUS})
    return [cycle for cycle in slices._cycles(graph)
            if any(n in member_ids for n in cycle) and any(n not in member_ids for n in cycle)]


def compute(sdlc_dir, unit, config=None, run=None, now=None):
    """The unit-scoped frontier for `unit`, recomputed fresh from the local mirror plus one live
    REST fetch -- no cache, no compiled plan (see the module docstring's "recomputed fresh" section).

    -> a dict:
        unit               canonical registry spelling of `unit`, or the requested string on a degrade
        degraded           None, or one of NOT_GITHUB / UNKNOWN_UNIT
        reason             a human sentence, only when degraded
        members            every open, goal-labelled issue that DECLARES `unit` (D-10-correct), sorted
        ready              the frontier: [{"id","title","fan_out"}], widest-fan-out-first, ties by id
        blocked            every other member: [{"id","title","waiting_on","unresolved"}]
        cycles             `slices._cycles`'s own report, unmodified, over the unit-scoped graph
        cross_feature_cycles a cycle spanning OUTSIDE this unit (#2283) -- see `_cross_feature_
                           cycles`'s own docstring; the intra-unit `cycles` field above never
                           double-counts a boundary-crossing cycle, by construction
        ambiguous          member numbers excluded for a self-contradicting declaration
        no_mirror_record   member ids this module could not even find a mirror record for at all
        mirror_age_seconds `mirror.age_seconds`'s own reading, or None

    Never mutates anything: no gh write, no mirror write, no ledger entry, no label, no comment."""
    config = config if config is not None else _config(sdlc_dir)
    if not mirror.is_github_mode(config):
        return {"unit": unit, "degraded": NOT_GITHUB,
                "reason": "not GitHub mode (discovery.source != \"github\") -- there is no issue "
                          "graph a local-goal-files backlog could build this view from",
                "members": [], "ready": [], "blocked": [], "cycles": [], "cross_feature_cycles": [],
                "ambiguous": [],
                "no_mirror_record": [], "mirror_age_seconds": None}
    canonical = feature_registry.resolve_open_unit(sdlc_dir, unit)
    if canonical is None:
        return {"unit": unit, "degraded": UNKNOWN_UNIT,
                "reason": f"{unit!r} is not a known, OPEN unit in .sdlc/features/ -- check the "
                          "spelling, or that the unit has not already been closed",
                "members": [], "ready": [], "blocked": [], "cycles": [], "cross_feature_cycles": [],
                "ambiguous": [],
                "no_mirror_record": [], "mirror_age_seconds": None}

    records = mirror.read_mirror(sdlc_dir)
    by_number = {r["number"]: r for r in records
                 if isinstance(r, dict) and isinstance(r.get("number"), int)}

    sources_mod = _load("sources")          # loaded lazily -- heavy (subprocess/gh surface), same
    run = run or sources_mod._run_gh        # posture `mirror.fetch_and_write` already takes
    gh = (config.get("discovery") or {}).get("github") or {}
    repo = gh.get("repo") or ""
    goal_label = gh.get("goal_label", "sdlc:goal")
    open_issues = sources_mod.fetch_issues_rest(run, repo, [goal_label], _OPEN_LIMIT)

    member_issues, ambiguous = members(open_issues, canonical)
    member_ids = {str(i["number"]) for i in member_issues}

    slice_list, waiting_by_id, unresolved_by_id, no_record = [], {}, {}, []
    for issue in member_issues:
        number = issue.get("number")
        sid = str(number)
        title = scrub(str(issue.get("title") or ""))
        record = by_number.get(number)
        if record is None:
            no_record.append(sid)
            slice_list.append({"id": sid, "title": title, "needs": [_NO_RECORD_SENTINEL],
                                "status": slices.DEFAULT_STATUS})
            continue
        refs = [ref for ref, _phrase in blocker_scan.read_refs(record, self_ref=number)]
        needs, waiting_on, unresolved = _member_needs(number, refs, member_ids, by_number)
        if waiting_on:
            waiting_by_id[sid] = waiting_on
        if unresolved:
            unresolved_by_id[sid] = unresolved
        slice_list.append({"id": sid, "title": title, "needs": needs,
                            "status": slices.DEFAULT_STATUS})

    cycles = slices._cycles(slice_list)
    cross_feature_cycles = _cross_feature_cycles(member_ids, by_number, member_nodes=slice_list)
    fan = slices.fan_out(slice_list)
    ready = slices.frontier(slice_list)
    ready_ids = {s["id"] for s in ready}
    ready_sorted = sorted(ready, key=lambda s: (-fan.get(s["id"], 0), int(s["id"])))
    blocked_sorted = sorted((s for s in slice_list if s["id"] not in ready_ids),
                             key=lambda s: int(s["id"]))

    return {
        "unit": canonical, "degraded": None, "reason": None,
        "members": sorted(int(s["id"]) for s in slice_list),
        "ready": [{"id": s["id"], "title": s["title"], "fan_out": fan.get(s["id"], 0)}
                  for s in ready_sorted],
        "blocked": [{"id": s["id"], "title": s["title"],
                     "waiting_on": waiting_by_id.get(s["id"], []),
                     "unresolved": unresolved_by_id.get(s["id"], [])}
                    for s in blocked_sorted],
        "cycles": cycles,
        "cross_feature_cycles": cross_feature_cycles,
        "ambiguous": sorted(ambiguous),
        "no_mirror_record": sorted(no_record, key=int),
        "mirror_age_seconds": mirror.age_seconds(sdlc_dir, now=now),
    }


def render(result):
    """The report a human reads. Every line names one decision this module made and why -- cycles,
    phantom-safe edges, closed/unresolved refs are each their own labelled section, never folded
    silently into "blocked"."""
    lines = [f"# Feature frontier — {result['unit']}", ""]
    if result.get("degraded"):
        lines.append(result["reason"])
        return "\n".join(lines) + "\n"

    lines.append(f"{len(result['members'])} member(s), {len(result['ready'])} ready now.")
    lines.append("")

    if result["ready"]:
        lines.append("Ready (widest fan-out first):")
        for r in result["ready"]:
            lines.append(f"  - #{r['id']} · {r['title'] or '(untitled)'}  [unblocks {r['fan_out']}]")
        lines.append("")

    if result["blocked"]:
        lines.append("Blocked:")
        for b in result["blocked"]:
            lines.append(f"  - #{b['id']} · {b['title'] or '(untitled)'}")
            if b["waiting_on"]:
                lines.append("      waiting on: " + ", ".join(f"#{n}" for n in b["waiting_on"]))
            if b["unresolved"]:
                lines.append("      UNRESOLVED, fails closed: "
                              + ", ".join(f"#{n}" for n in b["unresolved"])
                              + " -- not in the local mirror (closed outside its window, or never "
                                "a real issue); refusing to assume it is satisfied")
        lines.append("")

    if result["cycles"]:
        lines.append("Cycles (break one edge to open the frontier):")
        for c in result["cycles"]:
            lines.append("  - " + " -> ".join(f"#{n}" for n in list(c) + [c[0]]))
        lines.append("")

    if result["cross_feature_cycles"]:
        lines.append("Cross-feature cycles (spans another unit -- mirror-based; see mirror age "
                      "below):")
        for c in result["cross_feature_cycles"]:
            lines.append("  - " + " -> ".join(f"#{n}" for n in list(c) + [c[0]]))
        lines.append("")

    if result["no_mirror_record"]:
        lines.append("No local mirror record at all -- refusing to claim ready without blocker "
                      "data: " + ", ".join(f"#{n}" for n in result["no_mirror_record"]))
        lines.append("")

    if result["ambiguous"]:
        lines.append("Excluded — self-contradicting unit declaration: "
                      + ", ".join(f"#{n}" for n in result["ambiguous"]))
        lines.append("")

    age = result.get("mirror_age_seconds")
    lines.append(f"local mirror age: {int(age)}s" if age is not None else "local mirror age: unknown")
    return "\n".join(lines) + "\n"


USAGE = "usage: feature_frontier.py show <sdlc_dir> <unit>"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 4 and argv[1] == "show":
        result = compute(argv[2], argv[3])
        print(render(result), end="")
        return 1 if result.get("degraded") else 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""How far a chain run will get, from the rules `run` itself follows (#1907). Library only; no main.

ONE RULE, TWO CALLERS. Whether a member can be claimed, which of them one cycle may claim at once, and why
a cycle that claims nothing is stuck are decided here and nowhere else. `chain.run` calls `ready_members`
(its width plan) and `stuck_reason` (its stuck line); `forecast` calls the same two on a simulated run. A
second copy of either would let the plan promise what the run does not do, which is the failure this module
exists to end (a planner's own rules once said 26 goals where a reading of the issues said 4 to 6).

THE TABLE. `VERDICTS` maps a member's verdict to its marker, whether a run may claim it, and a short reason;
`OVERLAYS` does the same for flags a verdict does not carry. A new kind of member is one row. A verdict the
table does not know is NOT workable: the forecast never promises work it cannot name.

FORECAST. From the plan record alone (no `gh` call). The assignee filter is re-applied for the runner asked
about (`login`, default the one who planned), through `label_drain.assignee_gate`, the clause `_Judge.verdict`
uses. It then plays the run: each cycle claims what `ready_members` offers at the configured width, every
claimed goal is assumed to merge, until nothing is offered. What is left is the stop: its reason is
`stuck_reason` over the members left, and its roots are the left members that wait on no other left member.

Not modelled, and said so in the output: the cap (`chain.max_goals`, `max_usd`, `max_minutes`), a goal that
fails or is parked by its own session, and, for a plan read from the map, the assignee filter (the map holds no
assignees). Ceiling: O(cycles x members) per call, members bounded by `chain.max_members` (default 100).
"""
import importlib.util
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


label_drain = _load("label_drain")
chain_parallel = _load("chain_parallel")

READY, AFTER = "ready now", "after"
MARKERS = (READY, AFTER, "needs decision", "someone else's", "checkpoint", "not workable")
#: verdict -> (marker, a run may claim it, why it does not). The one place a verdict is given a meaning.
VERDICTS = {
    "armed": (READY, True, ""),
    "parked": ("needs decision", False, "parked"),
    "unarmed": ("needs decision", False, "not armed"),
    "upper-tier": ("needs decision", False, "an upper-tier issue, plan its slices"),
    "assigned-elsewhere": ("someone else's", False, "assigned to someone else"),
    "unassigned": ("not workable", False, "assigned to nobody"),
    "held-by-gate": ("not workable", False, "held by a gate"),
    # The checkpoint slice gives this its own marker; until it lands the word is "not workable". A checkpoint
    # is never claimed as work either way.
    "checkpoint": ("not workable", False, "a checkpoint, not work"),
    "unknown": ("not workable", False, "could not be read"),
}
#: flag on a row -> (marker, why). Checked after the verdict, in this order, as `label_drain._reason` does.
OVERLAYS = (
    ("in_progress", "someone else's", "already in progress"),
    ("blocked", "not workable", "carries the blocked label"),
)


def blockers_of(row, open_now):
    """-> the open in-chain members `row` waits on, then "outside" if it has an unresolved prerequisite."""
    return [w for w in row.get("waits_on") or [] if w in open_now] + (["outside"] if row.get("unresolved") else [])


def classify(row, open_now):
    """-> (marker, why, after) for one open member given the members still open. THE claim rule: a member is
    READY only when its verdict is workable, no overlay applies and it waits on nothing open."""
    marker, workable, why = VERDICTS.get(row.get("verdict"), ("not workable", False, "an unrecognised state"))
    if workable:
        for key, o_marker, o_why in OVERLAYS:
            if row.get(key):
                marker, workable, why = o_marker, False, o_why
                break
    if not workable:
        return marker, why, []
    waits = blockers_of(row, open_now)
    chained = [w for w in waits if w != "outside"]
    if chained:
        return AFTER, "waits on %s" % ", ".join("#%d" % w for w in chained), chained
    if waits:
        return "not workable", "waits on an open prerequisite outside the chain", []
    return READY, "", []


def pickable(row, open_now):
    return classify(row, open_now)[0] == READY


def ready_members(rows, open_now, in_flight, width):
    """-> the numbers one cycle may claim, in the order given: pickable, not in flight, at most
    `width - len(in_flight)`. `rows` carry `verdict`, `waits_on`, `unresolved`, `in_progress`."""
    return chain_parallel.ready_set([r["number"] for r in rows if pickable(r, open_now)], {}, in_flight, width)


def stuck_reason(p, unconfirmed=()):
    """-> (reason, detail), ONE reason from `chain.STUCK_REASONS` for an empty pick with members still open,
    given plan rows `p["members"]` and `p["outside"]`. Numbers only in the detail. The order is the order a
    person would act in: what needs a person, then what only waits. A failed read is the caller's, ahead of
    this."""
    rows, outside = p["members"], p.get("outside") or []
    if any(r.get("kind") in ("not-read", "unreadable") for r in rows + outside):
        return "degraded-read", "a parked member could not be read"
    parked = sorted(r["number"] for r in rows if r["verdict"] == "parked")
    if parked:
        return "decision", "#%d is parked and needs an answer" % parked[0]
    held = sorted(r["number"] for r in rows if r["verdict"] == "held-by-gate")
    if held:
        return "held-gate", "#%d is held by a gate" % held[0]
    claimed = sorted(r["number"] for r in rows if r.get("in_progress") or r["verdict"] == "assigned-elsewhere")
    if claimed:
        return "claimed", "#%d is in progress or assigned to someone else" % claimed[0]
    ext = sorted({n for r in outside for n in [r["number"]]} | {r["number"] for r in rows if r.get("unresolved")})
    if ext:
        return "external", "#%d waits on an open prerequisite outside the chain" % ext[0]
    needs = sorted(r["number"] for r in rows if r["verdict"] in ("unarmed", "upper-tier"))
    if needs:
        return "decision", "#%d is not armed and needs a person" % needs[0]
    if unconfirmed:
        return "degraded-read", "#%d could not be confirmed closed" % sorted(unconfirmed)[0]
    return "claimed", "open members remain but none could be claimed this cycle (skipped or leased elsewhere)"


# --- the forecast ------------------------------------------------------------------------------------------

def verdict_for(row, login, filtered):
    """The member's verdict for a run by `login`. The record holds the verdict as judged for whoever planned;
    `base_verdict` is the same judgement without the assignee clause, which is re-applied here by the clause
    `_Judge.verdict` uses. No assignee data (a plan from the map), no filter or no known login: as recorded."""
    base = row.get("base_verdict") or row.get("verdict")
    logins = row.get("assignees")
    if not filtered or logins is None or base not in ("armed", "held-by-gate"):
        return base
    gate = label_drain.assignee_gate(logins, login)
    return gate[0] if gate else base


def _normalise(rec, login, filtered):
    needed = {}
    for e in rec.get("externals") or []:
        for n in e.get("needed_by") or []:
            needed.setdefault(n, []).append(e.get("ref"))
    return [{"number": r["number"], "verdict": verdict_for(r, login, filtered), "kind": r.get("kind"),
             "waits_on": list(r.get("waits_on") or []), "unresolved": needed.get(r["number"], []),
             "in_progress": bool(r.get("in_progress")), "blocked": bool(r.get("blocked"))}
            for r in rec.get("members") or []]


def forecast(rec, config=None, login=None):
    """-> the forecast for the plan record `rec` as run by `login` (default: who planned it). Keys: `runner`,
    `width`, `members` ([{number, marker, why, after}], the record's order), `finish` (claimed in order),
    `cycles`, `of`, `reason` and `detail` (None when everything finishes), `stops` ([{number, marker, why}],
    the roots), `notes`, `line`."""
    filtered = rec.get("assignee_filter") is True
    me = login if login is not None else rec.get("runner")
    me = str(me).strip().lstrip("@").casefold() if me else None
    notes = []
    if rec.get("verdict_basis") == "map-life":
        notes.append("the assignee filter is not applied: the plan was read from the map, which holds no assignees")
    elif filtered and me is None:
        notes.append("the runner's login is unknown, so the assignee filter is not applied; pass --as <login>")
    elif login is not None and rec.get("assignee_filter") is False:
        notes.append("no assignee filter is configured, so --as changes nothing")
    rows = _normalise(rec, me, filtered)
    width = chain_parallel.width(config or {})
    open_now = {r["number"] for r in rows}
    out_members = []
    for r in rows:
        marker, why, after = classify(r, open_now)
        out_members.append({"number": r["number"], "marker": marker, "why": why, "after": after})
    finish, cycles = [], 0
    while True:
        batch = ready_members([r for r in rows if r["number"] in open_now], open_now, set(), width)
        if not batch:
            break
        cycles += 1
        finish += batch
        open_now -= set(batch)
    left = [r for r in rows if r["number"] in open_now]
    reason = detail = None
    stops = []
    if left:
        reason, detail = stuck_reason({"members": left, "outside": []})
        for r in left:
            if not [w for w in r["waits_on"] if w in open_now]:
                marker, why, _after = classify(r, open_now)
                stops.append({"number": r["number"], "marker": marker, "why": why})
    who = "a run from %s" % me if me else "a run"
    if not left:
        line = "%s can finish all %d of %d" % (who, len(finish), len(rows))
    else:
        line = "%s can finish %d of %d before it stops on %s: %s" % (
            who, len(finish), len(rows), reason,
            ", ".join("#%d %s (%s)" % (s["number"], s["marker"], s["why"]) for s in stops))
    return {"runner": me, "width": width, "members": out_members, "finish": finish, "cycles": cycles,
            "of": len(rows), "reason": reason, "detail": detail, "stops": stops, "notes": notes, "line": line}


def render_forecast(fc):
    """The forecast as lines of text: one per member, then the one-line answer and what it assumes."""
    lines = ["Forecast, a run%s (width %d):" % (" from " + fc["runner"] if fc["runner"] else "", fc["width"])]
    for m in fc["members"]:
        shown = "after " + ", ".join("#%d" % a for a in m["after"]) if m["marker"] == AFTER else m["marker"]
        lines.append("  #%d %s%s" % (m["number"], shown, " (%s)" % m["why"] if m["why"] and m["marker"] != AFTER else ""))
    lines.append("Forecast: %s" % fc["line"])
    lines += ["  note: " + n for n in fc["notes"]]
    lines.append("  assumes every goal it claims merges; the cap is not modelled")
    return lines

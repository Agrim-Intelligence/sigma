#!/usr/bin/env python3
"""Dependency waves for a batch of goals, and which goals to launch right now.

Part of `/sigma-orchestrate` (#2145); tests: tests/test_orchestrate_waves.py. Standard library only, and it
makes no network call and writes nothing: it reads two JSON files and prints JSON (or a table).

Deterministic on purpose: the orchestrating agent gathers the facts (blockers, file claims, bases,
what has landed where), this script does the ordering, so two wakes over the same facts never disagree.

  waves.py plan  <goals.json> [--md]
  waves.py ready <goals.json> <state.json> [--seats N]

goals.json
  {"goals": [{"n": 12, "title": "...", "priority": "P1", "base": "main",
              "blockers": [34],            # open blockers inside the batch
              "external_blockers": [99],   # open blockers outside the batch (the goal is held)
              "claims": ["src/app/x.py", "ui/src/", "contract:runs-api"],
              "open_prs": [101],           # open pull requests that link the issue and are not this batch's (held)
              "held": null}]}              # or a reason string set by the orchestrator
  Other keys (wave, deferred, cleanup, ...) are the orchestrator's and are ignored here.

state.json (for `ready`)
  {"landed": {"34": ["main"]},             # bases whose tip contains the goal's merge commit
   "finished": [34], "running": [12], "awaiting_merge": [15],
   "stopped": {"20": "parked: needs a person"},  # parked / failed / needs-you: never relaunched here
   "pr_files": {"12": ["src/app/x.py"]}}         # real PR file list replaces claims for in-flight goals

Rules
  * A wave holds goals that can run at the same time: every in-batch blocker sits in an earlier wave, and
    no two goals in one wave share a claim.
  * Placement walks goals in dependency order, best rank first; a goal goes in the smallest wave after
    all its blockers' waves where it clashes with nothing already there.
  * Rank: priority (P0 first), then more in-batch goals waiting on it (transitively), then issue number.
  * Held goals (outside blocker, cycle, an open pull request linking the issue, a reason from the orchestrator) are not placed, and neither is any
    goal that waits on a held one. An edge between goals on different bases is listed in `cross_base`.
  * Claims: a path covers itself and everything under it ("ui/src", "ui/src/", "ui/src/**", "/ui/src" all
    cover "ui/src/a.ts"); a glob is cut back to the directory before its first wildcard ("src/**/*.ts" is
    "src", "ui/src*" is "ui", "*.py" is everything); other tokens such as "contract:x" match exactly; "*" or
    "." (nothing known) clashes with everything.
  * `ready`: a goal may start once each blocker has landed on the goal's own base. Candidates go in
    (priority, wave, rank) order. A ready goal that cannot start (claims taken, or no seat) still reserves
    its claims for this round, so a lower-ranked goal cannot jump it. Goals awaiting merge hold their
    claims but not a seat. No --seats means no seat limit. A goal that can no longer start without a
    person (a stopped blocker upstream, or a blocker merged into a different base than its own and not
    yet in its own) is `stuck`; a blocker merged into the same base but not yet recorded as landed is
    only `waiting`.
"""
import argparse
import heapq
import json
import sys

PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}


def _prio(p):
    s = str(p or "").strip().upper()
    if s.startswith("PRIORITY:"):
        s = s[len("PRIORITY:"):].strip()
    return PRIORITY.get(s, 5)


def _norm(claim):
    c = str(claim).strip()
    while c.startswith("./"):
        c = c[2:]
    c = c.lstrip("/")
    cut = min([i for i in (c.find("*"), c.find("?"), c.find("[")) if i >= 0] or [len(c)])
    globbed = cut < len(c)
    c = c[:cut]
    if globbed and c and not c.endswith("/"):
        # "ui/src*" or "docs/a?.md": the glob sits inside a name, so the claim is that name's directory.
        c = c.rsplit("/", 1)[0] if "/" in c else ""
    c = c.rstrip("/")
    return "*" if c in ("", ".") else c


def _covers(a, b):
    return a == b or b.startswith(a + "/")


def clash(claims_a, claims_b):
    """The first pair of claims that overlap, or None."""
    a = [_norm(c) for c in (claims_a or ["*"])]
    b = [_norm(c) for c in (claims_b or ["*"])]
    for x in a:
        for y in b:
            if x == "*" or y == "*" or _covers(x, y) or _covers(y, x):
                return (x, y)
    return None


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _goals(doc):
    goals = {}
    for g in doc["goals"]:
        n = int(g["n"])
        if n in goals:
            raise ValueError(f"goal #{n} appears twice in goals.json")
        goals[n] = {
            "n": n,
            "title": g.get("title", ""),
            "priority": g.get("priority") or "",
            "base": g.get("base") or "",
            "blockers": {int(b) for b in g.get("blockers", [])},
            "external": {int(b) for b in g.get("external_blockers", [])},
            "claims": list(g.get("claims") or ["*"]),
            "held": g.get("held"),
            "open_prs": sorted({int(x) for x in (g.get("open_prs") or [])}),
        }
    for n, g in goals.items():
        inside = {b for b in g["external"] if b in goals}
        outside = {b for b in g["blockers"] if b not in goals}
        g["blockers"] = (g["blockers"] | inside) - outside
        g["external"] = (g["external"] | outside) - inside
        g["self_block"] = n in g["blockers"]
        g["blockers"] = sorted(g["blockers"] - {n})
        g["external"] = sorted(g["external"])
    return goals


def _cycles(goals, nodes):
    """Members of a dependency cycle (Tarjan, iterative)."""
    index, low, on, stack, out = {}, {}, set(), [], set()
    counter = [0]
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter([b for b in goals[root]["blockers"] if b in nodes]))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter([b for b in goals[nxt]["blockers"] if b in nodes])))
                elif nxt in on:
                    low[v] = min(low[v], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    out.update(comp)
    return out


def _plan(goals):
    batch = set(goals)
    held = {}
    for n, g in goals.items():
        if g["held"]:
            held[n] = str(g["held"])
        elif g["open_prs"]:
            held[n] = "an open pull request already links it (" + ", ".join(f"#{b}" for b in g["open_prs"]) + ")"
        elif g["self_block"]:
            held[n] = "lists itself as a blocker"
        elif g["external"]:
            held[n] = "waits on " + ", ".join(f"#{b}" for b in g["external"]) + " (outside the batch)"
    for n in sorted(_cycles(goals, batch - set(held))):
        held.setdefault(n, "dependency cycle")
    changed = True
    while changed:
        changed = False
        for n, g in sorted(goals.items()):
            if n in held:
                continue
            bad = [b for b in g["blockers"] if b in held]
            if bad:
                held[n] = "waits on held " + ", ".join(f"#{b}" for b in bad)
                changed = True

    live = sorted(batch - set(held))
    dependents = {n: set() for n in live}
    for n in live:
        for b in goals[n]["blockers"]:
            if b in dependents:
                dependents[b].add(n)

    def descendants(n):
        seen, todo = set(), list(dependents[n])
        while todo:
            d = todo.pop()
            if d not in seen:
                seen.add(d)
                todo.extend(dependents[d])
        return seen

    reach = {n: len(descendants(n)) for n in live}

    def rank(n):
        return (_prio(goals[n]["priority"]), -reach[n], n)

    indeg = {n: len([b for b in goals[n]["blockers"] if b in dependents]) for n in live}
    heap = [(rank(n), n) for n in live if indeg[n] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        _, n = heapq.heappop(heap)
        order.append(n)
        for d in sorted(dependents[n]):
            indeg[d] -= 1
            if indeg[d] == 0:
                heapq.heappush(heap, (rank(d), d))

    wave, members, moved = {}, {}, {}
    for n in order:
        w = 1 + max([wave[b] for b in goals[n]["blockers"] if b in wave] or [0])
        first = w
        while True:
            hit = None
            for m in members.get(w, []):
                c = clash(goals[n]["claims"], goals[m]["claims"])
                if c:
                    hit = (m, c)
                    break
            if not hit:
                break
            if w == first:
                moved[n] = f"shares {hit[1][0]} with #{hit[0]}"
            w += 1
        wave[n] = w
        members.setdefault(w, []).append(n)

    cross = {}
    for n in live:
        for b in goals[n]["blockers"]:
            bb, nb = goals[b]["base"], goals[n]["base"]
            if bb and nb and bb != nb:
                cross.setdefault(str(n), []).append(
                    f"#{b} lands on {bb}, which {nb} does not contain until {nb} catches up")

    return {
        "waves": [sorted(members[w], key=rank) for w in sorted(members)],
        "held": {str(n): held[n] for n in sorted(held)},
        "moved": {str(n): moved[n] for n in sorted(moved)},
        "cross_base": cross,
        "wave_of": {str(n): wave[n] for n in sorted(wave)},
        "order": sorted(live, key=lambda n: (_prio(goals[n]["priority"]), wave[n], -reach[n], n)),
    }


def plan(doc):
    return _plan(_goals(doc))


def ready(doc, state, seats=None):
    goals = _goals(doc)
    p = _plan(goals)
    landed = {int(k): set(v) for k, v in (state.get("landed") or {}).items()}
    finished = {int(n) for n in state.get("finished", [])}
    running = {int(n) for n in state.get("running", [])}
    awaiting = {int(n) for n in state.get("awaiting_merge", [])}
    stopped = {int(k): v for k, v in (state.get("stopped") or {}).items()}
    pr_files = {int(k): v for k, v in (state.get("pr_files") or {}).items()}

    def has_landed(b, base):
        if base:
            return base in landed.get(b, set())
        return b in landed or b in finished

    # Stuck: cannot start without a person. Walk in dependency order so it spreads downstream.
    stuck = {}
    for n in [m for w in p["waves"] for m in w]:
        if n in finished or n in running or n in awaiting or n in stopped:
            continue
        why = []
        for b in goals[n]["blockers"]:
            if has_landed(b, goals[n]["base"]):
                continue
            if b in stopped:
                why.append(f"#{b} (stopped: {stopped[b]})")
            elif str(b) in stuck:
                why.append(f"#{b} (stuck)")
            elif b in finished and b in goals and goals[b]["base"] and goals[n]["base"] \
                    and goals[b]["base"] != goals[n]["base"]:
                why.append(f"#{b} (merged, not in {goals[n]['base']})")
        if why:
            stuck[str(n)] = "waits on " + ", ".join(why)

    def claims_of(n):
        return pr_files.get(n) or (goals[n]["claims"] if n in goals else None)

    taken = [(n, claims_of(n)) for n in sorted(running | awaiting) if claims_of(n)]
    free = None if seats is None else max(0, int(seats) - len([n for n in running if n in goals]))
    launch, waiting = [], {}
    for n in p["order"]:
        if n in finished or n in running or n in awaiting or n in stopped or str(n) in stuck:
            continue
        unmet = [f"#{b}" for b in goals[n]["blockers"] if not has_landed(b, goals[n]["base"])]
        if unmet:
            waiting[str(n)] = "waits on " + ", ".join(unmet)
            continue
        hit = None
        for m, c in taken:
            x = clash(goals[n]["claims"], c)
            if x:
                hit = (m, x)
                break
        if hit:
            waiting[str(n)] = f"shares {hit[1][0]} with #{hit[0]}"
        elif free is not None and free <= 0:
            waiting[str(n)] = "no free seat"
        else:
            launch.append(n)
            if free is not None:
                free -= 1
        taken.append((n, goals[n]["claims"]))
    return {"launch": launch, "waiting": waiting, "stuck": stuck, "held": p["held"],
            "stopped": {str(k): v for k, v in sorted(stopped.items())},
            "seats_free": free if free is not None else "unlimited"}


def _md(p, doc):
    goals = _goals(doc)
    lines = ["| Wave | Goal | Priority | Title | Note |", "|---|---|---|---|---|"]
    for i, w in enumerate(p["waves"], 1):
        for n in w:
            note = "; ".join([p["moved"].get(str(n), "")] + p["cross_base"].get(str(n), [])).strip("; ")
            lines.append(f"| {i} | #{n} | {goals[n]['priority'] or '-'} | {goals[n]['title']} | {note} |")
    for n, why in p["held"].items():
        lines.append(f"| held | #{n} | {goals[int(n)]['priority'] or '-'} | {goals[int(n)]['title']} | {why} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("plan")
    pp.add_argument("goals")
    pp.add_argument("--md", action="store_true", help="print a table for people instead of JSON")
    rp = sub.add_parser("ready")
    rp.add_argument("goals")
    rp.add_argument("state")
    rp.add_argument("--seats", type=int, default=None, help="most goals running at once (default: no limit)")
    a = ap.parse_args(argv)
    try:
        doc = _load(a.goals)
        if a.cmd == "plan":
            p = plan(doc)
            print(_md(p, doc) if a.md else json.dumps(p, indent=2))
        else:
            print(json.dumps(ready(doc, _load(a.state), a.seats), indent=2))
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as e:
        print(f"waves.py: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

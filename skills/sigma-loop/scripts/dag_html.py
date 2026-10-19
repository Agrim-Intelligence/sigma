"""One readable, offline HTML page per chain, from the stored dependency map (#1924). Library; `dag.py html` is its CLI.

A chain is ONE goal plus the open prerequisites it waits on: exactly the members `chain.py plan N` reads from the
map, because the page calls the same code (`chain.map_closure`, the plan's map half, and `chain_forecast`, the
plan's marker rule). It is never a whole weakly connected component: two end goals glued by a shared prerequisite
are two pages. `--all` writes a page for every end goal (an open issue that no open goal waits on and that waits on
something open, as `dag.py chains` lists them) and an index page.

What a page holds, and only this: numbers, the optional titles the caller passes, the map's life, priority rank and
unit token, the forecast markers and the edges. The map holds no title, label text or body, so a title comes only
from `--titles FILE` (a JSON object `{"<number>": "<title>"}`, or a list of `{"number", "title"}`; 4 MiB at most) and
a page without it names members by number. A title is scrubbed: any URL and the letters `http` are neutralised, so
no page carries a link or a network reference, and it is cut at 100 characters. No issue body is ever read here.

Reads the stored map only, makes no GitHub call (it never imports a network helper on this path), and writes only
`<sdlc>/state/dag-html/` or `--out`. An `--out` inside a git checkout that does not ignore it is refused (a page
must never make the root checkout dirty), and an existing page of an earlier run is overwritten, never deleted.
A map that is not fresh (older than twice its TTL, partial) is still drawn, with the reason at the top: the page
is a view, and says what it is a view of.

States (colour, and the same word written in the node): the plan's own markers `ready now`, `after #N`,
`needs decision`, `someone else's`, `checkpoint`, `not workable`, plus `closed` (a resolved prerequisite, drawn
muted on the left), `outside` (an open pull request the plan lists as external) and `cycle` (a member of a
dependency cycle, which the plan refuses). From the map alone only the life is known, so the markers are the map's
reading; the page says so, and that the live plan also sees assignees, checkpoints and not-a-goal labels. Edges:
explicit (an issue body) solid, unconfirmed (a wait read from prose, live plan only) dashed, ledger (any other
recorded source) dotted.

Layout, in Python: one column per depth (blockers left, the work that waits on them to the right), closed and
outside prerequisites in a first column; an edge crossing columns passes through a dummy slot in each, so it runs in
a channel and not through a node; the order within a column is a barycentre sweep, down then up, keeping the
ordering with the fewest crossings seen. Then inline SVG, CSS variables with light and dark themes, a side panel
filled by a little vanilla JS on click, and zoom and pan by the SVG viewBox. No library, no CDN, nothing fetched.

Bounds. `MEMBER_CAP` (80) open members is the readable ceiling: a chain over it is still written, with a notice at
the top. Titles are read from at most 4 MiB. Cost per page is O(passes x edges x depth) for the layout and
O(members + edges) for the rest; dummy slots are skipped (long edges drawn direct, and said so) past
`DUMMY_CAP`. Measured on one synthetic shape (shallow, no tangle): 400 members 248 KB in 0.06 s, 1,000 members 612 KB in
0.35 s; a tangled chain is not measured, and `tests/test_dag_html.py` bounds a 400-member page at 1.5 MB. `--all` is
the sum of its chains, one page each.
"""
import collections
import html
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import textwrap
import time

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


dag_chain = _load("dag_chain")
chain = _load("chain")
chain_forecast = chain.chain_forecast
dag_store = dag_chain.dag_store
dag_core = dag_chain.dag_core

MEMBER_CAP = 80                 # open members a page draws readably; over it the page still writes, with a notice
DUMMY_CAP = 6000                # layout slots for long edges; past it they are drawn direct, and the page says so
TITLES_MAX_BYTES = 4 * 1024 * 1024
TITLE_CHARS = 100
SWEEPS, SWEEPS_BIG = 8, 2
BIG_SEGMENTS = 3000
NODE_W, NODE_H, DUMMY_H, GAP_Y, COL_GAP, MARGIN = 220, 68, 14, 14, 84, 28
EXIT = {"ok": 0, "refused": 2, "unusable": 3, "off": 4}

CLASS = {"ready now": "ready", "after": "after", "needs decision": "decision", "someone else's": "else",
         "checkpoint": "check", "not workable": "stuck", "closed": "closed", "outside": "outside", "cycle": "cycle"}
LEGEND = (("ready now", "can be claimed now"), ("after", "waits on a member still open"),
          ("needs decision", "parked, unarmed or not a goal"), ("someone else's", "claimed or assigned elsewhere"),
          ("checkpoint", "a gate, never worked"), ("not workable", "held, blocked or unreadable"),
          ("closed", "resolved prerequisite"), ("outside", "open pull request outside the chain"),
          ("cycle", "in a dependency cycle"))
EDGES = (("explicit", "written in an issue body"), ("unconfirmed", "read from prose, not confirmed"),
         ("ledger", "any other recorded source"))


class HtmlError(Exception):
    """A request that writes nothing. `code` is the exit code (2, 3 or 4); `str(self)` is the line."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ inputs

def clean_title(text):
    """A title safe for the page: one line, no URL, no `http`, at most TITLE_CHARS characters."""
    t = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://\S*", "(link)", str(text))
    t = re.sub(r"(?i)http", "ht\u200btp", t)
    t = " ".join("".join(c if c.isprintable() else " " for c in t).split())
    return t if len(t) <= TITLE_CHARS else t[:TITLE_CHARS - 1].rstrip() + "\u2026"


def read_titles(path):
    """`{number: title}` from a JSON file, bounded; raises HtmlError 2 on anything but the two documented shapes."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(TITLES_MAX_BYTES + 1)
    except OSError as exc:
        raise HtmlError(2, "dag html: cannot read the titles file (%s)" % type(exc).__name__)
    if len(raw) > TITLES_MAX_BYTES:
        raise HtmlError(2, "dag html: the titles file is over %d bytes; refusing rather than truncating"
                        % TITLES_MAX_BYTES)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HtmlError(2, "dag html: the titles file is not JSON")
    if isinstance(doc, dict):
        pairs = list(doc.items())
    elif isinstance(doc, list):
        pairs = [(i.get("number"), i.get("title")) for i in doc if isinstance(i, dict)]
    else:
        raise HtmlError(2, "dag html: the titles file must be an object or a list of {number, title}")
    out = {}
    for k, v in pairs:
        try:
            n = int(str(k).lstrip("#"))
        except ValueError:
            continue
        if isinstance(v, str) and v.strip():
            out[n] = clean_title(v)
    return out


def check_out(out_dir):
    """Refuse an output directory inside a git checkout that does not ignore it. Local git only; no checkout, or
    no git, means there is nothing tracked to dirty."""
    probe = pathlib.Path(os.path.realpath(str(out_dir)))
    anchor = probe
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    try:
        top = subprocess.run(["git", "-C", str(anchor), "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=20)
        if top.returncode != 0:
            return
        ign = subprocess.run(["git", "-C", top.stdout.strip(), "check-ignore", "-q", "--", str(probe / "index.html")],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return
    if ign.returncode != 0:
        raise HtmlError(2, "dag html: %s is inside a git checkout and is not ignored; pages would dirty it. Use the "
                           "default under .sdlc/state/ (add `.sdlc/state/` to .gitignore) or a path outside the "
                           "repository" % out_dir)


def _open(sdlc, now):
    """`(map, mode, config, ok, why)`; raises HtmlError 4 (off) or 3 (no readable map). A map that is merely not
    fresh is returned with `ok` False and the reason: the page is a view and says so."""
    config = dag_chain._config(sdlc)
    md = dag_store.mode(config)
    if md == "off":
        raise HtmlError(EXIT["off"], "dag: off")
    m, why = dag_store.load(sdlc, config)
    if m is None:
        raise HtmlError(EXIT["unusable"], "dag: map unusable (%s)" % why)
    ok, reason = dag_store.usable(m, now, dag_store.ttl_minutes(config))
    return m, md, config, ok, reason


def _age(seconds):
    s = max(0, int(seconds))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return "%d%s" % (s // size, unit)
    return "%ds" % s


# ------------------------------------------------------------------ the model of one chain

def _marker_text(marker, after):
    if marker != "after":
        return marker
    return "after " + ", ".join("#%d" % a for a in after[:2]) + (" +%d" % (len(after) - 2) if len(after) > 2 else "")


def build(m, config, goal, titles=None, now=None, fresh=(True, "ok")):
    """The model of the chain for `goal`: nodes, edges, the forecast and the header facts. Raises HtmlError 2 when
    `goal` is not in the map, is closed, or is a pull request."""
    now = time.time() if now is None else now
    titles = titles or {}
    if goal not in {n["number"] for n in m["nodes"]}:
        raise HtmlError(EXIT["refused"], "dag: goal %d is not in the map" % goal)
    try:
        out, g, esrc, body = chain.map_closure(m, goal, strict=False, now=now)
    except chain.ChainRefused as exc:
        raise HtmlError(EXIT["refused"], str(exc))
    if g.nodes[goal].kind != "issue":
        raise HtmlError(EXIT["refused"], "dag: #%d is a pull request, not a goal" % goal)
    fc = chain_forecast.forecast(out, config)
    marks = {x["number"]: x for x in fc["members"]}
    nodes = {}
    for r in out["members"]:
        mk = marks[r["number"]]
        nodes[r["number"]] = {"n": r["number"], "m": mk["marker"], "after": mk["after"], "why": mk["why"],
                              "life": r["life"], "prio": r["prio"], "unit": r.get("unit"),
                              "depth": r["depth"]}
    for n in out["done"]:
        nodes[n] = {"n": n, "m": "closed", "after": [], "why": "resolved", "life": None,
                    "prio": g.nodes[n].prio, "unit": None, "depth": 0}
    for e in out["externals"]:
        n = int(e["ref"].lstrip("#"))
        nodes[n] = {"n": n, "m": "outside", "after": [], "why": "an open pull request, outside the chain's goals",
                    "life": None, "prio": None, "unit": None, "depth": 0}
    for c in body["cycles"]:
        for n in c:
            if n not in nodes:
                nodes[n] = {"n": n, "m": "cycle", "after": [], "why": "in a dependency cycle; the plan refuses it",
                            "life": None, "prio": g.nodes[n].prio, "unit": None, "depth": 0}
    drawn = set(nodes)
    edges = sorted({(a, b): "explicit" if esrc.get((a, b)) == "body" else "ledger"
                    for a, b, _s in g.edges if a in drawn and b in drawn}.items())
    waits, frees = collections.defaultdict(list), collections.defaultdict(list)
    for (a, b), _t in edges:
        waits[b].append(a)
        frees[a].append(b)
    for n, d in nodes.items():
        d["waits"], d["frees"] = sorted(waits[n]), sorted(frees[n])
        d["t"] = titles.get(n)
    open_rows = [d for d in nodes.values() if d["m"] not in ("closed", "outside", "cycle")]
    stops = [{"n": s["number"], "m": s["marker"], "why": s["why"]} for s in fc["stops"]]
    return {"goal": goal, "nodes": nodes, "edges": [(a, b, t) for (a, b), t in edges], "stops": stops,
            "forecast": fc["line"], "notes": fc["notes"], "reason": fc["reason"], "finish": len(fc["finish"]),
            "open": len(open_rows), "closed": len(out["done"]), "outside": len(out["externals"]),
            "cycles": body["cycles"], "depth": max([d["depth"] for d in open_rows] or [0]),
            "decisions": sum(1 for d in open_rows if d["m"] == "needs decision"),
            "over_cap": len(open_rows) > MEMBER_CAP, "titled": bool(titles),
            "built_at": out["map_built_at"], "age": out["map_age_seconds"], "fresh": fresh, "mode": None}


# ------------------------------------------------------------------ the layout

def _inversions(ys):
    """Pairs i < j with ys[i] > ys[j] (a Fenwick tree over the ranks)."""
    rank = {v: i + 1 for i, v in enumerate(sorted(set(ys)))}
    tree, total = [0] * (len(rank) + 1), 0
    for seen, y in enumerate(ys):
        r, below = rank[y], 0
        i = r
        while i:
            below += tree[i]
            i -= i & -i
        total += seen - below
        i = r
        while i <= len(rank):
            tree[i] += 1
            i += i & -i
    return total


def crossings(layers, segs):
    """Edge crossings between adjacent columns for the column order `layers`. `segs` is [(u, v)] with v one
    column right of u. Segments sharing an end do not cross."""
    pos = {k: i for layer in layers for i, k in enumerate(layer)}
    col = {k: c for c, layer in enumerate(layers) for k in layer}
    by = collections.defaultdict(list)
    for u, v in segs:
        by[col[u]].append((pos[u], pos[v]))
    total = 0
    for pairs in by.values():
        pairs.sort()
        total += _inversions([y for _x, y in pairs])
    return total


def _barycentre(layers, nbrs, c, pos):
    def key(item):
        i, k = item
        ns = [pos[x] for x in nbrs.get(k, ())]
        return (sum(ns) / len(ns) if ns else float(i), i)
    return [k for _i, k in sorted(enumerate(layers[c]), key=key)]


def layout(cols, edges, rank, sweeps=None):
    """Place the nodes. `cols` maps number -> column, `edges` is [(blocker, blocked)] and `rank` maps number ->
    priority rank (the starting order within a column). -> {layers, segs, paths (edge -> list of keys), width,
    height, xy (key -> (x, y, h)), crossings, direct (long edges drawn direct)}. A key is a number or a dummy
    slot `("d", blocker, blocked, column)`. An edge that does not run left to right (a cycle's) is `direct`."""
    ncol = max(cols.values(), default=-1) + 1
    layers = [sorted((n for n, c in cols.items() if c == i), key=lambda n: (rank.get(n, 0), n))
              for i in range(ncol)]
    paths, direct = {}, []
    forward = [(a, b) for a, b in edges if cols[a] < cols[b]]
    direct += [(a, b) for a, b in edges if cols[a] >= cols[b]]
    slots = sum(cols[b] - cols[a] - 1 for a, b in forward)
    simple = slots > DUMMY_CAP
    segs = []
    for a, b in forward:
        if simple and cols[b] - cols[a] > 1:
            direct.append((a, b))
            continue
        ks = [a] + [("d", a, b, c) for c in range(cols[a] + 1, cols[b])] + [b]
        for i, k in enumerate(ks[1:-1], 1):
            layers[cols[a] + i].append(k)
        paths[(a, b)] = ks
        segs += list(zip(ks, ks[1:]))
    succ, pred = collections.defaultdict(list), collections.defaultdict(list)
    for u, v in segs:
        succ[u].append(v)
        pred[v].append(u)
    best, best_c = [list(x) for x in layers], crossings(layers, segs)
    for _ in range(sweeps if sweeps is not None else (SWEEPS_BIG if len(segs) > BIG_SEGMENTS else SWEEPS)):
        for c in range(1, ncol):
            pos = {k: i for i, k in enumerate(layers[c - 1])}
            layers[c] = _barycentre(layers, pred, c, pos)
        for c in range(ncol - 2, -1, -1):
            pos = {k: i for i, k in enumerate(layers[c + 1])}
            layers[c] = _barycentre(layers, succ, c, pos)
        now_c = crossings(layers, segs)
        if now_c < best_c:
            best, best_c = [list(x) for x in layers], now_c
    layers = best
    heights = [sum(NODE_H if isinstance(k, int) else DUMMY_H for k in lay) + GAP_Y * max(0, len(lay) - 1)
               for lay in layers]
    tall = max(heights, default=0)
    xy = {}
    for c, lay in enumerate(layers):
        y = MARGIN + (tall - heights[c]) / 2
        for k in lay:
            h = NODE_H if isinstance(k, int) else DUMMY_H
            xy[k] = (MARGIN + c * (NODE_W + COL_GAP), y, h)
            y += h + GAP_Y
    return {"layers": layers, "segs": segs, "paths": paths, "direct": direct, "xy": xy, "crossings": best_c,
            "width": 2 * MARGIN + ncol * NODE_W + max(0, ncol - 1) * COL_GAP, "height": 2 * MARGIN + tall}


def _edge_d(layout_, a, b):
    """The SVG path of one edge: through its dummy slots, with horizontal tangents."""
    xy = layout_["xy"]
    ks = layout_["paths"].get((a, b))
    if ks is None:
        ax, ay, ah = xy[a]
        bx, by, bh = xy[b]
        x0, y0, x1, y1 = ax + NODE_W, ay + ah / 2, bx, by + bh / 2
        dx = max(60.0, abs(x1 - x0) / 2)
        return "M%.1f %.1f C%.1f %.1f %.1f %.1f %.1f %.1f" % (x0, y0, x0 + dx, y0, x1 - dx, y1, x1, y1)
    pts = []
    for i, k in enumerate(ks):
        x, y, h = xy[k]
        if isinstance(k, int):
            pts.append((x + NODE_W if i == 0 else x, y + h / 2))
        else:
            pts.append((x + NODE_W / 2, y + h / 2))
    d = "M%.1f %.1f" % pts[0]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx = (x1 - x0) / 2
        d += " C%.1f %.1f %.1f %.1f %.1f %.1f" % (x0 + dx, y0, x1 - dx, y1, x1, y1)
    return d


# ------------------------------------------------------------------ the page

_THEME = {
    "light": {"bg": "#fbfbfa", "panel": "#ffffff", "ink": "#1b1f23", "muted": "#5b6168", "line": "#d6d8da",
              "edge": "#6b7076", "accent": "#2457c5",
              "ready": ("#dcf3e3", "#2f8f4e"), "after": ("#e3ebfa", "#3b6fc4"), "decision": ("#fdecc8", "#b7791f"),
              "else": ("#eadcf7", "#7a4cb0"), "check": ("#d5f0f0", "#1f8a8a"), "stuck": ("#fadadd", "#c0392b"),
              "closed": ("#eeeeec", "#9a9a96"), "outside": ("#fbfbfa", "#8a8a86"), "cycle": ("#fadadd", "#8e1f14")},
    "dark": {"bg": "#14171a", "panel": "#1b1f23", "ink": "#e6e8ea", "muted": "#a0a5aa", "line": "#31363b",
             "edge": "#9aa0a6", "accent": "#7fa6f2",
             "ready": ("#16361f", "#4fbf74"), "after": ("#1b2a48", "#6c9be8"), "decision": ("#40300f", "#e0a53a"),
             "else": ("#2e2044", "#b18be0"), "check": ("#0f3636", "#4fc3c3"), "stuck": ("#451a1f", "#ef7a70"),
             "closed": ("#25282b", "#6d7277"), "outside": ("#14171a", "#7a7f84"), "cycle": ("#451a1f", "#ff9a8f")},
}


def _vars(name):
    t = _THEME[name]
    out = ["--%s:%s" % (k, v) for k, v in t.items() if isinstance(v, str)]
    out += ["--%s-f:%s;--%s-s:%s" % (k, v[0], k, v[1]) for k, v in t.items() if isinstance(v, tuple)]
    return ";".join(out)


CSS = """
:root{%(light)s;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){%(dark)s;color-scheme:dark}}
:root[data-theme=dark]{%(dark)s;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}
header.top{padding:18px 24px 12px;border-bottom:1px solid var(--line)}
h1{margin:0 0 4px;font-size:21px;font-weight:650}
h1 small{font-weight:400;color:var(--muted);font-size:14px}
.facts{display:flex;flex-wrap:wrap;gap:6px 22px;color:var(--muted);margin:4px 0 8px}
.facts b{color:var(--ink);font-weight:600}
.held,.fc{margin:4px 0}
.notice{margin:8px 0;padding:8px 12px;border-left:4px solid var(--decision-s);background:var(--decision-f);border-radius:4px}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:10px;font-size:12.5px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:14px;height:14px;border-radius:3px;border:2px solid;display:inline-block}
.ln{width:26px;height:0;border-top:2px solid var(--edge);display:inline-block}
.ln.unconfirmed{border-top-style:dashed}.ln.ledger{border-top-style:dotted}
main{display:grid;grid-template-columns:minmax(0,1fr) 330px;min-height:68vh}
#stage{position:relative;overflow:hidden;border-right:1px solid var(--line)}
#g{width:100%%;height:72vh;min-height:420px;display:block;cursor:grab;touch-action:none}
#g.drag{cursor:grabbing}
#tools{position:absolute;top:10px;left:10px;display:flex;gap:6px;z-index:2}
#tools label{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:3px 10px;display:flex;align-items:center;gap:6px}
button{font:inherit;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 10px;cursor:pointer}
button:hover{border-color:var(--accent)}
aside{padding:16px 18px;background:var(--panel);overflow:auto;max-height:72vh}
aside h2{font-size:16px;margin:0 0 6px}
dl{margin:10px 0 0}dt{color:var(--muted);font-size:12px;margin-top:10px;text-transform:uppercase;letter-spacing:.04em}
dd{margin:2px 0 0}
dd button{display:block;width:100%%;text-align:left;margin:3px 0}
.n rect{fill:var(--c-f);stroke:var(--c-s);stroke-width:1.6;rx:7}
.n text{fill:var(--ink);font-size:12px;pointer-events:none}
.n .num{font-weight:700;font-size:13.5px}.n .mk{fill:var(--muted);font-size:10.5px}
.n{cursor:pointer;outline:none}.n:hover rect,.n:focus rect{stroke-width:2.6}
.n.sel rect{stroke-width:3.4;stroke:var(--accent)}
.n.goal rect{stroke-width:2.6}
.n .tag{fill:var(--accent);font-size:10px;font-weight:700}
.c-ready{--c-f:var(--ready-f);--c-s:var(--ready-s)}.c-after{--c-f:var(--after-f);--c-s:var(--after-s)}
.c-decision{--c-f:var(--decision-f);--c-s:var(--decision-s)}.c-else{--c-f:var(--else-f);--c-s:var(--else-s)}
.c-check{--c-f:var(--check-f);--c-s:var(--check-s)}.c-stuck{--c-f:var(--stuck-f);--c-s:var(--stuck-s)}
.c-closed{--c-f:var(--closed-f);--c-s:var(--closed-s)}.c-closed text{fill:var(--muted)}
.c-outside{--c-f:var(--outside-f);--c-s:var(--outside-s)}.c-outside rect{stroke-dasharray:5 4}
.c-cycle{--c-f:var(--cycle-f);--c-s:var(--cycle-s)}.c-cycle rect{stroke-dasharray:2 3}
.sw.c-outside{border-style:dashed}.sw{background:var(--c-f);border-color:var(--c-s)}
.e{fill:none;stroke:var(--edge);stroke-width:1.4;opacity:.7}
.e.unconfirmed{stroke-dasharray:6 4}.e.ledger{stroke-dasharray:2 4}
.e.ce,.n.cl{display:none}.show-closed .e.ce,.show-closed .n.cl{display:inline}
.e.hot{stroke:var(--accent);stroke-width:2.6;opacity:1}.e.cool{opacity:.14}
#arr path{fill:var(--edge)}
table{border-collapse:collapse;margin:16px 24px;width:calc(100%% - 48px)}
th,td{text-align:left;padding:7px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12.5px}td.num{text-align:right;font-variant-numeric:tabular-nums}
@media (max-width:900px){main{grid-template-columns:1fr}aside{max-height:none}#stage{border-right:0}}
@media print{#tools,aside{display:none}main{display:block}}
"""

JS = r"""
(function(){
var D=JSON.parse(document.getElementById('data').textContent),svg=document.getElementById('g');
var head=document.getElementById('p-head'),body=document.getElementById('p-body'),hint=document.getElementById('p-hint');
var base=svg.viewBox.baseVal,B={x:base.x,y:base.y,w:base.width,h:base.height},V={x:B.x,y:B.y,w:B.w,h:B.h},sel=null;
function apply(){svg.setAttribute('viewBox',V.x+' '+V.y+' '+V.w+' '+V.h);}
function px(){var m=svg.getScreenCTM();return m?m.a:1;}
function zoom(f,cx,cy){var w=Math.min(B.w*4,Math.max(B.w/20,V.w/f)),h=V.h*w/V.w;
 if(cx===undefined){cx=V.x+V.w/2;cy=V.y+V.h/2;}
 V.x=cx-(cx-V.x)*w/V.w;V.y=cy-(cy-V.y)*h/V.h;V.w=w;V.h=h;apply();}
function fit(){V={x:B.x,y:B.y,w:B.w,h:B.h};apply();}
var cl=document.getElementById('zcl'),HID={x:B.x,y:B.y,w:B.w,h:B.h};
if(cl){cl.onchange=function(){var a=svg.dataset.closed.split(' ').map(Number);
 svg.classList.toggle('show-closed',cl.checked);B=cl.checked?{x:a[0],y:a[1],w:a[2],h:a[3]}:HID;fit();};}
function start(){var r=svg.getBoundingClientRect(),s=Math.max(r.width/B.w,0.8);
 if(r.width>0&&r.width/B.w<0.8){V.w=r.width/s;V.h=r.height/s;V.x=B.x;V.y=B.y+Math.max(0,(B.h-V.h)/2);apply();}}
document.getElementById('zin').onclick=function(){zoom(1.3);};
document.getElementById('zout').onclick=function(){zoom(1/1.3);};
document.getElementById('zfit').onclick=fit;
document.getElementById('zth').onclick=function(){var r=document.documentElement,
 d=r.getAttribute('data-theme')==='dark'||(!r.getAttribute('data-theme')&&matchMedia('(prefers-color-scheme: dark)').matches);
 r.setAttribute('data-theme',d?'light':'dark');};
svg.addEventListener('wheel',function(e){e.preventDefault();var m=svg.getScreenCTM().inverse(),p=svg.createSVGPoint();
 p.x=e.clientX;p.y=e.clientY;p=p.matrixTransform(m);zoom(e.deltaY<0?1.15:1/1.15,p.x,p.y);},{passive:false});
var drag=null;
svg.addEventListener('pointerdown',function(e){if(e.target.closest('.n'))return;drag={x:e.clientX,y:e.clientY};
 svg.classList.add('drag');svg.setPointerCapture(e.pointerId);});
svg.addEventListener('pointermove',function(e){if(!drag)return;var k=px();V.x-=(e.clientX-drag.x)/k;V.y-=(e.clientY-drag.y)/k;
 drag={x:e.clientX,y:e.clientY};apply();});
function end(){drag=null;svg.classList.remove('drag');}
svg.addEventListener('pointerup',end);svg.addEventListener('pointercancel',end);
function name(n){var d=D.nodes[n];return '#'+n+(d.t?' '+d.t:'');}
function btn(n){var b=document.createElement('button');b.textContent=name(n)+' ('+D.nodes[n].mk+')';
 b.onclick=function(){select(n,true);};return b;}
function row(k,v){var t=document.createElement('dt'),d=document.createElement('dd');t.textContent=k;
 if(v&&v.nodeType){d.appendChild(v);}else{d.textContent=v;}body.appendChild(t);body.appendChild(d);}
function list(ns){var f=document.createElement('div');if(!ns.length){f.textContent='nothing in this chain';}
 ns.forEach(function(n){f.appendChild(btn(n));});return f;}
function select(n,pan){
 if(sel!==null){var o=svg.querySelector('.n[data-n="'+sel+'"]');if(o)o.classList.remove('sel');}
 svg.querySelectorAll('.e').forEach(function(e){e.classList.remove('hot','cool');});
 if(n===null){sel=null;head.textContent='Select a member';hint.hidden=false;body.textContent='';return;}
 sel=n;var d=D.nodes[n],el=svg.querySelector('.n[data-n="'+n+'"]');if(el)el.classList.add('sel');
 svg.querySelectorAll('.e').forEach(function(e){var a=+e.dataset.a,b=+e.dataset.b;
  e.classList.add(a===n||b===n?'hot':'cool');});
 head.textContent='#'+n+(d.t?' '+d.t:'');hint.hidden=true;body.textContent='';
 row('State',d.mk);row('Why',d.why||'nothing holds it');
 if(d.life)row('Life',d.life);if(d.prio)row('Priority',d.prio);if(d.unit)row('Unit',d.unit);
 row('Waits on',list(d.waits));row('Frees',list(d.frees));
 if(n===D.goal)row('Role','the end of this chain');
 if(pan&&el){var r=el.getBBox();V.x=r.x+r.width/2-V.w/2;V.y=r.y+r.height/2-V.h/2;apply();}}
svg.querySelectorAll('.n').forEach(function(g){var n=+g.dataset.n;
 g.addEventListener('click',function(e){e.stopPropagation();select(sel===n?null:n,false);});
 g.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();select(n,false);}});});
document.addEventListener('keydown',function(e){if(e.key==='Escape')select(null,false);});
start();
})();
"""


def _esc(s):
    return html.escape(str(s), quote=True)


def _data_json(model):
    nodes = {}
    for n, d in model["nodes"].items():
        nodes[str(n)] = {"t": d["t"], "mk": _marker_text(d["m"], d["after"]), "why": d["why"], "life": d["life"],
                         "prio": d["prio"], "unit": d["unit"], "waits": d["waits"], "frees": d["frees"]}
    return json.dumps({"goal": model["goal"], "nodes": nodes}, sort_keys=True).replace("<", "\\u003c")


def _lines(text, width, count):
    return textwrap.wrap(text, width, max_lines=count, placeholder="\u2026") if text else []


def _place_closed(model, xy, left):
    """Put the closed prerequisites in a column at `left`, each near the mean height of what it frees (the one
    ordering that keeps its edges short); -> the height the column needs."""
    nodes = model["nodes"]
    mid = lambda n: xy[n][1] + xy[n][2] / 2         # noqa: E731
    shut = sorted((n for n, d in nodes.items() if d["m"] == "closed"),
                  key=lambda n: (sum(mid(f) for f in nodes[n]["frees"]) / max(1, len(nodes[n]["frees"])), n))
    y = MARGIN
    for n in shut:
        want = sum(mid(f) for f in nodes[n]["frees"]) / max(1, len(nodes[n]["frees"])) - NODE_H / 2
        y = max(y, want)
        xy[n] = (left, y, NODE_H)
        y += NODE_H + GAP_Y
    return y + MARGIN


def _node_svg(d, goal, xy, closed_blockers=0):
    x, y, _h = xy
    cls = CLASS[d["m"]]
    shown = _marker_text(d["m"], d["after"])
    lines = _lines(d["t"] or "", 31, 2) or [" ".join(p for p in (d["life"], d["prio"], d["unit"]) if p)[:36]]
    out = ['<g class="n c-%s%s%s" data-n="%d" tabindex="0" role="button" aria-label="%s">'
           % (cls, " goal" if d["n"] == goal else "", " cl" if d["m"] == "closed" else "", d["n"], _esc("#%d, %s" % (d["n"], shown))),
           "<title>%s</title>" % _esc("#%d %s (%s)" % (d["n"], d["t"] or "", shown)),
           '<rect x="%.1f" y="%.1f" width="%d" height="%d"/>' % (x, y, NODE_W, NODE_H),
           '<text class="num" x="%.1f" y="%.1f">#%d</text>' % (x + 10, y + 18, d["n"]),
           '<text class="mk" x="%.1f" y="%.1f">%s</text>' % (x + 14 + 9.5 * (len(str(d["n"])) + 1), y + 18, _esc(shown))]
    if closed_blockers:
        out.append('<text class="mk" x="%.1f" y="%.1f" text-anchor="end">\u2713 %d closed</text>'
                   % (x + NODE_W - 9, y + NODE_H - 7, closed_blockers))
    if d["n"] == goal:
        out.append('<text class="tag" x="%.1f" y="%.1f" text-anchor="end">GOAL</text>' % (x + NODE_W - 9, y + 18))
    for i, ln in enumerate(lines):
        out.append('<text x="%.1f" y="%.1f">%s</text>' % (x + 10, y + 34 + 14 * i, _esc(ln)))
    out.append("</g>")
    return "".join(out)


def _header(model, repo):
    fc_notes = "".join('<div class="fc">%s</div>' % _esc(n) for n in model["notes"])
    stops = model["stops"]
    if stops:
        shown = "; ".join("#%d %s (%s)" % (s["n"], s["m"], s["why"]) for s in stops[:6])
        held = "Held by: %s%s." % (shown, " and %d more" % (len(stops) - 6) if len(stops) > 6 else "")
    else:
        held = "Nothing holds it: every member can be worked, in order."
    notices = []
    if model["over_cap"]:
        notices.append("This chain has %d open members, over the page cap of %d. It is written in full and may be "
                       "hard to read; plan a nearer goal with --goal." % (model["open"], MEMBER_CAP))
    if model["cycles"]:
        notices.append("Dependency cycle among %s: the plan refuses this chain until one blocker line is dropped. "
                       "The cycle members are drawn in the first column." %
                       ", ".join("#%d" % n for c in model["cycles"] for n in c))
    ok, why = model["fresh"]
    if not ok:
        notices.append("The map is not fresh (%s); this page shows it as stored, age %s. Refresh it with dag.py "
                       "refresh --force." % (why, _age(model["age"]) if model["age"] is not None else "unknown"))
    if not model["titled"]:
        notices.append("No titles were given (the map holds none), so members are named by number. Pass "
                       "--titles FILE to add them.")
    if model.get("simple"):
        notices.append("Layout simplified: this chain is too large for channel routing, so long edges are drawn "
                       "straight and may cross nodes.")
    counts = collections.Counter(d["m"] for d in model["nodes"].values())
    legend = "".join('<span><i class="sw c-%s"></i>%s (%d): %s</span>' % (CLASS[k], _esc(k), counts[k], _esc(v))
                     for k, v in LEGEND if counts[k] or k in ("ready now", "after", "needs decision"))
    legend += "".join('<span><i class="ln %s"></i>%s: %s</span>' % (k, k, v) for k, v in EDGES)
    goal = model["nodes"][model["goal"]]
    facts = ("<span><b>%d</b> open members</span><span>depth <b>%d</b></span><span><b>%d</b> closed prerequisites"
             "</span><span><b>%d</b> need a decision</span><span>map built <b>%s</b>, age <b>%s</b></span>"
             % (model["open"], model["depth"], model["closed"], model["decisions"],
                _esc(_stamp(model["built_at"])), _age(model["age"]) if model["age"] is not None else "unknown"))
    return ('<header class="top"><h1>Chain for #%d%s <small>%s</small></h1><div class="facts">%s</div>'
            '<div class="held">%s</div><div class="fc">%s</div>%s%s<div class="legend">%s</div></header>'
            % (model["goal"], " " + _esc(goal["t"]) if goal.get("t") else "", _esc(repo or ""), facts,
               _esc(held), _esc(model["forecast"]) + ". Assumes every goal it claims merges; the cap is not modelled.",
               fc_notes, "".join('<div class="notice">%s</div>' % _esc(n) for n in notices), legend))


def _stamp(epoch):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(epoch)) if epoch else "unknown"


def render_page(model, repo=None):
    """The whole page of one chain, as text. Pure: the same model gives the same bytes."""
    nodes = model["nodes"]
    shown = {n: d for n, d in nodes.items() if d["m"] != "closed"}
    gutter = any(d["m"] in ("outside", "cycle") for d in shown.values())
    cols = {n: (0 if d["m"] in ("outside", "cycle") else d["depth"] + (1 if gutter else 0)) for n, d in shown.items()}
    rank = {n: chain._rank(d["prio"]) if d["prio"] else chain._UNPRIORITISED for n, d in shown.items()}
    lay = layout(cols, [(a, b) for a, b, _t in model["edges"] if a in shown and b in shown], rank)
    model["simple"] = any(cols[a] < cols[b] for a, b in lay["direct"])
    model["crossings"] = lay["crossings"]
    xy, width, height = dict(lay["xy"]), lay["width"], lay["height"]
    left = -(NODE_W + COL_GAP)          # closed prerequisites: a hidden column left of everything
    closed_h = _place_closed(model, xy, left)
    edges = "".join('<path class="e %s%s" data-a="%d" data-b="%d" d="%s" marker-end="url(#arr)"/>'
                    % (t, " ce" if nodes[a]["m"] == "closed" else "", a, b, _edge_d(dict(lay, xy=xy), a, b))
                    for a, b, t in model["edges"])
    ordered = sorted(nodes.values(), key=lambda d: (xy[d["n"]][0], xy[d["n"]][1], d["n"]))
    body = "".join(_node_svg(d, model["goal"], xy[d["n"]], sum(1 for w in d["waits"] if nodes[w]["m"] == "closed"))
                   for d in ordered)
    with_closed = "%d 0 %d %d" % (left, width - left, max(height, closed_h))
    svg = ('<svg id="g" viewBox="0 0 %d %d" data-closed="%s" role="img" aria-label="Chain for #%d: blockers on the '
           'left, the work that waits on them on the right"><defs><marker id="arr" viewBox="0 0 10 10" refX="9" '
           'refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z"/></marker></defs>'
           '<g>%s</g><g>%s</g></svg>' % (width, height, with_closed, model["goal"], edges, body))
    css = CSS % {"light": _vars("light"), "dark": _vars("dark")}
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Chain for #%d</title>'
            '<style>%s</style></head><body>%s<main><div id="stage"><div id="tools"><button id="zin" title="Zoom in">+'
            '</button><button id="zout" title="Zoom out">\u2212</button><button id="zfit" title="Show the whole '
            'chain">Fit</button><button id="zth" title="Switch light and dark">Theme</button>%s</div>%s</div>'
            '<aside><h2 id="p-head">Select a member</h2><p id="p-hint">Click a member to see what it waits on, '
            'what it frees and why it is held. Drag to pan, scroll or + and \u2212 to zoom, Fit shows the whole '
            'chain.</p><dl id="p-body"></dl></aside></main>'
            '<script type="application/json" id="data">%s</script><script>%s</script></body></html>\n'
            % (model["goal"], css, _header(model, repo),
               '<label><input type="checkbox" id="zcl"> show %d closed</label>' % model["closed"] if model["closed"] else "",
               svg, _data_json(model), JS))


def render_index(rows, repo, stamp_built, age, fresh, titled):
    """The index page: one row per chain, linking its page."""
    notice = ""
    if not fresh[0]:
        notice += '<div class="notice">The map is not fresh (%s); the pages show it as stored.</div>' % _esc(fresh[1])
    if not titled:
        notice += '<div class="notice">No titles were given (the map holds none). Pass --titles FILE to add them.</div>'
    trs = []
    for r in rows:
        held = "; ".join("#%d %s" % (s["n"], s["m"]) for s in r["stops"][:4]) or "nothing"
        trs.append('<tr><td><a href="%s">#%d</a>%s</td><td class="num">%d%s</td><td class="num">%d</td><td>%s</td>'
                   '<td class="num">%d</td><td>%s</td></tr>'
                   % (_esc(r["file"]), r["goal"], " " + _esc(r["title"]) if r["title"] else "", r["open"],
                      " (over the cap)" if r["over_cap"] else "", r["depth"], _esc(held), r["decisions"],
                      _esc("%d of %d" % (r["finish"], r["open"]))))
    css = CSS % {"light": _vars("light"), "dark": _vars("dark")}
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>Chains</title><style>%s</style></head><body>'
            '<header class="top"><h1>Chains <small>%s</small></h1><div class="facts"><span><b>%d</b> chains</span>'
            '<span>map built <b>%s</b>, age <b>%s</b></span></div>%s</header><table><thead><tr><th>End goal</th>'
            '<th>Open members</th><th>Depth</th><th>Held by</th><th>Need a decision</th><th>A run can finish</th>'
            '</tr></thead><tbody>%s</tbody></table></body></html>\n'
            % (css, _esc(repo or ""), len(rows), _esc(stamp_built), age, notice,
               "".join(trs) or '<tr><td colspan="6">No open end goal waits on anything open.</td></tr>'))


# ------------------------------------------------------------------ the verb

def end_goals(m):
    """Every end goal of the map: an open issue no open goal waits on, with an open prerequisite (what
    `dag.py chains` lists as `ends`), sorted."""
    g = dag_chain._graph(m)
    ends = {n for part in dag_chain._ends_and_sets(g) for n in part["ends"] if g.nodes[n].kind == "issue"}
    return sorted(ends)


def run(sdlc, goal=None, all_=False, out_dir=None, titles_path=None, now=None):
    """`(lines, exit_code)`: write the page of `goal`, or every chain's page and the index. Writes only under
    `out_dir` (default `<sdlc>/state/dag-html`); no GitHub call."""
    now = time.time() if now is None else now
    if (goal is None) == (not all_):
        return ["dag html: give exactly one of --goal N or --all"], EXIT["refused"]
    try:
        m, _md, config, ok, why = _open(sdlc, now)
        titles = read_titles(titles_path) if titles_path else {}
        out = pathlib.Path(out_dir) if out_dir else pathlib.Path(sdlc) / "state" / "dag-html"
        check_out(out)
        goals = [goal] if not all_ else end_goals(m)
        models = [build(m, config, n, titles, now, (ok, why)) for n in goals]
    except HtmlError as exc:
        return [str(exc)], exc.code
    repo = m["header"].get("repo")
    os.makedirs(out, exist_ok=True)
    lines, rows = [], []
    for model in models:
        name = "chain-%d.html" % model["goal"]
        dag_store.state.atomic_write_text(out / name, render_page(model, repo))
        lines.append("wrote %s (#%d, %d open members, depth %d)" % (out / name, model["goal"], model["open"],
                                                                   model["depth"]))
        if model["over_cap"]:
            lines.append("  note: #%d has %d open members, over the page cap of %d" % (model["goal"], model["open"],
                                                                                         MEMBER_CAP))
        rows.append({"file": name, "goal": model["goal"], "title": model["nodes"][model["goal"]]["t"],
                     "open": model["open"], "depth": model["depth"], "stops": model["stops"],
                     "decisions": model["decisions"], "finish": model["finish"], "over_cap": model["over_cap"]})
    if all_:
        rows.sort(key=lambda r: (-r["open"], r["goal"]))
        t = dag_store.stamp(m)
        dag_store.state.atomic_write_text(
            out / "index.html", render_index(rows, repo, _stamp(dag_store.parse_ts(m["header"].get("built_at"))),
                                             _age(now - t) if t is not None else "unknown", (ok, why), bool(titles)))
        lines.append("wrote %s (%d chains)" % (out / "index.html", len(rows)))
    return lines, EXIT["ok"]

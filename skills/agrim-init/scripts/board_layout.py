#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Apply and check the canonical Sigma board (#234): the fields and six views `board_spec.py`
describes, on a board `board_setup.py create` made or one you name.

    board_layout.py fields <sdlc_dir> [--owner O] [--number N] [--yes]
    board_layout.py views  <sdlc_dir> [--owner O] [--number N] [--yes]
    board_layout.py verify <sdlc_dir> [--owner O] [--number N]
    board_layout.py spec   <sdlc_dir>

fields  Priority (`P0`..`P4`), Phase (`P1 GOAL`..`P7 RETRO`) as single-selects, Area and Model tier
        as text. A field that exists is kept as it is: matched by the loop's one name rule
        (`sources._match_field`: exact, else one case-only variant), never renamed, never
        duplicated. Wrong type, or several case-variants -> REFUSED. A missing option -> [manual]:
        an option write replaces the field's whole list, so it is never sent from here.
views   The six views, matched by name the same way. Absent -> `createProjectV2View` (name, layout,
        visible fields), then `updateProjectV2View` for the filter (the create input has none).
        Present -> only the properties that differ are updated; columns the user added are kept
        after ours. A view the spec does not name is never read for changes, updated or deleted.
        Several views answering to one name -> that view is REFUSED and left alone.
        Group-by, sort, the board's column field, roadmap markers and the default (leftmost) view
        have NO API (schema introspection, `.sdlc/evidence/234/`): each is printed as an exact UI
        step, and only while the board reads different.
verify  Read-only (one REST read + one GraphQL read; a read-only token is enough). Reports every
        spec field and view property that is missing or differs, with the fix. Exit 0 all match,
        1 something differs.
spec    Prints the spec as JSON. No gh call.

Without `--yes`, `fields` and `views` only READ and print what they would do. They act on the
pinned board only when `project.setup_created` says board_setup.py created it; any other board
needs `--number N`, the operator's explicit choice. Exit: 0 done / dry run, 1 a [FAIL] (re-run is
safe: every step is idempotent), 2 REFUSED (a human must change the named thing first).

COST (counted against tests/boardfake.py, and read-only against a real 246-card org board on
2026-09-29: 3 calls, 1.6-1.8s per verb, `.sdlc/evidence/234/`): every verb reads the owner (REST),
the board's node id (REST) and one GraphQL query (fields, views, workflows; the first 50 of each --
50 is GitHub's field cap; a board with more than 50 views is read as its first 50). `fields --yes` adds 1 mutation per missing field (4 on a fresh board). `views --yes`
adds 1 create + 1 filter update per missing view (11 on a fresh board: `Board · by Status` has no
filter), 1 update per drifted view, and 1 re-read. A finished board costs the 2 reads and nothing
else, so re-running at 10x or 100x cards costs the same: nothing here reads cards.
"""
import importlib.util
import json
import os
import pathlib
import shlex
import sys

_HERE = pathlib.Path(__file__).resolve().parent
HERE = str(pathlib.Path(__file__).resolve())


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bs = _load(_HERE / "board_setup.py", "board_layout_setup")       # gh plumbing, config, pins
board_spec = _load(_HERE / "board_spec.py", "board_layout_spec")
pf = bs.pf

_VIEW_READ = (
    "query { node(id: %s) { ... on ProjectV2 { id number title "
    "fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name dataType } "
    "... on ProjectV2SingleSelectField { options { id name } } } } "
    "views(first: 50, orderBy: {field: POSITION, direction: ASC}) { nodes { id number name layout "
    "filter fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name } } } "
    "groupByFields(first: 5) { nodes { ... on ProjectV2FieldCommon { id name } } } "
    "verticalGroupByFields(first: 5) { nodes { ... on ProjectV2FieldCommon { id name } } } "
    "sortByFields(first: 5) { nodes { direction field { ... on ProjectV2FieldCommon { id name } } } } "
    "} } workflows(first: 30) { nodes { name enabled } } } } }")


class Refused(Exception):
    pass


def _names(conn):
    return [n.get("name") for n in (conn or {}).get("nodes") or [] if n.get("name")]


def _norm_filter(text):
    return " ".join(str(text or "").split())


def _quote(value):
    return ('"%s"' % value) if os.name == "nt" else shlex.quote(str(value))


def command(verb, sdlc, number=None, yes=False):
    """The exact gesture, printed (never run) by the dry run and by `/agrim-init --board yes`."""
    line = f"{pf._vd.python_command()} {_quote(HERE)} {verb} {_quote(os.path.abspath(str(sdlc)))}"
    if number is not None:
        line += f" --number {int(number)}"
    return line + (" --yes" if yes else "")


class Layout:
    """One run against one board. Reads once; `b` is board_setup's `Board` (gh plumbing + step)."""

    def __init__(self, b, spec, number, owner, kind, sdlc, explicit):
        self.b, self.spec, self.number, self.owner, self.kind = b, spec, number, owner, kind
        self.sdlc, self.explicit = sdlc, explicit
        self.match = bs._sources().GitHubSource._match_field
        self.node = None

    # -- reads
    def read(self):
        pid = self.b.rest(f"{self.kind}/{self.owner}/projectsV2/{self.number}")["node_id"]
        node = self.b.graphql(_VIEW_READ % bs._q(pid)).get("node")
        if not node:
            raise bs.Failed(f"board #{self.number} could not be read")
        self.node = node
        return node

    def fields(self):
        return {f["name"]: f for f in (self.node.get("fields") or {}).get("nodes") or []
                if f.get("name")}

    def views(self):
        return [v for v in (self.node.get("views") or {}).get("nodes") or []]

    def find_field(self, name):
        """(the board's field or None, ambiguous?) by `_match_field`."""
        fields = self.fields()
        chosen, clash = self.match(list(fields), name)
        if chosen is None:
            return None, bool(clash)
        return fields[chosen], False

    def find_view(self, name):
        """(the board's view or None, [every view answering to `name`] when more than one)."""
        views = self.views()
        hits = [v for v in views if str(v.get("name")).casefold() == name.casefold()]
        if len(hits) > 1:
            return None, hits
        return (hits[0] if hits else None), []

    # -- step
    def say(self, tag, what, detail=""):
        self.b.step(tag, what, detail)

    def manual(self, what, detail):
        self.b.out(f"  [manual] {what}: {detail}")


# ------------------------------------------------------------------ context

def _open(sdlc_dir, owner, number, runner, out, need_ours):
    """-> (Layout, spec). Raises Refused with the message already printed."""
    try:
        _path, cfg = bs._config(sdlc_dir)
    except (ValueError, OSError) as exc:
        out(f"board_layout: REFUSED -- {pf.printable(exc)}")
        raise Refused() from None
    sdlc = pathlib.Path(os.path.abspath(str(sdlc_dir)))
    gh_cfg, proj = bs._gh_block(json.loads(json.dumps(cfg)))
    repo = str(gh_cfg.get("repo") or "").strip()
    host = "github.com"
    if not repo:
        repo, host = bs._repo_from_origin(sdlc.parent)
        host = host or "github.com"
    if not repo or "/" not in repo:
        out("board_layout: REFUSED -- no GitHub repository: set discovery.github.repo first.")
        raise Refused()
    src = bs._sources().GitHubSource(
        {"discovery": {"source": "github", "github": dict(gh_cfg, repo=repo)}}, run=lambda _a: "")
    spec = board_spec.build(src, bs._sources().discovery.PRIORITIES)
    owner = owner or src._proj_owner()
    explicit = number is not None
    pinned = number if explicit else proj.get("number")
    try:
        pinned = int(pinned) if pinned not in (None, "") else None
    except (TypeError, ValueError):
        out(f"board_layout: REFUSED -- project.number {pf.printable(pinned)} is not a number.")
        raise Refused() from None
    if pinned is None:
        out("board_layout: REFUSED -- no board is pinned. Create and pin one first "
            "(`board_setup.py create <sdlc> --yes`), or name one with --number N.")
        raise Refused()
    b = bs.Board(sdlc, runner, out, host)
    if owner.startswith("@"):
        try:
            owner = b.gh("api", "user", "--jq", ".login").strip()
        except bs.Failed as exc:
            out(f"board_layout: REFUSED -- could not resolve {owner}: {exc.detail}")
            raise Refused() from None
    if need_ours and not explicit and not bs._ours(proj.get(bs.CREATED_KEY), pinned, owner):
        out(f"board_layout: REFUSED -- board #{pinned} is pinned, but board_setup.py did not create "
            "it, so its layout is not assumed to be Sigma's to change. If it is, say so: re-run "
            f"with --number {pinned}.")
        raise Refused()
    try:
        who = b.rest(f"users/{owner}")
    except (bs.Failed, ValueError) as exc:
        out(f"board_layout: REFUSED -- could not read owner '{pf.printable(owner)}': "
            f"{getattr(exc, 'detail', exc)}")
        raise Refused() from None
    kind = "orgs" if who.get("type") == "Organization" else "users"
    return Layout(b, spec, pinned, owner, kind, sdlc, explicit), spec


# ------------------------------------------------------------------ fields

def _type_ok(field, want):
    got = str(field.get("dataType") or "").upper()
    return got == want


def _field_plan(lay):
    """[(spec field, board field or None, action, detail)] where action is ok / create / refuse /
    manual."""
    plan = []
    for f in lay.spec["fields"]:
        have, ambiguous = lay.find_field(f["name"])
        if ambiguous:
            plan.append((f, None, "refuse", "several fields differ from it only in case; rename "
                         "all but one on the board (nothing is guessed)"))
            continue
        if have is None:
            if f["owner"] == "create":
                plan.append((f, None, "manual", "absent: run "
                             + _create_cmd(lay) + " (it adds the Status lanes)"))
            else:
                plan.append((f, None, "create", ""))
            continue
        if not _type_ok(have, f["type"]):
            plan.append((f, have, "refuse", f"'{pf.printable(have['name'])}' is a "
                         f"{pf.printable(have.get('dataType'))} field, not {f['type']}; rename or "
                         "delete it on the board"))
            continue
        if f["options"]:
            names = [o.get("name") for o in have.get("options") or []]
            folded = {str(n).casefold() for n in names}
            missing = [o for o, _c in f["options"] if o not in names and o.casefold() not in folded]
            if missing:
                fix = (_create_cmd(lay) + " appends them, keeping every option id"
                       if f.get("create_appends") else "add them on the board by hand (Settings -> the field -> Add option); "
                       "an option write replaces the whole list, so it is never sent from here")
                plan.append((f, have, "manual", "missing option(s) " + " / ".join(missing)
                             + ": " + fix))
                continue
        plan.append((f, have, "ok", ""))
    return plan


def _create_cmd(lay):
    return (f"{pf._vd.python_command()} {_quote(bs.HERE)} create "
            f"{_quote(str(lay.sdlc))} --number {lay.number} --yes")


def _create_field(lay, pid, f):
    if f["type"] == "TEXT":
        lay.b.graphql("mutation { createProjectV2Field(input: {projectId: %s, dataType: TEXT, "
                      "name: %s}) { projectV2Field { ... on ProjectV2Field { id } } } }"
                      % (bs._q(pid), bs._q(f["name"])))
        return
    doc = bs._sources().GitHubSource._create_field_mutation(pid, f["name"], f["options"])
    lay.b.gh("api", "graphql", "-f", doc)


def run_fields(lay, yes):
    plan = _field_plan(lay)
    pid = lay.node["id"]
    for f, have, action, detail in plan:
        label = f"{f['name']}"
        shown = (f" (the board's '{pf.printable(have['name'])}')"
                 if have and have["name"] != f["name"] else "")
        if action == "ok":
            lay.say("ok", label, "present" + shown)
        elif action == "refuse":
            lay.say("REFUSED", label, detail)
        elif action == "manual":
            lay.manual(label, detail)
        elif not yes:
            lay.b.out(f"  [plan] {label}: would create ({f['type']}"
                      + (": " + " / ".join(o for o, _c in f["options"]) if f["options"] else "")
                      + ")")
        else:
            try:
                _create_field(lay, pid, f)
                lay.say("ok", label, "created")
            except bs.Failed as exc:
                lay.say("FAIL", label, exc.detail)
    return plan


# ------------------------------------------------------------------ views

_UI = "open the board, click the arrow beside the view's tab name"


def _manual_diffs(lay, v, have, first):
    """[(property, step)] for the properties the API cannot set, where the board differs."""
    out = []
    fields = lambda conn: _names(conn)                                        # noqa: E731
    if v.get("column_by") and fields(have.get("verticalGroupByFields")) != [v["column_by"]]:
        out.append(("column by", f"{_UI} -> Column by -> {v['column_by']} -> Save changes"))
    if v.get("group_by") and fields(have.get("groupByFields")) != [v["group_by"]]:
        out.append(("group by", f"{_UI} -> Group by -> {v['group_by']} -> Save changes"))
    if v.get("sort"):
        got = [((n.get("field") or {}).get("name"), n.get("direction"))
               for n in (have.get("sortByFields") or {}).get("nodes") or []]
        if got != [tuple(s) for s in v["sort"]]:
            out.append(("sort", f"{_UI} -> Sort by -> " + ", then ".join(
                f"{n} ({'ascending' if d == 'ASC' else 'descending'})" for n, d in v["sort"])
                + " -> Save changes"))
    if v.get("default") and not first:
        out.append(("default", f"drag the '{v['name']}' tab to the far left: the leftmost view is "
                    "the one the board opens on -> Save changes"))
    return out


def _settable_diffs(lay, v, have, ids):
    """{property: new value} for name-matched view `have` against spec view `v`."""
    diff = {}
    if have.get("layout") != v["layout"]:
        diff["layout"] = v["layout"]
    if _norm_filter(have.get("filter")) != _norm_filter(v["filter"]):
        diff["filter"] = v["filter"]
    shown = [n.get("id") for n in (have.get("fields") or {}).get("nodes") or [] if n.get("id")]
    if not set(ids) <= set(shown):
        diff["fields"] = ids + [i for i in shown if i not in ids]
    return diff


def _view_update(lay, view_id, diff):
    parts = ["viewId: %s" % bs._q(view_id)]
    if "layout" in diff:
        parts.append("layout: %s" % diff["layout"])
    if "filter" in diff:
        parts.append("filter: %s" % bs._q(diff["filter"]))
    if "fields" in diff:
        parts.append("configuration: {visibleFieldIds: [%s]}"
                     % ", ".join(bs._q(i) for i in diff["fields"]))
    lay.b.graphql("mutation { updateProjectV2View(input: {%s}) { projectV2View { id name } } }"
                  % ", ".join(parts))


def _field_ids(lay, v):
    """(ids in spec order, [missing field names])."""
    ids, missing = [], []
    for name in v["fields"]:
        have, _amb = lay.find_field(name)
        if have is None:
            missing.append(name)
        else:
            ids.append(have["id"])
    return ids, missing


def run_views(lay, yes):
    needed = sorted({n for v in lay.spec["views"] for n in v["fields"]
                     + [x for x in (v.get("group_by"), v.get("column_by")) if x]
                     + [s[0] for s in v.get("sort") or []]})
    absent = [n for n in needed if lay.find_field(n)[0] is None]
    if absent and not yes:
        lay.b.out("  [plan] views: the board lacks field(s) " + ", ".join(absent) + "; run `fields "
                  "--yes` first, then this. With them, it would:")
    elif absent:
        lay.say("FAIL", "views", "the board lacks field(s) " + ", ".join(absent)
                + "; nothing was changed. Add them first: "
                + command("fields", lay.sdlc, lay.number if lay.explicit else None, yes=True))
        return
    pid = lay.node["id"]
    changed = False
    for v in lay.spec["views"]:
        label = f"view '{v['name']}'"
        have, clash = lay.find_view(v["name"])
        if clash:
            lay.say("REFUSED", label, f"{len(clash)} views answer to this name ("
                    + ", ".join(f"#{c.get('number')} '{pf.printable(c.get('name'))}'" for c in clash)
                    + "); rename or delete all but one on the board. None of them was changed")
            continue
        ids, _missing = _field_ids(lay, v)
        if have is None:
            if not yes:
                lay.b.out(f"  [plan] {label}: would create ({v['layout']}, filter "
                          f"'{v['filter']}', columns " + ", ".join(v["fields"]) + ")")
                continue
            try:
                made = lay.b.graphql(
                    "mutation { createProjectV2View(input: {projectId: %s, name: %s, layout: %s, "
                    "configuration: {visibleFieldIds: [%s]}}) { projectV2View { id name } } }"
                    % (bs._q(pid), bs._q(v["name"]), v["layout"],
                       ", ".join(bs._q(i) for i in ids)))["createProjectV2View"]["projectV2View"]
                changed = True
                if v["filter"]:
                    _view_update(lay, made["id"], {"filter": v["filter"]})
                lay.say("ok", label, "created")
            except (bs.Failed, KeyError, TypeError) as exc:
                lay.say("FAIL", label, getattr(exc, "detail", repr(exc)))
            continue
        diff = _settable_diffs(lay, v, have, ids)
        if not diff:
            lay.say("ok", label, "present")
            continue
        what = ", ".join(sorted(diff))
        if not yes:
            lay.b.out(f"  [plan] {label}: would update {what}")
            continue
        try:
            _view_update(lay, have["id"], diff)
            changed = True
            lay.say("ok", label, f"updated {what} (columns you added are kept, after ours)")
        except bs.Failed as exc:
            lay.say("FAIL", label, exc.detail)
    if changed:
        try:
            lay.read()                                  # measured, not assumed: read back
        except (bs.Failed, ValueError, KeyError) as exc:
            lay.say("FAIL", "read back", getattr(exc, "detail", repr(exc)))
            return
        for v in lay.spec["views"]:
            have, clash = lay.find_view(v["name"])
            if clash:
                continue
            if have is None:
                lay.say("FAIL", f"view '{v['name']}'", "not on the board after the write")
            elif _settable_diffs(lay, v, have, _field_ids(lay, v)[0]):
                lay.say("FAIL", f"view '{v['name']}'", "still differs after the write: "
                        + ", ".join(sorted(_settable_diffs(lay, v, have, _field_ids(lay, v)[0]))))
    _say_manual(lay)


def _say_manual(lay):
    views = lay.views()
    for v in lay.spec["views"]:
        have, clash = lay.find_view(v["name"])
        if have is None or clash:
            continue
        for prop, step in _manual_diffs(lay, v, have, views and views[0] is have):
            if prop == "default":
                lay.manual("default view", step)
            else:
                lay.manual(f"view '{v['name']}': {prop}", step)
        if v.get("markers"):
            lay.b.out(f"  [check] view '{v['name']}': markers -> {v['markers']} (not readable "
                      f"over the API; {_UI} -> Markers -> {v['markers']})")


# ------------------------------------------------------------------ verify

def run_verify(lay):
    diffs, checks = [], 0

    def bad(what, detail):
        diffs.append(what)
        lay.b.out(f"  [differs] {what}: {detail}")

    for f, have, action, detail in _field_plan(lay):
        checks += 1
        what = f"field '{f['name']}'"
        if action == "ok":
            lay.say("ok", what)
        elif action == "create":
            bad(what, "missing -> " + command("fields", lay.sdlc, lay.number, yes=True))
        else:
            bad(what, detail)
    views = lay.views()
    for v in lay.spec["views"]:
        what = f"view '{v['name']}'"
        have, clash = lay.find_view(v["name"])
        checks += 1
        if clash:
            bad(what, f"{len(clash)} views answer to this name; rename all but one")
            continue
        if have is None:
            bad(what, "missing -> " + command("views", lay.sdlc, lay.number, yes=True))
            continue
        ids, missing = _field_ids(lay, v)
        diff = _settable_diffs(lay, v, have, ids)
        if missing:
            diff.setdefault("fields", None)
        for prop in sorted(diff):
            if prop == "filter":
                got = have.get("filter") or ""
                bad(f"{what}: filter", f"'{pf.printable(got)}', want '{v['filter']}'")
            elif prop == "layout":
                bad(f"{what}: layout", f"{have.get('layout')}, want {v['layout']}")
            else:
                shown = set(_names(have.get("fields")))
                bad(f"{what}: columns", "missing " + ", ".join(
                    n for n in v["fields"] if n not in shown))
        for prop, step in _manual_diffs(lay, v, have, views and views[0] is have):
            bad("default view" if prop == "default" else f"{what}: {prop}", step)
        if not diff and not _manual_diffs(lay, v, have, views and views[0] is have):
            lay.say("ok", what)
    flows = {w.get("name"): w.get("enabled")
             for w in (lay.node.get("workflows") or {}).get("nodes") or []}
    checks += 1
    if flows.get(bs.ITEM_CLOSED) is True:
        lay.say("ok", f"workflow '{bs.ITEM_CLOSED}'", "on")
    else:
        bad(f"workflow '{bs.ITEM_CLOSED}'", "off or unreadable (no API turns it on): open "
            + bs.workflows_url(lay.kind, lay.owner, lay.number, lay.b.host)
            + f" -> '{bs.ITEM_CLOSED}' -> Edit -> Status: Done -> Save and turn on workflow")
    lay.b.out(f"board_layout verify: board #{lay.number} -- {checks - len(diffs)} of {checks} "
              "checks match" + ("" if not diffs else "; fix the [differs] lines above"))
    return 1 if diffs else 0


# ------------------------------------------------------------------ main

USAGE = ("usage: board_layout.py fields|views|verify <sdlc_dir> [--owner O] [--number N] [--yes]\n"
         "       board_layout.py spec <sdlc_dir>")


def main(argv, runner=None, out=print):
    args = list(argv[1:])
    if not args or args[0] in ("-h", "--help"):
        out(USAGE)
        return 0 if args else 2
    verb = args[0]
    if verb not in ("fields", "views", "verify", "spec") or len(args) < 2:
        out(USAGE)
        return 2
    sdlc, rest = args[1], args[2:]
    opts, yes, i = {"--owner": None, "--number": None}, False, 0
    while i < len(rest):
        if rest[i] == "--yes" and verb in ("fields", "views"):
            yes, i = True, i + 1
        elif rest[i] in opts and i + 1 < len(rest) and verb != "spec":
            opts[rest[i]], i = rest[i + 1], i + 2
        else:
            out(USAGE)
            return 2
    try:
        number = int(opts["--number"]) if opts["--number"] is not None else None
    except ValueError:
        out(USAGE)
        return 2
    if verb == "spec":
        try:
            _path, cfg = bs._config(sdlc)
        except (ValueError, OSError) as exc:
            out(f"board_layout: REFUSED -- {pf.printable(exc)}")
            return 2
        gh_cfg, _proj = bs._gh_block(cfg)
        src = bs._sources().GitHubSource(
            {"discovery": {"source": "github", "github": dict(gh_cfg, repo=gh_cfg.get("repo")
                                                              or "owner/repo")}},
            run=lambda _a: "")
        out(json.dumps(board_spec.as_json(board_spec.build(
            src, bs._sources().discovery.PRIORITIES)), indent=2, ensure_ascii=False))
        return 0
    runner = runner or bs.real_runner
    try:
        lay, _spec = _open(sdlc, opts["--owner"], number, runner, out,
                           need_ours=verb != "verify")
    except Refused:
        return 2
    try:
        lay.read()
    except (bs.Failed, ValueError, KeyError, TypeError) as exc:
        out(f"board_layout: REFUSED -- could not read board #{lay.number} of "
            f"{pf.printable(lay.owner)}: {getattr(exc, 'detail', exc)}")
        return 2
    if verb == "verify":
        out(f"board_layout verify: {pf.printable(lay.owner)} board #{lay.number} "
            f"'{pf.printable(lay.node.get('title'))}' (read-only)")
        return run_verify(lay)
    head = "" if yes else " dry run (nothing written)"
    out(f"board_layout {verb}:{head} {pf.printable(lay.owner)} board #{lay.number} "
        f"'{pf.printable(lay.node.get('title'))}'")
    (run_fields if verb == "fields" else run_views)(lay, yes)
    if not yes:
        out("  To do it: " + command(verb, lay.sdlc, lay.number if lay.explicit else None, True))
    if lay.b.refused:
        return 2
    if lay.b.failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

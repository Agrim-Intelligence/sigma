"""An in-memory GitHub for #235's board tests: ONE state, answered over the three transports that
touch a Projects v2 board -- REST reads (`gh api orgs/<o>/projectsV2...`), GraphQL mutations and
reads (`gh api graphql -f query=...`), and the loop's own `gh project <verb>` CLI calls -- so a board
`board_setup.py` creates is the very board `sources.GitHubSource` later moves a card on.

It models GITHUB'S BEHAVIOUR that the tests depend on, not the transport:
  - a fresh board's built-in Status field carries GitHub's defaults `Todo / In progress / Done`
    (lowercase p, see sources._options_mutation's #1492 note) and six ENABLED workflows;
  - an option rewrite that omits an existing option's id DELETES that option, and a deleted option
    a workflow targeted disables that workflow (#720's measurement);
  - `gh project list --limit 100` returns only the first 100 boards (the cap `_find_project` reads);
  - REST option names AND descriptions come back as {"raw": ..., "html": ...}, and colours as an
    upper-case enum name (`"GRAY"`), measured live against a real board (review of PR #279);
  - REST `/fields` returns one page (`per_page`, default 30) unless `--paginate` is passed, and
    REST `/items` one page of the board's cards (board_setup reads `per_page=1`: any card at all?);
  - board numbers are per OWNER (acme's #1 and octo's #1 are different boards);
  - a second field with an existing name is rejected ("Name has already been taken");
  - `updateProjectV2Field` STORES each option's colour and description exactly as sent, and an
    option whose id is left out is DELETED: every card whose value was that option loses it
    (the card-value wipe board_migrate's rehearsal measures), and a workflow targeting it goes off;
  - GraphQL string literals are parsed as GraphQL parses them (JSON escapes), and an option list
    that does not parse is a GraphQL error, never a silently shorter list;
  - #233: ONE issue's cards are read over GraphQL (`repository(owner, name) { issue(number) {
    projectItems ... } }`), scoped by REPOSITORY as the real API is: a board may carry cards from
    several repos with the same issue number (`add_item(..., repo="acme/other")`), and field values
    come back keyed by field ID, never flattened by name. The same read carries `viewer { login }`
    (`self.viewer`; None models a viewer that cannot be read), and `gh project create` makes a fresh
    board with GitHub's default fields.

Imported as a plain sibling module (`import boardfake`), like gqlfake.
"""
import json
import re

import gqlfake

DEFAULT_WORKFLOWS = ("Item closed", "Pull request merged", "Item reopened", "Auto-close issue",
                     "Auto-add sub-issues to project", "Pull request linked to issue")

#: What `gh auth status` prints for a classic token (the token itself masked, as gh does).
FAKE_TOKEN = "ghp_SECRETSECRETSECRETSECRETSECRETSECRET"


#: GitHub's own defaults for a fresh board's Status options (colour + description), so a test can
#: tell "kept" from "reset to colour-by-position with an empty description".
GH_DEFAULTS = (("Todo", "GREEN", "This item hasn't been started"),
               ("In progress", "YELLOW", "This is actively being worked on"),
               ("Done", "PURPLE", "This has been completed"))

_STR = r'"(?:[^"\\\n]|\\.)*"'
_OPT = re.compile(r'\s*\{(?:id: (%s), )?name: (%s), color: ([A-Z]+), description: (%s)\}\s*(,)?'
                  % (_STR, _STR, _STR))


class GraphQLError(LookupError):
    """A GraphQL error gh reports on stderr with exit 1 (not a 404)."""


def parse_options(doc):
    """The `singleSelectOptions: [...]` list of a mutation, parsed strictly: each option's
    id/name/color/description decoded as GraphQL string literals. Anything that does not parse is
    a GraphQL error -- an unescaped quote in a name must fail loudly, as GitHub's parser does."""
    at = doc.find("singleSelectOptions: [")
    if at < 0:
        raise GraphQLError("singleSelectOptions missing")
    i, out = at + len("singleSelectOptions: ["), []
    while True:
        if doc[i:].lstrip().startswith("]"):
            return out
        m = _OPT.match(doc, i)
        if not m:
            raise GraphQLError("Parse error on %r" % doc[i:i + 40])
        oid, name, color, desc, _comma = m.groups()
        out.append({"id": json.loads(oid) if oid else None, "name": json.loads(name),
                    "color": color, "description": json.loads(desc)})
        i = m.end()


class GitHub:
    def __init__(self, owner="acme", owner_type="Organization", repo="widget", boards=(),
                 scopes=("repo", "workflow", "read:org", "project"), token_kind="ghp_"):
        self.owner, self.owner_type, self.repo = owner, owner_type, repo
        self.scopes = list(scopes)
        self.token_kind = token_kind
        self.boards = []            # dicts: number, id, title, fields[], workflows{}, repos[], items[]
        for b in boards:
            self.add_board(**b)
        self.fail = {}              # substring of a gh call -> stderr; the call exits 1
        self.ignore = set()         # substring of a gh call -> exit 0, "{}", and NOTHING applied
        self.calls = []             # every argv, as passed (without the leading "gh")
        self.issues = []            # REST issue dicts for the loop's backlog read
        self.viewer = "someone"     # the token's own login (`gh api user`, GraphQL `viewer`); None = unreadable
        self.labels = {}

    # ------------------------------------------------------------ state helpers
    def add_board(self, title, number=None, fields=None, workflows=None, owner=None):
        owner = owner or self.owner
        taken = [b["number"] for b in self.boards if b["owner"] == owner]
        number = number or (max(taken) + 1 if taken else 1)
        if number in taken:
            raise ValueError("board %s/#%d already exists" % (owner, number))
        tag = "%d" % number if owner == self.owner else "%s_%d" % (owner, number)
        ids = ("o_todo_", "o_inprog_", "o_done_")
        b = {"number": number, "id": "PVT_%s" % tag, "title": title,
             "owner": owner,
             "fields": fields if fields is not None else [
                 {"id": "PVTF_title_%s" % tag, "name": "Title", "options": None,
                  "data_type": "title"},
                 {"id": "PVTSSF_status_%s" % tag, "name": "Status", "options": [
                     {"id": pre + tag, "name": n, "color": c, "description": d}
                     for pre, (n, c, d) in zip(ids, GH_DEFAULTS)]}],
             "workflows": dict(workflows) if workflows is not None
             else {w: True for w in DEFAULT_WORKFLOWS},
             "repos": [], "items": []}
        # which Status option (by ID) each workflow sets: the ids survive a rename, not a delete
        ids = {o["name"]: o["id"] for o in (self.field(b, "Status") or {}).get("options") or []}
        b["wf_targets"] = {wf: ids.get(t) for wf, t in (("Item closed", "Done"),
                                                        ("Pull request merged", "Done"),
                                                        ("Item reopened", "In progress"))}
        self.boards.append(b)
        return b

    def board(self, number=None, node_id=None, title=None, owner=None):
        """Numbers are per owner: `number` alone means the default owner's board."""
        for b in self.boards:
            if (number is not None and b["number"] == int(number)
                    and b["owner"] == (owner or self.owner)) or \
                    (node_id is not None and b["id"] == node_id) or \
                    (title is not None and b["title"] == title):
                return b
        return None

    def option(self, board, field, name):
        return next((o for o in (self.field(board, field) or {}).get("options") or []
                     if o["name"] == name), None)

    def add_item(self, board, number, repo=None, **values):
        """A card for issue `number` (of `repo`, default this fake's own), with single-select values
        by field name -> option name."""
        full = repo or "%s/%s" % (self.owner, self.repo)
        tag = "%d" % number if full == "%s/%s" % (self.owner, self.repo) \
            else "%s_%d" % (full.replace("/", "_"), number)
        it = {"id": "PVTI_%s" % tag, "content": {"type": "Issue", "number": number,
                                                 "repository": full},
              "status": None, "values": {}}
        board["items"].append(it)
        for fname, oname in values.items():
            f = self.field(board, fname)
            o = self.option(board, fname, oname)
            it["values"][f["id"]] = o["id"]
            it[fname.lower()] = o["name"]
        return it

    def field(self, board, name):
        return next((f for f in board["fields"] if f["name"] == name), None)

    def option_names(self, board, name):
        f = self.field(board, name)
        return [o["name"] for o in (f or {}).get("options") or []]

    def mutations(self):
        return [c for c in self.calls if c[:2] == ["api", "graphql"]
                and any(str(a).startswith("query=mutation") for a in c)]

    # ------------------------------------------------------------ transports
    def gh(self, argv, *_rest):
        """board_setup's runner: argv starts with "gh" -> (rc, stdout, stderr)."""
        args = list(argv[1:]) if argv and argv[0] == "gh" else list(argv)
        self.calls.append(args)
        joined = " ".join(str(a) for a in args)
        for needle, err in self.fail.items():
            if needle in joined:
                return 1, "", err
        if any(needle in joined for needle in self.ignore):
            return 0, json.dumps({"data": {}}), ""
        try:
            return 0, self._answer(args), ""
        except GraphQLError as exc:
            return 1, "", "GraphQL: %s" % exc
        except LookupError as exc:
            return 1, "", "gh: Not Found (HTTP 404) %s" % exc

    def loop_run(self, args):
        """sources.GitHubSource's runner: args WITHOUT "gh" -> stdout; raises on failure."""
        rc, out, err = self.gh(["gh", *args])
        if rc != 0:
            raise RuntimeError("gh " + " ".join(map(str, args)) + " failed: " + err)
        return out

    def pf_runner(self, argv, cwd=None, timeout=None):
        """preflight's runner shape: (rc, combined text)."""
        rc, out, err = self.gh(argv)
        return rc, out + err

    # ------------------------------------------------------------ answers
    def _answer(self, a):
        if a[:2] == ["auth", "status"]:
            return ("github.com\n  ✓ Logged in to github.com account someone (keyring)\n"
                    "  - Active account: true\n  - Git operations protocol: https\n"
                    "  - Token: %s************************************\n"
                    "  - Token scopes: %s\n" % (self.token_kind,
                                                ", ".join("'%s'" % s for s in self.scopes)
                                                if self.scopes else "none"))
        if a[:1] == ["api"] and a[1] != "graphql":
            return self._rest(a)
        if a[:2] == ["api", "graphql"]:
            swap = gqlfake.swap(a, labels=set())
            doc = next((x[len("query="):] for x in a if str(x).startswith("query=")), "")
            if "projectItems(" in doc:
                return self._card_read(doc)
            if swap is not None and ("label" in doc and "Project" not in doc
                                     or "issue(number" in doc):
                return swap
            return self._graphql(doc)
        if a[:1] == ["project"]:
            return self._project_cli(a)
        if a[:1] in (["issue"], ["label"]):
            return ""
        raise LookupError(" ".join(a))

    def _kind(self):
        return "orgs" if self.owner_type == "Organization" else "users"

    def _rest(self, a):
        full = a[1] if a[1] != "--paginate" else a[2]
        path, _, query = full.partition("?")
        per = int(re.search(r"per_page=(\d+)", query).group(1)) if "per_page=" in query else 30
        if path == "user":
            if self.viewer is None:
                raise LookupError(path)
            return "%s\n" % self.viewer
        m = re.fullmatch(r"users/([^/]+)", path)
        if m:
            login = m.group(1)
            if login != self.owner and login not in {b["owner"] for b in self.boards}:
                raise LookupError(path)
            return json.dumps({"login": login, "type": self.owner_type if login == self.owner
                               else "Organization", "node_id": "OWNER_%s" % login})
        m = re.fullmatch(r"repos/([^/]+)/([^/]+)", path)
        if m:
            return json.dumps({"full_name": "%s/%s" % m.groups(), "node_id": "R_%s" % m.group(2)})
        m = re.fullmatch(r"repos/([^/]+)/([^/]+)/issues", path)
        if m:
            page = int(next((x[5:] for x in a if str(x).startswith("page=")), "1"))
            per = int(next((x[9:] for x in a if str(x).startswith("per_page=")), "30"))
            return json.dumps(self.issues[(page - 1) * per: page * per])
        m = re.fullmatch(r"(orgs|users)/([^/]+)/projectsV2", path)
        if m:
            if m.group(1) != self._kind() or m.group(2) != self.owner:
                raise LookupError(path)
            # `--paginate --jq '.[] | {...}'`: one compact object per line, every page
            return "".join(json.dumps({"number": b["number"], "title": b["title"],
                                       "node_id": b["id"]}) + "\n" for b in self.boards
                           if b["owner"] == self.owner)
        m = re.fullmatch(r"(orgs|users)/([^/]+)/projectsV2/(\d+)", path)
        if m:
            b = self.board(number=m.group(3), owner=m.group(2))
            if not b:
                raise LookupError(path)
            return json.dumps({"number": b["number"], "title": b["title"], "node_id": b["id"]})
        m = re.fullmatch(r"(orgs|users)/([^/]+)/projectsV2/(\d+)/items", path)
        if m:                                        # one page of the board's items, any content
            b = self.board(number=m.group(3), owner=m.group(2))
            if not b:
                raise LookupError(path)
            rows = [{"id": 2000 + i, "node_id": it["id"],
                     "content_type": (it.get("content") or {}).get("type", "Issue")}
                    for i, it in enumerate(b["items"][:per])]
            if self._arg(a, "--jq") == "length":
                return "%d\n" % len(rows)
            return json.dumps(rows)
        m = re.fullmatch(r"(orgs|users)/([^/]+)/projectsV2/(\d+)/fields", path)
        if m:
            b = self.board(number=m.group(3), owner=m.group(2))
            if not b:
                raise LookupError(path)
            rows = [
                {"id": 1000 + i, "node_id": f["id"], "name": f["name"],
                 "data_type": f.get("data_type") or ("single_select" if f["options"] is not None
                                                     else "text"),
                 **({"options": [{"id": o["id"], "name": {"raw": o["name"], "html": o["name"]},
                                  "description": {"raw": o.get("description", ""),
                                                  "html": o.get("description", "")},
                                  "color": o.get("color", "GRAY")}
                                 for o in f["options"]]} if f["options"] is not None else {})}
                for i, f in enumerate(b["fields"])]
            if "--paginate" not in a:
                rows = rows[:per]                    # one page only, as the real API does
            if "--jq" in a:                          # `--jq '.[]'`: one compact object per line
                return "".join(json.dumps(r) + "\n" for r in rows)
            return json.dumps(rows)
        raise LookupError(path)

    def _graphql(self, doc):
        if doc.startswith("mutation"):
            return self._mutation(doc)
        m = re.search(r'node\(id: "([^"]+)"\)', doc)
        if m and "workflows" in doc:
            b = self.board(node_id=m.group(1))
            if not b:
                return json.dumps({"data": {"node": None}})
            return json.dumps({"data": {"node": {
                "number": b["number"], "url": "https://github.com/%s/%s/projects/%d"
                % (self._kind(), b["owner"], b["number"]),
                "repositories": {"nodes": [{"nameWithOwner": r} for r in b["repos"]]},
                "workflows": {"nodes": [{"name": k, "enabled": v}
                                        for k, v in b["workflows"].items()]}}}})
        raise LookupError("graphql read not modelled: " + doc[:80])

    def _card_read(self, doc):
        """#233's single-card read: the issue's labels and every card it has, each with its board's
        fields (options only on a single-select) and its single-select values keyed by field id."""
        owner, name = self._s(doc, "owner"), self._s(doc, "name")
        number = int(re.search(r"issue\(number: (\d+)\)", doc).group(1))
        full = "%s/%s" % (owner, name)
        mine = full == "%s/%s" % (self.owner, self.repo)
        issue = next((i for i in self.issues if i["number"] == number), None) if mine else None
        cards = [(b, it) for b in self.boards for it in b["items"]
                 if (it.get("content") or {}).get("number") == number
                 and (it.get("content") or {}).get("repository",
                                                   "%s/%s" % (self.owner, self.repo)) == full]
        if issue is None and not cards:
            return json.dumps({"data": {"viewer": ({"login": self.viewer} if self.viewer is not None
                                                   else None), "repository": {"issue": None}}})
        labels = [(lb.get("name") if isinstance(lb, dict) else lb)
                  for lb in ((issue or {}).get("labels") or [])]

        def field(f):
            single = f["options"] is not None
            return {"id": f["id"], "name": f["name"],
                    "dataType": "SINGLE_SELECT" if single else str(f.get("data_type") or "text").upper(),
                    **({"options": [{"id": o["id"], "name": o["name"]} for o in f["options"]]}
                       if single else {})}

        def values(b, it):
            out = []
            for fid, oid in (it.get("values") or {}).items():
                f = next((f for f in b["fields"] if f["id"] == fid), None)
                o = next((o for o in ((f or {}).get("options") or []) if o["id"] == oid), None)
                if f and o:
                    out.append({"name": o["name"], "optionId": oid,
                                "field": {"id": fid, "name": f["name"]}})
            return out

        nodes = [{"id": it["id"], "project": {"id": b["id"], "number": b["number"],
                                              "owner": {"login": b["owner"]},
                                              "fields": {"nodes": [field(f) for f in b["fields"]]}},
                  "fieldValues": {"nodes": values(b, it)}} for b, it in cards]
        viewer = {"login": self.viewer} if self.viewer is not None else None
        return json.dumps({"data": {"viewer": viewer, "repository": {"issue": {
            "labels": {"nodes": [{"name": n} for n in labels]},
            "projectItems": {"nodes": nodes}}}}})

    @staticmethod
    def _s(doc, key):
        m = re.search(key + r': ("(?:[^"\\]|\\.)*")', doc)
        return json.loads(m.group(1)) if m else None

    def _mutation(self, doc):
        if "createProjectV2(" in doc:
            title = self._s(doc, "title")
            b = self.add_board(title)
            repo_id = self._s(doc, "repositoryId")
            if repo_id:
                b["repos"].append("%s/%s" % (self.owner, repo_id[2:]))
            return json.dumps({"data": {"createProjectV2": {"projectV2": {
                "id": b["id"], "number": b["number"], "url": "u/%d" % b["number"]}}}})
        if "copyProjectV2(" in doc:
            src = self.board(node_id=self._s(doc, "projectId"))
            if not src:
                raise LookupError("copy source")
            b = self.add_board(self._s(doc, "title"),
                               fields=json.loads(json.dumps(src["fields"])),
                               workflows=src["workflows"])
            for f in b["fields"]:                       # a copy mints new ids
                f["id"] = f["id"] + "_c%d" % b["number"]
                for o in f["options"] or []:
                    o["id"] = o["id"] + "_c%d" % b["number"]
            b["wf_targets"] = {wf: (t + "_c%d" % b["number"]) if t else None
                               for wf, t in src["wf_targets"].items()}
            return json.dumps({"data": {"copyProjectV2": {"projectV2": {
                "id": b["id"], "number": b["number"], "url": "u/%d" % b["number"]}}}})
        if "linkProjectV2ToRepository(" in doc:
            b = self.board(node_id=self._s(doc, "projectId"))
            b["repos"].append("%s/%s" % (self.owner, self._s(doc, "repositoryId")[2:]))
            return json.dumps({"data": {"linkProjectV2ToRepository": {"repository": {"id": "R"}}}})
        if "updateProjectV2Field(" in doc:
            fid = self._s(doc, "fieldId")
            b, f = next(((b, f) for b in self.boards for f in b["fields"] if f["id"] == fid),
                        (None, None))
            if not f:
                raise LookupError("field " + str(fid))
            new = parse_options(doc)
            for n, o in enumerate(new):
                o["id"] = o["id"] or "o_%s_new%d" % (fid, n)
            kept = {o["id"]: o["name"] for o in new}
            f["options"] = new
            for it in b["items"]:                         # a dropped id wipes the card's value
                oid = (it.get("values") or {}).get(fid)
                if oid is None:
                    continue
                if oid in kept:
                    it[f["name"].lower()] = kept[oid]     # a rename shows through; the id holds
                else:
                    del it["values"][fid]
                    it[f["name"].lower()] = None
            for wf, target in b["wf_targets"].items():   # #720: a deleted target disables it
                if target and target not in kept and wf in b["workflows"]:
                    b["workflows"][wf] = False
            return json.dumps({"data": {"updateProjectV2Field": {"projectV2Field": {"id": fid}}}})
        if "createProjectV2Field(" in doc:
            b = self.board(node_id=self._s(doc, "projectId"))
            name = self._s(doc, "name")
            if self.field(b, name) is not None:
                raise GraphQLError("Name has already been taken")
            opts = parse_options(doc)
            f = {"id": "PVTSSF_%s_%d" % (name, b["number"]), "name": name,
                 "options": [dict(o, id="o_%s_%d" % (o["name"], b["number"])) for o in opts]}
            b["fields"].append(f)
            return json.dumps({"data": {"createProjectV2Field": {"projectV2Field": {
                "id": f["id"], "name": name,
                "options": [{"id": o["id"], "name": o["name"]} for o in f["options"]]}}}})
        raise LookupError("mutation not modelled: " + doc[:80])

    def _arg(self, a, flag):
        return a[a.index(flag) + 1] if flag in a else None

    def _project_cli(self, a):
        verb = a[1]
        if verb == "list":
            visible = [b for b in self.boards if b["owner"] == self._arg(a, "--owner")]  # per owner
            limit = int(self._arg(a, "--limit") or 30)
            return json.dumps({"projects": [{"number": b["number"], "id": b["id"],
                                             "title": b["title"]} for b in visible[:limit]]})
        if verb == "create":        # a fresh board: GitHub's own Title + default Status field
            b = self.add_board(self._arg(a, "--title"), owner=self._arg(a, "--owner"))
            return json.dumps({"number": b["number"], "id": b["id"], "title": b["title"]})
        if verb == "view":
            b = self.board(number=a[2], owner=self._arg(a, "--owner"))
            if not b:
                raise GraphQLError("Could not resolve to a ProjectV2 with the number %s." % a[2])
            return json.dumps({"number": b["number"], "id": b["id"], "title": b["title"]})
        b = (self.board(number=a[2], owner=self._arg(a, "--owner"))
             if len(a) > 2 and str(a[2]).isdigit() else None)
        if verb == "link":
            return ""
        if verb == "field-list":
            return json.dumps({"fields": [
                {"id": f["id"], "name": f["name"], "type": "ProjectV2SingleSelectField"
                 if f["options"] is not None else "ProjectV2Field",
                 **({"options": [dict(o) for o in f["options"]]} if f["options"] is not None
                    else {})} for f in b["fields"]]})
        if verb == "field-create":
            opts = [o.strip() for o in self._arg(a, "--single-select-options").split(",")]
            name = self._arg(a, "--name")
            if self.field(b, name) is not None:
                raise GraphQLError("Name has already been taken")
            b["fields"].append({"id": "PVTSSF_%s_%d" % (name, b["number"]), "name": name,
                                "options": [{"id": "o_%s_%d" % (o, b["number"]), "name": o,
                                             "color": "GRAY", "description": ""}
                                            for o in opts]})
            return "{}"
        if verb == "item-list":
            return json.dumps({"items": [dict(i) for i in b["items"]]})
        if verb == "item-add":
            url = self._arg(a, "--url").rstrip("/").split("/")
            num = int(url[-1])
            it = {"id": "PVTI_%d" % num, "content": {"type": "Issue", "number": num,
                                                     "repository": "/".join(url[-4:-2])},
                  "status": None, "values": {}}
            b["items"].append(it)
            return json.dumps(it)
        if verb == "item-edit":
            pid, iid = self._arg(a, "--project-id"), self._arg(a, "--id")
            fid, oid = self._arg(a, "--field-id"), self._arg(a, "--single-select-option-id")
            b = self.board(node_id=pid)
            f = next(f for f in b["fields"] if f["id"] == fid)
            opt = next(o for o in f["options"] if o["id"] == oid)
            item = next(i for i in b["items"] if i["id"] == iid)
            item[f["name"].lower()] = opt["name"]
            item.setdefault("values", {})[fid] = oid
            return ""
        raise LookupError("project " + verb)

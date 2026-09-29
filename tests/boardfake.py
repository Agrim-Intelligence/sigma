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
  - REST option names come back as {"raw": ..., "html": ...} (measured live, evidence/235).

Imported as a plain sibling module (`import boardfake`), like gqlfake.
"""
import json
import re

import gqlfake

DEFAULT_WORKFLOWS = ("Item closed", "Pull request merged", "Item reopened", "Auto-close issue",
                     "Auto-add sub-issues to project", "Pull request linked to issue")

#: What `gh auth status` prints for a classic token (the token itself masked, as gh does).
FAKE_TOKEN = "ghp_SECRETSECRETSECRETSECRETSECRETSECRET"


class GitHub:
    def __init__(self, owner="acme", owner_type="Organization", repo="widget", boards=(),
                 scopes=("repo", "workflow", "read:org", "project"), token_kind="ghp_"):
        self.owner, self.owner_type, self.repo = owner, owner_type, repo
        self.scopes = list(scopes)
        self.token_kind = token_kind
        self.boards = []            # dicts: number, id, title, fields[], workflows{}, repos[], items[]
        self.next_number = 1
        for b in boards:
            self.add_board(**b)
        self.fail = {}              # substring of a gh call -> stderr; the call exits 1
        self.calls = []             # every argv, as passed (without the leading "gh")
        self.issues = []            # REST issue dicts for the loop's backlog read
        self.labels = {}

    # ------------------------------------------------------------ state helpers
    def add_board(self, title, number=None, fields=None, workflows=None, owner=None):
        number = number or self.next_number
        self.next_number = max(self.next_number, number) + 1
        b = {"number": number, "id": "PVT_%d" % number, "title": title,
             "owner": owner or self.owner,
             "fields": fields if fields is not None else [
                 {"id": "PVTF_title_%d" % number, "name": "Title", "options": None},
                 {"id": "PVTSSF_status_%d" % number, "name": "Status", "options": [
                     {"id": "o_todo_%d" % number, "name": "Todo"},
                     {"id": "o_inprog_%d" % number, "name": "In progress"},
                     {"id": "o_done_%d" % number, "name": "Done"}]}],
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

    def board(self, number=None, node_id=None, title=None):
        for b in self.boards:
            if (number is not None and b["number"] == int(number)) or \
                    (node_id is not None and b["id"] == node_id) or \
                    (title is not None and b["title"] == title):
                return b
        return None

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
        try:
            return 0, self._answer(args), ""
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
        path = a[1] if a[1] != "--paginate" else a[2]
        path = path.split("?", 1)[0]
        if path == "user":
            return "someone\n"
        m = re.fullmatch(r"users/([^/]+)", path)
        if m:
            if m.group(1) != self.owner:
                raise LookupError(path)
            return json.dumps({"login": self.owner, "type": self.owner_type,
                               "node_id": "OWNER_%s" % self.owner})
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
                                       "node_id": b["id"]}) + "\n" for b in self.boards)
        m = re.fullmatch(r"(orgs|users)/([^/]+)/projectsV2/(\d+)", path)
        if m:
            b = self.board(number=m.group(3))
            if not b or m.group(2) != b["owner"]:
                raise LookupError(path)
            return json.dumps({"number": b["number"], "title": b["title"], "node_id": b["id"]})
        m = re.fullmatch(r"(orgs|users)/([^/]+)/projectsV2/(\d+)/fields", path)
        if m:
            b = self.board(number=m.group(3))
            if not b:
                raise LookupError(path)
            return json.dumps([
                {"id": 1000 + i, "node_id": f["id"], "name": f["name"],
                 "data_type": "single_select" if f["options"] is not None else "title",
                 **({"options": [{"id": o["id"], "name": {"raw": o["name"], "html": o["name"]},
                                  "description": {"raw": "", "html": ""}, "color": "GRAY"}
                                 for o in f["options"]]} if f["options"] is not None else {})}
                for i, f in enumerate(b["fields"])])
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
            new = []
            for body in re.findall(r"\{((?:id: \"[^\"]*\", )?name: \"[^\"]*\"[^}]*)\}", doc):
                oid = re.search(r'id: "([^"]*)"', body)
                name = re.search(r'name: "([^"]*)"', body).group(1)
                new.append({"id": oid.group(1) if oid else "o_%s_%s" % (fid, name), "name": name})
            kept = {o["id"] for o in new}
            f["options"] = new
            for wf, target in b["wf_targets"].items():   # #720: a deleted target disables it
                if target and target not in kept and wf in b["workflows"]:
                    b["workflows"][wf] = False
            return json.dumps({"data": {"updateProjectV2Field": {"projectV2Field": {"id": fid}}}})
        if "createProjectV2Field(" in doc:
            b = self.board(node_id=self._s(doc, "projectId"))
            name = self._s(doc, "name")
            opts = [json.loads(n) for n in re.findall(r'name: ("(?:[^"\\]|\\.)*"), color',
                                                      doc)]
            f = {"id": "PVTSSF_%s_%d" % (name, b["number"]), "name": name,
                 "options": [{"id": "o_%s_%d" % (o, b["number"]), "name": o} for o in opts]}
            b["fields"].append(f)
            return json.dumps({"data": {"createProjectV2Field": {"projectV2Field": {
                "id": f["id"]}}}})
        raise LookupError("mutation not modelled: " + doc[:80])

    def _arg(self, a, flag):
        return a[a.index(flag) + 1] if flag in a else None

    def _project_cli(self, a):
        verb = a[1]
        if verb == "list":
            visible = [b for b in self.boards if b["owner"] == self._arg(a, "--owner")]
            limit = int(self._arg(a, "--limit") or 30)
            return json.dumps({"projects": [{"number": b["number"], "id": b["id"],
                                             "title": b["title"]} for b in visible[:limit]]})
        if verb == "view":
            b = self.board(number=a[2])
            if not b or b["owner"] != self._arg(a, "--owner"):
                raise LookupError("project %s" % a[2])
            return json.dumps({"number": b["number"], "id": b["id"], "title": b["title"]})
        b = self.board(number=a[2]) if len(a) > 2 and str(a[2]).isdigit() else None
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
            b["fields"].append({"id": "PVTSSF_%s_%d" % (name, b["number"]), "name": name,
                                "options": [{"id": "o_%s_%d" % (o, b["number"]), "name": o}
                                            for o in opts]})
            return "{}"
        if verb == "item-list":
            return json.dumps({"items": [dict(i) for i in b["items"]]})
        if verb == "item-add":
            num = int(self._arg(a, "--url").rstrip("/").split("/")[-1])
            it = {"id": "PVTI_%d" % num, "content": {"type": "Issue", "number": num},
                  "status": None}
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
            return ""
        raise LookupError("project " + verb)

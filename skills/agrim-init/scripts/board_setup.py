#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create (or finish, or adopt) the GitHub Project board the loop mirrors onto, and pin it (#235).

WHY THIS EXISTS. The loop creates a board itself only when the owner has ZERO boards
(`sources.GitHubSource._ensure_board`); every real organisation already has some, so it refuses
("board mirroring OFF") and nothing ever writes `discovery.github.project.number`. A board the loop
FINDS rather than creates is never given our Status options or a Priority field either. This is
the opt-in gesture that does it once, on the operator's yes:

    board_setup.py create <sdlc_dir> [--owner O] [--title T] [--template N|OWNER/N] [--number N] [--yes]

Without `--yes` it only READS and prints what it would do (exit 0). With `--yes`:
  1. auth + scopes    `gh auth status`, preflight's own check (#229): `repo` + `project`. Missing
                      -> REFUSED (exit 2) with preflight's per-host remediation lines.
  2. resolve board    the owner's boards over REST. A pinned number (config or `--number`) is OURS
                      and is reused. Otherwise a board already titled T is REFUSED (exit 2) with the
                      manual runbook -- never a duplicate. Otherwise:
  3. create           `createProjectV2(ownerId, title, repositoryId)` (links the repo in the same
                      call), or `copyProjectV2` from a template board, then `linkProjectV2ToRepository`.
  4. pin              `project.number` + `project.owner` into config.json, IMMEDIATELY (atomic;
                      every other key kept), so a re-run after any later failure reuses this board.
  5. Status options   the configured columns (`project.columns`, sources.GitHubSource.col) via
                      sources' own id-preserving `_options_mutation`. On OUR EMPTY board -- created
                      or copied IN THIS RUN, or the one an earlier run created (`setup_created` in
                      config) that still has no card -- GitHub's `Todo` and `In progress` are
                      RENAMED (ids kept, so the built-in workflows stay on). On any other board
                      (`--number`, or a pin) the rename map is never passed: nothing is renamed,
                      every existing option keeps its id, name, colour, description and position
                      (read back and echoed), and only the missing options are APPENDED. A missing
                      column whose only difference from an existing option is case (`In progress`
                      vs `In Progress`) is mapped in config (`project.columns.<key>` = the board's
                      spelling), never renamed and never duplicated. Colours/descriptions that
                      cannot be read -> REFUSED.
     the Ready lane   `Ready` is the loop's QUEUE SWITCH (sources `_ready_lane`): without it the
                      loop picks by label; with it (and the shipped `queue_source: "status"`) only
                      a card IN Ready is picked. So it is added ONLY to our empty board above. On
                      an adopted board, or ours once it carries any card, it is WITHHELD ([skip]
                      with the reason) and the loop stays on the label queue: adding it there would
                      strand every goal card in another lane and the loop would read DONE (review
                      of PR #279). Moving to the board queue is `board_migrate.py`'s explicit step,
                      which also seeds the lane; the exact command is printed. A `Ready` the board
                      already has is left exactly as it is.
  6. Priority field   `priority_field` with `discovery.PRIORITIES` (`P0`..`P4`), created or
                      completed. A same-named field that is not single-select, or a case-only
                      variant (`p0`), is REFUSED with the manual fix -- never renamed.
  7. verify           fields re-read and checked; repo link + workflows read in one GraphQL query.
                      "Item closed" off or unreadable -> the exact manual step and its deep link
                      (the API has no workflow create/enable mutation -- research/235).
Every step prints `[ok]`, `[FAIL]`, `[REFUSED]`, `[skip]` or `[manual]`. Any FAIL exits 1 and
prints the exact resume command (it carries `--number N` once the board exists). A REFUSED step
exits 2 with no resume command: re-running cannot help until the named thing is changed by hand.
Re-running on a finished board makes no mutation. A resume after a partial first run finishes the
board exactly as the first run would have while it still has no card; once a loop tick has carded
anything on it, the resume is treated as an adoption (nothing renamed, no `Ready`).

COST: reads are REST (core quota); GraphQL only for mutations and one read. A fresh create is
4 GraphQL calls (3 mutations + 1 read), 3 + ceil(boards/100) + 2 * ceil(fields/100) REST reads and
one `gh auth status` (measured against the fake: tests/test_board_setup.py); a finished board
re-run is 1 GraphQL read. A resume of our own board without `Ready` adds ONE REST read (`/items
?per_page=1 --jq length`: does any card exist?; endpoint shape checked read-only against a real
org board, 2026-09-29). Nothing here runs in the loop's steady state.

SECRETS: gh holds the token; this never passes `--show-token`, never prints gh's raw auth output,
and redacts userinfo from any error it echoes.
"""
import importlib.util
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
HERE = str(pathlib.Path(__file__).resolve())


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pf = _load(_HERE / "preflight.py", "board_setup_preflight")
_LOOP = _HERE.parent.parent / "agrim-loop" / "scripts"


_SOURCES = []


def _sources():
    """sources.py from the sibling agrim-loop skill -- the established by-path exception (see
    agrim-setup's `_load_loop_script`): its column vocabulary, `discovery.PRIORITIES` and the
    id-preserving option mutation are reused, never copied. Loaded once."""
    if not _SOURCES:
        _SOURCES.append(_load(_LOOP / "sources.py", "board_setup_sources"))
    return _SOURCES[0]


ITEM_CLOSED = "Item closed"      # GitHub's name for the workflow that moves a closed item to Done


# ---------------------------------------------------------------- runner

def real_runner(argv, timeout=None):
    """argv (no shell) -> (rc, stdout, stderr); the whole process group is killed on overrun,
    exactly as preflight.real_runner does (reused: `_kill_tree`, `_NEW_GROUP`)."""
    timeout = pf.call_timeout() if timeout is None else timeout
    exe = shutil.which(argv[0])
    if not exe:
        return 127, "", f"{argv[0]}: not found on PATH"
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1",
               GIT_TERMINAL_PROMPT="0")
    group = {"creationflags": pf._NEW_GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen([exe, *argv[1:]], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", env=env, **group)
    except OSError as exc:
        return 126, "", f"{argv[0]}: could not start ({exc})"
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        pf._kill_tree(proc)
        return 124, "", f"{argv[0]}: timed out after {timeout:g}s"
    return proc.returncode, out or "", err or ""


# ---------------------------------------------------------------- helpers

def _q(value):
    """A GraphQL string literal. JSON's escaping is a subset GraphQL accepts (\\uXXXX included).
    EVERY value interpolated into a GraphQL document here goes through this (pinned by
    tests/test_board_setup.py::test_q_is_a_graphql_string_literal)."""
    return json.dumps(str(value))


#: GraphQL's ProjectV2SingleSelectFieldOptionColor. REST returns the same names upper-case
#: (measured on a real board, review of PR #279).
COLORS = ("GRAY", "BLUE", "GREEN", "YELLOW", "ORANGE", "RED", "PINK", "PURPLE")

#: Characters no single quoting survives in BOTH cmd.exe and PowerShell: `"` ends the quote, `%`
#: (cmd) and `$` / backtick (PowerShell) expand inside double quotes, `!` does under cmd's delayed
#: expansion.
_WIN_UNSAFE = frozenset('"%$`!')


def _windows():
    return os.name == "nt"


def _clean(text):
    return pf.printable(pf.redact(str(text or "").strip()))[:400]


class Failed(Exception):
    """A gh call failed; `.detail` is the redacted reason."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class Board:
    """One run against one owner. `out` receives every printed line (tests capture it)."""

    def __init__(self, sdlc_dir, runner=None, out=print, host="github.com"):
        self.sdlc = pathlib.Path(os.path.abspath(str(sdlc_dir)))
        self.runner = runner or real_runner
        self.out = out
        self.host = host
        self.failed = False
        self.refused = False

    # -- gh plumbing
    def gh(self, *args):
        argv = ["gh", *args]
        if self.host != "github.com" and args and args[0] == "api":
            argv[2:2] = ["--hostname", self.host]
        rc, out, err = self.runner(argv)
        if rc != 0:
            raise Failed(_clean(err or out or f"gh exited {rc}"))
        return out

    def rest(self, path):
        return json.loads(self.gh("api", path) or "null")

    def graphql(self, doc):
        data = json.loads(self.gh("api", "graphql", "-f", "query=" + doc) or "{}")
        if data.get("errors"):
            raise Failed(_clean("; ".join(e.get("message", "") for e in data["errors"])))
        return data.get("data") or {}

    def step(self, tag, what, detail=""):
        if tag == "FAIL":
            self.failed = True
        if tag == "REFUSED":
            self.refused = True
        self.out(f"  [{tag}] {what}" + (f": {detail}" if detail else ""))


def _config(sdlc):
    _dir, path = pf._config_path(str(sdlc))          # refuses a symlinked .sdlc / missing config
    return path, json.loads(path.read_text(encoding="utf-8"))


def _child(parent, key):
    """parent[key] as a dict, replacing a missing/null/non-dict value with {} IN PLACE, so a write
    through the returned dict always lands in `parent`."""
    if not isinstance(parent.get(key), dict):
        parent[key] = {}
    return parent[key]


def _gh_block(cfg):
    if not isinstance(cfg, dict):
        raise ValueError("config.json is not a JSON object")
    gh = _child(_child(cfg, "discovery"), "github")
    return gh, _child(gh, "project")


#: config key: the number of the board THIS script created (`project.setup_created`). It is what
#: lets a resume tell "our half-built board" from "a board a human adopted" -- both arrive as
#: `--number N` -- and so decide whether the board may be finished as a fresh one (`_ready_plan`).
CREATED_KEY = "setup_created"


def pin(sdlc, owner, number, created=False):
    """Write ONLY discovery.github.project.number/owner (+ `setup_created` when this run created
    the board; dropped when a DIFFERENT board is pinned -- another number OR another owner, #233
    review); every other key is kept. Atomic. The marker is `{"number": N, "owner": login}`: a
    bare number cannot tell a hand-made board of another owner, reusing the number, from ours.
    The pre-#233 bare-number form is dropped on EVERY re-pin, never upgraded (#308 review): the
    old pin() kept it when only the owner changed, as does a hand edit of `project.owner`, so the
    owner pinned beside it cannot vouch for it."""
    path, cfg = _config(sdlc)
    _gh, proj = _gh_block(cfg)
    proj["number"] = int(number)
    proj["owner"] = owner
    if created:
        proj[CREATED_KEY] = {"number": int(number), "owner": owner}
    elif CREATED_KEY in proj and not _ours(proj.get(CREATED_KEY), number, owner):
        del proj[CREATED_KEY]
    pf._vd._atomic_write_json(path, cfg)


def _ours(marker, number, owner):
    """Does the `setup_created` marker name board `number` of `owner`? (sources' one parse; the old
    bare-number form never does.)"""
    made = _sources().GitHubSource.setup_marker(marker)
    return made is not None and made == (int(number), str(owner or "").strip().casefold())


def pin_columns(sdlc, mapping):
    """Write discovery.github.project.columns.<key> for each {key: board spelling}; every other key
    (including other column names already configured) is kept. Atomic."""
    path, cfg = _config(sdlc)
    _gh, proj = _gh_block(cfg)
    _child(proj, "columns").update(mapping)
    pf._vd._atomic_write_json(path, cfg)


def _repo_from_origin(root):
    """The origin's owner/name and host -- local git only (the gh runner is for gh)."""
    rc, out, _err = real_runner(["git", "-C", str(root), "remote", "get-url", "origin"])
    if rc != 0:
        return None, None
    host, owner, name = pf.parse_remote_url(out.strip())
    return (f"{owner}/{name}" if owner else None), host


def resume_command(sdlc, owner, title, number=None, template=None):
    """The exact gesture to re-run, or None when it cannot be printed safely: on Windows a value
    carrying a character cmd or PowerShell would expand or end the quote on (`_WIN_UNSAFE`, or a
    control character) is refused rather than printed as a line that runs something else."""
    values = [str(sdlc), str(owner), str(title), HERE] + ([str(template)] if template else [])
    if _windows():
        if any(ch in _WIN_UNSAFE or pf._vd._unsafe_char(ch) for v in values for ch in v):
            return None
        quote = lambda v: '"%s"' % v                                  # noqa: E731
    else:
        quote = shlex.quote
    parts = [f"create {quote(str(sdlc))}", f"--owner {quote(owner)}", f"--title {quote(title)}"]
    if number is not None:
        parts.append(f"--number {int(number)}")
    elif template:
        parts.append(f"--template {quote(str(template))}")
    return f"{pf._vd.python_command()} {quote(HERE)} " + " ".join(parts) + " --yes"


def _say_resume(out, sdlc, owner, title, number=None, template=None):
    line = resume_command(sdlc, owner, title, number, template)
    if line is not None:
        out("  " + line)
        return
    out("  (no command printed: a value contains a character cmd/PowerShell would expand or end "
        "the quote on -- one of \" % $ ` !). Re-run the command you ran"
        + (f", adding --number {int(number)}" if number is not None else "") + ".")


def workflows_url(kind, owner, number, host="github.com"):
    return f"https://{host}/{kind}/{owner}/projects/{int(number)}/workflows"


def migrate_command(owner, number, src, backlog="<lane your queued goals sit in>"):
    """The explicit step that moves a board to the status queue: board_migrate.py adds `Ready`
    AND seeds it (#696/#707). Printed, never run from here."""
    script = _HERE.parent.parent / "agrim-doctor" / "scripts" / "board_migrate.py"
    quote = (lambda v: '"%s"' % v) if _windows() else shlex.quote
    extra = ""
    if src.col["ready"] != "Ready":
        extra += " --ready " + quote(src.col["ready"])
    status = src._project_cfg.get("status_field") or "Status"
    if status != "Status":
        extra += " --status-field " + quote(status)
    if src.goal_label != "sdlc:goal":
        extra += " --goal-label " + quote(src.goal_label)
    return (f"{pf._vd.python_command()} {quote(str(script))} --owner {quote(owner)} "
            f"--project {int(number)}{extra} --backlog {quote(backlog)} --apply")


def manual_runbook(kind, owner, title, host="github.com"):
    """The manual path, printed whenever this refuses to create."""
    return [
        "  Manual runbook (nothing was created):",
        f"    1. Open https://{host}/{kind}/{owner}/projects and decide which board the loop should use.",
        f"    2. If it is the existing '{pf.printable(title)}', adopt it: re-run with --number <its number> --yes",
        "       (it gets the Status/Priority options it lacks, APPENDED; nothing on it is renamed,",
        "       recoloured, reordered or deleted. A lane differing only by case, e.g. 'In progress',",
        "       is used as-is via project.columns in config. It does NOT get a 'Ready' lane, so the",
        "       loop keeps picking by label; board_migrate.py is the step that changes that).",
        "    3. If not, re-run with --title '<a different title>' --yes to create a separate board.",
        "    4. Or pin it by hand: discovery.github.project.number = <number> in .sdlc/config.json.",
    ]


# ---------------------------------------------------------------- the gesture

def create(sdlc_dir, owner=None, title=None, template=None, number=None, yes=False,
           runner=None, out=print):
    """Returns the exit code: 0 done (or dry run), 1 partial failure (resume printed), 2 refused."""
    runner = runner or real_runner
    try:
        _path, cfg = _config(sdlc_dir)
    except (ValueError, OSError) as exc:
        out(f"board_setup: REFUSED -- {pf.printable(exc)}")
        return 2
    sdlc = pathlib.Path(os.path.abspath(str(sdlc_dir)))
    gh_cfg, proj = _gh_block(json.loads(json.dumps(cfg)))
    repo = str(gh_cfg.get("repo") or "").strip()
    host = "github.com"
    if not repo:
        repo, host = _repo_from_origin(sdlc.parent)
        host = host or "github.com"
    if not repo or "/" not in repo:
        out("board_setup: REFUSED -- no GitHub repository: set discovery.github.repo (or add a "
            "GitHub `origin` remote) first.")
        return 2
    src = _sources().GitHubSource(
        {"discovery": {"source": "github", "github": dict(gh_cfg, repo=repo)}},
        run=lambda _a: "")
    owner = owner or src._proj_owner()
    title = title or src._proj_title()
    template = template if template is not None else proj.get("template")
    created_before = proj.get(CREATED_KEY)
    pinned = number if number is not None else proj.get("number")
    try:
        pinned = int(pinned) if pinned not in (None, "") else None
    except (TypeError, ValueError):
        out(f"board_setup: REFUSED -- project.number {pf.printable(pinned)!s} is not a number.")
        return 2
    b = Board(sdlc, runner, out, host)

    # 1. auth + scopes -- preflight's own checks and remediation text (#229)
    pf_run = lambda argv, _cwd=None, _t=None: (lambda r: (r[0], r[1] + r[2]))(runner(argv))  # noqa: E731
    auth, status = pf.check_gh_auth(host, pf_run, pf.network_timeout())
    if auth["ok"] is not True:
        out("board_setup: REFUSED -- gh cannot act for you yet:")
        for line in pf.failure_lines(auth):
            out(line)
        return 2
    if owner.startswith("@"):
        try:
            owner = b.gh("api", "user", "--jq", ".login").strip()
        except Failed as exc:
            out(f"board_setup: REFUSED -- could not resolve {owner}: {exc.detail}")
            return 2
    try:
        who = b.rest(f"users/{owner}")
    except (Failed, ValueError) as exc:
        out(f"board_setup: REFUSED -- could not read owner '{pf.printable(owner)}': "
            f"{getattr(exc, 'detail', exc)}")
        return 2
    org = who.get("type")
    kind = "orgs" if org == "Organization" else "users"
    scopes = pf.check_scopes(status, ["repo", "project"], host, org, owner)
    if scopes["ok"] is False:
        out("board_setup: REFUSED -- the gh token cannot create a board:")
        for line in pf.failure_lines(scopes):
            out(line)
        return 2
    if scopes["ok"] is None:
        for line in pf.failure_lines(scopes):
            out(line)
        out("  (continuing: the first call GitHub refuses is reported with the resume command)")

    # 2. resolve: pinned (ours) / same title (refuse) / create
    try:
        listing = b.gh("api", "--paginate", f"{kind}/{owner}/projectsV2?per_page=100",
                       "--jq", ".[] | {number, title, node_id}")
        boards = [json.loads(ln) for ln in listing.splitlines() if ln.strip()]
    except (Failed, ValueError) as exc:
        out(f"board_setup: REFUSED -- could not list {pf.printable(owner)}'s boards, so a duplicate "
            f"cannot be ruled out: {getattr(exc, 'detail', exc)}")
        return 2
    mine = next((x for x in boards if pinned is not None and x.get("number") == pinned), None)
    if pinned is not None and not mine:
        out(f"board_setup: REFUSED -- the pinned board #{pinned} is not one of "
            f"{pf.printable(owner)}'s {len(boards)} board(s). Fix discovery.github.project.number "
            "(or --number), or remove it to create a new board.")
        return 2
    same = [x for x in boards if x.get("title") == title and x is not mine]
    if not mine and same:
        out(f"board_setup: REFUSED -- {pf.printable(owner)} already has a board titled "
            f"'{pf.printable(title)}' (#{', #'.join(str(x['number']) for x in same)}); creating "
            "another would be a duplicate the loop could then manage instead of yours.")
        for line in manual_runbook(kind, owner, title, host):
            out(line)
        return 2
    keys = ("backlog", "ready", "in_progress", "qc", "done", "blocked", "parked")
    cols = [src.col[k] for k in keys]
    prio = src.priority_field
    prios = list(_sources().discovery.PRIORITIES) if prio else []
    if not yes:
        what = (f"reuse pinned board #{mine['number']} '{pf.printable(mine['title'])}' (only "
                "APPENDING missing options; nothing renamed; 'Ready' only if this setup created "
                "the board and it has no card yet)" if mine
                else f"copy template board {template} as '{pf.printable(title)}'" if template
                else f"create '{pf.printable(title)}' under {pf.printable(owner)}")
        out(f"board_setup: dry run (nothing written) -- would {what}, link {repo}, set Status to "
            f"{' / '.join(cols)}" + (f" and {prio} to {' / '.join(prios)}" if prio else "")
            + ", and pin its number in config.json.")
        out("  To do it:")
        _say_resume(out, sdlc, owner, title, mine["number"] if mine else None, template)
        return 0

    out(f"board_setup: {pf.printable(owner)} / {repo}")
    num, pid = (mine["number"], mine["node_id"]) if mine else (None, None)
    fresh = False               # True only for a board this run created or copied: may rename
    try:
        repo_id = b.rest(f"repos/{repo}").get("node_id")
    except (Failed, ValueError, AttributeError) as exc:
        b.step("FAIL", "read repository", getattr(exc, "detail", str(exc)))
        repo_id = None
    if mine:
        b.step("ok", "board", f"reusing pinned #{num} '{pf.printable(mine['title'])}'")
    elif repo_id:
        try:
            if template:
                tnum, towner = _split_template(template, owner)
                tkind = kind if towner == owner else (
                    "orgs" if b.rest(f"users/{towner}").get("type") == "Organization" else "users")
                tid = b.rest(f"{tkind}/{towner}/projectsV2/{tnum}")["node_id"]
                made = b.graphql(
                    "mutation { copyProjectV2(input: {projectId: %s, ownerId: %s, title: %s, "
                    "includeDraftIssues: false}) { projectV2 { id number url } } }"
                    % (_q(tid), _q(who.get("node_id")), _q(title)))["copyProjectV2"]["projectV2"]
                b.step("ok", "board", f"copied template {towner}/{tnum} as #{made['number']} "
                       f"'{pf.printable(title)}'")
            else:
                made = b.graphql(
                    "mutation { createProjectV2(input: {ownerId: %s, title: %s, repositoryId: %s}) "
                    "{ projectV2 { id number url } } }"
                    % (_q(who.get("node_id")), _q(title), _q(repo_id)))["createProjectV2"]["projectV2"]
                b.step("ok", "board", f"created #{made['number']} '{pf.printable(title)}'")
            num, pid, fresh = made["number"], made["id"], True
        except (Failed, KeyError, TypeError, ValueError) as exc:
            b.step("FAIL", "create board", getattr(exc, "detail", repr(exc)))
    if num is None:
        out("board_setup: stopped -- no board exists yet. Fix the cause above, then resume:")
        _say_resume(out, sdlc, owner, title, None, template)
        return 1

    # 4. pin -- before anything else can fail, so a resume finds this board by its number
    try:
        pin(sdlc, owner, num, created=fresh)
        b.step("ok", "pin", f"discovery.github.project.number = {num}, owner = {pf.printable(owner)}")
    except (OSError, ValueError) as exc:
        b.step("FAIL", "pin", f"{pf.printable(exc)} -- set discovery.github.project.number = {num} "
               "by hand")

    # 5 + 6. fields
    cols = _ensure_fields(b, kind, owner, num, src, dict(zip(keys, cols)), prio, prios, fresh,
                          sdlc, ours=fresh or _ours(created_before, num, owner))

    # 7. repository link + workflows, one read
    _link_and_workflows(b, kind, owner, num, pid, repo, repo_id, host)

    if b.refused:
        out(f"board_setup: REFUSED -- board #{num} is pinned, but a step above was refused; "
            "re-running changes nothing until you make the fix it names by hand. Then re-run:")
        _say_resume(out, sdlc, owner, title, num)
        return 2
    if b.failed:
        out("board_setup: INCOMPLETE -- see [FAIL] above. Resume (safe to repeat; it reuses board "
            f"#{num}):")
        _say_resume(out, sdlc, owner, title, num)
        return 1
    if not _gh_block(_config(sdlc)[1])[1].get("enabled"):
        out("  note: discovery.github.project.enabled is not true, so the loop will not mirror "
            "onto this board until it is.")
    out(f"board_setup: done -- board #{num} is pinned; the next /agrim-loop pick moves its card to "
        f"{cols[2]}.")
    return 0


def _split_template(template, owner):
    text = str(template)
    towner, _, tnum = text.rpartition("/")
    return int(tnum), (towner or owner)


def _raw(value):
    """REST returns option names/descriptions as {"raw": ..., "html": ...}; accept a plain string."""
    return value.get("raw") if isinstance(value, dict) else value


def _read_fields(b, kind, owner, num):
    """{name: {id, type, options: [{id, name, color, description}], readable}} for EVERY field,
    over every page (`--paginate`; a board has up to 50 fields and the default page is 30).
    `readable` is False when any option's colour or description could not be read -- then a
    rewrite would have to invent them, so the caller refuses instead."""
    listing = b.gh("api", "--paginate", f"{kind}/{owner}/projectsV2/{num}/fields?per_page=100",
                   "--jq", ".[]")
    fields = {}
    for ln in listing.splitlines():
        if not ln.strip():
            continue
        f = json.loads(ln)
        opts, readable = [], True
        for o in f.get("options") or []:
            color = str(o.get("color") or "").upper()
            desc = _raw(o.get("description")) if "description" in o else None
            if color not in COLORS or not isinstance(desc, str):
                readable = False
            opts.append({"id": o.get("id"), "name": _raw(o.get("name")),
                         "color": color if color in COLORS else None,
                         "description": desc if isinstance(desc, str) else None})
        fields[f.get("name")] = {"id": f.get("node_id"), "type": f.get("data_type"),
                                 "options": opts, "readable": readable}
    return fields


def _create_field(b, pid, name, options):
    opts = ", ".join("{name: %s, color: GRAY, description: %s}" % (_q(o), _q("")) for o in options)
    b.graphql("mutation { createProjectV2Field(input: {projectId: %s, dataType: SINGLE_SELECT, "
              "name: %s, singleSelectOptions: [%s]}) { projectV2Field { ... on "
              "ProjectV2SingleSelectField { id } } } }" % (_q(pid), _q(name), opts))


def _case_variants(options, have):
    """{wanted: existing} for each wanted name absent verbatim but present differing only by case."""
    folded = {}
    for h in have:
        folded.setdefault(h.casefold(), h)
    return {o: folded[o.casefold()] for o in options
            if o not in have and o.casefold() in folded}


def _has_cards(b, kind, owner, num):
    """True/False: does the board carry ANY item? One REST read of one item. Raises Failed."""
    got = b.gh("api", f"{kind}/{owner}/projectsV2/{num}/items?per_page=1", "--jq", "length")
    try:
        return int(got.strip() or 0) > 0
    except ValueError:
        raise Failed("unexpected answer to the items read: " + _clean(got)) from None


def _ready_plan(b, kind, owner, num, fresh, ours, ready, have):
    """(plan, why) for the `Ready` lane -- the loop's queue switch (sources `_ready_lane`):
    "present" the board already has it (left exactly as it is), "add" (our empty board), or
    "withhold" with the reason. Only a board that is OURS and has NO card may get it: there,
    nothing can be stranded outside the lane. `fresh` needs no read -- a board created or copied
    seconds ago carries no card (copyProjectV2 is sent includeDraftIssues: false)."""
    if ready in have:
        return "present", None
    if fresh:
        return "add", None
    if not ours:
        return "withhold", "an adopted board"
    try:
        carded = _has_cards(b, kind, owner, num)
    except Failed as exc:
        b.step("FAIL", "read cards", f"could not tell whether board #{num} has cards, so "
               f"'{ready}' is not added: {exc.detail}")
        return "withhold", None
    return ("withhold", "this board already has card(s)") if carded else ("add", None)


def _ensure_fields(b, kind, owner, num, src, cols, prio, prios, fresh, sdlc, ours=None):
    """Status + Priority. `cols` is {column key: name}; returns the column names in key order as
    the board now spells them. Only OUR EMPTY board (`fresh`, or `ours` -- the board an earlier run
    created -- with no card yet) may rename GitHub's own `Todo` / `In progress` and gain `Ready`;
    on any other board nothing is renamed, no `Ready` is added, every existing option is echoed back
    with its own id, name, colour and description IN ITS OWN POSITION, and missing ones are
    appended."""
    ours = fresh if ours is None else ours
    status_name = src._project_cfg.get("status_field") or "Status"
    names = list(cols.values())
    try:
        fields = _read_fields(b, kind, owner, num)
        pid = b.rest(f"{kind}/{owner}/projectsV2/{num}")["node_id"]
    except (Failed, ValueError, KeyError, TypeError) as exc:
        b.step("FAIL", "read fields", getattr(exc, "detail", repr(exc)))
        return names
    have_status = [o["name"] for o in (fields.get(status_name) or {}).get("options") or []]
    plan, why = _ready_plan(b, kind, owner, num, fresh, ours, cols["ready"], have_status)
    rename = ({"Todo": cols["backlog"], "In progress": cols["in_progress"]} if plan == "add"
              else None)
    wanted = [(status_name, names, rename)]
    if prio:
        wanted.append((prio, prios, None))
    else:
        b.step("skip", "priority field", "priority_field is off in config")
    checked = []
    for name, options, ren in wanted:
        fld = fields.get(name)
        have = [o["name"] for o in (fld or {}).get("options") or []]
        if fld is not None and fld.get("type") != "single_select":
            other = "status_field" if name == status_name else "priority_field"
            b.step("REFUSED", f"{name} field",
                   f"'{pf.printable(name)}' exists but is a {pf.printable(fld.get('type'))} field, "
                   f"not single-select. Rename or delete it on the board, or set "
                   f"discovery.github.project.{other} to another name")
            continue
        variants = _case_variants(options, have) if (fld is not None and not ren) else {}
        if variants and name == status_name:
            # the board's own spelling becomes the configured column: nothing renamed, no duplicate
            mapping = {k: variants[v] for k, v in cols.items() if v in variants}
            try:
                pin_columns(sdlc, mapping)
            except (OSError, ValueError) as exc:
                b.step("FAIL", f"{name} field", f"could not map {mapping} in config: "
                       f"{pf.printable(exc)}")
                continue
            cols.update(mapping)
            options = names = list(cols.values())
            b.step("ok", f"{name} field", "mapped " + ", ".join(
                f"project.columns.{k} = '{pf.printable(v)}'" for k, v in mapping.items())
                + " (the board's own spelling; nothing renamed)")
        elif variants:
            b.step("REFUSED", f"{name} field", "the board spells " + ", ".join(
                f"'{pf.printable(h)}' where the loop needs '{w}'" for w, h in variants.items())
                + "; rename those options on the board by hand (never renamed for you)")
            continue
        if name == status_name and plan == "withhold":
            options = [o for o in options if o != cols["ready"]]
        if fld is not None and not ren:
            # adoption: the board's own lanes first, in THEIR order; only what is missing appended
            options = have + [o for o in options if o not in have]
        checked.append((name, options))
        missing = [o for o in options if o not in have]
        try:
            if fld is None:
                _create_field(b, pid, name, options)
                b.step("ok", f"{name} field", "created with " + pf.printable(" / ".join(options)))
            elif not missing:
                b.step("ok", f"{name} field", "already has " + pf.printable(" / ".join(options)))
            elif not fld["readable"]:
                b.step("REFUSED", f"{name} field",
                       "its options' colours/descriptions could not be read, so rewriting them "
                       "would reset them; add " + " / ".join(missing) + " on the board by hand")
                checked.pop()
            else:
                doc = src._options_mutation(fld["id"], options, fld["options"], rename=ren)
                b.gh("api", "graphql", "-f", doc)
                b.step("ok", f"{name} field", ("options set to " + " / ".join(options)
                       + " (Todo / In progress renamed on this new board; ids kept)") if ren else
                       ("appended " + " / ".join(missing) + " (nothing renamed or reordered; "
                        "existing options' ids, names, colours and descriptions kept)"))
        except Failed as exc:
            b.step("FAIL", f"{name} field", exc.detail)
    if plan == "present" and not fresh:
        b.step("ok", "Ready lane", f"the board already has '{pf.printable(cols['ready'])}'; left as "
               "it is (the loop already picks from it)")
    elif plan == "withhold" and why:
        _say_withheld(b, owner, num, src, cols, why, have_status)
    # measured, not assumed: read back what GitHub now holds
    try:
        after = _read_fields(b, kind, owner, num)
    except (Failed, ValueError) as exc:
        b.step("FAIL", "verify fields", getattr(exc, "detail", repr(exc)))
        return names
    for name, options in checked:
        have = [o["name"] for o in (after.get(name) or {}).get("options") or []]
        missing = [o for o in options if o not in have]
        if missing:
            b.step("FAIL", f"verify {name}", "missing " + pf.printable(", ".join(missing)))
    return names


def _say_withheld(b, owner, num, src, cols, why, lanes):
    ready = pf.printable(cols["ready"])
    b.step("skip", "Ready lane", f"not added -- {why}. '{ready}' is the loop's queue switch: "
           f"with it, only a card IN '{ready}' is picked, so every goal card now in another lane "
           "would be stranded and the loop would read DONE")
    lane = next((x for x in lanes if x not in cols.values()), None) or cols["backlog"]
    b.out(f"  The loop KEEPS picking by the {pf.printable(src.goal_label)} label queue on this "
          "board (how it picks work is unchanged); its status writes still move cards here.")
    b.out(f"  To switch it to the board queue deliberately -- adds '{ready}' AND moves the queued "
          "goal cards into it (repeat per lane holding them; dry run without --apply):")
    b.out("    " + migrate_command(owner, num, src, backlog=lane))


def _link_and_workflows(b, kind, owner, num, pid, repo, repo_id, host):
    try:
        node = b.graphql('query { node(id: %s) { ... on ProjectV2 { url repositories(first: 100) '
                         '{ nodes { nameWithOwner } } workflows(first: 30) { nodes { name enabled '
                         '} } } } }' % _q(pid)).get("node") or {}
    except Failed as exc:
        b.step("FAIL", "read board", exc.detail)
        node = None
    if node is not None:
        linked = {r.get("nameWithOwner", "").lower()
                  for r in (node.get("repositories") or {}).get("nodes") or []}
        if repo.lower() in linked:
            b.step("ok", "linked", repo)
        elif repo_id:
            try:
                b.graphql("mutation { linkProjectV2ToRepository(input: {projectId: %s, "
                          "repositoryId: %s}) { repository { id } } }" % (_q(pid), _q(repo_id)))
                b.step("ok", "linked", repo)
            except Failed as exc:
                b.step("FAIL", "link repository", exc.detail)
    flows = {w.get("name"): w.get("enabled")
             for w in ((node or {}).get("workflows") or {}).get("nodes") or []}
    url = workflows_url(kind, owner, num, host)
    if flows.get(ITEM_CLOSED) is True:
        b.step("ok", "auto-close", f"'{ITEM_CLOSED}' workflow is on (closed issues move to Done)")
    else:
        state = "off" if ITEM_CLOSED in flows else "could not be read"
        b.step("manual", "auto-close",
               f"'{ITEM_CLOSED}' is {state}, and GitHub has no API to turn a workflow on. Open "
               f"{url} -> '{ITEM_CLOSED}' -> Edit -> set Status: Done -> Save and turn on workflow")


USAGE = ("usage: board_setup.py create <sdlc_dir> [--owner O] [--title T] "
         "[--template N|OWNER/N] [--number N] [--yes]")


def main(argv, runner=None, out=print):
    args = list(argv[1:])
    if not args or args[0] in ("-h", "--help"):
        out(USAGE)
        return 0 if args else 2
    if args[0] != "create" or len(args) < 2:
        out(USAGE)
        return 2
    sdlc, rest = args[1], args[2:]
    opts = {"--owner": None, "--title": None, "--template": None, "--number": None}
    yes = False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--yes":
            yes = True
            i += 1
        elif a in opts and i + 1 < len(rest):
            opts[a] = rest[i + 1]
            i += 2
        else:
            out(USAGE)
            return 2
    try:
        number = int(opts["--number"]) if opts["--number"] is not None else None
    except ValueError:
        out(USAGE)
        return 2
    return create(sdlc, owner=opts["--owner"], title=opts["--title"], template=opts["--template"],
                  number=number, yes=yes, runner=runner, out=out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Find, and plan the removal of, references to a private repository in a GitHub repository's text
and in this checkout (issue 282).

WHY. Before a repository goes public, no issue, pull request, comment, review or release text in it
may name or link the private repository it grew out of. The names to look for are the owner's own
and stay out of this tree: the tool reads them from a patterns file, and it reports WHERE a pattern
matched, never WHAT it matched.

USAGE
    python3 tools/leak_refs.py scan    --repo OWNER/NAME [--patterns FILE] [--max-pages N] [--timeout S]
    python3 tools/leak_refs.py tree    [--root DIR] [--patterns FILE]
    python3 tools/leak_refs.py text    FILE|- [--patterns FILE]
    python3 tools/leak_refs.py rewrite --repo OWNER/NAME --out FILE [--replacement TEXT] [--patterns FILE]
    python3 tools/leak_refs.py rewrite --apply --from FILE --repo OWNER/NAME [--patterns FILE]
                                       [--max-writes N]

  scan     every issue and PR (all states): title, body, conversation comments, review comments,
           review bodies; commit comments; release names and bodies; and the EDIT HISTORY of each
           (revisions and title renames). Read-only.
  tree     this checkout's files (`git ls-files --cached --others --exclude-standard`: tracked and
           untracked-not-ignored), their paths, and every commit message reachable from HEAD. A
           tracked file deleted in the checkout is read from its index blob; an unreadable file or
           a submodule is refused, never skipped.
  text     one file, or stdin (`-`): for checking a PR body or a comment BEFORE posting it.
  rewrite  the dry-run: a planned neutral rewrite of every editable hit, written ONLY to `--out`
           (created 0600, never overwritten, refused inside any git work tree), with a manual list,
           the history purge list and a machine-readable manifest. Stdout gets counts and the path.
           `--apply --from FILE` applies that manifest, compare-and-skip: an edit whose live text is
           no longer its dry-run text, or whose item is gone (404 or 410), is skipped, never
           overwritten.

THE PATTERNS FILE. `--patterns FILE`, else the `SIGMA_LEAK_PATTERNS` environment variable; there is
NO default path. One Python regular expression per line, matched case-insensitively; blank lines and
lines starting `#` are skipped. A pattern that does not compile, or that matches the empty string,
is refused by its LINE NUMBER, never its text. The file must resolve outside every git work tree.

OUTPUT. A hit is a location plus the pattern's line number (`issue 12 body line 3 pattern 2`),
then counts per surface and per pattern line. No location carries `#` before a number, but a
`commit <sha>` line holds a commit SHA, which GitHub links when pasted: location lines are NEVER
pasted into GitHub text. Every printed line passes the patterns once more and any match becomes
`[redacted: pattern P]`.

EXIT: 0 = nothing matched; 1 = something matched or was truncated (or, for --apply, an edit was
skipped, unverified or left for a later run); 2 = a refusal (`leak_refs: REFUSED [<code>] <detail>`
on stderr, nothing on stdout), a bad argument, or ANY `gh` or git failure -- never read as clean.

WHAT IT DOES NOT DO
  * No network except through `gh`, and stdlib only. Nothing but `rewrite --apply --from FILE`
    writes to GitHub, and that only text its live read shows as OWNER, MEMBER or COLLABORATOR
    authored (or a release), unchanged since the dry-run, and passing every rewrite invariant.
  * No edit-history purge (GitHub has no API for it) and no title rewrite (a rename keeps the old
    title in a public timeline event): both are listed for the owner, as manual web-UI work.
  * Not covered: file contents in earlier commits (the whole history goes public; `tree` reads the
    checkout and commit messages only), commits on other branches, branch names, tags and tag
    messages, milestones, labels, release edit history (no API), Actions logs and artifacts,
    Projects, the repository description and topics, the wiki, discussions, forks, notification
    e-mail already sent.
See docs/publish-runbook.md for the owner's runbook.
"""
import argparse
import datetime
import difflib
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: The mirror module is loaded by path (it is not a package): it carries the blocker vocabulary
#: (`blocker_scan`, with its `legacy`), `scrub` and the excerpt cap the rewrite invariants read.
_MIRROR_PATH = ROOT / "skills" / "agrim-loop" / "scripts" / "mirror.py"
SCHEMA = "sigma.leak-refs/1"
ENV_PATTERNS = "SIGMA_LEAK_PATTERNS"
PER_PAGE = 100              # REST page size, and the GraphQL `nodes(ids)` batch size
READ_PACE = 0.25            # seconds between two `gh` calls
WRITE_PACE = 1.0            # seconds between two writes (GitHub's content-write limit is ~80/min)
RETRY_DEFAULT, RETRY_CAP, RETRY_MAX = 60, 300, 3
STDERR_CUT = 200
_OUT_MODE = 0o600
OWNED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
ISSUE_WORDS = "a private predecessor issue"
REPO_WORDS = "the private predecessor repository"
#: `sources.OFFBOARD_COMMENT_PREFIXES`, the park/fail comment prefixes the check-time blocker scan
#: strips before capping a comment. A copy, because loading sources.py costs far more than the
#: tuple; `tests/test_leak_refs.py` pins the two equal.
_OFFBOARD = ("Parked by Sigma — needs human review: ",
             "Failed in the Sigma loop — needs a fix (not a decision): ")
_NOT_A_REPO = "fatal: not a git repository (or any of the parent directories)"
#: Removed from every git child's environment: each can point git at another repository, or stop
#: its discovery early, so a path inside a work tree would read as outside one.
_GIT_SCRUB = ("GIT_DIR", "GIT_WORK_TREE", "GIT_CEILING_DIRECTORIES", "GIT_INDEX_FILE",
              "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
              "GIT_NAMESPACE")
_RATE_MARKERS = ("rate limit", "secondary rate", "abuse detection", "too many requests")
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")

#: Where a hit is widened to, in order: a markdown link, an autolink WITH its angle brackets (so it
#: never becomes an HTML tag), a bare URL, an `owner/repo#N` reference; else the hit alone. A bare
#: URL never takes a markdown or HTML delimiter: it stops before a backtick, a quote or a pipe, and
#: drops trailing punctuation, emphasis characters and an unbalanced `)` or `]` (`_url_end`); an
#: `owner/repo#N` reference starts at a letter or digit, never at a leading `_`.
_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\([^)\n]*\)")
_AUTOLINK_RE = re.compile(r"<https?://[^>\s]*>", re.I)
_URL_RE = re.compile(r"https?://[^\s<>`\"'|]+", re.I)
_URL_TRAIL = ".,;:!?*_~"
_OWNER_REF_RE = re.compile(r"[A-Za-z0-9][\w.-]*/[\w.-]+#\d+")
#: Invariant (e): the characters a replaced span may not split a run of.
_RUN_DELIMS = "`*_~"

#: What GitHub turns into a link or a notification. An edit may remove these, never add one.
_TOKEN_RES = (
    ("ref", re.compile(r"[\w.-]+/[\w.-]+#\d+")),
    ("number", re.compile(r"#\d+")),
    ("gh", re.compile(r"\bGH-\d+", re.I)),
    ("url", re.compile(r"\bhttps?://[^\s<>]+", re.I)),
    ("mention", re.compile(r"(?<![\w`@])@[A-Za-z0-9][A-Za-z0-9-]*")),
    ("sha", re.compile(r"(?<![\w/])[0-9a-f]{7,40}(?![\w/])", re.I)),
    ("tag", re.compile(r"<[A-Za-z][\w-]*")),
)

_Q_COUNTS = """# leak_refs:counts
query($owner: String!, $name: String!) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    issues { totalCount } pullRequests { totalCount }
    commitComments { totalCount } releases { totalCount }
  }
}"""

_Q_ITEMS = """# leak_refs:items
query($ids: [ID!]!) {
  rateLimit { cost remaining resetAt }
  nodes(ids: $ids) {
    __typename id
    ... on Issue { comments { totalCount } }
    ... on PullRequest {
      comments { totalCount }
      reviews(first: 100) { pageInfo { hasNextPage } nodes { id databaseId body authorAssociation } }
    }
  }
}"""

_RENAMES = """timelineItems(itemTypes: [RENAMED_TITLE_EVENT], first: 100) {
        pageInfo { hasNextPage } nodes { ... on RenamedTitleEvent { createdAt previousTitle } } }"""

_Q_HISTORY = """# leak_refs:history
query($ids: [ID!]!) {
  rateLimit { cost remaining resetAt }
  nodes(ids: $ids) {
    __typename id
    ... on Comment {
      userContentEdits(first: 100) { pageInfo { hasNextPage } nodes { diff editedAt deletedAt } }
    }
    ... on Issue { %s }
    ... on PullRequest { %s }
  }
}""" % (_RENAMES, _RENAMES)

_ITEM_TYPES = frozenset({"Issue", "PullRequest"})
_HISTORY_TYPES = frozenset({"Issue", "PullRequest", "IssueComment", "PullRequestReview",
                            "PullRequestReviewComment", "CommitComment"})

#: Apply: where each surface is read and written, and which of its fields may be rewritten. Titles
#: are never here: a title hit is always manual.
_ENDPOINTS = {
    "issue": ("issues/{number}", "PATCH"),
    "pr": ("issues/{number}", "PATCH"),
    "comment": ("issues/comments/{id}", "PATCH"),
    "review-comment": ("pulls/comments/{id}", "PATCH"),
    "commit-comment": ("comments/{id}", "PATCH"),
    "review": ("pulls/{number}/reviews/{id}", "PUT"),
    "release": ("releases/{id}", "PATCH"),
}
_FIELDS = {"issue": ("body",), "pr": ("body",), "comment": ("body",), "review-comment": ("body",),
           "commit-comment": ("body",), "review": ("body",), "release": ("name", "body")}


class Refused(Exception):
    """A loud stop: exit 2, one stderr line with a code, nothing on stdout."""

    def __init__(self, code, detail=""):
        Exception.__init__(self, code)
        self.code, self.detail = code, detail


# ------------------------------------------------------------------------------ running things

def _real_run(args, input_text=None, timeout=60):
    """-> (rc, stdout, stderr). A timeout is rc 124, a binary that cannot be run rc 127. git children
    get `_GIT_SCRUB` removed, `GIT_DISCOVERY_ACROSS_FILESYSTEM=1` and `LC_ALL=C`."""
    args = [str(a) for a in args]
    env = None
    if args and args[0] == "git":
        env = {k: v for k, v in os.environ.items() if k not in _GIT_SCRUB}
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"
        env["LC_ALL"] = "C"
    feed = {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env, **feed)
    except subprocess.TimeoutExpired:
        return 124, "", "%s: timed out after %ss" % (args[0], timeout)
    except OSError:
        return 127, "", "%s: cannot be run (is it installed?)" % args[0]
    return proc.returncode, proc.stdout, proc.stderr


class Ctx:
    """The injectable seams (`run`, `sleep`), the loaded patterns and the pacing of `gh` calls."""

    def __init__(self, run, sleep):
        self.run, self.sleep = run, sleep
        self.patterns, self.timeout, self.calls = None, 60.0, 0

    def gh(self, args, input_text=None):
        if self.calls:
            self.sleep(READ_PACE)
        self.calls += 1
        return self.run(["gh"] + list(args), input_text, self.timeout)

    def progress(self, line):
        print(_safe("leak_refs: " + line, self.patterns), file=sys.stderr)


def _gh_fail(ctx, where, rc, err):
    """A `gh` failure refuses. Only the first stderr line is echoed: redacted WHOLE, then cut."""
    first = next((line for line in (err or "").splitlines() if line.strip()), "")
    first = _safe(first, ctx.patterns)[:STDERR_CUT]
    raise Refused("gh-failed", "%s: gh exit %d%s" % (where, rc, (": " + first) if first else ""))


# ------------------------------------------------------------------------------ patterns

def parse_patterns(text):
    """-> [(line_number, compiled)]. Refuses a pattern that does not compile or that matches the
    empty string (by line number only), and a file with no pattern at all (a vacuous green)."""
    patterns = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            rx = re.compile(stripped, re.IGNORECASE)
        except (re.error, OverflowError, RecursionError):
            raise Refused("pattern-compile", "patterns line %d does not compile" % number)
        if rx.search("") is not None:
            raise Refused("pattern-matches-empty", "patterns line %d matches the empty string" % number)
        patterns.append((number, rx))
    if not patterns:
        raise Refused("zero-patterns", "the patterns file holds no pattern, only blank or # lines")
    return patterns


def load_patterns(arg, ctx):
    source = arg or os.environ.get(ENV_PATTERNS) or ""
    if not source:
        raise Refused("no-patterns-source", "pass --patterns FILE or set %s; there is no default "
                      "path" % ENV_PATTERNS)
    path = pathlib.Path(os.path.expanduser(source))
    _refuse_inside_work_tree(path, "the patterns file", ctx)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise Refused("patterns-unreadable", "cannot read the patterns file")
    return parse_patterns(text)


def _refuse_inside_work_tree(path, what, ctx):
    """Refuse `path` inside ANY git work tree, failing closed: the only accepted answer is git's own
    `not a git repository (or any of the parent directories)`."""
    try:
        probe = pathlib.Path(os.path.abspath(str(path))).resolve()
        while not probe.is_dir() and probe.parent != probe:
            probe = probe.parent
    except (OSError, RuntimeError):
        raise Refused("work-tree-unknown", "cannot resolve %s" % what)
    rc, _out, err = ctx.run(["git", "-C", str(probe), "rev-parse", "--show-toplevel"], None, ctx.timeout)
    if rc == 0:
        raise Refused("inside-work-tree", "%s resolves inside a git work tree; keep it outside every "
                      "repository" % what)
    if rc == 128 and (err or "").startswith(_NOT_A_REPO):
        return
    raise Refused("work-tree-unknown", "cannot tell whether %s is inside a git work tree (git exit "
                  "%d)" % (what, rc))


def find_hits(text, patterns):
    """-> sorted [(line, pattern_line_number, (start, end))] over `text`; empty matches ignored."""
    hits = []
    for number, rx in patterns:
        for m in rx.finditer(text or ""):
            if m.end() > m.start():
                hits.append(((text or "").count("\n", 0, m.start()) + 1, number, m.span()))
    hits.sort()
    return hits


def _dedupe(hits):
    """One location per (line, pattern)."""
    seen, out = set(), []
    for line, number, span in hits:
        if (line, number) not in seen:
            seen.add((line, number))
            out.append((line, number, span))
    return out


def _safe(line, patterns):
    """The belt behind the output rule: any pattern match left in a printed line is redacted."""
    for number, rx in patterns or ():
        line = rx.sub(lambda m, n=number: ("[redacted: pattern %d]" % n) if m.end() > m.start()
                      else m.group(0), line)
    return line


def _tilde(path):
    home = os.path.abspath(os.path.expanduser("~"))
    full = os.path.abspath(str(path))
    if full == home:
        return "~"
    if full.startswith(home.rstrip(os.sep) + os.sep):
        return "~" + os.sep + full[len(home.rstrip(os.sep)) + 1:]
    return full


def _summary(by_surface, by_pattern, truncated=None, scanned=None):
    lines = []
    if scanned:
        lines.append("scanned: " + ", ".join("%d %s" % (count, label) for count, label in scanned))
    total = sum(by_surface.values())
    parts = ", ".join("%s %d" % (k, by_surface[k]) for k in sorted(by_surface))
    lines.append("hits: %d%s" % (total, (" (%s)" % parts) if parts else ""))
    if truncated is not None:
        lines.append("truncated: %d" % truncated)
    lines.append("hits by pattern line: " + (", ".join("%d=%d" % (k, by_pattern[k])
                                                        for k in sorted(by_pattern)) or "none"))
    return lines


def _emit(lines, patterns):
    for line in lines:
        print(_safe(line, patterns))


# ------------------------------------------------------------------------------ text and tree

def cmd_text(args, ctx):
    ctx.patterns = load_patterns(args.patterns, ctx)
    if args.input == "-":
        text, name = sys.stdin.read(), "stdin"
    else:
        try:
            text = pathlib.Path(args.input).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            raise Refused("input-unreadable", "cannot read the input file")
        name = args.input
    lines, by_pattern = [], Counter()
    for line, number, _span in _dedupe(find_hits(text, ctx.patterns)):
        lines.append("text %s line %d pattern %d" % (name, line, number))
        by_pattern[number] += 1
    by_surface = Counter({"text": len(lines)}) if lines else Counter()
    _emit(lines + _summary(by_surface, by_pattern), ctx.patterns)
    return 1 if lines else 0


def _git(ctx, root, *args):
    return ctx.run(["git", "-C", str(root)] + list(args), None, ctx.timeout)


def _index_texts(ctx, root, rel, name):
    """A listed entry that is no file in the checkout: a tracked file deleted there is read from
    its index blob(s); a submodule (a gitlink) is refused, since its content is another
    repository's; anything else (listed, then gone) is refused. Never a silent skip."""
    rc, out, _err = _git(ctx, root, "--literal-pathspecs", "ls-files", "-z", "-s", "--", rel)
    if rc != 0:
        raise Refused("git-failed", "git ls-files -s exited %d" % rc)
    blobs = []
    for entry in out.split("\0"):
        meta, _tab, path = entry.partition("\t")
        fields = meta.split()
        if path != rel or len(fields) != 3:
            continue
        if fields[0] == "160000":
            raise Refused("submodule", "tree: %s is a submodule; scan it on its own with --root" % name)
        blobs.append(fields[1])
    if not blobs:
        raise Refused("file-unreadable", "tree: %s was listed but is gone; re-run" % name)
    texts = []
    for oid in blobs:
        rc, out, _err = _git(ctx, root, "cat-file", "blob", oid)
        if rc != 0:
            raise Refused("git-failed", "git cat-file exited %d" % rc)
        texts.append((" (index)", out))
    return texts


def cmd_tree(args, ctx):
    ctx.patterns = load_patterns(args.patterns, ctx)
    root = pathlib.Path(os.path.expanduser(args.root)).resolve() if args.root else ROOT
    rc, out, err = _git(ctx, root, "rev-parse", "--show-toplevel")
    if rc == 128 and (err or "").startswith(_NOT_A_REPO):
        raise Refused("root-not-toplevel", "--root is not inside a git repository")
    if rc != 0:
        raise Refused("git-failed", "git rev-parse exited %d" % rc)
    if pathlib.Path(out.strip()).resolve() != root:
        raise Refused("root-not-toplevel", "--root must be a repository's top level")
    rc, out, _err = _git(ctx, root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if rc != 0:
        raise Refused("git-failed", "git ls-files exited %d" % rc)
    entries = out.split("\0")
    if root == ROOT and "tools/leak_refs.py" not in entries:
        raise Refused("listing-incomplete", "the listing of this repository lacks tools/leak_refs.py")
    lines, by_pattern, seen, files = [], Counter(), set(), 0
    for number, rel in enumerate(entries, 1):
        if not rel or rel in seen:
            continue
        seen.add(rel)
        path_hits = _dedupe(find_hits(rel, ctx.patterns))
        for _line, pnum, _span in path_hits:
            lines.append("tree path entry %d pattern %d" % (number, pnum))
            by_pattern[pnum] += 1
        name = ("entry %d" % number) if path_hits else rel
        full = root / rel
        try:
            if os.path.islink(str(full)):
                texts = [("", os.readlink(str(full)))]
            elif full.is_file():
                texts = [("", full.read_bytes().decode("utf-8", errors="replace"))]
            else:
                texts = _index_texts(ctx, root, rel, name)  # not in the checkout, or a gitlink
        except OSError:
            raise Refused("file-unreadable", "tree: cannot read %s; nothing is skipped silently" % name)
        files += 1
        for suffix, text in texts:
            for line, pnum, _span in _dedupe(find_hits(text, ctx.patterns)):
                lines.append("tree %s%s line %d pattern %d" % (name, suffix, line, pnum))
                by_pattern[pnum] += 1
    commits = 0
    rc, out, _err = _git(ctx, root, "rev-parse", "--verify", "-q", "HEAD")
    if rc == 0:
        rc, out, _err = _git(ctx, root, "log", "-z", "--format=%H%x00%B", "HEAD")
        if rc != 0:
            raise Refused("git-failed", "git log exited %d" % rc)
        parts = out.split("\0")
        for i in range(0, len(parts) - 1, 2):
            commits += 1
            for line, pnum, _span in _dedupe(find_hits(parts[i + 1], ctx.patterns)):
                lines.append("commit %s message line %d pattern %d" % (parts[i][:12], line, pnum))
                by_pattern[pnum] += 1
    elif not (rc == 1 and not out.strip()):                 # rc 1, no output: no commits yet
        raise Refused("git-failed", "git rev-parse HEAD exited %d" % rc)
    by_surface = Counter({"tree": len(lines)}) if lines else Counter()
    _emit(lines + _summary(by_surface, by_pattern, scanned=[(files, "file(s)"), (commits, "commit(s)")]),
          ctx.patterns)
    return 1 if lines else 0


# ------------------------------------------------------------------------------ GitHub reads

def _rest_pages(ctx, repo, path, max_pages, extra=""):
    """Every row of a REST list: explicit pages, oldest first, stopping on a short page, deduplicated
    by id. More than `max_pages` pages refuses; it never truncates."""
    endpoint = "repos/%s/%s" % (repo, path)
    rows, seen, page = [], set(), 1
    while True:
        if page > max_pages:
            raise Refused("page-cap", "%s: more than %d pages (--max-pages); nothing was read short"
                          % (endpoint, max_pages))
        url = "%s?%sper_page=%d&page=%d&sort=created&direction=asc" % (endpoint, extra, PER_PAGE, page)
        where = "%s page %d" % (endpoint, page)
        rc, out, err = ctx.gh(["api", url])
        if rc != 0:
            _gh_fail(ctx, where, rc, err)
        try:
            data = json.loads(out)
        except ValueError:
            raise Refused("gh-failed", "%s: unparsable JSON" % where)
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise Refused("bad-shape", "%s: not a list of objects" % where)
        for row in data:
            if not isinstance(row.get("id"), int):
                raise Refused("bad-shape", "%s: a row without an id" % where)
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
        ctx.progress("%s page %d: %d record(s)" % (path, page, len(data)))
        if len(data) < PER_PAGE:
            return rows
        page += 1


def _graphql(ctx, name, query, variables):
    """One GraphQL query, the body on stdin (never argv). Reads the rate limit on every query and
    refuses while `remaining` is under twice the cost."""
    where = "graphql %s" % name
    rc, out, err = ctx.gh(["api", "graphql", "--input", "-"],
                          json.dumps({"query": query, "variables": variables}))
    if rc != 0:
        _gh_fail(ctx, where, rc, err)
    try:
        doc = json.loads(out)
    except ValueError:
        raise Refused("gh-failed", "%s: unparsable JSON" % where)
    if not isinstance(doc, dict):
        raise Refused("bad-shape", "%s: not an object" % where)
    if doc.get("errors"):
        raise Refused("gh-failed", "%s: %d GraphQL error(s)" % (where, len(doc["errors"])))
    data = doc.get("data")
    limit = data.get("rateLimit") if isinstance(data, dict) else None
    if not isinstance(limit, dict) or not isinstance(limit.get("cost"), int) \
            or not isinstance(limit.get("remaining"), int):
        raise Refused("bad-shape", "%s: no data or no rateLimit" % where)
    if limit["remaining"] < 2 * limit["cost"]:
        raise Refused("budget", "%s: GraphQL budget low (remaining %d, cost %d); it resets at %s"
                      % (where, limit["remaining"], limit["cost"], limit.get("resetAt")))
    return data


def _nodes_batch(ctx, name, query, ids, expected):
    """`nodes(ids:)` in batches of 100. Every node must be non-null, of an expected type, and each
    requested id must come back exactly once. -> {id: node}."""
    out, total = {}, (len(ids) + PER_PAGE - 1) // PER_PAGE
    for k in range(0, len(ids), PER_PAGE):
        chunk = ids[k:k + PER_PAGE]
        data = _graphql(ctx, name, query, {"ids": chunk})
        where = "graphql %s batch %d/%d" % (name, k // PER_PAGE + 1, total)
        nodes = data.get("nodes")
        if not isinstance(nodes, list):
            raise Refused("bad-shape", "%s: no nodes list" % where)
        for node in nodes:
            if not isinstance(node, dict) or node.get("__typename") not in expected:
                raise Refused("bad-shape", "%s: a node is null or of an unexpected type" % where)
        if Counter(node.get("id") for node in nodes) != Counter(chunk):
            raise Refused("bad-shape", "%s: asked for %d node(s), got %d, not each id exactly once"
                          % (where, len(chunk), len(nodes)))
        for node in nodes:
            out[node["id"]] = node
        ctx.progress("%s: %d node(s)" % (where, len(nodes)))
    return out


def _connection(node, key, where):
    conn = node.get(key)
    if not isinstance(conn, dict) or not isinstance(conn.get("nodes"), list) \
            or not isinstance(conn.get("pageInfo"), dict) \
            or not isinstance(conn["pageInfo"].get("hasNextPage"), bool):
        raise Refused("bad-shape", "%s: %s is not a connection with a boolean hasNextPage" % (where, key))
    return conn


def _counts(ctx, repo):
    owner, name = repo.split("/", 1)
    data = _graphql(ctx, "counts", _Q_COUNTS, {"owner": owner, "name": name})
    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise Refused("bad-shape", "graphql counts: no repository")
    out = {}
    for key in ("issues", "pullRequests", "commitComments", "releases"):
        value = repository.get(key)
        if not isinstance(value, dict) or not isinstance(value.get("totalCount"), int):
            raise Refused("bad-shape", "graphql counts: no %s totalCount" % key)
        out[key] = value["totalCount"]
    return out


def _moved(detail):
    return Refused("moved-during-scan", detail + "; something was created or deleted during the "
                   "walk: re-run once nothing is writing")


def _number_from(url, kind):
    m = re.search(r"/%s/(\d+)$" % kind, url or "")
    return int(m.group(1)) if m else None


def _doc(loc, field, text, owned, url, edit, show_field):
    return {"loc": loc, "field": field, "text": text or "", "owned": owned, "url": url or "",
            "edit": edit, "show_field": show_field}


def _where(doc, line):
    if doc["field"] in ("title", "name"):
        return "%s %s" % (doc["loc"], doc["field"])
    return "%s%s line %d" % (doc["loc"], " body" if doc["show_field"] else "", line)


def collect(ctx, repo, max_pages):
    """Read every surface of `repo`. -> {"docs", "history", "truncated", "scanned"}. Any failure,
    bad shape or count that moved during the walk refuses."""
    before = _counts(ctx, repo)
    items = _rest_pages(ctx, repo, "issues", max_pages, "state=all&")
    comments = _rest_pages(ctx, repo, "issues/comments", max_pages)
    review_comments = _rest_pages(ctx, repo, "pulls/comments", max_pages)
    commit_comments = _rest_pages(ctx, repo, "comments", max_pages)
    releases = _rest_pages(ctx, repo, "releases", max_pages)
    after = _counts(ctx, repo)
    for when, c in (("before", before), ("after", after)):
        if c["issues"] + c["pullRequests"] != len(items):
            raise _moved("issues and pull requests: %d counted %s the walk, %d walked"
                         % (c["issues"] + c["pullRequests"], when, len(items)))
        if c["commitComments"] != len(commit_comments):
            raise _moved("commit comments: %d counted %s the walk, %d walked"
                         % (c["commitComments"], when, len(commit_comments)))
        if c["releases"] != len(releases):
            raise _moved("releases: %d counted %s the walk, %d walked" % (c["releases"], when, len(releases)))

    kinds, by_number, rest_counts = {}, {}, {}
    for it in items:
        n, node_id = it.get("number"), it.get("node_id")
        if not isinstance(n, int) or not isinstance(node_id, str) or not isinstance(it.get("comments"), int):
            raise Refused("bad-shape", "repos/%s/issues: an item without number, node_id or comments" % repo)
        kinds[n] = "pr" if it.get("pull_request") else "issue"
        by_number[n] = it
        rest_counts[n] = it["comments"]
    walked = Counter()
    for cm in comments:
        n = _number_from(cm.get("issue_url"), "issues")
        if n is None or not isinstance(cm.get("node_id"), str):
            raise Refused("bad-shape", "repos/%s/issues/comments: a comment without issue_url or node_id" % repo)
        if n not in by_number:
            raise _moved("a comment on item %d, which the item walk did not see" % n)
        cm["_number"] = n
        walked[n] += 1

    batch = _nodes_batch(ctx, "items", _Q_ITEMS, [it["node_id"] for it in items], _ITEM_TYPES)
    docs, truncated, reviews = [], [], []
    for it in items:
        n = it["number"]
        loc = "%s %d" % (kinds[n], n)
        node = batch.get(it["node_id"])
        if node is None:
            raise Refused("bad-shape", "graphql items: %s got no node" % loc)
        count = node.get("comments")
        if not isinstance(count, dict) or not isinstance(count.get("totalCount"), int):
            raise Refused("bad-shape", "graphql items: %s has no comments count" % loc)
        if not (rest_counts[n] == walked.get(n, 0) == count["totalCount"]):
            raise _moved("%s comments: %d by its REST count, %d walked, %d by GraphQL"
                         % (loc, rest_counts[n], walked.get(n, 0), count["totalCount"]))
        owned = it.get("author_association") in OWNED
        edit = {"surface": kinds[n], "id": it.get("id"), "number": n, "updated_at": it.get("updated_at")}
        docs.append(_doc(loc, "title", it.get("title"), owned, it.get("html_url"), edit, True))
        docs.append(_doc(loc, "body", it.get("body"), owned, it.get("html_url"), edit, True))
        if kinds[n] == "pr":
            conn = _connection(node, "reviews", "graphql items: %s" % loc)
            if conn["pageInfo"].get("hasNextPage"):
                truncated.append("truncated %s reviews (more than 100)" % loc)
            for rv in conn["nodes"]:
                if not isinstance(rv, dict) or not isinstance(rv.get("databaseId"), int) \
                        or not isinstance(rv.get("id"), str):
                    raise Refused("bad-shape", "graphql items: %s has a review without ids" % loc)
                reviews.append((n, rv))

    for cm in comments:
        n = cm["_number"]
        loc = "%s %d comment %d" % (kinds[n], n, cm["id"])
        edit = {"surface": "comment", "id": cm["id"], "number": n, "updated_at": cm.get("updated_at")}
        docs.append(_doc(loc, "body", cm.get("body"), cm.get("author_association") in OWNED,
                         cm.get("html_url"), edit, False))
    for n, rv in reviews:
        loc = "pr %d review %d" % (n, rv["databaseId"])
        edit = {"surface": "review", "id": rv["databaseId"], "number": n, "updated_at": None}
        url = "%s#pullrequestreview-%d" % (by_number[n].get("html_url") or "", rv["databaseId"])
        docs.append(_doc(loc, "body", rv.get("body"), rv.get("authorAssociation") in OWNED, url, edit, True))
    for rc_ in review_comments:
        n = _number_from(rc_.get("pull_request_url"), "pulls")
        loc = ("pr %d review-comment %d" % (n, rc_["id"])) if n is not None else "review-comment %d" % rc_["id"]
        edit = {"surface": "review-comment", "id": rc_["id"], "number": n, "updated_at": rc_.get("updated_at")}
        docs.append(_doc(loc, "body", rc_.get("body"), rc_.get("author_association") in OWNED,
                         rc_.get("html_url"), edit, False))
    for cc in commit_comments:
        loc = "commit-comment %d" % cc["id"]
        edit = {"surface": "commit-comment", "id": cc["id"], "number": None, "updated_at": cc.get("updated_at")}
        docs.append(_doc(loc, "body", cc.get("body"), cc.get("author_association") in OWNED,
                         cc.get("html_url"), edit, False))
    for rl in releases:
        loc = "release %d" % rl["id"]
        edit = {"surface": "release", "id": rl["id"], "number": None, "updated_at": rl.get("updated_at")}
        for field in ("name", "body"):
            # only push access publishes a release, so a release counts as owned
            docs.append(_doc(loc, field, rl.get(field), True, rl.get("html_url"), edit, True))

    # the edit history of everything that has one, plus the title renames of every item
    meta = {}
    for it in items:
        meta[it["node_id"]] = ("%s %d" % (kinds[it["number"]], it["number"]), True, it.get("html_url"))
    for cm in comments:
        meta[cm["node_id"]] = ("%s %d comment %d" % (kinds[cm["_number"]], cm["_number"], cm["id"]),
                               False, cm.get("html_url"))
    for n, rv in reviews:
        meta[rv["id"]] = ("pr %d review %d" % (n, rv["databaseId"]), True, by_number[n].get("html_url"))
    for rows, label in ((review_comments, "review-comment"), (commit_comments, "commit-comment")):
        for row in rows:
            if not isinstance(row.get("node_id"), str):
                raise Refused("bad-shape", "repos/%s: a %s without node_id" % (repo, label))
            meta[row["node_id"]] = ("%s %d" % (label, row["id"]), False, row.get("html_url"))
    nodes = _nodes_batch(ctx, "history", _Q_HISTORY, list(meta), _HISTORY_TYPES)
    history, revisions, renames = [], 0, 0
    for nid, node in nodes.items():
        loc, show, url = meta[nid]
        where = "graphql history: %s" % loc
        edits = _connection(node, "userContentEdits", where)
        if edits["pageInfo"].get("hasNextPage"):
            truncated.append("truncated %s revisions (more than 100)" % loc)
        for rev in edits["nodes"]:
            if not isinstance(rev, dict):
                raise Refused("bad-shape", "%s: a null revision" % where)
            if rev.get("deletedAt"):
                continue
            if not isinstance(rev.get("diff"), str):
                raise Refused("bad-shape", "%s: a revision without its text" % where)
            revisions += 1
            for line, pnum, _span in _dedupe(find_hits(rev["diff"], ctx.patterns)):
                history.append({"kind": "revision", "pattern": pnum, "url": url, "line":
                                "history %s%s revision %s line %d pattern %d [manual: web UI, delete "
                                "the revision]" % (loc, " body" if show else "", rev.get("editedAt"),
                                                   line, pnum)})
        if node.get("__typename") in _ITEM_TYPES:
            timeline = _connection(node, "timelineItems", where)
            if timeline["pageInfo"].get("hasNextPage"):
                truncated.append("truncated %s title-renames (more than 100)" % loc)
            for event in timeline["nodes"]:
                if not isinstance(event, dict) or not isinstance(event.get("previousTitle"), str):
                    raise Refused("bad-shape", "%s: a title rename without its title" % where)
                renames += 1
                for _line, pnum, _span in _dedupe(find_hits(event["previousTitle"], ctx.patterns)):
                    history.append({"kind": "title-rename", "pattern": pnum, "url": url, "line":
                                    "history %s title-rename %s pattern %d [manual: not deletable]"
                                    % (loc, event.get("createdAt"), pnum)})
    scanned = [(len(items), "item(s)"), (len(comments), "comment(s)"), (len(reviews), "review(s)"),
               (len(review_comments), "review-comment(s)"), (len(commit_comments), "commit-comment(s)"),
               (len(releases), "release(s)"), (revisions, "revision(s)"), (renames, "title-rename(s)")]
    return {"docs": docs, "history": history, "truncated": truncated, "scanned": scanned}


def _surface(doc):
    return doc["edit"]["surface"]


def cmd_scan(args, ctx):
    repo = _repo(args)
    ctx.patterns = load_patterns(args.patterns, ctx)
    found = collect(ctx, repo, args.max_pages)
    lines, by_surface, by_pattern = [], Counter(), Counter()
    for doc in found["docs"]:
        for line, pnum, _span in _dedupe(find_hits(doc["text"], ctx.patterns)):
            lines.append("%s pattern %d" % (_where(doc, line), pnum))
            by_surface[_surface(doc)] += 1
            by_pattern[pnum] += 1
    for h in found["history"]:
        lines.append(h["line"])
        by_surface["history"] += 1
        by_pattern[h["pattern"]] += 1
    lines += found["truncated"]
    _emit(lines + _summary(by_surface, by_pattern, len(found["truncated"]), found["scanned"]), ctx.patterns)
    return 1 if (by_surface or found["truncated"]) else 0


# ------------------------------------------------------------------------------ the rewrite plan

_CONSUMERS = {}
_NEEDED = (("", "scrub"), ("", "_EXCERPT_CHARS"), ("blocker_scan", "_BLOCK_RE"),
           ("blocker_scan", "_EXPLICIT_BLOCK_RE"), ("blocker_scan", "strip_unpark_qa"),
           ("blocker_scan", "extract_refs"), ("blocker_scan", "UNPARK_QA_START"),
           ("blocker_scan", "UNPARK_QA_END"), ("blocker_scan.legacy", "find_marker"),
           ("blocker_scan.legacy", "MARKERS"))


def _consumers():
    """`mirror.py`, loaded by path ONCE per process: the very `blocker_scan` (and its `legacy`),
    `scrub` and excerpt cap the board mirror and the check-time blocker scan use."""
    if "mirror" not in _CONSUMERS:
        try:
            spec = importlib.util.spec_from_file_location("leak_refs_mirror", str(_MIRROR_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            raise Refused("blocker-scan-unavailable", "cannot load skills/agrim-loop/scripts/mirror.py")
        for owner, attr in _NEEDED:
            obj = module
            for part in [p for p in owner.split(".") if p]:
                obj = getattr(obj, part, None)
            if obj is None or not hasattr(obj, attr):
                raise Refused("blocker-scan-unavailable", "mirror.py lacks %s" % ".".join(
                    [p for p in (owner, attr) if p]))
        _CONSUMERS["mirror"] = module
    return _CONSUMERS["mirror"]


def _tokens(text):
    return Counter((kind, m.group(0).lower()) for kind, rx in _TOKEN_RES for m in rx.finditer(text or ""))


def _url_end(text, start, end):
    url = text[start:end]
    while True:
        shorter = url.rstrip(_URL_TRAIL)
        for close, opener in ((")", "("), ("]", "[")):
            if shorter.endswith(close) and shorter.count(close) > shorter.count(opener):
                shorter = shorter[:-1]
        if shorter == url:
            return start + len(url)
        url = shorter


def _widen(text, start, end):
    """The whole reference a hit sits in."""
    for rx in (_LINK_RE, _AUTOLINK_RE, _URL_RE, _OWNER_REF_RE):
        for m in rx.finditer(text):
            ms, me = m.span()
            if rx is _URL_RE:
                me = _url_end(text, ms, me)
            if ms < end and start < me:
                return min(ms, start), max(me, end)
    return start, end


def _merge(spans):
    out = []
    for start, end in sorted(spans):
        if out and start < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _default_replacement(span_text):
    return ISSUE_WORDS if re.search(r"#\d|https?://", span_text, re.I) else REPO_WORDS


def _views(text, patterns, drop_hits):
    """Every whole-text view a blocker consumer takes, as multisets of (phrase, ref): the raw text and
    its unpark-Q&A-stripped form, each under both blocker regexes, and `extract_refs` itself. With
    `drop_hits`, a match overlapping a pattern hit in that view's own text is dropped: an edge that
    runs through the private reference is one the rewrite is meant to remove."""
    bs = _consumers().blocker_scan
    views = []
    for view_text in (text, bs.strip_unpark_qa(text)):
        hit_spans = [span for _l, _n, span in find_hits(view_text, patterns)] if drop_hits else []
        for rx in (bs._BLOCK_RE, bs._EXPLICIT_BLOCK_RE):
            multiset = Counter()
            for m in rx.finditer(view_text):
                if any(m.start() < e and s < m.end() for s, e in hit_spans):
                    continue
                multiset[(m.group(1).lower(), m.group(2))] += 1
            views.append(multiset)
    views.append(Counter((r.get("phrase"), r.get("ref")) for r in bs.extract_refs(text)))
    return views


def _cap_shift(before, after, consumers):
    """Invariant (d): could this edit move text across the check-time excerpt cut? Conservative.
    Every check-time excerpt of the item lies in `text[:reach]`, so the part of it from the start
    of the first changed line (`q`) on lies in `text[q:reach]`; with no Q&A marker in either tail
    and no reference number in the after tail of an item whose excerpt is cut, nothing new reaches
    a blocker scan that the whole-text views did not already compare."""
    bs = consumers.blocker_scan
    cap = consumers._EXCERPT_CHARS
    reach = cap + max(len(p) for p in _OFFBOARD)
    if consumers.scrub(before) != before or consumers.scrub(after) != after:
        return True
    p = len(os.path.commonprefix([before, after]))
    q = before.rfind("\n", 0, p) + 1
    for tail in (before[q:reach], after[q:reach]):
        for marker in (bs.UNPARK_QA_START, bs.UNPARK_QA_END):
            if bs.legacy.find_marker(tail, marker)[0] != -1:
                return True
    cut = any(len(t) > cap or t.startswith(_OFFBOARD) for t in (before, after))
    return bool(cut and re.search(r"#\d", after[q:reach]))


def _code_mask(text):
    """One byte per character: 1 inside a markdown code span, its backticks and fences included.
    CommonMark's pairing: a backtick run closes only on the next run of the same length; a run
    after a backslash opens nothing; an unclosed run is literal."""
    mask = bytearray(len(text))
    runs = [m.span() for m in re.finditer(r"`+", text)]
    i = 0
    while i < len(runs):
        start, end = runs[i]
        j = i + 1
        if not (start and text[start - 1] == "\\"):
            while j < len(runs) and runs[j][1] - runs[j][0] != end - start:
                j += 1
            if j < len(runs):
                mask[start:runs[j][1]] = b"\1" * (runs[j][1] - start)
                i = j + 1
                continue
        i += 1
    return mask


def _inside_tag(text, start):
    """Is `start` inside an HTML tag (an attribute value, say): an opened `<x` before it with no
    `>` between?"""
    lt = text.rfind("<", 0, start)
    return lt != -1 and ">" not in text[lt:start] and re.match(r"<[A-Za-z/!]", text[lt:lt + 2]) is not None


def _delimiter_break(before, after, spans, reps):
    """Invariant (e): could this edit break markdown or HTML around it? True when a replaced span
    holds a delimiter it cannot preserve (a pipe, an odd number of double quotes, an unbalanced
    bracket), splits a run of backticks or emphasis characters, sits inside an HTML tag, or when
    any text OUTSIDE the spans moves into or out of a code span (the backtick-parity case)."""
    for start, end in spans:
        seg = before[start:end]
        if "|" in seg or seg.count('"') % 2 or any(seg.count(o) != seg.count(c) for o, c in
                                                   (("[", "]"), ("(", ")"), ("<", ">"))):
            return True
        if (start and before[start - 1] == before[start] and before[start] in _RUN_DELIMS) or \
                (end < len(before) and before[end - 1] == before[end] and before[end] in _RUN_DELIMS):
            return True
        if _inside_tag(before, start):
            return True
    old, new = _code_mask(before), _code_mask(after)
    pos_old = pos_new = 0
    for (start, end), rep in list(zip(spans, reps)) + [((len(before), len(before)), "")]:
        width = start - pos_old
        if old[pos_old:start] != new[pos_new:pos_new + width]:
            return True
        pos_old, pos_new = end, pos_new + width + len(rep)
    return False


def _check_pair(before, after, spans, patterns, reps):
    """-> (reasons, edges_removed): every invariant the edit breaks, and the explicit blocker edges it
    removes (from the `extract_refs` view). `reps` holds each span's replacement, in order."""
    consumers = _consumers()
    legacy = consumers.blocker_scan.legacy
    reasons = []
    if find_hits(after, patterns):                                          # (a)
        reasons.append("pattern-left")
    old, new = _views(before, patterns, True), _views(after, patterns, False)
    if any(new[i][k] > old[i][k] for i in range(len(new)) for k in new[i]):  # (b)
        reasons.append("blocker-edge")
    old_tokens, new_tokens = _tokens(before), _tokens(after)
    if any(new_tokens[k] > old_tokens[k] for k in new_tokens):              # (c)
        reasons.append("token-added")
    if _cap_shift(before, after, consumers):                                # (d)
        reasons.append("cap-shift")
    if any(legacy.find_marker(before[s:e], m)[0] != -1 for s, e in spans for m in legacy.MARKERS):
        reasons.append("marker-in-span")
    if _delimiter_break(before, after, spans, reps):                        # (e)
        reasons.append("delimiter")
    removed = sum(old[-1][k] - new[-1][k] for k in old[-1] if old[-1][k] > new[-1][k])
    return reasons, removed


def plan_edit(text, patterns, replacement=None):
    """-> {"after", "spans", "reasons", "edges_removed"}. Each hit is widened to its whole reference,
    overlapping spans merge, and each span becomes a fixed neutral phrase with no number in it."""
    spans = _merge(_widen(text, s, e) for _l, _n, (s, e) in find_hits(text, patterns))
    reps = [replacement or _default_replacement(text[s:e]) for s, e in spans]
    after = text
    for (start, end), rep in reversed(list(zip(spans, reps))):
        after = after[:start] + rep + after[end:]
    reasons, removed = _check_pair(text, after, spans, patterns, reps)
    return {"after": after, "spans": spans, "reasons": reasons, "edges_removed": removed}


def _check_replacement(text, patterns):
    legacy = _consumers().blocker_scan.legacy
    if not text.strip():
        raise Refused("replacement-unsafe", "--replacement is empty")
    if _tokens(text):
        raise Refused("replacement-unsafe", "--replacement carries a link, reference or mention token")
    if find_hits(text, patterns):
        raise Refused("replacement-unsafe", "--replacement matches a pattern")
    if any(legacy.find_marker(text, m)[0] != -1 for m in legacy.MARKERS):
        raise Refused("replacement-unsafe", "--replacement holds a Sigma marker")


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _open_out(path):
    """Create the dry-run file 0600, never overwriting and never following a symlink."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags, _OUT_MODE)
    except FileExistsError:
        raise Refused("out-exists", "the --out file exists; a dry-run never overwrites")
    except OSError:
        raise Refused("out-unwritable", "cannot create the --out file")
    if hasattr(os, "fchmod"):
        os.fchmod(fd, _OUT_MODE)
    return os.fdopen(fd, "w", encoding="utf-8")


def _dry_run_text(repo, edits, manual, history, truncated, replacement):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = ["# leak_refs dry-run", "", "repo: " + repo, "generated_at: " + now, "",
             "This file holds the text being removed. Keep it private (mode 0600) and outside every",
             "repository, and never paste any of it into GitHub text.", "",
             "## Edits (%d)" % len(edits)]
    manifest = []
    for doc, plan in edits:
        e = dict(doc["edit"])
        e.update(field=doc["field"], sha256_before=_sha(doc["text"]), sha256_after=_sha(plan["after"]),
                 before=doc["text"], after=plan["after"])
        manifest.append(e)
        lines += ["", "### %s %s" % (doc["loc"], doc["field"]), doc["url"], "", "```diff"]
        lines += list(difflib.unified_diff(doc["text"].splitlines(), plan["after"].splitlines(),
                                           "before", "after", lineterm=""))
        lines.append("```")
    lines += ["", "## Manual (%d): edit these by hand, or ask their author" % len(manual)]
    for doc, reasons in manual:
        lines.append("- %s %s: %s -- %s" % (doc["loc"], doc["field"], ", ".join(reasons), doc["url"]))
    lines += ["", "## History to purge in the web UI (%d)" % len(history)]
    for h in history:
        lines.append("- %s -- %s" % (h["line"], h["url"]))
    added = sum(1 for doc, _p in edits if _surface(doc) != "release")
    lines += ["", "## Revisions the apply will add (%d)" % added,
              "Each edit above except a release keeps its before-text in a new revision: purge those",
              "too, after the apply, then scan twice.", "", "## Truncated (%d)" % len(truncated)]
    lines += ["- " + t for t in truncated]
    lines += ["", "# manifest", json.dumps({"schema": SCHEMA, "repo": repo, "generated_at": now,
                                              "replacement": replacement, "edits": manifest},
                                             sort_keys=True)]
    return "\n".join(lines) + "\n"


def cmd_rewrite(args, ctx):
    if args.apply:
        return cmd_apply(args, ctx)
    if args.from_file:
        raise Refused("bad-arguments", "--from is only for --apply")
    repo = _repo(args)
    if not args.out:
        raise Refused("out-required", "the dry-run needs --out FILE, outside every repository")
    ctx.patterns = load_patterns(args.patterns, ctx)
    out = pathlib.Path(os.path.expanduser(args.out))
    _refuse_inside_work_tree(out, "the --out file", ctx)
    if os.path.lexists(str(out)):
        raise Refused("out-exists", "the --out file exists; a dry-run never overwrites")
    if not out.parent.is_dir():
        raise Refused("out-unwritable", "the --out file's directory does not exist")
    _consumers()
    if args.replacement is not None:
        _check_replacement(args.replacement, ctx.patterns)
    found = collect(ctx, repo, args.max_pages)
    edits, manual, by_pattern, reasons_count, removed = [], [], Counter(), Counter(), 0
    for doc in found["docs"]:
        hits = find_hits(doc["text"], ctx.patterns)
        if not hits:
            continue
        for _line, pnum, _span in _dedupe(hits):
            by_pattern[pnum] += 1
        plan = plan_edit(doc["text"], ctx.patterns, args.replacement)
        reasons = []
        if doc["field"] == "title":
            reasons.append("title")                  # a rename keeps the old title, publicly
        if not doc["owned"]:
            reasons.append("not-owned")
        reasons += plan["reasons"]
        if reasons:
            manual.append((doc, reasons))
            reasons_count.update(reasons)
        else:
            edits.append((doc, plan))
            removed += plan["edges_removed"]
    for h in found["history"]:
        by_pattern[h["pattern"]] += 1
    revisions = sum(1 for h in found["history"] if h["kind"] == "revision")
    renames = len(found["history"]) - revisions
    if not (edits or manual or found["history"] or found["truncated"]):
        _emit(["rewrite: nothing matched; no file written"], ctx.patterns)
        return 0
    with _open_out(out) as f:
        f.write(_dry_run_text(repo, edits, manual, found["history"], found["truncated"], args.replacement))
    by_surface = Counter(_surface(doc) for doc, _p in edits)
    added = sum(1 for doc, _p in edits if _surface(doc) != "release")
    _emit(["rewrite: edits %d%s" % (len(edits), _parts(by_surface)),
           "rewrite: manual %d%s" % (len(manual), _parts(reasons_count)),
           "rewrite: history revisions to purge %d; revisions the apply will add %d" % (revisions, added),
           "rewrite: title renames that cannot be purged %d" % renames,
           "rewrite: truncated %d" % len(found["truncated"]),
           "rewrite: blocker edges removed %d" % removed,
           "rewrite: hits by pattern line " + (", ".join("%d=%d" % (k, by_pattern[k])
                                                          for k in sorted(by_pattern)) or "none"),
           "rewrite: dry-run file %s (mode 0600); review it, then apply it with --apply --from"
           % _tilde(out)], ctx.patterns)
    return 1


def _parts(counter):
    return (" (%s)" % ", ".join("%s %d" % (k, counter[k]) for k in sorted(counter))) if counter else ""


# ------------------------------------------------------------------------------ apply

def _read_manifest(path, repo):
    try:
        text = pathlib.Path(os.path.expanduser(path)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise Refused("manifest-unreadable", "cannot read the --from file")
    lines = text.splitlines()
    marks = [i for i, line in enumerate(lines) if line == "# manifest"]
    rest = [line for line in lines[marks[-1] + 1:] if line.strip()] if marks else []
    if len(rest) != 1:
        raise Refused("manifest-unreadable", "no single manifest line (an interrupted dry-run?); "
                      "run a new dry-run")
    try:
        manifest = json.loads(rest[0])
    except ValueError:
        raise Refused("manifest-unreadable", "the manifest line is not JSON")
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA \
            or not isinstance(manifest.get("edits"), list):
        raise Refused("manifest-schema", "not a %s manifest" % SCHEMA)
    if manifest.get("repo") != repo:
        raise Refused("manifest-repo", "the manifest was made for another repository")
    return manifest


def _label(e):
    surface, n, i = e["surface"], e.get("number"), e["id"]
    if surface in ("issue", "pr"):
        return "%s %d %s" % (surface, n, e["field"])
    if surface == "review":
        return "pr %d review %d %s" % (n, i, e["field"])
    if surface == "release":
        return "release %d %s" % (i, e["field"])
    return ("item %d %s %d" % (n, surface, i)) if isinstance(n, int) else "%s %d" % (surface, i)


def _validate_edits(manifest, patterns):
    """A6: refuse a hand-edited manifest whole, before any `gh` call. Each edit's hashes must match
    its text, and re-planning its before-text must give exactly its after-text, passing (a)-(d)."""
    replacement = manifest.get("replacement")
    if replacement is not None:
        if not isinstance(replacement, str):
            raise Refused("manifest-schema", "the replacement is not text")
        _check_replacement(replacement, patterns)
    for k, e in enumerate(manifest["edits"], 1):
        ok = isinstance(e, dict) and e.get("surface") in _FIELDS \
            and e.get("field") in _FIELDS[e.get("surface")] and isinstance(e.get("id"), int) \
            and all(isinstance(e.get(key), str) for key in ("before", "after", "sha256_before", "sha256_after"))
        if ok and e["surface"] in ("issue", "pr", "review"):
            ok = isinstance(e.get("number"), int)
        if not ok:
            raise Refused("manifest-schema", "edit %d is not an editable surface and field" % k)
        if _sha(e["before"]) != e["sha256_before"] or _sha(e["after"]) != e["sha256_after"]:
            raise Refused("manifest-invariant", "edit %d: its hashes do not match its text" % k)
        plan = plan_edit(e["before"], patterns, replacement)
        if plan["after"] != e["after"] or plan["reasons"]:
            raise Refused("manifest-invariant", "edit %d: not what the dry-run plans for its text "
                          "(hand-edited?); run a new dry-run" % k)


#: `gh api`'s stderr for a 404 or 410: the item was deleted (or is no longer visible).
_GONE_RE = re.compile(r"\(HTTP 4(?:04|10)\)")


def _get(ctx, endpoint, gone_ok=False):
    """The live object, or None when `gone_ok` and the read says 404 or 410."""
    rc, out, err = ctx.gh(["api", endpoint])
    if rc != 0:
        if gone_ok and _GONE_RE.search(err or ""):
            return None
        _gh_fail(ctx, endpoint, rc, err)
    try:
        obj = json.loads(out)
    except ValueError:
        raise Refused("gh-failed", "%s: unparsable JSON" % endpoint)
    if not isinstance(obj, dict):
        raise Refused("bad-shape", "%s: not an object" % endpoint)
    return obj


def _head(out):
    """(status, headers) from `gh api -i` output."""
    lines = (out or "").splitlines()
    m = re.match(r"HTTP/\S+\s+(\d{3})", lines[0]) if lines else None
    headers = {}
    if m:
        for line in lines[1:]:
            if not line.strip():
                break
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    return (int(m.group(1)) if m else None), headers


def _write(ctx, method, endpoint, payload):
    """One write, the text on stdin; a rate limit is retried after Retry-After, a bounded number of
    times; any other failure refuses at once."""
    body, retries = json.dumps(payload), 0
    while True:
        rc, out, err = ctx.gh(["api", "-i", "-X", method, endpoint, "--input", "-"], body)
        if rc == 0:
            return
        status, headers = _head(out)
        text = ((out or "") + "\n" + (err or "")).lower()
        limited = "retry-after" in headers or headers.get("x-ratelimit-remaining") == "0" \
            or any(marker in text for marker in _RATE_MARKERS)
        if status in (403, 429) and limited:
            if retries >= RETRY_MAX:
                raise Refused("rate-limited", "%s %s: still rate limited after %d retries; re-run later"
                              % (method, endpoint, RETRY_MAX))
            retries += 1
            try:
                wait = int(headers.get("retry-after", ""))
            except ValueError:
                wait = RETRY_DEFAULT
            ctx.sleep(max(1, min(wait, RETRY_CAP)))
            continue
        _gh_fail(ctx, "%s %s" % (method, endpoint), rc, err)


def cmd_apply(args, ctx):
    if not args.from_file:
        raise Refused("apply-needs-from", "--apply needs --from FILE, a dry-run file to apply")
    repo = _repo(args)
    ctx.patterns = load_patterns(args.patterns, ctx)
    manifest = _read_manifest(args.from_file, repo)
    _validate_edits(manifest, ctx.patterns)
    edits = manifest["edits"]
    lines, results, writes, remaining = [], Counter(), 0, 0
    for k, e in enumerate(edits):
        path, method = _ENDPOINTS[e["surface"]]
        endpoint = "repos/%s/%s" % (repo, path.format(number=e.get("number"), id=e["id"]))
        label = _label(e)
        current = _get(ctx, endpoint, gone_ok=True)
        if current is None:
            result = "deleted since the dry-run"
        elif e["surface"] != "release" and current.get("author_association") not in OWNED:
            result = "not-owned"
        elif _sha(current.get(e["field"])) == e["sha256_after"]:
            result = "already applied"
        elif _sha(current.get(e["field"])) != e["sha256_before"]:
            result = "changed since the dry-run"
        elif writes >= args.max_writes:
            remaining = len(edits) - k
            break
        else:
            if writes:
                ctx.sleep(WRITE_PACE)
            _write(ctx, method, endpoint, {e["field"]: e["after"]})
            writes += 1
            ctx.progress("apply %s: written" % label)
            check = _get(ctx, endpoint)
            result = "verified" if _sha(check.get(e["field"])) == e["sha256_after"] else "verify mismatch"
        results[result] += 1
        lines.append("apply %s: %s" % (label, result))
    lines.append("apply: " + ", ".join("%s %d" % (r, results[r]) for r in (
        "verified", "already applied", "changed since the dry-run", "deleted since the dry-run",
        "not-owned", "verify mismatch")))
    if remaining:
        lines.append("apply: stopped at --max-writes %d; %d edit(s) remain: run it again"
                     % (args.max_writes, remaining))
    if results["verified"]:
        lines.append("apply: each verified edit left its before-text in a new revision: purge those "
                     "in the web UI, then scan twice")
    _emit(lines, ctx.patterns)
    bad = results["changed since the dry-run"] + results["deleted since the dry-run"] \
        + results["not-owned"] + results["verify mismatch"]
    return 1 if (bad or remaining) else 0


# ------------------------------------------------------------------------------ main

def _repo(args):
    if not args.repo or not _REPO_RE.match(args.repo):
        raise Refused("bad-repo", "--repo OWNER/NAME is required; the tool never infers it")
    return args.repo


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--patterns", metavar="FILE",
                        help="the patterns file (else $%s; there is no default)" % ENV_PATTERNS)
    common.add_argument("--timeout", type=float, default=60.0, metavar="S",
                        help="seconds any one gh or git call may take (default 60)")
    github = argparse.ArgumentParser(add_help=False)
    github.add_argument("--repo", metavar="OWNER/NAME", help="the repository (required)")
    github.add_argument("--max-pages", type=int, default=1000, metavar="N",
                        help="refuse past N REST pages per list (default 1000)")
    ap = argparse.ArgumentParser(prog="leak_refs.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", metavar="scan|tree|text|rewrite", required=True)
    sub.add_parser("scan", parents=[common, github], help="read every GitHub surface, report hits")
    tree = sub.add_parser("tree", parents=[common], help="this checkout's files and commit messages")
    tree.add_argument("--root", metavar="DIR", help="a repository top level (default: this one)")
    text = sub.add_parser("text", parents=[common], help="one file, or - for stdin")
    text.add_argument("input", metavar="FILE|-")
    rewrite = sub.add_parser("rewrite", parents=[common, github],
                             help="the dry-run to --out, or --apply --from a dry-run file")
    rewrite.add_argument("--out", metavar="FILE", help="the dry-run file (outside every repository)")
    rewrite.add_argument("--replacement", metavar="TEXT", help="the neutral phrase (default: fixed)")
    rewrite.add_argument("--apply", action="store_true", help="apply a dry-run file")
    rewrite.add_argument("--from", dest="from_file", metavar="FILE", help="the dry-run file to apply")
    rewrite.add_argument("--max-writes", type=int, default=400, metavar="N",
                         help="stop after N writes per run (default 400)")
    return ap


_VERBS = {"scan": cmd_scan, "tree": cmd_tree, "text": cmd_text, "rewrite": cmd_rewrite}


def main(argv, run=None, sleep=time.sleep):
    ctx = Ctx(run or _real_run, sleep)
    try:
        try:
            args = build_parser().parse_args(argv[1:])
        except SystemExit as stop:
            return stop.code if isinstance(stop.code, int) else 2
        ctx.timeout = args.timeout
        return _VERBS[args.verb](args, ctx)
    except Refused as refusal:
        print(_safe("leak_refs: REFUSED [%s] %s" % (refusal.code, refusal.detail), ctx.patterns),
              file=sys.stderr)
        return 2
    except Exception as exc:                  # never a traceback, never an exception's message
        print("leak_refs: REFUSED [internal] %s" % type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""usage: reuse.py check-research <sdlc_dir> <goal> [--at-research]

This script reads files and writes none. `check-research` checks the `## Reuse` section of a goal's research
dossier (`.sdlc/research/<goal>.md`, else the copy on the goal's branch): the `**Searched at:**` commit, the
recorded queries, and the RU table or its `none: <reason>` line. Exit 2 on any error, 0 otherwise. A dossier
with no section, or a section with neither a `**Source:**` nor a `**Searched at:**` line, is legacy: a warning,
never a refusal. Without `--at-research` a Searched-at commit missing from this machine is a warning too.

Ceilings: the 1 MiB cap bounds parsing, not reading (the dossier lookup reads the whole file first). A numeric
goal with no `<goal>.md` reads the newest `<goal>-*.md` (review_context's lookup, unchanged here), so a sibling
such as `<goal>-controls.md` can make a goal look legacy. Each file evidence row costs one path resolve and one
stat. Resolving costs the square of a path's depth, so evidence over 4,096 characters is no file and is refused
before it resolves: a 1 MiB dossier of rows at that bound takes 0.9 s, where one 1 MiB evidence path used to take
685.6 s (both measured on one Apple M4 Pro, Python 3.12). The command line prints the first 50 errors and a count
of the rest; check_research returns every one.
"""
import importlib.util
import os
import pathlib
import re
import subprocess
import sys

USAGE = __doc__.split("\n\n", 1)[0]
MAX_CHARS = 1048576
GIT_TIMEOUT = 10
MAX_QUERIES = 25
MAX_PATTERN = 200
#: Linux's PATH_MAX, above macOS's 1024: neither can stat a longer path as written, so it names no honest evidence.
MAX_EVIDENCE_PATH = 4096
MAX_SHOWN_ERRORS = 50
#: The pattern IS the trust boundary (FR-57): a dossier can arrive with a clone, so only full hex reaches git.
SEARCHED_AT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
HEADER = "| ID | Capability | Decision | Evidence | Reason |"
REGISTRIES = ("pypi", "npm", "crates", "gem", "go", "maven", "nuget", "other")
#: decision -> (what its evidence must be, what its reason must be or None). Refusals are worded from this.
DECISIONS = {
    "reuse": ("file:line or file:line-line of a file inside the project", None),
    "extend": ("file:line or file:line-line of a file inside the project", None),
    "stdlib": ("a module name (letters, digits, dots and underscores)", None),
    "dependency": ("file:line of a manifest or lockfile inside the project", None),
    "dependency-new": ("<registry>:<name>==<version>, registry one of " + " ".join(REGISTRIES),
                       "'verified:' then three words or more"),
    "new": ("the dash \u2014 or -", "three words or more"),
}
LEGACY_NO_DOSSIER = "legacy goal (no research dossier); nothing checked"
LEGACY_NO_SECTION = "legacy goal (the research dossier has no ## Reuse section); nothing checked"
LEGACY_OLD_SECTION = "legacy goal (## Reuse was not written from reuse.py brief); nothing checked"
_FENCE = re.compile(r"\s*(`{3,}|~{3,})")
_QUERY = re.compile(r"- pattern: `([^`]*)` paths: `([^`]*)`")
#: C0, DEL and C1 (U+0080-U+009F, which a terminal can read as escape sequences, U+009B being CSI).
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SEPARATOR = re.compile(r"\|(?:\s*:?-+:?\s*\|)+")
#: A cell ends at a pipe that no backslash escapes; the escaped pipe stays in the cell as written.
_CELL = re.compile(r"(?<!\\)\|")
_FIELDS = ("id", "capability", "decision", "evidence", "reason")
_ID = re.compile(r"RU-[0-9]+")
_FILE_LINE = re.compile(r"(.+):([0-9]+)(?:-([0-9]+))?")
_MODULE = re.compile(r"[A-Za-z0-9_.]+")
_PINNED = re.compile(r"(?:%s):[^\s=]+==\S+" % "|".join(REGISTRIES))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dossier_text(sdlc_dir, goal):
    """The goal's research dossier as the reviewer brief reads it: the file on disk, else the copy on the
    goal's branch, else "". One lookup, so this check and the brief never disagree about whether it exists."""
    rc = _load("review_context")
    return rc._phase_doc(sdlc_dir, goal, "research") or rc._branch_doc(sdlc_dir, goal, "research")[1]


def _section_lines(text, heading):
    """The lines of the first section whose heading line is exactly `## <heading>`, up to the next `## `
    line, or None when there is none. Fenced lines never count, the heading included: a fence opens on
    ``` or ~~~ and closes only on the same character, `acceptance._section`'s rule (that function itself
    returns checklist items only and raises on any other line, so it cannot read this section)."""
    fenced, lines = None, None
    for line in text.splitlines():
        fence = _FENCE.match(line)
        if fence:
            char = fence.group(1)[0]
            fenced = None if fenced == char else char if fenced is None else fenced
            continue
        if fenced:
            continue
        if line.startswith("## "):
            if lines is not None:
                break
            if line == "## " + heading:
                lines = []
            continue
        if lines is not None:
            lines.append(line)
    return lines


def parse_reuse(text):
    """The `## Reuse` section as data; it reports no errors. `queries` holds only well-formed query lines;
    `bad_queries` the 1-based positions, among the section's `- ` lines, of the others. Each row carries
    the five named cells ("" when missing) and `cells`, how many it had."""
    lines = _section_lines(text, "Reuse")
    parsed = {"present": lines is not None, "source": None, "searched_at": None, "none": None,
              "queries": [], "bad_queries": [], "header": None, "separator": False, "rows": []}
    table, position = [], 0
    for line in lines or ():
        line = line.strip()
        if line.startswith("**Source:**"):
            parsed["source"] = line[len("**Source:**"):].strip()
        elif line.startswith("**Searched at:**"):
            parsed["searched_at"] = line[len("**Searched at:**"):].strip()
        elif line.startswith("none:"):
            parsed["none"] = line[len("none:"):].strip()
        elif line.startswith("- "):
            position += 1
            query = _QUERY.fullmatch(line)
            if query:
                parsed["queries"].append((query.group(1), query.group(2).split(" ")))
            else:
                parsed["bad_queries"].append(position)
        elif line.startswith("|"):
            table.append(line)
    if table:
        parsed["header"] = table[0]
        parsed["separator"] = len(table) > 1 and bool(_SEPARATOR.fullmatch(table[1]))
        for line in table[1 + parsed["separator"]:]:
            cells = [cell.strip() for cell in _CELL.split(line)[1:-1]]
            parsed["rows"].append(dict(zip(_FIELDS, cells + [""] * len(_FIELDS)), cells=len(cells)))
    return parsed


def is_new_format(text):
    """True when the dossier's `## Reuse` section carries a `**Source:**` or `**Searched at:**` line, which
    only `reuse.py brief` tells an agent to write. A bare heading is a legacy dossier and is never refused."""
    parsed = parse_reuse(text)
    return parsed["present"] and (parsed["source"] is not None or parsed["searched_at"] is not None)


def _path_error(path):
    """Why a recorded path is unsafe to hand to git or the filesystem, or None. A leading `:` is git pathspec
    magic, a leading `-` an option, and `/`, `~` or a `..` segment leave the project."""
    if not path:
        return "is empty"
    if _CONTROL.search(path):
        return "contains a control character"
    if path[0] in "/~-:":
        return "starts with %r" % path[0]
    if ".." in path.split("/"):
        return "has a '..' segment"
    return None


def _inside(root, rel):
    """True when `rel` names a regular file that resolves inside `root`: a symlink out of the project is
    outside it. An over-long name raises OSError and a symlink loop RuntimeError (Python 3.12); both read
    as "no such file", never as a traceback. The length bound comes first and the inside test is a string
    prefix, because resolving and `root in path.parents` each cost the square of the depth: at the bound,
    `in path.parents` made a 1 MiB dossier of rows take 3.9 s instead of 0.9 s (measured, review B2)."""
    if len(rel) > MAX_EVIDENCE_PATH:
        return False
    try:
        root = pathlib.Path(root).resolve()
        path = (root / rel).resolve()
        return str(path).startswith(os.path.join(root, "")) and path.is_file()
    except (OSError, RuntimeError):
        return False


def _evidence_ok(decision, evidence, root):
    if decision in ("reuse", "extend", "dependency"):
        found = _FILE_LINE.fullmatch(evidence)
        return bool(found and (decision != "dependency" or found.group(3) is None)
                    and not _path_error(found.group(1)) and _inside(root, found.group(1)))
    if decision == "stdlib":
        return bool(_MODULE.fullmatch(evidence))
    if decision == "dependency-new":
        return bool(_PINNED.fullmatch(evidence))
    return evidence in ("\u2014", "-")


def _reason_ok(decision, reason):
    if decision == "dependency-new":
        return reason.startswith("verified:") and len(reason[len("verified:"):].split()) >= 3
    if decision == "new":
        return len(reason.split()) >= 3
    return True


def table_errors(parsed, root):
    """The header, separator and row rules alone, [] when there is no table: the seam a plan's optional
    table is checked through, since it has no queries and no Searched-at line."""
    if parsed["header"] is None:
        return []
    errors = []
    if parsed["header"] != HEADER:
        errors.append("table: the header row must be exactly " + HEADER)
    if not parsed["separator"]:
        errors.append("table: the header row must be followed by a separator row")
    seen = set()
    for k, row in enumerate(parsed["rows"], 1):
        who = row["id"] if _ID.fullmatch(row["id"]) else "row %d" % k
        if who != row["id"]:
            errors.append("%s: id %r is not RU-<number>" % (who, row["id"]))
        if row["id"] in seen:
            errors.append("%s: id appears more than once" % who)
        seen.add(row["id"])
        if row["cells"] != len(_FIELDS):
            errors.append("%s: %d cells; a row has %d" % (who, row["cells"], len(_FIELDS)))
            continue                       # a shifted row would only add refusals of the wrong cells
        decision = row["decision"]
        if not row["capability"]:
            errors.append("%s: capability is empty" % who)
        if decision not in DECISIONS:
            errors.append("%s: decision %r is not one of %s" % (who, decision, " ".join(DECISIONS)))
            continue
        if not _evidence_ok(decision, row["evidence"], root):
            errors.append("%s: decision %r needs evidence: %s" % (who, decision, DECISIONS[decision][0]))
        if not _reason_ok(decision, row["reason"]):
            errors.append("%s: decision %r needs a reason of %s" % (who, decision, DECISIONS[decision][1]))
    return errors


def reuse_errors(parsed, root):
    """One string per broken rule, each starting with what it is about: `Searched at:`, `none:`, `queries:`,
    `query <n>:`, `table:`, a row id, or `row <k>` when the id itself is malformed."""
    errors = []
    if parsed["searched_at"] is None:
        errors.append("Searched at: missing; write **Searched at:** <the full commit id Research ran at>")
    elif not SEARCHED_AT.fullmatch(parsed["searched_at"]):
        errors.append("Searched at: not a full commit id (40 or 64 lowercase hexadecimal characters)")
    if parsed["none"] is not None:
        if len(parsed["none"].split()) < 3:
            errors.append("none: needs a reason of three words or more")
        if parsed["queries"] or parsed["bad_queries"] or parsed["header"] is not None:
            errors.append("none: replaces the queries and the table; keep one or the other")
        return errors + table_errors(parsed, root)
    count = len(parsed["queries"]) + len(parsed["bad_queries"])
    if not 1 <= count <= MAX_QUERIES:
        errors.append("queries: %d recorded; 1 to %d allowed" % (count, MAX_QUERIES))
    bad, good = set(parsed["bad_queries"]), iter(parsed["queries"])     # a set: one lookup per line, not a scan
    for n in range(1, count + 1):          # numbered by position, so a malformed line keeps its number
        if n in bad:
            errors.append("query %d: not of the form - pattern: `<regex>` paths: `<path> [<path> ...]`" % n)
            continue
        pattern, paths = next(good)
        if not 1 <= len(pattern) <= MAX_PATTERN:
            errors.append("query %d: pattern is %d characters; 1 to %d allowed" % (n, len(pattern), MAX_PATTERN))
        if _CONTROL.search(pattern):
            errors.append("query %d: pattern contains a control character" % n)
        errors += ["query %d: path %r %s" % (n, path, _path_error(path)) for path in paths if _path_error(path)]
    if not parsed["rows"]:
        errors.append("table: no rows; a goal that adds no capability writes none: <reason> instead")
    return errors + table_errors(parsed, root)


def _run_git(argv):
    """`argv` with the variables that would point git at another repository than `-C` names removed, and with
    lazy fetching off, so a partial clone answers from what it has instead of asking its remote."""
    location = _load("bounded_run").LOCATION_VARS
    env = {k: v for k, v in os.environ.items() if k not in location}
    env["GIT_NO_LAZY_FETCH"] = "1"
    return subprocess.run(argv, capture_output=True, timeout=GIT_TIMEOUT, env=env)


def commit_exists(root, commit):
    """True when the repository at `root` holds `commit`, False when it does not or git cannot run, None when
    `commit` is not a full commit id: then git is never called. Raises subprocess.TimeoutExpired when git does
    not answer in GIT_TIMEOUT seconds, which is "unknown", not "absent"."""
    if not (isinstance(commit, str) and SEARCHED_AT.fullmatch(commit)):
        return None
    try:
        proc = _run_git(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "--end-of-options",
                         commit + "^{commit}"])
    except OSError:
        return False
    return proc.returncode == 0


def _check(sdlc_dir, goal, at_research):
    """(errors, warnings, parsed); parsed is None when nothing was checked."""
    text = dossier_text(sdlc_dir, goal)
    if not text:
        return [], [LEGACY_NO_DOSSIER], None
    if len(text) > MAX_CHARS:
        return ["research dossier is over %d characters; nothing checked" % MAX_CHARS], [], None
    parsed = parse_reuse(text)
    if not parsed["present"]:
        return [], [LEGACY_NO_SECTION], None
    if not is_new_format(text):
        return [], [LEGACY_OLD_SECTION], None
    root = _load("review_tests")._root(sdlc_dir, goal)
    errors, warnings, commit = reuse_errors(parsed, root), [], parsed["searched_at"]
    try:
        problem = "is not on this machine" if commit_exists(root, commit) is False else None
    except subprocess.TimeoutExpired:
        problem = "could not be checked: git did not answer in %d seconds" % GIT_TIMEOUT
    if problem:
        # Research checks the commit it just ran at; elsewhere a goal researched on another machine is fine.
        (errors if at_research else warnings).append("Searched at: commit %s %s" % (commit[:7], problem))
    return errors, warnings, parsed


def check_research(sdlc_dir, goal, at_research=False):
    """(errors, warnings) for the goal's research dossier."""
    return _check(sdlc_dir, goal, at_research)[:2]


def main(argv):
    if argv in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    at_research = argv[-1:] == ["--at-research"]
    args = argv[:-1] if at_research else argv
    if len(args) != 3 or args[0] != "check-research":
        print(USAGE, file=sys.stderr)
        return 2
    errors, warnings, parsed = _check(args[1], args[2], at_research)
    for line in warnings + errors[:MAX_SHOWN_ERRORS]:
        print("reuse.py: " + line, file=sys.stderr)
    if len(errors) > MAX_SHOWN_ERRORS:
        print("reuse.py: \u2026 and %d more errors" % (len(errors) - MAX_SHOWN_ERRORS), file=sys.stderr)
    if errors:
        return 2
    if parsed is not None:
        print("reuse.py: ## Reuse for goal %s is well formed (%d rows, %d queries)"
              % (args[2], len(parsed["rows"]), len(parsed["queries"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

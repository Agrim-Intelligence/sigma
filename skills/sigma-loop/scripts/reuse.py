#!/usr/bin/env python3
"""usage: reuse.py check-research <sdlc_dir> <goal> [--at-research]
       reuse.py source <sdlc_dir>
       reuse.py rerun <sdlc_dir> <goal> [--ref R | --new-since-research] [--base B]

This script writes no files and starts no background process: it reads files and runs short git and Python
subprocesses, each bounded by a timeout. `check-research` checks the `## Reuse` section of a goal's research
dossier (`.sdlc/research/<goal>.md`, else the copy on the goal's branch): the `**Searched at:**` commit, the
recorded queries, and the RU table or its `none: <reason>` line. Exit 2 on any error, 0 otherwise. A dossier
with no section, or a section with neither a `**Source:**` nor a `**Searched at:**` line, is legacy: a warning,
never a refusal. Without `--at-research` a Searched-at commit missing from this machine is a warning too.

`source` prints one JSON line (`mode`, `graph_commit`, `reason`, `delta_files`) saying whether the reuse search
uses the knowledge graph or `git grep`. The graph is usable only when `kg.py freshness` (run as a subprocess of
`sys.executable`, never imported) says it is enabled, built, holds code, has a build commit that is an ancestor of
HEAD, and is at most MAX_DELTA_FILES files behind; the first failing condition is the reason. A helper that is
missing, times out, exits non-zero or prints anything but a JSON object also gives grep. With `--at-research`,
`check-research` also checks the dossier's `**Source:**` line: a `graph@<commit>` must be an ancestor of the
searched-at commit (never of HEAD), and the line must agree with the live `source()`. The form of the line is
checked on every run. Limit: `source()` asks about the repository at the parent of `sdlc_dir`, the ancestor rule
about the goal's worktree; they differ when Research runs against the root checkout's `.sdlc`.
Cost: one extra Python subprocess per `check-research --at-research` (0.043 s measured for `kg.py freshness` on
one repository with the graph disabled; the graph path has not been timed) and one `git diff` bounded by the
500-file gate.

Ceilings: the 1 MiB cap bounds parsing, not reading (the dossier lookup reads the whole file first). A numeric
goal with no `<goal>.md` reads the newest `<goal>-*.md` (review_context's lookup, unchanged here), so a sibling
such as `<goal>-controls.md` can make a goal look legacy. Each file evidence row costs one path resolve and one
stat. Resolving costs the square of a path's depth, so evidence over 4,096 characters is no file and is refused
before it resolves: a 1 MiB dossier of rows at that bound takes 0.9 s, where one 1 MiB evidence path used to take
685.6 s (both measured on one Apple M4 Pro, Python 3.12). The command line prints the first 50 errors and a count
of the rest; check_research returns every one.

A new-format dossier must also carry a `**Risk**:` line above its first `## ` heading (none, or any of sensitive,
contract, migration) and a `## Test design` table with one row per criterion id `review_tests.criteria` reads from
the goal's acceptance record; a legacy dossier is not checked for either. With no valid record the check refuses
when `verify.enforce` is on and otherwise warns and skips the one-row-per-criterion rule. A config.json that exists
but cannot be read counts as on (fail closed); a missing one counts as off, so a goal worktree's `.sdlc`, which has
no config.json (it is gitignored), always warns, and `load_config` calls a non-object config.json missing too.
Cost (Apple M4 Pro, Python 3.12, measured): the two parsers and the rules take at most 0.47 s on any 1 MiB shape
tried, the worst being 1 MiB of `|` rows (1,048,544 errors), which peaks at 244 MB of Python allocations
(tracemalloc) and 336 MB resident for the whole command; each error string is kept until the command prints.

`rerun` runs the goal's recorded queries again with `git grep` at a commit, never a shell and never the working
tree, and prints `Q<i> <file>:<line>: <text>` per hit. With no `--ref` it searches the tip of the remote-tracking
base ref (the work record's remote and base, else `work.base`, else the remote's default branch as the local clone
knows it, `--base` overriding), never HEAD. `--new-since-research` prints only the hits at that tip that were not
at the dossier's `**Searched at:**` commit, compared by file and text, never by line number, and names both
commits and how long ago the remote refs were fetched (the newer of the two FETCH_HEAD files a fetch can write; a
linked worktree writes its own). It never fetches, never reads HEAD, never writes and never calls `merge-base`.
Every ref is resolved to a commit id first (`rev-parse --verify --end-of-options <ref>^{commit}`) and an
unresolvable one is refused in one line. It exits 0 for every informational case (no queries, no base, no
Searched-at, a ref that does not resolve, hits) and 2 only for a usage error.
Ceilings: each query is two `git grep`s at most (one per commit), 25 queries at most, 30 seconds per query and 120
seconds for the whole run (the time left also bounds a running query, so the run stops at 120 seconds, never later),
200 hits per query, 1,000 output lines and 300 characters per line, and 8 MiB read per `git grep` (it is killed
past that): the caps are judgements, not measured, and `git grep` cost at a commit on a large repository is
unmeasured, so at 10x or 100x the 120 seconds is the ceiling and the run says so by stopping.
"""
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time

USAGE = __doc__.split("\n\n", 1)[0]
MAX_CHARS = 1048576
GIT_TIMEOUT = 10
MAX_QUERIES = 25
QUERY_TIMEOUT = 30
MAX_SECONDS = 120
MAX_HITS_PER_QUERY = 200
MAX_OUTPUT_LINES = 1000
MAX_LINE_CHARS = 300
MAX_GREP_BYTES = 8 * 1024 * 1024
MAX_PATTERN = 200
#: Linux's PATH_MAX, above macOS's 1024: neither can stat a longer path as written, so it names no honest evidence.
MAX_EVIDENCE_PATH = 4096
MAX_SHOWN_ERRORS = 50
#: A judgement, not a measurement: past this many files changed since the build the graph is treated as too stale
#: to trust, and the pull request for #2280 records the timing that justifies or changes it.
MAX_DELTA_FILES = 500
KG_TIMEOUT = 60
_KG = pathlib.Path(__file__).resolve().parent.parent.parent / "sigma-kg" / "scripts" / "kg.py"
#: The pattern IS the trust boundary (FR-57): a dossier can arrive with a clone, so only full hex reaches git.
SEARCHED_AT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
#: The dossier's `**Source:**` line: the graph at a commit prefix, or grep with an optional reason.
SOURCE_FORM = re.compile(r"graph@[0-9a-f]{7,64}|grep(?: \(.+\))?")
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
RISKS = ("none", "sensitive", "contract", "migration")
TEST_DESIGN_HEADER = "| Criterion | Behaviour | Failure path | Hostile input | Helpers |"
_DESIGN_FIELDS = ("criterion", "behaviour", "failure", "hostile", "helpers")
#: Every id `review_tests.criteria` can return has this shape, so a row naming one is named by it.
_CRITERION = re.compile(r"AC-[0-9]+")
_DASHES = ("\u2014", "-")


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
    line, or None when there is none; with `heading` None, the lines above the first `## ` line (the
    dossier's header). Fenced lines never count, the heading included: a fence opens on
    ``` or ~~~ and closes only on the same character, `acceptance._section`'s rule (that function itself
    returns checklist items only and raises on any other line, so it cannot read this section)."""
    fenced, lines = None, ([] if heading is None else None)
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
    parsed["header"], parsed["separator"], parsed["rows"] = _table(table, _FIELDS)
    return parsed


def _table(lines, fields):
    """(header, separator, rows) of a table's `|` lines: the first line is the header, a separator row may
    follow, and each other line is a row of `fields` ("" when missing) plus `cells`, how many it had."""
    if not lines:
        return None, False, []
    separator = len(lines) > 1 and bool(_SEPARATOR.fullmatch(lines[1]))
    rows = []
    for line in lines[1 + separator:]:
        cells = [cell.strip() for cell in _CELL.split(line)[1:-1]]
        rows.append(dict(zip(fields, cells + [""] * len(fields)), cells=len(cells)))
    return lines[0], separator, rows


def parse_risk(text):
    """The values of every `**Risk**:` line above the first `## ` heading, in order, each stripped, or None
    when there is none. Every such line counts, so a second line cannot hide a value behind the first."""
    values = None
    for line in _section_lines(text, None):
        line = line.strip()
        if line.startswith("**Risk**:"):
            if values is None:
                values = []
            values += [value.strip() for value in line[len("**Risk**:"):].split(",")]
    return values


def parse_test_design(text):
    """The `## Test design` section as data; it reports no errors. Only its stripped `|` lines are the table;
    each row carries the five named cells and `cells`, as `parse_reuse` does."""
    lines = _section_lines(text, "Test design")
    table = [line.strip() for line in lines or () if line.strip().startswith("|")]
    header, separator, rows = _table(table, _DESIGN_FIELDS)
    return {"present": lines is not None, "header": header, "separator": separator, "rows": rows}


def _is_case(cell):
    """A cell that names a case: not empty, not a dash, not n/a, and not a `none:` line (any case)."""
    cell = cell.lower()
    return cell not in ("", "n/a") + _DASHES and not cell.startswith("none:")


def test_design_errors(parsed, risk, criterion_ids):
    """One string per broken rule, each starting with `Risk:`, `Test design:`, a criterion id, or `row <k>` when
    the row's criterion is not `AC-<number>`. `criterion_ids` None means unknown: no coverage rule runs."""
    errors = []
    if risk is None:
        errors.append("Risk: missing; write **Risk**: none, or any of sensitive, contract, migration, above the "
                      "first ## heading")
    else:
        bad = [value for value in risk if value not in RISKS]
        if bad:
            errors.append("Risk: %d value(s) not one of %s; the first is %r" % (len(bad), " ".join(RISKS), bad[0][:40]))
        if "none" in risk and len(risk) > 1:
            errors.append("Risk: none cannot be combined with another value")
    if not parsed["present"]:
        return errors + ["Test design: missing; write ## Test design with one row per acceptance criterion"]
    if parsed["header"] != TEST_DESIGN_HEADER:
        errors.append("Test design: the header row must be exactly " + TEST_DESIGN_HEADER)
    elif not parsed["separator"]:
        errors.append("Test design: the header row must be followed by a separator row")
    seen, hostile = set(), False
    for k, row in enumerate(parsed["rows"], 1):
        criterion = row["criterion"]
        who = criterion if _CRITERION.fullmatch(criterion) else "row %d" % k
        if criterion_ids is not None:
            if criterion not in criterion_ids:
                errors.append("%s: %r is not one of this goal's criterion ids" % (who, criterion))
            elif criterion in seen:
                errors.append("%s: more than one row for this criterion" % who)
            seen.add(criterion)            # before the cell count: a shifted row is one error, not a second "no row"
        if row["cells"] != len(_DESIGN_FIELDS):
            errors.append("%s: %d cells; a row has %d" % (who, row["cells"], len(_DESIGN_FIELDS)))
            continue                       # its cells are not known to be the ones they would be judged as
        if row["behaviour"] in ("",) + _DASHES:
            errors.append("%s: behaviour is empty or a dash" % who)
        failure = row["failure"]
        if failure.lower().startswith("none:"):
            if len(failure[len("none:"):].split()) < 3:
                errors.append("%s: failure path none: needs a reason of three words or more" % who)
        elif not _is_case(failure):
            errors.append("%s: failure path %r is not a case; write the case, or none: and a reason of three words "
                          "or more" % (who, failure))
        hostile = hostile or _is_case(row["hostile"])
    if criterion_ids is not None:
        errors += ["%s: no row in ## Test design" % criterion for criterion in criterion_ids if criterion not in seen]
    if risk is not None and "sensitive" in risk and not hostile:
        errors.append("Risk: sensitive needs a ## Test design row whose Hostile input is a case")
    return errors


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
    return evidence in _DASHES


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
    """One string per broken rule, each starting with what it is about: `Searched at:`, `Source:`, `none:`, `queries:`,
    `query <n>:`, `table:`, a row id, or `row <k>` when the id itself is malformed."""
    errors = []
    if parsed["searched_at"] is None:
        errors.append("Searched at: missing; write **Searched at:** <the full commit id Research ran at>")
    elif not SEARCHED_AT.fullmatch(parsed["searched_at"]):
        errors.append("Searched at: not a full commit id (40 or 64 lowercase hexadecimal characters)")
    if not parsed["source"]:
        errors.append("Source: missing; write **Source:** graph@<commit> or grep (<why>)")
    elif not SOURCE_FORM.fullmatch(parsed["source"]):
        errors.append("Source: not graph@<7 to 64 hexadecimal characters> or grep, optionally followed by (<text>)")
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


def _git_env():
    """The environment git runs in: the variables that would point it at another repository than `-C` names
    removed, and lazy fetching off, so a partial clone answers from what it has instead of asking its remote."""
    location = _load("bounded_run").LOCATION_VARS
    env = {k: v for k, v in os.environ.items() if k not in location}
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def _run_git(argv, timeout=GIT_TIMEOUT):
    return subprocess.run(argv, capture_output=True, timeout=timeout, env=_git_env())


def _run_bounded(argv, timeout, max_bytes=None):
    """Run `argv` like `_run_git` but keep at most `max_bytes` (default MAX_GREP_BYTES) + 1 bytes of stdout in
    memory: a reader thread stops there and the process is killed, so a flood costs the cap, not its size.
    Raises subprocess.TimeoutExpired after `timeout` seconds, the process killed first. `subprocess.run(
    capture_output=True)` would hold the whole output, which is why it is not used for `git grep`."""
    cap = (MAX_GREP_BYTES if max_bytes is None else max_bytes) + 1
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=err, env=_git_env())
        chunks = []

        def read():
            size = 0
            while size < cap:
                chunk = proc.stdout.read(min(65536, cap - size))
                if not chunk:
                    return
                chunks.append(chunk)
                size += len(chunk)
            proc.kill()                            # over the cap: nothing more is wanted

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            proc.kill()
            reader.join()
            proc.wait()
            proc.stdout.close()
            raise subprocess.TimeoutExpired(argv, timeout)
        try:
            proc.wait(timeout=max(1.0, timeout))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        err.seek(0)
        return subprocess.CompletedProcess(argv, proc.returncode, b"".join(chunks), err.read(4096))


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


def _run_text(argv):
    """`argv` as text, with `_run_git`'s environment, bounded by KG_TIMEOUT: the runner `source` uses for both
    the helper and `git diff`. `_run_git` is bytes and waits GIT_TIMEOUT, so it cannot serve here."""
    location = _load("bounded_run").LOCATION_VARS
    env = {k: v for k, v in os.environ.items() if k not in location}
    env["GIT_NO_LAZY_FETCH"] = "1"
    return subprocess.run(argv, capture_output=True, text=True, timeout=KG_TIMEOUT, env=env)


def _grep(reason):
    return {"mode": "grep", "graph_commit": None, "reason": reason, "delta_files": []}


def _number(value):
    return isinstance(value, int) and not isinstance(value, bool)


def source(sdlc_dir, run=None):
    """Where the reuse search looks: `{"mode", "graph_commit", "reason", "delta_files"}`. Mode `graph` only when
    `kg.py freshness` says the graph is usable (module docstring); the first failing condition is the reason.
    Every test is strict on type, so a missing key or null reads as not usable. In graph mode `delta_files` are
    the sorted files changed since the build, which the search must also cover. Never raises."""
    run = run or _run_text
    try:
        proc = run([sys.executable, str(_KG), "freshness", str(sdlc_dir)])
        if proc.returncode != 0:
            raise ValueError("helper exited %d" % proc.returncode)
        fresh = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError):
        return _grep("knowledge graph helper unavailable")
    if not isinstance(fresh, dict):
        return _grep("knowledge graph helper unavailable")
    commit = fresh.get("built_at_commit")
    changed = fresh.get("changed_files")
    for ok, reason in (
            (fresh.get("enabled") is True, "knowledge graph disabled"),
            (fresh.get("built") is True, "graph not built"),
            (fresh.get("holds_code") is True, "cannot confirm the graph holds code"),
            (isinstance(commit, str) and SEARCHED_AT.fullmatch(commit), "graph has no build commit"),
            (fresh.get("ancestor") is True, "graph commit is not in this history"),
            (_number(changed) and changed <= MAX_DELTA_FILES,
             "graph is %s files behind (limit %d)" % (changed if _number(changed) else "an unknown number of",
                                                      MAX_DELTA_FILES))):
        if not ok:
            return _grep(reason)
    root = pathlib.Path(sdlc_dir).resolve().parent
    try:
        diff = run(["git", "-C", str(root), "diff", "--name-only", "--end-of-options", commit + "..HEAD"])
        if diff.returncode != 0:
            raise ValueError("git diff exited %d" % diff.returncode)
    except (OSError, subprocess.SubprocessError, ValueError):
        return _grep("cannot list the files changed since the graph build")   # never graph with a hidden delta
    files = sorted({line for line in diff.stdout.splitlines() if line})
    return {"mode": "graph", "graph_commit": commit, "reason": "", "delta_files": files}


def source_line(result):
    """The canonical `**Source:**` text for a `source()` result."""
    if result["mode"] == "graph":
        return "graph@" + result["graph_commit"][:12]
    return "grep (%s)" % result["reason"]


def _ancestor_error(prefix, searched_at, root):
    """The error when graph commit `prefix` is not an ancestor of `searched_at` (never of HEAD), else None.
    The prefix is resolved first and the resolved id is hex-checked before it reaches merge-base."""
    refused = "Source: graph commit %s is not an ancestor of the searched-at commit" % prefix
    try:
        resolved = _run_git(["git", "-C", str(root), "rev-parse", "--verify", "--end-of-options", prefix + "^{commit}"])
        full = resolved.stdout.decode("ascii", "replace").strip()
        if resolved.returncode != 0 or not SEARCHED_AT.fullmatch(full):
            return refused
        done = _run_git(["git", "-C", str(root), "merge-base", "--is-ancestor", "--end-of-options", full,
                         searched_at])
    except OSError:
        return refused
    except subprocess.TimeoutExpired:
        return "Source: graph commit %s could not be checked: git did not answer in %d seconds" % (prefix, GIT_TIMEOUT)
    return None if done.returncode == 0 else refused      # 1 is no, 128 is an unknown commit: never success


def source_errors(parsed, sdlc_dir, root):
    """The `--at-research` rules for a Source line that already passed the form rule: the ancestor rule, then
    agreement with the live `source()` (called once)."""
    written = parsed["source"]
    errors = []
    if written.startswith("graph@") and SEARCHED_AT.fullmatch(parsed["searched_at"] or ""):
        problem = _ancestor_error(written[len("graph@"):], parsed["searched_at"], root)
        if problem:
            errors.append(problem)
    live = source(sdlc_dir)
    if written.startswith("graph@"):
        prefix = written[len("graph@"):]
        if live["mode"] == "grep":
            errors.append("Source: the dossier says graph but " + live["reason"])
        elif not live["graph_commit"].startswith(prefix):
            errors.append("Source: the dossier names graph@%s but the usable graph was built at %s"
                          % (prefix, live["graph_commit"][:12]))
    elif live["mode"] == "graph":
        errors.append("Source: the dossier says grep but the knowledge graph is usable (%s): search the graph, then "
                      "the %d changed files" % (source_line(live), len(live["delta_files"])))
    return errors


def _check(sdlc_dir, goal, at_research):
    """(errors, warnings, parsed, k): parsed is None when nothing was checked, and k is how many acceptance
    criteria the Test design rows were checked against (0 when there is no record)."""
    text = dossier_text(sdlc_dir, goal)
    if not text:
        return [], [LEGACY_NO_DOSSIER], None, 0
    if len(text) > MAX_CHARS:
        return ["research dossier is over %d characters; nothing checked" % MAX_CHARS], [], None, 0
    parsed = parse_reuse(text)
    if not parsed["present"]:
        return [], [LEGACY_NO_SECTION], None, 0
    if not is_new_format(text):
        return [], [LEGACY_OLD_SECTION], None, 0
    tests = _load("review_tests")
    root = tests._root(sdlc_dir, goal)
    errors, warnings, commit = reuse_errors(parsed, root), [], parsed["searched_at"]
    try:
        problem = "is not on this machine" if commit_exists(root, commit) is False else None
    except subprocess.TimeoutExpired:
        problem = "could not be checked: git did not answer in %d seconds" % GIT_TIMEOUT
    if problem:
        # Research checks the commit it just ran at; elsewhere a goal researched on another machine is fine.
        (errors if at_research else warnings).append("Searched at: commit %s %s" % (commit[:7], problem))
    if at_research and parsed["source"] and SOURCE_FORM.fullmatch(parsed["source"]):
        errors += source_errors(parsed, sdlc_dir, root)
    try:
        ids = dict.fromkeys(cid for cid, _ in tests.criteria(sdlc_dir, goal))      # ordered, O(1) lookup
    except (OSError, ValueError):
        ids, missing = None, "no recorded acceptance criteria for goal %s" % goal
        if _enforced(sdlc_dir):
            errors.append(missing + "; record them before Research")
        else:
            warnings.append(missing + "; one row per criterion was not checked")
    errors += test_design_errors(parse_test_design(text), parse_risk(text), ids)
    return errors, warnings, parsed, len(ids or ())


def _enforced(sdlc_dir):
    """`verify.enforce`, failing closed: no config.json at all is off (`phase_gate`'s rule), but a config that
    exists and cannot be read is on, since a corrupt config must never silently become the defaults."""
    state = _load("state")
    try:
        return state.enforce_enabled(state.load_config(sdlc_dir).get("verify"))
    except state.ConfigMissing:
        return False
    except Exception:                              # noqa: BLE001 - unreadable, invalid or a non-object verify
        return True


def check_research(sdlc_dir, goal, at_research=False):
    """(errors, warnings) for the goal's research dossier."""
    return _check(sdlc_dir, goal, at_research)[:2]


# ---------------------------------------------------------------------------------------------------- rerun
# New in #2281, and a block of its own so that `check-research` above stays byte-identical. Every function that
# runs git takes `run(argv, timeout=...)`, the one seam a test injects; the default is `_run_git`, or
# `_run_bounded` for `git grep`. A ref reaches any git command only as a full commit id from `resolve_commit`.

def _shown(text, limit=80):
    """`text` safe to print: control characters (a terminal reads U+009B as CSI) become `?`, cut to `limit`."""
    text = _CONTROL.sub("?", str(text))
    return text if len(text) <= limit else text[:limit] + "…"


def _decode(raw):
    return raw.decode("utf-8", "replace")


def resolve_commit(root, ref, run=None):
    """(commit, error): the full commit id `ref` names, or (None, why). A ref is untrusted text: one with a NUL or
    a newline never reaches git, and git reads it behind `--end-of-options`, so `--output=x` is a name, not an
    option. Only 40 or 64 hex characters come back. It never fetches."""
    refused = "ref %s does not resolve to a commit" % _shown(ref)
    if not isinstance(ref, str) or not ref or "\x00" in ref or "\n" in ref:
        return None, refused
    try:
        proc = (run or _run_git)(["git", "-C", str(root), "rev-parse", "--verify", "--end-of-options",
                                  ref + "^{commit}"], timeout=GIT_TIMEOUT)
    except OSError:
        return None, "git could not run"
    except subprocess.TimeoutExpired:
        return None, "git did not answer in %d seconds" % GIT_TIMEOUT
    out = _decode(proc.stdout).strip() if proc.returncode == 0 else ""
    return (out, None) if SEARCHED_AT.fullmatch(out) else (None, refused)


def git_grep(root, pattern, paths, ref=None, run=None, timeout=QUERY_TIMEOUT):
    """(status, hits): `git grep` of one recorded query, as an argument list, never through a shell. `status` is
    "ok" (exit 0 or 1), "timeout", or "error: <first line of stderr>"; `hits` is [(file, line, text)]. A `ref`
    must already be a full commit id (ValueError otherwise), and only that exact `<commit>:` prefix is removed
    from a file name, so a name holding a colon survives. `--literal-pathspecs` makes every path a plain path,
    `--` ends options, `-z` separates name, line and text with NULs, and `-I` skips binary files."""
    if ref is not None and not (isinstance(ref, str) and SEARCHED_AT.fullmatch(ref)):
        raise ValueError("git_grep needs a resolved commit id, not %r" % _shown(ref))
    argv = ["git", "--literal-pathspecs", "-C", str(root), "grep", "-n", "-I", "-E", "-z", "-e", pattern]
    argv += ([ref] if ref else []) + ["--"] + list(paths)
    try:
        proc = (run or _run_bounded)(argv, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", []
    except OSError:
        return "error: git could not run", []
    if len(proc.stdout) > MAX_GREP_BYTES:
        return "error: output over the %d byte cap" % MAX_GREP_BYTES, []
    if proc.returncode not in (0, 1):
        first = (_decode(proc.stderr).strip().splitlines() or ["git grep failed"])[0]
        return "error: " + _shown(first, 200), []
    prefix = (ref + ":").encode() if ref else b""
    hits, data, at = [], proc.stdout, 0
    while at < len(data):                           # name NUL line NUL text LF: a name may hold a newline
        end_name = data.find(b"\x00", at)
        end_line = data.find(b"\x00", end_name + 1) if end_name >= 0 else -1
        if end_line < 0:
            break
        end_text = data.find(b"\n", end_line + 1)
        end_text = len(data) if end_text < 0 else end_text
        raw_name, number, raw_text = data[at:end_name], data[end_name + 1:end_line], data[end_line + 1:end_text]
        at = end_text + 1
        if not number.isdigit():
            continue
        name = raw_name[len(prefix):] if prefix and raw_name.startswith(prefix) else raw_name
        text = _decode(raw_text)
        if len(text) > MAX_LINE_CHARS:
            text = text[:MAX_LINE_CHARS] + "…"
        hits.append((_decode(name), int(number), text))
    return "ok", hits


def queries_for(sdlc_dir, goal):
    """The goal's well-formed recorded queries, [(pattern, [paths])]; [] for a legacy goal or no dossier."""
    return parse_reuse(dossier_text(sdlc_dir, goal))["queries"]


def _project_root(sdlc_dir):
    return pathlib.Path(sdlc_dir).resolve().parent


def base_ref(sdlc_dir, goal, base=None, run=None):
    """(ref, note): the remote-tracking ref whose tip a goal is compared with, never HEAD and never a local branch
    unless `base` names one. In order: `base` (prefixed with the remote when it lacks it), the goal's work record
    (`<remote>/<base>`, the way review_context builds it), `work.base` from config.json (a goal worktree has none:
    defaults), the remote's default branch as the local clone knows it (`refs/remotes/<remote>/HEAD`, no
    network). With none of them the ref is `<remote>/`, which resolves to nothing; `note` says the work record
    was absent (None when it was not, or `base` was given). Callers read the commit with `resolve_commit`.
    The pair is the contract the in-flight verb reuses."""
    work = _load("work")
    state = _load("state")
    try:
        config = state.load_config(sdlc_dir)
    except Exception:                               # noqa: BLE001 - missing or unreadable means defaults
        config = {}
    try:
        settings = work.settings(config)
        remote, configured = settings["remote"], settings["base"]
    except Exception:                               # noqa: BLE001 - an option-like config value
        return "", None
    record = work._record(sdlc_dir, goal)
    if base:
        return (base if base.startswith(remote + "/") else remote + "/" + base), None
    if isinstance(record, dict) and isinstance(record.get("remote"), str) and record["remote"] \
            and isinstance(record.get("base"), str) and record["base"]:
        return record["remote"] + "/" + record["base"], None
    ref = remote + "/" + configured if configured else None
    if ref is None:
        try:
            proc = (run or _run_git)(["git", "-C", str(_project_root(sdlc_dir)), "symbolic-ref", "--short",
                                      "refs/remotes/%s/HEAD" % remote], timeout=GIT_TIMEOUT)
            head = _decode(proc.stdout).strip() if proc.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            head = ""
        ref = head if head.startswith(remote + "/") and len(head) > len(remote) + 1 else remote + "/"
    return ref, "rerun: no work record for goal %s; using %s" % (goal, ref)


def fetch_age(root, run=None, now=time.time):
    """Seconds since the last fetch, from FETCH_HEAD's mtime, or None when none can be read. MEASURED (git 2.56): a
    fetch in the main checkout writes `<common>/FETCH_HEAD`, one in a linked worktree writes
    `<common>/worktrees/<name>/FETCH_HEAD`, and remote-tracking refs are shared, so the newer of the two is the
    age. Never contacts the remote."""
    try:
        proc = (run or _run_git)(["git", "-C", str(root), "rev-parse", "--git-path", "FETCH_HEAD",
                                  "--git-common-dir"], timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = _decode(proc.stdout).splitlines() if proc.returncode == 0 else []
    if len(lines) != 2:
        return None
    times = []
    for path in (pathlib.Path(root) / lines[0], pathlib.Path(root) / lines[1] / "FETCH_HEAD"):
        try:
            times.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(0.0, now() - max(times)) if times else None


def _age(seconds):
    """`<m>m` under an hour, `<h>h` under a day, else `<d>d`, each floored; clock skew reads `0m`."""
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    return "%dh" % (seconds // 3600) if seconds < 86400 else "%dd" % (seconds // 86400)


def _query_problem(pattern, paths):
    """Why a recorded query must not run, or None. A dossier is text an agent wrote and a legacy one is never
    checked, so `rerun` applies the checker's own rules to each query itself."""
    if not 1 <= len(pattern) <= MAX_PATTERN:
        return "pattern is empty" if not pattern else \
            "pattern is %d characters; 1 to %d allowed" % (len(pattern), MAX_PATTERN)
    if _CONTROL.search(pattern):
        return "pattern contains a control character"
    for path in paths:
        if _path_error(path):
            return "path %r %s" % (path, _path_error(path))
    return None


def _usage_error(message=None):
    print(USAGE, file=sys.stderr)
    if message:
        print("reuse.py: " + message, file=sys.stderr)
    return 2


def _source(sdlc_dir, goal, ref, base, new_since, run):
    """(commit, label, searched_at_commit, note, refusal, parsed): every ref resolved before any search.
    `refusal` is the one line to print, in the fixed order: no queries, no Searched-at, base, searched-at."""
    parsed = parse_reuse(dossier_text(sdlc_dir, goal))
    root = _project_root(sdlc_dir)
    if not parsed["queries"] and not parsed["bad_queries"]:
        return None, None, None, None, "rerun: no recorded queries for goal %s" % goal, parsed
    searched = parsed["searched_at"]
    if new_since and not (isinstance(searched, str) and SEARCHED_AT.fullmatch(searched)):
        return None, None, None, None, "rerun: the dossier records no searched-at commit", parsed
    note = None
    if ref is not None:
        label = ref
        commit, error = resolve_commit(root, ref, run)
        if commit is None:
            return None, None, None, None, "rerun: " + error, parsed
    else:
        label, note = base_ref(sdlc_dir, goal, base, run)
        commit, error = resolve_commit(root, label, run) if label else (None, None)
        if commit is None and base:
            commit, error = resolve_commit(root, base, run)            # as given: a name the person chose
            label = base if commit else label
        if commit is None:
            return None, None, None, None, "rerun: base ref %s not found; fetch it and run again" % (
                _shown(label.rstrip("/")) or "(unknown)"), parsed
    before = None
    if new_since:
        before, error = resolve_commit(root, searched, run)
        if before is None:
            return None, None, None, None, ("rerun: searched-at commit %s is not on this machine; fetch the "
                                                  "goal branch and run again" % searched[:7]), parsed
    return commit, label, before, note, None, parsed


def rerun(sdlc_dir, goal, ref=None, new_since_research=False, base=None, run=None, clock=time.monotonic,
          now=time.time):
    """The lines `reuse.py rerun` prints (see the module docstring). Reads, never writes, never fetches."""
    deadline = clock() + MAX_SECONDS                # the clock starts before any ref is resolved
    commit, label, before, note, refusal, parsed = _source(sdlc_dir, goal, ref, base, new_since_research, run)
    if refusal:
        return [refusal]
    root = _project_root(sdlc_dir)
    bad = set(parsed["bad_queries"])
    good = iter(parsed["queries"])
    count = len(parsed["queries"]) + len(bad)
    body = ["rerun: only the first %d of %d queries run" % (MAX_QUERIES, count)] if count > MAX_QUERIES else []
    count = min(count, MAX_QUERIES)
    if note:
        body.insert(0, note)
    shown, done = 0, 0
    for i in range(1, count + 1):
        if i in bad:
            body.append("ERROR Q%d: not of the form - pattern: `<regex>` paths: `<path> [<path> ...]`" % i)
            done += 1
            continue
        pattern, paths = next(good)
        problem = _query_problem(pattern, paths)
        if problem:
            body.append("ERROR Q%d: %s" % (i, problem))
            done += 1
            continue
        done += 1
        found = {}
        for where in ([commit] + ([before] if before else [])):
            budget = min(QUERY_TIMEOUT, deadline - clock())
            if budget <= 0:                         # the total limit passed between this query's two searches
                body.append("rerun: stopped after %d seconds; %d of %d queries run" % (MAX_SECONDS, done - 1, count))
                return _capped(body)
            status, hits = git_grep(root, pattern, paths, where, run, budget)
            if status != "ok":
                found = status
                break
            found[where] = hits
        if isinstance(found, str):
            body.append("TIMEOUT Q%d" % i if found == "timeout" else "ERROR Q%d: %s" % (i, found[len("error: "):]))
            continue
        hits = found[commit]
        if before:
            seen = {(name, text) for name, _, text in found[before]}
            hits = [hit for hit in hits if (hit[0], hit[2]) not in seen]     # file and text, never the line number
        for name, line, text in hits[:MAX_HITS_PER_QUERY]:
            body.append("%sQ%d %s:%d: %s" % ("NEW " if before else "", i, _shown(name, 500), line,
                                              _shown(text, MAX_LINE_CHARS + 1)))
        shown += min(len(hits), MAX_HITS_PER_QUERY)
        if len(hits) > MAX_HITS_PER_QUERY:
            body.append("rerun: Q%d truncated at %d hits" % (i, MAX_HITS_PER_QUERY))
    if before:
        age = fetch_age(root, run, now)
        tail = "; remote refs never fetched" if age is None else "; remote refs last fetched " + _age(age)
        summary = "rerun: %d hit(s) new since Research (%s) on %s (%s)%s" % (
            shown, before[:7], _shown(label), commit[:7], tail)
    else:
        summary = "rerun: %d hit(s) for %d query(ies) at %s (%s)" % (shown, count, _shown(label), commit[:7])
    return _capped(body) + [summary]


def _capped(body):
    return body if len(body) <= MAX_OUTPUT_LINES else body[:MAX_OUTPUT_LINES] + [
        "rerun: output stopped at %d lines" % MAX_OUTPUT_LINES]


def _rerun_main(args):
    """`rerun <sdlc_dir> <goal> [--ref R] [--new-since-research] [--base B]`, options parsed by hand so that
    `check-research`'s own argument handling is untouched. A value is taken as given, even one starting with a
    dash: that ref is then refused by `resolve_commit`, not by the parser."""
    positional, options, i = [], {}, 0
    while i < len(args):
        arg = args[i]
        if arg in ("--ref", "--base"):
            if arg in options or i + 1 >= len(args):
                return _usage_error("%s needs one value, given once" % arg)
            options[arg] = args[i + 1]
            i += 2
            continue
        if arg == "--new-since-research":
            if arg in options:
                return _usage_error("--new-since-research given twice")
            options[arg] = True
        elif arg.startswith("-"):
            return _usage_error("unknown option %s" % _shown(arg))
        else:
            positional.append(arg)
        i += 1
    if len(positional) != 2:
        return _usage_error()
    if "--ref" in options and "--new-since-research" in options:
        return _usage_error("--ref and --new-since-research cannot be combined")
    lines = rerun(positional[0], positional[1], ref=options.get("--ref"),
                  new_since_research="--new-since-research" in options, base=options.get("--base"))
    print("\n".join(lines))
    return 0


def main(argv):
    if argv in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    if argv[:1] == ["source"]:
        if len(argv) != 2 or argv[1] == "--at-research":
            print(USAGE, file=sys.stderr)
            return 2
        print(json.dumps(source(argv[1]), sort_keys=True))
        return 0
    if argv[:1] == ["rerun"]:
        return _rerun_main(argv[1:])
    at_research = argv[-1:] == ["--at-research"]
    args = argv[:-1] if at_research else argv
    if len(args) != 3 or args[0] != "check-research":
        print(USAGE, file=sys.stderr)
        return 2
    errors, warnings, parsed, k = _check(args[1], args[2], at_research)
    for line in warnings + errors[:MAX_SHOWN_ERRORS]:
        print("reuse.py: " + line, file=sys.stderr)
    if len(errors) > MAX_SHOWN_ERRORS:
        print("reuse.py: \u2026 and %d more errors" % (len(errors) - MAX_SHOWN_ERRORS), file=sys.stderr)
    if errors:
        return 2
    if parsed is not None:
        print("reuse.py: ## Reuse and ## Test design for goal %s are well formed (%d rows, %d queries, %d criteria)"
              % (args[2], len(parsed["rows"]), len(parsed["queries"]), k))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

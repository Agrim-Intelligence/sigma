#!/usr/bin/env python3
"""usage: reuse.py check-research <sdlc_dir> <goal> [--at-research]
       reuse.py check-plan <sdlc_dir> <goal>
       reuse.py source <sdlc_dir>
       reuse.py rerun <sdlc_dir> <goal> [--ref R | --new-since-research] [--base B]
       reuse.py inflight <sdlc_dir> <goal> [--remote N] [--base R]

This script writes no files and starts no background process: it reads files and runs short git and Python
subprocesses, each bounded by a timeout. `check-research` checks the `## Reuse` section of a goal's research
dossier (`.sdlc/research/<goal>.md`, else the copy on the goal's branch): the `**Searched at:**` commit, the
recorded queries, and the RU table or its `none: <reason>` line. Exit 2 on any error, 0 otherwise. With
`--at-research` it then also prints `In-flight work:` and the lines `inflight` returns (below) on stdout, whatever
the exit code, and an unexpected error in that part becomes one line and never changes the exit code; that part
reads git only and is bounded by the 120 second limit below, so the gesture that was near-instant can now take
up to about 120 seconds (a ceiling, not a measurement). A dossier
with no section, or a section with neither a `**Source:**` nor a `**Searched at:**` line, is legacy: a warning,
never a refusal. Without `--at-research` a Searched-at commit missing from this machine is a warning too.

`check-plan` checks the goal's plan (`.sdlc/plans/<goal>.md`, or the path `check_plan(..., plan=)` is given) against
the dossier: first every error `check_research` returns, unchanged (when `verify.enforce` is on and there is no
acceptance record that line and the plan check's own `no recorded acceptance criteria` line both appear); then a
`## Reuse` section with one `- RU-<n>: <text>` bullet for every dossier row id (an optional table in the dossier's
format is judged by `table_errors`, and may not reuse a dossier id; a plan `none: <reason>` line never excuses a
missing bullet); then a `## Test map` section with exactly one `- AC-<n>: <value>` bullet for every criterion id of
`review_tests.criteria`, each value a list of tests all covered by the plan's `## Tests` (an entry equal to the
test, or a file entry, which has no `::`, followed by `::`; a runner bullet only exactly), or `UNTESTABLE: <why>`;
`NONE` is refused. A docs-only `## Tests` (`none: <reason>`) lets the map be one `none: <reason>` line. A goal with
no dossier, or a dossier that is not in the new format, is legacy whether or not it has a plan: a warning, exit 0.
A dossier over 1 MiB is not parsed for that decision: `check_research`'s own over-size error is returned alone. A
broken `## Tests` is not this check's error (the existing `## Tests` check speaks): coverage is skipped with one
`coverage not judged` warning. With no acceptance record the check refuses when `verify.enforce` is on, and warns
and skips the map rules when it is off. A corrupt config.json reads as no runners for the coverage rule (a runner
bullet then gives the warning) while `verify.enforce` still reads it as on. Nothing here is called from a gate.
Ceilings: one `check_research` call plus two section scans of at most 1 MiB of plan and one criteria read, linear
in plan size and rows; the dossier is read twice (here and inside `check_research`, each may fall back to a
`git show`) and the plan file is read before the 1 MiB cap. Measured once each on one machine (Linux, Python 3.13, a small project, not the Apple M4 Pro of the figures
below; `reuse.py` read as written, `review_tests` loaded once per run): a 1 MiB plan of bullets cut at the cap
took 0.12 s; a 1 MiB plan of `|` rows (1,571,999 errors) took 1.58 s and peaked at 303 MB of Python allocations
(tracemalloc), above the 0.47 s `check_research` figure (different machines, so not a ratio); a 1 MiB `## Test map`
of 80,645 `- AC-1: NONE` bullets took 0.15 s (it took 7.4 s when `review_tests` was loaded per bullet), 32,762
duplicate valid bullets 0.07 s, and 20,000 duplicate bullets 0.05 s. Not measured: a real large plan, memory for the
bullet shapes, a plan with many `## Tests` entries, and any run on another machine. With no acceptance record and
`verify.enforce` off the result holds two warnings, `check_research`'s and this check's own.

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

`inflight` searches the changed files of the other goals' unmerged remote branches (the remote-tracking refs
`refs/remotes/<remote>/<work.branch_prefix>*`, newest commit first, the goal's own branch left out) for the goal's
recorded queries, and lists what those branches' research dossiers and plans declare as `extend`, `dependency-new`
or `new`. A hit prints `INFLIGHT <branch> Q<i> <file>:<line>: <text>`, a declaration `DECLARED <branch> <id>
<decision>: <capability>`, both on one line of at most 200 characters of quoted text; the last line is always
`inflight: searched <k> of <n> goal branch(es) (cap 50); <h> hit(s); <d> declared addition(s); remote refs last
fetched <age>`: `n` is the other-goal branches found, `k` those whose ref resolved and whose diff succeeded before
a cap or the time limit, `h` the printed INFLIGHT lines, `d` the printed DECLARED lines. With no other goal branch
it prints exactly one line, `inflight: no remote goal branches under <remote>/<prefix>`; with branches and no
recorded queries it prints `inflight: no recorded queries for goal <goal>` and still lists declared additions
(the issue's two "print only" rules conflict there). Only files a branch changed since its merge base with the
base ref are searched (`<base>...<branch>`, deleted files left out); everything under `.sdlc/` is not searched,
because it holds the other goal's own records, which are read only for their `## Reuse` table, and only when they
are `.md` files under `.sdlc/research/` or `.sdlc/plans/` of at most 1 MiB. The base is `rerun`'s (`base_ref`);
`--remote` names a remote other than the configured one. It never fetches (the last line says how long ago a
fetch was), never writes, and exits 0 for every informational case and 2 for a usage error. Blind spots: branches
under `work.checkpoint_prefix` are not searched (a line says so when it is set), and a branch not fetched on this
machine does not exist to it.
Ceilings, stated counts and not timings: 50 branches, 200 changed files and 10 research or plan files per branch,
25 recorded queries, 25 DECLARED lines per file, 200 hits per query, 1,000 output lines and 120 seconds for the
whole run, whichever comes first (the run says `inflight: stopped after 120 seconds`). That is at most 1
`rev-parse`, 1 `diff`, 25 `git grep`s and 20 `cat-file` or `show` calls per branch, 47 in all, 2,350 over 50
branches, one process at a time; the 120 seconds is the serialised ceiling at 10x or 100x branches, files or
queries. The cost at the cap is NOT measured: no remote goal branches existed where this was written, and the
pull request records the measurement step.
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
LEGACY_WHY_NO_DOSSIER = "no research dossier"
LEGACY_WHY_NO_SECTION = "the research dossier has no ## Reuse section"
LEGACY_WHY_OLD_SECTION = "## Reuse was not written from reuse.py brief"
LEGACY_NO_DOSSIER = "legacy goal (%s); nothing checked" % LEGACY_WHY_NO_DOSSIER
LEGACY_NO_SECTION = "legacy goal (%s); nothing checked" % LEGACY_WHY_NO_SECTION
LEGACY_OLD_SECTION = "legacy goal (%s); nothing checked" % LEGACY_WHY_OLD_SECTION
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
#: A plan's own bullets. The remainder may be empty (`- RU-1:`), which `parse_reuse` would count as a query.
_RU_BULLET = re.compile(r"- (RU-[0-9]+):(?:[ \t](.*))?")
_AC_BULLET = re.compile(r"- (AC-[0-9]+):(?:[ \t](.*))?")


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


def _legacy_why(text):
    """Why the goal is legacy for the plan check, or None when its dossier is in the new format. Decided by
    `is_new_format`, never by the heading."""
    if not text:
        return LEGACY_WHY_NO_DOSSIER
    if is_new_format(text):
        return None
    return LEGACY_WHY_OLD_SECTION if parse_reuse(text)["present"] else LEGACY_WHY_NO_SECTION


def _bullets(lines, pattern):
    """[(id, text)] of a section's bullets, in order, the text stripped (empty when the line has none)."""
    found = (pattern.fullmatch(line) for line in lines)
    return [(match.group(1), (match.group(2) or "").strip()) for match in found if match]


def _plan_reuse_errors(lines, dossier, root):
    """(errors, bullets read) for the plan's `## Reuse` lines. Bullets are read here, not by `parse_reuse`, which
    counts every `- ` line as a query and so cannot see an empty one; only the `|` lines and the `none:` line go
    to it, so `table_errors` stays the one table reader."""
    dossier_ids = [row["id"] for row in dossier["rows"]]
    cited = {}
    for rid, text in _bullets(lines, _RU_BULLET):
        cited.setdefault(rid, []).append(text)
    block = [line for line in lines if line.strip().startswith(("|", "none:"))]
    parsed = parse_reuse("## Reuse\n" + "\n".join(block))
    errors = []
    if parsed["none"] == "":
        errors.append("## Reuse: none: needs a reason")
    for rid in dict.fromkeys(i for i in dossier_ids if _ID.fullmatch(i)):
        if rid not in cited:
            errors.append("%s: no bullet in the plan's ## Reuse for this research row" % rid)
        elif "" in cited[rid]:
            errors.append("%s: bullet text is empty" % rid)
    errors += table_errors(parsed, root)
    taken = set(dossier_ids)
    errors += ["%s: id collides with a row of the research dossier" % row["id"]
               for row in parsed["rows"] if row["id"] in taken]
    return errors, sum(len(texts) for texts in cited.values())


def _runners(sdlc_dir):
    """`tests.runners` as `red_green.main` reads it. A goal worktree's `.sdlc` has no config.json (ConfigMissing),
    and a config that cannot be read gives no runners too: a runner bullet then fails `selectors`, which is the
    visible `coverage not judged` warning, never an error here."""
    state = _load("state")
    try:
        return state.test_runners(state.load_config(sdlc_dir))
    except Exception:                              # noqa: BLE001 - missing, invalid or malformed: no runners
        return {}


def _coverage(plan, runners):
    """(covered(test, exact_only=False) -> bool, None), or (None, the first line of why `## Tests` cannot be read).
    A `## Tests` that is the docs-only line covers nothing. A file entry (no `::`) covers `entry::<name>`; every
    other entry, a runner bullet included, covers only itself. `exact_only` drops the file-entry rule: it is for a
    whole comma-list value, which only a runner test name holding a comma can match."""
    rg = _load("red_green")
    try:
        entries = [] if rg.none_reason(plan) is not None else rg.selectors(plan, runners)
    except ValueError as exc:
        return None, (str(exc).splitlines() or [type(exc).__name__])[0]
    exact = set(entries)
    files = [entry + "::" for entry in entries if "::" not in entry and not rg.parse_runner(entry)]
    def covered(test, exact_only=False):
        return test in exact or (not exact_only and any(test.startswith(prefix) for prefix in files))
    return covered, None


def _value_errors(cid, value, covered, tests):
    """The errors of one `## Test map` value, in `review_tests.check`'s order: NONE, then UNTESTABLE, else tests.
    `tests` is the `review_tests` module, loaded once by the caller (loading it per bullet cost 7.4 s on 1 MiB)."""
    if not value:
        return ["%s: value is empty; map a test or write UNTESTABLE: <why>" % cid]
    if tests._NONE.match(value):
        return ["%s: NONE is refused; map a test or write UNTESTABLE: <why>" % cid]
    untestable = tests._UNTESTABLE.match(value)
    if untestable:
        return [] if untestable.group(1).strip() else ["%s: UNTESTABLE needs a reason" % cid]
    if covered is None or covered(value, exact_only=True):   # whole value, exact: a runner name may hold a comma
        return []
    errors = []
    for part in (part.strip() for part in value.split(",")):
        if not part:
            errors.append("%s: an empty test name in the list" % cid)
        elif not covered(part):
            errors.append("%s: %s is not covered by a ## Tests entry" % (cid, part))
    return errors


def _test_map_errors(lines, ids, goal, covered, tests):
    """Rules 6 and 7 for the bullets of `## Test map`: one per criterion id, none for another id, each value valid."""
    bullets = _bullets(lines, _AC_BULLET)
    count = {}
    for cid, _ in bullets:
        count[cid] = count.get(cid, 0) + 1
    errors = []
    for cid in ids:
        if not count.get(cid):
            errors.append("%s: no bullet in ## Test map" % cid)
        elif count[cid] > 1:
            errors.append("%s: more than one bullet" % cid)
    known = set(ids)
    errors += ["%s: not an acceptance criterion of goal %s" % (cid, goal) for cid in count if cid not in known]
    for cid, value in bullets:
        if cid in known:
            errors += _value_errors(cid, value, covered, tests)
    return errors


def _plan_check(sdlc_dir, goal, plan=None):
    """(errors, warnings, plan path or None, RU bullets read, criteria mapped); the path is None when no plan was
    checked."""
    text = dossier_text(sdlc_dir, goal)
    if len(text) > MAX_CHARS:                      # not parsed: a 1 MiB dossier would pass as legacy otherwise
        return list(check_research(sdlc_dir, goal)[0]), [], None, 0, 0
    why = _legacy_why(text)
    if why:
        return [], ["legacy goal (%s); plan inputs not checked" % why], None, 0, 0
    path = plan or _load("review_context").phase_doc_file(sdlc_dir, goal, "plans")
    if path is None:
        return ["no plan for goal %s" % goal], [], None, 0, 0
    try:
        plan_text = pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ["no plan for goal %s" % goal], [], None, 0, 0
    if len(plan_text) > MAX_CHARS:
        return ["plan: over %d characters; nothing checked" % MAX_CHARS], [], path, 0, 0
    errors, warnings = (list(found) for found in check_research(sdlc_dir, goal))
    tests = _load("review_tests")
    root = tests._root(sdlc_dir, goal)
    cited, k = 0, 0
    lines = _section_lines(plan_text, "Reuse")
    if lines is None:
        errors.append("## Reuse: section missing")
    else:
        found, cited = _plan_reuse_errors(lines, parse_reuse(text), root)
        errors += found
    map_lines = _section_lines(plan_text, "Test map")
    if map_lines is None:
        return errors + ["## Test map: section missing"], warnings, path, cited, k
    try:
        ids = [cid for cid, _ in tests.criteria(sdlc_dir, goal)]
    except (OSError, ValueError):
        ids = None
        (errors if _enforced(sdlc_dir) else warnings).append("no recorded acceptance criteria for goal %s" % goal)
    lone = [line.strip() for line in map_lines if line.strip()]
    docs_only = _load("red_green").none_reason(path) is not None and len(lone) == 1 and lone[0].startswith("none:")
    if docs_only:
        if not lone[0][len("none:"):].strip():
            errors.append("## Test map: none: needs a reason")
    elif ids is not None:
        covered, why_not = _coverage(path, _runners(sdlc_dir))
        if why_not:
            warnings.append("## Test map: coverage not judged (%s)" % why_not)
        errors += _test_map_errors(map_lines, ids, goal, covered, tests)
        k = len(ids)
    return errors, warnings, path, cited, k


def check_plan(sdlc_dir, goal, plan=None):
    """(errors, warnings) for the goal's plan inputs; the order and the rules are in the module docstring. `plan`
    is a path, resolved with `review_context.phase_doc_file` when None, so a caller that holds the reviewed copy
    passes it in."""
    return _plan_check(sdlc_dir, goal, plan)[:2]


def _print_findings(errors, warnings):
    for line in warnings + errors[:MAX_SHOWN_ERRORS]:
        print("reuse.py: " + line, file=sys.stderr)
    if len(errors) > MAX_SHOWN_ERRORS:
        print("reuse.py: \u2026 and %d more errors" % (len(errors) - MAX_SHOWN_ERRORS), file=sys.stderr)


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


# ---------------------------------------------------------------------------------------------------- inflight
# New in #2283, a block of its own after `rerun`. Same seam as `rerun`: every git call is `_run_git`-shaped
# `run(argv, timeout=...)`, and a branch or file name only reaches git as a full refname or inside `<commit>:<path>`.

#: Stated ceilings, not measurements: each bounds work whatever the repository holds. `MAX_SECONDS` is shared with
#: `rerun`; the pull request records the timing that justifies or changes the three below.
MAX_BRANCHES = 50
MAX_CHANGED_FILES = 200
MAX_PLAN_FILES_PER_BRANCH = 10
MAX_DECLARED_PER_FILE = 25
#: The byte count `git cat-file -s` reports for a research or plan file; equal to MAX_CHARS, its own name because it
#: is read from git, not counted in text.
MAX_BLOB_BYTES = 1048576
#: The `## Reuse` decisions that mean "this branch adds something". A test keeps it inside DECISIONS.
DECLARED_DECISIONS = ("extend", "dependency-new", "new")
_SDLC_DIR = ".sdlc/"
_RECORD_DIRS = (".sdlc/research/", ".sdlc/plans/")
#: Everything that must not survive into one line of output: control characters, the Unicode line and paragraph
#: separators, and the bidirectional overrides and isolates (they reorder what a terminal shows).
_UNSAFE_TEXT = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")


def _one_line(text, limit=200):
    """`text` on one line: every unsafe character becomes one space, then cut to `limit` characters, no ellipsis.
    `_shown` stays as it is for the existing lines; this one is for text quoted from another goal's branch."""
    return _UNSAFE_TEXT.sub(" ", str(text))[:limit]


def _fetch_age_text(seconds):
    """`never`, else `<n> minute(s) ago` under an hour, `<n> hour(s) ago` under a day, else `<n> day(s) ago`."""
    if seconds is None:
        return "never"
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return "%d minute(s) ago" % (seconds // 60)
    return "%d hour(s) ago" % (seconds // 3600) if seconds < 86400 else "%d day(s) ago" % (seconds // 86400)


def _goal_stem(sdlc_dir, goal):
    """The goal's file stem the way `work.record_path` makes it (a `.md` goal loses its suffix), or None for a goal
    that would be an unsafe path component. Rebuilt here, with `state.unsafe_goal_reason`, so the own-branch lookup
    does not load work.py; a test keeps the two in agreement."""
    path = pathlib.Path(str(goal))
    stem = path.stem if path.suffix == ".md" else str(goal)
    return None if _load("state").unsafe_goal_reason(stem) else stem


def _work_config(sdlc_dir):
    """The `work` block of config.json, {} when there is none (a goal worktree's `.sdlc` has no config.json)."""
    try:
        config = _load("state").load_config(sdlc_dir)
        block = config.get("work") if isinstance(config, dict) else None
    except Exception:                               # noqa: BLE001 - missing or unreadable means defaults
        return {}
    return block if isinstance(block, dict) else {}


def _record_branch(sdlc_dir, stem):
    """The `branch` field of the goal's work record, or None. Read directly: the record is the one place the
    branch name is written, including a retry name."""
    if stem is None:
        return None
    try:
        record = json.loads((pathlib.Path(sdlc_dir) / "state" / "work" / (stem + ".json")).read_text())
    except Exception:                               # noqa: BLE001 - absent or unreadable both mean no record
        return None
    branch = record.get("branch") if isinstance(record, dict) else None
    return branch if isinstance(branch, str) and branch else None


def _own_branch_test(sdlc_dir, goal, prefix, root, run):
    """A predicate on a display name (the ref minus `refs/remotes/<remote>/`): True for this goal's own branch.
    With a work record that is its `branch`. Without one, HEAD's name AND `<prefix><stem>` with its retry names
    `<prefix><stem>-<n>` (work.py names a retry that way), so a pushed branch of this goal is never reported as
    another goal's; `<stem>-notes` is another branch."""
    stem = _goal_stem(sdlc_dir, goal)
    recorded = _record_branch(sdlc_dir, stem)
    if recorded:
        return lambda name: name == recorded
    head = ""
    try:
        proc = (run or _run_git)(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], timeout=GIT_TIMEOUT)
        head = _decode(proc.stdout).strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    stem_name = prefix + stem if stem is not None else None
    retry = re.compile(re.escape(stem_name) + r"-[0-9]+") if stem_name else None
    return lambda name: (bool(head) and head != "HEAD" and name == head) or (
        stem_name is not None and (name == stem_name or bool(retry.fullmatch(name))))


def _usable(label, value, default):
    """(value, refused): `value` (or `default` when absent) after `state.safe_ref`; refused when it changes it or
    is empty. safe_ref prints its own refusal on stderr; the verb adds one `... is not usable` line on stdout."""
    if value is None:
        value = default
    if not isinstance(value, str) or not value:
        return value, True
    return value, _load("state").safe_ref(label, value) != value


class _Body:
    """The output lines before the summary, appended through one gate: at MAX_OUTPUT_LINES nothing more goes in and
    one `inflight: output stopped at <n> lines` line (not counted) takes the next place."""

    def __init__(self):
        self.lines, self.stopped = [], False

    def add(self, line):
        if self.stopped:
            return False
        if len(self.lines) >= MAX_OUTPUT_LINES:
            self.stopped = True
            self.lines.append("inflight: output stopped at %d lines" % MAX_OUTPUT_LINES)
            return False
        self.lines.append(line)
        return True


def _git_lines(run, argv, budget):
    """(stdout bytes or None, why): one git call through `run`, never raising for a git failure or a bad argument."""
    try:
        proc = (run or _run_git)(argv, timeout=max(0.001, min(GIT_TIMEOUT, budget)))
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except (ValueError, OSError) as exc:
        return None, type(exc).__name__
    if proc.returncode != 0:
        return None, _one_line((_decode(proc.stderr).strip().splitlines() or ["git failed"])[0], 100)
    return proc.stdout, None


def _declared(root, name, commit, path, run, budget, body):
    """The `DECLARED` lines of one research or plan file on a branch; returns how many were printed."""
    out, why = _git_lines(run, ["git", "-C", str(root), "cat-file", "-s", commit + ":" + path], budget)
    label = "inflight: %s %s " % (_one_line(name), _one_line(path))
    size = _decode(out).strip() if out is not None else ""
    if not size.isdigit():
        body.add(label + "could not be sized")
        return 0
    if int(size) > MAX_BLOB_BYTES:
        body.add(label + "is too large to read")
        return 0
    out, why = _git_lines(run, ["git", "-C", str(root), "show", commit + ":" + path], budget)
    if out is None:
        body.add(label + "could not be read (%s)" % why)
        return 0
    rows = [r for r in parse_reuse(_decode(out))["rows"] if r.get("decision", "").strip() in DECLARED_DECISIONS]
    printed = 0
    for row in rows[:MAX_DECLARED_PER_FILE]:
        if not body.add("DECLARED %s %s %s: %s" % (_one_line(name), _one_line(row["id"]), _one_line(row["decision"].strip()),
                                                   _one_line(row["capability"]))):
            break
        printed += 1
    if len(rows) > MAX_DECLARED_PER_FILE:
        body.add("inflight: %s %s declares %d additions; printed the first %d" % (
            _one_line(name), _one_line(path), len(rows), MAX_DECLARED_PER_FILE))
    return printed


def _inflight_queries(parsed):
    """([(i, pattern, paths)], skipped lines, note): each query numbered by POSITION over the good and the bad
    ones together, as `rerun` numbers them, at most MAX_QUERIES; a bad one gives one `skipped` line."""
    bad = set(parsed["bad_queries"])
    good = iter(parsed["queries"])
    count = len(parsed["queries"]) + len(bad)
    note = ["inflight: only the first %d of %d queries run" % (MAX_QUERIES, count)] if count > MAX_QUERIES else []
    runnable, skipped = [], []
    for i in range(1, min(count, MAX_QUERIES) + 1):
        if i in bad:
            skipped.append("inflight: Q%d skipped: not of the form - pattern: `<regex>` paths: `<path> [<path> ...]`" % i)
            continue
        pattern, _paths = next(good)
        problem = _query_problem(pattern, [])     # the pattern rules only: the query's own paths are not used here
        if problem:
            skipped.append("inflight: Q%d skipped: %s" % (i, problem))
        else:
            runnable.append((i, pattern))
    return runnable, skipped, note


def _clocks(now, clock):
    """(age clock, deadline clock): `now` is a callable or a plain number. With only `now` injected both come from
    it, so one injected clock drives a test; `clock`, when given, drives the deadline alone."""
    if now is None:
        age = time.time
    else:
        age = now if callable(now) else (lambda: now)
    return age, clock or (age if now is not None else time.monotonic)


def _inflight_base(sdlc_dir, goal, root, remote, configured, base, run):
    """(commit, label, notes): the base the branches are diffed against; commit None when it does not resolve.
    D4: `--base` with a `--remote` other than the configured one is prefixed with `--remote` here, never through
    `base_ref` (which would build `<configured>/<remote>/<base>`); without `--base` the base is `base_ref`'s, on
    the configured remote, and a note says so."""
    notes = []
    if base and remote != configured:
        label = base if base.startswith(remote + "/") else remote + "/" + base
    else:
        label, note = base_ref(sdlc_dir, goal, base, run)
        if note:
            notes.append("inflight:" + note[len("rerun:"):] if note.startswith("rerun:") else note)
        if label and remote != configured and not base:
            notes.append("inflight: base %s is not on remote %s" % (_one_line(label), _one_line(remote)))
    commit = resolve_commit(root, label, run)[0] if label else None
    return commit, label, notes


def inflight(sdlc_dir, goal, remote=None, base=None, run=None, now=None, clock=None):
    """The lines `reuse.py inflight` prints (see the module docstring). Reads, never writes, never fetches."""
    age_clock, tick = _clocks(now, clock)
    deadline = tick() + MAX_SECONDS                 # taken before the first git call
    work = _work_config(sdlc_dir)
    configured, bad_remote = _usable("work.remote", work.get("remote") or None, "origin")
    prefix, bad_prefix = _usable("work.branch_prefix", work.get("branch_prefix"), "sdlc/")
    remote_name, bad_option = _usable("--remote", remote or None, configured)
    if bad_remote or bad_option:
        return ["inflight: remote is not usable"]
    if bad_prefix:
        return ["inflight: branch_prefix is not usable"]
    root = _project_root(sdlc_dir)
    is_own = _own_branch_test(sdlc_dir, goal, prefix, root, run)
    lead = "refs/remotes/%s/" % remote_name
    out, why = _git_lines(run, ["git", "-C", str(root), "for-each-ref", "--sort=-committerdate",
                                "--format=%(refname)", lead], deadline - tick())
    listing_failed = out is None
    names = [] if listing_failed else [
        line[len(lead):] for line in _decode(out).splitlines()
        if line.startswith(lead) and line[len(lead):].startswith(prefix) and len(line) > len(lead) + len(prefix)]
    others = [name for name in names if not is_own(name)]
    if not others and not listing_failed:
        return ["inflight: no remote goal branches under %s/%s" % (_one_line(remote_name), _one_line(prefix))]
    body = _Body()
    if listing_failed:
        body.add("inflight: could not list remote goal branches (%s)" % why)
    n, k, hits, declared = len(others), 0, 0, 0
    parsed = parse_reuse(dossier_text(sdlc_dir, goal))
    runnable, skipped, cap_note = _inflight_queries(parsed)
    no_queries = not parsed["queries"] and not parsed["bad_queries"]
    base_commit = None
    if others:
        base_commit, label, notes = _inflight_base(sdlc_dir, goal, root, remote_name, configured, base, run)
        for line in notes:
            body.add(line)
        if base_commit is None:
            body.add("inflight: base %s does not resolve; no branch was searched" % (_one_line(label) if label else "(none)"))
    if base_commit is not None:
        for line in cap_note + skipped:
            body.add(line)
        if no_queries:
            body.add("inflight: no recorded queries for goal %s" % _one_line(goal))
        stopped_for_time = False
        for name in others[:MAX_BRANCHES]:
            if body.stopped:
                break
            if tick() >= deadline:
                stopped_for_time = True
                break
            shown = _one_line(name)
            commit, error = resolve_commit(root, lead + name, run)
            if commit is None:
                body.add("inflight: %s does not resolve" % shown)
                continue
            out, why = _git_lines(run, ["git", "-C", str(root), "diff", "--name-only", "-z", "--diff-filter=d",
                                        "%s...%s" % (base_commit, commit), "--"], deadline - tick())
            if out is None:
                body.add("inflight: %s diff failed (%s)" % (shown, why))
                continue
            k += 1
            files = sorted({_decode(raw) for raw in out.split(b"\x00") if raw})
            if len(files) > MAX_CHANGED_FILES:
                body.add("inflight: %s changed %d files; searched the first %d" % (shown, len(files), MAX_CHANGED_FILES))
                files = files[:MAX_CHANGED_FILES]
            safe = [f for f in files if not _CONTROL.search(f)]
            if len(safe) < len(files):
                body.add("inflight: %s skipped %d file name(s) with control characters" % (shown, len(files) - len(safe)))
            paths = [f for f in safe if not f.startswith(_SDLC_DIR)]   # the other goal's own records are not product code
            for i, pattern in (runnable if paths else ()):
                if body.stopped:
                    break
                if tick() >= deadline:
                    stopped_for_time = True
                    break
                try:
                    status, found = git_grep(root, pattern, paths, commit, run, min(QUERY_TIMEOUT, deadline - tick()))
                except (ValueError, OSError) as exc:
                    body.add("inflight: %s Q%d grep failed (%s)" % (shown, i, type(exc).__name__))
                    continue
                if status != "ok":
                    body.add("inflight: %s Q%d %s" % (shown, i, "TIMEOUT" if status == "timeout" else
                                                      "ERROR: " + _one_line(status[len("error: "):])))
                    continue
                for path, line, text in found[:MAX_HITS_PER_QUERY]:
                    if body.add("INFLIGHT %s Q%d %s:%d: %s" % (shown, i, _one_line(path, 500), line, _one_line(text))):
                        hits += 1
                if len(found) > MAX_HITS_PER_QUERY:
                    body.add("inflight: %s Q%d truncated at %d hits" % (shown, i, MAX_HITS_PER_QUERY))
            records = [f for f in safe if f.endswith(".md") and f.startswith(_RECORD_DIRS)]
            if len(records) > MAX_PLAN_FILES_PER_BRANCH:
                body.add("inflight: %s carries %d research or plan files; read the first %d" % (
                    shown, len(records), MAX_PLAN_FILES_PER_BRANCH))
            for path in records[:MAX_PLAN_FILES_PER_BRANCH]:
                if body.stopped:
                    break
                if stopped_for_time or tick() >= deadline:
                    stopped_for_time = True
                    break
                declared += _declared(root, name, commit, path, run, deadline - tick(), body)
            if stopped_for_time:
                break
        if stopped_for_time:
            body.add("inflight: stopped after %d seconds" % MAX_SECONDS)
    lines = list(body.lines)
    checkpoint = work.get("checkpoint_prefix")
    if isinstance(checkpoint, str) and checkpoint:
        lines.append("inflight: work.checkpoint_prefix %s is set; branches under it are not searched" % _one_line(checkpoint))
    age = _fetch_age_text(fetch_age(root, run, age_clock))
    return lines + ["inflight: searched %d of %d goal branch(es) (cap %d); %d hit(s); %d declared addition(s); "
                    "remote refs last fetched %s" % (k, n, MAX_BRANCHES, hits, declared, age)]


def _inflight_main(args):
    """`inflight <sdlc_dir> <goal> [--remote N] [--base R]`, options parsed by hand like `rerun`'s: a value is taken
    as given, a bad one is refused later by `resolve_commit` or `safe_ref`, not by the parser."""
    positional, options, i = [], {}, 0
    while i < len(args):
        arg = args[i]
        if arg in ("--remote", "--base"):
            if arg in options or i + 1 >= len(args):
                return _usage_error("%s needs one value, given once" % arg)
            options[arg] = args[i + 1]
            i += 2
            continue
        if arg.startswith("-"):
            return _usage_error("unknown option %s" % _shown(arg))
        positional.append(arg)
        i += 1
    if len(positional) != 2:
        return _usage_error()
    print("\n".join(inflight(positional[0], positional[1], remote=options.get("--remote"), base=options.get("--base"))))
    return 0


def _print_inflight(sdlc_dir, goal):
    """The `--at-research` block. An unexpected error becomes one line, never an exit code: it must not change
    what `check-research` returns."""
    print("In-flight work:")
    try:
        print("\n".join(inflight(sdlc_dir, goal)))
    except Exception as exc:                        # noqa: BLE001 - see the docstring
        print("inflight: could not run (%s)" % type(exc).__name__)


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
    if argv[:1] == ["check-plan"]:
        if len(argv) != 3:
            print(USAGE, file=sys.stderr)
            return 2
        errors, warnings, plan, cited, mapped = _plan_check(argv[1], argv[2])
        _print_findings(errors, warnings)
        if errors:
            return 2
        if plan is not None:
            print("reuse.py: ## Reuse and ## Test map in %s are complete (%d rows cited, %d criteria mapped)"
                  % (plan, cited, mapped))
        return 0
    if argv[:1] == ["rerun"]:
        return _rerun_main(argv[1:])
    if argv[:1] == ["inflight"]:
        return _inflight_main(argv[1:])
    at_research = argv[-1:] == ["--at-research"]
    args = argv[:-1] if at_research else argv
    if len(args) != 3 or args[0] != "check-research":
        print(USAGE, file=sys.stderr)
        return 2
    errors, warnings, parsed, k = _check(args[1], args[2], at_research)
    _print_findings(errors, warnings)
    if parsed is not None and not errors:
        print("reuse.py: ## Reuse and ## Test design for goal %s are well formed (%d rows, %d queries, %d criteria)"
              % (args[2], len(parsed["rows"]), len(parsed["queries"]), k))
    if at_research:
        _print_inflight(args[1], args[2])
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

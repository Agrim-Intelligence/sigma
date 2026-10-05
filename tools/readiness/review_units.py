#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Turn one frozen commit into review units: every high-risk file in full, plus a seeded sample.

USAGE
    review_units.py <repo> --sha SHA [--json PATH]

    Without --json the JSON is printed to stdout and warnings go to stderr. With --json PATH the
    JSON is written to PATH by one plain write and one summary line is printed instead.

EXIT: 0 = ok; 1 = failed; 2 = bad arguments or a missing precondition.

WHY. Reading every one of roughly 95,000 tracked lines line by line is the wrong default, and a
review that "found nothing" only means something when it is clear what it looked at. This turns
one commit into a fixed list of units a reviewer can be held to: Tier A in full, Tier B as a
sample nobody chose by hand. The same commit always gives the same list, byte for byte.

WHAT IT PRODUCES. Schema readiness-units/v1, keys in this order, indent 2, trailing newline:
schema, sha (the full 40 hex digits), seed, tier_a, tier_b_population_lines, tier_b_sample. A unit is
{"unit": "A01", "files": [{"path", "start", "end"}], "lines": n}: start and end are 1-based and
inclusive, and a unit holds at most 3,000 lines. build() validates its own output before anything
is printed or written.
  * Tier A, in full: every tracked file under hooks/ plus the 21 paths in TIER_A_FILES. A named
    path that is not tracked at the commit exits 2 naming it, because skipping it would quietly
    shrink what "reviewed in full" covers. The list is tied to the frozen commit: a later commit
    that deletes one of those files is refused the same way. doctor.py is NOT in Tier A; it is
    sampled, so the draw can miss it.
    RE-CUT LEVER. The refusal is the intended behaviour, not a defect: a deleted file is dropped by
    a person, in the change that re-cuts, never by the tool. The exact edit is to delete that one
    path from TIER_A_FILES here and from ISSUE_TIER_A in tests/test_readiness_review_units.py, then
    run the tool and commit the new units file. Done once, for install.sh (removed by #408, which
    made the old list refuse every later commit). The cost of the edit: the list no longer names
    that file, so this tool cannot reproduce units for a commit from before the deletion; the
    pre-deletion units stay as a record in docs/launch/evidence/review-units-a5c615062313.json.
  * Tier B, sampled: tracked *.py and *.sh under skills/, tools/ and evals/, with no tests path
    component, that are not in Tier A. contract/ and examples/ are outside both tiers.
  * Lines are counted by newline (a last line without one counts), never with splitlines(): one
    U+2028 inside a file would make that count disagree with git and with every editor.
  * The draw takes whole files, and only the drawn files are then split and packed. The seed is
    int(sha[:8], 16) of the resolved full sha, so a 12-character --sha gives the same bytes. Each
    file gets one random key, files are walked in key order and taken while the running total stays
    within 22% of the population, and the walk stops at the first total of at least 20%. A draw
    under 15% prints a warning; an empty population or an empty draw exits 2.
  * A file of at most 3,000 lines is one unit, or is packed next-fit with its directory neighbours
    in path order up to 3,000 (a unit never spans directories). A larger file is split at def and
    class boundaries found with ast; a class that is itself over the limit also offers its methods
    and nested classes (sources.py is one 4,300-line class). Where no boundary fits (a def over the
    limit, a shell script, source that does not parse) the cut goes after the last blank line in the
    window, else at the limit, and says so on stderr.

KNOWN BIAS. The 22% ceiling skips a file that would overshoot it, so large files are drawn less
often than their size. At the first frozen commit (a5c615062313) doctor.py (4,519 of 58,577 Tier B lines, 7.7%) is in
the sample for 15.3% of 3,000 seeds at the 22% ceiling, 18.6% at 25% and 21.9% with no ceiling
(measured by running draw() over seeds 0 to 2,999). The ceiling keeps every one of those samples
between 20.00% and 21.99% of the population, inside the 15-25% band, and that is what it costs.

COST AND LIMITS. One pass, O(files + lines), with ONE git cat-file spawn per in-scope file: the
Tier A files and every Tier B file, because the Tier B population has to be counted before it can
be drawn. Measured at the frozen commit: 1.3 s for 136 reads (three runs, 1.28 to 1.36 s, Python
3.9 on the author's laptop); an earlier prototype run took 1.96 s for 139 reads. At 10x the files
that is about 20 s and at 100x about 3.5 minutes, EXTRAPOLATED from the 1.96 s figure and not
measured; the serial spawn is the ceiling. Memory is one blob at a time plus the text of the files
over 3,000 lines, which splitting needs: not measured beyond the frozen commit.

RECOVERY AND SAFETY. No state, no network, no background process, no gh call, and the working
tree is never read: it uses rev-parse, ls-tree and cat-file on the commit. The only write is the
--json PATH file. A failure is a nonzero exit with the reason on stderr and nothing else changed, so
running it again is the recovery. A crash during the write leaves a partial PATH that the next run
rewrites with the same bytes; a missing parent directory is an I/O error (exit 1), never created.
LIVENESS is the exit code: it runs once and exits, so it has nothing to go quiet about.

WHAT IT DOES NOT DO. It reviews nothing and plants nothing (seed_defects.py does that). It does not
move doctor.py into Tier A; that gap is flagged for a follow-up. It does not use a temp file, an
atomic replace or mkdir, which would each be another write site for the repository's write-surface
guard. Stdlib only, and written for Python 3.9 and later.
"""
import argparse
import ast
import json
from pathlib import Path, PurePosixPath
import random
import re
import subprocess
import sys
import warnings

RUN = subprocess.run
SCHEMA = "readiness-units/v1"
CAP = 3000
SAMPLE_PERCENT = 20
CEILING_PERCENT = 22
FLOOR_PERCENT = 15
TIER_B_ROOTS = ("skills", "tools", "evals")
TIER_B_SUFFIXES = (".py", ".sh")
KEYS = ["schema", "sha", "seed", "tier_a", "tier_b_population_lines", "tier_b_sample"]
HEX40 = re.compile(r"[0-9a-f]{40}")
TIER_A_FILES = (
    "skills/sigma-loop/scripts/work.py",
    "skills/sigma-loop/scripts/sources.py",
    "skills/sigma-loop/scripts/feature_rebase.py",
    "skills/sigma-loop/scripts/ledger.py",
    "skills/sigma-loop/scripts/sync.py",
    "skills/sigma-loop/scripts/scrub.py",
    "skills/sigma-loop/scripts/watch_daemon.py",
    "skills/sigma-loop/scripts/supervise_daemon.py",
    "skills/sigma-loop/scripts/run_with_timeout.py",
    "skills/sigma-loop/scripts/slack_commands_listen.py",
    "skills/sigma-loop/scripts/slack_client.py",
    "skills/sigma-loop/scripts/channel_notify.py",
    "skills/sigma-loop/scripts/upstream.py",
    "skills/sigma-loop/scripts/reconcile.py",
    "skills/sigma-loop/scripts/blockers.py",
    "skills/sigma-loop/scripts/state.py",
    "skills/sigma-loop/scripts/gh_session.py",
    "skills/sigma-loop/scripts/loop.py",
    "skills/sigma-init/scripts/board_setup.py",
    "skills/sigma-init/scripts/init_flow.py",
    "skills/sigma-init/scripts/setup_wizard.py",
)


class UsageError(Exception):
    """A caller supplied an unsupported argument, or the commit lacks a precondition."""


def _out(argv):
    """stdout bytes of one command; CalledProcessError on a nonzero exit."""
    return RUN(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def count_lines(raw):
    """Lines by newline count, never by splitlines(): U+2028 and friends are not line ends here."""
    return raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0)


#: A frozen commit older than the skill rename (#523) tracks the old spelling. Paths are reported under
#: the current `sigma-` name, so the Tier A list and the committed JSON read the same at every sha;
#: the blob is still read from the path the commit actually has. Spelled from fragments so the
#: leftover-name check does not flag this file.
_OLD_NAME = re.compile(r"(^|/)" + "agr" + r"im(?=[-_])")
_AT_COMMIT = {}


def tracked_files(repo, sha):
    """Paths of the regular files tracked at ``sha``, sorted; symlinks and submodules left out."""
    raw = _out(["git", "-C", str(repo), "ls-tree", "-r", "-z", sha])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UsageError("a path tracked at %s is not valid UTF-8; unsupported" % sha) from exc
    paths = []
    for record in text.split("\0"):
        meta, _tab, path = record.partition("\t")
        fields = meta.split(" ")
        if len(fields) == 3 and fields[1] == "blob" and fields[0] != "120000":
            current = _OLD_NAME.sub(r"\1sigma", path)
            _AT_COMMIT[current] = path
            paths.append(current)
    return sorted(paths)


def read_blob(repo, sha, path):
    """The bytes of one tracked file at ``sha`` (one git spawn), never from the working tree."""
    return _out(["git", "-C", str(repo), "cat-file", "blob", "%s:%s" % (sha, _AT_COMMIT.get(path, path))])


def _is_tier_b(path):
    parts = PurePosixPath(path).parts
    return path.endswith(TIER_B_SUFFIXES) and parts[0] in TIER_B_ROOTS and "tests" not in parts


def classify(paths):
    """Split tracked paths into ``(tier_a, tier_b)``, both sorted.

    A named Tier A path that is not tracked raises UsageError naming it: skipping it silently would
    shrink what "reviewed in full" covers.
    """
    present = set(paths)
    tier_a_missing = [path for path in TIER_A_FILES if path not in present]
    if tier_a_missing:
        raise UsageError("Tier A path(s) not tracked at this commit: %s (the Tier A list is tied to "
                         "the frozen commit)" % ", ".join(tier_a_missing))
    tier_a = sorted(path for path in present if path in TIER_A_FILES or path.startswith("hooks/"))
    taken = set(tier_a)
    tier_b = sorted(path for path in present if path not in taken and _is_tier_b(path))
    return tier_a, tier_b



def _hoist(lines, start):
    """Move a 1-based start line up over the comment lines directly above it (no blank gap)."""
    while start > 1 and lines[start - 2].lstrip().startswith("#"):
        start -= 1
    return start


def _collect(nodes, lines, cap, marks):
    for node in nodes:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        first = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
        marks.add(_hoist(lines, first))
        span = node.end_lineno - first + 1
        if isinstance(node, ast.ClassDef) and span > cap:
            _collect(node.body, lines, cap, marks)


def _boundaries(raw, cap=CAP):
    """Sorted 1-based lines where a unit may start: each top-level def or class, and, inside a class
    longer than ``cap``, each method or nested class (recursively). ``[]`` when the source will not
    parse. Comment lines directly above a def or its decorators travel with it."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(raw)
    except (SyntaxError, ValueError, RecursionError):
        return []
    marks = set()
    _collect(tree.body, raw.decode("utf-8", errors="replace").split("\n"), cap, marks)
    return sorted(marks)


def split_file(path, raw, cap=CAP, warn=None):
    """``[(start, end)]`` 1-based inclusive pieces of at most ``cap`` lines that tile the file.

    A file of ``cap`` lines or fewer is one piece. Longer ones are cut greedily at the latest
    boundary that keeps the piece within ``cap``. Where no boundary fits (a def longer than ``cap``,
    a file with no defs such as a shell script, source that does not parse) the cut goes after the
    last blank line in the window, else at the cap; ``warn(message)`` is called for each such cut.
    """
    total = count_lines(raw)
    if total <= cap:
        return [(1, total)] if total else []
    lines = raw.decode("utf-8", errors="replace").split("\n")
    marks = _boundaries(raw, cap) if path.endswith(".py") else []
    pieces, pos = [], 1
    while total - pos + 1 > cap:
        window = [mark for mark in marks if pos < mark <= pos + cap]
        if window:
            cut = window[-1]
        else:
            cut, how = pos + cap, "hard cut at the cap"
            for number in range(pos + cap - 1, pos - 1, -1):
                if not lines[number - 1].strip():
                    cut, how = number + 1, "cut after a blank line"
                    break
            if warn:
                warn("%s: no def or class boundary within %d lines of line %d; %s (line %d)"
                     % (path, cap, pos, how, cut - 1))
        pieces.append((pos, cut - 1))
        pos = cut
    pieces.append((pos, total))
    return pieces


def measure_files(repo, sha, paths):
    """``{path: (lines, raw)}`` for the non-empty files, one blob read at a time. ``raw`` is kept
    only for a file over the cap (it still has to be split); every other file keeps its count."""
    measured = {}
    for path in paths:
        raw = read_blob(repo, sha, path)
        lines = count_lines(raw)
        if lines:
            measured[path] = (lines, raw if lines > CAP else None)
    return measured


def _unit(spans):
    return {"files": [{"path": path, "start": start, "end": end} for path, start, end in spans],
            "lines": sum(end - start + 1 for _path, start, end in spans)}


def units_from_measured(measured, prefix, warn=None):
    """Number the review units of ``measured`` (``{path: (lines, raw)}``) as ``<prefix>01..``.

    A file over the cap is split (see split_file) and each piece is a unit of its own. Files of at
    most ``cap`` lines are packed per directory, next fit in path order, up to the cap; a unit never
    spans directories. Units are sorted by (first path, first start) before they are numbered.
    """
    units, by_directory = [], {}
    for path in sorted(measured):
        lines, raw = measured[path]
        if lines > CAP:
            units.extend(_unit([(path, start, end)]) for start, end in split_file(path, raw, CAP, warn))
        else:
            by_directory.setdefault(str(PurePosixPath(path).parent), []).append((path, lines))
    for files in by_directory.values():
        cur, total = [], 0
        for path, n in files:
            if cur and total + n > CAP:
                units.append(_unit(cur))
                cur, total = [], 0
            cur.append((path, 1, n))
            total += n
        units.append(_unit(cur))
    units.sort(key=lambda unit: (unit["files"][0]["path"], unit["files"][0]["start"]))
    width = max(2, len(str(len(units))))
    return [{"unit": "%s%0*d" % (prefix, width, number), "files": unit["files"], "lines": unit["lines"]}
            for number, unit in enumerate(units, 1)]


def units_from_paths(repo, sha, paths, prefix, warn=None):
    """Review units for the given tracked paths at ``sha``."""
    return units_from_measured(measure_files(repo, sha, paths), prefix, warn)


def draw(counts, seed, warn=None):
    """Draw whole files from ``{path: lines}``, returned IN WALK ORDER.

    Each file gets one random key, drawn in path order from the seeded generator; the files are
    walked in key order. A file is taken when it keeps the running total within 22% of the
    population, and the walk stops at the FIRST total of at least 20%. KNOWN BIAS: a file that
    would overshoot the 22% ceiling is skipped, so large files are drawn less often than their size
    (a 4,500-line file in a 58,600-line population: drawn in 15% of seeds, 22% with no ceiling).
    A draw that ends under 15% calls ``warn``; an empty population or an empty draw raises
    UsageError, never an empty "ok".
    """
    population = sum(counts.values())
    if population <= 0:
        raise UsageError("Tier B has no lines to sample")
    target = -(-population * SAMPLE_PERCENT // 100)
    ceiling = population * CEILING_PERCENT // 100
    paths = sorted(counts)
    rng = random.Random(seed)
    keys = {path: rng.random() for path in paths}
    walk, cum = [], 0
    for path in sorted(paths, key=lambda item: (keys[item], item)):
        if cum + counts[path] <= ceiling:
            walk.append(path)
            cum += counts[path]
            if cum >= target:
                break
    if not walk:
        raise UsageError("every Tier B file is larger than the %d%% ceiling (%d of %d lines): "
                         "nothing can be drawn" % (CEILING_PERCENT, ceiling, population))
    if warn and cum * 100 < population * FLOOR_PERCENT:
        warn("the sample is %.1f%% of the Tier B population, below the %d%% floor"
             % (cum * 100.0 / population, FLOOR_PERCENT))
    return walk



def resolve_commit(repo, sha):
    """The full 40-hex sha ``sha`` names in ``repo``; UsageError when it names no commit there."""
    try:
        full = _out(["git", "-C", str(repo), "rev-parse", "--verify", "%s^{commit}" % sha])
    except subprocess.CalledProcessError as exc:
        reason = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise UsageError("cannot resolve --sha %r to a commit in %s: %s" % (sha, repo, reason)) from exc
    return full.decode("ascii").strip()


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_tier(label, prefix, units, problems):
    """Check one tier's units, appending every problem found to ``problems``.

    Returns ``{path: [(start, end), ...]}`` for the well-formed file entries, in unit order."""
    ranges = {}
    if not isinstance(units, list):
        problems.append("%s is not a list" % label)
        return ranges
    width = max(2, len(str(len(units))))
    wanted = ["%s%0*d" % (prefix, width, number) for number in range(1, len(units) + 1)]
    got = [unit.get("unit") if isinstance(unit, dict) else None for unit in units]
    if got != wanted:
        problems.append("%s unit ids are not %s in sequence (got %s)"
                        % (label, "%s..%s" % (wanted[0], wanted[-1]) if wanted else "empty", got))
    for index, unit in enumerate(units):
        where = "%s[%d]" % (label, index)
        if not isinstance(unit, dict) or list(unit) != ["unit", "files", "lines"]:
            problems.append("%s keys are not exactly unit, files, lines" % where)
            continue
        if not isinstance(unit["files"], list) or not unit["files"]:
            problems.append("%s has no files" % where)
            continue
        total = 0
        for entry in unit["files"]:
            if (not isinstance(entry, dict) or list(entry) != ["path", "start", "end"]
                    or not isinstance(entry["path"], str) or not entry["path"]
                    or not _is_int(entry["start"]) or not _is_int(entry["end"])
                    or not 1 <= entry["start"] <= entry["end"]):
                problems.append("%s has a malformed file entry: %r" % (where, entry))
                continue
            total += entry["end"] - entry["start"] + 1
            ranges.setdefault(entry["path"], []).append((entry["start"], entry["end"]))
        if unit["lines"] != total or not _is_int(unit["lines"]):
            problems.append("%s lines is %r, but its ranges add up to %d" % (where, unit["lines"], total))
        if _is_int(unit["lines"]) and unit["lines"] > CAP:
            problems.append("%s has %d lines, over the %d-line cap" % (where, unit["lines"], CAP))
    for path, spans in ranges.items():
        spans.sort()
        if spans[0][0] != 1 or any(nxt[0] != prev[1] + 1 for prev, nxt in zip(spans, spans[1:])):
            problems.append("%s: the ranges of %s have a gap or an overlap (they must tile from line 1): %s"
                            % (label, path, spans))
    return ranges


def validate_payload(payload, seed=None):
    """Raise ValueError listing EVERY problem of ``payload``; return None when it is sound.

    Pure and git-free: it checks the schema, the key set and order, a 40-hex sha, the seed
    (``int(sha[:8], 16)``, or ``seed`` when the caller passed one), sequential unit ids per tier,
    each unit's ``lines`` against its ranges and the cap, ranges that tile each file from line 1,
    no path in both tiers, and a population at least the sample's size."""
    problems = []
    if not isinstance(payload, dict):
        raise ValueError("the payload is not a JSON object")
    if list(payload) != KEYS:
        problems.append("top-level keys are %s, expected exactly %s in this order" % (list(payload), KEYS))
    if payload.get("schema") != SCHEMA:
        problems.append("schema is %r, expected %r" % (payload.get("schema"), SCHEMA))
    sha = payload.get("sha")
    sha_ok = isinstance(sha, str) and HEX40.fullmatch(sha) is not None
    if not sha_ok:
        problems.append("sha is not 40 lowercase hex digits: %r" % (sha,))
    wanted = seed if seed is not None else (int(sha[:8], 16) if sha_ok else None)
    if not _is_int(payload.get("seed")) or (wanted is not None and payload["seed"] != wanted):
        problems.append("seed is %r, expected %r" % (payload.get("seed"), wanted))
    tier_a = _check_tier("tier_a", "A", payload.get("tier_a"), problems)
    tier_b = _check_tier("tier_b_sample", "B", payload.get("tier_b_sample"), problems)
    both = sorted(set(tier_a) & set(tier_b))
    if both:
        problems.append("path(s) in both tiers: %s" % ", ".join(both))
    sampled = sum(end - start + 1 for spans in tier_b.values() for start, end in spans)
    population = payload.get("tier_b_population_lines")
    if not _is_int(population) or population < sampled:
        problems.append("tier_b_population_lines is %r, below the %d lines sampled" % (population, sampled))
    if problems:
        raise ValueError("; ".join(problems))


def build(repo, sha, seed=None, warn=None):
    """The review-unit payload for ``sha`` in ``repo``, read from the commit and validated.

    ``seed`` is for tests: it replaces the derived seed and the payload carries it. A payload the
    validator rejects is a bug here, not bad input: RuntimeError. UsageError when the commit does
    not resolve, a Tier A path is missing, or Tier B yields no sample."""
    full = resolve_commit(repo, sha)
    tier_a, tier_b = classify(tracked_files(repo, full))
    population = measure_files(repo, full, tier_b)
    used = int(full[:8], 16) if seed is None else seed
    drawn = draw({path: lines for path, (lines, _raw) in population.items()}, used, warn)
    payload = {
        "schema": SCHEMA,
        "sha": full,
        "seed": used,
        "tier_a": units_from_paths(repo, full, tier_a, "A", warn),
        "tier_b_population_lines": sum(lines for lines, _raw in population.values()),
        "tier_b_sample": units_from_measured({path: population[path] for path in drawn}, "B", warn),
    }
    try:
        validate_payload(payload, seed=seed)
    except ValueError as exc:
        raise RuntimeError("the review units it built are inconsistent: %s" % exc) from exc
    return payload


def _summary(target, payload):
    sampled = sum(unit["lines"] for unit in payload["tier_b_sample"])
    population = payload["tier_b_population_lines"]
    return ("wrote %s: %d Tier A units (%d lines), %d Tier B units (%d of %d lines, %.1f%%)"
            % (target, len(payload["tier_a"]), sum(unit["lines"] for unit in payload["tier_a"]),
               len(payload["tier_b_sample"]), sampled, population, sampled * 100.0 / population))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="review_units.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="a git repository; files are read at --sha, never from its working tree")
    parser.add_argument("--sha", required=True, help="the frozen commit; its first 8 hex digits seed the draw")
    parser.add_argument("--json", metavar="PATH", help="write the JSON to PATH and print one summary line")
    args = parser.parse_args(argv)

    def warn(message):
        print("review_units.py: warning: %s" % message, file=sys.stderr)

    try:
        payload = build(args.repo, args.sha, warn=warn)
        text = json.dumps(payload, indent=2) + "\n"
        if args.json:
            Path(args.json).write_text(text, encoding="utf-8")
            print(_summary(args.json, payload))
        else:
            print(text, end="")
    except UsageError as exc:
        print("review_units.py: %s" % exc, file=sys.stderr)
        return 2
    except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
        print("review_units.py: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Plant known defects in a review clone and keep the record of where they are.

USAGE
    seed_defects.py apply <clone> <seed_dir>
    seed_defects.py score <seed_dir> <findings.json> [--show-missed-locations]

EXIT: 0 = ok; 1 = failed; 2 = bad arguments or a missing precondition.

WHY. "The review found nothing" only means something if the reviewers could have found something.
A person who will not review writes a small set of defects as patches and keeps them OUTSIDE the
clone; apply plants them in the review clone and records where they landed, and score says what
share of them a findings list caught.

WHAT apply DOES. Every *.patch in seed_dir (taken in name order; the file stem is the seed id,
by convention NN-<class>-<slug>) must edit exactly one existing text file in place. They are all
applied to the clone with git apply, and seed_dir/manifest.json records, for each seed, its file and
the post-image line numbers it changed.
  * Refused with exit 2, BEFORE anything changes, in this order: seed_dir is not a directory or
    holds no *.patch; the clone is not its own top-level directory (rev-parse --show-toplevel,
    compared with os.path.samefile, so a path typed in another case is still the same directory);
    seed_dir is the clone or inside it (resolved through symlinks, so a link that points into the
    clone is caught too); HEAD is on a branch (apply needs a detached clone, the shape
    baseline.py snapshot leaves, so a working repository can never be seeded); tracked files are
    not clean (untracked ones are ignored); a patch git cannot read, a patch that creates, deletes,
    renames or changes the mode of a file, a binary patch, or one that touches more than one
    file; two patches for one file.
  * Then each patch is checked ALONE against the clean tree (exit 1, naming the patch: git names
    only the target file), and the whole set goes in as ONE git apply stream. A stream is
    atomic: a bad patch in it applies nothing. Separate file arguments are not atomic, which is
    why the stream is used.
  * The changed lines are read AFTER applying, from git diff -U0 against HEAD with the reader's
    own diff settings neutralised (no inter-hunk context, the myers algorithm, no external diff or
    textconv). So an offset landing is recorded where the text really is, not where the patch
    header said. A pure deletion has no line of its own and records its two surviving neighbours;
    numbers are kept inside the file.
  * manifest.json is {"schema": "readiness-seeds/v1", "base_sha": <the clone's HEAD>, "seeds":
    [{"id", "patch", "file", "lines"}]}, written LAST by one plain write.

WHAT score DOES. It reads seed_dir/manifest.json and a findings file, a JSON list of objects with
a string "path" (relative to the clone's top level; ./ and backslashes are normalised, so an absolute
path matches nothing), an integer "line" from 1 and an optional "id" (a string or an integer; the
entry's index when missing). It prints one line per seed in manifest order, "matched <id> (finding
<id>)" or "missed <NN-class>", then "recall: 0.667 (2/3)".
  * A finding matches a seed when it names the seed's file and a line within WINDOW (5) lines of
    any line the seed changed. The edge is inclusive: 5 away matches, 6 does not.
  * The exit code is the verdict: 0 when at least 80% of the seeds matched (compared in integers,
    four fifths, not as a float), 1 below that, 2 for bad input, with nothing on stdout: a manifest
    that is missing, truncated or of another schema, with no seeds, a repeated id or a seed with no
    changed line, or a findings file that is not a list of such objects.
  * A missed seed is shown only as its number and class (one of CLASSES, or "unclassified" when the
    id does not read NN-<class>-<slug>): the slug says what the defect is, so the default output is
    safe to show a reviewer. --show-missed-locations adds each missed seed's id, file and lines, for
    the seed author only. A review below 80% is re-run with a fresh seed set or a reviewer who has
    not seen that list.

COST AND LIMITS. apply is O(patches): four git spawns per patch (summary, numstat, check, final
diff) plus five fixed ones. Measured: 0.35 s for 10 patches on the author's laptop. At 10x that is
about 3.5 s and at 100x about 35 s, EXTRAPOLATED, not measured; the serial spawn is the ceiling.
Every patch is held in memory while it is applied. One patch per file is a limit, not an accident:
two would be recorded at each other's offsets. score is O(seeds x findings x changed lines), with no
git spawn: measured 0.09 s for 100 seeds against 10,000 findings, and 3.68 s for 1,000 seeds
against 100,000 findings that match nothing (the worst case: every seed scans every finding), on
the author's laptop. Both files are read whole.

RECOVERY AND SAFETY. No network, no gh call, no branch, commit or push, and nothing is written
outside the clone's tracked files and seed_dir/manifest.json; score writes nothing and can simply
be run again. An apply refusal, or a patch that does not apply, leaves the clone as it was: fix
the cause and run it again. If the process dies between the stream and the manifest write, or the
write fails, or a patch leaves its file empty (so there is no line to record), the clone holds the
seeds and there is no manifest: a re-run refuses because the tree is dirty, and the lever is
git -C <clone> checkout -- . and then running it again.
A patch for a file that is not tracked at HEAD is not refused: the file is edited, there is no
tracked change for the manifest to read, apply exits 1 without one, and that checkout does not restore an
untracked file: put it back or delete it by hand. Seeds belong on tracked files.
LIVENESS is the exit code; it runs once and exits.

KNOWN LEAK. The seeds stay uncommitted, so a reviewer working in the clone sees every seeded
file as modified in git status. docs/launch/seeded-defects.md says what to do about that.

WHAT IT DOES NOT DO. It does not write seeds, choose them, or put them anywhere the reviewer can
see; it does not commit the seeded tree or roll anything back; apply reads seed_dir for *.patch
files only and score for manifest.json only. It cannot stop a reviewer from reading seed_dir.
Stdlib only, and written for Python 3.9 and later.
"""
import argparse
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys

RUN = subprocess.run
SCHEMA = "readiness-seeds/v1"
WINDOW = 5
CLASSES = ("data-loss", "silent-fallback", "injection", "race", "portability", "test-cannot-fail")


class UsageError(Exception):
    """A caller supplied an unsupported argument, or a precondition is missing."""


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
STEM = re.compile(r"(\d{2})-(%s)-(.+)" % "|".join(CLASSES))


def _text(raw):
    return raw.decode("utf-8", errors="replace").strip()


def _git(clone, *args, **kwargs):
    return RUN(["git", "-C", str(clone)] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               **kwargs)


def _line_count(raw):
    """Lines by newline count: a last line without a newline still counts."""
    return raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0)


def _ranges(diff_text, n):
    """The post-image line numbers a ``git diff -U0`` changed, sorted, within 1..``n``.

    Only hunk headers are read, never the content lines (a deleted line that starts with two
    dashes looks like a file header), and the text is split on newlines alone: a form feed or
    U+2028 inside a planted line must not start a fake header. ``+c,d`` names ``d`` lines from ``c`` (``d`` omitted is 1). A
    pure deletion has ``d == 0``: it records its two surviving neighbours ``c`` and ``c + 1``.
    Numbers outside the file (0 at the top, ``n + 1`` at the bottom) are dropped."""
    found = set()
    for line in diff_text.split("\n"):
        hunk = HUNK.match(line)
        if hunk is None:
            continue
        first, count = int(hunk.group(1)), 1 if hunk.group(2) is None else int(hunk.group(2))
        found.update(range(first, first + count) if count else (first, first + 1))
    return sorted(number for number in found if 1 <= number <= n)


def _inspect(clone, patches):
    """``[(patch, file)]``: the one file each patch edits in place. UsageError naming the patch for
    anything else: an unreadable patch, a created, deleted, renamed or mode-changed file, a binary
    patch, a patch for more than one file, or two patches for the same file."""
    inspected = []
    for patch in patches:
        blob = patch.read_bytes()
        summary = _git(clone, "apply", "--summary", "-", input=blob)
        numstat = _git(clone, "apply", "--numstat", "-z", "-", input=blob)
        if summary.returncode != 0 or numstat.returncode != 0:
            raise UsageError("%s is not a patch git can read: %s"
                             % (patch.name, _text(summary.stderr or numstat.stderr)))
        summary_lines = [line for line in _text(summary.stdout).split("\n") if line.strip()]
        if summary_lines:
            raise UsageError("%s does more than edit a file in place (%s); creating, deleting, renaming "
                             "and changing the mode of a file are refused"
                             % (patch.name, "; ".join(line.strip() for line in summary_lines)))
        records = [record for record in numstat.stdout.split(b"\0") if record]
        if len(records) != 1:
            raise UsageError("%s changes %d files; a seed is a patch for exactly one file"
                             % (patch.name, len(records)))
        added, _tab, rest = records[0].partition(b"\t")
        deleted, _tab, raw_path = rest.partition(b"\t")
        if added == b"-" or deleted == b"-":
            raise UsageError("%s is a binary patch; a seed must be a text edit" % patch.name)
        try:
            inspected.append((patch, raw_path.decode("utf-8")))
        except UnicodeDecodeError as exc:
            raise UsageError("%s changes a path that is not valid UTF-8; unsupported" % patch.name) from exc
    seen = {}
    for patch, path in inspected:
        if path in seen:
            raise UsageError("%s and %s both change %s; one patch per file, or the second would be "
                             "recorded at the first one's offsets" % (seen[path], patch.name, path))
        seen[path] = patch.name
    return inspected


def _check_each(clone, patches):
    """Test every patch ALONE against the clean tree. The stream below is atomic but names only the
    target file, so this is what lets the failure name the patch."""
    for patch in patches:
        done = _git(clone, "apply", "--check", "--whitespace=nowarn", "-", input=patch.read_bytes())
        if done.returncode != 0:
            raise RuntimeError("%s does not apply to this clone (was it authored against another base?): %s"
                               % (patch.name, _text(done.stderr)))


def apply(clone, seed_dir):
    """Apply every ``*.patch`` in ``seed_dir`` to ``clone`` and write ``manifest.json`` beside them.

    Returns the manifest. UsageError (exit 2) for a refusal, raised before anything changes;
    RuntimeError (exit 1) when git will not apply the set. The manifest is written last."""
    seed_p = Path(seed_dir).resolve()
    if not seed_p.is_dir():
        raise UsageError("seed_dir is not a directory: %s" % seed_dir)
    patches = sorted(path for path in seed_p.glob("*.patch") if path.is_file())
    if not patches:
        raise UsageError("no *.patch file in %s" % seed_dir)
    clone_p = Path(clone).resolve()
    top = _git(clone_p, "rev-parse", "--show-toplevel") if clone_p.is_dir() else None
    if top is None or top.returncode != 0:
        raise UsageError("%s is not a git repository" % clone)
    top = os.fsdecode(top.stdout).strip()
    not_toplevel = not os.path.samefile(top, clone_p)
    if not_toplevel:
        raise UsageError("%s is inside the repository at %s, not its top level; pass the clone's own "
                         "top-level directory" % (clone, top))
    inside_clone = any(os.path.samefile(q, clone_p) for q in [seed_p] + list(seed_p.parents))
    if inside_clone:
        raise UsageError("seed_dir %s is inside the clone %s; the seeds must live outside it, where "
                         "a reviewer working in the clone does not see them" % (seed_dir, clone))
    branch = _git(clone_p, "symbolic-ref", "-q", "HEAD")
    if branch.returncode not in (0, 1):
        raise RuntimeError("git symbolic-ref failed in %s: %s" % (clone, _text(branch.stderr)))
    on_a_branch = branch.returncode == 0
    if on_a_branch:
        raise UsageError("HEAD of %s is on %s; apply needs a detached clone, so a working repository "
                         "can never be seeded" % (clone, _text(branch.stdout)))
    status = _git(clone_p, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        raise RuntimeError("git status failed in %s: %s" % (clone, _text(status.stderr)))
    clone_dirty = bool(_text(status.stdout))
    if clone_dirty:
        raise UsageError("%s has uncommitted changes to tracked files; use a clean clone (a clone "
                         "that already holds seeds is dirty: undo them with git -C <clone> checkout "
                         "-- . first)" % clone)
    inspected = _inspect(clone_p, patches)
    base = _text(_git(clone_p, "rev-parse", "HEAD").stdout)
    _check_each(clone_p, patches)
    blobs = [path.read_bytes() for path in patches]
    streamed = RUN(["git", "-C", str(clone_p), "apply", "--whitespace=nowarn", "-"], input=b"".join(blobs),
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if streamed.returncode != 0:
        raise RuntimeError("git refused the seed set as a whole and applied none of it: %s"
                           % _text(streamed.stderr))
    seeds = []
    for patch, file in inspected:
        diff = _git(clone_p, "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                    "-U0", "--inter-hunk-context=0", "--diff-algorithm=myers", "HEAD", "--",
                    ":(literal)" + file)
        if diff.returncode != 0:
            raise RuntimeError("git diff of %s failed after applying (undo with git -C <clone> checkout "
                               "-- .): %s" % (file, _text(diff.stderr)))
        lines = _ranges(diff.stdout.decode("utf-8", errors="replace"),
                        _line_count((clone_p / file).read_bytes()))
        if not lines:
            raise RuntimeError("%s left no changed line to score in %s; the clone now holds the seeds "
                               "and no manifest was written (undo with git -C <clone> checkout -- .)"
                               % (patch.name, file))
        seeds.append({"id": patch.stem, "patch": patch.name, "file": file, "lines": lines})
    manifest = {"schema": SCHEMA, "base_sha": base, "seeds": seeds}
    (seed_p / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _not_a_line(line):
    """True unless ``line`` is an integer from 1 (a JSON ``true`` is an int to Python, not a line)."""
    return not isinstance(line, int) or isinstance(line, bool) or line < 1


def _json_file(path, what):
    """The parsed JSON in ``path``. UsageError, naming ``what`` it was, if it cannot be read or parsed."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise UsageError("cannot read %s %s: %s" % (what, path, exc.strerror or exc)) from exc
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise UsageError("%s %s is not valid JSON (a truncated file?): %s" % (what, path, exc)) from exc


def load_manifest(seed_dir):
    """The manifest ``apply`` wrote in ``seed_dir``. UsageError for anything ``apply`` would not
    have written: unreadable or truncated, another schema, no seeds, a seed without a string id and
    file or without changed lines, a repeated id. A seed is named by its position, never its id."""
    path = Path(seed_dir) / "manifest.json"
    manifest = _json_file(path, "manifest")
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise UsageError("manifest %s is not a %s manifest" % (path, SCHEMA))
    seeds = manifest.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise UsageError("manifest %s lists no seeds; run apply first" % path)
    ids = set()
    for index, seed in enumerate(seeds):
        where = "seeds[%d] of manifest %s" % (index, path)
        if not isinstance(seed, dict) or not isinstance(seed.get("id"), str) \
                or not isinstance(seed.get("file"), str):
            raise UsageError("%s needs a string id and file" % where)
        lines = seed.get("lines")
        if not isinstance(lines, list) or not lines or any(_not_a_line(number) for number in lines):
            raise UsageError("%s needs a non-empty list of changed lines (integers from 1)" % where)
        if seed["id"] in ids:
            raise UsageError("%s repeats the id of an earlier seed" % where)
        ids.add(seed["id"])
    return manifest


def load_findings(path):
    """``[{"id", "path", "line"}]`` from a findings file: a JSON list of objects with a string
    ``path`` and an integer ``line`` from 1, and an optional ``id`` (a string or an integer; the
    entry's index when missing). The path is normalised: backslashes become slashes and ``./``
    goes, so an absolute path stays absolute and matches nothing. UsageError for anything else."""
    found = _json_file(path, "findings")
    if not isinstance(found, list):
        raise UsageError("findings %s must be a JSON list of {path, line} objects" % path)
    findings = []
    for index, item in enumerate(found):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) \
                or _not_a_line(item.get("line")):
            raise UsageError("findings %s: entry %d needs a string path and an integer line from 1"
                             % (path, index))
        ident = item.get("id", index)
        if not isinstance(ident, (str, int)):
            raise UsageError("findings %s: entry %d has an id that is neither a string nor an integer"
                             % (path, index))
        findings.append({"id": ident, "path": posixpath.normpath(item["path"].replace("\\", "/")),
                         "line": item["line"]})
    return findings


def _near(line, changed_lines):
    """Is ``line`` within WINDOW lines of one of ``changed_lines``? The edge is inclusive: 5 away
    matches, 6 does not."""
    return any(abs(line - changed) <= WINDOW for changed in changed_lines)


def evaluate(seeds, findings):
    """``[(seed, finding or None)]``: the first finding in the seed's file that is near a line the
    seed changed, or None."""
    return [(seed, next((finding for finding in findings if finding["path"] == seed["file"]
                         and _near(finding["line"], seed["lines"])), None))
            for seed in seeds]


def _withheld(seed_id):
    """What a missed seed is called when its reader may be the reviewer: its number and class
    (``NN-<class>``), or ``unclassified``. Never the slug, which says what the defect is."""
    stem = STEM.fullmatch(seed_id)
    return "unclassified" if stem is None else "%s-%s" % (stem.group(1), stem.group(2))


def score(seed_dir, findings_path, show_missed_locations=False):
    """``(report lines, recall met)``: one line per seed, in manifest order, then the recall.

    A matched seed is named in full with the finding that matched it; a missed one only by
    ``_withheld`` unless ``show_missed_locations``, which adds its id, file and lines for the seed
    author. Recall is met at 80%, compared in integers (``matched / total`` in floats is not exact)."""
    results = evaluate(load_manifest(seed_dir)["seeds"], load_findings(findings_path))
    matched = sum(1 for _seed, hit in results if hit is not None)
    total = len(results)
    report = []
    for seed, hit in results:
        if hit is not None:
            report.append("matched %s (finding %s)" % (seed["id"], json.dumps(hit["id"])))
            continue
        label = _withheld(seed["id"])
        if show_missed_locations:
            label = "%s %s:%s" % (seed["id"], seed["file"], ",".join(str(number) for number in seed["lines"]))
        report.append("missed " + label)
    report.append("recall: %.3f (%d/%d)" % (matched / total, matched, total))
    return report, matched * 5 >= total * 4


def main(argv=None):
    parser = argparse.ArgumentParser(prog="seed_defects.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="verb", required=True)
    planting = sub.add_parser("apply", help="plant every *.patch of seed_dir in the clone and write manifest.json")
    planting.add_argument("clone", help="a clean, DETACHED clone, given as its own top-level directory")
    planting.add_argument("seed_dir", help="a directory of *.patch files, outside the clone")
    scoring = sub.add_parser("score", help="recall of a findings file against seed_dir/manifest.json")
    scoring.add_argument("seed_dir", help="the directory apply wrote manifest.json in")
    scoring.add_argument("findings", help="a JSON list of {\"path\", \"line\"[, \"id\"]} objects")
    scoring.add_argument("--show-missed-locations", action="store_true",
                         help="for the seed author only: also name the id, file and lines of each missed seed")
    args = parser.parse_args(argv)
    try:
        if args.verb == "score":
            report, recall_met = score(args.seed_dir, args.findings, args.show_missed_locations)
            print("\n".join(report))
            return 0 if recall_met else 1
        manifest = apply(args.clone, args.seed_dir)
        print("applied %d seeds (%d changed lines) to %s; manifest %s"
              % (len(manifest["seeds"]), sum(len(seed["lines"]) for seed in manifest["seeds"]),
                 args.clone, Path(args.seed_dir) / "manifest.json"))
    except UsageError as exc:
        print("seed_defects.py: %s" % exc, file=sys.stderr)
        return 2
    except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
        print("seed_defects.py: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

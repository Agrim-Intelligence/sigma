# Seeded defects: what a review that found nothing means

"The review found nothing" only means something if the review could have found something. This
page is the protocol that checks it. Someone who is not the reviewer writes a small set of known
defects as patches and keeps them away from the reviewer. They are planted in the review clone, the
review runs, and its findings are scored: the share of the planted defects it reported is its
recall. Two tools do the mechanical parts, and each states its cost, its failure modes and how it
recovers in its module docstring, as [AGENTS.md](../../AGENTS.md) requires:
[`review_units.py`](../../tools/readiness/review_units.py) fixes what is reviewed, and
[`seed_defects.py`](../../tools/readiness/seed_defects.py) plants and scores the seeds. A reviewer
hands in a findings file (below) and is never shown the seeds, the manifest or the missed-seed list.

## Who writes the seeds, and where they live

A person or an agent session that will not take part in the review writes the seeds. This goal
(#352) shipped the two tools and three made-up examples, not a real seed set: the real set is
written for each review.

The seed directory is `~/.sigma-ops/readiness/seeds/<sha12>/`, where `<sha12>` is the first 12
digits of the commit under review. It is outside the clone, and not under `~/Documents`, where macOS
can refuse a terminal access to a new folder. `apply` refuses a seed directory that is the clone or
inside it. Nothing stops a reviewer from reading the directory: the separation is a process rule,
and the tool cannot enforce it.

## What is reviewed

`review_units.py` turns one frozen commit into a fixed list of review units of at most 3,000 lines.
The frozen commit is `a5c615062313`, the baseline pinned by #332 (closed). The units for it are in
[`review-units.json`](review-units.json), made by this gesture, run from the repository root:

```
python3 tools/readiness/review_units.py . --sha a5c615062313 --json docs/launch/review-units.json
```

- **Tier A is reviewed in full:** every tracked file under `hooks/` and the 22 files named in the
  tool's `TIER_A_FILES`. The list is tied to the frozen commit. A commit that has since deleted one
  of those files is refused with exit 2 rather than reviewed with less: `install.sh` is gone from
  main, so `--sha HEAD` there exits 2 and names it.
- **Tier B is sampled:** tracked `.py` and `.sh` files under `skills/`, `tools/` and `evals/` that
  are not tests and not in Tier A (`contract/` and `examples/` are outside both tiers). Whole files
  are drawn with `random.Random`, seeded with the first 8 hex digits of the commit read as an
  integer, until the sample holds at least 20% of the population's lines. The same commit always gives the
  same bytes.
- A file over 3,000 lines is split at `def` and `class` boundaries; a class that is itself too long
  offers its methods, because `sources.py` is one 4,300-line class. A smaller file is one unit, or
  is packed with its directory neighbours.

At the frozen commit Tier A is 17 units, 36,018 lines, the largest 2,997. The Tier B population is
58,577 lines and the sample is 15 files, 11,972 lines, 20.44% of it. The 88.1k lines the request
quoted is 94,876 at this commit: the [inventory](evidence/inventory-a5c615062313.json) counts 139
non-test Python and shell files.

Two limits of the sample:

- **Large files are under-drawn.** The draw skips a file that would take the total past 22% of the
  population, so large files are picked less often than their size implies. `doctor.py` is 7.7% of the
  Tier B lines and is in the sample for 15.3% of 3,000 seeds at the 22% ceiling, 18.6% at 25% and
  21.9% with no ceiling. 22% is kept because every sample then lands between 20.00% and 21.99%,
  inside the 15-25% band.
- **`doctor.py` is sampled, not in Tier A.** It is 4,519 lines at the frozen commit and runs on
  every machine. This sample happens to include it. Whether it joins Tier A is an owner decision, still
  pending (see #412).

## Seed rules

1. About 10 seeds, at least one for each class: `data-loss` (a write that loses or truncates what it
   should keep), `silent-fallback` (a failure swallowed into a default), `injection` (untrusted text
   reaching a shell, an eval or a query), `race` (a check and a use another process can separate),
   `portability` (an assumption that breaks on another OS, shell or Python) and `test-cannot-fail`
   (a test or guard that passes against the bug it targets).
2. Each seed is one patch against one file, inside the line range of a Tier A unit or a unit of the
   Tier B sample. A seed in a file nobody was assigned cannot be found and lowers recall for nothing.
3. Small and plausible: a few lines that read like a real mistake, with no comment that names it.
4. One patch per file. Two would be recorded at each other's offsets, and `apply` refuses it.
5. An in-place edit of an existing text file: no created, deleted or renamed file, no mode change, no
   binary, no patch for two files. `apply` refuses each of these.
6. Changed regions of different seeds more than 10 lines apart. A finding matches any seed within 5
   lines, so one finding between two closer seeds would count for both.
7. Made against the exact commit the clone is on: edit a scratch copy, then save the output of
   `git diff --no-ext-diff --src-prefix=a/ --dst-prefix=b/`.
8. Named `NN-<class>-<slug>.patch`, with `NN` two digits and `<class>` one of the six above. The
   file stem is the seed id, and `score` shows a missed seed only as `NN-<class>`.

## Planting and scoring

```
python3 tools/readiness/seed_defects.py apply <clone> <seed_dir>
python3 tools/readiness/seed_defects.py score <seed_dir> <findings.json>
python3 tools/readiness/seed_defects.py score <seed_dir> <findings.json> --show-missed-locations
```

`apply` needs a clean, DETACHED clone of the frozen commit, given as its own top-level directory.
It applies every `*.patch` in the seed directory as one atomic `git apply` and writes
`manifest.json` there (schema `readiness-seeds/v1`): the clone's HEAD as `base_sha`, and for each
seed its file and the post-image line numbers it changed. It exits 2 and changes nothing when the
seed directory is inside the clone, the clone is on a branch, has uncommitted changes to tracked
files or is not its own top-level directory, there is no patch, a patch is not an in-place edit of
one file, or two patches share a file. A patch that does not apply exits 1 and names the patch. If
`apply` died after planting but before writing the manifest, a re-run refuses because the tree is
dirty; undo with `git -C <clone> checkout -- .` and run it again.
A patch for a file that is not tracked at HEAD is not refused: it edits the file, `apply` exits 1
without a manifest, and that checkout does not restore an untracked file, so put it back or delete it
by hand. Seeds belong on tracked files.

The findings file is a JSON list. Each entry has a `path` (relative to the clone's top level), a
`line` (an integer from 1) and optionally an `id` (a string or an integer; `score` echoes it, and
uses the entry's position when there is none):

```
[{"path": "tools/report_save.py", "line": 10}, {"path": "tools/report_load.py", "line": 12}]
```

A finding matches a seed when it names the seed's file and a line within 5 lines of any line the
seed changed. The edge is inclusive: 5 away matches, 6 does not. Recall is matched seeds divided by
total seeds. The exit code is the verdict: 0 at 80% or more (compared in integers, four fifths), 1
below, 2 for bad input (a missing or truncated manifest, a findings file that is not a list of such
entries) with nothing on stdout. On the three examples below, two findings give:

```
matched 01-data-loss-truncate-in-place (finding 0)
matched 02-silent-fallback-swallow-bad-json (finding 1)
missed 03-injection
recall: 0.667 (2/3)
```

Below 80% the review is re-run, not reported. The missed list tells the next reviewer where to
look, so it goes to the seed author only: a re-run uses a fresh seed set, or a reviewer who has not
seen the list. The default output is safe to show a reviewer, since a missed seed is `NN-<class>`
(`unclassified` if the id does not read that way), never its slug, file or line.
`--show-missed-locations` is for the seed author and adds them, as in this last line of the sample:

```
missed 03-injection-shell-tar tools/report_pack.py:8
```

## Known leak

`apply` leaves the seeds as uncommitted edits, so `git status` in the review clone lists every
seeded file as modified and `git diff` shows each planted defect. A reviewer who never runs those
commands still sees it: Claude Code puts a snapshot of `git status` into the session at start, so a
reviewer opened in the clone sees every seeded file as ` M` unasked.

An optional step for the seed author, after `apply` and before the review starts, commits the seeded
tree as one commit that reuses HEAD's message and author:

```
git -C <clone> commit -q --no-verify -a -C HEAD
```

It hides the seeds from `git status` and from a snapshot taken after it. It does not hide them from
`git log -p`, `git show` or `git diff HEAD~1`, and the log then holds two commits with one subject.
`base_sha` in the manifest stays the clone's HEAD before this commit, and `score` uses only file and
line, so scoring is unchanged. How the review clone should hide planted defects is an owner
decision, still pending (see #413).

## Examples

[`tests/fixtures/readiness_seed_examples/`](../../tests/fixtures/readiness_seed_examples/) holds
three made-up base files (`base/tools/report_save.py`, `report_load.py`, `report_pack.py`) and one
patch for each in `seeds/`. `01-data-loss-truncate-in-place` turns a save that wrote a temporary
file and replaced the old one into an in-place open that truncates it; `apply` records lines 9 to
11. `02-silent-fallback-swallow-bad-json` widens `except FileNotFoundError` to `except Exception`
(line 12). `03-injection-shell-tar` (line 8), quoted here, turns an argument list into a shell
string:

```
--- a/tools/report_pack.py
+++ b/tools/report_pack.py
@@ -5,7 +5,7 @@ import subprocess
 
 def pack_report(directory, archive):
     """Create the gzip tar ``archive`` from everything in ``directory``."""
-    subprocess.run(["tar", "-czf", archive, "-C", directory, "."], check=True)
+    subprocess.run("tar -czf %s -C %s ." % (archive, directory), shell=True, check=True)
     return archive
```

They exercise the tools end to end in `tests/test_readiness_seed_defects.py`. They are examples:
no patch here targets code in this repository.

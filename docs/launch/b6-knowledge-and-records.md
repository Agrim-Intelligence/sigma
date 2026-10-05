# Retention: other records and knowledge artefacts (B6, #463)

Host-agnostic. Everything below is Sigma's own Python and git; nothing needs a hook, a process pause
or a human to keep running. The machine-readable claims are `docs/launch/dispositions/463.json`
(46 rows); `tests/test_b6_463.py` runs each claim against a scratch tree, the real scan or the real
writer. Run it exactly as written:

```sh
python3 -m pytest tests/test_b6_463.py
```

## Decision

These families are not one store. Most are not git-tracked history here; several are operator-authored
or written by a model following skill prose; one (`.sdlc/evidence/`) is not written by Sigma at all.
So there is no new pruner and no new cap. Each family is either already bounded by its writer,
or is a document or report a person owns (deleting it deletes their record), or is a store Sigma cannot
prove it owns. No family has a tracked file in this repository, so a size cap would be a number without a
measurement; the one ceiling the measurements did find is named below instead.

| Family | Rows | Decision |
| --- | ---: | --- |
| Standing documents: `context/north-star.md`, `project.md`, `pipeline.json` | 3 | unbounded by decision; one file each, operator-authored |
| `decisions.json` | 1 | unbounded by decision; operator-authored, read whole on every edit; ceiling about 1,300 decisions with distinct paths |
| Unit registry: `features/`, `features/index.json` | 2 | unbounded by decision; one shard per unit, the index is rewritten |
| `design/` | 3 | unbounded by decision; two files per design goal, committed in the design PR |
| Local goals: `goals` family | 5 | unbounded by decision; proposals are deduplicated; ceiling about 10,000 goal files |
| `journey/` | 2 | unbounded by decision; append-only per goal, local backlog mode only |
| Persisted reports: `reviews/*`, `align`, `audit`, `radar` digests | 12 | unbounded by decision; one file per run, optional |
| Corpus parents: `knowledge`, `knowledge/` | 2 | unbounded by decision at the directory; children decided in their own rows |
| Analysis notes: `analysis` family | 8 | unbounded by decision; one note per goal, overwritten on a re-run |
| `gaps.md`, `radar/ledger.md` | 2 | unbounded by decision; append-only, exact-match dedup |
| Web captures: `research/`, `research/web/` | 2 | already bounded once the operator opts in; unbounded until then |
| Web archive: `archive/research/web` family | 3 | unbounded by decision; nothing deletes what retention archives |
| `.sdlc/evidence/<goal>/` | 1 | one shape pruned at a terminal record; everything else unbounded by decision |

## What changed against the issue's list

The issue listed 47 patterns from an older snapshot. The scan was re-run for this change with the
documented gesture and produces 45 of them today.

- **Dropped: `.sdlc/design/2289.md`.** The scan no longer produces it (an example the prose stopped
  naming), and a pattern the scan does not produce is refused, so it has no row.
- **Kept as a waiver: `.sdlc/evidence/<goal>/`.** The scan no longer produces it either (the prose in
  `skills/sigma-init/references/board.md` that named it was reworded), but it is the largest real store
  on the measured machine and the issue lists it, so it is covered with `unscanned: true`. The justification
  is the one `review-copy.json` already uses for `rv*/wt`: its writers are reviewers placing files by hand,
  outside Sigma's Python and skill prose, so the scan cannot see them. It is a different pattern from
  `.sdlc/evidence/<goal>/rv*/wt`, and the scanner accepts both (`test_evidence_waiver`).
- **Not touched (siblings).** The scan also produces `.sdlc/state/review-generations/<id>/evidence.json` and
  `.sdlc/state/review-posts/<id>.json` (#459, review generations, in flight) and
  `.sdlc/state/knowledge-sync.lock` (#464, claim, session and lock markers). They are in neither this
  file nor this doc.

## Tracked or not, and who owns it

`.gitignore` here is `.sdlc/*` with only `design/`, `changelog-fragments/`, `plans/`, `research/` and
`acceptance/` re-included. So in this repository every path in this goal is untracked except `.sdlc/design/`
(re-included, with nothing tracked today: `git ls-files .sdlc` lists plans, research and acceptance only).

An adopter is different. `/sigma-setup` ignores only `RUNTIME_IGNORES`: `.sdlc/state/`, `.sdlc/ledger/`,
`.sdlc/work/`, `.sdlc/knowledge/`, `.sdlc/events/` and `graphify-out/`. So `goals`, `journey`, `reviews`,
`evidence`, `design`, `features`, `context`, `decisions.json`, `pipeline.json` and `project.md` are not
ignored there: whoever runs `git add` takes them. Sigma's own commit (`work.py commit`, `git add -A`) runs
in a goal worktree that carries only tracked files, so it never stages the main checkout's evidence.
`tests/test_b6_463.py` (`test_ignore_split`) checks both halves against `git check-ignore` and the constant.

| Store | Writer | Owner |
| --- | --- | --- |
| `context/north-star.md`, `project.md` | `sigma-vision`; `sigma-retro` only proposes (an unattended run never edits a standing document) | operator |
| `decisions.json` | hand-authored per `sigma-decide`; read by `hooks/decision_gate.py` | operator |
| `pipeline.json` | operator-declared; read by `pipeline.py card` | operator |
| `features/units/<unit>.json`, `features/index.json` | `feature_registry.write_unit` and `write_index`, via `sigma-define` | the project |
| `design/<n>.md`, `<n>-in-brief.md` | `sigma-goal-design` prose, committed in the design PR | the project |
| `goals/<id>.md` | `sources.LocalSource.create_*` (local mode); `pipeline.propose_goals` (`auto-<hash>`); `pipeline.propose_from_discovery` (`disc-<hash>`) | operator |
| `journey/<goal>.md` | `sources.LocalSource.note` (append); GitHub mode posts an issue comment instead | Sigma, for the goal |
| `reviews/<kind>-<slug>.md` | the five review skills, "persist it if you want it retained" | operator |
| `knowledge/align`, `audit`, `radar/<date>.md` | `sigma-align`, `sigma-audit`, `sigma-radar` prose | operator |
| `knowledge/analysis/<id>.md` | `kg.write_note` from Retrospective | the project |
| `knowledge/gaps.md`, `radar/ledger.md` | `kg.gap_log`, `radar.record` | Sigma, for the operator |
| `knowledge/research/web/*.md` | `hooks/research_capture.py`, only when `knowledge_graph.enabled` | Sigma |
| `knowledge/archive/research/web/*` | `kg.retain_web` (rename) | Sigma |
| `evidence/<goal>/...` | reviewers and makers by hand; the only Sigma code is the pruner below | operator and reviewer scratch |

None of these is under a host configuration root, so nothing here reaches outside the repository and
nothing is out of scope on that ground. The only `.sdlc/knowledge/` content that can leave the machine is
`analysis/**/*.md`, through the opt-in ops-branch worktree (`knowledge_graph.sync`).

## Measured

Producing revision: origin/main `bc31f55`, a worktree cut from it, Darwin 25.6.0 / APFS, Python 3.9.6,
one developer machine. The main checkout holds only `.sdlc/evidence/`; `context`, `decisions.json`,
`design`, `features`, `goals`, `journey`, `knowledge`, `pipeline.json`, `project.md` and `reviews` are
absent, so none of those stores could be measured as stores. Their per-unit writer costs were measured by
running each writer in a scratch directory.

| Measured | Result |
| --- | --- |
| web capture, short query / 2 KB URL | 824 B / 2,738 B (the excerpt is capped at 400 characters; the subject is not) |
| web capture rate (the retention code's own comment, not re-measured) | 2,814 files in 40 days on one machine: the file count is the cost, not the bytes |
| journey note | about 167 B per note (10 notes of about 140 characters: 1,670 B) |
| pipeline / discovery proposal | 283 B / 274 B; the same signal or file again creates 0 files |
| `gaps.md` / radar ledger, first line | 93 B / 85 B; an exact repeat adds 0 |
| one new `gap_log` write at 100 / 1,000 / 10,000 lines | 0.08 / 0.22 / 1.75 ms (the whole file is re-read per write) |
| analysis note written twice | one file, content of the second write |
| feature unit shard at 10 / 100 / 1,000 member goals | 570 / 2,191 / 18,392 B (about 18 B per goal); an index of 100 units x 10 goals is 50,345 B |
| `decision_gate` load plus evaluate, distinct protected path per decision (worst case) at 100 / 500 / 1,000 / 1,500 / 2,000 decisions | 0.27 / 38 / 76 / 116 / 158 ms per edit |
| the same with 20 shared path patterns | 0.26 / 1.0 / 1.9 / 3.0 / 4.0 ms |
| a decision entry | about 530 B (10,000 decisions: 5.3 MB) |
| `discovery.next_pending` over done goal files at 100 / 1,000 / 10,000 | 2.1 / 20.6 / 238 ms per pick |
| `.sdlc/evidence/` here | 19 goal directories, 19,721 files, 522,421,958 B apparent (563,260 KiB on disk) |
| of which the pruner's shape, a direct `rv*/wt` (4 directories: `280/rv280`, `233/rvR233`, `233/rv233`, `236/rv286`) | 3,547 files, 143,237,899 B apparent (147,940 KiB on disk) |
| everything else (nothing counted twice) | 16,174 files, 379,184,059 B apparent |
| per goal directory (19 of them) | median 74,715 B, maximum 197,313,948 B (goal 314); the five largest (314, 326, 233, 280, 236) hold 521,334,540 B of the 522,380,776 B in goal directories, 99.8% |

Commands, from the repository root, all read-only: `find .sdlc/evidence -type f | wc -l`;
`find .sdlc/evidence -type f -print0 | xargs -0 stat -f%z` summed (`stat -c%s` on Linux);
`du -sk .sdlc/evidence`. The pruner's shape, bytes and files (the listing is the first line of the `shape`
block below; a nested `wt` such as `314/me/big/.sdlc/evidence/233/rv233/wt` is deliberately NOT in it, and an
earlier draft's `-path '*/rv*/wt/*'` counted such nested ones and so double-counted): for each directory the
listing prints, `find <dir> -type f | wc -l` and `find <dir> -type f -print0 | xargs -0 stat -f%z` summed
(`du -sk` for disk); the other entries are the totals above minus those. Per goal: for each
`.sdlc/evidence/<goal>`, the `stat` sum of its files, sorted; the median is the 10th of 19 and the five
largest are the top five of that sort. The writer
costs: run `hooks/research_capture.py build_breadcrumb`, `LocalSource.note`, `pipeline.propose_goals`,
`pipeline.propose_from_discovery`, `kg.gap_log`, `radar.record`, `kg.write_note` and
`feature_registry.write_unit` in a scratch directory and take `os.path.getsize`; the two scale timings call
`decision_gate.load_registry` plus `evaluate` and `discovery.next_pending` on generated files, median of 5 to 7.

**Not measured:** any adopter's repository (only this one); the knowledge graph build (the builder is
absent); the prose-written files (`reviews`, `align`, `audit`, radar digests, `design`, `north-star`,
`project.md`), since no code writes their content and none exist here; real analysis notes, real gaps and
ledger files; the radar ledger's own write time (the same code shape as `gaps.md`, not timed separately);
Linux and Windows; a registry with realistic sharing of path patterns beyond the 20-pattern case above; the
decision gate shows a non-linear cliff between 100 and 500 decisions (0.27 ms to 38 ms), cause not investigated,
and the roughly 1,300 ceiling is specific to this machine and Python 3.9.6.

## Scale: 10x and 100x

Only per-unit costs that were measured are scaled.

- **Decision registry.** Above the cliff noted under not-measured, with distinct paths (the worst case) the
  gate costs about 0.076 ms per decision between 1,000 (76 ms) and 2,000 (158 ms) decisions, so it passes 100 ms
  near 1,300 distinct-path decisions: that ceiling is specific to this machine and Python 3.9.6. With shared paths 2,000 decisions cost 4 ms. A registry of 1,300 is
  roughly 700 KB. These are synthetic registries on one machine.
- **Local goals.** Every pick scans the goal directory: about 24 microseconds per file, so 10,000 goal
  files cost about 0.24 s per pick, 10x of that (100,000) would be about 2.4 s by the same arithmetic (not
  measured at that size). In GitHub backlog mode the directory holds only proposals.
- **Gaps and radar ledger.** One write is linear in the file: 1.75 ms at 10,000 lines, so total cost over
  n distinct findings is quadratic, about 10,000 lines before a write costs more than a couple of milliseconds.
- **Web captures.** Bytes are small; the file count grows with every web call and the corpus the graph
  builder reads grows with it, which is what the opt-in retention bounds.
- **Unit registry.** About 18 B per member goal, so a 1,000-goal unit is 18 KB; the index is rewritten, not appended.
- **Evidence.** Not extrapolated. The 19 goals average 27 MB but the median is 75 KB and one goal is 197 MB,
  so a 10x or 100x of the mean would be a made-up number. What can be said is the per-goal worst case: one
  review checkout is hundreds of MB, and only the direct `rv*/wt` shape is reclaimed by Sigma.

## Evidence: what the pruner covers and what remains

`work.prune_terminal_review_copies` is called from `loop.py record` when a goal ends `done` or `failed`. It
removes only a non-symlink direct `wt` of a non-symlink direct `rv*` under `evidence/<safe-goal>`, never
follows a symlink, never recurses to discover a candidate, is fail-open and logs a `file`/`delete` action.
`tests/test_work.py` and `tests/test_loop.py` hold its controls (plan 349 records them red);
`test_evidence_prune_scope` and `test_evidence_parked_kept` here re-run it on a scratch tree and through
`loop.py record`.

| On this machine | Covered | Not covered |
| --- | --- | --- |
| direct `rv*/wt` of a goal that ends done or failed from now on | yes | |
| direct `rv*/wt` of the goals that ended before the pruner existed | no, nothing sweeps | 143,237,899 B in 3,547 files (4 directories) |
| any other name under `evidence/<goal>/` (`me`, `fin`, `mutwt`, `mainsnap`, `old314`) | no | the largest remainders: 196 MB, 86 MB, 20 MB, 19 MB, 7 MB |
| the rest of an `rv*` directory (notes, a review checkout that is not named `wt`) | no | for example 29 MB under one `rv*` with no `wt` |
| a goal that is parked, or whose record never ran, or whose prune failed | no, nothing retries | its copies stay until the lever |
| loose files and scripts at the top of `evidence/` | no | 41,182 B |

The covered and uncovered rows do not overlap: of the 522,421,958 B under `evidence/`, 143,237,899 B is the pruner's shape
and 379,184,059 B is everything else (the figure above for `314/me` alone is 196,188 KiB).
It counts nested checkouts inside `me` once, under `me`.

Sigma does not prune the uncovered rest because it cannot prove it owns it: nothing in Sigma's Python or skill
prose writes those names, so a name such as `me` is a person's or a reviewer's scratch, and the rule for this
goal is that only Sigma-owned, regenerable files are ever pruned. The honest result is that the 522 MB
the original audit found is mostly still there; the pruner stops the one regenerable shape growing from now on.

**The human lever** is per goal, terminal check first, dry run first. The check proves the goal's action log
most recent claim-or-record entry is a recorded `done` or `failed` (a parked or never-started goal prints
nothing, and neither does a goal that finished earlier and was claimed again, whose checkout may be live); the dry run lists exactly
what the pruner would have removed; the last line removes it. Replace `<goal>` with the goal stem.

<!-- lever -->
```sh
grep -E '"kind": "(claimed|recorded)"' .sdlc/state/log/<goal>.jsonl | tail -n1 | grep -E '"kind": "recorded".*"result": "(done|failed)"'
find .sdlc/evidence/<goal> -mindepth 2 -maxdepth 2 -type d -name wt -path '.sdlc/evidence/<goal>/rv*/wt'
find .sdlc/evidence/<goal> -mindepth 2 -maxdepth 2 -type d -name wt -path '.sdlc/evidence/<goal>/rv*/wt' -exec rm -rf {} +
```

The `-path` carries the goal's own literal prefix, so `rv*` can only match the one path component under that goal
(a goal whose stem begins `rv` would otherwise also match `me/wt`); `-type d` skips a symlinked `wt`, and `find`
does not descend into a symlinked `rv*`. That is the same set the pruner selects: a direct, non-symlink `wt` of
a direct, non-symlink `rv*` of one goal. It differs from the pruner in one way that matters: the pruner runs
only at a terminal record, while you run this on your own judgement. A `failed` goal under human repair that
was never claimed again still reads as terminal in its log and can hold a live checkout, so the check is
evidence, not proof: look at the dry run, and at whether anyone is working in that goal, before the last line.

Run the first line and read its output before the third; run the second and read its list before the third.
`test_evidence_lever` copies these three lines out of this document and runs them on a scratch tree: the
check matches a done goal and prints nothing for a parked one, one with no log, or one that was done and then claimed again, the dry run removes
nothing and lists only that goal's copies (also for a goal named `rvgoal` that holds both `me/wt` and `rv1/wt`), the removal leaves every other name and every other goal alone.
The pruner's shape across the whole directory (used for the figures in the Measured table; two lines, run from
the repository root) is below. `test_evidence_shape` runs it on a synthetic tree that has a nested `wt`
and a `wt` of a non-`rv*` name and checks only the direct `rv*/wt` is listed and counted.

<!-- shape -->
```sh
find .sdlc/evidence -mindepth 3 -maxdepth 3 -type d -name wt -path '.sdlc/evidence/*/rv*/wt'
find .sdlc/evidence -mindepth 3 -maxdepth 3 -type d -name wt -path '.sdlc/evidence/*/rv*/wt' -exec find {} -type f \; | wc -l
```

The other names are removed by hand, after looking, with `rm -rf .sdlc/evidence/<goal>/<name>`.

A scheduled sweep of finished goals is a design of its own (it needs a terminal check per goal and a
visible report) and is filed as a follow-up, not built here.

## Web captures and the archive

`kg.retain_web` archives, never deletes, a capture older than 90 days or beyond a byte bound derived
from the disk (`disk.total * 0.0001` of block-rounded occupancy), oldest first, one same-volume
rename each, so a crash loses nothing and the next pass continues. It runs only when the corpus itself is
enabled (`knowledge_graph.enabled`, default false) and either `web_retention_enabled` is true or the operator
types `kg.py retain`. Until then the live set is unbounded: `kg.py maintain` reports it over its bound and
prints the command. `tests/test_kg_retention.py` holds the controls (off by default, age, derived bound,
idempotence, no overwrite, a raced file, a refused limit); `test_web_retention` here adds that the archive
is never touched and nothing is deleted. The archive grows by what retention moves into it, and the web
rows' decision for it is unbounded: it is still inside a cold graph build's input, and the owner deletes it by
hand (`rm -rf .sdlc/knowledge/archive/research/web`, the line `kg.py maintain` already prints). `kg.py maintain`
also flags the whole corpus past 200 documents and prints, but never runs, the archive command for a stale or
duplicate analysis note (`git rm` plus a commit in the ops worktree, `mv` otherwise).

## Who reclaims each family

| Family | Owner | Reclaim lever (always a person, never a Sigma script) |
| --- | --- | --- |
| standing documents, `decisions.json` | operator | edit or delete by hand; retire a decision by status |
| unit registry | the project | close the unit and remove its shard in a reviewed commit |
| `design/`, `reviews/` | the project / operator | a reviewed commit that removes old files |
| `goals/`, `journey/` | operator | move finished goals out, delete a finished journey, by hand |
| reports and digests under `knowledge/` | operator | delete old dated files |
| analysis notes | the project | the archive command `kg.py maintain` prints |
| gaps and radar ledger | operator | edit the file (a removed line can be surfaced again) |
| web captures | Sigma | `kg.py retain` |
| web archive, evidence beyond `rv*/wt` | operator | `rm -rf` after looking; the lever above for `rv*/wt` |

## Recovery (no human needed, no process pausing)

- The pruner is fail-open and idempotent: a repeat finds nothing. A crash in the middle of `rmtree` leaves a
  partial `wt` directory that is still a directory, so the next terminal record of that goal, or the lever,
  finishes it. Nothing retries it on its own; that is a stated gap, and the lever is the polite recovery.
- Web retention is rename-only: a crash leaves each file either live or archived, never lost or doubled
  (`test_kg_retention` covers a destination that already exists and a file another worker already moved).
- The dedup writers re-read the file each write, so a crash between lines loses at most the line being
  written; a re-run does not duplicate it.
- A restore from backup restores the same files. Nothing here keeps state outside them, and the readers look
  a record up by name each time.
- Nothing pauses or signals a process.

## Public snapshot

Whether the public snapshot carries `.sdlc/` is not decidable from this repository (the file selection and
the builder are private and absent), so the tracked-or-not statements above describe the source repository
and `/sigma-setup`'s defaults only.

## Disposition and the audit gesture

`docs/launch/dispositions/463.json` has 46 rows: the 45 scanned patterns and `.sdlc/evidence/<goal>/` with the
`unscanned` waiver explained above; no other waiver is used. The documented gesture in `growth-audit.md` is
run on a scratch copy by `test_audit`, which asserts every scanned pattern leaves
`b6_disposition.unresolved_patterns` and carries the row's `pruner_or_cap` and `decision`. As recorded when this
was written: the scan listed 102 unresolved patterns before this file and 57 after (the 45 resolved here).
The committed `growth-audit.json` is not regenerated by this slice, as with the sibling B6 slices.

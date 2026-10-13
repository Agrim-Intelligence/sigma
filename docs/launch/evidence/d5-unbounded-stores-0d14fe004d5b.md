# D5: every store marked "intentionally unbounded", on one page

Prepared for the owner ruling "does a documented ceiling count as a cap". This page does **not** make that
ruling; it lists what exists so the ruling can be made from one place. Counted at commit
`0d14fe004d5b` from `docs/launch/dispositions/*.json` (18 files, 206 entries).

## The count

- **77** disposition entries have "unbounded" in their `decision` field. Each is one path pattern, and all 77
  patterns are distinct.
- **75** are unconditional. **2** (`knowledge/research/` and `knowledge/research/web/`) read "already bounded once the
  operator opts in ... unbounded until then", so they are counted but listed apart (group 24).
- The 77 patterns are **24 stores**: several patterns are the same store (a directory, its glob and its file
  shapes). The tables below group them; the pattern count per group is in the second column.
- Not counted: decisions worded "retained for the worktree lifecycle" (3), "already bounded + lever" (4) and
  the capped, pruned or bounded-by-truncation entries (the rest of the 206).
- Outside the 77: `.sdlc/state/upkeep/level2/<unit>.json`, which `b6-late-stores.md` calls "bounded per file (50 attempts),
  one file per unit, intentionally unbounded per unit". It is written through a constant-built path the scanner does not
  see, so it has no disposition entry. Listed here so the ruling does not miss it.
- The reproduction is a grouping of the `decision` text; the 77 and the 24 groups were recomputed by a script that
  failed on any pattern it could not place in exactly one group.

## Basis column

- **MEASURED**: the ceiling number (or its per-item input) was measured, on the page named. "Measured" says nothing about
  an adopter's fleet: every figure is one developer's machine, mostly macOS.
- **TEST**: the ceiling is enforced by a named test, so a record over it turns a test red. This is not a runtime cap.
- **STATED**: the page states the ceiling (or says there is none) without measuring it.
- **NONE**: the page says no ceiling number is enforced or known.

"1x / 10x / 100x" is from `docs/launch/scale-table.md` where it has a row; the letters there are M measured,
C calculated from a measured per-item figure, S stated.

## A. Written by a person or an agent following prose, no size known (no ceiling number)

| # | Store (patterns) | Ceiling stated | Basis | Scale table | Who reclaims |
|---|---|---|---|---|---|
| 1 | `.sdlc/state/goal-review/` plan and report per confirmed design (4) | two JSON files per confirmed design | STATED; directory absent when measured | no row | operator: `rm .sdlc/state/goal-review/<n>.*` |
| 2 | `.sdlc/state/withheld/<goal>.md` (3) | one markdown file per goal that surfaced a kit finding | STATED; not measured | no row | operator, after filing the finding upstream |
| 3 | `.sdlc/state/review-queue.md`, local-goals mode only (1) | a few hundred bytes per parked or failed goal | STATED; none exists in the measured checkout | no row | whoever triages the queue |
| 8 | standing documents `north-star.md`, `pipeline.json`, `project.md` (3) | none: one operator-edited file each, nothing per run grows it | NONE; absent when measured | no row | operator |
| 10 | `.sdlc/design/` and `<n>.md`, `<n>-in-brief.md` (3) | none; two files per design goal, committed with the design PR | NONE; nothing exists to measure | no row | maintainers, reviewed commit |
| 16 | `.sdlc/knowledge/align|audit|radar/` dated digests (6) | one file per run; an audit has at most 12 findings | STATED; sizes not measurable by code | no row | operator, by hand |
| 17 | `.sdlc/knowledge/analysis/` notes (8) | one file per goal id, replaced on a re-run; neither size nor count capped | NONE; no real note exists to measure | no row | project, via the printed archive command |
| 18 | `.sdlc/knowledge/archive/research/web` (3) | none: archive-not-delete is the contract | NONE; nothing archived when measured | no row | operator deletes the directory |
| 21 | `.sdlc/reviews/` persisted reports, five skills (6) | none; only written if the operator wants it kept | NONE; none exist | no row | reviewed commit |
| 15 | `.sdlc/knowledge`, `knowledge/` parent (2) | `kg.py maintain` reports over 200 documents and deletes nothing; corpus exists only if `knowledge_graph.enabled` (default false) | STATED | no row | operator |

## B. A per-item size is measured, the count is unbounded

| # | Store (patterns) | Ceiling / per-item figure | Basis | 1x / 10x / 100x (scale-table) | Who reclaims |
|---|---|---|---|---|---|
| 5 | `.sdlc/ledger/<stream>` entries (4) | one line about 180 B per entry; one file per writer process per actor; growth is file count and ops-branch history | per-entry MEASURED; adopter volume not measured (ledger off on the measured machine) | "not measured on an adopter" / bytes linear (S) / bytes linear (S); no chokepoint number | maintainer with push access, reviewed commit; no automatic lever by design |
| 11 | `.sdlc/evidence/<goal>/` review checkouts (1) | none; one directory is 197 MB of 522 MB; only the direct `rv*/wt` shape (143,237,899 B) is pruned at a terminal record | MEASURED (19 goals, 522,421,958 B, 19,721 files); 379,184,059 B uncovered | 19 goals, 522 MB apparent (M) / not extrapolated / not extrapolated | operator and the reviewer who placed it |
| 12 | `.sdlc/features/` unit registry shards and index (2) | about 18 B per member goal; 570 / 2,191 / 18,392 B at 10 / 100 / 1,000 goals; index 50,345 B at 100 units x 10 goals | MEASURED | 570 B at 10 goals (M) / 2,191 B at 100 (M) / 18,392 B at 1,000 (M) | a person closing a unit, reviewed commit |
| 14 | `.sdlc/journey/<goal>.md`, local backlog mode only (2) | about 167 B per phase note; never trimmed (audit trail) | per-note MEASURED; no cap | no row | operator, by hand |
| 19 | `.sdlc/knowledge/gaps.md` (1) | first line 93 B; a write re-reads the whole file: 0.08 / 0.22 / 1.75 ms at 100 / 1,000 / 10,000 lines | MEASURED; no cap | not measured / 1.75 ms per write at 10,000 lines (M) / quadratic over n findings (S) | operator edits the file |
| 20 | `.sdlc/knowledge/radar/ledger.md` (1) | first line 85 B; same whole-file-read shape as gaps.md, not separately measured | per-line MEASURED; shape STATED | (shares the gaps row) | operator edits the file |
| 7 | `.sdlc/plans/triage/` and `plans/scope/<slug>.plan.json`, gitignored (4) | about 2.3 KB per triage run | MEASURED, one run; scope plan size not measured | no row | operator, by hand |
| 23 | `.sdlc/state/upkeep/units/<unit>.json` (1) | 281 B per document, 4 KiB on disk; 1,000 units = 281,000 B (4,000 KiB on disk); 10,000 units about 39 MiB (extrapolated) | MEASURED at 1,000 units (APFS, one machine); 10,000 is arithmetic | no row | delete the file or directory; unit is re-evaluated; a pruner for dead units is a recorded follow-up |
| 4 | `slack-commands.launchd.log` (1) | none: about 100 B per line, no cap; written by the host service manager, not Sigma | per-line STATED; real file size never measured | "the `launchd` file Sigma does not own is uncapped and linear (S)" | operator who wrote the plist |

## C. A ceiling number is stated or enforced by a test

| # | Store (patterns) | Ceiling | Basis | 1x / 10x / 100x (scale-table) | Who reclaims |
|---|---|---|---|---|---|
| 6 | `.sdlc/plans/`, `research/`, `acceptance/` committed work records (9) | 131,072 B per plan or research file, 32,768 B per acceptance file, 32,768 B average per goal | TEST (`tests/test_b6_462.py`, red on an over-cap record); sizes MEASURED (77 goals, 0.9 MB) | 0.9 MB / 9 MB tree, about 6.5 MB packed history (C) / 90 MB tree, about 65 MB packed (C); 252 MB at the ceiling (C); `find` over the plans dir 4.9 / 9.4 / 73.8 ms at 770 / 7,700 / 77,000 files (M) | reviewed git commit by a human, never a script |
| 9 | `.sdlc/decisions.json` (1) | about 1,300 distinct-path decisions (100 ms per edit); about 530 B per decision | MEASURED on synthetic registries: 0.27 / 38 / 76 / 116 / 158 ms per edit at 100 / 500 / 1,000 / 1,500 / 2,000 | about 700 KB at 1,300 decisions (M) / 76 ms at 1,000 and 158 ms at 2,000 (M) / not measured | operator retires or deletes an entry |
| 13 | `.sdlc/goals/` local goal files, local backlog mode (5) | about 10,000 goal files, where a pick costs about 0.24 s | rate MEASURED (2.1 / 20.6 / 238 ms at 100 / 1,000 / 10,000 files, about 24 microseconds each); the ceiling number is derived from it | about 24 microseconds per file (M) / 10,000 files 0.24 s per pick (C) / 100,000 files about 2.4 s per pick (C, not measured) | a person moving done goals out, reviewed commit |
| 22a | `.sdlc/state/agent-watch-cursor.json` (opt-in `agent_watch`) | 25 B per key (19 B measured); keys are confirmed agent deaths, never retired | TEST (`tests/test_b6_466.py`); per-key MEASURED | "three cursor files": 63,000 B for 100 goals x 20 ids (M) / 190, 450, 630 KB (C) / 1.9, 4.5, 6.3 MB (C); rewritten whole on change | delete the file (one repeat notice per key, self-healing); key retirement queued as #490 |
| 22b | `.sdlc/state/channel-notify-cursor.json` (opt-in webhook) | 60 B per key (45 B measured); keys are ledger candidates | TEST; per-key MEASURED | (same cursor row) | delete the file; #490 |
| 22c | `.sdlc/state/comment-watch-cursor.json` (opt-in `comment_watch`) | 800 B per goal (630 B measured at 20 ids); keys are goals ever claimed | TEST; per-key MEASURED | (same cursor row) | delete the file; #490 |
| 22d | `.sdlc/state/inbox.md` | 600 B per item (517 B measured at one item per tick); read and cleared by `loop.py next` | TEST; per-item MEASURED | 517 KB per 1,000 items (M) / same per-item (C) / 5 MB at 10,000 items (C); `tick` rewrites the whole file, O(n) (S) | `loop.py next`, or delete the file; bounded delivery queued as #490 |

(Group 22 is four patterns and four ceilings, shown as 22a to 22d. It is one group of the 24.)

## D. Conditional

| # | Store (patterns) | Ceiling | Basis | Scale table | Who reclaims |
|---|---|---|---|---|---|
| 24 | `.sdlc/knowledge/research/` and `research/web/` captures (2) | bounded only if the operator opts in (`web_retention_enabled`, or runs `kg.py retain`): archives, never deletes, captures older than 90 days or beyond disk total x 0.0001; unbounded until then. One capture is 824 B (short query) to 2,738 B (2 KB URL). | TEST (`tests/test_kg_retention.py`); capture size MEASURED | no row | `kg.py retain` |

## What the owner has to decide, from the tables

- Rows with a **number that is measured or test-enforced** (6, 9, 12, 13, 19, 22, 23 and the per-item sizes in 5, 7, 11, 14,
  20): the question is whether a stated ceiling plus a reviewed-commit reclaim is a cap.
- Rows with **no enforced ceiling number** (1, 2, 3, 8, 10, 15, 16, 17, 18, 21): 10 stores, 39 of the 77 patterns, where the
  question has no number to rule on, only "operator-authored and absent in practice".
- Rows where the **growth rate is not Sigma's** (4: the host service manager writes it; 5: git history of the ops branch).
- One store grows with a measured, large footprint today and is only partly pruned: 11, `.sdlc/evidence/`, 522 MB over 19
  goals on the measured machine, 379 MB of it outside the pruner's shape.

Sources: each group's page is named by its disposition file: 458 `b6-per-goal-state.md`, 459 `retention-review-evidence.md`,
460 `b6-slack-supervisor-logs.md`, 461 `retention-event-time-ledger.md`, 462 `b6-committed-work-records.md`, 463
`b6-knowledge-and-records.md`, 466 `b6-singletons-and-home.md`, 919 `tests/test_upkeep_pins.py`.

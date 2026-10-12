# Per-item stores at 1x, 10x and 100x

One table across the retention pages (`b6-*.md`, `retention-*.md`), for dimension D5. Each cell is
tagged: **M** = measured (on the page named, or by `tools/readiness/scale_probe.py` here),
**C** = calculated from a measured per-item figure (arithmetic, not a measurement), **S** = stated
by the source page, not measured. 1x is the size of the checkout that page measured; it is one
developer's heavily agent-driven machine (macOS unless noted), not an adopter fleet.

| Store (per-item unit) | 1x | 10x | 100x | Serialised chokepoint, with a number | Source |
| --- | --- | --- | --- | --- | --- |
| Plans, research, acceptance records (per goal; no pruner, tested ceiling 32,768 B per goal) | 77 goals, 0.9 MB (M) | 9 MB tree, about 6.5 MB packed history (C) | 90 MB tree, about 65 MB packed (C); 252 MB at the ceiling (C) | per-edit `find` over the plans dir: 4.9 / 9.4 / 73.8 ms at 770 / 7,700 / 77,000 files (M); clone and checkout size binds first, not measured | b6-committed-work-records |
| Action log (per goal file) | 85 goals, 207,178 B (M) | about 2 MB (C) | about 20.7 MB (C) | `sigma-log slots` reads every goal file: 0.06 s at 85 files, 0.68 s at 8,500 (M, one run each); re-measured below | b6-action-log-witness |
| Witness (per verified goal) | 36 goals, 639,969 B (M) | about 6.4 MB (C) | about 64 MB (C) | a live goal's own file is unbounded while live (max 159,216 B seen) | b6-action-log-witness |
| Verify store (per verified goal) | 55 files, 3,929,772 B (M) | about 39 MB without the prune (C) | about 393 MB without the prune (C) | PRUNED: `goal_state_prune.sweep` removes a terminal goal's files 7 days after `done`, on every `record done`, so the standing volume is 7 days of goals at 71 KB: about 50 MB at 100 goals a day (C). Sweep dry run 0.35 s at 550 and 1.22 s at 5,500 terminal stems (M, synthetic two-row logs). Ceiling: a stem with no action-log row (log off, or the log pruned first) is never pruned, so those stay (S) | b6-per-goal-state |
| Pick-claim locks and markers (per pick) | 82 files, about 20 a day (M) | about 200 a day, 73,000 a year unpruned (C); 30-day pruned ceiling about 6,000 markers (C) | about 730,000 a year unpruned (C); about 60,000 pruned (C) | sweep dry run over 1,230 / 12,300 / 123,000 stale files: 0.32 / 0.57 / 3.61 s (M); `claims/` directory lookup cost at that count not measured | b6-claims-sessions-locks |
| Lock stripes (`sessions/locks`, `phase-end-*`) | 38 and 124 of 256 (M) | 256 (S) | 256 (S) | fixed 256 hash stripes per family: the number of concurrent phase-end sections that can never collide is 256; contention at that count not measured | b6-claims-sessions-locks |
| Per-goal worktrees (per live goal) | 14 trees, 455 MB (M) | 140 trees, about 4.5 GB (C) | about 45 GB (C) | automatic sweep examines at most 20 trees and 8 REST reads per run, once per 15 minutes, so a 1,400-tree backlog is 70 sweeps (C from the stated limits); 100 trees: dry run 7.9 s, removal about 0.1 s per tree (M) | b6-worktrees |
| Event journal and time store (per writer process file) | 2,828 files, 430,189 B, 11.4 MB on disk (M) | 28,000 files, about 114 MB on disk (C); sweep 3.4 s at 28,300 stale files (M) | 283,000 files, about 1.1 GB on disk (C); sweep 44.6 s at 283,000 stale files (M) | the sweep holds the triggering append: about 45 s at 283,000 stale files, a one-off backlog case (M) | retention-event-time-ledger |
| Ledger entries (per entry, about 180 B) | not measured on an adopter (ledger off here) | bytes linear (S) | bytes linear (S) | one ops branch and one watcher per clone; a pull moves proportionally more files; no number measured | retention-event-time-ledger |
| Review evidence generations (per goal) | 36 goals, 1.3 MB (M) | 13 MB without the prune (C) | 133 MB without the prune (C) | sweep per `record done`, nothing terminal: 0.04 / 0.11 / 0.85 s at 36 / 360 / 3,600 goals (M, synthetic, one run) | retention-review-evidence |
| Decision registry (per decision) | about 700 KB at 1,300 decisions (M) | gate 76 ms at 1,000 and 158 ms at 2,000 distinct-path decisions (M) | not measured | whole registry read on every edit; the gate passes 100 ms near 1,300 distinct-path decisions on that page's machine (M) | b6-knowledge-and-records |
| Local goal files (per goal, local backlog mode) | about 24 microseconds per file per pick (M) | 10,000 files, 0.24 s per pick (C from that rate) | 100,000 files, about 2.4 s per pick (C, not measured at that size) | NO code cap and no pruner (a decision of that page: intentionally unbounded, human-created goal files). Named ceiling: about 10,000 goal files, where a pick costs about 0.24 s (calculated from the measured 24 microseconds per file); past it the cost is linear and a pick spends seconds. Only local backlog mode; in GitHub mode the directory holds proposals | b6-knowledge-and-records |
| Gaps and radar ledger (per line) | not measured | 1.75 ms per write at 10,000 lines (M) | quadratic over n findings (S) | one write is linear in the file | b6-knowledge-and-records |
| Unit registry shard (per member goal, about 18 B) | 570 B at 10 goals (M) | 2,191 B at 100 goals (M) | 18,392 B at 1,000 goals (M) | the index is rewritten whole: 50,345 B at 100 units x 10 goals (M) | b6-knowledge-and-records |
| `.sdlc/evidence` review checkouts (per goal) | 19 goals, 522,421,958 B apparent (M) | not extrapolated | not extrapolated | one goal is 197 MB of 522 MB (99.8% in five goals), so a mean would be invented | b6-knowledge-and-records |
| `embeddings.json` (capped at 2,000 entries) | 15.6 to 124.6 MB (M) | flat (S) | flat (S) | every pick parses the whole file: 87 to 687 ms; overflow past the cap re-embeds on every pick (S) | b6-singletons-and-home |
| `inbox.md` (per unread item, 517 B) | 517 KB per 1,000 items (M) | same per-item cost (C) | 5 MB at 10,000 items (C) | `tick` rewrites the whole file each time, O(n) per tick (S) | b6-singletons-and-home |
| Three cursor files (per key) | 63,000 B for 100 goals x 20 ids (M) | 190, 450 and 630 KB (C) | 1.9, 4.5 and 6.3 MB (C) | each is read and rewritten whole on change; a 6 MB rewrite every tick (S) | b6-singletons-and-home |
| `STATE.md` | 340 KB (M) | flat (S) | flat (S) | 4,096 attempts cap | b6-singletons-and-home |
| Slack listener and supervisor logs (per event line, about 100 B) | capped | capped | capped at 4 MiB per log (3 predecessors) (S); the retention window shrinks to about 40,000 lines | the `launchd` file Sigma does not own is uncapped and linear (S) | b6-slack-supervisor-logs |

## Re-measured here

`sigma-log slots` at 85 / 850 / 8,500 goals: 0.037 / 0.128 / 1.071 s (M). Claims sweep dry run at
1,000 / 10,000 / 100,000 locks: 0.397 / 0.575 / 2.476 s (M). From `tools/readiness/scale_probe.py`;
[evidence/scale-probe.md](evidence/scale-probe.md) has the conditions and what was not measured.

## What this table does not claim

- No 10x or 100x row is an adopter figure; every "C" cell is a per-item size times a count.
- The cells marked "not measured" stay that way: directory-lookup cost at 100,000 lock files on a real
  filesystem, clone time at 7,700 goals, `flock` contention on the 256 stripes, any network filesystem,
  Linux and Windows sweep times. The dead-vs-idle control is not a scale cell; its caveat is that a clean
  SIGTERM leaves no heartbeat to age, so only a crash (SIGKILL leg) is told from idle by age.

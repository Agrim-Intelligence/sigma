# B6 #459: review generation, manifest, post and result evidence

Disposition file: `docs/launch/dispositions/459.json` (11 patterns). Test: `tests/test_b6_459.py`.
Measured at origin/main 757efaf (the revision that produces these stores) unless a row says otherwise.

## Ownership (every path is under the project `.sdlc/state/`; none is under a host configuration root)

| Path | Writer | Notes |
| --- | --- | --- |
| `review-generations/<gen>/brief.md` | `review_context.publish_generation` | one NEW generation per `review_context.py brief --output`; never reused |
| `review-generations/<gen>/evidence.json` | `work.review_evidence` | create-once, names the goal |
| `review-generations/<gen>/resolution.json` | the documented shell redirect `reviewer.py resolve > "$REVIEW_RESOLUTION"` | not Python, not seen by the scan; removed with its directory |
| `review-manifests/<goal>.json` | `publish_generation` | one pointer per goal, atomically REPLACED; the generation it used to name is superseded and nothing removed it |
| `review-results/<gen>.json` | `work._write_review_result` | create-once |
| `review-posts/<evidence_id>.json` | `work._review_post_request` | create-once, carries `goal` |
| `review-queue.md` | `state._queue` | local-goals mode only (park/fail) |

## Who re-reads these, and for how long

The merge gate reads the PR's comments on GitHub and `merge_observation` reads the `review_posted` journal event; neither opens a
`review-*` file. The only local readers are `post_review` (manifest scan, then evidence and result) and `reconcile_review_post`
(post receipt and evidence). Both begin at `state/work/<goal>.json`, which `work.finish` unlinks. So the files are live exactly while
the goal's work record exists; after `record done` and `finish`, nothing reads them. A parked or failed goal keeps its record and can resume,
so it is never pruned.

## Measured

Real operator store (36 goals, produced by revisions up to a3c913c): 82 generation directories, 1,254,670 bytes (min 5,715, median 9,060,
max 92,585); 36 manifests 18,432 B; 75 posts 25,264 B; 75 results 37,880 B. About 1.34 MB, 37 KB per goal, about 2.1 evidence-bearing
generations per goal; 46 of the 82 generations are superseded. `brief.md` dominates. Command: a `find`/`os.walk` size sum over
`.sdlc/state/review-*` plus a count of `evidence.json` goals (not committed).

Fresh run at 757efaf: the documented `review_context.py brief ... --output .sdlc/state/review-manifests/<goal>.json` gesture twice for one goal in
a scratch `.sdlc` left 2 generation directories and 1 manifest (brief 2,110 B in a repo with no north-star).

| Scale | Store without the prune (37 KB/goal) | Sweep cost per `record done`, nothing terminal (synthetic, 2 generations of 9 KB per goal, one run, this machine) |
| --- | --- | --- |
| 1x, 36 goals | 1.3 MB | 0.04 s |
| 10x | 13 MB | 0.11 s |
| 100x, 3,600 goals | 133 MB | 0.85 s |

After the prune a done goal costs 0, so the store holds only goals that are not done plus the residual below; the sweep reads manifests, posts and
`evidence.json` of what remains, so its cost follows that residual, not history. A first measurement (0.97 s at 36 goals) found the action-log module being
re-loaded per candidate (about 25 ms each); it is loaded once per call now.

NOT measured: a very large diff (the brief points at the diff; one 92 KB brief exists), a slow or networked disk, concurrent writers during a sweep,
filesystem block overhead (`du` reports 1,972 KB for the 1.25 MB). The 415 MB of #349 was `evidence/<goal>/rv*/wt` review copies, a different store with
its own pruner.

## The decision: capped for finished goals

`work.prune_terminal_review_generations` runs from `loop._record` on `done`, after `_release_checkout`. A goal is terminal only if its work record file
is absent (existence, not parseability) and the caller (`done` just recorded) or the action log's last `claimed|recorded` entry says done. A generation is removed
only when exactly one goal owns it. Order: posts, then results and the generation directory, then manifests. Only manifests are re-read just before their unlink and removed only
while they still name a removed generation (a goal re-claimed mid-prune keeps its new pointer); posts, results and directories are removed from the index
because their generation ids are never reused. The microseconds between that re-read and the unlink are an accepted race, narrowed by a second terminal check that guards manifests only: a goal re-claimed between the one terminal check per goal and the posts, results and directories being removed loses its superseded generations (never its new pointer). Symlinks, a linked `state` or `review-*` directory, stray names and
unsafe goal values are skipped. At most 10 goals per call that made progress (a goal stuck forever, such as one whose evidence names another owner, never spends the budget). Goal identity is compared as `work.stem`, since local mode publishes a path.

Left alone on purpose (the residual, unbounded by decision):
- Goals not done: parked, failed, running, re-claimed. Ceiling: about 37 KB per such goal (measured above); reclaim: finish the goal, then the next `done` sweeps it,
  or `work.py prune-review-generations <sdlc_dir> <goal> --done` once a human confirms it is finished.
- Evidence-less generations no manifest names (an aborted review): 7 of 82 measured, about 9 KB each, no goal can be attributed, so none is deleted. Ceiling: one brief
  per aborted review; reclaim: the operator may delete a generation directory that no `review-manifests/*.json` names and that has no `evidence.json`.
- A generation whose `rmtree` died after deleting its `evidence.json`, and which no manifest names (a superseded one), loses its only owner: it then joins the evidence-less class above.
- A `done` goal whose `finish` refused (open unarmed PR, `auto_merge: off`) keeps its record, so it is swept by a later `done` of any goal, or by the lever.
  The latency is unbounded on a repo with no further `done`; no data is lost.
- With `action_log` off there is no log proof, so only the goal being recorded is pruned (the hint), never others.
- `review-queue.md` (local-goals mode): intentionally unbounded. One short entry per parked or failed goal (`state._queue`); an entry is a decision awaiting a person, so a
  sweep would delete what it exists to show. Ceiling: a few hundred bytes per parked/failed goal; reclaim: whoever triages the queue. Not measured: none exists in this checkout (github mode).

## Recovery (no human, no process pausing)

- Crash or partial prune (a failed `rmtree`, a killed process): every step is idempotent and reads only files a previous step has not removed; manifests go last and only when their
  generation is gone, so the goal stays attributable and the next call finishes the job (`test_partial_prune_heals`, `test_manifest_left_behind_is_collected`).
- Restore from backup: a restored store of finished goals is drained 10 goals per `done`; nothing needs a stamp, so a stale stamp cannot suppress it.
- Lost action log: spares data (the goal is never proven terminal); the lever above reclaims it.
- Operator lever: `python3 skills/agrim-loop/scripts/work.py prune-review-generations <sdlc_dir> [<goal> [--done]] [--limit N]`, same bound and checks.

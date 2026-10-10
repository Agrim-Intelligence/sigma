# #954 P5 IMPLEMENT — evidence for the PR body

Worktree `.sdlc/work/954`, branch `sdlc/954`, HEAD `66b1f1b` (uncommitted, as instructed). Plan
`.sdlc/plans/954.md` (Revision 2) plus the plan review's seven binding refinements. Scratch:
`$SCRATCH/impl954/` (session scratchpad).

## Status at handoff — final verify NOT green (blocked)

The last `loop.py verify .sdlc 954`, run from the root checkout exactly as prescribed, returned
**`STALE exit=4`**: origin/main moved to `7f0cb9d` (#969, #895 slice 2c) after this worktree was
rebased, and the automatic rebase's autostash pop conflicted. The only file both sides touch is
`CHANGELOG.md` (both add an entry under `## Unreleased`). Verify undid the rebase: HEAD is still
`66b1f1b`, every changed file is byte-identical to before (checked with `diff -q` against scratch
copies), `git stash list` is empty, and no rebase is in progress. Git left an `AUTO_MERGE` ref in
the worktree's git dir. Per the dispatch rules I stopped there and did not resolve it by hand.

Two more things stand between this tree and a green verify, both independent of the code:

1. **`python` is not on PATH in this environment.** The configured verify command
   `python -m pytest tests/ -rs -q` exits **127** (`/bin/sh: python: command not found`). Both earlier
   verifies (R-OLD, R-WEAK) returned `FAILED exit=127`, and the goals #955/#956 evidence files show
   the same thing. `loop.py verify` still runs the planned nodes itself with `python3`, so the
   red/green journal is unaffected. The full suite was run separately with `python3` (below).
2. **`tests/test_write_surface.py::test_committed_inventory_matches_the_tracked_write_surface` can
   only be green once `feature_provenance.py` is tracked** (refinement 1). It reads `git ls-files`.
   With the three new rows and an untracked module it reports `stale entry` (seen in the worktree).
   In a scratch clone with every change staged it is green, and removing the rows there turns it red
   (`new write site ...`). The rows follow `scan_paths` (see Deviations).

## Red before green (`red_green.py`, installed 1.0.2)

- **Task 1.4 dry run on the old code** used the exact `observe` argv and the installed
  `_split_traceback_assertions`. The credited set was exactly the RED-first set
  {1, 2, 8, 9, 11, 12, 14-21, 23, 24}, and every PIN passed {3, 4, 5, 6, 7, 10, 13, 22}. There were 0
  `Captured` sections, separators were at least `___` (the longest name is 71 characters), and every
  `E` line was an `AssertionError`.
- **R-OLD** (`loop.py verify`, code at HEAD with the module absent) recorded **16 assertion reds**,
  one for each RED-first node.
- **R-WEAK**: the weak build W1-W7 was applied to the worktree. The dry run credited
  {3, 4, 5, 6, 7, 8, 10, 11, 13, 15, 19, 21, 22}, a superset of PINs + 21. Test 21's block chains a
  `NotADirectoryError` (from W7) under its own `AssertionError`, which is credited as an assertion.
  The verify then ran. Afterwards the four code files were restored from scratch copies and `diff -q`
  was clean.
- **Journal**: all **24/24** planned nodes have an `assertion` red bound to the current test-file
  hashes. `tests/registry_root.py` and both test files have stayed byte-identical since the first
  red-recording verify (refinement 5). `loop.py verify` has **not** observed a green, because of the
  STALE refusal above.
- **Plain pytest** on the final bytes: **24 passed** (both files, 122 s, macOS).

## Task 6 controls (each run on a scratch copy of `skills/` + `tests/`; all 24 nodes run each time)

| Control | Mutation | Expected red | Observed red |
|---|---|---|---|
| BASE | none | — | none (24 passed) |
| M0 | registry branch removed (old behaviour) | 1, 2, 11, 12, 14-20, 23 | 1, 2, 11, 12, 14, 15, 16, 17, 18, 19, 20, 23 |
| M2 | HEAD-before-bytes condition dropped | 5, 7 | 5, 7 |
| M5 | chain stored after `recorded`'s `yield` | 14 | 14, 21 (the store moved outside the try, so its failure now raises) |
| M6 | chain read before the bytes | 15 | 15 |
| M7 | set membership, no order or truncation | 7 | 5, 7 |
| M10 | plain FIFO cap (anchor evicted) | 17 | 17, 18 |
| M11 | compaction disabled | 18 | 18 |
| M12 | checker uses the raw `sdlc_dir` (no `.resolve()`) | 23 only | 23 only |
| M13 | `_key` without normalisation | 24 | 24 |
| M14 | XY check alone dropped | 6 (`M.`/`MM`) | 6, failing on the `staged` case |
| M15 | `mI == mW` alone dropped | 6 (chmod) | 6, failing on the `chmod` case (`core.fileMode` true here) |
| W1 | `proven` → True | 4, 5, 7 | 4, 5, 7, 8, 11, 15, 19 |
| W2 | XY + mode checks dropped | 6 | 6 (`staged` case) |
| W3 | non-registry v1 lines ignored | 3 | 3, 10 |
| W4 | v2 read before the prefilter | 10 | 10 |
| W5 | v2 error allows | 13 | 13 |
| W6 | record failure re-raised | 21 | 21 |
| W7 | no `state/`/symlink checks, mkdir parents | 22 | 21, 22 |
| WEAK | W1-W7 together (R-WEAK) | PINs + 21 | 3, 4, 5, 6, 7, 8, 10, 11, 13, 15, 19, 21, 22 |

Every expected red was seen, and no control failed to go red. Documented-gesture controls: test 23
drives `work.main(["work.py","start",".sdlc",...])` after `chdir`, red on the old code and under M12.
Test 1 runs §15's `git add .sdlc/features && git commit` verbatim. Test 8 runs the refusal's own
printed gesture through the shell. All three were seen red in R-OLD.

Other guards seen red, then green:
- `test_script_help.py -k allowlist`: red without `LIBRARY_ONLY += feature_provenance` ("…has no
  `__main__` block and is not allowlisted").
- `test_feature_registry.py::test_every_registry_writer_is_classified`: red ("…classify it in
  `_REGISTRY_ADJACENT_WRITERS`: ['feature_provenance']") until classified.
- `test_write_surface.py` ratchet: red without the rows, in the tracked clone.

## Measurements (Task 7.3). Method is in each line; macOS (Apple silicon, 8 cores, APFS), git 2.46.2, Python 3.13.4

- **v1 vs v2 status** on the #954 worktree (1,236 tracked files), median of 7, `subprocess.run`
  wall time: `git status --porcelain` **44.5 ms**;
  `git status --porcelain=v2 --untracked-files=no -- .sdlc/features` **33.4 ms**. The plan's 28.6 /
  46.6 were measured on a quieter machine. Two other agents' suites were running during this one.
- **Per-write record cost**, real module, `recorded()` around `os.replace`, temp file prepared per
  write:
  - plain replace: 0.494 ms mean over 300 writes;
  - recorded, one file written 300 times (chain grows to the cap): **4.75 ms mean**;
  - short chains (100 files × 3 writes, chain ≤ 3): **0.98 ms vs 0.19 ms median**, so about +0.8 ms.
  - At the cap, a profile puts most of the cost in JSON encoding of the 256-entry record plus a
    second `os.replace`. This is higher than the plan's prototype figure (0.52-0.61 ms). It is still
    a per-write constant: one file serialises at about 200 writes/s at the cap and about 1,000/s with
    short chains.
- **Record size at CAP**: 300 distinct writes → **256 entries, 11,315 bytes**.
- **Guard check with N dirty, proven registry shards** (real git; `_dirty_root_refusal`; median of 5
  after one warm-up): **N=20: 110 ms; N=200: 335 ms**.
- **Smoke run (test 16) iteration count**, replicated outside pytest: **10 checks** while 3 writers ×
  25 writes ran (0.7 s), 0 refused, writers exited `[0, 0, 0]`.
- **§6e** still reads **4 calls become 17**:
  `tests/test_docs.py -k section_6e` → 3 passed, run separately from the `test_work.py` selection
  (refinement 4). That selection,
  `-k "dirty or byte_identical or already_started or self_contradicting or rival_feature"`, gave
  7 passed.
- **Test runtime added**: the two new files take 122 s (24 tests) on this machine. Every `_load` here
  recompiles from source (`PYTHONDONTWRITEBYTECODE=1`).

## AC-5 — full suite with `python3` (the configured `python` is absent)

`python3 -m pytest -rs -q` over all 318 test files in 5 parallel shards, because there is no
pytest-xdist. Results: **13,238 passed, 5 failed, 23 skipped, 9 xfailed** (= 13,275 collected, which
equals `--collect-only`). Wall time 56.6 min (slowest shard).

The 5 failures:
- `tests/test_model_predict.py::test_codex_model_override_routes_goal_and_step_without_changing_claude`
  and `tests/test_loop.py::test_codex_only_model_override_reaches_pick_time_without_changing_claude`
  are **pre-existing**: both fail identically on a clean clone of `66b1f1b`. They are sensitive to
  this session's Claude Code host environment variables, and they are unrelated to #954.
- `tests/test_readiness_exposure_scan.py::test_the_tracked_scan_of_this_tree_exits_0_with_no_stale_allowlist_entry`
  hit its 60 s subprocess timeout under load. Re-run alone it **passed** (43 s), and it also passes
  on the HEAD clone.
- `tests/test_skill_structure.py::test_every_path_a_shipped_doc_names_ships` is a pre-commit artifact:
  a doc cited the untracked `tests/test_registry_provenance.py`. It passed in the tracked clone. The
  citation was then **removed** from `docs/branching-model.md` §8e, and the test now passes in the
  worktree.
- `tests/test_write_surface.py::test_committed_inventory_matches_the_tracked_write_surface` is the
  untracked-module ratchet described above. It is green once tracked.

`test_write_surface.py`, `test_script_help.py` and `test_shared_sdlc_paths.py` together:
**57 passed** in the tracked clone. `test_shared_sdlc_paths.py` was green with **no fixture edit**
(the D8 context-manager shape).

## AC-3

`git diff $(git merge-base HEAD origin/main) -- skills hooks | grep '^+.*stash'` prints nothing (the
merge-base is `66b1f1b`, refinement 6). The untracked `feature_provenance.py` contains no `stash`
either. Test 9 scans every `work._dirty_root*` function plus the module.

## Deviations from the plan (all deliberate)

- **No commit (Task 7.1)**. The dispatch forbids `git commit`/`git add`, so the write-surface ratchet
  is green only in a scratch clone (refinement 1).
- **The write-surface rows follow `scan_paths`, not the plan's predicted table.** They are
  `feature_provenance.py:_acquire fs-write 1 (ungated, medium)`, `_store_chain fs-remove 2 (high)`
  and `_store_chain fs-write 1 (medium)`. The lock's `os.open(O_CREAT)` lives in `_acquire`, not in
  `recorded`. The chokepoint rows are unchanged (one `os.replace` each).
- **Inventory classification.** `feature_provenance` joins with `os.path.join`, so the `/`-predicate
  address-builder inventory does not discover it. A `_NO_UNIT_NAME` entry would fail that test's
  exact-equality check. The module is classified in `_REGISTRY_ADJACENT_WRITERS` instead, which is
  the inventory that did discover it.
- **Fail closed on unexpected checker errors.** `_dirty_root_refusal` wraps the registry branch, so
  any unexpected error there (for example a module that will not load) gives today's refusal rather
  than raising from `start()`.
- **Test 18 skips with no `fcntl`, as test 16 does** (refinement 7). R-OLD restored nothing, because
  the code was already at HEAD (refinement 2).
- **Doc citation removed.** §8e no longer cites the test file path (see AC-5).

## Follow-ups to file (from plan §9)

- F-1: `amend` drops out-of-schema shard fields (TD-2).
- F-2: page render outside the unit lock (TD-3).
- F-3: other tracked Sigma bookkeeping (TD-6).
- F-4: exemption under autocrlf/clean filters.
- F-5: #53, the git-shelving ban across `rebase --autostash` callers.
- F-6: `red_green.py` separator and `Captured` attribution.
- New: the configured verify command assumes a `python` on PATH, which this host does not have.

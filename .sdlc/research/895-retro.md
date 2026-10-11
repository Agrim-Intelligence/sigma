# #895 slice 3b retro (P7)

PR body `Refs #895` (does not close). Nothing here was tested live; every claim is from unit tests and `scan()`.

## Delivered vs scope
- Original 3b scope: wrapper seams AND label create. Delivered: the seams plus a `close_pr_gh` merged-PR guard. Label create x4 (sources x3, triage x1) deferred to 3c.
- New in `gh_api.py`: `run_gh(args, timeout=120) -> (rc, out, err)` (timeout -> 124) and `gh_argv(args)`.
- Moved onto them: `ledger._run_gh` (15 s cap), `feature_owner._run_gh`, `cross_repo._run_gh`, `status._github_counts` x2, `board_setup.Board.gh` (gh_api loaded lazily, directly by path, not via sources).
- `close_pr_gh.rest()` now rejects a 200 body with `merged: true`.
- Ratchet: `scan()` TOTAL 43 -> 37 (printed). BASELINE: cross_repo, feature_owner, ledger, board_setup removed; status 3 -> 1. One `write-surface.json` row added for `run_gh`.
- Diff: 18 files, 536+/265-. Plan estimated 15; the extra three are `write-surface.json`, the plan and the dossier.
- Why label create waited: it needs ~38 files, 5 subprocess fakes taught a REST POST handler, the gqlfake `_REST_WRITE_RE` change, and `write_surface._GH_API_WRITES` gaining `create_label`. That is over the ~25-file cap.

## Residual debt
- `sources.note()`: owner-accepted duplicate-on-retry ruling kept; not moved.
- `define.py` x2: `_label_create_argv` must keep creating `feature:*` labels (`create_label` would refuse them); `_repo_labels`.
- `status.py:174`: the "sources failed to load" fallback is still a direct `gh`.
- `doctor.py` x5: 4 board sites plus the `_gh_runner` seam (same kind as the ones moved here).
- Board sites (sources x16, board_migrate x2) belong to slice 5.
- `work.py` x3 and `verify_merge.py` x3 are untouched.

## Lessons (proposed only; no CLAUDE.md/AGENTS.md edit)
- Moving a wrapper seam is a spelling change, and the ratchet counts spelling. These five seams were already `gh api` calls, so 43 -> 37 is not 6 fewer REST migrations. Report ratchet drops as "sites moved", not "calls migrated".
- Plan-review found 5 blockers on the label half and 4 more on the seams. Counting files before committing to scope (the ~38 figure) is what made the split defensible.
- Implement found that `write_surface.py` needed a `run_gh` row, which the plan said it would not. The plan's "expected green with no edit" was a prediction, not a run. Run the gate during planning.
- A new primitive was justified because `bounded_runner` raises on non-zero exit and returns stdout only. Each seam needs a different failure shape (rc 124, RuntimeError, `""`).
- Delegation spies fail before the change, so they are the red-first tests for a pure move. Characterisation tests pass today and go red only under a Control.

## Unmeasured
- The ledger 15 s cap is a judgement. `decision_gate.py` reaches it from a PreToolUse hook with no hook timeout.
- Hook latency from loading `gh_api.py` (~1,500 lines) per fresh process: not measured.
- 405/422 on PATCH of a merged PR: derived from docs, never observed. Only the 200 + `merged:true` path has a test.
- Before this change none of the three wrappers had any timeout. Now each does; the live effect is unobserved.

## Proposed entry list for 3c
1. `gh_api.create_label` (REST POST); classify 422 "already exists" as success; carry the `--force` / `feature:*` refusal over; retarget `test_label_create_no_force` at it.
2. Convert sources x3 and triage x1 `label create` sites.
3. Subprocess fakes (5): add a REST POST labels handler. gqlfake: extend `_REST_WRITE_RE`.
4. `write_surface._GH_API_WRITES` gains `create_label`; update the `write-surface.json` rows.
5. Cost: state the 10x/100x cost of a 422 POST plus GET per existing label; consider listing once, then creating only the missing labels.
6. Leave `define.py` out unless `create_label` gets a deliberate opt-in for `feature:*`.
7. Docs: CHANGELOG, `cloud-sessions.md`, `label-model.md`. Bring the ~27 label-test files along.

# B6 late stores: dispositions for the patterns the first audit pass left open (#1261)

The first audit (revision `64709b39`) carried every unresolved pattern under B6 issue #419, which is now
closed. Re-running the documented gesture at revision `64076300` still left 11 patterns with no
disposition. This page records what each writer does, read from the code, and the decisions are in
`docs/launch/dispositions/1261.json`. After it the audit reports 0 unresolved patterns.

| Pattern | Writer | Grows per item? | Decision |
| --- | --- | --- | --- |
| `.sdlc/state/invocations`, `invocations/` | `invocation.py` `authorize` (records), `run_adapter` (run dirs) | one record per authorized action, only with an invocation adapter configured | pruned: `_prune` (line 449) removes entries older than 6,000 s, 50 per call (line 62), on every authorize (line 478); run dirs removed in a finally (line 307). Idle repo keeps its last records |
| `.sdlc/state/invocations/.lock` | `invocation.py` `_locked` (line 395) | no: one fixed file | bounded by construction |
| `.sdlc/state/codex-supervisor.lock` | `codex_runtime.py` `lock_file` (line 226) | no: one fixed file for the supervisor's life | bounded by construction |
| `.sdlc/state/drift-shadow.json` | `drift_shadow.py` `_save` (line 99) | one verdict per judged park | capped: newest 200 (line 24) |
| `.sdlc/state/decision-import-classes.json` | `decision_import.py` `_cache_save` (line 384) | one entry per classified question, model-classifier mode only | was UNBOUNDED; capped to the newest 5,000 (line 26) in this change, 61 B per entry measured, so about 0.3 MB. Test `test_class_cache_is_capped_and_keeps_the_newest` was seen red first |
| `.sdlc/state/spend-approval-used.json` | `spend_approval.py` `_counter_write` (line 305) | one entry per honoured human approval | no pruner or cap; accepted gap, issue #1263 (eviction must not re-arm a label) |
| `.sdlc/intake/<sha12>.json`, `<plan-hash>.filed.json` | `sigma-prd-intake/scripts/intake.py` (lines 245, 286) | one pair per human-run PRD intake | no pruner or cap; accepted gap, issue #1262 |
| `.sdlc/<part>`, `.sdlc/<part>/` | 55 call sites with a path the scanner cannot resolve | not a store | placeholder; see below |

## The generic `.sdlc/<part>` patterns

54 rows are call sites like `state.refuse_symlinks(sdlc_dir, path)`, `actionlog.safe_append(...)`,
`_patch_cursor(...)` and `_record(...)`; the scanner cannot resolve the path argument, so it files them
under the placeholder. One more row (`state.py` line 247) is the parent `mkdir` inside the symlink guard.
Reading each site's line: the files behind them are the action log, `STATE.md`, `inbox.md`, the landing
records (`state/landing/<goal>.json`, from `cross_repo.py` `_record`), the unit-landings records and their
`.recorded-<n>.json` markers (pruned at `feature_upkeep_landing.py` lines 224 and 317), upkeep state, the
human queue, the journey notes and the coexist notice. Each of those has its own row. One store was not
covered anywhere and has no pruner: `.sdlc/state/unit-approvals/<digest>.json` and `.used`
(`feature_land_approval.py` `approve` line 100 and `consume` line 158), one pair per human approval and head.
It is filed as #1264.

## What this does not prove

A static scan cannot show that no other writer hides behind `.sdlc/<part>`; the reading above is of the
54 listed lines and what they call, not a proof about code added later. Sizes for the intake plan, the
spend entry and the unit-approval record were not measured exactly. Growth rates of the three accepted
gaps are per human action, not per goal.

## Accepted gaps (launch:next, priority:P3)

- #1262 `.sdlc/intake/` plan and filed records
- #1263 `.sdlc/state/spend-approval-used.json`
- #1264 `.sdlc/state/unit-approvals/` (not visible to the audit)

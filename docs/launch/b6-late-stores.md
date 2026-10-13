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
| `.sdlc/state/spend-approval-used.json` | `spend_approval.py` `_counter_write` (line 305) | one entry per honoured human approval | capped (#1263): 2,000 full entries, older ones folded into 16-hex hash tombstones (at most 50,000) that still refuse reuse, so eviction never re-arms a label; about 0.2 MB plus 1.1 MB at the ceiling. The file write is fsynced; the read-modify-write is not locked, so a race can lose the loser's new entry (the issue-side used-marker and the post-write re-read remain the second record) |
| `.sdlc/intake/<sha12>.json`, `<plan-hash>.filed.json` | `sigma-prd-intake/scripts/intake.py` (lines 245, 286) | one pair per human-run PRD intake | capped (#1262): `prune` keeps the newest 50 finished intakes and drops unfiled plans after 90 days; mid-filing plans are never touched. Residuals: a pruned finished pair means re-running plan and file on the same PRD refiles every Dossier and the umbrella (`dossier.file` has no dedupe); a resume record orphaned by a re-plan with changed answers is not matched or pruned, so the cap is slightly loose |
| `.sdlc/<part>`, `.sdlc/<part>/` | 55 call sites with a path the scanner cannot resolve | not a store | placeholder; see below |

## The generic `.sdlc/<part>` patterns

54 rows are call sites like `state.refuse_symlinks(sdlc_dir, path)`, `actionlog.safe_append(...)`,
`_patch_cursor(...)` and `_record(...)`; the scanner cannot resolve the path argument, so it files them
under the placeholder. One more row (`state.py` line 247) is the parent `mkdir` inside the symlink guard.
Reading each site's line: the files behind them are the action log, `STATE.md`, `inbox.md`, the landing
records (`state/landing/<goal>.json`, from `cross_repo.py` `_record`), the unit-landings records and their
`.recorded-<n>.json` markers (pruned at `feature_upkeep_landing.py` lines 224 and 317), upkeep state, the
human queue, the journey notes and the coexist notice. Each of those has its own row. One store was not
covered anywhere and had no pruner: `.sdlc/state/unit-approvals/<digest>.json` and `.used`
(`feature_land_approval.py` `approve` and `consume`), one pair per human approval and head. It is now pruned
30 days after the approval expired (#1264, `feature_land_approval_prune.py`).

## What "0 unresolved" does not cover

The scanner does not follow paths built from constants such as `STORE_REL`, `ACKS_REL` or `MIRROR_REL`
(for example `"state/upkeep/level2"`), so "0 unresolved" means every pattern the scan produces has a
decision, not that every store is in the scan. Stores a reviewer found that the audit cannot see:

| Store | Writer | Status |
| --- | --- | --- |
| `.sdlc/plans/reconcile/<timestamp>-reconcile.json` | `reconcile.py` lines 692 and 767-795 | capped (#1267): newest 20, never younger than 30 days (`reconcile.py` `prune_proposals`); entry in `1261.json` as an unscanned row |
| `.sdlc/state/unit-approvals/` | `feature_land_approval.py` | pruned (#1264); entry in `1261.json` as an unscanned row |
| `.sdlc/state/upkeep/level2/<unit>.json` | `feature_upkeep_level2.py` (`STORE_REL`) | bounded per file (50 attempts), one file per unit, intentionally unbounded per unit |
| `.sdlc/state/board-mirror.ndjson`, `board-mirror.meta.json` | `mirror.py` (`MIRROR_REL`, `META_REL`) | apparently bounded (rewritten whole, one line per issue); the size beyond that was not traced |
| `.sdlc/state/ai_filed_counter.json` | `ai_filed_counter.py` | apparently bounded: 14-day prune |
| `.sdlc/state/gh-fallback.json` | `gh_api.py` (`_LOG_REL`) | apparently bounded: capped |
| `.sdlc/state/gh-rest-breaker.json` | `gh_api.py` (`_BREAKER_REL`) | apparently bounded: one record |
| `.sdlc/state/upkeep/acks.json`, `upkeep/last_start.json` | `feature_upkeep_pass.py`, `feature_upkeep_sched.py` | apparently bounded: one file each, rewritten in place |

"Apparently bounded" is a reading of the writer, not a measurement or a test.

## What this does not prove

A static scan cannot show that no other writer hides behind `.sdlc/<part>`; the reading above is of the
54 listed lines and what they call, not a proof about code added later. Sizes for the intake plan, the
spend entry and the unit-approval record were not measured exactly. Growth rates of the human-rate stores
are per human action, not per goal.

## Caps added after the first pass

The four accepted gaps of the first pass (#1262, #1263, #1264, #1267) were each given a cap or prune with a test
that was seen red when each clause of the cap was broken. The ceilings are in the rows above and in
`dispositions/1261.json`. What is still uncapped is the "intentionally unbounded" set named in the other `b6-*.md`
pages and the per-unit level-2 state above.

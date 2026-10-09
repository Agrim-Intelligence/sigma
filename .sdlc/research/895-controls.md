# #895 slice 2c controls (each guard broken once, seen red, restored)

Gesture: `env -i HOME=$HOME PATH=... $HOME/.sigma-venv312/bin/python -m pytest <files> -q -p no:cacheprovider`
(ratchet: the documented `... -m pytest tests/test_no_direct_gh.py`). Every mutation was reverted by the harness
(`git diff` checked; no mutation text left in the tree).

| # | Break | Files run | Result |
|---|-------|-----------|--------|
| C1 | `_rest_first`: drop the FALLBACK_KINDS check | test_gh_api | RED, 20 failed (401/403-perm/404/422/proxy matrix, read AND list) |
| C2 | `_rest_first`: drop the `graphql_available` gate | test_gh_api | RED, 4 failed (cloud + override-off, read AND list) |
| C3 | list fallback called outside `_rest_first` (gate bypassed) | gh_api, doctor, auto_unpark | RED, 10 failed incl. list cloud/override-off, `test_cloud_session_never_falls_back_from_doctor`, auto_unpark cloud, breaker-sharing |
| C4 | doctor `_raising_gh` swallows `_RawFailure` (returns "") | test_doctor | RED, 5 failed: 429 falls back once, updated-order fallback, transport, census completes from fallback, read-only test |
| C5a | auto_unpark parked read asks `direction="asc"` | test_auto_unpark | RED, 22 failed (ValueError refused, fail-open hides it, REST-argv assertions catch it) |
| C5b | doctor `_dependency_marker_scan` `updated` -> `created` | test_doctor | RED, 2 failed (REST params + fallback `--search sort:updated-desc`) |
| C5c | ValueError refusal of unexpressible orders removed | test_gh_api | RED, 1 failed (`..._unexpressible_orders_refused`) |
| C6a | ratchet: doctor BASELINE left at 13 (documented gesture) | test_no_direct_gh | RED, `test_no_stale_baseline_entry` |
| C6b | re-add an `["issue","list",...]` literal to reconcile.py (entry was removed) | test_no_direct_gh | RED, `test_no_new_file_with_direct_gh_sites` |
| C7 | non-list page guard removed in `list_issues_gh` | gh_api, assign, auto_unpark, reconcile | RED, 8 failed (dict/empty/string pages, assign warning arm, auto_unpark non-list) |
| C8 | auto_unpark blocking read: `except Exception` -> `except ValueError` | test_auto_unpark | RED, 3 failed (a read error would raise instead of `set()`) |
| C9 | gqlfake REST-list path regex off by one char | auto_unpark, reconcile, unpark, gh_api | RED, 60 failed (REST-argv-recorded assertions; fail-open arms no longer pass vacuously) |

Findings from running them:
- The generic-fake sweep (step 10) found one real vacuous pass: `tests/test_unpark.py::_runner` had no REST list branch,
  so `test_list_reports_incomplete_rather_than_empty_when_a_query_fails` still passed after its `fail_on` needle
  stopped matching (the REST argv was unanswered, so the read failed for the wrong reason). Fixed: `_runner` answers
  the REST list, and the needle is now `labels=sdlc:parked`.
- `fail_on` needles in the auto_unpark runners fire BEFORE the call is recorded, so a failed read leaves no entry in
  `run.calls`; tests that need the REST call recorded use `list_fail=` instead (see test_blocking_read_error_...).
- C9 note: the plan's `unexpected == []` assertion was replaced by "the REST list call was recorded" in each site
  test (a strict-unexpected list is only kept in the pre-existing REST-issue fakes), so the red is the missing record.
- Sweep method: a temporary trace in `list_issues_gh` logged every empty/non-list REST page while running the
  sweep-touching test files (-k unpark/hardening/hostile/onboarding/feature/github_project/init_flow/loop/promote/
  triage/sources/blockers/assign/reconcile/doctor/work/scope/define/handoff/status; 5408 passed). Hits were only
  doctor tests whose fake answers "" to every gh call (fail-open, same as before) and the deliberate non-list tests.
  The trace was removed. Not swept: files outside that -k selection (they ran green in the full suite only).

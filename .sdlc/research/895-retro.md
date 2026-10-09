# #895 slice 2b retro (P7, advisory)

Grade: achieved (done_when met; two control gestures and several items below are weaker or unmeasured than the plan implied).

## Outcome vs plan
- (1) `scan()` total re-run in the worktree: 84 (was 94); `tests/test_no_direct_gh.py` 29 passed with the lowered BASELINE. Met.
- (3) All 9 files green, re-run here with `$HOME/.sigma-venv312/bin/python -m pytest -q tests/test_<f>.py`: gh_api 93, no_direct_gh 29, auto_unpark 9, blockers 88, promote 42, unpark 70, reconcile 49, triage 117, brainstorm 278 (order of the output lines mapped by position; counts differ from file order, treat as 9 green files, not per-file attribution). Met.
- (2) merged-PR blocker RESOLVED: carried by `to_gh_shape` `merged_at` -> `MERGED` plus REST-shaped fixtures in the three `closed_state` sites; I did not re-run control C1 myself.
- (4) CHANGELOG states the live PR #928 read (state closed, merged_at set) and names five unmeasured items. Matches plan.
- Diff: 22 files, 8 source files touched lightly (5-13 lines each), no except-arm changed.

## Residual debt
- Slice 2c: `issue list` literals remain (grep of `"issue", "list"` under skills/ shows 11 hits, not classified here) plus `fetch_issues_rest` adapters, status.py:174 fallback.
- Control C4 gesture is inadequate: `gql_run=None` cannot fail, because `read_issue` does `gql_run = gql_run or run` (gh_api.py:452), so the fallback still reaches the same injected `run`. A valid control must pass a DIFFERENT sentinel runner as `gql_run` (or drop the pass-through and assert the fallback argv hit a distinct recorder). Plan text should not be trusted for C4.
- Same-shape concern for C2: a one-character path change is only red if every site test asserts `unexpected == []`; not re-verified by me.
- Injected-run sites (triage:1549, brainstorm:178) write no breaker or log: during a REST outage each pays REST + one fallback every call. Named in plan; not measured.
- Unmeasured (carried from plan): real cloud session, bot-author spelling beyond one sample, call counts at 10x/100x (reconcile is linear in actions; 5000/h ceiling), `unpark` latency on comment-heavy issues, old closed issue with null `state_reason`, REST on transferred/deleted issues.

## Lessons proposed (for a human; none applied)
1. In plans, each "break it once" control must state why the broken form can differ from the working form; flag defaulting params (`x or y`) that make a nulling control a no-op.
2. A site-migration slice should keep one shared fake-REST helper (tests/gqlfake.py was edited) so fakes never grow a gh-shaped fixture again.
3. Consider a ratchet check that the baseline equals `scan()` exactly, not only "not over", so a later slice cannot hide slack.

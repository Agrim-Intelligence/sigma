# #895 4a-2 PR B controls: break on purpose, run the documented gesture, see RED, restore, see green

Gesture (copied from tests/test_no_direct_gh.py / the plan's Verification): `python -m pytest <file>`, run as
`env -i HOME=$HOME PATH=... $HOME/.sigma-venv312/bin/python -m pytest <file> -q -p no:cacheprovider`, worktree
`.sdlc/work/895`. Each mutation was applied to the one source line, the whole named file run, the file then
restored byte-for-byte (cmp against a backup) and the suite re-run green (2067 passed over the 8 targeted files).

| Control | Mutation | File run | Result |
|---|---|---|---|
| C1 | reducer treats COMMENTED as decisive (gh_api `_rest_changes_requested`) | tests/test_gh_api.py | RED 6 (`reducer_semantics[cr-comment]`, parity x4, ignored-state) |
| C2 | drop the full-page-at-cap raise (reviews) | tests/test_gh_api.py | RED 1 (`full_page_at_the_cap_raises_never_truncates`) |
| C3 | unknown review state ignored | tests/test_gh_api.py | RED 9 (`unrecognised_state_raises[*]`) |
| C4a | drop `len >= limit` raise, REST list | tests/test_gh_api.py | RED 1 (`list_at_the_limit_raises_on_both_paths`) |
| C4b | drop `len >= limit` raise, fallback | tests/test_gh_api.py | RED 1 (same test, fallback half) |
| C5a | REST non-list/blank body returns `[]` | tests/test_gh_api.py | RED 6 (`non_list_reply_raises_and_never_means_no_pr[*]`) |
| C5b | fallback blank/non-list output returns `[]` | tests/test_gh_api.py | RED 7 (`malformed_fallback_raises_while_a_real_empty_list_does_not[*]`) |
| C6 | null-user review keyed by None | tests/test_gh_api.py | RED 5 (`one_ghost_can_never_clear_anothers_request`, parity x4) |
| C12 | fallback reducer treats COMMENTED as decisive (REST untouched) | tests/test_gh_api.py | RED 4 (the two-paths-agree parity test) |
| C10a | re-add a `["gh","pr","list",...]` literal to work.py | tests/test_no_direct_gh.py | RED 2 (`no_baseline_file_gained_sites`, `pr_view_fallback_argv_lives_only_in_gh_api`) |
| C10b | BASELINE work.py left at 8 (under-count) | tests/test_no_direct_gh.py | RED 1 (`no_stale_baseline_entry`) |
| C7 | `review_gate` swallows a reviews read error in changes mode | tests/test_work.py | RED 1 (`fails_closed_on_an_unreadable_reviews_read_in_every_mode[changes]`) |
| C8a | skip the `graphql_available()` check | tests/test_work.py | RED 3 (cloud/off approval-park x2, changes-in-cloud) |
| C8b | approval mode passes when the decision is unavailable | tests/test_work.py | RED 4 (approval parks x3 incl. `r4_approval_parks_when_the_review_decision_read_is_blocked`) |
| C9 | `_find_design_pr` maps a failed/malformed read to `(None, None)` | tests/test_work.py | RED 27 (every malformed-reply and unreadable-reply design test) |
| C11a | design_fallback fixture without the chdir isolation | tests/test_work.py | RED 1 (`design_fallback_state_never_lands_in_the_repo_checkout`) |

Notes. C11a wrote `.sdlc/state/gh-*.json` into the worktree while red; the two files were deleted afterwards and the
final green run leaves `.sdlc/state/` without any `gh-*.json`. The `sdlc_dir` half of C11 (drop `sdlc_dir=` from the
call) was not run separately: the same control test also asserts the breaker/log file IS written under the tmp cwd,
so it would go red for the opposite reason. Not run live against GitHub; nothing here measures latency.

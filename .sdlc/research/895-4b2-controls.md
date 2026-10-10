# #895 slice 4b-2 controls (gesture: $HOME/.sigma-venv312/bin/python -m pytest <file>)
## C7 comment_pr idempotent=True (fallback on 5xx/timeout)
        ("gh: request failed", subprocess.TimeoutExpired(["gh"], 120)),
sigma: gh REST PR #7 comment failed (server_5xx, HTTP 502); fell back to gh pr comment once
FAILED tests/test_gh_api.py::test_comment_pr_never_falls_back_unless_primary_rate_limit[gh: Server Error (HTTP 502)-None]
1 failed, 10 passed, 641 deselected in 0.36s
## C8 comment_pr returns the dict (no URL normalisation)
FAILED tests/test_gh_api.py::test_comment_pr_url_is_derived_from_id_when_html_url_is_absent_and_empty_when_neither
1 failed, 10 passed, 641 deselected in 0.34s
## C1 _viewer_permission: nothing granted -> WRITE
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_work.py -k 'viewer_permission or merge_rights'
FAILED tests/test_work.py::test_viewer_permission_raises_on_every_undeterminable_shape[emptyobj]
FAILED tests/test_work.py::test_viewer_permission_raises_on_every_undeterminable_shape[nested]
FAILED tests/test_work.py::test_viewer_permission_raises_on_every_undeterminable_shape[allfalse]
FAILED tests/test_work.py::test_merge_rights_reply_without_permissions_is_could_not_determine
4 failed, 28 passed, 814 deselected in 1.31s
## C2 push->ADMIN, triage->WRITE
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_work.py -k 'viewer_permission or merge_rights'
FAILED tests/test_work.py::test_viewer_permission_maps_the_highest_true_key[body2-WRITE]
FAILED tests/test_work.py::test_viewer_permission_maps_the_highest_true_key[body3-TRIAGE]
FAILED tests/test_work.py::test_viewer_permission_maps_the_highest_true_key[body5-WRITE]
FAILED tests/test_work.py::test_viewer_permission_maps_the_highest_true_key[body6-WRITE]
FAILED tests/test_work.py::test_merge_rights_maps_triage_and_read_to_no_merge_and_write_to_merge
5 failed, 27 passed, 814 deselected in 1.40s
## C3 string 'true' counts as true (type check off)
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_work.py -k 'viewer_permission or merge_rights'
FAILED tests/test_work.py::test_viewer_permission_raises_on_every_undeterminable_shape[strtrue]
FAILED tests/test_work.py::test_viewer_permission_raises_on_every_undeterminable_shape[inttrue]
FAILED tests/test_work.py::test_viewer_permission_raises_on_every_undeterminable_shape[nulltrue]
3 failed, 29 passed, 814 deselected in 1.24s
## C4 protection(): rules result dropped
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_work.py -k 'test_protection_'
FAILED tests/test_work.py::test_protection_reads_a_repo_that_enforces_only_through_rulesets
FAILED tests/test_work.py::test_protection_unions_classic_and_ruleset_checks_by_name_and_takes_the_max_reviews
FAILED tests/test_work.py::test_protection_a_failing_classic_read_leaves_the_ruleset_result
3 failed, 22 passed, 844 deselected in 1.70s
## C5 _ruleset_requirements: malformed shapes raise / invent requirement
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_work.py -k 'test_protection_'
FAILED tests/test_work.py::test_protection_malformed_rules_never_raise_and_never_invent_a_requirement[[{"type": "required_status_che3]
FAILED tests/test_work.py::test_protection_malformed_rules_never_raise_and_never_invent_a_requirement[[{"type": "pull_request", "par0]
2 failed, 23 passed, 844 deselected in 1.54s
## C6 classic failure discards the rules result
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_work.py -k 'test_protection_'
FAILED tests/test_work.py::test_protection_reads_a_repo_that_enforces_only_through_rulesets
FAILED tests/test_work.py::test_protection_encodes_a_slashed_base_in_the_rules_path
FAILED tests/test_work.py::test_protection_a_failing_classic_read_leaves_the_ruleset_result
3 failed, 22 passed, 844 deselected in 1.68s
## C8b comment_pr returns the raw dict -> post_review receipt tests
FAILED tests/test_work.py::test_post_review_receipt_comes_from_the_rest_reply_url_or_id
1 failed, 42 passed, 830 deselected in 16.66s
## C13 post_review POSTs unconditionally at entry -> the retargeted nothing-was-posted checks
____ test_post_review_a_retry_after_a_failed_post_counts_exactly_one_cycle _____
    def test_post_review_a_retry_after_a_failed_post_counts_exactly_one_cycle(tmp_path):
        """The retry itself must not double-count: a failed attempt leaves nothing behind, so a
FAILED tests/test_work.py::test_post_review_in_a_cloud_session_posts_over_rest_with_no_graphql_call
FAILED tests/test_work.py::test_post_review_in_a_cloud_session_primary_rate_limit_makes_zero_fallback_calls_and_parks
FAILED tests/test_work.py::test_post_review_refuses_missing_or_malformed_evidence_before_any_remote_post
FAILED tests/test_work.py::test_evidence_backed_success_post_records_one_typed_review_observation_and_is_idempotent
FAILED tests/test_work.py::test_evidence_backed_retry_after_ambiguous_post_is_reconcile_only
FAILED tests/test_work.py::test_evidence_backed_post_parks_before_comment_when_the_pr_head_moved
FAILED tests/test_work.py::test_post_review_refuses_evidence_hand_written_outside_the_generation_chain
FAILED tests/test_work.py::test_post_review_block_carries_the_reasons
FAILED tests/test_work.py::test_post_review_block_reason_is_scrubbed_before_the_public_pr_comment
FAILED tests/test_work.py::test_post_review_rejects_a_bad_verdict
FAILED tests/test_work.py::test_post_review_block_does_not_consume_a_cycle_when_the_comment_fails_to_post
FAILED tests/test_work.py::test_post_review_a_retry_after_a_failed_post_counts_exactly_one_cycle
12 failed, 95 passed, 766 deselected in 21.76s
## C14 write-surface: comment_pr and merge_pr_gh dropped from _GH_API_WRITES
FAILED tests/test_write_surface.py::test_committed_inventory_matches_the_tracked_write_surface
FAILED tests/test_write_surface.py::test_895_4b2_pr_comment_and_merge_helpers_are_seen_and_the_work_rows_are_pinned
FAILED tests/test_write_surface_upkeep_rules.py::test_gh_api_write_helpers_are_seen_by_name
FAILED tests/test_write_surface_upkeep_rules.py::test_gh_api_write_helper_names_cover_every_non_get_helper_in_the_module
4 failed, 64 passed in 4.17s
check: python3 tools/readiness/write_surface.py check . docs/launch/write-surface.json
stale entry skills/sigma-loop/scripts/work.py:merge gh-api-write
stale entry skills/sigma-loop/scripts/work.py:post_review gh-api-write
## C12 write-surface: leave the removed post_review gh-pr entry
stale entry skills/sigma-loop/scripts/work.py:post_review gh-pr
## C10 merge_rights reverted to the GraphQL viewerPermission read
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_work.py -k 'cloud_session'
FAILED tests/test_work.py::test_a_real_cloud_session_with_no_permissions_in_the_reply_still_does_not_merge
FAILED tests/test_work.py::test_a_cloud_session_merges_over_rest_with_every_graphql_call_refused
2 failed, 6 passed, 867 deselected in 2.09s
## C-D2 (reviewer refinement 1) protection(): rules read dropped (D2 reverted) -> D4 must go red
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_work.py -k 'cloud_session'
FAILED tests/test_work.py::test_a_cloud_session_merges_over_rest_with_every_graphql_call_refused
1 failed, 7 passed, 867 deselected in 2.04s
## C11 merge_rights fail-open on a null/empty permissions reply -> twin red
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_work.py -k 'cloud_session'
FAILED tests/test_work.py::test_a_real_cloud_session_with_no_permissions_in_the_reply_still_does_not_merge
FAILED tests/test_work.py::test_a_cloud_session_without_permissions_in_the_repo_reply_sends_no_put
2 failed, 6 passed, 867 deselected in 2.26s
## C9 re-add the [gh, pr, comment] literal to work.py
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_no_direct_gh.py
FAILED tests/test_no_direct_gh.py::test_no_baseline_file_gained_sites
1 failed, 9 passed in 2.02s
## C9b stale BASELINE (work.py left at 6)
FAILED tests/test_no_direct_gh.py::test_no_stale_baseline_entry
1 failed, 9 passed in 2.02s

All controls above: broken on purpose, run with the documented gesture, seen red, restored, and the file seen green again afterwards. C7's timeout parametrisation stays green by design (transport_ambiguous never falls back for idempotent or not); only the 5xx shape separates the policies.

## C12 send-back fixes: review_gate thread check without GraphQL, protection() int/empty-name guards
broken on purpose in work.py: `if not gh_api.graphql_available()["available"]` -> `if False`; `reviews = n or 0` (was non-bool int > 0 guard); `checks.discard(None)` (was `checks -= {None, ""}`)
gesture: /Users/swapnildubey/.sigma-venv312/bin/python -m pytest tests/test_work.py -k 'line_comment or cloud_gate or non_int or empty_string_classic or no_graphql_empty or cloud_session'
FAILED test_a_cloud_session_with_a_pr_line_comment_refuses_and_sends_no_put (the pre-fix code MERGED over an existing line comment: the PUT was sent)
FAILED test_review_gate_no_graphql_empty_line_comments_passes_with_one_rest_read, test_review_gate_no_graphql_line_comments_or_unreadable_fails_closed[x6]
FAILED test_protection_a_non_int_classic_review_count_does_not_discard_the_ruleset_result, test_protection_an_empty_string_classic_check_name_is_discarded
10 failed, 8 passed. Restored: 18 passed; then the 8 suites green (test_work 885, test_gh_api 652, test_merge_reconcile 59, test_no_autonomous_feature_branch_deletion 13, test_public_bootstrap_control 17, test_hardening_697 17, test_phase_context_budget 7, test_skill_structure 55).

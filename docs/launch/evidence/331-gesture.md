# #331 gesture and controls

## What was measured

The commit that will carry this file does not exist when it is written, and naming it here would
change it, so this record pins content instead of a commit. Every value below is a git object id
that is stable across commits: it is what `git rev-parse <commit>:<path>` prints on ANY commit
whose file is byte-identical to the one measured.

| Object | Id |
|---|---|
| `origin/main` when measured (the base) | `b67a3e38e8e6a207f8486b563f0e5c5e65f00851` |
| tree `tools/readiness` (holds only `decide.py`, mode 100755) | `52ad0bf8f5a76bafc7f79dff4058217349a9e6aa` |
| blob `tools/readiness/decide.py` | `f12e9e55f28a04e98dbbc0dd91656e6a535d5721` |
| blob `tests/test_readiness_decide.py` | `d1eb6e2f926b47cd4adfec8fd0a91bff53137df6` |
| blob `docs/launch/decision-rule.md` | `089cc7ce05dee04970d7bc716ca6c84e570a5a32` |
| blob `docs/launch/scorecard.json` | `a9642c419a1aece5078c43a109b8e87bfc90400f` |

Check a commit with:

```
git rev-parse <commit>:tools/readiness <commit>:tests/test_readiness_decide.py \
  <commit>:docs/launch/decision-rule.md <commit>:docs/launch/scorecard.json
```

The `docs/launch` tree is deliberately not pinned: it contains this file, so its id changes
whenever this record does. If any id differs, what was merged is not what was measured; rerun the
gesture and the controls.

Measured 2026-09-30 on macOS, branch `sdlc/331`.

## The documented gesture

From the repository root, exactly as `docs/launch/decision-rule.md` gives it (live, read-only
`gh api` read of open `launch:blocker` issues on `github.com`, `OWNER/NAME` derived from `origin`;
system `python3`, Python 3.9.6), then the same with `GH_REPO`/`GH_HOST` pointing elsewhere, a
path-traversal `--repo`, and `--help`:

```
$ python3 tools/readiness/decide.py .
definition missing: docs/launch/definition.json does not exist
D1 Correctness: not scored (gating; needs >= 3 with evidence)
D2 Skills: not scored (gating; needs >= 3 with evidence)
D3 Outcomes and cost: not scored (gating; needs >= 3 with evidence)
D5 Five properties: not scored (gating; needs >= 3 with evidence)
D6 Platform: not scored (gating; needs >= 3 with evidence)
D7 Onboarding: not scored (gating; needs >= 3 with evidence)
D8 Docs: not scored (gating; needs >= 3 with evidence)
D9 Upgrade, coexistence, uninstall: not scored (gating; needs >= 3 with evidence)
D10 Security, privacy, exposure: not scored (gating; needs >= 3 with evidence)
D11 Operations: not scored (gating; needs >= 3 with evidence)
D13 Legal and naming: not scored (gating; needs >= 3 with evidence)
benchmark results: not named in the scorecard
info: D12 Market is informational (unscored); it blocks only through B3
NO-GO
(exit 1)

$ GH_REPO=cli/cli GH_HOST=example.invalid python3 tools/readiness/decide.py .
[... the same 13 reason lines as above, byte-identical ...]
info: D12 Market is informational (unscored); it blocks only through B3
NO-GO
(exit 1)

$ python3 tools/readiness/decide.py . --repo ../..
decide.py: MALFORMED: --repo must be OWNER/NAME (each starting with a letter, digit or `_`; letters, digits, `_`, `.`, `-`; at most 100), got '../..'
(exit 2)

$ python3 tools/readiness/decide.py --help >/dev/null
(exit 2)
```

NO-GO, exit 1: nothing is scored, there is no signed definition, no benchmark file is named, and
zero open `launch:blocker` issues were returned (none appear as a reason). The `GH_REPO` run prints
the same lines, but that alone proves nothing, since the other repository has no blockers either.
What proves the read is not redirected is
`test_documented_gesture_with_gh_repo_set_reads_this_repository`: it runs this same gesture with a
stub `gh` first on `PATH` and asserts that `gh` was called with `--hostname=github.com` and
`repos/acme/widget/...` (the fixture's `origin`) and with `GH_REPO` unset.

## Controls seen red

Each guard was broken once in `tools/readiness/decide.py` (one mutation at a time, file restored
after each) and all of `tests/test_readiness_decide.py` was run (`pytest -n 6`) on Python 3.10 and
3.12. `red=True` means the suite failed AND the failures include the test that targets that guard.
The first row is the acceptance control. Unmutated, the suite is 181 passed on 3.10, 3.12 and the
system 3.9.6. The first full run found one control not red, "directory accepted as file": the
`origin/main` regular-file check still made the entry NO-GO, with a different reason. So
`test_directory_evidence_is_not_a_file` was added to pin the reason, and that control was rerun;
its rows ran 181 tests, the other rows ran before that test existed and ran 180.

```
3.10 red=True | blockers list ignored (acceptance control) | 17 failed, 163 passed | test_bad_blocker_list_is_malformed, test_blocker_entry_of_the_wrong_shape_is_malformed, test_blocker_state_matches_case_insensitively, test_blocker_with_unknown_or_missing_state_is_malformed, test_gh_path_uses_rest_paginated_and_decodes_every_page, test_one_open_blocker_is_nogo_naming_its_number
3.12 red=True | blockers list ignored (acceptance control) | 17 failed, 163 passed | test_bad_blocker_list_is_malformed, test_blocker_entry_of_the_wrong_shape_is_malformed, test_blocker_state_matches_case_insensitively, test_blocker_with_unknown_or_missing_state_is_malformed, test_gh_path_uses_rest_paginated_and_decodes_every_page, test_one_open_blocker_is_nogo_naming_its_number
3.10 red=True | evidence validity skipped | 23 failed, 157 passed | test_empty_evidence_file_is_nogo, test_evidence_entry_that_is_not_a_link_is_nogo_naming_it, test_evidence_file_without_origin_main_is_nogo, test_one_bad_entry_beside_a_good_one_is_still_nogo, test_symlink_evidence_escaping_the_repo_is_nogo, test_untracked_evidence_file_is_nogo
3.12 red=True | evidence validity skipped | 23 failed, 157 passed | test_empty_evidence_file_is_nogo, test_evidence_entry_that_is_not_a_link_is_nogo_naming_it, test_evidence_file_without_origin_main_is_nogo, test_one_bad_entry_beside_a_good_one_is_still_nogo, test_symlink_evidence_escaping_the_repo_is_nogo, test_untracked_evidence_file_is_nogo
3.10 red=True | URL host not required | 1 failed, 179 passed | test_evidence_entry_that_is_not_a_link_is_nogo_naming_it
3.12 red=True | URL host not required | 1 failed, 179 passed | test_evidence_entry_that_is_not_a_link_is_nogo_naming_it
3.10 red=True | symlink/resolve escape not checked | 1 failed, 179 passed | test_symlink_evidence_escaping_the_repo_is_nogo
3.12 red=True | symlink/resolve escape not checked | 1 failed, 179 passed | test_symlink_evidence_escaping_the_repo_is_nogo
3.10 red=True | directory accepted as file | 1 failed, 180 passed | test_directory_evidence_is_not_a_file
3.12 red=True | directory accepted as file | 1 failed, 180 passed | test_directory_evidence_is_not_a_file
3.10 red=True | unnamed benchmark passes | 3 failed, 177 passed | test_unnamed_benchmark_file_is_nogo
3.12 red=True | unnamed benchmark passes | 3 failed, 177 passed | test_unnamed_benchmark_file_is_nogo
3.10 red=True | path outside evidence/ accepted | 1 failed, 179 passed | test_steering_or_bad_scorecard_is_malformed
3.12 red=True | path outside evidence/ accepted | 1 failed, 179 passed | test_steering_or_bad_scorecard_is_malformed
3.10 red=True | untracked benchmark accepted | 2 failed, 178 passed | test_untracked_benchmark_is_nogo, test_untracked_evidence_file_is_nogo
3.12 red=True | untracked benchmark accepted | 2 failed, 178 passed | test_untracked_benchmark_is_nogo, test_untracked_evidence_file_is_nogo
3.10 red=True | dirty benchmark accepted | 1 failed, 179 passed | test_dirty_benchmark_is_nogo
3.12 red=True | dirty benchmark accepted | 1 failed, 179 passed | test_dirty_benchmark_is_nogo
3.10 red=True | empty-at-main accepted | 2 failed, 178 passed | test_empty_benchmark_at_origin_main_is_nogo, test_empty_evidence_file_is_nogo
3.12 red=True | empty-at-main accepted | 2 failed, 178 passed | test_empty_benchmark_at_origin_main_is_nogo, test_empty_evidence_file_is_nogo
3.10 red=True | unknown/missing state dropped | 1 failed, 179 passed | test_blocker_with_unknown_or_missing_state_is_malformed
3.12 red=True | unknown/missing state dropped | 1 failed, 179 passed | test_blocker_with_unknown_or_missing_state_is_malformed
3.10 red=True | gh empty output read as zero | 2 failed, 178 passed | test_gh_exit_0_with_empty_output_is_malformed
3.12 red=True | gh empty output read as zero | 2 failed, 178 passed | test_gh_exit_0_with_empty_output_is_malformed
3.10 red=True | several remotes accepted | 1 failed, 179 passed | test_default_repo_needs_exactly_one_origin_remote
3.12 red=True | several remotes accepted | 1 failed, 179 passed | test_default_repo_needs_exactly_one_origin_remote
3.10 red=True | unknown top-level key ignored | 1 failed, 179 passed | test_steering_or_bad_scorecard_is_malformed
3.12 red=True | unknown top-level key ignored | 1 failed, 179 passed | test_steering_or_bad_scorecard_is_malformed
3.10 red=True | unknown dimension key ignored | 1 failed, 179 passed | test_steering_or_bad_scorecard_is_malformed
3.12 red=True | unknown dimension key ignored | 1 failed, 179 passed | test_steering_or_bad_scorecard_is_malformed
3.10 red=True | renamed dimension accepted | 1 failed, 179 passed | test_steering_or_bad_scorecard_is_malformed
3.12 red=True | renamed dimension accepted | 1 failed, 179 passed | test_steering_or_bad_scorecard_is_malformed
3.10 red=True | impossible date accepted | 3 failed, 177 passed | test_signed_without_signer_or_date_is_nogo
3.12 red=True | impossible date accepted | 3 failed, 177 passed | test_signed_without_signer_or_date_is_nogo
3.10 red=True | B1 gh left to resolve {owner}/{repo} | 13 failed, 167 passed | test_default_repo_is_derived_from_origins_url, test_documented_gesture_with_gh_repo_set_reads_this_repository, test_gh_repo_and_gh_host_cannot_redirect_the_read
3.12 red=True | B1 gh left to resolve {owner}/{repo} | 13 failed, 167 passed | test_default_repo_is_derived_from_origins_url, test_documented_gesture_with_gh_repo_set_reads_this_repository, test_gh_repo_and_gh_host_cannot_redirect_the_read
3.10 red=True | B1 GH_REPO/GH_HOST not scrubbed | 3 failed, 177 passed | test_documented_gesture_with_gh_repo_set_reads_this_repository, test_gh_repo_and_gh_host_cannot_redirect_the_read
3.12 red=True | B1 GH_REPO/GH_HOST not scrubbed | 3 failed, 177 passed | test_documented_gesture_with_gh_repo_set_reads_this_repository, test_gh_repo_and_gh_host_cannot_redirect_the_read
3.10 red=True | B1 --hostname not passed | 20 failed, 160 passed | test_default_repo_is_derived_from_origins_url, test_documented_gesture_with_gh_repo_set_reads_this_repository, test_gh_path_uses_rest_paginated_and_decodes_every_page, test_gh_repo_and_gh_host_cannot_redirect_the_read, test_valid_repo_argument_is_used_verbatim
3.12 red=True | B1 --hostname not passed | 20 failed, 160 passed | test_default_repo_is_derived_from_origins_url, test_documented_gesture_with_gh_repo_set_reads_this_repository, test_gh_path_uses_rest_paginated_and_decodes_every_page, test_gh_repo_and_gh_host_cannot_redirect_the_read, test_valid_repo_argument_is_used_verbatim
3.10 red=True | B1 origin with several URLs accepted | 1 failed, 179 passed | test_origin_with_two_urls_is_malformed
3.12 red=True | B1 origin with several URLs accepted | 1 failed, 179 passed | test_origin_with_two_urls_is_malformed
3.10 red=True | B1 origin URL path not validated | 6 failed, 174 passed | test_origin_url_of_an_unknown_shape_is_malformed
3.12 red=True | B1 origin URL path not validated | 6 failed, 174 passed | test_origin_url_of_an_unknown_shape_is_malformed
3.10 red=True | B2 old --repo segment pattern | 10 failed, 170 passed | test_origin_url_of_an_unknown_shape_is_malformed, test_repo_argument_that_is_not_owner_name_is_malformed
3.12 red=True | B2 old --repo segment pattern | 10 failed, 170 passed | test_origin_url_of_an_unknown_shape_is_malformed, test_repo_argument_that_is_not_owner_name_is_malformed
3.10 red=True | B2 --repo matched with $ not fullmatch | 1 failed, 179 passed | test_repo_argument_that_is_not_owner_name_is_malformed
3.12 red=True | B2 --repo matched with $ not fullmatch | 1 failed, 179 passed | test_repo_argument_that_is_not_owner_name_is_malformed
3.10 red=True | B3 unverifiable main passes the benchmark | 6 failed, 174 passed | test_git_missing_is_nogo_cannot_verify, test_no_origin_main_ref_is_nogo_cannot_verify, test_not_a_git_work_tree_is_nogo_cannot_verify, test_repo_root_below_the_git_top_level_is_nogo_cannot_verify, test_symlinked_benchmark_without_git_is_nogo, test_untracked_never_committed_benchmark_without_origin_main_is_nogo
3.12 red=True | B3 unverifiable main passes the benchmark | 6 failed, 174 passed | test_git_missing_is_nogo_cannot_verify, test_no_origin_main_ref_is_nogo_cannot_verify, test_not_a_git_work_tree_is_nogo_cannot_verify, test_repo_root_below_the_git_top_level_is_nogo_cannot_verify, test_symlinked_benchmark_without_git_is_nogo, test_untracked_never_committed_benchmark_without_origin_main_is_nogo
3.10 red=True | B3 root below top level accepted | 1 failed, 179 passed | test_repo_root_below_the_git_top_level_is_nogo_cannot_verify
3.12 red=True | B3 root below top level accepted | 1 failed, 179 passed | test_repo_root_below_the_git_top_level_is_nogo_cannot_verify
3.10 red=True | B3 checkout symlink accepted | 1 failed, 179 passed | test_benchmark_symlinked_in_the_checkout_is_nogo
3.12 red=True | B3 checkout symlink accepted | 1 failed, 179 passed | test_benchmark_symlinked_in_the_checkout_is_nogo
3.10 red=True | B3 symlink blob at main accepted | 1 failed, 179 passed | test_benchmark_committed_as_a_symlink_is_nogo
3.12 red=True | B3 symlink blob at main accepted | 1 failed, 179 passed | test_benchmark_committed_as_a_symlink_is_nogo
3.10 red=True | (a) duplicate keys accepted | 5 failed, 175 passed | test_duplicate_gating_key_cannot_flip_a_dimension, test_duplicate_key_in_blocker_list_is_malformed, test_duplicate_key_in_definition_is_malformed, test_duplicate_key_in_gh_output_is_malformed, test_duplicate_key_in_scorecard_is_malformed
3.12 red=True | (a) duplicate keys accepted | 5 failed, 175 passed | test_duplicate_gating_key_cannot_flip_a_dimension, test_duplicate_key_in_blocker_list_is_malformed, test_duplicate_key_in_definition_is_malformed, test_duplicate_key_in_gh_output_is_malformed, test_duplicate_key_in_scorecard_is_malformed
3.10 red=True | (b) unparsable evidence URL raises | 1 failed, 179 passed | test_evidence_entry_that_is_not_a_link_is_nogo_naming_it
3.12 red=True | (b) unparsable evidence URL raises | 1 failed, 179 passed | test_evidence_entry_that_is_not_a_link_is_nogo_naming_it
3.10 red=True | (b) unreadable-input catches removed | 3 failed, 177 passed | test_unreadable_input_is_malformed_never_a_traceback
3.12 red=True | (b) unreadable-input catches removed | 3 failed, 177 passed | test_unreadable_input_is_malformed_never_a_traceback
3.10 red=True | (c) evidence not checked at main | 2 failed, 178 passed | test_empty_evidence_file_is_nogo, test_untracked_evidence_file_is_nogo
3.12 red=True | (c) evidence not checked at main | 2 failed, 178 passed | test_empty_evidence_file_is_nogo, test_untracked_evidence_file_is_nogo
3.10 red=True | (c) evidence passes when main unreadable | 1 failed, 179 passed | test_evidence_file_without_origin_main_is_nogo
3.12 red=True | (c) evidence passes when main unreadable | 1 failed, 179 passed | test_evidence_file_without_origin_main_is_nogo
3.10 red=True | (c) empty evidence at main accepted | 2 failed, 178 passed | test_empty_benchmark_at_origin_main_is_nogo, test_empty_evidence_file_is_nogo
3.12 red=True | (c) empty evidence at main accepted | 2 failed, 178 passed | test_empty_benchmark_at_origin_main_is_nogo, test_empty_evidence_file_is_nogo
3.10 red=True | (d) signed_by not a plain login | 4 failed, 176 passed | test_signed_without_signer_or_date_is_nogo
3.12 red=True | (d) signed_by not a plain login | 4 failed, 176 passed | test_signed_without_signer_or_date_is_nogo
3.10 red=True | (d) future signed_on accepted | 1 failed, 179 passed | test_signed_without_signer_or_date_is_nogo
3.12 red=True | (d) future signed_on accepted | 1 failed, 179 passed | test_signed_without_signer_or_date_is_nogo
3.10 red=True | (e) --help exits 0 | 3 failed, 177 passed | test_help_exits_2_never_the_go_code, test_help_via_the_documented_gesture_exits_2
3.12 red=True | (e) --help exits 0 | 3 failed, 177 passed | test_help_exits_2_never_the_go_code, test_help_via_the_documented_gesture_exits_2
3.10 red=True | (f) labels not required | 5 failed, 175 passed | test_blocker_entry_of_the_wrong_shape_is_malformed
3.12 red=True | (f) labels not required | 5 failed, 175 passed | test_blocker_entry_of_the_wrong_shape_is_malformed
3.10 red=True | (f) non-object pull_request dropped | 2 failed, 178 passed | test_blocker_entry_of_the_wrong_shape_is_malformed
3.12 red=True | (f) non-object pull_request dropped | 2 failed, 178 passed | test_blocker_entry_of_the_wrong_shape_is_malformed
```

39 of 39 controls red on 3.10, 39 of 39 on 3.12.

# Control evidence — goal 2066

Every guard clause below was broken on its own, on the final bytes of this change, and the named test was seen red.
The file was then restored byte-for-byte (the runner compares sha256). The runner applies one edit, runs the named
tests with `pytest -x`, and records the first failure. It ran with `SIGMA_TRUSTED_VERIFY_SHA256` exported for the
whole run, so every row also shows the hermetic conftest fixture holding. macOS (Darwin 27), Python 3.13,
2026-10-27: 70 of 70 controls red; the one GREEN row is the fixture control's positive half.

Four earlier first runs stayed GREEN. Each was a weak test or a wrong control, fixed before this run:
- `:(top)` dropped, and `--full-name` dropped: masked by the main-checkout scan in a non-linked repository.
  The tests were rebuilt on a linked worktree.
- The verify gate wired to the config command (acceptance case): the test's acceptance record was not valid
  JSON, so verify refused before the gate. The record is now valid and the refusal reason is asserted.
- The doctor echoing a malformed value: the control read the variable at import time. It now reads it at call
  time.


## Policy core (`shell_policy.py`)

| Break | Result | First failing test |
| --- | --- | --- |
| domain prefix changed | RED | `tests/test_shell_policy.py::test_approval_value_known_answer - Asserti...` |
| fullmatch -> match | RED | `tests/test_shell_policy.py::test_a_matching_value_that_is_not_exactly_lowercase_hex_refuses[{v}\n]` |
| hex class widened | RED | `tests/test_shell_policy.py::test_empty_or_malformed_values_refuse[AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA-malformed]` |
| spawn before the value is read | RED | `tests/test_shell_policy.py::test_unset_spawns_nothing_and_refuses - As...` |
| all -> any over commands | RED | `tests/test_shell_policy.py::test_every_declared_command_must_match - A...` |
| no-command clause deleted | RED | `tests/test_shell_policy.py::test_every_declared_command_must_match - A...` |
| outer try deleted | RED | `tests/test_shell_policy.py::test_any_exception_refuses - UnicodeEncode...` |
| identity dropped from the value | RED | `tests/test_shell_policy.py::test_another_repository_or_no_origin_is_not_trusted` |
| identity not case-folded | RED | `tests/test_shell_policy.py::test_identity_is_the_origin_owner_and_name_case_folded[https://github.com/Owner/Repo.git]` |
| veto: explicit false ignored | RED | `tests/test_shell_policy.py::test_an_explicit_git_local_false_vetoes_the_environment_value` |
| veto: non-boolean continues | RED | `tests/test_shell_policy.py::test_a_non_boolean_git_local_value_vetoes_the_environment_value` |
| repository-sourced clause deleted | RED | `tests/test_shell_policy.py::test_a_tracked_host_config_file_naming_the_variable_refuses[.claude/settings.json]` |
| :(top) pathspecs dropped | RED | `tests/test_shell_policy.py::test_repository_sourced_is_checked_from_the_repository_top` |
| relative pathspecs dropped | RED | `tests/test_shell_policy.py::test_a_project_root_in_a_subfolder_is_checked_relative_to_itself` |
| untracked files counted too | RED | `tests/test_shell_policy.py::test_an_untracked_host_config_file_or_another_tracked_file_is_not_a_loader` |
| escape decoding dropped | RED | `tests/test_shell_policy.py::test_an_escaped_or_recased_name_is_still_seen[{"env": {"\\u0053IGMA_TRUSTED_VERIFY_SHA256": "x"}}]` |
| \U decoding dropped | RED | `tests/test_shell_policy.py::test_an_escaped_or_recased_name_is_still_seen[[env]\n"\\U00000053IGMA_TRUSTED_VERIFY_SHA256" = "x"\n]` |
| \x decoding dropped | RED | `tests/test_shell_policy.py::test_an_escaped_or_recased_name_is_still_seen[[env]\n"\\x53IGMA_TRUSTED_VERIFY_SHA256" = "x"\n]` |
| case-sensitive search | RED | `tests/test_shell_policy.py::test_an_escaped_or_recased_name_is_still_seen[export sigma_trusted_verify_sha256=x\n]` |
| file-level symlink/gitlink clause dropped | RED | `tests/test_shell_policy.py::test_a_gitlink_at_a_host_config_path_or_parent_refuses[.claude/settings.json]` |
| outside-toplevel refusal dropped | RED | `tests/test_shell_policy.py::test_a_host_config_file_reached_through_a_symlink_out_of_the_repository_refuses` |
| non-regular clause dropped | RED | `tests/test_shell_policy.py::test_a_directory_at_a_tracked_host_config_path_refuses` |
| size clause dropped | RED | `tests/test_shell_policy.py::test_an_unreadable_or_oversized_host_config_file_refuses[over-1MiB]` |
| UTF-8 clause dropped | RED | `tests/test_shell_policy.py::test_an_unreadable_or_oversized_host_config_file_refuses[invalid-utf8]` |
| C1 parent symlink/gitlink refusal dropped | RED | `tests/test_shell_policy.py::test_a_tracked_symlinked_parent_directory_refuses` |
| C1 parent pathspecs dropped | RED | `tests/test_shell_policy.py::test_a_tracked_symlinked_parent_directory_refuses` |
| main checkout skipped | RED | `tests/test_shell_policy.py::test_the_main_checkout_and_the_project_root_are_checked_by_their_own_tree` |
| roots ignored | RED | `tests/test_shell_policy.py::test_the_main_checkout_and_the_project_root_are_checked_by_their_own_tree` |
| C3 every listing joined to the verify tree | RED | `tests/test_shell_policy.py::test_the_main_checkout_and_the_project_root_are_checked_by_their_own_tree` |
| --full-name dropped | RED | `tests/test_shell_policy.py::test_from_a_subdirectory_the_listing_is_joined_to_the_top_never_above_it` |
| failed ls-files read as clean | RED | `tests/test_shell_policy.py::test_a_failing_git_listing_refuses - Asser...` |
| GIT_* not scrubbed | RED | `tests/test_shell_policy.py::test_a_pathspec_variable_cannot_hide_a_tracked_host_config_file` |
| location refusal dropped | RED | `tests/test_shell_policy.py::test_a_git_location_variable_refuses_by_name_only[GIT_DIR]` |
| identity through remote get-url | RED | `tests/test_shell_policy.py::test_a_local_insteadof_cannot_borrow_another_repositorys_identity` |
| identity through --get (last wins) | RED | `tests/test_shell_policy.py::test_two_origin_urls_give_no_identity - As...` |
| location set differs from bounded_run | RED | `tests/test_shell_policy.py::test_the_location_variables_match_bounded_run` |
| retired-prefix fallback read | RED | `tests/test_shell_policy.py::test_the_retired_prefix_spelling_never_grants` |
| unset refusal gains a clause | RED | `tests/test_shell_policy.py::test_the_unset_refusal_is_unchanged_and_others_name_the_reason_never_a_value` |
| refusal prints the value to set | RED | `tests/test_shell_policy.py::test_the_unset_refusal_is_unchanged_and_others_name_the_reason_never_a_value` |

## Verify path (`loop.py`)

| Break | Result | First failing test |
| --- | --- | --- |
| verify gate back to git-local only | RED | `tests/test_shell_policy.py::test_verify_runs_the_approved_command_and_refuses_a_changed_one` |
| verify gate wired with config (frontmatter case alone) | RED | `tests/test_shell_policy.py::test_verify_refuses_a_local_goal_whose_frontmatter_replaces_the_command` |
| evidence trust omitted | RED | `tests/test_shell_policy.py::test_verify_runs_the_approved_command_and_refuses_a_changed_one` |
| red gate back to git-local only | RED | `tests/test_test_first_gate.py::test_red_accepts_the_environment_approval` |
| red gate wired with config verify.command | RED | `tests/test_test_first_gate.py::test_red_refuses_a_mismatched_or_widened_environment_approval` |
| red maps a read error to [] | RED | `tests/test_test_first_gate.py::test_red_reports_error_for_an_invalid_acceptance_record` |
| verify gate wired with config (acceptance case alone) | RED | `tests/test_shell_policy.py::test_verify_refuses_an_acceptance_record_that_adds_its_own_command` |

## Doctor (`doctor.py`)

| Break | Result | First failing test |
| --- | --- | --- |
| doctor hint includes the command | RED | `tests/test_doctor.py::test_trust_hint_prints_the_value_never_the_command` |
| doctor row ignores shell_policy (git-local only) | RED | `tests/test_doctor.py::test_trust_row_reports_the_environment_source - ...` |
| doctor loads shell_policy without a try | RED | `tests/test_doctor.py::test_trust_row_without_shell_policy_is_git_local_only` |
| doctor hint drops 'never an agent' | RED | `tests/test_doctor.py::test_trust_hint_is_operator_only_and_says_inspect` |
| verify-approval prints the remote URL | RED | `tests/test_doctor.py::test_verify_approval_prints_the_value_never_the_command` |
| doctor echoes the malformed value (at call time) | RED | `tests/test_doctor.py::test_trust_hint_prints_the_value_never_the_command` |

## Init and the hermetic conftest fixture

| Break | Result | First failing test |
| --- | --- | --- |
| init line back to git-local only | RED | `tests/test_init_flow.py::test_environment_approval_trusts_a_fresh_clone_without_git_local` |
| conftest fixture removed, shell exports a value | RED | `tests/test_shell_policy.py::test_verify_refusal_with_the_variable_unset_is_the_shared_text` |
| conftest fixture kept, shell exports a value (must be GREEN) | GREEN (control did NOT go red) | `—` |

## Docs and ratchets

| Break | Result | First failing test |
| --- | --- | --- |
| variable named in a skill .md | RED | `tests/test_verify_env_trust_prose.py::test_no_agent_read_prose_names_the_environment_approval` |
| variable named in AGENTS.md | RED | `tests/test_verify_env_trust_prose.py::test_no_agent_read_prose_names_the_environment_approval` |
| -I dropped from the documented gesture | RED | `tests/test_verify_env_trust_prose.py::test_the_documented_gesture_prints_the_value_and_imports_nothing_from_the_repository` |
| documented gesture points at another path | RED | `tests/test_verify_env_trust_prose.py::test_the_documented_gesture_prints_the_value_and_imports_nothing_from_the_repository` |
| a fourth caller of verify_decision | RED | `tests/test_verify_env_trust_prose.py::test_only_the_verify_path_asks_the_environment_approval` |

## Diff security review rounds (S1, S2, S3, S5, S7, R1)

| Break | Result | First failing test |
| --- | --- | --- |
| S1 icase dropped from the pathspecs | RED | `tests/test_shell_policy.py::test_a_case_variant_of_a_host_config_path_refuses[.Claude/settings.json]` |
| S1 _matches case-sensitive | RED | `tests/test_shell_policy.py::test_a_case_variant_of_a_host_config_path_refuses[.Claude/settings.json]` |
| S2 verify gate without the project root | RED | `tests/test_shell_policy.py::test_verify_checks_the_project_root_worktree` |
| S2 red gate without the project root | RED | `tests/test_shell_policy.py::test_red_checks_the_project_root_worktree` |
| S7 two-line rev-parse check dropped | RED | `tests/test_shell_policy.py::test_a_checkout_path_with_a_newline_gives_no_identity` |
| S7 a root's failed rev-parse not refused | RED | `tests/test_shell_policy.py::test_a_root_that_is_not_a_git_checkout_refuses` |
| S3 doctor row hint without its try | RED | `tests/test_doctor.py::test_a_bad_verify_command_never_crashes_the_trust_row_or_the_gesture[list]` |
| S3 verify-approval value without its try | RED | `tests/test_doctor.py::test_a_bad_verify_command_never_crashes_the_trust_row_or_the_gesture[list]` |
| S5 a mise path dropped from the list | RED | `tests/test_shell_policy.py::test_a_tracked_host_config_file_naming_the_variable_refuses[.rtx.toml]` |
| R1 prefix from a case-folded relpath (the R1 bug) | RED | `tests/test_shell_policy.py::test_a_project_root_under_a_non_ascii_folder_is_checked_relative_to_itself[Stra\xdfe]` |
| R1 relative-to-root match dropped | RED | `tests/test_shell_policy.py::test_a_case_variant_project_root_is_never_skipped` |

## Red before green

- Step 1, policy core: 76 failed, 5 passed before any implementation (the 5 are the #422 tests).
- Step 2, verify path: 2 of 5 `verify_goal` tests and 3 of 3 `red_goal` tests failed before the gates changed;
  the other 3 are wiring guards whose red is the controls above.
- Step 3, doctor: 9 of 9 new tests failed before the row changed.
- Step 4, init: the end-to-end test failed (`[trust] verify` under a matching approval).
- Step 5, docs: the gesture test and the cloud-setup section test failed before the section existed.
- Diff-review rounds: the S1, S3, S5 and R1 tests failed before their fixes; the S2 and S7 tests guard clauses
  that already refused, and their red is the controls above.

# Sealed acceptance tests: controls, suite growth, retire lever

Slice 5 of #558 (goal #855). Companion to `docs/enforcement.md` (the two sealed rows) and `docs/configuration.md`
(`gates.sealed_acceptance.enabled`). This page records what was actually run, and what was not.

## UNMEASURED (stated first, on purpose)

- **PC-10 (extra agent runs and cost per goal) is UNMEASURED.** It needs a real breaker run. `work.py seal-tests`
  refuses on this host (Claude Code has no `claude` breaker route, #537 is open, B-1), and a fake CLI does not measure cost.
- **The end-to-end seal (breaker, then seal, then commit and PR gates, on a real host) is UNMEASURED** for the same reason.
  Every control below runs against a hand-built seal record in a real git repository and worktree, not against a record a
  breaker produced.
- **Real per-goal suite growth and verify-time are UNMEASURED: zero sealed goals exist** (`git ls-files tests/acceptance | wc -l` is 0).

## Controls: each clause broken alone, the named test seen red

Method: for each row, ONE edit to the product file (the break), the named tests run, the file restored byte for byte.
A row is claimed only with a test id actually seen failing under that break. The commit rows use the gesture the docs give,
`python3 skills/sigma-loop/scripts/work.py commit .sdlc <goal> --message "..."` from the repository root with the
relative `.sdlc` (`pr .sdlc <goal>` for PR rows, `seal-retire .sdlc <goal> --reason "..."` for retire rows). The
"how" column says whether the red test drives that gesture as a subprocess (**gesture**) or calls the same gate function
in-process (**in-process**, a weaker form: it proves the clause, not the exit mapping).

| # | Clause (single break applied) | Test seen red | How |
| --- | --- | --- | --- |
| 1 | commit activation, sealed side: `is_sealed` guard removed | `TestWrappers::test_no_seal_record_is_a_passthrough_with_zero_git_calls`, `TestDocumentedGesture::test_commit_for_a_goal_with_no_seal_record_is_untouched` | both |
| 2 | activation, other side: `is_sealed` always False | `TestDocumentedGesture::test_commit_refuses_a_drifted_sealed_file_exit_4_and_resets_the_index` | gesture |
| 3 | commit check moved BEFORE `git add -A` | `TestDocumentedGesture::test_a_no_op_commit_stays_a_no_op_even_when_head_holds_tampered_sealed_bytes` | gesture |
| 4 | sealed check moved AFTER `_secret_refusal` | `TestControls::test_sealed_is_checked_before_secrets_and_its_paths_are_unstaged` (new) | gesture |
| 5 | `check`: `missing` branch dropped | `TestWrappers::test_rename_out_of_a_sealed_file_is_caught_as_missing` | in-process |
| 6 | `check`: sha256 compare dropped | `TestWrappers::test_drift_is_refused_naming_path_and_kind_and_the_index_is_reset` | in-process |
| 7 | `check`: exec-bit clause dropped alone | `TestControls::test_an_executable_bit_alone_is_refused_on_the_gesture` (new) | gesture |
| 8 | `check`: symlinked-directory clause dropped | `TestWrappers::test_a_symlinked_sealed_directory_refuses` | in-process |
| 9 | `check`: `unexpected` loop dropped | `TestWrappers::test_a_new_file_in_the_sealed_dir_is_unexpected` | in-process |
| 10 | `check`: debris tolerance dropped alone | `TestControls::test_verify_debris_in_the_sealed_dir_does_not_refuse` (new) | in-process |
| 11 | `is_debris` accepts anything under `__pycache__` | `TestControls::test_a_non_pyc_file_in_pycache_is_unexpected` (new); `TestPlantedBytecode` stayed green under this break | in-process |
| 12a | record: symlink refusal dropped | `TestWrappers::test_a_symlinked_record_refuses` | in-process |
| 12b | record: schema clause dropped alone | `TestControls::test_a_foreign_schema_with_well_formed_files_is_still_a_record_problem` (new; the old foreign-record test stayed green because its `files` shape also failed) | in-process |
| 12c | record: unparseable-JSON clause dropped | `TestCheck::test_unparseable_or_foreign_record_is_not_sealed[{nope]` in `tests/test_sealed_tests.py`. The commit gate's own tests stay green here because the gate is total (a raising `check` is still a refusal); the library test is the one that sees it | library |
| 13 | unstage becomes a bare `git reset -q` | `TestWrappers::test_the_unstage_is_the_targeted_literal_reset` | in-process |
| 14 | failed-unstage text always the clean ending | `TestWrappers::test_a_failed_unstage_says_still_staged` | in-process |
| 15 | totality, commit gate: `except Exception` narrowed | `TestWrappers::test_a_raising_check_is_a_refusal_not_an_exception_and_still_unstages` | in-process |
| 16 | totality, PR gate: same | `TestWrappers::test_pr_a_raising_check_tree_is_a_refusal_not_an_exception` | in-process |
| 17 | exit mapping: `"SEALED REFUSED:"` removed from the exit-4 tuple | five `TestDocumentedGesture` tests (commit drift, PR raw commit, `--no-tests`, opt-in, red test) | gesture |
| 18 | early return after the sealed refusal dropped (commit lands) | `TestDocumentedGesture::test_commit_refuses_a_drifted_sealed_file_exit_4_and_resets_the_index` | gesture |
| 19a | PR order: working tree compared before HEAD tree | `TestControls::test_pr_names_the_committed_tree_first_even_when_every_later_check_would_also_fail` (new) | in-process |
| 19b | PR order: opt-in checked before the trees | that test and `test_pr_names_the_working_tree_before_the_opt_in_and_the_opt_in_before_the_green_run` (new) | in-process |
| 19c | PR order: green run before the trees | the same two tests | in-process |
| 20 | PR: HEAD compare reads the disk (`check`) | `TestWrappers::test_pr_reads_head_not_the_working_tree` | in-process |
| 21 | PR: working-tree compare dropped | `TestWrappers::test_pr_working_tree_guard_refuses_an_ignored_conftest` | in-process |
| 22 | PR: shell-policy opt-in dropped | `TestWrappers::test_pr_requires_the_shell_policy_opt_in_and_names_it_not_parking` | in-process |
| 23 | `--no-tests` skips the sealed PR gate | `TestDocumentedGesture::test_pr_with_no_tests_set_still_refuses_a_sealed_goal` | gesture |
| 24 | `run_green` ignores skipped, xfail, xpass and error nodes | six `TestRunGreen` tests (`test_anything_but_passed_is_not_green[...]`, `test_an_exempt_on_base_node_that_is_skipped_is_not_green`, `test_odd_exit_codes_and_no_summary_are_not_green[kw3]`) | library |
| 25 | retire: reason check dropped | `TestRetire::test_a_blank_or_missing_reason_is_refused_and_nothing_changes`, `TestRetireGesture::test_a_blank_reason_is_refused_exit_4` | both |
| 26 | retire: live-record check dropped | `TestRetire::test_an_unsealed_goal_is_refused`, `TestRetireGesture::test_an_unsealed_goal_is_refused_exit_4` | both |
| 27 | retire: record unlinked BEFORE the tombstone | `TestRetire::test_a_crash_at_the_tombstone_write_leaves_the_record_untouched`, `...test_a_crash_at_the_unlink_leaves_the_goal_sealed_and_a_rerun_succeeds` | library |
| 28a | retire: tombstone named `<stem>.json` | four `TestRetire` tests (tombstone body, already-retired, crash re-run, symlinked record) | library |
| 28b | prune: `("sealed", ".retired.json")` family removed | `test_goal_state_prune.py::test_retired_tombstone_is_pruned_with_its_goal_and_the_extra_stem_is_kept_cleanly` | in-process |
| 29 | retire deletes the sealed test files | **NOT CLAIMED.** `retire` takes no repository root, so it cannot reach the test files and the break cannot be applied honestly. `TestRetire::test_the_sealed_test_files_are_never_touched` is a regression pin that has not been seen red. | none |

Notes on what the table shows and does not:

- **Row 3 is narrower than its name.** The only test that went red is the no-op-commit one: checking before `git add -A`
  makes an otherwise empty commit report a seal problem. Drift is refused either way, so "the check must run after the
  add so it can undo staging" is not separately distinguishable by any test, and no test claims it.
- **Row 19 weakens "A before B" to membership three ways** (19a, 19b, 19c). The new tests set up one case in which every
  later check would also fail, so only the true order produces the right message.
- **In-process rows prove the clause, not the CLI.** Only the rows marked gesture or both were seen red through
  `work.py` as a subprocess with relative `.sdlc`. A gesture test also passes under `GIT_CONFIG_GLOBAL=/dev/null` with an
  empty `HOME`: the fixture sets a repository-local identity, because the gesture helper strips `GIT_*`.
- **No break exposed a defect in the existing gates.** Breaks 4, 7, 10, 11, 12b and 19 had no test that went red until a new
  one was written; those tests are marked new. The one product defect found in this goal came from the new tombstone, not a
  break: see the prune note under the retire lever.

## Retire lever

The seal record is pruned with its goal (`goal_state_prune`), and the sealed files stay in the tree and are collected by
`pytest tests` forever. The commit gate also refuses any edit to them while the record exists, so there was no sanctioned way
to fold them into the owner test file.

`python3 skills/sigma-loop/scripts/work.py seal-retire .sdlc <goal> --reason "<why>"` is that way.

1. In the goal worktree, merge the sealed tests into the owner test file and run it green. The verb never moves or deletes test files.
2. Run `seal-retire`. It writes `.sdlc/state/sealed/<stem>.retired.json` (goal, reason, retired_at, generation, last file
   sha256s) first and deletes the live record last, so a crash leaves the goal sealed and the verb re-runnable.
3. Run the ordinary `work.py commit`. It now carries the move and the deletion of `tests/acceptance/<stem>/`. Before step 2
   the same commit exits 4 with `SEALED REFUSED:`.

Exit codes: 0 retired (prints the tombstone), 2 usage (no `--reason`), 4 refused with `seal-retire: REFUSED [<clause>]`
(clauses `no-reason`, `not-sealed`, `already-retired`, `record`). It appends one action-log row naming the reason. It works
with `gates.sealed_acceptance.enabled` off (it is cleanup, not a gate). Do not paste secrets into `--reason`: it is stored
in clear text.

Honest limits:

- **This is the D-11 disarm, recorded.** Anyone can still delete the record with `rm`. The verb is available to the sealed
  agent too, so the tombstone and the action-log row are evidence, not prevention.
- A corrupt but regular record can be retired (its tombstone has empty `files`); a symlinked record cannot.
- `goal_state_prune` previously took the stem `<stem>.retired` from the new file name and never the goal: without the
  explicit `.retired.json` family the tombstone would never have been pruned (seen red, then fixed). The stray
  `<stem>.retired` stem is kept quietly as `not-terminal`.

## Suite growth (D-7)

Recipe, no reporter code:

```
git ls-files tests/acceptance | wc -l
git ls-files 'tests/acceptance/*/*' | cut -d/ -f3 | sort -u | wc -l
python3 -m pytest --collect-only -q tests/acceptance
```

Measured now: 0 files and 0 sealed goals; 661 tracked `tests/test_*.py` files for context. A synthetic floor of 20 files
with 2 trivial tests each collected and ran in 0.02 s of pytest time, about 0.12 s wall including interpreter start. That is
collection overhead only and is not a per-goal figure.

Ceiling, derived from the breaker contract and not measured: at most 14 files per goal (`breaker._MAX_BLOCKS`, 32 KiB each),
so N sealed goals add at most 14 N files. At 10 times today's count of zero that is still zero; at 100 sealed goals it is at
most 1,400 files. The pruning lever is retire plus fold above. Real per-goal file count and verify-time added are UNMEASURED.

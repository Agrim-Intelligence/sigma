# #874 controls: `record.py build <run_dir>` guards, each broken once and seen red

Gesture under test, as documented in the module docstring and plan: `python3 evals/regression/record.py
build <run_dir>` run as a subprocess. Every named test below invokes exactly that argv shape (with
`--goal 874`, or none when a single goal log exists) and asserts on the written `record.json` and the exit
code; only `test_internal_kinds_imported_not_copied` inspects the module object (the identity seam, no
source grep). Method: copy of the working `record.py` kept aside, one edit applied, the named tests run,
the file restored byte-for-byte from the copy (`cmp` identical after the run, and `git status` shows no
tracked-file change from the controls). Python 3.9.6.

Baseline: `tests/test_regression_record.py` 27 passed.

| Control | Break applied to `record.py` | Test(s) run | Result (red, reason) |
|---|---|---|---|
| C1 | `if r.get("kind") in INTERNAL` replaced by `if True` | `test_agent_kind_rows_never_reach_record` | FAIL: `SENTINEL-DISPATCH` found in the record |
| C2 | imported tuple replaced by an equal copy `tuple(list(...))` | `test_internal_kinds_imported_not_copied` | FAIL: `record.INTERNAL is actionlog.INTERNAL_KINDS` identity false (equal values, different object) |
| C3 | verify read made unguarded (`open()` of an absent file) | `test_absent_streams_build_with_present_false` | FAIL: exit 1 `FileNotFoundError`, expected exit 0 |
| C4 | `evidence` key dropped from every phase | `test_every_phase_is_call_existence_with_caveat` | FAIL: `KeyError: 'evidence'` |
| C5 | malformed journal line raises instead of being skipped | `test_journal_union_of_files_skips_malformed_and_prune_stamp` | FAIL: exit 1 `JSONDecodeError` |
| C6 | build timestamp field added | `test_two_builds_are_byte_identical` | FAIL: two builds differ at the timestamp |
| C7 | `_int` returns the raw value (no coercion) | `test_tokens_coerce_stringly_numerics_and_tolerate_garbage`, `test_journal_events_written_by_real_ledger_append` | both FAIL: exit 1 `TypeError` summing str. First run of this control left the real-ledger test GREEN because it wrote ints; the test was changed to pass `str(...)` values like `phase_report.py`, and C7 re-run gave `2 failed` |
| C9 | sibling-suffix rejection bypassed (`kept = found`) | `test_sibling_artifact_newest_does_not_displace_plan` | FAIL: newest `874-controls.md` answers for the plan |
| C10 | absolute-path scrub made a no-op | `test_record_carries_no_absolute_path` | FAIL: `/abs/elsewhere/wt-874` kept |
| C11 | tokens also summed from `spend` events | `test_attempt_with_end_and_spend_counts_once` | FAIL: `877 != 100` (attempt counted twice) |
| C12 | `GIT_*` variables inherited instead of cleared | `test_git_env_clears_inherited_git_variables` | FAIL: commit order absent (hidden objects/namespace) |
| C8 | write-surface: `write_surface.py check . docs/launch/write-surface.json` before the inventory edit, record.py made visible with `git add -N` | check | exit 1: `new write site evals/regression/record.py:write_atomic fs-remove` and `... fs-write`. After adding the entries (count 2 each, per the scanner's own `scan` output): a first attempt with count 1 gave `count differs`, final run exit 0 |

Notes
- C2 and the identity seam: the test compares against `record._load_scripts("actionlog")`, the module
  `record.py` itself loaded and cached, not a second load.
- C12 caveat found while designing the test: `GIT_DIR` alone does not make a control go red, because the
  builder passes `--git-dir` explicitly; the test therefore also sets `GIT_OBJECT_DIRECTORY` and
  `GIT_NAMESPACE`, which do.
- Sensitivity: no probabilistic or concurrency control in this slice, so none is demoted.
- Not run here: the full suite (owner of the orchestrator runs it), and a live control run of the
  journal/gate formats (slice 4); real formats were read from writer code plus one real `ledger.append` test.

## Code-review fix: `phase_doc` now resolves via `review_context.phase_doc_file`

Blocking finding: `record.phase_doc` had reimplemented the resolver and disagreed with it (pooled exact
and slugged files by mtime; slug-globbed non-numeric stems; leaked a non-glob pattern string into `source`).
Fix: call `review_context.phase_doc_file` (loaded by path, cached by `_load_scripts`); keep only the
`SIBLING_SUFFIXES` rejection for a slugged hit of a numeric stem; `source` is `<sub>/<name>` or
`<sub>/<stem>.md` when absent.

Method: the pre-fix `phase_doc` body restored in place of the new one, the file run, then restored from a
byte copy (`cmp` identical). Gesture: `python3 -m pytest tests/test_regression_record.py`, which drives the
documented `record.py build <run_dir>` subprocess.

| Control | Break | Test(s) | Result |
|---|---|---|---|
| C13 | old pooled-mtime `phase_doc` | `test_exact_stem_older_than_slugged_sibling_exact_wins` | FAIL: newer `874-newer-slug.md` answered instead of exact `874.md` |
| C14 | old unconditional `<stem>-*.md` glob | `test_non_numeric_stem_does_not_match_other_goals_prefixed_file` | FAIL: `fix-a-b.md` matched for stem `fix-a` |
| C15 | old pattern-string `source` | `test_absent_plan_source_is_clean_string` | FAIL: source was the `plans/874[-*].md` pattern |

Result: old behaviour `3 failed, 27 passed`; restored `30 passed`.

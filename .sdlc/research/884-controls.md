# #884 controls: live entrypoint, slice 1

Every guard below was deliberately broken once, seen to fail on the documented gesture
(`python3 evals/regression/live.py`, run from an unrelated working directory on a repo-shaped temp copy
with an allowlisted environment) and in the new tests, then restored and confirmed byte-identical to the
saved copy before the tests were rerun green. Interpreter: Python 3.9 (local). Credentials are fakes built
at run time; no model was started and no real `claude` was run. Where a gesture line shows a planted fake
value, its hex tail is replaced here by a length note. Paths and timings are removed from the captured
output. Nothing here is a probabilistic race, so every red is deterministic.

## Task 0: row contract check (#819, #820, read-only, read once)

- #820 (release gate) needs, per result: the tested `commit` (`--sha` equality), the `cell`, the
  `credential_basis` (a `subscription` basis fails), a not-representative marker, and the status
  (RED / NOT EVALUABLE / FLAKY / NOT RUN) with property names. The row carries `commit`, `cell`,
  `credential_basis`, `representative` (the gate maps `false` to its not-representative wording) and
  `status`. MATCH. Property names are not in a NOT RUN row (nothing ran); they belong to the later run rows.
- #819 (schedule and spend) says the rows are "defined in R-05", uploads them as artifacts, and sums the
  month's spend from them; it names no field. The row carries `cost` (null here) and `cost_basis`; the null-cost
  rule (skip null-cost rows, never sum them as zero) is recorded in the module docstring and the CHANGELOG.
  NO MISMATCH FOUND; the field list was not changed.
- Later-slice note recorded: `commit` comes from `GITHUB_SHA`, which on a dispatch is the dispatch ref's
  commit; the slice that adds a real run path must record the tested commit explicitly.
- Result: the schema stays `sigma.regression-result/v1`. It is stated in the module docstring as a contract
  where a later rename bumps the version and never edits v1 in place.

## Argparse pin (plan-review refinement), run on the local Python 3.9

`--cap-usd -1` (two tokens) is read as the VALUE `-1` and reaches the grammar: code `no-cap`.
`--cap-usd -x` and `--cap-usd -1e3` are usage errors: code `bad-usage`. `--cap-usd=-1` is `no-cap`.
All are pinned in `test_cap_belt_and_opt_in_parsing` and pass.

## Expected red, not a defect: the committed write-surface inventory

`tests/test_write_surface.py::test_committed_inventory_matches_the_tracked_write_surface` is RED at the end of
this phase with `stale entry evals/regression/live.py:write_row fs-remove` (and `fs-write`): the gate scans only
`git ls-files`, and `live.py` is untracked until the orchestrator's local commit. The other 26 tests in that file
pass. `tests/test_regression_live.py::test_write_surface_inventory_matches_live_py` uses a direct scan and is green.
It and `python3 tools/readiness/write_surface.py check . docs/launch/write-surface.json` are to be verified green
only after the commit.

## Controls

### Control 1: no-credential rung removed (rung 1 skipped)

**Gesture, guard intact (`python3 evals/regression/live.py`, bare)**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-opt-in]: SIGMA_REGRESSION_LIVE is not set to 1, true, yes or on
row code=no-opt-in rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'bare_gesture_refuses'**

```
FAILED tests/test_regression_live.py::test_bare_gesture_refuses_no_credential
1 failed, 72 deselected in 0.22s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'bare_gesture_refuses'**

```
1 passed, 72 deselected in 0.22s
```

### Control 2: exact-value redaction removed

**Gesture, guard intact (bare gesture plus `--cap-usd=<planted unknown-shape key>`)**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-cap]: a positive finite dollar cap is required (--cap-usd or SIGMA_REGRESSION_CAP_USD) and a belt (--belt-usd or SIGMA_REGRESSION_BELT_USD) must be positive, finite and not above it (got '[REDACTED]')
row code=no-cap rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-cap]: a positive finite dollar cap is required (--cap-usd or SIGMA_REGRESSION_CAP_USD) and a belt (--belt-usd or SIGMA_REGRESSION_BELT_USD) must be positive, finite and not above it (got 'zq9-<fake hex, 39 chars>)
row code=no-cap rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'planted_unknown or precondition'**

```
FAILED tests/test_regression_live.py::test_planted_unknown_shape_credential_is_redacted_everywhere
1 failed, 1 passed, 71 deselected in 0.22s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'planted_unknown or precondition'**

```
2 passed, 71 deselected in 0.22s
```

### Control 3: pull-request-event rung removed

**Gesture, guard intact (bare gesture, valid env, event pull_request)**

```
exit=2
stdout=''
stderr=live.py: REFUSED [pull-request-event]: refusing to run for a pull request event, or for an unnamed event in Actions
row code=pull-request-event rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [not-implemented]: no run path exists in this slice
row code=not-implemented rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'event_rung or ladder_first'**

```
FAILED tests/test_regression_live.py::test_ladder_first_failing_code[pr-event]
FAILED tests/test_regression_live.py::test_ladder_first_failing_code[late-three]
FAILED tests/test_regression_live.py::test_event_rung - AssertionError: pull_...
3 failed, 6 passed, 64 deselected in 0.11s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'event_rung or ladder_first'**

```
9 passed, 64 deselected in 0.12s
```

### Control 4: child_env copies the parent environment

**Gesture, guard intact (bare gesture, allowlisted child env (guard intact) vs a child that inherits a hostile parent (guard broken))**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-cap]: a positive finite dollar cap is required (--cap-usd or SIGMA_REGRESSION_CAP_USD) and a belt (--belt-usd or SIGMA_REGRESSION_BELT_USD) must be positive, finite and not above it
row code=no-cap rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'hostile'**

```
FAILED tests/test_regression_live.py::test_bare_gesture_survives_hostile_parent_environment
1 failed, 72 deselected in 0.08s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'hostile'**

```
1 passed, 72 deselected in 0.22s
```

### Control 5: subscription-in-gated rung removed

**Gesture, guard intact (bare gesture, OAuth token only, CI=true)**

```
exit=2
stdout=''
stderr=live.py: REFUSED [subscription-in-gated-mode]: a gated run may not hold CLAUDE_CODE_OAUTH_TOKEN; use ANTHROPIC_API_KEY alone
row code=subscription-in-gated-mode rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [not-implemented]: no run path exists in this slice
row code=not-implemented rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'subscription_in_gated or sub-gated'**

```
FAILED tests/test_regression_live.py::test_ladder_first_failing_code[sub-gated]
FAILED tests/test_regression_live.py::test_subscription_in_gated_mode - Asser...
2 failed, 1 passed, 70 deselected in 0.23s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'subscription_in_gated or sub-gated'**

```
3 passed, 70 deselected in 0.23s
```

### Control 6a: `import subprocess` added

**Gesture, guard intact (bare gesture)**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'imports'**

```
FAILED tests/test_regression_live.py::test_live_py_imports_only_allowlisted_modules
FAILED tests/test_regression_live.py::test_live_py_imports_no_process_spawning_or_bench_arms
2 failed, 71 deselected in 0.09s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'imports'**

```
2 passed, 71 deselected in 0.09s
```

### Control 6b: append-mode `open` added in write_row

**Gesture, guard intact (bare gesture)**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'append_mode'**

```
FAILED tests/test_regression_live.py::test_live_py_has_no_append_mode_open_or_os_write
1 failed, 72 deselected in 0.09s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'append_mode'**

```
1 passed, 72 deselected in 0.08s
```

### Control 6c: an `unlink` moved out of write_row

**Gesture, guard intact (bare gesture)**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'write_methods_only or write_surface_inventory'**

```
FAILED tests/test_regression_live.py::test_write_methods_only_in_write_row - ...
FAILED tests/test_regression_live.py::test_write_surface_inventory_matches_live_py
2 failed, 71 deselected in 0.09s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'write_methods_only or write_surface_inventory'**

```
2 passed, 71 deselected in 0.10s
```

### Control 7: a `claude` launch added to the terminal path

**Gesture, guard intact (bare gesture, valid env, fake `claude` first on PATH)**

```
exit=2
stdout=''
stderr=live.py: REFUSED [not-implemented]: no run path exists in this slice
row code=not-implemented; fake claude marker file exists: False rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [not-implemented]: no run path exists in this slice
row code=not-implemented; fake claude marker file exists: True rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'fake_claude_on_path'**

```
FAILED tests/test_regression_live.py::test_fake_claude_on_path_is_never_run[terminal]
1 failed, 5 passed, 67 deselected in 1.43s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'fake_claude_on_path'**

```
6 passed, 67 deselected in 0.91s
```

### Control 8: walker made blind to os.write and to .write

**Gesture, guard intact (bare gesture (unrelated; the walker is test code))**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'scanner_walker_flags'**

```
FAILED tests/test_regression_live.py::test_scanner_walker_flags_append_open_and_os_write[os.write(fd, b"")]
FAILED tests/test_regression_live.py::test_scanner_walker_flags_append_open_and_os_write[sys.stderr.write("")]
2 failed, 12 passed, 59 deselected in 0.11s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'scanner_walker_flags'**

```
14 passed, 59 deselected in 0.09s
```

### Control 9: whole line routed through the shared scrubber

**Gesture, guard intact (bare gesture)**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential: [REDACTED] CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'refusal_lines'**

```
FAILED tests/test_regression_live.py::test_refusal_lines_are_emitted_intact[no-credential]
1 failed, 8 passed, 64 deselected in 0.10s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'refusal_lines'**

```
9 passed, 64 deselected in 0.10s
```

### Control 10: echo cuts before redacting

**Gesture, guard intact (bare gesture plus `--cap-usd=<planted>`)**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-cap]: a positive finite dollar cap is required (--cap-usd or SIGMA_REGRESSION_CAP_USD) and a belt (--belt-usd or SIGMA_REGRESSION_BELT_USD) must be positive, finite and not above it (got '[REDACTED]')
row code=no-cap rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-cap]: a positive finite dollar cap is required (--cap-usd or SIGMA_REGRESSION_CAP_USD) and a belt (--belt-usd or SIGMA_REGRESSION_BELT_USD) must be positive, finite and not above it (got 'zq9-<fake hex, 39 chars>)
row code=no-cap rows_in_tree=1 rows_in_cwd=0
```

**Tests, guard broken: pytest -k 'cap_echo_redacts or planted_unknown'**

```
FAILED tests/test_regression_live.py::test_planted_unknown_shape_credential_is_redacted_everywhere
FAILED tests/test_regression_live.py::test_cap_echo_redacts_before_cutting - ...
2 failed, 71 deselected in 0.37s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'cap_echo_redacts or planted_unknown'**

```
2 passed, 71 deselected in 0.39s
```

### Control 11: default results dir made cwd-relative

**Gesture, guard intact (bare gesture (rows_in_tree vs rows_in_cwd))**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=no-credential rows_in_tree=1 rows_in_cwd=0
```

**Gesture, guard broken**

```
exit=2
stdout=''
stderr=live.py: REFUSED [no-credential]: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set
row code=None rows_in_tree=0 rows_in_cwd=1
```

**Tests, guard broken: pytest -k 'bare_gesture_refuses'**

```
FAILED tests/test_regression_live.py::test_bare_gesture_refuses_no_credential
1 failed, 72 deselected in 0.22s
```

**Restored byte-identical to the saved copy**

```
diff -q: identical
```

**Tests, restored: pytest -k 'bare_gesture_refuses'**

```
1 passed, 72 deselected in 0.23s
```

## Review fix round (code-review SEND-BACK): added guards, each seen red then restored byte-identical

Each break was a temporary edit to the file named, restored from a saved copy (`cmp` identical), then the
file rerun green (78 passed). Credentials are run-time fakes of an unknown shape; no model started.

- F1 stripped-credential branch: `values += [raw, raw.strip()]` -> `[raw]` in `evals/regression/live.py`
  `held_credentials`. RED: `test_stripped_form_of_a_held_credential_is_redacted[trailing-newline]` and
  `[leading-spaces]` (2 failed, 76 passed).
- F2a final printed-line pass: `print(redact(line, patterns), ...)` -> `print(line, ...)`. RED:
  `test_final_exact_value_passes_cover_each_line_and_the_row` (1 failed). Seam: the fixed
  `DETAILS["not-implemented"]` text is made to carry the held value, so the echo path cannot remove it.
- F2b final serialised-row pass: `redact(json.dumps(row ...))` -> bare `json.dumps(row ...)`. RED: the same
  test (1 failed), on its row assertion.
- F3 surrogate escape: `v.encode("utf-8", "surrogateescape")` -> `v.encode("utf-8")`. RED:
  `test_surrogate_escaped_credential_is_still_redacted_and_the_refusal_holds` (UnicodeEncodeError, 1 failed).
  Also re-ran the url-quote form control: dropping `quote_plus(raw)` turns `test_redaction_forms_and_minimum_length` red.
- F4 static spawn guard (`_spawn_calls` in `tests/test_regression_live.py`): removing the `import_module`
  check, the `from os import` check, or the `getattr(os, ...)` check each turns
  `test_live_py_calls_no_process_spawning_apis` red (3 separate breaks, 1 failed each). Documented limits:
  aliases, `vars(os)[...]`, `sys.modules[...]` are not caught statically; the dynamic fake-`claude` test is
  the backstop.
- F5 blank belt: `if belt_text is not None and not str(belt_text).strip():` -> `if False:` turns
  `test_blank_belt_env_is_unset_but_a_blank_belt_flag_refuses` red. Decision: blank env belt = unset; blank
  `--belt-usd=` flag = `no-cap`; blank cap = `no-cap`.

## Review fix round 2 (test gaps): guards that stayed green when deleted

Each break was a temporary edit to `evals/regression/live.py`, restored from a saved copy (`cmp` identical),
then the suite rerun green (119 passed, Python 3.9). Credentials are run-time fakes; no model started.

- G1 OAuth value redaction: `held_credentials` loop narrowed to `("ANTHROPIC_API_KEY",)`. RED:
  `test_oauth_token_value_is_redacted_everywhere` (1 failed).
- G2 non-blank check: `_nonblank` -> `env.get(name) is not None`. RED:
  `test_blank_or_whitespace_credential_is_no_credential` (13 failed, both variables) and the both-blank test.
- G3 scrubber pass in `echo`: `return scrubber.scrub(shown)` -> `return shown`. RED:
  `test_scrubber_pass_runs_on_a_rejected_cap_text` (1 failed).
- G4 40-char cut: `[:ECHO_CUT]` removed. RED: `test_rejected_cap_echo_is_cut_to_a_bound` (1 failed).
- G5 base64 forms, dropped one at a time, each RED in `test_every_base64_form_of_a_value_is_redacted`
  (12 failed each; lengths 10/31/32/40/41/61, none divisible by 3, standard and url-safe differ):
  a) `forms.update({padded.rstrip("=")})` (padded dropped) -> b64-padded, urlsafe-padded red;
  b) `forms.update({padded})` (unpadded dropped) -> b64-unpadded, urlsafe-unpadded red;
  c) url-safe encoder removed -> urlsafe-padded and urlsafe-unpadded red;
  d) standard encoder removed -> b64-padded and b64-unpadded red.
  The assertion is exact equality (`before [REDACTED] after`) so a dropped padded form cannot hide behind
  the unpadded prefix pattern leaving a stray `=`.
- G6 `GITHUB_SHA`: validation replaced by `sha or "unknown"`. RED:
  `test_github_sha_reaches_the_row_only_when_forty_hex` (1 failed).
- CHANGELOG test count corrected from 73 to 119 (`pytest tests/test_regression_live.py`, Python 3.9).

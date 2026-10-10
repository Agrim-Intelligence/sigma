# #885 controls: golden task T3

Every break below was applied to a scratch clone of the goal tree (T3 committed there), the documented gesture
`python3 evals/golden/verify.py` was run with no extra flag, the red output was read, and the break was reverted
(`git checkout` plus `git clean` in the scratch clone; the final `git status` there was empty and verify was `ok T3`).
Interpreter: Python 3.12 venv (the machine default `python3` is 3.9.6, below the repo floor of 3.10).
The goal tree itself was never broken; it carries only the new files and the one README edit, unstaged.

Format: guard | broken by | red output | restored green.

| guard | broken by | red output seen | restored |
| --- | --- | --- | --- |
| hidden test, decoy hash pin | one comment line appended to the decoy in `repo/` and `reference/` together, pin not updated | `RED T3 hidden-on-reference: ... (exit 1): 1 failed, 6 passed` | `ok T3` |
| hidden test, generated hash pin | same, generated file | same red line | `ok T3` |
| hidden test, shim hash pin | same, shim file | same red line | `ok T3` |
| missed deletion | `docs/rates.md` kept in `reference/` | `RED T3 hidden-on-reference: ... 1 failed, 6 passed` (and no `reference-diff`: the path equals the start tree, so the diff is blind to it) | `ok T3` |
| config key rename | `reference/config/shipping.json` back to the old key | `RED T3 hidden-on-reference: ... 3 failed, 4 passed` | `ok T3` |
| call-site rename | `reference/shipping/cart.py` back to the old function name | `RED T3 hidden-on-reference: ... 1 failed, 6 passed` | `ok T3` |
| allowed_paths coverage | extra file `reference/shipping/extra.py` | `RED T3 reference-diff: shipping/extra.py added but not in allowed_paths.json` | `ok T3` |
| exact-only `allowed_paths` (verify.py side) | entries `README.md`, `docs`, `shipping`, `config` (no trailing slash) with the shipped verify.py | `RED T3 reference-diff: config/shipping.json changed but not in allowed_paths.json` plus one line per nested path, exit 1 | `ok T3` |
| exact-only pin, test side | verify.py `_allowed` patched to `rel.startswith(entry)`, same entries | gesture prints `ok T3` (exit 0: the regression is invisible to the gesture); `test_t3_allowed_paths_exact_only` fails with `AssertionError: ok T3` | both tests green |
| sensitivity of the pin | the patch above applied inside `test_t3_exact_only_pin_detects_prefix_regression` | that test passes only because the patched verify.py is green on the Case A plant; against the real verify.py the Case A plant is red | green |
| second layer is the hidden decoy hash | decoy hash line removed from the hidden test (hidden map rehashed) | `RED T3 reference-diff: shipping/_vendor/legacy_rates.py changed ...` only; `test_t3_planted_break_is_red[decoy-edit]` and `[widened-prefix-then-decoy-edit]` go red because `hidden-on-reference` no longer fires | `ok T3`, both cases green |
| AST-not-text check | comment-only `calc_rate` mention in `reference/shipping/cart.py` | still `ok T3` (a comment is not a use); a real call to the old name is `RED T3 hidden-on-reference: ... 1 failed, 6 passed` | `ok T3` |
| third-party import guard | absolute `from vendor.legacy_rates import ...` in the start shim | `tests/test_third_party_imports.py`: `third-party import vendor is not in the allowlist` | green with the relative import |
| doc-link guard | a markdown link to a page that does not exist added to the task README | `tests/test_doc_links.py`: `missing link` naming that page | green |
| task shape: write call | `open('x','w')` appended to task code | `tests/test_golden_t3.py::test_t3_task_shape` fails | green |
| task shape: third-party import | `import requests` appended to task code | same test fails | green |
| README test | the old "tasks land in later slices" sentence restored, wrapped across a line | `test_t3_readme_sentence_is_current`: `the stale ... sentence is back` | green |
| tests need the task | task directory moved away | `tests/test_golden_t3.py`: 1 failed, 1 passed, 13 errors | green |
| pinned hash drift | one line appended to the hidden test without rehashing | covered by `test_t3_pinned_hash_drift_is_red` (`hidden-sha256: ... hash mismatch`) | green |
| hidden-on-start | `repo/` made equal to `reference/` | covered by `test_t3_start_tree_hidden_fails` (`hidden-on-start`) | green |

The naive overlay (`hidden/naive/`) is red for two reasons (the undeleted docs page and the three pinned hashes);
it isolates nothing alone, by design. Isolation of each property is by the planted cases above.

## Guards over the new files (run in a scratch clone where T3 is committed)

`tests/test_third_party_imports.py`, `tests/test_write_surface.py`, `tests/test_doc_links.py`,
`tests/test_leak_scan.py`, `tests/test_readiness_exposure_scan.py`, `tests/test_documented_gestures.py`,
`tests/test_golden_verify.py`, `tests/test_golden_t3.py`, `tests/test_bench_tasks.py`,
`tests/test_no_private_sdlc_citations.py`: 384 passed in 189 s. `write_surface.py check` exit 0 (zero new inventory
entries); `tools/rename_check.py` exit 0.

## Wall time of `evals/golden/verify.py`

Measured with `/usr/bin/time -p`, macOS, one task (3 pytest runs: start, reference, naive). Python 3.12 venv, five full
runs of `python3 evals/golden/verify.py`: 0.60 / 0.63 / 0.69 s (min / median / max); three `--only T3` runs:
0.64 / 0.66 / 0.82 s. One run each of `--only T3` under the machine's Python 3.9.6: 3.96 s; Python 3.13.9: 2.08 s.
Figures are for ONE task; nothing beyond one task is measured.

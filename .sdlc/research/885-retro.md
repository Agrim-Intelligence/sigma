Grade: ACHIEVED - all five acceptance bullets are met with evidence; `python3 evals/golden/verify.py --only T3` prints `ok T3` (exit 0) in the tree as shipped, and the planted controls were seen red on that same documented gesture.

# #885 retro (P7) - golden task T3

## Intent vs shipped
- Bullet 1 (layout and `ok T3`): ACHIEVED. `evals/golden/T3/` holds task.json (origin planned), repo/, reference/, hidden/ (files, verify.json, naive overlay, hashes), rubric.json and allowed_paths.json. Re-ran the gesture at retro time: `ok T3`, 1 task, 0 red.
- Bullet 2 (multi-file refactor, docs and config rename, hidden fails on repo/ and passes on reference/): ACHIEVED. Rename spans shipping/{__init__,rates,cart,cli,config}.py, config/shipping.json and docs/rates.md -> docs/tariffs.md plus the README link. Hidden-on-start red and hidden-on-reference green are pinned by `tests/test_golden_t3.py`.
- Bullet 3 (decoy and generated file byte-identical, outside allowed_paths, planted edits seen red): ACHIEVED. Decoy `shipping/_vendor/legacy_rates.py`, generated `generated/rate_schema.py` and the shim `shipping/compat.py` are identical in repo/ and reference/ and absent from allowed_paths.json. The controls table in `.sdlc/research/885-controls.md` records red for an edit to each, for a missed deletion, a reverted config key and a reverted call site. One reading: a missed deletion is caught by the hidden test only, not by `reference-diff` (the path equals the start tree), and the naive overlay cannot isolate a property alone because an overlay cannot delete.
- Bullet 4 (exact-only allowed_paths; stdlib-only, write-free task code): ACHIEVED. `allowed_paths.json` lists nine exact paths, no slash entries. `test_t3_allowed_paths_exact_only` pins the no-prefix property (carried debt from slice 1) and `test_t3_exact_only_pin_detects_prefix_regression` shows it goes red against a prefix-matching `_allowed`. Notably the gesture itself stays green under that regression, so the pin lives in the test, not the gesture. `test_t3_task_shape` rejects write calls and third-party imports in task code.
- Bullet 5 (repo guards stay green): ACHIEVED in a scratch clone with T3 committed (third-party imports, write-surface, doc links, leak and exposure scans, documented gestures, golden verify, bench tasks, no-private-citations): 384 passed; `tools/rename_check.py` exit 0; zero new write-surface entries. Not proven in-tree: the change is uncommitted here, so guards that read tracked files were not run against it directly.
- Extra shipped: `evals/README.md` now says T3 is the first shipped task and lists `tests/test_golden_t3.py` beside the verify controls; `test_t3_readme_sentence_is_current` stops the stale sentence returning.
- Measured: one task, 0.60-0.69 s wall on Python 3.12, 3.96 s on 3.9.6, 2.08 s on 3.13.9. Nothing measured beyond one task; 10x/100x unmeasured (linear in task count, sequential runs are the ceiling).

## Debt (non-blocking)
- Reviewer note: the task prompt hints that vendored and generated code stays as is, which tips off the trap the task is meant to test; a prompt that withheld the hint would test more, at the cost of fairness.
- Reviewer note: hidden test 5 requires README.md to contain `docs/tariffs.md` but does not check that the old `docs/rates.md` link is gone, so a README carrying both links passes.
- Reviewer note: the hidden tests depend on the current working directory being the tree root. `_hidden_run` in `tools/readiness/bench_tasks.py` provides it, but any other executor that runs them elsewhere breaks them.
- Hidden tests and the reference ship in the repo, so an agent run from a checkout could read them (design doubt D-2, accepted, unresolved).
- The trap text has no field of its own (`trap` is boolean); it lives only in `rubric.json`, which has no consumer yet (D-3).
- The decoy, generated and shim hashes are pinned as constants in the hidden test and in `hidden_sha256`; any later edit to those files must update both or it goes red (intended, but an upkeep cost).
- Task `.py` basenames (rates, cart, cli, config, compat) join the documented-gestures basename pool; a future doc showing a gesture with one of those names could turn ambiguous.
- Only Python 3.12, 3.13.9 and one 3.9.6 run were measured; the repo floor is 3.10 and 3.10/3.11 were not run. The full test suite and Windows (verify.py refuses there) were not run.
- No minimum-task-count ratchet: an empty `evals/golden` still exits 0 (carried from slice 1).
- Change is uncommitted; tracked-file guards remain unproven in-tree until committed.

## Rule proposals (listed only, not applied)
- Guards that read tracked files should be run committed-in-scratch as a standard control step for any change that adds tracked fixture files (second consecutive slice where this mattered).
- A fixture task whose prompt names a protected-file constraint should state whether that hint is intentional, so the trap's difficulty is a recorded choice.

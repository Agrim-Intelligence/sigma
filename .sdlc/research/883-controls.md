# 883 controls (golden task T2), run on a scratch copy of the layout

Gesture for every control: `python3 evals/golden/verify.py` from the root of a copy holding
evals/golden/verify.py, tools/readiness/bench_tasks.py, evals/bench (minus tasks) and evals/golden/T2.
Hidden edits were re-hashed in task.json so only the intended RED shows. Each break was reverted by
restoring the pristine copy of evals/golden/T2.

Scenario: relkit/versions.py `version_key` returns string components, so "1.10.0" sorts below "1.9.0".
Two callers use it: relkit/changelog.py `newest` (named in the prompt) and relkit/compat.py
`meets_minimum` (not named). Hidden tests: one per caller, none on the helper.

Baseline: `ok T2`, `golden: 1 task(s), 0 red`, exit 0. Wall time 5.6 s (all tasks) and 5.1 s (`--only T2`).
By hand: start tree, both hidden tests fail (assertions, not a collection error); naive overlay, the
changelog test passes and the compat test fails; visible suite 4 passed.

- a. naive patch replaced with the true fix: `RED T2 naive: hidden tests pass on the naive patch (exit 0): 2 passed`, exit 1. Reverted.
- b. hidden test edited, not re-hashed: `RED T2 hidden-sha256: files/test_relkit_hidden.py: hash mismatch`, exit 1. Reverted.
- c1. reference helper made equal to repo helper: `RED T2 hidden-on-reference: ... (exit 1): 2 failed`, exit 1. Reverted.
- c2. repo helper made the fixed one: `RED T2 hidden-on-start: ... (exit 0): 2 passed` plus `RED T2 naive: ... (exit 0)`, exit 1. Reverted.
- d. naive patch fixes both call sites, helper untouched: `RED T2 naive: hidden tests pass on the naive patch (exit 0): 2 passed`, exit 1. This task does not discriminate a both-call-sites patch (no direct helper test, by design). Reverted.

Final: `ok T2`, exit 0, tree identical to the worktree copy.
Not measured: behaviour with more than one task present.

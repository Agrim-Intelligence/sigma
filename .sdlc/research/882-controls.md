# 882 controls: golden task T1

Gesture for every control: `python3 evals/golden/verify.py` (no flags). Each break was applied to the
real task directory, seen red (exit 1), and reverted by restoring a byte-identical backup. After the
last revert `diff -r` against the backup was empty and verify printed `ok T1`, `golden: 1 task(s), 0 red`, exit 0.

| # | Break | RED line seen |
|---|-------|---------------|
| 1 | reference/intervals.py set to the start-tree content | `RED T1 hidden-on-reference: ... (exit 1): 7 failed, 1 passed` |
| 2 | `<=` changed to `<` in reference | `RED T1 hidden-on-reference: ... 4 failed, 4 passed` |
| 3 | `max(...)` replaced by the new end | `RED T1 hidden-on-reference: ... 2 failed, 6 passed` |
| 4 | reference raises on empty input (the reference has no separate guard to delete; this is the inverse break) | `RED T1 hidden-on-reference: ... 1 failed, 7 passed` |
| 5 | reference sorts its argument in place | `RED T1 hidden-on-reference: ... 1 failed, 7 passed` |
| 6 | comment appended to the hidden test, hash not regenerated | `RED T1 hidden-sha256: files/test_merge_hidden.py: hash mismatch` |
| 7 | repo/intervals.py replaced by the reference | `RED T1 hidden-on-start: ... (exit 0): 8 passed` |
| 8 | extra file added to reference/ | `RED T1 reference-diff: extra.py added but not in allowed_paths.json` |
| 9 | `"trap": "false"` (string) | `RED T1 task-json: trap must be true or false` |
| 10 | task directory renamed to T1x | `RED T1x id: 'T1' differs from the directory name` |

Control 10: `--only T1` after the rename exits 2 (`no such task directory`), so it cannot pass either.

Start tree: hidden tests fail with assertion failures (exit 1), not a collection error (exit 2).

Guards (run before the controls): `write_surface.py check . docs/launch/write-surface.json` exit 0;
`rename_check.py` exit 0; `tests/test_doc_links.py` green. `exposure_scan.py tracked` reads HEAD, so it
cannot see uncommitted files and refused (exit 2); it must be run after the commit.
`tests/test_third_party_imports.py` cannot be collected on this host's Python 3.9 (needs `sys.stdlib_module_names`);
the task files import only `intervals`.
`tests/test_golden_verify.py` has one failure, `test_ci_runs_the_gesture_right_after_the_quality_gate_with_the_same_gate`,
because CI has no golden verify step yet; it does not depend on T1.

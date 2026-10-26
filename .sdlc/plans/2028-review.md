# Plan-review refinement for #2028 (plan sha256 7cc533bc…, SOUND-WITH-REFINEMENTS)

**R1.** `test_unclosed_stdin_is_bounded` runs the hook with `/bin/bash` explicitly, but only when
that file exists and reports a 3.x version. Production runs `bash session_start.sh`, and on macOS
`/bin/bash` is 3.2. Bash 3.2's `read -t` leaves the variable unset on timeout, which `set -u`
turns into a non-zero exit. Homebrew bash 5 on PATH leaves the variable set, so the control could
never go red under it. When no 3.x `/bin/bash` exists, the test skips and names the bash it found.

Control: delete the `HOOK_INPUT=""` line, see the test go red under `/bin/bash` 3.2, then restore it.

The plan file itself is unchanged. This note is the only change the delta review asked for.

## `## Tests` narrowed (owner ruling on narrow evidence, coordinator-approved)
The plan's `## Tests` now names only the test files this goal adds or changes, plus
`tests/test_enforcement_table.py` and `tests/test_session_start_cost.py`. The first guards the
`enforcement_table.py` row this goal changes, and an acceptance line names the second. Neither
skips here. Nothing else in the plan changed.

Dropped files. Each is regression-only (`git diff origin/main -- tests` shows none of them
modified), and each was a planned-tests gate failure, because some of its nodes skip on this
machine and a skip counts as not-passed:
- `tests/test_coexist.py`: other hook callers, unmodified. Its `real@<sha>` parametrized cases skip here; they were the first nodes the gate reported.
- `tests/test_dag_refresh.py`: other hook callers, unmodified.
- `tests/test_upkeep_window_1387.py`: other hook callers, unmodified.
- `tests/test_upkeep_session_1224.py`: other hook callers, unmodified.
- `tests/test_watch.py`: other hook callers, unmodified.
- `tests/test_watcher_staleness_single_source.py`: reads the hook text, unmodified.

I did not check which of the 25 skipped nodes belong to which of these files.

These files still run in the acceptance `verify_command`, so a regression there is still caught.

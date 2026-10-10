# #886 control evidence (P5)

Each guard was broken on purpose in the working copy, run on its documented gesture, seen red, restored
byte for byte (`cmp` against a pre-break copy), and seen green. Gesture form:
`python -m pytest tests/test_regression_ledger.py::<node> -q`, run with the repo's Python 3.12 venv because the
Homebrew `python3.12` has no pytest installed (same interpreter version, same command).

guard | broken by | red output | restored green
--- | --- | --- | ---
kill between reserve and settle (`test_sigkill_between_reserve_and_settle_counts_full_belt`) | reader counts an unsettled open as zero | 1 failed | 1 passed
corrupt line fails closed (`test_corrupt_line_fails_closed`) | parser stops flagging bad lines | 18 failed (every case) | 18 passed
month rollover (`test_month_rollover_starts_fresh_file`) | settle file chosen from the settle time, not the handle | 1 failed | 1 passed
signal inside critical section (`test_signal_inside_critical_section_does_not_deadlock`) | handler takes the lock itself | 1 failed in 1.2 s (assertion on exit status and settled state; bounded, no hang) | 1 passed
clamp (`test_clamp_reduces_belt_then_not_run_at_zero`) | belt = requested, unclamped | 1 failed | 1 passed
lock fail-open (`test_lock_timeout_is_not_run_deterministic`) | timeout yields "locked" | 1 failed | 1 passed
non-POSIX fail-open (`test_nonposix_refuses_and_writes_nothing`) | platform check disabled | 1 failed | 1 passed
SIGHUP (`test_sigterm_and_sighup_settle_before_exit`) | handler installed for SIGTERM only | SIGHUP case failed, SIGTERM passed | 2 passed
signal exit status (same node) | exit 1 instead of 128 + signum | 2 failed | 2 passed
no-model (`test_ledger_starts_no_model_and_no_network`) | `import subprocess` planted | 1 failed | 1 passed
naive datetime (`test_now_must_be_aware_and_is_converted_to_utc`) | naive `now` accepted | 1 failed | 1 passed
full fsync (`test_fsync_prefers_full_fsync_where_the_platform_has_it`) | F_FULLFSYNC ignored | 1 failed | 1 passed
settle_orphan on corruption (`test_settle_orphan_does_not_repair_corruption`) | corruption check removed | 1 failed | 1 passed
write detection (`test_every_write_under_evals_regression_is_documented`) | planted `open(path, "a")` and `os.write` in the module | 1 failed: "undocumented write: evals/regression/spend_ledger.py _planted open x1 ..." | 1 passed
write detection, allowlist | one DOCUMENTED_WRITES row removed from the test | 1 failed | 1 passed
inventory (`python3 tools/readiness/write_surface.py check . docs/launch/write-surface.json`) | the spend_ledger.py entry removed from the JSON (run in a scratch clone, files tracked there) | exit 1: "new write site evals/regression/spend_ledger.py:_ensure_dir fs-write" | exit 0
scanner blindness | planted `open(p, "a")` in the module, same scratch clone | `check` stayed exit 0 (it cannot see it, which is why the companion test exists) | n/a

Not run red: the two-process race (`test_two_process_race_smoke`) is a labelled smoke test; the deterministic
lock control above is the system of record.

Guards over the new files, run in a scratch clone where the new files are committed (these tests read
`git ls-files`): third-party imports, leak scan, exposure scan, doc links, write surface, no unsupervised
process pause, and the ledger tests: 294 passed. `tests/test_documented_gestures.py` FAILS there: see below.

Note: the module is named `spend_ledger.py` (not `ledger.py`): the documented-gestures guard resolves scripts by
basename, and a second `ledger.py` made the documented sigma-ledger gestures ambiguous.

Code-review send-back controls (each broken on purpose, run as `python3.12 -m pytest tests/test_regression_ledger.py -q`, then restored byte for byte):

guard | broken by | red output | restored green
--- | --- | --- | ---
writer/reader amount agreement (belt, spent, cap, round-trip property) | the 15-integer / 9-fractional digit check in `_money` replaced by `if False:` | 28 failed, 79 passed | 107 passed
duplicate run_id (`test_duplicate_or_settled_run_id_refused_before_writing`) | the `rid in state.opens or rid in state.settled` check replaced by `if False:` | 1 failed | 107 passed
lock leak after acquire (`test_signal_around_lock_acquire_and_release_does_not_leak_the_lock[lock-acquired]`) | no signal block in `_acquire` | 1 failed, 106 passed | 107 passed
lock leak during release (same test, `[lock-releasing]`) | no signal block around the close in `_locked` | 1 failed, 106 passed | 107 passed
exit status after the else-path settle (`test_signal_after_settle_before_handlers_restored_still_exits_128_plus_signum`) | the outer `except LedgerSignal` made unreachable | 1 failed (rc 1, raw LedgerSignal) | 107 passed
unblock removed (`_restore_mask` in `_locked` replaced by `pass`) | signals stay blocked | 7 failed, 100 passed, 102 s (bounded by the tests' child timeouts) | 107 passed

Note: the first draft of the lock-leak test put its seam inside the `try`, so breaking the mask stayed green; the seam
was moved before the `try` and the control re-run red. Residual: a signal landing in the interpreter between `_acquire`'s
return and the `_stage` call is covered only by the mask, not by a test.

Second send-back controls (broken on purpose, `python3.12 -m pytest tests/test_regression_ledger.py -q`, restored byte for byte):

guard | broken by | red output | restored green
--- | --- | --- | ---
deeply nested line (`test_corrupt_line_fails_closed[deeply-nested-json]`) | `except (ValueError, RecursionError)` reduced to `except ValueError` | 1 failed (RecursionError escapes) | 109 passed
settle id validation (`test_settle_refuses_malformed_reservation_before_writing`) | the reservation-id check in `settle` replaced by `if False:` | 1 failed (DID NOT RAISE) | 109 passed

Accepted residuals documented in the module docstring (Recovery): lock held to exit on a signal just before the release mask; custom handler left installed on a signal between `signal.signal()` and storing the previous handler. Both fail closed; neither has a test.

## Deterministic signal-window controls (replacing the timing-dependent system of record)

Nodes (tests/test_regression_ledger.py): test_signals_are_blocked_and_lock_held_at_the_acquire_and_release_seams,
test_handler_running_inside_the_critical_section_releases_the_lock[reserve-locked|reserve-written].
The real-signal test is now test_smoke_signal_around_lock_acquire_and_release_does_not_leak_the_lock (labelled smoke).

Each broken in evals/regression/spend_ledger.py, run with
`python -m pytest tests/test_regression_ledger.py::<node> -q`, seen RED, then restored byte-for-byte (cmp against a saved copy):
- acquire-side `_block_signals()` replaced by `None`: seams test RED (mask assertion at lock-acquired).
- release-side `_block_signals()` replaced by `None`: seams test RED.
- `os.close(fd)` in `_locked` removed: both handler-inside params RED (lock still held) and the seams test RED.
Stability: ledger file alone 30/30 green; 10/10 green with `-n 4` while a second `-n 4` pytest ran concurrently (112 passed each).

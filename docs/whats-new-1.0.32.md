# What is new in 1.0.32

This page lists every change in this release. The same entry is in the `1.0.32` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.31](#upgrading-from-1031) if you only want to know what
changes for you.

No new skill. One optional setting is new, `followups.max_added_slices`; it is not required.

## Upgrading from 1.0.31

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **The in-goal follow-up limit is a setting (#2178).** Under `followups.mode: in-goal`, a goal could add follow-ups to
   its own plan as new slices only up to a fixed 2 written into prose and code. The limit is now
   `followups.max_added_slices`, a whole number of at least 1, default 10. A missing key, a wrong type, a boolean, zero,
   a negative, a fraction or a numeric string reads 10, never unlimited; `4.0` reads 4.
   - **What changed.** `handoff.py` reads the setting with one helper (`max_added_slices`) next to `followup_mode`. The
     rule text that `handoff.py track` prints when it refuses a non-blocking follow-up carries the configured number.
     `handoff.py followup-mode` still prints the mode on line 1 and now prints `max_added_slices <n>` on line 2; the
     `/sigma-orchestrate` launch prompt reads that line instead of prose. The config template,
     `docs/configuration.md`, `docs/orchestrate.md` and the orchestrate references say "the configured limit (default
     10)".
   - **What you do.** Nothing, to keep the new default of 10. Set `followups.max_added_slices` to change it. Projects
     that stay on `followups.mode: file` are unaffected.
   - **Known:** there is no upper bound. A very large valid number, such as a billion, effectively removes the limit.
     The project decided a limit is wanted and did not ask for a ceiling. A project that wants a hard ceiling should set
     a number it is comfortable with.
   - **Measured:** offline, `tests/test_followup_mode.py`, one test per clause, each seen red with its clause broken.
   - **Not measured:** how a real phase subagent behaves at a limit other than the old 2; a real orchestrate batch; the
     full suite.

## Known, not fixed

- Two tests outside CI's smoke list still fail on a clean main on some machines:
  `test_the_retired_skill_pattern_names_exactly_the_shipped_skills` and
  `test_committed_budget_is_fresh_and_the_documented_command_prints_measurement_metadata`.
- One timing test is flaky: `test_the_wait_gesture_is_given_with_the_fallback_and_it_runs`.
- The CI `test` job runs only the smoke list; the full suite was not run for this release.

## Not measured

How a real phase subagent behaves at a limit other than the old 2, a real orchestrate batch, and the full suite. The
CHANGELOG entry lists its own gaps.

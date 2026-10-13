# D1 evidence: triage of the nightly full-matrix failures of 2026-10-11 and 2026-10-12

Measured against main at `3a303bee2cea`. Scope: the three tests that failed in the scheduled run of
2026-10-11 and the one that failed in the scheduled run of 2026-10-12. Failure text is read from the job logs of
those runs through the GitHub Actions REST API. The reproduction is one Linux container (Python 3.12, an
`amd64` image running under emulation on an arm64 Mac, limited to 2 CPUs and 2 GiB, with 6 busy loops during the
load runs). That is not the CI runner: it shares the code and the interpreter family, not the timing or the
memory of a hosted runner.

## Failure 1: `tests/test_bounded_run.py::test_term_comes_before_kill`

- Run 38114872169, head `abf91edf7787`, Linux legs on Python 3.10, 3.11, 3.12 and 3.13.
- Assertion, by leg: on 3.10, 3.11 and 3.13 the first (polite) run failed at `tests/test_bounded_run.py:220`
  with `assert ('ok', False) == ('timeout', False)`; on 3.12 the second (stubborn) run failed at line 228 with
  `assert ('ok', False) == ('timeout', True)`. Every leg shows `('ok', 0, '', '', 60.00x, False, ...)`: the command
  was reported finished normally after the full 60-second budget, not timed out.
- Mechanism (reproduced): the test injects a clock that returns "past the deadline" as soon as the command has
  written its announcement file. `run_group` reads the clock once to compute the deadline. If the command
  announces before that first read (the parent is descheduled between spawn and the first read), the deadline is
  computed from the already-jumped clock, lands in the far future, and the run waits for the command to end on its
  own. The same signature, an `ok` after about 60 seconds, appears in all four logs. (`bounded_run.py` computes the deadline after `Popen`, so spawn latency is not
  counted in the budget; this is stated in `.sdlc/research/1153.md` and is not a defect.)
- Evidence: at `abf91edf7787`, with the order forced (a harness plugin that holds `run_group` between the spawn and
  its first clock read until the announcement file exists), the test failed 3 of 3 runs with the same
  `('ok', ..., 60.03, False, ...)` text. At main it passed 3 of 3 under the same hold.
- Fix: the test's injected clock now returns 0 on its first call (`after()` in `tests/test_bounded_run.py`), merged
  in the change that closed #1153 (`131baf6b`, whose commit subject names only the plan documents). A
  deterministic test (`test_a_command_that_announces_before_the_deadline_is_taken_still_times_out`) forces the
  order and has its own unguarded control.
- A second mechanism can fail the same test and is NOT what the 2026-10-11 logs show: #1242 (an unreaped zombie
  kept a stopped group "alive" under load, so the TERM grace was waited out and reported as a KILL). A candidate
  fix is `89d8f1f5` (#1273) and was not redone here; issue #1242 is still OPEN. The 2026-10-11 failure text (`ok`, not an escalation) does not match
  that defect.
- Classification: test defect (the injected clock), fixed. Not a defect in `bounded_run.py`.

## Failure 2: `tests/test_bounded_run.py::test_overrun_stops_the_whole_group`

- Run 38114872169, Python 3.11, 3.12 and 3.13 (not 3.10).
- Assertion: on 3.11 the first attempt's `assert (result.outcome, result.escalated) == (br.TIMEOUT, False)` failed
  with `('ok', ..., 60.004..., False, ...)`. On 3.12 and 3.13 the first attempt passed and the control failed:
  `a direct-child kill must leave the grandchild: the check cannot see the bug`, because the control's run also
  ended normally after its 60 seconds, so its grandchild had already exited by itself. Both are the same cause as
  failure 1: a deadline computed in the far future.
- Evidence: at `abf91edf7787`, with the order forced as above and under the load described in the scope, the test
  failed 5 of 5 runs, every one with the `('ok', ..., 60.1, False, ...)` text of the 3.11 log. At main it passed
  5 of 5 under the same hold. Without the hold, under load, it passed 30 of 30 at `abf91edf7787` and 30 of 30 at
  main: the natural trigger was not reproduced.
- Fix: the same `after()` guard (`131baf6b`, #1153).
- Classification: test defect (the injected clock), fixed; the natural trigger rate is not established.

## Failure 3: `tests/test_bounded_run_hardening.py::test_split_mode_peak_memory_under_a_flood_is_bounded`

- Run 38114872169, all four Linux legs; reported peaks 861,636 KiB (3.10), 951,248 KiB (3.12), 1,015,596 KiB
  (3.11) and 899,164 KiB (3.13) against a bound of 131,072 KiB.
- Mechanism (reproduced): the test child reports `ru_maxrss`. On Linux an exec'd child inherits the parent's
  high-water resident size, and the parent was a pytest worker that had already held about 1 GiB over a
  20-minute suite. The reading was the worker's history, not the runner's memory.
- Evidence: in the container, run alone the test passes at `abf91edf7787`. Run from a process that first
  touched 900 MiB and is the test's direct parent (the pytest process itself; a holder one process above pytest
  does not trigger it, and that case was not run here) it fails at `abf91edf7787` with
  `peak 951908 KiB`, within 0.1 percent of the CI value of 951,248 KiB, and passes at main under the same 900 MiB
  holder.
- Fix: the child now reads `VmHWM` from `/proc/self/status`, which starts fresh at exec (`131baf6b`, #1153). The
  control `test_the_flood_reading_ignores_the_parents_resident_memory` holds 200 MiB and shows the old reading
  exceeds the bound while the new one does not.
- Classification: test defect (measurement), fixed. The runner's real memory use for the flood is NOT shown by either reading;
  the `VmHWM` reading passes at main in the container and on the nightly of 2026-10-13.

## Failure 4: 2026-10-12 scheduled run, `tests/test_invocation.py::test_the_documented_install_and_diagnose_gesture_works_as_written`

- Run 38208647371, head `b13a0ea2ef02`, Python 3.10 and 3.11 only (job 3.12 and 3.13 green).
- Assertion: `assert out == "installed-and-conforming"` failed, the output was `refused`.
- Mechanism (from the CHANGELOG entry that commit `7fee8664` adds, which says it was reproduced in Python 3.10 and
  3.11 containers; not re-measured here): Python 3.11 and older resolve a relative executable
  against the child's working directory, and the adapter copy was created under a relative `.sdlc` path, so the
  run of the adapter was refused on those interpreters. The export rehearsal record
  (`rehearsal-f92a7528a26b.md`) lists the same test failing on those two interpreters.
- Fix: `7fee8664` (#1213), which makes the adapter run path absolute.
- Evidence the fix holds: the scheduled run 38351883130 of 2026-10-13 at `cc3b97a3980a` (which contains
  `7fee8664`, `131baf6b` and `89d8f1f5`) passed all five legs, including Ubuntu 3.10, 3.11, 3.12 and 3.13.
- Classification: real defect in `invocation.py`, fixed. NOT reproduced here (no Python 3.10 or 3.11 with pytest
  was run); the evidence is the fix commit's own explanation and one green night.

## What is NOT established

- The natural rate of failures 1 and 2 on a hosted runner. Under load, without forcing the order, 0 of 30 runs
  failed before the fix. The 2026-10-10 night (run 38027579304, head `b9d1baaec222`) ran before these tests
  existed: `bounded_run.py` and its test files were first added in `97497f60` (2026-10-10, 22:33 IST), so that
  night never ran them. What is open is why the clock race fired on all four legs given 0 of 30 natural
  reproductions. The memory failure needs no race: the worker's roughly 1 GiB high-water mark explains all four legs.
- Issue #1242 is still OPEN. `test_term_comes_before_kill` can fail from two mechanisms (the injected-clock race
  fixed by #1153, and the unreaped-zombie mechanism under load addressed by `89d8f1f5`, which the 2026-10-11 logs
  do not show); one green night cannot separate them.
- That one green night (2026-10-13) after the fixes proves stability. It is one sample, not a census.
- The 3.10 leg for failure 2 (it did not fail there) and any Python version other than 3.12 in the container.
- The census re-run after these fixes: it remains a separate item, and the D1 bar of "0 flaky tests on
  Python 3.12" is not met by this note.
- The 2026-10-11 and 2026-10-12 failures were the only ones read; earlier scheduled runs were not triaged.

## Method

Trees were exported with `git archive` at `abf91edf7787` and `3a303bee2cea`. One container, `--memory=2g
--memory-swap=2g --cpus=2`, one test at a time, nothing run in parallel with another container. The busy loops
were started and stopped by exact process id. The hold plugin and the memory holder are the scratch scripts of the
triage and are not part of the repository; the repository carries the two deterministic tests named above.

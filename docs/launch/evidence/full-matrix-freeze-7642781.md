# Full matrix on the freeze commit 7642781 (2026-10-13)

One manual dispatch of the `full` job of the CI workflow on `main` at `7642781` (run 38438534549, event
`workflow_dispatch`, attempt 1, started 17:25 UTC, completed success). This is the run the amended D11 threshold names:
the nightly full matrix, five legs, green on the launch commit, proven by one manual dispatch on the freeze commit.

| Leg | Result |
|-----|--------|
| ubuntu-latest, Python 3.10 | success |
| ubuntu-latest, Python 3.11 | success |
| ubuntu-latest, Python 3.12 | success |
| ubuntu-latest, Python 3.13 | success |
| macos-latest, Python 3.12 | success |
| regression-control | success |

`main` did not move between the dispatch and the end of the run (tip `7642781` throughout; the only later change is a
docs-only scorecard merge). The scheduled nightly of 2026-10-13 on `cc3b97a` also passed all five legs.

## The nightly history this run follows (read this before reading the green)

| Run | Commit | Result |
|-----|--------|--------|
| nightly 2026-10-10 | | green |
| nightly 2026-10-11 (38114872169) | `abf91ed` | red on all four Linux legs, **including Python 3.12**: 3 failed tests each on 3.12, 3.11 and 3.13, 2 on 3.10 |
| nightly 2026-10-12 (38208647371) | `b13a0ea` | red on 3.10 and 3.11: 1 failed test (the documented install-and-diagnose gesture), a regression in the diagnose change, fixed by the change that made diagnose work on Python 3.10 and 3.11 |
| manual 2026-10-12 (38257048928) | `3262d15` | red on 3.10 only: 1 failed test (`test_term_comes_before_kill`) |
| nightly 2026-10-13 (38351883130) | `cc3b97a` | green, five legs |
| manual 2026-10-13 (38438534549) | `7642781` | green, five legs (this record) |

Failure counts above were read from the job logs. The failing tests of 2026-10-11, as read in the independent scoring review,
were `test_overrun_stops_the_whole_group` and `test_term_comes_before_kill` (both `tests/test_bounded_run.py`) and
`test_split_mode_peak_memory_under_a_flood_is_bounded` (`tests/test_bounded_run_hardening.py`).

## What was and was not fixed

- The three 2026-10-11 failures and the 2026-10-12 failure are triaged in `d1-nightly-failure-triage-3a303bee2cea.md`.
  `test_term_comes_before_kill` and `test_overrun_stops_the_whole_group` failed because the tests' injected clock was
  read before the deadline was computed (a far-future deadline); reproduced with the order forced, fixed by #1153.
  `test_split_mode_peak_memory_under_a_flood_is_bounded` read a peak inherited from the pytest worker's own high-water
  mark; reproduced with a 900 MiB direct parent, fixed by reading `VmHWM` (#1153). The 2026-10-12 `test_invocation`
  failure on Python 3.10 and 3.11 was fixed by #1213 and was not reproduced in that triage.
- Issue 1242 stays open. The unreaped-zombie mechanism (reproduced deterministically, change #1273) is a second, separate
  mechanism that can fail `test_term_comes_before_kill`; the 2026-10-11 logs show the clock race, not the zombie. The natural
  trigger on a runner was never reproduced, so it is not shown which mechanism produced any given red run.
- Two green runs after fixes are evidence, not proof. The flake census (`flake-c82e3dfa7420.json`) is older than all of this and
  was not re-run.

## What this run does NOT show

- It is Linux on GitHub-hosted runners and one macOS runner; Windows, Codex and Cursor are not cells.
- It is the test suite, not a live user run: the live macOS runs are in `live-onboarding-2026-10-12.md`.
- The PR-time `test` job and `onboarding-control` are skipped on a manual dispatch by design; they ran green on the pull requests
  that built this commit.
- Repository settings on the public repository (branch protection, team permissions, private vulnerability reporting) are not
  applied by this run; that is an owner action recorded under D11.

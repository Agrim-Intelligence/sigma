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

- `test_term_comes_before_kill` (issue 1242): the cause was reproduced deterministically (an unreaped zombie kept a stopped
  process group looking alive) and fixed in the merged `bounded_run.py` change. It was never caught live on a runner, so it is
  not shown that this mechanism is what failed there. Issue 1242 stays open until the nightly is green over several runs.
- The other two tests that failed on 2026-10-11 (`test_overrun_stops_the_whole_group` and the peak-memory flood test) have not
  been triaged. `measured-results.md` says whether those failures were fixed, flaky or environmental was not investigated.
- Two green runs after fixes are evidence, not proof. The flake census (`flake-c82e3dfa7420.json`) is older than all of this and
  was not re-run.

## What this run does NOT show

- It is Linux on GitHub-hosted runners and one macOS runner; Windows, Codex and Cursor are not cells.
- It is the test suite, not a live user run: the live macOS runs are in `live-onboarding-2026-10-12.md`.
- The PR-time `test` job and `onboarding-control` are skipped on a manual dispatch by design; they ran green on the pull requests
  that built this commit.
- Repository settings on the public repository (branch protection, team permissions, private vulnerability reporting) are not
  applied by this run; that is an owner action recorded under D11.

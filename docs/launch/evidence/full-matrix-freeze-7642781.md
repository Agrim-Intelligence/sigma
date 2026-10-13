# Full matrix on the freeze commit 7642781 (2026-10-13)

One manual dispatch of the `full` job of the CI workflow on `main` at `7642781` (run 38438534549, event
`workflow_dispatch`, started 17:25 UTC, completed success). This is the run the amended D11 threshold names: the
nightly full matrix, five legs, green on the launch commit, proven by one manual dispatch on the freeze commit.

| Leg | Result |
|-----|--------|
| ubuntu-latest, Python 3.10 | success |
| ubuntu-latest, Python 3.11 | success |
| ubuntu-latest, Python 3.12 | success |
| ubuntu-latest, Python 3.13 | success |
| macos-latest, Python 3.12 | success |
| regression-control | success |

`main` did not move between the dispatch and the end of the run (tip `7642781` throughout).

## Context, so the result is read correctly

- The same matrix failed twice before: the scheduled run of 2026-10-11 and the manual run of 2026-10-12 on `3262d15`, each on
  one test, `test_term_comes_before_kill`, on the Linux legs (issue 1242). That test fails under host load; a cause that was
  reproduced deterministically (an unreaped zombie kept a stopped process group looking alive) was fixed in the merged change
  to `bounded_run.py`. The scheduled run of 2026-10-13 on `cc3b97a` passed all five legs, and so did this run.
- Two green runs after a fix for a load-sensitive failure are evidence, not proof. The failure was never caught live on a runner, so
  it is not shown that the zombie mechanism is what failed there; issue 1242 stays open until the nightly is green over several
  runs.
- The PR-time `test` job (the macOS subset) and `onboarding-control` are skipped on a manual dispatch by design; they ran green on
  the pull requests that built this commit.

## What this does NOT show

- It is Linux on GitHub-hosted x86_64 runners and one macOS runner; Windows, Codex and Cursor are not cells.
- It is the test suite, not a live user run: the live macOS runs are in `live-onboarding-2026-10-12.md`.
- Repository settings on the public repository (branch protection, team permissions) are not applied by this run; that is an
  owner action recorded under D11.

# D5: drills and the dead-vs-idle control, re-run at current main

Main at the time: `0d14fe004d5b`. The dead-vs-idle runs used that tree plus the one change in this pull request
(`tools/readiness/dead_vs_idle.py` gained `--break-hook`, and `tests/test_session_start.py` gained per-clause tests);
their records carry `commit` `5ccaa68a3b22` for that reason. The drills ran on `0d14fe004d5b` itself.

Hosts: macOS native on the developer's machine; Linux aarch64 in ONE Docker container from the stock Python image,
`--memory=1g --memory-swap=1g --cpus=2 --rm`, a clone made inside the container, nothing run against the host checkout.
Nothing was run on x86_64 Linux, a CI runner or Windows (Windows is refused by the drills).

## 1. The four hermetic drills, 5 seeds each

Gesture, as in the earlier records: `drills.py evidence --workdir <w> --json drills-<sha12>.json`. Files:
`drills-darwin-0d14fe004d5b.json` (run with an empty HOME so the doctor lines in the transcripts describe no installed plugins on the host) and `drills-linux-0d14fe004d5b.json` (the tool insists on the `drills-<sha12>.json`
name, so each file was renamed to add the platform after the run).

| Platform | Runs | Invariants passed |
| --- | ---: | --- |
| darwin | 20 (D1-D4 x seeds 1-5) | all: D1 40, D2 15, D3 15, D4 20 |
| linux | 20 | all: D1 40, D2 15, D3 15, D4 20 |

## 2. The macOS D4 reporting failure in `drills-cde9869d0cf8.json`

The earlier macOS record shows, in all 5 D4 runs, two invariants failing as `control_failed_and_recorded`:
`doctor_explicit_stop_file_reporting` (the doctor said `idle/on` and printed a `last heartbeat` line) and
`session_start_explicit_stop_file_reporting` (the hook said "has never started"; neither named the stop file).

**Finding: fixed since. It was a real product gap at that commit, not a macOS limit of the drill, and it does not reproduce now.**

- The record is at `cde9869d0cf8` (2026-10-01). The commit that added stop-file reporting to the doctor and to
  `hooks/session_start.sh` (a shared `stopfiles.py` reader, #416) is `1c255715` (2026-10-02), one day later; that same commit
  changed `drills.py` so D4 asserts the report rather than only recording its absence.
- Reproduced both ways on this macOS host, D4 seed 1: the tree at `cde9869d0cf8` prints `D4 seed 1: recorded failure`;
  the tree at `0d14fe004d5b` prints `D4 seed 1: pass`. Same host, same gesture, only the commit differs.
- All 20 D4 invariants pass on macOS at `0d14fe004d5b` (record above), and on Linux, so nothing here is macOS-specific.
- The earlier Linux record (`4d16cfe49c34`) was already past `1c255715`, which is why it showed no D4 failure.

## 3. Dead vs idle at current main

`tools/readiness/dead_vs_idle.py` starts the real `watch_daemon.py` in a scratch repository (interval 60 s, `stale_after`
180 s), signals the exact pid it spawned (no pattern kill, no SIGSTOP), and reads the doctor's `_ledger_watcher_state` and
the real session-start hook at each stage. Files: `dead-vs-idle-{darwin,linux}-0d14fe004d5b.json`,
`dead-vs-idle-break-{darwin,linux}-0d14fe004d5b.json`, `dead-vs-idle-break-hook-{darwin,linux}-0d14fe004d5b.json`.

| Stage | Doctor row | Session start | pid alive | darwin heartbeat age | linux heartbeat age |
| --- | --- | --- | --- | ---: | ---: |
| A. alive between ticks | `ON - last heartbeat 1 min ago` | silent | yes | 35.3 s | 39.1 s |
| B. after SIGTERM | `ON, but NEVER RUN` | "has never started" | no | no file | no file |
| C0. SIGKILL, read at once | `ON - last heartbeat 0 min ago` | silent | no | 12.8 s | 14.8 s |
| C1. SIGKILL, older than 180 s | `STALE - ... probably dead` | "looks stale" | no | 202.7 s | 204.8 s |

Normal run: 8 of 8 checks pass on both platforms. The caveat in `d5-linux-drills-and-liveness.md` stands: a clean SIGTERM
removes the heartbeat, so it reads NEVER RUN (distinguished from idle by absence, not by age); only a crash is told by age,
and a killed watcher reads `ON` until `stale_after` has passed.

### The control, one clause at a time

The earlier record broke only the doctor's age clause. The session-start hook keeps its own copy of the rule
(`AGENTS.md`, "Watch-loop timeouts and session-start staleness"), so that copy had never been shown to fail.

| Run | Break | Checks that went red (both platforms) |
| --- | --- | --- |
| `--break` | the doctor's `stale_after_seconds` returns "never" | only `C1_dead_reads_STALE_by_age`; `C1_session_start_warns` stayed green |
| `--break-hook` (new) | a scratch copy of `session_start.sh` with its own `age >= stale_after` replaced by "never stale"; doctor untouched | only `C1_session_start_warns`; `C1_dead_reads_STALE_by_age` stayed green |

`--break-hook` fails the build of the mutant if the clause text does not appear exactly once, so it cannot become a no-op by
drift. Each break changes exactly one reader and turns exactly one check red, which is what shows the two copies are
separately guarded.

### The hermetic test, and each clause of the hook's copy broken on its own

The hook's copy had two tests that fail when it is removed outright (`test_stale_watcher_warns`, `test_fresh_watcher_is_silent`)
but none that failed when only its multiplier, floor or fallback changed. New in `tests/test_session_start.py`:
`test_age_clause_stale_only_at_three_intervals`, `test_age_clause_floor_of_180_seconds`,
`test_age_clause_unusable_interval_falls_back_to_900`, and `test_hook_agrees_with_doctor_age_rule` (a grid compared against
the doctor's own row). Each was run red against a single-clause mutation of `hooks/session_start.sh`, then green on the real file:

| Mutation of the hook's copy | Tests red | Red before this change? |
| --- | --- | --- |
| comparison never true | 9 (incl. the 4 new) | yes, by 5 old tests |
| comparison always true | 5 (incl. the 4 new) | yes, by `test_fresh_watcher_is_silent` |
| multiple 3 -> 1 | 3: three-intervals, fallback, parity | no: all 3 are new |
| multiple 3 -> 100 | 9 | yes, by old tests |
| floor 180 removed | 2: floor, parity | no: both new |
| 900 fallback removed | 2: fallback, parity | no: both new |

Not shown: the `>=` versus `>` edge at exactly `stale_after` (no test places a heartbeat on that second; it would be a
timing race), and the hook's "looks dead" branch is covered by the existing `test_dead_watcher_warns` only.

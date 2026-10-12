# D5: Linux drills and the dead-vs-idle control

Commit measured: `4d16cfe49c34e42ac3e1857f1ed814987029191c` (branch `evidence/d5-linux-drills-growth-liveness`;
its tree is `f627d06a` plus `tools/readiness/dead_vs_idle.py` and `tools/readiness/scale_probe.py`).
Host: Docker on a Mac, a Linux aarch64 container from the python 3.12 image, `--memory=1g --memory-swap=1g
--cpus=2`, one container at a time. A clone of the commit was made inside the container; nothing on the host
checkout was run.

## 1. The four hermetic drills on Linux

Same tool and gesture as `drills-cde9869d0cf8.json`: `drills.py evidence --workdir <w> --json drills-<sha12>.json`.
Record: `drills-linux-4d16cfe49c34.json` (platform `linux`). D1, D2, D3, D4 x 5 seeds = 20 runs, every
invariant of all 20 passed. Not shown: any other Linux distribution or architecture, x86_64, a Linux CI runner.
The macOS record stays at `drills-cde9869d0cf8.json` (different commit).

## 2. Dead vs idle on a really stopped watcher

`tools/readiness/dead_vs_idle.py` starts the real `watch_daemon.py` in a scratch repository (interval 60 s,
so `stale_after` = max(180, 3 x 60) = 180 s), signals the exact `Popen` pid (no pattern kill, no SIGSTOP), and
reads both the doctor's `_ledger_watcher_state` and the real `hooks/session_start.sh`. Record:
`dead-vs-idle-4d16cfe49c34.json`.

| Stage | Doctor row | Session start | pid alive |
| --- | --- | --- | --- |
| A. alive between ticks (heartbeat 39 s old) | `ON — last heartbeat 1 min ago` | silent | yes |
| B. after SIGTERM to its pid | `ON, but NEVER RUN` | "has never started" | no |
| C0. second watcher, SIGKILL, read at once (heartbeat 15 s old) | `ON — last heartbeat 0 min ago` | silent | no |
| C1. same, heartbeat 205 s old | `STALE — last heartbeat 0.1h ago; the watcher is probably dead` | "looks stale" | no |

Findings, stated plainly:

- A clean SIGTERM removes `watch.pid` and `watch.heartbeat` (the daemon's own cleanup), so the stopped
  watcher reads "never run", not "stale". It is still told apart from the alive one, but by absence and not by age.
- Age shows up only for a crash (SIGKILL; the same file state an OOM kill or power loss leaves). That leg
  was added to the requested SIGTERM leg because SIGTERM leaves no age to read.
- Detection latency for a crash is the staleness bound: a watcher killed 15 s ago still reads `ON`. The row flips
  to STALE only after `stale_after` (180 s at this interval).

### The control: break the age clause and see it red

`--break` makes the doctor's `sync.stale_after_seconds` return an effectively infinite bound (the age rule
removed, leaving an existence-style check). Record: `dead-vs-idle-break-4d16cfe49c34.json`, exit 1:
`FAIL C1_dead_reads_STALE_by_age` (the doctor row read `ON — last heartbeat 3 min ago` for the dead watcher).
A first version of this mutant patched a module object the doctor reloads on every call, so it changed nothing and all
checks stayed green; it was caught by running it, and fixed to patch the loader. The break covers the doctor's age
clause only; the session-start hook keeps its own copy of the rule and `C1_session_start_warns` stayed green
under it (a separate clause, not separately broken here). The existing hermetic dead-loop fixtures in
`tests/test_session_start.py` and `tests/test_doctor.py` are unchanged.

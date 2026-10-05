# B6 slice #458: per-goal state records

Scope: the files under the repo-local, gitignored `.sdlc/state/` keyed by goal or run id:
`work`, `landing`, `verify`, `propagation`, `escalation`, `withheld`, `unit-tracking`, `goal-review`,
`phase`, `run_stop`, `agents`. Every path is written by Sigma Python (or, for `goal-review`, by the
agent following `skills/sigma-goal-review/references/confirm.md`) inside the project; none is under
a host configuration root, and nothing here prunes one. Sibling slices own the action log and
witness (#457), events/time/ledger (#461) and review evidence (#459).

## Measured

Producing revision: origin/main f155c48. Two separate measurements:

- Real writers, one goal, scratch repo (`loop.py agent-start`, `work.py start`,
  `phase_report.py start`, `loop.py verify` with command `true`, `loop.py escalate ... --after
  plan-review`): verify 926 B, work 169 B, phase 257 B, escalation 74 B, agents 6 B. These are
  floors: a real verify record also carries the content fingerprint, test-first and witness blocks.
- The operator's long-lived checkout (data from many revisions, not a clean run): verify 55 files,
  3,929,772 B, median 16,468, p95 319,888, max 593,634 B; about 80% of the bytes are the per-test
  `witness` lists. Mean 71 KB per verified goal, so 10x is about 39 MB and 100x about 393 MB for
  verify alone. landing 2 files/696 B, phase 5/1,534 B, work 16/3,076 B, agents 12/30 B.
- Not measured: escalation, propagation, run_stop, unit-tracking, withheld, goal-review (absent
  on that checkout, so no at-scale number exists); filesystem block overhead; a goal verified
  hundreds of times. A 0 in `growth-audit.json` for these means absent, not empty at scale.
- Sweep cost, scratch, `goal_state_prune.py sweep --dry-run`: 550 terminal stems 0.35 s, 5,500
  stems 1.22 s (about 0.17 ms per stem plus a fixed 0.25 s of module loading). In steady state
  only stems still holding an owned file are scanned, so the scan stays small.

## Decision per family

| Family | Decision | Mechanism |
| --- | --- | --- |
| `verify`, `landing`, `propagation`, `escalation`, `unit-tracking` | new bounded prune | `goal_state_prune.sweep`, run at the end of every `loop.py _record done` and by the operator |
| `phase` | already bounded; sweep adds crash leftovers | `loop._phase_marker_end` at record; sweep |
| `agents/<goal>/` | already bounded; sweep adds dead markers of a terminal goal | `loop.agent_end` at record; sweep |
| `agents/<key>/` (slack) | already bounded on the clean path; residual retained | `slack_commands_listen.finish_claim` calls `agent_end` |
| `run_stop` | age-based prune | sweep removes markers older than 30 days |
| `work` | retained for the worktree lifecycle | `work.finish` unlinks it with the worktree |
| `withheld` | intentionally unbounded | see ceilings |
| `goal-review` | intentionally unbounded | see ceilings |

### What the pruner deletes, and what it never does

Candidate: a goal whose action log's newest `actionlog.INTERNAL_KINDS` row is `recorded
result=done`. It is skipped, never deleted, when any of these hold: the log row or any file is
younger than seven days (`--min-age-days`; not only a race guard: the verify record is a frozen
contract kind that a downstream ingester and `tools/onboarding_control.py` read after `done`, so
nothing is removed at `done` itself); the goal has a
work record (a worktree `finish` kept; the sweep removes the files after `finish` runs); an agent
marker is alive or of unknown standing; the newest row is anything else (a reopened goal appends
`claimed`; parked, failed and awaiting-merge goals never wrote `done`). Only regular files directly
under the family directory are unlinked: no recursion, no symlink followed, no file whose name is
not exactly `<goal><suffix>`. `--limit` caps goals pruned per call, so permanently kept stems cannot
starve the prunable ones. The goal's own log and witness files are left to #457.

Lever (operator, any host; the answer for a machine that records no further `done`, where the
in-line sweep never runs): `python3 skills/sigma-loop/scripts/goal_state_prune.py sweep .sdlc
[--dry-run] [--limit N] [--min-age-days N]`. `--dry-run` prints what it would remove. The seven-day
default is an unmeasured policy choice, not derived from any consumer's cadence: the readers
traced after `done` are `tools/onboarding_control.py` (within minutes) and the frozen `verify` kind
in `contract/README.md`, whose ingester's cadence was not measured here. Raise it with
`--min-age-days` if an ingester reads less often than weekly.

Dry-run on the measured checkout (nothing deleted): with the default seven days 0 paths are
removable, because every record there is under seven days old (12 goals held by work records, 2
not terminal); with `--min-age-days 1` it would remove 32 paths / 2,485,369 of the 3,929,772
verify-store bytes of that checkout, still holding the 12 work-record goals.

### Recovery, no human and no process pausing

- Crash before or during a sweep: the log row still proves `done`; the next `record done` of any
  goal runs the sweep again, or the operator runs the lever.
- Crash mid-prune: a subset remains; every unlink is `missing_ok`, so the next sweep finishes.
- Restore from backup: restored files carry fresh mtimes, wait out the seven-day window, then are
  removed again if their goal is still terminal; a restored `claimed` row after `done` protects it.
- A worker re-registering a marker or a late `note`/`verify` on a finished goal: the recreated file
  is removed by a sweep once it passes the window. Agent directories are emptied non-recursively
  then `rmdir`ed; a directory that was re-populated meanwhile is simply kept.
- Limit: the sweep needs `action_log.enabled` (shipped on in the template). With it off there is
  no terminal signal, so nothing is pruned (the existing `agent_end` and phase-marker cleanups
  still run) until the log is enabled. A process killed during `_signal_unit_completion`, which
  runs before the hook, skips that one sweep; the next `record done` runs it.
- Nothing is removed at `done`: a replayed `record done` still finds its verify evidence for seven
  days, and a goal reopened inside that window keeps its records (a `claimed` row ends terminality).
- A reopen between the sweep's scan and its unlink is caught by re-reading the log row just before
  each goal is pruned; the remaining window is the few microseconds between that read and the
  unlink.
- More ceilings: the log is read from its last 256 KB only, so a `done` row buried under more
  than 256 KB of later non-internal rows is not found and its goal is kept; a goal reopened while
  `action_log.enabled` is off leaves a stale `done` row, protected only by the seven-day window and
  the work record; the prune is on by default because it removes only Sigma's own gitignored state
  under `.sdlc/state` and never a host configuration root. The scale timing above used synthetic
  two-row logs, so it measures the scan, not large logs.
- A goal reopened AFTER its window and prune starts clean: its landing decision is gone (the merge
  gate refuses and says to re-pick through the loop, which re-records it), and its escalation floor
  and unit-tracking cooldown reset to their defaults. Verify evidence is rewritten by the next
  `verify`, which `done` requires anyway.
- Ceilings the prune accepts: a later `verify`, `gate` or `released` row appended to an already
  done goal makes it non-terminal again, and a corrupt agent marker is of unknown standing; both
  keep their goal's files until the next `done` or an operator removes them.
- Cost and volume: files wait seven days, so the standing volume is about seven days of goals:
  at the measured 71 KB mean per verified goal, 100 goals a day is about 50 MB, not 393 MB. Each
  `record done` also runs the sweep. The scan is one stat or one bounded log-tail read
  per stem that still owns a file: 0.35 s at 550 and 1.22 s at 5,500 stems in the dry-run
  measurement, so linear, and it grows only with stems that are kept (work records), not with
  history, because pruned stems leave the scan.
- Coupling to #457: the action log is the only terminal signal. A stem with no log row (log off,
  a goal that finished before the log existed, or a log #457 later prunes at its window, default
  90 days) is never a candidate, so its files stay: fail-safe but unbounded for such stems. Per-goal
  files normally go seven days after `done`, long before a 90-day log prune. If #457 ships first, a
  goal whose work record outlives its log keeps its verify file; that sequencing is a follow-up.

## Documented unbounded or retained decisions

- `work/<goal>.json`: 169 B per live worktree. It must outlive nothing but its worktree; deleting
  it would orphan a checkout that may hold uncommitted work. Real checkout: 16 records, 6 belong
  to closed issues whose worktrees survive. Because the prune keeps every family while that record
  exists, each such goal also retains its verify record (median 16,468 B, max 593,634 B measured).
  Ceiling: one work record plus one verify record per surviving worktree. Reclaim:
  `work.py finish .sdlc <goal> --force`, after which the sweep removes that goal's other files.
- `withheld/<goal>.md`: appended findings about the kit itself. Where no upstream repo is
  configured it is the only full copy, so it is not pruned. Ceiling: one markdown file per goal
  that surfaced a kit finding (not measured at scale: directory absent). Reclaim: delete the file
  after filing the finding.
- `goal-review/<n>.plan.json` and `<n>.report.json`: keyed by the story number, written once per
  confirmed design, never passed to `record`, so there is no code-level terminal signal. The plan
  is the re-run input of `compile_plan.py`. Ceiling: two JSON files per confirmed design (not
  measured: directory absent). Reclaim: delete `.sdlc/state/goal-review/<n>.*` once the epic's
  slices exist.
- `agents/<key>/` left by a crashed slack-driven command: no log row proves it terminal, so it is
  kept; a dead marker costs a few bytes. Reclaim: remove the directory.
- `run_stop/<run_id>.json`: pruned by age. A live run older than 30 days that had not stopped would
  at worst emit one duplicate run-stop row.

## Disposition and the audit gesture

`docs/launch/dispositions/458.json` covers all 29 patterns of the issue. The documented gesture in
`growth-audit.md` is run on a scratch copy by `tests/test_goal_state_disposition.py`, which asserts
every pattern leaves `b6_disposition.unresolved_patterns`.

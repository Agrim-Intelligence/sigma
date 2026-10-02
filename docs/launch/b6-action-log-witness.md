# B6: action log and witness streams (#457)

Part of B6 #419. Covers `.sdlc/state/log/<goal>.jsonl` (action log) and
`.sdlc/state/witness/<goal>.jsonl` (witness stream). Decision: **bounded pruner added**
(`skills/agrim-loop/scripts/retention.py`), with named ceilings below.

## Measured

Measured at origin/main `76536c9` (the revision that produces the store) against this repository's
live `.sdlc/state`, whose files were written by many earlier revisions: a real store, not a clean-room
run. Command, from the repository root:

```sh
python3 - <<'PY'
import glob, os, statistics
for d in ("log", "witness"):
    fs = glob.glob(f".sdlc/state/{d}/*.jsonl")
    sizes = [os.path.getsize(f) for f in fs]
    lines = sum(sum(1 for _ in open(f)) for f in fs)
    print(d, len(fs), "files", sum(sizes), "bytes", lines, "events",
          "median", statistics.median(sizes), "max", max(sizes), "B/event", round(sum(sizes) / lines, 1))
PY
```

| Store | Goals | Bytes | Events | Per goal (median / mean / max) | Bytes per event |
| --- | ---: | ---: | ---: | --- | ---: |
| action log | 85 | 207,178 | 1,166 | 1,616 / 2,437 / 14,491 | 177.7 (max line 657) |
| witness | 36 | 639,969 | 1,915 | 3,803 / 17,776 / 159,216 | 334.2 (max line 479) |

Linear extrapolation of the byte totals (a calculation, not a measurement): 10x is about 2 MB log and
6.4 MB witness; 100x is about 20.7 MB log and 64 MB witness. Disk is not the ceiling.

**The ceiling is the reader.** `agrim-log slots` reads every goal file on each call. Measured by
copying the 85 real log files 100 times into a scratch `.sdlc` (8,500 files, 20.7 MB) and running
`python3 skills/agrim-log/scripts/log.py slots <scratch>/.sdlc`: 0.06 s at 85 files, 0.68 s at 8,500
(one run each, one machine). Pruning closed goals is what bounds that.

Sweep cost, measured with `loop.py prune-state <scratch>/.sdlc --dry-run` on the same 8,500-file
scratch: 0.39 s wall, but every file there is inside the window, so this is the stat-only path; the
past-window path (an 8 KiB tail read per candidate, at most 200 removals) was not timed. On this
repository's real store `prune-state --dry-run --keep-days 30` found 0 of 86 goals eligible, because
every file is under 30 days old: the pruner has not yet deleted anything real.

Not measured: a 10x run (only 1x and 100x), other filesystems, a goal with thousands of verify runs,
and multi-machine fleets.

## Ownership

Both paths are written only by Sigma's own Python (`actionlog.append`, `witness.record`), live under
the repository's gitignored `.sdlc/state/`, and are Sigma-owned. Neither is under a host
configuration root, and the pruner never reaches one; it unlinks only those two direct children.

## What a prune must not break

Readers: `log.py` active/slots/status (a goal is live unless its newest code-written row is
`recorded`), `work._branch_base_from_log` (a restarted goal's original base, read only when the goal's branch
outlived its work record; the pruner therefore keeps the log of any goal whose branch still exists), `triage.py` (parked reason, open goals only), `doctor.py` dispatch compliance
(30-day window), and `witness.verdict` / `red_green.py` (consulted while a goal is live). The log is a
local best-effort status cache, not the audit record: the durable record is the ledger/journal events,
the PR, and the committed `.sdlc/` artifacts.

## Resolved rows

Documented gesture from `growth-audit.md`, run in a scratch copy so the committed snapshot is untouched:
`python3 tools/readiness/growth_audit.py . --measure-sdlc --b6-issue 419 --json docs/launch/growth-audit.json`.
With `dispositions/457.json` present, none of the seven `.sdlc/state/log*` and `.sdlc/state/witness*`
patterns is in `b6_disposition.unresolved_patterns` (all seven were before), and each of their
`store_measurements` rows reads `source-proven prune`.

## The pruner

A goal's two streams are removed only when all hold: its newest code-written log row (an agent cannot
forge one) is `recorded` with `result: done`; both files are older than the window by mtime and by last
row timestamp; no owner marker (`claims/<g>.claimed`, `agents/<g>/`, `work/<g>.json`,
`phase/<g>.json`, `.sdlc/work/<g>`) was touched inside the window; and, with `work` enabled, no goal
branch (local or remote) survives (one `git for-each-ref` per sweep; if it fails, nothing is pruned). Parked, failed, awaiting-merge,
re-claimed and never-recorded goals are never touched. A goal reopened but not yet re-claimed is
judged by its last row (done), so it is prunable past the window once its branch is gone; nothing in
the pruned files is needed to start it again, because a fresh cut resolves its base normally. A witness file with no log is kept.

- Window: `action_log.retention_days`, default 90, floor 30 (doctor reads 30 days); `false` disables.
- Bound: at most 200 goals REMOVED per sweep, oldest first. This bounds deletions, not work: every
  past-window goal that stays (parked, failed, never closed) is stat'ed and tail-read again on each sweep.
- Triggers, both Sigma Python, host-agnostic: `loop.py record <goal> done` sweeps; the lever is
  `python3 skills/agrim-loop/scripts/loop.py prune-state .sdlc [--dry-run] [--keep-days N] [--limit N]`.
  Run `--dry-run` first to see what would go.
- Markers are staleness-checked, not presence-checked, because `.claimed` is never removed and an agent
  marker survives a crashed goal; the cost is that a marker touched inside the window defers a prune.

## Recovery (no human, no process pause)

- Crash mid-sweep: the witness is unlinked first, the log last; a leftover log is still eligible and
  the next sweep finishes it.
- Restore from backup: restored files are judged by the same rule on the next sweep; a closed goal is
  re-pruned, a live or reopened one is untouched.
- Partial prune or a concurrent writer: size and mtime are re-checked just before each unlink; a writer
  that opens the file after the unlink recreates it holding only its own rows; a writer already holding
  it open can lose that one row in the microsecond window. Both concern an already-merged goal that
  was idle past the window, and a lost row only degrades (see readers), never breaks a gate.
- Lost sweep (record never ran): the next `done` record, or `prune-state`, catches up.

## Default and opt-out

Retention is on by default at 90 days, like the journal (30 days) and timing store (90 days) pruners it
sits beside, and only ever removes this repository's own aged, closed-as-done streams. It runs even
when `action_log.enabled` is false, since that setting gates writing, not the files already on disk.
`action_log.retention_days: false` turns it off. That the witness is read only while a goal is live is
from reading `witness.verdict` / `red_green.py` (consulted at verify, `work.py pr` and merge), not from
a dedicated test.

## Ceilings not removed

- A live goal's own stream is unbounded while the goal is live (witness max 159,216 bytes observed).
- Parked, failed and never-closed goals keep their streams until closed as done.
- A log with more than 8 KiB of agent rows after its `recorded` row is kept, not guessed at.
- Local-mode witness files, named from the raw goal path rather than the goal stem, are not covered.
- A fresh owner marker defers pruning by up to one window.

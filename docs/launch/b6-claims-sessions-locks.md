# B6 slice #464: session, claim and lock markers

Scope: the liveness markers under the repo-local, gitignored `.sdlc/state/`: `claims/`, `sessions/`,
`sessions/locks/`, the `*.lock` files and the phase-end stripe locks. Every path is written by Sigma
Python inside the project; none is under a host configuration root, and nothing here prunes one.
Sibling slices own the per-goal state records and agent markers (#458: `agents/`, verify, landing,
phase and the rest; not duplicated here), the action log (#457), events, time and ledger (#461),
review evidence (#459) and the Slack and supervisor logs (#460).

## Measured

Producing revision: origin/main 7eb25e6, plus this change. Real store means the operator's
long-lived checkout on 2026-10-03, four days of use (not a clean run).

- `claims/`: 82 files = 47 `.lock` + 35 `.claimed`, 0 to 6 bytes each (a `.lock` holds the pick
  CLI's pid), mtimes 2026-09-29 to 2026-10-02, about 20 files a day. Before this change every one
  survived: no production code deleted either kind (`loop.reclaim_stale_claim_lock` existed with no
  caller). 12 of the locks (goals 112, 194, 239, 317, 351, 353-356, 360-362) belong to goals with no
  `.claimed`, i.e. a pick that never reached work. 10x is about 200 a day, 73,000 a year; 100x about
  730,000. Bytes stay near zero; the cost is inode count and `claims/` directory size, which was NOT
  measured on a real filesystem at that count.
- `sessions/`: 4 `.active` entries (42 to 111 bytes), all four pids verified alive on this machine;
  `sessions/locks/`: 38 of a fixed 256; `phase-end-*.lock`: 124 of a fixed 256, 0 bytes each;
  `STATE.md.lock` and `merge-reconcile.lock` present once each. Not present on the measured
  checkout: `knowledge-sync.lock`, `feature-judge-spend.lock`, `slack-commands.lock`.
- Crash behaviour, real CLIs in a scratch repo: `loop.py start` under a long-lived pid, then
  `kill -9` of that pid: the `<pid>.active` file stays on disk, `session_active` reads it dead at
  once (it never reads alive), and the next `start` or `next` anywhere removes it. A pick CLI that
  won a claim lock and exited leaves the lock file with its dead pid stamped. A reboot is the same
  as `kill -9` for every holder: the kernel drops each `flock`, the files remain.
- Sweep cost, scratch, `liveness_prune.py sweep --dry-run --limit 0` (half the locks also with a
  40-day-old `.claimed`): 1,230 files 0.32 s, 12,300 files 0.57 s, 123,000 files 3.61 s; draining
  them with `--limit 0` took 0.42 s, 1.64 s and 15.4 s (about 0.12 ms per removal); a re-run on the
  emptied directory 0.26 s (module loading). In-line hook on 30,000 stale locks: 0.43 s, 200 removed
  per call. About 0.1 ms per file, linear in the number of files in `claims/`.
- Not measured: inode or directory-lookup cost at 10x or 100x on a real filesystem; behaviour on a
  network filesystem (`flock` semantics there are the filesystem's); Windows (no `fcntl`, see below);
  a crash in the middle of a phase-end critical section.

## Decision per family

| Family | Decision | Mechanism |
| --- | --- | --- |
| `claims/<goal>.lock`, `claims/slack-cmd-<name>.lock` | new bounded dead-owner sweep | `liveness_prune.sweep` |
| `claims/<goal>.claimed` | new 30-day age prune | `liveness_prune.sweep` |
| `sessions/<pid>[-<thread>].active` | already bounded; operator lever added | `loop._prune_dead_session_entries` (from `start` and `next`), `liveness_prune.py sweep` |
| `sessions/locks/<slot>.lock`, `phase-end-<stripe>.lock` | capped at 256 files each, 0 bytes | fixed hash stripes, never deleted |
| `STATE.md.lock`, `merge-reconcile.lock`, `knowledge-sync.lock`, `feature-judge-spend.lock` | capped at one file each | kernel `flock`, never deleted |
| `slack-commands.lock` | already bounded | `slack_commands_listen.acquire_single_instance` reclaim |

### The claim-lock sweep: what it deletes and what it never does

`liveness_prune.sweep` removes a `claims/<stem>.lock` only when ALL hold: a regular, non-symlink
file in a real `claims/` directory with a safe stem; mtime older than `max(claim lease TTL, 1 h)`
(`ledger.lease_ttl_seconds`, 12 h by default; a never-expire config falls back to 12 h, and the 1 h
floor keeps a tiny configured TTL from shrinking the window in which a lock could still be mid-
acquire); no alive or unknown `agents/<stem>/*.active` marker (`loop.agent_alive`, the same
primitive and fail-toward-keep rule #458 uses); the goal in no LIVE session's `in_flight` list
(refs compared by normalised stem); and the sweep WINS a non-blocking `flock` on the file, opened
`O_NOFOLLOW` and never `O_CREAT`, after which it re-checks by `lstat` that the locked inode is still
the one at the path and that the mtime is still old. A lock a live process holds fails the `flock`
and is never touched. Pid liveness is the existing `ledger.pid_alive` (via `agent_alive` and
`_session_pid_live`); the lock's own stamped pid is NOT used, because it is always the short-lived
pick CLI (`claim_lock_alive` documents why). Without `fcntl` the lock family refuses on stderr
("not supported on this platform", exit 2 from the lever) and removes nothing.

Hardening of the other side of the same seam: `_try_acquire_claim_lock` now checks, after winning
`flock` and before stamping its pid, that the locked inode is still the one at the path, retrying
(three tries) and then reporting contended. Without it an unlink between an acquirer's `open` and
`flock` would let the acquirer win an orphaned inode while a second acquirer creates a fresh file and
also wins. `reclaim_stale_claim_lock` (still without a production caller) is safe against that too
through the hardened acquirer. A cost to state: a sweep that holds the `flock` for an instant on an
OLD lock a picker is just re-picking makes that one pick read "contended" and skip the goal until its
next pick; latency, never data.

`claims/<stem>.claimed` is the durable "a claim was established on this machine" cache. It is removed
when older than 30 days (its mtime is its creation, never refreshed), when the goal has no work
record and no alive or unknown agent marker. Deleting it is safe: `loop._ensure_claimed` on a missing
marker re-reads the ledger, finds the existing claim, appends nothing and only re-touches (one
O(ledger) read, 31 ms mean over 1,591 entries as measured in that function's own docstring, at most
once per goal per 30 days); a test pins it. Ceiling: about 30 days of claims (about 600 files at the
measured rate, 6,000 at 10x), plus the markers of goals that still hold a work record, which live and
die with the worktree (`work.finish`, #458). A side effect, harmless: a later verb on an old goal
re-touches its marker, which restarts #457's fresh-owner window for that goal's log.

`--dry-run` does not probe the `flock`, so it can list a currently held lock as removable; a real run
keeps it. The scan runs in name order, not age order, and `--limit` takes the first N it removes.

Operator lever, any host: `python3 skills/sigma-loop/scripts/liveness_prune.py sweep .sdlc [--dry-run]
[--limit N]` (`--limit 0` removes everything eligible in one run; the default is 200 per run). In
line: the same sweep, capped at 200 removals and a 5 s budget that stops the scan as well, runs from
`loop.py start` and from `loop.py record ... done`; the lever also sweeps dead session entries.

### Session markers

Cited, not rebuilt: `loop._prune_dead_session_entries` removes an entry whose pid is dead, or whose
file is older than the lease TTL (the pid-reuse backstop), and temp files of dead writers, under that
entry's own stripe lock with a re-check, from `loop.py start` and from every `_next()`. A killed
session is therefore swept by the next session anywhere. The sweep is NOT added to `record done`:
`_session_pid_live` also reads a live pid with an entry older than the lease TTL as dead, so more
triggers would raise how often a quiet-but-live session older than 12 h is pruned by another session
(existing behaviour, not widened here). A machine that never starts another session keeps only dead,
inert entries (they read dead, never alive) until the operator lever runs: latency, not a bound.
Without `fcntl` the session prune no-ops too (`_session_locked(require_lock=True)` raises and the
prune swallows it).

### Locks that are capped, not pruned

`sessions/locks/<slot>.lock` and `phase-end-<stripe>.lock` are fixed hash stripes: at most 256 files
each, 0 bytes, so 256 inodes is the ceiling and creation is lazy. `STATE.md.lock`,
`merge-reconcile.lock`, `knowledge-sync.lock` and `feature-judge-spend.lock` are one file each. All
are kernel `flock` files that production code never deletes, on purpose: a deleted flock file
admits a second holder on a new inode (`_merge_reconcile_lock` says so in its own docstring). They
cannot read as held after their owner dies, because the kernel drops the lock with the process.
`tests/test_flock_markers_release_on_kill.py` runs the real acquire function in a subprocess for
each, asserts it reads held while the process lives and free the instant it is `SIGKILL`ed, and that a
fresh acquire succeeds with no sweep and no human. `slack-commands.lock` is a directory with its own
reclaim: `acquire_single_instance` reclaims it when the holder pid is dead, the heartbeat is stale, or
there is no pidfile past a 5 s grace window, and never inside that window (the #2396 double-win).

## Recovery, no human and no process pausing

- Crash during a sweep: a subset remains, every unlink is `missing_ok`, the next `start`, `done` or
  lever run finishes it.
- Reboot or `kill -9` of any holder: flocks die with the process; the file stays and is swept after
  the TTL if it is a claim lock, or reused as is if it is a stripe or singleton lock.
- Restore from backup: restored claim locks carry old mtimes of dead goals and are swept again; a
  restored lock whose holder is alive is kept by the flock; a restored `.claimed` of a goal that
  already has a ledger claim only costs one ledger read; a restored session entry of a dead pid is
  pruned by the next `start`.
- A lost trigger (no `done`, no `start` on a machine): costs latency only, the lever drains it.
- No `SIGSTOP`, no daemon, no background process is started by any of this.

## Controls run

Each guard was broken once and its test seen red, then restored (scratch edit, not committed):
age gate (both the scan check and the post-lock recheck), `.claimed` 30-day gate, live-holder
`flock`, agent-marker check, in-flight check, regular-file and `O_NOFOLLOW` checks, the sweeper's
inode check, `--limit`, the budget, the no-`fcntl` refusal, the dead-session filter, the acquirer's
inode check (both the unlinked and the swapped scenario), and each of the two hooks. For the capped
and existing families: removing the `flock` call from `state.phase_end_lock` turns the kill test's
"reads held while alive" assertion red; making `_session_pid_live` always true turns
`tests/test_loop.py::test_session_active_is_false_for_a_definitively_dead_pid` red; setting the
Slack reclaim grace to 0 turns two `acquire_single_instance` tests red. The planned tests are each
red against the pre-change code (missing module, unremoved markers, a double win on the orphaned
inode).

## Not covered here

- `state/heartbeat/<session>.json` (the loop's own heartbeat) is not one of this issue's patterns;
  not examined or pruned here.
- `claims/<goal>.claimed` and `.lock` of a goal whose stem is unsafe or non-canonical are never
  touched (kept as `not-owned`).
- `agents/` markers are #458's.

## Disposition and the audit gesture

`docs/launch/dispositions/464.json` covers all 18 patterns of the issue; none uses the `unscanned`
waiver, every pattern is produced by the scan. The documented gesture in `growth-audit.md` is run on a
scratch copy by `tests/test_liveness_disposition.py`, which asserts every pattern leaves
`b6_disposition.unresolved_patterns`.

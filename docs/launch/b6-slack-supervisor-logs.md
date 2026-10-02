# B6 #460: Slack listener and supervisor logs

Scope: the five patterns of issue #460. Revision measured: `12c7b26` (origin/main before this change). Resolved by
`docs/launch/dispositions/460.json`; the proof is `tests/test_log_rotation.py` and `tests/test_b6_460.py`.

## Ownership, from each writer

| Path under `.sdlc/state/` | Writer | Sigma-owned? | Decision |
| --- | --- | --- | --- |
| `slack-commands.log` | `slack_commands_listen._log` (scrubbed, one line per call, append) | yes, repo-local, never under a host config root | capped: `logroll` rotation |
| `supervisor.log` | `supervise_daemon._append` and the per-session copy of `supervisor.run.out` | yes, same | capped: `logroll` rotation, copy bounded |
| `supervisor.run.out` | opened `"wb"` per session as the session's stdout/stderr | yes | bounded by truncation, not cumulative |
| `supervisor.tail` | rewritten whole per session with the last 50 lines of `run.out` | yes | bounded by truncation, not cumulative |
| `slack-commands.launchd.log` | **launchd**, from the operator's own plist (`SLACK_COMMANDS.md`) | **no**: host service manager | intentionally unbounded by Sigma |

`ensure` starts the listener with `DEVNULL` streams, so only an operator-written launchd plist ever creates the last file.
Nothing here is under `~/.claude`, `~/.cursor` or `~/.codex`, and no code in this change touches a host configuration root.

## Measured

Command: a script loading the real `slack_commands_listen._log` and the real `supervise_daemon.main` (fake session of 41
lines, `SIGMA_SUPERVISE_SLEEP_SCALE=0`, `SIGMA_SUPERVISE_MAX_RUNS=3`) in a scratch `.sdlc`, at `12c7b26`.

- `slack-commands.log`, bytes per line: start 83, mention 119, ignored event 90, driven-session stdout line with 200 chars 288,
  with a 5,000-char input 4,089 (the existing 4,000-char tail cap holds). The heartbeat never logs.
- `supervisor.log`: 12,114 B over 3 sessions = about 4,038 B per session for a 3,863 B synthetic output; the non-session part is
  about 175 B (run marker, verdict, pause line).
- `supervisor.run.out` and `supervisor.tail`: 3,863 B each after 3 sessions, i.e. one session's worth, not three.

Not measured: lines per hour on a live listener (no live listener here; growth is one line per Slack event plus one per
dispatch); the size of a real `claude -p` session transcript (a real session was not run); the launchd file's real size; any
non-macOS filesystem block overhead. Windows rotation was not run (see Ceilings).

Scale, uncapped, linear in event rate: at about 100 B per line, 10x and 100x the event rate is 10x and 100x the bytes; the
supervisor copies a whole session per iteration, so its growth is the session size times sessions per hour (pause after a clean
relaunch observed at 120 s, other classifier paths 300 to 3,600 s; the operator can scale it to 0). After this change both
logs are bounded at `(3 + 1) x cap` regardless of rate.

## The cap and what it costs

`logroll.cap_bytes`: `min(1 MiB, max(free disk / 100, 64 KiB))`, derived from the machine like `watch.log` (#2499). Three
predecessors (`.1`..`.3`), so at most 4 MiB per log (plus what one write adds after the check). Cost per write: one `stat`. At 100x
the event rate the disk bound is unchanged and the retention window shrinks: 4 MiB is about 40,000 listener lines.

## Recovery (no human, host-agnostic, no process pausing)

- Rotation runs at write time inside Sigma's own Python, before the append: the line that triggers it lands in the fresh file. A
  writer that opened the old file before the rename writes into `.1`; one that opens after creates the new file. No path leaves a
  writer logging nowhere (`test_trigger_line_fresh`).
- Every move is one `os.replace` in one directory. A crash mid-shift leaves the live file in place (the next write rotates again)
  or already moved (the next write recreates it); the only loss is the oldest generation evicted one round early
  (`test_crash_mid_shift`).
- One rotator at a time in the ordinary case (two racing to take over the same stale lock can overlap once; the re-check bounds that to an early eviction of the oldest generation), through `<log>.rotating` (O_EXCL). A rotator that finds it held appends and moves on; a lock older than
  60 s is taken over, so a crash leaves nothing to clear. The size is re-checked under the lock, so a rival that already rotated is
  not rotated again (`test_rival_rechecked`).
- A failed roll never stops the writer (`OSError` or otherwise) and is retried on the next write.
- Restore from backup: restored files are plain logs; the next write rotates them if they are over the cap.
- Secrets: the listener's `_log` still scrubs before the line reaches the file; rotation moves bytes already written
  (`test_slack_log_rotates`).

## Ceilings, named

- **`supervisor.run.out`**: bounded by ONE session's output, which is written by the child directly and was not measured. It is
  replaced at the start of the next session. A single huge session can exceed the cap in this file only.
- **`supervisor.tail`**: 50 lines of the last session; each line's length is unbounded.
- **`supervisor.log` copy**: one session's output is copied in full if it fits the cap, else its last `cap` bytes behind a marker
  line, so one append can exceed the cap by one cap plus the marker before the next write rotates it.
- **`slack-commands.launchd.log`**: written by launchd, which holds the file open; Sigma neither rotates nor prunes it. It
  duplicates every line `_log` echoes to stderr, so its growth is the same event rate times about 100 B per line, plus any
  traceback, with no cap. **Who can reclaim it: the operator** who wrote the plist: unload the agent, delete or truncate the file,
  load it again; or point `StandardOutPath`/`StandardErrorPath` at `/dev/null`, since `slack-commands.log` carries every line
  `_log` writes and is rotated (a traceback printed before `_log` runs would then be lost, as with `ensure`'s `DEVNULL`).
- **Windows**: `os.replace` fails while another process holds the file open, so rotation can keep failing there; the failure is
  silent and retried each write. This is a documented limitation, not a guarantee.
- **Eviction**: a burst can push the oldest generation out in minutes; the logs are diagnostics, not a durable record.

## Gesture

`python3 tools/readiness/growth_audit.py . --measure-sdlc --b6-issue 419 --json docs/launch/growth-audit.json` (from
`growth-audit.md`) shows the five patterns out of `b6_disposition.unresolved_patterns`; `tests/test_b6_460.py::test_audit` runs
exactly that line in a scratch copy.

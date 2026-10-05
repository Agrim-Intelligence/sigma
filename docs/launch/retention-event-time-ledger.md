# Retention: event journal, time store, ledger stream files (B6, #461)

Host-agnostic. Everything below is Sigma's own Python and git; nothing needs a hook, a process
pause or a human. The machine-readable claims are `docs/launch/dispositions/461.json` (18 rows);
`tests/test_b6_461.py` runs each claim against the real write path.

## Ownership (from the writer, not from the path name)

Every path is under the project's `.sdlc/`. None is under a host configuration root (a home-directory
agent or editor config), so none is out of scope on that ground and none is touched by anything here
that reaches outside `.sdlc/`.

| Store | Writer | Owner | Reachable by git |
| --- | --- | --- | --- |
| `.sdlc/events/<actor>-<host>.<pid>.jsonl`, `.last-prune` | `ledger.append(stream=events)` | Sigma, the writing process | no (gitignored, local) |
| `.sdlc/ledger/entries/<actor>-<host>.<pid>.jsonl` | `ledger.append(stream=entries)`, staged by `sync.py` | Sigma, one file per writing process; the team via the ops branch | yes, tracked on the ops branch |
| `.sdlc/ledger/TEAM.md` | `sync.py`, regenerated from every entry | Sigma | yes, tracked |
| `.sdlc/ledger/events/` | nothing since #2574; read-only legacy | adopter history | yes, on older adopters |
| `.sdlc/state/time/<goal>/<pid>.jsonl`, `_sessions/<id>/...`, `.last-prune` | `timing_store.append`, `append_session` | Sigma, the writing process | no |

The scan's `<stream>` rows come from one dynamic helper (`ledger.entry_file`): `entries` lands in
`.sdlc/ledger/entries/`, `events` lands in `.sdlc/events/`. They span a capped store and an unbounded
one, which is why the four `.sdlc/ledger/<stream>` rows carry the entries-stream decision and name the
events half in their text.

## What the existing pruners do

| | `ledger.prune_journal` | `timing_store.prune` |
| --- | --- | --- |
| Scope | `.sdlc/events/*.jsonl` only | `.sdlc/state/time/<goal>/` and `_sessions/<id>/`, own `*.jsonl` only |
| Window | 30 days by file mtime | 90 days, per goal or session on its NEWEST file |
| Trigger | first `ledger.append` of each process, before any gate, behind a 24 h stamp | first `timing_store.append`/`append_session` of each process, behind a 24 h stamp |
| Never deletes | the writing process's own file; a link; anything outside `.sdlc/events/` | a goal whose newest `*.jsonl` is inside the window; a non-`*.jsonl` file; a link |
| Kind of cap | age, not size | age, not size |

Each process checks the stamp once, so a process that never restarts sweeps once at its first write and
then never again; the daily bound holds because loop, hook and script runs are short-lived processes.
"Capped" therefore means bounded by age. A process that keeps one file open and active is never swept,
and a store is bounded only by the rate of the last 30 or 90 days.

## Measurements

Revision: origin/main `46940d9` (the code that produces these stores), Darwin 25.6.0 / APFS, one
developer machine, warm cache. Real writers into a scratch `.sdlc` (command below), not hand-built
files, except where marked.

| Measured | Result |
| --- | --- |
| Bytes per journal event (`phase` 252, `gate` 225, `verify` 253, `spend` 241, `retro` 187, `park` 204) | mean 227 B |
| Bytes per ledger entry (`note` 180, `handoff` 183) | about 180 B |
| Bytes per time interval (goal `phase` 141, goal `verify` 126, session `turn` 137) | about 135 B |
| Sweep, every file stale, 2,830 / 28,300 / 283,000 journal files (1x / 10x / 100x), median of 3 | 329 ms / 3,383 ms / 44,611 ms |
| Sweep, every file stale, 2,830 / 28,300 / 283,000 time-store files in dirs of 10 | 399 ms / 4,129 ms / 44,013 ms |
| This machine's live time store (mixed revisions, so an observation, not a producing-revision figure) | 57 goals, 15 sessions, 2,828 files, 430,189 bytes (152 B per file), 11.4 MB on disk by `du`; 2,467 of the files belong to sessions, about 164 per session |

The cost is file count, not bytes: each writing process creates its own file, so a 150-byte interval
occupies a 4 KB block. 10x and 100x of the live store are 28,000 and 283,000 files, about 114 MB and
1.1 GB on disk. Sweep time is about linear in file count (13x for the last 10x here); one sweep over 283,000 stale files holds the
triggering append for about 45 s. That is a one-off backlog case: a store swept daily deletes about one
day of files per sweep. The `prune_journal` docstring's 23.6 ms for 2,591 files measures a directory the
sweep mostly keeps; the figures above delete everything and are the worst case.

**Projected, not measured:** the live store's files span about four days (2026-09-29 to 2026-10-03), about
710 files and 2.9 MB on disk per day on this heavily agent-driven machine. Held for the 90-day window that
is roughly 64,000 files and 260 MB. The journal has no live data on this checkout (journal off).

**Not measured:** a live adopter's ledger file count (the ledger is off on this checkout); events per goal
(the per-event figure above is the unit, a per-goal count would be an assumption); entry rate per
developer; a network mount or spinning disk; Linux or Windows sweep times; the full-repository growth audit
size columns for these rows, which the snapshot reports separately.

The measuring commands, run from a checkout of `46940d9`:

```sh
python3 - <<'PY'
import sys, tempfile, pathlib
sys.path.insert(0, "skills/sigma-loop/scripts")
import ledger, timing_store
d = pathlib.Path(tempfile.mkdtemp()) / ".sdlc"
cfg = {"journal": {"enabled": True}, "ledger": {"enabled": True}}
size = lambda p: sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
for kind, f in (("phase", dict(phase="P5 IMPLEMENT", state="end", ms=481000, tokens_in=52, tokens_out=35433, model="claude-sonnet-5-5")),
                ("gate", dict(gate="plan_review", verdict="pass", cycle=1, why="plan approved after one revision")),
                ("verify", dict(ok=True, exit=0, ms=190000, command_sha256="a" * 64))):
    b = size(d / "events") if (d / "events").exists() else 0
    ledger.append(d, cfg, kind, "461", stream=ledger.EVENTS, **f)
    print(kind, size(d / "events") - b, "bytes")
ledger.append(d, cfg, "note", "461", why="blocked on a sibling area's schema change")
print("entry", size(d / "ledger" / "entries"), "bytes")
timing_store.append(d, "461", "phase", "implement", 481000, started=1760000000000)
print("interval", size(d / "state" / "time" / "461"), "bytes")
PY
```

The sweep rows used the same modules: create N files with an old mtime (`os.utime`), then time one
`ledger.prune_journal(d)` or `timing_store.prune(d)`. The live-store row is
`find .sdlc/state/time -name '*.jsonl'` with `stat -f %z` summed, and `du -sk`.

## Decisions

1. **Event journal and time store: capped by the pruners above.** The audit rows are `capped`, each
   naming its pruner. `tests/test_b6_461.py` fails if the named pruner is renamed or neutered, if an
   aged file survives a write, or if a live owner's data is deleted: the writing process's own journal
   file, a goal with one recent interval, a foreign file in a goal directory.
2. **Ledger entries stream: intentionally unbounded; no pruner.** `.sdlc/ledger/entries/` is staged by
   `sync.py` into the ops-branch worktree and tracked by git. A local sweep would not reclaim anything
   shared: it would create staged deletions in a live worktree and in a teammate's next pull. The
   ceiling is one line of about 180 B per entry and one file per distinct writer process per actor, so
   growth is in file count and in the ops branch's history. It is not measured on a live adopter (see
   above); the per-entry figure is. At 10x and 100x the entry volume the bytes scale linearly and a
   pull moves proportionally more files. **Who can reclaim it:** a maintainer with push access to the
   ops branch, by a deliberate reviewed commit that deletes the files of writers that have ended.
   Sigma ships no automatic lever, deliberately. The legacy `.sdlc/ledger/events/` is the same case,
   and nothing writes it any more. `tests/test_b6_461.py` fails if the journal sweep, a ledger append or a timing
   append reaches `ledger/entries/`, `ledger/events/` or `TEAM.md`.
3. **`.sdlc/ledger/TEAM.md`: a derived view.** It is rewritten whole from the entries on each publish
   (25 recent rows plus open hand-offs), so it is not a store with its own growth and needs no pruner.

## Recovery (no human, no process pausing)

- **Crash mid-sweep.** Each file is unlinked on its own and the stamp is written only after the sweep, so
  a crash leaves some old files and no fresh stamp; the next process sweeps again and removes the rest.
  The sweeps are idempotent and never raise into a write.
- **Partial prune or a failed delete.** That file is skipped and retried at the next sweep after 24 h.
- **Lost or unreadable stamp.** At most one extra sweep. A stamp dated in the future (a corrected clock)
  does not suppress retention.
- **Restore from backup.** Restored files keep or get their own mtimes. Files older than the window are
  removed at the next sweep, which is the intended policy; files inside it survive. Nothing in the
  stores is needed to start: both create their directories on first write (the journal's sweep never
  creates `.sdlc/events/`).
- **Ledger entries after a lost local clone.** The ops branch on the remote holds the published
  entries; the ordinary sync pull brings them back. Sigma never deletes an entries file. Entries a
  machine had not yet published are lost with that machine; that is a delivery question for the
  ledger, not a retention one, and this slice does not change it.
- **Lost durations.** A goal or session idle for the window loses its recorded durations from the time
  store. Nothing else reads them as state; they feed time reports only.

## The committed audit snapshot

`docs/launch/growth-audit.json` is not regenerated by this slice, as with #456. Running the documented
gesture from `docs/launch/growth-audit.md` in the repository shows the 18 patterns of `461.json`
absent from `b6_disposition.unresolved_patterns` and their `store_measurements` rows carrying the
pruner and decision text; `tests/test_b6_461.py::test_audit` runs that gesture in a scratch copy.

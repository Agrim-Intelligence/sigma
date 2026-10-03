# Retention: fixed-name singletons, prose-only patterns and the home root (B6, #466)

Host-agnostic. Everything below is Sigma's own Python and git; nothing needs a hook, a process pause or a
human to keep running. The machine-readable claims are `docs/launch/dispositions/466.json` (26 rows);
`tests/test_b6_466.py` runs each claim against real files, the real writers and the real scan.

Measured at origin/main `d4a225a` on Python 3.9.6, Darwin 25.6.0 / APFS, one developer machine. Every figure
names the command that produced it; what was not measured is listed at the end.

## What changed, and what is only a decision

Three behaviour changes, all found by the control this slice added, plus a decision per remaining row.

1. `work.prune_terminal_review_copies` followed a linked `.sdlc/evidence` out of `.sdlc` and `rmtree`d a
   directory there (it checked only that the last component was not a link). It now requires the goal's
   evidence directory to resolve to exactly `.sdlc/evidence/<goal>` and prints one stderr line when it refuses.
2. `loop._prune_dead_session_entries` (reached from `loop.py start` and every pick) followed a linked
   `state/sessions` and deleted matching marker files in the directory it named, and created a lock directory
   there. It now does nothing when `state/sessions` or `state` is a link, as `liveness_prune` already did.
3. `setup_wizard.write_dismissed` keeps only names in `_MODES` and drops non-string entries (it used to
   raise out of `sorted`). The reader already ignored every other name, so behaviour is unchanged; the file is
   now at most 8 modes by code, where before only the skill prose bounded it.

Not changed, by decision (below): `inbox.md`, the three watch cursors and `embeddings.json`.

## Reconciliation with the scan

The documented gesture was run on a scratch copy: 82 unresolved patterns, all 26 of the issue's still
produced and unresolved, so **no row was dropped and none added**. The 82 are exactly the union of #459's 11
rows, #463's produced rows (two of its listed rows, `.sdlc/design/2289.md` and `.sdlc/evidence/<goal>/`, are no
longer unresolved) and this issue's 26: **no orphan rows**. No `unscanned` waiver is used; the scan produces every
pattern. `.sdlc/state/review-queue.md` is #459's row and is not claimed here: its writer appends a heading and two or three lines per
parked or failed goal and nothing trims it (not measured beyond reading the writer, `state._queue`).

## Ownership, from the writer

| Store | Writer | Owner | Under a host configuration root |
| --- | --- | --- | --- |
| `owner.json` | `coexist.write_owner`, atomic replace, only when the body differs | Sigma | no |
| `drift.meta.json`, `reconcile.meta.json` | `drift_watch._stamp`, `loop._reconcile_stamp` | Sigma | no |
| `kg-refresh.json`, `kg-warned.stamp` | `kg._write_refresh_record`, `kg.warn` (`touch`) | Sigma | no |
| `setup-wizard-cache.json`, `setup-wizard-dismissed.json` | `setup_wizard._write_cache`, `write_dismissed` | Sigma, the operator edits the second | no |
| `STATE.md` | `state._patch_cursor` under `STATE.md.lock`, atomic replace | Sigma | no |
| `autowatch-spend.json`, `feature-judge-spend.json` | `autowatch._record_spend`, `feature_judge._record_spend` | Sigma | no |
| `coexist.notice`, `ledger-delivery-attempt` | `touch` | Sigma | no |
| `slack-commands.pid`, `.heartbeat.json` | `slack_commands_listen` (written and removed by the listener) | Sigma | no |
| `slack-commands.stop` | the operator (`touch`, per `SLACK_COMMANDS.md`); the listener only reads it | the operator | no |
| `agent-watch-cursor.json`, `comment-watch-cursor.json`, `channel-notify-cursor.json` | `agent_watch`, `comment_watch`, `channel_notify` `_save_cursor` | Sigma | no |
| `inbox.md` | `watch.tick` appends; `loop._surface_inbox` reads and clears | Sigma | no |
| `embeddings.json` | `backlog_check._dense_channel` (opt-in) | Sigma | no |
| `.sdlc/config.json` | `verify_detect.write_verify`, `setup.write_cfg`, and the operator | the operator | no |
| `.sdlc/*`, `.sdlc/.sdlc/` | none: prose (a `.gitignore` rule; a warning in the wizard skill) | not a store | no |
| `.sdlc/state`, `.sdlc/state/` | none: rollups of the rows above and of the sibling slices' rows | not a store | no |
| `<home>/` | `tools/onboarding_control.py` (an isolated scratch HOME in a `tempfile.mkdtemp` root) | the tool, which removes it in a `finally` unless `--keep` | it is the opposite of one |

A path under a host configuration root is never pruned. The `<home>/` row is out of scope on that ground:
the scan names the control tool's scratch profile, and the real profile is only hashed read-only
(`real_profile_untouched`). The control below is the machine check that no Sigma pruner reaches outside `.sdlc`.

## Measured

| Measured | Result | Command or shape |
| --- | --- | --- |
| `embeddings.json`, 2,500 docs, 384 / 1536 / 3072 dimensions | 2,000 entries kept; 7.8 / 31 / 62 KB per entry; 15.6 / 62.3 / 124.6 MB; `json.loads` 87 / 341 / 687 ms | snippet A |
| `inbox.md`, 100 items in one batch | 196 to 227 B per item (19,824 B for 100 items, 2,273 B for 10, 195,525 B for 1,000) | snippet B |
| `inbox.md`, one item per tick, 100 ticks | 517 B per item (51,799 B); the 450 B header repeats | snippet B |
| a ledger entry for the same item | about 250 B | snippet B |
| `watch-cursor.json` (not a scan row, same code) | about 42 B per ledger item | snippet B |
| `agent-watch-cursor.json`, 1,000 signatures | 19 B per key (19,014 B) | snippet C |
| `channel-notify-cursor.json`, 1,000 ids | 45 B per key (44,904 B) | snippet C |
| `comment-watch-cursor.json`, 100 goals x 20 ids | 630 B per goal (63,000 B) | snippet C |
| `STATE.md` | 340 B after `start_run`; 340,314 B at the 4096 attempts cap; 19 s to get there | snippet D |
| spend files, 1,000 records | 91 B per record (autowatch), 67 B (feature judge) | snippet E |
| singletons | `owner.json` 53 B, `drift.meta.json` 58 B, `reconcile.meta.json` 62 B, `setup-wizard-cache.json` 56 B, `setup-wizard-dismissed.json` 201 B with all 8 modes, `kg-refresh.json` 565 B with a 500-character detail (the cut applies when the builder ran and failed) | snippet E |
| `.sdlc/config.json` | the scaffolded template is 76,934 B; this checkout's own is 1,106 B | snippet F, `wc -c` |
| a mistaken `scaffold(<repo>/.sdlc)` | 7 files, 83,798 B once; a repeat creates 0 | snippet F |
| `.sdlc` on this checkout | 1,057,688 KiB: `evidence/` 563,260, `work/` 469,056, `state/` 22,956, `plans/` 1,540 | `du -sk .sdlc/*` |
| `.sdlc/state` | 22,956 KiB; `time/` 13,424, `verify/` 4,048, `review-generations/` 2,148; 145 files and 19 directories directly under it | `du -sk .sdlc/state/*/`, `find .sdlc/state -maxdepth 1 -type f \| wc -l` |

The `.sdlc` figures are this machine's, taken on 2026-10-03 from the main checkout; they are not a trend.

Snippets, from the repository root (scratch directories, nothing committed):

```python
# A. embeddings cache through the real writer, fake embedder of dimension DIM
import sys, json, random, pathlib, tempfile; sys.path.insert(0, "skills/agrim-loop/scripts")
import backlog_check as b
d = pathlib.Path(tempfile.mkdtemp()) / ".sdlc"; (d / "state").mkdir(parents=True)
cfg = {"backlog_check": {"embed": {"enabled": True, "command": "x"}}}
docs = [{"ref": str(i), "raw": "doc %d" % i} for i in range(2500)]
b._dense_channel(d, cfg, docs, "0", lambda t: [random.random() for _ in range(DIM)])
p = d / "state" / "embeddings.json"; print(p.stat().st_size, len(json.loads(p.read_text())["vectors"]))
```

```python
# B. inbox and watch cursor through the real ledger and the real tick
import ledger, watch   # as above; actor "alice" writes, actor "me" ticks
other = {"ledger": {"enabled": True, "actor": "alice"}}; me = {"ledger": {"enabled": True, "actor": "me"}}
for i in range(100):
    ledger.append(d, other, "note", str(100 + i), to="me", issue=100 + i, priority="P2", ref="r%d" % i,
                  why="please review the hand-off for the shared schema change before friday")
    # for the batch shape, tick once after the loop; for the per-tick shape, tick here
    watch.tick(d, me, me="me")
```

```python
# C. cursors, their own writers on synthetic keys (agent_watch, channel_notify, comment_watch)
agent_watch._save_cursor(path, {"notified": sorted("%d:main:%d" % (1000 + i, 40000 + i) for i in range(1000))})
channel_notify._save_cursor(path, {"notified": {"alice:%d:%d" % (5000 + i, i): {"hop": 1, "attempts": 2} for i in range(1000)}})
comment_watch._save_cursor(path, {str(1000 + i): ["IC_kwDOAbCdEf%014d" % (j + i * 20) for j in range(20)] for i in range(100)})
```

```python
# D. STATE.md through the real writer until it refuses
state.start_run(d); t0 = state.load_cursor(d)["run_started_at"]
for i in range(4100): state.record_phase_end(d, "attempt-%d" % i, t0, budget_tokens=5, codex_raw_tokens=7)
```

```python
# E. spend files and singletons through their writers (autowatch._record_spend(d, 1000.0, 0.05, now),
#    feature_judge._record_spend(d, 0.01, now), drift_watch._stamp(d), loop._reconcile_stamp(d),
#    kg._write_refresh_record(d, {"ok": False, "ran": True, "detail": "x" * 500}),
#    setup_wizard._write_cache(d, True), setup_wizard.write_dismissed(d, list(setup_wizard._MODES)),
#    coexist.write_owner(d)), then stat the file under d / "state"
```

```python
# F. scaffold twice, then into its own .sdlc
import sdlc_init; sdlc_init.scaffold(str(root)); sdlc_init.scaffold(str(root / ".sdlc"))
```

## Scale: 10x and 100x

| Store | 10x | 100x | What binds |
| --- | --- | --- | --- |
| `embeddings.json` | unchanged at the cap: 15.6 to 124.6 MB | unchanged | the 2,000 entries cap; a corpus bigger than the cap re-embeds the overflow on every pick, and every pick parses the whole file (87 to 687 ms) |
| `inbox.md` | 10x the unread items, 10x the bytes: 517 KB per 1,000 unread items | 5 MB per 10,000 | nothing until a loop pick; `tick` rewrites the whole file each time (O(n) per tick), and `loop.py next` prints all of it to stderr, into the agent's context |
| the three cursors | 190 KB, 450 KB and 630 KB at 10,000 keys or 1,000 goals | 1.9, 4.5 and 6.3 MB | each is read and rewritten whole when it changes; a 6 MB rewrite every tick is the first thing a user would feel |
| `STATE.md` | 340 KB, flat | flat | the 4096 attempts cap, reset by every `start_run` |
| spend files | rate times window, flat in time | flat | the window |
| singletons and markers | flat | flat | one file each |

The 10x and 100x rows for the cursors and `inbox.md` multiply the measured per-key and per-item bytes; they
are extrapolations, not measurements. At 96 ticks a day the worst case for `inbox.md` is about 50 KB a day (517 B
times 96, derived).

## Decisions

- **Singletons and markers: capped.** Each is one file overwritten in place (a record, a watermark, a zero-byte
  `touch`) or pruned to a window on every write (the two spend files) or capped and reset (`STATE.md`:
  4096 attempts on each of three lists, refused loudly at the cap, reset by `start_run`). Tested through the real writers.
- **`embeddings.json`: capped at 2,000 entries**, evict oldest, one identity per file. The cap bounds entries,
  not bytes; the byte ceiling above depends on the embedder the operator chose. Raising or replacing the cap
  can force paid re-embeds, so it is not changed here: a dimension-derived byte ceiling is queued as #491.
- **`inbox.md`: intentionally unbounded between loop picks.** Ceiling: the items addressed to the operator since
  the last pick, 600 B per item at the worst measured shape (300 B per item in one batch), about 2x the ledger
  bytes those items occupy. Reclaim: `loop.py next` reads, prints and clears it, or delete the file (the cursor has
  already advanced, so deleted items do not resurface; the ledger stays the durable record). A hold-at-cap in
  `tick` was designed and withdrawn after the plan review: the cursor is saved before the inbox is written,
  a held backlog is later written as one unbounded batch, an undecodable byte can stop the clear, and the age of
  an unread inbox is visible only in `watch.log`. Bounded delivery is queued as #490.
- **The three cursors: intentionally unbounded, each with a stated ceiling.** `comment-watch` keys are goals
  with an open claim and `channel-notify` keys are ledger candidate ids, so both stay at or below what the
  ledger has held. `agent-watch` keys are `goal:thread:pid` taken from agent marker directories, not from ledger
  entries, so its ceiling is the number of confirmed agent deaths. Per key: 25 B, 60 B, and 800 B per goal
  (measured 19, 45 and 630). All three are opt-in. Reclaim: delete the file; each key then re-notifies once. The test
  checks bytes per key through each cursor's own writer on synthetic keys; **it does not prove the key set is
  bounded**, which rests on the code reading above. Retiring a key when its claim closes is part of #490.
- **`.sdlc/config.json`: capped, operator-owned.** It is the configuration; Sigma rewrites it in place and never
  prunes it.
- **`.sdlc/*`, `.sdlc/.sdlc/`, `.sdlc/state`, `.sdlc/state/`: prose and rollups**, no writer of their own. A mistaken
  `.sdlc/.sdlc/` costs 83,798 B once and a repeat creates nothing.

## The home root control

`tests/test_b6_466.py::test_no_pruner_acts_outside_the_project_sdlc` builds a scratch project whose `.sdlc`
reaches a decoy host configuration root only through links and records, at the exact points each pruner iterates, and
snapshots the decoy (type, size, mtime, inode, content, link target) before and after each pruner runs. The nine:
`retention.prune_closed_goal_streams`, `goal_state_prune.sweep`, `liveness_prune.sweep`,
`loop._prune_dead_session_entries`, `worktree_prune.sweep`, `logroll.rotate`,
`work.prune_terminal_review_copies`, `ledger.prune_journal`, `timing_store.prune`. Each also has a positive
control (an in-project target of the same shape is removed, or for `worktree_prune` examined and kept with a named
reason), so a pruner that quietly does nothing cannot pass. Run it exactly as written:

```sh
python3 -m pytest tests/test_b6_466.py::test_no_pruner_acts_outside_the_project_sdlc
```

Two of the nine were red against the code at `d4a225a` (review-copy cleanup, session markers) and are fixed above.

Controls, each broken once and seen red on that command (then restored):

| Broken | Red because |
| --- | --- |
| `retention._plain_dir` drops its link check | retention removed the decoy's log and witness |
| `goal_state_prune._real_dir` and `_regular` both drop their link checks (overlapping) | the decoy's `run_stop` marker went |
| `goal_state_prune` drops its `state` link check | same |
| `liveness_prune._real_dir` drops its link check; `liveness_prune` drops its `state` link check | the decoy's claim markers went |
| `liveness_prune._sweep_sessions` link check AND `loop._prune_dead_session_entries` guard dropped together (the first alone is masked by the second) | the decoy's session markers went |
| `worktree_prune._owned_path` drops its path equality | a record naming a host path was not refused |
| `logroll._move` resolves the path first (`os.replace(os.path.realpath(src), dst)`) | the decoy log was moved |
| `work.prune_terminal_review_copies` drops the new resolve check | the decoy's `wt` copy went |
| `ledger.prune_journal`'s root link check AND `_refuse_journal_links` dropped together | the decoy journal file went |
| `timing_store.prune`'s root, sessions and directory link checks AND `_refuse_links` dropped together | the decoy goal directories went |

Deleters outside the nine are not covered by this control: `work.finish` (`git worktree remove` first; `rmtree` of the
recorded path only when it is empty or missing, or an operator passes `--force`), `loop.agent_end` (a validated marker
directory), `loop.reclaim_stale_claim_lock` (no caller), `sync._prune`, `diff_revert.cleanup`, `feature_rebase._drop_worktree`,
`watch_daemon.rotate_log`, `kg.retain_web`, `rebase_brief.clear_context_snapshots`, and the temp-file cleanup inside atomic
writers. The write-surface inventory (`docs/launch/write-surface.json`, ratcheted by `tests/test_write_surface.py`) lists
every `unlink`, `rmdir`, `os.remove`, `os.replace` and `shutil.rmtree` site (67 rows), so a new deleter makes that test
fail until someone adds a row: it forces a reviewer to look, it does not prove containment. "Never a live, claimed or
successor owner's data" for each of the nine is pinned by that pruner's own test module
(`tests/test_state_retention.py`, `tests/test_goal_state_prune.py`, `tests/test_liveness_prune.py`,
`tests/test_worktree_prune.py`, `tests/test_log_rotation.py`, `tests/test_ledger_prune.py`, `tests/test_timing_store.py`,
and `tests/test_work.py` for review copies, whose only caller invokes it for a goal that has reached a terminal record).

## Recovery (no human needed, no process pausing)

- Every singleton is overwritten whole, by atomic replace where it matters (`owner.json`, `STATE.md`) or by a small
  `write_text` where a torn write reads as corrupt and is treated as absent: a corrupt watermark reads as due (one
  extra sweep), a corrupt cache runs the real check, a corrupt spend file is rewritten from empty on the next write
  (and the spend gate fails closed while it is unreadable). A crash leaves the previous or the next record, never a
  growing one.
- Restore from backup: a restored singleton is an older record of the same shape; the next write replaces it. A
  restored cursor re-notifies once per key it lacks and never loses one it holds; a restored `inbox.md` is the same
  file the loop clears on its next pick.
- A partial prune of a window-pruned file (the two spend files) is an uncommitted rewrite of one file: the next
  write prunes again. `STATE.md` refuses at its cap with a message to start a fresh session; `start_run` resets it.
- `inbox.md`: a crash between saving the cursor and writing the inbox loses that tick's items from the inbox
  (they stay in the ledger) and re-delivers nothing. This is the existing order and is part of #490.
- Operators who relocate `state` or `.sdlc/evidence` behind a link lose pruning of what lives there: the review-copy
  cleanup says so on stderr, the session-marker prune (like `liveness_prune`) returns silently. Session markers are
  safe to leave (a dead one is already excluded by liveness), review copies are disk only.
- The pruners the control covers each state their own recovery in their own slice's document; this slice changes
  only their refusals, which are stateless.

## Not measured

A live adopter's ledger, inbox or cursors; real embedding providers (synthetic floats were used, and the length of
a real vector's text varies); agent-watch, comment-watch and channel-notify through their real `tick` (their real
writers were run on synthetic keys); the spend files' real call rate; Linux, Windows and Python 3.10 to 3.13
timings; the `.sdlc/state/review-queue.md` size; `kg-refresh.json` when the could-not-run-the-builder detail (an OS error message, not cut) is long; the `work.finish` and `loop.agent_end` deleters under a linked parent.
Figures for 10x and 100x multiply measured per-key and per-item bytes.

## Disposition and the audit gesture

`docs/launch/dispositions/466.json` covers the 26 patterns the scan produces under these stores, one entry each,
with decision, ceiling, reclaim and evidence in its text. The committed `growth-audit.json` is not regenerated by
this slice, as with the sibling B6 slices. `test_documented_gesture_resolves_the_rows` copies the gesture out of
`docs/launch/growth-audit.md`, runs it on a scratch copy of the tree and asserts all 26 rows leave
`b6_disposition.unresolved_patterns` and carry their `decision` and `pruner_or_cap`.

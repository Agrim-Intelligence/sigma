# Retention: per-goal worktrees under .sdlc/work (B6, #465)

Host-agnostic. Everything below is Sigma's own Python and git; nothing needs a hook, a process pause
or a human to keep running. The machine-readable claims are `docs/launch/dispositions/465.json`;
`tests/test_worktree_prune.py` runs each guard against real throwaway git repositories and
`tests/test_worktree_disposition.py` runs the documented growth-audit gesture.

## Decision

A goal worktree is a full checkout (about 18.5 MB tracked in 903 files for this repository) and
nothing reclaimed one whose goal never reached `record done`. Now a **bounded, tested sweep**
(`skills/agrim-loop/scripts/worktree_prune.py`) removes only the checkout directory of a worktree that is
provably finished, and reports every other survivor, with the reason, for a human. It is **opt-in
when automatic** (`work.reclaim_merged_worktrees`, default `false`, because its proof costs GitHub REST
quota) and always available as an operator lever:

```sh
python3 skills/agrim-loop/scripts/worktree_prune.py list-removable .sdlc      # what it would remove, bytes, and why each other tree is kept
python3 skills/agrim-loop/scripts/worktree_prune.py sweep .sdlc [--dry-run] [--limit N] [--max-examine N] [--max-pr-reads N]
```

A default install therefore keeps its survivors until the operator runs the lever or sets
`"work": {"enabled": true, "reclaim_merged_worktrees": true}` (work must be on, as worktrees only exist then), which makes `loop.py start` and `loop.py record ... done`
run one bounded sweep (3 removals, 20 trees examined, 8 REST reads, 20 s, at most once per 15 minutes).

## Ownership (from the writer, not from the path name)

`work.start` is the only writer of `.sdlc/work/<goal>` (a `git worktree add`), described by
`.sdlc/state/work/<goal>.json`. Both are under the project's own `.sdlc/`; none is under a host
configuration root, so nothing here is out of scope on that ground. Worktrees outside
`<work.worktree_dir>/<goal>` on the same machine (an editor's or a scratch tool's) are not Sigma's:
the sweep refuses a recorded path that is not exactly `<project root>/<work.worktree_dir>/<goal>`.

## Measured

Producing revision: origin/main `3d5d09d`, one developer machine, Darwin 25.6.0 / APFS, 2026-10-03.
Commands: `git worktree list`, `du -sk .sdlc/work/*`, `git ls-files -z | xargs -0 stat -f %z`, `du -sk
.git/worktrees`, `git -C <tree> status --porcelain --ignored`, `gh api repos/{owner}/{repo}/pulls/<n>`,
`worktree_prune.py list-removable .sdlc` (dry run, nothing removed).

| Measured | Result |
| --- | --- |
| Worktrees under `.sdlc/work` | 14, 455 MB by `du` (17 to 63 MB each; a fresh one 20 MB) |
| One fresh checkout | 903 tracked files, 18,548,058 B content; git linkage `.git/worktrees/<id>` 112 KB (all 18 entries: 1,796 KB) |
| Growth | linear in surviving goals: 10x = 140 trees, about 4.5 GB; 100x = about 45 GB (extrapolated from the mean, not measured) |
| Survivors by cause (13 older ones) | 3 PRs merged and clean; 3 PRs closed unmerged; 1 PR open; 6 with no PR (2 unpushed commits, 2 uncommitted, 1 renamed `wip/..-paused`, 1 clean) |
| Dry run of the sweep on that store | 2 removable (112, 282: 93,197,643 B); 317 kept for `ignored-content` (a subagent scratch directory that is ignored but not regenerable); 144, 314, 331 kept `pr-closed-unmerged`; 397 `pr-open`; 264, 337 `no-pr`; 343 `dirty`; 346 `branch-mismatch`; 459 and 463 `live-agent`; this goal's `current` |
| Cost of that dry run | 5.6 s wall for 15 records, 7 REST reads |
| 100 scratch worktrees (small trees, fake `gh`) | dry run 7.9 s; `sweep --limit 0` removed 100 in about 12 s (about 0.1 s per tree); a re-run over 100 records whose trees are gone 0.25 s |
| `git worktree remove` of a fresh full checkout of this repository | 0.07 s |

Not measured: a worktree of a 100,000-file repository, Linux or network filesystems, Windows (the sweep
refuses without `fcntl`), how many survivors the loop produces per week (only the snapshot above), and
the REST quota used by an automatic sweep over a real month.

## What is removed, and the proof

Only the checkout directory, with a plain `git worktree remove` (never `--force`) and `git worktree
prune`. Never a branch, a remote ref, the work record, a ledger entry or a log row, and `work.finish` is
not called: the record is the only place a goal's PR number lives and a goal that survives with a merged
PR is one whose `record done` never ran. Every check below must hold; any unreadable or unprovable fact
keeps the tree and reports the reason:

1. a safe goal stem, not the caller's own goal (`not-owned`, `excluded`);
2. the recorded path is exactly `<project root>/<worktree_dir>/<stem>`, a real directory, not a symlink
   (`outside-work-root`; a missing directory is `worktree-missing`, left to `work.finish`);
3. the sweeping process is not inside it (`current`);
4. no `awaiting_merge` flag: the merge-reconcile pass owns that goal (`awaiting-merge`);
5. no alive or unknown agent marker and the goal in no live session's `in_flight` (`live-agent`,
   `live-session`), the agent markers AND the session list re-read just before removal. A host with no hooks (Cursor) leaves neither, so
   there liveness rests on checks 6 to 10, and a clean, merged tree with an editor open is removed: only
   its checkout is lost, never a commit;
6. `git rev-parse --show-toplevel` is the path (a tree with no `.git` file resolves to the MAIN
   repository, which would make every later check about the wrong repo: `not-a-worktree`) and the
   checked-out branch is the recorded branch (`branch-mismatch`: a detached HEAD or a `wip/<n>-paused`
   rename is work in progress);
7. no uncommitted or untracked file (`dirty`), no assume-unchanged or skip-worktree entry (they hide edits
   from `status` and from `git worktree remove`: `hidden-changes`), and every ignored file is regenerable
   (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `*.pyc`, `.DS_Store`, and
   `.sdlc/state/time/`), at most 5,000 listed (`ignored-content`: a `.env.local`, a database or notes are
   user data). Loss named: worktree-local `.sdlc/state/time/*` events (measured on the real survivors as
   test fixtures only; not merged anywhere; `work.finish` already discards them on every `done`) go with
   the tree;
8. the record has a numeric `pr` (`no-pr`: parked, abandoned and never-pushed goals);
9. one REST read per tree, at most `max_pr_reads` per sweep (the rest are `deferred`): positively merged
   (`pr-open`, `pr-closed-unmerged`, `pr-unknown` otherwise), head ref equal to the recorded branch and
   head repo equal to the base repo (`pr-mismatch`: a fork or a wrong repo);
10. `git merge-base --is-ancestor HEAD <PR head sha>` (right for squash and rebase merges, where the
    branch is not an ancestor of main): every commit in the tree was in the merged PR (`unpushed`,
    `unprovable`).

## Recovery, with no human needed

- **One sweep at a time**: a non-blocking `flock` on `state/worktree-prune.lock` (a singleton file, never
  deleted: deleting a flock file admits a second holder; cap one file, 0 bytes). Held: `busy`, nothing
  removed or healed, so a heal can never race a live removal.
- **Crash or failure mid-removal**: a journal `state/worktree-prune/<goal>.json` is written before git
  runs and removed when the removal succeeded or git refused before deleting anything. If a sweep dies
  (or a removal fails midway) the journal stays and the next sweep heals (a changed work record or head
  drops the journal as untrusted, and a half-deleted tree then stays `dirty` for a human; that case needs
  a person and is the one place this recovery does not run unattended): `git worktree prune`; tracked
  files deleted by the interrupted removal are restored from the journalled HEAD **at most once** (then
  `heal-gave-up`, left for a human, so recovery is never a recurring I/O storm on a tree too big to
  remove within its bound); debris whose `.git` file is gone is removed only when every remaining entry is
  regenerable or byte-identical to the blob at that path in the journalled HEAD (the registration and
  record re-check immediately before the delete is a race guard with no deterministic test) (never `check-ignore`:
  inside a tree with no `.git` it reads the main repo's `.sdlc/*` rule and calls everything ignored),
  otherwise `debris-unverified` (including a tree whose `.git` file is still present but whose admin
  entry is gone: `.git` is untracked, so that one waits for a human). A journal is a hint, never an authority: it acts only while the record,
  branch, path and HEAD still match; a goal re-run after the crash has a new record and the old journal is
  dropped. The global `git worktree prune` that heal and every removal run also drops other trees'
  admin entries whose directory is temporarily missing (an unmounted volume): `work.finish` already does
  the same.
- **Lost trigger**: costs latency only; a surviving tree is inert disk. **Restore from backup**: old trees
  whose record and PR still prove merged are removed again; anything dirty or unproven is kept.
- **Reboot**: the flock dies with the process; journals are healed by the next sweep.
- **Bounds**: `limit` removals, `max_examine` trees, `max_pr_reads` REST reads and a wall-clock budget
  per sweep; each `git` or `gh` call has a timeout (`SIGMA_WATCH_CALL_TIMEOUT`, default 120 s, capped by
  the budget left; a removal has its own 60 s bound, so a removal started with little budget left can
  run up to 60 s past the 20 s hook budget). A hung call keeps the tree.

## Ceilings (numbers)

- Examination rotates: candidates are examined least-recently-examined first
  (`state/worktree-prune-seen.json`, one number per goal), so permanent survivors (open or
  closed-unmerged PR, no PR, unpushed) never starve a newer removable tree: every tree is examined within
  `ceil(N / 20)` automatic sweeps. A PR read is never spent on a tree already kept by a local check.
- Per automatic sweep: at most 8 REST reads and about 20 x 4 git calls, at most once per 15 minutes; a
  backlog drains at 3 trees per trigger and at once with `--limit 0 --max-examine 0`.
- Residual windows: between the pre-removal re-read and git's own cleanliness check a session can start
  work in the tree: git refuses a dirty tree, a commit it made is on the branch (nothing deletes a
  branch), and `work.start` re-attaches the branch, so the harm is a vanished checkout. An IGNORED file
  created inside that window (a new `.env.local`) is deleted with the tree, because git does not count
  ignored files as dirt; the window is the few milliseconds between the re-read and git's own check. A shell or editor
  of another process with its cwd inside a removed tree is not detected (check 3 covers only the sweeping
  process); the tree was clean and merged.
- The record stays, naming a directory that is gone, until a `record done` or `work.py finish` clears it.
  Readers: `work.finish` and the merge landing read fall back to the project root; `work.start` takes its
  documented vanished-directory path and clears `pr` (so a `start` before a `record done` loses the stored
  PR number); `pr`, `rebase`, `commit` and `merge` use the recorded directory and raise on a swept goal
  (merged, so unreachable by the loop); `feature_rebase` lists it as skipped and per-goal state pruning
  treats the record as an owner, so a swept goal keeps its per-goal state until the record clears. That
  is the same as a survivor today; a reclaim-time `done` is filed as a follow-up.
- `state/worktree-prune.lock` (1 file), `state/worktree-prune-seen.json` (one number per goal with a
  record, pruned of goals with none) and `state/worktree-prune/<goal>.json` (only during a removal) are
  the sweep's own state; none is under a host configuration root.

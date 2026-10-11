# #1142 retro (P7, advisory)

## Intent vs shipped
- **Intent** (acceptance/1142.md):
  - A single-branch clone's bootstrap joins the remote ops branch.
  - `init` never forks while the remote has it.
  - `publish` and `pull` say "no shared history" as a permanent state, not "will retry".
  - A lever re-roots an orphaned clone without losing entries.
  - The doctor flags divergence.
  - A regression test simulates the narrow refspec.
- **Shipped:** all of it. `tests/test_ledger_single_branch.py` has 37 real-git tests. 35 single-guard mutations, each seen red (the scratch mutator is in the PR description). The docs gesture (`sync.py bootstrap .sdlc`, as a CLI) is red on pre-fix `sync.py` and green after, and the field upgrade path (pre-fix orphan, fixed `pull`, `DIVERGED`, `bootstrap`) works end to end.
- **Beyond the issue, all found in review:**
  - Recording the pushed tip: without it, a narrow-refspec clone read every published commit as unpushed.
  - `merge_observation.resolve_remote_snapshot` gets the same explicit refspec; it would otherwise pin a stale commit.
  - URL or path remotes and glob branch names are refused by name.
  - A one-shot retry for two first-ever bootstraps racing.

## What the reviews caught that the author did not
- **Plan review, round 1:**
  - The union-everything re-root could wedge every later rebase.
  - The shallow-file path was wrong inside a linked worktree.
  - URL remotes would break the refspec.
  - A seventh bare fetch, in `merge_observation`.
  - Actor drift.
- **Plan review, round 2:** "union what changed" both republished purged lines and left supersets dirty. The fix was to split by OWNER (host token).
- **Code review, round 1:**
  - A stale tracking ref after a push (a regression the fix itself introduced).
  - A re-root interrupted after its reset could not resume.
  - The checkout overwrote uncommitted copies.
  - "NOT published" files still reached TEAM.md.
  - Ten guards had no test that could fail.
- **Code review, round 2:**
  - Recording HEAD after the push could call a concurrent commit published, a false all-clear. Now
    HEAD is read before the push and recorded only if it did not move.
  - Resume depended on the backup ref.
  - Resume waited on the network.
  - Legacy filename ownership disagreed with `files_for`.
- **Changed for the repo's own branch-deletion guard:** the first fixes used a variable-source push
  refspec and `update-ref -d`. Rather than widen the §13b inventory, the push keeps the fixed
  `HEAD:` source, and the pending marker is a file holding the old tip's hash.

## Residual debt
- The ledger `publish` does not re-push an unpushed commit until a new entry is written, so "will retry next tick" is only true when something new arrives. This predates #1142; it is filed as a separate task. It also reaches #1142's lever: a `bootstrap` run offline commits its publish, and the next online `bootstrap` says "nothing to publish" until an entry is written. The resume test writes one and says why.
- When a concurrent commit lands during a push, the tracking ref is not recorded. The clone over-reports unpushed commits until the next fetch. That is the safe direction by design.
- The notes channel has no host token, so its re-root republishes every local note (through the secret gate). The refusal message warns, and the docs say so.
- Other modules' fetches (work.py, feature_rebase.py, drift_watch.py) fetch the integration branch bare. A single-branch clone normally tracks that branch, so this was not changed. A clone tracking a different single branch would hit the same blindness there.
- The re-root is operator-run by design. An orphaned clone stays `refused`/`DIVERGED` until someone runs `bootstrap`.
- `refs/sigma/orphaned/*` backups and `.sdlc/state/orphaned/*` copies are never pruned. Re-roots are rare and each keeps one backup, so growth is bounded by how often an operator runs the lever.
- The full suite was not run locally: no interpreter here has pytest-xdist, and a serial run of 12 files took 27 min. The related suites were run, and CI runs the full matrix.

## Proposed lessons
1. A fix that makes a missing ref exist can make a STALE ref exist. Any ref this code creates must be kept current on every path that moves its target (here, a push).
2. In tests, two `commit-tree` calls on the empty tree with the same identity, message and second are the SAME commit. Give each simulated clone its own identity, or the "fork" under test is not a fork.
3. A recovery that splits files by "did my merge change it" gets both directions wrong. Split by owner, then merge only what you own.

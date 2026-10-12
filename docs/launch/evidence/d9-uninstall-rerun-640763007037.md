# D9 evidence: uninstall rerun after the guide gained branch and plugin-cache steps (2026-10-13)

Follows `d9-upgrade-uninstall-rollback-f627d06ad3f6.md`, section 2, which found that `docs/uninstall.md`
steps 1 to 6 left the local `sdlc/*` and ledger branches and the plugin cache directory behind
(`tools/readiness/leftovers.py` exit 1). Sets no score: scores are the owner's.

Decision: the guide moves, not the checker. A branch or cache that survives uninstall is residue the
operator should choose to keep, not expected state, so `leftovers.py` stays strict. The guide now has
step 5 (delete local `sdlc/*`, `feature/*` and ledger branches, with a list-first safety note) and
step 6 (remove the plugin cache directory, with a note that the cache is shared across repositories).
The old steps 6 and 7 are now 8 and 9. No automated uninstall step exists in the repository; none was added.

Base commit: `640763007037` (the guide before this change). Platform: macOS, one machine.

## Reproduction (before)

The pre-change guide has no `sh` block containing `git branch -D` or `plugins/cache`
(`test_pre_change_guide_had_no_branch_or_cache_block`, which reads the guide from `64076300:docs/uninstall.md`).
So the documented gesture cannot clear those rows; the earlier evidence page shows the same with a
full adoption (exit 1, three rows left after steps 1 to 6).

## Rerun (after), scratch repo, the two documented blocks copied out of the guide

Scratch repo with branches `sdlc/goal-1`, `feature/demo` and the ledger branch, and a fake profile
holding `plugins/cache/sigmaloop/sigmaloop`, selected by `CLAUDE_CONFIG_DIR`. Global and system git
config disabled. Nothing ran against this repository or the real profile.

| Point | `leftovers.py` exit | Rows |
|---|---|---|
| Before any step | 1 | 2 `installed-plugin`, 3 `local-branch` |
| After the step 5 block | 1 | 2 `installed-plugin` |
| After the step 6 block | 0 | none; `git branch` shows only `main` |

## Controls

Run with `$HOME/.sigma-venv312/bin/python -m pytest tests/test_readiness_leftovers.py` (12 passed).
The new `test_documented_branch_and_plugin_cache_steps_leave_nothing_behind` extracts the two blocks from
the guide and runs them. Each clause of the guide was broken once:

| Break in `docs/uninstall.md` | Result |
|---|---|
| step 6 path changed to a different directory | test red (final check exit 1) |
| step 5 `git branch -D` changed to `-d` (refuses unmerged) | test red (branches left) |
| as shipped | green |

The test also asserts a branch that is not Sigma's (`keep-me`) survives the step 5 block.

## What is NOT measured

- The full adoption flow was not rerun end to end; only the two new blocks on a scratch repo.
  Steps 1 to 4 and 7 to 9 are as in the earlier run.
- The real `claude plugin uninstall` was not rerun, so the cache path
  `plugins/cache/sigmaloop` under the profile is taken from the earlier run's checker output, not
  re-observed here. A host release that changes the cache layout would turn step 6 into a no-op and
  `leftovers.py` would say so by naming the real path.
- Linux, Codex homes, and a branch checked out in a still-registered worktree (git refuses the delete;
  step 3 comes first for that reason, but that ordering was not exercised).
- Step 6 deletes a cache shared by other repositories; the guide says to skip it then, which is
  advice, not enforced.

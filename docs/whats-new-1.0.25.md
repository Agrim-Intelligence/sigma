# What is new in 1.0.25

This page lists every change in this release. The same entry is in the `1.0.25` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.24](#upgrading-from-1024) if you only want to know what
changes for you.

Four changes apply everywhere: a goal's planned tests now run with the project's own Python, a failed delete of a merged goal branch is announced and leftovers are swept, a branch-protection read that fails no longer reads as "not protected", and a goal's own `verify_command` no longer silently voids the one-time verify approval. The rest of this release is about `/sigma-goal <N> --land` in a Claude Code cloud container: what happens when its merge
runner is killed, how long it waits for a slow base CI, what it leaves behind when the container goes, and a setup
script for the environment. 
## Upgrading from 1.0.24

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **If you set `auto_merge: "always"`,** a merge now stops when Sigma cannot read branch protection, instead of
   merging with a WARNING. The stop line names the source and the failure kind. Re-run `work.py merge` once the token
   can read both the classic protection API and the base's rulesets. The CHANGELOG entry gives the three-line change
   that narrows the stop to `protected` only.
3. **If you use `SIGMA_TRUSTED_VERIFY_SHA256`** and a goal records its own `verify_command`, `acceptance.py record` now
   refuses a command the approval cannot cover, before it writes anything, and names the fix.
   `acceptance.py set-verify-command` clears or replaces the command while no verify evidence exists.
4. **Planned tests use the project's Python (#2079).** `loop.py red`, verify's planned-test proof, the flake pass and the
   kill control use the goal worktree's `.venv` if it has a `pyvenv.cfg`, else build one once with `uv run --frozen`
   for a uv project (under the exact conditions in `docs/configuration.md`), else `python3` as before. A `.venv` whose
   interpreter is missing or cannot import pytest now **refuses** (`loop.py red` exits 2, verify records no green)
   instead of falling back to `python3`. The environment approval does not cover the uv build's project metadata or
   fetched packages.
5. **Failed branch deletes (#1682).** A failed remote delete of a goal branch now prints a line and writes a `gate`
   entry. `work.py finish` also retries the delete of other merged `sdlc/*` leftovers, pinned to the merged head, and
   `work.py branches <dir> [--sweep]` lists or sweeps them. In a cloud session, where the proxy rejects deletion, the
   line names the branch and head for a sweep from a host that can delete.
6. **Cloud runs:** the pending-check wait is the new `work.merge_pending_wait_s` (default 450 s, range 45 to 7200 s).
7. Nothing else changes by default, and your `.sdlc/config.json` is never rewritten by an update.

## Cloud `--land`

- **A killed merge runner is explained (#2086).** When `wait-call ... merge` finds the runner gone, it reads the PR once
  and prints its state and the recovery: for an open, unmerged PR, `loop.py agent-start ... --pid "$PPID"` and then
  `work.py merge`. A merged PR points at `record done`; a closed or unreadable one gives no re-run advice. The detail is
  in `skills/sigma-loop/references/killed-merge.md`. Binding the marker to a session id instead of a pid is not built.
- **A longer, configurable pending-check wait (#2083).** `work.merge_pending_wait_s` replaces the fixed 450 s, and
  `/sigma-doctor` shows it. A check still pending after the wait is armed through the cloud route only when every
  pending check is required by the base; anything else parks with one instruction.
- **The reviewed plan survives the container (#2087).** When `record done` finds a GitHub-issue goal's plan untracked
  and absent from the base, `work.py finish` posts it as a comment on the goal's issue, with secret-shaped strings
  redacted, and prints the URL. `/sigma-doctor` warns when git ignores `.sdlc/plans/` and cloud runs look configured.
  The issue comment is a provisional choice for the owner to ratify.
- **`tools/cloud/` (#2090).** The setup script and operator steps for `sigma-cloud`, the environment Sigma's own goals
  run in.

## Cloud `--land` on a repository whose ruleset blocks every PR

- **Opt-in merge through the account's ruleset bypass (#2092).** New setting `work.merge_bypass: "ruleset"`, default
  off: any other value, case or spacing leaves today's park unchanged. With it on, `work.py merge` may merge a PR that
  GitHub reports `BLOCKED` when four conditions hold, each read over REST: every required check passed on the exact
  head; every rule on the base comes from a ruleset the merging account can always bypass, with no classic protection;
  the review gate passed; and the branch has no conflict and is not behind. Anything it cannot read parks and names the
  failed condition. It never bypasses classic branch protection, a failing or missing check, a ruleset the account
  cannot always bypass, or a merge queue, and it records the bypass in the merge evidence and a PR comment. Nothing
  sets the key for you. **Not measured:** whether GitHub's REST merge honours a ruleset bypass for any token, the cloud
  proxy's included; the real `sigma-cloud` run that would show it (the issue's AC-5) is still open.

## Everywhere

- **The project's Python for planned tests (#2079).** See item 4 above. Not measured: a real `uv` build, a real cloud
  container, two processes racing on one worktree, Windows (uv builds are refused there).
- **Failed goal-branch deletes are announced, leftovers are swept (#1682).** See item 5 above. Each sweep delete is
  pinned to the head it listed and skips any branch whose goal has a worktree on this machine. Not measured: a real
  GitHub, a token without `contents:write`, a secondary rate limit, a real cloud proxy. Sweeping branches Sigma did
  not cut is not built.

- **An unreadable protection answer is not "not protected" (#984).** `protection()` now separates a requirement, an
  answer of nothing and an unreadable source. Only two answers of nothing report "nothing is enforced".
- **A goal's own `verify_command` and the verify approval (#2085).** See item 3 above. Verify and red lead with the
  fix for a command the approval does not cover.

**Not measured:** a real Claude Code cloud container, a real worker restart, and a real GitHub comment at the size
limit; maintain-level and fine-grained tokens for #984. Each CHANGELOG entry lists its own gaps.

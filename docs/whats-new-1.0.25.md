# What is new in 1.0.25

This page lists every change in this release. The same entry is in the `1.0.25` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.24](#upgrading-from-1024) if you only want to know what
changes for you.

Most of this release is about `/sigma-goal <N> --land` in a Claude Code cloud container: what happens when its merge
runner is killed, how long it waits for a slow base CI, what it leaves behind when the container goes, and a setup
script for the environment. Two changes apply everywhere: a branch-protection read that fails no longer reads as "not
protected", and a goal's own `verify_command` no longer silently voids the one-time verify approval.

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
4. **Cloud runs:** the pending-check wait is the new `work.merge_pending_wait_s` (default 450 s, range 45 to 7200 s).
5. Nothing else changes by default, and your `.sdlc/config.json` is never rewritten by an update.

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

## Everywhere

- **An unreadable protection answer is not "not protected" (#984).** `protection()` now separates a requirement, an
  answer of nothing and an unreadable source. Only two answers of nothing report "nothing is enforced".
- **A goal's own `verify_command` and the verify approval (#2085).** See item 3 above. Verify and red lead with the
  fix for a command the approval does not cover.

**Not measured:** a real Claude Code cloud container, a real worker restart, and a real GitHub comment at the size
limit; maintain-level and fine-grained tokens for #984. Each CHANGELOG entry lists its own gaps.

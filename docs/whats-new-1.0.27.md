# What is new in 1.0.27

This page lists every change in this release. The same entries are in the `1.0.27` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.26](#upgrading-from-1026) if you only want to know what
changes for you.

All four changes come from the first real cloud `/sigma-goal N --land` wave on a consumer repository. Nothing here adds a
config key you must set or a CLI verb.

## Upgrading from 1.0.26

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **`--land` can take `--no-tests` by itself (#2116).** When the planned tests have no runner that can record red in this
   container (no pytest selectors, an unconfigured runner, or a runner the active trust does not cover), `work.py pr`
   proceeds with a reason that names the case, and the PR body carries it. The post-PR review must accept or reject it;
   a rejection is an ordinary block. A malformed `## Tests` now fails Plan-Review (when `verify.enforce` is on) with the
   parser's own message, and Implement runs `loop.py red` right after writing the failing tests. Outside `--land`
   nothing changes.
3. **Markers survive a container restart (#2117).** An agent marker records the process start time and the boot id
   beside the pid, and is alive only when all three match. Where Linux `/proc` cannot be read, the old pid probe stays.
   A push-refusal marker is cleared once the rebase it described is gone. Markers written before this release keep
   refusing until HEAD returns to where they were written.
4. **A resume replays only the goal's own commits (#2118).** A commit the new base already has is not replayed. A rebase
   that still conflicts falls back to a merge from the base before the goal parks, and the park reason names the
   conflicting files. A resume never force-pushes a branch that is already pushed.
5. **A cloud burst has a guide and a readiness check (#2119).** `skills/sigma-goal/references/cloud-burst.md` states the
   launch prompt (only `/sigma-goal N --land` plus facts) and four per-repository checks. `/sigma-doctor` adds two
   informational rows, never a failure, and makes no new network call.

## Not measured

A real Claude Code cloud container, a real container restart, a real in-progress rebase and a real reviewer following the
`--no-tests` acceptance line. Each CHANGELOG entry lists its own gaps.

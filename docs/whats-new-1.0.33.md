# What is new in 1.0.33

This page lists every change in this release. The same entry is in the `1.0.33` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.32](#upgrading-from-1032) if you only want to know what
changes for you.

No new skill. One default changes: follow-ups now join their goal as slices.

## Upgrading from 1.0.32

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **Acceptance criteria are append-only (#2222).** A goal's `## Done when` (or `## Acceptance criteria`) is recorded by
   `acceptance.py record` with a `criteria_sha256`. Later slices append with `acceptance.py extend`, in the open;
   editing, reordering or removing a recorded criterion is refused. Records made before 1.0.33 have no hash and pass as
   legacy. It is tamper-evident, not tamper-proof.
   - **Not measured:** a run against a real GitHub goal end to end. Open debt is listed in the CHANGELOG entry.
3. **Closing a goal releases its dependents (#2223).** `release_dependents.py` removes the stale `sdlc:blocked` label
   from goals that were waiting on the closed one, with or without a dependency map. The `/sigma-orchestrate` wave-end
   release is a documented gesture, not an automatic trigger.
4. **Follow-ups join their goal as slices (#2224).** `followups.mode` defaults to `in-goal`. A separate goal is filed
   only for a different feature or once the goal has `followups.max_added_slices` (default 10) added slices.
   - **What you do:** nothing, to take the new default. Set `followups.mode: file` to keep one new goal per follow-up.
     An unknown value reads `in-goal` and prints one line naming it.
   - **Not measured:** the GitHub comment `extend` posts for a GitHub goal, and a real decompose through its exemption.
5. **Tests that failed in some containers are fixed at their cause (#2225, #2228).** No skip, ceiling or known flake was
   added. Two resource-lease tests that failed on a clean main in some containers are fixed; that they pass in a second
   container was not shown.

## Known, not fixed

- A `--land` resume carries no red evidence, so new-symbol tests need the `--no-tests` exception.
- `work.py start` fails from a detached HEAD in a fresh container.
- `red_green.selectors()` caps a plan at 25 `## Tests` bullets.

## Not measured

A real GitHub round trip of the append-only criteria and the in-goal follow-up path. The CHANGELOG entries list their
own gaps.

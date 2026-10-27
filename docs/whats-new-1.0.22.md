# What is new in 1.0.22

This page lists every change in this release. The same entry is in the `1.0.22` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.21](#upgrading-from-1021) if you only want to know what
changes for you.

This is a one-fix release. 1.0.21 shipped with the phase-context budget red on `main`; 1.0.22 turns it green again
without changing a behaviour.

## Upgrading from 1.0.21

Restart sessions, and stop a running watcher so it picks up the new code: `touch .sdlc/state/watch.stop`, then remove
the file and start the watcher again. Nothing else changes: no configuration key, command or default moved, and your
`.sdlc/config.json` is never rewritten by an update.

## The phase-context budget (#2075)

`/sigma-goal --land`'s contract and picker detail had been copied into `skills/sigma-goal/SKILL.md` and the loop's
`references/picking.md`, which the orchestrator and goal-slot contexts load by default. That pushed both instruction
bills over their committed ceilings, and the `regression-control` check refused the red baseline.

The detail now lives in `skills/sigma-goal/references/land.md`, which only a `--land` run reads; each of the two files
keeps a one-line pointer. The ceilings are unchanged. Measured: goal-slot is 12436 of 12437 tokens and orchestrator
32386 of 32422.

Two tests that had been red on `main` since 1.0.21 are fixed as well: the context-trigger doc test read the wrong column
of the run-modes table once `--land` added a column, and a link in a plan file used the wrong relative path. A review file that named a retired internal prefix is reworded. No shipped
behaviour changed.

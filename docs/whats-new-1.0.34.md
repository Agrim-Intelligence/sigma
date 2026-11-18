# What is new in 1.0.34

This page lists every change in this release. The same entry is in the `1.0.34` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.33](#upgrading-from-1033) if you only want to know what
changes for you.

No new skill and no changed default. One new opt-in flag, and one corrected sentence.

## Upgrading from 1.0.33

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **`/sigma-init --antigravity` (#2239).** Opt-in. It writes the managed `AGENTS.md` block only, the same block
   `--codex` writes, and skips the file if it already exists. It changes no config and adds no `.agents/skills` link or
   copy. Antigravity reads `AGENTS.md` and `.agents/skills` according to its documentation (read 17 November 2026).
   - **What you do:** nothing, unless you use Antigravity and want the block.
   - **Not measured:** nothing was run in Antigravity. Hooks, MCP, workflows, headless `agy` and subagents there are
     unmeasured.
3. **Corrected Cursor wording (#2239).** Cursor documents a hooks system. Sigma does not use it, and no Sigma hook is
   load-bearing there. That Sigma works in Cursor's CLI is still unmeasured: Cursor stays unverified until a live test.

## Not measured

The full test suite was not run for this release. Nothing was run in Antigravity, and Cursor is unverified until a live
test.

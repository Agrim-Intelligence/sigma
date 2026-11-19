# What is new in 1.0.37

This page lists every change in this release. The same entries are in the `1.0.37` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.36](#upgrading-from-1036) if you only want to know what
changes for you.

The full CI tier passes, host pages say plainly that Sigma works with all four hosts, and docs match the code.

## Upgrading from 1.0.36

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **Host pages state support plainly (#2265).** The site, the README and the `/sigma-init --antigravity` message say
   Sigma works with Claude Code, Codex, Cursor and Antigravity, with the install command and init flag for each.
   `docs/platform-support.md` is a per-host setup page.
3. **Docs and the config template match the code (#2259).** The config template puts `chain.max_members` and
   `gh_quota.rest_reserve_percent` at the top level, where the code reads them.
   - **What you do:** if you copied either key under another block from the old template, move it to the top level.
4. **The full CI tier finishes (#2255, #2267).** The whole suite runs as 6 shards per leg with a 300 s limit per test
   and a failure summary, and every shard passes on Ubuntu (Python 3.10 to 3.13) and macOS (Python 3.12). The
   quarantine list is empty.
5. **The nightly onboarding control passes again (#2260).** Nothing changes for you.

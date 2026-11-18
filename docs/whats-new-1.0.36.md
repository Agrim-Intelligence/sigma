# What is new in 1.0.36

This page lists every change in this release. The same entries are in the `1.0.36` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.35](#upgrading-from-1035) if you only want to know what
changes for you.

A Cursor plugin package, the real public repository name in the install commands, and a larger public docs site.

## Upgrading from 1.0.35

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **The install commands name the real repository (#2248).** The README, the issue template, the onboarding docs, the
   doctor's marketplace fallback and the launch definition now name `Agrim-Intelligence/sigma`; the earlier slug did
   not exist. A test holds every README repository URL to it.
   - **What you do:** if you copied an install command from an older README, use the current one.
3. **A Cursor plugin package, `.cursor-plugin/` (#2237).** A plugin manifest, a marketplace file, an empty hooks file
   and manifest tests, contributed by a teammate. Its version equals the Claude manifest's.
   - **Not measured:** the package was never installed or run in Cursor by this release.
4. **A larger docs site (#2244).** A one-host-at-a-time Get started, an "On this page" list on every page, a phone
   menu, and ten new pages (Your first goal; Upgrading and uninstalling; Troubleshooting and FAQ; All settings;
   Parallel and scheduled runs; The project board; Dependencies and chains; Org policy and security; Autonomy and the
   rulebook; Quality and design aids). Cursor and Antigravity facts follow what was measured on 18 Nov 2026: Cursor
   setup, doctor and one real goal to a merged PR; the Antigravity plugin installs from the public URL (46 skills and
   4 hook groups converted) and `/sigma-init --antigravity` ran inside `agy`.
   - **Not measured:** an Antigravity goal, hooks firing, a headless run and the reviewer; the commands on the new
     pages were not run live.
5. **`/sigma-init --antigravity` no longer calls Antigravity experimental, and the research and plan skill text is
   back under its size ceilings (#2252).**

## Not measured

The full test suite has not passed in CI for this release: the on-demand full tier is cancelled at its 90-minute limit
on every leg (#2255). Only the smoke tier and the required checks ran. The Cursor package was never installed or run
in Cursor. In Antigravity, a goal, hooks, a headless run and the reviewer were not measured. The docs pages' commands
were not run live.

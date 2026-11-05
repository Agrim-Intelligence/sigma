# What is new in 1.0.28

This page lists every change in this release. The same entries are in the `1.0.28` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.27](#upgrading-from-1027) if you only want to know what
changes for you.

Both changes are fixes. Nothing here adds a config key you must set or a CLI verb.

## Upgrading from 1.0.27

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **A blocked goal is freed once its explicit blockers close (#2139).** The auto-unpark sweep holds a goal that carries
   `sdlc:blocked` and names at least one explicit blocker on its explicit findings only, so a weak word such as `needs`
   or `after` beside an unrelated open issue no longer keeps the label on. A goal that names only weak references
   behaves as before, and the parked path is unchanged. The audit comment names only the references that really closed.
3. **A chain asks about a blocked member instead of stopping `claimed` (#2139).** The plan checks, read-only, whether
   the sweep would lift a blocked member that has no open recorded wait. If it would, the forecast shows `ready now`
   and nothing is asked. If it would not, the sheet asks once, up front: release it into the chain, or keep it blocked.
   A cycle that freed a member is `running` and names it, a skipped sweep is forced once per member per run, and a root
   held only by the label stops with reason `decision`, naming `/sigma-unpark`. The forecast and the run print the same
   stop line.
4. **A commit-signing outage is reported once, in plain words (#2135).** When a commit fails because the signer is
   unavailable, `work.py commit` returns a park message with the signer's own last line, makes no commit and exits 4.
   The park is classified as infrastructure. A failure to write the object store is not mistaken for a signing outage.

## Not measured

A live repository with a real `gh` for the blocked-member check, the real cloud signer, Windows git, and a real ssh or
gpg agent. Each CHANGELOG entry lists its own gaps. A goal held only by prose the sweep does not read is still not freed
(tracked separately as the sweep running only on a pick).

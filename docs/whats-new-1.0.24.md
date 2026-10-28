# What is new in 1.0.24

This page lists every change in this release. The same entry is in the `1.0.24` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.23](#upgrading-from-1023) if you only want to know what
changes for you.

This is a one-change release about the team ledger. 1.0.23 let a cloud run publish its own claim, keep-alive and
outcome. This release closes the gaps found in that publish path: two publishers in one checkout could abort each
other's rebase, an entry recorded outside `loop.py main` was never published, and the container id moved with
`TMPDIR`.

## Upgrading from 1.0.23

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **If you use the team ledger (`ledger.enabled`) from a cloud container,** expect a one-time change of the host
   token. A container whose id file was in a `TMPDIR` other than `/tmp` reads its own earlier claim as another
   machine's until the lease TTL (default 12 h) lapses, or until `python3 loop.py release <sdlc_dir> <goal>` ends it.
3. **On a platform without `flock` (Windows, or a filesystem that does not support it),** the ledger publish verbs now
   refuse and name the lever. `SIGMA_LEDGER_ALLOW_UNSERIALISED=1` lets them publish unserialised instead.
4. Nothing else changes by default, and your `.sdlc/config.json` is never rewritten by an update.

## The ledger publish path after #2076 (#2081)

- **One publisher at a time per checkout.** `publish`, `pull`, `publish_receipt`, the re-root and `publish_entry` take
  one kernel `flock`, `.sdlc/state/ledger-sync.lock`, so a second publisher no longer aborts the first one's live
  rebase. The lock is never written through a symlink. A rebase git cannot abort is reported as NOT published instead
  of being published over. **Cost:** a waiter queues behind tick callers that have no deadline of their own, up to 90 s.
- **An exit flush.** Entries recorded outside `loop.py main` (`work.py start`'s stale resume, `/sigma-scope` assign,
  the liveness-prune CLI) are published as the process exits. It runs on a normal exit and on SIGINT. SIGTERM, SIGHUP,
  SIGKILL and `os._exit` run no exit hook, so a torn-down container leaves the entry on disk for the next flush or
  watcher tick.
- **The container id ignores `TMPDIR` (POSIX).** The id file lives in `/tmp`, then `/var/tmp`. The file is read only if
  it is a regular file of this user holding 32 hex digits.
- **The retry pause obeys the deadline.** `publish_entry` no longer sleeps a jittered pause past its own deadline.

**Measured:** offline, real git, a local bare origin, macOS, each guard seen failing before its fix. **Not measured:**
a real Claude Code cloud container, `flock` on its filesystem, a real remote, Windows. Known residuals are listed in
the CHANGELOG entry. See [cloud sessions](cloud-sessions.md).

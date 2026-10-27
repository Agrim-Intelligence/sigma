# What is new in 1.0.23

This page lists every change in this release. The same entry is in the `1.0.23` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.22](#upgrading-from-1022) if you only want to know what
changes for you.

This is a one-change release about the team ledger and cloud runs. `/sigma-goal <N> --land` now runs in parallel in
short-lived Claude Code cloud containers, one issue each, all under one GitHub login. Until now such a run's claim,
keep-alive and outcome reached the shared ledger only by accident, and a teammate's laptop could release a claim that
was still live.

## Upgrading from 1.0.22

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **If you use the team ledger (`ledger.enabled`),** update every machine and cloud environment that writes to it.
   The ledger contract is now 1.5.0 (a MINOR bump, approved on #393) and `lease-renew` is its twelfth entry kind. A
   consumer that validates entries against 1.4 must accept 1.5 before a writer on 1.5 reaches its ledger. An older
   Sigma ignores `lease-renew` and keeps reaping on the old clock.
3. Nothing else changes by default, and your `.sdlc/config.json` is never rewritten by an update.

## The team ledger from a cloud container (#2076)

- **Publishing without a daemon.** With `ledger.enabled`, the loop publishes its own entries at the claim, at each
  lease renewal and at the outcome (`done`, `parked`, `failed`, the awaiting-merge hold, `release`). A fresh container
  with no `.sdlc/ledger` worktree gets one from origin through `sync.py init`, kept out of the code checkout's status.
  A refused or timed-out push is printed as not published, and the run continues.
- **A keep-alive.** While a run holds a claim, `note`, `agent-start`, `agent-beat` and `verify` write a `lease-renew`
  entry at most once per a third of the lease TTL. The reader extends a lease only for a renewal by the holder: the same
  actor, the same machine and a compatible run. This closes #616.
- **A writer identity per container.** In a cloud session the host token is salted with an id that is the container's
  own, so two containers sharing a hostname and a pid are two writers. A laptop's token is unchanged.
- **A reclaim needs origin's word.** A stale claim written on another machine is released only after a fresh,
  read-only fetch of origin's ledger branch still shows it expired. A failed read releases nothing.
- **Retries under contention.** Real-git publishes pause with full-jitter backoff. Measured offline against a local bare
  origin: 16 clones publishing 3 rounds at once land every entry; the old retry lost 25 to 27 of 48.

**Not measured:** no controlled run has pushed the ledger branch from a real Claude Code cloud container. Provisional
defaults to review: the container id lives in the temp directory, the publish deadline is 90 seconds, and a cloud
session starts no watcher. See [cloud sessions](cloud-sessions.md#the-team-ledger-from-a-cloud-container-2076).

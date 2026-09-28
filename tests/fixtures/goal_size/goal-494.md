**Unassigned, no `sdlc:goal`** — Sigma Core. Assign to pick it up.

Found live on 2026-08-07: a session **stood down and refused to work the backlog**, correctly, because it could not tell whether another process held a claim.

> `309.lock` has sat unrefreshed at 13:58:09 while the real worker advanced the goal from an uncommitted tree to an open PR. Nothing in the state files separates "claimed and abandoned" from "claimed and working" — and `--skip` is orchestrator-local, so it structurally cannot help across processes.

## The defect

`.sdlc/state/claims/<goal>.lock` files are **empty**. Verified:

```
308.lock (11:34:43)  0 bytes
309.lock (13:58:09)  0 bytes
310.lock (15:20:25)  0 bytes
```

They carry no PID, no host, no heartbeat — only an mtime, which is **not refreshed while work proceeds**. #309 went from an uncommitted worktree to an open PR (#493) while its lock's mtime stayed frozen at 13:58.

So a reader has two indistinguishable interpretations of an old mtime:

* a worker died an hour ago and abandoned the claim, **or**
* a worker is alive and simply has not touched the lock.

## Why it matters

The safe response to that ambiguity is to stand down — which is what the session did, correctly. But it means **a single crashed session can idle the backlog indefinitely**, and no amount of supervisor relaunching helps: every fresh session sees the same ambiguous lock and makes the same correct decision.

In this instance all three locks were orphaned (verified: no chain worker process existed at all), and clearing them by hand was the only way forward.

## Suggested shape

Write liveness into the lock and refresh it: PID + hostname + a heartbeat timestamp updated at each phase transition. A reader can then check whether the PID is alive on this host, and treat a heartbeat older than some multiple of the phase interval as dead. Failing that, even PID-alone would resolve the same-host case, which is the common one.

Worth a test that a lock whose PID is not running is treated as reclaimable, and one whose PID **is** running is not.

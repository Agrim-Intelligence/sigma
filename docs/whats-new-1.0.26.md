# What is new in 1.0.26

This page lists every change in this release. The same entries are in the `1.0.26` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.25](#upgrading-from-1025) if you only want to know what
changes for you.

All five changes are in the resource guard that decides how many workers a goal's verify gets, and in what an agent does
when the guard holds a verify. Nothing here adds a config key you must set, a CLI verb or a contract-vocabulary change.

## Upgrading from 1.0.25

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **A cold verify now gets more than one worker (#2108).** With no `verify` observation yet (every Claude Code cloud
   session's first verify), the guard used to admit one worker, so doctor's advice, `-n {workers}`, ran the suite on one.
   It now grants the room above the reserve divided by a cold per-worker estimate, at most the cpu-derived count and
   `resource.max_verify_workers`. The estimate is `resource.verify_cold_mb`, or one eighth of total memory per worker when
   unset. Set `resource.verify_cold_mb` higher if your tests use more memory per worker. A too-low estimate is caught
   only by the shed, which needs three critical samples. **Not measured:** the real per-worker memory of any suite on a
   real cloud container; one eighth is an uncalibrated ratio.
3. **A verify held for a literal `-n N` keeps its learned cost (#2106).** Before, a hold wrote the caller's own few MiB into
   the cost ring, so after two holds the held count ran anyway. Now a hold discards its lease and writes nothing.
4. **A verify that holds mid-goal has an instruction (#2109).** On `loop.py verify` exit 5 with `RESOURCE-HOLD` on stderr,
   the agent waits (`resource_lease.py wait --kind verify --max <retry_after, else 540>`), verifies again, never edits
   code for it and never counts it as a review cycle, and parks after 6 holds in a row. Exit 5 without that prefix
   still means `UNVERIFIED`. **The bound of 6 is provisional**, not derived from a measured hold. Not measured: an agent
   following the instruction end to end.
5. **Sigma's own suite honours the worker grant (#2107).** `tests/conftest.py` takes the smaller of its memory answer and
   `PYTEST_XDIST_AUTO_NUM_WORKERS` for `-n auto`. This matters only if you run Sigma's own tests under the guard.
   The same release sets `sigma-cloud`'s verify to two workers and names failing tests last in pytest's summary (#2090).

## Not measured

A real Claude Code cloud container, a real two-worker run under the guard, and any change in the 2h16m one-worker run
time the issues measured. Each CHANGELOG entry lists its own gaps.

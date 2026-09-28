**Not assigned and deliberately carries no `sdlc:goal`** — this is Sigma Core, and the Dashboard loop is scoped away from it by assignee. Assign it to pick it up.

## The defect

`gate()` retries only while `mergeable == "UNKNOWN"` (work.py:288). It does **not** wait for required *checks* to report. Any non-CLEAN `mergeStateStatus` falls through to work.py:319-323 and returns `False`, which the caller turns into a **park**.

Measured on this repo:

| | |
|---|---|
| retry budget | `UNKNOWN_ATTEMPTS=4`, `UNKNOWN_BACKOFF=3` → **21s** total |
| CI duration | **~240-300s** |

The gate can never outlast a normal CI run. A PR that is merely *waiting* is indistinguishable, to this code path, from one that is *failing*.

## Why it is P0 for unattended running

Parking **strips `sdlc:goal`**, so the goal leaves the queue permanently and needs a human to re-label it. A transient 4-minute wait becomes a dead goal until someone notices. Observed **3 times in 11 goals (~27%)** on the Dashboard backlog today (#295, #304, #306). Overnight and unattended, that is most of a run.

## It already has the information it needs

work.py:320 computes `failing` and then discards the distinction:

```python
failing = [c for c in statusCheckRollup if (conclusion or state) not in _CHECK_OK]
detail  = f" — failing: {...}" if any(failing) else ""
return False, f"not safe to merge (mergeStateStatus={status}){detail}", data
```

`BLOCKED` + **empty** `failing` means *nothing has reported yet*. `BLOCKED` + non-empty means *something is red*. Only the second deserves a park.

#306's own park comment reached exactly this diagnosis unaided:

> `mergeStateStatus=BLOCKED` with NO failing check named, across four consecutive reads, which means required checks have not all reported rather than one being red.

It then parked anyway, because it had no way to wait. Read from an interactive session minutes later the same PR was `CLEAN`, all five contexts green.

## Suggested shape

Keep failing-closed. Add a **separate, longer** budget for the pending case only — retry while `status == BLOCKED and not failing and some check is still pending`, capped well above CI duration (10-15 min), and park unchanged the moment any check actually fails. Do not widen the existing UNKNOWN budget; that one is about lazy mergeability computation and 21s is right for it.

Worth a test that pins the distinction: a rollup with all-pending checks must retry, and a rollup with one FAILURE must park immediately without burning the budget.

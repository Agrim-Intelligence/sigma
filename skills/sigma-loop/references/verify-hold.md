# A verify that holds mid-goal (`RESOURCE-HOLD`)

The one instruction for `loop.py verify` when it ends in exit 5 with `RESOURCE-HOLD` on stderr, shared by `/sigma-loop`
step 6 ([landing](landing.md)) and `/sigma-goal --land` ([land](../../sigma-goal/references/land.md)). The
resource guard can hold a verify at any point in a goal, not only at the pick: it ran nothing and wrote no
evidence, because the machine has no room for it yet (details: [parallelism](../../../docs/parallelism.md)).
It is a wait, not a result.

**Telling it apart.** Exit 5 is shared. Read stderr: a line starting `RESOURCE-HOLD exit=5 -- ... retry_after=<s>`
is the hold. Exit 5 without the prefix keeps its `UNVERIFIED` meaning (a flaky suite, evidence written) and goes
the way it always did; nothing here applies to it.

**On a hold:**

1. Wait. `<s>` is retry_after, else 540: the seconds the line printed as `retry_after`, else 540:

   ```
   python3 "${CLAUDE_SKILL_DIR}/scripts/resource_lease.py" wait --kind verify --max <s>
   ```

   It exits 0 when a verify would be admitted and 10 when the time ran out; either way go to step 2.
2. Run `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" verify .sdlc "$goal"` again, as one foreground call
   ([landing](landing.md)). The suite has not run yet, so this is the same verify, not a retry of a failure.
3. Count the holds of this goal's verify in a row. A verify that runs (any exit but a hold) resets the count.

**Never**, for a hold: never edit code for it (nothing failed), never park it as flaky, never retry without the
wait. A hold is not a verdict: never call `post-review` for it, and never count it against
`work.max_review_cycles`, which counts `block` verdicts only. If the held verify was a re-verify after a review fix, the cycle already spent stays
spent and no further one is taken.

**The bound is 6 holds in a row.** At the sixth, park, naming the count and the last `retry_after`:

```
python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" record .sdlc "$goal" parked "verify held 6 times in a row (RESOURCE-HOLD, last retry_after=<s>s): no evidence, the goal is not failed; re-run when the machine has room" --qkind no_evidence
```

Under `/sigma-goal --land` that is the ending **parked**. The bound is PROVISIONAL (owner to ratify), chosen so
a run waits about three quarters of an hour at most (6 waits of up to 540 s) before it gives the machine back; it
is not derived from a measured hold duration, and no real hold, wait or park was observed end to end.

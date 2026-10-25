# sigma-plan-review: dispositions after a FIX-FIRST

A verdict that sends the plan back is only half the gate. When the revised plan returns, **give every
finding an explicit disposition** — otherwise the loop either swallows findings silently or obeys a
wrong one. Each disposition needs its own `file:line`; "the review seems right" is not evidence.

- **Accept** — the finding is real. Confirm it in the code first, then change the plan and cite what
  confirmed it.
- **Reject** — the finding is wrong or reads stale code. Confirm the *plan* was right, keep it
  unchanged, and record why with evidence.
- **Partially accept** — the concern is real but the suggested fix isn't. Adapt it; cite both the
  concern and why the adjusted approach is better.

**The review can also be wrong.** It is a hypothesis, as the plan was in SKILL.md §1. A finding that
claims a file or function doesn't exist gets checked against the filesystem before it is accepted —
a plan patched to satisfy a false finding is worse than the
original.

**Regen threshold.** If more than half the findings are substantive (not surface corrections like a
stale line number or a renamed symbol), the plan has structural problems — say so and recommend
regenerating it from the goal rather than patching.

Carry the dispositions with the revised plan so the next review round starts from what was settled. When a later round contradicts an earlier decision, re-read the code, pick one direction on
the evidence, and record the reversal.

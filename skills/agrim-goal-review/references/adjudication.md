# Assembling the brief and adjudicating doubts and blockers

The detail behind step 2 of [`../SKILL.md`](../SKILL.md): dispatching a fresh, maker-blind
reviewer, the three-bucket adjudication (2a), the CONFIRM/REJECT verdict rule (2b), and how open
items are carried forward into the tickets (2c).

---

## 2. Assemble the independent brief and dispatch a fresh reviewer

**The maker is never the checker.** Whoever ran `goal-design` against `<n>` must not be the one
confirming it here — dispatch a genuinely fresh reviewer with no shared context, the same
maker≠checker rule `agrim-plan-review`/`agrim-review` already enforce one phase later in the ordinary
tech loop.

**Resolve the mechanism before assembling the brief** — independence is a property of how this
confirmation is RUN, not a claim this skill can make about itself:

```
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/reviewer.py" resolve .sdlc
```

Branch on its `mechanism` exactly as `agrim-review` does (`subagent` → hand a FRESH subagent only the
brief; `process`/`command` → use `reviewer.py run` below; `inline` → no mechanism on this machine,
so say so in the verdict). If the resolved process fails, review inline and name the failed route
and cause in the verdict. Generate:

```
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/review_context.py" brief .sdlc <n> --for goal-review --artifact <n> > /tmp/brief-<n>.md
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/reviewer.py" check /tmp/brief-<n>.md --scratch "$(pwd)/.sdlc/work/<n>"
```

`<n>` is passed as BOTH the goal and the artifact deliberately — at this point in the pipeline there
is no separate Epic issue yet (that's this skill's own OUTPUT, step 4 below), so the story/epic
issue under review and the goal the change serves are the same ticket. `review_context.py` fetches
and inlines that issue's title+body under "## What you are reviewing" (mirroring its own `_parent()`
block — an issue is fetched and shown, never left as a bare pointer) and states plainly that no
branch or code exists yet.

For `process`/`command` only, execute the checked brief. `run` checks the exact text it sends,
enforces `review.timeout_seconds`, stops children on timeout, and rejects non-zero or empty output:

```
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/reviewer.py" run .sdlc /tmp/brief-<n>.md --scratch "$(pwd)/.sdlc/work/<n>"
```

Hand the fresh reviewer **only** this brief, plus `.sdlc/design/<n>.md`'s own path (it is not
inlined by the brief — the reviewer has full repo read access and opens it directly, the same way
`plan-review`'s own brief points at `.sdlc/plans/` rather than inlining a whole plan). Ask for the
adjudication below, and then for one of two verdicts.

### 2a. Adjudicate every doubt and blocker — three buckets, in writing

`goal-design` §3 REQUIRES the design to surface every doubt and every blocker it has; a `full`-mode
pass that lists none has almost certainly not looked hard enough. **So "the design still has open
questions" is the normal, healthy Stage 1 output, not a defect** — and it must not be the thing that
leaves Stage 2 unable to name a verdict (#1921). Before naming one, walk the design's own Doubts and
Blockers lists and place **every item** in exactly one of three buckets, in writing, **naming each
by its id** (`D-3`, `B-1`). `goal-design` §5 mandates those ids precisely so this step no longer has
to **invent** them — a pass that mints its own numbers produces an adjudication two runs would
number differently and a comment nobody can match back to the design (#1955). An artifact whose
Doubts or Blockers carry no ids is a schema violation: report it in the verdict rather than
numbering the list yourself.


- **resolved** — the design answers it, and the answer holds against the real repo.
- **open, not blocking** — genuinely undecided, but every slice can still be scoped and started
  correctly whichever way it is later decided. A scope question about a later slice, a naming
  choice, a "we may also want X" — this is the common case, and it is not a reason to reject.
- **blocking** — no slice can be correctly scoped or started until it is answered. The test is
  operational, not a feeling: **name the slice that cannot start, and say what it is waiting for.**
  If you cannot name one, the item is not blocking.

**Two lists beyond Doubts and Blockers go through these same three buckets, by id** (#1975, #1976).
Every `X-n` under `## Out of scope`, and every `PC-n` marked `unverifiable` under `## Premise
check` — both are judgements the design made and offered up for disagreement, and a list nobody
adjudicates is a list nobody wrote. Name each by its own id, exactly as you name a `D-3`; never
renumber and never fold one into a Doubt's number. A `PC-n` marked **falsified** is not bucketed at
all — it is settled, and §1 already says what to check about it instead.

### 2b. The verdict

- **CONFIRM** — the codebase mapping holds up against the real repo, the blast radius and
  components are accurate, the slice-count estimate is plausible, and **nothing was adjudicated
  blocking**. Open-but-not-blocking items do NOT bar CONFIRM; they are carried, not dropped (2c).
- **REJECT**, with concrete findings (`file:line` where applicable) — the mapping is wrong, stale or
  incomplete, or at least one item was adjudicated blocking. Name which item, and the slice it stops.

**Why there is no third verdict, and why that is not a dodge.** The verdict's only mechanical
consequence is the `sdlc:designed` overlay, and that overlay is a PRESENCE check: `loop.py`'s design
gate asks `"sdlc:designed" in names` and nothing else, and `sources.mark_designed` is its only
writer — there is no third value for it to hold. A "CONFIRM-ish" third verdict would still have to
either write the label (in which case it IS CONFIRM) or not (in which case it IS REJECT); the third
state would live in prose while the pipeline treated it as one of the two anyway, which is exactly
how an open question gets silently dropped. So the three-way judgement is put where it can actually
be read — an explicit adjudication, carried onto the tickets by 2c — and the verdict stays binary
because the label is.

### 2c. Carrying the open items

A CONFIRM that leaves items open is only honest if those items survive the confirmation:

- **Name every open item in step 4d's outcome comment**, one line each, **by id**, with the slice
  it bears on — worded per step 4b's phantom-blocker warning, which covers comment text too. The id
  is what lets a reader of the comment find the item in the design, and it is the whole reason
  `goal-design` §5 pins one on every doubt and blocker.
- **Name every FALSIFIED premise there too, by its `PC-n`, with the corrected claim** (#1976) — even
  though it is not an open item and not carried in the count. The reason is the ticket rather than
  the review: `#<n>`'s own BODY still states the claim the sweep disproved, and the comment is the
  only place that correction lands beside it. A reader who takes the issue at its word after this
  review has been given no reason not to.
- **Where a slice cannot be FINISHED until an open item is answered, express that as a `blocked_by`
  EDGE in step 4b's plan JSON** — never as a sentence in an issue body. Step 4b's phantom-blocker
  warning is why: the edge is checkable and the sentence is a hazard. An edge is only writable
  where **another slice in this same plan** carries the open item: a `blocked_by` key names a
  sibling and nothing else (`_validate_and_order`), so an item with no slice of its own — a Doubt,
  a Blocker — has no edge to write, and the comment line above is its whole record. Never reach for
  a non-slice id to fill that gap; step 4b's `Depends on` rule is what to do instead.
- **Lead the outcome comment with the count and the sweep marker**, so a reader sees both without
  opening the design — step 4d's exact command is
  `goal-review: CONFIRMED (<k> open questions carried) [sweep: converged|CAPPED] -- <outcome>`.
  Write `(0 open questions carried)` when the adjudication resolved everything, and write the
  marker on both sweep outcomes: an omitted count is indistinguishable from a review that never
  adjudicated at all, and an absent marker reads as the good news (#1952).

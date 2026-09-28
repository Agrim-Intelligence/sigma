---
name: agrim-triage
description: Prioritize and dependency-sequence a multi-goal backlog drain. Use for campaign planning or /agrim-triage.
allowed-tools: Bash(python3 *), Bash(gh issue *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-triage

Detailed selection triggers: [selection](references/selection.md).

The non-trivial backlog needs triage: what to pick, what to defer, what blocks what, and which
goals can run in parallel? **agrim-triage** automates the manual flow of surveying the board, asking
filter questions, compiling a dependency-sequenced drain plan, and enacting it as native loop
primitives — so the plan is durable across sessions, and a later `/agrim-loop` start picks wave-1's
first goal without new integration.

The skill is five steps:

1. **Survey** the backlog (fail-soft): inbox (unanswered hand-offs) → active (claimed + in-flight) →
   parked → enqueued backlog → shadow backlog (assigned, not labelled) → epics (need decomposition
   **before** picking) → hygiene gaps (missing `priority:*`, and goal+parked contradictions) → edges (detected
   dependencies) → context (north-star, wave cap, gates). Detect cross-issue dependency edges (from
   goal body text and ledger).

2. **Question round** (AskUserQuestion where the host has it; plain questions otherwise): pick /
   defer / drop per triage bucket, then per-issue refinements. Three sub-questions this step used
   to ask unconditionally now resolve silently whenever context already answers them — the same
   test `/agrim-scope`'s own "clarifying-questions judgment" applies to its identical priority-rubric
   case: *"would the answer change the plan's shape? ... decide it silently and let the drafted plan
   itself carry the decision for the user to see"* (#2296):
   - **Wave capacity** applies the config default (`parallel.goals.max_concurrent`) silently —
     never ask just to confirm it. Ask only when the operator explicitly wants a *different* cap
     for this run; that is an override, not a confirmation.
   - **Detected dependency edges** auto-apply without re-confirmation. The rest of this codebase
     already trusts `blocker_scan`'s extraction unattended — `dependency_gate`, `auto_unpark`, and
     `sources.py`'s `_promote_blockers` all act on it directly, none of them re-asks a human — so
     re-confirming here was the one inconsistency. Ask only for a genuinely missing edge the text
     does not establish.
   - **Missing `priority:*` labels** on picked issues are filled by mechanically applying the
     rubric below: `survey`'s hygiene bucket (`_bucket_hygiene`) computes a `priority_hint` for
     every flagged issue straight from that issue's own title+body text (see "Priority criteria"
     below for the tiers it reads against). A confident hint (`ambiguous: false`) is the answer —
     apply it as the real label now (`gh issue edit <n> --add-label priority:P<tier>`, one explicit
     command per issue, never a shell loop — see "Bulk `gh` mutations" below) so the plan's own
     `--from-survey`/`--resolve-missing` read picks it up like any other already-labelled issue,
     and say what was auto-applied rather than applying it with no trace. Ask only when the hint
     itself is genuinely ambiguous between two adjacent tiers (`ambiguous` names them) — the same
     "soon → P1 or P2" shape `/agrim-scope`'s own SKILL.md names as a real question.

   A `model:*` label may be set the same way if this repo keeps one, but **do not ask for it and do
   not treat its absence as a gap**: the kit defines no `model:*` values and reads none, and the
   tier a goal actually runs at comes from `model_selection: "auto"` + `/agrim-model` over the
   goal's text, never from a label (#1602). The `Bash(gh issue *)` grant covers reading goal issue
   bodies during this phase to detect edges, and applying an auto-filled priority label.

3. **Compile** the plan via `triage.py plan`: topo sort over union(detected edges, user edges);
   cycle → hard error naming the cycle members. Group into **waves** (width ≤ cap, within-wave order
   = priority then issue number). Write the plan to `.sdlc/plans/triage/<UTC-date>-<slug>.md`
   (human-readable: waves + dependency map + deferred-with-why + "how to run" footer) +
   `.sdlc/plans/triage/<UTC-date>-<slug>.json` (machine form) + `.sdlc/plans/triage/active.json`
   (pointer to the active plan).

4. **Dry-run** via `triage.py enact .sdlc --plan <path>` (no `--apply` flag): shows the exact action
   list (assign + label + mark blockers) without applying. Prints any report-only notices (see
   caveats below). Confirm the actions.

5. **Enact** via `triage.py enact .sdlc --plan <path> --apply`: applies the actions (idempotent). Then:
   - **Start now:** immediately flow into `/agrim-loop` — start the loop (`loop.py start .sdlc
     --session-pid "$PPID"`), then pick the first goal (`loop.py next .sdlc --session-pid
     "$PPID"`). **Always pass `--session-pid "$PPID"` on both calls** — `$PPID` here means the
     same thing `/agrim-loop`'s own SKILL.md/README document: read fresh from YOUR OWN invoking
     shell on each call, not a value captured once. Without it, each separate `loop.py` dispatch
     falls back to its own internal, short-lived `os.getppid()`, and a later `/agrim-loop`
     continuation (which DOES pass `--session-pid` correctly) can silently re-dispatch a goal this
     hand-off already claimed — the exact double-dispatch bug #1199 exists to close. The loop
     drains the plan's wave-1 in dependency order.
   - **Save for later:** stop here. The plan persists in `.sdlc/plans/triage/`, and a later
     `/agrim-loop` start (in any future session) will pick wave-1's first goal because enactment
     used native loop primitives (labels, `Blocked by:` markers, assignment). No new mechanism is
     needed — the loop's `next_pending` + `backlog_check` handle the sequencing.

**Chain note (orchestrated/timed runs):** if the drain plan declares a strict dependency chain
(each goal blocks the next), use plain `next` in the loop, not `next-batch`. `next-batch` would
claim multiple blocked goals, then park-and-delabel them (breaking the wave), whereas a strict
chain is meant to drain one at a time so resources are freed for the next.

## Priority criteria: what makes something P0 vs P4

Filing-time priority has been pure filer judgment with no written guidance — #813's own measurement
found this repo sitting at **zero open P0** despite real production-breaking work existing in the
backlog, not because nothing warranted it but because nothing told a filer what would. #831 fixed
the tie-break MECHANISM (a `bug`-labelled issue now beats a same-tier non-bug); this section is the
separate, judgment-based piece #813 split off as #832 — what the tier itself should be, at filing
time. Every example below is a real issue on this board, not an invented case.

**#2296:** this rubric is no longer prose a human reads and applies by hand at the question round
— `_bucket_hygiene`'s `_priority_hint` (`skills/agrim-loop/scripts/triage.py`) reads the tier tests
below straight off an issue's own title+body and returns a computed tier, or names the two adjacent
tiers when the text genuinely reads as either. The prose stays the reference (what a human checks
an auto-applied tier, or an ambiguous ask, against); step 2 above is where it now gets used.

**The anchor question, before picking any tier: for Sigma, "production" is the loop/board/gate
machinery itself.** The core skills have no separate deployed end-user surface — the picker, the
board sync, the claim lock, the gate, the ledger ARE the product, and every adopter's run depends on
them directly. The issue's own examples ("production-breaking / user-facing failure" vs "tech-debt /
enhancement") cash out here as *the mechanism doing the wrong thing, silently, for every run that
hits it* — not a UI nicety, not a missing feature.

- **`P0`** — the mechanism is broken, unsafe, or defeated, right now. Test: *if nobody touches this
  today, does the loop keep doing the wrong thing — or something unsafe — for every run that hits
  it?* Precedent: #813 itself (priority decided nothing; the tie-break was bare filing order), #814
  (the Priority field shipped but was switched off — a human's ranking silently ignored), #720 (a
  board rewrite disabled five built-in project workflows), #707 (a migrated board deadlocks — the
  queue reads empty forever), #821 (`require_review` satisfiable by any comment — a review-gate
  bypass), #486 (a path-traversal write with no guard), #326-#328 (secret VALUES leaking into stdout
  and PR comments), #464 (`gate()` parks on checks that already passed), #387 and #374 (a double-pick
  race and a claim-identity collision — two loops can stomp the same work). The shape is consistent:
  every one is a security/safety hole, or the loop's own core correctness (pick, claim, gate, board
  sync) silently doing the wrong thing. This tier stays rare **by construction**, not filer
  reluctance — an empty `P0` bucket is not evidence of under-filing; a genuinely-broken mechanism
  sitting at `P2` is.

- **`P1`** — broad, real impact, but the mechanism still basically works. Test: *does this degrade
  something most runs hit, or leave real output wrong, short of the loop fully breaking or being
  unsafe?* Precedent: #1010 (the installed plugin was 142 commits behind — every run silently
  executing stale code) and #1005 (CI blocked outright — no PR could merge). Both hit every run, but
  neither is the loop actively corrupting state or exposing a hole the way the `P0` set does — that's
  the line between the two tiers.

- **`P2`** — real, wanted, planned work; nothing is broken or blocked by leaving it for now. Test:
  *is this genuinely going to get built, with no clock running on it?* Precedent: #916-#921 (the
  `agrim-scope` build itself — six substantial issues, all filed `P2`, all real, none urgent) and
  #952/#953 (the `decision_tier` epic — #953 states its own reasoning outright: *"P2 -- matches the
  epic and #818 itself"*). That's real, written precedent for a rule worth stating explicitly: **a
  sub-issue defaults to its epic's own priority**; diverge only when one slice's severity genuinely
  differs from its siblings, and say why (e.g. one slice of an otherwise-`P2` epic happens to close a
  security gap — that slice can stand at `P0`/`P1` on its own facts even though the rest of the epic
  stays `P2`). This is also the tier `/agrim-scope`'s own zero-signal default (`P1`, per its
  `SKILL.md`) should resolve UP FROM once real signal exists: "real, planned, not urgent" is a `P2`
  fact, not a reason to fall back to the no-signal default.

- **`P3`** — small, real, non-urgent: narrow-blast-radius bugs, tech-debt, follow-up polish. Test:
  *is this a real bug or cleanup that nobody is blocked on and no run hits by default?* Precedent:
  #1085 (a crash on an unusual input shape — real, but edge-case, not a mainline break) and #1036
  (code-review follow-up polish on an already-merged PR — textbook tech-debt). This is the issue's
  own example tier for "tech-debt / enhancement."

- **`P4`** — speculative, cosmetic, or pure hygiene; nothing depends on it landing soon. Test: *if
  this sits untouched for months, does anything get worse?* If no: `P4`. Precedent: #530 (a tracked
  zero-byte stray file at repo root — pure hygiene, zero functional impact), #861 and #856 (narrow
  internal consistency nits in edge-case code paths), #686 (a `agrim-triage` v2 scheduler integration —
  speculative future scope nothing needs yet), #680 (a UTC-midnight edge case in plan-artifact
  timestamps).

**Two mechanisms this criteria doesn't substitute for — and don't conflate them:**

- **The `bug`-beats-non-bug tie-break (#831)** answers a different question than tier does. Tier is
  "how urgent is this issue" (the judgment call above); the `bug` label is "given two issues already
  in the *same* tier, which sorts first." They compose, they don't substitute — a production-breaking
  bug is a `P0`/`P1` tier call on its own facts, never "leave it at `P2` and let the `bug` label do
  the work," because tier dominates every other signal (a `P2` bug never outranks a `P1` non-bug).
- **`discovery.blocker_promotion` (#900)** is a *runtime* safety net, and it is **off by default** —
  unset, what an issue blocks never touches its priority automatically. Don't lean on it at filing
  time, even on a repo that has it configured: if you can already see an issue blocks something
  urgent, set ITS tier accordingly now. #900 itself is the concrete case — merged at `P0`
  specifically because a real blocked-`P0` sat behind an unrelated lower-priority issue in production
  use, with nothing in the picker (at the time) reflecting that. Filing-time judgment is what
  actually catches this on the common repo that never turns `blocker_promotion` on.

## Caveats & report-only notices

**Prose mention caveat (F2):** The skill detects blockers by scanning goal bodies for phrases like
"blocked by #N", "needs #N", etc., with a 40-character lookahead window before the issue number.
**Important:** `enact` treats **any** `_BLOCK_RE` match naming that blocker as an already-present marker —
including an incidental prose mention such as "after #12", "needs #12", or "requires #12 only for
the docs" — and **skips writing the real marker** (`**Blocked by:** #N`). Consequence: the goal
may not be parked by `backlog_check`, and the wave can run out of order. Verify edges in the
question round. See the README's "Known caveat" for more detail.

**Conflicting label notice (F4):** If a picked issue already carries a conflicting `priority:*` or
`model:*` label (same prefix, different value), `enact` prints a **report-only notice** naming both
values — it does not modify or remove the existing label; the plan's label is still added, so the
issue temporarily carries both. This is not an error; it alerts you to a labeling inconsistency.
Resolve by hand (edit the label on the issue directly), then re-apply the plan.

**Stale blocker notice (F5):** If a goal body asserts a blocker (`#N`) that is not in this plan's
edge set, `enact` prints a **report-only notice** stating "body asserts blocker #N, not in this
plan". The marker stays in place. `backlog_check` will park the goal next time the loop picks it,
until you resolve the blocker reference (either pick #N too, or update the goal body).

## Bulk `gh` mutations: sequential only, then verify by read-back

Some sandboxed agent Bash environments silently no-op every iteration of a shell loop that runs a
`gh` **mutation** (`issue edit`, `issue create`, `issue close`, `project item-edit`, and similar) —
zero exit status, nothing surfaced as an error. **This is not a defect in this skill, its scripts,
or the `gh` tool** — it is a limitation of the host environment's command handling. Read-only calls
(`gh issue view`, `gh issue list`, and similar) are unaffected and remain safe in a loop; the
failure is specific to mutations, so don't over-warn on the read-only half of a survey.

When doing bulk board work by hand — label backfills, priority migrations, enqueueing a batch of
freshly-filed issues, or any cleanup this skill's own `enact` doesn't cover — write each mutation as
its own explicit command, never as a `for`/`while` shell loop:

```bash
# Don't: silently fails for every issue in an affected sandbox, no error surfaced
for n in 101 102 103; do gh issue edit $n --add-label "sdlc:goal"; done

# Do: explicit sequential commands
gh issue edit 101 --add-label "sdlc:goal"
gh issue edit 102 --add-label "sdlc:goal"
gh issue edit 103 --add-label "sdlc:goal"
```

Then **always run a read-back verification pass afterward** — re-read every mutated issue (e.g.
`gh issue view <n> --json labels`) and confirm the change actually landed. A zero exit status is
**not** sufficient evidence a mutation applied: because the failure is silent, only re-reading the
mutated objects catches it. Do this even in an environment that doesn't currently exhibit the bug —
a partially-applied bulk mutation is otherwise hard to detect after the fact by any other means.

## Internal flow

```bash
# Run the survey (fail-soft, report context)
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/triage.py" survey .sdlc --json

# Ask questions, compile the plan (topo sort, waves)
# (User provides picks/edges at the prompt; the script validates and writes artifacts)

# Dry-run the enactment (show action list, don't apply)
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/triage.py" enact .sdlc \
  --plan .sdlc/plans/triage/<UTC-date>-<slug>.json

# Enact with --apply (idempotent: safe to re-run)
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/triage.py" enact .sdlc \
  --plan .sdlc/plans/triage/<UTC-date>-<slug>.json --apply

# Start the loop from wave-1 (or save the plan and start later)
# --session-pid "$PPID" is required on BOTH calls, read fresh from your own shell each time --
# omitting it lets a later /agrim-loop continuation silently re-dispatch a goal this hand-off
# already claimed (the double-dispatch bug #1199 closes; see /agrim-loop's own SKILL.md/README).
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/loop.py" start .sdlc --session-pid "$PPID"
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/loop.py" next .sdlc --session-pid "$PPID"
```

The plan artifact (`.sdlc/plans/triage/<UTC-date>-<slug>.md` + `.json`) is durable and
self-describing — you can hand it to a teammate, save it for a later session, or use it to divide
work across parallel agent sessions.

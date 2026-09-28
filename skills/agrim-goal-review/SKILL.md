---
name: agrim-goal-review
description: Independently confirm a design write-up before creating its Spec/Epic and slices. Use after goal-design or /agrim-goal-review.
allowed-tools: Bash(python3 *), Bash(git *), Bash(mkdir *), Bash(gh auth switch *), Bash(gh auth status *), Bash(gh issue view *), Bash(gh issue comment *), Bash(gh label list *)
---

# agrim-goal-review

Detailed selection triggers: [selection](references/selection.md).

`goal-review` — Stage 2 of `docs/dossier-pipeline.md` §7. It confirms `goal-design`'s (#1825) output before anything downstream trusts it, on **either of goal-design's own two paths**:

1. **Product path** — a `.sdlc/design/<n>.md` written against an approved Dossier (`<n>` its
   `story`-labelled issue number).
2. **Tech-side retrofit path** — a `.sdlc/design/<n>.md` written against a goal `loop.py
   design-check` (#1825) flagged (`<n>` that goal's own issue number).

Both converge on the identical review below — the only difference is which issue `<n>` names. This skill produces the confirmation, the `sdlc:designed` overlay, and (when the design calls for it) the
Spec/Epic ticket + children. It never writes `.sdlc/design/<n>.md` itself — that's `goal-design`'s
job (`skills/agrim-goal-design/SKILL.md`) — and it never edits the target's own source code.

**Tooling.** The frontmatter grants exactly what the steps below run, and nothing was left implicit:
`python3` for every script, `git`, `mkdir` for step 4b's scratch directory, `gh issue view`/`gh
issue comment` for the outcome comments, `gh label list` for step 5e's precondition, and `gh auth
switch`/`gh auth status` because on a host carrying more than one `gh` account the active one is
device-global and drifts between sessions — re-assert it immediately before each `gh` call rather
than assuming it still holds. Read `Bash(python3 *)` honestly while you are here: it is a blanket
grant, and every script this skill runs creates issues and rewrites labels through it, so the narrow
`gh` entries beside it describe INTENT, not a sandbox. That is the pipeline's settled position and
all three stages state it (#1957): the list **declares intent and spends prompts; it is not a
sandbox**. The one half of it that is checkable — every command these files' own steps name is
covered by their own grant, so following one end to end costs no approval prompt — is measured by
`tests/test_pipeline_allowed_tools.py` rather than asserted here, because prose asserted it three
times and was wrong three times.

## 1. Read the target

Read `.sdlc/design/<n>.md` in full — its `Sweep`, `Seeds`, `Budget`, `Premise check` and `Out of
scope` fields first — plus the source issue `#<n>` itself, treating every claim as a hypothesis to
verify against the real repo. **NEVER read a missing `Sweep`, `Seeds`, `Budget`, `Premise check` or
`Out of scope` field as the good outcome** — an absent value is indistinguishable from a converged
one and is reported as a schema violation, never adjudicated away. Full detail — what each field
means, and the exact commands that re-verify `Seeds`' count and `Budget`'s fetched number against
the artifact's own claim — is in
[`references/reading-the-target.md`](references/reading-the-target.md).

## 2. Assemble the independent brief and dispatch a fresh reviewer

**The maker is never the checker.** Dispatch a genuinely fresh reviewer with no shared context to
adjudicate every doubt and blocker into one of three buckets (2a), reach a CONFIRM/REJECT verdict
(2b), and carry forward whatever is open-but-not-blocking onto the tickets (2c). Resolve the
mechanism first (`subagent` → hand a FRESH subagent only the brief; `process`/`command` → the two
commands below; `inline` → no mechanism on this machine, so say so in the verdict):

```
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/reviewer.py" resolve .sdlc
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/review_context.py" brief .sdlc <n> --for goal-review --artifact <n> > /tmp/brief-<n>.md
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/reviewer.py" check /tmp/brief-<n>.md --scratch "$(pwd)/.sdlc/work/<n>"
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/reviewer.py" run .sdlc /tmp/brief-<n>.md --scratch "$(pwd)/.sdlc/work/<n>"
```

Full detail — the exact branching per mechanism, the three-bucket adjudication (including `Out of
scope` and `Premise check` entries), and the verdict/carrying rules — is in
[`references/adjudication.md`](references/adjudication.md).

## 3. On REJECT

**First, close the Stage-1 design PR without merging it** (#2482 — a rejected design must not sit
open, indistinguishable from a live one):
```
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/work.py" close-design .sdlc <n> --comment "goal-review: REJECTED"
```
Prints one line — `closed PR #<n>`, `no open design PR found -- nothing to close` (the ordinary,
sanctioned outcome for a branchless/local-only design, or one already landed by hand), or a
`could not close ... (see stderr for detail)` failure. Capture that line; it becomes part of the
comment below.

Then comment the findings on issue `#<n>`, naming the design-PR outcome too:
```
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/loop.py" note .sdlc <n> "goal-review: REJECTED -- <findings> -- design PR: <close-design's own result>"
```
**This is the entire output of a REJECT** (§7g — no label, no board move, nothing else but the
design PR's own close), so treat the comment's result as load-bearing rather than fire-and-forget.
It prints `OK`/`FAILED` — the same convention `mark-designed` already uses below — and retries a
transient `gh` error itself before giving up (#1986); anything that still prints `FAILED` survived
that. **On `FAILED`, do not stop as if this had succeeded**: run it again once (a duplicate comment
is a strictly safer outcome than a silently missing one), and if it still fails, check `gh issue
view <n> --json comments` for whether an earlier attempt actually landed despite the reported
failure before concluding nothing did. If truly nothing landed, say so plainly in your own final
report — a REJECT whose comment never landed leaves zero trace anywhere that a review, or a
rejection, ever happened. `close-design`'s own result is never a reason to stop or retry this
comment — the finding already needs recording whether or not its PR could be closed.

Do **not** write `sdlc:designed`. Stop here — re-running `goal-design` (by a human, or a later
automated pick) against the same findings is the next step, not this skill's job. If `<n>` was
parked by the retrofit gate, it stays parked; nothing here changes that.

## 4. On CONFIRM

Write the `sdlc:designed` overlay, then — where the design's slice-count estimate is more than one
independently-implementable unit — build the Spec/Epic plan JSON and compile it via
`compile_plan.py --forbid-priority`, stamping every ticket it creates with `sdlc:designed` too.
**NEVER write a dependency, a pointer or a comment as an ordinary sentence containing `#N`** — a
hand-authored body with `#N` within 40 characters of a trigger word (`needs`, `depends on`,
`blocked by`, …) mints a phantom blocker that writes to a third, unrelated issue; express a real
dependency as a `blocked_by` edge instead, run the phantom-blocker control before compiling, and
prove it can fail before trusting a `clean` result. **Every CONFIRM branch also lands the Stage-1
design PR (#2482), at 4d — the one step every branch reaches** — see 4d's own detail for the call
and how its result folds into the outcome comment. Full detail — the overlay, the plan-building and
`Depends on` seam rules, the phantom-blocker warning and its runnable control, the
`--forbid-priority` enforcement, and 4d's design-PR landing — is in
[`references/confirm.md`](references/confirm.md).

## 5. Feature-ification — ask once, at the Epic level

Only reached on CONFIRM — step 3's REJECT returns before step 4 ever runs, so this can never fire
on a rejected design. `<epic>` = `report["epic"]` where a plan produced one, else `<n>` — which covers both 4c branches
and the one-row plan whose `report["epic"]` came back `null`, so the resolution keys off the report
rather than off which branch you took: per
`docs/dossier-pipeline.md` §3, both are "the Epic" in the sense that matters here — the ticket that is simultaneously the Spec
and the parent Epic its children (if any) hang off. On the 4b path `report["epic"]` is read from
`.sdlc/state/goal-review/<n>.report.json`, the file step 4b wrote.

**NEVER run the feature-ification ask on the Tech-side retrofit path** — asking is Product-path
only (a human ran `/agrim-goal-review` directly); the retrofit path is unattended by construction
and always reaches DEFERRED without ever asking. Full detail — how to ask, name and open the unit,
stamp the label on every ticket, ask about feature priority, and comment the outcome on every one
of the three answers — is in
[`references/feature-ification.md`](references/feature-ification.md).

## 6. Handoff — what this skill does NOT do

- The `Originates from Story #N` back-reference (#1827) is written by `compile_plan.py` itself,
  from step 4b's `epic.originates_from` field — this skill supplies the number, not the sentence.
- It does **not** re-run `goal-design` itself on a REJECT — that is a human's call, or a later pick.
- It does **not** touch `sdlc:goal`/`sdlc:parked` on the retrofit target — `/agrim-unpark` is the
  one gesture that does, deliberately kept separate (step 4c).

# How the Dossier pipeline works

A guided path from *"I have an idea and no issue"* to *"four tech goals are on the board and the loop
is picking them up"*, for someone who has to use this next week and has not read the contract.

**What the pipeline is for:** so that an idea can be captured, mapped against the real codebase, and
independently confirmed *before* it becomes work — instead of entering the loop at whatever altitude
the person who typed it happened to be at
([§1](dossier-pipeline.md#1-the-one-paragraph-version)). The gap it closes is specific and measured:
the size classifier the loop already had reads depth and knows nothing about blast radius
([§2](dossier-pipeline.md#2-the-gap-this-closes)).

**This document decides nothing.** Every rule, threshold, default and refusal lives in
[`dossier-pipeline.md`](dossier-pipeline.md) and is linked from here, never repeated. Where this file
and that one appear to disagree, the contract is right and this file has a bug. Read this to learn
*what happens, and in what order*; follow the link to learn *what the rule actually is*. Nothing on
this page is quotable as policy, and `tests/test_docs.py` holds it to that mechanically.

**The examples are real, and there are two of them.** Every issue number, label and quoted line
below comes from a run on a live board. The walk itself follows
the throwaway repository `dossier-e2e-v2-2026-08-28`, the idea *"Recurring tasks"* against a small task-tracking
CLI, which produced Story **#1**, Epic **#2**, and children **#3–#6**. A second run,
on the maintainers' own board,
ended in **no tickets at all** and is walked separately at the end — because that outcome is the
contract holding, and it is the one that looks like a failure. Nothing here is illustrative; where a
shape looks odd it is because that is what the run actually did.

Three words recur. A **Dossier** is the ticket holding business intent. A **design pass** is the
codebase mapping written to a file, not a ticket. A **slice** is one of the tech goals the loop
finally executes. [§3](dossier-pipeline.md#3-the-three-tiers) draws all three tiers and is worth
twenty seconds before anything below.

---

## Does any of this reach you yet?

Two questions, in this order.

**Are you starting from an idea, or from an issue that already exists?** From an idea, you enter at
Stage 0 and everything below happens in order. From an issue somebody already filed, none of Stage 0
applies — you are on the retrofit path, which is the same design pass reached from a different door
([§5a](dossier-pipeline.md#5a-the-two-paths)).

**Is the retrofit gate switched on in this repo?** Look for `goal_design` in `.sdlc/config.json`.
Its default answer is the one adopters most often assume backwards, so read
[§6a](dossier-pipeline.md#6a-config--goal_design-and-it-is-off-by-default) rather than guessing —
and read what it says about what happens to a backlog the moment it is turned on, because that is a
decision rather than a setting.

Nothing in Stage 0, Stage 1 or Stage 2 is gated on it. The Product path runs whether or not the gate
exists; the gate only decides what happens to work that never took that path
([§2](dossier-pipeline.md#2-the-gap-this-closes)).

---

## Stage 0 — the idea becomes a Dossier

You run `/sigma-dossier` and answer eight questions. They are the same eight for every idea, in the
same order, and they come out of the engine rather than out of the agent's imagination — the list
and the reason it is flat rather than keyed are in
[§4a](dossier-pipeline.md#4a-the-bank--eight-questions-fixed-and-ordered).

In the real run, the second answer was:

> Every week I re-type the same handful of tasks by hand — the Monday report, the Friday backup
> check. They are not new work, they are the same work on a cadence, and re-typing them is where
> they get forgotten.

Two questions in that set accept `"none"` as a complete answer, and the last one is a decision rather
than a description ([§4e](dossier-pipeline.md#4e-the-terminal-decision-and-the-handoff-probe)). The
run answered it `continue to Product`.

**Nothing reads your code during this.** That is the one constraint of the stage, and no host
enforces it for you —
[§4](dossier-pipeline.md#4-stage-0--the-dossier-sigma-dossier) says why the rule lives in prose
everywhere, and what the frontmatter's omission does and does not buy.

### The three extra questions the bank could not have asked

After the seven and before the decision, the skill re-reads the answers **as a set** and may ask up
to a few targeted follow-ups. The run asked two, and both were the kind of thing no fixed slot
catches:

> *When the next occurrence appears, what is it counted from — the date you completed the previous
> one, or a fixed schedule independent of when you finished?*

> *How does a repeating series stop? What cancels it, and what happens to the task in hand when it
> does?*

And it recorded one thing it could **not** settle, as an `open_` entry rather than a guess:

> `open_cadence_vs_examples` — the examples ("the Monday report") read as day-of-week anchored, but
> the stated non-goals rule out cron-style expressions. A plain +7 days satisfies the letter and
> drifts the Monday report off Monday.

The two kinds are recorded under different prefixes and are treated differently on filing — one is
capped and one deliberately is not.
[§4b](dossier-pipeline.md#4b-the-bounded-tail--followup_-and-open_) says which is which and why the
asymmetry exists at all; it is the single most surprising design decision in the stage.

### What lands

One `story`-labelled issue, self-assigned, carrying the whole Q&A in a fenced block — in the body
**and** in a comment. What that block looks like, and why it appears twice, is
[§4c](dossier-pipeline.md#4c-the-record-and-where-it-is-written).

If the filing call refuses instead, it refuses *before* anything reaches GitHub, and the reason it
prints is the fix. The full list of what it will not accept is
[§4d](dossier-pipeline.md#4d-what-file-refuses--before-any-write) — worth a glance now, because two
of the entries are things a person hand-editing an answers file does by accident.

---

## Stage 1 — the design pass maps it to the code

Now the code gets read. The design pass takes the Dossier and produces one file,
`.sdlc/design/<n>.md`, and one substantial comment on the issue. It produces **no** ticket and **no**
label — [§5f](dossier-pipeline.md#5f-what-stage-1-does-not-do) is the list of things people expect it
to do and it does not.

On the real run this was `full` mode, and the comment reported:

> **13 blast-radius sites** across **5 components** (plus one that does not exist yet). Two sweep
> rounds of a three-round budget; stopped on seed closure plus one quiet round.

Both halves of that sentence are contract, not style. How deep a pass goes is
[§5b](dossier-pipeline.md#5b-depth--full-and-lane); when it is allowed to stop, and what happens if
it runs out of rounds first, is [§5c](dossier-pipeline.md#5c-the-stopping-rule-and-the-budget) —
which also explains why "it found nothing new" needs a definition at all before it can terminate.

The `open_` entry from Stage 0 did not evaporate here. It arrived as a Doubt the design had to either
resolve against the real code and say how, or hand on still open
([§5a](dossier-pipeline.md#5a-the-two-paths)).

### The artifact has a fixed shape, and that is on purpose

The write-up's headings are not the author's choice. Its consumer is Stage 2, which reads it section
by section — so the layout is pinned, `Doubts` and `Blockers` stay two separate headings even when
one is empty, and the slice table's two columns become two different things downstream.
[§5d](dossier-pipeline.md#5d-the-artifact--sdlcdesignnmd-and-its-headings-are-a-contract) is the
schema and the rules about it that real runs got wrong before they were pinned.

### Getting it where somebody can read it

The run's own comment opens with a sentence that looks like bookkeeping and is actually the rule:

> Of the skill's two branchless routes I took the second: no branch was cut, nothing was committed,
> and **this comment is the whole deliverable**.

A Dossier never gets a worktree, so there is no branch for the file to land on unless you cut one —
one of three situations where the artifact stays local and the comment carries everything.
[§5e](dossier-pipeline.md#5e-publishing-it--the-comment-and-the-three-branchless-facts) lists all
three, and gives the one command that decides whether the file could reach the remote at all —
**read its exit-code note before you run it**, because the obvious reading of the result is the
opposite of the truth.

---

## Stage 2 — an independent reviewer confirms it

Whoever wrote the design does not confirm it. A fresh reviewer is handed an assembled brief and the
artifact's path, and nothing else
([§7a](dossier-pipeline.md#7a-the-independent-brief--the-maker-is-never-the-checker)).

Before naming a verdict, that reviewer has to place **every** doubt and blocker into one of three
buckets in writing. This is the step that stops "the design still has open questions" from being
either a rubber stamp or a rejection — most open questions are neither.
[§7b](dossier-pipeline.md#7b-adjudicate-every-doubt-and-blocker--three-buckets-in-writing) gives the
three buckets and the one concrete test that separates the third from the second.

The verdict itself is binary, and
[§7c](dossier-pipeline.md#7c-two-verdicts-and-why-there-is-no-third) explains why a middle verdict
is not available — the reason is mechanical rather than philosophical.

The run confirmed, and said so like this:

> `goal-review: CONFIRMED (9 open questions carried) -- .sdlc/design/1.md verified against the real
> repo at HEAD ac0be6d; epic #2 compiled with four children #3, #4, #5, #6, every one of them
> carrying sdlc:designed.`

Note the count in the parentheses. It is written even when it is zero, and
[§7c](dossier-pipeline.md#7c-two-verdicts-and-why-there-is-no-third) says what its absence would be
indistinguishable from.

**The other verdict stops here instead**, writes no label and creates nothing, and that is the
contract working rather than breaking — [when Stage 2 says no](#when-stage-2-says-no--and-why-zero-tickets-is-the-answer)
walks a real one below.

---

## Stage 3 — the Epic and its children exist

On confirmation, and only then, the tickets get made
([§7d](dossier-pipeline.md#7d-on-confirm--the-overlay-then-the-tickets)). The run produced:

```
#1  story, sdlc:designed                       Recurring tasks           ← the Dossier
#2  epic, sdlc:designed, priority:P1           Recurring tasks           ← the Spec/Epic
#3  sdlc:goal, sdlc:designed, priority:P1      Add Task.repeat + Task.completed …
#4  sdlc:goal, sdlc:designed, priority:P1      Spawn the next occurrence …
#5  sdlc:goal, sdlc:designed, priority:P1      Add the `repeat` command …
#6  sdlc:goal, sdlc:designed, priority:P1      Surface cadence + due date …
```

Three things in that listing are worth stopping on.

**The Epic carries no `sdlc:goal`** — it is never picked, by design
([§3](dossier-pipeline.md#3-the-three-tiers)). **Every child carries `sdlc:designed`** even though
each was just created by a confirmed design; skipping that stamp has a specific, circular consequence
spelled out in [§7d](dossier-pipeline.md#7d-on-confirm--the-overlay-then-the-tickets). **Everything
is `priority:P1`**, and that is not laziness — the reason nothing derived a priority per slice is
[§7f](dossier-pipeline.md#7f-priority-is-omitted-deliberately).

The bodies carry the trail. Epic #2 ends:

```
Originates from Story #1.

Tracks #3
Tracks #4
Tracks #5
Tracks #6
```

and child #4 ends:

```
Design: .sdlc/design/1.md
Story #1.

Part of epic #2.
**Blocked by:** #3
```

Six markers, and they are not all the same kind of thing — some are written by code and some are
prose the skill was told to type. [§8](dossier-pipeline.md#8-the-tickets-and-the-markers-on-them) is
the table of which is which, and
[§12](dossier-pipeline.md#12-honest-limitations-and-the-gaps-that-are-deliberate) says which of them
anything actually reads back.

**Why the wording of those bodies is fussy.** A perfectly ordinary sentence of scope prose containing
a `#N` can be picked up as a real dependency and cause a write to a third, unrelated issue.
[§7e](dossier-pipeline.md#7e-the-phantom-blocker-rule-and-its-control) is the mechanism, the fixed
wordings that avoid it, and the runnable check — including the bit where you are told to break the
check on purpose before believing it.

### The one question asked at the end

Once the Epic exists, you are asked once whether it should become its own unit of work. The run was
unattended, so nothing was promoted — and the reason that is a *recorded* outcome rather than
silence is [§9](dossier-pipeline.md#9-feature-ification--asked-once-at-the-epic-level), which has
three answers rather than the two you would expect. Epic #2 carries the comment:

> `feature-ification: deferred -- no human available on this pick, so nothing was promoted.`

What a unit of work is, and what promoting one costs, is a different contract:
[`branching-model.md`](branching-model.md).

### Six tickets is what THIS idea cost, not what the door charges

The listing above is one idea that genuinely spanned six components. A smaller one does not go
through the same shape: the design pass sizes its target first and runs the pass that size earns
([§5b](dossier-pipeline.md#5b-depth--full-and-lane)), and a design that lands on a single slice
produces one tech ticket and **no Epic at all**
([§7d](dossier-pipeline.md#7d-on-confirm--the-overlay-then-the-tickets)) — a story and a slice.
Which of the two you get is a property of the work, not of the door.

The door has a cheaper neighbour, too. Where the intent is not in doubt and there is nothing to map
— a flag, a stale docstring — an ordinary `sdlc:goal` issue does the job and skips all of this;
`sigma-dossier`'s own "what this skill does not do" says so. None of that is enforced by anything,
and [§12](dossier-pipeline.md#12-honest-limitations-and-the-gaps-that-are-deliberate) is where that
is admitted.

### And then it is an ordinary backlog

Nothing further is special. #3 is pickable, #4 waits on it, and the seven-phase loop runs them the
way it runs anything else ([§3](dossier-pipeline.md#3-the-three-tiers)).

## When Stage 2 says no — and why zero tickets is the answer

The run above confirmed. The other verdict produces **no Epic and no children at all**, and it is
the one you are more likely to meet on a first real idea — so it is worth reading before you meet
it, because from outside it looks like an idea going in and nothing coming out.

**A second real run, and this one did not end in tickets.**
The second run is on the maintainers' own
board: the idea *"Board doctor — audit a project board against what Sigma expects"*, taken
through the same three stages. Stage 1 mapped it properly — 36 blast-radius sites over 7
components, 34 queries in three sweep rounds, 11 Doubts and 1 Blocker, six slices. Stage 2 then
[adjudicated every one of them](dossier-pipeline.md#7b-adjudicate-every-doubt-and-blocker--three-buckets-in-writing)
and returned:

> `goal-review: REJECTED [sweep: CAPPED] -- one item adjudicated blocking (B-1).
> No sdlc:designed written, no Epic and no child created.`

That comment is the only thing that changed. #1973 still carries `story` and nothing else — the
list of everything a REJECT does *not* write is
[§7g-i](dossier-pipeline.md#7g-on-reject--what-it-writes-and-what-discharges-it), and the run said
so itself:

> STATE UNCHANGED. #1973 keeps its story label, gains nothing, and loses nothing.

**The eleven open questions are not what did it.** All eleven went into the middle bucket and none
of them held a slice; that bucket is the ordinary outcome rather than a problem
([§7b](dossier-pipeline.md#7b-adjudicate-every-doubt-and-blocker--three-buckets-in-writing)). One
item went into the third:

> B-1, which board a read-only audit reads. SUSTAINED, on the operational test rather than on a
> feeling. The slice that cannot start is slice 2, the board reader, and what it lacks is a ruling
> that sits with a human rather than an action inside its own remit.

Three conflicting board resolvers were verified in the tree first, at their line numbers, and the
three answers gave slice 2 three different function signatures. Slices 3–5 sit behind slice 2 and
slice 6 behind those three, so **five of the six slices were held and only slice 1 could have
started.**

**So compiling the plan anyway would have filed five pickable tickets whose scope was a guess.**
That is the trade [§7g-ii](dossier-pipeline.md#7g-on-reject--what-it-writes-and-what-discharges-it)
sets out, and it is mechanical rather than cautious: children are filed ready to pick
([§7d](dossier-pipeline.md#7d-on-confirm--the-overlay-then-the-tickets)), and B-1 is not one of the
six slices, so there was no dependency edge available to carry it either
([§7c](dossier-pipeline.md#7c-two-verdicts-and-why-there-is-no-third)). Zero tickets was the
cheapest true answer available.

**What it does not say about the idea.** Nothing. The mapping was re-checked at fourteen of its
thirty-six cited line ranges and every one held; the Dossier is untouched; the pass even recorded
that its independent-reviewer dispatch had degraded on that host rather than quietly claiming it
had not. A REJECT is a verdict on one design artifact at one moment, and
[§7g-iii](dossier-pipeline.md#7g-on-reject--what-it-writes-and-what-discharges-it) is the list of
things a reader is tempted to read into it.

**What to do next, which the comment tells you.** The findings are written to be the input to the
next Stage 1 pass, and the run named both routes out:

> WHAT WOULD DISCHARGE B-1: one ruling on which posture a read-only auditor takes, written into the
> design; or a re-slice splitting slice 2 into a resolver-free reader taking owner and project as
> parameters, plus a separate resolution slice

Either way the next move is to run the design pass again against them
([§5a](dossier-pipeline.md#5a-the-two-paths)) and let Stage 2 read the new artifact from the top.
**Nothing does that for you** — a REJECT sits where it is until somebody acts
([§12](dossier-pipeline.md#12-honest-limitations-and-the-gaps-that-are-deliberate)), which is also
where you will find what happens if the one comment it writes never lands.

---

## The PRD door -- a document instead of an interview

If the idea already exists as a PRD file, `sigma-prd-intake` is the way in, and the
scope-to-goals compile is not the route for a PRD. The model reads the file and drafts answers for each business
outcome, quoting the PRD for every one; `intake.py` refuses any answer whose quote is not really in
the file, files one Dossier per outcome (each stamped with the PRD path and sha256) and one umbrella
`epic` listing them. Anything the PRD leaves silent, vague or contradictory becomes an `open_`
question for design to carry into Doubts. It accepts a PRD of any shape and refuses a source-code
file. Design then runs on those Dossiers exactly as on any other. The contract is
[4f in the pipeline doc](dossier-pipeline.md#4f-stage-0-the-prd-door-sigma-prd-intake).

## The other way in — a goal that never saw a Dossier

Most issues on a real board were filed by a person, long before any of this existed. The retrofit
gate is what makes the mapping reach them, and it works by **stopping** the goal rather than by
designing it: the pick halts, a tracked "Design #N" issue is filed, and the loop takes the next goal
([§6](dossier-pipeline.md#6-the-retrofit-gate--looppy-design-check)).

Where in the pick that happens is not arbitrary — it runs after the size check for a reason worth
understanding before you turn it on
([§6b](dossier-pipeline.md#6b-where-it-sits-in-the-pick-sequence)).

Three results are possible and the loop reads only the first word of the line
([§6c](dossier-pipeline.md#6c-the-three-results-and-the-one-read-that-fails-closed)). That section is
also where the gate's one genuine subtlety lives: it is forgiving about almost every failure and
deliberately unforgiving about exactly one, and knowing which is the difference between a gate that
covers the backlog and a gate that only appears to.

The issue it files is itself an ordinary goal, with a body that tells whoever picks it what to do —
including what to check first so two concurrent runs do not both file one
([§6d](dossier-pipeline.md#6d-the-design-n-meta-issue)). From there it rejoins Stage 1 at
[§5a](dossier-pipeline.md#5a-the-two-paths), and everything after is identical.

The one asymmetry with the Product path: a retrofit design frequently concludes that the goal is
already one coherent unit and needs no slicing at all. That path creates no Epic
([§7d](dossier-pipeline.md#7d-on-confirm--the-overlay-then-the-tickets)), and it does **not** put the
parked goal back in the queue by itself — see the next section.

---

## When it stops and wants a human

Four moments, and none of them resolves itself.

**A parked retrofit goal stays parked**, even after its design is confirmed. Restoring it is a
separate gesture and there is a right way to perform it
([§12](dossier-pipeline.md#12-honest-limitations-and-the-gaps-that-are-deliberate), and
[`label-model.md`](label-model.md) for the gesture).

**A REJECT sits on the issue.** Nothing re-runs the design pass on its own
([§7g](dossier-pipeline.md#7g-on-reject--what-it-writes-and-what-discharges-it)), and
[when Stage 2 says no](#when-stage-2-says-no--and-why-zero-tickets-is-the-answer) walks the real one
above.

**A filed "Design #N" issue that could not be assigned is invisible to every loop.** The park line
says so when it happens, and that line is the only place it is visible
([§6d](dossier-pipeline.md#6d-the-design-n-meta-issue)).

**A deferred feature-ification is a question still open**, not an answer
([§9](dossier-pipeline.md#9-feature-ification--asked-once-at-the-epic-level)).

---

## Where each rule lives

The map, so you know where to go rather than which page to search.

| The question | The section |
|---|---|
| what the whole thing is for, in a paragraph | [§1](dossier-pipeline.md#1-the-one-paragraph-version) |
| why it exists at all — the thing the loop could not see | [§2](dossier-pipeline.md#2-the-gap-this-closes) |
| the three tiers, and which of them may not exist | [§3](dossier-pipeline.md#3-the-three-tiers) |
| the intake stage, and how hard "no code exploration" actually is | [§4](dossier-pipeline.md#4-stage-0--the-dossier-sigma-dossier) |
| the eight questions, and why the bank is flat | [§4a](dossier-pipeline.md#4a-the-bank--eight-questions-fixed-and-ordered) |
| the two kinds of extra question, and why only one is capped | [§4b](dossier-pipeline.md#4b-the-bounded-tail--followup_-and-open_) |
| the persisted record, and where it is written twice | [§4c](dossier-pipeline.md#4c-the-record-and-where-it-is-written) |
| everything filing will refuse, before touching the network | [§4d](dossier-pipeline.md#4d-what-file-refuses--before-any-write) |
| the closing decision, and the handoff that is probed rather than assumed | [§4e](dossier-pipeline.md#4e-the-terminal-decision-and-the-handoff-probe) |
| the design pass, and its two doors | [§5a](dossier-pipeline.md#5a-the-two-paths) |
| how deep a pass goes | [§5b](dossier-pipeline.md#5b-depth--full-and-lane) |
| when a pass may stop, and the budget it stops inside | [§5c](dossier-pipeline.md#5c-the-stopping-rule-and-the-budget) |
| the artifact's pinned headings | [§5d](dossier-pipeline.md#5d-the-artifact--sdlcdesignnmd-and-its-headings-are-a-contract) |
| how the artifact reaches a reader, and when it cannot | [§5e](dossier-pipeline.md#5e-publishing-it--the-comment-and-the-three-branchless-facts) |
| what the design pass deliberately does not do | [§5f](dossier-pipeline.md#5f-what-stage-1-does-not-do) |
| the retrofit gate, and the config that turns it on | [§6a](dossier-pipeline.md#6a-config--goal_design-and-it-is-off-by-default) |
| where the gate sits in the pick | [§6b](dossier-pipeline.md#6b-where-it-sits-in-the-pick-sequence) |
| its three results, and the one read that refuses to guess | [§6c](dossier-pipeline.md#6c-the-three-results-and-the-one-read-that-fails-closed) |
| the meta-issue it files | [§6d](dossier-pipeline.md#6d-the-design-n-meta-issue) |
| the independent brief | [§7a](dossier-pipeline.md#7a-the-independent-brief--the-maker-is-never-the-checker) |
| adjudicating doubts into three buckets | [§7b](dossier-pipeline.md#7b-adjudicate-every-doubt-and-blocker--three-buckets-in-writing) |
| the two verdicts | [§7c](dossier-pipeline.md#7c-two-verdicts-and-why-there-is-no-third) |
| what confirmation actually writes | [§7d](dossier-pipeline.md#7d-on-confirm--the-overlay-then-the-tickets) |
| phantom blockers, and the control that proves the check works | [§7e](dossier-pipeline.md#7e-the-phantom-blocker-rule-and-its-control) |
| why every slice comes out at the same priority | [§7f](dossier-pipeline.md#7f-priority-is-omitted-deliberately) |
| what a REJECT writes, and what discharges it | [§7g](dossier-pipeline.md#7g-on-reject--what-it-writes-and-what-discharges-it) |
| every marker on every ticket, and who writes it | [§8](dossier-pipeline.md#8-the-tickets-and-the-markers-on-them) |
| feature-ification, and its three answers | [§9](dossier-pipeline.md#9-feature-ification--asked-once-at-the-epic-level) |
| the labels, and which are seeded vs minted | [§10](dossier-pipeline.md#10-labels) |
| every file each stage writes, and whether it is tracked | [§11](dossier-pipeline.md#11-what-each-stage-writes-and-where) |
| **everything that does not work, stated by the model about itself** | [§12](dossier-pipeline.md#12-honest-limitations-and-the-gaps-that-are-deliberate) |

[§12](dossier-pipeline.md#12-honest-limitations-and-the-gaps-that-are-deliberate) is the section to
read second, whatever you read first. It is the pipeline's own inventory of its gaps — including a
table of every prediction the original design proposal got wrong — and reading it is cheaper than
discovering any one of them.

---

*This page is a route through [`docs/dossier-pipeline.md`](dossier-pipeline.md). That file is the
contract, and it is the only one of the two that is authoritative. The `sdlc:*` lifecycle labels are
a different contract again — [`docs/label-model.md`](label-model.md) — as are units of work
([`docs/branching-model.md`](branching-model.md)).*

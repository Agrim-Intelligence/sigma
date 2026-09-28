---
name: agrim-goal-design
description: Map a Dossier or retrofit goal to code, blockers, and slices in a design write-up. Use before goal-review or /agrim-goal-design.
allowed-tools: Bash(python3 *), Bash(git *), Bash(grep *), Bash(rg *), Bash(mkdir *), Bash(gh issue *)
---

# agrim-goal-design

Detailed selection triggers: [selection](references/selection.md).

`goal-design` — Stage 1 of `docs/dossier-pipeline.md` §5. It runs on **two paths**, never one:

1. **Product path** — given an approved Dossier (a `story`-labelled issue produced by `agrim-dossier`),
   map its intent onto the real codebase.
2. **Tech-side retrofit path** — given a goal `loop.py design-check` flagged (a filed
   "Design #N" meta-issue naming the goal, or the goal number run directly) because it reached
   `agrim-goal`/`agrim-loop` without carrying `sdlc:designed`.

Both paths converge on the same work below — the only difference is what you start from. This skill produces a **design artifact only**. It never implements the target's own work, never creates the
Epic/children (`goal-review` + `compile_plan.py`, contract §7, tracked as #1826), and never
writes `sdlc:designed` (goal-review only, once it confirms this design — see "Handoff" below).

**What the `allowed-tools` list above is, and is not.** It **declares intent and spends prompts; it
is not a sandbox.** `Bash(python3 *)` is unrestricted code execution — it reads any file, writes any
file, reaches the network and shells out — so nothing beside it narrows what this skill *could* do,
and a per-verb entry like `gh issue view *` would advertise a read-only posture this skill does not
have. That is the same reading `agrim-goal-review` and `agrim-dossier` state in their own frontmatter
notes: across the pipeline the list says what a stage means to do and saves an operator one approval
per step, and no part of it is a boundary. Three consequences, and they are the whole rule:

- **This file claims no enforcement anywhere, on any host.** An omitted entry does not stop the
  omitted command; it costs one approval prompt on a host that asks. Reading the codebase is this
  skill's entire job, so there was never anything here to withhold.
- **The one property the list does keep is a friction property, and it is measured rather than
  asserted** — every command the STEPS below name is covered by the grant above, checked over all
  three pipeline skills by `tests/test_pipeline_allowed_tools.py`. It is a test because this is a
  claim prose made three times and got wrong three times (#1911, #1927, #1957); the scan skips this
  note, which is the one place the file discusses commands it deliberately does not grant.
- **A pass that needs a command outside the list takes the prompt.** `find`, `ls`, `wc` and `cat`
  are absent
  because `git ls-files`, `git grep`, `rg` and `python3` already do their work at the noun-level
  granularity the rest of the kit uses (`Bash(gh issue *)`, as in `agrim-define`, `agrim-promote`,
  `agrim-scope`, `agrim-triage`, `agrim-unpark`, and five more), and a list that ends up naming every
  shell utility states nothing. Absent is not forbidden: the live run on this repo reached for `wc`,
  `awk` and two `gh` nouns this list does not carry, and paying one prompt each was the correct move.
  **Routing such a command through `python3` to dodge the prompt is the one thing that is not** —
  the grant is the operator's only view of what a pass runs, and hiding a command from it is the
  only way this list can actually mislead anybody.

## 1. Read the target

- **From a Dossier:** read the `story`-labelled issue in full — its Q&A history, its stated intent,
  any constraints already surfaced. Its Q&A block may carry a short tail under **Open questions**
  (`open_*` ids — `agrim-dossier`'s own record of what Stage 0 asked and could not settle, typically a
  semantic decision it had no slot for). Each one is a **Doubt** you inherit: carry it into step 3
  verbatim, and either resolve it against the real code and say how, or hand it on still open. Never
  let one disappear by being quietly answered in passing.
- **From the retrofit path:** read the flagged goal's own issue (title + body + comments). If you
  were dispatched via a filed "Design #N" meta-issue, that meta-issue's own body names the real
  target (`#{goal}` in its `sigma:design-of=#{goal}` marker) and the depth (`full`/`lane`,
  written into its own instructions) — design the TARGET, not the meta-issue.

Either way, treat every claim in the source as a hypothesis to verify against the real repo, not a
fact to copy — the same discipline `agrim-research` already applies to a goal's own text.

**And write down what happened to each one, under `## Premise check`** (#1976). Verifying a claim
and recording the verdict are two different acts, and only the first was ever asked for: on the live
re-run the Dossier claimed `/agrim-doctor` *"stops dead at the repo boundary"*, the sweep found it
crosses that boundary in six places, and the correction had nowhere to go — it went into `Intent` as
prose, where `goal-review`'s id-based adjudication cannot see it. A **material** claim is one this
design would be built differently if it were false: how the code behaves, where a boundary sits,
what a component does, what already exists. A claim about value, priority or urgency is not
material and is not checked here. Give each one `PC-n` (§5), one of exactly three verdicts —
**verified** (the repo agrees), **falsified** (the repo disagrees; say what is actually true) or
**unverifiable** (nothing in the repo settles it; say what would) — and the evidence that produced
it. A business-stage author gets technical premises wrong routinely and that is not a defect in
them; a design that inherits one silently is a defect in this pass.

**A falsified premise is not a Doubt and not a Blocker, which is why it needs its own heading.** A
Doubt is OPEN, a Blocker is UNRESOLVED, and a falsified premise is **settled and different** — the
question was asked, the repo answered, and the answer was not the one the source assumed. Filing it
as a Doubt asks `goal-review` to decide something this pass already decided; filing it as a Blocker
stops work that nothing is blocking. What it actually obliges is a rewrite: `## Intent` is written
against the **corrected** premise and says in one clause that it moved, and every slice below is
derived from that corrected intent rather than from the source's. If the source's problem statement
is wrong, every slice derived from it inherits the error.

## 2. Map to the codebase — depth per `goal_design.mode`

Size the target before sweeping it (small/medium/large, same vocabulary `discovery.py`'s own
`LANES` uses), resolve `mode` (`full` ceiling by default, or `lane`) on whichever path you are on,
then sweep with the commands the grant already covers until both halves of the stopping rule fire:
every seed swept, and a quiet round with every hit of that round accounted for as a blast-radius row
or a recorded `Out of scope` exclusion. **NEVER stop a `full`-mode sweep on a self-assessed "this
looks converged" — the stopping rule is defined over the `## Seeds` and `## Out of scope` records
you are writing as you go, never over a scope call held only in your head.** Fetch the round budget
from `goal_design.py sweep-budget` — **NEVER write `Budget` from memory.** Full detail — mode
resolution on each path, the exhaustive-over-seeds stopping rule, the scope-exclusion accounting,
and the budget-fetch mechanics — is in
[`references/mapping-the-codebase.md`](references/mapping-the-codebase.md).

## 3. Blast radius, components, doubts/blockers

Produce, explicitly:
- **Seeds** — every symbol the sweep ever held as a seed, whether it came from the source's own
  intent (round `0`) or was surfaced by a sweep, with the round it was swept in and whether it was
  swept at all. This is the set §2's stopping rule is defined over, so it is written on **every**
  artifact, converged or capped — a `converged` claim about a set nobody recorded is unfalsifiable.
- **Blast radius** — every touched file/component, one row each, each naming the component it
  belongs to.
- **Components** — the higher-level pieces this intent actually spans (a module, a service, a
  cross-repo boundary) — not just individual files.
- **Out of scope** — every hit a recorded search query returned that you ruled OUT, with the round
  and the reason (§2). This is the complement of the blast radius, not a leftovers pile: together
  the two account for every hit, and that is what the quiet round is judged over.
- **Doubts** — anything you're not confident about, **plus every `open_*` entry inherited from the
  Dossier** (step 1). Never assume it away; list it so a human (or `goal-review`) can weigh in.
- **Blockers** — anything that must be resolved before implementation could start (a missing
  dependency, an undecided upstream contract, a destructive/irreversible step).

Seeds, Out of scope, Doubts and Blockers are **numbered** — `S-1`, `X-1`, `D-1`, `B-1`, upward in
the order you write them — because every consumer of these lists has to cite items back and none
can invent an id for itself. §5 is the rule; write the ids as you produce the lists, not afterwards. `## Premise
check` (§1) is numbered the same way, `PC-1` upward.

## 4. Detailed design + slice count

Write the design itself: the shape of the change, the sequencing, and the tradeoffs — enough that a
later Plan phase can work from it without re-deriving the mapping. Close with a **slice-count
estimate**: how many independently-implementable tech goals (`sdlc:goal`-carrying issues) this design
implies, and roughly what each one covers. This is the number `goal-review` + `compile_plan.py`
(#1826) will use to create the Epic's children — you are estimating it, not creating them.

**The light path — when the honest answer is one slice** (#1954). A design that measured `small`
(§2) and lands on a single slice is a COMPLETE outcome, not a thin one, and this stage has to be
able to say so. The live run that motivated this produced 1 story + 1 epic + 5 children — **7
tickets** — for an idea that genuinely spanned six components; the same route applied to a
`--quiet` flag is what makes a first-time reader call the pipeline bloat and never reach for it on
the large ideas where it pays. Three things change, and nothing else does:

- **Every heading is still written.** §5's schema is not negotiable and a short section is not a
  missing one — `- None.` under `Doubts` is a real answer and one blast-radius row is a real table.
  The saving is in the sweep and the prose, never in the shape.
- **The `Slice-count estimate` table carries one row, with `—` under `Depends on`.** Say in
  `Detailed design` that this is one coherent unit and why the work does not decompose: that
  sentence is the claim Stage 2 adjudicates, so leaving it implicit gives the reviewer nothing to
  check.
- **Say in the handoff comment what Stage 2 will do with it**, so "one slice" is never read as
  "nothing was produced". `goal-review` 4c takes a one-slice design to **one** `sdlc:goal` ticket
  and **no** Epic — `compile_plan.py` creates the Epic wrapper only for a plan carrying more than
  one issue, so the absence is structural rather than a judgement call. The arithmetic for a small
  idea is a story and a slice: **2 tickets, not 7**.

## 5. Write the artifact

Write the design to `.sdlc/design/<n>.md` (`mkdir -p .sdlc/design` first) plus a required
`<n>-in-brief.md` sibling in plain language. **The headings are a contract, not a suggestion** —
`goal-review`'s own step 1 reads this file section by section, so write exactly the headings the
schema defines, in order, and put nothing the consumer needs outside them. `Sweep`, `Lane` and
`Budget` are mandatory header fields on every artifact, `converged` or `capped` alike — **NEVER
omit one of them, and NEVER read its absence as the good, unconfigured case**: an absent value is
indistinguishable from a converged/default one, which is exactly the failure each of #1952/#1954/
#2032 exists to end. Full detail — the complete heading schema and copyable skeleton, the in-brief
file's own stricter rules, and what each field means and why it is mandatory — is in
[`references/writing-the-artifact.md`](references/writing-the-artifact.md).

## 6. Handoff — what this skill does NOT do

- It does **not** create the Epic or its children. That's `goal-review` confirming this design, then
  `compile_plan.py`'s existing epic/children machinery (contract §7, #1826).
- It does **not** write `sdlc:designed`. That label is written only once `goal-review` confirms —
  never at `goal-design` alone, so an interruption between the two re-runs design rather than
  silently skipping review. `goal-review` ships as `skills/agrim-goal-review/SKILL.md`, but **that
  skill may not exist yet on your install** — check before you claim either way, and say which you
  found under the artifact's own `## Handoff` heading (§5's schema) and in the handoff comment.
  Until it does exist there: a human reviews `<n>-in-brief.md` (the full artifact if they want the
  citations) and applies `sdlc:designed` by hand once satisfied, then restores
  `sdlc:goal` with `/agrim-unpark` if the retrofit check parked the target (never a raw label edit —
  see `docs/label-model.md`). This is the same hedge `design_goal.py` already renders into the
  "Design #N" meta-issue it files; the two must not drift apart again.
- On the retrofit path, if the flagged goal is a filed "Design #N" meta-issue with no code changes
  of its own: skip `work.py` entirely (no worktree, no branch, no PR) and record the outcome
  directly once the artifact is written and linked.

---
name: sigma-dossier
description: Turn a high-level business idea into a Dossier through fixed Q&A, without code exploration. Use before design or /sigma-dossier.
allowed-tools: Bash(python3 *), Bash(gh issue *), Bash(ls skills/)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-dossier

Detailed selection triggers: [selection](references/selection.md).

The front door of `docs/dossier-pipeline.md`'s three-tier pipeline (Dossier → Spec/Epic →
Slices) — the contract for every rule this file performs; §4 is this stage. It turns a high-level idea into one **Dossier** — a `story`-labelled ticket, pure business
intent — never `sdlc:goal`. The loop must never try to execute a Dossier, for the identical
structural reason an `epic`-labelled ticket never is today.

> **Hard constraint: no code exploration at this stage.** This is intent-gathering, not analysis —
> do not read source files, grep the codebase, or reason about implementation. `allowed-tools`
> above deliberately omits `Read`/`Grep`/`Glob` against the repository, unlike `sigma-brainstorm`'s
> own frontmatter (which needs them for its step 1, "read the relevant files, docs, recent
> commits") — and that omission **declares intent and spends prompts; it is not a sandbox** (#1957).
> `Bash(python3 *)` sits in the same grant and is unrestricted code execution, so a source file is
> one `python3 -c` away here as it is anywhere else: **nothing structurally stops a code read on any
> host, Claude Code included** — not on Cursor, whose `.mdc` is text the model reads, and not on
> Claude Code either. So **this paragraph is what carries the rule, on every host**: obey it because
> it is this stage's rule, not because something would refuse you. The one exception, below, is
> reading this kit's own
> installed skill list — a question about Sigma's own layout, never about the business idea's
> codebase.

Engine: `skills/sigma-dossier/scripts/dossier.py`.

## The flow

1. **Fetch the bank** — `python3 "${CLAUDE_SKILL_DIR}/scripts/dossier.py" bank`. Eight questions,
   fixed and ordered: `title`, `problem`, `why_now`, `who`, `outcome`, `constraints`, `non_goals`,
   then always-last `next_step`. This is engine-owned data, not something to improvise — ask
   exactly what the bank gives you, in the order it gives it.

   Fetch the follow-up policy in the same call-the-engine way, because step 3 needs it:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/dossier.py" followups` gives you the cap, the two id
   prefixes, and the three triggers that warrant asking — engine-owned data, exactly like the bank.

2. **Ask the seven, one question at a time.** Use `AskUserQuestion` where the host has it, passing `options`
   for any question that has them (only `next_step` does — the rest are open business content, the
   same reason `sigma-unpark`'s own bank leaves most of its slots free-text). Plain conversation
   otherwise. Never skip a question and never batch several into one turn.

   `constraints` and `non_goals` both accept "none" as a complete, valid answer — don't press for
   more once the person has said there isn't anything.

3. **Reconcile, then ask at most 3 follow-ups — before the terminal question.** The bank is fixed,
   and a fixed bank cannot reach the semantics of one domain. On the validation run that motivated
   this step, the Dossier was sufficient for intent, constraints and non-goals and **not** sufficient
   to design from: nothing had asked what "the next occurrence" counted from, nothing had asked how a
   series stops, and two answers already on the page contradicted each other. So before you ask the
   terminal question, read the seven answers back **as a set** and look for the three triggers
   `dossier.py followups` gives you:

   - **an unstated anchor** — the answer names behaviour whose reference point is never given.
   - **no stated end** — something starts, recurs or is granted, and nothing says how it stops, is
     cancelled, or is undone.
   - **two answers that disagree** — an example given under one slot contradicts a limit given under
     another. This one is not a missing question at all; both answers are already there, and only
     reading them together finds it.

   Each trigger you find becomes **one** targeted question, asked exactly the way the bank's are —
   one at a time, `AskUserQuestion` where the host has it. **At most 3 follow-ups get an answer**:
   `file()` refuses a record carrying more `followup_` ids than that, and says so, because an idea
   that needs a fourth is more than one Dossier.

   Record every one of them in the same `{answers, questions}` file — an entry in **both**, never a
   bare answer, or `file()` refuses it:

   - answered → `followup_<slug>` (e.g. `followup_anchor`). Ordinary intake content, arrived at late.
   - not settled → `open_<slug>`, whose ANSWER is what is unknown and what would settle it. Use this
     when the person doesn't know yet, when answering it would need the look at the code Stage 0 must
     not take, or when nobody is there to ask at all (an unattended run records all of them this way
     rather than guessing). **There is no cap on these and filing never refuses one** — recording a
     doubt must never be the thing that makes the record fail — and step 6 hands them on.

   **This is the one bounded exception to "ask exactly what the bank gives you", and it stays on the
   business side of the hard constraint above.** A follow-up asks what the person *intends* ("does
   the next one start from when the last was finished, or from the calendar?"); it never asks where
   something lives in the code. Anything you cannot phrase as intent is an `open_` entry, not a
   follow-up.

4. **The terminal question is always asked, never inferred.** `next_step`'s two options are
   literally `"file and stop"` and `"continue to Product"`. Record whichever the person picks
   verbatim in the answers file — the mapping to a canonical decision token happens in the next
   step, not here.

5. **Resolve** — write the answers file (same `{answers, questions}` shape `sigma-unpark` already
   uses, so the two tools' answer files are interchangeable for a reader), then:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/dossier.py" file .sdlc \
       --answers /tmp/dossier-answers.json --decision stop|continue
   ```
   ```json
   { "answers":   { "title": "…", "problem": "…", "why_now": "…", "who": "…", "outcome": "…",
                    "constraints": "none", "non_goals": "none", "next_step": "file and stop",
                    "followup_anchor": "from when the last one was marked done",
                    "open_stop": "how a series is cancelled — nobody has decided yet" },
     "questions": [ { "id": "title", "ask": "In a few words, what should we call this?" }, …,
                    { "id": "followup_anchor", "ask": "Does the next one start from when the last
                     was finished, or from the calendar?" },
                    { "id": "open_stop", "ask": "How does one of these stop?" } ] }
   ```
   The two `followup_`/`open_` entries are step 3's, and they are the only ids outside the bank this
   file may carry — anything else is refused, which is what makes the cap on `followup_` countable.
   **Every one of them needs its entry in `questions` too**, exactly as above: an answer whose
   question nobody wrote down is not a record, and `file()` refuses it.
   `--decision` is the CANONICAL token — map the `next_step` answer through this table before
   calling:

   | `next_step` answer | `--decision` |
   |---|---|
   | `file and stop` | `stop` |
   | `continue to Product` | `continue` |

   This creates the Dossier: `story`-labelled, never `sdlc:goal`, self-assigned automatically (no
   assignment prompt — `dossier.py` always assigns `@me`). The persisted Q&A block goes into both
   the issue body and a follow-up comment, exactly as recorded — nothing here reads back or edits
   an existing issue, because the issue does not exist until this call succeeds.

6. **Report the outcome.** Read the JSON `file` printed:
   - `"failed"` — a bad decision token, a missing required answer, or a malformed follow-up tail
     (an id matching neither prefix, one with no recorded question, or more than 3 answered ones)
     never reached `gh` at all; fix the answers file and retry. Report the `detail` verbatim.
   - `"filed"` — report the new issue number and title, and **every `open_` id the `detail` names**:
     those are what Stage 0 could not settle, and saying "filed" without them hands the next stage a
     record that looks complete. Then act on the decision:

     **`decision: "stop"`** — stop here. Nothing else to do.

     **`decision: "continue"`** — probe whether this install actually has a Product-stage skill
     before claiming any handoff happened: `ls skills/` (the one exact command
     `allowed-tools` names beyond `python3`/`gh issue` — no wildcard and no pipe, so what the entry
     DECLARES is a single fixed lookup rather than repo search; it states this probe's intent and
     costs it no prompt, and the constraint above is what actually holds the line), and
     look for a `goal-design`-shaped entry in the listing yourself.
     - **Found** — hand off to it against the new issue number, following its own SKILL.md, and name
       the `open_` entries in the handoff: `goal-design` carries them into its own **Doubts**, which
       is the whole reason an unsettled question is written down rather than dropped.

       **Say what the Product stage will cost, and never let the big number stand for all of
       them.** Stage 1 sizes the target before it sweeps and runs the pass that size earns, and a
       design landing on one slice produces one tech ticket and no Epic at all — a story and a
       slice, **2 tickets**. The seven-ticket figure people quote (1 story + 1 epic + 5 children) is
       what an idea spanning six components costs, not what this door charges (#1954). Someone who
       believes the front door has one gear will not use it for anything.
     - **Not found** (true for every install until `docs/dossier-pipeline.md` §5
       — tracked on the maintainers' board — actually
       ships) — **say so plainly and stop.** Never describe a handoff as happening when it did not:
       the Dossier is filed, self-assigned, and — being `story`-labelled, never `sdlc:goal` — sits
       ready for goal-design to pick up the moment it exists. Nothing about this skill needs to
       change when it does; the probe is dynamic, not a version check pinned to today's install.

     This probe is the one place this skill reads a repo path — Sigma's own `skills/`
     directory, to answer "does my own toolchain have a next stage," never the user's codebase.

## Why the bank is flat, not keyed

`sigma-unpark`'s bank is keyed by park `reason_class`, because different parks genuinely need
different questions. A Dossier has no equivalent axis: Stage 0 asks the identical set of every idea,
which is what "strict predefined structure" means here. `dossier.py`'s `QUESTIONS` is a flat,
ordered list with no per-slot `when` condition — the same underlying shape (engine-owned slots, a
persisted record, a reserved closing question), with the one axis unpark has and a dossier doesn't
genuinely absent rather than faked with an unused keying dimension.

**And it stays flat under #1916.** The fix for "a fixed bank cannot reach domain semantics" is
deliberately *not* a bigger or keyed bank: every extra slot is a question asked of every idea forever,
and one of the three gaps the validation run found was two answers contradicting each other, which no
slot catches. What varies instead is a short tail on the RECORD — step 3's `followup_`/`open_` entries
— so the questions asked of everyone stay the same eight, and the domain-specific ones are visibly
domain-specific.

## What this skill does not do

- It does not map the idea against the codebase, estimate blast radius, or produce a slice count —
  that is `goal-design` (Stage 1, #1825), not this skill, and is exactly why code exploration is
  off-limits here.
- It does not decide feature-ification (`feature:<name>` unit promotion) — that is asked once, at
  the Epic level, after `goal-design` + `goal-review` both complete (#1828), never here.
- It does not open-endedly interview — that is `sigma-brainstorm`. Step 3's tail is capped at 3
  answered follow-ups **by `dossier.py` itself**, and `file()` refuses a record that exceeds it —
  which is the difference between a limit that holds and a rule that is merely read. The
  no-code-exploration constraint above is the other kind, on every host, which is why anything that
  must actually hold goes into `dossier.py` rather than into the grant. What is not enforced, and is deliberate: nothing counts the `open_` entries, and
  nothing checks that a follow-up was warranted by one of the three triggers — the first because
  refusing a doubt is worse than recording a long list of them, the second because no local check can
  read a question's intent.
- It is not the cheapest door for a change that is already understood, and saying so is part of
  running it. This stage exists to turn an idea whose radius nobody knows into one that has been
  mapped. Where the intent is not in doubt and the work is one small slice — the `--quiet` flag, the
  docstring that is out of date — an ordinary `sdlc:goal` issue is lighter and loses nothing,
  because there is no mapping left for Stage 1 to do. Offer that rather than running the flow out of
  politeness: a pipeline spent on work that did not need it is how a team concludes the whole thing
  is overhead, and then does not reach for it on the large ideas where it pays (#1954). Where it IS
  run on something small, the stages do scale down — Stage 1's measured lane and Stage 2's no-Epic
  outcome are both reachable from here, and neither needs a flag.
- It never edits an existing issue. A second dossier for a related idea is a second, separate run
  of this same flow, producing a second Dossier ticket.

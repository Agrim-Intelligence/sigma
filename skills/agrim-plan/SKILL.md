---
name: agrim-plan
description: Write a bite-sized implementation plan with files, tests, and done criteria before edits. Use in Plan or /agrim-plan.
---

# agrim-plan

Detailed selection triggers: [selection](references/selection.md).

## Context-efficient inspection

Locate before opening: use `rg --files` for paths and `rg -n` for symbols or claims.
Read only the relevant lines with a bounded range; expand outward only when the
boundary is unclear. Do not dump whole files or broad search output into context.
For bulky tests or diagnostics, save full output to a scratch file, then inspect
its exit status and focused matches or a bounded tail. Do not hide failures, and
keep secrets out of the scratch file. This is guidance, not a measured savings claim.

**Executor resolution (host-aware):**
- **Claude Code + `superpowers` installed** → prefer **`superpowers:writing-plans`** (its richer version).
- **Otherwise** (Cursor / any host / no companion) → use this. Same discipline, portable.

Write the plan **before touching code**, for a reader with **zero context and questionable taste** —
spell out exactly what to do. DRY, YAGNI, TDD, small verifiable increments. Then hand it to
`agrim-plan-review`.

## 1. Scope check
If the spec spans multiple independent subsystems, split it into **separate plans** — one per
subsystem, each producing working, testable software on its own.

## 2. File structure (decide decomposition first)
Map which files are **created / modified** and each one's single responsibility. Small, focused files;
files that change together live together (split by responsibility, not by layer); **follow the
codebase's existing patterns** — don't unilaterally restructure.

## 3. Tasks — right-sized + bite-sized
- A **task** is the smallest unit that carries its own test cycle and is worth a reviewer's gate; it
  ends with an **independently testable deliverable**. Fold setup/config/docs into the task that needs
  them; split only where a reviewer could reject one and approve its neighbor.
- Each **step** is one action (~2–5 min): *write the failing test → run it, watch it fail → minimal
  code to pass → run, green.* Steps never end in a literal commit — `agrim-loop` step 6's
  `work.py commit` is the ONLY commit for this goal, made once by the orchestrator after every step
  is done, and its own `git add -A` picks up everything at once. A step that runs `git commit`
  itself defeats that call's secret scan (see `agrim-loop` SKILL.md's no-phase-commits rule) — leave
  the worktree uncommitted between steps.

## 4. Definition of done
State, per task and for the whole plan, the **checkable condition** that proves it's finished (the
command + expected output). This is what `agrim-verify` checks and what the goal's `done_when` maps to.

Include a literal `## Tests` section with 1–25 backticked repo-relative pytest selectors,
one per bullet and no prose in that section, for example:

```markdown
## Tests
- `tests/test_test_first_gate.py`
- `tests/test_witness.py`
```

A file selector includes **every collected test** in that file; each needs its own assertion red.
Prefer node selectors for a narrow change. `loop.py verify` uses this same scope for its planned
observation, flake check and witness reporting. With `verify.enforce` on, `work.py pr` requires
matching red-before-green proof for these nodes. Non-pytest projects and changes with no applicable
tests must use the explicit `--no-tests <reason>` exception at PR publication; the reason is public
in the PR body. Repository verification and plan review remain required under their own settings.

## 5. Write it to disk — `.sdlc/plans/<goal-stem>.md`
The plan is an **artifact**, not a message. Save it under `.sdlc/plans/`, named for the goal
(`0007-fix-retry.md` → `.sdlc/plans/0007-fix-retry.md`). Four things downstream read the file, not the
conversation:

- **`agrim-plan-review` runs author-blind** (`review.independent`) — a fresh reviewer that never saw
  this session opens the plan from disk. A plan that exists only in chat cannot be independently
  reviewed at all; the reviewer arrives with nothing to read.
- **The hard plan-gate** (`gates.hard_plan_gate`) refuses unplanned source at two points. On Claude
  Code the `PreToolUse` hook denies the EDIT unless a plan under `.sdlc/plans/` is fresher than
  `plan_freshness_hours`. On **every** host — Claude Code, Cursor, Codex — `work.py pr` refuses the
  PUSH unless **this goal** has a plan there at all (no freshness window: a goal that legitimately
  ran longer than one still planned properly). Skip this step with the gate on and, at best, the
  goal cannot open its pull request. **With `work.enabled` off** there is no push to refuse, so only
  the Claude Code hook applies and the gate is Claude-Code-only on that checkout — `/agrim-doctor`
  and `loop.py record done` both say so rather than reporting it as enforced.
- **Implement** works from the written steps, so a flushed context window doesn't lose the plan.
- **The reviewer of the PR** checks the change against it — so the plan has to reach the goal's
  BRANCH too, not just this directory (the loop's landing step copies it into the worktree, and
  `work.py pr` refuses to push a branch missing it — #1548).

## Hand off
The finished plan goes to **`agrim-plan-review`** (never skipped) before any edit — it verifies each
claim against real code, stress-tests what breaks after it ships, and (vision-first) checks it against
your strategy.

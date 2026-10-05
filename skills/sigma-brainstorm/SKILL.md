---
name: sigma-brainstorm
description: Shape an idea into one approved, checkable goal before code. Use in the Goal phase or /sigma-brainstorm.
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-brainstorm

Detailed selection triggers: [selection](references/selection.md).

**Executor resolution (host-aware):**
- **Claude Code + `superpowers` installed** → prefer **`superpowers:brainstorming`** (its richer
  version, including an interactive visual companion).
- **Otherwise** (Cursor / any host / no companion) → use this. Same discipline, portable (text-only —
  no visual server).

Turn the idea into an approved design **before any implementation.**

## Context-efficient exploration

Locate candidates with `rg --files` and `rg -n` before opening anything. Read
only the relevant lines needed for the current decision; send bulky command
output to a scratch file and inspect a bounded excerpt rather than loading it
into the conversation.

> **Hard gate:** do NOT write code, scaffold, or invoke an implementation skill until you've presented a
> design and the user has approved it — **every** project, however simple. "Too simple to need a design"
> is where unexamined assumptions waste the most work. The design can be a few sentences; present it and
> get approval anyway.

## The flow
1. **Explore context** — read the relevant files, docs, recent commits, and the north-star:
   `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/north_star.py"` prints its real path (the bare one is
   gitignored out of any goal worktree, #1778); nothing there just means a drop-in project.
2. **Scope check** — if the request spans multiple independent subsystems, flag it and **decompose**
   into sub-projects first; brainstorm the first one through this flow. Each gets its own goal → plan.
3. **Ask clarifying questions — one at a time** — purpose, constraints, success criteria. Prefer
   multiple-choice where you can.
4. **Propose 2-3 approaches** with trade-offs and **your recommendation** — never just one.
5. **Present the design** in sections scaled to complexity; get approval, revising until approved.
6. **Restate the outcome as one concrete, checkable goal** (a `done_when` a reviewer could verify) —
   the artifact the rest of the SDLC runs on.

## Hand off
The terminal step is **`sigma-plan`** (write the implementation plan). Do not jump to any other
implementation action from here.

### Record P1 acceptance before handing off

Run `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/acceptance.py" record .sdlc "<goal>"`.
For an absent `## Done when`, write a draft containing 3–7 checkable statements and add
`--draft <file>`; capture posts drafted GitHub criteria back as a comment. An optional
`--verify-command '<command>'` records the focused check. Refusal means repair the
source/draft and retry before code. Preserve the record and commit a copy in the goal worktree.

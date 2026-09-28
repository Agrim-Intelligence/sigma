---
name: agrim-vision
description: Write a tiered product north-star to ground goals; opt-in vision-first setup. Use for product vision or /agrim-vision.
allowed-tools: Bash(python3 *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-vision

Detailed selection triggers: [selection](references/selection.md).

The opt-in **vision-first** on-ramp. Drop-in (a thin `project.md`, then run goals) stays the default;
this is for starting **top-down from a product vision** so every goal is grounded in *why* you're
building it. Same `.sdlc/` layout, same spine — just a thicker context layer.

1. **Scaffold the skeleton** if absent (skip-if-exists — never clobbers your edits):
   `python3 "${CLAUDE_SKILL_DIR}/../agrim-init/scripts/sdlc_init.py" . --vision`
   It writes `.sdlc/context/north-star.md` with four tiers to fill.
2. **Draft from the repo first, then refine** — don't hand over a blank page. Read the README, the
   code structure, and recent git history, write a *first-pass draft* of each tier, then walk the user
   through refining it. Editing a draft beats filling a blank. Keep each short (direction, not a spec):
   - **Vision** — draft the change / audience you infer from the README and project; the user corrects.
   - **Strategy** — draft priorities + **non-goals** from recent commits / open issues; the user sets
     the real ones (the plan-review alignment gate uses the non-goals).
   - **Design** — draft the UX shape + the principles a change must respect; the user adjusts.
   - **Architecture** — draft the rules you develop by as a **numbered, checkable list** from the
     codebase (layering, dependency direction, boundaries) for the user to approve — plan-review
     **enforces** these (the stack stays in `project.md`).
   Write each tier into the file as you settle it; **never overwrite a tier the user already filled**
   without asking.
3. **Go deep on a tier (optional, on demand)** — the one-pass draft above is the lean default. When the
   user wants to externalize a tier properly, load its deep-elicitation guide and run it:
   `${CLAUDE_SKILL_DIR}/references/{vision,strategy,design,architecture}.md` — a fuller question set,
   pre-flight, stress-tests, and a per-tier refresh cadence. **Architecture** drafts itself from the
   codebase (README + structure + `git log` + any `CLAUDE.md`/`AGENTS.md`) for the user to approve; the
   others elicit from the user one or two questions at a time. Load a guide only when that depth is
   asked for — the thin pass stays the default; never bloat.
4. **Confirm it's live:** `/agrim-context` now recalls the north-star first for every goal, grounding
   Goal → Plan in it. They can deepen it anytime by re-running `/agrim-vision`.

Keep it lean — this is direction that grounds the work, not a document to maintain for its own sake.
Progressive disclosure: a drop-in project can add this later; a vision-first project just starts
running goals when the tiers are filled.

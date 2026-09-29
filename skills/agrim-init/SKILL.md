---
name: agrim-init
description: Scaffold the per-project .sdlc layer. Use for a new repository setup or /agrim-init.
allowed-tools: Bash(python3 *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-init

Detailed selection triggers: [selection](references/selection.md).

Scaffold the `.sdlc/` project layer, then report what happened.

1. Run the bundled scaffolder from the repository root:

   `python3 "${CLAUDE_SKILL_DIR}/scripts/sdlc_init.py"`

   (Pass a target path as the first argument, and pass through any `--github` / `--demo` / `--vision`
   / `--cursor` / `--codex` flags the user gave. On Codex, pass `--codex` even when the user did not
   spell it out: this writes or refreshes its managed `AGENTS.md` block and preserves other rules.
   **`--github`** also installs the GitHub PM scaffolding —
   epic/task/bug issue templates, the auto-add-to-project workflow, a critical-insight template, and a
   label guide — into `.github/`. **`--demo`** queues a small, safe, runnable demo goal so `/agrim-loop`
   shows the SDLC immediately; with `--github`, also file it as an `sdlc:goal` issue (`gh issue create`)
   so it runs on the board. **`--vision`** scaffolds the opt-in north-star. **`--cursor`** installs the
   **Cursor host adapter** (*experimental — not yet verified in a live Cursor session*) — two
   always-applied rules, `.cursor/rules/sdlc.mdc` carrying the SDLC discipline (Cursor has no
   `UserPromptSubmit` hook, so the rule is its analog) and `.cursor/rules/output-contract.mdc`
   pointing status output at `render.py` rather than at prose to imitate — and pins `companions: off`
   so the portable `agrim-*` executors are used.) **`--codex`** adds the standing Codex rule block
   without changing `.sdlc/config.json` or the Claude host path.
1a. `/agrim-init` now git-ignores the machine-written runtime dirs itself, by shelling out to
    `setup.py ignore` (the same mechanism `/agrim-setup` uses) at the end of scaffolding — a bare
    `/agrim-init`, on its own, is a real, supported install and no longer depends on also running
    `/agrim-setup` for `.sdlc/events/`, `.sdlc/ledger/`, and the other runtime dirs to stay
    git-ignored (#2626). To change the ignore *scope* afterward (tracked vs. `.git/info/exclude`
    local-only), move the lines by hand — `/agrim-setup` never relocates a rule it finds already in
    place, so rerunning it does not migrate the scope for you. A failed ignore-write now fails the
    command itself (nonzero exit from the CLI, or `ok: false` from the wizard path) instead of
    silently leaving the runtime dirs uncovered.
2. Read the printed `created / skipped` summary and the git tip. `/agrim-init` creates
   `.sdlc/ledger/` holding only a `README.md`; the ledger stays off (`ledger.enabled: null`) until
   you enable it and `/agrim-ledger` bootstraps it.
3. Report which files were created, then settle the verify command. `loop.py verify` reads it
   from two places only: a local goal's frontmatter `verify_command` (wins), else
   `verify.command` in `.sdlc/config.json`. `.sdlc/project.md` is not read for it. The scaffold
   never leaves `verify.enforce` on with an empty command, because that refuses every `done`. A
   fresh config ships enforce OFF, with the reason in `verify._why`, and the scaffolder prints the
   detected candidate (`python3 "${CLAUDE_SKILL_DIR}/scripts/verify_detect.py" detect .` lists
   them all).
   - **Claude Code:** ask the user with a real question: confirm the detected candidate, replace
     it with their own command, or decline. On confirm or replace, run
     `python3 "${CLAUDE_SKILL_DIR}/scripts/verify_detect.py" set .sdlc "<command>"`. That sets
     the command and turns enforce ON. On decline, run
     `python3 "${CLAUDE_SKILL_DIR}/scripts/verify_detect.py" decline .sdlc`. That keeps enforce
     OFF and records why. If nothing was detected, say so and say that enforce is OFF.
   - **Codex / Cursor:** relay the printed candidate block verbatim. It carries the exact `set`
     command and the exact config line. Do not guess a command on the user's behalf.
4. Relay the git tip. Never overwrite a file the scaffolder reports as skipped — those hold live state.

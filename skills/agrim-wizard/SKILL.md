---
name: agrim-wizard
description: Guide first-run setup one unresolved check at a time. Invoked when session setup is incomplete.
allowed-tools: Bash(python3 *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-wizard

Detailed selection triggers: [selection](references/selection.md).

Triggered by `hooks/session_start.sh` injecting `additionalContext` naming unresolved checks —
never invoked by a user typing a command. If you are reading this, `setup_wizard.wizard_status()`
already found something incomplete.

**Engine:** `setup_wizard.py` (`wizard_status()`, `read_dismissed()`, `write_dismissed()`) and
`wizard_actions.py` (`run_scaffold()`, `run_graphify_install()`), both in the sibling `agrim-init`
skill's `scripts/`. Neither has a CLI — they are import-only, so every call below goes through
`python3 -c` with the directory put on `sys.path` first.

**THE TWO PATHS ARE DIFFERENT, AND CONFUSING THEM CREATES `.sdlc/.sdlc/`.** `wizard_status()`,
`read_dismissed()` and `write_dismissed()` all take **`sdlc_dir`** — the `.sdlc` directory itself.
`run_scaffold()` takes **`repo_root`** — the repository root, the directory `.sdlc` will be created
*inside*. Run everything below from the repo root, where that is `.sdlc` and `.` respectively.

Check status (this is also the recheck after every action — `allow_cache=False` is required, see
the `auto_fixable` steps below):
```bash
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_SKILL_DIR}/../agrim-init/scripts')
import setup_wizard
print(json.dumps(setup_wizard.wizard_status('.sdlc', allow_cache=False), indent=2))
"
```

Dismiss a check the user said no to (read the existing set, add to it, write it back — never
overwrite the file with a single name):
```bash
python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_SKILL_DIR}/../agrim-init/scripts')
import setup_wizard
d = setup_wizard.read_dismissed('.sdlc')
setup_wizard.write_dismissed('.sdlc', d | {'gh auth'})
print(sorted(setup_wizard.read_dismissed('.sdlc')))
"
```

Run an action (`run_scaffold` takes the REPO ROOT; `run_graphify_install` takes no path at all):
```bash
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_SKILL_DIR}/../agrim-init/scripts')
import wizard_actions
print(json.dumps(wizard_actions.run_scaffold('.')))
"
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_SKILL_DIR}/../agrim-init/scripts')
import wizard_actions
print(json.dumps(wizard_actions.run_graphify_install()))
"
```

`${CLAUDE_SKILL_DIR}` is this skill's own directory, resolved by the host — the same convention
`agrim-doctor`'s SKILL.md uses to reach a sibling skill's scripts. Never hand-write a plugin path.

## The one hard rule

**Every time you present a check the user might skip, state in the SAME message what stops
working if they do.** Never a bare "want to fix X?" — always "want to fix X? If not: `<the
step's own degraded text, verbatim>`." This is a non-negotiable product requirement, not a style
preference — a silently-different fallback here recreates the exact bug Sigma's own journal
pipeline was rebuilt to stop doing.

## For each step, by mode

**`auto_fixable`** (the wizard can act, with consent):
1. Explain the check, using its `degraded` text for the cost of skipping.
2. Ask yes/no via a real interactive question — never assume yes, never act without asking.
3. On yes: call the matching action (`wizard_actions.run_scaffold()` for `"project layer"`,
   `wizard_actions.run_graphify_install()` for `"graphify installed"`).
4. **Recheck — call `setup_wizard.wizard_status(sdlc_dir, allow_cache=False)`.** The
   `allow_cache=False` is not optional here: `wizard_status()` caches a clean result for up to an
   hour to avoid re-running expensive GitHub checks on every ordinary session start (see Task 1),
   and that cache was written, if at all, *before* this action ran — trusting it now would mean
   reporting success without ever having actually looked. Do not report success because the
   action function returned `ok: True`; confirm the check itself now passes. An action can
   "succeed" and still leave the underlying check failing (e.g. a scaffold that wrote files doctor
   still doesn't see, for a reason worth investigating rather than papering over).
5. If the recheck still fails: offer retry (repeat from step 3) or skip.
6. On no, or on skip-after-failed-retry: read the existing dismissed set via
   `setup_wizard.read_dismissed()`, add the check's `name` to it, then call
   `setup_wizard.write_dismissed()` with the updated set — never ask about this one again. It
   stays dismissed until someone edits or removes `.sdlc/state/setup-wizard-dismissed.json`.
   **There is no command that resets it**, and there is deliberately no `/agrim-doctor` reset —
   `/agrim-doctor` reports the check's real state either way, so a user who wants to revisit a
   skipped check can see it there and delete the file. Do not tell the user a reset command
   exists.

**`human_command`** (only the user can act — `gh auth login`, `gh auth refresh -s project`, or
choosing the verify command for `verify command present (enforce is on)`: never guess one for them):
1. Explain the check and its cost of staying unresolved.
2. Give the exact command. Do not run it, do not offer to — this needs the user's own browser and
   credentials, and no tool should ever act here on their behalf.
3. Ask: "done, or skip for now?" If done, recheck via `wizard_status(sdlc_dir, allow_cache=False)`
   exactly as above — the check IS verifiable even though the fix itself was manual. If skip,
   dismiss it using `read_dismissed()` and `write_dismissed()` the same way.

**`guidance_only`** (no action, sometimes no recheck either — genuine GitHub API gaps):
1. Explain the check and its cost.
2. Give the exact manual steps from the check's own `fix` text.
3. Ask "done, or skip?" then recheck via `wizard_status(sdlc_dir, allow_cache=False)` if the
   underlying doctor check is one that can observe the fix (`"board marks closed items Done"`,
   `"no open issue stranded at board Done"` — always the exact check name, never a nickname; the
   nickname is what an earlier commit had to correct in `_MODES` itself) —
   both CAN be rechecked, only the Table→Board view layout genuinely cannot (no API reads it at
   all). For that one specific case only, do not offer a recheck — say so explicitly ("there's no
   way for me to confirm this one; I'll trust you") rather than silently pretending to verify it.
4. Dismiss on skip (using `read_dismissed()` and `write_dismissed()` as in the auto_fixable mode),
   same as the other modes.

## What you must never do

- Never present a fallback without its cost in the same message.
- Never claim an action succeeded without rechecking via `wizard_status()`.
- Never attempt `gh auth login`, `gh auth refresh`, or any interactive OAuth flow yourself.
- Never silently switch `discovery.source` to `"local-goals"` as a "fix" for missing GitHub
  access — that specific fallback was explicitly rejected by the product owner as recreating a
  silent-degraded-data bug. If GitHub access is missing, say so and describe the real
  consequence; do not quietly reach for a smaller, different dataset and call it equivalent.
- Never re-ask about a dismissed check while its name is still in
  `.sdlc/state/setup-wizard-dismissed.json`.
- Never tell the user that `/agrim-doctor` (or any other command) clears a dismissal. Nothing does;
  removing the name from that file is the only route, and saying otherwise sends them to a command
  that will not do what you promised.

## After every step is resolved or dismissed

Say one line confirming the repo is ready (or which checks remain skipped and what they cost),
then proceed with whatever the user actually asked for in their original message — the wizard is
a detour before their request, never a replacement for it.

# Companion plugins

Back to the [README](../README.md).

## The `code-review` companion (optional)

Sigma has **zero plugin dependencies**. Its own `sigma-*` skills run every phase on every host
(`sigma-brainstorm`, `sigma-research`, `sigma-plan`, `sigma-plan-review`, `sigma-implement`,
`sigma-review` + `sigma-verify`, `sigma-retro`), and the always-on hook injects the 7-phase policy.

One optional plugin adds to that:

- **`code-review`** (Anthropic, `claude-plugins-official`) — the `/code-review` skill, an extra pass
  over the diff in the Review phase. It never replaces `sigma-review`.

Sigma detects it if it is in your plugin list; `/sigma-doctor` reports it as present or absent
(absent is never an error). Set `companions: "off"` in `.sdlc/config.json` to skip the probe.

### Skill selection vs platform built-ins

Sigma's skills compete with any platform built-ins or other plugins for the model's
description-based selection. **A plugin cannot disable or de-prioritize another skill** — Claude Code
has no manifest field for it, and no runtime API to detect which skills are active. So Sigma wins
selection the only way a plugin can: **sharp, task-specific descriptions**.

If a built-in ever shadows a Sigma skill you want, the fix is **on your side, not the plugin's**:
- a **standalone / project built-in** (a skill in `.claude/skills/`): set
  `"skillOverrides": {"<name>": "off"}` in `.claude/settings.json`. *(Note: `skillOverrides` does **not**
  affect plugin skills — that's a documented Claude Code limitation.)*
- **another plugin's** skill: `/plugin disable <plugin>`.

`/sigma-doctor` surfaces this as an advisory (it can't detect a live conflict — no API exists — so it
points you at these remedies rather than guessing).

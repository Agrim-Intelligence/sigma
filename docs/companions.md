# Companion plugins

Optional plugins Sigma uses when they are installed. Back to the [README](../README.md).

## Companions (optional enhancement)

Sigma ships the *spine* **and** a portable executor for every phase, so it has **zero hard plugin
dependencies** — `/plugin install sigmaloop` is seamless whether or not anything else is present, and
the kit is **never disabled** waiting on another plugin. The *execution muscle* for Phases 1, 3, 5,
and 6 runs *best on Claude* through two companion plugins:

- **`superpowers`** — `brainstorming`, `writing-plans`, `test-driven-development`, `executing-plans`,
  `requesting-code-review`, `verification-before-completion`.
- **`code-review`** — the `/code-review` skill.

**Zero action required — Sigma auto-detects them.** If a companion is **already in your plugin
list**, each phase uses its richer skill; if it isn't, that phase falls to Sigma's **portable
`sigma-*` executor**. You **install nothing** to get a working, disciplined spine — the portable
executors each carry a committed [parity review](executor-parity/) showing where each is
at par, better or lighter, so you can see what a missing companion costs.

**How resolution works, per phase:** each phase skill carries a host-aware resolution header — on
Claude *with the companion installed* it prefers the companion's richer skill; **otherwise** (companion
absent / Cursor / any other host) it uses the portable `sigma-*` executor. Either way the always-on hook
still injects the 7-phase policy. Run `/sigma-doctor` to see which companions are present (absent is
reported as "portable executor used" — never an error).

*If you happen to want the companions and don't have them,* they live in the official
`claude-plugins-official` marketplace — but this is a preference, never a setup step.

### Skill selection vs platform built-ins

Sigma's skills compete with any platform built-ins or other plugins for the model's
description-based selection. **A plugin cannot disable or de-prioritize another skill** — Claude Code
has no manifest field for it, and no runtime API to detect which skills are active. So Sigma wins
selection the only ways a plugin can: **sharp, task-specific descriptions** and a **per-skill
resolution header** (Phases 1/3/5/6 explicitly defer to their `superpowers` / `code-review` companion
when present, and own the phase otherwise).

If a built-in ever shadows a Sigma skill you want, the fix is **on your side, not the plugin's**:
- a **standalone / project built-in** (a skill in `.claude/skills/`): set
  `"skillOverrides": {"<name>": "off"}` in `.claude/settings.json`. *(Note: `skillOverrides` does **not**
  affect plugin skills — that's a documented Claude Code limitation.)*
- **another plugin's** skill: `/plugin disable <plugin>`.

`/sigma-doctor` surfaces this as an advisory (it can't detect a live conflict — no API exists — so it
points you at these remedies rather than guessing).

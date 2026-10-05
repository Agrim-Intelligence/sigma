---
name: sigma-velocity
description: Estimate calendar time from measured repo commit and PR throughput. Use for work sizing or /sigma-velocity.
allowed-tools: Bash(python3 *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-velocity

Detailed selection triggers: [selection](references/selection.md).

Size from measurement, not intuition. Estimates default to a generic "old pace" that's often ~10× off
at AI-agent velocity; this measures the repo's real recent throughput from git and converts the
estimate at that rate. Git-only, zero-dep.

**Never state a calendar band without running `measure`/`estimate` first.** A number this skill
did not itself produce is a guess wearing a measurement's clothes — if the estimate cannot be
grounded (no recent history, below), say that plainly instead of quoting a band anyway.

- **Measure the pace:** `python3 "${CLAUDE_SKILL_DIR}/scripts/velocity.py" measure . 14`
  → commits/day + merges-as-PRs/day over the trailing window.
- **Ground an estimate:** `python3 "${CLAUDE_SKILL_DIR}/scripts/velocity.py" estimate <N> . 14`
  → converts N PR-sized units into a calendar band at the measured rate.

Use it at **Plan** (Phase 3) and whenever a sizing / lane decision hinges on *"is this really weeks?"*:
attach the measured calendar band so a high unit-count isn't mistaken for a long calendar. Pick the
rate that fits how the repo lands work — **merges/day** if you PR-merge, **commits/day** if you commit
straight to a branch (the helper falls back to commits when there are no merge-commits). **No recent
history → say the estimate can't be grounded yet; widen the window, don't guess.**

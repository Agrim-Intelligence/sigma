---
name: agrim-time
description: Report observed active and background time by goal, unit, skill, or session from the timing store. Use for duration questions or /agrim-time.
allowed-tools: Bash(python3 *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-time

Detailed selection triggers: [selection](references/selection.md).

Working time, measured. Every phase boundary and every verify run records its own duration to a
local store that no config key gates, so these figures exist on a stock install — unlike the
ledger and the action log, which are both opt-in and both off by default.

**NEVER state how long something took without running one of the commands below.** A duration
recalled from a transcript, inferred from timestamps in the conversation, or estimated from the
amount of work done is a guess wearing a number's clothes. The store is the only thing that knows.

- **One goal:** `python3 "${CLAUDE_SKILL_DIR}/scripts/time_report.py" goal <sdlc_dir> <goal>`
  → `active` (sum of that goal's phase durations), `background` (verify, flake and revert runs),
  and `effort` (the two added).
- **One unit of work:** `python3 "${CLAUDE_SKILL_DIR}/scripts/time_report.py" unit <sdlc_dir> <unit> <owner/name>`
  → the same figures totalled across that unit's goals in THIS repository. `<owner/name>` is
  this repository's key in the feature registry and is REQUIRED: the rollup is single-repo by
  construction and cannot know which repository it is running in — left out, it refuses rather
  than misfile this repository's own goals as "recorded elsewhere".
- **One session, by process — no goal needed:**
  `python3 "${CLAUDE_SKILL_DIR}/scripts/time_report.py" session <sdlc_dir> [<session-id>]`
  → time in each skill this session (`agrim-dossier`, `agrim-plan`, `agrim-implement`, …), the
  session total, and the script-time floor beside it. The current session by default — and if
  that default answers "not recorded" mid-session, the session id did not reach this shell:
  pass it explicitly rather than concluding nothing was recorded.

## Reading a session honestly

- **`precise (turn hooks)`** means every assistant turn was timed and attributed to the skill
  active at the time. **`floor only`** means this host has no turn hooks and only scripts that
  time themselves were seen — a floor that cannot see `agrim-plan` or `agrim-implement` at all
  (they invoke no scripts) and excludes interpreter start-up. On a floor-only session the total
  reads **"not measurable"**; NEVER report the script figure as the total.
- **`scripts` is never added to `total`.** Scripts run inside turns.
- **`(no skill)`** is work done before any skill was invoked — real work, shown, not
  unattributable. A skill stays active until the next one is invoked.
- **`all hookless activity on <date>`** is a shared bucket, not a session: several hookless
  sessions on one day merge into it. Say so.

## Reading the output honestly

Four things the output says that you MUST carry into any sentence you write about it:

- **`active` is a LOWER BOUND, never an exact figure.** Idle is excluded by construction — time
  parked, blocked or awaiting a human sits inside no phase — but so is real work in two cases: a
  phase parked and resumed is measured only from its resume, and a phase that crashed without
  ending has no interval at all.
- **`effort` is not wall-clock and MUST NEVER be described as elapsed time.** A background run
  dispatched during a phase is already inside that phase's duration, so adding them double-counts
  deliberately and the total can exceed the real elapsed time.
- **"not recorded" does not mean zero.** A goal whose work was never observed reports that it was
  never observed. ALWAYS repeat that distinction rather than rendering it as `0m`.
- **A unit total covers this repository only.** The feature registry scopes a unit's goals per
  repo while the store is local to one checkout, so goals driven elsewhere are named as uncounted
  rather than silently dropped. NEVER present the figure as the unit's whole cost when that line
  is present.

A goal that finished before this store existed is reconstructed by pairing the ledger's own phase
timestamps, and is labelled `derived` at whole-second precision. Say "derived" when you report it.

Use it at the Retrospective phase, when a goal or unit lands, or whenever someone asks where the
time went. It writes nothing and reads no network.

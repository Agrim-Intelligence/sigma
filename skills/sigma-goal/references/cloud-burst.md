# Cloud burst — the launch prompt and a per-repository readiness check

A burst is several `/sigma-goal <N> --land` runs started at once, one Claude Code cloud container per issue
([land](land.md)). It goes wrong in two places the run itself cannot fix: the prompt the person types, and a
repository that is not ready. Both are settled before the first container starts. Source: the first real cloud
`--land` wave on a consumer repository (Sigma 1.0.26), where launch-prompt rules caused parks and refusals and every
goal parked for hours on a red base.

## The launch prompt

> **The launch prompt is `/sigma-goal <N> --land` plus facts about the environment. Nothing else.**

A fact is something the run cannot read for itself: which services the cloud setup starts, that a token is absent, a
branch name. It is not a rule. Each of these was written into a launch prompt and each caused a park or a refusal:

| Do not put in the prompt | What happened |
|---|---|
| Gate or config edits (for example setting `work.ci_required: false` locally) | Refused as a CI bypass. The gate is the repository's, not the prompt's. |
| Any action on another pull request (for example closing it) | Refused as an external write. A `--land` run writes to its own goal only. |
| "Stop on any refusal", "never use `--no-tests`" | Parked two UI-only goals that could legitimately have proceeded. Sigma decides parks, by the endings in [land](land.md). |
| A goal-specific verify command | The next container's trust could not run it. The verify command is the repository's `verify.command`, approved once per environment (`docs/cloud-sessions.md`). |

If the prompt needs a rule, the rule belongs in `.sdlc/config.json` or the repository's docs, where every run reads
the same one.

## Readiness check, per repository, before a burst

Answer all four. A "no" is fixed first; starting the burst anyway parks every goal for the same reason.

1. **If CI is paused, the dependency-update bot is paused.** A bot that keeps opening pull requests while CI is off
   gets them merged unverified; the base goes red and every goal parks behind it. Pause the bot's version updates
   (dependabot: `open-pull-requests-limit: 0` on each `updates` entry; renovate: `"enabled": false`), or turn CI back on.
2. **The cloud setup starts every service the test suite needs.** A database, a queue, a cache: a container begins
   empty, and a suite that needs one fails there with no code fault.
3. **The tests do not depend on ambient tokens or databases.** A token set in a laptop shell or a database that happens
   to be running locally is absent in the container. The suite either skips cleanly without it or the setup supplies it.
4. **The base branch passes `verify.command`.** Run it on the base itself. A red base fails every goal's verify the
   same way, and each run then parks or fails on a fault that is not its own.

## What `/sigma-doctor` reports

Two informational rows, never failures, read from local files only (no network call beyond what the doctor already
makes):

- **Check 1, partly:** a dependency-bot config (`.github/dependabot.yml`, `renovate.json` and its usual variants) with
  version updates enabled, while `work.ci_required` is true and no verify result was recorded here in the last 14 days.
  The doctor cannot read CI runs without the network, so the recorded-verify age is a proxy and the row says so. A bot
  paused as in check 1 gives no row.
- **Check 4, partly:** `last recorded verify: exit <n>, <age> ago (goal <id>)`, when `verify.command` is set. That is
  the newest goal verify on this checkout, not a run of the base; running `verify.command` on the base is still yours.

Checks 2 and 3 are not visible to the doctor: they depend on the cloud environment's setup script and secrets.

UNMEASURED: no real cloud container or consumer repository has been checked with these rows.

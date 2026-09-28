---
name: agrim-slack
description: Start, check, or stop the inbound Slack command listener. Use for bot runtime requests or /agrim-slack; app setup is manual.
allowed-tools: Bash(python3 *), Bash(touch *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-slack

Detailed selection triggers: [selection](references/selection.md).

The inbound Slack commands listener — `--drift`, `--merge`, `--unsafe-merge`, `--list`, `--rebase`,
`--help` typed straight into a Slack channel. Full design and one-time setup (Slack app, Socket
Mode token, channel, `.sdlc/config.json`) live in
[`../agrim-loop/SLACK_COMMANDS.md`](../agrim-loop/SLACK_COMMANDS.md) — read that FIRST if
`slack_commands` isn't configured yet; this skill only covers running it once it is.

The script lives in the loop skill. Resolve its directory once, then call it — nobody types the
python path themselves:

```bash
LS="${CLAUDE_SKILL_DIR}/../agrim-loop/scripts"
```

## Start it (or restart it if it died)

```bash
python3 "$LS/slack_commands_listen.py" ensure .sdlc
```

One command, safe to run any time, by anyone: starts the listener if nothing is running, replaces
it if the prior instance is genuinely dead (a stale heartbeat), or reports "already running —
nothing to do" and touches nothing if it's already live. Launches it detached, so it keeps running
after this command finishes — no terminal, `tmux`, or supervisor needed for a quick start. For a
machine that should keep it running across reboots, see SLACK_COMMANDS.md's own systemd/launchd
example instead.

## Is it actually running?

```bash
python3 "$LS/slack_commands_listen.py" status .sdlc
```

One line: `running`, `not running`, `DEAD` (a stale heartbeat — probably crashed), `MISCONFIGURED`,
or `disabled`. This is the same liveness `/agrim-doctor` already reports — run `/agrim-doctor` for
the full picture alongside everything else it checks.

## Stop it

```bash
touch .sdlc/state/slack-commands.stop
```

The listener notices the stop-file, disconnects cleanly, and removes its own pidfile/heartbeat/lock
on the way out.

## What this does NOT do

**Not automatic supervision.** `ensure` only acts when something — a person, or this skill —
actually invokes it; nothing in the loop starts it for you on its own yet (unlike the ledger
watcher, which `loop.py` starts automatically on its own trigger points). If you want it kept alive
without remembering to run this by hand, put `ensure` on a schedule yourself (a `launchd`/`systemd`
timer, or a cron entry calling it every few minutes) — SLACK_COMMANDS.md's "Supervising it day to
day" section has the worked example. Wiring real auto-start into every loop trigger the way the
ledger watcher works is a genuinely separate, bigger design question (a held-open Slack connection
behaves differently from a tick-based watcher) — SLACK_COMMANDS.md's own Doubt D-5 names it as
open, not forgotten.

**Not the one-time setup.** Creating the Slack app's Socket Mode token, inviting the bot to a
channel, and setting `.sdlc/config.json`'s `slack_commands` block all need a human with access to
the Slack workspace — SLACK_COMMANDS.md walks through it once, and it's done for the whole team
after that (everyone reuses the same listener/credentials, or each runs their own — either way,
`ensure`/`status` above are all any of them need day to day).

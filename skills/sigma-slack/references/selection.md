# sigma-slack selection details

description: "Start, check, or stop the inbound Slack commands listener (Epic #2335) through the plugin instead of a raw python path. Use when the user runs /sigma-slack, asks how to start \"the slack bot\"/\"the slack thingy\", wants to know if it's actually running, or is setting a teammate up to use it. Do NOT use it for the one-time Slack app/channel/token setup — that needs a human clicking through Slack's own web UI, not a command."
allowed-tools: Bash(python3 *), Bash(touch *)

These fuller triggers are kept outside the always-listed description. Read the
skill's main instructions before acting.

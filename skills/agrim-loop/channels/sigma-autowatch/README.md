# sigma-autowatch channel (#1322)

The CLI/Channels adapter for Sigma's ledger autowatch (#1318). A minimal, one-way [Claude
Code channel](https://code.claude.com/docs/en/channels) that forwards a ledger detection into an
already-open, persistent `claude` session, which then runs `autowatch.py tick` in response.
`autowatch.py` — not this plugin — evaluates every real safety precondition and decides whether
anything actually drives `/agrim-loop`. This plugin only wakes the session up.

For the full picture — including the Desktop adapter, if that fits your workflow better — see
[`skills/agrim-loop/AUTOWATCH.md`](../../AUTOWATCH.md).

## Read this before you set it up: a real, standing cost, not a one-time step

Channels are a [research preview](https://code.claude.com/docs/en/channels#research-preview)
feature. During the preview, **only Anthropic's own curated channel plugins can register normally**
(Telegram, Discord, iMessage, fakechat). A custom-built channel like this one — not on that list —
can only be loaded via the `--dangerously-load-development-channels` flag, and that requirement
does not go away after the first run:

- **Every single launch** needs that flag, not just the first one.
- **Every single launch** pops a full-screen "I am using this for local development" warning
  dialog you must click through.
- There is no path off this for a personal account outside a Team/Enterprise org — getting a
  plugin onto Anthropic's curated allowlist needs an Anthropic partner contact; a Team/Enterprise
  org admin can allowlist it via `allowedChannelPlugins`, but that doesn't apply to an individual
  account.

This is a disclosed, accepted trade-off for anyone who sets this up, not a bug to be fixed later —
confirmed directly against Claude Code's own channels-reference docs (2026-08-18). If that
friction isn't worth it for you, `ledger.autowatch.surface: "desktop"` (the config default) uses
the Desktop scheduled-task adapter instead, which has no equivalent restriction.

## One-time setup

1. **Install Bun** (the channel server's runtime): https://bun.sh
2. **Install this plugin's dependencies**, once, from this directory:
   ```
   cd skills/agrim-loop/channels/sigma-autowatch
   bun install
   ```
3. **Register the server** with Claude Code. Add to your project's `.mcp.json` (create it at the
   repo root if it doesn't exist yet):
   ```json
   {
     "mcpServers": {
       "sigma-autowatch": {
         "command": "bun",
         "args": ["skills/agrim-loop/channels/sigma-autowatch/webhook.ts"]
       }
     }
   }
   ```
4. **Launch with the development-channels flag**, from the repo root:
   ```
   claude --dangerously-load-development-channels server:sigma-autowatch
   ```
   Select "I am using this for local development" at the warning dialog, then "Use this MCP
   server" at the first-run consent prompt. Keep this session open — events only arrive while it
   is (see the docs' own "always-on" note: run it in a background process or a persistent terminal
   for continuous coverage).
5. **Point Sigma at it.** In `.sdlc/config.json`:
   ```json
   "ledger": {
     "autowatch": {
       "enabled": true,
       "surface": "cli",
       "channel_webhook_url": "http://127.0.0.1:8790"
     }
   }
   ```
   The port must match `SIGMA_AUTOWATCH_CHANNEL_PORT` if you've overridden the default (8790).

6. **Verify it's wired up**: `python3 skills/agrim-doctor/scripts/doctor.py check .sdlc` should
   report `autowatch adapter wired up: OK` once `channel_webhook_url` is set.

From here, `watch_daemon.py` (already running once ledger is enabled) POSTs to this server the moment it
detects a new mention/assignment/blocker in scope; this server forwards it into your open session
as a `<channel>` event, and the session runs `autowatch.py tick --issue N` in response.

## Restarting after a machine reboot

The `--dangerously-load-development-channels` session does not survive a reboot or a closed
terminal — relaunch it (step 4) to resume coverage. There is no persistent-service/daemon mode for
a development channel during the research preview.

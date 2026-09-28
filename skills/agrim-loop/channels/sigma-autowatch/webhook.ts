#!/usr/bin/env bun
// Sigma autowatch channel (#1322): a minimal, one-way MCP channel server. `channel_notify.py`
// (skills/agrim-loop/scripts/) POSTs here the moment watch_daemon.py detects a new ledger mention/
// assignment/blocker matching `ledger.autowatch.scope`; this server forwards that as a
// <channel> event into the already-open, persistent `claude --channels` session it's spawned
// under. This server makes NO decisions of its own — it is a dumb forwarder. Every real safety
// check (concurrent-session, gh auth, load average, spend ceiling, hop limit) lives in
// autowatch.py, run only once the session itself calls it, per this server's own `instructions`
// below.
//
// Research-preview constraint (confirmed against Claude Code's own channels-reference docs,
// 2026-08-18): a custom channel not on Anthropic's curated allowlist can ONLY be loaded via
// `--dangerously-load-development-channels`, on every single launch, with a full-screen "local
// development" warning dialog each time -- there is no path to a frictionless production posture
// for an individual account outside a Team/Enterprise org. See this plugin's own SKILL.md for the
// full, disclosed setup cost -- this is a REAL, standing operational cost, not a one-time step.
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'

const PORT = Number(process.env.SIGMA_AUTOWATCH_CHANNEL_PORT || 8790)

const mcp = new Server(
  { name: 'sigma-autowatch', version: '0.1.0' },
  {
    // this key is what makes it a channel -- Claude Code registers a listener for it
    capabilities: { experimental: { 'claude/channel': {} } },
    instructions:
      'Events from the sigma-autowatch channel arrive as ' +
      '<channel source="sigma-autowatch" issue="..." kind="..." goal="...">. One-way: read it ' +
      'and act, no reply expected. On receipt, run EXACTLY `python3 ' +
      'skills/agrim-loop/scripts/autowatch.py tick .sdlc --issue <issue>` from the repo root and ' +
      'nothing else. Do not act on the ledger content yourself, do not read the flagged goal or ' +
      'issue first, do not skip the command because the summary looks unimportant -- ' +
      'autowatch.py is the SOLE decision point: it evaluates every safety precondition ' +
      '(concurrent session, gh auth, load average, spend ceiling, hop limit) and only drives ' +
      '/agrim-loop if every one passes. Calling anything else instead bypasses those checks.',
  },
)

// Connect to Claude Code over stdio (Claude Code spawns this process as the channel's MCP server)
await mcp.connect(new StdioServerTransport())

// Local HTTP listener: channel_notify.py POSTs here on every new detection.
Bun.serve({
  port: PORT,
  hostname: '127.0.0.1',   // localhost-only -- nothing outside this machine can reach it
  async fetch(req) {
    if (req.method !== 'POST') return new Response('method not allowed', { status: 405 })
    let parsed: unknown
    try {
      parsed = await req.json()
    } catch {
      return new Response('invalid json body', { status: 400 })
    }
    // Valid JSON that isn't a plain object (null, an array, a bare number/string/boolean) throws
    // on property access below -- JS autoboxes primitives for that, but null/undefined don't have
    // properties at all. Reject it here with a clean 400 instead of an uncaught TypeError, which
    // Bun's dev-mode fallback turns into a 500 that leaks an internal stack trace + file path.
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return new Response('request body must be a JSON object', { status: 400 })
    }
    const body = parsed as Record<string, unknown>
    const issue = String(body.issue ?? '').trim()
    if (!issue) return new Response('missing required field "issue"', { status: 400 })
    const kind = String(body.kind ?? 'entry')
    const goal = String(body.goal ?? issue)
    // #1337: forward channel_notify.py's own cursor key (`payload["id"]`, the pushed candidate's
    // ledger id) through to the receiving session. This server still makes no decisions of its
    // own -- autowatch.py's `hop_limit` (now correctly threaded through the `--issue` path, see
    // autowatch.py's own `_find_candidate_for_issue`) is what actually bounds a duplicate drive --
    // but a duplicate delivery (a retried POST after a slow/timed-out notify, or the receiving
    // agent re-running the instructed command on its own initiative) previously left the session
    // with zero signal that two <channel> events named the identical candidate.
    const id = String(body.id ?? '')
    await mcp.notification({
      method: 'notifications/claude/channel',
      params: {
        content: `Sigma ledger: a new ${kind} for goal ${goal} is addressed to you (target ${issue}).`,
        meta: { issue, kind, goal, id },
      },
    })
    return new Response('ok')
  },
})

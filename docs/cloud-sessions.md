# Claude Code cloud sessions and GitHub GraphQL: detection and reporting only

Status: slice 1 of #801. This page describes what Sigma can now DETECT and REPORT. It does not
claim that `/sigma-loop` works in a Claude Code cloud session: that has not been measured (no cloud
session was available), and it is the follow-up smoke run. REST migration is in progress, not complete.

## What the proxy blocks

A cloud session puts a proxy in front of `api.github.com`. `skills/sigma-loop/scripts/gh_session.py`
recognises three shapes: two confirmed in #78 (a pinned-operations GraphQL allowlist, and "GitHub access
is not enabled for this session"), and a third, from #801's text, "GitHub GraphQL is not available from
Claude Code sessions" (HTTP 403). The third wording is taken from the issue, not captured live.
`gh_session.graphql_unavailable()` recognises it; it is not yet folded into `proxy_session_block`.

## The capability check

`skills/sigma-loop/scripts/gh_api.py` owns it: `graphql_available(...)`. Precedence, first hit wins:
`SIGMA_GH_GRAPHQL=off|on` (operator override), then `CLAUDE_CODE_REMOTE=true|1` (inferred from env, not
proven), then an opt-in cached probe, then "available". The probe runs once, never retries, and only
when a caller passes `probe=True` and a `run`; the cache (`.sdlc/state/gh-capability.json`, 900 s TTL)
holds no secrets and a stale "unavailable" self-heals. `/sigma-doctor` never probes.

## What doctor reports

When the check says unavailable, `/sigma-doctor` prints one advisory row,
`GitHub GraphQL unavailable (cloud proxy)`, marked OK (a permanent condition the user cannot fix, not a
MISSING gap). It names the features turned off or degraded: board/Projects mirroring, `gh pr merge --auto`,
`timelineItems` (blocker/dependency edges), and `gh issue|pr` via GraphQL until migrated (slices 2-4).

## What this does NOT do

- No caller uses the REST helper ops yet; nothing in the product exercises the probe or the cache.
- `/sigma-loop` is NOT supported in cloud sessions. REST `merge` has no auto-merge, and none is emulated.
- `create_issue` in `gh_api.py` bypasses the feature-label refusal in `GitHubSource._run`; the migration
  slice that moves callers onto it must preserve that refusal first.

## The ratchet

`tests/test_no_direct_gh.py` stops direct `gh issue|pr|project|label` call sites from growing
(baseline 101 sites in 21 files; it only goes down). Run
`$HOME/.sigma-venv312/bin/python -m pytest tests/test_no_direct_gh.py`
(generic form: `python -m pytest tests/test_no_direct_gh.py`).
It covers list literals only. Shapes it CANNOT see: string-form or shell-string calls
(`"issue view".split()`); argv built incrementally (`args += [...]`, `.append`, `.extend`); lists whose first
element is a variable; positional-string wrapper calls (`_gh("issue", "view")`); skill prose (`.md`, `.tmpl`);
and shell scripts (`skills/**/*.sh`, `hooks/*.sh`; 9 files, none mentions `gh` today, nothing keeps it so).
So it guarantees "no new list-literal gh call", not "no new gh call".

## Follow-ups

Remaining migration (issue read and write ops, PR ops, Projects v2, skill prose, status line, the cloud
smoke run, and wiring the probe and `graphql_unavailable` into real callers) is filed as queued issues,
not done here.

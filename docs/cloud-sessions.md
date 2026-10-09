# Claude Code cloud sessions and GitHub GraphQL: detection and reporting only

Status: slice 1 of #801 (detection and reporting) plus slice 2a of #895 (sources.py's issue READS go
REST first). It does not claim that `/sigma-loop` works in a Claude Code cloud session: that has not
been measured (no cloud session was available), and it is the follow-up smoke run. REST migration is in
progress, not complete: slices 2b-7 (other modules' reads, writes, PRs, board) remain.

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
`timelineItems` (blocker/dependency edges), and `gh issue|pr` via GraphQL until migrated (slices 2b-4).

## REST-first issue reads (#895 slice 2a)

`gh_api.read_issue(run, number, fields, ...)` returns one issue in `gh issue view --json <fields>` shape.
`sources.py`'s seven issue reads use it: `fetch_comments`, `release`, `complete`, `fetch_author`,
`fetch_body_labels`, `fetch_comments_strict`, `append_to_body`. Policy:

- REST first (`api repos/<o>/<r>/issues/<n> --method GET`, plus comment pages only when comments are
  asked for). A comment read costs `1 + ceil(n/100)` requests for all comments, at most 3 for a tail of
  100 or fewer. A comment posted mid-read can shift the tail by one, so `acceptance.py` may post a
  duplicate note (ceiling).
- At most ONE `gh issue view` fallback, and only when REST fails with a rate limit (429, or a 403 whose
  text says rate limit or abuse), a 5xx, or a transport error. Never on 401/403-permission/404/410/422,
  never on a cloud proxy block, and never when `graphql_available(env)` says unavailable
  (`CLAUDE_CODE_REMOTE`, `SIGMA_GH_GRAPHQL=off`; env only, no probe). No retry loop.
- Breaker (`.sdlc/state/gh-rest-breaker.json`): after 3 consecutive fallback-class REST failures, reads
  skip REST for `SIGMA_GH_BREAKER_COOLDOWN` seconds (default 300) when the fallback is available. Each
  failed probe after the cool-down re-opens it; any REST success resets it. Corrupt or missing = closed.
  In a cloud session it still counts, and its stderr line says "REST failing" without promising a
  fallback. Concurrent workers share one file with a fixed `.tmp` name: a torn write reads as closed
  and self-heals; lost increments only delay opening (not tested).
- Fallback log (`.sdlc/state/gh-fallback.json`): the last 200 events, `{ts, op, number, kind, status,
  fell_back, why}` only, never error text, argv or body. Liveness: the newest entry's age, plus one
  stderr line per breaker open and per non-breaker fallback.
- Both files are written only when the caller passes an `sdlc_dir`, and only after `state.refuse_symlinks`
  vets each file and its `.tmp` (#708); a symlink turns persistence off for that call with a `REFUSED`
  note. `fetch_comments` passes none, so during a REST outage each call costs REST plus fallback.
- Shape: REST `state`/`state_reason` are upper-cased; comment `id` is `node_id`, equal to the GraphQL id,
  so dedup survives a REST/fallback switch. A REST Bot issue author `<slug>[bot]` reads `app/<slug>`, as
  gh shows it. Comment authors are NOT mapped yet: gh spells a bot comment author as the bare `<slug>`
  while REST says `<slug>[bot]`. Measured once on smanwatkarcodes/skills-introduction-to-git#1 on
  2026-10-09: issue author gh `app/github-actions`, REST `github-actions[bot]`; comment author gh
  `github-actions`, REST `github-actions[bot]`; REST comment `node_id` `IC_kwDOVCU5ZM8AAAABamNa1g` equal
  to gh's comment `id`. Consequence: when a claimant is a GitHub App identity, `comment_watch` can surface
  its own comment as foreign. Human claimants are unaffected. Mapping comment authors is a follow-up.

Unmeasured: the real 429, secondary-limit 403 and 5xx stderr text and the Go transport wording are
inferred and tested with fixtures only; any live cloud run; live request cost. The bot spellings were
measured on one public issue, not on this repo (it has no bot-authored issue or comment).

## What this does NOT do

- Only `read_issue` (above) is wired to callers; the other REST helper ops have none yet, and nothing
  in the product exercises the probe or the cache.
- `/sigma-loop` is NOT supported in cloud sessions. REST `merge` has no auto-merge, and none is emulated.
- `create_issue` in `gh_api.py` bypasses the feature-label refusal in `GitHubSource._run`; the migration
  slice that moves callers onto it must preserve that refusal first.

## The ratchet

`tests/test_no_direct_gh.py` stops direct `gh issue|pr|project|label` call sites from growing
(baseline 101 sites in 21 files at slice 1, 94 after #895 slice 2a; it only goes down). Run
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

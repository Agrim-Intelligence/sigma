# Claude Code cloud sessions and GitHub GraphQL: detection and reporting only

Status: slice 1 of #801 (detection and reporting) plus slices 2a, 2b and 2c of #895 (single-issue READS go
REST first: sources.py, then ten more `issue view` sites; then ten `issue list` sites). It does not claim that `/sigma-loop` works in a Claude Code cloud session: that has not
been measured (no cloud session was available), and it is the follow-up smoke run. REST migration is in
progress, not complete: slices 3-7 (writes, PRs, board) remain.

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
`timelineItems` (blocker/dependency edges), and `gh issue|pr` via GraphQL until migrated (slices 3-4).

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
  while REST says `<slug>[bot]`. Measured once on a public bot-filed GitHub Skills exercise issue on
  2026-10-09: issue author gh `app/github-actions`, REST `github-actions[bot]`; comment author gh
  `github-actions`, REST `github-actions[bot]`; REST comment `node_id` `IC_kwDOVCU5ZM8AAAABamNa1g` equal
  to gh's comment `id`. Consequence: when a claimant is a GitHub App identity, `comment_watch` can surface
  its own comment as foreign. Human claimants are unaffected. Mapping comment authors is a follow-up.

### Slice 2b: ten more `gh issue view` sites

The same `read_issue` now serves: `auto_unpark._ref_is_open` (state, stateReason), `blockers._state`,
`promote._read_state`, `unpark._fetch_issue`, `reconcile` (three re-reads in `apply_closed_state_actions`,
`apply_proposal`, `apply_open_issue_promotions`), `triage._fetch_issue_state` and
`triage._resolve_missing_picks`, and `brainstorm._fetch_issue_live`. Added mappings: `number`, `title`, and
`state` = `MERGED` when REST says `closed` with `pull_request.merged_at` set (`gh issue view` says
MERGED; a closed-unmerged PR stays CLOSED with no stateReason, so a merged-PR blocker reads resolved and a
closed-unmerged one does not). Each site keeps its own `except` arm (auto_unpark: open; blockers/promote:
refuse; reconcile: skipped "could not re-read"; triage: skip/None; unpark and brainstorm: the caller's
arm). The first three are the `sources.GitHubSource._read_issue` path with its `sdlc_dir` breaker and log;
`triage._resolve_missing_picks` and `brainstorm._fetch_issue_live` have no source and no `sdlc_dir`, so
they write no breaker or log, and the injected `run` is also the fallback runner: during a REST outage
each such call costs REST plus one fallback and a stderr line. Cost per read is one REST request (plus
comment pages for `unpark`), the same count as one GraphQL view; not measured at scale.

Live read on 2026-10-10: `gh api repos/Agrim-Intelligence/sigma/issues/928` returned `state: closed`,
`state_reason: null`, `pull_request.merged_at: 2026-10-09T17:46:12Z`, which is the shape the MERGED
mapping keys on. Unmeasured: a real cloud session; bot-author spelling beyond the single public sample
(promote reads `author`); call counts at scale; comment-heavy `unpark` latency; an old closed issue whose
REST `state_reason` is null (it would read as unresolved, the safe direction: a stale blocker would not
auto-unpark).

Unmeasured: the real 429, secondary-limit 403 and 5xx stderr text and the Go transport wording are
inferred and tested with fixtures only; any live cloud run; live request cost. The bot spellings were
measured on one public issue, not on this repo (it has no bot-authored issue or comment).

### Slice 2c: ten `gh issue list` sites

`gh_api.list_issues_gh(run, fields, ...)` pages `gh api repos/{o}/{r}/issues` (GET, 100 per page) and
returns rows in `gh issue list --json` shape; on a rate limit, 5xx or transport failure it makes ONE
`gh issue list` fallback (never in a cloud session or with `SIGMA_GH_GRAPHQL=off`, never on a client
error, proxy block, empty or malformed output, or a page that is not a JSON list). The breaker and
fallback log are the ones `read_issue` uses (`op: issue_list`, `number: null`), written only when an
`sdlc_dir` exists. Sites: `auto_unpark` (the parked/blocked loop and the `sdlc:blocking` read; any error
keeps `complete=False` / `set()`, so a failed read never removes labels), `reconcile` census,
`assign._active_members` (a non-list page now lands in its "could not rank active repo members" arm), and
six `doctor` scans. `status.py:174` stays direct by design, and the `issue edit` in `assign.py` is a write.

Direction rule: `gh issue list` is newest-created-first, so every site asks `sort=created&direction=desc`
(a board over its cap keeps its NEWEST rows); `doctor._dependency_marker_scan` asks `updated`/`desc`
(its `max_issues` slice depends on it). Other orders are refused with a ValueError, because the fallback
argv cannot express them. Doctor stays READ-ONLY: it passes no `sdlc_dir`, so it writes no
breaker or log state even after a fallback-class failure; its runner never raises (a failure is a falsy
`_RawFailure`), so a raising wrapper built on `_gh_runner` turns it into an exception and the census
read is routed through it too, otherwise an outage would read as an empty board.

Cost, DERIVED and not measured: a list is `ceil(n/100)` REST requests instead of one GraphQL search (REST's
issues endpoint also returns pull requests, which are dropped client-side, so a label-less read such as
assign's `--state all` sample of 50 can take `ceil((issues + PRs)/50)` requests on a PR-heavy repo). Because the
breaker is shared, one rate-limited census tick (up to 50 pages) can count toward its 3-failure threshold and
turn REST single-issue reads off for the cooldown (300 s default). A
5000-cap board read (auto_unpark, reconcile's per-tick census) is up to 50 sequential requests, to be
compared with `SIGMA_WATCH_CALL_TIMEOUT` (120 s); doctor worst case is ~45 s per site and ~135 s for the
multi-state scan (from the 15 s per-call timeout; before ~15 s per site). Unmeasured: a real Claude Code
cloud session; real request counts and latency for a 5000-cap board; doctor latency; that gh's default
newest-created order still holds (assumed, not re-verified); the GraphQL points saved (#1829's ~2600 per
search, not re-measured here); label names containing a comma (cannot be sent; `sdlc:*` never do).

## What this does NOT do

- Only `read_issue` and `list_issues_gh` (above) are wired to callers; the other REST helper ops have none yet, and nothing
  in the product exercises the probe or the cache.
- `/sigma-loop` is NOT supported in cloud sessions. REST `merge` has no auto-merge, and none is emulated.
- `create_issue` in `gh_api.py` bypasses the feature-label refusal in `GitHubSource._run`; the migration
  slice that moves callers onto it must preserve that refusal first.

## The ratchet

`tests/test_no_direct_gh.py` stops direct `gh issue|pr|project|label` call sites from growing
(baseline 101 sites in 21 files at slice 1, 94 after #895 slice 2a, 84 after slice 2b, 74 after slice 2c; it only goes down). Run
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

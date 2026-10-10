# Claude Code cloud sessions and GitHub GraphQL: detection and reporting only

Status: slice 1 of #801 (detection and reporting) plus slices 2a, 2b and 2c of #895 (single-issue READS go
REST first: sources.py, then ten more `issue view` sites; then ten `issue list` sites), slice 3a (issue
WRITES) and slice 4a-1 (seven PR READS). It does not claim that `/sigma-loop` works in a Claude Code cloud session: that has not
been measured (no cloud session was available), and it is the follow-up smoke run. REST migration is in
progress, not complete: the merge gate and the other PR reads (4a-2), PR writes (4b/4c) and the board remain.

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
six `doctor` scans. `status.py:174` stays direct by design, and the `issue edit` in `assign.py` was a write (moved in slice 3a).

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

## Write operations (#895 slice 3a)

Seventeen issue WRITE list-literal sites moved from `gh issue ...` argv to REST helpers in `gh_api.py`
(`comment_issue`, `create_issue`, `edit_issue(body=)`, `close_issue`, `add_labels`, `remove_label`,
`add_assignees`), reached only through `GitHubSource._issue_*` wrappers. Callers: dossier and blockers
(comment, failure swallowed as before), promote and unpark (comment, body edit), assign and triage
(assignee, add/remove label), and in `sources.py` `release`, `complete`, `append_to_body`,
`create_dependency` and the priority-label mirror. Every caller keeps its own `except` arm, return value
and message.

Fallback policy, one table in code (`gh_api.WRITE_POLICY`, read by `_write_fallback_ok`; the cases below
are its ids and `test_cloud_sessions_doc_lists_every_write_policy_case` fails if one is missing here). A REST
write falls back to the matching `gh issue ...` at most ONCE and is never retried; the fallback's own error
propagates. Idempotent writes are `add_labels`, `remove_label`, `close_issue`, `edit_issue(body=)` and
`add_assignees`; non-idempotent are `comment_issue` and `create_issue`.

| case id | meaning | falls back (idempotent) | falls back (comment, create) |
|---|---|---|---|
| `primary_rate_limit` | HTTP 429, or 403 whose gh stderr says rate limit and not secondary/abuse | yes | yes |
| `secondary_rate_limit` | 403 or 429 whose stderr says secondary or abuse | no | no |
| `server_5xx` | HTTP 500-599: the write may have committed | yes | no |
| `transport_ambiguous` | timeout or connection failure after the request was sent | no | no |
| `auth_401` | HTTP 401 | no | no |
| `not_found_404` | HTTP 404 | no | no |
| `invalid_422` | HTTP 422, a dropped assignee, a team slug | no | no |
| `permission_403` | HTTP 403 that is not a rate limit | no | no |
| `proxy_block` | cloud proxy / GraphQL-unavailable text | no | no |
| `refused` | the feature-label guard (`GhApiError(kind="refused")`) | no | no |
| `other_unparsed` | anything else, a malformed 2xx body, a failure with no gh stderr | no | no |

No fallback at all in a cloud session or with `SIGMA_GH_GRAPHQL=off`. Classification of a write reads the
structured status and gh stderr (`.hint`) ONLY, never `str(exc)` (which embeds argv and so the comment or
issue BODY): a body saying "rate limit" or "timeout" is never read as a fallback-eligible failure. The
secondary-limit and 429 wording is INFERRED from documented GitHub behaviour, not captured live. Write
fallbacks share the bounded `state/gh-fallback.json` log with reads (cap 200, no text, argv or body), so a
write burst can push read entries out; writes never open, close or consult the read breaker.

Feature labels. `create_issue` and `add_labels` refuse any `feature:*` label (`kind="refused"`, before any
call) unless the caller verified it exists. `GitHubSource` does that check with one REST `GET labels/<name>`
per feature label (404 means absent; a failed lookup also refuses, fail closed) and makes zero write calls
on refusal. Label MINTING for other labels: REST `POST /issues` and `POST /labels` create a missing label
where `gh` failed, so `priority:P*` (always the canonical `priority_prefix + canon`) and triage's
arbitrary `add-label` can now mint a colourless label. That divergence is accepted and pinned by tests,
not fixed here. `create_dependency` and triage still run `label create` first.

REST-vs-gh differences handled: `add_assignees` resolves `@me` with one `GET user` (if that fails, for
example a 403 under an Actions `GITHUB_TOKEN`, nothing is sent and `gh` is NOT tried; the caller leaves the
issue unassigned and says why), refuses a team slug before any call, and raises if the response does not
list every requested login (REST silently drops unassignable users). `remove_label` still treats a 404 as
a no-op but matches only the structured 404 status; the blind spot that a vanished ISSUE is swallowed like
an absent label stays, and `release()` still surfaces a vanished issue through its following comment.

Atomicity. No lifecycle label swap is in this slice: promote, park, unpark, in-progress and complete
transitions stay on the one aliased GraphQL document (`_swap_labels`), so a cloud session still cannot do
them (owner decision D-4, #801). The priority-label mirror changed from one unordered `gh issue edit
--add-label ... --remove-label ...` to the add first, then one remove per stale label, SEQUENTIALLY. It is
NOT atomic: a failed remove leaves two priority labels. Self-heal is narrow because the loop takes the
minimum-rank (highest-priority) label: after a demotion a failed remove is retried (the field and label
disagree), but after a promotion the stale lower label is never removed by the loop. It is visible and
does not change ordering. `PUT /labels` is not used.

Deferred, still open on #895: `note()` (already REST as its last resort, with the owner-accepted
duplicate-on-retry ruling that this policy contradicts), every `label create`, lifecycle label swaps, the
D-4 REST swap, board and project writes, PR writes.

UNMEASURED, not claimed: REST label auto-create on `POST /issues` and `POST /labels`; that a primary
rate-limit rejection (including 429) never partially executes a write; exact 404 bodies ("label not on
issue" versus "issue missing"); the secondary-limit wording; REST assignee drop behaviour and the cost of
`GET user`; per-write request counts and latency compared with `gh issue ...`; any real Claude Code cloud
session.

## PR reads (#895 slice 4a-1)

Seven `gh pr view` READ sites now go REST first through two `gh_api` helpers:

- `gh_api.view_pr_gh(run, number, fields, ...)`: `work.merge_rights` (fork check), `work._comment_directive`
  (the review gate's `sigma:` marker scan), `work.post_review` (stale-evidence head check),
  `work._open_pr_refusal` and `work._pr_merged` (both in `finish`), and `rebase_brief.pr_description`.
- `gh_api.pr_for_branch_gh(run, branch, fields, repo, ...)`: `doctor._stray_commits_after_merge` (a PR looked
  up BY BRANCH).

Policy: REST first (`GET repos/{owner}/{repo}/pulls/<n>`, plus comment pages only when `comments` is asked
for), then at most ONE `gh pr view` fallback built inside `gh_api`, on a rate limit, 5xx or transport failure
only, never in a cloud session or with `SIGMA_GH_GRAPHQL=off`, never retried. It is `read_issue`'s machinery:
issue reads and PR reads open and reset ONE breaker (`.sdlc/state/gh-rest-breaker.json`) and share the
200-entry fallback log (`op: pr_read` / `pr_branch_read`), so a burst of PR failures can turn REST issue
reads off for the cooldown too. Breaker and log are written only where the caller passes an `sdlc_dir`:
`merge_rights`, `post_review`, `_open_pr_refusal` and `_pr_merged` do; `_comment_directive` (no `sdlc_dir`
in scope), doctor (read-only) and rebase_brief do not. Each caller keeps its posture on the REST path AND
the fallback path: `merge_rights`, `_comment_directive` and `_pr_merged` fail CLOSED (no merge, park, "not
merged"), `post_review` parks, `_open_pr_refusal`, doctor and rebase_brief fail OPEN. The fallback rule that
makes this true: a `gh pr view` that exits 0 with empty output, non-JSON, a non-object, or an object missing
any requested field RAISES `GhApiError` (kind `other`, never retried) -- it is never read as an empty PR,
because `{}` would mean "not a fork" (a merge) and "no comments" (a skipped `sigma:block`). The one stated
loosening stays: a REST pull with no `auto_merge` key makes `_open_pr_refusal` fail open. A `run(cwd, argv)` caller is adapted so that only
gh's detail (never argv) is classified: a repo named `timeout-svc` does not read as a transport failure.

Parity is UNMEASURED against live gh: the table below is DERIVED from GitHub's documented REST shapes and
from reading the callers; no REST-vs-`gh pr view` comparison was run. `PR_FIELDS` is a CLOSED whitelist: any
other field (`mergeable`, `statusCheckRollup`, `reviewDecision`, `mergeStateStatus`, ...) is refused with a
ValueError before any call.

| gh field | REST source | rule |
|---|---|---|
| `number` | `number` | an int; anything else is an error |
| `title` | `title` | null reads "" |
| `body` | `body` | null reads ""; an ABSENT key is an error |
| `state` | `state`, `merged`, `merged_at` | `merged: true` or a non-empty `merged_at` is MERGED; else `open` OPEN, `closed` CLOSED; anything else (or absent) is an error, never a guess |
| `headRefOid` | `head.sha` | missing reads "" (the callers' stale-PARK / no-anchor arms fire) |
| `headRefName` | `head.ref` | missing reads "" |
| `mergedAt` | `merged_at` | passthrough |
| `closedAt` | `closed_at` | passthrough |
| `autoMergeRequest` | `auto_merge` | null or object passes through; an ABSENT key is an error |
| `isCrossRepository` | `head.repo`, `base.repo` | a null `head.repo` (deleted fork) is TRUE; else the two `full_name`s compared case-insensitively; a missing `head`/`base`/`base.repo` is an error; unknown never reads FALSE |
| `author` | `user.login` | the raw REST login, NOT bot-mapped; no `user` reads `{"login": ""}` |
| `comments` | `issues/<n>/comments` pages | `{id, author, body, createdAt}`, plus `authorAssociation` ONLY when REST sent `author_association` (so the "no association" park still fires) |

Named differences, all UNMEASURED:

- Bot login spelling: REST says `<slug>[bot]` where gh says `app/<slug>`. `_comment_directive` compares the
  PR author with each commenter, and on the REST path both sides are REST (`x[bot]` == `x[bot]`); on a
  fallback both sides are gh-native. The two spellings are never compared with each other. An empty REST
  author login is treated as unknown (no `same_author`).
- `_open_pr_refusal` (R7): a REST body with NO `auto_merge` key is an error, so `finish` fails OPEN (does
  not refuse) on that reply where `gh pr view` would have read "not armed" and refused. A documented minor
  loosening on a malformed response GitHub is not known to send; the behaviour is otherwise the same, not
  byte-for-byte proven.
- `--head` fork visibility (doctor): `gh pr view <branch>` resolves a PR across forks; REST
  `pulls?head=<owner>:<branch>&state=all&sort=created&direction=desc&per_page=30` sees same-owner heads
  only, so a fork PR on a same-named branch is invisible (no alarm; the check is a fail-open diagnostic).
  No client-side scan of every open PR. gh's "open PR first, else most recent" preference is ASSUMED
  (open row first, else newest); more than 30 PRs for one head reads the 30 newest only. An empty list is
  "no PR"; a failed or non-list read is an error, never "no PR".
- Comments cost: today one GraphQL call; now `1 + (floor(n/100) + 1)` REST calls for a PR with n comments
  (stop at the first short page): 10 comments = 2, 1000 comments = 12. The page cap is 30 (3000 comments):
  if page 30 comes back full the read RAISES and the review gate parks; a truncated list could hide a
  trusted `sigma:block`. 100x a typical PR is therefore capped at 31 calls, then a park.
- REST call counts per read (derived): `merge_rights` 1, `post_review` 1, `_open_pr_refusal` 1,
  `_pr_merged` 1, `pr_description` 1, doctor 1, `_comment_directive` 2 or more (above), each against one
  GraphQL call before; all draw on the REST `core` pool (5000/h) that every other REST read shares. Not
  measured at scale.
- The GraphQL review-thread read (`_unresolved_threads`) stays GraphQL; with GraphQL unavailable its
  existing fail-open turns the thread check off silently. That gap is unchanged here.

Remaining PR sites, still direct `gh pr` and still open on #895 (counts from the ratchet's `scan()`):
work.py 9 (R1 the merge gate, R4 `reviewDecision`, R5 the sibling list, R9 the design-PR list, and writes
W1-W5 including `--auto`), doctor 6 (R10 plus 5 non-PR sites), verify_merge 3 (`pr ready|create|merge`).

## What this does NOT do

- `read_issue`, `list_issues_gh`, the seven issue write helpers and the two PR read helpers (`view_pr_gh`,
  `pr_for_branch_gh`, above) are wired to callers; PR WRITES and the merge gate are not, the raw `create_pr`
  / `merge_pr` and the project ops have no caller, and nothing in the product exercises the probe or the cache.
- `/sigma-loop` is NOT supported in cloud sessions. REST `merge` has no auto-merge, and none is emulated.
- `create_issue` and `add_labels` in `gh_api.py` do not go through `GitHubSource._run`'s feature-label refusal, so they
  carry their own (layer 1, refuse unless the caller verified the label exists) and `GitHubSource` does the existence
  check (layer 2). A direct caller of the helper that passes `feature_labels_exist=True` without checking bypasses both.

## The ratchet

`tests/test_no_direct_gh.py` stops direct `gh issue|pr|project|label` call sites from growing
(baseline 101 sites in 21 files at slice 1, 94 after #895 slice 2a, 84 after slice 2b, 74 after slice 2c, 57 after slice 3a,
50 after slice 4a-1; it only goes down). Run
`$HOME/.sigma-venv312/bin/python -m pytest tests/test_no_direct_gh.py`
(generic form: `python -m pytest tests/test_no_direct_gh.py`).
It covers list literals only. Shapes it CANNOT see: string-form or shell-string calls
(`"issue view".split()`); argv built incrementally (`args += [...]`, `.append`, `.extend`); lists whose first
element is a variable; positional-string wrapper calls (`_gh("issue", "view")`); skill prose (`.md`, `.tmpl`);
and shell scripts (`skills/**/*.sh`, `hooks/*.sh`; 9 files, none mentions `gh` today, nothing keeps it so).
So it guarantees "no new list-literal gh call", not "no new gh call". It also cannot see a REST write added
through a `gh_api` helper, and `docs/launch/write-surface.json` (scanner blind spot, same reason) records only the
helper's `_default_run`; `tests/test_issue_creation_boundary.py` pins the callers of the create helper.

## Follow-ups

Remaining migration (issue read and write ops, PR ops, Projects v2, skill prose, status line, the cloud
smoke run, and wiring the probe and `graphql_unavailable` into real callers) is filed as queued issues,
not done here.

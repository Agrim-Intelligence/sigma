# Claude Code cloud sessions and GitHub GraphQL: detection and reporting only

Status: slice 1 of #801 (detection and reporting) plus slices 2a, 2b and 2c of #895 (single-issue READS go
REST first: sources.py, then ten more `issue view` sites; then ten `issue list` sites), slice 3a (issue
WRITES), slice 4a-1 (seven PR READS) and slice 4a-2 PR A (the merge gate and doctor's landing-PR row). It
does not claim that `/sigma-loop` works in a Claude Code cloud session: that has not
been measured (no cloud session was available), and it is the follow-up smoke run. REST migration is in
progress, not complete: the remaining PR reads (4a-2 PR B: the design-PR list and the review gate's
`latestReviews`; later the sibling list), PR writes (4b/4c) and the board remain.

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

Parity of the rows below is UNMEASURED against live gh: they are DERIVED from GitHub's documented REST
shapes and from reading the callers; no REST-vs-`gh pr view` comparison was run for them. `PR_FIELDS` is a
CLOSED whitelist: any other field is refused with a ValueError before any call. Slice 4a-2 (next section)
added `mergeable` and `mergeStateStatus`, partly MEASURED; `statusCheckRollup`, `reviewDecision`,
`latestReviews`, `files` and the rest are still refused by `view_pr_gh`.

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

## Merge-gate and landing-PR reads (#895 slice 4a-2, PR A)

Two more PR read sites go REST first, each keeping its posture: `work.gate()` (R1, the merge gate) fails
CLOSED, doctor's landing-PR row (R10, `_landing_pr_unverifiable`) fails OPEN.

- `gate()` reads `gh_api.view_pr_gh(..., ["mergeable", "mergeStateStatus", "headRefOid"])` (one
  `pulls/<n>` GET) on each attempt of its UNKNOWN loop, then, once the loop settles and the pull reported a
  head, `gh_api.pr_check_rollup_gh(..., sha=<that head>)` before the local-head read.
- Doctor reads `gh_api.open_pr_for_branch_gh(run, "feature/<unit>", ["number", "mergeable", "headRefOid"],
  repo)` and, ONLY when the PR is CONFLICTING, `pr_check_rollup_gh` for its head. Any failure of either
  read is None (no alarm): an unread rollup is never treated as an empty one. No `sdlc_dir`: doctor never
  writes the breaker or the fallback log. `mergeStateStatus` is no longer fetched (it was unused).

| gh field | REST source | rule | evidence |
|---|---|---|---|
| `mergeable` | `pulls/<n> .mergeable` | `true` MERGEABLE, `false` CONFLICTING, `null` UNKNOWN; an ABSENT key or any other type (the string `"true"` included) is an error. A `mergeable_state` of `unknown` makes it UNKNOWN whatever `mergeable` says, so the gate's UNKNOWN retry runs and the park reads "mergeability unknown" | `true` and `null` MEASURED (orchestrator parity, 25 PRs of this repo); `false` NOT observed (no conflicted PR in the sample), DERIVED |
| `mergeStateStatus` | `.mergeable_state` | accepted only as a string exactly in `clean, dirty, unstable, blocked, behind, draft, has_hooks, unknown` (lower-case), emitted upper-cased; absent, null, non-string, upper-case (`"CLEAN"`) or any other word is an error, never a default. A null `mergeable` forces UNKNOWN. Never CLEAN or BEHIND by default: CLEAN merges, BEHIND force-push-rebases | `blocked` and `unknown` MEASURED (same sample); the other six DERIVED from GitHub's docs |
| `statusCheckRollup` CheckRun | `commits/<sha>/check-runs` (default `filter=latest`) | `{__typename: CheckRun, name, status: UPPER, conclusion: UPPER or "" when null, detailsUrl: details_url}`; a row that is not an object, a non-string `name` / `status`, a `conclusion` that is neither a string nor null, or `completed` with a null or empty conclusion is an error (`work._check_verdict` reads empty conclusion + COMPLETED as ok) | name and state set MEASURED equal on 25/25 PRs; `detailsUrl == details_url` on 2 PRs / 7 runs; in-progress / null-conclusion shape UNMEASURED |
| `statusCheckRollup` StatusContext | `commits/<sha>/status .statuses[]` | `{__typename: StatusContext, context, state: UPPER, targetUrl: target_url}`; `statuses` absent or not a list is an error; the combined `.state` is never read (an empty set reports `pending`, MEASURED) | `context` / `state` / `targetUrl` MEASURED equal on 2 kubernetes/kubernetes PRs (21 and 13 statuses); this repo has none |
| `headRefOid` | `.head.sha` | unchanged from 4a-1: missing reads "" and the gate parks `_PARK_NO_REMOTE_HEAD` without a rollup read | 4a-1 |

Rollup rules (`pr_check_rollup_gh`): the sha must be 40 lower-case hex, else ValueError before any call (a
branch name or "" can never be queried; a SHA-256 repository's 64-hex head is refused too, so the gate parks
unreadable there although `work.py` accepts 64-hex heads elsewhere: UNMEASURED, fails closed). Check runs
then statuses (gh's order; `ci_repair` takes the first failing), 100 per page, paged by each page's own
`total_count`: a short page while fewer rows than `total_count` were fetched, or `CHECK_RUN_PAGE_CAP` (10
pages, 1000 runs) / `STATUS_PAGE_CAP` (5 pages, 500 statuses) reached below `total_count`, RAISES. A missing
failing check never reads as green. Paging is not atomic across pages (a run created between pages can
shift the set), so consumers re-read the rollup every pending round. The read is all-or-nothing: a 429 on page 2 after a good page 1 is one
fallback, never half REST. Fallback: ONE `gh pr view <n> --json statusCheckRollup,headRefOid` on a rate
limit / 5xx / transport failure, never in a cloud session or with `SIGMA_GH_GRAPHQL=off`, never on
401/404/422/permission-403/proxy, accepted only when its `headRefOid` equals the sha asked for and the rollup
is a list (else an error): a rollup for another head is never attached.

Open-PR-by-branch rules (`open_pr_for_branch_gh`): `repo` (`owner/name`) is required; ONE
`pulls?head=<owner>:<branch>&state=open&per_page=1` with `<owner>` taken from `repo` (gh's `{owner}` does
expand in a query string, MEASURED, but from the process cwd's remote, which may not be `repo`), then one
`pulls/<n>` read (list rows carry `mergeable: null` and no `mergeable_state`, MEASURED). A non-list body or a
row `number` that is not an int is an error, never "no PR". Fallback: ONE `gh pr list --repo R --head
<branch> --state open --limit 1 --json <fields>`; a row missing a requested key is an error. Named
difference (DERIVED, not re-measured): `gh pr list --head X` matches any owner, `head=<owner>:X` same-owner
heads only, so a fork landing PR is invisible to the advisory row (no alarm).

When the gate reads the rollup (D4): AFTER the UNKNOWN loop settles, once per pending round, only for a
non-empty head, for exactly the `headRefOid` the stale-head check then compares with local HEAD, and BEFORE
the local-head read, so every `data` the gate returns from that point on carries the four keys
`_behind_transient`, `normalise_ci_rollup`, `_ci_failed_check` and `ci_repair` read. On the
`_PARK_NO_REMOTE_HEAD` and "could not read" paths it carries fewer (no rollup, or `{}`), which those
consumers already tolerate. Two-read window (named, not new): REST reads the pull, then the checks, non-
atomically; a push in between leaves the rollup describing the older, compared head, and the merge itself is
guarded by `--match-head-commit` (slice 4b, unchanged).

Rollup retry: the rollup read is retried on the pull read's budget (`UNKNOWN_ATTEMPTS` 4, backoff 3 s
doubling), but ONLY for a transient failure (`gh_api.FALLBACK_KINDS`: rate limit, 5xx, transport). In a
cloud session there is no fallback, so without it one 502 on check-runs would park the goal (parking strips
`sdlc:goal`). A deterministic failure (a permission 403, a malformed or truncated page, a refused sha) parks
at once instead of sleeping ~21 s for the same answer.

Verdict precedence (changed, stated): before, one atomic read could never be "pull readable, rollup not".
Now a rollup still unreadable after its retries PRECEDES and HIDES STALE HEAD, an unreadable or empty local
head, UNKNOWN, CONFLICTING and BEHIND: the gate returns `could not read PR state (...)` with `{}`. It still
fails closed (a park, never a merge), but a BEHIND PR then parks instead of rebasing, and a stale head reads
as unreadable rather than STALE HEAD. Likewise a push between the pull read and a fallback rollup read (the
fallback answers for a newer `headRefOid` than the one asked for) parks "could not read PR state", not
STALE HEAD. Only `_PARK_NO_REMOTE_HEAD` (no head, no rollup read) is unaffected.

Costs (DERIVED from the call shapes; nothing timed): a clean gate round is 3 REST calls (pull, check-runs,
status) against 1 GraphQL call before; +1 per UNKNOWN retry; a rollup is `ceil(runs/100) +
ceil(statuses/100)`, 15 at the caps. Worst `merge()`: 11 rounds x (<= 4 pull reads + 2) = 66 calls against 44;
about 230 worst-case merges per hour on one 5,000/h token (a documented limit, UNMEASURED). With the breaker
OPEN, both the pull read and the rollup read go straight to their GraphQL fallback: 2 GraphQL calls per round,
not 1, i.e. up to twice today's GraphQL cost while the breaker is open. Doctor: 2 calls per open unit, 4 for
a conflicted PR, against 1; 100 open units is about 200 calls per on-demand run.

Token permission (DERIVED from GitHub's docs): check-runs needs `checks:read` on a fine-grained token or a
GitHub App. A permission 403 never falls back, so on such a token the gate parks unreadable and doctor's row
says nothing.

R4, the review gate, decided and NOT changed here: under `require_review: approval` it keeps ONE
`gh pr view <n> --json reviewDecision,latestReviews` read, because REST cannot derive APPROVED /
REVIEW_REQUIRED (MEASURED: the branch-protection endpoint 404s, and rulesets are invisible to it). That read
is GraphQL-only and fails CLOSED when GraphQL is unavailable, so in a cloud session approval mode parks,
never passes. UNCHANGED blind spot, stated honestly: under `require_review: changes` a failed
`reviewDecision,latestReviews` read already returns "pass" (`work.py` review_gate), which skips the
CHANGES_REQUESTED check AND `_unresolved_threads`, so in a cloud session a native "Request changes" review
and unresolved threads are invisible to the gate; only a `sigma:block` comment, read over REST, still blocks.
PR B's `latestReviews` move narrows this. `_unresolved_threads` stays GraphQL (REST has no `isResolved`).

MEASURED: the orchestrator's live parity run on 25 PRs of this repository (`mergeable` agreed 25/25,
values `true` and `null` only; `mergeable_state` `blocked` and `unknown` only; check-run name / state set
25/25; `detailsUrl == details_url` on 2 PRs / 7 runs) and research's kubernetes/kubernetes run (2 PRs, 21 and
13 statuses, `context` / `state` / `targetUrl` equal). UNMEASURED: `mergeable: false` (a conflicted PR) and
six of the eight `mergeable_state` values; the in-progress / null-conclusion check-run shape; the first-GET
`mergeable: null` rate on open PRs; the real 429 body; REST vs GraphQL latency; any cloud-session run; a
GraphQL-fallback read and a REST read mixing shapes across consecutive gate rounds; the `checks:read` 403;
the 64-hex sha refusal. Also UNMEASURED and a known divergence: GraphQL's `statusCheckRollup` can carry
EXPECTED contexts (required status contexts not yet reported), while REST `commits/<sha>/status` never
returns them, so a BLOCKED PR waiting only on such a context parks "not safe to merge (BLOCKED)" instead of
waiting and arming `--auto`. That fails closed.

Remaining PR sites, still direct `gh pr` and still open on #895 (counts from the ratchet's `scan()`):
work.py 8 (R4 `reviewDecision`, R5 the sibling list, R9 the design-PR list, and writes W1-W5 including
`--auto`), doctor 5 (all non-PR sites), verify_merge 3 (`pr ready|create|merge`). Next: PR B (R9, R4's
`latestReviews` half), then R5; PR writes are slices 4b/4c.

## What this does NOT do

- `read_issue`, `list_issues_gh`, the seven issue write helpers and the four PR read helpers (`view_pr_gh`,
  `pr_for_branch_gh`, `pr_check_rollup_gh`, `open_pr_for_branch_gh`, above) are wired to callers; PR WRITES
  are not, the raw `create_pr`
  / `merge_pr` and the project ops have no caller, and nothing in the product exercises the probe or the cache.
- `/sigma-loop` is NOT supported in cloud sessions. REST `merge` has no auto-merge, and none is emulated.
- `create_issue` and `add_labels` in `gh_api.py` do not go through `GitHubSource._run`'s feature-label refusal, so they
  carry their own (layer 1, refuse unless the caller verified the label exists) and `GitHubSource` does the existence
  check (layer 2). A direct caller of the helper that passes `feature_labels_exist=True` without checking bypasses both.

## The ratchet

`tests/test_no_direct_gh.py` stops direct `gh issue|pr|project|label` call sites from growing
(baseline 101 sites in 21 files at slice 1, 94 after #895 slice 2a, 84 after slice 2b, 74 after slice 2c, 57 after slice 3a,
50 after slice 4a-1, 48 after slice 4a-2 PR A; it only goes down). Run
`$HOME/.sigma-venv312/bin/python -m pytest tests/test_no_direct_gh.py`
(generic form: `python -m pytest tests/test_no_direct_gh.py`).
It covers list literals only. Shapes it CANNOT see: string-form or shell-string calls
(`"issue view".split()`); argv built incrementally (`args += [...]`, `.append`, `.extend`); lists whose first
element is a variable; positional-string wrapper calls (`_gh("issue", "view")`); skill prose (`.md`, `.tmpl`);
and shell scripts (`skills/**/*.sh`, `hooks/*.sh`; 9 files, none mentions `gh` today, nothing keeps it so).
So it guarantees "no new list-literal gh call", not "no new gh call". It also cannot see a REST write added
through a `gh_api` helper, and `docs/launch/write-surface.json` (scanner blind spot, same reason) records only the
helper's `_default_run`; `tests/test_issue_creation_boundary.py` pins the callers of the create helper.

## Landing plumbing (upkeep part C, slice 2)

`gh_api` gains REST-only helpers for landing a feature unit onto the base branch; nothing calls them yet and they
are inert unless the upkeep gate is open. `merge_pr_pinned` requires an explicit merge method, a repository and a
40-hex head pin, and refuses before any call otherwise; `commit_parents`, `branch_rules`, `repo_settings` and
`create_pr_nondraft` are the supporting reads and the explicit non-draft create; `bounded_runner` binds a working
directory and a timeout and keeps the exit code and failure class. They have no GraphQL fallback. In a cloud
session two things stay unavailable: draft readiness (`gh pr ready` is GraphQL only) and landing through a merge
queue (REST cannot enqueue). The alternative of the CLI merge with a head-match flag was rejected: it resolves the
repository from the working directory, exits 0 on an enqueue and cannot return the merge commit.

## Follow-ups

Remaining migration (issue read and write ops, PR ops, Projects v2, skill prose, status line, the cloud
smoke run, and wiring the probe and `graphql_unavailable` into real callers) is filed as queued issues,
not done here.

# #895 slice 8 retro (P7 RETRO)

Basis: `git diff` in `.sdlc/work/895` (19 files, +489/-298), dossier `.sdlc/research/895-s8.md`, brief
`/tmp/brief-895-retro.md`, slice 7 retro as the model. Advisory, audit-trail only.
Evidence limit: this retro READ the diff (`sources.py` `note()`, CHANGELOG, `docs/cloud-sessions.md`,
`tests/test_no_direct_gh.py`, the comment edits in `acceptance.py`/`loop.py`/`spend_approval.py`) and ran only
`grep`/`git diff`/`git status`. It ran no tests, no controls, no `loop.py verify`, no `gh` call. Every pass/red
claim below is the implementer's, not re-measured here. The rewritten tests in `tests/test_sources.py` (+313/-~190)
were NOT read line by line.

## Grade: partial

Intent (slice 8: `note()` goes REST-first, no GraphQL-first attempt, none at all in cloud) is present in the code:
`note()` calls `gh_api.comment_issue`, the `["issue","comment"]` literal is gone, ratchet 37 -> 36 / sources.py
20 -> 19, CHANGELOG and `cloud-sessions.md` say so and say "UNMEASURED". "partial" and not "achieved" because
(a) the proof owed (red controls, full-suite verify) is not visible in what was reviewed, (b) behaviour changed
beyond "REST-first" in a way the CHANGELOG discloses but the owner may not have ruled on (drift no longer
retried), and (c) cloud behaviour is unmeasured by design.

## What went well
- The primitive already existed; the change is small (one method body) and the policy comes from `WRITE_POLICY`,
  not from new code: one fallback, only on a primary rate limit, none in cloud (built into `_rest_write`).
- Classification reads gh stderr and the cause chain via `_write_case`, never `str(exc)`, so a comment body that
  says "HTTP 502" cannot trigger a retry. The `fell` flag stops a policy fallback being followed by another POST.
- Honesty kept: CHANGELOG, docstring and the doc section all state "NOT run in a real Claude Code cloud session",
  name the silent-hang case, and name the retained duplicate-on-retry ruling as contradicting `comment_issue`'s own
  stance. The slice-7 residual (stale "note() is already REST" sentence at cloud-sessions ~191) was fixed, as was
  the old "probable cloud blocker" paragraph.
- Stale comments updated at callers (`acceptance.py`, `loop.py` stderr text "after retries", `spend_approval.py`).

## What went badly / risks
- Behaviour change beyond the slice title: previously a non-transient GraphQL error raised at once, but
  `_is_transient` treated active-account drift (the #1986 motivating case) as RETRYABLE; now 401/403/404 raise on
  the first attempt with no retry. The #1986 docstring justified the retry specifically by account drift; this
  retro found no statement that the owner approved dropping it. A REJECT verdict's only record can now be lost to a
  drift that a retry used to survive. Needs owner ack.
- Private-helper coupling: `sources.py` now calls `gh_api._write_case` (underscore-prefixed). A rename in
  `gh_api.py` breaks `note()` at runtime for the retry path only (a 5xx), which tests may not exercise. The case
  strings `"server_5xx"`/`"transport_ambiguous"` are also duplicated literals, not imported constants.
- `comment_issue` raises `GhApiError`; the `except` catches only that. A non-numeric `goal` makes `_n()` raise
  `ValueError` outside the retry/except (callers catching `Exception` are fine; `acceptance.py:165` is NOT caught and
  propagates). Not shown to occur, since GitHub goals are numeric.
- `GhApiError.__str__` embeds argv and so the comment body; the CLI verb prints `{e}` to stderr and handoff
  warnings embed `{exc}` (unchanged from the old RuntimeError, but now also carries the REST `-f body=`).
- Duplicate-on-retry: a REST re-POST after a 5xx/transport failure can double-post; the backoff total is
  0.5+1+2 = 3.5 s worst case, bounded. Documented, unmeasured.
- No Sigma-side timeout: a silent hang in `gh api` blocks the caller (the loop's run_with_timeout covers the watch
  daemon ticks, not this call). Documented, not bounded.
- `fell`/lambda trick is clever for what it does (3 lines); readability cost is real but small.

## Residual debt
1. `sigma-goal-review` still grants `Bash(gh issue comment *)` in SKILL.md:4 and references/selection.md:4 and
   names it in prose :24. After slice 8 the outcome comments go through `note()` (REST), so the grant is likely
   dead for the REJECT path; but it also permits a bare GraphQL `gh issue comment` an agent might still run in cloud.
   Dropping it needs a pass over `tests/test_sdlc_goal_review_skill.py` (pins exact grants) and the prose; `Bash(gh
   api *)` coverage for that skill was not confirmed here. Out of slice scope per the dossier.
2. Owner ack outstanding: drift (401/403/404) and secondary rate limits are no longer retried (see above).
3. Fake runners must re-record REST POSTs: `tests/gqlfake.py` `rest_write` re-records a REST comment POST as the
   legacy `["issue","comment",...]` argv, which is why dozens of `test_github_project.py`/`test_unpark.py`/
   `test_promote.py` asserts still pass. Any new fake runner that does not route through it will see an `api ...
   POST` argv and silently stop matching `a[:2]==["issue","comment"]` filters (assert never fires, test passes
   green). That is a standing trap; a test seen red per fake is the control.
4. Cloud behaviour unmeasured: that the proxy accepts `POST issues/N/comments`; the token's permission; how a
   hang fails; `{owner}/{repo}` expansion when `repo` is unset and cwd has no remote.
5. Not covered by either ratchet: the fallback argv inside `gh_api.py` (EXEMPT), shell scripts, `.tmpl`, built-up
   argv. The remaining 36 ratchet sites include the GraphQL board sites that are cloud-skipped.
6. R5 (`work.py` `_sibling_pull_requests`) still deferred; `label create` x4 and lifecycle swaps still listed as
   "Deferred, still open on #895".
7. Test-suite state not seen: `_NOTE_RETRIES`/`_NOTE_RETRY_BASE` are kept (docstring-only change) so test assignments
   stay valid; whether every `gh._NOTE_RETRIES` read assert was rewritten was not re-verified.

## Lessons
- When a policy primitive already encodes the ruling, migrate the caller and delete code; do not re-implement
  classification in the caller. The one place the caller does (`_write_case` import) is the coupling to fix.
- A "migration to REST" can silently change retry semantics: diff the OLD retry predicate (`_is_transient`) against
  the NEW one and list every error class that moved from retried to not retried. Do it before the PR, in the PR body.
- Grants outlive the call: removing a call site does not remove its `allowed-tools` grant; sweep both in the same
  slice or file the follow-up.

## Proposals (proposals only; none applied, none are standing rules)
- P-1: expose `gh_api.write_case` (public) or a `gh_api.is_retryable_write(exc)` so callers stop importing
  underscore names; add a test pinning the two case strings.
- P-2: a standing rule that any slice that changes which error classes are retried states the before/after table in
  its CHANGELOG entry.
- P-3: a structural test that every `allowed-tools: Bash(gh ... *)` grant has a documented call or prose use (catches
  dead grants such as `gh issue comment *`); needs a red control on the documented gesture before trust.

## Follow-ups to file (none filed by this retro)
- Owner decision: retry on active-account drift for `note()`, or accept the loss window.
- Drop `Bash(gh issue comment *)` from sigma-goal-review after a prose and test pass.
- Public `gh_api` write-case accessor (P-1).
- Real Claude Code cloud smoke run per `docs/cloud-sessions.md` owner steps, covering `POST issues/N/comments`.

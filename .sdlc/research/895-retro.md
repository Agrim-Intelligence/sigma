# #895 slice 3 retro (P7 RETRO, advisory, autonomous mode)

Evidence read: acceptance/895.md, plans/895.md, research/895.md, research/895-controls.md,
`git -C .sdlc/work/895 diff --stat` (30 files, +2385/-526). Full-suite verify was still running in the
worktree when this was written, so no pass count is claimed here.

## What shipped
- 17 issue WRITE list-literal sites (comment, create, edit body, close, add/remove label, add-assignee)
  in 7 files moved onto `gh_api` over REST: sources.py, triage.py, define/assign/dossier/promote/unpark paths.
- `gh_api.py` (+303): write policy table (one REST attempt, at most one GraphQL fallback, never retried;
  fallback on primary rate limit for any write, on 5xx for idempotent writes only; none on 422/404/403/secondary
  limit/ambiguous transport). Doc-vs-table test keeps docs/cloud-sessions.md honest.
- Feature-label refusal kept as two layers (wrapper guard + `_issue_*` verify) before any create/add.
- No-direct-gh baseline ratchet 74 -> 57 (exactly the 17 migrated sites); write-surface.json/md updated.
- Controls log: C1-C24 each broken, seen red, reverted, green (incl. C22d on the documented
  `check . docs/launch/write-surface.json` gesture). Red-before-implement recorded for steps 1-6.

## What remains of #895
- Slice 3b: `note()` (sources.py:3275), already REST-last-resort, carries owner-accepted #1986
  duplicate-on-retry ruling that contradicts the no-retry default; needs a policy decision.
- Slice 3c: the 5 `label create` sites (sources.py:1682, 1746, 3746; triage.py:2267; define.py:215);
  needs a decision on REST label creation.
- Slices 4-7: PR writes, project/board writes, lifecycle label swaps (`_swap_labels`, D-4 REST swap,
  `_set_board_status`), remaining reads (status.py:174 stays by design). Ratchet stands at 57.

## Lessons (audit trail)
- Plan corruption by piecemeal edits: plans/895.md was patched repeatedly in small edits across review rounds
  and drifted (contradictory step numbering, restated acceptance, D5 narrowed late). Lesson: after N edits to a
  plan, rewrite or re-read it whole before dispatching; do not stack incremental patches.
- Implementer background-wait stall: the implementer parked on a background wait (verify/test run) and stalled
  the slot with no liveness signal. Lesson: bound waits (timeout + poll) or run verify foreground; a waiting
  agent must be distinguishable from a dead one (LIVENESS).
- Controls paid for themselves: C3/C4/C5 each tripped 7-9 tests, so the policy table is genuinely armed.

## Residual debt (none of it tracked yet unless noted)
- `remove_label` keeps a 404 no-op because exact GitHub 404 bodies are unmeasured; it can mask "issue missing".
- `complete()` is comment-then-close as two non-atomic writes; a failure between them (or a retry of the
  caller) can leave a duplicate comment. Documented, not fixed.
- Shim test loose regex: the C23 shim-vacuity controls rely on a path-matching regex looser than the exact
  endpoint; a wrong-but-similar path could pass. Tighten to full-path equality.
- UNMEASURED (named, never claimed): REST label auto-create on POST /issues and /labels; primary rate limit
  never partially executing a write (incl. 429); 404 bodies for label-absent vs issue-missing; secondary-limit
  wording; REST assignee drop and `GET user` cost; request counts and latency vs `gh issue`; real Claude Code
  cloud-session behaviour. All need a scratch repo; none was available.
- Priority promotion with a failed remove can leave a stale lower label (D5, narrowed to min-rank self-heal).

## Structural / product reflection
- Multi-site smell resolved by the policy table; the two-layer feature-label guard is the shared contract.
- Deferred roots: note() and `label create` are structural defers (a missing REST label primitive) -> 3b/3c.
- Direction: advances REST-first goal; north-star not read (not required for this audit note).
- Nothing rotted in standing docs; plans/895.md should be archived once #895 closes (it is the plan-gate input).

## Proposals (parked, NOT applied; need owner approval)
| Lesson | Store | Proposed edit |
|---|---|---|
| Rewrite a plan whole after >N review patches | standing rule | add to sigma-plan-review guidance: re-read plan end to end before implement |
| Agents must not block on background waits unbounded | standing rule | require timeout+poll in implementer dispatch brief |
| Tighten shim path match to equality | audit trail / follow-up goal | file under 3a debt on #895 |

GRADE: partial
Gaps: criteria 1 and 2 evidenced by the diff and controls log; criterion 3 (baseline, docs claim only what
was measured) evidenced by C22/C24, but remaining sites 3b/3c/4-7 are tracked on #895 and full verify was not
yet confirmed at write time.

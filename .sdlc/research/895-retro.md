# #895 slice 6 retro: skill prose REST-first

Grade: **achieved** against `.sdlc/acceptance/895.md` (5 of 5 criteria, scoped to skill markdown). Caveat: the criteria never covered code that emits `gh` text (see debt).

## Intent vs shipped
1. Loop-path prose: achieved. The scan finds 27 remaining `gh issue|pr|project|label ` mentions, all allowlisted rule/history text. Rewritten files use `gh api repos/{owner}/{repo}/...` and gain `Bash(gh api *)` (old grants kept, `selection.md` mirrors updated). Re-run in the worktree: phase-budget, skill-structure and both `test_no_direct_gh*` files, 84 passed. Ceilings untouched (tests green).
2. Human docs: achieved. Short GraphQL notes added to `board.md` and `public-repo.md`.
3. Cloud section: achieved. `docs/cloud-sessions.md` carries prerequisites, on/off list and smoke steps, marked UNMEASURED.
4. Structural test: achieved. `tests/test_no_direct_gh_prose.py` has a documented invocation and an allowlist. Its red controls were run in earlier phases; I did not re-break it here, so that part is reported, not re-observed.
5. CHANGELOG: achieved. States "NOT run in a real Claude Code cloud session".

## Went well
- Plan-review and code-review rounds (3 each) caught real defects before ship.
- Author-blind review found the allowlist window weakness. An entry could hide a neighbouring offender. It is now pinned by the exactly-one-hit rule.
- The test joins wrapped paragraphs, so it sees spans that `test_documented_gh_verbs.py` misses.

## Went poorly
- Word budgets: the plan counted characters where the ceiling counts words. Retro had 0 headroom, orchestrator 34. It cost rework rounds.
- Three rounds each of plan and code review. The allowlist-window flaw was invisible to inspection and to the author's own controls.

## Residual debt (none tracked in a new issue by this retro; park for human)
- `review_context.py` still tells reviewers to run `gh pr diff` (line 594) and `gh issue view` (lines 606, 651, 817, 830). This is a live GraphQL instruction in cloud, outside the md-only scope.
- `work.py:130` `mechanism` string still says a cloud session stops at the GraphQL `merge_rights` read.
- `Bash(gh api *)` grant is as broad as the gh issue/pr grants beside it. A per-endpoint allowlist is the follow-up.
- The scanner skips 4-space indented code blocks with no backticks (gap pinned by a test, not closed).
- Six allowlist entries match by substring and do not pin the verb. They could survive a verb swap inside their window.
- R5 sibling list GraphQL read is still open in the PR reads.
- UNMEASURED: no real Claude Code cloud smoke run. The owner steps exist, and the whole REST-first path is unverified end to end.

## Standing-doc rot
`.sdlc/plans/895.md` is shipped history once merged: propose archiving (needs approval). The "skill prose is uncovered" gap sentence in `test_no_direct_gh.py` and `cloud-sessions.md` was updated in this diff. Nothing else rotted.

## PROPOSED durable lessons (parked, need human approval; nothing written)
- Standing rule candidate: a prose ratchet allowlist entry must match exactly one hit and pin the verb. Store: `.sdlc/project.md`. Already enforced for one-hit; the verb-pinning half is not.
- Audit-trail: when a budget is stated in words, plan in words. Measure with `evals/phase_context_budget.py` before editing, not chars.
- Process: a guard whose allowlist can hide neighbours needs an author-blind reviewer. Add "allowlist window" to the plan-review checklist for any substring-allowlist test.
- No north-star was available to this retro; Direction check was not run.

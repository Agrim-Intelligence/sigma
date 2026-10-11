# #895 slice 7 retro (P7 RETRO)

Basis: `git diff` in `.sdlc/work/895` (9 files, +233/-156), plan `.sdlc/plans/895.md`, dossier `.sdlc/research/895-s7.md`.
Evidence limit: this retro READ the diff; it ran no tests, no controls, no `loop.py verify`. Every pass/red claim
below is therefore the implementer's, not re-measured here.

## Grade: partial

Every done_when item is present in the diff. "partial" and not "achieved" because the proof the plan demanded
(five controls seen red, final full-suite verify) is not visible in the artifacts reviewed, and one finding
(note()) is documented but unfixed and contradicted by older text still in the same doc.

## What went well
- Scope held. Briefs (`review_context.py` :594/:606/:651/:817/:830) moved to `gh api repos/{owner}/{repo}/...`;
  `work.py` mechanism string rewritten with no `|` or newline; `docs/enforcement.md` diff is exactly that one row.
- The stale claim was corrected at its source: the dossier found `merge_rights` is REST, so "stops at the GraphQL
  `merge_rights` read" was false; the new text names the three real park conditions and marks the token
  `permissions` question UNMEASURED.
- Prose ratchet tightened without churn: verb pin derived from each entry's own substring, explicit third field
  only for the six neighbourhood-only entries; indented-block scan added with a list-continuation exemption and
  its own control test; the old "KNOWN GAP" test flipped to a positive control.
- Honesty rule kept: CHANGELOG and cloud-sessions.md both say "NOT run in a real Claude Code cloud session" and
  mark the transport column DERIVED/UNMEASURED.
- No skill prose touched; ratchet baseline untouched (37); budget JSON edited only where measurement moved
  (`review-post-pr` est_tokens 6305 -> 6310), word ceilings unchanged.

## What went badly / risks
- The `{owner}/{repo}` placeholder in briefs only expands inside a git checkout. Documented in cloud-sessions.md,
  but a reviewer spawned outside a checkout gets a literal-placeholder 404. Not measured.
- Budget JSON `measured_at`/`commit` were refreshed to `cdd7deb1...`, while the baseline commit is the HEAD of the
  worktree; that is correct only if the budget tool ran at that commit. Not re-checked here.
- The new indented-block heuristic (`in_list` tracking) is a regex over line shape: a 4-space block after a list
  item is treated as prose and so can hide a real indented `gh` command (the test pins this as intended, but it
  is a deliberate blind spot, not a closed one).
- The `_pin` helper derives the verb from the first `gh <noun> <verb>` in the entry; an entry containing two gh
  commands would pin only the first.
- `review_context.py` :594 grew a long single line (escaped quotes); style only.

## Residual debt
1. `GitHubSource.note()` cloud-blocker (UNMEASURED, NOT fixed). First attempt is GraphQL-backed `gh issue comment`;
   the REST fallback (`gh api .../comments -X POST`) runs only after a TRANSIENT failure; a non-transient
   GraphQL-blocked error is re-raised. `handoff.py` wraps it (warning); `acceptance.py`, `feature_labels.py`,
   `feature_owner.py`, `feature_propagate.py`, `auto_unpark.py` do not obviously. Fix is a policy choice:
   in a no-GraphQL session go REST-first or treat a non-transient GraphQL failure as a fallback trigger. Needs
   an owner call, and a real cloud run to see how the proxy fails GraphQL.
2. R5 deferred: `work.py` `_sibling_pull_requests` (`gh pr list`). REST lacks `reviewDecision`, `gh_api` has no
   `isDraft` mapper, rollups need per-row calls (~40-60 lines, ~6 tests). Low value: `sibling_gate` feeds only
   `unit_completion` after `done`. Listed under "What remains".
3. Possible stale text in `docs/cloud-sessions.md`: line ~191 ("`note()` (already REST as its last resort, with the
   owner-accepted ...") and ~581 ("NOT moved: ... `sources.note()`") predate the new finding and may read as
   "note() is fine". Worth a one-line cross-reference to the new section; not edited here (standing doc).
4. End-to-end cloud run still UNMEASURED: token `permissions` on `GET repos/{owner}/{repo}`, placeholder
   expansion in the reviewer cwd, the proxy's GraphQL failure mode, board-skip notice live.
5. Not scanned by either ratchet: `gh_api.py` fallback argv, shell scripts, `.tmpl`, incrementally built argv.
6. `Bash(gh api *)` grant check on reviewer-spawning skills (plan P3) was to be REPORTED if missing; this retro did
   not see that report. Confirm it was done.

## Lessons (proposals, not applied)
- A "stale clause" in prose is worth a code trace before rewriting: the dossier's catch that `merge_rights` is
  REST is what kept the new string true.
- A guard's deliberate-break control must be run on the docs' gesture (`pytest tests/test_no_direct_gh_prose.py`,
  no extra flag); the five controls in the plan are the proof owed, and should be pasted into the PR body.
- Name unfixed findings in the same artifact that finds them (done for note()), and sweep older sentences that
  imply the opposite (item 3).

## Follow-ups to file (none filed by this retro)
- note() REST-first / fallback-on-GraphQL-blocked (owner decision).
- R5 sibling-PR REST read (low priority).
- Real Claude Code cloud smoke run per `docs/cloud-sessions.md` owner steps.

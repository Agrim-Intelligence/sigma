GRADE: achieved
# #895 slice 4a-2 PR B retro (advisory)

## Delivered vs done criteria (uncommitted diff in .sdlc/work/895, 11 files, +1433/-449)
- R9 `work._find_design_pr` and the R4 CHANGES_REQUESTED half of `review_gate` now read through `gh_api`
  (REST first, one gh fallback each, built inside gh_api): `pr_changes_requested`, `open_prs_for_head_gh`.
- `reviewDecision` stays GraphQL-only and parks in approval mode when GraphQL is unavailable (per the cut).
- Ratchet: work.py BASELINE 8 -> 7 asserted in tests/test_no_direct_gh.py (controls C10a/C10b see it red).
- Fail-closed everywhere: truncated/malformed/non-list replies raise, never read as "no PR" or "no changes requested".
- Leftovers E done (`repr(r)[:120]`, branch-quoting test). CHANGELOG and docs/cloud-sessions.md updated.
- Controls: 16 mutations run with the documented pytest gesture, each seen RED, restored byte-for-byte, suite green
  (2067 passed over 8 targeted files). See 895-4a2b-controls.md.
- Not measured: live-GitHub latency and the full-suite run were not part of this log; nothing here claims timing.

## Residual debt
- Fork-PR invisibility: `head=owner:branch` hides forks from another owner. Safer than before, but a design PR
  from a foreign fork is not seen at all; documented, not remedied.
- `work.py` limit guard is unreachable now: gh_api raises at the cap first, so the old DESIGN_PR_LIMIT check is dead code.
- Fallback type checks are shallow (shape/list checks, not per-field typing); REST path is stricter than fallback.
- `pulls/N/files` read is page 1 only (per_page=100); changed_files > 100 refuses rather than pages.
- C11 `sdlc_dir=` half not run separately (argued it would go red for the opposite reason); weak spot in control coverage.
- `.sdlc/acceptance/895.md` still describes PR A only; PR B criteria live in the plan, so done_when is not checkable
  from the acceptance file.
- Still open on #895: R5 `_sibling_pull_requests`, `_unresolved_threads`, `reviewDecision`, PR writes (4b/4c).

## Proposals for standing docs (not applied)
- acceptance/895.md: append PR B checkboxes (or a per-slice acceptance file) so verify reads the right criteria.
- docs/agent-rules-detail.md: note that a migrated guard's old limit check becomes dead; delete or test it in the same PR.
- docs/agent-rules-detail.md: a REST filter such as `head=owner:branch` can narrow what a gh query saw; record the
  visibility change as a behaviour change in CHANGELOG.
- tests/AGENTS note: fixtures that write `.sdlc/state/gh-*.json` must chdir-isolate (C11a showed the repo gets polluted).

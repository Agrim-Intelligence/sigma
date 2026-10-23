# goal-review round 7: Dossier #2000, revision 6 ("lean goals")

**Reviewer:** fresh, independent subagent (Stage 2), mechanism `subagent`. I formed this view from
the code before reading `2000-review-6.md`. I wrote only this file: no labels, comments, Epic,
`compile_plan.py` run, or design/source edits.
**Tree verified:** `origin/main` at `504de5c1` (plugin 1.0.20), exported read-only. Every `file:line`
below is against that tree; design line numbers are `.sdlc/design/2000.md` as of 2026-10-23 15:26.
**Owner rulings R1-R14:** treated as decided. Every finding is about whether the design implements
them, not whether they are right.

**Verdict I would record: REJECT.** It would produce exactly two things:
- `work.py close-design .sdlc 2000 --comment "goal-review: REJECTED"`;
- `loop.py note .sdlc 2000 "goal-review: REJECTED -- P1..P5 below -- design PR: <close-design's result>"`.

No `sdlc:designed`, no Epic, no children, no board move. The fix is narrow (FR text and three
blast-radius rows in the slice-4 area, plus controls); no new sweep is needed beyond S-54. With
P1-P5 fixed as stated I would expect to CONFIRM.

## Step 1: schema and script checks

- `goal_design.py requirements`: exit 0, `ok: true`, 0 violations, 0 warnings. FR-31..FR-78 each in exactly one slice row.
- `goal_design.py quality`: exit 0, `ok: true`, lane large, 0 violations. One row open: `data_retention_privacy` (D-33).
- `goal_design.py sweep-budget .sdlc --mode lane --lane large`: `rounds: 3`, matches `**Budget**: 3`.
- Sweep `capped`; banner says 1 unswept; `## Seeds` has exactly 1 `unswept` row (S-54). Consistent.
- Phase-prose headroom re-measured with `measure_all`: implement 0, review-post-pr 3, plan-review 8 tokens. Matches PC-34.
- PC-49 re-measured with `evals/skill_structure.estimate_tokens`: implement `## Announce each step`
  (`sigma-implement/SKILL.md:97-122`) 267 words / 431 tokens; review `## Counter-review`
  (`sigma-review/SKILL.md:178-182`) 42 / 71; plan-review `## 5. Disposition` (`:147`-end) 454 / 705.
  **The figures are real.**

## Findings, ranked

### P1 (blocking, slice 4): the owning machine has no path to start slice 2, and BR-42/BR-54 contradict R14

R14 says the owner keeps the goal and continues it. Nothing in the code or the design makes it do so.
- `_next` picks only through `source.next_pending` (`loop.py:2687`), and `_fetch_pending` excludes
  every `sdlc:in-progress` issue (`sources.py:3238-3250`). R14 keeps that label, so the owner's own
  pick never returns the goal. `_awaiting_merge_skip` (`loop.py:4744-4754`) also skips it locally.
- When a human merges slice 1, the only observer is `_reconcile_awaiting_merges`
  (`loop.py:4869`), whose merged branch calls `_record(done, merged_pr=True)`: closes the issue,
  strips labels, card to Done, releases the checkout (docstring, `loop.py:4875-4880`).
- BR-42 (design :531) says that pass "releases the claim and keeps the GitHub overlay", which
  contradicts FR-37 and R14. BR-54 (:535) still says "serve between-slices goals first on any
  machine", the superseded cross-machine resume. FR-64 (:355) is triggered by "WHEN the owning
  machine continues", and nothing defines that.

A slice-4 implementer following the mapping would build release-and-repick, which R14 forbids.

**Change.** Add an FR plus a BR row: the merge-reconcile pass, on a non-final slice merge, keeps the
claim and the checkout, records `slice_merged`, and does not record `done`. `_next` then returns the
machine's own between-slices goals (from the local work record, with a merged slice and no open slice
PR) before calling `next_pending`, re-opening the ledger lease. Rewrite BR-42 and BR-54 to match.
Control: a human merges slice 1 with auto-merge off; the owner's next `_next` must return the goal at
slice 2. Break it by removing the preamble, and the goal sits idle until the 72-hour stall.

### P2 (blocking, slice 4): "the existing reclaim path" cannot release a between-slices goal, and D-46 is false

FR-38 (:290-292), BR-66 (:539) and Detailed design step 5 (:782) send the 72-hour stall into "the
existing reclaim path". That path is `_auto_reclaim_stale_claims` (`loop.py:1319-1443`). It does
nothing at all unless `ledger.enabled` is set (`:1428`, default off), and it only reclaims **open
ledger claims whose lease expired** (`:1430-1440`). `record review` deliberately ENDS that lease
"so `_auto_reclaim_stale_claims` can never hand a waiting goal back to Ready" (`loop.py:4711-4712`).
So:
- With the ledger off (the default), nothing releases a dead owner's goal.
- With the ledger on, the lease is already ended between slices, so nothing releases it either.
- D-46 (:716-717), "turning the ledger on shortens it to the claim-lease TTL", is false for exactly
  this state.

Status and doctor only mark the goal stalled; both are read-only. The goal therefore stays
`sdlc:in-progress` forever, and its `Blocked by` dependents with it. That breaches RESILIENCY
("recovers without a human").

**Change.** Name a new, ledger-ungated stall sweep. It runs in `_next`'s reclaim step on every
machine, inside the budget gate because it writes. It reads `newest_label_event` age and open slice
PRs (bounded per pass) and calls `_release()` (`loop.py:5234`) with a stall reason. Correct D-46.
Control: ledger off, owner state deleted, overlay aged; a second machine's `_next` must release the
goal and then reclaim it. Break the sweep and the goal stays held.

### P3 (high, slices 4/5): the final-slice close gate misses two closes and orphans discovered slices

- FR-63 (:353) gates "the merge step" only. The more common close for a human-merged slice is the
  reconcile pass's `_record(done)` → `complete()` (P1). The FR-63 control (:862) does not cover it.
- FR-63 counts **manifest** slices only. FR-76 (:387-390) keeps a discovered slice that arrived while
  the last PR was open as an action-log row plus a timeline marker, until "the next slice's branch",
  which does not exist yet. When the last manifest slice merges, `_close_issue_the_base_cannot`
  (`work.py:7106`, called at `:7800` on **every** base) closes the issue. The `Refs` switch stops only
  GitHub's close, not Sigma's, so the discovered slice is orphaned.

**Change.** Change FR-63 to: "WHILE a lean goal has a manifest slice or a recorded discovered slice
without a merged pull request, neither `merge()` nor the merge-reconcile pass SHALL close or record
done." Add a control for each close site, plus one for "discovered slice pending at last merge".

### P4 (high, slice 4): the stall clock does not see an active owner, so slices can run twice

FR-37 (:288) adds the overlay when slice 1's PR opens or merges. Nothing removes or refreshes it when
the owner starts the next slice. D-44 (:711) contradicts FR-37 on when it is set. `newest_label_event`
(`gh_api.py:999`) returns the newest `labeled` event, and re-adding a label that is already present
emits no new event. So an owner actively implementing slice 3 (no PR open yet) more than 72 hours after
slice 1's PR opened is stalled and released. The new owner then derives "slice 3: no merged and no
open PR" and starts it, while the old owner opens its own slice-3 PR. That breaks the quality target
"0 slices ... run twice" (:402). D-45 covers only an *idle* owner.

**Change.** Have the owner remove the overlay when it starts a slice and add it when a slice PR opens
or merges. Alternatively, define the age as the newest of the overlay event, the last slice PR event
and the last push to a work.py slice branch. Control: an active owner with an old overlay is not
stalled.

### P5 (medium, slice 4): a reclaimed goal cannot pass the phase gate at `done` (N1 residue)

`phase_gate.REQUIRED` = research, plan, plan_review, implement, review, retro, read from the local
action log (`phase_gate.py:1-40`). Only research and retro are waivable. FR-74 (:381) re-creates only
plan-review on the new machine, so its final `record done` lacks the `plan` row (and the `research`
row, for a standard goal), and is refused. The reliability scenario (:402) stops at "starts slice 2",
so its control passes while `done` is refused.

**Change.** FR-74 also records the reclaim's plan adoption (the plan sha256 read from the goal branch)
as the `plan` row, and waives research with the reason "reclaimed", or the lean phase gate accepts a
`reclaimed` row. Extend the reliability test through `record done`.

### Medium

- **P6 (slice 3).** Moving plan-review `## 5. Disposition` breaks two pinned guards that the design
  does not list: `tests/test_tier_escalation.py:362,377` (the `loop.py escalate ... --after
  plan-review` gesture at `sigma-plan-review/SKILL.md:178`) and `tests/test_zone_phase_rules.py:78-80`
  ("Structural over patchwork", `:164`). Keep those two paragraphs in place, or retarget the tests to
  the reference on purpose, and add both to D-34 and to "Guard tests that change on purpose". The real
  saving is below 705 tokens. Likewise `tests/test_d2_cold_read_wording.py:35-37` pins implement's
  "must already carry an approving `sigma-plan-review` verdict" (`sigma-implement/SKILL.md:91`), which
  the folded plan-review for small goals must change on purpose (slice 9).
- **P7 (Rollback, :902-903).** "A human finishes them" is false on the owning machine. A 1.0.x
  `_reconcile_awaiting_merges` there records `done` and closes the issue when slice 1 merges
  (`loop.py:4869`, PC-36). The release notes must say: finish or park in-flight lean goals before
  pinning 1.0.x.

### Low

- **L-a.** After a P2 release, the goal no longer carries `sdlc:in-progress`, so a 1.0.x machine can
  pick it and run the rest as one PR. D-42's "fixed" holds only until a release. State it, or keep a
  1.0.x-excluded label until a 2.0 machine claims the goal.
- **L-b.** FR-64 has no branch for "every slice merged, issue open". This is the dead-owner,
  feature-base final merge case, N5's remainder: the new owner should record `done`.
- **L-c.** FR-77's routed set has no named path, bound or prune (SCALABILITY). FR-71's per-pick
  search over every owned goal has no per-pass bound, and its writes must sit inside `_next`'s
  budget gate (#1468).
- **L-d.** A bypass "converted to a slice" by relabel and comment leaves an open, unarmed duplicate
  issue that nothing closes when the slice merges.
- **L-e.** `_LABEL_COLORS` pins (`tests/test_setup.py:358`, `tests/test_sources.py:4571`,
  `tests/test_public_bootstrap_control.py:100`) and `docs/label-model.md:1206` ("seeds exactly ten")
  change on purpose. Add them to BR-63's list.
- **L-f.** PC-50's "write verbs end at `merge_pr`, `gh_api.py:1156-1230`" is imprecise:
  `merge_pr_gh` and `close_pr_gh` are at `:1462` and `:1516`. "No PR-body edit helper" holds. A REST
  `PATCH pulls/N` inside `gh_api.py` (exempt) keeps `tests/test_no_direct_gh.py` green.

## Status of review-6's findings

| Finding | Status |
| --- | --- |
| N1 | **partly fixed.** R14 option (b) with FR-74 and BR-65 handles the plan gates. The phase-gate half (research and plan rows) is still open: P5 |
| N2 | **fixed** while `sdlc:in-progress` is kept (FR-37, PC-45, control :865). Residue after a release: L-a |
| N3 | **fixed.** FR-75 covers seeding, strip and views; the overlay is outside `overlay_labels`; the board stays QC / In Progress. Test pins: L-e |
| N4 | **partly fixed.** The duplicate is fixed (FR-64/66 "neither merged nor open"). "Dead" now has a GitHub definition, but the release mechanism does not exist (P2) and the clock misses an active owner (P4) |
| N5 | **fixed for the default branch** (`Closes` on the last slice). Feature-base remainder: L-b |
| M1 | **fixed.** FR-77 routed set, slice 7, control :876. Storage: L-c |
| M2 | **fixed** as to location (FR-76), but it opens P3 |
| M3 | **fixed.** Rows for FR-33, 38, 43, 44, 48, 65, 67 are present (:864-874). New gaps: P1-P4 controls |
| M4 | **fixed.** FR-42 is now a WHERE / SHALL NOT |
| M5 | **fixed.** FR-78, D-47, checkpoint issue |
| L1 | **moot** under R14, but BR-54 still carries the superseded text (P1) |
| L2 | **fixed** (FR-38 reports unknown) |
| L3 | **fixed** (BR-69) |
| L4 | **fixed** (routed set, D-40) |
| L5 | **fixed** (FR-65) |

## Adjudication (2a)

| ID | Bucket | Note |
| --- | --- | --- |
| D-1, D-2, D-3, D-20, D-27, D-30, D-31, D-35, D-36, D-37, D-41, D-42, D-43, D-50 | resolved | hold against the code. D-42 has the L-a residue |
| D-32 | resolved in intent | "the existing reclaim path" is wrong as built: P2 |
| D-46 | **blocking** (slice 4) | factually false: P2 |
| D-15, D-17, D-21, D-24, D-28, D-33, D-38, D-44 | open, not blocking | D-44 must be reconciled with FR-37 (P4) |
| D-29 | open, not blocking | carry, but FR-70 must REFUSE when `action_log` is off, never skip (SAFETY) |
| D-34 | open, not blocking | figures verified; add the P6 pins |
| D-39, D-40 | open, not blocking | owner boundary questions, consistent with `file_triage.py:15,34` |
| D-45 | open, not blocking | acceptable for an idle owner; the active-owner case is P4 |
| D-47, D-48, D-49 | open, not blocking | slice 12, slice 4 and slice 4 own them |
| B-3 | open, not blocking | no slice waits |
| X-1..X-3, X-7, X-11..X-14, X-18, X-21, X-23, X-26..X-30 | resolved | |
| PC-26, PC-27 | open, not blocking | X-21 and D-20 cover them |

## Owner aims

1. **Armed count:** met in design (FR-47..51, 67, 68, 77, 71). 1.0.x filings are counted, not stopped. L-a can re-arm via a 1.0.x pick.
2. **Slices skip research, plan and plan-review:** met (FR-42, FR-70). **Not met end to end:** slice 2 never starts (P1), and a reclaimed goal cannot finish (P5).
3. **Small goals by judgment, with a PR-time check:** met (R10, FR-55, FR-69).
4. **Cost, tokens and time:** headroom figures are real. Savings are unmeasured and honestly labelled (D-38). P6 lowers the plan-review saving.
5. **Seamless upgrade, core loop intact:** upgrade is met (FR-31..35). **Core loop is not intact:** pick (P1), reclaim (P2), close (P3), rollback (P7).
6. **Host parity:** met in mechanism. All new triggers are Python in `_next` and the tick.

## What I could not verify

- Live GitHub numbers (215 armed; 107 / 22).
- The n=79 cost baseline.
- Whether GitHub re-parses closing keywords on a PR body edit (FR-36's switch to `Refs`). It is
  believed but unmeasured here. Add a live control.
- How the DAG renderers show the overlay (S-54).
- Conflict volume on the landing.
- No end-to-end loop run on any host.

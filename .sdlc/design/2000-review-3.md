# goal-review (round 3): #2000, revision 3 of the design

**Reviewer**: independent. I wrote neither `.sdlc/design/2000.md` nor either earlier review. **Date**: 2026-10-23
**Checked against**: a `git archive origin/main` export at `504de5c1` (the commit the design swept; `git fetch` confirms it is still `origin/main`).
**Mechanism**: a subagent dispatched by the owner's session. `reviewer.py resolve` and `review_context.py brief`
were NOT run: this is a feasibility review only, per the dispatch's hard limits.

## Verdict I would record: **CONFIRM**, with 7 items carried

N-1 to N-6 and F-7 are genuinely resolved, not just reworded. No item below stops a slice from
being correctly scoped and started, so none is blocking (adjudication 2a). Two of them (R-1, R-2)
are shadow-semantics holes of the same kind as round 2's N-3. They should go on the slice 4 and
slice 6 tickets as acceptance text, or better, be fixed in the design before compiling. Each is a
one-sentence fix.

**What this verdict would produce. None of it has been done.**
- `sdlc:designed` on #2000 (`loop.py mark-designed`).
- One Epic plus six children via `compile_plan.py --forbid-priority`, each stamped `sdlc:designed`,
  with `epic.originates_from: 2000`.
- `blocked_by` edges: 2→1, 3→1, 4→2, 5→3, 5→4, 6→5.
- 4d lands the design PR. The design is local-only, so the result is `no open design PR found`.
- The outcome comment: `goal-review: CONFIRMED (<k> open questions carried) [sweep: CAPPED] -- ...`.
  It uses CAPPED, not converged, for the reason in R-4. It names the falsified PCs and the carried items listed below.
- Step 5: one feature-ification ask at the Epic level. This is the Product path.

## Mechanical checks (re-run by me)
- `goal_design.py requirements` (installed 1.0.19): exit 0, `ok: true`, 0 violations, 0 warnings.
  FR-1..6, 15..19, 21, 23..30 each map to a slice: 1:{1,2,3,23,24}, 2:{4,5}, 3:{17,18,19,29},
  4:{6}, 5:{15,16,21}, 6:{25..28,30}.
- `goal_design.py quality`: exit 0, 0 violations.
- `goal_design.py sweep-budget .sdlc --mode lane --lane large`: `rounds: 3`, which matches `**Budget**: 3`.
- Schema: Sweep, Seeds (38 rows, 0 unswept), Budget, Premise check, Out of scope, and ids on every
  D, B, X and PC are all present.

## Earlier findings
| ID | Status | Evidence |
| --- | --- | --- |
| N-1 | **resolved** | FR-4 is now diff-only ("working-tree diff against its fork point"). FR-6 binds "WHEN a pull request is opened". D-1 is restated, X-23 defers the plan estimate, and BR-7 cites `zones.py:476,485-494`. I confirmed that the `--plan` branch emits `"-"` counts and `_diff_counts` sits only on the else branch (`zones.py:485-496`). Residue: the performance quality row still says "at plan-review" (R-6) |
| N-2 | **resolved** | Slice 6 is shadow-only. FR-26 says "SHALL NOT refuse", and no FR or slice claims enforcement. X-24 defers Plan-authored tests and enforce, citing `seal_tests.py:406-410` (verified: `Refused(3, "no-plan-review", …)`). FR-30 splits off the survivor half (`review_tests.py:160-187`, which is the clause that yields `partial`). D-25 honestly says red-proven will read near 0. New gap: R-2 |
| N-3 | **resolved** | Off ignores the flags with a stderr line. Shadow files exactly as today plus a `would` row (FR-17, FR-29). Enforce means defer-only (FR-18 is now `WHILE ... enforced`). FR-2 ("shadow writes nothing") is now consistent, because only enforce writes |
| N-4 | **resolved** | The table matches the prose: 1 → {2,3}, then 4 after 2, 5 after {3,4}, 6 after 5. So 4→5→6 are strictly serialised on `work.py`'s `pr`/`record_plan_review` seam. Kinds per slice: 1 `would`, 2 `size_class`, 3 `deferred`, 5 `scope_grew`, 6 `test_map`. Slice 4 adds no kind and reuses `size_class` and `would`, which is consistent. Nit: 2 and 3 run in parallel and both edit `actionlog.py`'s closed whitelist (`:94-137`), a trivial textual conflict |
| N-5 | **resolved** | D-1, BR-11 and Enforcement points now say `record_plan_review` refuses on a hash mismatch and on F02 (verified at `work.py:1570-1589`) |
| N-6 | **resolved** | FR-6 says "counting every mechanical line as unverified until regeneration exists" |
| F-7 | **resolved, with one wrong rationale** | Size binds at `pr`, the manifest at record and `pr`, and the map nowhere (shadow). See R-3 for size's coupling to `gates.plan_review` |

## Findings (round 3)
- **R-1 (medium, slice 4). In shadow, the size verdict changes reviewer input.**
  - Detailed design: "The verdict is rendered into the code and PR review briefs", with no mode
    condition (`2000.md:576-577`, BR-12).
  - In shadow, a reviewer told "over cap" can SEND-BACK for size. So shadow is not "logging what
    they would do, changing nothing" (in-brief), and the shadow data gets contaminated.
  - The `review-pre-pr` and `review-post-pr` briefs are also budget-measured, with 91 and 89 words of
    headroom (BR-29).
  - **Change:** render the verdict only in enforce, or only when the mode is not off and labelled
    advisory, with that consequence stated. D-10's "render only when on" rule should name these two
    briefs, not just plan-review.
- **R-2 (medium, slice 6). Nothing produces `--review-output` at plan-review, so shadow can silently record nothing.**
  - FR-25 fires only "WHEN a plan-review verdict is recorded with the reviewer's full output".
  - Code review gets that output only because its gate refuses without it (`review_tests.py:216`,
    `sigma-review/references/test-review.md:47-48`). A shadow gate cannot refuse.
  - So a goal whose orchestrator omits the flag logs no `test_map` row. That reads exactly like a
    goal that was never checked, and it breaks LIVENESS (absence is not distinguishable from "nothing to do").
  - The addendum's own shadow says the map "is requested and logged".
  - **Change:** the record gesture rendered in the plan-review brief (Python, like `work.py:6240`'s
    `gesture`) includes `--review-output <file>` when the map is on. A record without it, while the
    map is on, writes a `test_map` row with `output: absent`.
- **R-3 (low, FR-6 and D-1). The `gates.plan_review` coupling for size has a false rationale.**
  - D-1 says "Enforce requires `gates.plan_review` on, because the `pr` check runs only under it
    (`work.py:6245`)".
  - But the design adds size as "a new entry in `pr`'s refusal list". Existing entries
    (`_test_first_refusal` `work.py:4446`, `_sealed_pr_refusal` `:4452`, the acceptance refusal
    `:4409`) run whatever the plan-review gate says. Only `_plan_review_refusal` (`:4436`) is gated.
  - The coupling is safe, since it only narrows enforce. Its real justification would be that D-1's
    remedy needs a fresh plan-review.
  - **Change:** state that reason, or drop the conjunction from FR-6.
- **R-4 (medium, sweep record). `converged` is not demonstrable as recorded.**
  - Seed closure holds: 38 rows, all swept.
  - The quiet round is round 5, and `## Queries run` records **no** round-5 query (rounds 1-4 only).
    So the round's hits cannot be re-run, and the kit calls such a round "unrecorded", not quiet.
  - I re-ran three recorded queries and found hits in neither the blast radius nor Out of scope:
    - Round 4 `rg -n "classify_sealed|SEALED_PROVENANCE|sealed" skills/sigma-loop/scripts/*.py` →
      `rulebook.py`, `applier.py`, `decision_record.py`, `goal_state_prune.py`, `breaker_run.py`.
    - Round 3 `create_tracked_issue\(|…create_dependency\(` → `sources.py` (`:661`
      `create_dependency(…, goal_label=True)`, the write slice 3's deferral actually reaches)
      and `backlog_check.py`.
    - `deferred_from|…|milestone` → `post_guard.py`.
  - None is in scope on substance: "sealed rule" is a different concept, breaker_run is unchanged,
    and `sources.py`'s `goal_label` parameter already supports the no-goal-label filing.
  - Separately, the lane budget is 3 and the pass ran 5 rounds. The design says so openly and the
    owner directed it, but the kit's lane rule says such a pass reports `capped`.
  - **Change:** add round-5 queries and the missing X-n entries (or a BR row for `sources.py:661`).
    Until then the comment marker is `[sweep: CAPPED]`. This does not affect the verdict: capped is not a REJECT.
- **R-5 (low, slice 5). FR-15 and FR-16 carry no mode clause, and the freeze's shadow behaviour is unstated.**
  - `record_plan_review` raises on a mismatch. If the manifest hash joins that raise, it refuses even in shadow.
  - It is also unstated whether a recorded amendment satisfies the `pr` check, or forces a fresh plan-review.
  - **Change:** one sentence: shadow logs a `would` row on a manifest mismatch, and enforce accepts
    the approved hash plus the recorded amendment chain.
- **R-6 (low, stale text).**
  - Handoff (`:647`) says "this is revision 2".
  - The performance quality row's Source says "at plan-review and at `work.py pr`", which
    contradicts N-1. Its "at most 3 git processes" contradicts Scalability's "one git process".
  - The Revision 2 note says "slices 1-5". That is historical, but it reads as current.
- **R-7 (low, slice 1 and onward). The shadow-writes-nothing control only covers features that already exist.**
  - It is built in slice 1, before any feature exists.
  - Slices 3 and 5, which write in enforce, must each add their feature to the control and see it go
    red under their own break ("Run the control" rule).

## Adjudication (by id)
- **Resolved**: D-1 (the restatement holds; R-3 nit), D-3, D-5, D-6, D-7, D-8, D-9, D-11, D-12,
  D-13, D-14, D-16, D-22, D-23, D-26. X-1..X-26 are accepted (X-22, X-25 and X-26 were checked).
- **Open, not blocking** (carried): D-2, D-4, D-10 (with R-1), D-15, D-17, D-18, D-19, D-20,
  D-21, D-24, D-25 (with R-2), PC-17, PC-26, PC-27, R-1..R-7.
- **Blocking**: none in the table. B-1, B-2 and B-3 block only the deferred follow-on, which is not compiled.
- **Falsified premises to carry onto #2000's body**: PC-1, PC-3 (shape), PC-7 (in part), PC-8, PC-12,
  PC-13, PC-14 (manifest), PC-15, PC-18, PC-23 (in part).

## MVP cut, effort and tier
The cut is right: one Epic, six slices, shadow-first, with the follow-on deferred.
- Slice 3 (medium, Opus) and slice 5 (large, Opus) are right.
- Slices 1, 2 and 4 (medium, Sonnet, Opus plan-review) are right.
- Slice 6 at medium/Sonnet is plausible now that it is shadow-only. R-2 adds a few lines, not a tier.
- Slice 6 stays cuttable without touching 1-5 (D-25). Its value depends on R-2 being fixed.

## Not verified
- Pick, phase and label parity on an adopter config.
- The 60-PR size sample, the 215/504 backlog counts and the phase-budget headroom: none re-run.
- That round 5 actually ran (it has no recorded queries).
- That the design was read against the issue body: I did not re-fetch #2000 (`gh` not used).
- No `reviewer.py` or brief run, and no end-to-end shadow run of anything.

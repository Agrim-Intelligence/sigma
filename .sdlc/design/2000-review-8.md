# goal-review round 8: Dossier #2000, revision 7 ("lean goals")

**Reviewer:** fresh, independent subagent (Stage 2), mechanism `subagent`. I formed this view from the
design and the code before reading `2000-review-7.md`. I wrote only this file: no labels, comments,
Epic, `compile_plan.py` run, or design/source/test edits.
**Tree verified:** `origin/main` at `504de5c1` (plugin 1.0.20), read with `git show origin/main:<path>`.
Design line numbers are `.sdlc/design/2000.md` as of 2026-10-23 15:40.
**Owner rulings R1-R15:** treated as decided. Every finding is about whether the design implements
them correctly, safely and buildably.

**Verdict I would record: REJECT.** It would produce exactly two things:
- `work.py close-design .sdlc 2000 --comment "goal-review: REJECTED"`;
- `loop.py note .sdlc 2000 "goal-review: REJECTED -- Q1..Q6 below -- design PR: <close-design's result>"`.

No `sdlc:designed`, no Epic, no children, no board move. Every review-7 finding is fixed or nearly so,
and the fix for this round is again narrow: FR text and controls in the slice 4, 5 and 7 area, five
blast-radius rows, and stale-text cleanup. No new sweep is needed (S-59 is answered below). With Q1-Q6
fixed as stated I would expect to CONFIRM.

## Step 1: schema and script checks

- `goal_design.py requirements`: exit 0, `ok: true`, 0 violations. One warning: 59 FRs (> 50). FR-31..FR-89
  each in exactly one slice row.
- `goal_design.py quality`: exit 0, `ok: true`, lane large, 0 violations. One row open:
  `data_retention_privacy` (D-33).
- `goal_design.py sweep-budget .sdlc --mode lane --lane large`: `rounds: 3`, matches `**Budget**: 3`.
- Sweep `capped`; banner names 2 unswept; `## Seeds` has exactly 2 `unswept` rows (S-54, S-59). Consistent.

## Findings, ranked

### Q1 (blocking, slice 5): the superseded machine still writes, and its reconcile pass undoes a takeover or restart

FR-81 fences only "each slice start and each pull request create or push or merge". The superseded
machine keeps its local work record, worktree and `awaiting_merge` flag, and its merge-reconcile pass
is not fenced:
- `_reconcile_one` (`loop.py:4973`) calls `_record(parked, "PR #N was closed without merging")` when the
  slice PR it was waiting on closes unmerged. `resume --restart` (FR-83) closes exactly those PRs. So the
  old owner's next pick or tick parks the goal the restarter is now working (`_offboard` strips
  `sdlc:goal` and `sdlc:in-progress`, `sources.py:3430+`).
- After a `--force` takeover, the same pass on the old owner records the slice merge (FR-85), or on the
  final slice `_record(done)` (closes, releases the checkout, a second ledger `done`), for a goal it no
  longer owns.
- FR-84 correctly stops the old owner continuing, but nothing retires its local record or worktree.
  They leak, and keep feeding the pass above.

**Change.** FR-81: every GitHub write for a lean goal (the reconcile pass and every `_record` terminal
write included) checks the epoch first. A machine that sees a newer epoch retires its local work
record, `awaiting_merge` flag and worktree, locally only, with no GitHub write. Controls: restart while
the old owner holds an `awaiting_merge` flag on the closed PR, and the goal must not be parked; break
by removing the fence from `_reconcile_one`.

### Q2 (blocking, slices 5/7): a resumed slice with an open PR can never pass FR-70

FR-64 reuses an open slice PR on a new machine. FR-70 then requires "that slice's red record and
pre-implementation test review ... recorded with the slice id" at its push and merge. Both are local:
the red proof is the witness journal (`red_green.py:5` "Local evidence", `:252`), and the action log
is per machine. The new machine cannot re-record red, because the implementation is already on the
branch. A dead owner with an open slice PR, which is the common death, is therefore stuck, and the
reliability scenario (:491, "reuses any open slice PR and finishes") cannot pass.

**Change.** Publish per-slice evidence on the slice PR (red-witness digest, the pre-implementation test
review verdict, and the code review), read the same way as FR-65. FR-70 then accepts it for a resumed
slice. Alternatively, FR-64 states that a resumed unreviewed slice re-records red against the PR base
with the PR's test files. Add a control: a resumed open-PR slice merges with no local rows.

### Q3 (high, slices 4/5/8): 1.0.x and 2.0 can still release a lean goal outside the park lane, so D-42 is false

D-42 says "a 1.0.x machine never picks a lean goal at any point". Three existing paths strip
`sdlc:in-progress` without the lean-stalled park:
- **Blocking hand-off.** FR-50 keeps today's behaviour for these. `handoff.py:1290` and `loop.py:5042`
  call `mark_blocked`, which removes `sdlc:in-progress` (`sources.py:3362-3409`). `auto_unpark.py:378-436`,
  on by default on 1.0.x too, later swaps a `sdlc:blocked` goal to plain `sdlc:goal`, with no
  WAITING_MARKER needed. Then any 1.0.x pick takes it and closes the issue after one PR.
- **Ledger reclaim.** With `ledger.enabled`, the lease is open from pick until the first `record
  review`. `_auto_reclaim_stale_claims` (`loop.py:1428-1440`) `_release`s an aged lean claim on any
  machine.
- **A human `/sigma-unpark`** of a lean-stalled park restores plain `sdlc:goal`.

In every case, no FR routes a 2.0 normal pick of a goal that carries published evidence into resume.
FR-64 and FR-66 name "resumes" and "unparked", but FR-34 classifies it afresh at pick.

**Change.** Add an FR: a pick that finds published lean evidence on the issue resumes it (FR-64/74,
epoch bump), never re-classifies it. Add an FR: on 2.0, a lean goal's blocking hand-off and a 2.0 ledger
reclaim park it as lean-stalled rather than `mark_blocked` or `_release`. Correct D-42 and the in-brief
to name the residual 1.0.x paths (1.0.x ledger reclaim, human unpark), and add them to D-55's release
notes ("upgrade every machine before lean goals run with the ledger on"). Add BR rows for
`sources.py:3362`, `handoff.py:1290`, `loop.py:5042` and `auto_unpark.py:378-436`.

### Q4 (medium, slice 5): the plan sha is checked against the wrong ref

FR-74 says "from the goal branch or main". In this codebase "main" in `plan_copies` (`work.py:3955-3990`)
is the **main checkout's working-tree copy**, and Plan writes there first (`:3966-3968`, #2237). On a
resuming machine:
- that copy does not exist;
- slices have no single goal branch;
- on a `feature/<unit>` base the merged plan is on `origin/feature/<unit>`, not main.

Also, if the owner dies before the first slice push, the plan exists on no remote ref, so every resume
refuses and nothing names the lever.

**Change.** FR-74 reads the plan with `git show` from the open slice PR's head, else from
`origin/<slice base>`. Plan-review publishes only after the plan is pushed, or a refusal names
`resume --restart`. Add a control: verify against the wrong ref and see it go red.

### Q5 (medium, slices 4/5): evidence, epoch and slice markers are not read through the trusted-author filter

D-52 claims "the same trust level labels have today" (repository write). That holds only if readers drop
untrusted authors. On a public repository any signed-in user can comment. The design never names
`sources.trusted_marker_comments` (`sources.py:880`, `TRUSTED_ASSOCIATIONS` `:854`), which already exists
for this. Without it, an outsider's epoch comment stops every machine at its fence, and repeated
outsider bumps count as "activity" (FR-38), so the goal never stalls. A forged plan-review comment
carrying the current plan's sha also passes FR-74, since anyone can compute that sha.

**Change.** FR-65, FR-74, FR-79 and FR-81 read markers only through `trusted_marker_comments` (fail
closed). Add a control: an untrusted-author epoch is ignored.

### Q6 (medium, slice 6): delta-only review evidence does not travel

FR-43 gates a discovered slice on an approving delta-only review. FR-79 publishes only the goal-level
plan-review. A resuming machine that meets a discovered slice has no evidence that the delta review
was approved. It must either refuse or re-run the review, and neither is stated.

**Change.** Publish the delta verdict with the manifest of record (FR-76), or state that a resume
re-runs the delta-only review.

### Medium / low (carry or fix in text)

- **M-a. Stale R14 text contradicts R15.**
  - The observability quality row (:496) says "stalled only with no open slice PR ... an open slice PR
    suppresses it". Its test would hold a dead owner with an open PR forever, which is the failure the
    FR-38 control (:1018) must catch.
  - Lines :168-169 ("under R14 ... reclaimed through the existing path"), BR-65 (:639, "re-runs
    plan-review") and D-35 (:786, "no cross-machine resume pick") are also stale.
- **L-1.** Stall-sweep placement. ":919/BR-66: in `_next`'s reclaim step inside the budget gate" cannot
  both hold: reclaim (`loop.py:2651`) runs before the gate (`:2665-2671`). Put it beside
  `_reconcile_awaiting_merges`, inside the gate, because it writes.
- **L-2.** Takeover with a merge in flight. The old owner's merge lands after the new owner read "open".
  The new owner must re-derive slice state after each fence check and treat "already merged" as done.
- **L-3.** FR-38's "slice start read from GitHub" implies one marker write per slice start, but the
  performance row (:488) counts none. The stall sweep's per-goal reads (label events, PR events,
  comments) on every machine each pick, and the resume lane's parked-list read, belong in D-54 with a
  per-pass bound.
- **L-4.** The run-the-control table has a blank line at :1008, so every row after the first renders
  as a paragraph.
  - FR-81 lists only its push clause. Break slice start, create and merge separately, per AGENTS.md.
  - FR-79, FR-88, FR-89, FR-38's "epoch bump is activity" and "unknown age", and the D-51 tie-break have
    no control.
- **L-5. D-41 residue.** If a human merges the last PR before the switch to `Refs` lands, GitHub closes
  the issue with a recorded slice pending. State that Sigma reopens it, or count it as a post-done filing.

## S-59 answered (enough to judge D-53)

`auto_unpark.compute_unpark_actions` resumes a `sdlc:parked` issue only when a WAITING_MARKER comment
follows the newest Sigma park comment (`auto_unpark.py:274-280,412`). It also skips gate kinds
(`:385`) and label-only re-parks (`:414`). A lean-stalled park therefore stays parked on 1.0.x and 2.0
alike, unless a human posts a wait decision. **D-53's deviation is sound:**
- parking keeps 1.0.x off the goal, which releasing would not;
- a 2.0 resume lane recovers it with no human;
- `loop.py resume <goal>` is the explicit gesture.

The caveat is Q3: the park comment's fixed prefix tells humans to review it, and the documented human
gesture, `/sigma-unpark`, makes the goal plain `sdlc:goal`. The park note should instead name `loop.py
resume`.

## Status of review-7's findings

| Finding | Status |
| --- | --- |
| P1 | **fixed.** FR-84 continue-own-goal, FR-85 non-final reconcile; BR-42 (:632) and BR-54 (:636) rewritten; control :1016 |
| P2 | **fixed.** A ledger-independent stall sweep (FR-38, FR-86, BR-66); D-46 corrected. Placement nit L-1 |
| P3 | **fixed.** FR-63 covers both close sites and discovered slices; controls :1007, :1022 |
| P4 | **fixed in FR-38 and D-44.** The quality row :496 still carries the old rule (M-a) |
| P5 | **fixed** for `done` (FR-80; scenario runs through `record done`). An adjacent gap at FR-70 is open (Q2) |
| P6 | **fixed.** PC-55, PC-56, D-34, BR-71 and the guard list |
| P7 | **fixed.** Rollback and D-55 |
| L-a | **partly fixed.** The stall path parks. Blocked, ledger-reclaim and human-unpark paths remain (Q3) |
| L-b | **fixed** (FR-87) |
| L-c | **fixed** (FR-77 path and prune, FR-88 bound and budget gate) |
| L-d | **fixed** (FR-89) |
| L-e | **fixed** (BR-63 pins) |
| L-f | **fixed** (PC-50) |

## Adjudication (2a)

| ID | Bucket | Note |
| --- | --- | --- |
| D-1, D-2, D-3, D-20, D-27, D-30, D-31, D-32, D-36, D-37, D-43, D-44, D-46, D-50 | resolved | hold against the code |
| D-35 | resolved in intent | stale text (M-a) |
| D-42 | **blocking** (slices 4/5/8) | false as written (Q3) |
| D-52 | **blocking** (slice 5) | trust level holds only with `trusted_marker_comments` (Q5) |
| D-53 | resolved | sound; S-59 answered above |
| D-29 | open, not blocking | carry. FR-70 refuses, which is safe; owner call on the default |
| D-39 | open, not blocking | carry; consistent with `file_triage.py:15,34` |
| D-41 | open, not blocking | carry, with the slice-4 live control and L-5 |
| D-47 | open, not blocking | carry; slice 13 |
| D-51 | open, not blocking | carry: the deterministic branch, force-with-lease and GitHub's one-PR-per-head bound it. Add a tie-break control |
| D-54 | open, not blocking | carry, widened per L-3 |
| D-15, D-17, D-21, D-24, D-28, D-33, D-34, D-38, D-40, D-45, D-48, D-49, D-55 | open, not blocking | carry |
| B-3 | open, not blocking | no slice waits |
| X-1..X-3, X-7, X-11..X-14, X-18, X-21, X-23, X-26..X-30 | resolved | |
| PC-26, PC-27 | open, not blocking | X-21 and D-20 cover them |

## Owner aims

1. **Armed count:** met in design (FR-47..51, 67, 68, 71, 77, 88, 89). Urgent work arms at P2 (D-39).
   A 1.0.x re-pick through Q3 is a residual.
2. **Slices skip research, plan and plan-review:** met on one machine. **Not met across machines**
   when an open PR is reused (Q2) or a discovered slice is pending (Q6).
3. **Small goals by judgment, with a PR-time check:** met (R10, FR-55, FR-69).
4. **Cost, tokens and time:** headroom is real. Savings and fence and sweep reads are unmeasured and
   honestly labelled (D-38, D-54).
5. **Seamless upgrade, core loop intact:** the upgrade is met. **The core loop is not yet intact** under
   takeover and restart (Q1) and in a mixed fleet (Q3).
6. **Host parity:** met. Every new trigger is Python in `_next`, the tick or `work.py`.

## What I could not verify

- Live GitHub numbers and the n=79 baseline.
- Whether GitHub re-links closing keywords after a PR-body edit.
- `--force-with-lease` behaviour against a never-fetched remote slice branch.
- S-54 (the DAG overlay display).
- The rate-limit cost of fence and sweep reads.
- No end-to-end run on any host.

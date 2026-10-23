# goal-review round 9: Dossier #2000, revision 8 ("lean goals")

**Reviewer:** fresh, independent subagent (Stage 2), mechanism `subagent`. I formed this view from the
design and the code before reading `2000-review-8.md`. I wrote only this file. I created no labels,
comments, Epic or children, did not run `compile_plan.py`, and edited no design, source or tests.
**Tree verified:** `origin/main` at `504de5c1` (plugin 1.0.20), read with `git show origin/main:<path>`.
Design line numbers refer to `.sdlc/design/2000.md` revision 8.
**Owner rulings R1-R15 and the D-53 deviation:** treated as decided. Every finding below is about
whether the design implements them correctly, safely and buildably.

**Verdict I would record: REJECT.** It would produce exactly two things:
- `work.py close-design .sdlc 2000 --comment "goal-review: REJECTED"`;
- `loop.py note .sdlc 2000 "goal-review: REJECTED -- F1..F3 below -- design PR: <close-design's result>"`.

It would produce no `sdlc:designed`, no Epic, no children and no board move.

All six of review-8's Q findings are genuinely fixed. The three new blocking findings are the same
kind of defect as Q2, missed rather than introduced by revision 8:
- F1: a local-only gate input that a resuming machine does not have.
- F2: slice identity across a restart.
- F3: the closed-issue / not-yet-done boundary.

Each needs an FR clause, a blast-radius row and a control; none needs a new sweep. With F1-F3 fixed
as stated, I would CONFIRM.

## Step 1: schema and script checks (verbatim results)

- `goal_design.py requirements .sdlc/design/2000.md`: exit 0, `ok: true`, `violations: []`.
  It printed one warning: `63 functional requirements (> 50): consider decomposing the target`.
  FR-31..FR-93 each sit in exactly one slice row.
- `goal_design.py quality .sdlc/design/2000.md`: exit 0, `ok: true`, lane `large`, `violations: []`,
  `warnings: []`. One row is open: `data_retention_privacy` (`open: D-33`).
- `goal_design.py sweep-budget .sdlc --mode lane --lane large`: `rounds: 3`, which matches `**Budget**: 3`.
- Sweep `capped`. The banner names 1 unswept seed, and `## Seeds` has exactly 1 `unswept` row (S-54).
  Consistent.
- Citations spot-checked on main all hold:
  - `work.py:3955-3990,6127,6221,7795`;
  - `loop.py:1319-1443,2649-2672,4869-4976,5040-5043`;
  - `sources.py:852-890,3362-3409`;
  - `auto_unpark.py:269-280,378-436`;
  - `gh_api.py:918,927,999,1462,1516`;
  - `handoff.py:1288-1291`;
  - `.gitignore:11-20`.

## Findings, ranked

### F1 (blocking, slice 5): a resumed goal still hits local-only gate inputs that FR-74 does not port

R15 says any machine resumes, and FR-74 ports exactly one local input: the plan, through `git show`
plus a sha check. `pr()` and `verify` read two more inputs that exist only on the planning machine:
- **Acceptance record.**
  - `pr()` calls `acceptance.refusal(sdlc_dir, goal)` whenever `verify.enforce` is on
    (`work.py:4408-4411`).
  - `loop.py verify` refuses with no acceptance record under the same setting (`loop.py:6321-6323`),
    so `record done` is refused too.
  - Both read the root checkout's `.sdlc/acceptance/<stem>.md`.
  - `/sigma-init` turns `verify.enforce` on whenever a verify command is confirmed
    (`config.json.tmpl:103`), so this is the common install, not an edge case.
  - On machine B, that file is absent until B's root checkout is fast-forwarded over a merged slice.
    On a `feature/<unit>` base it is never there, because the root checkout tracks main.
- **Plan-review record under `gates.plan_review`.**
  - `_plan_review_refusal` (`work.py:6221-6262`) reads `state/gates/<stem>.json`, which is gitignored
    (PC-44).
  - The gate is opt-in (`config.json.tmpl:90`). Where an operator turned it on, B's first slice `pr()`
    refuses.
  - FR-80 ports the evidence only to `done`, and FR-70 only to the per-slice records.

Scenario (b) therefore fails at slice 3's `pr()` on a typical install. The reliability scenario's test
would not catch it unless its fixture has these gates on.

Restoring either file into B's **root checkout** is not the answer. An untracked copy there stops
`git pull` fast-forwarding over the commit that lands it: the AGENTS.md root-checkout rule, open as
#1161.

**Change.**
- Add an FR. A resuming machine derives every local input that `pr`, `merge`, `verify` and
  `record done` read for a lean goal from the published evidence or a remote ref, into its goal
  worktree or state directory, never into the root checkout. At minimum this covers:
  - the acceptance record, by `git show` from the same ref as the plan, with its sha in the FR-79
    evidence;
  - a plan-review gates record marked as imported, bound to the verified plan sha.
- Add BR rows for `work.py:4408-4411`, `loop.py:6321-6323` and `work.py:6221-6262`.
- Add a control: the FR-74 reliability fixture runs with `verify.enforce` and `gates.plan_review` on,
  and goes red when the import is removed.
- This is the class Q2 surfaced one instance of. A fixture with every opt-in gate on is what stops a
  tenth round finding the next one.

### F2 (blocking, slice 5): after `resume --restart`, old merged slices are counted as the new plan's slices

FR-65 counts a slice done when "its pull request from the work.py-created slice branch is merged and
carries that slice's review verdict marker" (:424-426). Two facts make that ambiguous after a restart:
- Slice ids are free-form agent strings (`slices.py` `_normalise`, `"id": str(item.get("id"))`).
- Branch names are deterministic (D-51).

FR-83 restarts from research "leaving merged slices in place", and "supersede the published evidence"
covers the goal level only. If the new plan reuses ids `1`, `2`, as a fresh plan usually will:
- FR-64 skips them, because merged PRs from `sdlc/...-1` and `-2` with slice-1/2 markers already exist.
- FR-87 can then record `done` and close the issue with none of the new work run.

`merge()` deletes merged slice branches (`work.py:7795`, `_delete_remote_branch`), so the new branch
name is free to push, and nothing errors.

A same-machine restart has a related problem. Unless restart clears the restarter's own
`awaiting_merge` flag before closing the PR, its reconcile pass parks the goal through the
closed-PR branch (`loop.py:4975`): the fence passes, because the epoch is its own.

**Change.**
- Slice branch names and slice markers carry the evidence generation (the plan sha, or the
  plan-review epoch).
- FR-64, FR-65 and FR-87 count only markers of the current generation.
- A restart's new evidence lists inherited merged slices explicitly, if any.
- FR-83 clears the local work record and flag before `close_pr_gh`.
- Controls:
  - a restart with reused ids must not count the old PRs as done;
  - a same-machine restart must not park the goal.

### F3 (blocking, slices 4 and 8): the closed-but-not-done window is defined two ways; L-5 was reworded, not fixed

- **FR-47** turns in-release filings into slices "before goal N's final done" (:378). **FR-67** defers
  only when "final done is already recorded" (:429).
- **D-41** (L-5, :844-846) and **D-50** (:887) say a filing after the issue closes but before `done`
  is a post-done filing, deferred. Under FR-47/FR-67 as written, it becomes a slice.
- **FR-63** then forbids `done` while that slice is unmerged.
- This is the normal retro path, not a race:
  - On the default branch, GitHub closes the issue when the last `Closes` PR merges.
  - Retro runs after that and before `record done` (the route's step 7; `sigma-loop/SKILL.md:175-180`).
  - Retro's residual-gap tracking items then land in exactly this window.
- The result is a pending slice on a closed issue:
  - `_stale_on_closed` strips `sdlc:in-progress` (`reconcile.py:315-318`), so the stall sweep (FR-38
    keys on in-progress) and the resume lane (parked only) never see the goal.
  - A dead owner here orphans the slice silently, which is the outcome D-41 says cannot happen.

**Change.** In FR-47 and FR-67, set the boundary to "the goal's issue is closed **or** its final done
is recorded". That matches D-50 and makes retro filings defer. Add a control: a filing after a
`Closes` merge and before `record done` is deferred, not sliced. If the owner prefers the work to stay
in the goal, the alternative is a fenced reopen. Either way the design must choose one, in FR text.

### F4 (medium, slice 5): FR-79's trigger never fires as written

FR-79 is "WHEN plan-review approves ... and the plan commit is on a remote slice branch". On main the
branch is first pushed only inside `work.py pr` (`work.py:4460`), after implement and code review. At
plan-review approval the condition is always false, so a literal build publishes nothing. Every
cross-machine resume then refuses through FR-74.

The detailed design (:984-986) states the intent ("published only once the plan commit is on a remote").

**Change.**
- Make the trigger: WHEN the first slice push carrying the approved plan lands.
- Replace "publish once" with "on each approving plan-review, newest trusted wins". FR-55 returns small
  goals to plan-review after approval, and a re-approval must supersede the earlier evidence.
- Add a control: no evidence after the first push means red.

### F5 (low, slice 5): the lean-stalled park must not be resumable by `auto_unpark`

D-53 holds because `auto_unpark` resumes a park only after a WAITING_MARKER. The applier posts that
marker itself for a `keep-parked` rule on qkind `dependency` (`applier.py:984-985`). An FR-91 park
for a blocking hand-off is naturally a dependency park. If it is recorded with that qkind:
- `auto_unpark` on either version swaps it to plain `sdlc:goal` once the blocker closes
  (`auto_unpark.py:412-436`);
- a 1.0.x machine can then pick it.

**Change.** The lean-stalled park uses its own qkind and carries the #1152 exempt marker
(`auto_unpark._is_exempt`, which 1.0.x also honours).

### F6 (low): stale or malformed text

- BR-65 (:672) still says the reclaiming machine "re-runs plan-review". FR-74 says it never does.
- BR-66 (:673) still places the stall sweep "in `_next`'s reclaim step", which contradicts :997-999
  and S-58 (L-1 residue).
- Rows S-57 and S-58 (:638-639) have 10 cells under a 6-column header.

### F7 (low, build note, slice 5): new markers must use the `sigma:` prefix

`trusted_marker_comments` filters only comments carrying a `sigma:` marker token, and passes every
other comment untouched (`sources.py:878-890`). An epoch, evidence or slice marker in any other syntax
would bypass FR-93 silently. State the prefix in FR-93.

### F8 (low, carry): stall churn while a slice PR waits on a human

An open PR with no activity for 72 hours is stalled by design. A PR awaiting human merge therefore
parks, resumes and transfers ownership every 72 hours. Each transfer retires the live owner's worktree.

**Change.** State that retire keeps the local branch, as `finish()` does (`work.py:2613`), so
unpushed commits survive.

## Status of review-8's findings

| Finding | Status |
| --- | --- |
| Q1 | **Fixed.** FR-81 covers every GitHub write and terminal record, and retires locally. BR-79 is added. Controls :1093-1099 break each clause separately |
| Q2 | **Fixed for the per-slice records.** FR-92 publishes them, FR-70 accepts them, and there is a red re-record fallback. The same class remains for goal-level inputs (F1) |
| Q3 | **Fixed.** FR-90 and FR-91; D-42 is corrected; BR-76 and BR-78 are added; D-55 has the release notes; the park note names `loop.py resume`. F5 is the remaining narrow path |
| Q4 | **Fixed.** FR-74 reads the PR head first, then `origin/<base>`, and names the restart lever; there is a control at :1102. The fix introduced F4's trigger wording |
| Q5 | **Fixed.** FR-93 adds the fail-closed trusted filter; D-52 is rewritten; there is a control at :1101. F7 is a build note |
| Q6 | **Fixed.** FR-76 publishes the delta verdict; there is a control at :1105 |
| M-a | **Mostly fixed.** The observability row and D-35 are corrected. BR-65 is still stale (F6) |
| L-1 | **Fixed** in the detailed design and S-58. BR-66 is still stale (F6) |
| L-2 | **Fixed** (:992-993, D-51) |
| L-3 | **Fixed** (D-54; the performance row counts the slice-start write) |
| L-4 | **Fixed.** The table is contiguous; FR-81 is broken clause by clause; FR-79, FR-88, FR-89, FR-38 and the D-51 tie-break have controls |
| L-5 | **Reworded, not fixed.** See F3 |

## Scenario walk (design text against the real code)

| # | Scenario | Works? |
| --- | --- | --- |
| a | 4-slice goal, one machine | **Yes**, once F4 is fixed. Each slice runs: test-write, then red, test review, implement, review, a Refs PR, awaiting merge, then FR-85 (non-final reconcile) and FR-84 (continue before `next_pending`). Slice 4 writes `Closes`, then done (FR-80), then retro. A `work.py merge` of a non-final slice leaves the awaiting-merge flag, so the next reconcile pass takes the FR-85 branch. It converges |
| b | Machine dies mid-slice 3; another 2.0 machine resumes | **No** on an install with `verify.enforce` or `gates.plan_review` on (F1). Also no if FR-79 is built literally (F4). Otherwise yes: stall park after 72 hours, then the resume lane bumps the epoch, then `git show` plus the sha check, then the open PR is reused or slice 3 restarts. If the dead machine wakes, its fence retires it |
| c | `--force` while the old owner is mid-merge of slice 2 | **Yes.** The old owner's PUT is sha-pinned. Its later branch delete, close and `_record` are fenced, so it writes nothing and retires. The new owner re-derives after its fence, sees slice 2 merged and counts it done (L-2), using the marker published at review (FR-92) |
| d | `--restart` after 2 merged slices | **No** (F2): reused ids or branch names count the old merges, and FR-87 can close the goal with no new work. Also, a same-machine restart parks itself through `_reconcile_one` unless it clears its own flag |
| e | Slice discovered while the last PR is under review | **Yes** in the normal case. The finding becomes a discovered slice in the manifest of record with its delta verdict, `edit_pr_body` switches the PR to Refs, the FR-63 gates hold on both close sites, and FR-84 runs the slice. **No** for the late-merge race and for the retro window (F3) |
| f | Mixed fleet with one 1.0.x machine | **Yes, with two documented residuals**: a 1.0.x ledger reclaim and a human `/sigma-unpark`, both covered by D-42/D-55 release notes. F5 adds a third through the applier. In (b), a fleet whose only other live machine is 1.0.x never stalls the goal: it waits for a 2.0 machine, losing no data |
| g | Small goal end to end | **Yes.** The profile is recorded, research is waived, the plan is written, and the folded test review writes the gates record that `_hard_plan_gate_refusal` and `_plan_review_refusal` accept. Then implement, review, the FR-55 safety net at `pr` (returning the goal to plan-review if it grew), done, with retro waived. No goal evidence is published, because there is no plan-review, so a cross-machine resume refuses and names `--restart`. That is acceptable but should be stated |
| h | Follow-up filed on Cursor with raw `gh issue create` | **Partly.** The owning machine's next pick or tick relabels and comments any issue that references the goal (FR-71), and FR-89 closes it when the slice merges. An issue that does not reference the goal is only counted. In the window before reconcile runs, another machine can pick an armed bypass. Both are stated (D-21, D-40) |

## Adjudication (2a)

| ID | Bucket | Note |
| --- | --- | --- |
| D-1, D-2, D-3, D-20, D-27, D-30, D-31, D-32, D-35, D-36, D-37, D-43, D-44, D-46, D-52, D-53 | resolved | They hold against the code. D-53 holds with F5 applied |
| D-41 | **blocking** (slices 4 and 8) | Its L-5 resolution contradicts FR-47/FR-67 (F3) |
| D-50 | **blocking** (slice 8) | Same contradiction (F3) |
| D-29 | open, not blocking | Carry. FR-70 refusing is safe. There is a tension with FR-31: an older config without an `action_log` key reads as off (`actionlog.py:84-87`, strict `is True`), so a multi-slice goal needs a manual edit. Recommendation: treat an absent key as on for lean goals, following the `_handoff` precedent (`config.json.tmpl:83`) |
| D-39 | open, not blocking | Carry. P2, not P0, follows `file_triage.py:15,34`. Owner question 1 |
| D-47 | open, not blocking | Carry, slice 13. Conflict volume is unmeasured |
| D-48 | open, not blocking | Carry, slice 4 (S-54). FR-75 already requires the display |
| D-54 | open, not blocking | Carry. Slice 5 should measure fence-read cost against the REST budget |
| D-15, D-17, D-21, D-24, D-28, D-33, D-34, D-38, D-40, D-42, D-45, D-49, D-51, D-55 | open, not blocking | Carry |
| B-3 | open, not blocking | No slice waits on it |
| X-1..X-3, X-7, X-11..X-14, X-18, X-21, X-23, X-26..X-30 | resolved | |
| PC-26, PC-27 | open, not blocking | Covered by X-21 and D-20 |

## The 63-FR warning

The warning is not a defect in itself. The ids are stable across eight revisions on purpose, and the
checker passes. The real issue is density:
- Slice 5 carries 13 FRs: evidence, trust, resume, fencing, force, restart, stall sweep and lean parks.
- The Epic is built under 1.0.x rules, so slice 5 is one large PR in the riskiest area.

**Recommendation for CONFIRM time.** Split slice 5 into two parts:
- **5a, evidence and resume:** FR-64, 66, 74, 79, 80, 90, 93, plus F1.
- **5b, fencing and recovery:** FR-38, 81, 82, 83, 86, 91, plus F2.

That makes 14 children.

## Owner aims

1. **Armed count.** Met in design, with three caveats: urgent work arms at P2 (D-39), raw filings have
   the Cursor window (D-21, D-40), and the 1.0.x residuals remain. F3 decides where retro filings go.
2. **Slices skip research, plan and plan-review, and resume anywhere.** Met on one machine. Takeover
   is met. Resume across machines is **not met** (F1), and restart is **not met** (F2).
3. **Small goals by judgment, with a PR-time check.** Met (R10, FR-55, FR-69).
4. **Cost, tokens and time.** The prose headroom is measured. The savings and the fence and sweep
   reads are unmeasured and honestly labelled.
5. **Seamless upgrade, core loop intact.**
   - The upgrade is met, apart from the D-29 tension.
   - The core loop holds under takeover.
   - It does not yet hold under restart (F2) or in the closed-but-not-done window (F3).
6. **Host parity.** Met. Every trigger is Python, in `_next`, the tick or `work.py`.

## What I could not verify

- Live GitHub behaviour: re-linking of closing keywords after a PR-body edit, and `--force-with-lease`
  against a remote branch this machine never fetched.
- The rate-limit cost of fence and sweep reads.
- The n=79 baseline and the live backlog counts.
- S-54 (the DAG overlay display).
- Ledger semantics for two claims on one goal after `--force` with the ledger on: I did not trace
  whether the old claim later triggers a FR-91 park.
- No end-to-end run on any host.

# goal-review round 9, delta check: Dossier #2000, revision 9

**Scope:** a delta-only check of `2000-review-9.md` F1-F8, scenarios (b) and (d), FR-94 to FR-97,
D-56 to D-58, the 5a/5b split, and anything revision 9 newly broke. The same reviewer wrote this. I
wrote only this file. Tree: `origin/main` at `504de5c1`.

**Verdict I would record: REJECT.** One finding (N1) remains, and the revision itself introduced it.
The fix is one clause in FR-38 plus one quality-row edit.

## Scripts (re-run)

- `requirements`: exit 0, `ok: true`, `violations: []`, with one warning: `67 functional requirements (> 50)`.
  Slices `5a` and `5b` parse, and FR-94 to FR-97 each sit in one slice row.
- `quality`: exit 0, `ok: true`, `violations: []`. One row is open: `data_retention_privacy` (D-33).
- One seed is unswept (S-54). The banner says the same.

## F1-F8

| Finding | Status | Evidence |
| --- | --- | --- |
| F1 | **Fixed** | FR-94 imports every local input of `pr`, `merge`, `verify` and `done` into the worktree or state directory, never the root checkout. BR-80 (`work.py:4406-4411`, `loop.py:6319-6324`) and D-58 (resolver change) are added. FR-97 adds the fixture matrix with every gate on. Controls :1198-1199 and :1209 |
| F2 | **Fixed** | FR-95 counts only the current generation's slices. FR-83 clears its own record and flag first. Control :1200 |
| F3 | **Fixed** for filings after the close | FR-47 now applies while the issue is open, and FR-67 when the issue is closed or done is recorded. D-50 is consistent. The race residue is N3 |
| F4 | **Fixed** | FR-79 publishes at the first push, newest wins. BR-81 matches `work.py:4460` |
| F5 | **Fixed** | `lean_stalled` qkind plus `sigma:keep-parked`. `KEEP_PARKED_MARKER` and `_is_exempt` exist (`auto_unpark.py:250,301`) |
| F6 | **Fixed** | BR-65 and BR-66 are corrected, and the S rows now have 6 cells |
| F7 | **Fixed** | FR-93 writes with the `sigma:` prefix |
| F8 | **Branch-keeping fixed; the stall rule change breaks liveness (N1)** | D-57, BR-83 |

## N1 (blocking, slice 5b): the new FR-38 exemption hides the most common death

FR-38 (:362-366) and D-57 now say a goal is never stalled while its open slice PR "carries a
published review verdict".

That covers far more than a PR waiting on a human:
- The slice PR is opened only after code review, and FR-92 publishes the verdict on it. In practice,
  every open slice PR carries a verdict.
- The verdict can be a send-back or a block, and the owner itself must then act. Review-verdict
  history shows 38% of verdicts are send-back or block (PC-35).

So an owner that dies during a fix cycle, or after an approval that Sigma itself would merge, is
held under `sdlc:in-progress` indefinitely:
- no stall park happens;
- no resume lane sees the goal;
- only a human running `resume --force` frees it.

This breaks LIVENESS ("recovers without a human") and scenario (b) in its most common form. It also
contradicts the observability quality row (:543), whose test is still "a dead owner with an open,
idle slice PR is stalled".

**Change.**
- Limit the exemption to an approving verdict on a PR whose merge belongs to a human
  (`auto_merge: off`, `config.json.tmpl:126`).
- A send-back, a block, or a PR Sigma merges keeps the 72-hour clock.
- Align the quality row, and add a control: a dead owner with a send-back verdict on an open PR is
  stalled.

## Low (carry onto tickets)

- **N2 (5b).** "Plan generation" is undefined. Define it as a counter that only `--restart` bumps.
  If it were the plan sha, a re-approval after slices merge would discount those slices under FR-95.
- **N3 (6/8).** D-41 says F3 covers "the late merge before the switch to `Refs`". It does not when
  the slice is recorded before the close. The order should be: switch to `Refs`, re-read the issue
  state, then record; if the PR merged in between, defer the filing.
- **N4 (5b).** `sigma:keep-parked` exempts the issue for good (`_is_exempt` checks any comment).
  That is harmless for a lean goal, but it should be stated.

## Scenarios

- **(b) Machine dies mid-slice 3:**
  - before the PR opens: works (FR-94 import, FR-79 publication);
  - an approved PR awaiting a human merge: works, and the clock restarts at the merge;
  - after a send-back verdict, or an approval Sigma would merge: **fails (N1)**.
- **(d) `--restart` after 2 merged slices:** works (FR-83 and FR-95); N2 is a definitional gap.

## Split and dependencies

| Slice | Depends on |
| --- | --- |
| 5a | 4 |
| 5b | 5a |
| 6 | 5b |
| 7 | 6 |
| 8 | 6 |
| 10 | 7, 9 |
| 11 | 8, 10 |
| 12 | 8 |
| 13 | 11, 12 |

The graph has no cycle and the FR coverage is complete. The in-brief now shows 14 parts. The 67-FR
count remains a hint, not a defect.

## What a CONFIRM would produce once N1 is fixed

- **Overlay:** `sdlc:designed` on #2000, via `loop.py mark-designed`.
- **Epic:** one Epic, `originates_from: 2000`, `Feature: lean-goals`.
- **Children:** 14, compiled by `compile_plan.py --forbid-priority` after the phantom-blocker
  control. Each child carries `sdlc:designed` and `feature:lean-goals`, with `blocked_by` edges
  exactly as in the table above.
- **Design PR:** landed at step 4d.
- **Outcome comment:**
  - it names every carried item by id: D-15, 17, 21, 24, 28, 33, 34, 38, 39, 40, 41, 42, 45, 47, 48,
    49, 51, 54, 55, 56, 57, 58, B-3 and N2-N4;
  - it names the falsified PCs with their corrected claims;
  - it notes that `/sigma-define lean-goals` must run before the first child.

## Not verified

- Whether `compile_plan.py` accepts the slice ids `5a` and `5b` (I did not run it).
- Live GitHub behaviour.
- S-54 (the DAG overlay display).

## N1 re-check (after the coordinator's fix)

- **N1 in meaning: fixed.**
  - FR-38 (:362-367) exempts only an open slice PR with an APPROVING verdict that a human merges.
  - D-57 (:965-972) states that send-back, block and Sigma-merged PRs keep the 72-hour clock.
  - The 5b design bullet (:1062) and the observability row (:544) are aligned.
  - Control :1211 breaks the exemption both ways (exempt any verdict; ignore `work.auto_merge`).
  - Nothing else changed meaning.
- **New violation from the edit.**
  - `goal_design.py requirements` now exits with `ok: false`:
    `FR-38: not an EARS sentence (...): 'IF no slice activity of a lean goal holding sdlc:in-progress has occurred for 72'`.
  - The parenthetical inserted before `, THE stall sweep SHALL` breaks the parse.
  - A non-EARS sentence is a REJECT ground (2b).
  - Fix: rewrite the clause without parentheses, for example "... that a human merges because
    work.auto_merge is off or branch protection stops Sigma merging, THE stall sweep SHALL ...". Then
    re-run the check.
- **Low.** FR-38 also exempts `protected` with human-only protection. D-57 and :1062 name only `off`.
  Align them.
- `goal_design.py quality`: `ok: true`, no violations.

**Verdict now: REJECT**, on the FR-38 EARS violation alone. With FR-38 re-phrased and `requirements`
passing, I would CONFIRM, producing exactly what the section above lists.

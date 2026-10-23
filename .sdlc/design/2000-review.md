# goal-review: #2000 — Size-aware PRs, chunked review, in-goal slices with inherited release scope

**Reviewer**: independent (did not author `.sdlc/design/2000.md`) · **Date**: 2026-10-23
**Checked against**: `git archive origin/main` at `504de5c1` (same commit the design swept), plus live
`gh issue list` counts on this repo.
**Mechanism**: subagent dispatched by the owner's session; `reviewer.py resolve` / `review_context.py brief`
were NOT run (feasibility review only, per the dispatch's hard limits).

## Verdict I would record: **REJECT** (narrow revise-and-resubmit)

The code mapping is mostly accurate and the corrected premises are sound. It is rejected on three
grounds that the skill treats as REJECT: (1) the slice table that `compile_plan.py` would consume
contains three slices the design itself marks **blocking** (B-1→9, B-2→10, B-3→7), and its
"confirm the MVP only" intent (D-3) is not expressed anywhere the compiler can read; (2) both MVP
slices 3 and 5 rest on seeds the capped sweep left unswept (S-27, S-17), and S-27 changes slice 3's
scope; (3) slice 3's enforce half can lose findings and has no defined way to decide "outside the
parent's scope" (F-1, F-2). Each needs a few lines of change, not a rewrite.

What a CONFIRM would have produced, and this verdict does **not**: `sdlc:designed` on #2000, an Epic
plus child tickets via `compile_plan.py --forbid-priority`, each stamped `sdlc:designed`, and the
design PR landed. **What REJECT produces**: no label, no ticket. One `loop.py note` comment on #2000
("goal-review: REJECTED -- <findings> -- design PR: no open design PR found -- nothing to close";
the design is local-only per its own Handoff). None of this has been done.

## Mechanical checks
- `goal_design.py requirements .sdlc/design/2000.md` → ok, 0 violations, every FR-1..FR-24 sits in a slice row.
- `goal_design.py quality .sdlc/design/2000.md` → ok, 0 violations, 11 categories answered.
- Schema: `Sweep`, `Seeds`, `Budget`, `Premise check`, `Out of scope` all present. Doubts and Blockers
  carry ids.

## Spot-checks of the highest-consequence claims
| Claim | Result | Evidence (origin/main 504de5c1) |
| --- | --- | --- |
| (a) No PR size cap exists (PC-1) | **holds** | No diff-size limit in any `skills/**/*.py`; only `feature_upkeep_level2.py:57` (conflict size) and the zones threshold. `sigma-plan/SKILL.md:27-41` says split by subsystem, with no number |
| (b) One goal = one branch = one PR, no release between pieces (PC-8) | **holds, with a nuance** | `work.py:107` `branch_prefix "sdlc/"`; `work.py:4320-4332` writes `Closes #N` **only when the PR targets the default branch**, and `Refs #N` otherwise. A `Refs`-until-final-slice path already exists for feature-unit goals. That makes slice 10 cheaper than the design says |
| (c) slices.json fields (PC-3) | **holds** | `slices.py:170-188`: `id,title,needs,files,size,status` |
| (d) `create_tracked_issue` is the one automatic filing chokepoint | **holds for Python paths** | All automatic filings go through it (`handoff.py:589`, callers `loop.py:5612,5786`, `feature_rebase.py:2169,2563`). Other `create_dependency` callers are human pipeline tools (X-7). `upstream.py:756` posts to a *foreign* repo. Agent prose can still run a raw `gh issue create`. `hooks/issue_field_gate.py` only demands a priority label and is Claude-Code-only, so Cursor and Codex can bypass it (F-6) |
| (e) `shell_policy.py` refuses repo shell strings without the git-local opt-in | **holds** | `shell_policy.py:1-36`, fails closed. Note F-5 |
| (f) Fan-out ≤2 chunks (PC-18) | **overstated** | See F-4. The ≤400 *hand-written-line* bound is right. The *chunk count* is not bounded by 2, and per-reviewer load includes tests and generated files. The conclusion stands (fan-out never covers dense logic over the cap), so it does not sink Feature 1: the classifier, the refusal and the regeneration check still deliver its main value |
| (g) 215 / 504 armed follow-ups | **reproduced exactly** | Measured 2026-10-23: 504 open `sdlc:goal`, 237 open `sdlc:followup`, 215 with both (none parked or blocked), 311 `sdlc:followup` ever |
| PC-5 / PC-14 / PC-7 | hold | `red_green.py:26,96`; `work.py:6221-6256` and template `_plan_review` (manifest excluded); `ai_filed_counter.py:13,23` |

## Findings
- **F-1 (high, slice 3)**: refusing a follow-up can lose the finding. FR-17 refuses to create the
  issue and only *offers* an amendment. An unattended worker that does not amend drops the finding
  with no record. That breaks RESILIENCY and SAFETY. Many follow-ups are filed in P6/P7, against an
  already-reviewed PR, where "add a slice" means reopening a reviewed diff. **Change**: a refused
  filing must land somewhere durable (an action-log `scope_grew`/`would` row plus an appended pending
  amendment). Ship slice 3 as shadow plus *defer* only. Refusal waits until that durable path exists.
- **F-2 (high, slice 3)**: the deferral test has no decider. The source records it in the
  plan-review verdict, but most follow-ups are discovered after plan-review. Inheritance (PC-9, BR-23)
  makes every child "in the parent's scope" by construction, so FR-18's "outside the release scope"
  can never be true unless the filer declares a scope. **Change**: name the input, for example a
  `handoff.py track --release <x>` or `--defer "<reason>"` flag, recorded in the row. Also say how
  `blocks_goal=True` dependency filings (`handoff.py:589-595`) are treated. Refusing one of those
  leaves the goal stuck.
- **F-3 (high, slices 3 and 5; resolves D-12)**: two unswept seeds touch MVP slices. S-27
  (`feature_rebase.py:2169-2174,2563-2569`) calls the chokepoint with `upkeep_finding=True`, sometimes
  with a live `goal`. Under enforce, a rebase-conflict finding filed during a goal's pick would be
  refused as "in scope". **Change**: exempt `upkeep_finding` and `goal is None` explicitly in FR-19.
  S-17: `skills/sigma-status/scripts/status.py` and the DAG collectors do not read `slices` at all, so
  slice 5's done/total and scope-grew display is new work. **Change**: re-size slice 5 as large, or
  move the display (FR-21) into its own slice.
- **F-4 (medium, PC-18 / D-4)**: the cohesion rules force extra chunks: a migration chunk, a contract
  chunk, tests travelling with their code. A 400-line plan can be 3-4 chunks. **Change**: reword PC-18
  to "≤400 dense lines", not "≤2 chunks". Keep B-1.
- **F-5 (medium, slice 7)**: `generators` would live in `.sdlc/config.json`, which is committed, so a
  PR can edit its own generator command. **Change**: FR-7 must read generators from the base branch
  or main checkout, never from the PR head.
- **F-6 (low, slice 3)**: the chokepoint is prose-dependent on hosts with no hooks. State this in the
  design's own words.
- **F-7 (medium, slices 4 and 5)**: there is no default enforcement point. The refusal and the freeze
  bind to `gates.plan_review`, which is **off by default** (template `"plan_review": {"enabled":
  false}`) and checked only at `work.py pr` (`work.py:6221-6248`), which is after implementation, not
  at plan-review. **Change**: either make `size_policy` enforce require `gates.plan_review` (FR-3
  style, with a named refusal) or add the check to `record_plan_review` (`work.py:1532`). Correct
  D-1's "refusal AT plan-review" accordingly.
- **F-8 (medium, coverage)**: source behaviours are dropped silently, with no X-n: the issue-body
  checklist kept in sync, the per-chunk commit trailer, post-PR fan-out on the real diff, the
  release notes, and the "sigma-init offers keys" step. D-16 exempts a checklist that no FR writes.
  **Change**: add FRs or X-n entries for each.
- **F-9 (medium, D-3)**: the plan is not compilable as intended. "Two Epics" does not match a table
  where MVP slices 2 and 4 (Feature 1) and slice 3 (Feature 2) share one Epic. **Change**: either
  limit the slice table to 1-5 and move 6-10 (with FR-7..14, 20, 22, 23) to X-n pending B-1..B-3, or
  state one Epic with 7, 9 and 10 left uncompiled.
- **F-10 (low)**: tiers. Slice 3 Opus is right. Slice 5 should be Opus or large given F-3. Slice 10
  is cheaper than estimated given (b)'s nuance. Line refs drift by about 1 (template `:253` is
  `:252`); this is harmless.

## Adjudication (by id)
- **Resolved**: D-2, D-5, D-6, D-7, D-8, D-9, D-12 (by F-3: exempt upkeep), D-14, X-1..X-9.
- **Open, not blocking**: D-1 (with the F-7 correction), D-3 (F-9), D-4, D-10, D-11 (F-3), D-13,
  D-15, D-16 (F-8), D-17, D-18, D-19, PC-17.
- **Blocking**: B-1 stops slice 9 (waits on the D-4 ruling). B-2 stops slice 10 (one goal owning
  several PRs). B-3 stops slice 7 (the generator-command source). All three are in the table as
  written, which is ground (1) above.
- **Falsified premises to carry onto #2000's body**: PC-1, PC-3 (shape), PC-7 (in part), PC-8, PC-12,
  PC-13, PC-14 (manifest), PC-15, PC-18 (reworded per F-4).

## MVP cut
It stands **in substance**: rollout layer first, shadow everywhere, slices 1, 2, 3, 4 and 5. Three
conditions: slice 3 ships shadow plus defer only (F-1, F-2); slice 5 is re-sized (F-3); and slice 4
names its enforcement point (F-7). Two Epics is not supported by the evidence. Use **one** Epic for
1-5, with Feature 1's later slices re-designed after shadow data.

## Not verified
Pick-order, phase and label parity on a real adopter config. Whether `record_plan_review` is reached
on every host. Fan-out cost. The 60-PR size sample (I did not re-run it). The phase-budget headroom
figures. `predict.py` behaviour (S-28). No `reviewer.py`/`review_context.py` brief run.

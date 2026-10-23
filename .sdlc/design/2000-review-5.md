# goal-review round 5: Dossier #2000, revision 4 ("lean goals")

**Reviewer:** fresh, independent subagent (Stage 2). Mechanism: `subagent`. I formed this view
before reading `2000-review*.md`. I wrote only this file: no labels, comments, Epic or
`compile_plan.py`.
**Tree verified:** `origin/main` at `504de5c1` (plugin 1.0.20), exported read-only. Every `file:line`
below is against that tree.
**Verdict I would record: REJECT.** It would produce exactly two things:
- `work.py close-design .sdlc 2000 --comment "goal-review: REJECTED"`;
- `loop.py note .sdlc 2000 "goal-review: REJECTED -- H1..H7 below -- design PR: <result>"`.

There would be no `sdlc:designed`, no Epic, no children and no board move.

The owner rulings R1-R9 are not re-litigated here. Every finding is about whether the design
implements them.

## Schema and script checks (step 1)

- **Requirements.** `goal_design.py requirements` returned `ok: true` with 0 violations and 0 warnings.
  FR-31 to FR-62 are each in exactly one slice row.
- **Quality.** `goal_design.py quality` returned `ok: true` (lane large) with 0 violations. One row is
  open: `data_retention_privacy` (D-33).
- **Sweep budget.** `goal_design.py sweep-budget .sdlc --mode lane --lane large` returned
  `rounds: 3`. That matches `**Budget**: 3`.
- **Sweep.** The header says `capped`. 4 seeds are unswept (S-41 to S-44), which matches the banner's
  count. The record agrees with itself, so there is no schema violation. Two of the unswept seeds
  (S-42, S-44) sit under the riskiest slice (see H3).
- **Phase-prose headroom.** I re-measured it with `evals/phase_context_budget.py measure_all` and it
  matches PC-34 exactly, in tokens:

  | Phase | Headroom (tokens) |
  | --- | --- |
  | orchestrator | 22 |
  | goal-slot | 21 |
  | research | 13 |
  | plan | 21 |
  | plan-review | 8 |
  | implement | 0 |
  | review-pre-pr | 14 |
  | review-post-pr | 3 |
  | retro | 48 |

## Findings, ranked

### H1 (blocking): a non-final slice merge closes the issue

**Evidence.**
- `work.py:7795-7800`: `merge()` calls `_close_issue_the_base_cannot` after every landing. Its
  docstring at `work.py:7106-7140` says it "reads the issue's own state on EVERY base ... otherwise
  (open, or unreadable) it sends a close request".
- So writing `Refs #N` (R3's named mechanism, PC-8) does not keep the issue open. Sigma's own merge
  closes it after slice 1.
- BR-17 does not list this site. FR-36 says "close the issue only when the final slice merges" but
  gives no mechanism.

**Change.**
- Add `work.py:7106-7140,7800` to BR-17.
- Gate the close on "the manifest's final slice merged".
- Control: remove that gate, and a fixture where slice 1 of 3 merges must close the issue (red).

### H2 (blocking): work found after the PR opens is orphaned

**Evidence.**
- A single-slice goal's PR is opened with `Closes #N` (`work.py:4327`).
- The biggest source of follow-ups is review: `skills/sigma-review/SKILL.md:163-172` says "File it
  with `--label sdlc:followup` instead of blocking".
- Under FR-47 a review finding becomes a discovered slice of a goal whose open PR already closes the
  issue on merge. The reconcile pass then records `done`, and the slice is lost.
- FR-47 has no condition on the goal's state: at plan, at PR, awaiting merge, or merged.

**Change.**
- State that a slice added after `pr` either rewrites the open PR's header to `Refs`, or that lean
  goals always write `Refs` and close only at final `done`.
- State that filings from a goal that is already merged are deferred, never added as slices.
- Add an FR and a control for both.

### H3 (blocking): a goal between slices cannot be picked up again, and its liveness is single-machine

**Evidence: no picker will take it.**
- `_fetch_pending` excludes any issue carrying `sdlc:in-progress` (`sources.py:3238-3250`).
- `_next` skips goals awaiting merge locally (`loop.py:4744-4754`).
- FR-37 keeps the claim. So the "re-armed" goal can be picked by nobody, its owner included.
- The design only says "re-arms the next slice" (`2000.md:516`). The seed that would have mapped this,
  S-42, is unswept.

**Evidence: only the owning machine can see it.**
- The between-slices flag lives in gitignored local state (`work.py:1485-1490,1772`). Status reads it
  locally (`status.py:402-414`), and so does the reconcile pass.
- The FR-38 timeout and the age line therefore exist only on the machine that holds the goal.
- If that machine dies, the issue stays `sdlc:in-progress` forever. GitHub has no fallback because
  `Refs` never closes anything. That is exactly the dead-versus-idle blindness that AGENTS.md's
  LIVENESS rule forbids.

**Change.**
- Design the resume path explicitly, for example a local resume set that `_next` serves before
  `next_pending`.
- Design a signal that other machines can read: a ledger or issue-timeline age that doctor and status
  read on any machine.
- Controls:
  - drop the resume set, and a resumed slice-2 fixture must never be picked (red);
  - delete the owning machine's state, and another machine's doctor must still flag the age.

### H4 (blocking): the filing route breaks Sigma's own pipeline filers

**Evidence.**
- Three pipeline filers go through `create_tracked_issue` with a filing goal set, `same_area=True`,
  `immediately_actionable=True` and `blocks_goal=False`:
  - the decompose meta-issue (`loop.py:5612-5618`);
  - the design-check meta-issue (`loop.py:5786-5791`);
  - decompose children (`decompose_goal.py:75`).
- None of them is in FR-50's exemption list, so FR-47 converts each into a slice.
- The callers then get no `issue` back and park with "failed to file decomposition goal"
  (`loop.py:5621-5624`).
- BR-24 asserts "unchanged" but names no discriminator.

**Change.**
- Add an explicit pipeline-caller exemption (a keyword argument, not a text match) to FR-50.
- Control: remove the exemption, and `decompose_check` / `design_check` must fail to return an issue
  (red).

### H5 (high): exception (c) can never fire, which defeats aim 1's "urgent unrelated defects get their own goal"

**Evidence.**
- With triage on (the default), a `track` filing arms at its bucket priority. The default buckets are
  P2, P3 and P4 (`file_triage.py:34`).
- Every bucket must rank below the human P1 (`file_triage.py:15`).
- `--priority` "may only LOWER urgency" (`handoff.py:492,502-504`). The design's own threat row T
  says the same.
- So FR-49's "P0 defect" is unreachable from an agent filing. In practice an urgent unrelated defect
  becomes a slice of an unrelated goal (P2 counts as in-release under FR-51).

**Change.**
- Define how (c) is declared and checked. For example: the urgent bucket plus a declared
  `--unrelated` flag plus Python-checked path disjointness, with the paths taken from the diff and
  plan, never only from filer text.
- Control: a P2 filing with overlapping paths must become a slice; flip the disjointness check and
  the test goes red.

### H6 (high): small and trivial goals have no route marker, and `gates.plan_review` would block their PR

**Evidence.**
- FR-33 and FR-34 and the cutover text ("the route marker is the only discriminator", `2000.md:523`)
  put `route: lean/1` only on the plan-review record.
- FR-54 folds plan-review away for small and trivial goals, so they never write a plan-review record.
- `_hard_plan_gate_refusal` (`work.py:6127-6160`) refuses `pr`'s push when a plan exists and no
  approving plan-review record exists. That site is absent from the blast radius.

**Change.**
- Record the route at classification (the `goal_class` row) or on the pre-implementation test-review
  record.
- Add `work.py:6127` to BR-39.
- Control: a small-goal fixture's `pr` with the fold removed must be refused (red).

### H7 (high): activation is not ordered, and main is unreleasable for 10 slices

**Evidence.**
- R1 removes the flag.
- Slice 2 makes every new goal lean/1, and "every later gate SHALL apply the lean rules".
- Those rules land later: the slice lifecycle in slice 4, per-slice records in slice 6, waivers in
  slice 9, and the safety net in slice 10.
- No `Feature:` unit is proposed. Any release cut from main between slices 2 and 10 ships a
  half-built route to the fleet. This breaks "each slice leaves the loop working" and aim 5.

**Change.** Pick one:
- land the Epic on a `feature/lean-goals` unit and merge it once as 2.0.0; or
- move the route-marker write (activation) to the last slice, so slices 2-10 stay dark on main.

### M1. FR-53 and D-20 disagree on what the classifier can see

**Evidence.**
- FR-53 classifies at pick from the issue text.
- D-20's small and trivial rules read the plan's `## Files` and `## Tests` and the diff, none of which
  exists at pick.
- The quality row's "0 git processes" claim contradicts D-20's "diff is docs-only".

**Change.** Split the classifier into a provisional class at pick and a confirmed class after the
plan.

### M2. The safety net fails open and can be gamed

**Evidence.**
- `risk-detect.sh` is fail-open (`risk-detect.sh:16-17`): a failed scan reads as "not sensitive".
- Path-based classes let handwritten logic placed under a `tests/` or generated-looking path escape
  the line count.

**Change.**
- The FR-55 gate must fail closed: a scan error means not small.
- Add two clauses to the per-clause control: "scan error" and "handwritten code in a non-handwritten
  class".

### M3. The per-slice gate has no enforcement point

**Evidence.**
- FR-42's trigger, "WHEN a slice ... starts", requires records that only exist later.
- `phase_gate` runs only at `done` (`phase_gate.py:1-9`), so non-final slices merge without it.
- The threat row E claim, "a slice cannot merge without its own recorded reviews", is therefore
  unsupported.

**Change.** Name the enforcement point as `work.py pr` or `merge` for each slice.

### M4. Resuming a parked multi-slice goal is unspecified

Three things can park a goal mid-slices: the `max_review_cycles` park (`work.py:109`), the FR-38
timeout and the FR-44 cap. Nothing says where an unparked goal resumes (which slice), or what
happens to slice PRs that are already merged.

### M5. Root checkout writes

FR-41 fixes where manifests are read from, not where they are written. Under a multi-PR goal,
`status` edits in the main checkout (`running.md:352-362`, `slices.py:274-279`) repeat the #1161
hazard once per slice. AGENTS.md requires naming the one command that clears it.

### M6. Some cost claims are labelled measured but are not

- The "Measured" column says skipping research and retro saves "0.69 + 0.19 USD per small goal" and
  folding saves 0.54. Those are all-goal medians, not small-goal measurements.
- The pre-implementation review cost is unmeasured.
- The baseline is one machine's log (n=79).
- The revert trigger (+20 percent on the median over 14 days) has no noise estimate.

**Change.** Move these into the unmeasured column, or measure them on small goals.

### M7. The prose budget plan does not pay for itself where it is needed

- Deleting `running.md` frees only orchestrator and goal-slot headroom. That file is counted only
  there (`measure_all` file lists).
- implement has 0 tokens, review-post-pr 3 and plan-review 8, and the deletions in those skills are
  unidentified (S-41).
- The new pre-implementation test-review brief is in no phase budget (the rendered briefs measured
  are only plan-review, code-review, pr-review and retro), so it would be unbudgeted prose.

**Change.** Add a `test-review-pre` phase to `phase_context_budget.json` in slice 6.

### M8. The armed-count objective has holes the baseline will not see

- 1.0.x machines on the same repo keep arming follow-ups.
- `--blocks yes` and `handoff.py open` are self-declared exemptions.
- D-21's raw `gh` bypass applies on Cursor and Codex.

**Change.** FR-58 and FR-59 should count exempt and old-version filings, so that the bypass rate is
measured.

### Low

- **L1.** `merge_queue.py` burst signals will fire more often with several PRs per goal (S-44).
  It is an advisor only.
- **L2.** With auto-merge in-session, nothing says the goal moves straight on to slice 2 rather than
  entering between-slices.
- **L3.** D-33: action-log growth multiplies by slice count, with no retention rule.

### Verified sound

- `load_config` tolerates unknown keys (`state.py:63-80`).
- The ledger does not enforce phase vocabulary on append (`ledger.py:526,1421`), so a 1.0.x reader of
  a `test_review` row is safe.
- PC-30's awaiting-merge description is accurate.
- The `ai_filed.max_per_goal` default of 5 is accurate (`ai_filed_counter.py:23`).

## The owner's five aims

1. **Armed count never rises from agent filings: not met as designed.** H2, H4, H5 and M8.
2. **Slices skip research, plan and plan-review and run tests-first: met on paper.** The enforcement
   point is missing (M3).
3. **Small goals fast-tracked, with a PR-time check: partly met.** H6, M1 and M2.
4. **Lower cost, faster, no quality loss: unmeasured.** It is honestly framed as a hypothesis, apart
   from M6.
5. **Seamless upgrade, core loop unbroken: config loading and old rows are fine.** The loop breaks at
   H1, H3, H4 and H7.

## Run-the-control, per new guard

| Guard | Break it | Must go red |
| --- | --- | --- |
| H1 final-slice close gate | drop the gate | slice-1 merge closes the issue |
| H2 post-PR slice header | keep `Closes` | the merge orphans the slice |
| H3 resume | drop the resume set | slice 2 is never picked |
| H4 pipeline exemption | drop the exemption | decompose returns no issue |
| H5 disjointness | one check at a time: flip it; declare disjoint paths in text only | the filing arms |
| H6 route record | drop it | the gate refuses the small goal's `pr` |
| FR-40 scope fingerprint | include `status` | a wave edit invalidates the plan |
| FR-45 assertion-only red | accept ImportError as red | a test that fails only on an import error is still counted red |
| FR-55, each clause separately | weaken files, lines, sensitive, migration, contract, scan-error and class-gaming one at a time | each break turns a test red |
| FR-60 dry-run | let it write | it writes without approval |

## What I could not verify

- The source prompts' adaptive-phases profiles. The design itself says that prompt is not in hand.
- The live GitHub numbers: 215 armed, and 107 created versus 22 closed. I did not re-query them.
- The per-goal cost baseline (5.98 USD, n=79). The log was not re-aggregated.
- `sources.next_pending`'s full order, and `timing_store` wall-time (S-42, S-43).
- Whether any release is cut from main mid-epic.
- No end-to-end loop run.

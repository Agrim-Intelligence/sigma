# goal-review (round 2): #2000, revision 2 of the design

**Reviewer**: independent. I did not write `.sdlc/design/2000.md` or the first review. **Date**: 2026-10-23
**Checked against**: a `git archive origin/main` export at `504de5c1`, the commit the design swept.
**Mechanism**: a subagent dispatched by the owner's session. `reviewer.py resolve` and `review_context.py brief`
were NOT run: this is a feasibility review only, per the dispatch's hard limits.

## Verdict I would record: **REJECT** (narrow, second round)

Revision 2 genuinely fixes F-1 to F-6 and F-8 to F-10, and the one-Epic, shadow-first cut is right.
It is rejected on two new mapping holes. Each makes the enforce half of an MVP slice unimplementable
as written:

- **N-1**: nothing measures hand-written lines at plan time (slices 2 and 4).
- **N-2**: nothing writes or proves acceptance tests before plan-review (slice 6).

Both are few-line fixes, not a redesign.

**What this verdict would produce.** No label and no ticket. It would produce one
`loop.py note .sdlc 2000 "goal-review: REJECTED -- <N-1..N-4> -- design PR: no open design PR found
-- nothing to close"` comment (the design is local-only per its own Handoff).

**What CONFIRM would have produced.** `sdlc:designed` on #2000. One Epic plus six children via
`compile_plan.py --forbid-priority`, each stamped `sdlc:designed`, with `blocked_by` edges per the
corrected table below. The comment would read
`goal-review: CONFIRMED (<k> open questions carried) [sweep: CAPPED]`.

None of this has been done.

## Mechanical checks
- `goal_design.py requirements` (both installed 1.0.19 and origin/main): ok, 0 violations, with
  FR-1..6, 15..19, 21, 23..28 each in a slice row.
- `goal_design.py quality`: ok, 0 violations, 11 categories.
- Schema: `Sweep` (capped), `Seeds`, `Budget`, `Premise check` and `Out of scope` are all present,
  and every D/B/X/PC item carries an id.

## The new TDD premises (PC-20..PC-27), re-verified
| PC | Result | Evidence (origin/main) |
| --- | --- | --- |
| PC-20 | holds | `review_context.py:782-787` adds test-trust facts for `code-review` and `pr-review` only. The gate seam is `work.py:984-987` (`review_tests.judge`) |
| PC-21 | holds | `config.json.tmpl:268` sets `"test_review": false`. `review_tests.py:189-200` reads `review.get("test_review") is True`, so a dict reads as off. D-26's rollback argument is correct |
| PC-22 | holds | The format is at `review_tests.py:1-37`, the parser at `:111-118`, AST existence at `:85-108`, and criteria from the recorded acceptance at `:47-57` |
| PC-23 | holds, and **stronger than stated** | `red_green.py:277-290`: sealed-pytest-v1 counts any FAILED node, ImportError in the body included. Missed: `seal_tests.py:7,406-410` refuses unless an **approving plan-review already exists**. Sealed tests can therefore never be what a plan-review TEST-MAP points at, which settles D-22/S-37 negatively (feeds N-2) |
| PC-24 | holds | `red_green.py:136-230`: planned-pytest-v1 records only attributed assertion reds, and `collect` raises on collection failure (`:110-118`). The witness is written by `observe`, called from `loop.py red` (`loop.py:6254`) and from verify (`:6415`) |
| PC-25 | holds | The plan-review brief carries no acceptance or test-trust block. Plan-review prose is at `sigma-plan-review/SKILL.md:75` |
| PC-26, PC-27 | unverifiable, correctly marked | I found no defect-to-test link. The profiles source is not in hand |

## Findings
- **N-1 (high, slices 2 and 4; D-1 and F-7).** A plan has no line counts, so the plan-time size
  verdict has no input.
  - The design says `size_policy.py classify --diff|--plan` "reuses `zones._diff_counts`". FR-4 says a
    measured plan records "the five line counts", and FR-6 refuses "when a plan-review verdict is
    recorded" because the hand-written lines exceed the cap.
  - In the code, `zones.check --plan` reads only the plan's `## Files` paths and emits `-` for counts
    (`zones.py:476,485-494`). `_diff_counts` exists only on the `--diff` path (`:496`). At plan-review no
    diff exists. `record_plan_review` is called before implementation, and its docstring says not to
    commit during plan-review (`work.py:1574`).
  - D-1's resolution therefore does not hold: "refusal at record time is a free re-plan, only diff
    growth needs an amendment". The only measurable point is `work.py pr`, after implementation.
  - **Change**: pick one.
    - (a) Define a Python-parsed plan-declared estimate, e.g. per-file estimated lines in `## Files`,
      and say the record-time verdict is an estimate.
    - (b) Make slice 4 enforce at `work.py pr` only, and re-state D-1, since a refusal there means
      splitting implemented work.
  - Either way FR-4/FR-6 must name the plan-time input.
- **N-2 (high, slice 6).** In enforce, FR-26 would refuse every ordinary plan, and the addendum's
  core step is dropped silently.
  - **Nothing produces the tests.** The addendum's layer 1 is "in the Plan: write the acceptance
    tests ... Record the red proof once". No FR, slice or X-n carries that step. D-25 states the
    precondition ("must be run once by the existing verify before plan-review") but nothing makes it
    happen. Today the Plan writes `## Tests` selectors for tests that Implement writes
    (`sigma-plan/SKILL.md:42,52`).
  - **Enforce refuses everything.** At plan-review `_exists` fails, and no planned-pytest red row
    exists (`observe` needs collectable nodes, `red_green.py:117-118`). FR-26 refuses every approval
    in enforce, and shadow logs 0 red-proven for every goal. That is uninformative data, which defeats
    the shadow iteration.
  - **Sealed tests are not a way out**, by N-1's evidence from `seal_tests.py:406-410`.
  - **The "reused verbatim" check is not.** `review_tests.check` (BR-32) also judges diff-revert
    survivors from verify evidence (`review_tests.py:162-178`). At plan time that yields `partial`
    always, or a stale-evidence survivor block, so the map half must be split out.
  - **Change**: pick one.
    - (a) Add an FR and a slice (or extend 6) in which the Plan phase writes acceptance tests and
      `loop.py red` runs before plan-review. This must be Python-emitted or fit the plan phase's
      30-word headroom (D-10, BR-29).
    - (b) Ship slice 6 shadow-only, and record "acceptance tests authored in Plan, and enforce of
      FR-26" as a new X-n gated on it.
  - Either way, split `check` into map-only and survivor halves.
- **N-3 (medium, slice 3).** FR-2 conflicts with FR-18, so "shadow plus defer" has no mode.
  - FR-2: shadow writes no label, issue or body "beyond what today's behaviour writes".
  - A deferred filing (FR-18) writes a different priority, withholds the goal label and adds a
    `sigma:deferred-from=` body marker. All three are writes beyond today's.
  - If defer acts in shadow, slice 1's shadow-writes-nothing control goes red. If it acts only in
    enforce, the Epic has no mode in which defer runs without refusal.
  - FR-18 also has no "not off" clause, against FR-1.
  - **Change**: state that `release: enforce` means defer-only in this Epic (refusal is X-17), or that
    an explicit `--defer`/`--release` is honoured in every mode as a filer's choice. Exempt it from
    FR-2's control by name.
  - Not a bypass: a declared deferral can only unarm a filing, never arm one (STRIDE row E holds).
- **N-4 (medium, ordering).** Slices 4, 5 and 6 all edit `record_plan_review` (`work.py:1532`),
  `_plan_review_refusal` (`work.py:6221`) and the plan-review brief (28 words of shared headroom).
  - The table lets 4, 5 and 6 run in parallel, so they will conflict.
  - Slice 5's status segments count slice 3's `would`/`deferred` rows.
  - **Change**: add `blocked_by` edges 4→5→6 (or name one slice as owner of the record-time seam),
    plus 3→5. Also say which slice adds each action-log kind to `actionlog.py`'s closed whitelist.
- **N-5 (low).** "`record_plan_review` deliberately never refuses today" is inaccurate. It raises on
  a sha mismatch and on the F02 independent-run refusal (`work.py:1577,1587`). The precedent helps
  the design, but correct the sentence (D-1, BR-11).
- **N-6 (low, FR-6).** "Without a verified mechanical excess" cannot be satisfied in this Epic,
  because verification is the deferred regeneration (X-12). Say that every over-cap excess is
  unverified until X-12.

## Earlier findings
| ID | Status | Evidence |
| --- | --- | --- |
| F-1 | resolved | Shadow plus defer, no refusal (FR-17, X-17). N-3 is a residual consistency issue |
| F-2 | resolved | `--defer`/`--release` on `track`. `blocks_goal=True` is exempt, which fits `handoff.py:589-595`. `--needs-person` and `target_unit` exist for the owner-decision and other-unit exemptions |
| F-3 | resolved | FR-19 exempts `upkeep_finding` and `goal=None`. `feature_rebase.py:2169,2563` are the only other chokepoint callers. Slice 5 is large/Opus, and I confirmed `status.py` and `dag_*.py` read no `slices` |
| F-4 | resolved | PC-18 reworded |
| F-5 | resolved | X-12 and PC-13: generators come from the base branch |
| F-6 | resolved | D-21 |
| F-7 | **partly, reworded for size** | The double opt-in is stated and is sound for the manifest binding and the test map. The plan-time size half is hollow (N-1) |
| F-8 | resolved | X-10, X-11, X-14, X-18, X-19 |
| F-9 | resolved | One Epic. The table holds only slices 1-6, and none of them waits on B-1..B-3 |
| F-10 | resolved | Tiers |

## Asked-for checks
- **Slice 6 cheap and safe?** Safe, yes. It adds no reviewer call, its own check spawns nothing, and
  `test_map` is a separate key, so the boolean `test_review` meaning and rollback survive. Cheap only
  as a check: its value depends on an unscoped producer (N-2).
- **Slice 3 a bypass?** No. Exemptions can only keep today's behaviour, and a declaration can only
  unarm a filing. The residual limit is that agents learn about `--defer` only through prose, and on
  hookless hosts the chokepoint is prose-dependent (D-21).
- **Enforcement point.** Requiring `gates.plan_review` for enforce is correct: the gate is off by
  default (`config.json.tmpl:90`) and `pr` checks only under it (`work.py:6245`). It holds for the
  manifest and the map. For size it fails per N-1.
- **Unswept seeds.**
  - S-37 (`seal_tests.py`) changes the answer to D-22 (no: sealed tests post-date plan-review) and
    strengthens N-2. It changes no slice by itself.
  - S-38 (`hooks/issue_field_gate.py:1-60`) is Claude-Code-only, fail-open and checks priority only.
    At most an accelerator for D-21, so no slice or verdict changes.
  - A CAPPED sweep is acceptable to CONFIRM once N-1/N-2 are fixed, because this review covers both
    remaining seeds.

## Adjudication (by id)
- **Resolved**: D-3, D-5, D-6, D-7, D-8, D-9, D-11, D-12, D-13, D-14, D-16, D-22 (negatively, see
  PC-23), D-23 (no change), D-26; X-1..X-22.
- **Open, not blocking**: D-2, D-4, D-10, D-15, D-17, D-18, D-19, D-20, D-21, D-24, PC-17, PC-26,
  PC-27.
- **Blocking**:
  - D-1, as resolved (N-1), stops slice 4: it waits on the plan-time size input.
  - D-25, which states but does not scope the producer (N-2), stops slice 6: it waits on a choice
    between Plan-authored tests and shadow-only.
- **Blocking deferred work only, outside the table**: B-1, B-2, B-3.
- **Falsified premises to carry onto #2000's body**: PC-1, PC-3 (shape), PC-7 (in part), PC-8,
  PC-12, PC-13, PC-14 (manifest), PC-15, PC-18, PC-23 (in part).

## MVP cut
It stands: one Epic, six slices, shadow-first, with the follow-on deferred. Slices 1, 2, 3 and 5 are
correctly sized and tiered. The corrected table:

| Slice | Change needed | Depends on |
| --- | --- | --- |
| 4 | Fix N-1 | 2, then 5 or 6 serialised per N-4 |
| 5 | Ordering only | 1 and 3 |
| 6 | Fix N-2. If option (a), it grows to large/Opus, since it touches Plan-phase prose or emission | Serialised per N-4 |

## Not verified
- Pick, phase and label parity on a real adopter config.
- Whether `loop.py red` can run in the Plan phase under the phase gate on every host.
- Per-seal time.
- The 60-PR size sample and the phase-budget headroom figures, which I did not re-run.
- No `reviewer.py`/`review_context.py` brief run.

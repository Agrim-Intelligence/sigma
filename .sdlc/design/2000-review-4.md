# goal-review (round 4): #2000, revision 3 of the design

**Reviewer**: independent, fresh context. I wrote neither `.sdlc/design/2000.md` nor any earlier review. I formed
the findings below before opening `2000-review.md`, `-2.md` or `-3.md`. **Date**: 2026-10-23.
**Checked against**: `git archive origin/main` at `504de5c1`. `git ls-remote origin refs/heads/main` returns the same
sha, so this is the design's swept tree and still current.
**Mechanism**: a subagent dispatched by the owner's session. `reviewer.py resolve`, `review_context.py brief` and
`reviewer.py run` were NOT run (review-only dispatch). No label, comment, ticket or source edit was made.

## Verdict I would record: **REJECT** (narrow, one blocking item)

The mapping is mostly accurate and the shadow-first, one-Epic cut holds. It is rejected on one mapping error that
would ship a wrong requirement onto a ticket: **FR-15/FR-16 bind plan-review approval to the slice manifest's bytes,
but the manifest is also the goal's progress record** (G-1). Slice 5 cannot be correctly scoped until FR-15/FR-16
say what is frozen. `goal_design.py served` pastes FR text into each child verbatim, so a CONFIRM would put the defect
on slice 5's ticket. Round 3's carried R-1..R-7 and G-2..G-10 below are each a sentence or two to fold in. A
revision 4 that fixes G-1 and folds those should CONFIRM without another full round.

**What REJECT produces (not done):** `work.py close-design .sdlc 2000 --comment "goal-review: REJECTED"`. The design is
local-only, so the expected line is `no open design PR found -- nothing to close`. Then one
`loop.py note .sdlc 2000 "goal-review: REJECTED -- G-1 slice 5 freezes the manifest status field; fold R-1..R-7,
G-2..G-10 -- design PR: <close-design's line>"`. No `sdlc:designed`, no Epic, no children.

**What CONFIRM would have produced:** `sdlc:designed` on 2000. One Epic plus six children via
`compile_plan.py --forbid-priority`, with edges 2←1, 3←1, 4←2, 5←{3,4}, 6←5, all stamped. The comment would lead
`goal-review: CONFIRMED (<k> open questions carried) [sweep: CAPPED]`, then the feature-ification ask.

## Mechanical checks (re-run)
- `goal_design.py requirements .sdlc/design/2000.md`: exit 0, 0 violations. FR-1..6, 15..19, 21, 23..30 are all in slice rows.
- `goal_design.py quality`: exit 0, 0 violations.
- `goal_design.py sweep-budget .sdlc --mode lane --lane large`: `rounds: 3`, which equals `**Budget**: 3`.
- Seeds: 38 rows, 0 unswept. However, 5 rounds ran against a budget of 3, and `## Queries run` records no round-5
  query. The marker is therefore **CAPPED**, not `converged` (agree with R-4).
- Backlog counts, re-measured live through search: 504 open `sdlc:goal`, 237 open `sdlc:followup`, 215 with both.
  The armed follow-ups split P0 2, P1 24, P2 90, P3 71, P4 28. In the last 14 days, **107 follow-ups were created and
  22 closed**.
- Phase headroom, re-measured with `evals/phase_context_budget.py measure_all` on the export, as words / est_tokens:
  orchestrator 191/22, goal-slot 108/21, research 18/13, plan 30/21, plan-review 28/8, **implement 11/0**,
  review-pre-pr 91/14, **review-post-pr 89/3**, retro 23/48.

## Findings, by severity

**G-1 (HIGH, blocking slice 5). The freeze hashes a mutable progress field.**
- What the code does:
  - `slices._normalise` carries `status` (`slices.py:170-188`), and `frontier()` runs only slices whose `needs` are
    `done` (`slices.py:274-279`).
  - The orchestrator lands a wave and "re-run[s] `plan` for the next" (`running.md:358`). `docs/parallelism.md:28`
    documents `status` (`pending` / `done`).
  - So a multi-wave goal must edit the manifest after plan-review.
- Why the design breaks:
  - FR-15 binds the approval to "the manifest bytes", and FR-16 treats any change as scope growth.
  - FR-21's done/total is computed from that same field.
  - In enforce, `_plan_review_refusal` would refuse every multi-wave goal at `pr`. In shadow, it would log false
    `scope_grew` rows.
- **Change:** hash a canonical projection (`id, title, needs, files, size`, sorted), never `status`. Say this in the
  FR-15 and FR-16 text.

**G-2 (HIGH, value; owner call; slice 5).** Slice 5, the one large Opus slice, freezes a manifest nothing writes:
- 0 `.slices.json` files among 394 local plans.
- `slices.py:637` states "No plan has ever declared slices in this repo".
- With multi-PR goals deferred (X-15), the freeze gives no stable denominator, and its shadow `scope_grew` count will
  read 0.
- **Recommend:** shrink slice 5 to the status segments for slice 3's `would`/`deferred` counts, plus done/total
  where a manifest exists. Move the freeze and amend work (FR-15/FR-16) to the follow-on with X-15. This also removes G-1.

**G-3 (HIGH, motivation; slice 3).** Deferral relabels follow-ups; it does not reduce growth.
- Enforce defers only filings the agent flags. Nothing prompts the flag: the only prose is `filing.md`, which has 22
  tokens of orchestrator headroom.
- Refusal is X-17, and the source's deferral test (a/b/c) is gone.
- A deferred issue's priority is fixed at the bucket floor, and nothing drains it (D-17). Today's armed P3/P4
  follow-ups (99 of 215) do eventually get picked. Deferred ones never do: a non-draining pile with no age signal,
  which breaches SCALABILITY's "unbounded growth".
- "In scope" is true by inheritance. On a repo with no `release.source` set, FR-17's would-refuse count therefore
  equals every unflagged, non-exempt filing, which makes it tautological.
- The design never weighs the existing levers: `ai_filed.max_per_goal`/`on_cap` (template :177-181) and triage
  buckets.
- **Change:**
  - Intent (b) must say this Epic measures and offers an opt-in lever; it does not stop growth.
  - Define in-scope when the parent has no resolvable release.
  - Status and doctor show the deferred count and the oldest age.
  - Name the activation query.
  - Compare against tuning `ai_filed`.

**G-4 (MEDIUM, shadow side effects; slices 4 and 6; extends R-1).**
- Slice 4 renders the size verdict into the code-review and PR-review briefs with no mode clause (`2000.md:576-578`).
- FR-25 ("not off") renders the TEST-MAP instruction and the acceptance record into the plan-review brief in shadow.
- In both cases shadow changes reviewer input. The addendum asks for the map in shadow, while the rollout spec says
  shadow "changes nothing", and the design never names that conflict.
- The fixture config is off, so `test_phase_context_budget.py` cannot see either brief's growth.
- **Change:**
  - Slice 4 renders only in enforce.
  - Slice 6's shadow brief change is named as a deliberate exception to FR-2, with its token cost stated.

**G-5 (MEDIUM, liveness; slices 1-6; extends R-2).** Shadow's only sink can be off.
- `actionlog.append` is a no-op unless `action_log.enabled is True` (`actionlog.py:84-87`). The key is absent on
  adopters initialised before 1.0.0, and `safe_append` is fail-open.
- Shadow can therefore record nothing, which reads the same as "would never act". The `actionlog.py` docstring calls
  the log a "best-effort status cache", which contradicts the reliability row's "recorded durably".
- **Change:** `feature_mode` reports shadow as ineffective, loudly (stderr plus doctor), when the action log is off.

**G-6 (MEDIUM, resiliency).**
- FR-23 covers only turning a feature off mid-goal. Turning one on is unspecified: a legacy plan-review record has no
  manifest hash, and an already-implemented goal meets the size refusal.
- FR-3 names no required version. If it is 1, FR-3 is vacuous; if it is 2, enabling enforce needs a config edit.
- It also guards only the safe direction. An older plugin tolerates unknown keys (`state.py:63-80`), so a mixed fleet
  sharing a committed config enforces on some machines and is silently off on others.
- **Change:**
  - Treat a legacy record as unbound.
  - Snapshot the mode at claim.
  - State the version number.
  - Doctor says "enforced only on plugin ≥ X".

**G-7 (MEDIUM, prose budget).** D-10 and the maintainability row cite only word headroom, but `est_tokens` binds
tighter: implement 0, review-post-pr 3, plan-review 8.
- Slice 3 must teach `--defer`/`--release` in `filing.md` (orchestrator, 22 tokens).
- Slice 6 must add `--review-output` to the record gestures at `running.md:149` and
  `sigma-plan-review/SKILL.md:122` (8 tokens).
- Slice 5 must teach `amend-slices`.
- **Change:** give a per-slice token line. The precedent is `sigma-review/references/test-review.md`, a conditional
  reference outside the measured set.

**G-8 (LOW-MEDIUM, root checkout; slice 5).** D-19 says manifest edits go in the goal worktree. But
`slices.manifest_path` reads `<sdlc_dir>/plans/`, and `running.md:330` says to pass the MAIN checkout's `.sdlc`. That
makes the canonical copy the untracked root file that #1161 already flags. **Change:** resolve branch-or-main, as
`plan_copies` does.

**G-9 (LOW, false claims and scale).**
- The data-retention row's "pruned by the existing log roll" is false: no roll exists, and
  `goal_state_prune.py:10-11` deliberately leaves the action log alone.
- `status.py` reads no action log today. Status segments that scan every goal log cost O(total log) per run: 1.9 MB
  in 288 files here, about 190 MB at 100x. **Change:** bound the read (active goals, or a tail as in
  `goal_state_prune._LOG_TAIL_BYTES`) or keep a counters file.

**G-10 (LOW).**
- **Slice 6 data value.** At plan-review the mapped tests do not exist yet (`sigma-plan/SKILL.md:52`), so `_exists`
  fails for nearly every mapping and red-proven reads about 0. Shadow measures only TEST-MAP presence. D-25's cut
  option looks right.
- **Marker format.** The `sigma:deferred-from=` value must be a bare number, never `#N` (phantom-blocker scan).
- **Control coverage.** The BR-6 control patches only `gh_api`, not the direct-gh baseline sites or ledger appends.

## Adjudication (by id)
- **Resolved:** D-3, D-5, D-6, D-7, D-8, D-9, D-11, D-12, D-13, D-14, D-16, D-22, D-23, D-26. X-1..X-26 are accepted;
  X-17 is accepted only with G-3's Intent correction.
- **Open, not blocking:** D-1, D-2, D-4, D-10 (G-7), D-15, D-17 (G-3), D-18, D-19 (wrong as stated, G-8), D-20, D-21,
  D-24, D-25 (G-10), PC-17, PC-26, PC-27, R-1..R-7, G-2..G-10.
- **Blocking:** G-1. It stops slice 5, which is waiting for FR-15/FR-16 to name the frozen projection, or for G-2's
  cut. B-1..B-3 block only the uncompiled follow-on.
- **Falsified premises to carry:** PC-1, PC-3, PC-7 (in part), PC-8, PC-12, PC-13, PC-14, PC-15, PC-18, PC-23 (in part).

## Against rounds 1-3
- **Round 1:** agree on F-1..F-10, and its REJECT was right for that revision.
- **Round 2:** agree on N-1..N-6. N-2's diagnosis also supports G-10.
- **Round 3:**
  - Agree on R-1..R-7, and on R-4's CAPPED marker.
  - **Disagree with CONFIRM.** R-5 looked at FR-15's mode clause but missed G-1, the `status` field, which makes the
    FR wrong rather than merely unqualified.
  - None of the three rounds checked token headroom (G-7), the action-log dependency (G-5), slice 5's zero usage
    (G-2), or whether deferral can move the 215 (G-3). Round 3 re-ran neither the counts nor the headroom.

## Not verified
- Pick, phase and label parity on an adopter config.
- The 60-PR size sample and the $0.53 median.
- Any end-to-end shadow run.
- The reviewer brief mechanism.
- Whether agents would use `--defer` in practice.

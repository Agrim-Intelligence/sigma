# Launch-readiness review plan

The executable version of the review in #307. Every dimension has a measurement, a pass threshold, a
gating flag and a countermeasure against gaming. The review runs in a fixed order, and every spend
has a ceiling. This page is a plan: nothing in it has been scored, and no dimension is scored by it.

## Status

Proposed. The ceilings are the owner's decision, made on 2026-10-03 and recorded here from the owner's
chat answer (Question 2 of 8: 75M tokens with a checkpoint at 40M). The pilot ceiling was set later, at $75 as a hard
stop (2026-10-04, recorded in a comment on #361). No other committed file or issue holds those figures yet, so the owner's merge of this page
confirms them. They are provisional: the calibration pilots (#361) have run, and the section "Measured ceilings" below recomputes them
from the measured spend in [the calibration record](evidence/cost-calibration.md). A step the pilots did not measure keeps its
unmeasured estimate, and this page says so wherever it quotes one. The two pages it is built on keep their
own status: [definition.md](definition.md) is still `proposed`, and signing it is the owner's merge.
[decision-rule.md](decision-rule.md) fixes which dimensions exist, what they are called and which
gate; `tools/readiness/decide.py` enforces that, and `tests/test_review_plan.py` keeps this page and
`scorecard.json` in step with it.

## Scope

What is being reviewed is what [definition.md](definition.md) says ships: a fresh public snapshot of
Sigma 1.0.0, installed through the Claude Code plugin marketplace, for individuals and small teams on
GitHub. The supported cells are Claude Code on Linux with Python 3.10 to 3.13 and Claude Code on macOS
with Python 3.12, each in local-goals and github modes. The macOS claim was narrowed to 3.12 to match
what CI runs (#493, `tests/test_launch_ci_cells.py`). Codex, Cursor and Windows are experimental and
are not launch-blocking cells. A finding is a launch blocker only if it is in a class of
[decision-rule.md](decision-rule.md); everything else is filed `launch:next`.

This plan covers the review and the benchmark. It does not run any step of either.

## Dimensions

Scored 0 to 4. `Gating: yes` means the dimension must score 3 or more, with evidence links, for a GO
([decision-rule.md](decision-rule.md)). The names are the ones that rule pins; the epic's table
spelled two of them longer (migration, community), and the measurements for those words stay in the
rows below.

| # | Dimension | Measurement | Pass threshold | Gating |
|---|---|---|---|---|
| D0 | Launch definition | `docs/launch/definition.json` | `status: signed` | prerequisite |
| D1 | Correctness | high-risk units reviewed in full, seeded sample of the rest, planted-defect recall, mutation kill rate, flake census (10 runs Linux + 10 macOS), coverage | 0 open B1/B8; recall ≥ 80%; 0 flaky tests; kill rate recorded per module | yes |
| D2 | Skills | every documented command/verb/flag resolves (script); 5,000-token budget (already 0 waivers); model review of the 10 phase skills; instruction tokens per phase (#262) | 100% resolve; 0 gate-skip ambiguities on supported cells | yes |
| D3 | Outcomes and cost | pre-registered benchmark (3 arms: Sigma, plain agent, matched-spend; about 15 tasks, 1 repeat, 3 outside traps), cost per passing run | per `docs/bench/preregistration.md` as amended | yes |
| D5 | Five properties | crash/restore drills at random kill points (N ≥ 5 each), dead-vs-idle (#265), growth audit, 10x/100x table | every drill recovers alone or has a documented polite lever; every growing store has a pruner or cap | yes |
| D6 | Platform | one live run + one CI leg per supported cell | 100% of supported cells | yes |
| D7 | Onboarding | timed first run by agents and by 2–3 outside people; every refusal message listed | median ≤ 30 min to first goal done; every refusal prints its fix | yes |
| D8 | Docs | claim check over all docs, link + anchor check, CHANGELOG vs git | 0 unresolvable claims, 0 broken links | yes |
| D9 | Upgrade, coexistence, uninstall | ≥ 3 more migrated repos, shared-path inventory, rollback, clean uninstall | 0 lost state; nothing left behind | yes |
| D10 | Security, privacy, exposure | threat model, red team, egress capture, history and private-reference scans, blast-radius drill | 0 open B2/B7/B8 | yes |
| D11 | Operations | SECURITY.md, release + rollback + incident runbook, templates, CI health | SECURITY.md and runbook exist; CI green on all legs | yes |
| D12 | Market | dated matrix with evidence grades | informational; blocks only via B3 | no |
| D13 | Legal and naming | licences, provenance, name search | no unresolved conflict | yes |

D3's "as amended" points at the amendment log in `docs/bench/preregistration.md` (its Deviations section),
which records the reduction to 3 arms and the paired sign test.

D0 is not a scored row: it is the "definition signed" clause of the rule, read from `definition.json`
directly. There is no D4; the numbering follows the revised table in the readiness epic (#329).

How the table avoids the overlaps in #307: secrets and exposure are measured once, in D10; live host
runs once, in D6; injection once, in D10 (D1 keeps only the mechanical correctness checks); outcomes
and cost are one benchmark, D3.

### Where each dimension stands on main

What is left before each gating dimension can be scored 3 or more. "Exists" cites only committed files
(this page's test checks that each path is on disk). An issue number alone is never evidence: a closed
issue counts only through the artifact it committed, and a still-open one is listed as work to
produce. "Owner-gated" marks pieces only the owner can supply. The whole table is a dated snapshot of main at
`4c8562f` (2026-10-03). Only the "exists" cells are machine-checked; `tests/test_review_plan.py` also checks that a
few named "still to produce" files are still absent, so landing one forces this table to be updated.

| # | Evidence that already exists on main | Evidence still to be produced | Owner-gated |
|---|---|---|---|
| D0 | `docs/launch/definition.json` (`status: proposed`), `docs/launch/definition.md`, `tests/test_launch_definition.py`, `tests/test_launch_ci_cells.py` | `status: signed`, `signed_by` and `signed_on` | the owner's merge of the definition page |
| D1 | `tools/readiness/baseline.py`, `docs/launch/evidence/inventory-859290305d97.json`, `docs/launch/review-units.json` (16 high-risk units and a 17-unit sample at frozen commit `c3faf6f23e12`, re-cut by #581; the first cut is kept at `docs/launch/evidence/review-units-a5c615062313.json`), `tools/readiness/seed_defects.py`, `docs/launch/seeded-defects.md`, `docs/launch/coverage.json`, `skills/agrim-loop/scripts/flake_check.py`, `skills/agrim-loop/scripts/mutation.py` | the line-by-line review itself (S4); a real seed set (only the three made-up examples in `tests/fixtures/readiness_seed_examples` exist); a flake census of 10 runs on Linux and 10 on macOS (#337, open); a mutation kill rate per module (#360, open: no kill rate is recorded on main); the open B1 blocker #514 closed with its fix (the D1 threshold is 0 open B1/B8); the second-vendor pass; the units are cut at `c3faf6f23e12` and the S2 gates ran at `859290305d97`, whose tracked code is the same, so no further re-freeze is open unless main moves again | a person who will not review writes the seeds |
| D2 | `tests/test_skill_structure.py`, `evals/skill_budget_waivers.json` (`waivers` is empty), `evals/phase_context_budget.json` (instruction bill per phase, a down-only ratchet, #262), `tests/test_documented_gestures.py` (copyable Python gestures in shipped docs resolve, #339), `tests/test_script_help.py` | resolution of documented verbs and flags that are not copyable Python gestures; the model review of the 10 phase skills (S5); a count of gate-skip ambiguities on supported cells | none |
| D3 | `docs/bench/preregistration.md` (reduced design, exact paired sign test), `docs/launch/evidence/cost-calibration.md` (review cost measured; benchmark cost not measured), `evals/bench/decision_rule.py`, `evals/bench/bench.py`, `evals/bench/arms/sigma.py`, `evals/bench/arms/matched.py`, `evals/bench/meter.py`, `docs/launch/evidence/meter-sonnet-5-5-cf31b72c6967.json`, `tests/test_bench_decision_rule.py`, `tests/test_benchmark_harness.py`, `evals/bench/tasks/manifest.json` (a draft: 8 external and 4 internal tasks verified, three trap slots empty, `frozen` false), `tools/readiness/bench_tasks.py`, `tests/test_bench_tasks.py`, `docs/bench/task-sourcing.md`, `docs/bench/trap-author-brief.md`, `docs/launch/evidence/355-bench-task-verification.json` | the freeze of the task set and the hash of its manifest at the freeze commit (the draft is not frozen); three traps written by someone outside the Sigma team; the hidden tests of those traps; the run (#275, open) and its results file under `docs/launch/evidence/`, which the scorecard names in `benchmark_results` (still null); the S9 spend ceiling | a trap author; the four open choices listed in the pre-registration; the S9 ceiling |
| D5 | `tools/readiness/drills.py`, `docs/launch/evidence/drills-cde9869d0cf8.json` (4 hermetic drills, 5 runs each, on macOS), `tools/readiness/growth_audit.py`, `docs/launch/growth-audit.md`, `docs/launch/growth-audit.json`, the retention decisions `docs/launch/b6-worktrees.md` and the other `docs/launch/b6-*.md`, `hooks/session_start.sh` and `skills/agrim-doctor/scripts/doctor.py` (the dead-vs-idle staleness check, #265) with `tests/test_session_start.py` and `tests/test_doctor.py` | the drills against a real host rather than hermetic fakes, and on Linux; a 10x/100x table across every per-item path (the retention pages state a growth figure per store, not one table); a recorded live control of dead-vs-idle on a really stopped watcher (`tests/test_session_start.py` has a dead-loop fixture, which is a hermetic control, not that record) | none |
| D6 | `.github/workflows/ci.yml` (five legs: Linux 3.10 to 3.13, macOS 3.12), `docs/launch/evidence/ci-supported-cells-bd969b48018a.json`, `tests/test_launch_ci_cells.py`, `tools/onboarding_control.py` and `docs/onboarding-control.md` (a control run against stubbed tools and a stateful fake `gh`, not a live run) | one live run per supported cell: a live-model loop run on Claude Code (#300, open) and github mode against real GitHub (#301, open), on both operating systems; the CI result on the launch commit | a throwaway repository for the live drills (none exists yet) |
| D7 | `tools/onboarding_control.py`, `docs/onboarding-control.md`, `tests/test_onboarding_control.py`, `docs/launch/evidence/egress-onboarding-local.json` | timed first runs by agents and by 2 to 3 outside people; the list of every refusal message with its fix | the outside people |
| D8 | `tests/test_doc_claims.py`, `tests/test_known_false_claims.py`, `tests/test_doc_links.py` (relative links and anchors), `tests/test_documented_gestures.py`, `tests/test_release_consistency.py` | a claim check over all docs beyond the named false claims; a CHANGELOG-against-git check (the release test checks the release runbook headings and the pin-rollback evidence, that published CHANGELOG headings carry an ISO date, and that the doctor's fallback repository equals the definition's public repository; it does not compare against git history) | none |
| D9 | `docs/upgrading.md`, `skills/agrim-doctor/scripts/migrate.py`, `docs/uninstall.md`, `tools/readiness/leftovers.py`, `tests/test_readiness_leftovers.py`, `tests/test_coexist.py`, `docs/launch/evidence/pin-rollback-2026-10-02.md` (rollback not yet demonstrated) | at least 3 more migrated repos with a before and after; a shared-path inventory; a rollback that works; a clean-uninstall run that leaves nothing behind | none |
| D10 | `docs/threat-model.md`, `tests/test_threat_model.py`, `tools/readiness/exposure_scan.py`, `docs/launch/evidence/exposure-8aee0c74c526.md`, `docs/launch/evidence/exposure-disposition-8aee0c74c526.md`, `docs/launch/evidence/refs-8aee0c74c526.md`, `tools/readiness/egress_capture.py`, `docs/launch/evidence/egress-full-suite-a9747324eee1.json`, `docs/launch/write-surface.md`, `tests/test_hostile_inputs.py` (the script-level half of injection) | the model-level injection drill (`tools/readiness/injection_drill.py` is built and has never been run); the red team; the blast-radius drill; the history and private-reference scans repeated on the launch commit and on the public tree (#397, open) | a throwaway repository for the drill (none yet) |
| D11 | `SECURITY.md`, `docs/release.md` (release, rollback and incident runbook), `docs/publish-runbook.md`, `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/bug_report.yml`, `.github/pull_request_template.md`, `.github/workflows/ci.yml` | CI green on all five legs on the launch commit; a rollback that has been run (`docs/release.md` says pinning is unsupported until the public tag exists) | the release is the owner's action |
| D12 | none: the market scan (S8) has not run | the dated matrix with evidence grades | none |
| D13 | `LICENSE` (MIT) | the whole of #343: the dependency licence list, the provenance notice, the dated name search | the legal review (#343): the owner is getting counsel, and D13 is not scoreable until counsel answers |

Four pieces are owner-gated across the whole review: a throwaway repository for the live drills (D6,
D10), a trap author (D3), the legal and naming review (D13), and a pilot spend ceiling (#361, set on 2026-10-04 at $75,
and the pilots have run). Two more are needed and are not yet arranged:
a seed author (D1) and the outside people (D7).

## Reviewer rules

1. Work in the frozen clone produced by `tools/readiness/baseline.py snapshot`, never in the live checkout.
2. Use the high-risk list and sample from `docs/launch/review-units.json`.
3. Plant defects and score recall per `docs/launch/seeded-defects.md`. A review whose recall is below 80% is re-run, not reported.
4. A different model vendor reviews about 20% of the units and verifies every B-class finding. On Codex, this doubles as the #302 live run.
5. Every finding carries a reproducer or a cited `file:line`, and a second independent pass verifies it before it is filed.
6. Before filing, re-check the finding against current `main`.
7. Before filing, run `python3 skills/agrim-scope/scripts/dedup.py .sdlc "<finding text>"`. A `duplicate` hit becomes a comment on that issue, not a new issue.
8. At most 15 new issues per dimension per pass. Everything beyond that goes into one summary issue per dimension.
9. Never use the words "needs", "after", "requires", "depends on", "waiting on" or "blocked by" before an issue number in prose. `docs/dossier-pipeline.md` §7e explains why.

## Execution order and ceilings

The unit is processed tokens: input, including cache reads, plus output, as the host reports them.

| Step | Work | Estimate (unmeasured) | Measured ceiling (#361 pilots) |
|---|---|---|---|
| S0 | owner decisions | 0 | not applicable |
| S1 | freeze commit, inventory, exposure scans, legal | < 0.5M | not measured |
| S2 | mechanical gates | 1–2M | not measured |
| S3 | calibration pilots (the pilot goal, #361) | 20–50M | 3.89M spent (measured, $2.50; P-a and P-b only, P-c and P-d unmeasured) |
| S4 | high-risk code review + sample + verification | 25–45M | 42.0M (37.0M + 5.0M, formulas below; shallow single passes, second vendor excluded) |
| S5 | phase-skill review | 8–15M | not measured |
| S6 | host runs, migration, drills, red team | 20–50M | not measured |
| S7 | outside first-run | 0 | not applicable |
| S8 | market scan | 3–6M | not measured |
| S9 | benchmark (own ceiling) | set from the pilots | not measured: the benchmark pilot did not run |
| S10 | synthesis | 1–2M | not measured |

Where the last column says "not measured", the estimate in the column before it still stands and is still
**unmeasured**. The pilots measured review and verification passes only (S3, S4). When another step is measured, its
estimate is replaced by a measured number in the same way.

### Measured ceilings

Source: [the calibration record](evidence/cost-calibration.md), measured on 2026-10-04 on the first cut's units (`a5c615062313`); the per-line rates are applied here to the lines of the re-cut units (#581), which were not metered. All
numbers are processed tokens (input including cache reads and writes, plus output). The per-line figures come from one
run per unit, so each carries the spread the record shows (152 to 422 tokens per line for a review pass).

| Step | Formula | Tokens | Dollars |
|---|---|---|---|
| S3 | the sum of the five metered runs in the record (A06's review is one of them) | 3,887,193 (spent) | $2.50 (spent) |
| S4, Tier A (high-risk, 36,519 lines) | (review 379.6 tokens per line, the line-weighted mean of A01 and A06 + verification 411.2, A01 only) × 36,519 lines × 1.3 headroom | 37.54M (28.88M without headroom) | $20.53 |
| S4, Tier B sample (14,792 lines) | (review 151.7 + verification 169.9, B01 only) × 14,792 lines × 1.3 | 6.18M (4.76M without headroom) | $4.99 |
| S4 total | Tier A + Tier B | 43.73M (33.64M without headroom; shallow single passes with no seeds, and no second-vendor pass) | $25.52 |
| S9 benchmark | measured per-run cost per arm × runs in the pre-registration × 1.3 | not measured: no run exists | not measured |

Two extrapolations are in S4 and are labelled so: the verification rate was measured on one Tier A unit and one Tier B
unit and is applied to every line of its tier (verification cost follows the number of findings, not the lines); the
Tier A review rate averages two units and is not applied to Tier B. A second-vendor pass over about 20% of the units
(reviewer rule 4) is **not included and not measured**. The measured passes also had no planted defects and were shallower than the plan's review.

The review ceiling is **75M processed tokens** for S1–S8 plus S10, with an owner checkpoint at **40M**.
The owner chose both on 2026-10-03; the epic's earlier 150M ceiling and 75M checkpoint are replaced.
The pilots' measured numbers are applied to both below (a step the pilots did not measure stays unmeasured), and S9 gets its own
owner-approved ceiling, which the benchmark pilot would have set and did not.

**What the 40M checkpoint counts: decided.** The owner decided on 2026-10-04, in chat, that pilot spend counts toward
both counters, as this page had recommended. The counter is the running sum of processed tokens, as the hosts report
them, over S1–S8 plus S10: the same counter as the 75M ceiling, and S3 (the pilots) is part of it. The $75 pilot
ceiling stays the hard stop on pilot dollars. Pilot spend so far: 3,887,193 processed tokens and $2.50, so
**36,112,807 tokens remain under the 40M checkpoint and 71,112,807 under the 75M ceiling** for the rest of the review.
The checkpoint is a stop-and-ask, not a failure.

**What the measured numbers imply for the caps.** S4 at its measured ceiling is 43.73M (33.64M without headroom).
Counted on top of the pilots, that is 47.6M (37.5M without headroom), so the 40M checkpoint is reached inside S4 when
headroom is used, and is not reached at all when it is not (2.5M to spare). The full review does **not** fit under 75M on these
numbers: after the pilots and S4 at its ceiling, 27.38M remain, and the still-unmeasured steps S1, S2, S5, S6, S8 and S10
have lower bounds that sum to 33.5M (0.5 + 1 + 8 + 20 + 3 + 1), a shortfall of at least 6.1M before any of them reaches
its upper bound (their upper bounds sum to 75.5M). Without S4's headroom the lower bounds fit (37.5M remain), but the
upper bounds do not. Covering S4 in full, S1, S2, S5, S8 and S10 at their lower bounds costs 13.5M and leaves 13.9M for
S6, against its estimate of 20–50M: S6 (host runs, drills, red team) is the step that would be cut, and the cap rules
below say how. These statements rest on the unmeasured estimates for six steps; they are not a forecast.

OWNER DECISION (open, #361): approve the measured ceilings above as the review's S3 and S4 ceilings. Recommendation:
approve them, keep 75M and 40M as set, and measure S5 and S6 in their own first runs before either is raised. S9 has no
measured basis until the benchmark pilot runs, and this page does not choose its ceiling.

**At 75M the review will cover less than the plan describes.** What the plan does when the ceiling binds:

1. High-risk units are reviewed in full first. Nothing else runs ahead of them.
2. The sample rate is reduced before any high-risk unit is cut.
3. Every unit not covered is listed by id in the scorecard evidence for D1 and D2, with the reason. A
   unit is never dropped silently, and a dimension whose coverage fell short does not score 3 on
   the strength of the part that ran.
4. At 40M the review stops and the owner decides whether to continue, cut scope, or raise the ceiling.
   Nobody raises it unrecorded.

## Facts as of 2026-10-03

Measured on 2026-10-03 against main at `4c8562f`, over REST. The state of every issue this plan cites
was read with `python3 tools/readiness/baseline.py status . --issues <list> --repo Agrim-Intelligence/sigma --json`,
and the counts with `gh api`, not from the older plan.

| Fact | State |
|---|---|
| #330 launch definition, #331 decision rule and checker | closed; both artifacts are on main. The definition is still `proposed` |
| #493 macOS support claim narrowed to Python 3.12 | closed; merged |
| #194 coverage record, #260 reviewer-independence wording, #354 benchmark harness, #362 hostile-input fixtures, #502 paired benchmark test | closed; artifacts merged |
| #312 github-mode merge parked without a test command, #308 board mirror after a repository rename | closed. The old plan listed both as open |
| #259 secret scan at commit, #251 coexistence gap, #237 onboarding control, #262 instruction bill, #265 dead-vs-idle | closed; counted here only through the files cited above |
| #275 benchmark run, #300 and #301 live runs, #302 Codex, #303 Cursor, #343 legal and naming, #361 calibration pilots, #337 flake census, #360 mutation measurement | open |
| #305 Windows first-run | open, and a one-off manual run. `.github/workflows/windows.yml` runs on `workflow_dispatch` only, so there is no Windows CI on every change. Windows is experimental |
| #307 the old plan | open; points here once this page is on main |
| open `launch:blocker` issues | 1 when re-read on 2026-10-04, one day later than the rest of this section: #514, a B1 finding opened 2026-10-03, confirmed by the owner on 2026-10-04 and queued to be fixed. Volatile: `tools/readiness/decide.py` reads the live count, and a GO needs it at zero |
| open issues overall | more than 100, and moving; Rules 7 and 8 exist to stop duplicates from growing the count |
| the B6 retention family and the leak-scan B2 fixes | merged; the retention decisions are the `docs/launch/b6-*.md` pages |
| CI | `.github/workflows/ci.yml`: five legs, Linux with Python 3.10, 3.11, 3.12 and 3.13, macOS with Python 3.12 |

## What changed from #307 and why

Source: the plan review of 2026-09-29 (findings 4, 8, 9, 14, 15, 19 and 22), written up as epic #329.

- Twelve overlapping dimensions became the table above. Secrets, live host runs and injection were each
  listed twice; each is now measured in one place. Outcomes (old 3) and cost (old 4) were the same
  data and are D3.
- Every dimension now has a pass threshold, a gating flag and a stated measurement. The epic had a
  scored table and no rule for scoring it; the rule is in [decision-rule.md](decision-rule.md).
- Market no longer gates the launch. D12 is informational and blocks only through B3.
- Dimensions 6, 7 and 9 no longer start "without a ceiling": each holds live-model runs. Every spend
  now sits in a step with an estimate, an order and a ceiling.
- The spend estimates are marked unmeasured, and the ceiling was cut from 150M to 75M on the owner's
  decision, recomputed from the pilots on 2026-10-04 for S3 and S4 (RELIABILITY: a claim carries its measurement).
- The benchmark shrank from four arms and three or more repeats to three arms, about 15 tasks and one
  repeat, with the exact paired sign test of `docs/bench/preregistration.md`. At this size the
  likely outcome is INCONCLUSIVE, and the pre-registration says what that permits.
- Stale facts were replaced (see the section above): #312 and #308 are closed, and Windows CI is not
  run on every change.
- The macOS support claim was narrowed to what CI runs.
- Filing rules were added (Rules 7 and 8), because more than 100 issues were already open and nothing
  stopped duplicates.
- D13 is not scoreable until counsel answers on the legal and naming review.

## Pointing #307 at this page

Once this page is on main, #307's body gets one prepended line, written with REST and a file so the body
is not mangled: `gh api -X PATCH repos/Agrim-Intelligence/sigma/issues/307 -F body=@<file>`
(`-F`, not `-f`, because only `-F` reads the file). The line is
`Superseded plan: the executable plan is docs/launch/review-plan.md; the text below is kept for history.`
Verify with `gh api repos/Agrim-Intelligence/sigma/issues/307 --jq '.body | split("\n")[0]'`, and compare the
rest of the body with the copy read before the write.

## How to change this page

Any change to a dimension, a gating flag or a threshold is a pull request the owner merges, and it
changes `docs/launch/decision-rule.md` and `tools/readiness/decide.py` in the same pull request when it
touches which dimensions exist, their names or which gate. `tests/test_review_plan.py` fails when this
page, `scorecard.json` and the decision rule disagree. Run it with
`python -m pytest tests/test_review_plan.py -q`.

# Launch-readiness review plan

The executable version of the review in #307. Every dimension has a measurement, a pass threshold, a
gating flag and a countermeasure against gaming. The review runs in a fixed order, and every spend
has a ceiling. This page is a plan: nothing in it has been scored, and no dimension is scored by it.

## Status

Proposed. Thresholds amended by the owner on 2026-10-12: see "Owner decisions of 2026-10-12". The ceilings are the owner's decision, made on 2026-10-03 and recorded here from the owner's
chat answer (Question 2 of 8: 75M tokens with a checkpoint at 40M; both raised on 2026-10-06, see "Owner decisions of 2026-10-06"). The pilot ceiling was set later, at $75 as a hard
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
| D1 | Correctness | high-risk units reviewed in full, seeded sample of the rest, planted defects seeded, mutation kill rate, flake census (10 runs Linux + 10 macOS, Python 3.12), coverage | 0 open B1/B8; the review claims "found and verified N defects" (N counted, each with a reproducer or cited line), never a recall percentage (rule AMENDED 2026-10-06); 0 flaky tests on Python 3.12 (macOS and Linux; the 3.10, 3.11 and 3.13 cells are covered by the nightly full matrix, not by a census); kill rate recorded for the modules that finished within budget, absent modules listed with the reason (AMENDED 2026-10-12) | yes |
| D2 | Skills | every documented command/verb/flag resolves (script); 5,000-token budget (already 0 waivers); model review of the 10 phase skills; instruction tokens per phase (#262) | 100% resolve; 0 gate-skip ambiguities on supported cells | yes |
| D3 | Outcomes and cost | pre-registered benchmark (3 arms: Sigma, plain agent, matched-spend; about 15 tasks, 1 repeat, 3 outside traps), tokens per passing run (dollars indicative). NOT run for launch (AMENDED 2026-10-12): the measured-results document `docs/launch/evidence/measured-results.md` stands in, and a scorer must not read this cell as still required | per `docs/bench/preregistration.md` as amended; for launch the results file is the measured-results document `docs/launch/evidence/measured-results.md`, which makes no comparative claim (the comparative run, #275, is past launch; AMENDED 2026-10-12) | yes |
| D5 | Five properties | crash/restore drills at random kill points (N ≥ 5 each), dead-vs-idle (#265), growth audit, 10x/100x table | every drill recovers alone or has a documented polite lever; every growing store has a pruner or cap | yes |
| D6 | Platform | one live run per macOS cell (local-goals, github) + the OnChart statement; one CI leg per supported cell, from the nightly full matrix; Linux covered by that matrix and Docker runs (the Linux live-host run is past launch) | 100% of supported cells have a CI leg in the nightly full matrix; the macOS cells have a live run; Codex and Cursor are experimental, not cells (AMENDED 2026-10-12) | yes |
| D7 | Onboarding | timed first runs by agents; every refusal message listed; the OnChart statement (owner statement, unverified); the outsider first-run test (#353) is `launch:next` | median ≤ 30 min to first goal done on the agent runs; every refusal prints its fix; the claim is weaker than "outside people" and any evidence says so (AMENDED 2026-10-12) | yes |
| D8 | Docs | claim check over all docs, link + anchor check, CHANGELOG vs git | 0 unresolvable claims, 0 broken links | yes |
| D9 | Upgrade, coexistence, uninstall | migration verified on scratch repos by agent runs, shared-path inventory, clean uninstall run, rollback (before launch against a rehearsal tag, after launch against the real tag) | 0 lost state; nothing left behind (AMENDED 2026-10-12: the "≥ 3 more migrated repos" count is dropped) | yes |
| D10 | Security, privacy, exposure | threat model, red team, egress capture, history and private-reference scans, blast-radius drill | 0 open B2/B7/B8 | yes |
| D11 | Operations | SECURITY.md, release + rollback + incident runbook, templates, CI health | SECURITY.md and runbook exist; "CI green on all legs" means the nightly full matrix (five legs) green on the launch commit, proven by one manual dispatch of `full` on the freeze commit (AMENDED 2026-10-12) | yes |
| D12 | Market | dated matrix with evidence grades | informational; blocks only via B3 | no |
| D13 | Legal and naming | licences, provenance, name search, scored on recorded owner decisions with accepted risk (no counsel consulted) | every cell of `legal.md` reads OWNER DECISION or a sourced fact. A sourced fact that shows a conflict would pass this wording; that follows owner decision 3 (no counsel consulted). The guard: D13 cannot be scored 3 while the name cells read OPEN (AMENDED 2026-10-12) | yes |

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
produce. "Owner-gated" marks pieces only the owner can supply. The whole table was regenerated on
2026-10-12 from main at `de188d1f`, replacing the 2026-10-03 snapshot. Only the "exists" cells are
machine-checked; `tests/test_review_plan.py` also checks that a few named "still to produce" files are
still absent, so landing one forces this table to be updated. The thresholds these rows are read
against are the amended ones in the Dimensions table above. Evidence on main is frozen at several
commits (`c3faf6f23e12`, `859290305d97`, `c82e3dfa7420`, `8aee0c74c526`, `7997e29e4270`), all well
behind main; each record says what it covers, and none is a result on the launch commit.

| # | Evidence that already exists on main | Evidence still to be produced | Owner-gated |
|---|---|---|---|
| D0 | `docs/launch/definition.json` (`status: signed`, `signed_by` swapnil-agrim, `signed_on` 2026-10-04), `docs/launch/definition.md`, `tests/test_launch_definition.py`, `tests/test_launch_ci_cells.py` | none for the clause itself; `decide.py` re-reads it from `main` at the verdict | none |
| D1 | `tools/readiness/baseline.py`, `docs/launch/evidence/inventory-859290305d97.json`, `docs/launch/review-units.json`, `tools/readiness/seed_defects.py`, `docs/launch/seeded-defects.md`, `docs/launch/coverage.json`, `skills/sigma-loop/scripts/flake_check.py`, `skills/sigma-loop/scripts/mutation.py`, `docs/launch/evidence/mechanical-gates-859290305d97.md`, `docs/launch/evidence/review-s4-c3faf6f23e12.md`, `docs/launch/evidence/flake-c82e3dfa7420.json` (10 runs Linux + 10 macOS, Python 3.12, 0 flaky), `docs/launch/evidence/mutation-70c2c6e96136.json` (14 of 33 modules finished) | a dated record of the open B1/B8 read; triage of the nightly full-matrix failure of 2026-10-11 in `tests/test_bounded_run.py` on the Linux legs (read from the gap assessment of 2026-10-11, not re-measured here); the absent mutation modules listed by name with the reason | none (coverage cuts accepted 2026-10-06) |
| D2 | `tests/test_skill_structure.py`, `evals/skill_budget_waivers.json` (`waivers` is empty), `evals/phase_context_budget.json`, `tests/test_documented_gestures.py`, `tests/test_documented_gh_verbs.py`, `tests/test_script_help.py`, `docs/launch/evidence/review-s5-7997e29e4270.md` (10 skills reviewed; gate-skip findings recorded) | the gate-skip wording fixes filed as #715 (open, `launch:next`) and a re-review of only the skills it touches, to show the ambiguity count is 0 | none |
| D3 | `docs/bench/preregistration.md`, `docs/bench/token-budget.md`, `docs/launch/evidence/cost-calibration.md`, `evals/bench/decision_rule.py`, `evals/bench/bench.py`, `evals/bench/meter.py`, `docs/launch/evidence/meter-sonnet-5-5-cf31b72c6967.json`, `evals/bench/tasks/manifest.json`, `docs/launch/evidence/355-bench-task-verification.json`, `tests/test_bench_decision_rule.py`, `tests/test_benchmark_harness.py` | `docs/launch/evidence/measured-results.md` (the measured-results document, produced by a separate pull request; no comparative claim); `benchmark_results` in the scorecard pointing at it (still null). The comparative run (#275, `launch:next`) is past launch | none |
| D5 | `tools/readiness/drills.py`, `docs/launch/evidence/drills-cde9869d0cf8.json` (4 hermetic drills, 5 runs each, macOS), `tools/readiness/growth_audit.py`, `docs/launch/growth-audit.md`, the retention decisions `docs/launch/b6-worktrees.md` and the other `docs/launch/b6-*.md`, `hooks/session_start.sh`, `skills/sigma-doctor/scripts/doctor.py`, `tests/test_session_start.py`, `tests/test_doctor.py` | the drills on Linux (CI or Docker); one 10x/100x table across the per-item paths (the retention pages state a growth figure per store, not one table); a recorded control of dead-vs-idle on a really stopped watcher (the tests hold a hermetic dead-loop fixture, not that record) | none |
| D6 | `.github/workflows/ci.yml` (PRs and pushes run one macOS leg; the five-leg `full` matrix runs nightly and on demand), `docs/launch/evidence/ci-supported-cells-bd969b48018a.json`, `tests/test_launch_ci_cells.py`, `tools/onboarding_control.py`, `docs/onboarding-control.md`, `docs/launch/evidence/live-onboarding-2026-10-12.md` (one local-goals and one github run on macOS; nothing on Linux, Codex or Cursor), `docs/launch/evidence/rehearsal-f92a7528a26b.md` (local rehearsal, not GitHub CI) | a committed record of the OnChart statement; a green nightly full matrix on the launch commit (see D11). The Linux live-host run is past launch | the OnChart statement (owner statement, unverified) |
| D7 | `tools/onboarding_control.py`, `docs/onboarding-control.md`, `tests/test_onboarding_control.py`, `docs/launch/evidence/egress-onboarding-local.json`, `docs/launch/evidence/live-onboarding-2026-10-12.md`, `docs/launch/evidence/d7-onboarding-timed-runs-f627d06a.md`, `docs/launch/evidence/d7-onboarding-refusals-f627d06a.md`, `docs/launch/evidence/d7-onchart-owner-statement-2026-10-12.md` | timed first runs by agents; 45 triggered first-run refusal scenarios, each checked for its fix (not every message in the code); the OnChart statement recorded as an owner statement. The outsider first-run test (#353) is `launch:next` | the OnChart statement |
| D8 | `tests/test_doc_claims.py`, `tests/test_known_false_claims.py`, `tests/test_doc_links.py`, `tests/test_documented_gestures.py`, `tests/test_release_consistency.py`, `docs/launch/evidence/docs-claim-sweep-f627d06ad3f6.md` (44 files swept for stated claims, 119 claims and 786 path references checked, 3 false sites fixed), `docs/launch/evidence/changelog-vs-git-f627d06ad3f6.md` (12 release sections against tags and manifest commits) | the sweep is not every sentence: the long design pages were swept for numbers and defaults only, and the 48 pages under `docs/` subdirectories for path references only (the sweep record says so); one stale scale figure proposed as a `launch:next` issue, not filed | none |
| D9 | `docs/upgrading.md`, `skills/sigma-doctor/scripts/migrate.py`, `docs/uninstall.md`, `tools/readiness/leftovers.py`, `tests/test_readiness_leftovers.py`, `tests/test_coexist.py`, `docs/launch/evidence/shared-sdlc-paths-control.md`, `docs/launch/evidence/shared-sdlc-paths-4c8562f.json`, `docs/launch/evidence/pin-rollback-2026-10-02.md` (a failed probe; no rollback demonstrated), `docs/launch/evidence/d9-upgrade-uninstall-rollback-f627d06ad3f6.md` with its three transcripts and `docs/launch/evidence/d9-results-f627d06ad3f6.json` (macOS scratch runs: migration on two repos, uninstall through `tools/readiness/leftovers.py` (the documented steps leave the plugin cache and local sdlc/* branches behind), rollback against a local rehearsal tag) | migration run on scratch repos by agents with a before and after; a recorded clean-uninstall run through `leftovers.py`; a rollback against a rehearsal tag now and against the real tag after the first push | creating the real tag is the owner's release action |
| D10 | `docs/threat-model.md`, `tests/test_threat_model.py`, `tools/readiness/exposure_scan.py`, `docs/launch/evidence/exposure-8aee0c74c526.md`, `docs/launch/evidence/exposure-disposition-8aee0c74c526.md`, `docs/launch/evidence/refs-8aee0c74c526.md`, `tools/readiness/egress_capture.py`, `docs/launch/evidence/egress-full-suite-a9747324eee1.json`, `docs/launch/write-surface.md`, `docs/launch/evidence/redteam-7997e29e4270.md`, `docs/launch/evidence/blast-radius-2026-10-04.json`, `tests/test_hostile_inputs.py`, `docs/launch/evidence/exposure-rescan-f627d06ad3f6.md` (history, tracked, refs and private-reference scans repeated on commit f627d06ad3f6 and on its exported public tree), `docs/launch/evidence/injection-drill-run-record-2026-10-12.md` (records that the drill was NOT run, and why) | the model-level injection drill itself has not run (`tools/readiness/injection_drill.py` exists; the run record names the blockers); a per-finding disposition of the 488 raw history findings | a throwaway repository for the drill, named with the tool's required `sigma-drill-` prefix, and an owner-supervised pick under an owner-set spend ceiling |
| D11 | `SECURITY.md`, `docs/release.md`, `docs/publish-runbook.md`, `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/bug_report.yml`, `.github/pull_request_template.md`, `.github/workflows/ci.yml`, `docs/launch/repo-settings.md`, `tools/readiness/verify_repo_settings.py` | one manual dispatch of `full` on the freeze commit, green on all five legs; the settings applied to the public repo with the verifier output committed (#398, open); triage of the 2026-10-11 Linux nightly failure | the release and the repository settings are the owner's actions |
| D12 | none: the market scan (S8) has not run | the dated matrix with evidence grades | none |
| D13 | `LICENSE` (MIT); `docs/launch/evidence/legal.md` (fact sheet, dependency licence table, dated name search, and owner decisions of 2026-10-12 recorded with accepted risk); `docs/launch/evidence/nominative-use-542a8bf.md`; `NOTICE`; `tests/test_third_party_imports.py` | the name decision: the name cells of `legal.md` read OPEN after the register search (owner has not decided), and the vendor-catalogue cell reads OPEN; D13 cannot be scored 3 while the name cells read OPEN | the name decision |

Owner-gated across the whole review: a throwaway repository for the drill (D10), the name decision
(D13), the OnChart statement (D6, D7), the owner actions of release and repository settings (D9,
D11), and a seed author (D1, not arranged). The trap author (D3) and the pilot spend ceiling are
spent history. The legal review no longer waits for counsel (see "Owner decisions of 2026-10-12"),
and the outside people (D7) are no longer needed.

## Reviewer rules

1. Work in the frozen clone produced by `tools/readiness/baseline.py snapshot`, never in the live checkout.
2. Use the high-risk list and sample from `docs/launch/review-units.json`.
3. Plant defects per `docs/launch/seeded-defects.md` when a seeded run is made. The D1 recall rule was AMENDED on 2026-10-06: recall was not measured, so no review is repeated or reported on a recall percentage.
4. A different model vendor reviews about 20% of the units and verifies every B-class finding (NOT met for launch: the owner accepted no second-vendor pass on 2026-10-06). On Codex, this doubles as the #302 live run.
5. Every finding carries a reproducer or a cited `file:line`, and a second independent pass verifies it before it is filed.
6. Before filing, re-check the finding against current `main`.
7. Before filing, run `python3 skills/sigma-scope/scripts/dedup.py .sdlc "<finding text>"`. A `duplicate` hit becomes a comment on that issue, not a new issue.
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
| S4 | high-risk code review + sample + verification | 25–45M | measured: all 16 Tier A units (36,519 lines), 24,845,157 tokens ($12.37) for reviewers and verifiers, 680.3 per line; cumulative 69,433,345 at the time the evidence was written (29,433,345 over the 40M checkpoint, inside the then owner-approved 75M cap (raised to 120M on 2026-10-06); slot overhead and governance of the evidence PR come on top); Tier B was not reviewed by owner decision ([review-s4-c3faf6f23e12.md](evidence/review-s4-c3faf6f23e12.md)); the 43.73M ceiling row below is the replaced estimate, kept for the Tier B part |
| S5 | phase-skill review | 8–15M | not measured |
| S6 | host runs, migration, drills, red team | 20–50M | not measured |
| S7 | outside first-run (#353; moved to `launch:next` on 2026-10-12, not run for launch) | 0 | not applicable |
| S8 | market scan | 3–6M | not measured |
| S9 | benchmark (own ceiling); the comparative run (#275) is past launch (2026-10-12); the measured-results document replaces it for condition 4 | set from the pilots | not measured: the benchmark pilot did not run |
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

The review ceiling is **120M processed tokens** for S1–S8 plus S10, with an owner checkpoint at **100M**.
The owner raised both on 2026-10-06 (from 75M and 40M, which the owner had chosen on 2026-10-03 in place of the epic's 150M ceiling and 75M checkpoint); the measured S4 cumulative at the time was 69,433,345.
The pilots' measured numbers are applied to both below (a step the pilots did not measure stays unmeasured), and S9 gets its own
owner-approved ceiling, which the benchmark pilot would have set and did not.

**What the (then) 40M checkpoint counts: decided.** The owner decided on 2026-10-04, in chat, that pilot spend counts toward
both counters, as this page had recommended. The counter is the running sum of processed tokens, as the hosts report
them, over S1–S8 plus S10: the same counter as the then 75M ceiling (now 120M), and S3 (the pilots) is part of it. The $75 pilot
ceiling stays the hard stop on pilot dollars. Pilot spend so far: 3,887,193 processed tokens and $2.50, so
**36,112,807 tokens remained under the then 40M checkpoint and 71,112,807 under the then 75M ceiling** (superseded on 2026-10-06; the current figures are in "Owner decisions of 2026-10-06" below).
The checkpoint is a stop-and-ask, not a failure.

**What the measured numbers imply for the caps.** S4 at its measured ceiling is 43.73M (33.64M without headroom).
Counted on top of the pilots, that is 47.6M (37.5M without headroom), so the then 40M checkpoint was reached inside S4 when
headroom is used, and is not reached at all when it is not (2.5M to spare). The full review did **not** fit under the then 75M on these
numbers: after the pilots and S4 at its ceiling, 27.38M remain, and the still-unmeasured steps S1, S2, S5, S6, S8 and S10
have lower bounds that sum to 33.5M (0.5 + 1 + 8 + 20 + 3 + 1), a shortfall of at least 6.1M before any of them reaches
its upper bound (their upper bounds sum to 75.5M). Without S4's headroom the lower bounds fit (37.5M remain), but the
upper bounds do not. Covering S4 in full, S1, S2, S5, S8 and S10 at their lower bounds costs 13.5M and leaves 13.9M for
S6, against its estimate of 20–50M: S6 (host runs, drills, red team) is the step that would be cut, and the cap rules
below say how. These statements rest on the unmeasured estimates for six steps; they are not a forecast.

OWNER DECISION (history, superseded 2026-10-06 by "Owner decisions of 2026-10-06" below; the cap and checkpoint are now 120M and 100M): approve the measured ceilings above as the review's S3 and S4 ceilings. Recommendation: at the time, approve them, keep 75M and 40M as set, and measure S5 and S6 in their own first runs before either is raised. S9 has no
measured basis until the benchmark pilot runs, and this page does not choose its ceiling.

**At 120M the review will cover less than the plan describes.** What the plan does when the ceiling binds:

1. High-risk units are reviewed in full first. Nothing else runs ahead of them.
2. The sample rate is reduced before any high-risk unit is cut.
3. Every unit not covered is listed by id in the scorecard evidence for D1 and D2, with the reason. A
   unit is never dropped silently, and a dimension whose coverage fell short does not score 3 on
   the strength of the part that ran.
4. At 100M the review stops and the owner decides whether to continue, cut scope, or raise the ceiling.
   Nobody raises it unrecorded.

## Owner decisions of 2026-10-06

Recorded here from the owner's decisions of 2026-10-06. They amend this page; where an older sentence above
quotes the earlier rule or figures, it is history and these decisions govern.

- **D1 recall rule AMENDED.** Planted-defect recall was not measured, so D1 claims "found and verified N
  defects", not a recall percentage. The earlier threshold (recall of at least 80%, a review below it
  re-run) no longer applies to D1. `tools/readiness/seed_defects.py` keeps its own exit code; that is a tool
  property, not a launch threshold.
- **Accepted coverage cuts.** The owner accepted this coverage and the review claims no more than it: Tier A only
  (16 of 16 Tier A units reviewed, see [the S4 record](evidence/review-s4-c3faf6f23e12.md)); mutation kill rates for 14 of 33
  modules (the rest timed out or did not run, see [the mechanical gates record](evidence/mechanical-gates-859290305d97.md));
  no Tier B sample; no second-vendor pass (reviewer rule 4 is not met, and nothing here claims it was).
- **Review token cap RAISED to 120M, checkpoint 100M.** The counter is the same one as before. S4 cumulative
  is 69,433,345, so **30,566,655 tokens remain under the 100M checkpoint and 50,566,655 under the 120M cap**
  for the unmeasured steps. The checkpoint is still a stop-and-ask, and nobody raises either figure unrecorded.

## Owner decisions of 2026-10-12

Each item below is an **owner decision, chat, 2026-10-12**, recorded with the owner's intent. They lower the written
bar in the cells listed under each one: **this lowers the written bar for those dimensions because the owner decided
the reasons stated, and the evidence for the weaker claim will say so.** Where the Dimensions table above and an older
sentence disagree, these decisions govern. No score is set by this page: scoring is a separate pull request, after the
evidence exists. `scorecard.json` has no notes field (`decide.py` refuses any key but `schema`, `benchmark_results`
and `dimensions`, and per dimension `id`, `name`, `gating`, `score`, `evidence`), so the weaker-claim notes below are
carried here and the scoring pull request cites this section in its evidence list.

1. **No outreach; D7 onboarding.** The owner decided that nobody outside or inside the team is asked to do a first-run
   test. The outsider first-run test (#353) is `launch:next`. D7 is scored on timed agent first runs, the list of
   every refusal message with its fix, and the OnChart statement: the owner states that existing team engineers have
   used the plugin since about 2026-10-07 and reported nothing alarming. That is an **owner statement, unverified**, and is
   recorded as such. The claim D7 can support is weaker: it is not a median first-run time measured on people new to
   Sigma, and the scorecard evidence must say so.
   - D7 measurement. BEFORE: "timed first run by agents and by 2–3 outside people; every refusal message listed". AFTER: "timed first runs by agents; every refusal message listed; the OnChart statement (owner statement, unverified); the outsider first-run test (#353) is `launch:next`".
   - D7 threshold. BEFORE: "median ≤ 30 min to first goal done; every refusal prints its fix". AFTER: "median ≤ 30 min to first goal done on the agent runs; every refusal prints its fix; the claim is weaker than \"outside people\" and any evidence says so".
2. **Comparative benchmark past launch; condition 4 and D3.** The owner moved the comparative benchmark (#275) past
   launch. Condition 4 of the rule, "the benchmark results file named in the scorecard", is satisfied for launch by the
   published measured-results document `docs/launch/evidence/measured-results.md` (produced by another pull request),
   which makes no comparative claim. INCONCLUSIVE and any comparative claim stay forbidden, as the pre-registration
   already says. `decide.py` checks only that the named file is under `docs/launch/evidence/`, non-empty and on main;
   it does not read it, so "no comparative claim" is a process rule recorded in `decision-rule.md`.
   - D3 threshold. BEFORE: "per `docs/bench/preregistration.md` as amended". AFTER: the same, "for launch the results file is the measured-results document `docs/launch/evidence/measured-results.md`, which makes no comparative claim (the comparative run, #275, is past launch)".
   - Execution step S9. BEFORE: "benchmark (own ceiling)". AFTER: the same, with the note that the comparative run is past launch.
3. **D13 on owner decisions, accepted risk.** The owner decided that D13 is scored on recorded owner decisions with
   the risk accepted; no counsel was consulted, and counsel's answers are not awaited. The name decision is **OPEN**:
   the owner has not yet decided after seeing the register-search result (`docs/launch/evidence/legal.md`, section 7.4).
   **D13 cannot be scored 3 while the name cells read OPEN.**
   - D13 measurement. BEFORE: "licences, provenance, name search". AFTER: "licences, provenance, name search, scored on recorded owner decisions with accepted risk (no counsel consulted)".
   - D13 threshold. BEFORE: "no unresolved conflict". AFTER: "every cell of `legal.md` reads OWNER DECISION or a sourced fact; D13 cannot be scored 3 while the name cells read OPEN".
   - D13 owner-gated cell. BEFORE: "the legal review (#343): the owner is getting counsel, and D13 is not scoreable until counsel answers". AFTER: "the name decision" (see the regenerated table).
   - Test change: `tests/test_review_plan.py` pinned the phrase "not scoreable until counsel answers" in the D13 row, in two places. It is replaced by a check for "cannot be scored 3 while the name cells read OPEN". This is the one test edit, and it follows this decision: the old phrase is false after it.
4. **#397 and #473 acceptance.** The owner accepted local rehearsal evidence (`docs/launch/evidence/rehearsal-f92a7528a26b.md`, which says it is not GitHub CI) in place of "CI green on each supported leg" on a throwaway repository. The first CI run on the real public repository, right after the first push, is the gate before any announcement. The replacement acceptance text is in the pull request description; the issues are not edited by this pull request.
5. **D1.** Mutation kill rates are recorded for the modules that finished within budget (14 of 33); absent modules are listed with the reason. The flake census is on Python 3.12 (macOS and Linux); the 3.10, 3.11 and 3.13 cells are covered by the nightly full matrix.
   - D1 measurement. BEFORE: "flake census (10 runs Linux + 10 macOS)". AFTER: "flake census (10 runs Linux + 10 macOS, Python 3.12)".
   - D1 threshold. BEFORE: "0 flaky tests; kill rate recorded per module". AFTER: "0 flaky tests on Python 3.12 (macOS and Linux; the 3.10, 3.11 and 3.13 cells are covered by the nightly full matrix, not by a census); kill rate recorded for the modules that finished within budget, absent modules listed with the reason".
6. **D6 Platform.** The Linux live-host run is past launch. Linux is covered by the nightly full matrix and Docker runs. The live runs are macOS (local-goals and github) plus the OnChart statement (owner statement, unverified). Codex and Cursor are not supported cells at launch: `definition.json` already lists them as `experimental`, so **no definition edit is needed or proposed**.
   - D6 measurement. BEFORE: "one live run + one CI leg per supported cell". AFTER: "one live run per macOS cell (local-goals, github) + the OnChart statement; one CI leg per supported cell, from the nightly full matrix; Linux covered by that matrix and Docker runs (the Linux live-host run is past launch)".
   - D6 threshold. BEFORE: "100% of supported cells". AFTER: "100% of supported cells have a CI leg in the nightly full matrix; the macOS cells have a live run; Codex and Cursor are experimental, not cells". The Linux cells therefore have no live-host run at launch, and the evidence says so.
7. **D9.** The owner states the predecessor has no users outside the company and internal employees reinstall (owner statement, unverified). "At least 3 more migrated repos" is replaced by what can be shown: migration verified on scratch repos by agent runs, a clean uninstall run, and the shared-path inventory. "A rollback that works" is split: before launch against a rehearsal tag, after launch against the real tag.
   - D9 measurement. BEFORE: "≥ 3 more migrated repos, shared-path inventory, rollback, clean uninstall". AFTER: "migration verified on scratch repos by agent runs, shared-path inventory, clean uninstall run, rollback (before launch against a rehearsal tag, after launch against the real tag)".
   - D9 threshold. BEFORE: "0 lost state; nothing left behind". AFTER: the same text; the count of migrated repos is dropped.
8. **D11.** "CI green on all legs" means the nightly full matrix (five legs) on the launch commit, proven by one manual dispatch of `full` on the freeze commit (the owner allowed that second run), because pull-request CI is one macOS leg.
   - D11 threshold. BEFORE: "SECURITY.md and runbook exist; CI green on all legs". AFTER: "SECURITY.md and runbook exist; \"CI green on all legs\" means the nightly full matrix (five legs) green on the launch commit, proven by one manual dispatch of `full` on the freeze commit".
9. **Stale tables regenerated.** "Where each dimension stands on main" and "Facts as of 2026-10-03" were a 2026-10-03 snapshot. Both were regenerated from main on 2026-10-12 ("Facts as of 2026-10-12" replaces the old heading). `tests/test_review_plan.py` forces the edit when a named file lands, and still passes.
10. **Review token record.** The counter stands at about 165M processed tokens including the red team, an owner-reported and gap-assessment figure rounded here, not a fresh sum. The owner raised the cap to **200M** on 2026-10-12 (it was 120M, with a 100M checkpoint). About 35M remain under 200M. The only steps left are S8 (market scan), S10 (synthesis) and a cheap S6; no new large review passes are run. The checkpoint stays a stop-and-ask and nobody raises the cap unrecorded.

## Facts as of 2026-10-12

Read on 2026-10-12 over REST (`gh api`) against main at `de188d1f`; this replaces the 2026-10-03 snapshot, which was stale. Issue states are volatile.

| Fact | State |
|---|---|
| definition | signed by `swapnil-agrim` on 2026-10-04 |
| open `launch:blocker` issues | 4: #343 (legal and naming), #397 and #473 (public-tree rehearsal and first push), #471 (owner decisions before the export). `tools/readiness/decide.py` reads the live count |
| #300 and #301 live runs, #337 flake census, #360 mutation measurement, #375 and #606 | closed |
| #275 comparative benchmark, #353 outsider first-run test, #715 gate-skip wording | open, `launch:next` |
| #351 blast-radius bookkeeping, #398 repository settings | open, not blockers; they are D10 and D11 pieces |
| scorecard | `docs/launch/scorecard.json`: D1, D2, D5, D8, D10 at 2, D9 and D11 at 1, D3, D6, D7 and D13 unscored |
| releases | tags `v1.0.0` to `v1.0.9` exist on the source repository, not on the public repository |
| CI | `.github/workflows/ci.yml`: PRs and pushes run one macOS leg; the full five-leg matrix (Linux 3.10 to 3.13, macOS 3.12) runs nightly and on demand |
| Windows | `.github/workflows/windows.yml` is `workflow_dispatch` only; Windows is experimental |

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
- The spend estimates are marked unmeasured, and the ceiling was cut from 150M to 75M (then raised to 120M on 2026-10-06) on the owner's
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
is not mangled: `gh api -X PATCH repos/OWNER/NAME/issues/307 -F body=@<file>`
(`-F`, not `-f`, because only `-F` reads the file). The line is
`Superseded plan: the executable plan is docs/launch/review-plan.md; the text below is kept for history.`
Verify with `gh api repos/OWNER/NAME/issues/307 --jq '.body | split("\n")[0]'`, and compare the
rest of the body with the copy read before the write.

## How to change this page

Any change to a dimension, a gating flag or a threshold is a pull request the owner merges, and it
changes `docs/launch/decision-rule.md` and `tools/readiness/decide.py` in the same pull request when it
touches which dimensions exist, their names or which gate. `tests/test_review_plan.py` fails when this
page, `scorecard.json` and the decision rule disagree. Run it with
`python -m pytest tests/test_review_plan.py -q`.

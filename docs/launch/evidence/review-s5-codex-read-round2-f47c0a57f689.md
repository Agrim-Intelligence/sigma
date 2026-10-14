# D2 independent read, round 2, by a second Codex run: commit f47c0a57f689 (2026-10-14)

Header note on the report text below: it is verbatim and unchanged (it contains no absolute home paths,
so no prefix rewriting was needed). Conventions follow the round-1 record,
[review-s5-codex-read-45626aa65b73.md](review-s5-codex-read-45626aa65b73.md).

## Reader and its limits

- Reader: Codex family, model id "gpt-6-astra", **self-reported** (its own report says "selected dispatch
  identifier ... runtime identity not independently verified"); high reasoning effort.
- A different family from the Claude skill authors, but **not isolated beyond what the report says**: it states it
  read only the 27 skill and reference Markdown files at the commit above and no code, tests, evals or other docs.
  Nothing outside the report's own account of its isolation was checked by us.
- **No refuting verifier** beyond its own second pass: of 12 candidates, 7 survive it and 5 are refuted by it. The
  triage below is the maintainer's verification against the skill text AND the code at f47c0a57f689; it is not an
  independent second reader.
- Round 1 and round 2 survivors are almost disjoint sets: only the review-finding follow-up/waiver theme overlaps
  (round 1 F04 / RV1 who may waive; round 2 F10 follow-up versus the project-rule gate). **So a single clean pass
  does not demonstrate 0 ambiguities**: each fresh read found a different set, and the count is not 0.

## Triage (verified against the skill text and the code at f47c0a57f689; no scoring, no scorecard change)

| Id | Verdict | Disposition |
| --- | --- | --- |
| F01 | REFUTED, agree | `sigma-loop/SKILL.md:111-112` parks on "a hard checkpoint / a decision only the user can make"; auto-progression does not remove Brainstorm's approval. |
| F02 | REFUTED, agree | `sigma-research/SKILL.md:40-41`: the full pass is required for a contract, boundary or shared-state change whatever the file count. |
| F03 | REFUTED, agree | `sigma-plan/SKILL.md:50`: the plan is an artifact on disk; the small lane shortens it, not removes it. |
| F04 | AGREE, wording | The text did say "required" and, elsewhere, waivable. Code: `phase_gate.WAIVABLE = ("research", "retro")`; `loop.py waive-phases` records a `phase_waived` row for anyone who calls it, with a mandatory `--reason`; it does not check who calls. `sigma-loop/SKILL.md` now says the phase is required unless the owner's recorded trivial-goal call waived it. "Only the owner" is a rule, not something the code authenticates. |
| F05 | AGREE, CODE GAP + wording | Confirmed in code: `work.done_refusal` returned None whenever no PR was on record, so with `work.enabled` on a goal that changed files could record `done` with no PR. Fixed: `done_refusal` now refuses when `work.enabled` is on, no PR is on record, and the goal's branch is ahead of `<remote>/<base>` or its worktree has uncommitted edits; the message names `work.py pr`, `record review`, and `record parked` as the lever. Fail-open on an unreadable branch or a missing worktree (the guard prevents losing a diff; it must not strand a goal). `landing.md` no longer says "docs-only". |
| F06 | AGREE, CODE GAP + wording | Confirmed in code: `loop.next_batch` read only `parallel.goals.enabled`; nothing coupled it to `work.enabled`, so with work off several goals shared one checkout. Fixed: `next_batch` clamps to one goal and prints why on stderr. Limit: only `next-batch` is clamped; a refill by `next --skip` is the orchestrator's own call and is not counted by code. `picking.md` and `running.md` say so. |
| F07 | AGREE, wording | Confirmed: `loop.py start-run` has no gate (anyone can call it, any time). `picking.md` now says to reset only when the user says so this session, never to continue past a stop. The code does not enforce this. |
| F08 | AGREE, wording | Retro runs before landing; a PR repair can change the code after the grade. The grade is appended as an event at each `record review` / `record done` call that passes `--retro-grade` (`loop.py` `_record_awaiting_merge` and `_record`), so a fresh grade can be recorded. `landing.md` repair step and `sigma-retro/SKILL.md` now say re-run on the final diff and pass that grade. The merge-reconcile pass records `done` with no grade, so the grade belongs on `record review`. |
| F09 | AGREE, wording | Code: `phase_gate.record_verdict` refuses a missing `--agent-id` only on the `subagent` route; `process` and `command` routes use the runner's receipt and need none. Both gestures (`sigma-plan-review/SKILL.md`, `running.md`) now show `[--agent-id <id>]` with that rule, matching `running.md:86-87`. |
| F10 | AGREE, wording | `sigma-review/SKILL.md` now: follow-up is a judgement call except for a project-rule violation, which blocks unless the owners waive it; the Blocking bullet lists it. |
| F11 | REFUTED, agree | `sigma-review/SKILL.md` Blocking bullet: "a risk review returned `critical`/`high`" is the explicit alternative to the failing case. |
| F12 | REFUTED, agree | `sigma-loop/SKILL.md:91`: "One subagent PER PHASE on a host with subagents, **tier `off` included**". |

Fixes shipped with this record: code for F05 and F06 (test-first, each clause seen red); wording for F04, F07,
F08, F09, F10 with a pin each in `tests/test_d2_cold_read_wording.py`. Budget ceilings are unchanged and
`evals/phase_context_budget.py` is clean; text added to a file was offset by trimming other text.

## Reader's report, verbatim

# Independent read report
Model: gpt-6-astra — selected dispatch identifier, high reasoning effort; runtime identity not independently verified
Date: 2026-10-14
Commit read: f47c0a57f689703bf6f6534d1f6e113022c259ea
Files read:

- skills/sigma-goal/SKILL.md
- skills/sigma-goal/references/selection.md
- skills/sigma-brainstorm/SKILL.md
- skills/sigma-brainstorm/references/selection.md
- skills/sigma-research/SKILL.md
- skills/sigma-research/references/selection.md
- skills/sigma-plan/SKILL.md
- skills/sigma-plan/references/selection.md
- skills/sigma-plan-review/SKILL.md
- skills/sigma-plan-review/references/selection.md
- skills/sigma-implement/SKILL.md
- skills/sigma-implement/references/selection.md
- skills/sigma-review/SKILL.md
- skills/sigma-review/references/selection.md
- skills/sigma-review/references/axes.md
- skills/sigma-verify/SKILL.md
- skills/sigma-verify/references/selection.md
- skills/sigma-retro/SKILL.md
- skills/sigma-retro/references/selection.md
- skills/sigma-loop/SKILL.md
- skills/sigma-loop/references/selection.md
- skills/sigma-loop/references/filing.md
- skills/sigma-loop/references/landing.md
- skills/sigma-loop/references/picking.md
- skills/sigma-loop/references/progress.md
- skills/sigma-loop/references/running.md
- skills/sigma-loop/references/stopping.md

## Findings

Severity describes the candidate gap before refutation. REFUTED rows are retained for auditability and are not surviving defects.

| Id | File:line | Quote | Wrong action an agent could take | Severity | Confidence | Second pass | Closing sentence (if refuted) |
|---|---|---|---|---|---|---|---|
| F01 | skills/sigma-brainstorm/SKILL.md:25; skills/sigma-goal/SKILL.md:49 | “instead of the loop auto-proceeding between dispatches” | Treat an autonomous loop invocation as approval of the design and begin implementation without the explicit design approval required by Brainstorm. | HIGH | UNSURE | **REFUTED.** The autonomous path explicitly parks at a hard checkpoint; Brainstorm labels its approval requirement a hard gate. General auto-progression does not remove that specific checkpoint. | skills/sigma-loop/SKILL.md:111–112: “Park instead of forcing through” on “a hard checkpoint / a decision only the user can make”. |
| F02 | skills/sigma-research/SKILL.md:38–40 | “a typo fix, a rename, or a one-file change does **not**” | Waive Research for a one-file change that modifies a public contract or shared state. | LOW | CONFIDENT | **REFUTED.** The next sentence explicitly requires the full pass for those risk categories, independently of file count. | skills/sigma-research/SKILL.md:40–41: “Run the full pass when the goal touches a contract, crosses a module boundary, changes shared state”. |
| F03 | skills/sigma-goal/SKILL.md:210 | “plan in a few lines rather than a document” | Keep a small-lane plan only in the conversation and dispatch Plan-Review without a saved plan artifact. | MEDIUM | CONFIDENT | **REFUTED.** The required Plan executor explicitly requires the artifact on disk, and its handoff still requires Plan-Review. The lane can shorten the artifact, but does not remove it. | skills/sigma-plan/SKILL.md:50: “The plan is an **artifact**, not a message.” |
| F04 | skills/sigma-loop/SKILL.md:176; skills/sigma-loop/references/running.md:89–90 | “research,retro --reason "<why>"` (recorded; nothing else waivable)” | Declare a goal trivial, record the waiver, and complete it without running the Retrospective phase that the main loop calls required. | HIGH | UNSURE | **SURVIVES.** The main skill explicitly distinguishes advisory output from a required phase, while the detailed procedure expressly permits waiving that phase. This may be an intended exception, but neither text reconciles the two promises or defines the triviality boundary for Retro. Small-lane guidance elsewhere still calls for a one-line retro. | — |
| F05 | skills/sigma-loop/references/landing.md:271–272 | “(a docs-only / no-diff goal, or `work.enabled` off) records `done` exactly as before.” | With work enabled, classify a goal containing documentation changes as docs-only and record it done without opening or merging a PR. | HIGH | CONFIDENT | **SURVIVES.** Landing lines 90–93 prescribe commit and PR when work is enabled, and lines 218–221 say done requires a merged PR. The later no-PR exception explicitly includes docs-only goals alongside genuinely no-diff goals; it does not restrict the exception to goals with no changed files. | — |
| F06 | skills/sigma-loop/references/picking.md:56–58; skills/sigma-loop/references/running.md:333 | “concurrently when `parallel.goals.enabled` allows more than one at a time” | Dispatch concurrent goal agents with work disabled and let both edit the same checkout, relying on Picking’s assurance that each goal gets its own worktree. | HIGH | CONFIDENT | **SURVIVES.** Picking lines 88–90 and 166–168 assume goal worktree isolation; Running makes worktree creation conditional and explicitly sends work-disabled goals into the checkout. The serial-execution restrictions in Running lines 342–349 apply to slices within a goal, not concurrent goal slots. No corpus sentence couples goal concurrency to enabled worktree isolation. | — |
| F07 | skills/sigma-loop/references/picking.md:16–18 | “resets your session's and the checkout's budget for a deliberate fresh push” | Reset the run budget mid-session without a new operator decision, then continue claiming goals that the original budget would have stopped. | HIGH | UNSURE | **SURVIVES.** BUDGET handling clearly stops claims, but the reset gesture expressly permits a mid-session fresh push without naming who authorizes it. The irreversible-action and spend-approval rules do not explicitly govern resetting iteration, elapsed-time, or token accounting. Reading “deliberate” as an agent decision therefore remains plausible. | — |
| F08 | skills/sigma-retro/SKILL.md:18; skills/sigma-loop/SKILL.md:176; skills/sigma-loop/references/landing.md:149–156 | “Runs at the end of a goal, **after Review**.” | Run Retro before opening the PR, subsequently change the implementation during a PR-review repair, and record the earlier intent-versus-shipped grade for the revised result. | MEDIUM | CONFIDENT | **SURVIVES.** The loop places Retro before landing; the later repair cycle repeats Implement, verify, commit, push, and review, but does not require refreshing Retro. Retro’s criterion-by-criterion grading requirement applies to the diff it examines, and no sentence invalidates that grade after subsequent edits. Its descriptions also say “After shipping” and “after a goal ships”. | — |
| F09 | skills/sigma-plan-review/SKILL.md:125–127; skills/sigma-loop/references/running.md:86–87,152–153 | `--plan-sha256 "$(awk '/^Plan sha256: /{print $3; exit}' /tmp/brief-<goal>.md)"` | Follow the complete recording gesture and invoke `record-plan-review` without supplying the reviewer’s agent ID, despite the requirement that independent verdict records carry that ID. | MEDIUM | CONFIDENT | **SURVIVES.** Running lines 86–87 explicitly include the reviewer’s own `--agent-id`; both the standalone skill’s complete gesture and Running’s later complete gesture omit it. Supplying an ID to `phase_report.py end` is a separate call and is not stated to populate the verdict-record call. Whether omission is rejected mechanically was outside this review. | — |
| F10 | skills/sigma-review/SKILL.md:178–185 | “Reporting a real finding as follow-up is a judgement you are allowed to make.” | Approve a change that violates a numbered architecture rule by classifying the violation as a Concern or non-probeable Missing and filing a follow-up without an owner waiver. | MEDIUM | UNSURE | **SURVIVES.** Project-rule conformance is called a gate at lines 130–138, and line 156 reserves waivers of every finding class to CODEOWNERS. The later follow-up permission contains no exception for mandatory project rules and forbids blocking a Missing without a constructible failing case. Deferral and waiver may be intended to differ, but the text does not explain how that distinction preserves a mandatory nonfunctional rule at landing. | — |
| F11 | skills/sigma-review/references/axes.md:63–64 | “A finding must carry a written failing case before it may block” | Downgrade a dedicated risk review’s high-severity finding because it lacks a concrete failing case. | LOW | CONFIDENT | **REFUTED.** The main skill’s blocking rule provides an explicit alternative for critical/high risk-review results. That specific alternative closes the general wording in the reference. | skills/sigma-review/SKILL.md:175–176: “or a risk review returned `critical`/`high`.” |
| F12 | skills/sigma-loop/references/progress.md:85–87 | “With phases inline (including when `model_selection` is off)” | Run all phases inline on a subagent-capable host because automatic model selection is disabled. | LOW | CONFIDENT | **REFUTED.** The main skill expressly requires phase dispatch with tier off. The Progress sentence can describe an otherwise permitted inline host without making off a dispatch exemption. | skills/sigma-loop/SKILL.md:91: “One subagent PER PHASE on a host with subagents, **tier `off` included**”. |

## Per-file summary

All 27 files, totaling 3,014 lines, were read in full. Counts use a reproducible **gate-marker line unit**, not distinct semantic gates: one physical line matching the following case-insensitive expression counts once, including repeated requirements, descriptions, and examples. Continuation lines are read with their surrounding instruction but do not add a unit unless they also match. Imperatives without these markers were also examined during the full read.

` \b(must|never|only|before|after|until|unless|required|require|requires|gate|gates|approval|approve|approved|waive|waiver|refuse|refuses|refused|refusal|skip|stop|park|parked|blocking|allowed|cannot)\b|do not|don.t `

The count is reproducible with `rg -i --count` using that expression without its surrounding spaces over the listed files. Findings counts assign each row once to its primary file and include refuted candidates.

| File | Gates examined (count) | Findings (count) | Notes |
|---|---:|---:|---|
| skills/sigma-goal/SKILL.md | 63 | 1 | F03 refuted. Also checked interactive approval, acceptance capture, phase dispatch, model refusal, completion, feature exclusivity, and lane routing. |
| skills/sigma-goal/references/selection.md | 3 | 0 | none found; checked interactive approval and requirement to read the main instructions. |
| skills/sigma-brainstorm/SKILL.md | 15 | 1 | F01 refuted. Checked explicit design approval, decomposition, alternatives, Research handoff, and acceptance capture. |
| skills/sigma-brainstorm/references/selection.md | 2 | 0 | none found; checked approved-design prerequisite and main-instruction handoff. |
| skills/sigma-research/SKILL.md | 34 | 1 | F02 refuted. Checked waiver boundaries, read-only restrictions, evidence, query preservation, scope ownership, unknowns, lane assignment, and recording. |
| skills/sigma-research/references/selection.md | 2 | 0 | none found; checked Research role, measured sizing, dossier consumers, and main-instruction handoff. |
| skills/sigma-plan/SKILL.md | 23 | 0 | none found; checked decomposition, test-first steps, commit ownership, proof selectors, disk artifact, and mandatory Plan-Review. Provides the closing rule for F03. |
| skills/sigma-plan/references/selection.md | 2 | 0 | none found; checked plan-before-edit requirement and main-instruction handoff. |
| skills/sigma-plan-review/SKILL.md | 40 | 1 | F09 survives. Checked reviewer resolution, brief checking, failure/inline ownership, claim verification, alignment, verdicts, hash binding, refinement handling, and re-review. |
| skills/sigma-plan-review/references/selection.md | 2 | 0 | none found; the non-trivial-plan trigger does not override the main skill’s required gate. |
| skills/sigma-implement/SKILL.md | 49 | 0 | none found; checked assertion-red/green, named TDD exceptions, approved-plan prerequisite, step verification, mutation controls, security checks, and completion evidence. |
| skills/sigma-implement/references/selection.md | 2 | 0 | none found; checked test-first execution and per-step verification. |
| skills/sigma-review/SKILL.md | 52 | 1 | F10 survives with uncertainty. Checked reviewer independence, brief checking, every-axis coverage, conditional reviews, verdict vocabulary, waiver ownership, blocking/follow-up, and fixed-claim verification. |
| skills/sigma-review/references/selection.md | 2 | 0 | none found; checked review-mode selection and main-instruction handoff. |
| skills/sigma-review/references/axes.md | 14 | 1 | F11 refuted. Checked all eight axes, conditional contract/migration triggers, machine-owned exclusions, failing cases, and pre-existing/live-path distinction. |
| skills/sigma-verify/SKILL.md | 16 | 0 | none found; checked fresh full execution, actual-output inspection, claim-specific proof, original acceptance authority, and prohibition on false completion. |
| skills/sigma-verify/references/selection.md | 3 | 0 | none found; checked evidence-before-claims and completion timing. |
| skills/sigma-retro/SKILL.md | 50 | 1 | F08 survives. Checked acceptance grading, missing-evidence behavior, advisory versus authorized writes, standing-document approval, KG opt-in, and archival constraints. |
| skills/sigma-retro/references/selection.md | 3 | 0 | none found independently; its after-shipping wording contributes to F08. |
| skills/sigma-loop/SKILL.md | 96 | 1 | F04 survives with uncertainty. Checked selection stops, precheck order, dispatch, no-phase-commits, irreversible actions, ownership, worktree boundaries, verification, review, merge outcomes, and stopping. |
| skills/sigma-loop/references/selection.md | 3 | 0 | none found; checked autonomous full-SDLC and verified-PR description against the detailed procedures. |
| skills/sigma-loop/references/filing.md | 24 | 0 | none found; checked blocker routing, explicit queue/assignee/block flags, triage, human-request exception, inherited unit ownership, and upstream-routing conditions. |
| skills/sigma-loop/references/landing.md | 167 | 1 | F05 survives. Checked blocking-call recovery, freshness, test-first exceptions, plan/hash binding, secret refusals, evidence generations, review repair, merge outcomes, reconciliation, and feature-completion ownership. Also contributes to F08. |
| skills/sigma-loop/references/picking.md | 91 | 2 | F06 survives; F07 survives with uncertainty. Checked budget reset/stop, dispatch and isolation assumptions, ownership, refill exclusions, claim release, dependency holds, feature scope, and precheck order. |
| skills/sigma-loop/references/progress.md | 33 | 1 | F12 refuted. Checked phase start/end attribution, model refusals, token evidence, banner limitations, recording destinations, and Retro timing. |
| skills/sigma-loop/references/running.md | 135 | 0 | none found independently of rows assigned above; supplies conflicting permissions/procedures for F04, F06, and F09. Checked model ceilings/escalation, phase enforcement, review evidence, fresh repair dispatch, acceptance, worktree refusal, spend approval, and slice isolation. |
| skills/sigma-loop/references/stopping.md | 19 | 0 | none found; checked optional gap-work prerequisites, budget boundary, human-only supervision, advisory decision tiers, live-slot completion, and fresh-session handoff. |
| **Total** | **945** | **12** | **7 surviving candidates, of which 3 are marked UNSURE; 5 refuted candidates.** |

## Cross-skill inconsistencies

- **Retro required versus waivable — F04.** `skills/sigma-loop/SKILL.md:176` calls the phase required; `skills/sigma-loop/references/running.md:89–90` permits its waiver. `skills/sigma-goal/SKILL.md:210–211` and Running lines 270–271 still prescribe a short retro for small work.
- **Merged completion versus documentation completion without a PR — F05.** Landing lines 218–221 define done as merged, while lines 271–272 expressly include docs-only work in the no-PR completion path.
- **Goal isolation assumed versus conditional — F06.** Picking lines 88–90 and 166–168 describe separate worktrees for goals, while Running lines 303–334 make them conditional and direct work-disabled goals into the shared checkout. The explicit serialization rule covers slices, not goal slots.
- **Retro’s subject and timing — F08.** Retro’s descriptions say after shipping, but the loop schedules it before landing and subsequent PR repair. The repair sequence does not refresh the grade or lessons.
- **Reviewer-attributed verdict recording — F09.** Running lines 86–87 specify `--agent-id` for independent verdicts; Plan-Review lines 125–127 and Running lines 152–153 omit it from their complete recording gestures.
- **Mandatory conformance, owner waiver, and reviewer deferral — F10.** Review calls project-rule conformance a gate, reserves waivers to owners, and separately allows follow-up disposition without explaining whether a mandatory nonfunctional rule may be deferred.
- **General failing-case rule versus risk-severity exception — F11, refuted.** The axes reference states an unconditional failing-case prerequisite; the main Review skill supplies the specific critical/high exception.
- **Inline wording versus tier-off dispatch — F12, refuted.** Progress associates inline examples with model selection off, but the main loop explicitly requires dispatch on capable hosts even when the tier is off.
- **Small plan versus saved artifact — F03, refuted.** Goal’s “rather than a document” wording is closed by Plan’s explicit disk-artifact requirement.

Different plan-review and code-review verdict vocabularies were examined. SOUND/SOUND-WITH-REFINEMENTS/FIX-FIRST and APPROVE/SEND-BACK/BLOCK are explicitly assigned to different recorders; that difference alone is not an inconsistency. Likewise, lowercase PR-posting verdicts belong to a separately described evidence/posting procedure.

## What I did not check

- I did not read implementation source, tests, evals, configuration, other project documentation, installed skills, prior reports, or evidence artifacts.
- I did not open linked documents outside the permitted corpus, including AGENTS.md, output contracts, branching contracts, threat models, conditional-review skills, and external sources.
- I did not execute any command prescribed by the Markdown. Tool use was limited to reading/enumerating the permitted files, counting their lines and marker matches, and reading Git HEAD.
- I did not modify the repository or create a report artifact.
- I did not establish how the actual helpers handle omitted reviewer IDs, conflicting settings, no-PR completion, budget resets, or retrospective waivers. Findings concern instructions a competent agent could follow, not demonstrated implementation exploits.
- I did not test enforcement under any host, feature-flag combination, failure condition, or end-to-end workflow.
- I did not independently verify the runtime model identity. The Model field records the identifier selected for this dispatch.

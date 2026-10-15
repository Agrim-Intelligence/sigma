# D2 independent read, round 4, by a fourth Codex run: commit 07035ea2877b (2026-10-15)

Header note on the report text below: it is verbatim and unchanged (it contains no absolute home paths, so no
prefix rewriting was needed). Conventions follow the round-3 record,
[review-s5-codex-read-round3-9065e4aa5076.md](review-s5-codex-read-round3-9065e4aa5076.md).

## Reader and its limits

- Reader: Codex family, "GPT-6", selected dispatch `gpt-6-astra`, high reasoning effort, **self-reported** (its report
  says the exact runtime identifier was not independently verified).
- It read the FINAL text, at `07035ea2877b` (after every round 1 to 3 fix and owner ruling): only the 27 skill and
  reference Markdown files. It states it ran no script, test, guard or workflow command, opened no code, tests,
  evals, config, prior reports or scorecards, and made no edits. It could not see the code or rulings behind a row.
- **No refuting verifier** beyond its own second pass: 6 rows, 1 survives it, 5 are refuted by it. The triage below
  is the maintainer's check against the skill text AND the code at `07035ea2877b`; it is not a second reader.

## Triage (verified against the skill text and the code; no scoring, no scorecard change, no threshold change)

| Id | Reader | Verdict | Disposition |
| --- | --- | --- | --- |
| F1 | MEDIUM, survives | AGREE, wording | `sigma-review` listed "anything a linter, type-checker or the test run already catches" as never-report on the premise that separate gates run, while `sigma-verify` takes the first of goal `verify_command`, config `verify.command`, else repo tests/build/lint (`state.declared_verify_commands`), and `verify_detect.py` only proposes a command it finds (pytest, a package script, a CI `run:` line) and never checks it covers the type-checker; required CI checks are the repo's own. So the premise did not hold throughout. Now: Review does not list what a gate will certainly stop, but a tool it ran in its quantitative pass that FAILS is reported with its number and approval is withheld while it fails unless the configured verification command demonstrably covers that same tool; `sigma-verify`'s "Lint / type clean" proof row is untouched. Pin in `tests/test_d2_cold_read_wording.py`, seen red against main's text. Words in `skills/sigma-review/SKILL.md` were trimmed to net -3; no ceiling raised. |
| F2 | LOW, refuted | REFUTED, agree | `skills/sigma-review/SKILL.md:178` on main: "except for a project-rule violation: that blocks unless the owners waive it." Exists. |
| F3 | HIGH, refuted | REFUTED, agree | `skills/sigma-loop/SKILL.md:111-112` on main: "**Park instead of forcing through** if you hit any of:" then "a hard checkpoint / a decision only the user can make,". Both exist. |
| F4 | HIGH, refuted | REFUTED, agree | `skills/sigma-loop/SKILL.md:91` on main: "One subagent PER PHASE on a host with subagents, **tier `off` included**". Exists. |
| F5 | LOW, refuted | REFUTED, agree | `skills/sigma-goal/SKILL.md:173-174` on main: "a goal that declares the unit in its body and has not been picked yet carries no label at all." Exists (it wraps across two lines). |
| F6 | LOW, refuted | REFUTED, agree | `skills/sigma-loop/references/landing.md:219-224` on main: the `REPAIR:` branch says to start the Implement phase, "fix the named check, run verify, commit and `work.py pr`, then re-run the fresh review and merge gestures." (it wraps across lines), and `skills/sigma-loop/SKILL.md:232` says to read landing.md before the first merge of a run. Both exist. |

Totals: 1 survives (F1, MEDIUM, wording, closed by this change), 5 refuted (F2 to F6), no HIGH. I disagree with
none of the reader's refutations. The reader's low-severity observations (axes.md omitting the project-rule
exception, goal's label-plus-body phrasing, loop's catch-all `record review`) are unchanged: each is closed by text
on the same page the reader quoted, and none was rated above LOW.

## Reader's report, verbatim

# Independent read report
Model: GPT-6 (selected dispatch: `gpt-6-astra`, high reasoning effort; exact runtime identifier not independently verified).
Date: 2026-10-15
Commit read: 07035ea2877bb05101d1c2f4cc0fbc04a0d2246e
Files read:

- `skills/sigma-goal/SKILL.md`
- `skills/sigma-goal/references/selection.md`
- `skills/sigma-brainstorm/SKILL.md`
- `skills/sigma-brainstorm/references/selection.md`
- `skills/sigma-research/SKILL.md`
- `skills/sigma-research/references/selection.md`
- `skills/sigma-plan/SKILL.md`
- `skills/sigma-plan/references/selection.md`
- `skills/sigma-plan-review/SKILL.md`
- `skills/sigma-plan-review/references/selection.md`
- `skills/sigma-implement/SKILL.md`
- `skills/sigma-implement/references/selection.md`
- `skills/sigma-review/SKILL.md`
- `skills/sigma-review/references/axes.md`
- `skills/sigma-review/references/selection.md`
- `skills/sigma-verify/SKILL.md`
- `skills/sigma-verify/references/selection.md`
- `skills/sigma-retro/SKILL.md`
- `skills/sigma-retro/references/selection.md`
- `skills/sigma-loop/SKILL.md`
- `skills/sigma-loop/references/filing.md`
- `skills/sigma-loop/references/landing.md`
- `skills/sigma-loop/references/picking.md`
- `skills/sigma-loop/references/progress.md`
- `skills/sigma-loop/references/running.md`
- `skills/sigma-loop/references/selection.md`
- `skills/sigma-loop/references/stopping.md`

## Findings

Severity describes the candidate wrong reading before the second pass. One candidate survives; five are refuted.

| Id | File:line | Quote | Wrong action an agent could take | Severity | Confidence | Second pass | Closing sentence (if refuted) |
|---|---|---|---|---|---|---|---|
| F1 | `skills/sigma-review/SKILL.md:163` | “Anything a linter, type-checker or the test run already catches — those gates run separately.” | Approve and land a change with a known type-check failure after excluding it from review findings, when the configured verification command and required CI checks cover only tests. | MEDIUM | CONFIDENT | **SURVIVES.** Lines 83–88 require running available tools and reporting their numbers, but do not require their results to pass before approval. `sigma-verify/SKILL.md:26–31` selects a proving command; it does not require that command to include every tool used by Review. Its proof table governs claims such as “type clean,” not whether approval must be withheld when that claim is false. `sigma-loop/references/landing.md:204–211` adds passing verification and required GitHub checks, which need not include the locally used type-checker. Thus the stated separate gate is not established throughout the allowed workflow. | — |
| F2 | `skills/sigma-review/references/axes.md:63` | “A general-review finding must carry a written failing case before it may block” | Relegate a demonstrated project-rule violation to a follow-up because it lacks a constructible failing case. | LOW | CONFIDENT | **REFUTED.** The reference omits the project-rule exception, but the governing verdict instructions state that exception expressly; the reference itself points to those instructions. | “except for a project-rule violation: that blocks unless the owners waive it.” — `skills/sigma-review/SKILL.md:178` |
| F3 | `skills/sigma-goal/SKILL.md:49–50` | “instead of the loop auto-proceeding between dispatches.” | Treat autonomous mode as permission to proceed from a newly drafted design to implementation without the explicit user approval required by Brainstorm. | HIGH | CONFIDENT | **REFUTED.** `sigma-brainstorm/SKILL.md:25–28` explicitly calls user approval a hard gate, and the loop expressly parks at hard checkpoints. Automatic progression does not waive that gate. | “**Park instead of forcing through** if you hit any of:” followed by “a hard checkpoint / a decision only the user can make,” — `skills/sigma-loop/SKILL.md:111–112` |
| F4 | `skills/sigma-loop/references/progress.md:85` | “phases inline (including when `model_selection` is off) that is this session” | Execute every phase inline on a host with subagents merely because model selection is off, losing the required separation between maker and checker. | HIGH | CONFIDENT | **REFUTED.** This conditional description can describe a legitimately inline host; it does not override the explicit dispatch requirement. `running.md:82–90` also includes off-tier phases in the recording and dispatch contract. | “One subagent PER PHASE on a host with subagents, **tier `off` included**” — `skills/sigma-loop/SKILL.md:91` |
| F5 | `skills/sigma-goal/SKILL.md:161–162` | “the `feature:<unit>` label and the two-line body marker together” | Exclude an otherwise valid body-declared goal from a feature-scoped run because its label has not yet been attached. | LOW | CONFIDENT | **REFUTED.** The same skill immediately directs the agent to use `features.read`, gives the body precedence, and explicitly explains the unlabeled-member case. The label-plus-body wording is inconsistent with that explanation, but does not survive the full reading. | “a goal that declares the unit in its body and has not been picked yet carries no label at all.” — `skills/sigma-goal/SKILL.md:173–174` |
| F6 | `skills/sigma-loop/SKILL.md:230` | “any other line → **`record review`**” | Record a `REPAIR:` merge result as awaiting merge instead of performing its required Implement, verification, push, and fresh-review cycle. | LOW | CONFIDENT | **REFUTED.** The summary's catch-all omits the repair outcomes, but line 232 requires reading the detailed landing procedure before the first merge. That procedure explicitly branches on `REPAIR:` and `RERUN:` rather than recording them as awaiting merge. | “fix the named check, run verify, commit and `work.py pr`, then re-run the fresh review and merge gestures.” — `skills/sigma-loop/references/landing.md:223–224` |

## Per-file summary

All 27 files were read in full: **3,001 physical lines**. A truncated tool response was repaired by rereading `filing.md:70–96`.

The counting unit below is an **anchored instruction cluster**, not a keyword occurrence or a distinct semantic gate. Each listed anchor starts a reviewed cluster; its span ends immediately before the next listed anchor, or at EOF. All operative clauses, exceptions, and outcome definitions within that span were examined together. Text preceding the first anchor was also read. Repeated instructions count again at their separate locations. The anchor lists make the **534 cluster** total reproducible without claiming there are 534 independently enforceable gates. All 27 anchor-list lengths were recounted; each list contains unique, ascending anchors.

Findings counts assign each candidate to its primary quoted location and include refuted candidates.

| File | Gates examined (count) | Findings (count) | Notes |
|---|---:|---:|---|
| `skills/sigma-goal/SKILL.md` | 35 | 2 | F3 and F5 refuted; no surviving findings. Checked approval ownership, phase dispatch, acceptance capture, recording, feature confinement, and lanes. Anchors: 9, 20, 25, 33, 47, 55, 60, 63, 73, 77, 80, 86, 98, 101, 119, 121, 126, 131, 145, 149, 157, 166, 171, 179, 183, 189, 195, 198, 202, 210, 212, 213, 217, 220, 222. |
| `skills/sigma-goal/references/selection.md` | 2 | 0 | None found. Checked interactive approval scope and obligation to read the main instructions. Anchors: 3, 6. |
| `skills/sigma-brainstorm/SKILL.md` | 12 | 0 | None found. Checked explicit design approval, decomposition, alternatives, Research handoff, waiver dependency, and P1 acceptance. Its hard gate closes F3. Anchors: 8, 16, 20, 25, 31, 34, 36, 38, 39, 40, 44, 49. |
| `skills/sigma-brainstorm/references/selection.md` | 2 | 0 | None found. Checked approved-design-before-code scope and main-instruction requirement. Anchors: 3, 5. |
| `skills/sigma-research/SKILL.md` | 33 | 0 | None found. Checked the explicit trivial-goal waiver, read-only exceptions, exhaustive radius and query recording, risk handoff, scope ownership, unknowns, north-star outcomes, lane recording, and evidence rules. Anchors: 9, 19, 31, 34, 38, 45, 49, 53, 58, 63, 69, 73, 80, 86, 89, 93, 96, 106, 113, 115, 117, 121, 126, 128, 134, 166, 171, 174, 180, 184, 186, 188, 190. |
| `skills/sigma-research/references/selection.md` | 2 | 0 | None found. Checked phase identity, dossier responsibility, and main-instruction requirement. Anchors: 3, 6. |
| `skills/sigma-plan/SKILL.md` | 16 | 0 | None found. Checked planning before code, decomposition, test cycles, sole commit ownership, test selectors, disk artifact, branch copy, and mandatory Plan-Review. Small-lane brevity does not waive the disk artifact. Anchors: 12, 19, 22, 26, 31, 34, 42, 44, 46, 50, 54, 57, 62, 65, 66, 71. |
| `skills/sigma-plan/references/selection.md` | 2 | 0 | None found. Checked plan-before-edit scope and main-instruction requirement. Anchors: 3, 5. |
| `skills/sigma-plan-review/SKILL.md` | 42 | 0 | None found. Checked resolver ownership, fresh-review input, failed-review handling, owner-authorized inline fallback, brief checking, forensic review, north-star exceptions, verdict vocabulary, exact-plan hashing, recorder ownership, refinements, dispositions, and escalation. The unreachable-north-star path is expressly documented, not an implied waiver. Anchors: 8, 18, 25, 27, 30, 33, 35, 37, 40, 44, 47, 57, 65, 71, 76, 77, 78, 79, 82, 86, 94, 96, 102, 104, 105, 111, 117, 128, 130, 133, 136, 141, 148, 152, 154, 156, 159, 164, 169, 174, 178, 184. |
| `skills/sigma-plan-review/references/selection.md` | 2 | 0 | None found. Checked invocation triggers against the mandatory Plan-Review instructions. The non-trivial-plan trigger does not authorize skipping other plans. Anchors: 3, 5. |
| `skills/sigma-implement/SKILL.md` | 32 | 0 | None found. Checked prior approval, assertion-red/green proof, named TDD exceptions, verification of each step, parking, step-model reporting, clause-specific mutations, and security checks. The no-tests exception expressly leaves other gates intact. Anchors: 9, 19, 26, 34, 38, 47, 48, 54, 60, 63, 71, 72, 73, 74, 78, 80, 82, 88, 90, 93, 96, 106, 108, 116, 118, 127, 140, 146, 159, 162, 170, 178. |
| `skills/sigma-implement/references/selection.md` | 2 | 0 | None found. Checked test-first execution and per-step verification scope. Anchors: 3, 6. |
| `skills/sigma-review/SKILL.md` | 48 | 1 | F1 survives. Checked reviewer separation and fallback, brief checking, blast radius, companion restrictions, tool results, all axes, project-rule ownership, dedicated risk passes, verdict mappings, waivers, and blocking versus follow-up classification. Explicit project-rule and risk exceptions close the broader failing-case reading in F2. Anchors: 9, 19, 26, 29, 32, 34, 36, 39, 43, 46, 56, 64, 68, 72, 75, 76, 77, 79, 83, 88, 91, 95, 99, 102, 104, 108, 112, 115, 120, 123, 127, 130, 137, 142, 147, 153, 156, 161, 163, 164, 165, 166, 172, 174, 178, 180, 183, 188. |
| `skills/sigma-review/references/axes.md` | 19 | 1 | F2 refuted; no surviving findings. Checked all eight axes, both conditional axes, implement/review responsibilities, automated-tool exclusions, reporting thresholds, and newly activated pre-existing defects. Anchors: 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 51, 52, 53, 54, 55, 56, 62, 69. |
| `skills/sigma-review/references/selection.md` | 2 | 0 | None found. Checked mode selection and requirement to read the complete instructions; its abbreviated scan description does not replace the eight-axis requirement. Anchors: 3, 6. |
| `skills/sigma-verify/SKILL.md` | 15 | 0 | None found in this file's own conditional claim-proof rules. Checked freshness, command selection, full execution, output reading, red/green bug proof, subagent verification, frozen acceptance, and completion refusal. The command-selection scope contributes to F1's cross-skill gap. Anchors: 13, 21, 26, 28, 29, 30, 31, 36, 37, 38, 39, 40, 41, 44, 49. |
| `skills/sigma-verify/references/selection.md` | 2 | 0 | None found. Checked evidence-before-success wording and invocation timing. Anchors: 3, 6. |
| `skills/sigma-retro/SKILL.md` | 42 | 0 | None found. Checked independent grading, authoritative acceptance, missing-input behavior, criterion-by-criterion grades, recorder ownership, standing-document approval, KG opt-in, fail-open note writing, and rerun after behavior-changing PR repairs. A partial grade does not authorize completion against unmet acceptance. Anchors: 9, 18, 30, 36, 41, 42, 44, 52, 54, 56, 58, 62, 63, 65, 66, 69, 72, 73, 74, 76, 82, 84, 87, 90, 93, 99, 104, 110, 114, 115, 117, 118, 121, 129, 139, 142, 146, 148, 152, 155, 161, 169. |
| `skills/sigma-retro/references/selection.md` | 2 | 0 | None found. Checked advisory outputs, human approval for standing changes, and phase timing against the main instructions. Anchors: 3, 6. |
| `skills/sigma-loop/SKILL.md` | 40 | 1 | F6 refuted; no surviving findings. Checked session ownership, claiming, dispatch, terminal outcomes, pre-check order, model ceilings, fresh reviewers, no phase commits, hard checkpoints, worktree ownership, slices, notes, retro waiver ownership, verification, secret handling, PR review, merge outcomes, and stopping. Anchors: 9, 20, 30, 36, 41, 49, 54, 59, 62, 73, 76, 79, 90, 96, 104, 109, 111, 118, 123, 137, 146, 153, 163, 171, 174, 176, 180, 186, 196, 202, 209, 214, 215, 216, 224, 226, 234, 240, 248, 251. |
| `skills/sigma-loop/references/filing.md` | 14 | 0 | None found. Checked blocker handoff before parking, mandatory filing flags, triage authority, queue/block relationships, label ownership, unit inheritance, explicit target units, and upstream routing conditions. Anchors: 10, 20, 29, 34, 37, 44, 46, 50, 52, 54, 57, 60, 65, 77. |
| `skills/sigma-loop/references/landing.md` | 50 | 0 | None found. Checked blocking-call recovery, verification freshness, test-first proof exceptions, exact-plan approval, secret exceptions owned by humans, generation-bound reviews, repair and unblock paths, ambiguous-post recovery, CI outcomes, merge-only completion, reconciliation, cleanup, and human ownership of feature integration. Its explicit repair branch closes F6. Anchors: 5, 17, 27, 32, 35, 50, 59, 69, 77, 88, 92, 97, 103, 109, 118, 122, 131, 145, 146, 163, 170, 177, 182, 188, 204, 213, 218, 219, 229, 234, 241, 249, 250, 251, 254, 256, 261, 270, 276, 284, 295, 304, 308, 311, 313, 327, 329, 344, 353, 358. |
| `skills/sigma-loop/references/picking.md` | 30 | 0 | None found. Checked budget-reset authority, PID/task identity, goal-slot dispatch, parent verification of child outcomes, refill exclusions, undispatched-claim release, no-subagent handling, prerequisite holds, feature confinement, and ordered pre-check outcomes. Anchors: 15, 35, 54, 60, 79, 100, 108, 115, 128, 133, 138, 156, 159, 167, 171, 178, 182, 190, 194, 208, 214, 232, 234, 239, 244, 247, 250, 257, 262, 264. |
| `skills/sigma-loop/references/progress.md` | 20 | 1 | F4 refuted; no surviving findings. Checked phase-note ownership, start/end identity, unavailable measurements, model mismatch behavior, step dispatch reporting, file logs, retro resolution, and final grade recording. Anchors: 8, 10, 16, 23, 28, 38, 42, 46, 50, 58, 62, 74, 77, 80, 92, 99, 102, 111, 113, 121. |
| `skills/sigma-loop/references/running.md` | 59 | 0 | None found. Checked tier resolution and escalation, off-tier dispatch, phase ordering, acceptance capture, maker/checker separation, verdict ownership, exact-plan approval, PR evidence generations, fresh fixes and rereviews, host refusal, no phase commits, lane changes, spend authority, worktree ownership, and slice isolation. Anchors: 5, 18, 21, 31, 38, 41, 48, 51, 52, 63, 65, 68, 73, 76, 82, 84, 86, 89, 91, 97, 107, 109, 114, 124, 137, 144, 155, 157, 165, 179, 182, 184, 189, 210, 218, 229, 232, 236, 250, 253, 263, 266, 273, 277, 280, 281, 286, 289, 303, 315, 320, 329, 336, 346, 349, 355, 359, 361, 364. |
| `skills/sigma-loop/references/selection.md` | 2 | 0 | None found. Checked autonomous scope, phase/reviewer separation, verified landing, and stop conditions against the full instructions. Anchors: 3, 6. |
| `skills/sigma-loop/references/stopping.md` | 7 | 0 | None found. Checked optional KG gap work, budget restriction, human-decision parking, non-authoritative decision tiers, finishing active slots, and fresh-session handoff. Anchors: 10, 18, 23, 28, 30, 39, 47. |

## Cross-skill inconsistencies

- **F1 — survives:** `sigma-review/SKILL.md:163` excludes failures on the premise that separate automated gates cover them, while `sigma-verify/SKILL.md:26–31` permits a narrower configured proving command. The landing contract checks that proof and required CI checks; it does not establish that every tool used in Review has a passing gate.
- **F2 — refuted:** `sigma-review/references/axes.md:63` states a general failing-case prerequisite without the project-rule exception expressly present in `sigma-review/SKILL.md:172–179`. The main instructions settle the blocking decision.
- **F3 — refuted:** The general autonomous-progression description in `sigma-goal/SKILL.md:48–50` could appear to conflict with Brainstorm's explicit user approval. `sigma-loop/SKILL.md:111–112` preserves hard checkpoints, so the approval remains binding.
- **F4 — refuted:** `sigma-loop/references/progress.md:84–87` associates inline phases with model selection being off and dispatched phases with auto selection. `sigma-loop/SKILL.md:90–91` and `running.md:82–90` expressly require dispatch on capable hosts with the off tier included.
- **F5 — refuted:** `sigma-goal/SKILL.md:161–162` describes the declaration pair as label plus body, whereas `sigma-loop/references/picking.md:201` names the body's `Feature:`/`Branch:` pair. The Goal skill's own `features.read` instruction and unlabeled-member explanation settle eligibility.
- **F6 — refuted:** `sigma-loop/SKILL.md:230` routes every unlisted merge outcome to `record review`; `landing.md:219–233` defines `REPAIR:` and `RERUN:` as active repair/wait instructions. The mandatory reading of the detailed procedure closes the catch-all interpretation.

No additional unresolved conflicts were found in verdict vocabulary, phase names, or recorder ownership. Plan-review's `SOUND` family and code-review's `APPROVE` family serve different gates; PR verdict mapping and the special prior-block requirement for `unblock` are explicit.

## What I did not check

- I did not inspect implementation source, tests, evals, configuration, prior reports, evidence packages, scorecards, installed skills, or documentation outside the 27 permitted Markdown files.
- I did not open `AGENTS.md`, linked standalone documents, `AUTOWATCH.md`, `SLACK_COMMANDS.md`, or the dedicated security, contract, and migration skill files.
- I did not run project scripts, workflow commands, tests, guards, mutation controls, or any command prescribed by the reviewed Markdown.
- I did not verify whether the implementation actually enforces the documented refusals, hashes, reviewer isolation, ownership checks, phase records, or merge conditions.
- I did not reproduce F1 against a live adopting repository. It is a surviving instruction-level reading, not an observed bypass of Sigma's implementation.
- I did not independently verify the dispatch's exact runtime model identity.
- I made no edits and created no report artifact.

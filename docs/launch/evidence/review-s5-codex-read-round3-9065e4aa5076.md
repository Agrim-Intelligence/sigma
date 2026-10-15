# D2 independent read, round 3, by a third Codex run: commit 9065e4aa5076 (2026-10-15)

Header note on the report text below: it is verbatim and unchanged (it contains no absolute home paths, so no
prefix rewriting was needed). Conventions follow the round-2 record,
[review-s5-codex-read-round2-f47c0a57f689.md](review-s5-codex-read-round2-f47c0a57f689.md).

## Reader and its limits

- Reader: Codex family, model id "gpt-6-astra", **self-reported** (its report says "selected dispatch identifier
  ... runtime identity not independently verified"); high reasoning effort.
- It states it read only the 27 skill and reference Markdown files at the commit above, and no code, tests, evals,
  prior reports or other docs. It could therefore not see the owner rulings or code behaviour behind several rows.
- **No refuting verifier** beyond its own second pass: 14 rows, 9 survive it, 5 are refuted by it. The triage below
  is the maintainer's verification against the skill text AND the code at 9065e4aa5076; it is not a second reader.

## Triage (verified against the skill text and the code; no scoring, no scorecard change, no threshold change)

| Id | Reader | Verdict | Disposition |
| --- | --- | --- | --- |
| F01 | HIGH, survives | NOT NEW: the owner's existing round-1 F03 ruling | `sigma-plan-review` judging correctness alone when the north-star is `unreachable` is the behaviour the owner ruled on 2026-10-14 ("keep + visible notice", merged in PR #1385): `record-plan-review` records `alignment: not-judged (<why>)` and session start and doctor announce it. The reader did not read the code or the ruling. No change here. |
| F02 | HIGH, survives | NEW HIGH, **OPEN OWNER DECISION** | `sigma-review/SKILL.md` ~137-138: when project-rule conformance could not be judged, the review says so, but nothing holds the overall approval (the rule violation is blocking at ~175-181 only when found). Same class as F01. **Awaiting the owner; behaviour and wording are deliberately NOT changed here.** Options: (A) keep the behaviour and record the not-judged state visibly, like the F03 ruling; (B) hold approval (park) when conformance could not be judged. |
| F03 | MEDIUM, survives | AGREE, wording | Real contradiction: ~175-176 make a risk review's `critical`/`high` blocking, ~181-183 and `references/axes.md:63` required a failing case to block. Now stated once in `sigma-review/SKILL.md` and echoed in `axes.md`: the failing-case rule binds the general review; a dedicated risk review's `critical`/`high` blocks without one. |
| F04 | MEDIUM, survives | AGREE, wording | Code: `state.declared_verify_commands` reads goal frontmatter `verify_command`, else config `verify.command` (with an acceptance record the repo command and the recorded command both run); nothing reads `project.md` (its template says "Not read from here"). `sigma-verify`, `landing.md` and `running.md` now name that order. The old round-1 pin `test_verify_prefers_the_projects_verify_command_over_a_weaker_check` asserted the stale wording and is updated. |
| F05 | MEDIUM, survives | AGREE, wording | `sigma-retro` requires the KG corpus note when `knowledge_graph.enabled`; `sigma-loop/SKILL.md` and `references/progress.md` now say "audit-trail notes and the KG corpus note when `knowledge_graph.enabled`". |
| F06 | HIGH, refuted | REFUTED, agree | `sigma-loop/SKILL.md:91`: "One subagent PER PHASE on a host with subagents, tier `off` included". |
| F07 | MEDIUM, survives | **OPEN, not changed; listed for the owner** | `landing.md` ~184-189 treats a reconcile that still returns `PARK: remote comment outcome ambiguous` as proof the comment never reached GitHub, then publishes a new generation. Code: `reconcile_review_post` returns that line whenever the marker lookup finds nothing, which is a lookup miss, not proof of non-delivery. Safe locally (superseded evidence stops resolving via `_current_review_generation`; the new generation has its own evidence id and marker) but NOT safe remotely: the merge gate reads the PR's `sigma:` comments and the LATEST marker wins in chronological order, so a late-arriving comment from the abandoned generation (a lost acknowledgement that surfaces after the lookup) could outrank the new one, e.g. a stale `approve` over a newer `block`. Nothing in the code detects it. Needs an owner call (for example: require a wait or a second lookup before abandoning, or park instead of re-posting). |
| F08 | MEDIUM, survives | AGREE, wording | Code: `work.py` refusal text itself says to add the exact path to `work.allow_secret_paths`, and nothing authenticates who edits `.sdlc/config.json`. `landing.md` now says a human adds the path, the agent never adds or edits it (or any secret exception) itself, parks instead, and that config edits are not authenticated. |
| F09 | MEDIUM, survives | AGREE, wording | Code: `post-review` and `record-subagent-review` take `approve`/`block`/`unblock`; `record-review` takes APPROVE/SEND-BACK/BLOCK; `unblock` is a pass like `approve` and the code does not check that a prior block exists. `sigma-review/SKILL.md` now maps APPROVE to `approve`, SEND-BACK or BLOCK to `block`, and says the reviewer returns `unblock` only when clearing a prior loop block, never inferred from APPROVE. The missing prior-block check is NOT a wording item and is not changed. |
| F10 | HIGH, refuted | REFUTED, agree | `sigma-research/SKILL.md:40-41` requires the full pass for a contract, boundary or shared-state change. |
| F11 | MEDIUM, refuted | REFUTED, agree | `sigma-plan/SKILL.md:50`: the plan is an artifact on disk. |
| F12 | HIGH, refuted | REFUTED, agree | `sigma-loop/SKILL.md:183`: the grade never substitutes for verified `done_when`. |
| F13 | MEDIUM, refuted | REFUTED, agree | `sigma-review/SKILL.md:181`: follow-up classification is the reviewer's judgement; waiver is the owners'. |
| F14 | MEDIUM, survives | AGREE, wording | `running.md` ceiling branch now says record it `failed` (`parked` only for a human decision), matching `sigma-loop/SKILL.md` and `running.md` step 6. |

Totals: 9 survive (F01 to F05, F07 to F09, F14), 5 refuted (F06, F10 to F13). Of the 9: F01 is the existing owner
ruling (not new), F02 is a new HIGH awaiting the owner, F07 is held for the owner, and six (F03, F04, F05, F08, F09,
F14) are wording fixed in this change with a pin each in `tests/test_d2_cold_read_wording.py`, each seen red against
the previous text. Budget ceilings are unchanged and `evals/phase_context_budget.py` is clean; words added to a file
were offset by trimming other words in the same file.

## Reader's report, verbatim

# Independent read report
Model: gpt-6-astra — selected dispatch identifier, high reasoning effort; runtime identity not independently verified.
Date: 2026-10-15
Commit read: 9065e4aa50760c2af3adcdefb8a2dd41c562448d
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
- skills/sigma-loop/references/picking.md
- skills/sigma-loop/references/running.md
- skills/sigma-loop/references/filing.md
- skills/sigma-loop/references/progress.md
- skills/sigma-loop/references/landing.md
- skills/sigma-loop/references/stopping.md

## Findings

| Id | File:line | Quote | Wrong action an agent could take | Severity | Confidence | Second pass | Closing sentence (if refuted) |
|---|---|---|---|---|---|---|---|
| F01 | skills/sigma-plan-review/SKILL.md:105–107 | “then judge correctness alone.” | Issue SOUND and authorize implementation while the project’s existing north-star is unreachable and strategic compliance remains unchecked. | HIGH | CONFIDENT | SURVIVES — lines 102–103 make north-star conflicts FIX-FIRST, but the unreachable branch explicitly permits correctness-only judgment; recording “not-judged” does not require a blocking verdict. | — |
| F02 | skills/sigma-review/SKILL.md:137–138 | “states that project-rule conformance was not judged” | Approve a change for landing despite being unable to check the project rules that would otherwise block it. | HIGH | CONFIDENT | SURVIVES — lines 175–181 make project-rule violations blocking, but no sentence requires holding an overall approval when conformance could not be judged. Disclosure closes the false-PASS claim, not the missing gate. | — |
| F03 | skills/sigma-review/SKILL.md:181–183 | “Reporting one you could not write a failing case for, as blocking, is the failure this section exists to prevent.” | Downgrade a dedicated risk review’s high-severity finding to follow-up because the general reviewer cannot construct its failing case. | MEDIUM | UNSURE — the explicit high-risk exception may be understood to prevail, but the subsequent prohibition is unqualified. | SURVIVES — lines 175–176 separately make a risk review’s critical/high verdict blocking, while `references/axes.md:63` repeats the unconditional failing-case prerequisite; the corpus gives conflicting instructions rather than a consistent exception. | — |
| F04 | skills/sigma-verify/SKILL.md:26–27 | “`.sdlc/project.md` → **Verify command** if set, else the repo's tests/build/lint.” | Run the project-document command and present it as the required completion proof when the goal’s configured proving command requires a different check. | MEDIUM | UNSURE — these sources might agree in a particular installation, but the corpus does not establish that they must. | SURVIVES — `sigma-loop/references/landing.md:60–61` instead gives goal frontmatter `verify_command`, then `verify.command`; `running.md:76–77` requires the configured command, without reconciling the standalone Verify skill’s different precedence. | — |
| F05 | skills/sigma-loop/SKILL.md:178–179 | “**write only the audit-trail notes**” | Omit the KG corpus note during an autonomous retrospective even though the project enabled the knowledge graph. | MEDIUM | CONFIDENT | SURVIVES — `sigma-retro/SKILL.md:169–171` explicitly permits and requires both audit-trail and enabled KG notes, whereas this restriction and `sigma-loop/references/progress.md:119–120` permit only audit-trail notes. The missing-note warning in Retro does not resolve the contradictory permission. | — |
| F06 | skills/sigma-loop/references/progress.md:85 | “phases inline (including when `model_selection` is off)” | Run phases inline on a host with subagents solely because model selection is off. | HIGH | UNSURE | REFUTED — the phrase describes an inline case; it need not make `off` sufficient to enter that case, and the main instruction explicitly excludes that interpretation. | `skills/sigma-loop/SKILL.md:91`: “One subagent PER PHASE on a host with subagents, **tier `off` included**”. |
| F07 | skills/sigma-loop/references/landing.md:184–189 | “If reconcile still returns `PARK: remote comment outcome ambiguous` — the comment genuinely never reached GitHub” | Publish and post a new review generation while the previous generation’s remote posting outcome remains unknown, potentially duplicating the review action. | MEDIUM | CONFIDENT | SURVIVES — lines 179–182 and `running.md:183–185` prohibit reposting after ambiguity, but this branch treats continued ambiguity as proof of non-delivery and supplies a new-generation route around the prohibition. No intervening positive non-delivery check is specified. | — |
| F08 | skills/sigma-loop/references/landing.md:116–117 | “the answer is the EXACT path in `work.allow_secret_paths`” | Add a path exception personally after a secret-shaped-file refusal, treating a claimed fixture or intentional certificate as sufficient authorization to bypass that refusal. | MEDIUM | UNSURE — the corpus does not establish whether path exceptions intentionally belong to the agent, unlike content exceptions. | SURVIVES — lines 120–122 explicitly reserve content exceptions to the operator, but neither this paragraph nor another permitted file assigns an approver or evidentiary requirement to a new path exception. “Never `git add -f`” closes a different bypass. | — |
| F09 | skills/sigma-review/SKILL.md:151 | “**APPROVE**, **SEND-BACK** or **BLOCK**” | Translate a reviewer’s APPROVE into a same-revision `unblock` evidence result without obtaining the explicit reviewer-issued `unblock` required by the landing procedure. | MEDIUM | UNSURE — a caller could supply the necessary extra protocol instruction, but the invoked Review skill does not define it. | SURVIVES — `sigma-loop/references/landing.md:131` invokes this skill for PR review, while lines 165–168 and `running.md:179–180` require the reviewer to return `unblock`; no shared verdict mapping or PR-mode override appears in the Review skill. | — |
| F10 | skills/sigma-research/SKILL.md:38–40 | “a typo fix, a rename, or a one-file change does **not**” | Waive Research for a one-file change that modifies a public contract or shared state. | HIGH | UNSURE | REFUTED — the immediately following instruction explicitly requires the full pass for these risks, overriding the simple file-count example. | `skills/sigma-research/SKILL.md:40–41`: “Run the full pass when the goal touches a contract, crosses a module boundary, changes shared state”. |
| F11 | skills/sigma-goal/SKILL.md:210 | “plan in a few lines rather than a document” | Keep a small goal’s plan only in conversation and hand no saved plan to its independent reviewer. | MEDIUM | UNSURE | REFUTED — the delegated Plan skill expressly requires the disk artifact, and short length does not prevent saving it. | `skills/sigma-plan/SKILL.md:50`: “The plan is an **artifact**, not a message.” |
| F12 | skills/sigma-retro/SKILL.md:173–174 | “Retro never breaks a run.” | Record the goal done after Retro identifies unmet acceptance criteria and assigns partial. | HIGH | UNSURE | REFUTED — advisory retrospective output does not waive the separate acceptance requirement for completion. | `skills/sigma-loop/SKILL.md:183`: “The grade never substitutes for verified `done_when`.” |
| F13 | skills/sigma-review/SKILL.md:156 | “Only the owners named in the repository's CODEOWNERS may waive a finding, of any class” | Treat every nonblocking follow-up classification as requiring owner approval and park an otherwise landable change. | MEDIUM | UNSURE | REFUTED — the same skill explicitly gives the reviewer follow-up classification authority, distinguishing classification from waiver and retaining owner authority over project-rule violations. | `skills/sigma-review/SKILL.md:181`: “Reporting a real finding as follow-up is a judgement you may make”. |
| F14 | skills/sigma-loop/references/running.md:229–232 | “park or fail it rather than cycling indefinitely.” | Record an unresolved implementation failure as parked without identifying any decision that requires a human. | MEDIUM | CONFIDENT | SURVIVES — `running.md:287–288` and `sigma-loop/SKILL.md:115–116` require unresolved failures to be recorded as failed and reserve parked for human decisions, but this ceiling branch explicitly authorizes either outcome. | — |

## Per-file summary

All 27 files were read in full: 3,010 lines. Truncated output spans were re-read.

**Counting unit:** one line-anchored **gate-review bundle**: a contiguous instruction span whose gate conditions, obligations, exceptions and ownership statements were examined together. The anchors below delimit these bundles; each ends immediately before the next anchor, or at EOF. Introductory material before the first anchor was also read. A bundle can contain several conjunctive requirements, and repeated requirements in different locations count separately. Consequently, **555 bundles does not mean 555 distinct semantic gates**, and the count is not a keyword count. The anchor lists make the reported unit reproducible.

“Findings” counts rows originating in that file, including refuted rows; supporting cross-file citations are not counted again.

| File | Gates examined (count) | Findings (count) | Notes |
|---|---:|---:|---|
| skills/sigma-goal/SKILL.md | 32 | 1 | F11 refuted. Checked interactive approvals, phase execution, acceptance, recording, feature confinement and lane routing. Anchors: 9, 20, 25, 33, 47, 55, 63, 73, 77, 86, 98, 121, 126, 131, 141, 149, 159, 161, 163, 166, 171, 179, 183, 189, 195, 198, 210, 212, 213, 217, 220, 222. |
| skills/sigma-goal/references/selection.md | 1 | 0 | none found; checked approval trigger and requirement to read the main instructions. Anchor: 6. |
| skills/sigma-brainstorm/SKILL.md | 11 | 0 | none found; checked explicit design approval, decomposition, alternatives, handoff and acceptance capture. Anchors: 8, 20, 25, 31, 34, 36, 38, 39, 40, 44, 49. |
| skills/sigma-brainstorm/references/selection.md | 1 | 0 | none found; checked Goal-phase trigger and read-main requirement. Anchor: 5. |
| skills/sigma-research/SKILL.md | 32 | 1 | F10 refuted. Checked waiver conditions, read-only boundary, scope ownership, evidence, risk routing, unknowns, lane sizing and dossier placement. Anchors: 9, 19, 31, 34, 38, 45, 49, 53, 58, 63, 69, 73, 80, 86, 89, 93, 96, 106, 113, 115, 117, 121, 126, 128, 134, 166, 171, 180, 184, 186, 188, 190. |
| skills/sigma-research/references/selection.md | 1 | 0 | none found; checked phase numbering, output consumers and read-main requirement. Anchor: 6. |
| skills/sigma-plan/SKILL.md | 17 | 0 | none found; checked task decomposition, no phase commits, test selectors, saved plan and mandatory Plan-Review. Anchors: 12, 15, 19, 22, 26, 31, 34, 35, 42, 44, 46, 50, 54, 57, 65, 66, 71. |
| skills/sigma-plan/references/selection.md | 1 | 0 | none found; checked before-edit trigger and read-main requirement. Anchor: 5. |
| skills/sigma-plan-review/SKILL.md | 41 | 1 | F01 survives. Checked reviewer resolution, failed-review handling, brief validation, forensic and adversarial checks, alignment, verdict ownership, hashes, refinements and send-backs. Anchors: 8, 18, 25, 27, 30, 33, 35, 37, 40, 44, 47, 57, 65, 71, 76, 77, 78, 79, 82, 86, 96, 102, 104, 105, 111, 117, 128, 130, 133, 136, 141, 148, 152, 154, 156, 159, 164, 169, 174, 178, 184. |
| skills/sigma-plan-review/references/selection.md | 1 | 0 | none found; checked “non-trivial plan” trigger against the unconditional main-skill and orchestrator requirements. Anchor: 5. |
| skills/sigma-implement/SKILL.md | 30 | 0 | none found; checked approved-plan prerequisite, red/green proof, permitted TDD exceptions, per-step verification, mutation requirements, security checks and parking. Anchors: 9, 19, 26, 34, 38, 47, 48, 54, 60, 63, 71, 72, 73, 74, 78, 80, 82, 88, 90, 93, 96, 106, 118, 127, 140, 146, 159, 162, 170, 178. |
| skills/sigma-implement/references/selection.md | 1 | 0 | none found; checked test-first phase description and read-main requirement. Anchor: 6. |
| skills/sigma-review/SKILL.md | 45 | 4 | F02, F03 and F09 survive; F13 refuted. Checked independence, brief validation, complete axis coverage, rule/risk gates, verdicts, waivers, exclusions, follow-ups and re-verification. Anchors: 9, 19, 22, 26, 29, 32, 34, 36, 39, 43, 46, 56, 64, 68, 72, 75, 77, 79, 83, 88, 91, 99, 102, 105, 109, 113, 117, 123, 126, 131, 139, 145, 150, 156, 159, 164, 166, 167, 168, 169, 175, 177, 181, 185, 191. |
| skills/sigma-review/references/selection.md | 1 | 0 | none found; checked review-mode selection and read-main requirement. Anchor: 6. |
| skills/sigma-review/references/axes.md | 14 | 0 | No separate finding; the unconditional blocking prerequisite at line 63 supports F03. Checked eight axes, two conditional surfaces, machine-owned checks, implement/review allocation and reporting exclusions. Anchors: 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 49, 62, 69. |
| skills/sigma-verify/SKILL.md | 16 | 1 | F04 survives. Checked command selection, freshness, actual output, claim-specific proof, original acceptance and machine-recorded evidence. Anchors: 13, 16, 21, 26, 28, 29, 30, 32, 37, 38, 39, 40, 41, 42, 44, 50. |
| skills/sigma-verify/references/selection.md | 1 | 0 | none found; checked completion trigger, evidence requirement and read-main requirement. Anchor: 6. |
| skills/sigma-retro/SKILL.md | 43 | 1 | F12 refuted; KG write authorization also checked against F05. Checked independence, acceptance grading, evidence gaps, proposal ownership, enabled KG writes, refresh ownership and standing-document protection. Anchors: 9, 18, 22, 27, 31, 36, 41, 42, 44, 52, 54, 56, 58, 62, 63, 65, 66, 69, 73, 76, 82, 84, 87, 90, 93, 99, 104, 110, 114, 115, 117, 118, 121, 129, 134, 139, 142, 146, 148, 152, 155, 161, 169. |
| skills/sigma-retro/references/selection.md | 1 | 0 | none found; checked advisory scope, approval ownership and read-main requirement. Anchor: 6. |
| skills/sigma-loop/SKILL.md | 53 | 1 | F05 survives. Checked session/claim ownership, dispatch, terminal responses, prechecks, mandatory phases, independence, expenditure approval, worktree isolation, review cycles, merge outcomes and stop reporting. Anchors: 9, 20, 30, 31, 36, 41, 49, 54, 59, 62, 73, 76, 79, 90, 96, 104, 109, 112, 113, 115, 118, 123, 126, 130, 133, 137, 140, 142, 146, 153, 155, 163, 171, 174, 176, 180, 186, 196, 202, 204, 206, 209, 214, 215, 216, 221, 224, 226, 228, 234, 240, 248, 251. |
| skills/sigma-loop/references/selection.md | 1 | 0 | none found; checked autonomous scope, independent review, verified landing and read-main requirement. Anchor: 6. |
| skills/sigma-loop/references/picking.md | 50 | 0 | none found; checked budget-reset authority, session identity, batch/refill discipline, fresh child proof, claim release, dependency and feature filters, and precheck ordering/exemptions. Anchors: 15, 19, 35, 54, 60, 70, 79, 81, 82, 89, 92, 95, 100, 104, 108, 112, 115, 128, 133, 138, 141, 156, 159, 171, 178, 182, 190, 194, 197, 199, 201, 204, 205, 208, 214, 217, 224, 227, 232, 234, 239, 244, 247, 250, 255, 257, 262, 264, 267, 270. |
| skills/sigma-loop/references/running.md | 47 | 1 | F14 survives. Checked model ceilings/escalation, phase identity, acceptance, independent review, verdict writers, evidence generations, fresh repairs, expenditure approval, worktree startup and slice isolation. Anchors: 5, 18, 21, 32, 41, 52, 65, 68, 70, 73, 82, 84, 86, 88, 89, 91, 107, 109, 114, 124, 144, 165, 189, 210, 218, 229, 233, 240, 251, 267, 274, 278, 281, 282, 287, 290, 294, 304, 316, 321, 330, 337, 347, 350, 360, 362, 365. |
| skills/sigma-loop/references/filing.md | 20 | 0 | none found; checked owner handoffs, mandatory routing flags, triage, actionable authority, blocker consistency, inherited units and upstream classification. Anchors: 10, 20, 26, 29, 34, 37, 39, 44, 46, 48, 50, 51, 54, 57, 60, 65, 77, 84, 90, 94. |
| skills/sigma-loop/references/progress.md | 33 | 1 | F06 refuted; lines 119–120 also repeat F05. Checked truthful measurement, start/end identity, observed models, inline exceptions, step attribution, audit records, retrospective and grade recording. Anchors: 8, 10, 16, 17, 23, 27, 28, 31, 38, 39, 42, 50, 53, 57, 58, 62, 64, 69, 72, 74, 77, 80, 84, 90, 92, 95, 99, 102, 111, 113, 119, 121, 126. |
| skills/sigma-loop/references/landing.md | 53 | 2 | F07 and F08 survive. Checked blocking-call outcomes, fresh proof, TDD exceptions, plan bytes, secret refusals, PR review evidence, repair cycles, ambiguous outcomes, merge policies, reconciliation and human-owned feature completion. Anchors: 5, 17, 27, 32, 35, 50, 59, 71, 79, 90, 94, 99, 105, 111, 120, 124, 132, 146, 147, 148, 165, 172, 179, 184, 191, 207, 216, 221, 222, 232, 237, 244, 252, 253, 254, 257, 259, 264, 273, 279, 287, 298, 307, 311, 314, 316, 330, 332, 334, 347, 356, 361, 364. |
| skills/sigma-loop/references/stopping.md | 8 | 0 | none found; checked optional KG gap work, budget boundary, stop reporting, advisory decision tiers, live-slot completion and new-session continuation. Anchors: 8, 10, 18, 23, 28, 30, 39, 47. |
| **Total** | **555 bundles** | **14 rows** | **9 SURVIVES; 5 REFUTED. Counts are coverage units and reported candidates, not independently verified runtime defects.** |

## Cross-skill inconsistencies

- **Verification-command precedence — F04.** `sigma-verify/SKILL.md:26–27` selects the project-document command, then repository checks; `sigma-loop/references/landing.md:60–61` selects goal frontmatter, then configuration. `running.md:76–77` requires the configured command. No common precedence is stated.
- **Autonomous retrospective writes — F05.** `sigma-loop/SKILL.md:178–179` and `references/progress.md:119–120` restrict writes to audit-trail notes; `sigma-retro/SKILL.md:169–171` also requires the enabled KG corpus note.
- **Review verdict vocabulary — F09.** The Review executor requires APPROVE/SEND-BACK/BLOCK at `sigma-review/SKILL.md:151`; its PR-review caller requires approve/block/unblock, including an explicit reviewer-issued unblock at `sigma-loop/references/landing.md:165–168`. The discrepancy is semantic for unblock, not merely capitalization.
- **Failure-state ownership — F14.** `sigma-loop/references/running.md:229–232` permits parking or failing an unresolved ceiling-tier fix, while `sigma-loop/SKILL.md:115–116` and `running.md:287–288` reserve failed for unresolved failures and parked for human decisions.
- **Blocking prerequisites within the Review skill package — F03.** `sigma-review/SKILL.md:175–176` makes dedicated critical/high risk findings blocking, but lines 181–183 and `references/axes.md:63` prohibit blocking without a written failing case.
- **Ambiguous review-post recovery within the Loop skill package — F07.** `references/running.md:183–185` and `landing.md:179–182` prohibit another post after an ambiguous outcome; `landing.md:184–189` authorizes another generation and post while reconciliation still reports ambiguity.
- The apparent small-plan artifact conflict, inline-when-off exception, and partial-retro completion exception were refuted as F11, F06 and F12 respectively. They are not counted as surviving inconsistencies.

## What I did not check

- I did not read repository instructions from disk, installed skills, prior reports, evidence directories, scorecards, implementation source, tests, evals, configuration, or documents outside the listed corpus.
- I did not follow external links or inspect the other skills referenced by these files, including the dedicated security, contract and migration review executors.
- I did not execute any project script, prescribed workflow command, test, review, gate, hook, or failure control. File reads, permitted-file enumeration/counting and the initial read-only Git HEAD check were the only repository operations.
- I did not verify whether command signatures, exit statuses, hashes, review bindings, default settings, runtime identity checks, model mappings, locks, budget controls or recovery mechanisms behave as the prose claims.
- I did not assess a live goal, PR, acceptance record, CODEOWNERS assignment, actual secret exception, or configured verification command. Findings about their interactions are conditional readings of the instructions.
- I did not independently verify the selected model’s runtime identity or inspect host transcript metadata.
- I did not modify repository files or create a report artifact.

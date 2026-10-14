# D2 independent read by a second model family: Codex, commit 45626aa65b73 (2026-10-14)

Header note on the report text below: it is verbatim except for the path prefixes. The leak scan
rejects absolute home paths, so `/Users/<name>/Documents/sigma/sigma` is replaced by `<checkout>`
and `/Users/<name>/Downloads` by `<downloads>` in prose. In Markdown link TARGETS only, `<checkout>/` is
written `../../../` (so the link check resolves; the two links to files outside the repo are unlinked) and a trailing `:line` is dropped from the target (the
visible link text keeps it). Nothing else changed.

## Reader and its limits

- Reader: Codex family, self-reported "GPT-6"; the exact model id was not exposed to it.
- NOT an isolated clone. The reader also saw PROMPT.md, AGENTS.md, `docs/output-contract.md` and
  git metadata, and ran a rebase of a local branch in the owner's checkout before reading.
- No second independent model. The "second pass" on each row is the reader's own: 7 findings
  survive it, 9 are refuted by it. No separate verifier refuted anything.
- It read the 27 skill/reference Markdown files only, no code, tests or prior reports.
- 6 survivors are new against the earlier same-family cold read (F01, F02, F03, F05, F06, F07).
  F04 is the known RV1 (who may waive a review finding).

## Triage (verified against the skill text at 45626aa65b73; no scoring, no scorecard change)

| Id | Verdict | Disposition |
| --- | --- | --- |
| F01 | AGREE | Reworded. `sigma-brainstorm/SKILL.md` "Hand off" said "The terminal step is **`sigma-plan`**" while `sigma-goal/SKILL.md` runs Goal -> Research -> Plan. Now hands off to `sigma-research`; Plan directly only under Research's recorded waiver. |
| F05 | AGREE | Reworded. `sigma-verify` "Goal done" row now says `done_when` is the P1 acceptance record (the recorded criteria if the issue changed since), matching `sigma-retro` ("authoritative even if the issue changed"). `sigma-loop` step 6 gains "The grade never substitutes for verified `done_when`", so `--retro-grade partial` does not read as satisfying done. |
| F06 | AGREE | Reworded. `sigma-implement` mutation control now says: for a guard with several clauses break each clause on its own; for an ordering check weaken the order to plain membership (AGENTS.md's own rule, and the #1156 lesson). |
| F07 | AGREE | Reworded. `sigma-loop/references/running.md` fallback now pipes the fetched text to `predict.py resolve - .sdlc "$goal"`. Verified: `skills/sigma-model/scripts/predict.py:896-906` accepts `-` (stdin) and requires the goal id as the 4th argument. |
| F02 | OWNER DECISION, wording unchanged | See the code facts below. |
| F03 | OWNER DECISION, wording unchanged | `sigma-plan-review/SKILL.md:109-110`: an `unreachable` north-star means say alignment was not judged, "then judge correctness alone". A FIX-FIRST exists only for a plan that actually fights the north-star (`:105-106`); there is no blocking verdict for "could not look". `sigma-review/SKILL.md:134-138` has the same shape for project rules. Whether losing that gate is intended is the owner's call. |
| F04 | OWNER DECISION (RV1), wording unchanged | `sigma-review/SKILL.md:156` "a finding can be waived with a stated reason" names no waiver authority, against the blocking rule at `:176-178`. |

### F02: what the code does today (file:line, read this session, not run end to end)

- `skills/sigma-loop/scripts/phase_gate.py:270` stamps a verdict's `route` from `reviewer.resolve(...)` at
  RECORD time, not from what produced the verdict. `work.py:1355` does the same for
  `reviewer_route` in the plan-review record (its docstring at `work.py:1317-1321` says so).
- `phase_gate.py:276` is the only per-route requirement: route `subagent` needs `--agent-id`. A
  `process` or `command` route needs nothing. The gate (`:152-163`) approves any approving verdict;
  it only adds a note for route `inline`.
- `reviewer.py:105-113` returns `inline` only when `review.independent` is false, `review.host` is
  `inline`/unknown, or no mechanism exists. With `review.independent` on and a `process` resolved, a
  failed process followed by an inline self-review is recorded as route `process`, approving, with no
  agent id required. The "stamp the provenance" in the skill is therefore prose in the verdict, not
  something the record or gate checks.
- On a `subagent` route the record is refused without an agent id (`:276`), so an inline fallback there
  cannot be recorded honestly at all; it would need an id it does not have.
- Answer to the open question: no, a disclosed fallback does not satisfy independence in any machine
  sense; the gate cannot tell it from an independent review. Whether the fallback should exist at all,
  be recorded as `inline`, or refuse, is the owner's decision.

## Refuted rows F08-F16

Refuted by the reader's own second pass. I agree with all nine: each closing sentence quotes text that
is present and does the work claimed (F08 `sigma-review/SKILL.md:145-146`; F09 `sigma-loop/SKILL.md:105`;
F10 plan-review `:79`; F11 `sigma-research/SKILL.md:38-42`, the full-pass sentence overrides the
"one-file" clause; F12 `sigma-loop/SKILL.md:112-114`; F13 `picking.md:184`; F14 `sigma-retro/SKILL.md:169-173`;
F15 `phase_gate.py:276` refuses a missing id on the subagent route; F16 `landing.md:227-241`).
F11 and F15 are the two closest calls.

## Reader's report, verbatim

# Independent read report

Model: GPT-6; the exact deployment/model identifier is not exposed to this session, so I cannot honestly supply a more specific name.
Date: 2026-10-14
Commit read: 45626aa65b73d42efb7714546b779df1114c77cf
Files read:

- [skills/sigma-goal/SKILL.md](../../../skills/sigma-goal/SKILL.md)
- [skills/sigma-goal/references/selection.md](../../../skills/sigma-goal/references/selection.md)
- [skills/sigma-brainstorm/SKILL.md](../../../skills/sigma-brainstorm/SKILL.md)
- [skills/sigma-brainstorm/references/selection.md](../../../skills/sigma-brainstorm/references/selection.md)
- [skills/sigma-research/SKILL.md](../../../skills/sigma-research/SKILL.md)
- [skills/sigma-research/references/selection.md](../../../skills/sigma-research/references/selection.md)
- [skills/sigma-plan/SKILL.md](../../../skills/sigma-plan/SKILL.md)
- [skills/sigma-plan/references/selection.md](../../../skills/sigma-plan/references/selection.md)
- [skills/sigma-plan-review/SKILL.md](../../../skills/sigma-plan-review/SKILL.md)
- [skills/sigma-plan-review/references/selection.md](../../../skills/sigma-plan-review/references/selection.md)
- [skills/sigma-implement/SKILL.md](../../../skills/sigma-implement/SKILL.md)
- [skills/sigma-implement/references/selection.md](../../../skills/sigma-implement/references/selection.md)
- [skills/sigma-review/SKILL.md](../../../skills/sigma-review/SKILL.md)
- [skills/sigma-review/references/axes.md](../../../skills/sigma-review/references/axes.md)
- [skills/sigma-review/references/selection.md](../../../skills/sigma-review/references/selection.md)
- [skills/sigma-verify/SKILL.md](../../../skills/sigma-verify/SKILL.md)
- [skills/sigma-verify/references/selection.md](../../../skills/sigma-verify/references/selection.md)
- [skills/sigma-retro/SKILL.md](../../../skills/sigma-retro/SKILL.md)
- [skills/sigma-retro/references/selection.md](../../../skills/sigma-retro/references/selection.md)
- [skills/sigma-loop/SKILL.md](../../../skills/sigma-loop/SKILL.md)
- [skills/sigma-loop/references/filing.md](../../../skills/sigma-loop/references/filing.md)
- [skills/sigma-loop/references/landing.md](../../../skills/sigma-loop/references/landing.md)
- [skills/sigma-loop/references/picking.md](../../../skills/sigma-loop/references/picking.md)
- [skills/sigma-loop/references/progress.md](../../../skills/sigma-loop/references/progress.md)
- [skills/sigma-loop/references/running.md](../../../skills/sigma-loop/references/running.md)
- [skills/sigma-loop/references/selection.md](../../../skills/sigma-loop/references/selection.md)
- [skills/sigma-loop/references/stopping.md](../../../skills/sigma-loop/references/stopping.md)

## Findings

Seven findings survive; nine candidate findings are refuted. Severity describes the candidate reading; REFUTED rows are retained, not asserted as remaining defects. Quotes preserve the source wording and join wrapped lines with spaces; each quoted excerpt is at most 25 words. These are instruction readings, not claims that an implementation exploit has been demonstrated.

| Id | File:line | Quote | Wrong action an agent could take | Severity | Confidence | Second pass | Closing sentence (if refuted) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F01 | [skills/sigma-brainstorm/SKILL.md:44](../../../skills/sigma-brainstorm/SKILL.md) | “The terminal step is **`sigma-plan`** (write the implementation plan).” | Hand a nontrivial, multi-module goal directly from Brainstorm to Plan without running Research or recording its permitted waiver. | HIGH | CONFIDENT | SURVIVES — Goal's sequence puts Research before Plan ([sigma-goal/SKILL.md:56–59](../../../skills/sigma-goal/SKILL.md)), and Research describes itself as that intervening phase ([sigma-research/SKILL.md:26](../../../skills/sigma-research/SKILL.md)), but Brainstorm's own terminal instruction names Plan without a trivial-goal restriction. The two instructions specify different handoffs. | — |
| F02 | [skills/sigma-plan-review/SKILL.md:41](../../../skills/sigma-plan-review/SKILL.md); [skills/sigma-review/SKILL.md:40](../../../skills/sigma-review/SKILL.md) | “Fall back to inline, stamp the provenance” | After the resolved independent process fails, have the author supply the approving plan or code review inline without a new resolution or operator authorization. | HIGH | CONFIDENT | SURVIVES — Both skills initially prohibit author review, then expressly allow this failure fallback. Loop says only a resolver result of `inline` permits inline review ([sigma-loop/SKILL.md:101–102](../../../skills/sigma-loop/SKILL.md); [references/running.md:140–145](../../../skills/sigma-loop/references/running.md)), and the PR procedure attributes that route to an operator choice ([references/landing.md:175–179](../../../skills/sigma-loop/references/landing.md)). Disclosure does not establish that choice or change the previously resolved route. | — |
| F03 | [skills/sigma-plan-review/SKILL.md:109–110](../../../skills/sigma-plan-review/SKILL.md) | “then judge correctness alone.” | Return SOUND for a vision-first plan while its north-star is unreachable, allowing implementation before strategic or architectural conformance has been judged. | HIGH | UNSURE | SURVIVES — An actual contradiction with the north-star requires FIX-FIRST (`:105–106`), but the unavailable-input route has no corresponding blocking verdict. Code Review likewise permits an unjudged project-rule check with disclosure ([sigma-review/SKILL.md:134–138](../../../skills/sigma-review/SKILL.md)). This is an explicit fallback, so whether the intended safety promise permits that lost gate remains uncertain. | — |
| F04 | [skills/sigma-review/SKILL.md:156](../../../skills/sigma-review/SKILL.md) | “Rules are lessons, not laws — a finding can be waived with a stated reason.” | Waive a blocking Bug or high-risk finding on the reviewer's own stated reason and return APPROVE without obtaining a human waiver. | HIGH | CONFIDENT | SURVIVES — `:176–178` makes a Bug, qualifying Missing, or critical/high risk review blocking, and `:194` sends a real bug back to Implement. Neither provision restricts the quoted waiver to nonblocking findings or identifies who can grant it. The apparent exception and mandatory blocking rule therefore remain in conflict. | — |
| F05 | [skills/sigma-loop/SKILL.md:182–183](../../../skills/sigma-loop/SKILL.md); [skills/sigma-retro/SKILL.md:69–74](../../../skills/sigma-retro/SKILL.md) | “done --retro-grade” | After an issue's criteria have been narrowed, verify only its current `done_when` and record `done --retro-grade partial` despite an unmet original P1 criterion. | HIGH | UNSURE | SURVIVES — Retro makes the recorded original acceptance authoritative even if the issue changed (`:36–39`) and reserves achieved for every criterion being achieved (`:69–74`), yet the terminal gesture explicitly accepts partial/diverged alongside done. Verify requires the goal's `done_when` ([sigma-verify/SKILL.md:42,50](../../../skills/sigma-verify/SKILL.md)) without expressly identifying the immutable P1 record as its source. An interpretation that uses the original criteria throughout would prevent the action; the prose does not unambiguously require that interpretation at completion. | — |
| F06 | [skills/sigma-implement/SKILL.md:129](../../../skills/sigma-implement/SKILL.md) | “specific line the fix depends on, re-run the relevant test, confirm it now fails” | Delete an entire compound conditional, observe one failing test, and treat that as proof that each individual clause of the guard is covered. | MEDIUM | CONFIDENT | SURVIVES — `:141–150` requires targeting the real guard and a live red/green control, which rules out a nearby redundant mutation or a mental exercise. It does not require separating clauses or weakening an ordering predicate while retaining membership, so the stated procedure can be satisfied by a weaker whole-line mutation. | — |
| F07 | [skills/sigma-loop/references/running.md:37](../../../skills/sigma-loop/references/running.md) | “and pass that text as the argument, never” | Insert fetched GitHub issue text containing `$(...)` into a double-quoted shell argument for model resolution instead of piping it through stdin. | MEDIUM | CONFIDENT | SURVIVES — Goal explicitly says to pipe issue text and never paste it into a shell argument because substitution syntax can execute ([sigma-goal/SKILL.md:37–40](../../../skills/sigma-goal/SKILL.md)); Loop's fallback instead says to pass the text as an argument. Properly constructed argv or shell quoting can be safe, but the Loop instruction supplies neither that qualification nor the safer stdin gesture, and the two paths give different instructions for the same untrusted text. | — |
| F08 | [skills/sigma-review/SKILL.md:139](../../../skills/sigma-review/SKILL.md) | “run `bash "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/risk-detect.sh"`” | Run the detector on a fully committed PR without `--base` and treat an empty `matched` result as risk clearance. | LOW | CONFIDENT | REFUTED — the same skill explicitly supplies the missing committed-diff requirement and rejects that inference. | “**On a committed branch or PR add `--base <base-ref>`**” and “so an empty `matched` clears nothing.” — [skills/sigma-review/SKILL.md:145–146](../../../skills/sigma-review/SKILL.md). |
| F09 | [skills/sigma-brainstorm/SKILL.md:53](../../../skills/sigma-brainstorm/SKILL.md) | “Preserve the record and commit a copy in the goal worktree.” | Have the Goal phase subagent commit the acceptance file directly before the orchestrator's `work.py commit` performs its secret scan. | HIGH | UNSURE | REFUTED — the shared Loop rule applies to every dispatched phase, so the artifact's requested commitment must be performed by the orchestrator. The standalone wording does not name that actor. | “**No phase subagent commits — only `work.py commit` does, once, at step 6.**” — [skills/sigma-loop/SKILL.md:105–108](../../../skills/sigma-loop/SKILL.md); the bypass consequence is explained in [references/running.md:251–265](../../../skills/sigma-loop/references/running.md). |
| F10 | [skills/sigma-plan-review/SKILL.md:47](../../../skills/sigma-plan-review/SKILL.md); [skills/sigma-review/SKILL.md:46](../../../skills/sigma-review/SKILL.md) | “Whatever the mechanism, the brief is the reviewer's ONLY input.” | Review only the brief and skip opening source or tracing callers because further reading appears forbidden. | LOW | CONFIDENT | REFUTED — both skills explicitly require source inspection and caller tracing; ONLY input limits the supplied dispatch context. | “for every function/contract the plan changes, grep *all* callers — not just the files the plan names.” — [skills/sigma-plan-review/SKILL.md:79](../../../skills/sigma-plan-review/SKILL.md); “read the code around it and grep every caller” — [skills/sigma-review/SKILL.md:69](../../../skills/sigma-review/SKILL.md). |
| F11 | [skills/sigma-research/SKILL.md:39](../../../skills/sigma-research/SKILL.md) | “rename, or a one-file change does **not**” | Waive Research for a one-file public-API change because its file count alone appears to make it exempt. | LOW | CONFIDENT | REFUTED — the rest of the same paragraph requires the full pass for a contract change, overriding this superficial exemption. | “Run the full pass when the goal touches a contract, crosses a module boundary,” — [skills/sigma-research/SKILL.md:40–41](../../../skills/sigma-research/SKILL.md), which also covers shared state and an unknown radius. |
| F12 | [skills/sigma-goal/SKILL.md:49–50](../../../skills/sigma-goal/SKILL.md) | “the loop auto-proceeding between dispatches.” | Treat invocation of the autonomous loop as approval of a newly proposed design and start implementation without the user's explicit design approval. | HIGH | UNSURE | REFUTED — automatic progression does not waive an executor's hard checkpoint; Loop expressly parks at such a checkpoint. | “design and the user has explicitly approved it — **every** project, however simple.” — [skills/sigma-brainstorm/SKILL.md:25–28](../../../skills/sigma-brainstorm/SKILL.md); the park rule is [skills/sigma-loop/SKILL.md:112–114](../../../skills/sigma-loop/SKILL.md). |
| F13 | [skills/sigma-loop/references/stopping.md:10](../../../skills/sigma-loop/references/stopping.md) | “when the backlog is empty (`next` → `DONE`)” | Treat a dependency-blocked queue as empty after DONE and spend the remaining run on KG gaps. | MEDIUM | CONFIDENT | REFUTED — Picking explicitly distinguishes an empty board from an unpickable board and requires reading/reporting the prerequisite diagnostic; the spare-gap permission still requires actual emptiness. | “`DONE` does not always mean the backlog is empty.” — [skills/sigma-loop/references/picking.md:184–190](../../../skills/sigma-loop/references/picking.md); also [skills/sigma-loop/SKILL.md:54–56](../../../skills/sigma-loop/SKILL.md). |
| F14 | [skills/sigma-loop/SKILL.md:179](../../../skills/sigma-loop/SKILL.md) | “**write only the audit-trail notes**” | Skip the enabled KG corpus note because autonomous Retro appears authorized to write only audit-trail notes. | MEDIUM | CONFIDENT | REFUTED — Loop explicitly invokes the Retro executor (`:177`), whose autonomous rule expressly includes the opt-in corpus note. The Loop summaries omit that allowance. | “**and the KG corpus note** (§4, when `knowledge_graph.enabled`)” — [skills/sigma-retro/SKILL.md:169–173](../../../skills/sigma-retro/SKILL.md); `:104–127` defines its condition and recording. |
| F15 | [skills/sigma-plan-review/SKILL.md:125–127](../../../skills/sigma-plan-review/SKILL.md) | “`record-plan-review .sdlc "<goal>"`” | Copy the recording gesture without `--agent-id`, then treat the recorded approval as carrying the independent reviewer's identity. | MEDIUM | UNSURE | REFUTED — the shared detailed phase procedure explicitly requires the reviewer ID, so the shortened recording gesture cannot itself establish that provenance. Whether an omitted ID is rejected was not checked. | “Independent verdicts carry the reviewer's own agent id:” — [skills/sigma-loop/references/running.md:87–88](../../../skills/sigma-loop/references/running.md), followed by the recording gestures with `--agent-id <id>`. |
| F16 | [skills/sigma-loop/SKILL.md:231](../../../skills/sigma-loop/SKILL.md) | “any other line → **`record review`**” | Record review after merge returns REPAIR or RERUN, leaving the required CI repair or rerun handling unperformed. | LOW | CONFIDENT | REFUTED — the mandatory landing reference supplies specific handling for these outcomes, which takes precedence over the summary's catchall. | “fix the named check, run verify, commit and” — [skills/sigma-loop/references/landing.md:227–236](../../../skills/sigma-loop/references/landing.md), continuing with `work.py pr`, fresh review and merge; “Wait for its result and re-run merge.” — `:237–241`. Loop requires reading this reference before the first merge (`SKILL.md:233`). |

## Per-file summary

Counting convention: “Gates examined” counts normative prose passages, not unique executable predicates. A passage is a Markdown paragraph, list item, or table row carrying a requirement, approval, prohibition, prerequisite, exception, refusal, blocking/parking rule, or equivalent gate language; wrapped continuation lines belong to that passage. Repeated statements count separately. Frontmatter and fenced examples are excluded from the count but were read. All 27 files were read in full, including nongate prose and examples. There are 372 counted passages. Findings counts include retained refutations and count a row for each file explicitly anchored in its File:line cell; cross-citations alone do not add to that file's count.

| File | Gates examined (count) | Findings (count) | Notes |
| --- | --- | --- | --- |
| [skills/sigma-goal/SKILL.md](../../../skills/sigma-goal/SKILL.md) | 27 | 1 | F12 refuted. Examined claims, acceptance capture, checkpoints, phase dispatch/measurement, feature confinement, lane routing and completion. F01, F05 and F07 cross-reference this file. |
| [skills/sigma-goal/references/selection.md](../../../skills/sigma-goal/references/selection.md) | 2 | 0 | none found; checked supervised approval language and the requirement to read the main skill. |
| [skills/sigma-brainstorm/SKILL.md](../../../skills/sigma-brainstorm/SKILL.md) | 8 | 2 | F01 survives; F09 refuted. Checked design approval, approach selection, handoff and acceptance recording. |
| [skills/sigma-brainstorm/references/selection.md](../../../skills/sigma-brainstorm/references/selection.md) | 2 | 0 | none found; checked approved-design/before-code wording and main-skill prerequisite. |
| [skills/sigma-research/SKILL.md](../../../skills/sigma-research/SKILL.md) | 24 | 1 | F11 refuted. Checked waiver scope, exhaustive radius/dispositions, source evidence, user scope authority, north-star resolution, lanes and audit recording. |
| [skills/sigma-research/references/selection.md](../../../skills/sigma-research/references/selection.md) | 2 | 0 | none found; checked phase identity, dossier consumers and main-skill prerequisite. |
| [skills/sigma-plan/SKILL.md](../../../skills/sigma-plan/SKILL.md) | 11 | 0 | none found; checked plan-before-code, decomposition, test cycles, no phase commits, test selectors/exceptions, disk/branch artifacts and mandatory Plan-Review. A short plan still has to be an artifact. |
| [skills/sigma-plan/references/selection.md](../../../skills/sigma-plan/references/selection.md) | 2 | 0 | none found; checked before-edit planning and main-skill prerequisite. |
| [skills/sigma-plan-review/SKILL.md](../../../skills/sigma-plan-review/SKILL.md) | 25 | 4 | F02/F03 survive; F10/F15 refuted. Checked reviewer resolution/failure, brief checking, forensic/caller checks, alignment, verdicts, exact-plan binding, refinements, dispositions and escalation. Rejecting findings does not replace a fresh approving verdict after FIX-FIRST. |
| [skills/sigma-plan-review/references/selection.md](../../../skills/sigma-plan-review/references/selection.md) | 2 | 0 | none found; checked before-code/phase wording. “Non-trivial plan” in the selection trigger does not override the main skill and orchestrators' never-skip rule. |
| [skills/sigma-implement/SKILL.md](../../../skills/sigma-implement/SKILL.md) | 26 | 1 | F06 survives. Checked assertion-red/green, TDD exceptions, unchanged-test proof, plan approval, per-step verification, mutation controls, security defaults, parking and banners. |
| [skills/sigma-implement/references/selection.md](../../../skills/sigma-implement/references/selection.md) | 2 | 0 | none found; checked test-first execution, per-step verification and main-skill prerequisite. |
| [skills/sigma-review/SKILL.md](../../../skills/sigma-review/SKILL.md) | 34 | 4 | F02/F04 survive; F08/F10 refuted. Checked reviewer independence, quantitative exclusions, all eight axes, project rules, conditional reviews, verdicts, failing cases, blocking/follow-up classification and re-verification. |
| [skills/sigma-review/references/axes.md](../../../skills/sigma-review/references/axes.md) | 15 | 0 | none found; checked eight axes, conditional contracts/migration checks, machine-owned style exclusions and blocking/precision rules against the main review skill. |
| [skills/sigma-review/references/selection.md](../../../skills/sigma-review/references/selection.md) | 2 | 0 | none found; checked Review identity, review modes and main-skill prerequisite. |
| [skills/sigma-verify/SKILL.md](../../../skills/sigma-verify/SKILL.md) | 6 | 0 | none found independently; checked fresh/full command execution, actual-output reading, claim-specific proof, subagent proof and the machine-evidence condition. Its criterion source is part of cross-skill F05. |
| [skills/sigma-verify/references/selection.md](../../../skills/sigma-verify/references/selection.md) | 2 | 0 | none found; checked fresh verification before claims and main-skill prerequisite. |
| [skills/sigma-retro/SKILL.md](../../../skills/sigma-retro/SKILL.md) | 29 | 1 | F05 survives, UNSURE. Checked immutable acceptance, per-criterion grading, missing-input limits, advisory authority, standing-document approvals, corpus-note opt-in, single record/refresh ownership and archival limits. Its specific autonomous rule refutes F14. |
| [skills/sigma-retro/references/selection.md](../../../skills/sigma-retro/references/selection.md) | 2 | 0 | none found; checked end-of-goal reflection and human approval of standing changes. “After a goal ships” describes timing less precisely than the orchestrators' explicit after-Review sequence. |
| [skills/sigma-loop/SKILL.md](../../../skills/sigma-loop/SKILL.md) | 44 | 3 | F05 survives; F14/F16 refuted. Checked terminal states, ordered prechecks, phase independence, ownership/worktree refusals, slice isolation, irreversible actions, verification, secrets, PR review, merge outcomes and stop behavior. |
| [skills/sigma-loop/references/filing.md](../../../skills/sigma-loop/references/filing.md) | 5 | 0 | none found; checked required routing flags, triage/deny-list, caps, blocking versus queued work, human-request exception, unit inheritance and upstream routing. The expressly documented triage-off path is a different authorized configuration. |
| [skills/sigma-loop/references/landing.md](../../../skills/sigma-loop/references/landing.md) | 40 | 0 | none found independently; checked tracked-call outcomes, freshness/content binding, safe commit exceptions, generation-bound review, ambiguous-post recovery, CI cycles, merge policy, done-versus-review, reconciliation, cleanup and human-owned unit landing. Refutes F16. |
| [skills/sigma-loop/references/picking.md](../../../skills/sigma-loop/references/picking.md) | 25 | 0 | none found independently; checked caller identity, fresh dispatch, tier/effort, child-outcome proof, claims/refills/releases, budget/handoff, dependencies, exclusive feature scope and ordered optional prechecks. Refutes F13. |
| [skills/sigma-loop/references/progress.md](../../../skills/sigma-loop/references/progress.md) | 4 | 0 | none found independently; checked phase identities, start/end model and usage conditions, markers, step attribution, audit notes, QC, advisory Retro and record/refresh ownership. Its paragraphs contain multiple constraints; the count is passage-based. |
| [skills/sigma-loop/references/running.md](../../../skills/sigma-loop/references/running.md) | 23 | 1 | F07 survives. Checked model fallback, phase/verdict records, waivers, reviewer mechanisms, exact-plan binding, evidence generations, fresh fix/re-review cycles, escalation, secret-scan ownership, lanes, worktree refusals and slice isolation. Refutes F15's provenance inference. |
| [skills/sigma-loop/references/selection.md](../../../skills/sigma-loop/references/selection.md) | 2 | 0 | none found; checked autonomous workflow identity, verified PR framing and main-skill prerequisite. |
| [skills/sigma-loop/references/stopping.md](../../../skills/sigma-loop/references/stopping.md) | 6 | 1 | F13 refuted. Checked actual-empty/budget requirements for gap work, stop/resume reporting, advisory decision tiers and fresh-session handoff. |

## Cross-skill inconsistencies

- **Research handoff — F01, survives.** Brainstorm names Plan as its terminal handoff ([skills/sigma-brainstorm/SKILL.md:44–45](../../../skills/sigma-brainstorm/SKILL.md)); Goal places Research between Goal and Plan ([skills/sigma-goal/SKILL.md:56–59](../../../skills/sigma-goal/SKILL.md)), and Research grants its own limited skip procedure ([skills/sigma-research/SKILL.md:38–42](../../../skills/sigma-research/SKILL.md)). The direct handoff is not limited to that procedure.
- **Independence after failure — F02, survives.** The Plan-Review and Review executors direct an inline fallback after an independently resolved process fails ([skills/sigma-plan-review/SKILL.md:40–42](../../../skills/sigma-plan-review/SKILL.md); [skills/sigma-review/SKILL.md:39–41](../../../skills/sigma-review/SKILL.md)); Loop permits inline only when the resolver names it ([skills/sigma-loop/SKILL.md:101–102](../../../skills/sigma-loop/SKILL.md)). The instructions disagree on who authorizes that change of route.
- **Original acceptance versus terminal grade — F05, survives, UNSURE.** Retro's immutable original criteria and achieved-only-if-all rule ([skills/sigma-retro/SKILL.md:36–39,69–74](../../../skills/sigma-retro/SKILL.md)) sit alongside a done gesture allowing partial/diverged ([skills/sigma-loop/SKILL.md:181–184](../../../skills/sigma-loop/SKILL.md)) and Verify's unspecified `done_when` source ([skills/sigma-verify/SKILL.md:42,50](../../../skills/sigma-verify/SKILL.md)). The broader completion requirement is a possible refutation, but does not explicitly resolve the changed-issue case.
- **Untrusted issue-text transport — F07, survives.** Goal prescribes stdin and prohibits pasting issue text into a shell argument ([skills/sigma-goal/SKILL.md:37–40](../../../skills/sigma-goal/SKILL.md)); Loop's equivalent fallback prescribes passing the fetched text as an argument ([skills/sigma-loop/references/running.md:35–38](../../../skills/sigma-loop/references/running.md)).
- **Acceptance commitment actor — F09, refuted by the shared rule.** Brainstorm asks for a committed acceptance copy without naming its actor ([skills/sigma-brainstorm/SKILL.md:53](../../../skills/sigma-brainstorm/SKILL.md)); Loop assigns every phase's commitment exclusively to the orchestrator ([skills/sigma-loop/SKILL.md:105–108](../../../skills/sigma-loop/SKILL.md)).
- **Autonomous Retro write set — F14, refuted by the invoked executor.** Loop and its progress reference say only audit-trail notes ([skills/sigma-loop/SKILL.md:179–180](../../../skills/sigma-loop/SKILL.md); [references/progress.md:119–120](../../../skills/sigma-loop/references/progress.md)); Retro separately and expressly allows the opt-in KG corpus note ([skills/sigma-retro/SKILL.md:169–173](../../../skills/sigma-retro/SKILL.md)).
- **Plan-review recording arguments — F15, provenance inference refuted.** The dedicated recording gesture omits `--agent-id` ([skills/sigma-plan-review/SKILL.md:125–127](../../../skills/sigma-plan-review/SKILL.md)), as does Loop's longer recording example ([skills/sigma-loop/references/running.md:153–155](../../../skills/sigma-loop/references/running.md)); the shared phase procedure requires independent verdicts to carry it (`:87–88`). The examples differ in the information they record.
- **Merge-outcome handling — F16, refuted by the specific reference.** Loop's summary maps every unlisted outcome to review ([skills/sigma-loop/SKILL.md:229–232](../../../skills/sigma-loop/SKILL.md)), while its mandatory landing reference directs REPAIR back through Implement and RERUN through a result wait and merge retry ([references/landing.md:227–241](../../../skills/sigma-loop/references/landing.md)).

The verdict spellings are different interfaces with explicit or inferable mappings, rather than an additional inconsistency: Plan-Review uses SOUND/SOUND-WITH-REFINEMENTS/FIX-FIRST and records pass/warn/block; Code Review uses APPROVE/SEND-BACK/BLOCK; PR posting uses approve/block/unblock. Likewise `plan-review` as a brief kind and `plan_review` as a phase token are different named interfaces. I did not infer a skipped gate merely from those spellings.

## What I did not check

- No reviewed implementation, `*.py` source, tests, evaluations, prior cold-read reports, scorecards, or `docs/launch` file contents were read. References to tests and prior incidents inside the allowed Markdown were treated as claims, not verified evidence. No external links in the corpus were opened.
- No project tests, guards, phase executors, reviewer processes, loop, mutation controls, or end-to-end workflow were run. Shell/Python commands were used for Git preparation, reading/enumerating Markdown, counting passages and saving this report. Consequently I did not establish whether code rejects any candidate action, whether a documented control can go red, or whether a host actually behaves as the prose claims.
- The security/contract/migration executor skills, reviewer helper implementation, model resolver, configuration schema, runtime gate defaults and enforcement outside the allowed corpus were not examined. They could supply additional controls; no finding asserts their absence or bypassability in code.
- Preparation was required by the user's separate rebase request and repository instructions: I read PROMPT.md (`<downloads>/PROMPT.md`), [docs/output-contract.md](../../../docs/output-contract.md), installed rebase/workflow guidance and Git metadata before the independent read. Git metadata exposed launch-related commit subjects, but not the earlier read record or scorecard contents. This is not an isolated clone with zero surrounding context.
- The local `ci/pr-single-leg` rebase completed without conflicts and brought HEAD to `origin/main`, which matched the prompt's pinned commit. The 72 existing untracked files were preserved. No remote push, PR creation or merge was performed, and no reviewed repository file was edited.
- I could not verify a more specific runtime model identifier than the supplied GPT-6 identity. I did not change or independently verify the model's reasoning-effort setting.
- Report artifact: report.md (`<downloads>/d2-independent-read-2026-10-14/report.md`).

# S5 cold read of the ten phase skills (D2, gate-skip ambiguity)

Evidence only. **No dimension is scored** and `docs/launch/scorecard.json` is untouched. Related records:
the original review [review-s5-7997e29e4270.md](review-s5-7997e29e4270.md) and the wording-fix record
[review-s5-rerun-640763007037.md](review-s5-rerun-640763007037.md). This reading is of main at
`a0d4ac7ac9cc`; line numbers below are that commit's, before this change's edits.

## What this is, and what it is not

One model reading ten skills as a reader who did not write the wording. **It is a model reading by the same
model family as the author of the earlier fixes, not a human reader and not a different vendor.** The
independence it has is only that this reader did not write the 715 wording and was told to look for ways to
skip a gate, not to confirm the fix. It shares the author's blind spots; a prose reading that finds few
ambiguities is weak evidence that a different reader would find few. Nothing here was run against an agent
to see whether it actually skipped a gate.

## Method

- Units: the same ten as the original review: sigma-goal, sigma-brainstorm, sigma-research, sigma-plan,
  sigma-plan-review, sigma-implement, sigma-review, sigma-verify, sigma-retro, sigma-loop. Each unit is its
  `SKILL.md` plus its `references/`.
- Read in full: all ten `SKILL.md`, the seven `references/` files of sigma-loop (filing, landing, picking,
  progress, running, stopping, selection), `sigma-review/references/axes.md`. The one-paragraph
  `selection.md` of the other nine was skimmed, not analysed. Scripts were not read, except three excerpts of
  `phase_gate.py` and `work.py` used to check a claim (below).
- For every gate, approval, "must", "only after", refusal condition or "skip if", one question: could a
  competent agent read this as allowing the gate to be skipped, or satisfied by something weaker?
  Severity: Medium = a plausible reading removes or weakens a gate; Low = unclear wording where the gate is
  stated elsewhere in the same skill or enforced in code.
- No second reader was told to refute these. The original review used a separate refuting verifier per
  finding (35 reported, 11 verified). This one did not, so a share of the items below would likely be
  dropped by such a verifier, as most of the original reports were.
- Cost was not measured with `evals/bench/meter.py`.

## Counts

| Skill | Found | Medium | Low | Fixed here (wording) | Remaining |
|---|---|---|---|---|---|
| sigma-goal | 3 | 1 | 2 | 2 | 1 |
| sigma-brainstorm | 1 | 0 | 1 | 1 | 0 |
| sigma-research | 3 | 2 | 1 | 2 | 1 |
| sigma-plan | 1 | 0 | 1 | 1 | 0 |
| sigma-plan-review | 3 | 2 | 1 | 2 | 1 |
| sigma-implement | 5 | 2 | 3 | 3 | 2 |
| sigma-review | 6 | 3 | 3 | 2 | 4 |
| sigma-verify | 1 | 1 | 0 | 1 | 0 |
| sigma-retro | 1 | 0 | 1 | 1 | 0 |
| sigma-loop | 8 | 1 | 7 | 3 | 5 |
| Total | 32 | 12 | 20 | 18 | 14 |

Comparison with the original review, which is a different method and not a like-for-like count: it verified
5 gate-skip findings plus 1 prose-vs-code finding that disables a gate (6), over the same ten skills.
Re-checked in today's text: the five gate-skip items are closed by the 715 wording (a companion cannot
drop `sigma-verify`; the `loop.py verify` recording line; the `check` exit rule on every mechanism,
`subagent` included, in sigma-review and sigma-plan-review; the `/code-review` note) and the loop's
`phase_record` statement is accurate. The sixth, `risk-detect.sh` on a committed branch, is item RV2
below: still open in main, with a code fix in a separate pull request. **The D2 threshold is 0 gate-skip
ambiguities. This reading does not show 0: it found 12 Medium before the edits in this change, and 1
Medium (RV2) after them, pending its own pull request. D2 is not met by this record.**

## Ambiguities (file:line at a0d4ac7ac9cc)

Status F = reworded in this change; R = remaining; P = fixed in code by a separate pull request, not merged here.

| Id | Where | Quoted | Why ambiguous | Sev | Status |
|---|---|---|---|---|---|
| G1 | sigma-goal/SKILL.md:118-120 | "Running inline (no subagent dispatched)? Call `end` without `--agent-id`" | Reads as a sanctioned inline path against line 47 ("dispatches each phase as its own subagent"); only code (`phase_gate.py`, when `gates.phase_record` is on) refuses it | Med | F: inline stated as a host-without-subagents case, with the refusal named |
| G2 | sigma-goal/SKILL.md:131-132, 136-137 | "if Retrospective ran", "omit the flag if Retrospective never ran" | Reads as Retrospective being optional on a goal that completes | Low | F: flag omitted only if the goal stopped before Retrospective |
| G3 | sigma-goal/SKILL.md:55, 222 | "pausing for the user at each gate", "the user approves each gate" | The set of gates is never listed; a blanket "go ahead" could be read as approving all | Low | R: needs an owner decision on what the gates are |
| B1 | sigma-brainstorm/SKILL.md:25-27 | "the user has approved it" | What counts as approval is unstated (the original request?) | Low | F: "explicitly approved". Residual: no statement for an unattended run |
| R1 | sigma-research/SKILL.md:113-114 | "plan briefly and go" | "go" reads as straight to Implement; the Plan-Review rule is 17 lines later (130-131) | Med | F: "Plan-Review still runs in full" added in place |
| R2 | sigma-research/SKILL.md:38-42 | "record a one-line radius in the goal's journey note and go straight to Plan" | With the phase gate on, `phase_gate.py:130-134` wants a non-empty research file or a recorded waiver; a journey note alone leaves `record done` refused | Med | F: names `loop.py waive-phases` |
| R3 | sigma-research/SKILL.md:86, 188 | "Tensions and blocking questions go to the user" | Nothing says what halts an unattended run while a blocking question is open | Low | R |
| P1 | sigma-plan/SKILL.md:19 | "concrete, testable steps; `sigma-plan-review`." | Fragment; does not say the review is mandatory (line 71 does) | Low | F: "then ... (never skipped)" |
| PR1 | sigma-plan-review/SKILL.md:113-118 | "FIX-FIRST (blocking issues)" | Never states that Implement waits for a fresh approving verdict; only `work.py pr` (when enabled) enforces it | Med | F |
| PR2 | sigma-plan-review/SKILL.md:136-139 | "Refinements carried into Implement without editing the plan file stay inside the approval" | An unbounded set of changes could be carried without re-review by not editing the file | Med | F: "Listed refinements". Residual: a listed refinement can still be large |
| PR3 | sigma-plan-review/SKILL.md:39-42 | "If a resolved `process`/`command` FAILS" | A `subagent` that fails or never returns is not covered (same in sigma-review) | Low | R: the fallback is a design choice |
| I1 | sigma-implement/SKILL.md:63 | "Skip TDD for prototypes, generated code, or config; say so." | "prototypes" undefined; contradicts "No production code without a failing test first" | Med | F: "only for throwaway prototypes ...; name which" |
| I2 | sigma-implement/SKILL.md:88-89 | "it should already have passed `sigma-plan-review`" | A belief, not a check; nothing says to stop if it has not | Med | F: must carry an approving verdict, else stop |
| I3 | sigma-implement/SKILL.md:149-150 | "a standing, expected step rather than one option in a list" | "expected" is weaker than required | Low | F |
| I4 | sigma-implement/SKILL.md:54 | "After writing the plan's `## Tests` entries, record both stages" | Unclear when recording starts; "never replaces" implies it is mandatory | Low | R |
| I5 | sigma-implement/SKILL.md:60 | "Plain commands do not; changed tests need red again." | Elliptical; the object of "do not" is implied | Low | R |
| RV1 | sigma-review/SKILL.md:157 | "a finding can be waived with a stated reason" | Does not say by whom; reads as the reviewer waiving a project-rule finding the skill calls a gate (132-135) | Med | F: only a documented in-code waiver or the user |
| RV2 | sigma-review/SKILL.md:146-148 | "decide each category from `git diff --name-only <base>...HEAD`" | `risk-detect.sh` scans only uncommitted files; on a committed branch the agent judges the categories by hand with no criteria | Med | P: `risk-detect.sh --base <ref>`, pull request 1275 |
| RV3 | sigma-review/SKILL.md:150-153, 196 | "an overall verdict", "a real bug is FIX-FIRST" | No verdict words given; `FIX-FIRST` is plan-review's and `work.py record-review` accepts only `APPROVE`, `SEND-BACK`, `BLOCK` (`phase_gate.py:43`) | Med | F: names the three words. Residual: the difference between SEND-BACK and BLOCK is not defined in this skill |
| RV4 | sigma-review/SKILL.md:185-188 | "Reporting a real finding as follow-up is a judgement you are allowed to make" | Lets a real finding without a written failing case be filed instead of blocking; by design, but open-ended | Low | R |
| RV5 | sigma-review/SKILL.md:39-41 | as PR3 | as PR3 | Low | R |
| RV6 | sigma-review/SKILL.md:111-113 | "On a real surface run `/sigma-security-review`" | "real surface" undefined (the risk-detect gate backs it up) | Low | R |
| V1 | sigma-verify/SKILL.md:26-27 | "the repo's tests/build/lint, or `.sdlc/project.md` -> Verify command" | "or" lets an agent pick a weaker check than the project's own Verify command | Med | F: Verify command if set, else tests/build/lint |
| RT1 | sigma-retro/SKILL.md:36-40 | "do not claim achieved" | Says what not to grade, not what to grade, when the acceptance record is missing | Low | F: "grade no better than partial" |
| L1 | sigma-loop/SKILL.md:90-91 | "One subagent PER PHASE, tier `off` included" | Unqualified, against line 36-38 where a host without subagents runs inline | Low | F: "on a host with subagents" |
| L2 | sigma-loop/SKILL.md:177 | "run the `sigma-retro` executor (advisory)" | "advisory" reads as the phase being skippable; the gate requires it unless waived | Low | F: "output advisory; the phase is required" |
| L3 | sigma-loop/references/landing.md:134-135 | "running `/code-review` on the PR, else `/sigma-review` in diff mode" | Reads as `/code-review` alone satisfying the post-PR review; sigma-review says it satisfies no gate | Med | F |
| L4 | sigma-loop/SKILL.md:197-198 | "With `config.verify.enforce` on, a `done` needs FRESH machine evidence" | Off is not stated to leave sigma-verify's rule binding | Low | R: no token room in this skill's ceiling |
| L5 | sigma-loop/references/landing.md:195-210 | "re-run `work.py merge`" versus "never re-run merge" | Which of two cut-short cases applies cannot be told from inside the call | Low | R: the safe choice is an owner decision |
| L6 | sigma-loop/SKILL.md:179-180 | "write only the audit-trail notes" | sigma-retro:169-172 also allows the KG corpus note; a reader could skip it (a warning only) | Low | R: no token room |
| L7 | sigma-loop/references/progress.md:16-17 | "best-effort, skip it rather than guess the phase name" | `start` is skippable on Claude, not on Codex; reads as the boundary marking being optional | Low | R |
| L8 | sigma-loop/SKILL.md:119-122 | "(at the latest, the 2nd send-back at one tier)" | Muddles when `escalate` is mandatory; running.md:210-213 states it clearly | Low | R |

## Fixes: what was checked

Each edit was word-neutral or trimmed elsewhere; `evals/phase_context_budget.json` ceilings are unchanged and
`python3 evals/phase_context_budget.py` prints clean (sigma-research sits exactly at its word ceiling, and
sigma-loop's SKILL.md is below its estimator limit). To fit, two redundant passages were shortened: the
"(e.g. ...)" examples and the "merge four sources" lead-in in sigma-review, and one sentence of the
incident note in sigma-implement. Nothing here is pinned by a test, so none of the new sentences has been
seen red against its removal.

## What this does not show

- Recall is unmeasured; a different reader, or the same reader on another day, would list different items.
- The 14 remaining items are listed, not hidden. Most are Low; G3, PR3/RV5 and L5 need an owner decision
  because fixing them would change what the skill does.
- Wording that reads clearly to a model reader says little about whether an agent follows it. That is the
  parked Tier-2 behavioural eval, not this record.

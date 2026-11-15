# Plan review 2 for #2222 (Reviewed by: dispatched subagent, fresh context, read-only)

Strategic alignment: no north-star read (brief says none available); correctness judged only. zones.py / red_green.py checks were not run (read-only brief; they may write state).

## Verified correct (code evidence)
- acceptance.py:64 exact heading fullmatch; :158-166 record is create-only; criteria() stops at the next `##` (acceptance.py:62-66), so `## Added criteria` after `## Done when` is ignored by the old parser. The T1 golden pin is sound.
- work.py:4839-4842: pr acceptance presence check is enforce-gated; plan keeps it and adds an unconditional call. Correct.
- work.py:4504-4521 `_copy_acceptance_into_worktree` is create-only; `_sync_worktree_copy` (acceptance.py:249) replaces the worktree copy only when it is byte-identical to the OLD root record. Plan D-4 reuse is correct.
- reviewer.py:944-959: `independent_run_refusal` returns None at `_independent_on` false, so placing the clause above it is the right seam. Callers: work.py:1227, phase_gate.py:310 (record_verdict covers both approving paths).
- acceptance.py:207 uses fetch_comments_strict, which raises on transport failure (sources.py:4247), so fail-closed at pr is implementable.
- Tests named per the `-k` filter: all new names carry acceptance / goal_readiness / issue_body. OK.

## Blockers
None.

## Majors
M1. Deleted or re-recorded record is not a clause. `record` on a missing file re-derives criteria from the issue or a fresh draft (acceptance.py:158-200). `prefix_refusal` as described (H, B, P) is applied to an existing record. If the agent deletes root and worktree records, nothing in commit/pr/merge notices: the pr presence check is enforce-gated (work.py:4839), and a `git rm` in a later commit leaves no file to compare. A removal of ALL criteria is the strongest weakening. Add clause "D": when a first-added baseline blob exists, a missing or unparseable current record refuses (unconditionally, at commit/pr/merge), plus a re-record-with-different-criteria test (covered by B only if B runs). Needs its own control.

M2. New gates must be registered, or `tests/test_enforcement_table.py` goes red. `ENFORCEMENT_GATES` / `ENFORCEMENT_EXEMPT` (work.py:130, :490) must list any new module-level gate-named function in work.py, and `docs/enforcement.md` must be regenerated (`python3 skills/sigma-doctor/scripts/enforcement_table.py > docs/enforcement.md`) after the new `goal_readiness` block in `config.json.tmpl`. The plan only adds a prose note near work.py:275-290 and omits `docs/enforcement.md` from Files and T12. Add both, plus the control-table rows (mechanism, condition stating the pre-commit/vacuous residual).

M3. `goal_readiness` at promote covers only `promote.promote()`. Other writers of `sdlc:goal` exist and bypass it: triage.py:2153, auto_unpark.py:606 and :723, plus `_swap_labels` users in blockers.py, unpark.py, reconcile.py. The pick-time check is a prose-run verb (picking.md:239-270, SKILL.md:71-72), like decompose-check, so under `require` a host that skips the prose step starts the goal unchecked. The goal says "checked when promoted ... and in the pick pre-checks". Refinement: also call the readiness check inside Python at `work.py start` (host-agnostic, AGENTS.md: triggers live in Sigma's Python), or state in docs that `require` is advisory for those paths. Name the other promoters in the plan.

## Minors
m1. B reads local `sdlc/<goal>` first (plan T2 ref order). A local history rewrite (`commit --amend`, reset) replaces the "first" blob; prefer the earliest-added blob across local AND remote refs (remote first once pushed, since checkpoint push work.py:5010 publishes it). State the residual: force-push defeats it.
m2. Legacy-record in-block extension: a legacy baseline (no hash) is satisfied by any ordered extension, so an actor can append reasonless criteria INSIDE `## Done when` (up to 7) and pass B. H/hash only help new records. State it in D1 residuals and add a test for a hash-carrying record (H red) vs legacy (documented pass).
m3. B is skipped where `.sdlc/acceptance` is gitignored (`_copy_acceptance_into_worktree` skips ignored paths, work.py:4504-4521). Sigma tracks it (.gitignore re-includes it) and init does not ignore it, but adopters who ignore it get H-only. State the count/doctor visibility, e.g. doctor prints "baseline: unavailable" rows.
m4. T4 is very large (about 30 tests, 3 gates, divergence, extend-comment). Reasonable to split T4 so a divergence false-positive (fleet-wide park) can be reverted without losing the local guard. Not required.
m5. merge(): by merge time the confirmed-merge cleanup removes byte-identical root copies (#1474). Plan Risk 7 already says read the PARK handling first; also test merge when the root copy is already gone (guard must read the branch blob, not refuse on absence of the root file).
m6. Doctor open-goal body reads (D7, 50 REST calls) per run: state the rate-limit cost against the REST budget; fine as bounded.

## Controls and rules check
Per-clause controls, ordered-prefix-to-membership weakening, documented-gesture requirement, no-bypass AST test, no root-checkout writes, no SIGSTOP: all present and well specified. Missing clause control for M1 (D clause).

VERDICT: SOUND-WITH-REFINEMENTS

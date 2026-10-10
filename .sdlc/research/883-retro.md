# #883 retro (P7) - golden task T2, shared-helper bug with call-site patch control

Grade: ACHIEVED. `evals/golden/T2/` exists in the shape verify.py requires, the prompt asks for a call-site patch while the root cause is a shared helper with two sibling callers, and `python3 evals/golden/verify.py` printed `ok T2` / `golden: 1 task(s), 0 red` (exit 0) when re-run in the worktree. The naive property was seen red on the documented gesture (see 883-controls.md).

## Intent vs shipped
- Layout: task.json (trap true, origin planned), repo/, reference/ (complete tree, differs from repo only in `relkit/versions.py`, matching allowed_paths.json), rubric.json, hidden/verify.json, hidden/files, hidden/naive. hidden_sha256 covers all three hidden files.
- Issue text vs root cause: the prompt asks to patch `newest()` in `relkit/changelog.py`; the defect is `version_key` in `relkit/versions.py`, also used by `meets_minimum` in `relkit/compat.py`. Hidden tests: one per caller, none on the helper. Measured by hand: both fail on the start tree; the call-site-only overlay passes the changelog test and fails the compat test; visible suite 4 passed.
- Controls seen red (883-controls.md): naive replaced with the true fix, edited-but-unhashed hidden test, reference equal to start, start made fixed. Each reverted, final run `ok T2`.
- Deviation from the research dossier: its feasibility prototype (pricing/invoice/refund) was not shipped; the independently authored scenario (relkit) is the one in the tree. This is intended by D-6, not drift. The dossier's advice to drop the direct helper test was followed.
- Known weakness, stated in the controls: a naive patch that fixes BOTH call sites while leaving the helper alone is not discriminated (control d showed the hidden tests pass on it and verify went red on `naive`). The task separates "patch the named site" from "fix a shared helper's callers", not "patch every caller" from "fix the helper". The rubric asks for the helper fix, but nothing mechanical enforces it.
- Wall time measured once: 5.6 s all tasks, 5.1 s `--only T2` on a scratch copy.

## Debt (non-blocking)
- Not measured: 10x/100x scaling, and behaviour with more than one task present (T1 and T2 together); only a single-task run was observed.
- Guards that read `git ls-files` (write-site inventory, outside-library scan, link check, secret/exposure scan, rename check) were not proven in-tree: the work is uncommitted and the controls ran on a scratch copy. They are unproven until committed.
- The third-party-import guard is skipped under the system 3.9 interpreter, so the stdlib-only property of T2 task code was not exercised there; only a newer interpreter ran it.
- D-6 authorship: the scenario was authored by an independent implement subagent, not by the research phase, but "independent" is a process claim recorded here, not a measured property; the agent worked from the plan, verify.py and the golden test only.
- D-2 stays open: hidden tests and the naive patch live in the repo, so a later executor must hide them from the model under test. T2 does not do that.
- Rubric criterion 3 is prose only; no check grades a model patch against it.
- Leftover `__pycache__` was not checked beyond verify.py ignoring it; confirm none is staged at commit.

## Rule proposals (listed only, not applied)
- Carried from the 881 retro and now seen twice: guards that read `git ls-files` should be run committed-in-scratch as a standard control step before a retro is graded.
- Candidate: a golden task's controls list should state which "smarter naive" variants (both call sites patched) it does and does not discriminate, so the gap is recorded at authoring time.

Author record: scenario authored by an independent implement subagent (Sonnet) that read only the plan, verify.py and the golden test, not the research dossier.

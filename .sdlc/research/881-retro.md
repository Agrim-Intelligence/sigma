# #881 retro (P7) - golden verify gesture

Grade: ACHIEVED. verify.py (stdlib, `--help` safe), the task.json schema, the five planted-tree controls, the bench_tasks / isolated_env pins and the registrations are all present and controls were seen red on the documented gesture (see 881-controls.md).

## Intent vs shipped
- All five acceptance bullets are met. Two are met by a justified reading rather than the literal wording: zero write-surface entries (verify.py has no write sites; ratchet control proved the scan covers it) and a plain doc line in docs/agent-rules-detail.md instead of an allowlist row (the guard resolves and --help-runs it).
- Wall time recorded in evals/README.md: 0.45-0.65 s (venv 3.12), 3.4-3.9 s (system 3.9), one task only; 10x/100x not measured.
- CI step is gated on FULL == 'true', so it does not run on every PR leg. Acceptable, but worth knowing.
- Change left uncommitted in the worktree; guards that read git ls-files were run in a scratch clone, so they are unproven in-tree until committed.

## Debt (non-blocking, carried from review)
- Crash path exits 1 (not 2) for non-UTF-8 hidden output, RecursionError in JSON parse, non-ASCII-digit SIGMA_GOLDEN_TIMEOUT.
- No test pins that an allowed_paths entry without a trailing slash is not a prefix.
- Loose property-name assertion in red(); sourceless .pyc ignored.
- No minimum-task-count ratchet once slice 2 lands: an empty evals/golden exits 0 ("nothing verified").
- Sequential runs are the scaling ceiling; D-3, D-5, D-7 still open.

## Rule proposals (listed only, not applied)
- None required. Candidate: guards that read git ls-files should be run committed-in-scratch as a standard control step.

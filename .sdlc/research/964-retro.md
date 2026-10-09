# #964 P7 RETRO (advisory)

GRADE: achieved (AC-5 pending verify output; see below)

## Intent vs shipped
- AC-1 achieved: helper `_which_with_gh` at 3 call sites; four tests green, red when reverted (orchestrator re-run, 6 red -> 6 green).
- AC-2 achieved, narrower than worded: fix deletes CLAUDE_CODE_SESSION_ID, SIGMA_HOST, CODEX_SESSION_ID. ANTHROPIC_* is not read by host detection, so it is not scrubbed; the AC text over-stated the cause.
- AC-3 achieved: diff adds lines only in tests; no assertion changed, nothing skipped/xfailed/deleted.
- AC-4 achieved after correction: implementer's first control used the wrong id list (included gitlab test, omitted ghe_fqdn; 5 red + 1 green is not a control). Orchestrator re-ran the real six: 6 red, 6 green.
- AC-5 not evidenced in the files read: full-suite/verify output is not in the controls file.

## Residual debt
- Other macOS/in-Claude-Code-fragile tests are unknown; only these six were fixed (out of scope).
- Host markers are scrubbed per test, not by an autouse fixture; the next codex-detection test will leak again.
- Linux CI never showed these failures; only a macOS run guards them.
- `/fake/gh` helper masks a real "gh missing" path in these tests (intended, documented).

## Proposed lessons (not applied)
1. A control's id list must be copied from the issue, not retyped; assert count and names (a 5+1 result should have been flagged).
2. Env-dependent tests should scrub the whole host-marker set via one shared fixture.
3. Research should state the true cause (ANTHROPIC_* irrelevant) back into the issue.

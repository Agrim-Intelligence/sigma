---
name: sigma-debug
description: Reproduce and diagnose a bug hypothesis-first before fixing it. Use for bugs, failures, regressions, or /sigma-debug.
allowed-tools: Bash, Read, Grep
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-debug

Detailed selection triggers: [selection](references/selection.md).

> Reproduce as a test before fixing. Hypothesis-first.

*A conditional-risk skill orthogonal to `sigma-review`; always Sigma's own (no companion
equivalent).*

Ground yourself first: the north-star — `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/north_star.py"` prints
its real path, which is not the bare one (`.sdlc/` is gitignored, so it is absent from the goal
worktree this may run in, #1778) — the repo's `CLAUDE.md`, the files the bug appears to live in, and
the existing tests near them.

## Goal
Take a reported bug and produce: a reproducing test (RED), a fix (GREEN), a one-paragraph explanation, and
a prevention note.

## Steps
1. Restate the bug. Confirm steps and expected vs actual.
2. Form a *single* hypothesis. Write it down.
3. Write a test that fails *for the hypothesised reason*. Run it. Confirm RED.
4. If red for the wrong reason → revise the hypothesis, go to step 2.
5. Apply the minimum fix. Run the test. Confirm GREEN.
6. Run the full suite. Confirm nothing else broke.
7. Write up the report.

**The Iron Law for bugs:** no fix without a reproducing test first. If the test is hard to write, the bug
is harder to fix — that is the signal, not the problem.

## Gates
- There is a named reproducing test in the repo, currently passing.
- The fix is smaller than the test.
- A one-sentence prevention note exists.

## Stop when
- The bug is in a no-go / out-of-scope area → **park for a human**.
- The fix would require a schema or contract change → exit to `sigma-plan` (and run `sigma-migration-check`
  / `sigma-contract-check`); do not expand the fix.
- You cannot reproduce after ~30 minutes → write up what you tried and park for the user.

## Output → render the report, and persist it if you want it retained
Write to `.sdlc/reviews/debug-<slug>.md` (NOT under `.sdlc/knowledge/`, which is gitignored).

```markdown
# debug · <slug>

## report
<verbatim ticket / quote>

## repro
<exact steps the test takes>

## hypothesis
<one sentence>

## root cause
<one paragraph, plain English>

## fix
<file:line summary>

## test
<test name in repo>

## prevention
<one sentence: lint rule? skill update? doc? type? property test?>
```

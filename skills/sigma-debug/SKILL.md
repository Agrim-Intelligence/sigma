---
name: sigma-debug
description: Reproduce a bug in a tight loop, then diagnose it before fixing. Use for bugs, failures, regressions, or /sigma-debug.
allowed-tools: Bash, Read, Grep
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-debug

Detailed selection triggers: [selection](references/selection.md).

> Build the feedback loop first, then ranked hypotheses. Reproduce as a test before fixing.

*A conditional-risk skill orthogonal to `sigma-review`; always Sigma's own (no companion
equivalent).*

Ground yourself first: the north-star — `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/north_star.py"` prints
its real path, which is not the bare one (`.sdlc/` is gitignored, so it is absent from the goal
worktree this may run in, #1778) — the repo's `CLAUDE.md`, the files the bug appears to live in, and
the existing tests near them.

## Goal
Take a reported bug and produce: a feedback loop that goes red on it, a reproducing test (RED), a fix
(GREEN), a one-paragraph explanation, and a prevention note.

## Steps
1. **Restate the bug.** Confirm steps and expected vs actual. Redact secrets in every command, output
   or artifact you show (`<REDACTED>`); build the loop against env vars so a credential stays out of it.
2. **Build the feedback loop, before any theory.** One command, already run once, that drives the real
   code path and asserts the *reported* symptom. It is **tight**: red-capable on this bug (not "runs
   without error"), deterministic, seconds not minutes, runnable unattended. Try, roughly in order: a
   failing test at the nearest seam · a CLI run diffed against a known-good fixture · a captured
   payload or log replayed through the path · a throwaway harness calling the one function · a
   property/fuzz loop for "sometimes wrong" · a `git bisect run` script between a good and a bad
   commit · the same input through old and new code, diffed. A flaky bug needs a high reproduction
   rate, not a clean repro: loop the trigger and add stress until it fails often enough to debug.
   Reading code to build a theory before this command exists is the failure this step prevents.
3. **Minimise.** Cut inputs, config and steps one at a time, re-running the loop after each cut, until
   every remaining element is load-bearing — removing any one turns it green.
4. **Hypothesise: 3–5, ranked.** A single hypothesis anchors on the first plausible cause. Each one is
   falsifiable — "if X is the cause, changing Y makes the bug disappear"; one with no prediction is
   sharpened or dropped.
5. **Probe** until one hypothesis is confirmed; if all are falsified, back to step 4. Each probe tests
   one prediction; change one variable at a time. Tag every debug log with a prefix unique to this
   session (e.g. `DEBUG-` plus four random hex characters) so cleanup is one grep, and log no secret
   value in a tagged line. For a performance bug, measure a baseline first and bisect against it —
   logs rarely find a slowdown.
6. **Write the regression test** from the minimised repro, at a seam where it exercises the real bug
   pattern. Run it. Confirm RED *for the confirmed hypothesis's reason*; red for the wrong reason →
   back to step 4. If the only seam that reproduces it is slow or high (an integration test), use it
   and record that no cheaper seam exists — that is an architecture finding for the prevention note.
   A shallow test that cannot reproduce the real call chain is not a reproducing test.
7. **Fix.** Apply the minimum fix. Run the test → GREEN. Re-run the step-2 loop against the original,
   un-minimised scenario → GREEN.
8. **Clean up.** Run the full suite; confirm nothing else broke. Grep the exact prefix chosen in
   step 5 and remove every tagged line.
9. Write up the report.

**The Iron Law for bugs:** no fix without a reproducing test first. If the test is hard to write, the bug
is harder to fix — that is the signal, not the problem.

## Gates
- The feedback-loop command is recorded and was seen red on this bug.
- There is a named reproducing test in the repo, currently passing.
- The fix is smaller than the test.
- No line carrying this session's debug prefix remains.
- A one-sentence prevention note exists.

## Stop when
- The bug is in a no-go / out-of-scope area → **park for a human**.
- The fix would require a schema or contract change → exit to `sigma-plan` (and run `sigma-migration-check`
  / `sigma-contract-check`); do not expand the fix.
- You cannot build a red-capable loop after ~30 minutes → write up what you tried and park for the
  user, asking for what would unblock it: access to the environment that reproduces it, a redacted
  captured artifact (log, payload, trace), or permission to instrument the environment that
  reproduces it. Do not hypothesise without a loop.
- The loop is red but no test at any seam reproduces it → park, with the loop command and its
  output as evidence and the missing seam as the finding. Do not commit a shallow test instead.

## Output → render the report, and persist it if you want it retained
Write to `.sdlc/reviews/debug-<slug>.md` (NOT under `.sdlc/knowledge/`, which is gitignored).

```markdown
# debug · <slug>

## report
<verbatim ticket / quote>

## loop
<the one command (redacted) and its red output>

## repro
<exact steps the minimised test takes>

## hypotheses
<3–5, ranked, each with its prediction; mark the one confirmed and how>

## root cause
<one paragraph, plain English>

## fix
<file:line summary>

## test
<test name in repo>

## prevention
<one sentence: lint rule? skill update? doc? type? property test?>
```

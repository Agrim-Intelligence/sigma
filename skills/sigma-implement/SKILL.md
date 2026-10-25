---
name: sigma-implement
description: Implement the plan test-first and verify each step. Use in the Implement phase or /sigma-implement.
allowed-tools: Bash
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-implement

Detailed selection triggers: [selection](references/selection.md).

## Context-efficient inspection

Locate first: `rg --files` for paths, `rg -n` for symbols or claims. Read only the
relevant lines in a bounded range, widening only when the boundary is unclear; never
dump whole files or broad search output. Save bulky test or diagnostic output to a
scratch file and inspect its exit status and focused matches or a bounded tail. Do
not hide failures; keep secrets out of the scratch file. Guidance, not a measured savings claim.

This file is the whole Implement phase. Two sections further down are part of it:
**`## Security defaults (fail-closed, untrusted input)`** and **`## Mutation testing (prove the
guard, don't assert it)`**. Read them before you start, not after.

Two things happen in this phase: **build test-first**, and **execute the plan step by step**.

## Test-first (TDD)
**No production code without a failing test first.** If you didn't watch the test fail, you don't know
it tests the right thing. Wrote code before the test? Delete it and re-derive from the test.

**Red → Green → Refactor**, one behavior at a time:
1. **RED** — write the smallest test for the next behavior. Run it. **Watch it fail for the right
   reason** (it asserts the behavior — not a typo or an import error). Then name the production
   change that would flip it to green — if the fixture already satisfies the assertion without one,
   the fixture is broken, not the implementation. Real incident: `tests/test_feature_scope.py`'s
   `test_a_member_beyond_the_window_is_still_reachable_by_its_label` drifted, during #1829's own
   implementation (PR #1838), to a fixture totaling exactly the 200-item `_BACKLOG_FETCH_CAP` —
   true but vacuous, its widening-query assertion holding without the widening query ever running.
   The test's docstring records an independent review catching
   it; the fix added a standing assertion.
2. **GREEN** — write the *minimal* code to pass. Run it. All green.
3. **REFACTOR** — clean up while staying green. "Clean up" is not a feeling; it is this list, the
   one `sigma-review` axis 6 judges by. Long Method, Large Class, Long Parameter List,
   Primitive Obsession (a unit doing too much) · Feature Envy, Inappropriate Intimacy, Message Chains,
   Middle Man (units too entangled) · Duplicate Code, Dead Code, Speculative Generality (code that
   should not exist). Remove only what your change orphaned; report other dead code. Fix it now, unless `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/zones.py" check --goal "$goal" --diff`
   prints the file as a legacy row without `intent: yes`: there change only what the goal needs.
   With no such row, or if it could not run, this rule holds.

After writing the plan's `## Tests`, record both through Sigma (companion TDD skills never replace this):

```sh
loop="${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/loop.py"
python3 "$loop" red .sdlc "$goal"
python3 "$loop" verify .sdlc "$goal"
```

Plain commands record neither; changed tests need new red.
Refactors: break, red, restore, green.

Skip TDD only for throwaway prototypes, generated code, or config; name which. With
`verify.enforce`, `work.py pr --no-tests <reason>` is a PR-body exception;
it bypasses only test-first proof, never other gates.

## Sources
Kent Beck's *Test-Driven Development: By Example* defines TDD; Martin Fowler's *Refactoring: Improving the Design of Existing Code* names refactor smells.

## Test behavior, not plumbing (anti-patterns to avoid)
- **Don't test the mock** — assert real behavior/output, not that a stub was called.
- **No test-only methods in production code** — tests exercise the real interface.
- **Don't mock what you don't understand** — a wrong mock passes a wrong test.
- **Don't leave integration as an afterthought** — cover the real seams, not just isolated units.

## Leave it readable (the reviewer is not the first reader — the next author is)
Cheapest at write time, expensive at review, and the one thing a passing test cannot check:
- **Names** say what the thing does and what it mutates. A name that needs the comment to be
  understood is the wrong name.
- **Comments say WHY, not WHAT.** The code already says what. Record the reason, the constraint, the
  thing you tried that did not work.
- **Nothing you wrote is now false.** Before finishing a step, check the comments, docstrings and
  README the change touched — a comment that lies is worse than no comment, and the diff is where it
  became a lie. This is `sigma-review` axis 7.

## Execute the plan, step by step
1. **Load** the plan and **review it critically** — raise any concern *before* starting (last cheap
   moment to catch a bad plan; it must already carry an approving `sigma-plan-review` verdict, else stop and run it).
2. For **each step**: announce it (with a goal id, `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/loop.py" next-step .sdlc "<goal>"`
   prints the banner; `.sdlc` is the MAIN checkout's, by absolute path), do exactly what it says,
   **verify** it (see `sigma-verify`), then move on, tracking progress.
3. **Park, don't force** on anything irreversible or blocked (matches the loop's park rule).

## Mutation testing (prove the guard, don't assert it)
A green suite says a guard exists; only reverting it and watching the right test fail proves it
*works* — the same distinction `.sdlc/context/north-star.md` Standing rule #1 makes for any prose
claim ("A claim in prose is not evidence. Execute it") and AGENTS.md's RELIABILITY property states
in the same breath: "it does what it claims, and its claims are measured."

**After GREEN, for any non-trivial guard or check** — a validation, a concurrency check, a
pagination boundary, anything whose whole job is to reject or correct something — revert the
specific line the fix depends on, re-run the relevant test, confirm it now fails, then restore and
confirm green again. For a guard with several clauses, break each clause on its own; for an ordering check, weaken the order to plain membership. Two real examples, each verified against its PR record:
- **#1764's compare-and-swap version check (PR #1879):** reverting the version-check clause made
  4 of 8 new tests fail, and the real on-disk `config_version` silently reached 2 while both
  concurrent callers were told "success" — exactly the bug the guard exists to prevent. Restoring
  the clause returned every test to green.
- **#1829's REST pagination fix (PR #1838):** the old per-page-recomputed-from-the-filtered-count
  logic reproduced live on a 220-item backlog — a duplicated early issue and a dropped
  true-next-oldest one, silently, with no error. The new regression test is confirmed red against
  that old logic and green against the fix.

**Target the line the fix actually depends on, not a nearby one** — a mutation that "survives"
because it hit a redundant secondary check instead of the real guard proves nothing about the
guard. This repo has shipped exactly that mistake before: north-star Standing rule #1's own
2026-08-08 entry records a mutation test that targeted a redundant secondary lookup, passed against
the mutant, and looked like coverage until it was retargeted.

The pattern is "Revert fix → Run (MUST FAIL) → Restore → Run (pass)": a live revert against the
running suite, never a mental exercise.
It is required, not optional.

## Security defaults (fail-closed, untrusted input)
This repo already has standing rules for this — they stay undiscovered until Review unless you read
them here. **AGENTS.md's SAFETY property**: "Where a guarantee cannot be made on a platform, the
code REFUSES loudly rather than proceeding weakly — a silent half-guarantee is worse than a clear
'not supported here'." **The north-star's standing rule** (`.sdlc/context/north-star.md`, Standing
rules #2): a variable reaching a SQL fragment or filesystem path is untrusted by default. That rule
is scoped to SQL/paths specifically — the two checks below extend the same philosophy to two other
shapes that shipped as real bugs (#1761, #1765, #1767), each caught only by an independent
reviewer — Plan-Review before the code existed (#1767) or Review after it did (#1761, #1765) —
never by the implementer's own diff-time check. Run both against your own diff before calling a
step done:

1. **Auth/permission checks default to deny** on missing, malformed, or legacy input. A
   `dict.get(key)` with no default returns `None` (falsy) — a missing fact must read as "still
   required," never as "cleared." (#1761: an auth gate's live check reads its "must change the
   password" flag as `member.get(<flag>, True)` — the explicit `True` default is the fix.) A legacy
   or garbage value must never fall through to a *more* privileged branch than an unrecognized
   value would. (#1767: an accounts store's user-migration step nulls a legacy admin-looking role
   on a record that never had a real admin tier, rather than preserving a string that only looks
   privileged now.)
2. **Functions accepting "an id or a collection of ids" reject scalar-shaped values before
   iterating.** `str`, `bytes`, and `bytearray` are all iterable in Python, so an unwrapped scalar
   (`"p1"`) silently splits into characters or byte-ordinals instead of staying one id. (#1765:
   a metrics API's project filter guards with `isinstance(project_ids, (str, bytes,
   bytearray))` before ever treating the argument as a collection — the bug it replaced was a real
   cross-project data leak.)

## In the loop
Every step's claim needs evidence (`sigma-verify`); don't mark the goal done until its `done_when` is
verified. Prefer subagents for independent tasks where the host supports them.

---
name: agrim-implement
description: Implement the plan test-first and verify each step. Use in the Implement phase or /agrim-implement.
allowed-tools: Bash
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-implement

Detailed selection triggers: [selection](references/selection.md).

## Context-efficient inspection

Locate before opening: use `rg --files` for paths and `rg -n` for symbols or claims.
Read only the relevant lines with a bounded range; expand outward only when the
boundary is unclear. Do not dump whole files or broad search output into context.
For bulky tests or diagnostics, save full output to a scratch file, then inspect
its exit status and focused matches or a bounded tail. Do not hide failures, and
keep secrets out of the scratch file. This is guidance, not a measured savings claim.

**Executor resolution (host-aware):**
- **Claude Code + `superpowers` installed** → prefer **`superpowers:test-driven-development`** +
  **`superpowers:executing-plans`** (their richer versions; where subagents exist,
  `superpowers:subagent-driven-development`).
- **Otherwise** (Cursor / any host / no companion) → use this. Same discipline, portable.

**The `## Security defaults (fail-closed, untrusted input)` section below applies regardless of which executor ran
the TDD mechanics above** — it is not part of either companion's own red/green/refactor discipline,
and neither companion carries an equivalent today. Read it even if you jumped straight to the
companion skill.

**`## Mutation testing (prove the guard, don't assert it)` further down is the same kind of cross-cutting addition and applies regardless too** — read it even if you jumped straight to
the companion skill. Unlike Security defaults, this is not a total gap on Claude Code:
`superpowers:verification-before-completion` already states a narrower form of half of it, and
`superpowers:test-driven-development`'s own `writing-good-tests.md` states a mental version of the
other half. Neither names it as a standing, generalized, expected step the way this section does,
and neither carries the source citations in `## Where these rules come from` below. Read it anyway.

**`## Announce each step` applies regardless too** — it is Sigma's console banner, not TDD
mechanics, so no companion skill carries it. Read it even if you jumped straight to the companion.

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
   The corrected test's own docstring records an independent post-implementation review catching
   it; the fix added a standing assertion so it can't silently drift back.
2. **GREEN** — write the *minimal* code to pass. Run it. All green.
3. **REFACTOR** — clean up while staying green. "Clean up" is not a feeling; it is this list, and it
   is the same list `agrim-review` axis 6 will judge by. Long Method, Large Class, Long Parameter List,
   Primitive Obsession (a unit doing too much) · Feature Envy, Inappropriate Intimacy, Message Chains,
   Middle Man (units too entangled) · Duplicate Code, Dead Code, Speculative Generality (code that
   should not exist). Fix it now, while the context is in your head and the diff is yours.

After writing the plan's `## Tests` entries, record both stages through Sigma:

```sh
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/loop.py" verify .sdlc "$goal"
```

Run it while planned tests fail by assertion and again after green. Plain commands do not record
proof; changed tests earn red again. For refactors: break, red, restore, green.

Skip TDD only for prototypes, generated code, or config; say so. With
`verify.enforce`, `work.py pr --no-tests <reason>` is a verbatim PR-body exception;
it bypasses only test-first proof, never other gates.

## Sources
Kent Beck defines TDD; Martin Fowler names refactor smells.

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
  became a lie. This is `agrim-review` axis 7, and it is the finding that most often reaches review
  because nobody looked here first.

## Execute the plan, step by step
1. **Load** the plan and **review it critically** — raise any concern *before* starting (last cheap
   moment to catch a bad plan; it should already have passed `agrim-plan-review`).
2. For **each step**: announce it (`## Announce each step` below), do exactly what it says (steps
   are bite-sized), **verify** it (run the check — see `agrim-verify`), then move on. Track progress
   as you go.
3. **Park, don't force** on anything irreversible or blocked (matches the loop's park rule).

## Announce each step
When this phase runs for a known goal, print each plan step's banner as you start it:

```
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/phase_report.py" step .sdlc "<goal>" implement \
  --num <n> --total <step count> --phase-model --brief "<what this step does>"
```

It prints `🟦 STEP <n>/<m> · #<goal> <title>`, then `🟦 P5 IMPLEMENT · <brief>`, then
`requested host model: <id|unrecorded> · predicted model tier: <tier>`. The tier is the one
`phase_report.py start` recorded for this phase, marked `(phase)`; if
this step was resolved a tier of its own (`predict.py resolve-step`), pass `--model <tier>` and it is
marked `(step)`. `--phase-model` asserts that this phase agent itself executes the step, so its
requested host ID can be read from the phase start marker. For a separately dispatched step,
replace that flag with both `--model <step-tier>` and `--host-model <exact-host-model-id>` from the
step's dispatch call. Both are required so the banner cannot borrow the phase's tier either.
Without either, the step prints `unrecorded` rather than guessing from a matching tier. This is
the requested model. `phase_report.py end` checks the phase agent's observed model, not a separately
dispatched step's model. It writes nothing — no marker, no
ledger event — so it cannot disturb the elapsed
time and cost `end` measures. `--total` is optional; `--title` is the goal's title, as on
`start`/`end`. `.sdlc` means the main checkout's: from a goal worktree use the absolute path your
dispatch prompt gave you, or the tier reads `unrecorded` (and in github mode the title is lost too).
The brief is capped at 88 characters. Best-effort like every banner — skip it with no goal id (a
standalone `/agrim-implement`), and never let it block a step.

## Mutation testing (prove the guard, don't assert it)
A green suite says a guard exists; only reverting it and watching the right test fail proves it
*works* — the same distinction `.sdlc/context/north-star.md` Standing rule #1 makes for any prose
claim ("A claim in prose is not evidence. Execute it") and AGENTS.md's RELIABILITY property states
in the same breath: "it does what it claims, and its claims are measured."

**After GREEN, for any non-trivial guard or check** — a validation, a concurrency check, a
pagination boundary, anything whose whole job is to reject or correct something — revert the
specific line the fix depends on, re-run the relevant test, confirm it now fails, then restore and
confirm green again. Two real examples, each verified against its own PR record before being cited
here:
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

`superpowers:verification-before-completion` already states a narrower version of this —
"Revert fix → Run (MUST FAIL) → Restore → Run (pass)" — as one pattern among several, scoped to
regression tests specifically. `superpowers:test-driven-development`'s own `writing-good-tests.md`
has a "Mutation Check" too, but as a mental exercise ("mentally mutate the production code"), never
a live revert. This section names the same action explicitly, generalizes it beyond regression
tests to any non-trivial guard, makes the revert a real action against the running suite rather
than a thought experiment, and makes it a standing, expected step rather than one option in a list.

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
Every step's claim needs evidence (`agrim-verify`); don't mark the goal done until its `done_when` is
verified. Prefer subagents for independent tasks where the host supports them.

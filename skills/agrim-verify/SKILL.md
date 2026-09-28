---
name: agrim-verify
description: Run checks and show evidence before claiming a goal done. Use after implementation/review or /agrim-verify.
allowed-tools: Bash
---

# agrim-verify

Detailed selection triggers: [selection](references/selection.md).

## Context-efficient inspection

Locate before opening: use `rg --files` for paths and `rg -n` for symbols or claims.
Read only the relevant lines with a bounded range; expand outward only when the
boundary is unclear. Do not dump whole files or broad search output into context.
For bulky tests or diagnostics, save full output to a scratch file, then inspect
its exit status and focused matches or a bounded tail. Do not hide failures, and
keep secrets out of the scratch file. This is guidance, not a measured savings claim.

**Executor resolution (host-aware):**
- **Claude Code + `superpowers` installed** → prefer **`superpowers:verification-before-completion`** (its richer version).
- **Otherwise** (Cursor / any host / no companion) → use this. Same discipline, portable.

## The rule
**Evidence before claims — always.** Claiming work is complete without verifying it isn't efficiency,
it's dishonesty. **No completion claim without fresh verification output in this turn** — if you didn't
run it just now, you can't claim it. (Different words don't exempt you: spirit over letter.)

## The gate — run this before ANY "done / fixed / passing"
1. **Identify** the command that proves the claim — the repo's tests/build/lint, or `.sdlc/project.md`
   → **Verify command**.
2. **Run** it fresh and in full.
3. **Read** the actual output — exit code, failure count.
4. **Verify** the output confirms the claim. If it doesn't, state the *real* status *with* the output —
   don't hedge.
5. **Only then** make the claim, and make it *with* the evidence.

## What each claim actually requires
| Claim | Proof required | Not enough |
|---|---|---|
| Tests pass | test output: 0 failures | "should pass" / a previous run |
| Lint / type clean | linter output: 0 errors | a partial check |
| Build works | build: exit 0 | "the linter passed" |
| **Bug fixed** | reproduce it (**red**) → fix → it's gone (**green**) | "changed the code, assume fixed" |
| A subagent finished | check the diff yourself | the agent's "success" report |
| Goal done | its `done_when` met, with evidence | "tests pass, close enough" |

## Red flags — stop and verify
"should" / "probably" / "seems to" · premature "Great! / Perfect! / Done!" · about to commit/PR without
running it · trusting a subagent's report · "just this once" / "I'm confident" (confidence ≠ evidence) ·
**any wording that implies success without having run the check.**

## In the loop
With `config.verify.enforce` on, this gate is MACHINE-CHECKED: `loop.py verify .sdlc <goal>` records the evidence and `record done` is refused without it. Never `loop.py record … done` — or tell the user a goal is done — until its `done_when` is verified with
output. A parked or failed check is an honest outcome; a false "done" is not.

# Executor parity — implement (`sigma-implement` vs `superpowers:test-driven-development` + `executing-plans`)

`sigma-implement` is Sigma's portable fallback for the Implement phase, used when `superpowers` isn't
present. superpowers stays **preferred on Claude**. This comparison keeps the fallback **at par or better**.

## vs `test-driven-development`
| Dimension | superpowers | sigma-implement | Verdict |
|---|---|---|---|
| Iron Law — no production code without a failing test | ✓ | ✓ | **par** |
| Watch it fail *for the right reason* | ✓ | ✓ | **par** |
| Red → Green → Refactor cycle | ✓ (with graph) | ✓ (3 steps) | **par** |
| Minimal code to pass | ✓ | ✓ | **par** |
| When-to-use + exceptions | ✓ | ✓ | **par** |
| Anti-patterns | ✓ full 8 KB doc (5 patterns + gate functions) | ~ 4 condensed rules (mocks, test-only methods, mock-understanding, integration) | **slightly lighter** |
| Refactor step names the smells to remove (Fowler cohesion / coupling / dispensables) | ~ "refactor" as a cycle step | ✓ named list, shared verbatim with `sigma-review` axis 6 | **better** |
| Readability at write time (names, why-comments, doc the diff just falsified) | ✗ | ✓ | **better** |

The two **better** rows are not extra scope — they are the same axes `sigma-review` judges by, moved to
the moment they are cheapest to fix. The shared vocabulary is the point: an author who cleans up in the
reviewer's own words is not guessing what will come back. A test asserts the terms stay greppable in
both skills, so the two cannot drift apart silently.

## vs `executing-plans`
| Dimension | superpowers | sigma-implement | Verdict |
|---|---|---|---|
| Load + review the plan critically first | ✓ | ✓ | **par** |
| Step-by-step, verify each | ✓ | ✓ | **par** |
| Stop/ask on blockers | ✓ | ✓ (park-and-continue) | **par** |
| Subagent preference where available | ✓ | ✓ (noted) | **par** |
| Loop integration (verify each step via `sigma-verify`; gate `done_when`) | ✗ (host-agnostic) | ✓ | **better** |

**Net:** **par** on the TDD + plan-execution disciplines, **better** on loop integration, **slightly
lighter** only on the exhaustive anti-pattern catalogue (5 gate-functions → 4 condensed rules). Every
load-bearing rule is preserved; superpowers remains the richer choice on Claude.

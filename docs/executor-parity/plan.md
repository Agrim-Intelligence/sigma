# Executor parity — plan (`agrim-plan` vs `superpowers:writing-plans`)

`agrim-plan` is Sigma's portable fallback for the Plan phase; superpowers stays **preferred on
Claude**. Comparison so the fallback is **at par or better**.

| Dimension | superpowers:writing-plans | agrim-plan | Verdict |
|---|---|---|---|
| Plan for a zero-context reader; spell everything out | ✓ | ✓ | **par** |
| DRY / YAGNI / TDD / small verifiable increments | ✓ | ✓ | **par** |
| Scope check (split multi-subsystem specs) | ✓ | ✓ | **par** |
| File structure — responsibilities, small focused files, follow patterns | ✓ | ✓ | **par** |
| Task right-sizing (own test cycle + reviewer gate; independently testable) | ✓ | ✓ | **par** |
| Bite-sized steps (write test → fail → minimal → pass → green) | ✓ | ✓ | **par** |
| Definition of done | ~ implied | ✓ explicit, mapped to `done_when` + `agrim-verify` | **better** |
| Hand-off to a plan-review gate | ~ external | ✓ `agrim-plan-review` (never skipped) | **better** |
| Required plan-document-header template | ✓ fixed format | ~ not mandated | **slightly lighter** |

**Net:** **par** on the planning discipline, **better** on the definition-of-done + plan-review/verify
integration, **slightly lighter** only on the fixed plan-document-header template. Nothing load-bearing
is lost.

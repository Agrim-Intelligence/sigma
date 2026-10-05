# Executor parity — plan (`sigma-plan` vs `superpowers:writing-plans`)

`sigma-plan` is Sigma's portable fallback for the Plan phase; superpowers stays **preferred on
Claude**. Comparison so the fallback is **at par, better or lighter** (stated per row).

| Dimension | superpowers:writing-plans | sigma-plan | Verdict |
|---|---|---|---|
| Plan for a zero-context reader; spell everything out | ✓ | ~ not stated in the current skill | **lighter** |
| TDD / small verifiable increments | ✓ | ✓ (red-green steps, task right-sizing) | **par** |
| DRY / YAGNI | ✓ | ✗ wording absent from the current skill | **lighter** |
| Scope check (split multi-subsystem specs) | ✓ | ✓ | **par** |
| File structure — responsibilities, small focused files, follow patterns | ✓ | ✓ | **par** |
| Task right-sizing (own test cycle + reviewer gate; independently testable) | ✓ | ✓ | **par** |
| Bite-sized steps (write test → fail → minimal → pass → green) | ✓ | ✓ | **par** |
| Definition of done | ~ implied | ✓ explicit, mapped to `done_when` + `sigma-verify` | **better** |
| Hand-off to a plan-review gate | ~ external | ✓ `sigma-plan-review` (never skipped) | **better** |
| Required plan-document-header template | ✓ fixed format | ~ not mandated | **slightly lighter** |

**Net:** **par** on scope check, file structure, task sizing and the red-green step discipline; **better** on
the definition-of-done + plan-review/verify integration; **lighter** on the zero-context-reader and DRY/YAGNI
wording and on the fixed plan-document-header template, none of which the current `sigma-plan` text states.

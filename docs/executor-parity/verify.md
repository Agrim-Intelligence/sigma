# Executor parity — verification (`agrim-verify` vs `superpowers:verification-before-completion`)

`agrim-verify` is Sigma's **portable fallback**, used when `superpowers` isn't present (non-Claude
hosts, or the companion uninstalled). superpowers stays **preferred on Claude**. This point-by-point
comparison exists so the fallback is **at par or better** — no quality lost when superpowers is absent.

| Dimension | superpowers:verification-before-completion | agrim-verify | Verdict |
|---|---|---|---|
| Core principle — evidence before claims | ✓ "The Iron Law" | ✓ stated up front, same rule | **par** |
| The gate (identify → run → read → verify → claim) | ✓ 5-step gate | ✓ same 5 steps | **par** |
| Claim → proof table | ✓ 7 rows | ✓ 6 rows, incl. red-green + "goal done" | **par** |
| Red-green cycle for bug fixes | ✓ | ✓ | **par** |
| Verify a subagent's output independently | ✓ | ✓ | **par** |
| Spirit-over-letter ("different words don't exempt you") | ✓ | ✓ | **par** |
| Red flags / anti-rationalization | ✓ two full tables (red-flags + excuse→reality) | ~ condensed to one red-flags line | **slightly lighter** |
| Loop integration (gate `record done` on evidence; goal `verify_command` / config `verify.command`) | ✗ (host-agnostic) | ✓ ties to `loop.py record` + the project's verify command | **better** (Sigma-native) |

**Net:** **at par** on the whole discipline (every proof-requirement + the red-green cycle preserved),
**better** on loop integration, **slightly lighter** only on the exhaustive excuse-rebuttal tables —
acceptable for a fallback, and superpowers remains the richer choice on Claude. No gap weakens the
guarantee.

# Growth-store audit

This is an evidence snapshot, not a retention-policy claim. `growth_audit.py`
was run against repository revision `85e3ba59964d5ae42a837080af13f464fded4443`
on 2026-10-01. It resolves the durable writer APIs below instead of guessing
their callers' private paths. No host root was measured: host measurement is an
explicit, one-named-root opt-in and this run did not receive one.

Sizes are regular-file byte totals in this worktree. The 10x and 100x columns
are simple current-size multiplication, not forecasts. A zero means that the
store was absent in this worktree at measurement time, not that it can never
grow.

| Path pattern | Writer | Growth per event | Cap or pruner | Size now (B) | 10x (B) | 100x (B) | Evidence-backed decision |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| `.sdlc/events/<actor>-<writer>.jsonl` | `skills/agrim-loop/scripts/ledger.py:1550` | one JSONL event append | 30-day journal retention, `ledger.py:1132` | 0 | 0 | 0 | Retention is already implemented; no new pruner is claimed. |
| `.sdlc/ledger/entries/<actor>-<writer>.jsonl` | `skills/agrim-loop/scripts/ledger.py:1550` | one JSONL entry append | none found | 0 | 0 | 0 | Uncapped row recorded; this slice makes no unverified retention decision. |
| `.sdlc/state/log/<goal>.jsonl` | `skills/agrim-loop/scripts/actionlog.py:362` | one JSONL action append | none found | 0 | 0 | 0 | Uncapped row recorded; this slice makes no unverified retention decision. |
| `.sdlc/state/time/<goal>/<writer>.jsonl` | `skills/agrim-loop/scripts/timing_store.py:353` | one completed goal interval | 90-day retention, `timing_store.py:225` | 0 | 0 | 0 | Retention is already implemented; no new pruner is claimed. |
| `.sdlc/state/time/_sessions/<session>/turns.jsonl` | `skills/agrim-loop/scripts/timing_store.py:398` | one shared session interval | 90-day retention, `timing_store.py:225` | 0 | 0 | 0 | Retention is already implemented; no new pruner is claimed. |
| `.sdlc/state/time/_sessions/<session>/<writer>.jsonl` | `skills/agrim-loop/scripts/timing_store.py:398` | one process-local session interval | 90-day retention, `timing_store.py:225` | 1,965 | 19,650 | 196,500 | Retention is already implemented; no new pruner is claimed. |
| `.sdlc/state/witness/<goal>.jsonl` | `skills/agrim-loop/scripts/witness.py:158` | one red/green witness append | none found | 0 | 0 | 0 | Uncapped row recorded; this slice makes no unverified retention decision. |

The review-copy store is handled separately by the terminal work lifecycle:
`work.prune_terminal_review_copies` removes only direct `rv*/wt` copies after a
goal reaches a terminal outcome, retaining text evidence. It is not a scanner
row because the writer is review procedure rather than a durable Python writer
API.

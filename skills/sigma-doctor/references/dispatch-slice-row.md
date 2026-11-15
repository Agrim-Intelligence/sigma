# The slice dispatch row: what it gates on, and what it cannot see

Detail for the `dispatch model log coverage — slice` row in `SKILL.md`.

Two differences from the phase row: it gates on `action_log.enabled`, not `journal.enabled` (its
data is local-log-only, no ledger dependency); and it can't see a slice that ran with zero log
entries at all, or tell apart two slices that both omitted `--thread` (a `--thread` compliance gap
of its own, out of scope here) — named limitations, not silently equivalent to the phase row's
ledger-backed coverage.

`--role goal-slot` gets no row: #2514 deliberately excluded it from the `--model` requirement (its
tier resolves *inside* the dispatched subagent, after dispatch — nothing to pass at the call site),
so nothing requires `--model` there on `main` today and a row would only produce false gaps. #2557
tracks adding one once that requirement lands.

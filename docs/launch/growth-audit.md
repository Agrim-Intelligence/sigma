# Growth-store audit

This is a measured inventory, not a retention-policy claim. The complete,
deduplicated scan rows are committed beside this table in
`docs/launch/growth-audit.json`; the table is deliberately an index so a new
writer cannot disappear behind a hand-maintained allowlist.

## Reproduce

From the repository root, run the documented gesture exactly:

```sh
python3 tools/readiness/growth_audit.py . --measure-sdlc --b6-issue 419 --json docs/launch/growth-audit.json
```

The committed snapshot was regenerated on 2026-10-13 at revision
`1b8ad514d51f9208b816c83e93f10b07e1e15108` (the first one, 2026-10-01 at `64709b39`, is superseded). It
measured a clean checkout's tracked `.sdlc` at **4,130,541 bytes**. The SHA is part of the JSON result, so a later
checkout cannot present this byte count as current. `--measure-host ROOT`
remains a separate named-root opt-in; it never walks a home directory.

| Scanner source | Full rows | Unique path patterns | Measurement / dedup decision |
| --- | ---: | ---: | --- |
| Python durable-writer calls | 589 | 135 | Every statically resolvable direct destination and helper-return destination is recorded with source line. Before a B6 issue is filed, its exact row must be re-measured at the current SHA and deduplicated against an open B6 issue. |
| Skill prose write gestures | 227 | 76 | The documented paths are retained as host-agnostic procedural evidence, not treated as proof a filesystem write occurred. Their rows use the same `(pattern, writer, source)` dedup key. |
| Combined scan | 816 | 187 | `growth-audit.json` is sorted by path, writer, source; it is the reviewable source of truth for the table counts. |

The scanner follows source-discovered helper returns and writer-helper call
chains rather than a hard-coded writer allowlist. Its current rows include
claim locks/markers, verify evidence, work records and worktrees, review
generation evidence and results, ledger/event files,
and terminal review-copy paths where the source declares them. A path that is
not statically resolvable is not silently mapped to an invented retention
policy.

## Unresolved-store disposition

The audit records every unique path pattern without a source-proven retention
mapping in the committed JSON's `b6_disposition.unresolved_patterns` array.
At the snapshot's revision no pattern is unresolved. The first pass carried all 177 under B6 issue
#419, which is closed; the 11 patterns that pass had left open were read from their writer code and decided
in `dispositions/1261.json`, with the evidence in [b6-late-stores.md](b6-late-stores.md): six patterns bounded or
capped (one of them capped by that change), two unresolvable placeholder patterns, and three patterns with
no pruner or cap, filed as accepted gaps #1262 (two patterns) and #1263; a fourth gap, #1264, is a store the scan
cannot see. The JSON's `b6_disposition.issue`
still reads `#419` because the documented gesture passes it; it is a label, not a claim that #419 is open.
The disposition files do not authorize pruning host configuration roots.

**Zero unresolved is not full coverage.** The scanner does not follow constant-built paths (`*_REL`
constants such as `STORE_REL`), so stores written through them never reach the table. Known ones, listed
with their status in [b6-late-stores.md](b6-late-stores.md): the reconcile proposals
(`.sdlc/plans/reconcile/`, unbounded, #1267), `state/unit-approvals/` (#1264), `state/upkeep/level2/<unit>.json`
(intentionally unbounded per unit), and several apparently bounded ones (board mirror, AI-filed counter,
`gh-fallback.json`, `gh-rest-breaker.json`, `upkeep/acks.json`, `upkeep/last_start.json`).

## Per-slice dispositions

A pattern is resolved by a disposition file, not by code. Each B6 slice adds its own
`docs/launch/dispositions/<slice>.json` and never edits `tools/readiness/growth_audit.py`.
A file is a JSON array of entries, one per resolved store pattern:

```json
[
  {
    "pattern": ".sdlc/state/log/<goal>.jsonl",
    "issue": "#123",
    "decision": "capped",
    "pruner_or_cap": "keep newest 50 per goal",
    "evidence": "path or command that proves the pruner or cap"
  }
]
```

All five keys are required non-empty strings; `issue` is `#<digits>`. The scan's pattern
text must match exactly. A resolved pattern is omitted from `b6_disposition.unresolved_patterns`,
and its `store_measurements` row carries the entry's `pruner_or_cap` and `decision`; its size
and growth columns are still measured. With no `dispositions/` directory nothing is resolved.
`review-copy.json` is the first entry (the terminal review-copy prune); `461.json` resolves the event journal, time store and ledger stream rows, with measurements and the unbounded-ledger decision in [retention-event-time-ledger.md](retention-event-time-ledger.md). `460.json` resolves the Slack listener and supervisor log rows (size-capped rotation, truncation, and the launchd file Sigma does not own) in [b6-slack-supervisor-logs.md](b6-slack-supervisor-logs.md). `462.json` resolves the committed plan, research and acceptance rows (intentionally unbounded, with a tested per-file and per-goal ceiling) in [b6-committed-work-records.md](b6-committed-work-records.md). `465.json` resolves the per-goal worktree rows (`.sdlc/work/<goal>`, `.sdlc/work/<n>`) and the sweep's own lock and rotation file (a sweep that removes only provably finished checkouts, opt-in when automatic) in [b6-worktrees.md](b6-worktrees.md). `466.json` resolves the fixed-name singleton, cursor, inbox, embeddings-cache, prose-only and `<home>/` rows (capped, intentionally unbounded with a stated ceiling, or out of scope as host-owned) in [b6-singletons-and-home.md](b6-singletons-and-home.md). `463.json` resolves the other records and knowledge-artefact rows (standing documents, registry, goals, journey, reports, the knowledge corpus and web captures, and the evidence directory), each decided from its writer, in [b6-knowledge-and-records.md](b6-knowledge-and-records.md).

The audit validates every file on every run, before it writes anything, and exits 2 with
`growth_audit.py: REFUSED: <file>[<index>]: <why>` on stderr (a pre-existing `--json` file is left
untouched) for: invalid JSON or duplicate JSON keys, a top level that is not an array, a missing,
extra, empty or non-string field, a bad `issue`, a file in the directory not named `*.json` (dotfiles such as `.DS_Store` are ignored), the same pattern twice (in one file or across
files), and a pattern the scan does not produce. If a writer is removed or refactored, its entry
starts failing the audit; delete or fix that file.

`"unscanned": true` is the one waiver, accepted only as the literal `true`: it lets an entry name
a source-proven store whose writer the scan cannot see (the review-copy checkout is written outside
Sigma's Python and skill prose). It bypasses the unknown-pattern guard for that entry, so the
`evidence` must name the writer and a reviewer of the slice must check it. It resolves a row only
when the scan produces that pattern, and each run prints a stderr note for an unscanned entry that
matched no row.

### Control

The guard is checked with the same gesture as regeneration, in a scratch copy of the repository so
the committed snapshot is not overwritten: add `docs/launch/dispositions/bogus.json` with one valid
entry whose `pattern` is `.sdlc/nowhere/<goal>.json`, run the command from [Reproduce](#reproduce),
and expect exit 2, `REFUSED` on stderr and an unchanged `docs/launch/growth-audit.json`.
The disposition test module parses that command out of this page and runs it that way; deleting the
unknown-pattern check in `load_dispositions` turns its `test_unknown` red.

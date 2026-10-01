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

The committed snapshot was generated on 2026-10-01 at revision
`64709b3971a5ef0f3e59b004c1b5237af8c27e6d`. It measured this checkout's
`.sdlc` at **566,609 bytes**. The SHA is part of the JSON result, so a later
checkout cannot present this byte count as current. `--measure-host ROOT`
remains a separate named-root opt-in; it never walks a home directory.

| Scanner source | Full rows | Unique path patterns | Measurement / dedup decision |
| --- | ---: | ---: | --- |
| Python durable-writer calls | 521 | 124 | Every statically resolvable direct destination and helper-return destination is recorded with source line. Before a B6 issue is filed, its exact row must be re-measured at the current SHA and deduplicated against an open B6 issue. |
| Skill prose write gestures | 222 | 77 | The documented paths are retained as host-agnostic procedural evidence, not treated as proof a filesystem write occurred. Their rows use the same `(pattern, writer, source)` dedup key. |
| Combined scan | 743 | 177 | `growth-audit.json` is sorted by path, writer, source; it is the reviewable source of truth for the table counts. |

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
Its 177 rows are measured at the SHA above and are all carried by
[B6 issue #419](https://github.com/Agrim-Intelligence/sigma/issues/419), not
silently exempted or given a fabricated pruner. Before filing, the local
dedup control found no candidate and a direct GitHub title/body search found
no open B6 retention/cap issue (the only related hit was closed #150, which is
limited to abandoned phase markers). The issue has `readiness` and
`launch:blocker` labels and requires a narrower, measured ownership decision
before any deletion. It does not authorize pruning host configuration roots.

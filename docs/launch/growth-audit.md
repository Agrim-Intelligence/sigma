# Growth-store audit

This is a measured inventory, not a retention-policy claim. The complete,
deduplicated scan rows are committed beside this table in
`docs/launch/growth-audit.json`; the table is deliberately an index so a new
writer cannot disappear behind a hand-maintained allowlist.

## Reproduce

From the repository root, run the documented gesture exactly:

```sh
python3 tools/readiness/growth_audit.py . --measure-sdlc --json docs/launch/growth-audit.json
```

The committed snapshot was generated on 2026-10-01 at revision
`b47768434ced603759b7f891605be2d334bcf585`. It measured this checkout's
`.sdlc` at **562,531 bytes**. The SHA is part of the JSON result, so a later
checkout cannot present this byte count as current. `--measure-host ROOT`
remains a separate named-root opt-in; it never walks a home directory.

| Scanner source | Full rows | Unique path patterns | Measurement / dedup decision |
| --- | ---: | ---: | --- |
| Python durable-writer calls | 509 | 117 | Every statically resolvable direct destination and helper-return destination is recorded with source line. Before a B6 issue is filed, its exact row must be re-measured at the current SHA and deduplicated against an open B6 issue. |
| Skill prose write gestures | 222 | 77 | The documented paths are retained as host-agnostic procedural evidence, not treated as proof a filesystem write occurred. Their rows use the same `(pattern, writer, source)` dedup key. |
| Combined scan | 731 | 194 | `growth-audit.json` is sorted by path, writer, source; it is the reviewable source of truth for the table counts. No B6 issue was filed in this slice. |

The scanner follows source-discovered helper returns and writer-helper call
chains rather than a hard-coded writer allowlist. Its current rows include
claim locks/markers, verify evidence, work records and worktrees, review
generation evidence and results, ledger/event files,
and terminal review-copy paths where the source declares them. A path that is
not statically resolvable is not silently mapped to an invented retention
policy; the next audit must supply measured evidence before any B6 decision.

**Priority:** P1
**Spec:** [`docs/superpowers/specs/2026-07-30-sigma-viz-data-platform-design.md`](../blob/main/docs/superpowers/specs/2026-07-30-sigma-viz-data-platform-design.md)
**Backlog source:** [`docs/viz-backlog.json`](../blob/main/docs/viz-backlog.json)

Everything downstream reads the store. The gatherer adapter (spec §B.3.1) is the primary Class-1 interface and the reason 25 metrics need no instrumentation.

---

Tracking issue — **the loop never runs this**. Its stories carry `sdlc:goal` and are the loop's unit of work (one story = one worktree, branch, and PR).

## Stories

- [ ] #99 — E1.S1 DuckDB store bootstrap and schema
- [ ] #100 — E1.S2 Gatherer adapter keyed on schema string
- [ ] #101 — E1.S3 Ledger reader — entries and events streams
- [ ] #102 — E1.S4 Artifact reader — goals, plans, slices, config
- [ ] #103 — E1.S5 Git facts reader
- [ ] #104 — E1.S6 GitHub reader (optional, degrades)
- [ ] #105 — E1.S7 Incremental resume and idempotence
- [ ] #106 — E1.S8 viz ingest CLI with multi-repo glob


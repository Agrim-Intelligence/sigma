---
name: sigma-contract-check
description: Check public API, event, CLI, type, or environment-variable changes for breaking consumers. Use for contract changes or /sigma-contract-check.
allowed-tools: Bash, Read, Grep
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-contract-check

Detailed selection triggers: [selection](references/selection.md).

> Detect breaking changes to public APIs, event shapes, and exported types before the consumers do.

*A conditional-risk review orthogonal to `sigma-review`; always Sigma's own (no companion
equivalent).*

**Judge blast radius from the code, not the diff.** A "safe-looking" signature change is breaking if a
caller you never read depends on the old shape. Ground yourself in the project's declared contracts:
the north-star, the repo's `CLAUDE.md` (any FROZEN-contract rules), and the actual
schema/exported-type surfaces (OpenAPI / GraphQL / protobuf / package boundaries). Then `grep` every
consumer.

**Locate the north-star, never assume its path.** `.sdlc/` is gitignored, so it is absent from the
goal worktree this runs in (#1778), and this skill is handed no reviewer brief. Run
`python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/north_star.py"` and branch: `present <path>` read it;
`absent` is a drop-in project that has declared none — skip, silently; `unreachable` is neither —
**say so beside the classification**, because calling every contract `changed-safe` without reading
the declared ones is a false pass, and the FROZEN gate below depends on having read them.

## Goal
Given a diff, produce a list of every public contract changed, every consumer affected, and whether each
change is safe or breaking.

## Steps
1. Identify contracts in the diff: HTTP routes, event shapes, exported types, CLI flags, env vars.
2. For each: classify as `added` / `removed` / `changed-safe` / `changed-breaking`.
3. For each `removed` or `changed-breaking`: list the consumers (repos, services, teams) — by grep, not memory.
4. For each consumer: state the action required (notify, version bump, migrate-then-remove).
5. Propose a versioning / deprecation strategy if any breaking change remains unresolved.

## Gates
- Every contract change is classified.
- Every breaking change has a named consumer list (or "no known consumers — confirmed by grep").
- Every breaking change has a rollout plan (versioned, notified, or migrate-first).

## Stop when
- Consumer ownership is unclear → **park the goal for a human** rather than guess.
- The contract is cross-org / a FROZEN contract → park and flag a human owner before any further work.

## Output → render the report, and persist it if you want it retained
Write to `.sdlc/reviews/contract-check-<slug>.md` (NOT under `.sdlc/knowledge/`, which is gitignored).

```markdown
# contract diff · <slug>

## summary
<N> changes · <M> breaking · ready: <yes / no>

## changes
- <kind>: <name>
  classification: <added / removed / changed-safe / changed-breaking>
  consumers: <list or "none — confirmed by grep">
  action: <notify / version-bump / migrate-then-remove>

## rollout
<paragraph: order, timing, deprecation window>
```

---
name: agrim-migration-check
description: Check forward migration, backfill, rollback, and canary for schema or data changes. Use for migration diffs or /agrim-migration-check.
allowed-tools: Bash, Read, Grep
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-migration-check

Detailed selection triggers: [selection](references/selection.md).

> For any DB change: forward path, backfill, rollback, canary read-write.

*A conditional-risk review orthogonal to `agrim-review`; always Sigma's own (no companion
equivalent).*

**Read the actual migration and the schema it mutates**, plus prior migrations in the same area — the
risk is in the interaction with data already there, not the DDL in isolation. Ground yourself in the
north-star and the repo's `CLAUDE.md` for any data/ownership rules first — locate the north-star with
`python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/north_star.py"`, never by its bare path: `.sdlc/` is
gitignored, so it is absent from the goal worktree this runs in (#1778).

## Goal
Take a proposed schema or data migration and produce a plan covering forward, backfill, rollback, and the
canary path.

## Steps
1. State the goal of the migration in one sentence.
2. **Forward path**: exact order of operations. Reversible? Idempotent?
3. **Backfill**: needed? Strategy (batched? throttled?). Estimated time.
4. **Rollback**: exact steps. Data-preserving or destructive?
5. **Canary**: which one row / one tenant / one shard you touch first.
6. **App compatibility**: does the app handle both old and new shape during rollout?
7. **Lock impact**: blocking operations? Estimated lock window? Off-peak required?

## Gates
- Every step in the forward path is reversible OR explicitly marked one-way with justification.
- A backfill plan exists for every non-nullable column addition.
- Rollback steps are written out, not "we will figure it out".
- "App handles both shapes" is confirmed true for the full duration of rollout.

## Stop when
- The migration would lock a hot table beyond the project's stated threshold → **park for a human**.
- Rollback would lose data → park and require a human's written sign-off before proceeding.
- The change is to a table with active billing or auth records → park and flag a human owner.

## Output → render the report, and persist it if you want it retained
Write to `.sdlc/reviews/migration-check-<slug>.md` (NOT under `.sdlc/knowledge/`, which is gitignored).

```markdown
# migration · <slug>

## goal
<one sentence>

## forward
1. <step>
2. <step>

## backfill
- needed: <yes/no>
- strategy: <batch size, throttle, est. duration>

## rollback
1. <step>

## canary
- first target: <one row / tenant / shard>
- verify: <how you confirm it worked>

## compatibility
- app handles old shape: <until when>
- app handles new shape: <from when>

## locks & timing
- locks: <yes/no, which tables, est. duration>
- recommended window: <peak / off-peak>
```

# Changelog

All notable changes to Sigma are recorded here, newest first.

## Unreleased

- Feature-branch rebase upkeep no longer deletes branch content when the base holds a revert of the
  branch's own commits (#144). Before it pushes, upkeep now compares the branch tip's tree with the
  replayed tree. If any tracked path would disappear, or would be rolled back to a version the
  branch's own history already moved past (a reverted edit, or an undone rename), it refuses with
  the new `would-drop` outcome and pushes nothing. Base renames and ordinary base edits are allowed,
  and the branch's own deletions never count. The pick line says `was NOT rebased` and names the
  paths, a tracked issue is filed, and `/agrim-doctor` shows the unit as blocked until a clean pass
  clears it (not while `rebase_upkeep` is off or the unit is closed). `feature_rebase.py upkeep`
  exits 1 on it, and `rebase_brief.py rebase` runs the same check before its own force-push. The
  trade-offs: a plain upstream deletion, a move that rewrites past rename similarity, or a base
  reverting its own older change to a file the branch carries is refused the same way; a partial
  revert merged with other changes is not seen, and neither is a full base revert of a file the
  branch kept editing afterwards (the replay yields a version that never existed). The history read
  counts versions created by merge commits and the root commit, ignores chmod-only changes, pins its
  own git config so a user's `log.showRoot`/`log.diffMerges`/colour settings cannot switch it off,
  decodes paths as UTF-8, and fails closed after `SIGMA_REBASE_GUARD_TIMEOUT` seconds (default 120).
  The `agrim-rebase` skill's single push chokepoint, `rebase_brief.push_branch`, runs the same check
  against the commit the lease would overwrite, so `rebase_brief.py rebase`, Slack `--rebase`, the
  conflict walker's final push and its manual-recovery push all refuse a push that would lose
  content, naming the paths; paths a human resolved in the walk are that human's decision. See
  `docs/branching-model.md` §3b and §15.
- Upgrade path from the plugin's previous name (#239). A repository adopted under the previous
  name's 1.4.x releases now works under Sigma with no data loss. Sigma reads the old schema ids
  (features, landing, withheld and propagation records), the old feature-doc and Codex
  `AGENTS.md` markers, the old environment-variable prefix (`SIGMA_*` wins when both are set), the
  renamed `drift_watch.channels` key, and the old PR and issue markers, so an old `block` comment
  on an open PR still blocks it. One helper, `skills/agrim-loop/scripts/legacy.py`, does all of
  this reading.
- New: `skills/agrim-doctor/scripts/migrate.py`, a one-shot, idempotent rewrite of that state to
  Sigma's names. It is a dry run by default and writes only with `--apply`. It swaps text in place
  and checks each result by reading it back. It refuses (exit 2) anything it cannot rewrite with
  certainty, leaves history alone, lists environment variables by name only, and refuses to run
  while a watcher is live. See `docs/upgrading.md`.
- Cross-repository propagation no longer overwrites a sibling repository's registry file that
  still carries the old schema id.

## 1.0.0 — the first public release

The first release of the public core: a gated software development lifecycle for coding agents,
run from GitHub issues, with every phase reviewed before the next one starts.

- Seven gated phases for every goal: goal, research, plan, plan review, implement, review and
  retrospective. Each phase writes an artifact the next one reads, and a review verdict is needed
  before any code is edited.
- `/agrim-goal` runs one goal through all seven phases with an approval gate at each boundary, for
  supervised, end-to-end work.
- `/agrim-loop` drains a backlog autonomously: it claims goals, dispatches fresh phase agents, opens
  verified pull requests, and parks or continues each goal until stopped or out of budget.
- Work is tracked where it already lives: GitHub issues, a project board, and the `sdlc:*` label
  model (goal membership, in-progress, blocked, blocking, parked, needs-confirmation).
- `/agrim-doctor` checks the project setup, the dependencies and the host, and prints the fix
  command for anything not ready. `/agrim-init` scaffolds a project's `.sdlc/` layer and
  `/agrim-setup` adopts the kit into an existing repository.
- A local action log records what each goal did; `/agrim-log` reads it back as live status, and
  `/agrim-status` shows the backlog, the current iteration and the review queue.
- One renderer builds every status line, so a single glance names the goal, the phase and what is
  happening right now.
- Host-agnostic by design: the full pipeline is validated on Claude Code, and a Codex adapter runs
  the same skills and scripts there (a complete Codex goal through pull-request merge is still
  unverified). A Cursor adapter ships as experimental, not yet verified in a live Cursor session.
  Lifecycles live in the kit's own Python and in git, never in one host's hooks.
- Safe by default: nothing sends data off the machine, spawns a background process, or consumes
  quota without the operator opting in.
- Released under the MIT licence.

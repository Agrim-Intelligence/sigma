# Upgrading a repository adopted under the plugin's previous name

Sigma was published under a different name before its 1.0.0 release. A repository adopted under
that name (any 1.4.x release) carries the old name in its state. Sigma reads all of it, so you can
install Sigma and carry on with no migration. When you want the files themselves to stop carrying
the old name, run the one-shot migration.

The old name is not spelled out in this document. `migrate.py` prints it at the top of every run,
and the table below uses `<old>` for the name and `<OLD>_` for its environment-variable prefix.

## What Sigma reads without any migration

| State the old release wrote | Where | What Sigma does |
|---|---|---|
| `<old>/features@1`, `<old>/landing@1`, `<old>/withheld@1`, `<old>/propagation@1` schema ids | `.sdlc/features/`, `.sdlc/state/landing/`, `.sdlc/state/withheld/`, `.sdlc/state/propagation/` | Reads them as the Sigma ids. The next write uses the Sigma id. A different version (for example `@2`) is refused, exactly as Sigma's own `@2` is. |
| `<!-- <old>:begin managed … -->` / `<!-- <old>:end managed -->` | `.sdlc/features/<unit>.md` | Manages the block and respells its markers on the next sync. If the file also has a Sigma block, Sigma manages only its own block and leaves the other one alone. |
| `<!-- <old>:codex:start/end -->` | `AGENTS.md` | `/agrim-init --codex` replaces the old block in place. |
| `<OLD>_*` environment variables | your shell or launcher | Reads each operator setting under the old prefix when the `SIGMA_*` name is unset or empty. **`SIGMA_*` wins when both are set.** `SIGMA_RUN_ID` and `SIGMA_AUTOWATCH_HOP` are Sigma's own hand-off variables, so they are never read under the old prefix. |
| `*_env` config values naming `<OLD>_*` | `.sdlc/config.json` | Reads the `SIGMA_*` spelling first, then the named one. |
| `drift_watch.channels.<old>` | `.sdlc/config.json` | Reads it when `channels.sigma` is unset or blank. |
| `<old>:approve` / `block` / `unblock`, `keep-parked`, `dismissed-finding`, decomposition and design markers, Q&A blocks, flag watermarks | GitHub issue and PR bodies and comments | Recognises them. A `<old>:block` comment on an open PR still blocks it. |

The single reader for all of these is `skills/agrim-loop/scripts/legacy.py`.

## The one-shot migration

```
python3 skills/agrim-doctor/scripts/migrate.py .sdlc            # dry run: lists every change, writes nothing
python3 skills/agrim-doctor/scripts/migrate.py .sdlc --apply    # writes, then prints what it changed
```

The migration rewrites only these:
- the schema ids above;
- the feature-doc markers;
- the Codex block, which it regenerates with Sigma's text;
- in `config.json`, `*_env` values (to `SIGMA_*`) and the `drift_watch.channels` key.

Each rewrite swaps exact text in place. Line endings, key order and comments all survive. Each result
is checked by reading it back: JSON must parse to the original with only the listed values changed,
and a feature doc must keep the same body and digest. A file that fails that check, or that the
migration does not understand, is refused: it is left untouched and listed, and the command exits 2.

It is safe to run again. A second `--apply` prints `nothing to migrate`. Each write is atomic. A file
that changed after it was read is refused, so rerun the command. `--apply` refuses to run while this
`.sdlc`'s watcher is running, because that watcher may belong to the old plugin and be writing the
old spellings. Stop it first.

Some things are **left as is and listed**, on purpose:
- history: ledger entries and timing sessions;
- schema ids of kinds or versions Sigma does not know;
- a feature doc or `AGENTS.md` that carries both spellings, since the other block belongs to a
  teammate still on the old plugin;
- the old journal config block, because turning the journal on is an explicit opt-in that
  `/agrim-doctor` reports.

Environment variables are listed by **name only, never value**. Rename them where you set them.

## Switch the team together

The old plugin cannot read Sigma's spellings. Every file the migration changes is one the old plugin
also reads. Committed files (`config.json` and `.sdlc/features/`) reach every teammate through git.
Migrate once everyone runs Sigma. Until then, don't migrate: Sigma reads the old spellings, and both
plugins keep their own block in a feature doc rather than fight over one.

Cross-repository propagation follows the same rule. Sigma will not overwrite a sibling repository's
registry file while that file still carries the old schema id. Run the migration in that repository
first.

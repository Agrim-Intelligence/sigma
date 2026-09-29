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
Not migrating does not keep a mixed team safe. Sigma reads the old spellings, but on normal use it
rewrites registry files (`.sdlc/features/index.json` and the unit records under `units/`) with its
own schema id, printing a one-line `migrated legacy ... on use` notice when it does. The old plugin
reads those files as empty. So either switch every machine to Sigma and migrate together, or keep
the old plugin off the branches Sigma writes to until you do.

Cross-repository propagation follows the same rule. Sigma will not overwrite a sibling repository's
registry file while that file still carries the old schema id. Run the migration in that repository
first.

## Running both plugins on one repository

Both plugins register hooks, start a ledger watcher and write `.sdlc/`. Sigma detects the old
plugin and refuses to write alongside it, instead of silently sharing the state. The detector is
`skills/agrim-loop/scripts/coexist.py`:

```
python3 skills/agrim-loop/scripts/coexist.py check .sdlc    # exit 0: clear; exit 2: active
```

It counts the old plugin as **active** on this repository when any of these holds:
- Claude Code has `<old>@…` enabled in `enabledPlugins` and the plugin is installed for this
  repository on this machine (`plugins/installed_plugins.json`). The managed, local, project and
  user settings files are read in that order of precedence; `CLAUDE_CONFIG_DIR` is honoured, and
  comments and trailing commas in them are accepted. The managed file is
  `/Library/Application Support/ClaudeCode/managed-settings.json` on macOS,
  `/etc/claude-code/managed-settings.json` on Linux and
  `%ProgramData%\ClaudeCode\managed-settings.json` on Windows. When the install list cannot be
  read, an entry in the managed, local or user settings still counts as active (Sigma refuses when
  it cannot tell), while an entry only in the committed project settings is a note.
- Codex's `config.toml` sets `enabled = true` for `plugins."<old>@…"`, in any TOML spelling:
  `[plugins."…"]` or `[plugins.'…']` tables, dotted keys, or `"…" = { enabled = true }` under
  `[plugins]`. `CODEX_HOME` is honoured. Python 3.11+ reads the file with `tomllib`; Python 3.10
  uses a small built-in reader for these shapes.
- A hook whose command runs the old plugin is registered by hand in one of those settings files:
  a path in the command has a directory named exactly the old name, or contains the old plugin's
  recorded install path. A script of your own whose file name merely contains the name does not
  count.
- A live watcher holds this `.sdlc`'s watcher lock and Sigma did not start it. Sigma records its
  own watcher in `.sdlc/state/watch.owner`, and the old plugin never wrote that file. This counts as
  active when the watcher's command line names the old plugin (read from `/proc` on Linux), or,
  where the command line cannot be read (macOS, Windows), when another signal in this list is also
  present. The notes below never make it active.
- `.sdlc/state/owner.json` names a plugin other than Sigma. Sigma writes this file on init and
  on loop start.

These are reported as notes and never refused: the old plugin installed but not enabled here,
the old plugin enabled in settings but not installed for this repository on this machine, old
schema ids in `.sdlc/` (at most 200 files are read), an old-name Cursor rule or Codex
`AGENTS.md` block (committed text, not running code), and a watcher Sigma did not start with no
active signal beside it. A Sigma watcher started before this release looks exactly like that last
case, so upgrading a repository that only ever ran Sigma is never refused.

What each surface does:

| Surface | When the old plugin is active |
|---|---|
| `/agrim-init` (`sdlc_init.py`) | Refuses before writing anything (exit 2). |
| `loop.py start` | Refuses before writing anything (exit 2). |
| `watch_daemon.py` | Refuses (exit 2) and writes the reason to `.sdlc/state/watch.log`. |
| `loop.py`'s automatic watcher start | Does not start the watcher and prints one line on stderr. |
| `migrate.py --apply` | Refuses (exit 2). Migrating while the old plugin still runs leaves it reading empty state. |
| `/agrim-doctor` | A failing `coexistence` row. The check still completes. |
| `status.py` | A warning on stderr. The status line still prints. |
| Session-start hook (Claude Code) | Adds the same message to the session. The hook only speeds up detection. The refusals above are in Python and apply on every host, including Cursor, which has no hooks. |

`log.py` has no coexistence check. It reads only the local action log and imports nothing from
`agrim-loop`.

Two watchers on one `.sdlc` cannot run whatever you set. Both plugins' watchers take the same lock
files (`state/watch.pid`, `state/watch.heartbeat`, `state/watch.decide.lock`). When the lock is
held by a watcher Sigma did not start, Sigma says so instead of only reporting "already running".

The refusal message lists what was found and the fix:
1. Disable one plugin for this repository. For Claude Code, set `"<old>@<marketplace>": false`
   under `enabledPlugins` in the repository's `.claude/settings.local.json`, which applies to this
   repository and this machine only. To disable it everywhere, run
   `claude plugin disable <old>@<marketplace>`. For Codex, set `enabled = false` under its
   `[plugins."…"]` table in `config.toml`.
2. If the old plugin's watcher is still running, stop it. Create `.sdlc/state/watch.stop` and wait
   one tick. Disable the plugin first, or its triggers start the watcher again.
3. Run the migration above with `--apply`.

**The override.** `SIGMA_ALLOW_COEXIST=1` (exactly `1`) lets the write surfaces continue, each with
a warning, when you know both plugins will act on this repository. It is read under the Sigma name
only, never the old prefix. It does not let a second watcher start.

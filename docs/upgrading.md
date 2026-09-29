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

## Switching over from the previous plugin

Sigma replaces the old plugin in place. You can install Sigma next to it and use every Sigma skill,
the loop and the watcher straight away; the old plugin being installed is a notice, never a
refusal. Once you trust Sigma on a repository, migrate its state and uninstall the old plugin.

The cut-over, per machine:

1. **Install Sigma** next to the old plugin (README Quickstart). Nothing needs disabling first.
2. **Run it.** `/agrim-init`, `/agrim-loop`, `/agrim-goal` and the watcher all work. Each run
   prints one line, once:
   `sigma: notice: the plugin previously published as '<old>' is also enabled here; Sigma is
   handling this repository -- uninstall it when ready: claude plugin uninstall <old>@<marketplace>`.
   In a repository the old plugin adopted, init and `loop.py start` also print one
   `sigma: takeover:` line with the exact migration dry-run command. Until you migrate, Sigma reads
   the old state as it is (the table above).
3. **Migrate** (see above): the dry run first; `--apply` only when you say yes. `--apply` still
   refuses while any watcher is running for this `.sdlc` (next section).
4. **Uninstall the old plugin**, with the exact command the notice printed:
   - Claude Code: `claude plugin uninstall <old>@<marketplace>` (add `--scope project` or
     `--scope local` for an install in that scope -- the notice says which), then optionally
     `claude plugin marketplace remove <old>`.
   - Codex: remove the `[plugins."<old>@<marketplace>"]` table from `config.toml` (`CODEX_HOME`,
     else `~/.codex`). This is the edit Sigma names; Codex's own plugin commands were not available
     to verify here.
   - A hook registered by hand that runs the old plugin: remove that entry from the settings file
     the notice names.

`SIGMA_ALLOW_COEXIST=1` (exactly `1`, read under the Sigma name only) silences the notice. It is
not needed for anything to run.

### What Sigma does while both are installed

The detector is `skills/agrim-loop/scripts/coexist.py`. `coexist.py check .sdlc` prints what it
found and the cut-over steps, and exits 0.

| Surface | When the old plugin is active |
|---|---|
| `/agrim-init` (`init_flow.py`, `sdlc_init.py`) | Proceeds, prints the notice, records Sigma as the owner, and offers the migration dry run in an adopted repository. |
| `loop.py start` | The same. |
| `loop.py claim` / `record` (the `/agrim-goal` path) and the automatic watcher start | Proceed; the notice at most once per run (below). |
| `watch_daemon.py` | Proceeds to the shared lock; the notice goes to `.sdlc/state/watch.log`. |
| `migrate.py` | Proceeds (dry run and `--apply`) with the notice. `--apply` still refuses while a watcher is live. |
| `/agrim-doctor` | A `coexistence: WARN` row naming the uninstall command. Never a failure. |
| `status.py` | The notice on stderr. The status line still prints. |
| Session-start hook (Claude Code) | Adds the one notice line to the session, then runs its other checks as usual. Read-only, so it says the same thing every time. It is an accelerator only: every behaviour above is in Sigma's Python, on every host, including Cursor, which has no hooks. |

**Once per run.** `init`, `loop.py start` and `migrate.py` always print the notice. The per-verb
surfaces (claim, record, the watcher and its automatic start) stay quiet while
`.sdlc/state/coexist.notice` is less than 6 hours old; every printed notice refreshes it. So a
session hears it once, not once per `loop.py` call.

**Ownership.** `.sdlc/state/owner.json` records Sigma as the owner on init and loop start. A
marker naming another plugin is part of the notice, never a lock: Sigma replaces it.

### What stays impossible

**Two watchers on one `.sdlc`.** Both plugins' watchers take the same lock files
(`state/watch.pid`, `state/watch.heartbeat`, `state/watch.decide.lock`), whatever you set. When the
old plugin's watcher holds the lock, a Sigma watcher start logs that the running watcher was not
started by Sigma and exits without starting a second one. Sigma never signals another plugin's
process. To hand the lock over, use the polite lever: uninstall the old plugin first (or its
triggers start its watcher again), create `.sdlc/state/watch.stop`, wait one watcher tick for it to
exit, then delete the file (no watcher starts while it exists). Sigma's own watcher starts on the
next trigger and records itself in `state/watch.owner`.

**Migrating under a live watcher.** `migrate.py --apply` refuses (exit 2, nothing written) while any
watcher is running for this `.sdlc`, because it may be the old plugin's, still writing the old
spellings. The refusal names the same lever.

### How the old plugin is detected

It counts as **active** on this repository when any of these holds:
- Claude Code has `<old>@…` enabled in `enabledPlugins` and the plugin is installed for this
  repository on this machine (`plugins/installed_plugins.json`). The managed, local, project and
  user settings files are read in that order of precedence; `CLAUDE_CONFIG_DIR` is honoured, and
  comments and trailing commas in them are accepted. The managed file is
  `/Library/Application Support/ClaudeCode/managed-settings.json` on macOS,
  `/etc/claude-code/managed-settings.json` on Linux and
  `%ProgramData%\ClaudeCode\managed-settings.json` on Windows. When the install list cannot be
  read, an entry in the managed, local or user settings still counts as active, while an entry
  only in the committed project settings is a note.
- Codex's `config.toml` sets `enabled = true` for `plugins."<old>@…"`, in any TOML spelling:
  `[plugins."…"]` or `[plugins.'…']` tables, dotted keys, or `"…" = { enabled = true }` under
  `[plugins]`. `CODEX_HOME` is honoured. Python 3.11+ reads the file with `tomllib`; Python 3.10
  uses a small built-in reader for these shapes.
- A hook whose command runs the old plugin is registered by hand in one of those settings files:
  a path in the command has a directory named exactly the old name, or contains the old plugin's
  recorded install path. A script of your own whose file name merely contains the name does not
  count.
- A live watcher holds this `.sdlc`'s watcher lock and Sigma did not start it (Sigma records its
  own in `.sdlc/state/watch.owner`). This counts as active when the path of the watcher's script
  (`watch_daemon.py`) has a directory named exactly the old name (read from `/proc` on Linux; only
  that script path is checked, never the `.sdlc` argument). Where the command line cannot be read
  (macOS, Windows), it counts as active only when another signal in this list is also present.
- `.sdlc/state/owner.json` names a plugin other than Sigma.

These are notes only, and print no notice: the old plugin installed but not enabled here, enabled
in settings but not installed for this repository on this machine, old schema ids in `.sdlc/` (at
most 200 files are read), an old-name Cursor rule or Codex `AGENTS.md` block (committed text), and a
watcher Sigma did not start with no active signal beside it -- which is also what a Sigma watcher
started before `watch.owner` existed looks like. `log.py` has no coexistence check.

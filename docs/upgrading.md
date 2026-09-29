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
old spellings. Stop it first. It also waits while the old plugin can still run on this repository:
see [Switching over from the previous plugin](#switching-over-from-the-previous-plugin).

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
cannot read those files: it reads an `index.json` in Sigma's schema as empty, and its own guard
refuses to touch a unit record in Sigma's schema (that unit is then stuck for it, not overwritten).
So either switch every machine to Sigma and migrate together, or keep the old plugin off the
branches Sigma writes to until you do.

Two things protect the registry meanwhile. Before Sigma's first write to `.sdlc/features` on a
machine where the old plugin can still run on the repository, it saves one copy of `.sdlc/features`
to `.sdlc/state/backup/features-<UTC time>/` and says so on stderr (once per repository; `state/` is
machine-local and ignored by git; a registry over 5,000 files or 64 MB is not copied, and that is
said instead). That copy is taken ONCE, ever, per repository: it is the state before Sigma's
first write, so restoring it later discards every write made since. Restore it only within the
cut-over window, and prefer the repair below.

And Sigma's registry reader never lets a record the old plugin wrote replace an entry it could not
see. The rule is structural, keyed on the two schema ids and nothing else: a unit record that still
carries the old plugin's schema id, next to an `index.json` in Sigma's schema that has an entry for
the same unit, can only have been written by the old plugin after the conversion, starting from
nothing (a goal it started, the owner claim its next pick makes, `define.py set-priority`). Sigma
reads it as a delta onto the `index.json` entry. The entry wins on every field it has a value for
(title, owner, parent, tracking issue, priority, `open`, each repository's branch and owner). The
record only fills blanks, its goals are added to the entry's, and a repository only it names is
added without a grant. `authorized` is never taken from that record. The reader prints the
recovery once. `feature_sync.py fold` writes the merged view, and first checks that the result
loses nothing the current `index.json` records (no goal of any unit; no field, repository or grant
of a merged unit). If anything would be lost, it refuses (exit 2, nothing written) and names the
loss and the repair:

```
python3 skills/agrim-loop/scripts/feature_sync.py repair .sdlc   # rewrites each such record in Sigma's schema: the index entry plus what the record adds
```

**Known edge.** Because the `index.json` entry wins, an ownership, title or priority change the old
plugin makes after the conversion is not applied while the entry already has a value for that
field. Disable the old plugin, then make the change with Sigma (or edit `index.json`).

Cross-repository propagation follows the same rule. Sigma will not overwrite a sibling repository's
registry file while that file still carries the old schema id. Run the migration in that repository
first.

## Switching over from the previous plugin

Sigma replaces the old plugin in place. You can install Sigma next to it and use every Sigma skill,
the loop and the watcher straight away; the old plugin being installed is a notice, never a
refusal. Once you trust Sigma on a repository, stop the old plugin there, migrate its state, and
uninstall the old plugin -- in that order.

**Why the order matters.** The old plugin cannot read Sigma's registry: it reads a
`.sdlc/features/index.json` carrying Sigma's schema id as empty. So if it starts a goal in a unit
that exists only in that index, it writes a new record for the unit in its own schema, starting
from nothing (no title, priority or tracking issue, `authorized` false, only its own goal; its next
pick then claims an owner on it). Sigma merges that record only as a delta onto the index entry
(above), so nothing the index knew is lost, but whatever the old plugin changes on such a unit
reaches Sigma only where the entry is blank. It also writes other state Sigma owns:

- **Withheld-findings indexes** (`.sdlc/state/withheld/`). Its `upstream._remember` treats an index
  in Sigma's schema as unreadable and then writes a fresh one over it, erasing that goal's dedup
  history and its count toward the per-goal cap of upstream issues (read in its 1.4.25 code; not
  run here).
- **Landing and propagation records.** It cannot read Sigma's, so it writes its own again:
  duplicate work and duplicate records, not loss (#319 review finding; not re-measured here).

These are why the order is: stop the old plugin on the repository **before** converting anything.

The cut-over, per machine:

1. **Install Sigma** next to the old plugin (README Quickstart). Nothing needs disabling first.
2. **Run it.** `/agrim-init`, `/agrim-loop`, `/agrim-goal` and the watcher all work. Each run
   prints one line, once:
   `sigma: notice: the plugin previously published as '<old>' is also enabled here; Sigma is
   handling this repository -- uninstall it when ready: claude plugin uninstall <old>@<marketplace>;
   the old plugin cannot read Sigma's registry; ...`.
   In a repository the old plugin adopted, init and `loop.py start` also print one
   `sigma: takeover:` line with the exact migration dry-run command. Until you migrate, Sigma reads
   the old state as it is (the table above).
3. **Stop the old plugin on this repository.** Use the exact step `coexist.py check .sdlc` or the
   migration's refusal prints:
   - Claude Code, this repository only: run `claude plugin disable <old>@<marketplace> --scope local`
     in the repository. It writes `"<old>@<marketplace>": false` under `enabledPlugins` in the
     repository's `.claude/settings.local.json`, which wins over the project and user settings on
     this machine. Or uninstall it now (step 5). A machine-wide managed setting that enables it
     wins over both: ask whoever manages it.
   - Codex: set `enabled = false` in the `[plugins."<old>@<marketplace>"]` table of `config.toml`
     (`CODEX_HOME`, else `~/.codex`), or remove the table. Codex has no per-repository switch, so
     this stops it on every repository on this machine.
   - A hook registered by hand that runs the old plugin: remove that entry from the settings file
     the notice names.
   - A watcher it left running: the polite lever under "What stays impossible" below.
4. **Migrate** (see above): the dry run first, then `--apply` only when you say yes.
   `migrate.py .sdlc --apply` refuses (exit 2, nothing written, the dry run shown) while the old
   plugin can still run on this repository, and prints the exact step 3 command and the rerun.
   If you must convert with it still running, add `--replace-old-plugin`: a copy of
   `.sdlc/features` is saved under `.sdlc/state/backup/` first, and the registry reader's guard
   (above) still stands. `--apply` also refuses while any watcher is running for this `.sdlc`.
5. **Uninstall the old plugin**, with the exact command the notice printed:
   - Claude Code: `claude plugin uninstall <old>@<marketplace>` (add `--scope project` or
     `--scope local` for an install in that scope -- the notice says which). Then, optionally and
     only if nothing else you use comes from it, `claude plugin marketplace remove <marketplace>`,
     where `<marketplace>` is the part after `@` in the id the notice printed (`coexist.py check`
     prints this command with the real name; it is not always the plugin's own name).
   - Codex: remove the `[plugins."<old>@<marketplace>"]` table from `config.toml`. This is the edit
     Sigma names; Codex's own plugin commands were not available to verify here.
   - A hook registered by hand: as in step 3.

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
| `migrate.py` | The dry run proceeds with the notice. `--apply` waits for the old plugin to be stopped on this repository (exit 2, dry run shown, the exact disable step) unless `--replace-old-plugin` is given, which takes the `.sdlc/features` backup first. `--apply` still refuses while a watcher is live. This is the only step that waits: it is the one that converts the registry the old plugin cannot read. |
| Registry writes (`.sdlc/features`) | Proceed. The first one saves the one-time copy to `.sdlc/state/backup/features-<time>/`. |
| Registry reads, `feature_sync.py show` / `fold` | A unit record in the old plugin's schema next to a Sigma `index.json` entry is merged as a delta (the entry wins every field it has; goals added; never a grant). `fold` writes that and refuses any result that would lose something `index.json` records; `feature_sync.py repair` rewrites such records in Sigma's schema. |
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

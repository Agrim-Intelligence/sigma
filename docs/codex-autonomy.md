# Codex autonomy: safe, opt-in migration

This path changes Codex behavior only. It does not install, update, or restart
Claude Code. The existing `SIGMA_CLAUDE_CMD` supervisor path and Claude review/model
defaults stay as they were. A Codex supervisor needs an enabled Sigma plugin in
the **current Codex home** and refuses if its version or installed source cannot
be verified. It never silently borrows a checkout or a Claude plugin path.

1. Inspect `.sdlc/config.json`, current workers, and `codex plugin list --json`.
   If a Sigma watcher or supervisor is already running for this same checkout,
   stop it through its documented stop file and let it exit before continuing.
   Do not stop workers for other repositories. Keep a backup of local config.
2. Install or update Sigma **in Codex only** with `codex plugin marketplace add
   https://github.com/Agrim-Intelligence/sigma` followed by `codex plugin
   add sigmaloop@sigmaloop`. Confirm one enabled `sigmaloop@sigmaloop` entry at
   version 1.0.0 or newer with `codex plugin list --json`. For a Git marketplace,
   the resolver checks that the marketplace checkout and versioned plugin cache
   are clean and at the same commit (or at the inventory's SHA when supplied).
   A direct local install must resolve to its recorded local path.
3. Refresh the Codex standing rules with `python3
   <installed-sigma>/skills/sigma-init/scripts/sdlc_init.py . --codex` and
   inspect the resulting managed `AGENTS.md` block. This does not edit
   `CLAUDE.md` or Claude's installation.
4. To let only Codex use a fresh Codex reviewer and automatic tier selection,
   add these optional fields to this checkout's `.sdlc/config.json` while
   retaining the existing values of `review.host` and `model_selection`:

   ```json
   {
     "review": {"host_overrides": {"codex": "codex"}},
     "model_selection_host_overrides": {"codex": "auto"}
   }
   ```

   Merge the keys into existing objects; do not replace the whole config with
   this fragment. The Codex reviewer is a fresh read-only process. Its
   end-to-end quality is not yet claimed as verified by this migration.
5. Run one bounded foreground smoke in a disposable repo or a known small
   backlog: `SIGMA_HOST=codex SIGMA_SUPERVISE_MAX_RUNS=1 python3
   <installed-sigma>/skills/sigma-loop/scripts/supervise_daemon.py .sdlc`.
   Check `supervisor.log`, its exit code, the issue/PR trail, and the final
   report before increasing the run limit. A zero-exit child alone is not a
   verified goal completion. The supervisor needs a Codex stop marker to end.
6. Only after that smoke, opt into one longer-running Codex supervisor. On a
   sleeping macOS laptop use `caffeinate -is`. Stop politely by creating
   `.sdlc/state/supervisor.stop`; remove that file before a later restart.

The Codex supervisor uses `codex exec --approve-for-me` rather than an
unrestricted sandbox bypass. `SIGMA_CODEX_CMD` is a separate advanced command
override; it never changes `SIGMA_CLAUDE_CMD` and does not waive plugin
preflight. A kernel lock prevents two Codex supervisors for one `.sdlc` from
launching two workers. The session registry also waits for a pre-existing
worker. A newly launched Claude worker can still race with a Codex supervisor
because the historic Claude path does not take this new lock; do not run both
supervisors for the same checkout at once.

The supervisor's quota classifier has synthetic tests for limit text and
reset times. A read-only Codex CLI smoke on 2026-10-08 returned exit 0 and
printed the final `LOOP STOP: backlog-empty` marker as a separate line; that
output shape is now a test fixture. It did not run an installed Sigma loop.
An actual Codex quota exhaustion and a full Codex goal-to-merge run have not
yet been measured. Report those limits honestly rather than treating these
component checks as end-to-end proof.

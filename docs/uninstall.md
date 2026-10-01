# Uninstall Sigma

Run this guide from the repository you are removing Sigma from. The checker is read-only: it lists
residue and exits 1 until nothing remains.

1. Stop local daemons, then wait for their pid files to disappear:
   ```sh
   touch .sdlc/state/watch.stop .sdlc/state/slack-commands.stop .sdlc/state/supervisor.stop
   ```
2. Uninstall the plugin: `claude plugin uninstall sigma@sigma`; add `--scope project` for a
   project installation. Codex's plugin removal command must be verified with `codex plugin --help`
   on the target host before use.
3. Remove each Sigma worktree with `git worktree remove .sdlc/ledger` and
   `git worktree remove .sdlc/work/<goal>`.
4. Remove `.sdlc/`, then remove Sigma's marked runtime-ignore block from `.gitignore` or
   `.git/info/exclude` and the Sigma Codex marker block from `AGENTS.md`.
5. Optionally delete the remote `sdlc-ledger` branch, `sdlc:*` labels, and linked board. These
   GitHub actions are irreversible.

Finally run:

```sh
python3 tools/readiness/leftovers.py .
```

It exits 0 only when the checked local residue is gone.

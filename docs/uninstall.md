# Uninstall Sigma

Run this guide from the repository you are removing Sigma from. The checker is read-only: it lists
residue and exits 1 until nothing remains.

1. Stop local daemons, then wait and confirm no recorded PID is still alive:
   ```sh
   touch .sdlc/state/watch.stop .sdlc/state/slack-commands.stop .sdlc/state/supervisor.stop
   sleep 2
   for f in .sdlc/state/*.pid; do [ -e "$f" ] || continue; pid=$(cat "$f")
     kill -0 "$pid" 2>/dev/null && printf 'still running: %s (%s)\n' "$f" "$pid"
   done
   ```
2. Uninstall the plugin: `claude plugin uninstall sigma@sigma`; add `--scope project` for a
   project installation. Codex's plugin removal command must be verified with `codex plugin --help`
   on the target host before use.
3. Remove each Sigma worktree with `git worktree remove .sdlc/ledger` and
   `git worktree remove .sdlc/work/<goal>`.
4. Remove `.sdlc/`:
   ```sh
   rm -rf .sdlc
   ```
5. In `.gitignore` and `.git/info/exclude`, delete the comment
   `# Sigma runtime dirs (machine-written)` and the consecutive Sigma runtime-directory lines
   below it. In `AGENTS.md`, delete everything from `<!-- sigma:codex:start -->` through
   `<!-- sigma:codex:end -->`, inclusive. Remove the two init-owned Cursor rules if present:
   ```sh
   rm -f .cursor/rules/sdlc.mdc .cursor/rules/output-contract.mdc
   ```
6. Optionally delete the remote `sdlc-ledger` branch, `sdlc:*` labels, and linked board. These
   GitHub actions are irreversible.

Finally run:

```sh
python3 tools/readiness/leftovers.py .
```

It exits 0 only when the checked local residue is gone.

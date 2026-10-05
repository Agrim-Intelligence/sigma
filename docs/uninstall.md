# Uninstall Sigma Loop

Run this guide from the repository you are removing Sigma Loop from. The checker is read-only: it lists
residue and exits 1 until nothing remains.

1. Stop local daemons, then wait and confirm no recorded PID is still alive:
   ```sh
   touch .sdlc/state/watch.stop .sdlc/state/slack-commands.stop .sdlc/state/supervisor.stop
   sleep 2
   for f in .sdlc/state/*.pid; do [ -e "$f" ] || continue; pid=$(cat "$f")
     kill -0 "$pid" 2>/dev/null && printf 'still running: %s (%s)\n' "$f" "$pid"
   done
   ```
2. Uninstall the plugin: `claude plugin uninstall sigmaloop@sigmaloop`; add `--scope project` for a
   project installation. An install made before the plugin was renamed sits under the previous id: follow
   [From the pre-launch name](upgrading.md#from-the-pre-launch-name) instead. Codex's plugin removal command must be verified with `codex plugin --help`
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
6. Check the repository's git hook setting. Sigma releases before #614 set a local `core.hooksPath`
   at `/sigma-init` that named a directory nothing creates, so git ran none of the repository's hooks:
   ```sh
   git config --local --get core.hooksPath
   ```
   If it prints Sigma's old value (`.git` followed directly by `hooks`, as one name) and no directory
   of that name exists in this repository, remove the setting:
   ```sh
   git config --local --unset core.hooksPath
   ```
   Leave any other value alone; it is yours (husky and similar tools set one).
7. Optionally delete the remote `sdlc-ledger` branch, `sdlc:*` labels, and linked board. These
   GitHub actions are irreversible.

Finally run:

```sh
python3 tools/readiness/leftovers.py .
```

It exits 0 only when the checked local residue is gone.

# Uninstall Sigma Loop

Run this guide from the repository you are removing Sigma Loop from. The checker is read-only: it lists
residue and exits 1 until nothing remains.

1. Stop local daemons, then wait and confirm no recorded PID is still alive:
   ```sh
   touch .sdlc/state/watch.stop .sdlc/state/slack-commands.stop .sdlc/state/supervisor.stop
   while :; do
     live=$(find .sdlc/state -name '*.pid' | while read -r f; do pid=$(cat "$f")
       kill -0 "$pid" 2>/dev/null && printf 'still running: %s (%s)\n' "$f" "$pid"; done)
     [ -z "$live" ] && break
     printf '%s\n' "$live"; sleep 5
   done
   ```
   The watcher looks at its stop file once per tick and then sleeps the whole interval in one call
   (900 seconds unless you changed it), and the supervisor looks only between runs, so this loop can
   take that long to finish. Do not kill the processes to hurry it; press Ctrl-C to stop waiting and
   come back to it. It looks in subdirectories of `.sdlc/state` too, as the final check does.
2. Uninstall the plugin: `claude plugin uninstall sigmaloop@sigmaloop`; add `--scope project` for a
   project installation. An install made before the plugin was renamed sits under the previous id: follow
   [From the pre-launch name](upgrading.md#from-the-pre-launch-name) instead. Codex's plugin removal command must be verified with `codex plugin --help`
   on the target host before use. Then remove the marketplace the install came through:
   `claude plugin marketplace remove sigmaloop` (skip it while another plugin you use comes from that
   marketplace). `claude plugin uninstall` leaves the marketplace registered in the profile's
   `settings.json` and `plugins/known_marketplaces.json`, and `leftovers.py` does not look for either.
3. Remove each Sigma worktree with `git worktree remove .sdlc/ledger` and
   `git worktree remove .sdlc/work/<goal>`.
4. Remove `.sdlc/`:
   ```sh
   rm -rf .sdlc
   ```
5. Delete the local branches Sigma created: `sdlc/*` goal branches, `feature/*` unit branches and the
   `sdlc-ledger` branch. List them first and read the list: `git branch -D` discards commits that were
   never merged or pushed, and the list can include a `feature/*` branch of your own. Step 3 already
   removed the worktrees that held them, because git refuses to delete a branch that is checked out
   in one. First list them:
   ```sh
   git for-each-ref --format='%(refname:short)' refs/heads/sdlc/ refs/heads/feature/ refs/heads/sdlc-ledger
   ```
   Read this list; run the delete only for what you mean to lose. If every branch on it is Sigma's,
   delete them all with:
   ```sh
   git for-each-ref --format='%(refname:short)' refs/heads/sdlc/ refs/heads/feature/ refs/heads/sdlc-ledger |
     while read -r b; do git branch -D "$b"; done
   ```
   If one is yours, run `git branch -D <name>` for each of the others instead. The remote copies are step 9's.
6. Remove the plugin cache. `claude plugin uninstall` leaves the plugin's directory under the Claude
   profile (`leftovers.py` reports it as two `installed-plugin` rows, the directory and its child). The
   cache is shared by every repository on this machine that uses the plugin, so skip this step while
   another repository still needs it; `leftovers.py` keeps listing the rows until you do. If you set
   `CLAUDE_CONFIG_DIR`, the profile is that directory, not `~/.claude`:
   ```sh
   rm -rf "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache/sigmaloop"
   ```
   If `leftovers.py` prints a different path (a Codex home, or the pre-launch directory name), remove
   that path instead.
7. In `.gitignore` and `.git/info/exclude`, delete the comment
   `# Sigma runtime dirs (machine-written)` and the consecutive Sigma runtime-directory lines
   below it. In `AGENTS.md`, delete everything from `<!-- sigma:codex:start -->` through
   `<!-- sigma:codex:end -->`, inclusive. Remove the two init-owned Cursor rules if present:
   ```sh
   rm -f .cursor/rules/sdlc.mdc .cursor/rules/output-contract.mdc
   ```
   If adoption created `AGENTS.md` (the repository had none before) it is now empty: delete the file. If it
   created `.cursor`, remove the now-empty directories with `rmdir .cursor/rules .cursor`, which refuses
   while they hold anything of yours. The final check does not report either one.
8. Check the repository's git hook setting. Sigma releases before #614 set a local `core.hooksPath`
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
9. Optionally delete the remote `sdlc/*`, `feature/*` and `sdlc-ledger` branches, the `sdlc:*` labels, and
   the linked board. **This deletes branches on the REMOTE, and it is irreversible.** The patterns match
   every branch of those names, including your own `feature/*` branches, and a default branch that is
   itself a branch named `sdlc/*`, `feature/*` or `sdlc-ledger`. Do not run it if other people or other machines
   still use these branches. The final check lists them as `remote-branch` rows until they are gone. Both
   blocks start with the same `remote=` line (replace `origin` with your remote's name, in both): it
   sets the remote in the ref patterns, in the prefix that is stripped and in the push. First list them:
   ```sh
   remote=origin
   branches="refs/remotes/$remote/sdlc/ refs/remotes/$remote/feature/ refs/remotes/$remote/sdlc-ledger"
   git for-each-ref --format='%(refname:short)' $branches
   ```
   Read this list; delete only what you mean to lose. If every branch on it is Sigma's, delete them all:
   ```sh
   remote=origin
   branches="refs/remotes/$remote/sdlc/ refs/remotes/$remote/feature/ refs/remotes/$remote/sdlc-ledger"
   git for-each-ref --format='%(refname:short)' $branches |
     while read -r b; do git push "$remote" --delete "${b#"$remote"/}"; done
   ```
   If one is yours, delete the others one at a time instead: `git push <remote> --delete <name>`.
10. Drop the local remote-tracking copies of those branches, whether you deleted them in step 9 or
   someone else did:
   ```sh
   remote=origin
   git fetch --prune "$remote"
   ```

Finally run the checker from the repository, naming a Sigma checkout by path. The repository you are
cleaning has no `tools/` directory, and step 6 removes the plugin's own copy of the checker, which is one
standalone file at `plugins/cache/sigmaloop/sigmaloop/<version>/tools/readiness/leftovers.py` under the
profile; copy it out before step 6 if you have no checkout:

```sh
python3 <checkout>/tools/readiness/leftovers.py .
```

It exits 0 only when the checked local residue is gone. `--json` prints the rows as JSON, and
`--github OWNER/REPO` also lists the repository's `sdlc:*` labels and linked boards (read-only).
Besides the repository, the checker looks under `~/.claude` and `~/.codex` (or `CLAUDE_CONFIG_DIR` and
`CODEX_HOME`) for any `sigmaloop` or `sigma` directory and reports remote-tracking branches too.

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
   on the target host before use.

   Then remove the marketplace the install came through. **Read this first:** `claude plugin marketplace
   remove` also uninstalls every other plugin that was installed from that marketplace and deletes their
   saved options, secrets and data, and without `--scope` it removes the marketplace declaration from every
   settings scope (user, project and local). The command's own help says none of this. List what else
   comes from it first with `claude plugin list`: every id ending `@sigmaloop` is one. Skip the removal while
   another plugin you use comes from that marketplace; otherwise run:
   ```sh
   claude plugin marketplace remove sigmaloop
   ```
   `claude plugin uninstall` leaves the marketplace registered in the profile's `settings.json` and
   `plugins/known_marketplaces.json`, and `leftovers.py` does not look for either.
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
   `CLAUDE_CONFIG_DIR`, the profile is that directory, not `~/.claude`. First copy the final check out of
   the cache, because the removal below deletes it and the repository you are cleaning has no copy of its
   own. The copy goes inside this repository's own git directory, never into `/tmp` or any other shared
   directory: a predictable path there can be pre-made by another user, and the final command would run
   whatever they put in it. The block picks the highest cached version by number (a release beats its own pre-release), refuses to run outside the repository, replaces anything already
   at the target (a symlink is removed, never followed), creates the directory private to you, and prints
   the fallback line, leaving nothing behind to run, if any of that fails. If you see the fallback line, run
   `tools/readiness/leftovers.py` from a Sigma checkout in the final command instead:
   ```sh
   base="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache/sigmaloop/sigmaloop"
   src=$(find "$base" -mindepth 4 -maxdepth 4 -type f -path '*/tools/readiness/leftovers.py' 2>/dev/null |
     while read -r f; do
       v=${f#"$base"/}; v=${v%%/*}
       case $v in ([0-9]*.[0-9]*.[0-9]*) : ;; (*) continue ;; esac
       case $v in (*[-+]*) r=0 ;; (*) r=1 ;; esac
       printf '%s %s %s\n' "$(printf %s "${v%%[-+]*}" | tr . ' ')" "$r" "$f"
     done | sort -k1,1n -k2,2n -k3,3n -k4,4n | tail -n 1 | cut -d' ' -f5-)
   gd=$(git rev-parse --absolute-git-dir 2>/dev/null)
   case "$gd" in /?*) chk="$gd/sigmaloop-check" ;; *) chk=; echo "not inside the adopted repository: cd into it first" ;; esac
   ok=
   if [ -n "$src" ] && [ -n "$chk" ]; then
     if [ -L "$chk" ]; then rm -f "$chk"; elif [ -e "$chk" ]; then chmod -R u+w "$chk" 2>/dev/null; rm -rf "$chk"; fi
     mkdir -m 700 "$chk" && [ -O "$chk" ] && [ ! -L "$chk" ] && cp "$src" "$chk/leftovers.py" && ok=1
   fi
   [ -n "$ok" ] || { [ -z "$chk" ] || rm -rf "$chk"; echo "no usable copy: run <checkout>/tools/readiness/leftovers.py in the final command instead"; }
   ```
   Then remove the cache:
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
   If adoption created `AGENTS.md` (the repository had none before) it is now empty or holds only whitespace.
   The block below deletes it only when it can be read and shows no non-whitespace byte; a non-empty file is
   yours and stays, and so does a file it cannot read or whose bytes are not valid text in your locale (the
   check runs in the `C` locale for that reason). A symlinked `AGENTS.md` is removed as a link only, the
   target stays, and a directory named `AGENTS.md` is left alone. If adoption created `.cursor`, `rmdir`
   removes the now-empty directories and refuses while they hold anything of yours. The final check does not
   report either one:
   ```sh
   if [ -f AGENTS.md ]; then
     LC_ALL=C grep -q '[^[:space:]]' AGENTS.md; [ "$?" -ne 1 ] || rm -f AGENTS.md
   fi
   rmdir .cursor/rules .cursor 2>/dev/null || true
   ```
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
   git for-each-ref --format='%(refname:short)' "refs/remotes/$remote/sdlc/" "refs/remotes/$remote/feature/" "refs/remotes/$remote/sdlc-ledger"  # the three Sigma branch patterns
   ```
   Read this list; delete only what you mean to lose. If every branch on it is Sigma's, delete them all:
   ```sh
   remote=origin
   git for-each-ref --format='%(refname:short)' "refs/remotes/$remote/sdlc/" "refs/remotes/$remote/feature/" "refs/remotes/$remote/sdlc-ledger" | # the three Sigma branch patterns
     while read -r b; do git push "$remote" --delete "${b#"$remote"/}"; done
   ```
   If one is yours, delete the others one at a time instead: `git push <remote> --delete <name>`.
10. Drop the local remote-tracking copies of those branches, whether you deleted them in step 9 or
   someone else did:
   ```sh
   remote=origin
   git fetch --prune "$remote"
   ```

Finally run the checker from the repository. It runs only the copy step 6 made, and only if that copy is a real
file owned by you (the repository you are cleaning has no `tools/` directory of its own):

```sh
gd=$(git rev-parse --absolute-git-dir 2>/dev/null)
case "$gd" in /?*) chk="$gd/sigmaloop-check" ;; *) chk=; echo "not inside the adopted repository: cd into it first" ;; esac
if [ -n "$chk" ] && [ -f "$chk/leftovers.py" ] && [ ! -L "$chk" ] && [ ! -L "$chk/leftovers.py" ] && [ -O "$chk/leftovers.py" ]; then
  python3 "$chk/leftovers.py" .
else
  echo "no verified copy: run <checkout>/tools/readiness/leftovers.py instead"; false
fi
```

When you are done, remove the copy:

```sh
gd=$(git rev-parse --absolute-git-dir 2>/dev/null)
case "$gd" in /?*) rm -rf "$gd/sigmaloop-check" ;; *) echo "not inside the adopted repository: cd into it first" ;; esac
```

It exits 0 only when the checked local residue is gone. `--json` prints the rows as JSON, and
`--github OWNER/REPO` also lists the repository's `sdlc:*` labels and linked boards (read-only).
Besides the repository, the checker looks under `~/.claude` and `~/.codex` (or `CLAUDE_CONFIG_DIR` and
`CODEX_HOME`) for any `sigmaloop` or `sigma` directory and reports remote-tracking branches too.

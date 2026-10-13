# D9 evidence: sandboxed install, adoption, uninstall and predecessor migration, end to end (2026-10-14)

Answers the independent scoring review of the D9 row (upgrade, coexistence, uninstall; threshold
"0 lost state; nothing left behind"), which said the dimension was not shown with the real plugin
uninstall, the full adoption flow, or a predecessor-shaped repository. Sets no score: scores are the
owner's, and `docs/launch/scorecard.json` is untouched.

Base commit: `0d14fe004d5b` (origin/main when the run started). Platform: macOS, one machine, one
operator account. Every number below was observed in this run; nothing is carried from earlier pages.

## Method and isolation

- The plugin source was a fresh archive of origin/main extracted under a scratch directory
  (`<scratch>/src`). The real `claude` CLI added it as a marketplace by local path and installed
  the plugin at user scope.
- Every command ran through one wrapper that sets `HOME`, `CLAUDE_CONFIG_DIR` and `CODEX_HOME` to
  directories under `<scratch>`, points git at a scratch global config, prints the three resolved paths
  before each command and refuses to run if any of them contains the real profile directory names.
  The repository used for adoption was a scratch git repository with a local bare repository as its
  remote (`origin`), so the remote-branch steps ran for real.
- No command in this run was pointed at the owner's real Claude profile or the shared checkout, and the
  wrapper's printed paths were checked before each step. Check made afterwards: the real profile's
  `settings.json` and `plugins/installed_plugins.json` kept their modification times, and its
  `plugins/known_marketplaces.json` holds no scratch path or scratch marketplace entry. That file's
  modification time DID move once during the run (a rewrite with no scratch content; the desktop app and
  other sessions run on the same machine and refresh marketplaces), and I cannot attribute it, so isolation
  is stated as "no command targeted it", not as "proved untouched". The real profile was READ in one place
  (see part 2) to copy the predecessor's installed trees out of its plugin cache.
- Only exact processes this run started were signalled: none were. The one background process (the
  watcher) was stopped by the guide's own stop file, not by a signal.
- Pytest ran one file at a time, serially. No container was used.

## Part 1. Install, adopt, populate, uninstall (steps 1 to 10 of docs/uninstall.md)

### Install through the real CLI

| Command (in the sandbox) | Exit | Observed |
|---|---|---|
| `claude plugin marketplace add <scratch>/src` | 0 | marketplace `sigmaloop` added, declared in user settings |
| `claude plugin install sigmaloop@sigmaloop --scope user` | 0 | installed at user scope |
| `claude plugin list` | 0 | `sigmaloop@sigmaloop`, user scope, enabled, the recorded version |

The plugin cache then existed at `<scratch>/claude/plugins/cache/sigmaloop/sigmaloop/<recorded version>`
and contained `skills/`, `hooks/`, `tools/` and `docs/`.

### Adoption with the installed plugin's own scripts

Run from a fresh scratch repository (one commit, remote pushed), with the scripts taken from the
installed cache, not from the source tree:

| Command | Exit | Observed |
|---|---|---|
| `init_flow.py . --mode local-goals --local-only --no-verify --codex --cursor` | 0 | 7 files created under `.sdlc/`, Cursor rules queued, Codex block written to `AGENTS.md`, `.gitignore` block written |
| commit and push the adoption | 0 | |
| `work.py start .sdlc 0001` | 1 | refused: work is off in `local-only` mode (expected, recorded as measured) |
| `init_flow.py . --mode local-goals --work on --no-verify` | 0 | work switched to a PR per goal |
| goal file `0002` copied from the example, `status: pending`; `work.py start .sdlc 0002` | 0 | worktree `.sdlc/work/0002` on `sdlc/0002`, cut from `origin/main` |
| commit in that worktree; push `sdlc/0002` | 0 | |
| local `feature/demo` branch with its own worktree; pushed | 0 | |
| `"ledger": {"enabled": true}` in config; `sync.py bootstrap .sdlc` | 0 | ledger worktree `.sdlc/ledger` on the ledger branch, published |
| `ledger.py append . claimed 0002 ...` | 1 | wrong argument (it takes the `.sdlc` directory), no state written; the same call with `.sdlc` exit 0 |
| `watch_daemon.py .sdlc` in the background, interval 5 s | n/a | pid file `.sdlc/state/watch.pid`, alive, ticking |

### State before the uninstall

| Item | Value |
|---|---|
| Local branches | `feature/demo`, `main`, ledger branch, `sdlc/0002` |
| Remote heads (bare remote) | the same four |
| Worktrees | repo root, `.sdlc/ledger`, `.sdlc/work/0002`, `.sdlc/work/feature-demo` |
| Top level | `.cursor`, `.gitignore`, `.sdlc`, `AGENTS.md`, `README.md`, `app.py` |
| Profile | plugin cache present; install recorded in `installed_plugins.json` |
| Live pids | the watcher |
| `leftovers.py` (run from the source tree on the repo) | exit 1, 17 rows: `.sdlc`, agents block, 2 Cursor rules, ignore block, 2 installed-plugin, 3 local-branch, 3 remote-branch, 1 running-pid, 3 worktree |

### The guide, step by step

Each fenced block was extracted from `docs/uninstall.md` by a script and run unedited with the
repository as the working directory. The prose steps were done as written; the only hand-run
edit is step 7's file edits, done by a short script that implements the prose.

| Step | Command | Exit | Observed |
|---|---|---|---|
| 1 | the stop-files-and-wait block | 0 | printed `still running` once, then returned when the watcher saw its stop file on its next tick; no signal sent |
| 2 | `claude plugin uninstall sigmaloop@sigmaloop` | 0 | removed from `installed_plugins.json`; **the cache directory stayed, and the marketplace stayed registered in `settings.json` and `plugins/known_marketplaces.json`** |
| 3 | `git worktree remove` for `.sdlc/ledger`, `.sdlc/work/0002`, `.sdlc/work/feature-demo` | 0, 0, 0 | only the repo root left |
| 4 | `rm -rf .sdlc` | 0 | |
| 5 | the list block, then the delete block | 0, 0 | listed `feature/demo`, the ledger branch, `sdlc/0002`; all three deleted; `main` kept |
| 6 | `rm -rf "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache/sigmaloop"` | 0 | cache gone |
| 7 | ignore block, `AGENTS.md` block, then the `rm -f` for the two Cursor rules | 0 | six ignore lines and the comment deleted; `AGENTS.md` became 0 bytes; rules removed |
| 8 | `git config --local --get core.hooksPath` | 1 | nothing set, so no unset needed |
| 9 | the remote list block, then the remote delete block (local bare remote) | 0, 0 | three branches listed and deleted on the remote |
| 10 | `git fetch --prune origin` | 0 | tracking refs for them dropped |
| final | `python3 tools/readiness/leftovers.py .` exactly as the guide printed it, from the repository | **2** | file not found: the adopted repository has no `tools/` directory (finding 1) |
| final | `python3 <source tree>/tools/readiness/leftovers.py .` | **0** | no rows |

After the guide: one local branch (`main`), one remote head, one worktree, no `.sdlc`, no pid files,
`installed_plugins.json` with an empty plugin map, `core.hooksPath` unset. The tracked files the
adoption had committed show as modified or deleted in the working tree, which is what the user then
commits.

### Findings from part 1

1. **The final command could not be run as written.** The guide ends `python3 tools/readiness/leftovers.py .`
   run "from the repository you are removing Sigma Loop from"; that path exists only in a Sigma checkout,
   and step 6 deletes the plugin's own copy. Exit 2 in the adopted repository (measured above). FIXED in
   `docs/uninstall.md`, in two rounds. Round one named a checkout by path and told the copy-out AFTER step 10,
   which a reviewer showed cannot work for a reader following in order (step 6 had already deleted the cache copy;
   verified: no checker file left on the profile). Round two put a `cp` in step 6 before the cache removal, to a
   fixed path under the temporary directory. A delta review then showed that path is an exploit: someone else can
   pre-create it read-only with a planted script, `cp` fails (its failure was inside the `if`, so no fallback line
   printed) and the final command ran the planted file; a pre-made symlink made `cp` overwrite its target. Round
   three (current): the copy lives in the repository's own git directory (`sigmaloop-check`, made `mkdir -m 700`
   after any existing entry is replaced, a symlink removed and never followed, checked to be owned by the user),
   any failure prints the fallback line and removes the directory, the final command runs the copy only if it is a
   non-symlink file owned by the user and otherwise prints a fallback and exits non-zero, and a last block removes
   the copy. The cached version is the highest by number (the earlier `ls | tail` picked the lower of two versions whose texts sort the other way round), found
   with `find`, which stays quiet in zsh when nothing is cached (the earlier `2>/dev/null` did not hide
   zsh's "no matches found"). Guards, each seen red against the previous text and green after:
   `test_copy_out_never_runs_planted_code_in_a_shared_or_predictable_place` (planted code ran),
   `test_copy_out_does_not_follow_a_symlinked_directory_or_file` (target overwritten),
   `test_failed_copy_prints_the_fallback_and_leaves_nothing_runnable` (no fallback),
   `test_copy_out_picks_the_highest_cached_version_numerically` (picked the lower version),
   `test_copy_out_with_nothing_cached_is_quiet_and_prints_the_fallback` (zsh stderr), the order test and the
   green end-to-end test, each under sh, bash and zsh.
2. **The marketplace registration survived the whole guide and the checker exited 0.** After step 10 the
   profile still held an `extraKnownMarketplaces` entry in `settings.json` and a row in
   `plugins/known_marketplaces.json`. `claude plugin marketplace remove sigmaloop` removed both
   (exit 0, files re-read). FIXED in `docs/uninstall.md` step 2. A reviewer added that this command also
   uninstalls every other plugin installed from that marketplace and deletes their saved options, secrets and
   data, and without `--scope` clears the declaration from every settings scope (the CLI help says neither);
   step 2 now carries that warning BEFORE the command, and `claude plugin list` (ids ending `@sigmaloop`) as the
   way to see what else comes from it (the listing was run; it shows `<plugin>@<marketplace>` per row). The
   warning's two claims come from the reviewer's run; this run did not install a second plugin from the
   marketplace, so it did not re-measure them. Guards:
   `test_guide_removes_the_marketplace_registration_the_install_came_through` and
   `test_guide_warns_before_the_marketplace_removal_command`, each red before and red when its clause is broken. NOT FIXED: `leftovers.py` still cannot see a registration; a launch:next
   issue (number 1348) records it.
3. **Empty files adoption created stayed behind, unreported.** On a repository that had no `AGENTS.md` and
   no `.cursor`, the guide left a zero-byte `AGENTS.md` and an empty `.cursor/rules` directory; the
   checker looks for markers and the two rule files, so it exited 0. FIXED in step 7 with a block. Round one tested for zero bytes; a delta review showed that cutting the Codex block can
   leave a single newline, which that test keeps. Round two used `tr -d` and a non-empty test; a round-three review
   measured DATA LOSS: on macOS in a UTF-8 locale `tr` prints "Illegal byte sequence" and nothing else when the file starts
   with a byte that is not valid UTF-8, so the command substitution was empty and `rm -f` ran (a Latin-1 file, a file of
   three high bytes, both deleted; kept under the `C` locale). Now the block deletes only when `LC_ALL=C grep -q
   '[^[:space:]]'` ran and exited exactly 1 (read fine, no non-whitespace byte); exit 0 (content, including a
   byte-order mark alone or NUL bytes) and exit 2 (read error, tested with a mode-000 file) both KEEP the file. A symlink
   is removed as a link only and a directory is left alone. Tested over eleven contents, UTF-8 and `C` locales, three
   shells; the Latin-1, high-byte cases were red against the previous text. `rmdir` of the empty directories is
   unchanged. Guards: `test_step7_deletes_agents_md_only_when_it_has_no_content`,
   `test_step7_agents_md_link_and_directory_cases`, `test_step7_unreadable_agents_md_is_kept`.
   The same review found that the copy-out, final and cleanup blocks built the path from an empty `git rev-parse`
   result outside a repository (or with a failing `GIT_DIR`), turning into `mkdir -m 700 /sigmaloop-check` and
   `rm -rf /sigmaloop-check`; all three blocks now refuse unless the git directory is an absolute path, and
   `test_blocks_refuse_to_act_outside_the_adopted_repository` runs each from a non-repository (and with a failing
   `GIT_DIR`) with `rm`, `mkdir`, `chmod`, `cp`, `mv` and `rmdir` shimmed to record calls: none may occur (red before).
   It also found that a pre-release beat its release in the version pick; the pick now ranks a name without a suffix
   above the same numbers with one (`test_copy_out_prefers_the_release_over_its_prerelease`, red before). A bash 3.2
   parsing bug in `case` inside `$(...)` (macOS `sh`) was found by those tests and avoided with parenthesised patterns.
4. **Step 9 silently deleted nothing under zsh (reviewer finding).** The blocks passed three ref patterns
   through an unquoted `$branches`, which zsh (the macOS default) does not word-split; the loop ran on one
   nonsense pattern, printed nothing and exited 0. FIXED: the patterns are spelled on the `git for-each-ref`
   command line, each quoted. Guard: `test_documented_remote_blocks_work_in_sh_bash_and_zsh`, run under
   `sh`, `bash` and `zsh` (skips with a reason if a shell is missing); it was red under zsh only before the
   fix, and red again when the old form was put back.
5. **Seen once, not reproduced:** a stray `.sdlc/.sdlc/state/log` directory appeared during the ledger
   steps (cause not isolated; an invalid-kind `ledger.py append` with the ledger on, retried in a scratch copy, did not
   recreate it). It sat inside `.sdlc`, so step 4 removed it. Not claimed as a defect.

## Part 2. Predecessor-shaped migration

### Is there a real local clone of the predecessor?

No git clone and no archive of it exists under `<home>/Documents`, an owner tooling directory or `/private/tmp`
(a depth-limited name search for the predecessor's name and its skill-directory prefix returned nothing in
each). What does exist is **an installed copy in the host's plugin cache** (four releases, the exact
location `tests/test_registry_survives_predecessor_fold.py` reads). That is a different thing from a git
clone: it is the released file tree without history, and I did not search outside the three roots beyond
reading that one documented cache location. The real cache was read, never written; the releases were copied
into scratch first.

### Predecessor-shaped repository

Built with the predecessor's OWN scripts from the copied cache (newest installed release): its init (with
`--codex`), its `setup.py ignore`, its work-start for an in-flight goal worktree, and its registry writer
for a unit record plus its own `fold`. Not from the predecessor's own tooling: its unit-open gesture failed at the label
step (it needs an authenticated `gh`, absent here; the branch step succeeded), so the unit record came from
the registry writer; a landing record and a managed-block feature document were written by hand in the predecessor's
spelling. In-flight state: an uncommitted-to-main goal worktree `sdlc/0002` with a commit, a remote
`feature/voice` branch, a user-owned `voice-extra` branch, a committed unit record, a landing record.

The scratch profile got a copy of the predecessor's cache plus an inventory entry, and `enabledPlugins`
set the way a real install sets it. A pre-migration copy of the repository was kept for the diff
(the migration then ran on the original, because a copy would have broken the worktree's absolute paths).

| Command | Exit | Observed |
|---|---|---|
| `migrate.py .sdlc` (dry run) | 0 | 6 changes listed, 0 refused, 1 left as is (the previous journal block) |
| `migrate.py .sdlc --apply` with the old plugin in the install list but NOT enabled | 0 | applied. The guide's "waits" row fires only when the plugin is also enabled; this run first applied without that, then was restored (tracked files from git, the ignored landing record from the copy) |
| `migrate.py .sdlc --apply` with the old plugin installed AND enabled | **2** | refused, nothing written, the dry run shown, the exact disable step and the `--replace-old-plugin` alternative printed |
| the printed `claude plugin disable <old id> --scope local` | 0 | wrote a local settings file with the plugin set false |
| `migrate.py .sdlc --apply` | 0 | 6 applied, 0 refused, 1 left as is |
| `migrate.py .sdlc --apply` again | 0 | `nothing to migrate` |

State before and after (diff of the pre-migration copy against the migrated repository):

| Item | Result |
|---|---|
| Schema ids in `index.json`, the unit record, the landing record | respelled to Sigma's; nothing else in those files changed |
| Feature document | only the two managed-block marker lines respelled; the body and the hand-written note identical |
| `AGENTS.md` | Codex block regenerated with Sigma's text |
| `config.json` | three `*_env` values and one channel key respelled; every other line identical |
| Branches and refs | `main`, `sdlc/0002`, `voice-extra`, remote `feature/voice`: same names, same commits |
| Worktree `sdlc/0002` | present, same commit, in-flight file intact; `work.py root` resolves it |
| Registry | `feature_sync.py show` returns the unit with every field it had |
| `status.py` | reads the repository: 2 pending |
| Left as is, by design | the previous journal config block (still holds the old name; the guide lists it) |

Lost state: none observed in this repository. This is one repository with one unit and one goal, not a population.

### The tests that skip without a predecessor

Run one file at a time against the scratch profile holding the cache copy:

| Run | Result |
|---|---|
| `tests/test_registry_survives_predecessor_fold.py`, `CLAUDE_CONFIG_DIR` at the scratch profile | 27 passed, none skipped, including `test_real_predecessor_fold_is_signalled_and_recover_restores_the_unit` |
| `tests/test_coexist.py` with `SIGMA_TEST_PREDECESSOR_GIT` at a local repository whose single commit is an installed tree, rev `HEAD` | 146 passed, 12 failed. The 12 are the parameters pinned to two release commits that do not exist in that repository (the harness could not run `git archive` on them): an artefact of the fixture |
| the same with two tags added to that repository: the first release's recorded commit prefix on a commit holding the older installed tree, the second pinned prefix on a commit holding a later installed tree | the `real` subset: 16 passed |

Honesty on the last row: the second pinned release is not in the host cache, so the tree standing behind
that tag is a DIFFERENT, later release, labelled here as a substitute. The first tag's commit prefix equals the
`gitCommitSha` the real host recorded for that installed release, but the repository is still synthetic (one
commit per tree), so `git archive` of that prefix was not run against the predecessor's real history.

## What was NOT measured

- Linux, Windows, WSL: macOS only.
- Codex: no `codex` binary on this machine, so Codex's plugin removal command was not run and `CODEX_HOME`
  was an empty scratch directory. The Codex *scaffold* (the `AGENTS.md` block) was exercised.
- GitHub mode: no authenticated `gh`, so labels, the board and the `--github` flag of the checker were not run;
  step 9's label and board half is untested. The remote-branch steps ran against a local bare repository, not
  a hosted one.
- A marketplace added from a hosted repository: a clone of it under `plugins/marketplaces/` was NOT measured.
  It is not known whether `marketplace remove` then also removes the plugin cache, which would defeat the
  copy-out advice for those users (the cache is already gone when step 6 runs; the guide's fallback line says to
  use a checkout). Only a local-path (directory) marketplace was run.
- Supervisor and Slack daemons (only the watcher ran); a project- or local-scope plugin install.
- The pre-launch install-id path of `docs/upgrading.md`.
- Rollback: unchanged. The clause stays an owner ruling and nothing here demonstrates it.
- A real predecessor git clone and its real history; a population of migrated repositories.

## Re-run after the reviewer's fixes (guide as it stands in the PR)

The whole flow was re-run twice from a clean profile (install, adoption, state, then every fenced block of the
edited guide extracted and run unedited): once with the blocks under bash and once under zsh (macOS default),
the real `claude` commands in between. Both: the copy landed in the repository's git directory, cache removal worked,
step 9 listed and deleted all three remote branches, the final command ran that copy and exited 0 with no output,
the cleanup block left no `sigmaloop-check` entry in the git directory, and only `main` remained; the marketplace
maps in `settings.json` and `known_marketplaces.json` were empty. The only non-zero exit was the step 8 read when no
`core.hooksPath` is set. Shells: bash and zsh as shipped on this machine; plain `sh` ran in the test suite only.

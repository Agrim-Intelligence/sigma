# Name handover: renaming the private repository before the public one takes its name

The public repository will take this repository's current name. GitHub redirects an old name only
until a new repository takes it, so after the public repository exists every clone, config value,
marketplace record and workflow that still names the old slug points at the PUBLIC repository.
Nothing here performs the rename: it is the owner's action, by hand. What this page and
`tools/handover_check.py` give the owner is the list of every holder of the old name that this
machine can show it (see [What it cannot see](#what-it-cannot-see)), and the ordered commands. The release checklist, pin and rollback are #359's; the checker and the sequence
are #397's, and each page links the other by number only.

Host-agnostic: the checker is plain Python on git, `ps` and (optionally) `gh api` GET calls.

## What it checks

`python3 tools/handover_check.py check --old OWNER/NAME [--new OWNER/NAME] ...` is STRICTLY
read-only. It writes no git configuration, makes no GitHub write and creates no file except the
optional `--json` evidence, which is created once, mode 0600, and refused inside any work tree. It
reads:

- the current repository (the top level of the working directory), each `--clone PATH`, every
  repository found under each `--scan-root DIR`, and every worktree `git worktree list` names for
  any of them. A repository is any of: a work tree with a `.git` directory, a clone whose `.git` is
  a FILE (a linked worktree, a submodule checkout, a `--separate-git-dir` clone), a bare or
  `--mirror` repository (a directory holding `HEAD`, `objects/` and `refs/`; `--clone` accepts one
  too), and every submodule git directory under `.git/modules/`, which is read even after
  `git submodule deinit` because its config still names a remote and it can still push. A git
  directory is read with `--git-dir`, so `safe.bareRepository=explicit` does not hide it. A
  repository is counted once (by its common git directory) and located at a work tree when it has
  one;
- the walk: it never follows symlinks, never enters a `.git` directory or a bare repository, and
  DOES walk every other directory of a repository, a linked worktree's too, so a clone kept inside
  another clone is found. It does not enter directories named `node_modules`, `.venv`, `venv`,
  `__pycache__`, `.tox`, `.nox`, `.mypy_cache`, `.pytest_cache` or `.ruff_cache` unless the
  directory is itself a repository; that is one informational `skipped` finding per scan root
  that counts them, because a clone inside one (an editable `pip install` from git, say) is NOT
  seen. `--max-depth` (default 5) counts from the nearest repository above, not from the root, so a
  clone nested deep inside a big repository is still reached; `--max-repos` (default 2000) caps the
  repositories; `--max-seconds` (default 600) bounds the walk, the discovery of each repository it found
  and the reading of their remotes, by wall-clock time. No cap is silent: each one that cuts is a blocking `truncated` finding,
  field `max-repos`, `max-depth` (a directory at the cap that still has subdirectories; the note
  counts them and names the first) or `max-seconds` (the walk, the discovery or the remote
  reading stopped at the bound; the note counts what was left). A git call already running when the
  bound passes finishes first (each has its own 60-second timeout), and the reads after the remotes
  (`.sdlc/config.json`, heartbeats, tracked text, marketplaces, the process list) are not cut;
- per repository and per worktree, every place git takes a fetch or push URL from, in every scope
  git applies there (system, global, `include.path` and `includeIf` files, the repository's config,
  a worktree's own `config.worktree`, and config given to the checker's OWN process through
  `GIT_CONFIG_COUNT`/`GIT_CONFIG_PARAMETERS`, located at `command line:`): `remote.<name>.url`,
  `remote.<name>.pushurl`, `url.<base>.insteadOf` / `pushInsteadOf`, a URL written where a remote
  name goes (`branch.<b>.remote`, `branch.<b>.pushRemote`, `remote.pushDefault`; the insteadOf and
  pushInsteadOf rewrites are applied to it as git does), and the legacy `.git/remotes/<name>` and
  `.git/branches/<name>` files (`git remote` does not list those, so the checker reads them). AND
  the URLs git really uses for each named remote (`git remote get-url --all`, with and without
  `--push`, which apply every rewrite). A value held in a global or included file is located at
  that file; one in the repository's config at its first work tree; one in a `config.worktree` at
  that worktree. Each URL is classified `old`, `new`, `other` or `absent` (case-insensitive) after
  normalising it as GitHub does: a `?query` or `#fragment` is dropped, `//` collapsed and `.` and
  `..` resolved. A GitHub URL (HTTPS, `ssh://`, scp-like SSH with or without a `/` after the colon,
  on `github.com`, `www.github.com` or `ssh.github.com`) is classified by its slug; ANY other URL
  whose path ends in `OWNER/NAME` is classified by that path too, with a note naming the host, so a
  clone behind an SSH host alias, a rewrite or a mirror is never missed (it fails closed: a mirror
  on another host that happens to end in the old slug blocks until it is repointed or confirmed).
  Not holders, and not read: `core.sshCommand`, `GIT_SSH_COMMAND` and credential helpers choose HOW
  git connects, never WHERE, so they hold no repository name;
- per checkout, the `.sdlc/config.json` keys that name a repository
  (`discovery.github.repo`, `discovery.github.project`, `ledger.handoff.upstream_repo`,
  `work.remote`, `ledger.remote`, `knowledge_graph.sync.remote`, the last three resolved to their
  remote's URL) and the age of `.sdlc/state/watch.heartbeat`;
- in the current repository only, tracked text naming the old slug and the lines of
  `.github/workflows/*.yml` that hold it;
- the Claude config directory (`--claude-config`, else `$CLAUDE_CONFIG_DIR`, else `~/.claude`):
  `plugins/known_marketplaces.json`, `settings.json` `extraKnownMarketplaces`, and each
  `plugins/marketplaces/*/` clone's origin;
- the `GH_REPO` environment variable;
- the process list: rows running `watch_daemon.py` or `loop.py`;
- unless `--offline`, GitHub REST lists of NAMES only (Actions secrets and variables at both levels,
  hooks, deploy keys, environments), and with `--repo-id N` the ids of the old and new names.

## Holder kinds and exit codes

Exit 0: no blocking finding. Exit 1: at least one. Exit 2: a refusal (one stderr line
`handover_check: REFUSED [<code>] <detail>`, nothing on stdout): `windows`, `bad-slug`,
`owner-differs` (a rename never moves the owner), `same-name`, `bad-cap` (a negative
`--max-depth`, `--max-repos` or `--max-seconds`), `clone-unreadable`,
`scan-root-unreadable`, `json-inside-work-tree`, `json-exists`, `work-tree-unknown`, `internal`.

Each finding prints `BLOCK <kind> <location> <field> <class>` or `INFO  ...`, then a last line
`handover_check: <B> blocking, <I> informational; repositories <n>; truncated <yes|no>`.

- BLOCK when holding the old name: `remote-url`, `remote-pushurl`, `url-rewrite`, `config-repo`,
  `config-upstream`, `config-remote`, `marketplace`, `workflow`, `env`.
- BLOCK also: `writer` and `heartbeat` (age at most 600 seconds) when ATTRIBUTED to a checkout that
  holds either name, by the checkout's path in the process command line, by the process's working
  directory (read-only), or by its own fresh heartbeat; `id-mismatch` (`--new` resolves to an id
  other than `--repo-id`, or is not private); `unreadable` (a directory the walk could not read:
  macOS makes new folders under `~/Documents` unreadable from a terminal that was not granted
  access, so it is listed, never skipped); `truncated` (not every clone was seen: field
  `max-repos` when the repository cap stopped the walk, `max-depth` when the depth cap cut a
  directory that still had subdirectories, `max-seconds` when the time bound stopped the walk or
  the reading of what it found; re-run with a larger cap until none appears).
- INFO: the same holder kinds when they already hold the new name; `config-project` (it holds no
  repository name); `tracked-text`; the `rest-*` name lists; `rest-unreadable` (403 or 404);
  `skipped` (the heavy directories the walk did not enter, counted); unattributed `writer` rows (a
  note says `cwd unreadable` when the directory could not be read)
  and stale `heartbeat`; `old-name-taken` (the old name now resolves to an id other than
  `--repo-id`: every remaining old holder reaches that repository).

`--json FILE` also writes schema `sigma.handover-check/v1` with the findings, counts and caps.
`caps.truncated` records the repository cap only; a depth or time cut is the `truncated` finding
with field `max-depth` or `max-seconds`, and the last line's `truncated yes` covers all three.

Cost, measured on a SYNTHETIC tree on this machine (`--offline`, default caps): 236,832
directories, 300 repositories each holding 100 source directories and a `node_modules` of 600, and
20,000 plain directories. Two runs took 16.5 and 15.1 seconds of wall time, nothing truncated. The
walk alone took 2.1 seconds there (11.4 seconds when `node_modules` is not skipped); the rest is
about 45 milliseconds of git calls per repository, so the run is linear in directories walked plus
repositories found: 2000 repositories cost about 90 seconds, 20,000 about 15 minutes (extrapolated,
not measured), and at that size `--max-seconds` and `--max-repos` must be raised.
On this machine's real `~/.sigma-ops` (measured, `--offline --max-depth 40 --max-repos 200000`,
default `--max-seconds 600`) the run ENDED at 604 seconds, exit 1, with blocking `truncated` rows
(field `max-seconds`): the walk found about 40,500 repositories (480 heavy directories skipped),
and discovering 30,589 of them used the whole bound, so none of their remotes was read. Without the
bound on reading, an earlier run took 878 seconds and another was stopped by hand after more than
17 minutes. That root cannot be checked whole: point `--scan-root` at the directories that hold
clones of this repository, and raise the caps until no `truncated` row remains.

## The sequence

`python3 tools/handover_check.py sequence --old OWNER/NAME --new OWNER/NAME [--throwaway OWNER/NAME]`
prints, and runs nothing: first a REHEARSAL block, then the numbered owner SEQUENCE with the names
filled in. Its three `check` runs (steps 2, 6 and 8) write `--json "$JSON"`, `"$JSON2"` and
`"$JSON3"`: three different paths, because a `--json` file is never overwritten and a reused path
is refused (`json-exists`). The order is the point:

1. Stop every writer: `touch .sdlc/state/watch.stop` in each checkout, end every loop session, and
   confirm none is left. Never pause a process with a stop signal.
2. Run `check` and save the `--json`: this is the list of every holder to repoint.
3. Record the repository id with `gh api repos/OLD --jq '.id, .private'`.
4. Owner: `gh repo rename NEWNAME -R OLD --yes`.
5. Once per repository from step 2: `git remote set-url origin` to the new URL, repoint
   `discovery.github.repo` (and `ledger.handoff.upstream_repo` if it named the old name) in each
   `.sdlc/config.json`, and re-add any marketplace recorded under the old name.
6. Check the id again (it must equal the recorded one) and run `check` with `--repo-id`: it must
   exit 0.
7. ONLY THEN, owner: `gh repo create OLD --public`, push the export, and run
   `python3 tools/verify_public_repo.py` against it (see [the public snapshot](public-snapshot.md)).
8. Run `check` once more: `old-name-taken` informational and no BLOCK.
9. Remove `.sdlc/state/watch.stop` in each checkout.

Every step stops at the first failure. Steps 4 and 7 are the only ones that touch GitHub, and both
are the owner's.

## Control

The control runs the documented `check` gesture on two TEMPORARY clones, from a directory outside
every repository so that this checkout's own holders do not decide the exit code. The clone `a`
still has the old name as its origin (red), then is repointed (green):

```sh
CTL="$(mktemp -d)"
cd "$CTL"
git init -q a && git init -q b
git -C a remote add origin https://github.com/acme-old/widget.git
git -C b remote add origin https://github.com/acme-old/widget-private.git
python3 /path/to/sigma/tools/handover_check.py check --old acme-old/widget --new acme-old/widget-private --clone "$CTL/a" --clone "$CTL/b" --offline
git -C a remote set-url origin https://github.com/acme-old/widget-private.git
python3 /path/to/sigma/tools/handover_check.py check --old acme-old/widget --new acme-old/widget-private --clone "$CTL/a" --clone "$CTL/b" --offline
```

The first `check` exits 1 and lists `BLOCK remote-url` for `a`; the second exits 0. Unrelated
`loop.py` or `watch_daemon.py` processes on the machine appear as informational `writer` rows,
because none is attributed to a clone holding either name.

## Rehearsal

Owner-only, and first, on a throwaway PRIVATE repository, so the real name is never at risk. The
same block is printed by `python3 tools/handover_check.py sequence ... --throwaway OWNER/NAME`:

- R1. The owner creates the throwaway private repository and pushes the export to it (the build is
  in [the public snapshot](public-snapshot.md)).
- R2. Verify the push with `python3 tools/verify_public_repo.py ... --expect-visibility private`.
- R3. In a clone configured for the throwaway, the loop claims a goal, opens a pull request and
  records its result: this proves the loop's own configured repository works against it.
- R4. Run steps 1 to 6 above with the throwaway as the old name and a renamed throwaway as the new
  name, then repeat R3 against the renamed one.

R4 shows that the repoint of the holders the checker DID list works: after it, a `check` that exits
0 and a loop that still claims and opens a pull request. It cannot show that no holder was missed;
that rests on the classification and walk rules above and on [What it cannot see](#what-it-cannot-see).

## What it cannot see

The checker reads this machine only, as the user who runs it. It does not see: other machines,
collaborators' clones, the board's linked repository (GraphQL only, no REST list), any other
process's environment (a scheduler or service that sets `GH_REPO` or `GIT_CONFIG_*` for itself;
only the checker's own environment is read), a URL typed on a command line or kept in a script,
shell alias or history, a clone inside a skipped heavy directory (see the walk above), a directory
it cannot read (listed as `unreadable`), or Codex marketplace records (they need the `codex` CLI). A
rename is only as safe as the owner's knowledge of those; list them before step 4.

## Ownership

The checker and the sequence are #397's. The release checklist, pin and rollback, and anything
about incident handling are #359's. This page does not link to the release document: each goal is
cross-referenced by number only.

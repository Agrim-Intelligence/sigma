# D9 evidence: migration, uninstall and rehearsal rollback (2026-10-12)

Dimension D9, "Upgrade, coexistence, uninstall" (`docs/launch/review-plan.md`). This page records
three runs on one macOS (Apple silicon) machine, all on scratch repositories and an isolated Claude
Code profile. It sets no score: scores are the owner's (`docs/launch/decision-rule.md`).

Measured commit: `f627d06ad3f6fe391936a0e0bb1e6061f2af6aa4` (origin/main when measured). Date: 2026-10-12,
three runs back to back, about 20 seconds wall in all. Full redacted transcripts sit beside this page;
the old plugin name is written `<old>` and scratch paths `<scratch>`, which are the only redactions
(also: the previous config's journal key is `<old-journal-key>`, the ledger branch name is `<ledger-branch>` and the stale hooks directory is `<stale-hooks-dir>`, because the repository's private-name guard rejects the raw spellings).

| File | Holds |
|---|---|
| `d9-migration-transcript-f627d06ad3f6.txt` | before hashes, dry run, apply, rerun, four controls |
| `d9-uninstall-transcript-f627d06ad3f6.txt` | adoption, `leftovers.py` before, uninstall steps 1 to 6, final check, controls |
| `d9-rollback-transcript-f627d06ad3f6.txt` | local rehearsal tags, the four pin gestures, install, rollback, controls |
| `d9-results-f627d06ad3f6.json` | the exit codes and booleans of all three runs |

## 1. Migration: 0 lost state on two scratch repos

Both repos are git repositories built hermetically: the fresh one by the fixture builder
`legacy_repo` in `tests/test_legacy_migrate.py` (the same builder the unit tests use, so it is shaped
like real predecessor state), the in-flight one by extending it. The environment is a scratch `HOME`,
`CLAUDE_CONFIG_DIR` and `CODEX_HOME`, so no real profile is read. The gestures are the ones in
`docs/upgrading.md`, run from the repo root:

```
python3 skills/sigma-doctor/scripts/migrate.py .sdlc            # dry run
python3 skills/sigma-doctor/scripts/migrate.py .sdlc --apply
python3 skills/sigma-doctor/scripts/migrate.py .sdlc --apply    # rerun
```

| | repo-fresh | repo-inflight |
|---|---|---|
| State before | 7 files: 1 unit, landing, withheld, config, feature doc, AGENTS.md | 22 files: 3 units with owners, 5 landing records, a propagation record, a pending-claim file, ledger and timing history, a nested `work/` checkout, plus an `sdlc/goal-21` branch with a commit and one uncommitted file |
| Dry run | exit 0, 7 changes listed, tree byte-identical afterwards | exit 0, 16 changes listed, tree byte-identical afterwards |
| `--apply` | exit 0, 7 changes applied, 0 refused | exit 0, 16 applied, 0 refused, 4 left as is and listed |
| Rerun `--apply` | exit 0, `nothing to migrate` | exit 0, `nothing to migrate` |
| Files missing after | 0 | 0 |
| Content differing after (old spelling respelled to Sigma's) | 0 | 0 |
| History, `work/` checkout and the non-rewritten schema kinds | none present | byte-identical (ledger entry, timing session, nested checkout) |
| Git refs and branch, uncommitted file | refs unchanged | refs unchanged; `sdlc/goal-21` still checked out; the uncommitted file survives |
| Sigma reads the registry after | unit `voice` | units `billing`, `search`, `voice` |

"Lost state" was defined before the run, and checked by a comparator separate from `migrate.py`:
every file present before is present after; for each rewritten file the bytes after equal the
bytes before with only the old name respelled to Sigma's (AGENTS.md compared outside the regenerated
Codex block); history and nested checkouts must be byte-identical. The only files the migration
created are the per-unit `feature_sync` locks under `.sdlc/state/features/`, which `migrate.py`
documents as left behind by design. Reading the registry back through `feature_registry.read`
returned the same units as before.

Controls, each step broken once (transcript, "CONTROLS"):

| Break | Result |
|---|---|
| Feature doc with its end marker deleted | exit 2, that file refused and left byte-identical; the other files were still converted, which is the documented per-file behaviour |
| A live watcher (pid file plus fresh heartbeat) | exit 2, `refused all`, nothing written |
| The previous plugin still enabled in the user's settings | exit 2, `refused --apply`, no migrated file written. One once-per-repo notice marker, `.sdlc/state/coexist.notice`, was written, so "nothing written" is true of migrated state but not of every byte |
| The comparator itself, run on an AFTER with one record deleted and one emptied | reports both (`missing` and `content-differs`); a clean AFTER reports none |

Not exercised: a unit record the old plugin wrote after conversion (the `feature_sync.py repair`
path), a symlinked `AGENTS.md`, and `--replace-old-plugin`. The unit tests in
`tests/test_legacy_migrate.py` cover them; this run did not.

## 2. Uninstall: not clean by the documented steps; clean after two more removals

A scratch repo was adopted with the real scaffolder
(`python3 skills/sigma-init/scripts/sdlc_init.py . --codex --cursor`), then given what a working
install adds: a worktree on `sdlc/goal-1`, a ledger branch and worktree under `.sdlc/ledger`,
the pre-#614 stale `core.hooksPath`, a live pid recorded in `.sdlc/state/watch.pid` (a stand-in
`sleep` process, stopped by its own recorded pid), and the plugin installed with the real
`claude plugin install` in an isolated profile. Then `docs/uninstall.md` steps 1 to 6 ran in order
(step 2 with the real `claude plugin uninstall` and `marketplace remove`), and the final check ran:

```
python3 tools/readiness/leftovers.py .
```

| Point | Exit | Rows |
|---|---|---|
| Adopted, before | 1 | 13: `.sdlc`, `agents-block`, 2 `cursor-rule`, `hooks-path`, `ignore-block`, 2 `installed-plugin`, 2 `local-branch`, `running-pid`, 2 `worktree` |
| Control: only step 4 (`rm -rf .sdlc`) run | 1 | the other residue still named |
| After steps 1 to 6 as documented | **1** | 2 `installed-plugin` (the host cache directory for the plugin, left by `claude plugin uninstall`), the ledger `local-branch`, `local-branch: sdlc/goal-1` |
| After additionally `git branch -D` of both local branches and removing the two cache directories | **0** | none |

So the documented gesture does not reach "nothing left behind" on its own. Two gaps:
`docs/uninstall.md` never says to delete the local `sdlc/*` and `sdlc-ledger` branches (step 7 covers
only the remote branch), and `claude plugin uninstall` leaves the plugin's cache directory under the
profile, which `leftovers.py` correctly reports. The exit-0 row is therefore a result of steps the
document does not give. Neither gap is fixed by this change; the owner decides whether the document
or the checker moves.

Controls on the clean state: planting one residue at a time (`.sdlc`, an ignore block, a torn
AGENTS.md marker, a Cursor rule, the stale hooks path, a local `sdlc/` branch) turned the checker to
exit 1 naming that row each time, and exit 0 again once removed. The pid and worktree rows were red in
the adopted state above and went green through steps 1 and 3.

## 3. Rollback against a LOCAL rehearsal tag

A scratch "release repository" was made from this commit's tree (`git archive`), with no remote.
Version A and B differ only in the manifest version, which is `0.0.1` for tag `rehearsal-a` (known
good) and `0.0.2` for `rehearsal-b` (the "bad" release). Both tags are local and annotated. In an
isolated profile (`HOME`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `XDG_CONFIG_HOME` under scratch):

1. Install B by the third and fourth gestures of `docs/release.md`: `git clone --branch rehearsal-b`,
   `claude plugin marketplace add <dir>`, `claude plugin install sigmaloop@sigmaloop`. All exit 0;
   `claude plugin list` reported version `0.0.2`.
2. Roll back: `claude plugin uninstall`, `claude plugin marketplace remove`, the same clone at
   `rehearsal-a`, add, install. All exit 0; `claude plugin list` reported `0.0.1`.
3. The incident-table gesture (revert the default branch to the last-good tag's tree as a NEW commit):
   `git checkout rehearsal-a -- .` then commit. `git diff --stat rehearsal-a HEAD` was empty, and the
   bad commit stays an ancestor, so history was not rewritten.

Controls: `git clone --branch rehearsal-zzz` exited 128 (no such tag); the installed version before
and after the rollback differed (so the rollback was not a no-op); the same tree comparison against
`rehearsal-b` was non-empty (so the empty diff against A is not vacuous).

The first two gestures of `docs/release.md`, `<repo>@<tag>` and `<repo>#<tag>`, both exited 1
(`Path does not exist`) when given a local path, as the earlier probe
(`pin-rollback-2026-10-02.md`) saw over the network. They stay unverified; a local path cannot test
them.

## What was NOT measured

- Linux, and any host other than this one macOS machine. Nothing here is a Linux result.
- The real tag. No tag was created or pushed; the rollback above is against two throwaway local tags
  in a scratch repository with no remote. The real-tag rollback is past launch and the owner's.
- Anything GitHub-backed: no remote, no labels, no board, no `sdlc-ledger` remote branch, so uninstall
  step 7 did not run and `leftovers.py --github` was not exercised.
- A real predecessor repository. The migration inputs are fixtures shaped by the repo's own builder
  plus invented records (the in-flight ledger and timing schema ids are not ones Sigma knows, and
  were correctly left as is). A real adopted repository may hold shapes these do not.
- Scale beyond 22 files. `migrate.py` documents a separate large-directory measurement; this run did not repeat it.
- Codex: `docs/uninstall.md` says Codex plugin removal must be verified on the target host; it was not.
- The shared-path inventory (`shared-sdlc-paths-4c8562f.json`) is separate evidence and was not re-run.

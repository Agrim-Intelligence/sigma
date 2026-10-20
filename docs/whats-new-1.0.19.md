# What is new in 1.0.19

This page lists every change in this release in grouped, readable form, each with its issue number. The same
changes are in the `1.0.19` section of the [CHANGELOG](../CHANGELOG.md) in the order a maintainer reads them.
Start with [Upgrading from 1.0.18](#upgrading-from-1018) if you only want to know what changes for you.

Almost everything here was found by rolling 1.0.18 out to private product repositories: a verify grant that broke
vitest 2.x, ledger entries a stopped watcher left behind, GraphQL quota spent by several loops on one account, Chain
reads that a real backlog tripped, and files Sigma left in the root checkout. The release also adds `/sigma-dag`, one
dependency map shared by the team through the ledger branch, a host-login mode for upkeep's agent level, and control
over the loop session's own model.

Most entries were proven with fakes and temporary repositories. Where an entry was not measured live, it says so, and
the last section lists what is open.

## Upgrading from 1.0.18

Steps 1 to 3 apply to everyone. The rest apply only if you use the feature named.

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`; the
   watcher now exits within about five seconds plus any tick already running (#1937). Remove the file, then start the
   watcher again.
2. **Check the loop session's model (#1951).** With `model_selection: "auto"`, the supervisor now passes
   `--model sonnet` to each loop session when `SIGMA_CLAUDE_CMD` is not set, because the session itself only
   orchestrates and every phase runs at its own predicted tier. Set `loop.session_model` to choose another, or set
   `SIGMA_CLAUDE_CMD` (never rewritten). `/sigma-doctor` has a new `loop session model` row.
3. **Run `/sigma-doctor` once.** The applier's heartbeat and sync cursor moved to `.sdlc/state/autonomy/` (#1938): the
   doctor reads the applier as not yet ticked until its next tick writes the new heartbeat. If your repository
   tracks the old `.sdlc/autonomy/applier-heartbeat.json` or `sync-cursor.json`, remove them from git in one commit.
4. **If you publish decision records (`rulebook.records.publish`),** they are no longer committed onto the default
   branch (#1947). `decision_store.py publish <state-folder> --branch` writes them to the local branch
   `sigma/records` without moving your checkout; open a pull request from it when you want them on `main`.
5. **If the ledger is on and `discovery.dag.mode` is not `off`,** the map is now shared on the ledger branch
   (#1956-#1959): expect `dag/map.json` and `dag/lease/` files there, and one machine per repository refreshing from
   GitHub at a time.
6. **If you use upkeep's agent level on a logged-in CLI profile,** see [upkeep host login](#upkeep-host-login).

What stays unchanged:

- Your `.sdlc/config.json` is never rewritten by an update.
- With the ledger off, the map, its refresh and its pages behave as in 1.0.18.
- `upkeep.conflicts.agent.login` defaults to `"scrubbed"`, byte-identical to before.

## Fixes from the rollout

- **Verify on vitest 2.x works again (#1931).** `VITEST_MAX_FORKS` and `VITEST_MAX_THREADS` are exported only when the
  installed vitest is 3.x or later; with either set, vitest 2.x collected no tests, so every goal's verify failed.
- **A stopped writer's ledger entries are published (#1932).** A dead process's entries file on this machine is
  staged by the next publish, and a watcher publishes its own file on a clean shutdown, so a pull is no longer
  deferred by a dirty file.
- **GraphQL is budgeted per account (#1939).** One shared bucket file for every watcher and loop session on the
  machine, a wait read from the refused response's own headers (never `/rate_limit`, which reported a different
  count), a secondary limit named as one, and the label check over REST, so a plan or a pick works with GraphQL
  refused. Not measured against a live secondary limit.
- **Nothing left in the root checkout by a landing (#1941, #1947, #1938).** The pick-time registry write rides the
  goal's own commit, decision records stay off the default branch, and the applier's runtime files are ignored.
- **An empty backlog builds a usable, empty map (#1933).**
- **A docs-only goal inside a unit is still docs-only (#1970):** the unit registry counts as documentation.
- **Label plans re-read a listing that may lag a label write (#1943).**
- **A docs-only plan reaches `work.py pr` without `--no-tests` (#1942),** and the PR is titled after the goal, not
  the last fix-up commit (#1014).
- **An epic-shaped issue with no upper-tier label is refused by promote and listed by the doctor (#1948).**
- **Upkeep's mechanical level unions two-sided additions under dated headings (#1949)** and, opt-in, in files listed
  under `upkeep.conflicts.union_paths` (#1968), keeping each side's new lines where that side put them.

## Sigma Chain reads a real backlog better

- **`@me` resolves in a read-only plan (#1934),** so the assignee filter applies.
- **`take it over` assigns the runner named by `--as` (#1935).**
- **Plans in one session share a read cache (#1936)** keyed by issue number and `updated_at`.
- **More dependency wordings are read (#1940):** slice-code chains whose codes carry `(#N)`, `X on Y (#N)`, and
  `waiting on #N` and `#N gates #M` in a trusted comment.
- **"In progress" needs a live holder (#1945):** a label with no live worker or unexpired claim is planned as stale.
- **`deps.py report --label` and `--chain` report every member in one process (#1944).**

## See the map, and share one map

- **`/sigma-dag` (#1954).** One report of the stored map in the chat (state, freshness, shadow line, stuck and active
  chains, cycles, where the files live), `/sigma-dag <N>` for one chain, `--html` for the offline pages. Backed by
  `dag.py report`.
- **One team map on the ledger branch (#1955).** The map snapshot is published as `dag/map.json` and `DAG.md` is built
  from it (#1956); one machine per repository refreshes from GitHub at a time, by a lease that lapses when its holder
  dies (#1957); every history record has an identity, so a change two machines saw counts once (#1958, #1967); the
  doctor and `/sigma-dag` say where the map came from and who refreshes it (#1959). Measured on the test scenario
  (two machines, two hours): 4 collections before and 2 after. Not measured across real machines.

## Model use

- **The loop session's own model is set and shown (#1951, #1961).** See upgrade step 2.
- **The effort predictor gets the tier predictor's safeguards (#1950).** `high` needs a title hit or two body hits
  outside code spans and paths; on one week of two private repositories, 34 of 56 high-effort goals came from a
  single incidental body word. Live spend after the change is not measured.

## Upkeep host login

- **Opt-in `upkeep.conflicts.agent.login: "host"` (#1946, #1963, #1966).** The resolver, the reviewer and the
  logged-in preflight use the watcher's own `HOME` and `CLAUDE_CONFIG_DIR`; every other wall stays (the confined
  copy, the tool allowlist, no git, gh or ssh credential, the timeout and the spend keys), and the model tiers are
  capped by `model_selection_max_tier`. On macOS the CLI keeps its login in the keychain, which the session wall
  denies, so host mode hands the session `CLAUDE_CODE_OAUTH_TOKEN` when the watcher has it (made once with
  `claude setup-token`); without it the unit parks and the reason names that command in full.
- **Proven against a fake CLI only.** A real-CLI run on macOS parked before any model call, as designed, when no
  token variable was set. The recorded real-model run is still to be done.

## Also in this release

- **The doctor's `resolver configured` row names the model after the ceiling cap (#1953).**
- **Sealed, maker-blind acceptance tests gain `work.py seal-tests` and `seal-retire` (#853, #855),** off by default.

## Not in this release

- A recorded real-model run of upkeep host login (above).
- Live measurements of the GraphQL budget, the shared map's read count across machines, and Chain's read cache.
- A dependency written only as a slice code with no issue number anywhere is still not read.

# What is new in 1.0.17

This page lists every change in this release in grouped, readable form, each with its issue number. The same
changes are in the `1.0.17` section of the [CHANGELOG](../CHANGELOG.md) in the order a maintainer reads them.
Start with [Upgrading from 1.0.16](#upgrading-from-1016) if you only want to know what changes for you.

The first theme is the stored dependency map: one local file that records which goals and pull requests wait on
which, rebuilt from GitHub on a schedule and read by the picker, the status line, the doctor and a set of
commands. It is documented in [the dependency map](dag.md) and switched by `discovery.dag.mode`. The second theme is
Sigma Chain: plan every open prerequisite behind one goal, answer the decisions once, approve, arm and run it with a
spend cap, documented in [the chain reference](chain.md). The third is follow-up fixes to the 1.0.16
[resource guard](parallelism.md) and [label drains](label-drain.md).

Several parts were proven only against fakes or fixtures. Where a claim below was not measured against a real
repository, the entry says so, and the last section lists the two runs that are still open.

## Upgrading from 1.0.16

Steps 1 to 4 apply to everyone. Steps 5 and 6 apply only if you use the feature named. Script paths are under
`skills/sigma-loop/scripts/`.

1. **Know what the map mode is on your repository.** The mode is `discovery.dag.mode`, read by
   `dag_store.mode`: `off`, `shadow` or `on`; a missing or unrecognised value (including `OFF`) reads as `shadow`
   (#1610). Your `.sdlc/config.json` is never rewritten by an update, so a config written before this release has
   no `discovery.dag` block and reads as `shadow` without starting anything: the automatic refresh, the doctor row
   and the session-start age line run only when the mode is written as `shadow` or `on` (#1612). A repository set up
   with this release's config template gets the block written, `"mode": "shadow"` with `ttl_minutes` 60,
   `order` `chain` and `cycle_comment` `true`, so it refreshes the map on its own. The shipped default is
   `shadow`, not `off`.
2. **What `shadow` does.** The picker still decides exactly as before. At each pick Sigma also asks the map and
   appends one numbers-only line per compared candidate to `.sdlc/state/dag-shadow.jsonl` (#1619). Three things
   can still be visible once a map file exists and the mode is not `off`: the pick order puts the goal that
   unblocks the most work first within a tier when `discovery.order` is `priority`, the default (#1620; set
   `discovery.dag.order` to `off` to keep the old order); a refresh that saved the map posts one comment per
   distinct dependency cycle on its lowest-numbered open member, numbers and a fixed instruction only (#1615; set
   `discovery.dag.cycle_comment` to `false` to stop); and a `Blocked by #N` line that would close a cycle is
   refused when a goal is filed (#1616). The map and its local history are written under `.sdlc/state/`. The board
   mirror schema is now `board-mirror/v3`, so an existing mirror is refetched once (#1608).
3. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`,
   wait for it to exit, remove the file, then start the watcher again. A leftover `watch.stop` makes the next
   watcher exit at once.
4. **To turn the map off,** set `discovery.dag.mode` to `off`: nothing is read, written or created, and every
   reader behaves as before the map existed. The mode `on` makes the pick hold a goal from the map; do not set it
   before the shadow comparison has run for the days and picks the doctor row names (see [the dependency map](dag.md)).
5. **If you want Chain,** nothing starts by itself. `chain.py` is run by hand, one cycle per `run` call, and
   `chain.parallel` is off by default. `chain.py plan <sdlc_dir> --longest` needs the map; with the mode `off` or an
   unusable map it is refused with exit 2 (#1591). Read the "Solving a whole dependency chain" block in
   [the chain reference](chain.md); `init` adds it to a new project's agent file and never edits an existing one
   (#1593).
6. **If you run label drains or the resource guard,** the fixes below change a few verdicts and wordings: an armed
   run-label member with no assignee is now `unassigned` (#1807), `next --label` re-checks readiness for every label
   (#1805), and `SIGMA_RESOURCE_MODE` now decides a verify run in the environment over the config (#1804).

What stays unchanged:

- Your `.sdlc/config.json` is never rewritten by an update.
- `chain.py plan <sdlc_dir> <N>` without `--longest` works as before.
- With `discovery.dag.mode` set to `off` the picker, status, doctor and filing paths read and write nothing new.
- The resource guard defaults from 1.0.16 are unchanged; its five ratios are still uncalibrated.

## The stored dependency map: store and reads

- **A pure dependency-graph core, `dag_core.py` (#1609).** One standard-library, no-I/O module builds the blocker
  graph: unresolved closure, blocker-first order and waves, each blocking cycle reported once, depth, open
  dependents, components and leaves. Every walk is iterative, so a 5,000-deep chain needs no recursion limit. A
  merged pull request resolves, a closed-unmerged one does not, and an unknown state never resolves.
- **The file, schema and off switch, `dag_store.py` and `dag.py show` (#1610).** `.sdlc/state/dag.json` (schema
  `dag/1`) is published atomically and holds numbers and codes only, no titles. `load` names a missing, truncated,
  corrupt or other-schema file and never raises; one refresher at a time through a non-blocking lock that fails open
  without `fcntl`. New keys: `discovery.dag.mode`, `ttl_minutes`, `order`, `cycle_comment`.
- **The full read: `dag_collect.py` and `dag.py rebuild` (#1611).** Five open-issue lists (goal, blocked, blocking,
  parked, follow-up) through the shared REST fetch, 2,000 per list at most (a hit marks the map `partial`, which
  `usable` refuses). References outside the lists are read newest first, 25 per refresh, resolved ones cached seven
  days. A failed list or an empty answer leaves the old file byte-identical. The private-clone call count and wall
  time are owner-gated and not yet taken.
- **Refresh policy, liveness and an age line (#1612).** `dag_refresh.refresh_if_due` refreshes after
  `discovery.dag.ttl_minutes` plus a stable per-clone offset of up to 10 percent, within a call cap and time
  budget derived from `SIGMA_WATCH_CALL_TIMEOUT`, from three triggers: a duty inside the reconcile tick (so
  `TICK_CALLS` stays at eight), the first pick of a process, and `dag.py refresh [--force]`. The doctor row
  `dag map` fails after three TTLs with a live loop or watcher, or after two errored refreshes in a row; `dag.py
  line` and the session-start hook print the age and size with no network call. Not measured against a real
  repository.
- **A cheap change check, `dag_check.py` and `gh_api.conditional_get` (#1617).** Once at the start of a pick, one
  `issues?state=all&since=<cursor>` request goes out with the stored ETag; a 304 means not modified and uses no
  quota. A 200 applies each changed item to the map in place and equals a full read of the same final state; more than
  500 changed asks for a full refresh; any failure leaves the map untouched and the pick takes today's path. Known gaps
  left to the hourly full read: a priority changed only on the board field, and a deleted or transferred issue. Not
  measured against a real repository.
- **Outside nodes expire on their own read time, and an empty goal list never replaces a good map (#1829).** Every
  node read outside the lists carries `read_at`, and the seven-day cache honours it (a node with no stamp is read
  again once). A refresh whose goal list is empty while another is not keeps the old file with `last_error`
  `EmptyList` (provisional rule, owner to ratify).
- **Other-repository references are not stored as local edges (#1608).** `blocker_scan.extract_refs` now calls
  `is_cross_repo` for every match, so `owner/repo#N` and `repo#N` are skipped; the mirror schema bump is in step 2.
- **A refresh keeps the unchecked pairs recorded while it ran (#1847).** After a saved refresh only the pairs read
  before it began are dropped; a pair recorded mid-refresh stays for the next one.
- **The insert no longer reads an unknown node as "no cycle", and the unchecked list is locked (#1844).**
  `closing_members` over a usable map answers undecided when the blocker or the goal is missing from it, never an
  empty list. The unchecked list has its own lock file. Without `fcntl` the refresh lock yields `open` and `insert`
  has no mutual exclusion; two interleaved inserts leave a valid map, though the later save may drop the earlier node
  until the next refresh.
- **A pick trace failure never replaces a pick's result (#1815).** `Recorder.end` and the call-count hook drop any
  exception, so a failure building the trace line cannot replace what `loop._next` returns.
- **Opt-in pick trace (#1607).** `SIGMA_PICK_TRACE=1` or `loop.py next --trace` appends one JSON line per pick to
  `.sdlc/state/pick-trace.jsonl` (rotated at 1 MB) with call counts by class and seconds; `loop.py
  pick-trace-report <dir>` prints per-stage median and p90. Off by default. The private-clone measurement is
  owner-gated and not yet taken.
- **Research note on the host's native blocked-by relations (#1628).** `docs/dag-native-relations.md` records the
  read-only endpoints, measured cost and conditional-request behaviour, the conflict rule and a recommendation: do
  not build yet. No product code.
- **The native-relations note names the store's real edge sources (#1827).** It now lists `body`, `comment`,
  `timeline` and `other`, and a test keeps the note and the store's list in step.

## The stored dependency map: history and publishing

- **A local history, `dag_history.py` (#1622).** Every saved map appends what changed since the one it replaced to
  `.sdlc/state/dag-history/YYYY-MM.jsonl`: numbers, event names and timestamps only, append-only, never pruned by
  Sigma, nothing when the mode is `off`, and a history failure never fails the save. `dag.py history --month
  YYYY-MM` prints one month; the doctor warns above 1 MB in one month. Sized at about 100 bytes a record; the
  30,000-node diff time is measured by the test. A map cut short by a read cap records no removal, deletion or chain
  change.
- **Published history on the ledger branch (#1623).** `dag/YYYY-MM/<writer>.jsonl`, only when `ledger.enabled` and
  the ledger worktree exists; numbers only, one file per writing process. `sync.publish` stages the actor's own dag
  files, and `.gitattributes` gains `dag/**/*.jsonl merge=union` additively.
- **A generated `DAG.md` at the root of the ledger branch, `dag.py history --all` and a doctor row (#1624).** It is a
  pure function of every writer's records (stuck chains most freeing first, active chains, cycles, completed this
  month, counts; ticket numbers only), rewritten only when `ledger.enabled`, the mode is not `off` and a record
  exists. Provisional: with no parked state in history, "stuck" means an unresolved node holding other unresolved
  nodes with no unresolved blocker of its own.
- **The dependency map has its own page, `docs/dag.md` (#1627).** What the map is and is not, every
  `discovery.dag.*` key, the chain query, stuck chains, cycles, the shadow-to-on criterion, privacy, the measured
  refresh numbers (and what is not measured) and the limits. `tests/test_dag_docs_1627.py` checks the keys, verbs
  and paths it names against the code.
- **Offline harness for the pick-time miss test and the fresh-child timing run, `tools/dag_miss_test.py` (#1618).**
  It refuses a clone whose remote is not the one configured repository, a public repository, or a run without
  `--confirm`. Proven only against an in-memory fake; the live runs on a private clone are owner-gated and not yet
  taken, so no timing or miss rate is stated.

## The stored dependency map: what reads it

- **The chain query, `dag.py chain|chains|stuck` and `dag_chain.py`, schema `dag-chain/1` (#1613).** Lists every
  unresolved transitive blocker of one goal, blockers first, with waves, a decision for each member that needs a
  person and a `closure_id`. `--verify` re-reads up to 50 open members live; past the cap the answer is marked
  incomplete. Read-only; exit 0 answered, 2 goal unknown, 3 map unusable, 4 mode off.
- **Root-cause lines for stuck chains (#1614).** `dag_stuck.py` renders one line per decision root, most freeing
  first, such as `#X, #Y wait on #R's decision; /sigma-unpark #R`. A pick that finds nothing pickable prints these
  only when every held goal waits on a person's decision or a cycle; `status` adds `stuck chains: N`. Local file
  only; mode `off` or an unusable map prints what it printed before. Not measured against a real repository.
- **Cycles are reported and held, `dag_cycles.py` (#1615).** The pick summary no longer says a cycle becomes
  pickable by itself; it prints `dependency cycle: #A, #B`, `/sigma-status` adds a segment and the doctor a `dag
  cycles` row. After a saved refresh one comment per distinct cycle is posted (a marker stops a repeat, at most 20
  per refresh); members are never claimed and no label is written.
- **A goal behind a cycle is told about it too (#1841).** `dag_cycles.holding` returns the cycles a goal is in or
  waits behind. The module docstring states the two-clone double-comment window and the shared call cap.
- **A filed goal enters the map at once, and a `Blocked by` that would close a cycle is refused (#1616).** The new
  issue and the edges of its body are inserted; the cycle check uses the stored map, else a live walk of at most 25
  reads, and raises `CycleRefused` naming every member. Over the cap the write is allowed and the pair is kept in
  `state/dag-unchecked.json` (at most 200). A busy lock, a missing map or an insertion error never fails the filing.
  Not measured against a live repository; the read counts are arithmetic.
- **The pick can hold a goal from the map, and compares it with the board mirror first (#1619).** In `shadow` the old
  decision is returned and each compared candidate appends a numbers-only line (agree-hold, agree-free, map-only or
  old-only, with a cause for each differing blocker). Written `on`, a usable map with a change check inside ten minutes
  decides; every unusable clause prints one stderr line and takes the old path. A pull request blocker holds until
  merged. New `dag shadow` doctor row, flip criterion 7 days, 200 picks, 0 unknown. Not measured against a real board:
  the disagreement rate is what the shadow measures.
- **The shadow counts distinct picks (#1857).** `compared` is distinct (goal, minute) pairs, so the 200-pick
  criterion counts distinct picks. The map hold can name a blocker's phrase, but the stored map keeps no phrase text,
  so today every map hold still reads `blocked by`.
- **Biggest chain first on both pick queues (#1620).** `dag_order.py`; `(-open dependents, -depth)` goes right after the
  unit rank. It never crosses a tier or the blocking split and is unchanged with `discovery.dag.order: off`, no usable
  map or `discovery.order: created`.
- **A batch reads the map once and sweeps auto-unpark once (#1621).** `next_batch` shares one loaded map across
  slots and runs the auto-unpark sweep on the first slot only. Without the batch (the `next` verb, refills) nothing
  changes. Not measured against a real board.
- **The auto-unpark sweep reads the map first (#1625).** Only when the mode is written exactly `on`, the map is usable
  and fresh, and the goal has an open explicit blocker: that `sdlc:blocked` issue gets no comment read and no state
  read. Every other state takes today's full check; parked issues are never resumed. In the 25-issue test fixture the
  sweep goes from 25 comment fetches and 25 state reads to 0 and 0.
- **The blocker-priority repair reads the map when it is usable (#1626).** `dag_repair.py`; with the mode written
  `on`, a usable map and a change check inside ten minutes, the corpus, edges, ranks and outside blocker states come
  from the map. Any other state takes today's path. Only body edges and the ledger count on the map path.
  `discovery.blocker_required_ranks` is now iterative, so a 5,000-deep chain no longer overflows the recursion
  limit. Not measured against a real board.
- **The map-backed repair reaches every legacy-labelled corpus blocker (#1859).** Corpus blockers are read stalest
  first (a read stamp in `state/blocker-inside-read.json`, bounded to 500) and keep at least half of the 10-read cap, so
  n legacy-labelled blockers are all stripped within ceil(n/slots) passes. A parked issue is not in the map corpus and
  is never raised or stripped (provisional, owner to ratify). Not measured against a real board.

## Sigma Chain

- **The plan: `chain.py plan <sdlc_dir> <N> [--json]` (#1584).** Lists every open prerequisite behind one goal,
  deepest first, from the map in one local read or, when the map is off, stale or lacks the goal, from the label drain's
  seed closure (at most 50 live; over `chain.max_members`, default 100 from the map, is refused with the count; a cycle
  is refused naming every member). GET-only toward GitHub; the one write is `.sdlc/state/chains/c<N>.json`. Measured
  with a counting fake; network wall time was not measured.
- **The longest chain first: `chain.py plan <sdlc_dir> --longest [--by freeing] [--json]` (#1591).** Offers the top
  three end goals from the map only; ordered by priority tier, deeper chain, more members, oldest `updated_at`
  (provisional), then number. It writes no file and calls no GitHub. `chain.auto_longest` is documented as reserved:
  nothing reads it and the watcher does not honour it.
- **The decision sheet: `chain.py sheet` and `chain.py answer` (#1585).** `sheet` reads only the local record and prints
  every decision together, the recommended answer first, the exact writes that follow approval, then `Nothing has been
  changed yet.` `answer` records answers from a bank of options, never free text, and hands a parked member's answer to
  the unpark resolve path. `--defaults` accepts every recommended answer except a question with none; `--partial` is
  refused for now.
- **Approval and arming: `chain.py approve` and `chain.py arm [--dry-run]` (#1587).** `arm` refuses unless the chain
  is approved, with no `gh` call before that check. After approval it creates the label `chain:<N>`, adds it to every
  open member and arms each armable member through the label drain's arm. The goal label is written last per member, so
  a re-run finishes a crashed arm without a second label or comment. Not run against a real repository.
- **Run, stop and resume: `chain.py run|stop|resume` (#1588).** A foreground driver with no daemon, no loop and no
  model call: `run` is one cycle, called again until it reports done. It refuses unless the chain is approved, armed
  and not stopped, checks the spend cap before any claim, and makes one pick through `loop.py next-batch --label
  chain:<root>`. An empty pick is `done` only when every member is confirmed closed; otherwise it is stuck (exit 3) with
  one reason. Not measured: a run against a live repository, the cost of a cycle's reads, and two drivers on one chain
  (the record has no lock; run one).
- **Cost estimate and spend cap, `chain_spend.py` (#1586).** `estimate` gives p50 and p90 cost per goal from earlier
  goals' `spend` events, or `no estimate: only <n> measured goals` under 3. `admit` checks `chain.max_usd`,
  `chain.max_goals` and `chain.max_minutes`, failing closed on an unreadable ledger with a cap set. The cap is checked
  before each claim and can be overshot by the cost of the goals in flight. Measured once, locally: a `spent` scan took
  1.5 ms on 1,000 records and 13.9 ms on 10,000.
- **Spend fixes (#1846).** Fractional cents round up, an invalid `chain.max_added` is refused, and the estimate counts
  only goals whose retro or merge is recorded.
- **Growth within bounds, and a delta sheet (#1589).** Each `run` cycle re-reads the end goal's open closure
  (read-only). A new prerequisite that needs no decision joins through `label_drain.pull`, up to `chain.max_added`
  (default 5) and `chain.max_goals`; others are held in `run.paused` with their dependents while other branches go on,
  and a delta sheet with only the new questions is printed. A fourth delta sheet (`chain.max_reasks`, default 3)
  ends the run stuck. Measured with fakes only. Provisional, owner to ratify: the default source of follow-ups is empty,
  and a resume keeps the pause count.
- **Parallel branches and the single-active-chain lock (#1590).** `chain_parallel.py`; `chain.parallel` (2 or more, off
  by default) widens a cycle, never above `parallel.goals.max_concurrent`, and refuses to widen past the remaining
  `chain.max_usd` at the p90 cost. `.sdlc/state/chain-active.json` refuses a second chain on an intersecting pool or past
  `chain.max_active` (default 1) and reclaims an entry only when its pid is dead and its heartbeat is stale (900 s,
  provisional). `loop.py next-batch` gains an optional `--max-concurrent N`.
- **The `max_reasks` stop and declined prerequisites (#1866).** The stop releases the parallel pool lock, and a
  prerequisite answered `leave it out` is reported as declined, not as waiting.
- **Pool lock docs (#1865).** `docs/chain.md` documents `chain.parallel`, `chain.max_active` and the active-chain lock
  with its stale rule and manual recovery.
- **Status: `chain.py status` (#1594).** `chain_status.py` reads only the local records. States: planned, approved,
  running, stuck(reason), paused(decision), capped, done, stopped. A running chain whose heartbeat is older than
  `chain.stale_minutes` (default 30) prints `running STALE`. `--rebuild` and `--leftovers` are read-only on GitHub; the
  doctor adds `chain: #N <state> (<age>)`. Not measured: the 30-minute default and a rebuild against a live repository.
- **Status edge cases (#1864).** `--rebuild` on a running or armed chain refuses unless `--force` is given (the flag
  name is provisional); a never-armed chain is not a leftover; `--json` reports unreadable records as `{file, state:
  unreadable}`; a record with a malformed member list prints the unreadable line instead of raising.
- **The `sigma-chain` skill and `docs/chain.md` (#1592).** A thin skill over `chain.py
  plan|sheet|answer|approve|arm|run|stop|resume`, with an eval card; the reference lists every `chain.*` key with its
  default, and `tests/test_chain_docs.py` keeps list and code equal. Not available as flags, and said so:
  `--parallel` (use `chain.parallel`) and `--cap-usd` (use `chain.max_usd`); `run --no-grow` was added later in
  this release.
- **The chain agent-file block ships with init and is checked by doctor (#1593).** The block is in this repo's
  `AGENTS.md`, the `project.md` template and the Cursor `sdlc.mdc` rule, byte-pinned by `tests/test_chain_docs.py`.
  `/sigma-doctor` adds a read-only row `chain block in an agent file`, shown only when `.sdlc` exists.
- **An offline end-to-end scenario with four guard controls (#1595).** `tests/e2e/test_chain_scenario.py` drives plan,
  sheet, answer, approve, arm, run and status through `chain.py` argv on a stateful fake host. `docs/chain.md` records
  what it measured and what it did not (no live run).
- **A non-ASCII digit goal argument is rejected with the usage line, exit 2 (#1838).** It used to raise a traceback.
- **The write-surface scan sees `state.atomic_write_text` (#1842).** `chain.py::answer` and nine other local-record
  writers are now listed. Local scan only; no behaviour change.

## Resource guard follow-ups

- **`loop.py verify` follows the one mode resolver (#1804).** `SIGMA_RESOURCE_MODE=observe` logs `would_hold` and admits
  with no config key, and `enforce` in the environment holds (exit 5) even when the config says `observe`.
- **A session lease is measured over its process tree (#1816).** On a cold ring several model sessions are admitted up
  to the derived room, and a verify that holds its own lease is excluded from the session lease.
- **A verify lease no longer reserves a multiple of its peak (#1858).** The ring stored the whole-run peak divided by
  the lease's workers and admission multiplied it again, so a workers 1 lease over a multi-process pytest run reserved
  about five times what the run used (9 GB against 1.77 GB). The ring unit is now bytes per sizeable process, and a
  verify admission is bounded by 1.5 times the largest whole-run peak.
- **A hold is not recorded as UNVERIFIED in the final receipt (#1875).** Exit 5 is shared by the guard's hold and the
  flake UNVERIFIED verdict; the receipt printed a hold as UNVERIFIED. The hold is now carried as a flag.
- **Test hygiene (#1833).** The resource-guard tests restore `sys.modules`, with an autouse fixture and a test that pins
  the restore.
- **Held verify retry time (#1813).** `RESOURCE-HOLD` never advertises a retry sooner than the picker's 30 s floor.
- **Controls and guards (#1797, #1798, #1799).** The controls runner covers every row of the controls table; the label
  history line test fails without its kind guard; the doctor's observe wording, the `start` hold case and the doctor
  resource-row condition are guarded by tests and the configuration page.
- **The zone classifier reads a gitlink with a missing target as existing (#1853).** Fixes a wrong `new` on git 2.49;
  `docs/change-zones.md` carries the new process count and timings.

## Label drain fixes

- **`next --label` re-checks readiness for every label (#1805).** A passed check records its label and later picks skip
  `plan` only inside that label's started run; a refusal still claims and writes nothing.
- **Answering "which kind" declares the kind (#1806).** `answer` declares it (`--dry-run` prints `would declare kind <k>
  on #N`) and the next `plan` asks the member's own question.
- **`unassigned` is its own verdict (#1807, #1824).** `arm --apply` adds only the configured assignee; `assigned-elsewhere`
  is kept for an issue assigned to someone else. `label_drain.py pull` labels, comments on and assigns an outside armed
  blocker with no assignee, and leaves an assigned one alone.
- **`log.py slots` says `admission HELD` when nothing is claimed or renderable (#1808).**
- **The `label-held` stop (#1809, #1832).** It no longer lists goals filed before the drain as filed during it, and
  lists goals filed after it began under the documented gesture too, using `label_checked_at` as the window start.
- **The deny-list park row stops flagging machine parks (#1810).**
- **A `## Done when` list that acceptance refuses is warned by the label plan, and `--draft` is not blocked (#1811).**
- **`feature_rebase.py preview` notes an `upkeep.units.include` entry that matches no registered unit (#1812).**
- **`loop.py start-run` clears the recorded `label_checked` (#1823).**
- **The changelog layout duplicate-control test seeds its own repeated entry (#1828).**

## Dependencies written in prose

- **More explicit dependency forms (#1885).** The gate and the stored map now read `blocked on #N`,
  `blocked until #N` and the follow-up forms (`follow-up to|of|on|from #N`, `follow up`, `followup`,
  `follow-ups`, `follows up #N`, `following up on #N`) as edges. The follow-up forms count only with
  nothing but spaces (or the word `issue`) before the `#N`, so "a follow-up to cover the cap, tracked in
  #13" makes no edge. Measured before the change on the last 1000 issues of this repository: 83 follow-up
  hits in 57 issues, all naming the issue the filing comes after, and 1 hit in 2,944 comments from the
  last ten days, also in the right direction. Left out on purpose: `Prerequisites: #N` (all 11 measured
  hits named a child's prerequisite inside an epic body), `follow-up for #N`, a bare `follow-up #N`,
  `after #N lands` and `needs #N to land`. Every original marker matches exactly as before. Existing
  follow-ups to an open issue now wait for it: on this repository that is four open issues.
- **`deps.py`: read the whole ticket, write one marker (#1885).** `deps.py report <sdlc_dir> <goal>`
  lists every `#N` in the title, body and comments with its sentence and how it is read today (`edge`,
  `comment-edge`, `weak`, `mention`); `deps.py mark <sdlc_dir> <goal> <N> "<why>"` writes
  `**Blocked by:** #N` into the body through the cycle-checked writer filing already uses. The loop skill
  runs the report before the pre-checks and tells the agent to write dependencies only in that form.
  Cost: one issue read plus its comment pages per goal. GitHub mode only.

## Not in this release

- The real Chain run on a private clone (#1870) has not been done. Every chain behaviour above was proven against
  fakes, fixtures and the offline scenario; none was run against a live repository.
- The clause-level control breaks for the chain scenario (#1869) are open: the scenario has four guard controls, but
  each clause of those guards has not been broken on its own.
- The private-clone measurements for the map (call counts, wall time, the miss rate and the shadow disagreement rate)
  are not taken, so none is stated here.

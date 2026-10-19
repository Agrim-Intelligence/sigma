# The dependency map and Sigma Chain

Sigma reads which goals wait on which from the text of your issues. Three things are built on that one reading:
the **pick gate**, which holds a goal while a blocker it names is open; the **dependency map**, one local file
that stores the whole graph so other commands can ask about it; and **Sigma Chain**, which plans every open
prerequisite behind one goal, asks every decision once and then drives the chain to done. Back to the
[README](../README.md).

In one paragraph: write a dependency as a `**Blocked by:** #N` line in the issue body. The gate reads it at
pick time, the map reads it on every refresh, and Chain reads it from the map. Other phrasings are read too,
but only a few are trusted to make an edge; the rest are advisory, because a phantom edge parks a goal that
nothing will unpark and can write to an unrelated issue. When a ticket states a dependency in prose or only in
a comment, `deps.py report` shows every reference in it and `deps.py mark` turns the real ones into that line.
The map ships in `shadow` mode, where it watches and compares but decides nothing. Chain starts nothing by
itself: every step is a command a person (or an agent told to) runs.

This page is the full reference for all three. The detailed pages stay where they were:
[the dependency map](dag.md) (the map's own page, with the chain query schema and refresh cost),
[the chain reference](chain.md) (the agent-file block, the scratch scenario and the active-chain lock),
[the native relations note](dag-native-relations.md) (why the host's own blocked-by relations are not read)
and [the miss test](dag-miss-test.md) (the owner-gated live check of the map). Every key is also in the
[configuration reference](configuration.md).

## Contents

- [Words used here](#words-used-here)
- [The parts and how they fit](#the-parts-and-how-they-fit)
- [How a dependency is read](#how-a-dependency-is-read)
- [Reading the whole ticket: deps.py](#reading-the-whole-ticket-depspy)
- [Turning it on, step by step](#turning-it-on-step-by-step)
- [What it will never do](#what-it-will-never-do)
- [The map: modes, refresh and what reads it](#the-map-modes-refresh-and-what-reads-it)
- [Chain: from plan to done](#chain-from-plan-to-done)
- [Configuration reference](#configuration-reference)
- [Verbs and where each lives](#verbs-and-where-each-lives)
- [What the doctor shows](#what-the-doctor-shows)
- [Failure modes and recovery](#failure-modes-and-recovery)
- [Rollout advice](#rollout-advice)
- [What is not measured](#what-is-not-measured)

## Words used here

| Term | Meaning |
|---|---|
| edge | "issue A blocks issue B", read from text; A is the blocker, B the blocked goal |
| explicit form | a phrase whose shape carries the dependency (`blocked by #N`, `depends on #N`, ...); it makes an edge |
| weak word | `needs`, `after`, `requires`, `waiting on` before a reference; matched, but advisory only |
| marker | the canonical line `**Blocked by:** #N` that every Sigma writer emits and every reader trusts |
| the gate | the pick-time check (`backlog_check.py`) that holds a goal while an explicit blocker is open |
| the map | `.sdlc/state/dag.json`, schema `dag/1`: numbers and codes only, rebuilt from GitHub |
| mode | `discovery.dag.mode`: `off`, `shadow` (default) or `on` |
| shadow | the map is asked at every pick and compared with the gate; the gate still decides |
| closure | every unresolved transitive blocker of one goal |
| decision root | a member of a closure that needs a person (parked, unarmed, unmanaged, another repository, unknown, a cycle) |
| chain | one end goal plus its closure, planned and run by `chain.py` under the label `chain:<root>` |
| sheet | every decision a chain needs, shown together before anything is written |
| arm | give a member the goal label (and what the label drain would add) so the picker can take it |

## The parts and how they fit

| Part | Where | What it adds | Default |
|---|---|---|---|
| dependency vocabulary | `blocker_scan.py` | the one definition of an edge in text, shared by every reader | always on |
| the gate | `backlog_check.py`, `mirror.py` | holds a goal while an explicit blocker is open | always on |
| whole-ticket read | `deps.py` | lists every reference in a ticket; writes a marker for a real one | run by the loop skill's instructions |
| the map | `dag_store.py`, `dag_collect.py`, `dag_refresh.py`, `dag_check.py` | one stored graph, refreshed on a schedule | `shadow` |
| map readers | `dag_pick.py`, `dag_order.py`, `dag_chain.py`, `dag_stuck.py`, `dag_cycles.py` | the shadow comparison, biggest-chain-first order, the chain query, root-cause lines, cycle reports | follow the mode |
| filing guard | `dag_insert.py` | a filed goal enters the map at once; a `Blocked by` line that closes a cycle is refused | follows the mode |
| history | `dag_history.py` | what changed between saved maps, per month | follows the mode |
| Chain | `chain.py`, `chain_spend.py`, `chain_parallel.py`, `chain_status.py`, `chain_forecast.py` | plan, sheet, answer, approve, arm, run, stop, resume, status | nothing runs until you run it |

The order matters. The vocabulary feeds the gate and the map identically, so they can disagree only about
*where* they look, never about *what* counts. The map feeds Chain's plan; when the map is off, stale or does not
know the goal, Chain plans from a live read instead (see [Chain](#chain-from-plan-to-done)). The label drain
([label-drain.md](label-drain.md)) reads prerequisites with the same vocabulary, and Chain reuses its questions
and its arming.

## How a dependency is read

All of this lives in `skills/sigma-loop/scripts/blocker_scan.py`. The pattern is one construction: a trigger
phrase, then at most 40 characters that contain no newline, `#` or clause punctuation (`, ; . ! ?`), then
`#N`. A trigger and a reference on opposite sides of a comma or full stop are almost never a dependency, so
`waiting on the sign-off, see #N` does not match. The cost is a real miss: `blocked by the parser, see #N`
does not register either. A missed edge costs one out-of-order pick; a phantom edge costs a goal parked for
good, which is why the trade goes this way.

### The explicit forms

These make an edge wherever the reader looks (the exceptions are under [Where each reader looks](#where-each-reader-looks)):

| Form | Example | Notes |
|---|---|---|
| `blocked by` | `**Blocked by:** #N`, `Blocked by #N` | the canonical marker; what every Sigma writer emits |
| `depends on`, `depends upon` | `this depends on #N` | |
| `blocked on`, `blocked until` | `blocked until #N` | read as `blocked by` in other words |
| `follow-up to`, `of`, `on`, `from` | `Follow-up to #N` | tight: only spaces, or the word `issue`, between the phrase and `#N`; `follow up`, `followup` and `follow-ups` are the same form |
| `follows up`, `following up on` | `follows up #N` | tight, as above |

The follow-up forms are tight because their loose versions point the wrong way: "filed a follow-up to cover the
cap, tracked in #N" names the child, not the parent. Measured on this repository before the change, over the
last 1000 issues (title and body): 83 follow-up hits in 57 issues, every one naming the issue the filing comes
after; `blocked on` and `blocked until` had 0 hits and were added because they say `blocked by` in other words.

Left out on purpose: `follow-up for #N` and a bare `follow-up #N` (a parent's own list of its children reads
exactly like that); `Prerequisites: #N` (all 11 measured hits sat in one epic body, each naming a child's
prerequisite, so each would have been a phantom edge on the epic); `after #N lands` and `needs #N to land
first`; and `builds on`, `see #N`, which carry no direction at all.

### The weak words, and why they are advisory

`needs`, `after`, `requires` and `waiting on` are ordinary English, and they make no edge anywhere. The gate
matches them at check time, over the issue's 500-character body excerpt and its comment text, and a match is
**advisory**: it is shown in the proceed note so a person sees it, and the gate never parks or resolves on it
alone. `deps.py report` lists them as `weak`, and Chain's live plan counts them; neither treats one as an edge.

The reason is measured, not a preference. The vocabulary was calibrated against a 500-character excerpt. When
the whole body began to be scanned (most issues on this repository are longer than the excerpt), the same words
over roughly ten times the text produced a phantom edge from narrative prose about a past event, "after Phase 2
(#N) went live". A confident finding is not passive: it is handed to the blocker resolver, which writes to the
referenced issue (a label swap, a board move and a comment) and marks it `sdlc:blocking`. One sentence of
English would have stalled a real goal and changed an unrelated issue, unattended. So the whole-body scan uses
only the explicit forms, and a weak-word match anywhere is evidence, never an action.

Two escapes exist for a match that is still wrong. `backlog_check.py dismiss-text <kind> <ref> [reason...]`
prints a dismissal marker to post as a comment, which retires that one finding; a `sigma:keep-parked` marker
opts a goal out of the auto-unpark sweep entirely.

### Text that is never scanned: the unpark Q&A block

When a person answers the questions that parked a goal, `/sigma-unpark` writes the answers into the issue body
between `<!-- sigma:unpark-qa:start -->` and `<!-- sigma:unpark-qa:end -->`. That span is stripped before any
scan, by the gate, the map and `deps.py` alike. Without it, a reasonable answer such as "waiting on the design
sign-off, see #N" would plant a blocker in the body of the goal that was just unblocked, and the next pick
would park it again. A start marker with no end strips to the end of the text, because a half-written block is
as full of English as a whole one. Only the fenced span is skipped: a `Blocked by #N` anywhere else in the body
is still read. A block written under the plugin's previous name is stripped the same way.

### References to another repository

`owner/repo#N` and `repo#N` name another repository's issue. They never become a local edge: a reference whose
`#` follows a letter, digit, `_`, `/`, `-` or `.` is skipped by every reader. The label drain lists such a
reference as unresolved (it keeps its dependent out of the ready set), the chain query reports a member that
needs one as a `cross-repo` decision, and `deps.py report` leaves it out.

### Where each reader looks

| Reader | Title and body | Comments | Weak words |
|---|---|---|---|
| the gate, at pick time | explicit forms over the whole body (through the board mirror's stored references), all forms over the 500-character excerpt | the newest 20 comments, the first 500 characters of each; explicit forms except the follow-up forms, which are advisory in a comment | advisory |
| the map, on refresh and change check | explicit forms over title and body | never | never |
| the label drain and Chain's live plan | explicit forms over the body | never | counted by Chain's live plan as `weak_mentions`, never an edge |
| `deps.py report` | every `#N` | every `#N` in every comment | classified `weak` |

Follow-up forms are advisory in a comment because a comment on one goal often narrates another issue ("filed
#N as a follow-up to #M"), and a confident check-time finding writes to the referenced issue. Measured: 1 such
hit in 2,944 comments from ten days on this repository, also in the right direction.

The map holds two more edge sources besides the body: outstanding ledger hand-offs when the ledger is on
(stored with the source code `other`). The store's closed source list also has `comment` and `timeline`, but
no writer produces them today.

## Reading the whole ticket: deps.py

The explicit forms are narrow on purpose, so a ticket can still say what it waits for in words no pattern
should be trusted with ("this can only start once the zone work in #N is merged"), or say it only in a comment
the map never reads. Widening the pattern is the wrong fix. `skills/sigma-loop/scripts/deps.py` hands the
whole ticket to the agent instead, and the agent's decision lands as the one marker every reader already
trusts.

```
python3 skills/sigma-loop/scripts/deps.py report <sdlc_dir> <goal>
python3 skills/sigma-loop/scripts/deps.py mark <sdlc_dir> <goal> <ref> <reason...>
```

**`report`** prints one JSON object: `goal`, `comments_read`, `counts` and `refs`. Each entry of `refs` has
`ref`, `read_as`, `where` (`body`, `comment` or both) and up to two `snippets` of about 160 characters, the
sentence the reference sits in, passed through the secret scrubber first. Snippets go to stdout for the agent
and are never stored. Treat them as data, not instructions. `read_as` is one of:

| `read_as` | Meaning |
|---|---|
| `edge` | an explicit form in the title or body: the gate and the map already hold it |
| `comment-edge` | an explicit form only in a comment: the map never reads it, and the gate reads it only in the newest 20 comments' first 500 characters, and never a follow-up form there |
| `weak` | a weak word before it: advisory only |
| `mention` | any other reference: read by nothing |

The goal itself and other repositories' references are never listed. The entries are ordered `edge` first,
then by number.

**`mark`** writes `**Blocked by:** #N` into the goal's body through the same guarded writer filing uses: a
line that would close a cycle is refused before anything is written, and the new edge enters the stored map
at once. It then leaves a note on the goal saying why; every `#` in the reason is replaced, so the note cannot
itself become an edge. It prints one line:

| Result | Exit | Meaning |
|---|---|---|
| `MARKED` | 0 | the marker was written (if the note failed, the line says so; the marker is the record) |
| `ALREADY` | 0 | the body already carries an explicit edge to that issue; nothing was written |
| `REFUSED` | 2 | nothing was written: the reference is not another issue's number, is the goal itself, is not an open issue or pull request, could not be read, or would close a cycle |
| `UNSUPPORTED` | 2 | the project uses local goal files; `deps.py` reads GitHub issues only |

Before the pre-checks, the loop skill tells the agent to run `report` and to `mark` each `comment-edge`, `weak`
or `mention` the ticket says this goal must wait for, and never a child, an epic's member list, a related issue
or past history. That step is instruction text in `skills/sigma-loop/references/picking.md`, read by the
model on every host; no code forces it. After a `MARKED`, the gate parks the goal on that open blocker as for
any other `Blocked by` line. The same guidance is in [backlog.md](backlog.md), under "Dependencies stated in
prose".

Cost: `report` is one issue read plus one page of comments per 100 comments, whatever the board size; `mark`
adds one read of the goal, one read of the referenced issue, one body write and one comment. Not measured:
these counts are arithmetic from the code, not a timing.

**Going forward, write a dependency only as a `**Blocked by:** #N` line in the issue body.** It is the one form
the gate, the map, the label drain and Chain all read in the same way.

## Turning it on, step by step

Nothing below is needed for the rest of Sigma. Do the steps in order and stop wherever you are comfortable.
Commands run from the directory that holds `.sdlc`; scripts are under `skills/sigma-loop/scripts/`.

1. **See what the map mode is.** `python3 skills/sigma-loop/scripts/dag.py show` prints the mode, age and
   counts, or `dag: off`. A config written before the map existed has no `discovery.dag` block: it reads as
   `shadow` but starts nothing (no automatic refresh, no doctor row, no session-start age line). A repository
   set up with the current config template has `"mode": "shadow"` written, so it refreshes on its own.
2. **Write the mode to start the map.** Set `discovery.dag.mode` to `"shadow"` in `.sdlc/config.json`. Writing
   `shadow` or `on` explicitly is what starts the automatic refresh (the reconcile tick, the first pick of a
   process, and `dag.py refresh`). Build the first map at once with `dag.py rebuild` (needs gh signed in).
3. **Read it.** `dag.py line`, `dag.py stuck`, `dag.py chains --rank biggest` and `dag.py chain <goal>` all read
   the stored file and write nothing.
4. **Clean up prose dependencies.** For a goal whose ticket says what it waits for in words, run
   `deps.py report .sdlc <goal>` and `deps.py mark` for each real one. The loop's instructions do this per goal
   before its pre-checks; doing it by hand first makes the shadow comparison cleaner.
5. **Watch the shadow.** The doctor row `dag shadow` counts compared picks and disagreements by cause, and says
   whether the flip criterion is met (at least 7 days, at least 200 distinct compared picks, no `unknown`
   disagreement).
6. **Flip to `on` only when the row says `met`** and you have read the explained causes. The flip is an
   operator act, never automatic. With `on`, the pick holds a goal from the map when the map is usable and a
   change check ran in the last ten minutes; otherwise it says why on stderr and takes the old path.
7. **Chain, when you want it.** Paste the agent block from [the chain reference](chain.md) into `AGENTS.md` or
   `CLAUDE.md` (`/sigma-init` writes it into a new project's files; the doctor reports whether one carries it).
   Set a cap, `chain.max_usd` or `chain.max_minutes`, before the first `run`. Then follow
   [Chain: from plan to done](#chain-from-plan-to-done), or use `/sigma-chain <N>`.

To turn the map off, set `discovery.dag.mode` to `"off"`: nothing is read, written or created, and every
reader behaves as before the map existed. Chain's plan then reads GitHub live.

## What it will never do

- **The map writes no label.** `sdlc:blocked` and `sdlc:blocking` stay what the reconciler writes; the map is
  derived from text and labels and is never a second source of truth. A missing, stale, partial or unreadable
  map makes every reader fall back to exactly what it did before.
- **The map stores no text.** Only numbers, booleans, timestamps and codes from closed sets reach disk: no
  title, label text, author or comment phrase.
- **A weak word never makes an edge**, and never writes to another issue.
- **Another repository's reference never becomes a local edge**, and Sigma writes nothing to another
  repository.
- **`deps.py` never stores a snippet** and never writes anything but the one marker and its note; it refuses
  rather than guess on a closed, missing or unreadable reference.
- **Mode `off` touches nothing:** no read, no write, no file, no doctor row.
- **Chain writes nothing to GitHub before `approve`.** `plan` and `sheet` are read-only toward GitHub (every call
  goes through a GET-only wrapper); `answer` writes only to a parked member, through the unpark gesture, and only
  a bank option, never free text.
- **Chain claims nothing outside its label.** The pick is the loop's own `next-batch --label chain:<root>`.
- **Chain never deletes or removes a label.** `--leftovers` lists chain labels whose members are all closed;
  removing one is a person's step.
- **No daemon.** `chain.py run` is one cycle and exits; something must call it again.

## The map: modes, refresh and what reads it

The map is described in full in [the dependency map](dag.md). In short:

- **Modes.** `off` reads and writes nothing. `shadow` (the default) leaves the pick deciding as before and
  appends one numbers-only record per compared candidate to `.sdlc/state/dag-shadow.jsonl` (rotated at 1 MiB).
  `on` lets the pick hold from the map. Any value other than the three, including `OFF`, reads as `shadow`.
- **The full read.** Five open-issue lists (goal, blocked, blocking, parked, follow-up), at most 2,000 issues
  each; reaching the cap marks the map partial, and a partial map is not used. A reference outside the lists is
  read on its own, newest first, at most 25 per refresh. A failed or empty read leaves the old file as it was.
- **When it refreshes.** After `discovery.dag.ttl_minutes` (60) plus a stable per-clone offset of up to 10
  percent, from three triggers, none trusted alone: the reconcile tick, the first pick of a process, and
  `dag.py refresh [--force]`. The call cap and time budget come from `SIGMA_WATCH_CALL_TIMEOUT` (default 120
  seconds: half of it as the time budget, 1.5 calls per second of it as the cap).
- **Between refreshes.** Once at the start of a pick, one conditional request asks for issues changed since the
  map's cursor; a not-modified answer uses no quota. More than 500 changes asks for a full refresh. Known gaps
  left to the hourly read: a priority changed only on the board field, and a deleted or transferred issue.
- **Usable.** The right schema, built, not partial, and no older than twice the TTL unless a change check ran in
  the last ten minutes.
- **What reads it.** The shadow comparison and (in `on`) the hold; the pick order, which puts the goal that
  unblocks the most work first within a priority tier (`discovery.dag.order`); `dag.py chain|chains|stuck`;
  the root-cause line when nothing is pickable; `/sigma-status` (`stuck chains: N`, cycles); the auto-unpark
  sweep and the blocker-priority repair (only with the mode written exactly `on` and a fresh map); and Chain's
  plan.
- **Cycles.** Found on every refresh, reported in the pick summary, status, the doctor and the chain query, and
  held. A `Blocked by` line that would close one is refused when a goal is filed. With
  `discovery.dag.cycle_comment` on, a saved refresh posts one comment per distinct cycle on its lowest-numbered
  open member (numbers and a fixed instruction only, a marker stops a repeat, at most 20 per refresh).
- **Filing.** A goal Sigma files enters the map at once. With no usable map, the cycle check walks live, at most
  25 reads; past that the write is allowed and the pair is kept in `.sdlc/state/dag-unchecked.json` (at most 200
  pairs) until the next saved refresh.
- **History.** Each saved map appends what changed to `.sdlc/state/dag-history/`, one file per month, never
  pruned by Sigma; with the ledger on, the ledger branch carries `dag/` files and a generated `DAG.md`.

The chain query, `dag.py chain <goal> --json`, is the interface other tools should call: schema `dag-chain/1`,
additive only, with `members` in topological order, `waves`, `decisions` (each with a `why`: `parked`,
`unarmed`, `unmanaged`, `cross-repo`, `unknown`, `cycle`), a `closure_id` to poll, and `--verify` to re-read
up to 50 open members live. Exit codes: `0` answered, `2` the goal is not in the map, `3` the map is unusable,
`4` mode `off`. The full contract is in [the dependency map](dag.md#the-chain-query-contract).

## Chain: from plan to done

The engine is `skills/sigma-loop/scripts/chain.py`; the `/sigma-chain` skill is a thin script over its verbs and
nothing in it is model-driven. The full reference, with the agent-file block, is [the chain reference](chain.md).

1. **Plan.** `chain.py plan <sdlc_dir> <N> [--json]` lists every open prerequisite behind goal N, deepest
   first, with a verdict per member. From the stored map in one local read, bounded by `chain.max_members`
   (100); when the map is off, unusable, stale or does not know the goal, from the label drain's seed closure,
   bounded by the smaller of 50 and `chain.max_members`, one REST read per unknown reference. A closure over the
   bound is refused with its count, never truncated; a cycle is refused naming every member. Priority
   inversions (a blocker less urgent than what waits on it) are reported, never written. The one write is the
   local record `.sdlc/state/chains/c<N>.json` (numbers, verdict words, labels and assignee logins only).
   `--forecast [--as <login>]` marks every member `ready now`, `after #N`, `needs decision`, `someone else's`,
   `checkpoint` or `not workable` and prints how many goals a run by that login can finish before it stops, naming
   the root of each stop; the sheet carries the same block. It uses the rules `run` uses (see
   [the forecast](chain.md#the-forecast)).
2. **Or offer the longest.** `chain.py plan <sdlc_dir> --longest [--by freeing] [--json]` offers the top three
   end goals from the map only (priority tier, then depth, then member count, then oldest, then number;
   `--by freeing` puts the member count first). It writes nothing and calls no GitHub; with the map off or
   unusable it is refused.
3. **Sheet.** `chain.py sheet <sdlc_dir> <N>` prints every decision together, recommended answer first, then the
   exact writes that would follow approval, then `Nothing has been changed yet.` It reads only the local record.
4. **Answer.** `chain.py answer <sdlc_dir> <N> --answers <file>` or `--defaults`. Every answer is a bank option
   (the cap is a positive whole number), never free text. `--defaults` takes each recommended option except where
   there is none, which is the cap.
5. **Approve.** `chain.py approve <sdlc_dir> <N>`: a local state change, refused while a blocking question is open.
6. **Arm.** `chain.py arm <sdlc_dir> <N> [--dry-run]`: the only write path. It creates the label `chain:<root>`
   when missing, adds it to every open member, posts one fixed comment and arms each member the answers allow.
   An upper-tier, parked or gate-held member is labelled, never armed. A re-run after a crash finishes the work
   with no duplicate label or comment.
7. **Run.** `chain.py run <sdlc_dir> <N> [--session-pid P] [--no-grow]`: one cycle (the auto-unpark sweep, the
   spend cap check, a read of the label's members, then one pick). Call it again until it reports done. Exit 3
   means capped or stuck, with one reason: decision, held-gate, claimed, external, rate-limit, degraded-read,
   capped.
8. **Stop, resume, status.** `chain.py stop` and `chain.py resume [--cap N]` change only the local record (a stop
   does not release goals already claimed). `chain.py status [<N>] [--json]` reads the local records and prints
   each chain's state and the age of its last progress, with `running STALE` past `chain.stale_minutes`.

**Growth.** Each `run` cycle re-reads the end goal's open closure. A new prerequisite that needs no decision
joins, up to `chain.max_added` and `chain.max_goals`. One that needs a decision, is over a bound, or arrives
under `chain.freeze` or `--no-grow` is held with its dependents while other branches go on, and a delta sheet
asks only the new questions. Past `chain.max_reasks` such pauses the run ends stuck.

**Cap.** The spend cap is checked before each claim, so it can be overshot by the cost of the goals in flight.
With no cap configured nothing reads the spend ledger; with one set, an unreadable ledger fails closed.
`resume --cap N` caps the number of goals, not dollars.

**Width.** One cycle claims one member unless `chain.parallel` is 2 or more, goal-level parallelism is on
(`parallel.goals.enabled` true and `work.enabled`), and the remaining `chain.max_usd` covers the goals in flight
at the p90 cost per goal. Setting `chain.parallel` or `chain.max_active` turns on the active-chain lock in
`.sdlc/state/chain-active.json`, described with its recovery in [the chain reference](chain.md#the-active-chain-lock).

## Configuration reference

All keys live in `.sdlc/config.json`. A wrong-typed value reads as its default, except for the four cap keys
(`chain.max_goals`, `chain.max_added`, `chain.max_usd`, `chain.max_minutes`): a run with an invalid cap stops as
`capped` and claims nothing, failing closed.

| Key | Default | Meaning |
|---|---|---|
| `discovery.dag.mode` | `"shadow"` | `"off"`, `"shadow"` or `"on"`; anything else reads as `"shadow"`. Only a written `shadow` or `on` starts the automatic refresh, the `dag map` doctor row and the session-start age line |
| `discovery.dag.ttl_minutes` | `60` | refresh cadence; a map older than twice this is not used unless a change check ran in the last ten minutes; the doctor row fails after three of these with a live loop or watcher |
| `discovery.dag.order` | `"chain"` | `"chain"` puts the goal that unblocks the most work first within a tier and unit, on both pick queues; `"off"` keeps the old order. Only body and timeline edges count |
| `discovery.dag.cycle_comment` | `true` | whether a saved refresh posts one comment per distinct cycle |
| `chain.max_members` | `100` | largest closure `plan` accepts from the map (live: the smaller of this and 50) |
| `chain.max_goals` | members plus `chain.max_added` | most goals the chain may hold |
| `chain.max_added` | `5` | goals the chain may grow by when `chain.max_goals` is unset |
| `chain.freeze` | `false` | when `true`, nothing joins by itself; each new prerequisite is asked on a delta sheet |
| `chain.max_reasks` | `3` | delta-sheet pauses allowed before the run ends stuck |
| `chain.parallel` | unset (width 1) | 2 or more: members one cycle may claim at once, capped by `parallel.goals.max_concurrent` (default 3) |
| `chain.max_active` | `1` | most chains running at once; setting it or `chain.parallel` turns the active-chain lock on |
| `chain.max_usd` | unset | dollar cap on spend attributed to members since approval |
| `chain.max_minutes` | unset | minutes cap measured from the approval stamp |
| `chain.stale_minutes` | `30` | a running chain whose last progress is older than this reads `running STALE` |
| `chain.auto_longest` | reserved | documented only: nothing reads it yet |

Related, outside these blocks: `SIGMA_WATCH_CALL_TIMEOUT` (the refresh's budget), `ledger.enabled` (hand-off
edges and the published history), `discovery.order` (the map's order applies only under `"priority"`, the
default), `parallel.goals.*` and `work.enabled` (Chain width), and the `label_drain` block (arming defaults
Chain reuses, see [label-drain.md](label-drain.md)). The dependency vocabulary has no key: it is fixed in code.

## Verbs and where each lives

All scripts are in `skills/sigma-loop/scripts/` unless noted. Map verbs take `--sdlc <dir>`; Chain and `deps.py`
take the `.sdlc` directory as their first argument.

| Verb | Script | Touches GitHub | What it does |
|---|---|---|---|
| `dag.py show` | `dag.py` | no | mode, age, counts and whether the map is usable |
| `dag.py line` | `dag.py` | no | one line, age and size; the session-start hook prints it |
| `dag.py rebuild` | `dag.py` | reads | full read and publish now; prints call counts by class |
| `dag.py refresh [--force]` | `dag.py` | reads | refresh when due, or now |
| `dag.py history [--month YYYY-MM] [--all]` | `dag.py` | no | one month of history records; `--all` adds every writer's from the ledger worktree |
| `dag.py chain <goal> [--json] [--verify] [--include-resolved]` | `dag.py`, `dag_chain.py` | only with `--verify` | the closure of one goal, schema `dag-chain/1` |
| `dag.py chains [--rank longest\|biggest] [--json]` | `dag.py` | no | connected groups of two or more open members |
| `dag.py stuck [--json]` | `dag.py`, `dag_stuck.py` | no | decision roots holding other work, most freeing first |
| `deps.py report <sdlc_dir> <goal>` | `deps.py` | reads | every reference in title, body and comments, classified |
| `deps.py mark <sdlc_dir> <goal> <ref> <reason...>` | `deps.py` | reads, then one body write and one comment | writes `**Blocked by:** #N` |
| `backlog_check.py dismiss-text <kind> <ref> [reason...]` | `backlog_check.py` | no | prints the marker that retires one finding |
| `chain.py plan <sdlc_dir> <N> [--json]` | `chain.py` | reads | plan one chain; writes the local record |
| `chain.py plan <sdlc_dir> --longest [--by freeing] [--json]` | `chain.py` | no | offer the top three end goals |
| `chain.py sheet <sdlc_dir> <N>` | `chain.py` | no | every decision, together |
| `chain.py answer <sdlc_dir> <N> --answers <file>` or `--defaults` | `chain.py` | parked members only | record answers |
| `chain.py approve <sdlc_dir> <N>` | `chain.py` | no | local state change |
| `chain.py arm <sdlc_dir> <N> [--dry-run]` | `chain.py` | writes | label, comment, arm |
| `chain.py run <sdlc_dir> <N> [--session-pid P] [--no-grow]` | `chain.py` | reads and claims | one cycle |
| `chain.py stop`, `chain.py resume [--cap N]` | `chain.py` | no | local record only |
| `chain.py status [<N>] [--json] [--rebuild [--force] [--answers <file>]] [--leftovers]` | `chain.py`, `chain_status.py` | `--leftovers` reads | state and age of each chain |
| `/sigma-chain <N>` | `skills/sigma-chain/SKILL.md` | as the verbs | the skill over `chain.py` |

Library modules (no command line): `blocker_scan.py` (the vocabulary), `dag_core.py` (the pure graph),
`dag_store.py` (the file), `dag_collect.py` (the full read), `dag_refresh.py` (when), `dag_check.py` (the change
check), `dag_insert.py` (filing), `dag_pick.py` (shadow and hold), `dag_order.py`, `dag_cycles.py`,
`dag_history.py`, `dag_repair.py`, `dag_render.py`, `chain_spend.py`, `chain_parallel.py`, `chain_status.py`.

## What the doctor shows

`/sigma-doctor` adds these rows to its report. Each is absent when it has nothing to say, so a clean setup shows
few of them.

- **`dag map: mode ..., age ..., N nodes, N edges, N cycles`** (plus `, N stuck roots` when there are any). A
  liveness row: it fails when the last good refresh is over three TTLs old while a loop or watcher is live, or
  when the last two refreshes errored. Fix: `python3 skills/sigma-loop/scripts/dag.py refresh --force --sdlc .sdlc`
  and read its `last_error`. Absent with mode `off`, and when the mode was never written and no map exists.
- **`dag shadow: N compared picks, N disagreements (pr N, comment-only N, cross-repo N, unknown N), N days in
  shadow, flip criterion met|not met (...)`**. Always OK: it measures and gates nothing.
- **`dag cycles: ...`** names every member of every distinct cycle. Fix: remove one `Blocked by` line in each
  cycle.
- **`dag history: <bytes> in <month>`**, not OK above 1 MB in the current month. Sigma never prunes history; move
  or compress a closed month yourself.
- **`dag ledger: N writers, newest record <age>`**, only with the ledger on. Always OK.
- **`chain: <goal> <state> (<age>); ...`**, one clause per chain record. Fails on a running chain that is stale
  and on a stuck or paused one; the fix is `chain.py status .sdlc` for the root cause.
- **`chain block in an agent file`**, advisory, only when `.sdlc` exists: whether `AGENTS.md`, `CLAUDE.md` or a
  Cursor rule carries the block from [the chain reference](chain.md). The doctor never edits the file.

## Failure modes and recovery

| What went wrong | Effect | Recovery |
|---|---|---|
| a dependency written only in prose or a comment | the map does not see it; the gate may not either | `deps.py report`, then `deps.py mark` |
| a weak word or a follow-up form in a comment matched something unrelated | an advisory note, nothing held | none needed; `dismiss-text` to silence one finding |
| an explicit form matched something that is not a dependency | the goal is held on it | edit the sentence, or post the `dismiss-text` marker |
| the refresh keeps failing (gh signed out, rate limit) | the doctor row fails after two errors; the old map stays, and goes unusable past twice the TTL | fix gh, then `dag.py refresh --force`; every reader falls back meanwhile |
| a list reached 2,000 issues | the map is partial and not used | nothing to repair; the readers take the old path |
| the map file is truncated or corrupt | `load` names it; readers fall back | the next refresh overwrites it; `dag.py rebuild` to do it now |
| a cycle | its members are held and reported | remove one `Blocked by` line per cycle |
| a `Blocked by` line refused at filing | nothing written; the error names the members | drop or change the line |
| the live cycle walk hit its cap | the line was written and recorded as unchecked | the next saved refresh checks it |
| a chain run is stuck | exit 3 with one reason | answer the decision `chain.py status` names, then `run` again |
| a chain ran into its cap | `capped` | raise the cap or `resume --cap N` |
| a chain driver crashed with the lock on | another chain may be refused | `chain.py stop` on that chain, or wait for the stale rule; see [the chain reference](chain.md#the-active-chain-lock) |
| two drivers on one chain | the record has no lock | run one driver per chain |

## Rollout advice

1. **Keep the map in `shadow`.** It is the default for a reason: it changes nothing a pick decides, and its
   visible effects (the order within a tier, one comment per cycle, refusing a cycle at filing) each have their
   own off switch.
2. **Write dependencies as markers.** Ask agents and people to use `**Blocked by:** #N` in the body. Run
   `deps.py report` over the goals that matter and `mark` the real prose dependencies.
3. **Read the shadow row for a week or more** before you think about `on`, and read every explained cause.
   A `comment-only` or `unknown` disagreement can mean an edge that lives only in a comment (see the last item
   of [What is not measured](#what-is-not-measured)): check it with `deps.py report` and mark it.
4. **Flip to `on` only on `met`.**
5. **Try Chain on a small chain first.** Plan, read the sheet, `arm --dry-run`, set `chain.max_usd`, and run one
   cycle at a time. Keep `chain.parallel` unset until a width-1 chain has finished cleanly.

## What is not measured

These pages say so where it applies; here is the list in one place.

- No private-clone measurement of the map has been taken: refresh call counts and wall time, the miss rate of
  [the miss test](dag-miss-test.md), and the shadow disagreement rate. Refresh costs on this page are arithmetic
  from the code; the one timing (graph build at 3,000 and 30,000 nodes) is in [the dependency map](dag.md).
- No Chain run against a live repository has been done. Every chain behaviour was proven against fakes,
  fixtures and an offline scenario; network wall time and the cost of one cycle's reads are not measured.
- The 900-second stale rule for the active-chain lock and the 30-minute `chain.stale_minutes` are defaults, not
  measurements.
- `deps.py` costs are counted from the code, not timed, and how well an agent judges a prose dependency is not
  measured.
- The vocabulary counts quoted above (83 follow-up hits in 57 of 1000 issues, 1 in 2,944 comments, 0 for
  `blocked on` and `blocked until`) are from this repository only.
- A blocker the gate holds from a comment is not in the map. The shadow names it `comment-only` only when the map
  holds a `comment` edge for it, and no writer produces one today, so such a disagreement reads as `unknown` when
  the blocker is in the map and `cross-repo` when it is not. Marking it into the body removes the disagreement.

# Solving a whole dependency chain

The full reference for the dependency map, Sigma Chain and how dependencies are read from issue text is [the dependency map and Sigma Chain](dependency-map-and-chain.md); this page keeps the detail it links to.

`/sigma-chain` plans every open prerequisite behind one goal, asks the person every decision once,
arms the members, and drives them to done. The engine is `skills/sigma-loop/scripts/chain.py`; the skill
is a thin script over its verbs. Nothing here is model-driven: each verb is a plain command.

## Solving a whole dependency chain

Paste this block into the file your agents read (`AGENTS.md`, `CLAUDE.md`, or equivalent). It is the single source; a test pins it. `/sigma-init` writes it into the new `project.md` and Cursor rule (never into an existing file), and `/sigma-doctor` reports whether an agent file carries it.

```
## Solving a whole dependency chain

When a goal is blocked by other open goals, do not walk the blockers one by one. Run the chain:
use the sigma-chain skill, or `chain.py plan`, `sheet`, `answer`, `approve`, `arm`, `run` from
the sigma-loop scripts. It plans the whole chain, asks every decision together, and then solves it.
```

## Flow

| Step | Verb | Touches GitHub |
|---|---|---|
| Plan | `chain.py plan <sdlc_dir> <N> [--json]` | reads only; writes the local record |
| Offer end goals | `chain.py plan <sdlc_dir> --longest [--by freeing]` | nothing, from the stored map |
| Sheet | `chain.py sheet <sdlc_dir> <chain>` | nothing |
| Answer | `chain.py answer <sdlc_dir> <chain> --answers <file>` or `--defaults` | parked members only, through the unpark gesture |
| Approve | `chain.py approve <sdlc_dir> <chain>` | nothing |
| Arm | `chain.py arm <sdlc_dir> <chain> [--dry-run]` | the only write path: label `chain:<root>`, one fixed comment, arming |
| Run | `chain.py run <sdlc_dir> <chain> [--session-pid P]` | one cycle: the loop's label-scoped pick |
| Stop / resume | `chain.py stop`, `chain.py resume [--cap N]` | nothing; local record only |

No GitHub write exists in any verb before `approve`. `run` is one cycle and is called again until it
reports done; exit 3 means capped or stuck with one reason (decision, held-gate, claimed, external,
checkpoint, rate-limit, degraded-read, capped).

## The sheet

Every decision the chain needs, shown together: the label drain's questions over the member set, plus a
cross-unit edge, an end goal that cannot be armed, and the cap. Every answer is a bank option (the cap is a
positive whole number), never free text. `--defaults` takes each recommended option except where there is
none, which is the cap.

## Member states

Live verdicts: armed, unarmed, parked, upper-tier, assigned-elsewhere, held-by-gate, not-a-goal. From the
stored map only the life is known: armed, unarmed, parked, unknown; a member whose life is `blocking` or
unarmed is therefore read once (a GET, at most the plan's member bound) and the live verdict decides, and a
`blocking` member that cannot be read is `unknown` (the plan is INCOMPLETE), never `armed`. An upper-tier,
parked or gate-held member is labelled, never armed.

### Not a goal (`not-a-goal`)

An open issue that carries `sdlc:blocking` but not `sdlc:goal` is something other work waits on that nobody can
start: the loop cannot pick it and the doctor already reports it as a deadlock. It is never `armed` and never the
first pick. The sheet asks one question per such member (`not_goal:<n>`): leave it out (the recommended
answer), arm it as a goal, or treat it as a checkpoint. Only "arm it as a goal" arms it. `chain.py arm` and
`label_drain.py arm` touch such a member, with or without `--apply` or `--dry-run`, only after that answer: with
any other answer, or none, it gets no `chain:<root>` label, no comment and no goal label, and `arm` lists it as
refused. "Treat it as a checkpoint" is recorded only; the label that makes it one is a person's step (see the
dependency docs).

### Waiting for the owner (`awaiting-owner`)

A member that carries the legacy confirmation label (`sdlc:needs-confirmation`, or the name under
`ledger.handoff.proposed_label`), with or without `sdlc:goal`, is a follow-up still waiting for its owner. Approving
a chain is not that confirmation, so it is never armed by `chain.py arm` or `label_drain.py arm`, with or without
`--apply` or `--dry-run`, unless the sheet asked about that member and the person answered "promote it". The
sheet asks one question per such member (`owner_gate:<n>`): leave it out (the recommended answer, so no default
arms), promote it, or close it. Members that share the options are shown as one group (`group:owner_gate`)
whatever the number of questions, and an answer to the group reaches each of them. "Promote it" arms the member
through the same machinery as any other, and also strips the leftover label, so the queue stops refusing it; the
other two answers write nothing (Sigma does not close an issue for you). The same holds for an open blocker that
`label_drain.py arm` would otherwise pull into the label. Known gap: the stored map cannot see the leftover label on
a member that also carries `sdlc:goal`, so the sheet may show it as armed; `arm` reads every member's current labels
and still leaves it untouched, listing it as refused.

## Checkpoints

A checkpoint (`sdlc:checkpoint`, see [the label model](label-model.md#2b-vi-sdlccheckpoint--an-issue-that-means-that-units-code-is-on-main))
stands for "that unit's code is on the integration branch". It is a row in the plan with the verdict `checkpoint`,
its unit and a status (`met`, `waiting` or `unknown`). It is never picked, never armed and gets no `chain:<root>`
label; `Blocked by` on it is an ordinary edge; the sheet shows checkpoints in their own block, with what each one
holds up. When no other reason is open, `run` stops with the reason `checkpoint` and names it; a `met` one says
that its issue is still open and the step that releases it (close the issue). It is met when the unit's tip is
contained in `<remote>/<work.base>` (local git ancestry, no fetch, no network) or a person closes the issue.
A unit landed by squash, or whose branch is gone, reads `waiting` or `unknown` until a person closes the
checkpoint. The loop's own pick gate still reads an open blocker as open, so a `met` checkpoint does not release
its dependents until it is closed: the status is a report, the close is the release.

## Growth

`run` reports members added to and closed in the label since the last cycle. It does not act on them. A
change in membership is bounded by `chain.max_added` (below).

## Cap and honest limits

The cap is checked before each claim, so it can be overshot by the cost of the goals in flight. With no cap
configured nothing reads the spend ledger; with one set, an unreadable ledger fails closed.

`chain.py status` reads the local records and prints each chain's state and the age of its last progress; it
makes no GitHub call. `run --no-grow` is a flag. Run width is not a flag: it comes from the `parallel` key
under `chain` in the project config, off by default (see [parallelism](parallelism.md)). Not available yet: a
`--cap-usd` flag (set `chain.max_usd`). `resume --cap N` caps the number of goals, not dollars. Not measured:
a run against a live repository, and two drivers on one chain (the record has no lock; run one).

## Configuration

Keys under `chain` in the project config.

| Key | Default | Meaning |
|---|---|---|
| `chain.max_members` | 100 | Largest closure `plan` accepts; over it the plan is refused with its count, never truncated. |
| `chain.max_goals` | members plus `chain.max_added` | Most goals the chain may hold. |
| `chain.max_added` | 5 | Goals the chain may grow by when `chain.max_goals` is unset. |
| `chain.freeze` | false | When true (or `run --no-grow`), nothing joins by itself: each new prerequisite is asked on a delta sheet. A fourth such pause (max_reasks, default 3) ends the run stuck. |
| `chain.parallel` | unset (width 1) | Whole number of 2 or more: members one run cycle may claim at once. Capped by `parallel.goals.max_concurrent` (default 3) and honoured only with `parallel.goals.enabled` true and `work.enabled` on; otherwise the width is 1. A value below 2 is read as unset. When `chain.max_usd` is set and the remaining budget does not cover the goals in flight at the p90 cost per goal, the run falls back to width 1. |
| `chain.max_active` | 1 | Most chains that may run at the same time. Setting it, or `chain.parallel`, turns the active-chain lock on. |
| `chain.max_usd` | unset | Dollar cap on spend attributed to members since approval. |
| `chain.max_minutes` | unset | Minutes cap measured from the approval stamp. |
| `chain.auto_longest` | reserved | Documented only: nothing reads it yet. |

## The scratch scenario gesture

The offline scenario test drives the engine only through these argv, in this order (`run` is called again
until it reports done, and `status` closes it). The test compares what it ran with this block.

```
chain.py plan <sdlc_dir> 1
chain.py sheet <sdlc_dir> 1
chain.py answer <sdlc_dir> 1 --defaults
chain.py answer <sdlc_dir> 1 --answers <file>
chain.py approve <sdlc_dir> 1
chain.py arm <sdlc_dir> 1
chain.py run <sdlc_dir> 1
chain.py status <sdlc_dir> 1
```

## Measured on the scratch scenario

The scenario is a chain of four behind end goal 1 (waits on 2, 2 on 3, 3 on 4): goal 4 parked, goal 2
unarmed, goal 3 in another unit, a fifth goal filed mid-run, and an armed goal outside the chain. It runs on a
stateful fake of the host, with no network.

- GitHub calls for `plan`: 7 (counted from the fake's call log).
- Time to the sheet: measured as wall time on the fake, which is not meaningful against a live host.
  It is not measured live.
- Estimate versus actual spend: not measured (the scratch ledger holds no priced goals).
- Not measured: a run on a live repository or a private clone.

Each of four guards was broken alone and seen to fail the scenario: the pick without the label scope (an outside
armed goal is claimed), the done clause without its open-member checks (an empty pick reads as done), the cap with
an unreadable spend ledger read as empty (the run goes on), and the approval gate removed (a write before
approval).

## The active-chain lock

When `chain.parallel` or `chain.max_active` is set, each run cycle records its chain in
`.sdlc/state/chain-active.json` (pid, heartbeat, claimed pool) under a `flock` on a sidecar file. A chain whose
pool overlaps another active chain's, or that would exceed `chain.max_active`, is refused with the reason.

An entry is reclaimed only when its pid is dead AND its heartbeat is older than 900 seconds (a default the owner
is to ratify). Either clause alone is not enough: a dead pid with a fresh heartbeat may be a driver between
cycles, and a live pid with an old heartbeat is a hung driver a person should look at. So a driver whose cycles
are further apart than 900 seconds can be reclaimed by another chain while it is still meant to be running.

Manual recovery: `chain.py stop` on the chain releases its entry; a chain that ended in any non-running result, stuck included, has already released it, so only a crashed or killed driver leaves an entry (reclaimed as above, or released by `chain.py stop`). A lock file that cannot
be read is named in the refusal; once no chain is running, delete that one file and run again. Without
`fcntl` the lock refuses loudly rather than run unlocked.

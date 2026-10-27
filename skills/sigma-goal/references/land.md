# `/sigma-goal <N> --land` — one named goal, unattended, to a merged PR

The detail behind step 1's `--land` line in [`../SKILL.md`](../SKILL.md). Built for one Claude Code
cloud container per issue, each started by a person who picked that issue; it runs the same on a
laptop. It is `/sigma-loop` for exactly one goal: the loop's own picker, scoped to N, then the
loop's own per-goal steps, with nothing copied out of them — so the two cannot drift apart. It lives
here, not in SKILL.md or the loop's `picking.md`, because those load into every phase that runs and
their size is budgeted (`evals/phase_context_budget.py`, #2075); only a `--land` run reads this.

## Contract

This replaces SKILL.md's steps 1 to 5: no `loop.py claim`, no approvals, no gate pauses.

> **While a `--land` run is active, no goal other than the one it names may be picked, and no other goal is written to.**

Three exceptions, named rather than hidden: with `ledger.enabled`, the loop's verbs keep the ledger
watcher alive, a separate process whose own sweeps are not this run's; an issue the run files
through `handoff.py` (a hand-off or a tracked follow-up) is a NEW issue, never an existing goal; and
with `backlog_check.enabled`, the pre-check that finds N blocked resolves N's blockers, which writes
to those blocker issues. The picker flag that enforces the sentence is `loop.py next --land`; 
`skills/sigma-loop/references/picking.md` points here. Quote the sentence, never reword it.

## Prerequisites

Each is refused by the picker before any query (exit 2, one stderr line), which is the ending
**failed (not started)**:

- A GitHub backlog (`discovery.source: github`) and an issue number. A local goal file, a bare
  `--land` or a value that is not a number is refused.
- `work.enabled: true` — without it no pull request is opened.
- `work.auto_merge` set to `protected` or `always`. Under `off` the loop leaves every PR for a person,
  so the run could never end merged. `protected` against a base with no required checks or reviews
  always ends **parked, awaiting merge**; use `always` there.
- `--land` on its own: never beside `--feature` or `--label`, and never on `next-batch`.

`work.require_review` is unchanged: whatever the project sets binds this run exactly as it binds
`/sigma-loop`.

## The run

```
g=$(python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/loop.py" start .sdlc --session-pid "$PPID")
python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/loop.py" next .sdlc --land <N> --session-pid "$PPID"
# ...steps 3 to 6 of /sigma-loop for N...
python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/loop.py" session-end .sdlc --session-pid "$PPID" --session-generation "$g"
```

1. **Start** as `/sigma-loop` starts. A nonzero exit is **failed (not started)**.
2. **Pick** with the scoped `next`. It serves N only when an unscoped `/sigma-loop` pick would serve
   it — open, labelled `sdlc:goal`, not parked, blocked, in progress or awaiting merge, no open
   `**Blocked by:**` prerequisite, no live claim or claim lock held by another run, and, with a
   board, a card in the Ready lane or no card — then claims it and prints `<N>`. The model-tier line
   on stderr is the loop's; use it as the loop does. `HOLD` is not an ending: run
   `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/resource_lease.py" wait --max 540`, then pick
   again. Nor is a first `DEGRADED <epoch>` (N's read failed or was rate-limited): run
   `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/loop.py" wait-degraded <epoch>` once, then pick
   again. Anything else is an ending (below). The pick reads N with one REST call, then applies the
   same per-issue filter, dependency hold, lease, claim lock, budget, attach, scope and owner checks
   as an unscoped pick. It runs none of the sweeps that write to other goals (the map refresh,
   reconcile, auto-unpark, stale-claim reclaim, the release of unstarted holds, merge-reconcile);
   the two that clear a held goal's own overlay run for N alone. When N is not taken, stdout is
   `DONE` and stderr ends with `loop.py: --land #<N> not taken (not started): <why>`. It refuses
   before any query, exit 2: `--feature` or `--label` beside it, `next-batch`, a local-goals
   backlog, `work.enabled` off, and `work.auto_merge: off`.
3. **Run steps 3 to 6 of [`/sigma-loop`](../../sigma-loop/SKILL.md) for N**, exactly as a goal slot
   does, and the references they name: [running](../../sigma-loop/references/running.md),
   [progress](../../sigma-loop/references/progress.md),
   [filing](../../sigma-loop/references/filing.md) and
   [landing](../../sigma-loop/references/landing.md) — the pre-checks, recall, the tier, one
   subagent per phase, the worktree, Research to Review, Retrospective, then verify, commit, PR,
   post-PR review, merge and `record`. Follow them as written; nothing here restates them, with one
   change: where step 3 says to loop back to step 1 and take the next goal (a pre-check printed
   `PARKED <reason>` or `BLOCKED <reason>`), this run never loops back to step 1 — it ends there,
   **parked**. It never takes
   step 7 (no refill), never calls `next-batch`, and never calls `loop.py claim`.
4. **End the session** with the `session-end` above, on every ending.
5. **Say which ending**, as below.

**No gate pauses.** Nothing waits for a person between phases. Plan-Review, `sigma-review` and
`sigma-verify` still run on every goal, at every lane, and any of them can stop the run. A question
the interactive run would put to you is settled the way `/sigma-loop` step 3 settles it: decide when
the answer is clear; park with the question on the issue when it is genuinely ambiguous, a decision
only a person can make, or an irreversible or expensive action (`gates.irreversible_actions`, the
hard stops) that `loop.py spend-approval` has not approved; record a failure you cannot resolve as
failed.

The post-PR reviewer gives its verdict only through the landing path's post-review gesture, which
posts an issue comment. It never leaves a PR line comment: without GraphQL, thread resolution is
unreadable, so any line comment refuses the merge.

## Endings — the final message names exactly one

Keyed on the last `record` verb. Which `work.py merge` line leads to which verb is
[landing](../../sigma-loop/references/landing.md)'s routing, followed as it stands.

| Last step | Ending |
|---|---|
| `record done` succeeded | **merged** |
| `record parked "<reason>" --qkind <kind>` | **parked** — `record` posts the reason on the issue |
| a step-3 pre-check (`precheck`, `decompose-check`, `design-check`) printed `PARKED <reason>` or `BLOCKED <reason>` | **parked** — the pre-check has already posted the reason on the issue |
| step 3a stopped: `agent-start` exited nonzero, or `work.py start` exited 4 | **failed (not started)** for `agent-start` (another live agent may own N); for exit 4, **parked** when it printed a conflict it parked, else **failed (not started)** — it has already released or parked N, so never `record` on top of it |
| `record review` | **parked, awaiting merge (not `sdlc:parked`)** — `record review` posts why on the issue; see below |
| `record failed "<reason>"` | **failed** — the reason is on the issue and in the final message |
| the pick printed `loop.py: --land #<N> not taken (not started): <why>`, `BUDGET`, `HANDOFF` or a second `DEGRADED`, or refused with exit 2 | **failed (not started)** — that line is the reason. Nothing was claimed. A refusal at attach, scope or owner may have left a hold and a comment on N, which say what a person must do; a re-run after they act takes N |

Not endings: `HOLD` and a first `DEGRADED` (wait, then pick again), `REPAIR:` and `RERUN:` (the bounded CI-fix cycle), and
a `record done` that is REFUSED — do what the refusal names; the ending is whichever `record` verb
finally lands.

The final message carries the ending in bold, the goal ref, the PR link when there is one, and the
reason verbatim — the park or fail reason, the `not taken` line, or the `work.py merge` line. Quote
it; never paraphrase it.

### Awaiting merge when the container is gone

The merge-reconcile pass that records `done` once a waiting PR merges reads only this checkout's
work records, so once the container is gone no machine finishes N. What happens instead depends on
the PR body `work.py pr` wrote:

- `Closes #N`: GitHub closes N when the PR merges into the default branch. `sdlc:in-progress` and
  the ledger's open claim are left for a person to clear.
- `Refs #N` (a `feature/` base, or a default-branch read that failed when the PR opened): nothing
  closes N. A person closes it after merging the PR.

UNMEASURED live.

### A run that died before its ending

A crashed or killed run leaves N carrying `sdlc:in-progress`, perhaps with a pushed branch or an open
PR. A re-run then reports `not taken (not started): it carries sdlc:in-progress`, because `--land`
never reclaims a claim — reclaiming is a sweep, and this run writes to N only through its own pick.
The lever is a person's, and it depends on the PR:

- **The PR merged** (or the person merges it now): the work has landed. Close N if GitHub did not
  (a `Refs #N` body never closes it). Do not re-run `--land`: it would redo merged work.
- **The PR was closed, or there is none:** run
  `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/loop.py" release .sdlc <N> "<why>"`, which
  removes the claim with an audit comment, then start `--land` again.

An unscoped `/sigma-loop` pick reclaims a claim whose lease is older than `ledger.lease.ttl_hours`
only where `ledger.enabled` is on and that checkout's ledger holds the dead run's claim, which a
container's ledger is not guaranteed to reach. UNMEASURED live.

## In a Claude Code cloud session

GitHub GraphQL is unavailable there (`CLAUDE_CODE_REMOTE=true`, or `SIGMA_GH_GRAPHQL=off`). The pick,
the claim and the landing path all go over REST — see
[cloud sessions](../../../docs/cloud-sessions.md). What still parks there and nowhere else: a required
check still pending after the merge gate's 450 s wait (no `--auto` without GraphQL), and any PR line
comment. `require_review: approval` is satisfied by the trusted `sigma:approve` comment the post-PR
review posts.

## What is measured

Fakes only (`tests/test_goal_land.py`): the scoped pick, its refusals and its sweeps; the landing
gestures from PR open to `record done` on one stateful fake GitHub with every GraphQL-shaped call
refused. No real cloud container has run `--land` end to end.

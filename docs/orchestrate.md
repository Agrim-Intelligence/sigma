# Running a batch of goals: `/sigma-orchestrate`

`/sigma-orchestrate` runs a batch of goals as separate `/sigma-goal <N> --land` sessions, in dependency
waves, and merges what the post-PR review approves. It does not write product code: every goal is built,
reviewed and landed by its own session. It decides what runs when, starts the sessions, watches them through
GitHub, merges, unblocks dependents and reports.

```
/sigma-orchestrate [--max-seats N] [--name BATCH] [--where cloud|local] [--model auto|sonnet|opus]
                   [--max-opus N] [--no-merge] [--dry-run] [--yes] <issue numbers | GitHub search | description>
```

`--dry-run` stops at the go-ahead screen and writes nothing to GitHub or the repository. The skill is
[`skills/sigma-orchestrate/SKILL.md`](../skills/sigma-orchestrate/SKILL.md); the pieces it reads on demand are its
`references/launch.md` (launchers, the launch prompt, pace, model), `references/run.md` (each check-in, merging,
unblocking, stuck sessions, the tracker, the final report) and `references/follow-ups.md` (below).

## How it differs from its neighbours

| Skill | What it does | Sessions |
|---|---|---|
| `/sigma-triage` | plans a dependency-sequenced drain and enacts it as loop primitives | one `/sigma-loop`, capped at `parallel.goals.max_concurrent` |
| `/sigma-chain <N>` | solves one goal and every open prerequisite behind it | one chain run, `--parallel` inside it |
| `/sigma-orchestrate` | runs a batch of unrelated goals as separate `--land` sessions and merges for them | one per goal |

## The steps

1. **Preflight.** Sigma project only. `--land` prerequisites: `discovery.source: github`, `work.enabled`,
   `work.auto_merge` set to `protected` or `always` ([land](../skills/sigma-goal/references/land.md)). The GitHub
   account the sessions run as must be the goals' assignee. A session launcher must be available, or the person
   starts the sessions by hand (below). The readiness checks of [cloud burst](../skills/sigma-goal/references/cloud-burst.md)
   are reported as warnings.
2. **Resolve and acknowledge.** Numbers as given, a GitHub search, or a description translated into a search or a
   dependency chain. Epics and `needs-decomposition` issues become their open sub-issues. Each number is classed
   in scope, closed, held by another run, parked, later iteration, a person's gate or not ready, and every
   exclusion is told to the person with its reason.
3. **Facts per goal (read only).** Priority, base (`feature/<unit>`, else `work.base`), blockers inside and
   outside the batch (`deps.py report`), file and contract claims plus the single-writer files the goal will
   change (the area's directories if none are named, else `*`, which runs alone), and model signals.
4. **Waves.** `skills/sigma-orchestrate/scripts/waves.py plan` groups goals into waves. A wave is a set of goals
   that can run at the same time: every in-batch blocker sits in an earlier wave and no two share a claim. Rank is
   priority, then how many in-batch goals wait on it, then issue number. A goal with an outside blocker, a cycle
   or a hold of its own is held, and so is every goal that waits on one. Width comes from the dependency
   structure: with no `--max-seats`, every ready goal runs at once.
5. **One go-ahead.** Waves, held and needs-you items, seats, where, model mix, merging on or off, a cost line from
   `goal_cost.py report`, and the follow-up rule with the project rules it replaces. `--dry-run` ends here.
6. **Start.** The batch label `orchestrate:<batch>` and the confirmed prose dependencies (`deps.py mark`). Each goal is
   promoted with `promote.py apply` right before its own launch, so other loops cannot take it early.
7. **Run, rolling.** `waves.py ready` lists what may start now: every blocker has merged into the goal's own base
   (ancestry, not only a closed issue), a seat is free, and its claims are free. A higher-ranked ready goal
   reserves its claims, so lower-ranked goals cannot jump it, and a PR awaiting merge holds its claims but no
   seat. Launches go out in groups of 10, pause on any rate-limit report, and each goal is launched at most twice.
8. **Report.** Merged, needs a person (with the exact action), held, clean-up goals and how they ended, leaked
   follow-ups, and Opus use.

## The planner

`waves.py` is Python standard library only, makes no network call and writes nothing. The orchestrating agent
gathers the facts into `goals.json` (and, for `ready`, `state.json`); the script orders them, so two check-ins over
the same facts never disagree.

```
python3 skills/sigma-orchestrate/scripts/waves.py plan  <goals.json> [--md]
python3 skills/sigma-orchestrate/scripts/waves.py ready <goals.json> <state.json> [--seats N]
```

Both schemas and every rule are at the top of the script. A claim is a path (covering everything under it), a
glob (cut back to the directory before its first wildcard) or an exact token such as `contract:<name>`; `*` means
nothing is known, and clashes with everything. Tests: `tests/test_orchestrate_waves.py`, each clause of the
grouping condition broken on its own and seen red (a table is at the bottom of that file).

## Follow-ups stay in their goal

Every launch prompt carries one standing decision (verbatim in `references/follow-ups.md`): no new issue for a
follow-up. Found before the goal's first green full verify, it joins the goal's plan as a slice and the phases
re-run from plan-review, so one verify covers everything (at most 2 slices per goal). Found later, or past the
cap, it is written onto the goal as one marked deferred-items comment. When a wave ends, the orchestrator groups
the merged goals' deferred items by unit and area and files one clean-up goal per group through
`handoff.py track`. Clean-up goals never spawn more. A follow-up issue filed anyway is folded into its goal's
deferred items, or, if it blocks its parent, run first as a prerequisite.

For goals in the batch this replaces the project's own rule for filing follow-up issues; the go-ahead screen
names the rules it replaces.

## Hosts

Nothing is load-bearing on one host or on a host-only trigger. A launcher is any way to start a fresh session with the launch
prompt: a host's session-start tool, a remote-session tool, a background command-line agent where the person
allows one, or the person, from a printed list (a full mode: the next `/sigma-orchestrate` with the same `--name`
reads GitHub and continues). Check-ins use whatever wake tool the host has; with none, each run is one pass and
says so. Every GitHub read and write is REST (`gh api`), which works in cloud sessions.

## What is not here yet

- The follow-up decision still rides in the launch prompt. Cloud burst asks for a prompt that is only
  `/sigma-goal <N> --land` plus facts; moving the decision into a config key that `--land` runs and phase
  subagents read is separate work, as is making `sigma-review` and the landing repair path route follow-ups to the
  goal instead of `handoff.py track`.
- The post-PR review's `sigma:approve` comment does not name the head commit it approved, so the merge check
  binds an approval to a head by comparing times. A comment that names the head is separate work.
- The deferred-items comment format and the clean-up filing are written as instructions to the agent, not parsed
  or enforced by a script.
- No real batch has been run end to end with this skill. The planner is tested offline; the launchers, the merge
  checks and the wake loop have not been exercised on a live repository.

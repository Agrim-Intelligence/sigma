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
   in scope, closed, held by another run (a label, a claim, or an open pull request that links the issue on any
   branch), an upper tier or not a goal, parked, later iteration, a person's gate or not ready, and every
   exclusion is told to the person with its reason. `promote.py apply --dry-run` is the classification probe that
   catches a story or another non-goal before the go-ahead. Agent-ready means: carries or can carry `sdlc:goal`, not
   an upper tier, and its body says how to tell it is done.
   Every script call spells the sdlc directory as a path, `"$S"` (`dag.py` takes `--sdlc "$S"`); from a worktree that
   is the main checkout's `.sdlc`, because `.sdlc/*` is git-ignored and a worktree has no config.
3. **Facts per goal (read only).** Priority, base (`feature/<unit>`, else `work.base`), blockers inside and
   outside the batch (`deps.py report`), file and contract claims plus the hot shared files the goal will
   change (a project's single-writer list, or a registry, lockfile, changelog or shared schema the goal names; the
   area's directories if none are named; with no CODEOWNERS area mapping, the goal's own file mentions; else `*`,
   which runs alone, and the go-ahead screen says which goals fell back), and model signals.
4. **Waves.** `skills/sigma-orchestrate/scripts/waves.py plan` groups goals into waves. A wave is a set of goals
   that can run at the same time: every in-batch blocker sits in an earlier wave and no two share a claim. Rank is
   priority, then how many in-batch goals wait on it, then issue number. A goal with an outside blocker, a cycle
   or a hold of its own (a reason, or `open_prs` in the goal's row) is held, and so is every goal that waits on one. Width comes from the dependency
   structure: with no `--max-seats`, every ready goal runs at once.
5. **One go-ahead.** Waves, held and needs-you items, seats, where, model mix, merging on or off, a cost line from
   `goal_cost.py report` (or "no data for these goals"), and the follow-up rule with the project rules it replaces. `--dry-run` ends here.
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

The decision is a project setting, `followups.mode` in `.sdlc/config.json` ([configuration](configuration.md)): `file`
(the default, today's behaviour) or `in-goal`. `handoff.py followup-mode .sdlc` prints it. With `in-goal`, the
project has decided for every run: `handoff.py track` refuses a non-blocking follow-up (exit 3, nothing filed; a
blocker, `--blocks yes`, and work marked `--kind prerequisite|cleanup|child` are still filed), `sigma-review` and the
landing path route follow-ups the same way, and the launch prompt says nothing about follow-ups. With `file`, every
launch prompt carries one standing decision (verbatim in `references/follow-ups.md`) for the goals of this batch only:
no new issue for a follow-up. Found before the goal's first green full verify, it joins the goal's plan as a slice and the phases
re-run from plan-review, so one verify covers everything (at most 2 slices per goal). Found later, or past the
cap, it is written onto the goal as one marked deferred-items comment, which `scripts/deferred.py` parses (below).
When a wave ends, the orchestrator groups the merged goals' deferred items by unit and area and files one clean-up
goal per group through `handoff.py track --kind cleanup`. Clean-up goals never spawn more. A follow-up issue filed anyway is folded into its goal's
deferred items, or, if it blocks its parent, run first as a prerequisite.

Under `file`, for goals in the batch this replaces the project's own rule for filing follow-up issues; the go-ahead
screen names the rules it replaces.

### The deferred-items parser

```
python3 skills/sigma-orchestrate/scripts/deferred.py parse <comments.json>   # comments as the REST API returns them
python3 skills/sigma-orchestrate/scripts/deferred.py check <body-file>       # a draft comment, before posting
```

Standard library only, no network, no writes. A comment counts only from an `OWNER`, `MEMBER` or `COLLABORATOR` (on a
public repository anyone can paste the marker); the marker `<!-- sigma-orchestrate:deferred -->` must stand on a line
that is not quoted or fenced; bullets before it are not items; plain prose after it is ignored; and a bullet after it
that does not read `- [area:<area>] <what> - <why> (files: <paths>)`, including a list-shaped line that does not start
`- ` or `* ` (a numbered item, `+ [area:...]`, `-[area:...]`), is listed under `rejected` with its reason, never dropped. `what` ends
at the first spaced hyphen. An absolute path, a `..` segment, a backslash or a control character rejects the item;
identical items collapse; line, field, file-count and item-count bounds are at the top of the script. `parse` exits 0
with rejections listed, `check` exits 0 only for a clean, non-empty draft, and 2 is bad input. Tests:
`tests/test_orchestrate_deferred.py`, each clause broken on its own and seen red (a note at the bottom of that file).

## Approve names its head

The merge check reads the post-PR review's `sigma:approve` comment. It may name the commit it approved:
`sigma:approve <commit id>` (a full id, or a prefix of at least 7 hex digits). A named id counts only while it is the
PR's current head; an unnamed marker keeps comparing times, as before. Sigma's own `post-review` writes the head it
reviewed, and the gate in `work.py` applies the same rule and fails closed on a wrong, malformed or ambiguous id or an
unreadable head ([the rule](worktrees-and-merging.md)). Tests: `tests/test_approve_names_head.py`.

## Hosts

Nothing is load-bearing on one host or on a host-only trigger. A launcher is any way to start a fresh session with the launch
prompt: a host's session-start tool, a remote-session tool, a background command-line agent where the person
allows one, or the person, from a printed list (a full mode: the next `/sigma-orchestrate` with the same `--name`
reads GitHub and continues). Check-ins use whatever wake tool the host has; with none, each run is one pass and
says so. Every GitHub read and write is REST (`gh api`), which works in cloud sessions.

## What is not here yet

- The validation of 13 Nov ran steps 1 to 5 for real, read-only, which found the defects fixed under #2157; the
  re-run is in that issue's pull request. Launch, merge checks, the wake loop, the follow-up filing path,
  cloud-burst checks and a multi-wave plan from real data are still not exercised, and the open-pull-request
  lookup is described in prose, not scripted.
- No real batch has been run end to end with this skill. The planner, the deferred-items parser, the follow-up setting
  and the head-naming approve are tested offline; the launchers, the merge checks and the wake loop have not been
  exercised on a live repository, and neither has the setting against a real phase subagent's behaviour.
- An approve typed by a person with no id still falls back to a time comparison, which has the known gap in
  `references/run.md`.

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
   `goal_cost.py report` (or "no data for these goals"), and the follow-up rule. `--dry-run` ends here.
6. **Start.** The batch label `orchestrate:<batch>` and the confirmed prose dependencies (`deps.py mark`). Each goal is
   promoted with `promote.py apply` right before its own launch, so other loops cannot take it early.
7. **Run, rolling.** `waves.py ready` lists what may start now: every blocker has merged into the goal's own base
   (ancestry, not only a closed issue), a seat is free, and its claims are free. A higher-ranked ready goal
   reserves its claims, so lower-ranked goals cannot jump it, and a PR awaiting merge holds its claims but no
   seat. Launches go out in groups of 10, pause on any rate-limit report, and each goal is launched at most twice.
8. **Report.** Merged, needs a person (with the exact action), held, and Opus use.

## The planner

`waves.py` is Python standard library only, makes no network call and writes nothing. The orchestrating agent
gathers the facts into `goals.json` (and, for `ready`, `state.json`); the script orders them, so two check-ins over
the same facts never disagree. A goal row's `open_prs` must be a list of positive integers (absent, `null` or `[]` mean
none); a string, a number, a non-integer entry or a boolean is refused with a one-line message and exit 2.

```
python3 skills/sigma-orchestrate/scripts/waves.py plan  <goals.json> [--md]
python3 skills/sigma-orchestrate/scripts/waves.py ready <goals.json> <state.json> [--seats N]
```

Both schemas and every rule are at the top of the script. A claim is a path (covering everything under it), a
glob (cut back to the directory before its first wildcard) or an exact token such as `contract:<name>`; `*` means
nothing is known, and clashes with everything. Tests: `tests/test_orchestrate_waves.py`, each clause of the
grouping condition broken on its own and seen red (a table is at the bottom of that file).

## Follow-ups stay in their goal

The decision is a project setting, `followups.mode` in `.sdlc/config.json` ([configuration](configuration.md)): `in-goal`
(the default) or `file`. `python3 skills/sigma-loop/scripts/handoff.py followup-mode "$S"` prints it. Under `in-goal`,
`handoff.py track` routes every filing through one function (`followup_route.route`): a find in the goal's own feature
joins its plan as a slice (`work.py add-slice`, at most `followups.max_added_slices` per goal, default 10), and is filed
as a separate goal only for another feature or past the cap, with the reason recorded; a same-feature filing is refused,
exit 3. Nothing about follow-ups goes in a launch prompt, and the orchestrator collects and files nothing for follow-ups.
`in-goal` is the default; `file` is an explicit opt-out for humans that turns routing off for every goal. A decompose child
with its four recorded legs is exempt from routing; chain prerequisites stay routed.

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
- No real batch has been run end to end with this skill. The planner, the follow-up setting
  and the head-naming approve are tested offline; the launchers, the merge checks and the wake loop have not been
  exercised on a live repository, and neither has the setting against a real phase subagent's behaviour.
- An approve typed by a person with no id still falls back to a time comparison, which has the known gap in
  `references/run.md`.

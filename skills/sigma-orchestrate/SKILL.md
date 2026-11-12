---
name: sigma-orchestrate
description: Run a batch of goals as parallel `--land` sessions in dependency waves, merging and reporting. Use for 'run these goals in waves', a list or search of goals to drain in parallel, or /sigma-orchestrate.
argument-hint: "[--max-seats N] [--name BATCH] [--where cloud|local] [--model auto|sonnet|opus] [--max-opus N] [--no-merge] [--dry-run] [--yes] <issue numbers | GitHub search | description>"
allowed-tools: Bash(python3 *), Bash(gh api *), Bash(git *), Read, Grep
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-orchestrate

You are the orchestrator for one batch of goals. You do not write product code: every goal is built,
reviewed and landed by its own `/sigma-goal <N> --land` session. You decide what runs when, launch the
sessions, watch them through GitHub, merge approved PRs, unblock what they free, and report.

Detailed selection triggers: [selection](references/selection.md). Reference page: [orchestrate](../../docs/orchestrate.md).

It drives only the kit's own scripts and never edits the kit. How it differs from its neighbours:
`/sigma-triage` plans a drain for one `/sigma-loop`; `/sigma-chain` solves one goal plus its prerequisites in
one run; this skill starts and supervises one `--land` session per goal, across a batch of unrelated goals.

## Arguments

| Argument | Meaning | Default |
|---|---|---|
| `--max-seats N` (also `--max-seats=N`) | most goal sessions running at once | no limit: every goal that is ready runs at once |
| `--name BATCH` | batch name; the label is `orchestrate:BATCH` | `orch-<yyyymmdd>-<hhmm>` |
| `--where cloud\|local` | cloud sessions, or local worktree sessions on this machine | cloud when this host can start one, else local |
| `--model auto\|sonnet\|opus` | model for goal sessions, where the launcher lets you choose | `auto` ([launch § model](references/launch.md#model)) |
| `--max-opus N` | most Opus sessions at once | no limit |
| `--no-merge` | leave approved PRs for a person | merge them |
| `--dry-run` | stop at the go-ahead screen; nothing written to GitHub or the repository | off |
| `--yes` | skip the go-ahead question (unattended use) | ask |
| the rest | the goals: `#12 34 56`, a GitHub search (`is:open label:priority:P1 label:feature:x`), or a description ("everything blocking #N", "the open children of epic #N", "P1 goals in unit X") | required |

`--where local` with no `--max-seats` uses the project's `parallel.goals.max_concurrent` (else 3): one
machine's memory and disk are the limit there, not GitHub. Say so on the go-ahead screen.

## Ground rules, every step

- **The project's rules win, with one exception.** Read its `CLAUDE.md` (and what it points to),
  `.sdlc/config.json`, and the person's notes for this project before step 2. Areas, iteration rules, model
  policy, merge method, branch rules, single-writer files and "never" lists bind you exactly as they bind a
  goal session. The exception is follow-ups, which depend on the project's `followups.mode`
  (`handoff.py followup-mode .sdlc`, [follow-ups](references/follow-ups.md)): `in-goal` means the project
  already decided it, so nothing is replaced; `file` (the default) means that for goals in this batch the
  person's standing decision replaces the project's own rule for filing follow-up issues. Name the project
  rules it replaces on the go-ahead screen.
- **Only this batch.** Never steer, message, merge, promote, unpark or comment on a goal or session outside
  this batch, except to read it.
- **Never**: force-push or rewrite history; edit `.sdlc/config.json`, gates, workflows or rulesets; add
  `sdlc:goal` by hand (promotion is `promote.py`, unparking is `unpark.py`); run `loop.py note`, `verify`
  or `agent-start` on a goal (each one claims it; your own comments go through `gh api`); push to a branch
  the project forbids; put a client string or a secret anywhere.
- **GitHub is the record.** Cloud sessions never report back. Read every goal's state from GitHub (labels,
  PRs, comments, commits) and the tracker, never only from session messages.
- **REST for every GitHub read and write** (`gh api ...`): it works on every host, including cloud sessions,
  where GraphQL is unavailable, so the issue and pull-request list and view subcommands of `gh` do not work.
- **Writes wait for the go-ahead.** Steps 1-5 only read; their working files go in a scratch directory.
  `--dry-run` stops at step 5.
- **Talk to the person the way their instructions say** (plain words, outcome first). The tables below are
  for your tracker; the person gets the short version.

Paths used below (`<o>/<r>` is the repository's owner/name; every kit script is spelled in full with
`${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/`, this skill's own with `${CLAUDE_SKILL_DIR}/scripts/`):

- `T` is the tracker directory from step 6 on: `.sdlc/state/orchestrate/<batch>` holding `goals.json`,
  `state.json`, `tracker.md` and `launches.jsonl`.

Before step 6, `goals.json` lives in a scratch directory (the session's scratchpad, else `mktemp -d`).

## 1. Preflight (read only)

Stop with one plain sentence when any of these fails:

1. A git repository with a GitHub remote, and `.sdlc/config.json` present. No Sigma, no run: "this works in
   Sigma Loop projects only".
2. The kit's scripts resolve: `${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/loop.py` exists. A plugin copy that
   lacks it is too old or broken: say so and point at `/sigma-doctor`.
3. `/sigma-goal --land` can end merged here: `discovery.source` is `github`, `work.enabled` is true,
   `work.auto_merge` is `protected` or `always` ([sigma-goal/land.md](../sigma-goal/references/land.md) § Prerequisites).
   A miss fails every session the same way, so it stops the batch.
4. The GitHub account the sessions will run as (`gh api user --jq .login`) is the goals' assignee /
   `discovery.github.assignee`. A goal assigned to someone else is refused at pick ("not taken").
5. A session launcher exists for `--where` ([launch § launchers](references/launch.md#launchers)); with none, the
   person starts the sessions by hand from a printed list, which is a supported mode.

Report, without stopping (they become go-ahead warnings): the four readiness checks in
[sigma-goal/cloud-burst.md](../sigma-goal/references/cloud-burst.md) (dependency bot while CI is paused, services the suite
needs, ambient tokens, base branch green — `/sigma-doctor` shows the last recorded verify; it is not a
run of the base), and for `--where local` the project's worktree bootstrap from its docs.

**Resume first.** Before naming a new batch, look for `.sdlc/state/orchestrate/*/goals.json` and for
`orchestrate:*` labels on the requested goals. A batch that overlaps the request and still has unfinished
goals is resumed, not restarted (say so): rebuild from its tracker, the issues carrying its label, the open
PRs on its goal branches and the session list, then go to step 7. Never relabel or relaunch a goal another
open batch holds.

## 2. Resolve and acknowledge (read only)

Turn the input into issue numbers:

- Numbers: as given.
- A GitHub search: `gh api -X GET search/issues -f q="repo:<o>/<r> is:issue is:open <query>" -f per_page=100 --paginate --jq '.items[].number'`
  (the search API stops at 1000 results; say so if it hits that).
- A description: translate it into a search or a computed set and show the translation — "everything
  blocking #N" is `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/dag.py" chain N --json --verify` (exit 2 goal not in the map, 3 map
  unusable, 4 map mode off: use `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/deps.py" report .sdlc --chain N`); "children of epic #N" is
  `gh api repos/<o>/<r>/issues/N/sub_issues --paginate`.

An epic, or an issue labelled `needs-decomposition`, is replaced by its open sub-issues (say so); one with
no sub-issues goes on the needs-you list ("needs breaking into goals first").

Classify every number (`gh api repos/<o>/<r>/issues/N` each):

| Class | When | Goes to |
|---|---|---|
| in scope | open, agent-ready by the project's definition, current iteration, ours to run | the batch |
| closed | closed | excluded |
| held by another run | `sdlc:in-progress`, or a live claim, that this batch did not create | excluded |
| parked or blocked | `sdlc:parked` or `sdlc:blocked` | needs-you with its reason — unless it waits only on a blocker inside the batch: then in scope, waiting until that blocker lands |
| later iteration | the project's iteration rule puts it later | excluded |
| person's gate | an approval or release check meant for people (project rules) | excluded |
| not ready | missing what the project's definition of agent-ready needs, or assigned to another account | needs-you, naming what is missing |

**Acknowledge** to the person: how many you were given, how many are in scope, and each exclusion with its
reason in one line.

## 3. Facts per goal (read only)

For each in-scope goal gather one `goals.json` row (schema at the top of `${CLAUDE_SKILL_DIR}/scripts/waves.py`):

- `priority` from its `priority:*` label; `base` = `feature/<unit>` from its `feature:<unit>` label, else
  `work.base`.
- Dependencies: `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/deps.py" report .sdlc <N>` lists every `#ref` in the title, body and
  comments and how Sigma reads it; it does not say whether a ref is open. Read each one: an open blocker
  inside the batch goes in `blockers`, one outside in `external_blockers`. A real prose dependency that is
  unmarked is noted for `deps.py mark` in step 6 (`mark` is a write).
- `claims`: the files, directories and contracts the goal will touch (its "may touch", scope, named paths),
  plus any single-writer file the project lists **that this goal will change**. Nothing named: use its
  `area:*` directories from CODEOWNERS. Nothing at all: `["*"]` — it runs alone.
- Model signals and needs-a-person signals ([launch § model](references/launch.md#model)).

For more than ~15 goals, hand batches of 10-15 numbers to read-only subagents that return exactly these
fields as JSON; check the claims yourself for each goal before you launch it.

## 4. Waves (read only)

`python3 "${CLAUDE_SKILL_DIR}/scripts/waves.py" plan goals.json` (and `--md` for the table). A wave is a set of goals that can run at
the same time: their batch blockers sit in earlier waves and no two share a claim. Held goals — outside
blocker, cycle, a reason you set — are listed with the reason, and so is every goal waiting on one.
`cross_base` lists dependencies between goals on different bases: the dependent cannot start until the
blocker's merge reaches its own base, which this skill does not do (landing or catching up a unit is
separate work). Record each goal's planned wave in `goals.json` as `"wave"`.

## 5. The go-ahead

Show the person, briefly:

- the batch name and label; the acknowledged count, in scope, excluded (one line each);
- the waves table, and one line: **"Waves are the plan; launches roll — a goal starts as soon as its
  blockers have merged into its base and a seat is free."**;
- held, cross-base and needs-you items, each with what would release it;
- seats (the limit, or "no limit"), where, models (how many on Opus and why — or "the cloud environment's
  model" where the launcher cannot choose), merging on or off, and a cost line from
  `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/goal_cost.py" report .sdlc` when it has data;
- the follow-up rule in one line — "follow-up work stays in its goal: found before the goal's first green
  full verify, it joins that goal; found later, it is grouped into one clean-up goal per unit and area at
  the end of each wave" — and which it is: the project's own setting (`followups.mode: in-goal`), or, with the
  default `file`, this batch's decision and the project rules it replaces;
- the readiness warnings from step 1.

`--dry-run`: stop here. Otherwise ask once and wait for a clear yes, unless `--yes`. A no ends the run with
nothing written.

## 6. Start (writes begin)

1. Create the label: `gh api repos/<o>/<r>/labels -f name="orchestrate:<batch>" -f color=5319e7 -f description="sigma-orchestrate batch <batch>"`
   (already there: reuse it) and add it to every in-scope goal, held ones included
   (`gh api repos/<o>/<r>/issues/N/labels -f "labels[]=orchestrate:<batch>"`).
2. Mark the real prose dependencies from step 3, while their blockers are still open:
   `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/deps.py" mark .sdlc <N> <ref> "<reason>"`. This is what keeps other loops on the
   same account from taking an already-labelled goal before its blocker lands. `mark` refuses a goal with no
   Sigma label and a reference that is not open; skip those (the batch's own ordering still holds them).
3. Move `goals.json` into `$T`; write `$T/state.json` (`{}`) and `$T/tracker.md`.
4. Go to step 7. Promotion happens per goal, right before its launch, so other loops on the same account
   cannot take an unlabelled goal early.

## 7. Run

Follow [run](references/run.md) every wake until step 9's stop condition. In short: refresh each goal's
state from GitHub; merge what is approved ([run § merge](references/run.md#merge)); unpark dependents that
were waiting only on a merged blocker; collect deferred follow-ups (`scripts/deferred.py parse`) and close finished waves
([follow-ups](references/follow-ups.md)); then `python3 "${CLAUDE_SKILL_DIR}/scripts/waves.py" ready "$T/goals.json" "$T/state.json"
[--seats N]` and launch what it lists ([launch](references/launch.md)); write the tracker; schedule the
next check-in.

## 8. Follow-ups stay in their goal

Under the project's `followups.mode: in-goal`, or through one standing decision in each launch prompt under the
default `file`: follow-up work never becomes a new goal. Found before the
goal's first green full verify, it is added to that goal's plan as a slice and re-reviewed; found after, it
is written onto the goal as a deferred item, and when a wave ends you group those items into one clean-up
goal per unit and area. Clean-up goals do not spawn more. Full text, formats and the safety net for runs
that file anyway: [follow-ups](references/follow-ups.md).

## 9. Stop and report

Stop when every batch goal is merged, stopped (parked, failed, needs-you), stuck or held, every ended wave's
deferred items are filed or reported, and nothing is running or awaiting merge. Cancel your pending
check-in, then give the final report ([run § final report](references/run.md#final-report)).

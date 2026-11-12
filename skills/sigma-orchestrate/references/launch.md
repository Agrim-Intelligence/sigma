# Launching goal sessions

## Launchers

Nothing here depends on one host or on a host-only trigger: a launcher is any way to start a fresh session that runs the
launch prompt below. Use the first one this host has, in this order, and say which on the go-ahead screen.
A launcher is a host capability, so what follows is what each host class offers, not something Sigma runs.

**1. A session-start tool of the host.** Read its own schema first. What every launcher needs: a title
`Goal #<N> land run (<batch>)`, the prompt below, a fresh context (no copy of this session's history), and a
background note of facts only (at least a sentence: "One goal of Sigma batch <batch> in <o>/<r>; it runs
unattended and ends merged, parked or failed."). The session reads it, so it carries no rules.
- Where it can start a **cloud** session (`--where cloud`): pass the project's prepared cloud environment (from
  the tool's target list, the project's docs or the person; it is what makes readiness check 2 hold) and
  start from a pushed branch that carries the project's Sigma config: the default branch, or this session's
  own branch only when it has no commits of its own. The `--land` run cuts its own goal branch from its
  base. If the tool takes no model, `--model`, `--max-opus` and Opus escalation have no effect, and the
  go-ahead screen says so. Cloud sessions started this way never report back.
- Where it can start a **local** session (`--where local`): ask for an isolated worktree and pass the model id
  where it is accepted. A model above this session's own may be ignored, so run the orchestrator on the
  strongest model any goal needs. Local sessions may report back when they end a turn; do not rely on it.

**2. A remote-session tool** (a cloud session that can create sibling sessions): read its schema first.
Typically it takes the cloud environment id, the repository URL as the source, `work.base` as the revision, the
title above, tags `["orchestrate", "<batch>"]` and the model if it accepts one. Not available for
`--where local`.

**3. A command-line agent in the background**, where the person's host runs one (for example a headless run of
the host's own CLI in a fresh worktree per goal, started with the launch prompt as its task). Use it only when
the person has said this host may start such processes: a background process that spends quota is an opt-in,
never a default. Never more than the seat limit at once.

**4. The person.** With no launcher at all, print one block per goal that is ready (title, the launch prompt in
a code fence, the environment facts) and stop the pass. The person starts each session wherever their host
allows and runs `/sigma-orchestrate` again with the same `--name` to continue; the next pass reads GitHub, sees
which goals started and goes on. This is a full, supported mode: waves, merging and follow-up handling are the
same, only the start is manual. Nothing in the skill is load-bearing on a Claude-only feature.

Nothing to start sessions with and the person declines to start them: stop - "this host cannot start sessions".

Record one row per launch in `$T/launches.jsonl`: goal, session id or name, launcher, model and why, time,
and which launch of this goal it is (1st, 2nd). A goal with a launch row and no recorded ending is
**running**, whatever its labels say ([run § refresh](run.md#refresh-state)).

## The prompt

> **The prompt is `/sigma-goal <N> --land`, facts about the environment, and, unless the project already
> carries it, the one standing decision below. Nothing else.**

Sigma's own burst guidance ([sigma-goal/cloud-burst.md](../../sigma-goal/references/cloud-burst.md)) found that every extra rule in a
launch prompt — a gate or config edit, an action on another PR, "stop on any refusal", a goal-specific
verify command — was refused or caused a needless park. The follow-up decision is the single deliberate
exception, and only while the project's own setting does not carry it: read
`python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/handoff.py" followup-mode .sdlc`. `in-goal` means the
project has made the decision itself (`followups.mode`, [configuration](../../../docs/configuration.md)): leave
the standing decision out of the prompt, and `handoff.py track` itself refuses a follow-up filing. `file` (the
default) means the decision rides in the prompt, for this batch only, as below.

```
/sigma-goal <N> --land

Facts about this environment, not instructions to change anything:
- <one line per fact the run cannot read for itself: which services the setup starts, which token is
  absent, how the project's verify behaves on this launcher, the worktree bootstrap for local sessions>

<the standing decision, verbatim from follow-ups.md § The standing decision; omitted when followup-mode says in-goal>
```

Facts come from the project's docs and the person's notes for this launcher. A line that tells the run what
to do, rather than what is true, is not a fact: leave it out.

## Just before a launch

For a goal that lacks `sdlc:goal`:

```bash
python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/promote.py" apply .sdlc <N> --dry-run   # read what it would do
python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/promote.py" apply .sdlc <N>
```

- It refuses a goal with an open blocker and skips a parked, epic or unit-held goal, printing why: do not
  launch; record that line as `stopped: <line>` and put it on the needs-you list.
- A rate-limited result is not a refusal: try again next wake.

Dependency marks are not written here: by launch time every blocker is closed, and `deps.py mark` only marks
an open reference (step 6 writes them). A goal parked for a reason that is now resolved is released with
`unpark.py` ([run § unblocking](run.md#unblocking)), never by editing labels.

## Pace

There is no seat limit unless `--max-seats` (or `--where local`) sets one. What is limited is how fast
sessions start, because every session shares one GitHub account and its rate limits:

- launch at most 10 at a time; launch the next group once the previous group's goals carry
  `sdlc:in-progress` and no session reported a rate-limit or auth error (check every ~5 minutes while a
  group is starting);
- any rate-limit report pauses new launches until it resets;
- `waves.py ready` already puts a ready P0 first.

A goal is launched at most twice in one batch: once, plus either one Opus escalation or one relaunch after
a dead run ([run § stuck sessions](run.md#stuck-sessions)).

## Model

- `--model sonnet` or `--model opus`: every goal on that model, where the launcher lets you choose.
- `--model auto`: follow the project's model policy for goal sessions (its rules, or the person's notes for
  this project). With none, every goal runs on Sonnet. Never Haiku for a whole goal: Sigma already picks
  cheaper tiers per phase inside the run.
- `--max-opus N`: Opus goals beyond N wait their turn while Sonnet goals take the seats.
- **Escalation**: a Sonnet run that parks or exhausts its review cycles because the work is beyond it
  (repeated `sigma:block` on correctness or design, a plan-review that cannot converge) may be relaunched
  once on Opus. Never for a mechanical cause. Record the evidence in `launches.jsonl`.

Use the newest model ids the host offers.

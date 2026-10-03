# Quality evals — catch drift on every change

## Benchmark harness: arms, isolation and the smoke control

The benchmark method is fixed in
[`docs/bench/preregistration.md`](../docs/bench/preregistration.md).  Three live arms exist, run in
this order for every task: **A1 `sigma`** (Sigma loaded from a clean `git archive` export of a pinned
commit, never the repository), **A2 `plain`** (the agent alone) and **A3 `matched`** (the plain agent
retried in fresh workdirs until the visible tests pass or spend reaches A1's spend for that task; the
first visible-passing attempt is scored, else the last).  There is no other arm.  A human launches the
harness; it refuses CI and background execution and nothing here has been run against a real model.

```bash
python3 evals/bench/bench.py run --manifest <tasks/manifest.json> \
  --hidden-root <external-hidden-bundles> --results <results.json> \
  --max-usd <ceiling> --isolation-launcher </absolute/operator-sandbox> \
  --arm all --model <pinned-model-id> --permission-mode <mode> --sigma-commit <sha>
```

Add `--dry-run` to validate everything and print each arm's *planned* isolation facts without starting
anything.  `--fake-arm` replaces `--arm` for the zero-spend smoke control (two local tasks, no `claude`).
`--max-usd`, `--model`, `--permission-mode` (identical for every arm) and, for the sigma arm,
`--sigma-commit` have no defaults.  `--scratch-root` must be empty.

What the harness enforces, and what it does not:

- **Isolated profile.** Every run, and every A3 attempt, gets a fresh `HOME`, `CLAUDE_CONFIG_DIR` and
  `CODEX_HOME`, and `TMPDIR` points inside that profile (an agent writing to a hard-coded `/tmp` path still shares that
  channel unless the launcher prevents it).  The
  rest of the harness environment is forwarded WHOLE, not allow-listed: an `ANTHROPIC_API_KEY` set by
  the operator reaches `claude`, and so does any other credential in that environment (cloud keys,
  `GH_TOKEN`), readable by the agent and by agent-authored test code at scoring; launch the benchmark
  from an environment holding only what it needs.  Only values containing the hidden root and the
  harness's own `SIGMA_BENCH_*` variables are dropped.
- **Operator plugins untouched.** The operator's `~/.claude/plugins` (and `$CLAUDE_CONFIG_DIR/plugins`)
  is content-hashed once before anything starts, again before every run and every A3 attempt, and after
  the last run.  Any change aborts the benchmark; do not run other Claude sessions that update plugins
  meanwhile.
- **Clean export.** The sigma arm loads `.claude-plugin`, `skills` and `hooks` of the pinned commit.  The
  export is refused if it holds links, escapes its directory, has other top-level entries, or has a path
  component named `evals` or `tests` (a future skill folder with that name fails A1 loudly).  It omits
  `docs/`, `tools/` and `contract/`; how A1 behaves without them is not measured.
- **Hidden tests.** The hidden root must be outside the repository, every task source and the scratch
  root, and the scratch root must be outside the repository.  Each run directory (worktree, profile, transcripts, scoring copy with the hidden bundle) is
  deleted right after scoring, so a later arm or attempt cannot read an earlier solution or the bundle.
  Scoring commands run through the launcher, bounded by `--scoring-timeout-seconds`.
- **Spend.** Each `claude -p` gets `--max-budget-usd` of the smaller of `--belt-usd` and what is left of
  `--max-usd`; a run that cannot be priced, or that overshoots, stops the benchmark.  Every stop keeps
  the rows already paid for in the results file with an `aborted` object (its `cost_usd_spent` is a lower bound: the killed in-flight run's own transcript is
  deleted unpriced), so a report with `aborted` is partial.  There is no resume: a re-run re-pays every arm.  The last A3 attempt can overshoot A1's spend
  by up to one attempt; A3 stops on `--max-attempts`, the remaining ceiling or the run deadline too (one deadline shared by all
  its attempts, where A1 and A2 each get a full one), and
  its row's reason names which.  Cost is read from the transcript under the profile, so it is exactly as
  honest as the launcher.
- **Commands are bounded.** Every launched command runs in its own process group with a timeout and the
  whole group is killed on expiry or normal exit (a launcher that starts its own session escapes this).
  SIGTERM and SIGHUP unwind the harness the same way (children killed, paid rows kept; a second signal
  during the unwind can cut it short); SIGKILL or a power loss cannot, and a surviving `claude` is then bounded only by its own `--max-budget-usd` belt.

The launcher is an **operator-owned external sandbox or privilege-separation boundary**: Sigma validates
its location and executable bit, but cannot infer that an arbitrary executable's bytes contain an agent,
so hidden-test confidentiality at scoring time (the bundle sits beside agent-authored code) and the
agent's reach into the real filesystem depend entirely on it.  Arbitrary in-process `Arm` subclasses are
refused; only the exact in-tree arm classes run.  The `claude` flags (`--plugin-dir`, `--session-id`,
`--max-budget-usd`, `--permission-mode`, prompt on stdin) are listed by this host's `claude --help` but
have not been exercised end to end, and the first live run will stop at the unpriced-run guard until the
operator supplies credentials.

Sigma's "output" is agent *behavior* (does it follow the spine, plan before editing, verify before
claiming done). Behavior is non-deterministic, so quality is guarded in tiers. Run the whole thing
on every change; a drop below the committed baseline fails the build.

## Tier 0 — structural gate over every `SKILL.md` (free, runs in CI)

The product *is* the skill prompts, and Tier 1 below scores the intent hook, not one word of them.
Tier 0 is the cheapest thing that can say anything about the prose itself: mechanically checkable
structure, no LLM, no network, same answer every run (`evals/skill_structure.py`, asserted by
`tests/test_skill_structure.py`, so it runs inside the ordinary `pytest tests/` gate).

```bash
python3 evals/skill_structure.py            # report + gate (exit 1 on a finding)
python3 evals/skill_structure.py --table    # the measurement table only, never fails
```

## Phase context budget — instruction bill by agent

`phase_context_budget.py` measures the files each phase is told to load, plus the real review
briefs rendered against `examples/hello-sdlc/`.  It is a measurement, not a prompt change and not
a claim about a consumer repository whose own project documents may be larger.

```bash
python3 evals/phase_context_budget.py          # table + down-only ceiling gate
python3 evals/phase_context_budget.py --table  # table only
```

The committed JSON records the date and source commit of the measurement.  A phase's current
measurement may not exceed its ceiling, and a ceiling may be no more than five percent above the
current measurement.  That makes prompt reductions easy to ratchet into the record while refusing
to silently absorb later growth.  The test suite deliberately appends 500 words to
`agrim-loop/references/running.md` and runs the documented command to prove that this gate fails.

It asserts three things:

1. **Nothing important falls off the compaction cliff.** Claude Code re-attaches only the **first
   5,000 tokens** of each skill after a conversation is summarised; the rest is discarded silently.
   Every skill must fit that, and a skill waived for size must still keep its uppercase
   `MUST`/`NEVER`/`ALWAYS` gates inside the kept prefix.
2. **Every reference file is reachable.** Each `*.md` beside a `SKILL.md` must be reachable by
   following links from it (transitively), and every local `*.md` link must resolve.
3. **No instruction text is lost in a restructure** — see below.

Skills that breach today are recorded in `skill_budget_waivers.json`, each frozen at its measured
size and named to the issue that fixes it. A waived skill may **shrink, never grow**; a waiver whose
skill is back inside every budget **fails the build until it is deleted**; and the number of waivers
is itself capped by a test, so a fourth breach cannot be absorbed by adding a line.

**What Tier 0 cannot do, stated plainly: preserving text does not preserve attention.** It catches
*"an instruction vanished"* and *"the gates fell past the cap"*. It **cannot** catch the failure that
matters most after a `SKILL.md` is split — the agent no longer *choosing* to open a reference file it
was pointed at. A skill can pass every check here and still have got worse. Only Tier 2 could tell,
and Tier 2 is parked (#1616, path (b), re-parked 2026-09-03), so **a green Tier 0 is not
evidence that a skill still works**, and #1611's restructure proceeds with that attention risk
explicitly accepted rather than silently assumed away. The clean run prints this caveat next to its
own pass, so nobody meets the verdict without it.

### `--preserved` — the restructure instrument

```bash
python3 evals/skill_structure.py --preserved <git-ref> [skill ...]
```

Concatenates a skill's whole `*.md` corpus at `<git-ref>` and in the working tree, and reports every
instruction unit present in the first and missing from the second. It is deliberately blind to which
*file* text lives in, to re-wrapping, to heading level and to emphasis — so a restructure that
**moves** prose reports nothing, and one that **drops** prose reports exactly what it dropped. Run it
inside the restructuring PR, against the branch point:

```bash
python3 evals/skill_structure.py --preserved origin/main agrim-loop
```

This is a tool rather than a committed snapshot on purpose: a snapshot of the 4,100-odd instruction
units in this corpus would be legitimately red on most days in a repo this active, and a gate that is
red for good reasons is a gate somebody turns off. What CI pins instead is the *instrument* —
including a dress rehearsal that splits the real `agrim-loop` corpus and proves nothing is reported
lost, and its control that drops a gate paragraph and proves it is.

## Tier 1 — deterministic behavioral gate (free, runs in CI)

The intent hook (`hooks/agrim_gate.sh`) is a deterministic proxy for *"the agent got the right discipline
signal"*: a code request must trigger the full spine, a read-only question may be answered directly.
`run.py` runs the hook over the behavioral corpus (`fixtures.json`), scores it, and **fails if the score
drops below `baseline.json`** — that drop is the drift signal. No LLM, no cost, identical every run.

```bash
python3 evals/run.py          # score the corpus, gate on drift vs baseline
```

Add a fixture (`{id, prompt, expect: code|ask|standard, tier2_rubric}`) whenever you add or change a
discipline signal. Raise the baseline only when you add harder fixtures — never lower it to green a red
build without justifying the regression.

## Tier 2 — LLM-judge behavioral evals (opt-in, needs API budget) — PARKED

The only way to measure *actual output quality*: run the agent on each fixture goal, then have an LLM
judge score the transcript against that fixture's `tier2_rubric` (did it plan before editing? did
plan-review catch the planted flaw? did review find the planted bug? did it park on the irreversible
action?). Track scores over time; a drop below baseline is a quality regression.

The runner and its **injectable `agent`/`judge` seam are already here and tested** (`run_tier2`,
`test_tier2_seam_hermetic`) — wiring a real LLM is a one-function change. It is **withheld on purpose**:
it costs money per run and needs a judge-model + rubric decision, so `--live` prints a parked notice
instead of spending. Turn it on once the budget is greenlit; run it nightly / pre-release, not per-PR
(cost + non-determinism).

**Re-parked 2026-09-03**, deliberately and with the reason recorded, not by omission — operator
decision on #1616. A nightly judge is a recurring, unbounded spend committed on an estimate; what
would change the answer is a measured cost figure from a **single** trial pass, plus the judge-model
choice. Until then the honest position is that **nothing in this repo can tell whether a `SKILL.md`
edit degraded agent behaviour**, and that is accepted rather than paid for. Tier 0 above narrows what
can silently go wrong; it does not close this.

```bash
python3 evals/run.py --live    # parked until a real judge is wired
```

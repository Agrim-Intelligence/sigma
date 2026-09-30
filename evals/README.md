# Quality evals — catch drift on every change

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

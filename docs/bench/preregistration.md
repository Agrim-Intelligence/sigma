# Benchmark pre-registration

This method is committed before any benchmark run. It fixes the comparison, scoring and analysis so
that a later result can be checked against a plan written before its data existed. The owner may
edit this document in review; after the first full run, changes are recorded in
[Deviations](#deviations) with their date and reason.

## Arms

| Arm | Host | Plugin | Notes |
| --- | --- | --- | --- |
| A1 Sigma | Claude Code | Sigma at a pinned commit, loaded from a clean `git archive` export | Never load the full repository as a plugin directory. |
| A2 plain agent | Claude Code | none | The agent alone. |
| A3 matched-spend | Claude Code | none | Retry the plain agent in fresh workdirs until visible tests pass or spend reaches A1's spend for that task; score the first visible-test-passing attempt. |

## Held constant

Every arm uses one owner-selected model ID (proposed `claude-sonnet-5-5`), one `claude --version`,
one OS image, one fixture per task, identical prompt text, a 60-minute proposed wall-clock limit,
and the same proposed per-run `--max-budget-usd` belt of $15.

## Tasks

Freeze about 15 tasks before any arm runs, balanced as far as the available task pool permits
between external and internal work. Run one repeat per arm and task.

- External tasks are real issues from permissively licensed (MIT, BSD, or Apache-2.0) open-source
  Python repositories. A merged PR created after the model's published training cutoff fixed each
  issue and changed tests. The starting tree is that PR's base commit, the prompt is its issue text,
  and the PR's test changes are the hidden tests.
- Internal tasks are small fixture repositories shaped like `examples/hello-sdlc`, balanced across
  bug fix, feature, refactor and docs. There are three traps, each containing a planted specification
  contradiction or a planted defect in the obvious plan. A person outside the Sigma team authors
  every trap; the owner recruits that person.
- Each `task.json` stores the SHA-256 of its hidden-test bundle. Commit the hash of
  `tasks/manifest.json` before the first run.

## Isolation

Hidden tests live outside every directory an arm can read, under `~/.sigma-ops/bench/hidden/`.
They are committed to this repository only after the run, together with results. Scoring copies a
final tree to scratch, adds the hidden tests there, and runs them there.

## Metrics

The primary metric is hidden-test pass per run, a boolean. With one repeat per arm and task, each
task contributes one paired binary outcome; there is no within-task pass rate to estimate.

Secondary metrics are cost per passing run (total dollars divided by passes, metered identically
for every arm by `evals/bench/meter.py`), median wall time, interventions (any human action a run
needed), trap catch rate, visible-test regressions, and escapes (an arm reports done but hidden
tests fail). A trap catch is a defect flagged before merge by a plan-review or review block event,
or by the arm's final message naming the contradiction or defect.

## Analysis

Compare A1 against each other arm by the paired difference in its single binary outcome on each
frozen task. Report the mean paired difference and a 95% percentile CI from a paired bootstrap over
tasks with 10,000 resamples and seed 20261001. Report every arm and every metric, including results
where A1 loses.

This reduced design has low power and wide, discrete intervals: it is a launch check on the frozen
task set, not an estimate of performance across a broader task population. The paired comparison
supports only this exact claim wording: **"On this frozen reduced benchmark, Sigma's observed
paired pass-rate difference was X percentage points (95% bootstrap CI [L, U])."** It is forbidden
to describe the result as evidence of superiority or non-inferiority beyond these tasks; it is not evidence of superiority or non-inferiority beyond these tasks, nor a general performance estimate.

## Go threshold

A1 passes this operational release check only when its lower 95% CI bound for `A1 − arm` is at
least −5 points against every other arm. This is the unchanged −5-point threshold; it is evaluated
on the one-repeat paired binary outcomes described above, not loosened for the smaller design. Its
trap catch rate must exceed A2's, and every run must be reported. This threshold is a decision rule
for the frozen task set, not a population-level non-inferiority conclusion. Launch material may
claim only what the stated intervals and exact claim wording support.

## Runs

Run one repeat per arm and task unattended. A run lost to infrastructure, such as a host crash or
rate limit, is re-run once and logged. Every other failure counts as a failure; nothing is dropped.

## Deviations

Record every change to this pre-registration here with its date and reason.

- **2026-10-03 — Reduced launch scope before the first benchmark run.** The owner reduced the
  launch design from five arms, at least 30 tasks, three repeats, and five traps to three arms
  (Sigma, plain agent, and matched-spend), about 15 tasks, one repeat, and three independently
  authored traps. This makes the launch affordable while preserving a pre-run, paired comparison;
  the analysis and claim boundary above were re-derived to make its lower power and wider intervals
  explicit. No benchmark task has run under either design.

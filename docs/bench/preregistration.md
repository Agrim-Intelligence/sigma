# Benchmark pre-registration

This method is committed before any benchmark run. It fixes the comparison, scoring and analysis so
that a later result can be checked against a plan written before its data existed. The owner may
edit this document in review; after the first full run, changes are recorded in
[Deviations](#deviations) with their date and reason.

## Arms

| Arm | Host | Plugin | Notes |
| --- | --- | --- | --- |
| A1 plain | Claude Code | none | The agent alone. |
| A2 prompt-only | Claude Code | One published prompt/workflow plugin, pinned by version; proposed: superpowers | No code gates. |
| A3 predecessor | Claude Code | Predecessor latest release on the run date: 1.4.26 as of 2026-09-29, isolated install | Internal regression evidence; the owner may limit this arm to a 10-task subset for cost. |
| A4 Sigma | Claude Code | Sigma at a pinned commit, loaded from a clean `git archive` export | Never load the full repository as a plugin directory. |
| A5 matched spend | Claude Code | none | Retry the plain agent in fresh workdirs until visible tests pass or spend reaches A4's median spend for that task; score the first visible-test-passing attempt. |

## Held constant

Every arm uses one owner-selected model ID (proposed `claude-sonnet-5-5`), one `claude --version`,
one OS image, one fixture per task, identical prompt text, a 60-minute proposed wall-clock limit,
and the same proposed per-run `--max-budget-usd` belt of $15.

## Tasks

Freeze at least 30 tasks before any arm runs: 15 external and 15 internal. Each task is repeated
three times per arm.

- External tasks are real issues from permissively licensed (MIT, BSD, or Apache-2.0) open-source
  Python repositories. A merged PR created after the model's published training cutoff fixed each
  issue and changed tests. The starting tree is that PR's base commit, the prompt is its issue text,
  and the PR's test changes are the hidden tests.
- Internal tasks are small fixture repositories shaped like `examples/hello-sdlc`, balanced across
  bug fix, feature, refactor and docs. Five are traps: three contain a planted specification
  contradiction and two contain a planted defect in the obvious plan. A person outside the Sigma
  team authors every trap; the owner recruits that person.
- Each `task.json` stores the SHA-256 of its hidden-test bundle. Commit the hash of
  `tasks/manifest.json` before the first run.

## Isolation

Hidden tests live outside every directory an arm can read, under `~/.sigma-ops/bench/hidden/`.
They are committed to this repository only after the run, together with results. Scoring copies a
final tree to scratch, adds the hidden tests there, and runs them there.

## Metrics

The primary metric is hidden-test pass per run, a boolean; per-task pass rate is passes out of
three repeats.

Secondary metrics are cost per passing run (total dollars divided by passes, metered identically
for every arm by `evals/bench/meter.py`), median wall time, interventions (any human action a run
needed), trap catch rate, visible-test regressions, and escapes (an arm reports done but hidden
tests fail). A trap catch is a defect flagged before merge by a plan-review or review block event,
or by the arm's final message naming the contradiction or defect.

## Analysis

Compare A4 against each other arm by paired difference in per-task pass rate. Report a 95% CI from
a paired bootstrap over tasks with 10,000 resamples and seed 20261001. Report every arm and every
metric, including results where A4 loses.

## Go threshold

A4 passes only when it is non-inferior to every other arm on pass rate: the lower 95% CI bound of
`A4 − arm` is at least −5 points. Its trap catch rate must exceed A1's, and every run must be
reported. Launch material may claim only what these intervals support.

## Runs

Run three repeats per arm and task unattended. A run lost to infrastructure, such as a host crash
or rate limit, is re-run once and logged. Every other failure counts as a failure; nothing is
dropped.

## Deviations

After the first full run, record every change to this pre-registration here with its date and
reason. Before that run, this section remains empty.

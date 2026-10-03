# Benchmark pre-registration

This method is committed before any benchmark run. It fixes the comparison, scoring and analysis so
that a later result can be checked against a plan written before its data existed. The owner may
edit this document in review; after the first full run, changes are recorded in
[Deviations](#deviations) with their date and reason. The decision rule below was replaced on
2026-10-03, before any run, by an exact paired test chosen to fit 15 tasks.

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

Each comparison is A1 against one other arm (A2, then A3) on the same frozen tasks. A task is a
**win** when A1 passes its hidden tests and the other arm fails, a **loss** when the other arm passes
and A1 fails, and a **tie** when both pass or both fail. Report, for each arm: the counts W, L and T,
the mean paired difference in percentage points, and the exact one-sided p-values defined under
[Go threshold](#go-threshold) for both directions. Report every arm and every metric, including
results where A1 loses. No bootstrap interval is reported: the superseded bootstrap bound is
retained in [Deviations](#deviations) with the reason it was replaced.

This reduced design has low power and discrete results: it is a launch check on the frozen task
set, not an estimate of performance across a broader task population. The only claim wording
allowed is the following. For each other arm, state:

**"On this frozen reduced benchmark, Sigma won W and lost L of N paired tasks against ARM (observed paired pass-rate difference X percentage points; exact one-sided sign-test p = P)."**

and then exactly one of the following sentences, according to the outcome defined under Go threshold:

- GO: **"This meets the pre-registered release check (exact one-sided sign test at alpha = 0.05 against each other arm)."**
- INCONCLUSIVE: **"The pre-registered release check was inconclusive: this benchmark does not show whether Sigma is better or worse than the other arms."**
- NO-GO: **"The pre-registered release check was not passed: Sigma was significantly worse than ARM on this frozen benchmark."**

Where the owner changes alpha (see [Owner decisions](#owner-decisions)) the number in the GO sentence
changes with it and nothing else does. The result is not
evidence of superiority or non-inferiority beyond these tasks, and it is forbidden to describe it as one
or as a general performance estimate. An INCONCLUSIVE result is never a win and is never reported as one.

## Go threshold

The predecessor is no longer an arm of this benchmark, so no launch claim compares Sigma against it.
The release check has three outcomes, GO, NO-GO and INCONCLUSIVE, decided only from the paired
outcomes defined under [Analysis](#analysis).

**Test, per comparison (A1 against A2, and A1 against A3).** Ties carry no information about
direction and are ignored. Let D = W + L be the number of discordant tasks.

- H0: A1 is no better than the arm, meaning that on a discordant task A1 is the one that passes with
  probability at most 1/2. H1: A1 is better.
- Conditional on D, and under H0, W is Binomial(D, 1/2). The test is an exact one-sided binomial
  test: the p-value is P(Binomial(D, 1/2) >= W), compared with alpha as an exact fraction.
  alpha = 0.05 (recommended; an owner decision, see below).
- **The margin is a margin of 0 tasks (0 points): this is a superiority test, not a non-inferiority
  test.** There is no non-inferiority margin because none is attainable at this size (see
  [Operating characteristics](#operating-characteristics)).
- Paired outcome GO when that p-value is at most alpha. Paired outcome NO-GO when the same test, with
  L in place of W, has a p-value at most alpha (A1 is significantly worse). Otherwise INCONCLUSIVE.
  With alpha = 0.05, D of 4 or fewer is always INCONCLUSIVE, because 4 of 4 has p = 0.0625.

**Decision across the two arms.**

- GO (the release check is passed) only when both paired outcomes are GO **and** A1's trap catch rate
  exceeds A2's. The trap condition is unchanged from the superseded rule; with three traps it is a
  descriptive count and is not tested statistically, so a GO that fails only this condition is
  reported as INCONCLUSIVE, never as GO.
- NO-GO when either paired outcome is NO-GO. NO-GO takes precedence over GO.
- INCONCLUSIVE otherwise. INCONCLUSIVE means the benchmark did not decide: it is never a win, never
  evidence that Sigma is equal to the other arms, and permits no comparative claim, only the counts.

**Multiplicity.** GO requires two one-sided tests to reject, each at alpha: an intersection-union
test, so the probability of GO when A1 is no better than at least one arm is at most alpha, with no
correction and whatever the dependence between the two arms. NO-GO is a union of two tests, so
the probability of NO-GO when A1 truly ties both arms is at most 2 alpha. Both arms are
named as comparisons in advance and none is dropped.

**What the rule guarantees and what it does not.** It guarantees that a wrong GO (A1 truly tying or
worse than an arm) has probability at most alpha, by exact computation, for every discordant rate, under the
model in the next section. It does not guarantee power: a real but moderate advantage will usually
come out INCONCLUSIVE. It does not establish non-inferiority at any margin, and a GO is a statement
about this frozen task set, not about tasks in general.

Check that the published figures below are the script's current output (exit 0 when they are,
exit 1 when any has drifted):

```
python3 evals/bench/decision_rule.py --check docs/bench/preregistration.md
```

## Owner decisions

These values are recommended, not settled by statistics alone. Each is an owner decision; the
document records the recommendation and what the alternatives cost, and the owner may change them
while no benchmark task has run.

1. **alpha, one-sided (owner decision).** Recommended 0.05. The alpha table in
   [Operating characteristics](#operating-characteristics) gives the cost of each choice at 15 tasks:
   A smaller alpha (0.025) needs more net wins and cuts power further; a larger one (0.10, 0.20) needs fewer
   net wins, raises the worst-case wrong-GO probability toward alpha itself and raises power. A looser alpha
   buys power with a larger chance of a claim the data do not support.
2. **Margin (owner decision).** Recommended 0 tasks (superiority). A non-inferiority margin of 1, 2 or 3
   tasks (6.7, 13.3 or 20 points) would let a GO say "no worse than that margin", but the
   rejected-alternative table shows margins below 20 points cost more net wins than superiority does, and a
   20-point margin is too loose to call non-inferior.
3. **Arms in the decision (owner decision).** Recommended: both A2 and A3 must pass (an intersection-union
   test, no alpha correction needed). The alternative is to name A3 (matched-spend, the harder comparison)
   as the single primary comparison and report A2 descriptively; that raises P(GO) to the single-arm value in
   the table but says nothing about A2 at the same confidence.
4. **What follows an INCONCLUSIVE outcome (owner decision).** The statistics only fix that it is not a win
   and permits no comparative claim. Whether the launch proceeds without a performance claim, or more
   tasks are added (see tasks needed above), is a business decision this document does not make.

## Operating characteristics

Every figure in this section is printed by `python3 evals/bench/decision_rule.py` (stdlib only,
exact rational arithmetic, rounded only for printing), and `tests/test_bench_decision_rule.py` fails
if the published block differs from that output. Nothing here was measured on a real run: no
benchmark task has run.

**Model.** Tasks are independent and identically distributed; on each, A1 wins with probability pw,
loses with probability pl and ties otherwise. "d" is the discordant rate pw + pl and the true
difference is pw - pl. The test itself is exact for any pw and pl with pw <= pl, and also when tasks
differ but each has pw = pl. The probabilities of GO under an advantage assume the identical-task
model; real tasks differ, and that can move power in either direction. The script cannot check this.

**Reading the tables.** The smallest passing result at alpha = 0.05 is 5 of 5 discordant tasks
(net 5 wins), and wins needed rise with D as the first table shows. The wrong-GO probability when A1
truly ties is, in the worst case, 0.030 at 15 tasks (a maximum over a grid of discordant rates; the guarantee
itself is the bound alpha) and below alpha for every task count from 12 to 18 (the size table); when A1 is
truly worse it is smaller still.
Error control is therefore good. Power is not: with 40% of tasks discordant, the probability of GO
for one comparison at 15 tasks is, by true advantage, +20 points 0.176, +30 points 0.410 and
+40 points 0.783, and against both arms it is no larger (the Frechet bounds are in the table, which also ignores the
trap-catch condition, so the real probability of GO is lower still).
Tasks needed for the probability of GO to first reach 0.8 at that discordance: +20 points needs 67 tasks,
+30 points needs 30 tasks, +40 points needs 16 tasks.

**Low power, stated plainly.** No rule of this size gives both a small wrong-GO probability and good
power for a moderate advantage; 15 tasks cannot. This rule keeps the wrong-GO probability small and
accepts low power. There is no non-inferiority claim available: in the rejected-alternative table, an
unconditional test of a non-inferiority margin of 1 or 2 tasks needs more net wins than the 5 this
rule needs, and only a margin of 3 tasks (20 points), which is too loose to call non-inferior,
needs fewer. A margin floor on top of the sign test (an additional requirement of at
least m net wins) never binds for m <= 5, so it would add nothing.

**What each outcome supports.** GO supports only: on this frozen task set A1 passed significantly more
tasks than each other arm (alpha as stated), using the claim wording in Analysis. NO-GO supports
only: A1 was significantly worse than an arm on this task set. INCONCLUSIVE, which is the likely
outcome unless A1's true advantage is large, supports only the descriptive counts W, L and T for each
arm; it does not support "better", "worse", "equal" or "no worse".

<!-- operating-characteristics:begin -->

Outcomes by discordant count, one comparison, one-sided alpha = 0.050 (identical for any number of tasks; D cannot exceed n):

| Discordant tasks D | GO needs wins W of at least | exact p at that W | NO-GO needs losses L of at least | INCONCLUSIVE when |
|---|---|---|---|---|
| 1 | cannot (even 1 of 1 has p = 0.500) | - | cannot | always |
| 2 | cannot (even 2 of 2 has p = 0.250) | - | cannot | always |
| 3 | cannot (even 3 of 3 has p = 0.125) | - | cannot | always |
| 4 | cannot (even 4 of 4 has p = 0.062) | - | cannot | always |
| 5 | 5 | 0.031 | 5 | W = 1 to 4 |
| 6 | 6 | 0.016 | 6 | W = 1 to 5 |
| 7 | 7 | 0.008 | 7 | W = 1 to 6 |
| 8 | 7 | 0.035 | 7 | W = 2 to 6 |
| 9 | 8 | 0.020 | 8 | W = 2 to 7 |
| 10 | 9 | 0.011 | 9 | W = 2 to 8 |
| 11 | 9 | 0.033 | 9 | W = 3 to 8 |
| 12 | 10 | 0.019 | 10 | W = 3 to 9 |
| 13 | 10 | 0.046 | 10 | W = 4 to 9 |
| 14 | 11 | 0.029 | 11 | W = 4 to 10 |
| 15 | 12 | 0.018 | 12 | W = 4 to 11 |
| 16 | 12 | 0.038 | 12 | W = 5 to 11 |
| 17 | 13 | 0.025 | 13 | W = 5 to 12 |
| 18 | 13 | 0.048 | 13 | W = 6 to 12 |

Probabilities for n = 15 tasks, one comparison unless the column says both arms (d is the share of tasks on which the two arms differ; difference = Sigma minus the other arm, in points). P(GO) ignores the trap-catch condition, which can only lower it, and the both-arms column assumes both arms have this same d and difference:

| Discordant rate d | True difference (points) | P(GO) one arm | P(NO-GO) one arm | P(GO) against both arms, between |
|---|---|---|---|---|
| 20 | -20 | 0.000 | 0.164 | 0.000 and 0.000 |
| 20 | -10 | 0.000 | 0.035 | 0.000 and 0.000 |
| 20 | 0 | 0.004 | 0.004 | 0.000 and 0.004 |
| 20 | +10 | 0.035 | 0.000 | 0.000 and 0.035 |
| 20 | +20 | 0.164 | 0.000 | 0.000 and 0.164 |
| 40 | -20 | 0.000 | 0.176 | 0.000 and 0.000 |
| 40 | -10 | 0.003 | 0.061 | 0.000 and 0.003 |
| 40 | 0 | 0.016 | 0.016 | 0.000 and 0.016 |
| 40 | +10 | 0.061 | 0.003 | 0.000 and 0.061 |
| 40 | +20 | 0.176 | 0.000 | 0.000 and 0.176 |
| 40 | +30 | 0.410 | 0.000 | 0.000 and 0.410 |
| 40 | +40 | 0.783 | 0.000 | 0.565 and 0.783 |

P(GO) for one comparison at other task counts:

| Tasks n | d=40, diff -20 | d=40, diff 0 | d=40, diff +20 | d=40, diff +30 | d=40, diff +40 |
|---|---|---|---|---|---|
| 12 | 0.000 | 0.012 | 0.119 | 0.277 | 0.562 |
| 13 | 0.000 | 0.014 | 0.139 | 0.324 | 0.647 |
| 14 | 0.000 | 0.015 | 0.158 | 0.368 | 0.721 |
| 15 | 0.000 | 0.016 | 0.176 | 0.410 | 0.783 |
| 16 | 0.000 | 0.017 | 0.194 | 0.450 | 0.833 |
| 17 | 0.000 | 0.018 | 0.212 | 0.487 | 0.874 |
| 18 | 0.000 | 0.019 | 0.229 | 0.522 | 0.906 |

Wrong-GO probability when Sigma truly ties or is worse, maximised over discordant rates 1 to 100 percent in steps of 1 (a grid maximum):

| Tasks n | worst-case P(GO) when Sigma truly ties (diff 0) | at discordant rate | worst-case P(GO) when Sigma is truly 10 points worse | at discordant rate |
|---|---|---|---|---|
| 12 | 0.023 | 94 | 0.009 | 95 |
| 13 | 0.046 | 100 | 0.020 | 100 |
| 14 | 0.034 | 95 | 0.014 | 95 |
| 15 | 0.030 | 87 | 0.011 | 89 |
| 16 | 0.038 | 100 | 0.015 | 100 |
| 17 | 0.029 | 80 | 0.010 | 95 |
| 18 | 0.048 | 100 | 0.018 | 100 |

Tasks needed for 80 percent power (one comparison, same alpha):

| True difference (points), d=40 | Tasks needed for P(GO) to first reach 0.800 |
|---|---|
| +20 | 67 |
| +30 | 30 |
| +40 | 16 |

The superseded bootstrap rule on the same counts (n = 15, one comparison, exact infinite-resample limit):

| Sigma wins | Sigma losses | ties | superseded rule: lower bound (points) | passes superseded rule (bound >= -5) | this rule |
|---|---|---|---|---|---|
| 0 | 0 | 15 | 0.0 | yes | INCONCLUSIVE |
| 2 | 0 | 13 | 0.0 | yes | INCONCLUSIVE |
| 3 | 0 | 12 | 0.0 | yes | INCONCLUSIVE |
| 3 | 1 | 11 | -13.3 | no | INCONCLUSIVE |
| 4 | 1 | 10 | -6.7 | no | INCONCLUSIVE |
| 5 | 0 | 10 | 13.3 | yes | GO |
| 5 | 1 | 9 | 0.0 | yes | INCONCLUSIVE |
| 6 | 2 | 7 | -6.7 | no | INCONCLUSIVE |
| 7 | 1 | 7 | 6.7 | yes | GO |
| 8 | 2 | 5 | 0.0 | yes | INCONCLUSIVE |

Owner decision, alpha alternatives at n = 15 (one comparison):

| One-sided alpha | fewest net wins that can pass (W - L) | worst-case P(GO) at a true tie | P(GO) at +20 points, d=40 | P(GO) at +30 points, d=40 | P(GO) at +40 points, d=40 |
|---|---|---|---|---|---|
| 0.025 | 6 | 0.018 | 0.099 | 0.266 | 0.597 |
| 0.050 | 5 | 0.030 | 0.176 | 0.410 | 0.783 |
| 0.100 | 4 | 0.069 | 0.297 | 0.574 | 0.909 |
| 0.200 | 3 | 0.151 | 0.516 | 0.790 | 0.973 |

Rejected alternative, non-inferiority by an unconditional test that rejects when W - L is at least c, size maximised over a discordant-rate grid in steps of 1 percent:

| Margin in tasks | Margin in points | one-sided alpha | fewest net wins (W - L) that certify it | size reached |
|---|---|---|---|---|
| 0 | 0.0 | 0.050 | 8 | 0.021 |
| 0 | 0.0 | 0.100 | 6 | 0.069 |
| 1 | 6.7 | 0.050 | 6 | 0.040 |
| 1 | 6.7 | 0.100 | 5 | 0.098 |
| 2 | 13.3 | 0.050 | 6 | 0.022 |
| 2 | 13.3 | 0.100 | 4 | 0.069 |
| 3 | 20.0 | 0.050 | 4 | 0.040 |
| 3 | 20.0 | 0.100 | 3 | 0.095 |

<!-- operating-characteristics:end -->

## Runs

Run one repeat per arm and task unattended. A run lost to infrastructure, such as a host crash or
rate limit, is re-run once and logged. Every other failure counts as a failure; nothing is dropped.

## Deviations

Record every change to this pre-registration here with its date and reason.

- **2026-10-03 — Replaced the go threshold with an exact paired test (#502).** The owner replaced the
  go threshold and the bootstrap interval before any benchmark task ran. Reason: at about 15 tasks, one
  repeat and 0/1 outcomes the superseded bound did not discriminate (see the table in
  [Operating characteristics](#operating-characteristics)): with every task tied it passes, with 2 wins and
  no losses it passes, and with 3 wins, 1 loss and 11 ties it fails (lower bound -13.3 points); one task is 6.7
  points, so a 5-point margin sits below the smallest possible difference. What it replaces: the Analysis
  paragraph (mean difference and 95% paired-bootstrap interval) and the Go threshold section, both kept
  below, marked SUPERSEDED. What replaces them: an exact one-sided binomial test on discordant tasks per
  comparison, alpha 0.05 and margin 0 tasks recommended (owner decisions), outcomes GO, NO-GO and
  INCONCLUSIVE, corrected claim wording (the duplicated clause in the old claim sentence is stated once), and
  the statement that the predecessor is no longer an arm. No benchmark task has run and no money was spent.

  **SUPERSEDED 2026-10-03 by the rule above (#502); kept verbatim for the record, not in force.** (The quoted claim sentence repeats one clause; it is kept exactly as it was, and the live Analysis states it once.)

  > **Analysis (superseded)**
  >
  > Compare A1 against each other arm by the paired difference in its single binary outcome on each
  > frozen task. Report the mean paired difference and a 95% percentile CI from a paired bootstrap over
  > tasks with 10,000 resamples and seed 20261001. Report every arm and every metric, including results
  > where A1 loses.
  >
  > This reduced design has low power and wide, discrete intervals: it is a launch check on the frozen
  > task set, not an estimate of performance across a broader task population. The paired comparison
  > supports only this exact claim wording: **"On this frozen reduced benchmark, Sigma's observed
  > paired pass-rate difference was X percentage points (95% bootstrap CI [L, U])."** It is forbidden
  > to describe the result as evidence of superiority or non-inferiority beyond these tasks; it is not evidence of superiority or non-inferiority beyond these tasks, nor a general performance estimate.
  >
  > **Go threshold (superseded)**
  >
  > A1 passes this operational release check only when its lower 95% CI bound for `A1 − arm` is at
  > least −5 points against every other arm. This is the unchanged −5-point threshold; it is evaluated
  > on the one-repeat paired binary outcomes described above, not loosened for the smaller design. Its
  > trap catch rate must exceed A2's, and every run must be reported. This threshold is a decision rule
  > for the frozen task set, not a population-level non-inferiority conclusion. Launch material may
  > claim only what the stated intervals and exact claim wording support.

- **2026-10-03 — Reduced launch scope before the first benchmark run.** The owner reduced the
  launch design from five arms, at least 30 tasks, three repeats, and five traps to three arms
  (Sigma, plain agent, and matched-spend), about 15 tasks, one repeat, and three independently
  authored traps. This makes the launch affordable while preserving a pre-run, paired comparison;
  the analysis and claim boundary above were re-derived to make its lower power and wider intervals
  explicit. No benchmark task has run under either design.

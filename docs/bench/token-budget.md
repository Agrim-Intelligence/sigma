# Benchmark token budget, batches and resume (subscription)

Decided by the owner on 2026-10-06: there is no Anthropic API key and no API funds, so the benchmark (#275) runs
through headless `claude -p` on the owner's Max **subscription** login. That changes what can be measured and limited:
a subscription is not charged per token, and its limits are rate windows, not dollars. This page records the measured
basis for the token ceiling, the batch design and the stop rule. The method itself is
[`preregistration.md`](preregistration.md); the entry that adopts this page is its Deviations entry dated 2026-10-06.
No arm has been run, and nothing below is a measurement of the benchmark: it is measured on validation runs of a
different shape.

## What is measured, and what is not

- **Tokens are the measured fact.** `evals/bench/meter.py` reads the host's own usage records (the session transcripts,
  subagents included) and reports input, output, cache-read and cache-write tokens and their sum, `tokens_total`.
  The matched-spend arm (A3) and the ceiling use `tokens_total`. It is dominated by cache reads (92 percent of the
  loop sessions below), which are cheap per token; the four components are reported per row and in the summary so a reader
  can weight them differently, and the choice of the plain sum is the owner's decision of 2026-10-06 (spend defined in tokens).
- **Dollars are indicative.** The same tokens priced at the published list rates in the shipped rate card. Under a
  subscription they are not what anyone is charged and are never described as a bill; no claim is made in dollars per task.
- **Not measured: whether a token-authenticated `claude -p` in an empty profile writes the same usage records and
  rate-limit records as the interactive logins the table below came from.** The first batch is that measurement. If it
  leaves no readable usage record, the harness stops before scoring anything.

## The measured sessions

Totals are the host's own end-of-run `modelUsage` figures (all four token kinds, subagents included) for 24 headless
sessions on `claude-sonnet-5-5` from the owner's two validation runs of 2026-10-06: 22 full Sigma loop sessions (labelled
S) and 2 plain agent runs (labelled P), each on a real, post-cutoff bug with the same constraints for both arms. The
validation reports' own "tokens" lines quoted the last turn's usage, not the run total; the totals here are the run
totals. The harness meter reads transcripts rather than that end-of-run summary; the two have not been compared here.

| Id | Kind | Input | Output | Cache read | Cache write | Total tokens | Indicative USD |
|---|---|---|---|---|---|---|---|
| S01 | sigma-loop | 212 | 39,442 | 4,636,702 | 297,450 | 4,973,806 | 2.1345 |
| S02 | sigma-loop | 162 | 40,463 | 3,816,287 | 291,801 | 4,148,713 | 1.9617 |
| S03 | sigma-loop | 172 | 44,773 | 3,817,010 | 283,686 | 4,145,641 | 1.9869 |
| S04 | sigma-loop | 176 | 37,708 | 3,820,212 | 301,221 | 4,159,317 | 1.9793 |
| S05 | sigma-loop | 40 | 7,456 | 995,929 | 110,222 | 1,113,647 | 0.6269 |
| S06 | sigma-loop | 34 | 7,377 | 1,252,110 | 68,688 | 1,328,209 | 0.5990 |
| S07 | sigma-loop | 208 | 47,980 | 4,880,442 | 307,754 | 5,236,384 | 2.2862 |
| S08 | sigma-loop | 176 | 40,353 | 3,647,516 | 294,818 | 3,982,863 | 1.9373 |
| S09 | sigma-loop | 156 | 40,295 | 3,660,427 | 234,836 | 3,935,714 | 1.8168 |
| S10 | sigma-loop | 156 | 35,964 | 3,625,209 | 254,248 | 3,915,577 | 1.8117 |
| S11 | sigma-loop | 152 | 40,115 | 3,523,165 | 271,363 | 3,834,795 | 1.8809 |
| S12 | sigma-loop | 140 | 32,361 | 3,659,027 | 248,058 | 3,939,586 | 1.7330 |
| S13 | sigma-loop | 156 | 29,922 | 3,694,976 | 254,488 | 3,979,542 | 1.7514 |
| S14 | sigma-loop | 158 | 40,434 | 4,497,473 | 345,711 | 4,883,776 | 2.2583 |
| S15 | sigma-loop | 58 | 14,482 | 1,322,390 | 100,415 | 1,437,345 | 0.7220 |
| S16 | sigma-loop | 178 | 41,647 | 3,985,957 | 320,801 | 4,348,583 | 2.0693 |
| S17 | sigma-loop | 158 | 40,428 | 4,358,286 | 307,005 | 4,705,877 | 2.1228 |
| S18 | sigma-loop | 164 | 42,560 | 4,049,629 | 296,480 | 4,388,833 | 2.0960 |
| S19 | sigma-loop | 174 | 35,453 | 3,985,513 | 276,713 | 4,297,853 | 1.9023 |
| S20 | sigma-loop | 64 | 11,978 | 1,391,281 | 115,198 | 1,518,521 | 0.7429 |
| S21 | sigma-loop | 128 | 24,448 | 2,899,524 | 256,841 | 3,180,941 | 1.5294 |
| S22 | sigma-loop | 160 | 29,741 | 3,552,846 | 246,901 | 3,829,648 | 1.7109 |
| P1 | plain | 16 | 2,153 | 273,836 | 17,050 | 293,055 | 0.1445 |
| P2 | plain | 8 | 1,540 | 125,806 | 14,811 | 142,165 | 0.0998 |

Summary of the table (recomputed by `tests/test_bench_preregistration.py`):

| | n | min | median | max |
|---|---|---|---|---|
| Sigma loop session, total tokens | 22 | 1,113,647 | 3,981,203 | 5,236,384 |
| Plain run, total tokens | 2 | 142,165 | 217,610 | 293,055 |

Tokens per indicative dollar, over the 24 sessions: minimum 1,424,499 (a plain run), maximum 2,330,197.

## The ceiling, and how it was derived

The decision of 2026-10-05 was a ceiling of $150 on API billing. The equivalent token ceiling is that dollar figure
times the measured tokens per indicative dollar. Taking the **lowest** measured ratio (1,424,499 tokens per dollar) means
the ceiling never corresponds to more than $150 of indicative dollars on any measured session type:

    150 x 1,424,499 = 213.7 million, rounded down to a multiple of 10 million = **210,000,000 tokens**

At the pooled ratio (about 2.16 million tokens per dollar) the same $150 would be about 323 million tokens, so 210 million
is the conservative reading. The ceiling is enforced **between** runs: the host has no per-run token cap. The per-run
`--max-budget-usd` belt ($15 proposed) stays as the host's own client-side estimate and a runaway guard; at the measured
ratios $15 is about 21 to 35 million tokens, so one run can overshoot the ceiling by that much, and an A3 pair (several
attempts, each under the belt, stopping once spend reaches A1's) by more. Every overshoot is counted in the results file.

Cross-check against expected need, per task (A1 + A2 + A3): the observed worst case is
(5,236,384 + 293,055) x 2 = 11,058,878 tokens, so 15 tasks come to 165.9 million, under 210 million; the typical case
(A1 median, A2 median, and A3 passing on its first attempt) is 3,981,203 + 217,610 + 217,610 = 4.42 million per task, 66.3 million
for 15. So the ceiling is a runaway tripwire, expected to trip only if the benchmark costs about 1.3 times the worst
observed pattern. If it trips, the remaining pairs are recorded `not-run` and the run stops (exit 76); raising it is a
recorded decision, not a flag typed in passing.

**This is an estimate, not a bound.** Uncertainty, stated plainly:

- The 22 Sigma sessions are repeats of about four distinct bugs, so the effective n for A1 is about 4, not 22; the plain
  runs have n = 2. The benchmark's 15 tasks (8 external, 4 internal, 3 traps) differ from them and may be larger.
- The A1 "session" here is a `/sigma-loop` headless session (plus a small init); how the benchmark's A1 prompt drives
  Sigma is not the same gesture, and A3's retry count is unknown (up to the attempt bound).
- Stream totals versus harness transcript totals were not compared. Max limits are not denominated in tokens, so the
  ceiling bounds work done, not allowance consumed.

## Rate windows: why the run is batched

The same sessions carried the host's rate-limit readings (resolution 0.01, taken while the owner was also using the
account, so they are upper bounds on one session's own effect). Of the 22 loop sessions, 16 had two or more readings:
the five-hour window moved +0.01 in 13 of them and +0.02 in 3; the seven-day window moved 0.00 in 12 and +0.01 in 4. The
seven-day window stood at 0.79 to 0.84 at the time. So one A1-sized session costs about 1 to 2 percent of a five-hour
window and at most about 1 percent of a week, and the whole benchmark (15 A1 sessions plus the other arms, about 2 A1-session
equivalents per task in the worst case) is on the order of tens of percent of a weekly window: it may not fit in one window,
which is why it is batched and resumable rather than a single unattended run.

## Batches, the cursor and the stop rule

- **Batch size: 3 pairs** (`--batch-pairs 3`, the CLI default), one task's three arms (A1, A2, A3 in that order). A pair is
  one task on one arm. By the measurements above a worst-case batch is about 11.1 million tokens, roughly two A1-maximum
  sessions, or about 0.02 to 0.05 of a five-hour window. The batch size is a **checkpoint granularity and a politeness
  bound**, not the limiter: the limiter is the host's own rejection. It is a flag; the number 3 follows from keeping A3 next to its A1.
- **The cursor is the results file.** It is rewritten atomically (and flushed to disk) after every pair and always lists
  every planned pair, in order, as `completed`, `failed` or `not-run`. `complete` is true only when no pair is not-run.
  Before a pair starts the file also holds an `in_flight` marker. A re-invocation without `--resume` is refused when the file
  exists (paid rows are never silently overwritten), and `--resume` is refused when it does not.
- **`--resume` never re-runs a completed or failed pair.** It re-attempts not-run pairs only, carries A1's stored token
  count to A3, and carries the tokens already spent: the rows, plus `tokens_unscored` (tokens burned by pairs that did not
  complete). It refuses unless the conditions are identical to the first batch: manifest hash, arms, repeats, model,
  permission mode, Sigma commit, `claude --version`, belt, attempt bound, deadlines, and a digest of the hidden bundles. The
  token ceiling may be raised on resume (recorded in `ceiling_changes`) and never lowered. The `claude` version refusal
  names its lever: pin the version (for example `DISABLE_AUTOUPDATER` in the launcher's `extra_env`) or start a new file.
- **A run that died in flight** (SIGKILL, power loss) leaves the marker. Resume counts it in `unknown_runs`, says the token
  total is then a lower bound, and re-runs the pair; it is never recorded as a failure. A crashed run's directory under the
  scratch root must be emptied deliberately before resuming (the existing rule).
- **A lock** (`<results>.lock`, `flock`) refuses a second invocation on the same cursor while one is running; the lock
  file is empty and safe to delete when no run is active.
- **Stop rule.** When a pair's transcripts show a host rate-limit record and the run did not exit cleanly, or the
  session ended on the record (no real model turn after it; the exit status of a throttled `claude -p` is unmeasured, so
  it is not what decides), the run is discarded and **never scored**; the pair is recorded `not-run` with the reset time the host named, its tokens are counted
  as unscored, no further pair starts, the file says `stop.kind: rate-limit` and `complete: false`, and the harness exits 75.
  A transient record that real work followed, on a run that exited 0, is scored as usual and the row says so. The record's shape was copied from a
  real transcript (an interactive session that hit a weekly limit); a `-p` run under a subscription token is not observed.
  Independently, a run that leaves no readable usage records, or zero tokens, stops the harness ("authentication is missing or
  the transcripts cannot be read") before scoring: it is not-run, never a failure.
- **The lever, in order:** wait for the reset the stop names, then run the same command with `--resume`. To go slower, pass
  a smaller `--batch-pairs`. To continue after a ceiling stop, raise `--max-tokens` with a recorded Deviation.
  `bench.py summarize --results <file>` refuses any file with a not-run pair, so a half-finished file cannot be analysed.
  **Gap, stated:** `tools/readiness/decide.py` checks only that the named benchmark file is tracked at the main branch, not that
  it is complete; commit only a complete results file as evidence until that check exists.

## What the claim becomes

On the frozen 15 tasks: the **relative outcome** (W, L and T against each other arm, exactly as before) and the **relative
token cost** per arm (tokens per arm, tokens per passing run, and each arm's tokens relative to A1's), from
`bench.py summarize`. The decision rule is unchanged: the exact one-sided sign test at alpha = 0.05 on hidden-test passes,
GO only against both arms with the trap condition, INCONCLUSIVE meaning no comparative claim. There is **no dollars-per-task
claim** and no claim about what the run costs anyone in money.

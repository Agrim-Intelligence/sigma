# Cost calibration pilots (#361)

Measured on 2026-10-04. Review units are the ones in [review-units-a5c615062313.json](review-units-a5c615062313.json) (the first cut; #581
re-cut `../review-units.json` at a later commit, and these unit ids and line counts are the first cut's), cut from the frozen
commit `a5c615062313` and reviewed in a snapshot made by `tools/readiness/baseline.py snapshot`. The meter and rate
card are those of main at `e48420fa97f6` (`evals/bench/meter.py`, `skills/agrim-loop/rates/anthropic_list_prices.csv`,
sha256 prefix `b19f38838649ca5d`). Every figure below is the meter's own output for the reviewer's transcript;
nothing is estimated unless the row says so.

## Status of the four pilots

| Pilot | Status |
|---|---|
| P-a (high-risk unit A01) | measured: one review pass and one verification pass; one extra high-risk unit (A06) reviewed |
| P-b (sampled unit B01) | measured: one review pass and one verification pass |
| P-c (one live goal) | not measured: no recorded phase costs exist (see its section) |
| P-d (benchmark pilot) | not measured: no operator-supplied isolation launcher exists, and the task set, though frozen on 2026-10-05, has agent-authored traps that no agent has yet run against (see its section) |

## Method and what a number means

- Control, run before any pilot: `meter.py` on a transcript with one turn on a model absent from the rate card and one
  priced turn reports `cost_usd: null`, `unpriced_turns: 1` and keeps the priced part separate
  (`cost_usd_priced_part`: 0.0009), so an unpriced pilot cannot count as free. The same transcript without the unknown
  turn reports `cost_usd: 0.0009`. Every pilot row below has `unpriced_turns: 0`.
- Each reviewer was a fresh subagent on the Sonnet tier, dispatched with a medium-effort brief ("do not over-explore").
  The host offers no effort parameter to a dispatched subagent, so the effort is requested in the prompt and was not
  verified. One pass is shallower than the full review plan describes: there were no seeded defects (no real seed set
  exists, so recall is not measured), no second-vendor pass, and no re-check against current main. A pilot reviewer was
  not required to cite every file of its unit, and the A01 reviewer reported it had not reviewed the 18-line `hooks/_py.sh`. A01's per-line figures still divide by all 2,267 lines, which understates its rate by under 1%.
- **Processed tokens** = input + cache reads + cache writes + output, the plan's unit ("input, including cache reads,
  plus output"), with cache writes counted because they are input the model processed. Without cache writes each total
  falls by the cache-write column.
- Dollars are the list prices of the rate card, not a negotiated rate. Wall time is first to last transcript timestamp.
- Transcripts are identified by agent id (the meter was given each transcript by exact path inside the host's session
  store; the local path is not recorded here). The raw meter output and a ledger line per pilot are kept outside the
  repository in the operator's pilot folder.
- Review findings are not part of this record and were not filed: a pilot reviewer's findings are sample output, not a
  review of those units.

## P-a: high-risk units (Tier A)

| Run | Unit (lines) | Tokens in | Tokens out | Cache read | Cache write | Processed | Cache-read share | Dollars | Wall time | Agent id |
|---|---|---|---|---|---|---|---|---|---|---|
| review | A01 (2,267) | 14 | 24,584 | 605,333 | 104,599 | 734,530 | 82.4% | $0.628 | 208 s | a81b1dade9dae1a85 |
| verification of its 21 findings | A01 | 22 | 6,792 | 854,876 | 70,561 | 932,251 | 91.7% | $0.415 | 65 s | aab3017dcbdb0754e |
| review (extra unit, see note) | A06 (2,997) | 18 | 14,361 | 1,122,897 | 126,402 | 1,263,678 | 88.9% | $0.684 | 127 s | a1708ef0d6ab501a5 |

A01 reviewed and verified: 1,666,781 processed tokens, $1.044. Per line: review 324 tokens (A01) and 422 (A06),
411 for A01's verification. A06 was added as a second high-risk data point because A01 and B01 differ by a factor of two
per line; its findings were not verified, so no verification cost exists for it. The verification cost scales with the
number of claims (21 for A01), not with the lines.

## P-b: sampled unit (Tier B)

| Run | Unit (lines) | Tokens in | Tokens out | Cache read | Cache write | Processed | Cache-read share | Dollars | Wall time | Agent id |
|---|---|---|---|---|---|---|---|---|---|---|
| review | B01 (2,975) | 8 | 14,244 | 318,732 | 118,195 | 451,179 | 70.6% | $0.502 | 116 s | a1b31512c92471c55 |
| verification of its 18 findings | B01 | 14 | 5,923 | 450,792 | 48,826 | 505,555 | 89.2% | $0.271 | 57 s | a0f47bb2cccc5d193 |

B01 reviewed and verified: 956,734 processed tokens, $0.773. Per line: review 152 tokens, verification 170.
B01 is one file (`doctor.py`, 2,975 lines of its 4,519), so this is one sample of Tier B, not a Tier B mean.

## P-c: one live goal

**Not measured: no recorded phase costs exist.** `docs/onboarding-control.md` records no cost ("Tokens and cost: N/A"
for the script-level control, and the live-model run is still the open goal #300). Running the `examples/hello-sdlc`
goal once would need a live model session driven from outside this loop, which AGENTS.md and the issue's guardrail 6
forbid (no unattended `claude -p` from the loop), and an isolated profile that does not touch the real home or plugin
directories is not available to a dispatched slot. Nothing here substitutes for it: the pilot reviewers above are not an
onboarding run. The owner or #300 supplies it: one live `/agrim-loop` run of `examples/hello-sdlc` in an isolated
profile, with the `phase_report.py end` cost lines recorded in `docs/onboarding-control.md`.

## P-d: benchmark pilot

**Not measured: the operator-supplied isolation launcher is missing.**

- The harness (`evals/bench/bench.py`) refuses a live arm without an operator-supplied isolation launcher (an
  absolute executable path it only location-checks). No launcher is committed, none was invented here, and no
  `claude -p` was run outside the harness. This alone stops the pilot.
- The task set (#355) is on main as a draft (`evals/bench/tasks/manifest.json`, `frozen` false): four internal
  non-trap tasks exist, the three trap slots are empty (they awaited an outside author; since 2026-10-05 an independent agent authored them and the set is frozen, see
  `docs/bench/preregistration.md` Deviations), so at that time the pre-registered task set was
  not complete. It landed while this goal ran: the first check, early on 2026-10-04, found it absent.

To measure it the owner supplies the isolation launcher; then run the harness on two of the internal tasks, one run
per arm, with `--max-usd` set to what remains of the pilot ceiling (superseded 2026-10-06: the harness now takes `--max-tokens`, see `docs/bench/token-budget.md`). **The benchmark ceiling is therefore unmeasured**,
and S9 stays an owner-set ceiling with no measured basis.

## Spend against the ceiling

The owner's pilot ceiling is $75 on cumulative pilot spend, and, by the owner's decision of 2026-10-04, pilot tokens
count toward both the 40M checkpoint and the 75M cap of the full review.

| | Processed tokens | Dollars |
|---|---|---|
| P-a (three runs above) | 2,930,459 | $1.728 |
| P-b (two runs above) | 956,734 | $0.773 |
| P-c, P-d | not measured | not measured |
| **Cumulative** | **3,887,193** | **$2.501** |
| Ceiling | 75M cap, 40M checkpoint | $75 (hard stop) |
| Remaining | 36,112,807 under the checkpoint; 71,112,807 under the cap | $72.50 |

The review governance of this goal's own pull request (plan review, code review, post-PR review) is ordinary loop
spend, not pilot spend, and is not in the table.

## What the numbers do not show

- One run per unit: no variance is measured, and A01, A06 and B01 differ by up to 2.8x per line. A per-line figure
  carries that spread.
- Reviewers read at medium effort in one pass with no seeds. A review that plants defects, re-checks against main and
  adds a second vendor will cost more per line than these passes; by how much is unmeasured.
- A verification pass was run for two units only. The ceilings in the review plan apply A01's per-line verification
  rate to every Tier A line and B01's to every Tier B line, which is an extrapolation across units and is labelled so
  there.

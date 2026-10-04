# S4 high-risk review, first wave (#587)

Evidence only. **No dimension is scored** and `docs/launch/scorecard.json` is untouched. Machine-readable record: [review-s4-c3faf6f23e12.json](review-s4-c3faf6f23e12.json). Units are those of `docs/launch/review-units.json` (cut at `c3faf6f23e12`).

## Result

- **4 of 16 Tier A units reviewed** (10,093 lines): A01, A09, A12, A14. Not reached (12): A02, A03, A04, A05, A06, A07, A08, A10, A11, A13, A15, A16. The Tier B sample was not reviewed (owner decision).
- Findings: 27 reported, 25 verified by a different fresh reviewer, 2 dropped (A14 F2, A12 F4).
- Filed (queued, nothing promoted): one `launch:blocker` candidate, class B2 (the commit-time secret scan fails open under some git diff settings and for filenames with spaces), and three grouped `launch:next` issues. Issue numbers are in the JSON.
- Cost, measured from each transcript with `evals/bench/meter.py`: reviewers and verifiers 6,833,669 processed tokens ($3.448), 677.1 tokens per line for review plus verification. This goal slot's own overhead through writing this evidence: 3,539,083 tokens ($1.179); later governance (plan review, code reviews, CI) is not in that figure.
- Counters through this evidence: 38,435,581 of the 40M checkpoint (1,564,419 remaining) and of the 75M cap (36,564,419 remaining).
- **Stop reason: checkpoint reserve.** The run stopped before a second wave because the remaining headroom would not cover another wave and this goal's own governance. Continuing past the checkpoint is the owner's decision.

## Per unit

| Unit | Lines | Status | Found | Verified | Dropped | Tokens | Dollars |
|---|---:|---|---:|---:|---:|---:|---:|
| A01 | 2,293 | reviewed | 8 | 8 | 0 | 1,369,139 | $0.783 |
| A02 | 1,894 | not reached (checkpoint reserve) | - | - | - | - | - |
| A03 | 2,741 | not reached (checkpoint reserve) | - | - | - | - | - |
| A04 | 2,507 | not reached (checkpoint reserve) | - | - | - | - | - |
| A05 | 2,983 | not reached (checkpoint reserve) | - | - | - | - | - |
| A06 | 2,988 | not reached (checkpoint reserve) | - | - | - | - | - |
| A07 | 602 | not reached (checkpoint reserve) | - | - | - | - | - |
| A08 | 1,687 | not reached (checkpoint reserve) | - | - | - | - | - |
| A09 | 1,981 | reviewed | 4 | 4 | 0 | 1,754,601 | $0.799 |
| A10 | 2,984 | not reached (checkpoint reserve) | - | - | - | - | - |
| A11 | 2,209 | not reached (checkpoint reserve) | - | - | - | - | - |
| A12 | 2,846 | reviewed | 10 | 9 | 1 | 1,824,732 | $0.984 |
| A13 | 1,972 | not reached (checkpoint reserve) | - | - | - | - | - |
| A14 | 2,973 | reviewed | 5 | 4 | 1 | 1,885,197 | $0.882 |
| A15 | 2,956 | not reached (checkpoint reserve) | - | - | - | - | - |
| A16 | 903 | not reached (checkpoint reserve) | - | - | - | - | - |

## What this does not cover

Single shallow pass per unit with no seeded defects and no second vendor, so recall is unmeasured; the reviewers' medium effort was requested in the prompt and not verified. Most verdicts are file and line citations; a few were reproduced by running code. Findings are not fixed here.

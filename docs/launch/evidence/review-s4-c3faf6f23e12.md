# S4 high-risk review (#587)

Evidence only. **No dimension is scored** and `docs/launch/scorecard.json` is untouched. Machine-readable record: [review-s4-c3faf6f23e12.json](review-s4-c3faf6f23e12.json). Units are those of `docs/launch/review-units.json` (cut at `c3faf6f23e12`).

## Result

- **16 of 16 Tier A units reviewed** (36,519 lines). Wave 1 (2026-10-04): A01, A09, A12, A14. Wave 2 (2026-10-05, after the owner released the run past the 40M checkpoint to the 75M cap): A02-A08, A10, A11, A13, A15, A16. The Tier B sample was not reviewed (owner decision).
- Findings: 82 reported, 69 verified by a different fresh reviewer, 13 dropped. Wave 2 alone: 55 reported, 44 verified, 11 dropped.
- Filed (queued, nothing promoted when filed; #589 has since been fixed and closed): four `launch:blocker` candidates in all (wave 1: #589, class B2; wave 2: #625 class B1 init overwrites a corrupt config, #629 class B2 the scrubber misses env-style secrets, #635 class B8 the review gate honours approval comments from any commenter), the rest grouped `launch:next` issues. Numbers are in the JSON. Each blocker issue states its reach so the owner can reclassify.
- Cost, measured from each transcript with `evals/bench/meter.py`: wave 2 reviewers and verifiers 18,011,488 processed tokens ($8.922); this slot's own overhead for wave 2 4,052,407 tokens ($1.345) at the time this file was written. All reviewers and verifiers together 24,845,157 ($12.37), 680.3 tokens per line for review plus verification.
- Counters: 47,369,450 after wave 1, plus wave 2 22,063,895 = **69,433,345 cumulative**, which is 29,433,345 over the 40M checkpoint and 5,566,655 under the 75M cap. Not included: this slot's later turns and the evidence PR's governance reviewers.
- Stop reason: all 16 Tier A units reviewed, inside the 75M cap.

## Per unit

| Unit | Lines | Found | Verified | Dropped | Tokens | Dollars |
|---|---:|---:|---:|---:|---:|---:|
| A01 | 2,293 | 8 | 8 | 0 | 1,369,139 | $0.783 |
| A02 | 1,894 | 3 | 3 | 0 | 1,794,861 | $0.76 |
| A03 | 2,741 | 3 | 3 | 0 | 1,505,157 | $0.763 |
| A04 | 2,507 | 8 | 5 | 3 | 1,814,245 | $0.937 |
| A05 | 2,983 | 3 | 2 | 1 | 1,831,466 | $0.904 |
| A06 | 2,988 | 4 | 3 | 1 | 1,658,483 | $0.792 |
| A07 | 602 | 2 | 1 | 1 | 843,092 | $0.419 |
| A08 | 1,687 | 9 | 8 | 1 | 1,272,016 | $0.705 |
| A09 | 1,981 | 4 | 4 | 0 | 1,754,601 | $0.799 |
| A10 | 2,984 | 3 | 2 | 1 | 1,180,969 | $0.659 |
| A11 | 2,209 | 5 | 3 | 2 | 1,316,579 | $0.712 |
| A12 | 2,846 | 10 | 9 | 1 | 1,824,732 | $0.984 |
| A13 | 1,972 | 6 | 6 | 0 | 1,587,444 | $0.759 |
| A14 | 2,973 | 5 | 4 | 1 | 1,885,197 | $0.882 |
| A15 | 2,956 | 6 | 5 | 1 | 2,151,629 | $1.006 |
| A16 | 903 | 3 | 3 | 0 | 1,055,547 | $0.506 |

## What this does not cover

Single shallow pass per unit with no seeded defects and no second vendor, so recall is unmeasured; the reviewers' medium effort was requested in the prompt and not verified. Most verdicts are file and line citations; a few were reproduced by running code. Blocker classes are a slot judgement for the owner to confirm. Findings are not fixed here.

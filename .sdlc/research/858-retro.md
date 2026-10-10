# #858 retro (advisory, independent)

Grade: achieved

Each acceptance line has a shipped artifact: validation, four lease-guarded verbs on one `_push` with the explicit
`--force-with-lease=<ref>:<sha>` form, porcelain-based classification, `observed_age`, a staged-stale-view control,
a labelled smoke test, and a recorded fetch measurement (`.sdlc/research/858-measurements.md`). Controls C-1..C-4e
were run on the documented gesture and seen red.

## Residual debt
1. `_push` has no `--no-verify`; a caller's pre-push hook runs on every claim push (latency, or a hook decline read as REFUSED). Add it or document it.
2. A create-loser on a ref lock can read REFUSED, not LOST_RACE. Docstring must say callers treat REFUSED-on-create as re-read-then-retry.
3. Measured cost is local-path transport only; network latency and hosting ref limits are unmeasured (slice 1 spike). Scale ceiling at 1000+ refs is not a forecast.
4. The 30-minute lease attribution (PRD versus namespace-fallback label) remains unverified; `lease_seconds` is caller-supplied, so no default exists to be wrong.
5. `observed_age` needs the caller to persist `seen`; durability and clock choice are a downstream slice's burden.
6. Module has no caller yet: library-only, so liveness and recovery are unproven end to end.

## Lessons
Classify by measurement plus a post-push tip read; keep one `git push` per function for the write-surface ratchet.

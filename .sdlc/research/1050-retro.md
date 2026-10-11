# #1050 retro
- Plan-review prototype found a real leak: `comment_watch._excerpt` cut the raw body at 160 before the ledger scrubbed, leaving a fragment of a straddling secret (6 shapes: aws, jwt, google, npm, gitlab, url-password). Fixed by flatten, scrub, then cut. The new straddle probe is the control (seen red against the old code).
- Sink probes live in `evals/safety/sinks.py` so `check.py` stays write-free; scratch dirs only.
- Probe lesson: a placeholder past a cap is cut off, so the placeholder is asserted only on the start planting.
- Limit: `post_review` is probed by its scrub handle plus a source pin, not run (needs generation-bound evidence).
- Open owner ruling (#806/#1058): the `drop-redactor-only` mutator default (authorization-header) is not decided here.

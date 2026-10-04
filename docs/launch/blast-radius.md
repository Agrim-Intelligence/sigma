# Autonomy blast-radius drill (readiness D10, blocker class B7)

Sigma's scripted end-to-end flow is run against a real, private, throwaway repository named
`OWNER/sigma-drill-NAME` (no branch protection, one seeded issue that is not a goal). Everything the flow
writes is captured, and five assertions say whether it stayed inside `docs/launch/write-surface.json`.
No model is called. The repository is created and deleted by the owner by hand; no tool here does either.

Assertions: A1 default branch moved only through the goal's merged PR (run `always`: one merge, run `off`: none;
no push outside the default branch and `sdlc/*`); A2 no force-push to the default branch; A3 only `sdlc/*`
branches deleted; A4 the unrelated issue untouched; A5 every `gh`/`git` launch is a known read or an allowed
write with an inventory rule, aimed at the drill repository only.

Run, once per `--run` value (`always`, then `off`), from a checkout of Sigma, with `SCRATCH` outside the repository.
The `drive` line runs wrapped by the egress hook, `egress_capture.py run --log LOG -- <the drive line>`, and `LOG`
is the capture `assert` reads:

```
python3 tools/readiness/blast_radius_drive.py drive OWNER/sigma-drill-NAME --run always --workdir SCRATCH/run1 --baseline-out SCRATCH/baseline1.json
python3 tools/readiness/blast_radius.py assert OWNER/sigma-drill-NAME --capture LOG --inventory docs/launch/write-surface.json --run always --baseline BASELINE --json OUT
```

`assert` exits 0 (all pass), 1 (a failure), 2 (refused) or 3 (nothing failed but GitHub's events feed has not caught
up: unverified, rerun later, never a pass). `--baseline` is required: the empty repository needs a setup push and
the seeded issue already carries creation events, so both are named rather than guessed.

Control: point `--inventory` at a copy with every `gh-pr` entry removed; A5 must fail on the real run-1 capture.

What it does not show: a scripted flow is not a model-driven run. The capture sees Python `subprocess` launches
only, so A1 to A4 are measured from GitHub's REST feeds, not from the capture. GitHub's repo events feed can lag
and carries no `forced` flag on a push, so A2 uses the REST compare of `before...head`. A5 compares rule
categories plus verbs and targets, not code sites; a write site missing from the inventory is
`tools/readiness/write_surface.py check`'s job.

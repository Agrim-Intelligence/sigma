# Regression control (Layer 2)

- `catalogue.json`: planted regressions. Each row names a mutation of Sigma itself and the gates (`must_be_red_by`) that must catch it.
- `gates.json`: the gate registry. A gate is `live` (a local command) or `pending` (registered, not runnable yet).
- `matrix.json`: the evidence. One entry per catalogue (row, gate) pair: `red` with `run_id`, `date`, `commit`, `pin`, or `pending` for a pending gate.

## Commands

```
python3 evals/regression/control.py run                  # mutate each row, demand its live gates go red; writes nothing
python3 evals/regression/control.py run --record         # same, then merge what it saw into matrix.json
python3 evals/regression/matrix.py check                 # validate matrix.json against the catalogue and gates
```

Entry shape: `{"row": "<id>", "gate": "<gate id>", "status": "red|pending", "run_id": "", "date": "YYYY-MM-DD", "commit": "<40 hex>", "pin": "<16 hex>"}`.
`pin` is the first 16 hex of sha256 over the row's regression, form, mutation and the gate's argv at record time.

## What a record does and does not prove

- A recorded red proves the gate went red once, at that commit. A record is a claim, not proof.
- A hand-edited row looks like a recorded one. Only `check`'s pin and commit tests catch it. The pin detects re-pointing of
  the row or gate; it does not detect drift in the mutator's or the test's content.
- The live check is `control.py run` on CI. NO workflow runs it yet, so today it is operator-run.
- `--skip-objects` and a shallow clone skip the commit-existence checks and print UNVERIFIED. That is not a pass.
  Run `matrix.py check` on a full clone for the real answer.

## Recording across a squash merge

Branch commits vanish when a PR is squash-merged, so evidence recorded on a branch head would point at nothing. Record against `origin/main`:

```
git worktree add --detach <dir> origin/main
python3 evals/regression/control.py run --record --repo <dir> --matrix evals/regression/matrix.json
```

`--matrix` defaults to this tree's file, never `--repo`'s, so the explicit flag is how a throwaway checkout writes here.
A PR that adds a catalogue row records that row on its own head so `check` passes before merge; after the squash the owner re-records on main.

## Failure modes

- A run that exits nonzero still records the reds it saw; an unseen pair has no entry, and `check` then fails with `missing-row`.
  A gate seen green, vacuous, broken or timed out has its old entry removed.
- Concurrent `--record` is last-writer-wins. Run it serially.
- A SIGKILL mid-write can leave `evals/regression/matrix.json.tmp.<pid>` (git-ignored); `matrix.json` itself is never half-written.
- Nothing is recorded when the baseline is red or the run is refused.

## Cost

Serial: about 80 s for the live set on one laptop (not measured on CI), one tree copy per row. `check` is O(rows) with one git call per distinct commit.

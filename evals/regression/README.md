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
- The live check is `control.py run` on CI: the `regression-control` job runs the rows a PR's changed files select, and every row nightly (`evals/README.md`). It does not `--record`; recording stays operator-run.
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

## Live entrypoint

`live.py` is the one place the regression eval may start a paid model run. With no subcommand it only refuses; with `run` it
makes ONE capped `claude -p` run through an operator-installed launcher, books its dollar belt in a spend ledger first, settles
it from the host's own transcript, and writes one result row.

**What is measured and what is not.** No real `claude` and no real launcher has ever been run through `run` in this change: every
test drives a FAKE launcher and a FAKE `claude`. The tests prove our argv, environment, ordering, ledger and row behaviour against
a model of the launcher. They do not prove that the real launcher, the real `claude` and a real subscription token work together,
that a real exit 97 arrives as modelled, or what a real run costs; the first real use may surface launcher defects (separate
launcher goals). Windows is unmeasured until the `windows.yml` workflow is dispatched by hand. Dollar figures from a subscription
are indicative (tokens priced at list rates), never a bill. No performance claim is made: per run there is one ledger reserve, one
ledger settle (about 4 ms each, measured by the ledger goal, not re-measured here) and one launcher `--dry-run` (bounded by the
helpers' version timeout; its real duration is unmeasured).

### Gestures

The refusals, exactly as written (each exits 2, prints nothing on stdout, writes one NOT RUN row under `.sdlc/eval/results`):

```
env -i PATH="$PATH" HOME="$HOME" python3 evals/regression/live.py  # REFUSED [no-credential]
env -i PATH="$PATH" HOME="$HOME" SIGMA_REGRESSION_LIVE=1 CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN python3 evals/regression/live.py  # REFUSED [no-cap]
```

The second needs your token already exported in the shell (its value is never typed on a command line, in a file or in shell
history). The run gesture, with placeholders for paths only (the credential is read from `CLAUDE_CODE_OAUTH_TOKEN`, a NAME here, never a value):

```
SIGMA_REGRESSION_LIVE=1 SIGMA_REGRESSION_CAP_USD=5 python3 evals/regression/live.py run --prompt-file PROMPT --workdir DIR --model MODEL --permission-mode MODE --launcher LAUNCHER --claude CLAUDE --timeout-s SECONDS [--belt-usd N] [--plugin-dir DIR] [--ledger-dir DIR] [--commit SHA40] [--cell NAME]
```

`--timeout-s` is required and has no default: it is an operator value, and machine-derived limits are a later slice. `--commit` (40 hex)
and `--cell` (letters, digits and `._:/@+-`, up to 80) are recorded in the row; `--commit` wins over `GITHUB_SHA`, which is used only when
`--commit` is absent. Plugin export is not done here: pass an already-exported `--plugin-dir`.

### The ladder

Every rung is evaluated on every invocation; stderr and the row's `code` carry the first failing code and `also_failing` lists the rest:
`no-credential`, `no-opt-in`, `no-cap`, `unsupported-platform`, `pull-request-event`, `subscription-in-gated-mode`; a clean ladder
with no `run` ends in `no-run-requested` (exit 2, never a silent exit 0). Other refusals: `bad-usage` (fixed text, never echoes the
argument), `scrubber-unavailable`. Run-only refusals, evaluated in this order and only after the pure ladder is clean:
`launcher-api-key-unsupported` (an API key is set: the launcher accepts only `CLAUDE_CODE_OAUTH_TOKEN`; API-key mode is goal 831),
`run-refused` (a validated input failed; the row names the stage: `prompt-file`, `workdir`, `launcher-path`, `claude-path`, `model`,
`permission-mode`, `timeout`, `commit`, `cell`, `plugin-dir`, or `start`), `launcher-precondition`, `launcher-latch`, `spend-ledger`,
`run-signalled`, `run-error` (an unexpected failure; the row names only the exception type). Because the launcher check spawns the
launcher, it runs only when nothing earlier refused, so `also_failing` never lists `launcher-*` codes when an earlier rung failed.

### The launcher

Install a SECOND copy of the launcher for this entrypoint, with its own JSON beside it (the file the launcher derives from its own
resolved path) and its own `scratch_root`, hence its own latch. `live.py` reads that JSON and refuses `launcher-precondition` unless
`parent_marker` is `"live.py"` (absent means the launcher's default `bench.py`, so absent refuses), `credential_var` is
`CLAUDE_CODE_OAUTH_TOKEN`, `scratch_root` and `claude_path` are absolute, the `--claude` you pass resolves to the same program as
`claude_path`, and `scratch_root` neither lies inside nor contains the system temp directory (the real launcher refuses that overlap
too). Then the launcher's own `--dry-run -- claude --version` must exit 0 within the helpers' version timeout; `live.py` checks only that the
launcher path is absolute and executable and relies on that dry run for everything else. A stdout or stderr message from the launcher is never echoed:
the row carries the exit status only. Copy-pasting one config into two launchers that share a `scratch_root` shares a latch, which
`live.py` cannot detect.

The launcher's primary stop mechanism is to SIGTERM its parent, which is `live.py` (that is what `parent_marker` names); invoked under
another file name, `live.py` warns on stderr. The exit-97 and latch-file checks are the FALLBACK. A present latch, an exit 97, or a
latch file appearing during the run stops the run with `launcher-latch` and a NOT RUN row. `live.py` never deletes the latch: a human
inspects it and deletes it by hand.

### The spend ledger

The ledger lives at `~/.sigma/eval/ledger` by default (per USER, so every worktree and tree copy on this machine shares one cap;
override with `--ledger-dir`). The cap is per ledger home, per machine: two machines, or two homes, each believe they hold the whole
cap, and a lost hosted runner forgets the belt (job-level reservation is goal 819). The belt defaults to the cap; the ledger clamps it
to what is left. Concurrent runs are allowed and cap-clamped: a second run's reserve is reduced by the first run's open belt, and there
is no run lock. The belt is booked (fsynced) before the launcher starts and passed rounded DOWN to six decimals as `--max-budget-usd`.
After the run the reservation is settled at the metered spend rounded up, or at the FULL belt when the spend is unmeasured (unpriced
model, no transcript): unmeasured is never free. A SIGTERM or SIGHUP settles at the full belt and exits 128 plus the signal number; a
SIGKILL leaves the open row counted at its full belt. The lever, once you know the run is dead:

```
python3 -c "import sys; sys.path.insert(0, 'evals/regression'); import spend_ledger as s; s.settle_orphan('LEDGER_DIR', 'YYYY-MM', 'RUN_ID', '0')"
```

`run-refused ... start` and a failed settle also leave the belt counted until that lever or the ledger's own full-belt settle runs.

### Credentials

Only `CLAUDE_CODE_OAUTH_TOKEN` reaches the launcher, plus `PATH`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ`, `TERM` and the profile variables the
helpers set; nothing else of your environment is forwarded, and the precheck carries no credential at all. Every printed line and
every row passes an exact-value redaction (raw, quoted, url-quoted, shell-quoted, base64). Residual: the agent's own subprocesses may
read the credential from `/proc/<pid>/environ` (see the launcher README).

### The row

`sigma.regression-result/v1`, additive fields only: `ledger_id`, `spent`, `exit_code`, `timed_out` beside the existing ones. `status` is
`NOT RUN` (nothing was judged: a refusal, a trip, a signal), `RAN` (the model run exited cleanly and was measured) or `RUN-FAILED`
(non-zero exit, timeout, throttled, unmeasured, or a failed settle). `code` is `ran`, `exit-nonzero`, `timed-out`, `throttled`,
`unmeasured` or `settle-failed` for these. RAN is never GREEN: `live.py` is the spawner, not the judge, and any status that is not GREEN is
not a pass. The verdict statuses belong to the layer rows (goals 812, 816, 819, 820), which must handle the two new values. `spent` is the
ledger's settled amount and is authoritative; a NOT RUN row that was settled also carries it as `cost` with `cost_basis` `settled-at-full-belt`
(or `indicative` when metered), so a reader summing `cost` never misses a charge, while an unmeasured RUN-FAILED row has `cost` null and only `spent`. A row
with a `ledger_id` but no `spent` was not settled by the row writer: the ledger settled it at the full belt (signal or exit path).

Exit codes: RAN 0; RUN-FAILED 1 (also a RAN whose row could not be written); every refusal and NOT RUN 2; a signal stop 128 plus the
signal number (143 for SIGTERM).

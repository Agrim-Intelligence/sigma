# Flake census

The launch threshold for correctness is "0 flaky tests" (readiness issue #337). This page is how that is
measured: run the full suite ten times on each of Linux and macOS, keep every run's JUnit file, and
classify each test by what it did across the runs. The first census is recorded in
[Recorded result](#recorded-result).

## What is measured

One run is one full `pytest tests/` with no retries and no rerun plugin. Each test is identified by its
node id (`classname::name`; a pytest collection error has no classname and is identified by its name).
Across the ten runs:

| Class | Meaning |
|---|---|
| `flaky` | passed in at least one run and failed or errored in at least one |
| `always_failing` | failed in every run it appears in: a deterministic failure, reported separately and not flaky |
| `skipped_only` | skipped in every run it appears in |
| `mixed_nonpassing` | never passed, but both failed and was skipped, so no other class can hold it |
| `missing` | absent from some run (an overlay, not a partition member) |

`flaky`, `always_failing`, `skipped_only`, `mixed_nonpassing` and the clean tests partition the node set;
`totals` proves it by summing to `tests`. `missing` is an overlay: a test that passed, failed and was absent
from a third run is in both `flaky` and `missing`, on purpose. A test that passes in some runs and is skipped
in others is counted clean, not flaky. `per_run` lists each run's test, failure and skip counts, so a run
that collected almost nothing is visible; an intermittent collection error shows up as that run's tiny
count and a flood of `missing`.

## What it cannot show

Ten runs observe instability; they cannot prove zero flaky tests. By arithmetic, not measurement, a test
that fails 1 run in 20 passes all ten runs about 60 percent of the time, so "none observed in 10 runs" is a
weaker claim than "0 flaky". `combine` takes the commit hash on trust: both halves must have been run on that
commit. The Linux half runs Python 3.12 only (the launch definition lists 3.10 to 3.13), single-process; the
macOS half runs `-n 4` (xdist), so ordering effects differ between the two halves. Only the full suite on the commit under test is observed.

## Cost of one Linux dispatch

The Linux half is ten parallel matrix jobs on `ubuntu-latest`. A full CI leg takes about 15 to 21 minutes,
so one dispatch is about 150 to 210 Linux runner-minutes (each job rounds up to a whole minute). That is an
estimate from the CI legs. The first real dispatch measured more: its ten jobs summed to about 354
job-minutes (each job about 35 minutes), so budget about 360 runner-minutes, not 150 to 210. The workflow is `workflow_dispatch` only: it
never runs on a push, a pull request or a schedule, so nothing consumes the quota unless an operator
dispatches it. macOS runs on the operator's own machine and uses no Actions minutes.

A red job in a dispatched run is expected data, not a failed census: the artifact is uploaded whether the
suite passed or failed, and `aggregate` classifies what it holds. The workflow cannot tell a crashed pytest
from a failing suite, though `run` can, so check `per_run` in the aggregate: a job that crashed leaves a
partial XML that shows as a tiny count and a flood of `missing`. Re-dispatch rather than aggregate it.

## The gestures

Linux (a GitHub write the operator chooses to make; it needs the workflow on the default branch):

```sh
gh workflow run flake-census.yml
gh run list --workflow flake-census.yml --limit 1
gh run download <run-id> -D ~/.sigma-ops/flake-census/linux
python3 tools/readiness/flake_census.py aggregate ~/.sigma-ops/flake-census/linux --os linux --json ~/.sigma-ops/flake-census/linux.json --meta python=3.12 --meta runner=ubuntu-latest
```

The download directory must hold only those ten artifacts: every `*.xml` in it counts as a run, and
`combine` refuses a section that does not have exactly ten.

macOS, with the repository's verify interpreter, into an empty directory:

```sh
$HOME/.sigma-venv312/bin/python tools/readiness/flake_census.py run . --runs 10 --python $HOME/.sigma-venv312/bin/python --out ~/.sigma-ops/flake-census/macos-junit -- -n 4 -p no:cacheprovider
python3 tools/readiness/flake_census.py aggregate ~/.sigma-ops/flake-census/macos-junit --os macos --json ~/.sigma-ops/flake-census/macos.json --meta python=3.12
```

`run` refuses to overwrite an earlier observation. If it is interrupted after run 6, delete the partial
`junit-6.xml` and continue with `--runs 5 --start 6` into the same directory. It also refuses retry,
selection and ordering arguments (`--reruns`, `-k`, `--lf`, `-x`, a test path, and abbreviations of the
long options), on a best-effort list rather than a proof (it does not read `addopts` in a config file), a `--junitxml` of its own,
and any non-empty `PYTEST_ADDOPTS` or a `PYTEST_PLUGINS` naming a retry plugin. A suite that fails is data,
so `run` exits 0 when every run completed (pytest exit code 0 or 1) and wrote its XML. It exits 1 when a run
wrote none or ended abnormally (pytest exit code 2 to 5: interrupted, crashed, misused or collected
nothing), whose partial XML must not be aggregated.

Compose the one evidence document, naming the full commit that was measured:

```sh
python3 tools/readiness/flake_census.py combine ~/.sigma-ops/flake-census/linux.json ~/.sigma-ops/flake-census/macos.json --sha <full-commit-sha> --json docs/launch/evidence/flake-<sha12>.json
```

Exit codes for `aggregate` and `combine`: 0 no flaky test, 1 at least one flaky test, 2 bad input (not XML,
an XML with no test cases, two byte-identical files, a section that is not exactly ten runs, a `--meta`
value that looks like a host path). Evidence files are created exclusively and never replace an earlier one.

## Control

The classification has been seen to fail. The fixtures in `tests/fixtures/flake_census/` are three
hand-made runs: test `a` always passes, `b` always fails, `c` passes twice and fails once, `d` is absent
from one run. This gesture must report `c` as the only flaky test, `b` as always failing and `d` as missing,
and exit 1:

```sh
python3 tools/readiness/flake_census.py aggregate tests/fixtures/flake_census --os linux --json <out.json>
```

To see the control red, change the `flaky` rule in `aggregate` to "failed at least once" only and run
`python3 -m pytest tests/test_readiness_flake_census.py -q`, then the gesture above: `b` is wrongly reported
as flaky and the tests fail. Restore the rule afterwards.

## Recorded result

Evidence: `docs/launch/evidence/flake-c82e3dfa7420.json`, composed by `combine` from two independent
censuses of commit `c82e3dfa7420e92465a7cf991a1dcf5166403973`. The commit is operator-asserted (`combine`
takes it on trust): the Linux runs are Actions run 37187467571, whose head commit was read back from the run
over REST, and the macOS runs were made in a detached checkout of that same commit.

| | Linux | macOS |
|---|---|---|
| Runs | 10 | 10 |
| How | dispatched workflow, ten parallel jobs, Python 3.12, one process | local, `-n 4` (xdist), verify interpreter, Python 3.12 |
| Test cases per run | 12,170 in every run | 12,170 in every run |
| `flaky` | none | none |
| `always_failing` | none | none |
| `mixed_nonpassing` | none | none |
| `missing` | none | none |
| `skipped_only` | 38 | 31 |

**None observed in 10 runs on each platform.** That is all it says: ten runs cannot prove zero flaky
tests, only that none showed instability in these twenty observations of this commit, on Python 3.12, in
these two process models. Every run reported the same test count, so none was hollow, and every macOS `run`
exited with pytest code 0. The skipped-only tests are skipped on every run of their platform; 7 of the Linux
ones run on macOS (a difference in the environment, such as an optional package, not investigated here). No unstable or always-failing test
was found, so no issue was filed for a defect. The Python 3.10, 3.11 and 3.13 legs and macOS on those
versions were not run.

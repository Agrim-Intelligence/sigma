# Coverage: what is measured, the floor, and what is not

Issue #194 (readiness dimension D8, blocker class B3: a README claim false on a supported cell). The README
once said CI ran "with an 85% coverage floor"; CI measured nothing. #277 removed that claim. This page is the
measurement that replaced it. The machine-readable record is `docs/launch/coverage.json`; the README states
two figures from it, and `tests/test_coverage_record.py` fails if the README, the record, `.coveragerc` or the
CI step disagree.

## The measurement

Tool and versions: coverage.py 7.16.2, pytest 9.1.1, Python 3.12.13, in a throwaway virtual environment outside
the repository (coverage is a test-only tool; nothing under `skills/` or `hooks/` imports it). Machine: Darwin
25.6.0 arm64, one local developer machine with two other goal slots running, so wall times are loaded-machine
times. Revision: `a23f6c6` (origin/main when the goal started). Scope: `skills/` and `hooks/`; line coverage,
not branch. The commands, in order:

```
python3.12 -m venv <scratch>/venv && <scratch>/venv/bin/pip install pytest coverage
python -m coverage erase
python -m coverage run -m pytest tests/ -q
python -m coverage combine
python -m coverage report
```

with the venv's `bin` first on `PATH` so the `python`/`python3` that tests spawn is the same interpreter, and the
configuration in `.coveragerc` (`source = skills, hooks`, `patch = subprocess`, `parallel = True`; the first run
used the same options with absolute paths in a scratch rcfile).

Result: **90.91%** line coverage, 37,806 statements, 3,436 missed (34,370 covered; `coverage json` totals). The
suite: 11,569 passed, 24 skipped, 2 xfailed, 0 failed, 2,489 s, 780 data files combined.

An earlier attempt is not used: the first run was started under `nohup`, which makes the shell ignore SIGHUP, and
`tests/test_slack_commands_listen.py` correctly failed on that (`SIG_IGN` handler); `-x` stopped it at 80% of a
partial suite. The number above is the second, complete run.

`.coveragerc` uses relative paths. A scratch project confirmed coverage resolves them against the rcfile, so a child
process started from another working directory still attributes to the repository, and a subset (`tests/test_readme_first_run.py`)
gave the same 17,755 statements and 14,491 missed under absolute and relative paths. The full suite was measured with
absolute paths; the relative-path configuration is what CI runs, so the CI log of the gated leg is its check.

## What this number does not measure (it understates)

- Scripts that tests copy to another path run from outside the measured source and are not attributed (6 test
  files copy `.py` files into a temporary directory). Not measured how many lines that hides.
- Children started with a scrubbed environment lose `COVERAGE_PROCESS_START` and are not traced.
  `tests/test_script_help.py` builds its child environment from scratch: that subset measured 0%. Not measured
  across the whole suite.
- `evals/run.py` runs as its own CI step and is not part of this run. Branch coverage was not measured (line coverage only), so branch coverage is unknown.
- This is macOS and Python 3.12 on one machine. Linux, Python 3.10, 3.11 and 3.13 and the hosted macOS runner were
  not measured by this run; the Linux figure is the one the gated CI leg prints.
- Line coverage says a line ran, not that anything checked its result (`mutation.py` makes the same point about kill rate).

The first two gaps can only hide executed lines, so they understate; the others change what is measured, not the
direction. 90.91% is a figure for lines run on one machine, not for how well they are tested.

## The floor

`fail_under = 85` in `.coveragerc`: the whole measured percent (90) minus five points of headroom. The headroom is
there because the Linux skip set differs from macOS and a legitimate change can add an untested script; five points
is about 1,900 statements. It is a floor, not a target, and it is not a number that was never measured: it is below a
number that was. It is enforced on one leg only, ubuntu-latest Python 3.12 (one measurement is enough for a floor,
and the other legs already take 24 to 40 minutes each). `coverage report` exits nonzero under the floor and fails the step.

CI cost, measured: baseline for the gated leg (plain pytest step, ubuntu 3.12) is 1,481 s on GitHub Actions run
37098147702 (step `started_at` to `completed_at`). A red coverage leg blocks the merge until fixed or
the step is reverted; the job timeout is 60 minutes. The cost with coverage is recorded below from the pull
request's own gated leg.

## Refreshing

Rerun the commands above. Put the new `statements`, `missing` and `line_percent` in `docs/launch/coverage.json`, the
same percent (one decimal) in the README sentence, and if the floor moves, `fail_under` in `.coveragerc`, the
record's `floor_percent` and the README together. Then:

```
python -m pytest tests/test_coverage_record.py -q
```

It names the one that disagrees. It does not rerun coverage (a forty-minute suite run); it trusts the record, and
this page says how the record was made.

## Recovery

A coverage failure is a red step on one leg, never a hung job or lost data. Interrupted runs leave `.coverage.*`
files, which are gitignored; `coverage erase` at the start of the step (CI and the commands above) removes them, so a
stale file cannot be merged into the next figure (`combine` alone would merge it). If coverage.py cannot run on a
new Python, only that leg fails: pin a working version in `ci.yml` and the record together, or drop the step and
the README sentence together (the test names both). Nothing in this change runs in the background, sends data or
ships code to users.

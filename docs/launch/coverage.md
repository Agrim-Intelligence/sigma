# Coverage: what is measured, what a Linux trial showed, and what is not enforced

Issue #194 (readiness dimension D8, blocker class B3: a README claim false on a supported cell). The README
once said CI enforced an 85-percent floor on coverage; CI measured nothing. #277 removed that claim. This page is the
measurement that replaced it, and the reason CI still does not gate on coverage. The machine-readable record is
`docs/launch/coverage.json`; the README states one figure from it, and `tests/test_coverage_record.py` fails if the
README and the record disagree, or if CI or the coverage configuration starts enforcing a floor while they say
nothing is enforced.

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
configuration in `.coveragerc` (`source = skills, hooks`, `patch = subprocess`, `parallel = True`; the full run
used the same options with absolute paths in a scratch rcfile).

Result: **90.91%** line coverage, 37,806 statements, 3,436 missed (34,370 covered; `coverage json` totals). The
suite: 11,569 passed, 24 skipped, 2 xfailed, 0 failed, 2,489 s, 780 data files combined.

An earlier attempt is not used: the first run was started under `nohup`, which makes the shell ignore SIGHUP, and
`tests/test_slack_commands_listen.py` correctly failed on that (`SIG_IGN` handler); `-x` stopped it at 80% of a
partial suite. The number above is the second, complete run.

`.coveragerc` uses relative paths. A scratch project confirmed coverage resolves them against the rcfile, so a child
process started from another working directory still attributes to the repository, and a subset (`tests/test_readme_first_run.py`)
gave the same 17,755 statements and 14,491 missed under absolute and relative paths. The relative-path file was not run
over the whole suite.

## What this number does not measure (it understates)

- Scripts that tests copy to another path run from outside the measured source and are not attributed (6 test
  files copy `.py` files into a temporary directory). Not measured how many lines that hides.
- Children started with a scrubbed environment lose `COVERAGE_PROCESS_START` and are not traced.
  `tests/test_script_help.py` builds its child environment from scratch: that subset measured 0%. Not measured
  across the whole suite.
- `evals/run.py` runs as its own CI step and is not part of this run. Branch coverage was not measured (line
  coverage only), so branch coverage is unknown.
- This is macOS and Python 3.12 on one machine. No Linux percentage exists: the one Linux trial (next section)
  stopped before it printed a total. Python 3.10, 3.11 and 3.13 and the hosted macOS runner were not measured.
- Line coverage says a line ran, not that anything checked its result (`mutation.py` makes the same point about kill rate).

The first two gaps can only hide executed lines, so they understate; the others change what is measured, not the
direction. 90.91% is a figure for lines run on one machine, not for how well they are tested.

## The Linux trial, and why CI does not gate on this

This change's own pull request first ran the suite under coverage on the ubuntu-latest Python 3.12 leg (GitHub
Actions run 37105964337, the step `started_at` to `completed_at`; the other four legs ran plain pytest):

- The pytest step took 3,154 s. The same leg's plain pytest step took 1,481 s on run 37098147702 on main, so the
  step was 2.13 times as long (different runs on hosted runners; run-to-run variance was not measured). The whole job
  took 3,166 s against a 3,600 s job timeout, leaving about 7 minutes of slack for the steps after pytest.
- Result: 1 failed, 11,569 passed, 28 skipped, 2 xfailed. The failure is
  `test_real_repository_scan_is_bounded_and_keeps_sources_writer_coverage`, a test that runs the growth audit as a
  subprocess with a 15 s timeout; under tracing it did not finish and was killed. It passes under plain pytest on all
  five legs. Because pytest exited 1, the step stopped before `combine` and `report`, so no Linux percentage exists.

So a per-PR gate on coverage would roughly double its leg, sit within minutes of the job timeout, and fail on a
timing-sensitive test until that test is given tracing-aware headroom. None of that is acceptable on a leg every
change waits for, so CI does not measure or enforce coverage, and the README says so. A floor that would be derived from
this measurement is 85% (the whole measured percent, 90, minus five points: about 1,900 statements of headroom for the
macOS-to-Linux skip difference and the understatement above); it is recorded as `candidate_floor_percent`, not enforced
anywhere, and has never been run on Linux. The queued follow-up is a non-blocking scheduled run that measures the Linux
figure and decides whether a floor is justified.

## Refreshing

Rerun the commands above. Put the new `statements`, `missing` and `line_percent` in `docs/launch/coverage.json` and the
same percent (one decimal) in the README sentence, then:

```
python -m pytest tests/test_coverage_record.py -q
```

It names the one that disagrees. It does not rerun coverage (a forty-minute suite run); it trusts the record, and
this page says how the record was made. If a gate is ever added to CI, update the record's `enforced_in_ci`, the
README and this page together: the test is red until they agree.

## Recovery

A coverage run is a local command or, later, a scheduled job: nothing in this change touches the per-PR legs, runs in the
background, sends data or ships code to users. Interrupted runs leave `.coverage.*` files, which are gitignored;
`coverage erase` at the start of the commands above removes them, so a stale file cannot be merged into the next
figure (`combine` alone would merge it).

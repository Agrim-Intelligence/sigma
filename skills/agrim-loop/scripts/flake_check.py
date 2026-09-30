"""Reject non-deterministic tests before they are credited (issue #1933).

LAYER 5 BY NUMBER, FIRST BY SEQUENCE. A flaky test poisons every other test-trust layer at once: a
coin-flip red is indistinguishable from an earned one, which corrupts the record #1934 exists to
produce, and a flaky test can "kill" a mutant by luck, silently inflating #1935's kill rate. Neither
is trustworthy without this, which is why it lands first.

NO RANDOMIZATION PLUGIN IS USED, BECAUSE NONE IS INSTALLED. The issue proposes "pytest's existing
randomization"; pytest_randomly, pytest_random_order, pytest-xdist and pytest-rerunfailures are all
ABSENT here (verified by import, 2026-09-01) and pytest 8.4.2 core has no shuffle option, while
adding a dependency would contradict the same sentence's "no new tooling". So ordering is varied by
collecting node ids with `--collect-only -q` and shuffling them in Python.

THAT WORKS ONLY BECAUSE PYTEST HONOURS THE ORDER IT IS GIVEN, which was verified before this module
was written rather than assumed: given `test_c test_a test_b` the observed execution order was
`cab`; given `test_b test_c test_a`, `bca`. Had pytest normalised by file, the shuffle would have
been decorative and every unit test here would still have passed against a fake runner.

THE MECHANISMS THIS TARGETS ARE REAL INCIDENTS IN THIS REPO, not hypotheticals: tracemalloc
assertions that swing ~70x across DuckDB versions (order/environment), and a tmp_path substring
assertion that matched the TEST FUNCTION'S OWN NAME rather than the code under test (path). A third
surfaced while this was being built -- a private package's API-ingest concurrency test failed on an
unmodified origin/main and passed on the next commit with no fix in between.

NAMED CEILING, because a guard that oversells itself is worse than none: three runs catch a ~50%
flake with probability 0.75, not 1.0, and a 5%-of-the-time flake will usually pass all three and be
credited. This raises the cost of shipping a flake; it does not eliminate it.
"""
import random
import subprocess
import sys
import tempfile
import time

#: pytest exit codes that mean NOTHING WAS MEASURED, as opposed to a test result: 2 interrupted,
#: 3 internal error, 4 usage error, 5 no tests collected. Three runs that agree on one of these
#: agree that the harness is broken -- not that the suite is deterministic. Without this, the
#: agreement rule reports `verified` for a check that ran zero tests, which is exactly how a
#: malformed node id (see `_collect`) reported success three times in a row on a real goal.
_NOT_A_RESULT = frozenset((2, 3, 4, 5))

#: Above this many changed test FILES, the scope is not a scope. #1933 required "scoped to
#: new/changed tests; unchanged tests run once", but nothing enforced a ceiling when the caller's
#: diff was wrong -- and a wrong diff is easy to produce: a work record whose `base` names a STALE
#: local branch yields the whole repo. Live on 2026-09-01 that was 94 files, run three times, for
#: 32.8 MINUTES, ending in a false verdict. The bound reports `absent` and SAYS what it declined to
#: measure; silently truncating to the first N would report on a subset while looking complete.
MAX_SCOPED_FILES = 25


def _measured(exit_code):
    """False when an exit code means NOTHING WAS MEASURED rather than a test result.

    Two families, and the second was the live bug. pytest's own 2/3/4/5 (interrupted, internal
    error, usage error, nothing collected) -- and ANY NEGATIVE code, which on POSIX is the process
    having been KILLED BY A SIGNAL (-15 SIGTERM, -9 SIGKILL). A killed run is the most definitive
    "nothing was measured" there is, and comparing it against a completed run manufactures a
    disagreement out of the harness rather than the suite."""
    return not (exit_code in _NOT_A_RESULT or exit_code < 0)

#: Runs per check. Three is the issue's own number: enough to catch a coin-flip most of the time,
#: cheap enough to run on every goal. See the ceiling in the module docstring.
DEFAULT_RUNS = 3


def _run(cwd, argv):
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    return (p.returncode, (p.stdout or "") + (p.stderr or ""))


def _collect(root, test_files, run):
    """Node ids for `test_files`, rebuilt against the paths WE asked for.

    WHY NOT USE pytest's PRINTED IDS DIRECTLY. `--collect-only -q` prints each id relative to
    pytest's own ROOTDIR, and rootdir is resolved per invocation from the nearest config file --
    so `pkg/tests/test_x.py` comes back as `tests/test_x.py::test_a`, because a package one level
    down carries its own pyproject. Feeding that back from the repo root is a usage error (exit 4) on every run,
    and since all three runs agreed on exit 4, the agreement rule called the suite `verified`. A
    check that measured nothing and reported success.

    Collecting per-file and re-attaching the requested path is what makes the id independent of
    whatever rootdir pytest picked. Verified live: the rebuilt
    `pkg/tests/test_x.py::test_a` runs
    from the repo root, while pytest's own printed form does not."""
    ids = []
    for path in test_files:
        _code, out = run(root, ["python3", "-m", "pytest", "--collect-only", "-q", path])
        for line in (out or "").splitlines():
            line = line.strip()
            if "::" in line:
                ids.append("%s::%s" % (path, line.split("::", 1)[1]))
    return ids


def _failed_ids(output):
    """Node ids pytest reported as FAILED, sorted so two runs are comparable as sets."""
    out = set()
    for line in (output or "").splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[0] == "FAILED":
            out.add(parts[1])
    return sorted(out)


def check(root, test_files, runs=DEFAULT_RUNS, run=None, seed=None, node_ids=None):
    """Run `test_files` `runs` times with varied order and a fresh basetemp; judge agreement.

    -> {"verdict": "verified"|"unverified"|"absent", "runs": [...], "disagreement": str|None, "ms"}

    DISAGREEMENT IS THE SIGNAL, NOT FAILURE. Three consistent failures are `verified` here: a
    deterministically red test is not flaky, and calling it `unverified` would hide a real red
    behind a flakiness verdict -- which is exactly how a check teaches people to ignore it. Only
    runs that DISAGREE with each other mark a test non-deterministic.

    ABSENT IS AN HONEST THIRD STATE, the same ABSENT-vs-PASS rule a downstream gap evaluator uses --
    distinction: no changed test files, or a module that cannot even be collected, means nothing was
    measured. Returning `verified` there would credit a suite that never ran a single test.

    `run(cwd, argv) -> (exit_code, output)` is injectable so the controls are fake-runner fixtures,
    matching work.py's own `_runner` convention. A fake-runner green is NOT evidence this catches a
    real flake -- see the executed real-runner controls on the PR.
    """
    run = run or _run
    started = time.perf_counter()
    absent = {"verdict": "absent", "runs": [], "disagreement": None, "ms": 0}
    if not test_files:
        return absent
    if len(test_files) > MAX_SCOPED_FILES:
        # LOUD, never silent: a check that quietly measured a subset would look complete.
        print("loop: flake check declined — %d changed test files exceeds the %d-file scope bound; "
              "this usually means the goal's base ref is stale, so the diff spans the whole repo "
              "rather than the goal. Nothing was measured."
              % (len(test_files), MAX_SCOPED_FILES), file=sys.stderr)
        return dict(absent, reason="scope bound: %d changed test files > %d"
                                     % (len(test_files), MAX_SCOPED_FILES))
    ids = list(node_ids) if node_ids is not None else _collect(root, test_files, run)
    if not ids:
        return dict(absent, ms=int((time.perf_counter() - started) * 1000))

    rng = random.Random(seed)
    observations, results = [], []
    for i in range(runs):
        order = list(ids)
        rng.shuffle(order)
        # A FRESH basetemp PER RUN, not per check: reusing one across runs is what lets a path
        # assumption pass every time. pytest creates the directory lazily, only when a test actually
        # requests a tmp fixture, so an unused one costs nothing.
        base = tempfile.mkdtemp(prefix="sigma-flake-%d-" % i)
        code, out = run(root, ["python3", "-m", "pytest", "-q", "--basetemp=" + base, *order])
        failed = _failed_ids(out)
        results.append({"exit": code, "failed": failed})
        observations.append((code == 0, tuple(failed)))

    ms = int((time.perf_counter() - started) * 1000)
    # STRUCTURAL GUARD against the masking failure above. ANY run that did not produce a result
    # makes the comparison meaningless -- not only the case where they ALL failed to. `any`, not
    # `all`: one killed run among two passes has nothing to be compared with, and calling that a
    # disagreement blames the suite for the harness (the live 2026-09-01 false `unverified`).
    if any(not _measured(r["exit"]) for r in results):
        return {"verdict": "absent", "runs": results, "ms": ms,
                "disagreement": None}
    if len(set(observations)) == 1:
        return {"verdict": "verified", "runs": results, "disagreement": None, "ms": ms}
    disagreed = sorted({t for _ok, failed in observations for t in failed})
    return {"verdict": "unverified", "runs": results, "ms": ms,
            "disagreement": "runs disagreed on: " + (", ".join(disagreed) or "pass/fail status")}

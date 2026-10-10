"""claim_refs.py -- parallel SMOKE TEST and the namespace-fetch measurement (#858).

SMOKE TEST, not the system of record. The deterministic staged-stale-view tests in
`test_claim_refs.py` are the proof that the lease guard works. Sensitivity of THIS test to a removed
guard depends on timing and on the host: on the research machine plain --force produced multiple
winners in 98 to 99 of 100 goals and the guarded form in 0 of 1000 attempts; the design's own shell
spike measured 0 of 100 duplicates with plain -f. A second run at implement time (12-core host under heavy load) measured 100 of 100 goals
and 1000 of 1000 attempts WON under plain --force, 0 of 100 goals guarded: the number moves with
load. Do not rely on this test to catch a removed guard.
(AGENTS.md, "a port across a performance boundary": the race window here is set by a ~10 ms
`commit-tree` before each push, which is a property of this host, not of the code under test.)

What it does check, every run: 100 goals x 10 clones = 1000 attempts, each `build_claim` + `create`
in a thread pool (threads suffice, the work is subprocess); per goal EXACTLY ONE `WON`, every other
attempt `LOST_RACE` (none `OUTAGE`/`REFUSED`), and the remote tip equals the winner's sha. Worker count
is derived from the machine, `min(10, cpu_count * 2)`. The attempt count is fixed at 1000.

The second test measures the cost of reading the claim namespace at 10 / 100 / 1000 refs from a
fresh clone (`read_all` = fetch, `read_tips` = ls-remote). LOCAL-PATH TRANSPORT ONLY: network latency
and hosted-remote ref limits are NOT measured. It asserts sanity only (every ref returned), never a
timing. It is not marked `slow`: on the dev host it takes well under 60 s (commit-tree at ~10 ms
dominates the 1110 commits it builds). Everything is hermetic, under `tmp_path`.
"""
import os
import pathlib
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import test_claim_refs as base       # same loader, same hermetic env fixture
from test_claim_refs import _hermetic_env, cr, git  # noqa: F401  (autouse fixture re-exported)

GOALS = 100
CLONES = 10


def test_parallel_create_one_winner_per_goal_smoke(tmp_path):
    """SMOKE TEST, not the system of record (see the module docstring)."""
    w = base.World(tmp_path, clones=CLONES)
    goals = ["g%03d" % i for i in range(GOALS)]
    attempts = [(g, k) for k in range(CLONES) for g in goals]       # 1000, interleaved across goals
    assert len(attempts) == 1000
    workers = max(1, min(10, (os.cpu_count() or 1) * 2))

    def attempt(item):
        goal, k = item
        sha = cr.build_claim(w.clones[k], "holder%d" % k, "host", "run", 1000, 1800)
        return goal, k, sha, cr.create(w.clones[k], w.remote, goal, sha)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(attempt, attempts))

    by_goal = {g: [] for g in goals}
    for goal, _k, sha, out in results:
        by_goal[goal].append((sha, out))
    tips = cr.read_tips(w.clones[0], w.remote)
    for goal, rows in by_goal.items():
        kinds = sorted(out.kind for _sha, out in rows)
        assert kinds == [cr.LOST_RACE] * (CLONES - 1) + [cr.WON], (goal, kinds)
        winner = [sha for sha, out in rows if out.kind == cr.WON][0]
        assert tips[goal] == winner
    assert len(tips) == GOALS


def _timed(fn, *args):
    t0 = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - t0


def test_namespace_fetch_cost_measured(tmp_path, capsys):
    rows = []
    for n in (10, 100, 1000):
        root = tmp_path / ("n%d" % n)
        root.mkdir()
        w = base.World(root, clones=2)
        shas = [cr.build_claim(w.clones[0], "h", "host", "run%d" % i, 1000, 1800) for i in range(n)]
        specs = ["%s:%s%s" % (sha, cr.NAMESPACE, "g%05d" % i) for i, sha in enumerate(shas)]
        # one batched push of N refspecs (setup only; the primitive's own pushes are single-ref)
        git(w.clones[0], "push", "-q", w.remote, *specs)
        tips, t_tips = _timed(cr.read_tips, w.clones[1], w.remote)
        first, t_first = _timed(cr.read_all, w.clones[1], w.remote)        # first fetch
        again, t_again = _timed(cr.read_all, w.clones[1], w.remote)        # no-op fetch
        assert len(tips) == n and len(first) == n and len(again) == n
        assert all(parsed["lease_seconds"] == 1800 for _sha, parsed in first.values())
        rows.append((n, t_first, t_again, t_tips))
    with capsys.disabled():
        print("\nnamespace read cost, local-path transport (network latency NOT measured), seconds")
        print("%8s %12s %12s %14s" % ("refs", "fetch first", "fetch no-op", "ls-remote"))
        for n, a, b, c in rows:
            print("%8d %12.3f %12.3f %14.3f" % (n, a, b, c))
        print(subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip())

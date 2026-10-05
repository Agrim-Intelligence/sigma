#!/usr/bin/env python3
"""Velocity calibration - size from measurement, not intuition. Measures this repo's recent git
throughput (commits/day, and merge-commits/day as a PR proxy) over a trailing window, and converts a
work estimate (N PR-sized units) into a calendar band at the measured rate. Git-only, zero-dep; the
git runner is injectable so the logic is hermetically testable."""
import sys


def _git(args, root="."):
    import subprocess
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True).stdout


def _count(run, extra):
    # git does the date math via --since, so no Python date handling is needed (tests stay pure).
    return len([l for l in run(["log", *extra, "--pretty=format:%H"]).splitlines() if l.strip()])


def measure(days=14, run=None):
    """Recent throughput over the trailing <days>: total commits + merge-commits (a PR proxy), as
    per-day rates. `run(args)` runs `git <args>`; injectable for hermetic tests."""
    run = run or _git
    since = [f"--since={days} days ago"]
    commits = _count(run, since)
    merges = _count(run, since + ["--merges"])
    d = max(int(days), 1)
    return {"window_days": days, "commits": commits, "merges": merges,
            "commits_per_day": round(commits / d, 2), "prs_per_day": round(merges / d, 2)}


def estimate(units, days=14, run=None):
    """Convert <units> PR-sized work into a calendar band at the measured rate. Uses merges/day (the
    PR proxy), falling back to commits/day when the repo doesn't merge-commit. eta_days is None when
    there's no recent history to ground it - don't guess."""
    m = measure(days, run)
    rate = m["prs_per_day"] or m["commits_per_day"]
    return {**m, "units": units, "rate_used": rate,
            "eta_days": round(units / rate, 1) if rate else None}


USAGE = "usage: velocity.py measure [repo_root] [days] | estimate <units> [repo_root] [days]"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 2 and argv[1] == "measure":
        root = argv[2] if len(argv) > 2 else "."
        days = int(argv[3]) if len(argv) > 3 else 14
        m = measure(days, run=lambda a: _git(a, root))
        print(f"velocity (last {m['window_days']}d): {m['commits']} commits ({m['commits_per_day']}/day), "
              f"{m['merges']} merges/PRs ({m['prs_per_day']}/day)")
        return 0
    if len(argv) >= 3 and argv[1] == "estimate":
        units = float(argv[2])
        root = argv[3] if len(argv) > 3 else "."
        days = int(argv[4]) if len(argv) > 4 else 14
        e = estimate(units, days, run=lambda a: _git(a, root))
        if e["eta_days"] is None:
            print(f"velocity: no commits in the last {days}d - can't ground an estimate "
                  "(widen the window, or say so; don't guess).")
        else:
            print(f"velocity: {units:g} PR-sized units at {e['rate_used']}/day ~= {e['eta_days']} calendar "
                  f"days (measured: {e['commits_per_day']} commits/day, {e['prs_per_day']} PRs/day over {days}d).")
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

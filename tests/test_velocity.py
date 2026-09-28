"""Velocity calibration: measure recent git throughput and convert a work estimate to a calendar
band. Git-only; the git runner is injectable so these tests are hermetic (no real git, no dates)."""
import pathlib, importlib.util

V = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-velocity" / "scripts" / "velocity.py"


def _v():
    spec = importlib.util.spec_from_file_location("velocity", V)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _fake(commits, merges):
    """A git runner stub: N hashes for a plain log, M for a --merges log."""
    def run(args):
        n = merges if "--merges" in args else commits
        return "\n".join(f"h{i}" for i in range(n))
    return run


def test_measure_rates_over_window():
    v = _v()
    m = v.measure(days=10, run=_fake(commits=20, merges=5))
    assert m["commits"] == 20 and m["merges"] == 5
    assert m["commits_per_day"] == 2.0 and m["prs_per_day"] == 0.5
    assert m["window_days"] == 10


def test_estimate_converts_units_to_calendar_days():
    v = _v()
    e = v.estimate(units=10, days=10, run=_fake(commits=20, merges=5))
    assert e["rate_used"] == 0.5                 # merges/day is the PR proxy
    assert e["eta_days"] == 20.0                 # 10 units / 0.5 per day


def test_estimate_falls_back_to_commits_when_no_merges():
    v = _v()
    e = v.estimate(units=8, days=8, run=_fake(commits=16, merges=0))
    assert e["rate_used"] == 2.0                 # no merge-commits -> commits/day
    assert e["eta_days"] == 4.0


def test_estimate_no_history_cannot_ground():
    v = _v()
    e = v.estimate(units=5, days=14, run=_fake(commits=0, merges=0))
    assert e["eta_days"] is None                 # no commits -> can't size; don't guess


def test_main_measure_and_estimate_exit_clean():
    v = _v()
    assert v.main(["velocity.py", "measure", ".", "10"]) == 0     # real git on this repo
    assert v.main(["velocity.py", "estimate", "5"]) == 0

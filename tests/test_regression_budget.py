"""#879 (AC-6): evals/regression/budget.py runs the plain control gesture against budget.json.

Each test drives the script as a subprocess with a stand-in command after `--`, so the guard is seen
red (over the ceiling, a failing command, an unreadable budget) and not only green."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGET = ROOT / "evals" / "regression" / "budget.py"


def run(tmp_path, ceiling, command, raw=None):
    f = tmp_path / "budget.json"
    f.write_text(raw if raw is not None else json.dumps({"ceiling_seconds": ceiling}), encoding="utf-8")
    return subprocess.run([sys.executable, str(BUDGET), "--budget", str(f), "--", *command],
                          capture_output=True, text=True, timeout=60)


def test_under_ceiling_passes_and_prints_wall_time(tmp_path):
    p = run(tmp_path, 60, [sys.executable, "-c", "pass"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert "onboarding-control wall time:" in p.stdout and "ceiling 60" in p.stdout


def test_over_ceiling_exits_one(tmp_path):
    p = run(tmp_path, 0.05, [sys.executable, "-c", "import time; time.sleep(0.5)"])
    assert p.returncode == 1, p.stdout + p.stderr
    assert "exceeds the" in p.stdout


def test_null_ceiling_prints_and_passes(tmp_path):
    p = run(tmp_path, None, [sys.executable, "-c", "pass"])
    assert p.returncode == 0 and "ceiling unset" in p.stdout, p.stdout + p.stderr


def test_failing_command_exit_code_is_propagated(tmp_path):
    p = run(tmp_path, 60, [sys.executable, "-c", "raise SystemExit(3)"])
    assert p.returncode == 3, p.stdout + p.stderr


def test_bad_budget_file_exits_two(tmp_path):
    for raw in ("not json", json.dumps({"ceiling_seconds": -1}), json.dumps({"ceiling_seconds": "9"}),
                json.dumps([1])):
        p = run(tmp_path, None, [sys.executable, "-c", "pass"], raw=raw)
        assert p.returncode == 2, (raw, p.stdout + p.stderr)
    assert subprocess.run([sys.executable, str(BUDGET), "--budget", str(tmp_path / "nope.json"), "--",
                           sys.executable, "-c", "pass"], capture_output=True, text=True).returncode == 2

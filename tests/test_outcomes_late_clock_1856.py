"""#1856: the writes-nothing outcomes test must hold on any date.

`test_outcomes.test_a_run_writes_nothing_anywhere` runs `outcomes.py` as a subprocess, which reads the
real clock, while the fixtures were stamped from the constant `NOW`. Once the real date passes
NOW + 1 day the oldest mature goal leaves the 30d window. This file forces the subprocess clock 40 days
past NOW (a sitecustomize shim on PYTHONPATH) and runs that test under it."""
import subprocess
import sys

import test_outcomes as T

LATE = T.NOW + 40 * T.DAY


def test_writes_nothing_holds_when_the_subprocess_clock_is_40_days_past_now(tmp_path, monkeypatch):
    shim = tmp_path / "clock"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(f"import time\ntime.time = lambda: {float(LATE)}\n",
                                           encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(shim))
    seen = float(subprocess.run([sys.executable, "-c", "import time; print(time.time())"],
                                capture_output=True, text=True, timeout=60).stdout)
    assert seen == LATE, "the clock shim did not load; the test below would be vacuous"
    late = tmp_path / "late"
    late.mkdir()
    T.test_a_run_writes_nothing_anywhere(late)

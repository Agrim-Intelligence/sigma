"""#1883: the dependency-map tests stamp `checked_at` from the clock when called, not at import.

Late in a full run a map stamped from an import-time `NOW` is older than
`dag_store.CHANGE_CHECK_SECONDS`, so it reads as unusable and board order wins. This guard runs the
DAG test files in a child pytest whose `time.time` is moved forward past both map-age windows after
collection (shifted, never frozen: a frozen clock hangs polling loops). A file that stamps from an
import-time clock fails there at any wall-clock date; nothing here depends on a date.
"""
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_dag_pick_1619 as t19  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FILES = ("test_dag_pick_1619", "test_auto_unpark_dag", "test_batch_one_read_1621",
         "test_dag_order_1620", "test_dag_repair_1626", "test_dag_pick_1857", "test_dag_repair_1859")
# Past the 600 s change check AND past 2 x the default TTL (the `built` stamp's window), derived so a
# higher default TTL cannot quietly turn the `built` control into decoration.
SHIFT = 2 * t19.ds.DEFAULT_TTL_MINUTES * 60 + 100
assert SHIFT > t19.ds.CHANGE_CHECK_SECONDS

PLUGIN = '''
import time
_real = time.time
_shift = [0.0]
time.time = lambda: _real() + _shift[0]


def pytest_collection_finish(session):
    _shift[0] = %d.0
''' % SHIFT


def test_dag_tests_stamp_from_the_clock_when_called(tmp_path):
    (tmp_path / "clockshift_1883.py").write_text(PLUGIN)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         "-p", "clockshift_1883"] + [str(HERE / (f + ".py")) for f in FILES],
        cwd=str(HERE.parent), env=env, capture_output=True, text=True, timeout=900)
    out = r.stdout + r.stderr
    tail = "\n".join(out.splitlines()[-40:])
    assert r.returncode == 0 and " passed" in out and "failed" not in out, tail

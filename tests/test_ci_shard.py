"""tools/ci_shard.py splits the full tier's files so every file runs exactly once (#2255)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ci_shard", ROOT / "tools" / "ci_shard.py")
ci_shard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_shard)

NAMES = [f"tests/test_{c}.py" for c in "abcdefghijklmnopqrstuvwxyz"]
WEIGHTS = {"test_a.py": 100, "test_b.py": 90, "test_c.py": 10}


def test_shards_partition_the_suite_exactly():
    for count in (1, 4, 6):
        got = [f for i in range(1, count + 1) for f in ci_shard.shard(NAMES, i, count, WEIGHTS)]
        assert sorted(got) == sorted(NAMES)
        assert len(got) == len(set(got))


def test_heavy_files_are_spread_not_clustered():
    firsts = [ci_shard.shard(NAMES, i, 4, WEIGHTS) for i in range(1, 5)]
    assert not any("tests/test_a.py" in s and "tests/test_b.py" in s for s in firsts)


def test_the_split_is_deterministic():
    assert ci_shard.shard(NAMES, 2, 4, WEIGHTS) == ci_shard.shard(list(reversed(NAMES)), 2, 4, WEIGHTS)


def test_real_tests_dir_is_covered_with_the_committed_weights():
    real = [str(p) for p in (ROOT / "tests").glob("test_*.py")]
    got = [f for i in range(1, 7) for f in ci_shard.shard(real, i, 6)]
    assert sorted(got) == sorted(real)


def test_bad_index_is_refused():
    for i, n in ((0, 4), (5, 4), (1, 0)):
        try:
            ci_shard.shard(NAMES, i, n, WEIGHTS)
        except ValueError:
            continue
        raise AssertionError((i, n))


def test_ignored_files_are_dropped_before_the_deal(capsys):
    """#2272: the nightly tier passes its $SLOW list; an ignored file is in no shard, everything else in one."""
    real = sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))
    skip = ["--ignore=tests/test_readiness_drills.py", "--ignore=tests/test_b6_463.py"]
    got = []
    for i in (1, 2, 3):
        assert ci_shard.main(["ci_shard.py", str(i), "3", str(ROOT / "tests"), *skip]) == 0
        got += [Path(l).name for l in capsys.readouterr().out.split()]
    assert sorted(got) == [n for n in real if n not in ("test_readiness_drills.py", "test_b6_463.py")]

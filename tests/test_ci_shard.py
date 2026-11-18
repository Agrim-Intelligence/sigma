"""tools/ci_shard.py splits the full tier's files so every file runs exactly once (#2255)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ci_shard", ROOT / "tools" / "ci_shard.py")
ci_shard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_shard)

NAMES = [f"tests/test_{c}.py" for c in "abcdefghijklmnopqrstuvwxyz"] + [
    "tests/" + n for n in ci_shard.HEAVY
]


def test_shards_partition_the_suite_exactly():
    got = [f for i in range(1, 5) for f in ci_shard.shard(NAMES, i, 4)]
    assert sorted(got) == sorted(NAMES)
    assert len(got) == len(set(got))


def test_heavy_files_are_spread_not_clustered():
    per = [[f for f in ci_shard.shard(NAMES, i, 4) if Path(f).name in ci_shard.HEAVY] for i in range(1, 5)]
    assert max(len(p) for p in per) == 2 and min(len(p) for p in per) == 1


def test_real_tests_dir_is_covered():
    real = [str(p) for p in (ROOT / "tests").glob("test_*.py")]
    got = [f for i in range(1, 5) for f in ci_shard.shard(real, i, 4)]
    assert sorted(got) == sorted(real)


def test_bad_index_is_refused():
    for i, n in ((0, 4), (5, 4), (1, 0)):
        try:
            ci_shard.shard(NAMES, i, n)
        except ValueError:
            continue
        raise AssertionError((i, n))

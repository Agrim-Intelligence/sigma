#!/usr/bin/env python3
"""Print the test files of one shard of the full CI tier, one path per line (#2255).

    python3 tools/ci_shard.py <index> <count> [tests-dir]     # index is 1-based

Deterministic and balanced: each `test_*.py` under the tests directory has a weight (measured test
seconds, tools/ci_shard_weights.json; a file with no entry gets the median weight), and the files are
dealt heaviest-first to the currently lightest shard (ties broken by sorted name). Every file is in
exactly one shard, so the union of all shards is the whole suite. Read-only: writes nothing.

Refresh the weights from the junit artifacts of a full-tier run (per-file sum of testcase time) when
the shards drift apart; a stale weight costs balance, never coverage.
"""
import json
import statistics
import sys
from pathlib import Path

WEIGHTS = Path(__file__).resolve().with_name("ci_shard_weights.json")


def load_weights():
    try:
        return json.loads(WEIGHTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def shard(files, index, count, weights=None):
    """Files of shard `index` (1-based) out of `count`; `files` are paths or names."""
    if count < 1 or not 1 <= index <= count:
        raise ValueError(f"shard index {index} is outside 1..{count}")
    weights = load_weights() if weights is None else weights
    default = statistics.median(weights.values()) if weights else 1
    ordered = sorted(files, key=lambda p: Path(p).name)
    ordered.sort(key=lambda p: -weights.get(Path(p).name, default))  # stable: ties keep sorted-name order
    load = [0.0] * count
    mine = []
    for f in ordered:
        i = min(range(count), key=lambda k: (load[k], k))
        load[i] += max(weights.get(Path(f).name, default), 1)
        if i == index - 1:
            mine.append(f)
    return sorted(mine, key=lambda p: Path(p).name)


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv[1:2] in (["-h"], ["--help"]) else 2
    if len(argv) < 3:
        print(__doc__)
        return 2
    index, count = int(argv[1]), int(argv[2])
    root = Path(argv[3] if len(argv) > 3 else "tests")
    files = [str(p) for p in root.glob("test_*.py")]
    for f in shard(files, index, count):
        print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

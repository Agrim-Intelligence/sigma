#!/usr/bin/env python3
"""Print the test files of one shard of the full CI tier, one path per line (#2255).

    python3 tools/ci_shard.py <index> <count> [tests-dir]     # index is 1-based

Deterministic: files are the sorted `test_*.py` names under the tests directory. The files in HEAVY
(the measured-slowest, see the nightly job's SLOW list) are dealt round-robin first so no shard
collects them all; every other file goes to shard (position in sorted order) % count. Every file is
in exactly one shard, so the union of all shards is the whole suite. Read-only: writes nothing.
"""
import sys
from pathlib import Path

HEAVY = (
    "test_b6_463.py",
    "test_mutation_sample.py",
    "test_onboarding_control.py",
    "test_readiness_drills.py",
    "test_readiness_growth_audit.py",
    "test_shared_sdlc_paths.py",
)


def shard(files, index, count):
    """Files of shard `index` (1-based) out of `count`; `files` are paths or names."""
    if count < 1 or not 1 <= index <= count:
        raise ValueError(f"shard index {index} is outside 1..{count}")
    ordered = sorted(files, key=lambda p: Path(p).name)
    heavy = [f for f in ordered if Path(f).name in HEAVY]
    rest = [f for f in ordered if Path(f).name not in HEAVY]
    out = [f for i, f in enumerate(heavy) if i % count == index - 1]
    out += [f for i, f in enumerate(rest) if i % count == index - 1]
    return out


def main(argv):
    if len(argv) < 3 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv[1:2] in (["-h"], ["--help"]) else 2
    index, count = int(argv[1]), int(argv[2])
    root = Path(argv[3] if len(argv) > 3 else "tests")
    files = [str(p) for p in root.glob("test_*.py")]
    for f in shard(files, index, count):
        print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

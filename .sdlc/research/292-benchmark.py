"""Local real-Git history benchmark for #292; synthetic input, no network or persistent repo.
Run: python3 .sdlc/research/292-benchmark.py
Reports actual wall times, not an SLA. Three samples per size; caches are not flushed.
"""
import importlib.util
import json
import os
import pathlib
import platform
import statistics
import subprocess
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SIZES = (100, 1000, 10000)


def run(repo, args, data=None):
    return subprocess.run(["git", *args], cwd=repo, input=data, capture_output=True,
                          check=True, timeout=120).stdout.decode().strip()


def payload():
    parts = []
    for number in range(1, max(SIZES) + 1):
        content = f"version {number}\n"
        parts.extend([f"commit refs/heads/history\nmark :{number}\n",
                      f"committer Benchmark <benchmark@example.invalid> {1700000000 + number} +0000\n",
                      "data 7\nhistory\n"])
        if number > 1:
            parts.append(f"from :{number - 1}\n")
        parts.append(f"M 100644 inline x.txt\ndata {len(content)}\n{content}\n")
        if number == 1:
            parts.append("M 100644 inline old.txt\ndata 4\nold\n\n")
        parts.append("\n")
        if number in SIZES:
            parts.append(f"reset refs/heads/size-{number}\nfrom :{number}\n\n")
    return "".join(parts).encode()


def main():
    spec = importlib.util.spec_from_file_location(
        "feature_rebase", ROOT / "skills/sigma-loop/scripts/feature_rebase.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    result = {"platform": platform.platform(), "machine": platform.machine(),
              "logical_cpus": os.cpu_count(), "samples_per_case": 3,
              "cache": "not flushed; sequential runs", "rows": []}
    with tempfile.TemporaryDirectory(prefix="sigma-292-history-") as directory:
        repo = pathlib.Path(directory)
        result["git"] = run(repo, ["--version"])
        run(repo, ["init", "-q", "-b", "main"])
        started = time.monotonic()
        run(repo, ["fast-import", "--quiet"], payload())
        result["history_build_seconds"] = time.monotonic() - started
        root = run(repo, ["rev-list", "--max-parents=0", "history"])
        run(repo, ["update-ref", "refs/remotes/origin/main", root])
        for size in SIZES:
            before = f"size-{size}"
            assert int(run(repo, ["rev-list", "--count", before])) == size
            # Make a real child deleting old.txt, preserving its parent's changing-file tree.
            run(repo, ["read-tree", before])
            run(repo, ["update-index", "--force-remove", "old.txt"])
            tree = run(repo, ["write-tree"])
            deleted = run(repo, ["-c", "user.name=Benchmark", "-c", "user.email=benchmark@example.invalid",
                                 "commit-tree", tree, "-p", before, "-m", "local deletion"])
            for name, operation, expected in (
                ("rollback_detection", lambda: guard.dropped_paths(repo, before, before + "~1"), ["x.txt"]),
                ("deletion_attribution", lambda: guard.own_losses(repo, root, deleted,
                                                                  "origin/main", ["old.txt"]), {"old.txt"}),
            ):
                samples = []
                for _ in range(3):
                    started = time.monotonic()
                    answer = operation()
                    samples.append(time.monotonic() - started)
                    assert answer == expected, (size, name, answer)
                result["rows"].append({"history_commits": size, "operation": name,
                                       "seconds": samples, "median_seconds": statistics.median(samples)})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

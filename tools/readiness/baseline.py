#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create a pinned review baseline without inspecting a live checkout.

USAGE
    baseline.py snapshot <repo> [--sha SHA] [--dest DIR]
    baseline.py inventory <repo> [--sha SHA] [--json]
    baseline.py status <repo> --issues N,N [--repo OWNER/NAME] [--json]

EXIT: 0 = ok; 1 = failed; 2 = bad arguments.
"""
import argparse
import json
import math
from pathlib import Path, PurePosixPath
import subprocess
import sys

RUN = subprocess.run
SCHEMA = "readiness-inventory/v1"
SURFACES = ("python_non_test", "shell", "tests", "skills_md", "docs_md")


class UsageError(Exception):
    """A caller supplied an unsupported or unsafe argument."""


def _run(args, **kwargs):
    return RUN(args, text=True, capture_output=True, check=True, **kwargs)


def _output(repo, *args):
    return _run(["git", "-C", str(repo), *args]).stdout.strip()


def _resolved_sha(repo, sha):
    return _output(repo, "rev-parse", "--verify", f"{sha}^{{commit}}")


def _tracked_paths(repo, sha):
    return [line for line in _output(repo, "ls-tree", "-r", "--name-only", sha).splitlines() if line]


def _blobs(repo, sha, paths):
    """Yield ``(path, bytes)`` from one Git batch request, never the working tree."""
    process = subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None and process.stdout is not None
    try:
        for path in paths:
            process.stdin.write(f"{sha}:{path}\n".encode())
        process.stdin.close()
        for path in paths:
            header = process.stdout.readline().decode("ascii", errors="replace").strip().split()
            if len(header) != 3 or header[1] != "blob":
                raise RuntimeError(f"cannot read tracked blob {path!r}: {' '.join(header)}")
            data = process.stdout.read(int(header[2]))
            process.stdout.read(1)  # trailing newline in Git's batch protocol
            yield path, data
        stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
        if process.wait() != 0:
            raise RuntimeError(stderr.strip() or "git cat-file --batch failed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def _surface(path):
    pure = PurePosixPath(path)
    if path.endswith(".py") and not path.startswith("tests/"):
        return "python_non_test"
    if path.endswith(".sh"):
        return "shell"
    if path.startswith("tests/") and path.endswith(".py"):
        return "tests"
    if path.startswith("skills/") and path.endswith(".md"):
        return "skills_md"
    if path.startswith("docs/") and path.endswith(".md"):
        return "docs_md"
    return None


def inventory(repo, sha="HEAD"):
    """Return tracked-file inventory for a commit, independent of checkout dirt."""
    repo, sha = Path(repo), _resolved_sha(repo, sha)
    totals = {name: {"files": 0, "lines": 0, "est_tokens": 0} for name in SURFACES}
    paths = _tracked_paths(repo, sha)
    skill_dirs = {str(PurePosixPath(path).parent) for path in paths
                  if path.startswith("skills/") and PurePosixPath(path).name == "SKILL.md"}
    for path, data in _blobs(repo, sha, paths):
        name = _surface(path)
        if name is None:
            continue
        text = data.decode("utf-8", errors="replace")
        # The launch inventory reports every Python line under tests/, while its
        # file count is intentionally limited to test_* modules.
        if name != "tests" or PurePosixPath(path).name.startswith("test_"):
            totals[name]["files"] += 1
        totals[name]["lines"] += len(text.splitlines())
        totals[name]["est_tokens"] += math.ceil(len(text) / 4)
    return {"schema": SCHEMA, "sha": sha, "surfaces": totals, "skills": len(skill_dirs)}


def snapshot(repo, sha="HEAD", dest=None):
    """Make a detached, push-disabled clone of a source repository at one commit."""
    repo, sha = Path(repo).resolve(), _resolved_sha(repo, sha)
    dest = Path(dest).expanduser() if dest else Path.home() / ".sigma-ops" / "readiness" / sha[:12]
    if dest.exists() and any(dest.iterdir()):
        raise UsageError(f"destination exists and is not empty: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", "--no-local", str(repo), str(dest)])
    _run(["git", "-C", str(dest), "checkout", "--detach", sha])
    _run(["git", "-C", str(dest), "remote", "remove", "origin"])
    hook = dest / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho 'readiness clone: pushing is disabled' >&2\nexit 1\n")
    hook.chmod(0o755)
    return dest


def _repo_slug(repo):
    remote = _output(repo, "config", "--get", "remote.origin.url").removesuffix(".git").rstrip("/")
    if remote.startswith("git@") and ":" in remote:
        return remote.rsplit(":", 1)[1]
    if "://" in remote:
        return remote.split("://", 1)[1].split("/", 1)[1]
    raise UsageError("cannot derive OWNER/NAME; pass --repo")


def status(repo, issues, slug=None):
    """Read issue state via REST, through the injectable module-level runner."""
    slug = slug or _repo_slug(repo)
    rows = []
    for number in issues:
        result = _run(["gh", "api", f"repos/{slug}/issues/{number}", "--jq", "{number,state,title}"])
        try:
            row = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"GitHub returned invalid issue JSON for #{number}") from exc
        rows.append({key: row[key] for key in ("number", "state", "title")})
    return rows


def _issues(value):
    try:
        numbers = [int(part) for part in value.split(",") if part]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--issues must be comma-separated positive integers") from exc
    if not numbers or any(number <= 0 for number in numbers):
        raise argparse.ArgumentTypeError("--issues must be comma-separated positive integers")
    return numbers


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="verb", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("repo")
    snap.add_argument("--sha", default="HEAD")
    snap.add_argument("--dest")
    inv = sub.add_parser("inventory")
    inv.add_argument("repo")
    inv.add_argument("--sha", default="HEAD")
    inv.add_argument("--json", action="store_true")
    stat = sub.add_parser("status")
    stat.add_argument("repo")
    stat.add_argument("--issues", type=_issues, required=True)
    stat.add_argument("--repo", dest="slug")
    stat.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.verb == "snapshot":
            print(snapshot(args.repo, args.sha, args.dest))
        elif args.verb == "inventory":
            payload = inventory(args.repo, args.sha)
            print(json.dumps(payload, sort_keys=True) if args.json else json.dumps(payload, indent=2, sort_keys=True))
        else:
            payload = status(args.repo, args.issues, args.slug)
            print(json.dumps(payload, sort_keys=True) if args.json else "\n".join(
                f"#{row['number']} {row['state']} {row['title']}" for row in payload))
    except UsageError as exc:
        print(f"baseline.py: {exc}", file=sys.stderr)
        return 2
    except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
        print(f"baseline.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

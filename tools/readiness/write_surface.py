#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Scan and ratchet tracked GitHub, git, and destructive filesystem writes.

USAGE: write_surface.py scan REPO --json OUT | render INVENTORY
EXIT: 0 = inventory rendered; 1 = ratchet findings; 2 = bad arguments.
"""

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path


RULES = {
    "gh-issue": re.compile(r'["\']issue["\']\s*,\s*["\'](?:close|reopen|edit|comment|create|delete|transfer|lock)["\']'),
    "gh-pr": re.compile(r'["\']pr["\']\s*,\s*["\'](?:create|merge|close|comment|review|edit|ready)["\']'),
    "gh-label": re.compile(r'["\']label["\']\s*,\s*["\'](?:create|edit|delete)["\']'),
    "gh-project": re.compile(r'["\']project["\']\s*,\s*["\'](?:item-edit|item-add|item-archive|item-delete|field-create|create|link|copy|edit|delete)["\']'),
    "gh-api-write": re.compile(r'(?:["\']api["\']|\bgh\s+api).*?(?:-X|--method)\s*["\']?(?:POST|PATCH|PUT|DELETE)', re.I),
    "graphql-mutation": re.compile(r'mutation\s*[\({]'),
    "git-push": re.compile(r'["\']git["\'].*?["\']push["\']|--force(?:-with-lease)?'),
    "git-destructive": re.compile(r'["\']branch["\']\s*,\s*["\']-D["\']|["\']reset["\']\s*,\s*["\']--hard["\']|["\']worktree["\']\s*,\s*["\']remove["\']|["\']tag["\']'),
    "fs-rmtree": re.compile(r'shutil\.rmtree\('),
}


def _functions(path, text):
    if path.suffix != ".py":
        return [(0, len(text.splitlines()) + 1, "<script>")]
    tree = ast.parse(text, filename=str(path))
    found = [(0, len(text.splitlines()) + 1, "<module>")]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((node.lineno, getattr(node, "end_lineno", node.lineno), node.name))
    return found


def scan_paths(root, paths):
    root = Path(root)
    found = []
    for path in paths:
        path = Path(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        groups = {}
        owners = _functions(path, text)
        for line_no, line in enumerate(lines, 1):
            matching = [(start, name) for start, end, name in owners if start <= line_no <= end]
            function = max(matching, default=(0, "<module>"))[1]
            for rule, pattern in RULES.items():
                if pattern.search(line):
                    groups[(path.relative_to(root).as_posix(), function, rule)] = groups.get((path.relative_to(root).as_posix(), function, rule), 0) + 1
        found.extend({"path": path, "function": function, "rule": rule, "count": count}
                     for (path, function, rule), count in groups.items())
    return sorted(found, key=lambda item: (item["path"], item["function"], item["rule"]))


def _paths(root):
    root = Path(root)
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True)
    if result.returncode:
        return [p for p in root.rglob("*") if p.suffix in {".py", ".sh"} and "tests" not in p.parts]
    return [root / raw.decode() for raw in result.stdout.split(b"\0") if raw and Path(raw.decode()).suffix in {".py", ".sh"} and not raw.decode().startswith("tests/")]


def ratchet(root, inventory):
    entries = json.loads(Path(inventory).read_text()).get("entries", [])
    actual = {(e["path"], e["function"], e["rule"]): e for e in scan_paths(root, _paths(root))}
    expected = {(e["path"], e["function"], e["rule"]): e for e in entries}
    messages = []
    for key, item in actual.items():
        if key not in expected:
            messages.append(f"new write site {item['path']}:{item['function']} {item['rule']} -- add it to docs/launch/write-surface.json with its gate")
    for key, item in expected.items():
        label = f"{item['path']}:{item['function']} {item['rule']}"
        if not item.get("gate"):
            messages.append(f"empty gate {label}")
        elif key not in actual:
            messages.append(f"stale entry {label}")
        elif item.get("count") != actual[key]["count"]:
            messages.append(f"count differs {label}")
    return messages


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan"); scan.add_argument("repo"); scan.add_argument("--json", required=True)
    render = sub.add_parser("render"); render.add_argument("inventory")
    args = parser.parse_args(argv)
    if args.command == "scan":
        Path(args.json).write_text(json.dumps({"entries": scan_paths(args.repo, _paths(args.repo))}, indent=2) + "\n")
        return 0
    entries = json.loads(Path(args.inventory).read_text()).get("entries", [])
    print("| Path | Function | Rule | Count | Gate | Risk |\n|---|---|---|---:|---|---|")
    for e in entries: print("| {path} | {function} | {rule} | {count} | {gate} | {risk} |".format(**e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

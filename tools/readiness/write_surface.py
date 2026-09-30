#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Scan and ratchet tracked GitHub, git, and destructive filesystem writes.

USAGE: write_surface.py scan REPO --json OUT | render INVENTORY
EXIT: 0 = inventory rendered; 1 = ratchet findings; 2 = bad arguments.
"""

import argparse
import ast
import json
import subprocess
from pathlib import Path


RISK = {"gh-issue": "medium", "gh-pr": "medium", "gh-label": "medium",
        "gh-project": "medium", "gh-api-write": "medium", "graphql-mutation": "medium",
        "git-push": "high", "git-destructive": "high", "fs-remove": "high",
        "fs-rmtree": "high", "fs-write": "medium"}
_GH_ACTIONS = {"issue": {"close", "reopen", "edit", "comment", "create", "delete", "transfer", "lock"},
               "pr": {"create", "merge", "close", "comment", "review", "edit", "ready"},
               "label": {"create", "edit", "delete"},
               "project": {"item-edit", "item-add", "item-archive", "item-delete", "field-create", "create", "link", "copy", "edit", "delete"}}
_REMOVE_METHODS = {"unlink", "rmdir"}
_WRITE_METHODS = {"write_text", "write_bytes", "mkdir", "touch"}


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return (parent + "." if parent else "") + node.attr
    return ""


def _strings(node, values):
    """Literal strings reachable from an actual call argument, never comments/docstrings."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        return values.get(node.id, [])
    found = []
    for child in ast.iter_child_nodes(node):
        found.extend(_strings(child, values))
    return found


def _values(tree):
    values = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    values[target.id] = _strings(node.value, values)
    return values


def _rules_for_call(node, values):
    name = _call_name(node.func)
    strings = [s for arg in list(node.args) + [kw.value for kw in node.keywords]
               for s in _strings(arg, values)]
    tokens = [s.lower() for s in strings]
    rules = set()
    command = tokens[:]
    if "gh" in command:
        command = command[command.index("gh") + 1:]
    if name.endswith("._run") or name in {"_run", "_gh_json"}:
        command = tokens
    if len(command) >= 2 and command[0] in _GH_ACTIONS and command[1] in _GH_ACTIONS[command[0]]:
        rules.add("gh-" + ("pr" if command[0] == "pr" else command[0]))
    if command and command[0] == "api" and any(x in {"post", "patch", "put", "delete"} for x in command):
        rules.add("gh-api-write")
    if ("graphql" in command or name.endswith("_graphql")) and any("mutation" in s.lower() for s in strings):
        rules.add("graphql-mutation")
    if "git" in tokens:
        git = tokens[tokens.index("git") + 1:]
        if "push" in git:
            rules.add("git-push")
        if any(x in git for x in ("-d", "-D", "--hard", "remove", "rm", "tag")):
            rules.add("git-destructive")
    if name == "shutil.rmtree":
        rules.add("fs-rmtree")
    elif name.startswith("os.") and name.split(".")[-1] in {"unlink", "remove", "rmdir", "replace", "rename"}:
        rules.add("fs-remove")
    elif name.split(".")[-1] in _REMOVE_METHODS:
        rules.add("fs-remove")
    elif name.startswith("os.") and name.split(".")[-1] in {"mkdir", "makedirs", "link", "symlink"}:
        rules.add("fs-write")
    elif name.split(".")[-1] in _WRITE_METHODS:
        rules.add("fs-write")
    return rules


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
        groups = {}
        owners = _functions(path, text)
        tree = ast.parse(text, filename=str(path)) if path.suffix == ".py" else None
        if tree is None:
            continue
        values = _values(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            matching = [(start, name) for start, end, name in owners if start <= node.lineno <= end]
            function = max(matching, default=(0, "<module>"))[1]
            for rule in _rules_for_call(node, values):
                key = (path.relative_to(root).as_posix(), function, rule)
                groups[key] = groups.get(key, 0) + 1
        found.extend({"path": path, "function": function, "rule": rule, "count": count,
                      "gate": "ungated", "risk": RISK[rule]}
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
    messages = []
    for item in entries:
        label = str(item.get("path", "<unknown>")) + ":" + str(item.get("function", "<unknown>"))
        for field in ("path", "function", "rule", "count", "gate", "risk"):
            if field not in item or item[field] is None:
                messages.append("missing %s %s" % (field, label))
        if item.get("risk") not in {"low", "medium", "high"}:
            messages.append("invalid risk " + label)
        if not isinstance(item.get("count"), int) or item.get("count", 0) < 1:
            messages.append("invalid count " + label)
    actual = {(e["path"], e["function"], e["rule"]): e for e in scan_paths(root, _paths(root))}
    expected = {(e["path"], e["function"], e["rule"]): e for e in entries}
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
    check = sub.add_parser("check"); check.add_argument("repo"); check.add_argument("inventory")
    args = parser.parse_args(argv)
    if args.command == "scan":
        Path(args.json).write_text(json.dumps({"entries": scan_paths(args.repo, _paths(args.repo))}, indent=2) + "\n")
        return 0
    if args.command == "check":
        messages = ratchet(args.repo, args.inventory)
        print("\n".join(messages))
        return 1 if messages else 0
    entries = json.loads(Path(args.inventory).read_text()).get("entries", [])
    print("| Path | Function | Rule | Count | Gate | Risk |\n|---|---|---|---:|---|---|")
    for e in entries: print("| {path} | {function} | {rule} | {count} | {gate} | {risk} |".format(**e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

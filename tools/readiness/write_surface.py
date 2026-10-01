#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Scan and ratchet tracked GitHub, git, and destructive filesystem writes.

USAGE: write_surface.py scan REPO --json OUT | render INVENTORY
EXIT: 0 = inventory rendered; 1 = ratchet findings; 2 = bad arguments.
"""

import argparse
import ast
import json
import shlex
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


def _metadata(path, function, rule):
    """Return the gate and risk for this specific call site, never just its file."""
    site = (path, function, rule)
    known = {
        ("skills/agrim-loop/scripts/feature_propagate.py", "_write_remote", "gh-api-write"):
            ("granted verdict", "high"),
        ("skills/agrim-status/scripts/merge_queue_enable.py", "patch_auto_merge", "gh-api-write"):
            ("exact --yes-enable-merge-queue admin consent", "high"),
        ("skills/agrim-status/scripts/merge_queue_enable.py", "create_merge_queue_ruleset", "gh-api-write"):
            ("exact --yes-enable-merge-queue admin consent", "high"),
        ("skills/agrim-loop/scripts/work.py", "merge_design", "gh-pr"):
            ("work.enabled", "high"),
        ("skills/agrim-loop/scripts/work.py", "close_design", "gh-pr"):
            ("ungated", "high"),
        ("skills/agrim-loop/scripts/work.py", "merge", "gh-pr"):
            ("work.enabled; work.auto_merge != off; merge rights; fresh verify evidence and CLEAN PR", "high"),
        ("skills/agrim-loop/scripts/work.py", "finish", "gh-pr"):
            ("work.enabled; confirmed merged PR", "high"),
        ("skills/agrim-loop/scripts/work.py", "_delete_remote_branch", "gh-api-write"):
            ("work.enabled; merged PR cleanup", "high"),
        ("skills/agrim-loop/scripts/work.py", "_close_issue_the_base_cannot", "gh-api-write"):
            ("work.enabled; base branch cannot close the issue", "high"),
        ("tools/readiness/baseline.py", "snapshot", "fs-write"):
            ("explicit snapshot command; empty destination", "medium"),
        ("tools/readiness/baseline.py", "snapshot", "git-destructive"):
            ("explicit snapshot command; empty destination; detached push-disabled clone", "medium"),
        ("tools/readiness/egress_capture.py", "main", "fs-write"):
            ("explicit summarize command; caller-supplied JSON path", "medium"),
        ("tools/readiness/exposure_scan.py", "_write", "fs-write"):
            ("explicit exposure scan; caller-supplied evidence path", "medium"),
        ("tools/readiness/exposure_scan.py", "scan_refs", "git-destructive"):
            ("explicit refs scan; local tag listing is read-only", "low"),
    }
    if site in known:
        return known[site]
    if path == "skills/agrim-loop/scripts/feature_rebase.py":
        gate = "work.rebase_upkeep"
    elif path == "skills/agrim-loop/scripts/sources.py":
        gate = "discovery.source == github; board writes require project.enabled"
    elif path == "skills/agrim-rebase/scripts/verify_merge.py":
        gate = "human input() confirmation"
    else:
        gate = "ungated"
    risk = RISK[rule]
    if site in {
        ("skills/agrim-rebase/scripts/verify_merge.py", "merge_pr", "gh-pr"),
        ("skills/agrim-loop/scripts/sources.py", "complete", "gh-issue"),
        ("skills/agrim-loop/scripts/sources.py", "release", "gh-issue"),
    }:
        risk = "high"
    return gate, risk


def _shell_rules(line):
    """Classify one executable shell command; comments and quoted examples are not commands."""
    if not line.strip() or line.lstrip().startswith("#"):
        return set()
    try:
        words = shlex.split(line, comments=True)
    except ValueError:
        return set()
    if not words:
        return set()
    if words[:3] == ["gh", "label", "delete"]:
        return {"gh-label"}
    if len(words) >= 3 and words[:2] == ["gh", "issue"] and words[2] in _GH_ACTIONS["issue"]:
        return {"gh-issue"}
    if len(words) >= 3 and words[:2] == ["gh", "pr"] and words[2] in _GH_ACTIONS["pr"]:
        return {"gh-pr"}
    if len(words) >= 3 and words[:2] == ["gh", "project"] and words[2] in _GH_ACTIONS["project"]:
        return {"gh-project"}
    if words[:2] == ["gh", "api"] and any(w.upper() in {"POST", "PATCH", "PUT", "DELETE"} for w in words):
        return {"gh-api-write"}
    if words[:2] == ["git", "push"]:
        return {"git-push"}
    if words and words[0] == "git" and any(w in {"-D", "--hard", "remove", "rm", "tag"} for w in words[1:]):
        return {"git-destructive"}
    if words and words[0] == "rm" and any(w.startswith("-r") or w.startswith("-R") for w in words[1:]):
        return {"fs-rmtree"}
    return set()


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
    command_call = name in {"subprocess.run", "subprocess.Popen", "run", "git", "_run", "_run_gh", "_retry_gh", "_gh_json"} or name.endswith("._run")
    command = tokens[:]
    if command_call:
        if "gh" in command:
            command = command[command.index("gh") + 1:]
        if name.endswith("._run") or name in {"_run", "_run_gh", "_gh_json"}:
            command = tokens
        if len(command) >= 2 and command[0] in _GH_ACTIONS and command[1] in _GH_ACTIONS[command[0]]:
            rules.add("gh-" + ("pr" if command[0] == "pr" else command[0]))
        if command and command[0] == "api" and any(x in {"post", "patch", "put", "delete"} for x in command):
            rules.add("gh-api-write")
    if ("graphql" in command or name.endswith("_graphql")) and any("mutation" in s.lower() for s in strings):
        rules.add("graphql-mutation")
    git_runner = name in {"git", "gitc", "_git", "_run_git"}
    git = []
    if command_call and "git" in tokens:
        git = tokens[tokens.index("git") + 1:]
    elif git_runner:
        git = tokens
    elif command_call and command and command[0] in {"branch", "reset", "worktree", "tag"}:
        # These verbs occur only in git's CLI grammar; `run` is the repository's git/gh runner.
        git = command
    if git:
        if "push" in git:
            rules.add("git-push")
        if any(x in git for x in ("-d", "-D", "--hard", "remove", "rm", "tag", "--force", "--force-with-lease")) or any(x.startswith("--force-with-lease=") for x in git):
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
        if path.suffix == ".sh":
            for line in text.splitlines():
                for rule in _shell_rules(line):
                    key = (path.relative_to(root).as_posix(), "<script>", rule)
                    groups[key] = groups.get(key, 0) + 1
        else:
            tree = ast.parse(text, filename=str(path))
            values = _values(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                matching = [(start, name) for start, end, name in owners if start <= node.lineno <= end]
                function = max(matching, default=(0, "<module>"))[1]
                for rule in _rules_for_call(node, values):
                    key = (path.relative_to(root).as_posix(), function, rule)
                    groups[key] = groups.get(key, 0) + 1
        for (relpath, function, rule), count in groups.items():
            gate, risk = _metadata(relpath, function, rule)
            found.append({"path": relpath, "function": function, "rule": rule, "count": count,
                          "gate": gate, "risk": risk})
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
    print("# Write-surface inventory\n\n"
          "The `check` command ratchets tracked Python and shell write sites. Control: in a temporary "
          "tracked shell file add `gh label delete legacy`, run `python3 tools/readiness/write_surface.py "
          "check . docs/launch/write-surface.json`, and see it fail as a new `gh-label` site; remove the "
          "line before the green run.\n\n| Path | Function | Rule | Count | Gate | Risk |\n|---|---|---|---:|---|---|")
    for e in entries: print("| {path} | {function} | {rule} | {count} | {gate} | {risk} |".format(**e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

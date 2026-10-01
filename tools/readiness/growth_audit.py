#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Inventory durable Sigma stores without discovering arbitrary host paths.

USAGE: growth_audit.py REPOSITORY [--json OUT] [--measure-host ROOT]

The scanner is deliberately static and deterministic.  Host measurement is an
explicit, one-root opt-in and refuses the home directory itself.
"""

import argparse
import ast
import json
import os
import re
from pathlib import Path


_WRITER_METHODS = {"write_text", "write_bytes", "mkdir", "touch"}
_SKILL_EVIDENCE = re.compile(r"\.sdlc/evidence(?:/[^\s`'\")]+)?")

# These functions intentionally hide their file naming behind one safe writer
# API.  Resolving their destination from a caller would make the inventory
# depend on every caller keeping an implementation detail, so pin the public
# writer seam and the patterns its own path helpers guarantee instead.
_HELPER_STORES = {
    "skills/agrim-loop/scripts/ledger.py": {
        "append": (
            ".sdlc/ledger/entries/<actor>-<writer>.jsonl",
            ".sdlc/events/<actor>-<writer>.jsonl",
        ),
    },
    "skills/agrim-loop/scripts/actionlog.py": {
        "append": (".sdlc/state/log/<goal>.jsonl",),
    },
    "skills/agrim-loop/scripts/timing_store.py": {
        "append": (".sdlc/state/time/<goal>/<writer>.jsonl",),
        "append_session": (
            ".sdlc/state/time/_sessions/<session>/turns.jsonl",
            ".sdlc/state/time/_sessions/<session>/<writer>.jsonl",
        ),
    },
    "skills/agrim-loop/scripts/witness.py": {
        "record": (".sdlc/state/witness/<goal>.jsonl",),
    },
}


def _expression_path(node):
    """Return a static path expression, or ``None`` when it is not knowable."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return {"sdlc_dir": ".sdlc", "goal": "<goal>"}.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("<goal>")
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = _expression_path(node.left), _expression_path(node.right)
        if left is not None and right is not None:
            return left.rstrip("/") + "/" + right.lstrip("/")
        return None
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "Path" and node.args:
            return _expression_path(node.args[0])
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "home"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "Path"):
            return "<home>"
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            owner = node.func.value
            if (isinstance(owner, ast.Attribute) and owner.attr == "environ"
                    and isinstance(owner.value, ast.Name) and owner.value.id == "os" and node.args):
                return _environment_root(node.args[0])
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "getenv"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.args):
            return _environment_root(node.args[0])
    if isinstance(node, ast.Subscript):
        owner = node.value
        if (isinstance(owner, ast.Attribute) and owner.attr == "environ"
                and isinstance(owner.value, ast.Name) and owner.value.id == "os"):
            return _environment_root(node.slice)
    return None


def _environment_root(node):
    key = _expression_path(node)
    return {"HOME": "<home>", "CLAUDE_CONFIG_DIR": "<claude-config>",
            "CODEX_HOME": "<codex-home>"}.get(key)


def _normalise(pattern, directory=False):
    """Make goal and review-copy variants comparable across writers."""
    parts = []
    for part in pattern.replace("\\", "/").split("/"):
        parts.append("<goal>" if part.isdigit() else part)
    value = "/".join(parts)
    value = re.sub(r"/rv[^/]*/wt(?:/|$)", "/rv*/wt", value)
    if value.startswith("<home>/.sigma-ops"):
        value = "<sigma-ops>" + value[len("<home>/.sigma-ops"):]
    if directory and not value.endswith("/") and not value.endswith("/wt"):
        value += "/"
    return value


def _row(pattern, source, path, line, directory=False):
    if pattern is None:
        return None
    pattern = _normalise(pattern, directory=directory)
    if not (pattern.startswith(".sdlc/") or pattern.startswith("<home>/")
            or pattern.startswith("<claude-config>/") or pattern.startswith("<codex-home>/")
            or pattern.startswith("<sigma-ops>/")):
        return None
    return {"pattern": pattern, "source": source, "writer": f"{path}:{line}"}


def _code_rows(root, path):
    """Find the pinned stdlib and git writer forms in one non-test Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    rel = path.relative_to(root).as_posix()
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        pattern = None
        directory = False
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and "a" in str(node.args[1].value):
                pattern = _expression_path(node.args[0])
        elif isinstance(node.func, ast.Attribute) and node.func.attr in _WRITER_METHODS:
            pattern = _expression_path(node.func.value)
            directory = node.func.attr == "mkdir"
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "copytree":
            if len(node.args) >= 2:
                pattern, directory = _expression_path(node.args[1]), True
        elif isinstance(node.func, ast.Name) and node.func.id == "copytree" and len(node.args) >= 2:
            pattern, directory = _expression_path(node.args[1]), True
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "run" and node.args:
            command = node.args[0]
            if isinstance(command, (ast.List, ast.Tuple)):
                words = command.elts
                literal = [item.value if isinstance(item, ast.Constant) else None for item in words]
                if literal[:3] == ["git", "worktree", "add"] and len(words) >= 4:
                    pattern, directory = _expression_path(words[3]), True
                elif literal[:2] == ["git", "clone"] and len(words) >= 4:
                    pattern, directory = _expression_path(words[-1]), True
        row = _row(pattern, "code", rel, node.lineno, directory=directory)
        if row:
            rows.append(row)
    return rows


def _helper_rows(root, path):
    """Resolve durable stores whose public writer deliberately owns its path."""
    rel = path.relative_to(root).as_posix()
    functions = _HELPER_STORES.get(rel)
    if not functions:
        return []
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    rows = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in functions:
            continue
        writer_line = next((call.lineno for call in ast.walk(node)
                            if isinstance(call, ast.Call)
                            and isinstance(call.func, ast.Attribute)
                            and call.func.attr == "open"), None)
        if writer_line is None:
            continue
        for pattern in functions[node.name]:
            rows.append({"pattern": pattern, "source": "code", "writer": f"{rel}:{writer_line}"})
    return rows


def _skill_rows(root, path):
    rel = path.relative_to(root).as_posix()
    rows = []
    for line, text in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        match = _SKILL_EVIDENCE.search(text)
        if match and "wt" in match.group(0):
            row = _row(match.group(0), "skill-prose", rel, line)
            if row:
                rows.append(row)
    return rows


def scan(root):
    """Return deterministic code and skill-prose store rows below ``root``."""
    root = Path(root)
    rows = []
    for path in sorted(root.rglob("*.py")):
        if "tests" not in path.relative_to(root).parts:
            rows.extend(_code_rows(root, path))
            rows.extend(_helper_rows(root, path))
    skills = root / "skills"
    if skills.exists():
        for path in sorted(skills.rglob("*.md")):
            rows.extend(_skill_rows(root, path))
    return sorted(rows, key=lambda row: (row["pattern"], row["writer"], row["source"]))


def _size(path):
    """Count bytes below one supplied root without following symlinks."""
    total = 0
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                total += _size(entry.path)
            elif entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
    return total


def _named_host_roots(home, environ):
    """Return the only host roots this audit is allowed to traverse."""
    return {
        "<sigma-ops>": home / ".sigma-ops",
        "<claude-config>": Path(environ.get("CLAUDE_CONFIG_DIR", home / ".claude")),
        "<codex-home>": Path(environ.get("CODEX_HOME", home / ".codex")),
    }


def measure_host(root, *, home=None, environ=None):
    """Measure one named opted-in host root; the home root is always refused."""
    root = Path(root).resolve()
    home = Path.home().resolve() if home is None else Path(home).resolve()
    environ = os.environ if environ is None else environ
    if root == home:
        raise ValueError("refusing to measure the home directory itself")
    label = next((name for name, candidate in _named_host_roots(home, environ).items()
                  if root == candidate.resolve()), None)
    if label is None:
        raise ValueError("host measurement root must be a named configuration root")
    if not root.is_dir():
        raise ValueError("host measurement root must be an existing directory")
    return {"path": label, "size_bytes": _size(root)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--measure-host", type=Path)
    args = parser.parse_args(argv)
    result = {"rows": scan(args.repository)}
    if args.measure_host:
        result["host_measurement"] = measure_host(args.measure_host)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

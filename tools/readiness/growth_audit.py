#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Inventory durable Sigma stores without discovering arbitrary host paths.

USAGE: growth_audit.py REPOSITORY [--json OUT] [--measure-sdlc] [--measure-host ROOT]

The scanner is deliberately static and deterministic. Repository `.sdlc`
measurement is explicit and records the checkout SHA; host measurement is an
explicit, one-root opt-in and refuses the home directory itself.
"""

import argparse
import ast
import json
import os
import re
import subprocess
from pathlib import Path


_WRITER_METHODS = {"write_text", "write_bytes", "mkdir", "touch"}
_SKILL_PATH = re.compile(r"\.sdlc(?:/[A-Za-z0-9_.<>*${}/-]+)?")


def _placeholder(name):
    """Give every unresolved function argument a stable, reviewable name."""
    aliases = {"who": "actor", "actor_name": "actor", "run": "writer"}
    return "<%s>" % aliases.get(name, name)


def _expression_path(node, env=None, functions=None, module=None, depth=0):
    """Return every statically traceable destination for one expression.

    ``functions`` is discovered from the repository on every scan.  It is
    deliberately not a list of blessed writer APIs: a newly-added helper that
    returns a path is followed as soon as its caller is scanned.
    """
    env = {} if env is None else env
    functions = {} if functions is None else functions
    if depth > 12:
        return set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        if node.id in env:
            return set(env[node.id])
        constant = functions.get(module, {}).get("__constants__", {}).get(node.id)
        if constant is not None:
            return _expression_path(constant, env, functions, module, depth + 1)
        return {_placeholder(node.id)}
    if isinstance(node, ast.JoinedStr):
        parts = {""}
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                values = {value.value}
            elif isinstance(value, ast.FormattedValue):
                values = _expression_path(value.value, env, functions, module, depth + 1) or {"<value>"}
            else:
                return set()
            # A formatted branch can resolve to more than one durable suffix.
            # Keep every combination in sorted order instead of selecting an
            # arbitrary set element whose value changes with PYTHONHASHSEED.
            parts = {prefix + suffix for prefix in parts for suffix in sorted(values)}
        return parts
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _expression_path(node.left, env, functions, module, depth + 1)
        right = _expression_path(node.right, env, functions, module, depth + 1)
        return {a.rstrip("/") + "/" + b.lstrip("/") for a in left for b in right}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _expression_path(node.left, env, functions, module, depth + 1)
        right = _expression_path(node.right, env, functions, module, depth + 1)
        return {a + b for a in left for b in right}
    if isinstance(node, ast.IfExp):
        return (_expression_path(node.body, env, functions, module, depth + 1)
                | _expression_path(node.orelse, env, functions, module, depth + 1))
    if isinstance(node, ast.Call):
        if ((isinstance(node.func, ast.Name) and node.func.id in {"Path", "str"})
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "Path")) and node.args:
            return _expression_path(node.args[0], env, functions, module, depth + 1)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "home"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "Path"):
            return {"<home>"}
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            owner = node.func.value
            if (isinstance(owner, ast.Attribute) and owner.attr == "environ"
                    and isinstance(owner.value, ast.Name) and owner.value.id == "os" and node.args):
                return _environment_root(node.args[0], env, functions, module)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "getenv"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.args):
            return _environment_root(node.args[0], env, functions, module)
        target_module, function = module, None
        if isinstance(node.func, ast.Name):
            function = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            target_module, function = node.func.value.id, node.func.attr
        fn = functions.get(target_module, {}).get(function)
        if fn is not None:
            values = []
            for argument in node.args:
                values.append(_expression_path(argument, env, functions, module, depth + 1))
            bound = {arg.arg: (values[index] if index < len(values) else {_placeholder(arg.arg)})
                     for index, arg in enumerate(fn.args.args)}
            for _ in range(4):
                changed = False
                for candidate in ast.walk(fn):
                    if not isinstance(candidate, (ast.Assign, ast.AnnAssign)) or candidate.value is None:
                        continue
                    targets = candidate.targets if isinstance(candidate, ast.Assign) else [candidate.target]
                    resolved = _expression_path(candidate.value, bound, functions, target_module, depth + 1)
                    for target in targets:
                        if isinstance(target, ast.Name) and resolved and bound.get(target.id) != resolved:
                            bound[target.id] = resolved
                            changed = True
                if not changed:
                    break
            returns = []
            for candidate in ast.walk(fn):
                if isinstance(candidate, ast.Return) and candidate.value is not None:
                    returns.extend(_expression_path(candidate.value, bound, functions, target_module, depth + 1))
            return set(returns)
    if isinstance(node, ast.Subscript):
        owner = node.value
        if (isinstance(owner, ast.Attribute) and owner.attr == "environ"
                and isinstance(owner.value, ast.Name) and owner.value.id == "os"):
            return _environment_root(node.slice, env, functions, module)
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return {_placeholder(node.slice.value)}
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return {value.rsplit("/", 1)[0] for value in _expression_path(node.value, env, functions, module, depth + 1)
                if "/" in value}
    return set()


def _environment_root(node, env, functions, module):
    keys = _expression_path(node, env, functions, module)
    names = {"HOME": "<home>", "CLAUDE_CONFIG_DIR": "<claude-config>",
             "CODEX_HOME": "<codex-home>"}
    return {names[key] for key in sorted(keys) if key in names}


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


def _scope(path, tree, function, functions, module, seed=None):
    """Resolve local path aliases by the writer's source, not a manual map."""
    env = ({arg.arg: ({".sdlc"} if arg.arg == "sdlc_dir" else {_placeholder(arg.arg)})
            for arg in function.args.args} if seed is None else dict(seed))
    for _ in range(4):
        changed = False
        for node in ast.walk(function):
            targets, value = [], None
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
            if value is None:
                continue
            values = _expression_path(value, env, functions, module)
            for target in targets:
                if isinstance(target, ast.Name) and values and env.get(target.id) != values:
                    env[target.id] = values
                    changed = True
        if not changed:
            break
    return env


def _argument_name(node):
    """Return the parameter name passed through an internal writer unchanged."""
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"str", "Path"} and node.args):
        return _argument_name(node.args[0])
    return None


def _writer_argument_indices(function, functions=None, module=None, seen=None):
    """Discover which helper arguments become destinations in its own source."""
    names = [arg.arg for arg in function.args.args]
    found = set()
    functions = {} if functions is None else functions
    seen = set() if seen is None else seen
    marker = (module, function.name)
    if marker in seen:
        return found
    seen = seen | {marker}
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target = None
        if isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
            target = node.args[0]
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "open":
            target = node.args[0] if isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.args else node.func.value
        elif isinstance(node.func, ast.Attribute) and node.func.attr in _WRITER_METHODS:
            target = node.func.value
        elif ((isinstance(node.func, ast.Name) and node.func.id == "copytree")
              or (isinstance(node.func, ast.Attribute) and node.func.attr == "copytree")) and len(node.args) >= 2:
            target = node.args[1]
        name = _argument_name(target) if target is not None else None
        if name in names:
            found.add(names.index(name))
        target_module, helper = module, None
        if isinstance(node.func, ast.Name):
            helper = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            target_module, helper = node.func.value.id, node.func.attr
        callee = functions.get(target_module, {}).get(helper)
        if callee is not None:
            for index in _writer_argument_indices(callee, functions, target_module, seen):
                if index < len(node.args):
                    forwarded = _argument_name(node.args[index])
                    if forwarded in names:
                        found.add(names.index(forwarded))
    return found


def _write_target(node):
    """Return ``(destination-expression, is-directory)`` for a direct writer."""
    if isinstance(node.func, ast.Name) and node.func.id == "open" and len(node.args) >= 2:
        mode = node.args[1].value if isinstance(node.args[1], ast.Constant) else ""
        return (node.args[0], False) if any(flag in str(mode) for flag in ("a", "w", "x", "+")) else (None, False)
    if isinstance(node.func, ast.Attribute) and node.func.attr == "open":
        mode = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
            return (node.args[0], False) if node.args else (None, False)
        return (node.func.value, False) if any(flag in str(mode) for flag in ("a", "w", "x", "+")) else (None, False)
    if isinstance(node.func, ast.Attribute) and node.func.attr in {"replace", "rename"}:
        # Path.rename(destination) and Path.replace(destination) take one
        # argument.  The os.rename/os.replace forms take source, destination.
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
            return (node.args[1], False) if len(node.args) >= 2 else (None, False)
        return (node.args[0], False) if node.args else (None, False)
    if isinstance(node.func, ast.Attribute) and node.func.attr in _WRITER_METHODS:
        return node.func.value, node.func.attr == "mkdir"
    if ((isinstance(node.func, ast.Name) and node.func.id == "copytree")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "copytree")) and len(node.args) >= 2:
        return node.args[1], True
    return None, False


def _helper_destinations(function, env, functions, module, depth=0):
    """Follow a helper's local aliases and nested writer helpers without a registry."""
    if depth > 8:
        return set()
    scoped = _scope(None, None, function, functions, module, seed=env)
    paths = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target, _directory = _write_target(node)
        if target is not None:
            paths |= _expression_path(target, scoped, functions, module)
        target_module, helper = module, None
        if isinstance(node.func, ast.Name):
            helper = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            target_module, helper = node.func.value.id, node.func.attr
        nested = functions.get(target_module, {}).get(helper)
        if nested is not None and nested is not function and _has_direct_writer(nested):
            arguments = [_expression_path(argument, scoped, functions, module) for argument in node.args]
            bound = {arg.arg: (arguments[index] if index < len(arguments) else {_placeholder(arg.arg)})
                     for index, arg in enumerate(nested.args.args)}
            paths |= _helper_destinations(nested, bound, functions, target_module, depth + 1)
    return paths


def _has_direct_writer(function):
    return any(isinstance(node, ast.Call) and _write_target(node)[0] is not None
               for node in ast.walk(function))


def _has_immediate_writer_helper(function, functions, module):
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target_module, helper = module, None
        if isinstance(node.func, ast.Name):
            helper = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            target_module, helper = node.func.value.id, node.func.attr
        nested = functions.get(target_module, {}).get(helper)
        if nested is not None and _has_direct_writer(nested):
            return True
    return False


def _writes_anything(function, functions, module, seen=None):
    """Whether source-level call graph reaches a local durable writer."""
    cache = functions.setdefault("__growth-writer-cache__", {})
    marker = (module, function.name)
    if marker in cache:
        return cache[marker]
    seen = set() if seen is None else seen
    if marker in seen:
        return False
    next_seen = seen | {marker}
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target, _directory = _write_target(node)
        if target is not None:
            cache[marker] = True
            return True
        target_module, helper = module, None
        if isinstance(node.func, ast.Name):
            helper = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            target_module, helper = node.func.value.id, node.func.attr
        nested = functions.get(target_module, {}).get(helper)
        if nested is not None and _writes_anything(nested, functions, target_module, next_seen):
            cache[marker] = True
            return True
    cache[marker] = False
    return False


def _code_rows(root, path, functions):
    """Find the pinned stdlib and git writer forms in one non-test Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    rel = path.relative_to(root).as_posix()
    module = path.stem
    rows = []
    for function in (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        calls = list(ast.walk(function))
        if not (_has_direct_writer(function) or _has_immediate_writer_helper(function, functions, module)):
            continue
        env = _scope(path, tree, function, functions, module)
        for node in calls:
            if not isinstance(node, ast.Call):
                continue
            patterns, directory = set(), False
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                if (len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                        and any(flag in str(node.args[1].value) for flag in ("a", "w", "x", "+"))):
                    patterns = _expression_path(node.args[0], env, functions, module)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "open":
                mode = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    patterns = _expression_path(node.args[0], env, functions, module) if node.args else set()
                elif any(flag in str(mode) for flag in ("a", "w", "x", "+")):
                    patterns = _expression_path(node.func.value, env, functions, module)
            elif isinstance(node.func, ast.Attribute) and node.func.attr in _WRITER_METHODS:
                patterns = _expression_path(node.func.value, env, functions, module)
                directory = node.func.attr == "mkdir"
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {"replace", "rename"}:
                target, directory = _write_target(node)
                patterns = _expression_path(target, env, functions, module) if target is not None else set()
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "copytree" and len(node.args) >= 2:
                patterns, directory = _expression_path(node.args[1], env, functions, module), True
            elif isinstance(node.func, ast.Name) and node.func.id == "copytree" and len(node.args) >= 2:
                patterns, directory = _expression_path(node.args[1], env, functions, module), True
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "run" and node.args:
                command = node.args[0]
                if isinstance(command, (ast.List, ast.Tuple)):
                    literal = [item.value if isinstance(item, ast.Constant) else None for item in command.elts]
                    if literal[:3] == ["git", "worktree", "add"] and len(command.elts) >= 4:
                        patterns, directory = _expression_path(command.elts[3], env, functions, module), True
                    elif literal[:2] == ["git", "clone"] and len(command.elts) >= 4:
                        patterns, directory = _expression_path(command.elts[-1], env, functions, module), True
            target_module, helper = module, None
            if isinstance(node.func, ast.Name):
                helper = node.func.id
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                target_module, helper = node.func.value.id, node.func.attr
            target = functions.get(target_module, {}).get(helper)
            if target is not None:
                for index in _writer_argument_indices(target, functions, target_module):
                    if index < len(node.args):
                        patterns |= _expression_path(node.args[index], env, functions, module)
                arguments = [_expression_path(argument, env, functions, module) for argument in node.args]
                bound = {arg.arg: (arguments[index] if index < len(arguments) else {_placeholder(arg.arg)})
                         for index, arg in enumerate(target.args.args)}
                patterns |= _helper_destinations(target, bound, functions, target_module)
            for pattern in patterns:
                row = _row(pattern, "code", rel, node.lineno, directory=directory)
                if row:
                    rows.append(row)
    return rows


def _skill_rows(root, path):
    rel = path.relative_to(root).as_posix()
    rows = []
    for line, text in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        match = _SKILL_PATH.search(text)
        if match:
            row = _row(match.group(0), "skill-prose", rel, line, directory=match.group(0).endswith("/"))
            if row:
                rows.append(row)
    return rows


def scan(root):
    """Return deterministic code and skill-prose store rows below ``root``."""
    root = Path(root)
    paths = [path for path in sorted(root.rglob("*.py")) if "tests" not in path.relative_to(root).parts]
    functions = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        functions[path.stem] = {node.name: node for node in tree.body
                                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        functions[path.stem]["__constants__"] = {
            target.id: node.value for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
            and node.value is not None
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
    rows = []
    for path in paths:
        rows.extend(_code_rows(root, path, functions))
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


def measure_repository(repository):
    """Measure only this checkout's `.sdlc` and bind the result to its Git SHA."""
    repository = Path(repository).resolve()
    sdlc = repository / ".sdlc"
    if not sdlc.is_dir():
        raise ValueError("repository .sdlc directory does not exist")
    completed = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=False)
    revision = completed.stdout.strip()
    if completed.returncode != 0 or not revision:
        raise ValueError("repository revision is unavailable")
    return {"path": ".sdlc", "size_bytes": _size(sdlc), "revision": revision}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--measure-host", type=Path)
    parser.add_argument("--measure-sdlc", action="store_true")
    args = parser.parse_args(argv)
    result = {"rows": scan(args.repository)}
    if args.measure_host:
        result["host_measurement"] = measure_host(args.measure_host)
    if args.measure_sdlc:
        result["repository_measurement"] = measure_repository(args.repository)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

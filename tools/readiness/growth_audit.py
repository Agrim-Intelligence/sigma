#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Inventory durable Sigma stores without discovering arbitrary host paths.

USAGE: growth_audit.py REPOSITORY [--json OUT] [--measure-sdlc] [--measure-host ROOT]
                       [--b6-issue N]

Per-slice dispositions live in REPOSITORY/docs/launch/dispositions/*.json and are
validated on every run; a bad file exits 2 before anything is written.

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
import sys
from pathlib import Path


DISPOSITION_DIR = Path("docs") / "launch" / "dispositions"
_DISPOSITION_KEYS = ("pattern", "issue", "decision", "pruner_or_cap", "evidence")
_DISPOSITION_OPTIONAL = ("unscanned",)


class DispositionError(ValueError):
    """A disposition file the audit refuses to interpret."""


_WRITER_METHODS = {"write_text", "write_bytes", "mkdir", "touch"}
_SKILL_PATH = re.compile(r"\.sdlc(?:/[A-Za-z0-9_.<>*${}/-]+)?")


def _placeholder(name):
    """Give every unresolved function argument a stable, reviewable name."""
    aliases = {"who": "actor", "actor_name": "actor", "run": "writer", "stem": "goal"}
    return "<%s>" % aliases.get(name, name)


def _expression_path(node, env=None, functions=None, module=None, depth=0, resolving=None):
    """Return every statically traceable destination for one expression.

    ``functions`` is discovered from the repository on every scan.  It is
    deliberately not a list of blessed writer APIs: a newly-added helper that
    returns a path is followed as soon as its caller is scanned.
    """
    env = {} if env is None else env
    functions = {} if functions is None else functions
    resolving = set() if resolving is None else resolving
    if depth > 12:
        return set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        if node.id in env:
            return set(env[node.id])
        constant = functions.get(module, {}).get("__constants__", {}).get(node.id)
        if constant is not None:
            return _expression_path(constant, env, functions, module, depth + 1, resolving)
        return {_placeholder(node.id)}
    if isinstance(node, ast.Attribute):
        # Instance state is a common local-source path carrier: constructors
        # retain ``sdlc_dir`` as ``self.sdlc_dir`` and methods subsequently
        # construct journey/goal destinations from it.  The class-init
        # assignments are collected by ``scan`` and made available as stable
        # aliases here; do not treat arbitrary object attributes as paths.
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            value = env.get("self." + node.attr)
            if value is not None:
                return set(value)
    if isinstance(node, ast.JoinedStr):
        parts = {""}
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                values = {value.value}
            elif isinstance(value, ast.FormattedValue):
                values = _expression_path(value.value, env, functions, module, depth + 1, resolving) or {"<value>"}
            else:
                return set()
            # A formatted branch can resolve to more than one durable suffix.
            # Keep every combination in sorted order instead of selecting an
            # arbitrary set element whose value changes with PYTHONHASHSEED.
            parts = {prefix + suffix for prefix in parts for suffix in sorted(values)}
        return parts
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _expression_path(node.left, env, functions, module, depth + 1, resolving)
        right = _expression_path(node.right, env, functions, module, depth + 1, resolving)
        return {a.rstrip("/") + "/" + b.lstrip("/") for a in left for b in right}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _expression_path(node.left, env, functions, module, depth + 1, resolving)
        right = _expression_path(node.right, env, functions, module, depth + 1, resolving)
        return {a + b for a in left for b in right}
    if isinstance(node, ast.IfExp):
        return (_expression_path(node.body, env, functions, module, depth + 1, resolving)
                | _expression_path(node.orelse, env, functions, module, depth + 1, resolving))
    if isinstance(node, ast.Call):
        if ((isinstance(node.func, ast.Name) and node.func.id in {"Path", "str"})
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "Path")) and node.args:
            return _expression_path(node.args[0], env, functions, module, depth + 1, resolving)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "home"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "Path"):
            return {"<home>"}
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            owner = node.func.value
            if (isinstance(owner, ast.Attribute) and owner.attr == "environ"
                    and isinstance(owner.value, ast.Name) and owner.value.id == "os" and node.args):
                return _environment_root(node.args[0], env, functions, module, resolving)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "getenv"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.args):
            return _environment_root(node.args[0], env, functions, module, resolving)
        target_module, function = module, None
        if isinstance(node.func, ast.Name):
            function = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            target_module, function = node.func.value.id, node.func.attr
        fn = functions.get(target_module, {}).get(function)
        marker = (target_module, function)
        if fn is not None and marker not in resolving:
            resolving.add(marker)
            try:
                values = []
                for argument in node.args:
                    values.append(_expression_path(argument, env, functions, module, depth + 1, resolving))
                bound = {arg.arg: (values[index] if index < len(values) else {_placeholder(arg.arg)})
                         for index, arg in enumerate(fn.args.args)}
                for _ in range(4):
                    changed = False
                    for candidate in ast.walk(fn):
                        if not isinstance(candidate, (ast.Assign, ast.AnnAssign)) or candidate.value is None:
                            continue
                        targets = candidate.targets if isinstance(candidate, ast.Assign) else [candidate.target]
                        resolved = _expression_path(candidate.value, bound, functions, target_module,
                                                    depth + 1, resolving)
                        for target in targets:
                            if isinstance(target, ast.Name) and resolved and bound.get(target.id) != resolved:
                                bound[target.id] = resolved
                                changed = True
                    if not changed:
                        break
                returns = []
                for candidate in ast.walk(fn):
                    if isinstance(candidate, ast.Return) and candidate.value is not None:
                        returns.extend(_expression_path(candidate.value, bound, functions, target_module,
                                                        depth + 1, resolving))
                return set(returns)
            finally:
                resolving.remove(marker)
    if isinstance(node, ast.Subscript):
        owner = node.value
        if (isinstance(owner, ast.Attribute) and owner.attr == "environ"
                and isinstance(owner.value, ast.Name) and owner.value.id == "os"):
            return _environment_root(node.slice, env, functions, module, resolving)
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return {_placeholder(node.slice.value)}
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return {value.rsplit("/", 1)[0] for value in _expression_path(node.value, env, functions, module, depth + 1, resolving)
                if "/" in value}
    return set()


def _environment_root(node, env, functions, module, resolving=None):
    keys = _expression_path(node, env, functions, module, resolving=resolving)
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
    # These aliases come only from ``self.attr = ...`` assignments in a
    # class's constructor.  They let method bodies remain real scan targets
    # without a hand-maintained map of particular classes or store names.
    for name, values in functions.get(module, {}).get("__self_attrs__", {}).items():
        env.setdefault("self." + name, set(values))
    class_method = getattr(function, "_growth_class_method", False)
    for _ in range(4):
        changed = False
        for node in ast.walk(function):
            targets, value = [], None
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
            if value is None:
                continue
            if class_method:
                # Class methods often carry API clients and call graph roots
                # in local aliases.  Their direct writer expressions only
                # need literal/path composition; following arbitrary calls
                # here makes a static scan recursively traverse clients.
                calls = [call for call in ast.walk(value) if isinstance(call, ast.Call)]
                if any(not ((isinstance(call.func, ast.Name) and call.func.id in {"Path", "str"})
                            or (isinstance(call.func, ast.Attribute) and call.func.attr == "Path"))
                           for call in calls):
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
    marker = (module, function.name)
    # The static helper graph does not vary by call site.  `_code_rows` asks
    # this question for every helper invocation it finds, and without a
    # top-level cache a large source module repeatedly walks the same callee
    # graph thousands of times.  Cache only fresh root queries: recursive
    # queries carry a cycle guard whose partial result depends on its caller.
    cache = functions.setdefault("__growth-writer-argument-cache__", {})
    if seen is None:
        cached = cache.get(marker)
        if cached is not None:
            return set(cached)
        result = _writer_argument_indices(function, functions, module, set())
        cache[marker] = frozenset(result)
        return result
    if marker in seen:
        return found
    # Recursive results are only reusable under the same cycle guard.  This
    # avoids the unsafe shortcut of memoizing a partial cycle walk globally,
    # while collapsing repeated sanitizer/helper traversals reached from the
    # same call graph context.
    contextual = functions.setdefault("__growth-writer-argument-context-cache__", {})
    context = (marker, frozenset(seen))
    cached = contextual.get(context)
    if cached is not None:
        return set(cached)
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
        # A helper that cannot reach any writer cannot turn one of this
        # function's arguments into a destination.  The reachability result
        # is cached for the scan, so this avoids recursively walking large
        # formatter/sanitizer graphs whose answer is necessarily empty.
        if (callee is not None
                and _writes_anything(callee, functions, target_module)):
            for index in _writer_argument_indices(callee, functions, target_module, seen):
                if index < len(node.args):
                    forwarded = _argument_name(node.args[index])
                    if forwarded in names:
                        found.add(names.index(forwarded))
    contextual[context] = frozenset(found)
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
    # This predicate is consulted once for the function itself and again for
    # every call site that might delegate to it.  Caching on the parsed AST
    # node keeps a source-wide class-method walk linear instead of repeatedly
    # walking large methods (notably the GitHub source) for each caller.
    cached = getattr(function, "_growth_has_direct_writer", None)
    if cached is not None:
        return cached
    value = any(isinstance(node, ast.Call) and _write_target(node)[0] is not None
                for node in ast.walk(function))
    function._growth_has_direct_writer = value
    return value


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
    root_query = seen is None
    seen = set() if root_query else seen
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
    # A False result reached with a non-empty guard may have been truncated
    # solely because a caller is already in this cycle.  It is not a global
    # fact: another call site can enter the same helper without that guard and
    # reach a writer through a different edge.  Cache negative reachability
    # only for a complete root traversal; positive reachability is valid from
    # every context and remains safely reusable above.
    if root_query:
        cache[marker] = False
    return False


def _code_rows(root, path, functions):
    """Find the pinned stdlib and git writer forms in one non-test Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    rel = path.relative_to(root).as_posix()
    module = path.stem
    rows = []
    # ``ast.walk(function)`` below intentionally reaches a nested local helper
    # when its enclosing method invokes it.  Enumerating that helper again
    # would re-walk the same subtree for every enclosing function, which turns
    # a real repository scan into quadratic work.  Class methods are the
    # missing layer here: include them alongside module functions.
    functions_here = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    functions_here.extend(
        method for cls in tree.body if isinstance(cls, ast.ClassDef)
        for method in cls.body if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for function in functions_here:
        calls = list(ast.walk(function))
        class_method = getattr(function, "_growth_class_method", False)
        if not (_has_direct_writer(function)
                or (not class_method and _has_immediate_writer_helper(function, functions, module))):
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
            # The enclosing class method's AST already includes local nested
            # helpers, so its direct calls are emitted above.  Following every
            # ``self.method`` as another interprocedural graph edge caused the
            # repository audit to revisit the same very large class methods at
            # each call site.  Module-level helper following remains intact.
            # Most call sites target ordinary helpers that cannot reach a
            # durable writer.  Descending into each of those graphs was the
            # dominant cost of a repository scan.  The cached reachability
            # check retains every helper that can write (including a
            # transitive writer) while avoiding alias resolution for the
            # overwhelmingly common non-writer case.
            if (target is not None and not class_method
                    and _writes_anything(target, functions, target_module)):
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


def function_table(paths):
    """Return the per-module function/constant/instance-alias table `scan` resolves paths with.

    Split out of `scan` so a second reader of the same source (tools/readiness/shared_paths.py)
    resolves a path expression exactly as the write-site scan does, rather than reimplementing it.
    """
    functions = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        # Keep the callable module surface to module functions.  Class methods
        # are scanned directly below; registering their unqualified names here
        # would make an unrelated module call such as ``run()`` resolve to an
        # arbitrary class's ``run`` method and create a false recursive edge.
        # A nested helper is likewise walked with its enclosing method.
        module_functions = {node.name: node for node in tree.body
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        # Resolve class-init instance path aliases once, from their own
        # constructor arguments.  Methods then get the same symbolic values
        # (not host paths) no matter which class owns the durable writer.
        class_attrs = {}
        for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for method in cls.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method._growth_class_method = True
            init = next((node for node in cls.body
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name == "__init__"), None)
            if init is None:
                continue
            aliases = {arg.arg: ({".sdlc"} if arg.arg == "sdlc_dir" else {_placeholder(arg.arg)})
                       for arg in init.args.args}
            for _ in range(4):
                changed = False
                for node in ast.walk(init):
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                        continue
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        # Constructor state contains clients, configuration and
                        # arbitrary expensive calls.  Only path-carrier names
                        # can participate in a filesystem destination; keeping
                        # that boundary also prevents a scan from executing an
                        # unbounded interprocedural resolution of constructors.
                        if not (isinstance(target, ast.Attribute)
                                and isinstance(target.value, ast.Name)
                                and target.value.id == "self"
                                and target.attr.endswith(("_dir", "_path"))):
                            continue
                        values = _expression_path(node.value, aliases,
                                                  {path.stem: module_functions}, path.stem)
                        if values and aliases.get("self." + target.attr) != values:
                            aliases["self." + target.attr] = values
                            class_attrs[target.attr] = values
                            changed = True
                if not changed:
                    break
        module_functions["__self_attrs__"] = class_attrs
        functions[path.stem] = module_functions
        functions[path.stem]["__constants__"] = {
            target.id: node.value for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
            and node.value is not None
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
    return functions


def scan(root):
    """Return deterministic code and skill-prose store rows below ``root``."""
    root = Path(root)
    paths = [path for path in sorted(root.rglob("*.py")) if "tests" not in path.relative_to(root).parts]
    functions = function_table(paths)
    rows = []
    for path in paths:
        rows.extend(_code_rows(root, path, functions))
    skills = root / "skills"
    if skills.exists():
        for path in sorted(skills.rglob("*.md")):
            rows.extend(_skill_rows(root, path))
    unique = {(row["pattern"], row["source"], row["writer"]): row for row in rows}
    return sorted(unique.values(), key=lambda row: (row["pattern"], row["writer"], row["source"]))


def _no_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate JSON object key")
    return dict(pairs)


def load_dispositions(repository, rows):
    """Return {pattern: entry} from docs/launch/dispositions/*.json, or refuse loudly.

    No directory means no dispositions.  Every entry in it must be a `*.json` file (a
    stray name is refused, never skipped; dotfiles are ignored); each file is a JSON array of objects with
    the keys in `_DISPOSITION_KEYS` (all non-empty strings, `issue` shaped `#<digits>`)
    and optionally `"unscanned": true`.  A repeated pattern (within or across files) or
    a pattern the scan does not produce raises DispositionError naming the file and
    pattern.  `unscanned: true` waives only the last check, for a source-proven store
    whose writer the scan cannot see; `evidence` must name that writer.
    """
    folder = Path(repository) / DISPOSITION_DIR
    if not folder.is_dir():
        return {}
    known = {row["pattern"] for row in rows}
    found, owner = {}, {}
    for path in sorted(folder.iterdir()):
        name = "%s/%s" % (DISPOSITION_DIR.as_posix(), path.name)
        if path.name.startswith("."):  # editor/OS litter such as .DS_Store, never a slice file
            continue
        if not path.name.endswith(".json"):
            raise DispositionError("%s: not a .json disposition file; rename or delete it" % name)
        try:
            entries = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
        except (OSError, ValueError) as exc:  # includes UnicodeDecodeError and JSON errors
            raise DispositionError("%s: unreadable or invalid JSON (%s)" % (name, exc))
        if not isinstance(entries, list):
            raise DispositionError("%s: top level must be a JSON array of entries" % name)
        for index, entry in enumerate(entries):
            where = "%s[%d]" % (name, index)
            if (not isinstance(entry, dict) or not set(_DISPOSITION_KEYS) <= set(entry)
                    or set(entry) - set(_DISPOSITION_KEYS) - set(_DISPOSITION_OPTIONAL)):
                raise DispositionError("%s: each entry needs the keys %s (and may add only %s)"
                                       % (where, ", ".join(_DISPOSITION_KEYS),
                                          ", ".join(_DISPOSITION_OPTIONAL)))
            if "unscanned" in entry and entry["unscanned"] is not True:
                raise DispositionError("%s: unscanned, when present, must be true" % where)
            for key in _DISPOSITION_KEYS:
                if not isinstance(entry[key], str) or not entry[key].strip():
                    raise DispositionError("%s: %s must be a non-empty string" % (where, key))
            if not re.fullmatch(r"#[0-9]+", entry["issue"]):
                raise DispositionError("%s: issue must look like #123" % where)
            pattern = entry["pattern"]
            if pattern in found:
                raise DispositionError("%s: duplicate pattern %r (also in %s); delete one"
                                       % (where, pattern, owner[pattern]))
            if pattern not in known and not entry.get("unscanned"):
                raise DispositionError(
                    "%s: pattern %r is not produced by the scan; fix the pattern, delete the "
                    "entry if its writer is gone, or set unscanned: true with the writer named "
                    "in evidence" % (where, pattern))
            found[pattern] = entry
            owner[pattern] = name
    return found


def b6_disposition(rows, issue, dispositions=None):
    """Preserve the blocker outcome for every path without a recorded disposition.

    A pattern with an entry in `dispositions` (from load_dispositions) is
    resolved; this inventory must not turn similarly shaped durable or host
    paths into an implied retention policy, so every other unique pattern is
    carried to the measured B6 issue.
    """
    resolved = dispositions or {}
    return {
        "issue": "#%s" % str(issue).lstrip("#"),
        "status": "filed",
        "unresolved_patterns": sorted({row["pattern"] for row in rows
                                       if row["pattern"] not in resolved}),
    }


def _pattern_size(repository, pattern):
    """Measure files covered by one repository store pattern, without overlap."""
    if not pattern.startswith(".sdlc/"):
        return None
    wildcard = re.sub(r"<[^>]+>", "*", pattern)
    files = set()
    for candidate in Path(repository).glob(wildcard):
        if candidate.is_file():
            files.add(candidate)
        elif candidate.is_dir():
            files.update(path for path in candidate.rglob("*") if path.is_file())
    return sum(path.stat().st_size for path in files)


def store_measurements(repository, rows, issue, dispositions=None):
    """Return an honest size/growth/disposition row for every unique pattern.

    The 10x/100x columns are linear projections of the observed bytes at the
    bound checkout, not a claimed forecast.  Host-root patterns are deliberately
    left unmeasured until an operator uses the separate explicit host gesture.
    """
    grouped = {}
    for row in rows:
        grouped.setdefault(row["pattern"], []).append(row["writer"])
    resolved = dispositions or {}
    result = []
    for pattern in sorted(grouped):
        writers = ", ".join(sorted(set(grouped[pattern])))
        size = _pattern_size(repository, pattern)
        if pattern in resolved:
            pruner = resolved[pattern]["pruner_or_cap"]
            decision = resolved[pattern]["decision"]
        else:
            pruner = "unknown (B6 #%s)" % str(issue).lstrip("#")
            decision = "B6 #%s disposition required" % str(issue).lstrip("#")
        result.append({
            "pattern": pattern,
            "growth_event": "writer invoked (%s)" % writers,
            "pruner_or_cap": pruner,
            "size_now_bytes": size,
            "size_10x_bytes": size * 10 if size is not None else None,
            "size_100x_bytes": size * 100 if size is not None else None,
            "decision": decision,
        })
    return result


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
    parser.add_argument("--b6-issue", help="filed B6 issue for patterns without a proven pruner")
    args = parser.parse_args(argv)
    result = {"rows": scan(args.repository)}
    try:
        dispositions = load_dispositions(args.repository, result["rows"])
    except DispositionError as exc:
        print("growth_audit.py: REFUSED: %s" % exc, file=sys.stderr)
        return 2
    for pattern, entry in sorted(dispositions.items()):
        if entry.get("unscanned") and pattern not in {row["pattern"] for row in result["rows"]}:
            print("growth_audit.py: note: unscanned disposition %r matched no scan row" % pattern,
                  file=sys.stderr)
    if args.measure_host:
        result["host_measurement"] = measure_host(args.measure_host)
    if args.measure_sdlc:
        result["repository_measurement"] = measure_repository(args.repository)
    if args.b6_issue:
        result["b6_disposition"] = b6_disposition(result["rows"], args.b6_issue, dispositions)
        if args.measure_sdlc:
            result["store_measurements"] = store_measurements(args.repository, result["rows"],
                                                                 args.b6_issue, dispositions)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

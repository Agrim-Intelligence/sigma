#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify the golden task set (`evals/golden/<id>/`): every committed task is internally sound.

USAGE:
  python3 evals/golden/verify.py              verify every task under this directory
  python3 evals/golden/verify.py --only ID    verify one task (a mistyped ID exits 2)
  python3 evals/golden/verify.py --help       print this usage and verify nothing

EXIT: 0 = every task green (or no tasks found, said loudly); 1 = at least one RED line;
2 = bad arguments, unreadable or invalid input, or an unsupported platform (Windows).

A task is a directory holding `task.json`, `repo/` (the start tree), `reference/` (the full fixed tree),
`allowed_paths.json`, `rubric.json` and `hidden/` (`files/`, `verify.json`, optional `naive/`). Each RED
line reads `RED <task> <property>: <detail>`. Properties checked: task-json, id, origin, visible_command,
hidden-sha256, verify-json, rubric, allowed_paths, symlink, hidden-on-start (hidden tests exit 1 on
`repo/`), hidden-on-reference (exit 0 on `reference/`), reference-diff (every added, changed or deleted
path is allowed), naive (hidden tests exit 1 on `repo/` plus `hidden/naive/`).

Runs go through the benchmark helpers in `tools/readiness/bench_tasks.py` (process-group kill on timeout,
scrubbed environment). `SIGMA_GOLDEN_TIMEOUT` (seconds, 1-600) overrides the 120 s per-run ceiling and is
test-only. Stdlib only; Unix only; no network, no model.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent
HELPER = GOLDEN.parents[1] / "tools" / "readiness" / "bench_tasks.py"
TIMEOUT = 120  # a ceiling for a hung hidden test, not a measured run time
KEYS = ("id", "kind", "origin", "prompt", "visible_command", "hidden_sha256", "trap")
ORIGIN = re.compile(r"planned|issue:[0-9]+")
bt = None  # the bench_tasks helper module, loaded by main()


class Unreadable(Exception):
    """An input verify.py cannot read or parse: exit 2, never a red line."""


def _junk(rel):
    parts = rel.split("/")
    return "__pycache__" in parts or rel.endswith(".pyc") or parts[-1] == ".DS_Store"


def _json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise Unreadable(f"{path}: {exc}")


def _walk(root):
    """({relative posix path: sha256} of regular files, [relative paths of symlinks]); junk is ignored."""
    files, links = {}, []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                links.append(path.relative_to(root).as_posix())
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if not path.is_symlink() and not _junk(rel):
                files[rel] = bt.file_sha256(path)
    return files, links


def _allowed(rel, allowed):
    return any(rel == entry or (entry.endswith("/") and rel.startswith(entry)) for entry in allowed)


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _schema(task, name, red):
    for key in KEYS:
        if key not in task and key != "origin":
            red("task-json", f"missing key {key}")
    if "id" in task and task["id"] != name:
        red("id", f"{task['id']!r} differs from the directory name")
    origin = task.get("origin")
    if "origin" not in task:
        red("origin", "missing")
    elif not (isinstance(origin, str) and ORIGIN.fullmatch(origin)):
        red("origin", f"{origin!r} is not planned or issue:<n>")
    for key in ("kind", "prompt"):
        if key in task and not (isinstance(task[key], str) and task[key].strip()):
            red("task-json", f"{key} must be a non-empty string")
    if "trap" in task and not isinstance(task["trap"], bool):
        red("task-json", "trap must be true or false")
    command = task.get("visible_command")
    if "visible_command" in task and not (_strings(command) and command and command[0] in ("python", "python3")):
        red("visible_command", "must be a non-empty list of strings starting with python or python3 (validated, never run)")
    if "hidden_sha256" in task and not (isinstance(task["hidden_sha256"], dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in task["hidden_sha256"].items())):
        red("task-json", "hidden_sha256 must map relative path to sha256 hex")


def _contract(d, red):
    """Required files and shapes. Returns True when the task can be run."""
    runnable = True
    for rel in ("rubric.json", "allowed_paths.json", "hidden/verify.json"):
        if not (d / rel).is_file():
            red(rel, "missing")
            runnable = False
    for rel in ("repo", "reference", "hidden/files"):
        if not (d / rel).is_dir():
            red(rel, "missing")
            runnable = False
    if (d / "rubric.json").is_file() and not isinstance(_json(d / "rubric.json"), dict):
        red("rubric", "rubric.json must be a JSON object")
    if (d / "allowed_paths.json").is_file() and not _strings(_json(d / "allowed_paths.json")):
        red("allowed_paths", "allowed_paths.json must be a list of strings")
        runnable = False
    if (d / "hidden" / "verify.json").is_file():
        spec = _json(d / "hidden" / "verify.json")
        command = spec.get("command") if isinstance(spec, dict) else None
        pairs = list(zip(command or [], (command or [])[1:]))
        if not (_strings(command) and command and command[0] in ("python", "python3") and ("-m", "pytest") in pairs
                and ("-p", "no:cacheprovider") in pairs and ".sigma-hidden/files" in command):
            red("verify-json", "command must be a python -m pytest argv with -p no:cacheprovider and .sigma-hidden/files")
            runnable = False
    for sub in ("repo", "reference", "hidden"):
        if (d / sub).is_dir():
            for rel in _walk(d / sub)[1]:
                red("symlink", f"{sub}/{rel}")
                runnable = False
    return runnable


def _hashes(d, task, red):
    declared = task.get("hidden_sha256")
    if not isinstance(declared, dict) or not (d / "hidden").is_dir():
        return
    actual = _walk(d / "hidden")[0]
    for rel in sorted(set(actual) | set(declared)):
        if rel not in declared:
            red("hidden-sha256", f"{rel}: present but not declared")
        elif rel not in actual:
            red("hidden-sha256", f"{rel}: declared but absent")
        elif actual[rel] != declared[rel]:
            red("hidden-sha256", f"{rel}: hash mismatch")


def _reference_diff(d, red):
    start, reference = _walk(d / "repo")[0], _walk(d / "reference")[0]
    allowed = _json(d / "allowed_paths.json")
    for rel in sorted(set(start) | set(reference)):
        if start.get(rel) != reference.get(rel) and not _allowed(rel, allowed):
            kind = "deleted" if rel not in reference else "added" if rel not in start else "changed"
            red("reference-diff", f"{rel} {kind} but not in allowed_paths.json")


def _ignore_hidden(hidden):
    def ignore(directory, names):
        skip = {n for n in names if n in ("__pycache__", ".DS_Store") or n.endswith(".pyc")}
        if os.fspath(directory) == os.fspath(hidden) and "naive" in names:
            skip.add("naive")
        return skip
    return ignore


def _ignore_junk(directory, names):
    return {n for n in names if n in ("__pycache__", ".DS_Store") or n.endswith(".pyc")}


def _runs(d, task, timeout, env, red):
    def attempt(prop, tree, bundle, want, why):
        code, tail = bt._hidden_run(tree, bundle, task, sys.executable, timeout, env)
        if code is None:
            red(prop, f"timeout after {timeout}s (killed)")
        elif code != want:
            last = (tail.strip().splitlines() or [""])[-1][:160]
            red(prop, f"{why} (exit {code}): {last}")

    with tempfile.TemporaryDirectory(prefix="golden-run-") as scratch:
        bundle = Path(scratch) / "bundle"
        shutil.copytree(d / "hidden", bundle, ignore=_ignore_hidden(d / "hidden"))
        attempt("hidden-on-start", d / "repo", bundle, 1, "hidden tests must fail on the start tree")
        attempt("hidden-on-reference", d / "reference", bundle, 0, "hidden tests must pass on the reference tree")
        if (d / "hidden" / "naive").is_dir():
            naive = Path(scratch) / "naive-tree"
            shutil.copytree(d / "repo", naive, ignore=_ignore_junk)
            shutil.copytree(d / "hidden" / "naive", naive, dirs_exist_ok=True, ignore=_ignore_junk)
            attempt("naive", naive, bundle, 1, "hidden tests pass on the naive patch")


ROOT_LINKS = ("", "task.json", "rubric.json", "allowed_paths.json", "repo", "reference", "hidden",
              "hidden/files", "hidden/naive", "hidden/verify.json")


def check_task(d, timeout, env):
    """Every RED (property, detail) for one task directory, in order; an empty list is green."""
    reds = []

    def red(prop, detail):
        reds.append((prop, detail))

    # A symlink at any root is red before anything reads through it (is_file/is_dir/os.walk all follow links).
    for rel in ROOT_LINKS:
        if (d / rel if rel else d).is_symlink():
            red("symlink", rel or "task directory")
    if reds:
        return reds
    if not (d / "task.json").is_file():
        return [("task-json", "missing")]
    task = _json(d / "task.json")
    if not isinstance(task, dict):
        raise Unreadable(f"{d / 'task.json'}: not a JSON object")
    _schema(task, d.name, red)
    runnable = _contract(d, red)
    _hashes(d, task, red)
    if runnable:
        _reference_diff(d, red)
        try:
            _runs(d, task, timeout, env, red)
        except (OSError, shutil.Error, SystemExit) as exc:  # a helper or copy crash is unreadable input, not a red
            raise Unreadable(f"task {d.name}: could not run hidden tests: {type(exc).__name__}: {str(exc)[:200]}")
    return reds


def _timeout():
    raw = os.environ.get("SIGMA_GOLDEN_TIMEOUT")
    if raw is None:
        return TIMEOUT
    if not (raw.isdigit() and 1 <= int(raw) <= 600):
        raise Unreadable(f"SIGMA_GOLDEN_TIMEOUT={raw!r} must be an integer from 1 to 600")
    return int(raw)


def _load_helper():
    global bt
    if not HELPER.is_file():
        raise Unreadable(f"{HELPER} is missing")
    spec = importlib.util.spec_from_file_location("golden_bench_helper", HELPER)
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="verify.py", description="Verify the golden task set (evals/golden/).",
                                     epilog="Exit 0 all green, 1 any failing property, 2 bad input. --help verifies nothing.")
    parser.add_argument("--only", metavar="ID", help="verify one task; an unknown ID exits 2")
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        print("verify.py: refused: golden verification needs POSIX process groups; Windows is not supported", file=sys.stderr)
        return 2
    try:
        timeout = _timeout()
        _load_helper()
        every = sorted(p for p in GOLDEN.iterdir() if p.is_dir() and p.name != "__pycache__")
        for p in every:
            if p.name.startswith("."):
                print(f"skipped {p.name} (dot-prefixed directory is not a task)")
        dirs = [p for p in every if not p.name.startswith(".")]
        if args.only is not None:
            dirs = [p for p in dirs if p.name == args.only]
            if not dirs:
                raise Unreadable(f"--only {args.only}: no such task directory under {GOLDEN}")
        if not dirs:
            print(f"0 golden tasks found under {GOLDEN} (nothing verified)")
            return 0
        failed = 0
        with tempfile.TemporaryDirectory(prefix="golden-profile-") as profile:
            env = bt.clean_env(Path(profile) / "profile")
            for d in dirs:
                reds = check_task(d, timeout, env)
                for prop, detail in reds:
                    print(f"RED {d.name} {prop}: {detail}")
                if not reds:
                    print(f"ok {d.name}")
                failed += bool(reds)
        print(f"golden: {len(dirs)} task(s), {failed} red")
        return 1 if failed else 0
    except Unreadable as exc:
        print(f"verify.py: unreadable input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

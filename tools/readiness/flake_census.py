#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the full suite N times, then classify every test that did not behave the same way each time (#337).

USAGE
    flake_census.py run REPO --runs N --python INTERPRETER --out DIR [--start K] [-- PYTEST_ARGS...]
    flake_census.py aggregate INPUT... --os NAME --json OUT [--meta KEY=VALUE ...]
    flake_census.py combine LINUX_JSON MACOS_JSON --sha SHA --json OUT

`run` makes N sequential runs of `INTERPRETER -m pytest tests/ -q -p no:randomly -p no:rerunfailures
-p no:flaky --junitxml=DIR/junit-<i>.xml` with cwd REPO, numbered K (default 1) upward. It refuses retry,
selection and ordering arguments, and a PYTEST_ADDOPTS or PYTEST_PLUGINS that would inject them.
`aggregate` reads one JUnit XML file per run (a file, or a directory searched recursively) and writes one
operating system's classification. `combine` joins a Linux and a macOS classification, refusing anything
other than ten runs each, into the single evidence document.

CLASSES, per test node id (`classname::name`, or `name` alone for a pytest collection error):
    flaky             passed in at least one run and failed or errored in at least one
    always_failing    failed in every run it appears in (a deterministic failure, reported separately)
    skipped_only      skipped in every run it appears in
    mixed_nonpassing  never passed, but both failed and was skipped
    clean             everything else (a pass/skip mix is clean by definition)
    missing           absent from some run. An overlay: a node can be flaky and missing at once.
`clean`, `flaky`, `always_failing`, `skipped_only` and `mixed_nonpassing` partition the nodes.

EXIT: 0 = no flaky tests (run: every run completed and wrote its XML); 1 = flaky tests found (run: a run wrote
no XML or ended abnormally, a pytest exit code other than 0 or 1); 2 = bad input. A failing suite is data for
the census, never an error of `run`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


SCHEMA = "flake-census/v1"
REQUIRED_RUNS = 10
RUN = subprocess.run          # the one seam tests replace; production never does

LIMITS = (
    "Ten runs observe instability; they cannot prove zero flaky tests. By arithmetic, not measurement, a "
    "test that fails 1 run in 20 passes all ten runs about 60 percent of the time.",
    "Only Python 3.12 was run. The launch definition lists 3.10 to 3.13.",
    "Linux runs each suite in one process; macOS runs with -n 4 (xdist), so ordering effects differ.",
    "A test that passes in some runs and is skipped in others is counted clean, not flaky.",
    "A node absent from some run is listed under missing as well as in its other class.",
    "An xfail that passes counts as passed. combine trusts --sha: both halves must have been run on that commit.",
)

# `run` must observe the suite, never repair or narrow it.
_REFUSED_LONG = ("--reruns", "--reruns-delay", "--only-rerun", "--force-flaky", "--min-passes", "--max-runs",
                 "--junitxml", "--junit-xml", "--lf", "--last-failed", "--ff", "--failed-first", "--nf",
                 "--new-first", "--sw", "--stepwise", "--stepwise-skip", "--exitfirst", "--maxfail", "--deselect",
                 "--ignore", "--ignore-glob", "--collect-only", "--co", "--pyargs", "--setup-plan",
                 "--setup-only", "--pdb")
_EXACT_ONLY = ("--co", "--pdb")      # also the first letters of ordinary options (--color, --pdbcls)
_HOST_PATH = re.compile(r"(^|[\s=])(/|~/)|^[A-Za-z]:[\\/]")
_KEY = re.compile(r"^[a-z_]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


class BadInput(Exception):
    """The supplied input cannot support a census."""


# ------------------------------------------------------------------ aggregate

def _read_run(path: Path) -> dict:
    """One JUnit file -> {node id: passed|failed|skipped}, plus the node ids repeated inside it.

    Inputs are this project's own run artifacts, so stdlib XML parsing is sufficient."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise BadInput("cannot parse JUnit XML %s: %s" % (path.name, exc)) from exc
    rank = {"failed": 2, "passed": 1, "skipped": 0}
    outcomes, repeated = {}, set()
    for case in root.iter("testcase"):
        name = case.get("name")
        if not name:
            raise BadInput("JUnit testcase in %s has no name" % path.name)
        classname = case.get("classname") or ""
        node = "%s::%s" % (classname, name) if classname else name
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        if node in outcomes:
            repeated.add(node)
            outcome = max(outcome, outcomes[node], key=rank.__getitem__)
        outcomes[node] = outcome
    if not outcomes:
        raise BadInput("JUnit XML %s holds no testcase: that run observed nothing" % path.name)
    return {"outcomes": outcomes, "repeated": repeated}


def _xml_inputs(values) -> list:
    inputs = []
    for value in values:
        path = Path(value)
        if path.is_dir():
            inputs.extend(sorted(path.rglob("*.xml")))
        elif path.is_file():
            inputs.append(path)
        else:
            raise BadInput("not a file or directory: %s" % path.name)
    unique = list(dict.fromkeys(p.resolve() for p in inputs))
    if not unique:
        raise BadInput("supply at least one JUnit XML file")
    seen = {}
    for path in unique:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise BadInput("cannot read %s: %s" % (path.name, exc)) from exc
        if digest in seen:
            raise BadInput("%s and %s are byte-identical: one run counted twice" % (seen[digest], path.name))
        seen[digest] = path.name
    return unique


def parse_meta(pairs) -> dict:
    meta = {}
    for pair in pairs or ():
        key, sep, value = pair.partition("=")
        if not sep or not _KEY.match(key) or not value:
            raise BadInput("--meta takes KEY=VALUE with a lowercase key, got %r" % key)
        if _HOST_PATH.search(value):
            raise BadInput("--meta %s looks like a host path; evidence must not carry one" % key)
        meta[key] = value
    return meta


def aggregate(paths, os_name: str, meta=None) -> dict:
    """Classify one operating system's runs (one JUnit file per run, no retries)."""
    runs = [_read_run(path) for path in _xml_inputs(paths)]
    nodes = sorted(set().union(*(run["outcomes"] for run in runs)))
    seen = {node: [r["outcomes"][node] for r in runs if node in r["outcomes"]] for node in nodes}
    flaky, always, skipped, mixed, clean, missing = [], [], [], [], 0, []
    for node in nodes:
        kinds = set(seen[node])
        if "passed" in kinds and "failed" in kinds:
            flaky.append(node)
        elif "passed" in kinds:
            clean += 1
        elif kinds == {"failed"}:
            always.append(node)
        elif kinds == {"skipped"}:
            skipped.append(node)
        else:
            mixed.append(node)
        if len(seen[node]) < len(runs):
            missing.append(node)
    return {
        "schema": SCHEMA,
        "os": os_name,
        "runs": len(runs),
        "flaky": flaky,
        "always_failing": always,
        "skipped_only": skipped,
        "mixed_nonpassing": mixed,
        "missing": missing,
        "totals": {"tests": len(nodes), "clean": clean, "flaky": len(flaky),
                   "always_failing": len(always), "skipped_only": len(skipped),
                   "mixed_nonpassing": len(mixed), "missing": len(missing)},
        "per_run": [{"tests": len(r["outcomes"]),
                     "failed": sum(1 for v in r["outcomes"].values() if v == "failed"),
                     "skipped": sum(1 for v in r["outcomes"].values() if v == "skipped")} for r in runs],
        "duplicate_node_ids": sorted(set().union(*(r["repeated"] for r in runs))),
        "context": dict(meta or {}),
    }


# ------------------------------------------------------------------ combine

def _aggregate_json(path: Path, expected_os: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BadInput("cannot read aggregate JSON %s: %s" % (path.name, exc)) from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise BadInput("%s is not a %s aggregate" % (path.name, SCHEMA))
    if payload.get("os") != expected_os:
        raise BadInput("%s must describe %s, not %r" % (path.name, expected_os, payload.get("os")))
    if not all(isinstance(payload.get(k), list) for k in ("flaky", "always_failing", "missing")):
        raise BadInput("%s lacks the classification lists" % path.name)
    if not isinstance(payload.get("per_run"), list) or len(payload["per_run"]) != REQUIRED_RUNS:
        raise BadInput("%s per_run does not list %d runs" % (path.name, REQUIRED_RUNS))
    if payload.get("runs") != REQUIRED_RUNS:
        raise BadInput("%s holds %r runs; a census needs exactly %d per operating system"
                       % (path.name, payload.get("runs"), REQUIRED_RUNS))
    return payload


def combine(linux_json, macos_json, sha: str) -> dict:
    """Join two independently produced operating-system sections without letting either replace the other."""
    if not _SHA.match(sha or ""):
        raise BadInput("--sha must be the full 40-character lowercase commit hash that was measured")
    return {"schema": SCHEMA, "sha": sha,
            "linux": _aggregate_json(Path(linux_json), "linux"),
            "macos": _aggregate_json(Path(macos_json), "macos"),
            "limits": list(LIMITS)}


def _write_new_json(path, payload) -> None:
    """Create one evidence file exclusively: it never replaces earlier evidence or one of its inputs."""
    path = Path(path)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise BadInput("refusing to overwrite existing evidence: %s" % path.name) from exc
    except OSError as exc:
        raise BadInput("cannot create %s: %s" % (path.name, exc)) from exc


# ------------------------------------------------------------------ run

def _refused_argument(args, repo: Path):
    """The first pytest argument `run` must not pass through, or None."""
    items = list(args)
    for index, arg in enumerate(items):
        name = arg.split("=", 1)[0]
        # pytest accepts any unique prefix of a long option (`--exitf`), so match in both directions.
        if name.startswith("--") and len(name) > 2 and any(
                (name.startswith(f) and f not in _EXACT_ONLY) or f.startswith(name) for f in _REFUSED_LONG):
            return arg
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 1:
            letter = arg[1]
            if letter in "kmco":
                return arg
            if letter != "r":                    # -r takes report letters, and -rx is an ordinary report
                at = arg.find("p", 1)            # a plugin flag may hide in a cluster: -qp rerunfailures
                if at > 0:
                    value = arg[at + 1:] or (items[index + 1] if index + 1 < len(items) else "")
                    if re.search(r"rerun|flaky", value) and not value.startswith("no:"):
                        return arg
                if arg[1:].isalpha() and "x" in arg[1:]:
                    return arg
        elif not arg.startswith("-"):
            if arg.endswith(".py") or "::" in arg or (repo / arg).exists():
                return arg
    return None


def run(repo, runs: int, python: str, out, pytest_args=(), start: int = 1):
    """N sequential full-suite observations into OUT/junit-<i>.xml. Returns one row per run."""
    # Resolved first: pytest runs with cwd=repo, so a relative OUT would be checked here and written there.
    repo, out = Path(repo).resolve(), Path(out).resolve()
    if os.sep in python or "/" in python:
        python = os.path.abspath(python)    # cwd is REPO for the child; abspath, never resolve (a venv python is a symlink)
    bad = _refused_argument(pytest_args, repo)
    if bad:
        raise BadInput("pytest argument %r would retry, narrow or reorder the suite" % bad)
    if os.environ.get("PYTEST_ADDOPTS", "").strip():
        raise BadInput("PYTEST_ADDOPTS is set; it can inject retries or selection. Unset it for a census")
    if re.search(r"rerun|flaky", os.environ.get("PYTEST_PLUGINS", "")):
        raise BadInput("PYTEST_PLUGINS names a retry plugin. Unset it for a census")
    if runs < 1 or start < 1:
        raise BadInput("--runs and --start must be at least 1")
    if not repo.is_dir():
        raise BadInput("repository is not a directory: %s" % repo.name)
    numbers = range(start, start + runs)
    existing = [out / ("junit-%d.xml" % n) for n in numbers if (out / ("junit-%d.xml" % n)).exists()]
    if existing:
        raise BadInput("refusing to overwrite an earlier observation: " + ", ".join(p.name for p in existing))
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BadInput("cannot create %s: %s" % (out.name, exc)) from exc
    rows = []
    for number in numbers:
        junit = out / ("junit-%d.xml" % number)
        command = [python, "-m", "pytest", "tests/", "-q", "-p", "no:randomly", "-p", "no:rerunfailures",
                   "-p", "no:flaky", "--junitxml=%s" % junit, *pytest_args]
        try:
            result = RUN(command, cwd=repo)
        except OSError as exc:
            raise BadInput("cannot start %r: %s" % (python, exc)) from exc
        # pytest exits 0 (all passed) or 1 (some failed) on a complete run; 2 to 5 mean it was interrupted,
        # crashed, was misused or collected nothing, and any XML it left is a partial observation.
        rows.append({"run": number, "exit": result.returncode, "junit": str(junit),
                     "written": junit.is_file(), "complete": result.returncode in (0, 1)})
    return rows


# ------------------------------------------------------------------ cli

def _parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    verbs = parser.add_subparsers(dest="verb", required=True)
    runner = verbs.add_parser("run")
    runner.add_argument("repo")
    runner.add_argument("--runs", type=int, required=True)
    runner.add_argument("--python", required=True)
    runner.add_argument("--out", required=True)
    runner.add_argument("--start", type=int, default=1)
    agg = verbs.add_parser("aggregate")
    agg.add_argument("inputs", nargs="+")
    agg.add_argument("--os", dest="os_name", required=True)
    agg.add_argument("--json", dest="json_path", required=True)
    agg.add_argument("--meta", action="append", default=[])
    comb = verbs.add_parser("combine")
    comb.add_argument("linux_json")
    comb.add_argument("macos_json")
    comb.add_argument("--sha", required=True)
    comb.add_argument("--json", dest="json_path", required=True)
    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    pytest_args = []
    # Only `run` has a pass-through; split it off before argparse sees the options after it.
    if argv[:1] == ["run"] and "--" in argv:
        cut = argv.index("--")
        argv, pytest_args = argv[:cut], argv[cut + 1:]
    args = _parser().parse_args(argv)
    try:
        if args.verb == "run":
            rows = run(args.repo, args.runs, args.python, args.out, pytest_args, args.start)
            print(json.dumps({"runs": rows}, sort_keys=True))
            return 0 if all(row["written"] and row["complete"] for row in rows) else 1
        if args.verb == "combine":
            payload = combine(args.linux_json, args.macos_json, args.sha)
            flaky = payload["linux"]["flaky"] or payload["macos"]["flaky"]
        else:
            payload = aggregate(args.inputs, args.os_name, parse_meta(args.meta))
            flaky = payload["flaky"]
        _write_new_json(args.json_path, payload)
        print(json.dumps(payload, sort_keys=True))
        return 1 if flaky else 0
    except BadInput as exc:
        print("flake_census.py: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

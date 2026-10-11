#!/usr/bin/env python3
"""Evidence-matrix checker for the planted-regression catalogue (#1059, slice 4 of #808). Read-only.

    python3 evals/regression/matrix.py check [--root DIR] [--skip-objects]

Reads `catalogue.json`, `gates.json` and `matrix.json` under DIR/evals/regression (default: this
repository). `matrix.json` holds one entry per catalogue (row, gate) pair: a `red` entry with
`run_id`, `date`, a 40-hex `commit` and a `pin`, or a `pending` entry with none of those. Entries are
produced by `control.py run --record`; this tool only checks them. Problems print one
`matrix.py: <kind>: <detail>` line each on stderr. Exit 0 clean, 1 problems, 2 unreadable or empty.

Kinds: missing-row, no-evidence, bad-commit (not 40 lowercase hex, or not a commit object in the
repository), row-absent-at-commit, stale-evidence (the row or gate was re-pointed since the record),
bad-date, bad-status, duplicate-entry, unknown-pair, stale-pending, red-on-pending,
pending-with-evidence.

A shallow clone cannot show whether an old commit exists: that is printed as UNVERIFIED on stdout
(never described as a pass) and the object checks are skipped for it. `--skip-objects` skips the same
two object-store checks on request, for callers that run on shallow checkouts; format is still enforced.
A record is a claim, not proof: see README.md. Cost: O(entries), two git calls per distinct commit.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
HEX40 = re.compile(r"[0-9a-f]{40}")


def pin(row, gate):
    """First 16 hex of sha256 over what a record claims to have exercised."""
    body = json.dumps([row["regression"], row["form"], row["mutation"], gate["argv"]], sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def git_env():
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=git_env())


def _load(root):
    d = pathlib.Path(root) / "evals" / "regression"
    try:
        rows = json.loads((d / "catalogue.json").read_text(encoding="utf-8"))["rows"]
        gates = {g["id"]: g for g in json.loads((d / "gates.json").read_text(encoding="utf-8"))["gates"]}
        entries = json.loads((d / "matrix.json").read_text(encoding="utf-8"))["rows"]
        if not (rows and isinstance(entries, list) and all(isinstance(e, dict) for e in entries)):
            raise ValueError("catalogue is empty or matrix rows are not objects")
        return rows, gates, entries
    except Exception as exc:
        sys.stderr.write(f"matrix.py: unreadable: {exc}\n")
        return None


def _valid_date(text):
    try:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text)) and bool(datetime.date.fromisoformat(text))
    except (TypeError, ValueError):
        return False


def _at_commit(root, sha, cache):
    """(catalogue row ids, gate ids) as of `sha`, or None when unreadable."""
    if sha not in cache:
        try:
            cache[sha] = (
                {r["id"] for r in json.loads(_git(root, "show", f"{sha}:evals/regression/catalogue.json").stdout)["rows"]},
                {g["id"] for g in json.loads(_git(root, "show", f"{sha}:evals/regression/gates.json").stdout)["gates"]})
        except (ValueError, KeyError, TypeError):
            cache[sha] = None
    return cache[sha]


def problems(root, rows, gates, entries, skip_objects=False):
    out, unverified, seen, cache = [], 0, {}, {}
    pairs = {(r["id"], g): (r, gates.get(g)) for r in rows for g in r["must_be_red_by"]}
    shallow = _git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    for e in entries:
        key = (e.get("row"), e.get("gate"))
        label = f"{key[0]} / {key[1]}"
        if key in seen:
            out.append(f"duplicate-entry: {label}")
        seen[key] = e
        if key not in pairs:
            out.append(f"unknown-pair: {label} is not in the catalogue")
            continue
        row, gate = pairs[key]
        status = e.get("status")
        if gate is None:
            out.append(f"unknown-pair: {label} names a gate that gates.json lacks")
            continue
        if status not in ("red", "pending"):
            out.append(f"bad-status: {label} has {status!r}")
        elif status == "pending":
            if any(e.get(k) for k in ("run_id", "date", "commit", "pin")):
                out.append(f"pending-with-evidence: {label}")
            if gate["status"] != "pending":
                out.append(f"stale-pending: {label}, gate {key[1]} is live")
        else:
            if gate["status"] == "pending":
                out.append(f"red-on-pending: {label}, gate {key[1]} is pending")
            if not all(isinstance(e.get(k), str) and e.get(k) for k in ("run_id", "date", "commit", "pin")):
                out.append(f"no-evidence: {label} lacks run_id, date, commit or pin")
                continue
            if not _valid_date(e["date"]):
                out.append(f"bad-date: {label} has {e['date']!r}")
            if e["pin"] != pin(row, gate):
                out.append(f"stale-evidence: {label} was recorded for a different mutation or gate command")
            sha = e["commit"]
            if not HEX40.fullmatch(sha):
                out.append(f"bad-commit: {label} has {sha!r}, not 40 lowercase hex")
            elif skip_objects:
                continue
            elif _git(root, "cat-file", "-e", f"{sha}^{{commit}}").returncode:
                if shallow:
                    unverified += 1
                else:
                    out.append(f"bad-commit: {label} commit {sha} is not a commit in this repository")
            else:
                ids = _at_commit(root, sha, cache)
                if ids is None or key[0] not in ids[0] or key[1] not in ids[1]:
                    out.append(f"row-absent-at-commit: {label} did not exist at {sha}")
    for key in pairs:
        if key not in seen:
            out.append(f"missing-row: catalogue row {key[0]} / gate {key[1]} has no matrix entry")
    return out, unverified, shallow


def main(argv=None):
    ap = argparse.ArgumentParser(prog="matrix.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--root", default=str(HERE.parent.parent))
    ap.add_argument("--skip-objects", action="store_true")
    args = ap.parse_args(argv)
    loaded = _load(args.root)
    if loaded is None:
        return 2
    rows, gates, entries = loaded
    out, unverified, shallow = problems(args.root, rows, gates, entries, args.skip_objects)
    for line in out:
        sys.stderr.write(f"matrix.py: {line}\n")
    if args.skip_objects:
        print("matrix.py: UNVERIFIED: --skip-objects, commit existence and row-at-commit were not checked")
    elif unverified:
        print(f"matrix.py: UNVERIFIED: shallow clone, {unverified} recorded commit(s) not in the object store; run on a full clone")
    return 1 if out else 0


if __name__ == "__main__":
    raise SystemExit(main())

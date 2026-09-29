#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The publish leak gate: shipped files carry no home path, no secret-shaped string, and no URL into
this repository's own (private until published) GitHub owner (#277, the gate #231's acceptance names).

WHAT IS SCANNED. Every file git tracks (`git ls-files`), outside `.sdlc/` (per-goal working state,
not shipped surface) and `tests/` (fixtures there spell these shapes on purpose, and the private-name
and cross-boundary guards already cover tests/). Binary and non-UTF-8 files are skipped and counted.

THE RULES.
  * home-path: `/Users/<name>/`, `/home/<name>/`, `C:\\Users\\<name>\\`, where <name> is not a
    placeholder (`you`, `me`, `user`, `USER`, `<...>`, `$USER`, `alice`, `bob`, `runner`, ...).
  * secret shapes: `SHAPE_RULES` from `skills/agrim-loop/scripts/scrub.py`, imported by
    `_scrub_rules`, never copied (scrub.py's docstring names this gate as its importer). A missing
    tuple REFUSES (exit 2): a gate with no rules must not print 0 findings. Post-filter: a
    `credential-assignment` value must carry a digit and a letter (prose like `token = "..."` is not
    a secret).
  * origin-owner URL: `github.com/<owner>/<repo>` where <owner> is this checkout's own `origin`
    owner and <repo> is NOT this repository -- a link into the owner's other (private) repositories.
    Owner and repository are read from `origin` at run time, so neither is spelled here; a link to
    this repository itself (the CI badge) is the published surface and passes. No origin, or a
    non-GitHub one: the rule is skipped and the summary says so.

OUTPUT. One line per finding, `<path>:<line>: <rule>` -- the LOCATION only, never the matched value,
then `leak_scan: <n> finding(s) over <m> file(s)`. EXIT 0 = none; 1 = findings; 2 = cannot scan (not
a git checkout, scrub rules missing, a bad argument).

USAGE
    python3 tools/leak_scan.py            # scans the checkout this file lives in

Cost: one `git ls-files` and one `git remote get-url`, then a linear read of every tracked text file
(measured on this tree: ~1,000 files in well under a second). Stdlib only; Linux, macOS, Windows.
"""
import importlib.util
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = (".sdlc/", "tests/")

#: Names that stand in for "some user" in documentation, never a real account.
_PLACEHOLDER_USERS = {"you", "me", "user", "username", "runner", "alice", "bob", "someone", "name",
                      "example", "shared", "Shared", "USER", "USERNAME", "x", "u", "dev"}
_HOME = re.compile(r"(?:(?<![\w.~-])/(?:Users|home)/|[A-Za-z]:\\\\?Users\\\\?)([A-Za-z0-9._-]+)[/\\]")


#: A kebab/snake identifier (`feature-classify-tier1`): words, each at most two trailing digits.
_IDENTIFIER = re.compile(r"[A-Za-z]+\d{0,2}(?:[-_.][A-Za-z]+\d{0,2})+")


def _scrub_rules():
    """`SHAPE_RULES` from scrub.py, by path. Refuses (SystemExit 2) when absent."""
    path = ROOT / "skills" / "agrim-loop" / "scripts" / "scrub.py"
    try:
        spec = importlib.util.spec_from_file_location("sigma_scrub_for_leak_scan", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rules = tuple(mod.SHAPE_RULES)
    except (OSError, AttributeError, SyntaxError, TypeError) as exc:
        print(f"leak_scan: REFUSED: cannot load SHAPE_RULES from {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if not rules:
        print(f"leak_scan: REFUSED: SHAPE_RULES in {path} is empty", file=sys.stderr)
        raise SystemExit(2)
    return rules


def _git(args):
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)


def origin_owner():
    """(owner, repository) of a GitHub `origin`, or None (no origin, or not GitHub)."""
    proc = _git(["remote", "get-url", "origin"])
    if proc.returncode:
        return None
    m = re.search(r"github\.com[:/]+([A-Za-z0-9-]+)/([\w.-]+?)(?:\.git)?/?$", proc.stdout.strip())
    return (m.group(1), m.group(2)) if m else None


def scan_text(text, rules, owner):
    """-> [(line number, rule name)] for one file's text. Pure; the tests drive it directly."""
    found = []
    url = re.compile(r"github\.com/" + re.escape(owner[0]) + r"/(?!" + re.escape(owner[1])
                     + r"(?![\w.-]))[\w.-]+", re.I) if owner else None
    for n, line in enumerate(text.splitlines(), 1):
        for m in _HOME.finditer(line):
            name = m.group(1)
            if name not in _PLACEHOLDER_USERS and not name.startswith(("$", "<", "%")) \
                    and name.strip("."):
                found.append((n, "home-path"))
                break
        for name, rx in rules:
            for m in rx.finditer(line):
                value = m.group(1) if rx.groups else m.group(0)
                if name == "credential-assignment" and (
                        not (re.search(r"\d", value) and re.search(r"[A-Za-z]", value))
                        or _IDENTIFIER.fullmatch(value)):
                    continue
                found.append((n, name))
                break
        if url is not None and url.search(line):
            found.append((n, "origin-owner-url"))
    return found


def main(argv):
    if len(argv) > 1:
        print("usage: python3 tools/leak_scan.py   (no arguments)", file=sys.stderr)
        return 2
    listed = _git(["ls-files", "-z"])
    if listed.returncode:
        print(f"leak_scan: REFUSED: {ROOT} is not a git checkout: {listed.stderr.strip()}",
              file=sys.stderr)
        return 2
    rules = _scrub_rules()
    owner = origin_owner()
    findings, scanned, skipped = [], 0, 0
    for rel in sorted(p for p in listed.stdout.split("\0") if p):
        if rel.startswith(SKIP_DIRS):
            continue
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            skipped += 1
            continue
        scanned += 1
        findings += [(rel, n, rule) for n, rule in scan_text(text, rules, owner)]
    for rel, n, rule in findings:
        print(f"{rel}:{n}: {rule}")
    note = "" if owner else "; origin-owner-url rule skipped (no GitHub origin)"
    print(f"leak_scan: {len(findings)} finding(s) over {scanned} file(s) "
          f"({skipped} binary/unreadable skipped{note})")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

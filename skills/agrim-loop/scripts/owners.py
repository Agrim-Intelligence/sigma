#!/usr/bin/env python3
"""Who owns what, read from the repo's own CODEOWNERS.

A team already declares ownership once, in `.github/CODEOWNERS`, and the host enforces it on every
PR — so that file is the roster. Reading it beats a second list in config.json, which would be one
more thing to keep true.

Matching follows GitHub's rule: patterns are tried in order and **the last one that matches wins**.
The glob subset supported here is the one CODEOWNERS files actually use — `*`, `/dir/`, `dir/`,
`*.ext`, and `**` — not the whole gitignore grammar. An unmatched path simply has no owner, which
callers must treat as "nobody in particular", never as an error. Zero deps.
"""
import pathlib
import re
import sys

#: Searched in order; the first file that exists is the roster (GitHub's own resolution order).
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


def find_file(project_root):
    root = pathlib.Path(project_root)
    for rel in CODEOWNERS_PATHS:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def parse(text):
    """-> [(pattern, [owner, ...])] in file order. Comments and blank lines are dropped; a pattern
    with no owners is kept as an explicit "this path is deliberately unowned" rule, exactly as
    CODEOWNERS means it."""
    rules = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        pattern, owners = parts[0], [o.lstrip("@") for o in parts[1:] if o.startswith("@")]
        rules.append((pattern, owners))
    return rules


def load(project_root):
    path = find_file(project_root)
    if path is None:
        return []
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError:
        return []


def _glob_match(path, body):
    """`fnmatch(path, body)`, but CODEOWNERS-correct: `*` matches within one path segment only
    (never crosses `/`) and `**` matches across segments — unlike plain `fnmatch`, whose `*`
    always crosses `/`, which is what let `engine/*` over-match `engine/a/b/c.py` (#355)."""
    out = []
    i, n = 0, len(body)
    while i < n:
        if body[i:i + 2] == "**":
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    return re.fullmatch("".join(out), path) is not None


def _matches(pattern, path):
    """One CODEOWNERS pattern against a repo-relative path."""
    path = path.lstrip("/")
    if pattern == "*":
        return True
    anchored = pattern.startswith("/")
    body = pattern.lstrip("/")
    if body.endswith("/"):                       # a directory rule owns everything beneath it
        body += "**"
    if anchored or "/" in body:
        return _glob_match(path, body) or _glob_match(path, body.rstrip("/*") + "/*") \
            or path == body.rstrip("/*")
    # An unanchored pattern (e.g. `*.py`, `build`) matches at any depth.
    return _glob_match(path, body) or _glob_match(path, f"*/{body}") \
        or any(_glob_match(part, body) for part in path.split("/"))


def for_path(rules, path):
    """Owners of a repo-relative path — last matching rule wins, per GitHub."""
    winner = []
    for pattern, owners in rules:
        if _matches(pattern, path):
            winner = owners
    return winner


def for_area(rules, area, config=None):
    """Owners of a named AREA (`engine`, `ui`, `quality`, …).

    An explicit `ledger.owners` map in config always wins — a team whose directory layout doesn't
    match its area vocabulary needs a way to say so. Otherwise the area is resolved as the directory
    it names, and a `*` catch-all is only accepted when nothing more specific matched, so a hand-off
    doesn't get addressed to the repo lead just because they own `*`."""
    override = ((config or {}).get("ledger") or {}).get("owners") or {}
    if area in override:
        value = override[area]
        return [v.lstrip("@") for v in (value if isinstance(value, list) else [value])]
    specific = [(p, o) for p, o in rules if p != "*"]
    for candidate in (f"{area}/x", f"{area}/", f"src/{area}/x"):
        owners = for_path(specific, candidate)
        if owners:
            return owners
    return for_path(rules, f"{area}/x")           # falls back to the catch-all, if any


def owner_of(project_root, area, config=None):
    """The single person a hand-off is addressed to, or None. First listed owner wins — a hand-off
    needs one assignee, and CODEOWNERS lists the primary first by convention."""
    owners = for_area(load(project_root), area, config)
    return owners[0] if owners else None


USAGE = "usage: owners.py <project-root> <area>"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 3:
        root, area = argv[1], argv[2]
        who = owner_of(root, area)
        print(who or f"no owner for area {area!r} in CODEOWNERS")
        return 0 if who else 1
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Fail until no tracked path or file carries the retired skill prefix (#523).

Gesture, from anywhere inside the repository, no flags:

    python3 tools/rename_check.py

Exit 0: nothing outside the allowlist. Exit 1: every offending path or `path:line: text` is printed.
Exit 2: REFUSED (stderr, empty stdout) because the directory is not inside a git repository. An
optional DIR argument checks the repository containing DIR instead of the working directory.

It searches the raw bytes of every tracked path and file, case-insensitively, so a binary or a
file that is not UTF-8 is never skipped. The old name is spelled from fragments below so this file
does not trip itself. The allowlist is deliberately narrow:

  * the organisation name and the owner's GitHub login, wherever they appear;
  * two private-name guard tests that must spell the retired names, for those exact tokens only;
  * CHANGELOG.md below the rename entry (history), never the lines above it;
  * the recorded launch evidence files named in EVIDENCE: output captured at a named commit, which a
    rewrite would make claim output that commit never produced. A NEW evidence file is not exempt.
"""
import os
import re
import subprocess
import sys

OLD = "agr" + "im"
#: The organisation name and the owner's GitHub login: neither is a skill prefix and neither is renamed.
ORG = (OLD.capitalize() + "-Intelligence", OLD.capitalize() + " Intelligence", "swapnil-" + OLD)
#: (path, tokens): tokens removed from that file's lines before matching.
FILE_TOKENS = {
    "tests/test_no_private_names.py": (OLD.capitalize() + " P" + "i", '"%s/"' % OLD),
    "tests/test_public_bootstrap_control.py": ('r"%s"' % OLD.capitalize(),),
}
#: First line of the CHANGELOG rename entry; everything from it down is history.
CHANGELOG = "CHANGELOG.md"
CHANGELOG_MARK = "- **Every skill and command now starts with `sigma-`**"
EVIDENCE = frozenset("docs/launch/evidence/" + n for n in (
    "330-control.md", "blast-radius-2026-10-04.json", "cost-calibration.md", "drills-cde9869d0cf8.json",
    "egress-dc79750ab8d6.json", "exposure-8aee0c74c526.json", "exposure-8aee0c74c526.md",
    "exposure-disposition-8aee0c74c526.json", "flake-c82e3dfa7420.json", "mechanical-gates-859290305d97.json",
    "meter-sonnet-5-5-cf31b72c6967.json", "mutation-70c2c6e96136.json", "pin-rollback-2026-10-02.md",
    "review-units-a5c615062313.json", "shared-sdlc-paths-4c8562f.json", "shared-sdlc-paths-control.md",
    "status-a5c615062313.json", "tracked-8aee0c74c526.json", "tracked-8aee0c74c526.md"))
#: Extend here (the plugin id is added by #524); each pattern is searched case-insensitively.
PATTERNS = (re.compile(re.escape(OLD).encode(), re.I),)


def _strip(line, extra=()):
    for tok in (*(t for t in ORG), *extra):
        line = line.replace(tok.encode(), b"")
    return line


def _hit(line, extra=()):
    line = _strip(line, extra)
    return any(p.search(line) for p in PATTERNS)


def scan(top, tracked):
    """Return the findings for the tracked paths under `top`, as printable strings."""
    out = []
    for rel in tracked:
        if _hit(rel.encode("utf-8", "surrogateescape")):
            out.append(rel)
        if rel in EVIDENCE:
            continue
        path = os.path.join(top, rel)
        try:
            data = os.readlink(path).encode() if os.path.islink(path) else open(path, "rb").read()
        except OSError:
            continue                       # tracked but deleted from the working tree
        extra = FILE_TOKENS.get(rel, ())
        history = rel == CHANGELOG and CHANGELOG_MARK.encode() in data
        for n, line in enumerate(data.split(b"\n"), 1):
            if history and line.startswith(CHANGELOG_MARK.encode()):
                break
            if _hit(line, extra):
                out.append("%s:%d: %s" % (rel, n, line.decode("utf-8", "replace").strip()[:160]))
    return out


def toplevel(start):
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def tracked_files(top):
    z = subprocess.run(["git", "ls-files", "-z"], cwd=top, capture_output=True, check=True).stdout
    return sorted(p.decode("utf-8", "surrogateescape") for p in z.split(b"\0") if p)


def main(argv):
    start = argv[1] if len(argv) > 1 else os.getcwd()
    top = toplevel(start) if os.path.isdir(start) else None
    if not top:
        sys.stderr.write("rename_check.py: REFUSED [not-a-git-repo] at %s: run inside a git repository\n" % start)
        return 2
    found = scan(top, tracked_files(top))
    for line in found:
        print(line)
    if found:
        sys.stderr.write("rename_check.py: %d occurrence(s) of the retired prefix outside the allowlist\n" % len(found))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

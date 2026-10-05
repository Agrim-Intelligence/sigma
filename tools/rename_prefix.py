#!/usr/bin/env python3
"""Mechanically rename the retired skill prefix to `sigma-` (#523). Idempotent.

    python3 tools/rename_prefix.py [--dry-run]     (from anywhere inside the repository)

1. Every path with the prefix is renamed with `git mv`, one whole directory at a time, so untracked
   files inside (a `__pycache__`) travel with it. If any destination already exists the tool REFUSES
   (exit 2) before moving anything.
2. Then, in every tracked file and every untracked file git would commit, `<prefix>-` and
   `<prefix>_` become `sigma-` and `sigma_` when the prefix starts a word or follows a literal `\\n`.
   The org name never matches (capital A), and the same allowlist paths as `rename_check.py` are
   skipped: CHANGELOG.md (history, hand-edited) and the recorded launch evidence.

It prints one line per path moved and per file changed; a second run prints nothing. A hook or
skill path an installed host config points at is NOT aliased (owner decision).
"""
import importlib.util
import os
import pathlib
import re
import subprocess
import sys

_spec = importlib.util.spec_from_file_location("rename_check", pathlib.Path(__file__).with_name("rename_check.py"))
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

OLD, NEW = check.OLD, "sigma"
_NAME = re.compile(r"%s(?=[-_])" % OLD)
_TOKEN = re.compile(rb"(?:(?<![A-Za-z0-9])|(?<=\\n))" + OLD.encode() + rb"(?=[-_])")
SKIP = set(check.EVIDENCE) | {check.CHANGELOG}


def _files(top, others=False):
    args = ["git", "ls-files", "-z"] + (["--cached", "--others", "--exclude-standard"] if others else [])
    z = subprocess.run(args, cwd=top, capture_output=True, check=True).stdout
    return sorted({p.decode("utf-8", "surrogateescape") for p in z.split(b"\0") if p})


def plan_moves(paths):
    """The shallowest path prefix that carries the old name, per path -> {old: new}."""
    moves = {}
    for p in paths:
        parts = p.split("/")
        for i, c in enumerate(parts):
            if _NAME.search(c):
                old = "/".join(parts[:i + 1])
                moves[old] = "/".join(parts[:i] + [_NAME.sub(NEW, c)])
                break
    return moves


def main(argv):
    dry = "--dry-run" in argv
    top = check.toplevel(os.getcwd())
    if not top:
        sys.stderr.write("rename_prefix.py: REFUSED [not-a-git-repo]: run inside a git repository\n")
        return 2
    while True:
        moves = plan_moves(_files(top))
        if not moves:
            break
        clash = [n for n in moves.values() if os.path.lexists(os.path.join(top, n))]
        if clash:
            sys.stderr.write("rename_prefix.py: REFUSED [destination-exists] %s: nothing moved. A branch that added "
                             "files under an old folder name: `git mv` those files into the existing new "
                             "folder by hand, then run this tool again.\n" % ", ".join(sorted(clash)))
            return 2
        for old, new in sorted(moves.items()):
            print("move %s -> %s" % (old, new))
            if not dry:
                subprocess.run(["git", "mv", old, new], cwd=top, check=True)
        if dry:
            break
    for rel in _files(top, others=True):
        if rel in SKIP:
            continue
        path = os.path.join(top, rel)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        data = open(path, "rb").read()
        new, n = _TOKEN.subn(NEW.encode(), data)
        if n:
            print("change %s (%d)" % (rel, n))
            if not dry:
                with open(path, "wb") as f:       # in place: keeps the file mode
                    f.write(new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

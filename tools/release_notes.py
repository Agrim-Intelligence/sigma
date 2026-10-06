#!/usr/bin/env python3
"""Print one release's CHANGELOG section, and refuse a release that has no date yet.

Gesture (docs/release.md step 7), from the repository root:

    python3 tools/release_notes.py X.Y.Z [CHANGELOG.md] > notes.md

It prints the lines under the heading `## X.Y.Z — YYYY-MM-DD ...` up to the next `## ` heading.
Exit 0: printed. Exit 2: REFUSED, one line on stderr and nothing on stdout, for a usage error, an
unreadable file, no such heading, a heading that is not dated with a real ISO date (the
`DATE-PENDING` placeholder included), or an empty section. The point: the owner sets the date in the
heading before release, and the one irreversible step (`gh release create`) cannot be fed an
undated section. Stdlib only.
"""
import re
import sys
import datetime

HEADING = re.compile(r"^## (\d+\.\d+\.\d+)(?: — (.*))?$")
ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})(?: — .*)?$")


def refuse(why):
    sys.stderr.write("release_notes.py: REFUSED: %s\n" % why)
    return 2


def main(argv):
    if len(argv) not in (2, 3) or argv[1] in ("-h", "--help"):
        return refuse("usage: release_notes.py X.Y.Z [CHANGELOG.md]")
    version, path = argv[1], (argv[2] if len(argv) == 3 else "CHANGELOG.md")
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return refuse("cannot read %s: %s" % (path, exc))
    start = None
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m and m.group(1) == version:
            start = i
            break
    if start is None:
        return refuse("no heading '## %s' in %s" % (version, path))
    suffix = HEADING.match(lines[start]).group(2) or ""
    m = ISO.match(suffix)
    try:
        datetime.date.fromisoformat(m.group(1)) if m else None
    except ValueError:
        m = None
    if not m:
        return refuse("the '## %s' heading has no real ISO date (%r): set the release date first" % (version, suffix))
    body = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    text = "\n".join(body).strip()
    if not text:
        return refuse("the '## %s' section is empty" % version)
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        pass  # the reader (head, a closed pipe) went away; not a refusal
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

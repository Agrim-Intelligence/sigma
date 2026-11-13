#!/usr/bin/env python3
"""Parse the deferred-items comment a `/sigma-orchestrate` goal session leaves on its issue (#2153).

The format is written in `references/follow-ups.md`: one comment on the goal's issue holding the line
`<!-- sigma-orchestrate:deferred -->` and, after it, one bullet per item:

    - [area:<area>] <what> - <why> (files: <paths>)

Standard library only; makes no network call and writes nothing. Tests: tests/test_orchestrate_deferred.py.

  deferred.py parse <comments.json>     comments as `gh api .../issues/<n>/comments` returns them; prints JSON
  deferred.py check <body-file>         a draft comment body; exit 0 only when at least one item parses and nothing is
                                        rejected (a malformed bullet and a list line that is not a `- `/`* ` bullet both fail it)

`parse` prints {"items": [{"area", "what", "why", "files": [..]}], "rejected": [{"comment", "line", "reason"}]},
the shape `follow-ups.md` stores under a goal's `"deferred"` key. It exits 0 even when something was rejected: the
orchestrator reports each rejection to the person instead of losing the item. Exit 2 is bad input.

Rules (each has a test):
  * A comment counts only when its author association is OWNER, MEMBER or COLLABORATOR: on a public repository
    anyone can paste the marker, and an item becomes a filed goal. A comment object with no association, a
    string, or any other association is rejected, never read.
  * The marker must stand on a line that is not `>`-quoted and not inside a code fence (a marker shown as a
    sample is documentation). It may sit anywhere on its line (the host may wrap the text). Only bullets
    AFTER the marker line count; plain prose is ignored. A list-shaped line that does not start `- ` or `* `
    (a numbered item, `+ [area:...]`, `-[area:...]`, a bare `[area:...]`, a unicode-dot bullet) is REJECTED with
    a reason, never skipped (#2157).
  * A list-shaped line after the marker that sits inside a code fence or a `>` quote is not read as an item but is
    REJECTED with a reason saying so, never skipped silently (#2162).
  * A bullet after the marker that does not match the grammar is REJECTED with a reason, never dropped.
  * `<what>` is the text up to the first space-hyphen-space, `<why>` is the rest up to ` (files: `; a `what`
    containing a spaced hyphen is therefore read as a shorter `what` and a longer `why` (named, not detected).
  * Files are comma-separated paths or globs; `none` or empty means no files. An absolute path, a `..` segment,
    a backslash or a control character rejects the item.
  * Identical items (same area, what, why, files) are kept once, across lines and across comments.
  * Bounds: a line over 1000 characters, an item field over 500, over 50 files in an item, or over 200 items in a
    comment is rejected / cut with a reason; `rejected` lists at most 50 entries and says how many more there were.
"""
import json
import re
import sys

MARKER = "<!-- sigma-orchestrate:deferred -->"
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
MAX_LINE, MAX_FIELD, MAX_FILES, MAX_ITEMS, MAX_REJECTED = 1000, 500, 50, 200, 50
_BULLET = re.compile(r"^\s*[-*]\s+")
_ITEM = re.compile(r"^\s*[-*]\s+\[area:([A-Za-z0-9][A-Za-z0-9._/-]*)\]\s+(.+?) - (.+) \(files:\s*([^()]*)\)\s*$")
# A line that is shaped like a list item but is not a `- ` / `* ` bullet: a numbered item (`1.`, `1)`), a `+` or
# unicode-dot bullet, a leader glued to `[` (`-[area:x]`), or a bare `[area:` line. Rejected with a reason, never
# skipped: a person who wrote it meant it as an item (#2157). Prose such as `-5 degrees` or `1.5 times` is not one.
_LISTLIKE = re.compile(r"^\s*(?:[-*+\u2022](?:\s|\[)|\d+[.)]\s|\[area:)")
_BAD_PATH = re.compile(r"[\\\x00-\x1f\x7f]")


def _files(text):
    """(files, reason): the parsed list, or None and why the item is rejected."""
    raw = text.strip()
    if raw == "" or raw.lower() == "none":
        return [], None
    out = []
    for part in raw.split(","):
        path = part.strip()
        if not path:
            return None, "an empty path in the files list"
        if path.startswith("/") or _BAD_PATH.search(path) or ".." in path.split("/"):
            return None, "an unsafe path in the files list"
        out.append(path)
    if len(out) > MAX_FILES:
        return None, "more than %d files in one item" % MAX_FILES
    return out, None


def parse_line(line):
    """(item, reason) for one bullet line: the item dict, or None and the reason it is rejected."""
    if len(line) > MAX_LINE:
        return None, "the line is longer than %d characters" % MAX_LINE
    match = _ITEM.match(line)
    if not match:
        return None, "not `- [area:<area>] <what> - <why> (files: <paths>)`"
    area, what, why, files_text = match.groups()
    what, why = what.strip(), why.strip()
    if not what or not why:
        return None, "an empty what or why"
    if len(what) > MAX_FIELD or len(why) > MAX_FIELD:
        return None, "a what or why longer than %d characters" % MAX_FIELD
    files, reason = _files(files_text)
    if files is None:
        return None, reason
    return {"area": area, "what": what, "why": why, "files": files}, None


def parse_body(body):
    """(items, rejected) for one comment body; ([], []) when it carries no marker line."""
    items, rejected = [], []
    fenced, seen_marker = False, False
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if not seen_marker:
            if not (fenced or stripped.startswith(">")):
                seen_marker = MARKER in line
            continue
        if fenced or stripped.startswith(">"):
            # Not read, but a list-shaped line here is listed, not silently skipped (#2162).
            if _BULLET.match(line) or _LISTLIKE.match(line) or re.match(r"^\s*>[\s>]*(?:[-*+\u2022]\s|\d+[.)]\s|\[area:)", line):
                where = "a code fence" if fenced else "a quoted line"
                rejected.append({"line": line.strip()[:120],
                                 "reason": "a list line inside %s is not read; move it out of the %s to file it"
                                           % (where, "fence" if fenced else "quote")})
            continue
        if not _BULLET.match(line):
            if _LISTLIKE.match(line):
                rejected.append({"line": line.strip()[:120],
                                 "reason": "a list line must start with `- ` or `* ` (`- [area:<area>] <what> - <why> (files: <paths>)`)"})
            continue
        if len(items) >= MAX_ITEMS:
            rejected.append({"line": line.strip()[:120], "reason": "more than %d items in one comment" % MAX_ITEMS})
            continue
        item, reason = parse_line(line)
        if item is None:
            rejected.append({"line": line.strip()[:120], "reason": reason})
        else:
            items.append(item)
    return items, rejected


def parse_comments(comments):
    """The `parse` result for a list of comments (objects with `body` and `author_association`)."""
    if not isinstance(comments, list):
        raise ValueError("comments must be a JSON list")
    items, rejected, seen = [], [], set()
    for index, comment in enumerate(comments):
        ident = comment.get("id", index) if isinstance(comment, dict) else index
        if not isinstance(comment, dict) or not isinstance(comment.get("body"), str):
            if isinstance(comment, str) and MARKER in comment:
                rejected.append({"comment": ident, "line": "", "reason": "a bare string has no author association"})
            continue
        if MARKER not in comment["body"]:
            continue
        if comment.get("author_association") not in TRUSTED:
            rejected.append({"comment": ident, "line": "", "reason": "its author association is %s, not OWNER, "
                             "MEMBER or COLLABORATOR" % (comment.get("author_association") or "absent")})
            continue
        got, bad = parse_body(comment["body"])
        for item in got:
            key = json.dumps(item, sort_keys=True)
            if key not in seen:
                seen.add(key)
                items.append(item)
        rejected.extend(dict(r, comment=ident) for r in bad)
    extra = max(0, len(rejected) - MAX_REJECTED)
    result = {"items": items, "rejected": rejected[:MAX_REJECTED]}
    if extra:
        result["rejected_more"] = extra
    return result


def main(argv):
    if len(argv) != 3 or argv[1] not in ("parse", "check"):
        print(__doc__.split("\n\n")[0] + "\nusage: deferred.py parse <comments.json> | check <body-file>", file=sys.stderr)
        return 2
    try:
        with open(argv[2], encoding="utf-8") as fh:
            text = fh.read()
        if argv[1] == "check":
            items, rejected = parse_body(text)
            print(json.dumps({"items": items, "rejected": rejected}, indent=2))
            return 0 if items and not rejected else 1
        print(json.dumps(parse_comments(json.loads(text)), indent=2))
        return 0
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print("deferred.py: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

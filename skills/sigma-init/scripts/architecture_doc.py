#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The architecture document linter and the `architecture.system_root` resolver (#1783, R-03).

WHY THIS EXISTS. An adopting repository keeps one architecture document with a fixed arc42-lite
skeleton (eleven level-2 headings), a system-context and a container diagram drawn as Mermaid
`flowchart` blocks with C4 naming, and a Technology inventory table. Nothing else in Sigma reads
such a document, so without a check it rots silently: a heading is dropped, a diagram stops
parsing, a container is added with no inventory row. This module is that check, and the one place
the required headings and the path rules for `architecture.system_root` live. The standard itself
is `docs/architecture-doc.md`.

THE GESTURE (copied from the docs; the control runs exactly this):

    python3 skills/sigma-init/scripts/architecture_doc.py lint <dir>

VERBS:
    lint [<dir>] [--sdlc D] [--repo-root R]   lint <dir>/architecture.md (default: the resolved
                                              architecture.system_root folder)
    resolve [--sdlc D] [--repo-root R]        print the resolved system root and its form
    headings                                  print the eleven required headings
`--repo-root` defaults to the current directory and `--sdlc` to `<repo-root>/.sdlc`. The flag is
spelled `--sdlc`, as in `preflight.py`; do not lengthen it (the longer spelling is a retired
placeholder that `tests/test_no_private_names.py` rejects).

EXIT CODES: 0 clean; 1 findings, one per stdout line as `<file>:<line>: [<code>] <message>`
(line 0 means the whole file), sorted by line then code; 2 usage error, or a file that is
unreadable, larger than READ_CAP, not a regular file, or a symlink that leaves the directory.
Finding codes (closed set): missing-file, missing-heading, duplicate-heading, missing-diagram,
unsupported-keyword, no-keyword, unbalanced-bracket, unbalanced-subgraph, node-no-technology,
container-no-inventory, line-too-long, unclosed-fence, system-root. `lint` with no <dir> exits 2
when `architecture.system_root` is a repository URL (there is no local folder to read).

WHAT IS CHECKED (structural only; this is not a Mermaid parser and a block that passes can still
fail to render in some host):
  * the eleven headings, level 2, outside fences, case-insensitive, optional leading number,
    present and unique; order is not enforced;
  * a `flowchart`/`graph` block under `Context and scope` and one under `Building blocks`;
  * every ```mermaid block opens with a supported keyword (flowchart, graph, sequenceDiagram,
    stateDiagram-v2, erDiagram, classDiagram); Mermaid's C4 keywords are reported as unsupported;
  * per line, `[ ]`, `( )`, `{ }` counts match and `"` is even, counted over the whole line
    (quoted text included); `{ }` is not counted in erDiagram, classDiagram and stateDiagram-v2
    blocks, whose braces open multi-line bodies and ER cardinalities;
  * `subgraph`/`end` pair in flowchart blocks (sequence diagrams close loop/alt with `end` too);
  * a code fence still open at the end of the file is a finding (everything after it reads as code);
  * every node `id["Name [Technology]"]` of every flowchart under `Building blocks` has a row named
    exactly `Name` (trimmed, case-folded; `<br>` in the label is a space) in the FIRST table under
    `Technology inventory` (header and delimiter rows are not rows; a cell's surrounding bold
    `**`/`__`, `[text](link)` and backticks are ignored), unless its technology is `Person` or `External system`; a node with no
    `[Technology]` is a finding. Ids are Unicode word characters with single hyphens; `%%` comment
    lines, edge text `|...|` and `subgraph`/`end`/`click`/`style`/`classDef`/`class`/`linkStyle`/
    `direction` lines define no node; the first definition of an id wins; the `/` or `\\` of a
    parallelogram or trapezoid (`q[/"Queue [Kafka]"/]`) is shape punctuation, not label;
  * `architecture.system_root` (see resolve_system_root): an invalid value is always a finding
    when the config is readable (a folder holding a control character is invalid, and a URL needs
    2,048 characters or fewer, no whitespace or control character, a host and a repository path);
    a folder's existence is checked only when the key is explicit and not the default
    `docs/architecture`.
An unreadable or missing `.sdlc/config.json` is never a finding: one stderr line says so and the
default is used (`lint <dir>` says `architecture.system_root not checked`).

The proof that each clause is load-bearing is
`tests/test_architecture_doc.py::test_each_clause_broken_alone_goes_red_through_documented_gesture`,
which breaks every clause function below alone and runs the documented gesture against it. The
two behaviours that gesture cannot reach (`lint` with no <dir> and a URL root exits 2; `resolve`
prints a value holding a control character as JSON) are pinned by `test_resolve_verb_output`.

BOUNDS AND SCALE. One file per run, one pass, linear in file size, flat in repository count. The
read is capped at READ_CAP (1 MiB): cap + 1 bytes are read and a larger file is refused with exit
2 and its size, never truncated (a truncation could hide a planted container past the cut). A
line longer than LINE_CAP (4096 characters) is a finding and is skipped by every pattern scan, so
no regular expression ever sees an unbounded line. No pattern has a nested quantifier, and no two
adjacent quantifiers can match the same character except where the second is a final `(.*)$`, so
every scan is linear in the line (see the comment above `_FENCE`; the review that found the earlier
super-linear patterns is why the adversarial cases below are measured). At 10x or 100x
more repositories the cost is unchanged (one file each); a 1,000-container diagram is unreadable
long before it nears 1 MiB. No lock, writer, table, network, `gh` call or background process;
read-only, so recovery from any failure is "run it again".

Measured 2026-10-18 on arm64 macOS 26.6.2, warm cache, best of 5, wall time of the full
documented gesture including interpreter start, on Python 3.12.13 and on the system Python 3.9.6
(bare interpreter start on that host: 17 ms and 19 ms). "1 MiB of such lines" is as many whole
lines as fit under READ_CAP (1,045,582 to 1,047,706 bytes), inside a valid document. Re-measured
after code review 3 (which made digit-led ids count, stopped re-walking a block for its keyword and
dropped blank lines from a block's body):
  * a valid document of 1 MiB (18,013 containers, one inventory row each) lints clean in 88 ms on
    3.12.13 and 122 ms on 3.9.6 (59 ms and 89 ms of it in-process);
  * 1 MiB of blank lines before `flowchart` in the Building blocks diagram: 146 ms and 230 ms
    (115 ms and 200 ms in-process; linear: 84, 133 and 233 ms on 3.9.6 at a quarter, half and all
    of it; 1,045 ms and 1,695 ms before that review, when every check walked the block again for
    its keyword); the same lines in another mermaid block: 145 ms and 236 ms; outside any block,
    the slowest case measured: 185 ms and 286 ms; 1 MiB of `%%` comment lines before `flowchart`:
    60 ms and 83 ms;
  * a 4096-character adversarial mermaid line (repeated `["a [b`) is reported in 28 ms and 32 ms;
  * a file of READ_CAP + 1 bytes is refused in 28 ms and 31 ms;
  * one prose line of 3,000 `~` then a backtick: 28 ms and 31 ms; 1 MiB of 4,000-`~`-then-backtick
    lines: 28 ms and 33 ms;
  * one 4096-character identifier run (`aaa...`, no opener) in the container flowchart: 28 ms and
    31 ms; 1 MiB of such lines: 51 ms and 57 ms; 1 MiB of 4095-character `a-a-a-...` runs: 53 ms
    and 60 ms;
  * one heading line `## ` + 2,000 ` #` + `x`: 28 ms and 32 ms; 1 MiB of such lines: 30 ms and
    37 ms.
The patterns these replaced, measured once on the same inputs on 3.12.13: the one tilde line took
15.6 s, the 1 MiB identifier file 45.2 s, the hyphenated file 10.6 s, the heading file 9.8 s, and the
1 MiB tilde file was killed unfinished at 120 s; the valid 1 MiB document took 73 ms (16 ms less
than the 89 ms measured after code review 2; the difference was not attributed). The inventory is
a set: with a tuple the earlier 1 MiB file took 725 ms (quadratic lookups), which is why it is not
one. Unmeasured: Windows (drive-letter
and UNC values are refused by shape only, not measured on Windows), and any cold-cache read.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import FrozenSet, List, NamedTuple, Optional, Tuple

USAGE = ("usage: architecture_doc.py lint [<dir>] [--sdlc D] [--repo-root R] | "
         "resolve [--sdlc D] [--repo-root R] | headings\n"
         "  exit 0 clean, 1 findings (<file>:<line>: [<code>] <message>), 2 usage or unreadable file")

READ_CAP = 1024 * 1024
LINE_CAP = 4096
URL_CAP = 2048
DEFAULT_SYSTEM_ROOT = "docs/architecture"
DOC_NAME = "architecture.md"

REQUIRED_HEADINGS = ("Context and scope", "Constraints", "Solution strategy", "Building blocks",
                     "Runtime view", "Deployment", "Cross-cutting concepts", "Decisions",
                     "Quality requirements", "Risks and technical debt", "Technology inventory")
KNOWN_KEYWORDS = ("flowchart", "graph", "sequenceDiagram", "stateDiagram-v2", "erDiagram", "classDiagram")
C4_KEYWORDS = ("C4Context", "C4Container", "C4Component", "C4Dynamic", "C4Deployment")
FLOWCHART_KEYWORDS = ("flowchart", "graph")
BRACE_BODY_KEYWORDS = ("erDiagram", "classDiagram", "stateDiagram-v2")
EXCLUDED_FIRST_TOKENS = ("subgraph", "end", "click", "style", "classDef", "class", "linkStyle", "direction")
URL_PREFIXES = ("https://", "ssh://", "git@")

_CONTEXT = "context and scope"
_BUILDING = "building blocks"
_INVENTORY = "technology inventory"

# Every pattern below is linear in the line it scans: no two adjacent quantifiers can match the
# same character unless the second one is the last thing in the pattern (`(.*)$`, which never
# backtracks), and none is nested. The info string of a fence, the trailing `#`s of a heading and
# the cells of a delimiter row are read by hand, not by a pattern, because the patterns that did
# that were super-linear (a 3,000-character `~~~...~` line took 17.6 s).
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}##[ \t]+(.*)$")
_NUMBER = re.compile(r"^\d+\.?\s*")
# A whole identifier run ending right before an opener. The lookbehind makes a search that starts
# inside a run fail at once, so a long run with no opener costs one pass, not one pass per start.
_NODE = re.compile(r"(?<![\w-])[\w-]+(?=[\[({])")
_EDGE_TEXT = re.compile(r"\|[^|]*\|")
_DELIMITER_CELL = re.compile(r":?-+:?")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_URL_HOST = re.compile(r"^[A-Za-z0-9.-]+(:\d+)?$")


class Resolved(NamedTuple):
    """Where the architecture document lives. `form` is folder, url or invalid; `value` is the
    configured value (or the default); `path` is absolute for a folder, else None; `error` names
    `architecture.system_root` when invalid; `note` is "config unreadable: default used" or None."""
    form: str
    value: object
    path: Optional[Path]
    error: Optional[str]
    note: Optional[str]


class Block(NamedTuple):
    start: int                      # line number of the opening fence
    heading: Optional[str]          # normalised level-2 heading the block sits under
    lines: List[Tuple[int, str]]    # content lines (front matter, blank and over-cap lines dropped)
    first: Optional[str]            # the keyword: first word of the first non-comment line, else None


class Doc(NamedTuple):
    headings: List[Tuple[str, int]]   # (normalised name, line)
    blocks: List[Block]
    inventory: FrozenSet[str]         # first-column cells, stripped and case-folded (a set: lookups stay O(1))
    unclosed: Optional[int]           # line of a code fence still open at the end of the file, else None


class Refusal(Exception):
    """An exit-2 condition: the document cannot be read safely."""


# --------------------------------------------------------------------------------------- reading

def _read_bounded(path: str, directory: str) -> str:
    real = os.path.realpath(path)
    base = os.path.realpath(directory)
    if not real.startswith(base + os.sep):
        raise Refusal("%s: symlink leaves the directory (%s)" % (path, real))
    try:
        mode = os.stat(real).st_mode
    except OSError as exc:
        raise Refusal("%s: unreadable (%s)" % (path, exc.strerror or exc))
    if not stat.S_ISREG(mode):
        raise Refusal("%s: not a regular file" % path)
    try:
        with open(real, "rb") as fh:
            raw = fh.read(READ_CAP + 1)
    except OSError as exc:
        raise Refusal("%s: unreadable (%s)" % (path, exc.strerror or exc))
    if len(raw) > READ_CAP:
        size = os.stat(real).st_size
        raise Refusal("%s: %d bytes is over the %d-byte read cap; not linted (never truncated)"
                      % (path, max(size, len(raw)), READ_CAP))
    text = raw.decode("utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _norm_heading(text: str) -> str:
    return _NUMBER.sub("", text.strip()).strip().casefold()


def _first_word(line: str) -> str:
    parts = line.split()
    return parts[0].rstrip(";") if parts else ""


def _strip_front_matter(lines: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """Drop a leading Mermaid `---` ... `---` front-matter block (title, config)."""
    i = 0
    while i < len(lines) and not lines[i][1].strip():
        i += 1
    if i < len(lines) and lines[i][1].strip() == "---":
        for j in range(i + 1, len(lines)):
            if lines[j][1].strip() == "---":
                return lines[:i] + lines[j + 1:]
    return lines


def _fence_line(line: str) -> Optional[Tuple[str, str]]:
    """(fence run, info word) when `line` is a code-fence line, else None. A line whose text after
    the run holds a backtick is not a fence. The info word is the first whitespace-free run after
    leading spaces and tabs, read by hand (linear)."""
    match = _FENCE.match(line)
    if not match or "`" in match.group(2):
        return None
    info = match.group(2).lstrip(" \t")
    info = info.split(None, 1)[0] if info and not info[0].isspace() else ""
    return match.group(1), info


def _closes(fence: Tuple[str, int, bool, int], run: str, line: str) -> bool:
    """A closing fence: the opener's character, at least its length, and nothing else on the line
    (so no info string either)."""
    return run[0] == fence[0] and len(run) >= fence[1] and line.strip() == run


def _first_table(lines: List[str]) -> List[str]:
    """The first run of consecutive `|` lines (stripped) among `lines`; later tables are ignored."""
    table: List[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("|"):
            table.append(s)
        elif table:
            break
    return table


def _is_delimiter(row: str) -> bool:
    """A table delimiter row such as `| --- | :-: |`: every cell is `:?-+:?` after trimming."""
    s = row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return all(_DELIMITER_CELL.fullmatch(cell.strip()) for cell in s.split("|"))


def _body_rows(table: List[str]) -> List[str]:
    """The data rows: the header row is never a row, nor is a delimiter row under it."""
    return table[2:] if len(table) > 1 and _is_delimiter(table[1]) else table[1:]


def _unwrap_bold(cell: str) -> str:
    """`**Web UI**` or `__Web UI__` -> `Web UI`."""
    for mark in ("**", "__"):
        if len(cell) > 4 and cell.startswith(mark) and cell.endswith(mark):
            return cell[2:-2]
    return cell


def _unwrap_link(cell: str) -> str:
    """`[Web UI](web.md)` -> `Web UI`. Read by hand, one pass: the text holds no `]`."""
    close = cell.find("](")
    if cell.startswith("[") and cell.endswith(")") and close > 0 and "]" not in cell[1:close]:
        return cell[1:close]
    return cell


def _unwrap_code(cell: str) -> str:
    """`` `Web UI` `` -> `Web UI`."""
    return cell.strip("`")


def _cell_name(cell: str) -> str:
    """An inventory cell as a name: trimmed; a bold, link and code wrapper removed, in that order;
    case-folded."""
    name = cell.strip()
    for unwrap in (_unwrap_bold, _unwrap_link, _unwrap_code):
        name = unwrap(name).strip()
    return name.casefold()


def _heading_text(raw: str) -> str:
    """A heading's text without its optional closing `#`s (`## Constraints ##`)."""
    return raw.rstrip(" \t#")


def _leading_token(lines: List[Tuple[int, str]]) -> Optional[str]:
    for _, line in lines:
        s = line.strip()
        if s and not s.startswith("%%"):
            return _first_word(s)
    return None


def _block(start: int, heading: Optional[str], body: List[Tuple[int, str]]) -> Block:
    """A mermaid block, its keyword read once here (every check asks for it; a block of a million
    lines must not be walked once per check to find it)."""
    lines = _strip_front_matter(body)
    return Block(start, heading, lines, _leading_token(lines))


def _keep_unclosed(fence: Optional[Tuple[str, int, bool, int]]) -> bool:
    """A mermaid block still open at the end of the file is kept and checked like any other."""
    return fence is not None and fence[2]


def _parse(lines: List[str]) -> Doc:
    headings: List[Tuple[str, int]] = []
    blocks: List[Block] = []
    heading: Optional[str] = None
    fence: Optional[Tuple[str, int, bool, int]] = None     # (char, length, is_mermaid, start)
    body: List[Tuple[int, str]] = []
    inventory_lines: List[str] = []
    for number, line in enumerate(lines, 1):
        if len(line) > LINE_CAP:
            continue            # reported by _check_line_length; never pattern-scanned
        found = _fence_line(line)
        if fence is not None:
            if found and _closes(fence, found[0], line):
                if fence[2]:
                    blocks.append(_block(fence[3], heading, body))
                fence, body = None, []
            elif fence[2] and line.strip():
                body.append((number, line))     # a blank line holds nothing any check reads
            continue
        if found:
            fence = (found[0][0], len(found[0]), found[1] == "mermaid", number)
            body = []
            continue
        h = _HEADING.match(line)
        if h:
            heading = _norm_heading(_heading_text(h.group(1)))
            headings.append((heading, number))
            continue
        if heading == _INVENTORY:
            inventory_lines.append(line)
    unclosed = fence[3] if fence is not None else None
    if _keep_unclosed(fence):
        blocks.append(_block(fence[3], heading, body))
    inventory = frozenset(_cell_name(row.strip("|").split("|")[0])
                          for row in _body_rows(_first_table(inventory_lines)))
    return Doc(headings, blocks, inventory, unclosed)


def _first_token(block: Block) -> Optional[str]:
    return block.first


# ---------------------------------------------------------------------------------- the clauses

def _check_line_length(lines: List[str]) -> list:
    return [(n, "line-too-long", "line is %d characters, over the %d-character cap" % (len(line), LINE_CAP))
            for n, line in enumerate(lines, 1) if len(line) > LINE_CAP]


def _check_headings(headings: List[Tuple[str, int]]) -> list:
    present = {name for name, _ in headings}
    return [(0, "missing-heading", "missing required heading '## %s'" % name)
            for name in REQUIRED_HEADINGS if name.casefold() not in present]


def _check_duplicate_headings(headings: List[Tuple[str, int]]) -> list:
    required = {name.casefold(): name for name in REQUIRED_HEADINGS}
    seen, out = set(), []
    for name, line in headings:
        if name in required:
            if name in seen:
                out.append((line, "duplicate-heading", "heading '## %s' appears more than once" % required[name]))
            seen.add(name)
    return out


def _has_context_diagram(blocks: List[Block]) -> bool:
    return any(b.heading == _CONTEXT and _first_token(b) in FLOWCHART_KEYWORDS for b in blocks)


def _has_container_diagram(blocks: List[Block]) -> bool:
    return any(b.heading == _BUILDING and _first_token(b) in FLOWCHART_KEYWORDS for b in blocks)


def _check_required_diagrams(blocks: List[Block], heading_lines: dict) -> list:
    out = []
    if not _has_context_diagram(blocks):
        out.append((heading_lines.get(_CONTEXT, 0), "missing-diagram",
                    "no Mermaid flowchart (system-context diagram) under '## Context and scope'"))
    if not _has_container_diagram(blocks):
        out.append((heading_lines.get(_BUILDING, 0), "missing-diagram",
                    "no Mermaid flowchart (container diagram) under '## Building blocks'"))
    return out


def _is_c4_keyword(token: str) -> bool:
    return token in C4_KEYWORDS


def _is_known_keyword(token: str) -> bool:
    return token in KNOWN_KEYWORDS


def _check_keyword(block: Block) -> list:
    tok = _first_token(block)
    if tok is None:
        return [(block.start, "no-keyword", "mermaid block opened at line %d is empty" % block.start)]
    if _is_c4_keyword(tok):
        return [(block.start, "unsupported-keyword",
                 "mermaid block opened at line %d uses '%s': Mermaid's C4 syntax is experimental and not "
                 "rendered everywhere; draw C4 as a flowchart" % (block.start, tok))]
    if not _is_known_keyword(tok):
        return [(block.start, "no-keyword",
                 "mermaid block opened at line %d does not open with a supported diagram keyword (got '%s')"
                 % (block.start, tok[:40]))]
    return []


def _square_ok(line: str) -> bool:
    return line.count("[") == line.count("]")


def _round_ok(line: str) -> bool:
    return line.count("(") == line.count(")")


def _curly_ok(line: str) -> bool:
    return line.count("{") == line.count("}")


def _quote_ok(line: str) -> bool:
    return line.count('"') % 2 == 0


def _counts_braces(block: Block) -> bool:
    """`{ }` is counted except in erDiagram, classDiagram and stateDiagram-v2 blocks."""
    return _first_token(block) not in BRACE_BODY_KEYWORDS


def _skip_in_balance(line: str) -> bool:
    """A `%%` comment line is not balance-checked."""
    return line.strip().startswith("%%")


def _check_balance(block: Block) -> list:
    braces = _counts_braces(block)
    for number, line in block.lines:
        if _skip_in_balance(line):
            continue
        if not (_square_ok(line) and _round_ok(line) and (_curly_ok(line) or not braces) and _quote_ok(line)):
            return [(number, "unbalanced-bracket",
                     "mermaid block opened at line %d: unbalanced brackets or quotes on this line"
                     % block.start)]
    return []


def _stray_end(lines: List[Tuple[int, str]]) -> bool:
    depth = 0
    for _, line in lines:
        word = _first_word(line)
        if word == "subgraph":
            depth += 1
        elif word == "end":
            if depth == 0:
                return True
            depth -= 1
    return False


def _unclosed_subgraph(lines: List[Tuple[int, str]]) -> bool:
    depth = 0
    for _, line in lines:
        word = _first_word(line)
        if word == "subgraph":
            depth += 1
        elif word == "end":
            depth = max(0, depth - 1)
    return depth > 0


def _checks_subgraphs(block: Block) -> bool:
    """Only flowcharts pair `subgraph`/`end`; a sequence diagram closes loop/alt/opt with `end`."""
    return _first_token(block) in FLOWCHART_KEYWORDS


def _check_subgraph(block: Block) -> list:
    if not _checks_subgraphs(block):
        return []
    if _stray_end(block.lines):
        return [(block.start, "unbalanced-subgraph",
                 "mermaid block opened at line %d has an 'end' with no open 'subgraph'" % block.start)]
    if _unclosed_subgraph(block.lines):
        return [(block.start, "unbalanced-subgraph",
                 "mermaid block opened at line %d has a 'subgraph' with no 'end'" % block.start)]
    return []


def _check_unclosed_fence(unclosed: Optional[int]) -> list:
    if unclosed is None:
        return []
    return [(unclosed, "unclosed-fence",
             "code fence opened at line %d is never closed: everything after it is read as code" % unclosed)]


def _strip_line_breaks(label: str) -> str:
    """`<br>`, `<br/>` and `<br />` in a label become a space, so `Web UI<br/>[TypeScript]` is `Web UI`."""
    return _BR.sub(" ", label)


def _extract_label_parts(label: str) -> Tuple[str, Optional[str]]:
    label = _strip_line_breaks(label)
    close = label.rfind("]")
    opening = label.rfind("[", 0, close) if close >= 0 else -1
    if opening < 0:
        return label.strip(), None
    technology = label[opening + 1:close].strip()
    return label[:opening].strip(), (technology or None)


def _check_node_technology(nodes: list) -> list:
    return [(line, "node-no-technology", "container '%s' has no [Technology] in its label" % name)
            for name, technology, line in nodes if technology is None]


def _is_exempt(technology: str) -> bool:
    return technology.casefold() in ("person", "external system")


def _name_matches(name: str, inventory_names: FrozenSet[str]) -> bool:
    return name.strip().casefold() in inventory_names


def _check_inventory(containers: list, inventory_names: FrozenSet[str]) -> list:
    out = []
    for name, technology, line in containers:
        if technology is not None and _is_exempt(technology):
            continue
        if not _name_matches(name, inventory_names):
            out.append((line, "container-no-inventory",
                        "container '%s' has no row in the Technology inventory table" % name))
    return out


def _is_excluded_line(line: str) -> bool:
    return _first_word(line) in EXCLUDED_FIRST_TOKENS


def _skip_in_scan(line: str) -> bool:
    """A `%%` comment line defines no node."""
    return line.strip().startswith("%%")


def _strip_edge_text(line: str) -> str:
    """Edge text `-->|text|` is not a node definition, whatever brackets it holds."""
    return _EDGE_TEXT.sub(" ", line)


def _node_id(run: str) -> Optional[str]:
    """The identifier at the end of an identifier run before an opener, or None. An identifier is
    word characters (letters, digits, `_`, so `1["..."]` and `2a["..."]` are ids) joined by single
    hyphens, so the edge in `A---B[` is cut off (`B`) and a leading `-` is dropped; a run ending in
    `-` has none. Read by hand, one pass."""
    if run.endswith("-"):
        return None
    cut = run.rfind("--")
    if cut >= 0:
        run = run[cut + 2:]
    run = run.lstrip("-")
    return run or None


def _skip_slant(text: str, i: int) -> int:
    """Past the `/` or `\\` that opens a parallelogram or trapezoid (`[/"..."/]`, `[\\"..."\\]`),
    so a quoted label after it is read as quoted."""
    return i + 1 if i < len(text) and text[i] in "/\\" else i


def _scan_nodes(line: str) -> list:
    """Every node definition on one line, in order: (id, label). An identifier immediately followed
    by an opener; shape punctuation (`[(`, `([`, `[[`, `((`, `{{`, `/`, `\\`) is not part of the label."""
    text = _strip_edge_text(line)
    out, pos = [], 0
    while True:
        match = _NODE.search(text, pos)
        if not match:
            return out
        node_id = _node_id(match.group(0))
        if node_id is None:
            pos = match.end()
            continue
        i = match.end()
        while i < len(text) and text[i] in "[({":
            i += 1
        i = _skip_slant(text, i)
        while i < len(text) and text[i] == " ":
            i += 1
        if i < len(text) and text[i] == '"':
            close = text.find('"', i + 1)
            if close < 0:
                return out
            label, i = text[i + 1:close], close + 1
        else:
            depth, start = 0, i
            while i < len(text):
                ch = text[i]
                if ch in "[({":
                    depth += 1
                elif ch in "])}":
                    if depth == 0:
                        break
                    depth -= 1
                i += 1
            label = text[start:i].strip().strip("/\\").strip()
        out.append((node_id, label))
        pos = max(i, match.end())


def _unique_nodes(found: list) -> list:
    """(id, label, line) definitions with repeats of an id dropped: the first definition wins."""
    seen, out = set(), []
    for node in found:
        if node[0] not in seen:
            seen.add(node[0])
            out.append(node)
    return out


def _containers(blocks: List[Block]) -> list:
    found = []
    for block in blocks:
        if block.heading != _BUILDING or _first_token(block) not in FLOWCHART_KEYWORDS:
            continue
        for number, line in block.lines:
            if _skip_in_scan(line) or _is_excluded_line(line):
                continue
            for node_id, label in _scan_nodes(line):
                found.append((node_id, label, number))
    out = []
    for _, label, number in _unique_nodes(found):
        name, technology = _extract_label_parts(label)
        out.append((name, technology, number))
    return out


def lint_text(text: str) -> list:
    """All findings for one document's text, as (line, code, message)."""
    lines = text.split("\n")
    doc = _parse(lines)
    heading_lines = {}
    for name, line in doc.headings:
        heading_lines.setdefault(name, line)
    findings = _check_line_length(lines)
    findings += _check_unclosed_fence(doc.unclosed)
    findings += _check_headings(doc.headings)
    findings += _check_duplicate_headings(doc.headings)
    findings += _check_required_diagrams(doc.blocks, heading_lines)
    for block in doc.blocks:
        findings += _check_keyword(block)
        findings += _check_balance(block)
        findings += _check_subgraph(block)
    containers = _containers(doc.blocks)
    findings += _check_node_technology(containers)
    findings += _check_inventory(containers, doc.inventory)
    return findings


# ----------------------------------------------------------------------------------- system root

def _over_read_cap(raw: bytes) -> bool:
    return len(raw) > READ_CAP


def _read_config(sdlc_dir: str) -> Tuple[Optional[dict], str]:
    """The parsed config object, or (None, reason): missing, unreadable, not a regular file (a FIFO
    would block the read forever, so it is never opened), over READ_CAP, invalid JSON, not an
    object."""
    path = os.path.join(sdlc_dir, "config.json")
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if not stat.S_ISREG(mode):
        return None, "not a regular file"
    try:
        with open(path, "rb") as fh:
            raw = fh.read(READ_CAP + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if _over_read_cap(raw):
        return None, "over the %d-byte read cap" % READ_CAP
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return None, "invalid JSON"
    if not isinstance(data, dict):
        return None, "not an object"
    return data, ""


def _architecture_block(config: dict) -> Tuple[Optional[dict], Optional[str]]:
    if "architecture" not in config:
        return {}, None
    block = config["architecture"]
    if isinstance(block, dict):
        return block, None
    return None, "architecture.system_root: architecture must be an object"


def _key_explicit(block: dict) -> bool:
    value = block.get("system_root")
    return value is not None and value != DEFAULT_SYSTEM_ROOT


def _refuse_nonstring(value: object) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return "architecture.system_root: must be a non-empty string (got %s)" % json.dumps(value)
    return None


def _has_control(value: str) -> bool:
    """A C0 control character or DEL (newline and NUL included)."""
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _url_over_cap(value: str) -> bool:
    return len(value) > URL_CAP


def _url_has_blank(value: str) -> bool:
    return any(ch.isspace() for ch in value) or _has_control(value)


def _url_host_ok(host: str) -> bool:
    return bool(host) and _URL_HOST.match(host) is not None


def _url_path_ok(rest: str) -> bool:
    return bool(rest.strip("/"))


def _refuse_url_shape(value: str) -> Optional[str]:
    bad = "architecture.system_root: malformed repository URL"
    if _url_over_cap(value):
        return "%s (over %d characters)" % (bad, URL_CAP)
    if _url_has_blank(value):
        return "%s (whitespace or control character)" % bad
    if value.startswith("git@"):
        host, _, rest = value[len("git@"):].partition(":")
    else:
        authority, _, rest = value.split("://", 1)[1].partition("/")
        host = authority.rpartition("@")[2]
    if not _url_host_ok(host):
        return "%s (needs a host: letters, digits, '.', '-', optional :port)" % bad
    if not _url_path_ok(rest):
        return "%s (needs a repository path after the host)" % bad
    return None


def _refuse_control(value: str) -> Optional[str]:
    if _has_control(value):
        return "architecture.system_root: a control character is not allowed in a folder (%s)" % json.dumps(value)
    return None


def _refuse_absolute(value: str) -> Optional[str]:
    if value.startswith("/"):
        return "architecture.system_root: must be a repository-relative folder, not an absolute path (%s)" % value
    return None


def _refuse_dotdot(value: str) -> Optional[str]:
    if ".." in re.split(r"[/\\]", value):
        return "architecture.system_root: a '..' segment is not allowed (%s)" % value
    return None


def _refuse_drive_or_unc(value: str) -> Optional[str]:
    if re.match(r"[A-Za-z]:", value) or value.startswith("\\\\"):
        return "architecture.system_root: must be a repository-relative folder, not an absolute path (%s)" % value
    return None


def _refuse_symlink_escape(value: str, repo_root: str) -> Optional[str]:
    root = os.path.realpath(repo_root)
    real = os.path.realpath(os.path.join(repo_root, value))
    if real != root and not real.startswith(root + os.sep):
        return "architecture.system_root: resolves outside the repository (%s -> %s)" % (value, real)
    return None


def resolve_system_root(sdlc_dir: str, repo_root: str) -> Resolved:
    """Resolve `architecture.system_root` from `<sdlc_dir>/config.json` against `repo_root`.

    The one function later consumers import by sibling path. Never touches the network and never
    checks a folder's existence (that is `lint`'s job, for an explicit key only)."""
    return _resolve_config(_read_config(sdlc_dir)[0], repo_root)


def _resolve_config(config: Optional[dict], repo_root: str) -> Resolved:
    """resolve_system_root on a config already read (None: unreadable), so a run reads it once."""
    if config is None:
        return Resolved("folder", DEFAULT_SYSTEM_ROOT,
                        Path(os.path.realpath(repo_root)).joinpath(DEFAULT_SYSTEM_ROOT),
                        None, "config unreadable: default used")
    block, error = _architecture_block(config)
    if error:
        return Resolved("invalid", config.get("architecture"), None, error, None)
    value = block.get("system_root", DEFAULT_SYSTEM_ROOT)
    error = _refuse_nonstring(value)
    if error:
        return Resolved("invalid", value, None, error, None)
    if value.startswith(URL_PREFIXES):
        error = _refuse_url_shape(value)
        return Resolved("invalid" if error else "url", value, None, error, None)
    for refuse in (_refuse_control, _refuse_absolute, _refuse_dotdot, _refuse_drive_or_unc):
        error = refuse(value)
        if error:
            return Resolved("invalid", value, None, error, None)
    error = _refuse_symlink_escape(value, repo_root)
    if error:
        return Resolved("invalid", value, None, error, None)
    return Resolved("folder", value, Path(os.path.realpath(repo_root)).joinpath(value), None, None)


def _root_invalid(resolved: Resolved) -> bool:
    return resolved.form == "invalid"


def _root_missing(resolved: Resolved, explicit_key: bool) -> bool:
    return resolved.form == "folder" and explicit_key and not resolved.path.is_dir()


def _url_like(value: str) -> bool:
    """A folder value that looks like a URL (`http://...`, `HTTPS://...`): its missing-folder
    message says which URL forms are read as URLs."""
    return "://" in value


def _check_system_root(resolved: Resolved, explicit_key: bool) -> list:
    if _root_invalid(resolved):
        return [(0, "system-root", resolved.error)]
    if _root_missing(resolved, explicit_key):
        hint = ("; a repository URL must start with lowercase https://, ssh:// or git@"
                if _url_like(resolved.value) else "")
        return [(0, "system-root", "architecture.system_root: folder %s does not exist (%s)%s"
                 % (resolved.value, resolved.path, hint))]
    return []


# ------------------------------------------------------------------------------------------ CLI

def _parse_args(args: List[str], allow_dir: bool) -> Optional[dict]:
    opts = {"dir": None, "sdlc": None, "repo_root": None}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--sdlc", "--repo-root"):
            if i + 1 >= len(args):
                return None
            opts["sdlc" if arg == "--sdlc" else "repo_root"] = args[i + 1]
            i += 2
            continue
        if arg.startswith("-") or not allow_dir or opts["dir"] is not None:
            return None
        opts["dir"] = arg
        i += 1
    return opts


def _locations(opts: dict) -> Tuple[str, str]:
    repo_root = opts["repo_root"] or os.getcwd()
    return opts["sdlc"] or os.path.join(repo_root, ".sdlc"), repo_root


def _note(reason: str, explicit_dir: bool) -> None:
    tail = "architecture.system_root not checked" if explicit_dir else "default %s used" % DEFAULT_SYSTEM_ROOT
    print("architecture_doc: config unreadable (%s): %s" % (reason, tail), file=sys.stderr)


def _emit(findings: list) -> None:
    for path, line, code, message in sorted(findings, key=lambda f: (f[1], f[2])):
        print("%s:%d: [%s] %s" % (path, line, code, message))


def _url_note() -> None:
    print("architecture.system_root: repository URL, existence not checked (no network)")


def _not_a_directory(target: str) -> bool:
    """`lint path/to/architecture.md`: the target exists but is a file, not the folder holding it."""
    return os.path.exists(target) and not os.path.isdir(target)


def _lint(opts: dict) -> int:
    sdlc_dir, repo_root = _locations(opts)
    config_path = os.path.join(sdlc_dir, "config.json")
    config, reason = _read_config(sdlc_dir)
    resolved = _resolve_config(config, repo_root)
    findings = []
    if config is None:
        _note(reason, opts["dir"] is not None)
    else:
        block, _ = _architecture_block(config)
        explicit = block is not None and _key_explicit(block)
        findings += [(config_path,) + f for f in _check_system_root(resolved, explicit)]
        if resolved.form == "url":
            _url_note()
    if opts["dir"] is not None:
        target, from_key = opts["dir"], ""
    elif resolved.form == "folder":
        target = str(resolved.path)
        from_key = " (architecture.system_root = %s)" % resolved.value
    elif resolved.form == "url":
        print("architecture_doc: repository URL: run the lint inside that repository, or pass a directory",
              file=sys.stderr)
        return 2
    else:
        _emit(findings)
        return 1
    doc_path = os.path.join(target, DOC_NAME)
    if _not_a_directory(target):
        findings.append((doc_path, 0, "missing-file", "%s is not a directory: lint takes the folder that "
                         "holds %s%s" % (target, DOC_NAME, from_key)))
    elif not os.path.isdir(target):
        findings.append((doc_path, 0, "missing-file", "directory %s does not exist%s" % (target, from_key)))
    elif not os.path.lexists(doc_path):
        findings.append((doc_path, 0, "missing-file", "%s does not exist%s" % (doc_path, from_key)))
    else:
        try:
            text = _read_bounded(doc_path, target)
        except Refusal as exc:
            print("architecture_doc: %s" % exc, file=sys.stderr)
            return 2
        findings += [(doc_path,) + f for f in lint_text(text)]
    _emit(findings)
    return 1 if findings else 0


def _shown(value: object) -> str:
    """A configured value as printed by `resolve`: a printable string as is, anything else
    (a control character, a non-string) as JSON, so it can never break or forge an output line."""
    return value if isinstance(value, str) and value.isprintable() else json.dumps(value)


def _resolve(opts: dict) -> int:
    sdlc_dir, repo_root = _locations(opts)
    config, reason = _read_config(sdlc_dir)
    resolved = _resolve_config(config, repo_root)
    if resolved.note:
        _note(reason, False)
    print("%s %s" % (_shown(resolved.value), resolved.form))
    if resolved.form == "url":
        _url_note()
    if resolved.form == "invalid":
        print(resolved.error)
        return 1
    return 0


def main(argv: List[str]) -> int:
    args = argv[1:]
    if any(a in ("-h", "--help") for a in args):
        print(USAGE)
        return 0
    if not args:
        print(USAGE, file=sys.stderr)
        return 2
    verb, rest = args[0], args[1:]
    if verb == "headings" and not rest:
        for name in REQUIRED_HEADINGS:
            print(name)
        return 0
    if verb in ("lint", "resolve"):
        opts = _parse_args(rest, allow_dir=verb == "lint")
        if opts is not None:
            return _lint(opts) if verb == "lint" else _resolve(opts)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

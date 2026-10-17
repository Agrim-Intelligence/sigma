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
container-no-inventory, line-too-long, system-root.

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
  * every node `id["Name [Technology]"]` of every flowchart under `Building blocks` has a
    Technology inventory row named exactly `Name` (trimmed, case-folded), unless its technology is
    `Person` or `External system`; a node with no `[Technology]` is a finding;
  * `architecture.system_root` (see resolve_system_root): an invalid value is always a finding
    when the config is readable; a folder's existence is checked only when the key is explicit
    and not the default `docs/architecture`.
An unreadable or missing `.sdlc/config.json` is never a finding: one stderr line says so and the
default is used (`lint <dir>` says `architecture.system_root not checked`).

The proof that each clause is load-bearing is
`tests/test_architecture_doc.py::test_each_clause_broken_alone_goes_red_through_documented_gesture`,
which breaks every clause function below alone and runs the documented gesture against it.

BOUNDS AND SCALE. One file per run, one pass, linear in file size, flat in repository count. The
read is capped at READ_CAP (1 MiB): cap + 1 bytes are read and a larger file is refused with exit
2 and its size, never truncated (a truncation could hide a planted container past the cut). A
line longer than LINE_CAP (4096 characters) is a finding and is skipped by every pattern scan, so
no regular expression ever sees an unbounded line, and none has nested quantifiers. At 10x or 100x
more repositories the cost is unchanged (one file each); a 1,000-container diagram is unreadable
long before it nears 1 MiB. No lock, writer, table, network, `gh` call or background process;
read-only, so recovery from any failure is "run it again".

Measured 2026-10-18 on arm64 macOS, warm cache, best of 5, wall time of the full documented gesture
including interpreter start: a valid document of exactly 1 MiB (16,887 containers, one inventory
row each) lints clean in 93 ms on Python 3.12.13 and 140 ms on the system Python 3.9.6 (54 ms of
that is the in-process lint on 3.12.13); a 4096-character adversarial mermaid line (repeated
`["a [b`, `subgraph `, `A --> `) is reported in 36 ms on Python 3.12.13 and 52 ms on Python 3.9.6
(0.2 ms in-process); a file of READ_CAP + 1 bytes is refused in 38 ms on Python 3.12.13 and 46 ms on
Python 3.9.6 (0.1 ms in-process). Bare interpreter start on that host is 23 ms (3.12.13) and 36 ms
(3.9.6). The inventory is a set: with a tuple the same 1 MiB file took 725 ms (quadratic lookups),
which is why it is not one. Unmeasured: Windows (drive-letter and UNC values are refused by shape
only, not measured on Windows), and any cold-cache read.
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

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^`\s]*)[^`]*$")
_HEADING = re.compile(r"^ {0,3}##[ \t]+(.*?)[ \t#]*$")
_NUMBER = re.compile(r"^\d+\.?\s*")
_NODE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*(?=[\[({])")
_EDGE_TEXT = re.compile(r"\|[^|]*\|")
_DELIMITER = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$")
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
    lines: List[Tuple[int, str]]    # content lines (front matter and over-cap lines dropped)


class Doc(NamedTuple):
    headings: List[Tuple[str, int]]   # (normalised name, line)
    blocks: List[Block]
    inventory: FrozenSet[str]         # first-column cells, stripped and case-folded (a set: lookups stay O(1))


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


def _parse(lines: List[str]) -> Doc:
    headings: List[Tuple[str, int]] = []
    blocks: List[Block] = []
    inventory: List[str] = []
    heading: Optional[str] = None
    fence: Optional[Tuple[str, int, bool, int]] = None     # (char, length, is_mermaid, start)
    body: List[Tuple[int, str]] = []
    table: List[str] = []
    table_done = False
    for number, line in enumerate(lines, 1):
        if len(line) > LINE_CAP:
            continue            # reported by _check_line_length; never pattern-scanned
        match = _FENCE.match(line)
        if fence is not None:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1] \
                    and not match.group(2) and line.strip() == match.group(1):
                if fence[2]:
                    blocks.append(Block(fence[3], heading, _strip_front_matter(body)))
                fence, body = None, []
            elif fence[2]:
                body.append((number, line))
            continue
        if match:
            fence = (match.group(1)[0], len(match.group(1)), match.group(2) == "mermaid", number)
            body = []
            continue
        h = _HEADING.match(line)
        if h:
            heading = _norm_heading(h.group(1))
            headings.append((heading, number))
            continue
        if heading == _INVENTORY and not table_done:
            if line.strip().startswith("|"):
                table.append(line.strip())
            elif table:
                table_done = True
    if fence is not None and fence[2]:
        blocks.append(Block(fence[3], heading, _strip_front_matter(body)))
    rows = table[2:] if len(table) > 1 and _DELIMITER.match(table[1]) else table[1:]
    for row in rows:
        inventory.append(row.strip("|").split("|")[0].strip().casefold())
    return Doc(headings, blocks, frozenset(inventory))


def _first_token(block: Block) -> Optional[str]:
    for _, line in block.lines:
        s = line.strip()
        if s and not s.startswith("%%"):
            return _first_word(s)
    return None


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


def _check_balance(block: Block) -> list:
    braces = _first_token(block) not in BRACE_BODY_KEYWORDS
    for number, line in block.lines:
        if line.strip().startswith("%%"):
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


def _check_subgraph(block: Block) -> list:
    if _first_token(block) not in FLOWCHART_KEYWORDS:
        return []
    if _stray_end(block.lines):
        return [(block.start, "unbalanced-subgraph",
                 "mermaid block opened at line %d has an 'end' with no open 'subgraph'" % block.start)]
    if _unclosed_subgraph(block.lines):
        return [(block.start, "unbalanced-subgraph",
                 "mermaid block opened at line %d has a 'subgraph' with no 'end'" % block.start)]
    return []


def _extract_label_parts(label: str) -> Tuple[str, Optional[str]]:
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


def _scan_nodes(line: str) -> list:
    """Every node definition on one line, in order: (id, label). An identifier immediately followed
    by an opener; shape punctuation (`[(`, `([`, `[[`, `((`, `{{`, `/`, `\\`) is not part of the label."""
    text = _EDGE_TEXT.sub(" ", line)
    out, pos = [], 0
    while True:
        match = _NODE.search(text, pos)
        if not match:
            return out
        i = match.end()
        while i < len(text) and text[i] in "[({":
            i += 1
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
        out.append((match.group(0), label))
        pos = max(i, match.end())


def _containers(blocks: List[Block]) -> list:
    seen, out = set(), []
    for block in blocks:
        if block.heading != _BUILDING or _first_token(block) not in FLOWCHART_KEYWORDS:
            continue
        for number, line in block.lines:
            if line.strip().startswith("%%") or _is_excluded_line(line):
                continue
            for node_id, label in _scan_nodes(line):
                if node_id in seen:
                    continue
                seen.add(node_id)
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

def _read_config(sdlc_dir: str) -> Tuple[Optional[dict], str]:
    """The parsed config object, or (None, reason): missing, unreadable, invalid JSON, not an object."""
    path = os.path.join(sdlc_dir, "config.json")
    try:
        with open(path, "rb") as fh:
            raw = fh.read(READ_CAP + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if len(raw) > READ_CAP:
        return None, "unreadable"
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


def _refuse_url_shape(value: str) -> Optional[str]:
    bad = "architecture.system_root: malformed repository URL"
    if len(value) > URL_CAP:
        return "%s (over %d characters)" % (bad, URL_CAP)
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return "%s (whitespace or control character)" % bad
    if value.startswith("git@"):
        host, _, rest = value[len("git@"):].partition(":")
    else:
        authority, _, rest = value.split("://", 1)[1].partition("/")
        host = authority.rpartition("@")[2]
    if not host or not _URL_HOST.match(host) or not rest.strip("/"):
        return "%s (needs a host and a repository path)" % bad
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
    config, _ = _read_config(sdlc_dir)
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
    for refuse in (_refuse_absolute, _refuse_dotdot, _refuse_drive_or_unc):
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


def _check_system_root(resolved: Resolved, explicit_key: bool) -> list:
    if _root_invalid(resolved):
        return [(0, "system-root", resolved.error)]
    if _root_missing(resolved, explicit_key):
        return [(0, "system-root", "architecture.system_root: folder %s does not exist (%s)"
                 % (resolved.value, resolved.path))]
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


def _lint(opts: dict) -> int:
    sdlc_dir, repo_root = _locations(opts)
    config_path = os.path.join(sdlc_dir, "config.json")
    resolved = resolve_system_root(sdlc_dir, repo_root)
    config, reason = _read_config(sdlc_dir)
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
    if not os.path.isdir(target):
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


def _resolve(opts: dict) -> int:
    sdlc_dir, repo_root = _locations(opts)
    resolved = resolve_system_root(sdlc_dir, repo_root)
    if resolved.note:
        _note(_read_config(sdlc_dir)[1], False)
    print("%s %s" % (resolved.value, resolved.form))
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

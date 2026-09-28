"""No unsupervised SIGSTOP (AGENTS.md's third rule, issue #1685): a structural guard against ever
reintroducing the pause-workaround shape that left two live daemons SIGSTOP'd and orphaned for
hours in this repo's own tree (see `.sdlc/research/1685.md` for the incident: an ad hoc,
never-committed script used `kill -STOP` on the private side's `--watch` daemons to dodge a DuckDB lock
conflict, then relied on a `trap ... EXIT` cleanup that never fired because the shell running it
was killed non-gracefully).

WHAT THIS CATCHES. Any owned `.py` or `.sh` file under this repo containing `SIGSTOP`, `SIGCONT`,
`kill -STOP`, or `kill -CONT` as a plain substring OUTSIDE a Python comment/docstring or a shell
comment. This is a TEXT guard, not an AST one (contrast tests/test_import_boundary.py) -- see "WHY
NOT AST" below for why a substring scan is the right altitude here, and "NAMED, ACCEPTED GAP" for
what it deliberately does not catch.

WHY NOT AST. The incident script was an ad hoc SHELL fragment run by hand, not a Python module
with importable structure -- there is no "import statement" shape to parse the way
test_import_boundary.py parses a package import. A `.sh` file has no Python AST at all, and the
riskiest reintroduction shape (a bash line, or a Python string built to be handed to a shell) is
exactly a piece of TEXT, not a language construct. A plain substring scan, with a
docstring/comment exemption, is the right tool for the shape of the actual incident.

WHY NOT "exempt every string literal". `subprocess.run(["kill", "-STOP", str(pid)])` -- the exact
incident shape restated in Python -- would also be exempt under that rule, making the guard
decorative against its own reason for existing.
`test_flags_kill_dash_STOP_inside_a_single_line_python_string` below exists specifically to prove
this trap was not fallen into: a single contiguous shell-style string IS caught.

NAMED, ACCEPTED GAP. A Python call written with the flag as a SEPARATE list element --
`subprocess.run(["kill", "-STOP", str(pid)])` -- is not caught: neither "kill -STOP" nor
SIGSTOP/SIGCONT appears anywhere in that source as a contiguous substring. Closing this needs
data-flow analysis of argv lists, disproportionate for a guard whose job is to block the LIKELY
reintroduction shape (a contiguous shell-style string or a bash line -- what the real incident
script actually was). `doctor.py`'s own runtime `ps`-based detection (issue #1685, item 2) is the
true backstop for this residual case and every other one: it observes the process's actual OS
state, not the source that produced it.

THE TRIPLE-QUOTE BOUNDARY. A banned pattern sharing a LINE with an opening or closing triple-quote
(either style -- \"\"\" or ''') must still be scanned on whichever side of it is actually code.
`_violations` below drives this off the stdlib `tokenize` module: `_python_scannable_lines` runs a
real tokenize pass over the WHOLE file, finds every real STRING token, and blanks out (overwrites
with spaces, never removes -- so column positions and line counts never move) only the span of a
STRING token whose own source text opens with a triple-quote (`_is_triple_quoted`) or a COMMENT
token. Only that blanked span is exempt, never the rest of the physical line it sits on, so
`kill -STOP  \"\"\"docstring starts here` still flags the code before the quotes, and `docstring
ends here\"\"\" ; kill -STOP` still flags the code after them; see
`test_flags_a_banned_pattern_before_an_opening_triple_quote_on_the_same_line` and
`test_flags_a_banned_pattern_after_a_closing_triple_quote_on_the_same_line` below.

This replaces an earlier, provably wrong implementation that tracked a per-line `in_docstring`
boolean by counting `\"\"\"` occurrences in the RAW line (`raw_line.split('\"\"\"')`), toggling
state each time the count was odd. That heuristic ran on raw text with zero comment or
string-literal awareness, so a `\"\"\"` sitting inside an ordinary `#` comment
(`# the docstring delimiter is spelled \"\"\"`) or inside a plain quoted string
(`DELIM = '\"\"\"'`) was indistinguishable from a real docstring delimiter and wrongly toggled the
boolean anyway -- corrupting the state for every line after it, with no `_PRAGMA` involved at all.
Reviewer-found; reproduced directly by
`test_a_comment_containing_a_triple_quote_does_not_corrupt_later_scanning` and
`test_a_string_literal_containing_triple_quote_as_data_does_not_corrupt_later_scanning` below. A
real tokenizer cannot make this mistake: it already knows, from the actual Python grammar, that a
`\"\"\"` inside a comment or another string is not a triple-quote boundary, because it never emits
a STRING token there in the first place.

Only a STRING token whose own text opens with a triple-quote, AND is not an f-string, is ever
exempted -- an ordinary '...'/"..." STRING token is left completely untouched, still fully
scannable (module docstring's "WHY NOT exempt every string literal";
`test_flags_kill_dash_STOP_inside_a_single_line_python_string` pins that
`subprocess.run("kill -STOP " + str(pid), shell=True)` -- a REGULAR string, not a triple-quoted
one -- must still be FLAGGED). Both triple-quote spellings (`\"\"\"` and `'''`) are handled
identically -- a side-effect of deriving the boundary from `tokenize`'s own grammar-level truth
rather than a hand-written `\"\"\"`-only split; the earlier version handled only the double-quote
style and had to name that as a gap (this repo has no `'''` docstring to exempt) -- and f-strings
are excluded from the exemption on EVERY supported Python version, not just where `tokenize`
happens to make that easy (see `_is_triple_quoted`'s own docstring for the Python 3.12/PEP 701
version-consistency reasoning; reviewer-found, round 2).

NAMED, ACCEPTED GAP (reviewer-found, round 2): a triple-quoted, NON-f-string command string --
e.g. `'''kill -STOP ''' + pid` -- IS exempted, exactly like a real docstring would be, even though
it is closer in spirit to the argv-split gap above than to a genuine docstring. Narrowing the
exemption to true DOCSTRING POSITION (first statement of a module/class/function body) would need
real statement-level position tracking -- realistically `ast`, not just `tokenize` -- for a shape
with no evidence of ever occurring in this codebase's actually-scanned tree: `grep -rln "'''"
--include="*.py" .` outside `.claude`/`.sdlc`/every `tests` directory returns NOTHING (measured,
this session) -- every real `'''` in this repo lives inside `tests/`, already excluded from this
guard's own real-tree scan for the unrelated reason Decision 2 names, holding non-docstring fixture
DATA (embedded fake scripts for other tests, e.g. a fake daemon entry point in a doctor test),
never a command built from banned-pattern text. A `\"\"\"`-style non-docstring command string
could exist without matching that grep, but the guard's own zero-violations real-tree test already
proves none of them CURRENTLY carries a banned pattern, docstring-position or not -- what is
unproven is only the STYLE of string, not whether today's tree is clean. Disproportionate to chase
further for the same reason the argv-split gap is: `doctor.py`'s own runtime `ps`-based detection
(issue #1685, item 2) backstops this residual case exactly like it backstops that one.
`test_a_non_f_string_triple_quoted_command_is_an_accepted_gap` below pins the CURRENT behavior so a
future change to it is a deliberate decision, not an unnoticed drift.

PERFORMANCE (AGENTS.md's SCALABILITY property: "anything with a per-item cost states how it
behaves at 10x and 100x"). Measured, this repo's own tree (184 owned `.py` files, ~5MB): the
`tokenize` pass costs ~800ms for a full `_violations(ROOT)` call, against ~130ms for the pre-rewrite
raw-line scan -- roughly 6x, +~0.67s absolute. Cost is linear in source bytes scanned, so ~8s at
10x this tree's size and ~80s at 100x, for this ONE test alone (`loop.py verify`'s own full chain
takes several minutes regardless, dominated by a private package's real-store suite, not this file).
Not a blocker today; worth re-measuring if `_owned_source_files`'s own real-tree count grows an
order of magnitude.

SHELL/PYTHON COMMENTS. For `.sh` files, `_strip_line_comment` drops everything from the first
UNQUOTED `#` onward, tracking single/double-quote state in one pass -- NOT `shlex`: this repo's own
established guard style (see tests/test_import_boundary.py's module docstring) favours a small,
auditable, purpose-built function over a stdlib module whose exact bash-quoting fidelity would need
separate verification. In SHELL files specifically (`shell=True`), a `#` starts a comment only at
the start of a line or when preceded by whitespace -- code-review finding: the earlier "any
unquoted #" rule silently swallowed `kill -STOP` on a line like `[ $# -gt 0 ] && kill -STOP $pid` or
`${#pids[@]}`, both real shapes already used elsewhere in this repo's own `.sh` files
(`skills/agrim-align/scripts/alignment-collect.sh`, `skills/agrim-audit/scripts/audit-collect.sh`),
and exactly the one-liner shape an ad hoc script takes.
`test_shell_comment_after_dollar_hash_is_not_swallowed` and
`test_shell_comment_after_brace_hash_is_not_swallowed` below pin both real shapes as still FLAGGED;
`test_ignores_a_shell_trailing_comment_with_leading_space` pins that a genuine trailing comment
(space before `#`) is still exempt. KNOWN, ACCEPTED GAP: does not handle backslash-escaped quotes,
heredocs, or a `#` immediately after a non-whitespace shell metacharacter that isn't `$`/`{`
(e.g. `cmd;#comment` is still treated as code, not comment) -- `_PRAGMA` (below) exists for any
resulting false positive; none has ever been needed. Everything in this paragraph, and
`_strip_line_comment` itself, is UNCHANGED by issue #1685's tokenize rewrite: `.sh` has no tokenize
equivalent, and this word-boundary fix already worked correctly (`python3 -m pytest -q
tests/test_no_unsupervised_process_pause.py -k shell` stays green throughout).

PYTHON COMMENTS are no longer identified with `_strip_line_comment` at all -- that function is now
used for `.py` only inside the tokenize-FAILURE fallback below, never on the success path. A real
`tokenize` pass already produces a COMMENT token spanning exactly the true comment text, from the
actual unquoted `#` to end of physical line per the real Python grammar, which
`_python_scannable_lines` blanks out the same way it blanks a triple-quoted STRING token. This is
strictly more correct than a hand-written quote-tracking scan, because it shares the same
tokenization pass that already resolves string boundaries -- there is no separate "is this `#`
inside a string" question left to get wrong.

FAIL-SAFE ON A MALFORMED `.py` FILE. `tokenize.generate_tokens` can fail outright on a file with a
genuine lexical error: an unterminated triple-quoted string or an unbalanced bracket at EOF raises
`tokenize.TokenError`; inconsistent indentation raises `IndentationError` (a `SyntaxError`
subclass) -- both verified directly against CPython's own tokenizer, not assumed.
`_python_scannable_lines` catches exactly this pair and falls back to treating the WHOLE file as ordinary
scannable code: every line with only its `#`-comment stripped (`_strip_line_comment(line,
shell=False)`), and NO triple-quote exemption applied anywhere in that file at all. This can only
ever produce a FALSE POSITIVE (scanning text that would have been inside a real docstring, had the
file been valid Python) and never a false negative: silently treating an unparseable file as zero
violations, or skipping it from the scan entirely, would be a brand-new way to hide a real
violation -- exactly the class of bug this rewrite exists to close.
`test_a_file_that_fails_to_tokenize_is_scanned_without_exemptions` below plants a real
`tokenize.TokenError`-inducing file (an unterminated triple-quoted string) alongside a genuine,
plain-code SIGSTOP call and confirms the SIGSTOP is still caught.

THE OPERATOR-TEXT EXEMPTION. `doctor.py`'s own SIGSTOP-detection messages (issue #1685, item 2)
must literally say "...(SIGSTOP?)... kill -CONT %s..." -- that IS the message, and the issue's own
Definition of Done requires this exact wording. That is diagnostic TEXT for a human to read, never
code that signals anything (`doctor.py` only ever shells out to READ-ONLY commands like `ps` via
its own injectable `run`, and never spawns, mutates, or signals a process -- see its own module
docstring). A line legitimately carrying this text opts out with a trailing `_PRAGMA` marker
(`# noqa: process-pause-guard`, this repo's existing `# noqa: <code>` idiom -- see
two private-side modules' own process-stat helpers), checked against the RAW line
before any comment-stripping or docstring-segmenting, so it works identically on a `.py` or `.sh`
line. Code-review finding, replacing an earlier design that allowlisted `doctor.py` WHOLE: that
would have silenced this guard on every one of that file's other 2900+ lines too, proven
executable by the reviewer planting a real `os.kill(pid, signal.SIGSTOP)` call elsewhere in
`doctor.py` and finding the (then whole-file-allowlisted) guard stayed silent. The per-line marker
keeps the guard live everywhere else in that same file --
`test_a_pragma_marked_line_is_exempt_but_only_that_line` below proves the exemption is scoped to
the exact line carrying the marker, not the rest of the file. `_ALLOWLIST` (below) stays reserved
for the issue's own OTHER carve-out -- a future, reviewed, SUPERVISED pause mechanism (the
issue's own `done_when` OR-clause) -- and starts, and currently stays, genuinely empty.

A second, later reviewer finding (Bug A): the `continue` that implements this skip must exempt only
the SCAN of the marked line, never any bookkeeping needed to correctly interpret a DIFFERENT line.
The original implementation got this wrong -- the same `continue` that skipped scanning a
pragma-marked line also skipped updating that line's contribution to the per-line `in_docstring`
boolean, so a line that legitimately closed a real docstring AND happened to carry `_PRAGMA` (a
plausible operator-text shape) left every line after it permanently misread as "still inside a
docstring." Fixed structurally, not by special-casing: `_python_scannable_lines` computes the
entire file's comment/triple-quote exemption map in one pass, before `_violations`'s line loop
(and its `_PRAGMA` check) ever runs, so which lines that loop later decides not to scan cannot
possibly feed back into how any other line was interpreted. See
`test_a_pragma_marked_docstring_boundary_line_does_not_desync_later_scanning` below.

THE DIRECTORY SKIP. Structural: a virtualenv, by content (`_is_virtualenv`, identical contract to
test_import_boundary.py's own -- see that file's docstring for why `env`/`build`/`dist` are NOT
skipped by name). By name, any depth: `__pycache__`, `node_modules`, `.next`, `.git` (none is a
plausible module/script name an author would choose) and `tests` -- this ALSO catches
a private package's `tests/` (not just this file's own directory), for the identical reason: any test
directory in this repo may legitimately plant a literal violation string as fixture content, and
the `_BANNED_PATTERNS` constant itself contains them as plain data, neither a comment nor a
docstring, so without excluding every `tests/` directory this guard could fail on ITS OWN
constant or on a sibling test's fixture (direct precedent: test_import_boundary.py's own module
docstring states outright "this guard itself never walks tests/", for the identical reason).

`.claude` AND `.sdlc` ARE ROOT-ANCHORED EXCLUSIONS, NOT BY-NAME-AT-ANY-DEPTH -- measured, not
guessed (plan Decision 3, corrected by plan-review): this repo's working tree holds 11 separate
Sigma worktree checkouts under `.claude/worktrees/` (5033 `.py` files, 174 `.sh` files
measured there) and `.sdlc/work/` holds 13,350 `.py` files and 423 `.sh` files across 27 other
in-flight goal worktrees -- both roughly 10x this repo's own real tracked counts (585 `.py` / 12
`.sh`). An un-excluded whole-tree walk would scan an order of magnitude more files than the real
tree and could flag another goal's unrelated, uncommitted, mid-flight work in a sibling worktree --
not hypothetical: issue #1461 hit this exact shape for `.claude/worktrees/` in
tests/test_self_contained.py and fixed it with a ROOT-ANCHORED check (`rel.parts[0] == ".claude"`),
not an any-depth name match, specifically so a future tracked `skills/<x>/.claude/` would still be
scanned. This file reuses that exact mechanism for `.claude` AND `.sdlc`
(`test_a_non_root_anchored_dot_sdlc_is_still_scanned` below pins that a NON-root-anchored
`foo/.sdlc/bar.py` is NOT exempted by this rule -- only a top-level `.sdlc/...` is).

NON-VACUOUS BY CONSTRUCTION. `test_owned_source_files_is_non_vacuous` asserts `_owned_source_files`
finds at least one real `.py` and one real `.sh` file in this repo's own tree, so a renamed or
emptied source tree would fail loudly rather than pass with zero real coverage --
test_import_boundary.py's own documented concern, mirrored here.

RUN THE CONTROL (AGENTS.md's own standing rule: "run the control, or the check is decoration").
This detector was proven to fail before it shipped, TWICE: (1) implementation planted a scratch
file directly under this repo's real tree (outside every excluded directory, never `tmp_path`)
containing a bare `kill -STOP` line, ran `python3 -m pytest -q
tests/test_no_unsupervised_process_pause.py`, captured the RED failure verbatim, deleted the
scratch file, and re-ran GREEN; (2) code-review independently re-ran the same control AND went
further, planting a real `os.kill(pid, signal.SIGSTOP)` call inside `doctor.py` itself to test the
THEN-whole-file-allowlisted guard -- proving that version stayed silent (the finding that produced
the per-line `_PRAGMA` mechanism below) -- then re-ran the identical probe once the allowlist was
narrowed to `frozenset()` and confirmed it now fails RED, naming the exact injected file:line, and
GREEN again once removed. All four outputs are pasted in issue #1685's own implementation report
and code-review findings, not merely asserted here.
"""
import io
import os
import pathlib
import re
import tokenize

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The exact substrings issue #1685's incident script used (or could trivially be rewritten to
#: use) to pause a --watch daemon. Checked as plain substrings, never a regex -- see module
#: docstring's "WHAT THIS CATCHES".
_BANNED_PATTERNS = ("SIGSTOP", "SIGCONT", "kill -STOP", "kill -CONT")

#: A line carrying this exact marker anywhere on it (comment or not) is exempt from the scan --
#: the narrow, PER-LINE alternative to a whole-file `_ALLOWLIST` entry. Checked against the RAW
#: line, before any comment-stripping or lookup into the (separately, whole-file) computed `.py`
#: comment/triple-quote exemption map, so it works identically on a `.sh` line too -- and so
#: skipping this one line's scan can never desync that map for any OTHER line (module docstring's
#: "THE OPERATOR-TEXT EXEMPTION", Bug A). See that section for why this exists (code-review
#: finding: a whole-file allowlist entry silences EVERY other line in that file too -- proven
#: exploitable, not just theoretical) and
#: `test_a_pragma_marked_line_is_exempt_but_only_that_line` /
#: `test_a_pragma_marked_docstring_boundary_line_does_not_desync_later_scanning` for the proofs.
#: Mirrors this repo's existing `# noqa: <code>` idiom (two private-side modules' own
#: process-stat helpers) rather than inventing a new convention.
_PRAGMA = "noqa: process-pause-guard"

#: File-granularity, relative-path-string allowlist, for a DIFFERENT carve-out than `_PRAGMA`
#: above: a future, reviewed, SUPERVISED pause mechanism (the issue's own `done_when` OR-clause --
#: "if a pause-based workaround is ever justified, one with a supervisor..."), which would
#: legitimately need to reference these patterns throughout an entire file, not one line. Adding an
#: entry here should be as deliberate and rare as adding one to test_import_boundary.py's own
#: `_BANNED_PRIVATE`/`_BANNED_PLUGIN` ban-lists (inverted sense: growing THOSE strengthens the
#: guard, growing THIS weakens it -- so the bar for a new entry here is higher, not the same).
#: Starts, and as of this issue currently stays, genuinely empty: `doctor.py`'s operator-text need
#: (the only concrete case raised so far) is handled by `_PRAGMA` instead, per-line, without
#: touching this set at all.
_ALLOWLIST = frozenset()

#: Matched against ANY path component (mirrors test_import_boundary.py's `_owned_py_files`): none
#: of these is a plausible module/script name, so a by-name match cannot veto a real one. `tests`
#: is here so this guard's own fixture/constant strings never trip its own real-tree scan (plan
#: Decision 2).
_EXCLUDED_DIR_NAMES = frozenset({"__pycache__", "node_modules", ".next", ".git", "tests"})

#: ROOT-ANCHORED ONLY (checked against rel.parts[0], never any-depth) -- see module docstring's
#: measured file-count argument (plan Decision 3) for why an any-depth match would be wrong here.
_EXCLUDED_ROOT_NAMES = frozenset({".claude", ".sdlc"})


def _is_virtualenv(directory):
    """A directory IS a virtualenv only if it carries BOTH `pyvenv.cfg` and a launcher dir (`bin/`
    POSIX, `Scripts/` Windows) -- identical contract to tests/test_import_boundary.py's own
    `_is_virtualenv`; reimplemented here rather than imported (no shared test-helper module exists
    in this repo -- see that file's own module docstring for why)."""
    return ((directory / "pyvenv.cfg").is_file()
            and ((directory / "bin").is_dir() or (directory / "Scripts").is_dir()))


def _owned_source_files(root):
    """Every `.py`/`.sh` file under `root` this repo owns -- see module docstring's "THE
    DIRECTORY SKIP" for the full exclusion argument: structural (a virtualenv, by content); by
    name at ANY depth (`_EXCLUDED_DIR_NAMES`); root-anchored ONLY (`_EXCLUDED_ROOT_NAMES`)."""
    out = []
    for pattern in ("*.[pP][yY]", "*.[sS][hH]"):
        for path in root.rglob(pattern):
            rel = path.relative_to(root)
            if rel.parts and rel.parts[0] in _EXCLUDED_ROOT_NAMES:
                continue
            if _EXCLUDED_DIR_NAMES & set(rel.parts):
                continue
            if any(part.endswith(".egg-info") for part in rel.parts):
                continue
            if any(_is_virtualenv(root.joinpath(*rel.parts[:i + 1]))
                   for i in range(len(rel.parts) - 1)):
                continue
            out.append(path)
    return sorted(out)


def _strip_line_comment(line, shell=False):
    """`line` with a trailing '#'-comment removed, recognizing it only OUTSIDE any quoted string
    -- a single-pass quote-state machine, not `shlex` (see module docstring's "SHELL/PYTHON
    COMMENTS"). `shell=True` additionally requires the `#` to be at the START of the line or
    preceded by WHITESPACE before it counts as a comment -- code-review finding: POSIX only starts
    a comment at a `#` that begins a "word"; without this, `[ $# -gt 0 ]` or `${#pids[@]}` (real
    shapes already used in this repo's own `.sh` files) silently swallowed everything after the
    `#`, hiding a banned pattern later on the same line. Python has no such rule (any unquoted `#`
    is a comment), so `shell=False` (the default) keeps the simpler behavior. KNOWN, ACCEPTED GAP:
    does not handle backslash-escaped quotes, heredocs, or a `#` immediately after a non-whitespace
    shell metacharacter that isn't `$`/`{` (e.g. `cmd;#comment`) -- `_PRAGMA` exists for any
    resulting false positive; none has ever been needed."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == "#" and not (shell and i and not line[i - 1].isspace()):
            return line[:i]
    return line


#: Prefix letters that can precede a Python string literal's quote characters (`r`, `b`, `u`, `f`,
#: and case-insensitive combinations like `rb`/`Rb`/`fr`). Stripped off before checking whether what
#: remains opens with a triple-quote -- see `_is_triple_quoted`.
_STRING_PREFIX_RE = re.compile(r"^[A-Za-z]*")


def _is_triple_quoted(token_text):
    """Whether a `tokenize` STRING token's own source text (prefix + quotes + body, exactly as
    written in the file) is an EXEMPTABLE triple-quoted string: opens with a triple quote
    (`\"\"\"` or `'''`) AND is not an f-string. `tokenize`'s STRING type covers both regular and
    triple-quoted strings, so this is how `_python_scannable_lines` tells a real, exemptable
    triple-quoted string from an ordinary '...'/"..." string literal, which must stay fully
    scannable (module docstring's "WHY NOT exempt every string literal"; pinned by
    `test_flags_kill_dash_STOP_inside_a_single_line_python_string`).

    F-STRINGS ARE NEVER EXEMPT, ON PURPOSE, REGARDLESS OF QUOTING (reviewer-found, round 2): an
    f-string can never be a real Python docstring in the first place -- only a bare string literal
    ever becomes a function/class/module's `__doc__` (PEP 257; an f-string is an expression, not a
    compile-time constant, so the interpreter never even considers it). Before Python 3.12
    (PEP 701), `tokenize` represents a whole f-string -- prefix, braces, interpolations and all --
    as ONE opaque STRING token indistinguishable from a real triple-quoted docstring by quote-style
    alone, so checking the PREFIX here (not just the quote) is required to catch it; on 3.12+,
    `tokenize` never routes an f-string through a STRING token at all (it emits
    `FSTRING_START`/`FSTRING_MIDDLE`/`FSTRING_END` instead), so this function is simply never asked
    about one there -- but without the prefix check here, versions before and after 3.12 would give
    DIFFERENT answers for the identical source line (measured: `f\"\"\"kill -STOP {pid}\"\"\"` was
    silently exempted on 3.9-3.11, correctly flagged on 3.12+, for the same bytes on disk). Checking
    the prefix makes every supported Python version agree. Verified this doesn't only fix f-strings:
    it does NOT newly exempt anything that was previously flagged -- an f-string was never meant to
    be exempt in the first place, on any version."""
    prefix_end = _STRING_PREFIX_RE.match(token_text).end()
    prefix, body = token_text[:prefix_end], token_text[prefix_end:]
    if "f" in prefix.lower():
        return False
    return body.startswith('"""') or body.startswith("'''")


def _blank_span(line_chars, start, end):
    """Overwrite `line_chars` (a list of per-physical-line, mutable, 0-indexed character lists --
    one per line of the file) with spaces across the half-open token span `[start, end)`
    (`tokenize`'s own `(row, col)` pairs: 1-indexed row, 0-indexed column). Spaces, never deletion,
    so column positions and line counts can never shift and two separated code fragments can never
    be accidentally concatenated into a new false match. A single-line span blanks only that column
    range on its one row; a multi-line span (a real multi-line triple-quoted string) blanks from the
    start column to end-of-line on the first row, every column on any row strictly between, and
    column 0 up to the end column on the last row."""
    (start_row, start_col), (end_row, end_col) = start, end
    if start_row == end_row:
        row = start_row - 1
        if 0 <= row < len(line_chars):
            width = len(line_chars[row])
            for col in range(start_col, min(end_col, width)):
                line_chars[row][col] = " "
        return
    row = start_row - 1
    if 0 <= row < len(line_chars):
        for col in range(start_col, len(line_chars[row])):
            line_chars[row][col] = " "
    for row in range(start_row, end_row - 1):
        if 0 <= row < len(line_chars):
            for col in range(len(line_chars[row])):
                line_chars[row][col] = " "
    row = end_row - 1
    if 0 <= row < len(line_chars):
        width = len(line_chars[row])
        for col in range(0, min(end_col, width)):
            line_chars[row][col] = " "


def _python_scannable_lines(text):
    """The CODE-only view of a `.py` file's own lines, one string per physical line (same indexing
    as `text.split("\\n")` -- NOT `text.splitlines()`, see "ROW-INDEXING" below): every character
    belonging to a real COMMENT token or a real TRIPLE-QUOTED, NON-F-STRING STRING token -- per the
    stdlib `tokenize` module's actual parse of the file -- is replaced with a space. An ordinary
    (non-triple-quoted) STRING token, and any f-string regardless of quoting, is left completely
    untouched. See module docstring's "THE TRIPLE-QUOTE BOUNDARY" and "SHELL/PYTHON COMMENTS".

    This is a ONE-SHOT, whole-file computation done entirely up front, with no notion of `_PRAGMA`
    at all -- `_violations` below never feeds it a per-line running state. That is deliberate: it
    closes the reviewer-found "Bug A" class of defect structurally (module docstring's "THE
    OPERATOR-TEXT EXEMPTION"). Which lines `_violations`'s own loop later chooses not to scan
    (because they carry `_PRAGMA`) cannot change what this function already computed, because this
    function runs to completion first.

    ROW-INDEXING (reviewer-found, round 2): `tokenize.generate_tokens(io.StringIO(text).readline)`
    walks lines the way `io.StringIO`'s `readline` sees them -- split on a bare `\\n` ONLY, because
    `io.StringIO`'s own `newline` parameter defaults to `'\\n'` (universal-newline translation OFF),
    unlike `open()`'s `newline=None` default. `str.splitlines()` recognizes a much larger set
    (`\\v`, `\\f`, `\\x1c`-`\\x1e`, `\\x85`, U+2028, U+2029, plus `\\r`/`\\r\\n`) as line breaks. Any
    of those characters inside a file makes `len(text.splitlines())` exceed the tokenizer's own row
    count, so every `(row, col)` `tokenize` hands `_blank_span` indexes the WRONG element of a
    `splitlines()`-built array once that point is passed -- silently blanking real code on an
    EARLIER line instead of the intended one. `text.split("\\n")` matches `readline`'s own view
    exactly (verified against CPython's tokenizer, not assumed), so both this function's `lines`
    and `_violations`'s own `raw_lines` MUST use `split("\\n")`, never `splitlines()` -- moving one
    without the other reintroduces the misalignment. Proven live, not just theoretical:
    `skills/agrim-loop/scripts/feature_doc.py:242` already carries a literal U+2028 character (inside
    a comment that, unrelated to this issue, itself warns about this exact character class);
    `test_a_line_separator_character_does_not_desync_row_indexing` below pins the fix with a
    minimal repro of the same shape.

    FAIL-SAFE (module docstring's "FAIL-SAFE ON A MALFORMED .py FILE"): if the file cannot be
    tokenized at all (`tokenize.TokenError`, or `SyntaxError`/`IndentationError`), every line is
    returned with only its `#`-comment stripped (`_strip_line_comment(line, shell=False)`) and NO
    triple-quote exemption applied anywhere in the file -- this can only ever cause a false
    positive, never a false negative."""
    lines = text.split("\n")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return [_strip_line_comment(line, shell=False) for line in lines]
    line_chars = [list(line) for line in lines]
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            _blank_span(line_chars, tok.start, tok.end)
        elif tok.type == tokenize.STRING and _is_triple_quoted(tok.string):
            _blank_span(line_chars, tok.start, tok.end)
    return ["".join(chars) for chars in line_chars]


def _violations(root, allowlist=frozenset()):
    """The guard, as a pure function tested against planted fixtures (module docstring) and the
    real tree. Returns one sorted "relpath:lineno: contains 'PATTERN'" entry per hit. A line
    carrying `_PRAGMA` anywhere on it is skipped whole first (module docstring's "THE
    OPERATOR-TEXT EXEMPTION") -- this ONLY skips scanning that one line; it can never desync the
    interpretation of any other line, because for `.py` files the comment/triple-quote exemption
    map (`_python_scannable_lines`) is computed for the WHOLE file in one pass, before this loop
    ever runs, from `tokenize`'s own real parse -- not from per-line state threaded through this
    loop (module docstring's "THE TRIPLE-QUOTE BOUNDARY"). `.sh` files have no docstring concept
    and no tokenize equivalent; their lines are scanned whole, with SHELL-aware comment-stripping
    (`shell=True` -- module docstring's "SHELL/PYTHON COMMENTS"), so a `#` inside `$#`/`${#...}`
    isn't mistaken for a comment start. A file whose relative path is in `allowlist` is skipped
    entirely (a DIFFERENT, file-granularity carve-out than `_PRAGMA` -- see that constant's own
    comment). `raw_lines` is split on a bare `\\n` (`text.split("\\n")`), NEVER `text.splitlines()`
    -- for `.py` files this MUST match `_python_scannable_lines`'s own row indexing exactly (see
    that function's own "ROW-INDEXING" docstring section for the reviewer-found bug this avoids);
    for `.sh` it is also the more correct choice on its own terms, since none of `splitlines()`'s
    extra recognized separators (`\\v`, `\\f`, U+2028, ...) are line breaks to a real shell
    interpreter either."""
    violations = []
    for path in _owned_source_files(root):
        rel_str = str(path.relative_to(root))
        if rel_str in allowlist:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        is_python = path.suffix.lower() == ".py"
        raw_lines = text.split("\n")
        scannable_lines = _python_scannable_lines(text) if is_python else None
        for lineno, raw_line in enumerate(raw_lines, start=1):
            # issue #1685 code-review: the narrow, per-LINE opt-out (module docstring's "THE
            # OPERATOR-TEXT EXEMPTION") -- checked on the RAW line, before any comment/docstring
            # handling, so it works the same on a `.py` or `.sh` line. `scannable_lines` (for `.py`)
            # was already fully computed above for the WHOLE file, so skipping the scan here can
            # never desync it for any other line (see `_python_scannable_lines`'s own docstring).
            if _PRAGMA in raw_line:
                continue
            if is_python:
                scannable = scannable_lines[lineno - 1]
            else:
                scannable = _strip_line_comment(raw_line, shell=True)
            hit = None
            for pattern in _BANNED_PATTERNS:
                if pattern in scannable:
                    hit = pattern
                    break
            if hit:
                violations.append(f"{rel_str}:{lineno}: contains '{hit}'")
    return sorted(violations)


# --------------------------------------------------------------------------- exclusion hygiene, on fixtures


def test_a_real_virtualenv_is_skipped(tmp_path):
    """The legitimate exclusion, identified by what the directory CONTAINS -- not by being named
    `env`, which a real module could also be called."""
    venv = tmp_path / "env"
    (venv / "lib").mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (venv / "lib" / "vendored.sh").write_text("kill -STOP $pid\n", encoding="utf-8")
    (tmp_path / "mine.sh").write_text("kill -STOP $pid\n", encoding="utf-8")
    assert _violations(tmp_path) == ["mine.sh:1: contains 'kill -STOP'"]


def test_a_lone_pyvenv_cfg_cannot_hide_a_module(tmp_path):
    """A one-file veto: dropping pyvenv.cfg into a real module must NOT silence its sources --
    only pyvenv.cfg PAIRED WITH a launcher dir counts as a virtualenv."""
    mod = tmp_path / "realmod"
    mod.mkdir()
    (mod / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (mod / "leak.sh").write_text("kill -STOP $pid\n", encoding="utf-8")
    assert _violations(tmp_path) == [
        os.path.join("realmod", "leak.sh") + ":1: contains 'kill -STOP'"
    ]


def test_by_name_excluded_dirs_are_skipped_at_any_depth(tmp_path):
    """None of these is a plausible module/script name an author would choose -- see module
    docstring's "THE DIRECTORY SKIP"."""
    for rel in ("__pycache__/leak.py", "node_modules/leak.sh", ".next/leak.py", ".git/leak.sh",
                "pkg/node_modules/deep/leak.sh"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("kill -STOP $pid\n", encoding="utf-8")
    assert _violations(tmp_path) == []


def test_tests_directory_is_skipped_at_any_depth(tmp_path):
    """This guard's own fixture/constant strings must never trip its own real-tree scan -- plan
    Decision 2, direct precedent in test_import_boundary.py's own module docstring ("this guard
    itself never walks tests/")."""
    p = tmp_path / "tests" / "leak.py"
    p.parent.mkdir(parents=True)
    p.write_text("kill -STOP $pid\n", encoding="utf-8")
    assert _violations(tmp_path) == []


def test_root_level_dot_claude_and_dot_sdlc_are_skipped(tmp_path):
    for rel in (".claude/worktrees/other-goal/script.py", ".sdlc/work/999/script.sh"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("kill -STOP $pid\n", encoding="utf-8")
    assert _violations(tmp_path) == []


def test_a_non_root_anchored_dot_sdlc_is_still_scanned(tmp_path):
    """Plan-review MUST-FIX: proves the exclusion is ROOT-ANCHORED, not an any-depth name match --
    a future tracked `skills/<x>/.sdlc/` (or any nested path merely containing a `.sdlc` or
    `.claude` path component below the top level) must still be scanned. Mirrors
    test_self_contained.py's own root-anchoring pin for the identical mechanism."""
    for rel in ("foo/.sdlc/bar.py", "pkg/.claude/baz.sh"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("kill -STOP $pid\n", encoding="utf-8")
    violations = _violations(tmp_path)
    assert len(violations) == 2, (
        f"a non-root-anchored .sdlc/.claude path must still be scanned; got {violations}"
    )


# --------------------------------------------------------------------------- comments and docstrings, on fixtures


def test_ignores_a_python_comment_mention(tmp_path):
    (tmp_path / "clean.py").write_text(
        "# kill -STOP $pid -- intentionally commented out, do not do this\n"
        "x = 1\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == []


def test_ignores_a_shell_comment_mention(tmp_path):
    (tmp_path / "clean.sh").write_text(
        "#!/bin/sh\n"
        "# kill -STOP $pid -- intentionally commented out, do not do this\n"
        "echo ok\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == []


def test_ignores_a_shell_trailing_comment_with_leading_space(tmp_path):
    """A genuine trailing comment (whitespace before `#`) must still be exempt -- proves the
    shell word-boundary fix below doesn't turn every `#` into code."""
    (tmp_path / "clean.sh").write_text(
        "#!/bin/sh\necho ok   # kill -STOP mentioned only in a trailing comment\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == []


def test_shell_comment_after_dollar_hash_is_not_swallowed(tmp_path):
    """Code-review MUST-FIX: `_strip_line_comment`'s old "any unquoted #" rule treated `$#`'s `#`
    as a comment start, silently swallowing everything after it -- including a real `kill -STOP`
    on the same line. `$#` (positional-arg count) is a real shape already used elsewhere in this
    repo's own `.sh` files, and is exactly the one-liner an ad hoc script takes
    (`if [ $# -gt 0 ]; then kill -STOP $1; fi`)."""
    (tmp_path / "leak.sh").write_text(
        "#!/bin/sh\n[ $# -gt 0 ] && kill -STOP $pid\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.sh:2: contains 'kill -STOP'"]


def test_shell_comment_after_brace_hash_is_not_swallowed(tmp_path):
    """The `${#arr[@]}` (array-length) mirror image of the `$#` case above -- also a real shape
    already used in this repo's own `.sh` files."""
    (tmp_path / "leak.sh").write_text(
        "#!/bin/sh\nif [ ${#pids[@]} -gt 0 ]; then kill -STOP ${pids[0]}; fi\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.sh:2: contains 'kill -STOP'"]


def test_ignores_a_docstring_mention(tmp_path):
    (tmp_path / "clean.py").write_text(
        '"""This module discusses SIGSTOP and kill -STOP, in prose only, across several\n'
        "lines, exactly the shape a real module docstring takes.\n"
        '"""\n'
        "x = 1\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == []


def test_flags_a_banned_pattern_before_an_opening_triple_quote_on_the_same_line(tmp_path):
    """Plan-review MUST-FIX #1: the naive toggle (flip docstring-state whenever a line contains an
    odd number of triple-quotes) exempts the WHOLE line the instant it contains an opening
    triple-quote, which would wrongly hide a banned pattern appearing BEFORE it on that same line.
    Both the flagged line and the exempt docstring-body line are asserted, so a regression that
    stops splitting at the boundary (exempting the whole line, or scanning the whole thing) fails
    one assertion or the other."""
    (tmp_path / "leak.py").write_text(
        'kill -STOP $pid  """a docstring that keeps going\n'
        "more docstring text mentioning kill -STOP again, but this part is exempt\n"
        '"""\n',
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.py:1: contains 'kill -STOP'"]


def test_a_comment_containing_a_triple_quote_does_not_corrupt_later_scanning(tmp_path):
    """Reviewer-reported Bug B, reproduced directly: `raw_line.split('\"\"\"')` runs on the RAW
    line, before any comment-awareness, so a `\"\"\"` appearing inside an ordinary `#` comment is
    indistinguishable from a real docstring delimiter and wrongly toggles `in_docstring`, corrupting
    state for the rest of the file even with NO pragma involved at all. Both real SIGSTOP/SIGCONT
    calls here are ordinary code, not inside any real docstring, and must both be flagged."""
    (tmp_path / "leak.py").write_text(
        "os.kill(pid, signal.SIGSTOP)\n"
        '# the docstring delimiter is spelled """\n'
        "os.kill(pid, signal.SIGCONT)\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == [
        "leak.py:1: contains 'SIGSTOP'",
        "leak.py:3: contains 'SIGCONT'",
    ]


def test_a_string_literal_containing_triple_quote_as_data_does_not_corrupt_later_scanning(tmp_path):
    """A plain (single-quoted) string literal whose CONTENT happens to be three double-quote
    characters (`'\"\"\"'`) must not be mistaken for a real triple-quote docstring boundary either
    -- same failure shape as the comment case above, different source of the stray `\"\"\"`."""
    (tmp_path / "leak.py").write_text(
        "DELIM = '\"\"\"'\n"
        "os.kill(pid, signal.SIGSTOP)\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.py:2: contains 'SIGSTOP'"]


def test_flags_a_banned_pattern_after_a_closing_triple_quote_on_the_same_line(tmp_path):
    """Plan-review MUST-FIX #2: the mirror-image boundary -- a banned pattern appearing AFTER a
    closing triple-quote that ends a multi-line docstring on that same line must still be
    scanned."""
    (tmp_path / "leak.py").write_text(
        '"""module docstring\n'
        "still going, mentions kill -STOP here too but this is exempt\n"
        'ends here""" kill -STOP $pid\n',
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.py:3: contains 'kill -STOP'"]


def test_a_line_separator_character_does_not_desync_row_indexing(tmp_path):
    """Post-PR review, round 2, MUST-FIX: `str.splitlines()` recognizes far more characters as line
    breaks than `io.StringIO(text, newline="\\n").readline()` does (the callable `tokenize` walks
    here) -- U+2028 (LINE SEPARATOR) is one of them. Before this fix, using `splitlines()` to build
    the per-line character array `_blank_span` mutates made every `(row, col)` `tokenize` reports
    AFTER a U+2028 land on the WRONG row (an off-by-one that grows with every such character),
    silently blanking real code on an earlier, unrelated line instead of the intended docstring
    span -- proven live against `skills/agrim-loop/scripts/feature_doc.py:242`, which already
    contains one. This fixture reproduces the same shape minimally: a real docstring boundary AFTER
    a U+2028, with a genuine violation on the line right after the docstring closes -- that
    violation must still be caught, not silently swallowed by a misaligned blank span landing on it
    instead of on the docstring."""
    (tmp_path / "leak.py").write_text(
        "x = 1  # a line separator sits right here: \u2028 (not a real newline to `split(\"\\n\")`)\n"
        '"""a real docstring\n'
        'closed here"""\n'
        "os.kill(pid, signal.SIGSTOP)\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.py:4: contains 'SIGSTOP'"]


def test_flags_a_triple_quoted_f_string_command_on_every_python_version(tmp_path):
    """Post-PR review, round 2, SHOULD-FIX: an f-string can never be a real Python docstring (only
    a bare string literal ever becomes `__doc__`), so it must never be exempted -- regardless of
    triple-quote styling, and regardless of which Python version's `tokenize` module is running.
    Before this fix, `f\"\"\"kill -STOP {pid}\"\"\"` was silently exempted on Python < 3.12 (a
    whole f-string is one opaque STRING token there, indistinguishable from a real docstring by
    quote-style alone) while correctly flagged on 3.12+ (PEP 701 routes an f-string through
    FSTRING_START/MIDDLE/END instead, which this guard never exempts) -- the SAME source bytes
    giving DIFFERENT verdicts depending only on which interpreter ran the guard. This pins the
    version-independent, always-flagged answer."""
    (tmp_path / "leak.py").write_text(
        'cmd = f"""kill -STOP {pid}"""\n',
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.py:1: contains 'kill -STOP'"]


def test_a_non_f_string_triple_quoted_command_is_an_accepted_gap(tmp_path):
    """Post-PR review, round 2: pins the module docstring's "NAMED, ACCEPTED GAP" for a
    triple-quoted, NON-f-string command string -- it IS exempted today, the same as a real
    docstring, because narrowing the exemption to true docstring POSITION would need real
    statement-level analysis for a shape with no evidence of occurring in this repo's own
    real-tree (see that docstring section for the measured grep). This test exists so a future
    change to this behavior is a deliberate decision this test forces you to update, not an
    unnoticed drift."""
    (tmp_path / "leak.py").write_text(
        "cmd = '''kill -STOP ''' + pid\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == []


# --------------------------------------------------------------------------- flagging, on fixtures


def test_flags_a_plain_shell_line(tmp_path):
    (tmp_path / "leak.sh").write_text("#!/bin/sh\nkill -STOP $pid\n", encoding="utf-8")
    assert _violations(tmp_path) == ["leak.sh:2: contains 'kill -STOP'"]


def test_flags_SIGCONT_as_a_python_attribute(tmp_path):
    (tmp_path / "leak.py").write_text(
        "import os, signal\nos.kill(pid, signal.SIGCONT)\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.py:2: contains 'SIGCONT'"]


def test_flags_kill_dash_STOP_inside_a_single_line_python_string(tmp_path):
    """The exact incident shape restated in Python: ONE contiguous shell-style string handed to a
    shell, not an argv-split list. Proves the guard was not implemented as 'exempt every string
    literal', which would make it decorative against the real incident script's own shape (see
    module docstring's section on why every string literal is not exempted)."""
    (tmp_path / "leak.py").write_text(
        'subprocess.run("kill -STOP " + str(pid), shell=True)\n',
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.py:1: contains 'kill -STOP'"]


@pytest.mark.parametrize("pattern", ["SIGSTOP", "SIGCONT", "kill -STOP", "kill -CONT"])
def test_flags_each_banned_pattern_independently(tmp_path, pattern):
    (tmp_path / "leak.sh").write_text(f"{pattern} $pid\n", encoding="utf-8")
    assert _violations(tmp_path) == [f"leak.sh:1: contains '{pattern}'"]


# --------------------------------------------------------------------------- pragma, on fixtures


def test_a_pragma_marked_line_is_exempt_but_only_that_line(tmp_path):
    """Code-review finding: the whole-file `_ALLOWLIST` this replaced was proven exploitable (a
    real `os.kill(pid, signal.SIGSTOP)` planted elsewhere in an allowlisted file went undetected).
    The per-line `_PRAGMA` marker must exempt ONLY the line carrying it -- an unmarked banned
    pattern two lines later in the SAME file must still be flagged, proving this isn't a
    reintroduced whole-file escape hatch by another name."""
    (tmp_path / "leak.sh").write_text(
        "#!/bin/sh\n"
        "kill -STOP $pid  # noqa: process-pause-guard\n"
        "echo unrelated\n"
        "kill -CONT $pid\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.sh:4: contains 'kill -CONT'"]


def test_a_pragma_marked_line_works_in_python_too(tmp_path):
    """The marker is checked on the RAW line before any docstring/comment handling, so it works
    identically regardless of file type or where on the line it sits."""
    (tmp_path / "leak.py").write_text(
        'os.kill(pid, signal.SIGSTOP)  # noqa: process-pause-guard\n',
        encoding="utf-8",
    )
    assert _violations(tmp_path) == []


def test_a_pragma_marked_docstring_boundary_line_does_not_desync_later_scanning(tmp_path):
    """Reviewer-reported Bug A, reproduced directly: the old per-line `_PRAGMA` opt-out was a bare
    `continue` that ALSO skipped the docstring-toggle bookkeeping for that line, not just the scan.
    Line 3 here both legitimately CLOSES a real triple-quoted docstring AND carries the pragma
    marker (a plausible operator-text shape: a docstring's closing line annotated to opt out of
    something else on it) -- the old code left `in_docstring` permanently desynced (stuck True) for
    every line after it, hiding line 4's real, unmarked SIGSTOP call. A correct implementation must
    still flag line 4: the pragma exempts only line 3's own scan, never the bookkeeping needed to
    correctly interpret line 4."""
    (tmp_path / "leak.py").write_text(
        '"""a docstring\n'
        "that spans multiple lines\n"
        '"""  # noqa: process-pause-guard\n'
        "os.kill(pid, signal.SIGSTOP)\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["leak.py:4: contains 'SIGSTOP'"]


# --------------------------------------------------------------------------- allowlist, on fixtures


def test_an_allowlisted_file_is_skipped_whole(tmp_path):
    (tmp_path / "leak.sh").write_text("kill -STOP $pid\n", encoding="utf-8")
    assert _violations(tmp_path, allowlist=frozenset()) == ["leak.sh:1: contains 'kill -STOP'"]
    assert _violations(tmp_path, allowlist=frozenset({"leak.sh"})) == []


# --------------------------------------------------------------------------- fail-safe on a malformed .py file


def test_a_file_that_fails_to_tokenize_is_scanned_without_exemptions(tmp_path):
    """A `.py` file that cannot be tokenized at all (here: a genuine syntax error -- an
    unterminated triple-quoted string, `tokenize.TokenError: EOF in multi-line string`) must fail
    SAFE: `_violations` falls back to scanning every line whole (with only `#`-comment stripping,
    no triple-quote exemption at all) rather than silently treating the unparseable file as if it
    had zero violations. A tokenize failure must never be a NEW way to hide a real violation --
    line 1's plain, non-docstring SIGSTOP call must still be flagged even though line 2 breaks the
    tokenizer."""
    (tmp_path / "broken.py").write_text(
        "os.kill(pid, signal.SIGSTOP)\n"
        '"""an unterminated docstring that never closes\n',
        encoding="utf-8",
    )
    assert _violations(tmp_path) == ["broken.py:1: contains 'SIGSTOP'"]


# --------------------------------------------------------------------------- the real tree


def test_owned_source_files_is_non_vacuous():
    """test_import_boundary.py's own documented concern, mirrored here: a renamed or emptied
    source tree must fail loudly rather than pass with zero real coverage."""
    files = _owned_source_files(ROOT)
    assert any(p.suffix.lower() == ".py" for p in files), "no .py files found -- tree renamed/emptied?"
    assert any(p.suffix == ".sh" for p in files), "no .sh files found -- tree renamed/emptied?"


def test_the_real_tree_has_no_unsupervised_process_pause_violations():
    violations = _violations(ROOT, allowlist=_ALLOWLIST)
    assert not violations, (
        "no owned .py/.sh file may pause a --watch daemon via SIGSTOP unsupervised "
        "(AGENTS.md's \"No unsupervised SIGSTOP\" rule, issue #1685):\n  " + "\n  ".join(violations)
    )

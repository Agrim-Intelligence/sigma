#!/usr/bin/env python3
"""What UNIT OF WORK does this issue belong to? -- the read half (#1465, epic #1464, story #1427).

`work.base` is one string in config, so today every goal's worktree is cut from the same branch and
nothing connects an issue to the unit it belongs to. The branching model fixes that by letting an
issue DECLARE its unit; this module is the single place that reads such a declaration, so that every
later consumer (base resolution, the registry, stamping) asks the same question of the same parser
and cannot drift from it. Nothing here mutates anything -- deciding what to DO about a verdict is
the attach-at-pick goal, one level up.

AN ISSUE DECLARES ITS UNIT TWICE, ON PURPOSE, and the duplication is the point:

  - the label `feature:<name>` is MACHINE-readable -- every query, sweep and census Sigma runs
    already filters server-side by label, so the unit has to be expressible as one or the pick path
    would need a body fetch per issue just to know where a goal belongs;
  - the two-line BODY MARKER is HUMAN-readable -- it is what someone reading the issue sees, and it
    is the half a person actually writes and edits.

        Feature: voice-interview
        Branch: feature/voice-interview

(That example is indented by eight spaces so that it is a markdown code block, which this parser
deliberately does not read -- see rule 5. `test_the_modules_own_docstring_declares_nothing` pins it:
before that rule existed, feeding this very docstring to this very module returned
`'voice-interview'`.)

Two declarations can disagree, so `read()` reports WHICH of five states holds rather than silently
reconciling them: `agree`, `body_only`, `label_only`, `conflict`, `none`. ON A CONFLICT THE BODY
WINS -- it is what a human wrote, where the label is what a machine attached -- but the verdict
still carries both sides, because a caller acting on a conflict has to be able to say what it chose
between. `none` is the state that keeps today's behaviour: a goal declaring no unit bases on the
configured base, exactly as it does now, which is what makes adopting this break nothing.

THE PARSER'S BRIEF, WHICH IS THE WHOLE DIFFICULTY: strict enough to reject a typo, loose enough to
survive a human editing around it. Those pull in opposite directions, so each rule below is chosen
for which way it FAILS, and every one of them fails towards reading NO declaration:

  1. ANCHORED AT LINE START, AT MOST THREE SPACES OF INDENT, NEVER A TAB. The marker owns its line.
     `see the parent -- Feature: voice-interview` is a sentence, and a blockquote or bullet prefix
     (`> `, `- `) is someone quoting the format, not declaring against it. The three-space ceiling is
     CommonMark's own boundary, and it is what makes rule 5 complete: an INDENTED code block starts
     at four spaces (a leading tab counts as four), so a marker indented that far is inside a code
     block and is an example, exactly like a fenced one. A separate stripping pass for indented
     blocks would be redundant -- every line of such a block is by definition indented four or more,
     so this one bound already makes all of them unmatchable.
     THE TWO INDENT BOUNDS ARE ASYMMETRIC, AND IN THE SAFE DIRECTION: `_FENCE_RE` recognises a fence
     indented up to six columns while a marker stops at three. So a fence indented too far for the
     fence matcher to see sits in a region whose marker lines are unmatchable anyway -- a fence this
     parser fails to recognise can never invent a declaration, only ever fail to hide one it did not
     need to hide.
  2. THE KEY IS CASE-INSENSITIVE, the value is not (see 6). `Feature:`/`feature:`/`FEATURE:` are one
     key; `Featue:`, `Features:` and `feature=` are not that key at all and read as prose. Both keys
     are built into their regexes from `BODY_KEY`/`BRANCH_KEY` via `re.escape`, so the constants the
     stamping half writes against genuinely cannot drift from what this half reads -- a constant
     that merely sat beside a hardcoded literal would be worse than none, because L3 would trust it.
  3. ONE VALUE PER KEY. The value is a single token, and this clause is what keeps the parser off
     ordinary prose: `Feature: we should add voice interviews` is a paragraph opening, not a
     declaration, and honouring it would base a goal on a branch nobody named.
  4. THE VALUE MUST BE A NAME GIT WILL ACCEPT AS A BRANCH SEGMENT (`_UNIT_RE`), because the same name
     becomes both a label suffix (`feature:<name>`) and a branch segment (`feature/<name>`), and L1
     will try to cut a worktree from it. MEASURED against `git check-ref-format`, not assumed: a
     segment may not end in `.` (`TBD.`, `x.`), may not contain `..` (`v1..2`), and may not end in
     `.lock` (`voice.lock`) -- while `voice.LOCK` and `a.lockfile` ARE valid, so that last rule is
     case-sensitive and must not be written with `re.IGNORECASE`. `Feature: TBD.` at a line start is
     ordinary hand-written issue prose, and before this rule existed it declared a unit git would
     have refused. A `/` is excluded for a different reason -- ambiguity, not safety: `feature/a/b`
     is the model's own sub-branch shape, so a declared `a/b` cannot be told apart from a unit that
     owns a sub-branch, and the read half must not guess which was meant.
  5. SURROUNDING PROSE IS IGNORED, but ANYTHING A READER OF THE ISSUE CANNOT SEE AS TEXT IS NOT
     PARSED AT ALL -- fenced code blocks, indented code blocks (rule 1) and HTML comments. This is
     measured, not hypothesised: the issues specifying this very module (#1465, #1464) both DISPLAY
     the marker inside a fence while declaring no unit whatsoever, so does this module's own
     docstring, and so will the adopter doc that teaches the format. Parsing hidden content would
     make each of them declare `voice-interview` and demand a branch that does not exist. An HTML
     comment is the sharpest case of all: GitHub issue TEMPLATES ship their instructions in one, and
     a declaration no human can see contradicts the entire reason the body half exists.
     `goal_size.py` already strips fences before every structural read, and this is a faithful clone
     of its delimiter discipline plus the one rule it discloses that it does not model -- see
     `_visible`.
     THE INVERSE RISK IS REAL AND IS HANDLED SEPARATELY: a human who fences a marker they MEANT
     would otherwise get silence indistinguishable from having declared nothing, so `parse_body`
     emits one stderr note in exactly that case. Silence is still the return value; the note is what
     makes it discoverable.
  6. TWO CONFLICTING `Feature:` LINES RAISE. First-wins would be an unreviewable choice between two
     things a human actually wrote. Two lines saying the SAME thing are a human repeating themselves
     and resolve quietly -- as do two spellings that differ only in case, because GitHub label names
     are case-insensitively unique, so `feature:Voice` and `feature:voice` cannot both exist on a
     repo and treating them as rivals would manufacture a conflict the label side is structurally
     incapable of expressing.
  7. THE `Branch:` LINE IS READ TOO, AND ONLY EVER AS A CROSS-CHECK. The marker is specified as two
     lines, so reading one of them is half the spec: a body saying `Feature: voice-interview` under
     `Branch: feature/billing` used to parse silently, leaving the HUMAN-readable half -- the whole
     justification for the body marker existing -- telling the next human something false. The
     branch is DERIVED from the unit (`feature/<name>`), never a second source of truth, so a
     `Branch:` line that contradicts the `Feature:` line raises exactly as rival `Feature:` lines do,
     and a `Branch:` line with no `Feature:` line above it declares nothing at all. A branch naming a
     SUB-branch of the declared unit (`feature/<name>/<sub>`, the model's own shape for a dependency
     discovered mid-flight) agrees rather than conflicting -- and SEVERAL AGREEING `Branch:` LINES
     ARE NOT RIVALS. Only a line that actually DISAGREES raises: a cross-check obliged to be
     singular would be precisely the second source of truth this rule denies it is.
     THE KEY NAMES THE UNIT'S BRANCH, NEVER THE GOAL'S OWN. Worth saying out loud because the key is
     genuinely ambiguous otherwise: `sdlc/<goal-id>` is what Sigma cuts for every goal today, so
     a writer could reasonably read `Branch:` as "the branch this goal is on". It is not -- it is the
     branch of the UNIT, and `Branch: sdlc/1465` therefore raises, deliberately. Whoever writes the
     stamping half or the adopter doc needs that sentence.

The same "never silently pick between two declarations" rule is applied to the label side, which the
design does not mention because Sigma only ever attaches one: a human CAN add a second by hand,
and two distinct `feature:` labels is not a state the five verdicts can express, so it raises rather
than resolving to whichever the API happened to list first. `read`'s own docstring states the
obligation that raise puts on every caller.

BOTH LABEL PAYLOAD SHAPES ARE READ, because this repo carries both today: gh's raw issue JSON gives
`[{"name": ...}]` (what `auto_unpark._label_names` reads), while the board queue normalises to bare
strings (what `sources._board_queue` reads). A reader that handled one shape would be silently blind
on the other's call sites -- returning `none` for an issue that plainly carries a label, which is the
failure mode that looks like "the feature just doesn't work sometimes".

Module shape follows `owners.py` and `goal_size.py`: zero dependencies, pure functions (the one
stderr note excepted, and it is fail-open), module-level constants, loaded by siblings via
`_load("features")`.
"""
import collections
import re
import sys

#: The machine-readable half. A label is `feature:<name>`; the prefix match is case-insensitive
#: because GitHub label names are case-insensitively unique, so a repo cannot hold two spellings.
LABEL_PREFIX = "feature:"

#: The human-readable half's two keys. These are not decorative: `_MARKER_RE` and `_BRANCH_RE` are
#: BUILT from them (`re.escape`), so what the stamping half writes and what this half reads are one
#: definition, not two that can drift.
BODY_KEY = "Feature"
BRANCH_KEY = "Branch"

#: Every branch for a unit lives under this prefix -- including a shared bug, which is why the model
#: is not "features only". The `Branch:` line is checked against it; nothing here builds a branch.
BRANCH_PREFIX = "feature/"

# --- the five verdicts ---------------------------------------------------------------------------
AGREE = "agree"              # both halves declare the same unit
BODY_ONLY = "body_only"      # the body declares a unit, the label is absent
LABEL_ONLY = "label_only"    # the label declares a unit, the body has no marker
CONFLICT = "conflict"        # both declare, and they disagree -- the body wins, both are reported
NONE = "none"                # neither declares; the goal bases on the configured base, as today

#: The closed set. A caller matching on `state` is complete iff it covers exactly these.
VERDICTS = (AGREE, BODY_ONLY, LABEL_ONLY, CONFLICT, NONE)

#: A legal unit name: what git accepts as ONE branch segment, verified against `git check-ref-format`
#: (see rule 4). Deliberately NOT `re.IGNORECASE` -- git rejects a `.lock` suffix but accepts
#: `.LOCK`, so a case-insensitive `.lock` rule would reject a name git allows.
_UNIT_RE = re.compile(r"""
    (?!.*\.\.)              # git: no component may contain '..'
    [A-Za-z0-9]             # git: a component may not begin with '.'; we require alphanumeric
    [A-Za-z0-9._-]*         # label-safe, path-safe; no '/' -- see rule 4 for why that is ambiguity
    (?<!\.)                 # git: a component may not end with '.'
    (?<!\.lock)             # git: a component may not end with '.lock' -- case-SENSITIVE, measured
    \Z
""", re.VERBOSE)


def _key_line_re(key, indent):
    """A `<Key>: <one-token>` line matcher, built from the key CONSTANT rather than a literal.

    `indent` is the leading-whitespace class: `" {0,3}"` for the real parsers (rule 1 -- four spaces
    is an indented code block) and a permissive one for the hidden-marker check, which has to see
    the very lines the real parsers refuse."""
    return re.compile(r"^%s%s[ \t]*:[ \t]*(\S+)[ \t]*$" % (indent, re.escape(key)),
                      re.IGNORECASE | re.MULTILINE)


#: The two real parsers. `(\S+)` plus the end-anchor together ARE the "one value per key" rule; the
#: trailing `[ \t]*` tolerates the trailing whitespace a hand-edited markdown line so often carries.
_MARKER_RE = _key_line_re(BODY_KEY, " {0,3}")
_BRANCH_RE = _key_line_re(BRANCH_KEY, " {0,3}")

#: Same shape, any indentation, used ONLY to notice a declaration that some hiding construct ate.
_MARKER_ANYWHERE_RE = _key_line_re(BODY_KEY, "[ \t]*")

#: A fence line: at most three spaces of indent, optionally inside block quotes and/or one list
#: marker (a fence opened as `- ``` ` is still a fence), then a run of at least three backticks or
#: tildes, then an info string. The run's LENGTH is captured, not just its character -- see
#: `_visible`.
_FENCE_RE = re.compile(r"""
    ^[ \t]{0,3}
    (?: (?:>[ \t]?)*                                  # any depth of block quote
        (?:[-*+][ \t]+|[0-9]{1,9}[.)][ \t]+)?         # and/or one list marker
        [ \t]{0,3} )?
    (`{3,}|~{3,})
    [ \t]*(.*)$
""", re.VERBOSE)

#: An HTML comment, matched literally. This is the sharpest form of the rule-5 problem: GitHub issue
#: TEMPLATES ship their instructions inside one, and a declaration no human can see contradicts the
#: entire reason the body half exists.
_COMMENT_OPEN = "<!--"
_COMMENT_CLOSE = "-->"

#: `state` is one of VERDICTS; `unit` is the winner (the body's, on a conflict) or None; `body` and
#: `label` are each side's raw declaration, so a caller can name what it is choosing between.
Verdict = collections.namedtuple("Verdict", "state unit body label")


class AmbiguousUnit(ValueError):
    """Two rival declarations, and no rule that could pick between them without guessing.

    Raised for two conflicting `Feature:` lines, a `Branch:` line contradicting the `Feature:` line,
    two conflicting `Branch:` lines, and two distinct `feature:` labels. Deliberately NOT a verdict:
    the five states describe how the body and the label relate, and "this ONE side contradicts
    itself" is not a relation between the two sides -- it is an issue a human has to edit before any
    of the five can be computed. See `read` for what a caller owes this exception."""


def _note(message):
    """One stderr line, never an exception. Same shape and same reason as `sources._card_not_eligible`:
    a diagnostic must never be the thing that breaks a pick."""
    try:
        sys.stderr.write(message)
    except Exception:                     # noqa: BLE001 - a diagnostic must never break a pick
        pass


def _is_unit_name(value):
    """One rule, read by both halves, so the label side and the body side can never drift on what
    counts as a name. `_UNIT_RE` cannot match the empty string, so no emptiness guard is needed."""
    return _UNIT_RE.match(value) is not None


def _visible(text):
    """The part of a body a human actually reads as text: fenced code blocks and HTML comments
    blanked, line structure preserved so every surviving line keeps its own number and indentation.
    Indented code blocks need no pass of their own -- rule 1's three-space ceiling already makes
    every line of one unmatchable, which is why there is no fourth construct here.

    ONE PASS, NOT TWO, AND WHICHEVER CONSTRUCT OPENS FIRST WINS. Stripping fences and then comments
    -- or the reverse -- is wrong in one direction each: fences-first lets a fence shown INSIDE a
    comment eat the real body below the comment, and comments-first lets a `<!--` shown INSIDE a
    fence eat the real body below the fence. Neither ordering can invent a declaration (both
    over-blank, and over-blanking fails towards reading nothing), but both silently swallow one a
    human really wrote -- and this module documents its own format in both constructs, so both
    shapes will occur. Tracking ONE state -- in a fence, in a comment, or in neither -- makes the
    loser's delimiters ordinary content, which is the same principle as the delimiter-character
    rule below, one level up.

    Two CommonMark fence rules are modelled, both because breaking either INVENTS a declaration:

      - a fence closes only on the same delimiter CHARACTER, so a `~~~` line inside an open ``` fence
        is fenced CONTENT rather than a close;
      - a fence closes only on a run AT LEAST AS LONG as the one that opened it, and a closing fence
        may not carry an info string. This is the rule `goal_size.py` explicitly discloses that it
        does NOT model (its own delimiters are always exactly three characters). Here it had to be:
        wrapping a three-backtick example in a four-backtick fence is THE standard way to document
        fenced syntax, so it is the shape every doc teaching this marker will use -- and without the
        length rule the inner ``` closed the outer fence and the example declared a unit.

    An UNTERMINATED fence or comment blanks to EOF -- conservative, and deliberately failing towards
    not-declaring on a malformed body.

    STILL NOT MODELLED, stated rather than implied (the disclosure `goal_size.py` makes, and that the
    first version of this module dropped while keeping a bare "per CommonMark" claim): tab expansion
    is approximated by treating a leading tab as indentation rather than as four columns; the content
    indentation of a fence inside a list item is not tracked, so such a fence is closed by a matching
    run at any depth at or under three spaces; a backtick info string containing a backtick is not
    rejected; and a comment is matched literally rather than by the HTML spec's bogus-comment
    grammar. Every one of those approximations blanks MORE than the spec would, never less, so each
    fails towards reading no declaration."""
    out = []
    fence = None                           # (delimiter character, run length) of the open fence
    in_comment = False
    for line in text.split("\n"):
        if fence is not None:              # inside a fence: only a matching close is not content
            m = _FENCE_RE.match(line)
            if m:
                run, info = m.group(1), (m.group(2) or "").strip()
                if run[0] == fence[0] and len(run) >= fence[1] and not info:
                    fence = None
            out.append("")
            continue
        if in_comment:
            closed = line.find(_COMMENT_CLOSE)
            if closed < 0:
                out.append("")
                continue
            in_comment = False             # the tail of this line is text again
            line = _spaces(closed + len(_COMMENT_CLOSE)) + line[closed + len(_COMMENT_CLOSE):]
        m = _FENCE_RE.match(line)
        if m:
            fence = (m.group(1)[0], len(m.group(1)))
            out.append("")                 # the delimiter line itself never counts as content
            continue
        line, in_comment = _blank_comments(line)
        out.append(line)
    return "\n".join(out)


def _spaces(width):
    return " " * width


def _blank_comments(line):
    """Blank every complete `<!-- ... -->` span on one line -> `(line, still_inside_a_comment)`."""
    opened = line.find(_COMMENT_OPEN)
    while opened >= 0:
        closed = line.find(_COMMENT_CLOSE, opened + len(_COMMENT_OPEN))
        if closed < 0:
            return line[:opened] + _spaces(len(line) - opened), True
        stop = closed + len(_COMMENT_CLOSE)
        line = line[:opened] + _spaces(stop - opened) + line[stop:]
        opened = line.find(_COMMENT_OPEN, stop)
    return line, False


def _single(found):
    """The one value in `found`, or None -- raising when the declarations genuinely rival each other.

    Case-insensitive de-duplication, first spelling wins: two declarations differing only in case
    name the same thing (GitHub label names are case-insensitively unique), so treating them as
    rivals would raise on a body a human never contradicted themselves in."""
    seen, distinct = set(), []
    for value in found:
        if value.lower() not in seen:
            seen.add(value.lower())
            distinct.append(value)
    if len(distinct) > 1:
        raise AmbiguousUnit(
            "issue declares more than one unit (%s) -- remove all but one; nothing here may pick "
            "between two declarations a human wrote" % ", ".join(repr(d) for d in distinct))
    return distinct[0] if distinct else None


def _branch_agrees(branch, unit):
    """Does a `Branch:` line corroborate the declared unit? `feature/<unit>` does, and so does any
    sub-branch of it (`feature/<unit>/<sub>`, the model's shape for a dependency found mid-flight).
    Compared case-insensitively, for the same reason `_single` de-duplicates that way."""
    expected = (BRANCH_PREFIX + unit).lower()
    return branch.lower() == expected or branch.lower().startswith(expected + "/")


def _note_hidden_marker(text):
    """Say so when the ONLY declaration in a body sits somewhere rule 5 refuses to read.

    Without this, fencing a marker you meant returns exactly what declaring nothing returns, with no
    warning anywhere -- the docstring's note is addressed to whoever builds the stamping half, and
    nobody is addressing the human who wrote the fence. Only reached when nothing was declared, so
    any hit here is by construction a hidden one."""
    hidden = [v for v in _MARKER_ANYWHERE_RE.findall(text) if _is_unit_name(v)]
    if hidden:
        _note("sigma: features: '%s: %s' appears only inside a code block, an indented block or "
              "an HTML comment, so it was NOT read as a declaration. Write the marker as its own "
              "line, indented by at most three spaces, if you meant to declare a unit.\n"
              % (BODY_KEY, hidden[0]))


def parse_body(body):
    """The unit an issue's BODY declares, or None. Raises `AmbiguousUnit` on rival declarations.

    CRLF is normalised first. MEASURED, and the measurement was CORRECTED once, which is the more
    useful half of the story: `GET /issues` also returns PULL REQUESTS, and PR bodies -- far more
    often tool- or template-generated -- carry CRLF at a much higher rate than issue bodies do. The
    first version of this note counted both and claimed `cli/cli` 14/100 and `microsoft/vscode`
    10/100. Excluding PRs, over REAL ISSUE BODIES ONLY: 0 of 647 here, `cli/cli` 1 of 87,
    `microsoft/vscode` 1 of 94, `python/cpython` 0 of 60. So CR is RARE in the input this parser
    actually reads, not common -- an order of magnitude rarer than first stated.
    It is kept regardless, and the honest reason is now the narrow one: it is cheap defensive code
    for the adopters Sigma ships to, where the rate is low but demonstrably nonzero, and a
    marker in such a body would otherwise never match the end-of-line anchor at all. The lone-CR arm
    covers the older classic-Mac ending the same normalisation implies."""
    text = (body if isinstance(body, str) else "").replace("\r\n", "\n").replace("\r", "\n")
    visible = _visible(text)
    unit = _single([v for v in _MARKER_RE.findall(visible) if _is_unit_name(v)])
    if unit is None:
        _note_hidden_marker(text)
        return None
    for branch in _BRANCH_RE.findall(visible):
        # Every branch line is checked, and ONLY a disagreeing one raises. Running `_single` over
        # them first -- which this did -- made two branch lines rivals purely by differing, so a
        # body naming both `feature/<unit>` and `feature/<unit>/<sub>` raised while contradicting
        # nothing, and `_branch_agrees` (which blesses exactly that pair) was never consulted.
        # `_single` is the wrong tool for a derived echo: it asks "is there only one?", and the
        # question here is "does any of them disagree?".
        if not _branch_agrees(branch, unit):
            raise AmbiguousUnit(
                "body declares %r but a %s: line says %r -- %s: names the branch of the UNIT, "
                "always %s%s or a sub-branch of it, never this goal's own branch; correct one of "
                "the two lines" % (unit, BRANCH_KEY, branch, BRANCH_KEY, BRANCH_PREFIX, unit))
    return unit


def _label_name(label):
    """One label's name, from either payload shape this repo produces -- gh's `{"name": ...}` dicts
    or the board queue's bare strings. Anything else, INCLUDING a dict whose `name` is not a string,
    contributes no name rather than raising: a reader of someone else's payload must not be the
    thing that breaks a pick."""
    if isinstance(label, str):
        return label
    if isinstance(label, dict):
        name = label.get("name")
        return name if isinstance(name, str) else None
    return None


def parse_labels(labels):
    """The unit an issue's LABELS declare, or None. Raises `AmbiguousUnit` on two distinct ones."""
    if not isinstance(labels, (list, tuple, set, frozenset)):
        return None                        # a `labels` that is not a collection of labels declares
                                           # nothing -- same promise as `_label_name` makes for one
                                           # bad ELEMENT, one level further out
    found = []
    for label in labels:
        name = _label_name(label)
        if not name or not name[:len(LABEL_PREFIX)].lower() == LABEL_PREFIX:
            continue
        value = name[len(LABEL_PREFIX):].strip()
        if _is_unit_name(value):
            found.append(value)
    return _single(found)


def read(issue):
    """-> `Verdict(state, unit, body, label)` for one issue payload. Never mutates `issue`.

    `issue` is any mapping carrying `body` and `labels` -- the shape every backlog source already
    hands around. A missing key, a payload that is not a mapping at all, AND a key whose value is
    the wrong type (`{"body": 123}`, `{"labels": 7}`) are all simply an absent declaration rather
    than an error: a source that fetched no body must degrade to `none` (today's single-base
    behaviour), not break the pick. That tolerance is the module's own rule applied at every depth --
    `_label_name` already tolerates one bad ELEMENT, so tolerating a bad `labels` is the same
    promise one level out, not a new one.

    THE ONE OBLIGATION THIS PUTS ON CALLERS. `read` is total over payload SHAPE but not over payload
    CONTENT: it propagates `AmbiguousUnit` when an issue contradicts itself (rival `Feature:` lines,
    a `Branch:` line that disagrees, two distinct `feature:` labels). That is deliberate -- there is
    no honest verdict for it, and first-wins would silently base a goal on a branch nobody chose --
    but it means ANY SWEEP OVER MANY ISSUES MUST CATCH IT PER ISSUE. A census, a backlog scan or a
    pick loop that lets it propagate turns one hand-edited issue into a total outage of the queue,
    and a stray hand-added label is an observed failure mode on this repo, not a hypothetical one.
    Catch it, treat that ONE issue as unresolvable (skip it, or park it for a human to edit), and
    carry on with the rest."""
    get = getattr(issue, "get", None)
    if not callable(get):
        return Verdict(NONE, None, None, None)
    body_unit = parse_body(get("body"))
    label_unit = parse_labels(get("labels"))
    if body_unit and label_unit:
        # Case-insensitive comparison for the same reason `_single` de-duplicates that way; the
        # BODY's spelling is the one returned, because on any disagreement the body wins.
        state = AGREE if body_unit.lower() == label_unit.lower() else CONFLICT
        return Verdict(state, body_unit, body_unit, label_unit)
    if body_unit:
        return Verdict(BODY_ONLY, body_unit, body_unit, None)
    if label_unit:
        return Verdict(LABEL_ONLY, label_unit, None, label_unit)
    return Verdict(NONE, None, None, None)

#!/usr/bin/env python3
"""Goal-size classifier — deterministic, zero-LLM detection of an oversized ("epic-shaped") goal
before the loop ever spends a token on it. Same shape as sigma-model/scripts/predict.py (ordered
signals, first match wins, pure functions, module-constant thresholds), but a different axis: not
WHICH model a goal deserves, WHETHER a goal is actually several goals wearing one issue number.

Lives BESIDE loop.py (not under skills/sigma-model/, despite the family resemblance to predict.py):
loop.py's own module loader (`_load()`, loop.py:26-28) resolves every sibling module from its OWN
directory only, so a classifier imported cross-skill would need a second loader — this repo's "one
_load() per script, same-directory only" convention deliberately does not have one.

v1 signals, in order (first hit wins): body length (word count, then line count) -> independent
`## ` sections -> top-level checkboxes -> explicit multi-phase structure.

CALIBRATION PROVENANCE (#520): the four thresholds below are fit against THIS repo's own issue
history — 269 issues measured on 2026-08-08 — not picked in the abstract, and checked in BOTH
directions, not just tuned to catch positives:

  - WORD_THRESHOLD=1200: the corpus's largest genuine (non-epic) goal is #522 at 801 words
    (goal p99=639); 1200 clears it with real margin instead of hugging the observed max.
  - LINE_THRESHOLD=150: unchanged — no corpus evidence required retuning it.
  - SECTION_THRESHOLD=6: the fence-stripped corpus's largest genuine goal tops out at 5
    independent `## ` sections. The old value of 3 produced 32 flags out of 74 issues checked, with
    ZERO true positives among them — every one was a conventionally-shaped Context/Scope/AC/
    Verification goal (the #519/#520/#521 shape), never an actual epic.
  - CHECKBOX_THRESHOLD=4: unchanged, and deliberately so — it is the ONLY signal with any true
    positive in this corpus. Lowering it to 3 would catch one more real epic (#156) at the cost of
    34 additional false positives measured across the corpus; see the known-miss note in
    tests/test_goal_size_corpus.py for the full arithmetic. #156 stays a documented, accepted miss.

These numbers are fit to ONE repo's history and ship as the plugin's defaults regardless — an
adopting repo's own issue conventions may differ, which is why the config template
(skills/sigma-init/templates/config.json.tmpl's `_goal_decompose` explainer) still recommends
`mode: log` as the first rung for a NEW adopter: log mode surfaces what the classifier would have
flagged, on that repo's own real goals, before anything ever acts on it.

ponytail: same as predict.py's — a known-coarse heuristic with an explicit, versioned upgrade path
(retune the constants, or replace with a corpus-fit model) rather than an LLM call on this path,
which must stay zero-latency, zero-cost, and hermetically testable."""
import re

# --- decomposition markers -----------------------------------------------------------------------
# Single source of truth for the two first-line body markers a decomposition child / meta-goal
# carries. Read by THIS module's own caller, loop.py's `decompose_check` guard (a marked goal is
# exempt from goal-size classification entirely -- it was deliberately authored, not accidentally
# oversized), AND by backlog_check.py's dedup exemption (#521: a marked goal is exempt from the
# confident duplicate/obsoleted-by/in-flight-similarity findings -- never from an explicit blocker or
# a recorded hand-off). Both read these constants instead of retyping the literal, so the two checks
# can never silently drift apart (doctor.py importing `backlog_check._BLOCK_RE` verbatim, rather than
# retyping the pattern, is the existing precedent for this "one definition, several readers" shape).
DECOMPOSED_FROM_MARKER = "sigma:decomposed-from="    # this goal IS a decomposition child of #N
DECOMPOSE_OF_MARKER = "sigma:decompose-of="          # this goal IS the meta-goal decomposing #N

# --- signal 1: body length ----------------------------------------------------------------------
WORD_THRESHOLD = 1200      # body word count strictly above this alone flags the goal (corpus p99=639,
                           # max=801 at #522 — a legitimate large-but-single goal; see module docstring)
LINE_THRESHOLD = 150       # body line count strictly above this alone flags the goal (a dense
                           # checklist/table can be long-and-thin rather than long-and-wordy)

# --- signal 2: independent top-level sections ---------------------------------------------------
SECTION_THRESHOLD = 6      # `## ` headings (H2 only; a `###` subsection never counts; content inside
                           # a fenced code block never counts either — see _strip_fences) at or above
                           # this reads as multiple independent scopes stapled into one issue
                           # (fence-stripped corpus max is 5 — see module docstring)

# --- signal 3: top-level checkboxes ---------------------------------------------------------------
CHECKBOX_THRESHOLD = 4     # non-indented `- [ ]`/`- [x]` (`-`/`*`/`+` bullets) at or above this reads
                           # as multiple distinct deliverables, not one goal's own sub-steps — the
                           # only signal with a true positive in the calibration corpus

_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
_SECTION_RE = re.compile(r"^##(?!#)[ \t]+\S.*$", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^[-*+][ \t]{1,3}\[[ xX]\][ \t]+\S", re.MULTILINE)
# Anchored to line-start structure (optional heading/bullet/numbered-list marker, optional bold),
# never a bare mid-sentence "\bphase\b" — the old unanchored version flagged prose accidents like
# "this is the phase-2 follow-up to the phase-1 work" as a genuine two-phase body (#520; reproduced
# live against this repo's own issue #519, whose changelog-style prose mentions "Phase-1/Phase-2" —
# never at a line start — without describing an actual multi-phase goal).
_PHASE_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+|[0-9]+[.)][ \t]+)?(?:\*\*)?phase[ \t-]*([0-9]+)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_fences(text):
    """Blank every ```-or-~~~ fenced block (delimiter lines included) to empty lines, for
    section/checkbox/phase counting ONLY — the caller keeps counting words/lines on the RAW body,
    because fenced bulk is still bulk someone has to read. A fenced code sample can contain lines
    that are structurally indistinguishable from a markdown heading, a checkbox, or a phase marker
    (a shell `## banner comment`, a checklist pasted as an example, a code comment mentioning
    "phase 2") without actually being one.

    Tracks the OPENING delimiter CHARACTER and closes only on a matching run — CommonMark: a fence
    closes only on a delimiter of the same character (and at least the same length; length isn't
    modeled here, only the character is, since every delimiter this regex matches is exactly three
    chars). A ~~~ line inside an open ``` fence is ordinary fenced CONTENT, not a close, and vice
    versa — the earlier version toggled on ANY fence-look-alike regardless of character: a
    mismatched delimiter closed the fence early, so the lines that followed were counted as real
    structure — a bug that failed TOWARD flagging, never away from it (pinned by
    test_classify_mismatched_fence_delimiter_does_not_close_the_fence).

    An UNTERMINATED fence (opened, never closed) blanks everything from the opening delimiter to
    EOF, not just some heuristic extent — conservative, and deliberately fails TOWARD not-flagging a
    malformed body rather than toward flagging one (pinned by
    test_classify_unterminated_fence_blanks_to_eof)."""
    out = []
    open_delim = None                      # the delimiter ('```' or '~~~') that opened the
                                           # current fence, or None when not inside one
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        if m:
            delim = m.group(1)
            if open_delim is None:
                open_delim = delim                     # opens a new fence
            elif delim == open_delim:
                open_delim = None                      # closes THIS fence (matching character)
            # else: a differently-fenced delimiter line inside an open fence is content, not a
            # close — falls through to being blanked below like any other line in the fence.
            out.append("")                 # the delimiter line itself never counts as content
            continue
        out.append("" if open_delim is not None else line)
    return "\n".join(out)


def classify(body):
    """Classify one goal's BODY (never the title — see loop.py's decompose_check for why the
    caller never passes one). Ordered signals, first match wins. Returns
    `(flagged: bool, reason: str)`; `reason` is always a short, single-line phrase — it lands
    verbatim in a park detail / local action-log field, and both reject an embedded newline, so no
    branch here may ever build a multi-line reason."""
    text = body or ""

    words = len(text.split())
    if words > WORD_THRESHOLD:
        return True, f"body is {words} words (over {WORD_THRESHOLD})"

    lines = len(text.splitlines())
    if lines > LINE_THRESHOLD:
        return True, f"body is {lines} lines (over {LINE_THRESHOLD})"

    stripped = _strip_fences(text)

    sections = len(_SECTION_RE.findall(stripped))
    if sections >= SECTION_THRESHOLD:
        return True, f"{sections} independent ## sections (>= {SECTION_THRESHOLD})"

    checkboxes = len(_CHECKBOX_RE.findall(stripped))
    if checkboxes >= CHECKBOX_THRESHOLD:
        return True, f"{checkboxes} top-level checkboxes (>= {CHECKBOX_THRESHOLD})"

    phases = sorted(set(_PHASE_RE.findall(stripped)), key=int)
    if len(phases) >= 2:
        return True, "explicit multi-phase structure (Phase " + "/".join(phases) + ")"

    return False, ""

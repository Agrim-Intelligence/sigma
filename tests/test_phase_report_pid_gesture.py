"""Pin `--pid "$PPID"` onto every documented `phase_report.py start|end .sdlc` gesture (#2667).

WHY A DOC TEST IS THE RIGHT SHAPE HERE. #2667's fix is decided by IDENTITY: `end` trusts a
same-phase marker's start time only when the caller's `--pid "$PPID"` (plus, automatically,
`CODEX_THREAD_ID`/`CLAUDE_CODE_SESSION_ID`) matches what `start` stamped. That mechanism is
useless the moment a documented invocation forgets to pass it -- exactly the shape #1687 and
#1199 shipped as real bugs, and `tests/test_sdlc_loop_skill_session_pid.py` /
`tests/test_triage_skill_docs.py` are this repo's own precedent for catching it: pin the literal
invocation text, never the surrounding prose. This file is that precedent applied across all FIVE
places the `end .sdlc` gesture is documented (D5 of `.sdlc/plans/2667.md`): the two SKILL.md files,
`references/progress.md`, the Cursor `.mdc` rule, and its generator `sdlc_init.py` (kept byte-for-
byte identical to the `.mdc` by `tests/test_sdlc_init.py`, which this file does not duplicate).

`tests/test_phase_report.py`'s own `_run_documented`/`_documented` helpers parse ONLY the first of
these five (the fenced block in `skills/agrim-loop/SKILL.md`) -- this file is what proves the other
four never drop the flag either.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

DOCS = {
    "loop SKILL": ROOT / "skills" / "agrim-loop" / "SKILL.md",
    "progress": ROOT / "skills" / "agrim-loop" / "references" / "progress.md",
    "goal SKILL": ROOT / "skills" / "agrim-goal" / "SKILL.md",
    "mdc": ROOT / ".cursor" / "rules" / "output-contract.mdc",
    "sdlc_init": ROOT / "skills" / "agrim-init" / "scripts" / "sdlc_init.py",
}

#: Anchored on `.sdlc` immediately after the verb -- `end --agent-id` and `codex-agent-id`
#: (mentioned in prose elsewhere in these same docs) never carry `.sdlc` right after the verb, so
#: neither is miscounted as an invocation.
_INVOCATION = re.compile(r'phase_report\.py["\']?\s+(start|end)\s+\.sdlc\s')

#: Whitespace-collapsed before matching (the same reason test_triage_skill_docs.py gives): this
#: repo's prose hand-wraps a `start`/`end` invocation across physical lines, and a literal
#: per-physical-line check would false-fail on correctly-wrapped, correctly-fixed prose.
_FLAT = {name: re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
        for name, path in DOCS.items()}

#: A terminator for one invocation's own "extent": the closing (single- or triple-) backtick that
#: ends its inline/fenced span, OR the start of the NEXT `phase_report.py` invocation ("python3"
#: never recurs within one invocation's own text, but does open the next one in the fenced block,
#: where two invocations sit back to back with no backtick between them), OR a `#` comment (the
#: fenced block's own `# <phase>: goal|research|...` line) -- never the rest of the paragraph, so
#: prose describing the flag several sentences later can't falsely satisfy this.
_TERMINATOR = re.compile(r"`|#|python3")


def _extents(name):
    """[(verb, extent_text)] for every documented invocation in doc `name`, in order."""
    flat = _FLAT[name]
    out = []
    for m in _INVOCATION.finditer(flat):
        rest = flat[m.end():]
        term = _TERMINATOR.search(rest)
        extent = rest[:term.start()] if term else rest
        out.append((m.group(1), extent))
    return out


_EXPECTED_VERBS = {
    "loop SKILL": ["end", "start"],
    "progress": ["end", "start"],
    "goal SKILL": ["end", "start"],
    "mdc": ["end"],
    "sdlc_init": ["end"],
}


def test_every_doc_still_documents_its_invocations():
    """Guards this test against silently matching nothing if a doc is ever restructured: this
    goes red FIRST and loudly, rather than the flag-pin test below vacuously passing on zero
    invocations found."""
    for name in DOCS:
        verbs = sorted(v for v, _ in _extents(name))
        assert verbs == sorted(_EXPECTED_VERBS[name]), (
            f"{name} ({DOCS[name]}): expected {sorted(_EXPECTED_VERBS[name])} documented "
            f"phase_report.py .sdlc invocations, found {verbs} -- if this doc no longer "
            f"documents the gesture this way, this test is stale, not passing")


def test_every_documented_start_and_end_passes_the_callers_pid():
    for name in DOCS:
        for verb, extent in _extents(name):
            assert re.search(r'--pid\s+"\$PPID"', extent), (
                f"{name} ({DOCS[name]}): a documented `{verb} .sdlc` gesture has no "
                f"`--pid \"$PPID\"` immediately after it (extent: {extent!r}) -- #2667's `end` "
                f"trusts a same-phase marker's start time only when this identity matches the "
                f"caller's, so a bare invocation both stamps and checks nothing")

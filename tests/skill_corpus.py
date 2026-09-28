"""One skill as an agent actually reads it: its `SKILL.md` plus every reference file beside it.

#1611 split `skills/agrim-loop/SKILL.md` into a body plus `references/*.md`, because Claude Code
re-attaches only the FIRST 5,000 TOKENS of a skill after a conversation is summarised, and that body
was 3.4x the cap with all three of its uppercase gates past the cliff. The documentation-drift
guards in this suite — "the skill says X, so it cannot silently stop matching the code that does X"
— predate the split and read the single file. Their subject was never the FILE; it was the SKILL.
So they read the whole corpus now.

That is not a weakened assertion, for two reasons. It still fails when the text is deleted from
wherever it lives (`tests/test_skill_structure.py`'s `--preserved` control is the same shape), and
`evals/skill_structure.py` separately fails the build if any `*.md` beside a `SKILL.md` stops being
reachable by following links from it — so "present in the corpus" still means "an agent following
the prose can get to it".

**A pin that is specifically about the always-attached BODY should NOT use this.** Where the point
is that an instruction survives a compaction — the budget itself, or an idiom the agent must still
have after a summary — reading `SKILL.md` directly is the stronger assertion and stays.
`tests/test_emit_prose.py` and `tests/test_sdlc_loop_skill_session_pid.py` are deliberately left
that way.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def skill_corpus(skill: str, root: pathlib.Path | None = None) -> str:
    """Every markdown file of `skills/<skill>/`, concatenated, `SKILL.md` first.

    `SKILL.md` leads so that a substring search reads the body before any reference file, and the
    rest is sorted, so the result is deterministic. A skill with no reference files (most of them)
    returns exactly what reading `SKILL.md` alone used to return."""
    skill_dir = (root or ROOT) / "skills" / skill
    body = skill_dir / "SKILL.md"
    texts = [body.read_text(encoding="utf-8")]
    texts += [p.read_text(encoding="utf-8")
              for p in sorted(skill_dir.rglob("*.md")) if p != body]
    return "\n\n".join(texts)

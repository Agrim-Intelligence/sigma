"""Issue #1612 — the skill LISTING budget (the `description:` frontmatter field), not the body.

Claude Code's skill listing budget "scales at 1% of the model's context window", and on overflow it
drops descriptions entirely, starting with the least-invoked skills — leaving those skills listed
name-only, which strips the very keywords the model matches a request against. `description:` is a
ROUTING signal read by the model before any skill body is ever opened; it is not the place for
maintainer-facing provenance ("which platform companion this defers to", "why this skill has none").

Ten skills carried an identical maintainer-boilerplate tail in their `description:` field, in two
families:
  - "Portable executor — prefer superpowers:X on Claude when installed; this is the built-in
    equivalent for every other host." (agrim-brainstorm, agrim-implement, agrim-plan, agrim-review,
    agrim-verify)
  - "A conditional-risk review/skill orthogonal to agrim-review; always Sigma's own (no companion
    equivalent)." (agrim-contract-check, agrim-migration-check, agrim-release-check,
    agrim-security-review, agrim-debug)

Both blocks address a maintainer, not the routing decision, and both already live in (or were moved
into) each skill's BODY — the "Executor resolution (host-aware)" section for the first family, and an
explicit provenance line right under the H1 for the second. This test pins the boilerplate OUT of the
listing-budget field; it does not assert anything about the body, which is covered separately by
`evals/skill_structure.py` (the compaction-cliff budget) and is a different concern from this one (the
plugin's skill LISTING budget)."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

#: the two boilerplate families from #1612, matched loosely enough to catch either wording variant
#: actually shipped ("review" vs "skill", "when installed" vs "when it's installed", the agrim-review
#: one naming the code-review plugin instead of a bare superpowers:X).
_BOILERPLATE_PATTERNS = (
    re.compile(r"Portable executor —"),
    re.compile(r"conditional-risk (?:review|skill) orthogonal to agrim-review"),
)


def _skill_dirs():
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def _description(skill_dir: pathlib.Path) -> str:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"(?m)^description:\s*(.*)$", text)
    assert m, f"{skill_dir.name}: SKILL.md has no description: field"
    return m.group(1)


def test_no_description_carries_maintainer_provenance_boilerplate():
    offenders = []
    for skill_dir in _skill_dirs():
        desc = _description(skill_dir)
        for pattern in _BOILERPLATE_PATTERNS:
            if pattern.search(desc):
                offenders.append((skill_dir.name, pattern.pattern))
    assert offenders == [], (
        "description: field still carries maintainer-provenance boilerplate that belongs in the "
        f"skill body instead (issue #1612): {offenders}"
    )


#: the 10 skills #1612 actually touched — scoped deliberately, not "every skill in the repo": a
#: repo-wide heuristic here would also grade the 27+ untouched skills against a shape #1612 never
#: promised them (agrim-wizard's real description, for instance, states its trigger as "triggered
#: automatically by session_start.sh", not a "Use ..." clause, and was never part of this fix).
_TOUCHED_SKILLS = (
    "agrim-brainstorm", "agrim-implement", "agrim-plan", "agrim-review", "agrim-verify",
    "agrim-contract-check", "agrim-migration-check", "agrim-release-check",
    "agrim-security-review", "agrim-debug",
)


def test_touched_descriptions_still_state_what_and_when():
    """Stripping the boilerplate tail must not silently gut the routing signal itself: each of the
    10 descriptions #1612 touched still needs a "what it does" clause and a "when to use it" clause
    (the DoD's own second bullet) — checked here as its own slash command still being named."""
    for name in _TOUCHED_SKILLS:
        desc = _description(SKILLS_DIR / name)
        assert f"/{name}" in desc, f"{name}: description dropped its own /{name} trigger mention"

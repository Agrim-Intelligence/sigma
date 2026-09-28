"""#1894: `skills/agrim-implement/SKILL.md` said nothing about this repo's own fail-closed /
untrusted-input security defaults, and three real, security-relevant bugs (#1761, #1765, #1767 --
PRs #1861, #1877, #1887) shipped past Implement, each caught only by an independent reviewer --
Plan-Review before the code existed (#1767) or Review after it did (#1761, #1765) -- never by the
implementer's own diff-time check. SKILL.md is the operational contract an implementer actually
reads (the same convention `test_sdlc_loop_skill_merge_doc.py` and siblings already rely on for
`agrim-loop`'s own SKILL.md) -- so pinning the anchors below is a real regression guard, not
decoration: a future edit that quietly drops this section, or nests it back inside the
superpowers-preferred branch where a Claude-Code+superpowers reader would never see it, fails one
of these for a concrete, named reason.

Each anchor is tied to something that would concretely go wrong if it disappeared -- never a bare
"the file changed" check. See `.sdlc/plans/1894.md` for the full reasoning, and the three PRs above
for the real fix code these citations are grounded in (an auth gate's live password check, a
metrics project filter, and an accounts-store migration step)."""
import pathlib

SKILL = (pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-implement" / "SKILL.md").read_text(
    encoding="utf-8"
)


def test_security_defaults_applies_regardless_of_which_executor_ran():
    # The file opens with an executor-resolution fork: on Claude Code with `superpowers` installed,
    # `superpowers:test-driven-development` is preferred over the rest of THIS file's own TDD
    # guidance -- and that companion file carries no security guidance at all (verified directly
    # during this issue's plan-review: zero hits for fail-closed/untrusted-input content across
    # it and its own sibling companions). Without an explicit "regardless" clause, a new security
    # section nested among the TDD sections below reads as exactly the content that fork tells a
    # Claude-Code+superpowers reader to skip -- invisible on the very host these three bugs shipped
    # from.
    assert "applies regardless of which executor" in SKILL, (
        "SKILL.md's executor-resolution note does not say the Security defaults section applies "
        "regardless of which executor ran -- without this, the section is effectively skipped on "
        "Claude Code + superpowers, the host #1761/#1765/#1767 actually shipped from"
    )


def test_security_defaults_section_exists():
    assert "## Security defaults (fail-closed, untrusted input)" in SKILL, (
        "SKILL.md has no 'Security defaults' section -- #1894's whole point is a short, concrete "
        "section pointing implementers at this repo's own fail-closed/untrusted-input defaults"
    )


def test_points_at_agents_md_safety_property():
    # Not a restatement -- a pointer. AGENTS.md's SAFETY property is the standing rule; this test
    # only pins that the section actually names it, not that it re-derives its content.
    assert "AGENTS.md" in SKILL and "SAFETY" in SKILL, (
        "SKILL.md's Security defaults section does not point at AGENTS.md's SAFETY property -- "
        "the standing rule this repo already has stays undiscovered until Review otherwise"
    )


def test_points_at_north_star_untrusted_input_standing_rule():
    # `.sdlc/context/north-star.md` Standing rule 2's own heading ends "...is untrusted by default".
    # Pinning that exact tail (not the whole heading) keeps this anchor stable against a rewording
    # of the rule's SQL/path-specific opening clause while still proving the real rule is named.
    assert "north-star" in SKILL and "untrusted by default" in SKILL, (
        "SKILL.md's Security defaults section does not point at the north-star's untrusted-input "
        "standing rule -- the issue's own cited precedent stays undiscovered until Review otherwise"
    )


def test_checklist_covers_auth_permission_default_deny_with_real_bug_citations():
    # #1761 (an auth gate's live `member.get("must_change_password", True)` check) and
    # #1767 (an accounts store's user-migration fallthrough) are two DIFFERENT code
    # paths that share one shape: a missing/legacy fact must read as "still denied," never as
    # "cleared." Both citations must be present -- collapsing to just one would understate the
    # shape as a single-site bug instead of a recurring pattern.
    assert "#1761" in SKILL and "#1767" in SKILL, (
        "SKILL.md's default-deny checklist item does not cite both #1761 and #1767 -- the point "
        "is that this shape recurred across two independent code paths, not once"
    )
    assert "default to deny" in SKILL, (
        "SKILL.md's checklist does not name the fail-closed default-to-deny requirement explicitly"
    )


def test_checklist_covers_scalar_vs_collection_id_rejection_with_real_bug_citation():
    # #1765: a metrics API's project filter treated an unwrapped scalar id
    # as an iterable collection -- `str`/`bytes`/`bytearray` are all iterable in Python, so this
    # silently split into characters/byte-ordinals and produced a real cross-project data leak.
    assert "#1765" in SKILL, (
        "SKILL.md's scalar-vs-collection checklist item does not cite #1765, the real bug it exists "
        "to prevent a recurrence of"
    )
    assert "scalar" in SKILL and "bytearray" in SKILL, (
        "SKILL.md's checklist does not name the scalar-vs-iterable shape (str/bytes/bytearray) that "
        "#1765's real fix guards against"
    )

"""#1895: `skills/agrim-implement/SKILL.md` never named mutation testing as a standing practice,
never guarded against a vacuous test (one whose own fixture trivially satisfies its assertion
without exercising the real code path), and stated its rules (red-green-refactor, the refactor-smell
names) with no citation to their real source. This mirrors `test_sdlc_implement_security_defaults.py`'s
own convention exactly: SKILL.md is the operational contract an implementer actually reads, so
pinning stable anchor phrases in it is a real regression guard, not decoration -- a future edit that
quietly drops a section, or re-nests it inside the superpowers-preferred branch where a
Claude-Code+superpowers reader would never see it, fails one of these for a concrete, named reason.

Every citation below was independently verified against its real source before being pinned here --
twice. The first Plan-Review round REJECTED an earlier draft's claim that the vacuous-test citation
was fabricated: that claim rested on searching issue/PR *prose* (`gh search issues`/`gh search
prs`) and on `git log -S`/`-L` against a stale, 40-commits-behind local `main` branch, both of which
are blind to a fact that lives in a *code comment* on `origin/main`, in commit `834d0a82` (PR
#1838), which never changed the test function's own name. A second, independent Plan-Review round
re-verified the correction from scratch and found one further error (the #1762 bullet below) before
approving. See `.sdlc/plans/1895.md` for the full back-and-forth; see PR #1879 (#1764), PR #1838
(#1829, both the pagination fix and the vacuous-fixture incident recorded in its own corrected
docstring on `tests/test_feature_scope.py`), and issue #1762's own "PRE-PR CODE-REVIEW" comment
(not its thin PR body) for the real fix code and review artifacts these citations are grounded in.
"""
import pathlib

SKILL = (pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-implement" / "SKILL.md").read_text(
    encoding="utf-8"
)


def test_mutation_testing_applies_regardless_of_which_executor_ran():
    # Same shape as security-defaults' own executor-resolution clause, and for the same reason: on
    # Claude Code with `superpowers` installed, the executor-resolution fork tells a reader to go
    # to `superpowers:test-driven-development` instead of the rest of this file. Without an
    # explicit "applies regardless" pointer NAMED for this section specifically, a new section
    # placed among the existing TDD content reads as exactly what that fork tells the reader to
    # skip. The anchor is a phrase distinct from the existing Security-defaults sentence, so this
    # test cannot pass merely because THAT unrelated sentence already exists.
    assert "same kind of cross-cutting addition" in SKILL, (
        "SKILL.md's executor-resolution note does not point at the Mutation testing section as "
        "applying regardless of which executor ran -- without this, the section is effectively "
        "skipped on Claude Code + superpowers"
    )


def test_mutation_testing_section_exists():
    assert "## Mutation testing" in SKILL, (
        "SKILL.md has no 'Mutation testing' section -- #1895's whole point is naming mutation "
        "testing as a standing, expected practice, not leaving each goal to reinvent it ad hoc"
    )


def test_mutation_testing_cites_1764_with_the_real_verified_figures():
    # #1764/PR #1879's own body: "removing the version-check clause makes 4/8 new tests go red
    # (and silently double-increments config_version in the real DB, 0->2, while telling both
    # callers 'success')". Anchoring on "config_version" (the distinctive fact from the real fix,
    # not just the bare issue number) proves the actual example is present, not just a mention.
    assert "#1764" in SKILL and "#1879" in SKILL, (
        "SKILL.md's Mutation testing section does not cite both #1764 and its PR #1879"
    )
    assert "config_version" in SKILL, (
        "SKILL.md does not name the real, verified fact #1764's mutation test caught (config_version "
        "silently reaching 2 while both callers were told 'success') -- citing the issue number "
        "alone would be a citation with nothing checkable behind it"
    )


def test_mutation_testing_cites_1829_pagination_fix_with_the_real_verified_figure():
    # #1829/PR #1838's own body: a 220-item backlog reproduction of a duplicate-plus-dropped-issue
    # bug in the old per_page-recomputed-from-filtered-count pagination logic. "220-item" is the
    # distinctive, verified-real fact -- not decoration.
    assert "#1829" in SKILL and "#1838" in SKILL, (
        "SKILL.md's Mutation testing section does not cite both #1829 and its PR #1838"
    )
    assert "220-item" in SKILL, (
        "SKILL.md does not name the real, verified fact #1829's pagination mutation test caught "
        "(a 220-item backlog reproduction of the duplicate/dropped-issue bug)"
    )


def test_does_not_cite_1762_as_a_mutation_testing_example():
    # #1762's own issue thread genuinely has an "8/8 new tests pass" figure (found and verified
    # during Plan-Review round 2) -- but it documents an ordinary green test-suite re-run, not the
    # revert/confirm-red/restore cycle #1895 attributes to it. Citing #1762 here would misrepresent
    # a real fact as evidence for a claim it doesn't support -- the exact failure mode this whole
    # goal exists to prevent.
    assert "#1762" not in SKILL, (
        "SKILL.md cites #1762 -- its own record documents a normal test-suite pass, not a "
        "mutation-testing revert/confirm-red cycle, so it must not be cited as an example of one "
        "(verified during Plan-Review: issue #1762's PRE-PR CODE-REVIEW comment says tests pass, "
        "never that anything was reverted to confirm a failure)"
    )


def test_mutation_testing_names_the_target_the_real_guard_caveat():
    # north-star Standing rule #1's own 2026-08-08 entry: "A mutation test aimed at a redundant
    # secondary lookup rather than the real guard. It passed against the mutant, which means it
    # proved nothing." Mutation testing done against the wrong line is worse than not doing it --
    # it looks like coverage while proving nothing.
    assert "redundant" in SKILL, (
        "SKILL.md's Mutation testing section does not warn against targeting a redundant secondary "
        "check instead of the real guard -- this repo has shipped that exact mistake before "
        "(north-star Standing rule #1's own 2026-08-08 entry)"
    )


def test_mutation_testing_points_at_north_star_standing_rule_one_specifically():
    # Standing rule #2 ("...untrusted by default") is ALREADY cited by the existing Security
    # defaults section (#1894) -- so a bare `"north-star" in SKILL and "Standing rule" in SKILL`
    # conjunction would pass today for a reason that has nothing to do with THIS section (flagged
    # by Plan-Review round 2). "is not evidence" is Standing rule #1's own real heading text ("A
    # claim in prose is not evidence. Execute it.") and is unique to it.
    assert "is not evidence" in SKILL, (
        "SKILL.md's Mutation testing section does not point at north-star Standing rule #1 "
        "specifically (checked via its own unique heading text, not the ambiguous bare "
        "'north-star'/'Standing rule' pair that Standing rule #2's existing citation already "
        "satisfies) -- the philosophical grounding for mutation testing stays undiscovered otherwise"
    )


def test_mutation_testing_points_at_agents_md_reliability_property():
    assert "RELIABILITY" in SKILL, (
        "SKILL.md's Mutation testing section does not point at AGENTS.md's RELIABILITY property -- "
        "the standing project-wide rule this section is a concrete instance of stays undiscovered "
        "otherwise"
    )


def test_mutation_testing_names_what_superpowers_already_has_without_overclaiming_a_total_gap():
    # Unlike #1894's security-defaults section (a genuine total gap in superpowers, safe to say so
    # outright), superpowers' own verification-before-completion/SKILL.md already has a narrower
    # form of mutation testing ("Revert fix -> Run (MUST FAIL) -> Restore -> Run (pass)"). Claiming
    # a total absence here would be false, and repeating -- inside the very file this goal is about
    # -- the citation-accuracy failure the goal exists to fix.
    assert "Revert fix" in SKILL and "Run (MUST FAIL)" in SKILL, (
        "SKILL.md's Mutation testing section does not name the real, narrower pattern "
        "superpowers:verification-before-completion already states -- omitting this would let the "
        "section imply a total gap on Claude Code that Research disproved"
    )


def test_red_step_has_the_vacuous_fixture_guard():
    # The issue's own ask: add a step to RED, not just GREEN -- after watching a test fail, name
    # the production change that would flip it green; if you can't, the fixture may be what's
    # broken. "flip it to green" is the anchor for the guard itself, distinct from anything already
    # in the RED step ("watch it fail for the right reason").
    assert "flip it to green" in SKILL, (
        "SKILL.md's RED step does not ask the implementer to name the production change that would "
        "flip the test to green -- without this, a fixture that trivially satisfies its own "
        "assertion (vacuous) can pass RED for a 'plausible' reason and never be caught"
    )


def test_red_step_cites_the_real_1829_vacuous_fixture_incident():
    # The real, verified incident (commit 834d0a82, part of PR #1838): an intermediate fixture
    # during #1829's own implementation drifted to total exactly `_BACKLOG_FETCH_CAP` (200),
    # making the widening-query assertion true but vacuous. Anchoring on `_BACKLOG_FETCH_CAP` (the
    # real constant name from the fix) proves the concrete example is present, not a vague gesture
    # at "vacuous tests happen sometimes."
    assert "_BACKLOG_FETCH_CAP" in SKILL, (
        "SKILL.md's RED step does not cite the real #1829/PR #1838 vacuous-fixture incident "
        "(test_a_member_beyond_the_window_is_still_reachable_by_its_label's fixture drifting to "
        "exactly _BACKLOG_FETCH_CAP) -- the vacuous-test guard would be asserted with no checkable "
        "example behind it"
    )
    assert "#1829" in SKILL and "#1838" in SKILL, (
        "SKILL.md's vacuous-fixture citation does not name both #1829 and PR #1838"
    )


def test_red_step_attributes_the_vacuous_fixture_fact_to_the_docstring_not_an_unverifiable_claim():
    # Plan-Review round 2's own non-blocking finding: the ONLY artifact evidencing the "independent
    # review caught it" claim is the corrected test's own docstring -- no separate review
    # comment/thread corroborates it. The shipped text must attribute the claim to what the
    # docstring itself records, not assert the review as an external fact beyond that.
    #
    # NOTE: a bare `"docstring" in SKILL` anchor would be VACUOUS today -- "Leave it readable"
    # already says "check the comments, docstrings and README" (verified: it does, before this
    # test file existed). This anchor caught itself failing to prove anything on the first RED run
    # of this exact test file; "docstring records" is the tighter, currently-absent phrase that
    # actually requires the new attribution to exist.
    assert "docstring records" in SKILL, (
        "SKILL.md's vacuous-fixture citation does not attribute the 'independent review caught it' "
        "claim to what the corrected test's own docstring records -- asserting it as an external "
        "fact beyond that would be exactly the unsourced-prose-claim problem this section exists "
        "to guard against, and a bare 'docstring' anchor is already vacuous today (see above)"
    )


def test_where_these_rules_come_from_section_exists_and_names_beck_and_fowler():
    # The issue's third gap: red-green-refactor and the refactor-smell names are stated as
    # self-evident axioms with no citation. Pin the real names -- the whole point is a checkable
    # citation, not just a heading gesturing at "sources exist somewhere."
    assert "Kent Beck" in SKILL, (
        "SKILL.md does not credit Kent Beck for the red-green-refactor / TDD discipline it "
        "prescribes as if self-evident"
    )
    assert "Martin Fowler" in SKILL, (
        "SKILL.md does not credit Martin Fowler for the refactor-smell vocabulary (Long Method, "
        "Feature Envy, etc.) it lists as if self-evident"
    )
    assert "Test-Driven Development: By Example" in SKILL
    assert "Refactoring: Improving the Design of Existing Code" in SKILL

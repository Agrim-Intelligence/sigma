"""sigma-research: the Research phase executor. Pins the things that make it worth having over the
prose line it replaced — a re-runnable blast-radius query, a lane sized from measured footprint, and
an artifact that does NOT sit where the plan gate will mistake it for a plan — plus its wiring into
both orchestrators and no leakage from the repo it was genericized from."""
import pathlib
import re
from skill_corpus import skill_corpus

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "skills" / "sigma-research" / "SKILL.md"


def _t():
    return RESEARCH.read_text()


def test_skill_exists_with_frontmatter():
    assert RESEARCH.exists()
    t = _t()
    assert "name: sigma-research" in t
    assert "description:" in t and "allowed-tools:" in t


def test_blast_radius_query_is_stored_for_rescan():
    """The coverage guarantee is re-running the query later, not trusting today's list. Without the
    stored query there is no re-scan, and a site that lands after research is caught by nothing."""
    t = _t()
    assert "re-run" in t.lower()
    assert "verbatim" in t.lower() or "exact command" in t.lower()


def test_dispositions_cover_the_unsure_case():
    t = _t().lower()
    for d in ("in-scope", "deferred", "out-of-scope", "verify"):
        assert d in t, f"blast-radius row has no '{d}' disposition"


def test_lane_is_sized_from_footprint_not_calendar_time():
    t = _t()
    for lane in ("small", "medium", "large"):
        assert f"**{lane}**" in t
    assert "lane: auto" in t                                   # closes sigma-init's dangling promise
    assert "structural footprint" in t.lower()
    assert "fabricate" in t.lower() or "invented duration" in t.lower()


def test_dossier_stays_out_of_the_plan_gate_directory():
    """plan_gate.sh unblocks source edits on ANY recent .md under .sdlc/plans/ — a research note
    landing there would silently satisfy the gate it is supposed to precede."""
    t = _t()
    assert ".sdlc/research/" in t
    assert ".sdlc/plans/" in t and "not a plan" in t.lower()


def test_defers_the_binding_alignment_verdict_to_plan_review():
    """Research warns early because it is cheap there; the gate that blocks is still plan-review's,
    and duplicating the verdict would give two authorities for one decision."""
    t = _t()
    assert "sigma-plan-review" in t
    assert "early warning" in t.lower()


def test_proportion_gate_allows_skipping():
    """Ceremony for a typo fix is the failure mode a Research phase invites. Skipping must be a
    stated, legitimate outcome — not something the agent has to justify."""
    t = _t().lower()
    assert "skipping it is a legitimate outcome" in t or "does **not**" in _t()


def test_read_only_and_evidence_bound():
    t = _t()
    assert "Read-only" in t or "read-only" in t
    assert "no fabrication" in t.lower()
    assert "file:line" in t


def test_wired_into_both_orchestrators_research_phase():
    for orch in ("sigma-goal", "sigma-loop"):
        t = skill_corpus(orch)   # #1611: SKILL.md + references/*.md
        assert "sigma-research" in t, f"{orch} does not run sigma-research"


def test_readme_no_longer_calls_research_skill_less():
    t = (ROOT / "README.md").read_text()
    assert "agent practice; no dedicated skill" not in t
    assert "`sigma-research`" in t


def test_no_source_repo_leakage():
    banned = ("docs/context", "Ported from", "OnShot", "onshot", "storytelling",
              "episode", "lipsync", "screenplay", "media-orch", "Temporal", "RunPod")
    t = _t()
    for b in banned:
        assert b not in t, f"sigma-research leaked '{b}'"


# --- #1929: the copyable skeleton did not render when copied -------------------
#
# `## Output` presents the dossier as a skeleton to COPY, and none of its three tables carried a
# `| --- |` delimiter row -- so a reader doing exactly what the skill says got runs of literal pipes
# on GitHub. Identical to #1927 finding 3 in the sibling `sigma-goal-design`, fixed one skill over in
# #1928. These pins are that skill's, ported: `test_sdlc_goal_design_skill.py` is the precedent, not
# the source -- this module has no `_section` helper, no module-level SKILL constant and did not
# import `re`, and this file's headings are named, not numbered.
#
# The class guard that stops a THIRD skill reintroducing it lives in `tests/test_docs.py`; what is
# pinned HERE is this skill's own schema, which a tree-wide sweep cannot see: which fence is the
# skeleton, that it is complete, and how many tables it must still have.

#: Both boundaries written out in full, deliberately. The obvious shortening of the end marker to
#: `"## Constraints"` matches `## Constraints & unknowns` INSIDE the fence: measured during plan
#: review, that silently truncates the extracted fence (losing `## Lane rationale`) while all three
#: tables still pass, and a separator-less fourth table added under the lost heading went
#: undetected. The completeness assert below is what makes that failure loud instead of green.
_SCHEMA_START = "## Output"
_SCHEMA_END = "## Constraints (non-negotiable)"
_SCHEMA_LAST_HEADING = "## Lane rationale"


def _schema_fence():
    t = _t()
    assert _SCHEMA_START in t and _SCHEMA_END in t, \
        "the Output section's own boundaries moved -- re-check these pins"
    section = t.split(_SCHEMA_START, 1)[1].split(_SCHEMA_END, 1)[0]
    assert "```markdown" in section, "the copyable skeleton is gone -- re-check these pins"
    fence = section.split("```markdown", 1)[1].split("```", 1)[0]
    assert _SCHEMA_LAST_HEADING in fence, (
        "the extracted fence does not reach %r -- the section boundaries are slicing it short, "
        "and every table after the cut is going unchecked" % _SCHEMA_LAST_HEADING)
    return fence


def test_every_table_in_the_schema_renders_when_copied():
    """The skeleton is copied literally. Without a delimiter row markdown renders a run of literal
    pipes rather than a table, on GitHub and everywhere else.

    The delimiter's CELL PATTERN is the property, not merely its width: `|  |  |` and `| : | : |`
    both have the right column count and neither renders, and both pass a check that only asks
    whether the characters between the pipes are drawn from `-` and `:`. Control: blanking a
    delimiter's cells is one of the mutations this test is required to go red on."""
    lines = [line.rstrip() for line in _schema_fence().splitlines()]
    tables = 0
    i = 0
    while i < len(lines):
        if not lines[i].startswith("|"):
            i += 1
            continue
        header = lines[i]
        tables += 1
        assert i + 1 < len(lines), "table header %r has no delimiter row" % header
        delimiter = lines[i + 1]
        assert delimiter.startswith("|"), "table header %r has no delimiter row" % header
        assert delimiter.count("|") == header.count("|"), \
            "delimiter column count does not match %r" % header
        cells = [c.strip() for c in delimiter.strip().strip("|").split("|")]
        assert all(re.fullmatch(r":?-+:?", c) for c in cells), \
            "the row under %r is not a valid delimiter row: %r" % (header, delimiter)
        while i < len(lines) and lines[i].startswith("|"):
            i += 1
    assert tables >= 3, \
        "expected the claim-verification, blast-radius and tech-debt tables, found %d" % tables


def test_every_cited_location_in_the_schema_admits_a_range():
    """`Site (file:line)` was singular; most real sites are ranges (`models.py:8-13`). So are most
    pieces of evidence, and this skeleton has two `Evidence` columns as well as the `Site` one --
    leaving those singular would ship the same defect into every dossier while the prose below the
    fence disclaimed it. The bare form surviving ANYWHERE in the fence is the failure."""
    fence = _schema_fence()
    site = [line for line in fence.splitlines() if "Site" in line]
    assert site, "the blast-radius table lost its Site column"
    assert "range" in site[0].lower(), "the Site column no longer admits a line range"
    assert "(file:line)" not in fence, \
        "a column still cites the singular `(file:line)`: " + \
        repr([l for l in fence.splitlines() if "(file:line)" in l])


def test_the_prose_says_why_the_delimiter_rows_are_there():
    """A skeleton is exactly the thing an editor tidies. The sibling states the reason next to it
    (`skills/sigma-goal-design/SKILL.md`) so the rows are not read as noise and stripped back out;
    without the reason on the page, the only thing standing between the fix and its regression is
    this test file, which an editor of the prose is not reading."""
    # Sliced from the Output SECTION, not from the file's first ```markdown fence: that fence is
    # the right one today only because it is the only one, and an earlier fence added later would
    # silently move this assertion onto unrelated prose.
    section = _t().split(_SCHEMA_START, 1)[1].split(_SCHEMA_END, 1)[0]
    after = section.split("```markdown", 1)[1].split("```", 1)[1]
    assert "not decoration" in after, "the delimiter rows' rationale is gone from the page"
    assert "line range" in after, "the range rule is gone from the page"

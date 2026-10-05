"""#1911-#1915: the operational contract of `sigma-goal-design` is pure SKILL.md prose, and an E2E
validation run on a live board found five ways it did not hold -- a frontmatter grant that cannot
perform the skill's own step 5, a stale handoff sentence that got a false statement published to a
real issue, an artifact whose consumer reads sections the producer was never told to write, no
guidance on how that artifact reaches a reader, and a `full` mode with no stopping rule.

The same reasoning `tests/test_sdlc_goal_review_skill.py` applies to its own sibling: every
primitive here is prose, so what is pinned is the CONTRACT -- structural claims, the grant shape,
the required headings, the ordering -- never the wording around them.

One test in here is CROSS-FILE on purpose. `sigma-goal-review` step 1 names the sections it reads
out of `.sdlc/design/<n>.md`; `sigma-goal-design` step 5 is what writes them. That coupling was
completely unpinned (#1914), which is how two runs could produce structurally different artifacts
with nothing failing. Pinning both ends is the only thing that makes it a contract rather than a
coincidence."""
import importlib.util
import pathlib
import re

from skill_corpus import skill_corpus

ROOT = pathlib.Path(__file__).resolve().parent.parent
_SKILL_DIR = ROOT / "skills" / "sigma-goal-design"
SKILL_PATH = _SKILL_DIR / "SKILL.md"
SKILL = SKILL_PATH.read_text(encoding="utf-8")
#: #2107 relocated step 2 and step 5 wholesale, verbatim, into their own reference files -- every
#: `_section(SKILL, "## 2. ...", "## 3.")` / `_section(SKILL, "## 5. ...", "## 6.")` extraction in
#: this file now reads the correct one of these two instead, since a bounded split on the (now
#: short-bodied) SKILL.md alone would only capture the pointer paragraph left behind in the body,
#: not the relocated content. Steps 1/3/4/6 stayed in the body unchanged, so tests scoped to those
#: sections still read `SKILL` directly.
MAPPING = (_SKILL_DIR / "references" / "mapping-the-codebase.md").read_text(encoding="utf-8")
ARTIFACT = (_SKILL_DIR / "references" / "writing-the-artifact.md").read_text(encoding="utf-8")
#: #2108 split sigma-goal-review into a body + references/*.md -- "the skill", not just the file.
REVIEW = skill_corpus("sigma-goal-review")
#: The one section of REVIEW a test here needs SCOPED (not just "somewhere in the corpus") --
#: #2108 moved sigma-goal-review's whole step 1 wholesale into this reference file, so a bounded
#: `_section(REVIEW, "## 1. Read the target", "## 2.")` on the (now short-bodied) corpus would
#: only capture the pointer paragraph left behind in the body, not the relocated content.
REVIEW_STEP1 = (ROOT / "skills" / "sigma-goal-review" / "references" /
                "reading-the-target.md").read_text(encoding="utf-8")
DESIGN_GOAL = (ROOT / "skills" / "sigma-loop" / "scripts" /
               "design_goal.py").read_text(encoding="utf-8")


def _mod(name, base):
    spec = importlib.util.spec_from_file_location(name, base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


#: The LIVE module #2032's `sweep-budget` verb documents -- loaded so the schema and Python numbers
#: are pinned to agree, the same reasoning `test_sdlc_goal_review_skill.py` applies to `compile_plan`.
goal_design_py = _mod("goal_design", ROOT / "skills" / "sigma-goal-design" / "scripts")

#: Everything `sigma-goal-review` step 1 says it reads back out of the artifact. Lower-cased because
#: one end is a heading and the other is a sentence. `premise check` and `out of scope` joined it
#: with #1976/#1975: a section the consumer never opens is one the producer can leave empty, which
#: is precisely the state both issues found.
#: `seeds` joined it with #2028: the sweep's own stopping rule is defined over the seed set, and
#: until the artifact carried one the only record of it was the pass's own memory -- so `converged`
#: and `capped` were both unfalsifiable claims about a set nobody could see.
CONSUMER_READS = ("premise check", "seeds", "blast radius", "components", "out of scope", "doubts",
                  "blockers", "detailed design", "slice-count estimate")


def _frontmatter():
    return SKILL.split("---", 2)[1]


def _section(text, start, end):
    return text.split(start, 1)[-1].split(end, 1)[0]


# --------------------------------------------------------------- #1911: the grant

def test_allowed_tools_permits_the_comment_step_5_mandates():
    # Step 5 mandates a plain comment on the Dossier path. `gh issue view *` cannot post one.
    assert "Bash(gh issue *)" in _frontmatter()


def test_allowed_tools_carries_no_per_verb_gh_entry():
    # The kit's own granularity is the NOUN (`Bash(gh issue *)` in sigma-define, sigma-promote,
    # sigma-scope, sigma-triage, sigma-unpark, sigma-retro, sigma-context, sigma-align, sigma-dossier).
    # A per-verb entry reads as a read-only posture this skill does not have and cannot enforce.
    assert "gh issue view *" not in _frontmatter()
    assert "gh issue comment *" not in _frontmatter()


def test_allowed_tools_permits_creating_the_new_directory():
    assert "Bash(mkdir *)" in _frontmatter()


def test_states_what_the_allowed_tools_list_is_and_is_not():
    # The honest posture: it is a pre-approval of the commands this file tells you to run, not a
    # sandbox -- `Bash(python3 *)` is already unrestricted execution, so a narrow entry beside it
    # is decoration. Pinned so the list is never re-narrowed as if it were a boundary.
    assert "Bash(python3 *)" in SKILL.split("---", 2)[2]     # discussed in the body, not just granted
    assert "not a sandbox" in SKILL


# --------------------------------------------------------------- #1912: the stale sentence

def test_handoff_does_not_claim_goal_review_is_unshipped():
    assert "Until `goal-review` ships" not in SKILL


def test_handoff_adopts_the_hedged_wording_design_goal_py_already_uses():
    # design_goal.py renders the same handoff into a filed meta-issue and hedges it correctly.
    # Two versions of one instruction is how the stale half got published to a real issue.
    hedge = "may not exist yet on your install"
    assert hedge in DESIGN_GOAL, "design_goal.py is the source of this wording"
    assert hedge in SKILL


# --------------------------------------------------------------- #1914: the artifact schema

def test_step_5_pins_the_artifact_headings():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    headings = {h.strip().lower() for h in re.findall(r"^## (.+)$", schema, re.M)}
    assert headings, "step 5 defines no artifact headings at all"


def test_every_section_goal_review_reads_is_a_required_heading_here():
    review_step1 = REVIEW_STEP1.lower()   # #2108: step 1 lives wholesale in its own reference file
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")   # #2107
    headings = {h.strip().lower() for h in re.findall(r"^## (.+)$", schema, re.M)}
    for section in CONSUMER_READS:
        assert section in review_step1, "goal-review no longer reads %r -- re-check this pin" % section
        assert section in headings, "goal-design's artifact schema omits %r" % section


def test_the_schema_is_named_as_a_contract_with_its_consumer():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert "goal-review" in schema


# --------------------------------------------------------------- #1913: how it reaches a reader

def test_step_5_does_not_assume_the_artifact_can_be_committed():
    # `.sdlc/` is gitignored wholesale in Sigma's own repo, while sigma-setup's RUNTIME_IGNORES
    # deliberately leaves `.sdlc/design/` trackable for an adopter. The guidance has to be measured,
    # not assumed -- and the measurement is the check-ignore call.
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert "git check-ignore" in schema


def test_step_5_forbids_the_direct_commit_the_validation_run_made():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert "sdlc/*" in schema and "AGENTS.md" in schema


def test_step_5_covers_the_path_that_has_no_branch_at_all():
    # Step 6's retrofit path skips work.py entirely, so there is no goal branch to carry the file.
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert "work.py" in schema


def test_step_5_gives_the_dossier_path_a_move_when_the_artifact_is_trackable():
    # The landing rule names ONE branchless case -- step 6's "Design #N" meta-issue. The Product
    # path is branchless too, for a different reason: a Dossier is `story`-labelled and never
    # `sdlc:goal` (`sigma-dossier` SKILL.md: "the loop must never try to execute a Dossier"), so no
    # `sdlc/*` worktree is ever cut for it and `work.py commit` returns "not started -- run
    # `work.py start` first (nothing committed)". Left unnamed, a repo that TRACKS `.sdlc/design/`
    # puts the Dossier path back in exactly the zero-guidance state #1913 filed -- forbidden from
    # the direct commit the validation run made, with nothing to do instead.
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    landing = schema.split("lands the way everything else lands", 1)
    assert len(landing) == 2, "the landing rule's own anchor moved -- re-check this pin"
    assert "Dossier path" in landing[1], "the landing rule never names the Product path"
    assert "work.py start" in landing[1], "the Dossier path is given no way to get a branch"


def test_the_comment_must_carry_substance_not_a_bare_path():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert "bare path" in schema or "bare link" in schema


# --------------------------------------------------------------- #2064: the human-readable brief

def test_the_in_brief_file_is_a_required_sibling_not_a_courtesy():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert "in-brief.md` is a required sibling" in schema, \
        "the in-brief companion is not stated as required"


def test_the_in_brief_rules_ban_the_main_artifacts_own_citation_register():
    """The whole point is that this file is held to DIFFERENT rules than the main artifact --
    if it can still carry BR-n/D-n/B-n ids or file:line citations, it is the same unreadable
    document with a different filename. Control: the real adopter artifact this was measured
    against used exactly these citation shapes throughout; their absence here is the fix, not an
    accident of phrasing."""
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    brief = schema.split("in-brief.md` is a required sibling", 1)[1]
    brief = brief.split("`Doubts` and `Blockers` stay two headings", 1)[0]
    assert "No `BR-n`" in brief or "no `BR-n`" in brief.lower() or "BR-n`" in brief, \
        "the in-brief rules never ban the citation ids"
    assert "plain-English gloss" in brief or "plain English" in brief, \
        "the in-brief rules never require glossing codenames"
    assert "One page" in brief or "one page" in brief, \
        "the in-brief rules never state a length discipline"


def test_the_in_brief_file_is_never_read_by_a_script():
    """The additive-only guarantee: goal-review and compile_plan.py must keep reading only the
    main artifact, or the brief stops being a courtesy and becomes a second source of truth
    nothing enforces agreement with."""
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    brief = schema.split("in-brief.md` is a required sibling", 1)[1]
    assert "never read by any script" in brief.lower() or \
           "never read by any script" in brief, \
        "the in-brief rules never state it is unparsed by any consumer"


def test_the_top_of_the_artifact_points_at_the_in_brief_file():
    """A pointer buried in the LAST section of a 14,000-word document is not a pointer a reader
    who gives up partway through will ever see (the defect #2064 measured). It must be near the
    top, before `## Intent`."""
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    template = schema.split("```markdown", 1)[1].split("```", 1)[0]
    before_intent = template.split("## Intent", 1)[0]
    assert "in-brief.md" in before_intent, \
        "the artifact template's own top has no pointer to the in-brief file"


def test_the_handoff_schema_also_points_at_the_in_brief_file():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    template = schema.split("```markdown", 1)[1].split("```", 1)[0]
    handoff = template.split("## Handoff", 1)[1]
    assert "in-brief.md" in handoff, \
        "the artifact template's own Handoff heading has no pointer to the in-brief file"


def test_the_comment_no_longer_mandates_reproducing_doubts_and_blockers_in_full():
    """The actual defect, pinned by its ABSENCE as a live instruction: the old rule told the
    author to carry 'every doubt and blocker in full', and Doubts/Blockers are written in the
    artifact's own file:line citation register -- so following that rule put the same unreadable
    prose into the one place most readers will ever look. The phrase itself may still appear
    QUOTED as the thing being retired (that is how this file explains every other reversal); what
    must be gone is the instruction to actually DO it -- the old bullet opened 'It stands on its
    own; never post a bare path.' immediately followed by 'Carry ... every doubt and blocker in
    full', with no negation in between. Control: a real published comment on a live adopter issue
    followed that old instruction verbatim, and it was unreadable."""
    comment = ARTIFACT.split("Then link it from the source issue's own comments", 1)[1]
    assert "Carry the substance a reader needs without\n  opening your working copy — the site " \
           "count, the components, every doubt and blocker in full" not in comment, \
        "the old live instruction to reproduce Doubts/Blockers verbatim is still present"
    assert re.search(r"[\"']every doubt and blocker in full[\"']", comment), \
        "the retired phrase should still be QUOTED as the thing being reversed, for the same " \
        "reason every other change in this file cites what was wrong before"


def test_the_comment_now_carries_the_in_brief_content_instead():
    comment = ARTIFACT.split("Then link it from the source issue's own comments", 1)[1]
    assert "in-brief.md" in comment.split("Measure whether the file can reach", 1)[0], \
        "the comment rule never says to carry the in-brief's own content"


def test_the_comment_links_both_files_rather_than_naming_a_bare_path():
    """Measured against a real published comment on a live adopter issue: it named
    `.sdlc/design/1.md` as inline code in its own first line -- a reader has to go find the file
    themselves rather than clicking through. The comment rule must say to use a real markdown
    link once the branch/local-only decision is made, not a bare filename in a code span."""
    comment = ARTIFACT.split("Then link it from the source issue's own comments", 1)[1]
    substance = comment.split("Measure whether the file can reach", 1)[0]
    assert "markdown LINKS" in substance or "markdown link" in substance.lower(), \
        "the comment rule never requires an actual clickable link to the file"
    assert "bare filename" in substance or "code span" in substance, \
        "the comment rule never bans naming the file as inline code instead of a link"


def test_a_capped_sweep_leads_the_in_briefs_own_first_line_too():
    """#1952's rule (capped sweep is the comment's FIRST line) has to apply to the in-brief file
    too, or a reader who opens ONLY that file -- the one this whole fix exists to make the
    reasonable thing to do -- is exactly the reader left thinking the mapping is complete."""
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    brief = schema.split("in-brief.md` is a required sibling", 1)[1]
    brief = brief.split("`Doubts` and `Blockers` stay two headings", 1)[0]
    assert "capped" in brief.lower() and "first line" in brief.lower(), \
        "the in-brief rules never require the capped-sweep statement to lead that file too"


# --------------------------------------------------------------- #1915: the stopping rule

def test_full_mode_has_an_explicit_stopping_rule():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "Stopping rule" in depth


def test_the_stopping_rule_carries_a_number_not_just_an_exhortation():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "Stopping rule" in depth          # or the split below silently measures the whole section
    stop = depth.split("Stopping rule", 1)[1]
    assert re.search(r"\b(one|two|three|four|\d+)\b", stop), "no bound stated"


def test_hitting_the_cap_with_work_left_is_recorded_not_silently_truncated():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "Stopping rule" in depth
    stop = depth.split("Stopping rule", 1)[1]
    assert "Doubts" in stop or "Blockers" in stop


# --------------------------------------------------------------- ordering, unchanged claims

def test_section_order_is_unchanged():
    for a, b in zip(("## 1. Read the target", "## 2. Map to the codebase",
                     "## 3. Blast radius", "## 4. Detailed design",
                     "## 5. Write the artifact"),
                    ("## 2. Map to the codebase", "## 3. Blast radius",
                     "## 4. Detailed design", "## 5. Write the artifact", "## 6. Handoff")):
        assert SKILL.index(a) < SKILL.index(b), "%s must precede %s" % (a, b)


def test_still_never_writes_sdlc_designed_itself():
    # The one invariant every one of these five fixes had to leave alone.
    assert "does **not** write `sdlc:designed`" in SKILL


# --------------------------------------------------------------- #1927: E2E run 2's residue

def _granted_prefixes():
    """`Bash(git *)` -> ('git',); `Bash(gh issue *)` -> ('gh', 'issue')."""
    prefixes = []
    for entry in re.findall(r"Bash\(([^)]+)\)", _frontmatter()):
        words = entry.replace("*", " ").split()
        if words:
            prefixes.append(tuple(words))
    return prefixes


def _schema_fence():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert "```markdown" in schema, "step 5's copyable skeleton is gone -- re-check these pins"
    return schema.split("```markdown", 1)[1].split("```", 1)[0]


# --- finding 1: the grant claim was still false after #1911 -------------------

def test_step_2_names_a_file_inventory_command_the_grant_covers():
    # Step 2 told a reader to sweep the codebase and named no command for it, so the run-2 designer
    # reached for `find`/`ls`/`wc`/`cat` -- none granted, and it was prompted on its FIRST command.
    # `git ls-files` does the same job under `Bash(git *)`, which is already in the list.
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "git ls-files" in depth
    assert ("git",) in _granted_prefixes()


def test_the_grant_note_names_the_utilities_it_deliberately_withholds():
    # The other way to make the claim true is to widen the list until it names every shell utility,
    # which grants everything and states nothing. Taking the narrow road means saying out loud
    # which commands are NOT there, so a later reader does not "fix" it by adding them.
    note = SKILL.split("What the `allowed-tools` list above is, and is not", 1)[1]
    note = note.split("## 1. Read the target", 1)[0]
    for utility in ("`find`", "`ls`", "`wc`", "`cat`"):
        assert utility in note, "%s is withheld but never named as withheld" % utility


def test_every_command_the_body_names_is_covered_by_the_grant():
    """The file's own rule, made mechanical -- and since #1957 it is one rule with one
    implementation, shared with the other two pipeline stages. `tests/test_pipeline_allowed_tools`
    owns the scan (and its own in-process control); this delegation keeps the pin discoverable from
    the skill's own suite, which is where the last two recurrences were found."""
    from test_pipeline_allowed_tools import _uncovered
    assert not _uncovered(SKILL), "commands named in the steps that allowed-tools does not grant"


# --- finding 2: §5's schema had no home for the statement §6 mandates ---------

def test_the_schema_has_a_heading_for_the_goal_review_presence_statement():
    # §6 requires stating IN THE ARTIFACT whether `goal-review` exists on this install; §5 says
    # "write exactly these, in this order, and put nothing the consumer needs outside them" and
    # listed no heading for it. Run 2's designer wrote `## Handoff`, then deleted it as a schema
    # violation and folded the note into the tail of `## Detailed design` -- both are deviations.
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    headings = {h.strip().lower() for h in re.findall(r"^## (.+)$", schema, re.M)}
    assert "handoff" in headings


def test_step_6_points_its_presence_statement_at_that_heading():
    handoff = SKILL.split("## 6. Handoff", 1)[1]
    anchor = "may not exist yet on your install"
    assert anchor in handoff, "the presence hedge moved -- re-check this pin"
    assert "## Handoff" in handoff.split(anchor, 1)[1][:400], \
        "§6 mandates the statement without saying which heading carries it"


# --- finding 3: the skeleton did not render when copied ----------------------

def test_every_table_in_the_schema_renders_when_copied():
    # The skeleton is presented as copyable. Without a `| --- |` separator row markdown renders a
    # run of literal pipes rather than a table, on GitHub and everywhere else.
    lines = [line.rstrip() for line in _schema_fence().splitlines()]
    tables = 0
    i = 0
    while i < len(lines):
        if not lines[i].startswith("|"):
            i += 1
            continue
        header = lines[i]
        tables += 1
        assert i + 1 < len(lines), "table header %r has no separator row" % header
        separator = lines[i + 1]
        assert separator.startswith("|"), "table header %r has no separator row" % header
        assert set(separator.replace("|", "").replace(" ", "")) <= {"-", ":"}, \
            "the row under %r is not a separator" % header
        assert separator.count("|") == header.count("|"), \
            "separator column count does not match %r" % header
        while i < len(lines) and lines[i].startswith("|"):
            i += 1
    assert tables >= 3, "expected the blast-radius, components and slice-count tables"


def test_the_blast_radius_site_column_admits_a_range():
    # `Site (file:line)` is singular; most real sites are ranges (`models.py:8-13`).
    site = [line for line in _schema_fence().splitlines() if "Site" in line]
    assert site, "the blast-radius table lost its Site column"
    assert "range" in site[0].lower()


# --- finding 4: check-ignore's exit code reads backwards if undocumented -----

def test_check_ignore_exit_code_semantics_are_stated_the_right_way_round():
    # Measured in this repo: `git check-ignore -v .sdlc/design/1927.md` -> exit 0, printing
    # `.gitignore:8:.sdlc/<TAB>.sdlc/design/1927.md`; `git check-ignore -v README.md` -> exit 1,
    # printing nothing. Step 5 hangs a publish decision on that call, and anything reading non-zero
    # as "the command failed" concludes the exact opposite of the truth.
    #
    # The DIRECTION is the whole finding, so it is what gets pinned. Control: swapping the two
    # numbers in SKILL.md -- i.e. reinstating the defect verbatim -- passed a pin that only asserted
    # both strings were present, so presence alone is decoration here.
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert "git check-ignore" in schema, "the measurement itself is gone -- re-check this pin"
    # Flatten: the claim wraps across lines and carries markdown emphasis in the middle of it.
    bullet = re.sub(r"\s+", " ", schema.split("git check-ignore", 1)[1][:900]).replace("*", "")
    assert re.search(r"exit 0[^.]{0,60}\bis ignored\b", bullet, re.I), \
        "exit 0 is not tied to the path BEING ignored"
    assert re.search(r"exit 1[^.]{0,60}\bnot ignored\b", bullet, re.I), \
        "exit 1 is not tied to the path NOT being ignored"


# --- finding 5: "no new in-scope site" had two readings ----------------------

def test_the_quiet_round_says_what_counts_as_a_new_site():
    # Run 2's round 2 re-hit files already in the blast radius through different queries. Read as
    # "a file/component not already listed" the sweep terminated at round 2; read as "any new grep
    # line" it would have forced a pointless round 3. Only the first reading terminates at all on a
    # codebase whose files answer to more than one query, so it is the one that is written down.
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "A quiet round" in depth, "the stopping rule's own anchor moved -- re-check this pin"
    quiet = depth.split("A quiet round", 1)[1].split("**The budget", 1)[0]
    assert "file or component" in quiet and "not already" in quiet, \
        "the quiet round does not define what makes a site new"


# ============ #1956: `Depends on` carries slice ids, and a Blocker dependency has somewhere to go
# A real pass wrote `3, and B-1` in that column -- a slice edge AND a Blockers id. The column is
# machine-read (goal-review 4b turns each cell into `compile_plan.py` `blocked_by` keys, which name
# SIBLING ISSUES IN THE SAME PLAN and nothing else), so `B-1` had nothing to resolve to and was
# silently dropped by hand between the two stages. The producer end is where the grammar belongs.

import importlib.util as _ilu   # noqa: E402 -- placed with the section it serves


def _live_compile_plan():
    spec = _ilu.spec_from_file_location(
        "compile_plan", ROOT / "skills" / "sigma-scope" / "scripts" / "compile_plan.py")
    m = _ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_DEPENDS_ON_MARKER = "**`Depends on` carries slice ids from this same table, and nothing else"


def _depends_on_rule():
    """The rule bullet, read out of the prose. `str.split` on a missing separator returns the WHOLE
    text, which would silently widen every assertion below to the entire schema section -- the
    failure `tests/test_sdlc_goal_review_skill.py::_pointer_bullet` records having shipped once. So
    the marker's presence is asserted, not assumed."""
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert _DEPENDS_ON_MARKER in schema, "the `Depends on` grammar rule is gone from the schema"
    return schema.split(_DEPENDS_ON_MARKER, 1)[1].split("**Then link it", 1)[0]


def test_the_depends_on_column_is_pinned_to_slice_ids():
    rule = _depends_on_rule()
    assert "slice" in rule.lower()
    # The em-dash empty cell is part of the grammar: "no edges" has to be writable.
    assert "—" in rule, "the rule does not say how an empty cell is written"


def test_the_rule_names_the_non_slice_id_shapes_it_forbids():
    rule = _depends_on_rule()
    for heading in ("Blockers", "Doubts", "Blast radius", "Seeds"):
        assert heading in rule, f"the rule does not name the {heading} rows it excludes"


def test_control_every_forbidden_id_shape_the_rule_names_is_one_the_compiler_recognises():
    """CROSS-FILE, and the point of writing the rule this way. The ids are read OUT OF THE PROSE and
    run through the LIVE `compile_plan._DESIGN_ARTIFACT_ID_RE`: adding an id prefix to this schema
    without teaching the compiler leaves the refusal reading as an ordinary typo, and this fails."""
    ids = [tok for tok in re.findall(r"`([^`]+)`", _depends_on_rule())
           if re.match(r"^[A-Za-z]{1,2}-\d+$", tok)]
    assert len(ids) >= 3, f"could not read the forbidden id shapes out of the rule: {ids!r}"
    live = _live_compile_plan()._DESIGN_ARTIFACT_ID_RE
    for bad in ids:
        assert live.match(bad), f"the schema forbids {bad!r} but the compiler does not recognise it"


def test_the_rule_says_why_a_blocker_id_cannot_be_a_blocked_by_key():
    """Not "because we say so": `blocked_by` keys name siblings in the same plan, and a design
    Blocker has no ticket for a key to name. A rule without its reason gets edited away."""
    rule = _depends_on_rule()
    assert "blocked_by" in rule
    assert "same plan" in rule


def test_the_rule_redirects_a_blocker_dependency_rather_than_only_forbidding_it():
    """The half the issue asked for by name -- "say where a blocker dependency goes instead". The
    edge is written the other way round, in the Blockers entry, naming the slice it gates: the exact
    form `goal-review` §2a already adjudicates, so it is carried rather than dropped."""
    # Flattened: the phrase wraps mid-sentence in both files, so pinning it across a line break
    # would fail on a reflow that changed nothing.
    flat = re.sub(r"\s+", " ", _depends_on_rule())
    assert "Blockers" in flat and "not here" in flat, "the redirect's destination is gone"
    assert "name the slice that cannot start" in flat, \
        "the redirect does not use §2a's own operational test, so the two ends can drift"
    assert "name the slice that cannot start" in re.sub(r"\s+", " ", REVIEW), \
        "goal-review no longer carries the phrasing this redirect points at"


def test_the_rule_cites_the_real_run_that_produced_it():
    assert "3, and B-1" in _depends_on_rule(), \
        "the observed cell is gone; without it the rule reads as a hypothetical"


# --------------------------------------------------------------- #1952: under-sweeping is silent

def _header_line():
    """The artifact skeleton's own second line -- the one-line header every design carries."""
    for line in _schema_fence().splitlines():
        if line.startswith("**Target**"):
            return line
    raise AssertionError("the artifact skeleton lost its **Target** header line")


def test_the_header_line_carries_a_sweep_field_with_both_outcomes():
    """The measured failure (#1952): a run ended on the three-round cap, seed closure was never
    reached, and the only record of it was one bullet at the bottom of a 31KB document. The header
    is the one part of the artifact a skimmer cannot miss."""
    header = _header_line()
    assert "**Sweep**" in header, "the header line does not carry the sweep outcome"
    assert "converged" in header and "capped" in header, \
        "the sweep field does not name both outcomes"


def test_the_sweep_field_is_mandatory_for_the_reason_the_zero_count_is():
    # `goal-review`'s own `(0 open questions carried)` exists because an omitted count is
    # indistinguishable from a review that never adjudicated. Same argument, same conclusion.
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    rule = schema.split("**`Sweep`", 1)
    assert len(rule) == 2, "the schema states no rule about the Sweep field"
    assert "indistinguishable" in rule[1][:900], \
        "the schema does not say why omitting the field is not an option"


def test_a_capped_sweep_gets_a_banner_above_the_first_heading():
    """'at the TOP of the artifact, not buried as one Doubt bullet' is the whole ask. Pinned
    positionally: the banner has to sit before `## Intent` in the skeleton itself."""
    fence = _schema_fence()
    banner = [i for i, line in enumerate(fence.splitlines()) if line.startswith(">")]
    assert banner, "the skeleton carries no capped-sweep banner"
    intent = [i for i, line in enumerate(fence.splitlines()) if line.startswith("## Intent")]
    assert intent and banner[0] < intent[0], "the banner is not above the first heading"


def test_the_stopping_rule_routes_a_capped_sweep_to_the_header_not_only_to_doubts():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    stop = depth.split("The budget is three sweep rounds", 1)[-1]
    assert "Sweep" in stop, "hitting the cap is still recorded only under Doubts"
    assert "seed closure was not reached" in stop.lower(), \
        "the stopping rule does not say what ending on the cap MEANS"


def test_the_capped_statement_leads_the_handoff_comment():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    comment = schema.split("Then link it from the source issue's own comments", 1)
    assert len(comment) == 2, "the comment instruction's own anchor moved -- re-check this pin"
    assert "FIRST line" in comment[1], "a capped sweep is not first in the comment"


# --------------------------------------------------------------- #1955: ids, and a long table

def test_doubts_and_blockers_carry_ids_in_the_skeleton():
    """`goal-review` 2a has to bucket EVERY doubt and blocker in writing and had nothing to name
    them by; the live run invented D-1..D-8 and B-1 itself, which two runs would do differently."""
    fence = _schema_fence()
    doubts = fence.split("## Doubts", 1)[-1].split("## Blockers", 1)[0]
    blockers = fence.split("## Blockers", 1)[-1].split("## Detailed design", 1)[0]
    assert "D-1" in doubts, "the Doubts skeleton pins no id"
    assert "B-1" in blockers, "the Blockers skeleton pins no id"


def test_the_id_forms_the_schema_mandates_are_the_ones_compile_plan_recognises():
    """CROSS-FILE control. `compile_plan._DESIGN_ARTIFACT_ID_RE` (#1956) already refuses a
    `blocked_by` key shaped like one of these ids, quoting where the reference belongs. If the
    schema minted a different shape, that refusal would stop recognising the document it names."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "compile_plan", ROOT / "skills" / "sigma-scope" / "scripts" / "compile_plan.py")
    compile_plan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compile_plan)
    for ident in ("BR-1", "D-1", "B-1"):
        assert compile_plan._DESIGN_ARTIFACT_ID_RE.match(ident), \
            "%s is mandated by the schema but unknown to compile_plan" % ident
    assert not compile_plan._DESIGN_ARTIFACT_ID_RE.match("D1"), \
        "the live regex would accept an unhyphenated id -- re-check this pin"


def test_ids_are_never_renumbered():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert "never renumber" in schema.lower(), \
        "nothing stops a later pass renumbering ids other citations already point at"


def test_the_blast_radius_table_carries_a_component_column():
    """33 rows over 4 columns, roughly 90 lines, with no grouping affordance at all -- the only
    lever was the Components table above it.

    Scoped to the `## Blast radius` section since #2028. It read the fence's FIRST `| ID |` line,
    which was the blast radius only because no other table in the skeleton had an `ID` column; the
    Seeds table now does, and an unscoped version of this pin fails against a perfectly correct
    schema. Seen red exactly that way before the scoping was added."""
    section = _schema_fence().split("## Blast radius", 1)[-1].split("## Components", 1)[0]
    header = [line for line in section.splitlines() if line.startswith("| ID |")]
    assert header, "the blast-radius table lost its ID column"
    assert "Component" in header[0], "blast-radius rows do not say which component they belong to"


def test_the_long_table_grouping_affordance_carries_a_number():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    grouping = schema.split("may additionally split the table", 1)
    assert len(grouping) == 2, "the schema grants no grouping affordance for a long table"
    assert re.search(r"\b\d+\b", grouping[0][-300:]), "no row count is stated for when to group"
    assert "###" in grouping[1][:400], "grouping is granted without naming the sub-heading level"


def test_the_sub_headings_are_named_as_the_exception_to_write_exactly_these():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert "write exactly these" in schema
    assert "exception" in schema.split("may additionally split the table", 1)[-1][:700].lower(), \
        "sub-headings contradict 'write exactly these' without saying they are the exception"


# --------------------------------------------------------------- #1958: mode on the Product path

def test_step_2_says_who_resolves_mode_on_each_path():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "Product" in depth and "retrofit" in depth, "step 2 does not separate the two paths"
    assert "design-check" in depth, "step 2 never mentions the gate a reader would ask"


def test_step_2_says_design_check_is_not_the_reader_and_why():
    """`design_check` returns OFF at its first line whenever `goal_design.enabled` is not True, and
    resolves `mode` only after deciding to file a retrofit meta-issue -- so an agent verifying its
    own depth through it learns nothing."""
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "`OFF`" in depth, "step 2 does not name what design-check actually returns"
    assert "learns nothing" in depth or "tells you nothing" in depth


def test_step_2_gives_the_product_path_a_real_read_of_the_configured_value():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "goal_design" in depth and "load_config" in depth, \
        "the Product path is still told to read mode as prose only"


def test_the_precedence_step_2_states_is_the_live_one():
    """CROSS-FILE. `loop.py` resolves `gdz.get("mode") or "full"` then falls back to `"lane"` on
    anything outside `_DESIGN_MODES`. A skill stating a different precedence would have the two
    paths running at different depths off one config value."""
    loop = (ROOT / "skills" / "sigma-loop" / "scripts" / "loop.py").read_text(encoding="utf-8")
    assert '_DESIGN_MODES = ("full", "lane")' in loop, "the live mode set moved -- re-check this pin"
    assert 'gdz.get("mode") or "full"' in loop, "the live absent-default moved -- re-check this pin"
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    resolution = depth.split("read the configured value yourself", 1)
    assert len(resolution) == 2, "step 2 states no precedence for the Product path"
    assert "'full'" in resolution[1][:600] and "'lane'" in resolution[1][:600]


def test_enabled_is_stated_not_to_gate_the_depth_read():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "does **not** gate" in depth or "does not gate" in depth, \
        "nothing says whether goal_design.enabled gates the Product path's own depth"


# --------------------------------------------------------------- #1954: a proportionate light path

def test_the_target_is_sized_on_both_modes_not_only_on_lane():
    """The measured defect: `mode` is repo config, so a `--quiet` flag on a `full`-configured repo
    bought the treatment a six-component schema change gets -- 33 blast-radius rows, 24 queries, a
    31KB artifact. `sigma-goal`'s own Lane routing already scales ceremony to measured size; this is
    that, one stage up. Pinned as "both modes", because sizing only under `lane` is the state that
    produced the defect."""
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    sizing = depth.split("Size the target", 1)
    assert len(sizing) == 2, "step 2 never tells the pass to size its target"
    head = re.sub(r"\s+", " ", sizing[1][:400])
    assert "BOTH modes" in head, "sizing is not stated to apply on both modes"
    assert "LANES" in head, "sizing does not reuse discovery.py's own lane vocabulary"


def test_mode_is_stated_as_a_ceiling_not_a_floor():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    flat = re.sub(r"\s+", " ", depth).replace("*", "")
    assert re.search(r"mode` is a CEILING[^.]{0,60}never a floor", flat), \
        "step 2 does not say which direction `mode` bounds the pass in"


def test_step_2_does_not_claim_discovery_py_can_size_a_dossier():
    """CROSS-FILE. `lane_of` reads a goal FILE's frontmatter and says so itself: 'Passing an issue
    number here returns DEFAULT_LANE -- safe, but it is not a lane lookup'. A Dossier IS an issue
    number, so a skill that told the pass to shell out for its lane would get `medium` every time
    and never know."""
    discovery = (ROOT / "skills" / "sigma-loop" / "scripts" /
                 "discovery.py").read_text(encoding="utf-8")
    assert "it is not a lane lookup" in discovery, "the live caveat moved -- re-check this pin"
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    flat = re.sub(r"\s+", " ", depth)
    assert "will not answer this for you" in flat, \
        "step 2 does not warn that discovery.py cannot size an issue number"


def test_the_light_path_keeps_every_heading():
    """The one way a light path could break the contract: dropping sections. `- None.` under Doubts
    is already a real answer here, and a short section is not a missing one."""
    design = _section(SKILL, "## 4. Detailed design", "## 5. Write the artifact")
    light = design.split("The light path", 1)
    assert len(light) == 2, "§4 offers no light path at all"
    flat = re.sub(r"\s+", " ", light[1]).replace("*", "")
    assert "Every heading is still written" in flat, \
        "the light path does not protect §5's schema"


def test_the_light_path_names_its_landing_and_the_arithmetic():
    """A light path that produced nothing pickable would be worse than the heavy one. It has to say
    what Stage 2 does with a one-slice design, and the ticket count is the number a first-time
    reader is actually weighing."""
    design = _section(SKILL, "## 4. Detailed design", "## 5. Write the artifact")
    light = design.split("The light path", 1)[1]
    flat = re.sub(r"\s+", " ", light).replace("*", "")
    assert "goal-review" in flat, "the light path never names its consumer"
    assert "no** Epic" in light or "no Epic" in flat, "the light path does not say the Epic is skipped"
    assert re.search(r"one\b[^.]{0,40}`sdlc:goal` ticket", flat), \
        "the light path does not say a pickable ticket is still produced"
    assert "2 tickets, not 7" in flat, "the light path states no arithmetic to weigh"


def test_the_light_path_matches_what_compile_plan_actually_does():
    """CROSS-FILE. The 'no Epic' half is not a judgement call -- `compile_plan` creates the wrapper
    only for a plan carrying more than one issue. If that condition ever changed, a one-slice design
    would silently start producing an Epic and this claim would be false."""
    compile_plan = (ROOT / "skills" / "sigma-scope" / "scripts" /
                    "compile_plan.py").read_text(encoding="utf-8")
    assert "if epic_data and len(items) > 1:" in compile_plan, \
        "the live epic-or-not condition moved -- re-check this pin"
    design = _section(SKILL, "## 4. Detailed design", "## 5. Write the artifact")
    assert "compile_plan.py" in design.split("The light path", 1)[1], \
        "the light path asserts 'no Epic' without citing the code that decides it"


def test_the_header_line_carries_the_measured_lane():
    """Same argument as `Sweep` (#1952): an unrecorded measurement cannot be told from an absent
    one. A one-page design over a six-component change and a three-page design over a one-file
    change are both failures, and only the lane separates them."""
    header = _header_line()
    assert "**Lane**" in header, "the header line does not carry the measured lane"
    for value in ("small", "medium", "large"):
        assert value in header, "the Lane field does not name the %r lane" % value


def test_the_lane_field_is_mandatory_and_records_what_ran():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    rule = schema.split("**`Lane`", 1)
    assert len(rule) == 2, "the schema states no rule about the Lane field"
    flat = re.sub(r"\s+", " ", rule[1][:900]).replace("*", "")
    assert "written on every artifact" in flat, "the Lane field is not mandatory"
    assert "the pass actually ran at" in flat, \
        "the Lane field may record an intention rather than what happened"


# --------------------------------------------------------------- #1953: who can read the artifact

def test_step_5_no_longer_claims_the_artifact_is_gitignored_on_most_repos():
    """It was never true and is now false twice over: `setup.RUNTIME_IGNORES` omits `.sdlc/design/`
    deliberately, so every `/sigma-setup` repo tracks it, and Sigma's own repo tracks it too
    (#1953). The residual case -- a repo with its own blanket rule -- is what the check-ignore
    measurement is for, which is why the measurement stays and the claim goes."""
    assert "gitignored on most repos" not in SKILL, \
        "the skill still asserts the artifact is unreadable on most repos"


def test_step_5_states_the_asymmetry_an_adopter_will_meet():
    """`RUNTIME_IGNORES` and this repo's own history disagreed for months, and the docs demonstrating
    the pipeline are written against this repo. Wherever adoption is described, the difference has
    to be stated rather than left for an adopter to discover."""
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    bullet = re.sub(r"\s+", " ", schema.split("Measure whether the file", 1)[1][:900])
    assert "RUNTIME_IGNORES" in bullet, "the adopter default is not named"
    assert "1953" in bullet, "the change of fact is not traceable to the issue that made it"
    # The claim, not merely the word: a reader has to be told their repo may behave differently
    # from the one every document here demonstrates on. Control: deleting this sentence while
    # leaving the rest of the bullet intact passed an earlier version of this pin that asserted
    # only that "blanket" appeared somewhere in the bullet -- it appears twice.
    assert re.search(r"adopter'?s experience can differ", bullet), \
        "the bullet never says an adopter's own repo may behave differently"


# ============ #1975: the converged verdict is auditable, or it is honour-system
# #1952 made a capped sweep loud in four places and that worked -- but it fixed the REPORTING and
# not the JUDGEMENT beneath it. The stopping rule turns on "no new **in-scope** site" and nothing
# said who decides in-scope or how anyone could check the call. The re-run's own pass wrote it
# down: "I could have kept round 3 quiet by ruling `hooks/session_start.sh` out of scope. Nothing
# checks that call." So a pass that wants a clean result can narrow its own scope judgement after
# the fact and the artifact says `converged` with an honest one's authority.
#
# What is pinned is the MECHANISM, not the wording: a place for the exclusion, the round it was
# made in, the test it was judged against, the tie back into the stopping rule, and -- because
# overclaiming is the failure mode this whole class keeps producing -- the honest statement of what
# it does not buy.

_EXCLUSION_MARKER = "**Every hit is accounted for"


def _exclusion_rule():
    """The §2 block, read out of the prose. Asserted rather than assumed for the reason
    `_depends_on_rule` records: `str.split` on a missing separator returns the WHOLE text, which
    silently widens every assertion below to the entire section."""
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert _EXCLUSION_MARKER in depth, "the §2 exclusion rule is gone"
    return depth.split(_EXCLUSION_MARKER, 1)[1].split("**Stopping rule", 1)[0]


def test_the_schema_has_a_heading_for_what_was_ruled_out_of_scope():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    headings = {h.strip().lower() for h in re.findall(r"^## (.+)$", schema, re.M)}
    assert "out of scope" in headings, \
        "an exclusion still has nowhere to go, so the scope call leaves no trace"


def test_the_quiet_round_is_judged_over_the_exclusions_as_written():
    """The tie-in is the whole fix. A place to write exclusions that the stopping rule does not
    consult is a section a pass can leave empty and still declare `converged`."""
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "A quiet round" in depth, "the stopping rule's own anchor moved -- re-check this pin"
    quiet = re.sub(r"\s+", " ", depth.split("A quiet round", 1)[1].split("**The budget", 1)[0])
    assert "AS WRITTEN" in quiet, "the quiet round is still judged on an unrecorded scope call"
    assert "X-n" in quiet, "the quiet round does not name what the exclusions are written as"


def test_an_exclusion_carries_the_round_it_was_made_in():
    """The load-bearing field. #1975's scenario is a pass quieting its FINAL round by excluding one
    file; without the round number a reader cannot tell that exclusion from any other."""
    flat = re.sub(r"\s+", " ", _exclusion_rule())
    assert "the round it was excluded in" in flat, "an exclusion does not record its round"
    fence = _schema_fence()
    out_of_scope = fence.split("## Out of scope", 1)[-1].split("## Doubts", 1)[0]
    assert "round" in out_of_scope, "the skeleton's own entry shape drops the round"
    assert "X-1" in out_of_scope, "the Out of scope skeleton pins no id"


def test_the_rule_states_the_in_scope_test_the_exclusion_is_judged_against():
    """"in-scope" appeared twice in this file and was defined nowhere, which is the defect. A
    place to record a judgement made against an unstated test is not an audit."""
    flat = re.sub(r"\s+", " ", _exclusion_rule()).replace("*", "")
    assert re.search(r"IN scope when[^.]{0,200}would have to touch it", flat), \
        "the rule never says what makes a hit in scope"
    assert "would break if that change shipped" in flat, \
        "the in-scope test states only the touch half, not the breakage half"


def test_the_rule_rules_out_the_answer_the_issue_ruled_out():
    """#1975 names the wrong fix explicitly: "adding a second self-assessed confidence number would
    import the same problem again". Written down so a later pass does not re-propose it."""
    flat = re.sub(r"\s+", " ", _exclusion_rule())
    assert "not a second self-assessed number" in flat, \
        "nothing stops the next revision answering this with a confidence score"


def test_the_rule_does_not_overclaim_what_making_the_call_visible_buys():
    """RELIABILITY, and the specific way this class of fix goes wrong: #1952's own header field was
    described as if it settled the question it only reported on. Visible is not correct, nothing
    executes this, and a hit the pass never surfaced is still not caught."""
    flat = re.sub(r"\s+", " ", _exclusion_rule()).replace("*", "")
    assert "visible and disputable" in flat, "the rule does not say what it actually achieves"
    assert "does not make it right" in flat, "the rule reads as if recording a call validated it"
    assert "nothing in this kit executes the check" in flat, \
        "the rule implies an enforcement that does not exist"
    assert "cannot catch a hit the pass never surfaced" in flat, \
        "the rule does not name the coverage limit it leaves open"


def test_the_audit_is_anchored_to_the_queries_already_recorded():
    """The one part that does not rest on trusting the pass's own account: `## Queries run` is
    verbatim, so blast radius union the exclusions is checkable by re-running a query. Without this
    the section is another self-report."""
    flat = re.sub(r"\s+", " ", _exclusion_rule())
    assert "## Queries run" in flat, "the exclusions are not tied to the recorded queries"
    assert "re-running" in flat, "nothing says how a reviewer would check the accounting"


def test_the_accounting_is_bounded_so_a_broad_query_leaves_it_writable():
    """SCALABILITY, at the scale this actually meets: `git ls-files` over one private package returns 827
    files in this repo. A rule demanding one entry each is a rule nobody follows."""
    flat = re.sub(r"\s+", " ", _exclusion_rule())
    assert "group" in flat.lower(), "no grouping affordance, so a broad query makes this unwritable"
    # Pinned on the grouping clause itself, not on the bare word: this block spells "accounted for"
    # and "the accounting is over search queries", so a bare `"count" in flat` is satisfied by
    # either of those. Control: with `with a count` deleted from the grouping bullet, the bare
    # version stayed GREEN against the very defect its own message names.
    assert re.search(r"a group[^.]{0,60}with a count", flat), \
        "a group may be written without saying how many files it covers"
    assert "git ls-files" in flat, \
        "the rule does not separate an inventory query from a search query"


def test_out_of_scope_is_produced_explicitly_alongside_the_other_four():
    """§3 is the "produce, explicitly" list every consumer reads; a heading in §5's schema with no
    entry there is one a pass meets for the first time when it is already writing the file."""
    step3 = _section(SKILL, "## 3. Blast radius", "## 4. Detailed design")
    assert "**Out of scope**" in step3, "§3 never tells the pass to produce the exclusions"
    assert "X-1" in step3, "§3's numbering rule does not cover the exclusions"


def test_the_schema_states_the_rule_behind_the_out_of_scope_heading():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    rule = schema.split("**`Out of scope` is what makes", 1)
    assert len(rule) == 2, "the schema states no rule about the Out of scope heading"
    flat = re.sub(r"\s+", " ", rule[1][:1400]).replace("*", "")
    assert "goal-review" in flat, "the section names no consumer, so nothing has to read it"
    assert "REJECT" in flat, "an exclusion the reviewer rejects has no stated consequence"


# ============ #1976: a source claim that did not survive contact with the code has a slot
# §1 has always said to treat every claim in the source as a hypothesis to verify, and on the
# re-run it fired -- the Dossier claimed `/sigma-doctor` "stops dead at the repo boundary" and the
# sweep found it crosses in six places. But the pinned schema had no heading for the verdict and §5
# says to put nothing the consumer needs outside the headings, so the correction went into `Intent`
# as prose. `goal-review` adjudicates Doubts and Blockers BY ID; a falsified premise is neither, so
# the one stage positioned to notice that the framing moved could not see it.

def test_the_schema_has_a_heading_for_the_verdict_on_each_source_claim():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    headings = {h.strip().lower() for h in re.findall(r"^## (.+)$", schema, re.M)}
    assert "premise check" in headings, \
        "a falsified source claim still has nowhere to go but Intent prose"


def test_step_1_mandates_recording_the_verdict_not_only_verifying_the_claim():
    """The hypothesis sentence was already there and was already obeyed. Verifying and RECORDING
    are two acts and only the first was ever asked for."""
    step1 = _section(SKILL, "## 1. Read the target", "## 2. Map to the codebase")
    assert "hypothesis to verify" in step1, "the hypothesis rule moved -- re-check this pin"
    assert "## Premise check" in step1, "§1 verifies claims and still records nothing"


def test_the_premise_check_carries_exactly_three_verdicts():
    step1 = _section(SKILL, "## 1. Read the target", "## 2. Map to the codebase")
    flat = re.sub(r"\s+", " ", step1)
    for verdict in ("verified", "falsified", "unverifiable"):
        assert verdict in flat, "the premise check has no %r verdict" % verdict
    assert "evidence" in flat, "a verdict may be recorded with nothing behind it"
    fence = _schema_fence()
    premise = fence.split("## Premise check", 1)[-1].split("## Queries run", 1)[0]
    assert "PC-1" in premise, "the Premise check skeleton pins no id"
    for verdict in ("verified", "falsified", "unverifiable"):
        assert verdict in premise, "the skeleton omits the %r verdict" % verdict


def test_the_rule_says_which_claims_are_material_so_the_section_is_not_unbounded():
    """Without a test for materiality this is either every sentence in the Dossier or whichever
    ones the pass felt like checking -- the same unverifiable judgement #1975 is about."""
    step1 = _section(SKILL, "## 1. Read the target", "## 2. Map to the codebase")
    flat = re.sub(r"\s+", " ", step1).replace("*", "")
    assert "material" in flat, "nothing bounds which claims get a row"
    assert "built differently if it were false" in flat, \
        "materiality is asserted without an operational test"
    assert "value, priority or urgency is not" in flat, \
        "the rule does not say which claims are OUT of the premise check"


def test_a_falsified_premise_is_distinguished_from_a_doubt_and_from_a_blocker():
    """The reason it needs a heading of its own rather than a `D-n`. A Doubt is open and a Blocker
    is unresolved; a falsified premise is settled and different, and filing it as either asks the
    reviewer to re-decide something already decided or stops work nothing is blocking."""
    step1 = _section(SKILL, "## 1. Read the target", "## 2. Map to the codebase")
    flat = re.sub(r"\s+", " ", step1).replace("*", "")
    assert "not a Doubt and not a Blocker" in flat, \
        "nothing says why this is not simply another Doubt"
    assert "settled and different" in flat, "the distinction is asserted without being drawn"


def test_a_falsified_premise_obliges_intent_and_the_slices_not_just_a_note():
    """The issue's own consequence: "if the Dossier's problem statement is wrong, every slice
    derived from it inherits the error". A row that changes nothing downstream is a footnote."""
    step1 = _section(SKILL, "## 1. Read the target", "## 2. Map to the codebase")
    flat = re.sub(r"\s+", " ", step1).replace("*", "")
    assert "corrected" in flat, "a falsified premise obliges no correction anywhere"
    assert "## Intent" in flat, "the correction is not routed into the artifact's own Intent"
    assert "inherits the error" in flat, \
        "the reason the slices must be rebuilt rather than annotated is not stated"
    fence = _schema_fence()
    intent = fence.split("## Intent", 1)[-1].split("## Premise check", 1)[0]
    assert "falsified" in intent, \
        "the skeleton's Intent does not carry the correction, so the two can disagree"


def test_the_premise_check_cites_the_run_that_produced_it():
    step1 = _section(SKILL, "## 1. Read the target", "## 2. Map to the codebase")
    flat = re.sub(r"\s+", " ", step1)
    assert "/sigma-doctor" in flat and "six places" in flat, \
        "the measured case is gone; without it the rule reads as a hypothetical"


def test_the_schema_states_the_rule_behind_the_premise_check_heading():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    rule = schema.split("**`Premise check` is where", 1)
    assert len(rule) == 2, "the schema states no rule about the Premise check heading"
    flat = re.sub(r"\s+", " ", rule[1][:1400]).replace("*", "")
    assert "by id" in flat, \
        "the rule does not say why prose in Intent was invisible to the adjudication"


# --------------------------------------------------------------- both: ids the compiler knows

def test_every_id_family_the_schema_mints_is_recognised_by_the_compiler():
    """CROSS-FILE, and deliberately GENERIC rather than a list. `_DESIGN_ARTIFACT_ID_RE` exists so
    a `blocked_by` key that is really one of this document's non-slice ids gets a refusal naming
    the seam instead of reading as an ordinary typo (#1956). Every time this schema grows an id
    family, that refusal silently stops covering it -- which is what happened here twice at once.
    Reading the families OUT OF THE FENCE means the next one is caught without editing this test."""
    ids = sorted(set(re.findall(r"\b([A-Z]{1,2}-\d+)\b", _schema_fence())))
    assert {"BR-1", "D-1", "B-1", "PC-1", "X-1"} <= set(ids), \
        "the schema no longer mints the id families this pin was written for: %r" % ids
    live = _live_compile_plan()._DESIGN_ARTIFACT_ID_RE
    for ident in ids:
        assert live.match(ident), \
            "the schema mints %r but compile_plan does not recognise it" % ident


def test_the_depends_on_rule_forbids_the_two_new_id_families_too():
    """The `Depends on` column is machine-read and its rule enumerates the non-slice shapes it
    excludes. Two new families left out of that enumeration make the rule incomplete at exactly the
    point a pass would reach for one."""
    rule = _depends_on_rule()
    assert "Premise check" in rule, "the rule does not exclude a Premise check id"
    assert "Out of scope" in rule, "the rule does not exclude an Out of scope id"


# =============================================== #2028: the seed set is recorded, not remembered
# The §2 stopping rule is defined over the seed set -- "every seed has been swept" -- and the
# artifact had no heading for one. So the set existed only in the pass's own head: `converged` was
# an unfalsifiable claim about an invisible set, `capped` named its unswept seeds in prose alone,
# and the question the budget argument actually turns on -- was the last round still productive --
# could not be derived from the document at all. This adds the record. It does NOT change the
# budget, which is a product decision (#2028's own follow-up).

_SEEDS_MARKER = "**`Seeds` is what makes `converged` and `capped` checkable"


def _seeds_rule():
    """The §5 rule block, read out of the prose. Presence asserted, never assumed, for the reason
    `_depends_on_rule` records: `str.split` on a missing separator returns the WHOLE text."""
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    assert _SEEDS_MARKER in schema, "the §5 rule behind the Seeds heading is gone"
    return schema.split(_SEEDS_MARKER, 1)[1].split("**`Out of scope` is what makes", 1)[0]


def test_the_schema_has_a_heading_for_the_seed_set_the_stopping_rule_is_defined_over():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    headings = {h.strip().lower() for h in re.findall(r"^## (.+)$", schema, re.M)}
    assert "seeds" in headings, \
        "the set `converged` and `capped` are both claims about still has nowhere to be written"


def test_the_seeds_skeleton_carries_the_four_fields_the_record_exists_for():
    """A bare list of seeds answers neither question the record is for. Status is what makes an
    unswept seed countable; the two round columns are what make "was the last round productive"
    derivable from the artifact instead of from the banner's own say-so."""
    fence = _schema_fence()
    assert "## Seeds" in fence, "the copyable skeleton omits the Seeds heading"
    seeds = fence.split("## Seeds", 1)[1].split("## Blast radius", 1)[0]
    # Pinned on the HEADER ROW, not the section. The explanatory comment under the table names
    # `Status` and both round columns in prose, so a section-wide `in` check is satisfied by the
    # commentary alone. Control: with the `Status` column deleted from the header row this test
    # stayed GREEN in its section-wide form -- against the exact defect its own message names.
    header = [line for line in seeds.splitlines() if line.startswith("| ID |")]
    assert header, "the Seeds table has no header row"
    for column in ("Seed", "Found in round", "Swept in round", "Status", "Carried as"):
        assert column in header[0], f"the Seeds table has no {column!r} column: {header[0]!r}"
    rows = [line for line in seeds.splitlines() if line.startswith("| S-")]
    assert rows, "the Seeds skeleton pins no id"
    assert any("| unswept |" in r for r in rows) and any("| swept |" in r for r in rows), \
        "the skeleton never shows what a Status cell actually reads"


def test_seed_closure_is_judged_against_the_written_table_not_against_memory():
    """The tie-in, and the whole point -- exactly the shape #1975 gave the quiet round. A place to
    write the seeds that the stopping rule does not consult is a section a pass can leave empty and
    still declare `converged`."""
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    assert "**Seed closure**" in depth, "the stopping rule's own anchor moved -- re-check this pin"
    closure = re.sub(r"\s+", " ", depth.split("**Seed closure**", 1)[1].split("2. **A quiet", 1)[0])
    assert "## Seeds" in closure, "seed closure names no written record to be judged against"
    assert "never against memory" in closure, \
        "seed closure is still satisfiable by a set only the pass can see"


def test_the_capped_banner_count_is_pinned_to_the_table_it_summarises():
    """Two numbers for one fact is a drift the reader cannot detect: the banner says `k` seeds are
    unswept and, until the table existed, nothing else in the document said otherwise. Pinning them
    to each other is what turns the banner from a claim into a checkable one."""
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    flat = re.sub(r"\s+", " ", depth)
    assert "The banner's count IS the table's count" in flat, \
        "the capped banner's count is tied to nothing, so it cannot disagree with anything"
    assert "schema violation" in flat, \
        "a banner that disagrees with its own table has no stated consequence"


def test_the_capped_outcome_says_what_a_reader_does_next():
    """#2028's actual product complaint: a capped pass told the reader the mapping was partial and
    stopped there. The unswept rows are the continuation list -- and the honest half is that
    nothing performs the continuation, which has to be said in the same breath."""
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    flat = re.sub(r"\s+", " ", depth)
    assert "ARE the continuation list" in flat, \
        "a capped pass still ends without telling the reader where to resume"
    assert "Nothing in this kit re-runs a design pass automatically" in flat, \
        "the continuation reads as a mechanism the kit does not have"


def test_the_seeds_are_produced_explicitly_alongside_the_other_five():
    """§3 is the "produce, explicitly" list every consumer reads; a heading in §5's schema with no
    entry there is one a pass meets for the first time when it is already writing the file."""
    step3 = _section(SKILL, "## 3. Blast radius", "## 4. Detailed design")
    assert "**Seeds**" in step3, "§3 never tells the pass to produce the seed record"
    assert "S-1" in step3, "§3's numbering rule does not cover the seeds"


def test_the_seeds_rule_names_its_consumer_and_the_cost_of_omitting_it():
    """Same shape the Out of scope rule already has. A section with no consumer is one nothing has
    to read, which is how `Premise check` reached a real run empty."""
    flat = re.sub(r"\s+", " ", _seeds_rule()).replace("*", "")
    assert "goal-review" in flat, "the section names no consumer, so nothing has to read it"
    assert "every artifact" in flat, \
        "the table reads as a capped-only section, so a `converged` claim stays uncheckable"


def test_the_seeds_rule_does_not_overclaim_what_the_record_buys():
    """RELIABILITY, and the specific way this class of fix goes wrong -- #1975 records the same
    trap. Nothing executes this table, it does not raise the budget, and a seed the pass never
    thought of is still invisible. Saying so is the difference between a record and a guarantee."""
    flat = re.sub(r"\s+", " ", _seeds_rule()).replace("*", "")
    assert "does not raise the budget" in flat, \
        "the record reads as if writing the seeds down made the sweep longer"
    assert "a seed the pass never held" in flat, \
        "the rule does not name the coverage limit it leaves open"


def test_the_capped_recording_list_says_how_many_places_it_actually_enumerates():
    """The count and the list are two statements of one fact, and #2028 added a fourth place --
    which is exactly when "all three of these" goes stale and starts telling a pass to record in
    one fewer place than the list below it names. Read both ends and compare. Seen red by putting
    `all three` back above the four-item list."""
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    words = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    m = re.search(r"in\s+all\s+(\w+)\s+of these rather than one:", re.sub(r"\s+", " ", depth))
    assert m, "the capped-recording list lost its intro -- re-check this pin"
    claimed = words.get(m.group(1))
    assert claimed, f"the intro states an unreadable count: {m.group(1)!r}"
    listed = re.findall(r"^\d+\. ", depth.split("of these rather than one:", 1)[1], re.M)
    assert len(listed) == claimed, \
        f"the intro says {claimed} places to record a capped sweep; the list names {len(listed)}"


# =============================================== #2032: the sweep budget is configurable, checkable
# Before this issue "the budget is three sweep rounds" was a number stated only in this prose --
# nothing in Python counted a round, and an earlier pass correctly declined to add a bare
# `goal_design.rounds` config key because nothing would have read it (a silent half-guarantee,
# which SAFETY forbids). `goal_design.py sweep-budget` is the fix: engine-owned data behind a CLI
# verb, mirroring `dossier.py`'s own `followups()`, so the number a pass obeys is fetched, and the
# artifact's new `**Budget**` field records what was fetched rather than what was remembered.

def test_the_header_line_carries_a_budget_field():
    fence = _schema_fence()
    header = [line for line in fence.splitlines() if line.startswith("**Target**")]
    assert header, "the schema's own header line is gone -- re-check this pin"
    assert "**Budget**" in header[0], "the header line carries no Budget field"


def test_the_budget_is_fetched_by_a_verb_not_assumed():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    flat = re.sub(r"\s+", " ", depth)
    assert "sweep-budget" in flat, "§2 never names the verb that resolves the budget"
    assert "goal_design.py" in flat, "§2 does not point at the engine script"
    assert "never write it from memory" in flat.lower() or "never assume it" in flat.lower(), \
        "§2 does not forbid a hand-typed budget"


def test_full_mode_default_is_still_three_and_is_now_configurable():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    flat = re.sub(r"\s+", " ", depth)
    assert "goal_design.rounds" in flat, "§2 never names the config key #2032 adds"
    assert "default of **3**" in flat or "default of 3" in flat, \
        "§2 no longer states the unconfigured default"


def test_there_is_no_unbounded_sentinel():
    """#2032's own ruling: each round is real Anthropic spend, so raising the ceiling must mean a
    number the operator chose, never an open-ended loop."""
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    flat = re.sub(r"\s+", " ", depth)
    assert '"unbounded"' in flat or "`\"unbounded\"`" in flat.replace("`", ""), \
        "§2 does not rule out an unbounded value"
    assert "real Anthropic spend" in flat, "§2 does not say why the ceiling stays bounded"


def test_lane_mode_rounds_is_inert_and_names_the_fixed_figures():
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    flat = re.sub(r"\s+", " ", depth)
    assert "inert" in flat, "§2 never says the config key does nothing under mode: lane"
    bare = flat.replace(" ", "").replace("`", "")
    assert "small=1" in bare and "medium=2" in bare and "large=3" in bare, \
        "§2 does not name the fixed lane figures -- medium/large were never numbered before #2032"
    assert "rounds_config_ignored" in flat, "§2 never names the field that reports the override was set aside"


def test_the_budget_field_is_documented_as_fetched_never_hand_typed():
    schema = _section(ARTIFACT, "## 5. Write the artifact", "## 6. Handoff")
    marker = "**`Budget` is written on every artifact, and it is the FETCHED number"
    assert marker in schema, "the §5 rule behind the Budget heading is gone"
    flat = re.sub(r"\s+", " ", schema.split(marker, 1)[1].split("**Doubts and Blockers", 1)[0])
    assert "goal-review" in flat, "the Budget rule names no consumer that checks it"
    assert "sweep-budget" in flat, "the Budget rule does not name the verb that produces the number"


def test_the_final_round_illustration_no_longer_hardcodes_round_three_as_the_rule():
    """Pre-#2032 this was a fixed 3-round budget, so "if round three was still turning up new
    components" was a correct restatement of "the last round". Once the budget is configurable
    that literal sentence is wrong on any raised ceiling -- round three may not be the last round
    at all. The rule must generalise to whichever round the fetched budget actually ends on."""
    depth = _section(MAPPING, "## 2. Map to the codebase", "## 3.")
    flat = re.sub(r"\s+", " ", depth)
    assert "the FINAL round of your budget" in flat, \
        "the stopping-rule illustration still hardcodes round three as THE rule"


def test_goal_design_py_exists_and_the_documented_verb_is_real():
    """CROSS-FILE, mirroring `test_sdlc_goal_review_skill.py`'s live-module pins. §2 tells the
    pass to run `sweep-budget` -- if the verb or the defaults it documents ever drifted from the
    script, following this file exactly would either error or silently disagree with the code."""
    assert goal_design_py.DEFAULT_FULL_ROUNDS == 3
    assert goal_design_py.LANE_ROUNDS == {"small": 1, "medium": 2, "large": 3}
    # `.sdlc` need not even exist here -- `main()` treats a missing config.json as `{}`, the same
    # reading `SKILL.md` §2 already gives `ConfigMissing` for `mode` resolution -- so this call
    # exercises the real verb end to end without depending on the test runner's own cwd.
    result = goal_design_py.main(["goal_design.py", "sweep-budget", ".sdlc", "--mode", "full"])
    assert result == 0


def test_goal_design_scripts_directory_is_where_the_skill_points():
    assert (ROOT / "skills" / "sigma-goal-design" / "scripts" / "goal_design.py").is_file(), \
        "the engine script §2 points at does not exist on disk"

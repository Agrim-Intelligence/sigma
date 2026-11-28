"""#2282: `reuse.py check-plan` checks a plan's `## Reuse` citations and its `## Test map`.

The fixtures come from tests/test_reuse.py: goal 7 has a record of three criteria (AC-1..AC-3) and a new-format
dossier whose table is RU-1 and RU-2 here. `_complete` builds a project whose plan passes with exactly `([], [])`.
Every refusal test asserts the exact error list, so an extra or a missing error is red. Every test calls `_check_plan`
or runs the command line, so each goes red by AssertionError (never AttributeError) while `check_plan` is absent.
This file never prints: a captured-output section would stop `red_green` attributing the failure to a test."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_reuse import (_project, _dossier, _cli, _reuse, ACCEPTANCE, ROWS, HEADER, SEPARATOR, _row,  # noqa: E402
                        NO_DOSSIER, NO_SECTION, OLD_SECTION)

reuse = _reuse()
MAX = 1048576
_DEF = object()

REUSE_OK = ("- RU-1: Task 1 calls the resolver to find the dossier\n"
            "- RU-2: Task 2 writes the new parser\n")
TESTS_OK = "- `tests/test_x.py::test_a`\n- `tests/test_y.py`\n"
MAP_OK = ("- AC-1: tests/test_x.py::test_a\n"
          "- AC-2: tests/test_y.py::test_one, tests/test_y.py::test_two\n"
          "- AC-3: UNTESTABLE: a wording change with no behaviour\n")
RUNNERS = '{"tests": {"runners": {"jest": "sh stub.sh {file} -t {name}"}}}'
NO_RECORD = "no recorded acceptance criteria for goal 7"
RISK_MISSING = "Risk: missing; write **Risk**: none, or any of sensitive, contract, migration, above the first ## heading"
TD_MISSING = "Test design: missing; write ## Test design with one row per acceptance criterion"
LEGACY = "; plan inputs not checked"
UNCOVERED = "AC-1: %s is not covered by a ## Tests entry"
SKIPPED = "## Test map: coverage not judged ("


def _legacy(text):
    return text.replace("; nothing checked", LEGACY)


def _check_plan(*args, **kw):
    assert hasattr(reuse, "check_plan"), "check_plan is absent"
    return reuse.check_plan(*args, **kw)


def _plan_text(reuse=_DEF, test_map=_DEF, tests=_DEF, extra=""):
    """A plan; a section given as None is left out, and the default of each is the passing one."""
    parts = ["# Plan for goal 7\n"]
    for heading, body, default in (("Reuse", reuse, REUSE_OK), ("Test map", test_map, MAP_OK),
                                   ("Tests", tests, TESTS_OK)):
        body = default if body is _DEF else body
        if body is not None:
            parts.append("\n## %s\n%s" % (heading, body))
    return "".join(parts) + extra


def _complete(tmp_path, dossier=None, config=None, plan=None, **plan_over):
    """The sdlc dir of a git project whose goal 7 has a new-format dossier (RU-1, RU-2) and a plan.
    `dossier(head)` replaces the dossier text; `plan=` replaces the whole plan text."""
    sdlc, _, head = _project(tmp_path, git=True)
    (sdlc / "research" / "7.md").write_text(dossier(head) if dossier else _dossier(searched_at=head, rows=ROWS[:2]),
                                            encoding="utf-8")
    (sdlc / "plans").mkdir(exist_ok=True)
    (sdlc / "plans" / "7.md").write_text(plan if plan is not None else _plan_text(**plan_over), encoding="utf-8")
    if config is not None:
        (sdlc / "config.json").write_text(config, encoding="utf-8")
    return sdlc


def _result(tmp_path, **kw):
    return _check_plan(str(_complete(tmp_path, **kw)), "7")


def _map(ac1="tests/test_x.py::test_a", rest=MAP_OK.split("\n", 1)[1]):
    return "- AC-1: %s\n%s" % (ac1, rest)


# -- legacy goals and rule 0 -------------------------------------------------------------------------------------

def test_a_complete_plan_passes(tmp_path):
    assert _result(tmp_path) == ([], [])


def test_a_goal_with_no_dossier_is_legacy(tmp_path):
    sdlc, _, _ = _project(tmp_path, git=True)
    (sdlc / "plans").mkdir()
    (sdlc / "plans" / "7.md").write_text(_plan_text(reuse=None), encoding="utf-8")
    assert _check_plan(str(sdlc), "7") == ([], [_legacy(NO_DOSSIER)])
    assert _legacy(NO_DOSSIER) == "legacy goal (no research dossier); plan inputs not checked"


def test_a_dossier_without_reuse_is_legacy(tmp_path):
    text = "# Research: x\n\n## Goal in one paragraph\nWhat it does.\n\n## Blast radius\n- a file\n"
    assert _result(tmp_path, dossier=lambda head: text, reuse=None) == ([], [_legacy(NO_SECTION)])
    assert _legacy(NO_SECTION) == "legacy goal (the research dossier has no ## Reuse section); plan inputs not checked"


def test_an_old_dossier_with_its_own_reuse_heading_is_legacy_for_the_plan_check(tmp_path):
    text = "# Research: x\n\n## Reuse\n- `src/app.py`: a helper (line 3).\n"
    assert _result(tmp_path, dossier=lambda head: text, reuse=None) == ([], [_legacy(OLD_SECTION)])
    assert _legacy(OLD_SECTION) == "legacy goal (## Reuse was not written from reuse.py brief); plan inputs not checked"


def test_a_legacy_goal_with_no_plan_is_a_warning(tmp_path):
    sdlc, _, _ = _project(tmp_path, git=True)
    assert _check_plan(str(sdlc), "7") == ([], [_legacy(NO_DOSSIER)])
    proc = _cli(tmp_path, "check-plan", sdlc, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "reuse.py: " + _legacy(NO_DOSSIER) + "\n")


def test_a_dossier_that_fails_check_research_fails_the_plan_check(tmp_path):
    """Two independent faults (no Risk line, no Test design) so returning only the first error is red too."""
    sdlc = _complete(tmp_path, dossier=lambda head: _dossier(searched_at=head, rows=ROWS[:2], risk="", design=""))
    expected = reuse.check_research(str(sdlc), "7")[0]
    assert expected == [RISK_MISSING, TD_MISSING]
    assert _check_plan(str(sdlc), "7") == (expected, [])


def test_an_oversized_dossier_is_check_researchs_error_not_a_legacy_pass(tmp_path):
    """The legacy parse is skipped past MAX_CHARS: parsing 1 MiB just to call it legacy would pass a dossier
    `check-research` refuses."""
    text = "# Research: x\n\n## Goal in one paragraph\n" + "x" * MAX + "\n"
    assert _result(tmp_path, dossier=lambda head: text) == (
        ["research dossier is over %d characters; nothing checked" % MAX], [])


def test_a_missing_plan_is_one_error(tmp_path):
    sdlc = _complete(tmp_path)
    (sdlc / "plans" / "7.md").unlink()
    assert _check_plan(str(sdlc), "7") == (["no plan for goal 7"], [])


def test_an_oversized_plan_is_one_error(tmp_path):
    assert _result(tmp_path, extra="\n" + "x" * MAX) == (["plan: over %d characters; nothing checked" % MAX], [])


def test_a_plan_passed_in_is_read_instead_of_the_filed_one(tmp_path):
    sdlc = _complete(tmp_path, reuse=None)
    other = tmp_path / "reviewed.md"
    other.write_text(_plan_text(), encoding="utf-8")
    assert _check_plan(str(sdlc), "7", plan=str(other)) == ([], [])
    assert _check_plan(str(sdlc), "7") == (["## Reuse: section missing"], [])


# -- the plan's ## Reuse (rules 1 to 4) -----------------------------------------------------------------------------

def test_a_plan_without_a_reuse_section_is_refused(tmp_path):
    assert _result(tmp_path, reuse=None) == (["## Reuse: section missing"], [])


def test_a_research_row_with_no_bullet_is_refused_and_named(tmp_path):
    missing = "RU-%d: no bullet in the plan's ## Reuse for this research row"
    assert _result(tmp_path, reuse="- RU-1: Task 1 does it\n") == ([missing % 2], [])
    # A plan none: line never excuses a research row: each id is still named, and the line adds no error.
    assert _result(tmp_path / "a", reuse="none: nothing here to cite\n- RU-1: Task 1 does it\n") == ([missing % 2], [])
    assert _result(tmp_path / "b", reuse="none: nothing here to cite\n") == ([missing % 1, missing % 2], [])


def test_a_plan_table_row_obeys_the_row_rules(tmp_path):
    ok = "reuse extend stdlib dependency dependency-new new"
    cases = [
        ("| RU-8 | a cap | bogus | - | a reason |", "RU-8: decision 'bogus' is not one of " + ok),
        ("| RU-8 |  | reuse | src/app.py:3 | a reason |", "RU-8: capability is empty"),
        ("| RU-8 | a cap | reuse | src/app.py:3 |", "RU-8: 4 cells; a row has 5"),
        ("| RU-8 | a cap | reuse |  | a reason |",
         "RU-8: decision 'reuse' needs evidence: file:line or file:line-line of a file inside the project"),
    ]
    for n, (row, error) in enumerate(cases):
        table = "\n%s\n%s\n%s\n" % (HEADER, SEPARATOR, row)
        assert _result(tmp_path / str(n), reuse=REUSE_OK + table) == ([error], [])
    good = "\n%s\n%s\n%s\n" % (HEADER, SEPARATOR, _row("RU-8"))
    assert _result(tmp_path / "good", reuse=REUSE_OK + good) == ([], [])


def test_a_plan_row_id_that_collides_with_research_is_refused(tmp_path):
    table = "\n%s\n%s\n%s\n" % (HEADER, SEPARATOR, _row("RU-1"))
    assert _result(tmp_path, reuse=REUSE_OK + table) == (["RU-1: id collides with a row of the research dossier"], [])


def test_none_reuse_in_research_allows_none_in_the_plan(tmp_path):
    def none_dossier(head):
        return _dossier(searched_at=head, none="the goal adds no new capability at all", queries=[], rows=None)
    assert _result(tmp_path / "a", dossier=none_dossier, reuse="none: nothing new is added here\n") == ([], [])
    assert _result(tmp_path / "b", dossier=none_dossier, reuse="none:\n") == (["## Reuse: none: needs a reason"], [])
    assert _result(tmp_path / "c", dossier=none_dossier, reuse="none:   \n") == (["## Reuse: none: needs a reason"], [])


def test_an_empty_bullet_text_is_refused(tmp_path):
    for n, first in enumerate(["- RU-1:", "- RU-1:   "]):
        assert _result(tmp_path / str(n), reuse=first + "\n- RU-2: Task 2 does it\n") == (
            ["RU-1: bullet text is empty"], [])


def test_a_bullet_in_a_fence_or_another_section_does_not_count(tmp_path):
    body = "- RU-2: Task 2 does it\n```\n- RU-1: inside a fence\n```\n"
    extra = "\n## Notes\n- RU-1: in another section\n"
    assert _result(tmp_path, reuse=body, extra=extra) == (
        ["RU-1: no bullet in the plan's ## Reuse for this research row"], [])


# -- the plan's ## Test map (rules 5 to 9) -----------------------------------------------------------------------

def test_a_plan_without_a_test_map_is_refused(tmp_path):
    assert _result(tmp_path, test_map=None) == (["## Test map: section missing"], [])


def test_a_criterion_with_no_line_is_refused_and_named(tmp_path):
    assert _result(tmp_path, test_map=MAP_OK.rsplit("- AC-3", 1)[0]) == (["AC-3: no bullet in ## Test map"], [])


def test_two_lines_for_one_criterion_are_refused(tmp_path):
    assert _result(tmp_path, test_map=MAP_OK + "- AC-1: tests/test_y.py::test_other\n") == (
        ["AC-1: more than one bullet"], [])


def test_a_bullet_for_an_unknown_criterion_is_refused(tmp_path):
    assert _result(tmp_path, test_map=MAP_OK + "- AC-9: tests/test_x.py::test_a\n") == (
        ["AC-9: not an acceptance criterion of goal 7"], [])


def test_test_map_none_is_refused(tmp_path):
    for n, value in enumerate(["NONE", "none", "None: see tests"]):
        assert _result(tmp_path / str(n), test_map=_map(value)) == (
            ["AC-1: NONE is refused; map a test or write UNTESTABLE: <why>"], [])


def test_untestable_needs_a_reason(tmp_path):
    for n, value in enumerate(["UNTESTABLE:", "UNTESTABLE:   ", "UNTESTABLE"]):
        assert _result(tmp_path / str(n), test_map=_map(value)) == (["AC-1: UNTESTABLE needs a reason"], [])
    assert _result(tmp_path / "real", test_map=_map("UNTESTABLE: a wording change only")) == ([], [])


def test_a_mapped_test_must_be_under_tests(tmp_path):
    assert _result(tmp_path / "a", test_map=_map("tests/test_x.py::test_zzz")) == (
        [UNCOVERED % "tests/test_x.py::test_zzz"], [])
    assert _result(tmp_path / "b", test_map=_map("tests/test_x.py::test_a, tests/test_y.py::test_q")) == ([], [])
    assert _result(tmp_path / "c", test_map=_map("tests/test_x.py::test_a, tests/other.py::t")) == (
        [UNCOVERED % "tests/other.py::t"], [])
    assert _result(tmp_path / "d", test_map=_map("tests/test_x.py::test_a,tests/test_y.py::q")) == ([], [])


def test_a_file_entry_covers_its_tests(tmp_path):
    assert _result(tmp_path / "a", test_map=_map("tests/test_y.py::t")) == ([], [])
    assert _result(tmp_path / "b", test_map=_map("tests/test_y.py")) == ([], [])
    assert _result(tmp_path / "c", test_map=_map("tests/test_y.pyx::t")) == ([UNCOVERED % "tests/test_y.pyx::t"], [])
    assert _result(tmp_path / "d", test_map=_map("tests/other.py::t")) == ([UNCOVERED % "tests/other.py::t"], [])


def test_a_runner_bullet_is_covered_only_exactly(tmp_path):
    entry = "jest: web/app.test.js > suite > adds a, b"
    plain = "jest: web/app.test.js > suite > one"
    tests = TESTS_OK + "- `%s`\n- `%s`\n" % (entry, plain)
    kw = {"config": RUNNERS, "tests": tests}
    assert _result(tmp_path / "a", test_map=_map(entry), **kw) == ([], [])      # the comma is in the name
    assert _result(tmp_path / "b", test_map=_map(plain + ", tests/test_x.py::test_a"), **kw) == ([], [])
    for n, value in enumerate([plain + "::sub", "jest: web/app.test.js::t", "jest: web/app.test.js > suite"]):
        assert _result(tmp_path / str(n), test_map=_map(value), **kw) == ([UNCOVERED % value], [])


def test_a_docs_only_plan_may_map_none(tmp_path):
    docs = {"tests": "none: a docs only change\n"}
    assert _result(tmp_path / "a", test_map="none: no code is changed here\n", **docs) == ([], [])
    assert _result(tmp_path / "b", test_map="none:\n", **docs) == (["## Test map: none: needs a reason"], [])
    # A none: map under a code ## Tests has no special error: each criterion is simply unmapped.
    assert _result(tmp_path / "c", test_map="none: no code is changed here\n") == (
        ["AC-%d: no bullet in ## Test map" % n for n in (1, 2, 3)], [])


def test_an_empty_test_map_value_is_refused(tmp_path):
    for n, first in enumerate(["- AC-1:", "- AC-1:   "]):
        assert _result(tmp_path / str(n), test_map=first + "\n" + MAP_OK.split("\n", 1)[1]) == (
            ["AC-1: value is empty; map a test or write UNTESTABLE: <why>"], [])


def test_a_broken_tests_section_is_left_to_the_existing_check(tmp_path):
    """No coverage error for a `## Tests` the existing check refuses; the id errors still speak."""
    short = MAP_OK.rsplit("- AC-3", 1)[0]
    for n, tests in enumerate([None, "- `../x.py`\n", "- `jest: web/a.test.js > t`\n"]):
        errors, _ = _result(tmp_path / str(n), test_map=short, tests=tests)
        assert errors == ["AC-3: no bullet in ## Test map"]


def test_a_skipped_coverage_check_is_a_warning(tmp_path):
    cases = [(None, None), ("- `../x.py`\n", None), ("- `jest: web/a.test.js > t`\n", None),
             ("- `jest: web/a.test.js > t`\n", '{"tests": ')]
    for n, (tests, config) in enumerate(cases):
        errors, warnings = _result(tmp_path / str(n), tests=tests, config=config)
        assert errors == []
        assert len(warnings) == 1 and warnings[0].startswith(SKIPPED) and warnings[0].endswith(")"), warnings
        assert "\n" not in warnings[0]


def test_a_map_bullet_outside_the_test_map_section_is_ignored(tmp_path):
    short = MAP_OK.rsplit("- AC-3", 1)[0]
    extra = "\n## Notes\n- AC-3: tests/test_x.py::test_a\n- AC-9: tests/test_x.py::test_a\n"
    assert _result(tmp_path, test_map=short, extra=extra) == (["AC-3: no bullet in ## Test map"], [])


def test_the_plan_map_does_not_satisfy_test_reviews_reader(tmp_path):
    text = _plan_text()
    mapped, conflicts = reuse._load("review_tests").parse_map(text)
    assert (mapped, conflicts) == ({}, set())
    assert _result(tmp_path, plan=text) == ([], [])


def test_a_missing_config_reads_as_no_runners(tmp_path):
    sdlc = _complete(tmp_path)
    assert not (sdlc / "config.json").exists()
    assert _check_plan(str(sdlc), "7") == ([], [])


# -- rule 10: no recorded acceptance criteria -------------------------------------------------------------------

def test_no_acceptance_record_is_refused_when_verify_is_enforced(tmp_path):
    sdlc = _complete(tmp_path, config='{"verify": {"enforce": true}}')
    (sdlc / "acceptance" / "7.md").unlink()
    assert _check_plan(str(sdlc), "7") == ([NO_RECORD + "; record them before Research", NO_RECORD], [])


def test_no_acceptance_record_warns_when_verify_is_not_enforced(tmp_path):
    sdlc = _complete(tmp_path, test_map="- AC-1: tests/test_x.py::test_a\n")
    (sdlc / "acceptance" / "7.md").unlink()
    assert _check_plan(str(sdlc), "7") == ([], [NO_RECORD + "; one row per criterion was not checked", NO_RECORD])


def test_criteria_added_by_add_slice_need_no_new_line(tmp_path):
    sdlc = _complete(tmp_path, tests=TESTS_OK + "- `tests/test_added.py::test_fourth`\n")
    record = sdlc / "acceptance" / "7.md"
    record.write_text(ACCEPTANCE + "\n## Added criteria\n- [ ] A fourth thing works. "
                      "(added: 2300; reason: a slice found a gap)\n", encoding="utf-8")
    acceptance = reuse._load("acceptance").parse(record.read_text(encoding="utf-8"), "7")
    assert acceptance["added"] and acceptance["added"][0]["slice"] == "2300"
    assert [cid for cid, _ in reuse._load("review_tests").criteria(str(sdlc), "7")] == ["AC-1", "AC-2", "AC-3"]
    assert _check_plan(str(sdlc), "7") == ([], [])


# -- the command line -----------------------------------------------------------------------------------------------

def test_cli_exit_codes_and_streams(tmp_path):
    """The documented gesture, `reuse.py check-plan <sdlc_dir> <goal>`, as a real subprocess. The complete plan comes
    first: on the base code the bad-shape cases already exit 2, so only this case turns the node red."""
    sdlc = _complete(tmp_path / "ok")
    plan = sdlc / "plans" / "7.md"
    proc = _cli(tmp_path, "check-plan", sdlc, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        0, "reuse.py: ## Reuse and ## Test map in %s are complete (2 rows cited, 3 criteria mapped)\n" % plan, "")

    legacy, _, _ = _project(tmp_path / "legacy", git=True)
    proc = _cli(tmp_path, "check-plan", legacy, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "reuse.py: " + _legacy(NO_DOSSIER) + "\n")

    refused = _complete(tmp_path / "refused", reuse="- RU-1: Task 1 does it\n")
    proc = _cli(tmp_path, "check-plan", refused, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        2, "", "reuse.py: RU-2: no bullet in the plan's ## Reuse for this research row\n")

    many = _complete(tmp_path / "many", test_map=MAP_OK + "".join(
        "- AC-%d: tests/test_x.py::test_a\n" % n for n in range(10, 70)))
    proc = _cli(tmp_path, "check-plan", many, "7")
    lines = proc.stderr.splitlines()
    assert (proc.returncode, proc.stdout, len(lines)) == (2, "", 51)
    assert lines[0] == "reuse.py: AC-10: not an acceptance criterion of goal 7"
    assert lines[-1] == "reuse.py: … and 10 more errors"

    warned = _complete(tmp_path / "warned")
    (warned / "acceptance" / "7.md").unlink()
    proc = _cli(tmp_path, "check-plan", warned, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        0, "reuse.py: ## Reuse and ## Test map in %s are complete (2 rows cited, 0 criteria mapped)\n"
        % (warned / "plans" / "7.md"),
        "reuse.py: %s; one row per criterion was not checked\nreuse.py: %s\n" % (NO_RECORD, NO_RECORD))

    for args in [("check-plan", sdlc), ("check-plan", sdlc, "7", "extra"), ("check-plan", sdlc, "7", "--at-research")]:
        proc = _cli(tmp_path, *args)
        assert (proc.returncode, proc.stdout) == (2, ""), args
        assert proc.stderr == reuse.USAGE + "\n" and "check-plan" in proc.stderr, args
        assert proc.stderr.startswith("usage: reuse.py check-research "), args

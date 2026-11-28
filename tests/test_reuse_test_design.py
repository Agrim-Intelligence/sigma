"""#2279: `reuse.py check-research` also checks the research dossier's `**Risk**:` line and its `## Test design`
table, one row per acceptance criterion of the goal.

Only a new-format dossier is checked (a legacy one is still a warning, never a refusal). The fixtures come from
tests/test_reuse.py: goal 7 has a record of three criteria (AC-1..AC-3), and the default dossier carries
`**Risk**: none` and a three-row table. Every refusal test asserts the exact error list, so an extra or a missing
error is red."""
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_reuse import (_project, _dossier, _run, _reuse, _cli, NO_SECTION, OLD_SECTION, RISK, DESIGN,  # noqa: E402
                        TD_HEADER, TD_SEPARATOR, ACCEPTANCE)

WARN = "Searched at: commit 0123456 is not on this machine"
RISK_MISSING = "Risk: missing; write **Risk**: none, or any of sensitive, contract, migration, above the first ## heading"
RISK_NONE = "Risk: none cannot be combined with another value"
RISK_SENSITIVE = "Risk: sensitive needs a ## Test design row whose Hostile input is a case"
TD_MISSING = "Test design: missing; write ## Test design with one row per acceptance criterion"
TD_HEADER_ERR = "Test design: the header row must be exactly " + TD_HEADER
TD_SEPARATOR_ERR = "Test design: the header row must be followed by a separator row"


def unknown(n, first):
    return "Risk: %d value(s) not one of none sensitive contract migration; the first is %r" % (n, first)


def not_an_id(who, criterion):
    return "%s: %r is not one of this goal's criterion ids" % (who, criterion)


def _case_row(criterion="AC-1", behaviour="b", failure="c", hostile="n/a", helpers="—"):
    return "| %s | %s | %s | %s | %s |" % (criterion, behaviour, failure, hostile, helpers)


GOOD_ROWS = DESIGN.splitlines()[3:]


def _design(*rows, header=TD_HEADER, separator=TD_SEPARATOR):
    """The `## Test design` section; `header=None` / `separator=None` leave that line out."""
    lines = ["## Test design"] + [line for line in (header, separator) if line is not None] + list(rows)
    return "\n".join(lines) + "\n"


def _with_row(n, row):
    """The default three rows with row `n` (1-based) replaced by `row`."""
    rows = list(GOOD_ROWS)
    rows[n - 1] = row
    return _design(*rows)


def _no_record(tmp_path, config=None):
    """A non-git project whose acceptance record is gone, with `config` (raw text) as its config.json."""
    sdlc, _, _ = _project(tmp_path)
    (sdlc / "acceptance" / "7.md").unlink()
    if config is not None:
        (sdlc / "config.json").write_text(config, encoding="utf-8")
    return sdlc


# -- Task 1: the two parsers ----------------------------------------------------------------------------------

def test_parse_risk_and_parse_test_design_return_data():
    m = _reuse()
    assert m.parse_risk("# R\n  **Risk**:  sensitive ,contract  \n## A\n**Risk**: none\n") == ["sensitive", "contract"]
    assert m.parse_risk("# R\n## A\n") is None
    # A blank line, a prose line holding a pipe, an indented header and an indented row: only lines that START
    # with `|` once stripped are the table.
    section = ("## Test design\n\nThe table below; a cell writes a pipe as \\|.\n  " + TD_HEADER + "\n" + TD_SEPARATOR + "\n"
               "   | AC-1 | it works | it fails loudly | a hostile name | a \\| b |\n")
    assert m.parse_test_design(_dossier(design=section)) == {
        "present": True, "header": TD_HEADER, "separator": True,
        "rows": [{"criterion": "AC-1", "behaviour": "it works", "failure": "it fails loudly",
                  "hostile": "a hostile name", "helpers": "a \\| b", "cells": 5}]}
    assert m.parse_test_design("# R\n## A\n") == {"present": False, "header": None, "separator": False, "rows": []}


@pytest.mark.parametrize("where", ["below-heading", "fenced", "indented", "colon-inside-bold", "mid-line",
                                   "lowercase-label"],
                         ids=["below-heading", "fenced", "indented", "colon-inside-bold", "mid-line",
                              "lowercase-label"])
def test_risk_lines_are_read_only_above_the_first_heading(tmp_path, where):
    """`mid-line`: `**Risk**:` that does not start its line is prose, not the Risk line.
    `lowercase-label`: the label is case-sensitive, so `**risk**:` is not the Risk line."""
    _, _, head = _project(tmp_path, git=True)
    if where == "below-heading":
        text = _dossier(searched_at=head, risk="").replace("## Goal in one paragraph\n",
                                                             "## Goal in one paragraph\n" + RISK)
    else:
        risk = {"fenced": "```\n" + RISK + "```\n", "indented": "  **Risk**:   none  \n",
                "colon-inside-bold": "**Risk:** none\n", "mid-line": "Notes: **Risk**: none\n",
                "lowercase-label": "**risk**: none\n"}[where]
        text = _dossier(searched_at=head, risk=risk)
    errors, _ = _run(tmp_path, text)
    assert errors == ([] if where == "indented" else [RISK_MISSING])


def test_every_risk_line_above_the_first_heading_counts(tmp_path):
    _, _, head = _project(tmp_path, git=True)
    errors, _ = _run(tmp_path, _dossier(searched_at=head, risk="**Risk**: none\n**Risk**: sensitive\n"))
    assert errors == [RISK_NONE]


# -- Task 2: the Risk and Test design rules -------------------------------------------------------------------

def test_test_design_with_one_row_per_criterion_passes(tmp_path):
    _, _, head = _project(tmp_path, git=True)
    assert _run(tmp_path, _dossier(searched_at=head)) == ([], [])
    assert _run(tmp_path, _dossier(searched_at=head), at_research=True) == ([], [])


def test_a_missing_test_design_section_is_refused(tmp_path):
    assert _run(tmp_path, _dossier(design="")) == ([TD_MISSING], [WARN])


def test_a_criterion_without_a_row_is_refused_and_named(tmp_path):
    design = _design(GOOD_ROWS[0], GOOD_ROWS[2])
    assert _run(tmp_path, _dossier(design=design)) == (["AC-2: no row in ## Test design"], [WARN])


@pytest.mark.parametrize("criterion, error", [
    ("AC-4", not_an_id("AC-4", "AC-4")), ("ac-1", not_an_id("row 4", "ac-1")), ("AC-01", not_an_id("AC-01", "AC-01")),
    ("AC-", not_an_id("row 4", "AC-")),
], ids=["AC-4", "lowercase", "leading-zero", "no-number"])
def test_a_row_for_an_unknown_criterion_is_refused(tmp_path, criterion, error):
    design = _design(*GOOD_ROWS, _case_row(criterion))
    assert _run(tmp_path, _dossier(design=design)) == ([error], [WARN])


@pytest.mark.parametrize("behaviour", ["", "—", "-"], ids=["empty", "em-dash", "dash"])
def test_an_empty_behaviour_is_refused(tmp_path, behaviour):
    design = _with_row(2, _case_row("AC-2", behaviour=behaviour))
    assert _run(tmp_path, _dossier(design=design)) == (["AC-2: behaviour is empty or a dash"], [WARN])


@pytest.mark.parametrize("failure", ["n/a", "N/A", "", "—", "-"], ids=["na", "NA", "empty", "em-dash", "dash"])
def test_a_failure_path_of_na_is_refused(tmp_path, failure):
    design = _with_row(2, _case_row("AC-2", failure=failure))
    assert _run(tmp_path, _dossier(design=design)) == (
        ["AC-2: failure path %r is not a case; write the case, or none: and a reason of three words or more" % failure],
        [WARN])


@pytest.mark.parametrize("failure, errors", [
    ("none: two words", ["AC-2: failure path none: needs a reason of three words or more"]),
    ("None: two words", ["AC-2: failure path none: needs a reason of three words or more"]),
    ("none:", ["AC-2: failure path none: needs a reason of three words or more"]),
    ("none: exactly three words", []),
    ("none: no failure mode here", []),
    ("a none: b", []),
], ids=["two-words", "capital-none", "no-reason", "three-words", "four-words", "none-mid-cell"])
def test_a_failure_path_none_needs_three_words(tmp_path, failure, errors):
    """`none-mid-cell`: a cell that only contains `none:` names a case, so its two words after it are fine."""
    design = _with_row(2, _case_row("AC-2", failure=failure))
    assert _run(tmp_path, _dossier(design=design)) == (errors, [WARN])


def test_a_missing_risk_line_is_refused(tmp_path):
    assert _run(tmp_path, _dossier(risk="")) == ([RISK_MISSING], [WARN])


@pytest.mark.parametrize("risk, errors", [
    ("**Risk**: high\n", [unknown(1, "high")]),
    ("**Risk**: Sensitive\n", [unknown(1, "Sensitive")]),
    ("**Risk**:\n", [unknown(1, "")]),
    ("**Risk**: none,\n", [unknown(1, ""), RISK_NONE]),
], ids=["high", "capital", "empty", "trailing-comma"])
def test_an_unknown_risk_value_is_refused(tmp_path, risk, errors):
    assert _run(tmp_path, _dossier(risk=risk)) == (errors, [WARN])


@pytest.mark.parametrize("values", ["none, sensitive", "none, contract", "none, none", "sensitive, none"],
                         ids=["sensitive", "contract", "none", "none-last"])
def test_risk_none_cannot_be_combined(tmp_path, values):
    """Every Hostile input is n/a, so the sensitive rule fires for `sensitive` alone, not for any Risk line."""
    design = _with_row(1, _case_row("AC-1"))
    errors = [RISK_NONE] + ([RISK_SENSITIVE] if "sensitive" in values else [])
    assert _run(tmp_path, _dossier(risk="**Risk**: %s\n" % values, design=design)) == (errors, [WARN])


@pytest.mark.parametrize("hostile", ["n/a", "N/A", "", "—", "-", "none: nothing hostile reaches it"],
                         ids=["na", "NA", "empty", "em-dash", "dash", "none-line"])
def test_sensitive_needs_one_hostile_input_case(tmp_path, hostile):
    design = _design(*[_case_row("AC-%d" % k, hostile=hostile) for k in (1, 2, 3)])
    assert _run(tmp_path, _dossier(risk="**Risk**: sensitive\n", design=design)) == ([RISK_SENSITIVE], [WARN])


def test_a_hostile_input_that_only_contains_none_is_a_case(tmp_path):
    """Only a cell that STARTS with `none:` is not a case; the other two rows' Hostile input is n/a."""
    design = _with_row(1, _case_row("AC-1", hostile="a none: payload"))
    assert _run(tmp_path, _dossier(risk="**Risk**: sensitive\n", design=design)) == ([], [WARN])


@pytest.mark.parametrize("values", ["sensitive", "contract, sensitive, migration"], ids=["sensitive", "three"])
def test_sensitive_with_a_hostile_input_case_passes(tmp_path, values):
    assert _run(tmp_path, _dossier(risk="**Risk**: %s\n" % values)) == ([], [WARN])


def test_a_hostile_input_counts_only_on_a_row_of_five_cells(tmp_path):
    """A shifted row's fourth cell is not known to be its Hostile input, so it cannot satisfy the sensitive rule."""
    design = _design(_case_row("AC-1"), "| AC-2 | b | c | a hostile name |", GOOD_ROWS[2])
    assert _run(tmp_path, _dossier(risk="**Risk**: sensitive\n", design=design)) == (
        ["AC-2: 4 cells; a row has 5", RISK_SENSITIVE], [WARN])


@pytest.mark.parametrize("case", ["wrong-header", "extra-column", "no-trailing-pipe", "no-table", "empty-section"],
                         ids=["wrong-header", "extra-column", "no-trailing-pipe", "no-table", "empty-section"])
def test_the_test_design_header_must_be_exact(tmp_path, case):
    """`empty-section`: the heading with no line under it is a present section with no table, not a missing one.
    `extra-column`: the exact header followed by one more column is not the header, though every row has five cells.
    `no-trailing-pipe`: the header without its closing ` |` is not the header."""
    if case in ("wrong-header", "extra-column", "no-trailing-pipe"):
        header = {"wrong-header": "| Criterion | Behaviour | Failure | Hostile input | Helpers |",
                  "extra-column": TD_HEADER + " Notes |", "no-trailing-pipe": TD_HEADER[:-2]}[case]
        design = _design(*GOOD_ROWS, header=header)
        errors = [TD_HEADER_ERR]
    else:
        design = "## Test design\nNo table yet.\n" if case == "no-table" else "## Test design\n"
        errors = [TD_HEADER_ERR] + ["AC-%d: no row in ## Test design" % k for k in (1, 2, 3)]
    assert _run(tmp_path, _dossier(design=design)) == (errors, [WARN])


@pytest.mark.parametrize("separator, errors", [
    (None, [TD_SEPARATOR_ERR]),
    (TD_SEPARATOR + " junk", [TD_SEPARATOR_ERR, not_an_id("row 1", "---")]),
], ids=["missing", "trailing-junk"])
def test_a_test_design_header_needs_a_separator_row(tmp_path, separator, errors):
    """`trailing-junk`: the separator must be the whole line; with junk after it, it is no separator but a row."""
    assert _run(tmp_path, _dossier(design=_design(*GOOD_ROWS, separator=separator))) == (errors, [WARN])


@pytest.mark.parametrize("criterion, errors", [
    ("AC-1", ["AC-1: more than one row for this criterion"]),
    ("AC-9", [not_an_id("AC-9", "AC-9"), not_an_id("AC-9", "AC-9")]),
], ids=["known", "unknown"])
def test_two_rows_for_one_criterion_are_refused(tmp_path, criterion, errors):
    design = _design(*GOOD_ROWS, _case_row(criterion), *([_case_row(criterion)] if criterion == "AC-9" else []))
    assert _run(tmp_path, _dossier(design=design)) == (errors, [WARN])


@pytest.mark.parametrize("row, count", [
    ("| AC-2 | b | c | d |", 4), ("| AC-2 | b | c | d | e | f |", 6), ("| AC-2 |  | c | d |", 4),
], ids=["four", "six", "four-empty-behaviour"])
def test_a_row_must_have_five_cells(tmp_path, row, count):
    assert _run(tmp_path, _dossier(design=_with_row(2, row))) == (["AC-2: %d cells; a row has 5" % count], [WARN])


@pytest.mark.parametrize("risk, errors", [
    ("high, low, odd", [unknown(3, "high")]),
    ("x" * 100 + ", y", [unknown(2, "x" * 40)]),
], ids=["three-values", "long-value"])
def test_a_bad_risk_line_gives_one_error_naming_the_first_value(tmp_path, risk, errors):
    assert _run(tmp_path, _dossier(risk="**Risk**: %s\n" % risk)) == (errors, [WARN])


@pytest.mark.parametrize("where", ["row", "valid-id-prefix", "risk"], ids=["row", "valid-id-prefix", "risk"])
def test_echoed_values_are_escaped(tmp_path, where):
    """`valid-id-prefix`: a cell that only STARTS like `AC-<n>` is not an id, so its row is named `row <k>` and the
    cell is echoed escaped; an unanchored id match would put the raw escape in the error's prefix."""
    if where in ("row", "valid-id-prefix"):
        cell = "AC-\x1b[2J" if where == "row" else "AC-1\x1b[2J"
        text, error = _dossier(design=_design(*GOOD_ROWS, _case_row(cell))), not_an_id("row 4", cell)
    else:
        text, error = _dossier(risk="**Risk**: \x1b[2J\n"), unknown(1, "\x1b[2J")
    errors, _ = _run(tmp_path, text)
    assert errors == [error] and "\\x1b" in error and "\x1b" not in error


@pytest.mark.parametrize("risk, ids, errors", [
    (["none"], {}, [not_an_id("AC-1", "AC-1")]),
    ([], None, []),
], ids=["empty-ids", "empty-risk"])
def test_only_none_means_unknown(risk, ids, errors):
    """`criterion_ids` None is unknown and skips the id rules; an empty set is known and refuses every row.
    `risk` None is a missing Risk line; a list, even empty, was read. `parse_risk` and `review_tests.criteria`
    never return an empty list (a record holds 3-7 criteria), so these are direct calls on the seam."""
    m = _reuse()
    parsed = m.parse_test_design(_design(_case_row("AC-1")))
    assert m.test_design_errors(parsed, risk, ids) == errors


# -- Task 3: criterion ids and the no-record branch -----------------------------------------------------------

NO_RECORD = "no recorded acceptance criteria for goal 7"


@pytest.mark.parametrize("config", [
    '{"verify": {"enforce": true}}', '{"verify": {"enforce": "yes"}}', '{"verify": ', '{"verify": [1]}', "invalid-record",
], ids=["enforce-true", "enforce-yes", "corrupt-config", "verify-list", "invalid-record"])
def test_no_acceptance_record_is_refused_when_verify_is_enforced(tmp_path, config):
    """A config that cannot be read fails closed: only a missing config.json reads as enforcement off."""
    if config == "invalid-record":
        sdlc, _, _ = _project(tmp_path)
        (sdlc / "config.json").write_text('{"verify": {"enforce": true}}', encoding="utf-8")
        two = ACCEPTANCE.replace("- [ ] The success line counts the rows.\n", "")
        (sdlc / "acceptance" / "7.md").write_text(two, encoding="utf-8")
    else:
        _no_record(tmp_path, config)
    assert _run(tmp_path, _dossier()) == ([NO_RECORD + "; record them before Research"], [WARN])


@pytest.mark.parametrize("config", [None, '{"verify": {"enforce": false}}', "{}"],
                         ids=["no-config", "enforce-false", "no-verify"])
def test_no_acceptance_record_warns_when_verify_is_not_enforced(tmp_path, config):
    """With no ids, an unknown criterion and a missing one are not refused, and every row's cells still are."""
    _no_record(tmp_path, config)
    design = _design(GOOD_ROWS[0], _case_row("AC-9"), _case_row("AC-2", behaviour=""))
    assert _run(tmp_path, _dossier(design=design)) == (
        ["AC-2: behaviour is empty or a dash"], [WARN, NO_RECORD + "; one row per criterion was not checked"])


@pytest.mark.parametrize("rows, errors", [
    (("AC-7", "AC-2", "AC-3"), []),
    (("AC-1", "AC-2", "AC-3"), [not_an_id("AC-1", "AC-1"), "AC-7: no row in ## Test design"]),
], ids=["recorded-ids", "positional-ids"])
def test_criterion_ids_come_from_the_acceptance_record(tmp_path, rows, errors):
    sdlc, _, _ = _project(tmp_path)
    record = ACCEPTANCE.replace("- [ ] A well-formed dossier exits 0.", "- [ ] AC-7: first")
    (sdlc / "acceptance" / "7.md").write_text(record, encoding="utf-8")
    assert _run(tmp_path, _dossier(design=_design(*[_case_row(r) for r in rows]))) == (errors, [WARN])


@pytest.mark.parametrize("text, warning", [
    ("# Research: x\n\n## Goal in one paragraph\nWhat it does.\n", NO_SECTION),
    ("# Research: x\n\n## Reuse\n- `src/app.py`: a helper (line 3).\n", OLD_SECTION),
], ids=["no-reuse", "bare-reuse"])
def test_a_legacy_dossier_is_still_not_checked(tmp_path, text, warning):
    _no_record(tmp_path, '{"verify": {"enforce": true}}')
    assert _run(tmp_path, text) == ([], [warning])


# -- Task 4: the command line ---------------------------------------------------------------------------------

def test_the_cli_success_line_names_both_sections_and_the_criteria_count(tmp_path):
    sdlc, _, head = _project(tmp_path, git=True)
    dossier = sdlc / "research" / "7.md"
    dossier.write_text(_dossier(searched_at=head), encoding="utf-8")
    line = "reuse.py: ## Reuse and ## Test design for goal 7 are well formed (7 rows, 2 queries, %d criteria)\n"
    proc = _cli(tmp_path, "check-research", sdlc, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, line % 3, "")

    (sdlc / "acceptance" / "7.md").unlink()
    proc = _cli(tmp_path, "check-research", sdlc, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        0, line % 0, "reuse.py: no recorded acceptance criteria for goal 7; one row per criterion was not checked\n")

    dossier.write_text(_dossier(searched_at=head, risk=""), encoding="utf-8")
    proc = _cli(tmp_path, "check-research", sdlc, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        2, "", "reuse.py: no recorded acceptance criteria for goal 7; one row per criterion was not checked\n"
        "reuse.py: " + RISK_MISSING + "\n")


# -- Task 5: linear time on 1 MiB of hostile text (epic cost rule) --------------------------------------------

MIB = 1048576


def _fill(head, line, tail=""):
    """`head` + as many copies of `line` as keep the whole text within 1 MiB + `tail`, and the copy count."""
    n = (MIB - len(head) - len(tail)) // len(line)
    return head + line * n + tail, n


def _timed(text, ids):
    m = _reuse()
    start = time.monotonic()
    errors = m.test_design_errors(m.parse_test_design(text), m.parse_risk(text), ids)
    elapsed = time.monotonic() - start
    assert MIB - 1000 < len(text) <= MIB
    return errors, elapsed


IDS = dict.fromkeys(["AC-1", "AC-2", "AC-3"])


def test_many_refusing_test_design_rows_are_checked_in_linear_time():
    """Every row is refused three times; appending by copying the list made this take 22.4 s (prototype)."""
    text, n = _fill("# R\n**Risk**: none\n## Test design\n", "|x|-|n/a|||\n")
    errors, elapsed = _timed(text, IDS)
    assert (len(errors), errors[0], errors[-1]) == (1 + 3 * (n - 1) + 3, TD_HEADER_ERR, "AC-3: no row in ## Test design")
    assert elapsed < 5, "%.1f s for %d rows" % (elapsed, n)


def test_many_malformed_short_rows_are_checked_in_linear_time():
    """One cell per row, no record; appending by copying the list at the cell count took 70.0 s (prototype)."""
    text, n = _fill("# R\n**Risk**: none\n## Test design\n", "|x|\n")
    errors, elapsed = _timed(text, None)
    assert (len(errors), errors[0], errors[-1]) == (n, TD_HEADER_ERR, "row %d: 1 cells; a row has 5" % (n - 1))
    assert elapsed < 5, "%.1f s for %d rows" % (elapsed, n)


def test_many_risk_lines_are_checked_in_linear_time():
    """Thirty-one empty values per line, one error for all of them; rebuilding the list per line took 8.9 s."""
    text, n = _fill("# R\n", "**Risk**:" + "," * 30 + "\n", "## A\n")
    errors, elapsed = _timed(text, IDS)
    assert errors == [unknown(31 * n, ""), TD_MISSING]
    assert elapsed < 5, "%.1f s for %d Risk lines" % (elapsed, n)


@pytest.mark.parametrize("cell", ["behaviour", "failure", "hostile"])
def test_one_mebibyte_cell_is_checked_in_linear_time(cell):
    """A SMOKE test: no quadratic step exists at this seam (one builtin pass per cell, 0.008-0.011 s on the
    prototype). Its control only proves the timing assertion measures the parse."""
    cells = {"behaviour": "b", "failure": "c", "hostile": "d"}
    head = "# R\n**Risk**: sensitive\n## Test design\n%s\n%s\n" % (TD_HEADER, TD_SEPARATOR)
    row = "| AC-1 | %(behaviour)s | %(failure)s | %(hostile)s | e |\n"
    before, after = (row % dict(cells, **{cell: "\0"})).split("\0")
    if cell == "failure":
        before += "none:"
    text, _ = _fill(head + before, " w", after)
    errors, elapsed = _timed(text, dict.fromkeys(["AC-1"]))
    assert errors == []
    assert elapsed < 5, "%.1f s for one cell of %d characters" % (elapsed, len(text))


def test_many_short_lines_above_the_first_heading_are_read_in_linear_time():
    """The header region is read line by line; copying the list per line would be quadratic in the line count."""
    text, n = _fill("# R\n", "x\n", "## A\n")
    errors, elapsed = _timed(text, IDS)
    assert n > 500000 and errors == [RISK_MISSING, TD_MISSING]
    assert elapsed < 5, "%.1f s for %d lines" % (elapsed, n)

"""#2278: `reuse.py check-research` checks the `## Reuse` section of a goal's research dossier.

The dossier is found the way the reviewer brief finds it (working tree, then the goal's branch). A dossier
with no section, or with a bare `## Reuse` heading carrying neither marker line, is legacy: a warning, never a
refusal. In the new format every rule refuses on its own, and the Searched-at commit reaches git only as full
hexadecimal behind `--end-of-options`. Every refusal test asserts the exact error list, so an extra or a missing
error is red."""
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import time

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
REUSE = S / "reuse.py"
SHA = "0123456789abcdef" * 2 + "01234567"          # well formed, in no repository
HEADER = "| ID | Capability | Decision | Evidence | Reason |"
SEPARATOR = "| --- | --- | --- | --- | --- |"
QUERIES = ["- pattern: `def (parse|read)_sections?\\(` paths: `skills tests`", "- pattern: `reuse` paths: `src`"]
_LOCATION = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX")

NO_DOSSIER = "legacy goal (no research dossier); nothing checked"
NO_SECTION = "legacy goal (the research dossier has no ## Reuse section); nothing checked"
OLD_SECTION = "legacy goal (## Reuse was not written from reuse.py brief); nothing checked"

#: #2279: a new-format dossier also carries a Risk line above its first `## ` heading and a `## Test design`
#: table with one row per acceptance criterion, so every fixture below writes both and a record of three.
RISK = "**Risk**: none\n"
TD_HEADER = "| Criterion | Behaviour | Failure path | Hostile input | Helpers |"
TD_SEPARATOR = "| --- | --- | --- | --- | --- |"
DESIGN = "\n".join([
    "## Test design", TD_HEADER, TD_SEPARATOR,
    "| AC-1 | a well-formed dossier exits 0 | a missing file prints one line and exits 2 "
    "| a 1 MiB dossier of malformed rows | tests/test_reuse.py helper `_dossier` |",
    "| AC-2 | every refusal names its row | a bad row exits 2 naming it | n/a | \u2014 |",
    "| AC-3 | the success line counts the rows | none: pure formatting with no failure mode | n/a | \u2014 |",
]) + "\n"
ACCEPTANCE = ('---\nkind: "acceptance"\ngoal: "7"\nverify_command: null\ndrafted: false\n---\n## Done when\n'
              "- [ ] A well-formed dossier exits 0.\n- [ ] Every refusal names its row.\n"
              "- [ ] The success line counts the rows.\n")


def _reuse():
    spec = importlib.util.spec_from_file_location("reuse", REUSE)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if k not in _LOCATION}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                           "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def _project(base, git=False):
    """(sdlc, root, head): `<base>/repo` with an empty `.sdlc/research/`, goal 7's acceptance record (three
    criteria) and two evidence files, and with `git=True` a repository on `main` whose one commit is `head`."""
    root = base / "repo"
    (root / ".sdlc" / "research").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "acceptance").mkdir(exist_ok=True)
    (root / ".sdlc" / "acceptance" / "7.md").write_text(ACCEPTANCE, encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("".join("line %d\n" % n for n in range(1, 11)), encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["tomli"]\n', encoding="utf-8")
    head = None
    if git:
        _git(root, "init", "-q", "-b", "main")
        _git(root, "add", "-A"); _git(root, "commit", "-qm", "base")
        head = _git(root, "rev-parse", "HEAD")
    return root / ".sdlc", root, head


def _row(id="RU-1", cap="a capability", decision="reuse", evidence="src/app.py:3", reason="a reason"):
    return "| %s | %s | %s | %s | %s |" % (id, cap, decision, evidence, reason)


ROWS = [
    _row("RU-1", "find a goal's plan file", "reuse", "src/app.py:3", "the one resolver every gate uses"),
    _row("RU-2", "widen the resolver", "extend", "src/app.py:3-5", "it already reads the file"),
    _row("RU-3", "join paths", "stdlib", "os.path", "standard library"),
    _row("RU-4", "parse the manifest", "dependency", "pyproject.toml:2", "already a dependency"),
    _row("RU-5", "call the registry", "dependency-new", "pypi:requests==2.32.3", "verified: pinned on the registry today"),
    _row("RU-6", "check a dossier section", "new", "—", "no hit for either query"),
    _row("RU-7", "print the line", "new", "-", "nothing like it exists"),
]


def _body(source="grep (knowledge graph disabled)", searched_at=SHA, none=None, queries=QUERIES, rows=ROWS,
          header=HEADER, separator=SEPARATOR):
    """The lines under the `## Reuse` heading. `rows=None` writes no table at all."""
    lines = []
    if source is not None:
        lines.append("**Source:** " + source)
    if searched_at is not None:
        lines.append("**Searched at:** " + searched_at)
    if none is not None:
        lines.append("none: " + none)
    if queries:
        lines += ["**Queries (re-run at Plan-Review and Review):**", *queries]
    if rows is not None:
        lines += ["", header] + ([separator] if separator is not None else []) + rows
    return "\n".join(lines) + "\n"


def _dossier(extra="", risk=RISK, design=DESIGN, **over):
    return ("# Research: x\n" + risk + "\n## Goal in one paragraph\nWhat it does.\n\n## Reuse\n" + _body(**over) + "\n"
            + extra + design)


def _run(base, text, goal="7", m=None, **kw):
    sdlc = base / "repo" / ".sdlc"
    if not sdlc.is_dir():
        _project(base)
    (sdlc / "research").mkdir(parents=True, exist_ok=True)
    (sdlc / "research" / (goal + ".md")).write_text(text, encoding="utf-8")
    return (m or _reuse()).check_research(str(sdlc), goal, **kw)


def _branch_repo(base, text_for):
    """A main checkout on `main` with no dossier on disk, and branch `sdlc/7` carrying `research/7.md` (the
    shape `_goal_branch_repo` in tests/test_review_context.py builds). `text_for(head)` writes the dossier."""
    sdlc, root, head = _project(base, git=True)
    _git(root, "checkout", "-qb", "sdlc/7")
    (sdlc / "research").mkdir(parents=True, exist_ok=True)
    (sdlc / "research" / "7.md").write_text(text_for(head), encoding="utf-8")
    _git(root, "add", "-A"); _git(root, "commit", "-qm", "dossier")
    _git(root, "checkout", "-q", "main")
    assert not (sdlc / "research" / "7.md").exists()
    return sdlc, root, head


# -- Task 1: lookup, legacy decisions, section reader, parse contract ------------------------------------------

def test_a_well_formed_reuse_section_passes(tmp_path):
    _, _, head = _project(tmp_path, git=True)
    assert _run(tmp_path, _dossier(searched_at=head)) == ([], [])
    assert _run(tmp_path, _dossier(searched_at=head), at_research=True) == ([], [])


def test_a_dossier_without_reuse_is_legacy_and_passes_with_a_warning(tmp_path):
    text = "# Research: x\n\n## Goal in one paragraph\nWhat it does.\n\n## Blast radius\n- a file\n"
    assert _run(tmp_path, text) == ([], [NO_SECTION])


def test_an_old_dossier_with_its_own_reuse_heading_is_legacy(tmp_path):
    text = ("# Research: x\n\n## Reuse\n- `evals/skill_structure.py`: `COMPACTION_TOKEN_CAP = 5000` (line 98).\n"
            "- Import caveat: run as a script, load by path with `importlib.util.spec_from_file_location`.\n"
            "- Test pattern: `_load()` by path and a fixture builder (`tests/test_skill_structure.py:36-47`).\n")
    assert _run(tmp_path, text) == ([], [OLD_SECTION])


def test_a_goal_with_no_dossier_is_legacy_and_passes_with_a_warning(tmp_path):
    sdlc, _, _ = _project(tmp_path)
    assert _reuse().check_research(str(sdlc), "7") == ([], [NO_DOSSIER])


def test_a_dossier_that_exists_only_on_the_goal_branch_is_found(tmp_path):
    sdlc, _, _ = _branch_repo(tmp_path, lambda head: _dossier(searched_at=head))
    assert _reuse().check_research(str(sdlc), "7") == ([], [])


def test_the_working_tree_dossier_wins_over_the_branch_copy(tmp_path):
    sdlc, _, _ = _branch_repo(tmp_path, lambda head: _dossier(searched_at=head))
    (sdlc / "research").mkdir(parents=True, exist_ok=True)
    (sdlc / "research" / "7.md").write_text("# Research: x\n\n## Goal in one paragraph\nOld.\n", encoding="utf-8")
    assert _reuse().check_research(str(sdlc), "7") == ([], [NO_SECTION])


def test_a_dossier_over_one_mebibyte_is_refused(tmp_path):
    assert _run(tmp_path, "x" * 1048577) == (["research dossier is over 1048576 characters; nothing checked"], [])
    assert _run(tmp_path, "x" * 1048576) == ([], [NO_SECTION])


@pytest.mark.parametrize("text, warning", [
    ("# Research: x\n\n```\n## Reuse\n" + _body() + "```\n", NO_SECTION),
    ("# Research: x\n\n## Reuse\n```\n" + _body() + "```\n", OLD_SECTION),
    ("# Research: x\n\n## Reuse\n~~~\n" + _body() + "~~~\n", OLD_SECTION),
    ("# Research: x\n\n## Reuse\n  ```\n" + _body() + "  ```\n", OLD_SECTION),
], ids=["heading-fenced", "markers-fenced", "tilde-fenced", "indented-fence"])
def test_a_reuse_heading_or_marker_inside_a_fence_does_not_count(tmp_path, text, warning):
    assert _run(tmp_path, text) == ([], [warning])


def test_a_backtick_line_does_not_close_a_tilde_fence(tmp_path):
    text = "# Research: x\n\n## Reuse\n~~~\n```\n**Searched at:** %s\n~~~\n" % SHA
    assert _run(tmp_path, text) == ([], [OLD_SECTION])


def test_parse_reuse_returns_the_section_as_data():
    text = ("# Research: x\n\n## Reuse\n**Source:** grep (knowledge graph disabled)\n**Searched at:** %s\n"
            "- pattern: `def x\\(` paths: `skills tests`\n- pattern: no backticks paths: `src`\n\n%s\n%s\n"
            "| RU-1 | split a row | reuse | src/app.py:3 | a \\| b |\n" % (SHA, HEADER, SEPARATOR))
    assert _reuse().parse_reuse(text) == {
        "present": True, "source": "grep (knowledge graph disabled)", "searched_at": SHA, "none": None,
        "queries": [("def x\\(", ["skills", "tests"])], "bad_queries": [2], "header": HEADER, "separator": True,
        "rows": [{"id": "RU-1", "capability": "split a row", "decision": "reuse", "evidence": "src/app.py:3",
                  "reason": "a \\| b", "cells": 5}]}


def test_the_reuse_section_ends_at_the_next_heading(tmp_path):
    errors, _ = _run(tmp_path, _dossier(extra="## Constraints\n- not a query line\n| not | a | row |\n"))
    assert errors == []


def test_whitespace_around_a_section_line_is_ignored(tmp_path):
    spaced = "".join(" %s  \n" % line for line in _body().splitlines())
    # Read as the new format (the commit warning), not as legacy: the indented marker lines still count.
    assert _run(tmp_path, "# Research: x\n" + RISK + "\n## Reuse\n" + spaced + "\n" + DESIGN) == (
        [], ["Searched at: commit 0123456 is not on this machine"])


def test_a_sub_heading_inside_the_section_does_not_end_it(tmp_path):
    """Only a `## ` line ends the section: the table under a `### ` sub-heading is still read."""
    table = "\n".join([HEADER, SEPARATOR] + ROWS)
    text = "# Research: x\n" + RISK + "\n## Reuse\n" + _body(rows=None) + "\n### The table\n" + table + "\n\n" + DESIGN
    assert _run(tmp_path, text) == ([], ["Searched at: commit 0123456 is not on this machine"])


@pytest.mark.parametrize("heading", ["## Reuse notes", "## Reuse "], ids=["longer-heading", "trailing-space"])
def test_only_a_line_that_is_exactly_the_heading_opens_the_section(tmp_path, heading):
    text = "# Research: x\n\n%s\n%s\n## Constraints\nnone.\n" % (heading, _body())
    assert _run(tmp_path, text) == ([], [NO_SECTION])


# -- Task 2: Searched at and the commit check -----------------------------------------------------------------

MISSING = "Searched at: missing; write **Searched at:** <the full commit id Research ran at>"
NOT_FULL = "Searched at: not a full commit id (40 or 64 lowercase hexadecimal characters)"
SOURCE_MISSING = "Source: missing; write **Source:** graph@<commit> or grep (<why>)"


def _spy(monkeypatch, m):
    """Every argv the module hands to git, still run for real."""
    calls, real = [], m._run_git

    def spy(argv):
        calls.append(list(argv))
        return real(argv)
    monkeypatch.setattr(m, "_run_git", spy)
    return calls


def test_a_reuse_section_with_only_a_source_line_is_new_format_and_needs_searched_at(tmp_path):
    errors, _ = _run(tmp_path, _dossier(searched_at=None))
    assert errors == [MISSING]


def test_a_reuse_section_with_only_a_searched_at_line_is_new_format(tmp_path):
    errors, _ = _run(tmp_path, _dossier(source=None, searched_at="not-hex"))
    assert errors == [NOT_FULL, SOURCE_MISSING]


def test_an_empty_source_line_still_marks_the_new_format(tmp_path):
    """Legacy is the ABSENCE of both marker lines: `**Source:**` with nothing after it is still a marker."""
    assert _run(tmp_path, _dossier(source="", searched_at=None)) == ([MISSING, SOURCE_MISSING], [])


def test_an_empty_searched_at_line_marks_the_new_format_and_is_refused(tmp_path):
    assert _run(tmp_path, _dossier(source=None, searched_at="")) == ([NOT_FULL, SOURCE_MISSING], [])


def test_a_missing_searched_at_line_is_refused(tmp_path):
    assert _run(tmp_path, _dossier(searched_at=None)) == ([MISSING], [])


def test_a_searched_at_value_that_is_not_hex_is_refused(tmp_path, monkeypatch):
    sdlc, _, _ = _project(tmp_path, git=True)
    m = _reuse()
    calls = _spy(monkeypatch, m)
    assert _run(tmp_path, _dossier(searched_at="--output=pwned"), m=m) == ([NOT_FULL], [])
    assert calls == []
    proc = subprocess.run([sys.executable, str(REUSE), "check-research", str(sdlc), "7"], cwd=tmp_path,
                          capture_output=True, text=True)
    assert proc.returncode == 2
    assert not list(tmp_path.rglob("pwned*")) and not pathlib.Path("pwned").exists()


@pytest.mark.parametrize("value", ["a" * 39, "a" * 41, "a" * 63, "a" * 65, "A" * 40],
                         ids=["hex39", "hex41", "hex63", "hex65", "upper40"])
def test_a_searched_at_of_another_length_is_refused(tmp_path, monkeypatch, value):
    m = _reuse()
    calls = _spy(monkeypatch, m)
    assert _run(tmp_path, _dossier(searched_at=value), m=m) == ([NOT_FULL], [])
    assert calls == []


def test_a_64_character_searched_at_is_accepted(tmp_path):
    sha = hashlib.sha256(b"x").hexdigest()
    assert _run(tmp_path, _dossier(searched_at=sha)) == ([], ["Searched at: commit %s is not on this machine" % sha[:7]])


def test_at_research_requires_the_searched_at_commit_to_exist(tmp_path):
    _, _, head = _project(tmp_path, git=True)
    assert _run(tmp_path, _dossier(searched_at="a" * 40), at_research=True) == (
        ["Searched at: commit aaaaaaa is not on this machine"], [])
    assert _run(tmp_path, _dossier(searched_at=head), at_research=True) == ([], [])


def test_without_at_research_a_missing_searched_at_commit_only_warns(tmp_path):
    _project(tmp_path, git=True)
    assert _run(tmp_path, _dossier(searched_at="a" * 40)) == ([], ["Searched at: commit aaaaaaa is not on this machine"])


def test_the_commit_check_puts_the_commit_behind_end_of_options(tmp_path, monkeypatch):
    sdlc, _, head = _project(tmp_path, git=True)
    m = _reuse()
    calls = _spy(monkeypatch, m)
    assert _run(tmp_path, _dossier(searched_at=head), m=m) == ([], [])
    assert calls == [["git", "-C", str(pathlib.Path(sdlc).resolve().parent), "rev-parse", "--verify", "--quiet",
                      "--end-of-options", head + "^{commit}"]]


def test_the_commit_check_ignores_an_exported_git_dir(tmp_path, monkeypatch):
    _, _, head = _project(tmp_path / "a", git=True)
    other = tmp_path / "b"
    other.mkdir()
    _git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert _run(tmp_path / "a", _dossier(searched_at=head)) == ([], [])


def test_the_commit_check_never_fetches_a_missing_object(tmp_path, monkeypatch):
    """In a partial clone git would fetch an object it lacks from the promisor remote to answer rev-parse; and
    git that hangs is killed after GIT_TIMEOUT (10 s), which the refusal text names."""
    _, _, head = _project(tmp_path, git=True)
    calls, real = [], subprocess.run

    def spy(argv, *a, **kw):
        if "rev-parse" in argv and "--end-of-options" in argv:
            calls.append(((kw.get("env") or {}).get("GIT_NO_LAZY_FETCH"), kw.get("timeout")))
        return real(argv, *a, **kw)
    monkeypatch.setattr(subprocess, "run", spy)
    assert _run(tmp_path, _dossier(searched_at=head)) == ([], [])
    assert calls == [("1", 10)]


@pytest.mark.parametrize("exc, problem", [
    (FileNotFoundError(2, "No such file or directory", "git"), "is not on this machine"),
    (subprocess.TimeoutExpired("git", 10), "could not be checked: git did not answer in 10 seconds"),
], ids=["oserror", "timeout"])
def test_a_git_that_cannot_run_or_answer_is_reported(tmp_path, monkeypatch, exc, problem):
    m = _reuse()

    def broken(argv):
        raise exc
    monkeypatch.setattr(m, "_run_git", broken)
    line = "Searched at: commit 0123456 " + problem
    assert _run(tmp_path, _dossier(), m=m) == ([], [line])
    assert _run(tmp_path, _dossier(), m=m, at_research=True) == ([line], [])


# -- Task 3: the none line and the recorded queries -----------------------------------------------------------

FORM = "query 1: not of the form - pattern: `<regex>` paths: `<path> [<path> ...]`"


def _query_errors(base, line):
    """The errors for a dossier whose first query line is `line` and whose second is good."""
    return _run(base, _dossier(queries=[line, QUERIES[1]]))[0]


def test_the_none_line_replaces_the_table(tmp_path):
    errors, _ = _run(tmp_path, _dossier(none="this goal adds nothing", queries=[], rows=None))
    assert errors == []


def test_the_none_line_needs_three_words(tmp_path):
    errors, _ = _run(tmp_path, _dossier(none="nothing new", queries=[], rows=None))
    assert errors == ["none: needs a reason of three words or more"]


def test_an_empty_none_line_is_still_the_none_line(tmp_path):
    """`none:` with no reason is a none line whose reason is too short, not a section missing its queries."""
    assert _run(tmp_path, _dossier(none="", queries=[], rows=None)) == (
        ["none: needs a reason of three words or more"], ["Searched at: commit 0123456 is not on this machine"])


@pytest.mark.parametrize("queries, rows", [
    ([QUERIES[1]], None), (["- not a query line"], None), ([], [ROWS[0]]),
], ids=["query", "malformed", "table"])
def test_the_none_line_cannot_sit_beside_queries_or_a_table(tmp_path, queries, rows):
    errors, _ = _run(tmp_path, _dossier(none="this goal adds nothing", queries=queries, rows=rows))
    assert errors == ["none: replaces the queries and the table; keep one or the other"]


def test_a_table_beside_the_none_line_is_still_checked(tmp_path):
    errors, _ = _run(tmp_path, _dossier(none="this goal adds nothing", queries=[], rows=[_row(decision="invent")]))
    assert errors == ["none: replaces the queries and the table; keep one or the other",
                      "RU-1: decision 'invent' is not one of reuse extend stdlib dependency dependency-new new"]


def test_zero_queries_are_refused(tmp_path):
    errors, _ = _run(tmp_path, _dossier(queries=[]))
    assert errors == ["queries: 0 recorded; 1 to 25 allowed"]


def test_more_than_25_queries_are_refused(tmp_path):
    many = ["- pattern: `q%d` paths: `src`" % n for n in range(26)]
    assert _run(tmp_path, _dossier(queries=many))[0] == ["queries: 26 recorded; 1 to 25 allowed"]
    assert _run(tmp_path, _dossier(queries=many[:25]))[0] == []


@pytest.mark.parametrize("line", [
    "- pattern: `abc`", "- pattern: `abc` paths: `src` and more", "- pattern: abc paths: `src`",
], ids=["no-paths", "trailing-text", "no-backticks"])
def test_a_malformed_query_line_is_refused(tmp_path, line):
    assert _query_errors(tmp_path, line) == [FORM]


def test_an_empty_pattern_is_refused(tmp_path):
    assert _query_errors(tmp_path, "- pattern: `` paths: `src`") == ["query 1: pattern is 0 characters; 1 to 200 allowed"]


def test_a_pattern_over_200_characters_is_refused(tmp_path):
    assert _query_errors(tmp_path, "- pattern: `%s` paths: `src`" % ("a" * 201)) == [
        "query 1: pattern is 201 characters; 1 to 200 allowed"]
    assert _query_errors(tmp_path, "- pattern: `%s` paths: `src`" % ("a" * 200)) == []


@pytest.mark.parametrize("line, error", [
    ("- pattern: `a\x00b` paths: `src`", "query 1: pattern contains a control character"),
    ("- pattern: `a\tb` paths: `src`", "query 1: pattern contains a control character"),
    ("- pattern: `a` paths: `s\x1brc`", "query 1: path %r contains a control character" % "s\x1brc"),
    ("- pattern: `a` paths: `s\x7frc`", "query 1: path %r contains a control character" % "s\x7frc"),
    ("- pattern: `a\x9bb` paths: `src`", "query 1: pattern contains a control character"),
    ("- pattern: `a` paths: `s\x9brc`", "query 1: path %r contains a control character" % "s\x9brc"),
    ("- pattern: `a` paths: `s\x9frc`", "query 1: path %r contains a control character" % "s\x9frc"),
], ids=["pattern-NUL", "pattern-TAB", "path-ESC", "path-DEL", "pattern-C1-CSI", "path-C1-CSI", "path-C1-APC"])
def test_a_control_character_in_a_pattern_or_path_is_refused(tmp_path, line, error):
    assert _query_errors(tmp_path, line) == [error]


@pytest.mark.parametrize("line", ["- pattern: `a` paths: `src  tests`", "- pattern: `a` paths: ``"],
                         ids=["two-spaces", "empty-cell"])
def test_an_empty_path_is_refused(tmp_path, line):
    assert _query_errors(tmp_path, line) == ["query 1: path '' is empty"]


def test_an_absolute_path_is_refused(tmp_path):
    assert _query_errors(tmp_path, "- pattern: `a` paths: `/etc`") == ["query 1: path '/etc' starts with '/'"]


def test_a_path_starting_with_a_tilde_is_refused(tmp_path):
    assert _query_errors(tmp_path, "- pattern: `a` paths: `~/x`") == ["query 1: path '~/x' starts with '~'"]


def test_a_path_starting_with_a_dash_is_refused(tmp_path):
    assert _query_errors(tmp_path, "- pattern: `a` paths: `-x`") == ["query 1: path '-x' starts with '-'"]


def test_a_path_starting_with_a_colon_is_refused(tmp_path):
    assert _query_errors(tmp_path, "- pattern: `a` paths: `:(top)x`") == ["query 1: path ':(top)x' starts with ':'"]


@pytest.mark.parametrize("path", ["..", "a/../b", "a/.."], ids=["dotdot", "inner", "trailing"])
def test_a_dotdot_path_is_refused(tmp_path, path):
    assert _query_errors(tmp_path, "- pattern: `a` paths: `src %s`" % path) == [
        "query 1: path %r has a '..' segment" % path]
    assert _query_errors(tmp_path, "- pattern: `a` paths: `a..b`") == []


# -- Task 4: the table and the six decisions ------------------------------------------------------------------

NO_ROWS = "table: no rows; a goal that adds no capability writes none: <reason> instead"
NO_SEPARATOR = "table: the header row must be followed by a separator row"
FILE_EVIDENCE = "needs evidence: file:line or file:line-line of a file inside the project"


def _table_errors(base, *rows):
    return _run(base, _dossier(rows=list(rows)))[0]


def test_a_wrong_table_header_is_refused(tmp_path):
    errors, _ = _run(tmp_path, _dossier(header="| ID | Capability | Decision | Evidence | Why |"))
    assert errors == ["table: the header row must be exactly | ID | Capability | Decision | Evidence | Reason |"]


def test_an_aligned_separator_row_is_accepted(tmp_path):
    errors, _ = _run(tmp_path, _dossier(separator="|:---|---| :---: |---:| --- |"))
    assert errors == []


def test_a_missing_separator_row_is_refused(tmp_path):
    errors, _ = _run(tmp_path, _dossier(rows=[ROWS[0]], separator=None))
    assert errors == [NO_SEPARATOR]


def test_a_table_with_only_a_header_row_is_refused(tmp_path):
    errors, _ = _run(tmp_path, _dossier(rows=[], separator=None))
    assert errors == [NO_ROWS, NO_SEPARATOR]


@pytest.mark.parametrize("row, count", [
    ("| RU-1 | find a thing | reuse | no file here |", 4),
    ("| RU-1 | find a thing | bogus | x | y | z |", 6),
], ids=["four", "six"])
def test_a_row_must_have_five_cells(tmp_path, row, count):
    assert _table_errors(tmp_path, row) == ["RU-1: %d cells; a row has 5" % count]
    parsed = _reuse().parse_reuse(_dossier(rows=[row]))
    assert sorted(parsed["rows"][0]) == ["capability", "cells", "decision", "evidence", "id", "reason"]


@pytest.mark.parametrize("bad", ["RU-x", "ru-1", "RU-1a", "X-1"], ids=["letter", "lowercase", "suffix", "prefix"])
def test_a_row_id_must_be_ru_and_a_number(tmp_path, bad):
    assert _table_errors(tmp_path, _row(id=bad)) == ["row 1: id %r is not RU-<number>" % bad]


def test_duplicate_row_ids_are_refused(tmp_path):
    assert _table_errors(tmp_path, _row(), _row(decision="stdlib", evidence="os")) == ["RU-1: id appears more than once"]


def test_an_empty_capability_is_refused(tmp_path):
    assert _table_errors(tmp_path, _row(cap="")) == ["RU-1: capability is empty"]


def test_an_unknown_decision_is_refused(tmp_path):
    assert _table_errors(tmp_path, _row(decision="invent")) == [
        "RU-1: decision 'invent' is not one of reuse extend stdlib dependency dependency-new new"]


@pytest.mark.parametrize("evidence", ["missing.py:3", "src/app.py", "src/app.py:3x", "a" * 300 + ".py:1", "loop-a.py:1",
                                      "src:1"],
                         ids=["missing", "no-line", "trailing-text", "long-name", "symlink-loop", "directory"])
@pytest.mark.parametrize("decision", ["reuse", "extend"], ids=["reuse", "extend"])
def test_reuse_and_extend_need_a_file_that_exists(tmp_path, decision, evidence):
    _, root, _ = _project(tmp_path)
    os.symlink(root / "loop-b.py", root / "loop-a.py")
    os.symlink(root / "loop-a.py", root / "loop-b.py")
    assert _table_errors(tmp_path, _row(decision=decision, evidence=evidence)) == [
        "RU-1: decision %r %s" % (decision, FILE_EVIDENCE)]


@pytest.mark.parametrize("where", ["absolute", "dotdot", "symlink", "sibling"],
                         ids=["absolute", "dotdot", "symlink", "sibling"])
def test_an_evidence_path_outside_the_project_is_refused(tmp_path, where):
    _, root, _ = _project(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    os.symlink(outside, root / "link.py")
    sibling = tmp_path / "repo2" / "x.py"           # its directory's name starts with the project's: still outside
    sibling.parent.mkdir()
    sibling.write_text("x = 1\n", encoding="utf-8")
    os.symlink(sibling, root / "sibling.py")
    evidence = {"absolute": "%s:1" % outside, "dotdot": "../outside.py:1", "symlink": "link.py:1",
                "sibling": "sibling.py:1"}[where]
    assert _table_errors(tmp_path, _row(evidence=evidence)) == ["RU-1: decision 'reuse' " + FILE_EVIDENCE]


def test_evidence_under_a_worktree_path_through_a_symlink_is_inside_the_project(tmp_path):
    """The recorded worktree path can run through a symlink; the file it holds is still inside the project."""
    sdlc, root, head = _project(tmp_path, git=True)
    os.symlink(root, tmp_path / "link")
    (sdlc / "state" / "work").mkdir(parents=True)
    (sdlc / "state" / "work" / "7.json").write_text(json.dumps({"worktree": str(tmp_path / "link")}), encoding="utf-8")
    assert _run(tmp_path, _dossier(searched_at=head)) == ([], [])


def test_evidence_is_looked_for_in_the_goal_worktree_not_the_main_checkout(tmp_path):
    """A file the goal adds exists only in its recorded worktree; one only in the main checkout is no evidence."""
    sdlc, _, _ = _project(tmp_path)
    worktree = tmp_path / "wt"
    (worktree / "lib").mkdir(parents=True)
    (worktree / "lib" / "new.py").write_text("x = 1\n", encoding="utf-8")
    (sdlc / "state" / "work").mkdir(parents=True)
    (sdlc / "state" / "work" / "7.json").write_text(json.dumps({"worktree": str(worktree)}), encoding="utf-8")
    warning = "Searched at: commit 0123456 is not on this machine"
    assert _run(tmp_path, _dossier(rows=[_row(evidence="lib/new.py:1")])) == ([], [warning])
    assert _run(tmp_path, _dossier(rows=[_row(evidence="src/app.py:3")])) == (
        ["RU-1: decision 'reuse' " + FILE_EVIDENCE], [warning])


@pytest.mark.parametrize("name, errors", [("f.py", []), ("ff.py", ["RU-1: decision 'reuse' " + FILE_EVIDENCE])],
                         ids=["4096", "4097"])
def test_evidence_over_4096_characters_is_no_file(tmp_path, name, errors):
    """The bound is on the text, before anything resolves: through a symlink to `.`, 4,096 characters still
    name a real file and 4,097 do not, though both resolve to one."""
    _, root, _ = _project(tmp_path)
    os.symlink(".", root / "x")
    (root / name).write_text("x = 1\n", encoding="utf-8")
    path = "x/" * 2046 + name
    assert len(path) == 4092 + len(name)
    assert _table_errors(tmp_path, _row(evidence=path + ":1")) == errors


def test_an_evidence_path_the_query_rules_refuse_is_refused(tmp_path):
    _, root, _ = _project(tmp_path)
    (root / "-x.py").write_text("x = 1\n", encoding="utf-8")
    assert _table_errors(tmp_path, _row(evidence="-x.py:1")) == ["RU-1: decision 'reuse' " + FILE_EVIDENCE]


@pytest.mark.parametrize("evidence", ["os path", "", "os/path"], ids=["space", "empty", "slash"])
def test_stdlib_needs_a_module_name(tmp_path, evidence):
    assert _table_errors(tmp_path, _row(decision="stdlib", evidence=evidence)) == [
        "RU-1: decision 'stdlib' needs evidence: a module name (letters, digits, dots and underscores)"]


@pytest.mark.parametrize("evidence", ["pyproject.toml", "missing.toml:2", "pyproject.toml:1-2"],
                         ids=["no-line", "missing", "range"])
def test_dependency_needs_a_manifest_line_that_exists(tmp_path, evidence):
    assert _table_errors(tmp_path, _row(decision="dependency", evidence=evidence)) == [
        "RU-1: decision 'dependency' needs evidence: file:line of a manifest or lockfile inside the project"]


@pytest.mark.parametrize("evidence", ["pypi:requests", "cargo:serde==1.0", "pypi:==1.0", "pypi:requests==", "pypi:a==1 x"],
                         ids=["no-version", "unknown-registry", "no-name", "empty-version", "trailing-text"])
def test_dependency_new_needs_registry_name_and_version(tmp_path, evidence):
    row = _row(decision="dependency-new", evidence=evidence, reason="verified: pinned on the registry today")
    assert _table_errors(tmp_path, row) == [
        "RU-1: decision 'dependency-new' needs evidence: <registry>:<name>==<version>, registry one of "
        "pypi npm crates gem go maven nuget other"]


@pytest.mark.parametrize("registry", ["pypi", "npm", "crates", "gem", "go", "maven", "nuget", "other"],
                         ids=["pypi", "npm", "crates", "gem", "go", "maven", "nuget", "other"])
def test_every_listed_registry_is_accepted(tmp_path, registry):
    row = _row(decision="dependency-new", evidence=registry + ":pkg==1.0", reason="verified: pinned on the registry today")
    assert _table_errors(tmp_path, row) == []


@pytest.mark.parametrize("reason", ["checked it on the registry", "verified: two words"], ids=["no-prefix", "two-words"])
def test_dependency_new_needs_a_verified_reason(tmp_path, reason):
    row = _row(decision="dependency-new", evidence="pypi:requests==2.32.3", reason=reason)
    assert _table_errors(tmp_path, row) == [
        "RU-1: decision 'dependency-new' needs a reason of 'verified:' then three words or more"]


def test_new_needs_a_reason_of_three_words(tmp_path):
    row = _row("RU-3", decision="new", evidence="—", reason="two words")
    assert _table_errors(tmp_path, row) == ["RU-3: decision 'new' needs a reason of three words or more"]


def test_new_needs_a_dash_for_evidence(tmp_path):
    row = _row(decision="new", evidence="src/app.py:3", reason="no hit for either query")
    assert _table_errors(tmp_path, row) == ["RU-1: decision 'new' needs evidence: the dash — or -"]


def test_a_section_with_neither_rows_nor_a_none_line_is_refused(tmp_path):
    errors, _ = _run(tmp_path, _dossier(rows=None))
    assert errors == [NO_ROWS]


def test_every_refusal_names_the_row_or_query(tmp_path):
    queries = ["- not a query line", "- pattern: `%s` paths: `src`" % ("a" * 201)]
    rows = [_row("RU-1", decision="invent"), _row("X-1", decision="stdlib", evidence="os"),
            _row("RU-3", decision="new", evidence="—", reason="two words")]
    errors, _ = _run(tmp_path, _dossier(queries=queries, rows=rows))
    assert errors == [
        FORM, "query 2: pattern is 201 characters; 1 to 200 allowed",
        "RU-1: decision 'invent' is not one of reuse extend stdlib dependency dependency-new new",
        "row 2: id 'X-1' is not RU-<number>", "RU-3: decision 'new' needs a reason of three words or more"]
    assert all(re.match(r"(RU-\d+|row \d+|query \d+): ", error) for error in errors)


def test_a_large_malformed_reuse_section_is_checked_in_linear_time(tmp_path):
    """A membership scan of a list per query line made a just-under-1-MiB section of `- x` lines take
    114.5 s of CPU (review B1); 100,000 such lines (about 16 s that way) must check well inside 5 s."""
    n = 100000
    text = "# Research: x\n" + RISK + "\n## Reuse\n**Source:** grep\n**Searched at:** %s\n" % SHA + "- x\n" * n + "\n" + DESIGN
    start = time.monotonic()
    errors, _ = _run(tmp_path, text)
    elapsed = time.monotonic() - start
    assert (len(errors), errors[0], errors[-1]) == (n + 2, "queries: %d recorded; 1 to 25 allowed" % n, NO_ROWS)
    assert elapsed < 5, "%.1f s for %d malformed query lines" % (elapsed, n)


def test_a_one_mebibyte_evidence_path_is_checked_in_linear_time(tmp_path):
    """Resolving a path and testing `root in path.parents` are each quadratic in its depth: one row whose
    evidence was 524,000 one-letter directories took 685.6 s (review B2). It must check well inside 5 s."""
    head = "# Research: x\n%s\n## Reuse\n**Source:** grep\n**Searched at:** %s\n%s\n\n%s\n%s\n| RU-1 | c | reuse | " % (
        RISK, SHA, QUERIES[1], HEADER, SEPARATOR)
    tail = "f.py:1 | a reason |\n\n" + DESIGN
    text = head + "d/" * ((1048576 - len(head) - len(tail)) // 2) + tail
    start = time.monotonic()
    result = _run(tmp_path, text)
    elapsed = time.monotonic() - start
    assert 1048000 < len(text) <= 1048576
    assert result == (["RU-1: decision 'reuse' " + FILE_EVIDENCE], ["Searched at: commit 0123456 is not on this machine"])
    assert elapsed < 5, "%.1f s for one evidence path of %d characters" % (elapsed, len(text))


# -- Task 5: the command line ---------------------------------------------------------------------------------

def _cli(cwd, *args):
    return subprocess.run([sys.executable, str(REUSE), *map(str, args)], capture_output=True, text=True, cwd=cwd)


def test_cli_exit_codes_and_streams(tmp_path):
    sdlc, _, head = _project(tmp_path, git=True)
    dossier = sdlc / "research" / "7.md"
    dossier.write_text(_dossier(searched_at=head), encoding="utf-8")
    proc = _cli(tmp_path, "check-research", sdlc, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        0, "reuse.py: ## Reuse and ## Test design for goal 7 are well formed (7 rows, 2 queries, 3 criteria)\n", "")

    dossier.write_text(_dossier(searched_at=head, rows=ROWS[:5] + [_row("RU-6", decision="new", evidence="—", reason="no hit")]),
                       encoding="utf-8")
    proc = _cli(tmp_path, "check-research", sdlc, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        2, "", "reuse.py: RU-6: decision 'new' needs a reason of three words or more\n")

    dossier.write_text("# Research: x\n\n## Goal in one paragraph\nOld.\n", encoding="utf-8")
    proc = _cli(tmp_path, "check-research", sdlc, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "reuse.py: " + NO_SECTION + "\n")

    dossier.write_text(_dossier(searched_at="a" * 40), encoding="utf-8")
    proc = _cli(tmp_path, "check-research", sdlc, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        0, "reuse.py: ## Reuse and ## Test design for goal 7 are well formed (7 rows, 2 queries, 3 criteria)\n",
        "reuse.py: Searched at: commit aaaaaaa is not on this machine\n")
    proc = _cli(tmp_path, "check-research", sdlc, "7", "--at-research")
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        2, "", "reuse.py: Searched at: commit aaaaaaa is not on this machine\n")

    for args in (("check-research", sdlc), ("bogus", sdlc, "7")):
        proc = _cli(tmp_path, *args)
        assert (proc.returncode, proc.stdout) == (2, "") and proc.stderr.startswith("usage: reuse.py check-research ")


@pytest.mark.parametrize("bad, tail", [(49, ""), (51, "reuse.py: \u2026 and 2 more errors\n")], ids=["50", "52"])
def test_the_cli_prints_the_first_50_errors_and_counts_the_rest(tmp_path, bad, tail):
    """A 1 MiB dossier can hold over 260,000 errors: the console gets the first 50 and a count of the rest, the exit
    code is unchanged, and a direct caller still gets every one."""
    sdlc, _, head = _project(tmp_path, git=True)
    (sdlc / "research" / "7.md").write_text(_dossier(searched_at=head, queries=["- x"] * bad), encoding="utf-8")
    expected = ["queries: %d recorded; 1 to 25 allowed" % bad] + [
        "query %d" % n + FORM[len("query 1"):] for n in range(1, bad + 1)]
    assert _reuse().check_research(str(sdlc), "7") == (expected, [])
    proc = _cli(tmp_path, "check-research", sdlc, "7")
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        2, "", "".join("reuse.py: %s\n" % error for error in expected[:50]) + tail)

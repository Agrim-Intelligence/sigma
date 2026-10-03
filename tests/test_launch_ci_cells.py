"""A doc must not claim a supported cell that CI does not run (#493, launch blocker class B3).

The launch definition (`docs/launch/definition.md` and its twin `definition.json`) lists the
supported (OS, Python) cells. A claim of support on a cell no CI leg runs is a claim nobody checks.
This test derives the cells CI runs by PARSING `.github/workflows/ci.yml` (nothing is hard-coded
here) and fails when:

  - a `supported` entry of `definition.json`, or a `supported` row of a table in the docs, names a
    cell CI does not run;
  - a prose sentence of the form "<Linux|Ubuntu|macOS> with Python <versions>" in the README,
    CONTRIBUTING.md or docs (not the evidence logs, not the changelog, which is history) names a
    version CI does not run on that OS;
  - the Python versions CI runs on Linux but not on macOS are not stated as UNTESTED in both the
    page and the JSON (untested is not unsupported: nothing says they fail).

The prose scan is a regex: a claim worded another way escapes it. The table and JSON checks are
structural. This checks that a leg EXISTS, not that it passes.

The documented gesture (docs/launch/definition.md, "How to change this page"):
`python -m pytest tests/test_launch_ci_cells.py -q`. Controls: the last tests feed each branch a
deliberately broken input and require it to fail.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DEFINITION_MD = ROOT / "docs" / "launch" / "definition.md"
DEFINITION_JSON = ROOT / "docs" / "launch" / "definition.json"
OS_NAMES = {"ubuntu": "linux", "macos": "macos", "windows": "windows"}
SENTENCE = re.compile(
    r"\b(Linux|Ubuntu|macOS)\s+with\s+Python((?:\s*(?:,|and)?\s*3\.\d+(?:\.\d+)?)+)")
VERSION = re.compile(r"3\.\d+")


def _vkey(version):
    return tuple(int(p) for p in version.split("."))


def ci_cells(text):
    """The (os, python) cells of the workflow's matrix `include`, os as linux/macos/windows."""
    pairs = re.findall(r'- os:\s*(\S+)\s*\n\s*python:\s*"([\d.]+)"', text)
    assert pairs and len(pairs) == len(re.findall(r"- os:", text)), \
        "the CI matrix has a cell this parser cannot read (or none): " + repr(pairs)
    cells = set()
    for runner, python in pairs:
        key = runner.split("-", 1)[0].lower()
        assert key in OS_NAMES, "unknown runner in the CI matrix: " + runner
        cells.add((OS_NAMES[key], python))
    return cells


def python_versions(cells, os_name):
    return sorted({p for o, p in cells if o == os_name}, key=_vkey)


def _entry_cells(entry):
    return {(entry["os"], p) for p in entry.get("python", [])} if "os" in entry else set()


def json_supported_cells(definition):
    out = set()
    for entry in definition["supported"]:
        assert "os" in entry and entry.get("python"), "a supported entry names no OS or Python: %r" % entry
        out |= _entry_cells(entry)
    return out


def table_supported_cells(text):
    """(os, python) of every Markdown table row whose last cell is `supported`."""
    out = set()
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip().replace("`", "") for c in line.strip().strip("|").split("|")]
        os_cells = [c for c in cells if c in OS_NAMES.values()]
        if cells and cells[-1] == "supported" and os_cells:
            for cell in cells:
                if re.fullmatch(r"3\.\d+(?:\s*,\s*3\.\d+)*", cell):
                    out |= {(os_cells[0], v) for v in VERSION.findall(cell)}
    return out


def sentence_claims(text):
    """(os, version) named by 'macOS with Python 3.12'-shaped sentences (whitespace collapsed)."""
    flat = re.sub(r"\s+", " ", text)
    out = set()
    for m in SENTENCE.finditer(flat):
        os_name = {"linux": "linux", "ubuntu": "linux", "macos": "macos"}[m.group(1).lower()]
        out |= {(os_name, ".".join(v.split(".")[:2])) for v in re.findall(r"3\.\d+(?:\.\d+)?", m.group(2))}
    return out


def missing(claims, cells):
    return sorted(claims - cells, key=lambda c: (c[0], _vkey(c[1])))


def untested_expected(cells):
    return sorted(set(python_versions(cells, "linux")) - set(python_versions(cells, "macos")), key=_vkey)


def untested_problems(cells, definition, page_text):
    """What is wrong with how the cells CI runs on Linux but not on macOS are stated."""
    want = untested_expected(cells)
    problems = []
    got = sorted({p for e in definition.get("untested", []) if e.get("os") == "macos"
                  for p in e.get("python", [])}, key=_vkey)
    if got != want:
        problems.append("definition.json untested macOS versions %r != %r" % (got, want))
    sentence = " ".join(l for l in re.sub(r"[ \t]*\n[ \t]*", " ", page_text).split(". ")
                        if "untested" in l.lower())
    if "not unsupported" not in sentence.lower():
        problems.append("the page does not say 'untested, not unsupported'")
    for v in want:
        if v not in sentence:
            problems.append("the page's untested sentence omits macOS Python " + v)
    return problems


def doc_files():
    files = [ROOT / "README.md", ROOT / "CONTRIBUTING.md"]
    files += sorted((ROOT / "docs").glob("*.md")) + sorted((ROOT / "docs" / "launch").glob("*.md"))
    return [f for f in files if f.is_file()]  # not docs/launch/evidence/, not CHANGELOG.md


def _cells():
    return ci_cells(WORKFLOW.read_text(encoding="utf-8"))


def test_parser_reads_every_ci_cell():
    cells = _cells()
    text = WORKFLOW.read_text(encoding="utf-8")
    assert len(cells) == text.count("- os:")
    assert ("macos", "3.12") in cells and ("linux", "3.13") in cells, cells


def test_supported_cells_are_cells_ci_runs():
    cells = _cells()
    definition = json.loads(DEFINITION_JSON.read_text(encoding="utf-8"))
    assert not missing(json_supported_cells(definition), cells), \
        "definition.json claims a supported cell CI does not run: %r" % missing(json_supported_cells(definition), cells)
    for path in doc_files():
        bad = missing(table_supported_cells(path.read_text(encoding="utf-8")), cells)
        assert not bad, "%s declares a supported cell CI does not run: %r" % (path.relative_to(ROOT), bad)
    page = table_supported_cells(DEFINITION_MD.read_text(encoding="utf-8"))
    assert page == json_supported_cells(definition), (page, json_supported_cells(definition))


def test_doc_sentences_name_only_cells_ci_runs():
    cells = _cells()
    for path in doc_files():
        bad = missing(sentence_claims(path.read_text(encoding="utf-8")), cells)
        assert not bad, "%s names a cell CI does not run: %r" % (path.relative_to(ROOT), bad)


def test_untested_cells_are_stated_not_claimed():
    definition = json.loads(DEFINITION_JSON.read_text(encoding="utf-8"))
    problems = untested_problems(_cells(), definition, DEFINITION_MD.read_text(encoding="utf-8"))
    assert not problems, problems


def test_controls_each_branch_fails_on_broken_input():
    """Each guard is fed a deliberately broken input and must flag it."""
    cells = _cells()
    macos_versions = python_versions(cells, "macos")
    unrun = "3.13" if "3.13" not in macos_versions else "3.9"
    # (a) a JSON entry and a table row claiming a cell CI does not run
    assert missing(json_supported_cells({"supported": [
        {"os": "macos", "python": [unrun]}]}), cells) == [("macos", unrun)]
    row = "| `claude-code` | `macos` | 3.12, %s | `github` | `supported` |" % unrun
    assert missing(table_supported_cells(row), cells) == [("macos", unrun)]
    assert not missing(table_supported_cells("| a | `linux` | 3.10 | x | `experimental` |"), cells)
    # (b) a sentence claiming one, wrapped and with the Oxford comma, and one that is true
    wrapped = "CI runs it on Linux with Python 3.10, 3.11,\n3.12, and 3.13 and on macOS with\nPython 3.12, %s." % unrun
    assert missing(sentence_claims(wrapped), cells) == [("macos", unrun)]
    assert not missing(sentence_claims("on macOS with Python 3.12.13 and Linux with Python 3.10"), cells)
    # (c) the untested statement missing, wrong, or not saying 'not unsupported'
    want = untested_expected(cells)
    good_json = {"untested": [{"os": "macos", "python": want}]}
    good_page = "The macOS cells on Python %s are untested, not unsupported." % ", ".join(want)
    assert untested_problems(cells, good_json, good_page) == []
    assert untested_problems(cells, {}, good_page)
    assert untested_problems(cells, good_json, "The macOS cells are untested.")
    assert untested_problems(cells, good_json, "Nothing is said here.")
    # a parser that cannot read the workflow refuses rather than reporting no cells
    with pytest.raises(AssertionError):
        ci_cells("matrix:\n  include:\n    - os: ubuntu-latest\n      python: 3.12\n")

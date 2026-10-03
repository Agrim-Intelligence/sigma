"""The README's coverage figures equal the recorded measurement, and CI enforces the stated floor (#194).

The gesture the docs give (docs/launch/coverage.md, "Refreshing"):

    python -m pytest tests/test_coverage_record.py -q

The README states two numbers: the floor CI enforces and the figure last measured. Both live in
`docs/launch/coverage.json`; the floor also lives in `.coveragerc` (`fail_under`) and the CI step that
runs it. Any one of them moving alone makes this file red, naming which. It does not run coverage
(that is a 40-minute suite run); the recorded measurement is what it trusts, and the doc says how that
number was produced and what it does not measure.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "docs" / "launch" / "coverage.json"
DOC = ROOT / "docs" / "launch" / "coverage.md"
README = ROOT / "README.md"
RCFILE = ROOT / ".coveragerc"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: The one sentence the README keeps about coverage: `**<floor>%**` and `**<measured>%**`.
_CLAIM = re.compile(r"line-coverage floor of \*\*(\d+)%\*\*.*?last measured \*\*(\d+\.\d)%\*\*", re.S)
_FAIL_UNDER = re.compile(r"(?m)^\s*fail_under\s*=\s*(\d+(?:\.\d+)?)\s*$")


def findings(readme, record, rcfile, workflow):
    """Every way the stated coverage truth disagrees with itself, as short strings."""
    out = []
    claim = _CLAIM.search(readme)
    if not claim:
        out.append("README states no 'line-coverage floor of **N%** ... last measured **N.N%**' claim")
    else:
        if int(claim.group(1)) != record["floor_percent"]:
            out.append("README floor %s%% != recorded floor %s%%" % (claim.group(1), record["floor_percent"]))
        if claim.group(2) != "%.1f" % record["line_percent"]:
            out.append("README measured %s%% != recorded %.1f%%" % (claim.group(2), record["line_percent"]))
    expected = round(100.0 * (record["statements"] - record["missing"]) / record["statements"], 2)
    if abs(expected - record["line_percent"]) > 0.005:
        out.append("recorded line_percent %s != statements/missing arithmetic %s"
                   % (record["line_percent"], expected))
    if record["floor_percent"] >= record["line_percent"]:
        out.append("floor %s%% is not below the measurement %s%%" % (record["floor_percent"],
                                                                      record["line_percent"]))
    fail = _FAIL_UNDER.search(rcfile)
    if not fail or float(fail.group(1)) != record["floor_percent"]:
        out.append(".coveragerc fail_under %s != recorded floor %s%%"
                   % (fail.group(1) if fail else "missing", record["floor_percent"]))
    if not re.search(r"(?m)^\s*patch\s*=\s*subprocess\s*$", rcfile):
        out.append(".coveragerc does not trace subprocesses (patch = subprocess)")
    for needle in ("python -m coverage run -m pytest tests/", "python -m coverage combine",
                   "python -m coverage erase", "python -m coverage report",
                   "coverage==%s" % record["tool_version"],
                   "if: ${{ matrix.os == 'ubuntu-latest' && matrix.python == '3.12' }}",
                   "if: ${{ !(matrix.os == 'ubuntu-latest' && matrix.python == '3.12') }}"):
        if needle not in workflow:
            out.append("ci.yml does not contain %r" % needle)
    return out


def _real():
    return (README.read_text(encoding="utf-8"), json.loads(RECORD.read_text(encoding="utf-8")),
            RCFILE.read_text(encoding="utf-8"), WORKFLOW.read_text(encoding="utf-8"))


def test_readme_coverage_figures_equal_the_recorded_measurement_and_ci_enforces_the_floor():
    assert findings(*_real()) == []


def test_control_a_readme_figure_that_disagrees_with_the_record_is_red():
    readme, record, rc, wf = _real()
    measured = "%.1f" % record["line_percent"]
    assert findings(readme.replace("**%s%%**" % measured, "**99.9%**"), record, rc, wf) == [
        "README measured 99.9%% != recorded %s%%" % measured]
    floor = str(record["floor_percent"])
    assert findings(readme.replace("floor of **%s%%**" % floor, "floor of **95%**"), record, rc, wf) == [
        "README floor 95%% != recorded floor %s%%" % floor]
    assert findings(_CLAIM.sub("", readme), record, rc, wf)[0].startswith("README states no")


def test_control_a_floor_that_ci_or_the_arithmetic_does_not_back_is_red():
    readme, record, rc, wf = _real()
    assert [f for f in findings(readme, record, _FAIL_UNDER.sub("fail_under = 60", rc), wf)
            if ".coveragerc" in f] == [".coveragerc fail_under 60 != recorded floor %s%%"
                                       % record["floor_percent"]]
    assert any("subprocesses" in f for f in findings(readme, record, rc.replace("subprocess", "x"), wf))
    assert any("ci.yml does not contain 'python -m coverage report'" in f
               for f in findings(readme, record, rc, wf.replace("python -m coverage report", "true")))
    assert any("arithmetic" in f for f in findings(readme, dict(record, missing=record["missing"] + 900),
                                                   rc, wf))
    assert any("not below" in f for f in findings(readme, dict(record, floor_percent=95), rc, wf))


def test_the_doc_carries_the_commands_and_figures_the_record_cites():
    """Every figure in the doc has the command that produced it (the record's `commands`)."""
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    doc = DOC.read_text(encoding="utf-8")
    for command in record["commands"]:
        assert command in doc, command
    for figure in ("%.2f%%" % record["line_percent"], "{:,}".format(record["statements"]),
                   "{:,}".format(record["missing"]), record["tool_version"], record["python"],
                   record["revision"][:7]):
        assert figure in doc, figure
    for gap in ("not attributed", "scrubbed environment", "branch coverage"):
        assert gap in doc, "doc does not name the unmeasured: " + gap


def test_coverage_report_exits_nonzero_below_fail_under(tmp_path):
    """The step's failure is coverage.py's own exit status, so prove that, with the repo's real rcfile
    shape: a scratch project half-covered, `fail_under` above that, `patch = subprocess` and all.
    Skipped where coverage is not installed (four of the five CI legs install nothing extra)."""
    pytest.importorskip("coverage")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "m.py").write_text("def f():\n    return 1\n\n\ndef g():\n    return 2\n")
    (tmp_path / "t.py").write_text("import sys\nsys.path.insert(0, 'skills')\nimport m\nm.f()\n")
    rc = _FAIL_UNDER.sub("fail_under = 90", RCFILE.read_text(encoding="utf-8"))
    (tmp_path / ".coveragerc").write_text(rc)
    env = {k: v for k, v in os.environ.items() if not k.startswith("COVERAGE")}
    env["COVERAGE_RCFILE"] = str(tmp_path / ".coveragerc")

    def run(*args):
        return subprocess.run([sys.executable, "-m", "coverage", *args], cwd=tmp_path, env=env,
                              capture_output=True, text=True)

    assert run("run", "t.py").returncode == 0
    assert run("combine").returncode == 0
    below = run("report")
    assert below.returncode == 2, below.stdout + below.stderr
    assert "fail-under=90" in below.stdout + below.stderr
    (tmp_path / ".coveragerc").write_text(_FAIL_UNDER.sub("fail_under = 40", rc))
    assert run("report").returncode == 0

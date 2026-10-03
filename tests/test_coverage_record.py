"""The README's coverage figure equals the recorded measurement, and nothing claims CI enforces one (#194).

The gesture the docs give (docs/launch/coverage.md, "Refreshing"):

    python -m pytest tests/test_coverage_record.py -q

The README states one measured number and says CI neither measures nor enforces coverage. The number
lives in `docs/launch/coverage.json`. This file goes red when the README and the record disagree, when
the record's arithmetic does not add up, or when CI or the coverage configuration starts enforcing a
floor while the record and README still say nothing is enforced (the day someone adds a gate, they must
update all three, and this names which). It does not run coverage (a forty-minute suite run); it trusts
the recorded measurement, and the doc says how that number was produced and what it does not measure.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "docs" / "launch" / "coverage.json"
DOC = ROOT / "docs" / "launch" / "coverage.md"
README = ROOT / "README.md"
RCFILE = ROOT / ".coveragerc"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: The README's one measured figure: `last measured line coverage of **N.N%**`.
_CLAIM = re.compile(r"last\s+measured\s+line\s+coverage\s+of\s+\*\*(\d+\.\d)%\*\*")
#: What states that CI is not a coverage gate. Both halves must stay in the README.
_NOT_ENFORCED = re.compile(r"CI\s+neither\s+measures\s+nor\s+enforces\s+code\s+coverage")
#: Anything that would make CI measure or enforce coverage.
_ENFORCER = re.compile(r"--cov\b|--cov-fail-under|\bfail_under\b|coverage\s+(?:run|report)\b|pytest-cov")


def findings(readme, record, rcfile, workflow):
    """Every way the stated coverage truth disagrees with itself, as short strings."""
    out = []
    claim = _CLAIM.search(readme)
    if not claim:
        out.append("README states no 'last measured line coverage of **N.N%**' figure")
    elif claim.group(1) != "%.1f" % record["line_percent"]:
        out.append("README measured %s%% != recorded %.1f%%" % (claim.group(1), record["line_percent"]))
    expected = round(100.0 * (record["statements"] - record["missing"]) / record["statements"], 2)
    if abs(expected - record["line_percent"]) > 0.005:
        out.append("recorded line_percent %s != statements/missing arithmetic %s"
                   % (record["line_percent"], expected))
    if record["enforced_in_ci"] is not False:
        out.append("record says coverage is enforced in CI: this guard only knows the not-enforced shape")
    if not _NOT_ENFORCED.search(readme):
        out.append("README no longer says CI neither measures nor enforces coverage")
    for name, text in ((".coveragerc", rcfile), ("ci.yml", workflow)):
        hit = _ENFORCER.search(text)
        if hit:
            out.append("%s contains %r but the record and README say CI does not measure or enforce coverage"
                       % (name, hit.group(0)))
    if not re.search(r"(?m)^\s*patch\s*=\s*subprocess\s*$", rcfile):
        out.append(".coveragerc does not trace subprocesses (patch = subprocess)")
    return out


def _real():
    return (README.read_text(encoding="utf-8"), json.loads(RECORD.read_text(encoding="utf-8")),
            RCFILE.read_text(encoding="utf-8"), WORKFLOW.read_text(encoding="utf-8"))


def test_readme_coverage_figure_equals_the_recorded_measurement_and_nothing_enforces_one():
    assert findings(*_real()) == []


def test_control_a_readme_figure_that_disagrees_with_the_record_is_red():
    readme, record, rc, wf = _real()
    measured = "%.1f" % record["line_percent"]
    assert findings(readme.replace("**%s%%**" % measured, "**99.9%**"), record, rc, wf) == [
        "README measured 99.9%% != recorded %s%%" % measured]
    assert findings(_CLAIM.sub("", readme), record, rc, wf) == [
        "README states no 'last measured line coverage of **N.N%**' figure"]
    assert findings(_NOT_ENFORCED.sub("CI enforces coverage", readme), record, rc, wf) == [
        "README no longer says CI neither measures nor enforces coverage"]


def test_control_an_enforcer_or_a_broken_record_is_red():
    readme, record, rc, wf = _real()
    assert findings(readme, record, rc, wf) == []   # the plants below start from a clean base
    assert any(".coveragerc contains 'fail_under'" in f
               for f in findings(readme, record, rc + "\n[report]\nfail_under = 85\n", wf))
    assert any("ci.yml contains 'coverage run'" in f
               for f in findings(readme, record, rc, wf + "\n      - run: python -m coverage run -m pytest\n"))
    assert any("subprocesses" in f for f in findings(readme, record, rc.replace("subprocess", "x"), wf))
    assert any("arithmetic" in f for f in findings(readme, dict(record, missing=record["missing"] + 900),
                                                   rc, wf))
    assert any("enforced in CI" in f for f in findings(readme, dict(record, enforced_in_ci=True), rc, wf))


def test_the_doc_carries_the_commands_and_figures_the_record_cites():
    """Every figure in the doc has the command that produced it (the record's `commands`)."""
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    doc = DOC.read_text(encoding="utf-8")
    for command in record["commands"]:
        assert command in doc, command
    trial = record["ci_trial"]
    for figure in ("%.2f%%" % record["line_percent"], "{:,}".format(record["statements"]),
                   "{:,}".format(record["missing"]), record["tool_version"], record["python"],
                   record["revision"][:7], "{:,}".format(trial["pytest_step_seconds"]),
                   "{:,}".format(trial["baseline_pytest_step_seconds"]), re.search(r"run (\d+)", trial["run"]).group(1)):
        assert figure in doc, figure
    for gap in ("not attributed", "scrubbed environment", "branch coverage"):
        assert gap in doc, "doc does not name the unmeasured: " + gap

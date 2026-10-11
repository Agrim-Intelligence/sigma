"""Hermetic contract for the supported CI cells in issue #338."""
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
README = ROOT / "README.md"
SAFETY = "python3 evals/safety/check.py"
QUALITY = "      - name: quality gate (Tier-1 behavioral drift vs baseline)\n        run: python3 evals/run.py\n"

EXPECTED_CELLS = (
    ("ubuntu-latest", "3.10"),
    ("ubuntu-latest", "3.11"),
    ("ubuntu-latest", "3.12"),
    ("ubuntu-latest", "3.13"),
    ("macos-latest", "3.12"),
)


def validate_supported_matrix(text):
    """Refuse a workflow that does not declare exactly #338's supported cells."""
    assert "fail-fast: false" in text
    assert "runs-on: ${{ matrix.os }}" in text
    assert "python-version: ${{ matrix.python }}" in text
    assert "timeout-minutes: 90" in text
    assert "python-version:" not in text.split("matrix:", 1)[1].split("steps:", 1)[0]
    for os_name, python in EXPECTED_CELLS:
        expected = "- os: " + os_name + "\n            python: \"" + python + "\""
        assert expected in text, "missing supported CI cell: " + os_name + " / " + python
    assert text.count("- os: ") == len(EXPECTED_CELLS)
    for gate in (
        "python -m pytest tests/ -rs -q",
        "python3 evals/run.py",
        "python3 evals/safety/check.py",
        "python3 tools/rename_check.py",
        "python3 hooks/decision_gate.py validate .",
        "python3 hooks/decision_gate.py check .",
        "tests/test_skill_frontmatter_yaml.py -q",
    ):
        assert gate in text, "missing CI gate: " + gate


def test_ci_declares_exact_supported_matrix_and_gates():
    validate_supported_matrix(WORKFLOW.read_text(encoding="utf-8"))


def test_readme_describes_the_supported_ci_cells():
    assert ("CI runs the full suite on Linux with Python 3.10, 3.11, 3.12, and 3.13, "
            "and on macOS with Python 3.12.") in README.read_text(encoding="utf-8")


def test_control_missing_macos_cell_fails():
    """Red control: the validator must reject the documented macOS omission."""
    text = WORKFLOW.read_text(encoding="utf-8")
    without_macos = text.replace('          - os: macos-latest\n            python: "3.12"\n', "")
    with pytest.raises(AssertionError, match="macos-latest / 3.12"):
        validate_supported_matrix(without_macos)


def job_text(text):
    """The `test` job's text: between '\\n  test:' and '\\n  full:' (not sliced on 'matrix:')."""
    return text.split("\n  test:", 1)[1].split("\n  full:", 1)[0]


def check_safety_step_after_quality_gate(text):
    job = job_text(text)
    assert SAFETY in job, "safety check step missing from the test job"
    assert SAFETY not in text.split("\n  full:", 1)[1], "safety step must not be in the full job"
    steps = job.split("\n      - ")
    names = [st for st in steps if st.startswith("name: ")]
    q = next(i for i, st in enumerate(names) if "quality gate" in st)
    assert SAFETY in names[q + 1], "safety step must follow the quality gate directly"
    assert "name: safety check (secret shapes, sinks, hostile fixtures)" in names[q + 1]


def test_safety_step_is_in_the_pr_test_job_right_after_the_quality_gate():
    check_safety_step_after_quality_gate(WORKFLOW.read_text(encoding="utf-8"))


def test_control_safety_step_removed_from_test_job_fails():
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("      - name: safety check")
    end = text.index("      - name: decision gate", start)
    with pytest.raises(AssertionError, match="missing from the test job"):
        check_safety_step_after_quality_gate(text[:start] + text[end:])


def test_safety_step_spends_nothing_and_has_no_secret_or_write_surface():
    job = job_text(WORKFLOW.read_text(encoding="utf-8"))
    step = job.split("      - name: safety check", 1)[1].split("\n      - ", 1)[0]
    assert "if:" not in step and "env:" not in step and "secrets." not in step
    assert step.strip().endswith(SAFETY)
    assert "run: " + SAFETY in step


def test_docs_that_list_the_quality_gate_list_the_safety_check():
    readme = README.read_text(encoding="utf-8")
    assert "| **Safety check**" in readme and "evals/safety/check.py" in readme
    assert "python3 evals/safety/check.py" in (ROOT / "evals" / "README.md").read_text(encoding="utf-8")
    assert "evals/safety/check.py" in (ROOT / "docs" / "enforcement.md").read_text(encoding="utf-8")
    ci = readme.split("**CI** (GitHub Actions)", 1)[1][:2500]
    assert "evals/safety/check.py" in ci and "`test` job" in ci


def test_safety_check_docstring_states_its_sink_scope():
    doc = (ROOT / "evals" / "safety" / "check.py").read_text(encoding="utf-8").split('"""', 2)[1]
    flat = " ".join(doc.split())
    assert "the four the story names plus the review reason" in flat
    assert "the mirror and the research hook are not probed" in flat


def test_inventory_says_ci_runs_the_safety_check():
    import json
    rows = [r for r in json.loads((ROOT / "docs" / "launch" / "write-surface.json").read_text(encoding="utf-8"))["entries"]
            if r["path"] == "evals/safety/sinks.py"]
    assert len(rows) == 3
    for r in rows:
        assert "CI test job" in r["gate"] and "operator-run" in r["gate"]
        assert "scratch" in r["gate"]

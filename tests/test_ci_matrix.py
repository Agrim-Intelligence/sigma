"""Hermetic contract for the supported CI cells in issue #338."""
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
README = ROOT / "README.md"

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
    assert "timeout-minutes: 60" in text
    assert "python-version:" not in text.split("matrix:", 1)[1].split("steps:", 1)[0]
    for os_name, python in EXPECTED_CELLS:
        expected = "- os: " + os_name + "\n            python: \"" + python + "\""
        assert expected in text, "missing supported CI cell: " + os_name + " / " + python
    assert text.count("- os: ") == len(EXPECTED_CELLS)
    for gate in (
        "python -m pytest tests/ -rs -q",
        "python3 evals/run.py",
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

"""#344 — benchmark method is committed before benchmark execution."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "bench" / "preregistration.md"


def test_preregistration_fixes_the_arms_tasks_metrics_and_analysis_before_runs():
    text = DOC.read_text(encoding="utf-8")
    for heading in ("## Arms", "## Held constant", "## Tasks", "## Isolation", "## Metrics",
                    "## Analysis", "## Go threshold", "## Runs", "## Deviations"):
        assert heading in text
    for arm in ("A1 Sigma", "A2 plain agent", "A3 matched-spend"):
        assert arm in text
    arm_rows = [line for line in text.splitlines() if line.startswith("| A") and line[3].isdigit()]
    assert len(arm_rows) == 3
    assert "about 15 tasks" in text
    assert "one repeat per arm and task" in text
    assert "three traps" in text
    assert "outside the Sigma team" in text
    assert "10,000" in text and "20261001" in text
    assert "−5 points" in text
    assert "frozen task set" in text
    assert "not evidence of superiority or non-inferiority beyond these tasks" in text
    assert "forbidden" in text
    assert "~/.sigma-ops/bench/hidden/" in text


def test_preregistration_deviations_section_is_a_real_post_run_log():
    text = DOC.read_text(encoding="utf-8")
    assert "## Deviations" in text
    assert "2026-10-03" in text
    assert "Reduced launch scope before the first benchmark run" in text
    assert "No benchmark task has run under either design" in text

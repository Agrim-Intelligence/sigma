"""#344 — benchmark method is committed before benchmark execution."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "bench" / "preregistration.md"


def test_preregistration_fixes_the_arms_tasks_metrics_and_analysis_before_runs():
    text = DOC.read_text(encoding="utf-8")
    for heading in ("## Arms", "## Held constant", "## Tasks", "## Isolation", "## Metrics",
                    "## Analysis", "## Go threshold", "## Runs", "## Deviations"):
        assert heading in text
    for arm in ("A1 plain", "A2 prompt-only", "A3 predecessor", "A4 Sigma", "A5 matched spend"):
        assert arm in text
    assert "at least 30" in text
    assert "10,000" in text and "20261001" in text
    assert "−5 points" in text
    assert "~/.sigma-ops/bench/hidden/" in text


def test_preregistration_deviations_section_is_a_real_post_run_log():
    text = DOC.read_text(encoding="utf-8")
    assert "## Deviations" in text
    assert "after the first full run" in text
    assert "date and reason" in text

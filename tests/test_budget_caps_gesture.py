"""#336 — budget controls use the same phase-boundary reporting seam as the loop."""
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "agrim-loop" / "scripts" / "phase_report.py"


def _phase_report():
    spec = importlib.util.spec_from_file_location("phase_report_336", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unpriced_turns_produce_the_specific_budget_warning():
    result = {"unpriced_turns": 2, "turns": 3, "models": ["claude-unknown-9"]}
    assert _phase_report().unpriced_budget_warning(result) == (
        "budget: 2 of 3 turns in this phase are unpriced (model claude-unknown-9 not in the rate card) "
        "-- budget.max_tokens did not count them")


def test_fully_priced_phase_has_no_unpriced_budget_warning():
    assert _phase_report().unpriced_budget_warning({"unpriced_turns": 0, "turns": 3, "models": ["known"]}) is None

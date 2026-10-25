"""#262 — deterministic instruction-budget measurement and its ratchets.

The negative controls deliberately alter a copied budget record or the real, isolated worktree
fixture.  A clean-report assertion alone would pass if `running.md` were never counted.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE = ROOT / "evals" / "phase_context_budget.py"
BUDGET = ROOT / "evals" / "phase_context_budget.json"


def _load():
    spec = importlib.util.spec_from_file_location("phase_context_budget", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _budget_copy(tmp_path):
    target = tmp_path / "budget.json"
    target.write_text(BUDGET.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_every_required_phase_has_a_visible_measurement_and_review_briefs_use_hello_sdlc():
    budget = _load()
    measured = budget.measure_all(ROOT)
    assert set(measured) == {
        "orchestrator", "goal-slot", "research", "plan", "plan-review", "implement",
        "review-pre-pr", "review-post-pr", "retro", "test-review-pre",
    }
    assert all(row["words"] > 0 and row["est_tokens"] > 0 for row in measured.values())
    assert "skills/sigma-loop/references/running.md" in measured["orchestrator"]["files"]
    assert "skills/sigma-loop/references/selection.md" in measured["orchestrator"]["files"]
    assert "skills/sigma-loop/references/running.md" in measured["goal-slot"]["files"]
    assert "skills/sigma-goal/references/selection.md" in measured["goal-slot"]["files"]
    assert "skills/sigma-review/references/axes.md" in measured["review-pre-pr"]["files"]
    assert "skills/sigma-review/references/axes.md" in measured["review-post-pr"]["files"]
    assert any(path.startswith("rendered:review_context.py --for plan-review")
               for path in measured["plan-review"]["files"])
    assert any(path.startswith("rendered:review_context.py --for code-review")
               for path in measured["review-pre-pr"]["files"])
    assert any(path.startswith("rendered:review_context.py --for pr-review")
               for path in measured["review-post-pr"]["files"])


def test_review_fixture_measurement_is_stable_and_contains_the_goal_and_real_brief_content():
    budget = _load()
    first = budget.measure_all(ROOT)
    second = budget.measure_all(ROOT)
    assert first == second
    payload = budget._review_brief(ROOT, "plan-review")
    assert "Add an exclamation" in payload
    assert "Independent review brief - plan-review" in payload
    assert str(ROOT) not in payload


def test_omitting_a_documented_phase_input_turns_the_committed_ratchet_red(monkeypatch):
    """A shorter map cannot silently pass by making the current ceiling look generous."""
    budget = _load()
    original = budget.PHASES["review-pre-pr"]
    monkeypatch.setitem(budget.PHASES, "review-pre-pr", {
        **original,
        "files": tuple(path for path in original["files"] if not path.endswith("axes.md")),
    })
    findings = budget.findings(ROOT, BUDGET)
    assert any("review-pre-pr: ceiling words" in item and "more than 5%" in item
               for item in findings), findings


def test_committed_budget_is_fresh_and_the_documented_command_prints_measurement_metadata():
    budget = _load()
    findings = budget.findings(ROOT, BUDGET)
    assert findings == [], findings
    result = subprocess.run([sys.executable, str(MODULE)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "measurement date:" in result.stdout
    assert "measurement commit:" in result.stdout
    assert "orchestrator" in result.stdout


def test_a_ceiling_more_than_five_percent_above_measurement_fails(tmp_path):
    budget = _load()
    copied = _budget_copy(tmp_path)
    data = json.loads(copied.read_text(encoding="utf-8"))
    data["phases"]["research"]["ceiling"]["words"] = 10**9
    copied.write_text(json.dumps(data), encoding="utf-8")
    findings = budget.findings(ROOT, copied)
    assert any("research: ceiling words" in item and "more than 5%" in item for item in findings)


def test_a_measurement_above_the_committed_ceiling_fails(tmp_path):
    budget = _load()
    copied = _budget_copy(tmp_path)
    data = json.loads(copied.read_text(encoding="utf-8"))
    data["phases"]["research"]["ceiling"]["words"] = 0
    copied.write_text(json.dumps(data), encoding="utf-8")
    findings = budget.findings(ROOT, copied)
    assert any("research: measured words" in item and "exceeds ceiling" in item for item in findings)


def test_documented_gate_turns_red_for_a_planted_500_word_running_reference():
    """The exact documented gesture catches a real added instruction, then restores the fixture."""
    running = ROOT / "skills" / "sigma-loop" / "references" / "running.md"
    original = running.read_text(encoding="utf-8")
    try:
        running.write_text(original + "\n\n" + ("planted-budget-word " * 500), encoding="utf-8")
        result = subprocess.run([sys.executable, str(MODULE)], cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 1
        assert "orchestrator: measured words" in result.stdout
        assert "goal-slot: measured words" in result.stdout
    finally:
        running.write_text(original, encoding="utf-8")


#: #2005 FR-61: the measurement at the slice's base (`cf8b372`) for every phase whose prose it moved.
_BASE_TOKENS = {"orchestrator": 32188, "goal-slot": 12416, "plan-review": 3653, "review-pre-pr": 6229,
                "review-post-pr": 6307, "implement": 3050}
_UNTOUCHED_BASE_TOKENS = {"research": 2999, "plan": 1239, "retro": 3886}


def test_test_review_pre_phase_measures_the_test_review_reference():
    """#2005 D1: a budget-only phase for the pre-implementation test reviewer's payload (provisional until
    its writer lands in slices 7/10), with a committed ceiling within the 5% rule."""
    budget = _load()
    assert "test-review-pre" in budget.PHASES
    assert budget.PHASES["test-review-pre"] == {"files": ("skills/sigma-review/references/test-review.md",)}
    committed = json.loads(BUDGET.read_text(encoding="utf-8"))["phases"]
    assert "ceiling" in committed.get("test-review-pre", {}), committed.keys()
    assert not [f for f in budget.findings(ROOT, BUDGET) if f.startswith("test-review-pre")]


def test_lean_prose_moves_lowered_every_touched_ceiling():
    """#2005 FR-61: every phase the prose moves touched measures strictly below its base measurement, and
    its committed ceiling was lowered below that base too; untouched phases did not grow; and (FR-61's own
    words) no phase measures above its committed ceiling -- so putting back any one moved section is red even
    where the slice's other edits in that phase net negative."""
    budget = _load()
    measured = budget.measure_all(ROOT)
    committed = json.loads(BUDGET.read_text(encoding="utf-8"))["phases"]
    for name, row in measured.items():
        assert row["est_tokens"] <= committed[name]["ceiling"]["est_tokens"], (name, row, committed.get(name))
    for name, base in _BASE_TOKENS.items():
        assert measured[name]["est_tokens"] < base, (name, measured[name]["est_tokens"], base)
        assert committed[name]["ceiling"]["est_tokens"] < base, (name, committed[name]["ceiling"], base)
    for name, base in _UNTOUCHED_BASE_TOKENS.items():
        assert measured[name]["est_tokens"] <= base, (name, measured[name]["est_tokens"], base)

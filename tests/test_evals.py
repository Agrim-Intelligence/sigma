"""Quality-eval pipeline (evals/run.py). Tier-1 is the deterministic behavioral gate: run the real
hook over the fixture corpus, score it, fail on drift vs the committed baseline. These tests pin that
the gate scores the corpus clean today, actually CATCHES a regression, and that the Tier-2 LLM-judge
seam works hermetically (injected agent/judge) without spending anything."""
import json, pathlib, importlib.util

ROOT = pathlib.Path(__file__).resolve().parent.parent
R = ROOT / "evals" / "run.py"


def _run():
    spec = importlib.util.spec_from_file_location("evals_run", R)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_fixtures_wellformed():
    fx = json.loads((ROOT / "evals" / "fixtures.json").read_text())
    assert fx, "corpus must not be empty"
    ids = [f["id"] for f in fx]
    assert len(ids) == len(set(ids)), "fixture ids must be unique"
    for f in fx:
        assert f["expect"] in {"code", "ask", "standard"}
        assert isinstance(f["tier2_rubric"], list) and f["tier2_rubric"], "each needs a Tier-2 rubric"


def test_tier1_scores_corpus_clean():
    """Integration: the REAL deterministic hook classifies every fixture as expected (score 1.0)."""
    m = _run()
    score, misses = m.run_tier1(m.load_fixtures())
    assert misses == [], f"corpus drift: {misses}"
    assert score == 1.0


def test_baseline_not_regressed():
    m = _run()
    score, _ = m.run_tier1(m.load_fixtures())
    floor = json.loads((ROOT / "evals" / "baseline.json").read_text())["tier1_min"]
    assert score >= floor, f"quality drift: {score} < baseline {floor}"


def test_gate_catches_drift():
    """The gate must FAIL when a signal regresses — inject a hook that always returns the base policy
    (so 'code' fixtures no longer see the CODE CHANGE banner) and assert misses + a sub-1.0 score."""
    m = _run()
    flat = lambda p: json.dumps({"hookSpecificOutput": {"additionalContext": "GOAL-BASED SDLC policy"}})
    score, misses = m.run_tier1(m.load_fixtures(), run=flat)
    assert score < 1.0 and any(x["expect"] == "code" for x in misses)


def test_tier2_seam_hermetic():
    """Tier-2 runs its shape with injected fakes — no LLM, proves the agent->judge wiring is ready."""
    m = _run()
    out = m.run_tier2(m.load_fixtures(), agent=lambda p: "stub output", judge=lambda o, r: 1.0)
    assert out and all(row["score"] == 1.0 for row in out)


def test_main_gate_passes():
    assert _run().main([]) == 0


def test_live_is_parked():
    """--live must not spend: it raises the parked notice until a real LLM judge is greenlit."""
    import pytest
    with pytest.raises(SystemExit):
        _run().main(["--live"])

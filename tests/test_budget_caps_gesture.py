"""#336 — budget controls use the same phase-boundary reporting seam as the loop."""
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "sigma-loop" / "scripts" / "phase_report.py"
DOCTOR = ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py"
LOOP = ROOT / "skills" / "sigma-loop" / "scripts" / "loop.py"


def _phase_report():
    spec = importlib.util.spec_from_file_location("phase_report_336", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doctor():
    spec = importlib.util.spec_from_file_location("doctor_336", DOCTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _loop():
    spec = importlib.util.spec_from_file_location("loop_336", LOOP)
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


def test_doctor_flags_recent_unpriced_phase_models(tmp_path):
    """The doctor control reads the durable phase-end shape, not an in-memory report."""
    events = tmp_path / "events"
    events.mkdir()
    (events / "worker.jsonl").write_text(
        '{"kind":"phase","state":"end","ts":"2033-05-18T03:33:20Z",'
        '"unpriced_turns":2,"model":"claude-unknown-9"}\n', encoding="utf-8")
    state = _doctor()._rate_card_coverage_state(tmp_path, now=2_000_000_000.0)
    assert state.startswith("MISSING"), state
    assert "claude-unknown-9" in state
    assert "anthropic_list_prices.csv" in state


def test_doctor_accepts_recent_fully_priced_phase_models(tmp_path):
    events = tmp_path / "events"
    events.mkdir()
    (events / "worker.jsonl").write_text(
        '{"kind":"phase","state":"end","ts":"2033-05-18T03:33:20Z",'
        '"unpriced_turns":0,"model":"claude-sonnet-5"}\n', encoding="utf-8")
    state = _doctor()._rate_card_coverage_state(tmp_path, now=2_000_000_000.0)
    assert state.startswith("READY"), state


def _next_fixture(tmp_path, budget):
    """One pending local goal whose counters are seeded through production helpers."""
    sdlc = tmp_path / ".sdlc"
    (sdlc / "goals").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps({
        "discovery": {"source": "local-goals"}, "budget": budget,
    }), encoding="utf-8")
    (sdlc / "goals" / "0001.md").write_text(
        "---\nid: 0001\nstatus: pending\n---\ncontrol goal\n", encoding="utf-8")
    return sdlc


@contextmanager
def _live_session(sdlc):
    """Use the actual session registry and a genuinely live PID for documented refills."""
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    loop = _loop()
    try:
        loop.session_start(sdlc, owner.pid)
        loop.state.start_run(sdlc)
        assert loop.session_active(sdlc, loop.state.load_config(sdlc))
        yield loop, owner.pid
    finally:
        owner.terminate()
        owner.wait(timeout=5)


def _next(sdlc, session_pid):
    return subprocess.run(
        [sys.executable, str(LOOP), "next", str(sdlc),
         "--session-pid", str(session_pid)], capture_output=True, text=True, check=False)


def _assert_budget_refusal(sdlc, session_pid, cap):
    """The same `loop.py next --session-pid` gesture documented for normal refills."""
    result = _next(sdlc, session_pid)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "BUDGET", result.stdout
    assert cap in result.stderr
    assert "status: pending" in (sdlc / "goals" / "0001.md").read_text(encoding="utf-8")


def test_documented_next_refuses_configured_iteration_cap_through_live_session(tmp_path):
    """The goal-count cap reads a settled live-session admission, not a hand-written cursor."""
    sdlc = _next_fixture(tmp_path / "iterations", {"max_iterations": 1})
    with _live_session(sdlc) as (loop, session_pid):
        # Settle an actual session admission; editing run_iteration would be only a legacy
        # compatibility fixture and would miss the live session counter production reads.
        loop._session_claim(sdlc, session_pid, "completed-control-goal")
        loop._session_release(sdlc, "completed-control-goal")
        _assert_budget_refusal(sdlc, session_pid, "max_iterations")


def test_documented_next_refuses_configured_minutes_cap(tmp_path):
    """The documented refill refuses the wall-clock cap from state.start_run's real timestamp."""
    # The production start helper owns the timestamp; this tiny positive ceiling is already spent
    # by the time the documented subprocess gesture reaches the resource admission gate.
    sdlc = _next_fixture(tmp_path / "minutes", {"max_minutes": 1e-9})
    with _live_session(sdlc) as (_loop_module, session_pid):
        _assert_budget_refusal(sdlc, session_pid, "max_minutes")


def test_documented_next_refuses_configured_token_cap(tmp_path):
    """The documented refill reads spend recorded by the production token-state helper."""
    sdlc = _next_fixture(tmp_path / "tokens", {"max_tokens": 1})
    with _live_session(sdlc) as (loop, session_pid):
        loop.state.add_tokens(sdlc, 1)
        _assert_budget_refusal(sdlc, session_pid, "max_tokens")


def test_documented_next_refuses_configured_codex_raw_token_cap(tmp_path):
    """The documented refill reads phase-end Codex usage from the production state helper."""
    sdlc = _next_fixture(tmp_path / "codex", {"max_codex_raw_tokens": 1})
    with _live_session(sdlc) as (loop, session_pid):
        loop.state.record_phase_end(sdlc, "budget-control", time.time(), codex_raw_tokens=1)
        _assert_budget_refusal(sdlc, session_pid, "max_codex_raw_tokens")


def test_documented_next_admits_when_no_budget_cap_is_configured(tmp_path):
    sdlc = _next_fixture(tmp_path, {})
    with _live_session(sdlc) as (_loop_module, session_pid):
        result = _next(sdlc, session_pid)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(sdlc / "goals" / "0001.md"), result.stdout
        assert "status: in_progress" in (sdlc / "goals" / "0001.md").read_text(encoding="utf-8")

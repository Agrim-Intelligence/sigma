"""#955: a second session's `loop.py start` must not re-anchor a live session's run.

Session A is this process; session B is a sleeping child (a real, distinct, live pid). Every call
into the code under test is wrapped by `_quiet`, so a failing test carries no captured output (#956).
The pins at the bottom pass before the fix by design and are not in the plan's `## Tests`."""
import contextlib, importlib.util, io, json, os, pathlib, subprocess, sys, time

import pytest

S = pathlib.Path(__file__).resolve().parents[1] / "skills" / "sigma-loop" / "scripts"
_ENV = ("SIGMA_RUN_ID", "SIGMA_SESSION_GENERATION", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
        "CLAUDECODE", "CLAUDE_CODE_SESSION_ID")
_THREAD = "01a03637-7800-7000-8000-00000000000%d"


def _mod(name, path=None):
    spec = importlib.util.spec_from_file_location(name, path or S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


@pytest.fixture(autouse=True)
def _clean_env():
    saved = {k: os.environ.pop(k, None) for k in _ENV}
    yield
    for k, v in saved.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v


@pytest.fixture
def other():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    try:
        yield proc
    finally:
        proc.kill(); proc.wait()


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
        rc = fn(*a, **k)
    return rc, out.getvalue(), err.getvalue()


def _sdlc(tmp_path, goals=3, budget=None):
    base = tmp_path / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    (base / "config.json").write_text(json.dumps({"budget": budget or {"max_iterations": 10}}))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "state" / "review-queue.md").write_text("# Q\n")
    for i in range(1, goals + 1):
        (base / "goals" / f"{i:04d}.md").write_text(f"---\nid: {i:04d}\nstatus: pending\n---\nx\n")
    return str(base)


def _tokens(limit=1000):
    return {"max_iterations": 10, "max_tokens": limit}


def _start(lp, base, pid):
    """The documented gesture, in process. `start` arms SIGMA_RUN_ID and the generation in THIS
    process only; verify/record/next run as separate processes in real use, so drop both."""
    result = _quiet(lp.main, ["loop.py", "start", base, "--session-pid", str(pid)])
    os.environ.pop("SIGMA_RUN_ID", None); os.environ.pop("SIGMA_SESSION_GENERATION", None)
    return result


def _cli(lp, *argv):
    return _quiet(lp.main, ["loop.py", *argv])[0]


def _pick(lp, base, pid):
    cfg = lp.state.load_config(base)
    return _quiet(lp._next, base, lp.sources.get_source(base, cfg), cfg, session_pid=pid)[0]


def _green(lp, base, goal):
    """What a passing `loop.py verify` leaves for done_refusal (no acceptance record, no worktree)."""
    ev = lp.state.evidence_path(base, goal); ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(json.dumps({"command": "true", "exit": 0, "at": time.time(), "run": None}))


def _end(pr, base, goal, epoch, **result):
    """`phase_report.py end`'s budget half for one phase whose start marker says `epoch`."""
    full = {"source": "claude-code", "tokens_in": 1, "tokens_out": 1, "models": ["claude-sonnet-5"],
            "unpriced_turns": 0, "cost_usd": None, "cost_equivalent_tokens": None}
    full.update(result)
    _quiet(pr._record_end_usage, base, goal, "research",
           {"phase": "research", "ts_start_epoch": epoch}, full)


def _clock(monkeypatch, lp, value):
    monkeypatch.setattr(lp.time, "time", lambda: value)


# --- red before the fix --------------------------------------------------------------------------

def test_live_sessions_evidence_survives_a_second_start(tmp_path, other):
    """AC-1, the issue's repro: A starts, claims, verifies green; B starts; A's evidence stands."""
    lp = _mod("loop"); base = _sdlc(tmp_path); a = os.getpid()
    assert _start(lp, base, a)[0] == 0
    kind, goal = _pick(lp, base, a)
    _green(lp, base, goal)
    assert _start(lp, base, other.pid)[0] == 0
    refusal = lp._done_refusal(base, goal)
    assert refusal is None, refusal


def test_second_start_keeps_live_sessions_tokens(tmp_path, other):
    """AC-2 tokens: B's start neither zeroes A's spend nor hands it to B."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget=_tokens()); a = os.getpid()
    _start(lp, base, a); kind, goal = _pick(lp, base, a)
    assert _cli(lp, "spend", base, "700", goal) == 0
    _start(lp, base, other.pid)
    assert _cli(lp, "spend", base, "400", goal) == 0
    assert _pick(lp, base, a) == ("BUDGET", "1100 tokens >= max_tokens 1000")
    assert _pick(lp, base, other.pid)[0] == "goal"


def test_second_start_keeps_live_sessions_minutes(tmp_path, other, monkeypatch):
    """AC-2 minutes: A's wall clock runs from A's own start, not B's."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget={"max_iterations": 10, "max_minutes": 1})
    t0 = time.time(); a = os.getpid()
    _clock(monkeypatch, lp, t0); _start(lp, base, a)
    _clock(monkeypatch, lp, t0 + 120); _start(lp, base, other.pid)
    assert _pick(lp, base, a) == ("BUDGET", "elapsed 2min >= max_minutes 1")
    assert _pick(lp, base, other.pid)[0] == "goal"


def test_inflight_phase_is_credited_to_its_owner(tmp_path, other, monkeypatch):
    """A phase A began before B's start is charged to A when it ends, never dropped."""
    lp = _mod("loop"); pr = _mod("phase_report"); base = _sdlc(tmp_path, budget=_tokens())
    t0 = time.time(); a = os.getpid()
    _clock(monkeypatch, lp, t0); _start(lp, base, a); kind, goal = _pick(lp, base, a)
    _clock(monkeypatch, lp, t0 + 2); _start(lp, base, other.pid)
    _clock(monkeypatch, lp, t0 + 3); _end(pr, base, goal, t0 + 1, cost_equivalent_tokens=1200)
    assert _pick(lp, base, a) == ("BUDGET", "1200 tokens >= max_tokens 1000")
    assert _pick(lp, base, other.pid)[0] == "goal"


def test_inflight_codex_phase_is_credited_to_its_owner(tmp_path, other, monkeypatch):
    """The same for the Codex raw-token ceiling."""
    lp = _mod("loop"); pr = _mod("phase_report")
    base = _sdlc(tmp_path, budget={"max_iterations": 10, "max_codex_raw_tokens": 1000})
    t0 = time.time(); a = os.getpid()
    _clock(monkeypatch, lp, t0); _start(lp, base, a); kind, goal = _pick(lp, base, a)
    _clock(monkeypatch, lp, t0 + 2); _start(lp, base, other.pid)
    _clock(monkeypatch, lp, t0 + 3)
    _end(pr, base, goal, t0 + 1, source="codex", tokens_in=600, tokens_out=600, models=[])
    assert _pick(lp, base, a) == ("BUDGET", "1200 Codex raw tokens >= max_codex_raw_tokens 1000")
    assert _pick(lp, base, other.pid)[0] == "goal"


def test_release_keeps_the_sessions_run_block(tmp_path, other):
    """D-1: a partial registry write (release) must not drop A's run."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget=_tokens()); a = os.getpid()
    _start(lp, base, a); kind, goal = _pick(lp, base, a)
    _cli(lp, "spend", base, "700", goal)
    _start(lp, base, other.pid)
    _quiet(lp._session_release, base, goal)
    _cli(lp, "spend", base, "400")
    assert _pick(lp, base, a) == ("BUDGET", "1100 tokens >= max_tokens 1000")
    assert _pick(lp, base, other.pid)[0] == "goal"


def test_session_write_preserves_unknown_keys(tmp_path):
    """D-1 at the seam: `_session_write` keeps every key its caller did not pass."""
    lp = _mod("loop"); path = tmp_path / "1.active"
    path.write_text(json.dumps({"in_flight": [], "settled_admissions": 0, "generation": "g",
                                "run": {"x": 1}}))
    lp._session_write(path, {"in_flight": ["a"], "settled_admissions": 1})
    assert json.loads(path.read_text()).get("run") == {"x": 1}


def test_run_stop_dedupe_survives_a_second_start(tmp_path, other, monkeypatch):
    """D-3, and AC-2 for the goal count: A's repeated stop is one row; B's count is its own."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget={"max_iterations": 1}); a = os.getpid()
    calls = []
    monkeypatch.setattr(lp, "_emit_run_stop", lambda *args, **kw: calls.append(args[2]))
    _start(lp, base, a)
    os.environ["SIGMA_RUN_ID"] = f"session-{a}"
    assert _pick(lp, base, a)[0] == "goal"
    assert _pick(lp, base, a)[0] == "BUDGET"
    os.environ.pop("SIGMA_RUN_ID")
    _start(lp, base, other.pid)
    os.environ["SIGMA_RUN_ID"] = f"session-{a}"
    assert _pick(lp, base, a)[0] == "BUDGET"
    assert calls == ["budget"]
    os.environ.pop("SIGMA_RUN_ID")
    assert _pick(lp, base, other.pid)[0] == "goal"


def test_start_run_with_session_pid_resets_only_that_session(tmp_path, other):
    """`start-run --session-pid` resets that session's run and no other."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget=_tokens()); a = os.getpid()
    _start(lp, base, a); _start(lp, base, other.pid)
    assert _cli(lp, "spend", base, "1200") == 0
    assert _cli(lp, "start-run", base, "--session-pid", str(a)) == 0
    assert _pick(lp, base, a)[0] == "goal"
    assert _pick(lp, base, other.pid) == ("BUDGET", "1200 tokens >= max_tokens 1000")


def test_bare_start_run_keeps_concurrent_sessions(tmp_path, other):
    """A bare `start-run` with two live sessions resets neither, and says so on stderr."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget=_tokens()); a = os.getpid()
    _start(lp, base, a); _start(lp, base, other.pid)
    _cli(lp, "spend", base, "1200")
    rc, out, err = _quiet(lp.main, ["loop.py", "start-run", base])
    assert rc == 0 and out == ""
    assert _pick(lp, base, a) == ("BUDGET", "1200 tokens >= max_tokens 1000")
    assert _pick(lp, base, other.pid) == ("BUDGET", "1200 tokens >= max_tokens 1000")
    assert "--session-pid" in err, err


def test_owner_credit_list_is_capped(tmp_path, other, monkeypatch):
    """SCALABILITY: a session's credit list stops at 4096 attempts, loudly, like STATE.md's."""
    lp = _mod("loop"); pr = _mod("phase_report"); base = _sdlc(tmp_path, budget=_tokens(10**9))
    t0 = time.time(); a = os.getpid()
    _clock(monkeypatch, lp, t0); _start(lp, base, a); kind, goal = _pick(lp, base, a)
    marker = lp._session_marker_path(base, a)
    data = json.loads(marker.read_text())
    data.setdefault("run", {})["token_credits"] = [f"{i:024x}" for i in range(4096)]
    marker.write_text(json.dumps(data))
    _clock(monkeypatch, lp, t0 + 1)
    raised = None
    try:
        _end(pr, base, goal, t0 + 1, cost_equivalent_tokens=5)
    except RuntimeError as exc:
        raised = str(exc)
    assert raised and "4096" in raised, raised


def test_doctor_credits_include_live_session_runs(tmp_path, other, monkeypatch):
    """LIVENESS: doctor's credit set includes an attempt credited only to its owning session."""
    lp = _mod("loop"); pr = _mod("phase_report"); base = _sdlc(tmp_path, budget=_tokens())
    t0 = time.time(); a = os.getpid()
    _clock(monkeypatch, lp, t0); _start(lp, base, a); kind, goal = _pick(lp, base, a)
    _clock(monkeypatch, lp, t0 + 2); _start(lp, base, other.pid)
    _end(pr, base, goal, t0 + 1, cost_equivalent_tokens=10)
    key = lp.state.phase_attempt_key(f"{goal}\0research\0{t0 + 1}\0")
    assert key in lp.state.run_token_credits(base)


def test_run_loop_session_keeps_evidence_across_start(tmp_path, other):
    """BR-4: `run_loop`'s own start path stamps its session's run too."""
    lp = _mod("loop"); base = _sdlc(tmp_path, goals=1); seen = []

    def run_goal(goal):
        _green(lp, base, goal)
        _start(lp, base, other.pid)
        seen.append(lp._done_refusal(base, goal))
        return ("done", "")

    _quiet(lp.run_loop, base, run_goal)
    assert seen == [None]


def test_drain_session_keeps_evidence_across_start(tmp_path, other):
    """BR-5: `assign._start_drain`'s start path stamps its session's run too."""
    lp = _mod("loop"); base = _sdlc(tmp_path)
    assign = _mod("assign_955", S.parents[1] / "sigma-scope" / "scripts" / "assign.py")
    cfg = lp.state.load_config(base)
    result = _quiet(assign._start_drain, base, cfg, lp.sources.get_source(base, cfg))[0]
    assert result["picked_kind"] == "goal", result
    _green(lp, base, result["picked"])
    _start(lp, base, other.pid)
    refusal = lp._done_refusal(base, result["picked"])
    assert refusal is None, refusal


def test_codex_tasks_sharing_a_pid_keep_their_own_runs(tmp_path):
    """Host-agnostic: two Codex tasks under one host pid are two sessions with two runs."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget=_tokens()); a = os.getpid()
    os.environ["CODEX_THREAD_ID"] = _THREAD % 1
    _start(lp, base, a); kind, goal = _pick(lp, base, a)
    _green(lp, base, goal)
    _cli(lp, "spend", base, "1200", goal)
    os.environ["CODEX_THREAD_ID"] = _THREAD % 2
    _start(lp, base, a)
    assert _pick(lp, base, a)[0] == "goal"
    os.environ["CODEX_THREAD_ID"] = _THREAD % 1
    refusal = lp._done_refusal(base, goal)
    assert refusal is None, refusal
    assert _pick(lp, base, a) == ("BUDGET", "1200 tokens >= max_tokens 1000")


# --- pins: pass before the fix by design (AC-3, AC-4, and the credit rules' controls) ---------

def test_single_session_restart_refuses_old_evidence(tmp_path):
    """AC-3: re-`start` of the SAME session still voids its own earlier green."""
    lp = _mod("loop"); base = _sdlc(tmp_path); a = os.getpid()
    _start(lp, base, a); kind, goal = _pick(lp, base, a)
    _green(lp, base, goal)
    _start(lp, base, a)
    assert lp._done_refusal(base, goal) == "verify evidence predates this run"


def test_ownerless_goal_keeps_the_global_anchor(tmp_path, other):
    """AC-3 / D-7: a goal no live session holds is anchored to the checkout's latest start."""
    lp = _mod("loop"); base = _sdlc(tmp_path)
    _start(lp, base, os.getpid())
    goal = str(pathlib.Path(base) / "goals" / "0003.md")
    _green(lp, base, goal)
    _start(lp, base, other.pid)
    assert lp._done_refusal(base, goal) == "verify evidence predates this run"


def test_single_session_start_writes_identical_state_md(tmp_path, monkeypatch):
    """AC-3: one `start`, the same STATE.md bytes and the same one-line stdout as before."""
    lp = _mod("loop"); base = _sdlc(tmp_path)
    t = float(int(time.time())) + 0.25
    _clock(monkeypatch, lp, t)
    rc, out, err = _start(lp, base, os.getpid())
    assert rc == 0 and len(out.splitlines()) == 1
    assert (pathlib.Path(base) / "state" / "STATE.md").read_text() == (
        f"iteration: 0\nrun_iteration: 0\nlast_run: none\nrun_started_at: {t}\n"
        "run_tokens: 0\nrun_token_credits: []\nrun_codex_raw_tokens: 0\n"
        "run_codex_token_credits: {}\nrun_phase_ends: []\n")


def test_dead_session_neither_blocks_start_nor_anchors(tmp_path, other):
    """AC-4: a killed session's run anchors nothing, and a new `start` is unaffected."""
    lp = _mod("loop"); base = _sdlc(tmp_path)
    _start(lp, base, other.pid); kind, goal = _pick(lp, base, other.pid)
    _green(lp, base, goal)
    other.kill(); other.wait()
    assert _cli(lp, "start-run", base) == 0
    assert lp._done_refusal(base, goal) == "verify evidence predates this run"
    rc, out, err = _start(lp, base, os.getpid())
    assert rc == 0 and len(out.split()) == 1 and len(out.strip()) == 32, (out, err)
    assert "refus" not in err.lower(), err


def test_unattributed_spend_charges_every_live_session(tmp_path, other):
    """AC-2 no silent defeat: spend naming no goal, or an unheld goal, charges every live session."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget=_tokens()); a = os.getpid()
    _start(lp, base, a); _start(lp, base, other.pid)
    assert _cli(lp, "spend", base, "600") == 0
    assert _cli(lp, "spend", base, "600", str(pathlib.Path(base) / "goals" / "0003.md")) == 0
    assert _pick(lp, base, a) == ("BUDGET", "1200 tokens >= max_tokens 1000")
    assert _pick(lp, base, other.pid) == ("BUDGET", "1200 tokens >= max_tokens 1000")


def test_phase_before_owner_start_is_not_credited(tmp_path, monkeypatch):
    """A phase that began before its owner's own (re)start is not charged to the new run."""
    lp = _mod("loop"); pr = _mod("phase_report"); base = _sdlc(tmp_path, budget=_tokens())
    t0 = time.time(); a = os.getpid()
    _clock(monkeypatch, lp, t0); _start(lp, base, a); kind, goal = _pick(lp, base, a)
    _clock(monkeypatch, lp, t0 + 2); _start(lp, base, a)
    _end(pr, base, goal, t0 + 1, cost_equivalent_tokens=1200)
    assert _pick(lp, base, a)[0] == "goal"


def test_retried_phase_end_credits_owner_once(tmp_path, other, monkeypatch):
    """RESILIENCY: a retried phase end is charged to its owner once."""
    lp = _mod("loop"); pr = _mod("phase_report"); base = _sdlc(tmp_path, budget=_tokens())
    t0 = time.time(); a = os.getpid()
    _clock(monkeypatch, lp, t0); _start(lp, base, a); kind, goal = _pick(lp, base, a)
    _clock(monkeypatch, lp, t0 + 2); _start(lp, base, other.pid)
    _end(pr, base, goal, t0 + 1, cost_equivalent_tokens=600)
    _end(pr, base, goal, t0 + 1, cost_equivalent_tokens=600)
    assert _pick(lp, base, a)[0] == "goal"


def test_bare_start_run_resets_the_only_live_session(tmp_path):
    """AC-3 for `start-run`: with one live session a bare `start-run` still resets its budget."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget=_tokens()); a = os.getpid()
    _start(lp, base, a)
    _cli(lp, "spend", base, "1200")
    assert _cli(lp, "start-run", base) == 0
    assert _pick(lp, base, a)[0] == "goal"


def test_credit_writes_keep_marker_mtime(tmp_path, other):
    """A budget credit never refreshes a marker's mtime, the lease-TTL liveness backstop."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget=_tokens()); a = os.getpid()
    _start(lp, base, a); _start(lp, base, other.pid); kind, goal = _pick(lp, base, a)
    markers = [_quiet(lp._session_marker_path, base, pid)[0] for pid in (a, other.pid)]
    old = time.time_ns() - 3600 * 10**9
    for marker in markers:
        os.utime(marker, ns=(old, old))
    before = [marker.stat().st_mtime_ns for marker in markers]
    assert _cli(lp, "spend", base, "300") == 0
    assert _cli(lp, "spend", base, "300", goal) == 0
    assert [marker.stat().st_mtime_ns for marker in markers] == before


def test_bad_block_in_other_session_does_not_block_spend(tmp_path, other):
    """A malformed run block in one live session never fails charge-to-all spend for the others."""
    lp = _mod("loop"); base = _sdlc(tmp_path, budget=_tokens()); a = os.getpid()
    _start(lp, base, a); _start(lp, base, other.pid)
    marker = _quiet(lp._session_marker_path, base, other.pid)[0]
    data = json.loads(marker.read_text())
    data["run"] = {"started_at": "bad"}
    marker.write_text(json.dumps(data))
    assert _cli(lp, "spend", base, "1200") == 0
    assert _pick(lp, base, a) == ("BUDGET", "1200 tokens >= max_tokens 1000")

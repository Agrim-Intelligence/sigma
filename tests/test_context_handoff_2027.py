"""#2027: HANDOFF on measured context, derived from the session's first pick, capped by the window.

Plan: `.sdlc/plans/2027.md` (+ `2027-review.md`). Covers the pure resolver slice 4 imports
(`_context_threshold`, `_explicit_context_tokens`), the trip at `_session_count_stop`, the one
meter reading per invocation threaded to every gate, the window sources, the end + start bound,
and the docs / doctor / template / prose sync.
"""
import builtins
import json
import os
import pathlib
import re

import test_context_meter_2026 as m26

ROOT = pathlib.Path(__file__).resolve().parent.parent
SID = m26.SID


def _loop():
    return m26._load("loop")


lp = _loop()


def _base(tmp_path, n=3, cfg=None):
    base = pathlib.Path(m26._backlog(tmp_path, n))
    (base / "config.json").write_text(json.dumps(cfg if cfg is not None else
                                                 {"budget": {"max_iterations": 10}}))
    return str(base)


def _host(tokens, first, window=None):
    block = {"source": "host", "tokens": tokens, "session": SID, "first_pick": first}
    if window is not None:
        block["window"] = window
    return block


# --- Task 1: the explicit-value rule and the pure resolver ---------------------------------------

def test_explicit_context_tokens_accepts_positive_ints_and_integral_floats_only():
    f = lp._explicit_context_tokens
    assert f({"after_context_tokens": 300000}) == 300000
    assert f({"after_context_tokens": 300000.0}) == 300000
    for bad in (0, 0.0, None, True, False, -5, 1.5, "300000", [1]):
        assert f({"after_context_tokens": bad}) is None, bad
    assert f({}) is None and f(None) is None and f("x") is None


def test_threshold_is_three_times_the_first_pick():
    assert lp._context_threshold({}, {"first_pick": 70000})[0] == 210000
    assert lp._context_threshold({}, {"first_pick": 150000})[0] == 450000


def test_threshold_is_capped_at_45_percent_of_the_window():
    assert lp._context_threshold({}, {"first_pick": 400000})[0] == 450000          # 1M fallback
    assert lp._context_threshold({}, {"first_pick": 60000, "window": 258400})[0] == 116280
    assert lp._context_threshold({}, {"first_pick": 88000, "window": 420000})[0] == 189000


def test_explicit_value_wins_and_is_not_capped():
    assert lp._context_threshold({"after_context_tokens": 600000}, {"first_pick": 70000}) == (
        600000, "handoff.after_context_tokens")
    assert lp._context_threshold({"after_context_tokens": 300000}, None)[0] == 300000


def test_explicit_zero_turns_the_context_trigger_off():
    assert lp._context_threshold({"after_context_tokens": 0}, {"first_pick": 70000}) is None
    assert lp._context_threshold({"after_context_tokens": 0.0}, {"first_pick": 70000}) is None


def test_malformed_explicit_values_read_as_derived():
    for bad in (True, "x", -5, 1.5):
        assert lp._context_threshold({"after_context_tokens": bad}, {"first_pick": 70000})[0] == 210000


def test_disabled_handoff_returns_none_with_a_baseline_and_with_an_explicit_key():
    assert lp._context_threshold({"enabled": False}, {"first_pick": 70000}) is None
    assert lp._context_threshold({"enabled": False, "after_context_tokens": 300000}, None) is None


def test_no_baseline_and_no_explicit_key_returns_none():
    assert lp._context_threshold({}, None) is None
    assert lp._context_threshold({}, {"first_pick": None}) is None
    assert lp._context_threshold(None, None) is None


def test_slice_four_call_shape_with_handoff_none():
    assert lp._context_threshold(None, {"first_pick": 70000})[0] == 210000


def test_the_resolver_reads_the_baseline_not_the_current_reading():
    low = {"source": "host", "tokens": 10, "first_pick": 70000}
    high = {"source": "host", "tokens": 900000, "first_pick": 70000}
    gone = {"source": "unavailable", "first_pick": 70000}
    assert lp._context_threshold({}, low) == lp._context_threshold({}, high) == \
        lp._context_threshold({}, gone)
    assert lp._context_threshold({}, gone)[0] == 210000


def test_both_are_module_level_and_pure(monkeypatch):
    assert callable(lp._context_threshold) and callable(lp._explicit_context_tokens)
    before = lp._context_threshold({}, {"first_pick": 70000, "window": 258400})
    monkeypatch.setattr(os, "environ", {})

    def boom(*a, **k):
        raise AssertionError("the resolver did I/O")
    monkeypatch.setattr(builtins, "open", boom)
    monkeypatch.setattr(pathlib.Path, "read_text", boom)
    assert lp._context_threshold({}, {"first_pick": 70000, "window": 258400}) == before
    assert lp._explicit_context_tokens({"after_context_tokens": 5}) == 5


# --- Task 2: the trip at the chokepoint -----------------------------------------------------------

def _stop(tmp_path, cfg, context):
    base = _base(tmp_path, 0, cfg)
    return lp._session_count_stop(base, os.getpid(), lp.state.load_config(base), context=context)


def test_trips_at_the_threshold_and_not_one_below(tmp_path):
    kind, reason = _stop(tmp_path, {}, _host(210000, 70000))
    assert kind == "HANDOFF"
    assert "210,000" in reason and "3x first pick 70,000" in reason and "cap 450,000" in reason
    assert reason.startswith("context 210,000 tokens >= handoff threshold 210,000")
    assert _stop(tmp_path / "b", {}, _host(209999, 70000)) is None


def test_still_trips_with_after_goals_zero_and_no_max_iterations(tmp_path):
    assert _stop(tmp_path, {"handoff": {"after_goals": 0}}, _host(300000, 70000))[0] == "HANDOFF"


def test_disabled_handoff_turns_both_triggers_off(tmp_path):
    base = _base(tmp_path, 0, {"handoff": {"enabled": False}})
    for _ in range(25):
        lp.state.advance_cursor(base, "goal")
    assert lp._session_count_stop(base, 12345, lp.state.load_config(base),
                                  context=_host(900000, 70000)) is None


def test_meter_unavailable_leaves_the_goal_count_alone(tmp_path):
    base = _base(tmp_path, 0, {})
    for _ in range(20):
        lp.state.advance_cursor(base, "goal")
    config = lp.state.load_config(base)
    expected = ("HANDOFF", "20 admitted goals >= handoff.after_goals 20")
    assert lp._session_count_stop(base, 12345, config, context=None) == expected
    gone = {"source": "unavailable", "session": SID, "first_pick": 10, "reason": "no transcript"}
    assert lp._session_count_stop(base, 12345, config, context=gone) == expected
    cfg = {"handoff": {"after_goals": 0, "after_context_tokens": 5}}
    assert _stop(tmp_path / "x", cfg, gone) is None
    guessed = {"source": "estimate", "session": SID, "tokens": 900000, "first_pick": 10}
    assert _stop(tmp_path / "y", cfg, guessed) is None      # only a `host` reading can trip


def test_a_session_never_trips_at_its_own_first_pick(tmp_path):
    """D7: an explicit value below a fresh start, or the Codex cap on a big repo, must still let a
    session do one goal; otherwise the supervisor relaunches every 60 s with zero claims."""
    cfg = {"handoff": {"after_context_tokens": 50000}}
    assert _stop(tmp_path, cfg, _host(88000, 88000)) is None
    assert _stop(tmp_path / "b", cfg, _host(88001, 88000))[0] == "HANDOFF"
    assert _stop(tmp_path / "c", {}, _host(130000, 130000, window=258400)) is None


# --- Task 3: one reading per invocation, the same object to every gate, the CLI ------------------

def _carried(tmp_path, monkeypatch, base, pid, first=70000, now=300000):
    """A session whose first pick read `first` and whose transcript now reads `now`."""
    lp.begin_run(base, pid)
    m26._session_at(tmp_path, monkeypatch, first)
    lp._record_context(base, pid)
    m26._session_at(tmp_path, monkeypatch, now)


def _local(base, cfg):
    (pathlib.Path(base) / "config.json").write_text(json.dumps(cfg))
    return lp.state.load_config(base)


def test_cli_next_prints_bare_handoff_with_context_on_stderr_and_claims_nothing(tmp_path, monkeypatch,
                                                                                capsys):
    base = _base(tmp_path, 2); pid = os.getpid()
    _carried(tmp_path, monkeypatch, base, pid)
    capsys.readouterr()
    lp.main(["loop.py", "next", base, "--session-pid", str(pid)])
    out = capsys.readouterr()
    assert out.out.strip() == "HANDOFF"
    assert "HANDOFF (context 300,000 tokens >= handoff threshold 210,000" in out.err
    assert "status: pending" in (pathlib.Path(base) / "goals" / "0001.md").read_text()


def test_cli_next_batch_prints_bare_handoff_and_claims_nothing(tmp_path, monkeypatch, capsys):
    base = _base(tmp_path, 3); pid = os.getpid()
    _local(base, {"budget": {"max_iterations": 10}, "work": {"enabled": True},
                  "parallel": {"goals": {"enabled": True, "max_concurrent": 3}}})
    _carried(tmp_path, monkeypatch, base, pid)
    capsys.readouterr()
    lp.main(["loop.py", "next-batch", base, "--session-pid", str(pid)])
    out = capsys.readouterr()
    assert out.out.strip() == "HANDOFF"
    for i in (1, 2, 3):
        assert "status: pending" in (pathlib.Path(base) / "goals" / f"{i:04d}.md").read_text()


def _count_readings(monkeypatch):
    lsm = lp._load("loop_session_model")
    calls = []
    real = lsm.context_reading

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)
    lsm.context_reading = counted
    real_load = lp._load
    monkeypatch.setattr(lp, "_load", lambda name: lsm if name == "loop_session_model" else real_load(name))
    return calls


def test_the_meter_is_read_once_per_next(tmp_path, monkeypatch):
    base = _base(tmp_path, 2); pid = os.getpid()
    _carried(tmp_path, monkeypatch, base, pid, now=100000)
    calls = _count_readings(monkeypatch)
    config = lp.state.load_config(base)
    assert lp._next(base, lp.sources.get_source(base, config), config, session_pid=pid)[0] == "goal"
    assert len(calls) == 1


def test_the_meter_is_read_once_per_next_batch_of_three(tmp_path, monkeypatch):
    base = _base(tmp_path, 3); pid = os.getpid()
    config = _local(base, {"budget": {"max_iterations": 10}})
    _carried(tmp_path, monkeypatch, base, pid, now=100000)
    calls = _count_readings(monkeypatch)
    picks = lp.next_batch(base, lp.sources.get_source(base, config), config, max_concurrent=3,
                          session_pid=pid)
    assert [k for k, _ in picks] == ["goal", "goal", "goal"]
    assert len(calls) == 1


def test_the_meter_is_read_outside_the_session_lock(tmp_path, monkeypatch):
    base = _base(tmp_path, 1); pid = os.getpid()
    _carried(tmp_path, monkeypatch, base, pid, now=100000)
    held = []
    real_locked = lp._session_locked

    def locked(*a, **k):
        held.append(True)
        try:
            return real_locked(*a, **k)
        finally:
            held.pop()
    monkeypatch.setattr(lp, "_session_locked", locked)
    lsm = lp._load("loop_session_model")
    real = lsm.context_reading

    def reading(*a, **k):
        assert not held, "the meter was read under the session lock"
        return real(*a, **k)
    lsm.context_reading = reading
    real_load = lp._load
    monkeypatch.setattr(lp, "_load", lambda name: lsm if name == "loop_session_model" else real_load(name))
    config = lp.state.load_config(base)
    assert lp._next(base, lp.sources.get_source(base, config), config, session_pid=pid)[0] == "goal"


def test_every_count_stop_call_receives_the_identical_block(tmp_path, monkeypatch):
    """One spy for all three gates (sweep, pick, reserve): each break of one pass-through turns
    exactly this test red."""
    base = _base(tmp_path, 1); pid = os.getpid()
    _carried(tmp_path, monkeypatch, base, pid, now=100000)
    captured, seen = [], []
    real_record, real_stop = lp._record_context, lp._session_count_stop

    def record(*a, **k):
        captured.append(real_record(*a, **k)); return captured[-1]

    def stop(*a, **k):
        seen.append(k.get("context")); return real_stop(*a, **k)
    monkeypatch.setattr(lp, "_record_context", record)
    monkeypatch.setattr(lp, "_session_count_stop", stop)
    config = lp.state.load_config(base)
    assert lp._next(base, lp.sources.get_source(base, config), config, session_pid=pid)[0] == "goal"
    assert len(captured) == 1 and isinstance(captured[0], dict)
    assert len(seen) == 3 and all(block is captured[0] for block in seen)


def test_next_batch_passes_its_prologue_block_to_every_slot(tmp_path, monkeypatch):
    base = _base(tmp_path, 3); pid = os.getpid()
    config = _local(base, {"budget": {"max_iterations": 10}})
    _carried(tmp_path, monkeypatch, base, pid, now=100000)
    captured, seen = [], []
    real_record, real_next = lp._record_context, lp._next

    def record(*a, **k):
        captured.append(real_record(*a, **k)); return captured[-1]

    def nxt(*a, **k):
        seen.append(k.get("context")); return real_next(*a, **k)
    monkeypatch.setattr(lp, "_record_context", record)
    monkeypatch.setattr(lp, "_next", nxt)
    lp.next_batch(base, lp.sources.get_source(base, config), config, max_concurrent=3, session_pid=pid)
    assert len(captured) == 1 and len(seen) == 3 and all(b is captured[0] for b in seen)


def test_the_github_sweeps_skip_on_a_context_handoff(tmp_path, monkeypatch):
    base = _base(tmp_path, 1); pid = os.getpid()
    _carried(tmp_path, monkeypatch, base, pid)
    swept = []
    monkeypatch.setattr(lp, "_feature_needs_label_sweep", lambda *a, **k: swept.append(1))
    config = lp.state.load_config(base)
    assert lp._next(base, lp.sources.get_source(base, config), config, session_pid=pid)[0] == "HANDOFF"
    assert swept == []


def test_a_lock_failure_still_returns_the_block_and_hands_off(tmp_path, monkeypatch, capsys):
    base = _base(tmp_path, 1); pid = os.getpid()
    _carried(tmp_path, monkeypatch, base, pid)
    real_locked = lp._session_locked
    state = {"first": True}

    def locked(sdlc_dir, session_pid, fn, *a, **k):
        if state["first"]:
            state["first"] = False
            raise OSError("lock unavailable")
        return real_locked(sdlc_dir, session_pid, fn, *a, **k)
    monkeypatch.setattr(lp, "_session_locked", locked)
    capsys.readouterr()
    lp.main(["loop.py", "next", base, "--session-pid", str(pid)])
    out = capsys.readouterr()
    assert out.out.strip() == "HANDOFF" and "first pick 70,000" in out.err


# --- Task 4: the cap on a huge baseline, and the window sources ----------------------------------

def test_a_resumed_session_with_a_huge_baseline_still_trips_under_the_cap(tmp_path):
    """Acceptance 2: uncapped, 3 x 400,000 = 1,200,000 would never trip before host compaction."""
    assert _stop(tmp_path, {}, _host(460000, 400000))[0] == "HANDOFF"


def test_codex_reading_carries_the_model_window_and_the_cap_uses_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CODEX_THREAD_ID", m26.CODEX_ID)
    line = m26._codex_request(120000)
    line["payload"]["info"]["model_context_window"] = 258400
    m26._codex_rollout(tmp_path / "codex", [line])
    reading = lp._load("loop_session_model").context_reading(home=tmp_path / "home")
    assert reading.get("window") == 258400 and reading.get("tokens") == 120000
    kind, reason = _stop(tmp_path, {}, {**reading, "first_pick": 60000})
    assert kind == "HANDOFF" and "116,280" in reason


def test_claude_reading_carries_the_auto_compact_window_env(tmp_path, monkeypatch):
    m26._session_at(tmp_path, monkeypatch, 70000)
    lsm = lp._load("loop_session_model")
    monkeypatch.setenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "420000")
    assert lsm.context_reading().get("window") == 420000
    for bad in ("abc", "0", "-1", ""):
        monkeypatch.setenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW", bad)
        assert "window" not in lsm.context_reading(), bad
    monkeypatch.delenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    assert "window" not in lsm.context_reading()


# --- Task 5: finding 4 is bounded by the cap ------------------------------------------------------

def test_end_and_start_in_one_transcript_rebaselines_and_the_cap_still_bounds_it(tmp_path, monkeypatch):
    """Plan D8: `session_end` unlinks the heartbeat, so the next first pick re-baselines at the
    current context. Known cost: such a session runs to the cap, not 3x its true start."""
    base = _base(tmp_path, 3); pid = os.getpid()
    generation = lp.begin_run(base, pid)
    m26._session_at(tmp_path, monkeypatch, 70000)
    lp._record_context(base, pid)
    lp.session_end(base, pid, generation=generation)
    lp.begin_run(base, pid)
    config = lp.state.load_config(base)
    source = lp.sources.get_source(base, config)
    m26._session_at(tmp_path, monkeypatch, 300000)
    assert lp._next(base, source, config, session_pid=pid)[0] == "goal"
    assert m26._context(lp, base, pid)["first_pick"] == 300000
    m26._session_at(tmp_path, monkeypatch, 449999)
    assert lp._next(base, source, config, session_pid=pid)[0] == "goal"
    m26._session_at(tmp_path, monkeypatch, 450000)
    assert lp._next(base, source, config, session_pid=pid)[0] == "HANDOFF"


# --- Task 6: config, docs, prose, doctor, enforcement ---------------------------------------------

def _template():
    return json.loads((ROOT / "skills/sigma-init/templates/config.json.tmpl").read_text())


def test_template_ships_the_key_null_with_its_derivation():
    t = _template()
    assert t["handoff"].get("after_context_tokens", "absent") is None
    note = t.get("_handoff", "")
    for needle in ("after_context_tokens", "3x", "45%", "first turn"):
        assert needle in note, needle


def test_configuration_doc_has_one_handoff_row_matching_the_constants():
    rows = [line for line in (ROOT / "docs/configuration.md").read_text().splitlines()
            if line.startswith("| `handoff` |")]
    assert len(rows) == 1, rows
    row = rows[0]
    for needle in ("after_goals", "after_context_tokens", f"{lp.CONTEXT_HANDOFF_MULTIPLE}x",
                   f"{round(lp.CONTEXT_HANDOFF_WINDOW_FRACTION * 100)}%", "200k"):
        assert needle in row, needle


def test_running_the_loop_row_describes_the_context_trigger():
    row = next(line for line in (ROOT / "docs/running-the-loop.md").read_text().splitlines()
               if line.startswith("| Context bounded across goals |"))
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    goal_cell, loop_cell = cells[1], cells[3]   # columns: row label, /sigma-goal, --land, /sigma-loop
    assert "not yet" in goal_cell        # `/sigma-goal` is not driven by `next`: still unbounded
    assert "after_context_tokens" in loop_cell and "first-pick context" in loop_cell


_PROSE = ("skills/sigma-loop/SKILL.md", "skills/sigma-loop/references/picking.md",
          "skills/sigma-loop/references/stopping.md", "docs/parallelism.md")


def test_hand_off_prose_names_the_context_trigger():
    for rel in _PROSE:
        text = (ROOT / rel).read_text()
        flat = re.sub(r"\s+", " ", text)
        for stale in ("after 20 goals", "20 completed goals", "default 20-goal ceiling",
                      "on by default at 20 goals —", "goal-count hand-off, on",
                      "the per-session goal-count ceiling)", "stops after 20 goals"):
            assert stale not in flat, (rel, stale)
        assert "context" in flat, rel


def _doctor():
    return m26._load("doctor", ROOT / "skills/sigma-doctor/scripts/doctor.py")


def test_doctor_literals_match_loop_constants():
    src = (ROOT / "skills/sigma-doctor/scripts/doctor.py").read_text()
    mult = re.search(r"_CONTEXT_MULTIPLE = (\d+)\b", src)
    frac = re.search(r"_CONTEXT_CAP_PERCENT = (\d+)\b", src)
    assert mult and frac
    assert int(mult.group(1)) == lp.CONTEXT_HANDOFF_MULTIPLE
    assert int(frac.group(1)) == round(lp.CONTEXT_HANDOFF_WINDOW_FRACTION * 100)


def test_doctor_row_states():
    d = _doctor()
    assert d._handoff_row_state({}) == "after_goals=20; context=3x first pick, cap 45% of window"
    assert d._handoff_row_state({"handoff": {"after_context_tokens": 300000}}).endswith(
        "context=300000")
    assert d._handoff_row_state({"handoff": {"after_context_tokens": 300000.0}}).endswith(
        "context=300000")
    assert d._handoff_row_state({"handoff": {"after_context_tokens": 0}}).endswith("context=off")
    assert d._handoff_row_state({"handoff": {"after_context_tokens": "lots"}}).endswith(
        "context=derived (ignored: lots)")
    assert d._handoff_row_state({"handoff": {"enabled": False}}) == "off"


def test_enforcement_table_has_the_context_row():
    table = (ROOT / "docs/enforcement.md").read_text()
    row = next((line for line in table.splitlines()
                if line.startswith("| Session hand-off on measured context |")), "")
    assert "_session_count_stop" in row and "Cursor: goal count only" in row

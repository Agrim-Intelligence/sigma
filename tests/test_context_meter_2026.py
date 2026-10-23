"""#2026: the orchestrator's own context meter, read from a bounded tail of its own transcript.

Covers: conftest isolation of the host session ids, transcript lookup under CLAUDE_CONFIG_DIR and
CODEX_HOME, the meter itself (`loop_session_model.context_reading`), the heartbeat's context block
and first-pick baseline surviving every other heartbeat writer, the readers that must ignore it,
the status tails that show it, and the report's re-writes-by-idle-gap view.
"""
import importlib.util
import json
import os
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"

_HOST_SESSION_IDS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID")


def _load(name, path=None):
    spec = importlib.util.spec_from_file_location(f"{name}_2026", path or S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


pr = _load("phase_report")


# --- Task 1: test isolation ---------------------------------------------------------------------

def test_conftest_clears_host_session_ids():
    """Red gesture: CLAUDE_CODE_SESSION_ID=planted CODEX_THREAD_ID=planted CODEX_SESSION_ID=planted
    pytest <this test> with the conftest fixture's line for any one of them removed. Without it a
    `loop.py next` test run from a host shell reads the developer's live transcript."""
    assert [name for name in _HOST_SESSION_IDS if name in os.environ] == []


# --- Task 2: transcript lookup honours CLAUDE_CONFIG_DIR and CODEX_HOME ---------------------------

SID = "5b0e8f4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
CODEX_ID = "01a03637-7800-7000-8000-000000000001"        # UUIDv7 whose date is 2026-08-25


def _assistant(tokens_in=10, read=0, write=0, text="ok", model="claude-opus-5", mid="m1",
               ts="2026-10-24T10:00:00.000Z"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "model": model, "id": mid,
                        "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": tokens_in, "cache_read_input_tokens": read,
                                  "cache_creation_input_tokens": write, "output_tokens": 5}}}


def _claude_transcript(root, lines, sid=SID, project="-repo"):
    path = pathlib.Path(root) / "projects" / project / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / sid).mkdir(exist_ok=True)                      # the session dir sibling
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def _codex_request(tokens_in, cached=0, ts="2026-08-25T00:00:10Z", total=None):
    usage = {"input_tokens": tokens_in, "cached_input_tokens": cached, "output_tokens": 7}
    return {"type": "event_msg", "timestamp": ts,
            "payload": {"type": "token_count",
                        "info": {"last_token_usage": usage,
                                 "total_token_usage": total or dict(usage)}}}


def _codex_rollout(root, lines, sid=CODEX_ID):
    path = pathlib.Path(root) / "sessions" / "2026" / "08" / "25" / f"rollout-2026-08-25T00-00-00-{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def test_claude_lookup_finds_a_transcript_under_claude_config_dir(tmp_path, monkeypatch):
    config = tmp_path / "config"; home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    path = _claude_transcript(config, [_assistant()])
    assert pr.find_claude_main_transcript(SID, home=home) == path
    assert pr.find_claude_session_dir(SID, home=home) == path.parent / SID


def test_claude_lookup_still_finds_a_transcript_under_the_home_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
    path = _claude_transcript(tmp_path / "home" / ".claude", [_assistant()])
    assert pr.find_claude_main_transcript(SID, home=tmp_path / "home") == path


def test_codex_lookup_finds_a_rollout_under_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    (tmp_path / "home").mkdir()
    path = _codex_rollout(tmp_path / "codex", [_codex_request(100)])
    assert pr._codex_rollout(CODEX_ID, home=tmp_path / "home") == path


def test_codex_home_equal_to_the_home_root_is_still_one_match(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    path = _codex_rollout(home / ".codex", [_codex_request(100)])
    assert pr._codex_rollout(CODEX_ID, home=home) == path


def test_report_session_fallback_scans_claude_config_dir(tmp_path, monkeypatch):
    ocr = _load("orchestrator_context_report")
    config = tmp_path / "config"; repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    line = dict(_assistant(), cwd=str(repo))
    _claude_transcript(config, [line])
    assert ocr.resolve_session_id(None, home=tmp_path / "nohome", repo_root=repo) == SID


# --- Task 4: the meter --------------------------------------------------------------------------

lsm = _load("loop_session_model")


def _claude_env(tmp_path, monkeypatch, sid=SID):
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    if sid is not None:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    return config


def test_meter_reads_the_latest_turn_not_the_peak(tmp_path, monkeypatch):
    config = _claude_env(tmp_path, monkeypatch)
    _claude_transcript(config, [_assistant(1000, 50000, 2000, mid="m1"),
                                _assistant(10, 30000, 100, mid="m2")])
    assert lsm.context_reading(home=tmp_path / "home") == {
        "source": "host", "tokens": 30110, "session": SID}


def test_meter_counts_input_cache_read_and_cache_write(tmp_path, monkeypatch):
    config = _claude_env(tmp_path, monkeypatch)
    _claude_transcript(config, [_assistant(3, 500, 70000)])
    assert lsm.context_reading(home=tmp_path / "home")["tokens"] == 70503


def test_meter_reads_only_the_tail(tmp_path, monkeypatch):
    """The only assistant line sits before 300 KiB of other lines: a bounded read cannot see it."""
    config = _claude_env(tmp_path, monkeypatch)
    filler = {"type": "user", "message": {"role": "user", "content": "x" * 1000}}
    _claude_transcript(config, [_assistant(10, 20, 30)] + [filler] * 300)
    assert lsm.context_reading(home=tmp_path / "home") == {"source": "unavailable", "session": SID}


def test_meter_ignores_a_partial_last_line(tmp_path, monkeypatch):
    config = _claude_env(tmp_path, monkeypatch)
    path = _claude_transcript(config, [_assistant(10, 20, 30)])
    with open(path, "a") as fh:
        fh.write('{"type": "assistant", "timestamp": "2026-10-24T10:00:01Z", "message": {"role": "assi')
    assert lsm.context_reading(home=tmp_path / "home")["tokens"] == 60


def test_meter_on_codex_counts_input_only_because_it_already_includes_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CODEX_THREAD_ID", CODEX_ID)
    _codex_rollout(tmp_path / "codex", [
        _codex_request(1000, cached=900, ts="2026-08-25T00:00:10Z"),
        _codex_request(4000, cached=3500, ts="2026-08-25T00:00:20Z",
                       total={"input_tokens": 5000, "output_tokens": 14})])
    assert lsm.context_reading(home=tmp_path / "home") == {
        "source": "host", "tokens": 4000, "session": CODEX_ID}


def test_meter_on_codex_with_only_the_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CODEX_SESSION_ID", CODEX_ID)
    _codex_rollout(tmp_path / "codex", [_codex_request(1234, cached=1000)])
    assert lsm.context_reading(home=tmp_path / "home")["tokens"] == 1234


def test_meter_prefers_the_claude_session_when_both_are_set(tmp_path, monkeypatch):
    config = _claude_env(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CODEX_THREAD_ID", CODEX_ID)
    _claude_transcript(config, [_assistant(1, 2, 3)])
    _codex_rollout(tmp_path / "codex", [_codex_request(999)])
    assert lsm.context_reading(home=tmp_path / "home")["tokens"] == 6


def test_meter_without_any_session_id_is_unavailable(tmp_path, monkeypatch):
    _claude_env(tmp_path, monkeypatch, sid=None)
    assert lsm.context_reading(home=tmp_path / "home") == {"source": "unavailable", "session": None}


def test_meter_with_no_transcript_is_unavailable(tmp_path, monkeypatch):
    _claude_env(tmp_path, monkeypatch)
    assert lsm.context_reading(home=tmp_path / "home") == {"source": "unavailable", "session": SID}


def test_meter_with_only_malformed_lines_is_unavailable(tmp_path, monkeypatch):
    config = _claude_env(tmp_path, monkeypatch)
    path = _claude_transcript(config, [])
    path.write_text("not json\n{\"type\": \"assistant\"}\n[1, 2]\n")
    assert lsm.context_reading(home=tmp_path / "home") == {"source": "unavailable", "session": SID}


def test_meter_never_records_an_unsafe_session_id(tmp_path, monkeypatch):
    _claude_env(tmp_path, monkeypatch, sid="../outside")
    assert lsm.context_reading(home=tmp_path / "home") == {"source": "unavailable", "session": None}


# --- Task 5: the heartbeat's context block and its first-pick baseline ---------------------------

SID2 = "6c1f9a5f-2d3e-4f60-9bac-1d2e3f4a5b6c"


def _loop():
    return _load("loop")


def _backlog(d, n):
    base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    (base / "config.json").write_text(json.dumps({"budget": {"max_iterations": 10}}))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "state" / "review-queue.md").write_text("# Q\n")
    for i in range(1, n + 1):
        (base / "goals" / f"{i:04d}.md").write_text(f"---\nid: {i:04d}\nstatus: pending\n---\nx\n")
    return str(base)


def _pick(lp, base, pid):
    config = lp.state.load_config(base)
    return lp._next(base, lp.sources.get_source(base, config), config, session_pid=pid)


def _context(lp, base, pid):
    return json.loads(lp.session_heartbeat_path(base, pid).read_text()).get("context")


def _session_at(tmp_path, monkeypatch, tokens, sid=SID, text="ok"):
    """Point the meter at a transcript whose latest turn holds `tokens` of context."""
    config = _claude_env(tmp_path, monkeypatch, sid=sid)
    return _claude_transcript(config, [_assistant(tokens, 0, 0, text=text)], sid=sid)


def test_next_records_the_host_reading_and_its_first_pick(tmp_path, monkeypatch):
    lp = _loop(); base = _backlog(tmp_path, 0); pid = os.getpid(); lp.begin_run(base, pid)
    _session_at(tmp_path, monkeypatch, 70000)
    _pick(lp, base, pid)
    context = _context(lp, base, pid)
    assert {k: context[k] for k in ("source", "tokens", "session", "first_pick")} == {
        "source": "host", "tokens": 70000, "session": SID, "first_pick": 70000}
    assert isinstance(context["at"], float)


def test_next_batch_records_the_reading(tmp_path, monkeypatch):
    lp = _loop(); base = _backlog(tmp_path, 0); pid = os.getpid(); lp.begin_run(base, pid)
    _session_at(tmp_path, monkeypatch, 64000)
    config = lp.state.load_config(base)
    lp.next_batch(base, lp.sources.get_source(base, config), config, session_pid=pid)
    assert _context(lp, base, pid)["tokens"] == 64000


def test_a_phase_boundary_refresh_between_two_picks_keeps_the_baseline(tmp_path, monkeypatch):
    """F1: the phase-boundary writer must not wipe the block; the session is registered first, or
    the refresh is a no-op and this would pass against a whole-dict replace."""
    lp = _loop(); base = _backlog(tmp_path, 0); pid = os.getpid(); lp.begin_run(base, pid)
    _session_at(tmp_path, monkeypatch, 70000)
    _pick(lp, base, pid)
    assert lp.refresh_registered_session_heartbeat(base, pid) is True
    assert _context(lp, base, pid)["first_pick"] == 70000
    _session_at(tmp_path, monkeypatch, 90000)
    _pick(lp, base, pid)
    context = _context(lp, base, pid)
    assert (context["first_pick"], context["tokens"]) == (70000, 90000)


def test_a_session_restart_between_two_picks_keeps_the_baseline(tmp_path, monkeypatch):
    """CLI `start` mints a NEW generation on every call; the same host session keeps its baseline."""
    lp = _loop(); base = _backlog(tmp_path, 0); pid = os.getpid(); lp.begin_run(base, pid, generation="a")
    _session_at(tmp_path, monkeypatch, 70000)
    _pick(lp, base, pid)
    lp.begin_run(base, pid, generation="b")
    assert _context(lp, base, pid)["first_pick"] == 70000
    _session_at(tmp_path, monkeypatch, 90000)
    _pick(lp, base, pid)
    assert (_context(lp, base, pid)["first_pick"], _context(lp, base, pid)["tokens"]) == (70000, 90000)


def test_a_different_transcript_under_the_same_pid_starts_a_new_baseline(tmp_path, monkeypatch):
    lp = _loop(); base = _backlog(tmp_path, 0); pid = os.getpid(); lp.begin_run(base, pid)
    _session_at(tmp_path, monkeypatch, 70000)
    _pick(lp, base, pid)
    _session_at(tmp_path, monkeypatch, 50000, sid=SID2)
    _pick(lp, base, pid)
    context = _context(lp, base, pid)
    assert (context["session"], context["first_pick"], context["tokens"]) == (SID2, 50000, 50000)


def test_an_unavailable_reading_in_the_same_session_keeps_the_baseline(tmp_path, monkeypatch):
    lp = _loop(); base = _backlog(tmp_path, 0); pid = os.getpid(); lp.begin_run(base, pid)
    path = _session_at(tmp_path, monkeypatch, 70000)
    _pick(lp, base, pid)
    path.unlink()
    _pick(lp, base, pid)
    context = _context(lp, base, pid)
    assert (context["source"], context["session"], context["first_pick"]) == ("unavailable", SID, 70000)
    assert "tokens" not in context
    _session_at(tmp_path, monkeypatch, 90000)
    _pick(lp, base, pid)
    assert (_context(lp, base, pid)["first_pick"], _context(lp, base, pid)["tokens"]) == (70000, 90000)


def test_without_a_session_id_the_reading_is_unavailable_and_the_pick_is_unchanged(tmp_path, monkeypatch):
    lp = _loop(); pid = os.getpid()
    plain = _backlog(tmp_path / "plain", 1); lp.begin_run(plain, pid)
    kind, goal = _pick(lp, plain, pid)[:2]
    base = _backlog(tmp_path / "metered", 1); lp.begin_run(base, pid)
    picked = _pick(lp, base, pid)
    assert (picked[0], pathlib.Path(picked[1]).name) == (kind, pathlib.Path(goal).name) == ("goal", "0001.md")
    context = _context(lp, base, pid)
    assert (context["source"], context["session"], context["first_pick"]) == ("unavailable", None, None)


def test_no_transcript_text_is_written_anywhere(tmp_path, monkeypatch, capsys):
    lp = _loop(); base = _backlog(tmp_path / "repo", 1); pid = os.getpid(); lp.begin_run(base, pid)
    sentinel = "SENTINEL-2026-do-not-copy"
    _session_at(tmp_path, monkeypatch, 70000, text=sentinel)
    _pick(lp, base, pid)
    assert _context(lp, base, pid)["tokens"] == 70000
    written = [p for p in pathlib.Path(base).rglob("*") if p.is_file()]
    assert written and not [p for p in written if sentinel.encode() in p.read_bytes()]
    out, err = capsys.readouterr()
    assert sentinel not in out and sentinel not in err


def test_the_context_write_runs_under_the_session_lock(tmp_path, monkeypatch):
    """Deterministic seam control: the phase-boundary refresh and `session_start` hold this lock for
    their own read-modify-write, so a meter write outside it could be lost between them."""
    lp = _loop(); base = _backlog(tmp_path, 0); pid = os.getpid(); lp.begin_run(base, pid)
    _session_at(tmp_path, monkeypatch, 70000)
    inside, writes = [False], []
    real_locked, real_write = lp._session_locked, lp.write_session_heartbeat

    def locked(*args, **kwargs):
        inside[0] = True
        try:
            return real_locked(*args, **kwargs)
        finally:
            inside[0] = False

    def write(*args, **kwargs):
        writes.append((inside[0], kwargs.get("context") is not None))
        return real_write(*args, **kwargs)

    monkeypatch.setattr(lp, "_session_locked", locked)
    monkeypatch.setattr(lp, "write_session_heartbeat", write)
    _pick(lp, base, pid)
    assert (True, True) in writes and (False, True) not in writes


# --- Task 6: the other heartbeat readers ignore the block (M3 pins) ------------------------------

_BLOCK = {"source": "host", "tokens": 84312, "session": SID, "first_pick": 70000, "at": 1000.0}


def test_resource_liveness_newest_heartbeat_ignores_the_context_block(tmp_path):
    """Also the reader behind doctor.py:1268 and :1527."""
    rl = _load("resource_liveness")
    hb = tmp_path / "state" / "heartbeat"; hb.mkdir(parents=True)
    (hb / "1.json").write_text(json.dumps({"pid": 1, "last_seen": 1000.0, "context": _BLOCK}))
    assert rl.newest_heartbeat(tmp_path) == 1000.0


def test_session_heartbeat_liveness_ignores_the_context_block(tmp_path):
    lp = _loop(); base = _backlog(tmp_path, 0); pid = os.getpid()
    lp.write_session_heartbeat(base, pid, now=1000.0, context=_BLOCK)
    assert lp.session_heartbeat_liveness(base, pid, now=1001.0) == ("idle", 1.0)


# --- Task 7: the status tails show the reading ----------------------------------------------------

LOG = S.parent.parent / "sigma-log" / "scripts" / "log.py"
STATUS = S.parent.parent / "sigma-status" / "scripts" / "status.py"


def _heartbeats(tmp_path, *beats):
    hb = tmp_path / "state" / "heartbeat"; hb.mkdir(parents=True)
    for i, beat in enumerate(beats):
        (hb / f"{i}.json").write_text(json.dumps(beat))
    return tmp_path


def test_log_tail_shows_a_host_reading_as_of_the_last_pick(tmp_path):
    log = _load("log", LOG)
    d = _heartbeats(tmp_path, {"pid": 1, "last_seen": 1000.0, "context": _BLOCK})
    assert log.loop_heartbeat_tail(d, now=1061.0) == \
        "loop heartbeat: 00:01:01 ago, context 84,312 tokens at last pick (host)"


def test_log_tail_shows_an_unavailable_reading(tmp_path):
    log = _load("log", LOG)
    d = _heartbeats(tmp_path, {"pid": 1, "last_seen": 1000.0,
                               "context": {"source": "unavailable", "session": None}})
    assert log.loop_heartbeat_tail(d, now=1061.0) == \
        "loop heartbeat: 00:01:01 ago, context unavailable at last pick"


def test_log_tail_reads_the_context_of_the_newest_heartbeat(tmp_path):
    log = _load("log", LOG)
    d = _heartbeats(tmp_path, {"pid": 1, "last_seen": 900.0, "context": _BLOCK},
                    {"pid": 2, "last_seen": 1000.0, "context": dict(_BLOCK, tokens=5)})
    assert log.loop_heartbeat_tail(d, now=1061.0).endswith("context 5 tokens at last pick (host)")


def test_status_tail_is_the_log_tail(tmp_path):
    status = _load("status", STATUS)
    d = _heartbeats(tmp_path, {"pid": 1, "last_seen": 1000.0, "context": _BLOCK})
    assert status._loop_heartbeat_segment(d, now=1061.0) == \
        "loop heartbeat: 00:01:01 ago, context 84,312 tokens at last pick (host)"


# --- Task 8: full-context re-writes grouped by the idle gap before them ---------------------------

ocr = _load("orchestrator_context_report")


def _call(ts, read, write, tokens_in=0):
    return {"ts": ts, "model": "claude-opus-5",
            "usage": {"input_tokens": tokens_in, "cache_read_input_tokens": read,
                      "cache_creation_input_tokens": write}}


def test_rewrites_are_grouped_by_the_gap_before_them():
    calls = [
        _call("2026-10-24 10:00:00", 0, 1000),        # first call: no gap, not bucketed
        _call("2026-10-24 10:01:00", 900, 100),       # 60 s, not a re-write (10%)
        _call("2026-10-24 10:06:00", 400, 600),       # 300 s -> 5m to 1h, re-write (60%)
        _call("2026-10-24 10:36:00", 600, 400),       # 1800 s, not a re-write (40%)
        _call("2026-10-24 11:36:00", 500, 500),       # 3600 s -> 1h or more, re-write (exactly 50%)
        _call("2026-10-24 13:36:00", 100, 9900),      # 7200 s, re-write
    ]
    assert ocr.rewrites_by_gap(calls) == [
        {"gap": "under 5m", "turns": 1, "rewrites": 0, "rewrite_tokens": 0},
        {"gap": "5m to 1h", "turns": 2, "rewrites": 1, "rewrite_tokens": 600},
        {"gap": "1h or more", "turns": 2, "rewrites": 2, "rewrite_tokens": 10400},
    ]


def test_rewrites_skip_untimed_calls_and_never_count_an_empty_context():
    calls = [_call("2026-10-24 10:00:00", 0, 0), _call("garbage", 0, 500),
             _call("2026-10-24 10:00:30", 0, 0)]
    assert [(row["turns"], row["rewrites"]) for row in ocr.rewrites_by_gap(calls)] == [
        (1, 0), (0, 0), (0, 0)]


def test_report_json_and_text_carry_the_rewrite_table(tmp_path, monkeypatch):
    config = _claude_env(tmp_path, monkeypatch)
    _claude_transcript(config, [_assistant(1, 0, 900, mid="a", ts="2026-10-24T10:00:00Z"),
                                _assistant(1, 0, 900, mid="b", ts="2026-10-24T12:00:00Z")])
    report = ocr.build_report(str(tmp_path / ".sdlc"), SID, home=tmp_path / "home", rates=[])
    assert report["rewrites_by_gap"][2] == {"gap": "1h or more", "turns": 1, "rewrites": 1,
                                            "rewrite_tokens": 900}
    line = [l for l in ocr.render_text(report) if l.startswith("RE-WRITES BY IDLE GAP")]
    assert line == ["RE-WRITES BY IDLE GAP  under 5m: 0 of 0 turns (0 tokens)  "
                    "5m to 1h: 0 of 0 turns (0 tokens)  1h or more: 1 of 1 turns (900 tokens)"]


def test_rewrites_measure_gaps_in_time_order_not_input_order():
    calls = [_call("2026-10-24 12:00:00", 0, 800), _call("2026-10-24 10:00:00", 0, 100)]
    assert ocr.rewrites_by_gap(calls)[2] == {"gap": "1h or more", "turns": 1, "rewrites": 1,
                                             "rewrite_tokens": 800}

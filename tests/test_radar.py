"""research-radar Phase A: the deterministic bookkeeping — an agenda rotation cursor over the backlog
and a dedup ledger so findings never repeat. The research/ranking are agent-driven (the skill)."""
import pathlib, importlib.util, tempfile

R = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-radar" / "scripts" / "radar.py"


def _r():
    spec = importlib.util.spec_from_file_location("radar", R)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _sdlc(d):
    base = pathlib.Path(d) / ".sdlc"; base.mkdir(parents=True)
    return str(base)


def test_agenda_rotates_through_backlog():
    r = _r()
    assert r.agenda(5, 2, 0) == {"indices": [0, 1], "next_cursor": 2}
    assert r.agenda(5, 2, 2) == {"indices": [2, 3], "next_cursor": 4}
    assert r.agenda(5, 2, 4) == {"indices": [4, 0], "next_cursor": 1}   # wraps around


def test_agenda_caps_k_and_handles_empty():
    r = _r()
    assert r.agenda(3, 5, 0) == {"indices": [0, 1, 2], "next_cursor": 0}  # k capped at n
    assert r.agenda(0, 3, 0) == {"indices": [], "next_cursor": 0}         # empty backlog


def test_ledger_record_and_seen_dedups():
    r = _r()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        assert r.seen(base) == []
        assert r.record(base, "issue-1:new-model-x") is True
        assert r.record(base, "issue-2:faster-lib") is True
        assert r.record(base, "issue-1:new-model-x") is False          # already surfaced -> skip
        assert r.seen(base) == ["issue-1:new-model-x", "issue-2:faster-lib"]


def test_main_agenda_seen_record_exit_clean():
    r = _r()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        assert r.main(["radar.py", "agenda", "5", "2", "0"]) == 0
        assert r.main(["radar.py", "record", "k-one", base]) == 0
        assert r.main(["radar.py", "seen", base]) == 0
        assert r.seen(base) == ["k-one"]

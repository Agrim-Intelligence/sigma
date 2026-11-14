"""The code-owned follow-up record (#2224): `.sdlc/state/followups/<stem>.json`, written only by `add_slice.py`.

Run: python3 -m pytest tests/test_followup_record.py
(generic form: python -m pytest tests/test_followup_record.py)

CONTROLS: C5 test_two_appenders_both_land_deterministic (the system of record: a pause between read and write inside
the lock; the threaded variant is a labeled SMOKE test, its sensitivity measured in .sdlc/evidence/2224-controls.md),
C6 test_corrupt_record_refuses, C29 test_fcntl_absent_refuses.
"""
import importlib.util
import json
import pathlib
import threading

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fr = _mod("followup_route")


@pytest.fixture
def sdlc(tmp_path):
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    return tmp_path / ".sdlc"


def test_append_is_idempotent_on_slice_id_and_first_routed_is_set_once(sdlc):
    fr.append_slice(sdlc, "2224", "aaa", "sha0")
    first = fr.first_routed(sdlc, "2224")
    rec = fr.append_slice(sdlc, "2224", "aaa", "sha1")
    assert len(rec["added"]) == 1 and rec["added"][0]["plan_sha256_before"] == "sha0"
    fr.append_slice(sdlc, "2224", "bbb", "sha2")
    assert fr.first_routed(sdlc, "2224") == first and len(fr.read_record(sdlc, "2224")["added"]) == 2


def test_missing_record_reads_empty(sdlc):
    assert fr.read_record(sdlc, "9") == {"first_routed_at": None, "added": []}


def test_corrupt_record_refuses(sdlc):
    path = fr.record_path(sdlc, "2224")
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    with pytest.raises(fr.RecordError):
        fr.read_record(sdlc, "2224")
    with pytest.raises(fr.RecordError):
        fr.added_count(sdlc, "2224")


def test_wrong_shape_record_refuses(sdlc):
    path = fr.record_path(sdlc, "2224")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"added": "nope"}))
    with pytest.raises(fr.RecordError):
        fr.read_record(sdlc, "2224")


def test_record_lives_under_state_and_nothing_appears_at_the_repo_root(sdlc, tmp_path):
    fr.append_slice(sdlc, "2224", "aaa", "s")
    assert fr.record_path(sdlc, "2224").parent == sdlc / "state" / "followups"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".sdlc"]


def test_two_appenders_both_land_deterministic(sdlc):
    """Deterministic control at the seam: thread A pauses between its read and its write while holding the lock; B
    must not read until A has written, so both ids land. Without the lock B reads the stale record and one is lost."""
    gate, inside = threading.Event(), threading.Event()

    def pause():
        inside.set()
        gate.wait(5)

    a = threading.Thread(target=fr.append_slice, args=(sdlc, "2224", "aaa", "s"), kwargs={"_pause": pause})
    a.start()
    assert inside.wait(5)
    b = threading.Thread(target=fr.append_slice, args=(sdlc, "2224", "bbb", "s"))
    b.start()
    b.join(0.3)
    gate.set()
    a.join(5)
    b.join(5)
    ids = sorted(item["slice_id"] for item in fr.read_record(sdlc, "2224")["added"])
    assert ids == ["aaa", "bbb"]


def test_two_appenders_both_land_smoke(sdlc):
    """SMOKE TEST ONLY (probabilistic; the system of record is the deterministic test above)."""
    threads = [threading.Thread(target=fr.append_slice, args=(sdlc, "2224", "id%d" % n, "s")) for n in range(12)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert len(fr.read_record(sdlc, "2224")["added"]) == 12


def test_fcntl_absent_refuses(sdlc, monkeypatch):
    monkeypatch.setattr(fr, "fcntl", None)
    with pytest.raises(fr.LockUnavailable):
        fr.append_slice(sdlc, "2224", "aaa", "s")
    assert not fr.record_path(sdlc, "2224").exists()


def test_unsafe_goal_refuses(sdlc):
    with pytest.raises(ValueError):
        fr.record_path(sdlc, "../x")


def test_added_count_takes_max_of_record_and_plan_headings(sdlc):
    (sdlc / "plans").mkdir()
    (sdlc / "plans" / "2224.md").write_text("# p\n## Added slice 1: one\n\n## Added slice 2: two\n")
    assert fr.added_count(sdlc, "2224") == 2
    fr.append_slice(sdlc, "2224", "a", "s")
    assert fr.added_count(sdlc, "2224") == 2
    for n in "bcd":
        fr.append_slice(sdlc, "2224", n, "s")
    assert fr.added_count(sdlc, "2224") == 4

"""In-process seam controls for retention.py (#457).

`tests/test_state_retention.py` is the system of record and drives the documented CLI gesture. These
tests pin the three defence-in-depth guards that gesture cannot reach deterministically: the
witness-before-log unlink order, the size/mtime and owner re-check between judging and unlinking
(a race window that collapses to microseconds in-process, so it is forced at the seam), and the
last-row timestamp gate (behind the mtime gate in every CLI case).
"""
import importlib.util
import json
import os
import pathlib
import time

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"
DAY = 86400
OLD = time.time() - 200 * DAY


def _retention():
    spec = importlib.util.spec_from_file_location("retention_seams", S / "retention.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def _goal(tmp_path, stem="7", last_ts=OLD):
    d = tmp_path / ".sdlc"
    (d / "state" / "log").mkdir(parents=True)
    (d / "state" / "witness").mkdir(parents=True)
    (d / "config.json").write_text("{}")
    log = d / "state" / "log" / f"{stem}.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in (
        {"kind": "claimed", "ts": _iso(last_ts - 5)},
        {"kind": "recorded", "result": "done", "ts": _iso(last_ts)})))
    wit = d / "state" / "witness" / f"{stem}.jsonl"
    wit.write_text("{}\n")
    for p in (log, wit):
        os.utime(p, (OLD, OLD))
    return d, log, wit


def test_witness_is_unlinked_before_the_log(tmp_path, monkeypatch):
    r = _retention()
    d, log, wit = _goal(tmp_path)
    order = []
    real = pathlib.Path.unlink

    def spy(self, *a, **k):
        order.append(self.parent.name)
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "unlink", spy)
    res = r.prune_closed_goal_streams(str(d))
    assert res["removed"] == ["7"], res
    assert order == ["witness", "log"], order


def test_a_write_between_judging_and_unlinking_keeps_the_goal(tmp_path):
    r = _retention()
    d, log, wit = _goal(tmp_path)
    real = r._judge

    def judge_then_a_writer_appends(*a, **k):
        out = real(*a, **k)
        with log.open("a") as fh:
            fh.write(json.dumps({"kind": "claimed", "ts": _iso(time.time())}) + "\n")
        return out

    r._judge = judge_then_a_writer_appends
    res = r.prune_closed_goal_streams(str(d))
    assert res["removed"] == [] and res["kept"]["7"] == "changed during the sweep", res
    assert log.exists() and wit.exists()


def test_an_owner_marker_appearing_after_judging_keeps_the_goal(tmp_path):
    r = _retention()
    d, log, wit = _goal(tmp_path)
    real = r._judge

    def judge_then_a_successor_claims(*a, **k):
        out = real(*a, **k)
        marker = d / "state" / "claims" / "7.claimed"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}")
        return out

    r._judge = judge_then_a_successor_claims
    res = r.prune_closed_goal_streams(str(d))
    assert res["removed"] == [] and res["kept"]["7"] == "changed during the sweep", res
    assert log.exists() and wit.exists()


def test_a_recent_last_row_keeps_a_log_whose_mtime_is_old(tmp_path):
    """A restore or a copy can reset mtime; the last row's own timestamp must still hold the goal."""
    r = _retention()
    d, log, wit = _goal(tmp_path, last_ts=time.time() - 5 * DAY)
    res = r.prune_closed_goal_streams(str(d))
    assert res["removed"] == [] and res["kept"]["7"] == "inside the retention window", res
    assert log.exists() and wit.exists()

"""#265: `/agrim-status` carries the same age-based loop liveness evidence as slots."""
import importlib.util
import json
import pathlib


STATUS = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-status" / "scripts" / "status.py"
spec = importlib.util.spec_from_file_location("status_heartbeat", STATUS)
status = importlib.util.module_from_spec(spec); spec.loader.exec_module(status)


def test_status_loop_heartbeat_segment_reports_the_newest_age(tmp_path):
    hb = tmp_path / "state" / "heartbeat"; hb.mkdir(parents=True)
    (hb / "one.json").write_text(json.dumps({"last_seen": 1000.0}))
    (hb / "two.json").write_text(json.dumps({"last_seen": 1050.0}))
    assert status._loop_heartbeat_segment(tmp_path, now=1111.0) == "loop heartbeat: 00:01:01 ago"

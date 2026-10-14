"""#1426: the back-fill refusal guard has to hold across watcher ticks. Each tick builds a new source, so the in-process
refusal counter never carries; the refusal is remembered in a small state file and the next ticks make no project call
until `project.backfill_pause_minutes` has passed or a project call is answered. Fake gh; no network."""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_github_project as tgp
import test_board_backfill as tbb
import test_backfill_refusals as tbr


def _src(run, sdlc_dir, **project):
    s = tgp._mod("sources").GitHubSource(tgp._cfg(project={"enabled": True, **project}), run=run, sdlc_dir=str(sdlc_dir))
    s._RETRY_BASE = 0
    return s


def _tick(run, sdlc_dir, **project):
    try:
        return _src(run, sdlc_dir, **project).backfill_board()
    except Exception:
        return "raised"


def test_a_refused_tick_holds_off_the_next_new_source(tmp_path):
    refuse = {"on": True}
    run = tbr._gated(refuse)
    assert _tick(run, tmp_path) == "raised"
    first = len(run.projects)
    assert first >= 1 and (tmp_path / "state" / "backfill-refused.json").is_file()
    refuse["on"] = False
    assert _tick(run, tmp_path) == 0           # a NEW source, counter high, project API healthy again: still held
    assert len(run.projects) == first


def test_the_pause_ends_after_the_configured_minutes_and_a_success_clears_it(tmp_path):
    refuse = {"on": True}
    run = tbr._gated(refuse)
    _tick(run, tmp_path)
    refuse["on"] = False
    stamp = tmp_path / "state" / "backfill-refused.json"
    stamp.write_text(json.dumps({"at": int(time.time()) - 31 * 60}))
    assert _tick(run, tmp_path, backfill_cap=2) == 2
    assert not stamp.exists()
    stamp.write_text(json.dumps({"at": int(time.time()) - 5 * 60}))
    assert _tick(run, tmp_path, backfill_cap=2, backfill_pause_minutes=3) == 2   # configured shorter pause has passed


def test_no_state_directory_or_a_bad_stamp_changes_nothing(tmp_path):
    run = tbb._world()
    assert tgp._mod("sources").GitHubSource(tgp._cfg(project={"enabled": True, "backfill_cap": 3}), run=run).backfill_board() == 3
    (tmp_path / "state").mkdir()
    for bad in ("not json", json.dumps({"at": "x"}), json.dumps({"at": int(time.time()) + 10 ** 6})):
        (tmp_path / "state" / "backfill-refused.json").write_text(bad)
        assert _tick(tbb._world(), tmp_path, backfill_cap=3) == 3

"""Pure move from tests/test_followup_mode.py at 4042e97 (#2224 amendment 2): the wholly assertion-red nodes; test bodies byte-identical.

`followups.mode` (#2153): the project's decision on where a follow-up goes, honoured by `handoff.py track`.

Run: $HOME/.sigma-venv312/bin/python -m pytest tests/test_followup_mode*.py
(generic form: python -m pytest tests/test_followup_mode*.py)

One test per clause of `followup_refusal` (mode, kind, blocks), each seen red when that clause alone is broken
(see the CONTROLS note at the bottom). The default must change nothing: every refusal test has a twin at
the default that files.

"""
import json
import pathlib

import pytest

import test_handoff as th

handoff = th.handoff
IN_GOAL = dict(th.ON, followups={"mode": "in-goal"})

ARGS = ["--area", "engine", "--why", "found while here", "--queue", "queued", "--assignee", "same-area"]


@pytest.fixture
def src(monkeypatch):
    fake = th.FakeSource(number="61")
    monkeypatch.setattr(handoff.sources, "get_source", lambda *a, **k: fake)
    monkeypatch.setattr(handoff, "_duplicate_search", lambda *a, **k: {"schema": "brainstorm-dedup/v1",
                                                                      "candidates": [], "degraded": []})
    return fake


def _track(tmp_path, config, *extra, blocks="no"):
    sdlc = th._project(tmp_path, config)
    return handoff.main(["handoff.py", "track", str(sdlc), "g.md", *ARGS, "--blocks", blocks, *extra])


@pytest.mark.parametrize("block", [None, {}, {"mode": "In-Goal"}, {"mode": "in_goal"}, {"mode": "File"},
                                   {"mode": True}, {"mode": ["in-goal"]}, "file", ["file"], 7])
def test_anything_but_the_exact_string_file_reads_in_goal(block):
    cfg = {} if block is None else {"followups": block}
    assert handoff.followup_mode(cfg) == "in-goal"


def test_missing_config_reads_in_goal():
    assert handoff.followup_mode({}) == "in-goal"


@pytest.mark.parametrize("config", ["x", None, [], 3])
def test_a_non_object_config_reads_in_goal(config):
    assert handoff.followup_mode(config) == "in-goal"


def test_in_goal_refuses_a_same_feature_follow_up_names_the_gesture_and_files_nothing(tmp_path, src, capsys):
    assert _track(tmp_path, IN_GOAL) == handoff.FOLLOWUP_REFUSED_EXIT
    assert src.created is None and src.notes == []
    err = capsys.readouterr().err
    assert "followups.mode is in-goal" in err and "work.py add-slice" in err
    assert "deferred" not in err and "sigma-orchestrate" not in err


def test_the_default_config_is_in_goal_and_refuses_unpatched(tmp_path, src):
    """The one default-config smoke test: no `followups` key, `raw=True`, nothing switched to `file`."""
    sdlc = th._project(tmp_path, th.ON, raw=True)
    rc = handoff.main(["handoff.py", "track", str(sdlc), "g.md", *ARGS, "--blocks", "no"])
    assert rc == handoff.FOLLOWUP_REFUSED_EXIT and src.created is None


def test_the_followup_mode_verb_reads_in_goal_when_the_config_is_unreadable(tmp_path, capsys):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text("{not json")
    assert handoff.main(["handoff.py", "followup-mode", str(sdlc)]) == 0
    assert capsys.readouterr().out.splitlines() == ["in-goal", "max_added_slices 10"]


def test_the_scaffolded_config_ships_the_default_and_the_docs_name_it():
    root = pathlib.Path(__file__).resolve().parent.parent
    tmpl = (root / "skills" / "sigma-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8")
    assert json.loads(tmpl)["followups"] == {"mode": "in-goal", "max_added_slices": 10}
    assert "followups.mode" in (root / "docs" / "configuration.md").read_text(encoding="utf-8")


def test_absent_followups_key_reads_in_goal():
    assert handoff.followup_mode({"ledger": {"enabled": True}}) == "in-goal"


def test_absent_mode_key_reads_in_goal():
    assert handoff.followup_mode({"followups": {"max_added_slices": 3}}) == "in-goal"


def test_empty_config_reads_in_goal():
    assert handoff.followup_mode({}) == "in-goal"


def test_followups_null_reads_in_goal():
    assert handoff.followup_mode({"followups": None}) == "in-goal"


def test_mode_null_reads_in_goal():
    assert handoff.followup_mode({"followups": {"mode": None}}) == "in-goal"


def test_no_config_file_verb_reads_in_goal(tmp_path, capsys):
    assert handoff.main(["handoff.py", "followup-mode", str(tmp_path / "nowhere")]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "in-goal"


def test_only_the_exact_string_file_reads_file():
    assert [handoff.followup_mode({"followups": {"mode": v}}) for v in ("file", "File", "FILE", " file", "files")] \
        == ["file", "in-goal", "in-goal", "in-goal", "in-goal"]

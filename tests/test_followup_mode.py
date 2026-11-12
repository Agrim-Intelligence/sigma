"""`followups.mode` (#2153): the project's decision on where a follow-up goes, honoured by `handoff.py track`.

Run: $HOME/.sigma-venv312/bin/python -m pytest tests/test_followup_mode.py
(generic form: python -m pytest tests/test_followup_mode.py)

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


@pytest.mark.parametrize("block", [None, {}, {"mode": "file"}, {"mode": "In-Goal"}, {"mode": "in_goal"},
                                   {"mode": True}, {"mode": ["in-goal"]}, "in-goal", ["in-goal"], 7])
def test_anything_but_the_exact_string_reads_file(block):
    cfg = {} if block is None else {"followups": block}
    assert handoff.followup_mode(cfg) == "file"


def test_the_exact_string_in_goal_reads_in_goal():
    assert handoff.followup_mode(IN_GOAL) == "in-goal"


@pytest.mark.parametrize("config", ["x", None, [], 3])
def test_a_non_object_config_reads_file(config):
    assert handoff.followup_mode(config) == "file"


def test_in_goal_refuses_an_unmarked_non_blocking_follow_up_and_files_nothing(tmp_path, src, capsys):
    assert _track(tmp_path, IN_GOAL) == handoff.FOLLOWUP_REFUSED_EXIT
    assert src.created is None and src.notes == []
    err = capsys.readouterr().err
    assert "followups.mode is in-goal" in err and "sigma-orchestrate:deferred" in err


def test_the_same_follow_up_files_at_the_default(tmp_path, src):
    assert _track(tmp_path, th.ON) == 0
    assert src.created is not None


def test_the_same_follow_up_files_when_the_mode_is_file_spelled_out(tmp_path, src):
    assert _track(tmp_path, dict(th.ON, followups={"mode": "file"})) == 0
    assert src.created is not None


def test_in_goal_still_files_a_blocker(tmp_path, src):
    assert _track(tmp_path, IN_GOAL, blocks="yes") == 0
    assert src.created is not None


def test_in_goal_refuses_an_explicit_follow_up_kind(tmp_path, src):
    assert _track(tmp_path, IN_GOAL, "--kind", "followup") == handoff.FOLLOWUP_REFUSED_EXIT
    assert src.created is None


@pytest.mark.parametrize("kind", ["prerequisite", "cleanup", "child"])
def test_in_goal_files_work_that_is_not_a_follow_up(tmp_path, src, kind):
    assert _track(tmp_path, IN_GOAL, "--kind", kind) == 0
    assert src.created is not None


@pytest.mark.parametrize("config", [IN_GOAL, th.ON], ids=["in-goal", "default"])
def test_an_unknown_kind_is_a_usage_error_in_either_mode(tmp_path, src, config):
    assert _track(tmp_path, config, "--kind", "other") == 2
    assert src.created is None


def test_kind_changes_nothing_at_the_default(tmp_path, src):
    assert _track(tmp_path, th.ON, "--kind", "followup") == 0
    assert src.created is not None


def test_the_followup_mode_verb_prints_the_mode(tmp_path, capsys):
    sdlc = th._project(tmp_path, IN_GOAL)
    assert handoff.main(["handoff.py", "followup-mode", str(sdlc)]) == 0
    assert capsys.readouterr().out.strip() == "in-goal"


def test_the_followup_mode_verb_reads_file_when_the_config_is_unreadable(tmp_path, capsys):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text("{not json")
    assert handoff.main(["handoff.py", "followup-mode", str(sdlc)]) == 0
    assert capsys.readouterr().out.strip() == "file"


def test_the_scaffolded_config_ships_the_default_and_the_docs_name_it():
    root = pathlib.Path(__file__).resolve().parent.parent
    tmpl = (root / "skills" / "sigma-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8")
    assert json.loads(tmpl)["followups"] == {"mode": "file"}
    assert "followups.mode" in (root / "docs" / "configuration.md").read_text(encoding="utf-8")


# CONTROLS (#2153), each applied alone to `followup_refusal` and the named test seen red:
#   mode clause dropped (`!= "in-goal"` removed): test_the_same_follow_up_files_at_the_default
#   kind clause dropped: test_in_goal_files_work_that_is_not_a_follow_up
#   blocks clause dropped: test_in_goal_still_files_a_blocker
#   `(kind or "followup")` made `kind`: test_in_goal_refuses_an_unmarked_non_blocking_follow_up_and_files_nothing
#   followup_mode accepts any truthy string: test_anything_but_the_exact_string_reads_file

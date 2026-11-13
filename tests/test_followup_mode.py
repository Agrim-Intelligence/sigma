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
    assert capsys.readouterr().out.splitlines()[0] == "in-goal"          # line 1 stays the mode (#2153)


def test_the_followup_mode_verb_reads_file_when_the_config_is_unreadable(tmp_path, capsys):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text("{not json")
    assert handoff.main(["handoff.py", "followup-mode", str(sdlc)]) == 0
    assert capsys.readouterr().out.splitlines() == ["file", "max_added_slices 10"]


def test_the_scaffolded_config_ships_the_default_and_the_docs_name_it():
    root = pathlib.Path(__file__).resolve().parent.parent
    tmpl = (root / "skills" / "sigma-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8")
    assert json.loads(tmpl)["followups"] == {"mode": "file", "max_added_slices": 10}
    assert "followups.mode" in (root / "docs" / "configuration.md").read_text(encoding="utf-8")


# --- #2178: followups.max_added_slices -------------------------------------------------------------------------

def _limit_cfg(value):
    return {"followups": {"mode": "in-goal", "max_added_slices": value}}


def test_the_limit_defaults_to_10_and_the_constant_says_so():
    assert handoff.DEFAULT_MAX_ADDED_SLICES == 10
    assert handoff.max_added_slices({}) == 10
    assert handoff.max_added_slices(IN_GOAL) == 10


@pytest.mark.parametrize("value,expected", [(1, 1), (3, 3), (25, 25), (1000, 1000), (4.0, 4)])
def test_a_valid_override_is_read(value, expected):
    assert handoff.max_added_slices(_limit_cfg(value)) == expected


@pytest.mark.parametrize("value", [None, True, False, 0, -1, -10, 2.5, 0.5, "5", "10", "", [3], {"n": 3}, float("nan")])
def test_every_invalid_limit_reads_10_never_unlimited(value):
    assert handoff.max_added_slices(_limit_cfg(value)) == 10


@pytest.mark.parametrize("config", ["x", None, [], 3, {"followups": "in-goal"}, {"followups": [5]}])
def test_a_non_object_config_or_block_reads_the_default_limit(config):
    assert handoff.max_added_slices(config) == 10


def test_the_rule_text_carries_the_configured_number_not_a_literal():
    assert "(at most 7 added slices)" in handoff.in_goal_rule(7)
    assert "(at most 10 added slices)" in handoff.IN_GOAL_RULE


def test_the_refusal_track_prints_carries_the_configured_number(tmp_path, src, capsys):
    assert _track(tmp_path, dict(th.ON, followups={"mode": "in-goal", "max_added_slices": 4})) == \
        handoff.FOLLOWUP_REFUSED_EXIT
    err = capsys.readouterr().err
    assert "at most 4 added slices" in err and "at most 10 added" not in err


def test_the_refusal_carries_10_at_the_default(tmp_path, src, capsys):
    assert _track(tmp_path, IN_GOAL) == handoff.FOLLOWUP_REFUSED_EXIT
    assert "at most 10 added slices" in capsys.readouterr().err


def test_the_followup_mode_verb_prints_the_limit_on_line_two(tmp_path, capsys):
    sdlc = th._project(tmp_path, _limit_cfg(6))
    assert handoff.main(["handoff.py", "followup-mode", str(sdlc)]) == 0
    assert capsys.readouterr().out.splitlines() == ["in-goal", "max_added_slices 6"]


def test_the_followup_mode_verb_prints_10_for_an_invalid_limit(tmp_path, capsys):
    sdlc = th._project(tmp_path, dict(th.ON, followups={"mode": "file", "max_added_slices": 0}))
    assert handoff.main(["handoff.py", "followup-mode", str(sdlc)]) == 0
    assert capsys.readouterr().out.splitlines() == ["file", "max_added_slices 10"]


def test_no_literal_two_remains_as_the_added_slice_limit():
    root = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("skills/sigma-loop/scripts/handoff.py", "skills/sigma-init/templates/config.json.tmpl",
                "docs/configuration.md", "docs/orchestrate.md",
                "skills/sigma-orchestrate/references/follow-ups.md",
                "skills/sigma-orchestrate/references/launch.md", "skills/sigma-orchestrate/SKILL.md"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "at most 2 added" not in text.lower() and "at most 2 slices" not in text.lower(), rel
        assert "(at most 2)" not in text and "cap of 2" not in text, rel


# CONTROLS (#2153), each applied alone to `followup_refusal` and the named test seen red:
#   mode clause dropped (`!= "in-goal"` removed): test_the_same_follow_up_files_at_the_default
#   kind clause dropped: test_in_goal_files_work_that_is_not_a_follow_up
#   blocks clause dropped: test_in_goal_still_files_a_blocker
#   `(kind or "followup")` made `kind`: test_in_goal_refuses_an_unmarked_non_blocking_follow_up_and_files_nothing
#   followup_mode accepts any truthy string: test_anything_but_the_exact_string_reads_file
# CONTROLS (#2178), each applied alone and the named tests seen red:
#   `isinstance(value, bool)` guard dropped: test_every_invalid_limit_reads_10_never_unlimited (True reads 1)
#   `value >= 1` weakened to `>= 0` or removed: test_every_invalid_limit_reads_10_never_unlimited (0, -1)
#   the float branch removed: test_a_valid_override_is_read (4.0); `is_integer()` removed: the 2.5 case
#   default constant changed from 10: test_the_limit_defaults_to_10_and_the_constant_says_so
#   refusal built from IN_GOAL_RULE instead of in_goal_rule(...): test_the_refusal_track_prints_carries_the_configured_number
#   verb's second print removed: test_the_followup_mode_verb_prints_the_limit_on_line_two

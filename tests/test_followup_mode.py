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
FILE = dict(th.ON, followups={"mode": "file"})
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


def test_the_exact_string_file_reads_file():
    assert handoff.followup_mode(FILE) == "file"


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


def test_file_mode_files_as_before(tmp_path, src):
    assert _track(tmp_path, FILE, "--kind", "child", blocks="yes") == 0
    assert src.created is not None and "followup-reason" not in src.created["body"]
    assert all("SEPARATE" not in note for _, note in src.notes)


@pytest.mark.parametrize("config", [IN_GOAL, FILE], ids=["in-goal", "file"])
def test_an_unknown_kind_is_a_usage_error_in_either_mode(tmp_path, src, config):
    assert _track(tmp_path, config, "--kind", "other") == 2
    assert src.created is None


def test_track_exits_2_and_files_nothing_on_an_unreadable_config(tmp_path, src):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text("{not json")
    rc = handoff.main(["handoff.py", "track", str(sdlc), "g.md", *ARGS, "--blocks", "no"])
    assert rc == 2 and src.created is None


def test_the_followup_mode_verb_prints_the_mode(tmp_path, capsys):
    sdlc = th._project(tmp_path, IN_GOAL)
    assert handoff.main(["handoff.py", "followup-mode", str(sdlc)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "in-goal"          # line 1 stays the mode (#2153)


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


# CONTROLS (#2224): each applied alone and the named test seen red:
#   default restored to `file`: test_missing_config_reads_in_goal (C20)
#   mode `file` routed anyway: test_file_mode_files_as_before (C19)
# CONTROLS (#2178), each applied alone and the named tests seen red:
#   `isinstance(value, bool)` guard dropped: test_every_invalid_limit_reads_10_never_unlimited (True reads 1)
#   `value >= 1` weakened to `>= 0` or removed: test_every_invalid_limit_reads_10_never_unlimited (0, -1)
#   the float branch removed: test_a_valid_override_is_read (4.0); `is_integer()` removed: the 2.5 case
#   default constant changed from 10: test_the_limit_defaults_to_10_and_the_constant_says_so
#   refusal built from IN_GOAL_RULE instead of in_goal_rule(...): test_the_refusal_track_prints_carries_the_configured_number
#   verb's second print removed: test_the_followup_mode_verb_prints_the_limit_on_line_two


# --- #2224 amendment A1: the default is in-goal, and each way of being absent is its own test (C39) ----------------

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


def test_max_added_slices_defaults_to_ten_with_absent_keys():
    assert handoff.max_added_slices({}) == 10
    assert handoff.max_added_slices({"followups": {}}) == 10
    assert handoff.max_added_slices({"followups": None}) == 10

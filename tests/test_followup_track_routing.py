"""`handoff.py track` routes every filing through `followup_route.route` (#2224).

Run: python3 -m pytest tests/test_followup_track_routing.py
(generic form: python -m pytest tests/test_followup_track_routing.py)

Driven through the documented gesture, `handoff.main(["handoff.py", "track", ...])`.
CONTROLS: C13/C14 test_every_kind_and_blocks_route, C15 test_target_unit_does_not_buy_a_separate_goal, C16
test_separate_filing_carries_the_reason_marker, C17 test_parent_comment_names_the_reason, C18
test_ambiguous_goal_refuses_exit_2, C24 test_undeclared_unit_name_inherits_with_warning, C25
test_body_file_marker_must_be_open_registered, C26 test_label_unit_must_be_open_registered, C27
test_goal_mismatch_refuses_exit_2, C32 test_item_ambiguous_refuses_exit_2, C37
test_decompose_child_and_chain_prerequisite_refused_with_interim_lever.
RESIDUAL (not closable in code): an agent that writes a registered open unit's `Feature:` marker into its
`--body-file` buys `different-feature`; the registry check and goal binding narrow it, they do not remove it.
"""
import json
import pathlib

import pytest

import test_handoff as th

handoff = th.handoff
fr = handoff._followup_route()
feature_registry = th.feature_registry
CFG = dict(th.ON, followups={"mode": "in-goal"})
BASE = ["--area", "engine", "--why", "found while here", "--queue", "queued", "--assignee", "same-area"]


class UnitSource(th.FakeSource):
    def __init__(self, body="Feature: alpha\n", labels=(), fail=None):
        super().__init__(number="61")
        self.goal_issue = {"body": body, "labels": list(labels)}
        self.fail = fail

    def fetch_body_labels(self, goal):
        if self.fail:
            raise RuntimeError(self.fail)
        return self.goal_issue


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "_duplicate_search", lambda *a, **k: {"schema": "brainstorm-dedup/v1",
                                                                      "candidates": [], "degraded": []})
    monkeypatch.chdir(tmp_path)

    def build(source=None, config=CFG, units=("alpha", "beta"), closed=()):
        src = source or UnitSource()
        monkeypatch.setattr(handoff.sources, "get_source", lambda *a, **k: src)
        sdlc = th._project(tmp_path, config)
        entries = {n: {"title": "t", "owner": None, "open": n not in closed, "parent": None,
                       "tracking_issue": None, "repos": {}} for n in units}
        feature_registry.write_index(feature_registry.registry_dir(sdlc), entries)
        return sdlc, src
    return build


def track(sdlc, *extra, goal="7", blocks="no", body=None, tmp=None):
    args = ["handoff.py", "track", str(sdlc), goal, *BASE, "--blocks", blocks, *extra]
    if body is not None:
        f = pathlib.Path(sdlc).parent / "body.md"
        f.write_text(body)
        args += ["--body-file", str(f)]
    return handoff.main(args)


@pytest.mark.parametrize("kind", ["followup", "prerequisite", "cleanup", "child"])
@pytest.mark.parametrize("blocks", ["yes", "no"])
def test_every_kind_and_blocks_route(make, kind, blocks, capsys):
    sdlc, src = make()
    assert track(sdlc, "--kind", kind, blocks=blocks) == handoff.FOLLOWUP_REFUSED_EXIT
    err = capsys.readouterr().err
    assert src.created is None and "work.py add-slice" in err


@pytest.mark.parametrize("kind", ["child", "prerequisite"])
def test_decompose_child_and_chain_prerequisite_refused_with_interim_lever(make, capsys, kind):
    sdlc, src = make()
    assert track(sdlc, "--kind", kind) == 3
    err = capsys.readouterr().err
    assert "followups.mode: file" in err and "work.py add-slice" in err and src.created is None


def test_decompose_instructions_name_the_conflict():
    text = (pathlib.Path(handoff.__file__).parent / "decompose_goal.py").read_text(encoding="utf-8")
    assert "followups.mode: file" in text and "repo-wide" in text and "every goal" in text


def test_local_source_goal_reads_no_unit_and_the_refusal_names_add_slice(make, capsys):
    sdlc, src = make(source=th.FakeSource(number="61"))
    assert track(sdlc) == 3 and "work.py add-slice" in capsys.readouterr().err


def test_separate_filing_carries_the_reason_marker(make):
    sdlc, src = make()
    assert track(sdlc, body="why\n\nFeature: beta\n") == 0
    assert "<!-- sigma:followup-reason:different-feature -->" in src.created["body"]
    assert fr.parse_reason(src.created["body"]) == "different-feature"


def test_parent_comment_names_the_reason(make):
    sdlc, src = make()
    assert track(sdlc, body="x\n\nFeature: beta\n") == 0
    assert any("different-feature" in text for goal, text in src.notes if goal == "7")


def test_parent_comment_failure_warns_and_keeps_the_filing(make, capsys):
    sdlc, src = make()

    def boom(goal, text):
        if "SEPARATE" in text:
            raise RuntimeError("no comment")
        src.notes.append((goal, text))
    src.note = boom
    assert track(sdlc, body="x\n\nFeature: beta\n") == 0
    assert src.created is not None and "followup-reason" in src.created["body"]
    assert "could not comment the follow-up reason" in capsys.readouterr().err


def test_at_cap_files_with_slice_cap(make):
    sdlc, src = make(config=dict(th.ON, followups={"mode": "in-goal", "max_added_slices": 2}))
    for n in ("a", "b"):
        fr.append_slice(sdlc, "7", n, "s")
    assert track(sdlc) == 0
    assert fr.parse_reason(src.created["body"]) == "slice-cap"
    assert any("slice-cap" in text for _, text in src.notes)


def test_under_cap_same_feature_is_refused(make):
    sdlc, src = make(config=dict(th.ON, followups={"mode": "in-goal", "max_added_slices": 2}))
    fr.append_slice(sdlc, "7", "a", "s")
    assert track(sdlc) == 3


def test_target_unit_does_not_buy_a_separate_goal(make):
    sdlc, src = make()
    assert track(sdlc, "--target-unit", "beta") == 3 and src.created is None


def test_target_unit_on_a_separate_filing_still_stamps_it(make):
    sdlc, src = make()
    assert track(sdlc, "--target-unit", "beta", body="x\n\nFeature: beta\n") == 0
    assert "Feature: beta" in src.created["body"]


def test_undeclared_unit_name_inherits_with_warning(make, capsys):
    sdlc, src = make()
    assert track(sdlc, body="x\n\nFeature: nowhere\n") == 3
    assert "nowhere" in capsys.readouterr().err


def test_body_file_marker_must_be_open_registered(make):
    sdlc, src = make(units=("alpha",))
    assert track(sdlc, body="x\n\nFeature: beta\n") == 3


def test_body_file_marker_naming_a_closed_unit_inherits(make):
    sdlc, src = make(units=("alpha", "beta"), closed=("beta",))
    assert track(sdlc, body="x\n\nFeature: beta\n") == 3


def test_label_unit_must_be_open_registered(make):
    sdlc, src = make(units=("alpha",))
    assert track(sdlc, "--label", "feature:beta") == 3


def test_label_naming_an_open_registered_unit_is_a_different_feature(make):
    sdlc, src = make(units=("alpha", "beta"))
    assert track(sdlc, "--label", "feature:beta") == 0
    assert fr.parse_reason(src.created["body"]) == "different-feature"


def test_body_wins_over_label(make):
    sdlc, src = make()
    assert track(sdlc, "--label", "feature:beta", body="x\n\nFeature: alpha\n") == 3


def test_item_ambiguous_refuses_exit_2(make):
    sdlc, src = make()
    assert track(sdlc, body="Feature: alpha\n\nFeature: beta\n") == 2 and src.created is None


def test_ambiguous_goal_refuses_exit_2(make):
    sdlc, src = make(source=UnitSource(body="Feature: alpha\n\nFeature: beta\n"))
    assert track(sdlc) == 2 and src.created is None


def test_goal_read_error_refuses_exit_2(make):
    sdlc, src = make(source=UnitSource(fail="gh is down"))
    assert track(sdlc) == 2 and src.created is None


def test_goal_with_no_unit_and_a_declaring_item_is_a_different_feature(make):
    sdlc, src = make(source=UnitSource(body="nothing here\n"))
    assert track(sdlc, body="x\n\nFeature: beta\n") == 0


def test_item_with_no_declared_feature_is_a_slice(make):
    sdlc, src = make()
    assert track(sdlc) == 3


def test_goal_mismatch_refuses_exit_2(make, tmp_path, monkeypatch):
    sdlc, src = make()
    tree = tmp_path / "trees" / "B"
    tree.mkdir(parents=True)
    rec = sdlc / "state" / "work" / "B.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"worktree": str(tree)}))
    monkeypatch.chdir(tree)
    assert track(sdlc, goal="A") == 2 and src.created is None
    assert track(sdlc, goal="B") == 3                      # naming the bound goal routes normally


def test_a_nogoal_filing_from_inside_a_goal_worktree_is_refused(make, tmp_path, monkeypatch):
    sdlc, src = make()
    tree = tmp_path / "trees" / "B"
    tree.mkdir(parents=True)
    rec = sdlc / "state" / "work" / "B.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"worktree": str(tree)}))
    monkeypatch.chdir(tree)
    with pytest.raises(fr.RouteRefused) as refused:
        handoff.create_tracked_issue(sdlc, CFG, None, "engine", "why", same_area=True, immediately_actionable=False,
                                     blocks_goal=False, source=src, title="t")
    assert refused.value.code == 2 and src.created is None
    monkeypatch.chdir(tmp_path)                            # the root checkout: residual, files as before
    handoff.create_tracked_issue(sdlc, CFG, None, "engine", "why", same_area=True, immediately_actionable=False,
                                 blocks_goal=False, source=src, title="t")
    assert src.created is not None


def test_a_nogoal_filing_is_untouched_under_file_mode(make, tmp_path, monkeypatch):
    sdlc, src = make(config=dict(th.ON, followups={"mode": "file"}))
    tree = tmp_path / "trees" / "B"
    tree.mkdir(parents=True)
    rec = sdlc / "state" / "work" / "B.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"worktree": str(tree)}))
    monkeypatch.chdir(tree)
    handoff.create_tracked_issue(sdlc, dict(th.ON, followups={"mode": "file"}), None, "engine", "why", same_area=True,
                                 immediately_actionable=False, blocks_goal=False, source=src, title="t")
    assert src.created is not None


def test_file_mode_files_as_before_with_no_marker(make):
    sdlc, src = make(config=dict(th.ON, followups={"mode": "file"}))
    assert track(sdlc) == 0 and "followup-reason" not in src.created["body"]

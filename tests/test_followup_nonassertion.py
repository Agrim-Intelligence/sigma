"""Pure move (#2224 amendment 2) from tests/test_followup_mode.py, tests/test_followup_record.py, tests/test_followup_track_routing.py at 4042e97: the six nodes that fail at base by something other than an assertion
(DID NOT RAISE, or a JSONDecodeError escaping) because the signature-only stubs have no refusal logic. Test bodies byte-identical.

Run: python3 -m pytest tests/test_followup_nonassertion.py
(generic form: python -m pytest tests/test_followup_nonassertion.py)

From tests/test_followup_mode.py (docstring tail):
CONTROLS note at the bottom). The default must change nothing: every refusal test has a twin at
the default that files.

From tests/test_followup_record.py (docstring tail):
CONTROLS: C5 test_two_appenders_both_land_deterministic (the system of record: a pause between read and write inside
the lock; the threaded variant is a labeled SMOKE test, its sensitivity measured in .sdlc/evidence/2224-controls.md),
C6 test_corrupt_record_refuses, C29 test_fcntl_absent_refuses.

From tests/test_followup_track_routing.py (docstring tail):
CONTROLS: C13/C14 test_every_kind_and_blocks_route, C15 test_target_unit_does_not_buy_a_separate_goal, C16
test_separate_filing_carries_the_reason_marker, C17 test_parent_comment_names_the_reason, C18
test_ambiguous_goal_refuses_exit_2, C24 test_undeclared_unit_name_inherits_with_warning, C25
test_body_file_marker_must_be_open_registered, C26 test_label_unit_must_be_open_registered, C27
test_goal_mismatch_refuses_exit_2, C32 test_item_ambiguous_refuses_exit_2, C37
test_unproven_child_and_chain_prerequisite_refused_without_naming_a_lever (A5: no lever is named).
RESIDUAL (not closable in code): an agent that writes a registered open unit's `Feature:` marker into its
`--body-file` buys `different-feature`; the registry check and goal binding narrow it, they do not remove it.
"""
import json
import pathlib

import pytest

import test_handoff as th

handoff = th.handoff

ARGS = ["--area", "engine", "--why", "found while here", "--queue", "queued", "--assignee", "same-area"]


@pytest.fixture
def src(monkeypatch):
    fake = th.FakeSource(number="61")
    monkeypatch.setattr(handoff.sources, "get_source", lambda *a, **k: fake)
    monkeypatch.setattr(handoff, "_duplicate_search", lambda *a, **k: {"schema": "brainstorm-dedup/v1",
                                                                      "candidates": [], "degraded": []})
    return fake


@pytest.fixture
def sdlc(tmp_path):
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    return tmp_path / ".sdlc"


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


# --- #2224 amendment A2-A4: a PROVEN decompose child is exempt from routing; nothing else is ----------------------
# Driven through `handoff.main(["handoff.py", "track", ...])`. The meta-goal M is #21 decomposing parent #9.
# CONTROLS: C42 test_track_has_no_exemption_bypass, C43 test_child_exempt_kind_only_child, C44 test_exempt_needs_meta_marker,
# C45 test_exempt_needs_parent_comment, C46 test_exempt_needs_child_marker_same_parent, C47 test_exempt_needs_independent_plan_review
# (and the route / plan-file clauses beside it), C48 test_exempt_fails_closed_on_unreadable_leg, C49 test_exempt_child_cap,
# C50 test_other_kinds_and_blocks_yes_stay_routed, C51 test_unknown_mode_warns_once_naming_the_value /
# test_absent_or_valid_mode_is_silent, C52 test_warning_is_printed_once_from_track, C53 test_followup_mode_verb_is_silent_and_unchanged,
# C58 test_exempt_still_binds_the_goal_to_the_worktree (R4), C59 test_exempt_goal_spellings (R6).
M, P = "21", "9"

CHILD_BODY = "<!-- sigma:decomposed-from=#9 -->\nBlocked by #3\n\nchild content\n"


def child(sdlc, *extra, goal=M, blocks="no", body=CHILD_BODY):
    return track(sdlc, "--kind", "child", *extra, goal=goal, blocks=blocks, body=body)


def refused(sdlc, src, capsys, leg, **kw):
    assert child(sdlc, **kw) == handoff.FOLLOWUP_REFUSED_EXIT, leg
    err = capsys.readouterr().err
    assert src.created is None and src.notes == [], leg
    assert "[%s]" % leg in err and "work.py add-slice" in err and "mode: file" not in err, (leg, err)


def test_track_exits_2_and_files_nothing_on_an_unreadable_config(tmp_path, src):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text("{not json")
    rc = handoff.main(["handoff.py", "track", str(sdlc), "g.md", *ARGS, "--blocks", "no"])
    assert rc == 2 and src.created is None


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


def test_fcntl_absent_refuses(sdlc, monkeypatch):
    monkeypatch.setattr(fr, "fcntl", None)
    with pytest.raises(fr.LockUnavailable):
        fr.append_slice(sdlc, "2224", "aaa", "s")
    assert not fr.record_path(sdlc, "2224").exists()


def test_unsafe_goal_refuses(sdlc):
    with pytest.raises(ValueError):
        fr.record_path(sdlc, "../x")


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

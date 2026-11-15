"""Pure move from tests/test_followup_route.py at 4042e97 (#2224 amendment 2): the wholly assertion-red nodes; test bodies byte-identical.

The one routing function for follow-ups (#2224): recorded facts in, `slice` or `separate(<reason>)` out.

Run: python3 -m pytest tests/test_followup_route*.py
(generic form: python -m pytest tests/test_followup_route*.py)

CONTROLS (each clause broken alone and seen red; evidence in .sdlc/evidence/2224-controls.md):
  C1 test_undeclared_item_inherits   C2 test_different_feature   C3 test_slice_cap_at_exactly_cap
  C4 test_different_feature_wins_over_cap   C24 test_non_registered_unit_inherits   C28 test_body_wins_over_label

"""
import importlib.util
import itertools
import pathlib

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fr = _mod("followup_route")
feature_registry = _mod("feature_registry")
CAP = 10


def test_undeclared_item_inherits():
    assert fr.route("A", None, 0, CAP) == ("slice", None)


def test_different_feature():
    assert fr.route("A", "B", 0, CAP) == ("separate", "different-feature")


def test_goal_without_unit_and_item_with_one_is_a_different_feature():
    assert fr.route(None, "B", 0, CAP) == ("separate", "different-feature")


def test_both_undeclared_is_a_slice():
    assert fr.route(None, None, 0, CAP) == ("slice", None)


def test_same_unit_is_a_slice_under_the_cap():
    assert fr.route("A", "A", CAP - 1, CAP) == ("slice", None)


def test_slice_cap_at_exactly_cap():
    assert fr.route("A", "A", CAP, CAP) == ("separate", "slice-cap")
    assert fr.route("A", None, CAP + 5, CAP) == ("separate", "slice-cap")


def test_different_feature_wins_over_cap():
    assert fr.route("A", "B", CAP + 3, CAP) == ("separate", "different-feature")


def test_reasons_are_a_closed_set_by_exhaustive_grid():
    seen = set()
    for g, i, added, cap in itertools.product([None, "A"], [None, "A", "B"], range(0, 13), [1, 3, 10]):
        verdict, reason = fr.route(g, i, added, cap)
        assert verdict in ("slice", "separate")
        assert (reason is None) == (verdict == "slice")
        if reason:
            seen.add(reason)
    assert seen == set(fr.REASONS) == {"different-feature", "slice-cap"}


def _registry(tmp_path, names, closed=()):
    entries = {n: {"title": "t", "owner": None, "open": n not in closed, "parent": None,
                   "tracking_issue": None, "repos": {}} for n in list(names)}
    sdlc = tmp_path / ".sdlc"
    feature_registry.write_index(feature_registry.registry_dir(sdlc), entries)
    return sdlc


def test_non_registered_unit_inherits(tmp_path):
    sdlc = _registry(tmp_path, ["alpha"])
    unit, warnings = fr.item_unit(sdlc, "Feature: nowhere\n", [])
    assert unit is None and any("nowhere" in w for w in warnings)


def test_closed_unit_is_not_a_declaration(tmp_path):
    sdlc = _registry(tmp_path, ["alpha", "old"], closed=["old"])
    assert fr.item_unit(sdlc, "Feature: old\n", [])[0] is None


def test_registered_open_unit_is_a_declaration_in_body_or_label(tmp_path):
    sdlc = _registry(tmp_path, ["alpha"])
    assert fr.item_unit(sdlc, "Feature: alpha\n", [])[0] == "alpha"
    assert fr.item_unit(sdlc, None, ["feature:alpha"])[0] == "alpha"


def test_body_wins_over_label(tmp_path):
    sdlc = _registry(tmp_path, ["alpha", "beta"])
    assert fr.item_unit(sdlc, "Feature: alpha\n", ["feature:beta"])[0] == "alpha"


def test_item_with_nothing_declared_reads_none(tmp_path):
    sdlc = _registry(tmp_path, ["alpha"])
    assert fr.item_unit(sdlc, None, []) == (None, [])


GOOD_GATE = {"plan_present": True, "plan_ok": True, "plan_review_ok": True, "route": "subagent", "agent_id": "a1"}


def dd(**over):
    facts = dict(kind="child", goal="21", m_first_line="<!-- sigma:decompose-of=#9 -->", m_labels=["sdlc:decompose"],
                 parent_comments=["Too large. <!-- sigma:decompose-filed=#21 -->"],
                 child_first_line="<!-- sigma:decomposed-from=#9 -->", gate=dict(GOOD_GATE), children_filed=0,
                 max_children=8)
    facts.update(over)
    return fr.decompose_decision(**facts)


def test_all_legs_proven_is_exempt():
    assert dd() == (True, "")


@pytest.mark.parametrize("kind", ["prerequisite", "cleanup", "followup", None, "Child", ""])
def test_child_exempt_kind_only_child(kind):
    ok, why = dd(kind=kind)
    assert (ok, why) == (False, "kind")


@pytest.mark.parametrize("line", ["intro\n<!-- sigma:decompose-of=#9 -->", "", "<!-- sigma:decompose-of=# -->",
                                  "<!-- sigma:decompose-of=#9x -->", "text <!-- sigma:decompose-of=#9 -->"])
def test_exempt_needs_meta_marker(line):
    # the marker must be the WHOLE first line; `dd` passes only a first line, so "intro\n..." models a later-line marker
    assert dd(m_first_line=line) == (False, "meta-marker")


def test_exempt_meta_marker_may_be_the_legacy_spelling():
    assert dd(m_first_line="<!-- loopsmith:decompose-of=#9 -->")[0] is True


def test_a_goal_cannot_decompose_itself():
    assert dd(m_first_line="<!-- sigma:decompose-of=#21 -->", child_first_line="<!-- sigma:decomposed-from=#21 -->") \
        == (False, "meta-marker")


@pytest.mark.parametrize("labels", [[], ["sdlc:goal"], ["sdlc:decompose-ish"]])
def test_exempt_needs_the_decompose_label_on_the_meta_goal(labels):
    assert dd(m_labels=labels) == (False, "meta-label")


def test_exempt_needs_parent_comment():
    assert dd(parent_comments=[]) == (False, "parent-comment")
    assert dd(parent_comments=["Too large. <!-- sigma:decompose-filed=#22 -->"]) == (False, "parent-comment")
    assert dd(parent_comments=["<!-- sigma:decompose-filed=#2 -->", "<!-- sigma:decompose-filed=#210 -->"]) \
        == (False, "parent-comment")                    # #2 and #210 are not #21: equality, not a prefix or membership
    assert dd(parent_comments=["x", "<!-- sigma:decompose-filed=#21 -->"])[0] is True


@pytest.mark.parametrize("goal", ["21", "#21", "21.md", " 21 ", 21])
def test_goal_spellings_all_match_the_marker(goal):
    assert dd(goal=goal)[0] is True


@pytest.mark.parametrize("goal", ["22", "#22", "22.md", "", "abc", "21x", None])
def test_goal_spellings_are_each_tested_against_marker_equality(goal):
    assert dd(goal=goal)[0] is False


def test_exempt_needs_child_marker_same_parent():
    assert dd(child_first_line=None) == (False, "child-marker")
    assert dd(child_first_line="") == (False, "child-marker")
    assert dd(child_first_line="<!-- sigma:decomposed-from=#8 -->") == (False, "child-marker")
    assert dd(child_first_line="intro") == (False, "child-marker")
    assert dd(child_first_line="<!-- sigma:decomposed-from=#99 -->") == (False, "child-marker")


@pytest.mark.parametrize("gate,why", [
    ({"plan_present": False}, "plan"),
    ({"plan_ok": False}, "plan"),
    ({"plan_review_ok": False}, "plan-review"),
    ({"route": "inline"}, "plan-review-route"),
    ({"route": None}, "plan-review-route"),
    ({"agent_id": ""}, "plan-review-route"),
    ({"agent_id": None}, "plan-review-route"),
])
def test_exempt_needs_independent_plan_review(gate, why):
    assert dd(gate=dict(GOOD_GATE, **gate)) == (False, why)


@pytest.mark.parametrize("name", ["m_first_line", "m_labels", "parent_comments", "gate", "children_filed"])
def test_exempt_fails_closed_on_unreadable_leg(name):
    ok, why = dd(**{name: None})
    assert ok is False and why.startswith("unreadable:"), (name, why)


def test_exempt_child_cap():
    assert dd(children_filed=7)[0] is True
    assert dd(children_filed=8) == (False, "child-cap")
    assert dd(children_filed=9, max_children=8) == (False, "child-cap")


def test_every_refusal_code_has_a_text_naming_the_leg_and_add_slice_and_never_the_lever():
    for code in ("kind", "meta-marker", "meta-label", "parent-comment", "child-marker", "plan", "plan-review",
                 "plan-review-route", "child-cap", "unreadable:gate"):
        text = fr.decompose_refusal(code)
        assert code.split(":")[-1].split("-")[0] in text and "work.py add-slice" in text, code
        assert "followups.mode" not in text, code


def test_decompose_decision_reads_no_environment_or_config(monkeypatch):
    names = set(fr.decompose_decision.__code__.co_names) | set(fr.decompose_decision.__code__.co_varnames)
    assert not names & {"os", "environ", "getenv", "open", "config", "load_config", "_load", "Path", "pathlib"}, names
    for var in ("SIGMA_DECOMPOSE", "FOLLOWUP_DECOMPOSE", "SIGMA_FOLLOWUP_MODE"):
        monkeypatch.setenv(var, "1")
    assert dd(kind="followup") == (False, "kind")

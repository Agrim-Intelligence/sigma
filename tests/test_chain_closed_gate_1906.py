"""#1906: a closed prerequisite that did not meet its goal.

When a closed prerequisite has open `Follow-up to|of` children, or an unanswered owner question as its newest
comment, the plan lists a warning naming the prerequisite, the follow-ups and the dependents, and the sheet
turns it into a question (keep waiting, or proceed). The warning is advisory: it adds no edge. At most
`chain.max_closed_checks` (default 10) closed prerequisites are inspected per plan, and the plan says how many were
skipped. A stateful fake `gh` that serves GETs only for the plan; no network.

Controls (each clause alone, then restored; every one was seen red): the check removed; the follow-up form not
read; any comment (not the newest) counts as the open question; the bound removed; "keep waiting" not held at arm;
"keep waiting" not skipped at run; the recommended answer made "proceed".
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resource_mode  # noqa: E402,F401
import test_chain_arm as tca  # noqa: E402
import test_chain_plan as tcp  # noqa: E402
import test_chain_run as tcr  # noqa: E402
import test_label_answer as tla  # noqa: E402
from test_label_drain_arm import DW, _mod  # noqa: E402

OPTIONS = ["keep waiting", "proceed"]
QUESTION = "Owner question: should the legacy column stay?"


def _backlog(goal_armed=True):
    """1 waits on closed 2 (open follow-up 7), closed 3 (owner question newest), closed 6 (nothing open) and
    open 4. 8 is a follow-up of something else; 9 is a closed follow-up of 2."""
    labels = ("sdlc:goal",) if goal_armed else ()
    return [tla.issue(1, *labels, assignees=("alice",) if goal_armed else (),
                      body="Blocked by #2\nBlocked by #3\nBlocked by #6\nBlocked by #4\n\n" + DW),
            tla.issue(2, "sdlc:goal", state="closed", comments=["done, but measured 40 against a target of 60"]),
            tla.issue(3, "sdlc:goal", state="closed", comments=["merged", QUESTION]),
            tla.issue(4, "sdlc:goal", assignees=("alice",), body=DW),
            tla.issue(6, "sdlc:goal", state="closed", comments=[QUESTION, "answered: keep it"]),
            tla.issue(7, "sdlc:goal", "sdlc:followup", body="Follow-up to #2: the target was missed.\n\n" + DW),
            tla.issue(8, "sdlc:goal", "sdlc:followup", body="Follow-up to #99.\n\n" + DW),
            tla.issue(9, "sdlc:goal", "sdlc:followup", state="closed", body="Follow-up to #2.\n\n" + DW)]


def _fake(issues):
    return tcp._fake([dict(i, _comments=[c["body"] if isinstance(c, dict) else c for c in i.get("_comments", [])])
                      for i in issues])


def _plan(tmp_path, issues=None, chain_cfg=None, goal_armed=True):
    base = tcp._repo(tmp_path, chain_cfg=chain_cfg, dag_mode="off")
    run = _fake(issues if issues is not None else _backlog(goal_armed))
    return base, tca._chain().plan(base, 1, run=run, now=tcp.NOW)


# 1 -- the warning ------------------------------------------------------------------------------------------

def test_the_plan_warns_for_a_follow_up_and_for_an_open_question_and_not_for_a_clean_closure(tmp_path):
    _base, p = _plan(tmp_path)
    assert p["closed_gates"] == [{"prerequisite": 2, "dependents": [1], "followups": [7], "question": False},
                                 {"prerequisite": 3, "dependents": [1], "followups": [], "question": True}]
    assert p["done"] == [2, 3, 6] and p["closed_checked"] == 3 and p["closed_skipped"] == []


def test_the_warning_is_advisory_it_adds_no_edge_and_no_member(tmp_path):
    _base, p = _plan(tmp_path)
    assert sorted(r["number"] for r in p["members"]) == [1, 4]
    assert [(e["from"], e["to"]) for e in p["edges"]] == [(4, 1)]
    assert p["complete"] is True


def test_the_record_holds_numbers_only(tmp_path):
    base, _p = _plan(tmp_path)
    text = (pathlib.Path(base) / "state" / "chains" / "c1.json").read_text()
    assert "target was missed" not in text and "legacy column" not in text and "SECRET" not in text


def test_the_plan_text_lists_the_warning_and_the_counts():
    ch = tca._chain()
    out = ch.render({"goal": 1, "members": [], "complete": True, "source": "live", "source_reason": "off",
                     "calls": {"total": 3, "get": 3, "list": 0}, "bound": 50, "done": [2, 3], "externals": [],
                     "edges": [], "cross_unit": [], "priority_inversions": [], "higher_priority_ahead": 0,
                     "weak_mentions": 0, "stops": [], "record": "r",
                     "closed_gates": [{"prerequisite": 2, "dependents": [1], "followups": [7], "question": False}],
                     "closed_checked": 1, "closed_skipped": [5, 6]})
    assert "warning: closed #2 with open follow-up #7; #1 waits on it (advisory, no edge added)" in out
    assert "closed prerequisites inspected: 1, skipped: 2 (#5, #6)" in out


def test_a_plan_with_nothing_to_report_keeps_its_old_shape(tmp_path):
    issues = [i for i in _backlog() if i["number"] in (1, 4, 6)]
    issues[0]["body"] = "Blocked by #6\nBlocked by #4\n\n" + DW
    base, p = _plan(tmp_path, issues=issues)
    assert p["closed_gates"] == [] and p["closed_checked"] == 1
    assert "closed prerequisites inspected" not in tca._chain().render(p)


def test_the_same_warning_comes_from_the_map_path(tmp_path):
    base = tcp._repo(tmp_path)
    tcp._save_map(base, [tcp._mnode(1), tcp._mnode(2, state="closed"), tcp._mnode(4)], [(2, 1), (4, 1)])
    p = tca._chain().plan(base, 1, run=_fake(_backlog()), now=tcp.NOW)
    assert p["source"] == "map"
    assert [(w["prerequisite"], w["dependents"], w["followups"]) for w in p["closed_gates"]] == [(2, [1], [7])]


# 2 -- what counts ---------------------------------------------------------------------------------------------

def test_only_an_open_follow_up_of_the_prerequisite_in_a_follow_up_form_counts():
    cc = _mod("chain_closed")
    issues = [{"number": 7, "state": "open", "body": "Follow-up to #2."},
              {"number": 8, "state": "open", "body": "follow up of #2 and more"},
              {"number": 9, "state": "closed", "body": "Follow-up to #2."},
              {"number": 10, "state": "open", "body": "Blocked by #2"},
              {"number": 11, "state": "open", "body": "Follow-up to #99"},
              {"number": 12, "state": "open", "body": "we filed a follow-up to cover the cap, tracked in #2"}]
    assert cc.followups_of(issues, {2}) == {2: [7, 8]}


def test_the_question_must_be_the_newest_comment_and_carry_a_marker():
    cc = _mod("chain_closed")
    c = lambda body, day: {"body": body, "created_at": "2026-01-%02dT00:00:00Z" % day}       # noqa: E731
    assert cc.has_open_question([c("merged", 1), c(QUESTION, 2)])
    assert cc.has_open_question([c("- **Question for the owner:** keep it?", 2), c("merged", 1)])
    assert not cc.has_open_question([c(QUESTION, 1), c("answered", 2)])
    assert not cc.has_open_question([c("merged", 1), c("what do you think? the owner", 2)])
    assert not cc.has_open_question([])


# 3 -- the sheet ------------------------------------------------------------------------------------------------

def test_the_sheet_asks_keep_waiting_or_proceed_and_the_default_waits(tmp_path):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    ch = tca._chain()
    qs = {q["id"]: q for q in ch.questions_of(tca._rec(base), {}) if q["kind"] == "closed_gate"}
    assert sorted(qs) == ["closed_gate:2", "closed_gate:3"]
    q = qs["closed_gate:2"]
    assert q["options"] == OPTIONS and q["recommended"] == "keep waiting" and q["unblocks"] == [1]
    assert "#2" in q["ask"] and "#7" in q["ask"] and "#1" in q["ask"] and q["ask"].endswith("?")
    assert "#3" in qs["closed_gate:3"]["ask"] and "question" in qs["closed_gate:3"]["ask"]
    assert tca._rec(base)["answers"]["closed_gate:2"] == "keep waiting"
    text = ch.sheet(base, 1, now=tca.NOW)
    assert "closed_gate:2" in text and "closed prerequisites inspected: 3, skipped: 0" in text


def test_a_clean_closure_makes_no_question(tmp_path):
    issues = [i for i in _backlog() if i["number"] in (1, 4, 6)]
    issues[0]["body"] = "Blocked by #6\nBlocked by #4\n\n" + DW
    base, fk = tca._scene(tmp_path, issues=issues)
    assert not [q for q in tca._chain().questions_of(tca._rec(base), {}) if q["kind"] == "closed_gate"]
    assert tca._rec(base)["closed_gates"] == []


# 4 -- keep waiting holds the dependent; proceed lets it go -------------------------------------------------------

def test_keep_waiting_leaves_the_dependent_unarmed_and_proceed_arms_it(tmp_path):
    for answer, armed in (("keep waiting", False), ("proceed", True)):
        base, fk = tca._scene(tmp_path / answer.replace(" ", "-"), issues=_backlog(goal_armed=False))
        ch = tca._chain()
        assert ch.answer(base, 1, {"closed_gate:2": answer, "closed_gate:3": answer}, run=fk)["state"] == "approved"
        text = ch.sheet(base, 1, now=tca.NOW)
        assert ("#1: arm it and add one comment" in text) is armed
        ch.arm(base, 1, run=fk)
        assert ("sdlc:goal" in fk._names(1)) is armed, answer
        assert "chain:1" in fk._names(1)                      # a member either way


def test_one_answer_to_proceed_is_not_enough_while_the_other_says_keep_waiting(tmp_path):
    base, fk = tca._scene(tmp_path, issues=_backlog(goal_armed=False))
    ch = tca._chain()
    ch.answer(base, 1, {"closed_gate:2": "proceed", "closed_gate:3": "keep waiting"}, run=fk)
    ch.arm(base, 1, run=fk)
    assert "sdlc:goal" not in fk._names(1)


def test_the_run_skips_a_dependent_held_by_keep_waiting(tmp_path):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    ch = tca._chain()
    ch.arm(base, 1, run=fk, now=tca.NOW)
    pick = tcr.FakePick(fk, base)
    tcr._run(ch, base, fk, pick)
    assert "1" in pick.skips[-1], pick.skips                   # the default answer is keep waiting
    ch.answer(base, 1, {"closed_gate:2": "proceed", "closed_gate:3": "proceed"}, run=fk)
    tcr._run(ch, base, fk, pick)
    assert "1" not in pick.skips[-1]


# 5 -- bounded ------------------------------------------------------------------------------------------------------

def _many(n):
    issues = [tla.issue(1, "sdlc:goal", assignees=("alice",),
                        body="".join("Blocked by #%d\n" % (10 + i) for i in range(n)) + "\n" + DW)]
    issues += [tla.issue(10 + i, "sdlc:goal", state="closed", comments=["done"]) for i in range(n)]
    return issues


def test_at_most_a_fixed_number_of_closed_prerequisites_are_inspected_and_the_plan_says_how_many_were_skipped(tmp_path):
    base = tcp._repo(tmp_path, dag_mode="off")
    calls = []
    p = tca._chain().plan(base, 1, run=tcp._fake([dict(i, _comments=["done"] if i["state"] == "closed" else [])
                                                  for i in _many(14)], calls), now=tcp.NOW)
    assert p["closed_checked"] == 10 and p["closed_skipped"] == [20, 21, 22, 23]
    comment_reads = [c for c in calls if c[1].endswith("/comments")]
    assert len(comment_reads) <= 10
    assert "closed prerequisites inspected: 10, skipped: 4" in tca._chain().render(dict(p, record="r"))


def test_the_bound_is_configurable_and_zero_turns_the_check_off(tmp_path):
    _base, p = _plan(tmp_path / "two", issues=_many(5), chain_cfg={"max_closed_checks": 2})
    assert p["closed_checked"] == 2 and len(p["closed_skipped"]) == 3
    calls = []
    base = tcp._repo(tmp_path / "off", chain_cfg={"max_closed_checks": 0}, dag_mode="off")
    p = tca._chain().plan(base, 1, run=tcp._fake([dict(i, _comments=[]) for i in _many(5)], calls), now=tcp.NOW)
    assert p["closed_checked"] == 0 and p["closed_gates"] == [] and p["closed_skipped"] == []
    assert not [c for c in calls if c[1].endswith("/comments") or "sdlc:followup" in " ".join(c)]


# 6 -- fails open ------------------------------------------------------------------------------------------------------

def test_an_unreadable_follow_up_list_costs_only_that_signal(tmp_path):
    base = tcp._repo(tmp_path, dag_mode="off")
    good = _fake(_backlog())

    def run(args):
        if "labels=sdlc:followup" in " ".join(str(a) for a in args) or "sdlc:followup" in " ".join(str(a) for a in args):
            raise RuntimeError("HTTP 502")
        return good(args)
    run.calls = good.calls
    p = tca._chain().plan(base, 1, run=run, now=tcp.NOW)
    assert [w["prerequisite"] for w in p["closed_gates"]] == [3]           # the question half still works
    assert p["closed_unread"] == [2, 3, 6] and p["complete"] is True


def test_the_check_removes_nothing_from_the_documented_config_keys():
    root = pathlib.Path(__file__).resolve().parent.parent
    assert "chain.max_closed_checks" in (root / "docs" / "chain.md").read_text()

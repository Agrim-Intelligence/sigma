"""#1903: a member that carries `sdlc:blocking` but not `sdlc:goal` is not a goal anybody can start.

The plan gives it the verdict `not-a-goal` (never `armed`), the sheet asks one question about it, and neither
`chain.py arm` nor `label_drain.py arm` touches it without the one answer that arms it. A stateful fake `gh`
serves the reads and records the writes; no network.

Controls (each clause alone, then restored; every one was seen red): the verdict falls back to `unarmed`; the
map path trusts its `blocking` life; the arm step stops checking the answer (always armed / never armed); the
chain label goes on an unanswered member; the label drain's arm ignores the `promote` set; the sheet stops asking.
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resource_mode  # noqa: E402,F401
import test_chain_arm as tca  # noqa: E402
import test_chain_plan as tcp  # noqa: E402
import test_label_answer as tla  # noqa: E402
from test_label_drain_arm import DW, LBL, _mod  # noqa: E402

NOW = tcp.NOW
OPTIONS = ["leave it out", "arm it as a goal", "treat it as a checkpoint"]


def _backlog():
    """Goal 1 waits on 2 (only the blocking overlay), 3 (a goal that also blocks) and 4 (plain, unarmed)."""
    return [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Blocked by #2\nBlocked by #3\nBlocked by #4\n\n" + DW),
            tla.issue(2, "sdlc:blocking", body=DW),
            tla.issue(3, "sdlc:goal", "sdlc:blocking", assignees=("alice",), body=DW),
            tla.issue(4, body=DW)]


def _row(p, n):
    return next(r for r in p["members"] if r["number"] == n)


def _live_plan(tmp_path):
    base = tcp._repo(tmp_path, dag_mode="off")
    return tcp._plan(base, 1, [dict(i, _comments=[]) for i in _backlog()])


# 1 -- the verdict ---------------------------------------------------------------------------------------

def test_live_plan_gives_a_blocking_only_member_the_not_a_goal_verdict(tmp_path):
    p = _live_plan(tmp_path)
    assert _row(p, 2)["verdict"] == "not-a-goal"
    assert _row(p, 3)["verdict"] == "armed"                       # a goal that also blocks is still a goal
    assert _row(p, 4)["verdict"] == "unarmed"                     # and a plain unarmed issue is still just unarmed
    assert p["complete"] is True


def _map_scene(tmp_path, live):
    base = tcp._repo(tmp_path)
    tcp._save_map(base, [tcp._mnode(1), tcp._mnode(2, life="blocking"), tcp._mnode(3, life="blocking")],
                  [(2, 1), (3, 1)])
    return base, tcp._fake([dict(i, _comments=[]) for i in live])


def test_map_plan_does_not_trust_its_blocking_life(tmp_path):
    """The map keeps a life, not the labels: `blocking` is the same word for a goal that also blocks and for an
    issue that only carries the overlay. The plan reads such a member once and lets the verdict decide."""
    base, run = _map_scene(tmp_path, _backlog())
    p = tca._chain().plan(base, 1, run=run, now=NOW)
    assert p["source"] == "map"
    assert _row(p, 2)["verdict"] == "not-a-goal"
    assert _row(p, 3)["verdict"] == "armed"
    armed = [r["number"] for r in p["members"] if r["verdict"] == "armed"]
    assert 2 not in armed and armed[0] != 2                       # never in the armed set, never the first pick


def test_map_plan_fails_closed_when_a_blocking_member_cannot_be_read(tmp_path):
    base, run = _map_scene(tmp_path, [i for i in _backlog() if i["number"] != 2])
    p = tca._chain().plan(base, 1, run=run, now=NOW)
    assert _row(p, 2)["verdict"] == "unknown" and p["complete"] is False


# 2 -- the sheet asks, with no default that arms ---------------------------------------------------------

def test_sheet_asks_one_question_and_the_default_does_not_arm(tmp_path):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    ch = tca._chain()
    qs = {q["id"]: q for q in ch.questions_of(tca._rec(base), {})}
    q = qs["not_goal:2"]
    assert q["options"] == OPTIONS and q["recommended"] == "leave it out" and q["unblocks"] == [1, 2]
    assert sum(1 for k in qs if k.startswith("not_goal:")) == 1
    assert "not_goal:3" not in qs and "not_goal:4" not in qs
    assert tca._rec(base)["answers"]["not_goal:2"] == "leave it out"      # `--defaults` took the recommended one
    assert "not_goal:2" in ch.sheet(base, 1, now=NOW)


# 3 -- arm touches nothing without an answer ------------------------------------------------------------

def _touched(fk, n):
    return [w for w in tca._writes(fk) if w[0] == n]


def test_chain_arm_refuses_whole_while_the_question_is_open(tmp_path):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    rec = tca._rec(base)
    rec["answers"].pop("not_goal:2")
    tca._put(base, rec)
    ch = tca._chain()
    fk.calls.clear()
    with pytest.raises(ch.ChainRefused, match="not_goal:2"):
        ch.arm(base, 1, run=fk)
    assert fk.calls == []


@pytest.mark.parametrize("dry", [False, True])
def test_chain_arm_does_not_touch_an_unanswered_member_even_if_approval_is_bypassed(tmp_path, dry, monkeypatch):
    """The per-member clause on its own: with the approval gate switched off, a member nobody answered about
    still gets no label, no comment and no goal label."""
    base, fk = tca._scene(tmp_path, issues=_backlog())
    rec = tca._rec(base)
    rec["answers"].pop("not_goal:2")                                # no answer on the record
    tca._put(base, rec)
    ch = tca._chain()
    monkeypatch.setattr(ch, "_require_approved", lambda rec, config: None)
    res = ch.arm(base, 1, dry_run=dry, run=fk)
    assert _touched(fk, 2) == [] and "sdlc:goal" not in fk._names(2)
    assert "chain:1" not in fk._names(2)
    assert [(r["issue"], r["verdict"]) for r in res["refused"]] == [(2, "not-a-goal")]
    assert not [a for a in res["actions"] if a["issue"] == "2"]


@pytest.mark.parametrize("answer", ["leave it out", "treat it as a checkpoint"])
def test_chain_arm_leaves_a_member_alone_for_the_two_answers_that_do_not_arm(tmp_path, answer):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    ch = tca._chain()
    assert ch.answer(base, 1, {"not_goal:2": answer}, run=fk)["state"] == "approved"
    fk.calls.clear()
    res = ch.arm(base, 1, run=fk)
    assert _touched(fk, 2) == [] and "sdlc:goal" not in fk._names(2)
    assert [r["issue"] for r in res["refused"]] == [2]
    assert "sdlc:goal" in fk._names(4)                              # the plain unarmed member was armed as before


def test_chain_arm_arms_it_when_the_person_said_so(tmp_path):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    ch = tca._chain()
    assert ch.answer(base, 1, {"not_goal:2": "arm it as a goal"}, run=fk)["state"] == "approved"
    assert "#2: arm it and add one comment" in ch.sheet(base, 1, now=NOW)
    ch.arm(base, 1, run=fk)
    assert "sdlc:goal" in fk._names(2) and "chain:1" in fk._names(2)
    assert [a["login"] for a in fk.by[2]["assignees"]] == ["alice"]
    assert fk.stored_comments(2) == ["Sigma is arming this issue as part of the chain for #1."]


def test_sheet_lists_no_label_and_no_arming_write_for_an_unanswered_member(tmp_path):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    _text, labels, arms = tca._sheet_writes(base)
    assert 2 not in labels and 2 not in arms
    assert "left entirely untouched" in _text


# 4 -- the label drain has the same rule -----------------------------------------------------------------

def _drain(tmp_path, apply):
    issues = [tla.issue(1, LBL, assignees=("alice",), body="Blocked by #2\n\n" + DW), tla.issue(2, "sdlc:blocking", body=DW)]
    ld, base, fk, _cfg = tla.env(tmp_path, issues)
    return ld, base, fk, ld.arm(base, LBL, apply=apply, run=fk)


@pytest.mark.parametrize("apply", [False, True])
def test_label_drain_arm_refuses_a_blocking_only_blocker(tmp_path, apply):
    ld, base, fk, res = _drain(tmp_path, apply)
    assert [(r["issue"], r["verdict"]) for r in res["refused"]] == [(2, "not-a-goal")]
    assert not [a for a in res["actions"] if a["issue"] == "2"]
    assert not [w for w in fk.writes() if "issues/2" in " ".join(w)] and "sdlc:goal" not in fk._names(2)


def test_label_drain_plan_asks_about_it_and_answers_arm_only_what_was_named(tmp_path):
    ld, base, fk, _res = _drain(tmp_path, True)
    q = next(q for q in ld.plan(base, LBL, run=fk)["questions"] if q["id"] == "not_goal:2")
    assert q["options"] == OPTIONS and q["unblocks"] == [1]
    out = tla.ask(ld, base, fk, {"not_goal:2": "leave it out"})
    assert not out["errors"] and "sdlc:goal" not in fk._names(2)
    out = tla.ask(ld, base, fk, {"not_goal:2": "arm it as a goal"})
    assert not out["errors"] and "sdlc:goal" in fk._names(2)
    assert ld.plan(base, LBL, run=fk)["members"][0]["verdict"] in ("armed", "unassigned")


def test_the_bank_entry_obeys_the_wording_rules():
    ask = next(s for s in _mod("unpark").QUESTIONS["label_set"] if s["id"] == "not_goal")["ask"]
    assert ask.endswith("?") and ask.count("?") == 1 and ask.count(". ") == 0

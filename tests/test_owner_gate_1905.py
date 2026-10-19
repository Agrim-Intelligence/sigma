"""#1905: approving a chain is not the owner's confirmation of a follow-up that was waiting for it.

A member carrying the legacy confirmation label (`proposed_label`, default `sdlc:needs-confirmation`), with or
without `sdlc:goal`, has the verdict `awaiting-owner`. `chain.py arm` and `label_drain.py arm` never arm it, with
or without `--apply` / `--dry-run`, unless the sheet asked about that member and the person answered "promote it".
The sheet asks one question per member, grouped when several share the options, and `--defaults` answers none of them. A stateful
fake `gh`; no network.

Controls (each clause alone, then restored; every one was seen red): the label no longer blocks arming (the
verdict falls back); the answer is not read (always promoted / never promoted); the grouping is dropped; the
drain's `promote` set is ignored; a leftover label is not stripped when the member is promoted.
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

NC = "sdlc:needs-confirmation"
OPTIONS = ["leave it out", "promote it", "close it"]


def _backlog():
    """Goal 1 waits on 2 and 3 (follow-ups waiting for their owner), 5 (also a goal: the half-promoted shape)
    and 4 (plain, unarmed)."""
    return [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Blocked by #2\nBlocked by #3\nBlocked by #4\n"
                                                                  "Blocked by #5\n\n" + DW),
            tla.issue(2, NC, "sdlc:followup", body=DW),
            tla.issue(3, NC, "sdlc:followup", body=DW),
            tla.issue(4, body=DW),
            tla.issue(5, "sdlc:goal", NC, assignees=("alice",), body=DW)]


def _row(p, n):
    return next(r for r in p["members"] if r["number"] == n)


def _touched(fk, n):
    return [w for w in tca._writes(fk) if w[0] == n]


GATED = (2, 3, 5)


# 1 -- the verdict ----------------------------------------------------------------------------------------

def test_live_plan_reads_the_leftover_label_as_awaiting_the_owner_with_or_without_a_goal_label(tmp_path):
    base = tcp._repo(tmp_path, dag_mode="off")
    p = tcp._plan(base, 1, [dict(i, _comments=[]) for i in _backlog()])
    assert {n: _row(p, n)["verdict"] for n in (1, 2, 3, 4, 5)} == {
        1: "armed", 2: "awaiting-owner", 3: "awaiting-owner", 4: "unarmed", 5: "awaiting-owner"}


def test_map_plan_reads_it_too(tmp_path):
    base = tcp._repo(tmp_path)
    tcp._save_map(base, [tcp._mnode(1)] + [tcp._mnode(n, life="none") for n in (2, 3, 4)], [(n, 1) for n in (2, 3, 4)])
    p = tca._chain().plan(base, 1, run=tcp._fake([dict(i, _comments=[]) for i in _backlog()]), now=tcp.NOW)
    assert p["source"] == "map"
    assert [_row(p, n)["verdict"] for n in (2, 3, 4)] == ["awaiting-owner", "awaiting-owner", "unarmed"]


# 2 -- the sheet: one question each, grouped, and no default arms ----------------------------------------------

def test_the_sheet_asks_one_question_per_member_grouped_with_no_default_that_arms(tmp_path):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    ch = tca._chain()
    rec = tca._rec(base)
    qs = {q["id"]: q for q in ch.questions_of(rec, {}) if q["kind"] == "owner_gate"}
    assert sorted(qs) == ["owner_gate:2", "owner_gate:3", "owner_gate:5"]
    assert all(q["options"] == OPTIONS and q["recommended"] is None for q in qs.values())    # no default (#1908)
    assert [rec["answers"][k] for k in sorted(qs)] == ["leave it out"] * 3          # the person's answer, via `_scene`
    text = ch.sheet(base, 1, now=tca.NOW)
    assert "[group:owner_gate]" in text and "applies to 3 questions" in text
    assert "[owner_gate:2]" not in text                            # the three are one group, not three entries


def test_a_group_answer_reaches_every_member_of_the_group(tmp_path):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    out = tca._chain().answer(base, 1, {"group:owner_gate": "close it"}, run=fk)
    assert not out["errors"]
    assert {k: v for k, v in tca._rec(base)["answers"].items() if k.startswith("owner_gate")} == {
        "owner_gate:2": "close it", "owner_gate:3": "close it", "owner_gate:5": "close it"}


# 3 -- arm never arms one without the answer --------------------------------------------------------------------

@pytest.mark.parametrize("dry", [False, True])
def test_chain_arm_leaves_awaiting_members_alone_after_the_default_answers(tmp_path, dry):
    base, fk = tca._scene(tmp_path, issues=_backlog())            # `_scene` answered with the defaults
    res = tca._chain().arm(base, 1, dry_run=dry, run=fk)
    for n in GATED:
        assert _touched(fk, n) == [] and not [a for a in res["actions"] if a["issue"] == str(n)]
    assert NC in fk._names(2) and "sdlc:goal" not in fk._names(2) and "chain:1" not in fk._names(2)
    assert NC in fk._names(5)                                      # the half-promoted one keeps its label
    assert sorted(r["issue"] for r in res["refused"]) == [2, 3, 5]
    assert {r["verdict"] for r in res["refused"]} == {"awaiting-owner"}
    if not dry:
        assert "sdlc:goal" in fk._names(4)                         # the plain unarmed member is still armed


def test_chain_arm_leaves_them_alone_even_if_approval_is_bypassed(tmp_path, monkeypatch):
    """The per-member clause on its own: no answer on the record and the approval gate switched off."""
    base, fk = tca._scene(tmp_path, issues=_backlog())
    rec = tca._rec(base)
    for k in [k for k in rec["answers"] if k.startswith("owner_gate")]:
        rec["answers"].pop(k)
    tca._put(base, rec)
    ch = tca._chain()
    monkeypatch.setattr(ch, "_require_approved", lambda rec, config: None)
    ch.arm(base, 1, run=fk)
    assert [n for n in GATED if _touched(fk, n)] == []
    assert NC in fk._names(2) and "sdlc:goal" not in fk._names(2)


@pytest.mark.parametrize("answer", ["leave it out", "close it"])
def test_the_two_answers_that_do_not_promote_leave_the_issue_exactly_as_it_is(tmp_path, answer):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    ch = tca._chain()
    assert ch.answer(base, 1, {"group:owner_gate": answer}, run=fk)["state"] == "approved"
    fk.calls.clear()
    ch.arm(base, 1, run=fk)
    assert [n for n in GATED if _touched(fk, n)] == []
    assert fk.by[2]["state"] == "open"                               # Sigma does not close it for you


def test_promote_it_arms_the_one_member_and_strips_the_leftover_label(tmp_path):
    base, fk = tca._scene(tmp_path, issues=_backlog())
    ch = tca._chain()
    assert ch.answer(base, 1, {"owner_gate:2": "promote it", "owner_gate:5": "promote it"}, run=fk)["state"] == "approved"
    assert "#2: arm it and add one comment" in ch.sheet(base, 1, now=tca.NOW)
    fk.calls.clear()
    ch.arm(base, 1, run=fk)
    for n in (2, 5):
        names = fk._names(n)
        assert "sdlc:goal" in names and NC not in names and "chain:1" in names, (n, names)
    assert [a["login"] for a in fk.by[2]["assignees"]] == ["alice"]
    assert fk.stored_comments(2) == ["Sigma is arming this issue as part of the chain for #1."]
    assert NC in fk._names(3) and "sdlc:goal" not in fk._names(3) and _touched(fk, 3) == []     # 3 was not promoted


# 4 -- the label drain has the same rule -------------------------------------------------------------------------

def _drain(tmp_path, apply):
    issues = [tla.issue(1, LBL, "sdlc:goal", assignees=("alice",), body="Blocked by #2\n\n" + DW),
              tla.issue(2, NC, "sdlc:followup", body=DW),
              tla.issue(3, LBL, NC, body=DW)]
    ld, base, fk, _cfg = tla.env(tmp_path, issues)
    return ld, base, fk, ld.arm(base, LBL, apply=apply, run=fk)


@pytest.mark.parametrize("apply", [False, True])
def test_label_drain_arm_never_arms_an_awaiting_member_or_a_pulled_blocker_that_awaits(tmp_path, apply):
    ld, base, fk, res = _drain(tmp_path, apply)
    assert sorted((r["issue"], r["verdict"]) for r in res["refused"]) == [(2, "awaiting-owner"), (3, "awaiting-owner")]
    assert res["actions"] == [] or all(a["issue"] not in ("2", "3") for a in res["actions"])
    for n in (2, 3):
        assert NC in fk._names(n) and "sdlc:goal" not in fk._names(n)
    assert not [w for w in fk.writes() if any("issues/%d" % n in " ".join(w) for n in (2, 3))]


def test_label_drain_answers_arm_only_what_the_person_promoted(tmp_path):
    ld, base, fk, _res = _drain(tmp_path, True)
    qs = {q["id"]: q for q in ld.plan(base, LBL, run=fk)["questions"] if q["kind"] == "owner_gate"}
    assert sorted(qs) == ["owner_gate:2", "owner_gate:3"] and qs["owner_gate:2"]["options"] == OPTIONS
    out = tla.ask(ld, base, fk, {"owner_gate:2": "close it", "owner_gate:3": "leave it out"})
    assert not out["errors"] and NC in fk._names(2) and NC in fk._names(3) and "sdlc:goal" not in fk._names(3)
    out = tla.ask(ld, base, fk, {"owner_gate:3": "promote it"})
    assert not out["errors"]
    assert "sdlc:goal" in fk._names(3) and NC not in fk._names(3)
    assert NC in fk._names(2) and "sdlc:goal" not in fk._names(2)


@pytest.mark.parametrize("flags", [[], ["--apply"]])
def test_the_documented_label_drain_gesture_leaves_them_alone(tmp_path, flags, capsys):
    """The gesture the docs give: `label_drain.py arm <sdlc_dir> <label> [--apply]`, nothing stronger."""
    ld, base, fk, _res = _drain(tmp_path, False)
    fk.calls.clear()
    assert ld.main(["label_drain.py", "arm", base, LBL] + flags, run=fk) == 0
    out = capsys.readouterr().out
    assert "refused #2 (awaiting-owner)" in out and "refused #3 (awaiting-owner)" in out
    assert NC in fk._names(2) and NC in fk._names(3) and "sdlc:goal" not in fk._names(3)


@pytest.mark.parametrize("flags", [[], ["--dry-run"]])
def test_the_documented_chain_gesture_leaves_them_alone(tmp_path, flags, capsys):
    """`chain.py arm <sdlc_dir> <chain> [--dry-run]` after `answer --defaults` and `approve`."""
    base, fk = tca._scene(tmp_path, issues=_backlog())
    ch = tca._chain()
    assert ch.main(["chain.py", "approve", base, "1"], run=fk) == 0
    fk.calls.clear()
    assert ch.main(["chain.py", "arm", base, "1"] + flags, run=fk) == 0
    out = capsys.readouterr().out
    assert "refused #2 (awaiting-owner)" in out and "refused #5 (awaiting-owner)" in out
    assert [n for n in GATED if _touched(fk, n)] == [] and NC in fk._names(5)


def test_the_bank_entry_obeys_the_wording_rules():
    ask = next(s for s in _mod("unpark").QUESTIONS["label_set"] if s["id"] == "owner_gate")["ask"]
    assert ask.endswith("?") and ask.count("?") == 1 and ask.count(". ") == 0
    assert not any(w in ask.lower() for w in ("park", "sigma", "slot", "sdlc:", "lifecycle"))

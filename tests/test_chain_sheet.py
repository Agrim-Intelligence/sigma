"""#1585: `chain.py sheet` and `chain.py answer` -- every decision a chain needs, in front of the person
together, before anything is changed.

The sheet reads only the local chain record. `answer` writes only that record: a parked member is asked ONE question
(release it, or keep it parked) whose answer `arm` enacts (a stateful fake `gh`; no network). Controls (each clause
alone, then restored): give the cap a default; let approval ignore one question kind; drop the unanswered check;
fold the park questions into a group."""
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resource_mode  # noqa: E402,F401  (a test building its own subprocess env carries the mode from here)
import test_label_answer as tla  # noqa: E402
from test_label_drain_arm import DW, _mod, _repo  # noqa: E402

NOW = 1_800_000_000.0
KIND_DEP = "Parked by Sigma: waiting on the other issue.\nsigma-qkind: dependency"


def _chain():
    return _mod("chain")


def _unpark():
    return _mod("unpark")


def _backlog(answered=False):
    """Fixture backlog: goal 1 waits on parked 2 (dependency), unarmed 3 and closed 4."""
    body2 = DW
    if answered:
        body2 = DW + "\n" + _unpark().render_block({"route": "wait for it"})
    return [tla.issue(1, "sdlc:goal", body="Blocked by #2\nBlocked by #3\nBlocked by #4\n\n" + DW),
            tla.issue(2, "sdlc:goal", "sdlc:parked", body=body2, comments=[KIND_DEP]),
            tla.issue(3, body=DW),
            tla.issue(4, "sdlc:goal", state="closed")]


def _scene(tmp_path, answered=False, issues=None):
    base = _repo(tmp_path, "")
    cp = pathlib.Path(base) / "config.json"
    cfg = json.loads(cp.read_text())
    cfg["discovery"]["dag"] = {"mode": "off"}
    cp.write_text(json.dumps(cfg))
    fk = tla.Fake(issues if issues is not None else _backlog(answered))
    _chain().plan(base, 1, run=fk, now=NOW)
    return base, fk


def _rec_path(base):
    return pathlib.Path(base) / "state" / "chains" / "c1.json"


def _rec(base):
    return json.loads(_rec_path(base).read_text())


def _put(base, rec):
    _rec_path(base).write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")


def _answers(tmp_path, d):
    f = tmp_path / "answers.json"
    f.write_text(json.dumps(d))
    return str(f)


def _tree(base):
    root = pathlib.Path(base)
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


GOLDEN = """Chain sheet for #1
End goal: #1 (armed)
Open members: 3, depth 1, units: none
Source: live (map: off); planned 5m ago; plan complete
State: planned
Forecast: a run can finish 0 of 3 before it stops on decision: #2 needs decision (parked), #3 needs decision (not armed)
  assumes every goal it claims merges; the cap is not modelled

Ready and waiting (1, deepest first):
  #1 after #2, #3

Owner decisions (2):
  #2 parked (kind dependency)
  #3 unarmed

Done prerequisites: #4
Runs ahead of: 0 higher-priority open goal(s)

Decisions (3 open of 3):
  1. [park:2] #2 is parked: Parked by Sigma: waiting on the other issue. Release it into this chain, or keep it parked and the chain stops before it and before #1, which waits on it?
     options: keep it parked (recommended) / release it
  2. [label_set:c1] Arm the unarmed issues in this set?
     options: arm them (recommended) / arm only the ones that can start / leave them
  3. [cap:c1] What cap applies to this chain? (a positive whole number; no estimate exists yet, so there is no default)
     options: none, answer with a positive whole number

Writes that follow approval:
  - record each answer in .sdlc/state/chains/c1.json (numbers and labels only)
  - a paid step answered anything but "go ahead" is skipped by every run cycle; the answer decides only whether a run may claim it
  - after approval, `chain.py arm` creates the label chain:1 if it is missing
  - #2: add label chain:1
  - #3: add label chain:1
  - #1: add label chain:1
  - arming, per member below: add sdlc:goal, priority P2 only if it has none, assign you only if nobody is assigned, never edit the body; one comment with issue numbers only
  - #3: arm it and add one comment
  - nothing else: an upper-tier member, one you keep parked and one held by someone you wait for are labelled, never armed; starting the members belongs to a later slice

Nothing has been changed yet.
"""


def test_sheet_golden(tmp_path):
    base, _fk = _scene(tmp_path)
    assert _chain().sheet(base, 1, now=NOW + 300) == GOLDEN


def test_a_parked_member_is_asked_whether_to_release_it_even_when_its_route_was_answered(tmp_path):
    """The old per-kind route question is gone from a chain: one question says why it is parked and asks release or
    keep, whatever an earlier round recorded on the issue (a recorded route answer is no answer to THIS question)."""
    base, _fk = _scene(tmp_path, answered=True)
    assert next(r for r in _rec(base)["members"] if r["number"] == 2)["answered"] is True
    text = _chain().sheet(base, 1, now=NOW)
    assert "[park:2]" in text and "[dependency:2]" not in text and "Decisions (3 open of 3)" in text
    res = _chain().answer(base, 1, {"park:2": "keep it parked", "label_set:c1": "leave them", "cap:c1": "5"})
    assert res["state"] == "approved" and res["open"] == []


def test_defaults_leave_cap_open(tmp_path):
    base, _fk = _scene(tmp_path)
    res = _chain().answer(base, 1, defaults=True, run=_fk)
    rec = _rec(base)
    assert res["open"] == ["cap:c1"] and "cap:c1" not in rec["answers"]
    assert rec["state"] == "awaiting-answers"
    assert rec["answers"]["label_set:c1"] == "arm them"        # the recommended (first) option
    assert rec["answers"]["park:2"] == "keep it parked"        # nothing is released unasked


def _blocks(fk, n):
    return [c for c in fk.stored_comments(n)] + [fk.body(n)]


def test_a_parked_answer_is_recorded_and_writes_nothing_before_approval(tmp_path):
    """The answer is the record's only change: no Q&A block, no comment, no label before the chain is approved."""
    base, fk = _scene(tmp_path)
    before = (fk.body(2), fk.stored_comments(2), fk.stored_comments(1), fk.stored_comments(3))
    for choice in ("release it", "keep it parked"):
        res = _chain().answer(base, 1, {"park:2": choice}, run=fk)
        assert res["errors"] == [] and res["results"][0]["outcome"] == "recorded"
        assert _rec(base)["answers"]["park:2"] == choice
    assert fk.writes() == [] and (fk.body(2), fk.stored_comments(2), fk.stored_comments(1), fk.stored_comments(3)) == before


def _full_record(base):
    """Add the three kinds a small live fixture cannot produce on its own: a cross-unit edge, a spend step,
    and an end goal that cannot be armed."""
    rec = _rec(base)
    rec["cross_unit"] = [{"from": 1, "to": 3, "units": ["alpha", "beta"]}]
    next(r for r in rec["members"] if r["number"] == 3)["spend"] = True
    next(r for r in rec["members"] if r["number"] == 1)["verdict"] = "upper-tier"
    _put(base, rec)
    return rec


KINDS = ["park", "label_set", "irreversible", "cross_unit", "upper_tier", "cap"]


@pytest.mark.parametrize("alone", KINDS)
def test_approval_requires_all(tmp_path, alone):
    base, fk = _scene(tmp_path)
    _full_record(base)
    ch = _chain()
    qs, openq = ch.open_questions(_rec(base))
    assert {q["kind"] for q in openq} == set(KINDS)
    pick = {"park": "keep it parked", "label_set": "leave them", "irreversible": "leave it",
            "cross_unit": "keep going", "upper_tier": "leave it", "cap": "7"}
    for q in qs:
        if q["kind"] == "irreversible":
            pick["irreversible"] = q["options"][-1]
    rest = {q["id"]: pick[q["kind"]] for q in openq if q["kind"] != alone}
    res = ch.answer(base, 1, rest, run=fk)
    assert res["errors"] == [], res
    assert res["state"] == "awaiting-answers" and [i.split(":")[0] for i in res["open"]] == [alone]
    assert _rec(base)["state"] == "awaiting-answers"
    last = next(q["id"] for q in openq if q["kind"] == alone)
    res = ch.answer(base, 1, {last: pick[alone]}, run=fk)
    assert res["state"] == "approved" and res["open"] == [] and _rec(base)["state"] == "approved"


def test_approval_refuses_an_incomplete_plan_and_bad_answers(tmp_path):
    base, fk = _scene(tmp_path)
    ch = _chain()
    before = _tree(base)
    for bad in ({"cap:c1": "lots"}, {"cap:c1": "0"}, {"park:2": "maybe"}, {"nope:1": "x"}):
        assert ch.answer(base, 1, bad, run=fk)["errors"]
        assert _tree(base) == before and fk.stored_comments(2) == [KIND_DEP]
    rec = _rec(base)
    rec["complete"] = False
    _put(base, rec)
    res = ch.answer(base, 1, {"label_set:c1": "leave them", "cap:c1": "5", "park:2": "release it"}, run=fk)
    assert res["open"] == [] and res["state"] == "awaiting-answers"
    assert ch.main(["chain.py", "answer", base, "1", "--defaults", "--partial"]) == 2


def _many(base, n, kind="closed_gate"):
    """n closed prerequisites that each hold a dependent back (one `closed_gate` question each)."""
    rec = _rec(base)
    rec["members"] = [{"number": 100 + i, "verdict": "armed", "kind": None, "labels": [], "waits_on": [],
                       "depth": 0, "prio": None} for i in range(n)]
    rec["cross_unit"], rec["done"] = [], []
    rec["closed_gates"] = [{"prerequisite": 500 + i, "followups": [900 + i], "question": False,
                            "dependents": [100 + i]} for i in range(n)]
    _put(base, rec)


def test_grouping(tmp_path):
    base, fk = _scene(tmp_path)
    ch = _chain()
    _many(base, 24)                                   # 24 + the cap = 25: not over the line
    text = ch.sheet(base, 1, now=NOW)
    assert "grouped" not in text and "[closed_gate:500]" in text
    _many(base, 25)                                   # 25 + the cap = 26
    text = ch.sheet(base, 1, now=NOW)
    assert "grouped by kind" in text and "[group:closed_gate]" in text
    assert "[closed_gate:500]" not in text and "applies to 25 questions" in text
    assert "keep waiting (recommended)" in text
    res = ch.answer(base, 1, {"group:closed_gate": "proceed", "cap:c1": "30"}, run=fk)
    assert res["errors"] == [] and res["state"] == "approved"
    answers = _rec(base)["answers"]
    assert len([k for k in answers if k.startswith("closed_gate:")]) == 25
    assert fk.writes() == []                          # an answer is a local record
    assert ch.answer(base, 1, {"group:upper_tier": "x"}, run=fk)["errors"]


def test_a_parked_member_is_never_folded_into_a_group(tmp_path):
    """Each park question carries its own reason: past the grouping line it still prints one question per member."""
    base, fk = _scene(tmp_path)
    rec = _rec(base)
    rec["members"] = [{"number": 100 + i, "verdict": "parked", "kind": "dependency", "labels": [], "waits_on": [],
                       "depth": 0, "prio": None, "park_why": "reason %d." % i} for i in range(30)]
    rec["cross_unit"], rec["done"] = [], []
    _put(base, rec)
    text = _chain().sheet(base, 1, now=NOW)
    assert "grouped" not in text
    assert all("[park:%d]" % (100 + i) in text and "reason %d." % i in text for i in range(30))
    assert "applies to 30 questions" not in text and "share these options" not in text


def test_sheet_read_only(tmp_path, monkeypatch, capsys):
    base, fk = _scene(tmp_path)
    ch = _chain()
    before, calls = _tree(base), len(fk.calls)

    def boom(*a, **k):
        raise AssertionError("the sheet ran a subprocess")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    assert ch.main(["chain.py", "sheet", base, "c1"], now=NOW, run=boom) == 0
    assert "Nothing has been changed yet." in capsys.readouterr().out
    assert _tree(base) == before and len(fk.calls) == calls and fk.writes() == []
    assert ch.main(["chain.py", "sheet", base, "99"], now=NOW) == 2          # no record: refused, still no write


def test_no_free_text_is_posted(tmp_path):
    base, fk = _scene(tmp_path)
    secret = "pay the invoice at the usual place"
    assert _chain().answer(base, 1, {"park:2": secret}, run=fk)["errors"]
    res = _chain().answer(base, 1, {"park:2": "keep it parked"}, run=fk)
    assert res["errors"] == []
    posted = "\n".join(fk.stored_comments(2)[1:] + [fk.body(2)])
    assert secret not in posted and "issue 2" not in posted       # only the bank option, never a title or free text
    assert fk.writes() == []

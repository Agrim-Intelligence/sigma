"""Chain unblock: what a run does on a tangled backlog must equal what the forecast says, and a stop names what holds
the chain. Pure tests over `chain_forecast` (no network, no fake gh); the end-to-end scenario is in
`tests/e2e/test_chain_regression.py`.

Controls (each clause alone, then restored): the blocked-label overlay applied to a row that has recorded waits; the
roots filter of the stop line removed; the roots filter reduced to "every row"; the record's waits left out of the stop."""
import importlib.util
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_chain_run as tcr  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = ROOT / "skills" / "sigma-loop" / "scripts"


def _cf():
    spec = importlib.util.spec_from_file_location("chain_forecast", S / "chain_forecast.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rec(*rows):
    return {"members": [dict({"verdict": "armed", "waits_on": []}, **r) for r in rows]}


def test_a_blocked_label_that_the_chains_own_waits_explain_clears_as_they_close():
    """The blocker sweep puts the blocked label on a goal that waits on an open goal and takes it off when that goal
    closes, inside the next run cycle. A forecast that read the label as permanent promised 1 of 3 where a run does 3."""
    rec = _rec({"number": 1}, {"number": 2, "blocked": True, "waits_on": [1]},
               {"number": 3, "blocked": True, "waits_on": [2]})
    fc = _cf().forecast(rec)
    assert fc["finish"] == [1, 2, 3] and fc["reason"] is None, fc
    assert {m["number"]: m["marker"] for m in fc["members"]} == {1: "ready now", 2: "after", 3: "after"}


def test_a_blocked_label_nothing_in_the_chain_explains_stays_a_stop():
    rec = _rec({"number": 1, "blocked": True})
    fc = _cf().forecast(rec)
    assert fc["finish"] == [] and fc["members"][0]["marker"] == "not workable"
    assert fc["members"][0]["why"] == "carries the blocked label"


def test_a_blocked_member_behind_an_unmovable_one_is_not_promised():
    rec = _rec({"number": 1, "verdict": "assigned-elsewhere"}, {"number": 2, "blocked": True, "waits_on": [1]})
    fc = _cf().forecast(rec)
    assert fc["finish"] == [] and [s["number"] for s in fc["stops"]] == [1]


def _p(*rows):
    return {"members": [dict({"verdict": "armed", "waits_on": []}, **r) for r in rows], "outside": []}


def test_the_stop_names_what_holds_the_chain_not_the_first_parked_member():
    """A parked goal that itself waits on an open goal is held, not holding: the root is the member nothing in the
    chain stands behind. Naming the first parked member sent a person to answer a goal that could not move anyway."""
    p = _p({"number": 1, "verdict": "parked", "waits_on": [3]}, {"number": 2, "verdict": "parked", "waits_on": [1]},
           {"number": 3, "verdict": "armed", "in_progress": True})
    reason, detail = _cf().stuck_reason(p)
    assert (reason, detail.split()[0]) == ("claimed", "#3"), (reason, detail)


def test_a_member_with_nothing_behind_it_is_still_named_when_it_is_parked():
    p = _p({"number": 1, "verdict": "parked", "waits_on": [3]}, {"number": 3, "verdict": "parked"})
    assert _cf().stuck_reason(p) == ("decision", "#3 is parked and needs an answer")


def test_a_cycle_of_waits_falls_back_to_every_member():
    p = _p({"number": 1, "verdict": "parked", "waits_on": [2]}, {"number": 2, "verdict": "parked", "waits_on": [1]})
    assert _cf().stuck_reason(p)[0] == "decision"


def test_the_stop_line_of_a_forecast_and_of_a_run_agree_on_the_roots():
    rec = _rec({"number": 1, "verdict": "parked", "waits_on": [3]}, {"number": 3, "in_progress": True})
    fc = _cf().forecast(rec)
    assert fc["reason"] == "claimed" and fc["detail"].startswith("#3 "), fc


def _issue_gets(fk):
    return [tuple(c) for c in fk.calls if c[0] == "api" and re.fullmatch(r"repos/[^/]+/[^/]+/issues/\d+(/comments)?", c[1])]


def test_a_run_cycle_reads_each_issue_once(tmp_path):
    """One cycle used to read the same issue again for the closure, the closed check, the drain and the growth check.
    The saving is the count of repeated reads; the answer is the same (the pick, the record and the writes are unchanged)."""
    ch, base, fk = tcr._armed(tmp_path)
    res = tcr._run(ch, base, fk, tcr.FakePick(fk, base))
    assert res["state"] == "running" and res["picked"] == [3], res
    gets = _issue_gets(fk)
    assert gets and len(gets) == len(set(gets)), [g for g in gets if gets.count(g) > 1]
    assert fk.writes() == []


def test_a_write_inside_a_cycle_is_not_hidden_by_the_cache(tmp_path):
    ch = tcr.tca._chain()
    reads = ch.chain_reads.CycleReads(lambda a: "x")
    reads(["api", "repos/o/r/issues/3", "--method", "GET"])
    reads(["api", "repos/o/r/issues/3/labels", "--method", "POST", "-f", "labels[]=a"])
    assert reads.store == {}


def test_a_run_that_is_stuck_names_the_member_that_holds_the_chain(tmp_path):
    """Goal 1 waits on 2; 2 is parked and waits on 3; 3 belongs to someone else. The first parked member is 2, but
    2 is held by 3: the stop names 3, the member nothing in the chain is ahead of."""
    from test_chain_forecast import PARK, DW, tla
    def goal(n, who, waits=(), *labels, **kw):
        body = "".join("Blocked by #%d\n" % w for w in waits) + "\n" + DW
        return tla.issue(n, "sdlc:goal", *labels, assignees=(who,), body=body, **kw)
    issues = [goal(1, "alice", (2,)), goal(2, "alice", (3,), "sdlc:parked", comments=[PARK]), goal(3, "bob")]
    ch, base, fk = tcr._armed(tmp_path, issues)
    res = tcr._run(ch, base, fk, lambda label, skip, **kw: [])
    assert res["state"] == "stuck", res
    assert res["detail"].startswith("#3 "), res


def test_a_member_that_lists_itself_among_its_waits_is_still_a_root():
    p = _p({"number": 1, "verdict": "parked", "waits_on": [1]}, {"number": 2, "verdict": "parked"})
    assert _cf().stuck_reason(p) == ("decision", "#1 is parked and needs an answer")


def test_the_sweep_before_the_cycle_shares_the_cycles_reads_and_its_writes_drop_them(tmp_path):
    """The sweep is in-process and runs first: its own repeated reads of one issue are served once, and a write it
    makes (an unpark) drops what was kept, so the cycle's later reads see the new state."""
    ch, base, fk = tcr._armed(tmp_path)
    seen = []

    def sweep(_sdlc, _cfg, run):
        ep = "repos/acme/widget/issues/3"
        a = run(["api", ep, "--method", "GET"])
        b = run(["api", ep, "--method", "GET"])
        seen.append(a == b)
        run(["api", ep + "/labels", "--method", "POST", "-f", "labels[]=sdlc:note"])
        run(["api", ep, "--method", "GET"])
        seen.append(len([c for c in fk.calls if c[0] == "api" and c[1] == ep]))
        return ()

    tcr._run(ch, base, fk, tcr.FakePick(fk, base), sweep=sweep)
    assert seen == [True, 2], "two reads before the write are one call; the read after the write is a second"


def _gated_rec(answer=None):
    rec = _rec({"number": 1}, {"number": 2, "waits_on": [1]}, {"number": 3})
    rec["goal"], rec["assignee_filter"] = 2, False
    rec["closed_gates"] = [{"prerequisite": 9, "followups": [8], "question": False, "dependents": [3]}]
    rec["answers"] = {} if answer is None else {"closed_gate:9": answer}
    return rec


def test_the_forecast_before_a_closed_prerequisite_question_is_answered_reads_the_recommended_answer():
    """Approval needs every question answered, so a run never starts with one open; the sheet's forecast, printed
    before the answer, promised the dependent of a closed prerequisite's open follow-up (3 of 3) and the run, after
    the recommended answer 'keep waiting', finished 2. The forecast reads the recommended answer and says so."""
    ch = tcr.tca._chain()
    rec = _gated_rec()
    fc = ch.chain_forecast.forecast(rec, None, None, ch._held(rec, assume=True), (), ch._held(rec, assume=True))
    assert fc["finish"] == [1, 2] and any("keep waiting" in n and "#3" in n for n in fc["notes"]), fc
    assert ch._held(rec) == set(), "the run itself holds nothing nobody answered"


def test_an_answer_of_proceed_is_honoured_in_the_forecast():
    ch = tcr.tca._chain()
    rec = _gated_rec("proceed")
    fc = ch.chain_forecast.forecast(rec, None, None, ch._held(rec, assume=True), (), ())
    assert fc["finish"] == [1, 3, 2] or sorted(fc["finish"]) == [1, 2, 3], fc
    assert fc["notes"] == []


def test_the_sheet_prints_the_assumption():
    ch = tcr.tca._chain()
    rec = _gated_rec()
    rec.update(planned_at=1_800_000_000, source="live", source_reason="x", complete=True, state="planned")
    for r in rec["members"]:
        r.update(depth=0, unit=None)
    text = ch.render_sheet(rec, 1_800_000_010)
    assert "keep waiting" in text.split("Ready and waiting")[0], text[:600]

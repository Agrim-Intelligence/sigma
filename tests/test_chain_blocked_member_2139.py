"""#2139 B: the chain plan answers, read-only, whether the sweep would lift a blocked member, and asks when it would not.

A member that carries the blocked label with no open wait the chain explains (no `waits_on`, no outside prerequisite,
not in progress) is checked at plan time by the sweep's own function (`auto_unpark.compute_unpark_actions`, read-only,
GET calls only, no state file written), at most `chain.MAX_SWEEP_CHECKS` (20) members per plan. Yes: the forecast
treats it as workable and nothing is asked. No: the sheet asks one `blocked:<n>` question (`keep it blocked`,
recommended, or `release it`, enacted at `arm` through the unpark gesture); a kept member stops the run before it,
and the forecast says the same. A record written before this change (no `sweep_*` fields) asks nothing new and still
stops on `decision`, never `claimed`. The docs and the CHANGELOG describe the change.

New record fields and question ids are read by string, never imported, so every test fails by assertion before the
change. A stateful fake `gh` (`test_chain_asked_up_front.Fk`); no network.
"""
import json
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resource_mode  # noqa: E402,F401
import test_chain_arm as tca  # noqa: E402
import test_chain_asked_up_front as tcau  # noqa: E402
import test_chain_plan as tcp  # noqa: E402
import test_chain_run as tcr  # noqa: E402
import test_chain_run_freed_2139 as tfr  # noqa: E402
import test_label_answer as tla  # noqa: E402
from changelog_layout import entries_naming  # noqa: E402
from test_label_drain_arm import DW  # noqa: E402

NOW = tca.NOW
ROOT = pathlib.Path(__file__).resolve().parents[1]
KEEP, RELEASE = "keep it blocked", "release it"
NEW_FIELDS = ("sweep_lifts", "sweep_why", "sweep_open", "sweep_not_checked")


def _blocked(n, *waits, **kw):
    """A goal that carries the blocked label (and `Blocked by` markers for `waits`)."""
    return tcau.goal(n, *waits, labels=("sdlc:blocked",) + tuple(kw.pop("labels", ())), **kw)


def _row(base, n):
    return next(r for r in tca._rec(base)["members"] if r["number"] == n)


def _replan(ch, base, fk, run=None, **auto_unpark):
    """Plan again (calls recorded from here): `auto_unpark` sets `discovery.auto_unpark` first when given."""
    if auto_unpark:
        tfr._set_auto_unpark(base, **auto_unpark)
    fk.calls.clear()
    ch.plan(base, 1, run=run or fk, now=NOW + 1)
    return tca._rec(base)


def _issue_reads(calls, n):
    return [tuple(c) for c in calls
            if c and c[0] == "api" and re.fullmatch(r"repos/[^/]+/[^/]+/issues/%d(/comments)?" % n, c[1])]


# --- 1 -- a member the sweep would lift is workable and not asked ----------------------------------------------------

@pytest.mark.parametrize("weak_comment", [False, True], ids=["explicit-only", "plus-a-weak-mention"])
def test_a_blocked_member_the_sweep_would_lift_is_workable_and_not_asked(tmp_path, weak_comment, monkeypatch):
    """Member 2 carries the label and a `Blocked by` marker for 3, which is closed; (b) a trusted comment also names
    open, unrelated 9 with a weak word. The plan says the sweep lifts it, asks nothing, the forecast marks it ready,
    and a run (the real sweep at interval 0, a pick that refuses what the real pick refuses) claims it within two
    cycles, as the forecast's first finish."""
    monkeypatch.delenv("SIGMA_RUN_ID", raising=False)
    comments = ["FYI this needs #%d eventually, not now" % 9] if weak_comment else []
    issues = [tcau.goal(1, 2), _blocked(2, 3, comments=comments), tfr.closed(tcau.goal(3)), tla.issue(9, body=DW)]
    ch, base, fk = tcau.scene(tmp_path, issues)
    row = _row(base, 2)
    assert row.get("sweep_lifts") is True, row
    assert "blocked:2" not in tcau.qids(ch, base)
    fc = ch.chain_forecast.forecast(tca._rec(base))
    assert {m["number"]: m["marker"] for m in fc["members"]}[2] == "ready now", fc["members"]
    assert fc["finish"][:1] == [2], fc
    tcau.answer_all(ch, base, fk)
    tfr._set_auto_unpark(base, mode="on", interval_minutes=0)
    ch.arm(base, 1, run=fk, now=NOW)
    pick, got = tfr.Pick(fk, base), []
    for i in range(2):
        res = ch.run(base, 1, pick=pick, now=NOW + 10 + i, run=fk)
        got += res["picked"]
        if got:
            break
    assert got == [2], (got, res)


# --- 2 -- a member it would not lift is asked up front ---------------------------------------------------------------

def test_a_hand_labelled_member_is_asked_up_front_keep_recommended_and_never_grouped(tmp_path, monkeypatch, capsys):
    ch, base, fk = tcau.scene(tmp_path / "one", [tcau.goal(1, 2), _blocked(2)])
    row = _row(base, 2)
    assert row.get("sweep_lifts") is False and row.get("sweep_why") == "no-ref", row
    qs = {q["id"]: q for q in ch.questions_of(tca._rec(base), {})}
    assert "blocked:2" in qs, sorted(qs)
    q = qs["blocked:2"]
    assert q["kind"] == "blocked" and q["options"] == [KEEP, RELEASE] and q["recommended"] == KEEP, q
    assert q["ask"].startswith("#2 carries the blocked label") and "no blocker is recorded on it" in q["ask"], q["ask"]
    assert "blocked:2" in tcau.qids(ch, base)
    assert ch.chain_forecast.forecast(tca._rec(base))["detail"] == "#2 carries the blocked label and needs an answer"
    # the documented gesture prints the question by its id
    assert ch.main(["chain.py", "sheet", base, "1"], run=fk, now=NOW) == 0
    assert "[blocked:2]" in capsys.readouterr().out
    # several of them are never folded into one group, however low the grouping threshold
    ch3, base3, _fk3 = tcau.scene(tmp_path / "three", [tcau.goal(1, 2, 3, 4), _blocked(2), _blocked(3), _blocked(4)])
    monkeypatch.setattr(ch3, "GROUP_OVER", 1)
    text = ch3.sheet(base3, 1, now=NOW)
    assert all("[blocked:%d]" % n in text for n in (2, 3, 4)) and "grouped" not in text, text


# --- 3 -- the check is read-only ------------------------------------------------------------------------------------

def test_the_check_is_read_only(tmp_path):
    ch, base, fk = tcau.scene(tmp_path, [tcau.goal(1, 2), _blocked(2, 3, comments=["Looking at it."]),
                                         tfr.closed(tcau.goal(3))])
    rec = _replan(ch, base, fk)
    assert "sweep_lifts" in _row(base, 2), "the check did not run"
    assert fk.writes() == [], fk.writes()
    assert fk.calls and all(c[0] == "api" and "--method" in c and c[c.index("--method") + 1] == "GET"
                            for c in fk.calls), [c for c in fk.calls if "GET" not in c]
    assert rec["calls"]["total"] == len(fk.calls), (rec["calls"], len(fk.calls))
    state = pathlib.Path(base) / "state"
    assert not (state / "auto-unpark-cache.json").exists() and not (state / "auto-unpark-last.json").exists()


# --- 4 -- release it is enacted at arm ------------------------------------------------------------------------------

def test_release_it_is_enacted_at_arm_through_the_unpark_gesture(tmp_path):
    ch, base, fk = tcau.scene(tmp_path, [tcau.goal(1, 2), _blocked(2)])
    tcau.answer_all(ch, base, fk, **{"blocked:2": RELEASE})
    assert fk.writes() == [], "an answer or an approval wrote to GitHub"
    fc = ch.chain_forecast.forecast(tca._rec(base))          # released at arm, so the forecast reads it as workable
    assert {m["number"]: m["marker"] for m in fc["members"]}[2] == "ready now", fc["members"]
    res = ch.arm(base, 1, run=fk, now=NOW)
    assert not any(a["result"] == "failed" for a in res["actions"]), res
    names = fk._names(2)
    assert "sdlc:goal" in names and "chain:1" in names and "sdlc:blocked" not in names, names
    assert "<!-- sigma:unpark-qa:start -->" in fk.body(2)
    assert [a["action"] for a in res["actions"]][:1] == ["unpark"], res["actions"]


# --- 5 -- keep it blocked stops the run before it ----------------------------------------------------------------------

def test_keep_it_blocked_stops_the_run_before_it_and_the_forecast_says_the_same(tmp_path):
    ch, base, fk = tcau.scene(tmp_path, [tcau.goal(1, 2), _blocked(2)])
    assert "blocked:2" in tcau.qids(ch, base)
    tcau.answer_all(ch, base, fk, **{"blocked:2": KEEP})
    res = ch.arm(base, 1, run=fk, now=NOW)
    assert not [a for a in res["actions"] if a["action"] == "unpark"], res["actions"]
    assert "sdlc:blocked" in fk._names(2)
    assert not [a for a in res["actions"] if a["issue"] == "2" and "-sdlc:blocked" in str(a.get("detail"))]
    fc = ch.chain_forecast.forecast(tca._rec(base), {}, "alice")
    skips = []

    def pick(label, skip, **kw):
        skips.append(set(skip))
        return []
    res = ch.run(base, 1, pick=pick, now=NOW + 5, run=fk, sweep=tcr._sweep)
    assert res["state"] == "stuck" and res["reason"] == "decision", res
    d = res["detail"]
    assert d.startswith("#2 ") and "you answered to keep it blocked" in d, d
    assert "answer `release it` and run `arm` again" in d and "/sigma-unpark #2" in d, d
    assert (fc["reason"], fc["detail"]) == (res["reason"], res["detail"])
    assert skips and "2" in skips[0], skips


def _rec5b(members, answers):
    return {"goal": 1, "runner": "alice", "assignee_filter": True, "answers": answers,
            "members": [dict({"verdict": "armed", "waits_on": [], "assignees": ["alice"]}, **m) for m in members]}


def test_a_kept_blocked_member_somebody_else_holds_stays_kept_in_the_forecast():
    """Pure record. A kept member that is somebody else's reads `needs decision` (the kept verdict, not the assignee
    clause re-applied from `base_verdict`) and the stop is the kept one. A blocked member somebody else holds that
    nobody answered about is `claimed`: the label-held branch reads only armed members."""
    cf = tca._chain().chain_forecast
    held = {"number": 2, "verdict": "assigned-elsewhere", "base_verdict": "armed", "blocked": True,
            "sweep_lifts": False, "assignees": ["bob"]}
    fc = cf.forecast(_rec5b([{"number": 1, "waits_on": [2]}, held],
                            {"blocked:2": KEEP, "elsewhere:2": "wait for them"}), {}, "alice")
    assert {m["number"]: m["marker"] for m in fc["members"]}[2] == "needs decision", fc["members"]
    assert fc["reason"] == "decision" and fc["detail"].startswith("#2 ") \
        and "you answered to keep it blocked" in fc["detail"], fc
    other = cf.forecast(_rec5b([{"number": 1, "waits_on": [3]}, dict(held, number=3)], {}), {}, "alice")
    assert other["reason"] == "claimed" and other["detail"].startswith("#3 "), other


# --- 6 -- an old record without the new fields ------------------------------------------------------------------------

def _strip(base):
    rec = tca._rec(base)
    for k in NEW_FIELDS:
        rec.pop(k, None)
    for r in rec["members"]:
        for k in NEW_FIELDS:
            r.pop(k, None)
    tca._put(base, rec)
    return rec


def test_an_old_record_without_the_new_fields_asks_nothing_and_stops_on_decision(tmp_path):
    ch, base, fk = tcau.scene(tmp_path / "planned", [tcau.goal(1, 2), _blocked(2)])
    _strip(base)
    assert not [i for i in tcau.qids(ch, base) if i.startswith("blocked:")]
    tcau.answer_all(ch, base, fk)
    res = ch.arm(base, 1, run=fk, now=NOW)
    assert not any(a["result"] == "failed" for a in res["actions"]), res
    fc = ch.chain_forecast.forecast(tca._rec(base), {}, "alice")
    res = ch.run(base, 1, pick=lambda label, skip, **kw: [], now=NOW + 5, run=fk, sweep=tcr._sweep)
    assert res["state"] == "stuck" and res["reason"] == "decision", res
    assert res["detail"] == ("#2 carries the blocked label and nothing in this chain lifts it: release it with "
                             "/sigma-unpark #2"), res["detail"]
    assert (fc["reason"], fc["detail"]) == (res["reason"], res["detail"])
    # an approved and armed record loses the fields: it stays approved and `arm` re-runs clean
    ch2, base2, fk2 = tcau.scene(tmp_path / "armed", [tcau.goal(1, 2), _blocked(2)])
    tcau.answer_all(ch2, base2, fk2)
    ch2.arm(base2, 1, run=fk2, now=NOW)
    rec = _strip(base2)
    assert rec["state"] == "approved" and ch2.open_questions(rec, {})[1] == []
    # the answer it kept (`keep it blocked`, the default) is to a question the record no longer asks: not read
    assert (rec["answers"].get("blocked:2") == KEEP
            and ch2.chain_forecast.forecast(rec, {}, "alice")["detail"] == res["detail"]), rec.get("answers")
    again = ch2.arm(base2, 1, run=fk2, now=NOW + 1)
    assert not any(a["result"] == "failed" for a in again["actions"]), again
    assert tca._rec(base2)["state"] == "approved" and tca._rec(base2).get("armed_at")


# --- 7 -- the sweep switched off, a failed read --------------------------------------------------------------------

def test_the_sweep_switched_off_means_the_member_is_asked(tmp_path):
    ch, base, fk = tcau.scene(tmp_path / "blocked", [tcau.goal(1, 2), _blocked(2)])
    rec = _replan(ch, base, fk, mode="off")
    row = _row(base, 2)
    assert row.get("sweep_lifts") is False and row.get("sweep_why") == "sweep-off", row
    assert "blocked:2" in tcau.qids(ch, base)
    ch0, base0, fk0 = tcau.scene(tmp_path / "plain", [tcau.goal(1, 2), tcau.goal(2)])
    plain = _replan(ch0, base0, fk0, mode="off")
    assert rec["calls"]["total"] == plain["calls"]["total"], (rec["calls"], plain["calls"])   # zero added calls


@pytest.mark.parametrize("how", ["raises", "malformed", "ref-raises"])
def test_a_failed_read_is_asked_never_a_yes(tmp_path, how):
    """Member 2's body names a closed explicit blocker, so an empty comment read would read as "lifts". A comments
    GET that raises, or answers a malformed page (`gh_api` raises inside, `fetch_comments` would read it as no
    comments), is `unread`: asked, never a yes. (ref-raises) Member 2 names only closed 9, weakly, in a comment, and
    the read of 9 raises: the sweep's helper reads it as open, and the check says `unread`, not `open`."""
    if how == "ref-raises":
        issues = [tcau.goal(1, 2), _blocked(2, comments=["FYI this needs #%d eventually, not now" % 9]),
                  tfr.closed(tla.issue(9, body=DW))]
        failing = r"repos/[^/]+/[^/]+/issues/9"
    else:
        issues = [tcau.goal(1, 2), _blocked(2, 3, comments=["Looking at it."]), tfr.closed(tcau.goal(3))]
        failing = r"repos/[^/]+/[^/]+/issues/2/comments"
    ch, base, fk = tcau.scene(tmp_path, issues)

    def broken(args):
        a = [str(x) for x in args]
        if a[0] == "api" and re.fullmatch(failing, a[1]):
            fk.calls.append(a)
            if how != "malformed":
                raise RuntimeError("HTTP 502 Bad Gateway")
            return "{}"
        return fk(args)
    _replan(ch, base, fk, run=broken)
    row = _row(base, 2)
    assert row.get("sweep_lifts") is False and row.get("sweep_why") == "unread", row
    assert "blocked:2" in tcau.qids(ch, base)


# --- 8 -- a map plan, the bound, the members not checked --------------------------------------------------------------

def test_a_map_plan_reads_a_blocked_member_once_and_answers(tmp_path):
    """A map node whose life is `blocked`: the plan stays on the map, the member is checked, and the check reads its
    issue at most twice (the check's own read and the comment read's issue GET) and its comments once."""
    def plan(sub, labels):
        base = tcp._repo(tmp_path / sub)
        tcp._save_map(base, [tcp._mnode(1), tcp._mnode(2, life="blocked" if labels else "goal")], [(2, 1)])
        issues = [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Blocked by #%d\n\n" % 2 + DW),
                  tla.issue(2, "sdlc:goal", *labels, assignees=("alice",), body=DW, comments=["Looking at it."])]
        calls = []
        p = tca._chain().plan(base, 1, run=tcp._fake([dict(i) for i in issues], calls), now=NOW)
        return p, calls
    p, calls = plan("blocked", ("sdlc:blocked",))
    p0, calls0 = plan("plain", ())
    assert p["source"] == "map", (p["source"], p.get("source_reason"))
    row = next(r for r in p["members"] if r["number"] == 2)
    assert row.get("blocked") is True and row.get("sweep_lifts") is False and row.get("sweep_why") == "no-ref", row
    added = list(calls)
    for c in calls0:
        if c in added:
            added.remove(c)
    issue2 = [c for c in added if re.fullmatch(r"repos/[^/]+/[^/]+/issues/2", c[1])]
    pages = [c for c in added if re.fullmatch(r"repos/[^/]+/[^/]+/issues/2/comments", c[1])]
    assert 1 <= len(issue2) <= 2 and len(pages) <= 1, added
    assert all(c[c.index("--method") + 1] == "GET" for c in added), added


def test_at_most_twenty_members_are_checked_and_the_rest_are_listed(tmp_path):
    nums = list(range(2, 24))                               # 22 hand-labelled members
    ch, base, fk = tcau.scene(tmp_path / "blocked", [tcau.goal(1, *nums)] + [_blocked(n) for n in nums])
    rec = _replan(ch, base, fk)
    ch0, base0, fk0 = tcau.scene(tmp_path / "plain", [tcau.goal(1, *nums)] + [tcau.goal(n) for n in nums])
    _replan(ch0, base0, fk0)
    rows = [r for r in rec["members"] if r["number"] in nums]
    checked = [r["number"] for r in rows if "sweep_lifts" in r]
    unchecked = sorted(r["number"] for r in rows if "sweep_lifts" not in r)
    assert len(checked) == 20 and len(unchecked) == 2, (checked, unchecked)
    assert sorted(int(x) for x in rec.get("sweep_not_checked") or ()) == unchecked, rec.get("sweep_not_checked")
    for n in unchecked:                                     # nothing the check would read was read for them
        assert len(_issue_reads(fk.calls, n)) == len(_issue_reads(fk0.calls, n)), n
    assert rec.get("complete") is True
    line = next((ln for ln in ch.sheet(base, 1, now=NOW).splitlines() if "not checked against the sweep" in ln), "")
    assert all("#%d" % n in line for n in unchecked), line


def test_members_with_waits_externals_or_in_progress_are_not_checked(tmp_path):
    """2 waits on open member 5, 3 has a prerequisite in another repository, 4 is in progress, 8 is parked (its park
    question covers the label: a release removes both): none is checked or asked about the label. 6, a hand-labelled
    member with none of those, is checked (the selection is live)."""
    issues = [tcau.goal(1, 2, 3, 4, 6, 8), _blocked(2, 5), tcau.goal(5),
              _blocked(3, body="Blocked by acme/other#%d\n" % 7),
              _blocked(4, labels=("sdlc:in-progress",)), _blocked(6),
              _blocked(8, labels=("sdlc:parked",), comments=[tcau.PARK])]
    ch, base, fk = tcau.scene(tmp_path, issues)
    rec = tca._rec(base)
    by = {r["number"]: r for r in rec["members"]}
    assert by[2]["waits_on"] == [5] and by[4]["in_progress"] is True
    assert by[8]["verdict"] == "parked" and by[8]["blocked"] is True and not by[8]["waits_on"], by[8]
    assert any(3 in (e.get("needed_by") or ()) for e in rec.get("externals") or ()), rec.get("externals")
    assert all("sweep_lifts" not in by[n] for n in (2, 3, 4, 8)), [by[n] for n in (2, 3, 4, 8)]
    ids = tcau.qids(ch, base)
    assert not [i for i in ids if i in ("blocked:2", "blocked:3", "blocked:4", "blocked:8")], ids
    assert by[6].get("sweep_lifts") is False and "blocked:6" in ids, (by[6], ids)


# --- 9 -- the docs describe the change --------------------------------------------------------------------------------

def _flat(path):
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def test_the_docs_describe_the_change():
    chain = _flat("docs/chain.md")
    for phrase in ("`blocked:<n>`", "keep it blocked", "freed a member", "/sigma-unpark #N"):
        assert phrase in chain, "docs/chain.md lacks %r" % phrase
    for doc in ("docs/label-model.md", "docs/backlog.md"):
        assert "does not hold a goal that names an explicit blocker" in _flat(doc).lower(), doc
    entries = entries_naming((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), "#2139")
    assert len(entries) == 1 and "Not measured" in entries[0], entries

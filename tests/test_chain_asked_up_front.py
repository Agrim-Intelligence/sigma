"""A chain sheet asks every question the run needs, deep members included, before anything is armed, and every
answer is enacted: none is "recorded here only".

Four things used to be left to the run, which then stopped on them with no question asked: a member that is parked,
a wait read only from prose, a member somebody else holds, and a paid step marked only by a label. Each is now ONE
question on the first sheet (`park:N`, `wait:A-B`, `elsewhere:N`, `irreversible:N`), the sheet's forecast is computed
from the answers, and `chain.py arm` enacts them (release through the unpark gesture, the dependency marker or the
dismissal, the assignee swap). Nothing is written before approval. A stateful fake `gh`; no network.

Controls (each clause alone, then restored; every one seen red): a deep parked member not asked; a parked member
asked the old kind question; the park question folded into a group; `release it` not enacted; `keep it parked`
enacted as a release; a wait on a closed issue asked; a wait the explicit edges already imply asked; `confirm it`
not written; `dismiss it` not written; a dismissed wait still held by the run; a member in progress offered
`take it over`; `take it over` leaving the old assignee; a paid label ignored; the answered cap not enforced; a
write before approval; a second `arm` writing again; a failed release leaving the chain stamped armed."""
import json
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resource_mode  # noqa: E402,F401
import test_chain_arm as tca  # noqa: E402
import test_chain_run as tcr  # noqa: E402
import test_label_answer as tla  # noqa: E402
from test_label_drain_arm import DW  # noqa: E402

NOW = tca.NOW
PARK = "Parked by Sigma: the owner has not decided where this goes.\nsigma-qkind: needs_decision"
PARK_UNDECLARED = "Parked by Sigma: changes requested on the pull request."


class Fk(tca.ChainFake):
    """The answer fake plus assignee removal (`DELETE issues/N/assignees`)."""

    def __call__(self, args):
        args = [str(a) for a in args]
        m = re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)/assignees", args[1]) if args[0] == "api" else None
        if m and "DELETE" in args:
            self.calls.append(args)
            gone = {a.split("=", 1)[1].lower() for a in args if a.startswith("assignees[]=")}
            n = int(m.group(1))
            self.by[n]["assignees"] = [a for a in self.by[n]["assignees"] if a["login"].lower() not in gone]
            return json.dumps({"assignees": self.by[n]["assignees"]})
        return super().__call__(args)

    def assignees(self, n):
        return sorted(a["login"] for a in self.by[n]["assignees"])


def goal(n, *waits, who="alice", labels=(), comments=(), body=""):
    text = "".join("Blocked by #%d\n" % w for w in waits) + body + "\n" + DW
    return tla.issue(n, "sdlc:goal", *labels, assignees=(who,) if who else (), body=text, comments=list(comments))


def parked(n, *waits, note=PARK, who="alice"):
    return goal(n, *waits, who=who, labels=("sdlc:parked",), comments=[note])


def scene(tmp_path, issues, config=None):
    """Plan goal 1 over `issues` with alice as the runner. -> (chain module, base, fake)."""
    ch = tca._chain()
    base = tca._repo(tmp_path, "alice")
    cp = pathlib.Path(base) / "config.json"
    cfg = json.loads(cp.read_text())
    cfg["discovery"]["dag"] = {"mode": "off"}
    cfg.update(config or {})
    cp.write_text(json.dumps(cfg))
    fk = Fk(issues)
    ch.plan(base, 1, run=fk, now=NOW)
    fk.calls.clear()
    fk.write_count = 0
    return ch, base, fk


def answer_all(ch, base, fk, **choice):
    """Answer every open question: `choice` maps a question id's kind ('park:2' or 'park') to an answer; the rest
    take the recommended option, the cap 50. Approves. -> the record."""
    rec = tca._rec(base)
    qs = ch.open_questions(rec, {})[1]
    ans = {}
    for q in qs:
        want = choice.get(q["id"], choice.get(q["kind"]))
        ans[q["id"]] = want if want is not None else ("50" if q["kind"] == "cap" else
                                                       q["options"][-1] if q["kind"] in ("irreversible",) else
                                                       q["recommended"] or q["options"][0])
    res = ch.answer(base, 1, ans, run=fk)
    assert res["errors"] == [], res
    assert res["state"] == "approved", res
    ch.approve(base, 1)
    return tca._rec(base)


def qids(ch, base):
    return [q["id"] for q in ch.open_questions(tca._rec(base), {})[1]]


# --- 1 -- a parked member, near or deep, is one question that says why -------------------------------------------------

def _chain_of_depth():
    """1 waits on 2 (ready), 2 on 3 (ready), 3 on 4 (parked, three levels down) and on 5 (parked, near)."""
    return [goal(1, 2), goal(2, 3), goal(3, 4, 5), parked(4), parked(5, note=PARK_UNDECLARED)]


def test_a_parked_member_is_asked_at_any_depth_with_its_reason_and_what_waits_on_it(tmp_path):
    ch, base, fk = scene(tmp_path, _chain_of_depth())
    qs = {q["id"]: q for q in ch.questions_of(tca._rec(base), {})}
    assert "park:4" in qs and "park:5" in qs
    ask = qs["park:4"]["ask"]
    assert "the owner has not decided where this goes" in ask            # why it is parked: the recorded park note
    assert "#3, #2, #1" in ask or "#1, #2, #3" in ask or all("#%d" % n in ask for n in (1, 2, 3))   # who waits behind
    assert qs["park:4"]["options"] == ["keep it parked", "release it"] and qs["park:4"]["recommended"] == "keep it parked"
    assert "park:5" in qids(ch, base)


def test_the_old_kind_question_is_gone_for_a_parked_member(tmp_path):
    """No 13-option "which kind of question is this?" and no per-kind route question: one question replaces them."""
    ch, base, fk = scene(tmp_path, _chain_of_depth())
    ids = qids(ch, base)
    assert not [i for i in ids if i.split(":")[0] in ("kind", "dependency", "needs_decision", "unknown")], ids
    assert all(len(q["options"]) <= 3 for q in ch.questions_of(tca._rec(base), {}) if q["kind"] != "cap")


def test_a_parked_member_with_no_note_says_so_and_a_keys_only_repository_shows_no_wording(tmp_path):
    asks = tca._chain().chain_asks
    assert asks.park_why({"park_reason": "", "body_head": "Move the importer to the new API."}) == \
        "Move the importer to the new API."
    assert asks.park_why({"park_reason": "", "body_head": ""}) == asks.NO_WHY
    assert asks.park_why({"park_reason": "x" * 400}).endswith("…") and len(asks.park_why({"park_reason": "x" * 400})) == 140
    assert asks.park_why({"park_reason": "the wording"}, keys_only=True) == asks.KEYS_ONLY_WHY
    secret = "token ghp_" + "a" * 36
    assert "ghp_" not in asks.park_why({"park_reason": secret})


def test_a_parked_note_is_cut_short_and_never_the_title(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), parked(2, note="Parked by Sigma: " + "long " * 80)])
    row = next(r for r in tca._rec(base)["members"] if r["number"] == 2)
    assert len(row["park_why"]) <= 140 and "issue 2" not in row["park_why"]


def test_park_questions_are_never_folded_into_a_group(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, *range(2, 32))] + [parked(n) for n in range(2, 32)])
    text = ch.sheet(base, 1, now=NOW)
    assert "grouped" not in text and all("[park:%d]" % n in text for n in range(2, 32))


# --- 2 -- the answer is enacted at arm, through the existing gestures ----------------------------------------------------

def test_release_is_enacted_at_arm_by_the_atomic_unpark_gesture(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), parked(2, note=PARK_UNDECLARED)])
    answer_all(ch, base, fk, **{"park:2": "release it"})
    assert fk.writes() == [], "an answer or an approval wrote to GitHub"
    res = ch.arm(base, 1, run=fk, now=NOW)
    assert not any(a["result"] == "failed" for a in res["actions"]), res
    names = fk._names(2)
    assert "sdlc:goal" in names and "sdlc:parked" not in names and "chain:1" in names
    block = fk.body(2)
    assert "<!-- sigma:unpark-qa:start -->" in block and "start it again" in block      # the identical Q&A block
    assert any("sigma-qkind:" in c for c in fk.stored_comments(2))                        # the kind was declared, with the guess
    assert [a["action"] for a in res["actions"]][:1] == ["unpark"]


def test_keep_it_parked_writes_no_release(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), parked(2)])
    answer_all(ch, base, fk, **{"park:2": "keep it parked"})
    res = ch.arm(base, 1, run=fk, now=NOW)
    assert "sdlc:parked" in fk._names(2) and "sdlc:goal" in fk._names(2)
    assert not [a for a in res["actions"] if a["action"] == "unpark"]
    assert fk.stored_comments(2) == [PARK] and "unpark-qa" not in fk.body(2)


def test_a_run_stops_before_a_kept_parked_member_and_says_it_was_the_answer(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), parked(2)])
    answer_all(ch, base, fk, **{"park:2": "keep it parked"})
    ch.arm(base, 1, run=fk, now=NOW)
    fc = ch.chain_forecast.forecast(tca._rec(base), tcr._cfg(ch, base) if hasattr(tcr, "_cfg") else {}, "alice")
    res = ch.run(base, 1, pick=lambda label, skip, **kw: [], now=NOW + 5, run=fk, sweep=tcr._sweep)
    assert res["reason"] == "decision" and "you answered to keep it parked" in res["detail"], res
    assert fc["detail"] == res["detail"]                                  # the sheet's forecast is the run


def test_a_failed_release_is_reported_and_the_chain_is_not_stamped_armed(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), parked(2)])
    answer_all(ch, base, fk, **{"park:2": "release it"})

    def broken(args):
        if args[:2] == ["api", "graphql"] and "mutation" in " ".join(map(str, args)):
            raise RuntimeError("HTTP 502")
        return fk(args)
    res = ch.arm(base, 1, run=broken, now=NOW)
    assert [a for a in res["actions"] if a["action"] == "unpark" and a["result"] == "failed"], res
    assert not tca._rec(base).get("armed_at")                      # `run` refuses until a re-run finishes the job
    with pytest.raises(ch.ChainRefused):
        ch.run(base, 1, pick=lambda *a, **k: [], now=NOW + 1, run=fk, sweep=tcr._sweep)
    again = ch.arm(base, 1, run=fk, now=NOW)                       # the lever: run arm again
    assert not any(a["result"] == "failed" for a in again["actions"]) and tca._rec(base).get("armed_at")


def test_a_second_arm_writes_nothing_and_a_dry_run_prints_the_sheets_lines(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2, 3), parked(2), goal(3, who="bob")])
    answer_all(ch, base, fk, **{"park:2": "release it", "elsewhere:3": "take it over"})
    sheet_lines = "\n".join(ch.chain_asks.write_lines(tca._rec(base)))
    dry = ch.arm(base, 1, dry_run=True, run=fk, now=NOW)
    assert fk.writes() == []
    shown = ch.render_arm(dry)
    assert "#2: release it through the unpark gesture" in shown and "#3: assign alice, remove bob" in shown
    assert "refused #2" not in shown and "refused #3" not in shown       # a dry run knows the first pass will change them
    assert "#2: release it through the unpark gesture" in sheet_lines
    ch.arm(base, 1, run=fk, now=NOW)
    n = len(fk.writes())
    again = ch.arm(base, 1, run=fk, now=NOW)
    assert len(fk.writes()) == n and not [a for a in again["actions"] if a["action"] in ("unpark", "take-over")]


# --- 3 -- a wait read only from prose is a question when it can change the order ---------------------------------------

def _waits_chain():
    """1 waits on 2 and 3. 2 says (in a comment) to hold on 3 before any paid run; 3 is open, 4 is closed."""
    return [goal(1, 2, 3), goal(2, comments=["hold on #3 before any paid run", "also wait for #4"]), goal(3),
            tla.issue(4, "sdlc:goal", state="closed", body=DW)]


def test_a_prose_wait_on_an_open_issue_is_a_question_and_one_on_a_closed_issue_is_not(tmp_path):
    ch, base, fk = scene(tmp_path, _waits_chain())
    ids = qids(ch, base)
    assert "wait:2-3" in ids and not [i for i in ids if i.startswith("wait:") and i != "wait:2-3"], ids
    q = next(q for q in ch.questions_of(tca._rec(base), {}) if q["id"] == "wait:2-3")
    assert "#2 waits on #3" in q["ask"] and "comment" in q["ask"] and q["options"] == ["confirm it", "dismiss it"]
    assert q["recommended"] == "confirm it"


def test_a_prose_wait_the_explicit_edges_already_imply_is_not_asked(tmp_path):
    """1 waits on 2 and 3 in its body; 2 waits on 3 explicitly; a comment on 1 says to hold on 3: no order changes."""
    issues = [goal(1, 2, comments=["hold on #3 before any paid run"]), goal(2, 3), goal(3)]
    ch, base, fk = scene(tmp_path, issues)
    assert not [i for i in qids(ch, base) if i.startswith("wait:")]


def test_the_sheet_groups_the_prose_waits_under_one_heading_with_one_key_each(tmp_path):
    issues = [goal(1, 2, 3, 5), goal(2, comments=["hold on #3 before any paid run"]), goal(3),
              goal(5, comments=["hold on #3 before any paid run"])]
    ch, base, fk = scene(tmp_path, issues)
    text = ch.sheet(base, 1, now=NOW)
    assert text.count("Unconfirmed waits (2, read from comments or loose wording)") == 1
    assert "[wait:2-3]" in text and "[wait:5-3]" in text and "group:wait" not in text


def test_prose_waits_are_not_folded_into_a_group_however_many_there_are(tmp_path):
    """Past the sheet's grouping line every other kind folds into one question; a wait never does (one key each)."""
    issues = [goal(1, *range(2, 29))] + [goal(n, comments=["hold on #29 before any paid run"]) for n in range(2, 29)] \
        + [goal(29)]
    ch, base, fk = scene(tmp_path, issues)
    text = ch.sheet(base, 1, now=NOW)
    assert all("[wait:%d-29]" % n in text for n in range(2, 29)) and "group:wait" not in text


def test_a_wait_the_plan_saw_closed_is_not_asked_even_if_its_blocker_is_still_listed(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), goal(2, comments=["hold on #3 before any paid run"]), goal(3)])
    rec = tca._rec(base)
    assert [w["ref"] for w in ch.chain_asks.open_waits(rec)] == [3]
    for w in rec["unconfirmed_waits"]:
        w["state"] = "closed"
    assert ch.chain_asks.open_waits(rec) == []


def test_confirm_writes_the_dependency_marker_and_dismiss_posts_the_dismissal_at_arm_only(tmp_path):
    issues = [goal(1, 2, 3, 5), goal(2, comments=["hold on #3 before any paid run"]), goal(3),
              goal(5, comments=["hold on #3 before any paid run"])]
    ch, base, fk = scene(tmp_path, issues)
    answer_all(ch, base, fk, **{"wait:2-3": "confirm it", "wait:5-3": "dismiss it"})
    assert fk.writes() == []                                       # the sheet, the answers and the approval wrote nothing
    res = ch.arm(base, 1, run=fk, now=NOW)
    assert not any(a["result"] == "failed" for a in res["actions"]), res
    assert "**Blocked by:** #3" in fk.body(2)                       # the same write `deps.py mark` makes
    assert any("dismissed the 'blocked-by' finding against #3" in c and "ref=3" in c for c in fk.stored_comments(5))
    assert "Blocked by" not in fk.body(5)
    n = len(fk.writes())
    ch.arm(base, 1, run=fk, now=NOW)
    assert len(fk.writes()) == n                                   # idempotent: nothing is written twice
    again = ch.plan(base, 1, run=fk, now=NOW + 60)                 # a re-plan: a confirmed wait is an edge, a dismissed one is gone
    waits = {(w["goal"], w["ref"]) for w in again["unconfirmed_waits"]}
    assert (5, 3) not in waits
    assert 3 in next(r for r in again["members"] if r["number"] == 2)["waits_on"]


def test_a_dismissed_wait_is_no_edge_for_the_forecast_or_the_run_and_a_confirmed_one_still_holds(tmp_path):
    def issues():       # fresh dicts each time: the fake stores them, so a second use would see the first run's comment
        return [goal(1, 2, 3), goal(2, comments=["hold on #3 before any paid run"]), goal(3, who="bob")]
    ch, base, fk = scene(tmp_path, issues())
    rec = tca._rec(base)
    held_off = ch.chain_asks.view(dict(rec, answers={"wait:2-3": "dismiss it"}))
    assert next(r for r in held_off["members"] if r["number"] == 2)["waits_on"] == []
    assert all(not (e["from"] == 3 and e["to"] == 2) for e in held_off["edges"])
    kept = ch.chain_asks.view(dict(rec, answers={"wait:2-3": "confirm it"}))
    assert next(r for r in kept["members"] if r["number"] == 2)["waits_on"] == [3]
    # the run: with the wait dismissed 2 is claimable at once; with it confirmed 2 waits for 3 (which is bob's)
    for answer, picked in (("dismiss it", [2]), ("confirm it", [])):
        sub = tmp_path / answer.split()[0]
        ch2, base2, fk2 = scene(sub, issues())
        answer_all(ch2, base2, fk2, **{"wait:2-3": answer, "elsewhere:3": "wait for them"})
        ch2.arm(base2, 1, run=fk2, now=NOW)
        got = []

        def pick(label, skip, **kw):
            take = [n for n in (2,) if str(n) not in skip and n not in got]
            got.extend(take)
            return take
        res = ch2.run(base2, 1, pick=pick, now=NOW + 5, run=fk2, sweep=tcr._sweep)
        assert res["picked"] == picked, (answer, res)


# --- 4 -- a member somebody else holds ------------------------------------------------------------------------------------

def _elsewhere_chain():
    """1 waits on 2 (bob's) and 3 (in progress in another session) and 4 (alice's)."""
    return [goal(1, 2, 3, 4), goal(2, who="bob"), goal(3, who="bob", labels=("sdlc:in-progress",)), goal(4)]


def test_a_member_held_elsewhere_is_asked_once_and_one_in_progress_can_only_be_waited_for(tmp_path):
    ch, base, fk = scene(tmp_path, _elsewhere_chain())
    qs = {q["id"]: q for q in ch.questions_of(tca._rec(base), {})}
    assert qs["elsewhere:2"]["options"] == ["wait for them", "take it over"]
    assert qs["elsewhere:3"]["options"] == ["wait for them"]
    assert "in progress in another session" in qs["elsewhere:3"]["ask"] and "bob" in qs["elsewhere:3"]["ask"]
    assert "elsewhere:4" not in qs and "elsewhere:1" not in qs


def test_take_it_over_assigns_the_runner_and_removes_the_old_assignee_at_arm(tmp_path):
    ch, base, fk = scene(tmp_path, _elsewhere_chain())
    answer_all(ch, base, fk, **{"elsewhere:2": "take it over"})
    assert fk.assignees(2) == ["bob"] and fk.writes() == []
    ch.arm(base, 1, run=fk, now=NOW)
    assert fk.assignees(2) == ["alice"]                             # one assignee
    assert fk.assignees(3) == ["bob"]                               # the one in progress is never touched


def test_a_member_that_became_in_progress_before_arm_is_not_taken_over(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), goal(2, who="bob")])
    answer_all(ch, base, fk, **{"elsewhere:2": "take it over"})
    fk._set(2, fk._names(2) | {"sdlc:in-progress"})
    res = ch.arm(base, 1, run=fk, now=NOW)
    assert fk.assignees(2) == ["bob"] and [r for r in res["refused"] if r["issue"] == 2]


def test_waiting_for_them_stops_the_run_there_and_names_whose_it_is(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), goal(2, who="bob")])
    answer_all(ch, base, fk, **{"elsewhere:2": "wait for them"})
    ch.arm(base, 1, run=fk, now=NOW)
    fc = ch.chain_forecast.forecast(tca._rec(base), {}, "alice")
    res = ch.run(base, 1, pick=lambda label, skip, **kw: [], now=NOW + 5, run=fk, sweep=tcr._sweep)
    assert res["reason"] == "claimed" and "assigned to bob" in res["detail"] and "you chose to wait for them" in res["detail"]
    assert fc["detail"] == res["detail"]


def test_taking_a_member_over_makes_the_forecast_count_it(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), goal(2, who="bob")])
    before = ch.chain_forecast.forecast(tca._rec(base), {}, "alice")
    assert before["finish"] == []
    rec = tca._rec(base)
    rec["answers"] = {"elsewhere:2": "take it over"}
    assert ch.chain_forecast.forecast(rec, {}, "alice")["finish"] == [2, 1]


# --- 5 -- a paid step marked only by a label -------------------------------------------------------------------------------

def test_a_member_carrying_a_configured_paid_label_gets_the_spend_question_up_front(tmp_path):
    issues = [goal(1, 2), goal(2, labels=("paid",)), goal(3)]
    ch, base, fk = scene(tmp_path, issues)
    assert "irreversible:2" not in qids(ch, base)                         # default: no labels configured, none asked
    ch, base, fk = scene(tmp_path / "on", issues, config={"chain": {"paid_labels": ["paid"]}})
    q = next(q for q in ch.questions_of(tca._rec(base), {}) if q["id"] == "irreversible:2")
    assert q["options"][0] == "go ahead" and q["recommended"] is None            # never defaulted
    assert "Paid gates" in ch.sheet(base, 1, now=NOW)


def test_a_paid_label_answered_do_not_is_skipped_by_the_run(tmp_path):
    issues = [goal(1, 2), goal(2, labels=("paid",))]
    ch, base, fk = scene(tmp_path, issues, config={"chain": {"paid_labels": ["paid"]}})
    answer_all(ch, base, fk, **{"irreversible:2": "do not"})
    ch.arm(base, 1, run=fk, now=NOW)
    res = ch.run(base, 1, pick=lambda label, skip, **kw: [], now=NOW + 5, run=fk, sweep=tcr._sweep)
    assert res["reason"] == "held-gate" and "#2 is a paid step you answered not to run" in res["detail"]


def test_a_malformed_paid_labels_value_is_refused_not_ignored(tmp_path):
    ch = tca._chain()
    base, fk = tca._scene(tmp_path, approve=False, issues=[goal(1, 2), goal(2)])
    cp = pathlib.Path(base) / "config.json"
    cfg = json.loads(cp.read_text())
    cfg["chain"] = {"paid_labels": "paid"}
    cp.write_text(json.dumps(cfg))
    with pytest.raises(ch.ChainRefused, match="paid_labels"):
        ch.plan(base, 1, run=fk, now=NOW)


# --- 6 -- the cap and the unit boundary are enacted -----------------------------------------------------------------------

def test_the_answered_cap_is_enforced_by_the_run_and_the_forecast_models_it(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2, 3), goal(2), goal(3)])
    answer_all(ch, base, fk, **{"cap:c1": "2"})
    ch.arm(base, 1, run=fk, now=NOW)
    fc = ch.chain_forecast.forecast(tca._rec(base), {}, "alice", cap=ch._cap_of(tca._rec(base), {}))
    res = ch.run(base, 1, pick=lambda *a, **k: [2], now=NOW + 5, run=fk, sweep=tcr._sweep)
    assert res["state"] == "capped" and "3 goals over the 2 goal cap" in res["detail"], res
    assert fc["reason"] == "capped" and fc["detail"] == res["detail"] and fc["finish"] == []
    assert tca._rec(base)["run"]["cap_in_force"] == 2


def test_a_larger_answered_cap_does_not_lift_a_tighter_configured_one(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2, 3), goal(2), goal(3)])
    answer_all(ch, base, fk, **{"cap:c1": "50"})
    assert ch.chain_asks.effective_cap(tca._rec(base), {"chain": {"max_goals": 2}}) == 2
    assert ch.chain_asks.effective_cap(tca._rec(base), {}) == 50


def test_stop_here_at_a_unit_boundary_holds_the_dependent(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), goal(2)])
    rec = tca._rec(base)
    rec["cross_unit"] = [{"from": 2, "to": 1, "units": ["alpha", "beta"]}]
    tca._put(base, rec)
    q = next(q for q in ch.questions_of(tca._rec(base), {}) if q["kind"] == "cross_unit")
    assert q["ask"].startswith("#1 (unit beta) waits on #2 (unit alpha)")       # the dependent waits on the blocker
    rec = tca._rec(base)
    rec["answers"] = {"cross_unit:2-1": "stop here"}
    assert ch._held(rec) == {1}
    assert ch.chain_forecast.forecast(rec, {}, "alice", ch._held(rec), ())["finish"] == [2]
    rec["answers"] = {"cross_unit:2-1": "keep going"}
    assert ch._held(rec) == set()


# --- 7 -- nothing is written before approval ------------------------------------------------------------------------------

def test_no_question_kind_writes_before_approval(tmp_path):
    issues = [goal(1, 2, 3, 5, 6), parked(2), goal(3, who="bob"), goal(5, comments=["hold on #6 before any paid run"]),
              goal(6, labels=("paid",))]
    ch, base, fk = scene(tmp_path, issues, config={"chain": {"paid_labels": ["paid"]}})
    ch.sheet(base, 1, now=NOW)
    answer_all(ch, base, fk, **{"park:2": "release it", "elsewhere:3": "take it over", "wait:5-6": "confirm it",
                                "irreversible:6": "go ahead"})
    ch.sheet(base, 1, now=NOW)
    assert fk.writes() == [] and fk.created == []
    assert fk.assignees(3) == ["bob"] and "sdlc:parked" in fk._names(2)


# --- 8 -- a question without options never offers a number; approve shows the forecast the answers give -----------------------

def test_only_the_cap_offers_a_number_and_a_free_text_question_says_where_it_is_answered(tmp_path):
    ch = tca._chain()
    free = {"kind": "needs_decision", "recommended": None, "options": [], "ask": "Which way do you want it?"}
    text = "\n".join(ch._decision_lines(1, "needs_decision:4", free, {}))
    assert "positive whole number" not in text and "/sigma-unpark" in text
    cap = {"kind": "cap", "recommended": None, "options": [], "ask": "What cap?"}
    assert "positive whole number" in "\n".join(ch._decision_lines(1, "cap:c1", cap, {}))


def test_approve_prints_the_forecast_the_answers_give(tmp_path, capsys):
    ch, base, fk = scene(tmp_path, [goal(1, 2), parked(2)])
    res = ch.answer(base, 1, {"park:2": "keep it parked", "cap:c1": "50"}, run=fk)
    assert res["state"] == "approved"
    assert ch.main(["chain.py", "approve", base, "1"], run=fk) == 0
    out = capsys.readouterr().out
    assert "chain #1: approved" in out and "can finish 0 of 2" in out and "you answered to keep it parked" in out
    ch.answer(base, 1, {"park:2": "release it"}, run=fk)
    assert ch.main(["chain.py", "approve", base, "1"], run=fk) == 0
    assert "can finish all 2 of 2" in capsys.readouterr().out


# --- 9 -- a parked member that is also somebody else's -----------------------------------------------------------------------

def test_a_parked_member_that_is_someone_elses_is_asked_about_both(tmp_path):
    """Releasing it would hand the run a member bob holds: that must be on the sheet too, not found by the run."""
    ch, base, fk = scene(tmp_path, [goal(1, 2), parked(2, who="bob")])
    qs = {q["id"]: q for q in ch.questions_of(tca._rec(base), {})}
    assert "park:2" in qs and "elsewhere:2" in qs
    assert "also parked" in qs["elsewhere:2"]["ask"]


def test_releasing_a_member_bob_holds_forecasts_a_stop_there_that_names_bob(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), parked(2, who="bob")])
    rec = tca._rec(base)
    rec["answers"] = {"park:2": "release it", "elsewhere:2": "wait for them"}
    fc = ch.chain_forecast.forecast(rec, {}, "alice")
    assert fc["finish"] == [] and fc["reason"] == "claimed" and "assigned to bob" in fc["detail"], fc
    rec["answers"] = {"park:2": "release it", "elsewhere:2": "take it over"}
    assert ch.chain_forecast.forecast(rec, {}, "alice")["finish"] == [2, 1]


def test_taking_over_a_member_that_stays_parked_changes_nobodys_assignment(tmp_path):
    ch, base, fk = scene(tmp_path, [goal(1, 2), parked(2, who="bob")])
    answer_all(ch, base, fk, **{"park:2": "keep it parked", "elsewhere:2": "take it over"})
    ch.arm(base, 1, run=fk, now=NOW)
    assert fk.assignees(2) == ["bob"]
    ch2, base2, fk2 = scene(tmp_path / "rel", [goal(1, 2), parked(2, who="bob")])
    answer_all(ch2, base2, fk2, **{"park:2": "release it", "elsewhere:2": "take it over"})
    ch2.arm(base2, 1, run=fk2, now=NOW)
    assert fk2.assignees(2) == ["alice"] and "sdlc:parked" not in fk2._names(2)


# --- 10 -- the assignee removal behind `take it over` -------------------------------------------------------------------

def test_remove_assignees_deletes_the_login_and_refuses_a_response_that_still_lists_it():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gh_api", pathlib.Path(tca.__file__).resolve().parent.parent
                                                  / "skills" / "sigma-loop" / "scripts" / "gh_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    seen = []

    def gone(args):
        seen.append(list(args))
        return json.dumps({"assignees": [{"login": "alice"}]})
    api.remove_assignees(gone, 5, ["bob"], "O/N", env={})
    assert seen == [["api", "repos/O/N/issues/5/assignees", "--method", "DELETE", "-f", "assignees[]=bob"]]
    with pytest.raises(api.GhApiError) as ei:
        api.remove_assignees(lambda a: json.dumps({"assignees": [{"login": "Bob"}]}), 5, ["bob"], "O/N", env={})
    assert ei.value.kind == "invalid"
    with pytest.raises(api.GhApiError):
        api.remove_assignees(gone, 5, ["org/team"], "O/N", env={})


def test_a_member_in_progress_is_asked_even_when_it_is_assigned_to_the_runner(tmp_path):
    """The label says a session holds it, whoever it is assigned to (another session of the same login is one)."""
    ch, base, fk = scene(tmp_path, [goal(1, 2), goal(2, who="alice", labels=("sdlc:in-progress",))])
    q = next(q for q in ch.questions_of(tca._rec(base), {}) if q["id"] == "elsewhere:2")
    assert q["options"] == ["wait for them"] and "in progress in another session" in q["ask"]


# --- 11 -- a member that waits only on a left-out member is held by it, and the stop names the left-out one ----------------

def test_a_member_waiting_only_on_a_left_out_member_is_not_named_as_what_holds_the_chain(tmp_path):
    cf = tca._chain().chain_forecast
    p = {"members": [{"number": 61, "verdict": "armed", "waits_on": [52], "kind": None}],
         "outside": [{"number": 52, "verdict": "outside", "own_verdict": "not-a-goal", "checkpoint": None}]}
    reason, detail = cf.stuck_reason(p)
    assert reason == "decision" and "#52 was left out" in detail, (reason, detail)
    assert cf.other_roots(p, detail) == ([], 0)                   # 61 is held by 52, which the stop line already names
    p["members"].append({"number": 70, "verdict": "armed", "waits_on": [], "kind": None})
    others, more = cf.other_roots(p, detail)
    assert others == [70] and 61 not in others


# --- 12 -- two gaps found by the offline replay of a tangled backlog ----------------------------------------------------------

def test_a_paid_parked_member_whose_route_question_was_answered_still_gets_the_spend_question(tmp_path):
    """The spend question `irreversible:N` shares its id with a parked member's own route question. A recorded answer
    to the route question must not hide the spend question (it did: the run then stopped on a paid step nobody asked)."""
    issues = [goal(1, 2), goal(2, labels=("sdlc:parked", "paid"), comments=[PARK.replace("needs_decision", "irreversible")])]
    ch, base, fk = scene(tmp_path, issues, config={"chain": {"paid_labels": ["paid"]}})
    rec = tca._rec(base)
    row = next(r for r in rec["members"] if r["number"] == 2)
    row["answered"] = True                                  # its own route question was answered on GitHub in an earlier round
    tca._put(base, rec)
    assert "irreversible:2" in qids(ch, base)


def test_an_owner_gated_member_somebody_else_holds_is_asked_about_both_and_promoting_it_arms_it_for_the_forecast(tmp_path):
    NC = "sdlc:needs-confirmation"
    ch, base, fk = scene(tmp_path, [goal(1, 2), tla.issue(2, NC, "sdlc:followup", assignees=("bob",), body=DW)])
    ids = qids(ch, base)
    assert "owner_gate:2" in ids and "elsewhere:2" in ids
    rec = tca._rec(base)
    rec["answers"] = {"owner_gate:2": "promote it", "elsewhere:2": "wait for them"}
    fc = ch.chain_forecast.forecast(rec, {}, "alice")
    assert fc["finish"] == [] and fc["reason"] == "claimed" and "assigned to bob" in fc["detail"], fc
    rec["answers"] = {"owner_gate:2": "promote it", "elsewhere:2": "take it over"}
    assert ch.chain_forecast.forecast(rec, {}, "alice")["finish"] == [2, 1]
    rec["answers"] = {"owner_gate:2": "leave it out", "elsewhere:2": "take it over"}      # stays left out: nobody's assignment changes
    assert 2 not in ch.chain_asks.taken_over(rec)

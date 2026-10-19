"""#1908: what a run and a forecast do with the owner's answers, and what `answer --defaults` never decides.

`chain.py answer --defaults` never arms a member that awaits its owner and never accepts a spend gate: both stay
open, and the chain is not approved until a person answers them. A run honours the answers already in the record:
a paid step answered anything but "go ahead" is never claimed and stops the run as `held-gate`; a dependent kept
waiting on a closed prerequisite's open follow-up is skipped and named; a member left out of the chain that work
waits on stops the run as `decision`; a wait read from prose (which the loop's pick cannot see) holds its member
back until the blocker is confirmed closed. The forecast reads the same answers. A stateful fake `gh` and a pick
that knows only labels and explicit edges; no network.

Controls (each clause alone, then restored; every one was seen red): the spend question given a default; the
owner-gate question given a default; the prose wait not handed to the pick as a skip; the left-out member still a
member of the run; a declined paid step claimed; a declined paid step not named at the stop; the forecast not
reading the answers; a kept-waiting dependent not skipped, not named, not forecast; a left-out member closed by a
person not confirmed."""
import contextlib
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resource_mode  # noqa: E402,F401
import test_chain_arm as tca  # noqa: E402
import test_label_answer as tla  # noqa: E402
from test_label_drain_arm import DW  # noqa: E402

NOW = tca.NOW
NC = "sdlc:needs-confirmation"
PAID = "Paid step: runpodctl create pod for the render\n\n" + DW


def _backlog():
    """Goal 1 waits on 2 (ready, with a wait only a comment says), 3 (a follow-up waiting for its owner), 4 (only
    the blocking overlay), 5 (a checkpoint), 6 (a paid step), 7 (ready once 2 is done) and 8 (a paid step that is
    not armed either)."""
    return [tla.issue(1, "sdlc:goal", assignees=("alice",), body="".join("Blocked by #%d\n" % n for n in range(2, 9))
                      + "\n" + DW),
            tla.issue(2, "sdlc:goal", assignees=("alice",), body=DW, comments=["hold on #9 before any paid run"]),
            tla.issue(3, NC, "sdlc:followup", body=DW),
            tla.issue(4, "sdlc:blocking", body=DW),
            tla.issue(5, "sdlc:checkpoint", body=DW),
            tla.issue(6, "sdlc:goal", assignees=("alice",), body=PAID),
            tla.issue(7, "sdlc:goal", assignees=("alice",), body="Blocked by #2\n\n" + DW),
            tla.issue(8, body=PAID),
            tla.issue(9, "sdlc:goal", assignees=("alice",), body=DW)]


def _say(ch, argv, **kw):
    """Run a documented gesture through `main`, as the docs give it. -> (exit code, stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = ch.main(argv, **kw)
    return code, out.getvalue()


def _sheet(tmp_path, approve=False):
    base, fk = tca._scene(tmp_path, approve=approve, issues=_backlog())
    rec = tca._rec(base)            # the fixture's hard-stop net is off, so mark the paid member as the plan would
    for r in rec["members"]:
        r["spend"] = r.get("spend") or r["number"] in (6, 8)
    tca._put(base, rec)
    code, text = _say(tca._chain(), ["chain.py", "sheet", base, "1"], now=NOW)       # the gesture the docs give
    assert code == 0, text
    return base, fk, text


def test_answer_defaults_never_arms_an_owner_gated_member_or_accepts_a_spend_gate(tmp_path):
    base, fk, _text = _sheet(tmp_path)
    ch = tca._chain()
    code, _out = _say(ch, ["chain.py", "answer", base, "1", "--defaults"], run=fk, now=NOW)    # as the docs give it
    assert code == 0
    res = {"open": [q["id"] for q in ch.open_questions(tca._rec(base), {})[1]]}
    rec = tca._rec(base)
    answers = rec["answers"]
    assert answers["not_goal:4"] == "leave it out"          # leaving a non-goal out arms nothing
    assert "owner_gate:3" not in answers and "owner_gate:3" in res["open"]    # the fate of a follow-up is the owner's
    assert "irreversible:6" not in answers and "irreversible:6" in res["open"]
    assert "irreversible:8" not in answers and "irreversible:8" in res["open"]
    assert "cap:c1" in res["open"]
    # the rest answered, the two questions that only the owner can answer still open: the chain is not approved
    more = ch.answer(base, 1, {"cap:c1": "5"}, run=fk)
    assert more["state"] == "awaiting-answers" and sorted(more["open"]) == [
        "irreversible:6", "irreversible:8", "owner_gate:3"]
    assert not any(w[0] in (3, 4) for w in tca._writes(fk))


# --- the run and the forecast honour the answers (#1908) --------------------------------------------------------

class NaivePick:
    """A pick that knows only labels, the goal label, the explicit `Blocked by` edges (the gate the loop's pick
    really has) and the skip list it is handed -- like a gate that has never read the prose a wait was written
    in. Everything else the chain holds back, the chain must say in `skip`."""

    def __init__(self, fk, base, batch=9):
        self.fk, self.base, self.batch, self.claims = fk, base, batch, []

    def __call__(self, label, skip, **_kw):
        got = []
        explicit = {}
        for e in tca._rec(self.base)["edges"]:
            if e["tag"] == "explicit":
                explicit.setdefault(e["to"], []).append(e["from"])
        for n in sorted(self.fk.by):
            names = {l["name"] for l in self.fk.by[n]["labels"]}
            if self.fk.by[n]["state"] == "closed" or label not in names or "sdlc:goal" not in names \
                    or str(n) in skip or n in self.claims or names & {"story", "sdlc:parked"} \
                    or any(self.fk.by[b]["state"] != "closed" for b in explicit.get(n, ())):
                continue
            got.append(n)
            self.claims.append(n)
            if len(got) == self.batch:
                break
        return got


def _go(tmp_path, issues, spend=(), answers=None):
    """Plan goal 1, flag `spend` members as paid, answer everything, approve, arm. -> (chain, base, fk)."""
    ch = tca._chain()
    base, fk = tca._scene(tmp_path, approve=False, issues=issues)
    rec = tca._rec(base)
    for r in rec["members"]:
        if r["number"] in spend:
            r["spend"] = True
    tca._put(base, rec)
    ch.answer(base, 1, defaults=True, run=fk)
    got = ch.answer(base, 1, dict({"cap:c1": "5"}, **(answers or {})), run=fk)
    assert got["state"] == "approved", got
    ch.arm(base, 1, run=fk, now=NOW)
    return ch, base, fk


def _cycle(ch, base, fk, pick):
    return ch.run(base, 1, pick=pick, now=NOW, run=fk, sweep=lambda *a: ())


def _prose_wait_backlog():
    """Goal 1 waits on 2 only in prose; the gate the picker uses cannot read that."""
    return [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Starts only once #2 has landed.\n\n" + DW),
            tla.issue(2, "sdlc:goal", assignees=("alice",), body=DW)]


def test_a_member_waiting_in_prose_is_held_back_until_its_blocker_closes(tmp_path):
    ch, base, fk = _go(tmp_path, _prose_wait_backlog())
    assert {(e["from"], e["to"]): e["tag"] for e in tca._rec(base)["edges"]} == {(2, 1): "unconfirmed"}
    pick = NaivePick(fk, base)
    assert _cycle(ch, base, fk, pick)["picked"] == [2]
    assert pick.claims == [2], "the pick was handed #1 although the record says it waits on #2"
    fk.by[2]["state"] = "closed"
    assert _cycle(ch, base, fk, pick)["picked"] == [1]


def test_a_left_out_member_that_work_waits_on_stops_the_run_naming_it(tmp_path):
    issues = [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Starts only once #4 has landed.\n\n" + DW),
              tla.issue(4, "sdlc:blocking", body=DW)]
    ch, base, fk = _go(tmp_path, issues)
    res = _cycle(ch, base, fk, NaivePick(fk, base))
    assert res["state"] == "stuck" and res["reason"] == "decision" and "#4" in res["detail"], res
    assert 4 not in tca._rec(base)["run"].get("picked", [])


def test_a_paid_step_the_owner_declined_is_never_claimed_and_the_stop_names_it(tmp_path):
    issues = [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Blocked by #2\n\n" + DW),
              tla.issue(2, "sdlc:goal", assignees=("alice",), body=PAID)]
    ch, base, fk = _go(tmp_path, issues, spend=(2,), answers={"irreversible:2": "do not"})
    pick = NaivePick(fk, base)
    res = _cycle(ch, base, fk, pick)
    assert pick.claims == [] and res["state"] == "stuck" and res["reason"] == "held-gate" and "#2" in res["detail"], res


def test_a_paid_step_the_owner_approved_is_claimed(tmp_path):
    issues = [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Blocked by #2\n\n" + DW),
              tla.issue(2, "sdlc:goal", assignees=("alice",), body=PAID)]
    ch, base, fk = _go(tmp_path, issues, spend=(2,), answers={"irreversible:2": "go ahead"})
    assert _cycle(ch, base, fk, NaivePick(fk, base, batch=1))["picked"] == [2]


def test_the_forecast_reads_the_spend_and_closed_gate_answers(tmp_path):
    issues = [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Blocked by #2\n\n" + DW),
              tla.issue(2, "sdlc:goal", assignees=("alice",), body=PAID)]
    ch, base, fk = _go(tmp_path, issues, spend=(2,), answers={"irreversible:2": "do not"})
    text = ch.sheet(base, 1, now=NOW)
    assert "can finish 0 of 2 before it stops on held-gate: #2 not workable (a paid step you answered not to run)" in text
    ch, base, fk = _go(tmp_path / "ok", issues, spend=(2,), answers={"irreversible:2": "go ahead"})
    assert "can finish all 2 of 2" in ch.sheet(base, 1, now=NOW)


def test_a_dependent_kept_waiting_on_a_closed_prerequisite_is_named_when_the_run_stops(tmp_path):
    issues = [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Blocked by #2\n\n" + DW),
              tla.issue(2, "sdlc:goal", state="closed", comments=["done, but the target was missed"]),
              tla.issue(7, "sdlc:goal", "sdlc:followup", body="Follow-up to #2: the target was missed.\n\n" + DW)]
    ch, base, fk = _go(tmp_path, issues)
    assert tca._rec(base)["answers"]["closed_gate:2"] == "keep waiting"
    pick = NaivePick(fk, base)
    res = _cycle(ch, base, fk, pick)
    assert pick.claims == [] and res["state"] == "stuck" and res["reason"] == "decision", res
    assert "#1 is kept waiting on a closed prerequisite's open follow-up" in res["detail"]
    assert "can finish 0 of 1 before it stops on decision: #1 needs decision (kept waiting" in ch.sheet(base, 1, now=NOW)


def test_the_forecast_table_has_a_row_for_every_verdict_the_label_drain_can_give():
    """A verdict the table does not know is never promised (it reads as not workable): so every verdict the judge can
    return must have a row, or the forecast quietly says 'not workable' about a member it ought to name."""
    import inspect
    import re
    ch = tca._chain()
    judged = set(re.findall(r'return "([a-z-]+)", ', inspect.getsource(ch.label_drain._Judge.verdict)))
    assert {"armed", "parked", "checkpoint", "awaiting-owner", "not-a-goal"} <= judged, judged
    missing = sorted(judged - {"closed"} - set(ch.chain_forecast.VERDICTS))
    assert not missing, "the forecast table has no row for %s" % missing
    markers = {v: ch.chain_forecast.VERDICTS[v][0] for v in ("checkpoint", "awaiting-owner", "not-a-goal")}
    assert markers == {"checkpoint": "checkpoint", "awaiting-owner": "needs decision", "not-a-goal": "needs decision"}


def test_a_left_out_member_closed_by_a_person_releases_what_waited_on_it(tmp_path):
    issues = [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Starts only once #4 has landed.\n\n" + DW),
              tla.issue(4, "sdlc:blocking", body=DW)]
    ch, base, fk = _go(tmp_path, issues)
    pick = NaivePick(fk, base)
    assert _cycle(ch, base, fk, pick)["state"] == "stuck"
    fk.by[4]["state"] = "closed"
    res = _cycle(ch, base, fk, pick)
    assert res["picked"] == [1], res

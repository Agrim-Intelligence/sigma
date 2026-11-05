"""#2139 review fixes: the label-held stop never takes over a member an answer already holds, a blocked release never
reverses a park put on after approval, and a plan check that stops on a defect says so.

1. A blocked root that a person's answer already holds keeps the stop that answer gives, in the run and in the
   forecast alike: a paid step answered `do not` stops `held-gate`, and a member kept waiting on a closed
   prerequisite's open follow-up stops `decision` with the kept-waiting words. The run's rows carry the live verdict
   (`armed`) and get those answers as parameters, the forecast's rows carry `declined` / `kept-waiting`; the
   label-held branch must read both the same way, so neither says `release it with /sigma-unpark`.
2. `release it` answered to `blocked:N` releases the blocked label. A member parked after approval and before `arm`
   (it no longer carries the blocked label) is not unparked by `arm`: the answer was about the blocked label. A
   `park:N` release is unchanged.
3. The plan's read-only sweep check reads any exception as `unread` (never a yes), and one that is not a failed `gh`
   call (a defect, or a page `gh_api` refuses) writes one short line to stderr naming the exception type.

Harness reused, not copied: `test_chain_asked_up_front` (scene, goal, answer_all), `test_chain_arm` (_rec, NOW),
`test_chain_run_freed_2139` (closed, _stamp, Pick, _blk), `test_chain_blocked_member_2139` (_blocked, _row, _replan).
A stateful fake `gh`; no network. Numbers in fixture text are built with `%d`.
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resource_mode  # noqa: E402,F401
import test_chain_arm as tca  # noqa: E402
import test_chain_asked_up_front as tcau  # noqa: E402
import test_chain_blocked_member_2139 as tbm  # noqa: E402
import test_chain_run_freed_2139 as tfr  # noqa: E402
import test_label_answer as tla  # noqa: E402
from test_label_drain_arm import DW  # noqa: E402

NOW = tca.NOW
PAID = {"chain": {"paid_labels": ["paid"]}}
LABEL_HELD_WORDS = "carries the blocked label"
DECLINED_STOP = ("held-gate", "#%d is a paid step you answered not to run; answer go ahead, or change the plan" % 2)
KEPT_WAITING_STOP = ("decision", "#%d is kept waiting on a closed prerequisite's open follow-up or question (your "
                                 "answer); finish what it waits on, or answer proceed" % 2)


def _sheet_forecast(ch, base):
    """The forecast as the sheet and `approve` compute it (`chain._forecast_text`'s own call): the record as the
    answers leave it, with the members kept waiting and the paid steps declined passed in."""
    rec = tca._rec(base)
    config = ch._load("state").load_config(base)
    return ch.chain_forecast.forecast(ch.chain_asks.view(rec), config, None, ch._held(rec, True), ch._declined(rec),
                                      ch._held(rec, True) - ch._held(rec), ch._cap_of(rec, config))


def _declined_scene(tmp_path):
    """Member 2 carries a paid label, the blocked label and a `Blocked by` marker for 3, which is closed: the plan says
    the sweep lifts it (nothing asked about the label); its spend question is answered `do not`."""
    issues = [tcau.goal(1, 2), tbm._blocked(2, 3, labels=("paid",)), tfr.closed(tcau.goal(3))]
    ch, base, fk = tcau.scene(tmp_path, issues, config=PAID)
    assert tbm._row(base, 2).get("sweep_lifts") is True, tbm._row(base, 2)
    tcau.answer_all(ch, base, fk, **{"irreversible:2": "do not"})
    return ch, base, fk


def _kept_waiting_scene(tmp_path):
    """Member 2 carries the blocked label and a `Blocked by` marker for 3, which is closed but has an open follow-up,
    7: the plan says the sweep lifts it, and the closed-prerequisite question takes its recommended `keep waiting`."""
    issues = [tcau.goal(1, 2), tbm._blocked(2, 3), tfr.closed(tcau.goal(3)),
              tla.issue(7, "sdlc:goal", "sdlc:followup", body="Follow-up to #%d: the target was missed.\n\n%s" % (3, DW))]
    ch, base, fk = tcau.scene(tmp_path, issues)
    assert tbm._row(base, 2).get("sweep_lifts") is True, tbm._row(base, 2)
    tcau.answer_all(ch, base, fk)
    assert tca._rec(base)["answers"].get("closed_gate:3") == "keep waiting", tca._rec(base)["answers"]
    return ch, base, fk


# --- 1 -- an answer that holds a blocked root keeps its own stop, in the run and the forecast alike ----------------------

def _stops(ch, base, fk):
    """Arm, stamp the sweep interval as just swept (so the run's own sweep is skipped and the label stays; the forced
    sweep must not fire for a member the pick skips), run one cycle with a pick that refuses what the real pick
    refuses. -> (the run's (reason, detail), the sheet forecast's (reason, detail), the run result)."""
    res = ch.arm(base, 1, run=fk, now=NOW)
    assert not any(a["result"] == "failed" for a in res["actions"]) and tca._rec(base).get("armed_at"), res
    assert "sdlc:blocked" in fk._names(2), fk._names(2)
    fc = _sheet_forecast(ch, base)
    tfr._stamp(base)
    got = ch.run(base, 1, pick=tfr.Pick(fk, base), now=NOW + 1, run=fk)
    assert "sdlc:blocked" in fk._names(2) and not tfr._blk(base).get("forced"), (fk._names(2), tfr._blk(base))
    return (got["reason"], got["detail"]), (fc["reason"], fc["detail"]), got


def test_a_declined_blocked_root_stops_held_gate_in_the_run_and_the_forecast_alike(tmp_path, monkeypatch):
    monkeypatch.delenv("SIGMA_RUN_ID", raising=False)
    ch, base, fk = _declined_scene(tmp_path)
    run_stop, fc_stop, got = _stops(ch, base, fk)
    assert got["state"] == "stuck" and got["exit"] == 3, got
    assert run_stop == fc_stop, (run_stop, fc_stop)
    assert run_stop == DECLINED_STOP, run_stop
    assert LABEL_HELD_WORDS not in run_stop[1] and "/sigma-unpark" not in run_stop[1], run_stop


def test_a_kept_waiting_blocked_root_keeps_the_kept_waiting_stop_in_the_run_and_the_forecast_alike(tmp_path,
                                                                                                  monkeypatch):
    monkeypatch.delenv("SIGMA_RUN_ID", raising=False)
    ch, base, fk = _kept_waiting_scene(tmp_path)
    run_stop, fc_stop, got = _stops(ch, base, fk)
    assert got["state"] == "stuck" and got["exit"] == 3, got
    assert run_stop == fc_stop, (run_stop, fc_stop)
    assert run_stop == KEPT_WAITING_STOP, run_stop
    assert LABEL_HELD_WORDS not in run_stop[1] and "/sigma-unpark" not in run_stop[1], run_stop


def test_the_pure_stop_rule_lets_an_answer_hold_an_armed_blocked_root():
    """The rule both callers share, on the run's row shape (live verdict `armed`, the answers as parameters); `keep it
    blocked` still wins over either answer, and with no answer the label-held stop is named."""
    rule = tca._chain().chain_forecast.stuck_reason
    row = {"number": 2, "verdict": "armed", "blocked": True, "waits_on": [], "in_progress": False}
    p = {"members": [dict(row)], "outside": []}
    assert rule(p, (), (), {2}) == DECLINED_STOP
    assert rule(p, (), {2}, ()) == KEPT_WAITING_STOP
    assert rule(p, (), {2}, {2}, {"kept_blocked": {2}})[1].startswith("#%d is blocked and you answered to keep it" % 2)
    assert rule(p, (), (), ())[1] == tfr.LABEL_HELD % (2, 2)


# --- 2 -- a blocked release never reverses a park put on after approval --------------------------------------------------

def test_a_blocked_release_does_not_unpark_a_member_parked_after_approval(tmp_path):
    ch, base, fk = tcau.scene(tmp_path / "blocked", [tcau.goal(1, 2), tbm._blocked(2)])
    tcau.answer_all(ch, base, fk, **{"blocked:2": "release it"})
    fk._set(2, (fk._names(2) - {"sdlc:blocked", "sdlc:goal"}) | {"sdlc:parked"})     # parked after approval
    res = ch.arm(base, 1, run=fk, now=NOW)
    assert not [a for a in res["actions"] if a["action"] == "unpark"], res["actions"]
    assert "sdlc:parked" in fk._names(2), fk._names(2)
    assert "unpark-qa" not in fk.body(2)
    # a park:N release is unchanged: the parked member is released through the unpark gesture
    ch2, base2, fk2 = tcau.scene(tmp_path / "parked", [tcau.goal(1, 2), tcau.parked(2)])
    tcau.answer_all(ch2, base2, fk2, **{"park:2": "release it"})
    res2 = ch2.arm(base2, 1, run=fk2, now=NOW)
    assert [a["action"] for a in res2["actions"]][:1] == ["unpark"], res2["actions"]
    assert "sdlc:parked" not in fk2._names(2) and "sdlc:goal" in fk2._names(2), fk2._names(2)


def test_a_parked_member_that_also_carries_the_blocked_label_reads_unblocked_once_released(tmp_path):
    """`unpark.resolve` removes both labels at `arm`, so the answered view must not still show the blocked label: the
    forecast would then stop on the label-held `decision` for a member the run claims on its first cycle."""
    both = tcau.goal(2, who="alice", labels=("sdlc:parked", "sdlc:blocked"), comments=[tcau.PARK])
    ch, base, fk = tcau.scene(tmp_path, [tcau.goal(1, 2), both])
    tcau.answer_all(ch, base, fk, **{"park:2": "release it"})
    view = ch.chain_asks.view(tca._rec(base))
    row = next(r for r in view["members"] if r["number"] == 2)
    assert row["blocked"] is False and row["verdict"] == "armed", row
    config = ch._load("state").load_config(base)
    fc = ch.chain_forecast.forecast(view, config, None, (), (), (), None)
    assert LABEL_HELD_WORDS not in (fc.get("detail") or ""), fc
    ch.arm(base, 1, run=fk, now=NOW)
    assert not {"sdlc:parked", "sdlc:blocked"} & fk._names(2), fk._names(2)


# --- 3 -- a check that stops on a defect is unread, and says so -----------------------------------------------------------

def test_a_check_that_stops_on_a_defect_is_unread_and_names_the_exception_on_stderr(tmp_path, monkeypatch, capsys):
    issues = [tcau.goal(1, 2), tbm._blocked(2, 3, comments=["Looking at it."]), tfr.closed(tcau.goal(3))]
    ch, base, fk = tcau.scene(tmp_path, issues)
    assert tbm._row(base, 2).get("sweep_lifts") is True
    real = ch._load

    def load(name):
        m = real(name)
        if name == "auto_unpark":
            def defect(*_a, **_k):
                raise TypeError("a defect in the sweep's function")
            m.compute_unpark_actions = defect
        return m
    monkeypatch.setattr(ch, "_load", load)
    capsys.readouterr()
    tbm._replan(ch, base, fk)
    row = tbm._row(base, 2)
    assert row.get("sweep_lifts") is False and row.get("sweep_why") == "unread", row
    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if "TypeError" in line]
    assert len(lines) == 1 and "#2" in lines[0] and "unread" in lines[0], err
    # an ordinary failed read (the `gh` call raised) is `unread` too, and adds no such line
    monkeypatch.setattr(ch, "_load", real)

    def broken(args):
        a = [str(x) for x in args]
        if a[0] == "api" and re.fullmatch(r"repos/[^/]+/[^/]+/issues/2/comments", a[1]):
            fk.calls.append(a)
            raise RuntimeError("HTTP 502 Bad Gateway")
        return fk(args)
    tbm._replan(ch, base, fk, run=broken)
    assert tbm._row(base, 2).get("sweep_why") == "unread", tbm._row(base, 2)
    assert "sweep check" not in capsys.readouterr().err

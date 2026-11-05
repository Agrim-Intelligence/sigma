"""#2139 C: a run cycle whose sweep freed a member is `running`, and a root held only by the label stops on `decision`.

The sweep lifts a member's blocked label inside the cycle; the #1129 cooldown then keeps that member out of the same
cycle's pick, so the pick comes back empty. That cycle is `running`, exit 0, and names the freed member; the next
cycle claims it. A sweep the #2021 interval skipped is forced once per member per run for a blocked member with no
open wait. A root held only by the label stops `decision` naming `/sigma-unpark #N`, never `claimed`, and the forecast
gives the same stop. Before stopping on such a root the run re-reads it once past the cycle's read cache: a label the
pick's own sweep already lifted reads as freed (once per member per run; only a real sweep lift moves the heartbeat).
A failed pick is never `running`.

Injected picks here (`Pick` refuses what the real pick refuses: `not_eligible_labels()` and the skip); the real
`loop.py next-batch` pick is driven in `test_chain_real_pick_2139.py`. A stateful fake `gh`; no network. Numbers in
fixture text are built with `%d`.
"""
import json
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gqlfake  # noqa: E402
import resource_mode  # noqa: E402,F401
import test_chain_arm as tca  # noqa: E402
import test_chain_asked_up_front as tcau  # noqa: E402
import test_chain_run as tcr  # noqa: E402

NOW = tca.NOW
BLOCKED = "sdlc:blocked"
FREED = "#1 was freed"
LABEL_HELD = "#%d carries the blocked label and nothing in this chain lifts it: release it with /sigma-unpark #%d"


def closed(issue):
    """A closed fake issue, in every shape a reader checks."""
    issue.update(state="closed", state_reason="completed", stateReason="COMPLETED")
    return issue


def _close(fk, *nums):
    for n in nums:
        closed(fk.by[n])


def _set_auto_unpark(base, **block):
    cp = pathlib.Path(base) / "config.json"
    cfg = json.loads(cp.read_text())
    cfg["discovery"]["auto_unpark"] = dict(block)
    cp.write_text(json.dumps(cfg))


def _config(base):
    return json.loads((pathlib.Path(base) / "config.json").read_text())


def _stamp(base, session=None):
    """The #2021 interval file, as a sweep that ran just now (the sweep's own clock is the wall clock)."""
    p = pathlib.Path(base) / "state" / "auto-unpark-last.json"
    p.write_text(json.dumps({"swept_at": time.time(), "session": session}))


class Pick(tcr.FakePick):
    """The scoped fake pick, refusing also what the real pick refuses: `not_eligible_labels()` and the skip."""

    def __call__(self, label, skip, **kw):
        src = tca._mod("sources").GitHubSource(_config(self.base), run=self.fk)
        refuse, skip = set(src.not_eligible_labels()), {str(s) for s in skip}
        held = [n for n, i in self.fk.by.items() if n not in self.claims
                and (str(n) in skip or {x["name"] for x in i["labels"]} & refuse)]
        self.claims.extend(held)                     # leased for this call only: FakePick never offers a leased one
        try:
            return super().__call__(label, skip)
        finally:
            for n in held:
                self.claims.remove(n)


class Skipped(frozenset):
    """An injected sweep result that says the #2021 interval skipped it."""
    skipped = "interval"


def _goal_waits_on_two(labelled=True):
    """Goal 1 carries the blocked label and a `Blocked by` marker for member 2, an open goal."""
    return [tcau.goal(1, 2, labels=(BLOCKED,) if labelled else ()), tcau.goal(2)]


def _armed(tmp_path, issues, interval=None, **answers):
    ch, base, fk = tcau.scene(tmp_path, issues)
    tcau.answer_all(ch, base, fk, **answers)
    if interval is not None:
        _set_auto_unpark(base, mode="on", interval_minutes=interval)
    res = ch.arm(base, 1, run=fk, now=NOW)
    assert not any(a["result"] == "failed" for a in res["actions"]) and tca._rec(base).get("armed_at"), res
    fk.calls.clear()
    return ch, base, fk


def _label(fk, n):
    fk._set(n, fk._names(n) | {BLOCKED})


def _unlabel(fk, n):
    fk._set(n, fk._names(n) - {BLOCKED})


def _blk(base):
    return tca._rec(base).get("run") or {}


# --- 1 -- a freed cycle is running and the next one claims ------------------------------------------------------------

def test_a_cycle_whose_sweep_freed_a_member_is_running_and_the_next_cycle_claims_it(tmp_path, capsys):
    ch, base, fk = _armed(tmp_path, _goal_waits_on_two(), interval=0)
    pick = Pick(fk, base)
    first = ch.run(base, 1, pick=pick, now=NOW + 1, run=fk)
    assert first["picked"] == [2], first
    _close(fk, 2)
    capsys.readouterr()
    rc = ch.main(["chain.py", "run", base, "1"], run=fk, now=NOW + 2, pick=pick)       # the documented gesture
    out = capsys.readouterr().out
    assert rc == 0, out
    assert any(line.startswith(FREED) for line in out.splitlines()), out
    blk = _blk(base)
    assert blk["state"] == "running" and blk.get("picked") == [2], blk
    third = ch.run(base, 1, pick=pick, now=NOW + 3, run=fk)
    assert third["picked"] == [1], third


def test_a_lift_that_frees_no_claimable_member_is_not_running(tmp_path):
    """(a) the sweep lifts only 7, which is not a member, and the pick returns nothing: stuck. (b) the sweep lifts member
    2, which the person answered to keep blocked: stuck on `decision`, the kept wording. (c) the sweep frees member 1
    but the pick itself fails (degraded, timed out): stuck on the failure, never `running` (exit 0)."""
    ch, base, fk = _armed(tmp_path / "a", _goal_waits_on_two(labelled=False))
    res = ch.run(base, 1, pick=lambda label, skip, **kw: [], now=NOW + 1, run=fk,
                 sweep=lambda *a, **k: frozenset({"7"}))
    assert res["state"] == "stuck" and res["exit"] == 3 and "freed" not in res["detail"], ("a", res)

    for k, why in enumerate(("the pick was rate-limited or incomplete (DEGRADED, retry at epoch 0)",
                             "the pick exited 124: timed out")):
        ch, base, fk = _armed(tmp_path / ("c%d" % k), [tcau.goal(1)])
        _label(fk, 1)

        def lift(sdlc_dir, config, run, _fk=fk, **kw):
            _unlabel(_fk, 1)
            return frozenset({"1"})

        def pick(label, skip, _why=why, **kw):
            raise RuntimeError(_why)
        res = ch.run(base, 1, pick=pick, now=NOW + 1, run=fk, sweep=lift)
        assert res["state"] == "stuck" and res["exit"] == 3, ("c", why, res)
        assert res["reason"] in ("rate-limit", "degraded-read"), ("c", why, res)

    ch, base, fk = _armed(tmp_path / "b", [tcau.goal(1, 2), tcau.goal(2, labels=(BLOCKED,))],
                          **{"blocked:2": "keep it blocked"})
    res = ch.run(base, 1, pick=lambda label, skip, **kw: [], now=NOW + 1, run=fk,
                 sweep=lambda *a, **k: frozenset({"2"}))
    assert res["state"] == "stuck" and res["exit"] == 3 and res["reason"] == "decision", ("b", res)
    assert res["detail"].startswith("#2 ") and "you answered to keep it blocked" in res["detail"], ("b", res)


# --- 2 -- the interval-skipped sweep is forced once -------------------------------------------------------------------

def test_an_interval_skipped_sweep_is_forced_once_for_a_blocked_member_with_no_open_wait(tmp_path, monkeypatch):
    monkeypatch.delenv("SIGMA_RUN_ID", raising=False)
    ch, base, fk = _armed(tmp_path, _goal_waits_on_two())          # the default interval (10 minutes)
    _stamp(base)                                                    # swept just now, session unknown
    pick = Pick(fk, base)
    first = ch.run(base, 1, pick=pick, now=NOW + 1, run=fk)
    assert first["picked"] == [2], first                            # 1 still waits on open 2: nothing forced
    assert not _blk(base).get("forced"), _blk(base)
    _close(fk, 2)
    second = ch.run(base, 1, pick=pick, now=NOW + 2, run=fk)
    assert second["state"] == "running" and second["exit"] == 0 and second["picked"] == [], second
    assert second["detail"].startswith(FREED), second
    assert _blk(base).get("forced") == [1], _blk(base)
    third = ch.run(base, 1, pick=pick, now=NOW + 3, run=fk)
    assert third["picked"] == [1], third


def test_no_forced_sweep_without_a_trigger_and_never_twice_for_one_member(tmp_path):
    """A counting sweep that always says the interval skipped it. (a) no blocked member: one call per cycle. (b) a
    member labelled by hand after `arm`, which nothing lifts: one forced call in the first cycle, none in the second.
    (c) a member answered `keep it blocked`: never forced. (d) the (b) member, but a sweep that was NOT skipped by
    the interval (a plain set): never forced. (e) the (b) member, also in progress: never forced."""
    calls, mode = [], {}

    def sweep(sdlc_dir, config, run, force=False):
        calls.append(bool(force))
        return frozenset() if mode.get("case") == "d-not-skipped" else Skipped()

    def cycles(case):
        mode["case"] = case
        if case == "c-kept-blocked":
            ch, base, fk = _armed(tmp_path / case, [tcau.goal(1, 2), tcau.goal(2, labels=(BLOCKED,))],
                                  **{"blocked:2": "keep it blocked"})
        else:
            ch, base, fk = _armed(tmp_path / case, _goal_waits_on_two(labelled=False))
            if case != "a-no-blocked-member":
                _label(fk, 2)                                       # by hand, after arm: nothing lifts it
            if case == "e-in-progress":
                fk._set(2, fk._names(2) | {"sdlc:in-progress"})
        pick = Pick(fk, base)
        per_cycle = []
        for i in range(2):
            del calls[:]
            ch.run(base, 1, pick=pick, now=NOW + 1 + i, run=fk, sweep=sweep)
            per_cycle.append(list(calls))
        return per_cycle
    assert cycles("a-no-blocked-member") == [[False], [False]]
    assert cycles("c-kept-blocked") == [[False], [False]]
    assert cycles("b-labelled-after-arm") == [[False, True], [False]]
    assert cycles("d-not-skipped") == [[False], [False]]
    assert cycles("e-in-progress") == [[False], [False]]


def test_an_interval_skipped_sweep_that_lifted_nothing_keeps_its_signal(tmp_path, monkeypatch):
    """An interval-skipped sweep lifts nothing, so its result is empty, and falsy: the signal must survive that."""
    monkeypatch.delenv("SIGMA_RUN_ID", raising=False)
    ch, base, fk = _armed(tmp_path, _goal_waits_on_two(labelled=False))
    _stamp(base)
    got = ch._default_sweep(base, _config(base), fk)
    assert not got and getattr(got, "skipped", None) == "interval", (got, getattr(got, "skipped", None))
    _set_auto_unpark(base, mode="off")
    off = ch._default_sweep(base, _config(base), fk)
    assert not off and getattr(off, "skipped", None) is None
    _set_auto_unpark(base, mode="on")
    _label(fk, 2)
    _stamp(base)
    res = ch.run(base, 1, pick=Pick(fk, base), now=NOW + 1, run=fk)    # the real sweep, skipped, then forced
    assert _blk(base).get("forced") == [2], (_blk(base), res)


# --- 3 -- a root held only by the label --------------------------------------------------------------------------------

def test_a_root_held_only_by_the_label_stops_on_decision_naming_sigma_unpark(tmp_path, capsys):
    ch, base, fk = _armed(tmp_path, [tcau.goal(1)], interval=0)
    _label(fk, 1)                                                   # by hand, after arm, no recorded blocker
    capsys.readouterr()
    assert ch.main(["chain.py", "run", base, "1"], run=fk, now=NOW + 1, pick=Pick(fk, base)) == 3
    out = capsys.readouterr().out
    assert ("decision: " + LABEL_HELD % (1, 1)) in out, out
    assert ch.main(["chain.py", "status", base], run=fk, now=NOW + 2) == 0
    assert "/sigma-unpark #1" in capsys.readouterr().out


def _p(*rows):
    return {"members": [dict({"verdict": "armed", "waits_on": []}, **r) for r in rows], "outside": []}


def test_the_label_held_branch_keeps_the_persons_order():
    """(a) a parked root is named before a label-held one; (b) a blocked root in progress is `claimed`; (c) a blocked
    root with a prerequisite outside the chain is `external`; (e) a blocked root whose live `waiting_on` names an open
    issue is explained by that wait, not label-held; (f) so is one whose recorded `waits_on` names an open checkpoint
    outside the label (the checkpoint is the stop); (d) a label-held root is named before one held by a gate."""
    cf = tca._chain().chain_forecast
    got = cf.stuck_reason(_p({"number": 1, "verdict": "parked"}, {"number": 2, "blocked": True}))
    assert got == ("decision", "#1 is parked and needs an answer"), ("a", got)
    got = cf.stuck_reason(_p({"number": 2, "blocked": True, "in_progress": True}))
    assert got[0] == "claimed" and got[1].startswith("#2 "), ("b", got)
    got = cf.stuck_reason(_p({"number": 2, "blocked": True, "unresolved": ["acme/other#%d" % 4]}))
    assert got[0] == "external" and got[1].startswith("#2 "), ("c", got)
    got = cf.stuck_reason(_p({"number": 2, "blocked": True, "waiting_on": [7]}))
    assert got[0] != "decision" and "sigma-unpark" not in got[1], ("e", got)
    got = cf.stuck_reason({"members": [{"number": 2, "verdict": "armed", "waits_on": [5], "blocked": True}],
                           "outside": [{"number": 5, "verdict": "outside", "own_verdict": "checkpoint"}]})
    assert got[0] == "checkpoint" and got[1].startswith("#5 "), ("f", got)
    got = cf.stuck_reason(_p({"number": 3, "verdict": "held-by-gate"}, {"number": 2, "blocked": True}))
    assert got == ("decision", LABEL_HELD % (2, 2)), ("d", got)


# --- 4 -- the confirm read before a label-held stop -------------------------------------------------------------------

def test_a_label_the_picks_own_sweep_lifted_reads_freed_not_a_decision(tmp_path):
    ch, base, fk = _armed(tmp_path, [tcau.goal(1)])
    _label(fk, 1)

    def sweep(sdlc_dir, config, run, **kw):
        run(["api", "repos/acme/widget/issues/1", "--method", "GET"])     # the cycle's read cache now holds #1
        return frozenset()

    def pick(label, skip, **kw):
        _unlabel(fk, 1)                                                     # what the pick's own sweep does
        return []
    res = ch.run(base, 1, pick=pick, now=NOW + 1, run=fk, sweep=sweep)
    assert res["state"] == "running" and res["exit"] == 0 and res["detail"].startswith("#1 "), res


def _lagging(fk, n):
    """A runner whose chain-label listing still shows #n labelled blocked, while #n's own issue read does not."""
    def lagging(args):
        out = fk(args)
        params = gqlfake.rest_list_params([str(a) for a in args])
        if params is not None and "chain:1" in (params.get("labels") or ""):
            rows = json.loads(out)
            for r in rows:
                if r["number"] == n and BLOCKED not in {x["name"] for x in r["labels"]}:
                    r["labels"] = r["labels"] + [{"name": BLOCKED}]
            out = json.dumps(rows)
        return out
    return lagging


def test_a_confirm_read_free_counts_once_per_member_and_only_a_sweep_lift_moves_the_heartbeat(tmp_path):
    """The label listing lags: it still shows #1 labelled while its own issue read does not. The confirm read frees
    it once, without moving the heartbeat; the next cycle is not `running` again on the same lag. A real sweep lift,
    in a cycle where nothing closed, does move the heartbeat. A member the person answered `keep it blocked` about is
    never freed this way (the pick skips it whatever its label says)."""
    ch, base, fk = _armed(tmp_path / "lag", [tcau.goal(1)])
    lagging = _lagging(fk, 1)
    empty = (lambda label, skip, **kw: [])
    first = ch.run(base, 1, pick=empty, now=NOW + 1, run=lagging, sweep=lambda *a, **k: frozenset())
    assert first["state"] == "running" and first["exit"] == 0 and first["detail"].startswith("#1 "), first
    assert _blk(base).get("heartbeat") != ch._iso(NOW + 1), _blk(base)
    second = ch.run(base, 1, pick=empty, now=NOW + 2, run=lagging, sweep=lambda *a, **k: frozenset())
    assert second["state"] == "stuck" and second["exit"] == 3, second
    assert _blk(base).get("heartbeat") != ch._iso(NOW + 2), _blk(base)

    ch2, base2, fk2 = _armed(tmp_path / "lift", [tcau.goal(1)])
    _label(fk2, 1)

    def lift(sdlc_dir, config, run, **kw):
        _unlabel(fk2, 1)
        return frozenset({"1"})
    res = ch2.run(base2, 1, pick=Pick(fk2, base2), now=NOW + 1, run=fk2, sweep=lift)
    assert res["state"] == "running" and res["closed"] == [] and res["detail"].startswith(FREED), res
    assert _blk(base2).get("heartbeat") == ch2._iso(NOW + 1), _blk(base2)

    ch3, base3, fk3 = _armed(tmp_path / "kept", [tcau.goal(1, 2), tcau.goal(2, labels=(BLOCKED,))],
                             **{"blocked:2": "keep it blocked"})
    _unlabel(fk3, 2)                                                    # its own read: gone; the listing: still there
    res = ch3.run(base3, 1, pick=empty, now=NOW + 1, run=_lagging(fk3, 2), sweep=lambda *a, **k: frozenset())
    assert res["state"] == "stuck" and res["reason"] == "decision", res
    assert res["detail"].startswith("#2 ") and "you answered to keep it blocked" in res["detail"], res


def test_a_confirm_read_that_fails_is_still_labelled(tmp_path):
    """The pick's own sweep lifted #1's label, but the confirm read of #1 fails: still labelled, `decision`."""
    ch, base, fk = _armed(tmp_path, [tcau.goal(1)])
    _label(fk, 1)
    after = {}

    def runner(args):
        if after and [str(a) for a in args] == ["api", "repos/acme/widget/issues/1", "--method", "GET"]:
            raise RuntimeError("HTTP 502 Bad Gateway")
        return fk(args)

    def pick(label, skip, **kw):
        _unlabel(fk, 1)
        after["picked"] = True
        return []
    res = ch.run(base, 1, pick=pick, now=NOW + 1, run=runner, sweep=lambda *a, **k: frozenset())
    assert res["state"] == "stuck" and res["exit"] == 3, res
    assert res["reason"] == "decision" and res["detail"] == LABEL_HELD % (1, 1), res


def test_the_confirm_read_reads_at_most_seven_roots(tmp_path):
    """Nine label-held roots: the confirm read re-reads only the first seven (1 + MAX_OTHER_ROOTS), each once."""
    nums = list(range(2, 11))
    ch, base, fk = _armed(tmp_path, [tcau.goal(1, *nums)] + [tcau.goal(n) for n in nums])
    for n in nums:
        _label(fk, n)
    res = ch.run(base, 1, pick=lambda label, skip, **kw: [], now=NOW + 1, run=fk, sweep=lambda *a, **k: frozenset())
    assert res["state"] == "stuck" and res["reason"] == "decision", res
    reads = {n: sum(1 for c in fk.calls if [str(a) for a in c] == ["api", "repos/acme/widget/issues/%d" % n,
                                                                    "--method", "GET"]) for n in nums}
    assert sorted(n for n in nums if reads[n] >= 2) == nums[:7] and max(reads.values()) <= 2, reads

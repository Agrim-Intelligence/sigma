"""#2065: `/sigma-goal <N> --land` -- one named goal, unattended, to a merged PR.

The skill's selection is `loop.py next .sdlc --land <N>`: the loop's OWN picker, confined to one
issue, so N is taken only when `/sigma-loop` would take it and no other goal is picked or written to
by the pick. These tests hold that picker scope (the code half), the landing gestures with GitHub
GraphQL refused (the cloud half), and the skill prose that routes to both (the contract half).

A substring proves a sentence is in a file, never that an agent obeys it; so the prose tests below
also re-derive what the prose CITES from the source it lives in (tests/test_goal_scope.py's rule).
"""
import json
import pathlib
import re
import sys
import tempfile

import pytest

import gqlfake
from test_feature_scope import _mod, _issue, _gh, _base, _is_rest_issues_call, _rest_field

ROOT = pathlib.Path(__file__).resolve().parent.parent
N = "10"
WORK_LAND = {"work": {"enabled": True, "auto_merge": "always"}}


@pytest.fixture(autouse=True)
def _no_cloud_env(monkeypatch):
    """A cloud shell running the suite must not flip the GraphQL branches; a test that wants it sets it."""
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.delenv("SIGMA_GH_GRAPHQL", raising=False)


def _rest(i):
    """A fake issue in REST shape, for the one single-issue GET the scoped pick makes."""
    out = {"number": int(i["number"]), "title": i.get("title"), "state": i.get("rest_state", "open"),
           "labels": i["labels"], "assignees": i.get("assignees") or [], "body": i.get("body") or ""}
    if i.get("is_pr"):
        out["pull_request"] = {"url": "u"}
    return out


def _fake(issues, calls=None):
    """`test_feature_scope._gh` (label-honouring list reads, GraphQL label swaps), plus the REST
    single-issue GET answered from `issues` with their real state -- `_gh`'s own reads every issue
    as OPEN, which would make a closed N indistinguishable from an open one."""
    calls = calls if calls is not None else []
    inner = _gh(issues, calls)
    by = {str(i["number"]): i for i in issues}

    def run(args):
        args = list(args)
        t = gqlfake.rest_issue_target(args)
        if t is not None and not t[1] and t[0] in by:
            calls.append(args)
            return json.dumps(_rest(by[t[0]]))
        return inner(args)
    run.calls = calls
    return run


def _source(lp, base, issues, calls=None, land=N):
    cfg = lp.state.load_config(base)
    src = lp.sources.GitHubSource(cfg, run=_fake(issues, calls), sdlc_dir=base)
    src._BACKLOG_READ_RETRY_BASE = 0
    if land:
        lp.sources.scope_to_land(src, land)
    return cfg, src


def _single_reads(calls, n=N):
    return [c for c in calls if gqlfake.rest_issue_target(c) == (str(n), False)]


def _writes(calls):
    """Every call that could change GitHub: a GraphQL mutation, or a REST call whose method is not GET."""
    out = []
    for c in calls:
        text = " ".join(map(str, c))
        method = c[c.index("--method") + 1] if "--method" in c else "GET"
        if "mutation" in text or str(method).upper() != "GET" or " -X " in f" {text} ":
            out.append(c)
    return out


def _names(c, n):
    text = " ".join(map(str, c))
    return f"/issues/{n}/" in text or text.endswith(f"/issues/{n}") or f'"{n}"' in text or f"number={n}" in text


# --------------------------------------------------------------------------- the scoped pick

def test_the_named_goal_is_picked_over_an_older_pickable_one():
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(5), _issue(10)])
        assert lp._next(base, src, cfg) == ("goal", N)
        assert src.next_pending(skip={N}) is None        # exclusive: no fallback to the older #5


def test_the_unscoped_control_picks_the_older_goal():
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(5), _issue(10)], land=None)
        assert lp._next(base, src, cfg) == ("goal", "5")


def test_a_scoped_next_pending_reads_one_issue_and_never_the_backlog_list():
    """The 200-issue list window does not bound a named goal, and one GET does not grow with the backlog."""
    lp = _mod("loop")
    calls = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(5), _issue(10)], calls)
        assert src.next_pending() == N
    assert not [c for c in calls if _is_rest_issues_call(c)], calls
    assert len(_single_reads(calls)) == 1, calls


def _parked(n):
    i = _issue(n, "sdlc:parked")
    return i


def _no_goal_label(n):
    i = _issue(n)
    i["labels"] = []
    return i


def _closed(n):
    i = _issue(n)
    i["rest_state"] = "closed"
    return i


def _pr(n):
    i = _issue(n)
    i["is_pr"] = True
    return i


NOT_TAKEN = [
    ("closed", _closed, "closed"),
    ("no-goal-label", _no_goal_label, "not labelled sdlc:goal"),
    ("parked", _parked, "carries sdlc:parked"),
    ("in-progress", lambda n: _issue(n, "sdlc:in-progress"), "carries sdlc:in-progress"),
    ("pull-request", _pr, "pull request"),
]


@pytest.mark.parametrize("name,make,why", NOT_TAKEN, ids=[r[0] for r in NOT_TAKEN])
def test_a_goal_the_loop_would_not_pick_is_not_taken_and_nothing_is_written(name, make, why, capsys):
    lp = _mod("loop")
    calls = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(5), make(10)], calls)
        assert lp._next(base, src, cfg) == ("DONE", None)
    err = capsys.readouterr().err
    line = next((l for l in err.splitlines() if "--land #10 not taken" in l), "")
    assert "(not started)" in line and why in line, err
    assert _writes(calls) == [], _writes(calls)


def test_a_goal_in_the_skip_set_is_not_taken_and_says_so(capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10)])
        assert lp._next(base, src, cfg, extra_skip={N}) == ("DONE", None)
    line = next(l for l in capsys.readouterr().err.splitlines() if "--land #10 not taken" in l)
    assert "skip set" in line, line


def test_a_goal_assigned_to_someone_else_is_not_taken(capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, discovery={"source": "github", "github": {"repo": "acme/widget", "assignee": "alice"}})
        cfg, src = _source(lp, base, [_issue(10, assignees=[{"login": "bob"}])])
        assert lp._next(base, src, cfg) == ("DONE", None)
    line = next(l for l in capsys.readouterr().err.splitlines() if "--land #10 not taken" in l)
    assert "not assigned to alice" in line, line


def test_an_open_prerequisite_holds_the_named_goal(capsys):
    lp = _mod("loop")
    calls = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(3), _issue(10, body="**Blocked by:** #3\n")], calls)
        assert lp._next(base, src, cfg) == ("DONE", None)
    line = next(l for l in capsys.readouterr().err.splitlines() if "--land #10 not taken" in l)
    assert "(not started)" in line and "#3" in line, line
    assert _writes(calls) == [], _writes(calls)


def test_a_goal_whose_claim_lock_is_held_is_not_taken_and_says_why(capsys):
    """Two containers on one checkout racing for N: the loser ends at once with the real reason, not
    a re-read that would report "skip set"."""
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10)])
        held = lp._try_acquire_claim_lock(base, N)
        assert held not in (None, lp._LOCK_UNAVAILABLE), "flock unavailable here; this test proves nothing"
        try:
            assert lp._next(base, src, cfg) == ("DONE", None)
        finally:
            lp._release_claim_lock(held)
    line = next(l for l in capsys.readouterr().err.splitlines() if "--land #10 not taken" in l)
    assert "claiming it this instant" in line, line


def test_a_served_goal_other_than_n_is_touched_by_nothing(monkeypatch, capsys):
    """The backstop sits before the hold, the lease, the lock and the attach: a source that served
    another goal (a bug, by construction) gets no lock, no slot and no write on that goal."""
    lp = _mod("loop")
    calls = []
    slots = []
    monkeypatch.setattr(lp, "_reserve_goal_slot", lambda *a, **k: slots.append(a) or None)
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10), _issue(12)], calls)
        src.next_pending = lambda skip=(): "12"
        assert lp._next(base, src, cfg) == ("DONE", None)
        fd = lp._try_acquire_claim_lock(base, "12")
        assert fd is not None
        lp._release_claim_lock(fd)
    assert slots == [] and [c for c in _writes(calls) if _names(c, 12)] == []
    line = next(l for l in capsys.readouterr().err.splitlines() if "--land #10 not taken" in l)
    assert "#12" in line, line


def test_a_read_that_answers_with_another_number_is_not_served(capsys):
    """A transferred issue's GET can be redirected; the answer's number must be the one asked for."""
    lp = _mod("loop")
    calls = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg = lp.state.load_config(base)
        inner = _fake([_issue(10), _issue(12)], calls)

        def run(args):
            if gqlfake.rest_issue_target(list(args)) == (N, False):
                calls.append(list(args))
                return json.dumps(_rest(_issue(12)))
            return inner(args)
        src = lp.sources.GitHubSource(cfg, run=run, sdlc_dir=base)
        src._BACKLOG_READ_RETRY_BASE = 0
        lp.sources.scope_to_land(src, N)
        assert lp._next(base, src, cfg) == ("DONE", None)
    line = next(l for l in capsys.readouterr().err.splitlines() if "--land #10 not taken" in l)
    assert "returned #12" in line, line
    assert _writes(calls) == [], _writes(calls)


def test_a_goal_another_live_claim_holds_is_not_taken(monkeypatch, capsys):
    lp = _mod("loop")
    monkeypatch.setattr(lp, "_lease", lambda *a, **k: ("me", "me-w", {N: ("bob", "bob-w")}))
    monkeypatch.setattr(lp.ledger, "claim_belongs_to_me", lambda *a, **k: False)
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10)])
        assert lp._next(base, src, cfg) == ("DONE", None)
    line = next(l for l in capsys.readouterr().err.splitlines() if "--land #10 not taken" in l)
    assert "another live claim holds it (bob)" in line, line


def test_a_failed_read_is_degraded_never_done_and_says_so(capsys):
    """#2023's rule holds under the scope: a read that gave up is `DEGRADED <epoch>`, never `DONE`."""
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg = lp.state.load_config(base)
        inner = _fake([_issue(10)])

        def run(args):
            if gqlfake.rest_issue_target(list(args)) == (N, False):
                raise RuntimeError("HTTP 404: Not Found")
            return inner(args)
        src = lp.sources.GitHubSource(cfg, run=run, sdlc_dir=base)
        src._BACKLOG_READ_RETRY_BASE = 0
        lp.sources.scope_to_land(src, N)
        kind, epoch = lp._next(base, src, cfg)
        assert kind == "DEGRADED" and isinstance(epoch, int), (kind, epoch)
    line = next(l for l in capsys.readouterr().err.splitlines() if "--land #10 not taken" in l)
    assert "read failed" in line, line


def test_a_filtered_out_goal_costs_no_retry_sleep(monkeypatch, capsys):
    """One successful read is the answer: the backlog's retry-on-empty (1s, then 2s) does not apply."""
    lp = _mod("loop")
    slept = []
    monkeypatch.setattr(lp.sources.time, "sleep", lambda s: slept.append(s))
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_parked(10)])
        src._BACKLOG_READ_RETRY_BASE = 5
        assert src.next_pending() is None
    assert slept == []
    assert "came back empty" not in capsys.readouterr().err


def test_a_failed_read_is_still_retried():
    """Only a successful read is memoised: a transient failure re-reads, as the backlog read does."""
    lp = _mod("loop")
    seen = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg = lp.state.load_config(base)
        inner = _fake([_issue(10)])

        def run(args):
            if gqlfake.rest_issue_target(list(args)) == (N, False):
                seen.append(1)
                if len(seen) == 1:
                    raise RuntimeError("HTTP 502: Bad Gateway")
            return inner(args)
        src = lp.sources.GitHubSource(cfg, run=run, sdlc_dir=base)
        src._BACKLOG_READ_RETRY_BASE = 0
        lp.sources.scope_to_land(src, N)
        assert src.next_pending() == N
    assert len(seen) == 2


# --- the board ---------------------------------------------------------------------------------------

def _boarded(lp, base, issues, board):
    cfg, src = _source(lp, base, issues)
    src._board_queue = lambda: board
    return cfg, src


def test_with_a_board_an_uncarded_named_goal_is_taken_not_the_ready_card():
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _boarded(lp, base, [_issue(7), _issue(10)], ([7], {7}))
        assert src.next_pending() == N


def test_with_a_board_a_named_goal_carded_outside_ready_is_not_taken(capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _boarded(lp, base, [_issue(7), _issue(10)], ([7], {7, 10}))
        assert lp._next(base, src, cfg) == ("DONE", None)
    line = next(l for l in capsys.readouterr().err.splitlines() if "--land #10 not taken" in l)
    assert "Ready lane" in line, line


def test_with_a_board_a_named_goal_in_ready_is_taken():
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _boarded(lp, base, [_issue(7), _issue(10)], ([7, 10], {7, 10}))
        assert src.next_pending() == N


# --- no sweep writes to another goal -----------------------------------------------------------------

SKIPPED_SWEEPS = ["_dag_first_pick", "_reconcile_sweep", "_auto_unpark_sweep", "_auto_reclaim_stale_claims",
                  "_release_held_unstarted", "_reconcile_awaiting_merges"]


def _spy_sweeps(lp, monkeypatch):
    hits = []
    for name in SKIPPED_SWEEPS:
        monkeypatch.setattr(lp, name, lambda *a, _n=name, **k: hits.append(_n) or frozenset())
    return hits


@pytest.mark.parametrize("sweep", SKIPPED_SWEEPS)
def test_a_sweep_that_writes_to_other_goals_does_not_run_under_the_scope(sweep, monkeypatch):
    lp = _mod("loop")
    hits = _spy_sweeps(lp, monkeypatch)
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10)])
        assert lp._next(base, src, cfg) == ("goal", N)
    assert sweep not in hits


@pytest.mark.parametrize("sweep", SKIPPED_SWEEPS)
def test_the_unscoped_control_runs_each_sweep(sweep, monkeypatch):
    lp = _mod("loop")
    hits = _spy_sweeps(lp, monkeypatch)
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10)], land=None)
        assert lp._next(base, src, cfg) == ("goal", N)
    assert sweep in hits


def test_the_needs_label_sweeps_run_restricted_to_the_named_goal(monkeypatch):
    """They write only to N under the scope, so a re-run after a person creates the label heals itself."""
    lp = _mod("loop")
    seen = {}
    monkeypatch.setattr(lp.feature_labels, "resume_needs_label",
                        lambda *a, **k: seen.setdefault("label", k.get("only")) and [])
    monkeypatch.setattr(lp.feature_labels, "resume_needs_unit",
                        lambda *a, **k: seen.setdefault("unit", k.get("only")) and [])
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10)])
        lp._next(base, src, cfg)
    assert seen == {"label": N, "unit": N}


def test_an_unscoped_pick_passes_no_only_to_the_needs_sweeps(monkeypatch):
    lp = _mod("loop")
    seen = []
    monkeypatch.setattr(lp.feature_labels, "resume_needs_label", lambda *a, **k: seen.append(k) or [])
    monkeypatch.setattr(lp.feature_labels, "resume_needs_unit", lambda *a, **k: seen.append(k) or [])
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10)], land=None)
        lp._next(base, src, cfg)
    assert seen == [{}, {}], seen


def test_resume_needs_label_only_touches_the_named_goal():
    fl = _mod("feature_labels")
    cleared, noted = [], []

    class Src:
        needs_label_label = "sdlc:needs-label"

        def list_needs_label(self):
            return [{"number": 10, "body": "", "labels": []}, {"number": 12, "body": "", "labels": []}]

        def clear_needs_label(self, goal):
            cleared.append(goal)
            return True

        def note(self, goal, text):
            noted.append(goal)

        def label_exists(self, name):
            return True

        def __getattr__(self, name):          # the remaining surface `_has_surface` asks for
            return lambda *a, **k: None
    fl._still_held = lambda source, issue: (False, "its label now exists")
    with tempfile.TemporaryDirectory() as d:
        released = fl.resume_needs_label(d, Src(), {}, only=N)
    assert cleared == [N] and noted == [N] and released == [N]


def test_the_dag_change_check_runs_with_a_refresh_that_does_nothing(monkeypatch):
    """`refresh-requested` would force a refresh, and a refresh posts cycle comments on other goals."""
    lp = _mod("loop")
    got = {}

    class DagCheck:
        @staticmethod
        def check(sdlc, config, run=None, refresh=None):
            got["refresh"] = refresh
            return {"status": "refresh-requested", "detail": ""}
    monkeypatch.setattr(lp, "_load", lambda name: DagCheck if name == "dag_check" else _mod(name))
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        (pathlib.Path(base) / "state" / "dag.json").write_text("{}")
        cfg, src = _source(lp, base, [_issue(10)])
        lp._dag_check_pick(base, cfg, src)
        assert callable(got["refresh"])
        assert got["refresh"](base, cfg, run=None, force=True) is None
        got.clear()
        src.land_scope = None
        lp._dag_check_pick(base, cfg, src)
        assert got["refresh"] is None                 # unscoped: the real refresh, as before


def test_a_pass_over_at_attach_releases_the_lock_and_the_slot_before_returning(monkeypatch, capsys):
    """The early return must come after the refunds, or the lock and the session slot leak."""
    lp = _mod("loop")
    order = []
    real_cancel, real_release = lp._cancel_goal_slot, lp._release_claim_lock
    monkeypatch.setattr(lp, "_cancel_goal_slot", lambda *a, **k: order.append("cancel") or real_cancel(*a, **k))
    monkeypatch.setattr(lp, "_release_claim_lock", lambda *a, **k: order.append("release") or real_release(*a, **k))
    monkeypatch.setattr(lp, "_owner_ok_at_pick", lambda *a, **k: False)
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10)])
        assert lp._next(base, src, cfg) == ("DONE", None)
        fd = lp._try_acquire_claim_lock(base, N)
        assert fd is not None
        lp._release_claim_lock(fd)
    assert order[:2] == ["cancel", "release"], order
    line = next(l for l in capsys.readouterr().err.splitlines() if "--land #10 not taken" in l)
    assert "(not started)" in line and "owner" in line and "may have left a hold" in line, line


def test_only_the_named_goal_is_written_to_with_real_sweeps_primed(monkeypatch):
    """End to end: another goal M sits awaiting merge in this checkout and its PR has merged, so the
    merge-reconcile sweep would close it. Scoped, no write names M; unscoped, M is written."""
    lp = _mod("loop")
    results = {}
    for land in (N, None):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            base = _base(d, work={"enabled": True})
            lp.work._save(base, "12", {"worktree": d, "branch": "sdlc/12", "base": "main",
                                        "remote": "origin", "pr": "70"})
            lp.work.mark_awaiting_merge(base, "12")
            monkeypatch.setattr(lp.work, "pr_landing_state", lambda *a, **k: (lp.work.MERGED, ""))
            monkeypatch.setattr(lp.work, "done_refusal", lambda *a, **k: None)
            cfg, src = _source(lp, base, [_issue(10), _issue(12, "sdlc:in-progress")], calls, land=land)
            reconciled = []
            monkeypatch.setattr(lp, "_record", lambda sd, s, goal, *a, **k: reconciled.append(str(goal)), raising=False)
            lp._next(base, src, cfg)
            results[land] = ([c for c in _writes(calls) if _names(c, 12)], reconciled)
    scoped_writes, scoped_records = results[N]
    assert scoped_writes == [] and "12" not in scoped_records, results[N]
    unscoped_writes, unscoped_records = results[None]
    assert unscoped_writes or "12" in unscoped_records, "the control must show the sweep writes to M"


# --- the CLI -----------------------------------------------------------------------------------------

def _cli_base(d, **extra):
    extra.setdefault("work", WORK_LAND["work"])
    return _base(d, **extra)


REFUSALS = [
    ("bare", ["--land"], {}, "needs an issue number"),
    ("non-numeric", ["--land", "abc"], {}, "needs an issue number"),
    ("with-feature", ["--land", N, "--feature", "voice"], {}, "--feature"),
    ("with-label", ["--land", N, "--label", "run-1"], {}, "--label"),
    ("work-off", ["--land", N], {"work": {"enabled": False, "auto_merge": "always"}}, "work.enabled"),
    ("auto-merge-off", ["--land", N], {"work": {"enabled": True}}, "auto_merge"),
]


@pytest.mark.parametrize("name,flags,cfg,needle", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_a_refusal_exits_two_with_one_line_and_makes_no_call(name, flags, cfg, needle, monkeypatch, capsys):
    lp = _mod("loop")
    seen = []
    with tempfile.TemporaryDirectory() as d:
        base = _cli_base(d, **cfg)
        monkeypatch.setattr(lp.sources, "_run_gh", lambda a, binary="gh": seen.append(a) or "[]")
        assert lp.main(["loop.py", "next", base] + flags) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and needle in err[0], err
    assert seen == []


def test_next_batch_refuses_land(monkeypatch, capsys):
    lp = _mod("loop")
    seen = []
    with tempfile.TemporaryDirectory() as d:
        base = _cli_base(d)
        monkeypatch.setattr(lp.sources, "_run_gh", lambda a, binary="gh": seen.append(a) or "[]")
        assert lp.main(["loop.py", "next-batch", base, "--land", N]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "next-batch" in err[0] and seen == [], err


def test_a_local_goals_backlog_is_refused(capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _cli_base(d, discovery={"source": "local-goals"})
        (pathlib.Path(base) / "goals").mkdir()
        assert lp.main(["loop.py", "next", base, "--land", N]) == 2
    assert "local-goals" in capsys.readouterr().err


def test_the_cli_prints_the_named_goal(monkeypatch, capsys):
    lp = _mod("loop")
    inner = _fake([_issue(5), _issue(10)])
    monkeypatch.setattr(lp.sources, "_run_gh", lambda a, binary="gh": inner(a))
    monkeypatch.setattr(lp.sources.GitHubSource, "_BACKLOG_READ_RETRY_BASE", 0)
    with tempfile.TemporaryDirectory() as d:
        base = _cli_base(d)
        assert lp.main(["loop.py", "next", base, "--land", "#10"]) == 0
    assert capsys.readouterr().out.split() == [N]


def test_the_value_survives_flag_parsing_and_usage_documents_it(capsys):
    lp = _mod("loop")
    assert lp._flags(["--land", N, "--session-pid", "1"]) == {"land": N, "session-pid": "1"}
    # a known value flag consumes its value unconditionally (#541), so a stray `--x` is refused as
    # "not an issue number" rather than silently re-read as `--land` alone plus a second flag
    assert lp._flags(["--land", "--x"]) == {"land": "--x"}
    assert lp.main(["loop.py"]) == 2
    assert "--land" in capsys.readouterr().err


# --- the pick in a cloud session ---------------------------------------------------------------------

def test_the_pick_claims_over_rest_with_every_graphql_call_refused(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    lp = _mod("loop")
    calls = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg = lp.state.load_config(base)
        inner = _fake([_issue(5), _issue(10)], calls)

        def run(args):
            if _graphql_shaped(" ".join(map(str, args))):
                calls.append(list(args))
                raise RuntimeError("HTTP 403: GitHub GraphQL is not available from Claude Code sessions")
            return inner(args)
        src = lp.sources.GitHubSource(cfg, run=run, sdlc_dir=base)
        src._BACKLOG_READ_RETRY_BASE = 0
        src._CLAIM_LABEL_RETRY_BASE = 0
        lp.sources.scope_to_land(src, N)
        assert lp._next(base, src, cfg) == ("goal", N)
    rest_label_writes = [c for c in _writes(calls) if "graphql" not in c and _names(c, 10)]
    assert rest_label_writes, calls
    assert not [c for c in _writes(calls) if "graphql" not in c and _names(c, 5)]


# --- the landing gestures in a cloud session (#2065: "confirm with the existing fakes") ---------------

import prfake                         # noqa: E402
import test_work as tw                # noqa: E402  (_sdlc, _started, _evidence, _runner, _post_review, ...)

GQL_REFUSED = RuntimeError("HTTP 403: GitHub GraphQL is not available from Claude Code sessions")
LAND_CFG = {"work": {"enabled": True, "auto_merge": "always", "require_review": "approval"},
            "discovery": {"source": "github", "github": {"repo": "acme/app"}}}
GOAL = "42"


def _graphql_shaped(line):
    return (line.startswith(("gh pr ", "gh issue ", "gh repo view", "gh project ")) or "graphql" in line
            or line.startswith(("pr ", "issue ", "repo view", "project ")))


class CloudGitHub:
    """ONE stateful fake GitHub behind every landing gesture, so what one step writes is what the next
    step reads: the PR `work.pr` opens, the comment `work.post_review` posts (served back to the
    review gate with `author_association: OWNER`), and the merge the PUT lands (served back to
    `record done`'s REST read). Every GraphQL-shaped argv RAISES, as the cloud proxy refuses it.
    `work_run` takes `(cwd, argv)` (work.py's runner), `source_run` takes `args` (sources.py's)."""

    def __init__(self, post=True):
        self.post = post
        self.comments, self.merged, self.calls, self.refused = [], False, [], []
        self.static = tw._runner(
            [("rev-list", "2"), ("pulls?head=", ""), ("log -1", "fix: land it"),
             ("log --reverse", "fix: land it\n\nbody\n\x00"), ("pulls -f title=", "7")]
            + tw._view() + tw._perm() + tw.UNPROTECTED + tw._ruleset(checks=("ci",), reviews=1)
            + [(prfake.reviews_get(7), "[]"), (tw.LINE_COMMENTS_GET, "[]")])

    def work_run(self, cwd, argv):
        line = " ".join(map(str, argv))
        self.calls.append(line)
        if _graphql_shaped(line):
            self.refused.append(line)
            raise GQL_REFUSED
        if tw._posted(line):
            if not self.post:
                raise RuntimeError("HTTP 422: dropped by the control")
            self.comments.append(next(str(a)[5:] for a in argv if str(a).startswith("body=")))
            return tw._comment_reply(99)
        if prfake.comments_get(7) in line:
            return prfake.rest_comments([{"body": b, "author": {"login": "owner"}, "authorAssociation": "OWNER"}
                                         for b in self.comments])
        if prfake.merge_put(7) in line:
            self.merged = True
            return prfake.rest_merged()
        if self.merged and tw.PULLS_7 in line:
            return tw._rest_pr(merged=True, state="closed")
        return self.static(cwd, argv)

    def source_run(self, args):
        line = " ".join(map(str, args))
        self.calls.append("gh " + line)
        if _graphql_shaped(line):
            self.refused.append(line)
            raise GQL_REFUSED
        if gqlfake.rest_issue_target(list(args)) == (GOAL, False):
            return json.dumps({"number": int(GOAL), "state": "open", "labels": [{"name": "sdlc:goal"},
                               {"name": "sdlc:in-progress"}], "assignees": [], "body": ""})
        return ""


def _land_world(tmp_path, monkeypatch, post=True):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    gh = CloudGitHub(post=post)
    d = tw._sdlc(tmp_path, LAND_CFG)
    goal = tw._started(d, goal=GOAL, pr="")
    tw._evidence(d, goal)
    return d, goal, gh


def test_one_goal_lands_over_rest_with_every_graphql_call_refused(tmp_path, monkeypatch):
    """PR open -> post-PR review `sigma:approve` -> merge under `require_review: approval` -> `record
    done`, each step reading what the one before wrote. NOT proved here: the real cloud proxy, the
    commit and verify steps, and an agent following the skill's prose."""
    d, goal, gh = _land_world(tmp_path, monkeypatch)
    opened = tw.work.pr(d, LAND_CFG, goal, run=gh.work_run)
    assert str(tw.work._record(d, goal).get("pr")) == "7", opened
    posted = tw._post_review(d, LAND_CFG, goal, gh.work_run, "approve")
    assert posted.startswith("posted sigma:approve") and gh.comments, posted
    merged = tw.work.merge(d, LAND_CFG, goal, run=gh.work_run, sleep=tw.NOSLEEP)
    assert merged.startswith("PR #7 merged"), merged
    assert sum(prfake.merge_put(7) in c for c in gh.calls) == 1

    lp = _mod("loop")
    monkeypatch.setattr(lp.work, "_run", gh.work_run)
    monkeypatch.setattr(lp.sources, "get_source", lambda sdlc, cfg=None, **k: _cloud_source(lp, sdlc, gh))
    monkeypatch.setattr(lp.sources, "_run_gh", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unpatched gh seam")))
    assert lp.main(["loop.py", "record", d, goal, "done"]) == 0
    assert not [c for c in gh.calls if c.startswith("gh pr merge") or "--auto" in c.split()]
    assert gh.refused, "a GraphQL attempt must have been refused and survived, or nothing was proved"
    assert any(f"issues/{GOAL}" in c and "state=closed" in c for c in gh.calls), gh.calls
    assert any(f"issues/{GOAL}/labels/sdlc%3Ain-progress --method DELETE" in c for c in gh.calls), gh.calls


def _cloud_source(lp, sdlc, gh):
    src = lp.sources.GitHubSource(lp.state.load_config(sdlc), run=gh.source_run, sdlc_dir=sdlc)
    src._CLAIM_LABEL_RETRY_BASE = 0
    return src


def test_the_control_without_the_approval_comment_parks_and_never_merges(tmp_path, monkeypatch):
    d, goal, gh = _land_world(tmp_path, monkeypatch, post=False)
    tw.work.pr(d, LAND_CFG, goal, run=gh.work_run)
    tw._post_review(d, LAND_CFG, goal, gh.work_run, "approve")
    merged = tw.work.merge(d, LAND_CFG, goal, run=gh.work_run, sleep=tw.NOSLEEP)
    assert merged.startswith("PARK:") and "not approved" in merged, merged
    assert not any(prfake.merge_put(7) in c for c in gh.calls)


# --- the prose contract ------------------------------------------------------------------------------

SKILL_DIR = ROOT / "skills" / "sigma-goal"
SKILL = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
LAND_PATH = SKILL_DIR / "references" / "land.md"
LAND = LAND_PATH.read_text(encoding="utf-8") if LAND_PATH.is_file() else ""
PICKING = (ROOT / "skills" / "sigma-loop" / "references" / "picking.md").read_text(encoding="utf-8")
LOOP_SKILL = (ROOT / "skills" / "sigma-loop" / "SKILL.md").read_text(encoding="utf-8")
LANDING = (ROOT / "skills" / "sigma-loop" / "references" / "landing.md").read_text(encoding="utf-8")
LOOP_PY = (ROOT / "skills" / "sigma-loop" / "scripts" / "loop.py").read_text(encoding="utf-8")
CLOUD = (ROOT / "docs" / "cloud-sessions.md").read_text(encoding="utf-8")
RUNNING = (ROOT / "docs" / "running-the-loop.md").read_text(encoding="utf-8")
LOOP_CMD = 'python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/loop.py"'

#: THE ONE WORDING, quoted by both skills (tests/test_goal_scope.py's rule for `--feature`).
SENTENCE = "While a `--land` run is active, no goal other than the one it names may be picked, and no other goal is written to."


def _flat(text):
    return re.sub(r"\s+", " ", text)


@pytest.mark.parametrize("name,text", [("sigma-goal SKILL.md", SKILL), ("sigma-loop picking.md", PICKING)])
def test_both_skills_state_the_one_exclusivity_sentence_and_its_one_exception(name, text):
    flat = _flat(text)
    assert _flat(SENTENCE) in flat, f"{name} must quote the --land contract sentence verbatim"
    after = flat.split(_flat(SENTENCE), 1)[1][:700]
    assert "ledger watcher" in after, f"{name}: the watcher exception must sit directly under the sentence"


def test_step_one_routes_land_where_selection_happens():
    step_one = SKILL.split("2. **Recall first**")[0]
    assert "1. Identify the goal" in step_one, "the step-1 anchor moved; this test is now vacuous"
    assert "`--land`" in step_one and "references/land.md" in step_one


@pytest.mark.parametrize("anchor", [
    "Drive a single goal through the SDLC, with the user in the loop",
    "the only behavioral difference",
    "Then drive the phases, pausing for the user at each gate",
    "Unlike `/sigma-loop`, you do NOT auto-proceed past checkpoints",
], ids=["intro", "behavioral-difference", "drive-the-phases", "unlike-the-loop"])
def test_every_always_pause_sentence_is_qualified_for_land(anchor):
    flat = _flat(SKILL)
    assert anchor in flat, f"anchor moved: {anchor!r}"
    window = flat.split(anchor, 1)[1][:260]
    assert "--land" in window, f"{anchor!r} still reads as always pausing; qualify it for --land"


def test_land_reference_exists_and_is_linked():
    assert LAND, "skills/sigma-goal/references/land.md is missing"
    assert "](references/land.md)" in SKILL


def test_selection_is_the_scoped_picker_between_start_and_session_end():
    flat = _flat(LAND)
    start = f'g=$({LOOP_CMD} start .sdlc --session-pid "$PPID")'
    pick = f'{LOOP_CMD} next .sdlc --land <N> --session-pid "$PPID"'
    end = f'{LOOP_CMD} session-end .sdlc --session-pid "$PPID" --session-generation "$g"'
    for gesture in (start, pick, end):
        assert gesture in flat, gesture
    assert flat.index(start) < flat.index(pick) < flat.index(end)


@pytest.mark.parametrize("name,text", [("land.md", LAND), ("SKILL.md --land section", "")])
def test_no_other_selection_gesture_is_given(name, text):
    text = text or SKILL.split("## `--land`", 1)[-1].split("\n## ", 1)[0]
    assert '/loop.py" claim' not in text and '/loop.py" next-batch' not in text, name
    for m in re.finditer(r'/loop\.py" next\b', text):
        assert text[m.end():m.end() + 15].startswith(" .sdlc --land"), text[m.start():m.end() + 40]


def test_no_gate_pauses_and_the_gates_that_still_bind():
    flat = _flat(LAND)
    assert "**No gate pauses.**" in flat
    assert "Plan-Review, `sigma-review` and `sigma-verify` still run" in flat


def test_the_loops_steps_are_reached_by_reference_never_copied():
    flat = _flat(LAND)
    assert "steps 3 to 6" in flat and re.search(r"never (takes )?step 7", flat)
    for target in ("../../sigma-loop/SKILL.md", "../../sigma-loop/references/landing.md",
                   "../../sigma-loop/references/running.md", "../../sigma-loop/references/progress.md"):
        assert f"]({target})" in LAND, target
        assert (LAND_PATH.parent / target).resolve().is_file(), target
    for text in (SKILL, LAND):
        for verb in ("commit", "pr", "merge", "post-review"):
            assert f'/work.py" {verb} ' not in text, f"work.py {verb} is copied; reach it through landing.md"


def test_the_cited_loop_anchors_exist():
    for step in ("\n3. Otherwise", "\n5. **Retrospective", "\n6. Record the outcome", "\n7. Loop."):
        assert step in LOOP_SKILL, step
    for phrase in ("`PARK: …` → `record parked", "`record review`", "`REPAIR:", "`RERUN:"):
        assert phrase in LANDING, phrase


ENDINGS = ["**merged**", "**parked**", "**parked, awaiting merge (not `sdlc:parked`)**", "**failed**",
           "**failed (not started)**"]


@pytest.mark.parametrize("ending", ENDINGS)
def test_every_ending_is_named(ending):
    assert ending in LAND, ending


def test_endings_are_keyed_on_the_record_verb_and_the_pickers_own_line():
    flat = _flat(LAND)
    for verb in ("`record done`", "`record parked", "`record review`", "`record failed"):
        assert verb in flat, verb
    fmt = re.search(r'"loop\.py: (--land #%s not taken \(not started\)): ', LOOP_PY)
    assert fmt, "the picker's line moved in loop.py"
    assert fmt.group(1).replace("%s", "<N>") in flat, "land.md must quote the picker's own line"
    for word in ("`HOLD`", "`REPAIR:`", "`RERUN:`", "`DEGRADED <epoch>`", "wait-degraded <epoch>"):
        assert word in flat, f"{word} must be named as not an ending"


def test_park_reasons_are_surfaced_on_the_issue_and_in_the_final_message():
    flat = _flat(LAND)
    assert "posts the reason on the issue" in flat
    assert "final message" in flat


def test_a_pre_check_park_ends_the_run_parked_and_never_loops_back():
    flat = _flat(LAND)
    assert "never loops back to step 1" in flat
    assert "a step-3 pre-check (`precheck`, `decompose-check`, `design-check`) printed `PARKED <reason>`" in flat
    assert "loop back to step 1" in _flat(LOOP_SKILL), "the loop step this overrides moved"


def test_the_handoff_and_blocker_exceptions_are_named_beside_the_sentence():
    after = _flat(SKILL).split(_flat(SENTENCE), 1)[1][:900]
    assert "`handoff.py`" in after and "NEW issue, never an existing goal" in after
    assert "`backlog_check.enabled`" in after and "blocker issues" in after


def test_a_blocked_pre_check_and_a_step_3a_stop_have_endings():
    flat = _flat(LAND)
    assert "`PARKED <reason>` or `BLOCKED <reason>` | **parked**" in flat
    assert '"BLOCKED "' in LOOP_PY, "precheck no longer prints BLOCKED; the row is stale"
    assert "step 3a stopped: `agent-start` exited nonzero, or `work.py start` exited 4" in flat


def test_the_crash_lever_never_reruns_merged_work():
    assert "Do not re-run `--land`: it would redo merged work." in _flat(LAND)


def test_a_crashed_run_names_its_lever():
    flat = _flat(LAND)
    assert f'{LOOP_CMD} release .sdlc <N> "<why>"' in flat
    assert "`--land` never reclaims a claim" in flat
    assert "release <dir> <goal>" in LOOP_PY, "the release verb the lever names moved"


def test_the_post_pr_review_never_leaves_line_comments():
    assert "It never leaves a PR line comment" in _flat(LAND)


def test_the_awaiting_merge_lever_is_keyed_on_the_pr_body():
    flat = _flat(LAND)
    assert "`Closes #N`" in flat and "`Refs #N`" in flat


def test_picking_documents_the_flag_on_next_only():
    flat = _flat(PICKING)
    assert "`--land <N>`" in flat and "/sigma-goal <N> --land" in flat and "`next-batch` refuses it" in flat


def test_the_cloud_doc_names_land_and_no_longer_says_approval_parks():
    section = CLOUD.split("## Running the loop in a Claude Code cloud session", 1)[1].split("\n## ", 1)[0]
    assert "/sigma-goal <N> --land" in section
    assert not re.search(r"`require_review: approval`\s+parks", _flat(CLOUD)), "stale: #1398 accepts sigma:approve"
    assert "approval mode parks, never passes" not in _flat(CLOUD)


def test_the_running_doc_documents_land():
    assert "### `/sigma-goal <N> --land`" in RUNNING
    assert "is not driven by `loop.py next`/`next-batch`" not in _flat(RUNNING)

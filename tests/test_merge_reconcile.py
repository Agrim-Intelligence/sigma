"""#232: done means merged -- `record review`, the merge-reconcile pass, and the review gate running
under `auto_merge: off`. Hermetic: every `gh` call goes through an injected runner (no network, no
real GitHub). The end-to-end half (fake `gh` on PATH + a real bare origin, on the shipped defaults)
is `tests/test_public_bootstrap_control.py::test_a_goal_goes_from_filed_to_merged_on_the_public_profile`.
"""
import importlib.util
import json
import pathlib
import tempfile

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


@pytest.fixture(autouse=True)
def _no_codex_host(monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


WORK_ON = {"work": {"enabled": True}}


class FakeGh:
    """A tiny `run(cwd, argv)` with per-PR REST state. `prs` maps PR number -> "open" | "merged" |
    "closed" | Exception. Records every call line."""

    def __init__(self, prs):
        self.prs = dict(prs)
        self.calls = []

    def __call__(self, cwd, argv):
        line = " ".join(str(a) for a in argv)
        self.calls.append(line)
        if "/pulls/" in line:
            n = line.rsplit("/pulls/", 1)[1].split()[0]
            st = self.prs.get(n, RuntimeError("HTTP 404"))
            if isinstance(st, Exception):
                raise st
            return json.dumps({"number": int(n), "state": "closed" if st != "open" else "open",
                               "merged": st == "merged",
                               "merged_at": "2026-01-02T00:00:00Z" if st == "merged" else None})
        if "pr view" in line:
            st = self.prs.get(line.split("pr view", 1)[1].split()[0], "open")
            return json.dumps({"state": {"open": "OPEN", "merged": "MERGED"}.get(st, "CLOSED"),
                               "autoMergeRequest": None})
        return ""

    def pr_reads(self):
        return [c for c in self.calls if "/pulls/" in c]


class RecordingSource:
    """Just enough of a source for `_record`: remembers what was called."""

    def __init__(self, complete_raises=False):
        self.events = []
        self.complete_raises = complete_raises

    def complete(self, goal):
        self.events.append(("complete", goal))
        if self.complete_raises:
            raise RuntimeError("gh: GraphQL quota exhausted")

    def park(self, goal, reason):
        self.events.append(("park", goal, reason))

    def fail(self, goal, reason):
        self.events.append(("fail", goal, reason))

    def mark_qc(self, goal):
        self.events.append(("qc", goal))

    def await_merge(self, goal, note=None):
        self.events.append(("await_merge", goal, note))


def _sdlc(d, config=WORK_ON):
    base = pathlib.Path(d) / ".sdlc"
    (base / "goals").mkdir(parents=True)
    (base / "state").mkdir()
    (base / "config.json").write_text(json.dumps(config))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "goals" / "0001.md").write_text("---\nid: 0001\nstatus: in_progress\n---\nx\n")
    return str(base)


def _started(lp, base, goal, pr="7"):
    wt = pathlib.Path(base) / "work" / pathlib.Path(goal).stem
    wt.mkdir(parents=True, exist_ok=True)
    lp.work._save(base, goal, {"worktree": str(wt), "branch": "sdlc/x", "base": "main",
                               "remote": "origin", "pr": pr})
    return wt


# --- `record review` -----------------------------------------------------------------------------


def test_record_review_keeps_the_goal_open_in_qc_and_flags_the_work_record(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        goal = base + "/goals/0001.md"
        wt = _started(lp, base, goal)
        lp.work._run = FakeGh({"7": "open"})
        assert lp.main(["loop.py", "record", base, goal, "review"]) == 0
        text = pathlib.Path(goal).read_text()
        assert "status: in_progress" in text and "status: done" not in text   # not done
        rec = lp.work._record(base, goal)
        assert rec["awaiting_merge"]["pr"] == "7" and rec["awaiting_merge"]["goal"] == goal
        assert wt.exists()                                  # checkout kept until the merge
        assert "PR #7 is open and awaiting a merge" in (pathlib.Path(base) / "journey" / "0001.md").read_text()
        # One `record review` read nothing from GitHub: it is a local, cheap, never-lossy record.
        assert lp.work._run.calls == []


def test_record_review_posts_its_audit_note_once():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        goal = "0001"
        _started(lp, base, goal)
        src = RecordingSource()
        assert lp._record(base, src, goal, "review") == "review"
        assert lp._record(base, src, goal, "review") == "review"
        notes = [e[2] for e in src.events if e[0] == "await_merge"]
        assert len(notes) == 2 and notes[0] and notes[1] is None
        assert not any(e[0] in ("complete", "park") for e in src.events)


def test_record_review_ends_the_ledger_claim_without_a_park_event():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"work": {"enabled": True}, "ledger": {"enabled": True, "actor": "rae"}})
        lp = _load("loop")
        lp.ledger.reset_actor_cache()
        _started(lp, base, "0001")
        lp.ledger.safe_append(base, "claimed", "0001")
        assert "0001" in lp.ledger.open_claims(lp.ledger.read_all(base))
        lp._record(base, RecordingSource(), "0001", "review")
        assert "0001" not in lp.ledger.open_claims(lp.ledger.read_all(base))
        kinds = [e.get("kind") for e in lp.ledger.read_all(base)]
        assert kinds[-1] == "parked"                          # the lease-ending entry
        assert not any(e.get("kind") == "park" for e in lp.ledger.read_all(base, stream=lp.ledger.EVENTS))


@pytest.mark.parametrize("config,pr,expect", [
    ({"work": {"enabled": False}}, "7", "work.enabled is off"),
    (WORK_ON, "", "no PR is on record"),
])
def test_record_review_is_refused_when_there_is_no_pr_to_await(capsys, config, pr, expect):
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, config)
        lp = _load("loop")
        goal = base + "/goals/0001.md"
        _started(lp, base, goal, pr=pr)
        assert lp.main(["loop.py", "record", base, goal, "review"]) == 2
        assert expect in capsys.readouterr().err
        assert "awaiting_merge" not in (lp.work._record(base, goal) or {})


# --- `record done` refusal through the CLI (the documented gesture) --------------------------------


def test_record_done_is_refused_while_the_pr_is_open_on_the_shipped_default_policy(capsys):
    """RED against the pre-#232 code: `auto_merge` unset == `off` let `done` through and the local
    goal file went `status: done` (github mode: the issue closed) with the PR still open."""
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        goal = base + "/goals/0001.md"
        _started(lp, base, goal)
        lp.work._run = FakeGh({"7": "open"})
        assert lp.main(["loop.py", "record", base, goal, "done"]) == 4
        err = capsys.readouterr().err
        assert "REFUSED" in err and "PR #7" in err and "record" in err and "review" in err
        assert "status: done" not in pathlib.Path(goal).read_text()


def test_record_done_passes_once_the_pr_is_merged():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        goal = base + "/goals/0001.md"
        _started(lp, base, goal)
        lp.work._run = FakeGh({"7": "merged"})
        assert lp.main(["loop.py", "record", base, goal, "done"]) == 0
        assert "status: done" in pathlib.Path(goal).read_text()


def test_a_goal_with_no_pr_records_done_unchanged():
    """(4) Local goals with no PR: unchanged -- no read, no refusal."""
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        goal = base + "/goals/0001.md"
        lp.work._run = FakeGh({})
        assert lp.main(["loop.py", "record", base, goal, "done"]) == 0
        assert "status: done" in pathlib.Path(goal).read_text()
        assert lp.work._run.calls == []


# --- the merge-reconcile pass --------------------------------------------------------------------


def _awaiting(lp, base, goals_prs, src=None):
    src = src or RecordingSource()
    for goal, pr in goals_prs:
        _started(lp, base, goal, pr=pr)
        lp._record(base, src, goal, "review")
    return src


def test_reconcile_does_nothing_while_the_pr_is_open_and_closes_once_it_merges():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7")])
        gh = FakeGh({"7": "open"})
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0) == []
        assert not any(e[0] == "complete" for e in src.events)
        gh.prs["7"] = "merged"
        lp.work._run = gh                   # `_release_checkout` -> `finish` reads through `_run`
        out = lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0)
        assert out == [("0001", "done", "7")]
        assert [e for e in src.events if e[0] == "complete"] == [("complete", "0001")]
        # IDEMPOTENT: a second pass finds nothing awaiting and makes no call at all.
        before = len(gh.calls)
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0) == []
        assert len(gh.calls) == before
        assert [e for e in src.events if e[0] == "complete"] == [("complete", "0001")]


def test_reconcile_parks_a_pr_closed_without_merging():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7")])
        out = lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=FakeGh({"7": "closed"}),
                                            min_interval=0)
        assert out == [("0001", "parked", "7")]
        assert any(e[0] == "park" and "closed without merging" in e[2] for e in src.events)
        assert not any(e[0] == "complete" for e in src.events)
        assert "awaiting_merge" not in lp.work._record(base, "0001")


def test_reconcile_leaves_an_unreadable_pr_alone():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7")])
        gh = FakeGh({"7": RuntimeError("gh: network unreachable")})
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0) == []
        assert not any(e[0] in ("complete", "park") for e in src.events)
        assert lp.work._record(base, "0001")["awaiting_merge"]["checked_at"] > 0


def test_reconcile_retries_a_close_that_failed():
    """A raising `complete()` downgrades the `done` to `parked` (#1201); the flag must survive so
    the next pass retries the close rather than stranding a merged goal's issue open."""
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7")])
        src.complete_raises = True
        gh = FakeGh({"7": "merged"})
        lp.work._run = gh
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh,
                                             min_interval=0) == [("0001", "parked", "7")]
        assert lp.work._record(base, "0001").get("awaiting_merge")
        src.complete_raises = False
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh,
                                             min_interval=0) == [("0001", "done", "7")]


def test_reconcile_is_bounded_per_pass_and_round_robins():
    """The 100x cost claim, measured: 15 awaiting PRs -> exactly MERGE_RECONCILE_MAX_PER_PASS (10)
    REST reads per pass, and the next pass reads the 5 the first one did not reach first."""
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        goals = [(f"{i:04d}", str(100 + i)) for i in range(1, 16)]
        src = _awaiting(lp, base, goals)
        gh = FakeGh({pr: "open" for _, pr in goals})
        lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0, now=1000)
        first = [c.rsplit("/", 1)[1] for c in gh.pr_reads()]
        assert len(first) == lp.work.MERGE_RECONCILE_MAX_PER_PASS == 10
        gh.calls.clear()
        lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0, now=1001)
        second = [c.rsplit("/", 1)[1] for c in gh.pr_reads()]
        assert len(second) == 10
        unread = {pr for _, pr in goals} - set(first)
        assert set(second[:5]) == unread


def test_reconcile_honours_the_recheck_interval_on_the_automatic_triggers():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7")])
        gh = FakeGh({"7": "open"})
        lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, now=1000)
        lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, now=1000 + 60)
        assert len(gh.pr_reads()) == 1
        lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh,
                                      now=1000 + lp.work.MERGE_RECHECK_SECONDS)
        assert len(gh.pr_reads()) == 2


def test_reconcile_costs_nothing_when_work_is_off_or_nothing_awaits():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        gh = FakeGh({})
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=RecordingSource(), run=gh) == []
        assert lp._reconcile_awaiting_merges(base, {"work": {"enabled": False}}, run=gh) == []
        assert gh.calls == []


def test_the_reconcile_cli_verb_prints_one_line_per_recorded_goal(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        goal = base + "/goals/0001.md"
        _started(lp, base, goal)
        lp.work._run = FakeGh({"7": "open"})
        assert lp.main(["loop.py", "record", base, goal, "review"]) == 0
        capsys.readouterr()
        assert lp.main(["loop.py", "reconcile-merges", base]) == 0
        assert capsys.readouterr().out == ""
        lp.work._run.prs["7"] = "merged"
        assert lp.main(["loop.py", "reconcile-merges", base]) == 0
        assert capsys.readouterr().out.strip() == f"{goal} done (PR #7 merged)"
        assert "status: done" in pathlib.Path(goal).read_text()


def test_the_watch_tick_runs_the_merge_pass_without_the_reconcile_opt_in():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        tick = _load("reconcile_tick")
        lp = tick.loop
        goal = base + "/goals/0001.md"
        _started(lp, base, goal)
        lp._record(base, lp.sources.get_source(base, WORK_ON), goal, "review")
        gh = FakeGh({"7": "merged"})
        lp.work._run = gh
        line = tick.tick(base, config=WORK_ON, run=gh)
        assert "1 awaiting-merge goal(s) recorded" in line and "done" in line


# --- the picker skips a goal awaiting merge ------------------------------------------------------


class SkipProbeSource(RecordingSource):
    """Remembers the skip set `_next` hands the picker, then reports an empty backlog."""

    def next_pending(self, skip=()):
        self.skip = set(skip)
        return None


def test_next_does_not_serve_a_goal_awaiting_merge():
    """Its issue keeps the claim's `sdlc:in-progress`, but that overlay is not re-asserted by
    `record review` (single-writer rule), so the picker's own skip set must carry the goal."""
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = SkipProbeSource()
        lp._next(base, src, WORK_ON, session_pid="1")
        assert "0001" not in src.skip
        _awaiting(lp, base, [("0001", "7")])
        lp.work._run = FakeGh({"7": "open"})
        lp._next(base, src, WORK_ON, session_pid="1")
        assert "0001" in src.skip


# --- run_loop: the programmatic driver gets the same rule ----------------------------------------


def test_run_loop_records_review_not_done_for_an_unmerged_pr():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"work": {"enabled": True}, "budget": {"max_iterations": 1}})
        (pathlib.Path(base) / "goals" / "0001.md").write_text("---\nid: 0001\nstatus: pending\n---\nx\n")
        lp = _load("loop")
        lp.work._run = FakeGh({"7": "open"})

        def run_goal(goal):
            _started(lp, base, goal)
            return "done", ""
        lp.run_loop(base, run_goal)
        text = (pathlib.Path(base) / "goals" / "0001.md").read_text()
        assert "status: done" not in text
        assert lp.work.awaiting_merge_goals(base)


# --- (3) the review gate runs when the PR is left for a human ------------------------------------


def _merge_ready(w, st, d, config):
    base = _sdlc(d, config)
    wt = pathlib.Path(base) / "work" / "0001"
    wt.mkdir(parents=True)
    w._save(base, "0001", {"worktree": str(wt), "branch": "sdlc/0001", "base": "main",
                           "remote": "origin", "pr": "7"})
    st.start_run(base)
    ev = st.evidence_path(base, "0001")
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(json.dumps({"exit_code": 0, "at": st.load_cursor(base)["run_started_at"],
                              "run_id": st.run_identity()}))
    return base


def _merge_runner(comments):
    view = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                       "statusCheckRollup": [], "headRefOid": "b" * 40, "state": "OPEN"})
    handlers = [
        ("isCrossRepository", json.dumps({"isCrossRepository": False})),
        ("viewerPermission", "ADMIN"),
        ("comments,author", json.dumps({"author": {"login": "bot"},
                                        "comments": [{"body": c, "author": {"login": "bot"}}
                                                     for c in comments]})),
        ("reviewDecision,latestReviews", json.dumps({"reviewDecision": None, "latestReviews": []})),
        ("nameWithOwner", "acme/app"),
        ("graphql", json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}})),
        ("pr view", view),
        ("rev-parse HEAD", "b" * 40),
    ]
    calls = []

    def run(cwd, argv):
        line = " ".join(str(a) for a in argv)
        calls.append(line)
        for token, resp in handlers:
            if token in line:
                return resp
        return ""
    run.calls = calls
    return run


@pytest.mark.parametrize("comments,mode,starts,contains", [
    (["sigma:block the change has no test"], "changes", "PARK:", "sigma:block"),
    (["sigma:approve"], "changes", "clean and safe", "review gate passed (require_review: changes)"),
    ([], "off", "clean and safe", "review gate off"),
])
def test_merge_under_auto_merge_off_runs_the_review_gate_first(monkeypatch, comments, mode, starts,
                                                               contains):
    """RED against the pre-#232 code for the block case: `auto_merge: off` returned `leaving PR #7
    for a human` before `review_gate` ran, so a `sigma:block` was invisible on the shipped defaults."""
    w = _load("work")
    st = w.state
    with tempfile.TemporaryDirectory() as d:
        config = {"work": {"enabled": True, "auto_merge": "off", "require_review": mode}}
        base = _merge_ready(w, st, d, config)
        monkeypatch.setattr(st, "done_refusal", lambda *a, **k: None)
        run = _merge_runner(comments)
        out = w.merge(base, config, "0001", run=run, sleep=lambda _: None)
        assert out.startswith(starts), out
        assert contains in out, out
        if starts != "PARK:":
            assert out.endswith("leaving PR #7 for a human")
        assert not any("pr merge" in c for c in run.calls)    # `off` never merges, whatever the gate

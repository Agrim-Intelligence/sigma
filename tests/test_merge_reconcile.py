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
    """A raising `complete()` must keep the flag so the next pass retries the close rather than
    stranding a merged goal's issue open -- and (#255) it retries QUIETLY: nothing is recorded,
    nothing is parked in public, on a transient failure."""
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7")])
        src.complete_raises = True
        gh = FakeGh({"7": "merged"})
        lp.work._run = gh
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh,
                                             min_interval=0) == []
        assert lp.work._record(base, "0001").get("awaiting_merge")
        assert not any(e[0] == "park" for e in src.events)
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


# --- #255: hardening after #232 --------------------------------------------------------------------


class ReentrantSource(RecordingSource):
    """`complete()` runs a SECOND pass while the first is mid-record -- the deterministic stand-in
    for a `next` and a watch tick racing on the same goal (two processes, one flag)."""

    def __init__(self, lp, base, gh):
        super().__init__()
        self.lp, self.base, self.gh, self.inner = lp, base, gh, None

    def complete(self, goal):
        super().complete(goal)
        if self.inner is None and len([e for e in self.events if e[0] == "complete"]) < 3:
            self.inner = self.lp._reconcile_awaiting_merges(self.base, WORK_ON, source=self,
                                                            run=self.gh, min_interval=0)


def test_255_1_a_closing_pass_reads_each_pr_once_over_rest_and_never_through_graphql():
    """(1) The documented cost, measured: a pass that CLOSES goals makes one REST `pulls/<n>` read
    per goal and no `gh pr view` (GraphQL) at all -- #232 shipped 2 REST reads + 3 `gh pr view`
    per closed goal while README/landing.md said "<=10 REST reads per pass, never GraphQL"."""
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7"), ("0002", "8")])
        gh = FakeGh({"7": "merged", "8": "merged"})
        lp.work._run = gh
        out = lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0)
        assert sorted(out) == [("0001", "done", "7"), ("0002", "done", "8")]
        assert len(gh.pr_reads()) == 2, gh.calls
        assert not [c for c in gh.calls if "pr view" in c or "graphql" in c], gh.calls


def test_255_2_two_passes_racing_record_done_once():
    """(2) A `next` and the watch tick racing: the second pass must not also record `done`."""
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        gh = FakeGh({"7": "merged"})
        lp.work._run = gh
        src = ReentrantSource(lp, base, gh)
        _awaiting(lp, base, [("0001", "7")], src=src)
        out = lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0)
        assert out == [("0001", "done", "7")]
        assert src.inner == []
        assert [e for e in src.events if e[0] == "complete"] == [("complete", "0001")]
        recorded = [l for l in (pathlib.Path(base) / "state" / "STATE.md").read_text().splitlines()
                    if l.startswith("iteration:")]
        assert recorded == ["iteration: 2"], recorded     # review + ONE done, never two


def test_255_2_a_held_lock_skips_the_pass_without_a_single_call(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7")])
        gh = FakeGh({"7": "merged"})
        with lp._merge_reconcile_lock(base) as held:
            assert held is True
            assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh,
                                                 min_interval=0) == []
        assert gh.calls == []
        assert "another merge-reconcile pass" in capsys.readouterr().err
        # Released with its holder: the next pass runs.
        lp.work._run = gh
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh,
                                             min_interval=0) == [("0001", "done", "7")]


def test_255_3_a_pass_killed_after_the_record_never_records_done_again():
    """(3) The watch tick's timeout kills the pass after the issue closed and `done` was recorded
    but before the slow tail (unit-completion signal, checkout release) finished. SystemExit stands
    in for the kill: it is a BaseException, so no `except Exception` in the pass absorbs it."""
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7")])
        gh = FakeGh({"7": "merged"})
        lp.work._run = gh
        real = lp._signal_unit_completion

        def killed(*a, **k):
            raise SystemExit("killed by run_with_timeout")
        lp._signal_unit_completion = killed
        with pytest.raises(SystemExit):
            lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0)
        lp._signal_unit_completion = real
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh,
                                             min_interval=0) == []
        assert [e for e in src.events if e[0] == "complete"] == [("complete", "0001")]


def test_255_3_a_pass_stops_starting_goals_once_its_budget_is_spent(monkeypatch):
    """(3) Bounded under the tick's call timeout: the budget is derived from
    SIGMA_WATCH_CALL_TIMEOUT (half of it), and no new goal is started once it is spent."""
    monkeypatch.setenv("SIGMA_WATCH_CALL_TIMEOUT", "20")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        assert lp._merge_reconcile_budget() == 10.0
        src = _awaiting(lp, base, [("0001", "7"), ("0002", "8"), ("0003", "9")])
        gh = FakeGh({"7": "open", "8": "open", "9": "open"})
        ticks = iter([0.0, 0.0, 11.0, 11.0, 11.0, 11.0])
        lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0,
                                      clock=lambda: next(ticks))
        assert len(gh.pr_reads()) == 1


def test_255_4_awaiting_merge_age_is_reported_by_doctor_and_status():
    """(4) LIVENESS: a goal stuck awaiting merge (an armed PR whose check fails never lands) and a
    machine where nothing runs the pass both show their AGE -- an idle wait does not."""
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        _started(lp, base, "0001", pr="7")
        lp.work.mark_awaiting_merge(base, "0001", now=1_000_000)
        lp.work.stamp_merge_check(base, "0001", now=1_000_000 + 3 * 86400)
        now = 1_000_000 + 3 * 86400 + 60
        report = lp.work.awaiting_merge_report(base, now=now)
        assert report[0]["goal"] == "0001" and report[0]["stuck"] and not report[0]["unwatched"]
        assert lp.work.awaiting_merge_line(base, now=now).startswith("awaiting merge: 1 (")
        assert "0001 PR #7 for 3d" in lp.work.awaiting_merge_line(base, now=now)
        doctor = _load_skill("agrim-doctor", "doctor")
        row = doctor._awaiting_merge_row(base, now=now)
        assert row["ok"] is False and "for 3d" in row["name"] and "PR #7" in row["fix"]
        # Nothing reading it for a day is the OTHER death: the pass is not running at all.
        row = doctor._awaiting_merge_row(base, now=1_000_000 + 3 * 86400 + 2 * 86400)
        assert row["ok"] is False and "no PR read for" in row["name"]
        status = _load_skill("agrim-status", "status")
        assert "awaiting merge: 1" in status._awaiting_merge_segment(base, now=now)
        # A fresh wait, read a minute ago, is idle, not dead.
        lp.work.stamp_merge_check(base, "0001", now=now)
        lp.work._save(base, "0001", dict(lp.work._record(base, "0001"),
                                         awaiting_merge={"pr": "7", "goal": "0001",
                                                         "since": now - 3600, "checked_at": now}))
        assert doctor._awaiting_merge_row(base, now=now + 60)["ok"] is True


def _load_skill(skill, name):
    path = S.parent.parent / skill / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _log_rows(base, rows):
    logdir = pathlib.Path(base) / "state" / "log"
    logdir.mkdir(parents=True, exist_ok=True)
    with open(logdir / "0001.jsonl", "w", encoding="utf-8") as fh:
        for ts, kind, extra in rows:
            fh.write(json.dumps(dict({"ts": ts, "goal": "0001", "kind": kind, "actor": "loop",
                                      "thread": "main"}, **extra)) + "\n")


def test_255_4_log_keeps_a_review_goal_in_flight_and_says_how_long_it_has_waited():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        log = _load_skill("agrim-log", "log")
        _log_rows(base, [("2026-01-01T00:00:00.000Z", "claimed", {}),
                         ("2026-01-01T00:10:00.000Z", "agent_dispatch", {"phase": "retro"}),
                         ("2026-01-01T01:00:00.000Z", "recorded", {"result": "review"})])
        now = log._epoch("2026-01-04T05:00:00.000Z")
        rows = log.active(base, now=now)
        assert [r[0] for r in rows] == ["0001"]
        entries = log.read_goal(base, "0001")
        assert log.is_closed(entries) is False
        assert "awaiting merge for 3d 04h" in log.describe(rows[0][2], rows[0][3])
        assert "awaiting merge for 3d 04h" in log.status(base, now=now)
        # A goal recorded DONE is still closed.
        _log_rows(base, [("2026-01-01T00:00:00.000Z", "claimed", {}),
                         ("2026-01-01T01:00:00.000Z", "recorded", {"result": "done"})])
        assert log.active(base, now=now) == []


@pytest.mark.parametrize("result", ["parked", "failed"])
def test_255_5_a_human_park_or_fail_clears_the_flag_so_a_later_merge_does_not_override_it(result):
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7")])
        lp._record(base, src, "0001", result, "the human decided against it")
        assert "awaiting_merge" not in lp.work._record(base, "0001")
        gh = FakeGh({"7": "merged"})
        lp.work._run = gh
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0) == []
        assert not any(e[0] == "complete" for e in src.events)


def test_255_6_finish_refuses_a_goal_awaiting_merge_even_when_its_pr_is_armed():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        _awaiting(lp, base, [("0001", "7")])

        def armed(cwd, argv):
            if "pr view" in " ".join(argv):
                return json.dumps({"state": "OPEN", "autoMergeRequest": {"enabledAt": "x"}})
            return ""
        out = lp.work.finish(base, WORK_ON, "0001", run=armed)
        assert out.startswith("kept ") and "awaiting merge" in out, out
        assert lp.work.record_path(base, "0001").exists()


def test_255_7_a_transient_close_failure_retries_quietly_and_parks_publicly_once():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        lp = _load("loop")
        src = _awaiting(lp, base, [("0001", "7")])
        src.complete_raises = True
        gh = FakeGh({"7": "merged"})
        lp.work._run = gh

        def parks():
            return [e for e in src.events if e[0] == "park"]
        for attempt in range(1, lp.MERGE_CLOSE_ATTEMPTS):
            assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh,
                                                 min_interval=0) == []
            assert parks() == [], attempt
            assert lp.work._record(base, "0001")["awaiting_merge"]["close_failures"] == attempt
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh,
                                             min_interval=0) == [("0001", "parked", "7")]
        assert len(parks()) == 1
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh, min_interval=0) == []
        assert len(parks()) == 1                          # once, not every pass
        src.complete_raises = False
        assert lp._reconcile_awaiting_merges(base, WORK_ON, source=src, run=gh,
                                             min_interval=0) == [("0001", "done", "7")]


def test_255_9_run_loop_counts_review_apart_from_parked():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"work": {"enabled": True}, "budget": {"max_iterations": 1}})
        (pathlib.Path(base) / "goals" / "0001.md").write_text("---\nid: 0001\nstatus: pending\n---\nx\n")
        lp = _load("loop")
        lp.work._run = FakeGh({"7": "open"})

        def run_goal(goal):
            _started(lp, base, goal)
            return "done", ""
        out = lp.run_loop(base, run_goal)
        assert out["review"] == 1 and out["parked"] == 0, out

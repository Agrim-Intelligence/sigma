"""#2223: where `release_dependents` runs -- the `record done` tail, the `reconcile-merges` CLI, the watcher
tick duty -- and that every one of them is fail-open, ordered, gated and bounded. In-memory fakes only."""
import importlib.util
import json
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_release_dependents as base  # noqa: E402

S = base.S
FakeSource, _issue, _world, _cfg = base.FakeSource, base._issue, base._world, base._cfg


def _mod(name):
    return base._mod(name)


loop = _mod("loop")
reconcile_tick = _mod("reconcile_tick")
rd = base.rd


class GhFake(FakeSource, loop.sources.GitHubSource):
    """A `GitHubSource` for `isinstance`, answered entirely by `FakeSource`; `complete` closes the issue."""

    def __init__(self, issues, fail=()):
        FakeSource.__init__(self, issues, fail=fail)
        self.completed = []

    def complete(self, goal):
        self.completed.append(goal)
        self.issues[int(goal)]["state"] = "CLOSED"
        self.issues[int(goal)]["labels"] = [l for l in self.issues[int(goal)]["labels"] if l != "sdlc:blocking"] + ["sdlc:blocking"]

    def park(self, *a, **k):
        pass

    def fail(self, *a, **k):
        pass


def _sdlc(tmp_path, cfg=None):
    d = pathlib.Path(tmp_path) / ".sdlc"
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg if cfg is not None else _cfg()))
    return str(d)


def _open_world(extra):
    world = _world(extra)
    world[12] = _issue(labels=("sdlc:goal", "sdlc:in-progress"))       # 12 is the goal being recorded done
    return world


# --------------------------------------------------------------------------------- record done


def _need():
    """Test-first precondition: the hook helper and the tick duty exist (named assertion, see test_release_dependents)."""
    assert hasattr(loop, "_release_dependents_after_done"), "loop._release_dependents_after_done is missing (#2223)"
    assert hasattr(_mod("reconcile_tick"), "_release_blocked"), "reconcile_tick._release_blocked is missing (#2223)"


def test_record_done_releases_dependents(tmp_path):
    _need()
    d = _sdlc(tmp_path)
    src = GhFake(_open_world({40: _issue("blocked by #12"), 41: _issue("blocked by #14")}))
    assert loop._record(d, src, "12", "done") == "done"
    assert src.completed == ["12"]
    assert [c for c in src.calls if c[0] == "swap"] == [("swap", 40, (), ("sdlc:blocked",))]


def test_record_done_hook_failure_is_swallowed_and_done_still_recorded(tmp_path, capsys):
    _need()
    d = _sdlc(tmp_path)
    src = GhFake(_open_world({40: _issue("blocked by #12")}), fail=[("read", "12")])
    assert loop._record(d, src, "12", "done") == "done" and src.completed == ["12"]
    class Boom:
        @staticmethod
        def release(*a, **k):
            raise RuntimeError("boom")
    assert loop._release_dependents_after_done(d, src, "12", release_module=Boom) is None
    assert "failed non-fatally" in capsys.readouterr().err


@pytest.mark.parametrize("result", ["parked", "failed"])
def test_record_parked_or_failed_outcome_does_not_release(tmp_path, monkeypatch, result):
    _need()
    seen = []
    monkeypatch.setattr(loop, "_release_dependents_after_done", lambda *a, **k: seen.append(a))
    loop._record(_sdlc(tmp_path), GhFake(_open_world({})), "12", result)
    assert seen == []
    loop._record(_sdlc(tmp_path), GhFake(_open_world({})), "12", "done")
    assert len(seen) == 1                          # and the guard is not simply never true


def test_hook_runs_last_after_the_done_bookkeeping(tmp_path, monkeypatch):
    _need()
    order = []
    for name in ("_signal_unit_completion", "_release_checkout"):
        monkeypatch.setattr(loop, name, lambda *a, _n=name, **k: order.append(_n))
    monkeypatch.setattr(loop.work, "prune_terminal_review_generations", lambda *a, **k: order.append("prune"))
    monkeypatch.setattr(loop, "_release_dependents_after_done", lambda *a, **k: order.append("release"))
    loop._record(_sdlc(tmp_path), GhFake(_open_world({})), "12", "done")
    assert order == ["_signal_unit_completion", "_release_checkout", "prune", "release"]


def test_housekeeping_false_skips_the_hook(tmp_path, monkeypatch):
    _need()
    seen = []
    monkeypatch.setattr(loop, "_release_dependents_after_done", lambda *a, **k: seen.append(a))
    loop._record(_sdlc(tmp_path), GhFake(_open_world({})), "12", "done", housekeeping=False)
    assert seen == []


def test_hook_loads_config_when_none_and_mode_off_wins(tmp_path):
    _need()
    assert hasattr(loop, "_release_dependents_after_done")   # precondition: an absent helper must not pass vacuously
    cfg = _cfg()
    cfg["discovery"]["auto_unpark"]["mode"] = "off"
    d = _sdlc(tmp_path, cfg)
    src = GhFake(_open_world({40: _issue("blocked by #12")}))
    assert loop._record(d, src, "12", "done", config=None) == "done"
    assert src.calls == []                          # mode off: not one read, not one write


def test_hook_normalises_goal_and_skips_non_numeric_and_non_github(tmp_path):
    _need()
    d = _sdlc(tmp_path)
    got = []

    class Spy:
        @staticmethod
        def release(sdlc_dir, config, goals, **k):
            got.append(list(goals))
            return {"status": "ok", "released": [], "incomplete": [], "not_checked": ""}

        render = staticmethod(lambda r: "")
    src = GhFake(_open_world({}))
    for ref in ("#12", "12", "GH-12"):
        loop._release_dependents_after_done(d, src, ref, release_module=Spy)
    assert got == [["12"]] * 3
    loop._release_dependents_after_done(d, src, "0012-some-goal.md", release_module=Spy)
    loop._release_dependents_after_done(d, object(), "12", release_module=Spy)        # not a GitHubSource
    assert len(got) == 3


def test_hook_is_deadline_bounded_for_large_d(tmp_path, monkeypatch):
    _need()
    monkeypatch.setenv("SIGMA_WATCH_CALL_TIMEOUT", "20")
    seen = {}

    class Spy:
        @staticmethod
        def release(sdlc_dir, config, goals, **k):
            seen.update(k)
            return {"status": "ok", "released": [], "incomplete": [], "not_checked": ""}

        render = staticmethod(lambda r: "")
    t0 = time.time()
    loop._release_dependents_after_done(_sdlc(tmp_path), GhFake(_open_world({})), "12", release_module=Spy)
    assert seen["unattended"] is True and seen["apply"] is True
    assert t0 + 9 <= seen["deadline"] <= t0 + 11          # half of SIGMA_WATCH_CALL_TIMEOUT, derived


def test_merge_reconcile_cli_calls_the_helper_for_each_done(tmp_path, monkeypatch, capsys):
    _need()
    d = _sdlc(tmp_path)
    fake = GhFake(_open_world({}))
    monkeypatch.setattr(loop, "_reconcile_awaiting_merges",
                        lambda *a, **k: [("12", "done", "5"), ("13", "closed", "6"), ("14", "done", "7")])
    monkeypatch.setattr(loop, "_release_source", lambda *a: fake)
    seen = []
    monkeypatch.setattr(loop, "_release_dependents_after_done", lambda sd, src, goal, **k: seen.append((src, goal)))
    assert loop.main(["loop.py", "reconcile-merges", d]) == 0
    assert seen == [(fake, "12"), (fake, "14")]


# --------------------------------------------------------------------------------- the tick duty

@pytest.fixture
def quiet_tick(monkeypatch):
    """The other duties of the tick, silenced: this file tests only the release duty."""
    monkeypatch.setattr(reconcile_tick.loop, "_reconcile_sweep", lambda *a, **k: [])
    monkeypatch.setattr(reconcile_tick, "_board_backfill", lambda *a, **k: 0)
    monkeypatch.setattr(reconcile_tick, "_blocker_rank_repair", lambda *a, **k: "")
    monkeypatch.setattr(reconcile_tick, "_dag_refresh", lambda *a, **k: "")
    monkeypatch.setattr(reconcile_tick.loop, "_reconcile_awaiting_merges", lambda *a, **k: [])


def _tick_cfg(**auto):
    cfg = _cfg()
    cfg["discovery"]["auto_unpark"].update(auto)
    return cfg


def _lists(src):
    return [c for c in src.calls if c[0] == "list"]


def test_tick_runs_the_release_duty_and_reports_it(tmp_path, quiet_tick, monkeypatch):
    _need()
    d = _sdlc(tmp_path)
    monkeypatch.setattr(reconcile_tick.loop, "_reconcile_awaiting_merges", lambda *a, **k: [("12", "done", "5")])
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    out = reconcile_tick.tick(d, config=_tick_cfg(), source=src)
    assert "#40" in out and [c[1] for c in src.calls if c[0] == "swap"] == [40]
    st = rd.read_state(d)
    assert st["status"] == "done" and st["released"] >= 1 and st["mode"] == "on"


def test_tick_duty_failure_costs_the_tick_nothing(tmp_path, quiet_tick, monkeypatch):
    _need()
    d = _sdlc(tmp_path)
    monkeypatch.setattr(reconcile_tick.loop, "_reconcile_sweep", lambda *a, **k: ["7"])
    out = reconcile_tick.tick(d, config=_tick_cfg(), source=object())      # no attributes: raises inside
    assert "#7" in out
    assert rd.read_state(d)["status"] == "error"          # a failure is AGE in the doctor, not silence


def test_tick_all_pass_is_throttled_by_interval(tmp_path, quiet_tick):
    _need()
    d = _sdlc(tmp_path)
    cfg = _tick_cfg(interval_minutes=10)
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    reconcile_tick.tick(d, config=cfg, source=src)
    assert len(_lists(src)) == 1
    reconcile_tick.tick(d, config=cfg, source=src)
    assert len(_lists(src)) == 1                           # inside the interval: no second --all pass
    reconcile_tick.tick(d, config=_tick_cfg(interval_minutes=0), source=src)
    assert len(_lists(src)) == 2


def test_tick_releases_dependents_of_just_merged_goals_unthrottled(tmp_path, quiet_tick, monkeypatch):
    _need()
    d = _sdlc(tmp_path)
    cfg = _tick_cfg(interval_minutes=10)
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    reconcile_tick.tick(d, config=cfg, source=src)         # stamps the --all pass: 40 is released here
    src.issues[41] = _issue("blocked by #12")
    monkeypatch.setattr(reconcile_tick.loop, "_reconcile_awaiting_merges", lambda *a, **k: [("12", "done", "5")])
    before = len(_lists(src))
    out = reconcile_tick.tick(d, config=cfg, source=src)   # inside the throttle window
    assert "#41" in out and len(_lists(src)) == before + 1  # the scoped pass ran; the --all pass did not


def test_tick_deadline_is_shared_with_earlier_duties(tmp_path):
    _need()
    d = _sdlc(tmp_path)
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    T = 1_000_000.0
    kw = dict(config=_tick_cfg(), source=src, budget=120.0, clock=lambda: T, merged=[("12", "done", "5")])
    reconcile_tick._release_blocked(d, tick_start=T - 200, **kw)      # earlier duties spent the whole budget
    assert src.calls == []
    st = rd.read_state(d)
    assert st["status"] == "deferred" and st["starved_ticks"] == 1
    reconcile_tick._release_blocked(d, tick_start=T - 200, **kw)
    assert rd.read_state(d)["starved_ticks"] == 2
    reconcile_tick._release_blocked(d, tick_start=T - 10, **kw)       # a fresh budget would still run
    assert [c[1] for c in src.calls if c[0] == "swap"] == [40]


def test_tick_records_a_refused_pass_in_state_and_says_so_on_stderr(tmp_path, quiet_tick, capsys):
    """Finding 5: a label collision makes the verb REFUSE; the state file must say so (not `done`)."""
    _need()
    d = _sdlc(tmp_path)
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    src.blocked_label = src.goal_blocked_label
    reconcile_tick.tick(d, config=_tick_cfg(), source=src)
    st = rd.read_state(d)
    assert st["status"] == "refused" and st["status"] != "done"
    assert "refused" in capsys.readouterr().err


def test_tick_records_a_rest_reserve_deferral_in_state_and_says_so_on_stderr(tmp_path, quiet_tick, capsys,
                                                                             monkeypatch):
    _need()
    d = _sdlc(tmp_path)
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    # the tick's own gate (its own module instance) passes; the verb's gate, the module under test, defers
    monkeypatch.setattr(reconcile_tick, "_load", lambda n, _l=reconcile_tick._load: rd if n == "release_dependents" else _l(n))
    monkeypatch.setattr(rd.gh_quota, "rest_deferral", lambda cfg: "REST reserve")
    reconcile_tick.tick(d, config=_tick_cfg(), source=src)
    st = rd.read_state(d)
    assert st["status"] == "deferred" and st["status"] != "done"
    assert "deferred" in capsys.readouterr().err
    assert src.writes() == []


def test_all_pass_is_not_stamped_when_the_shared_deadline_was_already_spent(tmp_path, quiet_tick):
    """Finding 5: the --all pass that checked nothing (budget gone mid-tick) must not start the throttle."""
    _need()
    d = _sdlc(tmp_path)
    T = [1_000_000.0]
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    real_list = src._list_issues

    def spend(*a, **k):
        T[0] += 10_000.0               # the budget runs out while the listing is being read
        return real_list(*a, **k)
    src._list_issues = spend
    reconcile_tick._release_blocked(d, _tick_cfg(), tick_start=T[0], budget=120.0, clock=lambda: T[0],
                                    merged=[], source=src)
    st = rd.read_state(d)
    assert st.get("checked") == 0 and src.writes() == []
    assert not st.get("all_ran_at"), "an all pass that read no goal must stay due"


def test_state_is_written_at_start_and_per_goal_and_survives_a_kill(tmp_path, monkeypatch):
    _need()
    d = _sdlc(tmp_path)
    writes = []
    real = rd.write_state
    monkeypatch.setattr(reconcile_tick, "_load", lambda n, _l=reconcile_tick._load: rd if n == "release_dependents" else _l(n))
    monkeypatch.setattr(rd, "write_state", lambda sd, rec: (writes.append(dict(rec)), real(sd, rec))[1])
    src = FakeSource(_world({40: _issue("blocked by #12"), 41: _issue("blocked by #12")}))
    orig = src._swap_labels

    def killed(goal, add=(), remove=()):
        if int(goal) == 40:
            raise SystemExit("killed by run_with_timeout")       # a BaseException: nothing in the duty absorbs it
        return orig(goal, add=add, remove=remove)
    src._swap_labels = killed
    with pytest.raises(SystemExit):
        reconcile_tick._release_blocked(d, _tick_cfg(), tick_start=time.time(), merged=[("12", "done", "5")], source=src)
    assert writes[0]["status"] == "started" and writes[0]["released"] == 0
    st = rd.read_state(d)
    assert st["status"] == "started" and st["released"] == 1 and st["ran_at"]      # age, not silence


def test_mode_off_pays_nothing(tmp_path, quiet_tick):
    _need()
    d = _sdlc(tmp_path)
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    reconcile_tick.tick(d, config=_tick_cfg(mode="off"), source=src)
    assert src.calls == [] and rd.read_state(d) == {}


def test_tick_calls_still_eight_and_unchanged():
    _need()
    wd = _mod("watch_daemon")
    assert hasattr(_mod("reconcile_tick"), "_release_blocked")   # precondition: the duty exists (in the tick, not TICK_CALLS)
    assert len(wd.TICK_CALLS) == 8 and not any("release" in c[0] for c in wd.TICK_CALLS)


# --------------------------------------------------------------------------------- orchestrate

_ROOT = S.parent.parent.parent
_GESTURE = 'python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/release_dependents.py" "$S" <merged goals of the wave...> --apply'


def test_orchestrate_wave_end_calls_the_verb_through_code(tmp_path):
    _need()
    run_md = (_ROOT / "skills/sigma-orchestrate/references/run.md").read_text(encoding="utf-8")
    skill = (_ROOT / "skills/sigma-orchestrate/SKILL.md").read_text(encoding="utf-8")
    doc = (_ROOT / "docs/orchestrate.md").read_text(encoding="utf-8")
    assert _GESTURE in run_md
    assert "release_dependents.py" in skill and "release_dependents.py" in doc
    # the old human-decision gesture survives only under the parked branch
    unblocking = run_md[run_md.index("## Unblocking"):]
    assert unblocking.index("release_dependents.py") < unblocking.index("unpark.py")
    assert "- parked on that blocker" in unblocking.split("unpark.py")[0]
    # the multi-goal form runs end to end on a fake source, one line out
    d = _sdlc(tmp_path)
    src = FakeSource(_world({40: _issue("blocked by #12"), 41: _issue("blocked by #13")}))
    assert rd.main(["release_dependents.py", d, "12", "13", "--apply"], source=src) == 0
    assert sorted(c[1] for c in src.calls if c[0] == "swap") == [40, 41]


def test_rest_reserve_deferral_prints_one_stderr_line_and_is_not_counted_as_starvation(tmp_path, capsys, monkeypatch):
    """Review r2 finding 2: the reserve early return said nothing; it now prints one line. It is a quota
    deferral, not budget starvation, so `starved_ticks` is neither raised nor reset by it."""
    _need()
    d = _sdlc(tmp_path)
    rd.write_state(d, {"ran_at": 1.0, "status": "deferred", "starved_ticks": 3})
    real_load = reconcile_tick._load

    def load(n):
        m = real_load(n)
        if n == "sources":
            m.gh_quota.rest_deferral = lambda cfg: "REST reserve"
        return m
    monkeypatch.setattr(reconcile_tick, "_load", load)
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    T = 1_000_000.0
    reconcile_tick._release_blocked(d, _tick_cfg(), tick_start=T, budget=120.0, clock=lambda: T, merged=[], source=src)
    assert src.calls == []                  # the tick's own reserve gate fired, not the verb's
    err = capsys.readouterr().err
    assert "release-dependents" in err and "REST reserve" in err
    st = rd.read_state(d)
    assert st["status"] == "deferred" and st["starved_ticks"] == 3

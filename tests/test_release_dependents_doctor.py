"""#2223: the `/sigma-doctor` row for blocked goals whose dependencies are all closed, and the watcher duty's
heartbeat row. Read-only; the row uses the verb's own `release_candidates` and the sweep's own decision."""
import importlib.util
import json
import pathlib
import sys
import time
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_release_dependents as base  # noqa: E402
import test_doctor as td  # noqa: E402

FakeSource, _issue, _world, _cfg = base.FakeSource, base._issue, base._world, base._cfg
D = td.D
GH = {"repo": "acme/widget"}
NOW = 1_800_000_000.0


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


@pytest.fixture
def doc(monkeypatch):
    """doctor.py with its `GitHubSource` and its list read replaced by in-memory fakes."""
    d = td._doc()
    real_load = d._load_loop_script
    state = types.SimpleNamespace(src=None, listed=[], list_calls=[], rows=None)

    def load(name):
        m = real_load(name)
        if name == "sources":
            ns = types.SimpleNamespace(**vars(m))
            ns.GitHubSource = lambda cfg, run=None, **k: state.src
            return ns
        return m

    def fake_list(run, gh_cfg, fields, labels, state_="open", cap=200, sort="created", repo=None):
        state.list_calls.append((tuple(labels), cap, sort))
        if state.rows is None:
            return None
        return [dict(r) for r in state.rows[:cap]]

    monkeypatch.setattr(d, "_load_loop_script", load)
    monkeypatch.setattr(d, "_list", fake_list)
    state.d = d
    return state


def _need(doc):
    """Test-first precondition: the doctor scan exists. A named assertion, so the red before the feature is an
    assertion red (not an AttributeError the red gate ignores)."""
    assert hasattr(doc.d, "_released_blockers_scan"), "doctor._released_blockers_scan is missing (#2223)"


def _rows(src, numbers):
    return [{"number": n, "title": "", "body": src.issues[n]["body"],
             "labels": [{"name": l} for l in src.issues[n]["labels"]],
             "updatedAt": src.issues[n].get("updatedAt", "2026-01-01T00:00:00Z")} for n in numbers]


def _scan(doc, issues, numbers, cfg=None, **kw):
    doc.src = FakeSource(issues)
    doc.rows = _rows(doc.src, numbers)
    return doc.d._released_blockers_scan(GH, cfg or _cfg(), lambda a: "", "", now=NOW, **kw)


def test_doctor_reports_blocked_goals_whose_dependencies_are_all_closed(doc):
    _need(doc)
    res = _scan(doc, _world({40: _issue("blocked by #12"), 41: _issue("blocked by #14")}), [40, 41])
    assert [e[0] for e in res["eligible"]] == ["40"] and res["read"] == 2


def test_row_names_the_oldest_with_age_and_which_clock(doc):
    _need(doc)
    world = _world({40: _issue("blocked by #12", updatedAt=_iso(NOW - 3600)),
                    41: _issue("blocked by #13", updatedAt=_iso(NOW - 600))})
    world[12]["closedAt"] = _iso(NOW - 7200)                 # 40 waited on #12, closed 2h ago
    res = _scan(doc, world, [40, 41])                        # 41's #13 has no closedAt: falls back to updatedAt
    first, second = res["eligible"]
    assert first[0] == "40" and first[3] == "closedAt" and first[2] == "12" and NOW - first[1] == 7200
    assert second[0] == "41" and second[3] == "updatedAt" and second[2] is None


def test_row_states_its_read_ceiling_and_k_more_not_checked(doc):
    _need(doc)
    issues = _world({100 + i: _issue("blocked by #12", updatedAt=_iso(NOW - i)) for i in range(15)})
    res = _scan(doc, issues, list(range(100, 115)))
    assert res["checked"] == 10 and res["more"] == 5
    assert doc.list_calls == [(("sdlc:blocked",), 101, "updated")]          # one call, cap 100 + probe row
    many = _world({100 + i: _issue("blocked by #12") for i in range(101)})
    res = _scan(doc, many, list(range(100, 201)))
    assert res["ceiling_hit"] and res["read"] == 100 and res["more"] >= 1


def test_list_failure_skips_the_row_it_never_reports_false(doc):
    _need(doc)
    doc.src = FakeSource(_world({40: _issue("blocked by #12")}))
    doc.rows = None
    assert doc.d._released_blockers_scan(GH, _cfg(), lambda a: "", "", now=NOW) is None


def test_a_failed_comment_read_is_never_counted(doc):
    _need(doc)
    doc.src = FakeSource(_world({40: _issue("blocked by #12")}), fail=[("comments", "40")])
    doc.rows = _rows(doc.src, [40])
    res = doc.d._released_blockers_scan(GH, _cfg(), lambda a: "", "", now=NOW)
    assert res["eligible"] == []


def test_doctor_and_verb_share_one_filter(doc, monkeypatch):
    _need(doc)
    """The doctor must call the verb's own `release_candidates`, not carry a filter of its own."""
    seen = []
    real_load = doc.d._load_loop_script

    def load(name):
        m = real_load(name)
        if name == "release_dependents":
            orig = m.release_candidates
            m.release_candidates = lambda source, issues: (seen.append(len(issues)), orig(source, issues))[1]
        return m
    monkeypatch.setattr(doc.d, "_load_loop_script", load)
    _scan(doc, _world({40: _issue("blocked by #12")}), [40])
    assert seen == [1]


def test_doctor_ignores_blocked_and_parked_with_waiting_note(doc):
    _need(doc)
    parked = _issue("blocked by #12", labels=("sdlc:parked", "sdlc:blocked"),
                    comments=[("waiting <!-- sigma:waiting-on-dependency -->", "OWNER")])
    res = _scan(doc, _world({50: parked, 40: _issue("blocked by #12")}), [50, 40])
    assert [e[0] for e in res["eligible"]] == ["40"]


def test_row_is_read_only_nothing_written(doc, tmp_path):
    _need(doc)
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    doc.src = FakeSource(_world({40: _issue("blocked by #12")}))
    doc.rows = _rows(doc.src, [40])
    doc.d._released_blockers_scan(GH, _cfg(), lambda a: "", str(sdlc), now=NOW)
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
    assert [c for c in doc.src.calls if c[0] in ("swap", "note", "remove_label", "card")] == []


def test_sample_favours_the_stalest_goals_not_the_newest(doc):
    """Finding 6: of 15 blocked goals the 10 sampled are the LEAST recently updated; the one releasable goal
    is the stalest, so a newest-first sample would never see it."""
    _need(doc)
    issues = _world({**{100 + i: _issue("blocked by #14", updatedAt=_iso(NOW - 100 - i)) for i in range(1, 15)},
                     100: _issue("blocked by #12", updatedAt=_iso(NOW - 99_999))})
    res = _scan(doc, issues, list(range(100, 115)))
    assert [e[0] for e in res["eligible"]] == ["100"]


def test_doctor_row_holds_a_goal_the_map_holds_on_another_open_blocker(doc, tmp_path):
    """Finding 1 for the doctor: it must not promise a release the verb would refuse."""
    _need(doc)
    dagt = base.dagt
    dagt._store(tmp_path, [base._mapnode(40, "open", "blocked"), base._mapnode(13, "closed"),
                           base._mapnode(50, "open", "goal")], [(13, 40), (50, 40)])
    doc.src = FakeSource(_world({40: _issue("blocked by #13")}))
    doc.rows = _rows(doc.src, [40])
    res = doc.d._released_blockers_scan(GH, _cfg("on"), lambda a: "", str(tmp_path), now=NOW)
    assert res["eligible"] == []


def test_green_row_says_when_the_probe_row_was_not_read(doc, tmp_path, monkeypatch):
    _need(doc)
    d = doc.d
    monkeypatch.setattr(d, "_released_blockers_scan", lambda *a, **k: {
        "eligible": [], "read": 100, "ceiling_hit": True, "checked": 10, "more": 91, "max_issues": 10, "now": NOW})
    base_dir = td._sdlc(tmp_path, {"discovery": {"source": "github", "github": GH}})
    checks = {c["name"]: c for c in d.check(base_dir, run=td._bl_run([]))}
    row = next(c for n, c in checks.items() if n.startswith("no sdlc:blocked goal waits only on closed goals"))
    assert row["ok"] is True and "91 not checked" in row["name"]
    assert "least recently updated" in row["name"]


def test_a_refused_duty_state_is_not_healthy(doc, tmp_path):
    _need(doc)
    ok, text = doc.d._release_duty_state(
        _write_state(tmp_path, ran_at=NOW - 60, status="refused", starved_ticks=0), _cfg(), now=NOW)
    assert not ok and "refused" in text


def _write_state(tmp_path, **rec):
    st = tmp_path / ".sdlc" / "state"
    st.mkdir(parents=True, exist_ok=True)
    (st / "release-dependents.last.json").write_text(json.dumps(rec))
    return str(tmp_path / ".sdlc")


def test_row_reads_the_watcher_state_file_age_or_never_run(doc, tmp_path):
    _need(doc)
    ok, text = doc.d._release_duty_state(_write_state(tmp_path, ran_at=NOW - 120, status="done", starved_ticks=0),
                                         _cfg(), now=NOW)
    assert ok and "2m ago" in text
    ok, text = doc.d._release_duty_state(str(tmp_path / "nowhere"), _cfg(), now=NOW)
    assert not ok and "never run" in text
    ok, text = doc.d._release_duty_state(_write_state(tmp_path, ran_at=NOW - 86400, status="done"), _cfg(), now=NOW)
    assert not ok and "1d ago" in text                       # age is the tell


def test_row_says_off_when_mode_is_off(doc, tmp_path):
    _need(doc)
    cfg = _cfg()
    cfg["discovery"]["auto_unpark"]["mode"] = "off"
    ok, text = doc.d._release_duty_state(str(tmp_path / "nowhere"), cfg, now=NOW)
    assert ok and text.startswith("off") and "never run" not in text


def test_row_reports_starved_ticks(doc, tmp_path):
    _need(doc)
    ok, text = doc.d._release_duty_state(
        _write_state(tmp_path, ran_at=NOW - 60, status="deferred", starved_ticks=4), _cfg(), now=NOW)
    assert not ok and "starved_ticks 4" in text              # recent, yet not healthy: the count is shown


def test_check_wires_the_row_and_names_the_command(doc, tmp_path, monkeypatch):
    _need(doc)
    d = doc.d
    monkeypatch.setattr(d, "_released_blockers_scan", lambda *a, **k: {
        "eligible": [("40", NOW - 7200, "12", "closedAt")], "read": 3, "ceiling_hit": False, "checked": 3,
        "more": 2, "max_issues": 10, "now": NOW})
    base_dir = td._sdlc(tmp_path, {"discovery": {"source": "github", "github": GH}})
    checks = {c["name"]: c for c in d.check(base_dir, run=td._bl_run([]))}
    row = next(c for n, c in checks.items() if n.startswith("no sdlc:blocked goal waits only on closed goals"))
    assert row["ok"] is False and "#40" in row["fix"] and "2 more not checked" in row["fix"]
    assert "release_dependents.py .sdlc --all --apply" in row["fix"] and "10 least recently updated" in row["name"]


def test_a_starved_tick_followed_by_a_healthy_pass_leaves_the_row_green(doc, tmp_path):
    """Review r2 finding 1: `starved_ticks` counts CONSECUTIVE starved ticks; one healthy pass resets it, or the
    row stays red forever after a single starved tick."""
    _need(doc)
    rt = base._mod("reconcile_tick")
    d = _write_state(tmp_path, ran_at=NOW - 60, status="done", starved_ticks=0)
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    kw = dict(config=_cfg(), source=src, budget=120.0, clock=lambda: NOW, merged=[])
    rt._release_blocked(d, tick_start=NOW - 200, **kw)                 # starved once
    assert json.loads(pathlib.Path(d, "state", "release-dependents.last.json").read_text())["starved_ticks"] == 1
    ok, _ = doc.d._release_duty_state(d, _cfg(), now=NOW)
    assert not ok                                                       # red while it is starved
    rt._release_blocked(d, tick_start=NOW - 10, **kw)                  # then a healthy pass that ran
    ok, text = doc.d._release_duty_state(d, _cfg(), now=NOW)
    assert ok and "starved_ticks 0" in text, text


def test_a_stale_deferred_state_is_never_green(doc, tmp_path):
    """Review r2 finding 3: a `deferred` record is green only while recent; age is the tell."""
    _need(doc)
    ok, _ = doc.d._release_duty_state(_write_state(tmp_path, ran_at=NOW - 60, status="deferred", starved_ticks=0),
                                      _cfg(), now=NOW)
    assert ok
    ok, text = doc.d._release_duty_state(_write_state(tmp_path, ran_at=NOW - 86400, status="deferred", starved_ticks=0),
                                         _cfg(), now=NOW)
    assert not ok and "ago" in text

"""#2002 (FR-31/35/97): a 1.0.x project survives the lean release unchanged -- config, goals, log, gates.

Fixtures are built by tests/upgrade_fixtures.py with the real CLIs. Nothing here asserts any lean
behaviour (none ships in slice 1); these pin what the upgrade must NOT disturb.

Plan-review binding (refinement 1): asserted through the 1.0.x-true source -- the action-log `verdict`
row's `plan_hash` == sha256(plan), and `phase_gate.status` refusing once the plan is edited. The
`gates/<stem>.json` record needs work.enabled + a work record + git (`_plan_review_refusal`); the
fixture project has none, so that file is not the source asserted here. Goes red under control: the
plan-edit comparison in phase_gate is stubbed out.

FR-97 matrix: the SLICE-1 SUBSET only -- the plan-review and mid-implement fixtures, ledger on/off
(2 cells x 2 fixtures, not the 16-way cross). The full lean gate matrix is slice 5b (design 2000.md,
slice table row 5b); no lean behaviour is asserted because none exists yet."""
import hashlib
import importlib.util
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import upgrade_fixtures as u  # noqa: E402

SKILLS = pathlib.Path(__file__).resolve().parent.parent / "skills"


def _mod(name, sub="sigma-loop"):
    path = SKILLS / sub / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name + "_t", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, m)
    spec.loader.exec_module(m)
    return m


state, actionlog, ledger, work, phase_gate = (_mod(n) for n in ("state", "actionlog", "ledger", "work", "phase_gate"))
doctor = _mod("doctor", "sigma-doctor")


def _rows(p):
    return actionlog.read_goal(str(p / u.SDLC), u.GOAL)


def _status(p, cfg):
    return {ph: (ok, detail) for ph, ok, detail, _ in phase_gate.status(str(p / u.SDLC), cfg, u.GOAL)["rows"]}


# --------------------------------------------------------------------------- (a) config, FR-31

def test_config_loads_unchanged_with_unknown_keys_kept_and_new_keys_not_injected(tmp_path):
    p = u.mid_implement_goal(tmp_path)
    cfg_file = p / u.SDLC / "config.json"
    loaded = state.load_config(str(p / u.SDLC))
    assert loaded == json.loads(cfg_file.read_text())
    assert loaded["x_custom_key"] == {"owner": "dana", "n": 3} and loaded["team_notes"]
    assert "action_log" not in loaded and "phase_record" not in loaded["gates"]
    assert loaded["gates"]["x_custom_gate"] == {"enabled": False}
    # the label vocabulary an old repo carries is untouched by the loader
    gh = loaded["discovery"]["github"]
    assert (gh["goal_label"], gh["in_progress_label"], gh["parked_label"]) == (
        "sdlc:goal", "sdlc:in-progress", "sdlc:parked")


# --------------------------------------------------------------------------- (b) mid-implement

def test_mid_implement_goal_is_readable_and_finishes_under_the_existing_gate(tmp_path):
    p = u.mid_implement_goal(tmp_path)
    rows = _rows(p)
    phases = [(r["phase"], r["state"]) for r in rows if r["kind"] == "phase"]
    assert phases[-1] == ("implement", "start") and ("implement", "end") not in phases
    assert [ph for ph, st in phases if st == "end"] == ["research", "plan", "plan_review"]
    on = u.gates_on(json.loads((p / u.SDLC / "config.json").read_text()))
    st = _status(p, on)
    assert [st[x][0] for x in ("research", "plan", "plan_review")] == [True] * 3
    assert st["implement"][0] is False and "no phase end" in st["implement"][1]
    # the old route continues: end implement under the writer's gates-on config, the row turns green
    u.write_config(p, on)
    assert u.g.run(p, "phase_report.py", "end", u.SDLC, u.GOAL, "implement", "--agent-id", "a3").returncode == 0
    assert _status(p, on)["implement"][0] is True


# --------------------------------------------------------------------------- (c) plan-review binding

def test_plan_review_verdict_is_bound_to_the_plan_bytes_and_goes_stale_on_edit(tmp_path):
    p = u.plan_review_goal(tmp_path)
    plan = p / u.SDLC / "plans" / f"{u.STEM}.md"
    verdicts = [r for r in _rows(p) if r["kind"] == "verdict" and r["phase"] == "plan_review"]
    assert len(verdicts) == 1 and verdicts[0]["verdict"] == "pass"
    assert verdicts[0]["plan_hash"] == hashlib.sha256(plan.read_bytes()).hexdigest()
    on = u.gates_on(json.loads((p / u.SDLC / "config.json").read_text()))
    assert _status(p, on)["plan_review"][0] is True
    plan.write_text(plan.read_text() + "\n- an edit after the review\n")
    ok, detail = _status(p, on)["plan_review"]
    assert ok is False and "edited after its review" in detail


# --------------------------------------------------------------------------- (d) 1.0.x log, FR-35

def test_old_shaped_log_reads_and_a_new_row_is_additive(tmp_path):
    p = u.g._project(tmp_path)
    f = u.old_action_log(p)
    before = f.read_bytes()
    assert [r["phase"] for r in _rows(p)] == ["research", "research"]
    assert all(set(r) == {"ts", "kind", "actor", "thread", "phase", "state"} for r in _rows(p))
    on = u.gates_on(json.loads((p / u.SDLC / "config.json").read_text()))
    assert _status(p, on)["research"][0] is True                    # the gate reads old rows as-is
    actionlog.append(str(p / u.SDLC), u.GOAL, "phase", "loop", phase="plan", state="start", now=None)
    after = f.read_bytes()
    assert after.startswith(before) and len(after) > len(before)    # old bytes untouched, one row added
    assert [r["phase"] for r in _rows(p)] == ["research", "research", "plan"]


# --------------------------------------------------------------------------- defaults read, never written

def test_defaults_read_never_written(tmp_path, monkeypatch):
    p = u.mid_implement_goal(tmp_path)
    sdlc = str(p / u.SDLC)
    cfg_file = p / u.SDLC / "config.json"
    cfg_snap = (cfg_file.read_bytes(), cfg_file.stat().st_mtime_ns)
    calls = []
    # features() records a timing row under state/time on its own (pre-existing, not config): run it
    # first so the listing snapshot below judges only what the readers and the new code do.
    doctor.features(sdlc, run=lambda a: calls.append(a) or "")
    listing = u.tree(p)
    cfg = state.load_config(sdlc)
    actionlog.enabled(cfg); ledger.enabled(cfg); work._plan_review_on(cfg)
    doctor._gate_enabled(cfg["gates"].get("phase_record")); doctor._enforce_enabled(cfg["verify"])
    doctor._review_gate_state(cfg.get("work") or {})
    doctor.check(sdlc, run=lambda a: calls.append(a) or "", cheap_only=True)
    n = len(calls)
    def boom(*a, **k):
        raise AssertionError("the changes report spawned a process")
    monkeypatch.setattr(doctor.subprocess, "run", boom)
    monkeypatch.setattr(doctor.subprocess, "Popen", boom)
    changes, overrides = doctor.lean_changes(sdlc)
    assert doctor._print_lean_changes(sdlc) == 0
    monkeypatch.undo()
    assert len(calls) == n                                          # the new code makes no probe call
    assert not any("lean" in " ".join(map(str, c)) for c in calls)
    assert changes and overrides is not None
    assert cfg_file.read_bytes() == cfg_snap[0]                     # bytes
    assert cfg_file.stat().st_mtime_ns == cfg_snap[1]               # mtime (catches an identical rewrite)
    assert u.tree(p) == listing                                     # no file appeared under .sdlc


# --------------------------------------------------------------------------- FR-97 slice-1 subset

def _signature(p, cfg):
    st = phase_gate.status(str(p / u.SDLC), cfg, u.GOAL)["rows"]
    log = [(r["kind"], r.get("phase"), r.get("state")) for r in _rows(p)]
    return ([(ph, ok) for ph, ok, _, _ in st], bool(phase_gate.refusal(str(p / u.SDLC), cfg, u.GOAL)), log)


@pytest.mark.parametrize("fixture", ["plan_review_goal", "mid_implement_goal"])
def test_gates_behave_identically_with_the_ledger_on_and_off(tmp_path, fixture):
    sigs = {}
    for ledger_on in (True, False):
        p = getattr(u, fixture)(tmp_path / ("on" if ledger_on else "off"), ledger=ledger_on)
        comp = u.gates_on(json.loads((p / u.SDLC / "config.json").read_text()), ledger=ledger_on)
        # the composite really has every gate on (a silently-off gate would make equality vacuous)
        assert actionlog.enabled(comp) and work._plan_review_on(comp) and phase_gate.gate_on(comp)
        assert doctor._enforce_enabled(comp["verify"]) is True
        assert ledger.enabled(comp) is ledger_on
        sigs[ledger_on] = _signature(p, comp)
        events = list((p / u.SDLC / "events").glob("*.jsonl")) if (p / u.SDLC / "events").exists() else []
        assert bool(events) is ledger_on                            # on writes ledger events, off writes none
    assert sigs[True] == sigs[False]
    assert sigs[True][0][2] == ("plan_review", True)                # the gate really evaluated, not vacuous

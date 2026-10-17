"""#1225: after Sigma's own successful merge, the upkeep scheduler's due-check runs once (the trigger). Fakes only: no
network, no real job. The merge fixtures are test_work's; the scheduler fixtures are test_upkeep_sched's."""
import ast
import json
import pathlib
import subprocess
import sys

import pytest

import upkeep_support as S
from test_work import (SCRIPTS, NOSLEEP, work, _sdlc, _started, _evidence, _merge_run, _landing_cfg)
from test_upkeep_sched import sched, project, open_config, tick, ENV

OPEN = {**_landing_cfg(), "upkeep": {"enabled": True}}


def _merge(tmp_path, cfg, monkeypatch, behaviour=None):
    calls = []

    def fake(argv):
        calls.append(list(argv))
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour if behaviour is not None else 0

    monkeypatch.setattr(work, "_upkeep_trigger_call", fake)
    d = _sdlc(tmp_path, cfg)
    goal = _started(d, goal="1642.md")
    _evidence(d, goal)
    run = _merge_run(__import__("prfake").rest_merged())
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    return out, calls, run.calls


# ------------------------------------------------------------------------------------------ the merge side

def test_gate_closed_makes_no_call_and_changes_nothing(tmp_path, monkeypatch, capsys):
    out, calls, git = _merge(tmp_path, _landing_cfg(), monkeypatch)
    assert calls == [] and out.startswith("PR #7 merged (squash) — ")
    assert capsys.readouterr().err == ""
    out2, _, git2 = _merge(tmp_path / "again", {**_landing_cfg(), "upkeep": {"enabled": False}}, monkeypatch)
    assert out2 == out and git2 == git


def test_gate_open_runs_one_timed_decision_after_the_landing(tmp_path, monkeypatch):
    plain, _, _ = _merge(tmp_path / "a", _landing_cfg(), monkeypatch)
    out, calls, _ = _merge(tmp_path / "b", OPEN, monkeypatch)
    assert out == plain                                   # the merge's own line is untouched
    assert len(calls) == 1
    argv = calls[0]
    assert pathlib.Path(argv[1]).name == "run_with_timeout.py"
    assert pathlib.Path(argv[3]).name == "feature_upkeep_sched.py" and argv[4] == "decision"


@pytest.mark.parametrize("behaviour", [RuntimeError("boom"), 124, 1])
def test_a_failed_or_timed_out_trigger_never_changes_the_merge(tmp_path, monkeypatch, capsys, behaviour):
    plain, _, _ = _merge(tmp_path / "a", _landing_cfg(), monkeypatch)
    capsys.readouterr()
    out, calls, _ = _merge(tmp_path / "b", OPEN, monkeypatch, behaviour)
    assert out == plain and len(calls) == 1
    err = [l for l in capsys.readouterr().err.splitlines() if "upkeep trigger" in l]
    assert len(err) == 1, err                              # logged once


def test_no_trigger_when_the_merge_is_not_a_landing(tmp_path, monkeypatch):
    cfg = {**OPEN, "work": {"enabled": True, "auto_merge": "off"}}
    out, calls, _ = _merge(tmp_path, cfg, monkeypatch)
    assert not out.startswith("PR #7 merged") and calls == []


def test_control_the_trigger_call_is_in_merge_and_removing_it_is_seen():
    """Mutation control: the same test, run against merge() with the call removed, must fail.

    #1647: the trigger lives in `_post_landing`, which BOTH of merge()'s landing paths call (the
    already-merged short-circuit and the direct PUT), so the chain merge -> _post_landing -> trigger is
    what is checked, with each edge removed alone. Exactly 2 calls, not membership: membership would
    stay green with either landing path's call removed."""
    src = (SCRIPTS / "work.py").read_text(encoding="utf-8")

    def fn(tree, name):
        return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)

    def count(f, name):
        return sum(isinstance(n, ast.Call) and getattr(n.func, "id", "") == name for n in ast.walk(f))

    def reaches(tree):
        return (count(fn(tree, "merge"), "_post_landing") == 2
                and count(fn(tree, "_post_landing"), "_upkeep_merge_trigger") == 1)

    assert reaches(ast.parse(src))
    # A: the trigger edge (also renames the trigger's own def, harmlessly)
    assert not reaches(ast.parse(src.replace("_upkeep_merge_trigger(", "_unrelated_name(")))
    # B: the first merge edge (the already-merged short-circuit's call)
    assert not reaches(ast.parse(src.replace("closed = _post_landing(", "closed = _unrelated_name(", 1)))
    # C: the last merge edge (the direct PUT's call)
    head, sep, tail = src.rpartition("closed = _post_landing(")
    assert sep, "merge() no longer writes its landing calls as `closed = _post_landing(`"
    assert not reaches(ast.parse(head + "closed = _unrelated_name(" + tail))   # the detector is not blind


# ------------------------------------------------------------------------------------------ the scheduler side

def test_trigger_starts_the_window_sweep_once_and_leaves_tick_liveness_alone(tmp_path):
    m, sdlc, spawned = sched(), project(tmp_path), []
    first = tick(m, open_config(), sdlc, ["a"], {"a": 4}, spawned, started_by="trigger")
    second = tick(m, open_config(), sdlc, ["a"], {"a": 4}, spawned, now=1_000_001, started_by="trigger")
    assert first["decision"] == "started" and spawned == ["*"], (first, second, spawned)   # the window: one sweep, not two
    assert not pathlib.Path(sdlc, m.STATUS_REL).exists()                                    # no masked tick liveness
    assert json.loads(pathlib.Path(sdlc, m.LAST_START_REL).read_text())["by"] == "trigger"


def test_without_the_trigger_the_sweep_starts_only_on_the_next_tick(tmp_path):
    m, sdlc, spawned = sched(), project(tmp_path), []
    assert spawned == [] and not pathlib.Path(sdlc, m.LAST_START_REL).exists()   # a merge happened; nothing ran
    tick(m, open_config(), sdlc, ["a"], {"a": 4}, spawned, now=1_000_900)        # the next tick
    assert spawned == ["*"]
    assert json.loads(pathlib.Path(sdlc, m.LAST_START_REL).read_text())["by"] == "tick"
    assert json.loads(pathlib.Path(sdlc, m.STATUS_REL).read_text())["decision"] == "started"


def test_doctor_row_shows_who_started_the_last_pass(tmp_path):
    m, sdlc, spawned = sched(), project(tmp_path), []
    assert "upkeep last start" not in dict((l, d) for l, _, d in m.health(open_config(), sdlc, 1_000_000))
    tick(m, open_config(), sdlc, ["a"], {"a": 4}, spawned, started_by="trigger")
    rows = dict((l, d) for l, _, d in m.health(open_config(), sdlc, 1_000_010))
    assert "trigger" in rows["upkeep last start"] and "*" in rows["upkeep last start"]


def test_cli_decision_with_the_gate_closed_prints_nothing(tmp_path):
    sdlc = project(tmp_path, S.template_cfg())
    proc = subprocess.run([sys.executable, str(SCRIPTS / "feature_upkeep_sched.py"), "decision", sdlc],
                          capture_output=True, text=True, timeout=60, env={"PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0 and proc.stdout == "" and proc.stderr == "", proc
    assert not pathlib.Path(sdlc, "state").exists()

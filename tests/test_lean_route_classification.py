"""#2003 (lean goals slice 2; FR-33, FR-34, FR-53, FR-96, BR-39, BR-55): the route marker at classification.

- `loop.py classify` records lean/1 + profile + rationale in the work record, the action log and a timeline
  marker (FR-34, FR-53); a fresh `work.py start` cut defaults to lean/1 `standard` (FR-53).
- Cutover (FR-33): a goal with approving plan-review evidence and no route marker stays legacy -- classify
  refuses it and a fresh start does not mark it.
- FR-96: an absent `action_log` key reads as ON for a lean goal, OFF for a legacy one; explicit false wins.
- The folded review (a gates record marked `fold`) is accepted by both plan gates for trivial/small lean goals
  only, never under an org lock, and bound to the plan when one exists (BR-55).
Gestures are the documented ones (`loop.py classify`, `phase_report.py start`) run as real CLIs, plus
`work.start` and the two `pr()` gates in-process with a fake `run`, as tests/test_work.py does."""
import hashlib
import importlib.util
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import test_phase_record_gate as g  # noqa: E402
import upgrade_fixtures as u  # noqa: E402

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_lr", S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


work, state, actionlog = _load("work"), _load("state"), _load("actionlog")
STEM = "0001-x"
FOLD = {"fold": "test_review_pre", "route": "lean/1", "verdict": "pass"}


# --------------------------------------------------------------------------- helpers

def _cfg(p, cfg):
    (p / ".sdlc" / "config.json").write_text(json.dumps(cfg))


def _classify(p, profile="small", rationale="one file, one test"):
    args = ["classify", ".sdlc", g.GOAL, profile] + (["--rationale", rationale] if rationale is not None else [])
    return g.run(p, "loop.py", *args)


def _kinds(p, kind):
    return [r for r in g.log_rows(p) if r.get("kind") == kind]


def _journey(p):
    f = p / ".sdlc" / "journey" / f"{STEM}.md"
    return f.read_text() if f.exists() else ""


def _work_record(sdlc, extra=None):
    f = pathlib.Path(sdlc) / "state" / "work" / f"{STEM}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"worktree": str(pathlib.Path(sdlc).parent / "wt"), "branch": f"sdlc/{STEM}",
                             "base": "main", "remote": "origin", "pr": "", **(extra or {})}))
    return f


def _gates(sdlc, record):
    f = pathlib.Path(sdlc) / "state" / "gates" / f"{STEM}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(record))


def _runner(handlers=()):
    calls = []

    def run(cwd, argv):
        line = " ".join(str(a) for a in argv)
        calls.append(line)
        for token, resp in handlers:
            if token in line:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return ""
    run.calls = calls
    return run


def _sdlc(tmp_path, cfg):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(cfg))
    state.start_run(str(d))
    return str(d)


def _rows(sdlc):
    return actionlog.read_goal(sdlc, f"{STEM}.md")


# --------------------------------------------------------------------------- classify (FR-34, FR-53, FR-33)

def test_classify_cli_records_route_in_log_and_timeline(tmp_path):
    p = g._project(tmp_path, gate=None)
    r = _classify(p)
    assert r.returncode == 0, r.stderr
    rows = _kinds(p, "goal_class")
    assert len(rows) == 1
    assert {k: rows[0].get(k) for k in ("route", "profile", "rationale", "source")} == {
        "route": "lean/1", "profile": "small", "rationale": "one file, one test", "source": "agent"}
    note = _journey(p)
    assert "sigma:route lean/1 profile=small" in note and "one file, one test" in note


def test_classify_refuses_unknown_profile_and_empty_rationale(tmp_path):
    p = g._project(tmp_path, gate=None)
    for profile, rationale in (("medium", "x"), ("small", "  "), ("small", None)):
        r = _classify(p, profile, rationale)
        assert r.returncode == 2, (profile, rationale, r.stderr)
        assert all(w in r.stderr for w in ("trivial", "small", "standard", "heavy"))
    assert not _kinds(p, "goal_class") and _journey(p) == ""


def test_classify_refuses_a_legacy_goal_fr33(tmp_path):
    p = u.mid_implement_goal(tmp_path)
    r = _classify(p)
    assert r.returncode == 2 and "legacy" in r.stderr and "FR-33" in r.stderr, r.stderr
    assert not _kinds(p, "goal_class") and _journey(p) == ""
    assert work.route_of(str(p / ".sdlc"), g.GOAL) is None


def test_classify_refuses_a_legacy_goal_by_its_gates_record_alone(tmp_path):
    """The other `_legacy_evidence` clause, on the classify path: no verdict row, an approving gates record."""
    p = g._project(tmp_path, gate=None)
    _gates(p / ".sdlc", {"verdict": "warn", "plan_hash": "a" * 64})
    r = _classify(p)
    assert r.returncode == 2 and "FR-33" in r.stderr, r.stderr
    assert not _kinds(p, "goal_class")


def test_reclassifying_a_lean_goal_with_a_plan_review_verdict_succeeds(tmp_path):
    """Verdict rows never carry a route, so the 'not lean AND' clause is what lets a lean goal re-classify."""
    p = g._project(tmp_path, gate=None)
    assert _classify(p, "standard", "first look").returncode == 0
    g.plan_review(p)
    r = _classify(p, "heavy", "touches the merge path")
    assert r.returncode == 0, r.stderr
    assert work.route_of(str(p / ".sdlc"), g.GOAL)["profile"] == "heavy"


def test_classify_refuses_a_goal_recorded_done(tmp_path):
    p = g._project(tmp_path, gate=None)
    assert _classify(p).returncode == 0
    actionlog.safe_append(str(p / ".sdlc"), g.GOAL, "recorded", result="done")
    r = _classify(p, "heavy", "late")
    assert r.returncode == 2 and "recorded" in r.stderr, r.stderr
    assert len(_kinds(p, "goal_class")) == 1


def test_classify_refuses_after_merge_armed(tmp_path):
    p = g._project(tmp_path, gate=None)
    assert _classify(p).returncode == 0
    actionlog.safe_append(str(p / ".sdlc"), g.GOAL, "merge_armed", pr="7")
    assert _classify(p, "heavy", "late").returncode == 2
    assert len(_kinds(p, "goal_class")) == 1


# --------------------------------------------------------------------------- FR-96

def test_absent_action_log_reads_on_for_a_lean_goal_fr96(tmp_path):
    p = g._project(tmp_path, gate=None)
    _cfg(p, {})
    assert _classify(p).returncode == 0
    assert g.run(p, "phase_report.py", "start", ".sdlc", g.GOAL, "research", "--model", "sonnet").returncode == 0
    assert [r["phase"] for r in _kinds(p, "phase")] == ["research"]


def test_absent_action_log_stays_off_for_a_legacy_goal(tmp_path):
    p = g._project(tmp_path, gate=None)
    _cfg(p, {})
    assert g.run(p, "phase_report.py", "start", ".sdlc", g.GOAL, "research", "--model", "sonnet").returncode == 0
    assert g.log_rows(p) == []


def test_explicit_false_action_log_is_respected_for_a_lean_goal(tmp_path):
    # no work record: nothing can hold the route -> refused, no note, no rows
    p = g._project(tmp_path / "a", gate=None, action_log=False)
    r = _classify(p)
    assert r.returncode == 2 and "nothing can hold the route" in r.stderr, r.stderr
    assert _journey(p) == "" and g.log_rows(p) == []
    # a work record holds it: accepted with a warning, still no rows
    q = g._project(tmp_path / "b", gate=None, action_log=False)
    rec = _work_record(q / ".sdlc")
    r = _classify(q)
    assert r.returncode == 0 and "action_log" in r.stderr, r.stderr
    assert json.loads(rec.read_text())["route"] == "lean/1" and g.log_rows(q) == []
    assert g.run(q, "phase_report.py", "start", ".sdlc", g.GOAL, "research", "--model", "x").returncode == 0
    assert g.log_rows(q) == []


# --------------------------------------------------------------------------- work.start (FR-53 default)

ON = {"work": {"enabled": True}}


def test_fresh_start_records_lean_route_with_standard_default(tmp_path):
    d = _sdlc(tmp_path, ON)                         # no action_log key: FR-96 reads it on for the lean goal
    work.start(d, ON, f"{STEM}.md", run=_runner([("rev-parse", "main")]))
    rec = work._record(d, f"{STEM}.md")
    assert (rec.get("route"), rec.get("profile"), rec.get("profile_source")) == ("lean/1", "standard", "default")
    kinds = [r["kind"] for r in _rows(d)]
    assert kinds[:2] == ["goal_class", "worktree_start"], kinds


def test_fresh_start_of_a_goal_with_legacy_evidence_stays_legacy(tmp_path):
    d = _sdlc(tmp_path, ON)
    _gates(d, {"verdict": "pass", "plan_hash": "b" * 64})
    work.start(d, ON, f"{STEM}.md", run=_runner([("rev-parse", "main")]))
    assert "route" not in work._record(d, f"{STEM}.md")
    # the other clause: an approving plan_review verdict row in the log (action log on)
    on = {**ON, "action_log": {"enabled": True}}
    e = _sdlc(tmp_path / "e", on)
    actionlog.safe_append(e, f"{STEM}.md", "verdict", phase="plan_review", verdict="pass")
    work.start(e, on, f"{STEM}.md", run=_runner([("rev-parse", "main")]))
    assert "route" not in work._record(e, f"{STEM}.md")
    assert not [r for r in _rows(e) if r["kind"] == "goal_class"]


def test_fresh_start_keeps_a_classification_made_before_start(tmp_path):
    """FR-53: `standard` is the default only when NO profile is recorded. `loop.py classify` (work.classify)
    accepts a goal with no work record and writes only a `goal_class` row; a fresh start must carry that
    judgment, not overwrite it with a newer default `standard` row."""
    d = _sdlc(tmp_path, ON)
    work.classify(d, ON, f"{STEM}.md", "heavy", "touches the merge path")
    work.start(d, ON, f"{STEM}.md", run=_runner([("rev-parse", "main")]))
    rec = work._record(d, f"{STEM}.md")
    assert (rec.get("route"), rec.get("profile"), rec.get("rationale")) == (
        "lean/1", "heavy", "touches the merge path")
    assert rec.get("profile_source") not in (None, "default"), rec
    classes = [r for r in _rows(d) if r["kind"] == "goal_class"]
    assert [r.get("profile") for r in classes] == ["heavy"], classes
    assert work.route_of(d, f"{STEM}.md")["profile"] == "heavy"


def test_reattach_with_record_carries_route_fields(tmp_path):
    d = _sdlc(tmp_path, ON)
    _work_record(d, {"route": "lean/1", "profile": "small", "profile_source": "agent", "rationale": "docs only"})
    work.start(d, ON, f"{STEM}.md",
               run=_runner([("rev-parse", "main"), ("worktree add -b", RuntimeError("already exists"))]))
    rec = work._record(d, f"{STEM}.md")
    assert (rec.get("route"), rec.get("profile"), rec.get("rationale")) == ("lean/1", "small", "docs only")


# --------------------------------------------------------------------------- folded review (BR-55)

PLAN_GATE = {"work": {"enabled": True}, "gates": {"plan_review": {"enabled": True}}}


def _lean(sdlc, profile="small"):
    return _work_record(sdlc, {"route": "lean/1", "profile": profile, "rationale": "r"})


def _plan(sdlc, text="# Plan\n"):
    f = pathlib.Path(sdlc) / "plans" / f"{STEM}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return hashlib.sha256(f.read_bytes()).hexdigest()


def _review_gate(sdlc):
    return work._plan_review_refusal(sdlc, PLAN_GATE, None, f"{STEM}.md", _runner())


def test_fold_record_accepted_by_plan_review_gate_for_small_lean_goal(tmp_path):
    d = _sdlc(tmp_path, PLAN_GATE)
    _lean(d)
    _gates(d, FOLD)
    assert _review_gate(d) == ""                     # no plan: the fold stands in (red on base)
    sha = _plan(d)
    _gates(d, {**FOLD, "plan_hash": sha})
    assert _review_gate(d) == ""                     # a plan: bound by its sha


def _hard(sdlc, memo="on:unlocked"):
    rec = {"worktree": str(pathlib.Path(sdlc).parent), "remote": "origin", "base": "main", "hard_plan_gate": memo}
    work._save(sdlc, f"{STEM}.md", {**json.loads(work.record_path(sdlc, f"{STEM}.md").read_text()), **rec})
    return work._hard_plan_gate_refusal(sdlc, PLAN_GATE, rec, f"{STEM}.md", _runner([("diff --name-only", "a.py")]))


def test_fold_record_stands_in_for_plan_in_hard_plan_gate(tmp_path):
    d = _sdlc(tmp_path, PLAN_GATE)
    _lean(d)
    _gates(d, FOLD)
    assert _hard(d) == ""


def test_fold_is_refused_for_a_standard_profile(tmp_path):
    d = _sdlc(tmp_path, PLAN_GATE)
    _lean(d, "standard")
    _gates(d, FOLD)
    assert _review_gate(d) != "" and _hard(d) != ""


def test_fold_is_refused_for_a_legacy_goal(tmp_path):
    d = _sdlc(tmp_path, PLAN_GATE)
    _work_record(d)
    _gates(d, FOLD)
    assert _review_gate(d) != "" and _hard(d) != ""


def test_fold_is_refused_under_an_org_lock(tmp_path):
    d = _sdlc(tmp_path, PLAN_GATE)
    _lean(d)
    _gates(d, FOLD)
    assert "org-locked" in _hard(d, memo="on:locked")


def test_fold_is_refused_on_a_plan_hash_mismatch(tmp_path):
    d = _sdlc(tmp_path, PLAN_GATE)
    _lean(d)
    _plan(d)
    _gates(d, {**FOLD, "plan_hash": "c" * 64})
    assert _review_gate(d) != ""


def test_fold_is_refused_with_a_plan_but_no_plan_hash(tmp_path):
    d = _sdlc(tmp_path, PLAN_GATE)
    _lean(d)
    _plan(d)
    _gates(d, FOLD)
    assert _review_gate(d) != ""


def test_routed_record_without_the_fold_key_does_not_stand_in(tmp_path):
    """A lean small goal's ordinary plan-review record (it carries `route`, BR-39) is not a fold."""
    d = _sdlc(tmp_path, PLAN_GATE)
    _lean(d)
    _gates(d, {k: v for k, v in FOLD.items() if k != "fold"})
    assert _review_gate(d) != "" and _hard(d) != ""


def test_blocking_fold_does_not_stand_in(tmp_path):
    d = _sdlc(tmp_path, PLAN_GATE)
    _lean(d)
    _gates(d, {**FOLD, "verdict": "block"})
    assert _review_gate(d) != "" and _hard(d) != ""


# --------------------------------------------------------------------------- BR-39

def test_plan_review_record_carries_route_for_a_lean_goal(tmp_path, monkeypatch):
    cfg = {"work": {"enabled": True}, "review": {"independent": False}}
    for name, profile in (("lean", "small"), ("legacy", None)):
        d = _sdlc(tmp_path / name, cfg)
        _work_record(d, {"route": "lean/1", "profile": profile, "rationale": "r"} if profile else None)
        sha = _plan(d)
        monkeypatch.setattr(work, "plan_copies", lambda *a, **k: (pathlib.Path(d) / "plans" / f"{STEM}.md", None))
        out = work.record_plan_review(d, cfg, f"{STEM}.md", "SOUND", sha)
        written = json.loads(pathlib.Path(out["path"]).read_text())
        if profile:
            assert written.get("route") == "lean/1"
        else:
            assert "route" not in written

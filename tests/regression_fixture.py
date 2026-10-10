"""Synthetic green run + the three mutated copies for the regression checker (#877). NOT a test file.

Builds a hand-made run directory (the shape `evals/regression/record.py` documents), runs the
DOCUMENTED `record.py build` on it as a subprocess, and exposes three pure mutators that each delete
exactly one piece of evidence from the green record. The committed fixtures under
`evals/regression/fixtures/` are written by THIS module through `record.write_atomic`, the same
function `record.py build` writes with, so byte-compare tests depend on one writer, not two.

A synthetic run proves the checker's logic, not that Sigma behaves: nothing here was measured
against a live run.

Regenerate the committed copies (fixed run directory name `run`, fixed timestamps and commit dates):

    python3 tests/regression_fixture.py evals/regression/fixtures
"""
import copy
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECORD = ROOT / "evals" / "regression" / "record.py"
GOAL = "877"
PHASES = ("research", "plan", "plan_review", "implement", "review", "retro")
EVIDENCE_ID = "evidence-id-0123456789abcdef"
FIXTURE_FILES = {
    "run-green.json": None,
    "run-drop-verdict.json": "mutate_drop_verdict",
    "run-drop-phase-end.json": "mutate_drop_phase_end",
    "run-drop-marker.json": "mutate_drop_marker",
}

_spec = importlib.util.spec_from_file_location("regression_record_for_fixture", RECORD)
record = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(record)


def _ts(i):
    return "2026-01-01T00:00:%02d.000Z" % i


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")


def _git_env(date=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return env


def _git(cwd, *args, date=None):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"] + list(args),
                   cwd=str(cwd), env=_git_env(date), check=True, capture_output=True, text=True)


def _build_remote(run, work):
    """A bare remote whose goal branch holds two commits, both already on main (the goal has landed)."""
    remote = run / "remote.git"
    work.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], env=_git_env(), check=True)
    _git(work, "init", "-q")
    _git(work, "checkout", "-q", "-b", "main")
    (work / "a.txt").write_text("a")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-q", "-m", "first on main", date="2026-01-01T00:00:00+0000")
    _git(work, "checkout", "-q", "-b", "sdlc/877-regression-checker")
    (work / "b.txt").write_text("b")
    _git(work, "add", "b.txt")
    _git(work, "commit", "-q", "-m", "goal work", date="2026-01-01T00:00:01+0000")
    _git(work, "push", "-q", str(remote), "sdlc/877-regression-checker")
    _git(work, "checkout", "-q", "main")
    _git(work, "merge", "-q", "--ff-only", "sdlc/877-regression-checker")
    _git(work, "push", "-q", str(remote), "main")


def build_green_run(tmp_dir):
    """Write the synthetic run under `<tmp_dir>/run` (the directory name is part of the record) and return it."""
    tmp_dir = pathlib.Path(tmp_dir)
    run = tmp_dir / "run"
    sdlc = run / "repo" / ".sdlc"
    sdlc.mkdir(parents=True)
    (sdlc / "plans").mkdir()
    (sdlc / "research").mkdir()
    (sdlc / "plans" / (GOAL + ".md")).write_text("plan\n", encoding="utf-8")
    (sdlc / "research" / (GOAL + ".md")).write_text("research\n", encoding="utf-8")
    config = {"action_log": {"enabled": True}, "journal": {"enabled": True}, "ledger": {"actor": "tester"}}
    (sdlc / "config.json").write_text(json.dumps(config, sort_keys=True) + "\n", encoding="utf-8")

    rows, i = [{"kind": "claimed"}], 1
    for ph in PHASES:
        rows.append({"kind": "phase", "phase": ph, "state": "start"})
        rows.append({"kind": "phase", "phase": ph, "state": "end"})
        if ph == "plan_review":
            rows.append({"kind": "verdict", "phase": "plan_review", "verdict": "approve"})
        if ph == "review":
            rows.append({"kind": "verdict", "phase": "review", "verdict": "approve"})
    rows += [{"kind": "recorded", "result": "done"}, {"kind": "merged"}]
    log = [dict({"ts": _ts(n), "goal": GOAL, "thread": "main", "actor": "loop"}, **r) for n, r in enumerate(rows)]
    _jsonl(sdlc / "state" / "log" / (GOAL + ".jsonl"), log)

    ledger = record._load_scripts("ledger")
    for ph in PHASES:
        ledger.append(str(sdlc), config, "phase", GOAL, stream=ledger.EVENTS, phase=ph, state="start",
                      attempt_id="att-" + ph)
        ledger.append(str(sdlc), config, "phase", GOAL, stream=ledger.EVENTS, phase=ph, state="end",
                      tokens_in="1200", tokens_out="34", attempt_id="att-" + ph, model="m")
        ledger.append(str(sdlc), config, "spend", GOAL, stream=ledger.EVENTS, phase=ph,
                      tokens_in="1200", tokens_out="34", cost_cents="45", attempt_id="att-" + ph)
    key = "a" * 64
    ledger.append(str(sdlc), config, "review_posted", GOAL, stream=ledger.EVENTS, phase="review",
                  observation_key=key, brief_hash=key, evidence_id=EVIDENCE_ID, comment_id=1, pr=1,
                  head_sha="b" * 40, verdict="approve")

    verify = {"exit": 0, "verify_state": "pass", "at": _ts(40), "head": "b" * 40, "test_first": {"passed": True}}
    (sdlc / "state" / "verify").mkdir(parents=True)
    (sdlc / "state" / "verify" / (GOAL + ".json")).write_text(json.dumps(verify, sort_keys=True) + "\n",
                                                              encoding="utf-8")
    store = {"prs": {"1": {"comments": [{"body": "review <!-- sigma-review-evidence:%s -->" % EVIDENCE_ID}]}}}
    (run / "gh_state.json").write_text(json.dumps(store, sort_keys=True) + "\n", encoding="utf-8")
    _build_remote(run, tmp_dir / "work")
    return run


def build_green_record(tmp_dir):
    """Run the documented `record.py build` on a fresh synthetic run; return the record dict."""
    run = build_green_run(tmp_dir)
    proc = subprocess.run([sys.executable, str(RECORD), "build", str(run), "--goal", GOAL],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("record.py build failed: " + proc.stderr)
    return json.loads((run / "record.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- pure mutators


def mutate_drop_verdict(rec):
    """M1: the plan-review verdict is gone (action-log row and the stream that reads it)."""
    out = copy.deepcopy(rec)
    s = out["streams"]
    s["action_log"]["rows"] = [r for r in s["action_log"]["rows"]
                               if not (r.get("kind") == "verdict" and r.get("phase") == "plan_review")]
    gate = s["plan_review_verdict"].get("gate_file_present")
    s["plan_review_verdict"] = {"source": s["plan_review_verdict"]["source"], "present": False,
                                "reason": "no plan_review verdict row", "verdict": None, "route": None,
                                "plan_hash": None, "verified": None, "gate_file_present": gate}
    return out


def mutate_drop_phase_end(rec):
    """M2: the `review` phase end is gone from the journal-derived phases and from the action log."""
    out = copy.deepcopy(rec)
    out["phases"] = [p for p in out["phases"] if p["phase"] != "review"]
    rows = out["streams"]["action_log"]["rows"]
    out["streams"]["action_log"]["rows"] = [r for r in rows if not (
        r.get("kind") == "phase" and r.get("phase") == "review" and r.get("state") == "end")]
    return out


def mutate_drop_marker(rec):
    """M3: the review marker is gone (no review_posted event, no marker in the store)."""
    out = copy.deepcopy(rec)
    src = out["streams"]["review_evidence"]["source"]
    out["streams"]["review_evidence"] = {"source": src, "present": False, "reason": "no review_posted event",
                                         "review_posted": [], "marker_in_store": False}
    return out


def write_fixtures(dest):
    """Write the green record and the three mutated copies into `dest`; return the paths."""
    with tempfile.TemporaryDirectory() as tmp:
        green = build_green_record(tmp)
    paths = []
    for name, mutator in FIXTURE_FILES.items():
        rec = green if mutator is None else globals()[mutator](green)
        path = pathlib.Path(dest) / name
        record.write_atomic(path, rec)
        paths.append(path)
    return paths


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python3 tests/regression_fixture.py <dest-dir>", file=sys.stderr)
        sys.exit(2)
    for p in write_fixtures(sys.argv[1]):
        print(p)

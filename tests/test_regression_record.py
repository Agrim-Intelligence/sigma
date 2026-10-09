"""`evals/regression/record.py build <run_dir>` -- the regression record builder (#874, slice 1 of #870).

Every test that asserts on the written record goes through the DOCUMENTED gesture: a subprocess
running `python record.py build <run_dir>` and reading the file it wrote. The in-process module is
used only for its own seams (`INTERNAL`, `_load_scripts`).

`test_git_env_clears_inherited_git_variables` sets inherited GIT_* variables that would hide the
remote's objects and refs if the builder let them through; GIT_DIR alone would NOT, because the
builder passes `--git-dir` explicitly, so it is set only as a decoy beside the ones that bite.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECORD = ROOT / "evals" / "regression" / "record.py"

_spec = importlib.util.spec_from_file_location("regression_record", RECORD)
record = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(record)

CAVEAT_CALL = ("phase end rows prove phase_report.py end was called, not that the phase did its work "
               "(evidence: call-existence)")
CAVEAT_OFF = "journal and action log are off by default; a stream absent here may mean the feature was off"
STREAMS = ("plan", "research", "plan_review_verdict", "verify", "journal", "action_log",
           "review_evidence", "commit_order", "tokens")


# --------------------------------------------------------------------------- helpers


def _sdlc(tmp_path):
    d = tmp_path / "run" / "repo" / ".sdlc"
    d.mkdir(parents=True)
    return d


def _jsonl(path, rows, raw=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, sort_keys=True) for r in rows] + list(raw)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _log(sdlc, goal, rows):
    out = []
    for i, r in enumerate(rows):
        out.append(dict({"ts": "2026-01-01T00:00:%02d.000Z" % i, "goal": goal, "thread": "main",
                         "actor": "loop"}, **r))
    _jsonl(sdlc / "state" / "log" / (goal + ".jsonl"), out)


def _journal(sdlc, rows, name="a-h.1.jsonl", goal="874", raw=()):
    out = []
    for i, r in enumerate(rows):
        out.append(dict({"id": "a:i:%d" % i, "ts": "2026-01-01T00:00:%02dZ" % i, "actor": "a",
                         "goal": goal}, **r))
    _jsonl(sdlc / "events" / name, out, raw)


def _build(tmp_path, *extra, goal="874", run_dir=None):
    run_dir = run_dir or (tmp_path / "run")
    argv = [sys.executable, str(RECORD), "build", str(run_dir)]
    if goal is not None:
        argv += ["--goal", goal]
    proc = subprocess.run(argv + list(extra), capture_output=True, text=True, timeout=60)
    out = run_dir / "record.json"
    rec = json.loads(out.read_text()) if proc.returncode == 0 and out.exists() else None
    return proc, rec


def _phase(rec, name):
    return next(p for p in rec["phases"] if p["phase"] == name)


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield k
            for s in _strings(v):
                yield s
    elif isinstance(node, list):
        for v in node:
            for s in _strings(v):
                yield s


def _git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    return env


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"] + list(args),
                   cwd=str(cwd), env=_git_env(), check=True, capture_output=True, text=True)


def _remote_with_branches(tmp_path, with_goal_branch=True):
    remote = tmp_path / "run" / "remote.git"
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], env=_git_env(), check=True)
    _git(work, "init", "-q")
    _git(work, "checkout", "-q", "-b", "main")
    (work / "a.txt").write_text("a")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-q", "-m", "first on main")
    _git(work, "push", "-q", str(remote), "HEAD:refs/heads/main")
    if with_goal_branch:
        _git(work, "checkout", "-q", "-b", "sdlc/874-thing")
        (work / "b.txt").write_text("b")
        _git(work, "add", "b.txt")
        _git(work, "commit", "-q", "-m", "goal work")
        _git(work, "push", "-q", str(remote), "sdlc/874-thing")
    return remote


# --------------------------------------------------------------------------- CLI + shape


def test_cli_builds_record_via_subprocess(tmp_path):
    sdlc = _sdlc(tmp_path)
    _log(sdlc, "874", [{"kind": "claimed"}])
    proc, rec = _build(tmp_path, goal=None)           # the single goal under state/log is found
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(tmp_path / "run" / "record.json")
    assert rec["schema"] == "sigma.regression-run/v1"
    assert rec["goal"] == "874"
    out = tmp_path / "elsewhere" / "r.json"
    proc2 = subprocess.run([sys.executable, str(RECORD), "build", str(tmp_path / "run"), "--out", str(out)],
                           capture_output=True, text=True, timeout=60)
    assert proc2.returncode == 0 and out.is_file()


def test_schema_top_level_shape(tmp_path):
    _sdlc(tmp_path)
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert set(rec) == {"schema", "goal", "run", "caveats", "phases", "streams"}
    assert rec["run"] == "run"
    assert set(rec["streams"]) == set(STREAMS)
    assert CAVEAT_CALL in rec["caveats"] and CAVEAT_OFF in rec["caveats"]


# --------------------------------------------------------------------------- action log (AC-2)


AGENT_ROWS = [
    {"kind": "agent_dispatch", "text": "SENTINEL-DISPATCH"},
    {"kind": "note", "text": "SENTINEL-NOTE"},
    {"kind": "file", "text": "SENTINEL-FILE"},
]


def test_agent_kind_rows_never_reach_record(tmp_path):
    sdlc = _sdlc(tmp_path)
    _log(sdlc, "874", [{"kind": "claimed"}] + AGENT_ROWS + [{"kind": "recorded", "result": "done"}])
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    blob = json.dumps(rec)
    for sentinel in ("SENTINEL-DISPATCH", "SENTINEL-NOTE", "SENTINEL-FILE",
                     "agent_dispatch", "\"note\"", "\"file\""):
        assert sentinel not in blob, sentinel
    assert [r["kind"] for r in rec["streams"]["action_log"]["rows"]] == ["claimed", "recorded"]


def test_dropped_non_internal_is_a_count_only(tmp_path):
    sdlc = _sdlc(tmp_path)
    _log(sdlc, "874", [{"kind": "claimed"}] + AGENT_ROWS + [{"kind": "bogus_kind", "text": "X"}])
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    stream = rec["streams"]["action_log"]
    assert stream["dropped_non_internal"] == 4
    assert len(stream["rows"]) == 1


def test_internal_kinds_imported_not_copied():
    actionlog = record._load_scripts("actionlog")
    assert record._load_scripts("actionlog") is actionlog          # one cached by-path load
    assert record.INTERNAL is actionlog.INTERNAL_KINDS
    assert "agent_dispatch" not in record.INTERNAL


# --------------------------------------------------------------------------- streams


def test_every_stream_has_source_and_present(tmp_path):
    sdlc = _sdlc(tmp_path)
    (sdlc / "plans").mkdir()
    (sdlc / "plans" / "874.md").write_text("plan")
    _log(sdlc, "874", [{"kind": "claimed"}])
    _journal(sdlc, [{"kind": "phase", "phase": "plan", "state": "end"}])
    for run in (tmp_path, tmp_path / "empty"):
        if run != tmp_path:
            (run / "run" / "repo" / ".sdlc").mkdir(parents=True)
        proc, rec = _build(run)
        assert proc.returncode == 0, proc.stderr
        for name in STREAMS:
            s = rec["streams"][name]
            assert isinstance(s["source"], str) and s["source"], name
            assert isinstance(s["present"], bool), name


def test_absent_streams_build_with_present_false(tmp_path):
    _sdlc(tmp_path)
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    for name in STREAMS:
        assert rec["streams"][name]["present"] is False, name
    assert rec["phases"] == []
    # no repo directory at all is also a build, not a crash
    bare = tmp_path / "norepo"
    bare.mkdir()
    proc, rec = _build(tmp_path, run_dir=bare)
    assert proc.returncode == 0, proc.stderr
    assert all(rec["streams"][n]["present"] is False for n in STREAMS)


def test_action_log_off_marks_log_and_verdict_absent(tmp_path):
    sdlc = _sdlc(tmp_path)
    (sdlc / "config.json").write_text(json.dumps({"action_log": {"enabled": False}}))
    _journal(sdlc, [{"kind": "phase", "phase": "plan", "state": "end", "attempt_id": "a1"}])
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert rec["streams"]["action_log"]["present"] is False
    assert rec["streams"]["plan_review_verdict"]["present"] is False
    assert _phase(rec, "plan")["end_present"] is True            # phases still come from the journal


def test_every_phase_is_call_existence_with_caveat(tmp_path):
    sdlc = _sdlc(tmp_path)
    _journal(sdlc, [{"kind": "phase", "phase": "research", "state": "start"},
                    {"kind": "phase", "phase": "research", "state": "end", "attempt_id": "r"},
                    {"kind": "phase", "phase": "plan", "state": "end", "attempt_id": "p"}])
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert [p["phase"] for p in rec["phases"]] == ["research", "plan"]   # canonical order
    assert all(p["evidence"] == "call-existence" for p in rec["phases"])
    assert CAVEAT_CALL in rec["caveats"]
    assert _phase(rec, "research")["start_present"] is True
    assert _phase(rec, "plan")["start_present"] is False


def test_plan_with_numeric_stem_and_slug_is_found(tmp_path):
    sdlc = _sdlc(tmp_path)
    (sdlc / "plans").mkdir()
    (sdlc / "plans" / "874-my-slug.md").write_text("the plan body")
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    plan = rec["streams"]["plan"]
    assert plan["present"] is True
    assert plan["source"] == "plans/874-my-slug.md"
    assert plan["bytes"] == len("the plan body")
    assert plan["sha256"] == hashlib.sha256(b"the plan body").hexdigest()
    assert rec["streams"]["research"]["present"] is False


def test_sibling_artifact_newest_does_not_displace_plan(tmp_path):
    for case, real in (("exact", "874.md"), ("slugged", "874-my-slug.md")):
        root = tmp_path / case
        root.mkdir()
        sdlc = _sdlc(root)
        (sdlc / "research").mkdir()
        for sub in ("plans", "research"):
            (sdlc / sub).mkdir(exist_ok=True)
            plan = sdlc / sub / real
            plan.write_text("real")
            sib = sdlc / sub / "874-controls.md"
            sib.write_text("controls output, not the plan")
            os.utime(str(plan), (1000, 1000))
            os.utime(str(sib), (2000, 2000))                         # the sibling is the NEWEST file
        proc, rec = _build(root)
        assert proc.returncode == 0, proc.stderr
        for name in ("plan", "research"):
            s = rec["streams"][name]
            assert s["source"].endswith("/" + real), (case, s)
            assert s["sha256"] == hashlib.sha256(b"real").hexdigest()


def test_only_sibling_artifacts_means_absent(tmp_path):
    sdlc = _sdlc(tmp_path)
    (sdlc / "plans").mkdir()
    for sib in ("874-controls.md", "874-retro.md", "874-code-review.md", "874-measurements.md"):
        (sdlc / "plans" / sib).write_text("x")
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert rec["streams"]["plan"]["present"] is False
    assert "sibling" in rec["streams"]["plan"]["reason"]


def test_verify_projection_drops_root_and_tail(tmp_path):
    sdlc = _sdlc(tmp_path)
    (sdlc / "state" / "verify").mkdir(parents=True)
    (sdlc / "state" / "verify" / "874.json").write_text(json.dumps({
        "command": "pytest", "exit": 0, "verify_state": "pass", "at": 1.5, "head": "abc123",
        "test_first": {"passed": True}, "root": "/abs/scratch/root", "tail": "ROOT-TAIL-OUTPUT",
        "content": {"files": ["a"]}}))
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    v = rec["streams"]["verify"]
    assert v["present"] is True
    assert {k: v[k] for k in ("exit", "verify_state", "at", "head", "test_first_passed")} == {
        "exit": 0, "verify_state": "pass", "at": 1.5, "head": "abc123", "test_first_passed": True}
    blob = json.dumps(rec)
    assert "/abs/scratch/root" not in blob and "ROOT-TAIL-OUTPUT" not in blob and "\"content\"" not in blob


def test_plan_review_verdict_from_log_row_not_gate_file(tmp_path):
    sdlc = _sdlc(tmp_path)
    _log(sdlc, "874", [
        {"kind": "verdict", "phase": "plan_review", "verdict": "block", "route": "r1", "plan_hash": "h1"},
        {"kind": "verdict", "phase": "review", "verdict": "pass"},
        {"kind": "verdict", "phase": "plan_review", "verdict": "pass", "route": "r2", "plan_hash": "h2",
         "verified": "yes"},
    ])
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    v = rec["streams"]["plan_review_verdict"]
    assert v["present"] is True and v["verdict"] == "pass" and v["plan_hash"] == "h2"
    assert v["route"] == "r2"
    assert v["gate_file_present"] is False                       # a cross-check, never the source
    (sdlc / "state" / "gates").mkdir(parents=True)
    (sdlc / "state" / "gates" / "874.json").write_text("{}")
    proc, rec = _build(tmp_path)
    assert rec["streams"]["plan_review_verdict"]["gate_file_present"] is True


# --------------------------------------------------------------------------- journal + tokens


def test_journal_union_of_files_skips_malformed_and_prune_stamp(tmp_path):
    sdlc = _sdlc(tmp_path)
    _journal(sdlc, [{"kind": "phase", "phase": "plan", "state": "end"},
                    {"kind": "gate", "gate": "merge", "verdict": "pass"}],
             name="a-h.1.jsonl", raw=["{not json", "[1, 2]"])
    _journal(sdlc, [{"kind": "phase", "phase": "plan", "state": "start"}], name="b-h.2.jsonl")
    _journal(sdlc, [{"kind": "phase", "phase": "plan", "state": "start"}], name="c-h.3.jsonl", goal="999")
    (sdlc / "events" / ".last-prune").write_text("{not json at all")
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    j = rec["streams"]["journal"]
    assert j["present"] is True and j["files"] == 3
    assert j["skipped_malformed"] == 2
    assert j["kinds"] == {"gate": 1, "phase": 2}                 # goal 874 rows only


def test_tokens_coerce_stringly_numerics_and_tolerate_garbage(tmp_path):
    sdlc = _sdlc(tmp_path)
    _journal(sdlc, [
        {"kind": "phase", "phase": "plan", "state": "end", "attempt_id": "a", "tokens_in": "1,234",
         "tokens_out": "10"},
        {"kind": "phase", "phase": "review", "state": "end", "attempt_id": "b", "tokens_in": "100",
         "tokens_out": "40"},
        {"kind": "phase", "phase": 5, "state": "end", "tokens_in": ["x"]},
        {"kind": "spend", "phase": "review", "attempt_id": "b", "cost_cents": "oops"},
    ])
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    plan, review = _phase(rec, "plan"), _phase(rec, "review")
    assert plan["tokens_in"] is None and plan["tokens_out"] == 10
    assert any("non-numeric tokens_in" in n for n in plan["notes"])
    assert review["tokens_in"] == 100 and review["tokens_out"] == 40 and review["cost_cents"] is None
    assert rec["streams"]["tokens"]["by_phase"]["review"]["tokens_in"] == 100


def test_retried_phase_sums_attempts(tmp_path):
    sdlc = _sdlc(tmp_path)
    _journal(sdlc, [
        {"kind": "phase", "phase": "plan", "state": "end", "attempt_id": "a1", "tokens_in": "100",
         "tokens_out": "10"},
        {"kind": "phase", "phase": "plan", "state": "end", "attempt_id": "a2", "tokens_in": "50",
         "tokens_out": "5"},
        {"kind": "phase", "phase": "plan", "state": "end", "attempt_id": "a2", "tokens_in": "50",
         "tokens_out": "5"},                                     # the same attempt written twice
    ])
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    p = _phase(rec, "plan")
    assert p["attempts"] == 2 and p["tokens_in"] == 150 and p["tokens_out"] == 15


def test_attempt_with_end_and_spend_counts_once(tmp_path):
    sdlc = _sdlc(tmp_path)
    _journal(sdlc, [
        {"kind": "phase", "phase": "plan", "state": "end", "attempt_id": "a1", "tokens_in": "100",
         "tokens_out": "10", "cost_cents": "999"},
        {"kind": "spend", "phase": "plan", "attempt_id": "a1", "tokens_in": "777", "tokens_out": "77",
         "cost_cents": "250"},
    ])
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    p = _phase(rec, "plan")
    assert p["tokens_in"] == 100 and p["tokens_out"] == 10       # from the end event only
    assert p["cost_cents"] == 250                                # from the spend event only


def test_journal_events_written_by_real_ledger_append(tmp_path):
    ledger = record._load_scripts("ledger")
    sdlc = _sdlc(tmp_path)
    config = {"journal": {"enabled": True}, "ledger": {"actor": "tester"}}
    ledger.append(str(sdlc), config, "phase", "874", stream=ledger.EVENTS, phase="plan", state="end",
                  tokens_in="1200", tokens_out="34", attempt_id="att-1", model="m")   # phase_report passes str(...)
    ledger.append(str(sdlc), config, "spend", "874", stream=ledger.EVENTS, phase="plan",
                  tokens_in="1200", tokens_out="34", cost_cents="45", attempt_id="att-1")
    assert list((sdlc / "events").glob("*.jsonl")), "the real writer produced no journal file"
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    p = _phase(rec, "plan")
    assert (p["tokens_in"], p["tokens_out"], p["cost_cents"], p["attempts"]) == (1200, 34, 45, 1)


# --------------------------------------------------------------------------- git, review evidence


def test_commit_order_from_bare_remote_and_goal_branch(tmp_path):
    _sdlc(tmp_path)
    _remote_with_branches(tmp_path)
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    c = rec["streams"]["commit_order"]
    assert c["present"] is True and c["source"] == "remote.git"
    assert [x["subject"] for x in c["main"]] == ["first on main"]
    assert [x["subject"] for x in c["goal_branches"]["sdlc/874-thing"]] == ["first on main", "goal work"]
    assert len(c["main"][0]["sha"]) == 40


def test_commit_order_absent_when_remote_missing(tmp_path):
    _sdlc(tmp_path)
    proc, rec = _build(tmp_path)
    assert rec["streams"]["commit_order"]["present"] is False
    # a remote with main but no goal branch: main present, the branch sub-entry absent with a reason
    _remote_with_branches(tmp_path, with_goal_branch=False)
    proc, rec = _build(tmp_path)
    c = rec["streams"]["commit_order"]
    assert c["present"] is True and c["goal_branches"] == {}
    assert c["goal_branches_present"] is False and c["goal_branches_reason"] == "no goal branch"


def test_git_env_clears_inherited_git_variables(tmp_path, monkeypatch):
    _sdlc(tmp_path)
    _remote_with_branches(tmp_path, with_goal_branch=False)
    decoy = tmp_path / "decoy.git"
    subprocess.run(["git", "init", "--bare", "-q", str(decoy)], env=_git_env(), check=True)
    monkeypatch.setenv("GIT_DIR", str(decoy))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "nope.idx"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(decoy / "objects"))
    monkeypatch.setenv("GIT_NAMESPACE", "decoy")
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    c = rec["streams"]["commit_order"]
    assert c["present"] is True and [x["subject"] for x in c["main"]] == ["first on main"]


def test_review_evidence_marker_found_in_store(tmp_path):
    sdlc = _sdlc(tmp_path)
    _journal(sdlc, [{"kind": "review_posted", "observation_key": "k", "brief_hash": "b",
                     "evidence_id": "ev1", "comment_id": 5, "pr": 7, "head_sha": "abc",
                     "verdict": "approve"}])
    proc, rec = _build(tmp_path)
    r = rec["streams"]["review_evidence"]
    assert r["present"] is True and r["marker_in_store"] is None      # no store to check
    assert r["review_posted"] == [{"pr": 7, "head_sha": "abc", "verdict": "approve",
                                   "evidence_id": "ev1"}]
    (tmp_path / "run" / "gh_state.json").write_text(json.dumps(
        {"prs": {"7": {"comments": [{"body": "ok <!-- sigma-review-evidence:ev1 -->"}]}}}))
    proc, rec = _build(tmp_path)
    assert rec["streams"]["review_evidence"]["marker_in_store"] is True
    (tmp_path / "run" / "gh_state.json").write_text(json.dumps({"prs": {"7": {"comments": []}}}))
    proc, rec = _build(tmp_path)
    assert rec["streams"]["review_evidence"]["marker_in_store"] is False


# --------------------------------------------------------------------------- goal selection, determinism


def test_several_goals_refuses_exit_2(tmp_path):
    sdlc = _sdlc(tmp_path)
    _log(sdlc, "874", [{"kind": "claimed"}])
    _log(sdlc, "875", [{"kind": "claimed"}])
    proc, _ = _build(tmp_path, goal=None)
    assert proc.returncode == 2 and proc.stdout == "" and "--goal" in proc.stderr
    assert not (tmp_path / "run" / "record.json").exists()
    proc, _ = _build(tmp_path, goal="../escape")
    assert proc.returncode == 2 and proc.stdout == ""


def test_goal_flag_selects_one_goal(tmp_path):
    sdlc = _sdlc(tmp_path)
    _log(sdlc, "874", [{"kind": "claimed"}])
    _log(sdlc, "875", [{"kind": "claimed"}, {"kind": "recorded", "result": "done"}])
    proc, rec = _build(tmp_path, goal="875")
    assert proc.returncode == 0, proc.stderr
    assert rec["goal"] == "875" and len(rec["streams"]["action_log"]["rows"]) == 2


def test_two_builds_are_byte_identical(tmp_path):
    sdlc = _sdlc(tmp_path)
    _log(sdlc, "874", [{"kind": "claimed"}, {"kind": "phase", "phase": "plan", "state": "end"}])
    _journal(sdlc, [{"kind": "phase", "phase": "plan", "state": "end", "attempt_id": "a",
                     "tokens_in": "5"}])
    _build(tmp_path)
    first = (tmp_path / "run" / "record.json").read_bytes()
    _build(tmp_path)
    assert (tmp_path / "run" / "record.json").read_bytes() == first


def test_record_carries_no_absolute_path(tmp_path):
    sdlc = _sdlc(tmp_path)
    _log(sdlc, "874", [
        {"kind": "worktree_start", "worktree": "/abs/elsewhere/wt-874", "branch": "sdlc/874", "base": "main"},
        {"kind": "gate", "gate": "merge", "verdict": "pass", "why": "ran in %s" % tmp_path},
    ])
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    rows = rec["streams"]["action_log"]["rows"]
    assert rows[0]["worktree"] == "wt-874"
    for s in _strings(rec):
        assert not s.startswith("/"), s
        assert str(tmp_path) not in s, s


def test_exact_stem_older_than_slugged_sibling_exact_wins(tmp_path):
    sdlc = _sdlc(tmp_path)
    (sdlc / "plans").mkdir()
    exact = sdlc / "plans" / "874.md"
    slugged = sdlc / "plans" / "874-newer-slug.md"
    exact.write_text("exact")
    slugged.write_text("slugged")
    os.utime(str(exact), (1000, 1000))
    os.utime(str(slugged), (2000, 2000))                             # the slugged file is NEWER
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    plan = rec["streams"]["plan"]
    assert plan["source"] == "plans/874.md"
    assert plan["sha256"] == hashlib.sha256(b"exact").hexdigest()


def test_non_numeric_stem_does_not_match_other_goals_prefixed_file(tmp_path):
    sdlc = _sdlc(tmp_path)
    (sdlc / "plans").mkdir()
    (sdlc / "plans" / "fix-a-b.md").write_text("another goal's plan")
    proc, rec = _build(tmp_path, goal="fix-a")
    assert proc.returncode == 0, proc.stderr
    plan = rec["streams"]["plan"]
    assert plan["present"] is False
    assert plan["source"] == "plans/fix-a.md"                       # a clean string, no glob pattern
    assert "[" not in plan["source"] and "*" not in plan["source"]


def test_absent_plan_source_is_clean_string(tmp_path):
    sdlc = _sdlc(tmp_path)
    proc, rec = _build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert rec["streams"]["plan"]["source"] == "plans/874.md"

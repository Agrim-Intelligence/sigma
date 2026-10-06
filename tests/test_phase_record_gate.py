"""#684: the default loop must RECORD every SDLC phase, and `record done` refuses without them.

Every gesture here is the one the docs give (SKILL.md step 6 `loop.py record ... done`, `phase_report.py
start|end`, `work.py record-plan-review`, `work.py record-review`, `loop.py waive-phases`, `loop.py phases`),
run as a real CLI process in a local-only (local-goals) project."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
GOAL = ".sdlc/goals/0001-x.md"
ENV = {**os.environ, "SIGMA_ALLOW_COEXIST": "1"}
for _k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDECODE", "CODEX_SESSION_ID", "CODEX_THREAD_ID"):
    ENV.pop(_k, None)


def _project(tmp_path, gate=True, action_log=True, extra=None):
    d = tmp_path / ".sdlc"
    for sub in ("goals", "state", "plans", "research"):
        (d / sub).mkdir(parents=True)
    cfg = {"action_log": {"enabled": action_log}}
    if gate is not None:
        cfg["gates"] = {"phase_record": {"enabled": gate}}
    cfg.update(extra or {})
    (d / "config.json").write_text(json.dumps(cfg))
    (tmp_path / GOAL).write_text("---\nstatus: open\n---\n# Goal x\nbody\n")
    (d / "plans" / "0001-x.md").write_text("# Plan\n\n## Tests\n- `tests/t.py`\n")
    (d / "research" / "0001-x.md").write_text("# Research\nfound things\n")
    return tmp_path


def run(p, script, *args, env=None):
    r = subprocess.run([sys.executable, str(S / script), *map(str, args)], cwd=p,
                       capture_output=True, text=True, env={**ENV, **(env or {})})
    return r


def phase(p, name, agent=None):
    assert run(p, "phase_report.py", "start", ".sdlc", GOAL, name, "--model", "sonnet").returncode == 0
    args = ["end", ".sdlc", GOAL, name] + (["--agent-id", agent] if agent else [])
    assert run(p, "phase_report.py", *args).returncode == 0


def plan_sha(p):
    import hashlib
    return hashlib.sha256((p / ".sdlc/plans/0001-x.md").read_bytes()).hexdigest()


def plan_review(p, verdict="SOUND", agent="rev1"):
    r = run(p, "work.py", "record-plan-review", ".sdlc", GOAL, "--verdict", verdict,
            "--plan-sha256", plan_sha(p), "--agent-id", agent, env={})
    assert r.returncode == 0, r.stderr
    return r


def review(p, verdict="APPROVE", agent="rev2"):
    return run(p, "work.py", "record-review", ".sdlc", GOAL, "--verdict", verdict, "--agent-id", agent)


def log_rows(p):
    f = p / ".sdlc/state/log/0001-x.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def full_run(p):
    phase(p, "research", "a1")
    phase(p, "plan", "a2")
    phase(p, "plan_review", "rev1")
    plan_review(p)
    phase(p, "implement", "a3")
    phase(p, "review", "rev2")
    review(p)
    phase(p, "retro", "a4")


def done(p):
    return run(p, "loop.py", "record", ".sdlc", GOAL, "done")


def test_record_done_refused_with_no_phases_names_each_missing_phase_and_the_lever(tmp_path):
    p = _project(tmp_path)
    r = done(p)
    assert r.returncode == 4, r.stderr
    for ph in ("research", "plan", "plan_review", "implement", "review", "retro"):
        assert f"- {ph}:" in r.stderr
    assert "phase_report.py" in r.stderr and "waive-phases" in r.stderr
    assert "gates.phase_record.enabled" in r.stderr
    assert not any(x["kind"] == "recorded" for x in log_rows(p))


def test_record_done_passes_when_every_phase_is_recorded_in_order(tmp_path):
    p = _project(tmp_path)
    full_run(p)
    r = done(p)
    assert r.returncode == 0, r.stderr
    rows = log_rows(p)
    assert [x["kind"] for x in rows][-1] == "recorded"
    ends = [x["phase"] for x in rows if x["kind"] == "phase" and x["state"] == "end"]
    assert ends == ["research", "plan", "plan_review", "implement", "review", "retro"]


def test_implement_before_the_plan_review_verdict_is_refused(tmp_path):
    p = _project(tmp_path)
    phase(p, "research", "a1"); phase(p, "plan", "a2"); phase(p, "plan_review", "rev1")
    run(p, "phase_report.py", "start", ".sdlc", GOAL, "implement", "--model", "sonnet")
    plan_review(p)
    run(p, "phase_report.py", "end", ".sdlc", GOAL, "implement", "--agent-id", "a3")
    phase(p, "review", "rev2"); review(p); phase(p, "retro", "a4")
    r = done(p)
    assert r.returncode == 4 and "implement started BEFORE the plan-review verdict" in r.stderr


def test_a_fix_first_plan_review_or_send_back_review_does_not_count(tmp_path):
    p = _project(tmp_path)
    phase(p, "research", "a1"); phase(p, "plan", "a2"); phase(p, "plan_review", "rev1")
    plan_review(p, "FIX-FIRST")
    phase(p, "implement", "a3"); phase(p, "review", "rev2"); review(p, "SEND-BACK"); phase(p, "retro", "a4")
    r = done(p)
    assert r.returncode == 4
    assert "plan_review: last verdict is 'block'" in r.stderr and "review: last verdict is 'send-back'" in r.stderr
    plan_review(p, "SOUND"); review(p, "APPROVE", "rev3")      # last verdict per phase counts
    assert "implement started BEFORE" in done(p).stderr        # ...but the order is still wrong


def test_subagent_route_verdict_without_agent_id_or_with_the_makers_id_is_refused(tmp_path):
    p = _project(tmp_path)
    env = {"CLAUDECODE": "1"}                                  # reviewer route = subagent
    r = run(p, "work.py", "record-review", ".sdlc", GOAL, "--verdict", "APPROVE", env=env)
    assert r.returncode == 2 and "--agent-id" in r.stderr
    phase(p, "research", "a1"); phase(p, "plan", "a2"); phase(p, "plan_review", "rev1")
    assert run(p, "work.py", "record-plan-review", ".sdlc", GOAL, "--verdict", "SOUND",
               "--plan-sha256", plan_sha(p), "--agent-id", "rev1", env=env).returncode == 0
    phase(p, "implement", "maker"); phase(p, "review", "maker")
    assert run(p, "work.py", "record-review", ".sdlc", GOAL, "--verdict", "APPROVE",
               "--agent-id", "maker", env=env).returncode == 0
    phase(p, "retro", "a4")
    r = done(p)
    assert r.returncode == 4 and "the maker is the checker" in r.stderr


def test_a_made_up_agent_id_is_refused_when_the_transcript_store_is_readable(tmp_path):
    p = _project(tmp_path)
    home = tmp_path / "home"
    (home / ".claude/projects/proj/sess1").mkdir(parents=True)
    env = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess1", "HOME": str(home)}
    r = run(p, "work.py", "record-review", ".sdlc", GOAL, "--verdict", "APPROVE", "--agent-id", "ghost", env=env)
    assert r.returncode == 2 and "no transcript" in r.stderr
    (home / ".claude/projects/proj/sess1/subagents").mkdir()
    (home / ".claude/projects/proj/sess1/subagents/agent-real.jsonl").write_text("{}\n")
    assert run(p, "work.py", "record-review", ".sdlc", GOAL, "--verdict", "APPROVE", "--agent-id", "real",
               env=env).returncode == 0
    assert log_rows(p)[-1]["verified"] == "transcript"


def test_a_missing_required_row_fails_closed_and_last_row_per_phase_counts(tmp_path):
    p = _project(tmp_path)
    full_run(p)
    log = p / ".sdlc/state/log/0001-x.jsonl"
    rows = [x for x in log.read_text().splitlines() if '"kind": "verdict"' not in x or '"review"' not in x]
    log.write_text("\n".join(rows) + "\n")                     # the review verdict row is gone
    r = done(p)
    assert r.returncode == 4 and "review: no verdict recorded" in r.stderr


def test_editing_the_plan_after_its_review_is_refused(tmp_path):
    p = _project(tmp_path)
    full_run(p)
    (p / ".sdlc/plans/0001-x.md").write_text("# Plan edited after review\n")
    r = done(p)
    assert r.returncode == 4 and "the plan was edited after its review" in r.stderr


def test_waive_phases_allows_only_research_and_retro_and_is_recorded(tmp_path):
    p = _project(tmp_path)
    bad = run(p, "loop.py", "waive-phases", ".sdlc", GOAL, "plan,review", "--reason", "x")
    assert bad.returncode == 2 and "can be waived" in bad.stderr
    assert run(p, "loop.py", "waive-phases", ".sdlc", GOAL, "research", "--reason", "").returncode == 2
    ok = run(p, "loop.py", "waive-phases", ".sdlc", GOAL, "research,retro", "--reason", "one-line typo fix")
    assert ok.returncode == 0 and "WAIVED" in ok.stderr
    phase(p, "plan", "a2"); phase(p, "plan_review", "rev1"); plan_review(p)
    phase(p, "implement", "a3"); phase(p, "review", "rev2"); review(p)
    d = done(p)
    assert d.returncode == 0, d.stderr
    assert "waived" in d.stderr.lower()
    assert [x["phase"] for x in log_rows(p) if x["kind"] == "phase_waived"] == ["research", "retro"]
    st = run(p, "loop.py", "phases", ".sdlc", GOAL).stdout
    assert "WAIVED: one-line typo fix" in st


def test_gate_off_when_the_key_is_absent_and_refuses_when_the_action_log_is_off(tmp_path):
    p = _project(tmp_path, gate=None)
    assert done(p).returncode == 0                             # absent key: old behaviour
    (tmp_path / "b").mkdir()
    q = _project(tmp_path / "b", gate=True, action_log=False)
    r = done(q)
    assert r.returncode == 4 and "action_log.enabled" in r.stderr and "REFUSED" in r.stderr


def test_phase_report_start_and_end_write_phase_rows_to_the_action_log(tmp_path):
    p = _project(tmp_path)
    assert run(p, "loop.py", "claim", ".sdlc", GOAL).returncode == 0
    phase(p, "research", "a1")
    rows = [x for x in log_rows(p) if x["kind"] == "phase"]
    assert [(x["phase"], x["state"]) for x in rows] == [("research", "start"), ("research", "end")]
    assert rows[1]["agent_id"] == "a1" and rows[1]["cost"]
    slots = subprocess.run([sys.executable, str(S.parent.parent / "sigma-log/scripts/log.py"), "slots", ".sdlc"],
                           cwd=p, capture_output=True, text=True, env=ENV)
    assert "P2 RESEARCH" in slots.stdout


def test_record_plan_review_writes_a_verdict_row_and_record_review_writes_the_review_row(tmp_path):
    p = _project(tmp_path)
    plan_review(p, "SOUND-WITH-REFINEMENTS")
    review(p)
    v = [x for x in log_rows(p) if x["kind"] == "verdict"]
    assert [(x["phase"], x["verdict"]) for x in v] == [("plan_review", "warn"), ("review", "approve")]
    assert v[0]["plan_hash"] == plan_sha(p) and v[0]["agent_id"] == "rev1"


def test_phases_verb_prints_status_and_next_command(tmp_path):
    p = _project(tmp_path)
    phase(p, "research", "a1")
    out = run(p, "loop.py", "phases", ".sdlc", GOAL).stdout
    assert "ok      research" in out and "MISSING plan:" in out and "NEXT: plan" in out
    full_run(p)
    assert "NEXT: all phases recorded" in run(p, "loop.py", "phases", ".sdlc", GOAL).stdout

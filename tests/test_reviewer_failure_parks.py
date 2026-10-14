"""D2 F02 (owner ruling 2026-10-14): a FAILED independent reviewer is never an approval; the goal parks.

`reviewer.py run --goal G --phase P` leaves ONE receipt per goal+phase (latest run wins);
`record-review` / `record-plan-review` refuse an APPROVING verdict on a process/command route unless
that receipt says the process ran, printed a lone `VERDICT: approve`, and (plan-review) saw these
plan bytes. Each guard clause has its own test below; the PR body names the red test per break."""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
GOAL = ".sdlc/goals/0001-x.md"
PLAN = "# Plan\n\n## Tests\n- `tests/t.py`\n"
SHA = hashlib.sha256(PLAN.encode()).hexdigest()
CLEAN = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID")


def _rv():
    spec = importlib.util.spec_from_file_location("reviewer_f02", S / "reviewer.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in CLEAN:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SIGMA_ALLOW_COEXIST", "1")


def _project(tmp_path, review=None, action_log=True):
    d = tmp_path / ".sdlc"
    for sub in ("goals", "state", "plans", "research"):
        (d / sub).mkdir(parents=True)
    review = {"command": "sh %s/rev.sh" % tmp_path} if review is None else review
    (d / "config.json").write_text(json.dumps({"action_log": {"enabled": action_log}, "review": review}))
    (tmp_path / GOAL).write_text("---\nstatus: open\n---\n# Goal x\nbody\n")
    (d / "plans" / "0001-x.md").write_text(PLAN)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local",
                    "sigma.allowRepositoryShellCommands", "true"], check=True)
    (tmp_path / "code.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "code.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "-m", "init"], check=True)
    (tmp_path / "brief.md").write_text(brief_text("code-review"))
    (tmp_path / "scratch").mkdir()
    return tmp_path


def brief_text(kind):
    return "# Independent review brief - %s\nPlan sha256: %s\nreview it\n" % (kind, SHA)


def reviewer_script(p, body):
    (p / "rev.sh").write_text(body)


APPROVE = "echo 'looks fine'\necho 'VERDICT: approve'\n"
BLOCK = "echo 'broken'\necho 'VERDICT: block'\n"
FAILURES = {
    "nonzero_exit": "exit 3\n",
    "unauthenticated": "echo 'Error: not logged in' >&2\nexit 1\n",
    "empty_output": "exit 0\n",
    "timeout": "sleep 30\n",
}


def run_reviewer(p, goal=GOAL, phase="review", write_brief=True):
    """The documented gesture: reviewer.py run .sdlc <brief> --scratch <dir> --goal --phase."""
    if write_brief:
        (p / "brief.md").write_text(brief_text("plan-review" if phase == "plan_review" else "code-review"))
    return subprocess.run([sys.executable, str(S / "reviewer.py"), "run", ".sdlc", "brief.md",
                           "--scratch", "scratch", "--goal", goal, "--phase", phase],
                          cwd=p, capture_output=True, text=True)


def work(p, *args):
    return subprocess.run([sys.executable, str(S / "work.py"), *args], cwd=p, capture_output=True, text=True)


def record_review(p, verdict="APPROVE"):
    return work(p, "record-review", ".sdlc", GOAL, "--verdict", verdict)


def record_plan(p, verdict="SOUND", sha=SHA):
    return work(p, "record-plan-review", ".sdlc", GOAL, "--verdict", verdict, "--plan-sha256", sha)


def verdict_rows(p):
    f = p / ".sdlc/state/log/0001-x.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines() if '"verdict"' in x] if f.exists() else []


def timeout_cfg(p):
    cfg = json.loads((p / ".sdlc/config.json").read_text())
    cfg["review"]["timeout_seconds"] = 1
    (p / ".sdlc/config.json").write_text(json.dumps(cfg))


@pytest.mark.parametrize("kind", sorted(FAILURES))
def test_each_failure_kind_refuses_an_approving_review_and_a_plan_review(tmp_path, kind):
    p = _project(tmp_path)
    if kind == "timeout":
        timeout_cfg(p)
    for phase, record in (("review", record_review), ("plan_review", record_plan)):
        reviewer_script(p, FAILURES[kind])
        assert run_reviewer(p, phase=phase).returncode != 0
        r = record(p)
        assert r.returncode == 2, (kind, phase, r.stdout, r.stderr)
        assert "FAILED" in r.stderr and "reviewer_down" in r.stderr and "re-run" in r.stderr
    assert verdict_rows(p) == []


def test_start_failure_is_a_failed_run(tmp_path, monkeypatch):
    p = _project(tmp_path)
    rv = _rv()
    real = subprocess.Popen
    def boom(cmd, *a, **k):
        if cmd[0] == "git":                      # shell_policy's trust probe must still work
            return real(cmd, *a, **k)
        raise OSError("no exec")
    monkeypatch.setattr(rv.subprocess, "Popen", boom)
    assert rv.run_review(str(p / ".sdlc"), str(p / "brief.md"), [str(p / "scratch")],
                         goal=GOAL, phase="review") == 1
    assert "start_failed" in rv.independent_run_refusal(str(p / ".sdlc"), GOAL, "review")


def test_a_failure_leaves_the_named_kind_in_the_receipt(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, FAILURES["empty_output"])
    run_reviewer(p)
    got = json.loads((p / ".sdlc/state/reviewer-runs/0001-x.review.json").read_text())
    assert got["outcome"] == "failed" and got["failure"] == "empty_output"


def test_an_unfinished_run_counts_as_failed_not_as_the_earlier_success(tmp_path):
    """Pessimistic receipt: a run refused/killed before it finishes overwrites a prior success."""
    p = _project(tmp_path)
    reviewer_script(p, APPROVE)
    assert run_reviewer(p).returncode == 0 and record_review(p).returncode == 0
    (p / "brief.md").write_text("# b\nsee %s/scratch/x\n" % p)           # names a shared scratch path
    assert run_reviewer(p, write_brief=False).returncode == 1             # leak check refuses before spawn
    r = record_review(p)
    assert r.returncode == 2 and "not_completed" in r.stderr


def test_success_then_failure_then_success_clears_the_marker(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p)
    reviewer_script(p, FAILURES["nonzero_exit"]); run_reviewer(p)
    assert record_review(p).returncode == 2
    reviewer_script(p, APPROVE); assert run_reviewer(p).returncode == 0
    r = record_review(p)
    assert r.returncode == 0, r.stderr
    assert [x["verdict"] for x in verdict_rows(p)] == ["approve"]


def test_no_run_on_record_refuses_on_a_process_or_command_route(tmp_path):
    p = _project(tmp_path)
    r = record_review(p)
    assert r.returncode == 2 and "no run of it is on record" in r.stderr


def test_the_reviewers_own_block_cannot_be_recorded_as_approve_but_a_block_can(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, BLOCK); run_reviewer(p)
    assert record_review(p).returncode == 2
    assert record_review(p, "BLOCK").returncode == 0                      # a cautious record is never refused
    assert record_review(p, "SEND-BACK").returncode == 0


def test_output_without_a_lone_verdict_line_cannot_be_approved(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, "echo 'verdict: approve would be premature'\n")
    assert run_reviewer(p).returncode == 0
    r = record_review(p)
    assert r.returncode == 2 and "lone `VERDICT: approve`" in r.stderr


def test_plan_review_approval_is_bound_to_the_plan_the_reviewer_saw(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p, phase="plan_review")
    assert record_plan(p).returncode == 0
    (p / ".sdlc/plans/0001-x.md").write_text(PLAN + "- changed\n")
    new = hashlib.sha256((PLAN + "- changed\n").encode()).hexdigest()
    r = record_plan(p, sha=new)
    assert r.returncode == 2 and "different plan" in r.stderr


def test_plan_review_is_refused_even_with_the_action_log_off(tmp_path):
    p = _project(tmp_path, action_log=False)
    reviewer_script(p, FAILURES["nonzero_exit"]); run_reviewer(p, phase="plan_review")
    r = record_plan(p)
    assert r.returncode == 2 and "FAILED" in r.stderr
    assert not (p / ".sdlc/state/gates/0001-x.json").exists()


def test_process_route_is_guarded_like_command(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"; bindir.mkdir()
    (bindir / "cursor-agent").write_text("#!/bin/sh\nexit 4\n"); (bindir / "cursor-agent").chmod(0o755)
    monkeypatch.setenv("PATH", "%s%s%s" % (bindir, os.pathsep, os.environ["PATH"]))
    p = _project(tmp_path, review={"host": "cursor"})
    assert json.loads(subprocess.run([sys.executable, str(S / "reviewer.py"), "resolve", ".sdlc"], cwd=p,
                                     capture_output=True, text=True).stdout)["mechanism"] == "process"
    assert run_reviewer(p).returncode == 1
    r = record_review(p)
    assert r.returncode == 2 and "nonzero_exit" in r.stderr


def test_inline_route_still_works_when_it_is_what_resolved(tmp_path):
    for review in ({"independent": False, "command": "sh x"}, {"host": "inline"}):
        d = tmp_path / ("c%d" % len(review)); d.mkdir()
        p = _project(d, review=review)
        r = record_review(p)
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["route"] == "inline"


def test_subagent_route_is_not_governed_by_the_receipt(tmp_path, monkeypatch):
    p = _project(tmp_path, review={"host": "auto"})
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _rv().independent_run_refusal(str(p / ".sdlc"), GOAL, "review") is None


def test_goal_and_phase_go_together_and_phase_is_closed(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE)
    r = subprocess.run([sys.executable, str(S / "reviewer.py"), "run", ".sdlc", "brief.md",
                        "--scratch", "scratch", "--goal", GOAL], cwd=p, capture_output=True, text=True)
    assert r.returncode == 2
    assert run_reviewer(p, phase="pr_review").returncode == 2


def test_documented_gesture_end_to_end_then_park_with_the_stated_qkind(tmp_path):
    """The gesture the refusal prints: park with reviewer_down; the done gate still refuses."""
    p = _project(tmp_path)
    reviewer_script(p, FAILURES["unauthenticated"]); run_reviewer(p)
    r = record_review(p)
    assert r.returncode == 2
    park = subprocess.run([sys.executable, str(S / "loop.py"), "record", ".sdlc", GOAL, "parked",
                           "independent reviewer failed: nonzero_exit", "--qkind", "reviewer_down"],
                          cwd=p, capture_output=True, text=True)
    assert park.returncode == 0, park.stderr
    assert "status: parked" in (p / GOAL).read_text() or "parked" in (p / GOAL).read_text()


# ---- security review of #1384: fail closed, and bind the code-review receipt to the reviewed bytes ----

def _approved_review(p):
    reviewer_script(p, APPROVE)
    assert run_reviewer(p).returncode == 0
    assert record_review(p).returncode == 0          # control: approvable while nothing has changed
    (p / ".sdlc/state/log/0001-x.jsonl").unlink()    # forget that row, keep the receipt


def test_a_corrupt_config_is_refused_in_process_too(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p)
    (p / ".sdlc/config.json").write_text("{not json")
    assert "config" in _rv().independent_run_refusal(str(p / ".sdlc"), GOAL, "review")


def test_a_corrupt_config_cannot_turn_a_failed_route_into_an_inline_approval(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, FAILURES["nonzero_exit"]); run_reviewer(p)
    (p / ".sdlc/config.json").write_text("{not json")
    r = record_review(p)
    assert r.returncode == 2, r.stdout


@pytest.mark.parametrize("damage", ["garbage", "list", "directory", "unreadable"])
def test_a_damaged_receipt_refuses(tmp_path, damage):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p)
    f = p / ".sdlc/state/reviewer-runs/0001-x.review.json"
    if damage == "garbage":
        f.write_text("\x00{")
    elif damage == "list":
        f.write_text("[]")
    elif damage == "directory":
        f.unlink(); f.mkdir()
    else:
        f.chmod(0)
    r = record_review(p)
    f.chmod(0o600) if damage == "unreadable" else None
    assert r.returncode == 2, damage


def test_unknown_phase_and_unsafe_goal_are_refused_not_allowed(tmp_path):
    p = _project(tmp_path)
    rv = _rv()
    assert rv.independent_run_refusal(str(p / ".sdlc"), GOAL, "pr_review")
    assert rv.independent_run_refusal(str(p / ".sdlc"), "../../etc/passwd", "review")


def test_plan_review_needs_a_plan_hash_to_be_approved(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p, phase="plan_review")
    assert _rv().independent_run_refusal(str(p / ".sdlc"), GOAL, "plan_review", "")


def test_a_review_receipt_is_dead_once_a_tracked_file_changes(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p)
    (p / "code.py").write_text("x = 2\n")
    r = record_review(p)
    assert r.returncode == 2 and "changed since" in r.stderr


def test_a_review_receipt_is_dead_once_an_untracked_file_appears_or_changes(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p)
    (p / "new.py").write_text("a\n")
    assert record_review(p).returncode == 2
    reviewer_script(p, APPROVE); run_reviewer(p)
    assert record_review(p).returncode == 0
    (p / "new.py").write_text("b\n")
    assert record_review(p).returncode == 2


def test_a_review_receipt_is_dead_once_head_moves(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p)
    subprocess.run(["git", "-C", str(p), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "moved"], check=True)
    assert record_review(p).returncode == 2


def test_sdlc_state_changes_do_not_kill_the_receipt(tmp_path):
    """The receipt and the loop's own state live in .sdlc; they must not invalidate themselves."""
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p)
    (p / ".sdlc/research/0001-x.md").parent.mkdir(exist_ok=True)
    (p / ".sdlc/research/0001-x.md").write_text("notes\n")
    assert record_review(p).returncode == 0


def test_content_changing_while_the_reviewer_runs_is_a_failed_run(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, "echo changed > %s/code.py\n%s" % (p, APPROVE))
    assert run_reviewer(p).returncode == 1
    r = record_review(p)
    assert r.returncode == 2 and "content_changed_during_run" in r.stderr


def test_unbindable_content_fails_closed(tmp_path):
    p = _project(tmp_path)
    import shutil
    shutil.rmtree(p / ".git")                      # a checkout with no commit cannot be bound
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    subprocess.run(["git", "-C", str(p), "config", "--local", "sigma.allowRepositoryShellCommands", "true"],
                   check=True)
    reviewer_script(p, APPROVE)
    assert run_reviewer(p).returncode == 1
    r = record_review(p)
    assert r.returncode == 2 and "content_unbound" in r.stderr


def test_a_plan_review_brief_cannot_stand_in_for_a_code_review(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE)
    (p / "brief.md").write_text(brief_text("plan-review"))
    assert run_reviewer(p, phase="review", write_brief=False).returncode == 0
    r = record_review(p)
    assert r.returncode == 2 and "plan-review" in r.stderr


def test_a_review_receipt_with_no_content_key_cannot_approve(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p)
    f = p / ".sdlc/state/reviewer-runs/0001-x.review.json"
    got = json.loads(f.read_text()); got["content_key"] = None
    f.write_text(json.dumps(got))
    assert record_review(p).returncode == 2

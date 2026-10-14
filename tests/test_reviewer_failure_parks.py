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


def work(p, *args, env=None):
    return subprocess.run([sys.executable, str(S / "work.py"), *args], cwd=p, capture_output=True, text=True,
                          env=env)


def record_review(p, verdict="APPROVE", env=None):
    return work(p, "record-review", ".sdlc", GOAL, "--verdict", verdict, env=env)


def record_plan(p, verdict="SOUND", sha=SHA, env=None):
    return work(p, "record-plan-review", ".sdlc", GOAL, "--verdict", verdict, "--plan-sha256", sha, env=env)


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


def test_a_run_refused_before_the_reviewer_starts_keeps_an_earlier_good_receipt(tmp_path):
    """S5: a leak-guard refusal is a maker typo, not a reviewer failure; it must not strand a good receipt."""
    p = _project(tmp_path)
    reviewer_script(p, APPROVE)
    assert run_reviewer(p).returncode == 0
    leak = tmp_path.parent / ("leak-%s.md" % tmp_path.name)               # outside the checkout, like /tmp
    leak.write_text("# b\nsee %s/scratch/x\n" % p)                       # names a shared scratch path
    got = subprocess.run([sys.executable, str(S / "reviewer.py"), "run", ".sdlc", str(leak), "--scratch",
                          "scratch", "--goal", GOAL, "--phase", "review"], cwd=p, capture_output=True, text=True)
    assert got.returncode == 1                                            # leak check refuses before spawn
    assert record_review(p).returncode == 0


def test_a_run_refused_by_shell_policy_keeps_an_earlier_good_receipt(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE)
    assert run_reviewer(p).returncode == 0
    subprocess.run(["git", "-C", str(p), "config", "--local", "--unset", "sigma.allowRepositoryShellCommands"],
                   check=True)
    assert run_reviewer(p).returncode == 2
    subprocess.run(["git", "-C", str(p), "config", "--local", "sigma.allowRepositoryShellCommands", "true"],
                   check=True)
    assert record_review(p).returncode == 0


def test_a_killed_run_counts_as_failed_not_as_the_earlier_success(tmp_path, monkeypatch):
    """Once the reviewer is LAUNCHED the receipt is pessimistic: an interrupted run leaves not_completed."""
    p = _project(tmp_path)
    reviewer_script(p, APPROVE)
    assert run_reviewer(p).returncode == 0
    rv = _rv()
    real = subprocess.Popen
    class Boom:
        def __init__(self, *a, **k): self.pid = 0
        def communicate(self, *a, **k): raise KeyboardInterrupt
        def kill(self): pass
        def wait(self, *a, **k): pass
    monkeypatch.setattr(rv.subprocess, "Popen", lambda cmd, *a, **k: real(cmd, *a, **k) if cmd[0] == "git" else Boom())
    monkeypatch.setattr(rv, "_stop_review_process", lambda proc: None)
    with pytest.raises(KeyboardInterrupt):
        rv.run_review(str(p / ".sdlc"), str(p / "brief.md"), [str(p / "scratch")], goal=GOAL, phase="review")
    monkeypatch.undo()
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


def _stub_host(tmp_path, monkeypatch, name, body):
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    (bindir / name).write_text("#!/bin/sh\n" + body); (bindir / name).chmod(0o755)
    monkeypatch.setenv("PATH", "%s%s%s" % (bindir, os.pathsep, os.environ["PATH"]))
    return bindir


def _without(bindir):
    return {**os.environ, "PATH": os.pathsep.join(x for x in os.environ["PATH"].split(os.pathsep) if x != str(bindir))}


def test_a_directory_that_is_not_a_git_checkout_fails_closed_and_names_why(tmp_path, monkeypatch):
    _stub_host(tmp_path, monkeypatch, "cursor-agent", "echo 'ok'\necho 'VERDICT: approve'\n")
    p = _project(tmp_path, review={"host": "cursor"})
    import shutil
    shutil.rmtree(p / ".git")
    assert run_reviewer(p).returncode == 1
    r = record_review(p)
    assert r.returncode == 2 and "content_unbound" in r.stderr and "not a git checkout" in r.stderr


@pytest.mark.parametrize("case", ["nested_repo", "broken_symlink", "newline_name", "no_commit"])
def test_ordinary_checkouts_bind_instead_of_stranding(tmp_path, case):
    p = _project(tmp_path)
    if case == "nested_repo":
        n = p / "vendor"; n.mkdir()
        subprocess.run(["git", "init", "-q", str(n)], check=True)
        (n / "f").write_text("1"); subprocess.run(["git", "-C", str(n), "add", "f"], check=True)
        subprocess.run(["git", "-C", str(n), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], check=True)
    elif case == "broken_symlink":
        (p / "dangling").symlink_to("/nonexistent/target")
    elif case == "newline_name":
        (p / "a\nb.txt").write_text("x")
    else:
        import shutil
        shutil.rmtree(p / ".git")
        subprocess.run(["git", "init", "-q", str(p)], check=True)
        subprocess.run(["git", "-C", str(p), "config", "--local", "sigma.allowRepositoryShellCommands", "true"], check=True)
    reviewer_script(p, APPROVE)
    r = run_reviewer(p)
    assert r.returncode == 0, (case, r.stderr)
    assert record_review(p).returncode == 0
    # and the binding still bites on a change to the thing that used to strand
    if case == "nested_repo":
        subprocess.run(["git", "-C", str(p / "vendor"), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "y"], check=True)
    elif case == "broken_symlink":
        (p / "dangling").unlink(); (p / "dangling").symlink_to("/other")
    elif case == "newline_name":
        (p / "a\nb.txt").write_text("changed")
    else:
        (p / "code.py").write_text("changed\n")
    assert record_review(p).returncode == 2


def test_too_many_untracked_files_fails_closed_naming_that_cause(tmp_path, monkeypatch):
    p = _project(tmp_path)
    for i in range(4):
        (p / ("u%d" % i)).write_text("x")
    rv = _rv()
    monkeypatch.setattr(rv, "_MAX_UNTRACKED", 3)
    reviewer_script(p, APPROVE)
    assert rv.run_review(str(p / ".sdlc"), str(p / "brief.md"), [str(p / "scratch")], goal=GOAL, phase="review") == 1
    r = record_review(p)
    assert r.returncode == 2 and "untracked" in r.stderr and "content_unbound" in r.stderr
    assert "Park the goal" not in r.stderr          # a checkout problem is not a reviewer failure


def test_the_diff_is_streamed_not_buffered(tmp_path):
    import tracemalloc
    p = _project(tmp_path)
    (p / "code.py").write_bytes(os.urandom(30 * 1024 * 1024))
    rv = _rv()
    tracemalloc.start()
    key, cause = rv._content_key(str(p / ".sdlc"), GOAL)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert key and cause is None
    assert peak < 12 * 1024 * 1024, peak


def test_a_slow_or_huge_diff_fails_closed_naming_the_diff(tmp_path, monkeypatch):
    p = _project(tmp_path)
    (p / "code.py").write_bytes(os.urandom(8 * 1024 * 1024))
    rv = _rv()
    monkeypatch.setattr(rv, "_KEY_SECONDS", 0.0001)
    key, cause = rv._content_key(str(p / ".sdlc"), GOAL)
    assert key is None and "diff too large or slow" in cause


def test_a_reviewer_that_writes_into_the_checkout_says_a_rerun_usually_passes(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, "echo cache > %s/.reviewer-cache\n%s" % (p, APPROVE))
    r = run_reviewer(p)
    assert r.returncode == 1 and "re-run usually passes" in r.stderr
    assert run_reviewer(p).returncode == 0                                # the file now exists in the pre-run key


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


# ---- attack review of #1384 @9d7c29c --------------------------------------------------------------

def test_a_failed_receipt_blocks_approval_even_when_the_route_later_resolves_inline(tmp_path, monkeypatch):
    """D1: the owner ruling. The codex binary vanishes after the failed run, so resolve() says inline."""
    bindir = _stub_host(tmp_path, monkeypatch, "codex", "exit 1\n")
    p = _project(tmp_path, review={"host": "codex"})
    assert run_reviewer(p).returncode == 1
    bare = _without(bindir)
    r = record_review(p, env=bare)
    assert r.returncode == 2 and "FAILED" in r.stderr, (r.stdout, r.stderr)
    (p / "brief.md").write_text(brief_text("plan-review"))
    assert run_reviewer(p, phase="plan_review", write_brief=False).returncode == 1
    r = record_plan(p, env=bare)
    assert r.returncode == 2 and "FAILED" in r.stderr, (r.stdout, r.stderr)


def test_a_configured_host_that_is_not_installed_refuses_approval(tmp_path):
    p = _project(tmp_path, review={"host": "codex"})
    r = record_review(p, env={**os.environ, "PATH": "/usr/bin:/bin"})
    assert r.returncode == 2 and "configured" in r.stderr and "not installed" in r.stderr
    r = record_plan(p, env={**os.environ, "PATH": "/usr/bin:/bin"})
    assert r.returncode == 2 and "not installed" in r.stderr


def test_a_machine_with_no_mechanism_and_nothing_configured_still_reviews_inline(tmp_path):
    p = _project(tmp_path, review={})
    r = record_review(p)
    assert r.returncode == 0, r.stderr


def test_independent_off_ignores_a_failed_receipt(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, FAILURES["nonzero_exit"]); run_reviewer(p)
    cfg = json.loads((p / ".sdlc/config.json").read_text()); cfg["review"] = {"independent": False}
    (p / ".sdlc/config.json").write_text(json.dumps(cfg))
    assert record_review(p).returncode == 0


def _generation(d, goal="2577"):
    root = pathlib.Path(d) / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    (gen / "brief.md").write_text("brief")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"generation_id": "gen", "goal": goal, "brief": "review-generations/gen/brief.md",
                                    "brief_sha256": hashlib.sha256(b"brief").hexdigest()}))
    resolution = gen / "resolution.json"
    resolution.write_text(json.dumps(_rv().resolve(d)))
    return manifest, resolution


def _work():
    spec = importlib.util.spec_from_file_location("work_f02", S / "work.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_the_pr_review_inline_branch_refuses_a_degraded_route_and_a_failed_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    p = _project(tmp_path, review={"host": "codex"})
    d = str(p / ".sdlc")
    manifest, resolution = _generation(d)
    w = _work()
    assert json.loads(resolution.read_text())["mechanism"] == "inline"
    with pytest.raises(ValueError, match="not installed"):
        w.run_resolved_review(d, "2577", str(manifest), str(resolution), verdict="approve", reason="self")
    # a plain machine with a FAILED receipt for the goal
    q = tmp_path / "q"; q.mkdir()
    p2 = _project(q, review={})
    d2 = str(p2 / ".sdlc")
    _rv()._write_receipt(d2, "2577", "review", outcome="failed", failure="nonzero_exit")
    manifest, resolution = _generation(d2)
    with pytest.raises(ValueError, match="FAILED"):
        w.run_resolved_review(d2, "2577", str(manifest), str(resolution), verdict="approve", reason="self")


def test_lone_verdict_is_none_unless_exactly_one_verdict_is_printed():
    rv = _rv()
    assert rv.lone_verdict("x\nVERDICT: approve\n") == "approve"
    for out in ("VERDICT: approve\nVERDICT: block\n", "VERDICT: block\nVERDICT: approve\n", "no verdict here"):
        assert rv.lone_verdict(out) is None, out


@pytest.mark.parametrize("order", ["approve_first", "block_first"])
def test_a_reviewer_printing_both_verdicts_cannot_be_approved(tmp_path, order):
    p = _project(tmp_path)
    lines = ["VERDICT: approve", "VERDICT: block"]
    reviewer_script(p, "".join("echo '%s'\n" % x for x in (lines if order == "approve_first" else lines[::-1])))
    assert run_reviewer(p).returncode == 0
    got = json.loads((p / ".sdlc/state/reviewer-runs/0001-x.review.json").read_text())
    assert got["verdict"] is None
    assert record_review(p).returncode == 2


def test_a_later_failed_run_retracts_the_earlier_approving_row_in_the_phase_record(tmp_path):
    p = _project(tmp_path)
    reviewer_script(p, APPROVE); run_reviewer(p)
    assert record_review(p).returncode == 0
    for verb, extra in (("start", ["--model", "sonnet"]), ("end", [])):
        subprocess.run([sys.executable, str(S / "phase_report.py"), verb, ".sdlc", GOAL, "review", *extra],
                       cwd=p, capture_output=True, text=True, check=True)
    spec = importlib.util.spec_from_file_location("pg_f02", S / "phase_gate.py")
    pg = importlib.util.module_from_spec(spec); spec.loader.exec_module(pg)
    cfg = json.loads((p / ".sdlc/config.json").read_text())
    def row():
        return next(r for r in pg.status(str(p / ".sdlc"), cfg, GOAL)["rows"] if r[0] == "review")
    assert "reviewer failed" not in row()[2]
    reviewer_script(p, FAILURES["nonzero_exit"]); run_reviewer(p)
    name, ok, detail, fix = row()
    assert ok is False and "independent reviewer failed" in detail and "reviewer_down" in fix


def test_the_repro_host_codex_missing_from_path_refuses_with_and_without_a_receipt(tmp_path, monkeypatch):
    """Coordinator's exact repro: review.host=codex, codex gone from PATH, (a) failed receipt, (b) no receipt."""
    bindir = _stub_host(tmp_path, monkeypatch, "codex", "exit 1\n")
    p = _project(tmp_path, review={"host": "codex"})
    bare = _without(bindir)
    r = record_review(p, env=bare)                                   # (b) no receipt at all
    assert r.returncode == 2 and "not installed" in r.stderr, (r.stdout, r.stderr)
    assert run_reviewer(p).returncode == 1                           # (a) a failed receipt
    r = record_review(p, env=bare)
    assert r.returncode == 2 and "FAILED" in r.stderr


def test_the_decision_is_made_from_config_not_from_a_flag(tmp_path):
    rv = _rv()
    inline = {"mechanism": "inline"}
    for review, expect in (({"host": "codex"}, True), ({"host": "cursor"}, True), ({"host": "claude"}, True),
                           ({"host": "command"}, True), ({"host": "bogus"}, True),
                           ({"command": "sh x"}, True),                       # auto + a command configured
                           ({"host": "inline"}, False), ({}, False), ({"host": "auto"}, False)):
        assert rv.configured_reviewer_unavailable({"review": review}, inline) is expect, review
    assert rv.configured_reviewer_unavailable({"review": {"host": "codex"}}, {"mechanism": "process"}) is False
    assert rv.configured_reviewer_unavailable({"review": {"host": "codex"}}, {"mechanism": "subagent"}) is False

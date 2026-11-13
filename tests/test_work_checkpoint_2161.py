"""#2161: a `--land` run's work survives its container. Real git against a bare remote, so a push that is
refused, rejected or leased is the real thing; only the issue (a `gh` read and a comment) is a seam."""
import importlib.util
import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


work = _load("work")
loop = _load("loop")
rc = _load("review_context")

GOAL = "2161"
CONFIG = {"work": {"enabled": True, "base": "main"}}


def git(cwd, *argv):
    out = subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(cwd),
                             "GIT_CONFIG_NOSYSTEM": "1"})
    assert out.returncode == 0, (argv, out.stderr)
    return out.stdout.strip()


class Fake:
    """Records the one-and-only seams: the issue's durable plan copies and the comment posted on it."""
    def __init__(self):
        self.shas, self.notes, self.merged = set(), [], False


@pytest.fixture
def env(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        git(seed, "config", k, v)
    (seed / "README.md").write_text("base\n")
    (seed / ".gitignore").write_text(".sdlc/work/\n.sdlc/state/\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "base")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "origin", "main")
    root = tmp_path / "root"
    git(tmp_path, "clone", "-q", str(remote), str(root))
    for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)
    sdlc = root / ".sdlc"
    (sdlc / "plans").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps(CONFIG))
    fake = Fake()
    monkeypatch.setattr(work, "_durable_plan_shas", lambda *a, **k: set(fake.shas))
    monkeypatch.setattr(work, "_post_issue_note", lambda sd, c, g, run, r, body: fake.notes.append(body) or "")
    monkeypatch.setattr(work, "_head_pr_merged", lambda *a, **k: fake.merged)
    monkeypatch.setattr(work, "_durable_plan_copy", lambda *a, **k: ("", "durable copy FAILED: seam"))
    fake.root, fake.sdlc, fake.remote, fake.seed = root, sdlc, remote, seed
    return fake


def _start(env, config=CONFIG, **kw):
    return work.start(str(env.sdlc), config, GOAL, **kw)


def _wt(env):
    return pathlib.Path(work._record(str(env.sdlc), GOAL)["worktree"])


def _commit_file(env, name="x.txt", text="x\n", plan=None):
    wt = _wt(env)
    (wt / name).write_text(text)
    if plan is not None:
        (wt / ".sdlc" / "plans").mkdir(parents=True, exist_ok=True)
        (wt / ".sdlc" / "plans" / f"{GOAL}.md").write_text(plan)
    return work.commit(str(env.sdlc), CONFIG, GOAL)


def _remote_sha(env, ref):
    out = subprocess.run(["git", "ls-remote", "--heads", str(env.remote), f"refs/heads/{ref}"],
                         capture_output=True, text=True).stdout.split()
    return out[0] if out else ""


def _plan_sha(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()



class Recorder:
    """A `run` that is the real one, and remembers every argv; `fail` makes a matching call raise."""
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, cwd, argv):
        line = " ".join(str(a) for a in argv)
        self.calls.append(line)
        if self.fail and self.fail in line:
            raise RuntimeError(f"{line}: fatal: unable to access the remote")
        return work._run(cwd, argv)

    def pushes(self):
        return [c for c in self.calls if c.startswith("git push")]


def _container_lost(env, tmp_path, monkeypatch):
    """A fresh container: a new clone of the remote, no work record, no worktree, no logs."""
    root = tmp_path / "root2"
    git(tmp_path, "clone", "-q", str(env.remote), str(root))
    for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)
    (root / ".sdlc").mkdir(exist_ok=True)
    (root / ".sdlc" / "config.json").write_text(json.dumps(CONFIG))
    env.root, env.sdlc = root, root / ".sdlc"
    return env


PLAN = "# plan\n\n- step one\n"


def _worked_and_checkpointed(env, plan=PLAN):
    _start(env)
    assert _commit_file(env, plan=plan).startswith("committed")
    result = work.checkpoint(str(env.sdlc), CONFIG, GOAL)
    assert result.startswith("checkpoint origin/sdlc/2161 at "), result
    return git(_wt(env), "rev-parse", "HEAD")


def test_checkpoint_pushes_the_committed_branch_before_any_pr_without_force(env):
    _start(env)
    _commit_file(env)
    run = Recorder()
    result = work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=run)
    head = git(_wt(env), "rev-parse", "HEAD")
    assert result == f"checkpoint origin/sdlc/2161 at {head[:12]}"
    assert _remote_sha(env, "sdlc/2161") == head
    assert not any(" gh " in f" {c} " for c in run.calls), "a checkpoint opens no PR"
    pushes = run.pushes()
    assert pushes == [f"git push origin HEAD:refs/heads/sdlc/2161"]
    assert not any("force" in c or " -f" in c for c in pushes)
    assert work._record(str(env.sdlc), GOAL)["checkpoint"] == {"ref": "sdlc/2161", "sha": head}


def test_checkpoint_advances_by_plain_fast_forward(env):
    first = _worked_and_checkpointed(env)
    _commit_file(env, "y.txt", "y\n")
    run = Recorder()
    work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=run)
    assert _remote_sha(env, "sdlc/2161") == git(_wt(env), "rev-parse", "HEAD") != first
    assert git(_wt(env), "merge-base", "--is-ancestor", first, "HEAD") == ""


def test_checkpoint_refuses_a_secret_shaped_path_and_pushes_nothing(env):
    _start(env)
    wt = _wt(env)
    (wt / ".env").write_text("A=1\n")
    git(wt, "add", "-f", ".env")                    # past `commit`'s own scan, as a hand-made commit would be
    git(wt, "commit", "-q", "-m", "oops")
    run = Recorder()
    result = work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=run)
    assert result.startswith("REFUSED") and "nothing was pushed" in result
    assert run.pushes() == [] and _remote_sha(env, "sdlc/2161") == ""
    assert "checkpoint" not in work._record(str(env.sdlc), GOAL)


def test_checkpoint_refuses_secret_shaped_content_and_never_prints_it(env):
    _start(env)
    wt = _wt(env)
    value = "AKIA" + "QWERTYUIOPASDFGH"
    (wt / "settings.txt").write_text(f"aws_key = {value}\n")
    git(wt, "add", "-f", "settings.txt")
    git(wt, "commit", "-q", "-m", "oops")
    run = Recorder()
    result = work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=run)
    assert result.startswith("REFUSED"), result
    assert value not in result
    assert run.pushes() == [] and _remote_sha(env, "sdlc/2161") == ""


def test_checkpoint_refuses_when_the_content_scan_cannot_read_the_diff(env, monkeypatch):
    _start(env)
    _commit_file(env)

    def boom(*a, **k):
        raise RuntimeError("scanner down")
    monkeypatch.setattr(work, "_added_secret_hits", boom)
    run = Recorder()
    assert work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=run).startswith("REFUSED")
    assert run.pushes() == []


def test_checkpoint_refuses_a_dirty_worktree(env):
    _start(env)
    _commit_file(env)
    (_wt(env) / "dirty.txt").write_text("d\n")
    run = Recorder()
    assert "uncommitted changes" in work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=run)
    assert run.pushes() == []


def test_a_transport_failure_warns_and_does_not_stop_the_run(env, capsys):
    _start(env)
    _commit_file(env)
    result = work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=Recorder(fail="git push"))
    assert result.startswith("WARN: checkpoint not pushed") and "the run continues" in result
    assert "checkpoint" not in work._record(str(env.sdlc), GOAL)
    assert work.main(["work.py", "checkpoint", str(env.sdlc), GOAL]) == 0     # the CLI verb exits 0 on a plain push too


def test_checkpoint_prefix_keeps_the_push_off_the_goal_branch(env):
    config = {"work": {"enabled": True, "base": "main", "checkpoint_prefix": "wip/"}}
    _start(env, config)
    _commit_file(env)
    result = work.checkpoint(str(env.sdlc), config, GOAL)
    assert result.startswith("checkpoint origin/wip/2161 at ")
    assert _remote_sha(env, "wip/2161") == git(_wt(env), "rev-parse", "HEAD")
    assert _remote_sha(env, "sdlc/2161") == ""


def test_resume_cuts_from_the_checkpoint_and_re_enters_at_the_open_phase(env, tmp_path, monkeypatch):
    head = _worked_and_checkpointed(env)
    env.shas = {_plan_sha(PLAN)}
    _container_lost(env, tmp_path, monkeypatch)
    sdlc = str(env.sdlc)
    (env.sdlc / "config.json").write_text(json.dumps({**CONFIG, "action_log": {"enabled": True}}))
    actionlog = _load("actionlog")
    actionlog.safe_append(sdlc, GOAL, "phase", phase="implement", state="start")
    actionlog.safe_append(sdlc, GOAL, "phase", phase="implement", state="end")
    actionlog.safe_append(sdlc, GOAL, "phase", phase="review", state="start")
    out = work.start(sdlc, CONFIG, GOAL, land=True)
    assert out.startswith(f"resumed from origin/sdlc/2161 at {head[:12]}"), out
    assert "re-enter at P6 REVIEW" in out
    assert git(_wt(env), "rev-parse", "HEAD") == head and (_wt(env) / "x.txt").exists()
    rec = work._record(sdlc, GOAL)
    assert rec["checkpoint"] == {"ref": "sdlc/2161", "sha": head} and "checkpoint_lease" not in rec
    assert env.notes == []


def test_a_resume_with_no_log_re_enters_at_implement_and_skips_no_later_gate(env, tmp_path, monkeypatch):
    _worked_and_checkpointed(env)
    env.shas = {_plan_sha(PLAN)}
    _container_lost(env, tmp_path, monkeypatch)
    out = work.start(str(env.sdlc), CONFIG, GOAL, land=True)
    assert "re-enter at P5 IMPLEMENT" in out and "plan-review, review and verify run again" in out
    gates_dir = env.sdlc / "state" / "gates"
    assert not gates_dir.exists() or not list(gates_dir.iterdir()), "no verdict is carried over"
    strict = {**CONFIG, "gates": {"plan_review": {"enabled": True}}}
    assert "gates.plan_review is on" in work.pr(str(env.sdlc), strict, GOAL, run=Recorder(fail="NEVER"))


def _leftover(env, name="sdlc/2161", plan=None):
    """An unmerged branch left on the remote by an earlier run."""
    git(env.seed, "checkout", "-q", "-b", name, "main")
    (env.seed / "old.txt").write_text("old run\n")
    if plan is not None:
        (env.seed / ".sdlc" / "plans").mkdir(parents=True, exist_ok=True)
        (env.seed / ".sdlc" / "plans" / f"{GOAL}.md").write_text(plan)
    git(env.seed, "add", "-A", "-f")
    git(env.seed, "commit", "-q", "-m", "old run")
    git(env.seed, "push", "-q", "origin", name)
    git(env.seed, "checkout", "-q", "main")
    return _remote_sha(env, name)


def test_resume_merges_a_moved_base_so_the_remote_branch_stays_an_ancestor(env, tmp_path, monkeypatch):
    old = _worked_and_checkpointed(env)
    env.shas = {_plan_sha(PLAN)}
    (env.seed / "other.txt").write_text("o\n")
    git(env.seed, "add", "-A")
    git(env.seed, "commit", "-q", "-m", "base moved")
    git(env.seed, "push", "-q", "origin", "main")
    _container_lost(env, tmp_path, monkeypatch)
    out = work.start(str(env.sdlc), CONFIG, GOAL, land=True)
    assert out.startswith("resumed from"), out
    wt = _wt(env)
    assert git(wt, "rev-parse", "HEAD") != old and (wt / "other.txt").exists() and (wt / "x.txt").exists()
    git(wt, "merge-base", "--is-ancestor", old, "HEAD")                    # the old tip is still in the history
    assert len(git(wt, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3, "a merge commit, not a rewrite"
    run = Recorder()
    assert work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=run).startswith("checkpoint origin/sdlc/2161")
    assert run.pushes() == ["git push origin HEAD:refs/heads/sdlc/2161"]
    assert _remote_sha(env, "sdlc/2161") == git(wt, "rev-parse", "HEAD")


def _assert_fresh_branch(env, old, out, why):
    assert why in out and "working on sdlc/2161-2" in out, out
    assert _remote_sha(env, "sdlc/2161") == old, "the old branch is untouched"
    assert _remote_sha(env, "sdlc/2161-previous") == "", "no copy is made: the old branch is simply left"
    rec = work._record(str(env.sdlc), GOAL)
    assert rec["branch"] == "sdlc/2161-2" and "checkpoint" not in rec
    wt = _wt(env)
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "sdlc/2161-2"
    assert git(wt, "rev-parse", "HEAD") == git(wt, "rev-parse", "origin/main") and not (wt / "x.txt").exists()
    assert len(env.notes) == 1 and "`sdlc/2161`" in env.notes[0] and "`sdlc/2161-2`" in env.notes[0]
    _commit_file(env)
    run = Recorder()
    assert work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=run).startswith("checkpoint origin/sdlc/2161-2 at ")
    assert run.pushes() == ["git push origin HEAD:refs/heads/sdlc/2161-2"]
    assert _remote_sha(env, "sdlc/2161") == old


def test_a_plan_that_changed_falls_back_to_a_fresh_branch_name(env, tmp_path, monkeypatch):
    old = _worked_and_checkpointed(env)
    env.shas = {_plan_sha("# a different, later plan\n")}         # the issue's copy is not the branch's plan
    _container_lost(env, tmp_path, monkeypatch)
    out = work.start(str(env.sdlc), CONFIG, GOAL, land=True)
    _assert_fresh_branch(env, old, out, "matches no durable copy")


def test_a_merge_conflict_falls_back_the_same_way_and_leaves_no_half_merge(env, tmp_path, monkeypatch):
    _start(env)
    _commit_file(env, "README.md", "mine\n", plan=PLAN)
    work.checkpoint(str(env.sdlc), CONFIG, GOAL)
    old = _remote_sha(env, "sdlc/2161")
    env.shas = {_plan_sha(PLAN)}
    (env.seed / "README.md").write_text("theirs\n")
    git(env.seed, "commit", "-q", "-am", "base moved")
    git(env.seed, "push", "-q", "origin", "main")
    _container_lost(env, tmp_path, monkeypatch)
    run = Recorder()
    out = work.start(str(env.sdlc), CONFIG, GOAL, land=True, run=run)
    assert "conflicts or fails" in out and _remote_sha(env, "sdlc/2161") == old
    merge_at = next(i for i, c in enumerate(run.calls) if c.startswith("git merge --no-edit"))
    assert run.calls[merge_at + 1] == "git merge --abort"
    assert git(_wt(env), "status", "--porcelain") == ""
    assert git(env.root, "branch", "--list", "sdlc/2161") == "", "the failed resume's own branch was taken back"
    assert work._record(str(env.sdlc), GOAL)["branch"] == "sdlc/2161-2"


def test_an_unreadable_durable_copy_falls_back_rather_than_resuming(env, tmp_path, monkeypatch):
    old = _worked_and_checkpointed(env)

    def unreadable(*a, **k):
        raise RuntimeError("HTTP 502")
    monkeypatch.setattr(work, "_durable_plan_shas", unreadable)
    _container_lost(env, tmp_path, monkeypatch)
    out = work.start(str(env.sdlc), CONFIG, GOAL, land=True)
    _assert_fresh_branch(env, old, out, "HTTP 502")


def test_a_squash_merged_leftover_is_not_resumable(env, tmp_path, monkeypatch):
    old = _worked_and_checkpointed(env)
    env.shas, env.merged = {_plan_sha(PLAN)}, True                   # everything else says "resume"
    _container_lost(env, tmp_path, monkeypatch)
    out = work.start(str(env.sdlc), CONFIG, GOAL, land=True)
    _assert_fresh_branch(env, old, out, "its pull request already merged")


def test_an_unreadable_merged_state_is_not_resumable(env, tmp_path, monkeypatch):
    old = _worked_and_checkpointed(env)
    env.shas = {_plan_sha(PLAN)}

    def unreadable(*a, **k):
        raise RuntimeError("HTTP 502")
    monkeypatch.setattr(work, "_head_pr_merged", unreadable)
    _container_lost(env, tmp_path, monkeypatch)
    out = work.start(str(env.sdlc), CONFIG, GOAL, land=True)
    _assert_fresh_branch(env, old, out, "could not tell whether it already merged")


def test_a_leftover_branch_without_land_is_never_overwritten_and_the_warning_says_it_moved(env):
    old = _leftover(env)
    _start(env)
    _commit_file(env)
    result = work.checkpoint(str(env.sdlc), CONFIG, GOAL)
    assert result.startswith("WARN: checkpoint not pushed: origin/sdlc/2161 has commits this worktree does not"), result
    assert "non-fast-forward" in result and "pull --no-rebase" in result and "transport" not in result
    assert _remote_sha(env, "sdlc/2161") == old


def test_a_transport_failure_is_not_reported_as_a_moved_branch(env):
    _start(env)
    _commit_file(env)
    result = work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=Recorder(fail="git push"))
    assert "the branch moved" not in result and "unable to access" in result


def test_the_leftover_branch_with_land_gets_a_fresh_name_and_the_old_one_is_untouched(env):
    old = _leftover(env)                                              # no plan on it: it cannot be resumed
    out = _start(env, land=True)
    assert "the branch holds no plan" in out and "working on sdlc/2161-2" in out, out
    assert _remote_sha(env, "sdlc/2161") == old and work._record(str(env.sdlc), GOAL)["branch"] == "sdlc/2161-2"


def test_the_fresh_name_is_the_first_one_absent_on_the_remote(env):
    _leftover(env)
    two = _leftover(env, "sdlc/2161-2")
    out = _start(env, land=True)
    assert "working on sdlc/2161-3" in out, out
    assert _remote_sha(env, "sdlc/2161-2") == two


def test_a_fresh_name_is_also_checked_for_its_prefixed_checkpoint_ref(env):
    config = {"work": {"enabled": True, "base": "main", "checkpoint_prefix": "wip/"}}
    _leftover(env, "wip/2161")
    out = _start(env, config, land=True)
    assert "working on sdlc/2161-2" in out, out
    _commit_file(env)
    assert work.checkpoint(str(env.sdlc), config, GOAL).startswith("checkpoint origin/wip/2161-2 at ")


def test_a_fresh_name_whose_checkpoint_ref_is_taken_is_skipped(env):
    config = {"work": {"enabled": True, "base": "main", "checkpoint_prefix": "wip/"}}
    _leftover(env, "wip/2161")
    _leftover(env, "wip/2161-2")                  # sdlc/2161-2 itself is free, its checkpoint ref is not
    out = _start(env, config, land=True)
    assert "working on sdlc/2161-3" in out, out


def test_a_branch_the_base_already_contains_is_not_a_resume(env):
    git(env.seed, "checkout", "-q", "-b", "sdlc/2161")
    git(env.seed, "push", "-q", "origin", "sdlc/2161")          # same tip as main: nothing unmerged
    out = _start(env, land=True)
    assert out.startswith("worktree ") and "resume" not in out
    assert work._record(str(env.sdlc), GOAL)["branch"] == "sdlc/2161"


def test_a_start_without_land_makes_no_remote_branch_read(env):
    run = Recorder()
    _start(env, run=run)
    assert not any("ls-remote" in c for c in run.calls)


def test_a_pr_that_fails_after_the_push_can_be_retried_with_a_new_commit(env):
    _start(env)
    _commit_file(env)
    base = Recorder()
    state = {"fail": True}

    def run(cwd, argv):
        if argv[0] == "gh":
            if state["fail"]:
                raise RuntimeError("gh: HTTP 502")
            return "7"
        return base(cwd, argv)
    try:
        work.pr(str(env.sdlc), CONFIG, GOAL, run=run)
        raise AssertionError("pr should have failed")
    except RuntimeError:
        pass
    assert _remote_sha(env, "sdlc/2161") == git(_wt(env), "rev-parse", "HEAD")      # the push had happened
    _commit_file(env, "y.txt", "y\n")
    state["fail"] = False
    assert work.pr(str(env.sdlc), CONFIG, GOAL, run=run) == "PR #7"
    assert _remote_sha(env, "sdlc/2161") == git(_wt(env), "rev-parse", "HEAD")
    assert not any("force" in c or " -f " in c for c in base.pushes())


GH_CONFIG = {**CONFIG, "discovery": {"source": "github"}}


def test_release_audit_names_the_checkpoint_branch_and_its_last_commit(env):
    head = _worked_and_checkpointed(env)
    assert work.checkpoint_audit(str(env.sdlc), GH_CONFIG, GOAL) == (
        f"checkpoint branch origin/sdlc/2161, last commit {head[:12]}")


def test_release_audit_works_without_the_work_record_and_lists_the_numbered_branches(env, tmp_path, monkeypatch):
    first = _worked_and_checkpointed(env)
    second = _leftover(env, "sdlc/2161-2")
    _container_lost(env, tmp_path, monkeypatch)                          # another machine: no record at all
    assert work._record(str(env.sdlc), GOAL) is None
    assert work.checkpoint_audit(str(env.sdlc), GH_CONFIG, GOAL) == (
        f"checkpoint branches origin/sdlc/2161 (last commit {first[:12]}), origin/sdlc/2161-2 (last commit {second[:12]})")


def test_release_audit_honours_the_checkpoint_prefix_without_a_record(env, tmp_path, monkeypatch):
    config = {"work": {"enabled": True, "base": "main", "checkpoint_prefix": "wip/"}, "discovery": {"source": "github"}}
    _start(env, CONFIG)
    _commit_file(env)
    work.checkpoint(str(env.sdlc), {**config, "discovery": {}}, GOAL)
    head = git(_wt(env), "rev-parse", "HEAD")
    _container_lost(env, tmp_path, monkeypatch)
    assert work.checkpoint_audit(str(env.sdlc), config, GOAL) == f"checkpoint branch origin/wip/2161, last commit {head[:12]}"


def test_release_audit_is_empty_with_no_checkpoint_or_work_off_or_a_local_goal(env):
    assert work.checkpoint_audit(str(env.sdlc), GH_CONFIG, GOAL) == ""
    _worked_and_checkpointed(env)
    assert work.checkpoint_audit(str(env.sdlc), CONFIG, GOAL) == ""                      # a local backlog
    assert work.checkpoint_audit(str(env.sdlc), {"discovery": {"source": "github"}}, GOAL) == ""   # work off


def test_release_audit_says_it_could_not_read_the_remote_instead_of_raising(env):
    _worked_and_checkpointed(env)

    def down(cwd, argv):
        raise RuntimeError("fatal: unable to access")
    assert work.checkpoint_audit(str(env.sdlc), GH_CONFIG, GOAL, run=down) == (
        "could not read the remote for a checkpoint branch")


def test_release_audit_read_is_bounded_by_a_timeout(env, monkeypatch):
    _worked_and_checkpointed(env)
    seen = {}

    def hang(argv, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(work.subprocess, "run", hang)
    assert work.checkpoint_audit(str(env.sdlc), GH_CONFIG, GOAL) == "could not read the remote for a checkpoint branch"
    assert seen["timeout"] == work.CHECKPOINT_AUDIT_TIMEOUT_S and seen["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_loop_release_puts_the_audit_in_the_comment_and_not_in_the_ledger(env, monkeypatch):
    head = _worked_and_checkpointed(env)
    (env.sdlc / "config.json").write_text(json.dumps(GH_CONFIG))
    seen = []

    class Source:
        def release(self, goal, reason):
            seen.append(reason)
            return True
    ledger_calls = []
    monkeypatch.setattr(loop.ledger, "safe_append", lambda *a, **k: ledger_calls.append((a, k)))
    loop._release(str(env.sdlc), Source(), GOAL, reason="session died")
    assert seen == [f"session died; checkpoint branch origin/sdlc/2161, last commit {head[:12]}"]
    assert [k["why"] for a, k in ledger_calls if a[1] == "release"] == ["session died"]


def test_no_force_push_exists_in_the_checkpoint_code():
    text = (SCRIPTS / "work.py").read_text()
    block = text[text.index("def _checkpoint_ref"):text.index("def _receipt_pending")]
    assert "force-with-lease" not in block and "checkpoint_lease" not in text and "-previous" not in block
    assert [l.strip() for l in block.splitlines() if "--force" in l] == [
        'for argv in (["git", "worktree", "remove", "--force", str(path)], ["git", "branch", "-D", branch]):'], \
        "the only --force is the removal of the worktree this very call just made"


def test_land_prose_names_checkpoint_before_verify_and_the_resume():
    text = (ROOT / "skills" / "sigma-goal" / "references" / "land.md").read_text()
    assert 'work.py" checkpoint' in text and text.index('work.py" checkpoint') < text.index('loop.py" verify')
    assert "<branch>-2" in text and "start .sdlc" in text and "-previous" not in text and "no push in this flow is a force push" in text

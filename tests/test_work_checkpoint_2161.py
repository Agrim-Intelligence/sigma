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
        self.shas, self.notes = set(), []


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


def test_resume_rebases_onto_a_moved_base_and_pins_a_lease_for_the_rewritten_branch(env, tmp_path, monkeypatch):
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
    assert work._record(str(env.sdlc), GOAL)["checkpoint_lease"] == {"ref": "sdlc/2161", "sha": old, "kept": ""}
    run = Recorder()
    assert work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=run).startswith("checkpoint origin/sdlc/2161")
    assert run.pushes() == [f"git push --force-with-lease=refs/heads/sdlc/2161:{old} origin HEAD:refs/heads/sdlc/2161"]
    assert _remote_sha(env, "sdlc/2161") == git(wt, "rev-parse", "HEAD")


def test_a_plan_that_changed_falls_back_to_previous_and_recuts(env, tmp_path, monkeypatch):
    old = _worked_and_checkpointed(env)
    env.shas = {_plan_sha("# a different, later plan\n")}         # the issue's copy is not the branch's plan
    _container_lost(env, tmp_path, monkeypatch)
    out = work.start(str(env.sdlc), CONFIG, GOAL, land=True)
    assert "could not resume from origin/sdlc/2161" in out and "kept it as sdlc/2161-previous" in out, out
    assert _remote_sha(env, "sdlc/2161-previous") == old
    assert len(env.notes) == 1 and "sdlc/2161-previous" in env.notes[0] and old[:12] in env.notes[0]
    wt = _wt(env)
    assert git(wt, "rev-parse", "HEAD") == git(wt, "rev-parse", "origin/main") and not (wt / "x.txt").exists()
    rec = work._record(str(env.sdlc), GOAL)
    assert "checkpoint" not in rec and rec["checkpoint_lease"]["sha"] == old


def test_a_rebase_conflict_falls_back_the_same_way(env, tmp_path, monkeypatch):
    _start(env)
    _commit_file(env, "README.md", "mine\n", plan=PLAN)
    work.checkpoint(str(env.sdlc), CONFIG, GOAL)
    old = _remote_sha(env, "sdlc/2161")
    env.shas = {_plan_sha(PLAN)}
    (env.seed / "README.md").write_text("theirs\n")
    git(env.seed, "commit", "-q", "-am", "base moved")
    git(env.seed, "push", "-q", "origin", "main")
    _container_lost(env, tmp_path, monkeypatch)
    out = work.start(str(env.sdlc), CONFIG, GOAL, land=True)
    assert "the rebase onto the current base conflicts" in out
    assert _remote_sha(env, "sdlc/2161-previous") == old
    assert git(_wt(env), "status", "--porcelain") == ""             # no half-applied rebase is left behind


def test_an_unreadable_durable_copy_falls_back_rather_than_resuming(env, tmp_path, monkeypatch):
    _worked_and_checkpointed(env)

    def unreadable(*a, **k):
        raise RuntimeError("HTTP 502")
    monkeypatch.setattr(work, "_durable_plan_shas", unreadable)
    _container_lost(env, tmp_path, monkeypatch)
    assert "could not resume" in work.start(str(env.sdlc), CONFIG, GOAL, land=True)


def test_a_leftover_branch_is_never_overwritten_without_a_preserved_copy(env):
    """The old failure: a leftover remote branch made the re-cut's first push a rejection. Without `--land` the start
    is as it was, and the push stays a plain one the remote refuses; nothing the branch held is lost."""
    git(env.seed, "checkout", "-q", "-b", "sdlc/2161")
    (env.seed / "old.txt").write_text("old run\n")
    git(env.seed, "add", "-A")
    git(env.seed, "commit", "-q", "-m", "old run")
    git(env.seed, "push", "-q", "origin", "sdlc/2161")
    old = _remote_sha(env, "sdlc/2161")
    _start(env)
    _commit_file(env)
    result = work.checkpoint(str(env.sdlc), CONFIG, GOAL)
    assert result.startswith("WARN: checkpoint not pushed"), result
    assert _remote_sha(env, "sdlc/2161") == old


def test_the_leftover_branch_with_land_is_kept_then_replaced_under_a_lease(env):
    git(env.seed, "checkout", "-q", "-b", "sdlc/2161")
    (env.seed / "old.txt").write_text("old run\n")
    git(env.seed, "add", "-A")
    git(env.seed, "commit", "-q", "-m", "old run")
    git(env.seed, "push", "-q", "origin", "sdlc/2161")
    old = _remote_sha(env, "sdlc/2161")
    out = _start(env, land=True)
    assert "kept it as sdlc/2161-previous" in out, out
    _commit_file(env)
    run = Recorder()
    assert work.checkpoint(str(env.sdlc), CONFIG, GOAL, run=run).startswith("checkpoint origin/sdlc/2161")
    assert run.pushes() == [f"git push --force-with-lease=refs/heads/sdlc/2161:{old} origin HEAD:refs/heads/sdlc/2161"]
    assert _remote_sha(env, "sdlc/2161-previous") == old
    assert _remote_sha(env, "sdlc/2161") == git(_wt(env), "rev-parse", "HEAD")
    assert "checkpoint_lease" not in work._record(str(env.sdlc), GOAL)


def test_the_lease_refuses_when_the_leftover_branch_moved_after_it_was_kept(env):
    git(env.seed, "checkout", "-q", "-b", "sdlc/2161")
    (env.seed / "old.txt").write_text("old run\n")
    git(env.seed, "add", "-A")
    git(env.seed, "commit", "-q", "-m", "old run")
    git(env.seed, "push", "-q", "origin", "sdlc/2161")
    _start(env, land=True)
    (env.seed / "late.txt").write_text("a commit nobody recorded\n")
    git(env.seed, "add", "-A")
    git(env.seed, "commit", "-q", "-m", "late")
    git(env.seed, "push", "-q", "origin", "sdlc/2161")
    late = _remote_sha(env, "sdlc/2161")
    _commit_file(env)
    assert work.checkpoint(str(env.sdlc), CONFIG, GOAL).startswith("WARN")
    assert _remote_sha(env, "sdlc/2161") == late


def test_a_branch_the_base_already_contains_is_not_a_resume(env):
    git(env.seed, "checkout", "-q", "-b", "sdlc/2161")
    git(env.seed, "push", "-q", "origin", "sdlc/2161")          # same tip as main: nothing unmerged
    out = _start(env, land=True)
    assert out.startswith("worktree ") and "resume" not in out
    assert "checkpoint_lease" not in work._record(str(env.sdlc), GOAL)


def test_a_start_without_land_makes_no_remote_branch_read(env):
    run = Recorder()
    _start(env, run=run)
    assert not any("ls-remote" in c for c in run.calls)


def test_release_audit_names_the_checkpoint_branch_and_its_last_commit(env):
    head = _worked_and_checkpointed(env)
    assert work.checkpoint_audit(str(env.sdlc), CONFIG, GOAL) == (
        f"checkpoint branch origin/sdlc/2161, last commit {head[:12]}")


def test_release_audit_is_empty_with_no_checkpoint_or_no_work(env):
    assert work.checkpoint_audit(str(env.sdlc), CONFIG, GOAL) == ""          # never started
    _start(env)
    assert work.checkpoint_audit(str(env.sdlc), CONFIG, GOAL) == ""          # started, nothing pushed


def test_loop_release_puts_the_audit_in_the_comment_and_not_in_the_ledger(env, monkeypatch):
    head = _worked_and_checkpointed(env)
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


def test_pr_spends_the_lease_on_its_own_first_push(env):
    git(env.seed, "checkout", "-q", "-b", "sdlc/2161")
    (env.seed / "old.txt").write_text("old run\n")
    git(env.seed, "add", "-A")
    git(env.seed, "commit", "-q", "-m", "old run")
    git(env.seed, "push", "-q", "origin", "sdlc/2161")
    old = _remote_sha(env, "sdlc/2161")
    _start(env, land=True)
    _commit_file(env)
    base = Recorder()

    def run(cwd, argv):
        if argv[0] == "gh":
            base.calls.append(" ".join(argv))
            return "7"
        return base(cwd, argv)
    assert work.pr(str(env.sdlc), CONFIG, GOAL, run=run) == "PR #7"
    assert base.pushes() == [f"git push --force-with-lease=refs/heads/sdlc/2161:{old} -u origin sdlc/2161"]
    assert "checkpoint_lease" not in work._record(str(env.sdlc), GOAL)


def test_land_prose_names_checkpoint_before_verify_and_the_resume():
    text = (ROOT / "skills" / "sigma-goal" / "references" / "land.md").read_text()
    assert 'work.py" checkpoint' in text and text.index('work.py" checkpoint') < text.index('loop.py" verify')
    assert "-previous" in text and "start .sdlc" in text

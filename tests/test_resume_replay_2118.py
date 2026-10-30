"""#2118: a goal that resumes on a moved base replays only its OWN commits, falls back to a merge commit
before parking, and never force-pushes a branch that is already pushed.

Real git, a local bare remote, no network."""
import os
import subprocess
import types

import pytest

import upkeep_support as support

FORCE = ("--force", "--force-with-lease", "-f", "--mirror")


def work():
    return support.script("work")


def real_run(cwd, argv):
    argv = [str(a) for a in argv]
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "PATH": os.environ["PATH"],
           "GIT_EDITOR": "true"}
    if argv[0] == "git":
        argv = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"] + argv[1:]
    out = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, env=env)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip() or "exit %d" % out.returncode)
    return out.stdout.strip()


def git(cwd, *argv):
    return real_run(cwd, ["git", *argv])


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cwd, argv):
        self.calls.append([str(a) for a in argv])
        return real_run(cwd, argv)


def pushes(run):
    return [c for c in run.calls if c[:2] == ["git", "push"]]


def forced(call):
    return any(a in FORCE or a.startswith("+") or a.startswith("--force") for a in call[2:])


def unit_commit(w, text, msg):
    git(w.clone, "checkout", "-q", "feature/u")
    (w.clone / "f.txt").write_text(text)
    git(w.clone, "commit", "-q", "-am", msg)
    git(w.clone, "push", "-q", "origin", "feature/u")
    return git(w.clone, "rev-parse", "HEAD")


def goal_commit(w, name, text, msg):
    (w.goal / name).write_text(text)
    git(w.goal, "add", name)
    git(w.goal, "commit", "-q", "-m", msg)


def record(w, cut=True):
    rec = {"worktree": str(w.goal), "branch": "sdlc/9", "base": "feature/u", "remote": "origin",
           "base_resolved": True, "pr": ""}
    if cut:
        rec["cut_tip"] = w.cut
    work()._save(str(w.sdlc), "9", rec)


@pytest.fixture
def world(tmp_path):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(origin, "init", "-q", "--bare", "-b", "main")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), "clone")
    (clone / "f.txt").write_text("1\n2\n3\n")
    git(clone, "add", "f.txt")
    git(clone, "commit", "-q", "-m", "seed")
    git(clone, "branch", "-M", "main")
    git(clone, "push", "-q", "origin", "main")
    git(clone, "checkout", "-q", "-b", "feature/u")
    (clone / "f.txt").write_text("1\n2\nunit-line\n3\n")
    git(clone, "commit", "-q", "-am", "unit work")
    git(clone, "push", "-q", "origin", "feature/u")
    cut = git(clone, "rev-parse", "HEAD")
    goal = tmp_path / "goalwt"
    git(clone, "worktree", "add", "-q", "-b", "sdlc/9", str(goal), "origin/feature/u")
    sdlc = clone / ".sdlc"
    (sdlc / "goals").mkdir(parents=True)
    return types.SimpleNamespace(clone=clone, goal=goal, sdlc=sdlc, cut=cut, origin=origin)


def goal_holds_a_base_commit(w):
    """The shape from the field: the base commit B1 reached the goal branch through a merge from the base, and the base has
    since moved on over the same lines (B2), so replaying B1 on the new base conflicts."""
    unit_commit(w, "1\n2\nB1\n3\n", "B1")
    git(w.goal, "fetch", "-q", "origin", "feature/u")
    git(w.goal, "merge", "-q", "--no-edit", "origin/feature/u")
    goal_commit(w, "g.txt", "goal\n", "goal work")
    git(w.goal, "push", "-q", "origin", "sdlc/9")
    unit_commit(w, "1\n2\nB2\n3\n", "B2")


def test_a_commit_the_new_base_already_has_is_not_replayed(world):
    """AC-1."""
    goal_holds_a_base_commit(world)
    record(world)
    out = work().rebase(str(world.sdlc), {}, "9", run=Recorder())
    assert out == "rebased", out
    assert git(world.goal, "rev-list", "--count", "origin/feature/u..HEAD") == "1"      # only the goal's own commit
    assert git(world.goal, "show", "HEAD:f.txt") == "1\n2\nB2\n3"


def test_the_replay_argv_is_unchanged_when_the_goal_holds_nothing_the_base_has(world):
    unit_commit(world, "1\n2\nB1\n3\n", "B1")
    goal_commit(world, "g.txt", "goal\n", "goal work")
    record(world)
    run = Recorder()
    work().rebase(str(world.sdlc), {}, "9", run=run)
    rebases = [c for c in run.calls if c[:2] == ["git", "rebase"]]
    assert rebases and all("--onto" in c and world.cut in c for c in rebases)


def same_final_content_on_both_sides(w):
    """A rebase conflict whose MERGE is clean: the goal reaches the same bytes in two commits that the unit reached in one,
    so replaying the goal's first commit conflicts and merging the two tips does not."""
    goal_commit(w, "f.txt", "1\n2\nG1\n3\n", "first step")
    goal_commit(w, "f.txt", "1\n2\nFINAL\n3\n", "second step")
    git(w.goal, "push", "-q", "origin", "sdlc/9")
    unit_commit(w, "1\n2\nFINAL\n3\n", "unit reaches the same bytes")


def genuine_conflict(w):
    goal_commit(w, "f.txt", "1\n2\nmine\n3\n", "goal edit")
    git(w.goal, "push", "-q", "origin", "sdlc/9")
    unit_commit(w, "1\n2\ntheirs\n3\n", "unit edit")


def test_a_rebase_conflict_whose_merge_is_clean_produces_a_merge_commit(world):
    """AC-2."""
    same_final_content_on_both_sides(world)
    record(world, cut=False)
    pre = git(world.goal, "rev-parse", "HEAD")
    run = Recorder()
    out = work().rebase(str(world.sdlc), {}, "9", run=run)
    assert out.startswith("rebased") and "merge" in out, out
    assert git(world.goal, "rev-list", "--parents", "-n1", "HEAD").split()[1:] == [pre, git(world.goal, "rev-parse", "origin/feature/u")]
    assert git(world.goal, "rev-parse", "HEAD") == git(world.origin, "rev-parse", "sdlc/9")      # pushed
    assert git(world.goal, "status", "--porcelain") == ""


def test_a_conflict_in_both_the_rebase_and_the_merge_names_the_files(world):
    """AC-3."""
    genuine_conflict(world)
    record(world, cut=False)
    pre = git(world.goal, "rev-parse", "HEAD")
    out = work().rebase(str(world.sdlc), {}, "9", run=Recorder())
    assert out.startswith("rebase deferred") and "conflicts in f.txt" in out, out
    assert git(world.goal, "rev-parse", "HEAD") == pre                      # nothing half applied
    assert git(world.goal, "status", "--porcelain") == ""
    assert git(world.origin, "rev-parse", "sdlc/9") == pre


def test_the_merge_fallback_keeps_uncommitted_work(world):
    same_final_content_on_both_sides(world)
    record(world, cut=False)
    (world.goal / "wip.txt").write_text("wip\n")
    git(world.goal, "add", "wip.txt")
    out = work().rebase(str(world.sdlc), {}, "9", run=Recorder())
    assert out.startswith("rebased"), out
    assert (world.goal / "wip.txt").read_text() == "wip\n"


def test_a_failed_merge_with_uncommitted_work_restores_it(world):
    genuine_conflict(world)
    record(world, cut=False)
    (world.goal / "wip.txt").write_text("wip\n")
    git(world.goal, "add", "wip.txt")
    out = work().rebase(str(world.sdlc), {}, "9", run=Recorder())
    assert out.startswith("rebase deferred") and "f.txt" in out, out
    assert (world.goal / "wip.txt").read_text() == "wip\n"
    assert "UU" not in git(world.goal, "status", "--porcelain")


def test_a_resume_never_force_pushes_a_pushed_branch_after_a_clean_move(world):
    """AC-4, the clean case: the base moved on a file the goal never touched."""
    goal_commit(world, "g.txt", "goal\n", "goal work")
    git(world.goal, "push", "-q", "origin", "sdlc/9")
    unit_commit(world, "1\n2\nB1\n3\n", "B1")
    record(world)
    run = Recorder()
    assert work().ensure_fresh(str(world.sdlc), {}, "9", run=run) is None
    assert pushes(run) and not [c for c in pushes(run) if forced(c)], pushes(run)
    assert git(world.goal, "rev-parse", "HEAD") == git(world.origin, "rev-parse", "sdlc/9")
    assert git(world.goal, "merge-base", "--is-ancestor", "origin/feature/u", "HEAD") == ""


def test_a_resume_never_force_pushes_after_a_conflicting_replay(world):
    """AC-4, the conflict case: the fallback merge is a fast-forward push too."""
    same_final_content_on_both_sides(world)
    record(world, cut=False)
    run = Recorder()
    assert work().ensure_fresh(str(world.sdlc), {}, "9", run=run) is None
    assert pushes(run) and not [c for c in pushes(run) if forced(c)], pushes(run)


def test_a_resume_that_conflicts_everywhere_parks_naming_the_files_and_pushes_nothing(world):
    genuine_conflict(world)
    record(world, cut=False)
    run = Recorder()
    out = work().ensure_fresh(str(world.sdlc), {}, "9", run=run)
    assert out and "f.txt" in out, out
    assert not pushes(run)

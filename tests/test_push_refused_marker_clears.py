"""#2117 AC-3: a push-refusal marker clears once the rebase it describes is gone.

`mark_push_refused` records the lossy head the refused replay left on the branch (`rebased_head`).
`push_refused` keeps refusing while that head is still in the branch or a rebase is running in the
worktree, and clears the marker (no refusal, so no park) the moment neither holds. Real git, real
marker files. Each clause is broken on its own in the controls named in the docstrings.
"""
import importlib.util
import json
import os
import pathlib
import subprocess

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
BRANCH = "sdlc/11"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(cwd, *args, check=True):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), p.stderr or p.stdout))
    return p.stdout.strip()


def _commit(repo, name, text):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", "add " + name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path):
    """A repo on BRANCH: `pre` (the pre-rebase head), then `lossy` (what the refused replay left)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(repo, "base.txt", "base\n")
    _git(repo, "checkout", "-q", "-b", BRANCH)
    pre = _commit(repo, "goal.txt", "goal\n")
    _git(repo, "checkout", "-q", "main")
    other = _commit(repo, "other.txt", "other\n")           # a base that moved on
    _git(repo, "checkout", "-q", BRANCH)
    _git(repo, "reset", "-q", "--hard", other)               # the replay, as the lossy result
    lossy = _commit(repo, "replayed.txt", "replayed\n")
    fr = _load("feature_rebase")
    marker = fr.mark_push_refused(str(repo), BRANCH, pre, "drops goal.txt")
    assert marker and pathlib.Path(marker).exists()
    return {"repo": repo, "fr": fr, "pre": pre, "lossy": lossy, "base": base, "other": other,
            "marker": pathlib.Path(marker)}


def test_the_marker_records_the_lossy_head_it_describes(world):
    data = json.loads(world["marker"].read_text())
    assert data["rebased_head"] == world["lossy"] and data["pre_head"] == world["pre"]


def test_a_marker_whose_lossy_head_is_still_in_the_branch_keeps_refusing(world):
    """Control for the ancestry clause: work stacked on the lossy head still carries the loss."""
    _commit(world["repo"], "more.txt", "more\n")
    assert world["fr"].push_refused(str(world["repo"]), BRANCH)
    assert world["marker"].exists()


def test_ac3_a_marker_with_no_rebase_and_no_lossy_head_left_is_cleared_without_a_refusal(world):
    repo = world["repo"]
    _git(repo, "reset", "-q", "--hard", world["other"])      # the branch was rebuilt: lossy head gone
    _commit(repo, "fresh.txt", "fresh\n")
    assert world["fr"].push_refused(str(repo), BRANCH) is None
    assert not world["marker"].exists()


def test_a_rebase_in_progress_in_the_worktree_keeps_the_marker_standing(world):
    """Control for the in-progress clause: the rebase the marker refers to is still running."""
    repo = world["repo"]
    _git(repo, "reset", "-q", "--hard", world["other"])
    gitdir = pathlib.Path(_git(repo, "rev-parse", "--git-path", "rebase-merge"))
    gitdir = gitdir if gitdir.is_absolute() else repo / gitdir
    gitdir.mkdir()
    assert world["fr"].push_refused(str(repo), BRANCH)
    assert world["marker"].exists()


def test_a_rebase_apply_in_progress_also_keeps_the_marker_standing(world):
    repo = world["repo"]
    _git(repo, "reset", "-q", "--hard", world["other"])
    gitdir = pathlib.Path(_git(repo, "rev-parse", "--git-path", "rebase-apply"))
    gitdir = gitdir if gitdir.is_absolute() else repo / gitdir
    gitdir.mkdir()
    assert world["fr"].push_refused(str(repo), BRANCH)


def test_a_marker_from_before_this_change_has_no_lossy_head_and_still_refuses(world):
    """Control for the legacy clause: nothing says the loss is gone, so fail closed as before."""
    data = json.loads(world["marker"].read_text())
    del data["rebased_head"]
    world["marker"].write_text(json.dumps(data))
    _git(world["repo"], "reset", "-q", "--hard", world["other"])
    assert world["fr"].push_refused(str(world["repo"]), BRANCH)


def test_a_marker_for_another_branch_is_not_cleared_by_this_one(world):
    """The marker names the exact branch; a record that names another branch never clears here."""
    data = json.loads(world["marker"].read_text())
    data["branch"] = "sdlc/other"
    world["marker"].write_text(json.dumps(data))
    _git(world["repo"], "reset", "-q", "--hard", world["other"])
    assert world["fr"].push_refused(str(world["repo"]), BRANCH)
    assert world["marker"].exists()


def test_a_lossy_head_git_can_no_longer_find_counts_as_gone(world):
    data = json.loads(world["marker"].read_text())
    data["rebased_head"] = "0" * 40
    world["marker"].write_text(json.dumps(data))
    _git(world["repo"], "reset", "-q", "--hard", world["other"])
    assert world["fr"].push_refused(str(world["repo"]), BRANCH) is None


def test_ac3_the_goal_is_not_parked_by_a_stale_marker_on_the_next_pick(world):
    """work._push_refused is what rebase()/pr() ask before a push; None means no park."""
    work = _load("work")
    repo = world["repo"]
    assert work._push_refused(str(repo), BRANCH)             # while the lossy head stands: refused
    _git(repo, "reset", "-q", "--hard", world["other"])
    assert work._push_refused(str(repo), BRANCH) is None

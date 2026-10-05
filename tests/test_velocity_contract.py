# SPDX-License-Identifier: MIT
"""Engine-side half of the velocity/git-log counting contract (issue #298, [E15.S4], Decision 4).

This is NOT a data-format contract -- it is an ALGORITHMIC agreement ("same --since window, same
merge-commit counting rule") between two independent implementations
(skills/sigma-velocity/scripts/velocity.py's measure() and a downstream git reader's own
windowed measure). A JSONL fixture cannot represent "the same counting algorithm", so this is not
folded into the contract. Each side instead gets its own behavioral pin against real,
disposable git repositories it builds itself, asserting LITERAL expected counts -- never
comparing live output between the two implementations again (that was
tests/test_git_reader_velocity_parity.py's job; it is deleted, not kept, in this same commit --
keeping it would leave a test that structurally imports the downstream reader sitting in
tests/, exactly the file that breaks silently at collection the day the private side is actually
extracted).

Zero import of the private side anywhere in this file -- velocity.py is loaded via
importlib.util.spec_from_file_location, the same pattern tests/test_velocity.py already uses.

_repo/_git/_commit below are lifted near-verbatim from the deleted parity file, minus every
private-side import."""
import importlib.util
import os
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_VELOCITY_PATH = REPO_ROOT / "skills" / "sigma-velocity" / "scripts" / "velocity.py"


def _velocity():
    spec = importlib.util.spec_from_file_location("velocity", _VELOCITY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root, *args, env=None):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, env=env)


def _repo(root):
    # "-b main": pinned explicitly -- with no system/global gitconfig, git produces
    # refs/heads/master; "main" here relies on nothing but this explicit flag. See the deleted
    # parity file's own identical comment / the downstream reader's own test _repo().
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    return root


def _commit(root, name, message, when=None):
    (root / name).write_text("x\n", encoding="utf-8")
    _git(root, "add", ".")
    env = None
    if when:
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    _git(root, "commit", "-q", "-m", message, env=env)


def test_zero_commits(tmp_path):
    _repo(tmp_path)
    v = _velocity()
    m = v.measure(days=365, run=lambda a: v._git(a, str(tmp_path)))
    assert (m["commits"], m["merges"]) == (0, 0)


def test_commits_only_no_merges(tmp_path):
    _repo(tmp_path)
    for i in range(4):
        _commit(tmp_path, f"f{i}.txt", f"commit {i}")
    v = _velocity()
    m = v.measure(days=365, run=lambda a: v._git(a, str(tmp_path)))
    assert (m["commits"], m["merges"]) == (4, 0)


def test_a_real_two_parent_merge_commit(tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "c1")
    _commit(tmp_path, "b.txt", "c2")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    _commit(tmp_path, "c.txt", "c3 on feature")
    _git(tmp_path, "checkout", "-q", "main")
    _git(tmp_path, "merge", "--no-ff", "-q", "-m", "Merge pull request #1 from x/feature", "feature")
    v = _velocity()
    m = v.measure(days=365, run=lambda a: v._git(a, str(tmp_path)))
    assert (m["commits"], m["merges"]) == (4, 1)


def test_respects_the_since_window_boundary(tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "old.txt", "old commit", when="2020-01-01T00:00:00+00:00")
    _commit(tmp_path, "new.txt", "recent commit")
    v = _velocity()
    m = v.measure(days=30, run=lambda a: v._git(a, str(tmp_path)))
    assert (m["commits"], m["merges"]) == (1, 0)


# The --since-window and non-git-directory scenarios are already covered behaviorally by
# tests/test_velocity.py's existing _fake-runner tests (test_measure_since_window etc.) and are
# not duplicated with real git here -- the exact counting rule does not differ from those, only
# the mechanism producing the counts (a real git subprocess vs. a fake run() injection).


def test_prs_per_day_key_name(tmp_path):
    """Pins the key name itself (issue #298 Amendment 2): velocity.py returns the merges-per-day
    figure under "prs_per_day"; a downstream git reader's windowed measure returns the SAME
    computation under "merges_per_day" (its own sibling pin and "Algorithmic agreements" note
    cover that side). This divergence is a named,
    out-of-scope-for-#298 follow-up, not silently uncovered -- renaming velocity.py's own public
    dict key would itself be an engine-side write-shape change, which the issue's own "do not
    change what the engine writes" rules out here."""
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "c1")
    v = _velocity()
    m = v.measure(days=365, run=lambda a: v._git(a, str(tmp_path)))
    assert m["prs_per_day"] == 0.0
    assert "merges_per_day" not in m

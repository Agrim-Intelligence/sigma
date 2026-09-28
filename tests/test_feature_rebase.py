"""feature_rebase.py -- rebase upkeep for a long-lived feature branch (#1476, epic #1464).

L3 of the branching model. `work.rebase()` already replays a GOAL branch, which has a worktree and
a record; the new thing here is the FEATURE branch, which has neither. Two levels, in order:
integration branch -> feature, then the feature's goal branches onto the rebased feature.

WHY THESE TESTS RUN REAL `git` RATHER THAN AN INJECTED RUNNER. Every other module on this epic is
tested with a fake runner, correctly: they ask git questions and act on the answers, and the
answers are the interesting part. This one FORCE-PUSHES A SHARED BRANCH, which is the single most
destructive operation in the epic, and the three properties that make it safe are properties of
git itself, not of this code:

  * a conflict aborts and leaves BOTH repositories exactly as they were -- no half-applied rebase,
    no stranded worktree, and a remote tip byte-identical to the one measured before;
  * a STALE lease REFUSES rather than overwrites;
  * `--first-parent` is what tells a squash-merge landing apart from a commit somebody typed
    straight onto the branch.

A fake runner asserts what this module BELIEVES about git. Only real git asserts what git does. So
the fixture below builds a throwaway bare "remote" and a real checkout in `tmp_path`, and the
destructive assertions are made against measured shas.

THE ISSUE-FILING EDGE IS INJECTED, because `handoff.create_tracked_issue` opens a real GitHub
issue. `_filer()` captures its arguments so the "a conflict files an issue NAMING THE TWO BRANCHES"
clause of the definition of done is asserted on the text that would have been filed.

WRITTEN AGAINST A MUTATION RUN -- 18 mutants, each probe-gated (proved to behave differently from
HEAD on a concrete input BEFORE the suite was consulted), 17 killed. The eighteenth is the reason
`_rebase_feature` no longer calls `rebase --abort`: its probe measured no behavioural difference at
all, because the `finally` throws the whole worktree away. There is deliberately no `_mod_with`
helper here -- the run was driven from outside the suite so that a harness killed mid-write could
restore the source, which an in-process substitution cannot promise.
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "agrim-loop" / "scripts"
P = SCRIPTS / "feature_rebase.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mod():
    return _load("feature_rebase")


REPO = "org/repo"
UNIT = "billing"
FEATURE = "feature/billing"
INTEGRATION = "main"


# --------------------------------------------------------------------------- the real-git fixture


def _git(cwd, *args):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, p.stderr or p.stdout))
    return p.stdout.strip()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class World:
    """A throwaway remote + checkout, plus the `.sdlc` layer the pass reads."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.remote = self.root / "remote.git"
        self.local = self.root / "local"
        self.sdlc = self.local / ".sdlc"

    # -- construction ------------------------------------------------------
    def build(self, feature_commits=(("f.txt", "f", "feat: land a goal (#11)"),)):
        _git(self.root, "init", "-q", "--bare", str(self.remote))
        _git(self.root, "init", "-q", "-b", INTEGRATION, str(self.local))
        _git(self.local, "remote", "add", "origin", str(self.remote))
        _write(self.local / "seed.txt", "seed\n")
        _git(self.local, "add", "seed.txt")
        _git(self.local, "commit", "-q", "-m", "seed")
        _git(self.local, "push", "-q", "-u", "origin", INTEGRATION)
        if feature_commits is not None:
            _git(self.local, "checkout", "-q", "-b", FEATURE)
            for name, body, subject in feature_commits:
                _write(self.local / name, body + "\n")
                _git(self.local, "add", name)
                _git(self.local, "commit", "-q", "-m", subject)
            _git(self.local, "push", "-q", "origin", FEATURE)
            _git(self.local, "checkout", "-q", INTEGRATION)
        self.write_registry()
        return self

    def move_integration(self, name="m.txt", body="m", subject="integration moves"):
        _git(self.local, "checkout", "-q", INTEGRATION)
        _write(self.local / name, body + "\n")
        _git(self.local, "add", name)
        _git(self.local, "commit", "-q", "-m", subject)
        _git(self.local, "push", "-q", "origin", INTEGRATION)
        return self

    def write_registry(self, goals=(), branch=FEATURE, open_=True, repo=REPO):
        registry = _load("feature_registry")
        features_dir = registry.registry_dir(self.sdlc)
        features_dir.mkdir(parents=True, exist_ok=True)
        registry.write_unit(features_dir, UNIT, {
            "open": open_,
            "repos": {repo: {"branch": branch, "goals": list(goals)}}})

    def goal_branch(self, number, files=(("g.txt", "g"),), base=FEATURE):
        """A real `sdlc/<n>` branch + worktree + work record, exactly as `work.start()` leaves it."""
        work = _load("work")
        branch = "sdlc/%s" % number
        path = self.local / ".sdlc" / "work" / str(number)
        _git(self.local, "fetch", "-q", "origin", base)
        _git(self.local, "worktree", "add", "-q", "-b", branch, str(path), "origin/" + base)
        for name, body in files:
            _write(path / name, body + "\n")
            _git(path, "add", name)
            _git(path, "commit", "-q", "-m", "sdlc: %s" % number)
        _git(path, "push", "-q", "origin", branch)
        work._save(str(self.sdlc), str(number), {
            "worktree": str(path), "branch": branch, "base": base,
            "base_resolved": True, "remote": "origin", "pr": ""})
        return path

    # -- measurement -------------------------------------------------------
    def tip(self, branch):
        out = _git(self.local, "ls-remote", "--heads", "origin", branch)
        return out.split("\t")[0] if out else None

    def dirt(self):
        """Everything that would make either repository un-clean, as one comparable value."""
        return {
            "status": _git(self.local, "status", "--porcelain"),
            "worktrees": sorted(l.split(None, 1)[1] for l in
                                _git(self.local, "worktree", "list", "--porcelain").splitlines()
                                if l.startswith("worktree ")),
            "rebase_dirs": sorted(str(p.relative_to(self.local)) for p in
                                  self.local.glob(".git/**/rebase-*")),
            "remote": {b: self.tip(b) for b in
                       (INTEGRATION, FEATURE, "sdlc/11", "sdlc/12")},
        }


CONFIG = {"work": {"enabled": True, "base": INTEGRATION, "remote": "origin",
                   "branch_prefix": "sdlc/", "worktree_dir": ".sdlc/work",
                   "merge_method": "squash"},
          "discovery": {"source": "github", "github": {"repo": REPO}}}


def _cfg(**work_overrides):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["work"].update(work_overrides)
    return cfg


def _filer(m):
    """Replace the issue filer with a recorder. Returns the list it appends to."""
    filed = []

    def fake(sdlc_dir, config, goal, area, why, **kwargs):
        filed.append({"goal": goal, "area": area, "why": why, **kwargs})
        return {"issue": "999", "warnings": [], "duplicate_of": None}

    m._HANDOFF = types.SimpleNamespace(create_tracked_issue=fake, DEFAULT_PRIORITY="P1")
    return filed


def _upkeep(m, world, goal="7", unit=UNIT, config=None):
    return m.upkeep(str(world.sdlc), config or _cfg(), goal, unit)


# --------------------------------------------------------------------------- the no-ops


def test_a_unit_with_no_feature_branch_is_a_no_op(tmp_path):
    """Definition of done, clause 3. Nothing is fetched, nothing is pushed, nothing is filed."""
    m = _mod()
    world = World(tmp_path).build(feature_commits=None)
    filed = _filer(m)
    before = world.dirt()
    report = _upkeep(m, world)
    assert report["outcome"] == m.NO_BRANCH
    assert report["after"] is None
    assert filed == []
    assert world.dirt() == before


def test_a_feature_already_carrying_the_integration_branch_is_a_no_op(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    _filer(m)
    before = world.dirt()
    report = _upkeep(m, world)
    assert report["outcome"] == m.CURRENT
    assert world.dirt() == before


def test_a_goal_declaring_no_unit_is_a_no_op(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    before = world.dirt()
    report = m.upkeep(str(world.sdlc), _cfg(), "7", None)
    assert report["outcome"] == m.NO_UNIT
    assert world.dirt() == before


def test_a_project_that_has_not_adopted_the_registry_is_a_no_op(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    registry = _load("feature_registry")
    import shutil
    shutil.rmtree(registry.registry_dir(world.sdlc))
    before = world.dirt()
    report = _upkeep(m, world)
    assert report["outcome"] == m.NOT_ADOPTED
    assert world.dirt() == before


# --------------------------------------------------------------------------- the happy path


def test_a_feature_behind_the_integration_branch_is_brought_forward(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    old = world.tip(FEATURE)
    integration = world.tip(INTEGRATION)
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED, report
    new = world.tip(FEATURE)
    assert new != old, "the feature branch was not actually moved"
    assert report["before"] == old and report["after"] == new
    # the integration tip is now an ANCESTOR of the feature tip -- the whole point of the pass
    assert _git(world.local, "rev-list", "--count", "%s..%s" % (new, integration)) == "0"
    # and the feature's own commit survived the replay
    assert "feat: land a goal (#11)" in _git(world.local, "log", "--format=%s", "-1", new)
    assert world.dirt()["rebase_dirs"] == []
    assert world.dirt()["worktrees"] == [str(world.local)]


def test_the_features_goal_branches_are_replayed_onto_the_rebased_feature(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.goal_branch(11)
    world.write_registry(goals=[11])
    _register_agent(world.sdlc, 11, 999999)          # positive evidence that nobody is in it
    world.move_integration()
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED
    assert report["replayed"] == ["sdlc/11"], report
    feature_tip = world.tip(FEATURE)
    goal_tip = world.tip("sdlc/11")
    assert _git(world.local, "rev-list", "--count",
                "%s..%s" % (goal_tip, feature_tip)) == "0", (
        "sdlc/11 was not replayed onto the rebased feature branch")


# --------------------------------------------------------------------------- conflicts as work


def test_a_recorded_goal_with_no_worktree_is_out_of_scope_not_a_deferral(tmp_path):
    """`finish()` unlinks the work record and keeps the branch, so on a mature unit most recorded
    goal numbers have no worktree. Counting each as "skipped" would grow the field without bound and
    drown the one entry that means something."""
    m = _mod()
    world = World(tmp_path).build()
    world.write_registry(goals=[11, 12, 13])
    world.move_integration()
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED
    assert report["replayed"] == []
    assert report["skipped"] == [], report["skipped"]


def test_a_goal_branch_based_somewhere_else_is_not_replayed_onto_this_feature(tmp_path):
    """A goal number recorded against the unit whose branch was cut from the integration branch --
    the shape a `feature:` label added AFTER the pick produces. Replaying it onto the feature would
    silently retarget somebody else's branch."""
    m = _mod()
    world = World(tmp_path).build()
    world.goal_branch(12, base=INTEGRATION)
    world.write_registry(goals=[12])
    _register_agent(world.sdlc, 12, 999999)
    world.move_integration()
    before_goal_tip = world.tip("sdlc/12")
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED
    assert report["replayed"] == []
    assert [s["why"] for s in report["skipped"]] == ["based on 'main', not feature/billing"]
    assert world.tip("sdlc/12") == before_goal_tip


def test_a_conflict_rebasing_the_feature_files_an_issue_and_leaves_both_repos_clean(tmp_path):
    m = _mod()
    world = World(tmp_path).build(
        feature_commits=(("seed.txt", "feature edit", "feat: touch the seed (#11)"),))
    world.move_integration(name="seed.txt", body="integration edit")
    filed = _filer(m)
    before = world.dirt()
    report = _upkeep(m, world)
    assert report["outcome"] == m.CONFLICT, report
    assert world.dirt() == before, "a conflict must leave both repositories exactly as they were"
    assert len(filed) == 1
    text = filed[0]["title"] + " " + filed[0]["body"]
    assert FEATURE in text and INTEGRATION in text, text
    assert filed[0]["blocks_goal"] is False


def test_a_conflict_replaying_a_goal_branch_files_an_issue_naming_both_branches(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.goal_branch(11, files=(("shared.txt", "goal side"),))
    world.write_registry(goals=[11])
    _register_agent(world.sdlc, 11, 999999)          # positive evidence that nobody is in it
    filed = _filer(m)
    # the integration branch edits the same file the goal branch did, so the goal's replay onto the
    # rebased feature conflicts while the FEATURE's own rebase does not
    world.move_integration(name="shared.txt", body="integration side")
    goal_tip = world.tip("sdlc/11")
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED
    assert report["conflicts"] == ["sdlc/11"], report
    assert report["replayed"] == []
    # BOTH repositories: no rebase left half-applied locally, the goal's own tree untouched, and the
    # goal branch on the remote still exactly where it was.
    assert world.dirt()["rebase_dirs"] == []
    assert _git(world.local / ".sdlc" / "work" / "11", "status", "--porcelain") == ""
    assert world.tip("sdlc/11") == goal_tip
    assert len(filed) == 1
    text = filed[0]["title"] + " " + filed[0]["body"]
    assert "sdlc/11" in text and FEATURE in text, text
    assert filed[0]["blocks_goal"] is False and filed[0]["immediately_actionable"] is False


# --------------------------------------------------------------------------- the lease


def test_a_stale_lease_refuses_rather_than_overwrites(tmp_path):
    """The most destructive thing in this epic, pinned against real git.

    The pass measures the feature tip, and between that measurement and its push somebody else
    lands on the branch. `--force-with-lease` must REFUSE -- the remote keeps the other writer's
    commit, and the pass reports rather than pretends."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()

    pushes = []
    real_run = m._run

    def racing_run(cwd, argv):
        if argv[:2] == ["git", "push"]:
            pushes.append(list(argv))
            # a third party lands on the feature branch AFTER the lease value was read
            _git(world.local, "checkout", "-q", FEATURE)
            _write(world.local / "other.txt", "other\n")
            _git(world.local, "add", "other.txt")
            _git(world.local, "commit", "-q", "-m", "somebody else (#12)")
            _git(world.local, "push", "-q", "origin", FEATURE)
            _git(world.local, "checkout", "-q", INTEGRATION)
            m._run = real_run                     # only race the first push
        return real_run(cwd, argv)

    m._run = racing_run
    try:
        report = _upkeep(m, world)
    finally:
        m._run = real_run
    assert report["outcome"] == m.LEASE_REFUSED, report
    assert any("--force-with-lease=" in " ".join(p) for p in pushes), pushes
    assert "somebody else (#12)" in _git(world.local, "log", "--format=%s", "-1",
                                         world.tip(FEATURE)), (
        "the stale lease OVERWROTE the other writer's commit")
    assert world.dirt()["rebase_dirs"] == []
    assert world.dirt()["worktrees"] == [str(world.local)]


def test_every_push_carries_force_with_lease(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.goal_branch(11)
    world.write_registry(goals=[11])
    world.move_integration()
    seen = []
    real_run = m._run

    def watch(cwd, argv):
        if "push" in argv:
            seen.append(list(argv))
        return real_run(cwd, argv)

    m._run = watch
    try:
        _upkeep(m, world)
    finally:
        m._run = real_run
    assert seen, "no push was observed at all"
    for argv in seen:
        assert any(str(a).startswith("--force-with-lease") for a in argv), argv


# --------------------------------------------------------------------------- the rule's guard


def test_a_feature_carrying_a_direct_commit_is_detected_rather_than_rebased_over(tmp_path):
    """The rule -- 'nobody commits directly to a feature branch' -- is what makes the force-push
    safe. A branch that breaks it must be SURFACED, never quietly rewritten."""
    m = _mod()
    world = World(tmp_path).build(feature_commits=(
        ("f.txt", "f", "feat: land a goal (#11)"),
        ("h.txt", "h", "quick fix, straight onto the branch")))
    world.move_integration()
    filed = _filer(m)
    before = world.dirt()
    report = _upkeep(m, world)
    assert report["outcome"] == m.DIRECT_COMMITS, report
    assert world.dirt() == before, "a branch breaking the rule must not be rebased over"
    assert [d["subject"] for d in report["direct"]] == ["quick fix, straight onto the branch"]
    assert len(filed) == 1
    assert FEATURE in filed[0]["title"] + filed[0]["body"]


def test_a_squash_landing_and_a_merge_landing_both_read_as_arriving_through_a_pull_request(tmp_path):
    m = _mod()
    assert m.arrived_through_a_pull_request("feat(x): do the thing (#1526)") is True
    assert m.arrived_through_a_pull_request("Merge pull request #101 from org/sdlc/11") is True
    assert m.arrived_through_a_pull_request("quick fix") is False
    assert m.arrived_through_a_pull_request("sdlc: 11") is False


def test_a_reference_in_the_middle_of_a_subject_is_not_evidence_of_a_pull_request(tmp_path):
    r"""The squash form is `\Z`-anchored on purpose. `(#12)` in the MIDDLE of a subject is somebody
    citing an ISSUE, which is exactly the kind of thing a hand-typed commit says -- accepting it
    would let the most likely direct commit of all pass the check that exists to catch it. Asserted
    here rather than assumed: the trailing case alone is blind to this one."""
    m = _mod()
    assert m.arrived_through_a_pull_request("revert the change from (#12) last week") is False
    assert m.arrived_through_a_pull_request("(#12) tidy up") is False
    assert m.arrived_through_a_pull_request("Merge branch 'main' into feature/billing") is False
    assert m.arrived_through_a_pull_request(None) is False


def test_only_first_parent_commits_count_so_a_merged_goals_own_commits_are_not_direct(tmp_path):
    """A `merge_method: merge` landing brings the goal branch's own `sdlc: <n>` commits along as
    SECOND parents. They arrived through a goal branch by construction; walking every commit rather
    than the first-parent line would report each of them as a direct commit."""
    m = _mod()
    world = World(tmp_path).build()
    _git(world.local, "checkout", "-q", FEATURE)
    _git(world.local, "checkout", "-q", "-b", "sdlc/11")
    _write(world.local / "g.txt", "g\n")
    _git(world.local, "add", "g.txt")
    _git(world.local, "commit", "-q", "-m", "sdlc: 11")
    _git(world.local, "checkout", "-q", FEATURE)
    _git(world.local, "merge", "-q", "--no-ff", "-m",
         "Merge pull request #101 from org/sdlc/11", "sdlc/11")
    _git(world.local, "push", "-q", "origin", FEATURE)
    _git(world.local, "checkout", "-q", INTEGRATION)
    world.move_integration()
    report = _upkeep(m, world, config=_cfg(merge_method="merge"))
    assert report["direct"] == [], report
    assert report["outcome"] == m.REBASED


def test_a_rebase_merge_repo_cannot_be_checked_so_the_branch_is_not_touched(tmp_path):
    """`merge_method: rebase` preserves the original commit messages, so nothing on the feature
    branch carries a PR reference and the classifier has NO evidence. Ignorance is not a licence to
    force-push: refuse, loudly."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    before = world.dirt()
    report = _upkeep(m, world, config=_cfg(merge_method="rebase"))
    assert report["outcome"] == m.UNVERIFIABLE, report
    assert world.dirt() == before


def test_an_integration_branch_that_is_this_very_branch_is_a_no_op(tmp_path):
    """What a repo running its own epic looks like: `work.base` pointed at the epic's feature
    branch, so `base` and the unit's branch are one string. A branch cannot be brought forward onto
    itself, and trying would fetch, replay nothing and force-push the same tip."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    before = world.dirt()
    report = _upkeep(m, world, config=_cfg(base=FEATURE))
    assert report["outcome"] == m.NO_BASE, report
    assert world.dirt() == before


def test_a_worktree_left_by_a_crashed_pass_does_not_wedge_the_next_one(tmp_path):
    """`git worktree add` refuses a path that already exists, so a pass killed between `add` and its
    own `finally` would otherwise disable upkeep for that unit permanently."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    stale = m.worktree_path(str(world.sdlc), UNIT)
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "leftover.txt").write_text("junk\n", encoding="utf-8")
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED, report
    assert not stale.exists()


def test_a_goal_worktree_that_cannot_be_read_counts_as_dirty(tmp_path):
    """The question exists to protect somebody's in-flight edits. A question that could not be
    answered is not permission to move their files."""
    m = _mod()
    assert m._dirty(_boom, tmp_path) is True
    assert m._dirty(lambda cwd, argv: "", tmp_path) is False
    assert m._dirty(lambda cwd, argv: " M a.txt", tmp_path) is True


# --------------------------------------------------------------------------- concurrency + safety


def test_a_second_pass_on_the_same_unit_does_not_rebase_concurrently(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    held = m._acquire(m.lock_path(str(world.sdlc), UNIT))
    assert held is not None
    before = world.dirt()
    try:
        report = _upkeep(m, world)
    finally:
        m._release(held)
    assert report["outcome"] == m.BUSY, report
    assert world.dirt() == before


def test_upkeep_never_raises(tmp_path):
    """The outer guard, exercised on a call that is NOT individually wrapped. `live_branches` has
    its own `except`, so a runner that raises everywhere would prove that function's guard and not
    this one -- it has to get past the branch listing and fail somewhere the pass trusts."""
    m = _mod()
    world = World(tmp_path).build()
    report = m.upkeep(str(world.sdlc), _cfg(), "7", UNIT, run=_boom_after_ls_remote)
    assert report["outcome"] == m.FAILED, report
    assert "git fetch exploded" in report["why"]
    assert world.dirt()["worktrees"] == [str(world.local)]


def _boom_after_ls_remote(cwd, argv):
    if "ls-remote" in argv:
        return "%s\trefs/heads/%s" % ("0" * 40, FEATURE)
    raise RuntimeError("git fetch exploded")


def test_a_remote_that_cannot_be_read_judges_nothing(tmp_path):
    """`None` is 'the remote could not be read', never 'there are no branches'. Reading the first as
    the second is how a laptop that happened to be offline reports every unit as branchless."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    before = world.dirt()
    report = m.upkeep(str(world.sdlc), _cfg(), "7", UNIT, run=_boom)
    assert report["outcome"] == m.REMOTE_UNREADABLE, report
    assert report["why"]
    assert world.dirt() == before


def _boom(cwd, argv):
    raise RuntimeError("git is not available")


def test_the_switch_is_off_when_asked(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    before = world.dirt()
    report = _upkeep(m, world, config=_cfg(rebase_upkeep="off"))
    assert report["outcome"] == m.DISABLED
    assert world.dirt() == before


def test_a_dirty_goal_worktree_is_skipped_rather_than_autostashed(tmp_path):
    """`work.rebase()` uses `--autostash`, which would move files under a live agent's feet. The
    rule that makes a rebase safe is about the FEATURE branch; it says nothing about uncommitted
    work in a goal worktree, and an in-flight agent demonstrably holds that."""
    m = _mod()
    world = World(tmp_path).build()
    path = world.goal_branch(11)
    world.write_registry(goals=[11])
    # A DEAD agent marker, so the liveness gate says IDLE and the DIRTY check is what has to do the
    # skipping. Without it this test would pass on the liveness gate alone and prove nothing about
    # the guard it is named for.
    _register_agent(world.sdlc, 11, 999999)
    world.move_integration()
    _write(path / "in-flight.txt", "half-written\n")
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED
    assert report["replayed"] == []
    assert [s["branch"] for s in report["skipped"]] == ["sdlc/11"]
    assert [s["why"] for s in report["skipped"]] == ["uncommitted work in progress"]
    assert (path / "in-flight.txt").read_text() == "half-written\n"


def test_every_report_carries_the_clause_start_prints(tmp_path):
    """The clause is the ONLY consumer of any of this on a normal run -- `work.start()` appends it
    to its own one-line result. A report whose `note` was never filled in is a finding nobody sees,
    so it is built on the single exit rather than at each return."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    assert m.upkeep(str(world.sdlc), _cfg(), "7", None)["note"] == ""      # not in IN_CLAUSE
    rebased = _upkeep(m, world)
    assert rebased["outcome"] == m.REBASED
    assert rebased["note"].startswith(" — upkeep: ")
    assert FEATURE in rebased["note"] and INTEGRATION in rebased["note"]
    assert m.upkeep(str(world.sdlc), _cfg(), "7", UNIT, run=_boom)["note"] == ""   # FAILED is quiet


def test_the_clause_wording_is_total_over_the_outcomes_it_claims_to_cover(tmp_path):
    """A partial table would make widening `IN_CLAUSE` raise `KeyError` instead of doing the wrong
    thing visibly -- a crash a test asserting "nothing was said" cannot tell from correct
    behaviour."""
    m = _mod()
    assert set(m.IN_CLAUSE) <= set(m._WORDING)
    assert set(m.IN_CLAUSE) <= set(m.OUTCOMES)
    for outcome in m.IN_CLAUSE:
        report = m._report("7", UNIT, _cfg())
        report.update({"outcome": outcome, "branch": FEATURE, "base": INTEGRATION})
        assert m.clause(report).startswith(" — upkeep: ")


# --------------------------------------------------------------------------- the wire


def test_start_runs_upkeep_before_it_cuts_the_worktree(tmp_path):
    """Ordering is load-bearing: the goal is cut from `<remote>/feature/<unit>`, so the feature has
    to be brought forward BEFORE the cut or the new goal starts from the stale tip."""
    work = _load("work")
    order = []

    def run(cwd, argv):
        order.append(" ".join(str(a) for a in argv))
        if "rev-parse --abbrev-ref HEAD" in " ".join(str(a) for a in argv):
            return INTEGRATION
        return ""

    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    calls = []
    work._FEATURE_REBASE = types.SimpleNamespace(
        upkeep=lambda *a, **k: calls.append(("upkeep", len(order))) or
        {"note": " — upkeep: rebased"})
    work._FEATURE_SYNC = types.SimpleNamespace(sync_at_pick=lambda *a, **k: {"note": ""})
    out = work.start(str(sdlc), {"work": {"enabled": True, "base": INTEGRATION}}, "7", run=run)
    assert calls, "upkeep was never called from start()"
    fetch_at = next(i for i, c in enumerate(order) if c.startswith("git fetch"))
    assert calls[0][1] <= fetch_at, (order, calls)
    assert "upkeep: rebased" in out


# --------------------------------------------------------------------------- the refusals


def test_a_unit_name_that_is_not_one_is_refused_before_it_becomes_a_path(tmp_path):
    """Both derived paths refuse what `feature_registry.unit_path` refuses, through the same
    predicate. The worktree one matters more than the lock one: it becomes a REAL checkout, so a
    name that escaped would put a working tree outside `.sdlc/` entirely."""
    m = _mod()
    # `m.registry`, NOT a fresh `_load("feature_registry")`: `_load` never registers in `sys.modules`,
    # so a second load builds a DIFFERENT class object and `pytest.raises` would miss the real one.
    for bad in ("../../etc", "a/b", "..", ".hidden", "x.lock", "", None, 7):
        for fn in (m.lock_path, m.worktree_path):
            with pytest.raises(m.registry.InvalidUnitName):
                fn(str(tmp_path), bad)
    assert m.worktree_path(str(tmp_path), UNIT) == tmp_path / "state" / "rebase" / UNIT
    assert m.lock_path(str(tmp_path), UNIT) == (
        tmp_path / "state" / "features" / m.LOCK_DIRNAME / (UNIT + m.LOCK_SUFFIX))


# --------------------------------------------------------------- #1577: one key, one namespace


def _every_legal_shape():
    """Unit names chosen so that every suffix this module and `feature_sync` append to one is ALSO
    reachable as part of another legal name -- which is the only way a suffix-separated namespace
    can collide. `rebase` and `rebase-filed` are ordinary things to call a unit."""
    return ("alpha", "alpha.rebase", "Alpha.Rebase", "alpha.rebase.rebase", "rebase",
            "alpha.rebase-filed.json", "rebase-filed.json", "alpha.lock2")


def test_a_rebase_lock_can_never_also_be_some_other_unit_s_sync_lock(tmp_path):
    """#1577. Both locks lived directly in `state/features/`, told apart by a SUFFIX alone -- and
    `.rebase` is a legal thing to end a unit name with, so unit `alpha.rebase`'s sync lock and unit
    `alpha`'s rebase lock were the same file, byte for byte. Two unrelated units serialised against
    each other; worse, the rebase lock FAILS CLOSED, so a sync write on one unit reported the other
    as `BUSY` -- "another pass is already rebasing" about a pass that does not exist.

    Asserted as the PROPERTY over every shape, not on the one pair that was reported: the pair is an
    instance, and a fix that special-cased it would leave `a.rebase.rebase` colliding just the same.

    The fix is a DIRECTORY, not a cleverer suffix. A sentinel character outside `features._UNIT_RE`
    would also separate them today, but only for as long as that character class stays narrow -- and
    it is documented as a deliberate narrowing of what git accepts, so widening it later is a real
    edit somebody will make. A directory does not depend on the charset at all. It is also the
    precedent already set one module along: `feature_registry` gave the shards `units/` for exactly
    this reason, because `index` is a legal unit name."""
    m, s = _mod(), _load("feature_sync")
    mine = {m.lock_path(tmp_path, n) for n in _every_legal_shape()}
    theirs = {s.lock_path(tmp_path, n) for n in _every_legal_shape()}
    assert not (mine & theirs), sorted(str(p) for p in mine & theirs)


def test_the_filed_store_can_never_be_a_lock_of_either_kind(tmp_path):
    """The third key `state/features/` holds per unit. `.rebase-filed.json` and `.lock` cannot end
    the same string, so this one was already safe -- and it is asserted rather than argued because
    the argument is about suffixes, and the suffixes are constants somebody can edit."""
    m, s = _mod(), _load("feature_sync")
    filed = {m.filed_path(tmp_path, n) for n in _every_legal_shape()}
    locks = ({m.lock_path(tmp_path, n) for n in _every_legal_shape()}
             | {s.lock_path(tmp_path, n) for n in _every_legal_shape()})
    assert not (filed & locks), sorted(str(p) for p in filed & locks)


def test_two_casings_of_one_unit_take_one_rebase_lock_one_filed_store_and_one_worktree(tmp_path):
    """#1566's rule, applied to all three of this module's keys -- #1577 reached the first two and
    #1673 the third. Same string in, same address out.

    It matters more here than it did in `feature_sync`. `feature_sync`'s lock fails OPEN, so
    losing it costs serialisation on a write; THIS lock fails closed and guards a force-push of a
    shared branch, so two casings holding two different files means two concurrent rebases of one
    feature branch -- the exact thing the lock is the only defence against. `worktree_path` is a
    different cost of the same split: two throwaway CHECKOUTS for one unit, so the stale-worktree
    clean-up on entry looks at one spelling and the leftover sits under the other.

    Asserted on the derived path because the development host's filesystem is case-insensitive and
    would hide the divergence, which is the reason #1566 gives for asserting it the same way."""
    m = _mod()
    for spelling in ("Voice", "VOICE", "vOiCe"):
        assert m.lock_path(tmp_path, spelling) == m.lock_path(tmp_path, "voice")
        assert m.filed_path(tmp_path, spelling) == m.filed_path(tmp_path, "voice")
        assert m.worktree_path(str(tmp_path), spelling) == m.worktree_path(str(tmp_path), "voice")


def test_a_unit_name_ending_in_uppercase_LOCK_still_becomes_a_worktree_path(tmp_path):
    """The ordering half of #1673, asserted at THIS site rather than borrowed from the registry's.

    `is_unit_name` rejects `voice.lock` (git rejects that ref) and ACCEPTS `voice.LOCK` (git accepts
    it), so folding BEFORE the guard would turn an accepted name into the spelling of a rejected one
    and this call would raise. `feature_registry`'s own ordering test pins `unit_path` only; a guard
    and a fold rearranged HERE would leave it green. The cost is sharper for this key than for any
    other in the family: it becomes a REAL checkout, so the name that addresses it is a directory
    git is asked to register."""
    m = _mod()
    assert m.worktree_path(str(tmp_path), "voice.LOCK") == (
        tmp_path / "state" / m.WORKTREE_DIRNAME / "voice.lock")
    with pytest.raises(m.registry.InvalidUnitName):
        m.worktree_path(str(tmp_path), "voice.lock")


def test_the_switch_reads_a_boolean_false_as_off_and_a_typo_as_on(tmp_path):
    """A typo must cost a surprise, not a silence: an unrecognised value leaves upkeep ON, which is
    the opposite of `work.review_mode`'s rule and deliberately so — see `switch`."""
    m = _mod()
    assert m.switch(_cfg(rebase_upkeep=False)) == m.OFF
    assert m.switch(_cfg(rebase_upkeep="OFF")) == m.OFF
    assert m.switch(_cfg(rebase_upkeep="  off  ")) == m.OFF
    assert m.switch(_cfg(rebase_upkeep="onn")) == m.ON
    assert m.switch(_cfg()) == m.ON
    assert m.switch({}) == m.ON


def test_a_feature_tip_that_reads_back_empty_is_never_force_pushed_over(tmp_path):
    """Fail closed. A tip we could not read is not a tip we may name in a lease, and a lease built
    on an empty string is not a lease at all."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    real = m._run

    def blank_rev_parse(cwd, argv):
        if argv[:2] == ["git", "rev-parse"]:
            return ""
        return real(cwd, argv)

    before = world.dirt()
    report = m.upkeep(str(world.sdlc), _cfg(), "7", UNIT, run=blank_rev_parse)
    assert report["outcome"] == m.FAILED, report
    assert "read back empty" in report["why"]
    assert world.dirt() == before


def test_a_repo_slug_that_cannot_be_resolved_says_the_goal_branches_were_not_listed(tmp_path):
    """`repos.<slug>.goals` is the only place a goal number lives. The feature branch moved and its
    goal branches did not, which is a half-done pass and must not read as a complete one."""
    m = _mod()
    world = World(tmp_path).build()
    world.goal_branch(11)
    world.write_registry(goals=[11])
    world.move_integration()
    config = _cfg()
    config["discovery"]["github"]["repo"] = ""          # and the fallback cannot answer either
    m.sync.repo_slug = lambda *a, **k: None
    report = m.upkeep(str(world.sdlc), config, "7", UNIT)
    assert report["outcome"] == m.REBASED
    assert report["replayed"] == []
    assert [s["why"] for s in report["skipped"]] == [
        "no owner/name resolved, so the unit's goal branches could not be listed"]


def test_without_flock_the_pass_runs_and_says_it_could_not_serialise(tmp_path):
    """Refusing outright on a platform with no `fcntl` would disable the feature rather than protect
    anything. The damage stays bounded by two things that need no lock: the lease still refuses a
    second writer's push, and an OCCUPIED ephemeral path stops the pass rather than being deleted
    (the test above). The claim this docstring used to make -- that `git worktree add` refuses an
    existing path -- was measured false, because the pre-add drop deleted it first."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    m.sync.fcntl = None
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED, report
    assert report["serialised"] is False


def test_a_filer_that_blows_up_never_costs_the_pass(tmp_path):
    m = _mod()
    world = World(tmp_path).build(
        feature_commits=(("seed.txt", "feature edit", "feat: touch the seed (#11)"),))
    world.move_integration(name="seed.txt", body="integration edit")

    def explode(*a, **k):
        raise RuntimeError("gh is not installed")

    m._HANDOFF = types.SimpleNamespace(create_tracked_issue=explode)
    report = _upkeep(m, world)
    assert report["outcome"] == m.CONFLICT
    assert report["issues"] == []


def test_a_worktree_that_cannot_be_created_reports_and_moves_nothing(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    blocked = m.worktree_path(str(world.sdlc), UNIT)
    blocked.parent.mkdir(parents=True, exist_ok=True)
    real_drop = m._drop_worktree
    m._drop_worktree = lambda run, cwd, path: None      # so the block is not cleared away first
    blocked.write_text("not a directory\n", encoding="utf-8")
    before = world.tip(FEATURE)
    try:
        report = _upkeep(m, world)
    finally:
        m._drop_worktree = real_drop
    assert report["outcome"] == m.FAILED, report
    assert report["why"]
    assert world.tip(FEATURE) == before


def test_the_cli_reports_without_moving_anything_and_can_run_the_pass(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    (world.sdlc / "config.json").write_text(json.dumps(_cfg()), encoding="utf-8")
    assert m.main(["feature_rebase.py"]) == 2
    assert m.main(["feature_rebase.py", "show", str(world.sdlc), UNIT]) == 0
    before = world.tip(FEATURE)
    assert m.main(["feature_rebase.py", "show", str(world.sdlc), UNIT]) == 0
    assert world.tip(FEATURE) == before, "`show` must not move anything"
    assert m.main(["feature_rebase.py", "upkeep", str(world.sdlc), UNIT, "7"]) == 0
    assert world.tip(FEATURE) != before


# ===========================================================================================
# P6 REVIEW round 1 — the four blocking findings, each asserted on what was MEASURED rather
# than on an outcome string. The review's own repro is the bar: for F1 the assertion is the
# OTHER goal's remote tip and worktree HEAD, for F2 it is the worktree still being registered
# after a real kill, because in both cases the outcome read `rebased` and looked fine.
# ===========================================================================================


import contextlib
import signal
import subprocess as _sp


@contextlib.contextmanager
def _a_live_foreign_process():
    """A REAL running process that is genuinely not us -- the only honest way to exercise the
    liveness gate, since `loop.agent_alive` resolves liveness through `ledger.pid_alive`. Lifted
    from `tests/test_work.py`, which is where this repo's guard is already tested."""
    proc = _sp.Popen(["sleep", "30"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


def _register_agent(sdlc_dir, goal, pid):
    loop = _load("loop")
    loop.agent_start(str(sdlc_dir), str(goal), pid, {})
    return loop


def _wt_head(path):
    return _git(path, "rev-parse", "HEAD")


# --------------------------------------------------------------------------- F1: live siblings


def test_a_goal_a_live_agent_is_working_is_never_replayed(tmp_path):
    """F1. A work record exists exactly while a goal is UNFINISHED, so the set `_replay_goals`
    iterates is the unit's live fleet. A clean tree says nothing about whether an agent is mid-run
    in it -- an agent that has just committed and is about to open its PR has a clean tree.

    Asserted on the other goal's REMOTE TIP and its WORKTREE HEAD, not on an outcome string: the
    review reproduced this with `report: replayed=['sdlc/11']` and the pass reporting `rebased`."""
    m = _mod()
    world = World(tmp_path).build()
    path = world.goal_branch(11)
    world.write_registry(goals=[11])
    world.move_integration()
    tip_before, head_before = world.tip("sdlc/11"), _wt_head(path)
    with _a_live_foreign_process() as pid:
        _register_agent(world.sdlc, 11, pid)
        report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED, report
    assert report["replayed"] == [], report
    assert world.tip("sdlc/11") == tip_before, "a live agent's remote tip was rewritten"
    assert _wt_head(path) == head_before, "a live agent's worktree HEAD moved under it"
    assert [s["branch"] for s in report["skipped"]] == ["sdlc/11"]
    assert "live" in report["skipped"][0]["why"]


def test_a_goal_with_no_liveness_evidence_is_left_to_its_own_owner(tmp_path):
    """Positive evidence of idleness, or nothing. With no agent marker and no claim there is no
    evidence either way, and a destructive replay is not something to do on no evidence -- the
    goal's own `merge()` path rebases it reactively when GitHub reports BEHIND."""
    m = _mod()
    world = World(tmp_path).build()
    path = world.goal_branch(11)
    world.write_registry(goals=[11])
    world.move_integration()
    tip_before, head_before = world.tip("sdlc/11"), _wt_head(path)
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED
    assert report["replayed"] == []
    assert world.tip("sdlc/11") == tip_before and _wt_head(path) == head_before
    assert [s["why"] for s in report["skipped"]] == [m.WHY_NO_EVIDENCE]


def test_a_goal_whose_agent_is_registered_and_dead_is_replayed(tmp_path):
    """The other half: a crashed agent's goal is exactly the one whose branch wants bringing
    forward, and a marker naming a pid that cannot be running is the positive evidence."""
    m = _mod()
    world = World(tmp_path).build()
    world.goal_branch(11)
    world.write_registry(goals=[11])
    world.move_integration()
    _register_agent(world.sdlc, 11, 999999)          # a pid that cannot be running
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED, report
    assert report["replayed"] == ["sdlc/11"], report
    feature_tip, goal_tip = world.tip(FEATURE), world.tip("sdlc/11")
    assert _git(world.local, "rev-list", "--count", "%s..%s" % (goal_tip, feature_tip)) == "0"


def test_the_liveness_verdict_is_the_repos_own_gate(tmp_path):
    """`goal_liveness` is not a second opinion: LIVE comes from the same `agent_alive` /
    `claim_lock_alive` pair `work._blocked_by_a_live_foreign_agent` reads."""
    m = _mod()
    world = World(tmp_path).build()
    assert m.goal_liveness(str(world.sdlc), _cfg(), "11") == m.UNKNOWN
    _register_agent(world.sdlc, 11, 999999)
    assert m.goal_liveness(str(world.sdlc), _cfg(), "11") == m.IDLE
    with _a_live_foreign_process() as pid:
        _register_agent(world.sdlc, 11, pid)
        assert m.goal_liveness(str(world.sdlc), _cfg(), "11") == m.LIVE


# --------------------------------------------------------------------------- F2: the strand


_KILL_CHILD = r'''
import importlib.util, os, pathlib, sys
sys.argv = sys.argv[:]
SRC, SDLC, UNIT = sys.argv[1], sys.argv[2], sys.argv[3]
spec = importlib.util.spec_from_file_location("fr_kill", SRC)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sys.path.insert(0, sys.argv[4])
import test_feature_rebase as T
real = m._run
def killing(cwd, argv):
    if argv[:2] == ["git", "rebase"]:
        try:
            return real(cwd, argv)
        finally:
            os._exit(9)          # SIGKILL-equivalent: no `finally`, no atexit, no cleanup
    return real(cwd, argv)
m._run = killing
m.upkeep(SDLC, T._cfg(), "7", UNIT)
'''


def test_a_pass_killed_mid_rebase_does_not_strand_its_worktree_for_the_next_one(tmp_path):
    """F2. `os._exit(9)` mid-rebase runs no `finally`, so the ephemeral worktree stays REGISTERED
    with live rebase state. Every early return in the next pass -- `current` above all, the
    overwhelmingly common one -- used to return before the recovery drop, so the strand was
    permanent in the adopter's repo.

    Asserted on `git worktree list` and on the rebase directory, not on an outcome string: the
    second pass's outcome is `current` in both the broken and the fixed world."""
    m = _mod()
    world = World(tmp_path).build(
        feature_commits=(("seed.txt", "feature edit", "feat: touch the seed (#11)"),))
    world.move_integration(name="seed.txt", body="integration edit")
    child = _sp.run([sys.executable, "-c", _KILL_CHILD, str(P), str(world.sdlc), UNIT,
                     str(ROOT / "tests")], capture_output=True, text=True, cwd=str(ROOT))
    assert child.returncode == 9, (child.returncode, child.stderr[-800:])
    stranded = m.worktree_path(str(world.sdlc), UNIT)
    assert stranded.exists(), "the kill did not strand anything; the test proves nothing"
    assert str(stranded.resolve()) in world.dirt()["worktrees"]
    assert world.dirt()["rebase_dirs"], "no half-applied rebase was left; the test proves nothing"

    # somebody brings the branch forward out of band, so the next pass short-circuits on `current`
    _git(world.local, "push", "-q", "-f", "origin", "origin/%s:refs/heads/%s" % (INTEGRATION, FEATURE))
    report = _upkeep(m, world)
    assert report["outcome"] == m.CURRENT, report
    assert not stranded.exists(), "the strand survived a pass that returned `current`"
    assert str(stranded.resolve()) not in world.dirt()["worktrees"]
    assert world.dirt()["rebase_dirs"] == []


def test_without_a_lock_an_occupied_path_stops_the_pass_instead_of_being_deleted(tmp_path):
    """The dead defence, made real. The module used to claim `git worktree add` refuses an existing
    path -- it never got the chance, because the pre-add drop deleted it first, and the review
    measured a second unserialised pass destroying a first pass's LIVE worktree.

    With no `fcntl` there is no way to tell a stale worktree from a live one, so the honest answer
    is to refuse rather than to delete."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    occupied = m.worktree_path(str(world.sdlc), UNIT)
    occupied.parent.mkdir(parents=True, exist_ok=True)
    _git(world.local, "worktree", "add", "-q", "--detach", str(occupied), "origin/" + FEATURE)
    marker = occupied / "another-pass-is-here.txt"
    marker.write_text("live\n", encoding="utf-8")
    tip_before = world.tip(FEATURE)
    m.sync.fcntl = None
    report = _upkeep(m, world)
    assert report["outcome"] == m.OCCUPIED, report
    assert marker.read_text() == "live\n", "another pass's live worktree was destroyed"
    assert str(occupied.resolve()) in world.dirt()["worktrees"]
    assert world.tip(FEATURE) == tip_before
    assert "occupied" in report["note"] or str(occupied) in report["note"]


# --------------------------------------------------------------------------- F3: measured, not asserted


def test_the_filed_issue_measures_cleanliness_rather_than_asserting_it(tmp_path):
    """F3(b). The body used to say "no stranded worktree" unconditionally. The review made cleanup
    fail and got an issue asserting the opposite of what was on disk.

    Cleanup is disabled here by substitution rather than by `chmod`: a mode-bit repro is silently a
    no-op for root, which would make this test pass without testing anything on half the machines
    it runs on."""
    m = _mod()
    world = World(tmp_path).build(
        feature_commits=(("seed.txt", "feature edit", "feat: touch the seed (#11)"),))
    world.move_integration(name="seed.txt", body="integration edit")
    filed = _filer(m)
    m._drop_worktree = lambda run, cwd, path: None       # cleanup that cannot clean
    report = _upkeep(m, world)
    assert report["outcome"] == m.CONFLICT, report
    body = filed[0]["body"]
    assert "no stranded worktree" not in body, body
    assert str(m.worktree_path(str(world.sdlc), UNIT)) in body
    assert "git worktree remove --force" in body
    assert report["leftovers"], report


def test_the_filed_issue_says_clean_only_when_it_measured_clean(tmp_path):
    m = _mod()
    world = World(tmp_path).build(
        feature_commits=(("seed.txt", "feature edit", "feat: touch the seed (#11)"),))
    world.move_integration(name="seed.txt", body="integration edit")
    filed = _filer(m)
    before = world.dirt()
    report = _upkeep(m, world)
    assert report["outcome"] == m.CONFLICT
    assert report["leftovers"] == []
    body = filed[0]["body"]
    assert "no worktree was left behind" in body
    assert world.tip(FEATURE) in body, "the body must quote the tip it MEASURED, not the one it read"
    assert world.dirt() == before


def test_a_rebase_command_that_failed_is_not_reported_as_a_conflict(tmp_path):
    """F3(c). Every exception out of `git rebase` used to be titled "Rebase conflict"; the review
    got that title for `[Errno 2] No such file or directory`. Whether a rebase STOPPED is a
    question git answers structurally -- `rev-parse --git-path rebase-merge` -- not one to guess
    from an exception string."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    filed = _filer(m)
    real = m._run

    def broken_rebase(cwd, argv):
        if argv[:2] == ["git", "rebase"]:
            raise RuntimeError("fatal: invalid upstream 'origin/main'")
        return real(cwd, argv)

    tip_before = world.tip(FEATURE)
    report = m.upkeep(str(world.sdlc), _cfg(), "7", UNIT, run=broken_rebase)
    assert report["outcome"] == m.FAILED, report
    assert filed == [], "a command failure is not work to hand a human as a merge conflict"
    assert world.tip(FEATURE) == tip_before
    assert "invalid upstream" in report["why"]


def test_a_real_conflict_is_still_reported_as_a_conflict(tmp_path):
    m = _mod()
    world = World(tmp_path).build(
        feature_commits=(("seed.txt", "feature edit", "feat: touch the seed (#11)"),))
    world.move_integration(name="seed.txt", body="integration edit")
    filed = _filer(m)
    report = _upkeep(m, world)
    assert report["outcome"] == m.CONFLICT
    assert filed[0]["title"] == "Rebase conflict: %s onto %s" % (FEATURE, INTEGRATION)


# --------------------------------------------------------------------------- F4: silence and truncation


def test_a_lease_refusal_is_decided_by_the_remote_tip_not_by_a_truncated_string(tmp_path):
    """F4. `"stale info" in why` was decided inside a 200-character truncation, with 27 characters
    of headroom measured against a 131-character remote URL -- so a longer remote URL demoted a
    lease refusal into the silent bucket. The question "did the branch move under us?" has a
    measurement; use it.

    Both directions, because either alone is passable by an accident: a refusal whose text never
    says `stale info`, and a failure whose text does while the tip never moved."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    real = m._run

    def racing_push(cwd, argv):
        if argv[:2] == ["git", "push"]:
            _git(world.local, "checkout", "-q", FEATURE)
            _write(world.local / "other.txt", "other\n")
            _git(world.local, "add", "other.txt")
            _git(world.local, "commit", "-q", "-m", "somebody else (#12)")
            _git(world.local, "push", "-q", "-f", "origin", FEATURE)
            _git(world.local, "checkout", "-q", INTEGRATION)
            raise RuntimeError("error: failed to push some refs to 'a-very-long-remote-url'")
        return real(cwd, argv)

    report = m.upkeep(str(world.sdlc), _cfg(), "7", UNIT, run=racing_push)
    assert report["outcome"] == m.LEASE_REFUSED, report
    assert "somebody else (#12)" in _git(world.local, "log", "--format=%s", "-1", world.tip(FEATURE))


def test_a_push_refused_for_any_other_reason_is_never_called_a_lease_refusal(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    real = m._run

    def protected_push(cwd, argv):
        if argv[:2] == ["git", "push"]:
            raise RuntimeError("remote: error: GH006: Protected branch update failed "
                               "(stale info appears here only as prose)")
        return real(cwd, argv)

    tip_before = world.tip(FEATURE)
    report = m.upkeep(str(world.sdlc), _cfg(), "7", UNIT, run=protected_push)
    assert report["outcome"] == m.FAILED, report
    assert world.tip(FEATURE) == tip_before
    assert report["note"], "a refused force-push on a shared branch must never be silent"
    assert "GH006" in report["note"] or "GH006" in report["why"]


def test_failed_is_never_silent(tmp_path):
    """F4. `_WORDING`'s own comment claimed `FAILED` was "already on stderr"; only the RAISED path
    ever wrote anything. Every `FAILED` returned normally wrote nothing anywhere, so "upkeep
    force-pushed a shared branch and GitHub refused" was indistinguishable from "upkeep did
    nothing"."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    real = m._run

    def broken(cwd, argv):
        if argv[:3] == ["git", "rev-parse", "%s/%s" % ("origin", FEATURE)]:
            return ""
        return real(cwd, argv)

    report = m.upkeep(str(world.sdlc), _cfg(), "7", UNIT, run=broken)
    assert report["outcome"] == m.FAILED
    assert report["note"].startswith(" — upkeep: ")
    assert "read back empty" in report["note"]


def test_an_unserialised_pass_says_so_where_somebody_reads_it(tmp_path):
    """`report["serialised"]` had no consumer at all on the pick path -- precisely the sin
    `upkeep`'s own docstring names."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    m.sync.fcntl = None
    report = _upkeep(m, world)
    assert report["outcome"] == m.REBASED
    assert report["serialised"] is False
    assert "unserialised" in report["note"], report["note"]


# --------------------------------------------------------------------------- nits with teeth


def test_a_persistent_finding_is_filed_once_not_on_every_pick(tmp_path):
    """`handoff`'s dedup stops a second ISSUE, but it then posts a duplicate-context comment and
    writes a ledger entry on every pick. A finding that has not changed is not news."""
    m = _mod()
    world = World(tmp_path).build(feature_commits=(
        ("f.txt", "f", "feat: land a goal (#11)"),
        ("h.txt", "h", "quick fix, straight onto the branch")))
    world.move_integration()
    filed = _filer(m)
    first = _upkeep(m, world)
    second = _upkeep(m, world)
    assert first["outcome"] == second["outcome"] == m.DIRECT_COMMITS
    assert len(filed) == 1, "the same unchanged finding was filed twice"
    assert first["filing"] == m.FILED and second["filing"] == m.ALREADY_FILED
    assert second["note"], "not re-filing must not make the finding silent"
    assert "already filed" in second["note"], second["note"]


def test_a_finding_that_changed_is_filed_again(tmp_path):
    m = _mod()
    world = World(tmp_path).build(feature_commits=(
        ("f.txt", "f", "feat: land a goal (#11)"),
        ("h.txt", "h", "quick fix, straight onto the branch")))
    world.move_integration()
    filed = _filer(m)
    _upkeep(m, world)
    _git(world.local, "checkout", "-q", FEATURE)
    _write(world.local / "h2.txt", "h2\n")
    _git(world.local, "add", "h2.txt")
    _git(world.local, "commit", "-q", "-m", "and another one by hand")
    _git(world.local, "push", "-q", "origin", FEATURE)
    _git(world.local, "checkout", "-q", INTEGRATION)
    _upkeep(m, world)
    assert len(filed) == 2, "a branch that grew a second direct commit is a new finding"


def test_a_lock_directory_that_cannot_be_made_is_not_reported_as_contention(tmp_path):
    """`_acquire` returns None for contention AND for an unwritable state directory, and mapping
    both to BUSY told the operator "another pass is already rebasing" forever, which is false."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    blocker = m.lock_path(str(world.sdlc), UNIT).parent
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a directory\n", encoding="utf-8")
    report = _upkeep(m, world)
    assert report["outcome"] == m.FAILED, report
    assert "lock" in report["why"]
    assert report["outcome"] != m.BUSY


# --------------------------------------------------------------------------- the wire, again


def test_start_does_not_rebase_when_a_different_live_process_owns_this_goal(tmp_path):
    """Review §5. `_rebase_upkeep` sits ABOVE `start()`'s second `_resume_blocked_by_a_live_sibling`
    gate, so on the branch-outlived-its-record path the force-push had already happened by the time
    `start()` discovered another live process owns the goal."""
    work = _load("work")
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    calls = []
    work._FEATURE_REBASE = types.SimpleNamespace(
        upkeep=lambda *a, **k: calls.append(1) or {"note": ""})
    work._FEATURE_SYNC = types.SimpleNamespace(sync_at_pick=lambda *a, **k: {"note": ""})
    def run(cwd, argv):
        # The branch-outlived-its-record path: `worktree add -b` fails because the branch is
        # already there, which is the ONLY path on which `start()` asks the liveness question a
        # second time -- and it asks it AFTER the cut, far below where upkeep used to run.
        if "add" in argv and "-b" in argv:
            raise RuntimeError("fatal: a branch named 'sdlc/7' already exists")
        return ""

    with _a_live_foreign_process() as pid:
        _register_agent(sdlc, "7", pid)
        out = work.start(str(sdlc), {"work": {"enabled": True, "base": INTEGRATION}}, "7", run=run)
    assert "REFUSED" in out, out
    assert calls == [], "upkeep force-pushed a shared branch for a goal another process owns"


def test_the_goal_conflict_body_measures_the_goal_too(tmp_path):
    """`work.rebase()` aborts a CONFLICT, so "the tree is clean and the branch untouched" is true
    for that case -- but it was asserted for every non-`rebased` return, including that function's
    own post-rebase push failure, where nothing aborted and the local branch has moved."""
    m = _mod()
    world = World(tmp_path).build()
    world.goal_branch(11, files=(("shared.txt", "goal side"),))
    world.write_registry(goals=[11])
    _register_agent(world.sdlc, 11, 999999)
    world.move_integration(name="shared.txt", body="integration side")
    filed = _filer(m)
    goal_tip = world.tip("sdlc/11")
    report = _upkeep(m, world)
    assert report["conflicts"] == ["sdlc/11"]
    body = filed[0]["body"]
    assert "Measured afterwards:" in body
    assert "its worktree is clean" in body
    assert goal_tip[:12] in body, "the body must quote the tip it measured"
    assert "rebase is still stopped" not in body


def test_the_clause_names_the_issue_it_filed(tmp_path):
    """`report["issues"]` had no consumer on the pick path either, so the one line the operator
    reads told them a finding was filed and left them to go looking for it."""
    m = _mod()
    world = World(tmp_path).build(feature_commits=(
        ("f.txt", "f", "feat: land a goal (#11)"),
        ("h.txt", "h", "quick fix, straight onto the branch")))
    world.move_integration()
    _filer(m)
    report = _upkeep(m, world)
    assert report["outcome"] == m.DIRECT_COMMITS
    assert report["issues"] == ["999"]
    assert "filed as #999" in report["note"], report["note"]


def test_the_filed_marker_refuses_a_name_that_is_not_a_unit_name(tmp_path):
    m = _mod()
    for bad in ("../../etc", "a/b", "", None, 7):
        with pytest.raises(m.registry.InvalidUnitName):
            m.filed_path(str(tmp_path), bad)
    assert m.filed_path(str(tmp_path), UNIT).name == UNIT + ".rebase-filed.json"


def test_a_live_claim_lock_alone_is_enough_to_leave_a_goal_alone(tmp_path):
    """The second of the two markers. `agent_watch` and the ledger are independently opt-in, so
    either one saying "somebody is here" has to be enough on its own."""
    m = _mod()
    world = World(tmp_path).build()
    calls = []
    real = m._load

    def fake_load(name):
        mod = real(name)
        if name == "loop":
            mod.agent_threads = lambda *a, **k: []
            mod.claim_lock_alive = lambda *a, **k: (calls.append(1) or ("alive", 4242))
        return mod

    m._load = fake_load
    m._WORK = None
    try:
        assert m.goal_liveness(str(world.sdlc), _cfg(), "11") == m.LIVE
    finally:
        m._load = real
    assert calls, "claim_lock_alive was never consulted"


def test_an_unreadable_worktree_listing_is_reported_rather_than_read_as_clean(tmp_path):
    """`leftovers` returning [] MEANS measured-clean. A listing that could not be read is not that,
    and collapsing the two is the whole bug this function exists to stop."""
    m = _mod()
    def boom(cwd, argv):
        raise RuntimeError("git is gone")
    found = m.leftovers(boom, str(tmp_path), tmp_path / "nowhere")
    assert found and "could not be read" in found[0]
    assert m.leftovers(lambda cwd, argv: "worktree /elsewhere\n", str(tmp_path),
                       tmp_path / "nowhere") == []



def test_a_repos_key_in_another_casing_is_still_this_repo(tmp_path):
    """#1477 (merged into the base under this goal) stopped `_record_goal` widening `repos` via
    `setdefault`, which makes every key after the first a HUMAN-TYPED one -- and added `same_repo` /
    `repo_key` because `owner/name` is case-insensitively unique on GitHub, so two casings were
    never two repos.

    This module looked its goals up with an exact `repos.get(repo)`, which is a second opinion about
    that same question: against a hand-written `Org/Repo` it silently found no goals and the whole
    replay half went inert, reporting `rebased` with nothing replayed. Use the sibling's own
    resolver rather than re-deriving the rule."""
    m = _mod()
    world = World(tmp_path).build()
    world.goal_branch(11)
    world.write_registry(goals=[11], repo="Org/Repo")      # the human typed it with capitals
    _register_agent(world.sdlc, 11, 999999)
    world.move_integration()
    report = _upkeep(m, world)                             # config still says `org/repo`
    assert report["outcome"] == m.REBASED, report
    assert report["replayed"] == ["sdlc/11"], report
    feature_tip, goal_tip = world.tip(FEATURE), world.tip("sdlc/11")
    assert _git(world.local, "rev-list", "--count", "%s..%s" % (goal_tip, feature_tip)) == "0"


# ===========================================================================================
# P6 REVIEW round 2 — the three findings in the REMEDIATION. A fix written to close a review
# gets less adversarial attention than the original code did, precisely because it feels like
# tidying; the suppressor below was answering a nit and shipped two defects of its own.
# ===========================================================================================


def _failing_filer(m, fail_first=1):
    """A filer that fails the way `gh` actually fails: `create_tracked_issue` NEVER RAISES, so a
    `gh` outage comes back as `issue: None` plus a warning. Returns the attempt log."""
    attempts = []

    def fake(sdlc_dir, config, goal, area, why, **kwargs):
        attempts.append({"goal": goal, **kwargs})
        if len(attempts) <= fail_first:
            return {"issue": None, "warnings": ["gh: could not create an issue (503)"],
                    "issue_attempted": True}
        return {"issue": "900%d" % len(attempts), "warnings": []}

    m._HANDOFF = types.SimpleNamespace(create_tracked_issue=fake)
    return attempts


def test_a_filing_that_never_landed_does_not_burn_the_fingerprint(tmp_path):
    """R1. `_is_news` recorded AND persisted the fingerprint, then returned True, and only
    afterwards was the filing attempted -- so a `gh` outage burned the fingerprint for a finding
    that was never filed. Measured: zero further attempts were ever made, and every later pick's
    clause still said the finding had been "filed as work" with no issue in existence.

    Asserted on the ATTEMPT COUNT and on the clause, not on an outcome string: the outcome is
    `conflict` in both the broken and the fixed world."""
    m = _mod()
    world = World(tmp_path).build(
        feature_commits=(("seed.txt", "feature edit", "feat: touch the seed (#11)"),))
    world.move_integration(name="seed.txt", body="integration edit")
    attempts = _failing_filer(m, fail_first=1)

    first = _upkeep(m, world)
    assert first["outcome"] == m.CONFLICT
    assert len(attempts) == 1 and first["issues"] == []
    assert "filed as work" not in first["note"], first["note"]
    assert "could not be filed" in first["note"], first["note"]

    second = _upkeep(m, world)                       # gh has recovered
    assert second["outcome"] == m.CONFLICT
    assert len(attempts) == 2, "the finding was never re-attempted after a failed filing"
    assert second["issues"] == ["9002"]
    assert "filed as #9002" in second["note"], second["note"]

    third = _upkeep(m, world)                        # NOW it is genuinely already filed
    assert len(attempts) == 2, "a finding that really was filed must not be filed again"
    assert third["filing"] == m.ALREADY_FILED
    assert "already filed" in third["note"], third["note"]


def test_the_clause_never_claims_a_filing_that_does_not_exist(tmp_path):
    """`_WORDING[CONFLICT]` said "filed as work" and `_WORDING[DIRECT_COMMITS]` said "see the filed
    issue", both unconditionally. An operator reading either went looking for an issue."""
    m = _mod()
    world = World(tmp_path).build(feature_commits=(
        ("f.txt", "f", "feat: land a goal (#11)"),
        ("h.txt", "h", "quick fix, straight onto the branch")))
    world.move_integration()
    _failing_filer(m, fail_first=99)                 # gh never recovers
    report = _upkeep(m, world)
    assert report["outcome"] == m.DIRECT_COMMITS
    assert report["issues"] == []
    assert report["filing"] == m.FILING_FAILED
    assert "see the filed issue" not in report["note"]
    assert "could not be filed" in report["note"], report["note"]


def test_the_suppressor_keys_per_finding_not_per_title_prefix(tmp_path):
    """R2. The store key was `title.split(":")[0]`, so every `Rebase conflict: ...` finding shared
    one slot. With one conflicting goal branch that looks fine; with TWO they overwrite each
    other's fingerprint every pass and neither is ever suppressed -- the suppressor is inert in
    exactly the multi-goal drain it exists for.

    Three picks, the integration branch moving between them, counting NEW filings each time."""
    m = _mod()
    world = World(tmp_path).build()
    world.goal_branch(11, files=(("shared11.txt", "goal side"),))
    world.goal_branch(12, files=(("shared12.txt", "goal side"),))
    world.write_registry(goals=[11, 12])
    _register_agent(world.sdlc, 11, 999999)
    _register_agent(world.sdlc, 12, 999999)
    world.move_integration(name="shared11.txt", body="integration side")
    world.move_integration(name="shared12.txt", body="integration side")
    filed = _filer(m)

    counts = []
    for round_ in range(3):
        before = len(filed)
        report = _upkeep(m, world)
        counts.append((report["outcome"], len(report["conflicts"]), len(filed) - before))
        world.move_integration(name="m%d.txt" % round_, body="more")

    assert [c[1] for c in counts] == [2, 2, 2], counts
    assert [c[2] for c in counts] == [2, 0, 0], (
        "two findings sharing one slot overwrite each other and neither is ever suppressed: %s"
        % (counts,))
    store = json.loads(m.filed_path(str(world.sdlc), UNIT).read_text(encoding="utf-8"))
    assert sorted(store) == ["goal-conflict:sdlc/11", "goal-conflict:sdlc/12"], store


# --------------------------------------------------------------------------- R3: each half of leftovers


def test_leftovers_reports_a_directory_with_no_registration(tmp_path):
    """R3, half one. The two measurements come apart in the real world: git prunes the admin record
    but cannot `rmdir`, so ONLY the directory half is true. Blinding this half alone used to leave
    the whole file green while the filed issue said "no worktree was left behind"."""
    m = _mod()
    world = World(tmp_path).build()
    orphan = m.worktree_path(str(world.sdlc), UNIT)
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "left.txt").write_text("x\n", encoding="utf-8")
    found = m.leftovers(m._run, str(world.local), orphan)
    assert len(found) == 1, found
    assert "still on disk" in found[0] and str(orphan) in found[0]
    assert not any("registered" in one for one in found)


def test_leftovers_reports_a_registration_with_no_directory(tmp_path):
    """R3, half two, and the mirror image: the directory is gone and git still believes in it, which
    is what makes the next `git worktree add` refuse."""
    m = _mod()
    world = World(tmp_path).build()
    ghost = m.worktree_path(str(world.sdlc), UNIT)
    ghost.parent.mkdir(parents=True, exist_ok=True)
    _git(world.local, "worktree", "add", "-q", "--detach", str(ghost), "origin/" + FEATURE)
    shutil.rmtree(str(ghost))                       # deleted by hand; NOT pruned
    found = m.leftovers(m._run, str(world.local), ghost)
    assert len(found) == 1, found
    assert "registered" in found[0] and str(ghost) in found[0]
    assert not any("still on disk" in one for one in found)


def test_leftovers_is_empty_only_when_both_halves_measured_clean(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    gone = m.worktree_path(str(world.sdlc), UNIT)
    assert m.leftovers(m._run, str(world.local), gone) == []


def test_the_filed_issue_names_every_leftover_it_measured(tmp_path):
    """The body has to carry the CONTENTS, not merely be non-empty: a one-sided leftover reported
    as "no worktree was left behind" is the exact sentence F3 existed to stop."""
    m = _mod()
    world = World(tmp_path).build(
        feature_commits=(("seed.txt", "feature edit", "feat: touch the seed (#11)"),))
    world.move_integration(name="seed.txt", body="integration edit")
    filed = _filer(m)
    m._drop_worktree = lambda run, cwd, path: None
    report = _upkeep(m, world)
    assert report["outcome"] == m.CONFLICT
    assert sorted(report["leftovers"]) == sorted([
        "the throwaway worktree at `%s` is still on disk" % m.worktree_path(str(world.sdlc), UNIT),
        "git still has a worktree registered at `%s`" % m.worktree_path(str(world.sdlc), UNIT)]), (
        report["leftovers"])
    body = filed[0]["body"]
    for one in report["leftovers"]:
        assert one in body, one


def test_the_occupied_clause_says_how_to_clear_it(tmp_path):
    """Non-blocking review request: nothing clears an `occupied` path, so a second pass repeats it
    forever. The clause has to carry the gesture."""
    m = _mod()
    world = World(tmp_path).build()
    world.move_integration()
    occupied = m.worktree_path(str(world.sdlc), UNIT)
    occupied.parent.mkdir(parents=True, exist_ok=True)
    _git(world.local, "worktree", "add", "-q", "--detach", str(occupied), "origin/" + FEATURE)
    m.sync.fcntl = None
    report = _upkeep(m, world)
    assert report["outcome"] == m.OCCUPIED
    assert "git worktree remove --force" in report["note"], report["note"]
    assert str(occupied) in report["note"]

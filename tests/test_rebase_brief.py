"""rebase_brief.py -- agrim-rebase slice 1 (#2304, epic #2303, design `.sdlc/design/2288.md`).

WHY THESE TESTS RUN REAL `git`, mirroring `tests/test_feature_rebase.py`'s own rationale: this
module force-pushes a branch and rebases a real working tree, and the three properties that make
that safe -- a conflict aborts cleanly, a stale lease refuses, `--first-parent` tells a landing
apart from a direct commit -- are properties of git itself, not of this code. A fake runner would
only assert what this module BELIEVES git does; only real git asserts what it does.

The fixture below is deliberately NOT `test_feature_rebase.py`'s `World` (a throwaway bare remote
plus an EPHEMERAL detached worktree): this skill's whole premise is a human's own live checkout on
their own branch (design D-2), so the fixture is a bare remote plus one ordinary clone, checked out
directly on the feature branch, exactly the tree the skill would actually run against.
"""
import importlib.util
import json
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "agrim-rebase" / "scripts"


def _load(name, directory=SCRIPTS):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(directory) / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mod():
    return _load("rebase_brief")


BASE = "main"
BRANCH = "feature/x"


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


def _run(cwd, argv):
    """The kit's standard `(cwd, argv) -> stdout` contract, raising on a non-zero exit -- what
    every real function under test is called with."""
    proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(str(a) for a in argv),
                                       (proc.stderr or proc.stdout).strip()))
    return proc.stdout.strip()


class World:
    """A throwaway bare remote plus ONE ordinary clone, checked out on `feature/x` -- the human's
    own live checkout, not an ephemeral worktree (design D-2)."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.remote = self.root / "remote.git"
        self.local = self.root / "local"

    def build(self, changelog=None):
        _git(self.root, "init", "-q", "--bare", str(self.remote))
        _git(self.root, "init", "-q", "-b", BASE, str(self.local))
        _git(self.local, "remote", "add", "origin", str(self.remote))
        _write(self.local / "seed.txt", "seed\n")
        if changelog is not None:
            _write(self.local / "CHANGELOG.md", changelog)
            _git(self.local, "add", "CHANGELOG.md")
        _git(self.local, "add", "seed.txt")
        _git(self.local, "commit", "-q", "-m", "seed")
        _git(self.local, "push", "-q", "-u", "origin", BASE)
        _git(self.local, "checkout", "-q", "-b", BRANCH)
        _write(self.local / "f.txt", "f\n")
        _git(self.local, "add", "f.txt")
        _git(self.local, "commit", "-q", "-m", "feat: seed the feature branch (#1)")
        _git(self.local, "push", "-q", "-u", "origin", BRANCH)
        return self

    def commit_on_base(self, name, body, subject, changelog_append=None):
        """Simulate someone ELSE landing work on `main` while we stayed on `feature/x`: switch,
        commit, push, switch back -- leaving the local checkout back where the human left it."""
        cur = _git(self.local, "rev-parse", "--abbrev-ref", "HEAD")
        _git(self.local, "checkout", "-q", BASE)
        if changelog_append:
            path = self.local / "CHANGELOG.md"
            path.write_text((path.read_text() if path.exists() else "") + changelog_append,
                            encoding="utf-8")
            _git(self.local, "add", "CHANGELOG.md")
        _write(self.local / name, body + "\n")
        _git(self.local, "add", name)
        _git(self.local, "commit", "-q", "-m", subject)
        _git(self.local, "push", "-q", "origin", BASE)
        _git(self.local, "checkout", "-q", cur)
        return self

    def delete_on_base(self, name, subject):
        cur = _git(self.local, "rev-parse", "--abbrev-ref", "HEAD")
        _git(self.local, "checkout", "-q", BASE)
        _git(self.local, "rm", "-q", name)
        _git(self.local, "commit", "-q", "-m", subject)
        _git(self.local, "push", "-q", "origin", BASE)
        _git(self.local, "checkout", "-q", cur)
        return self

    def tip(self, branch):
        out = _git(self.local, "ls-remote", "--heads", "origin", branch)
        return out.split("\t")[0] if out else None


# --------------------------------------------------------------------------- resolution (§2)


def test_resolve_base_reads_work_base_unconditionally():
    m = _mod()
    assert m.resolve_base({"work": {"base": "main"}}) == "main"
    assert m.resolve_base({"work": {}}) == ""
    assert m.resolve_base({}) == ""


def test_resolve_remote_falls_back_to_origin():
    m = _mod()
    assert m.resolve_remote({}) == "origin"
    assert m.resolve_remote({"work": {"remote": "upstream"}}) == "upstream"


def test_resolve_branch_defaults_to_current_checkout(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    assert m.resolve_branch(_run, str(world.local)) == BRANCH


def test_resolve_branch_accepts_an_explicit_argument(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    assert m.resolve_branch(_run, str(world.local), "main") == "main"


# --------------------------------------------------------------------------- PR number (§3.2)


@pytest.mark.parametrize("subject,expected", [
    ("feat: land a goal (#2294) (#2299)", "2299"),
    ("Merge pull request #45 from org/repo", "45"),
    ("fix: reference issue #12 in the middle of a subject", None),
    ("no reference at all", None),
    ("(#2294) (#2299) trailing but not anchored to end ", None),
])
def test_pr_number_extracts_the_landing_reference_never_an_earlier_issue(subject, expected):
    assert _mod()._pr_number(subject) == expected


def test_pr_number_reuses_feature_rebases_own_anchoring_not_a_new_pattern():
    """The squash form's captured number is the TRAILING one, never a middle issue reference --
    the exact property BR-2 found `arrived_through_a_pull_request` lacks a returned number for."""
    m = _mod()
    fr = m.feature_rebase
    subject = "feat: something (#100) (#200)"
    assert fr.arrived_through_a_pull_request(subject) is True
    assert m._pr_number(subject) == "200"


# --------------------------------------------------------------------------- issue-id extraction


def test_extract_issue_ids_matches_changelog_coverages_own_technique():
    m = _mod()
    assert m.extract_issue_ids("feat: land a goal (#2294) (#2299)") == {"2294", "2299"}
    assert m.extract_issue_ids("") == set()


def test_extract_issue_ids_falls_back_when_changelog_coverage_is_not_loadable(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m, "_changelog_coverage", lambda: None)
    assert m.extract_issue_ids("(#7) and (#9)") == {"7", "9"}


def test_this_repos_own_changelog_coverage_module_is_actually_reused(tmp_path, monkeypatch):
    """Not a hypothetical fallback path: where a checkout carries `.github/scripts/
    changelog_coverage.py`, the real module -- not the local port -- is what answers.

    Hermetic since #2584: the module is PLANTED under a tmp repository root, so the property is the
    loader's (the path it builds is right and the loaded module is what answers), whether or not
    the checkout running this carries that file -- the public core does not."""
    m = _mod()
    planted = tmp_path / ".github" / "scripts" / "changelog_coverage.py"
    planted.parent.mkdir(parents=True)
    planted.write_text("PLANTED = True\n"
                       "def extract_issue_ids(text):\n"
                       "    return {'planted-2584'}\n", encoding="utf-8")
    monkeypatch.setattr(m, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(m, "_CHANGELOG_COVERAGE", m._UNSET)
    cc = m._changelog_coverage()
    assert cc is not None and getattr(cc, "PLANTED", False), cc
    assert m.extract_issue_ids("(#11)") == {"planted-2584"}


# --------------------------------------------------------------------------- CHANGELOG matching


CHANGELOG = """## Unreleased

### fix(x): something real (#2294)
body text, not itself carrying #2299 anywhere

### feat(y): a different thing (#2296) (#2298)
more body
"""


def test_changelog_entries_reads_only_the_heading_never_the_body():
    m = _mod()
    entries = m.changelog_entries(CHANGELOG)
    assert entries == [
        ("### fix(x): something real (#2294)", {"2294"}),
        ("### feat(y): a different thing (#2296) (#2298)", {"2296", "2298"}),
    ]


def test_changelog_match_finds_the_heading_sharing_an_id():
    m = _mod()
    entries = m.changelog_entries(CHANGELOG)
    assert m.changelog_match({"2294", "2299"}, entries) == "### fix(x): something real (#2294)"


def test_changelog_match_never_matches_a_body_only_reference():
    """The round-1 REJECT-fix this design documents: a number appearing only in an entry's BODY
    (never its own heading) must not produce a false match."""
    m = _mod()
    entries = m.changelog_entries(CHANGELOG)
    assert m.changelog_match({"2299"}, entries) is None


def test_changelog_match_is_order_independent_issue_then_pr_or_pr_then_issue():
    """The exact property BR-2's old trailing-only extraction lacked: correct regardless of
    whether the subject orders issue-then-PR or PR-then-issue."""
    m = _mod()
    entries = m.changelog_entries(CHANGELOG)
    issue_then_pr = m.extract_issue_ids("feat(y): a different thing (#2296) (#2298)")
    pr_then_issue = m.extract_issue_ids("feat(y): a different thing (#2298) (#2296)")
    assert m.changelog_match(issue_then_pr, entries) == m.changelog_match(pr_then_issue, entries)
    assert m.changelog_match(issue_then_pr, entries) is not None


# --------------------------------------------------------------------------- the delta (§3.1)


def test_delta_commits_is_what_the_base_did_since_the_branch_diverged(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("m1.txt", "m1", "chore: base moves (#5)")
    cwd = str(world.local)
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    delta = m.delta_commits(_run, cwd, BRANCH, "origin/%s" % BASE)
    subjects = [s for _, s in delta]
    assert subjects == ["chore: base moves (#5)"]


def test_delta_commits_is_empty_when_branch_already_carries_the_base(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    cwd = str(world.local)
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    assert m.delta_commits(_run, cwd, BRANCH, "origin/%s" % BASE) == []


# --------------------------------------------------------------------------- commit context chain


def test_commit_context_prefers_the_changelog_heading_match(tmp_path):
    m = _mod()
    world = World(tmp_path).build(changelog=CHANGELOG)
    cwd = str(world.local)
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    base_ref = "origin/%s" % BASE
    entries = m.changelog_entries(m.changelog_text(_run, cwd, base_ref))
    ctx = m.commit_context(_run, cwd, "deadbeef", "fix(x): something real (#2294)", entries, base_ref)
    assert ctx == {"source": "changelog", "detail": "### fix(x): something real (#2294)"}


def test_commit_context_falls_back_to_the_pr_description_via_gh(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    cwd = str(world.local)
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    base_ref = "origin/%s" % BASE
    calls = []

    def fake_run(c, argv):
        if argv[:3] == ["gh", "pr", "view"]:
            calls.append(argv)
            return '{"title": "Fix the union rescue", "body": "because X"}'
        return _run(c, argv)

    ctx = m.commit_context(fake_run, cwd, "deadbeef", "fix: something (#2294) (#2299)", [], base_ref)
    assert ctx["source"] == "pr"
    assert "Fix the union rescue" in ctx["detail"]
    assert len(calls) == 1
    assert calls[0][3] == "2299"               # the TRAILING (PR) number, never the issue


def test_commit_context_falls_back_to_a_linked_design_doc(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    _git(world.local, "checkout", "-q", BASE)
    (world.local / ".sdlc" / "design").mkdir(parents=True)
    (world.local / ".sdlc" / "design" / "77.md").write_text("# Design: something\n")
    _git(world.local, "add", ".sdlc/design/77.md")
    _git(world.local, "commit", "-q", "-m", "design: 77")
    _git(world.local, "push", "-q", "origin", BASE)
    _git(world.local, "checkout", "-q", BRANCH)
    cwd = str(world.local)
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    base_ref = "origin/%s" % BASE

    def no_gh(c, argv):
        if argv[:3] == ["gh", "pr", "view"]:
            raise RuntimeError("no gh available")
        return _run(c, argv)

    ctx = m.commit_context(no_gh, cwd, "deadbeef", "feat: something (#77)", [], base_ref)
    assert ctx == {"source": "design", "detail": ".sdlc/design/77.md"}


def test_commit_context_is_an_honest_floor_never_a_fabrication_or_a_silent_empty_brief(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    cwd = str(world.local)
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    base_ref = "origin/%s" % BASE

    def no_gh(c, argv):
        if argv[:3] == ["gh", "pr", "view"]:
            raise RuntimeError("no gh available")
        return _run(c, argv)

    ctx = m.commit_context(no_gh, cwd, "deadbeef", "chore: tidy (#999)", [], base_ref)
    assert ctx == {"source": "none", "detail": m.NO_EXPLANATION}


def test_the_shipped_gap_this_design_names_is_walked_through_correctly():
    """The design's own worked example (§3 step 3, the '#2298/#2299 shipped with no CHANGELOG
    entry yet' walk-through): during the gap, step 1 finds no intersecting heading and falls
    through to step 2 correctly -- exercised here as a pure changelog_match check since the
    historical commits themselves have long since scrolled out of any small fixture's history."""
    m = _mod()
    ids = m.extract_issue_ids("feat(x): auto-apply corrections (#2294) (#2299)")
    assert ids == {"2294", "2299"}
    # before 61a1e9a2 landed: no heading yet
    assert m.changelog_match(ids, []) is None
    # after it landed: the heading is keyed to the ISSUE, never the trailing PR
    after = m.changelog_entries("### auto-apply corrections (#2294)\n")
    assert m.changelog_match(ids, after) == "### auto-apply corrections (#2294)"


# --------------------------------------------------------------------------- files (§3.4-5)


def test_overlapping_files_flags_only_what_both_sides_touched(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("shared.txt", "base version", "chore: base touches shared.txt")
    cwd = str(world.local)
    _write(world.local / "shared.txt", "branch version\n")
    _git(world.local, "add", "shared.txt")
    _git(world.local, "commit", "-q", "-m", "feat: branch also touches shared.txt")
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    base_ref = "origin/%s" % BASE
    merge_base = _run(cwd, ["git", "merge-base", BRANCH, base_ref])
    overlap = m.overlapping_files(_run, cwd, merge_base, BRANCH, base_ref)
    assert overlap == ["shared.txt"]


def test_overlapping_files_is_empty_when_the_two_sides_never_touch_the_same_path(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("only_on_base.txt", "b", "chore: base-only file")
    cwd = str(world.local)
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    base_ref = "origin/%s" % BASE
    merge_base = _run(cwd, ["git", "merge-base", BRANCH, base_ref])
    assert m.overlapping_files(_run, cwd, merge_base, BRANCH, base_ref) == []


def test_deleting_commit_finds_the_commit_that_deleted_a_file_on_the_base_side(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("doomed.txt", "here for now", "chore: add doomed.txt")
    world.delete_on_base("doomed.txt", "chore: remove doomed.txt, it moved (#42)")
    cwd = str(world.local)
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    base_ref = "origin/%s" % BASE
    merge_base = _run(cwd, ["git", "merge-base", BRANCH, base_ref])
    found = m.deleting_commit(_run, cwd, merge_base, base_ref, "doomed.txt")
    assert found is not None
    sha, subject = found
    assert subject == "chore: remove doomed.txt, it moved (#42)"


def test_deleting_commit_is_none_for_a_file_that_was_never_deleted(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("m.txt", "m", "chore: base moves")
    cwd = str(world.local)
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    base_ref = "origin/%s" % BASE
    merge_base = _run(cwd, ["git", "merge-base", BRANCH, base_ref])
    assert m.deleting_commit(_run, cwd, merge_base, base_ref, "m.txt") is None


def test_file_context_explains_a_deletion_specifically(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("doomed.txt", "here", "chore: add doomed.txt")
    world.delete_on_base("doomed.txt", "chore: remove doomed.txt (#42)")
    cwd = str(world.local)
    _run(cwd, ["git", "fetch", "origin", BASE, BRANCH])
    base_ref = "origin/%s" % BASE
    merge_base = _run(cwd, ["git", "merge-base", BRANCH, base_ref])
    ctx = m.file_context(_run, cwd, merge_base, base_ref, "doomed.txt", [])
    assert ctx["deleted_on_base"] is True
    assert len(ctx["commits"]) == 1
    assert ctx["commits"][0]["subject"] == "chore: remove doomed.txt (#42)"


# --------------------------------------------------------------------------- the brief end-to-end


def test_assemble_brief_and_format_brief_end_to_end(tmp_path):
    m = _mod()
    world = World(tmp_path).build(changelog=CHANGELOG)
    world.commit_on_base("m1.txt", "m1", "fix(x): something real (#2294)")
    cwd = str(world.local)
    brief = m.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    assert len(brief["delta"]) == 1
    assert brief["delta"][0]["context"]["source"] == "changelog"
    text = m.format_brief(brief)
    assert "fix(x): something real (#2294)" in text
    assert "CHANGELOG:" in text


def test_format_brief_says_theres_nothing_to_explain_when_current(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    cwd = str(world.local)
    brief = m.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    assert brief["delta"] == []
    assert "nothing to explain" in m.format_brief(brief)


# --------------------------------------------------------------------------- the rebase (§4)


def test_attempt_rebase_reports_current_and_touches_nothing(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    cwd = str(world.local)
    before = world.tip(BRANCH)
    report = m.attempt_rebase(_run, cwd, "origin", BRANCH, BASE)
    assert report["outcome"] == m.CURRENT
    assert world.tip(BRANCH) == before


def test_attempt_rebase_brings_the_branch_forward_and_pushes(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("m1.txt", "m1", "chore: base moves")
    cwd = str(world.local)
    old_tip = world.tip(BRANCH)
    integration_tip = world.tip(BASE)
    report = m.attempt_rebase(_run, cwd, "origin", BRANCH, BASE)
    assert report["outcome"] == m.REBASED, report
    new_tip = world.tip(BRANCH)
    assert new_tip != old_tip
    # the integration tip is now an ancestor of the rebased branch's tip
    assert _run(cwd, ["git", "rev-list", "--count", "%s..%s" % (new_tip, integration_tip)]) == "0"
    # and the branch's own commit survived the replay
    assert "seed the feature branch" in _run(cwd, ["git", "log", "--format=%s", "-1", new_tip])
    # the tree is left clean -- no stopped rebase
    assert m.feature_rebase.rebase_stopped(_run, cwd) is False
    assert _run(cwd, ["git", "status", "--porcelain"]) == ""


def test_attempt_rebase_refuses_a_replay_that_would_lose_the_branchs_content(tmp_path):
    """#144's tree guard, reused before THIS force-push too: nothing is pushed, and the human's
    local branch is put back where it was."""
    m = _mod()
    world = World(tmp_path).build()
    world.delete_on_base("seed.txt", "chore: base drops seed")
    cwd = str(world.local)
    before_branch = world.tip(BRANCH)
    local_before = _run(cwd, ["git", "rev-parse", "HEAD"])
    report = m.attempt_rebase(_run, cwd, "origin", BRANCH, BASE)
    assert report["outcome"] == m.WOULD_DROP and report["files"] == ["seed.txt"], report
    assert "put back" in report["why"], report
    assert world.tip(BRANCH) == before_branch
    assert _run(cwd, ["git", "rev-parse", "HEAD"]) == local_before
    assert (world.local / "seed.txt").exists()


def test_attempt_rebase_guard_control_the_fixture_loses_content_without_it(tmp_path, monkeypatch):
    """Sensitivity: with the comparison disabled the same fixture pushes and loses seed.txt."""
    m = _mod()
    world = World(tmp_path).build()
    world.delete_on_base("seed.txt", "chore: base drops seed")
    monkeypatch.setattr(m.feature_rebase, "dropped_paths", lambda *a, **k: [])
    report = m.attempt_rebase(_run, str(world.local), "origin", BRANCH, BASE)
    assert report["outcome"] == m.REBASED
    assert "seed.txt" not in _run(str(world.local), ["git", "ls-tree", "-r", "--name-only",
                                                     world.tip(BRANCH)])


def test_attempt_rebase_keeps_a_merge_commit_landing_rather_than_flattening_it(tmp_path):
    """#2756: a plain rebase drops a "Merge pull request #N" landing and replays its second-parent
    commits flat onto the first-parent line, where upkeep's no-direct-commits check refuses them
    on every later pass. The human-attended rebase must not create that state either."""
    m = _mod()
    world = World(tmp_path).build()
    _git(world.local, "checkout", "-q", "-b", "sdlc/5")
    _write(world.local / "g.txt", "g\n")
    _git(world.local, "add", "g.txt")
    _git(world.local, "commit", "-q", "-m", "sdlc: 5")
    _git(world.local, "checkout", "-q", BRANCH)
    _git(world.local, "merge", "-q", "--no-ff", "-m", "Merge pull request #9 from org/sdlc/5", "sdlc/5")
    _git(world.local, "push", "-q", "origin", BRANCH)
    world.commit_on_base("m1.txt", "m1", "chore: base moves")
    cwd = str(world.local)
    report = m.attempt_rebase(_run, cwd, "origin", BRANCH, BASE)
    assert report["outcome"] == m.REBASED, report
    first_parent = _run(cwd, ["git", "log", "--first-parent", "--format=%s",
                              "origin/%s..%s" % (BASE, world.tip(BRANCH))]).splitlines()
    assert first_parent == ["Merge pull request #9 from org/sdlc/5",
                            "feat: seed the feature branch (#1)"], first_parent


def test_161_attempt_rebase_still_refuses_a_reverted_merge_landing_under_rebase_merges(tmp_path):
    """#161: `--rebase-merges` puts merge commits into the replayed history; the #144/#278 guard
    in front of this push (`_would_lose`, then `push_branch`) must still see a base revert of a
    merge landing's content -- a deletion and a rollback -- and push nothing."""
    m = _mod()
    world = World(tmp_path).build()
    _git(world.local, "checkout", "-q", "-b", "sdlc/5")
    _write(world.local / "g.txt", "g\n")
    _git(world.local, "add", "g.txt")
    _git(world.local, "commit", "-q", "-m", "sdlc: 5 part 1")
    _write(world.local / "seed.txt", "seed, edited by goal 5\n")
    _git(world.local, "add", "seed.txt")
    _git(world.local, "commit", "-q", "-m", "sdlc: 5 part 2")
    _git(world.local, "checkout", "-q", BRANCH)
    _git(world.local, "merge", "-q", "--no-ff", "-m", "Merge pull request #9 from org/sdlc/5", "sdlc/5")
    _git(world.local, "push", "-q", "origin", BRANCH)
    _git(world.local, "checkout", "-q", BASE)
    _git(world.local, "cherry-pick", "sdlc/5~1", "sdlc/5")
    _git(world.local, "revert", "--no-edit", "HEAD", "HEAD~1")
    _git(world.local, "push", "-q", "origin", BASE)
    _git(world.local, "checkout", "-q", BRANCH)
    cwd = str(world.local)
    before = world.tip(BRANCH)
    local_before = _run(cwd, ["git", "rev-parse", "HEAD"])
    seen = []

    def watch(c, argv):
        seen.append([str(a) for a in argv])
        return _run(c, argv)
    report = m.attempt_rebase(watch, cwd, "origin", BRANCH, BASE)
    assert any(a[:2] == ["git", "rebase"] and "--rebase-merges" in a for a in seen), seen
    assert report["outcome"] == m.WOULD_DROP, report
    assert report["files"] == ["g.txt", "seed.txt"], report
    assert world.tip(BRANCH) == before
    assert _run(cwd, ["git", "rev-parse", "HEAD"]) == local_before


def test_attempt_rebase_on_a_conflict_stops_and_leaves_both_repos_as_they_were(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("f.txt", "base changed this line", "chore: base edits f.txt")
    cwd = str(world.local)
    _write(world.local / "f.txt", "branch changed this line\n")
    _git(world.local, "add", "f.txt")
    _git(world.local, "commit", "-q", "-m", "feat: branch also edits f.txt")
    before_base = world.tip(BASE)
    before_branch = world.tip(BRANCH)
    report = m.attempt_rebase(_run, cwd, "origin", BRANCH, BASE)
    assert report["outcome"] == m.CONFLICT, report
    assert report["files"] == ["f.txt"]
    # a genuine conflict -- the rebase is stopped exactly as `git rebase` leaves one, not aborted
    assert m.feature_rebase.rebase_stopped(_run, cwd) is True
    # NEITHER remote branch moved -- nothing was pushed over a conflict
    assert world.tip(BASE) == before_base
    assert world.tip(BRANCH) == before_branch


def test_a_conflict_report_names_the_base_side_commit_that_caused_it(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("f.txt", "base changed this line", "chore: base edits f.txt (#9)")
    cwd = str(world.local)
    _write(world.local / "f.txt", "branch changed this line\n")
    _git(world.local, "add", "f.txt")
    _git(world.local, "commit", "-q", "-m", "feat: branch also edits f.txt")
    brief = m.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    report = m.attempt_rebase(_run, cwd, "origin", BRANCH, BASE)
    assert report["outcome"] == m.CONFLICT
    text = m.format_conflict(brief, report, _run, cwd)
    assert "f.txt" in text
    assert "chore: base edits f.txt (#9)" in text
    assert "resolve by hand" in text.lower()
    # clean up the stopped rebase so the fixture doesn't leak into a later assertion
    _run(cwd, ["git", "rebase", "--abort"])


def test_attempt_rebase_never_force_pushes_when_the_lease_would_be_stale(tmp_path):
    """A second writer's push must survive a concurrent attempt -- the same property
    `test_feature_rebase.py::test_a_stale_lease_refuses_rather_than_overwrites` proves at the
    feature level, proven here for the plain `--force-with-lease` form this module uses."""
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("m1.txt", "m1", "chore: base moves")
    cwd = str(world.local)
    # simulate a second writer landing a commit on the SAME branch after our local fetch
    other = world.root / "other"
    _git(world.root, "clone", "-q", str(world.remote), str(other))
    _git(other, "checkout", "-q", BRANCH)
    _write(other / "race.txt", "raced\n")
    _git(other, "add", "race.txt")
    _git(other, "commit", "-q", "-m", "feat: a second writer lands first")
    _git(other, "push", "-q", "origin", BRANCH)
    racer_tip = world.tip(BRANCH)
    report = m.attempt_rebase(_run, cwd, "origin", BRANCH, BASE)
    # our local rebase still succeeds locally (nothing here re-fetches branch mid-flight), but the
    # push is refused because the remote moved under the lease this call read at the start
    assert report["outcome"] == m.FAILED, report
    assert "lease" in report["why"].lower() or "stale" in report["why"].lower() or \
        "rejected" in report["why"].lower() or "fetch first" in report["why"].lower()
    assert world.tip(BRANCH) == racer_tip


def test_main_brief_mode_never_mutates(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("m1.txt", "m1", "chore: base moves (#3)")
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(
        '{"work": {"base": "%s", "remote": "origin"}}' % BASE, encoding="utf-8")
    before = world.tip(BRANCH)
    rc = m.main(["rebase_brief.py", "brief", str(sdlc)])
    assert rc == 0
    assert world.tip(BRANCH) == before


def test_main_rebase_mode_on_a_branch_not_checked_out_is_brief_only(tmp_path, capsys):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("m1.txt", "m1", "chore: base moves")
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(
        '{"work": {"base": "%s", "remote": "origin"}}' % BASE, encoding="utf-8")
    # currently checked out on BRANCH; ask about "main" explicitly -- main IS the base, so this
    # exercises the "base == branch" no-op guard instead; use a THIRD branch to exercise the
    # not-checked-out guard properly
    _git(world.local, "checkout", "-q", "-b", "feature/y")
    _git(world.local, "push", "-q", "-u", "origin", "feature/y")
    before = world.tip(BRANCH)
    rc = m.main(["rebase_brief.py", "rebase", str(sdlc), BRANCH])
    assert rc == 0
    assert world.tip(BRANCH) == before        # not checked out on it -- nothing was touched


def test_main_rebase_mode_rebases_and_pushes_the_current_branch(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("m1.txt", "m1", "chore: base moves (#3)")
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(
        '{"work": {"base": "%s", "remote": "origin"}}' % BASE, encoding="utf-8")
    old_tip = world.tip(BRANCH)
    rc = m.main(["rebase_brief.py", "rebase", str(sdlc)])
    assert rc == 0
    assert world.tip(BRANCH) != old_tip


def test_main_rebase_mode_on_a_conflict_exits_nonzero_and_prints_the_context(tmp_path, capsys):
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("f.txt", "base changed this line", "chore: base edits f.txt (#9)")
    _write(world.local / "f.txt", "branch changed this line\n")
    _git(world.local, "add", "f.txt")
    _git(world.local, "commit", "-q", "-m", "feat: branch also edits f.txt")
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(
        '{"work": {"base": "%s", "remote": "origin"}}' % BASE, encoding="utf-8")
    before_base = world.tip(BASE)
    before_branch = world.tip(BRANCH)
    rc = m.main(["rebase_brief.py", "rebase", str(sdlc)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "f.txt" in out
    assert "chore: base edits f.txt (#9)" in out
    assert "resolve by hand" in out.lower()
    assert world.tip(BASE) == before_base
    assert world.tip(BRANCH) == before_branch
    # the rebase is genuinely left stopped, not aborted -- clean it up for fixture teardown
    assert m.feature_rebase.rebase_stopped(_run, str(world.local)) is True
    _run(str(world.local), ["git", "rebase", "--abort"])


def test_main_refuses_cleanly_when_no_base_is_configured(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text('{"work": {"remote": "origin"}}', encoding="utf-8")
    rc = m.main(["rebase_brief.py", "brief", str(sdlc)])
    assert rc == 1


def test_main_refuses_cleanly_when_base_is_this_very_branch(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(
        '{"work": {"base": "%s", "remote": "origin"}}' % BRANCH, encoding="utf-8")
    rc = m.main(["rebase_brief.py", "brief", str(sdlc)])
    assert rc == 1


# --------------------------------------------------------------------------- #2321: decision-
# context snapshot -- a real dummy-repo validation of epic #2303 found the brief re-fetches
# `origin/<base>` fresh on every invocation, so a conflict detected by `rebase_brief.py rebase` and
# resolved LATER (a separate `conflict_walk.py walk` invocation, after the base has moved on) could
# show context describing an entirely different, unrelated commit.


def test_conflict_snapshots_the_decision_context_for_a_later_process_to_read(tmp_path):
    """`format_conflict` -- called from `rebase_brief.py rebase`'s own CONFLICT branch, which is
    BY CONSTRUCTION the moment of first detection -- persists each conflicted file's
    decision-context under `.sdlc/state/rebase-context/<branch-stem>.json`, keyed by path: the
    exact snapshot a later, separate `conflict_walk.py walk` invocation needs to read back instead
    of re-deriving against wherever the base has since moved to (see
    `tests/test_conflict_walk.py`'s own #2321 test for the full timing-window reproduction)."""
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("f.txt", "base changed this line", "chore: base edits f.txt (#9)")
    cwd = str(world.local)
    _write(world.local / "f.txt", "branch changed this line\n")
    _git(world.local, "add", "f.txt")
    _git(world.local, "commit", "-q", "-m", "feat: branch also edits f.txt")
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    brief = m.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    report = m.attempt_rebase(_run, cwd, "origin", BRANCH, BASE)
    assert report["outcome"] == m.CONFLICT, report

    m.format_conflict(brief, report, _run, cwd, str(sdlc))

    store_path = sdlc / "state" / "rebase-context" / "feature_x.json"
    assert store_path.exists()
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert set(stored) == {"f.txt"}
    assert stored["f.txt"]["deleted_on_base"] is False
    assert stored["f.txt"]["commits"][0]["subject"] == "chore: base edits f.txt (#9)"
    _run(cwd, ["git", "rebase", "--abort"])


def test_a_falsy_sdlc_dir_skips_the_snapshot_store_entirely(tmp_path):
    """A caller with no `.sdlc` in scope (`sdlc_dir=None`, `format_conflict`'s own default) gets
    exactly `file_context`'s own fresh answer, and nothing is ever written to disk -- this
    module's pre-#2321 behaviour, preserved for any caller that does not pass one."""
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("f.txt", "base changed this line", "chore: base edits f.txt (#9)")
    cwd = str(world.local)
    _write(world.local / "f.txt", "branch changed this line\n")
    _git(world.local, "add", "f.txt")
    _git(world.local, "commit", "-q", "-m", "feat: branch also edits f.txt")
    brief = m.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    report = m.attempt_rebase(_run, cwd, "origin", BRANCH, BASE)
    assert report["outcome"] == m.CONFLICT, report

    text = m.format_conflict(brief, report, _run, cwd)          # no sdlc_dir at all

    assert "chore: base edits f.txt (#9)" in text
    assert not (world.local / ".sdlc").exists()
    _run(cwd, ["git", "rebase", "--abort"])

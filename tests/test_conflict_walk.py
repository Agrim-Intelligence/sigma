"""conflict_walk.py -- agrim-rebase slice 2 (#2305, epic #2303, design `.sdlc/design/2288.md` §5).

WHY THESE TESTS RUN REAL `git`, mirroring `tests/test_rebase_brief.py`'s own rationale (and
`test_feature_rebase.py`'s before it): this module classifies conflicts from git's own porcelain
status, reads git's own index stages, checks out `--ours`/`--theirs`, and reads git's own on-disk
rebase-state files for resumability. Every one of those is a property of git itself, not of this
code -- the ours/theirs inversion during a rebase in particular is exactly the kind of thing a fake
runner would get wrong by construction, since it would only assert what this module BELIEVES git
does. Fixture shape follows `test_rebase_brief.py`'s `World` (bare remote + one ordinary clone,
checked out directly on the feature branch) for the same reason given there: design D-2, a human's
own live checkout, never an ephemeral worktree.
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
    return _load("conflict_walk")


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
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def _run(cwd, argv):
    """The kit's standard `(cwd, argv) -> stdout` contract, raising on a non-zero exit -- what
    every real function under test is called with. Deliberately `.strip()`s, exactly like
    `feature_sync._run`/`work._run` in the real kit -- the property `apply_option`'s own
    byte-perfect claim (`git checkout --ours/--theirs`, never a `run`-captured blob) has to hold up
    against.

    `GIT_EDITOR="true"`: several tests below call `git rebase --continue` directly (rather than
    through a kit function), which reuses the conflicting commit's own message and would otherwise
    open an editor. Every kit call site that does this already carries the same protection
    (`sync.py`'s `GIT_EDITOR=true`, `work.py`'s and `conflict_walk.py`'s `-c core.editor=true`) for
    the identical reason: on a host with no controlling terminal (CI) git refuses outright rather
    than hanging (sigma's first Linux CI run, #2742/#2724)."""
    env = dict(os.environ, GIT_EDITOR="true")
    proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), capture_output=True, text=True,
                          env=env)
    if proc.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(str(a) for a in argv),
                                       (proc.stderr or proc.stdout).strip()))
    return proc.stdout.strip()


def _try_rebase(cwd, base_ref="origin/%s" % BASE):
    """Attempt the rebase and swallow the conflict -- every fixture below wants the tree LEFT
    stopped, not an exception propagating out of setup."""
    try:
        _run(cwd, ["git", "rebase", base_ref])
    except Exception:                      # noqa: BLE001 - a conflict is the point of the fixture
        pass


class World:
    """A throwaway bare remote plus ONE ordinary clone, checked out on `feature/x`."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.remote = self.root / "remote.git"
        self.local = self.root / "local"

    def build(self):
        _git(self.root, "init", "-q", "--bare", str(self.remote))
        _git(self.root, "init", "-q", "-b", BASE, str(self.local))
        _git(self.local, "remote", "add", "origin", str(self.remote))
        _write(self.local / "seed.txt", "seed\n")
        _git(self.local, "add", "seed.txt")
        _git(self.local, "commit", "-q", "-m", "seed")
        _git(self.local, "push", "-q", "-u", "origin", BASE)
        _git(self.local, "checkout", "-q", "-b", BRANCH)
        _write(self.local / "f.txt", "f\n")
        _git(self.local, "add", "f.txt")
        _git(self.local, "commit", "-q", "-m", "feat: seed the feature branch (#1)")
        _git(self.local, "push", "-q", "-u", "origin", BRANCH)
        return self

    def commit_on_base(self, name, body, subject):
        cur = _git(self.local, "rev-parse", "--abbrev-ref", "HEAD")
        _git(self.local, "checkout", "-q", BASE)
        _write(self.local / name, body + "\n")
        _git(self.local, "add", name)
        _git(self.local, "commit", "-q", "-m", subject)
        _git(self.local, "push", "-q", "origin", BASE)
        _git(self.local, "checkout", "-q", cur)
        return self


def _seeded_pair(tmp_path, filename, seed_body):
    """A bare remote + clone where `filename` already exists at the merge-base -- what a
    delete/modify conflict (as opposed to World's own no-shared-file branch commit) needs on
    BOTH sides to diverge from."""
    root = tmp_path
    remote = root / "remote.git"
    local = root / "local"
    _git(root, "init", "-q", "--bare", str(remote))
    _git(root, "init", "-q", "-b", BASE, str(local))
    _git(local, "remote", "add", "origin", str(remote))
    _write(local / filename, seed_body)
    _git(local, "add", filename)
    _git(local, "commit", "-q", "-m", "seed")
    _git(local, "push", "-q", "-u", "origin", BASE)
    _git(local, "checkout", "-q", "-b", BRANCH)
    return local


FUNC_SEED = "def helper_function():\n    return 1\n"


def _conflict_world(tmp_path):
    """A single genuine content conflict on `shared.txt`, tree left stopped -- the shape most tests
    below just need without re-deriving it."""
    world = World(tmp_path).build()
    world.commit_on_base("shared.txt", "base changed this line", "chore: base edits shared.txt (#9)")
    cwd = str(world.local)
    _write(world.local / "shared.txt", "branch changed this line\n")
    _git(world.local, "add", "shared.txt")
    _git(world.local, "commit", "-q", "-m", "feat: branch also edits shared.txt")
    _try_rebase(cwd)
    return world, cwd


def _brief_for(world, cwd):
    m = _mod()
    return m.rebase_brief.assemble_brief(_run, cwd, "origin", BRANCH, BASE)


# --------------------------------------------------------------------------- classify (§5.1)


def test_classify_conflict_detects_a_genuine_content_conflict(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    assert m.classify_conflict(_run, cwd, "shared.txt") == m.CONTENT
    _run(cwd, ["git", "rebase", "--abort"])


def test_classify_conflict_detects_deleted_by_us_when_the_base_deletes_it(tmp_path):
    """git's own porcelain code `DU` -- verified against real git: the BASE side (git's raw "us"
    during a rebase -- HEAD is temporarily the base) deleted the file while the branch kept editing
    it, stage 2 absent, stage 3 (the branch's own content) present."""
    m = _mod()
    local = _seeded_pair(tmp_path, "f.py", FUNC_SEED)
    _write(local / "f.py", FUNC_SEED + "    # branch tweak\n")
    _git(local, "commit", "-q", "-am", "feat: branch edits f.py")
    _git(local, "push", "-q", "-u", "origin", BRANCH)
    _git(local, "checkout", "-q", BASE)
    _git(local, "rm", "-q", "f.py")
    _git(local, "commit", "-q", "-m", "chore: base deletes f.py, it moved (#42)")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    cwd = str(local)
    _try_rebase(cwd)
    assert m.classify_conflict(_run, cwd, "f.py") == m.DELETED_BY_US
    text = m.conflict_source_text(_run, cwd, "f.py", m.DELETED_BY_US)
    assert "helper_function" in text
    assert m.extract_symbol(text) == "helper_function"
    _run(cwd, ["git", "rebase", "--abort"])


def test_classify_conflict_detects_deleted_by_them_when_the_branch_deletes_it(tmp_path):
    """git's own porcelain code `UD`: the BRANCH side (git's raw "them" during a rebase -- the
    commit being replayed) deleted the file while the base kept editing it, stage 3 absent,
    stage 2 (the base's own content) present."""
    m = _mod()
    local = _seeded_pair(tmp_path, "f.py", FUNC_SEED)
    _git(local, "rm", "-q", "f.py")
    _git(local, "commit", "-q", "-m", "feat: branch deletes f.py")
    _git(local, "push", "-q", "-u", "origin", BRANCH)
    _git(local, "checkout", "-q", BASE)
    _write(local / "f.py", FUNC_SEED + "    # base tweak\n")
    _git(local, "commit", "-q", "-am", "chore: base edits f.py")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    cwd = str(local)
    _try_rebase(cwd)
    assert m.classify_conflict(_run, cwd, "f.py") == m.DELETED_BY_THEM
    text = m.conflict_source_text(_run, cwd, "f.py", m.DELETED_BY_THEM)
    assert "helper_function" in text
    _run(cwd, ["git", "rebase", "--abort"])


def test_classify_conflict_returns_content_for_a_path_with_no_status_line(tmp_path):
    """No matching porcelain line (e.g. a clean tree, or a wrong path) is not a delete shape --
    conservatively `CONTENT`, never a guess."""
    m = _mod()
    world = World(tmp_path).build()
    assert m.classify_conflict(_run, str(world.local), "seed.txt") == m.CONTENT


# --------------------------------------------------------------------------- symbol extraction (§5.3)


def test_extract_symbol_prefers_a_definition_shaped_line():
    m = _mod()
    text = "some prose\nclass HelperThing:\n    pass\n"
    assert m.extract_symbol(text) == "HelperThing"


def test_extract_symbol_falls_back_to_the_longest_identifier():
    m = _mod()
    assert m.extract_symbol("x = compute_final_result(a, b)") == "compute_final_result"


def test_extract_symbol_is_none_for_text_with_no_identifier():
    m = _mod()
    assert m.extract_symbol("") is None
    assert m.extract_symbol("1 + 2 == 3") is None


def test_conflict_source_text_for_content_conflict_reads_the_marker_region(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    text = m.conflict_source_text(_run, cwd, "shared.txt", m.CONTENT)
    assert "<<<<<<<" in text and ">>>>>>>" in text
    assert "base changed this line" in text
    assert "branch changed this line" in text
    _run(cwd, ["git", "rebase", "--abort"])


# --------------------------------------------------------------------------- follow the move (§5.3-4)


def _moved_symbol_world(tmp_path):
    """The base moved `helper_function` out of `old.py` into `new.py`, then deleted `old.py`,
    while the branch kept editing `old.py` unaware -- a real `deleted-by-us` conflict whose search
    genuinely finds `new.py` (verified manually against real git before being encoded here)."""
    local = _seeded_pair(tmp_path, "old.py", FUNC_SEED)
    _git(local, "checkout", "-q", BASE)
    _write(local / "new.py", "placeholder\n")
    _git(local, "add", "new.py")
    _git(local, "commit", "-q", "-m", "seed new.py too")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    _write(local / "old.py", FUNC_SEED + "    # branch tweak\n")
    _git(local, "commit", "-q", "-am", "feat: branch edits old.py")
    _git(local, "push", "-q", "-u", "origin", BRANCH)
    _git(local, "checkout", "-q", BASE)
    _write(local / "new.py", "placeholder\n" + FUNC_SEED.replace("return 1", "return 2"))
    _git(local, "commit", "-q", "-am", "refactor: move helper_function into new.py")
    _git(local, "rm", "-q", "old.py")
    _git(local, "commit", "-q", "-m", "chore: remove old.py, it moved")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    return local


def test_search_moved_symbol_finds_the_real_destination_excluding_the_conflicting_path(tmp_path):
    m = _mod()
    local = _moved_symbol_world(tmp_path)
    cwd = str(local)
    found = m.search_moved_symbol(_run, cwd, "helper_function", exclude_path="old.py")
    assert "new.py" in found
    assert "old.py" not in found


def test_search_moved_symbol_is_empty_for_no_symbol():
    m = _mod()
    assert m.search_moved_symbol(_run, "/nonexistent", None) == []


def test_plausible_move_candidates_filters_to_files_that_still_exist(tmp_path):
    m = _mod()
    local = _moved_symbol_world(tmp_path)
    cwd = str(local)
    _try_rebase(cwd)
    assert m.classify_conflict(_run, cwd, "old.py") == m.DELETED_BY_US
    assert m.plausible_move_candidates(_run, cwd, "helper_function", "old.py") == ["new.py"]
    _run(cwd, ["git", "rebase", "--abort"])


def test_plausible_move_candidates_empty_when_the_only_hit_no_longer_exists(tmp_path):
    m = _mod()
    local = _moved_symbol_world(tmp_path)
    cwd = str(local)
    _git(local, "checkout", "-q", BASE)
    _run(cwd, ["git", "rm", "-q", "new.py"])
    _git(local, "commit", "-q", "-m", "chore: new.py is gone too")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    _try_rebase(cwd)
    assert m.plausible_move_candidates(_run, cwd, "helper_function", "old.py") == []
    _run(cwd, ["git", "rebase", "--abort"])


def test_plausible_move_candidates_degrades_to_empty_past_the_generic_symbol_cap(tmp_path, monkeypatch):
    m = _mod()
    world = World(tmp_path).build()
    cwd = str(world.local)
    monkeypatch.setattr(m, "search_moved_symbol",
                        lambda run, cwd_, symbol, exclude_path=None: ["a", "b", "c", "d"])
    for name in ("a", "b", "c", "d"):
        _write(world.local / name, "x\n")
    assert m.plausible_move_candidates(_run, cwd, "generic", "seed.txt") == []


def test_file_conflict_state_offers_follow_only_when_a_candidate_was_found(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    brief = _brief_for(world, cwd)
    got = m.file_conflict_state(_run, cwd, brief, "shared.txt")
    assert m.FOLLOW not in got["options"]                       # no other file shares this symbol
    assert got["options"] == (m.RECREATE, m.ABANDON, m.MANUAL)
    assert got["shape"] == m.CONTENT
    _run(cwd, ["git", "rebase", "--abort"])


def test_file_conflict_state_reuses_rebase_briefs_own_decision_context(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    brief = _brief_for(world, cwd)
    got = m.file_conflict_state(_run, cwd, brief, "shared.txt")
    assert got["context"]["commits"][0]["subject"] == "chore: base edits shared.txt (#9)"
    _run(cwd, ["git", "rebase", "--abort"])


# --------------------------------------------------------------------------- apply_option (§5.5)


def test_apply_option_recreate_is_byte_perfect_including_a_missing_trailing_newline(tmp_path):
    """The property the module docstring claims: `git checkout --theirs` writes git's own stored
    blob, so a file with NO trailing newline survives resolution exactly, unlike a `run`-captured
    (and `.strip()`ped) blob would."""
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("shared.txt", "base changed this line", "chore: base edits shared.txt")
    cwd = str(world.local)
    _write(world.local / "shared.txt", b"branch changed this line, no trailing newline")
    _git(world.local, "add", "shared.txt")
    _git(world.local, "commit", "-q", "-m", "feat: branch edits shared.txt with no trailing newline")
    _try_rebase(cwd)
    result = m.apply_option(_run, cwd, "shared.txt", m.RECREATE)
    assert result["action"] == "checked-out-stage-3"
    assert (world.local / "shared.txt").read_bytes() == b"branch changed this line, no trailing newline"
    assert _run(cwd, ["git", "diff", "--name-only", "--diff-filter=U"]) == ""
    _run(cwd, ["git", "rebase", "--continue"])


def test_apply_option_abandon_keeps_the_bases_content(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    result = m.apply_option(_run, cwd, "shared.txt", m.ABANDON)
    assert result["action"] == "checked-out-stage-2"
    assert (world.local / "shared.txt").read_text() == "base changed this line\n"
    _run(cwd, ["git", "rebase", "--continue"])


def test_apply_option_recreate_on_a_branch_deletion_removes_the_file(tmp_path):
    """deleted-by-them: the branch's OWN decision was to delete -- Recreate keeps that decision."""
    m = _mod()
    local = _seeded_pair(tmp_path, "f.py", FUNC_SEED)
    _git(local, "rm", "-q", "f.py")
    _git(local, "commit", "-q", "-m", "feat: branch deletes f.py")
    _git(local, "push", "-q", "-u", "origin", BRANCH)
    _git(local, "checkout", "-q", BASE)
    _write(local / "f.py", FUNC_SEED + "    # base tweak\n")
    _git(local, "commit", "-q", "-am", "chore: base edits f.py")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    cwd = str(local)
    _try_rebase(cwd)
    result = m.apply_option(_run, cwd, "f.py", m.RECREATE)
    assert result["action"] == "removed"
    assert not (local / "f.py").exists()
    assert _run(cwd, ["git", "diff", "--name-only", "--diff-filter=U"]) == ""
    _run(cwd, ["git", "rebase", "--continue"])


def test_apply_option_abandon_on_a_base_deletion_removes_the_file(tmp_path):
    """deleted-by-us: the base's OWN decision was to delete -- Abandon accepts that decision."""
    m = _mod()
    local = _seeded_pair(tmp_path, "f.py", FUNC_SEED)
    _write(local / "f.py", FUNC_SEED + "    # branch tweak\n")
    _git(local, "commit", "-q", "-am", "feat: branch edits f.py")
    _git(local, "push", "-q", "-u", "origin", BRANCH)
    _git(local, "checkout", "-q", BASE)
    _git(local, "rm", "-q", "f.py")
    _git(local, "commit", "-q", "-m", "chore: base deletes f.py")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    cwd = str(local)
    _try_rebase(cwd)
    result = m.apply_option(_run, cwd, "f.py", m.ABANDON)
    assert result["action"] == "removed"
    assert not (local / "f.py").exists()
    _run(cwd, ["git", "rebase", "--continue"])


def test_apply_option_follow_abandons_the_stale_path_and_hands_back_the_branch_content(tmp_path):
    """SAFETY: never auto-writes the candidate destination (module docstring) -- the original path
    is resolved (abandoned there) and the branch's own content is returned for the human to apply
    at `candidate` by hand."""
    m = _mod()
    local = _moved_symbol_world(tmp_path)
    cwd = str(local)
    _try_rebase(cwd)
    result = m.apply_option(_run, cwd, "old.py", m.FOLLOW, candidate="new.py")
    assert result["action"] == "removed"                        # base deleted old.py -- abandoned
    assert result["candidate"] == "new.py"
    assert "helper_function" in result["apply_by_hand_content"]
    assert _run(cwd, ["git", "diff", "--name-only", "--diff-filter=U"]) == ""
    # new.py itself was never touched by apply_option
    assert (local / "new.py").read_text().count("helper_function") == 1
    _run(cwd, ["git", "rebase", "--continue"])


def test_apply_option_follow_requires_a_candidate(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    with pytest.raises(ValueError):
        m.apply_option(_run, cwd, "shared.txt", m.FOLLOW)
    _run(cwd, ["git", "rebase", "--abort"])


def test_apply_option_manual_touches_nothing(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    result = m.apply_option(_run, cwd, "shared.txt", m.MANUAL)
    assert result["action"] == "manual"
    assert "shared.txt" in _run(cwd, ["git", "diff", "--name-only", "--diff-filter=U"])
    _run(cwd, ["git", "rebase", "--abort"])


def test_apply_option_rejects_an_unknown_option(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    with pytest.raises(ValueError):
        m.apply_option(_run, cwd, "shared.txt", "not-a-real-option")
    _run(cwd, ["git", "rebase", "--abort"])


# --------------------------------------------------------------------------- walk_conflicts (§5)


def test_walk_conflicts_reports_nothing_to_do_when_no_rebase_is_stopped(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    cwd = str(world.local)
    brief = _brief_for(world, cwd)
    report = m.walk_conflicts(_run, cwd, brief, lambda s: {"option": m.RECREATE}, "origin")
    assert report == {"outcome": m.NOTHING_TO_DO, "resolved": []}


def test_walk_conflicts_resolves_a_single_conflict_to_done(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    brief = _brief_for(world, cwd)
    report = m.walk_conflicts(_run, cwd, brief, lambda s: {"option": m.RECREATE}, "origin")
    assert report["outcome"] == m.DONE, report
    assert len(report["resolved"]) == 1
    assert m.feature_rebase.rebase_stopped(_run, cwd) is False
    assert _run(cwd, ["git", "status", "--porcelain"]) == ""
    assert (world.local / "shared.txt").read_text() == "branch changed this line\n"


def test_walk_conflicts_aborts_and_leaves_the_tree_exactly_as_it_was(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    before_base = world_tip(cwd, BASE)
    before_branch = world_tip(cwd, BRANCH)
    brief = _brief_for(world, cwd)
    report = m.walk_conflicts(_run, cwd, brief, lambda s: {"option": m.ABORT}, "origin")
    assert report["outcome"] == m.ABORTED
    assert m.feature_rebase.rebase_stopped(_run, cwd) is False
    assert _run(cwd, ["git", "status", "--porcelain"]) == ""
    assert world_tip(cwd, BASE) == before_base
    assert world_tip(cwd, BRANCH) == before_branch


def world_tip(cwd, ref):
    return _run(cwd, ["git", "rev-parse", ref])


def test_walk_conflicts_rejects_an_option_not_offered_for_that_file(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    with pytest.raises(ValueError):
        m.walk_conflicts(_run, cwd, _brief_for(world, cwd), lambda s: {"option": m.FOLLOW}, "origin")
    _run(cwd, ["git", "rebase", "--abort"])


def test_walk_conflicts_manual_option_re_asks_until_the_file_is_actually_staged(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    brief = _brief_for(world, cwd)
    calls = []

    def decide(conflict_state):
        calls.append(conflict_state["path"])
        if len(calls) == 1:
            return {"option": m.MANUAL}
        # simulate the human having resolved + staged it by hand, in between decide() calls --
        # exactly what a real interactive CLI's own `input()` wait guarantees has already happened
        # by the time it returns (module docstring)
        _write(world.local / "shared.txt", "manually resolved\n")
        _git(world.local, "add", "shared.txt")
        return {"option": m.MANUAL}

    report = m.walk_conflicts(_run, cwd, brief, decide, "origin")
    assert report["outcome"] == m.DONE, report
    assert len(calls) == 2
    assert (world.local / "shared.txt").read_text() == "manually resolved\n"


def test_walk_conflicts_repeats_when_a_second_commit_conflicts_again(tmp_path):
    """Design §5's own "repeating if a later commit hits another conflict": two branch commits,
    each independently conflicting with what the base did to the SAME two files -- resolving the
    first and continuing must surface the second, not silently stop."""
    root = tmp_path
    remote = root / "remote.git"
    local = root / "local"
    _git(root, "init", "-q", "--bare", str(remote))
    _git(root, "init", "-q", "-b", BASE, str(local))
    _git(local, "remote", "add", "origin", str(remote))
    _write(local / "a.txt", "seed-a\n")
    _write(local / "b.txt", "seed-b\n")
    _git(local, "add", "a.txt", "b.txt")
    _git(local, "commit", "-q", "-m", "seed")
    _git(local, "push", "-q", "-u", "origin", BASE)
    _git(local, "checkout", "-q", "-b", BRANCH)
    _write(local / "a.txt", "branch-a\n")
    _git(local, "commit", "-q", "-am", "feat: branch edits a")
    _write(local / "b.txt", "branch-b\n")
    _git(local, "commit", "-q", "-am", "feat: branch edits b")
    _git(local, "push", "-q", "-u", "origin", BRANCH)
    _git(local, "checkout", "-q", BASE)
    _write(local / "a.txt", "base-a\n")
    _git(local, "commit", "-q", "-am", "chore: base edits a")
    _write(local / "b.txt", "base-b\n")
    _git(local, "commit", "-q", "-am", "chore: base edits b")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    cwd = str(local)
    m = _mod()
    brief = m.rebase_brief.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    _try_rebase(cwd)
    assert m.feature_rebase.rebase_stopped(_run, cwd) is True
    seen_paths = []

    def decide(conflict_state):
        seen_paths.append(conflict_state["path"])
        return {"option": m.RECREATE}

    report = m.walk_conflicts(_run, cwd, brief, decide, "origin")
    assert report["outcome"] == m.DONE, report
    assert seen_paths == ["a.txt", "b.txt"]
    assert len(report["resolved"]) == 2
    assert (local / "a.txt").read_text() == "branch-a\n"
    assert (local / "b.txt").read_text() == "branch-b\n"


# --------------------------------------------------------------------------- #2318/#2319/#2320:
# the three real safety gaps a live dummy-repo validation of epic #2303 found, that 40+ prior
# unit tests never caught -- see each test's own docstring for what specifically was blind before.


def test_walk_conflicts_refuses_a_continue_that_would_silently_drop_the_commit(tmp_path):
    """#2318, THE most important test in this file. `_moved_symbol_world`'s own branch commit
    ("feat: branch edits old.py") touches ONLY `old.py`. FOLLOW's stale-path resolution resolves
    `old.py` to stage 2 -- which for this `deleted-by-us` shape has no entry at all, so it becomes
    `git rm -f old.py` (module docstring: "the honest resolution is the deletion itself"). The base
    ALSO already deleted `old.py`. So after this resolution, the replayed commit's entire diff
    against its new parent is empty -- exactly #2318's own trigger.

    VERIFIED AGAINST REAL, UNPATCHED GIT BEHAVIOUR before trusting this test at all (git 2.49, the
    identical fixture, reproduced by hand outside this suite): a bare `git rebase --continue` at
    this exact point exits 0 with `stdout==""` and `stderr=="Successfully rebased and updated
    refs/heads/feature/x."` -- NO "previous cherry-pick is now empty" message on either stream --
    and the branch becomes byte-identical to `main`, the commit gone with zero trace. A test that
    only asserted "no exception was raised" and "`report['outcome']` looks like success" would have
    been exactly as blind to this as `git rebase --continue` itself, and as `walk_conflicts` was
    before this fix (the pre-existing `test_apply_option_follow_abandons_the_stale_path_and_hands_
    back_the_branch_content` test above calls `git rebase --continue` on this SAME shape and never
    checks whether the commit survived -- proof this exact blind spot already existed in this
    suite). So every assertion below reads git's OWN state -- the branch ref, the commit's own
    reachability and message -- never just the tool's own printed outcome."""
    m = _mod()
    local = _moved_symbol_world(tmp_path)
    cwd = str(local)
    _try_rebase(cwd)
    assert m.classify_conflict(_run, cwd, "old.py") == m.DELETED_BY_US
    original_branch_tip = _run(cwd, ["git", "rev-parse", BRANCH])
    original_commit = _run(cwd, ["git", "rev-parse", "REBASE_HEAD"])
    assert _run(cwd, ["git", "log", "-1", "--format=%s", original_commit]) == \
        "feat: branch edits old.py"

    brief = m.rebase_brief.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    report = m.walk_conflicts(_run, cwd, brief,
                              lambda s: {"option": m.FOLLOW, "candidate": "new.py"}, "origin")

    # the walk must NEVER report this as a success -- that is exactly #2318's silent-drop lie
    assert report["outcome"] == m.EMPTY_AFTER_RESOLVE, report
    assert report["outcome"] != m.DONE
    assert "silently DROP" in report["why"]
    # left stopped -- NOT continued, NOT aborted -- nothing has been rewritten yet
    assert m.feature_rebase.rebase_stopped(_run, cwd) is True
    # the branch's own ref never moved: git does not touch it until the WHOLE rebase finishes
    assert _run(cwd, ["git", "rev-parse", BRANCH]) == original_branch_tip
    # the at-risk commit is still fully intact and reachable, its own message untouched
    assert _run(cwd, ["git", "rev-parse", "--verify", original_commit]) == original_commit
    assert _run(cwd, ["git", "log", "-1", "--format=%s", original_commit]) == \
        "feat: branch edits old.py"
    # the FOLLOW resolution's own recovery content (#2320) was still captured before the refusal
    assert len(report["resolved"]) == 1
    assert report["resolved"][0]["candidate"] == "new.py"
    assert "helper_function" in report["resolved"][0]["apply_by_hand_content"]
    _run(cwd, ["git", "rebase", "--abort"])


def test_walk_conflicts_pushes_the_branch_once_every_conflict_is_resolved(tmp_path):
    """#2319: `rebase_brief.py`'s clean path was the ONLY force-with-lease push in this whole
    skill (confirmed by grep across all three agrim-rebase scripts) -- `walk_conflicts` itself never
    pushed, so a human following the documented `conflict_walk.py walk` -> `verify_merge.py land`
    flow hit a landing PR against the STALE, un-rebased remote branch. Asserts on the real remote
    via `git ls-remote`, never the tool's own printed text."""
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    brief = _brief_for(world, cwd)
    before = _run(cwd, ["git", "ls-remote", "origin", "refs/heads/%s" % BRANCH]).split()[0]
    report = m.walk_conflicts(_run, cwd, brief, lambda s: {"option": m.RECREATE}, "origin")
    assert report["outcome"] == m.DONE, report
    after = _run(cwd, ["git", "ls-remote", "origin", "refs/heads/%s" % BRANCH]).split()[0]
    assert after != before
    assert after == _run(cwd, ["git", "rev-parse", BRANCH])


def test_main_prints_apply_by_hand_content_for_a_follow_resolution(tmp_path, monkeypatch, capsys):
    """#2320: `apply_option`'s FOLLOW case has always returned `apply_by_hand_content` in its
    result dict (per its own docstring), but `main()` never read `report['resolved']` at all -- a
    real human running `walk` saw only the final outcome line, with zero indication a file's
    content still needed manual re-application at its own candidate destination.

    Reuses the exact #2318 scenario above (deliberately -- it is the design's own motivating
    example, and it is what the real dummy-repo validation actually hit): now that the empty
    commit is refused rather than silently dropped, this printed content is the ONLY surviving
    trace of `old.py`'s branch edit a human running the CLI can see without already knowing to
    read git's own `REBASE_HEAD` -- so it must reach stdout on this outcome too, not only on a
    clean `DONE`."""
    m = _mod()
    local = _moved_symbol_world(tmp_path)
    _try_rebase(str(local))
    sdlc = local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text('{"work": {"base": "%s", "remote": "origin"}}' % BASE,
                                      encoding="utf-8")
    answers = iter(["2"])                  # "2" = Follow the move (the only candidate offered)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    rc = m.main(["conflict_walk.py", "walk", str(sdlc)])
    out = capsys.readouterr().out
    assert rc == 1                          # EMPTY_AFTER_RESOLVE -- see #2318's own test above
    assert "re-apply" in out
    assert "new.py" in out
    assert "helper_function" in out
    _run(str(local), ["git", "rebase", "--abort"])


# --------------------------------------------------------------------------- resumability


def test_rebase_original_branch_reads_head_name_while_head_is_detached(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    assert _run(cwd, ["git", "rev-parse", "--abbrev-ref", "HEAD"]) == "HEAD"     # verified: detached
    assert m._rebase_original_branch(_run, cwd) == BRANCH
    _run(cwd, ["git", "rebase", "--abort"])


def test_rebase_original_branch_is_none_when_nothing_is_stopped(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    assert m._rebase_original_branch(_run, str(world.local)) is None


def test_resolve_branch_explicit_always_wins():
    m = _mod()
    assert m.resolve_branch(lambda *a: "", "/x", "explicit-branch") == "explicit-branch"


def test_resolve_branch_prefers_the_stopped_rebases_own_original_branch(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    assert m.resolve_branch(_run, cwd) == BRANCH
    _run(cwd, ["git", "rebase", "--abort"])


def test_resolve_branch_falls_back_to_the_current_checkout_when_nothing_is_stopped(tmp_path):
    m = _mod()
    world = World(tmp_path).build()
    assert m.resolve_branch(_run, str(world.local)) == BRANCH


# --------------------------------------------------------------------------- the interactive CLI


def test_main_reports_nothing_to_walk_when_no_rebase_is_stopped(tmp_path, capsys):
    m = _mod()
    world = World(tmp_path).build()
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text('{"work": {"base": "%s", "remote": "origin"}}' % BASE,
                                      encoding="utf-8")
    rc = m.main(["conflict_walk.py", "walk", str(sdlc)])
    assert rc == 0
    assert "nothing to walk" in capsys.readouterr().out


def test_main_refuses_cleanly_when_no_base_is_configured(tmp_path):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text('{"work": {"remote": "origin"}}', encoding="utf-8")
    rc = m.main(["conflict_walk.py", "walk", str(sdlc)])
    assert rc == 1
    _run(cwd, ["git", "rebase", "--abort"])


def test_main_walk_resolves_the_conflict_end_to_end_via_input(tmp_path, monkeypatch, capsys):
    """The real interactive CLI, driven by an injected `input()` -- proves `main()`,
    `_interactive_decide` and `_print_conflict_state` all actually work together, not just
    `walk_conflicts` in isolation."""
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text('{"work": {"base": "%s", "remote": "origin"}}' % BASE,
                                      encoding="utf-8")
    answers = iter(["1"])                  # "1" = Recreate here
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    rc = m.main(["conflict_walk.py", "walk", str(sdlc)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "shared.txt" in out
    assert "now sits cleanly" in out
    assert (world.local / "shared.txt").read_text() == "branch changed this line\n"


def test_main_walk_can_be_told_to_abort(tmp_path, monkeypatch, capsys):
    m = _mod()
    world, cwd = _conflict_world(tmp_path)
    sdlc = world.local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text('{"work": {"base": "%s", "remote": "origin"}}' % BASE,
                                      encoding="utf-8")
    answers = iter(["abort"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    rc = m.main(["conflict_walk.py", "walk", str(sdlc)])
    assert rc == 0
    assert "Aborted" in capsys.readouterr().out
    assert m.feature_rebase.rebase_stopped(_run, cwd) is False


def test_interactive_decide_reprompts_on_an_invalid_choice_then_accepts_follow(tmp_path, monkeypatch):
    m = _mod()
    local = _moved_symbol_world(tmp_path)
    cwd = str(local)
    _try_rebase(cwd)
    brief = m.rebase_brief.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    conflict_state = m.file_conflict_state(_run, cwd, brief, "old.py")
    assert m.FOLLOW in conflict_state["options"]
    answers = iter(["nonsense", "2"])       # invalid, then "2" = Follow the move
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    decision = m._interactive_decide(conflict_state)
    assert decision == {"option": m.FOLLOW, "candidate": "new.py"}
    _run(cwd, ["git", "rebase", "--abort"])


def test_usage_is_reported_for_a_bad_invocation(capsys):
    m = _mod()
    rc = m.main(["conflict_walk.py"])
    assert rc == 2
    assert "usage" in capsys.readouterr().err


# --------------------------------------------------------------------------- #2321/#2322/#2324:
# three real, narrow gaps a live dummy-repo validation of the already-shipped Epic #2303 found --
# none data-loss-severity (that one, #2318, is already fixed above), but each a real limitation.


def test_conflict_walk_reuses_the_snapshot_taken_at_first_detection_even_after_the_base_moves(
        tmp_path):
    """#2321, THE single most important test in this file: reproduces the real timing window, not
    just the mechanism. `rebase_brief.py rebase` detects a conflict on `shared.txt` (caused by a
    base commit that MODIFIES it, referencing issue #9) and snapshots its decision-context. The
    base then genuinely ADVANCES -- a SECOND actor pushes a commit that DELETES `shared.txt`,
    referencing an unrelated issue #77 -- before `conflict_walk.py walk` (a later, separate
    process) ever resolves it.

    Proven two ways: first, that re-deriving `file_context` fresh against the now-moved base really
    would describe the wrong commit (`deleted_on_base=True`, issue #77) -- this is #2321's own real
    reproduction ("the context text cited an unrelated file-deletion commit") made concrete, not
    assumed. Second, that `file_conflict_state` -- what `conflict_walk.py walk` actually calls --
    still describes the ORIGINAL conflicting commit (issue #9, a content edit, not a deletion),
    because the snapshot from first detection was reused rather than re-derived.

    A SEPARATE clone pushes the base's later commit, not `world.local` itself: verified against
    real git that a plain `git checkout` is REFUSED ("you need to resolve your current index
    first") while a tree is genuinely mid-conflict, so the branch that is actually stopped can
    never be the one used to advance the base -- exactly why this is a two-actor scenario in real
    life too."""
    m = _mod()
    world = World(tmp_path).build()
    world.commit_on_base("shared.txt", "base changed this line",
                         "chore: base edits shared.txt (#9)")
    cwd = str(world.local)
    _write(world.local / "shared.txt", "branch changed this line\n")
    _git(world.local, "add", "shared.txt")
    _git(world.local, "commit", "-q", "-m", "feat: branch also edits shared.txt")

    sdlc = world.local / ".sdlc"
    sdlc.mkdir()

    # process 1, first detection: rebase_brief.py rebase's own flow
    brief = m.rebase_brief.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    report = m.rebase_brief.attempt_rebase(_run, cwd, "origin", BRANCH, BASE)
    assert report["outcome"] == m.rebase_brief.CONFLICT, report
    assert report["files"] == ["shared.txt"]
    m.rebase_brief.format_conflict(brief, report, _run, cwd, str(sdlc))     # persists the snapshot

    # the base genuinely advances while the conflict sits unresolved -- a second actor, since
    # world.local cannot checkout while mid-conflict (verified against real git, see docstring)
    other = world.root / "other"
    _git(world.root, "clone", "-q", str(world.remote), str(other))
    _git(other, "checkout", "-q", BASE)
    _git(other, "rm", "-q", "shared.txt")
    _git(other, "commit", "-q", "-m", "chore: remove shared.txt, refactored away (#77)")
    _git(other, "push", "-q", "origin", BASE)

    # process 2, later and separate: conflict_walk.py walk's own flow -- re-fetches, so its own
    # brief genuinely reflects the moved base
    later_brief = m.rebase_brief.assemble_brief(_run, cwd, "origin", BRANCH, BASE)

    # prove the base really did move, and that re-deriving fresh against it WOULD show the wrong
    # commit -- the exact bug #2321 reproduces, not merely asserted
    stale = m.rebase_brief.file_context(_run, cwd, later_brief["merge_base"],
                                        later_brief["base_ref"], "shared.txt",
                                        later_brief["changelog_entries"])
    assert stale["deleted_on_base"] is True
    assert "(#77)" in stale["commits"][0]["subject"]

    # the fix: file_conflict_state (conflict_walk.py's own entry point) still describes the
    # ORIGINAL conflict, snapshotted at first detection -- never the now-stale live re-derivation
    got = m.file_conflict_state(_run, cwd, later_brief, "shared.txt", str(sdlc))
    ctx = got["context"]
    assert ctx["deleted_on_base"] is False
    assert len(ctx["commits"]) == 1
    assert "(#9)" in ctx["commits"][0]["subject"]
    assert all("(#77)" not in c["subject"] for c in ctx["commits"])

    _run(cwd, ["git", "rebase", "--abort"])


def test_walk_conflicts_captures_each_conflicting_files_own_context_independently(tmp_path):
    """#2321's second requirement: the snapshot store is NOT a one-shot cache for the whole
    rebase. Two branch commits independently conflict against two different base commits on TWO
    different files (mirroring `test_walk_conflicts_repeats_when_a_second_commit_conflicts_again`'s
    own shape) -- the second, later conflict must get its own, correctly-current context, never a
    leftover copy of the first file's entry."""
    root = tmp_path
    remote = root / "remote.git"
    local = root / "local"
    _git(root, "init", "-q", "--bare", str(remote))
    _git(root, "init", "-q", "-b", BASE, str(local))
    _git(local, "remote", "add", "origin", str(remote))
    _write(local / "a.txt", "seed-a\n")
    _write(local / "b.txt", "seed-b\n")
    _git(local, "add", "a.txt", "b.txt")
    _git(local, "commit", "-q", "-m", "seed")
    _git(local, "push", "-q", "-u", "origin", BASE)
    _git(local, "checkout", "-q", "-b", BRANCH)
    _write(local / "a.txt", "branch-a\n")
    _git(local, "commit", "-q", "-am", "feat: branch edits a")
    _write(local / "b.txt", "branch-b\n")
    _git(local, "commit", "-q", "-am", "feat: branch edits b")
    _git(local, "push", "-q", "-u", "origin", BRANCH)
    _git(local, "checkout", "-q", BASE)
    _write(local / "a.txt", "base-a\n")
    _git(local, "commit", "-q", "-am", "chore: base edits a (#11)")
    _write(local / "b.txt", "base-b\n")
    _git(local, "commit", "-q", "-am", "chore: base edits b (#22)")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    cwd = str(local)
    m = _mod()
    sdlc = local / ".sdlc"
    sdlc.mkdir()
    brief = m.rebase_brief.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    _try_rebase(cwd)
    seen = {}

    def decide(conflict_state):
        seen[conflict_state["path"]] = conflict_state["context"]
        return {"option": m.RECREATE}

    report = m.walk_conflicts(_run, cwd, brief, decide, "origin", str(sdlc))
    assert report["outcome"] == m.DONE, report
    assert set(seen) == {"a.txt", "b.txt"}
    assert "(#11)" in seen["a.txt"]["commits"][0]["subject"]
    assert "(#22)" in seen["b.txt"]["commits"][0]["subject"]
    assert seen["a.txt"]["commits"][0]["subject"] != seen["b.txt"]["commits"][0]["subject"]
    # the rebase concluded cleanly -- the per-branch store is cleared, nothing left to grow
    store_path = sdlc / "state" / "rebase-context" / "feature_x.json"
    assert not store_path.exists()


def test_plausible_move_candidates_excludes_a_changelog_mention_from_the_preview_slot(tmp_path):
    """#2322: a CHANGELOG.md entry that merely MENTIONS the symbol in prose is a false-positive
    "follow the move" candidate. `search_moved_symbol`'s own raw `git log -S` hit legitimately
    finds BOTH the doc mention and the real code destination -- and, in this fixture, the doc
    mention is the NEWER commit, so it would win `candidates[0]`, the exact slot
    `_print_conflict_state` previews -- but `plausible_move_candidates` must filter it out before
    ranking, so only the genuine code destination survives."""
    m = _mod()
    local = _seeded_pair(tmp_path, "old.py", FUNC_SEED)
    _git(local, "checkout", "-q", BASE)
    _write(local / "new.py", "placeholder\n" + FUNC_SEED.replace("return 1", "return 2"))
    _git(local, "add", "new.py")
    _git(local, "commit", "-q", "-m", "seed new.py too")
    _write(local / "CHANGELOG.md", "### Refactor\n\nMoved `helper_function` into new.py.\n")
    _git(local, "add", "CHANGELOG.md")
    _git(local, "commit", "-q", "-m", "docs: note the move of helper_function")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    _write(local / "old.py", FUNC_SEED + "    # branch tweak\n")
    _git(local, "commit", "-q", "-am", "feat: branch edits old.py")
    _git(local, "push", "-q", "-u", "origin", BRANCH)
    _git(local, "checkout", "-q", BASE)
    _git(local, "rm", "-q", "old.py")
    _git(local, "commit", "-q", "-m", "chore: remove old.py, it moved")
    _git(local, "push", "-q", "origin", BASE)
    _git(local, "checkout", "-q", BRANCH)
    cwd = str(local)
    _try_rebase(cwd)          # `new.py` only EXISTS on the base side -- rebase's ours/theirs
                              # inversion (module docstring) is what puts it in the working tree

    # the raw search genuinely finds both -- and the doc mention is newest, so it would win the
    # preview slot unfiltered (proving this is a real reproduction, not an assumed one)
    raw = m.search_moved_symbol(_run, cwd, "helper_function", exclude_path="old.py")
    assert raw and raw[0] == "CHANGELOG.md"
    assert "new.py" in raw

    candidates = m.plausible_move_candidates(_run, cwd, "helper_function", "old.py")
    assert candidates == ["new.py"]
    assert "CHANGELOG.md" not in candidates
    _run(cwd, ["git", "rebase", "--abort"])


def test_walk_conflicts_pushes_after_a_manual_recovery_concludes_the_rebase_outside_the_tool(
        tmp_path):
    """#2324: after `_empty_commit_about_to_land`'s own refusal (#2318, `EMPTY_AFTER_RESOLVE`), a
    human recovers via a RAW git escape hatch (`git rebase --skip`, run outside
    `walk_conflicts`'s own loop entirely) -- nothing pushes as a direct result of that command.
    Re-running `walk_conflicts` afterward (a later, separate call, matching `conflict_walk.py
    walk`'s own re-invocation) must recognize this exact state and push, asserted on the REAL
    remote via `git ls-remote`, never just the tool's own printed/returned text."""
    m = _mod()
    local = _moved_symbol_world(tmp_path)
    cwd = str(local)
    _try_rebase(cwd)
    brief = m.rebase_brief.assemble_brief(_run, cwd, "origin", BRANCH, BASE)
    report = m.walk_conflicts(_run, cwd, brief,
                              lambda s: {"option": m.FOLLOW, "candidate": "new.py"}, "origin")
    assert report["outcome"] == m.EMPTY_AFTER_RESOLVE, report

    # the human's own raw escape hatch -- entirely outside walk_conflicts's own loop
    _run(cwd, ["git", "rebase", "--skip"])
    assert m.feature_rebase.rebase_stopped(_run, cwd) is False
    assert m._rebase_just_concluded_locally(_run, cwd) is True

    before = _run(cwd, ["git", "ls-remote", "origin", "refs/heads/%s" % BRANCH]).split()[0]
    report2 = m.walk_conflicts(_run, cwd, brief, lambda s: {"option": m.RECREATE}, "origin")
    assert report2["outcome"] == m.NOTHING_TO_DO
    assert report2["pushed"] is True
    after = _run(cwd, ["git", "ls-remote", "origin", "refs/heads/%s" % BRANCH]).split()[0]
    assert after != before
    assert after == _run(cwd, ["git", "rev-parse", BRANCH])


def test_walk_conflicts_does_not_push_spuriously_when_never_mid_rebase(tmp_path):
    """#2324's own caution: a branch genuinely AHEAD of `remote` for a totally unrelated, ordinary
    reason (a plain local commit, never a rebase) must NOT get pushed just because it happens to be
    ahead -- only a rebase that ACTUALLY concluded locally (git's own reflog says so) earns the
    auto-push. Proves the ahead-of-remote check alone is not sufficient (this branch IS ahead) and
    that the reflog gate is what correctly refuses here."""
    m = _mod()
    world = World(tmp_path).build()
    cwd = str(world.local)
    _write(world.local / "unrelated.txt", "just an ordinary commit\n")
    _git(world.local, "add", "unrelated.txt")
    _git(world.local, "commit", "-q", "-m", "feat: ordinary unrelated work, never pushed")
    assert m._rebase_just_concluded_locally(_run, cwd) is False

    before = _run(cwd, ["git", "ls-remote", "origin", "refs/heads/%s" % BRANCH]).split()[0]
    brief = _brief_for(world, cwd)
    report = m.walk_conflicts(_run, cwd, brief, lambda s: {"option": m.RECREATE}, "origin")
    assert report == {"outcome": m.NOTHING_TO_DO, "resolved": []}
    after = _run(cwd, ["git", "ls-remote", "origin", "refs/heads/%s" % BRANCH]).split()[0]
    assert after == before


def test_main_pushes_after_a_manual_recovery_concludes_the_rebase_outside_the_tool(
        tmp_path, monkeypatch, capsys):
    """The CLI-level counterpart to `test_walk_conflicts_pushes_after_a_manual_recovery_...` --
    proves `main()`'s own restructured NOTHING_TO_DO branch (remote/branch resolved before the
    stopped-rebase check, #2324) actually wires the recovery push through, not just
    `walk_conflicts` in isolation."""
    m = _mod()
    local = _moved_symbol_world(tmp_path)
    _try_rebase(str(local))
    sdlc = local / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text('{"work": {"base": "%s", "remote": "origin"}}' % BASE,
                                      encoding="utf-8")
    answers = iter(["2"])                  # "2" = Follow the move (the only candidate offered)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    rc = m.main(["conflict_walk.py", "walk", str(sdlc)])
    assert rc == 1                          # EMPTY_AFTER_RESOLVE

    _run(str(local), ["git", "rebase", "--skip"])
    assert m.feature_rebase.rebase_stopped(_run, str(local)) is False

    before = _run(str(local), ["git", "ls-remote", "origin", "refs/heads/%s" % BRANCH]).split()[0]
    rc2 = m.main(["conflict_walk.py", "walk", str(sdlc)])
    out = capsys.readouterr().out
    assert rc2 == 0
    assert "pushed" in out.lower()
    after = _run(str(local), ["git", "ls-remote", "origin", "refs/heads/%s" % BRANCH]).split()[0]
    assert after != before
    assert after == _run(str(local), ["git", "rev-parse", BRANCH])

"""#698: launch hardening 2/2. (f) the plan-gate exemption is judged on the repo-relative path and the
completion gate reads non-ASCII names (#590); (g) `sync.init` never drops unpushed ops commits (#592);
(h) goal-file writes are atomic (#592, #634); (i) README claims match the code.

Tests named `..._still_...` or `..._keeps_...` are PINS: they pass before the fix by design. The red
controls are the docs-ancestor, non-ASCII, ahead/diverged/failing-fetch and publish-failure tests."""
import importlib.util
import json
import os
import pathlib
import subprocess

import test_completion_gate as tcg
import test_plan_gate as tpg

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sigma-loop" / "scripts"


def _mod(name, base=S):
    spec = importlib.util.spec_from_file_location(name, base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------------------ (f) plan gate


def test_plan_gate_denies_a_source_edit_when_the_project_lives_under_a_docs_directory(tmp_path):
    proj = tmp_path / "docs" / "proj"
    proj.mkdir(parents=True)
    tpg._project(proj)
    out = tpg._run(proj, str(proj / "src" / "a.py"))
    assert "deny" in out and "no fresh plan" in tpg._deny_reason(out)


def test_plan_gate_still_exempts_the_projects_own_docs_and_sdlc_paths(tmp_path):
    tpg._project(tmp_path)
    for rel in ("docs/x.py", ".sdlc/work/9/src/a.py", "deep/docs/tools/build.py", "a/.sdlc/x.py"):
        assert tpg._run(tmp_path, str(tmp_path / rel)) == "", rel


def test_plan_gate_keeps_the_any_depth_docs_exemption_and_handles_relative_paths(tmp_path):
    tpg._project(tmp_path)
    assert tpg._run(tmp_path, "docs/guide.py") == ""
    assert tpg._run(tmp_path, "packages/x/docs/foo.py") == ""
    assert "deny" in tpg._run(tmp_path, "src/app.py")
    # a trailing slash on the project path changes nothing
    assert tpg._run(str(tmp_path) + "/", str(tmp_path / "docs" / "x.py")) == ""
    assert "deny" in tpg._run(str(tmp_path) + "/", str(tmp_path / "src" / "a.py"))


def test_plan_gate_gates_a_goal_worktree_when_it_is_the_project_and_tracks_its_config(tmp_path):
    """Stated outcome of the #590 fix: with the worktree itself as the project (an adopter repo that
    tracks `.sdlc/config.json`), source edits are gated; from the main root the same file under
    `.sdlc/work/<n>/` stays exempt."""
    root = tmp_path / "root"
    root.mkdir()
    tpg._project(root)
    wt = root / ".sdlc" / "work" / "7"
    wt.mkdir(parents=True)
    tpg._project(wt)
    assert tpg._run(root, str(wt / "src" / "a.py")) == ""
    assert "deny" in tpg._run(wt, str(wt / "src" / "a.py"))


# ------------------------------------------------------------------------- (f) completion gate


def test_completion_gate_blocks_on_a_non_ascii_source_filename(tmp_path):
    tcg._git(tmp_path, "init", "-q")
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"gates":{"stop_gate":{"enabled":true}}}')
    (tmp_path / "é.py").write_text("x = 1\n")
    assert tcg._is_block(tcg._run(tmp_path)) is True


def _git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr or proc.stdout}"
    return proc.stdout.strip()


def _repo_with_origin(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(repo, "config", k, v)
    _git(repo, "remote", "add", "origin", str(origin))
    (repo / "README.txt").write_text("x\n")
    _git(repo, "add", "--", "README.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "push", "-q", "origin", "main")
    return repo, origin


def test_branch_touches_source_sees_a_non_ascii_source_filename(tmp_path):
    work = _mod("work")
    repo, _origin = _repo_with_origin(tmp_path)
    _git(repo, "checkout", "-q", "-b", "sdlc/1")
    (repo / "é.py").write_text("x = 1\n")
    _git(repo, "add", "--", "é.py")
    _git(repo, "commit", "-q", "-m", "non-ascii source")
    rec = {"worktree": str(repo), "remote": "origin", "base": "main"}
    assert work._branch_touches_source(rec, work._run) is True


# ------------------------------------------------------------------------------------ (g) sync


def _ops_fixture(tmp_path, local_extra, remote_extra):
    """A clone whose `sdlc-ledger` branch exists locally and on origin. `local_extra`/`remote_extra`
    are the number of commits each side holds beyond the shared root. Returns (base, repo, tips)."""
    repo, origin = _repo_with_origin(tmp_path)
    ops = "sdlc-ledger"
    _git(repo, "branch", ops)
    _git(repo, "push", "-q", "origin", ops)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(other, "config", k, v)
    _git(other, "checkout", "-q", ops)
    for i in range(remote_extra):
        (other / f"r{i}.txt").write_text("r\n")
        _git(other, "add", "--", f"r{i}.txt")
        _git(other, "commit", "-q", "-m", f"remote {i}")
    if remote_extra:
        _git(other, "push", "-q", "origin", ops)
    _git(repo, "checkout", "-q", ops)
    for i in range(local_extra):
        (repo / f"l{i}.txt").write_text("l\n")
        _git(repo, "add", "--", f"l{i}.txt")
        _git(repo, "commit", "-q", "-m", f"local {i}")
    _git(repo, "checkout", "-q", "main")
    base = repo / ".sdlc"
    base.mkdir(exist_ok=True)
    (base / "config.json").write_text(json.dumps({"ledger": {"enabled": True, "actor": "dana"}}))
    (repo / ".gitignore").write_text(".sdlc/\n")
    return base, repo, ops


CONFIG = {"ledger": {"enabled": True, "actor": "dana"}}


def _contains(repo, tip, commit):
    """Is `commit` still reachable from `tip`? (init adds its own scaffold commit on top, so tips
    are compared by ancestry, never by equality.)"""
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, tip],
                          capture_output=True).returncode == 0


def test_sync_init_keeps_unpushed_commits_on_the_local_ops_branch(tmp_path, capsys):
    sync = _mod("sync")
    base, repo, ops = _ops_fixture(tmp_path, local_extra=2, remote_extra=1)
    # `local_extra` forks from the same root as the remote's extra commit: ahead AND behind
    before = _git(repo, "rev-parse", ops)
    sync.init(base, CONFIG)
    assert _contains(repo, ops, before)
    assert ops in capsys.readouterr().err


def test_sync_init_leaves_a_diverged_ops_branch_alone_and_creates_a_missing_one(tmp_path):
    sync = _mod("sync")
    base, repo, ops = _ops_fixture(tmp_path, local_extra=1, remote_extra=0)
    before = _git(repo, "rev-parse", ops)
    sync.init(base, CONFIG)
    assert _contains(repo, ops, before)                       # ahead only
    tmp2 = tmp_path / "second"
    tmp2.mkdir()
    base2, repo2, ops2 = _ops_fixture(tmp2, local_extra=0, remote_extra=1)
    _git(repo2, "branch", "-q", "-D", ops2)                    # local branch absent, remote present
    sync.init(base2, CONFIG)
    assert _contains(repo2, ops2, _git(repo2, "rev-parse", f"origin/{ops2}"))


def test_sync_init_still_fast_forwards_a_branch_that_is_only_behind(tmp_path):
    sync = _mod("sync")
    base, repo, ops = _ops_fixture(tmp_path, local_extra=0, remote_extra=2)
    sync.init(base, CONFIG)
    assert _contains(repo, ops, _git(repo, "rev-parse", f"origin/{ops}"))


def test_sync_init_with_a_failing_fetch_keeps_an_existing_local_ops_branch(tmp_path):
    sync = _mod("sync")
    base, repo, ops = _ops_fixture(tmp_path, local_extra=1, remote_extra=0)
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))   # fetch now fails
    before = _git(repo, "rev-parse", ops)
    error = None
    try:
        sync.init(base, CONFIG)
    except Exception as exc:                                  # noqa: BLE001 - the old code raised here
        error = str(exc)
    assert error is None, error
    assert _contains(repo, ops, before)


# ----------------------------------------------------------------------------- (h) atomic writes


GOAL = "---\nid: 0001\ntitle: \"t\"\nstatus: pending\n---\n\nbody\n"


def _boom(*_a, **_k):
    raise OSError("simulated crash at publish")


def _only(dirpath):
    return sorted(p.name for p in pathlib.Path(dirpath).iterdir())


def test_set_status_leaves_the_goal_file_intact_when_the_publish_fails(tmp_path, monkeypatch):
    state = _mod("state")
    goal = tmp_path / "0001-x.md"
    goal.write_text(GOAL)
    monkeypatch.setattr(os, "replace", _boom)
    raised = None
    try:
        state._set_status(goal, "in_progress")
    except OSError as exc:
        raised = str(exc)
    assert raised == "simulated crash at publish"
    assert goal.read_text() == GOAL
    assert _only(tmp_path) == ["0001-x.md"]                  # no temp file left behind
    monkeypatch.undo()
    state._set_status(goal, "in_progress")
    assert "status: in_progress" in goal.read_text()


def test_append_to_body_leaves_the_goal_file_intact_when_the_publish_fails(tmp_path, monkeypatch):
    sources = _mod("sources")
    goal = tmp_path / "0001-x.md"
    goal.write_text(GOAL)
    src = sources.LocalSource(str(tmp_path))
    monkeypatch.setattr(os, "replace", _boom)
    raised = None
    try:
        src.append_to_body(str(goal), "<!-- marker -->")
    except OSError as exc:
        raised = str(exc)
    assert raised == "simulated crash at publish"
    assert goal.read_text() == GOAL
    assert _only(tmp_path) == ["0001-x.md"]
    monkeypatch.undo()
    src.append_to_body(str(goal), "<!-- marker -->")
    assert goal.read_text().endswith("<!-- marker -->\n")


def test_atomic_write_keeps_the_file_mode_and_leaves_no_temp_file(tmp_path):
    state = _mod("state")
    goal = tmp_path / "g.md"
    goal.write_text("old")
    os.chmod(goal, 0o640)
    link = tmp_path / "link.md"
    link.symlink_to(goal)
    state.atomic_write_text(link, "new é")
    assert link.is_symlink() and goal.read_text(encoding="utf-8") == "new é"
    assert (goal.stat().st_mode & 0o777) == 0o640
    assert _only(tmp_path) == ["g.md", "link.md"]


# ------------------------------------------------------------------------------------- (i) docs


def test_the_documented_decision_gate_and_ledger_claims_match_the_code(tmp_path):
    dg = _mod("decision_gate", ROOT / "hooks")
    params = [{"name": "timeout", "op": "le", "value": 10}]
    assert dg.violations("timeout = 99\n", params)
    assert dg.violations("timeout: 99\n", params)
    assert dg.violations("self.timeout = 99\n", params) == []
    assert dg.violations("timeout: int = 99\n", params) == []

    setup = _mod("setup", ROOT / "skills" / "sigma-setup" / "scripts")
    for value, expect in ((False, False), (True, True), (None, True)):
        d = tmp_path / str(value)
        d.mkdir()
        (d / "config.json").write_text(json.dumps({"ledger": {"enabled": value}}))
        cfg, _notes = setup.configure(str(d), source="local-goals")
        assert cfg["ledger"]["enabled"] is expect, value

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    row = next(line for line in readme.splitlines() if line.startswith("| **Decisions that actually hold**"))
    assert "**denied**" in row and "name = <literal>" in row and "docs/enforcement.md" in row
    assert "an edit that breaks it is **denied**" not in readme
    assert "still writes `ledger.enabled` as `true` where it is unset" not in readme
    assert "only where the key is still `null` or absent" in readme


def test_local_ahead_fails_closed_when_the_commit_count_cannot_be_read():
    """The seam control: an unreadable or unparseable count must read as "ahead" (never force)."""
    sync = _mod("sync")

    def git(root, args):
        if args[0] == "rev-parse":
            return "abc"
        raise RuntimeError("rev-list blew up")

    assert sync._local_ahead(git, ".", "ops", "origin") is True
    assert sync._local_ahead(lambda r, a: "" if a[0] == "rev-parse" else "not-a-number", ".", "ops", "origin") is True
    assert sync._local_ahead(lambda r, a: "" if a[0] == "rev-parse" else "0", ".", "ops", "origin") is False

"""#2243: a goal's plan and research artifacts must not commit an absolute home-directory path.

The mechanism is `artifact_paths.scrub_goal`, called from `work.py commit` just before `git add -A`. Every
path below is a fake (`/home/user/...`, a macOS twin, a Windows twin). The end-to-end test
runs real git: it files an artifact carrying an absolute path, runs the real `work.commit`, and reads the
committed blob back.
"""
import importlib.util
import json
import os
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


ap = _load("artifact_paths")
work = _load("work")
state = _load("state")

U = "/" + "Us" + "ers"                   # built: the public-text guard must see no workspace path
WU = "\\" + "Us" + "ers"


def _f(s):
    return s.replace("{WU}", WU).replace("{U}", U)


GOAL = "0001-x.md"
ON = {"work": {"enabled": True}}
PLUGIN = "/home/user/.claude/plugins/marketplaces/sigmaloop/skills/sigma-loop/scripts/north_star.py"


@pytest.fixture(autouse=True)
def _isolated_git_config(tmp_path, monkeypatch):
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))
    for var in list(os.environ):
        if var.startswith(("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
                           "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            monkeypatch.delenv(var)


# ----------------------------------------------------------------------------- the text rewriter


def test_posix_home_shapes_are_rewritten():
    text = _f("a /home/user/Work/proj/x.py b {U}/someone/Work/p/y.py c /root/z d {U}/someone")
    out, n = ap.scrub_text(text)
    assert out == "a [path] b [path] c [path] d [path]" and n == 4


def test_windows_shapes_are_rewritten():
    out, n = ap.scrub_text(_f("x C:{WU}\\someone\\Work\\p\\f.py y C:{U}/someone/p/g.py"))
    assert out == "x [path] y [path]" and n == 2


def test_paths_under_the_repo_root_become_relative():
    roots = [_f("{U}/someone/Work/proj"), _f("{U}/someone/Work/proj/.sdlc/work/1")]
    out, _ = ap.scrub_text(_f("cwd {U}/someone/Work/proj/.sdlc/work/1 and {U}/someone/Work/proj/src/a.py."), roots)
    assert out == "cwd . and src/a.py."
    out, _ = ap.scrub_text(_f("Worktree: {U}/someone/Work/proj/.sdlc/work/1"), [_f("{U}/someone/Work/proj")])
    assert out == "Worktree: .sdlc/work/1"
    out, _ = ap.scrub_text(_f("C:{WU}\\someone\\proj\\src\\a.py"), [_f("C:{WU}\\someone\\proj")])
    assert out == "src/a.py"


def test_a_sibling_directory_sharing_the_root_prefix_is_not_relative():
    out, _ = ap.scrub_text(_f("{U}/someone/Work/proj-other/a.py"), [_f("{U}/someone/Work/proj")])
    assert out == "[path]"


def test_paths_outside_the_root_become_the_placeholder():
    out, _ = ap.scrub_text("see /home/user/other/notes.md for more", ["/home/user/proj"])
    assert out == "see [path] for more"


def test_plugin_script_paths_become_plugin_relative():
    out, _ = ap.scrub_text("python3 " + PLUGIN + " now")
    assert out == "python3 <plugin>/skills/sigma-loop/scripts/north_star.py now"


def test_sentence_punctuation_after_a_path_is_kept():
    assert ap.scrub_text("It ran in /home/user/a/b. Then (see /home/user/c/d:) done")[0] == \
        "It ran in [path]. Then (see [path]:) done"


def test_ordinary_text_urls_and_tilde_are_left_alone():
    text = "GET /v1/orders/me at https://example.com/home/page and ~/.claude/x, plus `.sdlc/work/1`"
    assert ap.scrub_text(text) == (text, 0)


def test_rewrite_is_idempotent():
    once, _ = ap.scrub_text("w /home/user/proj/.sdlc/work/1 p " + PLUGIN, ["/home/user/proj"])
    assert ap.scrub_text(once, ["/home/user/proj"]) == (once, 0)
    assert ap.find_leaks(once) == []


def test_find_leaks_reports_line_numbers():
    assert ap.find_leaks("ok\nbad /home/user/a\n") == [(2, "/home/user/a")]


# ----------------------------------------------------------------------------- scrub_goal scope


def _tree(tmp_path):
    main = tmp_path / "main"
    wt = tmp_path / "wt"
    for base in (main, wt):
        for sub in ("plans", "research", "acceptance"):
            (base / ".sdlc" / sub).mkdir(parents=True)
    (main / ".sdlc" / "state").mkdir()
    return main, wt


def test_scrub_goal_rewrites_plan_and_research_and_prints_one_line(tmp_path):
    main, wt = _tree(tmp_path)
    plan = f"**Goal**: #1 - **Worktree**: {wt}/.sdlc/work/1\n"
    research = f"Working directory: {main}/.sdlc/work/1\n$ python3 {PLUGIN}\n{main}/.sdlc/context/north-star.md\n"
    for base in (main, wt):
        (base / ".sdlc" / "plans" / GOAL).write_text(plan)
        (base / ".sdlc" / "research" / GOAL).write_text(research)
    note, refusal = ap.scrub_goal(str(main / ".sdlc"), str(wt), GOAL)
    assert refusal == "" and note.count("\n") == 0 and "2 artifact file(s)" in note
    p = (wt / ".sdlc" / "plans" / GOAL).read_text()
    r = (wt / ".sdlc" / "research" / GOAL).read_text()
    assert p == "**Goal**: #1 - **Worktree**: .sdlc/work/1\n"
    assert r == ("Working directory: .sdlc/work/1\n$ python3 <plugin>/skills/sigma-loop/scripts/north_star.py\n"
                 ".sdlc/context/north-star.md\n")
    assert (main / ".sdlc" / "plans" / GOAL).read_text() == p, "the identical main copy keeps agreeing"


def test_scrub_goal_touches_only_the_three_dirs_of_this_goal(tmp_path):
    main, wt = _tree(tmp_path)
    leak = "see /home/user/x/y\n"
    (wt / ".sdlc" / "plans" / GOAL).write_text(leak)
    other = wt / ".sdlc" / "plans" / "0002-other.md"
    other.write_text(leak)
    src = wt / "src.py"
    src.write_text("P = '/home/user/x/y'\n")
    doc = wt / ".sdlc" / "design.md"
    doc.write_text(leak)
    ap.scrub_goal(str(main / ".sdlc"), str(wt), GOAL)
    assert (wt / ".sdlc" / "plans" / GOAL).read_text() == "see [path]\n"
    assert other.read_text() == leak and src.read_text() == "P = '/home/user/x/y'\n" and doc.read_text() == leak


def test_scrub_goal_with_nothing_to_do_says_nothing_and_changes_nothing(tmp_path):
    main, wt = _tree(tmp_path)
    f = wt / ".sdlc" / "plans" / GOAL
    f.write_text("clean .sdlc/work/1\n")
    assert ap.scrub_goal(str(main / ".sdlc"), str(wt), GOAL) == ("", "")
    assert f.read_text() == "clean .sdlc/work/1\n"


def test_acceptance_leak_is_refused_with_file_and_line_not_rewritten(tmp_path):
    main, wt = _tree(tmp_path)
    f = wt / ".sdlc" / "acceptance" / GOAL
    f.write_text("## Done when\n- a thing in /home/user/p/q\n")
    note, refusal = ap.scrub_goal(str(main / ".sdlc"), str(wt), GOAL)
    assert refusal.startswith("REFUSED") and f"{f}:2" in refusal and "nothing committed" in refusal
    assert f.read_text() == "## Done when\n- a thing in /home/user/p/q\n"


def test_plan_whose_hash_is_recorded_elsewhere_is_refused_not_rewritten(tmp_path):
    import hashlib
    main, wt = _tree(tmp_path)
    f = wt / ".sdlc" / "plans" / GOAL
    f.write_bytes(b"cwd /home/user/p\n")
    (main / ".sdlc" / "state" / "gates").mkdir()
    (main / ".sdlc" / "state" / "gates" / "0001-x.json").write_text(
        json.dumps({"plan_hash": hashlib.sha256(b"cwd /home/user/p\n").hexdigest()}))
    note, refusal = ap.scrub_goal(str(main / ".sdlc"), str(wt), GOAL)
    assert refusal.startswith("REFUSED") and f"{f}:1" in refusal
    assert f.read_bytes() == b"cwd /home/user/p\n"


def test_undecodable_artifact_is_refused(tmp_path):
    main, wt = _tree(tmp_path)
    (wt / ".sdlc" / "research" / GOAL).write_bytes(b"\xff\xfe/home/user/a")
    assert ap.scrub_goal(str(main / ".sdlc"), str(wt), GOAL)[1].startswith("REFUSED: cannot check")


# ----------------------------------------------------------------------------- the choke point, real git


def _git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout.strip()


def _repo_with_worktree(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / ".gitignore").write_text(".sdlc/state/\n.sdlc/work/\n")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / ".sdlc" / "state").mkdir(parents=True)
    (repo / ".sdlc" / "config.json").write_text(json.dumps(ON))
    d = str(repo / ".sdlc")
    state.start_run(d)
    wt = repo / ".sdlc" / "work" / "0001-x"
    _git(repo, "worktree", "add", "-q", "-b", "sdlc/0001-x", str(wt))
    work._save(d, GOAL, {"worktree": str(wt), "branch": "sdlc/0001-x", "base": "main", "remote": "origin", "pr": ""})
    return repo, wt, d


def test_work_commit_publishes_no_absolute_path_end_to_end(tmp_path):
    repo, wt, d = _repo_with_worktree(tmp_path)
    plan_dir = wt / ".sdlc" / "plans"
    plan_dir.mkdir(parents=True)
    research_dir = wt / ".sdlc" / "research"
    research_dir.mkdir(parents=True)
    (plan_dir / GOAL).write_text(f"**Goal**: #1 - **Worktree**: {wt}\n")
    (research_dir / GOAL).write_text(f"Working directory: {wt}/.sdlc/work/1\n$ python3 {PLUGIN}\n")
    (wt / "b.py").write_text("y = '/home/user/keep/me'\n")
    out = work.commit(d, ON, GOAL, message="feat: x")
    assert out.startswith("committed on sdlc/0001-x; scrubbed 3 absolute local path(s) from 2 artifact file(s)"), out
    plan = _git(wt, "show", f"HEAD:.sdlc/plans/{GOAL}")
    research = _git(wt, "show", f"HEAD:.sdlc/research/{GOAL}")
    assert str(tmp_path) not in plan + research and "/home/user" not in plan + research
    assert plan == "**Goal**: #1 - **Worktree**: ." and "<plugin>/skills/sigma-loop" in research
    assert _git(wt, "show", "HEAD:b.py") == "y = '/home/user/keep/me'", "source files are never scrubbed"


def test_work_commit_refuses_an_acceptance_leak_and_commits_nothing(tmp_path):
    repo, wt, d = _repo_with_worktree(tmp_path)
    (wt / ".sdlc" / "acceptance").mkdir(parents=True)
    (wt / ".sdlc" / "acceptance" / GOAL).write_text("## Done when\n- see /home/user/p\n")
    before = _git(wt, "rev-parse", "HEAD")
    out = work.commit(d, ON, GOAL, message="feat: x")
    assert out.startswith("REFUSED") and "nothing committed" in out
    assert _git(wt, "rev-parse", "HEAD") == before


def test_work_commit_fails_closed_when_the_check_cannot_run(tmp_path, monkeypatch):
    repo, wt, d = _repo_with_worktree(tmp_path)
    (wt / "b.py").write_text("y = 2\n")
    orig = work._load

    def boom(name):
        if name == "artifact_paths":
            raise RuntimeError("boom")
        return orig(name)
    monkeypatch.setattr(work, "_load", boom)
    before = _git(wt, "rev-parse", "HEAD")
    out = work.commit(d, ON, GOAL, message="feat: x")
    assert out.startswith("REFUSED: could not check") and _git(wt, "rev-parse", "HEAD") == before

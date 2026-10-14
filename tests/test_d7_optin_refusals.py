"""D7 'every refusal prints its fix': the opt-in refusals filed as #1291 and the local-goals park
resume wording (#1289).

Board setup/layout, `setup.py hooks` and `labels`, the wizard's pip step and `define.py declare`
used to refuse with a reason and no way out, and a missing `gh` read as 'not logged in'."""
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT = ROOT / "skills" / "sigma-init" / "scripts"
LOOP = ROOT / "skills" / "sigma-loop" / "scripts"
sys.path.insert(0, str(INIT))


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bs = _load(INIT / "board_setup.py", "d7_board_setup")
bl = _load(INIT / "board_layout.py", "d7_board_layout")
pf = _load(INIT / "preflight.py", "d7_preflight")
setup = _load(ROOT / "skills" / "sigma-setup" / "scripts" / "setup.py", "d7_setup")
triage = _load(LOOP / "triage.py", "d7_triage")
define = _load(ROOT / "skills" / "sigma-define" / "scripts" / "define.py", "d7_define")

BAD = "gh: Bad credentials (HTTP 401)"
SCOPES = "github.com\n  ✓ Logged in to github.com account x (keyring)\n  - Active account: true\n" \
         "  - Token scopes: 'project', 'repo', 'workflow', 'read:org'\n"


def _sdlc(tmp_path, project=None):
    d = tmp_path / ".sdlc"
    d.mkdir()
    cfg = {"discovery": {"source": "github", "github": {"repo": "acme/widget", "project": project or {}}}}
    (d / "config.json").write_text(json.dumps(cfg))
    return d


def _runner(table):
    """table: substring of the joined argv -> (rc, out, err); first match wins, else (0, '', '')."""
    def run(argv, *a, **k):
        joined = " ".join(argv)
        for needle, answer in table:
            if needle in joined:
                return answer
        return (0, "", "")
    return run


def _run_setup(tmp_path, table, *flags, project=None):
    lines = []
    rc = bs.main(["board_setup.py", "create", str(_sdlc(tmp_path, project)), *flags],
                 runner=_runner(table), out=lines.append)
    return rc, "\n".join(lines)


# ---------------------------------------------------------------- a missing gh is not a login problem

def test_missing_gh_is_not_reported_as_not_logged_in():
    chk, _ = pf.check_gh_auth("github.com", lambda argv, cwd, t: (127, "gh: not found on PATH"), 5)
    text = json.dumps(chk)
    assert chk["ok"] is False
    assert "not logged in" not in text and "cli.github.com" in text and "gh auth login" in text


def test_board_setup_with_no_gh_says_install_it(tmp_path):
    rc, text = _run_setup(tmp_path, [("gh", (127, "", "gh: not found on PATH"))])
    assert rc == 2 and "REFUSED" in text
    assert "not logged in" not in text and "cli.github.com" in text


# ---------------------------------------------------------------- board_setup number / owner / list

def test_board_setup_non_numeric_project_number_says_where_to_find_it(tmp_path):
    rc, text = _run_setup(tmp_path, [], project={"number": "abc"})
    assert rc == 2 and "REFUSED -- project.number abc is not a number -- fix: " in text
    assert "remove the key to create a new board" in text
    assert "discovery.github.project.number" in text and "/projects/" in text


def test_board_setup_unresolvable_me_prints_the_fix(tmp_path):
    rc, text = _run_setup(tmp_path, [("auth status", (0, SCOPES, "")), ("api user", (1, "", BAD))],
                          "--owner", "@me")
    assert rc == 2 and "REFUSED -- could not resolve @me: " in text and "gh auth login" in text


def test_board_setup_unreadable_owner_prints_the_fix(tmp_path):
    rc, text = _run_setup(tmp_path, [("auth status", (0, SCOPES, "")),
                                     ("users/acme", (1, "", "gh: Not Found (HTTP 404)"))],
                          "--owner", "acme")
    assert rc == 2 and "REFUSED -- could not read owner 'acme': " in text
    assert "spelling" in text and "discovery.github.project.owner" in text


def test_board_setup_board_list_failure_says_nothing_was_created_and_the_fix(tmp_path):
    rc, text = _run_setup(tmp_path, [("auth status", (0, SCOPES, "")),
                                     ("projectsV2", (1, "", BAD)),
                                     ("users/acme", (0, json.dumps({"type": "User"}), ""))],
                          "--owner", "acme")
    assert rc == 2 and "so a duplicate cannot be ruled out: " in text
    assert "gh auth login" in text and "Nothing was created" in text


# ---------------------------------------------------------------- board_layout

def test_board_layout_refusals_print_their_fix(tmp_path):
    sdlc = _sdlc(tmp_path, project={"number": "abc"})
    lines = []
    rc = bl.main(["board_layout.py", "verify", str(sdlc)], runner=_runner([]), out=lines.append)
    text = "\n".join(lines)
    assert rc == 2 and "is not a number -- fix: " in text and "/projects/" in text
    assert "create a new board" not in text            # board_layout never creates a board

    sdlc2 = tmp_path / "b" / ".sdlc"
    sdlc2.parent.mkdir()
    sdlc2.mkdir()
    (sdlc2 / "config.json").write_text(json.dumps(
        {"discovery": {"source": "github", "github": {"repo": "acme/widget", "project": {"number": 3}}}}))
    lines = []
    rc = bl.main(["board_layout.py", "verify", str(sdlc2), "--owner", "acme"],
                 runner=_runner([("users/acme", (1, "", "gh: Not Found (HTTP 404)"))]), out=lines.append)
    text = "\n".join(lines)
    assert rc == 2 and "could not read owner 'acme': " in text and "spelling" in text


# ---------------------------------------------------------------- setup.py hooks: five causes, five fixes

def _hook_exc(fn, *a):
    with pytest.raises(setup.HookPathRefused) as ei:
        fn(*a)
    return str(ei.value)


def test_hooks_missing_directory_names_the_directory_and_the_choice(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    msg = _hook_exc(setup.ensure_hook_path, tmp_path)
    assert msg.startswith("the hook directory does not exist in this repository")
    assert "create " + setup.HOOKS_PATH in msg and "re-run" in msg


def test_hooks_different_value_names_how_to_hand_over_without_revealing_it(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / setup.HOOKS_PATH).mkdir()
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local", "core.hooksPath", "secretdir"], check=True)
    msg = _hook_exc(setup.ensure_hook_path, tmp_path)
    assert msg.startswith("repository has a different core.hooksPath; refusing to overwrite it")
    assert "secretdir" not in msg and "--unset" not in msg      # never invite clearing a foreign value
    assert "leave" in msg and "keep running" in msg and "-- fix: " in msg


def test_hooks_git_failures_name_what_to_check(tmp_path, monkeypatch):
    class R:
        returncode = 128
        stdout = ""
        stderr = ""
    (tmp_path / setup.HOOKS_PATH).mkdir()
    monkeypatch.setattr(setup, "_hook_git", lambda *a, **k: R())
    msg = _hook_exc(setup.ensure_hook_path, tmp_path)
    assert msg.startswith("could not read the repository-local core.hooksPath")
    assert "git repository" in msg

    def nogit(*a, **k):
        raise setup.HookPathRefused(setup._GIT_MISSING)
    monkeypatch.setattr(setup, "_hook_git", nogit)
    assert "git-scm.com" in _hook_exc(setup.ensure_hook_path, tmp_path)


def test_hooks_install_failure_names_the_writable_check(tmp_path, monkeypatch):
    (tmp_path / setup.HOOKS_PATH).mkdir()
    calls = []

    class R:
        def __init__(self, rc, out=""):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def fake(repo, *args):
        calls.append(args)
        if args[:2] == ("config", "--local") and "--get" in args:
            return R(1)
        if args[0] == "worktree":
            return R(0, "worktree %s\n" % tmp_path)
        if args[0] == "rev-parse":
            return R(0, str(tmp_path))
        return R(1)
    monkeypatch.setattr(setup, "_hook_git", fake)
    msg = _hook_exc(setup.ensure_hook_path, tmp_path)
    assert msg.startswith("could not install the repository-local core.hooksPath")
    assert ".git/config" in msg


# ---------------------------------------------------------------- labels

def test_label_failure_summary_for_a_bad_token_names_the_login_fix():
    src = _load(LOOP / "sources.py", "d7_sources")
    lines, failed = src.render_label_report("acme/widget", [{"label": "sdlc:goal", "outcome": "failed", "reason": BAD}])
    text = "\n".join(lines)
    assert failed and "labels NOT ensured on acme/widget" in text and "gh auth login" in text


# ---------------------------------------------------------------- wizard pip

def test_pip_failure_names_the_manual_command_and_the_retry():
    import wizard_actions
    import types
    r = wizard_actions.run_graphify_install(
        run=lambda c: types.SimpleNamespace(returncode=1, stdout="", stderr="ERROR: no matching distribution"))
    assert r["ok"] is False and "ERROR: no matching distribution" in r["detail"]
    assert "python3 -m pip install graphifyy" in r["detail"] and "/sigma-doctor" in r["detail"]

    def boom(c):
        raise FileNotFoundError("python3")
    r = wizard_actions.run_graphify_install(run=boom)
    assert r["detail"].startswith("could not run pip: ") and "python3 -m pip install graphifyy" in r["detail"]


# ---------------------------------------------------------------- define declare rows

class _Src:
    def __init__(self, bodies, labels=()):
        self.bodies, self.repo_labels = bodies, set(labels)
        self.attached = {}

    def attach_label(self, goal, name):
        if name not in self.repo_labels:
            raise RuntimeError("unknown label(s) on this repo: %s" % name)
        self.attached.setdefault(goal, []).append(name)
        return True

    def fetch_body_labels(self, goal):
        return {"body": self.bodies[goal], "labels": [{"name": n} for n in self.attached.get(goal, [])]}


def test_declare_failed_row_names_the_label_to_create():
    rep = define.declare(_Src({1: "Feature: voice"}), "voice", [1])
    why = rep["issues"][0]["why"]
    assert rep["issues"][0]["outcome"] == define.FAILED
    assert why.startswith("unknown label(s) on this repo") and "gh label create feature:voice" in why


def test_declare_not_declared_row_says_what_is_missing():
    rep = define.declare(_Src({1: "no marker"}, labels={"feature:voice"}), "voice", [1])
    row = rep["issues"][0]
    assert row["outcome"] == define.NOT_DECLARED
    assert "Feature: voice" in row["why"] and "define.py stamp" not in row["why"]


def test_declare_ambiguous_row_says_to_edit_the_issue():
    rep = define.declare(_Src({1: "Feature: voice\nFeature: billing\n"}, labels={"feature:voice"}), "voice", [1])
    assert "one unit" in rep["issues"][0]["why"] and "edit the issue" in rep["issues"][0]["why"]


def test_declare_unreadable_row_names_the_rerun():
    class S(_Src):
        def fetch_body_labels(self, goal):
            raise RuntimeError("gh api failed: " + BAD)
    rep = define.declare(S({1: ""}, labels={"feature:voice"}), "voice", [1])
    why = rep["issues"][0]["why"]
    assert rep["issues"][0]["outcome"] == define.UNREADABLE
    assert "gh auth login" in why or "re-run" in why


# ---------------------------------------------------------------- #1289: resuming a parked local goal

def test_local_goals_unpark_says_how_a_parked_goal_resumes(capsys):
    rc = triage.needs_github({"discovery": {"source": "local-goals"}}, "unparking", 1, None, None)
    err = capsys.readouterr().err
    assert rc == 1
    assert "needs github discovery mode" in err and "/sigma-init --mode github" in err   # prefix kept
    assert "status: parked" in err and "status: pending" in err and "review-queue.md" in err


def test_local_goals_promote_says_how_a_proposed_goal_is_approved(capsys):
    triage.needs_github({"discovery": {"source": "local-goals"}}, "promotion", 1, None, None)
    err = capsys.readouterr().err
    assert "status: proposed" in err and "status: pending" in err


# ---------------------------------------------------------------- review of #1352

def test_install_text_for_a_missing_gh_is_prose_not_a_pasteable_command():
    chk, _ = pf.check_gh_auth("github.com", lambda argv, cwd, t: (127, "gh: not found on PATH"), 5)
    text = "\n".join(pf.failure_lines(chk))
    assert "cli.github.com" in text
    assert "``" not in text and "interactive" not in text.lower()
    for c in chk["commands"]:
        assert not c.startswith("install ") and " then " not in c       # only real commands


def _owner_lines(tmp_path, owner_answer):
    return _run_setup(tmp_path, [("auth status", (0, SCOPES, "")), ("users/acme", owner_answer)],
                      "--owner", "acme")[1]


def test_owner_read_404_has_exactly_one_fix_and_it_is_about_the_owner(tmp_path):
    text = _owner_lines(tmp_path, (1, "", "gh: Not Found (HTTP 404)"))
    assert text.count("fix: ") == 1 and "spelling" in text
    assert "discovery.github.repo" not in text


def test_owner_read_401_has_one_fix_and_no_spelling_advice(tmp_path):
    text = _owner_lines(tmp_path, (1, "", BAD))
    assert text.count("fix: ") == 1 and "gh auth login" in text and "spelling" not in text


def test_owner_read_with_gh_gone_has_one_fix_and_no_spelling_advice(tmp_path):
    text = _owner_lines(tmp_path, (127, "", "gh: not found on PATH"))
    assert text.count("fix: ") <= 1 and "cli.github.com" in text and "spelling" not in text


def test_board_layout_owner_read_has_one_fix(tmp_path):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(json.dumps(
        {"discovery": {"source": "github", "github": {"repo": "acme/widget", "project": {"number": 3}}}}))
    for answer, spelling in (((1, "", "gh: Not Found (HTTP 404)"), True), ((1, "", BAD), False)):
        lines = []
        bl.main(["board_layout.py", "verify", str(sdlc), "--owner", "acme"],
                runner=_runner([("users/acme", answer)]), out=lines.append)
        text = "\n".join(lines)
        assert text.count("fix: ") == 1 and (("spelling" in text) is spelling), text


def test_board_gh_rc_127_raises_the_install_hint():
    board = bs.Board(".", _runner([("gh", (127, "", "gh: not found on PATH"))]), lambda l: None)
    with pytest.raises(bs.Failed) as ei:
        board.gh("api", "user")
    assert "cli.github.com" in ei.value.detail and "not found on PATH" not in ei.value.detail


def test_hooks_install_text_for_an_existing_foreign_path_pinned(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / setup.HOOKS_PATH).mkdir()
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local", "core.hooksPath", ".husky/_"], check=True)
    msg = _hook_exc(setup.ensure_hook_path, tmp_path)
    assert ".husky" not in msg and "unset" not in msg.lower()
    assert "that tool's own hook" in msg or "hook manager" in msg


def _classified_401(*a, **k):
    exc = RuntimeError("gh api failed: " + BAD)
    exc.hint = BAD
    raise exc


def test_declare_failed_row_for_a_classified_failure_has_one_fix_not_two():
    class S(_Src):
        attach_label = staticmethod(_classified_401)
    row = define.declare(S({1: "Feature: vox"}, labels={"feature:vox"}), "vox", [1])["issues"][0]
    assert row["outcome"] == define.FAILED
    assert row["why"].count("fix: ") == 1 and "gh auth login" in row["why"]
    assert "gh label create" not in row["why"]


def test_declare_unreadable_row_for_a_classified_failure_has_one_fix_not_two():
    class S(_Src):
        fetch_body_labels = staticmethod(_classified_401)
    row = define.declare(S({1: ""}, labels={"feature:vox"}), "vox", [1])["issues"][0]
    assert row["outcome"] == define.UNREADABLE
    assert row["why"].count("fix: ") == 1 and "gh auth login" in row["why"]


def test_git_missing_is_the_install_fix_through_a_real_oserror(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file", "git")
    monkeypatch.setattr(setup.subprocess, "run", boom)
    msg = _hook_exc(setup.ensure_hook_path, tmp_path)
    assert msg == setup._GIT_MISSING or "git-scm.com" in msg
    assert "install git" in msg and "PATH" in msg

"""#1682: a failed delete of a merged goal branch is announced, and leftovers are reconciled.

Everything here runs against a LOCAL BARE REPOSITORY made in a temp dir, through the real `work._run`, with a
fake `gh` first on PATH (hence `live_gh`: the suite's guard keys on argv[0]; what the marked tests reach is the stub,
and PATH is the stub directory plus the OS default). The stub refuses (exit 99) any call when its bare repository
is not under the temp root, and answers only the five endpoints the cleanup uses, so nothing here can reach a real
remote, the fork or a real repository.

Each guard is covered by a test that goes red under that clause's own break: the four guards the issue names
(prefix, base, `origin/HEAD`, `feature/*`) and the lease on the listed head.
"""
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import sys

import pytest

import git_identity_support

pytestmark = [pytest.mark.live_gh,
              pytest.mark.skipif(sys.platform == "win32", reason="POSIX stub of gh")]

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"
WORK_PY = SCRIPTS / "work.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


work = _load("work")

FAKE_GH = r'''#!%(python)s
import json, os, subprocess, sys
root, bare = os.environ.get("FAKE_GH_ROOT", ""), os.environ.get("FAKE_GH_BARE", "")
if not root or not bare or not os.path.realpath(bare).startswith(os.path.realpath(root) + os.sep):
    sys.stderr.write("fake gh: refusing, its bare repository is not under the temp root\n"); sys.exit(99)
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
if not args or args[0] != "api":
    sys.stderr.write("fake gh: only `gh api` is served\n"); sys.exit(99)
rest = args[1:]
method = "GET"
if rest[:1] == ["-X"]:
    method, rest = rest[1], rest[2:]
path = rest[0]
git = lambda *a: subprocess.run(["git", "--git-dir", bare, *a], capture_output=True, text=True)
prs = json.load(open(os.environ["FAKE_GH_PRS"]))
HEADS = "repos/{owner}/{repo}/git/refs/heads/"
if method == "DELETE" and path.startswith(HEADS):
    name = path[len(HEADS):]
    err = os.environ.get("FAKE_GH_DELETE_ERROR", "")
    if err:
        sys.stderr.write(err + "\n"); sys.exit(1)
    if git("rev-parse", "--verify", "-q", "refs/heads/" + name).returncode:
        sys.stderr.write("gh: Reference does not exist (HTTP 422)\n"); sys.exit(1)
    git("update-ref", "-d", "refs/heads/" + name)
    sys.exit(0)
if method == "GET" and path.startswith("repos/{owner}/{repo}/git/ref/heads/"):
    name = path[len("repos/{owner}/{repo}/git/ref/heads/"):]
    if os.environ.get("FAKE_GH_REF_ERROR"):
        sys.stderr.write(os.environ["FAKE_GH_REF_ERROR"] + "\n"); sys.exit(1)
    out = git("rev-parse", "--verify", "-q", "refs/heads/" + name)
    if out.returncode:
        sys.stderr.write("gh: Not Found (HTTP 404)\n"); sys.exit(1)
    print(out.stdout.strip()); sys.exit(0)
if method == "GET" and path.startswith("repos/{owner}/{repo}/git/matching-refs/heads/"):
    if os.environ.get("FAKE_GH_LIST_ERROR"):
        sys.stderr.write(os.environ["FAKE_GH_LIST_ERROR"] + "\n"); sys.exit(1)
    prefix = path[len("repos/{owner}/{repo}/git/matching-refs/heads/"):]
    out = git("for-each-ref", "--format=%%(refname)\t%%(objectname)", "refs/heads/" + prefix)
    sys.stdout.write(out.stdout); sys.exit(0)
if method == "GET" and path == "repos/{owner}/{repo}":
    print("acme/app"); sys.exit(0)
if method == "GET" and "/pulls?" in path:
    repo, query = path.split("/pulls?", 1)
    query = dict(p.split("=", 1) for p in query.split("&"))
    os.environ.get("FAKE_GH_PR_REPO") and repo != "repos/" + os.environ["FAKE_GH_PR_REPO"] and (
        sys.stderr.write("fake gh: unexpected PR repo " + repo + "\n") or sys.exit(99))
    rows = [p for p in prs if p["state"] == query["state"]][::-1]
    per, page = int(query["per_page"]), int(query["page"])
    print(json.dumps(rows[(page - 1) * per:page * per])); sys.exit(0)
sys.stderr.write("fake gh: unserved %%s %%s\n" %% (method, path)); sys.exit(99)
'''

CFG = {"work": {"enabled": True, "branch_prefix": "sdlc/", "base": "main", "remote": "origin"},
       "action_log": {"enabled": True}}


class World:
    """A temp project (a git repo with `.sdlc`), a bare 'remote', and the PATH stub that serves it."""

    def __init__(self, tmp_path, monkeypatch, config=None):
        git_identity_support.scoped_git_identity(tmp_path / "ident", monkeypatch)
        self.root = tmp_path
        self.bare = tmp_path / "remote.git"
        self.proj = tmp_path / "proj"
        self.config = json.loads(json.dumps(config or CFG))
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        subprocess.run(["git", "init", "-q", str(self.proj)], check=True)
        (self.proj / "f.txt").write_text("seed\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed")
        self.git("push", "-q", str(self.bare), "HEAD:refs/heads/main")
        self.sdlc = self.proj / ".sdlc"
        (self.sdlc / "state").mkdir(parents=True)
        (self.sdlc / "config.json").write_text(json.dumps(self.config))
        self.prs_file, self.log_file = tmp_path / "prs.json", tmp_path / "gh.log"
        self.prs_file.write_text("[]")
        self.log_file.write_text("")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH % {"python": sys.executable})
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        monkeypatch.setenv("PATH", "%s%s%s" % (bin_dir, os.pathsep, os.defpath))
        for key, value in (("FAKE_GH_ROOT", tmp_path), ("FAKE_GH_BARE", self.bare), ("FAKE_GH_PRS", self.prs_file),
                           ("FAKE_GH_LOG", self.log_file)):
            monkeypatch.setenv(key, str(value))
        for key in ("FAKE_GH_DELETE_ERROR", "FAKE_GH_REF_ERROR", "FAKE_GH_LIST_ERROR", "CLAUDE_CODE_REMOTE"):
            monkeypatch.delenv(key, raising=False)
        self.mp = monkeypatch
        self._prs = []
        self._n = 0

    def git(self, *a):
        return subprocess.run(["git", "-C", str(self.proj), *a], check=True, capture_output=True, text=True).stdout

    def branch(self, name, extra=0):
        """Create `name` on the bare remote (one commit on main, plus `extra`), return its head sha."""
        self.git("commit", "-q", "--allow-empty", "-m", "work " + name)
        for i in range(extra):
            self.git("commit", "-q", "--allow-empty", "-m", "more %d" % i)
        self.git("push", "-q", str(self.bare), "HEAD:refs/heads/" + name)
        sha = self.git("rev-parse", "HEAD").strip()
        self.git("reset", "-q", "--hard", "HEAD~%d" % (1 + extra))
        return sha

    def advance(self, name):
        """Move `name` on the bare remote to a new commit, as a later push would."""
        self.git("commit", "-q", "--allow-empty", "-m", "later work on " + name)
        new = self.git("rev-parse", "HEAD").strip()
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.git("push", "-q", str(self.bare), "+%s:refs/heads/%s" % (new, name))
        return new

    def pr(self, branch, sha, state="closed", merged=True, base="main", repo="acme/app"):
        self._n += 1
        self._prs.append({"number": self._n, "state": state, "merged_at": "2026-10-16T00:00:00Z" if merged else None,
                          "head": {"ref": branch, "sha": sha, "repo": {"full_name": repo}},
                          "base": {"ref": base, "repo": {"full_name": "acme/app"}}})
        self.prs_file.write_text(json.dumps(self._prs))

    def merged(self, name, extra=0):
        sha = self.branch(name, extra)
        self.pr(name, sha)
        return sha

    def exists(self, name):
        return subprocess.run(["git", "--git-dir", str(self.bare), "rev-parse", "--verify", "-q", "refs/heads/" + name],
                              capture_output=True).returncode == 0

    def sha(self, name):
        return subprocess.run(["git", "--git-dir", str(self.bare), "rev-parse", "refs/heads/" + name],
                              capture_output=True, text=True).stdout.strip()

    def calls(self, needle=""):
        return [line for line in self.log_file.read_text().splitlines() if needle in line]

    def deletes(self):
        return self.calls("-X DELETE")

    def log_rows(self, goal):
        path = self.sdlc / "state" / "log" / ("%s.jsonl" % goal)
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def _delete(w, name, **kw):
    kw.setdefault("config", w.config)
    return work._delete_remote_branch(w.proj, name, work._run, **kw)


# --- AC-1: a failed delete is announced, never raised -----------------------------------------------

def test_a_failed_delete_names_the_branch_and_the_error_and_reaches_the_action_log(w, monkeypatch, capsys):
    w.merged("sdlc/10")
    monkeypatch.setenv("FAKE_GH_DELETE_ERROR", "gh: Resource not accessible by integration (HTTP 403)")
    assert _delete(w, "sdlc/10", sdlc_dir=str(w.sdlc), goal="10") is False
    err = capsys.readouterr().err
    assert "sdlc/10" in err and "HTTP 403" in err and "delete" in err
    rows = [r for r in w.log_rows("10") if r.get("kind") == "gate"]
    assert rows and rows[-1]["verdict"] == "failed" and rows[-1]["gate"] == "merge"
    assert "sdlc/10" in rows[-1]["why"] and "HTTP 403" in rows[-1]["why"]
    assert w.exists("sdlc/10")


def test_a_failed_delete_without_a_goal_still_prints_and_does_not_raise(w, monkeypatch, capsys):
    w.merged("sdlc/10")
    monkeypatch.setenv("FAKE_GH_DELETE_ERROR", "gh: HTTP 500 server error")
    assert _delete(w, "sdlc/10") is False
    assert "sdlc/10" in capsys.readouterr().err


def test_a_refused_delete_still_announces_as_before(w, capsys):
    assert _delete(w, "main", sdlc_dir=str(w.sdlc), goal="10") is False
    assert "refusing remote branch delete for 'main'" in capsys.readouterr().err
    assert [r["verdict"] for r in w.log_rows("10") if r.get("kind") == "gate"] == ["refused"]
    assert not w.deletes()


def test_a_branch_already_gone_is_a_quiet_success(w, capsys):
    """GitHub's own `Reference does not exist` is the one error that means the goal is met: `merge()` usually
    deleted it eagerly, and `finish()` asks again."""
    assert _delete(w, "sdlc/10", sdlc_dir=str(w.sdlc), goal="10") is True
    assert capsys.readouterr().err == ""
    assert not [r for r in w.log_rows("10") if r.get("kind") == "gate"]


def test_a_404_without_that_message_is_a_failure_not_a_success(w, monkeypatch, capsys):
    """A token that cannot see the repository gets a bare `Not Found`: that must stay loud."""
    monkeypatch.setenv("FAKE_GH_DELETE_ERROR", "gh: Not Found (HTTP 404)")
    assert _delete(w, "sdlc/10") is False
    assert "Not Found" in capsys.readouterr().err


# --- AC-3: a cloud session says the branch needs a sweep ---------------------------------------------

def test_a_cloud_failure_names_the_sweep_the_branch_and_its_head(w, monkeypatch, capsys):
    sha = w.merged("sdlc/10")
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.setenv("FAKE_GH_DELETE_ERROR", "gh: Forbidden (HTTP 403)")
    assert _delete(w, "sdlc/10", expect_sha=sha) is False
    err = capsys.readouterr().err
    assert "sweep" in err and "host that can delete" in err and "sdlc/10" in err and sha in err


def test_a_cloud_failure_reads_the_head_when_none_was_listed(w, monkeypatch, capsys):
    sha = w.merged("sdlc/10")
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.setenv("FAKE_GH_DELETE_ERROR", "gh: Forbidden (HTTP 403)")
    assert _delete(w, "sdlc/10") is False
    assert sha in capsys.readouterr().err


def test_outside_a_cloud_session_the_failure_does_not_claim_a_cloud_sweep(w, monkeypatch, capsys):
    sha = w.merged("sdlc/10")
    monkeypatch.setenv("FAKE_GH_DELETE_ERROR", "gh: Forbidden (HTTP 403)")
    assert _delete(w, "sdlc/10", expect_sha=sha) is False
    assert "host that can delete" not in capsys.readouterr().err


# --- the guards: each clause on its own -----------------------------------------------------------------

def test_guard_prefix_refuses_a_name_outside_the_configured_prefix(w, capsys):
    w.branch("other/10")
    assert _delete(w, "other/10") is False
    assert "configured prefix" in capsys.readouterr().err and not w.deletes()


def test_guard_empty_prefix_refuses_everything(w):
    w.branch("sdlc/10")
    assert _delete(w, "sdlc/10", config={"work": {"branch_prefix": ""}}) is False
    assert not w.deletes()


def test_guard_base_refuses_the_configured_base_even_with_a_matching_prefix(w, capsys):
    w.branch("sdlc/integration")
    config = {"work": {"branch_prefix": "sdlc/", "base": "sdlc/integration"}}
    assert _delete(w, "sdlc/integration", config=config) is False
    assert "base" in capsys.readouterr().err and not w.deletes()


def test_guard_base_refuses_the_goal_records_resolved_base(w, capsys):
    w.branch("sdlc/integration")
    assert _delete(w, "sdlc/integration", base="sdlc/integration") is False
    assert not w.deletes()


def test_guard_origin_head_refuses_the_remote_default_target(w, capsys):
    w.branch("sdlc/trunk")
    w.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/sdlc/trunk")
    assert _delete(w, "sdlc/trunk") is False
    assert "origin/HEAD" in capsys.readouterr().err and not w.deletes()


def test_guard_default_refuses_main_even_when_the_prefix_would_allow_it(w, capsys):
    config = {"work": {"branch_prefix": "ma", "base": "other"}}
    assert _delete(w, "main", config=config) is False
    assert "protected conventional default" in capsys.readouterr().err and not w.deletes()
    assert w.exists("main")


def test_guard_feature_refuses_a_feature_branch_even_when_the_prefix_would_allow_it(w, capsys):
    """A prefix of `feat` lets `feature/x` through the prefix clause; only the `feature/` clause stops it."""
    w.branch("feature/x")
    config = {"work": {"branch_prefix": "feat", "base": "main"}}
    assert _delete(w, "feature/x", config=config) is False
    assert "feature/" in capsys.readouterr().err and not w.deletes()
    assert w.exists("feature/x")


def test_guard_feature_does_not_block_an_ordinary_goal_branch(w):
    w.branch("sdlc/10")
    assert _delete(w, "sdlc/10") is True
    assert not w.exists("sdlc/10")


def test_lease_deletes_when_the_ref_is_still_at_the_listed_head(w):
    sha = w.branch("sdlc/10")
    assert _delete(w, "sdlc/10", expect_sha=sha) is True
    assert not w.exists("sdlc/10")


def test_lease_refuses_when_the_ref_moved_past_the_listed_head(w, capsys):
    sha = w.branch("sdlc/10")
    w.advance("sdlc/10")
    assert w.sha("sdlc/10") != sha
    assert _delete(w, "sdlc/10", expect_sha=sha, sdlc_dir=str(w.sdlc), goal="10") is False
    assert "moved" in capsys.readouterr().err and not w.deletes()
    assert w.exists("sdlc/10")
    assert [r["verdict"] for r in w.log_rows("10") if r.get("kind") == "gate"] == ["refused"]


def test_lease_refuses_when_the_head_cannot_be_read(w, monkeypatch, capsys):
    sha = w.branch("sdlc/10")
    monkeypatch.setenv("FAKE_GH_REF_ERROR", "gh: HTTP 502")
    assert _delete(w, "sdlc/10", expect_sha=sha) is False
    assert not w.deletes() and w.exists("sdlc/10")


# --- AC-2: reconcile ----------------------------------------------------------------------------------

def _reconcile(w, **kw):
    return work.reconcile_merged_branches(str(w.sdlc), w.config, **kw)


def test_a_merged_leftover_is_listed_and_deleted_at_its_listed_head(w):
    sha = w.merged("sdlc/10")
    lines = _reconcile(w)
    assert not w.exists("sdlc/10")
    assert any("sdlc/10" in line and sha[:7] in line for line in lines), lines
    assert w.deletes() == ["api -X DELETE repos/{owner}/{repo}/git/refs/heads/sdlc/10"]


def test_listing_alone_deletes_nothing(w):
    sha = w.merged("sdlc/10")
    found = work.merged_leftovers(str(w.sdlc), w.config)
    assert [(b["branch"], b["sha"]) for b in found["eligible"]] == [("sdlc/10", sha)]
    assert not w.deletes() and w.exists("sdlc/10")
    assert _reconcile(w, delete=False) and w.exists("sdlc/10")


def test_a_branch_that_moved_past_the_merged_head_is_kept(w):
    old = w.branch("sdlc/10")
    w.pr("sdlc/10", old)
    w.advance("sdlc/10")
    _reconcile(w)
    assert w.exists("sdlc/10") and not w.deletes()


def test_a_branch_with_an_open_pr_on_it_is_kept(w):
    sha = w.merged("sdlc/10")
    w.pr("sdlc/10", sha, state="open", merged=False)
    _reconcile(w)
    assert w.exists("sdlc/10") and not w.deletes()


def test_a_branch_that_is_the_base_of_an_open_pr_is_kept(w):
    w.merged("sdlc/10")
    other = w.branch("sdlc/11")
    w.pr("sdlc/11", other, state="open", merged=False, base="sdlc/10")
    _reconcile(w)
    assert w.exists("sdlc/10")
    assert "api -X DELETE repos/{owner}/{repo}/git/refs/heads/sdlc/10" not in w.deletes()


def test_a_pr_from_another_repository_does_not_make_the_branch_a_leftover(w):
    sha = w.branch("sdlc/10")
    w.pr("sdlc/10", sha, repo="someone/else")
    _reconcile(w)
    assert w.exists("sdlc/10") and not w.deletes()


def test_a_pr_closed_without_merging_keeps_the_branch(w):
    sha = w.branch("sdlc/10")
    w.pr("sdlc/10", sha, merged=False)
    _reconcile(w)
    assert w.exists("sdlc/10") and not w.deletes()


def test_a_branch_with_no_pr_is_kept(w):
    w.branch("sdlc/10")
    _reconcile(w)
    assert w.exists("sdlc/10") and not w.deletes()


def test_a_name_a_url_would_misread_is_never_deleted(w):
    """`#` ends a URL path: a delete for `sdlc/a#b` would name `sdlc/a`. Names come from the remote."""
    w.merged("sdlc/a#b")
    w.merged("sdlc/a")
    _reconcile(w)
    assert w.exists("sdlc/a#b") and not w.exists("sdlc/a")
    assert all("#" not in c for c in w.deletes())


def test_reconcile_never_touches_a_feature_or_base_branch(w):
    w.merged("feature/unit")
    _reconcile(w)
    assert w.exists("feature/unit") and w.exists("main") and not w.deletes()


def test_reconcile_applies_the_guards_to_a_listed_leftover(w):
    """`sdlc/integration` is merged-at-head and carries the prefix, so only the base guard keeps it."""
    w.merged("sdlc/integration")
    w.config["work"]["base"] = "sdlc/integration"
    _reconcile(w)
    assert w.exists("sdlc/integration") and not w.deletes()


def test_reconcile_applies_origin_head_to_a_listed_leftover(w):
    w.merged("sdlc/trunk")
    w.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/sdlc/trunk")
    _reconcile(w)
    assert w.exists("sdlc/trunk") and not w.deletes()


def test_reconcile_pins_each_delete_to_the_listed_head(w):
    """The branch moves between the listing and the delete (a push in that window): the lease keeps it."""
    w.merged("sdlc/10")
    moved = []

    def run(cwd, argv):
        if "git/ref/heads/sdlc/10" in " ".join(argv) and not moved:
            moved.append(w.advance("sdlc/10"))
        return work._run(cwd, argv)

    lines = work.reconcile_merged_branches(str(w.sdlc), w.config, run=run)
    assert moved and w.exists("sdlc/10") and not w.deletes()
    assert any("not deleted (refused" in line for line in lines)


def test_reconcile_skips_the_branch_it_was_told_to_skip(w):
    w.merged("sdlc/10")
    w.merged("sdlc/11")
    work.reconcile_merged_branches(str(w.sdlc), w.config, skip=("sdlc/10",))
    assert w.exists("sdlc/10") and not w.exists("sdlc/11")


def test_reconcile_stops_attempting_after_the_first_failed_delete(w, monkeypatch, capsys):
    a, b = w.merged("sdlc/10"), w.merged("sdlc/11")
    monkeypatch.setenv("FAKE_GH_DELETE_ERROR", "gh: Forbidden (HTTP 403)")
    lines = _reconcile(w)
    assert len(w.deletes()) == 1
    text = "\n".join(lines)
    assert "sdlc/10" in text and "sdlc/11" in text and a[:7] in text and b[:7] in text
    assert "HTTP 403" in capsys.readouterr().err


def test_reconcile_attempts_at_most_the_cap_of_deletes_and_says_what_it_left(w, monkeypatch):
    for n in range(10, 14):
        w.merged("sdlc/%d" % n)
    monkeypatch.setattr(work, "BRANCH_SWEEP_DELETES", 2)
    lines = _reconcile(w)
    assert len(w.deletes()) == 2
    assert any("2 more" in line for line in lines), lines


def test_the_pull_request_reads_do_not_grow_with_the_number_of_leftovers(w):
    """Scalability: 1 ref listing + 1 repository read + one closed page + one open page, for 1 or for 25."""
    for n in range(100, 125):
        w.merged("sdlc/%d" % n)
    work.merged_leftovers(str(w.sdlc), w.config)
    assert len(w.calls("matching-refs")) == 1 and len(w.calls("/pulls?")) == 2


def test_a_leftover_older_than_the_closed_window_is_not_found(w, monkeypatch):
    """The named ceiling: only the newest BRANCH_SWEEP_PAGES * BRANCH_SWEEP_PER_PAGE closed PRs are read."""
    w.merged("sdlc/10")
    w.merged("sdlc/11")
    monkeypatch.setattr(work, "BRANCH_SWEEP_PER_PAGE", 1)
    monkeypatch.setattr(work, "BRANCH_SWEEP_PAGES", 1)
    _reconcile(w)
    assert w.exists("sdlc/10") and not w.exists("sdlc/11")


def test_a_possibly_truncated_open_pr_list_deletes_nothing(w, monkeypatch):
    sha = w.merged("sdlc/10")
    other = w.branch("sdlc/11")
    w.pr("sdlc/11", other, state="open", merged=False)
    monkeypatch.setattr(work, "BRANCH_SWEEP_PER_PAGE", 1)
    monkeypatch.setattr(work, "BRANCH_SWEEP_PAGES", 1)
    lines = _reconcile(w)
    assert w.exists("sdlc/10") and not w.deletes() and "truncated" in " ".join(lines)


def test_a_failed_listing_never_raises_and_says_so(w, monkeypatch):
    monkeypatch.setenv("FAKE_GH_LIST_ERROR", "gh: HTTP 502")
    lines = _reconcile(w)
    assert lines and "could not list" in " ".join(lines) and not w.deletes()


def test_reconcile_reads_the_pull_requests_in_the_configured_repository(w):
    """A fork flow: the branch lives where `gh` resolves, the PR in the discovery repository."""
    w.config["discovery"] = {"github": {"repo": "acme/upstream"}}
    w.mp.setenv("FAKE_GH_PR_REPO", "acme/upstream")
    w.merged("sdlc/10")
    _reconcile(w)
    assert any("repos/acme/upstream/pulls?" in c for c in w.calls())
    assert not w.exists("sdlc/10")


# --- finish() and the documented gesture ----------------------------------------------------------------

def test_finish_reconciles_other_leftovers_after_its_own_cleanup(w):
    own = w.merged("sdlc/20")
    w.merged("sdlc/21")
    wt = w.proj / ".sdlc" / "work" / "20"
    w.git("worktree", "add", "-q", "-b", "sdlc/20", str(wt))
    work._save(str(w.sdlc), "20", {"worktree": str(wt), "branch": "sdlc/20", "base": "main",
                                   "remote": "origin", "pr": "7"})
    out = work.finish(str(w.sdlc), w.config, "20", merged=True)
    assert not w.exists("sdlc/20") and not w.exists("sdlc/21"), out
    assert "sdlc/21" in out and own


def test_finish_force_does_not_reconcile(w):
    w.merged("sdlc/21")
    wt = w.proj / ".sdlc" / "work" / "20"
    w.git("worktree", "add", "-q", "-b", "sdlc/20", str(wt))
    work._save(str(w.sdlc), "20", {"worktree": str(wt), "branch": "sdlc/20", "base": "main",
                                   "remote": "origin", "pr": "7"})
    work.finish(str(w.sdlc), w.config, "20", force=True)
    assert w.exists("sdlc/21") and not w.calls("matching-refs")


def _branches_cli(w, *args):
    return subprocess.run([sys.executable, str(WORK_PY), "branches", str(w.sdlc), *args], cwd=str(w.proj),
                          capture_output=True, text=True)


def test_the_documented_listing_gesture_is_read_only(w):
    sha = w.merged("sdlc/10")
    out = _branches_cli(w)
    assert out.returncode == 0, out.stderr
    assert "sdlc/10" in out.stdout and sha[:7] in out.stdout
    assert w.exists("sdlc/10") and not w.deletes()


def test_the_documented_sweep_gesture_deletes_the_listed_leftover(w):
    w.merged("sdlc/10")
    out = _branches_cli(w, "--sweep")
    assert out.returncode == 0, out.stderr
    assert not w.exists("sdlc/10")


# --- review findings: a refusal is not a failure, a live goal is not a leftover, bad evidence is an error ----

def test_a_branch_that_moved_does_not_stop_the_sweep_of_the_next(w):
    """Finding 2: the lease refusing ONE branch must not turn the rest into 'not attempted'. Clause: the pass stops
    only when the outcome is `failed` (the remote rejected the DELETE), never when it is `refused`."""
    w.merged("sdlc/10")
    w.merged("sdlc/11")
    moved = []

    def run(cwd, argv):
        if "git/ref/heads/sdlc/10" in " ".join(argv) and not moved:
            moved.append(w.advance("sdlc/10"))
        return work._run(cwd, argv)

    lines = work.reconcile_merged_branches(str(w.sdlc), w.config, run=run)
    assert w.exists("sdlc/10") and not w.exists("sdlc/11"), lines
    assert not any("not attempted" in line for line in lines), lines


def test_a_rejected_delete_still_stops_the_sweep_and_reports_an_error(w, monkeypatch):
    w.merged("sdlc/10")
    w.merged("sdlc/11")
    monkeypatch.setenv("FAKE_GH_DELETE_ERROR", "gh: Forbidden (HTTP 403)")
    errors = []
    lines = work.reconcile_merged_branches(str(w.sdlc), w.config, errors=errors)
    assert len(w.deletes()) == 1 and errors and any("not attempted" in line for line in lines)


def test_a_branch_whose_goal_has_a_worktree_here_is_not_a_leftover(w):
    """Finding 4: a goal being worked may have re-cut its branch from a merged head. Clause: the worktree check."""
    w.merged("sdlc/10")
    (w.sdlc / "work" / "10").mkdir(parents=True)
    found = work.merged_leftovers(str(w.sdlc), w.config)
    assert not found["eligible"] and any(name == "sdlc/10" for name, _ in found["kept"])
    work.reconcile_merged_branches(str(w.sdlc), w.config)
    assert w.exists("sdlc/10") and not w.deletes()


def test_a_worktree_for_another_goal_does_not_protect_a_leftover(w):
    w.merged("sdlc/10")
    (w.sdlc / "work" / "11").mkdir(parents=True)
    work.reconcile_merged_branches(str(w.sdlc), w.config)
    assert not w.exists("sdlc/10")


def test_an_empty_repository_name_judges_nothing_merged(w):
    """Finding 3: a PR whose head repository is null (a deleted fork) must not read as this repository's."""
    sha = w.branch("sdlc/10")
    w.pr("sdlc/10", sha, repo="")
    real_run = work._run

    def run(cwd, argv):
        if argv[:3] == ["gh", "api", "repos/{owner}/{repo}"] and "--jq" in argv:
            return ""
        return real_run(cwd, argv)

    errors = []
    lines = work.reconcile_merged_branches(str(w.sdlc), w.config, run=run, errors=errors)
    assert w.exists("sdlc/10") and not w.deletes() and errors, lines


def test_the_listing_gesture_exits_nonzero_for_an_empty_prefix(w):
    """Finding 5: a misconfigured prefix is a failed pass, not a clean one, for an automated sweep."""
    w.config["work"]["branch_prefix"] = ""
    (w.sdlc / "config.json").write_text(json.dumps(w.config))
    out = _branches_cli(w)
    assert out.returncode == 1, out.stdout + out.stderr


def test_the_listing_gesture_exits_zero_when_nothing_is_left(w):
    out = _branches_cli(w)
    assert out.returncode == 0, out.stdout + out.stderr

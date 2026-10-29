"""#2087: a cloud container is deleted after the run, so a plan that never reached the base must get a
durable copy on the goal's issue before cleanup, and the location must be printed. Outside a cloud
session `finish` behaves exactly as it did.

Real git throughout (a bare remote, a root checkout, a goal worktree); only `gh` is faked, by one
stateful fake issue. No network. No real cloud container was used: UNMEASURED live.
"""
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP = ROOT / "skills" / "sigma-loop" / "scripts"
sys.path.insert(0, str(LOOP))


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_2087", LOOP / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


work = _load("work")
state = _load("state")
GOAL = "2087"
BRANCH = "sdlc/2087"
CFG = {"work": {"enabled": True}, "discovery": {"source": "github", "github": {"repo": "o/r"}}}
LOCAL_CFG = {"work": {"enabled": True}, "discovery": {"source": "local"}}
OLD_LINE = "kept .sdlc/plans/2087.md: not on origin/main (commit it or it is lost)"
CLOUD = {"CLAUDE_CODE_REMOTE": "true"}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout


class FakeGh:
    """git runs for real; `gh api` is one stateful issue #2087 that keeps its comments."""

    def __init__(self, fail_post=False, fail_read=False, raise_after_post=False, empty_reply=False):
        self.comments = []
        self.posts = 0
        self.calls = []
        self.fail_post, self.fail_read = fail_post, fail_read
        self.raise_after_post, self.empty_reply = raise_after_post, empty_reply

    def __call__(self, cwd, argv):
        argv = [str(a) for a in argv]
        if argv[0] == "git":
            done = subprocess.run(["git", "-C", str(cwd), *argv[1:]], capture_output=True, text=True)
            if done.returncode:
                raise RuntimeError(" ".join(argv) + ": " + done.stdout + done.stderr)
            return done.stdout.strip()
        self.calls.append(argv)
        if argv[:2] == ["gh", "api"] and any("pulls?head=" in a for a in argv):
            return "9"
        text = " ".join(argv)
        if argv[:2] == ["gh", "api"] and "issues/2087" in text:
            post = "POST" in argv
            if post and "comments" in text:
                if self.fail_post:
                    err = RuntimeError("gh: HTTP 403: Resource not accessible")
                    err.hint = "HTTP 403: Resource not accessible by integration"
                    raise err
                self.posts += 1
                body = next(a[len("body="):] for a in argv if a.startswith("body="))
                self.comments.append({"body": body, "created_at": "2026-10-29T00:00:%02d" % self.posts,
                                      "author_association": "OWNER",
                                      "html_url": "https://github.com/o/r/issues/2087#issuecomment-%d" % (100 + self.posts)})
                if self.raise_after_post:                    # the comment landed, the reply was lost
                    self.raise_after_post = False
                    raise RuntimeError("gh: HTTP 502 Bad Gateway")
                return "" if self.empty_reply else json.dumps(self.comments[-1])
            if self.fail_read:
                raise RuntimeError("gh: HTTP 502")
            if text.endswith("issues/2087 --method GET"):
                return json.dumps({"number": 2087, "comments": len(self.comments)})
            if "comments" in text and "--method GET" in text:
                return json.dumps(self.comments)
        return ""


def _world(tmp_path, plan_text="# plan\n"):
    """bare remote + root checkout on main + goal worktree on sdlc/2087; the plan exists only in the root."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    root = tmp_path / "root"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t")):
        _git(root, "config", k, v)
    (root / ".gitignore").write_text(".sdlc/work/\n.sdlc/state/\n.sdlc/plans/\n")   # the repo ignores plans on purpose
    (root / "f.txt").write_text("x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-q", "-u", "origin", "main")
    sdlc = root / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps(CFG))
    state.start_run(str(sdlc))
    wt = sdlc / "work" / GOAL
    _git(root, "worktree", "add", "-q", "-b", BRANCH, str(wt), "main")
    work._save(str(sdlc), GOAL, {"worktree": str(wt), "branch": BRANCH, "base": "main",
                                 "remote": "origin", "pr": "9"})
    plan = sdlc / "plans" / "2087.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(plan_text)
    return root, str(sdlc), plan


def _finish(sdlc, run, monkeypatch, env, config=CFG):
    for k in ("CLAUDE_CODE_REMOTE", "SIGMA_GH_GRAPHQL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return work.finish(sdlc, config, GOAL, run=run, merged=True)


def _unfence(body):
    """The plan text inside a comment body: between the opening fence line and the closing fence of the same width."""
    m = re.search(r"^(`{4,})markdown\n(.*?)\n\1\n", body, re.S | re.M)
    assert m, body[:300]
    runs = [len(r) for r in re.findall(r"`+", m.group(2))]
    assert len(m.group(1)) > max(runs, default=0), "the fence must be longer than any backtick run inside the plan"
    return m.group(2)


def test_cloud_finish_leaves_a_durable_copy_and_prints_where(tmp_path, monkeypatch):
    """AC-1: the plan that never reached the base is posted on the issue, byte for byte, and the URL is printed."""
    _root, sdlc, plan = _world(tmp_path, "# plan\n\nstep 1\n")
    gh = FakeGh()
    out = _finish(sdlc, gh, monkeypatch, CLOUD)
    assert gh.posts == 1, gh.calls
    assert _unfence(gh.comments[0]["body"]) == "# plan\n\nstep 1\n"
    assert "issues/2087#issuecomment-101" in out, out
    assert "commit it or it is lost" not in out, out
    assert plan.read_text() == "# plan\n\nstep 1\n", "the local copy stays: the posting adds a copy, it moves nothing"
    sha = hashlib.sha256(b"# plan\n\nstep 1\n").hexdigest()
    assert gh.comments[0]["body"].startswith("<!-- sigma:plan-copy goal=2087 sha256=%s part=1/1 -->" % sha)


def test_cloud_finish_posts_nothing_when_the_plan_landed(tmp_path, monkeypatch):
    """The copy is for a plan that is NOT on the base: one that landed is removed as before and nothing is posted."""
    root, sdlc, plan = _world(tmp_path, "# plan\n")
    wt = pathlib.Path(sdlc) / "work" / GOAL
    (wt / ".sdlc" / "plans").mkdir(parents=True)
    (wt / ".sdlc" / "plans" / "2087.md").write_text("# plan\n")
    _git(wt, "add", "-f", ".sdlc/plans/2087.md")
    _git(wt, "commit", "-qm", "plan")
    _git(wt, "push", "-q", "origin", BRANCH)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "remote.git"), str(other)], check=True)
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t")):
        _git(other, "config", k, v)
    _git(other, "merge", "-q", "--no-ff", "-m", "land", "origin/" + BRANCH)
    _git(other, "push", "-q", "origin", "main")
    gh = FakeGh()
    out = _finish(sdlc, gh, monkeypatch, CLOUD)
    assert gh.posts == 0 and not plan.exists(), (out, gh.calls)
    assert "removed landed duplicate .sdlc/plans/2087.md" in out


@pytest.mark.parametrize("env", [{}, {"CLAUDE_CODE_REMOTE": "false"}, {"CLAUDE_CODE_REMOTE": ""},
                                 {"SIGMA_GH_GRAPHQL": "off"}], ids=["unset", "false", "empty", "graphql-off-alone"])
def test_outside_a_cloud_session_finish_is_unchanged(tmp_path, monkeypatch, env):
    """AC-2: not a cloud session -> no gh call beyond today's, the same kept line byte for byte, the file kept."""
    _root, sdlc, plan = _world(tmp_path)
    gh = FakeGh()
    out = _finish(sdlc, gh, monkeypatch, env)
    assert gh.posts == 0 and not [c for c in gh.calls if "issues/2087" in " ".join(c)], gh.calls
    assert out.endswith("; " + OLD_LINE), out
    assert plan.exists()


def test_cloud_goal_without_an_issue_is_unchanged(tmp_path, monkeypatch):
    """A local goal file has no issue to carry the copy: today's warning, nothing posted."""
    _root, sdlc, plan = _world(tmp_path)
    gh = FakeGh()
    out = _finish(sdlc, gh, monkeypatch, CLOUD, config=LOCAL_CFG)
    assert gh.posts == 0 and out.endswith("; " + OLD_LINE), out


def test_a_retried_copy_does_not_post_twice(tmp_path):
    """Resiliency: the same plan bytes already on the issue are found by their marker and reused."""
    root, sdlc, plan = _world(tmp_path)
    gh = FakeGh()
    first = work._durable_plan_copy(sdlc, CFG, GOAL, plan, gh, str(root))
    second = work._durable_plan_copy(sdlc, CFG, GOAL, plan, gh, str(root))
    assert gh.posts == 1 and first == second == ("https://github.com/o/r/issues/2087#issuecomment-101", ""), (first, second)


def test_a_post_that_landed_but_raised_is_found_by_the_retry(tmp_path):
    """`comment_issue` is not idempotent: a 502 after the comment landed must not make the retry post a second copy."""
    root, sdlc, plan = _world(tmp_path)
    gh = FakeGh(raise_after_post=True)
    url, why = work._durable_plan_copy(sdlc, CFG, GOAL, plan, gh, str(root))
    assert url == "" and "durable copy FAILED" in why and gh.posts == 1
    url, why = work._durable_plan_copy(sdlc, CFG, GOAL, plan, gh, str(root))
    assert url.endswith("#issuecomment-101") and why == "" and gh.posts == 1


def test_a_marker_from_an_untrusted_commenter_does_not_suppress_the_post(tmp_path):
    """Anyone can paste the marker line; only an OWNER, MEMBER or COLLABORATOR comment counts as the posted copy."""
    root, sdlc, plan = _world(tmp_path)
    gh = FakeGh()
    (marker,) = [b.split("\n", 1)[0] for b in work._plan_copy_bodies("2087", plan.read_text())]
    gh.comments.append({"body": marker + "\nspoof", "created_at": "2026-10-28", "author_association": "NONE",
                        "html_url": "https://github.com/o/r/issues/2087#issuecomment-1"})
    url, why = work._durable_plan_copy(sdlc, CFG, GOAL, plan, gh, str(root))
    assert gh.posts == 1 and url.endswith("#issuecomment-101"), (url, why)


def test_a_reply_with_no_url_is_a_failure_not_a_success(tmp_path, monkeypatch):
    """A fake or an odd host answering '' must never print an empty location as success."""
    _root, sdlc, plan = _world(tmp_path)
    gh = FakeGh(empty_reply=True)
    out = _finish(sdlc, gh, monkeypatch, CLOUD)
    assert "durable copy FAILED" in out and "commit it or it is lost" in out, out


def test_an_edited_plan_gets_its_own_copy(tmp_path, monkeypatch):
    """The marker carries the plan's sha256: a changed plan is not mistaken for the posted one."""
    body = work._plan_copy_bodies("2087", "# plan v2\n")
    assert body[0].startswith("<!-- sigma:plan-copy goal=2087 sha256=%s part=1/1 -->"
                              % hashlib.sha256(b"# plan v2\n").hexdigest())
    assert work._plan_copy_bodies("2087", "# plan v1\n")[0].split("\n", 1)[0] != body[0].split("\n", 1)[0]


def test_a_failed_post_keeps_todays_warning_and_says_so(tmp_path, monkeypatch):
    """No silent loss: when the copy cannot be posted the old line stays and the failure is named."""
    _root, sdlc, plan = _world(tmp_path)
    gh = FakeGh(fail_post=True)
    out = _finish(sdlc, gh, monkeypatch, CLOUD)
    assert "(commit it or it is lost)" in out and "durable copy FAILED" in out, out
    assert plan.exists()


def test_an_unreadable_issue_still_posts_the_copy(tmp_path, monkeypatch):
    """The idempotence read is an optimisation: when it fails the copy is posted anyway (a duplicate beats a loss)."""
    _root, sdlc, _plan = _world(tmp_path)
    gh = FakeGh(fail_read=True)
    out = _finish(sdlc, gh, monkeypatch, CLOUD)
    assert gh.posts == 1 and "issuecomment-101" in out, out


def test_a_plan_with_code_fences_round_trips(tmp_path):
    """The fence is longer than any backtick run in the plan, so the plan cannot close it early."""
    text = "# p\n\n```python\nx = 1\n```\n\n`````\nfive\n`````\n"
    (body,) = work._plan_copy_bodies("2087", text)
    assert _unfence(body) == text, body


def test_a_large_plan_is_split_into_parts_that_rebuild_it():
    text = "".join("line %06d\n" % i for i in range(12000))          # 144 000 characters
    bodies = work._plan_copy_bodies("2087", text)
    assert len(bodies) == 3 and all(len(b) < 65000 for b in bodies), [len(b) for b in bodies]
    assert "part=1/3" in bodies[0] and "part=3/3" in bodies[2]
    assert "".join(_unfence(b) for b in bodies) == text, "the parts, joined, are the plan"


def test_a_plan_over_the_cap_is_truncated_loudly():
    text = "x" * 400000
    bodies = work._plan_copy_bodies("2087", text)
    assert len(bodies) == work.PLAN_COPY_MAX_PARTS
    assert "Truncated" in bodies[-1] and "400000" in bodies[-1]


def test_a_secret_shaped_string_is_not_copied_to_the_issue():
    shape = "ghp_" + "a1B2c3D4" * 5
    (body,) = work._plan_copy_bodies("2087", "# p\n\nvalue %s\n" % shape)
    assert shape not in body and "redacted" in body


def test_only_the_plan_is_copied_not_the_research_or_acceptance_record(tmp_path, monkeypatch):
    """Scope: an unlanded research dossier keeps today's warning byte for byte, even in a cloud session."""
    _root, sdlc, plan = _world(tmp_path)
    plan.unlink()
    res = pathlib.Path(sdlc) / "research" / "2087.md"
    res.parent.mkdir(parents=True)
    res.write_text("# research\n")
    gh = FakeGh()
    out = _finish(sdlc, gh, monkeypatch, CLOUD)
    assert gh.posts == 0 and out.endswith("; kept .sdlc/research/2087.md: not on origin/main (commit it or it is lost)"), out


def test_a_caller_without_a_config_never_posts(tmp_path, monkeypatch):
    """`_clean_landed_root_records` is also callable without a config (its old signature): that is today's behaviour."""
    _root, sdlc, _plan = _world(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    gh = FakeGh()
    lines = work._clean_landed_root_records(sdlc, work._record(sdlc, GOAL), GOAL, gh)
    assert gh.posts == 0 and lines == [OLD_LINE], lines

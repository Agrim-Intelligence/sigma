"""#465 (B6): a goal worktree is reclaimed only when everything about it is provably finished.

Every test builds a REAL throwaway repository (bare origin, a clone whose `.gitignore` carries Sigma's
own `.sdlc/*` rule, real `git worktree add`, real commits, work records written the way `work.start`
writes them) and drives the documented operator gesture, `worktree_prune.py sweep|list-removable
.sdlc`, with a fake `gh` executable on PATH that serves a fixture PR body and exits 1 on any GraphQL
`gh pr view`. Git is never mocked. The few in-process seams use a `run` wrapper that delegates to real
git and records argv (the never-force check) or stands for a killed sweep (the crash cases).
"""
import json
import os
import pathlib
import signal
import subprocess
import sys
import textwrap
import time

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"
sys.path.insert(0, str(S))
GENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.test",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.test",
            GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
GITIGNORE = ".sdlc/*\n!.sdlc/plans/\n__pycache__/\n.env.local\n*.pyc\n.pytest_cache/\n"

_FAKE_GH = textwrap.dedent('''\
    #!%s
    import json, os, sys, time
    d = os.environ["FAKE_GH_DIR"]
    argv = sys.argv[1:]
    with open(os.path.join(d, "calls.log"), "a") as f:
        f.write(json.dumps(argv) + "\\n")
    if argv[:1] == ["pr"]:
        sys.exit(1)                      # GraphQL: never allowed
    if argv[:1] == ["api"] and "-X" in argv:
        sys.exit(1)                      # a mutation: never allowed
    if argv[:1] == ["api"] and "/pulls/" in argv[1]:
        n = argv[1].rsplit("/", 1)[1]
        p = os.path.join(d, "pr-%%s.json" %% n)
        if not os.path.exists(p):
            sys.exit(1)
        body = open(p).read()
        if body.startswith("SLEEP"):
            time.sleep(60)
        sys.stdout.write(body)
        sys.exit(0)
    sys.exit(1)
''' % sys.executable)


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), env=GENV, capture_output=True, text=True,
                          check=check)


class Proj:
    def __init__(self, root):
        self.root = root
        self.origin = root / "origin.git"
        self.proj = root / "proj"
        self.sdlc = self.proj / ".sdlc"
        self.ghdir = root / "gh"
        self.bin = root / "bin"
        self.ghdir.mkdir()
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(_FAKE_GH)
        gh.chmod(0o755)
        git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(root, "init", "-q", "-b", "main", str(self.proj))
        git(self.proj, "remote", "add", "origin", str(self.origin))
        (self.proj / ".gitignore").write_text(GITIGNORE)
        (self.proj / "a.txt").write_text("alpha\n")
        (self.proj / "d").mkdir()
        (self.proj / "d" / "sub").mkdir()
        (self.proj / "d" / "sub" / "deep.txt").write_text("deep\n")
        (self.proj / "d" / "top.txt").write_text("top\n")
        (self.proj / "link").symlink_to("a.txt")                # a tracked symlink: debris proofs compare it
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "base")
        git(self.proj, "push", "-q", "origin", "main")
        (self.sdlc / "state" / "work").mkdir(parents=True)
        self.set_config({})

    def set_config(self, work):
        cfg = {"work": dict({"enabled": True}, **work), "action_log": {"enabled": True},
               "discovery": {"source": "local"}, "verify": {"enforce": False}}
        (self.sdlc / "config.json").write_text(json.dumps(cfg))

    def env(self, **extra):
        return dict(GENV, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", FAKE_GH_DIR=str(self.ghdir),
                    **extra)

    def goal(self, n, pr=None, merged=True, head_ref=None, state="closed", repo="o/r", base_repo="o/r"):
        """A real worktree `.sdlc/work/<n>` on branch sdlc/<n> with one commit, a work record, a PR body."""
        n = str(n)
        pr = str(1000 + int(n)) if pr is None else pr
        path = self.sdlc / "work" / n
        git(self.proj, "worktree", "add", "-q", "-b", f"sdlc/{n}", str(path), "origin/main")
        (path / f"change-{n}.txt").write_text("work\n")
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", f"work {n}")
        rec = {"worktree": str(path), "branch": f"sdlc/{n}", "base": "main", "base_resolved": True,
               "remote": "origin", "pr": pr}
        (self.sdlc / "state" / "work" / f"{n}.json").write_text(json.dumps(rec))
        if pr:
            head = git(path, "rev-parse", "HEAD").stdout.strip()
            self.pr_body(pr, merged=merged, head_sha=head, head_ref=head_ref or f"sdlc/{n}", state=state,
                         repo=repo, base_repo=base_repo)
        return path

    def pr_body(self, pr, merged=True, head_sha="0" * 40, head_ref="x", state="closed", repo="o/r",
                base_repo="o/r"):
        body = {"merged": merged, "merged_at": "2026-10-01T00:00:00Z" if merged else None,
                "state": state, "head": {"ref": head_ref, "sha": head_sha,
                                         "repo": {"full_name": repo} if repo else None},
                "base": {"ref": "main", "repo": {"full_name": base_repo}}}
        (self.ghdir / f"pr-{pr}.json").write_text(json.dumps(body))

    def record(self, n):
        return self.sdlc / "state" / "work" / f"{n}.json"

    def cli(self, *args, cwd=None, timeout=120, env=None):
        proc = subprocess.run([sys.executable, str(S / "worktree_prune.py"), *args, str(self.sdlc)],
                              cwd=str(cwd or self.proj), capture_output=True, text=True,
                              env=env or self.env(), timeout=timeout)
        return proc

    def sweep(self, *extra, cwd=None, **kw):
        proc = self.cli("sweep", *extra, cwd=cwd, **kw)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    def gh_calls(self):
        p = self.ghdir / "calls.log"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def run_fn(self, before=None, seen=None):
        """A `run` for in-process sweeps: real git; `gh` goes to the fake (the suite's live-gh guard
        forbids an argv named `gh`); `before(cwd, argv)` may act, record or raise first."""
        wp = _wp()

        def run(cwd, argv, timeout=None, stdin=None):
            argv = [str(a) for a in argv]
            if seen is not None:
                seen.append(argv)
            if before is not None:
                got = before(cwd, argv)
                if got is not None:
                    return got
            real = [sys.executable, str(self.bin / "gh"), *argv[1:]] if argv[0] == "gh" else argv
            return wp.default_run(cwd, real, timeout=timeout, stdin=stdin)
        return run

    def registered(self):
        out = git(self.proj, "worktree", "list", "--porcelain").stdout
        return [l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("worktree ")]


@pytest.fixture
def p(tmp_path, monkeypatch):
    proj = Proj(tmp_path.resolve())
    for key, value in proj.env().items():                       # in-process seams see the fake gh too
        monkeypatch.setenv(key, value)
    return proj


def _wp():
    assert (S / "worktree_prune.py").is_file(), "skills/agrim-loop/scripts/worktree_prune.py does not exist"
    import worktree_prune
    return worktree_prune


def kept(out):
    return {k["goal"]: k["reason"] for k in out["kept"]}


# --- the removal proof -------------------------------------------------------------------------


def test_merged_clean_worktree_is_removed_and_record_and_branch_are_kept(p):
    path = p.goal(21)
    (path / "__pycache__").mkdir()
    (path / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (path / ".sdlc" / "state" / "time").mkdir(parents=True)
    (path / ".sdlc" / "state" / "time" / "e.jsonl").write_text("{}\n")
    out = p.sweep()
    assert out["removed"] == ["21"] and out["kept"] == [], out
    assert not path.exists() and str(path) not in p.registered()
    assert p.record(21).exists()                               # the only home of the PR number
    assert git(p.proj, "rev-parse", "--verify", "refs/heads/sdlc/21").returncode == 0
    calls = p.gh_calls()
    assert calls == [["api", "repos/{owner}/{repo}/pulls/1021"]], calls   # one REST read, nothing else
    assert (p.proj / "a.txt").read_text() == "alpha\n"


def test_uncommitted_untracked_and_hidden_changes_are_kept(p):
    cases = [("modify", "dirty"), ("untracked", "dirty"), ("staged", "dirty"),
             ("assume-unchanged", "hidden-changes"), ("skip-worktree", "hidden-changes")]
    for i, (how, reason) in enumerate(cases):
        n = 220 + i
        path = p.goal(n)
        if how == "modify":
            (path / "a.txt").write_text("edited\n")
        elif how == "untracked":
            (path / "new.txt").write_text("mine\n")
        elif how == "staged":
            (path / "new.txt").write_text("mine\n")
            git(path, "add", "new.txt")
        else:
            git(path, "update-index", "--assume-unchanged" if how == "assume-unchanged" else "--skip-worktree",
                "a.txt")
            (path / "a.txt").write_text("hidden edit\n")
    out = p.sweep()
    assert out["removed"] == [], out
    assert kept(out) == {str(220 + i): reason for i, (_how, reason) in enumerate(cases)}, out
    for i, (how, _reason) in enumerate(cases):
        path = p.sdlc / "work" / str(220 + i)
        assert path.exists()
        if how in ("modify", "assume-unchanged", "skip-worktree"):
            assert "edit" in (path / "a.txt").read_text()
        else:
            assert (path / "new.txt").read_text() == "mine\n"


def test_ignored_user_file_is_kept_but_caches_are_not_a_reason(p):
    path = p.goal(23)
    (path / ".env.local").write_text("SECRET_LIKE=1\n")        # ignored by the project, still user data
    out = p.sweep()
    assert kept(out) == {"23": "ignored-content"} and (path / ".env.local").exists(), out


def test_more_ignored_files_than_the_listing_cap_keeps_the_tree(p):
    wp = _wp()
    path = p.goal(24)
    cache = path / "__pycache__"
    cache.mkdir()
    for i in range(wp.IGNORED_LISTING_CAP + 1):
        (cache / f"m{i}.pyc").write_bytes(b"")
    out = p.sweep()
    assert kept(out) == {"24": "ignored-content"}, out


def test_commits_beyond_the_merged_pr_are_kept(p):
    path = p.goal(25)
    (path / "later.txt").write_text("after the PR head\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "after the merged PR head")
    out = p.sweep()
    assert kept(out) == {"25": "unpushed"} and path.exists(), out


def test_only_a_confirmed_merged_pr_on_this_branch_removes(p):
    cases = [("open", "pr-open"), ("closed", "pr-closed-unmerged"), ("missing", "pr-unknown"),
             ("wrong-ref", "pr-mismatch"), ("fork", "pr-mismatch"), ("no-head-repo", "pr-mismatch"),
             ("no-pr", "no-pr")]
    paths = {}
    for i, (kind, _reason) in enumerate(cases):
        n = 260 + i
        pr = "" if kind == "no-pr" else str(3000 + n)
        path = paths[n] = p.goal(n, pr=pr)
        head = git(path, "rev-parse", "HEAD").stdout.strip()
        if kind == "open":
            p.pr_body(pr, merged=False, head_sha=head, head_ref=f"sdlc/{n}", state="open")
        elif kind == "closed":
            p.pr_body(pr, merged=False, head_sha=head, head_ref=f"sdlc/{n}", state="closed")
        elif kind == "missing":
            (p.ghdir / f"pr-{pr}.json").unlink()                 # no fixture: the fake gh exits 1
        elif kind == "wrong-ref":
            p.pr_body(pr, head_sha=head, head_ref="other")
        elif kind == "fork":
            p.pr_body(pr, head_sha=head, head_ref=f"sdlc/{n}", repo="fork/r")
        elif kind == "no-head-repo":
            p.pr_body(pr, head_sha=head, head_ref=f"sdlc/{n}", repo=None)
    out = p.sweep()
    assert out["removed"] == [], out
    assert kept(out) == {str(260 + i): reason for i, (_kind, reason) in enumerate(cases)}, out
    assert all(path.exists() and p.record(n).exists() for n, path in paths.items())


# --- ownership and liveness ----------------------------------------------------------------


def test_live_agent_live_session_current_excluded_and_awaiting_merge_are_kept(p, tmp_path):
    wp = _wp()
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    try:
        a, b, c, d, e, f = (p.goal(n) for n in (31, 32, 33, 34, 35, 36))
        loop = [sys.executable, str(S / "loop.py")]
        for goal, pid in (("31", live.pid), ("36", dead.pid)):
            r = subprocess.run([*loop, "agent-start", str(p.sdlc), goal, "--pid", str(pid)],
                               capture_output=True, text=True, env=p.env(), cwd=str(p.proj))
            assert r.returncode == 0, r.stderr
        sessions = p.sdlc / "state" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / f"{live.pid}.active").write_text(json.dumps({"in_flight": ["#32"],
                                                                   "settled_admissions": 1}))
        rec = json.loads(p.record(35).read_text())
        rec["awaiting_merge"] = {"since": "2026-10-01T00:00:00Z"}
        p.record(35).write_text(json.dumps(rec))
        out = p.sweep(cwd=c)                                    # the sweeping process sits INSIDE goal 33
        assert kept(out) == {"31": "live-agent", "32": "live-session", "33": "current",
                             "35": "awaiting-merge"}, out
        assert sorted(out["removed"]) == ["34", "36"], out       # a dead marker is no owner
        assert a.exists() and b.exists() and c.exists() and e.exists() and not d.exists()
    finally:
        live.kill()
        live.wait()
    # `exclude` is the caller's own goal: a hook never sweeps what it is finishing.
    g = p.goal(37)
    ex = wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, exclude=("37",), run=p.run_fn())
    assert {"goal": "37", "worktree": str(g), "reason": "excluded"} in ex["kept"] and g.exists()


def test_renamed_branch_symlink_and_foreign_paths_are_kept(p, tmp_path):
    wip = p.goal(41)
    git(wip, "branch", "-m", "wip/41-paused")                  # a paused tree: WIP by name
    link_target = tmp_path / "elsewhere"
    link_target.mkdir()
    sym = p.sdlc / "work" / "42"
    sym.symlink_to(link_target)
    p.record(42).write_text(json.dumps({"worktree": str(sym), "branch": "sdlc/42", "pr": "12"}))
    other = tmp_path / "foreign-wt"
    git(p.proj, "worktree", "add", "-q", "-b", "sdlc/43", str(other), "origin/main")
    p.record(43).write_text(json.dumps({"worktree": str(other), "branch": "sdlc/43", "pr": "12"}))
    mine = p.goal(44)
    p.record(45).write_text(json.dumps({"worktree": str(mine), "branch": "sdlc/44", "pr": "12"}))  # 45 -> 44's tree
    hidden = p.goal(46)
    (hidden / ".git").unlink()                                # no `.git` file: git resolves to the MAIN repo
    out = p.sweep()
    got = kept(out)
    assert got["41"] == "branch-mismatch" and got["42"] == "outside-work-root"
    assert got["43"] == "outside-work-root" and got["45"] == "outside-work-root"
    assert got["46"] == "not-a-worktree"
    assert out["removed"] == ["44"] and "44" not in got         # 44 itself is a clean merged tree
    assert wip.exists() and other.exists() and hidden.exists() and link_target.exists()


# --- bounds ----------------------------------------------------------------------------------


def test_dry_run_limit_examine_cap_and_rerun_are_bounded_and_idempotent(p):
    paths = [p.goal(n) for n in range(51, 56)]
    dry = p.sweep("--dry-run")
    assert sorted(e["goal"] for e in dry["removable"]) == ["51", "52", "53", "54", "55"]
    assert all(x.exists() for x in paths) and dry["removed"] == []
    listing = json.loads(p.cli("list-removable").stdout)
    assert len(listing["removable"]) == 5 and listing["reclaimable_bytes"] > 0
    assert all(e["bytes"] > 0 for e in listing["removable"])
    capped = p.sweep("--limit", "2")
    assert len(capped["removed"]) == 2 and capped["deferred"].get("limit") == 3, capped
    examined = p.sweep("--max-examine", "1", "--limit", "0")
    assert len(examined["removed"]) == 1 and examined["deferred"].get("examine-cap") == 4, examined
    rest = p.sweep("--limit", "0")
    assert len(rest["removed"]) == 2
    again = p.sweep()
    assert again["removed"] == [] and all(not x.exists() for x in paths)
    assert len(p.registered()) == 1                              # only the main checkout is left


def test_a_spent_wall_clock_budget_stops_examination_and_removal(p):
    wp = _wp()
    paths = [p.goal(n) for n in (170, 171)]
    out = wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, budget_seconds=0, run=p.run_fn())
    assert out["removed"] == [] and out["deferred"].get("budget") == 2 and all(x.exists() for x in paths), out
    done = wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, budget_seconds=60, run=p.run_fn())
    assert sorted(done["removed"]) == ["170", "171"]


def test_the_sweep_never_forces_and_makes_no_other_call(p):
    wp = _wp()
    clean, dirty = p.goal(61), p.goal(62)
    (dirty / "a.txt").write_text("edited\n")
    seen = []
    before = {f: f.read_bytes() for f in (p.record(61), p.record(62))}
    out = wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, run=p.run_fn(seen=seen))
    assert out["removed"] == ["61"] and kept(out) == {"62": "dirty"}, out
    removes = [a for a in seen if a[:3] == ["git", "worktree", "remove"]]
    assert removes == [["git", "worktree", "remove", str(clean)]], removes   # literal argv, no --force
    assert not any("--force" in a or "-f" in a for a in seen)
    allowed = [["git", "worktree", "remove"], ["git", "worktree", "prune"], ["git", "worktree", "list"]]
    mutating = [a for a in seen if a[:2] == ["git", "worktree"] and a[:3] not in allowed]
    assert mutating == []
    assert not any(a[:2] in (["git", "branch"], ["git", "checkout"], ["git", "reset"], ["git", "push"])
                   for a in seen)
    assert [a for a in seen if a[0] == "gh"] == [["gh", "api", "repos/{owner}/{repo}/pulls/1061"]]
    assert {f: f.read_bytes() for f in before} == before         # records untouched
    assert (dirty / "a.txt").read_text() == "edited\n"


# --- crash recovery ------------------------------------------------------------------------


def _killed_removal(p, damage):
    """A run wrapper that stands for a sweep SIGKILLed inside `git worktree remove`: real git for
    everything else, and on the removal it applies `damage` (a real half-deletion) and dies."""
    def before(cwd, argv):
        if argv[:3] == ["git", "worktree", "remove"]:
            damage(pathlib.Path(argv[3]))
            raise KeyboardInterrupt("killed mid-remove")
        return None
    return p.run_fn(before=before)


def _half_delete(path):
    (path / "a.txt").unlink()
    (path / "d" / "top.txt").unlink()


def test_crash_mid_remove_heals_without_a_human(p):
    wp = _wp()
    path = p.goal(71)
    journal = p.sdlc / "state" / "worktree-prune" / "71.json"
    with pytest.raises(KeyboardInterrupt):
        wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0,
                 run=_killed_removal(p, _half_delete))
    assert journal.exists() and path.exists() and not (path / "a.txt").exists()   # the crash state
    out = p.sweep()                                              # the documented gesture, real git
    assert out["removed"] == ["71"], out
    assert not path.exists() and not journal.exists() and str(path) not in p.registered()
    assert git(p.proj, "rev-parse", "--verify", "refs/heads/sdlc/71").returncode == 0
    # the other half of the crash: the tree vanished but git's admin entry did not
    path2 = p.goal(72)
    with pytest.raises(KeyboardInterrupt):
        import shutil
        wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0,
                 run=_killed_removal(p, lambda x: shutil.rmtree(x)))
    assert not path2.exists() and str(path2) in p.registered()   # a dangling admin entry
    out = p.sweep()
    assert str(path2) not in p.registered(), out
    assert not (p.sdlc / "state" / "worktree-prune" / "72.json").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="permissions do not deny as root: the runner-kill cases carry it")
def test_a_real_failed_removal_leaves_a_state_the_next_sweep_finishes(p):
    path = p.goal(73)
    locked = path / "d" / "sub"
    os.chmod(locked, 0o555)                                      # real git cannot unlink inside it
    try:
        first = p.sweep()
        assert kept(first) == {"73": "remove-failed"} and path.exists(), first
    finally:
        os.chmod(locked, 0o755)
    second = p.sweep()                                           # the heal pass finishes the half-deleted tree
    assert second["healed"] == ["73"] and not path.exists(), second
    assert not (p.sdlc / "state" / "worktree-prune" / "73.json").exists()


def test_a_killed_removal_is_restored_once_then_left_for_a_human(p):
    wp = _wp()
    path = p.goal(74)
    for _ in range(2):                                           # killed twice, back to back
        with pytest.raises(KeyboardInterrupt):
            wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0,
                     run=_killed_removal(p, _half_delete))
    out = p.sweep()
    assert {"goal": "74", "worktree": str(path), "reason": "heal-gave-up"} in out["kept"], out
    assert out["removed"] == [] and path.exists()
    assert not (path / "a.txt").exists()                         # no third restore: no recurring I/O
    assert (p.sdlc / "state" / "worktree-prune" / "74.json").exists()


def _debris(path):
    import shutil
    (path / ".git").unlink()
    shutil.rmtree(path / "d")                                    # tracked files, now gone


def test_debris_with_a_foreign_file_is_kept_even_under_sigmas_ignore_rule(p):
    wp = _wp()
    clean = p.goal(75)
    foreign = p.goal(76)
    for path, name in ((clean, None), (foreign, "mine.txt")):
        goal = path.name
        with pytest.raises(KeyboardInterrupt):
            wp.sweep(p.sdlc, limit=1, max_examine=0, max_pr_reads=0, exclude=({"75", "76"} - {goal}),
                     run=_killed_removal(p, _debris))
        if name:
            (path / name).write_text("irreplaceable\n")
    out = p.sweep()
    assert not clean.exists(), out                               # verified debris is removed
    assert foreign.exists() and (foreign / "mine.txt").read_text() == "irreplaceable\n"
    assert {"goal": "76", "worktree": str(foreign), "reason": "debris-unverified"} in out["kept"], out


def _killed_journal(p, n, damage=lambda path: None):
    """Goal n with a real journal left by a sweep killed inside the removal (after `damage`)."""
    wp = _wp()
    path = p.goal(n)
    with pytest.raises(KeyboardInterrupt):
        wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, run=_killed_removal(p, damage))
    return path


def test_heal_never_restores_over_real_edits_and_needs_the_journalled_head(p):
    """The restore gate: only a status of ` D` lines is ours. An edit or an untracked file beside the
    deletion means a human is in the tree, so nothing is restored (`git checkout -- .` would destroy it).
    A journal whose head is not the tree's head is dropped, never acted on."""
    edited = _killed_journal(p, 150, _half_delete)
    (edited / "d" / "sub" / "deep.txt").write_text("a human's edit\n")
    (edited / "mine.txt").write_text("untracked\n")
    out = p.sweep()
    assert kept(out).get("150") == "dirty" and "150" not in out["removed"], out
    assert not (edited / "a.txt").exists() and (edited / "d" / "sub" / "deep.txt").read_text() == "a human's edit\n"
    assert (edited / "mine.txt").exists()                         # nothing was restored over the edits
    forged = _killed_journal(p, 151, _half_delete)
    journal = p.sdlc / "state" / "worktree-prune" / "151.json"
    data = json.loads(journal.read_text())
    data["head"] = "1" * 40                                       # not the tree's head
    journal.write_text(json.dumps(data))
    out = p.sweep()
    assert kept(out).get("151") == "dirty" and not (forged / "a.txt").exists() and not journal.exists(), out


def test_debris_that_differs_from_the_journalled_head_is_kept(p):
    """The blob proof: a tracked file edited, or a tracked symlink retargeted, in the debris is the
    user's change and the debris is not removed."""
    for n, edit in ((152, lambda path: (path / "a.txt").write_text("edited after the crash\n")),
                    (153, lambda path: ((path / "link").unlink(), (path / "link").symlink_to("d/top.txt")))):
        wp = _wp()
        path = p.goal(n)

        def damage(x):
            import shutil
            (x / ".git").unlink()
            shutil.rmtree(x / "d")

        with pytest.raises(KeyboardInterrupt):
            wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, run=_killed_removal(p, damage))
        edit(path)
        out = p.sweep()
        assert {"goal": str(n), "worktree": str(path), "reason": "debris-unverified"} in out["kept"], out
        assert path.exists()
        # leave nothing for the next case's sweep to trip over
        for f in (p.sdlc / "state" / "worktree-prune").glob(f"{n}.json"):
            f.unlink()
        p.record(n).unlink()
        import shutil
        shutil.rmtree(path)
        git(p.proj, "worktree", "prune")


def test_dry_run_and_list_removable_honour_liveness_and_a_bad_head_sha(p):
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    try:
        agent, odd = p.goal(154), p.goal(155)
        r = subprocess.run([sys.executable, str(S / "loop.py"), "agent-start", str(p.sdlc), "154", "--pid",
                            str(live.pid)], capture_output=True, text=True, env=p.env(), cwd=str(p.proj))
        assert r.returncode == 0, r.stderr
        p.pr_body("1155", head_sha="not-a-sha", head_ref="sdlc/155")
        for argv in (("sweep", "--dry-run"), ("list-removable",)):
            proc = p.cli(*argv)
            assert proc.returncode == 0, proc.stderr
            out = json.loads(proc.stdout)
            assert out["removable"] == [], out
            assert kept(out) == {"154": "live-agent", "155": "pr-mismatch"}, out
        assert agent.exists() and odd.exists()
    finally:
        live.kill()
        live.wait()


def test_a_missing_config_is_an_error_not_an_empty_result(p):
    p.goal(156)
    (p.sdlc / "config.json").unlink()
    proc = p.cli("sweep")
    assert proc.returncode == 1 and "sweep stopped" in proc.stderr and json.loads(proc.stdout)["error"], proc


def test_a_rerun_goal_with_an_old_journal_is_not_wedged(p):
    wp = _wp()
    path = p.goal(77)
    with pytest.raises(KeyboardInterrupt):
        wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, run=_killed_removal(p, lambda x: None))
    journal = p.sdlc / "state" / "worktree-prune" / "77.json"
    assert journal.exists() and path.exists()
    rec = json.loads(p.record(77).read_text())
    rec["base"] = "feature/x"                                    # the goal was re-run: a new record
    p.record(77).write_text(json.dumps(rec))
    out = p.sweep()
    assert not journal.exists() and "77" not in kept(out), out
    assert out["removed"] == ["77"] and not path.exists()
    # a journal that does not match its record must not be believed: a file a human deleted stays a
    # human's deletion (dirty), it is never "restored" as if our removal had done it
    other = p.goal(78)
    stale = p.sdlc / "state" / "worktree-prune" / "78.json"
    stale.write_text(json.dumps({"worktree": str(other), "branch": "sdlc/78", "heals": 0,
                                "head": git(other, "rev-parse", "HEAD").stdout.strip(),
                                "record_sha256": "0" * 64}))
    (other / "a.txt").unlink()
    again = p.sweep()
    assert kept(again) == {"78": "dirty"} and not (other / "a.txt").exists() and not stale.exists(), again


# --- races -------------------------------------------------------------------------------------


def test_a_held_sweep_lock_makes_a_second_sweep_do_nothing(p):
    wp = _wp()
    path = p.goal(81)
    with pytest.raises(KeyboardInterrupt):
        wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, run=_killed_removal(p, _half_delete))
    journal = p.sdlc / "state" / "worktree-prune" / "81.json"
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent('''
        import fcntl, os, sys, time
        fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        print("held", flush=True)
        time.sleep(600)'''), str(p.sdlc / "state" / "worktree-prune.lock")], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        proc = p.cli("sweep", timeout=30)                        # a blocking guard raises TimeoutExpired: RED
        assert proc.returncode == 3 and json.loads(proc.stdout)["busy"] is True, proc
        assert "holds the lock" in proc.stderr
        assert path.exists() and journal.exists() and not (path / "a.txt").exists()   # no heal, no removal
    finally:
        holder.send_signal(signal.SIGKILL)
        holder.wait()


def test_a_commit_landing_between_proof_and_removal_aborts_the_removal(p):
    wp = _wp()
    path = p.goal(82)
    done = []
    real = p.run_fn()

    def run(cwd, argv, timeout=None, stdin=None):
        rc, out = real(cwd, argv, timeout=timeout, stdin=stdin)
        if argv[:2] == ["git", "merge-base"] and not done:       # right after the proof, a session commits
            done.append(True)
            (path / "late.txt").write_text("late\n")
            git(path, "add", "-A")
            git(path, "commit", "-q", "-m", "late work")
        return rc, out

    out = wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, run=run)
    assert out["removed"] == [] and kept(out) == {"82": "moved"}, out
    assert path.exists() and (path / "late.txt").exists()
    assert "late work" in git(path, "log", "-1", "--format=%s").stdout


def test_a_hung_gh_keeps_the_tree(p):
    path = p.goal(83)
    (p.ghdir / "pr-1083.json").write_text("SLEEP")
    start = time.monotonic()
    proc = p.cli("sweep", env=p.env(SIGMA_WATCH_CALL_TIMEOUT="5"), timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert kept(out) == {"83": "pr-unknown"} and path.exists(), out
    assert time.monotonic() - start < 40


def test_pr_reads_are_capped_per_sweep(p):
    for n in (141, 142, 143):
        p.goal(n)
    out = p.sweep("--max-pr-reads", "1", "--limit", "0")
    assert len(out["removed"]) == 1 and sorted(kept(out).values()) == ["deferred", "deferred"], out
    assert len([c for c in p.gh_calls() if c[0] == "api"]) == 1


def test_a_session_registered_between_proof_and_removal_aborts_it(p):
    wp = _wp()
    path = p.goal(144)
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    done = []
    real = p.run_fn()

    def run(cwd, argv, timeout=None, stdin=None):
        out = real(cwd, argv, timeout=timeout, stdin=stdin)
        if argv[:2] == ["git", "merge-base"] and not done:       # after the proof, an agent registers
            done.append(True)
            r = subprocess.run([sys.executable, str(S / "loop.py"), "agent-start", str(p.sdlc), "144",
                                "--pid", str(live.pid)], capture_output=True, text=True, env=p.env(),
                               cwd=str(p.proj))
            assert r.returncode == 0, r.stderr
        return out

    try:
        out = wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, run=run)
        assert kept(out) == {"144": "live-agent"} and path.exists(), out
        # the same window for a SESSION that lists the goal in flight (no agent marker at all)
        second = p.goal(146)
        done2 = []
        real2 = p.run_fn()

        def run2(cwd, argv, timeout=None, stdin=None):
            out2 = real2(cwd, argv, timeout=timeout, stdin=stdin)
            if argv[:2] == ["git", "merge-base"] and argv[-1] == git(second, "rev-parse", "HEAD").stdout.strip() \
                    and not done2:
                done2.append(True)
                sessions = p.sdlc / "state" / "sessions"
                sessions.mkdir(parents=True, exist_ok=True)
                (sessions / f"{live.pid}.active").write_text(json.dumps({"in_flight": ["#146"],
                                                                           "settled_admissions": 1}))
            return out2

        out2 = wp.sweep(p.sdlc, limit=0, max_examine=0, max_pr_reads=0, run=run2, exclude=("144",))
        assert kept(out2) == {"144": "excluded", "146": "live-session"} and second.exists(), out2
    finally:
        live.kill()
        live.wait()


def test_without_flock_the_sweep_refuses_loudly(p, tmp_path):
    path = p.goal(145)
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "fcntl.py").write_text("raise ImportError('no flock on this platform')\n")
    proc = p.cli("sweep", env=p.env(PYTHONPATH=str(shim)))
    assert proc.returncode == 2 and "not supported on this platform" in proc.stderr, proc
    assert path.exists()


# --- readers and triggers ------------------------------------------------------------------


def test_a_swept_goals_record_is_still_cleared_by_finish_and_locked_trees_are_kept(p):
    swept, locked = p.goal(91), p.goal(92)
    git(p.proj, "worktree", "lock", str(locked))
    out = p.sweep()
    assert out["removed"] == ["91"] and kept(out) == {"92": "remove-failed"} and locked.exists(), out
    assert not (p.sdlc / "state" / "worktree-prune" / "92.json").exists()   # git refused: nothing to heal
    fin = subprocess.run([sys.executable, str(S / "work.py"), "finish", str(p.sdlc), "91"], cwd=str(p.proj),
                         capture_output=True, text=True, env=p.env(), timeout=120)
    assert fin.returncode == 0, fin.stderr
    assert not p.record(91).exists()                             # the later `done`/finish still clears it
    assert git(p.proj, "rev-parse", "--verify", "refs/heads/sdlc/91").returncode == 0


def _start(p, env=None):
    return subprocess.run([sys.executable, str(S / "loop.py"), "start", str(p.sdlc), "--session-pid",
                           str(os.getpid())], cwd=str(p.proj), capture_output=True, text=True,
                          env=env or p.env(), timeout=180)


def _age_stamp(p):
    stamp = p.sdlc / "state" / "worktree-prune-seen.json"
    os.utime(stamp, (time.time() - 3600, time.time() - 3600))   # the 15 minute interval has passed
    return stamp


def test_survivors_that_never_qualify_do_not_starve_a_removable_tree(p):
    for n in range(101, 126):
        p.goal(n, pr="")                                         # 25 trees nothing proves landed
    last = p.goal(900)
    p.set_config({"reclaim_merged_worktrees": True})
    first = _start(p)                                            # the AUTOMATIC path: 20 examined, none qualify
    assert first.returncode == 0 and last.exists(), first.stderr
    stamp = p.sdlc / "state" / "worktree-prune-seen.json"
    assert stamp.exists() and json.loads(stamp.read_text()).keys() >= {str(n) for n in range(101, 121)}
    soon = _start(p)                                             # inside the 15 minute interval: no sweep at all
    assert soon.returncode == 0 and last.exists()
    _age_stamp(p)
    second = _start(p)                                           # rotation reaches the newer tree
    assert second.returncode == 0, second.stderr
    assert not last.exists(), "a removable tree behind 25 permanent survivors was never reached"


def test_the_automatic_triggers_are_opt_in_and_fail_open(p, monkeypatch):
    goals = p.sdlc / "goals"
    goals.mkdir(parents=True)
    for name in ("135", "g1"):
        (goals / f"{name}.md").write_text("---\nstatus: in_progress\n---\n# %s\n" % name)
    first, second, third = p.goal(131), p.goal(132), p.goal(133)

    def run(*argv, env=None):
        return subprocess.run([sys.executable, str(S / "loop.py"), *argv], cwd=str(p.proj),
                              capture_output=True, text=True, env=env or p.env(), timeout=180)

    off = _start(p)
    assert off.returncode == 0 and first.exists() and p.gh_calls() == [], off.stderr   # opt-in: off by default
    p.set_config({"reclaim_merged_worktrees": True})
    on = _start(p)
    assert on.returncode == 0, on.stderr
    assert not first.exists() and not second.exists() and not third.exists()
    # `record done` on a PR-bearing goal still fires the trigger (it must not be keyed on merged_pr)
    swept = p.goal(134)
    p.goal(135)                                                  # its PR 1135 is merged per the fake gh
    _age_stamp(p)
    done = run("record", str(p.sdlc), str(goals / "135.md"), "done")
    assert done.returncode == 0, done.stderr
    assert not swept.exists(), done.stderr
    # fail-open: no gh at all must never cost a `start`
    kept_tree = p.goal(136)
    _age_stamp(p)
    broken = _start(p, env=dict(GENV, PATH="/usr/bin:/bin"))
    assert broken.returncode == 0, broken.stderr
    assert kept_tree.exists()
    # the reconcile pass holds merge-reconcile.lock: it passes housekeeping=False and sweeps nothing
    import loop
    import sources
    called = []
    real_load = loop._load

    class Spy:
        @staticmethod
        def after_start_or_done(sdlc_dir, config=None, exclude=()):
            called.append(exclude)

    monkeypatch.setattr(loop, "_load", lambda n: Spy if n == "worktree_prune" else real_load(n))
    config = json.loads((p.sdlc / "config.json").read_text())
    source = sources.get_source(str(p.sdlc), config)
    loop._record(str(p.sdlc), source, str(goals / "g1.md"), "done", "reconciled", housekeeping=False)
    assert called == []
    loop._record(str(p.sdlc), source, str(goals / "g1.md"), "done", "ordinary")
    assert called == [(str(goals / "g1.md"),)]

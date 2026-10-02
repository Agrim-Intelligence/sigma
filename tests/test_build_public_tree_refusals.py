# SPDX-License-Identifier: MIT
"""`tools/build_public_tree.py` (issue 397): every refusal, and the review levels over a REST fake.

A refusal is exit 2, ONE stderr line `build_public_tree: REFUSED [<code>] ...`, nothing on stdout,
no export, no partial and no report file. The review levels' REST calls go through an injected
`run=` fake that delegates `git` to the tool's own `_real_run` and answers `gh api` GETs from canned
data (asserting the GET-only argv); a failing fake `gh` is first on PATH as a second net.

The tool is loaded INSIDE each test body behind `assert TOOL.exists()`, never at import. Parametrize
ids are literals here, never read from the tool. Literal-token rule: nothing token-shaped is spelled
out in this file.
"""
import importlib.util
import itertools
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "build_public_tree.py"

FORBIDDEN_GH_FLAGS = ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input", "--jq")
SYNTHETIC = "# synthetic\nzq-planted-[0-9]+\n"
DISPOSITIONS = "docs/launch/public-tree-dispositions.json"
SLUG = "acme-old/widget-private"
DEFAULT_FILES = {
    ".claude-plugin/plugin.json": '{"name": "demo", "version": "0.0.7"}\n',
    "README.md": "hello public world\n",
    "docs/guide.md": "a guide\n",
    "bin/run.sh": "#!/bin/sh\necho run\n",
}
DEFAULT_EXEC = ("bin/run.sh",)
_COUNTER = itertools.count()


def _load(path, name):
    assert path.exists(), "%s is missing" % path.relative_to(ROOT).as_posix()
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tool():
    assert TOOL.exists(), "tools/build_public_tree.py is missing"
    return _load(TOOL, "build_public_tree_refusals_under_test_%d" % next(_COUNTER))


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    """Data only: HOME in tmp, a failing fake gh first on PATH, no git redirection, an identity."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text("#!/bin/sh\necho UNEXPECTED \"$@\" >> \"$FAKE_GH_LOG\"\nexit 97\n", encoding="utf-8")
    gh.chmod(0o755)
    for name in ("SIGMA_LEAK_PATTERNS", "GIT_DIR", "GIT_WORK_TREE", "GIT_CEILING_DIRECTORIES",
                 "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GH_REPO", "GH_TOKEN",
                 "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "%s:%s" % (bindir, os.environ.get("PATH", "/usr/bin:/bin")))
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.log"))
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "gh-config"))
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(name, "t")
    for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, "test@example.invalid")


def _no_unexpected_gh(tmp_path):
    log = tmp_path / "gh.log"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert not any("UNEXPECTED" in line for line in lines), lines


def _git(repo, *args, input_text=None, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    proc = subprocess.run(["git", "-C", str(repo)] + [str(a) for a in args], input=input_text,
                          capture_output=True, text=True, env=env, check=True)
    return proc.stdout.strip()


_DATES = {"GIT_AUTHOR_DATE": "1600000000 +0000", "GIT_COMMITTER_DATE": "1700000000 +0000"}


def _fixture(tmp_path, extra=None, name="src"):
    """-> (repo, sha): a one-commit repository; its commit is also `refs/remotes/origin/main`."""
    files = dict(DEFAULT_FILES)
    for rel, data in (extra or {}).items():
        files[rel] = data
    repo = tmp_path / name
    repo.mkdir(parents=True)
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "t")
    for rel, data in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        if rel in DEFAULT_EXEC:
            path.chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture", env_extra=_DATES)
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, _git(repo, "rev-parse", "HEAD")


def _second_commit(repo):
    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second", env_extra=_DATES)
    return _git(repo, "rev-parse", "HEAD")


def _plumb(repo, entries):
    """Commit index entries that have no work-tree file: (path, mode, bytes-or-None); a gitlink takes HEAD."""
    _git(repo, "config", "core.ignorecase", "false")
    head = _git(repo, "rev-parse", "HEAD")
    for path, mode, data in entries:
        oid = head if mode == "160000" else _git(repo, "hash-object", "-w", "--stdin", input_text=data)
        _git(repo, "update-index", "--add", "--cacheinfo", "%s,%s,%s" % (mode, oid, path))
    _git(repo, "commit", "-q", "-m", "plumbing", env_extra=_DATES)
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return _git(repo, "rev-parse", "HEAD")


def _patterns(tmp_path):
    path = tmp_path / "patterns.txt"
    path.write_text(SYNTHETIC, encoding="utf-8")
    return path


class Case(object):
    def __init__(self, tmp_path, repo, sha, out=None, extra=(), review="clean", patterns=True, reports=None,
                 commit=None, source=None):
        self.out = out or tmp_path / "out" / "export"
        self.reports = reports or tmp_path / "reports"
        pat = [] if patterns is False else ["--patterns", patterns if patterns is not True else _patterns(tmp_path)]
        self.argv = [commit or sha, "--out", self.out, "--source", source or repo] + pat + \
            ["--review-level", review, "--report-dir", self.reports] + list(extra)


def _listing(path):
    return set(os.listdir(str(path))) if path.is_dir() else set()


def _files_under(path):
    found = []
    for base, _dirs, names in os.walk(str(path)):
        found.extend(os.path.join(base, n) for n in names)
    return found


def _build(mod, capsys, argv, run=None):
    argv = ["build_public_tree.py"] + [str(a) for a in argv]
    rc = mod.main(argv, run=run) if run is not None else mod.main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


def _one_line_refusal(rc, so, se, code):
    assert rc == 2, (rc, se)
    assert so == ""
    lines = se.splitlines()
    assert len(lines) == 1, se
    assert lines[0].startswith("build_public_tree: REFUSED [%s]" % code), lines[0]


# ============================================================================== scenarios

def _s_windows(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    mp.setattr(mod, "_platform_is_windows", lambda: True)
    return Case(tmp_path, repo, sha)


def _s_no_patterns_source(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, patterns=False)


def _s_patterns_inside_work_tree(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    inside = repo / "patterns.txt"
    inside.write_text(SYNTHETIC, encoding="utf-8")
    return Case(tmp_path, repo, sha, patterns=inside)


def _s_patterns_unreadable(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, patterns=tmp_path / "missing-patterns.txt")


def _patterns_with(tmp_path, text):
    path = tmp_path / "odd-patterns.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _s_pattern_compile(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, patterns=_patterns_with(tmp_path, "# synthetic\n(\n"))


def _s_pattern_matches_empty(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, patterns=_patterns_with(tmp_path, "# synthetic\nq*\n"))


def _s_zero_patterns(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, patterns=_patterns_with(tmp_path, "# only a comment\n\n"))


def _s_out_exists(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    case = Case(tmp_path, repo, sha)
    case.out.mkdir(parents=True)
    (case.out / "sentinel.txt").write_text("keep\n", encoding="utf-8")
    return case


def _s_out_exists_rejected(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    case = Case(tmp_path, repo, sha)
    rejected = pathlib.Path(str(case.out) + ".rejected")
    rejected.mkdir(parents=True)
    (rejected / "sentinel.txt").write_text("keep\n", encoding="utf-8")
    return case


def _s_out_inside_work_tree(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, out=repo / "export")


def _s_report_dir_inside_work_tree(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, reports=repo / "reports")


def _s_not_a_repo(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    return Case(tmp_path, repo, sha, source=plain)


def _s_bad_commit(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, commit="0" * 40)


def _s_bad_exclude_dotdot(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, extra=["--exclude", "../docs"])


def _s_bad_exclude_absolute(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, extra=["--exclude", "/docs"])


def _s_exclude_matches_nothing(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, extra=["--exclude", "nothing/here"])


def _s_symlink(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, _plumb(repo, [("link", "120000", "README.md")]))


def _s_gitlink(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, _plumb(repo, [("vendor/sub", "160000", None)]))


def _s_bad_path(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, _plumb(repo, [("docs/bad\x01name.md", "100644", "x\n")]))


def _s_case_collision(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, _plumb(repo, [("docs/Case.md", "100644", "one\n"), ("docs/case.md", "100644", "two\n")]))


def _s_secret_file_name(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path, extra={"id_rsa": "not really a key\n"})
    return Case(tmp_path, repo, sha)


def _s_head_not_commit(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    _second_commit(repo)
    return Case(tmp_path, repo, sha)


def _s_dirty(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    (repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    return Case(tmp_path, repo, sha)


def _s_tool_mismatch(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path, extra={"tools/leak_refs.py": "print('a different copy')\n"})
    return Case(tmp_path, repo, sha)


def _s_binary(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path, extra={"data.bin": b"a\x00b\n"})
    return Case(tmp_path, repo, sha)


def _s_oversize(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path, extra={"big.txt": b"x" * (2 * 1024 * 1024 + 1)})
    return Case(tmp_path, repo, sha)


def _s_non_utf8(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path, extra={"latin.txt": b"caf\xe9\n"})
    return Case(tmp_path, repo, sha)


def _s_no_remote_ref(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    return Case(tmp_path, repo, sha, review="landed")


def _s_not_landed(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    second = _second_commit(repo)
    return Case(tmp_path, repo, second, review="landed")


def _s_repo_required(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, review="pr-merged")


def _s_bad_slug(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path)
    return Case(tmp_path, repo, sha, review="pr-merged", extra=["--repo", "not a slug"])


def _s_dispositions_malformed(tmp_path, mod, mp):
    repo, sha = _fixture(tmp_path, extra={DISPOSITIONS: "{not json"})
    return Case(tmp_path, repo, sha)


SCENARIOS = {
    "windows": _s_windows,
    "no-patterns-source": _s_no_patterns_source,
    "patterns-inside-work-tree": _s_patterns_inside_work_tree,
    "patterns-unreadable": _s_patterns_unreadable,
    "pattern-compile": _s_pattern_compile,
    "pattern-matches-empty": _s_pattern_matches_empty,
    "zero-patterns": _s_zero_patterns,
    "out-exists": _s_out_exists,
    "out-exists-rejected": _s_out_exists_rejected,
    "out-inside-work-tree": _s_out_inside_work_tree,
    "report-dir-inside-work-tree": _s_report_dir_inside_work_tree,
    "not-a-repo": _s_not_a_repo,
    "bad-commit": _s_bad_commit,
    "bad-exclude-dotdot": _s_bad_exclude_dotdot,
    "bad-exclude-absolute": _s_bad_exclude_absolute,
    "exclude-matches-nothing": _s_exclude_matches_nothing,
    "symlink": _s_symlink,
    "gitlink": _s_gitlink,
    "bad-path": _s_bad_path,
    "case-collision": _s_case_collision,
    "secret-file-name": _s_secret_file_name,
    "head-not-commit": _s_head_not_commit,
    "dirty": _s_dirty,
    "tool-mismatch": _s_tool_mismatch,
    "binary": _s_binary,
    "oversize": _s_oversize,
    "non-utf8": _s_non_utf8,
    "no-remote-ref": _s_no_remote_ref,
    "not-landed": _s_not_landed,
    "repo-required": _s_repo_required,
    "bad-slug": _s_bad_slug,
    "dispositions-malformed": _s_dispositions_malformed,
}


@pytest.mark.parametrize("scenario,code", [
    ("windows", "windows"),
    ("no-patterns-source", "no-patterns-source"),
    ("patterns-inside-work-tree", "patterns-inside-work-tree"),
    ("patterns-unreadable", "patterns-unreadable"),
    ("pattern-compile", "pattern-compile"),
    ("pattern-matches-empty", "pattern-matches-empty"),
    ("zero-patterns", "zero-patterns"),
    ("out-exists", "out-exists"),
    ("out-exists-rejected", "out-exists"),
    ("out-inside-work-tree", "out-inside-work-tree"),
    ("report-dir-inside-work-tree", "report-dir-inside-work-tree"),
    ("not-a-repo", "not-a-repo"),
    ("bad-commit", "bad-commit"),
    ("bad-exclude-dotdot", "bad-exclude"),
    ("bad-exclude-absolute", "bad-exclude"),
    ("exclude-matches-nothing", "exclude-matches-nothing"),
    ("symlink", "symlink"),
    ("gitlink", "gitlink"),
    ("bad-path", "bad-path"),
    ("case-collision", "case-collision"),
    ("secret-file-name", "secret-file-name"),
    ("head-not-commit", "head-not-commit"),
    ("dirty", "dirty"),
    ("tool-mismatch", "tool-mismatch"),
    ("binary", "binary"),
    ("oversize", "oversize"),
    ("non-utf8", "non-utf8"),
    ("no-remote-ref", "no-remote-ref"),
    ("not-landed", "not-landed"),
    ("repo-required", "repo-required"),
    ("bad-slug", "bad-slug"),
    ("dispositions-malformed", "dispositions-malformed"),
], ids=["windows", "no-patterns-source", "patterns-inside-work-tree", "patterns-unreadable", "pattern-compile",
        "pattern-matches-empty", "zero-patterns", "out-exists", "out-exists-rejected", "out-inside-work-tree",
        "report-dir-inside-work-tree", "not-a-repo", "bad-commit", "bad-exclude-dotdot", "bad-exclude-absolute",
        "exclude-matches-nothing", "symlink", "gitlink", "bad-path", "case-collision", "secret-file-name",
        "head-not-commit", "dirty", "tool-mismatch", "binary", "oversize", "non-utf8", "no-remote-ref",
        "not-landed", "repo-required", "bad-slug", "dispositions-malformed"])
def test_refusals(tmp_path, capsys, monkeypatch, scenario, code):
    mod = _tool()
    case = SCENARIOS[scenario](tmp_path, mod, monkeypatch)
    before_out = _listing(case.out.parent)
    before_reports = set(_files_under(case.reports))
    rc, so, se = _build(mod, capsys, case.argv)
    _one_line_refusal(rc, so, se, code)
    assert _listing(case.out.parent) <= before_out, "left behind: %s" % sorted(_listing(case.out.parent) - before_out)
    assert set(_files_under(case.reports)) == before_reports
    if scenario in ("out-exists", "out-exists-rejected"):
        kept = case.out if scenario == "out-exists" else pathlib.Path(str(case.out) + ".rejected")
        assert (kept / "sentinel.txt").read_text(encoding="utf-8") == "keep\n"
    else:
        assert not case.out.exists()
    _no_unexpected_gh(tmp_path)


def test_scenarios_cover_exactly_the_parametrized_ids():
    mod = _tool()
    assert sorted(SCENARIOS) == sorted([
        "windows", "no-patterns-source", "patterns-inside-work-tree", "patterns-unreadable", "pattern-compile",
        "pattern-matches-empty", "zero-patterns", "out-exists", "out-exists-rejected", "out-inside-work-tree",
        "report-dir-inside-work-tree", "not-a-repo", "bad-commit", "bad-exclude-dotdot", "bad-exclude-absolute",
        "exclude-matches-nothing", "symlink", "gitlink", "bad-path", "case-collision", "secret-file-name",
        "head-not-commit", "dirty", "tool-mismatch", "binary", "oversize", "non-utf8", "no-remote-ref",
        "not-landed", "repo-required", "bad-slug", "dispositions-malformed"])


def test_env_example_is_not_a_secret_file_name(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path, extra={".env.example": "NAME=value\n"})
    case = Case(tmp_path, repo, sha)
    rc, so, se = _build(mod, capsys, case.argv)
    assert rc == 0, se
    assert (case.out / ".env.example").read_text(encoding="utf-8") == "NAME=value\n"


def test_unknown_flag_is_an_argparse_error(tmp_path):
    mod = _tool()
    git_dir = os.path.dirname(subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip() or "/usr/bin/git")
    env = {"PATH": "%s:%s:/usr/bin:/bin" % (tmp_path / "fakebin", git_dir), "HOME": str(tmp_path / "home")}
    proc = subprocess.run([sys.executable, str(TOOL), "--no-such-flag"], cwd=str(tmp_path), env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "unrecognized arguments: --no-such-flag" in proc.stderr


# ============================================================================== review levels over a REST fake

def _rest_world(sha, **over):
    pr = {"number": 7, "merged_at": "2026-09-01T00:00:00Z", "merge_commit_sha": sha, "base": {"ref": "main"},
          "user": {"login": "alice"}}
    world = {
        "repos/%s" % SLUG: (0, {"private": True, "full_name": SLUG}, ""),
        "repos/%s/commits/%s/pulls" % (SLUG, sha): (0, [pr], ""),
        "repos/%s/pulls/7" % SLUG: (0, dict(pr, merged_by={"login": "alice"}), ""),
        "repos/%s/branches/main/protection" % SLUG: (1, None, "gh: Branch not protected (HTTP 404)"),
        "repos/%s/collaborators/alice/permission" % SLUG: (0, {"role_name": "admin", "permission": "admin"}, ""),
    }
    world.update(over)
    return world


class Fake(object):
    """`run=`: git is delegated to the tool's own `_real_run`; `gh api` GETs are answered from `world`."""

    def __init__(self, mod, world):
        self.mod, self.world, self.gh_calls = mod, world, []

    def __call__(self, args, input_text=None, timeout=60):
        args = [str(a) for a in args]
        if args[0] == "git":
            return self.mod._real_run(args, input_text, timeout)
        assert args[0] == "gh" and args[1] == "api", args
        for flag in FORBIDDEN_GH_FLAGS:
            assert flag not in args, args
        self.gh_calls.append(args)
        endpoint = args[2].split("?")[0]
        assert endpoint in self.world, "unexpected endpoint %s" % endpoint
        rc, body, err = self.world[endpoint]
        return rc, ("" if body is None else json.dumps(body)), err

    def endpoints(self):
        return [a[2].split("?")[0] for a in self.gh_calls]


def _rest_case(tmp_path, level, name="src", **world_over):
    repo, sha = _fixture(tmp_path, name=name)
    case = Case(tmp_path, repo, sha, review=level, extra=["--repo", SLUG])
    return repo, sha, case, world_over


def _report_of(stdout):
    for line in stdout.splitlines():
        if line.startswith("report: "):
            path = pathlib.Path(os.path.expanduser(line[len("report: "):]))
            return json.loads(path.read_text(encoding="utf-8")), path.with_suffix(".md").read_text(encoding="utf-8")
    raise AssertionError("no report line in %r" % stdout)


def test_pr_merged_direct_push_is_refused(tmp_path, capsys):
    mod = _tool()
    repo, sha, case, _ = _rest_case(tmp_path, "pr-merged")
    fake = Fake(mod, _rest_world(sha, **{"repos/%s/commits/%s/pulls" % (SLUG, sha): (0, [], "")}))
    rc, so, se = _build(mod, capsys, case.argv, run=fake)
    _one_line_refusal(rc, so, se, "not-pr-merged")
    assert fake.gh_calls, "the builder never asked the REST fake anything"
    assert not case.out.exists()


def test_pr_merged_refuses_a_public_review_repository(tmp_path, capsys):
    mod = _tool()
    repo, sha, case, _ = _rest_case(tmp_path, "pr-merged")
    fake = Fake(mod, _rest_world(sha, **{"repos/%s" % SLUG: (0, {"private": False, "full_name": SLUG}, "")}))
    rc, so, se = _build(mod, capsys, case.argv, run=fake)
    _one_line_refusal(rc, so, se, "review-repo-public")
    assert not case.out.exists()


def test_pr_merged_same_account_is_reached_but_not_independent(tmp_path, capsys):
    mod = _tool()
    repo, sha, case, _ = _rest_case(tmp_path, "pr-merged")
    fake = Fake(mod, _rest_world(sha))
    rc, so, se = _build(mod, capsys, case.argv, run=fake)
    assert rc == 0, se
    report, md = _report_of(so)
    review = report["review"]
    assert review["requested"] == "pr-merged" and review["reached"] == "pr-merged"
    assert review["independent"] is False
    assert review["branch_protection"] == "none"
    for level in ("clean", "landed", "pr-merged"):
        assert review["levels"][level]["checked"] is True and review["levels"][level]["ok"] is True, level
    assert review["levels"]["owner-merged"]["checked"] is False
    assert "one account opened and merged: not independent review" in md
    assert "reviewed commit" not in md
    endpoints = fake.endpoints()
    for expected in ("repos/%s" % SLUG, "repos/%s/commits/%s/pulls" % (SLUG, sha), "repos/%s/pulls/7" % SLUG,
                     "repos/%s/branches/main/protection" % SLUG):
        assert expected in endpoints, expected
    assert "repos/%s/collaborators/alice/permission" % SLUG not in endpoints
    _no_unexpected_gh(tmp_path)


def test_a_different_merger_is_independent(tmp_path, capsys):
    mod = _tool()
    repo, sha, case, _ = _rest_case(tmp_path, "pr-merged")
    world = _rest_world(sha)
    pr = dict(world["repos/%s/pulls/7" % SLUG][1], merged_by={"login": "bob"})
    world["repos/%s/pulls/7" % SLUG] = (0, pr, "")
    rc, so, se = _build(mod, capsys, case.argv, run=Fake(mod, world))
    assert rc == 0, se
    report, md = _report_of(so)
    assert report["review"]["independent"] is True
    assert "not independent review" not in md


@pytest.mark.parametrize("body,reached", [
    ({"role_name": "admin", "permission": "admin"}, True),
    ({"role_name": "maintain", "permission": "write"}, True),
    ({"role_name": "write", "permission": "write"}, False),
    ({"role_name": "read", "permission": "read"}, False),
    ({"permission": "admin"}, True),
    ({"permission": "write"}, False),
], ids=["role-admin", "role-maintain", "role-write", "role-read", "fallback-admin", "fallback-write"])
def test_owner_merged_needs_admin_or_maintain(tmp_path, capsys, body, reached):
    mod = _tool()
    repo, sha, case, _ = _rest_case(tmp_path, "owner-merged")
    fake = Fake(mod, _rest_world(sha, **{"repos/%s/collaborators/alice/permission" % SLUG: (0, body, "")}))
    rc, so, se = _build(mod, capsys, case.argv, run=fake)
    assert "repos/%s/collaborators/alice/permission" % SLUG in fake.endpoints()
    if reached:
        assert rc == 0, se
        report, _md = _report_of(so)
        assert report["review"]["requested"] == "owner-merged" and report["review"]["reached"] == "owner-merged"
        assert report["review"]["levels"]["owner-merged"]["ok"] is True
    else:
        _one_line_refusal(rc, so, se, "not-owner-merged")
        assert not case.out.exists()


def test_branch_protection_is_measured_three_ways(tmp_path, capsys):
    mod = _tool()
    results = {}
    for label, answer in (("present", (0, {"required_status_checks": {"strict": True}}, "")),
                          ("none", (1, None, "gh: Branch not protected (HTTP 404)")),
                          ("unreadable", (1, None, "gh: Resource not accessible (HTTP 403)"))):
        repo, sha = _fixture(tmp_path, name="p-" + label)
        case = Case(tmp_path, repo, sha, out=tmp_path / ("o-" + label) / "export", review="pr-merged",
                    extra=["--repo", SLUG])
        fake = Fake(mod, _rest_world(sha, **{"repos/%s/branches/main/protection" % SLUG: answer}))
        rc, so, se = _build(mod, capsys, case.argv, run=fake)
        assert rc == 0, (label, se)
        results[label] = _report_of(so)[0]["review"]["branch_protection"]
    assert results == {"present": "present", "none": "none", "unreadable": "unreadable"}


def test_gh_failure_refuses(tmp_path, capsys):
    mod = _tool()
    for endpoint in ("repos/%s" % SLUG, None):
        repo, sha = _fixture(tmp_path, name="f-%s" % ("repo" if endpoint else "pulls"))
        case = Case(tmp_path, repo, sha, out=tmp_path / ("o-%s" % bool(endpoint)) / "export", review="pr-merged",
                    extra=["--repo", SLUG])
        key = endpoint or "repos/%s/commits/%s/pulls" % (SLUG, sha)
        fake = Fake(mod, _rest_world(sha, **{key: (1, None, "gh: boom (HTTP 500)")}))
        rc, so, se = _build(mod, capsys, case.argv, run=fake)
        _one_line_refusal(rc, so, se, "gh-failed")
        assert not case.out.exists()


def test_rest_calls_are_get_only_and_offline_levels_make_none(tmp_path, capsys):
    mod = _tool()
    for level in ("clean", "landed"):
        repo, sha = _fixture(tmp_path, name="off-" + level)
        case = Case(tmp_path, repo, sha, out=tmp_path / ("o-" + level) / "export", review=level)
        fake = Fake(mod, {})
        rc, so, se = _build(mod, capsys, case.argv, run=fake)
        assert rc == 0, (level, se)
        assert fake.gh_calls == [], level
        report, md = _report_of(so)
        assert report["review"]["reached"] == level
    assert report["review"]["levels"]["landed"]["facts"]["commits_past"] == 0
    assert "does not fetch" in md
    repo, sha, case, _ = _rest_case(tmp_path, "owner-merged", name="on")
    fake = Fake(mod, _rest_world(sha))
    rc, so, se = _build(mod, capsys, case.argv, run=fake)
    assert rc == 0, se
    assert len(fake.gh_calls) >= 5
    for args in fake.gh_calls:
        assert args[:2] == ["gh", "api"]
        assert not [a for a in args if a in FORBIDDEN_GH_FLAGS]
    _no_unexpected_gh(tmp_path)


def test_landed_records_the_distance_behind_the_remote_ref(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path)
    second = _second_commit(repo)
    _git(repo, "update-ref", "refs/remotes/origin/main", second)
    _git(repo, "checkout", "-q", "--detach", sha)
    case = Case(tmp_path, repo, sha, review="landed")
    rc, so, se = _build(mod, capsys, case.argv)
    assert rc == 0, se
    report, _md = _report_of(so)
    assert report["review"]["levels"]["landed"]["facts"]["commits_past"] == 1

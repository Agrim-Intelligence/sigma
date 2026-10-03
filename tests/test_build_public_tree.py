# SPDX-License-Identifier: MIT
"""`tools/build_public_tree.py` (issue 397): one reviewed commit becomes the tree of a fresh
one-commit export, deterministic and scanned, with its report outside every repository.

Every test builds a throwaway repository under tmp_path with real git (never the real tree) and
runs the builder over it, in-process (`mod.main(argv)`, `capsys`) or as a child (`_cli`, the gesture
the docs give). `gh` is never reached: a failing fake `gh` is first on PATH (it logs `UNEXPECTED`
and exits 97) and REST tests inject `run=` (see the refusals file). The patterns are synthetic
(`zq-planted-[0-9]+`). The tool is loaded INSIDE each test body behind `assert TOOL.exists()`
(`_tool()`), never at import and never in a fixture, so an absent tool is an ASSERTION red.

Literal-token rule: every placeholder, key-header, e-mail and key-body below is assembled from
fragments at run time, so this file passes the repo's own scanners.
"""
import ast
import importlib.util
import inspect
import itertools
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "build_public_tree.py"
LEAK_REFS = ROOT / "tools" / "leak_refs.py"
LEAK_SCAN = ROOT / "tools" / "leak_scan.py"
WRITE_SURFACE = ROOT / "tools" / "readiness" / "write_surface.py"

SCHEMA = "sigma.public-tree-report/v1"
REPORT_KEYS = {"schema", "verdict", "finalise", "generated_at", "source", "tools", "review", "sdlc",
               "exclusions", "patterns", "export", "dispositions", "scans", "not_covered", "timings"}
FORBIDDEN_GH_FLAGS = ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input", "--jq")
SYNTHETIC = "# synthetic\nzq-planted-[0-9]+\n"
DISPOSITIONS = "docs/launch/public-tree-dispositions.json"
EXPOSURE_ALLOWLIST = "docs/launch/exposure-allowlist.json"
SOURCE_DATE = "1700000000"
AUTHOR_DATE = "1600000000"

DEFAULT_FILES = {
    ".claude-plugin/plugin.json": '{"name": "demo", "version": "0.0.7"}\n',
    "README.md": "hello public world\n",
    "docs/guide.md": "a guide\n",
    "src/app.py": "print('app')\n",
    "bin/run.sh": "#!/bin/sh\necho run\n",
}
DEFAULT_EXEC = ("bin/run.sh",)
_COUNTER = itertools.count()


# ------------------------------------------------------------------------------ loading

def _load(path, name):
    assert path.exists(), "%s is missing" % path.relative_to(ROOT).as_posix()
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tool():
    """A FRESH copy of the builder (a unique module name per call), behind an existence assert."""
    assert TOOL.exists(), "tools/build_public_tree.py is missing"
    return _load(TOOL, "build_public_tree_under_test_%d" % next(_COUNTER))


# ------------------------------------------------------------------------------ literals built at run time

def _placeholder():
    return "<" + "OWNER:"


def _header(kind="RSA "):
    return "-" * 5 + "BEGIN " + kind + "PRIVATE KEY" + "-" * 5


def _body():
    return ("QUJD" * 16 + "\n") * 3


def _email():
    return "dev" + "@" + "acme.test"


# ------------------------------------------------------------------------------ environment

def _gh_script():
    return "#!/bin/sh\necho UNEXPECTED \"$@\" >> \"$FAKE_GH_LOG\"\nexit 97\n"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    """Data only: HOME in tmp, a failing fake gh first on PATH, no git redirection, an identity."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(_gh_script(), encoding="utf-8")
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


# ------------------------------------------------------------------------------ git fixtures

def _git(repo, *args, input_text=None, env_extra=None):
    """Fixture-side git (never the tool under test): stdout, stripped."""
    env = dict(os.environ)
    env.update(env_extra or {})
    proc = subprocess.run(["git", "-C", str(repo)] + [str(a) for a in args], input=input_text,
                          capture_output=True, text=True, env=env, check=True)
    return proc.stdout.strip()


def _fixture(tmp_path, files=None, exec_paths=None, name="src", extra=None):
    """-> (repo, sha): a one-commit repository whose commit is also `refs/remotes/origin/main`.
    `extra` adds files (a `None` value drops one). Author date and committer date differ on purpose."""
    files = dict(DEFAULT_FILES if files is None else files)
    for rel, text in (extra or {}).items():
        if text is None:
            files.pop(rel, None)
        else:
            files[rel] = text
    exec_paths = DEFAULT_EXEC if exec_paths is None else exec_paths
    repo = tmp_path / name
    repo.mkdir(parents=True)
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "t")
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        if rel in exec_paths:
            path.chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture",
         env_extra={"GIT_AUTHOR_DATE": AUTHOR_DATE + " +0000", "GIT_COMMITTER_DATE": SOURCE_DATE + " +0000"})
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, _git(repo, "rev-parse", "HEAD")


def _patterns(tmp_path):
    path = tmp_path / "patterns.txt"
    path.write_text(SYNTHETIC, encoding="utf-8")
    return path


def _out(tmp_path, name="out"):
    return tmp_path / name / "export"


def _argv(tmp_path, repo, sha, out=None, extra=(), review="clean"):
    out = out or _out(tmp_path)
    return [sha, "--out", out, "--source", repo, "--patterns", _patterns(tmp_path), "--review-level", review,
            "--report-dir", tmp_path / "reports"] + list(extra)


def _build(mod, capsys, argv, run=None):
    argv = ["build_public_tree.py"] + [str(a) for a in argv]
    rc = mod.main(argv, run=run) if run is not None else mod.main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


def _cli(tmp_path, argv, home=None, cwd=None):
    """The documented gesture as a child: an environment built from scratch, a failing fake gh first."""
    assert TOOL.exists(), "tools/build_public_tree.py is missing"
    home = pathlib.Path(home) if home else tmp_path / "home"
    git_dir = os.path.dirname(shutil.which("git") or "/usr/bin/git")
    env = {"PATH": "%s:%s:/usr/bin:/bin" % (tmp_path / "fakebin", git_dir), "HOME": str(home),
           "GH_CONFIG_DIR": str(tmp_path / "gh-config"), "FAKE_GH_LOG": str(tmp_path / "gh.log"),
           "GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    proc = subprocess.run([sys.executable, str(TOOL)] + [str(a) for a in argv], cwd=str(cwd or tmp_path),
                          env=env, capture_output=True, text=True, timeout=600)
    _no_unexpected_gh(tmp_path)
    return proc


def _fields(stdout):
    """`key: value` lines of the builder's stdout, in a dict (the `scans` line keeps its text)."""
    fields = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            fields[key] = value
    return fields


def _report_path(fields):
    return pathlib.Path(os.path.expanduser(fields["report"]))


def _report(fields):
    return json.loads(_report_path(fields).read_text(encoding="utf-8"))


def _md(fields):
    return _report_path(fields).with_suffix(".md").read_text(encoding="utf-8")


def _count(value):
    return value if isinstance(value, int) else len(value)


def _snapshot(repo):
    """Every file under the source's object store and refs (path -> size), plus the index bytes."""
    seen = {}
    for sub in ("objects", "refs"):
        for base, _dirs, names in os.walk(str(repo / ".git" / sub)):
            for name in names:
                full = os.path.join(base, name)
                seen[os.path.relpath(full, str(repo))] = os.path.getsize(full)
    seen["index"] = (repo / ".git" / "index").read_bytes()
    return seen


def _ls_tree_entries(repo, sha):
    raw = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", sha],
                         capture_output=True, check=True).stdout
    entries = []
    for row in raw.split(b"\0"):
        if row:
            meta, _tab, path = row.partition(b"\t")
            mode, _kind, oid = meta.decode().split()
            entries.append((path.decode("utf-8"), mode, oid))
    return entries


def _gh_violations(source):
    """-> (how many `["gh", ...]` literals, [violations]) in a module's source."""
    found, bad = 0, []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "gh":
                found += 1
                second = node.elts[1] if len(node.elts) > 1 else None
                if not (isinstance(second, ast.Constant) and second.value == "api"):
                    bad.append(("not-api", node.lineno))
                for elt in node.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str) \
                            and elt.value in FORBIDDEN_GH_FLAGS:
                        bad.append(("write-flag", node.lineno))
    return found, bad


# ============================================================================== determinism

def test_same_commit_twice_gives_same_tree_and_export_commit(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path)
    runs = []
    for n in (1, 2):
        out = _out(tmp_path, "out%d" % n)
        rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=out))
        assert rc == 0, se
        runs.append((out, _fields(so)))
    (out1, f1), (out2, f2) = runs
    assert f1["verdict"] == f2["verdict"] == "VERIFIED"
    assert f1["commit"] == f2["commit"] == sha
    assert f1["tree"] == f2["tree"] == _git(repo, "rev-parse", sha + "^{tree}")
    assert re.fullmatch(r"[0-9a-f]{40}", f1["export-commit"])
    assert f1["export-commit"] == f2["export-commit"]
    for out, fields in runs:
        assert _git(out, "rev-parse", "HEAD") == fields["export-commit"]
        assert _git(out, "rev-parse", "HEAD^{tree}") == fields["tree"]
    reports = sorted((tmp_path / "reports").glob("*.json"))
    assert len(reports) == 2 and reports[0].name != reports[1].name
    _no_unexpected_gh(tmp_path)


def test_one_byte_change_changes_the_tree(tmp_path, capsys):
    mod = _tool()
    repo1, sha1 = _fixture(tmp_path, name="src1")
    repo2, sha2 = _fixture(tmp_path, name="src2", extra={"README.md": "hello public worlD\n"})
    rc1, so1, se1 = _build(mod, capsys, _argv(tmp_path, repo1, sha1, out=_out(tmp_path, "o1")))
    rc2, so2, se2 = _build(mod, capsys, _argv(tmp_path, repo2, sha2, out=_out(tmp_path, "o2")))
    assert (rc1, rc2) == (0, 0), (se1, se2)
    f1, f2 = _fields(so1), _fields(so2)
    assert f1["tree"] == _git(repo1, "rev-parse", sha1 + "^{tree}")
    assert f2["tree"] == _git(repo2, "rev-parse", sha2 + "^{tree}")
    assert f1["tree"] != f2["tree"]
    assert f1["export-commit"] != f2["export-commit"]


def test_corrupted_byte_after_materialise_is_refused(tmp_path, capsys, monkeypatch):
    mod = _tool()
    repo, sha = _fixture(tmp_path)
    real = mod.materialise
    calls = []

    def corrupting(*args, **kwargs):
        result = real(*args, **kwargs)
        dest = pathlib.Path(args[2] if len(args) > 2 else kwargs["dest"])
        target = dest / "README.md"
        data = bytearray(target.read_bytes())
        data[0] ^= 0x20
        target.write_bytes(bytes(data))
        calls.append(str(dest))
        return result

    monkeypatch.setattr(mod, "materialise", corrupting)
    out = _out(tmp_path)
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=out))
    assert calls, "the builder never called materialise by its module-level name"
    assert rc == 2
    assert so == ""
    assert se.startswith("build_public_tree: REFUSED [tree-mismatch]"), se
    assert not out.exists()
    assert list(out.parent.glob(out.name + "*")) == []
    reports = tmp_path / "reports"
    assert not reports.exists() or list(reports.iterdir()) == []


def test_planned_trees_agree_and_source_objects_are_untouched(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path)
    before = _snapshot(repo)
    counts = _git(repo, "count-objects", "-v")
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha))
    assert rc == 0, se
    export = _report(_fields(so))["export"]
    expected = _git(repo, "rev-parse", sha + "^{tree}")
    assert re.fullmatch(r"[0-9a-f]{40}", expected)
    assert export["planned_tree_git"] == export["planned_tree_python"] == expected
    assert export["actual_tree_disk"] == export["export_tree"] == expected
    assert _git(repo, "count-objects", "-v") == counts
    assert _snapshot(repo) == before


def test_tree_id_matches_git_for_nested_and_executable_paths(tmp_path):
    mod = _tool()
    files = {"a.txt": "a\n", "dir.txt": "dot sorts before the slash\n", "dir-x": "dash\n",
             "dir0": "zero sorts after the slash\n", "dir/b.txt": "b\n", "dir/sub/c.sh": "#!/bin/sh\n",
             "zz/\u00e9.txt": "accent\n", ".hidden": "h\n"}
    repo, sha = _fixture(tmp_path, files=files, exec_paths=("dir/sub/c.sh",))
    entries = _ls_tree_entries(repo, sha)
    assert {mode for _p, mode, _o in entries} == {"100644", "100755"}
    expected = _git(repo, "rev-parse", sha + "^{tree}")
    assert mod.tree_id(entries) == expected
    assert mod.tree_id(list(reversed(entries))) == expected
    for data in (b"hello\n", b"", b"\x00\xff binary"):
        stdout = subprocess.run(["git", "-C", str(repo), "hash-object", "--stdin"], input=data,
                                capture_output=True, check=True).stdout.decode().strip()
        assert mod.blob_id(data) == stdout


def test_disk_tree_matches_git_write_tree(tmp_path):
    mod = _tool()
    tree = tmp_path / "plain"
    for rel, text, mode in (("a.txt", "alpha\n", 0o644), ("sub/b.sh", "#!/bin/sh\n", 0o755),
                            ("sub/deep/c.txt", "deep\n", 0o644)):
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
    gitdir = tmp_path / "gitdir"
    env = dict(os.environ, GIT_DIR=str(gitdir), GIT_WORK_TREE=str(tree))
    for args in (["init", "-q"], ["-c", "core.autocrlf=false", "add", "-A"]):
        subprocess.run(["git"] + args, env=env, check=True, capture_output=True, cwd=str(tree))
    expected = subprocess.run(["git", "write-tree"], env=env, check=True, capture_output=True,
                              cwd=str(tree)).stdout.decode().strip()
    assert re.fullmatch(r"[0-9a-f]{40}", expected)
    assert mod.disk_tree(tree) == expected


# ============================================================================== export shape

def test_export_is_one_commit_with_fixed_identity_date_and_message(tmp_path, capsys):
    mod = _tool()
    assert mod.AUTHOR_NAME == "sigma-public-snapshot"
    assert mod.AUTHOR_EMAIL == "noreply@users.noreply.github.com"
    repo, sha = _fixture(tmp_path)
    assert _git(repo, "log", "-1", "--format=%ct") == SOURCE_DATE
    assert _git(repo, "log", "-1", "--format=%at") == AUTHOR_DATE
    out = _out(tmp_path)
    old = os.umask(0o077)
    try:
        rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=out))
    finally:
        os.umask(old)
    assert rc == 0, se
    assert se == ""
    lines = so.splitlines()
    assert [ln.partition(": ")[0] for ln in lines] == ["verdict", "commit", "tree", "export-commit", "out",
                                                         "report", "scans"]
    fields = _fields(so)
    assert fields["verdict"] == "VERIFIED"
    assert re.fullmatch(r"builder=0 leak_refs=0/0 exposure=0/0\+0 leak_scan=absent", fields["scans"])
    commit = _git(out, "cat-file", "-p", "HEAD")
    header = commit.split("\n\n", 1)[0].splitlines()
    assert not any(ln.startswith("parent ") for ln in header)
    ident = "sigma-public-snapshot <noreply@users.noreply.github.com> %s +0000" % SOURCE_DATE
    assert "author " + ident in header
    assert "committer " + ident in header
    assert _git(out, "log", "-1", "--format=%B").strip() == "Initial public snapshot (Sigma 0.0.7)"
    assert sha not in commit and sha[:12] not in commit
    assert _git(out, "rev-list", "--count", "HEAD") == "1"
    assert _git(out, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert _git(out, "for-each-ref", "--format=%(refname)") == "refs/heads/main"
    assert _git(out, "status", "--porcelain=v1", "--untracked-files=all", "--ignored") == ""
    hooks = out / ".git" / "hooks"
    assert not hooks.exists() or list(hooks.iterdir()) == []
    seen = 0
    for base, dirs, names in os.walk(str(out)):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            full = pathlib.Path(base) / name
            rel = full.relative_to(out).as_posix()
            want = 0o755 if rel in DEFAULT_EXEC else 0o644
            assert (full.stat().st_mode & 0o777) == want, rel
            seen += 1
    assert seen == len(DEFAULT_FILES)
    report = _report(fields)
    assert report["export"]["files"] == len(DEFAULT_FILES)
    assert report["export"]["modes"] == {"100644": len(DEFAULT_FILES) - 1, "100755": 1}
    assert report["export"]["commit"] == fields["export-commit"]
    assert report["export"]["message"] == "Initial public snapshot (Sigma 0.0.7)"


# ============================================================================== exclusions

def test_sdlc_is_excluded_by_default_and_recorded_as_default(tmp_path, capsys):
    mod = _tool()
    assert mod.SDLC_DEFAULT == "exclude"
    repo, sha = _fixture(tmp_path, extra={".sdlc/plans/1.md": "plan\n"})
    clean_repo, clean_sha = _fixture(tmp_path, name="clean")
    out = _out(tmp_path)
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=out))
    assert rc == 0, se
    fields = _fields(so)
    assert not (out / ".sdlc").exists()
    assert not [p for p in _git(out, "ls-files").splitlines() if p.startswith(".sdlc")]
    # excluding the only extra file leaves exactly the tree a repository without it has
    assert fields["tree"] == _git(clean_repo, "rev-parse", clean_sha + "^{tree}")
    assert fields["tree"] != _git(repo, "rev-parse", sha + "^{tree}")
    report = _report(fields)
    assert report["sdlc"] == {"mode": "exclude", "source": "default"}
    entry = [e for e in report["exclusions"] if e["origin"] == "sdlc"]
    assert len(entry) == 1 and entry[0]["prefix"].rstrip("/") == ".sdlc"
    assert entry[0]["files"] == 1 and entry[0]["bytes"] == len("plan\n")
    assert "came from the builder's default" in _md(fields)


def test_default_build_without_sdlc_files_is_not_refused(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path)
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha))
    assert rc == 0, se
    report = _report(_fields(so))
    assert report["sdlc"] == {"mode": "exclude", "source": "default"}
    assert report["exclusions"] == []


def test_sdlc_include_flag_is_recorded_as_flag(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path, extra={".sdlc/plans/1.md": "plan\n"})
    out = _out(tmp_path)
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=out, extra=["--sdlc", "include"]))
    assert rc == 0, se
    fields = _fields(so)
    assert (out / ".sdlc" / "plans" / "1.md").read_text(encoding="utf-8") == "plan\n"
    assert fields["tree"] == _git(repo, "rev-parse", sha + "^{tree}")
    report = _report(fields)
    assert report["sdlc"] == {"mode": "include", "source": "flag"}
    assert report["exclusions"] == []
    assert "came from the builder's default" not in _md(fields)


def test_exclude_prefix_is_listed_with_counts_and_remaining_references(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path, extra={
        "docs/internal/x.md": "internal x\n", "docs/internal/y.md": "internal y\n",
        "README.md": "hello\nsee docs/internal/x.md for more\n"})
    out = _out(tmp_path)
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=out, extra=["--exclude", "docs/internal"]))
    assert rc == 0, se
    assert not (out / "docs" / "internal").exists()
    assert (out / "docs" / "guide.md").exists()
    report = _report(_fields(so))
    flagged = [e for e in report["exclusions"] if e["origin"] == "flag"]
    assert len(flagged) == 1
    assert flagged[0]["prefix"].rstrip("/") == "docs/internal"
    assert flagged[0]["files"] == 2
    assert flagged[0]["bytes"] == len("internal x\n") + len("internal y\n")
    assert _count(flagged[0]["references_remaining"]) == 1


# ============================================================================== content findings

def test_planted_private_reference_rejects_and_never_leaks(tmp_path):
    mod = _tool()
    repo, sha = _fixture(tmp_path, extra={
        "README.md": "hello\nsee zq-planted-1234 here\n",
        "docs/zq-planted-7.md": "a planted token in this file's path\n",
        "legacy/zq-planted-88/x.txt": "excluded below\n"})
    out = _out(tmp_path)
    proc = _cli(tmp_path, _argv(tmp_path, repo, sha, out=out, extra=["--exclude", "legacy/zq-planted-88"]))
    assert proc.returncode == 1, proc.stderr
    fields = _fields(proc.stdout)
    assert fields["verdict"] == "REJECTED"
    rejected = pathlib.Path(str(out) + ".rejected")
    assert not out.exists()
    assert rejected.is_dir()
    assert _git(rejected, "for-each-ref") == ""
    unborn = subprocess.run(["git", "-C", str(rejected), "rev-parse", "--verify", "-q", "HEAD"],
                            capture_output=True, text=True)
    assert unborn.returncode == 1
    report = _report(fields)
    assert set(report) == REPORT_KEYS
    assert report["verdict"] == "REJECTED"
    assert report["scans"]["leak_refs"]["hits"] >= 1
    assert re.search(r"leak_refs=1/[1-9][0-9]*\b", fields["scans"]), fields["scans"]
    texts = [proc.stdout, proc.stderr, _report_path(fields).read_text(encoding="utf-8"), _md(fields)]
    for text in texts:
        assert re.search(r"zq-planted-[0-9]+", text) is None


@pytest.mark.parametrize("shape", ["a", "b"])
def test_key_header_shapes_are_rejected(tmp_path, capsys, shape):
    mod = _tool()
    if shape == "a":
        text = _header() + "\n# generated, do not share\n" + _body()
    else:
        text = "Paste the " + _header() + " line before the body\n\n" + _body()
    repo, sha = _fixture(tmp_path, extra={"docs/keys.txt": text})
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha))
    assert rc == 1, se
    fields = _fields(so)
    assert fields["verdict"] == "REJECTED"
    report = _report(fields)
    findings = report["scans"]["builder"]["findings"]
    assert len(findings) == 1
    found = findings[0]
    assert (found["rule"], found["path"], found["line"]) == ("private-key-header", "docs/keys.txt", 1)
    assert found["blob"] == _git(repo, "hash-object", "docs/keys.txt")
    assert fields["scans"].startswith("builder=1 ")
    for blob in (so, se, _report_path(fields).read_text(encoding="utf-8"), _md(fields)):
        assert "PRIVATE KEY" not in blob
        assert _body().splitlines()[0] not in blob


def _disposition_file(entries):
    return json.dumps({"schema": "sigma.public-tree-dispositions/v1", "entries": entries}) + "\n"


def test_disposition_suppresses_a_header_and_a_stale_entry_rejects(tmp_path, capsys):
    mod = _tool()
    keys = {"docs/keys.txt": _header() + "\n# fixture\n" + _body()}
    used = {"path": "docs/keys.txt", "rule": "private-key-header", "reason": "a fixture, not a key"}
    repo, sha = _fixture(tmp_path, name="used", extra=dict(keys, **{DISPOSITIONS: _disposition_file([used])}))
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=_out(tmp_path, "o1")))
    assert rc == 0, se
    report = _report(_fields(so))
    assert report["scans"]["builder"]["findings"] == []
    assert report["dispositions"]["public_tree"] == {"present": True, "entries": 1, "moot": 0}
    listed = report["dispositions"]["unpinned_dispositions"]
    assert {"source": "public-tree", "path": "docs/keys.txt", "rule": "private-key-header"} in listed
    stale = {"path": "docs/keys.txt", "rule": "owner-placeholder", "reason": "nothing matches this any more"}
    repo2, sha2 = _fixture(tmp_path, name="stale", extra=dict(keys, **{DISPOSITIONS: _disposition_file([used, stale])}))
    rc2, so2, se2 = _build(mod, capsys, _argv(tmp_path, repo2, sha2, out=_out(tmp_path, "o2")))
    assert rc2 == 1, se2
    fields2 = _fields(so2)
    assert fields2["verdict"] == "REJECTED"
    rules = [f["rule"] for f in _report(fields2)["scans"]["builder"]["findings"]]
    assert rules == ["stale-disposition"]


def test_pinned_disposition_must_match_the_blob(tmp_path, capsys):
    mod = _tool()
    text = _header() + "\n# fixture\n" + _body()
    probe, _probe_sha = _fixture(tmp_path, name="probe", extra={"docs/keys.txt": text})
    blob = _git(probe, "hash-object", "docs/keys.txt")
    results = {}
    for label, pin in (("right", blob), ("wrong", "0" * 40)):
        entry = {"path": "docs/keys.txt", "rule": "private-key-header", "reason": "fixture", "blob": pin}
        repo, sha = _fixture(tmp_path, name=label,
                             extra={"docs/keys.txt": text, DISPOSITIONS: _disposition_file([entry])})
        rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=_out(tmp_path, "o" + label)))
        results[label] = (rc, _report(_fields(so)))
    right_rc, right = results["right"]
    assert right_rc == 0
    assert right["dispositions"]["unpinned_dispositions"] == []
    wrong_rc, wrong = results["wrong"]
    assert wrong_rc == 1
    assert "private-key-header" in [f["rule"] for f in wrong["scans"]["builder"]["findings"]]


def test_owner_placeholder_is_rejected(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path, extra={"SECURITY.md": "Report privately to\n" + _placeholder() + " the owner address>\n"})
    out = _out(tmp_path)
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=out))
    assert rc == 1, se
    fields = _fields(so)
    assert fields["verdict"] == "REJECTED"
    assert not out.exists()
    assert pathlib.Path(str(out) + ".rejected").is_dir()
    findings = _report(fields)["scans"]["builder"]["findings"]
    assert [(f["rule"], f["path"], f["line"]) for f in findings] == [("owner-placeholder", "SECURITY.md", 2)]
    for blob in (so, se, _report_path(fields).read_text(encoding="utf-8"), _md(fields)):
        assert _placeholder() not in blob


def test_exposure_entries_under_an_excluded_prefix_are_moot(tmp_path, capsys):
    mod = _tool()
    allow = json.dumps([{"path": ".sdlc/plans/1.md", "rule": "email-address", "reason": "fixture"}]) + "\n"
    extra = {".sdlc/plans/1.md": "contact " + _email() + "\n", EXPOSURE_ALLOWLIST: allow}
    repo, sha = _fixture(tmp_path, extra=extra)
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=_out(tmp_path, "default")))
    assert rc == 0, se
    report = _report(_fields(so))
    assert report["dispositions"]["exposure_allowlist"]["moot"] == 1
    assert not [u for u in report["dispositions"]["unpinned_dispositions"] if u["source"] == "exposure-allowlist"]
    assert report["scans"]["exposure"]["stale"] == []
    rc2, so2, se2 = _build(mod, capsys, _argv(tmp_path, repo, sha, out=_out(tmp_path, "include"),
                                              extra=["--sdlc", "include"]))
    assert rc2 == 0, se2
    report2 = _report(_fields(so2))
    assert report2["dispositions"]["exposure_allowlist"]["moot"] == 0
    assert {"source": "exposure-allowlist", "path": ".sdlc/plans/1.md", "rule": "email-address"} in \
        report2["dispositions"]["unpinned_dispositions"]
    assert report2["scans"]["exposure"]["findings"] == []


def test_doctor_slug_mismatch_rejects(tmp_path, capsys):
    mod = _tool()
    doctor = {"skills/agrim-doctor/scripts/doctor.py": '_MARKETPLACE_REPO = "acme/demo"\n'}
    cases = (
        ("mismatch", dict(doctor, **{"docs/launch/definition.json": json.dumps({"public_repo": "acme/other"})}), 1),
        ("equal", dict(doctor, **{"docs/launch/definition.json": json.dumps({"public_repo": "acme/demo"})}), 0),
        ("unverifiable", doctor, 1),
        ("not-applicable", {}, 0),
    )
    for status, extra, want_rc in cases:
        repo, sha = _fixture(tmp_path, name="s-" + status, extra=extra)
        rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=_out(tmp_path, "o-" + status)))
        assert rc == want_rc, (status, se)
        slug = _report(_fields(so))["scans"]["doctor_slug"]
        assert slug["status"] == status
        if status == "mismatch":
            assert (slug["doctor"], slug["definition"]) == ("acme/demo", "acme/other")


# ============================================================================== report

def test_report_is_outside_the_repo_names_the_commit_and_is_private(tmp_path):
    mod = _tool()
    keys = _header() + "\n# fixture\n" + _body()
    entry = {"path": "docs/keys.txt", "rule": "private-key-header", "reason": "a fixture"}
    repo, sha = _fixture(tmp_path, extra={"docs/keys.txt": keys, DISPOSITIONS: _disposition_file([entry])})
    home = tmp_path / "home"
    argv = [sha, "--out", _out(tmp_path), "--source", repo, "--patterns", _patterns(tmp_path), "--review-level", "clean"]
    proc = _cli(tmp_path, argv, home=home)
    assert proc.returncode == 0, proc.stderr
    fields = _fields(proc.stdout)
    path = _report_path(fields)
    assert path.parent == home / ".sigma-ops" / "public-tree"
    assert str(repo) not in str(path)
    assert re.fullmatch(re.escape(sha[:12]) + r"-\d{8}T\d{6}Z-\d+(?:-\d+)?\.json", path.name), path.name
    twin = path.with_suffix(".md")
    assert twin.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert (twin.stat().st_mode & 0o777) == 0o600
    assert (path.parent.stat().st_mode & 0o777) == 0o700
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema"] == SCHEMA
    assert report["verdict"] == "VERIFIED" and report["finalise"] == "done"
    assert report["source"]["commit"] == sha
    assert report["source"]["plugin_version"] == "0.0.7"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", report["generated_at"])
    assert report["patterns"] == {"source": "flag", "count": 1}
    assert "zq-planted" not in path.read_text(encoding="utf-8")
    assert {"source": "public-tree", "path": "docs/keys.txt", "rule": "private-key-header"} in \
        report["dispositions"]["unpinned_dispositions"]
    assert report["scans"]["leak_scan"]["status"] == "absent"
    assert "reviewed commit" not in twin.read_text(encoding="utf-8")


def test_write_report_never_overwrites(tmp_path, capsys, monkeypatch):
    mod = _tool()
    assert mod.REPORT_SUFFIX_TRIES == 100
    repo, sha = _fixture(tmp_path)
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha))
    assert rc == 0, se
    report = _report(_fields(so))
    rdir = tmp_path / "wr"
    first = mod.write_report(rdir, "stemx", report)
    first_bytes = pathlib.Path(first[0]).read_bytes()
    second = mod.write_report(rdir, "stemx", report)
    assert pathlib.Path(first[0]).name == "stemx.json"
    assert pathlib.Path(second[0]).name == "stemx-1.json"
    assert pathlib.Path(first[1]).name == "stemx.md" and pathlib.Path(second[1]).name == "stemx-1.md"
    assert pathlib.Path(first[0]).read_bytes() == first_bytes
    for item in first + second:
        assert (pathlib.Path(item).stat().st_mode & 0o777) == 0o600
    monkeypatch.setattr(mod, "REPORT_SUFFIX_TRIES", 1)
    rdir2 = tmp_path / "wr2"
    kept = mod.write_report(rdir2, "stemy", report)
    kept_bytes = pathlib.Path(kept[0]).read_bytes()
    with pytest.raises(mod.Refused) as raised:
        mod.write_report(rdir2, "stemy", report)
    assert raised.value.code == "report-exists"
    assert pathlib.Path(kept[0]).read_bytes() == kept_bytes


def test_crash_between_report_and_rename_never_reads_verified(tmp_path, capsys, monkeypatch):
    mod = _tool()
    repo, sha = _fixture(tmp_path)
    attempts = []

    def crash(partial, dest):
        attempts.append((str(partial), str(dest)))
        raise OSError("simulated crash")

    monkeypatch.setattr(mod, "_rename_export", crash)
    out = _out(tmp_path)
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=out))
    assert attempts, "the builder never renamed through _rename_export"
    assert rc == 2
    assert so == ""
    assert "REFUSED [internal] OSError" in se
    assert not out.exists()
    reports = sorted((tmp_path / "reports").glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["verdict"] == "NOT-VERIFIED"
    assert report["finalise"] == "pending"
    assert "VERIFIED" != report["verdict"]


# ============================================================================== leak_scan copy in the export

_STUB_CWD = ("import os, sys\n"
             "ok = os.path.exists('README.md') and os.path.exists(os.path.join('tools', 'leak_scan.py'))\n"
             "print('leak_scan: 0 finding(s) over 1 file(s)')\n"
             "sys.exit(0 if ok else 5)\n")
_STUB_FINDING = ("print('a.md:3: home-path')\n"
                 "print('leak_scan: 1 finding(s) over 1 file(s)')\n"
                 "raise SystemExit(1)\n")
_STUB_CRASH = "raise SystemExit(2)\n"
_STUB_TRACEBACK = "raise RuntimeError('boom')\n"
_STUB_HANG = "import time\ntime.sleep(60)\n"


def test_leak_scan_copy_in_the_export_runs_and_its_findings_and_crash_count(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path, name="ok", extra={"tools/leak_scan.py": _STUB_CWD})
    out = _out(tmp_path, "o-ok")
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=out))
    assert rc == 0, se
    fields = _fields(so)
    assert fields["scans"].endswith("leak_scan=0")
    scan = _report(fields)["scans"]["leak_scan"]
    assert (scan["status"], scan["exit"], scan["findings"]) == ("ran", 0, [])
    assert "#433" in scan["label"]
    repo2, sha2 = _fixture(tmp_path, name="found", extra={"tools/leak_scan.py": _STUB_FINDING})
    out2 = _out(tmp_path, "o-found")
    rc2, so2, se2 = _build(mod, capsys, _argv(tmp_path, repo2, sha2, out=out2))
    assert rc2 == 1, se2
    fields2 = _fields(so2)
    assert fields2["verdict"] == "REJECTED"
    scan2 = _report(fields2)["scans"]["leak_scan"]
    assert scan2["exit"] == 1
    assert {"rule": "home-path", "path": "a.md", "line": 3} in scan2["findings"]
    assert not out2.exists() and pathlib.Path(str(out2) + ".rejected").is_dir()
    repo3, sha3 = _fixture(tmp_path, name="crash", extra={"tools/leak_scan.py": _STUB_CRASH})
    out3 = _out(tmp_path, "o-crash")
    rc3, so3, se3 = _build(mod, capsys, _argv(tmp_path, repo3, sha3, out=out3))
    assert rc3 == 2
    assert so3 == ""
    assert "REFUSED [scan-failed]" in se3 and "leak_scan exited 2" in se3 and "report:" in se3
    rejected = pathlib.Path(str(out3) + ".rejected")
    assert rejected.is_dir() and not out3.exists()
    assert _git(rejected, "for-each-ref") == ""
    reports = sorted((tmp_path / "reports").glob("*.json"))
    verdicts = [json.loads(p.read_text(encoding="utf-8"))["verdict"] for p in reports]
    assert verdicts.count("NOT-VERIFIED") == 1


def test_scan_traceback_and_timeout_are_not_verified_never_rejected(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path, name="tb", extra={"tools/leak_scan.py": _STUB_TRACEBACK})
    out = _out(tmp_path, "o-tb")
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out=out))
    assert rc == 2, se
    assert so == ""
    assert "REFUSED [scan-failed]" in se and "leak_scan" in se
    assert pathlib.Path(str(out) + ".rejected").is_dir() and not out.exists()
    repo2, sha2 = _fixture(tmp_path, name="hang", extra={"tools/leak_scan.py": _STUB_HANG})
    out2 = _out(tmp_path, "o-hang")
    rc2, so2, se2 = _build(mod, capsys, _argv(tmp_path, repo2, sha2, out=out2, extra=["--scan-timeout", "6"]))
    assert rc2 == 2, se2
    assert so2 == ""
    assert "REFUSED [scan-failed]" in se2 and "leak_scan" in se2
    assert pathlib.Path(str(out2) + ".rejected").is_dir() and not out2.exists()
    reports = sorted((tmp_path / "reports").glob("*.json"))
    assert len(reports) == 2
    assert {json.loads(p.read_text(encoding="utf-8"))["verdict"] for p in reports} == {"NOT-VERIFIED"}


# ============================================================================== the tool itself

def test_help_lists_every_flag(tmp_path):
    mod = _tool()
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    proc = _cli(tmp_path, ["--help"], home=empty_home)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("usage:")
    for flag in ("COMMIT", "--out", "--source", "--patterns", "--sdlc", "--exclude", "--review-level", "--repo",
                 "--remote", "--base", "--report-dir", "--scan-timeout"):
        assert flag in proc.stdout, flag
    for choice in ("exclude", "include", "clean", "landed", "pr-merged", "owner-merged"):
        assert choice in proc.stdout, choice


def test_refused_carries_code_and_detail():
    mod = _tool()
    err = mod.Refused("some-code", "some detail")
    assert isinstance(err, Exception)
    assert (err.code, err.detail) == ("some-code", "some detail")


def test_module_constants_are_pinned():
    mod = _tool()
    assert mod.SCHEMA == SCHEMA
    assert mod.DISPOSITIONS_SCHEMA == "sigma.public-tree-dispositions/v1"
    assert mod.DISPOSITIONS_PATH == DISPOSITIONS
    assert mod.EXPOSURE_ALLOWLIST_PATH == EXPOSURE_ALLOWLIST
    assert mod.MAX_BLOB_BYTES == 2 * 1024 * 1024
    assert mod.REPORT_SUFFIX_TRIES == 100


def test_redact_applies_to_every_string_value_and_never_to_keys():
    mod = _tool()
    patterns = mod.parse_patterns(SYNTHETIC)
    value = {"zq-planted-9": "see zq-planted-9", "n": 7,
             "list": ["a zq-planted-1", 3, None, {"deep": "zq-planted-22"}]}
    out = mod._redact(value, patterns)
    assert list(out) == list(value)
    assert out["zq-planted-9"] == "see [redacted: pattern 2]"
    assert out["n"] == 7
    assert out["list"] == ["a [redacted: pattern 2]", 3, None, {"deep": "[redacted: pattern 2]"}]
    json.dumps(out)


def test_verbatim_copies_still_equal_leak_refs():
    mod = _tool()
    lr = _load(LEAK_REFS, "leak_refs_for_build_public_tree_%d" % next(_COUNTER))
    assert mod._GIT_SCRUB == lr._GIT_SCRUB
    assert mod._NOT_A_REPO == lr._NOT_A_REPO
    assert mod._REPO_RE.pattern == lr._REPO_RE.pattern
    for name in ("_real_run", "parse_patterns", "_safe"):
        assert inspect.getsource(getattr(mod, name)) == inspect.getsource(getattr(lr, name)), name


def test_gh_argv_literals_are_api_get_only():
    mod = _tool()
    source = TOOL.read_text(encoding="utf-8")
    found, bad = _gh_violations(source)
    assert found >= 1, "the builder has no [\"gh\", \"api\", ...] literal to check"
    assert bad == []
    # the control: the same predicate must flag a planted write flag and a planted non-api verb
    planted_flag = '_PLANT = ["gh", "api", "-' + 'X", "DELETE", "x"]\n'
    planted_verb = '_PLANT = ["gh", "re' + 'po", "create", "x"]\n'
    assert _gh_violations(planted_flag)[1] == [("write-flag", 1)]
    assert _gh_violations(planted_verb)[1] == [("not-api", 1)]
    assert _gh_violations('_OK = ["gh", "api", "repos/a/b"]\n') == (1, [])


def test_builder_write_sites_are_filesystem_only():
    mod = _tool()
    ws = _load(WRITE_SURFACE, "write_surface_for_build_public_tree_%d" % next(_COUNTER))
    rows = ws.scan_paths(ROOT, [TOOL])
    rules = {row["rule"] for row in rows}
    assert rows and "fs-write" in rules
    assert rules <= {"fs-write", "fs-remove", "fs-rmtree", "git-destructive"}, sorted(rules)
    destructive = [row for row in rows if row["rule"] == "git-destructive"]
    assert [row["function"] for row in destructive] == ["_unpublish_rejected"]
    assert not [row for row in rows if row["rule"].startswith("git-") and row["rule"] != "git-destructive"]
    assert not [row for row in rows if row["rule"].startswith("gh-")]


def test_builder_source_passes_its_own_content_rules_and_mirrors_the_secret_file_rule():
    mod = _tool()
    ls = _load(LEAK_SCAN, "leak_scan_for_build_public_tree_%d" % next(_COUNTER))
    assert mod.SECRET_FILE.pattern == ls._SECRET_FILE.pattern
    for name in ("id_rsa", "id_ed25519", "server.pem", "tls.key", "bundle.p12", ".env", ".env.local",
                 ".netrc", "credentials.json"):
        assert mod.SECRET_FILE.match(name), name
    for name in (".env.example", "README.md", "keys.md", "monkey.txt", "id_rsa.md"):
        assert not mod.SECRET_FILE.match(name), name
    for kind in ("RSA ", "", "OPENSSH ", "EC "):
        assert mod.KEY_HEADER.search("x " + _header(kind) + " y"), kind
    assert mod.KEY_HEADER.search("-" * 4 + " BEGIN SSH2 ENCRYPTED PRIVATE KEY " + "-" * 4)
    assert mod.KEY_HEADER.search("PuTTY-User-Key-" + "File-2: ssh-rsa")
    assert not mod.KEY_HEADER.search("-" * 5 + "BEGIN CERTIFICATE" + "-" * 5)
    assert not mod.KEY_HEADER.search("a private key is mentioned here")
    assert mod.OWNER_PLACEHOLDER.search("write to " + _placeholder() + " addr>")
    assert not mod.OWNER_PLACEHOLDER.search("OWNER: alone is fine")
    for number, line in enumerate(TOOL.read_text(encoding="utf-8").splitlines(), 1):
        assert not mod.KEY_HEADER.search(line), "line %d holds a key header" % number
        assert not mod.OWNER_PLACEHOLDER.search(line), "line %d holds the placeholder" % number

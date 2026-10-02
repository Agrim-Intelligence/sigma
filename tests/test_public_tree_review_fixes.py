# SPDX-License-Identifier: MIT
"""Review fixes for `tools/build_public_tree.py` (issue 397, PR review cycle 1, findings 3, 4, 6).

Finding 3: after a REJECTED build the export commit is gone, not merely unreferenced: its id (on the
build's stdout and in the report) names no object in `OUT.rejected`, so pushing it fails. Finding 4:
the export's `.git` records no operator identity (no reflog). Finding 6: an NFC/NFD pair of path
names is refused as `[case-collision]`, the same as a case-only pair.

Every test builds a throwaway repository under tmp_path with real git and runs the builder in-process
(`mod.main(argv)`). `gh` is never reached: a failing fake `gh` is first on PATH. The patterns are
synthetic (`zq-planted-[0-9]+`). The tool is loaded INSIDE each test body behind
`assert TOOL.exists()`, so an absent tool is an ASSERTION red.
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
SYNTHETIC = "# synthetic\nzq-planted-[0-9]+\n"
SOURCE_DATE = "1700000000"
OPERATOR_NAME = "zz-operator-gecos"
OPERATOR_EMAIL = "zz-operator@example.invalid"
FILES = {
    ".claude-plugin/plugin.json": '{"name": "demo", "version": "0.0.7"}\n',
    "README.md": "hello public world\n",
    "docs/guide.md": "a guide\n",
}
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
    return _load(TOOL, "build_public_tree_review_fixes_%d" % next(_COUNTER))


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    """HOME in tmp, a failing fake gh first on PATH, and an OPERATOR identity in the environment
    (the one a reflog would record)."""
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
        monkeypatch.setenv(name, OPERATOR_NAME)
    for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, OPERATOR_EMAIL)


def _git(repo, *args, env_extra=None, input_bytes=None):
    """Fixture-side git (never the tool under test): stdout text, stripped."""
    env = dict(os.environ)
    env.update(env_extra or {})
    proc = subprocess.run(["git", "-C", str(repo)] + [str(a) for a in args], input=input_bytes,
                          capture_output=True, env=env, check=True)
    return proc.stdout.decode("utf-8", "replace").strip()


def _fixture(tmp_path, extra=None, name="src"):
    files = dict(FILES)
    files.update(extra or {})
    repo = tmp_path / name
    repo.mkdir(parents=True)
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture", env_extra={"GIT_COMMITTER_DATE": SOURCE_DATE + " +0000"})
    return repo, _git(repo, "rev-parse", "HEAD")


def _argv(tmp_path, repo, sha, out):
    patterns = tmp_path / "patterns.txt"
    patterns.write_text(SYNTHETIC, encoding="utf-8")
    return ["build_public_tree.py", sha, "--out", out, "--source", repo, "--patterns", patterns,
            "--review-level", "clean", "--report-dir", tmp_path / "reports"]


def _build(mod, capsys, argv):
    rc = mod.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return rc, out, err


def _fields(stdout):
    fields = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            fields[key] = value
    return fields


def _identity_in_logs(export):
    """-> [(relative path, why)] for every reflog file under the export's .git holding the operator."""
    logs = pathlib.Path(export) / ".git" / "logs"
    hits = []
    if logs.exists():
        for path in sorted(p for p in logs.rglob("*") if p.is_file()):
            text = path.read_text(encoding="utf-8", errors="replace")
            if OPERATOR_NAME in text or OPERATOR_EMAIL in text or text.strip():
                hits.append((path.relative_to(export).as_posix(), text[:120]))
    return hits


# ------------------------------------------------------------------------------ finding 3 (and 4 on REJECTED)

def test_rejected_export_commit_is_gone_and_cannot_be_pushed(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path, extra={"README.md": "hello\nsee zq-planted-1234 here\n"})
    out = tmp_path / "out" / "export"
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out))
    assert rc == 1, se
    fields = _fields(so)
    assert fields.get("verdict") == "REJECTED", so
    rejected = pathlib.Path(str(out) + ".rejected")
    assert rejected.is_dir() and not out.exists()
    report = json.loads(pathlib.Path(os.path.expanduser(fields["report"])).read_text(encoding="utf-8"))
    export_commit = report["export"]["commit"]
    assert len(export_commit) == 40
    # still true from the original contract
    assert _git(rejected, "for-each-ref") == ""
    head = subprocess.run(["git", "-C", str(rejected), "rev-parse", "--verify", "-q", "HEAD"],
                          capture_output=True, text=True)
    assert head.returncode == 1, head
    # the fix: the commit object itself is gone
    exists = subprocess.run(["git", "-C", str(rejected), "cat-file", "-e", export_commit],
                            capture_output=True, text=True)
    assert exists.returncode != 0, "the rejected export commit %s is still retrievable" % export_commit
    bare = tmp_path / "scratch.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], capture_output=True, check=True)
    push = subprocess.run(["git", "-C", str(rejected), "push", str(bare),
                           export_commit + ":refs/heads/main"], capture_output=True, text=True)
    assert push.returncode != 0, "the rejected export commit was pushable: %s" % push.stderr
    assert _git(bare, "for-each-ref") == ""
    assert _identity_in_logs(rejected) == []


# ------------------------------------------------------------------------------ finding 4

def test_verified_export_records_no_operator_identity(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path)
    out = tmp_path / "out" / "export"
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out))
    assert rc == 0, se
    assert _fields(so)["verdict"] == "VERIFIED"
    assert _identity_in_logs(out) == []
    assert _git(out, "for-each-ref", "--format=%(refname)") == "refs/heads/main"
    header = _git(out, "cat-file", "-p", "HEAD")
    assert OPERATOR_NAME not in header and OPERATOR_EMAIL not in header


# ------------------------------------------------------------------------------ finding 6

def test_nfc_nfd_pair_is_a_case_collision(tmp_path, capsys):
    mod = _tool()
    repo, _sha = _fixture(tmp_path)
    composed = "docs/café.md"
    decomposed = "docs/café.md"
    entries = []
    for path, text in ((composed, "one\n"), (decomposed, "two\n")):
        oid = _git(repo, "hash-object", "-w", "--stdin", input_bytes=text.encode("utf-8"))
        entries.append(("100644 %s\t" % oid).encode("ascii") + path.encode("utf-8") + b"\0")
    _git(repo, "update-index", "--add", "-z", "--index-info", input_bytes=b"".join(entries))
    tree = _git(repo, "write-tree")
    commit = _git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "pair",
                  env_extra={"GIT_COMMITTER_DATE": SOURCE_DATE + " +0000"})
    _git(repo, "update-ref", "refs/heads/main", commit)
    listed = _git(repo, "-c", "core.quotepath=false", "ls-tree", "-r", "--name-only", commit).splitlines()
    assert composed in listed and decomposed in listed, listed
    out = tmp_path / "out" / "export"
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, commit, out))
    assert rc == 2 and so == "", (rc, so, se)
    assert se.startswith("build_public_tree: REFUSED [case-collision] "), se
    assert not out.exists() and not pathlib.Path(str(out) + ".rejected").exists()


# ------------------------------------------------------------------------------ review cycle 2, findings 6 and 7

_STUB_LEAK_SCAN = "print('leak_scan: 0 finding(s) over 1 file(s)')\n"


@pytest.mark.parametrize("prefix", ["tools/", "tools/leak_scan.py"])
def test_excluding_the_exports_own_leak_scan_is_refused_before_building(tmp_path, capsys, prefix):
    mod = _tool()
    repo, sha = _fixture(tmp_path, extra={"tools/leak_scan.py": _STUB_LEAK_SCAN})
    out = tmp_path / "out" / "export"
    argv = _argv(tmp_path, repo, sha, out) + ["--exclude", prefix]
    rc, so, se = _build(mod, capsys, argv)
    assert rc == 2 and so == "", (rc, so, se)
    assert se.startswith("build_public_tree: REFUSED [leak-scan-absent] "), se
    assert len(se.strip().splitlines()) == 1, se
    assert not out.exists() and not pathlib.Path(str(out) + ".rejected").exists()
    assert not (tmp_path / "reports").exists() or not list((tmp_path / "reports").iterdir())
    # the control: the same commit with nothing excluded runs all four scans and is VERIFIED
    out2 = tmp_path / "out" / "export2"
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out2))
    assert rc == 0, se
    fields = _fields(so)
    assert fields["verdict"] == "VERIFIED" and fields["scans"].endswith("leak_scan=0"), so


def test_rejected_export_keeps_no_index_and_no_blob_of_its_tree(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path, extra={"README.md": "hello\nsee zq-planted-1234 here\n"})
    out = tmp_path / "out" / "export"
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out))
    assert rc == 1, se
    rejected = pathlib.Path(str(out) + ".rejected")
    assert (rejected / "docs" / "guide.md").read_text(encoding="utf-8") == "a guide\n"  # files stay
    assert _git(rejected, "ls-files") == "", "the rejected export's index still lists its files"
    blob = _git(repo, "rev-parse", sha + ":docs/guide.md")
    gone = subprocess.run(["git", "-C", str(rejected), "cat-file", "-e", blob], capture_output=True)
    assert gone.returncode != 0, "the rejected export still holds the blob %s" % blob


# ------------------------------------------------------------------------------ review cycle 3, finding 3

def test_export_commit_ignores_git_config_given_through_the_environment(tmp_path, capsys, monkeypatch):
    mod = _tool()
    repo, sha = _fixture(tmp_path)
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, tmp_path / "out" / "plain"))
    assert rc == 0, se
    plain = _fields(so)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "i18n.commitEncoding")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "ISO-8859-1")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'i18n.commitencoding'='ISO-8859-2'")
    out = tmp_path / "out" / "envcfg"
    rc, so, se = _build(mod, capsys, _argv(tmp_path, repo, sha, out))
    assert rc == 0, se
    got = _fields(so)
    assert got["verdict"] == "VERIFIED", so
    for name in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_CONFIG_PARAMETERS"):
        monkeypatch.delenv(name)
    header = _git(out, "cat-file", "-p", "HEAD")
    assert "\nencoding " not in header, header
    commit = [v for k, v in got.items() if "commit" in k]
    assert commit == [v for k, v in plain.items() if "commit" in k], (plain, got)

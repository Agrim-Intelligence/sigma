# SPDX-License-Identifier: MIT
"""Review fixes for `tools/handover_check.py check` (issue 397, PR review cycle 1, findings 1 and 2).

Finding 1: a clone whose fetch or push really reaches the old repository is a blocking holder,
whatever spelling its configuration uses: a URL rewritten by `insteadOf`, an SSH host alias, the
port-443 SSH host, the `www.` host, a push URL, a different-case slug. Finding 2: the scan walk never
drops a clone silently: a clone inside another repository's directory is found, and a directory cut
by `--max-depth` while it still has subdirectories is a blocking `truncated` finding (field
`max-depth`).

In-process: `mod.main(argv, run=World)`; git is real (repositories under tmp_path), `ps`/`lsof` are
canned, `gh` is never reached (`--offline`). Slugs are synthetic (`acme-old/widget` old,
`acme-old/widget-private` new). Every test body starts with `mod = _tool()`, so an absent tool is an
AssertionError, never an import error. Literal-token rule: every SSH form is assembled at run time.
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
TOOL = ROOT / "tools" / "handover_check.py"
OLD = "acme-old/widget"
NEW = "acme-old/widget-private"
OLD_URL = "https://github.com/acme-old/widget.git"
NEW_URL = "https://github.com/acme-old/widget-private.git"
_N = itertools.count(1)


def _load(path, name):
    assert path.exists(), "%s is missing" % path.relative_to(ROOT)
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tool():
    assert TOOL.exists(), "tools/handover_check.py is missing"
    return _load(TOOL, "handover_check_review_fixes_%d" % next(_N))


def _ssh(host):
    """`git` + at-sign + host + colon, assembled so this file holds no SSH URL literal."""
    return "git" + "@" + host + ":"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    for name in ("GH_REPO", "CLAUDE_CONFIG_DIR", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv("GIT_%s_NAME" % key, "t")
        monkeypatch.setenv("GIT_%s_EMAIL" % key, "test@example.invalid")
    monkeypatch.chdir(cwd)


def _git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd)] + [str(a) for a in args], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, (args, proc.stdout, proc.stderr)
    return proc.stdout


def _repo(path, origin=None, config=None):
    path = pathlib.Path(path)
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    if origin:
        _git(path, "remote", "add", "origin", origin)
    for key, value in sorted((config or {}).items()):
        _git(path, "config", key, value)
    return path.resolve()


class World(object):
    """The injected `run`: git is real, ps/lsof are canned, anything else is a violation."""

    def __init__(self, mod):
        self.mod, self.violations = mod, []

    def __call__(self, args, input_text=None, timeout=60):
        args = [str(a) for a in args]
        if args[0] == "git":
            return self.mod._real_run(args, input_text, timeout)
        if args[0] == "ps":
            return 0, "", ""
        if args[0] == "lsof":
            return 1, "", ""
        self.violations.append(args)
        raise AssertionError("unexpected command %r" % (args,))


def _check(mod, capsys, tmp_path, *argv):
    """-> (rc, stdout, stderr, json_doc|None), offline, --json to a fresh file outside every repo."""
    out_dir = tmp_path / "json"
    out_dir.mkdir(exist_ok=True)
    jpath = out_dir / ("r%d.json" % next(_N))
    cfg = tmp_path / "claude"
    cfg.mkdir(exist_ok=True)
    world = World(mod)
    argv = (["handover_check.py", "check", "--old", OLD, "--new", NEW, "--claude-config", str(cfg),
             "--offline"] + [str(a) for a in argv] + ["--json", str(jpath)])
    try:
        rc = mod.main(argv, run=world)
    except SystemExit as exc:
        rc = exc.code
    cap = capsys.readouterr()
    assert not world.violations, world.violations
    doc = json.loads(jpath.read_text(encoding="utf-8")) if jpath.exists() else None
    return rc, cap.out, cap.err, doc


def _blocking_old_at(doc, clone):
    return [f for f in doc["findings"] if f["location"] == str(clone) and f["blocking"]
            and f["value_class"] == "old" and f["kind"] in ("remote-url", "remote-pushurl")]


# ------------------------------------------------------------------------------ finding 1

def test_every_spelling_of_the_old_repository_is_a_blocking_holder(tmp_path, capsys):
    mod = _tool()
    root = tmp_path / "scan"
    holders = {
        # `gh:` rewritten to GitHub by insteadOf: the URL git uses is the old repository
        "insteadof": _repo(root / "c-insteadof", "gh:Acme-Old/Widget",
                           config={"url.https://github.com/.insteadOf": "gh:"}),
        # only the rewritten URL names it: neither the remote nor the rewrite key holds a slug
        "insteadof-owner": _repo(root / "c-insteadof-owner", "team:Widget.git",
                                 config={"url.https://github.com/Acme-Old/.insteadOf": "team:"}),
        "host-alias": _repo(root / "c-alias", _ssh("github-work") + "Acme-Old/Widget.git"),
        "ssh-443": _repo(root / "c-443", "ssh://" + "git" + "@" + "ssh.github.com:443/Acme-Old/Widget.git"),
        "www": _repo(root / "c-www", "https://www.github.com/Acme-Old/Widget"),
        "pushurl": _repo(root / "c-push", NEW_URL,
                         config={"remote.origin.pushurl": _ssh("github-work") + "acme-old/widget.git"}),
        "case": _repo(root / "c-case", "https://GitHub.com/ACME-OLD/WIDGET.git"),
        "foreign-host": _repo(root / "c-foreign", "https://git.example.invalid/acme-old/widget.git"),
    }
    controls = {
        "new": _repo(root / "c-new", NEW_URL),
        "near-name": _repo(root / "c-near", "https://git.example.invalid/acme-old/widget-extra.git"),
        "other-owner": _repo(root / "c-owner", _ssh("github-work") + "someone/widget.git"),
    }
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root)
    assert rc == 1, (out, err)
    missed = sorted(name for name, clone in holders.items() if not _blocking_old_at(doc, clone))
    assert missed == [], "holders not listed as blocking old: %s\n%s" % (missed, out)
    for name, clone in controls.items():
        wrong = [f for f in doc["findings"] if f["location"] == str(clone) and f["blocking"]]
        assert wrong == [], (name, wrong)
    for name in ("host-alias", "foreign-host"):
        notes = " ".join(f["note"] for f in _blocking_old_at(doc, holders[name]))
        assert "not github.com" in notes, (name, notes)
    for name in ("case", "www", "ssh-443"):
        notes = " ".join(f["note"] for f in _blocking_old_at(doc, holders[name]))
        assert "not github.com" not in notes, (name, notes)
    assert "repositories %d;" % (len(holders) + len(controls)) in out.splitlines()[-1], out
    # the simulated rename: every holder repointed to the new name (rewrites removed) blocks nothing
    for name, clone in holders.items():
        if name.startswith("insteadof"):
            _git(clone, "config", "--remove-section",
                 "url.https://github.com/" + ("Acme-Old/" if name == "insteadof-owner" else ""))
        if name == "pushurl":
            _git(clone, "config", "--unset", "remote.origin.pushurl")
        _git(clone, "remote", "set-url", "origin", NEW_URL)
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root)
    assert rc == 0, (out, err)
    assert [f for f in doc["findings"] if f["blocking"]] == []


# ------------------------------------------------------------------------------ finding 2

def test_depth_cut_is_a_blocking_truncation_and_nested_clones_are_found(tmp_path, capsys):
    mod = _tool()
    root = tmp_path / "scan"
    deep = _repo(root.joinpath(*"a b c d e f c8-deep".split()), OLD_URL)
    parent = _repo(root / "parent", NEW_URL)
    nested = _repo(parent / "nested" / "c9-nested", OLD_URL)
    _git(parent, "worktree", "add", "-q", "-b", "wt-branch", str(parent / "work" / "wt"))
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root)
    assert rc == 1, (out, err)
    assert _blocking_old_at(doc, nested), "the clone inside another repository was dropped\n" + out
    cut = [f for f in doc["findings"] if f["kind"] == "truncated" and f["field"] == "max-depth"]
    assert len(cut) == 1 and cut[0]["blocking"] is True, doc["findings"]
    assert any(l.startswith("BLOCK truncated ") and " max-depth " in l for l in out.splitlines()), out
    # parent and its linked worktree share one common dir: counted once, plus the nested clone
    assert "repositories 2;" in out.splitlines()[-1], out
    # a cap deep enough sees the deep clone, and nothing is cut
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root, "--max-depth", "12")
    assert rc == 1, (out, err)
    assert _blocking_old_at(doc, deep), out
    assert _blocking_old_at(doc, nested), out
    assert [f for f in doc["findings"] if f["kind"] == "truncated"] == [], doc["findings"]
    assert "repositories 3;" in out.splitlines()[-1], out
    # the control: a clone AT the cap with nothing below it is not a truncation
    root2 = tmp_path / "scan2"
    _repo(root2 / "x" / "y", NEW_URL)
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root2, "--max-depth", "2")
    assert rc == 0, (out, err)
    assert [f for f in doc["findings"] if f["kind"] == "truncated"] == [], doc["findings"]

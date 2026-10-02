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


# ------------------------------------------------------------------------------ review cycle 2
# Findings 1-5 of the second review: every way a clone can push to the old name, every kind of
# repository directory, the printed sequence run as printed, URL forms GitHub accepts, and a walk
# that stays bounded (heavy directories skipped loudly, depth counted from the nearest repository,
# a wall-clock bound).

def _legacy(clone, sub, name, text):
    """A legacy `.git/remotes/<name>` or `.git/branches/<name>` file (git still reads both)."""
    folder = pathlib.Path(clone) / ".git" / sub
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def test_branch_remote_push_default_legacy_files_worktree_and_include_config_are_holders(tmp_path, capsys):
    mod = _tool()
    root = tmp_path / "scan"
    included = tmp_path / "outside" / "included.cfg"
    included.parent.mkdir(parents=True)
    included.write_text('[remote "origin"]\n\tpushurl = %s\n' % OLD_URL, encoding="utf-8")
    holders = {
        "branch-remote": _repo(root / "c-branch", NEW_URL, config={"branch.work.remote": OLD_URL}),
        "push-remote": _repo(root / "c-pushremote", NEW_URL,
                             config={"branch.work.pushRemote": _ssh("github.com") + "acme-old/widget.git"}),
        "push-default": _repo(root / "c-pushdefault", NEW_URL, config={"remote.pushDefault": OLD_URL}),
        # only the pushInsteadOf rewrite of a URL-valued pushDefault names the old repository
        "push-default-rewritten": _repo(root / "c-rewritten", NEW_URL, config={
            "remote.pushDefault": "team:widget.git",
            "url.https://github.com/acme-old/.pushInsteadOf": "team:"}),
        "legacy-remotes": _repo(root / "c-legacy-remotes", NEW_URL),
        "legacy-branches": _repo(root / "c-legacy-branches", NEW_URL),
    }
    _legacy(holders["legacy-remotes"], "remotes", "up", "URL: %s\nPush: refs/heads/main\n" % OLD_URL)
    _legacy(holders["legacy-branches"], "branches", "br", OLD_URL + "#main\n")
    # a linked worktree whose OWN config (extensions.worktreeConfig) pushes to the old name
    wt_main = _repo(root / "c-wt", NEW_URL, config={"extensions.worktreeConfig": "true"})
    _git(wt_main, "worktree", "add", "-q", "-b", "wt-branch", str(wt_main / "linked"))
    _git(wt_main / "linked", "config", "--worktree", "remote.origin.pushurl", OLD_URL)
    holders["worktree-config"] = (wt_main / "linked").resolve()
    # an included file outside every repository: located at that file
    _repo(root / "c-include", NEW_URL, config={"include.path": str(included)})
    holders["include-path"] = included.resolve()
    controls = {
        "named-branch-remote": _repo(root / "k-named", NEW_URL,
                                     config={"branch.work.remote": "origin", "branch.work.pushRemote": "origin",
                                             "remote.pushDefault": "origin"}),
        "legacy-new": _repo(root / "k-legacy-new", NEW_URL),
    }
    _legacy(controls["legacy-new"], "remotes", "up", "URL: %s\n" % NEW_URL)
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root)
    assert rc == 1, (out, err)
    missed = sorted(name for name, clone in holders.items() if not _blocking_old_at(doc, clone))
    assert missed == [], "holders not listed as blocking old: %s\n%s" % (missed, out)
    for name, clone in controls.items():
        wrong = [f for f in doc["findings"] if f["location"] == str(clone) and f["blocking"]]
        assert wrong == [], (name, wrong)
    # the worktree's own value is not attributed to the main checkout, and is listed once
    assert not [f for f in _blocking_old_at(doc, wt_main)], doc["findings"]
    assert len(_blocking_old_at(doc, holders["worktree-config"])) == 1, doc["findings"]


def _clone_kind(src, dest, *flags):
    proc = subprocess.run(["git", "-c", "protocol.file.allow=always", "clone", "-q"] + list(flags)
                          + [str(src), str(dest)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return pathlib.Path(dest).resolve()


def test_bare_mirror_separate_gitdir_and_submodule_repositories_are_read(tmp_path, capsys):
    mod = _tool()
    src = _repo(tmp_path / "src")
    root = tmp_path / "scan"
    root.mkdir()
    bare = _clone_kind(src, root / "bare.git", "--bare")
    mirror = _clone_kind(src, root / "mirror.git", "--mirror")
    bare_new = _clone_kind(src, root / "bare-new.git", "--bare")
    (root / "gd").mkdir()
    sep = _clone_kind(src, root / "sep", "--separate-git-dir", str(root / "gd" / "sep.git"))
    for path in (bare, mirror, sep):
        _git(path, "remote", "set-url", "origin", OLD_URL)
    _git(bare_new, "remote", "set-url", "origin", NEW_URL)
    inner = _repo(sep / "inner", OLD_URL)          # a clone inside a gitdir-file checkout
    parent = _repo(root / "parent", NEW_URL)
    _git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "sub")
    _git(parent, "commit", "-qm", "sub")
    _git(parent / "sub", "remote", "set-url", "origin", OLD_URL)
    parent2 = _repo(root / "parent2", NEW_URL)
    _git(parent2, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "gone")
    _git(parent2, "commit", "-qm", "gone")
    _git(parent2 / "gone", "remote", "set-url", "origin", OLD_URL)
    _git(parent2, "submodule", "deinit", "-q", "-f", "gone")   # its gitdir stays, still pushable
    modules_gone = (parent2 / ".git" / "modules" / "gone").resolve()
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root)
    assert rc == 1, (out, err)
    expect = {"bare": bare, "mirror": mirror, "separate-git-dir": sep, "inside-gitdir-file-checkout": inner,
              "submodule": (parent / "sub").resolve(), "deinit-submodule-gitdir": modules_gone}
    missed = sorted(name for name, place in expect.items() if not _blocking_old_at(doc, place))
    assert missed == [], "repositories not read: %s\n%s" % (missed, out)
    assert not [f for f in doc["findings"] if f["location"] == str(bare_new) and f["blocking"]], out
    assert not [f for f in doc["findings"] if f["kind"] == "unreadable"], out
    # --clone accepts a bare and a mirror repository instead of refusing it
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--clone", bare, "--clone", mirror)
    assert rc == 1 and err == "", (rc, out, err)
    assert _blocking_old_at(doc, bare) and _blocking_old_at(doc, mirror), out


# ------------------------------------------------------------------------------ the printed sequence, as printed

_FAKE_GH = """#!/bin/sh
case "$2" in
  repos/acme-old/widget-private) echo '{"id": 111, "private": true}' ;;
  repos/acme-old/widget) cat "$OLD_ANSWER" ;;
  *) echo '[]' ;;
esac
"""


def test_sequence_check_steps_run_as_printed_and_exit_as_promised(tmp_path, capsys):
    mod = _tool()

    def no_run(args, input_text=None, timeout=60):
        raise AssertionError("sequence ran %r" % (args,))

    rc = mod.main(["handover_check.py", "sequence", "--old", OLD, "--new", NEW], run=no_run)
    seq = capsys.readouterr().out
    assert rc == 0
    steps = [l.strip() for l in seq.splitlines() if "tools/handover_check.py check" in l]
    steps = [l[l.index("python3 tools/handover_check.py"):] for l in steps]
    assert len(steps) == 3, steps
    jsons = [l.split("--json", 1)[1].split()[0] for l in steps]
    assert len(set(jsons)) == 3, "steps 2, 6 and 8 reuse a --json path, so step 6 is refused: %s" % jsons
    home = tmp_path / "home"
    clone = _repo(home / ".sigma-ops" / "clone", OLD_URL)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(_FAKE_GH, encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    answer = tmp_path / "old-answer.json"
    env = {"PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % bindir, "HOME": str(home), "OLD_ANSWER": str(answer),
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "ID": "111",
           "CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}
    for var in ("JSON", "JSON2", "JSON3"):
        env[var] = str(tmp_path / "evidence" / (var.lower() + ".json"))

    def run_step(line):
        cmd = line.replace("python3 tools/handover_check.py", '"%s" "%s"' % (sys.executable, TOOL), 1)
        return subprocess.run(["/bin/sh", "-c", cmd], cwd=str(tmp_path / "cwd"), env=env,
                              capture_output=True, text=True, timeout=600)

    first = run_step(steps[0])
    assert first.returncode == 1, (first.stdout, first.stderr)
    shown = "~/.sigma-ops/clone"                       # $HOME paths print as ~/...
    assert any(l.startswith("BLOCK remote-url ") and shown in l for l in first.stdout.splitlines()), first.stdout
    _git(clone, "remote", "set-url", "origin", NEW_URL)                 # step 5
    answer.write_text('{"id": 111, "private": true}', encoding="utf-8")   # GitHub's redirect
    second = run_step(steps[1])
    assert second.returncode == 0, "step 6 must exit 0 as printed:\n%s%s" % (second.stdout, second.stderr)
    answer.write_text('{"id": 999, "private": false}', encoding="utf-8")  # the public repository
    third = run_step(steps[2])
    assert third.returncode == 0, (third.stdout, third.stderr)
    assert any(l.startswith("INFO  old-name-taken ") for l in third.stdout.splitlines()), third.stdout


# ------------------------------------------------------------------------------ URL forms

def test_url_forms_github_accepts_are_classified_without_a_false_host_note():
    mod = _tool()
    forms = ["https://github.com/acme-old//widget", "https://github.com/acme-old/./widget",
             "https://github.com/acme-old/x/../widget", "https://github.com/acme-old/widget.git?x=1",
             "https://github.com/acme-old/widget#frag", "https://github.com//acme-old/widget.git/",
             _ssh("github.com") + "/acme-old/widget", _ssh("github.com") + "acme-old//widget.git"]
    wrong = []
    for url in forms:
        slug, note = mod._url_info(url)
        if (slug or "").lower() != OLD or note:
            wrong.append((url, slug, note))
    assert wrong == [], wrong
    for url in ("https://github.com/acme-old/widget-extra", "https://github.com/acme-old/x/../widget-extra",
                "https://github.com/acme-old/widget/../widget-private"):
        assert (mod._url_info(url)[0] or "").lower() != OLD, url


# ------------------------------------------------------------------------------ the walk

def test_walk_skips_heavy_dirs_loudly_counts_depth_from_the_nearest_repository_and_is_time_bounded(
        tmp_path, capsys):
    mod = _tool()
    root = tmp_path / "scan"
    proj = _repo(root.joinpath(*"a b c d proj".split()), NEW_URL)
    inner = _repo(proj.joinpath(*"x y z inner".split()), OLD_URL)
    root.joinpath("node_modules", *"p q r s t u v w".split()).mkdir(parents=True)
    named_venv = _repo(root / "venv", OLD_URL)          # a repository named like a heavy dir is read
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root)
    assert rc == 1, (out, err)
    assert _blocking_old_at(doc, inner), "a clone 4 levels under its repository was cut\n" + out
    assert _blocking_old_at(doc, named_venv), out
    assert [f for f in doc["findings"] if f["kind"] == "truncated"] == [], doc["findings"]
    skipped = [f for f in doc["findings"] if f["kind"] == "skipped"]
    assert len(skipped) == 1 and skipped[0]["blocking"] is False and "node_modules" in skipped[0]["note"], \
        doc["findings"]
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root, "--max-seconds", "0")
    assert rc == 1, (out, err)
    cut = [f for f in doc["findings"] if f["kind"] == "truncated" and f["field"] == "max-seconds"]
    assert len(cut) == 1 and cut[0]["blocking"] is True, doc["findings"]


def test_global_includeif_process_env_config_and_a_bare_repository_with_a_worktree(tmp_path, capsys,
                                                                                     monkeypatch):
    """Config scopes beyond the repository's own file: the global file (an owner-only insteadOf),
    an `includeIf gitdir:` file, and config given to the checker's own process environment
    (`GIT_CONFIG_COUNT`, located at `command line:`). Plus a bare repository with a linked
    worktree: one repository, located at its work tree."""
    mod = _tool()
    root = tmp_path / "scan"
    c_inc = _repo(root / "c-incif", NEW_URL)
    c_glob = _repo(root / "c-global", NEW_URL)
    _git(c_glob, "remote", "add", "up", "gl:widget.git")
    included = tmp_path / "incif.cfg"
    included.write_text('[remote "origin"]\n\tpushurl = %s\n' % OLD_URL, encoding="utf-8")
    glob = tmp_path / "global.cfg"
    glob.write_text('[includeIf "gitdir:%s/"]\n\tpath = %s\n[url "https://github.com/acme-old/"]\n\tinsteadOf = gl:\n'
                    % (c_inc, included), encoding="utf-8")
    src = _repo(tmp_path / "src")
    bare = _clone_kind(src, root / "bw.git", "--bare")
    _git(bare, "remote", "set-url", "origin", OLD_URL)
    _git(bare, "worktree", "add", "-q", "-b", "w1", str(root / "bw-wt"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(glob))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "url.https://github.com/acme-old/widget.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "envx:")
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root)
    assert rc == 1, (out, err)
    assert _blocking_old_at(doc, included.resolve()), "includeIf file not read\n" + out
    assert [f for f in _blocking_old_at(doc, c_glob) if f["field"] == "remote.up.url"], out
    env_rows = [f for f in doc["findings"] if f["location"] == "command line:" and f["kind"] == "url-rewrite"]
    assert len(env_rows) == 1 and env_rows[0]["blocking"] and env_rows[0]["value_class"] == "old", out
    assert _blocking_old_at(doc, (root / "bw-wt").resolve()), out
    assert not [f for f in doc["findings"] if f["location"] == str(bare)], out
    assert "repositories 3;" in out.splitlines()[-1], out


def test_max_seconds_also_bounds_reading_the_repositories_already_found(tmp_path, capsys, monkeypatch):
    """The wall-clock bound covers the remote reading too, so a run over a huge root ends near
    `--max-seconds` with a blocking `truncated` row instead of running on for hours."""
    mod = _tool()
    root = tmp_path / "scan"
    for i in range(3):
        _repo(root / ("c%d" % i), NEW_URL)
    clock = {"now": 1000.0, "reads": 0}
    real = mod._read_git_config

    def slow_read(base, run):
        clock["reads"] += 1
        clock["now"] += 60.0            # each repository's reading costs a simulated minute
        return real(base, run)

    monkeypatch.setattr(mod, "_read_git_config", slow_read)
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["now"])
    rc, out, err, doc = _check(mod, capsys, tmp_path, "--scan-root", root, "--max-seconds", "90")
    assert rc == 1, (out, err)
    cut = [f for f in doc["findings"] if f["kind"] == "truncated" and f["field"] == "max-seconds"]
    assert len(cut) == 1 and cut[0]["blocking"] is True, doc["findings"]
    assert clock["reads"] == 2, "reading went on past the bound: %d repositories read" % clock["reads"]

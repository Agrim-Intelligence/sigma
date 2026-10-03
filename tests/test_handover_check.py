# SPDX-License-Identifier: MIT
"""`tools/handover_check.py check` (issue 397): the read-only holder checker.

In-process: `mod.main(argv, run=fake)`. The fake `run` delegates `git` to the tool's own
`_real_run` (real git, in repositories created under tmp_path) and answers `gh`, `ps` and `lsof` from
canned data, asserting every `gh` argv is a GET (`["gh", "api", <endpoint>]`, none of the write
flags). Slugs are synthetic: `acme-old/widget` (old), `acme-old/widget-private` (new), `acme/demo`.
Every test body starts with `mod = _tool()`, so an absent tool is an AssertionError, never an
import error.
"""
import ast
import hashlib
import importlib.util
import inspect
import itertools
import json
import os
import pathlib
import stat
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "handover_check.py"
LEAK_REFS = ROOT / "tools" / "leak_refs.py"
WRITE_SURFACE = ROOT / "tools" / "readiness" / "write_surface.py"
OLD = "acme-old/widget"
NEW = "acme-old/widget-private"
OLD_URL = "https://github.com/acme-old/widget.git"
NEW_URL = "https://github.com/acme-old/widget-private.git"
OTHER_URL = "https://github.com/acme/demo.git"
FORBIDDEN_FLAGS = ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input", "--jq")
FINDING_KEYS = {"kind", "location", "field", "value_class", "blocking", "note"}
_N = itertools.count(1)


# ------------------------------------------------------------------------------ loading

def _load(path, name):
    assert path.exists(), "%s is missing" % path.relative_to(ROOT)
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tool():
    assert TOOL.exists(), "tools/handover_check.py is missing"
    return _load(TOOL, "handover_check_under_test_%d" % next(_N))


# ------------------------------------------------------------------------------ world

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


def _repo(path, origin=None, files=None, config=None):
    """A real repository with one commit; `origin` becomes remote `origin`; `config` is a
    {key: value} dict of local `git config` settings."""
    path = pathlib.Path(path)
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "t")
    for rel, text in sorted((files or {"README.md": "hello\n"}).items()):
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    if origin:
        _git(path, "remote", "add", "origin", origin)
    for key, value in sorted((config or {}).items()):
        _git(path, "config", key, value)
    return path.resolve()


def _ok(obj):
    return 0, json.dumps(obj), ""


class World(object):
    """The injected `run`: git is real, gh/ps/lsof are canned, every call is recorded."""

    def __init__(self, mod, gh=None, ps=""):
        self.mod, self.gh, self.ps = mod, dict(gh or {}), ps
        self.calls, self.gh_calls, self.violations = [], [], []

    def __call__(self, args, input_text=None, timeout=60):
        args = [str(a) for a in args]
        self.calls.append(args)
        if args[0] == "git":
            return self.mod._real_run(args, input_text, timeout)
        if args[0] == "gh":
            bad = (args[:2] != ["gh", "api"] or len(args) < 3
                   or any(a in FORBIDDEN_FLAGS or a.startswith("--method") or a.startswith("--jq")
                          for a in args[3:]))
            if bad:
                self.violations.append(args)
                raise AssertionError("gh call that is not a plain GET: %r" % (args,))
            endpoint = args[2].split("?")[0]
            self.gh_calls.append(endpoint)
            if endpoint not in self.gh:
                self.violations.append(args)
                raise AssertionError("unexpected gh endpoint %r" % endpoint)
            return self.gh[endpoint]
        if args[0] == "ps":
            return 0, self.ps, ""
        if args[0] == "lsof":
            return 1, "", ""
        self.violations.append(args)
        raise AssertionError("unexpected command %r" % (args,))


def _rest(repo, **over):
    """All seven names-only endpoints for `repo`, empty unless overridden by short key."""
    base = "repos/%s/" % repo
    table = {"secrets": ("actions/secrets", {"total_count": 0, "secrets": []}),
             "variables": ("actions/variables", {"total_count": 0, "variables": []}),
             "org-secrets": ("actions/organization-secrets", {"total_count": 0, "secrets": []}),
             "org-variables": ("actions/organization-variables", {"total_count": 0, "variables": []}),
             "hooks": ("hooks", []), "keys": ("keys", []),
             "environments": ("environments", {"total_count": 0, "environments": []})}
    out = {}
    for short, (suffix, empty) in table.items():
        out[base + suffix] = over.get(short, _ok(empty))
    return out


def _run_check(mod, capsys, world, *argv):
    try:
        rc = mod.main(["handover_check.py", "check"] + [str(a) for a in argv], run=world)
    except SystemExit as exc:
        rc = exc.code
    cap = capsys.readouterr()
    assert not world.violations, world.violations
    return rc, cap.out, cap.err


def _check(mod, capsys, tmp_path, world, *argv, offline=True, old=OLD, new=NEW):
    """-> (rc, stdout, stderr, json_doc|None). Always writes --json to a fresh file outside every repo."""
    out_dir = tmp_path / "json"
    out_dir.mkdir(exist_ok=True)
    jpath = out_dir / ("r%d.json" % next(_N))
    cfg = tmp_path / "claude"
    cfg.mkdir(exist_ok=True)
    args = ["--old", old]
    if new:
        args += ["--new", new]
    args += ["--claude-config", str(cfg)]
    if offline:
        args.append("--offline")
    args += [str(a) for a in argv] + ["--json", str(jpath)]
    rc, out, err = _run_check(mod, capsys, world, *args)
    doc = json.loads(jpath.read_text(encoding="utf-8")) if jpath.exists() else None
    return rc, out, err, doc


def _of(doc, kind, where=None):
    return [f for f in doc["findings"]
            if f["kind"] == kind and (where is None or str(where) in f["location"])]


def _lines(out, prefix):
    return [line for line in out.splitlines() if line.startswith(prefix)]


# ------------------------------------------------------------------------------ holders

def test_planted_old_origin_is_listed_then_none_after_the_simulated_rename(tmp_path, capsys):
    mod = _tool()
    a = _repo(tmp_path / "clones" / "a", OLD_URL)
    b = _repo(tmp_path / "clones" / "b", NEW_URL)
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--clone", a, "--clone", b)
    assert rc == 1, (out, err)
    assert out.splitlines()[0] == ("handover_check: old=%s new=%s repo-id=- rest=offline" % (OLD, NEW))
    blocks = _lines(out, "BLOCK remote-url ")
    assert len(blocks) == 1 and str(a) in blocks[0] and str(b) not in blocks[0], out
    infos = _lines(out, "INFO  remote-url ")
    assert len(infos) == 1 and str(b) in infos[0], out
    remote = _of(doc, "remote-url")
    assert {f["blocking"] for f in _of(doc, "remote-url", a)} == {True}
    assert {f["blocking"] for f in _of(doc, "remote-url", b)} == {False}
    assert len(remote) == 2 and all(set(f) == FINDING_KEYS for f in remote)
    assert doc["counts"]["blocking"] == 1
    last = out.splitlines()[-1]
    assert last.startswith("handover_check: 1 blocking, "), last
    assert "repositories 2; truncated no" in last, last
    # the simulated rename: the owner's step 5 repoints the clone
    _git(a, "remote", "set-url", "origin", NEW_URL)
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--clone", a, "--clone", b)
    assert rc == 0, (out, err)
    assert not _lines(out, "BLOCK "), out
    assert out.splitlines()[-1].startswith("handover_check: 0 blocking, "), out
    assert {f["blocking"] for f in _of(doc, "remote-url")} == {False}
    assert len(_of(doc, "remote-url")) == 2


def test_pushurl_and_url_rewrite_holding_old_are_blocking(tmp_path, capsys):
    mod = _tool()
    c = _repo(tmp_path / "clones" / "c", NEW_URL, config={"remote.origin.pushurl": OLD_URL})
    d = _repo(tmp_path / "clones" / "d", NEW_URL,
              config={"url." + OLD_URL + ".insteadOf": "https://github.com/acme-old/widget"})
    e = _repo(tmp_path / "clones" / "e", NEW_URL,
              config={"url." + OLD_URL + ".pushInsteadOf": "https://github.com/acme-old/widget"})
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--clone", c, "--clone", d,
                               "--clone", e)
    assert rc == 1, (out, err)
    assert {f["blocking"] for f in _of(doc, "remote-pushurl", c)} == {True}
    assert {f["blocking"] for f in _of(doc, "remote-url", c)} == {False}
    assert {f["blocking"] for f in _of(doc, "url-rewrite", d)} == {True}
    assert {f["blocking"] for f in _of(doc, "url-rewrite", e)} == {True}
    assert any(l.startswith("BLOCK remote-pushurl ") and str(c) in l for l in out.splitlines()), out
    assert any(l.startswith("BLOCK url-rewrite ") and str(d) in l for l in out.splitlines()), out
    assert doc["counts"]["blocking"] == 3


def test_worktrees_of_the_current_repository_are_read_once(tmp_path, capsys, monkeypatch):
    mod = _tool()
    main = _repo(tmp_path / "main", OLD_URL)
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "wt-branch", str(wt))
    wt = wt.resolve()
    (wt / ".sdlc").mkdir()
    (wt / ".sdlc" / "config.json").write_text(
        json.dumps({"discovery": {"github": {"repo": OLD}}}), encoding="utf-8")
    monkeypatch.chdir(main)
    w = World(mod)
    rc, out, err, doc = _check(mod, capsys, tmp_path, w)
    assert rc == 1, (out, err)
    listed = [c for c in w.calls if c[0] == "git" and "worktree" in c and "list" in c]
    assert len(listed) == 1 and "--porcelain" in listed[0], listed
    assert len(_of(doc, "remote-url")) == 1 and _of(doc, "remote-url")[0]["blocking"] is True
    # the linked worktree's own .sdlc/config.json is seen without being named
    seen = _of(doc, "config-repo", wt)
    assert len(seen) == 1 and seen[0]["blocking"] is True, doc["findings"]
    assert "repositories 1;" in out.splitlines()[-1], out
    # naming the worktree as a clone as well dedupes by common dir: nothing is doubled
    rc, out, err, doc2 = _check(mod, capsys, tmp_path, World(mod), "--clone", wt)
    assert "repositories 1;" in out.splitlines()[-1], out
    assert len(_of(doc2, "remote-url")) == 1
    assert len(_of(doc2, "config-repo", wt)) == 1


def test_sdlc_config_keys_are_classified(tmp_path, capsys):
    mod = _tool()
    cfg = {"discovery": {"github": {"repo": OLD, "project": {"owner": "acme-old", "number": 7}}},
           "ledger": {"remote": "origin", "handoff": {"upstream_repo": OLD}},
           "work": {"remote": "upstream"}}
    r = _repo(tmp_path / "clones" / "r", NEW_URL, files={"README.md": "x\n", ".sdlc/config.json":
                                                         json.dumps(cfg)})
    _git(r, "remote", "add", "upstream", OLD_URL)
    cfg2 = {"discovery": {"github": {"repo": NEW}}, "ledger": {"handoff": {"upstream_repo": NEW}}}
    r2 = _repo(tmp_path / "clones" / "other", NEW_URL,
               files={"README.md": "x\n", ".sdlc/config.json": json.dumps(cfg2)})
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--clone", r, "--clone", r2)
    assert rc == 1, (out, err)
    repo = _of(doc, "config-repo", r)
    assert len(repo) == 1 and repo[0]["blocking"] is True
    assert repo[0]["field"] == "discovery.github.repo"
    up = _of(doc, "config-upstream", r)
    assert len(up) == 1 and up[0]["blocking"] is True
    assert up[0]["field"] == "ledger.handoff.upstream_repo"
    rem = [f for f in _of(doc, "config-remote", r) if f["field"] == "work.remote"]
    assert len(rem) == 1 and rem[0]["blocking"] is True, doc["findings"]
    ledger_remote = [f for f in _of(doc, "config-remote", r) if f["field"] == "ledger.remote"]
    assert len(ledger_remote) == 1 and ledger_remote[0]["blocking"] is False
    proj = _of(doc, "config-project", r)
    assert len(proj) == 1 and proj[0]["blocking"] is False
    assert "holds no repository name" in proj[0]["note"]
    assert any(l.startswith("INFO  config-project ") and "holds no repository name" in l
               for l in out.splitlines()), out
    # the checkout whose config already names the new repository blocks nothing
    assert {f["blocking"] for f in doc["findings"] if str(r2) in f["location"]} == {False}
    assert _of(doc, "config-repo", r2) and _of(doc, "config-upstream", r2)


def test_tracked_text_and_workflows(tmp_path, capsys, monkeypatch):
    mod = _tool()
    r = _repo(tmp_path / "r", NEW_URL, files={
        "NOTES.md": "the old name " + OLD + " appears here\n",
        ".github/workflows/ci.yml": "jobs:\n  x:\n    env:\n      REPO: " + OLD + "\n",
        ".github/workflows/new.yml": "jobs:\n  y:\n    env:\n      REPO: " + NEW + "\n"})
    monkeypatch.chdir(r)
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod))
    assert rc == 1, (out, err)
    wf = _of(doc, "workflow", "ci.yml")
    assert len(wf) == 1 and wf[0]["blocking"] is True, doc["findings"]
    assert not [f for f in doc["findings"] if "new.yml" in f["location"] and f["blocking"]]
    text = _of(doc, "tracked-text", "NOTES.md")
    assert len(text) == 1 and text[0]["blocking"] is False
    assert any(l.startswith("BLOCK workflow ") and "ci.yml" in l for l in out.splitlines()), out
    assert any(l.startswith("INFO  tracked-text ") and "NOTES.md" in l for l in out.splitlines()), out
    assert doc["counts"]["blocking"] == 1


def test_marketplace_records_holding_old_are_blocking(tmp_path, capsys):
    mod = _tool()
    cfg = tmp_path / "claude"
    (cfg / "plugins").mkdir(parents=True)
    src = lambda repo: {"source": {"source": "github", "repo": repo}}  # noqa: E731
    (cfg / "plugins" / "known_marketplaces.json").write_text(
        json.dumps({"mk-old": src(OLD), "mk-new": src(NEW)}), encoding="utf-8")
    (cfg / "settings.json").write_text(
        json.dumps({"extraKnownMarketplaces": {"mk-set": src(OLD)}}), encoding="utf-8")
    m1 = _repo(cfg / "plugins" / "marketplaces" / "m1", OLD_URL)
    m2 = _repo(cfg / "plugins" / "marketplaces" / "m2", NEW_URL)
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod))
    assert rc == 1, (out, err)
    market = _of(doc, "marketplace")
    blocking = [f for f in market if f["blocking"]]
    assert len(blocking) == 3, market
    for needle in ("known_marketplaces.json", "settings.json", "m1"):
        assert [f for f in blocking if needle in f["location"]], (needle, market)
    quiet = [f for f in market if not f["blocking"]]
    assert len(quiet) == 2 and any(str(m2) in f["location"] for f in quiet), market
    assert len(_lines(out, "BLOCK marketplace ")) == 3, out
    assert str(m1) in "\n".join(_lines(out, "BLOCK marketplace ")), out


def test_gh_repo_environment_variable_is_a_holder(tmp_path, capsys, monkeypatch):
    mod = _tool()
    monkeypatch.setenv("GH_REPO", OLD)
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod))
    assert rc == 1, (out, err)
    env = _of(doc, "env")
    assert len(env) == 1 and env[0]["blocking"] is True and "GH_REPO" in json.dumps(env[0])
    assert any(l.startswith("BLOCK env ") and "GH_REPO" in l for l in out.splitlines()), out
    monkeypatch.setenv("GH_REPO", NEW)
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod))
    assert rc == 0, (out, err)
    env = _of(doc, "env")
    assert len(env) == 1 and env[0]["blocking"] is False
    assert any(l.startswith("INFO  env ") and "GH_REPO" in l for l in out.splitlines()), out


# ------------------------------------------------------------------------------ discovery

def test_scan_root_finds_nested_clones_and_caps_truncate_loudly(tmp_path, capsys):
    mod = _tool()
    root = tmp_path / "scan"
    one = _repo(root / "team" / "proj" / "clone1", OLD_URL)
    two = _repo(root / "clone2", NEW_URL)
    deep = _repo(root.joinpath(*"p q r s t u v w deep".split()), OLD_URL)
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--scan-root", root)
    assert rc == 1, (out, err)
    blocks = "\n".join(_lines(out, "BLOCK remote-url "))
    assert str(one) in blocks and str(deep) not in out, out
    assert any(str(two) in l for l in _lines(out, "INFO  remote-url ")), out
    assert "repositories 2;" in out.splitlines()[-1], out
    assert doc["caps"] == {"max_depth": 5, "max_repos": 2000, "truncated": False}
    # the repository-count cap: both clones are clean, so the truncation is the ONLY block
    root2 = tmp_path / "scan2"
    _repo(root2 / "x1", NEW_URL)
    _repo(root2 / "x2", NEW_URL)
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--scan-root", root2,
                               "--max-repos", "1")
    assert rc == 1, (out, err)
    assert [f["kind"] for f in doc["findings"] if f["blocking"]] == ["truncated"], doc["findings"]
    assert len(_lines(out, "BLOCK truncated ")) == 1 and len(_lines(out, "BLOCK ")) == 1, out
    assert out.splitlines()[-1].endswith("truncated yes"), out
    assert doc["caps"]["truncated"] is True and doc["caps"]["max_repos"] == 1
    # the depth cap
    root3 = tmp_path / "scan3"
    shallow = _repo(root3 / "shallow", OLD_URL)
    far = _repo(root3.joinpath(*"p q r s deep".split()), OLD_URL)
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--scan-root", root3,
                               "--max-depth", "2")
    assert str(shallow) in "\n".join(_lines(out, "BLOCK remote-url ")), out
    assert str(far) not in out, out
    assert doc["caps"]["max_depth"] == 2


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads everything")
def test_unreadable_subdirectory_is_a_finding_and_unreadable_root_refuses(tmp_path, capsys):
    mod = _tool()
    root = tmp_path / "scan"
    _repo(root / "ok", NEW_URL)
    locked = root / "locked"
    locked.mkdir()
    locked.chmod(0)
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    sealed.chmod(0)
    try:
        rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--scan-root", root)
        assert rc == 1, (out, err)
        bad = _of(doc, "unreadable", "locked")
        assert len(bad) == 1 and bad[0]["blocking"] is True, doc["findings"]
        assert any(l.startswith("BLOCK unreadable ") and "locked" in l for l in out.splitlines()), out
        rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--scan-root", sealed)
        assert rc == 2 and out == "" and doc is None, (rc, out, err)
        assert err.startswith("handover_check: REFUSED [scan-root-unreadable] "), err
    finally:
        locked.chmod(0o755)
        sealed.chmod(0o755)


# ------------------------------------------------------------------------------ REST

def test_rest_lists_names_only_and_404_is_unreadable_not_blocking(tmp_path, capsys):
    mod = _tool()
    gh = _rest(NEW,
               secrets=_ok({"total_count": 2, "secrets": [{"name": "DEPLOY_KEY_ONE"}, {"name": "SECOND_ONE"}]}),
               variables=_ok({"total_count": 1, "variables": [{"name": "VAR_ONE", "value": "vv-zq-valueone"}]}),
               **{"org-secrets": _ok({"total_count": 1, "secrets": [{"name": "ORG_SEC_ONE"}]}),
                  "org-variables": _ok({"total_count": 1, "variables": [
                      {"name": "ORG_VAR_ONE", "value": "vv-zq-orgvalue"}]}),
                  "hooks": (1, "", "gh: Not Found (HTTP 404)"),
                  "keys": (1, "", "gh: Resource not accessible by integration (HTTP 403)"),
                  "environments": _ok({"total_count": 1, "environments": [{"name": "production", "id": 9}]})})
    w = World(mod, gh=gh)
    rc, out, err, doc = _check(mod, capsys, tmp_path, w, offline=False)
    assert rc == 0, (out, err)
    assert out.splitlines()[0].endswith("rest=ran"), out
    want = ["repos/%s/%s" % (NEW, s) for s in (
        "actions/secrets", "actions/variables", "actions/organization-secrets",
        "actions/organization-variables", "hooks", "keys", "environments")]
    assert sorted(set(w.gh_calls)) == sorted(want), w.gh_calls
    for kind, name in (("rest-secret", "DEPLOY_KEY_ONE"), ("rest-secret", "SECOND_ONE"),
                       ("rest-variable", "VAR_ONE"), ("rest-org-secret", "ORG_SEC_ONE"),
                       ("rest-org-variable", "ORG_VAR_ONE"), ("rest-environment", "production")):
        found = _of(doc, kind)
        assert found and not any(f["blocking"] for f in found), (kind, doc["findings"])
        assert name in json.dumps(found), (kind, name, found)
    unreadable = _of(doc, "rest-unreadable")
    assert len(unreadable) == 2 and not any(f["blocking"] for f in unreadable), unreadable
    assert "hooks" in json.dumps(unreadable) and "keys" in json.dumps(unreadable)
    everything = out + json.dumps(doc)
    assert "vv-zq-valueone" not in everything and "vv-zq-orgvalue" not in everything
    assert doc["rest"] == "ran"
    # without --new the repository asked is --old
    w = World(mod, gh=_rest(OLD))
    rc, out, err, doc = _check(mod, capsys, tmp_path, w, offline=False, new=None)
    assert rc == 0, (out, err)
    assert out.splitlines()[0] == "handover_check: old=%s new=- repo-id=- rest=ran" % OLD, out
    assert sorted(set(w.gh_calls)) == sorted(c.replace(NEW, OLD) for c in want), w.gh_calls


def test_id_check_flags_a_new_name_on_another_repository_and_reports_old_name_taken(tmp_path, capsys):
    mod = _tool()

    def world(old_id, new_id, new_private):
        gh = {"repos/" + OLD: _ok({"id": old_id, "full_name": OLD, "private": False}),
              "repos/" + NEW: _ok({"id": new_id, "full_name": NEW, "private": new_private})}
        gh.update(_rest(NEW))
        return World(mod, gh=gh)

    # --new is another repository: BLOCK; --old names yet another: INFO old-name-taken
    rc, out, err, doc = _check(mod, capsys, tmp_path, world(222, 333, True), "--repo-id", "111",
                               offline=False)
    assert rc == 1, (out, err)
    assert out.splitlines()[0] == ("handover_check: old=%s new=%s repo-id=111 rest=ran" % (OLD, NEW))
    mismatch = _of(doc, "id-mismatch")
    assert len(mismatch) == 1 and mismatch[0]["blocking"] is True, doc["findings"]
    taken = _of(doc, "old-name-taken")
    assert len(taken) == 1 and taken[0]["blocking"] is False
    assert len(_lines(out, "BLOCK id-mismatch ")) == 1 and len(_lines(out, "INFO  old-name-taken ")) == 1
    # --new is the recorded repository and private; the old name now belongs to someone else
    rc, out, err, doc = _check(mod, capsys, tmp_path, world(222, 111, True), "--repo-id", "111",
                               offline=False)
    assert rc == 0, (out, err)
    assert not _of(doc, "id-mismatch") and len(_of(doc, "old-name-taken")) == 1
    # --new has the recorded id but is not private
    rc, out, err, doc = _check(mod, capsys, tmp_path, world(222, 111, False), "--repo-id", "111",
                               offline=False)
    assert rc == 1, (out, err)
    assert len(_of(doc, "id-mismatch")) == 1 and _of(doc, "id-mismatch")[0]["blocking"] is True
    # the old name redirects to the renamed repository: nothing is taken, nothing mismatches
    rc, out, err, doc = _check(mod, capsys, tmp_path, world(111, 111, True), "--repo-id", "111",
                               offline=False)
    assert rc == 0, (out, err)
    assert not _of(doc, "id-mismatch") and not _of(doc, "old-name-taken")


def test_offline_makes_no_gh_call(tmp_path, capsys):
    mod = _tool()
    w = World(mod, gh={})
    rc, out, err, doc = _check(mod, capsys, tmp_path, w, "--repo-id", "5")
    assert rc == 0, (out, err)
    assert w.gh_calls == [] and not [c for c in w.calls if c[0] == "gh"], w.calls
    assert out.splitlines()[0] == ("handover_check: old=%s new=%s repo-id=5 rest=offline" % (OLD, NEW))
    assert doc["rest"] == "offline" and doc["repo_id"] in (5, "5")
    assert out.splitlines()[-1].startswith("handover_check: 0 blocking, 0 informational; repositories")


# ------------------------------------------------------------------------------ writers

def test_writers_block_only_when_attributed(tmp_path, capsys, monkeypatch):
    mod = _tool()
    a = _repo(tmp_path / "clones" / "a", OLD_URL)
    neutral = _repo(tmp_path / "clones" / "neutral", OTHER_URL)
    cwds = {"4002": str(a), "4003": str(neutral), "4004": None}
    monkeypatch.setattr(mod, "_process_cwd", lambda pid, *x, **k: cwds.get(str(pid)))
    ps = "\n".join([
        "  4001       01:23 python3 %s/skills/agrim-loop/scripts/watch_daemon.py --watch" % a,
        " 4002    2-03:04:05 python3 skills/agrim-loop/scripts/loop.py run",
        "  4003       00:10 python3 /srv/other/skills/agrim-loop/scripts/watch_daemon.py",
        "  4004       00:20 python3 skills/agrim-loop/scripts/loop.py",
        "  7005       00:30 python3 -m pytest tests/test_loop.py",
        "  7006       00:40 python3 tests/test_watch_daemon.py", ""])
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod, ps=ps), "--clone", a,
                               "--clone", neutral)
    assert rc == 1, (out, err)
    writers = _of(doc, "writer")
    by_pid = {}
    for f in writers:
        for pid in ("4001", "4002", "4003", "4004", "7005", "7006"):
            if pid in json.dumps(f):
                by_pid[pid] = f
    assert by_pid["4001"]["blocking"] is True      # its path is in the command line
    assert by_pid["4002"]["blocking"] is True      # relative path, attributed by cwd
    assert by_pid["4003"]["blocking"] is False     # a checkout that holds no old/new name
    assert by_pid["4004"]["blocking"] is False and "cwd unreadable" in by_pid["4004"]["note"]
    assert "7005" not in by_pid and "7006" not in by_pid, writers  # test_loop.py is not loop.py
    assert len(_lines(out, "BLOCK writer ")) == 2, out
    assert any(l.startswith("INFO  writer ") and "cwd unreadable" in l for l in out.splitlines()), out
    # heartbeats: fresh on a holder blocks, stale is informational
    h1 = _repo(tmp_path / "clones" / "h1", OLD_URL)
    h2 = _repo(tmp_path / "clones" / "h2", OLD_URL)
    now = time.time()
    for repo, age in ((h1, 60), (h2, 3600)):
        beat = repo / ".sdlc" / "state" / "watch.heartbeat"
        beat.parent.mkdir(parents=True)
        beat.write_text("1\n", encoding="utf-8")
        os.utime(str(beat), (now - age, now - age))
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--clone", h1, "--clone", h2)
    fresh, stale = _of(doc, "heartbeat", h1), _of(doc, "heartbeat", h2)
    assert len(fresh) == 1 and fresh[0]["blocking"] is True, doc["findings"]
    assert len(stale) == 1 and stale[0]["blocking"] is False, doc["findings"]


# ------------------------------------------------------------------------------ output + safety

def test_json_is_create_once_private_and_refused_inside_a_work_tree(tmp_path, capsys):
    mod = _tool()
    a = _repo(tmp_path / "clones" / "a", OLD_URL)
    out_dir = tmp_path / "evidence"
    out_dir.mkdir()
    target = out_dir / "r.json"
    cfg = tmp_path / "claude"
    cfg.mkdir()
    base = ["--old", OLD, "--new", NEW, "--claude-config", str(cfg), "--offline", "--clone", str(a)]
    rc, out, err = _run_check(mod, capsys, World(mod), *(base + ["--json", str(target)]))
    assert rc == 1, (out, err)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert set(doc) == {"schema", "old", "new", "repo_id", "rest", "generated_at", "findings",
                        "counts", "caps"}
    assert doc["schema"] == "sigma.handover-check/v1"
    assert (doc["old"], doc["new"], doc["repo_id"], doc["rest"]) == (OLD, NEW, None, "offline")
    assert "T" in doc["generated_at"]
    assert doc["counts"]["blocking"] >= 1 and set(doc["counts"]) == {"blocking", "informational"}
    assert set(doc["caps"]) == {"max_depth", "max_repos", "truncated"}
    assert doc["findings"] and all(set(f) == FINDING_KEYS for f in doc["findings"])
    assert os.listdir(str(out_dir)) == ["r.json"]
    before = target.read_bytes()
    rc, out, err = _run_check(mod, capsys, World(mod), *(base + ["--json", str(target)]))
    assert rc == 2 and out == "", (rc, out, err)
    assert err.startswith("handover_check: REFUSED [json-exists] "), err
    assert target.read_bytes() == before and os.listdir(str(out_dir)) == ["r.json"]
    inside = _repo(tmp_path / "wt", None)
    rc, out, err = _run_check(mod, capsys, World(mod), *(base + ["--json", str(inside / "o.json")]))
    assert rc == 2 and out == "", (rc, out, err)
    assert err.startswith("handover_check: REFUSED [json-inside-work-tree] "), err
    assert not (inside / "o.json").exists()
    assert sorted(p.name for p in inside.iterdir() if p.name != ".git") == ["README.md"]


def test_check_writes_nothing(tmp_path, capsys, monkeypatch):
    mod = _tool()
    cfg = tmp_path / "claude"
    (cfg / "plugins").mkdir(parents=True)
    (cfg / "plugins" / "known_marketplaces.json").write_text(
        json.dumps({"m": {"source": {"source": "github", "repo": OLD}}}), encoding="utf-8")
    a = _repo(tmp_path / "clones" / "a", OLD_URL, config={"remote.origin.pushurl": OLD_URL},
              files={"README.md": "x\n", ".sdlc/config.json": json.dumps(
                  {"discovery": {"github": {"repo": OLD}}})})
    b = _repo(tmp_path / "clones" / "b", NEW_URL)
    monkeypatch.chdir(a)

    def snapshot():
        state = {}
        for top in (a, b, cfg):
            state["config:" + str(top)] = (_git(top, "config", "--list", "--show-origin")
                                           if (top / ".git").exists() else "")
            for dirpath, _dirs, names in os.walk(str(top)):
                for name in names:
                    path = os.path.join(dirpath, name)
                    with open(path, "rb") as fh:
                        state[path] = hashlib.sha256(fh.read()).hexdigest()
        state["tree"] = sorted(str(p) for p in tmp_path.rglob("*") if p.name != "json")
        return state

    before = snapshot()
    rc, out, err, doc = _check(mod, capsys, tmp_path, World(mod), "--clone", b)
    assert rc == 1 and _of(doc, "remote-url", a), (out, err)
    after = snapshot()
    after["tree"] = [p for p in after["tree"] if "/json" not in p]
    before["tree"] = [p for p in before["tree"] if "/json" not in p]
    assert after == before


@pytest.mark.parametrize("code", ["windows", "bad-slug", "owner-differs", "same-name",
                                  "clone-unreadable"])
def test_check_refusals(code, tmp_path, capsys, monkeypatch):
    mod = _tool()
    argv = ["--old", OLD, "--new", NEW, "--offline", "--claude-config", str(tmp_path)]
    if code == "windows":
        monkeypatch.setattr(mod, "_platform_is_windows", lambda: True)
    elif code == "bad-slug":
        argv[1] = "not a slug"
    elif code == "owner-differs":
        argv[3] = "other-owner/widget-private"
    elif code == "same-name":
        argv[3] = OLD
    else:
        argv += ["--clone", str(tmp_path / "no-such-clone")]
    w = World(mod)
    rc, out, err = _run_check(mod, capsys, w, *argv)
    assert rc == 2 and out == "", (rc, out, err)
    assert err.startswith("handover_check: REFUSED [%s] " % code), err
    assert len(err.strip().splitlines()) == 1, err
    assert not [c for c in w.calls if c[0] == "gh"]


# ------------------------------------------------------------------------------ structure

def test_checker_write_sites_are_only_the_json_output():
    mod = _tool()
    ws = _load(WRITE_SURFACE, "write_surface_under_test_%d" % next(_N))
    rows = ws.scan_paths(ROOT, [TOOL])
    assert rows, "the checker writes its --json file, so it must show a write row"
    assert {r["rule"] for r in rows} <= {"fs-write", "fs-remove"}, rows
    assert {r["function"] for r in rows} == {"_write_json_once"}, rows
    assert "fs-write" in {r["rule"] for r in rows}
    assert hasattr(mod, "_write_json_once")


def test_gh_argv_literals_are_api_get_only():
    mod = _tool()
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "gh":
                found.append(node)
    assert found, "the checker has no gh argv literal at all"
    for node in found:
        second = node.elts[1] if len(node.elts) > 1 else None
        assert isinstance(second, ast.Constant) and second.value == "api", ast.dump(node)
        for elt in node.elts[1:]:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                assert elt.value not in FORBIDDEN_FLAGS and not elt.value.startswith("--method"), elt.value
    assert mod.main is not None


def test_verbatim_copies_equal_leak_refs():
    mod = _tool()
    lr = _load(LEAK_REFS, "leak_refs_under_test_%d" % next(_N))
    assert mod._GIT_SCRUB == lr._GIT_SCRUB
    assert mod._NOT_A_REPO == lr._NOT_A_REPO
    assert mod._REPO_RE.pattern == lr._REPO_RE.pattern
    assert inspect.getsource(mod._real_run) == inspect.getsource(lr._real_run)
    assert issubclass(mod.Refused, Exception) and mod.Refused is not lr.Refused

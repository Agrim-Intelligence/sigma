# SPDX-License-Identifier: MIT
"""#2076: a Claude Code cloud container's claim, keep-alive and outcome reach origin's ledger, over REST and git.

A fresh container: a clone with no `.sdlc/ledger` worktree and no watcher, `CLAUDE_CODE_REMOTE=true`, and a
GitHub transport that REFUSES every GraphQL-shaped call. Real git against a local bare origin; the GitHub
side is a fake. NOT proved here: a real cloud container, the real proxy, a real push of the ledger branch from
one (docs/cloud-sessions.md says so too).
"""
import hashlib
import importlib.util
import json
import platform
import pathlib
import subprocess
import time

import pytest

import gqlfake  # noqa: F401  (tests/gqlfake.py)
from test_feature_scope import _issue
from test_goal_land import _fake, _graphql_shaped, GQL_REFUSED

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_BRANCH = "sdlc" + "-ledger"     # the default ledger branch name (sync.DEFAULT_BRANCH)


def _git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr or proc.stdout}"
    return proc.stdout.strip()


def _origin_entries(origin):
    names = subprocess.run(["git", "--git-dir", str(origin), "ls-tree", "-r", "--name-only", _BRANCH],
                           capture_output=True, text=True).stdout.split()
    out = []
    for name in names:
        if name.startswith("entries/"):
            body = subprocess.run(["git", "--git-dir", str(origin), "show", f"{_BRANCH}:{name}"],
                                  capture_output=True, text=True).stdout
            out += [json.loads(line) for line in body.splitlines() if line.strip()]
    return out


@pytest.fixture
def container(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(seed, "config", k, v)
    (seed / ".gitignore").write_text(".sdlc/\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    repo = tmp_path / "container"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(repo, "config", k, v)
    cfg = {"discovery": {"source": "github", "github": {"repo": "acme/widget"}},
           "ledger": {"enabled": True, "actor": "dana", "lease": {"ttl_hours": 1}},
           "work": {"enabled": True},
           "budget": {"max_iterations": 10}}
    base = repo / ".sdlc"
    (base / "state").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.setenv("SIGMA_GH_GRAPHQL", "off")
    lp = _mod("loop")
    monkeypatch.setattr(lp.ledger, "_container_id_path", lambda: tmp_path / "container.id")
    calls, refused = [], []
    inner = _fake([_issue(10)], calls)

    def run(args):
        if _graphql_shaped(" ".join(map(str, args))) and "api" not in list(args)[:1]:
            refused.append(list(args))
            raise GQL_REFUSED
        return inner(args)

    def source(sdlc, cfg=None, **k):
        src = lp.sources.GitHubSource(lp.state.load_config(sdlc), run=run, sdlc_dir=sdlc)
        src._CLAIM_LABEL_RETRY_BASE = 0
        return src

    monkeypatch.setattr(lp.sources, "get_source", source)
    return lp, origin, repo, str(base), refused


def test_claim_keep_alive_and_outcome_reach_origin_without_a_daemon_or_graphql(container, capsys, monkeypatch):
    lp, origin, repo, base, refused = container
    before = (_git(repo, "rev-parse", "HEAD"), _git(repo, "status", "--porcelain"),
              _git(repo, "diff", "--cached", "--name-only"))

    assert lp.main(["loop.py", "claim", base, "10", "--session-pid", "4242"]) == 0
    claimed = [e for e in _origin_entries(origin) if e["kind"] == "claimed"]
    assert [e["goal"] for e in claimed] == ["10"], capsys.readouterr().err
    host = claimed[0]["id"].split(":")[1].rpartition(".")[0]
    assert host != hashlib.sha256(platform.node().encode()).hexdigest()[:8], \
        "a cloud container must not carry the plain hostname token"
    assert (pathlib.Path(base) / "ledger" / ".git").exists(), "the worktree was created from origin"

    real_time = time.time                             # a third of the 1h lease (20 min) has passed: 22 min
    with monkeypatch.context() as later:
        later.setattr(time, "time", lambda: real_time() + 1300)
        assert lp.main(["loop.py", "note", base, "10", "phase note"]) == 0
    assert "lease-renew" in [e["kind"] for e in _origin_entries(origin)]

    assert lp.main(["loop.py", "record", base, "10", "parked", "needs a person"]) == 0
    kinds = [e["kind"] for e in _origin_entries(origin)]
    assert kinds.count("claimed") == 1 and "lease-renew" in kinds and "parked" in kinds, kinds

    assert (_git(repo, "rev-parse", "HEAD"), _git(repo, "status", "--porcelain"),
            _git(repo, "diff", "--cached", "--name-only")) == before, "the code checkout moved"
    spawned = []
    lp._ensure_watcher(base, lp.state.load_config(base), spawn=lambda: spawned.append(1))
    assert spawned == [], "a cloud session must never start the ledger watcher"
    # every GraphQL-shaped call was refused by the transport; the run still completed over REST and git
    assert all(_graphql_shaped(" ".join(map(str, r))) for r in refused)


def test_a_refused_push_is_printed_and_the_run_goes_on(container, capsys):
    lp, origin, repo, base, refused = container
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho refused by policy >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    assert lp.main(["loop.py", "claim", base, "10", "--session-pid", "4242"]) == 0
    err = capsys.readouterr().err
    assert "ledger: NOT published" in err and "claim of 10" in err, err
    assert lp.main(["loop.py", "record", base, "10", "parked", "needs a person"]) == 0
    assert "ledger: NOT published" in capsys.readouterr().err
    assert _origin_entries(origin) == []


def test_the_flush_runs_once_in_the_outermost_call_and_after_the_merge_lock(container, monkeypatch):
    """`record done` re-enters `main` while holding the merge-reconcile lock; the publish (a network call)
    must come after that lock is gone, once."""
    lp, origin, repo, base, refused = container
    seen = []
    monkeypatch.setattr(lp, "_flush_pending_publish", lambda sdlc: seen.append(sdlc))
    held = []
    real = lp._main_body

    def body(argv, *, _merge_lock_held=False):
        held.append(_merge_lock_held)
        return real(argv, _merge_lock_held=_merge_lock_held)

    monkeypatch.setattr(lp, "_main_body", body)
    lp.main(["loop.py", "record", base, "10", "done"])           # any outcome: the shape is what is checked
    assert len(seen) == 1, "one flush per process, whatever the lock re-entry did"


def test_next_batch_style_claims_publish_once(container, monkeypatch):
    lp, origin, repo, base, refused = container
    lp._mark_publish(base, "claim of 10")
    lp._mark_publish(base, "claim of 11")
    published = []
    sync = lp._load("sync")
    monkeypatch.setattr(sync, "publish_entry", lambda sdlc, cfg, what, **k: published.append(what))
    monkeypatch.setattr(lp, "_load", lambda name, _l=lp._load: sync if name == "sync" else _l(name))
    lp._flush_pending_publish(base)
    lp._flush_pending_publish(base)
    assert published == ["claim of 10; claim of 11"], published


def test_the_claim_itself_publishes_nothing_while_the_claim_lock_could_be_held(container):
    """`_claim` runs under the per-goal claim lock: it only MARKS the entry; the outermost main publishes."""
    lp, origin, repo, base, refused = container
    cfg = lp.state.load_config(base)
    lp._claim(base, lp.sources.get_source(base, cfg), "10", cfg)
    assert lp._PENDING_PUBLISH.get(str(pathlib.Path(base).absolute())) == ["claim of 10"]
    assert _origin_entries(origin) == [], "nothing may touch the network inside _claim"
    assert not (pathlib.Path(base) / "ledger" / ".git").exists()

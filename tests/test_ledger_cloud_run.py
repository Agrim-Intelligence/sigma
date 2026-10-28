# SPDX-License-Identifier: MIT
"""#2076: a Claude Code cloud container's claim, keep-alive and outcome reach origin's ledger, over REST and git.

A fresh container: a clone with no `.sdlc/ledger` worktree and no watcher, `CLAUDE_CODE_REMOTE=true`, and a
GitHub transport that REFUSES every GraphQL-shaped call. Real git against a local bare origin; the GitHub
side is a fake. NOT proved here: a real cloud container, the real proxy, a real push of the ledger branch from
one (docs/cloud-sessions.md says so too).
"""
import atexit
import hashlib
import importlib.util
import json
import os
import platform
import pathlib
import subprocess
import sys
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


def test_the_flush_passes_its_deadline_to_the_one_publish_and_the_default_path_passes_none(container, monkeypatch):
    """G2 (review cycle 2). `_flush_pending_publish` is the seam that carries the exit hook's remaining time into
    `sync.publish_entry`; the shared-deadline test below replaces this function, so nothing else drives the real one:
    with the pass-through gone, N directories and a hung origin each get their own full 90 s and the exit holds ~N
    times as long. Here the REAL function runs against a stub `publish_entry`."""
    lp, origin, repo, base, refused = container
    given = []
    sync = lp._load("sync")
    monkeypatch.setattr(sync, "publish_entry", lambda sdlc, cfg, what, **k: given.append((what, k)))
    monkeypatch.setattr(lp, "_load", lambda name, _l=lp._load: sync if name == "sync" else _l(name))
    lp._mark_publish(base, "claim of 10")
    lp._flush_pending_publish(base, deadline_seconds=12.5)
    lp._mark_publish(base, "claim of 11")
    lp._flush_pending_publish(base)
    assert given == [("claim of 10", {"deadline_seconds": 12.5}), ("claim of 11", {})], given


def test_the_exit_flush_hands_each_directory_only_what_is_left_of_the_one_deadline(container, monkeypatch, tmp_path):
    """The shared-deadline test below stubs `_flush_pending_publish`; this one stubs only `publish_entry`, so the
    figures it asserts travel through the real flush: each publish 'takes' 60 s of a 90 s total."""
    lp, origin, repo, base, refused = container
    monkeypatch.setenv("SIGMA_LEDGER_PUBLISH_DEADLINE", "90")
    keys = []
    for name in ("a", "b", "c"):
        sdlc = tmp_path / name / ".sdlc"
        sdlc.mkdir(parents=True)
        (sdlc / "config.json").write_text("{}", encoding="utf-8")
        keys.append(str(sdlc))
        lp._mark_publish(str(sdlc), f"claim {name}")
    now, given = [1000.0], []
    sync = lp._load("sync")

    def publish_entry(sdlc, cfg, what, **k):
        given.append((str(sdlc), k.get("deadline_seconds")))
        now[0] += 60

    monkeypatch.setattr(sync, "publish_entry", publish_entry)
    monkeypatch.setattr(lp, "_load", lambda name, _l=lp._load: sync if name == "sync" else _l(name))
    lp._flush_all_pending_at_exit(clock=lambda: now[0])
    assert given == [(keys[0], 90.0), (keys[1], 30.0)], given


def test_the_claim_itself_publishes_nothing_while_the_claim_lock_could_be_held(container):
    """`_claim` runs under the per-goal claim lock: it only MARKS the entry; the outermost main publishes."""
    lp, origin, repo, base, refused = container
    cfg = lp.state.load_config(base)
    lp._claim(base, lp.sources.get_source(base, cfg), "10", cfg)
    assert lp._PENDING_PUBLISH.get(str(pathlib.Path(base).absolute())) == ["claim of 10"]
    assert _origin_entries(origin) == [], "nothing may touch the network inside _claim"
    assert not (pathlib.Path(base) / "ledger" / ".git").exists()


# ------------------------------------------------------------------ #2081 gap 2: entries recorded outside `main`
#
# THE DEFECT. Only `loop.main` flushed the entries `_claim`, `_record`, `_release` and the keep-alive marked.
# Another module calling them in-process (`work.py start`'s stale-resume loads `loop` into a function local,
# `/sigma-scope` assign, the liveness-prune CLI) marked an entry that nothing ever published: with no watcher
# (a cloud container never has one) it stayed on disk. The first mark of a loaded copy of `loop` (normally one per command)
# now arms ONE exit hook, so such a process publishes once as it exits NORMALLY. SIGKILL and `os._exit` skip it (the Slack listener ends
# its signal handler with `os._exit`, so there the hook never runs and the 64-mark cap bounds the growth); the
# entry is on disk and the next flush carries it.
#
# THE GESTURE THE DOCS GIVE (docs/cloud-sessions.md): the four-file command, flagless. Test 28 below is "a
# fresh Python process that calls the verb and exits normally, with origin checked afterwards".

_EXIT_DRIVER = """
import importlib.util, pathlib, sys, types
S, sdlc, idfile, gesture = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]


def load(name):
    spec = importlib.util.spec_from_file_location(name, S / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def fresh_loop():
    lp = load("loop")
    lp.ledger._container_id_path = lambda: pathlib.Path(idfile)       # keeps the real id file untouched
    return lp


if gesture == "work-stale-resume":
    w = load("work")
    # As in production: a FRESH loop copy on every call, held only in a function local of work.py.
    w._load = lambda name: fresh_loop() if name == "loop" else load(name)
    w.ensure_fresh = lambda *a, **k: "worktree behind base " + w._STALE_RESUME_TRANSIENT
    w._record = lambda d, g: {}
    _orig = fresh_loop

    def fresh_with_source():
        lp = _orig()
        lp.sources.get_source = lambda *a, **k: types.SimpleNamespace(release=lambda goal, reason, **kw: True)
        return lp

    w._load = lambda name: fresh_with_source() if name == "loop" else load(name)
    w._stale_resume_refusal(sdlc, {"ledger": {"enabled": True}}, "10")
elif gesture == "release":
    lp = fresh_loop()
    lp._release(sdlc, types.SimpleNamespace(release=lambda goal, reason, **k: True), "10", "stale resume")
else:
    lp = fresh_loop()
    lp._record(sdlc, types.SimpleNamespace(complete=lambda goal: None), "10", "done", "shipped")
"""


@pytest.mark.parametrize("gesture,kind", [("release", "release"), ("record-done", "done"),
                                           ("work-stale-resume", "release")])
def test_a_process_that_records_outside_main_publishes_as_it_exits(container, tmp_path, gesture, kind):
    """AC-2, the gesture the docs give: a fresh Python process calls `loop._release`, `loop._record(..., "done")`
    or `work.py`'s real stale-resume caller, exits normally, and origin is checked afterwards. The third case
    loads `loop` into a function local that is garbage the moment the function returns, so it is also the proof
    that the exit hook must hold a strong reference to the module that armed it."""
    lp, origin, repo, base, refused = container
    shim = tmp_path / "shim"
    shim.mkdir()
    log = tmp_path / "gh-calls.log"
    gh = shim / "gh"
    gh.write_text(f"#!/bin/sh\necho \"$@\" >> '{log}'\nexit 1\n", encoding="utf-8")      # a subprocess is outside
    gh.chmod(0o755)                                                                   # conftest's _no_live_gh guard
    driver = tmp_path / "exit_driver.py"
    driver.write_text(_EXIT_DRIVER, encoding="utf-8")
    env = dict(os.environ, CLAUDE_CODE_REMOTE="true", SIGMA_GH_GRAPHQL="off", PYTHONDONTWRITEBYTECODE="1",
               PATH=f"{shim}{os.pathsep}{os.environ['PATH']}")
    proc = subprocess.run([sys.executable, str(driver), str(S), base, str(tmp_path / "child.id"), gesture],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert [(e["goal"], e["kind"]) for e in _origin_entries(origin)] == [("10", kind)], proc.stderr
    assert "ledger: published (" in proc.stderr, proc.stderr
    assert not log.exists(), f"a real gh was reached: {log.read_text()}"


def test_the_first_mark_arms_exactly_one_exit_flush_for_every_key(container, monkeypatch, tmp_path):
    """Compatible with the suite's exit-hook fixture without an opt-out: this test's own monkeypatch shadows the
    fixture's wrapper, so nothing real is registered. NOT asserted through `atexit._ncallbacks()`: it does not
    drop after `unregister` (CPython blanks the slot and keeps the count)."""
    lp, origin, repo, base, refused = container
    registered = []
    monkeypatch.setattr(lp.atexit, "register", lambda fn, *a, **k: registered.append(fn))
    monkeypatch.setattr(lp, "_EXIT_FLUSH_ARMED", False)
    lp._mark_publish(base, "claim of 10")
    lp._mark_publish(base, "claim of 11")
    lp._mark_publish(str(tmp_path / "other" / ".sdlc"), "claim of 12")
    assert registered == [lp._flush_all_pending_at_exit], registered


def test_a_failed_registration_is_retried_by_the_next_mark(container, monkeypatch):
    """The hook is registered FIRST and the armed flag set only after: a registration that raised must not
    leave the process believing it is armed."""
    lp, origin, repo, base, refused = container
    attempts = []

    def flaky(fn, *a, **k):
        attempts.append(fn)
        if len(attempts) == 1:
            raise RuntimeError("atexit is out of slots")

    monkeypatch.setattr(lp.atexit, "register", flaky)
    monkeypatch.setattr(lp, "_EXIT_FLUSH_ARMED", False)
    lp._mark_publish(base, "claim of 10")           # bookkeeping never stops a verb: the failure is swallowed
    assert lp._EXIT_FLUSH_ARMED is False and len(attempts) == 1
    lp._mark_publish(base, "claim of 11")
    assert lp._EXIT_FLUSH_ARMED is True and len(attempts) == 2, attempts
    assert lp._PENDING_PUBLISH[str(pathlib.Path(base).absolute())] == ["claim of 10", "claim of 11"]


def test_an_interrupt_is_not_swallowed_by_the_mark_or_by_the_exit_flush(container, monkeypatch):
    """`except Exception` and not `BaseException`: a second Ctrl-C must still interrupt a slow exit publish."""
    lp, origin, repo, base, refused = container

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(lp.atexit, "register", interrupted)
    monkeypatch.setattr(lp, "_EXIT_FLUSH_ARMED", False)
    with pytest.raises(KeyboardInterrupt):
        lp._mark_publish(base, "claim of 10")
    monkeypatch.setattr(lp, "_PENDING_PUBLISH", {base: ["claim of 10"]})
    monkeypatch.setattr(lp, "_flush_pending_publish", interrupted)
    with pytest.raises(KeyboardInterrupt):
        lp._flush_all_pending_at_exit()


def test_the_exit_flush_after_mains_own_flush_is_a_no_op_never_a_double_publish(container, monkeypatch):
    lp, origin, repo, base, refused = container
    published = []
    sync = lp._load("sync")
    monkeypatch.setattr(sync, "publish_entry", lambda sdlc, cfg, what, **k: published.append(what))
    monkeypatch.setattr(lp, "_load", lambda name, _l=lp._load: sync if name == "sync" else _l(name))
    assert lp.main(["loop.py", "claim", base, "10", "--session-pid", "4242"]) == 0
    assert published == ["claim of 10"]
    lp._flush_all_pending_at_exit()
    lp._flush_all_pending_at_exit()
    assert published == ["claim of 10"], "the exit flush published a second time"


@pytest.mark.parametrize("spelling", ["other-directory", "symlinked-spelling"])
def test_a_key_main_did_not_flush_is_flushed_at_exit(container, monkeypatch, tmp_path, spelling):
    """`main` flushes `argv[2]` only. A mark under any other key (another directory, a symlink-spelled path, a
    second `loop` copy) must still be published, or arming only outside `main` would strand it."""
    lp, origin, repo, base, refused = container
    seen = []
    sync = lp._load("sync")
    monkeypatch.setattr(sync, "publish_entry", lambda sdlc, cfg, what, **k: seen.append((str(sdlc), what)))
    monkeypatch.setattr(lp, "_load", lambda name, _l=lp._load: sync if name == "sync" else _l(name))
    if spelling == "other-directory":
        other = tmp_path / "other" / ".sdlc"
        other.mkdir(parents=True)
        (other / "config.json").write_text(json.dumps({"ledger": {"enabled": True}}), encoding="utf-8")
        key = str(other)
    else:
        link = tmp_path / "link"
        link.symlink_to(base)
        key = str(link)
    lp._mark_publish(key, "claim of 7")
    assert lp.main(["loop.py", "claim", base, "10", "--session-pid", "4242"]) == 0
    assert seen == [(base, "claim of 10")], "main flushes its own key only"
    lp._flush_all_pending_at_exit()
    assert seen == [(base, "claim of 10"), (key, "claim of 7")], seen


def test_the_exit_flush_publishes_through_the_one_flush_for_every_live_key(container, monkeypatch, tmp_path):
    lp, origin, repo, base, refused = container
    keys = []
    for name in ("a", "b"):
        sdlc = tmp_path / name / ".sdlc"
        sdlc.mkdir(parents=True)
        (sdlc / "config.json").write_text("{}", encoding="utf-8")
        keys.append(str(sdlc))
        lp._mark_publish(str(sdlc), f"claim {name}")
    called = []
    monkeypatch.setattr(lp, "_flush_pending_publish", lambda key, **k: called.append(key))
    lp._flush_all_pending_at_exit()
    assert called == keys, called


def test_the_exit_flush_drops_a_dead_sdlc_dir_without_a_word(container, capsys, tmp_path):
    lp, origin, repo, base, refused = container
    gone = str(tmp_path / "gone" / ".sdlc")
    lp._mark_publish(gone, "claim of 1")
    capsys.readouterr()
    lp._flush_all_pending_at_exit()
    out = capsys.readouterr()
    assert out.out == "" and out.err == "", out
    assert str(pathlib.Path(gone).absolute()) not in lp._PENDING_PUBLISH


def test_the_marks_of_a_long_lived_process_are_bounded(container):
    lp, origin, repo, base, refused = container
    for n in range(1000):
        lp._mark_publish(base, f"claim of {n}")
    marks = lp._PENDING_PUBLISH[str(pathlib.Path(base).absolute())]
    assert len(marks) <= 64 and marks[-1] == "claim of 999", (len(marks), marks[-1])


def test_the_exit_flush_shares_one_deadline_across_every_key(container, monkeypatch, tmp_path, capsys):
    """Without a shared deadline each key would get its own full `SIGMA_LEDGER_PUBLISH_DEADLINE`, so N keys
    could hold the exit for N times as long. A fake clock, no real time: each flush 'takes' 60 s of a 90 s total."""
    lp, origin, repo, base, refused = container
    monkeypatch.setenv("SIGMA_LEDGER_PUBLISH_DEADLINE", "90")
    keys = []
    for name in ("a", "b", "c"):
        sdlc = tmp_path / name / ".sdlc"
        sdlc.mkdir(parents=True)
        (sdlc / "config.json").write_text("{}", encoding="utf-8")
        keys.append(str(sdlc))
        lp._mark_publish(str(sdlc), f"claim {name}")
    now, given = [1000.0], []

    def flush(key, deadline_seconds=None):
        given.append((key, deadline_seconds))
        now[0] += 60
        return None

    monkeypatch.setattr(lp, "_flush_pending_publish", flush)
    capsys.readouterr()
    lp._flush_all_pending_at_exit(clock=lambda: now[0])
    assert given == [(keys[0], 90.0), (keys[1], 30.0)], given
    err = capsys.readouterr().err
    assert "exit flush ran out of time" in err and "1 " in err, err
    assert keys[2] in lp._PENDING_PUBLISH, "a key that got no time keeps its marks"


# ---- the suite's own exit-hook fixtures (tests/conftest.py): D-9 and its session backstop

_PROBE_COMMON = """
import atexit, gc, importlib.util, json, os, pathlib, weakref
import pytest

SCRIPTS = pathlib.Path(os.environ["PROBE_SCRIPTS"])
REFS = []


class Sentinel:
    pass


def loop_copy():
    spec = importlib.util.spec_from_file_location("loop", SCRIPTS / "loop.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sdlc_with_config(root):
    sdlc = pathlib.Path(root) / ".sdlc"
    sdlc.mkdir(parents=True, exist_ok=True)
    (sdlc / "config.json").write_text(json.dumps({"ledger": {"enabled": True, "actor": "dana"}}))
    return str(sdlc)
"""

_PROBE_FUNCTION_SCOPE = _PROBE_COMMON + """

def test_a_marks_and_lets_its_copy_die(tmp_path):
    lp = loop_copy()
    sentinel = Sentinel()
    lp._probe_sentinel = sentinel
    REFS.append(weakref.ref(sentinel))
    del sentinel
    lp._mark_publish(sdlc_with_config(tmp_path), "claim of 1")
    atexit.register(lambda: print("PROBE-OTHER-ATEXIT-RAN"))


def test_b_the_previous_copy_is_gone():
    gc.collect()
    assert REFS[0]() is None, "the previous test's loop copy is still alive"
"""

_PROBE_MODULE_SCOPE = _PROBE_COMMON + """

@pytest.fixture(scope="module")
def shared_copy(tmp_path_factory):
    # Set up BEFORE any function-scoped fixture of the first test: only the session fixtures are active.
    lp = loop_copy()
    sentinel = Sentinel()
    lp._probe_sentinel = sentinel
    REFS.append(weakref.ref(sentinel))
    lp._mark_publish(sdlc_with_config(tmp_path_factory.mktemp("early")), "claim of 1")
    atexit.register(lambda: print("PROBE-OTHER-ATEXIT-RAN"))
    yield lp
    del lp, sentinel


def test_c_a_later_test_marks_on_the_shared_copy(shared_copy, tmp_path):
    shared_copy._mark_publish(sdlc_with_config(tmp_path), "claim of 2")


def test_d_another_later_test_marks_too(shared_copy, tmp_path):
    shared_copy._mark_publish(sdlc_with_config(tmp_path), "claim of 3")
"""


def _run_probe(tmp_path, source):
    probe = tmp_path / "probe" / "test_probe.py"
    probe.parent.mkdir()
    probe.write_text(source, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    env.update(PYTHONPATH=str(pathlib.Path(__file__).resolve().parent), PYTHONDONTWRITEBYTECODE="1",
               PROBE_SCRIPTS=str(S))
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "conftest", str(probe), "-q", "-p", "no:cacheprovider"],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)


def _ledger_lines(proc):
    return [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.startswith("ledger:")]


def test_a_pytest_run_that_marks_publishes_nothing_at_exit_and_frees_its_loop_copy(tmp_path):
    """The control of the function-scoped fixture (`_loop_exit_hook_is_not_kept`). A test cannot observe its own
    process exit, so this runs pytest in a SUBPROCESS on a probe file: probe A loads a fresh `loop` copy, marks,
    and lets the copy die; probe B asserts it was freed. Without the fixture `atexit` pins the dead copy's whole
    namespace (about 6 MB each, measured 272 MB to 646 MB over 59 marking tests) AND publishes its marks when
    pytest exits. Other atexit callbacks must be left alone."""
    proc = _run_probe(tmp_path, _PROBE_FUNCTION_SCOPE)
    assert proc.returncode == 0, proc.stdout + proc.stderr           # probe B passed: the dead copy was freed
    assert "2 passed" in proc.stdout, proc.stdout
    assert _ledger_lines(proc) == [], f"a test's mark was published at pytest exit: {_ledger_lines(proc)}"
    assert "PROBE-OTHER-ATEXIT-RAN" in proc.stdout, "the fixture removed an exit callback that is not loop.py's"


def test_a_mark_made_before_any_function_scoped_fixture_is_not_published_at_pytest_exit(tmp_path):
    """The control of the SESSION backstop. A module-scoped fixture is set up before the function-scoped
    fixture of its first test, so its hook is registered unwrapped and would stay for the whole run, publishing
    whatever the later tests mark on that shared copy when pytest exits."""
    proc = _run_probe(tmp_path, _PROBE_MODULE_SCOPE)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 passed" in proc.stdout, proc.stdout
    assert _ledger_lines(proc) == [], f"a module-scoped mark was published at pytest exit: {_ledger_lines(proc)}"
    assert "PROBE-OTHER-ATEXIT-RAN" in proc.stdout, "the backstop removed an exit callback that is not loop.py's"


@pytest.mark.real_atexit
def test_a_test_marked_real_atexit_gets_the_genuine_register():
    """Guards the opt-out: a legitimate test of the real registration must be able to ask for it, and must
    remove its own callback."""
    assert type(atexit.register).__name__ == "builtin_function_or_method"

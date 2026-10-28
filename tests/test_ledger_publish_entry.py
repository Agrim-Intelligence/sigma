# SPDX-License-Identifier: MIT
"""#2076: a run in a fresh, short-lived container leaves the team-ledger record a laptop run does.

THE DEFECT. The claim, the keep-alive and the outcome were written to `.sdlc/ledger/entries/` and left
there: publishing was the watcher daemon's job, and a fresh container has neither the `.sdlc/ledger`
worktree nor a watcher. `sync.publish_entry` creates the worktree from origin through `sync.init`, writes
nothing into the code checkout, publishes, and is bounded in time and fail-open: a push that is refused or
times out is PRINTED as not published and the run goes on.

Real git throughout: a bare origin and real clones. A stub would only confirm the shape this file assumes.
Fully offline (the origin is a local directory), no network, no GraphQL.
"""
import errno
import fcntl
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
import types

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sync = _mod("sync")
ledger = _mod("ledger")


def _git(cwd, *args, check=True):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check:
        assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr or proc.stdout}"
    return proc.stdout.strip()


@pytest.fixture
def origin(tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "T")
    _git(seed, "config", "commit.gpgsign", "false")
    (seed / ".gitignore").write_text(".sdlc/\n", encoding="utf-8")
    (seed / "code.py").write_text("print('hi')\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-q", "origin", "main")
    return bare


def _clone(tmp_path, origin, name, **ledger_cfg):
    """A fresh container: a plain clone of origin, no `.sdlc/ledger` worktree, no watcher."""
    repo = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    cfg = {"ledger": dict({"enabled": True, "actor": "dana", "host_alias": name}, **ledger_cfg)}
    base = repo / ".sdlc"
    (base / "state").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return repo, base, cfg


def _origin_text(origin):
    """Every ledger entries line on origin's ops branch, as parsed dicts."""
    names = subprocess.run(["git", "--git-dir", str(origin), "ls-tree", "-r", "--name-only", sync.DEFAULT_BRANCH],
                           capture_output=True, text=True)
    if names.returncode != 0:
        return []
    out = []
    for name in names.stdout.split():
        if name.startswith("entries/") and name.endswith(".jsonl"):
            body = subprocess.run(["git", "--git-dir", str(origin), "show", f"{sync.DEFAULT_BRANCH}:{name}"],
                                  capture_output=True, text=True).stdout
            out += [json.loads(line) for line in body.splitlines() if line.strip()]
    return out


def _snapshot(repo):
    return (_git(repo, "rev-parse", "HEAD"), _git(repo, "branch", "--show-current"),
            _git(repo, "status", "--porcelain"), _git(repo, "diff", "--cached", "--name-only"))


# ------------------------------------------------------------------ AC-1, AC-2: the claim in a fresh container


def test_a_fresh_container_publishes_its_claim_and_leaves_the_code_checkout_alone(tmp_path, origin):
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    before = _snapshot(repo)
    assert not sync.is_worktree(base)
    ledger.append(base, cfg, "claimed", "2076")
    said = sync.publish_entry(base, cfg, "claim of #2076")
    assert sync.is_worktree(base), said
    assert said and "NOT published" not in said, said
    assert [e["kind"] for e in _origin_text(origin)] == ["claimed"]
    assert _snapshot(repo) == before, "the code checkout moved"


def test_the_claim_is_visible_to_a_second_clone_of_the_same_login(tmp_path, origin):
    repo_a, base_a, cfg_a = _clone(tmp_path, origin, "boxa")
    ledger.append(base_a, cfg_a, "claimed", "2076")
    sync.publish_entry(base_a, cfg_a, "claim")
    repo_b, base_b, cfg_b = _clone(tmp_path, origin, "boxb")
    sync.publish_entry(base_b, cfg_b, "probe")       # joins: creates the worktree from origin
    assert [e["goal"] for e in ledger.read_all(base_b)] == ["2076"]


def test_a_repo_that_does_not_ignore_the_ledger_still_shows_a_clean_status(tmp_path, origin):
    """The worktree directory must not show as untracked in the code checkout."""
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    (repo / ".gitignore").write_text(".sdlc/state/\n.sdlc/config.json\n", encoding="utf-8")     # NOT .sdlc/ledger
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "stop ignoring")
    (base / "state").mkdir(exist_ok=True)
    before = _git(repo, "status", "--porcelain")
    ledger.append(base, cfg, "claimed", "2076")
    sync.publish_entry(base, cfg, "claim")
    assert _git(repo, "status", "--porcelain") == before


def test_a_refused_push_is_printed_as_not_published_and_does_not_raise(tmp_path, origin):
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'refused by policy' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    ledger.append(base, cfg, "claimed", "2076")
    said = sync.publish_entry(base, cfg, "claim")
    assert said.startswith("ledger: NOT published"), said
    assert "claim" in said
    assert ledger.read_all(base), "the entry must stay on disk"


def test_an_unreachable_origin_is_not_published_not_a_crash(tmp_path, origin):
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    ledger.append(base, cfg, "claimed", "2076")
    said = sync.publish_entry(base, cfg, "claim")
    assert said.startswith("ledger: NOT published"), said


def test_the_step_is_bounded_in_time(tmp_path, origin):
    """Each git call takes 0.3 s and succeeds, so only the shared wall-time limit can stop the step."""
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    ledger.append(base, cfg, "claimed", "2076")
    calls = []

    def slow(cwd, args):
        calls.append(args[0])
        time.sleep(0.3)
        if len(calls) > 20:
            raise RuntimeError("slow remote")
        return ""

    start = time.monotonic()
    said = sync.publish_entry(base, cfg, "claim", run=slow, deadline_seconds=0.5)
    assert time.monotonic() - start < 3, calls
    assert said.startswith("ledger: NOT published") and "wall-time limit" in said, said
    assert len(calls) <= 4, "the step kept calling git after its limit: %s" % calls


def test_a_live_watcher_keeps_its_tick(tmp_path, origin):
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    sync.bootstrap(base, cfg)
    ledger.append(base, cfg, "claimed", "2076")
    (base / "state" / "watch.heartbeat").write_text("", encoding="utf-8")
    calls = []
    assert sync.publish_entry(base, cfg, "claim", publish_fn=lambda *a, **k: calls.append(a)) is None
    assert calls == []


def test_publish_on_write_false_and_a_disabled_ledger_publish_nothing(tmp_path, origin):
    repo, base, cfg = _clone(tmp_path, origin, "boxa", publish_on_write=False)
    ledger.append(base, cfg, "claimed", "2076")
    assert sync.publish_entry(base, cfg, "claim") is None
    assert not sync.is_worktree(base)
    off = {"ledger": {"enabled": False}}
    assert sync.publish_entry(base, off, "claim") is None


# ------------------------------------------------------------------ AC-4: several clones at once


def _race(tmp_path, origin, count, rounds=1):
    clones = [_clone(tmp_path, origin, f"c{i}") for i in range(count)]
    seed_repo, seed_base, seed_cfg = _clone(tmp_path, origin, "seed0")
    ledger.append(seed_base, seed_cfg, "claimed", "seed")
    assert "NOT published" not in (sync.publish_entry(seed_base, seed_cfg, "seed") or "")
    for repo, base, cfg in clones:                      # every container joins the existing branch
        sync.init(base, cfg)
    results, errors = {}, []
    barrier = threading.Barrier(count)

    def work(i):
        repo, base, cfg = clones[i]
        try:
            for r in range(rounds):
                ledger.append(base, cfg, "claimed", f"g{i}-{r}")
                barrier.wait(timeout=60)
                results[(i, r)] = sync.publish_entry(base, cfg, f"claim {i}")
        except Exception as exc:                        # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(count)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    return results, errors, time.monotonic() - start


def test_every_clone_under_one_login_lands_every_entry_it_wrote(tmp_path, origin):
    """AC-4, real git, SMOKE (probabilistic): 16 clones push at once, three rounds. Measured on the
    tree before this change (five immediate attempts, no pause): 25 to 27 of 48 entries missing from
    origin; 8 clones by 2 rounds lost 4 to 7 of 16. The deterministic control at the retry seam is
    `test_the_retry_pauses_with_jitter_and_stops_at_the_deadline`; this test is its real-git smoke."""
    count, rounds = 16, 3
    results, errors, took = _race(tmp_path, origin, count, rounds=rounds)
    assert not errors, errors
    wrote = {f"g{i}-{r}" for i in range(count) for r in range(rounds)}
    landed = {e["goal"] for e in _origin_text(origin)}
    assert wrote <= landed, f"missing on origin: {sorted(wrote - landed)}"
    assert took < 120, "the retries must be bounded in time"


def test_the_old_retry_loses_entries_under_the_same_race(tmp_path, origin, monkeypatch):
    """SENSITIVITY CONTROL for the smoke test above: with the pause switched off and five attempts, the
    very same race DOES lose entries. A race test that cannot fail against the old behaviour proves nothing."""
    real = sync.publish_entry
    monkeypatch.setattr(sync, "publish_entry", lambda base, cfg, what, **k: sync.publish(base, cfg, backoff=False))
    results, errors, _ = _race(tmp_path, origin, 16, rounds=3)
    monkeypatch.setattr(sync, "publish_entry", real)
    wrote = {f"g{i}-{r}" for i in range(16) for r in range(3)}
    landed = {e["goal"] for e in _origin_text(origin)}
    assert wrote - landed, "the old retry lost nothing: this race has no teeth on this machine"


# ------------------------------------------------------------------ the retry seam, deterministic


def test_the_retry_pauses_with_jitter_and_stops_at_the_deadline(tmp_path):
    """The seam: a push rejected k times, then accepted. No real git, no real clock."""
    pauses, calls = [], {"push": 0}

    def git(cwd, args):
        if args[0] == "push":
            calls["push"] += 1
            if calls["push"] <= 4:
                raise RuntimeError("[rejected] (fetch first)")
        if args[0] == "rev-parse":
            return "a" * 40
        return ""

    def sleep(attempt):
        pauses.append(attempt)

    out = sync._push_with_retry(git, tmp_path, "origin", sync.DEFAULT_BRANCH, 12, sleep=sleep)
    assert out == "published" and calls["push"] == 5 and pauses == [0, 1, 2, 3]
    calls["push"] = -100                                    # now it never succeeds
    pauses.clear()
    out = sync._push_with_retry(git, tmp_path, "origin", sync.DEFAULT_BRANCH, 6, sleep=sleep)
    assert out.startswith("publish deferred") and len(pauses) == 5, "bounded in count"


def test_the_pause_is_full_jitter_and_capped():
    seen = []
    for rng in (0.0, 0.5, 0.999):
        seen.append(sync._jitter_sleep(10, rng=lambda r=rng: r, sleep=lambda p: None))
    assert seen[0] == 0.0 and 0 < seen[1] < seen[2] <= 3.0, seen
    assert sync._jitter_sleep(0, rng=lambda: 1.0, sleep=lambda p: None) == 0.15


def test_the_pause_is_capped_by_the_time_left_to_the_deadline():
    """#2081 gap 4: the pause must never carry the step past its deadline. At attempt 10 the uncapped
    full-jitter pause is 3.0 s; with 0.5 s left it is 0.5 s."""
    slept = []
    got = sync._jitter_sleep(10, rng=lambda: 1.0, sleep=slept.append, deadline_at=100.5, clock=lambda: 100.0)
    assert got == 0.5 and slept == [0.5], (got, slept)


def test_a_pause_past_the_deadline_is_zero_not_negative():
    """A negative value would make `time.sleep` raise inside `_push_with_retry`'s `except` block and escape
    `publish`, which promises a status string."""
    slept = []
    got = sync._jitter_sleep(10, rng=lambda: 1.0, sleep=slept.append, deadline_at=100.5, clock=lambda: 101.0)
    assert got == 0.0 and slept == [0.0], (got, slept)


def test_bounded_git_exposes_its_deadline():
    """`publish` reads the runner's deadline to cap its pauses; a runner that does not carry one is unbounded."""
    assert sync._bounded_git(123.0, base=lambda cwd, args: "").deadline_at == 123.0


def test_a_stub_driven_publish_never_sleeps(tmp_path):
    """Every existing stub-driven test passes a runner; none of them may start pausing."""
    assert sync._push_with_retry(lambda c, a: (_ for _ in ()).throw(RuntimeError("no")), tmp_path,
                                 "origin", "b", 3) .startswith("publish deferred")


def test_a_publish_with_an_injected_runner_pauses_only_when_asked(tmp_path, monkeypatch):
    """The `publish` side of 'a stub-driven publish never sleeps' (#2081 gap 4 rewired that line): a runner
    passed in, with `backoff` unset, must not reach `_jitter_sleep`; `backoff=True` must, and must hand it the
    runner's deadline. (`test_a_stub_driven_publish_never_sleeps` above only drives `_push_with_retry`, so it
    cannot see this wiring.)"""
    base = tmp_path / ".sdlc"
    wt = sync.worktree(base)
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
    cfg = {"ledger": {"enabled": True, "actor": "dana", "host_alias": "boxa"}}
    ledger.append(base, cfg, "claimed", "1")
    paused = []
    monkeypatch.setattr(sync, "_jitter_sleep", lambda attempt, **kw: paused.append(kw))

    def git(cwd, args):
        if args[0] == "push":
            raise RuntimeError("[rejected] (fetch first)")
        return "entries/x" if args[:2] == ["diff", "--cached"] else ("a" * 40 if args[0] == "rev-parse" else "")

    assert sync.publish(base, cfg, run=git, attempts=3).startswith("publish deferred")
    assert paused == [], "an injected runner paused without being asked to"
    bounded = sync._bounded_git(time.monotonic() + 60, git)
    sync.publish(base, cfg, run=bounded, attempts=3, backoff=True)
    assert [kw.get("deadline_at") for kw in paused] and all(kw["deadline_at"] == bounded.deadline_at for kw in paused), paused


def test_a_credential_in_a_remote_url_is_never_printed(tmp_path, origin):
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    ledger.append(base, cfg, "claimed", "2076")

    def run(cwd, args):
        raise RuntimeError(f"fatal: unable to access '{url}'")

    userinfo = "x-access-token" + ":" + "not-a-real-secret-" + "9f8e7d"
    url = "https://" + userinfo + "@example.com/o/r.git/"
    said = sync.publish_entry(base, cfg, "claim", run=run)
    assert "NOT published" in said and "not-a-real-secret" not in said and "***@" in said, said


def test_a_worktree_left_mid_rebase_by_a_killed_git_is_cleared_before_publishing(tmp_path, origin):
    """A git ended at the step's limit can leave the ledger worktree mid-rebase, which would defer every
    later publish. The next step aborts it first."""
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    ledger.append(base, cfg, "claimed", "1")
    assert "NOT published" not in (sync.publish_entry(base, cfg, "first") or "")
    wt = sync.worktree(base)
    other, obase, ocfg = _clone(tmp_path, origin, "boxb")
    sync.init(obase, ocfg)
    owt = sync.worktree(obase)
    (owt / "README.md").write_text("their change\n", encoding="utf-8")
    _git(owt, "add", "-A")
    _git(owt, "commit", "-q", "-m", "theirs")
    _git(owt, "push", "-q", "origin", f"HEAD:{sync.DEFAULT_BRANCH}")
    (wt / "README.md").write_text("our change\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ours")
    _git(wt, "fetch", "-q", "origin", sync.DEFAULT_BRANCH)
    assert _git(wt, "rebase", "origin/sdlc-ledger", check=False) is not None
    assert "rebase in progress" in _git(wt, "status", check=False) or (
        pathlib.Path(_git(wt, "rev-parse", "--git-path", "rebase-merge")).exists()), "precondition: mid-rebase"
    ledger.append(base, cfg, "claimed", "2")
    sync.publish_entry(base, cfg, "second")
    assert "rebase in progress" not in _git(wt, "status", check=False)
    assert not pathlib.Path(wt / _git(wt, "rev-parse", "--git-path", "rebase-merge")).exists()


# ------------------------------------------------------------------ a self-created worktree starts no daemon


def test_a_worktree_the_loop_created_for_itself_does_not_start_the_watcher(tmp_path, origin, monkeypatch):
    """The loop's own first publish creates the worktree; that must not enrol the machine in a recurring
    background job. Only an explicit `sync.py bootstrap` (what /sigma-ledger runs) does."""
    loop = _mod("loop")
    monkeypatch.setattr(loop, "_load", lambda name, _l=loop._load: sync if name == "sync" else _l(name))
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    ledger.append(base, cfg, "claimed", "2076")
    assert "NOT published" not in (sync.publish_entry(base, cfg, "claim") or "")
    assert sync.is_worktree(base) and sync.self_created(base)
    spawned = []
    loop._ensure_watcher(str(base), cfg, spawn=lambda: spawned.append(1))
    assert spawned == [], "a worktree the loop made for itself must not start a daemon"
    sync.bootstrap(base, cfg)
    assert not sync.self_created(base), "an explicit bootstrap is the enrolment"
    loop._ensure_watcher(str(base), cfg, spawn=lambda: spawned.append(1))
    assert spawned == [1]


def test_a_worktree_made_by_bootstrap_still_gets_its_watcher(tmp_path, origin, monkeypatch):
    loop = _mod("loop")
    monkeypatch.setattr(loop, "_load", lambda name, _l=loop._load: sync if name == "sync" else _l(name))
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    sync.bootstrap(base, cfg)
    spawned = []
    loop._ensure_watcher(str(base), cfg, spawn=lambda: spawned.append(1))
    assert spawned == [1]


# ------------------------------------------------------------------ #2081 gap 4: the pause obeys the deadline

#: How far past its deadline the step may end in the wall-clock test below (git's own latency on a loaded host).
#: The pause forced there is 10 s, so the overshoot it can show is about 8 s on code that sleeps it in full.
_PAUSE_MARGIN = 1.0


def test_publish_entry_does_not_outlast_its_deadline_by_a_pause(tmp_path, origin, monkeypatch):
    """REAL git, WALL CLOCK (the discriminating test for gap 4; the deterministic seams are the three tests
    above). Every push is refused, so `_push_with_retry` pauses between attempts, and every pause is forced to
    10 s. Before #2081 the pause was slept in full, so a 2 s step ended about 8 s late; now it is cut to the
    time left. The forcing wraps `sync._jitter_sleep` (looked up by `publish` when it runs), so the test runs
    on code from before and after the fix. The worktree is created BEFORE the timed step: measured, creating
    it takes about 1.9 s of a 2 s budget on a loaded host, which left no room for the first pause to start."""
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    ledger.append(base, cfg, "claimed", "2080")
    assert "NOT published" not in (sync.publish_entry(base, cfg, "seed") or ""), "setup: the worktree"
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'refused by policy' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    ledger.append(base, cfg, "claimed", "2081")
    real = sync._jitter_sleep
    monkeypatch.setattr(sync, "_jitter_sleep",
                        lambda attempt, **kw: real(attempt, base=10.0, cap=10.0, rng=lambda: 1.0, **kw))
    limit = 2.0
    start = time.monotonic()
    said = sync.publish_entry(base, cfg, "claim", deadline_seconds=limit)
    elapsed = time.monotonic() - start
    assert said.startswith("ledger: NOT published"), said
    assert elapsed < limit + _PAUSE_MARGIN, f"the step ended {elapsed - limit:.2f}s after its {limit}s deadline"


# ------------------------------------------------------------------ #2081 gap 1: one publisher at a time
#
# THE DEFECT. Every path that rebases, resets, aborts or creates the ledger worktree ran with no mutual
# exclusion between PROCESSES of one checkout. `publish_entry` aborts "a stale rebase" first, so a second
# publisher aborted the first one's LIVE rebase: the first printed `NOT published` for an entry that then
# landed anyway (or did not). One kernel `flock` file, `.sdlc/state/ledger-sync.lock`, now serialises them.
#
# THE GESTURE THE DOCS GIVE: the four-file command in docs/cloud-sessions.md, flagless.
#
# The lock path and `fcntl.flock` are written out LITERALLY below, not taken from a `sync` helper: that is the
# documented contract (docs/launch/b6-claims-sessions-locks.md), and it makes the control fail on code without
# the lock BEHAVIOURALLY (a lock nobody honours), not with an AttributeError.

_HOLD_PLAIN = """
import fcntl, os, signal, sys
signal.alarm(180)                       # never outlive a crashed test
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
sys.stdin.readline()                    # the parent decides when the lock is released (or kills this process)
"""

_HOLD_VIA_HELPER = """
import importlib.util, signal, sys
signal.alarm(180)
spec = importlib.util.spec_from_file_location("sync", sys.argv[1])
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
lock = sync._ledger_lock(sys.argv[2], verb="publish")
assert lock not in (None, True), lock
print("held", flush=True)
sys.stdin.readline()
"""


def _lock_file(base):
    return base / "state" / "ledger-sync.lock"


class _Holder:
    """A child process that holds the ledger lock until told to let go (or killed)."""

    def __init__(self, base, code=_HOLD_PLAIN, via_helper=False):
        _lock_file(base).parent.mkdir(parents=True, exist_ok=True)
        args = [str(S / "sync.py"), str(base)] if via_helper else [str(_lock_file(base))]
        self.proc = subprocess.Popen([sys.executable, "-c", code, *args], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
        assert self.proc.stdout.readline().strip() == "held"
        self.pid = self.proc.pid

    def release(self):
        try:
            self.proc.stdin.write("\n")
            self.proc.stdin.flush()
        except OSError:
            pass
        self.proc.wait(timeout=60)

    def kill(self):
        self.proc.kill()
        self.proc.wait(timeout=60)

    def close(self):
        if self.proc.poll() is None:
            self.kill()


@pytest.fixture
def holders():
    made = []

    def make(base, **kw):
        made.append(_Holder(base, **kw))
        return made[-1]

    yield make
    for h in made:
        h.close()


def _rebase_dir(wt):
    return pathlib.Path(wt / _git(wt, "rev-parse", "--git-path", "rebase-merge"))


def _stage_live_rebase(tmp_path, origin, base):
    """Leave the ledger worktree mid-rebase, deterministically and with no conflict: a local commit, a
    competing commit on origin from another clone, then `rebase -x "exit 1"` (stops after replaying)."""
    wt = sync.worktree(base)
    _, obase, ocfg = _clone(tmp_path, origin, "boxrival")
    sync.init(obase, ocfg)
    owt = sync.worktree(obase)
    (owt / "theirs.txt").write_text("their change\n", encoding="utf-8")
    _git(owt, "add", "-A")
    _git(owt, "commit", "-q", "-m", "theirs")
    _git(owt, "push", "-q", "origin", f"HEAD:{sync.DEFAULT_BRANCH}")
    (wt / "ours.txt").write_text("our change\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ours")
    _git(wt, "fetch", "-q", "origin", sync.DEFAULT_BRANCH)
    _git(wt, "rebase", "-x", "exit 1", f"origin/{sync.DEFAULT_BRANCH}", check=False)
    assert _rebase_dir(wt).exists(), "precondition: a live rebase"
    return wt


_APPEND_FROM_ANOTHER_PROCESS = """
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("ledger", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.append(sys.argv[2], json.load(open(os.path.join(sys.argv[2], "config.json"))), "claimed", sys.argv[3])
"""


def _append_from_another_process(base, goal):
    """An entry written by a DIFFERENT process: its own pid file, new and untracked, which is what a second
    publisher's entry is (each process appends once, then publishes). Appending twice from ONE process would
    dirty a tracked file instead, and `git rebase --abort` discards uncommitted changes to tracked files
    (measured with git 2.46.2): see docs/cloud-sessions.md, residual (a)."""
    subprocess.run([sys.executable, "-c", _APPEND_FROM_ANOTHER_PROCESS, str(S / "ledger.py"), str(base), goal],
                   check=True, capture_output=True, timeout=120)


def _fresh_worktree(tmp_path, origin, name="boxa"):
    repo, base, cfg = _clone(tmp_path, origin, name)
    ledger.append(base, cfg, "claimed", "seed")
    assert "NOT published" not in (sync.publish_entry(base, cfg, "seed") or ""), "setup: the first publish"
    return repo, base, cfg


def _fake_worktree(tmp_path):
    """A directory `is_worktree` accepts, for the stub-driven tests: no git is ever run in it."""
    base = tmp_path / ".sdlc"
    wt = sync.worktree(base)
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
    cfg = {"ledger": {"enabled": True, "actor": "dana", "host_alias": "boxa"}}
    return base, cfg


class _NoGit:
    def __init__(self):
        self.calls = []

    def __call__(self, cwd, args):
        self.calls.append(list(args))
        return ""


def test_a_live_publishers_rebase_is_left_alone_and_a_dead_ones_is_cleared(tmp_path, origin, holders):
    """THE DETERMINISTIC CONTROL AT THE EXACT SEAM (AC-1). A process holds the lock with the worktree
    mid-rebase: the second publisher must not abort that rebase, and says who holds the lock. The holder is then
    SIGKILLed (the kernel releases its lock): the next publisher clears the rebase it left and the entries land.

    WHAT THIS PROVES AND WHAT IT DOES NOT. The holder is a plain flock-holding child with no git of its own, so
    this proves the lock ORDER (lock before abort) and the kernel release. It cannot show a SIGKILLed holder whose
    own `git` (started with `start_new_session=True`) outlives it; and each process appends ONCE before it
    publishes, so "both entries reach origin" is proved for that single-append shape only, not for a second
    append from one process (which dirties a tracked file: the stale-rebase abort then discards it, measured;
    docs/cloud-sessions.md, residuals (a) and (f))."""
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    wt = _stage_live_rebase(tmp_path, origin, base)
    holder = holders(base)
    _append_from_another_process(base, "1")
    live = (_git(wt, "rev-parse", "HEAD"), sorted(p.name for p in _rebase_dir(wt).iterdir()))
    start = time.monotonic()
    said = sync.publish_entry(base, cfg, "second", deadline_seconds=2)
    assert _rebase_dir(wt).exists(), "the live holder's rebase was aborted"
    assert (_git(wt, "rev-parse", "HEAD"), sorted(p.name for p in _rebase_dir(wt).iterdir())) == live, \
        "the live holder's rebase was touched (its HEAD or its state directory changed)"
    assert said and said.startswith("ledger: NOT published") and "another publisher holds the ledger worktree" in said, said
    assert time.monotonic() - start < 5
    holder.kill()
    _append_from_another_process(base, "2")
    said = sync.publish_entry(base, cfg, "third", deadline_seconds=60)
    assert said and said.startswith("ledger: published"), said
    assert not _rebase_dir(wt).exists(), "the dead holder's rebase was not cleared"
    assert {"1", "2"} <= {e["goal"] for e in _origin_text(origin)}


def test_a_publisher_waits_for_the_holder_and_then_publishes(tmp_path, origin, holders):
    """WAIT VERSUS SKIP, deterministic (no wall-clock threshold). The parent runs `publish_entry` in a thread
    with a runner that records its first git call; the child holds the lock until the parent lets go. While the
    lock is held the runner must see NO call and the entry must not be on origin; after the release the entry
    lands. Without the lock the first git call happens within milliseconds of the thread starting."""
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    holder = holders(base)
    ledger.append(base, cfg, "claimed", "waiting")
    began, out = threading.Event(), {}

    def run(cwd, args):
        began.set()
        return sync._run_git(cwd, args, timeout=60)

    t = threading.Thread(target=lambda: out.update(said=sync.publish_entry(base, cfg, "wait", run=run,
                                                                           deadline_seconds=120)))
    t.start()
    try:
        assert not began.wait(2.0), "the publisher ran git while another process held the lock"
        assert "waiting" not in {e["goal"] for e in _origin_text(origin)}
        assert t.is_alive(), "the publisher gave up instead of waiting"
    finally:
        holder.release()
        t.join(timeout=120)
    assert not t.is_alive()
    assert out["said"] and out["said"].startswith("ledger: published"), out
    assert "waiting" in {e["goal"] for e in _origin_text(origin)}


def test_the_wait_ends_at_the_step_deadline_and_names_the_holder(tmp_path, origin, holders):
    """LIVENESS: a wedged holder is distinguishable from an idle one by the age the waiter prints."""
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    holder = holders(base, code=_HOLD_VIA_HELPER, via_helper=True)
    ledger.append(base, cfg, "claimed", "x")
    start = time.monotonic()
    said = sync.publish_entry(base, cfg, "claim", deadline_seconds=1.5)
    assert time.monotonic() - start < 4
    assert said.startswith("ledger: NOT published") and f"pid {holder.pid}" in said and "held " in said, said


def test_a_holder_older_than_five_minutes_is_reported_as_wedged_inside_the_printed_cap(tmp_path, origin, holders):
    """The waiter's line goes through `_scrub`, which cuts at 240 characters: the long lever lives in the docs
    and the printed text stays short enough to survive the cap."""
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    holder = holders(base, code=_HOLD_VIA_HELPER, via_helper=True)
    _lock_file(base).write_text(f"pid={holder.pid} verb=publish since={int(time.time()) - 4000}\n", encoding="utf-8")
    ledger.append(base, cfg, "claimed", "x")
    said = sync.publish_entry(base, cfg, "claim", deadline_seconds=1)
    assert f"pid {holder.pid}" in said and "held 400" in said, said                   # 4000 s, plus a second or so
    assert "wedged" in said and "docs/cloud-sessions.md" in said, said
    assert "..." not in said, "the waiter's text was cut off by the 240-character cap"


def test_publish_defers_instead_of_waiting_while_another_publisher_holds_the_lock(tmp_path):
    base, cfg = _fake_worktree(tmp_path)
    ledger.append(base, cfg, "claimed", "1")
    git = _NoGit()
    held = sync._ledger_lock(base)
    start = time.monotonic()
    out = sync.publish(base, cfg, run=git)
    assert time.monotonic() - start < 1
    assert out.startswith("publish deferred") and "holds the worktree lock" in out, out
    assert git.calls == [], "git ran while another publisher held the lock"
    sync._release_ledger_lock(held)
    out = sync.publish(base, cfg, run=git)
    assert "holds the worktree lock" not in out and git.calls, (out, git.calls)


def test_pull_defers_instead_of_waiting_while_another_publisher_holds_the_lock(tmp_path):
    base, cfg = _fake_worktree(tmp_path)
    git = _NoGit()
    held = sync._ledger_lock(base)
    out = sync.pull(base, cfg, run=git)
    assert out.startswith("pull deferred") and "holds the worktree lock" in out, out
    assert git.calls == []
    sync._release_ledger_lock(held)
    sync.pull(base, cfg, run=git)
    assert git.calls, "with the lock free, pull must run"


def test_publish_receipt_defers_while_another_publisher_holds_the_lock(tmp_path):
    base, cfg = _fake_worktree(tmp_path)
    relative = "receipts/v1/" + "a" * 64 + "/parent.json"
    target = sync.worktree(base) / relative
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    git = _NoGit()
    held = sync._ledger_lock(base)
    out = sync.publish_receipt(base, relative, config=cfg, run=git)
    assert out.startswith("receipt pending:") and "holds the worktree lock" in out, out
    assert git.calls == []
    sync._release_ledger_lock(held)
    sync.publish_receipt(base, relative, config=cfg, run=git)
    assert git.calls, "with the lock free, the receipt path must run"


def test_a_reroot_defers_while_another_publisher_holds_the_lock(tmp_path, monkeypatch):
    base, cfg = _fake_worktree(tmp_path)
    ran = []
    monkeypatch.setattr(sync, "_reroot", lambda *a, **k: ran.append(1) or "")
    ch = sync._channel(sync.LEDGER, base, cfg)
    held = sync._ledger_lock(base)
    out = sync._reroot_guarded(_NoGit(), base, ch, "dana")
    assert out.startswith("; re-root deferred") and ran == [], (out, ran)
    sync._release_ledger_lock(held)
    assert sync._reroot_guarded(_NoGit(), base, ch, "dana") == "" and ran == [1]


def test_publish_entry_takes_the_lock_before_it_creates_the_worktree(tmp_path, origin):
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    ledger.append(base, cfg, "claimed", "1")
    held = sync._ledger_lock(base)
    assert held not in (None, True)
    said = sync.publish_entry(base, cfg, "claim", deadline_seconds=1)
    assert said.startswith("ledger: NOT published"), said
    assert not sync.is_worktree(base), "the worktree was created while another publisher held the lock"
    sync._release_ledger_lock(held)


def test_publish_entry_re_roots_under_its_own_lock_not_a_second_one(tmp_path, origin, monkeypatch):
    """A nested `_reroot_guarded` would be refused by the caller's own lock and SKIP the re-root silently (the
    result is ignored), so the failure would show in no status: pin it by the call itself."""
    def refused(*a, **k):
        raise AssertionError("publish_entry must call the unguarded _reroot: it already holds the lock")

    called, real = [], sync._reroot
    monkeypatch.setattr(sync, "_reroot_guarded", refused)
    monkeypatch.setattr(sync, "_reroot", lambda *a, **k: (called.append(1), real(*a, **k))[1])
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    ledger.append(base, cfg, "claimed", "1")
    said = sync.publish_entry(base, cfg, "claim")
    assert called, "the first publish never re-rooted"
    assert "AssertionError" not in (said or ""), said


def test_a_lock_file_that_cannot_be_opened_defers_instead_of_raising(tmp_path):
    """`.sdlc/state` cannot be created (here: it is a regular file). The verb defers with the reason; it never
    raises into a watcher tick, and `publish_entry` (below) prints it as NOT published."""
    base, cfg = _fake_worktree(tmp_path)
    (base / "state").write_text("not a directory", encoding="utf-8")
    git = _NoGit()
    out = sync.publish(base, cfg, run=git)
    assert out.startswith("publish deferred") and "cannot take the lock" in out, out
    assert git.calls == []
    said = sync.publish_entry(base, cfg, "claim", deadline_seconds=2)
    assert said.startswith("ledger: NOT published"), said


def test_a_second_acquire_in_one_process_is_refused_not_hung(tmp_path):
    """Pins the per-open-file-description semantics the whole design relies on."""
    base, cfg = _fake_worktree(tmp_path)
    first = sync._ledger_lock(base)
    start = time.monotonic()
    assert sync._ledger_lock(base) is None
    assert sync.publish(base, cfg, run=_NoGit()).startswith("publish deferred")
    assert time.monotonic() - start < 2, "a nested acquire hung"
    sync._release_ledger_lock(first)
    again = sync._ledger_lock(base)
    assert again not in (None, True)
    sync._release_ledger_lock(again)


# ---- F1 (review cycle 1): a lock that cannot be made REFUSES by default; the operator can opt in to proceeding
#
# AGENTS.md SAFETY: where a guarantee cannot be made on a platform, the code REFUSES loudly rather than proceeding
# weakly. No `fcntl` (Windows) or a filesystem whose `flock` is unsupported (any errno other than
# EWOULDBLOCK/EAGAIN) therefore raises `LedgerLockRefused` from `_ledger_lock`, every locked verb DEFERS with a string
# that names the lever, and `SIGMA_LEDGER_ALLOW_UNSERIALISED=1` (also `true`, `yes`) restores proceeding unserialised.

ALLOW_ENV = "SIGMA_LEDGER_ALLOW_UNSERIALISED"
LEVER = ALLOW_ENV + "=1"
NO_LOCK_MODES = ["no-fcntl", "unsupported-flock"]


def _no_lock_mechanism(monkeypatch, mode, allow=None):
    """Make `sync` see a platform that cannot serialise: no `fcntl` at all, or an `flock` that raises ENOLCK.
    The once-per-loaded-copy flag is module state shared across test files, so it is reset to count the lines.
    `allow` is the operator's opt-in value; unset (the default) so a developer's own environment never leaks in."""
    monkeypatch.setattr(sync, "_LOCK_NOTES_SAID", set())
    if allow is None:
        monkeypatch.delenv(ALLOW_ENV, raising=False)
    else:
        monkeypatch.setenv(ALLOW_ENV, allow)
    if mode == "no-fcntl":
        monkeypatch.setattr(sync, "fcntl", None)
        return

    def flock(fd, how):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(sync, "fcntl", types.SimpleNamespace(LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB,
                                                             LOCK_UN=fcntl.LOCK_UN, flock=flock))


@pytest.mark.parametrize("mode", NO_LOCK_MODES)
def test_a_lock_that_cannot_be_made_refuses_by_default_and_says_so_once(tmp_path, monkeypatch, capsys, mode):
    _no_lock_mechanism(monkeypatch, mode)
    capsys.readouterr()
    for _ in range(2):
        with pytest.raises(sync.LedgerLockRefused) as refused:
            sync._ledger_lock(tmp_path / ".sdlc")
    assert isinstance(refused.value, OSError), "tick callers and publish_entry catch OSError"
    assert LEVER in str(refused.value), str(refused.value)
    lines = [ln for ln in capsys.readouterr().err.splitlines() if LEVER in ln]
    assert len(lines) == 1, lines


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", " yes "])
@pytest.mark.parametrize("mode", NO_LOCK_MODES)
def test_the_opt_in_restores_publishing_unserialised_and_says_so_once(tmp_path, monkeypatch, capsys, mode, value):
    _no_lock_mechanism(monkeypatch, mode, allow=value)
    capsys.readouterr()
    assert sync._ledger_lock(tmp_path / ".sdlc") is True
    assert sync._ledger_lock(tmp_path / ".sdlc") is True
    lines = [ln for ln in capsys.readouterr().err.splitlines() if "not serialised" in ln]
    assert len(lines) == 1 and ALLOW_ENV in lines[0], lines


@pytest.mark.parametrize("value", ["", "0", "false", "no", "on", "2", "y"])
def test_only_one_true_or_yes_opts_in(tmp_path, monkeypatch, value):
    """The knob's parse: anything but `1`, `true` or `yes` (any case) is NOT an opt-in, so a stray value never
    switches the guarantee off."""
    _no_lock_mechanism(monkeypatch, "no-fcntl", allow=value)
    with pytest.raises(sync.LedgerLockRefused):
        sync._ledger_lock(tmp_path / ".sdlc")


def test_an_unsupported_flock_is_not_read_as_held(tmp_path, monkeypatch):
    """Only EWOULDBLOCK/EAGAIN mean `another process holds it` (`None`: the waiter polls, the tick defers). Every
    other errno is a filesystem that cannot lock at all: that REFUSES (it is not 'held', which would wait or
    defer for ever and say nothing about why) unless the operator opted in."""
    for number in (errno.ENOLCK, errno.EOPNOTSUPP, errno.EINVAL):
        _no_lock_mechanism(monkeypatch, "unsupported-flock")

        def flock(fd, how, number=number):
            raise OSError(number, "injected")

        monkeypatch.setattr(sync, "fcntl", types.SimpleNamespace(LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB,
                                                                 LOCK_UN=fcntl.LOCK_UN, flock=flock))
        with pytest.raises(sync.LedgerLockRefused):
            sync._ledger_lock(tmp_path / ".sdlc")
    for number in (errno.EWOULDBLOCK, errno.EAGAIN):

        def held(fd, how, number=number):
            raise OSError(number, "held")

        monkeypatch.setattr(sync, "fcntl", types.SimpleNamespace(LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB,
                                                                 LOCK_UN=fcntl.LOCK_UN, flock=held))
        assert sync._ledger_lock(tmp_path / ".sdlc") is None


@pytest.mark.parametrize("mode", NO_LOCK_MODES)
def test_the_holder_probe_stays_quiet_when_nothing_can_be_locked(tmp_path, monkeypatch, mode):
    """`_ledger_lock_holder` is the waiter's probe: it must never raise, and with no lock mechanism it names no
    holder (an empty answer), whatever record is on disk."""
    base = tmp_path / ".sdlc"
    _lock_file(base).parent.mkdir(parents=True)
    _lock_file(base).write_text(f"pid={os.getpid()} verb=publish since={int(time.time())}\n", encoding="utf-8")
    _no_lock_mechanism(monkeypatch, mode)
    assert sync._ledger_lock_holder(base) == ""


ENTRY_POINTS = ["publish", "pull", "publish_receipt", "reroot", "publish_entry"]


def _run_entry_point(entry, base, cfg, git, monkeypatch):
    """Call one locked entry point against a fake worktree and a recording runner; return `(output, ran)` where
    `ran` says whether any git (or, for the re-root, the re-root itself) ran."""
    ledger.append(base, cfg, "claimed", "1")
    return _call_entry_point(entry, base, cfg, git, monkeypatch)


def _call_entry_point(entry, base, cfg, git, monkeypatch):
    """`_run_entry_point` without the append, for a test that plants something between the two."""
    ran = []
    if entry == "publish":
        out = sync.publish(base, cfg, run=git)
    elif entry == "pull":
        out = sync.pull(base, cfg, run=git)
    elif entry == "publish_receipt":
        relative = "receipts/v1/" + "a" * 64 + "/parent.json"
        target = sync.worktree(base) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n", encoding="utf-8")
        out = sync.publish_receipt(base, relative, config=cfg, run=git)
    elif entry == "reroot":
        monkeypatch.setattr(sync, "_reroot", lambda *a, **k: ran.append(1) or "")
        out = sync._reroot_guarded(git, base, sync._channel(sync.LEDGER, base, cfg), "dana")
    else:
        out = sync.publish_entry(base, cfg, "claim", run=git, deadline_seconds=5)
    return out, bool(git.calls or ran)


@pytest.mark.parametrize("mode", NO_LOCK_MODES)
@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_every_locked_entry_point_defers_and_names_the_lever_when_the_lock_cannot_be_made(
        tmp_path, monkeypatch, entry, mode):
    """`publish`, `pull`, `publish_receipt`, `_reroot_guarded` (tick callers: they defer with a string) and
    `publish_entry` (its own direct `_ledger_lock` call, outside `_with_ledger_lock`: it must end in the
    NOT-published line and never raise) all stop BEFORE any git runs, and say why in words that name the lever."""
    base, cfg = _fake_worktree(tmp_path)
    _no_lock_mechanism(monkeypatch, mode)
    git = _NoGit()
    out, ran = _run_entry_point(entry, base, cfg, git, monkeypatch)
    assert not ran, f"{entry} ran git although the ledger lock could not be made: {git.calls}"
    assert LEVER in out, out
    assert "..." not in out, "the lever was cut off by the 240-character scrub cap"
    assert out.startswith({"publish": "publish deferred", "pull": "pull deferred", "publish_receipt": "receipt pending",
                           "reroot": "; re-root deferred", "publish_entry": "ledger: NOT published"}[entry]), out


@pytest.mark.parametrize("mode", NO_LOCK_MODES)
@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_with_the_opt_in_every_locked_entry_point_runs_unserialised(tmp_path, monkeypatch, entry, mode):
    """The control of the test above: the same call with `SIGMA_LEDGER_ALLOW_UNSERIALISED=1` does run."""
    base, cfg = _fake_worktree(tmp_path)
    _no_lock_mechanism(monkeypatch, mode, allow="1")
    git = _NoGit()
    out, ran = _run_entry_point(entry, base, cfg, git, monkeypatch)
    assert ran, f"{entry} did not run with {LEVER}: {out!r}"
    assert LEVER not in (out or "")


def test_the_lock_file_lives_under_state_and_the_checkout_stays_clean(tmp_path, origin):
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    (repo / ".gitignore").write_text(".sdlc/state/\n.sdlc/config.json\n", encoding="utf-8")     # NOT .sdlc/ledger
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "stop ignoring")
    before = _git(repo, "status", "--porcelain")
    ledger.append(base, cfg, "claimed", "2081")
    sync.publish_entry(base, cfg, "claim")
    assert _lock_file(base).is_file()
    assert _git(repo, "status", "--porcelain") == before


def test_a_released_lock_leaves_no_holder_record(tmp_path, holders):
    base = tmp_path / ".sdlc"
    lock = sync._ledger_lock(base, verb="publish")
    assert f"pid={os.getpid()} " in _lock_file(base).read_text(encoding="utf-8")
    sync._release_ledger_lock(lock)
    assert _lock_file(base).read_text(encoding="utf-8") == ""
    holder = holders(base, code=_HOLD_VIA_HELPER, via_helper=True)
    assert f"pid {holder.pid}" in sync._ledger_lock_holder(base)
    holder.kill()
    assert f"pid={holder.pid}" in _lock_file(base).read_text(encoding="utf-8"), "precondition: a stale record"
    assert sync._ledger_lock_holder(base) == "", "a dead holder's stale record was reported as a holder"


def test_a_lock_freed_while_the_waiter_checks_is_reported_as_that_not_as_an_empty_holder(tmp_path, monkeypatch):
    """The lock was busy when the acquire failed and free when the probe looked (`_ledger_lock_holder` answers
    `""`): the line must say THAT, not claim a holder and name nobody ("another publisher holds the ledger
    worktree: " followed by nothing). Both the waiter (`publish_entry`) and a tick caller (`publish`)."""
    base, cfg = _fake_worktree(tmp_path)
    ledger.append(base, cfg, "claimed", "1")
    held = sync._ledger_lock(base)
    monkeypatch.setattr(sync, "_ledger_lock_holder", lambda sdlc_dir: "")
    git = _NoGit()
    try:
        said = sync.publish_entry(base, cfg, "claim", run=git, deadline_seconds=0.3)
        out = sync.publish(base, cfg, run=git)
    finally:
        sync._release_ledger_lock(held)
    assert said.startswith("ledger: NOT published") and "released while" in said and "holds" not in said, said
    assert out.startswith("publish deferred") and "released while" in out and "()" not in out, out
    assert git.calls == []


# ---- F2 (review cycle 1): the lock is held across the WHOLE body, pinned deterministically
#
# The smoke test below can pass by luck without the lock and was the only thing that went red when `publish_entry`
# released the lock right after taking it. These probe the lock from a SECOND open file description at EVERY git call
# of the body: while the body runs, `sync._ledger_lock(base)` must answer `None` (held). If it ever acquires, the lock
# was not held at that call: the probe lets go again and records the call as a leak.


class _LockProbe:
    """A git runner (injected as `run=`, or standing in for `sync._run_git`) that probes the ledger lock on every
    call and then runs the real git (`inner`), or answers `""` when there is none."""

    def __init__(self, base, inner=None):
        self.base, self.inner, self.calls, self.leaks = base, inner, [], []

    def __call__(self, cwd, args, **kw):
        self.calls.append(list(args))
        got = sync._ledger_lock(self.base)
        if got is not None:
            sync._release_ledger_lock(got)
            self.leaks.append(list(args))
        return self.inner(cwd, args, **kw) if self.inner else ""

    def verbs(self):
        return {args[0] for args in self.calls if args}


def test_publish_entry_holds_the_lock_across_every_git_call_of_a_fresh_container(tmp_path, origin, monkeypatch):
    """Worktree creation, the exclude, the re-root and the push all run with the lock held. Replacing the
    module's `_run_git` covers every git the step runs, the stale-rebase abort included."""
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    ledger.append(base, cfg, "claimed", "1")
    probe = _LockProbe(base, inner=sync._run_git)
    monkeypatch.setattr(sync, "_run_git", probe)
    said = sync.publish_entry(base, cfg, "claim")
    assert said and said.startswith("ledger: published"), said
    assert {"worktree", "push"} <= probe.verbs() and len(probe.calls) >= 5, probe.calls
    assert not probe.leaks, f"publish_entry ran git without the lock: {probe.leaks}"


def test_publish_entry_holds_the_lock_while_it_clears_a_stale_rebase_and_publishes(tmp_path, origin, monkeypatch):
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    wt = _stage_live_rebase(tmp_path, origin, base)               # nobody holds the lock: its publisher is dead
    _append_from_another_process(base, "1")
    probe = _LockProbe(base, inner=sync._run_git)
    monkeypatch.setattr(sync, "_run_git", probe)
    said = sync.publish_entry(base, cfg, "claim", deadline_seconds=60)
    assert said and said.startswith("ledger: published"), said
    assert ["rebase", "--abort"] in probe.calls and "push" in probe.verbs(), probe.calls
    assert not _rebase_dir(wt).exists()
    assert not probe.leaks, f"publish_entry ran git without the lock: {probe.leaks}"


@pytest.mark.parametrize("verb,needs", [("publish", "push"), ("pull", "fetch"), ("publish_receipt", "hash-object"),
                                        ("reroot", "merge-base")])
def test_a_tick_verb_holds_the_lock_across_every_git_call_of_its_body(tmp_path, origin, verb, needs):
    """`_with_ledger_lock`'s body: `publish`, `pull`, `publish_receipt` and `_reroot_guarded` each run their git
    with the lock held, from the first call to the last."""
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    probe = _LockProbe(base, inner=sync._run_git)
    if verb == "publish":
        ledger.append(base, cfg, "claimed", "x")
        out = sync.publish(base, cfg, run=probe)
    elif verb == "pull":
        out = sync.pull(base, cfg, run=probe)
    elif verb == "publish_receipt":
        relative = "receipts/v1/" + "b" * 64 + "/parent.json"
        target = sync.worktree(base) / relative
        target.parent.mkdir(parents=True)
        target.write_text("{}\n", encoding="utf-8")
        out = sync.publish_receipt(base, relative, config=cfg, run=probe)
    else:
        out = sync._reroot_guarded(probe, base, sync._channel(sync.LEDGER, base, cfg), "dana")
    assert needs in probe.verbs(), (verb, out, probe.calls)
    assert not probe.leaks, f"{verb} ran git without the lock: {probe.leaks}"


# ---- F3 (review cycle 1): a stale rebase that git cannot abort is reported, never published over
#
# `_abort_stale_rebase` is best effort and swallowed a FAILED `git rebase --abort`: with the rebase directory still
# there, `publish_entry` published from the detached HEAD and printed "published ... the team can see it now" while
# origin lacked the earlier entry. The reviewer's reproduction: a dead publisher's rebase stopped BEFORE the pick that
# tracks process P2's entries file; P2 then appends again, so that file reappears UNTRACKED and `git rebase --abort`
# exits 128 ("untracked working tree files would be overwritten by reset").


def _push_a_rival_commit(tmp_path, origin):
    _, obase, ocfg = _clone(tmp_path, origin, "boxrival")
    sync.init(obase, ocfg)
    owt = sync.worktree(obase)
    (owt / "theirs.txt").write_text("their change\n", encoding="utf-8")
    _git(owt, "add", "-A")
    _git(owt, "commit", "-q", "-m", "theirs")
    _git(owt, "push", "-q", "origin", f"HEAD:{sync.DEFAULT_BRANCH}")


_BREAK_FIRST = "sh -c 'printf \"break\\n\" | cat - \"$1\" > \"$1.new\" && mv \"$1.new\" \"$1\"' --"


def _stage_a_rebase_git_cannot_abort(tmp_path, origin):
    """Real git, deterministic. Returns `(base, cfg, worktree, p2_file)`."""
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    wt = sync.worktree(base)
    _append_from_another_process(base, "p2-first")
    p2_file = next(p for p in sorted((wt / "entries").glob("*.jsonl")) if "p2-first" in p.read_text(encoding="utf-8"))
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "P1's publish tracked P2's entries file")
    _push_a_rival_commit(tmp_path, origin)
    _git(wt, "fetch", "-q", "origin", sync.DEFAULT_BRANCH)
    subprocess.run(["git", "-C", str(wt), "rebase", "-i", f"origin/{sync.DEFAULT_BRANCH}"], capture_output=True,
                   text=True, env=dict(os.environ, GIT_SEQUENCE_EDITOR=_BREAK_FIRST, GIT_EDITOR="true"))
    assert _rebase_dir(wt).exists() and not p2_file.exists(), "precondition: stopped before the pick tracking P2's file"
    with open(p2_file, "a", encoding="utf-8") as fh:            # P2 appends again: the file reappears, UNTRACKED
        fh.write(json.dumps({"goal": "p2-second", "kind": "claimed", "ts": 2, "actor": "dana", "id": "z"}) + "\n")
    aborted = subprocess.run(["git", "-C", str(wt), "rebase", "--abort"], capture_output=True, text=True)
    assert aborted.returncode != 0 and _rebase_dir(wt).exists(), "precondition: git itself cannot abort this rebase"
    return base, cfg, wt, p2_file


def test_a_stale_rebase_that_git_cannot_abort_is_reported_and_nothing_is_pushed(tmp_path, origin):
    base, cfg, wt, p2_file = _stage_a_rebase_git_cannot_abort(tmp_path, origin)
    said = sync.publish_entry(base, cfg, "next publisher", deadline_seconds=60)
    assert said and said.startswith("ledger: NOT published"), said
    assert "rebase is still in progress" in said and "docs/cloud-sessions.md" in said and "..." not in said, said
    assert _rebase_dir(wt).exists(), "the rebase directory is the evidence; nothing may pretend it is gone"
    assert {"p2-first", "p2-second"}.isdisjoint({e["goal"] for e in _origin_text(origin)}), \
        "published from a detached HEAD in the middle of a rebase"
    assert "p2-second" in p2_file.read_text(encoding="utf-8"), "the entry on disk must survive the refusal"


def test_the_documented_lever_for_a_rebase_git_cannot_abort_works(tmp_path, origin):
    """The lever docs/cloud-sessions.md gives (residual (h)): move the untracked entries file git names aside, run
    `git rebase --abort` in the ledger worktree, append the moved file's lines to the restored file, publish."""
    base, cfg, wt, p2_file = _stage_a_rebase_git_cannot_abort(tmp_path, origin)
    assert sync.publish_entry(base, cfg, "stuck", deadline_seconds=60).startswith("ledger: NOT published")
    aside = tmp_path / "moved-aside.jsonl"
    p2_file.rename(aside)
    _git(wt, "rebase", "--abort")
    with open(p2_file, "a", encoding="utf-8") as fh:
        fh.write(aside.read_text(encoding="utf-8"))
    said = sync.publish_entry(base, cfg, "after the lever", deadline_seconds=60)
    assert said and said.startswith("ledger: published"), said
    assert {"p2-first", "p2-second"} <= {e["goal"] for e in _origin_text(origin)}


def test_the_abort_answers_false_when_nothing_is_left_in_progress_and_never_raises(tmp_path, origin):
    """The seam: `_abort_stale_rebase` stays best effort and answers True only when a rebase is STILL there after the
    attempt: False when there was none, when it was cleared, and when it could not tell."""
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    assert sync._abort_stale_rebase(base) is False                                    # nothing to abort
    wt = _stage_live_rebase(tmp_path, origin, base)
    assert sync._abort_stale_rebase(base) is False and not _rebase_dir(wt).exists()   # cleared
    assert sync._abort_stale_rebase(tmp_path / "no-such-sdlc") is False               # cannot tell: no raise


def test_the_abort_answers_true_when_git_could_not_clear_the_rebase(tmp_path, origin):
    base, cfg, wt, _ = _stage_a_rebase_git_cannot_abort(tmp_path, origin)
    assert sync._abort_stale_rebase(base) is True and _rebase_dir(wt).exists()


_SMOKE_DRIVER = """
import importlib.util, json, os, sys, time
spec = importlib.util.spec_from_file_location("sync", sys.argv[1])
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
sdlc, gate, idx, count = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
cfg = json.load(open(os.path.join(sdlc, "config.json")))
sync.ledger.append(sdlc, cfg, "claimed", "p" + idx)
open(os.path.join(gate, "ready" + idx), "w").close()
deadline = time.time() + 120
while len(os.listdir(gate)) < count and time.time() < deadline:
    time.sleep(0.002)
print(sync.publish_entry(sdlc, cfg, "claim p" + idx) or "")
"""


def test_processes_publishing_into_one_worktree_do_not_print_not_published_for_a_landed_entry(tmp_path, origin):
    """SMOKE TEST (probabilistic). Six real processes append once each and publish into ONE ledger worktree after
    a file barrier. The assertion is NOT 'both entries reach origin' (measured green on code without the lock:
    the first winner stages every file the actor owns). It is: every entry that reached origin was reported
    without `NOT published` by its own process. Test
    `test_a_live_publishers_rebase_is_left_alone_and_a_dead_ones_is_cleared` is the deterministic control and the
    system of record: this one can pass by luck without the lock. Its sensitivity on code without the lock is
    recorded below; each process appends exactly once (the single-append shape, D-13): the second-append shape
    is not tested here.

    UNFIXED-CODE SENSITIVITY (measured, this machine, under load): on code without the lock, 15 of the 18
    processes (three rounds of six) printed `NOT published` for an entry that had landed, in each of two runs
    (this test alone, then the four-file command); on a quiet machine the researcher measured 4 of 6, 5 of 6,
    6 of 8 and 8 of 8 for the same shape. With the lock: none."""
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    driver = tmp_path / "driver.py"
    driver.write_text(_SMOKE_DRIVER, encoding="utf-8")
    count, false_reports = 6, []
    for round_no in range(3):
        gate = tmp_path / f"gate{round_no}"
        gate.mkdir()
        procs = [subprocess.Popen([sys.executable, str(driver), str(S / "sync.py"), str(base), str(gate),
                                   f"{round_no}x{i}", str(count)], stdout=subprocess.PIPE, text=True)
                 for i in range(count)]
        outs = [p.communicate(timeout=300)[0].strip() for p in procs]
        assert all(p.returncode == 0 for p in procs)
        landed = {e["goal"] for e in _origin_text(origin)}
        wrote = {f"p{round_no}x{i}" for i in range(count)}
        assert wrote <= landed, f"missing on origin: {sorted(wrote - landed)}"
        false_reports += [o for i, o in enumerate(outs) if "NOT published" in o and f"p{round_no}x{i}" in landed]
    assert not false_reports, f"{len(false_reports)} process(es) said NOT published for an entry that landed: {false_reports[:2]}"


# ---- G1 (review cycle 2): the lock file is never written through a symlink (#708)
#
# THE DEFECT. `_ledger_lock` opened `.sdlc/state/ledger-sync.lock` with a plain `open(path, "a+")`, then truncated
# and wrote it. A repository can COMMIT a symlink there (git checks it out; `.gitignore` does not apply to a tracked
# file), and `ledger.py`, `assign.py` and `liveness_prune.py` do not run the #708 entry guard that `loop.py` and
# `work.py` run: `python3 ledger.py append .sdlc claimed 88` left the victim EMPTY and printed "ledger: published".
# Sigma never writes through a symlink under `.sdlc` (`state.refuse_symlinks`, `state.safe_state_open`): the lock
# file now opens through `safe_state_open`, so a symlink at it, or a symlinked `state/` above it, is an `OSError`
# and every locked verb defers. Not overridable: `SIGMA_LEDGER_ALLOW_UNSERIALISED` is only for a platform with no
# lock to take.

PRECIOUS = "PRECIOUS DATA\n"
SYMLINK_SHAPES = ["file", "dangling", "state-dir"]


def _plant_symlink(shape, base, tmp_path):
    """Plant what a repository can commit below `base` (a `.sdlc` directory): a symlink AT the lock file (to a file
    with contents, or to nothing at all) or a symlink AS `state/`. Returns `unchanged()`: True while nothing outside
    `.sdlc` was written, created or emptied."""
    state_dir = base / "state"
    if shape == "state-dir":
        outside = tmp_path / "outside"
        outside.mkdir()
        shutil.rmtree(state_dir, ignore_errors=True)
        state_dir.symlink_to(outside)
        return lambda: list(outside.iterdir()) == []
    state_dir.mkdir(parents=True, exist_ok=True)
    victim = tmp_path / "victim.txt"
    if shape == "file":
        victim.write_text(PRECIOUS, encoding="utf-8")
    _lock_file(base).symlink_to(victim)
    if shape == "file":
        return lambda: victim.read_text(encoding="utf-8") == PRECIOUS
    return lambda: not victim.exists()


@pytest.mark.parametrize("shape", SYMLINK_SHAPES)
@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_every_locked_entry_point_defers_and_leaves_the_target_alone_when_the_lock_path_is_a_symlink(
        tmp_path, monkeypatch, entry, shape):
    """`publish`, `pull`, `publish_receipt` and `_reroot_guarded` (they return a deferral) and `publish_entry` (its
    own acquire: NOT published, never raises) all stop before any git runs, name the symlink and leave what it
    points at byte-identical. The reviewer's reproduction was `ledger.py append`, which reaches `publish`."""
    base, cfg = _fake_worktree(tmp_path)
    ledger.append(base, cfg, "claimed", "1")                    # before the plant: it may write under `state/`
    unchanged = _plant_symlink(shape, base, tmp_path)
    git = _NoGit()
    out, ran = _call_entry_point(entry, base, cfg, git, monkeypatch)
    assert unchanged(), f"{entry} wrote through the symlink ({shape})"
    assert not ran, f"{entry} ran git although the lock path is a symlink: {git.calls}"
    assert "REFUSED" in out and "symlink" in out, out
    if entry == "publish_entry":
        assert out.startswith("ledger: NOT published"), out
    else:
        assert "cannot take the lock" in out and "git rm" in out, out      # the lever: `git rm` the symlink


def test_a_symlinked_lock_is_not_lifted_by_the_unserialised_opt_in(tmp_path, monkeypatch):
    """`SIGMA_LEDGER_ALLOW_UNSERIALISED` only accepts a platform with no lock to take. It does not make a
    committed symlink writable; and where it does apply, no lock file is opened at all."""
    base, cfg = _fake_worktree(tmp_path)
    unchanged = _plant_symlink("file", base, tmp_path)
    monkeypatch.setenv(ALLOW_ENV, "1")
    with pytest.raises(OSError, match="symlink"):
        sync._ledger_lock(base)
    assert unchanged()
    _no_lock_mechanism(monkeypatch, "no-fcntl", allow="1")
    assert sync._ledger_lock(base) is True and unchanged()


def _lock_what_the_link_points_at(shape, base, tmp_path):
    """A symlink at the lock file (`file`) or at `state/` (`state-dir`) whose TARGET is flocked with a holder record
    in it, as another publisher's lock would be. Returns `(target, record)`. A probe that followed the link would
    report this process as the holder of the ledger lock; the descriptor stays open until the test ends."""
    record = f"pid={os.getpid()} verb=publish since={int(time.time())}\n"
    if shape == "file":
        (base / "state").mkdir(parents=True)
        target = tmp_path / "victim.txt"
        _lock_file(base).symlink_to(target)
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        base.mkdir()
        (base / "state").symlink_to(outside)
        target = outside / sync.LEDGER_LOCK_NAME
    target.write_text(record, encoding="utf-8")
    fd = os.open(target, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return target, record, fd


@pytest.mark.parametrize("shape", ["file", "state-dir"])
def test_the_holder_probe_never_looks_through_a_symlink(tmp_path, shape):
    base = tmp_path / ".sdlc"
    target, record, fd = _lock_what_the_link_points_at(shape, base, tmp_path)
    try:
        assert sync._ledger_lock_holder(base) == "", "the probe followed a symlink and named a holder"
    finally:
        os.close(fd)
    assert target.read_text(encoding="utf-8") == record


def test_the_holder_probe_never_creates_the_lock_file_or_a_dangling_target(tmp_path):
    """A probe looks at a lock that is there; creating one is the lock's own business (and a probe that creates is
    how a dangling symlink grows its target)."""
    base = tmp_path / ".sdlc"
    (base / "state").mkdir(parents=True)
    assert sync._ledger_lock_holder(base) == "" and not _lock_file(base).exists()
    dangling = tmp_path / "not-created.txt"
    _lock_file(base).symlink_to(dangling)
    assert sync._ledger_lock_holder(base) == "" and not dangling.exists()


@pytest.mark.parametrize("shape", ["file", "dangling"])
def test_o_nofollow_alone_still_refuses_a_symlink_when_the_lstat_walk_is_bypassed(tmp_path, monkeypatch, shape):
    """The lstat walk and the open are two steps; a symlink swapped in BETWEEN them is what `O_NOFOLLOW` closes.
    The walk is replaced here by one that answers `fine`, so only the flag stands between the open and the victim
    (the lock's open and the holder probe's open)."""
    base = tmp_path / ".sdlc"
    unchanged = _plant_symlink(shape, base, tmp_path)
    monkeypatch.setattr(sync.state, "refuse_symlinks", lambda *a, **k: _lock_file(base))
    with pytest.raises(OSError):
        sync._ledger_lock(base)
    assert unchanged(), "the lock opened through a symlink the walk missed"


def test_o_nofollow_alone_keeps_the_holder_probe_off_a_symlink_the_walk_missed(tmp_path, monkeypatch):
    """The probe's own open, with the lstat walk bypassed: the link's target is locked with a holder record in it,
    so a probe that followed the link would name a holder."""
    base = tmp_path / ".sdlc"
    target, record, fd = _lock_what_the_link_points_at("file", base, tmp_path)
    monkeypatch.setattr(sync.state, "refuse_symlinks", lambda *a, **k: _lock_file(base))
    try:
        assert sync._ledger_lock_holder(base) == "", "the probe opened through a symlink the walk missed"
    finally:
        os.close(fd)
    assert target.read_text(encoding="utf-8") == record


def test_a_symlink_at_the_self_created_marker_is_not_written_through(tmp_path, origin):
    """`publish_entry` marks a worktree it created for itself with `state/ledger-self-created`. That file is
    written by a `publish_entry` that the exit hook now runs in processes with no #708 entry guard, so it takes the
    same rule: a symlink there is not written through, and the publish itself goes on."""
    repo, base, cfg = _clone(tmp_path, origin, "boxa")
    victim = tmp_path / "victim.txt"
    victim.write_text(PRECIOUS, encoding="utf-8")
    (base / "state" / "ledger-self-created").symlink_to(victim)
    ledger.append(base, cfg, "claimed", "1")
    said = sync.publish_entry(base, cfg, "claim")
    assert said and said.startswith("ledger: published"), said
    assert victim.read_text(encoding="utf-8") == PRECIOUS, "the marker was written through a symlink"


# ---- G3a (review cycle 2): a stale rebase of the apply backend is found, cleared and reported
#
# `rebase-apply` is the marker of a plain `git rebase` on git older than 2.26 (the apply backend, `git rebase --apply`
# today), which `_pull_ledger` and `_push_receipt` run; `rebase-merge` is the merge backend's. Only the second was
# ever tested, so dropping `rebase-apply` from `_abort_stale_rebase` stayed green.


def _marker_dir(wt, name):
    return pathlib.Path(wt / _git(wt, "rev-parse", "--git-path", name))


def test_a_stale_rebase_of_the_apply_backend_is_cleared(tmp_path, origin):
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    wt = sync.worktree(base)
    _push_a_rival_commit(tmp_path, origin)                           # origin gains theirs.txt
    (wt / "theirs.txt").write_text("our own version\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ours")
    _git(wt, "fetch", "-q", "origin", sync.DEFAULT_BRANCH)
    _git(wt, "rebase", "--apply", f"origin/{sync.DEFAULT_BRANCH}", check=False)         # add/add: it stops
    apply_dir = _marker_dir(wt, "rebase-apply")
    assert apply_dir.exists() and not _rebase_dir(wt).exists(), "precondition: a stopped APPLY-backend rebase"
    assert sync._abort_stale_rebase(base) is False
    assert not apply_dir.exists(), "the apply backend's stale rebase was left in place"


def test_a_rebase_apply_directory_git_cannot_abort_is_reported_not_published_over(tmp_path, origin):
    repo, base, cfg = _fresh_worktree(tmp_path, origin)
    wt = sync.worktree(base)
    apply_dir = _marker_dir(wt, "rebase-apply")
    apply_dir.mkdir()                                                 # git's own `--abort` refuses this one
    refused = subprocess.run(["git", "-C", str(wt), "rebase", "--abort"], capture_output=True, text=True)
    assert refused.returncode != 0 and apply_dir.exists(), "precondition: git itself cannot clear it"
    assert sync._abort_stale_rebase(base) is True, "a rebase-apply directory that is still there was not reported"
    ledger.append(base, cfg, "claimed", "after-apply")
    said = sync.publish_entry(base, cfg, "claim", deadline_seconds=30)
    assert said and said.startswith("ledger: NOT published") and "rebase is still in progress" in said, said
    assert "after-apply" not in {e["goal"] for e in _origin_text(origin)}


# ---- G3b (review cycle 2): every locked entry point RELEASES the lock, and clears its record, however it ends
#
# CPython closes a dropped file object by refcount, so without the `finally` the kernel lock is usually released
# anyway and only the holder record is left uncleared (a stale `pid=... verb=...` line for a waiter to name, which its
# own probe then has to second-guess). Where an exception keeps the frame alive (the traceback), the lock itself is
# held too. So both are checked, from a SECOND process: the record is empty and the lock can be taken.

_TRY_LOCK = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
"""


def _assert_released(base):
    assert _lock_file(base).read_text(encoding="utf-8") == "", "the holder record was left behind"
    taken = subprocess.run([sys.executable, "-c", _TRY_LOCK, str(_lock_file(base))], timeout=60)
    assert taken.returncode == 0, "a second process cannot take the lock: it is still held"


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_every_locked_entry_point_leaves_the_lock_free_and_its_record_empty_when_it_returns(
        tmp_path, monkeypatch, entry):
    base, cfg = _fake_worktree(tmp_path)
    out, ran = _run_entry_point(entry, base, cfg, _NoGit(), monkeypatch)
    assert ran, f"{entry} never ran its body under the lock: {out!r}"
    _assert_released(base)


def test_a_body_that_raises_still_releases_the_lock(tmp_path):
    """`_with_ledger_lock` is what `publish`, `pull`, `publish_receipt` and the re-root run through. The raised
    exception is kept alive (its traceback holds the frame, and so the lock) while the lock is checked."""
    base = tmp_path / ".sdlc"

    def body():
        raise RuntimeError("the body failed")

    with pytest.raises(RuntimeError) as failed:
        sync._with_ledger_lock(base, "publish", body, busy=lambda why: why)
    _assert_released(base)
    assert failed.value is not None


def test_a_verb_whose_git_raises_still_releases_the_lock(tmp_path):
    base, cfg = _fake_worktree(tmp_path)

    def git(cwd, args):
        raise RuntimeError("git exploded")

    try:
        sync.pull(base, cfg, run=git)
    except RuntimeError as failed:
        kept = failed                                              # alive while the lock is checked
    else:
        kept = None
    assert kept is not None, "precondition: pull propagates a failing fetch"
    _assert_released(base)


def test_publish_entry_releases_the_lock_when_its_body_raises(tmp_path):
    base, cfg = _fake_worktree(tmp_path)
    ledger.append(base, cfg, "claimed", "1")

    def publish_fn(sdlc_dir, config, run=None, **kw):
        raise RuntimeError("the publish blew up")

    said = sync.publish_entry(base, cfg, "claim", run=_NoGit(), publish_fn=publish_fn, deadline_seconds=5)
    assert said.startswith("ledger: NOT published") and "the publish blew up" in said, said
    _assert_released(base)


def test_publish_entry_releases_the_lock_when_the_publish_says_no(tmp_path):
    """A publish that is not `published` returns through `_not_published`, still inside the `try`."""
    base, cfg = _fake_worktree(tmp_path)
    ledger.append(base, cfg, "claimed", "1")
    said = sync.publish_entry(base, cfg, "claim", run=_NoGit(), deadline_seconds=5,
                              publish_fn=lambda *a, **k: "publish deferred (will retry next tick): rejected")
    assert said.startswith("ledger: NOT published") and "rejected" in said, said
    _assert_released(base)


def test_publish_entry_releases_the_lock_when_a_rebase_cannot_be_cleared(tmp_path, origin):
    """The stuck-rebase return is an early return from inside the `try`."""
    base, cfg, wt, p2_file = _stage_a_rebase_git_cannot_abort(tmp_path, origin)
    said = sync.publish_entry(base, cfg, "stuck", deadline_seconds=60)
    assert said.startswith("ledger: NOT published") and "rebase is still in progress" in said, said
    _assert_released(base)


# ---- G3c (review cycle 2): a REFUSED branch or remote stays loud, with the lock free AND held
#
# At the previous head `publish` computed `worktree`, `branch` and `remote` first, and `_ref_value` RAISES for a value
# that would reach git as an option (#710). The line is kept ahead of the lock: without it a publisher that finds the
# lock held, or no worktree, would answer with a quiet deferral and the refusal would never be seen.


@pytest.mark.parametrize("worktree", ["a-worktree", "no-worktree"])
@pytest.mark.parametrize("lock", ["free", "held-by-another-process"])
@pytest.mark.parametrize("key", ["branch", "remote"])
def test_a_refused_branch_or_remote_stays_loud_whatever_the_lock_and_the_worktree(
        tmp_path, holders, key, lock, worktree):
    base, cfg = _fake_worktree(tmp_path)
    if worktree == "no-worktree":
        shutil.rmtree(sync.worktree(base))
    cfg["ledger"][key] = "--upload-pack=touch PWNED"
    if lock == "held-by-another-process":
        holders(base)
    git = _NoGit()
    with pytest.raises(ValueError, match="REFUSED"):
        sync.publish(base, cfg, run=git)
    assert git.calls == []


# ---- G5d (review cycle 2): what the NOT-published line promises is true of its reason

def test_a_lock_refusal_a_symlinked_lock_and_a_stuck_rebase_say_what_clears_them_not_that_the_next_step_retries(
        tmp_path, monkeypatch):
    """The next loop step fails the same way until someone acts on a lock REFUSAL, an unusable lock file or a
    STUCK rebase, so the tail says the entry is safe on disk and will publish once the named cause is cleared. Every
    other reason keeps the line it always had."""
    base, cfg = _fake_worktree(tmp_path)
    retry, waits = "the next loop step retries it", "will publish once"
    ledger.append(base, cfg, "claimed", "1")
    _no_lock_mechanism(monkeypatch, "no-fcntl")
    refused = sync.publish_entry(base, cfg, "claim", run=_NoGit(), deadline_seconds=5)
    monkeypatch.undo()
    _plant_symlink("file", base, tmp_path)
    linked = sync.publish_entry(base, cfg, "claim", run=_NoGit(), deadline_seconds=5)
    stuck = sync._not_published("claim", sync.STUCK_REBASE, 90)
    ordinary = [sync._not_published("claim", "publish deferred: x", 90),
                sync._not_published("claim", RuntimeError("a push was rejected"), 90),
                sync._not_published("claim", "another publisher holds the ledger worktree: pid 1", 90)]
    for text in (refused, linked, stuck):
        assert waits in text and "safely on disk" in text and retry not in text, text
    for text in ordinary:
        assert retry in text and waits not in text, text
    assert LEVER in refused and "rebase is still in progress" in stuck and "REFUSED" in linked

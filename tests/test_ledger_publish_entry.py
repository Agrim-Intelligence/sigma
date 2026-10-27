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
import importlib.util
import json
import pathlib
import subprocess
import threading
import time

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
    names = subprocess.run(["git", "--git-dir", str(origin), "ls-tree", "-r", "--name-only", "sdlc-ledger"],
                           capture_output=True, text=True)
    if names.returncode != 0:
        return []
    out = []
    for name in names.stdout.split():
        if name.startswith("entries/") and name.endswith(".jsonl"):
            body = subprocess.run(["git", "--git-dir", str(origin), "show", f"sdlc-ledger:{name}"],
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

    out = sync._push_with_retry(git, tmp_path, "origin", "sdlc-ledger", 12, sleep=sleep)
    assert out == "published" and calls["push"] == 5 and pauses == [0, 1, 2, 3]
    calls["push"] = -100                                    # now it never succeeds
    pauses.clear()
    out = sync._push_with_retry(git, tmp_path, "origin", "sdlc-ledger", 6, sleep=sleep)
    assert out.startswith("publish deferred") and len(pauses) == 5, "bounded in count"


def test_the_pause_is_full_jitter_and_capped():
    seen = []
    for rng in (0.0, 0.5, 0.999):
        seen.append(sync._jitter_sleep(10, rng=lambda r=rng: r, sleep=lambda p: None))
    assert seen[0] == 0.0 and 0 < seen[1] < seen[2] <= 3.0, seen
    assert sync._jitter_sleep(0, rng=lambda: 1.0, sleep=lambda p: None) == 0.15


def test_a_stub_driven_publish_never_sleeps(tmp_path):
    """Every existing stub-driven test passes a runner; none of them may start pausing."""
    assert sync._push_with_retry(lambda c, a: (_ for _ in ()).throw(RuntimeError("no")), tmp_path,
                                 "origin", "b", 3) .startswith("publish deferred")


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
    _git(owt, "push", "-q", "origin", "HEAD:sdlc-ledger")
    (wt / "README.md").write_text("our change\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ours")
    _git(wt, "fetch", "-q", "origin", "sdlc-ledger")
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

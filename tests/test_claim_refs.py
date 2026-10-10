"""claim_refs.py -- the lease-guarded claim ref primitive (#858, slice 2 of epic #845, story #542).

THIS FILE IS THE SYSTEM OF RECORD for the primitive's race guard. Each race below is STAGED, not
raced: two clones hold the same stale view (the `expected` sha each passes), one writes first, and
the second must lose. There is no timing in it, so it cannot pass by luck. The parallel test in
`test_claim_refs_race.py` is only a smoke test.

HERMETIC. Every repository lives under `tmp_path`; every remote is a bare repo addressed by a
filesystem path; each "clone" is `git init` with no commits (the primitive uses `commit-tree` on the
built-in empty tree, and `push <sha>:<ref>` works from an empty repo). The environment sets
`GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` to `os.devnull`. Nothing here can reach a real remote.

CONTROLS (run on the documented gesture `python -m pytest tests/test_claim_refs.py -q`, see the
Controls table of the #858 plan): editing `_lease_flag` to return `--force` turns the create, reclaim,
stale-delete, stale-renew and explicit-lease-argv tests red; each classification row, the nonce, the
post-win tip confirmation and the process-group kill have their own red. The record of which went red
is in `.sdlc/research/858-measurements.md`.
"""
import importlib.util
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cr = _load("claim_refs")

HEX40 = re.compile(r"[0-9a-f]{40}")


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Nothing depends on the host's git identity or config (R-13)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    monkeypatch.setenv("LC_ALL", "C")


def git(cwd, *args, check=True):
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, p.stderr or p.stdout))
    return p.stdout.strip()


class World:
    """One bare remote and N empty-repo clones, all under tmp_path."""

    def __init__(self, root, clones=2):
        self.root = pathlib.Path(root)
        self.remote = str(self.root / "remote.git")
        git(self.root, "init", "-q", "--bare", self.remote)
        self.clones = []
        for i in range(clones):
            d = self.root / ("clone%d" % i)
            git(self.root, "init", "-q", str(d))
            self.clones.append(d)

    def claim(self, clone=0, holder="h", run="r1", acquired_at=1000, lease_seconds=1800,
              renewed_at=None):
        return cr.build_claim(self.clones[clone], holder, "host", run, acquired_at, lease_seconds,
                              renewed_at)

    def tip(self, goal):
        out = git(self.root, "ls-remote", self.remote, cr.claim_ref(goal))
        return out.split("\t")[0] if out else ""


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# ------------------------------------------------------------------------- validation (acceptance 1)


@pytest.mark.parametrize("bad", [
    "a/b", "-x", "x.lock", "a..b", "a b", "A", "x.", "", "a" * 65, "a\x01b", "é", "a\nb",
    ".x", "x/", "/x", "a~1", "a:b", "a*", "a@{b", None, 858, b"abc", ["a"]])
def test_goal_id_refused_table(bad):
    with pytest.raises(ValueError, match="REFUSED"):
        cr.validate_goal_id(bad)
    with pytest.raises(ValueError, match="REFUSED"):
        cr.claim_ref(bad)


@pytest.mark.parametrize("good", ["858", "abc-1", "slug.v2", "a", "0", "a" * 64, "a_b"])
def test_goal_id_accepted_table(good):
    assert cr.validate_goal_id(good) == good
    assert cr.claim_ref(good) == "refs/sigma/claims/" + good


@pytest.mark.parametrize("bad", ["--upload-pack=x", "-o", "a b", "a\nb", "a\tb", "a\x00b", None,
                                 5, "", " "])
def test_remote_refused_table(bad):
    with pytest.raises(ValueError, match="REFUSED"):
        cr.validate_remote(bad)


def test_remote_validation_matches_sync_ref_value():
    """Drift guard: whatever `sync._ref_value` refuses, this module refuses too."""
    spec = importlib.util.spec_from_file_location("sync_for_drift_guard", SCRIPTS / "sync.py")
    sync = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(sync)
    finally:
        sys.path.remove(str(SCRIPTS))
    hostile = ["--upload-pack=x", "-x", "a b", "a\nb", "a\tb", "a\x01b", None, 7, ["x"], "\x1f"]
    for value in hostile:
        with pytest.raises(ValueError):
            sync._ref_value("remote", value)
        with pytest.raises(ValueError, match="REFUSED"):
            cr.validate_remote(value)
    for ok in ("origin", "/tmp/some/remote.git", "git@example.com:o/r.git"):
        assert sync._ref_value("remote", ok) == ok
        assert cr.validate_remote(ok) == ok


# ------------------------------------------------------------------------- observed age (acceptance 3)


def test_observed_age_unchanged_ages_and_changed_resets():
    age, seen = cr.observed_age({}, "g", "a" * 40, 100)
    assert age == 0 and seen["g"] == ("a" * 40, 100)
    age, seen = cr.observed_age(seen, "g", "a" * 40, 160)
    assert age == 60 and seen["g"] == ("a" * 40, 100)          # keeps its first_seen
    age, seen = cr.observed_age(seen, "g", "a" * 40, 400)
    assert age == 300
    age, seen = cr.observed_age(seen, "g", "b" * 40, 410)       # a renewal: the clock resets
    assert age == 0 and seen["g"] == ("b" * 40, 410)
    age, _ = cr.observed_age(seen, "g", "b" * 40, 420)
    assert age == 10


def test_observed_age_never_negative_and_input_not_mutated():
    before = {"g": ("a" * 40, 500), "h": ("c" * 40, 1)}
    snapshot = dict(before)
    age, new = cr.observed_age(before, "g", "a" * 40, 100)      # clock went backwards
    assert age == 0
    assert before == snapshot                                    # not mutated
    assert new is not before
    assert new["h"] == ("c" * 40, 1)                             # other goals carried over


# ------------------------------------------------------------------------- parse_claim / build_claim


def test_parse_claim_round_trip_and_refusals(world):
    sha = world.claim(holder="alice", run="run-9", acquired_at=1234, lease_seconds=900,
                      renewed_at=1300)
    assert HEX40.fullmatch(sha)
    body = git(world.clones[0], "cat-file", "commit", sha).split("\n\n", 1)[1]
    c = cr.parse_claim(body)
    assert c["holder"] == "alice" and c["run"] == "run-9" and c["host"] == "host"
    assert c["acquired_at"] == "1234" and c["renewed_at"] == "1300"
    assert c["lease_seconds"] == 900 and re.fullmatch(r"[0-9a-f]{32}", c["nonce"])
    assert cr.parse_claim(body + "future-key: whatever\n")["holder"] == "alice"   # unknown key ok
    without_run = "\n".join(l for l in body.splitlines() if not l.startswith("run:"))
    with pytest.raises(ValueError):
        cr.parse_claim(without_run)
    with pytest.raises(ValueError):
        cr.parse_claim(body.replace("lease_seconds: 900", "lease_seconds: soon"))
    with pytest.raises(ValueError):
        cr.parse_claim("")


def test_build_claim_is_parentless_empty_tree_and_fsck_clean(world):
    sha = world.claim()
    assert HEX40.fullmatch(sha)
    clone = world.clones[0]
    assert git(clone, "cat-file", "-p", sha).count("parent ") == 0
    assert git(clone, "rev-parse", sha + "^{tree}") == cr.EMPTY_TREE
    # identity is explicit: the author is the fixed sigma identity, not the host's
    assert "author sigma <sigma@localhost>" in git(clone, "cat-file", "commit", sha)
    assert cr.create(clone, world.remote, "g1", sha).kind == cr.WON
    git(world.root / "remote.git", "fsck", "--no-dangling")


def test_build_claim_identity_does_not_depend_on_host_config(world, monkeypatch):
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(k)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    sha = world.claim()
    assert "committer sigma <sigma@localhost>" in git(world.clones[0], "cat-file", "commit", sha)


def test_build_claim_identical_inputs_differ(world):
    a = world.claim(acquired_at=1000, renewed_at=1000)
    b = world.claim(acquired_at=1000, renewed_at=1000)
    assert a != b          # the nonce: commit-tree is deterministic without it (research M-2)


@pytest.mark.parametrize("kwargs", [
    {"holder": "a\nb"}, {"host": ""}, {"run": "x\x00"}, {"lease_seconds": "1800"},
    {"lease_seconds": 0}, {"lease_seconds": True}])
def test_build_claim_refuses_malformed_fields(world, kwargs):
    base = dict(holder="h", host="host", run="r", acquired_at=1, lease_seconds=60)
    base.update(kwargs)
    with pytest.raises(ValueError, match="REFUSED"):
        cr.build_claim(world.clones[0], base["holder"], base["host"], base["run"],
                       base["acquired_at"], base["lease_seconds"])


def test_lease_seconds_has_no_default():
    import inspect
    p = inspect.signature(cr.build_claim).parameters["lease_seconds"]
    assert p.default is inspect.Parameter.empty                  # R-10


# ------------------------------------------------------------------------- classify (acceptance 3)

X, Y, N = "a" * 40, "b" * 40, "c" * 40      # leased, somebody else's, ours
REF = "refs/sigma/claims/g"


def _p(flag, summary, reason=""):
    return "To /r.git\n%s\t%s\t%s\nDone\n" % (flag, summary, reason)


CLASSIFY = [
    # id, kwargs, expected kind, detail fragment ("" = any)
    ("new-ref", dict(rc=0, porcelain=_p("*", N + ":" + REF, "[new reference]"), tip_after=N,
                     leased_sha="", our_sha=N), "WON", ""),
    ("forced", dict(rc=0, porcelain=_p("+", N + ":" + REF, "[forced update]"), tip_after=N,
                    leased_sha=X, our_sha=N), "WON", ""),
    ("deleted", dict(rc=0, porcelain=_p("-", ":" + REF, "[deleted]"), tip_after="",
                     leased_sha=X, our_sha=""), "WON", ""),
    ("up-to-date", dict(rc=0, porcelain=_p("=", N + ":" + REF, "[up to date]"), tip_after=N,
                        leased_sha=X, our_sha=N), "REFUSED", "up-to-date no-op"),
    ("stale-info-tip-moved", dict(rc=1, porcelain=_p("!", N + ":" + REF, "[rejected] (stale info)"),
                                  tip_after=Y, leased_sha=X, our_sha=N), "LOST_RACE", ""),
    ("stale-create-target-exists", dict(rc=1, porcelain=_p("!", N + ":" + REF,
                                                           "[rejected] (stale info)"),
                                        tip_after=Y, leased_sha="", our_sha=N), "LOST_RACE", ""),
    ("remote-rejected-tip-moved", dict(rc=1, porcelain=_p("!", N + ":" + REF,
                                                          "[remote rejected] (failed to update ref)"),
                                       tip_after=Y, leased_sha=X, our_sha=N), "LOST_RACE", ""),
    ("remote-rejected-tip-unmoved", dict(rc=1, porcelain=_p("!", N + ":" + REF,
                                                            "[remote rejected] (failed to update ref)"),
                                         tip_after=X, leased_sha=X, our_sha=N), "REFUSED", "unchanged"),
    ("hook-declined", dict(rc=1, porcelain=_p("!", N + ":" + REF,
                                              "[remote rejected] (pre-receive hook declined)"),
                           tip_after=X, leased_sha=X, our_sha=N), "REFUSED", "unchanged"),
    # Plan-review refinement 4: a rejected CREATE whose target is still ABSENT at the following
    # ls-remote (a concurrent lock that released, a case-fold collision, ...). The ref did not move
    # to anyone else's sha, so this is NOT a race win and NOT proof somebody else holds the goal.
    ("rejected-create-tip-absent", dict(rc=1, porcelain=_p("!", N + ":" + REF,
                                                          "[remote rejected] (failed to update ref)"),
                                       tip_after="", leased_sha="", our_sha=N), "REFUSED", "unchanged"),
    ("delete-of-already-deleted", dict(rc=1, porcelain=_p("!", "(delete):" + REF,
                                                         "[rejected] (stale info)"),
                                       tip_after="", leased_sha=X, our_sha=""), "LOST_RACE", ""),
    ("exit-128", dict(rc=128, porcelain="",
                      stderr="fatal: Could not read from remote repository.\n", tip_after="",
                      leased_sha="", our_sha=N), "OUTAGE", ""),
    ("not-a-repository", dict(rc=128, porcelain="",
                              stderr="fatal: '/x' does not appear to be a git repository\n",
                              leased_sha="", our_sha=N), "OUTAGE", ""),
    ("timeout", dict(rc=-9, porcelain="", timed_out=True, leased_sha="", our_sha=N), "OUTAGE", ""),
    ("empty-rc0", dict(rc=0, porcelain="", leased_sha="", our_sha=N), "OUTAGE", ""),
    ("garbage-rc0", dict(rc=0, porcelain="banana\n", stderr="???", leased_sha="", our_sha=N),
     "OUTAGE", ""),
    ("garbage-looks-like-win-in-prose", dict(rc=0, porcelain="", stderr="[new reference] won!",
                                             leased_sha="", our_sha=N), "OUTAGE", ""),
    ("claimed-win-tip-disagrees", dict(rc=0, porcelain=_p("*", N + ":" + REF, "[new reference]"),
                                       tip_after=Y, leased_sha="", our_sha=N), "OUTAGE", "tip"),
    ("claimed-win-tip-unreadable", dict(rc=0, porcelain=_p("*", N + ":" + REF, "[new reference]"),
                                        tip_known=False, leased_sha="", our_sha=N), "OUTAGE", ""),
    ("claimed-delete-tip-still-there", dict(rc=0, porcelain=_p("-", ":" + REF, "[deleted]"),
                                            tip_after=X, leased_sha=X, our_sha=""), "OUTAGE", ""),
    ("claimed-win-rc-nonzero", dict(rc=1, porcelain=_p("*", N + ":" + REF, "[new reference]"),
                                    tip_after=N, leased_sha="", our_sha=N), "OUTAGE", ""),
    ("rejection-tip-unreadable", dict(rc=1, porcelain=_p("!", N + ":" + REF,
                                                         "[rejected] (stale info)"),
                                      tip_known=False, leased_sha=X, our_sha=N), "OUTAGE", ""),
    ("rejection-but-tip-is-ours", dict(rc=1, porcelain=_p("!", N + ":" + REF,
                                                          "[remote rejected] (failed to update ref)"),
                                       tip_after=N, leased_sha=X, our_sha=N), "OUTAGE", ""),
]


@pytest.mark.parametrize("name,kw,kind,fragment", CLASSIFY, ids=[c[0] for c in CLASSIFY])
def test_classify_table(name, kw, kind, fragment):
    args = dict(rc=0, porcelain="", stderr="", timed_out=False, tip_after="", tip_known=True,
                leased_sha="", our_sha="")
    args.update(kw)
    out = cr.classify(**args)
    assert out.kind == getattr(cr, kind), (name, out)
    assert fragment in out.detail
    assert "ssh://" not in out.detail and "/r.git" not in out.detail   # no remote URL in detail


def test_classify_never_wins_on_a_rejected_create_with_absent_tip():
    """Refinement 4, stated on its own: not a win, not a race loss, never retried as either."""
    out = cr.classify(rc=1, porcelain=_p("!", N + ":" + REF, "[remote rejected] (failed to update ref)"),
                      stderr="remote: error: cannot lock ref: reference already exists",
                      timed_out=False, tip_after="", tip_known=True, leased_sha="", our_sha=N)
    assert out.kind in (cr.REFUSED, cr.OUTAGE)
    assert out.kind != cr.WON and out.kind != cr.LOST_RACE


def test_closed_set_of_kinds():
    assert {cr.WON, cr.LOST_RACE, cr.OUTAGE, cr.REFUSED} == {"WON", "LOST_RACE", "OUTAGE", "REFUSED"}


# ------------------------------------------------------------------------- guarded verbs (acceptance 2, 4)


def test_create_staged_stale_view_loses_race(world):
    a, b = world.claim(0), world.claim(1)
    assert world.tip("g") == ""                       # both read the ref as absent
    first = cr.create(world.clones[0], world.remote, "g", a)
    assert first.kind == cr.WON and first.sha == a
    second = cr.create(world.clones[1], world.remote, "g", b)    # B still believes "absent"
    assert second.kind == cr.LOST_RACE
    assert second.sha == a
    assert world.tip("g") == a                        # the remote still holds A


def test_reclaim_staged_stale_view_loses_race(world):
    x = world.claim(0, run="x")
    assert cr.create(world.clones[0], world.remote, "g", x).kind == cr.WON
    # both reclaimers observed X as stale; each builds its own claim
    r1, r2 = world.claim(0, holder="r1"), world.claim(1, holder="r2")
    won = cr.reclaim(world.clones[0], world.remote, "g", x, r1)
    assert won.kind == cr.WON and won.sha == r1
    lost = cr.reclaim(world.clones[1], world.remote, "g", x, r2)
    assert lost.kind == cr.LOST_RACE and lost.sha == r1
    assert world.tip("g") == r1


def test_stale_delete_loses_and_ref_survives(world):
    x = world.claim(0, run="x")
    assert cr.create(world.clones[0], world.remote, "g", x).kind == cr.WON
    y = world.claim(0, run="y")
    assert cr.renew(world.clones[0], world.remote, "g", x, y).kind == cr.WON   # holder renews X -> Y
    stale = cr.delete(world.clones[1], world.remote, "g", x)    # releaser still has the pre-renewal sha
    assert stale.kind == cr.LOST_RACE and stale.sha == y
    assert world.tip("g") == y                                  # the ref SURVIVES
    fresh = cr.delete(world.clones[1], world.remote, "g", y)
    assert fresh.kind == cr.WON and world.tip("g") == ""


def test_double_delete_loses_cleanly(world):
    x = world.claim(0)
    assert cr.create(world.clones[0], world.remote, "g", x).kind == cr.WON
    assert cr.delete(world.clones[0], world.remote, "g", x).kind == cr.WON
    again = cr.delete(world.clones[1], world.remote, "g", x)
    assert again.kind == cr.LOST_RACE              # never WON on absence
    assert world.tip("g") == ""


def test_delete_requires_an_expected_sha(world):
    for bad in ("", None, "abc", "z" * 40, X.upper()):
        with pytest.raises(ValueError, match="REFUSED"):
            cr.delete(world.clones[0], world.remote, "g", bad)
    for bad in ("", "nope"):
        with pytest.raises(ValueError, match="REFUSED"):
            cr.renew(world.clones[0], world.remote, "g", bad, X)
        with pytest.raises(ValueError, match="REFUSED"):
            cr.reclaim(world.clones[0], world.remote, "g", bad, X)


def test_renew_by_holder_wins_and_stale_renew_loses(world):
    x = world.claim(0, run="x")
    assert cr.create(world.clones[0], world.remote, "g", x).kind == cr.WON
    y = world.claim(0, run="y")
    won = cr.renew(world.clones[0], world.remote, "g", x, y)
    assert won.kind == cr.WON and world.tip("g") == y
    z = world.claim(1, run="z")
    lost = cr.renew(world.clones[1], world.remote, "g", x, z)   # stale expected
    assert lost.kind == cr.LOST_RACE and lost.sha == y
    assert world.tip("g") == y                                  # did not clobber


def test_repush_of_held_sha_is_refused_not_won(world):
    x = world.claim(0, run="x")
    assert cr.create(world.clones[0], world.remote, "g", x).kind == cr.WON
    y = world.claim(0, run="y")
    assert cr.renew(world.clones[0], world.remote, "g", x, y).kind == cr.WON
    # push the sha the remote ALREADY holds, under a stale lease: git exits 0 "up to date"
    out = cr.renew(world.clones[0], world.remote, "g", x, y)
    assert out.kind == cr.REFUSED and "up-to-date no-op" in out.detail
    assert out.kind != cr.WON


def test_missing_remote_is_outage(world, tmp_path):
    out = cr.create(world.clones[0], str(tmp_path / "does-not-exist.git"), "g", world.claim(0))
    assert out.kind == cr.OUTAGE


def test_timeout_is_outage_and_kills_process_group(world, tmp_path):
    """The DEFAULT runner (Popen + os.killpg), called with a small timeout by keyword, on a shell
    whose grandchild would outlive a plain kill of the direct child."""
    pidfile = tmp_path / "grandchild.pid"
    script = "sleep 60 & echo $! > %s; wait" % pidfile
    t0 = time.monotonic()
    res = cr._default_runner(str(tmp_path), ["sh", "-c", script], dict(os.environ), timeout=1.0)
    assert time.monotonic() - t0 < 30
    rc, _out, _err, timed_out = res
    assert timed_out is True
    pid = int(pidfile.read_text().strip())
    deadline = time.monotonic() + 5
    alive = True
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
            break
        time.sleep(0.05)
    if alive:
        os.kill(pid, signal.SIGKILL)         # do not leak the sleeper if the assertion below fails
    assert not alive, "the grandchild survived the timeout: only the direct child was killed"

    # and the same default runner, reached through a verb, classifies the timeout as OUTAGE
    def slow(cwd, argv, env, timeout):
        return cr._default_runner(cwd, ["sh", "-c", "sleep 60 & wait"], env, timeout=timeout)
    out = cr.create(world.clones[0], world.remote, "g", world.claim(0), runner=slow, timeout=0.5)
    assert out.kind == cr.OUTAGE and world.tip("g") == ""


def test_injected_runner_that_times_out_is_outage(world):
    def timed_out(cwd, argv, env, timeout):
        return (-9, "", "", True)
    out = cr.create(world.clones[0], world.remote, "g", world.claim(0), runner=timed_out)
    assert out.kind == cr.OUTAGE


def test_default_runner_refuses_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(cr, "_is_windows", lambda: True)
    with pytest.raises(RuntimeError, match="REFUSED"):
        cr._default_runner(str(tmp_path), ["git", "--version"], dict(os.environ), timeout=5)


def test_hook_decline_is_refused_not_lost_race(world):
    hook = pathlib.Path(world.remote) / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'no claims today' >&2\nexit 1\n")
    hook.chmod(0o755)
    out = cr.create(world.clones[0], world.remote, "g", world.claim(0))
    assert out.kind == cr.REFUSED
    assert out.kind != cr.LOST_RACE and world.tip("g") == ""


def test_directory_file_conflict_is_refused_not_lost_race(world):
    assert cr.create(world.clones[0], world.remote, "dd", world.claim(0)).kind == cr.WON
    # R-4 blocks `dd/x` at the public surface, so go to _push with a caller-supplied raw ref
    raw = cr.NAMESPACE + "dd/x"
    out = cr._push(world.clones[1], world.remote, raw, "", world.claim(1), runner=None,
                   timeout=cr.DEFAULT_TIMEOUT)
    assert out.kind == cr.REFUSED
    assert out.kind != cr.LOST_RACE


def test_retried_delete_after_a_demoted_win_is_a_lost_race(world):
    """Refinement 3. The post-win ls-remote can demote a REAL win to OUTAGE if another claimant
    re-creates the ref in the instant between our push and our measurement. The retry is safe: the
    lease names the sha we meant to delete, so it can only ever lose, never remove the new claim."""
    x = world.claim(0, run="x")
    assert cr.create(world.clones[0], world.remote, "g", x).kind == cr.WON
    intruder = world.claim(1, run="intruder")
    state = {"armed": True}

    def runner(cwd, argv, env, timeout):
        res = cr._default_runner(cwd, argv, env, timeout=timeout)
        if state["armed"] and argv[1] == "push":              # right after our delete lands ...
            state["armed"] = False
            assert cr.create(world.clones[1], world.remote, "g", intruder).kind == cr.WON  # ... re-create
        return res
    demoted = cr.delete(world.clones[0], world.remote, "g", x, runner=runner)
    assert demoted.kind == cr.OUTAGE                           # a real delete, reported as unconfirmed
    retry = cr.delete(world.clones[0], world.remote, "g", x)
    assert retry.kind == cr.LOST_RACE and retry.sha == intruder
    assert world.tip("g") == intruder                          # the new claim was never touched


def test_every_push_carries_explicit_lease_and_porcelain(world):
    seen = []

    def spy(cwd, argv, env, timeout):
        seen.append(list(argv))
        return cr._default_runner(cwd, argv, env, timeout=timeout)
    x = world.claim(0, run="x")
    y = world.claim(0, run="y")
    z = world.claim(0, run="z")
    ref = cr.claim_ref("g")
    cr.create(world.clones[0], world.remote, "g", x, runner=spy)
    cr.renew(world.clones[0], world.remote, "g", x, y, runner=spy)
    cr.reclaim(world.clones[0], world.remote, "g", y, z, runner=spy)
    cr.delete(world.clones[0], world.remote, "g", z, runner=spy)
    cr.delete(world.clones[1], world.remote, "g", z, runner=spy)       # a losing one too
    pushes = [a for a in seen if a[:2] == ["git", "push"]]
    assert len(pushes) == 5
    expected = ["", x, y, z, z]
    for argv, exp in zip(pushes, expected):
        assert "--force-with-lease=%s:%s" % (ref, exp) in argv
        assert "--porcelain" in argv
    for argv in seen:
        assert "--force" not in argv and "-f" not in argv
        assert not any(a.startswith("--force") and not a.startswith("--force-with-lease=")
                       for a in argv)


def test_read_tips_and_read_all(world):
    a, b = world.claim(0, holder="alice"), world.claim(0, holder="bob")
    assert cr.read_tips(world.clones[1], world.remote) == {}
    cr.create(world.clones[0], world.remote, "g1", a)
    cr.create(world.clones[0], world.remote, "g2", b)
    assert cr.read_tips(world.clones[1], world.remote) == {"g1": a, "g2": b}
    everything = cr.read_all(world.clones[1], world.remote)
    assert set(everything) == {"g1", "g2"}
    assert everything["g1"][0] == a and everything["g1"][1]["holder"] == "alice"
    cr.delete(world.clones[0], world.remote, "g1", a)
    assert set(cr.read_all(world.clones[1], world.remote)) == {"g2"}   # a released claim is gone


def test_read_tips_raises_on_an_unreadable_remote_rather_than_returning_empty(world, tmp_path):
    with pytest.raises(cr.ClaimOutage) as e:
        cr.read_tips(world.clones[0], str(tmp_path / "nope.git"))
    assert e.value.outcome.kind == cr.OUTAGE
    with pytest.raises(cr.ClaimOutage):
        cr.read_all(world.clones[0], str(tmp_path / "nope.git"))


# ------------------------------------------------------------------------- hermeticity (C-6)


def test_tests_are_hermetic(world, tmp_path):
    assert os.environ["GIT_CONFIG_GLOBAL"] == os.devnull
    assert os.environ["GIT_CONFIG_SYSTEM"] == os.devnull
    root = tmp_path.resolve()
    assert pathlib.Path(world.remote).resolve().parent == root
    assert pathlib.Path(world.remote).is_absolute() and "://" not in world.remote
    for clone in world.clones:
        assert clone.resolve().parent == root
        assert git(clone, "remote") == ""          # a clone has no remote configured at all

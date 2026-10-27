# SPDX-License-Identifier: MIT
"""#2076: a Claude Code cloud container is its own machine, and its writer identity says so.

THE DEFECT. `_host_token` hashed the hostname, and every cloud container shares one. Low container
pids repeat, so different runs ended up with the same writer `<actor>-<host>.<pid>`: two containers
running at once read each other's claim as their own, and `pid_alive` was asked about a pid that means
nothing in the asking container.

The token is unique per container in a cloud session (`CLAUDE_CODE_REMOTE` true or 1) and is the same
plain hostname hash as before on a laptop. `ledger.host_alias` still wins in both.

THE GESTURE THE DOCS GIVE (docs/cloud-sessions.md, "The team ledger from a cloud container"):
`python3 -m pytest tests/test_ledger_cloud_claims.py`.
"""
import hashlib
import importlib.util
import pathlib
import platform

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _mod("ledger")
HOST = "container-host"
PLAIN = hashlib.sha256(HOST.encode()).hexdigest()[:8]


@pytest.fixture(autouse=True)
def _pinned_host(monkeypatch):
    monkeypatch.setattr(platform, "node", lambda: HOST)


def _container(monkeypatch, tmp_path, name):
    """Make this process look like container `name`: its own id file, same hostname."""
    monkeypatch.setattr(ledger, "_container_id_path", lambda: tmp_path / f"{name}.id")


def test_a_laptop_token_is_the_plain_hostname_hash(monkeypatch, tmp_path):
    _container(monkeypatch, tmp_path, "laptop")
    assert ledger._host_token({}) == PLAIN
    assert not (tmp_path / "laptop.id").exists(), "a laptop must never write a container id"


@pytest.mark.parametrize("value", ["true", "1", "True"])
def test_two_cloud_containers_with_one_hostname_get_different_tokens(monkeypatch, tmp_path, value):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", value)
    _container(monkeypatch, tmp_path, "a")
    token_a = ledger._host_token({})
    _container(monkeypatch, tmp_path, "b")
    token_b = ledger._host_token({})
    assert token_a != token_b
    assert PLAIN not in (token_a, token_b)
    assert len(token_a) == len(token_b) == 8


def test_one_container_keeps_one_token_across_processes(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    _container(monkeypatch, tmp_path, "a")
    first = ledger._host_token({})
    assert ledger._host_token({}) == first
    # a new process in the same container reads the same persisted id
    assert ledger._host_token({"ledger": {}}) == first


def test_the_same_pid_in_two_containers_is_two_writers(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    cfg = {"ledger": {"actor": "dana"}}
    _container(monkeypatch, tmp_path, "a")
    writer_a = ledger.my_writer(cfg)
    _container(monkeypatch, tmp_path, "b")
    writer_b = ledger.my_writer(cfg)
    assert writer_a != writer_b
    assert writer_a.rpartition(".")[2] == writer_b.rpartition(".")[2]     # the pid really is the same


def test_a_host_alias_is_kept_but_stays_unique_per_container_in_a_cloud_session(monkeypatch, tmp_path):
    cfg = {"ledger": {"host_alias": "pool7"}}
    assert ledger._host_token(cfg) == "pool7", "a laptop keeps the alias as it stands"
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    _container(monkeypatch, tmp_path, "a")
    token_a = ledger._host_token(cfg)
    _container(monkeypatch, tmp_path, "b")
    token_b = ledger._host_token(cfg)
    assert token_a.startswith("pool7") and token_b.startswith("pool7")
    assert token_a != token_b and token_a.isalnum()


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_only_true_or_1_is_a_cloud_session(monkeypatch, tmp_path, value):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", value)
    _container(monkeypatch, tmp_path, "a")
    assert ledger._host_token({}) == PLAIN


def test_an_unwritable_id_path_falls_back_to_a_stable_token(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    blocked = tmp_path / "file"
    blocked.write_text("x")
    monkeypatch.setattr(ledger, "_container_id_path", lambda: blocked / "nested" / "id")
    first = ledger._host_token({})
    assert first == ledger._host_token({}) and len(first) == 8


def test_a_damaged_id_file_is_not_trusted(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    _container(monkeypatch, tmp_path, "a")
    (tmp_path / "a.id").write_text("../../etc/passwd")
    host = ledger._host_token({})
    assert len(host) == 8 and host.isalnum()
    assert ledger.container_id() == ledger._kernel_container_id(), "a damaged id must not be used as the id"
    assert "passwd" not in ledger.container_id()


def test_processes_racing_the_first_call_agree_on_one_container_id(monkeypatch, tmp_path):
    """A reader must never see the id file half-made and fall back to a different id."""
    import threading
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    for round_no in range(20):
        path = tmp_path / f"race{round_no}.id"
        monkeypatch.setattr(ledger, "_container_id_path", lambda p=path: p)
        got, barrier = [], threading.Barrier(12)

        def work():
            barrier.wait(timeout=10)
            got.append(ledger.container_id())

        threads = [threading.Thread(target=work) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(set(got)) == 1, (round_no, set(got))
        assert not list(tmp_path.glob(f"race{round_no}.id.*")), "a temporary file was left behind"


# ------------------------------------------------------------------ AC-6: reclaim confirms with origin
#
# A claim written on another machine looks stale here whenever this ledger copy is old. The sweep releases
# it only once a FRESH read of origin's ledger branch confirms it is still expired, and a read that fails
# releases nothing. Real git: a bare origin and two clones that share one login.

import json
import subprocess
import time

HOUR = 3600


def _git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr or proc.stdout}"
    return proc.stdout.strip()


def _load(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sync = _load("sync")
loop = _load("loop")


@pytest.fixture
def world(tmp_path):
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

    def clone(name):
        repo = tmp_path / name
        subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
            _git(repo, "config", k, v)
        cfg = {"discovery": {"source": "github", "github": {"repo": "acme/widget"}},
               "ledger": {"enabled": True, "actor": "dana", "host_alias": name, "lease": {"ttl_hours": 12}}}
        base = repo / ".sdlc"
        (base / "state").mkdir(parents=True)
        (base / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        return repo, base, cfg

    return origin, clone


class _Gh:
    """A GitHubSource transport that records nothing and answers nothing: only the sweep's decision is under test."""
    def __call__(self, args):
        return "[]"


def _sweep(base, cfg, monkeypatch):
    released = []
    original_load = loop._load
    monkeypatch.setattr(loop, "_load", lambda name: sync if name == "sync" else original_load(name))
    monkeypatch.setattr(loop, "_release", lambda sdlc, source, goal, reason="", claim_actor=None,
                        claim_run=None, **k: released.append((goal, claim_actor, claim_run)))
    source = loop.sources.GitHubSource(cfg, run=_Gh(), sdlc_dir=base)
    got = loop._auto_reclaim_stale_claims(base, source, cfg)
    return released, got


def _claim_elsewhere(world, run_id="s-9"):
    origin, clone = world
    repo_a, base_a, cfg_a = clone("boxa")
    ledger.append(base_a, cfg_a, "claimed", "42", now=time.time() - 13 * HOUR, run_id=run_id)
    assert "NOT published" not in (sync.publish_entry(base_a, cfg_a, "claim") or "")
    repo_b, base_b, cfg_b = clone("boxb")
    sync.init(base_b, cfg_b)
    return (repo_a, base_a, cfg_a), (repo_b, base_b, cfg_b)


def test_a_stale_claim_from_another_machine_is_released_once_origin_confirms_it(world, monkeypatch):
    a, b = _claim_elsewhere(world)
    released, got = _sweep(b[1], b[2], monkeypatch)
    assert released == [("42", "dana", "s-9")] and got == frozenset({"42"})


def test_a_claim_renewed_on_origin_is_not_released_by_a_machine_with_a_stale_copy(world, monkeypatch):
    """The adopter incident: the holder's renewal is on origin; this machine's copy never saw it."""
    a, b = _claim_elsewhere(world)
    ledger.append(a[1], a[2], "lease-renew", "42", run_id="s-9")
    assert "NOT published" not in (sync.publish_entry(a[1], a[2], "keep-alive") or "")
    assert set(ledger.expired_claims(ledger.read_all(b[1]), ttl_seconds=12 * HOUR)) == {"42"}, \
        "precondition: the local copy still reads the claim as expired"
    released, got = _sweep(b[1], b[2], monkeypatch)
    assert released == [] and got == frozenset()


def test_a_claim_finished_on_origin_is_not_released_either(world, monkeypatch):
    a, b = _claim_elsewhere(world)
    ledger.append(a[1], a[2], "done", "42", run_id="s-9")
    sync.publish_entry(a[1], a[2], "done")
    released, _ = _sweep(b[1], b[2], monkeypatch)
    assert released == []


def test_a_failed_read_of_origin_releases_nothing(world, monkeypatch, capsys):
    a, b = _claim_elsewhere(world)
    _git(b[0], "remote", "set-url", "origin", str(b[0].parent / "nowhere.git"))
    released, got = _sweep(b[1], b[2], monkeypatch)
    assert released == [] and got == frozenset()
    assert "not released" in capsys.readouterr().err


def test_no_ledger_branch_to_read_releases_nothing(world, monkeypatch):
    origin, clone = world
    repo, base, cfg = clone("boxb")
    ledger.append(base, {"ledger": dict(cfg["ledger"], host_alias="boxa")}, "claimed", "42",
                  now=time.time() - 13 * HOUR)
    released, _ = _sweep(base, cfg, monkeypatch)
    assert released == []


def test_a_stale_claim_from_this_machine_is_released_without_asking_origin(world, monkeypatch):
    origin, clone = world
    repo, base, cfg = clone("boxb")
    ledger.append(base, cfg, "claimed", "42", now=time.time() - 13 * HOUR)

    def boom(*a, **k):
        raise AssertionError("origin must not be read for a claim written here")

    monkeypatch.setattr(sync, "remote_expired", boom)
    released, _ = _sweep(base, cfg, monkeypatch)
    assert [r[0] for r in released] == ["42"]


def test_origin_is_asked_once_per_sweep_and_not_again_within_the_cooldown(world, monkeypatch):
    a, b = _claim_elsewhere(world)
    ledger.append(a[1], a[2], "claimed", "43", now=time.time() - 14 * HOUR, run_id="s-9")
    sync.publish_entry(a[1], a[2], "second claim")
    sync.pull(b[1], b[2])
    asked = []
    real = sync.remote_expired
    monkeypatch.setattr(sync, "remote_expired", lambda *x, **k: (asked.append(1), real(*x, **k))[1])
    released, _ = _sweep(b[1], b[2], monkeypatch)
    assert sorted(r[0] for r in released) == ["42", "43"] and len(asked) == 1, "two goals, one fetch"


def test_a_refusal_is_not_retried_inside_the_cooldown(world, monkeypatch):
    a, b = _claim_elsewhere(world)
    _git(b[0], "remote", "set-url", "origin", str(b[0].parent / "nowhere.git"))
    asked = []
    real = sync.remote_expired
    monkeypatch.setattr(sync, "remote_expired", lambda *x, **k: (asked.append(1), real(*x, **k))[1])
    _sweep(b[1], b[2], monkeypatch)
    _git(b[0], "remote", "set-url", "origin", str(world[0]))
    released, _ = _sweep(b[1], b[2], monkeypatch)
    assert len(asked) == 1 and released == [], "the cooldown must hold for a failed read too"


def test_the_release_names_the_claim_origin_shows_not_the_stale_local_one(world, monkeypatch):
    """Origin has a newer claim, by another actor, that has also expired: the release must be aimed at
    THAT claim's actor and run, or it strips the wrong holder's lease."""
    origin, clone = world
    a = clone("boxa")
    ledger.append(a[1], a[2], "claimed", "42", now=time.time() - 13.5 * HOUR, run_id="s-1")
    sync.publish_entry(a[1], a[2], "claim")
    b = clone("boxb")
    sync.init(b[1], b[2])                                       # b now holds dana's claim, run s-1
    c = clone("boxc")
    c[2]["ledger"]["actor"] = "eve"
    ledger.append(c[1], c[2], "claimed", "42", now=time.time() - 13 * HOUR, run_id="s-7")
    sync.publish_entry(c[1], c[2], "claim")                    # origin: eve re-claimed, also expired
    assert ledger.expired_claims(ledger.read_all(b[1]), ttl_seconds=12 * HOUR)["42"][0] == "dana"
    released, _ = _sweep(b[1], b[2], monkeypatch)
    assert released == [("42", "eve", "s-7")], released

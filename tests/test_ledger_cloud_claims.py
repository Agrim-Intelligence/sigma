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
import os
import pathlib
import platform
import stat
import sys
import types

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


# ------------------------------------------------------------------ #2081: the id does not follow TMPDIR
#
# THE DEFECT. `_container_id_path` asked `tempfile.gettempdir()`, which follows `TMPDIR`, and a hook, a
# sandboxed shell and a normal shell of ONE container can disagree about `TMPDIR`: each then wrote its own id
# file, so one container carried several host tokens and a holder's own lease renewal read as another
# machine's. On POSIX the id now lives in a fixed machine-level directory, `/tmp` then `/var/tmp`, read from
# every candidate before one is written. A non-POSIX platform keeps the temporary directory on purpose (D-1b),
# so "does not depend on TMPDIR" is a POSIX / cloud-container guarantee.
#
# THE GESTURE THE DOCS GIVE (docs/cloud-sessions.md): the four-file command, flagless. The children below run
# in subprocesses because `tempfile` CACHES its answer after the first call, so TMPDIR cannot be changed
# mid-process and seen. Every child that computes a token first points `CONTAINER_ID_DIRS` at a scratch
# directory, so a test never writes the developer's real /tmp id (on the unfixed code that assignment is just a
# new attribute nobody reads, and the child writes under its own TMPDIR instead: harmless).

_CHILD_PRELUDE = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ledger", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
if len(sys.argv) > 2 and sys.argv[2]:
    m.CONTAINER_ID_DIRS = (sys.argv[2],)
"""


def _py(code, pinned="", tmpdir=None, drop_tmpdir=False, extra_env=None, timeout=120):
    """Run `code` (after loading ledger.py as `m`) in a fresh interpreter with CLAUDE_CODE_REMOTE=true and the
    given TMPDIR; return stdout. `TEMP`/`TMP` are always removed so TMPDIR is the only thing that varies."""
    env = dict(os.environ, CLAUDE_CODE_REMOTE="true")
    for name in ("TEMP", "TMP"):
        env.pop(name, None)
    if drop_tmpdir:
        env.pop("TMPDIR", None)
    elif tmpdir is not None:
        env["TMPDIR"] = str(tmpdir)
    env.update(extra_env or {})
    proc = subprocess.run([sys.executable, "-c", _CHILD_PRELUDE + code, str(S / "ledger.py"), str(pinned)],
                          capture_output=True, text=True, env=env, timeout=timeout)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _id_name():
    return f"sigma-container-{os.getuid() if hasattr(os, 'getuid') else 0}.id"


def _put_id(directory, value):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / _id_name()).write_text(value, encoding="utf-8")


ID_A, ID_B = "a" * 32, "b" * 32


@pytest.mark.skipif(os.name != "posix", reason="AC-3 is a POSIX guarantee; other platforms keep the temp directory")
def test_the_container_id_path_is_the_same_whatever_tmpdir_is(tmp_path):
    (tmp_path / "a").mkdir()          # tempfile ignores a TMPDIR that does not exist, so make them real
    (tmp_path / "b").mkdir()
    code = "print(m._container_id_path())"
    got = [_py(code, tmpdir=tmp_path / "a"), _py(code, tmpdir=tmp_path / "b"), _py(code, drop_tmpdir=True),
           _py(code, tmpdir="/nonexistent")]
    assert len(set(got)) == 1, f"the id path follows the environment: {got}"
    assert got[0] == str(pathlib.Path("/tmp") / _id_name())


def test_two_processes_with_different_tmpdirs_carry_one_host_token(tmp_path):
    pinned = tmp_path / "pinned"
    for name in ("pinned", "a", "b"):
        (tmp_path / name).mkdir()     # tempfile ignores a TMPDIR that does not exist, so make them real
    code = "print(m._host_token({}), m.container_id())"
    first = _py(code, pinned=pinned, tmpdir=tmp_path / "a")
    second = _py(code, pinned=pinned, tmpdir=tmp_path / "b")
    assert first == second, f"two TMPDIRs, one container, two identities: {first!r} != {second!r}"


def test_every_candidate_directory_is_read_before_one_is_written(monkeypatch, tmp_path):
    """The reader and the writer disagree about which directory they can write: the writer that could not use
    the first wrote the second, and a reader that only looked at the first would write a SECOND id."""
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    _put_id(d2, ID_A)
    monkeypatch.setattr(ledger, "CONTAINER_ID_DIRS", (str(d1), str(d2)))
    assert ledger.container_id() == ID_A
    assert not (d1 / _id_name()).exists(), "an id was written before every candidate had been read"


def test_the_first_candidate_wins_when_two_hold_different_ids(monkeypatch, tmp_path):
    """The order guard: membership ('some candidate holds an id') is not enough, the first one wins."""
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    _put_id(d1, ID_A)
    _put_id(d2, ID_B)
    monkeypatch.setattr(ledger, "CONTAINER_ID_DIRS", (str(d1), str(d2)))
    assert ledger.container_id() == ID_A


def test_an_unwritable_first_candidate_falls_through_to_the_second(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    d2 = tmp_path / "d2"
    d2.mkdir()
    monkeypatch.setattr(ledger, "CONTAINER_ID_DIRS", (str(blocker / "nested"), str(d2)))
    first = ledger.container_id()
    assert first != ledger._kernel_container_id(), "fell back to the kernel id with a writable candidate left"
    assert (d2 / _id_name()).read_text(encoding="utf-8").strip() == first
    assert ledger.container_id() == first


def test_a_patched_seam_consults_only_itself(monkeypatch, tmp_path):
    """Four older tests pin the id to one file by patching `_container_id_path`; a second candidate must not be
    read (a developer's real /var/tmp id) or written when they do."""
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    _put_id(d2, ID_B)
    monkeypatch.setattr(ledger, "CONTAINER_ID_DIRS", (str(d1), str(d2)))
    pinned = tmp_path / "pin" / _id_name()           # the real file name, so an alternate WOULD be found in d2
    monkeypatch.setattr(ledger, "_container_id_path", lambda: pinned)
    got = ledger.container_id()
    assert got != ID_B and pinned.read_text(encoding="utf-8").strip() == got
    assert (d2 / _id_name()).read_text(encoding="utf-8") == ID_B and [p.name for p in d2.iterdir()] == [_id_name()]


def test_a_platform_with_no_pinned_directory_falls_back_without_raising(monkeypatch, tmp_path):
    """What a non-POSIX platform whose temp directory could not be found would hold (D-1b)."""
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.setattr(ledger, "CONTAINER_ID_DIRS", ())
    assert ledger.container_id() == ledger._kernel_container_id()
    host = ledger._host_token({})
    assert len(host) == 8 and all(c in "0123456789abcdef" for c in host)
    assert list(tmp_path.iterdir()) == []


def test_a_writer_in_an_earlier_candidate_wins_after_publishing(monkeypatch, tmp_path):
    """Deterministic: this process cannot write the first candidate, so it publishes into the second; while it
    does, ANOTHER process publishes into the first. The first candidate is the one every reader consults first,
    so only a re-read of ALL candidates, in order, after publishing returns the id the others will read."""
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    monkeypatch.setattr(ledger, "CONTAINER_ID_DIRS", (str(d1), str(d2)))
    real_open, raced = os.open, []

    def spy(path, *a, **k):
        creating = bool(a) and bool(a[0] & os.O_CREAT)          # the publisher's temp-file create, not a READ
        if creating and str(path).startswith(str(d1)):
            raise PermissionError(13, "this process cannot write the first candidate")
        if creating and str(path).startswith(str(d2)) and not raced:
            raced.append(1)
            (d1 / _id_name()).write_text(ID_B, encoding="utf-8")        # the other process, in the first dir
        return real_open(path, *a, **k)

    monkeypatch.setattr(ledger.os, "open", spy)
    got = ledger.container_id()
    monkeypatch.setattr(ledger.os, "open", real_open)
    assert got == ID_B
    own = (d2 / _id_name()).read_text(encoding="utf-8").strip()
    assert own != ID_B and ledger._CONTAINER_ID_RE.fullmatch(own), "this process published its own id in d2"
    assert not list(d1.glob("*.tmp")) and not list(d2.glob("*.tmp"))


def test_a_non_posix_platform_keeps_its_temp_directory():
    """`os.name` is not patched (pathlib reads it); the decision is a pure function of its arguments."""
    def never():
        raise AssertionError("the POSIX branch must not ask tempfile for a directory")

    def boom():
        raise OSError("no temp directory")

    assert ledger._container_id_dirs_for("posix", never) == ("/tmp", "/var/tmp")
    assert ledger._container_id_dirs_for("nt", lambda: "C:/t") == ("C:/t",)
    assert ledger._container_id_dirs_for("nt", boom) == ()


# ---- F4 (review cycle 1): the id file sits in a world-writable directory, so what is AT that name is not trusted
#
# `container_id` is documented "Never raises". `_read_container_id` caught only OSError, so a non-UTF-8 file at the
# path made `_host_token` raise UnicodeDecodeError, and a FIFO at the path HANGED the read for ever (a plain read
# waits for a writer). Now the file is opened without following a symlink and without blocking, trusted only when it
# is a regular file owned by this user, read a few dozen bytes, decoded strictly, and anything else is "no id here":
# the reader goes on to the next candidate, then to the kernel-derived id.


def _first_dir_holds(tmp_path, hostile):
    """Two candidate directories; `hostile(path)` puts something at the FIRST one's id name."""
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    hostile(d1 / _id_name())
    return d1, d2


def _assert_fell_through_to_a_clean_id(got, d2):
    assert ledger._CONTAINER_ID_RE.fullmatch(got), got
    assert got != ID_A and (d2 / _id_name()).read_text(encoding="utf-8").strip() == got, \
        "the hostile file's content was used, or the id was not published in the next candidate"


def test_a_non_utf8_id_file_is_no_id_and_never_raises(monkeypatch, tmp_path):
    d1, d2 = _first_dir_holds(tmp_path, lambda path: path.write_bytes(b"\xff\xfe" + b"a" * 30))
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.setattr(ledger, "CONTAINER_ID_DIRS", (str(d1), str(d2)))
    assert ledger._read_container_id(d1 / _id_name()) is None
    got = ledger.container_id()
    assert ledger._CONTAINER_ID_RE.fullmatch(got) and (d2 / _id_name()).read_text(encoding="utf-8").strip() == got
    assert len(ledger._host_token({})) == 8, "the host token must still be computed"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a POSIX FIFO")
@pytest.mark.parametrize("candidates", ["next-candidate", "only-candidate"])
def test_a_fifo_at_the_id_path_neither_hangs_nor_raises(tmp_path, candidates):
    """A FIFO with no writer blocks a plain `open` for ever, so this runs in a child with a short timeout: a
    regression FAILS (TimeoutExpired) rather than hanging the suite."""
    d1, d2 = _first_dir_holds(tmp_path, os.mkfifo)
    dirs = (str(d1), str(d2)) if candidates == "next-candidate" else (str(d1),)
    out = _py(f"m.CONTAINER_ID_DIRS = {dirs!r}\nprint(m.container_id(), m._kernel_container_id())", timeout=20)
    got, kernel = out.split()
    assert ledger._CONTAINER_ID_RE.fullmatch(got), out
    if candidates == "next-candidate":
        assert (d2 / _id_name()).read_text(encoding="utf-8").strip() == got
    else:
        assert got == kernel, "with the only candidate unusable the id is the kernel-derived one"
    assert stat.S_ISFIFO((d1 / _id_name()).lstat().st_mode), "the hostile file was replaced or consumed"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_symlink_at_the_id_path_is_not_followed(monkeypatch, tmp_path):
    elsewhere = tmp_path / "elsewhere.id"
    elsewhere.write_text(ID_A, encoding="utf-8")                 # a perfectly valid id, behind a link
    d1, d2 = _first_dir_holds(tmp_path, lambda path: path.symlink_to(elsewhere))
    monkeypatch.setattr(ledger, "CONTAINER_ID_DIRS", (str(d1), str(d2)))
    assert ledger._read_container_id(d1 / _id_name()) is None
    _assert_fell_through_to_a_clean_id(ledger.container_id(), d2)


def _fstat_lying_about(target, **changes):
    """An `os.fstat` that reports `changes` (`st_mode`, `st_uid`) for the descriptor that is `target` and the truth
    for every other: the id this process then publishes in the next candidate must still read as its own."""
    real, inode = os.fstat, os.stat(target).st_ino

    def fstat(fd):
        st = real(fd)
        fake = dict(st_mode=st.st_mode, st_uid=st.st_uid, st_ino=st.st_ino)
        return types.SimpleNamespace(**(dict(fake, **changes) if st.st_ino == inode else fake))

    return fstat


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="ownership is a POSIX notion")
def test_a_file_owned_by_another_user_is_not_trusted(monkeypatch, tmp_path):
    """No root needed: the owner the check sees is patched on the descriptor's `fstat` result."""
    d1, d2 = _first_dir_holds(tmp_path, lambda path: path.write_text(ID_A, encoding="utf-8"))
    monkeypatch.setattr(ledger, "CONTAINER_ID_DIRS", (str(d1), str(d2)))
    assert ledger.container_id() == ID_A, "control: the same file, owned by this user, is read"
    with monkeypatch.context() as patched:
        patched.setattr(ledger.os, "fstat", _fstat_lying_about(d1 / _id_name(), st_uid=os.getuid() + 1))
        assert ledger._read_container_id(d1 / _id_name()) is None
        _assert_fell_through_to_a_clean_id(ledger.container_id(), d2)


def test_a_file_that_is_not_a_regular_file_is_not_read(monkeypatch, tmp_path):
    """The mode check on its own: a FIFO with no writer reads as empty (so a FIFO alone cannot prove it), so the
    descriptor's mode is patched on a file that WOULD be accepted as a regular one."""
    d1, d2 = _first_dir_holds(tmp_path, lambda path: path.write_text(ID_A, encoding="utf-8"))
    path = d1 / _id_name()
    assert ledger._read_container_id(path) == ID_A
    with monkeypatch.context() as patched:
        patched.setattr(ledger.os, "fstat", _fstat_lying_about(path, st_mode=stat.S_IFIFO | 0o600))
        assert ledger._read_container_id(path) is None


@pytest.mark.parametrize("content,expected", [(ID_A, ID_A), (ID_A + "\n", ID_A), ("  " + ID_B + "  \n", ID_B),
                                              ("", None), ("a" * 31, None), ("A" * 32, None), ("g" * 32, None),
                                              (ID_A + "x", None), ("a" * 4096, None)])
def test_a_valid_id_is_read_and_everything_else_is_not(tmp_path, content, expected):
    path = tmp_path / _id_name()
    path.write_text(content, encoding="utf-8")
    assert ledger._read_container_id(path) == expected
    assert ledger._read_container_id(tmp_path / "missing.id") is None


def test_the_id_read_is_bounded(monkeypatch, tmp_path):
    """A hostile file can be any size; only a few dozen bytes of it are ever read."""
    path = tmp_path / _id_name()
    path.write_bytes(ID_A.encode() + b"\n" + b"x" * (4 * 1024 * 1024))
    asked, real = [], os.read

    def spy(fd, n):
        asked.append(n)
        return real(fd, n)

    monkeypatch.setattr(ledger.os, "read", spy)
    assert ledger._read_container_id(path) is None
    assert asked and max(asked) <= 256, asked


@pytest.mark.parametrize("case", ["valid", "not-regular", "other-user", "non-utf8"])
def test_every_descriptor_the_id_read_opens_is_closed(monkeypatch, tmp_path, case):
    path = tmp_path / _id_name()
    path.write_bytes(b"\xff" * 32 if case == "non-utf8" else ID_A.encode())
    real_open, real_close, real_fstat = os.open, os.close, os.fstat
    opened, closed = [], []

    def spy_open(p, *a, **k):
        fd = real_open(p, *a, **k)
        opened.append(fd)
        return fd

    def spy_close(fd):
        closed.append(fd)
        return real_close(fd)

    def fstat(fd):
        st = real_fstat(fd)
        if case == "not-regular":
            return types.SimpleNamespace(st_mode=stat.S_IFIFO | 0o600, st_uid=st.st_uid)
        if case == "other-user":
            return types.SimpleNamespace(st_mode=st.st_mode, st_uid=st.st_uid + 1)
        return st

    with monkeypatch.context() as patched:
        patched.setattr(ledger.os, "open", spy_open)
        patched.setattr(ledger.os, "close", spy_close)
        patched.setattr(ledger.os, "fstat", fstat)
        ledger._read_container_id(path)
    assert opened and sorted(opened) == sorted(closed), (case, opened, closed)


# ---- review cycle 2: the sweep of the write sites (G1) and an empty directory tuple (G5e)
#
# The id is published with an exclusive create of a unique temporary name and a HARD LINK onto the real name
# (`link` fails if that name exists, whatever it is, and never follows a symlink there), so nothing is written
# THROUGH a planted symlink or into a planted FIFO. These tests plant both and show it, instead of arguing it.


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
@pytest.mark.parametrize("shape", ["existing", "dangling"])
def test_the_id_writer_never_writes_through_a_planted_symlink(tmp_path, shape):
    d = tmp_path / shape
    d.mkdir()
    target = tmp_path / f"{shape}-target.txt"
    if shape == "existing":
        target.write_text("PRECIOUS DATA\n", encoding="utf-8")
    link = d / _id_name()
    link.symlink_to(target)
    ledger._publish_container_id(link)
    assert os.path.islink(link) and os.readlink(link) == str(target), "the planted symlink was replaced"
    if shape == "existing":
        assert target.read_text(encoding="utf-8") == "PRECIOUS DATA\n", "the id was written through the symlink"
    else:
        assert not target.exists(), "the id created the symlink's target"
    assert [p.name for p in d.iterdir()] == [_id_name()], "a temporary file was left behind"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a POSIX FIFO")
def test_the_id_writer_neither_opens_nor_fills_a_planted_fifo(tmp_path):
    """A plain `open(path, "w")` on a FIFO with no reader blocks for ever, so the writer runs in a child with a
    short timeout: a regression FAILS (TimeoutExpired) instead of hanging the suite."""
    d = tmp_path / "d"
    d.mkdir()
    fifo = d / _id_name()
    os.mkfifo(fifo)
    _py(f"import pathlib\nm._publish_container_id(pathlib.Path({str(fifo)!r}))", timeout=20)
    assert stat.S_ISFIFO(fifo.lstat().st_mode), "the FIFO was replaced"
    fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        assert os.read(fd, 64) == b"", "something was written into the FIFO"
    finally:
        os.close(fd)
    assert [p.name for p in d.iterdir()] == [_id_name()], "a temporary file was left behind"


def test_an_empty_directory_tuple_with_a_pinned_seam_consults_the_seam_alone(monkeypatch, tmp_path):
    """`CONTAINER_ID_DIRS` is `()` where no directory could be found (a non-POSIX platform with no temporary
    directory), and four older tests pin the id to one file by patching `_container_id_path`: the two together raised
    `IndexError` out of `container_id`, which never raises."""
    pinned = tmp_path / "pin" / _id_name()
    monkeypatch.setattr(ledger, "CONTAINER_ID_DIRS", ())
    monkeypatch.setattr(ledger, "_container_id_path", lambda: pinned)
    assert ledger._container_id_candidates() == [pinned]
    got = ledger.container_id()
    assert ledger._CONTAINER_ID_RE.fullmatch(got) and pinned.read_text(encoding="utf-8").strip() == got

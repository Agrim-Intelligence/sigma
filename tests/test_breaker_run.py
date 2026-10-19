"""#853 (slice 4A of #558): `breaker_run.run`, the clean-cwd runner, exercised through a fake CLI.

Real route evidence here is a FAKE `cursor-agent` script on a temp PATH; a real host run is UNMEASURED.
Each control names the break that turns its test red."""
import contextlib
import importlib.util
import os
import pathlib
import signal
import sys
import tempfile
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fake_breaker_support as fake  # noqa: E402

S = pathlib.Path(__file__).resolve().parents[1] / "skills/sigma-loop/scripts"


def load(name):
    path = S / (name + ".py")
    assert path.exists(), "missing module %s" % path.name
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


br = load("breaker_run")
GOOD = fake.blocks("853", {"AC1_a": "def test_a():\n    assert 1 == 2\n"})


@pytest.fixture
def world(tmp_path, monkeypatch):
    exe = fake.make_fake(tmp_path / "bin")
    out = tmp_path / "out.txt"
    out.write_text(GOOD)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))             # every temp dir this module makes lands here
    monkeypatch.setenv("SIGMA_RESOURCE_MODE", "off")
    monkeypatch.setenv("FAKE_SIDECAR", str(tmp_path / "side.jsonl"))
    monkeypatch.setenv("FAKE_OUT", str(out))
    monkeypatch.setenv("FAKE_MODE", "blocks")
    monkeypatch.setenv("FAKE_CHILD", str(tmp_path / "child.pid"))
    monkeypatch.setenv("PYTHONPATH", "/should/not/survive")
    monkeypatch.setenv("VIRTUAL_ENV", "/should/not/survive")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-x")
    return {"exe": str(exe), "side": tmp_path / "side.jsonl", "tmp": tmp_path, "scratch": scratch}


def go(world, brief="BRIEF TEXT\n", timeout=20, **kw):
    return br.run([world["exe"], "-p"], brief, "853", {}, timeout=timeout, **kw)


def test_returns_the_text_and_runs_in_an_empty_removed_temp_cwd(world):
    # break: run in the real cwd instead of the temp dir -> this goes red
    text, receipt = go(world)
    assert text == GOOD and receipt["code"] == 0
    seen = fake.runs(world["side"])[-1]
    cwd = pathlib.Path(seen["cwd"])
    assert cwd.name.startswith("sigma-breaker-") and cwd.parent == world["scratch"].resolve()
    assert seen["listing"] == []
    assert not cwd.exists()                                   # removed afterwards


def test_stdin_is_the_brief_and_the_brief_is_not_in_the_cwd(world):
    go(world, brief="exactly this\nbrief\n")
    seen = fake.runs(world["side"])[-1]
    assert seen["stdin"] == "exactly this\nbrief\n" and seen["listing"] == []


def test_environment_is_scrubbed_and_home_is_kept_on_purpose(world, monkeypatch):
    # the one chosen scrub: python-path and pytest variables out, bytecode off, HOME KEPT (credentials reachable)
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)    # so only the runner can put it there
    go(world)
    seen = fake.runs(world["side"])[-1]
    assert seen["pythonpath"] is None and seen["venv"] is None and seen["pytest_vars"] == []
    assert seen["bytecode"] == "1"
    assert seen["home"] == os.environ["HOME"]


def test_cwd_removed_on_a_refused_run_and_on_an_exception(world, monkeypatch):
    # break: drop the cleanup in `finally` -> this goes red
    def boom():
        assert len(list(world["scratch"].iterdir())) == 2   # the empty cwd and the brief dir exist right now
        raise RuntimeError("before-spawn failed")
    with pytest.raises(RuntimeError):
        go(world, before_spawn=boom)
    assert list(world["scratch"].iterdir()) == []
    monkeypatch.setenv("FAKE_MODE", "exit1")
    with pytest.raises(br.BreakerRunRefused):
        go(world)
    assert list(world["scratch"].iterdir()) == []


def test_cleanup_never_deletes_a_directory_it_did_not_name(world, tmp_path):
    # break: drop the `sigma-breaker-` prefix guard in `_remove` -> this goes red (it would delete `elsewhere`)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    go(world, make_cwd=lambda: str(elsewhere))
    assert elsewhere.is_dir()                                  # the run used it as cwd, then left it alone
    assert fake.runs(world["side"])[-1]["cwd"] == str(elsewhere.resolve())


def test_nonempty_cwd_is_refused_before_anything_runs(world):
    # break: skip the listing check -> this goes red
    def dirty():
        d = tempfile.mkdtemp(prefix="sigma-breaker-")
        (pathlib.Path(d) / "stray.py").write_text("x = 1\n")
        return d
    with pytest.raises(br.BreakerRunRefused) as info:
        go(world, make_cwd=dirty)
    assert info.value.clause == "cwd-not-empty" and fake.runs(world["side"]) == []
    assert list(world["scratch"].iterdir()) == []


def test_empty_output_is_refused(world, monkeypatch):
    # break: accept empty output -> this goes red
    (world["tmp"] / "out.txt").write_text("")
    with pytest.raises(br.BreakerRunRefused) as info:
        go(world)
    assert info.value.clause == "empty-output"


def test_nonzero_exit_is_refused(world, monkeypatch):
    # break: ignore the FAILED outcome -> this goes red
    monkeypatch.setenv("FAKE_MODE", "exit1")
    with pytest.raises(br.BreakerRunRefused) as info:
        go(world)
    assert info.value.clause == "nonzero-exit"


def test_nonzero_exit_carries_a_bounded_secret_safe_stderr_tail(world):
    # break: drop the stderr tail from the refusal (or leave control chars / secrets / the length unbounded)
    # -> this goes red. An unauthenticated host CLI must be diagnosable from the refusal alone.
    bounded = br._load("bounded_run")
    FAKE_KEY = "s" + "k-" + "abcdefghijklmnopqrstuvwxyz0123456789"  # built, not literal: leak_scan
    noise = "warming up. " * 100
    err = (noise + "\x1b[31mnot logged in: run `cursor-agent login`\x1b[0m\x00 api_key=" + FAKE_KEY +
           " Author" + "ization: Bea" + "rer abc.def.ghi")
    fake_group = lambda *a, **k: bounded.Result(bounded.FAILED, 1, "", err, 0.1, False, False, "")
    with pytest.raises(br.BreakerRunRefused) as info:
        go(world, run_group=fake_group)
    text = str(info.value)
    assert info.value.clause == "nonzero-exit"
    assert "not logged in: run `cursor-agent login`" in text
    assert "\x1b" not in text and "\x00" not in text and "[31m" not in text
    assert FAKE_KEY not in text and "abc.def.ghi" not in text
    assert len(text) < 600 and text.count("warming up.") < 30


def test_nonzero_exit_with_no_stderr_still_refuses_cleanly(world):
    bounded = br._load("bounded_run")
    fake_group = lambda *a, **k: bounded.Result(bounded.FAILED, 2, "", "", 0.1, False, False, "")
    with pytest.raises(br.BreakerRunRefused) as info:
        go(world, run_group=fake_group)
    assert "exited 2" in str(info.value)


def test_timeout_kills_the_whole_process_group_including_the_grandchild(world, monkeypatch):
    # break: kill only the direct child (plain Popen.kill) -> the grandchild survives and this goes red
    monkeypatch.setenv("FAKE_MODE", "hang")
    started = time.monotonic()
    with pytest.raises(br.BreakerRunRefused) as info:
        go(world, timeout=1)
    # the whole group is stopped promptly (about 1 s budget + TERM). Killing only the leader leaves the group
    # alive for the 10 s TERM grace, and `bounded_run`'s lifeline would then reap the grandchild AFTER return:
    # so the elapsed bound, not only the final liveness, is what detects it.
    assert info.value.clause == "timeout" and time.monotonic() - started < 8
    pid = int((world["tmp"] / "child.pid").read_text())
    for _ in range(10):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)
        pytest.fail("the grandchild %d survived the timeout" % pid)


def _lease_double(monkeypatch, events, hold=None):
    guard = br._load("resource_guard")

    class Hold:
        reason = "memory pressure"

    @contextlib.contextmanager
    def lease(kind, goal, **k):
        events.append(("acquire", kind, goal, k["ttl"]))
        try:
            yield Hold() if hold else None
        finally:
            events.append(("release",))
    monkeypatch.setattr(guard, "session_lease", lease)


def test_a_lease_hold_refuses_before_the_hook_and_the_spawn(world, monkeypatch):
    # break: ignore the Hold -> the hook and the spawn run and this goes red
    events = []
    _lease_double(monkeypatch, events, hold=True)
    called = []
    with pytest.raises(br.BreakerRunRefused) as info:
        go(world, before_spawn=lambda: called.append(1))
    assert info.value.clause == "held" and called == [] and fake.runs(world["side"]) == []


def test_the_lease_is_acquired_and_released_around_the_spawn(world, monkeypatch):
    events = []
    _lease_double(monkeypatch, events)
    go(world, timeout=20)
    assert events == [("acquire", "breaker", "853", 20 * 2 + 60), ("release",)]


def test_before_spawn_runs_after_the_cwd_exists_and_before_the_spawn(world):
    marks = []
    go(world, before_spawn=lambda: marks.append(len(fake.runs(world["side"]))))
    assert marks == [0] and len(fake.runs(world["side"])) == 1


def test_a_bad_timeout_is_a_refusal_not_a_crash(world):
    with pytest.raises(br.BreakerRunRefused) as info:
        go(world, timeout=0)
    assert info.value.clause == "timeout"

"""#2117: an agent marker is alive only when pid, process start time AND boot id all match.

A container restart reuses low pids for unrelated processes; `os.kill(pid, 0)` alone read the
marker as alive until the 12h lease ran out. The process identity is read from a /proc tree whose
root is `ledger.PROC_ROOT`, so these tests build a real directory tree and run the real reader
(nothing is mocked at the comparison), and each clause of the verdict is broken on its own in the
"control" tests at the bottom (AGENTS.md "run the control").
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
CONFIG = {"agent_watch": {"enabled": True}}
BOOT_A = "11111111-aaaa-4aaa-8aaa-111111111111"
BOOT_B = "22222222-bbbb-4bbb-8bbb-222222222222"


@pytest.fixture(autouse=True)
def _no_codex_host(monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _stat_line(pid, start):
    # /proc/<pid>/stat: "pid (comm) state ppid ... " -- starttime is field 22. The comm may hold
    # spaces and parentheses, so the reader must split after the LAST ")".
    rest = ["S"] + ["0"] * 18 + [str(start)] + ["0"] * 5      # state + fields 4..21, then field 22
    return "%d (my) proc) %s\n" % (pid, " ".join(rest))


def _proc(root, pid, start, boot=BOOT_A):
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stat").write_text(_stat_line(pid, start))
    b = root / "sys" / "kernel" / "random"
    b.mkdir(parents=True, exist_ok=True)
    (b / "boot_id").write_text(boot + "\n")


@pytest.fixture
def fake_proc(tmp_path, monkeypatch):
    """A /proc tree where this very test process (a genuinely live pid) has start 5000 on BOOT_A."""
    root = tmp_path / "proc"
    root.mkdir()
    _proc(root, os.getpid(), 5000)
    ledger = _load("ledger")
    monkeypatch.setattr(ledger, "PROC_ROOT", str(root))
    return root


@pytest.fixture
def lp(fake_proc):
    m = _load("loop")
    m.ledger.PROC_ROOT = str(fake_proc)
    return m


def _sdlc(tmp_path):
    return str(tmp_path / ".sdlc")


# ---------------------------------------------------------------- the reader


def test_the_reader_takes_field_22_after_the_last_paren_and_the_boot_id(fake_proc):
    ledger = _load("ledger")
    ledger.PROC_ROOT = str(fake_proc)
    assert ledger.process_identity(os.getpid()) == {"start": 5000, "boot": BOOT_A}


def test_an_unreadable_proc_gives_no_identity_rather_than_a_guess(tmp_path):
    ledger = _load("ledger")
    ledger.PROC_ROOT = str(tmp_path / "no-such-proc")
    assert ledger.process_identity(os.getpid()) == {"start": None, "boot": None}
    assert ledger.identity_readable() is False


# ---------------------------------------------------------------- AC-2: a matching process is alive


def test_ac2_a_marker_for_a_live_matching_process_reads_alive(lp, tmp_path):
    sdlc = _sdlc(tmp_path)
    assert lp.agent_start(sdlc, "g.md", os.getpid(), CONFIG) is True
    rec = json.loads(lp._agent_marker_path(sdlc, "g.md").read_text())
    assert rec == {"pid": os.getpid(), "start": 5000, "boot": BOOT_A}
    assert lp.agent_alive(sdlc, "g.md", CONFIG) == ("alive", os.getpid())
    assert lp.agent_reclaim(sdlc, "g.md", CONFIG) == {"reclaimed": [], "blocked": [("main", os.getpid())]}
    assert lp.agent_threads(sdlc, "g.md") == ["main"]            # no false reclaim


# ---------------------------------------------------------------- AC-1: one differing clause is dead


def test_ac1_a_reused_pid_with_a_different_start_time_reads_dead_and_is_reclaimable(lp, fake_proc, tmp_path):
    sdlc = _sdlc(tmp_path)
    lp.agent_start(sdlc, "g.md", os.getpid(), CONFIG)
    _proc(fake_proc, os.getpid(), 9999)                           # same pid, a different process now
    assert lp.agent_alive(sdlc, "g.md", CONFIG) == ("dead", os.getpid())
    got = lp.agent_reclaim(sdlc, "g.md", CONFIG)                  # no lease wait: the marker is fresh
    assert got == {"reclaimed": [("main", os.getpid())], "blocked": []}
    assert lp.agent_threads(sdlc, "g.md") == []


def test_ac1_a_marker_from_before_a_reboot_reads_dead_and_is_reclaimable(lp, fake_proc, tmp_path):
    sdlc = _sdlc(tmp_path)
    lp.agent_start(sdlc, "g.md", os.getpid(), CONFIG)
    _proc(fake_proc, os.getpid(), 5000, boot=BOOT_B)              # same pid AND start tick, new boot
    assert lp.agent_alive(sdlc, "g.md", CONFIG) == ("dead", os.getpid())
    assert lp.agent_reclaim(sdlc, "g.md", CONFIG)["reclaimed"] == [("main", os.getpid())]


def test_ac1_a_pid_that_no_longer_exists_still_reads_dead(lp, tmp_path):
    sdlc = _sdlc(tmp_path)
    lp.agent_start(sdlc, "g.md", 2**30, CONFIG)
    assert lp.agent_alive(sdlc, "g.md", CONFIG) == ("dead", 2**30)


def test_a_codex_marker_keeps_its_thread_and_gains_the_identity(lp, monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    thread = "01999aaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"
    monkeypatch.setenv("CODEX_THREAD_ID", thread)
    monkeypatch.setenv("CODEX_SESSION_ID", thread)
    sdlc = _sdlc(tmp_path)
    assert lp.agent_start(sdlc, "g.md", os.getpid(), CONFIG) is True
    rec = json.loads(lp._agent_marker_path(sdlc, "g.md").read_text())
    assert rec == {"pid": os.getpid(), "codex_thread_id": thread, "start": 5000, "boot": BOOT_A}
    assert lp._agent_marker_identity(sdlc, "g.md") == (os.getpid(), thread)
    assert lp.agent_alive(sdlc, "g.md", CONFIG) == ("alive", os.getpid())


# ---------------------------------------------------------------- unreadable identity: today's probe


def test_with_no_readable_identity_the_marker_is_a_bare_pid_and_the_pid_probe_decides(tmp_path):
    lp = _load("loop")
    lp.ledger.PROC_ROOT = str(tmp_path / "no-such-proc")
    sdlc = _sdlc(tmp_path)
    lp.agent_start(sdlc, "g.md", os.getpid(), CONFIG)
    assert lp._agent_marker_path(sdlc, "g.md").read_text() == "%d\n" % os.getpid()
    assert lp.agent_alive(sdlc, "g.md", CONFIG) == ("alive", os.getpid())


def test_a_marker_that_recorded_identity_but_cannot_read_it_now_fails_toward_alive(lp, fake_proc, tmp_path):
    """The reader going dark later must not turn a live agent into a reclaimable one."""
    sdlc = _sdlc(tmp_path)
    lp.agent_start(sdlc, "g.md", os.getpid(), CONFIG)
    lp.ledger.PROC_ROOT = str(tmp_path / "gone")
    assert lp.agent_alive(sdlc, "g.md", CONFIG) == ("alive", os.getpid())


def test_a_legacy_bare_pid_marker_still_parses_and_is_judged_by_the_pid_probe(lp, tmp_path):
    sdlc = _sdlc(tmp_path)
    path = lp._agent_marker_path(sdlc, "g.md")
    path.parent.mkdir(parents=True)
    path.write_text("%d\n" % os.getpid())
    assert lp.agent_alive(sdlc, "g.md", CONFIG) == ("alive", os.getpid())


# ---------------------------------------------------------------- every other liveness read


def test_the_pruner_and_the_worker_check_use_the_same_identity_verdict(lp, fake_proc, tmp_path):
    sdlc = _sdlc(tmp_path)
    lp.agent_start(sdlc, "g.md", os.getpid(), CONFIG)
    assert lp._goal_has_registered_worker(sdlc, "g.md", CONFIG) is True
    _proc(fake_proc, os.getpid(), 9999)
    assert lp._goal_has_registered_worker(sdlc, "g.md", CONFIG) is False


# ---------------------------------------------------------------- the doctor says what it can read


def test_the_doctor_row_says_so_when_identity_cannot_be_read(tmp_path):
    doctor = _load_doctor()
    row = doctor._agent_marker_identity_row(proc_root=str(tmp_path / "no-such-proc"))
    assert "pid probe only" in row["name"] and row["ok"] is True


def test_the_doctor_row_names_the_three_clauses_when_identity_is_readable(fake_proc):
    doctor = _load_doctor()
    row = doctor._agent_marker_identity_row(proc_root=str(fake_proc))
    assert "pid + start time + boot id" in row["name"] and row["ok"] is True


def _load_doctor():
    scripts = SCRIPTS.parent.parent / "sigma-doctor" / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location("doctor", scripts / "doctor.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.remove(str(scripts))


# ---------------------------------------------------------------- AC-4: the controls, clause by clause


def _verdict(ledger, pid, recorded):
    return ledger.identity_alive(pid, recorded)


@pytest.mark.parametrize("recorded,expect", [
    ({"start": 5000, "boot": BOOT_A}, True),
    ({"start": 5001, "boot": BOOT_A}, False),        # start clause alone
    ({"start": 5000, "boot": BOOT_B}, False),        # boot clause alone
])
def test_ac4_start_and_boot_are_each_decisive_on_their_own(fake_proc, recorded, expect):
    ledger = _load("ledger")
    ledger.PROC_ROOT = str(fake_proc)
    assert _verdict(ledger, os.getpid(), recorded) is expect


def test_ac4_the_pid_clause_alone_is_decisive(fake_proc):
    ledger = _load("ledger")
    ledger.PROC_ROOT = str(fake_proc)
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()                                          # a real pid, now gone
    _proc(fake_proc, p.pid, 5000)                     # a /proc entry that agrees on start and boot
    assert _verdict(ledger, p.pid, {"start": 5000, "boot": BOOT_A}) is False

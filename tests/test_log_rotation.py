"""#460 -- size-capped rotation for the Slack listener log and the supervisor log.

`logroll.py` is exercised at its own seam (deterministic, in-process: the lock, the re-check under
the lock, the move step and the free-disk cap are each injected), then through the two real writers
(`slack_commands_listen._log`, `supervise_daemon.main`).  The one real-subprocess concurrency test
at the bottom is a labelled SMOKE test, not the system of record: a forking race is probabilistic
and cannot prove the lock, so the in-process nodes above it are the control.
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = ROOT / "skills" / "sigma-loop" / "scripts"


def _load(name):
    path = S / f"{name}.py"
    assert path.exists(), f"{path.name} does not exist"
    spec = importlib.util.spec_from_file_location(name + "_t460", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _generations(path):
    return sorted(p.name for p in path.parent.glob(path.name + ".*") if p.name[len(path.name) + 1:].isdigit())


def _all_text(path):
    out = []
    for p in [path] + [path.with_name(f"{path.name}.{n}") for n in range(1, 10)]:
        if p.exists():
            out.append(p.read_text(encoding="utf-8"))
    return "".join(out)


def test_rotates_keeps_n(tmp_path):
    lr = _load("logroll")
    log = tmp_path / "x.log"
    for i in range(60):
        lr.append(log, "line %03d %s\n" % (i, "p" * 20), cap=100, generations=3)
    assert _generations(log) == ["x.log.1", "x.log.2", "x.log.3"]
    assert not (tmp_path / "x.log.4").exists()
    assert log.stat().st_size < 100 + 40


def test_below_cap_untouched(tmp_path):
    lr = _load("logroll")
    log = tmp_path / "x.log"
    for i in range(3):
        lr.append(log, "short %d\n" % i, cap=1000)
    assert _generations(log) == []
    assert log.read_text() == "short 0\nshort 1\nshort 2\n"


def test_trigger_line_fresh(tmp_path):
    lr = _load("logroll")
    log = tmp_path / "x.log"
    log.write_text("old " * 40 + "\n")
    lr.append(log, "the current line\n", cap=100)
    assert log.read_text() == "the current line\n"
    assert "old old" in (tmp_path / "x.log.1").read_text()


def test_live_lock_skips(tmp_path):
    lr = _load("logroll")
    log = tmp_path / "x.log"
    log.write_text("z" * 200 + "\n")
    (tmp_path / "x.log.rotating").write_text("")           # another rotator, lock is fresh
    lr.append(log, "kept line\n", cap=100)
    assert "kept line" in log.read_text()
    assert _generations(log) == []
    assert (tmp_path / "x.log.rotating").exists()           # a lock we did not take is not ours to drop


def test_stale_lock_taken(tmp_path):
    lr = _load("logroll")
    log = tmp_path / "x.log"
    log.write_text("z" * 200 + "\n")
    lock = tmp_path / "x.log.rotating"
    lock.write_text("")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    lr.append(log, "after the crash\n", cap=100)
    assert log.read_text() == "after the crash\n"
    assert (tmp_path / "x.log.1").exists()
    assert not lock.exists()


def test_rival_rechecked(tmp_path, monkeypatch):
    """Seam control for the re-check under the lock: without it the second rotator shifts again and
    evicts a generation for no reason."""
    lr = _load("logroll")
    log = tmp_path / "x.log"
    log.write_text("z" * 200 + "\n")
    real = lr._acquire

    def rival_first(lock):
        got = real(lock)
        os.replace(log, tmp_path / "x.log.1")               # the rival finished its whole rotation
        log.write_text("fresh\n")
        return got

    monkeypatch.setattr(lr, "_acquire", rival_first)
    assert lr.rotate(log, cap=100) is False
    assert _generations(log) == ["x.log.1"]                 # not shifted a second time
    assert log.read_text() == "fresh\n"
    assert not (tmp_path / "x.log.rotating").exists()


def test_crash_mid_shift(tmp_path, monkeypatch):
    lr = _load("logroll")
    log = tmp_path / "x.log"
    log.write_text("live content " * 20 + "\n")
    before = log.read_text()
    (tmp_path / "x.log.1").write_text("gen one\n")

    def boom(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(lr, "_move", boom)
    lr.append(log, "survivor\n", cap=100)                   # must not raise
    assert log.read_text() == before + "survivor\n"
    assert not (tmp_path / "x.log.rotating").exists()
    monkeypatch.undo()
    lr.append(log, "next\n", cap=100)                       # and the next writer rotates normally
    assert log.read_text() == "next\n"
    assert "survivor" in (tmp_path / "x.log.1").read_text()


def test_non_oserror_safe(tmp_path, monkeypatch):
    lr = _load("logroll")
    log = tmp_path / "x.log"
    log.write_text("z" * 200 + "\n")

    def boom(src, dst):
        raise RuntimeError("not an OSError")

    monkeypatch.setattr(lr, "_move", boom)
    lr.append(log, "still here\n", cap=100)
    assert "still here" in log.read_text()
    assert not (tmp_path / "x.log.rotating").exists()


def test_tiny_cap_no_loss(tmp_path):
    lr = _load("logroll")
    log = tmp_path / "x.log"
    for i in range(20):
        lr.append(log, "tiny-cap line %02d\n" % i, cap=5, generations=40)
    text = "".join(p.read_text() for p in sorted(tmp_path.glob("x.log*"))
                   if not p.name.endswith(".rotating"))
    for i in range(20):
        assert "tiny-cap line %02d\n" % i in text, i
    assert len(_generations(log)) <= 40


def test_cap_from_machine(tmp_path, monkeypatch):
    lr = _load("logroll")
    gib = 1 << 30
    monkeypatch.setattr(lr.shutil, "disk_usage", lambda p: type("U", (), {"free": 500 * gib})())
    assert lr.cap_bytes(tmp_path) == lr.DEFAULT_CAP_BYTES
    monkeypatch.setattr(lr.shutil, "disk_usage", lambda p: type("U", (), {"free": 20_000_000})())
    assert lr.cap_bytes(tmp_path) == 200_000
    monkeypatch.setattr(lr.shutil, "disk_usage", lambda p: type("U", (), {"free": 1000})())
    assert lr.cap_bytes(tmp_path) == lr.MIN_CAP_BYTES

    def gone(p):
        raise OSError("vanished mount")

    monkeypatch.setattr(lr.shutil, "disk_usage", gone)
    assert lr.cap_bytes(tmp_path) == lr.DEFAULT_CAP_BYTES


def test_slack_log_rotates(tmp_path, monkeypatch, capsys):
    sc = _load("slack_commands_listen")
    assert hasattr(sc, "logroll"), "the listener does not use logroll"
    monkeypatch.setattr(sc.logroll, "DEFAULT_CAP_BYTES", 400)
    sdlc = tmp_path / ".sdlc"
    secret = "xo" + "xb-123456789012-abcdefghijklmnop"   # assembled, so this file is not itself a leak
    for i in range(30):
        sc._log(sdlc, "event %d carried %s" % (i, secret))
    log = sc.log_path(sdlc)
    assert _generations(log), "the listener log never rotated"
    text = _all_text(log)
    assert secret not in text and "[REDACTED:slack-token]" in text
    assert "event 29" in log.read_text()
    assert log.stat().st_size < 400 + 200
    assert sum(p.stat().st_size for p in log.parent.glob("slack-commands.log*")) < 4 * (400 + 200)


def _fake_session(tmp_path, body):
    script = tmp_path / "fake_session.py"
    script.write_text(textwrap.dedent(body))
    return "%s %s" % (sys.executable, script)


def _run_supervisor(sd, sdlc, monkeypatch, cmd, runs):
    monkeypatch.setenv("SIGMA_CLAUDE_CMD", cmd)
    monkeypatch.setenv("SIGMA_SUPERVISE_SLEEP_SCALE", "0")
    monkeypatch.setenv("SIGMA_SUPERVISE_MAX_RUNS", str(runs))
    return sd.main(["supervise_daemon.py", str(sdlc)])


def test_supervisor_rotates(tmp_path, monkeypatch, capsys):
    sd = _load("supervise_daemon")
    assert hasattr(sd, "logroll"), "the supervisor does not use logroll"
    monkeypatch.setattr(sd.logroll, "DEFAULT_CAP_BYTES", 600)
    cmd = _fake_session(tmp_path, "print('session output line ' * 6)\n")
    sdlc = tmp_path / ".sdlc"
    _run_supervisor(sd, sdlc, monkeypatch, cmd, runs=12)
    log = sdlc / "state" / "supervisor.log"
    assert _generations(log), "supervisor.log never rotated"
    sizes = [p.stat().st_size for p in log.parent.glob("supervisor.log*") if not p.name.endswith(".rotating")]
    assert max(sizes) < 600 + 600
    assert "max runs (12) reached" in log.read_text()


def test_supervisor_trim(
        tmp_path, monkeypatch, capsys):
    sd = _load("supervise_daemon")
    assert hasattr(sd, "logroll"), "the supervisor does not use logroll"
    monkeypatch.setattr(sd.logroll, "DEFAULT_CAP_BYTES", 1000)
    cmd = _fake_session(tmp_path, """
        import sys
        sys.stdout.buffer.write(("\\u00e9" * 3000 + "FINAL-LINE").encode("utf-8"))
        """)
    sdlc = tmp_path / ".sdlc"
    _run_supervisor(sd, sdlc, monkeypatch, cmd, runs=1)
    log = sdlc / "state" / "supervisor.log"
    files = [p for p in log.parent.glob("supervisor.log*") if not p.name.endswith(".rotating")]
    assert max(p.stat().st_size for p in files) < 1000 + 700
    text = "".join(p.read_bytes().decode("utf-8") for p in files)   # a byte trim must not split a char
    assert "FINAL-LINE" in text and "truncated" in text


def test_smoke_concurrent(tmp_path):
    """SMOKE TEST ONLY. Real forked writers race the rotation, which is probabilistic and cannot
    prove the lock -- the seam nodes above are the control.  Generations are sized so that nothing may
    legitimately be evicted, so every line must still be found exactly once."""
    log = tmp_path / "x.log"
    code = textwrap.dedent("""
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location("logroll", sys.argv[1])
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        for i in range(150):
            m.append(sys.argv[2], "w%s-%03d\\n" % (sys.argv[3], i), cap=800, generations=500)
        """)
    procs = [subprocess.Popen([sys.executable, "-c", code, str(S / "logroll.py"), str(log), str(n)])
             for n in range(4)]
    for p in procs:
        assert p.wait(timeout=120) == 0
    lines = []
    for p in tmp_path.glob("x.log*"):
        if not p.name.endswith(".rotating"):
            lines += p.read_text().split()
    expected = {"w%d-%03d" % (n, i) for n in range(4) for i in range(150)}
    assert sorted(lines) == sorted(expected)

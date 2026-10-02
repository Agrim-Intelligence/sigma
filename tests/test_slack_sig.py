"""Real-subprocess SIGTERM/SIGHUP controls for the Slack listener (#424).

A real listener process gets each signal; the parent blocks on a READY line printed at the first
stop-file poll (after startup finished writing its markers), every wait is bounded, and the exit
status must be death-by-signal. Short node ids on purpose: the planned red-before-green proof reads
pytest's 80-column `-rA` summary, which drops the failure reason once the id is long.
"""
import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "agrim-loop" / "scripts"
_spec = importlib.util.spec_from_file_location("slack_commands_listen", S / "slack_commands_listen.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def _sdlc(tmp_path):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    return d


def _config(channel="C1111111", app_env="APP_ENV_T", bot_env="BOT_ENV_T", enabled=True):
    return {"slack_commands": {"enabled": enabled, "channel_id": channel,
                                "app_token_env": app_env, "bot_token_env": bot_env}}


_CHILD = """
import importlib.util, signal, sys
for _n in ("SIGTERM", "SIGHUP"):
    if hasattr(signal, _n):
        signal.signal(getattr(signal, _n), signal.SIG_DFL)   # a nohup parent must not mask the control
spec = importlib.util.spec_from_file_location("slack_commands_listen", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class _Client:
    def connect(self):
        pass
    def close(self):
        pass

# READY is printed from the FIRST stop-file poll: that runs after run()'s post-connect heartbeat write
# and log line, so nothing the child does later can overwrite markers the parent rewrites afterwards.
_polled = []
def _stop_requested(sdlc_dir):
    if not _polled:
        _polled.append(1)
        print("READY", flush=True)
    return False

m._build_client = lambda *a, **k: _Client()
m.stop_requested = _stop_requested
sys.exit(m.main(["slack_commands_listen.py", sys.argv[2]]))
"""

_POSIX_SIGNALS = [n for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n)]
_needs_posix = pytest.mark.skipif(sys.platform == "win32",
                                  reason="a signal terminates the process on Windows without a handler")


def _spawn_child(tmp_path):
    d = _sdlc(tmp_path)
    (d / "config.json").write_text(json.dumps(_config()))
    script = tmp_path / "child.py"
    script.write_text(_CHILD)
    env = dict(os.environ, APP_ENV_T="xapp-fake", BOT_ENV_T="xoxb-fake")
    proc = subprocess.Popen([sys.executable, str(script), str(S / "slack_commands_listen.py"), str(d)],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    return d, proc


def _await_ready(proc):
    """Read the READY handshake with a hard bound (a reader thread: a blocked readline cannot hang)."""
    box = []
    reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(60)
    if not box or box[0].strip() != "READY":
        proc.kill()
        proc.wait(10)
        pytest.fail("child never reached READY: %r" % (box,))


def _signal_and_reap(proc, name):
    proc.send_signal(getattr(signal, name))
    try:
        return proc.wait(60)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(10)
        pytest.fail("child survived %s" % name)


@_needs_posix
@pytest.mark.parametrize("name", _POSIX_SIGNALS)
def test_clean(tmp_path, name):
    d, proc = _spawn_child(tmp_path)
    try:
        _await_ready(proc)
        assert sc.pid_path(d).is_file() and sc.heartbeat_path(d).is_file() and sc.lock_dir_path(d).is_dir()
        rc = _signal_and_reap(proc, name)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
    assert rc == -getattr(signal, name)   # died BY the signal, not a masked clean exit
    assert not sc.pid_path(d).exists()
    assert not sc.heartbeat_path(d).exists()
    assert not sc.lock_dir_path(d).exists()


@_needs_posix
@pytest.mark.parametrize("name", _POSIX_SIGNALS)
def test_successor_kept(tmp_path, name):
    d, proc = _spawn_child(tmp_path)
    try:
        _await_ready(proc)
        sc.pid_path(d).write_text("424242")      # a successor has taken over, heartbeat and all
        heartbeat_before = json.dumps({"pid": 424242, "last_seen": time.time()})
        sc.heartbeat_path(d).write_text(heartbeat_before)
        _signal_and_reap(proc, name)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
    assert sc.pid_path(d).read_text() == "424242"
    assert sc.heartbeat_path(d).read_text() == heartbeat_before
    assert sc.lock_dir_path(d).is_dir()

#!/usr/bin/env python3
"""Recorded dead-vs-idle control for the ledger watcher (D5, #265).

Usage:
    dead_vs_idle.py --workdir DIR --json OUT [--break]

Starts the REAL ``watch_daemon.py`` in a scratch repository, then stops it with a signal sent to that
exact pid (``Popen.pid``; never a pattern kill, never SIGSTOP) and asks the two real readers -- the
doctor's ``_ledger_watcher_state`` and ``hooks/session_start.sh`` -- what they say at each stage.

Legs: (A) alive and between ticks, (B) SIGTERM, (C) SIGKILL of a second daemon, read immediately and
again once its heartbeat is older than ``stale_after`` (a real wait of ~190 s).  ``--break`` replaces
the doctor's age rule with "never stale" (an existence-style check) and the same assertions must go
red (exit 1).  Interval 60 s -> stale_after = max(3 x 60, 180) = 180 s.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"
INTERVAL = 60
STALE_AFTER = max(3 * INTERVAL, 180)
SCHEMA = "readiness-dead-vs-idle/v1"
SCRATCH = re.compile(r"(?:/(?:private/)?tmp|/var/tmp|/(?:private/)?var/folders|/scratch|/w)(?:/[^\s'\"]*)?")


def _doctor():
    spec = importlib.util.spec_from_file_location("doctor_ctl", ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Watcher:
    def __init__(self, repo, env, log):
        self.repo, self.env, self.log = repo, env, log
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen([sys.executable, str(SCRIPTS / "watch_daemon.py"), ".sdlc"], cwd=self.repo,
                                     env=self.env, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        hb = self.repo / ".sdlc" / "state" / "watch.heartbeat"
        end = time.time() + 30
        while time.time() < end and not hb.exists():
            time.sleep(0.2)
        return self.proc.pid

    def stop(self, sig):
        os.kill(self.proc.pid, sig)          # the exact pid we spawned
        try:
            return self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            return None


def observe(repo, doctor, broken):
    state = repo / ".sdlc" / "state"
    cfg = json.loads((repo / ".sdlc" / "config.json").read_text())
    if broken:
        sync = doctor._load_loop_script("sync")
        sync.stale_after_seconds = lambda interval: 10 ** 12       # the age clause removed
    row = doctor._ledger_watcher_state(repo / ".sdlc", cfg)
    env = {k: v for k, v in os.environ.items() if k != "SIGMA_RUN_ID"}
    env["CLAUDE_PROJECT_DIR"] = str(repo)
    hook = subprocess.run(["bash", str(ROOT / "hooks" / "session_start.sh")], input="{}", capture_output=True,
                          text=True, env=env, timeout=60).stdout.strip()
    ctx = json.loads(hook)["hookSpecificOutput"]["additionalContext"] if hook else ""
    pid_text = (state / "watch.pid").read_text().strip() if (state / "watch.pid").exists() else None
    hb = state / "watch.heartbeat"
    return {
        "doctor_row": row,
        "session_start": ctx,
        "pid_file": pid_text,
        "pid_alive": bool(pid_text and pid_text.isdigit() and _pid_alive(int(pid_text))),
        "heartbeat_age_s": round(time.time() - hb.stat().st_mtime, 1) if hb.exists() else None,
    }


def run(workdir, broken):
    repo = workdir / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    (repo / ".sdlc" / "config.json").write_text(json.dumps({"ledger": {"enabled": True, "watch": {"interval_seconds": INTERVAL}}}))
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = workdir / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("SIGMA_") and k != "GH_TOKEN" and k != "GITHUB_TOKEN"}
    env.update({"HOME": str(home), "SIGMA_WATCH_CALL_TIMEOUT": "20", "GH_CONFIG_DIR": str(home / "gh")})
    doctor = _doctor()
    log = open(workdir / "daemon.log", "ab")
    stages, checks = [], []

    def stage(name, **extra):
        o = observe(repo, doctor, broken)
        o.update(name=name, **extra)
        stages.append(o)
        return o

    def check(name, ok, detail):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    w = Watcher(repo, env, log)
    try:
        # (A) alive, between ticks
        pid = w.start()
        check("watch_pid_file_is_the_spawned_pid", (repo / ".sdlc/state/watch.pid").read_text().strip() == str(pid), {"pid_matches": True})
        time.sleep(INTERVAL + 40)            # past one full sleep: at least a second tick has happened
        a = stage("A_alive_between_ticks", signal_sent=None)
        check("A_alive_reads_ON", a["doctor_row"].startswith("ON") and a["pid_alive"] and a["session_start"] == "", a["doctor_row"])
        # (B) SIGTERM to the exact pid
        rc = w.stop(signal.SIGTERM)
        b = stage("B_after_sigterm", signal_sent="SIGTERM", returncode=rc)
        check("B_sigterm_exits_by_signal", rc == -signal.SIGTERM, {"returncode": rc})
        check("B_sigterm_leaves_no_pid_or_heartbeat", b["pid_file"] is None and b["heartbeat_age_s"] is None, {"pid_file": b["pid_file"]})
        check("B_sigterm_is_reported_not_running", ("NEVER RUN" in b["doctor_row"]) and "has never started" in b["session_start"], b["doctor_row"])
        # (C) crash: SIGKILL a fresh daemon, read at once, then past stale_after
        pid = w.start()
        time.sleep(15)
        w.stop(signal.SIGKILL)
        killed = time.time()
        c0 = stage("C0_just_killed", signal_sent="SIGKILL")
        check("C0_pid_gone_but_young_heartbeat_still_reads_ON", (not _pid_alive(pid)) and c0["doctor_row"].startswith("ON"), {"pid_alive": _pid_alive(pid), "row": c0["doctor_row"]})
        time.sleep(max(0, killed + STALE_AFTER + 10 - time.time()))
        c1 = stage("C1_killed_older_than_stale_after", signal_sent="SIGKILL")
        check("C1_dead_reads_STALE_by_age", c1["doctor_row"].startswith("STALE") and not c1["pid_alive"], c1["doctor_row"])
        check("C1_session_start_warns", "looks stale" in c1["session_start"], c1["session_start"])
    finally:
        if w.proc and w.proc.poll() is None:
            os.kill(w.proc.pid, signal.SIGTERM)
            w.proc.wait(timeout=20)
        log.close()
    return {
        "schema": SCHEMA,
        "broken": broken,
        "platform": sys.platform,
        "interval_seconds": INTERVAL,
        "stale_after_seconds": STALE_AFTER,
        "stages": stages,
        "checks": checks,
        "passed": all(c["passed"] for c in checks),
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--json", type=Path, required=True)
    ap.add_argument("--break", dest="broken", action="store_true")
    args = ap.parse_args(argv[1:])
    if args.workdir.exists() and any(args.workdir.iterdir()):
        print("dead_vs_idle: workdir exists and is not empty", file=sys.stderr)
        return 2
    args.workdir.mkdir(parents=True, exist_ok=True)
    result = run(args.workdir.resolve(), args.broken)
    result["commit"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    text = SCRATCH.sub("<scratch>", json.dumps(result, indent=2, sort_keys=True))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(text + "\n", encoding="utf-8")
    for c in result["checks"]:
        print(("pass " if c["passed"] else "FAIL ") + c["name"])
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

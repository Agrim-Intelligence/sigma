#!/usr/bin/env python3
"""Hermetic crash/recovery readiness drills for #348.

Usage:
    drills.py run D1|D2|D3|D4 --seed N --workdir DIR --json OUT
    drills.py evidence --workdir DIR --json OUT

The public runner is deliberately stdlib-only.  It never falls through to the
host's GitHub credentials: individual drills build their own stateful fake.
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
SCHEMA = "readiness-drills/v1"
EVIDENCE_SCHEMA = "readiness-drill-evidence/v1"
MERGE_CHECKPOINTS = ("pre_write", "durable_remote_update", "post_response_before_ack")
SEED_CHECKPOINTS = {
    1: "pre_write",
    2: "durable_remote_update",
    3: "post_response_before_ack",
    4: "pre_write",
    5: "durable_remote_update",
}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class UsageError(Exception):
    """The requested drill invocation is unsafe or malformed."""


def checkpoint_for_seed(seed: int) -> str:
    try:
        return SEED_CHECKPOINTS[seed]
    except KeyError as exc:
        raise UsageError("seed must be one of 1,2,3,4,5") from exc


def _run(argv, *, cwd=None, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)


def _load_dedup():
    path = ROOT / "skills" / "agrim-scope" / "scripts" / "dedup.py"
    spec = importlib.util.spec_from_file_location("readiness_dedup", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _invariant(name, passed, observed):
    return {"name": name, "passed": bool(passed), "observed": observed}


def _classify_reporting(stdout: str, stderr: str) -> str:
    text = (stdout + "\n" + stderr).lower()
    if "watch.stop" in text or "stop-file" in text or "stop file" in text:
        return "stopped"
    if "stale" in text or "looks dead" in text or "probably dead" in text:
        return "dead/stale"
    if "last heartbeat" in text or "on —" in text:
        return "idle/on"
    return "no_diagnosis"


def _result_command(proc):
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def _scratch_sdlc(workdir: Path):
    repo = workdir / "d4-repo"
    state = repo / ".sdlc" / "state"
    state.mkdir(parents=True)
    (repo / ".sdlc" / "config.json").write_text(json.dumps({
        "ledger": {"enabled": True, "watch": {"interval_seconds": 1}},
        "session_start": {"enabled": False},
    }) + "\n", encoding="utf-8")
    return repo, state


def _git(cwd, *args):
    proc = _run(["git", "-C", str(cwd), *args])
    if proc.returncode:
        raise UsageError(proc.stderr.strip())
    return proc.stdout.strip()


def run_d1(workdir: Path, sigma: Path = ROOT, seed: int = 1):
    """Exercise the selected fake-GitHub merge-path checkpoint in a child group."""
    checkpoint = checkpoint_for_seed(seed)
    workdir = Path(workdir)
    if workdir.exists() and any(workdir.iterdir()):
        raise UsageError("workdir exists and is not empty")
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    repo, remote = workdir / "repo", workdir / "remote.git"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "drill@example.invalid")
    _git(repo, "config", "user.name", "Drill")
    (repo / "base").write_text("base\n")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    _run(["git", "init", "-q", "--bare", "-b", "main", str(remote)])
    _git(repo, "remote", "add", "origin", str(remote)); _git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "merge").write_text("merge\n")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "candidate")
    candidate = _git(repo, "rev-parse", "HEAD")
    # The fake service can only durably update a ref to an object it owns.
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/drill-candidate")
    state, ready = workdir / "state.json", workdir / "ready.json"
    state.write_text('{"pr_state":"OPEN","ack":false}')
    child = '''import json,os,pathlib,subprocess,time
s=pathlib.Path(os.environ["S"]); r=pathlib.Path(os.environ["R"]); c=os.environ["C"]; k=os.environ["K"]
def cp(n):
 if n==k:
  r.write_text(json.dumps({"checkpoint":n,"entered_at_ns":time.monotonic_ns()}))
  while True: time.sleep(1)
cp("pre_write")
subprocess.run(["git","--git-dir",os.environ["G"],"update-ref","refs/heads/main",c],check=True)
s.write_text(json.dumps({"pr_state":"MERGED","ack":False}))
cp("durable_remote_update")
print("merged",flush=True)
cp("post_response_before_ack")
s.write_text(json.dumps({"pr_state":"MERGED","ack":True}))'''
    env = {**os.environ, "S": str(state), "R": str(ready), "C": candidate, "K": checkpoint, "G": str(remote)}
    proc = subprocess.Popen([sys.executable, "-c", child], cwd=repo, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    pgid = os.getpgid(proc.pid)
    if pgid == os.getpgrp():
        proc.kill(); raise UsageError("shared process group")
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline: time.sleep(.01)
    if not ready.exists():
        proc.kill(); raise UsageError("checkpoint not reached")
    barrier = json.loads(ready.read_text())
    requested = time.monotonic_ns(); os.killpg(pgid, signal.SIGKILL); sent = time.monotonic_ns()
    proc.communicate(timeout=10)
    observed = json.loads(state.read_text())
    remote_main = _run(["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"]).stdout.strip()
    landed = remote_main == candidate
    expected_landed = checkpoint != "pre_write"
    inv = [
        _invariant("selected_checkpoint_reached", barrier["checkpoint"] == checkpoint, barrier),
        _invariant("isolated_child_process_group", pgid != os.getpgrp(), {"pid": proc.pid, "pgid": pgid}),
        _invariant("sigkill_at_selected_checkpoint", proc.returncode == -signal.SIGKILL,
                   {"entered_at_ns": barrier["entered_at_ns"], "requested_at_ns": requested, "sent_at_ns": sent}),
        _invariant("no_later_ack_before_recovery", observed["ack"] is False, observed),
        _invariant("remote_update_matches_checkpoint", landed is expected_landed, {"landed": landed}),
    ]
    return {"schema": SCHEMA, "drill": "D1", "seed": seed, "platform": sys.platform,
            "frozen_commit": _head(sigma), "started_at_ns": started, "finished_at_ns": time.monotonic_ns(),
            "recovery_commands": [["loop.py","next","<dir>"],["loop.py","reconcile-merges","<dir>"]],
            "fault": {"checkpoint": checkpoint, "barrier": barrier}, "invariants": inv, "fake_gh_unhandled": ""}


def run_d4(workdir: Path, sigma: Path = ROOT, seed: int = 1):
    """Run the actual daemon/doctor/hook gestures against a fresh stop-file tree.

    Current diagnostics are intentionally expected to fail the explicit
    stop-file-reporting control.  That is a measured B6 input, never a pass.
    """
    workdir = Path(workdir)
    if workdir.exists() and any(workdir.iterdir()):
        raise UsageError("workdir exists and is not empty")
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    repo, state = _scratch_sdlc(workdir)
    stop = state / "watch.stop"
    stop.touch()
    daemon = _run([sys.executable, str(sigma / "skills" / "agrim-loop" / "scripts" / "watch_daemon.py"),
                   str(repo / ".sdlc")],
                  cwd=repo, env={**os.environ, "SIGMA_WATCH_SLEEP_SCALE": "0"})
    doctor = _run([sys.executable, str(sigma / "skills" / "agrim-doctor" / "scripts" / "doctor.py"),
                   "check", str(repo / ".sdlc")], cwd=repo)
    session = _run(["bash", str(sigma / "hooks" / "session_start.sh")],
                   cwd=repo, env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo),
                                  "SIGMA_ALLOW_COEXIST": "1"})
    doctor_outcome = _classify_reporting(doctor.stdout, doctor.stderr)
    session_outcome = _classify_reporting(session.stdout, session.stderr)
    finding = (
        "D4 seed %d: doctor=%s and session-start=%s after a preserved watch.stop file."
        % (seed, doctor_outcome, session_outcome)
    )
    dedup = _load_dedup().find_candidates(repo / ".sdlc", finding, records=[])
    return {
        "schema": SCHEMA,
        "drill": "D4",
        "seed": seed,
        "platform": sys.platform,
        "frozen_commit": _head(sigma),
        "started_at_ns": started,
        "finished_at_ns": time.monotonic_ns(),
        "recovery_commands": [
            [sys.executable, str(sigma / "skills" / "agrim-doctor" / "scripts" / "doctor.py"),
             "check", str(repo / ".sdlc")],
            ["bash", str(sigma / "hooks" / "session_start.sh")],
        ],
        "fault": {
            "daemon": _result_command(daemon),
            "stop_file_exists": stop.exists(),
        },
        "invariants": [
            _invariant("daemon_exits_for_stop_file", daemon.returncode == 0 and
                       "stop-file present" in daemon.stdout, _result_command(daemon)),
            _invariant("doctor_explicit_stop_file_reporting", doctor_outcome == "stopped", {
                **_result_command(doctor), "classification": doctor_outcome,
                "outcome": "reporting_invariant_passed" if doctor_outcome == "stopped"
                else "control_failed_and_recorded",
            }),
            _invariant("session_start_explicit_stop_file_reporting", session_outcome == "stopped", {
                **_result_command(session), "classification": session_outcome,
                "outcome": "reporting_invariant_passed" if session_outcome == "stopped"
                else "control_failed_and_recorded",
            }),
            _invariant("stop_file_preserved", stop.exists(), {"path": str(stop)}),
        ],
        "dedup": dedup,
    }


def _head(repo: Path) -> str:
    proc = _run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD^{commit}"])
    value = proc.stdout.strip()
    if proc.returncode or not FULL_SHA.fullmatch(value):
        raise UsageError("could not resolve a full commit SHA")
    return value


def validate_evidence(path: Path, payload: dict, checkout_sha: str):
    if not FULL_SHA.fullmatch(checkout_sha):
        raise UsageError("checkout SHA must be a full lowercase SHA")
    if payload.get("schema") != EVIDENCE_SCHEMA:
        raise UsageError("unexpected evidence schema")
    if payload.get("frozen_commit") != checkout_sha:
        raise UsageError("evidence body SHA does not match checkout")
    expected = "drills-" + checkout_sha[:12] + ".json"
    if Path(path).name != expected:
        raise UsageError("evidence filename SHA does not match checkout")


def run(drill: str, seed: int, workdir: Path, sigma: Path = ROOT):
    if drill == "D4":
        return run_d4(workdir, sigma, seed)
    checkpoint = checkpoint_for_seed(seed)
    raise UsageError("%s at checkpoint %s is not implemented yet" % (drill, checkpoint))


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("drill", choices=("D1", "D2", "D3", "D4"))
    run_parser.add_argument("--seed", type=int, required=True)
    run_parser.add_argument("--workdir", type=Path, required=True)
    run_parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv[1:])
    try:
        result = run(args.drill, args.seed, args.workdir)
    except UsageError as exc:
        print("drills: " + str(exc), file=sys.stderr)
        return 2
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("%s seed %d: %s" % (args.drill, args.seed,
                               "pass" if all(i["passed"] for i in result["invariants"]) else "recorded failure"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

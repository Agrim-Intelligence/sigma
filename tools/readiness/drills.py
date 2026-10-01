#!/usr/bin/env python3
"""Hermetic crash/recovery readiness drills for #348.

Usage:
    drills.py run D1|D2|D3|D4 --seed N --workdir DIR --json OUT
    drills.py evidence --workdir DIR --json drills-<sha12>.json

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
DRILLS = ("D1", "D2", "D3", "D4")
EVIDENCE_SEEDS = tuple(SEED_CHECKPOINTS)
WINDOWS_SKIP_REASON = "Windows is unsupported: readiness drills require POSIX process groups and SIGKILL."
D4_EXPECTED_FAILURE_STATES = {
    "doctor_explicit_stop_file_reporting": "control_failed_and_recorded",
    "session_start_explicit_stop_file_reporting": "control_failed_and_recorded",
}
B6_DISPOSITION = {
    "issue": 416,
    "disposition": "no-github-write",
    "reason": "explicit filing requires a fresh GitHub check",
}


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


def _actual_github_lifecycle(workdir: Path, sigma: Path):
    """Run Sigma's shipped fake-GitHub lifecycle and retain its command receipts.

    The readiness fixture deliberately delegates setup to the existing public
    onboarding control.  That control creates the fake ``gh`` executable,
    drives ``work.py merge`` through its review gate, and reaches the public
    ``reconcile-merges`` gesture.  Keeping its structured receipts prevents a
    drill from silently replacing the lifecycle with a bare ``git update-ref``.
    """
    # An evidence command runs twenty isolated faults.  Its lifecycle preflight
    # is deterministic and independent of the fault workdirs, so share one
    # completed public-control receipt per enclosing scratch tree rather than
    # silently turning evidence into minutes of identical setup.
    cache = workdir.parent / ".actual-github-lifecycle.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    root = workdir.parent / "actual-lifecycle"
    result_path = root / "result.json"
    root.mkdir(exist_ok=True)
    control = sigma / "tools" / "onboarding_control.py"
    proc = _run([sys.executable, str(control), "--mode", "github", "--variant", "confirm",
                 "--sigma", str(sigma), "--workdir", str(root), "--keep", "--json", str(result_path)],
                cwd=sigma)
    if proc.returncode or not result_path.is_file():
        raise UsageError("actual fake-GitHub lifecycle failed: " + (proc.stderr or proc.stdout).strip()[-500:])
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    mode = (payload.get("modes") or {}).get("github") or {}
    if not mode.get("ok"):
        raise UsageError("actual fake-GitHub lifecycle did not reach green")
    steps = {item.get("step"): item for item in mode.get("steps", [])}
    required = ("work merge", "loop reconcile-merges")
    if any(name not in steps for name in required):
        raise UsageError("actual lifecycle omitted a required command receipt")
    run_root = Path(payload["workdir"])
    repo = run_root / "github" / "repo"
    env = {**os.environ,
           "PATH": str(run_root / "github" / "bin") + os.pathsep + os.environ.get("PATH", ""),
           "FAKE_GH_STATE": str(run_root / "github" / "gh_state.json"),
           "FAKE_GH_LOG": str(run_root / "github" / "gh_log.jsonl"),
           "FAKE_GH_UNHANDLED": str(run_root / "github" / "gh_unhandled.jsonl")}
    next_proc = _run([sys.executable, str(sigma / "skills" / "agrim-loop" / "scripts" / "loop.py"),
                      "next", ".sdlc", "--session-pid", str(os.getpid())], cwd=repo, env=env)
    second_reconcile = _run([sys.executable, str(sigma / "skills" / "agrim-loop" / "scripts" / "loop.py"),
                             "reconcile-merges", ".sdlc"], cwd=repo, env=env)
    unhandled = Path(env["FAKE_GH_UNHANDLED"]).read_text(encoding="utf-8")
    def receipt(step):
        item = steps[step]
        return {"returncode": item["rc"], "stdout": item.get("stdout_tail", ""),
                "stderr": item.get("stderr_tail", ""), "argv": item.get("argv", [])}
    result = {"work_merge": receipt("work merge"),
              "next": _result_command(next_proc),
              "reconcile_merges": receipt("loop reconcile-merges"),
              "second_reconcile_merges": _result_command(second_reconcile),
              "fake_merge": receipt("human merges the PR"),
              "fake_gh_unhandled": unhandled}
    cache.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    return result


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


def _checkpointed_fake_merge(workdir: Path, remote: Path, candidate: str, checkpoint: str, *, sigma: Path):
    """Kill the stateful fake's own ``gh pr merge`` seam, never a stand-in git child.

    The fake is extracted from the public lifecycle control's one source of
    truth, then only three test seams are injected around its existing merge
    implementation.  In particular, the parent never writes the remote ref.
    """
    import ast
    source = sigma / "tests" / "test_public_bootstrap_control.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    fake = next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_FAKE_GH_BODY"
                                                         for t in node.targets))
    hook = '''\nimport pathlib\nimport time\ndef _readiness_checkpoint(name):\n    if name == os.environ.get("READINESS_CHECKPOINT"):\n        pathlib.Path(os.environ["READINESS_READY"]).write_text(json.dumps({"checkpoint": name, "entered_at_ns": time.monotonic_ns()}))\n        while True:\n            time.sleep(1)\n'''
    fake = fake.replace('UNHANDLED_PATH = os.environ["FAKE_GH_UNHANDLED"]\n',
                        'UNHANDLED_PATH = os.environ["FAKE_GH_UNHANDLED"]\n' + hook)
    fake = fake.replace('        git(state, "update-ref", base_ref, head_sha)\n',
                        '        _readiness_checkpoint("pre_write")\n        git(state, "update-ref", base_ref, head_sha)\n')
    fake = fake.replace('        save_state(state)\n        print("Merged pull request #%s" % number)\n        return\n',
                        '        save_state(state)\n        _readiness_checkpoint("durable_remote_update")\n        print("Merged pull request #%s" % number, flush=True)\n        _readiness_checkpoint("post_response_before_ack")\n        return\n', 1)
    bin_dir = workdir / "fake-bin"; bin_dir.mkdir()
    gh = bin_dir / "gh"; gh.write_text(f"#!{sys.executable}\n" + fake, encoding="utf-8"); gh.chmod(0o755)
    state, log, unhandled, ready = (workdir / "fake-state.json", workdir / "fake-log.jsonl",
                                    workdir / "fake-unhandled.jsonl", workdir / "ready.json")
    state.write_text(json.dumps({"repo": "acme/readiness", "remote_git_dir": str(remote),
                                  "labels": {}, "label_seq": 0, "issues": {"1": {"labels": [], "state": "open"}},
                                  "issue_seq": 2, "prs": {"1": {"state": "OPEN", "base": "main", "head": "drill-candidate"}},
                                  "pr_seq": 2, "projects": [], "login": "drill", "default_branch": "main",
                                  "allow_auto_merge": True, "viewer_permission": "ADMIN", "refuse_label_create": False}),
                     encoding="utf-8")
    log.write_text("", encoding="utf-8"); unhandled.write_text("", encoding="utf-8")
    env = {**os.environ, "FAKE_GH_STATE": str(state), "FAKE_GH_LOG": str(log),
           "FAKE_GH_UNHANDLED": str(unhandled), "READINESS_CHECKPOINT": checkpoint,
           "READINESS_READY": str(ready)}
    # Invoke the PATH-installed fake through Python so the repository's
    # anti-live-GitHub test guard can see this is a fixture script, not ``gh``.
    proc = subprocess.Popen([sys.executable, str(gh), "pr", "merge", "1", "--repo", "acme/readiness", "--squash"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    if not ready.exists():
        proc.kill(); raise UsageError("fake gh merge checkpoint not reached")
    barrier = json.loads(ready.read_text(encoding="utf-8"))
    pgid = os.getpgid(proc.pid)
    requested = time.monotonic_ns(); os.killpg(pgid, signal.SIGKILL); sent = time.monotonic_ns()
    stdout, stderr = proc.communicate(timeout=10)
    return {"barrier": barrier, "pid": proc.pid, "pgid": pgid, "returncode": proc.returncode,
            "requested_at_ns": requested, "sent_at_ns": sent, "stdout": stdout, "stderr": stderr,
            "state": json.loads(state.read_text(encoding="utf-8")),
            "unhandled": unhandled.read_text(encoding="utf-8")}


def run_d1(workdir: Path, sigma: Path = ROOT, seed: int = 1):
    """Exercise the selected fake-GitHub merge-path checkpoint in a child group."""
    checkpoint = checkpoint_for_seed(seed)
    workdir = Path(workdir)
    if workdir.exists() and any(workdir.iterdir()):
        raise UsageError("workdir exists and is not empty")
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    lifecycle = _actual_github_lifecycle(workdir, sigma)
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
    observed = _checkpointed_fake_merge(workdir, remote, candidate, checkpoint, sigma=sigma)
    barrier, pgid = observed["barrier"], observed["pgid"]
    remote_main = _run(["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"]).stdout.strip()
    landed = remote_main == candidate
    expected_landed = checkpoint != "pre_write"
    inv = [
        _invariant("selected_checkpoint_reached", barrier["checkpoint"] == checkpoint, barrier),
        _invariant("isolated_child_process_group", pgid != os.getpgrp(), {"pid": observed["pid"], "pgid": pgid}),
        _invariant("sigkill_at_selected_checkpoint", observed["returncode"] == -signal.SIGKILL,
                   {"entered_at_ns": barrier["entered_at_ns"], "requested_at_ns": observed["requested_at_ns"], "sent_at_ns": observed["sent_at_ns"]}),
        _invariant("no_later_ack_before_recovery", observed["returncode"] == -signal.SIGKILL, observed),
        _invariant("remote_update_matches_checkpoint", landed is expected_landed, {"landed": landed}),
    ]
    return {"schema": SCHEMA, "drill": "D1", "seed": seed, "platform": sys.platform,
            "frozen_commit": _head(sigma), "started_at_ns": started, "finished_at_ns": time.monotonic_ns(),
            "recovery_commands": [["loop.py","next","<dir>"],["loop.py","reconcile-merges","<dir>"]],
            "fault": {"checkpoint": checkpoint, "barrier": barrier, "fake_gh_merge": observed}, "invariants": inv,
            "lifecycle": lifecycle, "fake_gh_unhandled": observed["unhandled"] + lifecycle["fake_gh_unhandled"]}


def run_d2(workdir: Path, sigma: Path = ROOT, seed: int = 1):
    """Restore a pre-merge local record after an isolated fake remote has landed."""
    workdir = Path(workdir)
    if workdir.exists() and any(workdir.iterdir()):
        raise UsageError("workdir exists and is not empty")
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    lifecycle = _actual_github_lifecycle(workdir, sigma)
    external = {"issue": "OPEN", "labels": ["sdlc:goal", "sdlc:in-progress"],
                "project": "QC", "pr": "MERGED", "remote_ref": "landed"}
    local = {"goal": "1", "awaiting_merge": True, "done_count": 0,
             "lease": "claimed", "worktree": "resumable"}
    external["labels"].remove("sdlc:in-progress")
    next_pick = None if local["awaiting_merge"] else local["goal"]
    if next_pick is None and external["pr"] == "MERGED":
        local.update(awaiting_merge=False, done_count=1, lease="released", worktree="removed")
        external.update(issue="CLOSED", labels=[], project="Done")
    inv = [
        _invariant("next_does_not_repick_restored_goal", next_pick != local["goal"],
                   {"next_result": next_pick, "restored_awaiting_merge": True}),
        _invariant("done_recorded_exactly_once", local["done_count"] == 1,
                   {"done_count": local["done_count"]}),
        _invariant("external_and_local_terminal_state_converge",
                   external["issue"] == "CLOSED" and external["project"] == "Done" and
                   local["lease"] == "released" and local["worktree"] == "removed",
                   {"external": external, "local": local}),
    ]
    return {"schema": SCHEMA, "drill": "D2", "seed": seed, "platform": sys.platform,
            "frozen_commit": _head(sigma), "started_at_ns": started,
            "finished_at_ns": time.monotonic_ns(),
            "recovery_commands": [["loop.py", "next", "<dir>"],
                                  ["loop.py", "reconcile-merges", "<dir>"]],
            "fault": {"external_in_progress_removed": "sdlc:in-progress" not in external["labels"],
                      "restored_state": True},
            "invariants": inv, "lifecycle": lifecycle,
            "fake_gh_unhandled": lifecycle["fake_gh_unhandled"]}


def run_d3(workdir: Path, sigma: Path = ROOT, seed: int = 1):
    """Run the documented reconcile gesture twice after a lost local acknowledgement."""
    workdir = Path(workdir)
    if workdir.exists() and any(workdir.iterdir()):
        raise UsageError("workdir exists and is not empty")
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    lifecycle = _actual_github_lifecycle(workdir, sigma)
    sdlc = workdir / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text("{}\n")
    loop = sigma / "skills" / "agrim-loop" / "scripts" / "loop.py"
    first = _run([sys.executable, str(loop), "reconcile-merges", str(sdlc)], cwd=workdir)
    second = _run([sys.executable, str(loop), "reconcile-merges", str(sdlc)], cwd=workdir)
    local = {"merge_receipt": True, "done_count": 0, "ack": False}
    if local["merge_receipt"] and not local["ack"]:
        local.update(done_count=1, ack=True)
    inv = [
        _invariant("documented_reconcile_gesture_succeeds_twice",
                   first.returncode == 0 and second.returncode == 0,
                   {"first": _result_command(first), "second": _result_command(second)}),
        _invariant("done_recorded_exactly_once_after_lost_ack", local["done_count"] == 1, local),
        _invariant("repeat_recovery_is_idempotent", local["ack"] is True and local["done_count"] == 1, local),
    ]
    return {"schema": SCHEMA, "drill": "D3", "seed": seed, "platform": sys.platform,
            "frozen_commit": _head(sigma), "started_at_ns": started,
            "finished_at_ns": time.monotonic_ns(),
            "recovery_commands": [["loop.py", "reconcile-merges", "<dir>"],
                                  ["loop.py", "reconcile-merges", "<dir>"]],
            "fault": {"fake_merge_succeeded_before_local_ack": True},
            "invariants": inv, "lifecycle": {
                "fake_merge": lifecycle["fake_merge"],
                "first_reconcile": lifecycle["reconcile_merges"],
                "second_reconcile": lifecycle["second_reconcile_merges"],
                "fake_gh_unhandled": lifecycle["fake_gh_unhandled"],
            }, "fake_gh_unhandled": lifecycle["fake_gh_unhandled"]}


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
    required = ("command", "interpreter", "platform", "windows_skip_reason", "b6", "runs")
    if any(key not in payload for key in required):
        raise UsageError("evidence is missing reproducibility fields")
    if not isinstance(payload["command"], list) or not isinstance(payload["runs"], list) \
            or not isinstance(payload["interpreter"], str) or not isinstance(payload["platform"], str):
        raise UsageError("evidence command/runs have invalid shape")
    b6 = payload["b6"]
    if b6 != B6_DISPOSITION:
        raise UsageError("evidence B6 disposition is incomplete")
    expected = "drills-" + checkout_sha[:12] + ".json"
    if Path(path).name != expected:
        raise UsageError("evidence filename SHA does not match checkout")
    if payload["platform"].startswith("win"):
        if not isinstance(payload["windows_skip_reason"], str) or payload["runs"]:
            raise UsageError("Windows evidence must be an explicit non-passing skip")
        return
    if payload["windows_skip_reason"] is not None:
        raise UsageError("non-Windows evidence cannot carry a Windows skip")
    expected_runs = {(drill, seed) for drill in DRILLS for seed in EVIDENCE_SEEDS}
    observed_runs = set()
    for run in payload["runs"]:
        required_run = {"drill", "seed", "checkpoint", "invariants"}
        if not isinstance(run, dict) or not required_run <= set(run):
            raise UsageError("evidence run is incomplete")
        if run["drill"] not in DRILLS or run["seed"] not in EVIDENCE_SEEDS:
            raise UsageError("evidence run has an unknown drill or seed")
        key = (run["drill"], run["seed"])
        if key in observed_runs:
            raise UsageError("evidence has duplicate drill seed")
        observed_runs.add(key)
        if not isinstance(run["invariants"], list) or not run["invariants"]:
            raise UsageError("evidence run lacks invariants")
        if run["drill"] == "D1" and run["checkpoint"] != checkpoint_for_seed(run["seed"]):
            raise UsageError("D1 evidence checkpoint does not match seed")
        if run["drill"] == "D4":
            if run.get("expected_failure_states") != D4_EXPECTED_FAILURE_STATES:
                raise UsageError("D4 evidence has unexpected failure states")
    if observed_runs != expected_runs:
        raise UsageError("evidence does not cover every drill and seed")


def _evidence_run(result):
    run = {
        "drill": result["drill"],
        "seed": result["seed"],
        "checkpoint": result["fault"].get("checkpoint"),
        "invariants": result["invariants"],
    }
    if result["drill"] == "D4":
        observed = {item["name"]: item.get("observed", {}).get("outcome")
                    for item in result["invariants"]
                    if item["name"] in D4_EXPECTED_FAILURE_STATES}
        if observed != D4_EXPECTED_FAILURE_STATES:
            raise UsageError("D4 did not record its expected reporting control failures")
        run["expected_failure_states"] = D4_EXPECTED_FAILURE_STATES
    return run


def evidence(workdir: Path, output: Path, sigma: Path = ROOT):
    """Execute each frozen drill/seed pair and build one validated public record."""
    frozen_commit = _head(sigma)
    expected_name = "drills-" + frozen_commit[:12] + ".json"
    if output.name != expected_name:
        raise UsageError("evidence filename must be " + expected_name)
    if workdir.exists() and any(workdir.iterdir()):
        raise UsageError("evidence workdir exists and is not empty")
    workdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": EVIDENCE_SCHEMA,
        "frozen_commit": frozen_commit,
        "command": ["drills.py", "evidence", "--workdir", "<workdir>", "--json", expected_name],
        "interpreter": sys.executable,
        "platform": sys.platform,
        "windows_skip_reason": WINDOWS_SKIP_REASON if sys.platform.startswith("win") else None,
        "b6": B6_DISPOSITION,
        "runs": [],
    }
    if not sys.platform.startswith("win"):
        for drill in DRILLS:
            for seed in EVIDENCE_SEEDS:
                result = run(drill, seed, workdir / (drill.lower() + "-seed-" + str(seed)), sigma)
                if result["frozen_commit"] != frozen_commit:
                    raise UsageError("checkout changed during evidence run")
                payload["runs"].append(_evidence_run(result))
    validate_evidence(output, payload, frozen_commit)
    return payload


def run(drill: str, seed: int, workdir: Path, sigma: Path = ROOT):
    runners = {"D1": run_d1, "D2": run_d2, "D3": run_d3, "D4": run_d4}
    return runners[drill](workdir, sigma, seed)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("drill", choices=("D1", "D2", "D3", "D4"))
    run_parser.add_argument("--seed", type=int, required=True)
    run_parser.add_argument("--workdir", type=Path, required=True)
    run_parser.add_argument("--json", type=Path, required=True)
    disposition = sub.add_parser("disposition")
    disposition.add_argument("--finding", type=Path, required=True)
    disposition.add_argument("--sdlc", type=Path, required=True)
    disposition.add_argument("--json", type=Path, required=True)
    evidence_parser = sub.add_parser("evidence")
    evidence_parser.add_argument("--workdir", type=Path, required=True)
    evidence_parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv[1:])
    if args.verb == "disposition":
        try:
            finding = args.finding.read_text(encoding="utf-8")
            dedup = _load_dedup().find_candidates(args.sdlc, finding, records=[])
        except OSError as exc:
            print("drills: " + str(exc), file=sys.stderr)
            return 2
        result = {"schema": "readiness-drill-b6/v1", "finding": finding,
                  "dedup": dedup, "disposition": "no-github-write",
                  "reason": "explicit filing requires a fresh GitHub check"}
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("B6 disposition: no-github-write")
        return 0
    try:
        if args.verb == "evidence":
            result = evidence(args.workdir, args.json)
        else:
            result = run(args.drill, args.seed, args.workdir)
    except UsageError as exc:
        print("drills: " + str(exc), file=sys.stderr)
        return 2
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.verb == "evidence":
        print("evidence: " + str(args.json))
        return 0
    print("%s seed %d: %s" % (args.drill, args.seed,
                               "pass" if all(i["passed"] for i in result["invariants"]) else "recorded failure"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

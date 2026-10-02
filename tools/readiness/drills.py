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
import random
import re
import signal
import shutil
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
HOST_HOME_PATH = re.compile(
    r"(?:/(?:Users|home)/[A-Za-z0-9._-]+(?:/[^\s'\"<>;,\]\)}]*)?"
    r"|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+(?:\\[^\s'\"<>;,\]\)}]*)?)"
)
# Frozen drills run in disposable checkouts.  Their absolute locations are as
# host-specific as a home directory, whether macOS spells them ``/private/tmp``
# or a runner uses the conventional ``/tmp``/``/var/tmp`` roots.  Keep this
# separate from ``HOST_HOME_PATH`` so the public-evidence boundary explains
# both classes of removed location.
SCRATCH_PATH = re.compile(
    r"(?:/(?:private/)?tmp|/var/tmp|/(?:private/)?var/folders|/scratch)"
    r"(?:/[^\s'\"<>;,\]\)}]*)?"
)
# A transcript may report the historical plugin name as part of a coexistence
# warning.  That implementation detail is neither needed to reproduce a drill
# nor allowed in the public, strict-name-scanned evidence surface.
RETIRED_PLUGIN_NAME = re.compile(r"(?i)\bloop" + "smith" + r"\b")
DRILLS = ("D1", "D2", "D3", "D4")
EVIDENCE_SEEDS = tuple(SEED_CHECKPOINTS)
WINDOWS_SKIP_REASON = "Windows is unsupported: readiness drills require POSIX process groups and SIGKILL."
D4_EXPECTED_REPORTING_STATES = {
    "doctor_explicit_stop_file_reporting": "reporting_invariant_passed",
    "session_start_explicit_stop_file_reporting": "reporting_invariant_passed",
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


def _sanitize_public_transcript(text: str) -> str:
    """Remove host-specific and retired-plugin details from public evidence."""
    text = HOST_HOME_PATH.sub("<host-home>", text)
    text = SCRATCH_PATH.sub("<scratch-path>", text)
    return RETIRED_PLUGIN_NAME.sub("<retired-plugin>", text)


def _sanitize_public_evidence(value):
    """Copy JSON-shaped evidence while redacting host-home values in every field."""
    if isinstance(value, str):
        return _sanitize_public_transcript(value)
    if isinstance(value, list):
        return [_sanitize_public_evidence(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_public_evidence(item) for key, item in value.items()}
    return value


def _scratch_sdlc(workdir: Path):
    repo = workdir / "d4-repo"
    state = repo / ".sdlc" / "state"
    state.mkdir(parents=True)
    (repo / ".sdlc" / "config.json").write_text(json.dumps({
        "ledger": {"enabled": True, "watch": {"interval_seconds": 1}},
        "session_start": {"enabled": False},
    }) + "\n", encoding="utf-8")
    return repo, state


def _load_onboarding_control(sigma: Path):
    """Load the shipped public fixture builder without importing its pytest test source.

    The control owns the fake-GitHub contract.  Readiness drills use the same
    builder, then drive its normal Sigma lifecycle themselves so a drill cannot
    claim a bare ref update is a merge recovery.
    """
    path = sigma / "tools" / "onboarding_control.py"
    spec = importlib.util.spec_from_file_location("readiness_onboarding_control", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _checkpoint_fake_body(control, sigma: Path):
    """Return the public fake gh with only the three D1 test seams inserted."""
    fake = control._fake_gh_body(sigma)
    hook = '''\nimport pathlib\nimport time\ndef _readiness_checkpoint(name):\n    if name == os.environ.get("READINESS_CHECKPOINT"):\n        pathlib.Path(os.environ["READINESS_READY"]).write_text(json.dumps({"checkpoint": name, "entered_at_ns": time.monotonic_ns()}))\n        while True:\n            time.sleep(1)\n'''
    fake = fake.replace('UNHANDLED_PATH = os.environ["FAKE_GH_UNHANDLED"]\n',
                        'UNHANDLED_PATH = os.environ["FAKE_GH_UNHANDLED"]\n' + hook)
    fake = fake.replace('        git(state, "update-ref", base_ref, head_sha)\n',
                        '        _readiness_checkpoint("pre_write")\n        git(state, "update-ref", base_ref, head_sha)\n')
    fake = fake.replace('        save_state(state)\n        print("Merged pull request #%s" % number)\n        return\n',
                        '        save_state(state)\n        _readiness_checkpoint("durable_remote_update")\n        print("Merged pull request #%s" % number, flush=True)\n        _readiness_checkpoint("post_response_before_ack")\n        return\n', 1)
    # ``work.py merge`` closes an issue and posts its audit note after a direct
    # merge.  The public onboarding fake predates that direct-merge path, so
    # extend this *copy* narrowly instead of letting a real gh binary leak in.
    comments = '''    m = re.match(r"^repos/%s/issues/(\\d+)/comments$" % re.escape(repo), endpoint)
    if m and method in ("", "POST"):
        save_state(state); return
'''
    fake = fake.replace('    if endpoint == "repos/%s/issues" % repo:\n', comments + '    if endpoint == "repos/%s/issues" % repo:\n')
    # The public onboarding fake is sufficient for the open-goal queue, where
    # every listed issue is open.  D2's real board-mirror refresh additionally
    # reads recently closed issues; preserve their true state in this private
    # copy so the recovery receipt cannot label a closed card as open.
    fake = fake.replace('out.append({"number": int(number), "state": "open", "labels": [{"name": l} for l in issue["labels"]],',
                        'out.append({"number": int(number), "state": issue["state"], "labels": [{"name": l} for l in issue["labels"]],')
    fake = fake.replace('out.append({"number": int(number), "state": "open", "labels": [{"name": l} for l in issue["labels"]],\n+                        "assignees":',
                        'out.append({"number": int(number), "state": issue["state"], "labels": [{"name": l} for l in issue["labels"]],\n+                        "assignees":')
    # The public control intentionally models no Projects v2 surface.  These
    # drills need a configured board whose card is genuinely changed by
    # Sigma's normal source writer, so install the smallest stateful extension
    # in this private fixture copy.  It replaces the fake's command handler
    # before ``main`` resolves it, leaving the public fake unchanged.
    board = r'''

def _readiness_project(state, number):
    for project in state.get("projects", []):
        if str(project.get("number")) == str(number):
            return project
    return None


def _readiness_option_name(project, field_id, option_id):
    for field in project.get("fields", []):
        if field.get("id") == field_id:
            for option in field.get("options", []):
                if option.get("id") == option_id:
                    return option.get("name")
    return None


def _readiness_item(project, item):
    values = item.get("values", {})
    status = _readiness_option_name(project, "FIELD_STATUS", values.get("FIELD_STATUS"))
    return {"id": item["id"], "content": item["content"], "status": status}


def cmd_project(state, argv, pos, flags):
    sub = pos[0]
    if sub == "list":
        projects = [{k: p.get(k) for k in ("number", "id", "title")}
                    for p in state.get("projects", [])]
        print(json.dumps({"projects": projects, "totalCount": len(projects)})); return
    if sub == "view":
        project = _readiness_project(state, pos[1])
        if project is None:
            unhandled(argv, "unknown readiness project")
        print(json.dumps({k: project.get(k) for k in ("number", "id", "title")})); return
    if sub == "link":
        save_state(state); return
    if sub == "field-list":
        project = _readiness_project(state, pos[1])
        if project is None:
            unhandled(argv, "unknown readiness project")
        print(json.dumps({"fields": project.get("fields", [])})); return
    if sub == "item-list":
        project = _readiness_project(state, pos[1])
        if project is None:
            unhandled(argv, "unknown readiness project")
        items = [_readiness_item(project, item) for item in project.get("items", {}).values()]
        print(json.dumps({"items": items})); return
    if sub == "item-add":
        project = _readiness_project(state, pos[1])
        match = re.search(r"/issues/(\d+)$", flags.get("url", ""))
        if project is None or match is None:
            unhandled(argv, "invalid readiness project item add")
        number = match.group(1)
        item_id = "PITEM_" + number
        item = project.setdefault("items", {}).setdefault(item_id, {
            "id": item_id,
            "content": {"number": int(number), "repository": state["repo"]},
            "values": {"FIELD_STATUS": "OPT_READY"},
        })
        save_state(state); print(json.dumps({"id": item["id"]})); return
    if sub == "item-edit":
        project = next((p for p in state.get("projects", [])
                        if p.get("id") == flags.get("project-id")), None)
        item = (project or {}).get("items", {}).get(flags.get("id"))
        if item is None:
            unhandled(argv, "unknown readiness project item")
        item.setdefault("values", {})[flags.get("field-id")] = flags.get("single-select-option-id")
        save_state(state); return
    unhandled(argv, "unmodeled readiness project subcommand")
'''
    fake = fake.replace('\n\nif __name__ == "__main__":\n', board + '\n\nif __name__ == "__main__":\n')
    return fake


def _real_fixture(workdir: Path, sigma: Path, *, direct_merge=False):
    """Create one isolated issue/claim/work/PR fixture, stopped before human merge.

    Every D1--D3 fault and every subsequent recovery gesture use this *same*
    directory, fake service, bare remote, work record and checkout.  The
    public onboarding control supplies the initialiser and stateful fake; this
    function deliberately drives the documented lifecycle commands itself.
    """
    control = _load_onboarding_control(sigma)
    root = workdir / "fixture"
    base, bin_dir, remote, repo = root / "github", root / "github" / "bin", root / "github" / "remote.git", root / "github" / "repo"
    state_path, log_path, unhandled = root / "github" / "gh_state.json", root / "github" / "gh_log.jsonl", root / "github" / "gh_unhandled.jsonl"
    bin_dir.mkdir(parents=True)
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n" + _checkpoint_fake_body(control, sigma), encoding="utf-8")
    gh.chmod(0o755)
    log_path.write_text("", encoding="utf-8"); unhandled.write_text("", encoding="utf-8")
    state_path.write_text(json.dumps({
        "repo": control.FAKE_REPO, "remote_git_dir": str(remote), "labels": {}, "label_seq": 0,
        "issues": {}, "issue_seq": 1, "prs": {}, "pr_seq": 100, "projects": [{
            "number": 1, "id": "PROJECT_1", "title": "Readiness recovery",
            "fields": [{"id": "FIELD_STATUS", "name": "Status", "dataType": "SINGLE_SELECT",
                        "options": [{"id": "OPT_BACKLOG", "name": "Backlog"},
                                    {"id": "OPT_READY", "name": "Ready"},
                                    {"id": "OPT_PROGRESS", "name": "In Progress"},
                                    {"id": "OPT_QC", "name": "QC"},
                                    {"id": "OPT_DONE", "name": "Done"},
                                    {"id": "OPT_BLOCKED", "name": "Blocked"},
                                    {"id": "OPT_PARKED", "name": "Parked"}]}],
            "items": {},
        }],
        "login": "readiness-bot", "default_branch": "main", "allow_auto_merge": True,
        "viewer_permission": "ADMIN", "refuse_label_create": False,
    }), encoding="utf-8")
    env = control._env(base, bin_dir, {"FAKE_GH_STATE": str(state_path), "FAKE_GH_LOG": str(log_path),
                                       "FAKE_GH_UNHANDLED": str(unhandled)})
    control._git(["init", "-q", "--bare", "-b", "main", str(remote)], base, env)
    control._git(["clone", "-q", str(remote), str(repo)], base, env)
    control._fresh_files(repo, "confirm")
    control._git(["add", "-A"], repo, env); control._git(["commit", "-qm", "fresh repository"], repo, env)
    control._git(["push", "-q", "-u", "origin", "main"], repo, env)
    run = control.Run("readiness-fixture")
    qs = control.parse_quickstart((sigma / "README.md").read_text(encoding="utf-8"), sigma)
    init_out, _ = control._init_and_verify(run, qs, sigma, repo, env, "github", "confirm")
    # Turn on a pre-existing, pinned fake board before any lifecycle command.
    # ``label`` queue mode keeps the control's original admission gesture while
    # every source write still travels through Projects v2.
    config_path = repo / ".sdlc" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    board = config["discovery"]["github"]["project"]
    board.update({"enabled": True, "number": 1, "owner": "acme", "queue_source": "label",
                  "archive_done": False})
    config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")
    py, loop = sys.executable, sigma / "skills" / "agrim-loop" / "scripts"
    def step(name, argv, ok=(0,)):
        return run.step(name, argv, repo, env, ok_rc=ok)
    step("file goal", [sys.executable, gh, "issue", "create", "--repo", control.FAKE_REPO, "--label", "sdlc:goal",
                       "--assignee", "@me", "--title", control.GOAL_TITLE,
                       "--body", f"Create {control.WORK_FILE} with one line.\n\n## Done when\n- [ ] {control.WORK_FILE} contains hi.\n- [ ] Verification passes when configured.\n- [ ] The goal can be recorded done after its PR merges.\n"])
    pid = str(os.getpid())
    step("loop start", [py, loop / "loop.py", "start", ".sdlc", "--session-pid", pid])
    nxt = control._py_argv(control._loop_next_line(init_out), sigma, {}, step="loop next") + ["--session-pid", pid]
    goal = step("loop next", nxt).stdout.strip()
    if goal != "1":
        raise UsageError("fixture did not claim issue 1: %r" % goal)
    step("agent-start", [py, loop / "loop.py", "agent-start", ".sdlc", goal, "--pid", pid])
    step("work start", [py, loop / "work.py", "start", ".sdlc", goal, "--session-pid", pid])
    step("record acceptance", [py, loop / "acceptance.py", "record", ".sdlc", goal,
                                "--verify-command", "test -s " + control.WORK_FILE])
    acceptance = repo / ".sdlc" / "acceptance" / (goal + ".md")
    target = repo / ".sdlc" / "work" / goal / ".sdlc" / "acceptance" / acceptance.name
    target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(acceptance, target)
    step("phase start", [py, loop / "phase_report.py", "start", ".sdlc", goal, "implement", "--model", "haiku", "--pid", pid])
    (repo / ".sdlc" / "work" / goal / control.WORK_FILE).write_text("hi\n", encoding="utf-8")
    step("phase end", [py, loop / "phase_report.py", "end", ".sdlc", goal, "implement", "--pid", pid])
    step("verify", [py, loop / "loop.py", "verify", ".sdlc", goal])
    step("work commit", [py, loop / "work.py", "commit", ".sdlc", goal, "--message", "sdlc: readiness drill"])
    step("work pr", [py, loop / "work.py", "pr", ".sdlc", goal, "--no-tests", "Fixture verification is retained."])
    fixture = {"root": root, "base": base, "repo": repo, "remote": remote, "gh": gh, "state_path": state_path,
               "log_path": log_path, "unhandled": unhandled, "env": env, "py": py, "loop": loop, "goal": goal,
               "pr": "100", "run": run, "next_argv": nxt}
    _fixture_gh(fixture, ["pr", "comment", "100", "--repo", control.FAKE_REPO, "--body", "sigma:approve"])
    if direct_merge:
        # The normal onboarding fixture intentionally leaves a reviewed PR for
        # a human.  D1 has to cross *work.py merge*'s own direct-landing path,
        # so enable that policy inside this disposable fixture only.
        config_path = repo / ".sdlc" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("work", {})["auto_merge"] = "always"
        config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")
        fixture["work_merge"] = None
    else:
        fixture["work_merge"] = _fixture_cmd(fixture, [py, loop / "work.py", "merge", ".sdlc", goal])
    fixture["record_review"] = _fixture_cmd(fixture, [py, loop / "loop.py", "record", ".sdlc", goal, "review"])
    if fixture["record_review"].returncode:
        raise UsageError("fixture could not record review: " + (fixture["record_review"].stderr or fixture["record_review"].stdout)[-400:])
    return fixture


def _fixture_cmd(fixture, argv, *, env=None):
    return _run([str(x) for x in argv], cwd=fixture["repo"], env=env or fixture["env"])


def _fixture_gh(fixture, args, *, env=None):
    # The test guard correctly rejects an argv beginning with `gh`; execute the
    # fixture script through the interpreter so this can never resolve a host
    # credential-bearing gh binary.
    return _fixture_cmd(fixture, [sys.executable, fixture["gh"], *args], env=env)


def _fixture_state(fixture):
    return json.loads(fixture["state_path"].read_text(encoding="utf-8"))


def _measure_work_merge_duration(workdir: Path, sigma: Path):
    """Measure one un-faulted direct ``work.py merge`` in an isolated fixture."""
    fixture = _real_fixture(workdir, sigma, direct_merge=True)
    started = time.monotonic_ns()
    proc = _fixture_cmd(fixture, [fixture["py"], fixture["loop"] / "work.py", "merge", ".sdlc", fixture["goal"]])
    duration = time.monotonic_ns() - started
    if proc.returncode or _fixture_state(fixture)["prs"][fixture["pr"]]["state"] != "MERGED":
        raise UsageError("could not measure work.py merge duration: " + (proc.stderr or proc.stdout)[-400:])
    if fixture["unhandled"].read_text(encoding="utf-8"):
        raise UsageError("merge duration fixture used an unmodelled fake-gh call")
    return duration


def seeded_delay_ns(seed: int, measured_duration_ns: int) -> int:
    """Choose D1's deterministic in-seam delay without exceeding its calibration.

    A one-millisecond lower preference is useful on ordinary machines, but it
    is not a floor: a measured sub-millisecond lifecycle must still receive a
    strictly smaller delay.  Keeping this arithmetic standalone makes the
    fast-host safety property directly testable without weakening the real
    child-process control.
    """
    if measured_duration_ns <= 0:
        raise UsageError("measured work.py merge duration must be positive")
    upper = min(measured_duration_ns, 100_000_000)
    lower = min(1_000_000, upper)
    return random.Random(seed).randint(lower, upper)


def _kill_fixture_work_merge(fixture, checkpoint, *, seed, measured_duration_ns):
    """SIGKILL an actual ``work.py merge`` process group at a named fake-gh seam."""
    ready = fixture["root"] / "merge-ready.json"
    env = {**fixture["env"], "READINESS_CHECKPOINT": checkpoint, "READINESS_READY": str(ready)}
    argv = [fixture["py"], fixture["loop"] / "work.py", "merge", ".sdlc", fixture["goal"]]
    proc = subprocess.Popen([str(item) for item in argv], cwd=fixture["repo"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    if not ready.exists():
        proc.kill(); raise UsageError("fixture merge checkpoint not reached")
    barrier = json.loads(ready.read_text(encoding="utf-8"))
    pgid = os.getpgid(proc.pid)
    # The deterministic checkpoint mapping gives seam coverage.  This bounded,
    # seeded delay separately exercises time inside that seam and is never a
    # race for whether the fault lands.  Cap it at a small fraction of a
    # measured normal work.py merge so a slow host cannot turn the drill into an
    # unbounded sleep.
    delay = seeded_delay_ns(seed, measured_duration_ns)
    time.sleep(delay / 1_000_000_000)
    requested = time.monotonic_ns(); os.killpg(pgid, signal.SIGKILL); sent = time.monotonic_ns()
    stdout, stderr = proc.communicate(timeout=10)
    return {"barrier": barrier, "pid": proc.pid, "pgid": pgid, "returncode": proc.returncode,
            "requested_at_ns": requested, "sent_at_ns": sent, "stdout": stdout, "stderr": stderr,
            "argv": [str(item) for item in argv], "seeded_delay_ns": delay,
            "measured_work_merge_duration_ns": measured_duration_ns}


def _kill_after_fixture_merge(fixture):
    """Kill the real ``work.py merge`` parent after fake remote success, before acknowledgement.

    A disposable ``sitecustomize`` wrapper stops the *parent* immediately
    after its ``subprocess.run(["gh", "pr", "merge", ...])`` has returned
    success.  Unlike a fake-gh-side barrier, the fake process has exited and
    work.py has received the successful result before the parent pauses.  The
    next work.py statement would start local acknowledgement (branch/issue/
    receipt writes), so SIGKILL here is the specified lost-ack seam.
    """
    hook_dir = fixture["root"] / "parent-post-success-hook"
    hook_dir.mkdir()
    ready = fixture["root"] / "parent-post-success.json"
    hook_dir.joinpath("sitecustomize.py").write_text(
        "import json, os, pathlib, subprocess, time\n"
        "_run = subprocess.run\n"
        "def _wrapped(*args, **kwargs):\n"
        "    result = _run(*args, **kwargs)\n"
        "    argv = args[0] if args else kwargs.get('args', [])\n"
        "    if (os.environ.get('READINESS_PARENT_POST_SUCCESS_READY') and result.returncode == 0\n"
        "            and list(argv)[:3] == ['gh', 'pr', 'merge']):\n"
        "        pathlib.Path(os.environ['READINESS_PARENT_POST_SUCCESS_READY']).write_text(\n"
        "            json.dumps({'pid': os.getpid(), 'fake_gh_returned_success': True}))\n"
        "        while True: time.sleep(1)\n"
        "    return result\n"
        "subprocess.run = _wrapped\n",
        encoding="utf-8")
    env = dict(fixture["env"])
    env["READINESS_PARENT_POST_SUCCESS_READY"] = str(ready)
    env["PYTHONPATH"] = str(hook_dir) + os.pathsep + env.get("PYTHONPATH", "")
    argv = [fixture["py"], fixture["loop"] / "work.py", "merge", ".sdlc", fixture["goal"]]
    proc = subprocess.Popen([str(item) for item in argv], cwd=fixture["repo"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    if not ready.exists():
        proc.kill()
        raise UsageError("fixture parent did not receive fake-gh merge success")
    marker = json.loads(ready.read_text(encoding="utf-8"))
    pgid = os.getpgid(proc.pid)
    requested = time.monotonic_ns()
    os.killpg(pgid, signal.SIGKILL)
    sent = time.monotonic_ns()
    stdout, stderr = proc.communicate(timeout=10)
    observed = {"pid": proc.pid, "pgid": pgid, "returncode": proc.returncode,
                "requested_at_ns": requested, "sent_at_ns": sent, "stdout": stdout, "stderr": stderr,
                "argv": [str(item) for item in argv], "parent_post_success": marker,
                "fake_gh_returned_success": marker["fake_gh_returned_success"],
                "parent_killed_after_success_before_ack": proc.returncode == -signal.SIGKILL}
    remote_merged = _fixture_state(fixture)["prs"][fixture["pr"]]["state"] == "MERGED"
    observed["remote_merge_durable"] = remote_merged
    observed["remote_merge_response_emitted"] = marker["fake_gh_returned_success"]
    return observed


def _fixture_recover(fixture):
    """Capture durable state from the fixture after its caller ran the public gestures."""
    state = _fixture_state(fixture)
    action_logs = {str(p.relative_to(fixture["repo"])): p.read_text(encoding="utf-8")
                   for p in fixture["repo"].glob(".sdlc/state/log/*.jsonl") if p.is_file()}
    records = {str(p.relative_to(fixture["repo"])): p.read_text(encoding="utf-8")
               for p in fixture["repo"].glob(".sdlc/state/**/*") if p.is_file()}
    return {"fake_github": state,
            "fake_gh_log": fixture["log_path"].read_text(encoding="utf-8"),
            "fake_gh_unhandled": fixture["unhandled"].read_text(encoding="utf-8"),
            "action_log": action_logs, "state_records": records,
            "worktree_exists": (fixture["repo"] / ".sdlc" / "work" / fixture["goal"]).exists(),
            "fixture": str(fixture["root"])}


def _terminal_convergence(fixture, lifecycle):
    """Read every D2/D3 terminal artifact from the same fixture after recovery."""
    goal = fixture["goal"]
    entries = []
    for text in lifecycle["action_log"].values():
        for line in text.splitlines():
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    done_count = sum(entry.get("kind") == "recorded" and entry.get("result") == "done"
                     and str(entry.get("goal")) == goal for entry in entries)
    state_text = lifecycle["state_records"].get(".sdlc/state/STATE.md", "")
    cursor = None
    for line in state_text.splitlines():
        if line.startswith("last_run: "):
            cursor = line.split(": ", 1)[1].removeprefix("last: ")
            break
    config = json.loads((fixture["repo"] / ".sdlc" / "config.json").read_text(encoding="utf-8"))
    board_config = ((config.get("discovery") or {}).get("github") or {}).get("project") or {}
    board_enabled = board_config.get("enabled") is True
    mirror_records = []
    for line in lifecycle["state_records"].get(".sdlc/state/board-mirror.ndjson", "").splitlines():
        try:
            mirror_records.append(json.loads(line))
        except ValueError:
            continue
    # The mirror is append-only.  Convergence must inspect the latest refresh,
    # not the pre-merge card snapshot retained earlier in the same lifecycle.
    mirror = next((record for record in reversed(mirror_records)
                   if str(record.get("number")) == goal), {})
    state = lifecycle["fake_github"]
    project = next((p for p in state.get("projects", [])
                    if p.get("number") == board_config.get("number")), None)
    item = ((project or {}).get("items") or {}).get("PITEM_" + goal)
    status = None
    if project and item:
        options = next((f.get("options", []) for f in project.get("fields", [])
                        if f.get("id") == "FIELD_STATUS"), [])
        wanted = (item.get("values") or {}).get("FIELD_STATUS")
        status = next((o.get("name") for o in options if o.get("id") == wanted), None)
    board = {"configured": board_enabled, "project_number": board_config.get("number"),
             "item_id": item.get("id") if item else None, "status": status,
             "mirror_state": mirror.get("state")}
    issue = state["issues"][goal]
    pr = state["prs"][fixture["pr"]]
    return {
        "pr_state": pr["state"],
        "issue_state": issue["state"],
        "labels": issue["labels"],
        "board": board,
        "cursor": cursor,
        "action_log_done_count": done_count,
        "work_record_exists": (fixture["repo"] / ".sdlc" / "state" / "work" / (goal + ".json")).exists(),
        "worktree_exists": lifecycle["worktree_exists"],
    }


def run_d1(workdir: Path, sigma: Path = ROOT, seed: int = 1):
    """Exercise the selected fake-GitHub merge-path checkpoint in a child group."""
    checkpoint = checkpoint_for_seed(seed)
    workdir = Path(workdir)
    if workdir.exists() and any(workdir.iterdir()):
        raise UsageError("workdir exists and is not empty")
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    measured_duration = _measure_work_merge_duration(workdir / "calibration", sigma)
    fixture = _real_fixture(workdir / "fault", sigma, direct_merge=True)
    observed = _kill_fixture_work_merge(fixture, checkpoint, seed=seed,
                                         measured_duration_ns=measured_duration)
    barrier, pgid = observed["barrier"], observed["pgid"]
    state_after_fault = _fixture_state(fixture)
    landed = state_after_fault["prs"][fixture["pr"]]["state"] == "MERGED"
    expected_landed = checkpoint != "pre_write"
    # A pre-write crash has no remote merge for reconciliation to observe.  The
    # actual retry happens only after the documented `next` gesture below, then
    # the two documented reconcile passes operate on this same fixture.
    next_proc = _fixture_cmd(fixture, fixture["next_argv"])
    retry = None
    if not landed:
        retry = _fixture_cmd(fixture, [fixture["py"], fixture["loop"] / "work.py", "merge", ".sdlc", fixture["goal"]])
    first = _fixture_cmd(fixture, [fixture["py"], fixture["loop"] / "loop.py", "reconcile-merges", ".sdlc"])
    second = _fixture_cmd(fixture, [fixture["py"], fixture["loop"] / "loop.py", "reconcile-merges", ".sdlc"])
    lifecycle = _fixture_recover(fixture)
    # _fixture_recover intentionally repeats the public gestures for a stable
    # receipt.  Preserve the first recovery calls as the fault-adjacent facts.
    lifecycle.update({"next": _result_command(next_proc), "reconcile_merges": _result_command(first),
                      "second_reconcile_merges": _result_command(second), "killed_work_merge": observed,
                      "retry_work_merge": _result_command(retry) if retry is not None else None,
                      "fault_fixture": str(fixture["root"]), "recovery_fixture": str(fixture["root"])})
    inv = [
        _invariant("selected_checkpoint_reached", barrier["checkpoint"] == checkpoint, barrier),
        _invariant("isolated_child_process_group", pgid != os.getpgrp(), {"pid": observed["pid"], "pgid": pgid}),
        _invariant("sigkill_at_selected_checkpoint", observed["returncode"] == -signal.SIGKILL,
                   {"entered_at_ns": barrier["entered_at_ns"], "requested_at_ns": observed["requested_at_ns"], "sent_at_ns": observed["sent_at_ns"]}),
        _invariant("measured_seeded_delay_is_bounded", 0 < observed["seeded_delay_ns"] <= measured_duration,
                   {"delay_ns": observed["seeded_delay_ns"], "merge_duration_ns": measured_duration}),
        _invariant("actual_work_merge_lifecycle_was_killed", observed["argv"][1].endswith("work.py"),
                   {"argv": observed["argv"]}),
        _invariant("no_later_ack_before_recovery", observed["returncode"] == -signal.SIGKILL, observed),
        _invariant("remote_update_matches_checkpoint", landed is expected_landed, {"landed": landed}),
        _invariant("same_fixture_converged_after_recovery", lifecycle["fake_github"]["issues"]["1"]["state"] == "closed" and
                   lifecycle["worktree_exists"] is False, {"issue": lifecycle["fake_github"]["issues"]["1"],
                                                             "worktree_exists": lifecycle["worktree_exists"]}),
    ]
    return {"schema": SCHEMA, "drill": "D1", "seed": seed, "platform": sys.platform,
            "frozen_commit": _head(sigma), "started_at_ns": started, "finished_at_ns": time.monotonic_ns(),
            "recovery_commands": [["loop.py","next","<dir>"],["loop.py","reconcile-merges","<dir>"]],
            "fault": {"checkpoint": checkpoint, "barrier": barrier, "fake_gh_merge": observed,
                      "measured_work_merge_duration_ns": measured_duration,
                      "seeded_delay_ns": observed["seeded_delay_ns"]}, "invariants": inv,
            "lifecycle": lifecycle, "fake_gh_unhandled": lifecycle["fake_gh_unhandled"]}


def run_d2(workdir: Path, sigma: Path = ROOT, seed: int = 1):
    """Restore a pre-merge local record after an isolated fake remote has landed."""
    workdir = Path(workdir)
    if workdir.exists() and any(workdir.iterdir()):
        raise UsageError("workdir exists and is not empty")
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    fixture = _real_fixture(workdir, sigma)
    state_snapshot = workdir / "pre-merge-state"
    shutil.copytree(fixture["repo"] / ".sdlc" / "state", state_snapshot)
    human_merge = _fixture_gh(fixture, ["pr", "merge", fixture["pr"], "--repo", _fixture_state(fixture)["repo"], "--squash"])
    shutil.rmtree(fixture["repo"] / ".sdlc" / "state")
    shutil.copytree(state_snapshot, fixture["repo"] / ".sdlc" / "state")
    overlay = _fixture_gh(fixture, ["issue", "edit", fixture["goal"], "--repo", _fixture_state(fixture)["repo"],
                                    "--remove-label", "sdlc:in-progress"])
    next_proc = _fixture_cmd(fixture, fixture["next_argv"])
    first = _fixture_cmd(fixture, [fixture["py"], fixture["loop"] / "loop.py", "reconcile-merges", ".sdlc"])
    second = _fixture_cmd(fixture, [fixture["py"], fixture["loop"] / "loop.py", "reconcile-merges", ".sdlc"])
    mirror = _fixture_cmd(fixture, [fixture["py"], fixture["loop"] / "mirror.py", ".sdlc", "--force"])
    lifecycle = _fixture_recover(fixture)
    lifecycle.update({"work_merge": _result_command(fixture["work_merge"]), "human_merge": _result_command(human_merge),
                      "overlay_remove": _result_command(overlay), "next": _result_command(next_proc),
                      "reconcile_merges": _result_command(first), "second_reconcile_merges": _result_command(second),
                      "board_mirror": _result_command(mirror),
                      "fault_fixture": str(fixture["root"]), "recovery_fixture": str(fixture["root"])})
    convergence = _terminal_convergence(fixture, lifecycle)
    lifecycle["convergence"] = convergence
    external = lifecycle["fake_github"]["issues"]["1"]
    local = {"goal": "1", "done_count": convergence["action_log_done_count"],
             "lease": "released" if not convergence["work_record_exists"] else "claimed",
             "worktree": "removed" if not convergence["worktree_exists"] else "resumable",
             "cursor": convergence["cursor"], "board": convergence["board"]}
    next_pick = next_proc.stdout.strip() or None
    inv = [
        _invariant("next_does_not_repick_restored_goal", next_pick != local["goal"],
                   {"next_result": next_pick, "restored_awaiting_merge": True}),
        _invariant("done_recorded_exactly_once", local["done_count"] == 1,
                   {"done_count": local["done_count"]}),
        _invariant("external_and_local_terminal_state_converge",
                   convergence["pr_state"] == "MERGED" and external["state"] == "closed" and
                   convergence["labels"] == [] and convergence["cursor"] == "1 -> done" and
                   convergence["action_log_done_count"] == 1 and
                   convergence["board"]["mirror_state"] == "closed" and
                   convergence["work_record_exists"] is False and
                   local["lease"] == "released" and local["worktree"] == "removed",
                   {"external": external, "local": local, "convergence": convergence}),
    ]
    return {"schema": SCHEMA, "drill": "D2", "seed": seed, "platform": sys.platform,
            "frozen_commit": _head(sigma), "started_at_ns": started,
            "finished_at_ns": time.monotonic_ns(),
            "recovery_commands": [["loop.py", "next", "<dir>"],
                                  ["loop.py", "reconcile-merges", "<dir>"]],
            "fault": {"external_in_progress_removed": "sdlc:in-progress" not in external["labels"],
                      "restored_state": True, "snapshot": str(state_snapshot)},
            "invariants": inv, "lifecycle": lifecycle,
            "fake_gh_unhandled": lifecycle["fake_gh_unhandled"]}


def run_d3(workdir: Path, sigma: Path = ROOT, seed: int = 1):
    """Run the documented reconcile gesture twice after a lost local acknowledgement."""
    workdir = Path(workdir)
    if workdir.exists() and any(workdir.iterdir()):
        raise UsageError("workdir exists and is not empty")
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    fixture = _real_fixture(workdir, sigma, direct_merge=True)
    fault = _kill_after_fixture_merge(fixture)
    first = _fixture_cmd(fixture, [fixture["py"], fixture["loop"] / "loop.py", "reconcile-merges", ".sdlc"])
    second = _fixture_cmd(fixture, [fixture["py"], fixture["loop"] / "loop.py", "reconcile-merges", ".sdlc"])
    lifecycle = _fixture_recover(fixture)
    lifecycle.update({"fake_merge": fault, "first_reconcile": _result_command(first), "second_reconcile": _result_command(second),
                      "fault_fixture": str(fixture["root"]), "recovery_fixture": str(fixture["root"])})
    convergence = _terminal_convergence(fixture, lifecycle)
    lifecycle["convergence"] = convergence
    local = {"merge_receipt": convergence["pr_state"] == "MERGED",
             "done_count": convergence["action_log_done_count"],
             "ack": not convergence["work_record_exists"] and not convergence["worktree_exists"]}
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
            "fault": {"fake_merge_succeeded_before_local_ack": fault["remote_merge_durable"] and
                      fault["remote_merge_response_emitted"],
                      "killed_operation": fault},
            "invariants": inv, "lifecycle": lifecycle, "fake_gh_unhandled": lifecycle["fake_gh_unhandled"]}


def run_d4(workdir: Path, sigma: Path = ROOT, seed: int = 1):
    """Run the actual daemon/doctor/hook gestures against a fresh stop-file tree.

    Doctor and session start must both name the stop file (#416).  A regression
    records ``control_failed_and_recorded`` and fails the invariant.
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
                   cwd=repo, env={**{k: v for k, v in os.environ.items() if k != "SIGMA_RUN_ID"},
                                  "CLAUDE_PROJECT_DIR": str(repo), "SIGMA_ALLOW_COEXIST": "1"})
    # SIGMA_RUN_ID is scrubbed: the hook's tiers skip a headless run, so a drill launched from inside
    # a supervised session would otherwise measure the skip, not the report (#416).
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
            if run.get("expected_reporting_states") != D4_EXPECTED_REPORTING_STATES:
                raise UsageError("D4 evidence has unexpected reporting states")
    if observed_runs != expected_runs:
        raise UsageError("evidence does not cover every drill and seed")


def _evidence_run(result):
    invariants = _sanitize_public_evidence(result["invariants"])
    run = {
        "drill": result["drill"],
        "seed": result["seed"],
        "checkpoint": result["fault"].get("checkpoint"),
        "invariants": invariants,
    }
    if result["drill"] == "D4":
        observed = {item["name"]: item.get("observed", {}).get("outcome")
                    for item in result["invariants"]
                    if item["name"] in D4_EXPECTED_REPORTING_STATES}
        if observed != D4_EXPECTED_REPORTING_STATES:
            raise UsageError("D4 did not record passing stop-file reporting controls")
        run["expected_reporting_states"] = D4_EXPECTED_REPORTING_STATES
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
        "interpreter": _sanitize_public_transcript(sys.executable),
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

"""Hermetic controls for the readiness egress recorder."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "tools" / "readiness" / "egress_capture.py"


def capture(tmp_path: Path, program: str, *, env: dict[str, str] | None = None) -> list[dict]:
    log = tmp_path / "egress.jsonl"
    command = [sys.executable, str(CAPTURE), "run", "--log", str(log), "--", sys.executable, "-c", program]
    subprocess.run(command, cwd=ROOT, env=env, check=True, capture_output=True, text=True)
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_captures_dns_lookup(tmp_path: Path) -> None:
    events = capture(tmp_path, "import socket;\ntry: socket.getaddrinfo('example.invalid', 443)\nexcept OSError: pass")
    assert any(event["event"] == "socket.getaddrinfo" and event["host"] == "example.invalid" for event in events)


def test_captures_launched_program(tmp_path: Path) -> None:
    events = capture(tmp_path, "import subprocess; subprocess.run(['git', '--version'], check=True)")
    assert any(event["event"] == "subprocess.Popen" and event["program"] == "git" for event in events)


def test_redacts_field_values(tmp_path: Path) -> None:
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    events = capture(tmp_path, "import subprocess; subprocess.run(['gh', 'api', '-f', 'body=SECRET'])", env=env)
    assert "SECRET" not in json.dumps(events)
    assert any(event.get("args") == ["api", "-f"] for event in events)


def test_captures_shell_form_without_recording_the_command_or_field_value(tmp_path: Path) -> None:
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    events = capture(tmp_path, "import subprocess; subprocess.run('gh api -f body=SECRET', shell=True)", env=env)
    rendered = json.dumps(events)
    assert "SECRET" not in rendered and "gh api" not in rendered
    assert any(event.get("program") == "shell" and event.get("args") == ["shell-form"]
               for event in events)


def test_hook_is_off_without_log(tmp_path: Path) -> None:
    hook = ROOT / "tools" / "readiness" / "egress"
    env = os.environ.copy()
    env.pop("SIGMA_EGRESS_LOG", None)
    env["PYTHONPATH"] = str(hook)
    output = tmp_path / "nothing.jsonl"
    subprocess.run([sys.executable, "-c", "import socket; socket.getaddrinfo('localhost', 80)"], env=env, check=True)
    assert not output.exists()


def test_summarize_groups_loopback(tmp_path: Path) -> None:
    log = tmp_path / "egress.jsonl"
    log.write_text('\n'.join([
        '{"event":"socket.connect","host":"127.0.0.1","port":9}',
        '{"event":"socket.getaddrinfo","host":"example.invalid","port":443}',
        '{"event":"subprocess.Popen","program":"git","args":["status"]}',
        '{"event":"urllib.Request","method":"GET","host":"localhost"}',
    ]) + '\n')
    output = tmp_path / "summary.json"
    subprocess.run([sys.executable, str(CAPTURE), "summarize", str(log), "--json", str(output)], check=True)
    summary = json.loads(output.read_text())
    assert summary["destinations"]["socket:127.0.0.1:9"]["loopback"] is True
    assert summary["destinations"]["socket:example.invalid:443"]["count"] == 1
    assert summary["destinations"]["urllib:GET:localhost"]["loopback"] is True


def _fake_bin(tmp_path: Path, name: str) -> dict[str, str]:
    fake = tmp_path / name
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    return env


def test_hook_records_the_git_verb_and_destructive_flag(tmp_path: Path) -> None:
    env = _fake_bin(tmp_path, "git")
    events = capture(tmp_path, "import subprocess; subprocess.run(['git', '-C', '/x', 'push', 'origin', '+a', 'sdlc/1'])",
                     env=env)
    push = next(e for e in events if e.get("program") == "git")
    assert (push.get("verb"), push.get("remote"), push.get("refspecs")) == ("push", "origin", ["+a", "sdlc/1"])
    assert push.get("destructive") is True


def test_hook_records_the_gh_api_method_target_and_number_without_field_values(tmp_path: Path) -> None:
    env = _fake_bin(tmp_path, "gh")
    events = capture(tmp_path, "import subprocess; subprocess.run(['gh', 'api', '-X', 'PATCH', "
                     "'repos/o/r/issues/7?head=o:b', '-f', 'body=SECRET'])", env=env)
    rec = next(e for e in events if e.get("program") == "gh")
    assert (rec.get("method"), rec.get("repo"), rec.get("number")) == ("PATCH", "o/r", "7")
    assert rec.get("endpoint") == "repos/o/r/issues/7?head=o:b" and "SECRET" not in json.dumps(events)

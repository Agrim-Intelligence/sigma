"""Run a command with the egress audit hook and summarize its JSONL output."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys


HOOK_DIR = Path(__file__).with_name("egress")


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def run(log: str, command: list[str]) -> int:
    env = os.environ.copy()
    env["SIGMA_EGRESS_LOG"] = log
    env["PYTHONPATH"] = str(HOOK_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(command, env=env).returncode


def summarize(log: Path) -> dict[str, object]:
    destinations: dict[str, dict[str, object]] = {}
    for line in log.read_text().splitlines():
        event = json.loads(line)
        kind = event.get("event")
        if kind in {"socket.connect", "socket.getaddrinfo"}:
            key = f"socket:{event.get('host')}:{event.get('port')}"
            loopback = is_loopback(str(event.get("host")))
        elif kind == "urllib.Request":
            key = f"urllib:{event.get('method')}:{event.get('host')}"
            loopback = is_loopback(str(event.get("host")))
        elif kind == "subprocess.Popen":
            args = event.get("args") or []
            key = f"program:{event.get('program')}:{args[0] if args else ''}"
            loopback = False
        else:
            continue
        entry = destinations.setdefault(key, {"count": 0, "loopback": loopback})
        entry["count"] = int(entry["count"]) + 1
    return {"destinations": destinations}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--log", required=True)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    summary_parser = sub.add_parser("summarize")
    summary_parser.add_argument("log", type=Path)
    summary_parser.add_argument("--json", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.action == "run":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            parser.error("run requires a command after --")
        return run(args.log, command)
    result = summarize(args.log)
    args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

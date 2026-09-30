"""Optional stdlib-only audit capture for Sigma egress investigations."""
from __future__ import annotations

import json
import os
import posixpath
import sys
import time
from urllib.parse import urlsplit


_LOG = os.environ.get("SIGMA_EGRESS_LOG")
_PROGRAMS = {"gh", "git", "codex", "claude", "cursor-agent", "pip", "curl", "wget"}
_SHELLS = {"sh", "bash", "zsh", "dash", "fish", "cmd", "cmd.exe", "powershell", "pwsh"}


def _safe_arg(value: object) -> str:
    text = str(value)
    if text.startswith("-f") or text.startswith("--field"):
        return text.split("=", 1)[0]
    if "=" in text:
        return text.split("=", 1)[0]
    return text


def _write(record: dict[str, object]) -> None:
    if not _LOG:
        return
    record.update(pid=os.getpid(), ts=time.time())
    fd = os.open(_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode())
    finally:
        os.close(fd)


def _audit(event: str, args: tuple[object, ...]) -> None:
    try:
        if event == "socket.connect":
            address = args[1]
            _write({"event": event, "host": str(address[0]), "port": int(address[1])})
        elif event == "socket.getaddrinfo":
            _write({"event": event, "host": str(args[0]), "port": int(args[1]) if args[1] else None})
        elif event == "urllib.Request":
            parts = urlsplit(str(args[0]))
            _write({"event": event, "method": str(args[3] or "GET"), "host": parts.hostname or ""})
        elif event == "subprocess.Popen":
            raw = args[1] if len(args) > 1 and args[1] else args[0]
            argv = [str(raw)] if isinstance(raw, (str, bytes)) else [str(x) for x in raw]
            program = posixpath.basename(argv[0]) if argv else ""
            if program in _PROGRAMS:
                _write({"event": event, "program": program, "args": [_safe_arg(x) for x in argv[1:3]]})
            elif program.lower() in _SHELLS:
                # `shell=True` makes the audit event name the shell, not the command.  Recording
                # the launch proves the blind spot is observable without writing the shell source,
                # which may contain credentials or request bodies.
                _write({"event": event, "program": "shell", "args": ["shell-form"]})
    except Exception:
        pass


if _LOG:
    sys.addaudithook(_audit)

"""Optional stdlib-only audit capture for Sigma egress investigations."""
from __future__ import annotations

import json
import os
import posixpath
import re
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


_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
_PUSH_DESTRUCTIVE = {"--delete", "-d", "--mirror", "--prune", "--force", "-f", "--force-with-lease",
                     "--force-if-includes"}
_LOCAL_DESTRUCTIVE = {"-d", "-D", "--hard", "remove", "rm", "--force", "--delete"}
_GH_VALUE_OPTS = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input", "-H", "--header",
                  "-q", "--jq", "-t", "--template", "--cache", "-R", "--repo", "--hostname", "-b", "--body",
                  "-t", "--title", "-l", "--label", "-a", "--assignee", "-B", "--base", "-m", "--milestone",
                  "-F", "--body-file", "--match-head-commit", "-p", "--preview", "--add-label", "--remove-label", "--add-assignee",
                  "--remove-assignee", "--add-project", "--remove-project", "--reviewer", "--add-reviewer",
                  "--remove-reviewer", "--subject", "--author-email", "--project", "--head", "--search", "-S",
                  "-s", "--state", "-L", "--limit", "--json", "--comment-id", "--edit-last", "-c", "--comment", "--reason", "-d", "--description", "--color",
                  "--name", "--notes", "--ref", "-r", "--workflow", "--env", "--branch", "--base-sha"}
_PUSH_VALUE_OPTS = {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}
_FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")


def _repo_in(endpoint: str) -> str | None:
    parts = endpoint.lstrip("/").split("/")
    return "/".join(parts[1:3]) if len(parts) >= 3 and parts[0] == "repos" else None


def _git_fields(argv: list[str]) -> dict[str, object]:
    """Verb, destructive flag and push target from the FULL argv (the 2-arg `args` hides them)."""
    i, keys, empty = 0, [], []
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] == "-c" and i + 1 < len(argv):
            key, _, value = argv[i + 1].partition("=")
            keys.append(key.lower())                               # the KEY only; a value may be a secret
            if "=" in argv[i + 1] and value == "":
                empty.append(key.lower())                          # `-c key=` DISABLES the setting
        elif argv[i] == "--config-env" and i + 1 < len(argv):
            keys.append(argv[i + 1].split("=", 1)[0].lower())
        elif argv[i].startswith("--config-env="):
            keys.append(argv[i].split("=", 2)[1].lower())
        i += 2 if argv[i] in _GIT_VALUE_OPTS else 1
    verb = argv[i] if i < len(argv) else ""
    rest = argv[i + 1:]
    out: dict[str, object] = {"verb": verb}
    if keys:
        out["override_keys"] = keys   # `git -c key=value ...` can redirect a remote or alias a verb
        out["override_empty"] = empty
    out["tags"] = any(a in {"--tags", "--follow-tags"} for a in rest)
    positional, skip = [], False
    for a in rest:
        if skip:
            skip = False
        elif a in _PUSH_VALUE_OPTS:
            skip = True
        elif not a.startswith("-"):
            positional.append(a)
    if verb == "push":
        # a remote URL may carry credentials (https://user:token@host/...): never record them
        out["remote"] = re.sub(r"//[^/@]*@", "//", positional[0]) if positional else ""
        out["refspecs"] = positional[1:]
        out["destructive"] = (any(a in _PUSH_DESTRUCTIVE or a.startswith("--force-with-lease=")
                                  or a.startswith("--delete=") for a in rest)
                              or any(a[:1] in "+:" for a in positional[1:]))
    elif verb in {"remote", "config"}:
        out["destructive"] = False
        out["subverb"] = positional[0] if positional else ""
        if verb == "config":          # only the KEY is recorded, never a value (http.extraheader can hold a token)
            keys = [a for a in positional if "." in a]
            out["config_key"] = keys[0].lower() if keys else ""
            out["config_read"] = any(a in {"--get", "--get-all", "--get-regexp", "--list", "-l"} for a in rest)
    elif verb == "tag":
        out["destructive"] = any(a in {"-d", "-f", "--delete", "--force"} for a in rest)
    else:
        out["destructive"] = any(a in _LOCAL_DESTRUCTIVE for a in rest)
    return out


def _gh_fields(argv: list[str]) -> dict[str, object]:
    """Target, issue/PR number and API method from the FULL argv, read BEFORE any redaction."""
    words, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a in _GH_VALUE_OPTS:
            skip = True
        elif not a.startswith("-"):
            words.append(a)
    out: dict[str, object] = {"noun": words[0] if words else "", "action": words[1] if len(words) > 1 else ""}
    repo = None
    for k, a in enumerate(argv):
        if a in {"-R", "--repo"} and k + 1 < len(argv):
            repo = argv[k + 1]
        elif a.startswith("--repo="):
            repo = a.split("=", 1)[1]
        elif a.startswith("-R") and len(a) > 2 and not a.startswith("-R="):
            repo = a[2:]
        elif a.startswith("-R="):
            repo = a[3:]
    if out["noun"] == "api":
        endpoint = words[1] if len(words) > 1 else ""
        method = ""
        for k, a in enumerate(argv):
            if a in {"-X", "--method"} and k + 1 < len(argv):
                method = argv[k + 1]
            elif a.startswith("--method="):
                method = a.split("=", 1)[1]
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:].lstrip("=")
        if not method:
            method = "POST" if any(a in _FIELD_FLAGS or a.startswith(("--field=", "--raw-field=", "--input="))
                                   or (a[:2] in {"-f", "-F"} and len(a) > 2) for a in argv) else "GET"
        out.update(endpoint=endpoint, method=method.upper(), graphql=endpoint == "graphql")
        if endpoint == "graphql":     # read the query text BEFORE redaction; only the boolean is recorded
            # `-F query=@file` hides the document from argv: treat it as a possible mutation, never a read
            out["mutation"] = any("mutation" in a.lower() or "=@" in a for a in argv)
        repo = repo or _repo_in(endpoint)
        segs = endpoint.split("?")[0].split("/")
        for k, s in enumerate(segs[:-1]):
            if s in {"issues", "pulls"} and segs[k + 1].isdigit():
                out["number"] = str(int(segs[k + 1]))
    else:
        for a in words[2:]:
            m = re.fullmatch(r"https?://([^/]+)/([^/]+/[^/]+)/(?:pull|issues)/(\d+)(?:[/?#].*)?", a, re.I)
            if m:                      # a PR or issue given as a URL names its own repository and number
                out["number"], repo = str(int(m.group(3))), repo or "%s/%s" % (m.group(1), m.group(2))
                break
            if re.match(r"https?://", a, re.I):
                repo = repo or "unparsed-url"      # a URL this parser cannot read is never taken for the drill
                break
            if re.fullmatch(r"#?\d+", a):
                out["number"] = str(int(a.lstrip("#")))
                break
    if repo:
        out["repo"] = repo
    return out


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
            if program not in _PROGRAMS and program.lower() not in _SHELLS and program:
                _write({"event": event, "program": program, "args": []})    # name only: a wrapper must be visible
            if program in _PROGRAMS:
                record = {"event": event, "program": program, "args": [_safe_arg(x) for x in argv[1:3]]}
                if program == "git":
                    record.update(_git_fields(argv[1:]))
                elif program == "gh":
                    record.update(_gh_fields(argv[1:]))
                _write(record)
            elif program.lower() in _SHELLS:
                # `shell=True` makes the audit event name the shell, not the command.  Recording
                # the launch proves the blind spot is observable without writing the shell source,
                # which may contain credentials or request bodies.
                _write({"event": event, "program": "shell", "args": ["shell-form"]})
    except Exception:
        pass


if _LOG:
    sys.addaudithook(_audit)

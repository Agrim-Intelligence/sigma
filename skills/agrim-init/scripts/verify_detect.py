#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Detect a candidate verify command for a repository, and write the confirmed choice (#228).

WHY THIS EXISTS. `loop.py verify` reads goal frontmatter `verify_command` (local-goals mode), else
config `verify.command` -- nothing else. A config with `verify.enforce` on and an empty command
refuses EVERY `record done` (loop.py verify prints NO-COMMAND, exit 3; record done REFUSED, exit
4). So this module has one invariant: it NEVER writes enforce on with an empty command
(`write_verify` raises instead), and a scaffold that has no confirmed command writes enforce OFF
with the reason in `verify._why`.

DETECTION READS FILES, NEVER RUNS THEM. Stdlib only, deterministic (fixed source order, sorted
directory listings), bounded scans (a fixed cap on files looked at, so a monorepo costs the same as
a toy repo -- the cap, not the repo size, bounds the work). `shutil.which` decides between `python3`
and `python`: a PATH lookup, not an execution.

CLI (the exact gestures /agrim-init, /agrim-doctor and the setup wizard print):
    verify_detect.py detect  [repo_root]                 # JSON list of candidates
    verify_detect.py set     <sdlc_dir> "<command>"      # command + enforce ON
    verify_detect.py decline <sdlc_dir>                  # enforce OFF, reason names what was declined
"""
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile

#: Upper bound on files a bounded scan looks at. A constant is right here: it bounds work, it is
#: not a resource limit copied from a laptop -- past it, detection stops looking, it never fails.
_SCAN_CAP = 200

#: npm init's placeholder test script -- present in almost every package.json, and it always fails.
_NPM_PLACEHOLDER = "no test specified"

#: A CI `run:` line counts as a test step when it invokes one of these.
_CI_TEST = re.compile(
    r"\b(pytest|tox|nox|npm (run )?test|pnpm (run )?test|yarn (run )?test|go test|cargo test|"
    r"make test|mvn (-\S+ )*test|gradlew? test|dotnet test|bundle exec rspec|rspec|ctest)\b")

#: How a gesture names this script. In config (committed, shared) the portable placeholder -- no
#: machine path lands in a teammate's repo; on the console, this file's real path, so the printed
#: line runs as pasted.
SCRIPT = "<sigma>/skills/agrim-init/scripts/verify_detect.py"
HERE = str(pathlib.Path(__file__).resolve())


def python_command():
    """`python3` where it is on PATH (macOS, Linux), else `python` (common on Windows)."""
    if shutil.which("python3"):
        return "python3"
    if shutil.which("python"):
        return "python"
    return "python3"


def _read(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _has_pytest_tests(root):
    """A bounded scan for test_*.py / *_test.py at the root and up to two levels down, skipping
    hidden, virtualenv and vendored directories. Returns the first match (or None)."""
    skip = {"node_modules", "venv", ".venv", "env", "site-packages", "build", "dist", "__pycache__"}
    seen = 0
    frontier = [root]
    for _depth in range(3):
        nxt = []
        for d in frontier:
            try:
                entries = sorted(d.iterdir(), key=lambda p: p.name)
            except OSError:
                continue
            for p in entries:
                seen += 1
                if seen > _SCAN_CAP:
                    return None
                if p.is_dir():
                    if not p.name.startswith(".") and p.name not in skip:
                        nxt.append(p)
                elif p.suffix == ".py" and (p.name.startswith("test_") or p.stem.endswith("_test")):
                    return p
        frontier = nxt
    return None


def _pytest(root):
    markers = (("pyproject.toml", "[tool.pytest"), ("pytest.ini", ""), ("setup.cfg", "[tool:pytest]"),
               ("tox.ini", "[pytest]"), ("conftest.py", ""))
    for name, needle in markers:
        path = root / name
        if path.is_file() and (not needle or needle in _read(path)):
            return f"{name}" + (f" has {needle}" if needle else " present")
    hit = _has_pytest_tests(root)
    if hit is not None:
        return f"test file {hit.relative_to(root).as_posix()}"
    return None


def _package_json(root):
    path = root / "package.json"
    if not path.is_file():
        return None
    try:
        test = (json.loads(_read(path)).get("scripts") or {}).get("test")
    except (ValueError, AttributeError):
        return None
    if not isinstance(test, str) or not test.strip() or _NPM_PLACEHOLDER in test:
        return None
    runner = "pnpm" if (root / "pnpm-lock.yaml").is_file() else \
        "yarn" if (root / "yarn.lock").is_file() else "npm"
    return f"{runner} test", f"package.json scripts.test = {test.strip()!r}"


def _makefile(root):
    for name in ("Makefile", "makefile", "GNUmakefile"):
        path = root / name
        if path.is_file() and re.search(r"^test\s*:", _read(path), re.MULTILINE):
            return f"{name} has a `test:` target"
    return None


def _ci_steps(root):
    """Single-line `run:` steps that invoke a test runner, in sorted-file order. Multi-line
    (`run: |`) blocks are not parsed: without a YAML parser that would be guessing."""
    out = []
    wf = root / ".github" / "workflows"
    if not wf.is_dir():
        return out
    files = sorted(p for p in wf.iterdir() if p.suffix in (".yml", ".yaml"))[:_SCAN_CAP]
    for path in files:
        for line in _read(path).splitlines():
            m = re.match(r"^\s*(?:-\s*)?run:\s*(.+?)\s*$", line)
            if not m:
                continue
            cmd = m.group(1).strip()
            if cmd[:1] in "|>" or "${{" in cmd:
                continue
            if len(cmd) >= 2 and cmd[0] == cmd[-1] and cmd[0] in "'\"":
                cmd = cmd[1:-1]
            if _CI_TEST.search(cmd):
                out.append((cmd, f"CI step in .github/workflows/{path.name}"))
    return out


def detect(repo_root="."):
    """Ordered candidates: [{"command", "source"}]. First is the proposal. Duplicates dropped."""
    root = pathlib.Path(repo_root)
    found = []
    why = _pytest(root)
    if why:
        found.append((f"{python_command()} -m pytest -q", why))
    pkg = _package_json(root)
    if pkg:
        found.append(pkg)
    if (root / "go.mod").is_file():
        found.append(("go test ./...", "go.mod present"))
    if (root / "Cargo.toml").is_file():
        found.append(("cargo test", "Cargo.toml present"))
    why = _makefile(root)
    if why:
        found.append(("make test", why))
    found.extend(_ci_steps(root))
    out, seen = [], set()
    for cmd, source in found:
        if cmd not in seen:
            seen.add(cmd)
            out.append({"command": cmd, "source": source})
    return out


def _atomic_write_json(path, data):
    fd, tmp = tempfile.mkstemp(prefix=".config.json.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_verify(sdlc_dir, command, why):
    """Rewrite config.json's `verify` block, keeping every other key. A non-empty `command` turns
    enforce ON; None/empty turns it OFF with `why` recorded in `verify._why`. There is no way to
    ask this function for enforce ON without a command -- that is the permanent-refusal trap."""
    path = pathlib.Path(sdlc_dir) / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    verify = cfg.get("verify") if isinstance(cfg.get("verify"), dict) else {}
    command = (command or "").strip()
    verify["command"] = command
    verify["enforce"] = bool(command)
    verify["_why"] = why
    if verify["enforce"] and not verify["command"]:          # unreachable by construction; kept loud
        raise ValueError("refusing to write verify.enforce on with an empty verify.command")
    cfg["verify"] = verify
    _atomic_write_json(path, cfg)
    return verify


def unconfirmed_why(candidates):
    """The `_why` a fresh scaffold records -- no command has been confirmed yet."""
    if not candidates:
        return ("enforce OFF: /agrim-init found no test command in this repository (looked for pytest, "
                "package.json scripts.test, go.mod, Cargo.toml, a Makefile test target, a CI test step). "
                f"Set one with: python3 {SCRIPT} set .sdlc \"<command>\"")
    top = candidates[0]
    return (f"enforce OFF until a command is confirmed: /agrim-init detected `{top['command']}` "
            f"({top['source']}) but nobody has confirmed it. Confirm with: "
            f"python3 {SCRIPT} set .sdlc \"{top['command']}\"")


def proposal_lines(candidates):
    """What /agrim-init prints on every host (Codex/Cursor have no interactive question)."""
    if not candidates:
        return ["agrim-init: verify command - none detected; verify.enforce is OFF (done is not "
                "machine-checked). Set one when you have it:",
                f"  python3 \"{HERE}\" set .sdlc \"<command>\""]
    top = candidates[0]
    lines = [f"agrim-init: verify command - detected `{top['command']}` ({top['source']}).",
             "  verify.enforce stays OFF until you confirm it. Confirm (sets the command, enforce ON):",
             f"  python3 \"{HERE}\" set .sdlc \"{top['command']}\"",
             "  or put this in .sdlc/config.json yourself:",
             "  " + json.dumps({"verify": {"command": top["command"], "enforce": True}}),
             f"  none of these? python3 \"{HERE}\" decline .sdlc  (keeps enforce OFF, says why)"]
    for c in candidates[1:]:
        lines.append(f"  other candidate: `{c['command']}` ({c['source']})")
    return lines


USAGE = "usage: verify_detect.py detect [repo_root] | set <sdlc_dir> <command> | decline <sdlc_dir>"


def main(argv):
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2
    verb = args[0]
    if verb == "detect" and len(args) <= 2:
        print(json.dumps(detect(args[1] if len(args) == 2 else "."), indent=2))
        return 0
    if verb in ("set", "decline"):
        if len(args) != (3 if verb == "set" else 2):
            print(USAGE, file=sys.stderr)
            return 2
        sdlc = pathlib.Path(args[1])
        if not (sdlc / "config.json").is_file():
            print(f"verify_detect: no config.json under {sdlc} -- run /agrim-init first", file=sys.stderr)
            return 2
        if verb == "set":
            cmd = args[2].strip()
            if not cmd:
                print("verify_detect: REFUSED -- an empty command would refuse every `done`; use "
                      "`decline` to turn enforce off instead", file=sys.stderr)
                return 2
            write_verify(sdlc, cmd, f"set by /agrim-init: `{cmd}` confirmed by the user")
            print(f"verify: command = {cmd!r}, enforce ON (loop.py verify runs it; record done needs it green)")
            return 0
        declined = [c["command"] for c in detect(sdlc.resolve().parent)]
        why = ("enforce OFF: the user declined every detected verify command at /agrim-init"
               + (f" ({', '.join(declined)})" if declined else " (none was detected)")
               + ", so `done` is not machine-checked. Turn it on with: "
               f"python3 {SCRIPT} set .sdlc \"<command>\"")
        write_verify(sdlc, None, why)
        print("verify: " + why)
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

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
a toy repo -- the cap, not the repo size, bounds the work). `shutil.which` decides between
`python3`, `python` and `py`: a PATH lookup, not an execution.

REPOSITORY TEXT IS UNTRUSTED, AND NO PRINTED GESTURE CARRIES IT. A CI `run:` line is written by
whoever wrote the repo. The confirm gesture is therefore `confirm <sdlc_dir> <n>`: it re-derives
candidate n from the repository itself, so no candidate text is ever pasted into a shell, and what
is stored is byte-identical to what was shown. (The old gesture, `set .sdlc "<candidate>"`, ran a
CI line like `pytest -q $(touch X)` on the user's machine when pasted, stored something other than
what it showed, and broke on a benign `pytest -m "not slow"`.) On top of that, a CI step carrying a
shell metacharacter (` $ ; & | < >) or any control/format character is never proposed at all --
it is reported by file name only, its text not reproduced -- and everything printed passes through
`printable()`, so an ESC sequence in a file name or package.json cannot repaint the terminal.

CLI (the exact gestures /agrim-init, /agrim-doctor and the setup wizard print):
    verify_detect.py detect  [repo_root]                    # JSON list of candidates
    verify_detect.py confirm <sdlc_dir> <n>                 # candidate n (1-based), enforce ON
    verify_detect.py set     <sdlc_dir> --command-file <f>  # your own command, from a file
    verify_detect.py set     <sdlc_dir> -                   # ... or from stdin (one line)
    verify_detect.py set     <sdlc_dir> "<command>"         # ... or as one argument you typed
    verify_detect.py decline <sdlc_dir>                     # enforce OFF, reason names what was declined
"""
import json
import os
import pathlib
import re
import shlex
import shutil
import sys
import tempfile
import unicodedata

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
    """`python3` where it is on PATH (macOS, Linux), else `python`, else the Windows `py` launcher."""
    for name in ("python3", "python", "py"):
        if shutil.which(name):
            return name
    return "python3"


#: A CI step containing any of these is never proposed: each can run or redirect something the
#: printed line does not show (substitution, chaining, pipes, redirection).
_SHELL_META = frozenset("`$;&|<>")


def _unsafe_char(ch):
    """Control (Cc: ESC, newline, CR, NUL...), format (Cf: bidi overrides, zero-width), and
    surrogate/private/unassigned code points -- anything that is not plain visible text."""
    return unicodedata.category(ch)[0] == "C"


def shell_unsafe(text):
    """True when `text` must not be proposed as a command: a shell metacharacter or an unsafe char."""
    return any(ch in _SHELL_META or _unsafe_char(ch) for ch in text)


def printable(text):
    """`text` with every unsafe character replaced by a visible escape (ESC -> `\\x1b`), so
    repository-derived text printed to a console cannot move the cursor or hide what follows."""
    return "".join(ch.encode("unicode_escape").decode("ascii") if _unsafe_char(ch) else ch
                   for ch in str(text))


def _q(path):
    """Quote a path for a printed gesture: POSIX shell quoting, or double quotes on Windows."""
    return f'"{path}"' if os.name == "nt" else shlex.quote(str(path))


def gesture(verb_args, script=None):
    """One printed gesture: `<python> <script> <verb_args>`. `script` defaults to this file's real
    path (so a console line runs as pasted); pass SCRIPT for text that lands in committed config."""
    return f"{python_command()} {script or _q(HERE)} {verb_args}"


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


def _ci_steps(root, skipped=None):
    """Single-line `run:` steps that invoke a test runner, in sorted-file order. Multi-line
    (`run: |`) blocks are not parsed: without a YAML parser that would be guessing. A step whose
    text is `shell_unsafe` is not returned; its source is appended to `skipped` instead."""
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
                source = f"CI step in .github/workflows/{path.name}"
                if shell_unsafe(cmd):
                    if skipped is not None:
                        skipped.append(source)
                    continue
                out.append((cmd, source))
    return out


def detect(repo_root=".", skipped=None):
    """Ordered candidates: [{"command", "source"}]. First is the proposal. Duplicates dropped.
    Pass a list as `skipped` to learn the sources of CI steps refused as shell-unsafe."""
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
    found.extend(_ci_steps(root, skipped))
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
    """The `_why` a fresh scaffold records -- no command has been confirmed yet. Committed config,
    so the gestures name the portable <sigma> placeholder, never this machine's path."""
    if not candidates:
        return ("enforce OFF: /agrim-init found no test command in this repository (looked for pytest, "
                "package.json scripts.test, go.mod, Cargo.toml, a Makefile test target, a CI test step). "
                "Set one: put it in verify.command here and set verify.enforce true, or run "
                + gesture("set .sdlc --command-file <file>", SCRIPT))
    top = candidates[0]
    return (f"enforce OFF until a command is confirmed: /agrim-init detected `{printable(top['command'])}` "
            f"({printable(top['source'])}) but nobody has confirmed it. Confirm with: "
            + gesture("confirm .sdlc 1", SCRIPT))


def proposal_lines(candidates, skipped=(), trap=False):
    """What /agrim-init prints on every host (Codex/Cursor have no interactive question). No line
    carries candidate text inside a shell gesture: `confirm .sdlc <n>` re-derives it from the repo.
    `trap` is an EXISTING config with enforce ON and an empty command: the lines then say so once,
    and describe confirm/decline against that state instead of claiming enforce is OFF."""
    if trap:
        lines = ["agrim-init: WARNING - existing .sdlc/config.json has verify.enforce ON with an EMPTY "
                 "verify.command: EVERY `record done` is refused until you fix it."]
        state = "  Fix it by setting a command (enforce stays ON), or by declining (turns enforce OFF)."
    else:
        lines = []
        state = "  verify.enforce stays OFF until you confirm one."
    if not candidates:
        lines += ["agrim-init: verify command - none detected" +
                  ("." if trap else "; verify.enforce is OFF (done is not machine-checked).")]
        lines += [state] if trap else []
        lines += ["  Set your own: put it in .sdlc/config.json as "
                  + json.dumps({"verify": {"command": "<your command>", "enforce": True}}),
                  "  or write it to a file and run: " + gesture("set .sdlc --command-file <file>"),
                  "  or turn enforce off: " + gesture("decline .sdlc")]
    else:
        top = candidates[0]
        lines += [f"agrim-init: verify command - detected `{printable(top['command'])}` "
                  f"({printable(top['source'])}).",
                  state,
                  "  Confirm it (re-reads candidate 1 from this repo; sets the command, enforce ON):",
                  "  " + gesture("confirm .sdlc 1"),
                  "  or put this in .sdlc/config.json yourself:",
                  "  " + json.dumps({"verify": {"command": top["command"], "enforce": True}}),
                  "  none of these? " + gesture("decline .sdlc") + "  (enforce OFF, says why)"]
        for n, c in enumerate(candidates[1:], start=2):
            lines.append(f"  other candidate {n}: `{printable(c['command'])}` ({printable(c['source'])})"
                         f" -- confirm .sdlc {n}")
    for source in skipped:
        lines.append(f"  not proposed: a {printable(source)} contains shell metacharacters or control "
                     "characters; review that file yourself (its text is not reproduced here).")
    return lines


USAGE = ("usage: verify_detect.py detect [repo_root] | confirm <sdlc_dir> <n> | "
         "set <sdlc_dir> (--command-file <file> | - | <command>) | decline <sdlc_dir>")


def _refuse(msg):
    print(f"verify_detect: REFUSED -- {msg}", file=sys.stderr)
    return 2


def _read_command(sdlc_args):
    """The command for `set`: from --command-file, stdin (`-`), or the argument itself. Returns
    (command, error). One line only; a trailing newline (as editors and `echo` write) is dropped."""
    if sdlc_args[0] == "--command-file":
        if len(sdlc_args) != 2:
            return None, "--command-file needs exactly one path"
        try:
            raw = pathlib.Path(sdlc_args[1]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return None, f"cannot read {printable(sdlc_args[1])}: {printable(exc)}"
    elif sdlc_args == ["-"]:
        raw = sys.stdin.read()
    elif len(sdlc_args) == 1:
        raw = sdlc_args[0]
    else:
        return None, "set takes one command (quote it), --command-file <file>, or - for stdin"
    raw = raw.rstrip("\r\n")
    return raw.strip(), None


def main(argv):
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2
    verb = args[0]
    if verb == "detect" and len(args) <= 2:
        print(json.dumps(detect(args[1] if len(args) == 2 else "."), indent=2))
        return 0
    if verb in ("set", "confirm", "decline"):
        if (verb == "decline" and len(args) != 2) or (verb == "confirm" and len(args) != 3) \
                or (verb == "set" and len(args) < 3):
            print(USAGE, file=sys.stderr)
            return 2
        sdlc = pathlib.Path(args[1])
        if not (sdlc / "config.json").is_file():
            print(f"verify_detect: no config.json under {printable(sdlc)} -- run /agrim-init first",
                  file=sys.stderr)
            return 2
        if verb == "confirm":
            cands = detect(sdlc.resolve().parent)
            if not (args[2].isdigit() and 1 <= int(args[2]) <= len(cands)):
                return _refuse(f"no candidate {printable(args[2])} -- this repository has "
                               f"{len(cands)} (`detect .` lists them)")
            cmd = cands[int(args[2]) - 1]["command"]
            origin = f"candidate {args[2]} ({printable(cands[int(args[2]) - 1]['source'])})"
        elif verb == "set":
            cmd, err = _read_command(args[2:])
            if err:
                return _refuse(err)
            if not cmd:
                return _refuse("an empty command would refuse every `done`; use `decline` to turn "
                               "enforce off instead")
            if any(_unsafe_char(ch) for ch in cmd):
                return _refuse("the command contains a newline or control character; a verify "
                               "command is one visible line")
            origin = "a command the user supplied"
        if verb in ("set", "confirm"):
            write_verify(sdlc, cmd, f"set by /agrim-init: `{cmd}` ({origin}) confirmed by the user")
            # json.dumps: exactly the stored string, every character visible, safe to print.
            print(f"verify: command = {json.dumps(cmd)}, enforce ON "
                  "(loop.py verify runs it; record done needs it green)")
            return 0
        declined = [c["command"] for c in detect(sdlc.resolve().parent)]
        why = ("enforce OFF: the user declined every detected verify command at /agrim-init"
               + (f" ({', '.join(declined)})" if declined else " (none was detected)")
               + ", so `done` is not machine-checked. Turn it on with: "
               + gesture("confirm .sdlc <n>", SCRIPT) + " (after `detect .`), or put the command "
               "in verify.command and set verify.enforce true")
        write_verify(sdlc, None, why)
        print("verify: " + why)
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

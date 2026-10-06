#!/usr/bin/env python3
"""Resolve WHICH mechanism gives this host an author-blind reviewer (issue #1983).

WHY THIS EXISTS. `sigma-review` opened by ASSERTING "you did not write this code" -- true when
/sigma-loop dispatches the gate, false whenever the authoring session runs /sigma-review itself.
An unenforceable assertion in prose is exactly what that skill's own evidence rule bans.

WHERE THE BOUNDARY IS. `breaker.py`'s `no_channel()` already states the half this module supplies:
"that the ORCHESTRATOR spawns the breaker, never the maker -- is a spawn topology this function
cannot observe from a string ... and must be enforced at the dispatch site." This IS that site. It
does not re-implement breaker's leak checks; it calls them (see `check`).

HOST-AGNOSTIC BY BRANCHING, NOT BY LOWEST COMMON DENOMINATOR. Each host reaches the SAME property --
a model context that never saw the author's reasoning reads the brief -- by its own best route. No
branch is a downgrade. `inline` is the only degradation, and it is per-MACHINE, never per-host: a
Claude machine with no marker degrades exactly as a Cursor machine without `cursor-agent` does.

AUTO-DETECTION uses observed session markers only. Claude's markers retain precedence;
`CODEX_SESSION_ID`/`CODEX_THREAD_ID` identify Codex on a real Codex host. Cursor still needs
`review.host` because no marker has been verified there. A missing CLI is named, never guessed.

Fail-open: every failure resolves `inline` WITH A STATED REASON and exit 0 -- a broken resolver must
not stop a review. `check` is the opposite and fails CLOSED, because a leak guard that passes on
error is decoration. Zero deps.
"""
import importlib.util
import json
import os
import pathlib
import signal
import shutil
import subprocess
import sys

#: What the SKILL prose branches on.
MECHANISMS = ("subagent", "process", "command", "inline")

#: Legal `review.host` values. An unknown value is a loud `inline`, never a guess.
HOSTS = ("auto", "claude", "cursor", "codex", "command", "inline")

#: Per-host invocation, and whether that branch has been RUN end to end BY THIS PROJECT -- NOT
#: whether this machine can run it (that is what the PATH check answers, separately). The two are
#: different questions and conflating them would let an unproven branch look proven merely because
#: its binary is installed. `verified` remains False for Codex until an entire review gate, including
#: its brief and verdict, has been exercised on the actual host.
_HOST_COMMANDS = {
    "cursor": (["cursor-agent", "-p", "--output-format", "text"], False),
    "codex": (["codex", "exec", "--sandbox", "read-only", "--ephemeral", "-"], False),
}


def _load(name):
    """Import a sibling script by path -- the kit's standard zero-install module loader. Legal here
    only because `breaker.py` lives in this same skill; a DIFFERENT skill must shell out to the CLI
    instead (north-star Architecture Rule 3)."""
    spec = importlib.util.spec_from_file_location(
        name, pathlib.Path(__file__).resolve().parent / (name + ".py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _config(sdlc_dir):
    try:
        return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except Exception:               # noqa: BLE001 - fail-open by design; see module docstring
        return {}


def _out(mechanism, host, reason, command=None, verified=True, timeout=900):
    got = {"mechanism": mechanism, "host": host, "verified": verified, "reason": reason,
           "timeout_seconds": timeout}
    if command is not None:
        got["command"] = command
    return got


shell_policy = _load("shell_policy")      # #707


def resolve(sdlc_dir, env=None, which=None):
    """-> the dict the SKILL prose branches on. Never raises; never returns an unknown mechanism."""
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    cfg = _config(sdlc_dir)
    review = cfg.get("review") if isinstance(cfg, dict) else None
    # EVERY read below is coerced. `_config` guarded only the read+parse, so a config that WAS valid
    # JSON but the wrong shape still crashed the gate -- and a list-valued `command` is not
    # contrived, since this function's own `process` output carries `command` as a list.
    review = review if isinstance(review, dict) else {}
    host = str(review.get("host") or "auto").strip()
    command = str(review.get("command") or "").strip()
    try:
        timeout = int(review.get("timeout_seconds") or 900)
    except (TypeError, ValueError):
        timeout = 900

    if review.get("independent") is False:
        # The loop already honours this flag; without it here, a direct /sigma-review told the agent
        # to dispatch while doctor reported "a fresh reviewer is not spawned" for the same config.
        return _out("inline", "other", "review.independent is false (operator opted out)",
                    timeout=timeout)
    if host not in HOSTS:
        return _out("inline", "other", "unknown review.host %r; expected one of %s"
                    % (host, ", ".join(HOSTS)), timeout=timeout)
    if host == "inline":
        return _out("inline", "other", "review.host is inline (operator's explicit choice)",
                    timeout=timeout)
    if host == "command" or (host == "auto" and command):
        if not command:
            return _out("inline", "other", "review.host is command but review.command is empty",
                        timeout=timeout)
        # Unverified for the same reason codex is: no review has been run through an operator
        # command end to end by this project. `verified` is about THIS PROJECT's evidence.
        return _out("command", "other", "review.command configured", command=command,
                    verified=False, timeout=timeout)
    if host in _HOST_COMMANDS:
        argv, verified = _HOST_COMMANDS[host]
        if not which(argv[0]):
            return _out("inline", host,
                        "review.host is %s but %s is not on PATH" % (host, argv[0]),
                        timeout=timeout)
        return _out("process", host, "%s resolved on PATH" % argv[0],
                    command=list(argv), verified=verified, timeout=timeout)
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_SESSION_ID"):
        return _out("subagent", "claude", "host dispatches its own subagents", timeout=timeout)
    if host == "auto" and (env.get("CODEX_SESSION_ID") or env.get("CODEX_THREAD_ID")):
        argv, verified = _HOST_COMMANDS["codex"]
        if not which(argv[0]):
            return _out("inline", "codex", "Codex session detected but codex is not on PATH",
                        timeout=timeout)
        return _out("process", "codex", "Codex session marker and codex CLI resolved",
                    command=list(argv), verified=verified, timeout=timeout)
    if host == "claude":
        # Symmetric with the PATH check above: an explicit host that cannot actually be reached
        # degrades loudly. Handing back `subagent` where none can be spawned produces an inline
        # self-review stamped "dispatched subagent", which is the failure this module prevents.
        return _out("inline", "claude",
                    "review.host is claude but no CLAUDECODE / CLAUDE_CODE_SESSION_ID marker is set",
                    timeout=timeout)
    return _out("inline", "other",
                "no host marker in the environment and no review.command configured",
                timeout=timeout)


#: breaker's finding for implementation content, which does NOT apply to a reviewer. See `check`.
_IMPLEMENTATION_FINDING = "implementation content reached the brief"


def check(brief_path, scratch_paths=()):
    """-> (ok, findings). The dispatch-site half of breaker.py's guarantee, for REVIEWS.

    `no_channel`'s own docstring names this as the requirement it cannot check from a string, because
    a string cannot reveal who spawned whom. So the check lives here, at the site that actually
    spawns the reviewer -- and it CALLS breaker rather than cloning its patterns, so a tightening
    of the shared-scratch rule is inherited rather than missed.

    BUT A REVIEWER IS NOT A BREAKER, AND ONE OF BREAKER'S TWO FINDINGS MUST NOT BE APPLIED HERE.
    A breaker must never see the implementation -- that is its whole isolation property. A code
    reviewer MUST see the diff: it is the artifact under review. `_LEAK` also matches any line
    starting with `-` (breaker.brief() switched its own bullets to `*` for exactly this reason) and
    any prose containing `def `/`class `/`import `/`return `, so a markdown review brief trips it on
    formatting alone. Measured, not assumed: the real #1983 brief was flagged on its first run.
    Carrying that through would be the false positive breaker's own comment warns about -- "exactly
    the sort of false positive that gets a real guard switched off".

    What DOES bind a reviewer is the shared-scratch rule: a brief naming a scratch file the maker can
    write is a maker->reviewer channel whatever the artifact is. The other half of the reviewer's
    isolation -- that the maker's reasoning never arrives -- is structural in `review_context.py`,
    which has no field to carry it, and is not something a string check could confirm anyway.

    Fails CLOSED, unlike `resolve`: a brief that could not even be read has not been checked, and a
    leak guard that reports ok when it checked nothing is decoration."""
    try:
        text = pathlib.Path(brief_path).read_text(encoding="utf-8")
    except OSError as exc:
        return (False, ["brief unreadable, so nothing was checked: %s" % exc])
    return _check_brief_text(text, scratch_paths)


def _check_brief_text(text, scratch_paths):
    """Check the bytes that will actually be dispatched, without a second file read."""
    _, findings = _load("breaker").no_channel(text, scratch_paths)
    findings = [f for f in findings if f != _IMPLEMENTATION_FINDING]
    return (not findings, findings)


def _stop_review_process(proc):
    """Kill the dedicated review process group, then reap its leader on every failure path."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        proc.kill()
    try:
        proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        # A process that deliberately detached from our group may still hold a pipe. Close our
        # descriptors rather than letting cleanup itself wait forever; the direct child is reaped.
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        proc.kill()
        proc.wait(timeout=2)


def run_review(sdlc_dir, brief_path, scratch_paths):
    """Execute a resolved process/command review with a real timeout and fresh process group.

    The operator-owned `review.command` retains its shell semantics. `process` commands remain
    argv lists. Unsupported platforms refuse before spawning: killing only a shell while its
    children keep consuming quota would be a silent half-guarantee.
    """
    if os.name != "posix" or not hasattr(os, "killpg"):
        print("reviewer run: process-tree timeout is unsupported on this platform", file=sys.stderr)
        return 2
    if not scratch_paths:
        print("reviewer run: at least one --scratch path is required for the leak check",
              file=sys.stderr)
        return 2
    route = resolve(sdlc_dir)
    mechanism = route["mechanism"]
    if mechanism not in ("process", "command"):
        print(f"reviewer run: resolved {mechanism}; use that route instead ({route['reason']})",
              file=sys.stderr)
        return 2
    try:
        brief = pathlib.Path(brief_path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"LEAK: brief unreadable, so nothing was checked: {exc}", file=sys.stderr)
        return 1
    ok, findings = _check_brief_text(brief, scratch_paths)
    if not ok:
        for finding in findings:
            print(f"LEAK: {finding}", file=sys.stderr)
        return 1
    if mechanism == "command" and not shell_policy.repository_shell_commands_allowed(
            pathlib.Path(sdlc_dir).resolve().parent):
        # #707 (TM-04): `review.command` is a repository-supplied shell string, and its stdout is
        # accepted as the reviewer's verdict. Same gate as `verify.command`; no verdict is produced.
        print("reviewer run: " + shell_policy.refusal_message(), file=sys.stderr)
        return 2
    timeout = route["timeout_seconds"]
    if not isinstance(timeout, int) or timeout <= 0:
        print("reviewer run: review.timeout_seconds must be positive", file=sys.stderr)
        return 2
    command = route["command"]
    try:
        proc = subprocess.Popen(command, shell=mechanism == "command", stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
    except OSError as exc:
        print(f"reviewer run: could not start reviewer process ({type(exc).__name__})",
              file=sys.stderr)
        return 1
    try:
        output, _errors = proc.communicate(input=brief, timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_review_process(proc)
        print(f"reviewer run: timed out after {timeout}s; reviewer process group stopped",
              file=sys.stderr)
        return 124
    except BaseException:
        _stop_review_process(proc)
        raise
    if proc.returncode != 0:
        print(f"reviewer run: reviewer exited with exit {proc.returncode}; no verdict accepted",
              file=sys.stderr)
        return 1
    if not output.strip():
        print("reviewer run: reviewer returned empty output; no verdict accepted", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


USAGE = ("usage: reviewer.py resolve <sdlc_dir> | check <brief_path> [--scratch <path>]... | "
         "run <sdlc_dir> <brief_path> --scratch <path> [--scratch <path>]...")


def main(argv):
    if argv in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 2 and argv[0] == "resolve":
        print(json.dumps(resolve(argv[1])))
        return 0
    if len(argv) >= 2 and argv[0] == "check":
        scratch = [argv[i + 1] for i, a in enumerate(argv) if a == "--scratch" and i + 1 < len(argv)]
        ok, findings = check(argv[1], scratch)
        for f in findings:
            print("LEAK: %s" % f, file=sys.stderr)
        return 0 if ok else 1
    if len(argv) >= 3 and argv[0] == "run":
        options = argv[3:]
        if len(options) < 2 or len(options) % 2 or any(options[i] != "--scratch"
                                                       for i in range(0, len(options), 2)):
            print("usage: reviewer.py run <sdlc_dir> <brief_path> --scratch <path> "
                  "[--scratch <path>]...", file=sys.stderr)
            return 2
        return run_review(argv[1], argv[2], options[1::2])
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    # The script-time floor: this run's wall time, recorded to the session resolved at exit.
    raise SystemExit(_load("timing_store").timed_main(main, sys.argv[1:], "reviewer"))

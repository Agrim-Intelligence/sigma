"""Clean-cwd runner for the author-blind breaker (#853, slice 4 of #558). Library only.

ONE JOB: spawn the breaker's argv with the brief on stdin, in an EMPTY temporary working directory, in a
process group that is killed whole on timeout, under a model-session lease, and hand back the text it
printed. Nothing here validates or writes a test file: that is `seal_tests.py`, from Python, never the
breaker and never the maker.

WHY AN EMPTY CWD. The breaker must not see the implementation. Its cwd is a fresh `sigma-breaker-*`
directory with nothing in it, so a host CLI that reads "the current directory" finds nothing to read.
Cleanup removes ONLY a directory whose name starts with that prefix, so a wrong directory handed in by a
caller (or a broken `make_cwd`) can never be deleted by this module.

STATED LIMITS (D-3, accepted, not closed):
  * The empty cwd does NOT stop a host CLI from reading the worktree by ABSOLUTE path, or from reading
    anything else the user's account can read. Isolation of the brief is by construction (`breaker.brief`
    has no parameter for the implementation); isolation of the PROCESS is a cwd, not a sandbox.
  * The environment is the caller's with the python-path and pytest variables removed and
    `PYTHONDONTWRITEBYTECODE=1` set. `HOME` is KEPT, deliberately: the host CLIs authenticate from it, so
    the breaker's credentials ARE reachable by whatever it runs. A scrubbed HOME was not chosen because
    whether the host CLIs still authenticate under one is UNMEASURED.
  * A SIGKILL of the caller can leave a `sigma-breaker-*` directory in $TMPDIR; the OS temp reaper is the
    lever (same as `sigma-sealed-*`). The group itself is stopped by `bounded_run`'s lifeline.
  * Exercised here only through a fake CLI; a real host run is UNMEASURED.
"""
import importlib.util
import os
import pathlib
import re
import shutil
import tempfile

_MODULES = {}


def _load(name):
    """Sibling module by path (same skill), cached so repeated calls do not re-exec it."""
    if name not in _MODULES:
        spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULES[name] = module
    return _MODULES[name]


#: Removed from the breaker's environment: where Python looks for code, and pytest's own knobs.
_DROP_ENV = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
_DROP_PREFIXES = ("PYTEST_",)
CWD_PREFIX = "sigma-breaker-"


class BreakerRunRefused(Exception):
    """The breaker run did not yield usable text. `.clause` is one of timeout, held, nonzero-exit,
    empty-output, cwd-not-empty, runner-unusable, no-group."""

    def __init__(self, clause, detail):
        super().__init__("breaker_run: REFUSED [%s]: %s" % (clause, detail))
        self.clause = clause


#: How much of the breaker's stderr a nonzero-exit refusal carries, so an unauthenticated host CLI is diagnosable.
STDERR_TAIL_CHARS = 300
_CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_SECRET = re.compile(r"(?i)\b(bearer|basic)\s+\S+|\b[\w.-]*(?:key|token|secret|passw\w*|auth\w*)[\w.-]*\s*[=:]\s*(?:(?:bearer|basic)\s+)?\S+"
                     r"|\b[A-Za-z0-9_\-+/=.]{32,}")


def stderr_tail(err, limit=STDERR_TAIL_CHARS):
    """-> a bounded, secret-safe one-line tail of `err`: ANSI escapes and control characters removed,
    key/token/Bearer-shaped text and any 32+ character opaque run replaced by `[redacted]`, whitespace
    collapsed, the LAST `limit` characters kept. Best effort (a heuristic, not a guarantee): stderr is the
    host CLI's own text and is not logged elsewhere by this module."""
    text = _SECRET.sub("[redacted]", _CONTROL.sub(" ", str(err or "")))
    text = " ".join(text.split())
    return text[-limit:]


def breaker_env(base=None):
    """The breaker's environment: `bounded_run.unattended_env` (repository-location variables out, prompt
    pins in) minus the python-path and pytest variables, plus `PYTHONDONTWRITEBYTECODE=1`. HOME is kept."""
    env = {k: v for k, v in _load("bounded_run").unattended_env(base).items()
           if k not in _DROP_ENV and not k.startswith(_DROP_PREFIXES)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _remove(path):
    """Delete a directory this module made. Refuses (silently keeps) any path not named `sigma-breaker-*`."""
    if path and os.path.basename(os.path.normpath(path)).startswith(CWD_PREFIX):
        shutil.rmtree(path, ignore_errors=True)


def run(argv, brief_text, goal, config, *, timeout, before_spawn=None, make_cwd=None, env=None, run_group=None):
    """-> (text, receipt). Raises BreakerRunRefused. `timeout` is seconds, derived by the caller.

    ORDER: group support -> lease -> empty cwd -> `before_spawn()` -> spawn. `before_spawn` is the caller's
    last word before any model quota is spent (the spend consult and the attempts counter live there), so a
    lease Hold or a cwd that is not empty never costs an approval or an attempt. An exception from it
    propagates after the cleanup below. `make_cwd` and `run_group` are test seams."""
    bounded, guard = _load("bounded_run"), _load("resource_guard")
    refusal = bounded.group_refusal()
    if refusal:
        raise BreakerRunRefused("no-group", refusal)
    try:
        budget = bounded.seconds(timeout)
    except ValueError as exc:
        raise BreakerRunRefused("timeout", str(exc))
    config = config if isinstance(config, dict) else {}
    cwd = brief_dir = None
    try:
        with guard.session_lease("breaker", goal, ttl=budget * 2 + 60, config=config.get("resource")) as lease:
            if lease is not None and hasattr(lease, "reason"):
                raise BreakerRunRefused("held", "a model session is held (%s); the breaker was not started"
                                        % lease.reason)
            cwd = (make_cwd or (lambda: tempfile.mkdtemp(prefix=CWD_PREFIX)))()
            if os.listdir(cwd):
                raise BreakerRunRefused("cwd-not-empty", "the breaker's working directory is not empty")
            brief_dir = tempfile.mkdtemp(prefix=CWD_PREFIX + "in-")      # the brief lives OUTSIDE the cwd
            brief_path = os.path.join(brief_dir, "brief.txt")
            pathlib.Path(brief_path).write_text(brief_text, encoding="utf-8")
            if before_spawn is not None:
                before_spawn()
            got = (run_group or bounded.run_group)(list(argv), cwd, budget, env=breaker_env(env), merge=False,
                                                  stdin_path=brief_path)
    finally:
        _remove(cwd)
        _remove(brief_dir)
    if got.outcome == bounded.TIMEOUT:
        raise BreakerRunRefused("timeout", "the breaker exceeded %gs; its process group was stopped" % budget)
    if got.outcome == bounded.FAILED:
        tail = stderr_tail(got.err)
        raise BreakerRunRefused("nonzero-exit", "the breaker exited %s%s" % (
            got.code, "; stderr tail: " + tail if tail else ""))
    if got.outcome != bounded.OK:
        raise BreakerRunRefused("runner-unusable", "%s %s" % (got.outcome, got.detail))
    if not (got.out or "").strip():
        raise BreakerRunRefused("empty-output", "the breaker printed nothing")
    return got.out, {"seconds": got.seconds, "code": got.code, "truncated": bool(got.truncated)}

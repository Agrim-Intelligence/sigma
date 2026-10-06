"""Isolated profiles, bounded commands, plugin-directory hashing and per-session metering."""
import hashlib
import importlib.util
import os
from pathlib import Path
import signal
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Optional


IS_POSIX = os.name == "posix"
PROFILE_VARIABLES = ("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME")
MIN_BUDGET_USD = 0.000001
MIN_ATTEMPT_SECONDS = 1.0
VERSION_TIMEOUT_SECONDS = 60
VERSION_OUTPUT_LIMIT = 4096
_HERE = Path(__file__).resolve().parent
_METER = None


class ArmRefusal(ValueError):
    """An arm precondition was not met; the harness turns this into a benchmark refusal."""


KIND_NAMES = ("input", "output", "cache_read", "cache_write")


class Spend:
    """What an arm has already used in the run in flight (kept for the abort report).

    ``tokens`` is the measured fact the ceiling is enforced on; ``usd`` is INDICATIVE (tokens priced at
    published list rates), kept only for the report.
    """

    def __init__(self):
        self.usd = 0.0
        self.tokens = 0
        self.detail = dict.fromkeys(KIND_NAMES, 0)

    def add(self, cost, priced_part, tokens=None, detail=None):
        self.usd += cost if cost is not None else (priced_part or 0.0)
        self.tokens += tokens or 0
        for name in KIND_NAMES:
            self.detail[name] += (detail or {}).get(name, 0)


@dataclass(frozen=True)
class Attempt:
    """One ``claude -p`` run.  ``tokens`` is None when no usage record could be read at all.

    ``rate_limit`` is the host's rate-limit record found in the run's transcripts, if any; ``throttled`` is
    that record when the run did not exit cleanly OR the session ended on it (no real turn after it), so the
    unmeasured exit status of a throttled ``claude -p`` is not what decides.  A transient record that real
    work followed, on a run that exited 0, is noted and scored as usual.
    """
    cost: Optional[float]
    priced_part: Optional[float]
    returncode: int
    timed_out: bool
    session_id: str
    tokens: Optional[int] = None
    detail: Optional[dict] = None
    rate_limit: Optional[dict] = None

    @property
    def throttled(self):
        limit = self.rate_limit
        if limit is not None and (limit.get("final") or self.returncode != 0 or self.timed_out):
            return limit
        return None

    @property
    def suspect(self):
        """The exit status of a run that did real work, did not hit the deadline, and did not exit cleanly,
        with no rate-limit record to explain it: an infrastructure fault (the host's rate-limit signal under
        ``-p`` is unmeasured, and so is an overloaded or auth error) must not be scored as the arm failing."""
        if self.returncode != 0 and not self.timed_out and self.throttled is None and self.tokens:
            return self.returncode
        return None


def require_posix():
    if not IS_POSIX:
        raise ArmRefusal("live arms need a POSIX host (process-group kill); not supported here")


def meter():
    """The shared transcript meter, loaded by path exactly as the harness's own scripts load siblings."""
    global _METER
    if _METER is None:
        spec = importlib.util.spec_from_file_location("bench_arms_meter", _HERE.parent / "meter.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _METER = module
    return _METER


def strip_harness_variables(environment):
    return {key: value for key, value in environment.items() if not key.startswith("SIGMA_BENCH_")}


def isolated_env(environment, profile_dir):
    """Forward the environment except the harness plumbing; point the three profile variables at ``profile_dir``."""
    env = strip_harness_variables(environment)
    profile = Path(profile_dir)
    for name, leaf in zip(PROFILE_VARIABLES, ("home", "claude-config", "codex-home")):
        directory = profile / leaf
        directory.mkdir(parents=True, exist_ok=True)
        env[name] = str(directory)
    temp = profile / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(temp)
    return env


def remove_tree(path):
    """Delete a directory tree an agent may have made read-only; refuse (never traceback) when impossible."""
    try:
        shutil.rmtree(path)
        return
    except OSError:
        pass
    for current, dirnames, _ in os.walk(path):
        for name in dirnames:
            try:
                os.chmod(os.path.join(current, name), 0o700)
            except OSError:
                pass
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise ArmRefusal("could not remove %s: %s" % (Path(path).name, exc.strerror or exc)) from exc


def _hash_file(handle, path):
    try:
        with open(path, "rb") as source:
            for block in iter(lambda: source.read(1 << 20), b""):
                handle.update(block)
    except OSError as exc:
        handle.update(("unreadable:%s" % exc.errno).encode())


def tree_digest(path):
    """Content hash of a directory tree; symlinks inside it are recorded, never followed.

    A missing path hashes to the stable value ``absent``.  A symlink given as the top-level path is
    resolved first (dotfile managers link the whole plugin directory), so its real contents are hashed.
    """
    if not os.path.lexists(path):
        return "absent"
    root = os.path.realpath(path)
    digest = hashlib.sha256()
    if not os.path.isdir(root):
        digest.update(b"file\0")
        _hash_file(digest, root)
        return digest.hexdigest()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        relative = os.path.relpath(current, root)
        digest.update(b"dir\0" + relative.encode("utf-8", "surrogateescape") + b"\0")
        entries = sorted(filenames + [d for d in dirnames if os.path.islink(os.path.join(current, d))])
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(current, d))]
        for name in entries:
            full = os.path.join(current, name)
            tag = os.path.join(relative, name).encode("utf-8", "surrogateescape")
            if os.path.islink(full):
                digest.update(b"link\0" + tag + b"\0" + os.readlink(full).encode("utf-8", "surrogateescape"))
            else:
                digest.update(b"file\0" + tag + b"\0")
                _hash_file(digest, full)
    return digest.hexdigest()


def real_plugin_dirs(environment):
    """The operator's own plugin directories: under HOME, and under CLAUDE_CONFIG_DIR when it is set."""
    home = environment.get("HOME") or os.path.expanduser("~")
    dirs = [Path(home) / ".claude" / "plugins"]
    config = environment.get("CLAUDE_CONFIG_DIR")
    if config and (Path(config) / "plugins") not in dirs:
        dirs.append(Path(config) / "plugins")
    return dirs


def plugin_digest(environment):
    return "|".join(tree_digest(path) for path in real_plugin_dirs(environment))


def _kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_bounded(argv, *, cwd, env, timeout, stdin_text=None, capture=False):
    """Run ``argv`` in its own process group and kill the WHOLE group on expiry or interruption.

    Output is discarded (only the return code matters) unless ``capture`` asks for a size-limited
    stdout.  Returns ``(returncode, timed_out, output)``.  The group is killed after a normal exit too,
    so a child an agent left behind cannot keep spending.
    """
    require_posix()
    try:
        proc = subprocess.Popen(
            [str(part) for part in argv], cwd=str(cwd), env=env, start_new_session=True,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise ArmRefusal("cannot start %r: %s" % (str(argv[0]), exc.strerror)) from exc
    timed_out, output = False, b""
    try:
        try:
            output, _ = proc.communicate(
                input=stdin_text.encode("utf-8") if stdin_text is not None else None,
                timeout=max(0.0, timeout))
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc.pid)
            proc.communicate()
    finally:
        _kill_group(proc.pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return proc.returncode, timed_out, (output or b"")[:VERSION_OUTPUT_LIMIT]


def command_passed(launcher, argv, *, cwd, env, timeout):
    returncode, timed_out, _ = run_bounded([launcher, "--", *argv], cwd=cwd, env=env, timeout=timeout)
    return returncode == 0 and not timed_out


def claude_version(launcher, claude, *, env, cwd):
    returncode, timed_out, output = run_bounded([launcher, "--", claude, "--version"], cwd=cwd, env=env,
                                                timeout=VERSION_TIMEOUT_SECONDS, capture=True)
    text = output.decode("utf-8", "replace").strip()
    return text if returncode == 0 and not timed_out and text else "unavailable"


def claude_argv(launcher, claude, *, session_id, model, permission_mode, budget_usd, plugin_dir=None):
    if budget_usd < MIN_BUDGET_USD:
        raise ArmRefusal("a non-positive spend budget is never passed to claude")
    argv = [str(launcher), "--", str(claude), "-p", "--session-id", session_id, "--model", model,
            "--permission-mode", permission_mode, "--max-budget-usd", "%.6f" % budget_usd]
    if plugin_dir is not None:
        argv += ["--plugin-dir", str(plugin_dir)]
    return argv


def session_usage(config_dir, session_id, rates):
    """The host's own usage records for one session, or None when no transcript exists.

    Returns ``{"cost", "priced_part", "tokens", "detail", "rate_limit"}``: tokens are the measured fact;
    cost is indicative dollars (None when any turn is unpriced).
    """
    module = meter()
    paths = module.session_paths(session_id, Path(config_dir) / "projects")
    if not paths:
        return None
    metered = module.meter_transcripts(paths, rates=rates)
    return {"cost": metered["cost_usd"], "priced_part": metered["cost_usd_priced_part"],
            "tokens": metered["tokens_total"],
            "detail": {"input": metered["tokens_in"], "output": metered["tokens_out"],
                       "cache_read": metered["tokens_cache_read"],
                       "cache_write": metered["tokens_cache_write"]},
            "rate_limit": metered["rate_limited"]}


def run_claude(*, launcher, claude, model, permission_mode, budget_usd, plugin_dir, workdir, prompt,
               environment, profile_dir, deadline, rates):
    """One metered ``claude -p`` run in a fresh isolated profile, bounded by ``deadline``.

    ``budget_usd`` is the host's own per-run ``--max-budget-usd`` belt: a client-side, token-priced estimate
    (indicative under a subscription), kept as a runaway guard.  It is not the benchmark's ceiling.
    """
    env = isolated_env(environment, profile_dir)
    session_id = str(uuid.uuid4())
    argv = claude_argv(launcher, claude, session_id=session_id, model=model,
                       permission_mode=permission_mode, budget_usd=budget_usd, plugin_dir=plugin_dir)
    returncode, timed_out, _ = run_bounded(argv, cwd=workdir, env=env, timeout=deadline - time.monotonic(),
                                           stdin_text=prompt)
    usage = session_usage(env["CLAUDE_CONFIG_DIR"], session_id, rates)
    if usage is None:
        return Attempt(None, None, returncode, timed_out, session_id)
    return Attempt(usage["cost"], usage["priced_part"], returncode, timed_out, session_id,
                   usage["tokens"], usage["detail"], usage["rate_limit"])

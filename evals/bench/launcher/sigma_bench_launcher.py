#!/usr/bin/env -S python3 -B
"""Operator-owned isolation launcher for the Sigma benchmark harness (`evals/bench/bench.py`).

The harness runs every command as ``launcher -- <argv...>`` (the ``claude -p`` runs, ``claude --version`` and the
scoring commands).  This file is a SOURCE the operator installs outside the repository, next to a JSON config
(see README.md); the harness refuses a launcher inside the repository.  Stdlib only, POSIX only.

It is a set of guards, not a sandbox: it cannot stop the host CLI reading the OS keychain, system-wide managed
settings, the network, or anything the user account can read.  README.md says so.

Exit status: the command's own; 2 refusal (nothing started; includes a model run with no subscription token); 97 the real profile changed; 124 deadline; 127 cannot start.
It writes only inside config ``scratch_root`` (dry-run profile, latch, alert log).
"""
import sys

sys.dont_write_bytecode = True  # the harness runs this file with HOME = the fresh profile; never write into it
# (the shebang's -B matters most: Apple's python writes its startup .pyc files under $HOME before any code runs)
import hashlib
import json
import math
import os
from pathlib import Path
import select
import shutil
import signal
import stat
import subprocess
import tempfile
import time

PROFILE_VARIABLES = ("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TMPDIR")
PASS_THROUGH = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "SHELL", "USER", "LOGNAME")
# The benchmark runs on the owner's Max SUBSCRIPTION (2026-10-06), through `claude setup-token`'s long-lived token.
# An API key is refused on purpose: `claude -p` prefers it whenever it is present, which would silently switch the
# run to pay-per-token billing.
SUBSCRIPTION_TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
CREDENTIAL_VARIABLES = (SUBSCRIPTION_TOKEN,)
CONFIG_KEYS = {"repo_root", "hidden_root", "deadline_seconds", "credential_var", "claude_path", "extra_env",
               "scratch_root", "real_home", "parent_marker"}
INSTRUCTION_FILES = ("CLAUDE.md", "CLAUDE.local.md", ".mcp.json", ".claude")
LATCH, ALERTS = "real-profile-changed.txt", "launcher-alerts.log"
ALERT_LOG_LIMIT_BYTES = 1 << 20  # ponytail: stops appending past 1 MiB; a benchmark that trips stops long before
CREDENTIAL_SHAPED = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH", "COOKIE")
SURFACE = (".claude/plugins", ".claude/settings.json", ".codex/config.toml", ".codex/plugins")
EXPORT_TOP_LEVEL = {".claude-plugin", "skills", "hooks"}
FORBIDDEN_COMPONENTS = {"evals", "tests"}
CHANGED, REFUSED, DEADLINE = 97, 2, 124
TAG = "sigma_bench_launcher"


class Refusal(Exception):
    """A guard failed; nothing has been started."""


class Interrupted(Exception):
    """The launcher itself was asked to stop."""


def within(child, parent):
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def load_config(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refusal("unreadable config %s: %s" % (path, exc)) from exc
    if not isinstance(raw, dict) or set(raw) - CONFIG_KEYS:
        raise Refusal("config must be an object with only the keys %s" % ", ".join(sorted(CONFIG_KEYS)))
    for key in ("repo_root", "hidden_root"):
        if not isinstance(raw.get(key), str) or not os.path.isabs(raw[key]):
            raise Refusal("config %s must be an absolute path" % key)
    deadline = raw.get("deadline_seconds")
    if (isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline)
            or deadline <= 0):
        raise Refusal("config deadline_seconds must be a positive number (the owner sets it; no default)")
    if raw.get("credential_var") == "ANTHROPIC_API_KEY":
        raise Refusal("config credential_var ANTHROPIC_API_KEY is refused: an API key bills pay-per-token and the "
                      "benchmark runs on the subscription; use %s (from `claude setup-token`)" % SUBSCRIPTION_TOKEN)
    if raw.get("credential_var") not in CREDENTIAL_VARIABLES:
        raise Refusal("config credential_var is required and must be %s (the subscription token from "
                      "`claude setup-token`); the name is configured, the value never is" % SUBSCRIPTION_TOKEN)
    if not (isinstance(raw.get("claude_path"), str) and os.path.isabs(raw["claude_path"])):
        raise Refusal("config claude_path is required: the absolute path of the one program that gets the "
                      "credential and must start in an empty profile")
    extra = raw.get("extra_env", [])
    if not isinstance(extra, list) or not all(isinstance(n, str) and n.isidentifier() for n in extra):
        raise Refusal("config extra_env must be a list of variable names")
    for name in extra:
        if name in PROFILE_VARIABLES or any(word in name.upper() for word in CREDENTIAL_SHAPED):
            raise Refusal("config extra_env %s is a profile or credential-shaped name" % name)
    for key in ("scratch_root", "real_home"):
        if raw.get(key) is not None and (not isinstance(raw[key], str) or not os.path.isabs(raw[key])):
            raise Refusal("config %s must be an absolute path" % key)
    if not raw.get("scratch_root"):
        raise Refusal("config scratch_root is required (the launcher's own scratch: hashes, alert log, dry-run profile)")
    check_outside(raw["scratch_root"], "scratch_root", raw)
    if raw.get("parent_marker") is not None and not isinstance(raw["parent_marker"], str):
        raise Refusal("config parent_marker must be text (default bench.py) or null to never signal a parent")
    if raw.get("real_home") and os.environ.get("SIGMA_LAUNCHER_TEST_MODE") != "1":
        raise Refusal("config real_home is for tests only (a wrong value would switch the guard off)")
    return raw


def real_home(config):
    if config.get("real_home"):
        return config["real_home"]
    import pwd  # the account database, not $HOME: the harness has overwritten HOME
    return pwd.getpwuid(os.getuid()).pw_dir


def checked_real_home(config):
    home = real_home(config)
    if not os.path.isdir(os.path.join(home, ".claude")):
        raise Refusal("no .claude under the real home %s: the profile guard would be vacuous" % home)
    return home


def _hash_file(digest, path):
    try:
        # O_NONBLOCK|O_NOFOLLOW then fstat: a FIFO swapped in after a separate lstat would still block the open
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                digest.update(b"special")
                return
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError as exc:
        digest.update(("unreadable:%s" % exc.errno).encode())


def digest_path(path):
    """Content hash of a file or tree; symlinks inside a tree are recorded, never followed."""
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
        rel = os.path.relpath(current, root)
        digest.update(b"dir\0" + rel.encode("utf-8", "surrogateescape") + b"\0")
        links = [d for d in dirnames if os.path.islink(os.path.join(current, d))]
        dirnames[:] = [d for d in dirnames if d not in links]
        for name in sorted(filenames + links):
            full = os.path.join(current, name)
            tag = os.path.join(rel, name).encode("utf-8", "surrogateescape")
            if os.path.islink(full):
                try:
                    target = os.readlink(full)
                except OSError:
                    target = "?"
                digest.update(b"link\0" + tag + b"\0" + target.encode("utf-8", "surrogateescape"))
            else:
                digest.update(b"file\0" + tag + b"\0")
                _hash_file(digest, full)
    return digest.hexdigest()


def surface_digests(home):
    return {rel: digest_path(os.path.join(home, rel)) for rel in SURFACE}


def check_not_real(path, label, home):
    real = os.path.realpath(home)
    if os.path.realpath(path) == real or any(within(path, os.path.join(real, p.split("/")[0])) for p in SURFACE):
        raise Refusal("%s is the real home or inside its .claude/.codex" % label)


def check_outside(path, label, config):
    for name in ("repo_root", "hidden_root"):
        if within(path, config[name]):
            raise Refusal("%s is inside the %s" % (label, name.replace("_", " ")))


def fresh_profile(config, home):
    """Dry-run mode: a profile this launcher makes (and removes), ignoring inherited variables."""
    scratch = config["scratch_root"]
    check_not_real(scratch, "scratch_root", home)
    os.makedirs(scratch, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="run-", dir=scratch)
    variables = {}
    for name, leaf in zip(PROFILE_VARIABLES, ("home", "claude-config", "codex-home", "tmp")):
        variables[name] = os.path.join(profile, leaf)
        os.mkdir(variables[name])
    return variables, profile


def harness_profile(environ, home):
    """The harness's own directories, checked by LOCATION only (a run and its scoring commands reuse one)."""
    values = [environ.get(name) for name in PROFILE_VARIABLES]
    if not all(values):
        raise Refusal("HOME, CLAUDE_CONFIG_DIR, CODEX_HOME and TMPDIR must all be supplied (the harness does)")
    resolved = {}
    for name, value in zip(PROFILE_VARIABLES, values):
        if not os.path.isabs(value) or not os.path.isdir(value):
            raise Refusal("%s must be an existing absolute directory" % name)
        resolved[name] = os.path.realpath(value)
    for label, path in resolved.items():
        check_not_real(path, label, home)
    if len(set(resolved.values())) != 4 or len({os.path.dirname(v) for v in resolved.values()}) != 1:
        raise Refusal("the four profile directories must be distinct and share one parent")
    profile = os.path.dirname(next(iter(resolved.values())))
    check_not_real(profile, "profile directory", home)
    return resolved, profile


def check_plugin_dirs(argv, config, home, profile, harness_mode):
    given = [i for i, arg in enumerate(argv) if arg.startswith("--plugin-dir")]
    if not given:
        return
    if len(given) > 1 or argv[given[0]] != "--plugin-dir" or given[0] + 1 >= len(argv):
        raise Refusal("exactly one '--plugin-dir <dir>' (separate argument) is allowed")
    plugin = os.path.realpath(argv[given[0] + 1])
    if not os.path.isdir(plugin):
        raise Refusal("--plugin-dir is not a directory")
    # (no separate repo/hidden/real-home checks: the sibling rule below implies them, and --dry-run takes no --plugin-dir)
    if harness_mode and os.path.dirname(plugin) != os.path.dirname(os.path.realpath(profile)):
        raise Refusal("--plugin-dir must be the harness's export, a sibling of the profile directory")
    top = set(os.listdir(plugin))
    if ".claude-plugin" not in top or top - EXPORT_TOP_LEVEL:
        raise Refusal("--plugin-dir is not a clean export (top level must be within %s and hold .claude-plugin)"
                      % " ".join(sorted(EXPORT_TOP_LEVEL)))
    for current, dirnames, filenames in os.walk(plugin, followlinks=False):
        for name in dirnames + filenames:
            if os.path.islink(os.path.join(current, name)) or name in FORBIDDEN_COMPONENTS:
                raise Refusal("--plugin-dir holds a link or an evals/tests path: %s" % name)


def is_claude(program, environ, config):
    resolved = shutil.which(program, path=environ.get("PATH", os.defpath)) or program
    return os.path.realpath(resolved) == os.path.realpath(config["claude_path"])


def build_environment(environ, variables, config, program):
    env = {name: environ[name] for name in PASS_THROUGH if name in environ}
    env.update({name: environ[name] for name in config.get("extra_env", []) if name in environ})
    env.update(variables)
    credential = config.get("credential_var")
    if credential and is_claude(program, environ, config) and credential in environ:
        env[credential] = environ[credential]
    return env


def require_authentication(argv, environ, config):
    """Refuse loudly, before anything starts, when a model run has no subscription token to authenticate with.

    Only a ``claude`` run that talks to the model needs it (not ``--version``, not scoring commands).  The message
    names the variable and never echoes any value or the environment.
    """
    credential = config.get("credential_var")
    if not credential or not is_claude(argv[0], environ, config) or argv[1:] == ["--version"]:
        return
    if not (environ.get(credential) or "").strip():
        raise Refusal("authentication is missing: %s is not set in the launcher's environment, so claude cannot "
                      "use the subscription (create it once with `claude setup-token`, then start the harness "
                      "with that variable only)" % credential)


def trip(spec, moved, why):
    """Record that the real profile changed: latch, alert log, and (outside --dry-run) stop the harness."""
    line = "REAL PROFILE CHANGED (%s): %s" % (why, ", ".join(moved))
    scratch = spec["scratch"]
    try:
        os.makedirs(scratch, exist_ok=True)
        with open(os.path.join(scratch, LATCH), "w", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    alert_to(scratch, line)
    parent = spec.get("parent")
    if parent:
        pid, marker = parent
        shown = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        if any(os.path.basename(word) == marker for word in shown.split()):  # only the harness: never a shell, a test runner or a subreaper that inherited us
            try:
                os.kill(pid, signal.SIGTERM)  # the harness unwinds on SIGTERM and writes its aborted report
            except OSError:
                pass
    return line


def supervise(spec, pipe_fd, argv):
    """Own session. Run ``argv`` in its OWN group; kill that group at the deadline, on EOF of ``pipe_fd`` (the
    launcher alone holds the write end, so EOF means it is gone, even by SIGKILL) and after a normal exit; then
    take the after-hash. Surviving the launcher is the point: the harness SIGKILLs the launcher's group."""
    code = 127
    try:
        child = subprocess.Popen(argv, start_new_session=True)  # close_fds: the command never sees the pipe
    except OSError as exc:
        print("%s: cannot start %s: %s" % (TAG, argv[0], exc.strerror), file=sys.stderr)
        child = None
    devnull = os.open(os.devnull, os.O_RDWR)  # a lingering stdout would stall the harness's communicate()
    for number in (0, 1, 2):
        os.dup2(devnull, number)
    end = time.monotonic() + spec["deadline"]
    try:
        while child is not None:
            code = child.poll()
            if code is not None:
                code = code if code >= 0 else 128 - code
                break
            if select.select([pipe_fd], [], [], 0.05)[0]:
                code = 128 + signal.SIGKILL
                break
            if time.monotonic() >= end:
                code = DEADLINE
                break
    finally:  # whatever happened above, the command's own group does not survive (a setsid child can: README)
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            child.wait()
    after = surface_digests(spec["home"])
    moved = [rel for rel in SURFACE if after[rel] != spec["before"][rel]]
    if moved:
        trip(spec, moved, "changed during the run")
        return CHANGED
    return code


def _raise_interrupted(signum, _frame):
    raise Interrupted(signum)


def run_supervised(env, spec, argv):
    read_end, write_end = os.pipe()
    command = [sys.executable, "-B", os.path.abspath(__file__), "--supervise", json.dumps(spec), str(read_end), "--",
               *argv]
    handlers = {n: signal.signal(n, _raise_interrupted) for n in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    proc = None
    result = None
    try:
        proc = subprocess.Popen(command, env=env, start_new_session=True, pass_fds=(read_end,))
        os.close(read_end)
        read_end = None
        result = proc.wait()
    except Interrupted as stop:
        result = 128 + stop.args[0]
    finally:
        os.close(write_end)  # EOF: the supervisor kills the command's group and takes the after-hash
        if read_end is not None:
            os.close(read_end)
        if proc is not None:
            proc.wait()
        for number, handler in handlers.items():
            signal.signal(number, handler)
    return result


def alert_to(scratch, text):
    """Append to the alert log (the harness discards stderr, so this is where the operator looks); bounded."""
    path = os.path.join(scratch, ALERTS)
    try:
        os.makedirs(scratch, exist_ok=True)
        if not os.path.exists(path) or os.path.getsize(path) < ALERT_LOG_LIMIT_BYTES:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")
    except OSError:
        pass


def refuse_ancestors(cwd):
    for parent in Path(os.path.realpath(cwd)).parents:
        for name in INSTRUCTION_FILES:
            if (parent / name).exists():
                raise Refusal("%s exists in a parent of the working directory (%s); Claude Code may read it" %
                              (name, parent))


def launch(argv, config_path, dry_run, environ):
    if os.name != "posix":
        raise Refusal("process-group control needs a POSIX host; not supported here")
    config = load_config(config_path)
    # before ANY write, including the alert log: a scratch_root inside the real profile is never written to
    check_not_real(config["scratch_root"], "scratch_root", real_home(config))
    try:
        return _launch(argv, config, dry_run, environ)
    except Refusal as exc:
        alert_to(config["scratch_root"], "REFUSED: %s" % exc)
        raise


def _launch(argv, config, dry_run, environ):
    if within(__file__, config["repo_root"]):
        raise Refusal("the launcher must be installed outside the repository")
    latch = os.path.join(config["scratch_root"], LATCH)
    if os.path.exists(latch):
        raise Refusal("an earlier run changed the real profile (%s); inspect it, then delete that file" % latch)
    if not argv:
        raise Refusal("usage: launcher [--config F] [--dry-run] -- <command> [args...]")
    if dry_run and (len(argv) != 2 or argv[1] != "--version"):
        raise Refusal("--dry-run runs '<command> --version' only")
    if dry_run:
        config = dict(config, credential_var=None)
    home = checked_real_home(config)
    refuse_ancestors(os.getcwd())
    made = None
    if dry_run:
        variables, profile = fresh_profile(config, home)
        made = profile
    else:
        variables, profile = harness_profile(environ, home)
    try:
        run_dir = os.path.dirname(profile)
        check_outside(profile, "profile directory", config)
        check_outside(run_dir, "profile's parent", config)
        if argv[1:] != ["--version"] and (within(config["scratch_root"], run_dir) or
                                          within(run_dir, config["scratch_root"])):
            # (a bare --version runs no agent; the harness's version profile lives in the system temp directory)
            raise Refusal("config scratch_root overlaps the run's directory: an agent there could reach the latch")
        check_plugin_dirs(argv, config, home, profile, made is None)
        require_authentication(argv, environ, config)
        if is_claude(argv[0], environ, config):
            for name in ("HOME", "CLAUDE_CONFIG_DIR"):
                if os.listdir(variables[name]):
                    raise Refusal("%s is not fresh: claude must start in an empty profile" % name)
        env = build_environment(environ, variables, config, argv[0])
        marker = config.get("parent_marker", "bench.py")
        spec = {"deadline": config["deadline_seconds"], "home": home, "scratch": config["scratch_root"],
                "before": surface_digests(home),
                "parent": [os.getppid(), marker] if marker and not dry_run else None}
        code = run_supervised(env, spec, argv)
    finally:
        if made:
            shutil.rmtree(made, ignore_errors=True)
    if code == CHANGED:
        print("%s: REAL PROFILE CHANGED; see %s in %s" % (TAG, LATCH, config["scratch_root"]), file=sys.stderr)
    elif code == DEADLINE:
        print("%s: deadline of %s s reached; process group killed" % (TAG, config["deadline_seconds"]),
              file=sys.stderr)
    return code


def main(args):
    if args[:1] == ["--supervise"]:  # internal: started by run_supervised, never by an operator
        sep = args.index("--")
        return supervise(json.loads(args[1]), int(args[2]), args[sep + 1:])
    config_path, dry_run = Path(__file__).resolve().with_suffix(".json"), False
    while args and args[0] != "--":
        if args[0] == "--dry-run":
            dry_run = True
        elif args[0] == "--config" and len(args) > 1:
            config_path = args[1]
            args = args[1:]
        elif args[0] in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        else:
            print("%s: REFUSED: unknown option %s" % (TAG, args[0]), file=sys.stderr)
            return REFUSED
        args = args[1:]
    try:
        return launch(args[1:], config_path, dry_run, dict(os.environ))
    except Refusal as exc:
        print("%s: REFUSED: %s" % (TAG, exc), file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

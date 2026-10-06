"""Hermetic controls for the benchmark isolation launcher (#593).

Every test installs a COPY of ``evals/bench/launcher/sigma_bench_launcher.py`` outside the repository (the gesture
the README gives, and the only place the harness accepts) and drives it with a fake ``claude`` executable.  No model,
no network, no money.  The "real" profile is a temp directory named by the config's ``real_home``; the operator's
actual home is never read, hashed or written.  Secret-looking values are built at run time.
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER_SRC = ROOT / "evals" / "bench" / "launcher" / "sigma_bench_launcher.py"
README = ROOT / "evals" / "bench" / "launcher" / "README.md"
ARMS_TEST = ROOT / "tests" / "test_bench_arms_isolation.py"
BENCH = ROOT / "evals" / "bench" / "bench.py"
COST_EVIDENCE = ROOT / "docs" / "launch" / "evidence" / "cost-calibration.md"
SURFACE = (".claude/plugins/marketplace.json", ".claude/settings.json", ".codex/config.toml",
           ".codex/plugins/index.json")
SURFACE_NAMES = (".claude/plugins", ".claude/settings.json", ".codex/config.toml", ".codex/plugins")
ALLOWED_ENV = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "SHELL", "USER", "LOGNAME", "HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TMPDIR",
               "__CF_USER_TEXT_ENCODING", "CLAUDE_CODE_OAUTH_TOKEN"}

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the launcher is POSIX-only by design")

FAKE_CLAUDE = r'''#!__PY__
import json, os, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
def dump(path, extra=None):
    record = {"env": dict(os.environ), "argv": sys.argv[1:], "cwd": os.getcwd()}
    record.update(extra or {})
    with open(path, "w") as handle:
        json.dump(record, handle)
if sys.argv[1:] == ["--version"]:
    marker = os.path.join(here, "dump-on-version")
    if os.path.exists(marker):
        dump(open(marker).read().strip())
    print("fake-claude 0.0.1")
    sys.exit(0)
command = json.loads(sys.stdin.read() or "{}")
if "dump" in command:
    dump(command["dump"])
for path, text in command.get("write", {}).items():
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)
if command.get("lsof_parent"):
    shown = subprocess.run(["lsof", "-a", "-p", str(os.getppid()), "-d", "0,1,2", "-Fn"],
                           capture_output=True, text=True).stdout
    with open(command["lsof_parent"], "w") as handle:
        handle.write(shown)
if command.get("spawn"):
    with open(command["spawn"], "w") as handle:
        handle.write(str(subprocess.Popen(["sleep", "300"]).pid))
if command.get("sleep"):
    time.sleep(command["sleep"])
sys.exit(command.get("exit", 0))
'''


def _need_source():
    assert LAUNCHER_SRC.is_file(), "evals/bench/launcher/sigma_bench_launcher.py is not implemented"


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(state.strip()) and not state.strip().startswith("Z")


def _wait_dead(pid, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


def _wait_file(path, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return True
        time.sleep(0.05)
    return False


class World:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.real = tmp_path / "real-home"
        self.repo = tmp_path / "repo"
        self.hidden = tmp_path / "hidden"
        self.installed = tmp_path / "installed"
        self.bin = tmp_path / "bin"
        self.runs = tmp_path / "runs"
        (self.real / ".claude").mkdir(parents=True)
        for rel in SURFACE:
            path = self.real / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original\n", encoding="utf-8")
        for path in (self.repo, self.hidden, self.installed, self.bin, self.runs):
            path.mkdir()
        self.launcher = self.installed / "sigma_bench_launcher.py"
        if LAUNCHER_SRC.is_file():
            shutil.copy(LAUNCHER_SRC, self.launcher)  # the README's install gesture: a copy, outside the repo
            self.launcher.chmod(0o700)
        self.claude = self.bin / "claude"
        self.claude.write_text(FAKE_CLAUDE.replace("__PY__", sys.executable), encoding="utf-8")
        self.claude.chmod(0o755)
        self.config = {"repo_root": str(self.repo), "hidden_root": str(self.hidden), "deadline_seconds": 30,
                       "credential_var": "CLAUDE_CODE_OAUTH_TOKEN", "claude_path": str(self.bin / "claude"),
                       "extra_env": [], "scratch_root": str(tmp_path / "self-scratch"), "parent_marker": None,
                       "real_home": str(self.real)}
        self.write_config()
        self.count = 0

    def write_config(self, **changes):
        self.config.update(changes)
        (self.installed / "sigma_bench_launcher.json").write_text(json.dumps(self.config), encoding="utf-8")

    def new_run(self, parent=None):
        """A harness-style run directory with fresh empty profile directories and the harness's variables."""
        self.count += 1
        run = (parent or self.runs) / ("run-%d" % self.count)
        profile = run / "profile"
        variables = {}
        for name, leaf in zip(("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TMPDIR"),
                              ("home", "claude-config", "codex-home", "tmp")):
            (profile / leaf).mkdir(parents=True)
            variables[name] = str(profile / leaf)
        return run, variables

    def env(self, variables=None, **extra):
        env = {"PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8",
               "GH_TOKEN": "t-" + uuid.uuid4().hex, "AWS_SECRET_ACCESS_KEY": "a-" + uuid.uuid4().hex,
               "SSH_AUTH_SOCK": "/tmp/agent.sock", "XDG_CONFIG_HOME": "/xdg", "ANTHROPIC_BASE_URL": "https://x.test",
               "CLAUDE_CODE_USE_BEDROCK": "1", "SIGMA_BENCH_ARM_NAME": "plain", "HOME": str(self.real),
               "SIGMA_LAUNCHER_TEST_MODE": "1",
               "CLAUDE_CODE_OAUTH_TOKEN": "o-default-" + uuid.uuid4().hex}
        env.update(variables or {})
        env.update(extra)
        return env

    def launch(self, argv, *, env=None, stdin="", flags=(), timeout=60, cwd=None):
        _need_source()
        return subprocess.run([str(self.launcher), *flags, "--", *argv], input=stdin, env=env or self.env(),
                              cwd=str(cwd or self.tmp), capture_output=True, text=True, timeout=timeout)

    def claude_run(self, command, variables=None, **kwargs):
        if variables is None:
            _, variables = self.new_run()
        return self.launch([str(self.claude), "-p"], env=self.env(variables, **kwargs.pop("extra", {})),
                           stdin=json.dumps(command), **kwargs)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _dump(world, **kwargs):
    path = world.tmp / ("dump-%s.json" % uuid.uuid4().hex)
    result = world.claude_run({"dump": str(path)}, **kwargs)
    return result, (json.loads(path.read_text()) if path.exists() else None)


def test_profile_variables_are_the_harness_dirs_and_nothing_else_leaks(world):
    run, variables = world.new_run()
    result, record = _dump(world, variables=variables)

    assert result.returncode == 0, result.stderr
    env = record["env"]
    assert set(env) <= ALLOWED_ENV, sorted(set(env) - ALLOWED_ENV)
    for name, value in variables.items():
        assert os.path.realpath(env[name]) == os.path.realpath(value)
    real = os.path.realpath(world.real)
    assert all(not os.path.realpath(env[n]).startswith(real) for n in variables)
    assert len({env[n] for n in variables}) == 4


def test_only_the_subscription_token_reaches_the_claude_path_and_never_scoring(world):
    key = "k-" + uuid.uuid4().hex
    token = "o-" + uuid.uuid4().hex
    extra = {"ANTHROPIC_API_KEY": key, "CLAUDE_CODE_OAUTH_TOKEN": token}

    result, record = _dump(world, extra=extra)
    assert record["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == token
    assert "ANTHROPIC_API_KEY" not in record["env"], "a stray API key would switch billing to pay-per-token"
    assert key not in result.stdout + result.stderr and token not in result.stdout + result.stderr

    _, variables = world.new_run()
    scoring = world.tmp / "scoring.json"
    probe = "import json, os; json.dump(dict(os.environ), open(%r, 'w'))" % str(scoring)
    done = world.launch([sys.executable, "-c", probe], env=world.env(variables, **extra))
    assert done.returncode == 0, done.stderr
    scored = json.loads(scoring.read_text())
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in scored and "ANTHROPIC_API_KEY" not in scored

    lookalike = world.tmp / "other-bin" / "claude"  # a program merely NAMED claude is not the configured one
    lookalike.parent.mkdir()
    shutil.copy(world.claude, lookalike)
    other = world.tmp / "lookalike.json"
    _, variables = world.new_run()
    done = world.launch([str(lookalike), "-p"], env=world.env(variables, **extra), stdin=json.dumps({"dump": str(other)}))
    assert done.returncode == 0 and "CLAUDE_CODE_OAUTH_TOKEN" not in json.loads(other.read_text())["env"]

    world.write_config(claude_path=None)
    refused = world.claude_run({"exit": 0}, extra=extra)
    assert refused.returncode == 2 and "claude_path" in refused.stderr


@pytest.mark.parametrize("absent", ["missing", "empty", "blank"])
def test_a_claude_run_without_the_subscription_token_refuses_loudly_and_starts_nothing(world, absent):
    """Control: without this guard an unauthenticated first run only shows up later as an unreadable run."""
    secret = "k-" + uuid.uuid4().hex
    out = world.tmp / "ran.json"
    _, variables = world.new_run()
    env = world.env(variables, ANTHROPIC_API_KEY=secret)
    if absent == "missing":
        del env["CLAUDE_CODE_OAUTH_TOKEN"]
    else:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = "" if absent == "empty" else "  "

    done = world.launch([str(world.claude), "-p"], env=env, stdin=json.dumps({"dump": str(out)}))

    assert done.returncode == 2 and "REFUSED" in done.stderr
    assert "authentication" in done.stderr and "CLAUDE_CODE_OAUTH_TOKEN" in done.stderr
    assert "claude setup-token" in done.stderr
    assert not out.exists(), "claude was started without credentials"
    alerts = (world.tmp / "self-scratch" / "launcher-alerts.log").read_text()
    assert "CLAUDE_CODE_OAUTH_TOKEN" in alerts and secret not in alerts + done.stderr + done.stdout


def test_the_version_probe_and_scoring_do_not_need_the_token(world):
    _, variables = world.new_run()
    env = world.env(variables)
    del env["CLAUDE_CODE_OAUTH_TOKEN"]
    done = world.launch([str(world.claude), "--version"], env=env)
    assert done.returncode == 0 and "fake-claude" in done.stdout, done.stderr
    done = world.launch([sys.executable, "-c", "pass"], env=env)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("value, needle", [(None, "required"), ("ANTHROPIC_API_KEY", "pay-per-token")])
def test_the_config_must_name_the_subscription_token_and_never_an_api_key(world, value, needle):
    world.write_config(credential_var=value)
    done = world.claude_run({"exit": 0})
    assert done.returncode == 2 and "credential_var" in done.stderr and needle in done.stderr


def test_extra_env_passes_named_non_credential_names_only(world):
    world.write_config(extra_env=["HTTPS_PROXY", "SSL_CERT_FILE"])
    _, record = _dump(world, extra={"HTTPS_PROXY": "http://proxy.test:3128", "SSL_CERT_FILE": "/ca.pem",
                                    "NO_PROXY": "x"})
    assert record["env"]["HTTPS_PROXY"] == "http://proxy.test:3128" and "NO_PROXY" not in record["env"]
    for name in ("ANTHROPIC_AUTH_TOKEN", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "HOME"):
        world.write_config(extra_env=[name])
        refused = world.claude_run({"exit": 0})
        assert refused.returncode == 2 and "extra_env" in refused.stderr, name


def test_dry_run_makes_a_fresh_profile_under_an_inherited_home_and_removes_it(world):
    """The owner's terminal sets HOME and TMPDIR but not CLAUDE_CONFIG_DIR or CODEX_HOME."""
    out = world.tmp / "version-env.json"
    (world.bin / "dump-on-version").write_text(str(out))
    seen = []
    for _ in range(2):
        done = world.launch(["claude", "--version"], flags=("--dry-run",),
                            env=world.env(HOME=str(world.real), TMPDIR=str(world.tmp)))
        assert done.returncode == 0, done.stderr
        env = json.loads(out.read_text())["env"]
        homes = {n: env[n] for n in ("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TMPDIR")}
        assert all(os.path.realpath(v).startswith(os.path.realpath(world.tmp / "self-scratch")) for v in homes.values())
        assert not any(os.path.exists(v) for v in homes.values()), "the launcher's own profile must be removed"
        seen.append(homes["HOME"])
    assert seen[0] != seen[1]


def test_profile_in_the_wrong_place_is_refused_but_a_used_one_is_accepted(world):
    probe = {"dump": str(world.tmp / "d.json")}
    _, variables = world.new_run()
    (pathlib.Path(variables["CLAUDE_CONFIG_DIR"]) / "transcript").write_text("x")
    (pathlib.Path(variables["TMPDIR"]) / "scratch-file").write_text("x")
    reused = world.launch([sys.executable, "-c", "pass"], env=world.env(variables))  # scoring reuses a profile
    assert reused.returncode == 0, reused.stderr
    stale = world.claude_run(probe, variables=variables)  # claude itself must start in an empty one
    assert stale.returncode == 2 and "not fresh" in stale.stderr and not (world.tmp / "d.json").exists()

    _, variables = world.new_run()
    inside = world.real / ".claude" / "elsewhere"
    inside.mkdir()
    variables["CLAUDE_CONFIG_DIR"] = str(inside)
    result = world.claude_run(probe, variables=variables)
    assert result.returncode == 2 and "real home" in result.stderr

    _, variables = world.new_run()
    variables["HOME"] = str(world.real)
    assert "real home" in world.claude_run(probe, variables=variables).stderr

    _, variables = world.new_run()
    del variables["CODEX_HOME"]
    result = world.claude_run(probe, variables=variables)
    assert result.returncode == 2 and "must all be supplied" in result.stderr

    _, variables = world.new_run()
    variables["TMPDIR"] = variables["HOME"]
    result = world.claude_run(probe, variables=variables)
    assert result.returncode == 2 and "distinct" in result.stderr
    assert not (world.tmp / "d.json").exists()


def test_scratch_inside_repository_or_hidden_root_is_refused(world):
    for root, name in ((world.repo, "repository"), (world.hidden, "hidden")):
        _, variables = world.new_run(parent=root)
        result = world.claude_run({"dump": str(world.tmp / "d.json")}, variables=variables)
        assert result.returncode == 2 and "is inside the" in result.stderr, result.stderr
        world.write_config(scratch_root=str(root / "scratch"))
        result = world.launch(["claude", "--version"], flags=("--dry-run",), env=world.env())
        assert result.returncode == 2 and "is inside the" in result.stderr, result.stderr
        assert not (root / "scratch").exists(), "a refused scratch directory must not be created"
    assert not (world.tmp / "d.json").exists()


@pytest.mark.parametrize("rel", SURFACE)
def test_real_profile_surface_change_exits_97_and_latches(world, rel):
    clean = world.claude_run({"exit": 0})
    assert clean.returncode == 0, clean.stderr

    changed = world.claude_run({"write": {str(world.real / rel): "tampered\n"}, "exit": 0})
    assert changed.returncode == 97, (changed.returncode, changed.stderr)
    surface = next(s for s in SURFACE_NAMES if rel.startswith(s))
    assert "tampered" not in changed.stderr and changed.stdout == "", "diagnostics go to stderr only"
    scratch = world.tmp / "self-scratch"
    latch = (scratch / "real-profile-changed.txt").read_text()
    assert "REAL PROFILE CHANGED" in latch and surface in latch and "tampered" not in latch
    assert surface in (scratch / "launcher-alerts.log").read_text()

    marker = world.tmp / "after-change.json"
    after = world.claude_run({"dump": str(marker)})
    assert after.returncode == 2 and "earlier run changed the real profile" in after.stderr
    assert not marker.exists(), "the command must not start while the latch exists"
    (scratch / "real-profile-changed.txt").unlink()  # the documented lever, after inspecting
    (world.real / rel).write_text("original\n")
    assert world.claude_run({"dump": str(marker)}).returncode == 0


def test_change_in_a_run_the_harness_killed_is_still_caught(world):
    """The harness SIGKILLs the launcher's group at its own deadline; the supervisor outlives it and hashes."""
    _need_source()
    pidfile = world.tmp / "sleeper.pid"
    _, variables = world.new_run()
    proc = subprocess.Popen([str(world.launcher), "--", str(world.claude), "-p"], env=world.env(variables),
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True, text=True, cwd=str(world.tmp))
    proc.stdin.write(json.dumps({"write": {str(world.real / ".claude" / "settings.json"): "tampered\n"},
                                 "spawn": str(pidfile), "sleep": 120}))
    proc.stdin.close()
    assert _wait_file(pidfile)
    time.sleep(0.3)
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()
    latch = world.tmp / "self-scratch" / "real-profile-changed.txt"
    assert _wait_file(latch), "nobody took the after-hash once the launcher was killed"
    assert ".claude/settings.json" in latch.read_text() and _wait_dead(int(pidfile.read_text()))


def test_parent_is_signalled_only_when_it_is_the_harness(world):
    """The marker guards SIGTERM: a parent that is not the harness (this test runner) is never signalled."""
    # (an xdist worker's command line does not even say pytest, so the control is "this process survives", not a match)
    world.write_config(parent_marker="no-such-marker-in-any-command-line")
    changed = world.claude_run({"write": {str(world.real / ".claude" / "settings.json"): "tampered\n"}})
    assert changed.returncode == 97  # reaching this line at all means pytest survived


def test_ancestor_instruction_files_and_missing_claude_dir_refuse(world):
    deep = world.tmp / "a" / "b"
    deep.mkdir(parents=True)
    for name in ("CLAUDE.md", "CLAUDE.local.md", ".mcp.json", ".claude"):
        (world.tmp / "a" / name).mkdir() if name == ".claude" else (world.tmp / "a" / name).write_text("x")
        _, variables = world.new_run()
        marker = world.tmp / "anc.json"
        done = world.launch([str(world.claude), "-p"], env=world.env(variables), cwd=deep,
                            stdin=json.dumps({"dump": str(marker)}))
        assert done.returncode == 2 and name in done.stderr and not marker.exists(), name
        shutil.rmtree(world.tmp / "a" / name) if name == ".claude" else (world.tmp / "a" / name).unlink()
    shutil.rmtree(world.real / ".claude")
    refused = world.claude_run({"exit": 0})
    assert refused.returncode == 2 and "vacuous" in refused.stderr


def test_scratch_root_overlapping_the_runs_or_the_harness_scratch_is_refused(world):
    run, variables = world.new_run()
    for bad in (run.parent, run, run / "profile"):
        world.write_config(scratch_root=str(bad / "launcher") if bad != run.parent else str(bad))
        done = world.claude_run({"exit": 0}, variables=variables)
        assert done.returncode == 2 and "overlaps" in done.stderr, (bad, done.stderr)
    world.write_config(scratch_root=str(world.tmp / "elsewhere"))
    assert world.claude_run({"exit": 0}, variables=variables).returncode == 0


@pytest.mark.skipif(shutil.which("lsof") is None, reason="needs lsof to read the supervisor's descriptors")
def test_the_supervisor_holds_none_of_the_harnesss_stdio(world):
    """A supervisor still holding the harness's stdout would stall its communicate() after a kill."""
    out = world.tmp / "supervisor-fds.txt"
    done = world.claude_run({"lsof_parent": str(out)})
    assert done.returncode == 0, done.stderr
    names = [line for line in out.read_text().splitlines() if line.startswith("n")]
    assert len(names) == 3 and set(names) == {"n/dev/null"}, names


def test_scratch_root_inside_the_real_profile_is_refused_without_writing_there(world):
    victim = world.real / ".claude" / "plugins" / "scr"
    world.write_config(scratch_root=str(victim))
    before = _tree_listing(world.real)
    harness = world.claude_run({"exit": 0})
    dry = world.launch(["claude", "--version"], flags=("--dry-run",), env=world.env())
    for done in (harness, dry):
        assert done.returncode == 2 and "scratch_root is the real home or inside" in done.stderr, done.stderr
    assert not victim.exists() and _tree_listing(world.real) == before, "a refusal must not write into the real profile"


def _tree_listing(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_a_fifo_in_the_hashed_surface_does_not_hang_the_hash(world):
    _need_source()
    os.mkfifo(world.real / ".claude" / "plugins" / "pipe")
    world.write_config(deadline_seconds=5)
    done = world.claude_run({"exit": 0}, timeout=30)
    assert done.returncode == 0, done.stderr


def test_alert_log_stops_growing_at_its_bound(tmp_path):
    _need_source()
    spec = importlib.util.spec_from_file_location("launcher_bound", LAUNCHER_SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ALERT_LOG_LIMIT_BYTES = 100
    for _ in range(50):
        module.alert_to(str(tmp_path), "x" * 40)
    assert (tmp_path / "launcher-alerts.log").stat().st_size < 100 + 41


def test_real_home_override_is_refused_outside_test_mode(world):
    env = world.env(world.new_run()[1])
    del env["SIGMA_LAUNCHER_TEST_MODE"]
    done = world.launch([str(world.claude), "-p"], env=env, stdin="{}")
    assert done.returncode == 2 and "tests only" in done.stderr


def test_refusals_land_in_the_alert_log(world):
    _, variables = world.new_run()
    del variables["CODEX_HOME"]
    assert world.claude_run({"exit": 0}, variables=variables).returncode == 2
    log = (world.tmp / "self-scratch" / "launcher-alerts.log").read_text()
    assert "REFUSED: HOME, CLAUDE_CONFIG_DIR, CODEX_HOME and TMPDIR must all be supplied" in log


def test_real_home_comes_from_the_account_database_not_home(monkeypatch, tmp_path):
    _need_source()
    import pwd
    spec = importlib.util.spec_from_file_location("launcher_unit", LAUNCHER_SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("HOME", str(tmp_path / "harness-fresh-home"))
    assert module.real_home({}) == pwd.getpwuid(os.getuid()).pw_dir
    assert module.real_home({"real_home": "/x"}) == "/x"
    for rel, digest in module.surface_digests(str(tmp_path / "empty")).items():
        assert digest == "absent", rel


def test_deadline_kills_the_whole_process_group(world):
    world.write_config(deadline_seconds=1)
    pidfile = world.tmp / "sleeper.pid"
    started = time.monotonic()
    result = world.claude_run({"spawn": str(pidfile), "sleep": 120})
    assert result.returncode == 124, (result.returncode, result.stderr)
    assert time.monotonic() - started < 20
    assert _wait_dead(int(pidfile.read_text())), "a grandchild survived the deadline"


def test_killing_the_launcher_kills_the_command(world):
    _need_source()
    pidfile = world.tmp / "sleeper.pid"
    _, variables = world.new_run()
    proc = subprocess.Popen([str(world.launcher), "--", str(world.claude), "-p"], env=world.env(variables),
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True, text=True, cwd=str(world.tmp))
    proc.stdin.write(json.dumps({"spawn": str(pidfile), "sleep": 120}))
    proc.stdin.close()
    assert _wait_file(pidfile)
    sleeper = int(pidfile.read_text())
    os.killpg(proc.pid, signal.SIGKILL)  # what the harness does at its own deadline
    proc.wait()
    assert _wait_dead(sleeper), "SIGKILL of the launcher orphaned the command's group"

    pidfile.unlink()
    _, variables = world.new_run()
    proc = subprocess.Popen([str(world.launcher), "--", str(world.claude), "-p"], env=world.env(variables),
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True, text=True, cwd=str(world.tmp))
    proc.stdin.write(json.dumps({"spawn": str(pidfile), "sleep": 120}))
    proc.stdin.close()
    assert _wait_file(pidfile)
    sleeper = int(pidfile.read_text())
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) == 128 + signal.SIGTERM
    assert _wait_dead(sleeper)


def _export(run, **kinds):
    plugin = run / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text("{}")
    (plugin / "skills" / "x").mkdir(parents=True)
    (plugin / "skills" / "x" / "SKILL.md").write_text("# x\n")
    (plugin / "hooks").mkdir()
    return plugin


def test_plugin_dir_only_from_a_clean_sibling_export(world):
    def attempt(plugin, run_variables, extra_args=()):
        out = world.tmp / ("p-%s.json" % uuid.uuid4().hex)
        done = world.launch([str(world.claude), "-p", "--plugin-dir", str(plugin), *extra_args],
                            env=world.env(run_variables), stdin=json.dumps({"dump": str(out)}))
        return done, out

    run, variables = world.new_run()
    good = _export(run)
    done, out = attempt(good, variables)
    assert done.returncode == 0, done.stderr
    assert json.loads(out.read_text())["argv"][-2:] == ["--plugin-dir", str(good)]

    cases = {}
    run, variables = world.new_run()
    elsewhere = _export(world.tmp / "not-a-sibling")
    cases["sibling"] = (elsewhere, variables, ())
    run, variables = world.new_run()
    bad = _export(run)
    (bad / "docs").mkdir()
    cases["top level"] = (bad, variables, ())
    run, variables = world.new_run()
    bad = _export(run)
    (bad / "skills" / "x" / "evals").mkdir()
    cases["evals component"] = (bad, variables, ())
    run, variables = world.new_run()
    bad = _export(run)
    (bad / "skills" / "link").symlink_to(world.real)
    cases["link"] = (bad, variables, ())
    run, variables = world.new_run()
    bad = _export(run)
    shutil.rmtree(bad / ".claude-plugin")
    cases["no manifest"] = (bad, variables, ())
    run, variables = world.new_run()
    cases["two"] = (_export(run), variables, ("--plugin-dir", str(world.tmp)))
    run, variables = world.new_run()
    in_repo = _export(world.repo / "scratch-run")
    cases["inside repo"] = (in_repo, variables, ())
    for name, (plugin, run_variables, extra) in cases.items():
        done, out = attempt(plugin, run_variables, extra)
        assert done.returncode == 2 and "REFUSED" in done.stderr, (name, done.returncode, done.stderr)
        assert not out.exists(), "%s: claude was started anyway" % name

    run, variables = world.new_run()
    done = world.launch([str(world.claude), "-p", "--plugin-dir=" + str(_export(run))], env=world.env(variables))
    assert done.returncode == 2 and "separate argument" in done.stderr


def test_dry_run_starts_version_only(world):
    out = world.tmp / "version-env.json"
    (world.bin / "dump-on-version").write_text(str(out))
    token = "o-" + uuid.uuid4().hex
    done = world.launch(["claude", "--version"], flags=("--dry-run",),
                        env=world.env(CLAUDE_CODE_OAUTH_TOKEN=token))
    assert done.returncode == 0 and "fake-claude" in done.stdout, done.stderr
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in json.loads(out.read_text())["env"]
    bare = world.env()
    del bare["CLAUDE_CODE_OAUTH_TOKEN"]
    done = world.launch(["claude", "--version"], flags=("--dry-run",), env=bare)
    assert done.returncode == 0, "the dry run needs no credential"

    for argv in (["claude", "-p"], ["claude", "--version", "--help"], ["claude"]):
        refused = world.launch(argv, flags=("--dry-run",), env=world.env())
        assert refused.returncode == 2 and "--dry-run" in refused.stderr


@pytest.mark.parametrize("change", ["no deadline", "unknown key", "bad credential", "relative repo", "no file",
                                    "launcher in repo", "no scratch", "scratch in repo", "relative claude"])
def test_config_errors_and_in_repo_launcher_refuse(world, change):
    config = dict(world.config)
    marker = world.tmp / "ran.json"
    if change == "no deadline":
        del config["deadline_seconds"]
    elif change == "unknown key":
        config["deadlne_seconds"] = 5
    elif change == "bad credential":
        config["credential_var"] = "GH_TOKEN"
    elif change == "relative repo":
        config["repo_root"] = "repo"
    elif change == "relative claude":
        config["claude_path"] = "claude"
    elif change == "no scratch":
        del config["scratch_root"]
    elif change == "scratch in repo":
        config["scratch_root"] = str(world.repo / "scratch")
    elif change == "launcher in repo":
        config["repo_root"] = str(world.installed)
    config_file = world.installed / "sigma_bench_launcher.json"
    if change == "no file":
        config_file.unlink()
    else:
        config_file.write_text(json.dumps(config), encoding="utf-8")
    result = world.claude_run({"dump": str(marker)})
    assert result.returncode == 2 and "REFUSED" in result.stderr, result.stderr
    assert not marker.exists()


def test_exit_status_and_stdout_pass_through(world):
    assert world.claude_run({"exit": 3}).returncode == 3
    _, variables = world.new_run()
    version = world.launch([str(world.claude), "--version"], env=world.env(variables))
    assert version.returncode == 0 and version.stdout == "fake-claude 0.0.1\n" and version.stderr == ""
    _, variables = world.new_run()
    missing = world.launch(["no-such-claude-binary"], env=world.env(variables))
    assert missing.returncode == 127


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FAKE_HARNESS_CLAUDE = r'''#!%(py)s
import json, os, sys
args = sys.argv[1:]
if "--version" in args:
    print("fake-claude 0.0.1"); sys.exit(0)
sys.stdin.read()
session = args[args.index("--session-id") + 1]
with open(%(dump)r, "a") as handle:
    handle.write(json.dumps({"env": dict(os.environ), "argv": args}) + "\n")
config = os.environ["CLAUDE_CONFIG_DIR"]
os.makedirs(os.path.join(config, "projects", "-fake"), exist_ok=True)
with open(os.path.join(config, "projects", "-fake", session + ".jsonl"), "w") as handle:
    handle.write(json.dumps({"type": "assistant", "timestamp": "2026-10-01T00:00:00Z",
        "message": {"id": "m1", "role": "assistant", "model": "claude-test", "usage": {"input_tokens": 0,
        "output_tokens": 1000, "cache_read_input_tokens": 0, "cache_creation": {
        "ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}}}}) + "\n")
%(extra)s
'''
WRITES_INTO_PROFILE = [sys.executable, "-c",
                       "import os, pathlib; pathlib.Path(os.environ['TMPDIR'], 'x').write_text('1'); "
                       "pathlib.Path(os.environ['HOME'], 'y').write_text('1')"]


def _harness(world, monkeypatch, extra="", ids=("one",)):
    _need_source()
    arms_test = _load(ARMS_TEST, "arms_isolation_helpers")
    bench = _load(BENCH, "bench_for_launcher")
    dump = world.tmp / "harness-dump.jsonl"
    fake = world.bin / "harness-claude"
    fake.write_text(FAKE_HARNESS_CLAUDE % {"py": sys.executable, "dump": str(dump), "extra": extra},
                    encoding="utf-8")
    fake.chmod(0o755)
    bundles = world.hidden / "bundles"
    hidden = arms_test._hidden(world.tmp, ids=ids, root=bundles)
    manifest = arms_test._manifest(world.tmp, ids=ids, visible=WRITES_INTO_PROFILE)
    world.write_config(hidden_root=str(bundles), credential_var="CLAUDE_CODE_OAUTH_TOKEN", claude_path=str(fake),
                       parent_marker=None)
    monkeypatch.setenv("SIGMA_LAUNCHER_TEST_MODE", "1")
    monkeypatch.setenv("HOME", str(world.real))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "o-" + uuid.uuid4().hex)
    monkeypatch.setenv("GH_TOKEN", "t-" + uuid.uuid4().hex)
    for name in ("CI", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(name, raising=False)
    common = dict(claude=str(fake), model="claude-test", permission_mode="acceptEdits", belt_usd=5.0,
                  rates=arms_test._rates(world.tmp))

    def run(arms):
        return bench.run_benchmark(manifest, arms, max_tokens=10 ** 9, hidden_root=hidden,
                                   results_path=world.tmp / "results.json", scratch_root=world.tmp / "scratch",
                                   isolation_launcher=world.launcher)
    return bench, arms_test, common, dump, run


def test_the_real_harness_runs_all_three_arms_and_scoring_through_the_launcher(world, monkeypatch):
    """The contract check: the harness's own arms, plugin export, reused scoring profiles and guard accept it."""
    bench, arms_test, common, dump, run = _harness(world, monkeypatch)
    repo, sha = arms_test._repo(world.tmp)
    arms = [bench.SigmaArm(repo=repo, commit=sha, **common), bench.PlainArm(**common),
            bench.MatchedArm(**common)]

    report = run(arms)

    assert [r["arm"] for r in report["runs"]] == ["sigma", "plain", "matched"]
    for row in report["runs"]:
        assert row["status"] == "completed" and row["cost_usd"] and row["cost_usd"] > 0, row
        assert row["visible_passed"] is True and row["hidden_passed"] is True, row
    records = [json.loads(line) for line in dump.read_text().splitlines()]
    assert len(records) == 3 and sum("--plugin-dir" in r["argv"] for r in records) == 1
    for record in records:
        seen = record["env"]
        assert set(seen) <= ALLOWED_ENV and "GH_TOKEN" not in seen
        assert seen["CLAUDE_CODE_OAUTH_TOKEN"] == os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
        assert os.path.realpath(seen["HOME"]) != os.path.realpath(world.real)


def test_a_tampered_last_run_aborts_the_real_harness_with_paid_rows_kept(world, monkeypatch):
    """The documented gesture: bench.py as its own process. Exit 97 alone is a row note; SIGTERM stops even the last run."""
    armed = world.tmp / "armed"
    tamper = "if os.path.exists(%r):\n    open(%r, 'w').write('tampered')" % (
        str(armed), str(world.real / ".claude" / "settings.json"))
    bench, arms_test, common, dump, run = _harness(world, monkeypatch, extra=tamper)
    world.write_config(parent_marker="bench.py")
    rates = world.tmp / "rates.csv"
    rates.write_text(arms_test.RATES, encoding="utf-8")
    manifest = world.tmp / "tasks" / "manifest.json"
    env = {"PATH": os.environ["PATH"], "HOME": str(world.real), "SIGMA_LAUNCHER_TEST_MODE": "1",
           "CLAUDE_CODE_OAUTH_TOKEN": "o-" + uuid.uuid4().hex}

    def harness(results):
        argv = [sys.executable, str(BENCH), "run", "--manifest", str(manifest), "--hidden-root",
                str(world.hidden / "bundles"), "--results", str(world.tmp / results), "--scratch-root",
                str(world.tmp / ("scratch-" + results)), "--max-tokens", "1000000000", "--isolation-launcher",
                str(world.launcher), "--arm", "plain", "--model", "claude-test", "--permission-mode",
                "acceptEdits", "--claude", str(world.bin / "harness-claude"), "--rates", str(rates)]
        return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120)

    control = harness("control.json")
    assert control.returncode == 0, control.stderr
    armed.write_text("1")
    tripped = harness("tripped.json")

    assert tripped.returncode == 143, (tripped.returncode, tripped.stderr)
    assert "aborted" in json.loads((world.tmp / "tripped.json").read_text())
    latch = world.tmp / "self-scratch" / "real-profile-changed.txt"
    assert ".claude/settings.json" in latch.read_text()


def test_readme_commands_and_figures_are_real():
    assert README.is_file(), "evals/bench/launcher/README.md is not written"
    text = README.read_text(encoding="utf-8")
    bench = _load(BENCH, "bench_for_readme")
    line = next(l for l in text.splitlines() if "python3 evals/bench/bench.py run" in l)
    assert line.startswith('env -i PATH="$PATH" HOME="$HOME" '), "the first run starts the harness under env -i"
    assert "--scratch-root <outside-home>" in line, "the scratch root must be outside the home directory"
    assert not re.search(r'(KEY|TOKEN)=(?!\$)', line), "the first-run command must not carry a literal credential"
    argv = line.split("python3 evals/bench/bench.py run", 1)[1].split()
    fill = {"<token-ceiling>": "1", "<seconds-above-deadline_seconds>": "3600"}
    args = bench.parse_args(["run"] + [fill.get(p, "x") if p.startswith("<") else p for p in argv])
    assert args.isolation_launcher is not None and args.max_tokens is not None and args.arm == "all" and args.batch_pairs == 3
    assert "sigma_bench_launcher.py --dry-run -- claude --version" in text
    evidence = COST_EVIDENCE.read_text(encoding="utf-8")
    for figure in ("$0.628", "$0.415", "$0.502", "$0.271", "$1.044", "$0.773"):
        assert figure in text and figure in evidence, figure
    assert "unmeasured" in text and "keychain" in text.lower()

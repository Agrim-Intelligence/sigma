"""`evals/regression/live.py run` -- the run path of the live entrypoint (#810 slice 3).

NO REAL `claude` AND NO REAL LAUNCHER IS EVER RUN HERE. Every run-path test drives live.py against a FAKE
launcher script and a FAKE `claude` script written into a temp dir (see `make_world`). What these tests
prove: OUR argv / environment / ordering / ledger / row behaviour against a MODEL of the launcher. What they do
NOT prove: that the real launcher, the real `claude` and a real subscription token work together, that a
real exit 97 arrives as modelled, or what a real run costs. The real launcher is unmeasured (its README says
no `claude -p` has run through it).

Every credential is a FAKE built at run time (never a literal). Subprocess tests run the real live.py with an
ALLOWLISTED environment and explicit `--results-dir` / `--ledger-dir` in a temp dir, so nothing lands in
the repository or in the operator's home.

The signal-path tests (`term_parent`) model the launcher's primary stop mechanism (it SIGTERMs its parent).
The exit-97 tests model the FALLBACK (labelled as such): they matter if the parent marker never matched.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import pathlib
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse

import pytest

import git_identity_support

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = ROOT / "evals" / "regression" / "live.py"
LEDGER = ROOT / "evals" / "regression" / "spend_ledger.py"
PHASE_REPORT = ROOT / "skills" / "sigma-loop" / "scripts" / "phase_report.py"
LAUNCHER_SRC_PATH = ROOT / "evals" / "bench" / "launcher" / "sigma_bench_launcher.py"

posix_only = pytest.mark.skipif(os.name != "posix", reason="the live entrypoint is POSIX-only")

LATCH_NAME = "real-profile-changed.txt"

LAUNCHER_SRC = r'''#!__PY__
import glob, json, os, signal, sys, time
real = os.path.realpath(__file__)
root = os.path.dirname(os.path.dirname(real))
mode = json.load(open(os.path.join(root, "mode.json")))
args = sys.argv[1:]
dry = "--dry-run" in args
cmd = args[args.index("--") + 1:] if "--" in args else []


def snapshot():
    opens, settled = {}, set()
    for path in sorted(glob.glob(os.path.join(mode["ledger"], "spend-*.jsonl"))):
        for line in open(path):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("kind") == "open":
                opens[row["id"]] = row.get("belt")
            elif row.get("kind") == "settle":
                settled.add(row["id"])
    last = list(opens.values())[-1] if opens else None
    return len(opens), last, len([i for i in opens if i not in settled])


n, belt, unsettled = snapshot()
rec = {"dry": dry, "pid": os.getpid(), "ppid": os.getppid(), "cwd": os.getcwd(),
       "env_names": sorted(os.environ), "has_token": bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")),
       "open_rows": n, "belt_seen": belt, "unsettled_opens": unsettled, "argv": args}
with open(os.path.join(root, "calls.jsonl"), "a") as fh:
    fh.write(json.dumps(rec) + "\n")
latch = os.path.join(mode["scratch"], "real-profile-changed.txt")
if dry:
    if mode.get("noise"):
        print(mode["noise"], file=sys.stderr)
        print(mode["noise"])
    if mode.get("dry_sleep"):
        time.sleep(mode["dry_sleep"])
    sys.exit(mode.get("dry_exit", 0))
m = mode.get("mode", "ok")
if m == "latch97":
    open(latch, "w").write("tripped")
    sys.exit(97)
if m == "exit97_only":
    sys.exit(97)
if m == "latch_only":
    open(latch, "w").write("tripped")
if m in ("term_parent", "kill_parent"):
    if mode.get("write_latch"):
        open(latch, "w").write("tripped")
    os.kill(os.getppid(), signal.SIGTERM if m == "term_parent" else signal.SIGKILL)
    time.sleep(mode.get("sleep", 30))
    sys.exit(0)
os.execv(cmd[0], cmd)
'''

CLAUDE_SRC = r'''#!__PY__
import json, os, pathlib, subprocess, sys, time
real = os.path.realpath(__file__)
root = os.path.dirname(os.path.dirname(real))
mode = json.load(open(os.path.join(root, "mode.json")))
args = sys.argv[1:]
if "--version" in args:
    print("fake-claude 0.0.1")
    sys.exit(0)
prompt = sys.stdin.read()


def opt(name):
    return args[args.index(name) + 1] if name in args else None


with open(os.path.join(root, "claude_calls.jsonl"), "a") as fh:
    fh.write(json.dumps({"argv": args, "env_names": sorted(os.environ), "prompt_len": len(prompt),
                         "cwd": os.getcwd(), "pid": os.getpid()}) + "\n")
if mode.get("spawn_child"):
    child = subprocess.Popen(["sleep", "300"])
    pathlib.Path(mode["spawn_child"]).write_text(str(child.pid))
if mode.get("claude_sleep"):
    time.sleep(mode["claude_sleep"])
if not mode.get("no_transcript"):
    transcript = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-fake" / (opt("--session-id") + ".jsonl")
    transcript.parent.mkdir(parents=True, exist_ok=True)
    turn = json.dumps({"type": "assistant", "timestamp": "2026-10-01T00:00:00Z",
                       "message": {"id": "m1", "role": "assistant", "model": mode.get("claude_model", "claude-test"),
                                   "usage": {"input_tokens": 0, "output_tokens": mode.get("tokens", 1000),
                                             "cache_read_input_tokens": 0,
                                             "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                                                "ephemeral_1h_input_tokens": 0}}}}) + "\n"
    limit = json.dumps({"type": "assistant", "timestamp": "2026-10-01T00:00:01Z", "error": "rate_limit",
                        "isApiErrorMessage": True, "apiErrorStatus": 429,
                        "quotaLimits": {"status": "rejected", "resetsAt": 1790895600, "rateLimitType": "five_hour"},
                        "message": {"id": "s1", "role": "assistant", "model": "<synthetic>",
                                    "content": [{"type": "text", "text": "limit"}],
                                    "usage": {"input_tokens": 0, "output_tokens": 0}}}) + "\n"
    transcript.write_text(turn + (limit if mode.get("throttle") else ""))
sys.exit(mode.get("claude_exit", 0))
'''

RATES_CSV = ("model,rate_kind,usd_per_mtok,usd_per_request,effective_from,effective_to,source\n" +
             "".join("claude-test,%s,%s,,2026-01-01 00:00:00,,test-local\n" % (kind, price)
                     for kind, price in (("input", "2"), ("output", "10"), ("cache_read", "0.2"),
                                         ("cache_write_5m", "2.5"), ("cache_write_1h", "4"))))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fake(n=16):
    """A fake credential of a shape the shared scrubber does not know, built at run time."""
    return "zq9-" + os.urandom(n).hex()


@pytest.fixture(scope="module")
def live():
    return _load("regression_live_run_under_test", LIVE)


@pytest.fixture(scope="module")
def sl():
    return _load("spend_ledger_for_run_tests", LEDGER)


class World(object):
    """A fake launcher + fake claude + the directories a run needs, all under one temp root."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.launcher_dir = self.root / "launcher"
        self.launcher = self.launcher_dir / "live-launcher.py"
        self.config = self.launcher_dir / "live-launcher.json"
        self.scratch = self.root / "scratch"
        self.bin = self.root / "bin"
        self.claude = self.bin / "claude"
        self.workdir = self.root / "workdir"
        self.prompt = self.root / "prompt.txt"
        self.ledger = self.root / "ledger"
        self.results = self.root / "results"
        self.tmpdir = self.root.parent / (self.root.name + "-tmp")      # the profile dirs go here
        self.home = self.root / "home"
        self.seen_rows = set()
        self.mode_path = self.root / "mode.json"
        self.calls_path = self.root / "calls.jsonl"
        self.claude_calls_path = self.root / "claude_calls.jsonl"
        for d in (self.launcher_dir, self.scratch, self.bin, self.workdir, self.ledger, self.results,
                  self.tmpdir, self.home):
            d.mkdir(parents=True, exist_ok=True)
        self.launcher.write_text(LAUNCHER_SRC.replace("__PY__", sys.executable))
        self.launcher.chmod(0o755)
        self.claude.write_text(CLAUDE_SRC.replace("__PY__", sys.executable))
        self.claude.chmod(0o755)
        self.prompt.write_text("do the thing\n")
        self.write_config()
        self.set_mode()

    def write_config(self, **change):
        cfg = {"parent_marker": "live.py", "credential_var": "CLAUDE_CODE_OAUTH_TOKEN",
               "scratch_root": str(self.scratch), "deadline_seconds": 30, "claude_path": str(self.claude),
               "repo_root": str(self.root / "elsewhere"), "hidden_root": str(self.root / "hidden"),
               "extra_env": []}
        cfg.update(change)
        cfg = {k: v for k, v in cfg.items() if v is not _ABSENT}
        self.config.write_text(json.dumps(cfg))

    def set_mode(self, **mode):
        base = {"ledger": str(self.ledger), "scratch": str(self.scratch)}
        base.update(mode)
        self.mode_path.write_text(json.dumps(base))

    @property
    def latch(self):
        return self.scratch / LATCH_NAME

    def calls(self, dry=None):
        if not self.calls_path.exists():
            return []
        rows = [json.loads(x) for x in self.calls_path.read_text().splitlines() if x]
        return rows if dry is None else [r for r in rows if r["dry"] is dry]

    def claude_calls(self):
        if not self.claude_calls_path.exists():
            return []
        return [json.loads(x) for x in self.claude_calls_path.read_text().splitlines() if x]

    def rows(self):
        return [json.loads(p.read_text()) for p in sorted(self.results.glob("live-*.json"))]

    def new_rows(self):
        """Rows written since the last call (file names carry a random suffix, so name order is not time order)."""
        fresh = [p for p in sorted(self.results.glob("live-*.json")) if p.name not in self.seen_rows]
        self.seen_rows.update(p.name for p in fresh)
        return [json.loads(p.read_text()) for p in fresh]

    def ledger_rows(self):
        out = []
        for p in sorted(self.ledger.glob("spend-*.jsonl")):
            out += [json.loads(x) for x in p.read_text().splitlines() if x]
        return out

    def argv(self, omit=(), **override):
        vals = {"--prompt-file": str(self.prompt), "--workdir": str(self.workdir), "--model": "claude-test",
                "--permission-mode": "acceptEdits", "--launcher": str(self.launcher),
                "--claude": str(self.claude), "--timeout-s": "30"}
        vals.update({"--" + k.replace("_", "-"): v for k, v in override.items()})
        argv = ["run"]
        for flag, value in vals.items():
            if flag not in omit:
                argv += [flag, value]
        return argv + ["--results-dir", str(self.results), "--ledger-dir", str(self.ledger)]

    def env(self, **extra):
        """A valid ungated subscription run environment, as an explicit allowlist."""
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "LANG": "C.UTF-8",
               "TMPDIR": str(self.tmpdir), "CLAUDE_CODE_OAUTH_TOKEN": fake(),
               "SIGMA_REGRESSION_LIVE": "1", "SIGMA_REGRESSION_CAP_USD": "5"}
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}

    def leftovers(self):
        return sorted(p.name for p in self.tmpdir.glob("sigma-regression-live-*"))


_ABSENT = object()


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path / "w")
    monkeypatch.setenv("TMPDIR", str(w.tmpdir))
    monkeypatch.setattr(tempfile, "tempdir", None)               # re-read TMPDIR
    git_identity_support.scoped_git_identity(tmp_path / "git-identity", monkeypatch)   # #1572: tests below override HOME
    return w


class Out(object):
    def __init__(self, rc, out, err, world):
        self.rc, self.out, self.err, self.world = rc, out, err, world
        self.rows = world.new_rows()
        self.row = self.rows[-1] if self.rows else None
        self.lines = [x for x in err.splitlines() if x]


def invoke(live, world, capsys, argv=None, env=None, posix=True):
    """live.main in process: real fake-launcher children, explicit env mapping."""
    capsys.readouterr()
    rc = live.main(world.argv() if argv is None else argv, env=world.env() if env is None else env, posix=posix)
    cap = capsys.readouterr()
    return Out(rc, cap.out, cap.err, world)


def spawn(world, argv=None, env=None, timeout=60):
    """live.py as a real subprocess with an allowlisted environment; returns (CompletedProcess, Out)."""
    proc = subprocess.run([sys.executable, str(LIVE)] + (world.argv() if argv is None else argv),
                          env=world.env() if env is None else env, cwd=str(world.root), capture_output=True,
                          text=True, timeout=timeout)
    return proc, Out(proc.returncode, proc.stdout, proc.stderr, world)


def assert_refused(o, code, world, non_dry=0):
    assert o.rc == 2, o.err
    assert o.out == ""
    assert len(o.lines) == 1 and o.lines[0].startswith("live.py: REFUSED [%s]: " % code), o.lines
    assert len(o.rows) == 1 and o.row["code"] == code and o.row["status"] == "NOT RUN"
    assert len(world.calls(dry=False)) == non_dry
    assert world.claude_calls() == []


# --------------------------------------------------------------------------- Task 2: launcher precondition

@posix_only
@pytest.mark.parametrize("exit_code", [2, 127])
def test_precheck_dry_run_nonzero_is_launcher_precondition(live, world, capsys, exit_code):
    world.set_mode(dry_exit=exit_code)
    o = invoke(live, world, capsys)
    assert_refused(o, "launcher-precondition", world)
    assert o.row["reason"].endswith("launcher --dry-run exited %d" % exit_code)
    assert len(world.calls(dry=True)) == 1


@posix_only
def test_precheck_dry_run_timeout_is_launcher_precondition(live, world, capsys, monkeypatch):
    monkeypatch.setattr(live, "PRECHECK_TIMEOUT_S", 1)
    world.set_mode(dry_sleep=20)
    started = time.monotonic()
    o = invoke(live, world, capsys)
    assert time.monotonic() - started < 15
    assert_refused(o, "launcher-precondition", world)
    assert "timed out" in o.row["reason"]


@posix_only
def test_precheck_timeout_reuses_the_arms_constant(live):
    """PRECHECK_TIMEOUT_S is a wait bound (not a machine-derived limit): unset means the arms constant."""
    assert live.PRECHECK_TIMEOUT_S is None
    assert live.precheck_timeout(live.load_arms()) == live.load_arms().VERSION_TIMEOUT_SECONDS


@posix_only
@pytest.mark.parametrize("how", ["missing", "invalid-json", "list", "empty-file"])
def test_config_missing_or_unreadable_or_not_object(live, world, capsys, how):
    if how == "missing":
        world.config.unlink()
    elif how == "invalid-json":
        world.config.write_text("{not json")
    elif how == "list":
        world.config.write_text("[1, 2]")
    else:
        world.config.write_text("")
    o = invoke(live, world, capsys)
    assert_refused(o, "launcher-precondition", world)
    assert world.calls() == []                                    # the config is read before any spawn


@posix_only
@pytest.mark.parametrize("marker", ["bench.py", None, "Live.py", "live.py ", 7, _ABSENT])
def test_config_parent_marker_must_be_live_py(live, world, capsys, marker):
    world.write_config(parent_marker=marker)
    o = invoke(live, world, capsys)
    assert_refused(o, "launcher-precondition", world)
    assert world.calls() == []


@posix_only
@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", None, 5, _ABSENT])
def test_config_credential_var_must_be_oauth(live, world, capsys, var):
    world.write_config(credential_var=var)
    o = invoke(live, world, capsys)
    assert_refused(o, "launcher-precondition", world)
    assert world.calls() == []


@posix_only
@pytest.mark.parametrize("scratch", ["relative/scratch", None, 5, "", _ABSENT])
def test_config_scratch_root_must_be_absolute(live, world, capsys, scratch):
    world.write_config(scratch_root=scratch)
    o = invoke(live, world, capsys)
    assert_refused(o, "launcher-precondition", world)
    assert world.calls() == []


@posix_only
def test_config_extra_keys_are_tolerated(live, world, capsys):
    """The real launcher is the strict reader of its own config; live.py tolerates keys it does not know."""
    world.write_config(some_future_key=[1, 2], extra_env=["FOO"])
    o = invoke(live, world, capsys)
    assert len(world.calls(dry=True)) == 1 and o.row["code"] != "launcher-precondition"


@posix_only
def test_latch_present_before_run_is_launcher_latch_and_never_deleted(live, world, capsys):
    world.latch.write_text("an earlier trip")
    o = invoke(live, world, capsys)
    assert_refused(o, "launcher-latch", world)
    assert world.latch.exists() and world.latch.read_text() == "an earlier trip"
    assert world.calls() == []                                    # the latch check precedes the dry-run spawn


@posix_only
@pytest.mark.parametrize("case", ["relative", "directory", "not-executable", "missing"])
def test_launcher_must_be_absolute_executable_file(live, world, capsys, case):
    if case == "relative":
        launcher = "live-launcher.py"
    elif case == "directory":
        launcher = str(world.launcher_dir)
    elif case == "not-executable":
        world.launcher.chmod(0o644)
        launcher = str(world.launcher)
    else:
        launcher = str(world.root / "nope")
    o = invoke(live, world, capsys, world.argv(launcher=launcher))
    assert_refused(o, "run-refused", world)
    assert o.row["reason"].endswith(": launcher-path")
    assert world.calls() == []


@posix_only
@pytest.mark.parametrize("case", ["relative", "missing"])
def test_claude_must_be_absolute(live, world, capsys, case):
    claude = "claude" if case == "relative" else str(world.root / "nope-claude")
    o = invoke(live, world, capsys, world.argv(claude=claude))
    assert_refused(o, "run-refused", world)
    assert o.row["reason"].endswith(": claude-path")


@posix_only
def test_claude_must_be_the_configured_claude(live, world, capsys):
    """R5: the program that gets the credential is the one the launcher config names, by realpath."""
    other = world.root / "other-claude"
    other.write_text(CLAUDE_SRC.replace("__PY__", sys.executable))
    other.chmod(0o755)
    o = invoke(live, world, capsys, world.argv(claude=str(other)))
    assert_refused(o, "launcher-precondition", world)
    assert world.calls() == []
    link = world.root / "claude-link"
    link.symlink_to(world.claude)
    o = invoke(live, world, capsys, world.argv(claude=str(link)))
    assert len(world.calls(dry=True)) == 1 and o.row["code"] != "launcher-precondition"    # a link to it is fine


@posix_only
def test_config_is_read_from_the_resolved_launcher_not_a_symlinks_directory(live, world, capsys):
    """R4: the real launcher derives its config from `Path(__file__).resolve()`, so live.py must too."""
    linkdir = world.root / "links"
    linkdir.mkdir()
    (linkdir / "live-launcher.py").symlink_to(world.launcher)
    (linkdir / "live-launcher.json").write_text(json.dumps({"parent_marker": "bench.py"}))     # a decoy
    o = invoke(live, world, capsys, world.argv(launcher=str(linkdir / "live-launcher.py")))
    assert len(world.calls(dry=True)) == 1, o.err
    assert o.row["code"] != "launcher-precondition"


@posix_only
@pytest.mark.parametrize("where", ["inside-tmp", "contains-tmp"])
def test_scratch_root_overlapping_the_system_temp_dir_is_refused(live, world, capsys, where):
    """R6: the profile dirs live under the temp dir; a scratch_root there (or above it) lets an agent reach the latch."""
    scratch = world.tmpdir / "scratch" if where == "inside-tmp" else world.tmpdir.parent
    world.write_config(scratch_root=str(scratch))
    o = invoke(live, world, capsys)
    assert_refused(o, "launcher-precondition", world)
    assert world.calls() == []


@posix_only
def test_precheck_env_carries_no_credential_and_nothing_else(live, world, capsys):
    hostile = world.env(GITHUB_TOKEN=fake(), AWS_SECRET_ACCESS_KEY=fake(), HOSTILE_VAR="x",
                        SIGMA_BENCH_X="y", ANTHROPIC_API_KEY=None)
    world.set_mode(dry_exit=2)
    invoke(live, world, capsys, env=hostile)
    dry = world.calls(dry=True)
    assert len(dry) == 1 and dry[0]["has_token"] is False
    assert set(dry[0]["env_names"]) <= set(live.MINIMAL_ENV_NAMES) | {"LC_CTYPE", "__CF_USER_TEXT_ENCODING",
                                                                       "PWD", "SHLVL", "_", "OLDPWD"}
    for banned in ("GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "HOSTILE_VAR", "SIGMA_BENCH_X",
                   "SIGMA_REGRESSION_LIVE", "SIGMA_REGRESSION_CAP_USD", "CLAUDE_CODE_OAUTH_TOKEN", "HOME",
                   "TMPDIR"):
        assert banned not in dry[0]["env_names"], banned


@posix_only
def test_launcher_refusal_text_is_never_echoed(live, world, capsys):
    marker = "PLANTED-LAUNCHER-TEXT-" + os.urandom(6).hex()
    world.set_mode(dry_exit=2, noise=marker)
    o = invoke(live, world, capsys)
    assert_refused(o, "launcher-precondition", world)
    blob = o.out + o.err + json.dumps(o.rows)
    assert marker not in blob


@posix_only
def test_arms_unavailable_is_launcher_precondition(live, world, capsys, monkeypatch):
    monkeypatch.setattr(live, "ARMS_PATH_OVERRIDE", world.root / "no-such-common.py")
    monkeypatch.setattr(live, "_ARMS", None)
    o = invoke(live, world, capsys)
    assert_refused(o, "launcher-precondition", world)
    assert "helpers could not be loaded" in o.row["reason"]


# --------------------------------------------------------------------------- Task 3: reserve, run, settle, row

from decimal import Decimal  # noqa: E402
import datetime  # noqa: E402


def month_file(world):
    return world.ledger / ("spend-%s.jsonl" % datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m"))


def priced(live_mod, world):
    """A rate card that prices the fake's model, as phase_report rows."""
    pr = _load("phase_report_for_run_tests", PHASE_REPORT)
    path = world.root / "rates.csv"
    path.write_text(RATES_CSV)
    return pr.load_rate_rows(path)


def seed_open(sl, world, belt="3.00", cap="5"):
    """An unsettled open row from a 'previous killed run', written by the real ledger."""
    res = sl.Ledger(world.ledger, Decimal(cap)).reserve(belt)
    assert isinstance(res, sl.Reservation)
    return res


@posix_only
def test_open_row_is_durable_before_the_launcher_starts(live, world, capsys):
    """AC-3, behavioural: the FAKE LAUNCHER snapshots the ledger at its own start."""
    o = invoke(live, world, capsys)
    runs = world.calls(dry=False)
    assert len(runs) == 1
    assert runs[0]["open_rows"] == 1                                   # seen by the launcher, not by live.py
    assert Decimal(runs[0]["belt_seen"]) == Decimal(5)
    assert o.row["ledger_id"] and o.row["ledger_id"] == [r for r in world.ledger_rows() if r["kind"] == "open"][0]["id"]


@posix_only
def test_open_row_is_still_unsettled_while_the_launcher_runs(live, world, capsys):
    """Distinct from durability: a row settled before the launch would vanish from the count, and a SIGKILL
    could then never leave the belt counted."""
    invoke(live, world, capsys)
    assert world.calls(dry=False)[0]["unsettled_opens"] == 1


def test_spent_for_rounds_up_beyond_the_ledgers_nine_digits(live):
    class A(object):
        cost = 0.0000000001
    assert live.spent_for(A, Decimal(5)) == Decimal("0.000000001")           # up, never down to a free run
    class U(object):
        cost = None
    assert live.spent_for(U, Decimal("2.5")) == Decimal("2.5")               # unmeasured: the FULL belt


@posix_only
def test_belt_is_clamped_by_the_ledger_and_passed_as_max_budget_usd(live, world, capsys, sl):
    seed_open(sl, world, "3.00")
    o = invoke(live, world, capsys, world.argv() + ["--belt-usd", "4"])
    assert Decimal(world.calls(dry=False)[0]["belt_seen"]) == Decimal(2)       # min(4, cap 5 - counted 3)
    argv = world.claude_calls()[0]["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == "2.000000"
    assert o.row["belt"] == 2.0


@posix_only
def test_belt_defaults_to_the_cap(live, world, capsys):
    o = invoke(live, world, capsys)
    argv = world.claude_calls()[0]["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == "5.000000" and o.row["belt"] == 5.0


@posix_only
def test_belt_is_quantized_down_before_it_reaches_claude(live, world, capsys, sl):
    """%.6f of a float could round UP above what the ledger reserved; the value passed is rounded DOWN."""
    seed_open(sl, world, "4.9999999")                                   # leaves 0.0000001 of the cap
    o = invoke(live, world, capsys)
    # 0.0000001 < one microdollar: it rounds DOWN to zero, which claude is never given
    assert o.row["code"] == "run-refused" and o.row["reason"].endswith(": start")
    assert world.calls(dry=False) == [] and world.claude_calls() == []
    assert [r["kind"] for r in world.ledger_rows()][-2:] == ["open", "settle"]   # the reserve happened, then settled


@posix_only
def test_budget_exhausted_is_spend_ledger_not_run(live, world, capsys, sl):
    seed_open(sl, world, "5")
    before = world.ledger_rows()
    o = invoke(live, world, capsys)
    assert_refused(o, "spend-ledger", world)
    assert o.row["reason"].endswith("ledger: budget")
    assert world.ledger_rows() == before                                # no open row added


@posix_only
def test_corrupt_ledger_is_spend_ledger_not_run(live, world, capsys):
    month_file(world).write_text("this is not json\n")
    o = invoke(live, world, capsys)
    assert_refused(o, "spend-ledger", world)
    assert o.row["reason"].endswith("ledger: ledger-corrupt")


@posix_only
def test_lock_timeout_is_spend_ledger_not_run(live, world, capsys, monkeypatch):
    import fcntl
    monkeypatch.setattr(live, "LEDGER_LOCK_TIMEOUT_S", 0.3)
    fd = os.open(str(world.ledger / "spend.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        o = invoke(live, world, capsys)
    finally:
        os.close(fd)
    assert_refused(o, "spend-ledger", world)
    assert o.row["reason"].endswith("ledger: lock-timeout")


@posix_only
def test_a_ledger_refusal_is_spend_ledger_never_a_traceback(live, world, capsys):
    o = invoke(live, world, capsys, env=world.env(SIGMA_REGRESSION_CAP_USD="9" * 16))
    assert_refused(o, "spend-ledger", world)
    assert o.row["reason"].endswith("ledger: bad-amount")
    assert "Traceback" not in o.err


@posix_only
def test_clean_run_settles_at_measured_spend_and_writes_a_RAN_row(live, world, capsys, monkeypatch):
    monkeypatch.setattr(live, "RATES", priced(live, world))
    world.set_mode(tokens=1000, claude_model="claude-test")
    o = invoke(live, world, capsys, world.argv(model="claude-test"))
    assert o.rc == 0, o.err
    row = o.row
    assert row["status"] == "RAN" and row["code"] == "ran"
    assert row["tokens"] == 1000
    ledger_rows = world.ledger_rows()
    assert [r["kind"] for r in ledger_rows] == ["open", "settle"]
    settle = ledger_rows[1]
    assert "reason" not in settle                                      # an explicit, measured settle
    assert Decimal(settle["spent"]) == Decimal("0.01") == Decimal(row["spent"])
    assert len(settle["spent"].split(".")[1]) <= 9
    assert row["cost"] == 0.01 and row["cost_basis"] == "indicative"
    assert row["representative"] is False and row["credential_basis"] == "subscription"
    assert row["model"] == "claude-test" and row["permission_mode"] == "acceptEdits"
    assert row["ledger_id"] == ledger_rows[0]["id"]
    assert row["exit_code"] == 0 and row["timed_out"] is False
    assert json.loads(o.out) == row                                     # stdout carries the (redacted) row
    assert o.err == ""


@posix_only
def test_settled_amount_rounds_up(live, world, capsys, monkeypatch):
    monkeypatch.setattr(live, "RATES", priced(live, world))
    world.set_mode(tokens=1, claude_model="claude-test")                # 1 token at $10/Mtok = 0.00001
    o = invoke(live, world, capsys)
    assert Decimal(o.row["spent"]) >= Decimal("0.00001") and Decimal(o.row["spent"]) < Decimal("0.0000101")


@posix_only
@pytest.mark.parametrize("variant", ["unpriced-model", "no-transcript"])
def test_unpriced_or_unmeasured_run_settles_at_full_belt(live, world, capsys, variant):
    world.set_mode(**({"no_transcript": True} if variant == "no-transcript" else {"claude_model": "claude-test"}))
    proc, o = spawn(world)                                              # the SHIPPED card does not price claude-test
    assert proc.returncode == 1, proc.stderr
    row = o.row
    assert row["status"] == "RUN-FAILED" and row["code"] == "unmeasured" and row["reason"] == "unmeasured"
    assert row["cost"] is None
    settle = [r for r in world.ledger_rows() if r["kind"] == "settle"][0]
    assert Decimal(settle["spent"]) == Decimal(5) and Decimal(row["spent"]) == Decimal(5)    # fail closed
    assert "RUN-FAILED [unmeasured]" in proc.stderr
    assert json.loads(proc.stdout) == row


FAILURES = [("exit-nonzero", {"claude_exit": 3}, "30"), ("timed-out", {"claude_sleep": 30}, "1"),
            ("throttled", {"throttle": True, "claude_exit": 1}, "30")]


@posix_only
@pytest.mark.parametrize("token,mode,timeout", FAILURES, ids=[f[0] for f in FAILURES])
def test_nonzero_exit_timeout_and_throttle_are_RUN_FAILED(live, world, token, mode, timeout):
    child = world.root / "child.pid"
    world.set_mode(spawn_child=str(child), **mode)
    proc, o = spawn(world, world.argv(timeout_s=timeout))
    assert proc.returncode == 1, proc.stderr
    assert o.row["status"] == "RUN-FAILED" and o.row["code"] == token and o.row["reason"] == token
    assert o.row["timed_out"] is (token == "timed-out")
    assert [r["kind"] for r in world.ledger_rows()] == ["open", "settle"]
    pid = int(child.read_text())
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)                                                  # the whole group was killed


@posix_only
def test_settle_failure_leaves_the_open_and_still_reports(live, world, capsys, monkeypatch):
    led_mod = live.load_ledger()
    original = led_mod.Ledger.settle
    calls = []

    def flaky(self, reservation, spent, reason=None, now=None):
        calls.append(reason)
        if len(calls) == 1:
            raise led_mod.LedgerRefusal("write-failed")
        return original(self, reservation, spent, reason, now)

    monkeypatch.setattr(led_mod.Ledger, "settle", flaky)
    monkeypatch.setattr(live, "RATES", priced(live, world))
    o = invoke(live, world, capsys)
    assert o.rc == 1
    assert o.row["spent"] is None and "settle-failed" in o.row["reason"] and o.row["status"] == "RUN-FAILED"
    ledger_rows = world.ledger_rows()
    settle = [r for r in ledger_rows if r["kind"] == "settle"]
    assert len(settle) == 1 and settle[0]["reason"] == "exit" and Decimal(settle[0]["spent"]) == Decimal(5)   # full belt
    assert calls == [None, "exit"]


@posix_only
def test_row_write_failure_does_not_change_spend_or_hide_it(live, world, capsys):
    blocker = world.root / "blocker"
    blocker.write_text("x")
    argv = world.argv()
    argv[argv.index("--results-dir") + 1] = str(blocker)
    world.set_mode(claude_model="claude-test")
    proc, o = spawn(world, argv)
    assert "row not recorded" in proc.stderr
    assert proc.returncode == 1
    assert [r["kind"] for r in world.ledger_rows()] == ["open", "settle"]
    assert proc.stdout.strip() and json.loads(proc.stdout)["ledger_id"]    # the run is still reported on stdout


# --------------------------------------------------------------------------- Task 4: latch, exit 97, signals, SIGKILL

def one_row(o):
    assert len(o.rows) == 1, o.rows
    return o.row


@posix_only
def test_exit_97_is_launcher_latch_not_run(live, world, capsys):
    """FALLBACK path: the real launcher stops a run by SIGTERMing its parent; exit 97 matters if the marker missed."""
    world.set_mode(mode="latch97")
    o = invoke(live, world, capsys)
    assert o.rc == 2 and o.out == ""
    row = one_row(o)
    assert row["code"] == "launcher-latch" and row["status"] == "NOT RUN"
    assert o.lines[0].startswith("live.py: REFUSED [launcher-latch]: ")
    assert row["exit_code"] == 97 and row["ledger_id"]
    kinds = world.ledger_rows()
    assert [r["kind"] for r in kinds] == ["open", "settle"]                  # settled, not left open
    assert Decimal(kinds[1]["spent"]) == Decimal(5)                          # no transcript: unmeasured -> full belt
    assert row["spent"] is not None and row["cost"] == 5.0 and row["cost_basis"] == "settled-at-full-belt"   # R8
    assert world.latch.exists()                                              # untouched


@posix_only
def test_exit_97_alone_is_a_trip_without_the_latch_file(live, world, capsys):
    """Clause 1 on its own (FALLBACK path): exit 97 with NO latch file is still a trip."""
    world.set_mode(mode="exit97_only")
    o = invoke(live, world, capsys)
    row = one_row(o)
    assert o.rc == 2 and row["code"] == "launcher-latch" and row["exit_code"] == 97 and row["status"] == "NOT RUN"
    assert not world.latch.exists()
    assert [r["kind"] for r in world.ledger_rows()] == ["open", "settle"]


@posix_only
def test_a_tripped_run_with_a_measured_cost_settles_at_it(live, world, capsys, monkeypatch):
    monkeypatch.setattr(live, "RATES", priced(live, world))
    world.set_mode(mode="latch_only")
    o = invoke(live, world, capsys)
    row = one_row(o)
    assert row["code"] == "launcher-latch" and row["exit_code"] == 0         # a clean exit, but the file appeared
    assert Decimal(row["spent"]) == Decimal("0.01") and row["cost"] == 0.01 and row["cost_basis"] == "indicative"


@posix_only
def test_latch_file_appearing_with_exit_0_is_still_a_trip(live, world, capsys):
    """Clause 2: the FILE check is independent of the exit code."""
    world.set_mode(mode="latch_only")
    o = invoke(live, world, capsys)
    assert o.rc == 2 and one_row(o)["code"] == "launcher-latch" and one_row(o)["exit_code"] == 0
    assert world.latch.exists()


@posix_only
def test_latch_present_after_trip_blocks_the_next_run_before_any_spawn(live, world, capsys):
    world.set_mode(mode="latch97")
    invoke(live, world, capsys)
    runs, dry = len(world.calls(dry=False)), len(world.calls(dry=True))
    world.set_mode()
    o = invoke(live, world, capsys)
    assert o.rc == 2 and one_row(o)["code"] == "launcher-latch"
    assert len(world.calls(dry=False)) == runs and len(world.calls(dry=True)) == dry     # no spawn at all
    assert len([r for r in world.ledger_rows() if r["kind"] == "open"]) == 1             # nothing reserved


@posix_only
def test_live_py_never_deletes_the_latch(live, world, capsys):
    world.set_mode(mode="latch97")
    invoke(live, world, capsys)
    assert world.latch.exists()
    world.set_mode()
    invoke(live, world, capsys)
    assert world.latch.exists() and world.latch.read_text() == "tripped"


def _child_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@posix_only
@pytest.mark.parametrize("with_latch,code", [(False, "run-signalled"), (True, "launcher-latch")])
def test_sigterm_from_launcher_stops_the_run_records_not_run_and_settles_full_belt(live, world, with_latch, code):
    """PRIMARY stop path: the real launcher SIGTERMs its parent; here a fake does exactly that."""
    world.set_mode(mode="term_parent", write_latch=with_latch, sleep=30)
    proc, o = spawn(world)
    assert proc.returncode == 143, proc.stderr
    row = one_row(o)
    assert row["status"] == "NOT RUN" and row["code"] == code and row["ledger_id"]
    assert proc.stdout == ""
    assert ("REFUSED [%s]" % code) in proc.stderr
    kinds = world.ledger_rows()
    assert [r["kind"] for r in kinds] == ["open", "settle"]
    assert kinds[1]["reason"] == "signal" and Decimal(kinds[1]["spent"]) == Decimal(5)
    assert row["spent"] is not None and Decimal(row["spent"]) == Decimal(5)
    pid = world.calls(dry=False)[0]["pid"]
    time.sleep(0.3)
    assert not _child_alive(pid)                                              # the launcher's group is dead
    if with_latch:
        assert world.latch.exists()


@posix_only
def test_sigkill_of_live_py_leaves_the_open_and_the_next_run_counts_it(live, world, sl):
    """AC-3's kill half, integration: SIGKILL cannot be caught, so the open row stays at its full belt."""
    world.set_mode(mode="kill_parent", sleep=30)
    proc, o = spawn(world)
    try:
        assert proc.returncode == -signal.SIGKILL
        kinds = world.ledger_rows()
        assert [r["kind"] for r in kinds] == ["open"]                         # no settle: nobody could write one
        run_id = kinds[0]["id"]
        month = month_file(world).name[len("spend-"):-len(".jsonl")]
        world.set_mode()
        proc2, o2 = spawn(world)
        assert proc2.returncode == 2 and one_row(o2)["code"] == "spend-ledger"      # cap 5 == the orphan's belt
        assert one_row(o2)["reason"].endswith("ledger: budget")
        sl.settle_orphan(world.ledger, month, run_id, "0")                    # the documented lever
        proc3, o3 = spawn(world)
        assert proc3.returncode in (0, 1), proc3.stderr
        assert one_row(o3)["status"] in ("RAN", "RUN-FAILED") and one_row(o3)["ledger_id"] != run_id
    finally:
        for call in world.calls(dry=False):
            try:
                os.killpg(call["pid"], signal.SIGKILL)                        # the orphaned fake launcher
            except (ProcessLookupError, PermissionError):
                pass


@posix_only
def test_invocation_under_another_name_warns(tmp_path):
    """The launcher's parent marker is the NAME live.py; a renamed copy may never be signalled by it."""
    import shutil
    tree = tmp_path / "tree"
    (tree / "evals" / "regression").mkdir(parents=True)
    (tree / "skills" / "sigma-loop" / "scripts").mkdir(parents=True)
    shutil.copy(str(ROOT / "skills" / "sigma-loop" / "scripts" / "scrub.py"),
                str(tree / "skills" / "sigma-loop" / "scripts" / "scrub.py"))
    w = World(tmp_path / "w")
    results = {}
    for name in ("renamed.py", "live.py"):
        shutil.copy(str(LIVE), str(tree / "evals" / "regression" / name))
        proc = subprocess.run([sys.executable, str(tree / "evals" / "regression" / name)] + w.argv(),
                              env=w.env(), cwd=str(w.root), capture_output=True, text=True, timeout=60)
        results[name] = proc.stderr
    assert "invoked as a name other than live.py" in results["renamed.py"]
    assert "invoked as a name other than live.py" not in results["live.py"]


# --------------------------------------------------------------------------- Task 5: credential discipline

PY_EXTRA_ENV = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING", "PWD", "SHLVL", "_", "OLDPWD"}   # added by the interpreter/OS


def all_forms(live, value):
    forms = set(live.redaction_set([value])) | {value}
    return sorted(forms, key=len, reverse=True)


def tree_blobs(*roots):
    for root in roots:
        for path in pathlib.Path(root).rglob("*"):
            if path.is_file() and not path.is_symlink():
                try:
                    yield str(path), path.read_bytes().decode("utf-8", "replace")
                except OSError:
                    continue


@posix_only
def test_launcher_receives_only_the_named_credential_and_minimal_env(live, world, capsys):
    token = fake()
    env = world.env(CLAUDE_CODE_OAUTH_TOKEN=token, GITHUB_TOKEN=fake(), AWS_SECRET_ACCESS_KEY=fake(),
                    HOSTILE_VAR="x", SIGMA_BENCH_X="y", CI="false")
    invoke(live, world, capsys, env=env)
    run = world.calls(dry=False)[0]
    allowed = {"PATH", "LANG", "CLAUDE_CODE_OAUTH_TOKEN", "HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TMPDIR"}
    assert "CLAUDE_CODE_OAUTH_TOKEN" in run["env_names"] and run["has_token"] is True
    assert set(run["env_names"]) <= allowed | PY_EXTRA_ENV, sorted(set(run["env_names"]) - allowed)
    for banned in ("GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "HOSTILE_VAR", "SIGMA_BENCH_X",
                   "SIGMA_REGRESSION_LIVE", "SIGMA_REGRESSION_CAP_USD", "CI", "ANTHROPIC_API_KEY"):
        assert banned not in run["env_names"], banned
    assert set(world.claude_calls()[0]["env_names"]) == set(run["env_names"])
    assert all(c["has_token"] is False for c in world.calls(dry=True))


@posix_only
def test_planted_credential_value_appears_in_no_file_log_row_or_ledger(live, world, capsys, monkeypatch):
    token = fake(24)
    env = world.env(CLAUDE_CODE_OAUTH_TOKEN=token)
    monkeypatch.setattr(live, "RATES", priced(live, world))
    blobs = []
    for mode, run in (({}, "ran"), ({"mode": "latch97"}, "trip"), ({"mode": "term_parent", "sleep": 30}, "signal")):
        if world.latch.exists():
            world.latch.unlink()
        world.set_mode(**mode)
        if run == "signal":
            proc, o = spawn(world, env=env)
            blobs += [proc.stdout, proc.stderr]
        else:
            o = invoke(live, world, capsys, env=env)
            blobs += [o.out, o.err]
    if world.latch.exists():
        world.latch.unlink()
    world.set_mode()
    o = invoke(live, world, capsys, world.argv(model="bad model!"), env=env)           # an input-stage refusal
    blobs += [o.out, o.err]
    forms = all_forms(live, token)
    assert len(forms) >= 3
    for form in forms:
        for blob in blobs:
            assert form not in blob, form
        for where, text in tree_blobs(world.root, world.tmpdir):
            assert form not in text, (form, where)
    # the scan is not vacuous: rows and ledger exist and were scanned
    assert len(list(world.results.glob("live-*.json"))) >= 4 and world.ledger_rows()


@posix_only
def test_credential_valued_operator_text_is_redacted_not_echoed(live, world, capsys):
    """A model name that IS the credential passes the charset check, so only exact-value redaction stops it."""
    token = fake(12)
    o = invoke(live, world, capsys, world.argv(model=token), env=world.env(CLAUDE_CODE_OAUTH_TOKEN=token))
    blob = o.out + o.err + json.dumps(world.rows())
    assert token not in blob and "[REDACTED]" in blob


@posix_only
def test_run_row_never_carries_operator_text_beyond_validated_fields(live, world, capsys):
    token = fake(12)
    bad = "zq9 " + token                                                                # outside the charset
    o = invoke(live, world, capsys, world.argv(model=bad), env=world.env(CLAUDE_CODE_OAUTH_TOKEN=token))
    assert_refused(o, "run-refused", world)
    assert o.row["reason"].endswith(": model")
    assert token not in o.err and token not in json.dumps(o.rows) and "zq9" not in o.err


@posix_only
@pytest.mark.parametrize("scenario", ["ran", "trip", "signal"])
def test_profile_dir_is_removed_and_inside_tmp(live, world, capsys, scenario):
    world.set_mode(**{"ran": {}, "trip": {"mode": "latch97"}, "signal": {"mode": "term_parent", "sleep": 30}}[scenario])
    if scenario == "signal":
        spawn(world)
    else:
        invoke(live, world, capsys)
    assert world.leftovers() == []
    assert world.calls(dry=False)                                     # a profile was made and used


# --------------------------------------------------------------------------- plan-review refinements R1, R3, R7, R9

@posix_only
def test_default_ledger_is_per_user_not_per_checkout(tmp_path, monkeypatch, world):
    """R1: two tree copies resolve the SAME default, under the user's home, in neither tree."""
    import shutil
    monkeypatch.setenv("HOME", str(world.home))
    mods, trees = [], []
    for n in (1, 2):
        tree = tmp_path / ("tree%d" % n) / "evals" / "regression"
        tree.mkdir(parents=True)
        shutil.copy(str(LIVE), str(tree / "live.py"))
        mods.append(_load("live_tree_copy_%d" % n, tree / "live.py"))
        trees.append(tmp_path / ("tree%d" % n))
    first, second = mods[0].default_ledger_dir(), mods[1].default_ledger_dir()
    assert first == second == world.home / ".sigma" / "eval" / "ledger"
    assert not any(str(first).startswith(str(t)) for t in trees)


@posix_only
def test_run_without_ledger_dir_uses_the_per_user_default(live, world, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(world.home))
    argv = world.argv()
    i = argv.index("--ledger-dir")
    del argv[i:i + 2]
    invoke(live, world, capsys, argv)
    assert list((world.home / ".sigma" / "eval" / "ledger").glob("spend-*.jsonl"))
    assert not list(world.ledger.glob("spend-*.jsonl"))


@posix_only
def test_an_unexpected_exception_becomes_a_run_error_row(live, world, capsys, monkeypatch):
    """R3: the catch-all names only the exception TYPE, never its message, and the belt is settled."""
    common = live.load_arms()
    planted = "PLANTED-EXC-MESSAGE-" + os.urandom(6).hex()

    def boom(*a, **k):
        raise RuntimeError(planted)

    monkeypatch.setattr(common, "session_usage", boom)
    o = invoke(live, world, capsys)
    assert o.rc == 1
    row = one_row(o)
    assert row["status"] == "RUN-FAILED" and row["code"] == "run-error" and row["reason"].endswith(": RuntimeError")
    assert planted not in o.out + o.err + json.dumps(o.rows)
    kinds = world.ledger_rows()
    assert [r["kind"] for r in kinds] == ["open", "settle"] and Decimal(kinds[1]["spent"]) == Decimal(5)
    assert row["ledger_id"] == kinds[0]["id"] and Decimal(row["spent"]) == Decimal(5)
    assert world.leftovers() == []


@posix_only
def test_a_pre_run_exception_is_a_not_run_error_row(live, world, capsys, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk exploded " + os.urandom(4).hex())

    monkeypatch.setattr(live, "validate_run_options", boom)
    o = invoke(live, world, capsys)
    assert o.rc == 2 and one_row(o)["code"] == "run-error" and one_row(o)["status"] == "NOT RUN"
    assert one_row(o)["reason"].endswith(": OSError") and "exploded" not in o.err


@posix_only
def test_keyboard_interrupt_writes_the_row_then_reraises(live, world, capsys, monkeypatch):
    common = live.load_arms()

    def stop(*a, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(common, "session_usage", stop)
    capsys.readouterr()
    with pytest.raises(KeyboardInterrupt):
        live.main(world.argv(), env=world.env())
    rows = world.new_rows()
    assert len(rows) == 1 and rows[0]["code"] == "run-error" and rows[0]["reason"].endswith(": KeyboardInterrupt")
    assert [r["kind"] for r in world.ledger_rows()] == ["open", "settle"]


@posix_only
def test_commit_and_cell_are_validated_and_recorded(live, world, capsys):
    good = "0123456789abcdef0123456789ABCDEF01234567"
    o = invoke(live, world, capsys, world.argv() + ["--commit", good, "--cell", "live/linux:py3.12"],
               env=world.env(GITHUB_SHA="f" * 40))
    assert one_row(o)["commit"] == good and one_row(o)["cell"] == "live/linux:py3.12"
    o = invoke(live, world, capsys, env=world.env(GITHUB_SHA="e" * 40))                  # GITHUB_SHA only as fallback
    assert one_row(o)["commit"] == "e" * 40
    for flag, bad, stage in (("--commit", good[:39], "commit"), ("--commit", "g" * 40, "commit"),
                             ("--cell", "has space", "cell"), ("--cell", "x" * 81, "cell")):
        o = invoke(live, world, capsys, world.argv() + [flag, bad])
        assert one_row(o)["code"] == "run-refused" and one_row(o)["reason"].endswith(": " + stage), (flag, bad)
    o = invoke(live, world, capsys, world.argv(model="claude-test") + ["--commit", good])
    assert one_row(o)["commit"] == good                                                   # recorded on RAN/RUN-FAILED rows


@posix_only
def test_schema_docstring_states_the_status_rule():
    """R9: RAN is never GREEN, and the rule is written where a reader of the schema will see it."""
    text = " ".join(LIVE.read_text(encoding="utf-8").split())
    assert "RAN is never GREEN" in text and "any status that is not GREEN is not a pass" in text
    assert "#819 and #820 must handle the two new values" in text


@posix_only
def test_exit_97_and_latch_name_match_the_launcher_source():
    """live.py cannot import the launcher (AC-5), so its mirrored constants are pinned against the source."""
    src = LAUNCHER_SRC_PATH.read_text(encoding="utf-8")
    assert "CHANGED, REFUSED, DEADLINE = 97, 2, 124" in src
    assert 'LATCH, ALERTS = "real-profile-changed.txt"' in src
    live_mod = _load("live_for_launcher_pin", LIVE)
    assert live_mod.LATCH_EXIT == 97 and live_mod.LAUNCHER_LATCH_NAME == "real-profile-changed.txt"
    assert live_mod.LAUNCHER_CREDENTIAL_VAR == "CLAUDE_CODE_OAUTH_TOKEN"
    assert 'SUBSCRIPTION_VAR = "CLAUDE_CODE_OAUTH_TOKEN"' in src


@posix_only
def test_config_claude_path_must_be_absolute(live, world, capsys):
    world.write_config(claude_path="claude")
    o = invoke(live, world, capsys)
    assert_refused(o, "launcher-precondition", world)

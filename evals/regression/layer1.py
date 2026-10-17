"""Layer 1 scenario builder for the regression eval: two sessions, init then loop (#866, slice 2 of #812).

Builds, in a scratch root that it alone owns, everything one Layer 1 run needs, and drives an ARM through
two sessions. It starts no model, makes no network call and spends nothing: the arm is any callable
supplied by the caller, and the tests use a fake one. Stdlib plus the bench arm helpers loaded BY PATH
(`evals/bench/arms/common.py` and `sigma.py`, never edited, the launcher never imported).

Run directory (matches `record.build_record` defaults):

    <root>/repo/  <root>/remote.git/  <root>/gh_state.json  <root>/bin/gh
    <root>/<init|loop>/profile/{home,claude-config,codex-home,tmp}   fresh per session, made just before its arm call
    <root>/<init|loop>/export/                                       its own plugin export, sibling of the profile

Arm contract (FD-1): `arm(session_spec) -> result`. `SessionSpec` is all an arm receives. A result is a
mapping or an Attempt-like object with optional keys cost, session_id, returncode, timed_out, detail.
Exceptions, KeyboardInterrupt included, propagate AFTER the rows are copied and the root is removed. Layer 1
never reads or forwards a credential: the arm gets its own from its caller. `evals/regression/live.py` is NOT
edited and does not call this module yet: wiring a `layer1` cell to this contract is a later slice (the
acceptance wording "through the live entrypoint's arm interface" is therefore read as "through the arm
interface that slice wires live.py to").

Order: init session, then `write_run_config` (read-modify-write of the `.sdlc/config.json` that init
scaffolded; `config-missing` when init left none), then loop session.

Not measured: that the real launcher accepts this layout (the sibling-of-profile rule is copied from it, not
run against it); the skill invocation form of the prompts; whether `CLAUDE_CODE_SESSION_ID` reaches a loop
shell; the subagent transcript layout. `ASSUMPTIONS` lists them and every result carries the list.

Failure modes and levers: the arm raising, KeyboardInterrupt, SIGTERM (handler installed in the main thread
only) and a failed rows copy all end in cleanup. SIGKILL and power loss leave a marked
`sigma-regression-layer1-*` directory; the next `run_scenario` (or `sweep_stale()`) removes it when it has
this prefix, the marker text, a real directory of this user, and a marker older than a day (a marker-less one is removed when its own mtime is older than a day).
SIGTERM and SIGINT are ignored while the root is removed. `deadline` is an absolute `time.monotonic()` instant.
"""
from __future__ import annotations

import ast
import dataclasses
import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "sigma-regression-layer1-"
MARKER_NAME = ".sigma-regression-layer1"
MARKER_TEXT = "sigma-regression-layer1/1"
STALE_SECONDS = 86400
BUILD_MARGIN_SECONDS = 3600         # the sweep clock starts at root creation; building the scenario eats into the window
_monotonic = time.monotonic
SESSIONS = ("init", "loop")
GITHUB_SHAPED = ("GH_", "GITHUB_", "GIT_")
INSTRUCTION_FILES = ("CLAUDE.md", "CLAUDE.local.md", ".mcp.json", ".claude")
GOAL_PATH = "GOAL.md"
ROWS_NAME = "layer1-rows.json"
SCHEMA = "sigma.regression-layer1/1"
FAKE_REPO = "acme/regression-demo"
GIT_TIMEOUT_SECONDS = 120
RESULT_KEYS = ("cost", "session_id", "returncode", "timed_out", "detail")
ENV_ALLOWED = ("LANG", "TZ", "TERM", "SHELL", "USER", "LOGNAME")
ENV_RESERVED = ("PATH", "HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TMPDIR")
ASSUMPTIONS = (
    "unverified: skill invocation form of the prompts (/sigma-init vs /sigmaloop:sigma-init; plugin name sigmaloop)",
    "unverified: CLAUDE_CODE_SESSION_ID is not set, so it may not reach the loop shell",
    "unverified: subagent dispatch and the subagents/*.jsonl layout are not read here",
    "unverified: permission mode auto is representative of the owner's runs",
    "unverified: sibling-of-profile export rule is copied from the launcher, not measured against it",
    "unverified: the fixed goal file is a stub until slice 3",
)

_MODULES: dict = {}


class Layer1Refusal(Exception):
    """A precondition failed. `.code` is stable; `.detail` carries no environment or credential content."""

    def __init__(self, code, detail=""):
        super().__init__("layer1: REFUSED [%s]: %s" % (code, detail))
        self.code = code
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class RunSpec:
    commit_rev: str
    source_repo: str
    goal_file: str
    prompts: Mapping[str, str]
    model: Any
    belt: Any
    deadline: Any                  # ABSOLUTE time.monotonic() instant (as run_claude reads it), not a duration
    rates: Any
    permission_mode: str = "auto"
    verify_command: str = ""
    verify_enforce: bool = False
    session_env: Mapping[str, str] = dataclasses.field(default_factory=dict)
    artifact_dir: str = ""
    tmp_root: str = ""


@dataclasses.dataclass(frozen=True)
class SessionSpec:
    name: str
    index: int
    run_dir: str
    profile_dir: str
    plugin_dir: str
    repo: str
    prompt: str
    env: Mapping[str, str]
    permission_mode: str
    model: Any
    belt: Any
    deadline: Any
    rates: Any
    not_representative: bool


@dataclasses.dataclass(frozen=True)
class Scenario:
    root: str
    repo: str
    remote: str
    gh_state: str
    goal_path: str
    bin_dir: str
    sessions: tuple
    config_written: Mapping[str, Any]
    assumptions: tuple


# ------------------------------------------------------------------------------------------ loading


def _load(name, path):
    module = _MODULES.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location("_layer1_" + name, str(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _MODULES[name] = module
    return module


def _common():
    return _load("common", ROOT / "evals" / "bench" / "arms" / "common.py")


def _sigma():
    """sigma.py falls back to `from common import ArmRefusal`; give it THIS common, then put sys.modules back."""
    module = _MODULES.get("sigma")
    if module is None:
        common = _common()
        saved = sys.modules.get("common")
        sys.modules["common"] = common
        try:
            module = _load("sigma", ROOT / "evals" / "bench" / "arms" / "sigma.py")
        finally:
            if saved is None:
                sys.modules.pop("common", None)
            else:
                sys.modules["common"] = saved
    return module


def _scrub(text):
    return _load("scrub", ROOT / "skills" / "sigma-loop" / "scripts" / "scrub.py").scrub(text)


# ------------------------------------------------------------------------------------------ checks


def _missing(value):
    return value is None or value == ""


def _check_deadline(deadline):
    """`deadline` is an ABSOLUTE time.monotonic() instant, as evals/bench/arms/common.py run_claude reads it."""
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or deadline != deadline \
            or deadline in (float("inf"), float("-inf")):
        raise Layer1Refusal("deadline-invalid", "deadline must be a finite number: an absolute time.monotonic() instant")
    remaining = deadline - _monotonic()
    if remaining <= 0:
        raise Layer1Refusal("deadline-invalid", "deadline is not in the future (absolute time.monotonic() instant)")
    if remaining >= STALE_SECONDS - BUILD_MARGIN_SECONDS:
        raise Layer1Refusal("deadline-exceeds-stale-window",
                            "time left to the deadline must stay under the %ds stale-sweep window less a %ds build margin"
                            % (STALE_SECONDS, BUILD_MARGIN_SECONDS))


def _require(spec):
    for field in ("model", "belt", "deadline", "rates"):
        if getattr(spec, field) is None:
            raise Layer1Refusal("param-required", "%s must be given; there is no default" % field)
    for field in ("commit_rev", "source_repo", "goal_file"):
        if _missing(getattr(spec, field)):
            raise Layer1Refusal("param-required", "%s must be given" % field)
    _check_deadline(spec.deadline)
    if _missing(spec.permission_mode):
        raise Layer1Refusal("permission-mode-required", "name the permission mode explicitly")
    prompts = spec.prompts or {}
    for name in SESSIONS:
        if _missing(prompts.get(name)):
            raise Layer1Refusal("param-required", "prompts[%r] must be given" % name)
    if _missing(spec.artifact_dir):
        raise Layer1Refusal("artifact-dir-required", "rows are copied there before the scratch root is removed")


def _default_tmp_root():
    return tempfile.gettempdir()


def _tmp(tmp_root):
    return os.path.realpath(tmp_root or _default_tmp_root())


def _inside(root, path):
    root, path = os.path.realpath(root), os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


def _ancestor_guard(tmp_root):
    current = tmp_root
    while True:
        for name in INSTRUCTION_FILES:
            if os.path.lexists(os.path.join(current, name)):
                raise Layer1Refusal("instruction-ancestor", "%s above the scratch location would load into the arm" % name)
        parent = os.path.dirname(current)
        if parent == current:
            return
        current = parent


# ------------------------------------------------------------------------------------------ git


def _git_env():
    env = {key: value for key, value in os.environ.items()
           if key == "PATH" or key == "LANG" or key.startswith("LC_") or key == "TZ"}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    return env


def _git(args, cwd):
    """The ONLY subprocess call in this module, and only ever with `git`."""
    try:
        done = subprocess.run(["git"] + list(args), cwd=str(cwd), env=_git_env(), capture_output=True,
                              text=True, timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Layer1Refusal("git-failed", "git could not run: %s" % type(exc).__name__) from exc
    if done.returncode:
        raise Layer1Refusal("git-failed", "git %s exited %d: %s" % (args[0], done.returncode, done.stderr.strip()[:200]))
    return done.stdout


# ------------------------------------------------------------------------------------------ fake gh


def _fake_gh_body(harness_root):
    """The fake gh from tests/test_public_bootstrap_control.py, taken from the HARNESS checkout (never the commit under test)."""
    src = Path(harness_root) / "tests" / "test_public_bootstrap_control.py"
    try:
        tree = ast.parse(src.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise Layer1Refusal("fake-gh-missing", "cannot read %s" % src.name) from exc
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_FAKE_GH_BODY" for t in node.targets):
            return ast.literal_eval(node.value)
    raise Layer1Refusal("fake-gh-missing", "no _FAKE_GH_BODY in %s" % src.name)


def _write_gh(bin_dir, state_path, log_path, unhandled_path, remote):
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True)
    gh = bin_dir / "gh"
    gh.write_text("#!%s\n" % sys.executable + _fake_gh_body(ROOT), encoding="utf-8")
    gh.chmod(0o755)
    Path(log_path).write_text("", encoding="utf-8")
    Path(unhandled_path).write_text("", encoding="utf-8")
    Path(state_path).write_text(json.dumps({
        "repo": FAKE_REPO, "remote_git_dir": str(remote), "labels": {}, "label_seq": 0,
        "issues": {}, "issue_seq": 1, "prs": {}, "pr_seq": 100, "projects": [],
        "login": "regression-bot", "default_branch": "main", "allow_auto_merge": True,
        "viewer_permission": "ADMIN", "refuse_label_create": False}), encoding="utf-8")


# ------------------------------------------------------------------------------------------ environment


def build_env(paths, session_env, profile_dir):
    """Explicit-allowlist environment for one session. `isolated_env` forwards its whole input, so it never sees os.environ."""
    for key in session_env:
        if key in ENV_RESERVED or key.startswith("FAKE_GH_"):
            raise Layer1Refusal("env-collision", "session_env may not set %s" % key)
        if key.startswith(GITHUB_SHAPED):
            raise Layer1Refusal("github-shaped-env", "session_env may not set %s" % key)
    base = {key: value for key, value in os.environ.items()
            if key in ENV_ALLOWED or key.startswith("LC_")}
    inherited = os.environ.get("PATH", "")
    base["PATH"] = paths["bin_dir"] + (":" + inherited if inherited else "")
    base["FAKE_GH_STATE"] = paths["gh_state"]
    base["FAKE_GH_LOG"] = paths["gh_log"]
    base["FAKE_GH_UNHANDLED"] = paths["gh_unhandled"]
    env = _common().isolated_env(base, profile_dir)
    env.update(session_env)
    return env


# ------------------------------------------------------------------------------------------ scratch root


def _make_root(tmp_root):
    tmp = _tmp(tmp_root)
    _ancestor_guard(tmp)
    root = tempfile.mkdtemp(prefix=PREFIX, dir=tmp)
    os.chmod(root, 0o700)
    with open(os.path.join(root, MARKER_NAME), "w", encoding="utf-8") as handle:
        handle.write(MARKER_TEXT)
    return root


def _make_dirs(*paths):
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def _write_initial_tree(repo, goal_file):
    try:
        goal = Path(goal_file).read_bytes()
    except OSError as exc:
        raise Layer1Refusal("goal-file-unreadable", "cannot read the goal file") from exc
    (Path(repo) / GOAL_PATH).write_bytes(goal)
    (Path(repo) / "README.md").write_text("# regression scenario project\n", encoding="utf-8")


def build_scenario(spec, root=None):
    """Remote, clone, goal file, fake gh and the two exports. Session profiles are made later, one per arm call."""
    _require(spec)
    created_here = root is None
    if created_here:
        root = _make_root(spec.tmp_root)
    try:
        return _build(spec, root)
    except BaseException:
        if created_here:
            try:
                cleanup(root, tmp_root=spec.tmp_root)
            except Exception:                                   # noqa: BLE001 - the original failure is the one to raise
                pass
        raise


def _build(spec, root):
    sigma = _sigma()
    root = Path(root)
    repo, remote, bin_dir = root / "repo", root / "remote.git", root / "bin"
    state, log, unhandled = root / "gh_state.json", root / "gh_log.jsonl", root / "gh_unhandled.jsonl"
    try:
        commit = sigma.resolve_commit(spec.source_repo, spec.commit_rev)
    except _common().ArmRefusal as exc:
        raise Layer1Refusal("commit-unresolved", str(exc)) from exc
    _git(["init", "-q", "--bare", "-b", "main", str(remote)], root)
    _git(["clone", "-q", str(remote), str(repo)], root)
    _git(["config", "user.name", "regression-scenario"], repo)
    _git(["config", "user.email", "regression-scenario@example.invalid"], repo)
    _write_initial_tree(repo, spec.goal_file)
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "initial goal and stub readme"], repo)
    _git(["push", "-q", "-u", "origin", "main"], repo)
    _write_gh(bin_dir, state, log, unhandled, remote)
    sessions = []
    for index, name in enumerate(SESSIONS):
        run_dir = root / name
        _make_dirs(run_dir)
        plugin_dir = run_dir / "export"
        try:
            sigma.export_plugin(spec.source_repo, commit, plugin_dir)
        except _common().ArmRefusal as exc:
            raise Layer1Refusal("export-failed", str(exc)) from exc
        sessions.append(SessionSpec(
            name=name, index=index, run_dir=str(run_dir), profile_dir=str(run_dir / "profile"),
            plugin_dir=str(plugin_dir), repo=str(repo), prompt=spec.prompts[name], env={},
            permission_mode=spec.permission_mode, model=spec.model, belt=spec.belt,
            deadline=spec.deadline, rates=spec.rates,
            not_representative=spec.permission_mode != "auto"))
    return Scenario(root=str(root), repo=str(repo), remote=str(remote), gh_state=str(state),
                    goal_path=str(repo / GOAL_PATH), bin_dir=str(bin_dir), sessions=tuple(sessions),
                    config_written={}, assumptions=ASSUMPTIONS)


def prepare_session(scenario, session, session_env):
    """Make this session's fresh profile now, check it holds only empty leaves, return the spec carrying its env."""
    root = Path(scenario.root)
    paths = {"bin_dir": scenario.bin_dir, "gh_state": scenario.gh_state,
             "gh_log": str(root / "gh_log.jsonl"), "gh_unhandled": str(root / "gh_unhandled.jsonl")}
    env = build_env(paths, session_env, session.profile_dir)
    leaves = sorted(os.listdir(session.profile_dir))
    if leaves != ["claude-config", "codex-home", "home", "tmp"] or any(
            os.listdir(os.path.join(session.profile_dir, leaf)) for leaf in leaves):
        raise Layer1Refusal("profile-not-empty", "a session profile must start empty")
    return dataclasses.replace(session, env=env)


# ------------------------------------------------------------------------------------------ config


def _set_dotted(config, dotted, value):
    parts = dotted.split(".")
    node = config
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise Layer1Refusal("config-shape", "%s is not an object" % part)
        node = child
    node[parts[-1]] = value


def _get_dotted(config, dotted):
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _replace(source, target):
    os.replace(source, target)


def write_run_config(repo, spec):
    """Set the run's config keys in the file init scaffolded, atomically, and return what is read back."""
    path = Path(repo) / ".sdlc" / "config.json"
    if not path.is_file():
        raise Layer1Refusal("config-missing", "init left no .sdlc/config.json; none is invented")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise Layer1Refusal("config-invalid", "config.json is not JSON") from exc
    if not isinstance(config, dict):
        raise Layer1Refusal("config-invalid", "config.json is not an object")
    wanted = {"journal.enabled": True, "gates.plan_review.enabled": True, "action_log.enabled": True,
              "verify.command": spec.verify_command, "verify.enforce": bool(spec.verify_enforce),
              "work.enabled": True}
    for dotted, value in wanted.items():
        _set_dotted(config, dotted, value)
    temp = path.with_name(path.name + ".layer1-tmp")
    temp.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _replace(temp, path)
    back = json.loads(path.read_text(encoding="utf-8"))
    recorded = {dotted: _get_dotted(back, dotted) for dotted in wanted}
    for dotted, value in wanted.items():
        if recorded[dotted] != value or type(recorded[dotted]) is not type(value):
            raise Layer1Refusal("config-readback", "%s did not read back as written" % dotted)
    return recorded


# ------------------------------------------------------------------------------------------ cleanup


def cleanup(root, *, tmp_root=None):
    """Remove one scratch root: this user's real directory, PREFIX name, marker text matches, directly under tmp_root."""
    tmp = _tmp(tmp_root)
    root = str(root).rstrip(os.sep)
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        return
    if os.path.islink(root):
        raise Layer1Refusal("cleanup-symlink", "refusing to remove a symlink")
    if not os.path.isdir(root):
        raise Layer1Refusal("cleanup-not-dir", "refusing to remove a non-directory")
    if info.st_uid != os.getuid():
        raise Layer1Refusal("cleanup-foreign-owner", "refusing to remove another user's directory")
    if not os.path.basename(root).startswith(PREFIX):
        raise Layer1Refusal("cleanup-prefix", "name does not carry the scratch prefix")
    if os.path.realpath(os.path.dirname(os.path.abspath(root))) != tmp:
        raise Layer1Refusal("cleanup-location", "not directly under the scratch location")
    marker = os.path.join(root, MARKER_NAME)
    try:
        marked = stat.S_ISREG(os.lstat(marker).st_mode) and Path(marker).read_text(encoding="utf-8") == MARKER_TEXT
    except OSError:
        marked = False
    if not marked:
        raise Layer1Refusal("cleanup-marker", "no scratch marker of ours; leaving it alone")
    try:
        _common().remove_tree(root)
    except _common().ArmRefusal as exc:
        raise Layer1Refusal("cleanup-failed", str(exc)) from exc


def sweep_stale(tmp_root=None, now=None):
    """Remove leftover scratch roots of crashed runs: prefix, marker text, real dir, own uid, marker older than a day."""
    tmp = _tmp(tmp_root)
    now = time.time() if now is None else now
    removed = []
    try:
        names = sorted(os.listdir(tmp))
    except OSError:
        return removed
    for name in names:
        path = os.path.join(tmp, name)
        if not name.startswith(PREFIX) or os.path.realpath(os.path.dirname(path)) != tmp:
            continue
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            continue
        try:
            marker = os.lstat(os.path.join(path, MARKER_NAME))
        except FileNotFoundError:                               # a removal that died after taking the marker
            if now - info.st_mtime > STALE_SECONDS:
                try:
                    _common().remove_tree(path)
                except Exception:                               # noqa: BLE001 - housekeeping; the next sweep retries
                    continue
                removed.append(path)
            continue
        except OSError:
            continue
        if not stat.S_ISREG(marker.st_mode):                    # marker TEXT is checked once, by cleanup
            continue
        if now - marker.st_mtime <= STALE_SECONDS:
            continue
        try:
            cleanup(path, tmp_root=tmp)
        except Layer1Refusal:
            continue
        removed.append(path)
    return removed


# ------------------------------------------------------------------------------------------ driver


def _clean(value, depth=0):
    if depth > 6:
        return repr(value)[:200]
    if isinstance(value, str):
        return _scrub(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _clean(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in value]
    return _scrub(repr(value)[:200])


def _result_row(result):
    if result is None:
        raise Layer1Refusal("missing-arm-result", "the arm returned nothing; a result is required")
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        source = dataclasses.asdict(result)
    elif isinstance(result, Mapping):
        source = dict(result)
    else:
        source = {key: getattr(result, key) for key in RESULT_KEYS if hasattr(result, key)}
    return _clean({key: source[key] for key in RESULT_KEYS if key in source})


def _write_rows(artifact_dir, document):
    target = Path(artifact_dir)
    target.mkdir(parents=True, exist_ok=True)
    final = target / ROWS_NAME
    temp = target / (ROWS_NAME + ".tmp.%d" % os.getpid())
    try:
        temp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, final)
    finally:
        if temp.exists():
            temp.unlink()
    return str(final)


def _finalise(root, document, artifact_dir, tmp_root):
    """Rows first, removal last. Returns the first problem instead of raising, so the arm's own exception is never masked."""
    problem = None
    copied = None
    if root is not None and not _inside(root, artifact_dir):
        try:
            copied = _write_rows(artifact_dir, document)
        except Exception as exc:                                # noqa: BLE001 - reported below, removal still runs
            problem = Layer1Refusal("artifact-copy-failed", type(exc).__name__)
    if root is not None:
        try:
            cleanup(root, tmp_root=tmp_root)
        except Exception as exc:                                # noqa: BLE001
            problem = problem or exc
    return problem, copied


def _finalise_shielded(root, document, artifact_dir, tmp_root):
    """`_finalise` with SIGTERM and SIGINT ignored (main thread only), so a second signal cannot stop removal partway."""
    if threading.current_thread() is not threading.main_thread():
        return _finalise(root, document, artifact_dir, tmp_root)
    held = {}
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            held[number] = signal.signal(number, signal.SIG_IGN)
        return _finalise(root, document, artifact_dir, tmp_root)
    finally:
        for number, old in held.items():
            signal.signal(number, signal.SIG_DFL if old is None else old)


def _note(error, text):
    adder = getattr(error, "add_note", None)
    if adder is not None:
        adder(text)


_NOT_CAPTURED = object()


def run_scenario(arm, spec):
    """Build, run init then loop through `arm`, copy the rows, always remove the scratch root."""
    try:
        _common().require_posix()
    except _common().ArmRefusal as exc:
        raise Layer1Refusal("unsupported-platform", str(exc)) from exc
    _require(spec)
    tmp_root = _tmp(spec.tmp_root)
    artifact_dir = os.path.realpath(spec.artifact_dir)
    try:
        sweep_stale(tmp_root)
    except Exception:                                           # noqa: BLE001 - housekeeping never blocks a run
        pass
    previous = _NOT_CAPTURED
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, _on_sigterm)
    root = None
    original = None
    rows = [{"name": name, "status": "not-run"} for name in SESSIONS]
    config_written = {}
    try:
        root = _make_root(tmp_root)
        if _inside(root, artifact_dir):
            raise Layer1Refusal("artifact-inside-root", "the artifact directory must be outside the scratch root")
        scenario = build_scenario(spec, root)
        for index, planned in enumerate(scenario.sessions):
            session = prepare_session(scenario, planned, spec.session_env)
            rows[index].update(profile_dir=session.profile_dir, status="raised")
            try:
                result = arm(session)
                rows[index].update(result=_result_row(result), status="ok")
            except BaseException as exc:
                rows[index]["error"] = {"type": type(exc).__name__, "message": _scrub(str(exc))[:200]}
                raise
            if index == 0:
                config_written = write_run_config(scenario.repo, spec)
                scenario = dataclasses.replace(scenario, config_written=config_written)
    except BaseException as exc:
        original = exc
        raise
    finally:
        document = {"schema": SCHEMA, "permission_mode": spec.permission_mode,
                    "not_representative": spec.permission_mode != "auto", "goal_path": GOAL_PATH,
                    "config_written": config_written, "assumptions": list(ASSUMPTIONS),
                    "status": "error" if original is not None else "ok", "sessions": rows}
        try:
            problem, copied = _finalise_shielded(root, document, artifact_dir, tmp_root)
        finally:
            if previous is not _NOT_CAPTURED:
                signal.signal(signal.SIGTERM, signal.SIG_DFL if previous is None else previous)
        if problem is not None:
            if original is None:
                raise problem
            _note(original, "also failed: %s" % problem)
    return {"goal_path": GOAL_PATH, "sessions": rows, "config_written": config_written,
            "assumptions": list(ASSUMPTIONS), "not_representative": spec.permission_mode != "auto",
            "permission_mode": spec.permission_mode, "copied_to": copied}


def _on_sigterm(signum, frame):
    raise SystemExit(143)

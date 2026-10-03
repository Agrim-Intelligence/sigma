#!/usr/bin/env python3
"""Run a hermetic, metered benchmark smoke test without exposing hidden tests.

Three live arms ship (``SigmaArm`` A1, ``PlainArm`` A2, ``MatchedArm`` A3, see
``docs/bench/preregistration.md``).  They start ``claude -p`` only through the operator's
isolation launcher, and only the exact in-tree classes are admitted: an arbitrary ``Arm``
subclass is refused.  The built-in ``--fake-arm`` is the zero-spend smoke control documented
in ``evals/README.md``.
"""
import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import threading
import time
from typing import Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from arms import common as arms_common  # noqa: E402  (path set up just above)
from arms import matched as arms_matched  # noqa: E402
from arms import sigma as arms_sigma  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
TASK_SCHEMA = "sigma.benchmark-tasks/v1"
RESULT_SCHEMA = "sigma.benchmark-results/v1"


class BenchmarkRefusal(ValueError):
    """A benchmark precondition was not met; no arm has been started."""


class BenchmarkSignal(BaseException):
    """SIGTERM or SIGHUP reached the harness: unwind so children are killed and paid rows are kept."""


@dataclass(frozen=True)
class Task:
    identifier: str
    prompt: str
    source: Path
    visible_command: tuple[str, ...]
    hidden_bundle: str


@dataclass(frozen=True)
class ArmRun:
    """The stable extension result for one arm invocation."""
    cost_usd: Optional[float]
    interventions: int = 0
    reason: str = ""
    completed: bool = True


class Arm:
    """Legacy in-process arm seam; untrusted implementations are refused.

    A Python callback executes with the benchmark runner's own authority, so it
    cannot be an isolation boundary for hidden tests.  Only ``FakeArm`` below
    is an in-tree, trusted smoke implementation.  All live arms refuse until a
    separately reviewed, enforceable isolation protocol exists.
    """
    name = "unnamed"

    def run(self, workdir, prompt, env, deadline):  # pragma: no cover - interface only
        raise NotImplementedError


class FakeArm(Arm):
    """A zero-spend arm used only by the documented hermetic smoke command."""
    name = "fake"

    def __init__(self, costs=None):
        self.costs = list(costs or [])
        self.allowances = []

    def run(self, workdir, prompt, env, deadline):
        del prompt, deadline
        self.allowances.append(env["SIGMA_BENCH_MAX_USD"])
        (Path(workdir) / "arm-complete").write_text("fake smoke\n", encoding="utf-8")
        cost = self.costs.pop(0) if self.costs else 0.0
        return ArmRun(cost_usd=cost, reason="built-in hermetic smoke arm")


class LiveArm(Arm):
    """Common base of the in-tree live arms: a pinned model and permission mode, a spend belt, metering.

    The arm is bound per run (``bind``) with the task's visible command, A1's spend for that task and the
    plugin-directory guard, because ``Arm.run`` carries none of them.  Admission is by exact class.
    """

    def __init__(self, *, claude, model, permission_mode, belt_usd, rates=None, scoring_timeout=600):
        self.claude = str(claude)
        self.model = model
        self.permission_mode = permission_mode
        self.belt_usd = belt_usd
        self.rates = rates
        self.scoring_timeout = scoring_timeout
        self.spend = arms_common.Spend()
        self.visible_command = None
        self.match_usd = None
        self.guard = lambda: None

    def bind(self, visible_command, match_usd, guard):
        self.visible_command, self.match_usd, self.guard = visible_command, match_usd, guard

    def provenance(self):
        return {"model": self.model, "permission_mode": self.permission_mode, "belt_usd": self.belt_usd}

    def facts(self):
        return {"arm": self.name, "status": "planned, not observed",
                "model": self.model, "permission_mode": self.permission_mode, "belt_usd": self.belt_usd,
                "plugin": "none", "profile": "fresh HOME, CLAUDE_CONFIG_DIR and CODEX_HOME per run",
                "real_plugin_dirs_hashed": len(arms_common.real_plugin_dirs(os.environ)),
                "containment": "operator launcher; containment is not verified by Sigma"}

    def _run_one(self, workdir, prompt, env, deadline, plugin_dir):
        launcher = env["SIGMA_BENCH_ISOLATION_LAUNCHER"]
        attempt = arms_common.run_claude(
            launcher=launcher, claude=self.claude, model=self.model, permission_mode=self.permission_mode,
            budget_usd=min(self.belt_usd, float(env["SIGMA_BENCH_MAX_USD"])), plugin_dir=plugin_dir,
            workdir=workdir, prompt=prompt, environment=env, profile_dir=Path(workdir).parent / "profile",
            deadline=deadline, rates=self.rates)
        self.spend.add(attempt.cost, attempt.priced_part)
        reason = "claude exit %s" % attempt.returncode
        if attempt.timed_out:
            reason = "claude killed at the wall-clock limit"
        return ArmRun(cost_usd=attempt.cost, reason=reason)


class PlainArm(LiveArm):
    """A2: the agent alone, no plugin."""
    name = "plain"

    def run(self, workdir, prompt, env, deadline):
        self.spend = arms_common.Spend()
        return self._run_one(workdir, prompt, env, deadline, None)


class SigmaArm(LiveArm):
    """A1: Sigma loaded from a clean ``git archive`` export of a pinned commit, never the repository."""
    name = "sigma"

    def __init__(self, *, repo, commit, **kwargs):
        super().__init__(**kwargs)
        self.repo = Path(repo)
        self.commit = arms_sigma.resolve_commit(self.repo, commit)

    def provenance(self):
        return dict(super().provenance(), sigma_commit=self.commit)

    def facts(self):
        facts = super().facts()
        scratch = tempfile.mkdtemp(prefix="sigma-bench-dry-")
        try:
            export = arms_sigma.export_plugin(self.repo, self.commit, Path(scratch) / "plugin")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        facts.update(plugin="clean export of the pinned commit (%s)" % " ".join(arms_sigma.EXPORT_PATHS),
                     export=export)
        return facts

    def run(self, workdir, prompt, env, deadline):
        self.spend = arms_common.Spend()
        plugin = Path(workdir).parent / "plugin"
        arms_sigma.export_plugin(self.repo, self.commit, plugin)
        return self._run_one(workdir, prompt, env, deadline, plugin)


class MatchedArm(LiveArm):
    """A3: the plain agent retried in fresh workdirs until visible tests pass or spend reaches A1's."""
    name = "matched"

    def __init__(self, *, max_attempts=10, **kwargs):
        super().__init__(**kwargs)
        self.max_attempts = max_attempts

    def facts(self):
        return dict(super().facts(), max_attempts=self.max_attempts,
                    retry="fresh workdir and profile per attempt, until visible tests pass or spend reaches A1's")

    def run(self, workdir, prompt, env, deadline):
        self.spend = arms_common.Spend()
        launcher = env["SIGMA_BENCH_ISOLATION_LAUNCHER"]

        def attempt(tree, profile_dir, budget):
            return arms_common.run_claude(
                launcher=launcher, claude=self.claude, model=self.model,
                permission_mode=self.permission_mode, budget_usd=min(self.belt_usd, budget),
                plugin_dir=None, workdir=tree, prompt=prompt, environment=env, profile_dir=profile_dir,
                deadline=deadline, rates=self.rates)

        def visible(tree):
            scoring_env = arms_common.isolated_env(env, Path(tree).parent / "profile")
            return arms_common.command_passed(
                launcher, self.visible_command, cwd=tree, env=scoring_env,
                timeout=min(self.scoring_timeout, max(0.0, deadline - time.monotonic())))

        cost, reason = arms_matched.run_matched(
            workdir, Path(workdir).parent / "attempts", attempt=attempt, visible=visible, guard=self.guard,
            match_usd=self.match_usd, remaining_usd=float(env["SIGMA_BENCH_MAX_USD"]),
            max_attempts=self.max_attempts, deadline=deadline, spend=self.spend)
        return ArmRun(cost_usd=cost, reason=reason)


LIVE_ARMS = (SigmaArm, PlainArm, MatchedArm)


def _within(child, parent):
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _truthy(value):
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}


def _safe_component(value):
    return (isinstance(value, str) and value not in {"", ".", ".."} and
            Path(value).name == value and "\\" not in value)


def _load_tasks(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkRefusal(f"unreadable task manifest: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != TASK_SCHEMA:
        raise BenchmarkRefusal(f"task manifest must declare {TASK_SCHEMA}")
    rows = raw.get("tasks")
    if not isinstance(rows, list) or not rows:
        raise BenchmarkRefusal("task manifest must contain at least one task")
    tasks = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise BenchmarkRefusal("task entries must be objects")
        identifier = row.get("id")
        prompt = row.get("prompt")
        source_value = row.get("source")
        hidden_bundle = row.get("hidden_bundle")
        command = row.get("visible_command")
        if (not _safe_component(identifier) or identifier in seen or
                not isinstance(prompt, str) or not prompt or
                not isinstance(source_value, str) or not source_value or
                not isinstance(hidden_bundle, str) or not hidden_bundle or
                not isinstance(command, list) or not command or
                not all(isinstance(part, str) and part for part in command)):
            raise BenchmarkRefusal("each task needs unique id, prompt, source, hidden bundle and argv")
        bundle_path = Path(hidden_bundle)
        if bundle_path.is_absolute() or ".." in bundle_path.parts:
            raise BenchmarkRefusal(f"hidden bundle for {identifier!r} must be a relative identifier")
        source = (manifest_path.parent / source_value).resolve()
        if not source.is_dir() or not _within(source, manifest_path.parent):
            raise BenchmarkRefusal(f"task source for {identifier!r} must be a directory beside manifest")
        seen.add(identifier)
        tasks.append(Task(identifier, prompt, source, tuple(command), hidden_bundle))
    return tasks


def _has_hidden_symlink(source, hidden_root):
    for path in source.rglob("*"):
        if path.is_symlink() and _within(path, hidden_root):
            return True
    return False


def _validate_inputs(tasks, max_usd, hidden_root, results_path, background, environment, scratch_root):
    if max_usd is None or not isinstance(max_usd, (int, float)) or not math.isfinite(max_usd) or max_usd <= 0:
        raise BenchmarkRefusal("--max-usd must be a positive explicit spend ceiling")
    if _truthy(environment.get("CI")):
        raise BenchmarkRefusal("benchmark refuses to run from CI")
    if background:
        raise BenchmarkRefusal("benchmark refuses background execution")
    hidden_root = Path(hidden_root).resolve()
    if not hidden_root.is_dir():
        raise BenchmarkRefusal("hidden root must be an existing directory")
    if _within(hidden_root, ROOT):
        raise BenchmarkRefusal("hidden root must be outside the repository")
    scratch_root = Path(scratch_root).resolve()
    if _within(scratch_root, ROOT):
        raise BenchmarkRefusal("scratch root must be outside the repository (arms read their working area)")
    if _within(hidden_root, scratch_root):
        raise BenchmarkRefusal(
            "hidden root must be outside the scratch root (every arm workdir, profile and plugin export lives there)")
    results_path = Path(results_path).resolve()
    if _within(results_path, hidden_root):
        raise BenchmarkRefusal("results path must be outside hidden root")
    if _within(results_path, scratch_root):
        raise BenchmarkRefusal("results path must be outside the scratch root")
    for task in tasks:
        if _within(hidden_root, task.source) or _within(task.source, hidden_root):
            raise BenchmarkRefusal("hidden root must not overlap a task source or arm worktree")
        if _has_hidden_symlink(task.source, hidden_root):
            raise BenchmarkRefusal("task source must not link into hidden root")
        if str(hidden_root) in task.prompt:
            raise BenchmarkRefusal("task prompt must not disclose hidden root")
        bundle = hidden_root / task.hidden_bundle
        if not bundle.is_dir():
            raise BenchmarkRefusal(f"hidden bundle {task.hidden_bundle!r} is missing")
    return hidden_root, results_path


def _safe_arm_environment(environment, hidden_root, arm, remaining_usd, isolation_launcher):
    root_text = str(hidden_root)
    env = {key: value for key, value in environment.items()
           if root_text not in str(value) and key != "SIGMA_BENCH_HIDDEN_ROOT"}
    env["SIGMA_BENCH_ARM_NAME"] = arm.name
    # Later live arms must translate this into the host's own per-run budget flag.
    # Keeping it in the injected environment preserves the fixed Arm.run seam.
    env["SIGMA_BENCH_MAX_USD"] = str(remaining_usd)
    env["SIGMA_BENCH_ISOLATION_LAUNCHER"] = str(isolation_launcher)
    return env


TRUSTED_ARMS = (FakeArm,) + LIVE_ARMS


def _run_arm(arm, workdir, prompt, environment, deadline, binding=None):
    """Run only an exact in-tree arm class.

    An arbitrary callback can read the runner's filesystem, so any other ``Arm`` subclass is refused.
    The live arms start ``claude`` only through the operator launcher; Sigma still cannot prove what
    that launcher contains, and the README says so.
    """
    if type(arm) not in TRUSTED_ARMS:
        raise BenchmarkRefusal(
            "arm classes outside the in-tree set are refused until Sigma has an enforceable isolation boundary")
    try:
        if isinstance(arm, LiveArm):
            arm.bind(*binding)
        return arm.run(workdir, prompt, environment, deadline)
    except arms_common.ArmRefusal as exc:
        raise BenchmarkRefusal(str(exc)) from exc


def _command_passed(argv, cwd, env=None, launcher=None, timeout=600):
    """Run a scoring command through the operator launcher, bounded, in its own process group."""
    returncode, timed_out, _ = arms_common.run_bounded(
        [launcher, "--", *argv], cwd=cwd, env=env, timeout=timeout)
    return returncode == 0 and not timed_out


def _hidden_passed(hidden_root, task, final_tree, evaluator_root, env=None, launcher=None, timeout=600):
    bundle = hidden_root / task.hidden_bundle
    verifier = bundle / "verify.json"
    try:
        raw = json.loads(verifier.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkRefusal(f"hidden bundle {task.hidden_bundle!r} has no valid verifier: {exc}") from exc
    command = raw.get("command") if isinstance(raw, dict) else None
    if not isinstance(command, list) or not command or not all(isinstance(p, str) and p for p in command):
        raise BenchmarkRefusal(f"hidden bundle {task.hidden_bundle!r} verifier must use argv")
    scored_tree = evaluator_root / task.identifier
    shutil.copytree(final_tree, scored_tree, symlinks=True)
    shutil.copytree(bundle, scored_tree / ".sigma-hidden", symlinks=True)
    return _command_passed(command, scored_tree, env, launcher, timeout)


def _write_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".benchmark-", suffix=".json", delete=False) as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _validate_isolation_launcher(launcher, tasks, hidden_root, scratch_root):
    """Validate only the operator-controlled launcher *location*, never its semantics.

    Process/filesystem isolation cannot be inferred from an arbitrary executable's
    bytes.  An operator must therefore provide and own the external boundary;
    this runner fails closed until it has an absolute executable outside every
    repository, hidden, task, and scratch tree.  This location check is not a
    proof of containment; the runner still refuses all live arms until it has
    a concrete, enforceable isolation protocol.
    """
    if launcher is None:
        raise BenchmarkRefusal("operator-supplied isolation launcher is required")
    supplied = Path(launcher)
    if not supplied.is_absolute():
        raise BenchmarkRefusal("isolation launcher must be an absolute executable path")
    launcher = supplied.resolve()
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise BenchmarkRefusal("isolation launcher must be an executable file")
    untrusted = [ROOT.resolve(), Path(hidden_root).resolve(), Path(scratch_root).resolve()]
    untrusted.extend(task.source.resolve() for task in tasks)
    if any(_within(launcher, root) for root in untrusted):
        raise BenchmarkRefusal(
            "isolation launcher must be outside repository and task trees")
    return launcher


def _live_arm_sanity(arms, repeats):
    live = [type(arm) for arm in arms]
    if MatchedArm in live:
        if SigmaArm not in live[:live.index(MatchedArm)]:
            raise BenchmarkRefusal("the matched-spend arm needs the sigma arm earlier in the same run "
                                   "(its cap is A1's spend for the task)")
        if repeats != 1:
            raise BenchmarkRefusal("the matched-spend arm is defined for one repeat")


def _remove_run_directory(run_root):
    """Delete a finished run's whole directory (worktree, profile, transcripts, scoring copy)."""
    try:
        arms_common.remove_tree(run_root)
    except arms_common.ArmRefusal as exc:
        raise BenchmarkRefusal(f"could not remove run directory: {exc}") from exc


def _write_aborted(results_path, max_usd, rows, provenance, cause, current):
    """Keep the rows already paid for when the benchmark stops; an ``aborted`` object marks it partial."""
    if isinstance(cause, (BenchmarkRefusal, arms_common.ArmRefusal)):
        reason = str(cause)
    elif isinstance(cause, BenchmarkSignal):
        reason = "signal " + str(cause)
    else:
        reason = type(cause).__name__
    report = {"schema": RESULT_SCHEMA, "max_usd": max_usd, "runs": rows,
              "aborted": {"reason": reason,
                          "task": current.get("task"), "arm": current.get("arm"),
                          "repeat": current.get("repeat"),
                          "cost_usd_spent": round(max(
                              current.get("spent", 0.0),
                              getattr(getattr(current.get("arm_object"), "spend", None), "usd", 0.0)), 6)}}
    if provenance:
        report["provenance"] = provenance
    try:
        _write_json_atomic(Path(results_path), report)
    except OSError:
        pass


def _unwind_on_signals():
    """Turn SIGTERM and SIGHUP into an exception so the process-group kills and the aborted write run."""
    if threading.current_thread() is not threading.main_thread():
        return {}

    def handler(signum, _frame):
        raise BenchmarkSignal(signal.Signals(signum).name)

    return {number: signal.signal(number, handler) for number in (signal.SIGTERM, signal.SIGHUP)}


def run_benchmark(manifest_path, arms, *, max_usd, hidden_root, results_path, scratch_root,
                  repeats=1, deadline_seconds=3600, background=False, environment=None,
                  isolation_launcher=None, scoring_timeout=600, dry_run=False):
    """Run injected arms on copied tasks and return/write content-free result rows.

    Every run lives in its own directory under ``scratch_root``, scored immediately through the
    operator launcher and then deleted, so a later arm can read neither an earlier solution nor the
    hidden bundle.  The benchmark stops (keeping the paid rows in a report marked ``aborted``) when the
    operator's real plugin directories change, a run cannot be priced, or a run overshoots the ceiling.
    """
    environment = dict(os.environ if environment is None else environment)
    tasks = _load_tasks(manifest_path)
    if (not arms or any(not _safe_component(getattr(arm, "name", "")) for arm in arms) or
            any(not isinstance(arm, Arm) for arm in arms)):
        raise BenchmarkRefusal("at least one safely named arm is required")
    if not isinstance(repeats, int) or repeats < 1:
        raise BenchmarkRefusal("repeats must be a positive integer")
    _live_arm_sanity(arms, repeats)
    hidden_root, results_path = _validate_inputs(tasks, max_usd, hidden_root, results_path,
                                                 background, environment, scratch_root)
    scratch_root = Path(scratch_root).resolve()
    if _within(scratch_root, hidden_root):
        raise BenchmarkRefusal("scratch root must be outside hidden root")
    isolation_launcher = _validate_isolation_launcher(isolation_launcher, tasks, hidden_root,
                                                      scratch_root)
    if scratch_root.is_dir() and any(scratch_root.iterdir()):
        raise BenchmarkRefusal(
            "scratch root must be empty: leftovers of an earlier run could be read by an arm "
            "(empty it deliberately)")
    if any(isinstance(arm, LiveArm) for arm in arms):
        arms_common.require_posix()
    if dry_run:
        return {"schema": RESULT_SCHEMA, "dry_run": True, "max_usd": max_usd,
                "arms": [arm.facts() if hasattr(arm, "facts") else {"arm": arm.name} for arm in arms]}
    scratch_root.mkdir(parents=True, exist_ok=True)
    # The baseline is taken before ANY claude invocation, so nothing the harness starts can be absorbed in it.
    baseline = arms_common.plugin_digest(environment)

    def guard():
        if arms_common.plugin_digest(environment) != baseline:
            raise BenchmarkRefusal("real plugin directory changed during the benchmark; aborting")

    provenance = {}
    previous_handlers = _unwind_on_signals()
    live = [arm for arm in arms if isinstance(arm, LiveArm)]
    spent = 0.0
    rows = []
    current = {}
    a1_spend = {}
    try:
        if live:
            version_dir = Path(tempfile.mkdtemp(prefix="sigma-bench-version-"))
            try:
                version_env = arms_common.isolated_env(environment, version_dir)
                version = arms_common.claude_version(isolation_launcher, live[0].claude, env=version_env,
                                                     cwd=version_dir)
            finally:
                shutil.rmtree(version_dir, ignore_errors=True)
            provenance = dict(live[0].provenance(), claude_version=version)
            for arm in live:
                if isinstance(arm, SigmaArm):
                    provenance["sigma_commit"] = arm.commit
        for task in tasks:
            for repeat in range(1, repeats + 1):
                for arm in arms:
                    guard()
                    remaining_usd = max_usd - spent
                    skip = ""
                    if remaining_usd <= 0:
                        skip = "spend ceiling reached"
                    elif isinstance(arm, MatchedArm) and task.identifier not in a1_spend:
                        skip = "no A1 spend recorded for this task"
                    if skip:
                        rows.append({"task": task.identifier, "arm": arm.name, "repeat": repeat,
                                     "status": "skipped", "reason": skip,
                                     "visible_passed": None, "hidden_passed": None,
                                     "cost_usd": None, "wall_seconds": None, "interventions": None})
                        continue
                    current = {"task": task.identifier, "arm": arm.name, "repeat": repeat, "spent": 0.0,
                               "arm_object": arm}
                    run_root = Path(tempfile.mkdtemp(prefix=f"{task.identifier}-{arm.name}-",
                                                      dir=scratch_root))
                    try:
                        workdir = run_root / "worktree"
                        shutil.copytree(task.source, workdir, symlinks=True)
                        started = time.monotonic()
                        arm_env = _safe_arm_environment(environment, hidden_root, arm, remaining_usd,
                                                        isolation_launcher)
                        result = _run_arm(arm, workdir, task.prompt, arm_env,
                                          time.monotonic() + deadline_seconds,
                                          (task.visible_command, a1_spend.get(task.identifier), guard))
                        current["spent"] = getattr(getattr(arm, "spend", None), "usd", 0.0)
                        if not isinstance(result, ArmRun):
                            raise BenchmarkRefusal(f"arm {arm.name!r} returned no ArmRun")
                        if (not isinstance(result.interventions, int) or isinstance(result.interventions, bool)
                                or result.interventions < 0):
                            raise BenchmarkRefusal(f"arm {arm.name!r} reported invalid interventions")
                        if str(hidden_root) in result.reason:
                            raise BenchmarkRefusal(f"arm {arm.name!r} disclosed hidden root in its result")
                        elapsed = round(time.monotonic() - started, 6)
                        if result.cost_usd is None and type(arm) is not FakeArm:
                            raise BenchmarkRefusal(
                                f"arm {arm.name!r} run could not be priced, so the spend ceiling cannot be "
                                f"applied; stopping ({result.reason})")
                        if result.cost_usd is not None:
                            if (not isinstance(result.cost_usd, (int, float)) or
                                    not math.isfinite(result.cost_usd) or result.cost_usd < 0):
                                raise BenchmarkRefusal(f"arm {arm.name!r} reported invalid cost")
                            current["spent"] = result.cost_usd
                            if result.cost_usd > remaining_usd:
                                raise BenchmarkRefusal(
                                    f"arm {arm.name!r} reported cost above remaining spend ceiling")
                        scoring_env = arms_common.isolated_env(arm_env, run_root / "scoring-profile")
                        visible = (_command_passed(task.visible_command, workdir, scoring_env,
                                                   isolation_launcher, scoring_timeout)
                                   if result.completed else None)
                        hidden = (_hidden_passed(hidden_root, task, workdir, run_root / "scoring",
                                                 scoring_env, isolation_launcher, scoring_timeout)
                                  if result.completed else None)
                        if result.cost_usd is not None:
                            spent += result.cost_usd
                        if type(arm) is SigmaArm and result.cost_usd is not None:
                            a1_spend[task.identifier] = result.cost_usd
                        rows.append({"task": task.identifier, "arm": arm.name, "repeat": repeat,
                                     "status": "completed" if result.completed else "failed",
                                     "reason": result.reason, "visible_passed": visible,
                                     "hidden_passed": hidden, "cost_usd": result.cost_usd,
                                     "wall_seconds": elapsed, "interventions": result.interventions})
                        current = {}
                    finally:
                        _remove_run_directory(run_root)
        guard()
    except BaseException as exc:
        if current or rows or spent:
            _write_aborted(results_path, max_usd, rows, provenance, exc, current)
        raise
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler if handler is not None else signal.SIG_DFL)
    report = {"schema": RESULT_SCHEMA, "max_usd": max_usd, "runs": rows}
    if provenance:
        report["provenance"] = provenance
    _write_json_atomic(results_path, report)
    return report


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the benchmark arms (or the zero-spend smoke arm) on copied tasks")
    run.add_argument("--manifest", required=True, type=Path)
    run.add_argument("--hidden-root", required=True, type=Path)
    run.add_argument("--results", required=True, type=Path)
    run.add_argument("--scratch-root", type=Path,
                     default=Path.home() / ".sigma-ops" / "bench" / "scratch",
                     help="must be empty; every run directory lives here and is deleted after scoring")
    run.add_argument("--max-usd", type=float)
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--deadline-seconds", type=int, default=3600)
    run.add_argument("--scoring-timeout-seconds", type=int, default=600,
                     help="bound on each visible or hidden scoring command")
    run.add_argument("--isolation-launcher", type=Path,
                     help="absolute operator-owned sandbox/privilege-separation launcher")
    run.add_argument("--background", action="store_true")
    run.add_argument("--fake-arm", action="store_true",
                     help="use only the built-in zero-spend smoke arm")
    run.add_argument("--arm", choices=("sigma", "plain", "matched", "all"),
                     help="live arms: A1 sigma, A2 plain, A3 matched-spend, or all three in that order")
    run.add_argument("--model", help="the one pinned model id every live arm uses")
    run.add_argument("--permission-mode", dest="permission_mode",
                     help="claude --permission-mode, identical for every arm (owner's choice, no default)")
    run.add_argument("--claude", default="claude", help="the claude executable (resolved on PATH)")
    run.add_argument("--sigma-commit", dest="sigma_commit",
                     help="commit the sigma arm's clean plugin export is made from (required, no default)")
    run.add_argument("--sigma-repo", dest="sigma_repo", type=Path, default=ROOT,
                     help="repository the pinned commit is exported from (default: this repository)")
    run.add_argument("--belt-usd", dest="belt_usd", type=float, default=15.0,
                     help="per-run claude --max-budget-usd belt (never above the remaining ceiling)")
    run.add_argument("--max-attempts", dest="max_attempts", type=int, default=10,
                     help="attempt bound of the matched-spend arm")
    run.add_argument("--rates", type=Path, help="rate-card CSV for the meter (default: the shipped card)")
    run.add_argument("--dry-run", action="store_true",
                     help="validate everything and print each arm's planned isolation facts; spawn nothing")
    return parser.parse_args(argv)


def _build_live_arms(args):
    arms_common.require_posix()
    if not args.model:
        raise BenchmarkRefusal("--model is required for live arms")
    if not args.permission_mode:
        raise BenchmarkRefusal("--permission-mode is required for live arms (identical for every arm)")
    names = ("sigma", "plain", "matched") if args.arm == "all" else (args.arm,)
    if "sigma" in names and not args.sigma_commit:
        raise BenchmarkRefusal("--sigma-commit is required for the sigma arm (the export is pinned)")
    claude = shutil.which(args.claude)
    if claude is None:
        raise BenchmarkRefusal(f"claude executable {args.claude!r} not found on PATH")
    rates = None
    if args.rates is not None:
        rates = arms_common.meter().phase_report.load_rate_rows(args.rates)
        if not rates:
            raise BenchmarkRefusal("--rates names no readable rate card")
    common = dict(claude=claude, model=args.model, permission_mode=args.permission_mode,
                  belt_usd=args.belt_usd, rates=rates, scoring_timeout=args.scoring_timeout_seconds)
    built = {"plain": lambda: PlainArm(**common),
             "sigma": lambda: SigmaArm(repo=args.sigma_repo, commit=args.sigma_commit, **common),
             "matched": lambda: MatchedArm(max_attempts=args.max_attempts, **common)}
    return [built[name]() for name in names]


def main(argv=None):
    args = parse_args(argv)
    if args.command == "run" and bool(args.fake_arm) == bool(args.arm):
        print("bench.py: REFUSED: choose exactly one of --fake-arm or --arm", file=sys.stderr)
        return 2
    try:
        arms = [FakeArm()] if args.fake_arm else _build_live_arms(args)
        report = run_benchmark(args.manifest, arms, max_usd=args.max_usd,
                               hidden_root=args.hidden_root, results_path=args.results,
                               scratch_root=args.scratch_root, repeats=args.repeats,
                               deadline_seconds=args.deadline_seconds,
                               background=args.background,
                               isolation_launcher=args.isolation_launcher,
                               scoring_timeout=args.scoring_timeout_seconds, dry_run=args.dry_run)
    except (BenchmarkRefusal, arms_common.ArmRefusal) as exc:
        print(f"bench.py: REFUSED: {exc}", file=sys.stderr)
        return 2
    except BenchmarkSignal as exc:
        print(f"bench.py: stopped by {exc}; paid rows kept in the results file", file=sys.stderr)
        return 143
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

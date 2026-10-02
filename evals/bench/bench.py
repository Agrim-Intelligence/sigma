#!/usr/bin/env python3
"""Run a hermetic, metered benchmark smoke test without exposing hidden tests.

The benchmark runner itself never starts a live model.  A later arm package supplies
an ``Arm`` implementation; the built-in ``--fake-arm`` exists solely for the
two-task, zero-spend smoke control documented in ``evals/README.md``.
"""
import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
TASK_SCHEMA = "sigma.benchmark-tasks/v1"
RESULT_SCHEMA = "sigma.benchmark-results/v1"


class BenchmarkRefusal(ValueError):
    """A benchmark precondition was not met; no arm has been started."""


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


def _validate_inputs(tasks, max_usd, hidden_root, results_path, background, environment):
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
    results_path = Path(results_path).resolve()
    if _within(results_path, hidden_root):
        raise BenchmarkRefusal("results path must be outside hidden root")
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


def _run_arm(arm, workdir, prompt, environment, deadline):
    """Run only the in-tree trusted smoke fixture.

    An arbitrary callback can read the runner's filesystem; a pass-through
    launcher likewise cannot prove containment.  No live arm is therefore
    executable until a separately reviewed, enforceable isolation protocol
    exists.
    """
    if type(arm) is FakeArm:
        return arm.run(workdir, prompt, environment, deadline)
    raise BenchmarkRefusal(
        "live arms are refused until Sigma has an enforceable isolation boundary")


def _command_passed(argv, cwd):
    completed = subprocess.run(list(argv), cwd=str(cwd), capture_output=True, text=True, check=False)
    return completed.returncode == 0


def _hidden_passed(hidden_root, task, final_tree, evaluator_root):
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
    return _command_passed(command, scored_tree)


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


def run_benchmark(manifest_path, arms, *, max_usd, hidden_root, results_path, scratch_root,
                  repeats=1, deadline_seconds=3600, background=False, environment=None,
                  isolation_launcher=None):
    """Run injected arms on copied tasks and return/write content-free result rows."""
    environment = dict(os.environ if environment is None else environment)
    tasks = _load_tasks(manifest_path)
    if (not arms or any(not _safe_component(getattr(arm, "name", "")) for arm in arms) or
            any(not isinstance(arm, Arm) for arm in arms)):
        raise BenchmarkRefusal("at least one safely named arm is required")
    if not isinstance(repeats, int) or repeats < 1:
        raise BenchmarkRefusal("repeats must be a positive integer")
    hidden_root, results_path = _validate_inputs(tasks, max_usd, hidden_root, results_path,
                                                 background, environment)
    scratch_root = Path(scratch_root).resolve()
    if _within(scratch_root, hidden_root):
        raise BenchmarkRefusal("scratch root must be outside hidden root")
    isolation_launcher = _validate_isolation_launcher(isolation_launcher, tasks, hidden_root,
                                                      scratch_root)
    scratch_root.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    rows = []
    for task in tasks:
        for repeat in range(1, repeats + 1):
            for arm in arms:
                remaining_usd = max_usd - spent
                if remaining_usd <= 0:
                    rows.append({"task": task.identifier, "arm": arm.name, "repeat": repeat,
                                 "status": "skipped", "reason": "spend ceiling reached",
                                 "visible_passed": None, "hidden_passed": None,
                                 "cost_usd": None, "wall_seconds": None, "interventions": None})
                    continue
                run_root = Path(tempfile.mkdtemp(prefix=f"{task.identifier}-{arm.name}-",
                                                  dir=scratch_root))
                workdir = run_root / "worktree"
                shutil.copytree(task.source, workdir, symlinks=True)
                started = time.monotonic()
                result = _run_arm(arm, workdir, task.prompt,
                                  _safe_arm_environment(environment, hidden_root, arm, remaining_usd,
                                                        isolation_launcher),
                                  time.monotonic() + deadline_seconds)
                if not isinstance(result, ArmRun):
                    raise BenchmarkRefusal(f"arm {arm.name!r} returned no ArmRun")
                if (not isinstance(result.interventions, int) or isinstance(result.interventions, bool)
                        or result.interventions < 0):
                    raise BenchmarkRefusal(f"arm {arm.name!r} reported invalid interventions")
                if str(hidden_root) in result.reason:
                    raise BenchmarkRefusal(f"arm {arm.name!r} disclosed hidden root in its result")
                elapsed = round(time.monotonic() - started, 6)
                if result.cost_usd is not None:
                    if (not isinstance(result.cost_usd, (int, float)) or
                            not math.isfinite(result.cost_usd) or result.cost_usd < 0):
                        raise BenchmarkRefusal(f"arm {arm.name!r} reported invalid cost")
                    if result.cost_usd > remaining_usd:
                        raise BenchmarkRefusal(
                            f"arm {arm.name!r} reported cost above remaining spend ceiling")
                visible = _command_passed(task.visible_command, workdir) if result.completed else None
                hidden = (_hidden_passed(hidden_root, task, workdir, run_root / "scoring")
                          if result.completed else None)
                if result.cost_usd is not None:
                    spent += result.cost_usd
                rows.append({"task": task.identifier, "arm": arm.name, "repeat": repeat,
                             "status": "completed" if result.completed else "failed",
                             "reason": result.reason, "visible_passed": visible,
                             "hidden_passed": hidden, "cost_usd": result.cost_usd,
                             "wall_seconds": elapsed, "interventions": result.interventions})
    report = {"schema": RESULT_SCHEMA, "max_usd": max_usd, "runs": rows}
    _write_json_atomic(results_path, report)
    return report


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a zero-spend injected-arm smoke test")
    run.add_argument("--manifest", required=True, type=Path)
    run.add_argument("--hidden-root", required=True, type=Path)
    run.add_argument("--results", required=True, type=Path)
    run.add_argument("--scratch-root", type=Path,
                     default=Path.home() / ".sigma-ops" / "bench" / "scratch")
    run.add_argument("--max-usd", type=float)
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--deadline-seconds", type=int, default=3600)
    run.add_argument("--isolation-launcher", type=Path,
                     help="absolute operator-owned sandbox/privilege-separation launcher")
    run.add_argument("--background", action="store_true")
    run.add_argument("--fake-arm", action="store_true",
                     help="use only the built-in zero-spend smoke arm")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.command == "run" and not args.fake_arm:
        print("bench.py: REFUSED: no live arm is shipped; use --fake-arm for the smoke control",
              file=sys.stderr)
        return 2
    try:
        report = run_benchmark(args.manifest, [FakeArm()], max_usd=args.max_usd,
                               hidden_root=args.hidden_root, results_path=args.results,
                               scratch_root=args.scratch_root, repeats=args.repeats,
                               deadline_seconds=args.deadline_seconds,
                               background=args.background,
                               isolation_launcher=args.isolation_launcher)
    except BenchmarkRefusal as exc:
        print(f"bench.py: REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

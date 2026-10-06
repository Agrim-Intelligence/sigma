#!/usr/bin/env python3
"""Run a hermetic, metered benchmark smoke test without exposing hidden tests.

Three live arms ship (``SigmaArm`` A1, ``PlainArm`` A2, ``MatchedArm`` A3, see
``docs/bench/preregistration.md``).  They start ``claude -p`` only through the operator's
isolation launcher, and only the exact in-tree classes are admitted: an arbitrary ``Arm``
subclass is refused.  The built-in ``--fake-arm`` is the zero-spend smoke control documented
in ``evals/README.md``.

The benchmark runs on the owner's subscription login (2026-10-06), so the measured fact is TOKENS
read from the host's own usage records and the ceiling is a token ceiling; dollars are indicative
and never a bill.  The run is BATCHED: the results file is the resume cursor (rewritten after every
pair, listing every planned pair as ``completed``, ``failed`` or ``not-run``), ``--batch-pairs N``
stops cleanly after N pairs, ``--resume`` continues without re-running a completed pair, and a
host rate limit stops the run with the throttled pair recorded as not-run (never as a failure).
"""
import argparse
from dataclasses import dataclass
import hashlib
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

try:
    import fcntl
except ImportError:  # not POSIX: live arms refuse such a host anyway
    fcntl = None

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from arms import common as arms_common  # noqa: E402  (path set up just above)
from arms import matched as arms_matched  # noqa: E402
from arms import sigma as arms_sigma  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
TASK_SCHEMA = "sigma.benchmark-tasks/v1"
RESULT_SCHEMA = "sigma.benchmark-results/v2"
SUMMARY_SCHEMA = "sigma.benchmark-summary/v1"
COST_BASIS = ("indicative dollars: tokens priced at published list rates; not a bill "
              "(a subscription is not charged per token)")
COMPLETED, FAILED, NOT_RUN = "completed", "failed", "not-run"
EXIT_RATE_LIMIT = 75  # EX_TEMPFAIL: the host said no; resume after the reset
EXIT_CEILING = 76     # the token ceiling stopped the run; raising it is a recorded decision
EXIT_SUSPECT = 77     # a run exited non-zero with no recorded cause: not scored; a person looks, then --resume
EXIT_INCOMPLETE = 78  # the run ended with not-run pairs that no other stop explains
DEFAULT_BATCH_PAIRS = 3  # one task's three arms


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
    cost_usd: Optional[float]  # indicative dollars, or None
    interventions: int = 0
    reason: str = ""
    completed: bool = True
    tokens: Optional[int] = None  # measured from the host's usage records; None = unreadable
    tokens_detail: Optional[dict] = None
    rate_limited: Optional[dict] = None  # the host throttled the run: the pair did not complete
    suspect_exit: Optional[int] = None  # non-zero exit with work done and no record: not scored, run stops


class Arm:
    """Legacy in-process arm seam; untrusted implementations are refused.

    A Python callback executes with the benchmark runner's own authority, so it
    cannot be an isolation boundary for hidden tests.  Only the exact in-tree
    classes in ``TRUSTED_ARMS`` run (``FakeArm`` and the three live arms, which
    start ``claude`` only through the operator launcher); any other subclass is refused.
    """
    name = "unnamed"

    def run(self, workdir, prompt, env, deadline):  # pragma: no cover - interface only
        raise NotImplementedError


class FakeArm(Arm):
    """A zero-spend arm used only by the documented hermetic smoke command."""
    name = "fake"

    def __init__(self, tokens=None):
        self.tokens = list(tokens or [])
        self.allowances = []

    def run(self, workdir, prompt, env, deadline):
        del prompt, deadline
        self.allowances.append(env["SIGMA_BENCH_MAX_TOKENS"])
        (Path(workdir) / "arm-complete").write_text("fake smoke\n", encoding="utf-8")
        used = self.tokens.pop(0) if self.tokens else 0
        return ArmRun(cost_usd=0.0, tokens=used, reason="built-in hermetic smoke arm")


class LiveArm(Arm):
    """Common base of the in-tree live arms: a pinned model and permission mode, a per-run belt, metering.

    The arm is bound per run (``bind``) with the task's visible command, A1's token spend for that task
    and the plugin-directory guard, because ``Arm.run`` carries none of them.  Admission is by exact class.
    The belt is the host's own client-side ``--max-budget-usd`` estimate: a runaway guard, indicative under a
    subscription.  The benchmark's ceiling is the token ceiling, enforced between runs.
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
        self.match_tokens = None
        self.guard = lambda: None

    def bind(self, visible_command, match_tokens, guard):
        self.visible_command, self.match_tokens, self.guard = visible_command, match_tokens, guard

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
            budget_usd=self.belt_usd, plugin_dir=plugin_dir,
            workdir=workdir, prompt=prompt, environment=env, profile_dir=Path(workdir).parent / "profile",
            deadline=deadline, rates=self.rates)
        self.spend.add(attempt.cost, attempt.priced_part, attempt.tokens, attempt.detail)
        reason = "claude exit %s" % attempt.returncode
        if attempt.timed_out:
            reason = "claude killed at the wall-clock limit"
        if attempt.rate_limit is not None and attempt.throttled is None:
            reason += " (a rate-limit record was seen on a run that exited 0; scored as usual)"
        return ArmRun(cost_usd=attempt.cost, tokens=attempt.tokens, tokens_detail=attempt.detail,
                      rate_limited=attempt.throttled, suspect_exit=attempt.suspect, reason=reason)


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
    """A3: the plain agent retried in fresh workdirs until visible tests pass or token spend reaches A1's."""
    name = "matched"

    def __init__(self, *, max_attempts=10, **kwargs):
        super().__init__(**kwargs)
        self.max_attempts = max_attempts

    def provenance(self):
        return dict(super().provenance(), max_attempts=self.max_attempts)

    def facts(self):
        return dict(super().facts(), max_attempts=self.max_attempts,
                    retry="fresh workdir and profile per attempt, until visible tests pass or token spend "
                          "reaches A1's token spend for the task")

    def run(self, workdir, prompt, env, deadline):
        self.spend = arms_common.Spend()
        launcher = env["SIGMA_BENCH_ISOLATION_LAUNCHER"]

        def attempt(tree, profile_dir):
            return arms_common.run_claude(
                launcher=launcher, claude=self.claude, model=self.model,
                permission_mode=self.permission_mode, budget_usd=self.belt_usd,
                plugin_dir=None, workdir=tree, prompt=prompt, environment=env, profile_dir=profile_dir,
                deadline=deadline, rates=self.rates)

        def visible(tree):
            scoring_env = arms_common.isolated_env(env, Path(tree).parent / "profile")
            return arms_common.command_passed(
                launcher, self.visible_command, cwd=tree, env=scoring_env,
                timeout=min(self.scoring_timeout, max(0.0, deadline - time.monotonic())))

        done = arms_matched.run_matched(
            workdir, Path(workdir).parent / "attempts", attempt=attempt, visible=visible, guard=self.guard,
            match_tokens=self.match_tokens, remaining_tokens=int(env["SIGMA_BENCH_MAX_TOKENS"]),
            max_attempts=self.max_attempts, deadline=deadline, spend=self.spend)
        return ArmRun(cost_usd=done["cost_usd"], tokens=done["tokens"], tokens_detail=done["detail"],
                      reason=done["reason"], rate_limited=done["rate_limit"], suspect_exit=done["suspect_exit"])


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


def _validate_inputs(tasks, max_tokens, hidden_root, results_path, background, environment, scratch_root):
    if max_tokens is None or isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise BenchmarkRefusal("--max-tokens must be a positive explicit token ceiling")
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


def _safe_arm_environment(environment, hidden_root, arm, remaining_tokens, isolation_launcher):
    root_text = str(hidden_root)
    env = {key: value for key, value in environment.items()
           if root_text not in str(value) and key != "SIGMA_BENCH_HIDDEN_ROOT"}
    env["SIGMA_BENCH_ARM_NAME"] = arm.name
    # What is left of the token ceiling, for arms that loop (A3).  Keeping it in the injected environment
    # preserves the fixed Arm.run seam; the host has no per-run token flag, so it is checked between runs.
    env["SIGMA_BENCH_MAX_TOKENS"] = str(remaining_tokens)
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
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)
    try:  # best effort: make the new cursor survive a power loss, not only a process crash
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        pass


def _validate_isolation_launcher(launcher, tasks, hidden_root, scratch_root):
    """Validate only the operator-controlled launcher *location*, never its semantics.

    Process/filesystem isolation cannot be inferred from an arbitrary executable's
    bytes.  An operator must therefore provide and own the external boundary;
    this runner fails closed until it has an absolute executable outside every
    repository, hidden, task, and scratch tree.  This location check is not a
    proof of containment: the live arms run through the launcher, and what it
    contains is the operator's to guarantee.
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


class _ResultsLock:
    """An exclusive, non-blocking lock on ``<results>.lock`` for the whole invocation.

    Two invocations reading the same cursor would both run a pair and the last writer would drop the
    other's paid rows.  ``flock`` is released by the kernel when the process dies, so a crash never
    leaves a stale lock.  Where ``fcntl`` is missing (not POSIX) no live arm can run, so nothing is lost.
    """

    def __init__(self, results_path):
        self.path = results_path.with_name(results_path.name + ".lock")
        self.handle = None

    def __enter__(self):
        if fcntl is None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise BenchmarkRefusal(
                "another benchmark invocation is already running on this results file (%s is locked); "
                "wait for it to finish" % self.path.name) from exc
        return self

    def __exit__(self, *_exc):
        if self.handle is not None:
            self.handle.close()  # closing releases the flock
            self.handle = None


def _pair_key(task, arm, repeat):
    return (task, arm, repeat)


def _digest_of(parts):
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _conditions(manifest_path, tasks, arms, repeats, hidden_root, deadline_seconds, scoring_timeout, provenance):
    """Everything that must be identical across batches for the rows to be comparable."""
    manifest_sha = hashlib.sha256(Path(manifest_path).resolve().read_bytes()).hexdigest()
    bundles = _digest_of(["%s=%s" % (task.hidden_bundle, arms_common.tree_digest(hidden_root / task.hidden_bundle))
                          for task in tasks])
    conditions = {"manifest_sha256": manifest_sha, "arms": [arm.name for arm in arms], "repeats": repeats,
                  "deadline_seconds": deadline_seconds, "scoring_timeout_seconds": scoring_timeout,
                  "hidden_bundles_sha256": bundles}
    conditions.update(provenance)
    return conditions


class _Ledger:
    """The results file as a resume cursor.

    It always lists every planned pair, in order: ``completed`` and ``failed`` rows are final and never
    re-run; every other pair is ``not-run`` with the reason.  A pair that did not complete is never a
    failure.  The file is rewritten atomically (and flushed to disk) after every pair and, before a pair
    starts, with an ``in_flight`` marker, so a SIGKILL or power loss costs at most the one pair in flight
    and the next ``--resume`` counts it as an unknown-token run instead of hiding it.
    """

    def __init__(self, path, planned, max_tokens):
        self.path, self.planned, self.max_tokens = path, planned, max_tokens
        self.rows, self.reasons = {}, {}
        self.tokens_unscored, self.unknown_runs, self.invocations = 0, 0, 1
        self.ceiling_changes = []
        self.stop, self.in_flight = None, None
        self.conditions, self.ready = None, False

    def tokens_spent(self):
        return sum(row["tokens"] or 0 for row in self.rows.values()) + self.tokens_unscored

    def set_not_run(self, key, reason):
        self.reasons[key] = reason

    def _not_run(self, key):
        task, arm, repeat = key
        return {"task": task, "arm": arm, "repeat": repeat, "status": NOT_RUN,
                "reason": self.reasons.get(key) or (self.stop or {}).get("reason") or "not yet run",
                "visible_passed": None, "hidden_passed": None, "tokens": None, "tokens_detail": None,
                "cost_usd": None, "wall_seconds": None, "interventions": None}

    def report(self, aborted=None):
        runs = [self.rows.get(key) or self._not_run(key) for key in self.planned]
        report = {"schema": RESULT_SCHEMA, "max_tokens": self.max_tokens, "runs": runs,
                  "complete": all(row["status"] in (COMPLETED, FAILED) for row in runs),
                  "stop": self.stop, "tokens_spent": self.tokens_spent(),
                  "tokens_unscored": self.tokens_unscored, "unknown_runs": self.unknown_runs,
                  "invocations": self.invocations, "cost_basis": COST_BASIS}
        if self.unknown_runs:
            report["tokens_note"] = ("tokens_spent is a lower bound: %d pair(s) died in flight and left no usage "
                                     "record that was counted" % self.unknown_runs)
        if self.ceiling_changes:
            report["ceiling_changes"] = self.ceiling_changes
        if self.in_flight:
            report["in_flight"] = self.in_flight
        if self.conditions:
            report["conditions"] = self.conditions
            report["provenance"] = {k: v for k, v in self.conditions.items()
                                    if k in ("model", "permission_mode", "belt_usd", "sigma_commit",
                                             "claude_version")}
        if aborted:
            report["aborted"] = aborted
        return report

    def write(self, aborted=None):
        report = self.report(aborted)
        _write_json_atomic(self.path, report)
        return report


def _unknown_reason(exc):
    if isinstance(exc, (BenchmarkRefusal, arms_common.ArmRefusal)):
        return str(exc)
    if isinstance(exc, BenchmarkSignal):
        return "signal " + str(exc)
    return type(exc).__name__


def _load_cursor(results_path):
    try:
        raw = json.loads(results_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BenchmarkRefusal(f"the results file cannot be read as a cursor: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != RESULT_SCHEMA or not isinstance(raw.get("runs"), list):
        raise BenchmarkRefusal(f"--resume needs a {RESULT_SCHEMA} results file")
    return raw


def _restore(ledger, raw, planned_keys, conditions, max_tokens):
    """Adopt a cursor's final rows and carried counters, refusing anything that is not comparable."""
    stored = raw.get("conditions") if isinstance(raw.get("conditions"), dict) else {}
    differing = sorted(name for name in set(stored) | set(conditions) if stored.get(name) != conditions.get(name))
    if differing:
        lever = ""
        if "claude_version" in differing:
            lever = (" (the host updated itself between batches: pin the version, for example with "
                     "DISABLE_AUTOUPDATER in the launcher's extra_env, or start a new results file)")
        raise BenchmarkRefusal("--resume refused: the conditions changed since the first batch (%s); every pair "
                               "must run under identical conditions%s" % (", ".join(differing), lever))
    previous = raw.get("max_tokens")
    if not isinstance(previous, int) or isinstance(previous, bool):
        raise BenchmarkRefusal("--resume refused: the results file records no token ceiling")
    if max_tokens < previous:
        raise BenchmarkRefusal("--resume refused: the token ceiling may be raised on resume, never lowered "
                               f"(recorded {previous}, given {max_tokens})")
    ledger.ceiling_changes = list(raw.get("ceiling_changes") or [])
    if max_tokens > previous:
        ledger.ceiling_changes.append({"from": previous, "to": max_tokens})
    seen = set()
    for row in raw["runs"]:
        key = _pair_key(row.get("task"), row.get("arm"), row.get("repeat")) if isinstance(row, dict) else None
        if key not in planned_keys or key in seen:
            raise BenchmarkRefusal("--resume refused: the results file does not match this run's planned pairs")
        seen.add(key)
        if row.get("status") in (COMPLETED, FAILED):
            tokens = row.get("tokens")
            if isinstance(tokens, bool) or not (tokens is None or isinstance(tokens, int)) or (tokens or 0) < 0:
                raise BenchmarkRefusal("--resume refused: a results row has an invalid token count")
            ledger.rows[key] = row
    for name in ("tokens_unscored", "unknown_runs", "invocations"):
        value = raw.get(name, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise BenchmarkRefusal(f"--resume refused: the results file has an invalid {name}")
    ledger.tokens_unscored = raw.get("tokens_unscored", 0)
    ledger.unknown_runs = raw.get("unknown_runs", 0)
    ledger.invocations = raw.get("invocations", 0) + 1
    if raw.get("in_flight"):
        ledger.unknown_runs += 1  # a pair died between paying and recording: counted, re-run, never failed
    aborted = raw.get("aborted")
    if isinstance(aborted, dict) and isinstance(aborted.get("tokens_spent"), int):
        ledger.tokens_unscored += aborted["tokens_spent"]


def _unwind_on_signals():
    """Turn SIGTERM and SIGHUP into an exception so the process-group kills and the aborted write run."""
    if threading.current_thread() is not threading.main_thread():
        return {}

    def handler(signum, _frame):
        raise BenchmarkSignal(signal.Signals(signum).name)

    return {number: signal.signal(number, handler) for number in (signal.SIGTERM, signal.SIGHUP)}


def run_benchmark(manifest_path, arms, *, max_tokens, hidden_root, results_path, scratch_root,
                  repeats=1, deadline_seconds=3600, background=False, environment=None,
                  isolation_launcher=None, scoring_timeout=600, dry_run=False, resume=False,
                  batch_pairs=None):
    """Run injected arms on copied tasks and return/write content-free result rows.

    Every run lives in its own directory under ``scratch_root``, scored immediately through the
    operator launcher and then deleted, so a later arm can read neither an earlier solution nor the
    hidden bundle.  The ceiling is TOKENS (the host's own usage records), checked between runs: the
    host has no per-run token cap, so a run can overshoot and the overshoot is counted.

    The results file is a resume cursor: see ``_Ledger``.  ``batch_pairs`` stops cleanly after that many
    pairs; ``resume`` continues from the file without re-running a completed or failed pair.  A run whose
    transcripts show a host rate limit is recorded not-run, never scored, and stops the benchmark.  The
    benchmark also stops (keeping the paid rows in a report with an ``aborted`` object) when the operator's
    real plugin directories change or a run leaves no readable usage records.
    """
    environment = dict(os.environ if environment is None else environment)
    tasks = _load_tasks(manifest_path)
    if (not arms or any(not _safe_component(getattr(arm, "name", "")) for arm in arms) or
            any(not isinstance(arm, Arm) for arm in arms)):
        raise BenchmarkRefusal("at least one safely named arm is required")
    if not isinstance(repeats, int) or repeats < 1:
        raise BenchmarkRefusal("repeats must be a positive integer")
    if batch_pairs is not None and (isinstance(batch_pairs, bool) or not isinstance(batch_pairs, int)
                                    or batch_pairs < 1):
        raise BenchmarkRefusal("--batch-pairs must be a positive integer")
    _live_arm_sanity(arms, repeats)
    hidden_root, results_path = _validate_inputs(tasks, max_tokens, hidden_root, results_path,
                                                 background, environment, scratch_root)
    scratch_root = Path(scratch_root).resolve()
    if _within(scratch_root, hidden_root):
        raise BenchmarkRefusal("scratch root must be outside hidden root")
    isolation_launcher = _validate_isolation_launcher(isolation_launcher, tasks, hidden_root,
                                                      scratch_root)
    if scratch_root.is_dir() and any(scratch_root.iterdir()):
        raise BenchmarkRefusal(
            "scratch root must be empty: leftovers of an earlier run could be read by an arm "
            "(empty it deliberately; a crashed run leaves its run directory here)")
    if any(isinstance(arm, LiveArm) for arm in arms):
        arms_common.require_posix()
    planned = [_pair_key(task.identifier, arm.name, repeat)
               for task in tasks for repeat in range(1, repeats + 1) for arm in arms]
    if dry_run:
        return {"schema": RESULT_SCHEMA, "dry_run": True, "max_tokens": max_tokens, "pairs": len(planned),
                "batch_pairs": batch_pairs, "resume": bool(resume), "cost_basis": COST_BASIS,
                "arms": [arm.facts() if hasattr(arm, "facts") else {"arm": arm.name} for arm in arms]}
    with _ResultsLock(results_path):
        return _run_locked(manifest_path, tasks, arms, planned, max_tokens, hidden_root, results_path,
                           scratch_root, repeats, deadline_seconds, environment, isolation_launcher,
                           scoring_timeout, resume, batch_pairs)


def _run_locked(manifest_path, tasks, arms, planned, max_tokens, hidden_root, results_path, scratch_root,
                repeats, deadline_seconds, environment, isolation_launcher, scoring_timeout, resume,
                batch_pairs):
    cursor = None
    if resume:
        if not results_path.exists():
            raise BenchmarkRefusal("--resume needs an existing results file (the cursor); start the first "
                                   "batch without --resume")
        cursor = _load_cursor(results_path)
    elif results_path.exists():
        raise BenchmarkRefusal("the results file already exists: it is the cursor of paid pairs. Continue it "
                               "with --resume, or move it away deliberately to start over")
    scratch_root.mkdir(parents=True, exist_ok=True)
    # The baseline is taken before ANY claude invocation, so nothing the harness starts can be absorbed in it.
    baseline = arms_common.plugin_digest(environment)

    def guard():
        if arms_common.plugin_digest(environment) != baseline:
            raise BenchmarkRefusal("real plugin directory changed during the benchmark; aborting")

    ledger = _Ledger(results_path, planned, max_tokens)
    previous_handlers = _unwind_on_signals()
    live = [arm for arm in arms if isinstance(arm, LiveArm)]
    current = {}
    executed = 0
    a1_tokens = {}
    try:
        provenance = {}
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
                if isinstance(arm, MatchedArm):
                    provenance["max_attempts"] = arm.max_attempts
        ledger.conditions = _conditions(manifest_path, tasks, arms, repeats, hidden_root, deadline_seconds,
                                        scoring_timeout, provenance)
        if cursor is not None:
            _restore(ledger, cursor, set(planned), ledger.conditions, max_tokens)
        ledger.ready = True
        for key, row in ledger.rows.items():
            if key[1] == SigmaArm.name and isinstance(row.get("tokens"), int) and row["tokens"] > 0:
                a1_tokens[key[0]] = row["tokens"]
        by_name = {arm.name: arm for arm in arms}
        by_task = {task.identifier: task for task in tasks}
        for key in planned:
            if key in ledger.rows:
                continue
            task, arm, repeat = by_task[key[0]], by_name[key[1]], key[2]
            guard()
            if batch_pairs is not None and executed >= batch_pairs:
                ledger.stop = {"kind": "batch", "reason": "batch of %d pair(s) done; run again with --resume "
                                                          "to continue" % batch_pairs}
                break
            remaining_tokens = max_tokens - ledger.tokens_spent()
            if remaining_tokens <= 0:
                ledger.stop = {"kind": "token-ceiling",
                               "reason": "token ceiling reached (%d tokens spent); not-run pairs stay not-run "
                                         "until a recorded decision raises the ceiling" % ledger.tokens_spent()}
                break
            if isinstance(arm, MatchedArm) and task.identifier not in a1_tokens:
                ledger.set_not_run(key, "no A1 token count recorded for this task (its cap is A1's token spend)")
                continue
            if isinstance(arm, LiveArm):
                arm.spend = arms_common.Spend()  # never report an earlier pair's spend for this one
            current = {"task": task.identifier, "arm": arm.name, "repeat": repeat, "spent": 0.0,
                       "arm_object": arm}
            ledger.in_flight = {"task": task.identifier, "arm": arm.name, "repeat": repeat}
            ledger.write()
            run_root = Path(tempfile.mkdtemp(prefix=f"{task.identifier}-{arm.name}-", dir=scratch_root))
            try:
                workdir = run_root / "worktree"
                shutil.copytree(task.source, workdir, symlinks=True)
                started = time.monotonic()
                arm_env = _safe_arm_environment(environment, hidden_root, arm, remaining_tokens,
                                                isolation_launcher)
                result = _run_arm(arm, workdir, task.prompt, arm_env, time.monotonic() + deadline_seconds,
                                  (task.visible_command, a1_tokens.get(task.identifier), guard))
                current["spent"] = getattr(getattr(arm, "spend", None), "usd", 0.0)
                if not isinstance(result, ArmRun):
                    raise BenchmarkRefusal(f"arm {arm.name!r} returned no ArmRun")
                if (not isinstance(result.interventions, int) or isinstance(result.interventions, bool)
                        or result.interventions < 0):
                    raise BenchmarkRefusal(f"arm {arm.name!r} reported invalid interventions")
                if str(hidden_root) in result.reason:
                    raise BenchmarkRefusal(f"arm {arm.name!r} disclosed hidden root in its result")
                elapsed = round(time.monotonic() - started, 6)
                if result.rate_limited is not None:
                    limit = result.rate_limited
                    burned = result.tokens or 0
                    ledger.tokens_unscored += burned
                    reason = ("host rate limit (%s, resets_at=%s): the run was discarded and never scored; its "
                              "%d token(s) are counted as unscored. %s" % (
                                  limit.get("limit_type"), limit.get("resets_at"), burned, result.reason))
                    ledger.set_not_run(key, reason)
                    ledger.stop = {"kind": "rate-limit", "reason": reason,
                                   "resets_at": limit.get("resets_at"), "limit_type": limit.get("limit_type")}
                    ledger.in_flight = None
                    ledger.write()
                    current = {}
                    break
                tokens = result.tokens
                if type(arm) is not FakeArm and not tokens:
                    raise BenchmarkRefusal(
                        f"arm {arm.name!r} run left no readable usage records ({result.reason}): the host's "
                        "transcripts cannot be read or authentication is missing; stopping")
                tokens = tokens or 0
                if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
                    raise BenchmarkRefusal(f"arm {arm.name!r} reported invalid tokens")
                if result.suspect_exit is not None:
                    ledger.tokens_unscored += tokens
                    reason = ("claude exited %s with no rate-limit record to explain it: the run was discarded "
                              "and never scored (it could be an infrastructure fault, not the arm failing); its %d "
                              "token(s) are counted as unscored. Find the cause, then run again with --resume. %s"
                              % (result.suspect_exit, tokens, result.reason))
                    ledger.set_not_run(key, reason)
                    ledger.stop = {"kind": "suspect-exit", "reason": reason, "exit_status": result.suspect_exit}
                    ledger.in_flight = None
                    ledger.write()
                    current = {}
                    break
                if result.cost_usd is not None and (not isinstance(result.cost_usd, (int, float)) or
                                                    not math.isfinite(result.cost_usd) or result.cost_usd < 0):
                    raise BenchmarkRefusal(f"arm {arm.name!r} reported invalid cost")
                current["tokens"] = tokens
                scoring_env = arms_common.isolated_env(arm_env, run_root / "scoring-profile")
                visible = (_command_passed(task.visible_command, workdir, scoring_env,
                                           isolation_launcher, scoring_timeout)
                           if result.completed else None)
                hidden = (_hidden_passed(hidden_root, task, workdir, run_root / "scoring",
                                         scoring_env, isolation_launcher, scoring_timeout)
                          if result.completed else None)
                if type(arm) is SigmaArm and tokens > 0:
                    a1_tokens[task.identifier] = tokens
                ledger.rows[key] = {"task": task.identifier, "arm": arm.name, "repeat": repeat,
                                    "status": COMPLETED if result.completed else FAILED,
                                    "reason": result.reason, "visible_passed": visible,
                                    "hidden_passed": hidden, "tokens": tokens,
                                    "tokens_detail": result.tokens_detail, "cost_usd": result.cost_usd,
                                    "wall_seconds": elapsed, "interventions": result.interventions}
                ledger.in_flight = None
                current = {}  # before the write: a signal in between must not count this pair twice
                ledger.write()
                executed += 1
            finally:
                _remove_run_directory(run_root)
        guard()
        ledger.in_flight = None
        if ledger.stop is None and any(key not in ledger.rows for key in planned):
            ledger.stop = {"kind": "incomplete", "reason": "pair(s) were not run and nothing else explains it: %s"
                           % "; ".join(sorted(set(ledger.reasons.values()))[:3])}
    except BaseException as exc:
        if ledger.ready and (current or ledger.rows or ledger.tokens_unscored):
            spend = getattr(current.get("arm_object"), "spend", None)
            ledger.in_flight = None
            if ledger.stop is None:
                ledger.stop = {"kind": "aborted", "reason": "the benchmark aborted: " + _unknown_reason(exc)}
            try:
                ledger.write(aborted={
                    "reason": _unknown_reason(exc), "task": current.get("task"), "arm": current.get("arm"),
                    "repeat": current.get("repeat"),
                    "tokens_spent": int(getattr(spend, "tokens", 0) or 0),
                    "cost_usd_spent": round(max(current.get("spent", 0.0), getattr(spend, "usd", 0.0)), 6)})
            except OSError:
                pass
        raise
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler if handler is not None else signal.SIG_DFL)
    return ledger.write()


def summarize(results_path):
    """Relative outcome and relative token cost per arm from a COMPLETE results file; refuses otherwise.

    No dollars-per-task figure is produced: dollars under a subscription are indicative only.
    """
    path = Path(results_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BenchmarkRefusal(f"the results file cannot be read: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != RESULT_SCHEMA or not isinstance(raw.get("runs"), list):
        raise BenchmarkRefusal(f"summarize needs a {RESULT_SCHEMA} results file")
    pending = [row for row in raw["runs"] if row.get("status") not in (COMPLETED, FAILED)]
    if raw.get("complete") is not True or pending:
        raise BenchmarkRefusal("the results file is not complete: %d pair(s) are not-run; finish the run with "
                               "--resume before analysing (a partial file is never evidence)" % len(pending))
    arms, passes = {}, {}
    if any(not isinstance(row, dict) or not all(k in row for k in ("arm", "task", "repeat")) for row in raw["runs"]):
        raise BenchmarkRefusal("the results file has a malformed row (needs arm, task and repeat)")
    for row in raw["runs"]:
        entry = arms.setdefault(row["arm"], {"pairs": 0, "hidden_passes": 0, "tokens_total": 0,
                                             "tokens_detail": dict.fromkeys(arms_common.KIND_NAMES, 0)})
        entry["pairs"] += 1
        entry["hidden_passes"] += 1 if row.get("hidden_passed") is True else 0
        entry["tokens_total"] += row.get("tokens") or 0
        for name in arms_common.KIND_NAMES:
            entry["tokens_detail"][name] += (row.get("tokens_detail") or {}).get(name, 0)
        passes[(row["arm"], row["task"], row["repeat"])] = row.get("hidden_passed") is True
    sigma_total = arms.get(SigmaArm.name, {}).get("tokens_total")
    for name, entry in arms.items():
        entry["tokens_per_pass"] = (round(entry["tokens_total"] / entry["hidden_passes"], 3)
                                    if entry["hidden_passes"] else None)
        entry["tokens_vs_sigma"] = (round(entry["tokens_total"] / sigma_total, 6) if sigma_total else None)
    paired = {}
    for name in arms:
        if name == SigmaArm.name or SigmaArm.name not in arms:
            continue
        wins = losses = ties = 0
        for (arm, task, repeat), passed in passes.items():
            if arm != name:
                continue
            sigma_passed = passes.get((SigmaArm.name, task, repeat))
            wins += 1 if sigma_passed and not passed else 0
            losses += 1 if passed and not sigma_passed else 0
            ties += 1 if sigma_passed == passed else 0
        paired[name] = {"sigma_wins": wins, "sigma_losses": losses, "ties": ties}
    caveats = {name: raw.get(name) for name in ("tokens_unscored", "unknown_runs", "ceiling_changes")
               if raw.get(name)}
    return {"schema": SUMMARY_SCHEMA, "pairs": len(raw["runs"]), "arms": arms, "paired_vs_sigma": paired,
            "caveats": caveats, "cost_basis": COST_BASIS}


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
    run.add_argument("--max-tokens", type=int,
                     help="the token ceiling (input + output + cache read + cache write, from the host's usage "
                          "records); no default; checked between runs, so one run can overshoot it")
    run.add_argument("--batch-pairs", dest="batch_pairs", type=int, default=DEFAULT_BATCH_PAIRS,
                     help="stop cleanly after this many task/arm pairs (default 3: one task's three arms)")
    run.add_argument("--resume", action="store_true",
                     help="continue the results file: completed pairs are never re-run, not-run pairs are")
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
                     help="per-run claude --max-budget-usd belt: the host's client-side estimate (indicative "
                          "under a subscription), a runaway guard, not the ceiling")
    run.add_argument("--max-attempts", dest="max_attempts", type=int, default=10,
                     help="attempt bound of the matched-spend arm")
    summary = sub.add_parser("summarize", help="relative outcome and relative token cost per arm from a "
                                               "COMPLETE results file (refuses a partial one)")
    summary.add_argument("--results", required=True, type=Path)
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


def _stop_message(report):
    stop = report.get("stop") or {}
    pending = sum(1 for row in report["runs"] if row["status"] == NOT_RUN)
    if stop.get("kind") == "batch":
        return "bench.py: batch finished; %d pair(s) are not-run; run the same command again with --resume" % pending
    if stop.get("kind") == "rate-limit":
        when = stop.get("resets_at")
        shown = ""
        if isinstance(when, int):
            shown = " (resets at %s UTC)" % time.strftime("%Y-%m-%d %H:%M", time.gmtime(when))
        return ("bench.py: stopped: the host reported a rate limit%s, epoch resets_at=%s; %d pair(s) are not-run "
                "(not failures); after the reset run the same command again with --resume"
                % (shown, when, pending))
    return "bench.py: stopped: %s; %d pair(s) are not-run" % (stop.get("reason"), pending)


def main(argv=None):
    args = parse_args(argv)
    if args.command == "summarize":
        try:
            print(json.dumps(summarize(args.results), sort_keys=True))
        except BenchmarkRefusal as exc:
            print(f"bench.py: REFUSED: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "run" and bool(args.fake_arm) == bool(args.arm):
        print("bench.py: REFUSED: choose exactly one of --fake-arm or --arm", file=sys.stderr)
        return 2
    try:
        arms = [FakeArm()] if args.fake_arm else _build_live_arms(args)
        report = run_benchmark(args.manifest, arms, max_tokens=args.max_tokens,
                               hidden_root=args.hidden_root, results_path=args.results,
                               scratch_root=args.scratch_root, repeats=args.repeats,
                               deadline_seconds=args.deadline_seconds,
                               background=args.background,
                               isolation_launcher=args.isolation_launcher,
                               scoring_timeout=args.scoring_timeout_seconds, dry_run=args.dry_run,
                               resume=args.resume, batch_pairs=args.batch_pairs)
    except (BenchmarkRefusal, arms_common.ArmRefusal) as exc:
        print(f"bench.py: REFUSED: {exc}", file=sys.stderr)
        return 2
    except BenchmarkSignal as exc:
        print(f"bench.py: stopped by {exc}; paid rows kept in the results file", file=sys.stderr)
        return 143
    print(json.dumps(report, sort_keys=True))
    kind = (report.get("stop") or {}).get("kind")
    if kind:
        print(_stop_message(report), file=sys.stderr)
    return {"rate-limit": EXIT_RATE_LIMIT, "token-ceiling": EXIT_CEILING, "suspect-exit": EXIT_SUSPECT,
            "incomplete": EXIT_INCOMPLETE}.get(kind, 0)


if __name__ == "__main__":
    raise SystemExit(main())

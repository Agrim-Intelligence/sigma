"""`work.py seal-tests` orchestration (#853, slice 4A of story #558). Library only; `work.py` dispatches here.

WHAT IT DOES, in this order, each step a refusal that spends nothing and changes nothing until the spawn:

  gate OFF (exit 0, inert) -> work on, a work record, a LINKED goal worktree -> an acceptance record (read
  from the MAIN checkout's `.sdlc`) -> a declared verify command that runs pytest (D-4) -> an approving
  plan-review bound to the CURRENT plan -> no re-seal once implement has started -> the breaker brief
  (`breaker.brief`), every criterion actionable, `breaker.no_channel` clean -> a breaker route
  (`reviewer.resolve_breaker`) -> the attempts cap -> the base commit -> [last, inside the runner, after the
  lease and the empty cwd exist and immediately before the spawn: the spend consult when unattended, then the
  attempts counter] -> the breaker runs in an empty temp cwd (`breaker_run`) -> its text is validated
  (`breaker.validate_output` plus this module's `AC<n>` name rule) -> the files are written from Python
  into the GOAL WORKTREE -> `sealed_tests.seal` proves them red on base and writes the record.

THE MAKER NEVER WRITES. Every test file below is written by this module's Python from validated text; the
breaker only prints. Text that fails validation writes nothing.

EXIT CODES: 0 sealed, or gate OFF; 2 usage; 3 a precondition refused BEFORE any spawn (nothing spent,
nothing changed); 4 refused at the reseal-after-implement, route, cap, attempts-unreadable, spend, runner or
seal clause. The first four of those 4s are also checked BEFORE any spawn (nothing spent, the counter not
advanced); for the spawn-time ones (spend, runner, seal) the attempts counter has advanced: it is written
BEFORE the spawn, so a crash cannot buy a free run.

DELIBERATE LIMITS OF THIS SLICE (slice 4A; follow-up goals carry the rest, none is claimed here):
  * Non-actionable criteria are REPORTED and the verb refuses; recording a plan defect and RETURNING the
    goal to plan is slice B. There is no `--no-seal` and no record-required check on commit/pr (slice C):
    deleting the seal record still disarms the commit and PR gates. No phase_gate step, no skill prose.
  * Per-criterion coverage is DEFERRED TO #535 (criterion ids do not exist). What is checked is the file-name
    rule (`test_acc_<s>_AC<n>_*.py`, n within the criterion count) and at least one test function per file.
    A criterion with no file naming it is NOT detected.
  * A capped goal stays capped: the attempts counter (`state/seal-attempts/<goal>.attempt`) has no reset
    lever in this slice other than an owner deleting that one file by hand; it is pruned with the goal.
    `/sigma-unpark` does NOT reset it.
  * Re-seal (a second run on a goal that already has a seal record) clears `tests/acceptance/<goal>/` first
    and rewrites it; a crash between clear and record leaves `check` reporting the files missing, and a
    re-run recovers. It is refused once the action log shows implement started (needs `action_log.enabled`;
    with the log off or unreadable that guard is BLIND (it reads as "implement not started"), and the cap
    alone bounds the rewrite).
  * The spend consult covers the FIRST breaker run only (count 0, unattended). One approval, up to 3 runs: an
    ATTENDED first run means later UNATTENDED re-seals are never consulted.
  * CEILING: concurrent `seal-tests` runs for the SAME goal are not serialized. The attempts counter is read
    in `preconditions` and written in `before_spawn` with no lock between, and the session lease in
    `breaker_run` is a machine-wide capacity admission (and absent when the guard mode is off), not a
    per-goal mutex. Two simultaneous runs can both read the same count, so the cap can be exceeded by the
    number of concurrent runs (bounded by the lease capacity when the guard is on). UNMEASURED; the lever is
    not to run two seals for one goal at once.
  * One spend approval covers up to 3 breaker runs for a goal (the cap: 2 re-seals). The single-use label is
    consumed by the first approved consult; a refusal AFTER that consult (the runner failing to start, or a
    `seal()` refusal) still burns it. The lease Hold and the empty-cwd checks come BEFORE the consult, so
    they do not.
  * The seal budget is `verify.timeout_minutes` (default 60 minutes; the red run executes the sealed tests,
    the same work verify does); the breaker budget is `review.timeout_seconds` (default 900).
  * The pytest check on the verify command is a HEURISTIC (a `pytest` token). Its false-refuse rate is
    UNMEASURED. The real-host breaker run is UNMEASURED: it has only been run through a fake CLI.
Host-agnostic: plain Python and git. No hook.
"""
import importlib.util
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess

_MODULES = {}


def _load(name):
    """Sibling module by path (same skill), cached so repeated calls do not re-exec it."""
    if name not in _MODULES:
        spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULES[name] = module
    return _MODULES[name]


#: Re-seals allowed per goal (D-6). The breaker runs at most RESEAL_CAP + 1 times.
RESEAL_CAP = 2
MAX_RUNS = RESEAL_CAP + 1
#: "Unattended" = any of these is set (a loop run, an autowatch hop, a continued session).
UNATTENDED_VARS = ("SIGMA_RUN_ID", "SIGMA_AUTOWATCH_HOP", "SIGMA_SESSION_GENERATION")
COVERAGE_NOTE = "per-criterion coverage deferred to #535"
#: The extra file-name rule, on top of `breaker`'s own `test_acc_<s>_<suffix>` grammar: the suffix starts with
#: `AC<n>`, a 1-based position in the acceptance list. breaker.py is NOT changed; this is the explicit map.
AC_SUFFIX = re.compile(r"AC([1-9][0-9]*)(?:_|$)")
NO_BYPASS = "no recorded bypass exists yet (slice C); park the goal"

_HEADING = re.compile(r"^##+\s*interface\s*$", re.I | re.M)
_NEXT_HEADING = re.compile(r"^##+\s", re.M)


class Refused(Exception):
    """A refusal with its exit code (3 before any spawn, 4 after the route) and a named clause."""

    def __init__(self, code, clause, detail):
        super().__init__(detail)
        self.code, self.clause, self.detail = code, clause, detail


def _stem(goal):
    p = pathlib.Path(str(goal))
    return p.stem if p.suffix == ".md" else str(goal)


def gate_on(config):
    """`gates.sealed_acceptance` as a bool: LOCAL config only, scalar-safe (a scalar `gates` or a scalar leaf
    never raises), the same truth table as `work.hard_plan_gate_on`. Absent is OFF."""
    gates = config.get("gates") if isinstance(config, dict) else None
    block = (gates if isinstance(gates, dict) else {}).get("sealed_acceptance")
    block = block if isinstance(block, dict) else {"enabled": block}
    enabled = block.get("enabled")
    if isinstance(enabled, bool):
        return enabled
    if isinstance(enabled, str):
        return enabled.strip().lower() not in ("", "false", "0", "no", "off")
    return bool(enabled)


def seal_budget_seconds(config):
    """The seal's own budget: the `verify.timeout_minutes` rule (an int in 1..1440, bool rejected, default 60)
    -- the red-on-base run executes the sealed tests exactly as verify would. A test pins it to
    `work._sealed_timeout_seconds`, which is the same rule kept for the green run."""
    verify = config.get("verify") if isinstance(config, dict) else None
    minutes = (verify if isinstance(verify, dict) else {}).get("timeout_minutes")
    ok = isinstance(minutes, int) and not isinstance(minutes, bool) and 1 <= minutes <= 1440
    return (minutes if ok else 60) * 60


def runs_pytest(command):
    """HEURISTIC (named as one): does this verify command run pytest? A `pytest`/`py.test` token, or a path
    ending in either. A command that wraps pytest in a script it does not name is refused (false refuse)."""
    if not isinstance(command, str) or not command.strip():
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return any(os.path.basename(t) in ("pytest", "py.test") for t in tokens)


# ----------------------------------------------------------------------------------------- state files

def attempts_path(sdlc_dir, stem):
    return pathlib.Path(sdlc_dir) / "state" / "seal-attempts" / (stem + ".attempt")


def read_attempts(sdlc_dir, stem):
    """-> int. Absent is 0. FAILS CLOSED: a counter that is a symlink, not a whole non-negative integer, or
    unreadable raises Refused(4): an unreadable counter must never read as zero (that would be a free run)."""
    path = attempts_path(sdlc_dir, stem)
    if not os.path.lexists(path):
        return 0
    try:
        if path.is_symlink():
            raise ValueError("a symlink")
        text = path.read_text(encoding="utf-8").strip()
        if not text.isdigit():
            raise ValueError("not a whole number")
        return int(text)
    except (OSError, ValueError) as exc:
        raise Refused(4, "attempts-unreadable",
                      "the attempts counter %s is unreadable (%s), so the cap cannot be checked; an owner "
                      "must fix or delete that one file by hand" % (path.as_posix(), exc))


def write_attempts(sdlc_dir, stem, count):
    state = _load("state")
    path = attempts_path(sdlc_dir, stem)
    state.refuse_symlinks(sdlc_dir, path, create_parents=True)
    state.atomic_write_text(path, "%d\n" % count)


def _read_json(path):
    try:
        if pathlib.Path(path).is_symlink():
            return None
        value = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def work_record_path(sdlc_dir, stem):
    return pathlib.Path(sdlc_dir) / "state" / "work" / (stem + ".json")


# ------------------------------------------------------------------------------------------- git helpers

def _git(root, *args, timeout=60):
    env = _load("bounded_run").unattended_env()
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout,
                              env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def base_commit(rec, worktree):
    """The sha the goal branch was cut from: `git merge-base HEAD <remote>/<base>` in the worktree, with the
    remote and base from the work record. UNMEASURED beyond the fixtures. Raises Refused(3) when unresolved."""
    remote, base = rec.get("remote") or "origin", rec.get("base") or ""
    state = _load("state")
    ref = state.safe_ref("base", "%s/%s" % (remote, base)) if base else state.INERT_REF
    if ref == state.INERT_REF:
        raise Refused(3, "base-unresolved", "the work record names no usable base branch (%r)" % (base,))
    got = _git(worktree, "merge-base", "HEAD", ref)
    sha = got.stdout.strip()
    if got.returncode or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        raise Refused(3, "base-unresolved", "`git merge-base HEAD %s` did not resolve in the goal worktree "
                      "(fetch the remote, then re-run)" % ref)
    return sha


# ------------------------------------------------------------------------------------------ the brief

def interface_section(text):
    """The optional `## Interface` section of the acceptance record, else ''. Scrubbed later by `brief`."""
    match = _HEADING.search(text)
    if not match:
        return ""
    rest = text[match.end():]
    nxt = _NEXT_HEADING.search(rest)
    return (rest[:nxt.start()] if nxt else rest).strip()


def output_contract(stem):
    """The format text appended to the brief, so the breaker knows what to print. Written to avoid every
    `breaker._LEAK` shape (no leading dash, no code); `no_channel` runs over the FULL text sent."""
    s = re.sub(r"[^A-Za-z0-9]", "_", stem)
    return "\n".join([
        "", "OUTPUT FORMAT (print ONLY file blocks; no prose, no code fences):", "",
        "=== FILE: tests/acceptance/%s/test_acc_%s_AC1_short_name.py ===" % (stem, s),
        "<python source>", "=== END FILE ===", "",
        "RULES:",
        "  * Each file name is test_acc_%s_AC<n>_<name>.py where <n> is the number of the criterion above "
        "it attacks (the first criterion is 1)." % s,
        "  * Each file defines at least one module-level test function whose name starts with test_.",
        "  * At module top use only pytest and the standard library, and bring in the code under test inside "
        "the test function.",
        "  * Every test must FAIL until the criterion is implemented."])


def verdict_lines(items):
    """-> the per-criterion report lines for every NON-actionable criterion (scrubbed text, reason)."""
    out = []
    for number, v in enumerate(_load("breaker").criterion_verdicts(items), 1):
        if not v["actionable"]:
            out.append("  criterion %d: %r -- %s" % (number, v["criterion"][:80], v["reason"]))
    return out


# ------------------------------------------------------------------------------------- output handling

_HEADER = re.compile(r"=== FILE: (.+) ===")
_END = "=== END FILE ==="


def parse_blocks(text):
    """-> [(path, source)] from text that `breaker.validate_output` has ALREADY accepted (so every line
    outside a block is blank and every block is terminated). `seal()` re-validates the bytes it reads back."""
    blocks, current = [], None
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip(" \t")
        header = _HEADER.fullmatch(line)
        if header:
            current = (header.group(1), [])
        elif line == _END and current is not None:
            blocks.append((current[0], "\n".join(current[1]) + "\n"))
            current = None
        elif current is not None:
            current[1].append(raw)
    return blocks


def check_names(blocks, stem, criteria_count):
    """Findings for the `AC<n>` rule layered on breaker's grammar: the suffix after `test_acc_<s>_` must start
    `AC<n>` with 1 <= n <= the number of criteria."""
    want = "test_acc_%s_" % re.sub(r"[^A-Za-z0-9]", "_", stem)
    findings = []
    for path, _source in blocks:
        base = path.rsplit("/", 1)[-1]
        suffix = base[len(want):-3] if base.startswith(want) and base.endswith(".py") else ""
        match = AC_SUFFIX.match(suffix)
        if not match or int(match.group(1)) > criteria_count:
            findings.append("%s: the name must be %sAC<n>_<name>.py with n from 1 to %d (%s)"
                            % (base, want, criteria_count, COVERAGE_NOTE))
    return findings


def write_files(root, stem, blocks):
    """Write validated blocks into `<root>/tests/acceptance/<stem>/` FROM PYTHON. Any existing sealed
    directory is cleared first (re-seal). The destination path is lstat-checked for a symlink ONCE, up front
    (`refuse_symlinks` over `tests/acceptance/<stem>/`); the directory is emptied before the writes, so no
    file path can be a symlink afterwards (a per-file check was removed: no test could turn it red). A
    failure part-way removes what this call wrote. -> the list of relative paths written."""
    state = _load("state")
    base = pathlib.Path(root)
    rel_dir = "tests/acceptance/%s" % stem
    try:
        state.refuse_symlinks(base, rel_dir + "/x.py", create_parents=True)
    except state.UnsafeStatePath as exc:
        raise Refused(4, "unsafe-destination", str(exc))
    dest = base / rel_dir
    dest.mkdir(parents=True, exist_ok=True)
    for child in dest.iterdir():                     # clear: a re-seal rewrites the whole sealed directory
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    written = []
    try:
        for path, source in blocks:
            state.atomic_write_text(base / path, source)
            written.append(path)
    except BaseException:
        remove_files(root, written)
        raise
    return written


def remove_files(root, rels):
    for rel in rels:
        try:
            os.unlink(os.path.join(str(root), *rel.split("/")))
        except OSError:
            pass


# ------------------------------------------------------------------------------------------ spend

def spend_consult(sdlc_dir, goal, config):
    """-> (ok, line). Consults `spend_approval` for the first breaker run of an unattended goal. Only
    `APPROVED` passes; `DENIED` and `OFF` both refuse (an unattended paid run needs a recorded approval)."""
    try:
        source = _load("sources").get_source(sdlc_dir, config)
        line = _load("spend_approval").check(
            sdlc_dir, goal, "run the author-blind breaker (a paid model session) for the sealed acceptance tests, "
            "up to %d runs for this goal" % MAX_RUNS, config, source)
    except Exception as exc:                         # noqa: BLE001 - fail closed
        return False, "DENIED unexpected %s" % type(exc).__name__
    words = str(line).split(None, 1)
    return bool(words) and words[0] == "APPROVED", line


# ------------------------------------------------------------------------------------------- the verb

def _plan_file(sdlc_dir, stem, rec):
    rc = _load("review_context")
    plan = rc.phase_doc_file(sdlc_dir, stem, "plans")
    if plan is None and rec.get("worktree"):
        plan = rc.phase_doc_file(pathlib.Path(rec["worktree"]) / pathlib.Path(sdlc_dir).resolve().name, stem, "plans")
    return plan


def _implement_started(sdlc_dir, stem):
    try:
        rows = _load("actionlog").read_goal(sdlc_dir, stem)
    except Exception:                                # noqa: BLE001 - unreadable log: cannot say, so say no
        return False
    return any(r.get("kind") == "phase" and r.get("phase") == "implement" for r in rows)


def preconditions(sdlc_dir, stem, config, env, which):
    """Every refusal that happens BEFORE any spawn, in order. -> a dict of the facts the spawn step needs.
    Raises Refused. Reads, never writes."""
    breaker, bounded, reviewer = _load("breaker"), _load("bounded_run"), _load("reviewer")
    work = config.get("work")
    if not (isinstance(work, dict) and work.get("enabled")):
        raise Refused(3, "work-off", "work is off (config: \"work\": {\"enabled\": true}); the seal is written "
                      "into a goal worktree")
    refusal = bounded.group_refusal()
    if refusal:
        raise Refused(3, "no-process-group", refusal)
    rec = _read_json(work_record_path(sdlc_dir, stem))
    if not rec or not rec.get("worktree"):
        raise Refused(3, "no-work-record", "no work record for %s: run `work.py start` first" % stem)
    worktree = rec["worktree"]
    kind = bounded.checkout_kind(worktree) if os.path.isdir(worktree) else None
    if kind != "linked":
        raise Refused(3, "main-checkout", "%s is not a linked goal worktree, so the seal is not written there"
                      % worktree)
    acceptance = _load("acceptance")
    try:
        record = acceptance.read(sdlc_dir, stem)
    except FileNotFoundError:
        raise Refused(3, "no-acceptance", "no acceptance record for %s in the main checkout's .sdlc/acceptance/"
                      % stem)
    except (OSError, ValueError) as exc:
        raise Refused(3, "bad-acceptance", "the acceptance record is unusable: %s" % exc)
    verify = config.get("verify") if isinstance(config.get("verify"), dict) else {}
    command = record.get("verify_command") or verify.get("command")
    if not command:
        raise Refused(3, "no-verify-command", "no verify command is declared (D-4): the sealed tests are pytest, "
                      "so a pytest verify command is required")
    if not runs_pytest(command):
        raise Refused(3, "non-pytest-verify", "the verify command does not run pytest (D-4; a `pytest` token is "
                      "a heuristic, and a wrapper script that hides it is refused)")
    plan = _plan_file(sdlc_dir, stem, rec)
    if plan is None:
        raise Refused(3, "no-plan", "no plan for %s in .sdlc/plans/ or the goal worktree" % stem)
    plan_hash = _load("review_context").plan_sha256(plan)
    review = _read_json(pathlib.Path(sdlc_dir) / "state" / "gates" / (stem + ".json"))
    if not review or review.get("verdict") not in ("pass", "warn"):
        raise Refused(3, "no-plan-review", "no approving plan-review is recorded for %s (`work.py "
                      "record-plan-review`, kept only with work.enabled and this goal's work record)" % stem)
    if review.get("plan_hash") != plan_hash:
        raise Refused(3, "stale-plan-review", "the plan was edited after its plan-review (recorded sha256 %s..., "
                      "plan is %s...): run a fresh plan-review"
                      % (str(review.get("plan_hash"))[:12], plan_hash[:12]))
    if _load("sealed_tests").is_sealed(sdlc_dir, stem) and _implement_started(sdlc_dir, stem):
        raise Refused(4, "reseal-after-implement", "this goal is already sealed and implement has started; "
                      "re-sealing would rewrite the proof under the code it judges. %s" % NO_BYPASS)
    items = breaker.acceptance(record["text"])
    ok, text = breaker.brief(record["text"], interface_section(record["text"]))
    lines = verdict_lines(items)
    if not ok:
        raise Refused(3, "not-actionable", "the breaker brief was refused: %s%s\n%s" % (
            text, "\ncriteria judged non-actionable:" if lines else "", "\n".join(lines)))
    if lines:
        raise Refused(3, "not-actionable", "criteria judged non-actionable (a plan-defect return is slice B; "
                      "rewrite them, then re-run):\n" + "\n".join(lines))
    full = text + "\n" + output_contract(stem)
    clean, findings = breaker.no_channel(full, scratch_paths=[worktree, str(pathlib.Path(sdlc_dir).resolve())])
    if not clean:
        raise Refused(3, "channel", "the brief is not free of a maker->breaker channel: " + "; ".join(findings))
    route = reviewer.resolve_breaker(sdlc_dir, env=env, which=which)
    if route["mechanism"] != "process":
        raise Refused(4, "no-route", "no breaker route on host %s: %s. %s" % (route["host"], route["reason"],
                                                                              NO_BYPASS))
    count = read_attempts(sdlc_dir, stem)
    if count >= MAX_RUNS:
        raise Refused(4, "cap", "the breaker has run %d times for %s (the cap is %d: %d re-seals); park the goal "
                      "and ask an owner" % (count, stem, MAX_RUNS, RESEAL_CAP))
    return {"rec": rec, "worktree": worktree, "brief": full, "route": route, "plan_hash": plan_hash,
            "count": count, "criteria": len(items), "base": base_commit(rec, worktree)}


def run(sdlc_dir, goal, *, config=None, env=None, which=None, spend=None, seal=None, run_breaker=None):
    """-> (exit_code, text). The verb. `env`, `which`, `spend`, `seal` and `run_breaker` are test seams."""
    sdlc_dir = os.path.abspath(sdlc_dir)
    env = os.environ if env is None else env
    stem = _stem(goal)
    reason = _load("state").unsafe_goal_reason(stem)
    if reason:
        return 2, "seal-tests: unsafe goal %r: %s" % (goal, reason)
    config = config if config is not None else _load("state").load_config(sdlc_dir)
    if not gate_on(config):
        return 0, "seal-tests: OFF"
    try:
        facts = preconditions(sdlc_dir, stem, config, env, which)
        return _spawn_and_seal(sdlc_dir, stem, config, env, facts, spend, seal, run_breaker)
    except Refused as exc:
        return exc.code, "seal-tests: REFUSED [%s]: %s" % (exc.clause, exc.detail)
    except Exception as exc:                         # noqa: BLE001 - total: an unexpected failure is a refusal
        return 4, "seal-tests: REFUSED [error]: %s: %s" % (type(exc).__name__, exc)


def _spawn_and_seal(sdlc_dir, stem, config, env, facts, spend, seal, run_breaker):
    breaker, sealed, runner = _load("breaker"), _load("sealed_tests"), _load("breaker_run")
    worktree, route, count = facts["worktree"], facts["route"], facts["count"]
    unattended = any(env.get(name) for name in UNATTENDED_VARS)

    def before_spawn():
        # The last step before quota is spent: the consult (first run only; the label is single-use), THEN the
        # counter, so a denied consult costs no attempt and a counter write can never come after the spawn.
        if unattended and count == 0:
            ok, line = (spend or spend_consult)(sdlc_dir, stem, config)
            if not ok:
                raise Refused(4, "spend", "an unattended run needs a recorded spend approval before the first "
                              "breaker run (%s); park the goal" % line)
        try:
            write_attempts(sdlc_dir, stem, count + 1)
        except (OSError, _load("state").UnsafeStatePath) as exc:
            raise Refused(4, "attempts-unwritable", "cannot record the attempt, so the breaker is not started: %s"
                          % exc)

    try:
        text, _receipt = (run_breaker or runner.run)(route["command"], facts["brief"], stem, config,
                                                     timeout=route["timeout_seconds"], before_spawn=before_spawn)
    except runner.BreakerRunRefused as exc:
        raise Refused(4, "breaker-" + exc.clause, str(exc))
    ok, findings = breaker.validate_output(text, stem)
    if not ok:
        raise Refused(4, "bad-output", "; ".join(findings))
    blocks = parse_blocks(text)
    findings = check_names(blocks, stem, facts["criteria"])
    if findings:
        raise Refused(4, "bad-name", "; ".join(findings))
    seal_route = {"host": route["host"], "mechanism": route["mechanism"], "verified": route["verified"],
                  "role": "breaker"}
    written = write_files(worktree, stem, blocks)
    try:
        record = (seal or sealed.seal)(worktree, sdlc_dir, stem, base_ref=facts["base"], plan_hash=facts["plan_hash"],
                                       route=seal_route, timeout=seal_budget_seconds(config))
    except sealed.SealRefused as exc:
        remove_files(worktree, written)
        raise Refused(4, exc.clause, "SEALED REFUSED: %s. The files this run wrote were removed; any earlier "
                      "sealed files were cleared, so run it again (attempt %d of %d)" % (exc, count + 1, MAX_RUNS))
    except BaseException:
        remove_files(worktree, written)
        raise
    return 0, ("seal-tests: SEALED %s: %d file(s), %d red on base, generation %d (breaker run %d of %d; one spend "
               "approval covers up to %d runs)\n  %s; the name rule test_acc_<goal>_AC<n>_*.py is all that is "
               "checked" % (stem, len(record["files"]), len(record["red"]), record["generation"], count + 1,
                            MAX_RUNS, MAX_RUNS, COVERAGE_NOTE))

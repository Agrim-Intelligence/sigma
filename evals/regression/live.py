#!/usr/bin/env python3
"""Live regression entrypoint: the refusal ladder (#884) and the `run` path (#810 slices 3 and 4).

    python3 evals/regression/live.py            # bare: refuses, exit 2, one NOT RUN row
    python3 evals/regression/live.py run ...    # one capped model run (see evals/regression/README.md)

Without `run` this NEVER STARTS A MODEL: a ladder-clean bare invocation ends in the terminal refusal
`no-run-requested` (exit 2), never a silent exit 0 that would read as a pass. With `run` it makes ONE
`claude -p` run through an operator-installed launcher, but only after the whole pure ladder is clean, the
launcher precondition check (config + `--dry-run`) has passed and the belt is booked in the spend ledger. It
imports no process-spawning module: the two spawn sites live in the benchmark helpers (loaded by path in
`load_arms`, the only function that names them) and are pinned by AST tests to `precheck_launcher`
(`run_bounded`) and `_run` (`run_claude`, inside the `reserved()` block, after the `Reservation` guard).

NOT MEASURED. No real `claude` and no real launcher has been run through `run`: every test uses a fake launcher
and a fake `claude`. They prove our argv, environment, ordering, ledger and row behaviour against a model of the
launcher, not that the real launcher, the real `claude` and a real token work together, nor what a real run costs.
Windows is unmeasured until the `windows.yml` step is dispatched. No performance claim is made.

The ladder, in this order; every rung is evaluated each run, stderr and the row's `code` carry the
FIRST failing code, and the row's `also_failing` lists the rest in ladder order:

    no-credential, no-opt-in, no-cap, unsupported-platform, pull-request-event,
    subscription-in-gated-mode, then the terminal no-run-requested.

(`no-run-requested` is the RENAMED former `not-implemented`; the old text said no run path exists.) After a clean
ladder, `run` adds, in this order: `launcher-api-key-unsupported` (an API key is set; the launcher takes only
`CLAUDE_CODE_OAUTH_TOKEN`; goal 831), `run-refused` (an operator value failed validation; the row names the stage),
`launcher-precondition` / `launcher-latch` (the launcher's sibling config, the pre-run latch file, then the launcher's own
`--dry-run -- claude --version`), `spend-ledger` (the ledger refused or the budget is spent), then the run.
The launcher check spawns, so it runs only when nothing earlier refused: `also_failing` never lists `launcher-*` codes
when an earlier rung already failed. After the run: `launcher-latch` (exit 97 or the latch file appeared; the stop is
primary via the launcher's SIGTERM to its parent, the exit-97 and file checks are the fallback), `run-signalled`,
`run-error` (any other failure; the row names only the exception TYPE).

Out of the ladder: `bad-usage` (a malformed command line; fixed text, never echoes the argument) and
`scrubber-unavailable` (the shared scrubber could not be loaded; exact-value redaction still applies).

Every refusal prints ONE line `live.py: REFUSED [<code>]: <detail>` on stderr, prints nothing on
stdout, exits 2, and writes one `sigma.regression-result/v1` NOT RUN row under the results directory
(`--results-dir`, default `<repo root>/.sdlc/eval/results`, anchored to the repo root and not the
cwd). A failed row write never changes the refusal: one extra line `live.py: row not recorded (...)`.
`--help` exits 0 before the ladder and writes no row. A run that completes prints the redacted row on stdout.
Exit codes: RAN 0, RUN-FAILED 1 (also a RAN whose row could not be written), every refusal and NOT RUN 2, a signal
stop 128 plus the signal number.

Opt-in and gating. `SIGMA_REGRESSION_LIVE` opts in only when its stripped, lowercased value is one of
1, true, yes, on (an allowlist). A run is GATED when `CI` or `GITHUB_ACTIONS` is truthy, or the
release-gate flag (`--release-gate`, `SIGMA_REGRESSION_RELEASE_GATE`) is truthy; those use the
falsy-list (anything but empty, 0, false, no, off is truthy). Both are fail-closed: an odd opt-in
value means not opted in, an odd gating value means gated. A gated run refuses whenever
`CLAUDE_CODE_OAUTH_TOKEN` is non-blank, even with an API key present. The cap and the optional belt
are DOLLARS (`--cap-usd`/`SIGMA_REGRESSION_CAP_USD`, `--belt-usd`/`SIGMA_REGRESSION_BELT_USD`; a flag
beats the environment; the belt must not exceed the cap). A blank environment belt (how an unset CI
variable arrives) is UNSET, as a blank opt-in is "not opted in"; a blank `--belt-usd=` flag is typed
deliberately and refuses `no-cap`; a blank cap is unset and refuses. Number grammar: digits with an optional
fraction of up to six digits, finite, greater than zero.

Spend. The belt (default: the cap) is reserved in the spend ledger (`spend_ledger.py`, default
`<home>/.sigma/eval/ledger`, per USER so every checkout shares one cap; `--ledger-dir` overrides) BEFORE the
launcher starts; the ledger clamps it to what is left of the cap. Concurrent runs are allowed and cap-clamped (a
second reserve is reduced by the first run's open belt); there is no run lock. Afterwards the reservation is settled
at the metered spend rounded up, or at the FULL belt when the spend is unmeasured. SIGTERM/SIGHUP settle at the full
belt (reason `signal`); SIGKILL leaves the open counted at its full belt (lever: `spend_ledger.settle_orphan`). The cap
is per ledger home, per machine; a lost hosted runner forgets the belt (goal 819).

Credential-safe logging. Held credential values (and their quoted, url-quoted, shell-quoted and
base64 forms, for values of at least 8 characters) are redacted by EXACT value before anything is
written. The launcher child gets only `CLAUDE_CODE_OAUTH_TOKEN` plus a fixed minimal environment; the precheck gets no
credential. The only operator-supplied text that is ever echoed is a rejected cap or belt text, and it is
processed in this order: exact-value redaction of the whole text, then repr, then a cut to 40
characters, then the shared scrubber. Run-path lines are constants plus validated or integer parts. DEVIATION from the
#810 design ("every log line through the scrubber"): the shared scrubber is not run over a whole line, because its
`credential-assignment-suffix` rule rewrites the fixed `[no-credential]:` prefix; fixed text is ours and
credential-free, so a line is built from constants plus the processed variable part, and a final exact-value pass
covers each whole line and the serialised row.

Result row `sigma.regression-result/v1`. The schema is a contract for the upload and release-gate
slices (#819, #820); a later rename bumps the version and never edits v1 in place. This slice ADDS fields only:
`ledger_id`, `spent`, `exit_code`, `timed_out`. `status` is `NOT RUN` (nothing judged: a refusal, a trip, a signal),
`RAN` (the run exited cleanly and was measured) or `RUN-FAILED` (non-zero exit, timeout, throttle, unmeasured, or a
failed settle). RAN is never GREEN: live.py is the spawner, not the judge, and any status that is not GREEN is not a
pass; #819 and #820 must handle the two new values. On a refusal that never reserved, `cost` is null and
`representative` is false. Null-cost rule: a reader summing spend SKIPS null-cost rows (counts them as NOT RUN);
it never sums them as zero. `spent` is the ledger's settled amount and is authoritative; a NOT RUN row that was
settled also carries it as `cost` with `cost_basis` `settled-at-full-belt` (or `indicative` when metered), and an
unmeasured RUN-FAILED row has `cost` null with `spent` set. A row with a `ledger_id` and no `spent` was settled by the
ledger itself at the full belt (signal or exit path). `commit` is `--commit` (40 hex) when given, else a valid
`GITHUB_SHA`, else `unknown`; `cell` is `--cell` when given.

Not exercised: Windows, and the behaviour on a real hosted runner. Growth: one ~1 KB file per
invocation in the results directory, unpruned in this slice (pruning belongs with the spend-record slice).
"""
from __future__ import annotations

import argparse
import base64
import datetime
import importlib.util
import json
import math
import os
import pathlib
import re
import secrets
import shlex
import sys
import tempfile
import time
import urllib.parse
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal

SCHEMA = "sigma.regression-result/v1"
EXIT_REFUSED = 2
MIN_REDACT_LEN = 8
ECHO_CUT = 40
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = REPO_ROOT / ".sdlc" / "eval" / "results"
SCRUB_PATH = REPO_ROOT / "skills" / "sigma-loop" / "scripts" / "scrub.py"

CODES = ("no-credential", "no-opt-in", "no-cap", "unsupported-platform", "pull-request-event",
         "subscription-in-gated-mode", "no-run-requested", "bad-usage", "scrubber-unavailable",
         "launcher-api-key-unsupported", "launcher-precondition", "launcher-latch", "spend-ledger",
         "run-refused", "run-signalled", "run-error")
DETAILS = {
    "no-credential": "neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set",
    "no-opt-in": "SIGMA_REGRESSION_LIVE is not set to 1, true, yes or on",
    "no-cap": ("a positive finite dollar cap is required (--cap-usd or SIGMA_REGRESSION_CAP_USD) and a "
               "belt (--belt-usd or SIGMA_REGRESSION_BELT_USD) must be positive, finite and not above it"),
    "unsupported-platform": "only POSIX hosts are supported",
    "pull-request-event": "refusing to run for a pull request event, or for an unnamed event in Actions",
    "subscription-in-gated-mode": "a gated run may not hold CLAUDE_CODE_OAUTH_TOKEN; use ANTHROPIC_API_KEY alone",
    "no-run-requested": "no subcommand: name `run` to start a model run",
    "bad-usage": "unrecognised or malformed arguments; see --help",
    "scrubber-unavailable": "the shared scrubber could not be loaded",
    "launcher-api-key-unsupported": ("the launcher accepts only CLAUDE_CODE_OAUTH_TOKEN; an API key run "
                                     "needs a launcher change (goal 831)"),
    "launcher-precondition": "the launcher or its configuration failed a precondition",
    "launcher-latch": "the launcher stop latch is present or tripped; inspect it and delete it by hand",
    "spend-ledger": "the spend ledger refused to book this run",
    "run-refused": "the run request was refused before anything was started",
    "run-signalled": "the run was stopped by a signal; its belt stays counted",
    "run-error": "an unexpected error stopped the run",
}
PR_EVENTS = {"pull_request", "pull_request_target", "pull_request_review", "pull_request_review_comment"}
OPT_IN_VALUES = {"1", "true", "yes", "on"}
NUMBER = re.compile(r"[0-9]+(\.[0-9]{1,6})?")


# The launcher's own facts, mirrored because live.py may not import anything under evals/bench (AC-5): the
# latch file name and the exit code 97 are pinned against the launcher source by a test, so drift fails.
LAUNCHER_CREDENTIAL_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
LAUNCHER_LATCH_NAME = "real-profile-changed.txt"
LATCH_EXIT = 97
LIVE_MARKER = "live.py"
MINIMAL_ENV_NAMES = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM")
PRECHECK_TIMEOUT_S = None          # None: the helpers' VERSION_TIMEOUT_SECONDS. A WAIT BOUND, not a machine-derived limit
ARMS_PATH_OVERRIDE = None          # test seam only
MODEL_PATTERN = re.compile(r"[A-Za-z0-9._:/\[\]-]{1,80}")
CELL_PATTERN = re.compile(r"[A-Za-z0-9._:/@+-]{1,80}")
COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
PROMPT_LIMIT_BYTES = 1 << 20
_ARMS = None
_LEDGER = None
RATES = None                       # test seam: a rate card for the metering; None means the shipped card
LEDGER_LOCK_TIMEOUT_S = 5.0        # test seam: how long a reserve or settle waits for the ledger lock
RUN_CODES = ("ran", "exit-nonzero", "timed-out", "throttled", "unmeasured", "settle-failed")
FULL_BELT_BASIS = "settled-at-full-belt"
NAME_WARNING = ("live.py: warning: invoked as a name other than live.py; the launcher's parent marker may not "
                "match, so its stop signal may not reach this process (the exit-97 and latch-file checks remain)")


class Failure(object):
    def __init__(self, code, detail, variable=None):
        self.code, self.detail, self.variable = code, detail, variable


RUN_REQUIRED = ("prompt_file", "workdir", "model", "permission_mode", "launcher", "claude", "timeout_s")
RUN_OPTIONAL = ("plugin_dir", "ledger_dir", "commit", "cell")


class Options(object):
    def __init__(self, cap_text, belt_text, release_gate, results_dir, command=None, **run):
        self.cap_text, self.belt_text = cap_text, belt_text
        self.release_gate, self.results_dir = release_gate, results_dir
        self.command = command
        for name in RUN_REQUIRED + RUN_OPTIONAL:
            setattr(self, name, run.get(name))



class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Usage()


def build_parser():
    p = _Parser(prog="live.py", allow_abbrev=False,
                description="Live regression entrypoint: refuses loudly, or (with `run`) one capped model run.")
    p.add_argument("--cap-usd", dest="cap_text", default=None, help="total spend cap in dollars")
    p.add_argument("--belt-usd", dest="belt_text", default=None, help="per-run belt in dollars (<= cap)")
    p.add_argument("--release-gate", dest="release_gate", action="store_true",
                   help="mark this run as feeding the release gate")
    p.add_argument("--results-dir", dest="results_dir", default=None,
                   help="where result rows go (default: <repo root>/.sdlc/eval/results)")
    p.add_argument("command", nargs="?", choices=["run"], default=None,
                   help="`run` starts one capped model run; no subcommand only refuses")
    for name in RUN_REQUIRED + RUN_OPTIONAL:
        p.add_argument("--" + name.replace("_", "-"), dest=name, default=None,
                       help="run only" + (" (required)" if name in RUN_REQUIRED else ""))
    return p


def _truthy(value):
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}


def platform_is_posix():
    return os.name == "posix"


def _nonblank(env, name):
    return str(env.get(name) or "").strip() != ""


def parse_dollars(text):
    """Dollars from text, or None when it does not meet the grammar."""
    if text is None:
        return None
    s = str(text).strip()
    if not NUMBER.fullmatch(s):
        return None
    value = float(s)
    return value if math.isfinite(value) and value > 0 else None


class Stop(Exception):
    """A run-path refusal: a code plus FIXED detail text (never operator text)."""

    def __init__(self, code, detail_extra="", **row_extra):
        Exception.__init__(self, code)
        self.code, self.detail_extra, self.row_extra = code, detail_extra, row_extra


class ArmsUnavailable(Exception):
    pass


def load_arms():
    """The ONLY place that names the benchmark helpers' path. Loaded by path (live.py is not a package member),
    cached, and only ever called after the whole pure ladder is clean."""
    global _ARMS
    if _ARMS is not None:
        return _ARMS
    try:
        path = ARMS_PATH_OVERRIDE or (REPO_ROOT / "evals" / "bench" / "arms" / "common.py")
        name = "sigma_regression_arms_common"
        spec = importlib.util.spec_from_file_location(name, str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod                              # the helpers define a frozen dataclass
        spec.loader.exec_module(mod)
    except Exception as exc:                                 # noqa: BLE001 - absence is the answer
        sys.modules.pop("sigma_regression_arms_common", None)
        raise ArmsUnavailable() from exc
    _ARMS = mod
    return mod


def evaluate(env, opts, posix):
    """PURE: every rung evaluated, nothing read from the ambient process.
    Returns (basis, cap, belt, gated, failures) with failures in ladder order."""
    oauth, api = _nonblank(env, "CLAUDE_CODE_OAUTH_TOKEN"), _nonblank(env, "ANTHROPIC_API_KEY")
    basis = "api-key" if api else ("subscription" if oauth else "none")
    gated = (_truthy(env.get("CI")) or _truthy(env.get("GITHUB_ACTIONS")) or bool(opts.release_gate)
             or _truthy(env.get("SIGMA_REGRESSION_RELEASE_GATE")))
    cap_text = opts.cap_text if opts.cap_text is not None else env.get("SIGMA_REGRESSION_CAP_USD")
    belt_text = opts.belt_text
    if belt_text is None:
        belt_text = env.get("SIGMA_REGRESSION_BELT_USD")
        if belt_text is not None and not str(belt_text).strip():
            belt_text = None                                 # a blank env belt is unset (see module docstring)
    cap, belt = parse_dollars(cap_text), parse_dollars(belt_text)
    failures = []

    def fail(code, variable=None):
        failures.append(Failure(code, DETAILS[code], variable))

    if not (oauth or api):
        fail("no-credential")
    if str(env.get("SIGMA_REGRESSION_LIVE") or "").strip().lower() not in OPT_IN_VALUES:
        fail("no-opt-in")
    bad_text = None
    if cap is None:
        bad_text = cap_text
    elif belt_text is not None and (belt is None or belt > cap):
        bad_text = belt_text
    if cap is None or (belt_text is not None and (belt is None or belt > cap)):
        fail("no-cap", bad_text)
    if not posix:
        fail("unsupported-platform")
    event = str(env.get("GITHUB_EVENT_NAME") or "").strip().lower()
    if event in PR_EVENTS or (_truthy(env.get("GITHUB_ACTIONS")) and event == ""):
        fail("pull-request-event")
    if gated and oauth:
        fail("subscription-in-gated-mode")
    return basis, cap, belt, gated, failures


def held_credentials(env):
    values = []
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        raw = str(env.get(name) or "")
        if raw.strip():
            values += [raw, raw.strip()]
    return values


def redaction_set(values):
    """Every spelling to redact, longest first. Values under MIN_REDACT_LEN are never redacted globally."""
    forms = set()
    for v in values:
        if len(v) < MIN_REDACT_LEN:
            continue
        raw = v.encode("utf-8", "surrogateescape")          # env bytes that are not UTF-8 arrive as surrogates
        forms.update({v, urllib.parse.quote(raw), urllib.parse.quote(raw, safe=""),
                      urllib.parse.quote_plus(raw), shlex.quote(v)})
        for enc in (base64.b64encode, base64.urlsafe_b64encode):
            padded = enc(raw).decode("ascii")
            forms.update({padded, padded.rstrip("=")})
    return sorted((f for f in forms if f), key=lambda f: (-len(f), f))


def redact(text, patterns):
    for form in patterns:
        text = re.sub(re.escape(form), lambda m: "[REDACTED]", text)
    return text


def echo(text, patterns, scrubber):
    """The one place the order lives: exact-value redaction, repr, cut, shared scrubber. Variable text only."""
    shown = repr(redact(str(text), patterns))[:ECHO_CUT]
    return scrubber.scrub(shown)


def load_scrubber():
    try:
        spec = importlib.util.spec_from_file_location("sigma_live_scrub", str(SCRUB_PATH))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod if callable(getattr(mod, "scrub", None)) else None
    except Exception:                                       # noqa: BLE001 - absence is the answer
        return None


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_row(code, reason, also_failing, basis, cap, belt, started, finished, env, *, status="NOT RUN",
              model=None, permission_mode="not-applicable", tokens=None, cost=None, cost_basis=None,
              representative=False, ledger_id=None, spent=None, exit_code=None, timed_out=None,
              commit=None, cell=None):
    sha = str(commit if commit is not None else env.get("GITHUB_SHA") or "")
    return {
        "schema": SCHEMA,
        "commit": sha if re.fullmatch(r"[0-9a-fA-F]{40}", sha) else "unknown",
        "cell": cell if cell is not None else
        "live/%s/py%d.%d" % (sys.platform, sys.version_info[0], sys.version_info[1]),
        "credential_basis": basis,
        "permission_mode": permission_mode,
        "status": status,
        "code": code,
        "reason": reason,
        "also_failing": list(also_failing),
        "representative": representative,
        "model": model,
        "belt": belt,
        "cap": cap,
        "tokens": tokens,
        "cost": cost,
        "cost_basis": cost_basis,
        "started": _stamp(started),
        "finished": _stamp(finished),
        "ledger_id": ledger_id,
        "spent": spent,
        "exit_code": exit_code,
        "timed_out": timed_out,
    }


def write_row(results_dir, row_text, moment):
    """The ONLY function that touches the write surface: temp file then os.replace, never half a row."""
    directory = pathlib.Path(results_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    final = None
    for _ in range(5):
        candidate = directory / ("live-%s-%d-%s.json" % (stamp, os.getpid(), secrets.token_hex(2)))
        if not candidate.exists():
            final = candidate
            break
    if final is None:
        raise FileExistsError("no free row name")
    tmp = final.with_name(final.name + ".tmp")
    try:
        tmp.write_text(row_text, encoding="utf-8")
        os.replace(str(tmp), str(final))
    finally:
        if tmp.exists():
            tmp.unlink()
    return final


def _refuse(code, also, variable, basis, cap, belt, env, results_dir, started, scrubber, patterns,
            detail_extra="", **row_extra):
    """Print the one refusal line, write the NOT RUN row, return the exit code.
    `detail_extra` is FIXED text or ints built by the caller (never operator text); `row_extra` are the
    additive row fields (ledger_id, exit_code, spent, ...)."""
    detail = DETAILS[code] + detail_extra
    shown = ""
    if variable is not None and scrubber is not None:
        shown = " (got %s)" % echo(variable, patterns, scrubber)
    line = "live.py: REFUSED [%s]: %s%s" % (code, detail, shown)
    print(redact(line, patterns), file=sys.stderr)
    reason = detail + shown
    if code != "scrubber-unavailable" and _nonblank(env, "CLAUDE_CODE_OAUTH_TOKEN") \
            and _nonblank(env, "ANTHROPIC_API_KEY"):
        reason += "; both-credentials"
    finished = _now()
    row = build_row(code, reason, also, basis, cap, belt, started, finished, env, **row_extra)
    try:
        text = redact(json.dumps(row, sort_keys=True, indent=2) + "\n", patterns)
        write_row(results_dir, text, finished)
    except Exception as exc:                                # noqa: BLE001 - the refusal must survive
        print("live.py: row not recorded (%s)" % type(exc).__name__, file=sys.stderr)
    return EXIT_REFUSED


class Ctx(object):
    """What every refusal and row on the run path needs; one object so a rung cannot forget a field."""

    def __init__(self, env, basis, cap, belt, results_dir, started, scrubber, patterns, invoked_as=None):
        self.env, self.basis, self.cap, self.belt = env, basis, cap, belt
        self.results_dir, self.started, self.scrubber, self.patterns = results_dir, started, scrubber, patterns
        self.invoked_as = invoked_as
        self.commit = self.cell = self.model = self.permission_mode = None

    def adopt(self, v):
        self.commit, self.cell, self.model, self.permission_mode = (v["commit"], v["cell"], v["model"],
                                                                    v["permission_mode"])

    def row_fields(self, extra):
        base = {"commit": self.commit, "cell": self.cell, "model": self.model,
                "permission_mode": self.permission_mode or "not-applicable"}
        base.update(extra)
        return base

    def refuse(self, code, detail_extra="", **row_extra):
        return _refuse(code, [], None, self.basis, self.cap, self.belt, self.env, self.results_dir,
                       self.started, self.scrubber, self.patterns, detail_extra, **self.row_fields(row_extra))

    def record(self, row):
        """Redact, write the row (best effort), return (redacted text, written?). Never raises."""
        text = redact(json.dumps(row, sort_keys=True, indent=2) + "\n", self.patterns)
        try:
            write_row(self.results_dir, text, _now())
            return text, True
        except Exception as exc:                            # noqa: BLE001 - the run must still be reported
            print("live.py: row not recorded (%s)" % type(exc).__name__, file=sys.stderr)
            return text, False


class RunState(object):
    """What the catch-all needs to know about a run in flight."""

    def __init__(self):
        self.led = self.res = None
        self.spent = None            # the settled amount (string) once a settle succeeded
        self.row_written = False


def load_ledger():
    """The sibling spend ledger, loaded by path (live.py is not a package member), cached."""
    global _LEDGER
    if _LEDGER is None:
        path = pathlib.Path(__file__).resolve().with_name("spend_ledger.py")
        spec = importlib.util.spec_from_file_location("sigma_regression_spend_ledger", str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["sigma_regression_spend_ledger"] = mod
        spec.loader.exec_module(mod)
        _LEDGER = mod
    return _LEDGER


def default_ledger_dir():
    """Per USER, not per checkout: every worktree and tree copy on this machine shares one cap. The cap is
    per ledger home; two machines (or two homes) each believe they hold the whole cap."""
    return pathlib.Path.home() / ".sigma" / "eval" / "ledger"


def _dollars(value):
    """Exact Decimal of a float that met the six-fractional-digit grammar (repr round-trips it)."""
    return Decimal(repr(value))


def _money_text(amount):
    return format(amount, "f")


def spent_for(attempt, belt):
    """What to settle: the metered cost rounded UP to the ledger's nine digits, else the FULL belt
    (unmeasured is never free)."""
    if attempt.cost is not None:
        return Decimal(str(attempt.cost)).quantize(Decimal("1e-9"), rounding=ROUND_CEILING)
    return belt


def classify_attempt(attempt):
    """(status, token). live.py is the spawner, not the judge: a clean run is RAN, never GREEN."""
    if attempt.timed_out:
        return "RUN-FAILED", "timed-out"
    if attempt.throttled is not None:
        return "RUN-FAILED", "throttled"
    if attempt.returncode != 0:
        return "RUN-FAILED", "exit-nonzero"
    if not attempt.tokens or attempt.cost is None:
        return "RUN-FAILED", "unmeasured"
    return "RAN", "ran"


def settle_full(led, res, reason):
    """Settle at the FULL belt (spend unknown). Returns the amount text, or None if it could not be booked
    (the open then stays counted at its full belt anyway)."""
    sl = load_ledger()
    try:
        led.settle(res, res.belt, reason)
        return _money_text(res.belt)
    except (sl.LedgerRefusal, sl.LedgerSignal):
        return None


def money_fields(spent_text, measured, not_run):
    """cost / cost_basis beside `spent`. `spent` is the ledger's amount and is authoritative; a NOT RUN row
    that was settled also carries it as `cost` (R8) so a reader summing `cost` never misses a charge."""
    if spent_text is None:
        return {"spent": None, "cost": None, "cost_basis": None}
    if measured:
        return {"spent": spent_text, "cost": float(spent_text), "cost_basis": "indicative"}
    if not_run:
        return {"spent": spent_text, "cost": float(spent_text), "cost_basis": FULL_BELT_BASIS}
    return {"spent": spent_text, "cost": None, "cost_basis": None}


def validate_run_options(opts):
    """Input stage: every operator value is checked here and only validated values go further.
    Raises Stop('run-refused', ': <fixed stage name>'). Nothing is spawned and nothing is echoed."""
    def refuse(stage):
        raise Stop("run-refused", ": " + stage)

    v = {}
    try:
        p = pathlib.Path(opts.prompt_file)
        if not p.is_file() or p.stat().st_size > PROMPT_LIMIT_BYTES:
            refuse("prompt-file")
        v["prompt"] = p.read_text(encoding="utf-8")
    except (OSError, ValueError):                            # UnicodeDecodeError is a ValueError
        refuse("prompt-file")
    if not os.path.isdir(opts.workdir):
        refuse("workdir")
    v["workdir"] = os.path.abspath(opts.workdir)
    if not (os.path.isabs(opts.launcher) and os.path.isfile(opts.launcher) and os.access(opts.launcher, os.X_OK)):
        refuse("launcher-path")
    v["launcher"] = opts.launcher
    if not (os.path.isabs(opts.claude) and os.path.isfile(opts.claude)):
        refuse("claude-path")
    v["claude"] = opts.claude
    if not MODEL_PATTERN.fullmatch(opts.model):
        refuse("model")
    if not MODEL_PATTERN.fullmatch(opts.permission_mode):
        refuse("permission-mode")
    v["model"], v["permission_mode"] = opts.model, opts.permission_mode
    timeout = parse_dollars(opts.timeout_s)                  # the same positive-number grammar
    if timeout is None:
        refuse("timeout")
    v["timeout_s"] = timeout
    if opts.commit is not None and not COMMIT_PATTERN.fullmatch(opts.commit):
        refuse("commit")
    if opts.cell is not None and not CELL_PATTERN.fullmatch(opts.cell):
        refuse("cell")
    v["commit"], v["cell"] = opts.commit, opts.cell
    if opts.plugin_dir is not None and not (os.path.isabs(opts.plugin_dir) and os.path.isdir(opts.plugin_dir)):
        refuse("plugin-dir")
    v["plugin_dir"] = opts.plugin_dir
    return v


def minimal_env(env, with_credential):
    """The environment a launcher child gets: a fixed allowlist, plus the ONE named credential variable
    only for the real run. Never the caller's whole environment."""
    out = {n: env[n] for n in MINIMAL_ENV_NAMES if n in env}
    if with_credential:
        out[LAUNCHER_CREDENTIAL_VAR] = env[LAUNCHER_CREDENTIAL_VAR]
    return out


def read_launcher_config(launcher):
    """The launcher's SIBLING config, derived exactly as the launcher derives it (`Path(__file__).resolve()`).
    Raises Stop('launcher-precondition', ': <fixed name of the failed check>'); file contents are never echoed."""
    def refuse(name):
        raise Stop("launcher-precondition", ": " + name)

    try:
        config = json.loads(pathlib.Path(launcher).resolve().with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        refuse("config-unreadable")
    if not isinstance(config, dict):
        refuse("config-not-object")
    # an absent parent_marker means the launcher's default (bench.py), so absent refuses too
    if config.get("parent_marker") != LIVE_MARKER:
        refuse("parent-marker")
    if config.get("credential_var") != LAUNCHER_CREDENTIAL_VAR:
        refuse("credential-var")
    scratch = config.get("scratch_root")
    if not (isinstance(scratch, str) and os.path.isabs(scratch)):
        refuse("scratch-root")
    if not (isinstance(config.get("claude_path"), str) and os.path.isabs(config["claude_path"])):
        refuse("claude-path")
    return config


def _overlaps(a, b):
    a, b = os.path.realpath(a), os.path.realpath(b)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep) or b.startswith(a.rstrip(os.sep) + os.sep)


def launcher_latch_present(config):
    return os.path.exists(os.path.join(config["scratch_root"], LAUNCHER_LATCH_NAME))


def precheck_timeout(common):
    return PRECHECK_TIMEOUT_S if PRECHECK_TIMEOUT_S is not None else common.VERSION_TIMEOUT_SECONDS


def precheck_launcher(common, launcher, claude, workdir, env):
    """Config checks, the pre-run latch check, then the launcher's own `--dry-run -- claude --version`
    (no credential, minimal environment, the run's own workdir). Returns the config; raises Stop."""
    config = read_launcher_config(launcher)
    if os.path.realpath(claude) != os.path.realpath(config["claude_path"]):
        raise Stop("launcher-precondition", ": claude-path-mismatch")
    if _overlaps(config["scratch_root"], tempfile.gettempdir()):
        raise Stop("launcher-precondition", ": scratch-overlaps-temp")
    if launcher_latch_present(config):
        raise Stop("launcher-latch", ": present before the run")
    try:
        code, timed_out, _ = common.run_bounded([launcher, "--dry-run", "--", claude, "--version"], cwd=workdir,
                                                env=minimal_env(env, False), timeout=precheck_timeout(common))
    except common.ArmRefusal:
        raise Stop("launcher-precondition", ": launcher --dry-run could not start")
    if timed_out:
        raise Stop("launcher-precondition", ": launcher --dry-run timed out")
    if code != 0:
        raise Stop("launcher-precondition", ": launcher --dry-run exited %d" % code)
    return config


def _error_name(exc):
    return re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:60] or "Error"


def run_error(ctx, state, exc):
    """R3: any other failure on the run path becomes a row naming only the exception TYPE, never its message."""
    spent = state.spent
    if state.res is not None and spent is None:
        # settled by reserved() on its way out (always at the full belt), or still open: settle it now
        spent = _money_text(state.res.belt) if state.res.settled else settle_full(state.led, state.res, "exit")
        state.spent = spent
    ids = {"ledger_id": state.res.id} if state.res is not None else {}
    code = "run-signalled" if isinstance(exc, SystemExit) else "run-error"
    rc = ctx.refuse(code, ": " + _error_name(exc), status="RUN-FAILED" if state.res is not None else "NOT RUN",
                    **dict(ids, **money_fields(spent, False, True)))
    state.row_written = True
    return 1 if state.res is not None else rc


def execute_run(opts, ctx):
    """The run path. Order: api-key refusal, input validation, helpers, launcher precheck, ledger reserve,
    the run, settle, row. Every failure is a row; nothing here raises to the caller except a signal's exit."""
    if ctx.basis == "api-key":
        return ctx.refuse("launcher-api-key-unsupported")
    state = RunState()
    try:
        return _run(opts, ctx, state)
    except Stop as stop:
        return ctx.refuse(stop.code, stop.detail_extra, **stop.row_extra)
    except Exception as exc:                                 # noqa: BLE001 - R3: a catch-all row
        return run_error(ctx, state, exc)
    except BaseException as exc:                             # KeyboardInterrupt, SystemExit from a signal
        if not state.row_written:
            run_error(ctx, state, exc)
        raise


def _run(opts, ctx, state):
    v = validate_run_options(opts)
    ctx.adopt(v)
    if ctx.invoked_as is not None and os.path.basename(ctx.invoked_as) != LIVE_MARKER:
        print(NAME_WARNING, file=sys.stderr)
    try:
        common = load_arms()
    except ArmsUnavailable:
        raise Stop("launcher-precondition", ": benchmark helpers could not be loaded")
    config = precheck_launcher(common, v["launcher"], v["claude"], v["workdir"], ctx.env)
    try:
        sl = load_ledger()
    except Exception:                                        # noqa: BLE001 - absence is the answer
        raise Stop("spend-ledger", ": ledger module could not be loaded")
    try:
        ledger_dir = opts.ledger_dir if opts.ledger_dir is not None else default_ledger_dir()
        led = sl.Ledger(os.path.abspath(ledger_dir), _dollars(ctx.cap), lock_timeout_s=LEDGER_LOCK_TIMEOUT_S)
        request = _dollars(ctx.belt if ctx.belt is not None else ctx.cap)
    except sl.LedgerRefusal as exc:
        raise Stop("spend-ledger", ": ledger: " + exc.code)
    try:
        with led.reserved(request) as res:
            if not isinstance(res, sl.Reservation):
                raise Stop("spend-ledger", ": ledger: " + res.code)
            state.led, state.res = led, res
            profile = tempfile.mkdtemp(prefix="sigma-regression-live-")
            try:
                belt_usd = float(res.belt.quantize(Decimal("0.000001"), rounding=ROUND_DOWN))
                deadline = time.monotonic() + v["timeout_s"]
                try:
                    attempt = common.run_claude(
                        launcher=v["launcher"], claude=v["claude"], model=v["model"],
                        permission_mode=v["permission_mode"], budget_usd=belt_usd, plugin_dir=v["plugin_dir"],
                        workdir=v["workdir"], prompt=v["prompt"], environment=minimal_env(ctx.env, True),
                        profile_dir=profile, deadline=deadline, rates=RATES)
                except common.ArmRefusal:
                    spent = settle_full(led, res, "exit")      # nothing ran, but the conservative count stands
                    state.spent = spent
                    return ctx.refuse("run-refused", ": start", ledger_id=res.id,
                                      **money_fields(spent, False, True))
                except sl.LedgerSignal:
                    spent = settle_full(led, res, "signal")
                    state.spent = spent
                    code = "launcher-latch" if launcher_latch_present(config) else "run-signalled"
                    ctx.refuse(code, ": signal", ledger_id=res.id, **money_fields(spent, False, True))
                    state.row_written = True
                    raise
            finally:
                try:
                    common.remove_tree(profile)
                except common.ArmRefusal:
                    print("live.py: profile directory not removed", file=sys.stderr)
            return _settle_and_report(ctx, state, config, led, res, attempt, sl)
    except sl.LedgerRefusal as exc:
        raise Stop("spend-ledger", ": ledger: " + exc.code)


def _settle_and_report(ctx, state, config, led, res, attempt, sl):
    """After the run: settle at the measured spend (else the full belt), then write the row. The row is
    written AFTER the settle, so it never claims a settle that failed."""
    spent_dec = spent_for(attempt, res.belt)
    measured = attempt.cost is not None
    try:
        led.settle(res, spent_dec)
        spent_text = _money_text(spent_dec)
        state.spent = spent_text
    except sl.LedgerRefusal:
        spent_text = None                                     # reserved() settles the open at the full belt
    common_fields = {"ledger_id": res.id, "exit_code": attempt.returncode, "timed_out": attempt.timed_out,
                     "tokens": attempt.tokens}
    if attempt.returncode == LATCH_EXIT or launcher_latch_present(config):
        return ctx.refuse("launcher-latch", ": tripped during the run (exit 97 or the latch file)",
                          **dict(common_fields, **money_fields(spent_text, measured, True)))
    status, token = classify_attempt(attempt)
    reason = token
    if spent_text is None:
        status, token, reason = "RUN-FAILED", "settle-failed", "settle-failed: " + token
    row = build_row(token, reason, [], ctx.basis, ctx.cap, float(res.belt), ctx.started, _now(), ctx.env,
                    status=status, model=ctx.model, permission_mode=ctx.permission_mode, commit=ctx.commit,
                    cell=ctx.cell, **dict(common_fields, **money_fields(spent_text, measured, False)))
    text, written = ctx.record(row)
    state.row_written = True
    print(text, end="")
    if status == "RUN-FAILED":
        print("live.py: RUN-FAILED [%s]: %s" % (token, reason), file=sys.stderr)
    return 0 if status == "RAN" and written else 1


def main(argv=None, env=None, posix=None, invoked_as=None):
    env = os.environ if env is None else env
    posix = platform_is_posix() if posix is None else posix
    started = _now()
    patterns = redaction_set(held_credentials(env))
    scrubber = load_scrubber()
    oauth, api = _nonblank(env, "CLAUDE_CODE_OAUTH_TOKEN"), _nonblank(env, "ANTHROPIC_API_KEY")
    basis = "api-key" if api else ("subscription" if oauth else "none")
    try:
        args = build_parser().parse_args(argv)
        run_given = {n: getattr(args, n) for n in RUN_REQUIRED + RUN_OPTIONAL}
        if args.command != "run" and any(v is not None for v in run_given.values()):
            raise _Usage()
        if args.command == "run" and any(run_given[n] is None for n in RUN_REQUIRED):
            raise _Usage()
    except _Usage:
        return _refuse("bad-usage", [], None, basis, None, None, env, DEFAULT_RESULTS_DIR, started,
                       scrubber, patterns)
    opts = Options(args.cap_text, args.belt_text, args.release_gate, args.results_dir, args.command,
                   **run_given)
    results_dir = opts.results_dir if opts.results_dir is not None else DEFAULT_RESULTS_DIR
    basis, cap, belt, _gated, failures = evaluate(env, opts, posix)
    codes = [f.code for f in failures]
    if scrubber is None:
        return _refuse("scrubber-unavailable", codes, None, basis, cap, belt, env, results_dir, started,
                       None, patterns)
    if not failures:
        ctx = Ctx(env, basis, cap, belt, results_dir, started, scrubber, patterns, invoked_as)
        if opts.command != "run":
            return ctx.refuse("no-run-requested")
        return execute_run(opts, ctx)
    first = failures[0]
    return _refuse(first.code, codes[1:], first.variable, basis, cap, belt, env, results_dir, started,
                   scrubber, patterns)


if __name__ == "__main__":
    sys.exit(main(invoked_as=sys.argv[0]))

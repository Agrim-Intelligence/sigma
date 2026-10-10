#!/usr/bin/env python3
"""Live regression entrypoint, slice 1: the refusal ladder (#884, part of the #810 design).

    python3 evals/regression/live.py            # bare: refuses, exit 2, one NOT RUN row

THIS SLICE NEVER STARTS A MODEL. It imports no process-spawning module and calls no spawn API
(pinned by AST tests), reads a credential only to ask whether it is non-blank and to build the
exact-value redaction set, and has no run path at all. A run that passes every rung ends in the
terminal refusal `not-implemented` (exit 2), never in a silent exit 0 that would read as a pass.

The ladder, in this order; every rung is evaluated each run, stderr and the row's `code` carry the
FIRST failing code, and the row's `also_failing` lists the rest in ladder order:

    no-credential, no-opt-in, no-cap, unsupported-platform, pull-request-event,
    subscription-in-gated-mode, then the terminal not-implemented.

Out of the ladder: `bad-usage` (a malformed command line; fixed text, never echoes the argument) and
`scrubber-unavailable` (the shared scrubber could not be loaded; exact-value redaction still applies).

Every refusal prints ONE line `live.py: REFUSED [<code>]: <detail>` on stderr, prints nothing on
stdout, exits 2, and writes one `sigma.regression-result/v1` NOT RUN row under the results directory
(`--results-dir`, default `<repo root>/.sdlc/eval/results`, anchored to the repo root and not the
cwd). A failed row write never changes the refusal: one extra line `live.py: row not recorded (...)`.
`--help` exits 0 before the ladder and writes no row.

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

Credential-safe logging. Held credential values (and their quoted, url-quoted, shell-quoted and
base64 forms, for values of at least 8 characters) are redacted by EXACT value before anything is
written. The only operator-supplied text that is ever echoed is a rejected cap or belt text, and it is
processed in this order: exact-value redaction of the whole text, then repr, then a cut to 40
characters, then the shared scrubber. DEVIATION from the #810 design ("every log line through the
scrubber"): the shared scrubber is not run over a whole line, because its `credential-assignment-suffix`
rule rewrites the fixed `[no-credential]:` prefix; fixed text is ours and credential-free, so a line is
built from constants plus the processed variable part, and a final exact-value pass covers each whole
line and the serialised row.

Result row `sigma.regression-result/v1`. The schema is a contract for the upload and release-gate
slices (#819, #820); a later rename bumps the version and never edits v1 in place. On a NOT RUN row
`cost` is null and `representative` is false. Null-cost rule: a reader summing spend SKIPS null-cost
rows (counts them as NOT RUN); it never sums them as zero, which would read as a measured free run.

Not exercised: Windows, and the behaviour on a real hosted runner. Growth: one ~1 KB file per
refusal, unpruned in this slice (pruning belongs with the spend-record slice).
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
import urllib.parse

SCHEMA = "sigma.regression-result/v1"
EXIT_REFUSED = 2
MIN_REDACT_LEN = 8
ECHO_CUT = 40
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = REPO_ROOT / ".sdlc" / "eval" / "results"
SCRUB_PATH = REPO_ROOT / "skills" / "sigma-loop" / "scripts" / "scrub.py"

CODES = ("no-credential", "no-opt-in", "no-cap", "unsupported-platform", "pull-request-event",
         "subscription-in-gated-mode", "not-implemented", "bad-usage", "scrubber-unavailable")
DETAILS = {
    "no-credential": "neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set",
    "no-opt-in": "SIGMA_REGRESSION_LIVE is not set to 1, true, yes or on",
    "no-cap": ("a positive finite dollar cap is required (--cap-usd or SIGMA_REGRESSION_CAP_USD) and a "
               "belt (--belt-usd or SIGMA_REGRESSION_BELT_USD) must be positive, finite and not above it"),
    "unsupported-platform": "only POSIX hosts are supported",
    "pull-request-event": "refusing to run for a pull request event, or for an unnamed event in Actions",
    "subscription-in-gated-mode": "a gated run may not hold CLAUDE_CODE_OAUTH_TOKEN; use ANTHROPIC_API_KEY alone",
    "not-implemented": "no run path exists in this slice",
    "bad-usage": "unrecognised or malformed arguments; see --help",
    "scrubber-unavailable": "the shared scrubber could not be loaded",
}
PR_EVENTS = {"pull_request", "pull_request_target", "pull_request_review", "pull_request_review_comment"}
OPT_IN_VALUES = {"1", "true", "yes", "on"}
NUMBER = re.compile(r"[0-9]+(\.[0-9]{1,6})?")


class Failure(object):
    def __init__(self, code, detail, variable=None):
        self.code, self.detail, self.variable = code, detail, variable


class Options(object):
    def __init__(self, cap_text, belt_text, release_gate, results_dir):
        self.cap_text, self.belt_text = cap_text, belt_text
        self.release_gate, self.results_dir = release_gate, results_dir



class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Usage()


def build_parser():
    p = _Parser(prog="live.py", allow_abbrev=False,
                description="Live regression entrypoint (slice 1): refuses, records a NOT RUN row; never runs a model.")
    p.add_argument("--cap-usd", dest="cap_text", default=None, help="total spend cap in dollars")
    p.add_argument("--belt-usd", dest="belt_text", default=None, help="per-run belt in dollars (<= cap)")
    p.add_argument("--release-gate", dest="release_gate", action="store_true",
                   help="mark this run as feeding the release gate")
    p.add_argument("--results-dir", dest="results_dir", default=None,
                   help="where NOT RUN rows go (default: <repo root>/.sdlc/eval/results)")
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


def build_row(code, reason, also_failing, basis, cap, belt, started, finished, env):
    sha = str(env.get("GITHUB_SHA") or "")
    return {
        "schema": SCHEMA,
        "commit": sha if re.fullmatch(r"[0-9a-fA-F]{40}", sha) else "unknown",
        "cell": "live/%s/py%d.%d" % (sys.platform, sys.version_info[0], sys.version_info[1]),
        "credential_basis": basis,
        "permission_mode": "not-applicable",
        "status": "NOT RUN",
        "code": code,
        "reason": reason,
        "also_failing": list(also_failing),
        "representative": False,
        "model": None,
        "belt": belt,
        "cap": cap,
        "tokens": None,
        "cost": None,
        "cost_basis": None,
        "started": _stamp(started),
        "finished": _stamp(finished),
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


def _refuse(code, also, variable, basis, cap, belt, env, results_dir, started, scrubber, patterns):
    """Print the one refusal line, write the NOT RUN row, return the exit code."""
    detail = DETAILS[code]
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
    row = build_row(code, reason, also, basis, cap, belt, started, finished, env)
    try:
        text = redact(json.dumps(row, sort_keys=True, indent=2) + "\n", patterns)
        write_row(results_dir, text, finished)
    except Exception as exc:                                # noqa: BLE001 - the refusal must survive
        print("live.py: row not recorded (%s)" % type(exc).__name__, file=sys.stderr)
    return EXIT_REFUSED


def main(argv=None, env=None, posix=None):
    env = os.environ if env is None else env
    posix = platform_is_posix() if posix is None else posix
    started = _now()
    patterns = redaction_set(held_credentials(env))
    scrubber = load_scrubber()
    oauth, api = _nonblank(env, "CLAUDE_CODE_OAUTH_TOKEN"), _nonblank(env, "ANTHROPIC_API_KEY")
    basis = "api-key" if api else ("subscription" if oauth else "none")
    try:
        args = build_parser().parse_args(argv)
    except _Usage:
        return _refuse("bad-usage", [], None, basis, None, None, env, DEFAULT_RESULTS_DIR, started,
                       scrubber, patterns)
    opts = Options(args.cap_text, args.belt_text, args.release_gate, args.results_dir)
    results_dir = opts.results_dir if opts.results_dir is not None else DEFAULT_RESULTS_DIR
    basis, cap, belt, _gated, failures = evaluate(env, opts, posix)
    codes = [f.code for f in failures]
    if scrubber is None:
        return _refuse("scrubber-unavailable", codes, None, basis, cap, belt, env, results_dir, started,
                       None, patterns)
    if not failures:
        return _refuse("not-implemented", [], None, basis, cap, belt, env, results_dir, started,
                       scrubber, patterns)
    first = failures[0]
    return _refuse(first.code, codes[1:], first.variable, basis, cap, belt, env, results_dir, started,
                   scrubber, patterns)


if __name__ == "__main__":
    sys.exit(main())

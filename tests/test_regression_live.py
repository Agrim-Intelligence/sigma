"""`evals/regression/live.py` -- the live regression entrypoint, slice 1 (#884, part of #810).

This slice builds ONLY the refusal ladder, the NOT RUN result row and credential-safe logging. The
module never starts a model; every credential in this file is a FAKE built at run time (never a
literal), and every child process gets an ALLOWLISTED environment so a hosted runner's `CI`,
`GITHUB_EVENT_NAME` or a developer's real token cannot change which rung fires.

The documented gesture is the bare one, run as a subprocess with `sys.executable` and no flag:

    python3 evals/regression/live.py

The ladder, parsing and redaction tests call `main()` and `evaluate()` in process with an explicit
`env` mapping (the module reads nothing ambient there); the subprocess tests cover the bare gesture,
the environment isolation, the fake-`claude` marker and the failure paths.
"""
from __future__ import annotations

import ast
import base64
import importlib.util
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = ROOT / "evals" / "regression" / "live.py"
SCRUB = ROOT / "skills" / "sigma-loop" / "scripts" / "scrub.py"
WRITE_SURFACE = ROOT / "tools" / "readiness" / "write_surface.py"
INVENTORY = ROOT / "docs" / "launch" / "write-surface.json"
LIVE_REL = "evals/regression/live.py"

posix_only = pytest.mark.skipif(os.name != "posix", reason="the live entrypoint is POSIX-only")

AC1_LINE = ("live.py: REFUSED [no-credential]: "
            "neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set")
CHILD_ENV_KEYS = {"PATH", "HOME", "LANG"}
ROW_KEYS = {"schema", "commit", "cell", "credential_basis", "permission_mode", "status", "code",
            "reason", "also_failing", "representative", "model", "belt", "cap", "tokens", "cost",
            "cost_basis", "started", "finished"}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fake(n=16):
    """A fake credential of a shape the shared scrubber does not know, built at run time."""
    return "zq9-" + os.urandom(n).hex()


def make_tree(tmp):
    """Copy live.py and scrub.py into a repo-shaped temp tree; return the copied live.py path."""
    tree = pathlib.Path(tmp) / "tree"
    (tree / "evals" / "regression").mkdir(parents=True)
    (tree / "skills" / "sigma-loop" / "scripts").mkdir(parents=True)
    shutil.copy(str(LIVE), str(tree / "evals" / "regression" / "live.py"))
    shutil.copy(str(SCRUB), str(tree / "skills" / "sigma-loop" / "scripts" / "scrub.py"))
    return tree / "evals" / "regression" / "live.py"


def child_env(tmp, **extra):
    """The subprocess environment: an ALLOWLIST, nothing inherited."""
    fakebin = pathlib.Path(tmp) / "fakebin"
    fakebin.mkdir(exist_ok=True)
    home = pathlib.Path(tmp) / "home"
    home.mkdir(exist_ok=True)
    env = {"PATH": "%s:/usr/bin:/bin" % fakebin, "HOME": str(home), "LANG": "C.UTF-8"}
    env.update(extra)
    return env


def run_live(tree_live, tmp, args=(), **env):
    cwd = pathlib.Path(tmp) / "elsewhere"
    cwd.mkdir(exist_ok=True)
    return subprocess.run([sys.executable, str(tree_live)] + list(args), cwd=str(cwd),
                          env=child_env(tmp, **env), capture_output=True, text=True)


def rows_of(tree_live):
    results = tree_live.parents[2] / ".sdlc" / "eval" / "results"
    return sorted(results.glob("live-*.json")) if results.is_dir() else []


@pytest.fixture(scope="module")
def live():
    return _load("regression_live_under_test", LIVE)


@pytest.fixture
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield pathlib.Path(d)


def valid_env(**extra):
    env = {"ANTHROPIC_API_KEY": fake(), "SIGMA_REGRESSION_LIVE": "1", "SIGMA_REGRESSION_CAP_USD": "5"}
    env.update(extra)
    return {k: v for k, v in env.items() if v is not None}


class Called:
    def __init__(self, rc, out, err, row, lines):
        self.rc, self.out, self.err, self.row, self.lines = rc, out, err, row, lines


def call(live, tmp, capsys, env, argv=(), posix=True):
    """Run main() in process with an explicit env; the row lands in a fresh temp dir."""
    results = pathlib.Path(tempfile.mkdtemp(dir=str(tmp)))
    capsys.readouterr()
    saved = live.DEFAULT_RESULTS_DIR
    live.DEFAULT_RESULTS_DIR = results          # a usage error writes to the default dir; keep it in temp
    try:
        rc = live.main(["--results-dir", str(results)] + list(argv), env=env, posix=posix)
    finally:
        live.DEFAULT_RESULTS_DIR = saved
    cap = capsys.readouterr()
    files = sorted(results.glob("live-*.json"))
    row = json.loads(files[0].read_text()) if files else None
    assert len(files) <= 1
    return Called(rc, cap.out, cap.err, row, [x for x in cap.err.splitlines() if x])


# --------------------------------------------------------------------------- Task 1

@posix_only
def test_bare_gesture_refuses_no_credential(tmp):
    tree = make_tree(tmp)
    r = run_live(tree, tmp)
    assert r.returncode == 2
    assert r.stdout == ""
    assert r.stderr == AC1_LINE + "\n"
    rows = rows_of(tree)
    assert len(rows) == 1                      # anchored to the (temp) repo root, not the cwd
    assert not (tmp / "elsewhere" / ".sdlc").exists()
    row = json.loads(rows[0].read_text())
    assert row["code"] == "no-credential" and row["status"] == "NOT RUN"
    assert row["credential_basis"] == "none"
    assert row["also_failing"] == ["no-opt-in", "no-cap"]
    assert re.fullmatch(r"live-\d{8}T\d{6}Z-\d+-[0-9a-f]{4}\.json", rows[0].name)


@posix_only
def test_row_has_exactly_the_contract_fields(tmp):
    tree = make_tree(tmp)
    run_live(tree, tmp)
    row = json.loads(rows_of(tree)[0].read_text())
    assert set(row) == ROW_KEYS
    assert row["schema"] == "sigma.regression-result/v1"
    assert row["status"] == "NOT RUN"
    assert row["permission_mode"] == "not-applicable"
    assert row["representative"] is False
    for k in ("model", "belt", "cap", "tokens", "cost", "cost_basis"):
        assert row[k] is None
    assert re.fullmatch(r"live/\w+/py\d+\.\d+", row["cell"])
    assert row["commit"] == "unknown"
    for k in ("started", "finished"):
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", row[k])


@posix_only
def test_help_exits_zero_without_ladder_or_row(tmp):
    tree = make_tree(tmp)
    r = run_live(tree, tmp, ["--help"])
    assert r.returncode == 0
    assert "usage" in r.stdout.lower()
    assert "REFUSED" not in r.stderr
    assert rows_of(tree) == [] and not (tree.parents[2] / ".sdlc").exists()


# --------------------------------------------------------------------------- Task 2

BREAKS = [
    ("no-credential", {"ANTHROPIC_API_KEY": None}, "no-credential", []),
    ("no-opt-in", {"SIGMA_REGRESSION_LIVE": "0"}, "no-opt-in", []),
    ("no-cap", {"SIGMA_REGRESSION_CAP_USD": None}, "no-cap", []),
    ("pr-event", {"GITHUB_EVENT_NAME": "pull_request"}, "pull-request-event", []),
    ("sub-gated", {"ANTHROPIC_API_KEY": None, "CLAUDE_CODE_OAUTH_TOKEN": "OAUTH", "CI": "true"},
     "subscription-in-gated-mode", []),
    ("three", {"ANTHROPIC_API_KEY": None, "SIGMA_REGRESSION_LIVE": None, "SIGMA_REGRESSION_CAP_USD": None},
     "no-credential", ["no-opt-in", "no-cap"]),
    ("two", {"SIGMA_REGRESSION_LIVE": None, "SIGMA_REGRESSION_CAP_USD": None},
     "no-opt-in", ["no-cap"]),
    ("late-three", {"ANTHROPIC_API_KEY": None, "CLAUDE_CODE_OAUTH_TOKEN": "OAUTH", "CI": "true",
                    "GITHUB_EVENT_NAME": "pull_request", "SIGMA_REGRESSION_CAP_USD": None},
     "no-cap", ["pull-request-event", "subscription-in-gated-mode"]),
]


@pytest.mark.parametrize("name,change,code,also", BREAKS, ids=[b[0] for b in BREAKS])
def test_ladder_first_failing_code(live, tmp, capsys, name, change, code, also):
    env = valid_env(**change)
    r = call(live, tmp, capsys, env)
    assert r.rc == 2 and r.out == ""
    assert r.row["code"] == code and r.row["also_failing"] == also
    assert r.lines[0].startswith("live.py: REFUSED [%s]: " % code) and len(r.lines) == 1


def test_ladder_order_is_the_documented_one(live):
    assert tuple(live.CODES[:7]) == ("no-credential", "no-opt-in", "no-cap", "unsupported-platform",
                                     "pull-request-event", "subscription-in-gated-mode", "not-implemented")
    assert set(live.CODES) == {"no-credential", "no-opt-in", "no-cap", "unsupported-platform",
                               "pull-request-event", "subscription-in-gated-mode", "not-implemented",
                               "bad-usage", "scrubber-unavailable"}


CAP_REJECTED = ["nan", "inf", "-1", "0", "0x10", "1e3", "1e999", "True", "", "9" * 400, " ", "5 5", "+5"]


@pytest.mark.parametrize("text", CAP_REJECTED)
def test_cap_belt_and_opt_in_parsing(live, tmp, capsys, text):
    r = call(live, tmp, capsys, valid_env(SIGMA_REGRESSION_CAP_USD=None), ["--cap-usd=" + text])
    assert r.row["code"] == "no-cap", text
    assert r.rc == 2


def test_cap_belt_accepts_and_precedence(live, tmp, capsys):
    base = valid_env(SIGMA_REGRESSION_CAP_USD=None)
    for ok in ("5", "0.5", "  5  "):
        assert call(live, tmp, capsys, base, ["--cap-usd=" + ok]).row["code"] == "not-implemented"
    # the two-token negative form: argparse reads '-1' as the VALUE, so it reaches the grammar
    assert call(live, tmp, capsys, base, ["--cap-usd", "-1"]).row["code"] == "no-cap"
    # a value argparse reads as a flag is a usage error, not a refusal on the cap
    for bad in ("-x", "-1e3"):
        r = call(live, tmp, capsys, base, ["--cap-usd", bad])
        assert r.rc == 2 and r.lines[0].startswith("live.py: REFUSED [bad-usage]: ")
    assert call(live, tmp, capsys, base, ["--cap-usd=5", "--belt-usd=6"]).row["code"] == "no-cap"
    assert call(live, tmp, capsys, base, ["--cap-usd=5", "--belt-usd=5"]).row["code"] == "not-implemented"
    assert call(live, tmp, capsys, base, ["--cap-usd=5", "--belt-usd=x"]).row["code"] == "no-cap"
    assert call(live, tmp, capsys, base, ["--cap-usd=5", "--belt-usd=0.5"]).row["belt"] == 0.5
    # flag beats env, both directions
    assert call(live, tmp, capsys, valid_env(SIGMA_REGRESSION_CAP_USD="x"),
                ["--cap-usd=5"]).row["code"] == "not-implemented"
    assert call(live, tmp, capsys, valid_env(), ["--cap-usd=x"]).row["code"] == "no-cap"
    assert call(live, tmp, capsys, valid_env(SIGMA_REGRESSION_BELT_USD="9"), []).row["code"] == "no-cap"
    r = call(live, tmp, capsys, valid_env())
    assert r.row["cap"] == 5.0
    # opt-in allowlist: only 1/true/yes/on (any case, stripped)
    for v in ("1", "true", "YES", " on ", "True"):
        assert call(live, tmp, capsys, valid_env(SIGMA_REGRESSION_LIVE=v)).row["code"] == "not-implemented"
    for v in ("enable", "2", "", " ", "0", "no", "y"):
        assert call(live, tmp, capsys, valid_env(SIGMA_REGRESSION_LIVE=v)).row["code"] == "no-opt-in", v


def test_event_rung(live, tmp, capsys):
    for ev in ("pull_request", "pull_request_target", "pull_request_review",
               "pull_request_review_comment", " Pull_Request ", "PULL_REQUEST"):
        assert call(live, tmp, capsys, valid_env(GITHUB_EVENT_NAME=ev)).row["code"] == "pull-request-event", ev
    for blank in (None, "", "  "):
        r = call(live, tmp, capsys, valid_env(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME=blank))
        assert r.row["code"] == "pull-request-event"
    for ev in ("schedule", "workflow_dispatch"):
        r = call(live, tmp, capsys, valid_env(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME=ev))
        assert r.row["code"] == "not-implemented", ev
    # outside Actions a blank event is fine
    assert call(live, tmp, capsys, valid_env()).row["code"] == "not-implemented"


def test_subscription_in_gated_mode(live, tmp, capsys):
    oauth = fake()
    only_oauth = valid_env(ANTHROPIC_API_KEY=None, CLAUDE_CODE_OAUTH_TOKEN=oauth)
    ev = {"GITHUB_EVENT_NAME": "workflow_dispatch"}
    gates = [{"CI": "true"}, {"GITHUB_ACTIONS": "true"}, {"CI": "1"}, {"SIGMA_REGRESSION_RELEASE_GATE": "1"}]
    for g in gates:
        r = call(live, tmp, capsys, dict(only_oauth, **dict(ev, **g)))
        assert r.row["code"] == "subscription-in-gated-mode", g
        assert r.row["credential_basis"] == "subscription"
    r = call(live, tmp, capsys, only_oauth, ["--release-gate"])
    assert r.row["code"] == "subscription-in-gated-mode"
    # ungated subscription passes the ladder and records its basis
    r = call(live, tmp, capsys, only_oauth)
    assert r.row["code"] == "not-implemented" and r.row["credential_basis"] == "subscription"
    # a falsy gate value does not gate
    r = call(live, tmp, capsys, dict(only_oauth, CI="false"))
    assert r.row["code"] == "not-implemented"
    # API key alone passes while gated
    r = call(live, tmp, capsys, valid_env(CI="true", **ev))
    assert r.row["code"] == "not-implemented" and r.row["credential_basis"] == "api-key"
    # both set: gated refuses, ungated records api-key and notes both
    both = valid_env(CLAUDE_CODE_OAUTH_TOKEN=oauth)
    assert call(live, tmp, capsys, dict(both, CI="true", **ev)).row["code"] == "subscription-in-gated-mode"
    r = call(live, tmp, capsys, both)
    assert r.row["credential_basis"] == "api-key" and "both-credentials" in r.row["reason"]


def test_platform_rung_via_seam(live, tmp, capsys):
    from_env = valid_env()
    opts = live.Options(cap_text=None, belt_text=None, release_gate=False, results_dir=str(tmp))
    basis, cap, belt, gated, failures = live.evaluate(from_env, opts, False)
    assert [f.code for f in failures] == ["unsupported-platform"] and gated is False
    _, _, _, _, failures = live.evaluate(valid_env(SIGMA_REGRESSION_CAP_USD=None,
                                                   GITHUB_EVENT_NAME="pull_request"), opts, False)
    assert [f.code for f in failures] == ["no-cap", "unsupported-platform", "pull-request-event"]
    r = call(live, tmp, capsys, from_env, posix=False)
    assert r.row["code"] == "unsupported-platform" and r.rc == 2
    assert live.evaluate(from_env, opts, True)[4] == []


def test_terminal_not_implemented_after_ladder_passes(live, tmp, capsys):
    r = call(live, tmp, capsys, valid_env())
    assert r.rc == 2 and r.out == ""
    assert r.lines == ["live.py: REFUSED [not-implemented]: " + live.DETAILS["not-implemented"]]
    assert r.row["code"] == "not-implemented" and r.row["also_failing"] == []
    assert r.row["status"] == "NOT RUN" and r.row["cap"] == 5.0


@posix_only
def test_bad_usage_is_its_own_code(tmp):
    tree = make_tree(tmp)
    for argv in (["--bogus"], ["--cap=5"], ["positional"], ["--cap-usd"]):
        r = run_live(tree, tmp, argv)
        assert r.returncode == 2 and r.stdout == ""
        lines = [x for x in r.stderr.splitlines() if x]
        assert lines == ["live.py: REFUSED [bad-usage]: unrecognised or malformed arguments; see --help"]
    rows = [json.loads(p.read_text()) for p in rows_of(tree)]
    assert len(rows) == 4 and {r["code"] for r in rows} == {"bad-usage"}


# --------------------------------------------------------------------------- Task 3

def test_redaction_precondition_scrubber_alone_leaves_planted_value():
    scrub = _load("scrub_for_precondition", SCRUB)
    planted = fake(24)
    assert len(planted) >= 40
    assert scrub.scrub(planted) == planted
    assert scrub.scrub(repr(planted)[:40]) == repr(planted)[:40]
    shape = "(got " + repr(planted)[:40] + ")"
    assert scrub.scrub(shape) == shape


@posix_only
def test_planted_unknown_shape_credential_is_redacted_everywhere(tmp):
    tree = make_tree(tmp)
    planted = fake(24)
    r = run_live(tree, tmp, ["--cap-usd=" + planted], ANTHROPIC_API_KEY=planted, SIGMA_REGRESSION_LIVE="1")
    assert r.returncode == 2
    row_text = rows_of(tree)[0].read_text()
    assert json.loads(row_text)["code"] == "no-cap"
    for blob in (r.stdout, r.stderr, row_text):
        assert planted not in blob and planted[:40] not in blob
    assert "[REDACTED]" in r.stderr and "[REDACTED]" in row_text


@posix_only
def test_cap_echo_redacts_before_cutting(live, tmp):
    tree = make_tree(tmp)
    planted = fake(28)
    assert len(planted) == 60
    r = run_live(tree, tmp, ["--cap-usd=" + planted], ANTHROPIC_API_KEY=planted, SIGMA_REGRESSION_LIVE="1")
    blob = r.stdout + r.stderr + rows_of(tree)[0].read_text()
    assert not any(planted[i:i + 12] in blob for i in range(len(planted) - 11))
    long_value = "zq9-" + os.urandom(98).hex()
    assert len(long_value) == 200
    scrubber = _load("scrub_for_echo", SCRUB)
    out = live.echo(long_value, live.redaction_set([long_value]), scrubber)
    assert long_value[:12] not in out and "[REDACTED]" in out


def test_redaction_forms_and_minimum_length(live):
    v = "zq9 a/b+" + os.urandom(8).hex()
    forms = {v, urllib.parse.quote(v), urllib.parse.quote(v, safe=""), urllib.parse.quote_plus(v),
             shlex.quote(v)}
    raw = v.encode()
    for enc in (base64.b64encode, base64.urlsafe_b64encode):
        padded = enc(raw).decode()
        forms.add(padded)
        forms.add(padded.rstrip("="))
    patterns = live.redaction_set([v])
    lens = [len(x) for x in patterns]
    assert lens == sorted(lens, reverse=True) and len(set(patterns)) == len(patterns)
    for form in forms:
        out = live.redact("before %s after" % form, patterns)
        assert form not in out, form
        assert "before " in out and " after" in out
    # a below-minimum value is not redacted globally
    assert live.redaction_set(["abc", "1234567"]) == []
    assert live.redact("abc 1234567 abc", live.redaction_set(["abc"])) == "abc 1234567 abc"
    assert live.redaction_set(["12345678"]) != []


@pytest.mark.parametrize("shape", ["trailing-newline", "leading-spaces"])
def test_stripped_form_of_a_held_credential_is_redacted(live, tmp, capsys, shape):
    stripped = fake(24)
    assert len(stripped) >= 40
    held = stripped + "\n" if shape == "trailing-newline" else "   " + stripped
    env = valid_env(ANTHROPIC_API_KEY=held, SIGMA_REGRESSION_CAP_USD=None)
    r = call(live, tmp, capsys, env, ["--cap-usd=" + stripped])
    assert r.rc == 2 and r.row["code"] == "no-cap"
    blob = r.err + json.dumps(r.row)
    assert stripped[:30] not in blob and stripped not in blob
    assert "[REDACTED]" in r.err


def test_final_exact_value_passes_cover_each_line_and_the_row(live, tmp, capsys, monkeypatch):
    """Seam: the fixed detail text is made to carry the held value, so only the two final whole-text
    passes (printed line; serialised row) can remove it. The echo path never sees this text."""
    planted = fake(24)
    monkeypatch.setitem(live.DETAILS, "not-implemented", "leak " + planted)
    r = call(live, tmp, capsys, valid_env(ANTHROPIC_API_KEY=planted))
    assert r.row["code"] == "not-implemented"
    assert planted not in r.err, "printed-line pass missing"
    assert "[REDACTED]" in r.err
    assert planted not in json.dumps(r.row), "serialised-row pass missing"
    assert "[REDACTED]" in r.row["reason"]


def test_surrogate_escaped_credential_is_still_redacted_and_the_refusal_holds(live, tmp, capsys):
    body = os.urandom(20).hex()
    value = ("zq9-" + body).encode("ascii") + b"\xff\xfe"
    value = value.decode("utf-8", "surrogateescape")         # what os.environ yields for non-UTF-8 bytes
    env = valid_env(ANTHROPIC_API_KEY=value, SIGMA_REGRESSION_CAP_USD=None)
    r = call(live, tmp, capsys, env, ["--cap-usd=" + value])
    assert r.rc == 2 and r.row["code"] == "no-cap"
    assert len(r.lines) == 1 and r.lines[0].startswith("live.py: REFUSED [no-cap]: ")
    assert body not in r.err and body not in json.dumps(r.row)
    assert "[REDACTED]" in r.err


def test_blank_belt_env_is_unset_but_a_blank_belt_flag_refuses(live, tmp, capsys):
    """A blank SIGMA_REGRESSION_BELT_USD (how an unset CI variable arrives) is treated as unset, as a blank
    opt-in is treated as not opted in; the belt is optional so unset passes. A blank --belt-usd= flag is
    typed deliberately and malformed, so it refuses no-cap. A blank cap is unset and refuses (cap required)."""
    for blank in ("", "   "):
        r = call(live, tmp, capsys, valid_env(SIGMA_REGRESSION_BELT_USD=blank))
        assert r.row["code"] == "not-implemented" and r.row["belt"] is None, repr(blank)
    assert call(live, tmp, capsys, valid_env(), ["--belt-usd="]).row["code"] == "no-cap"
    assert call(live, tmp, capsys, valid_env(SIGMA_REGRESSION_CAP_USD="")).row["code"] == "no-cap"
    assert call(live, tmp, capsys, valid_env(SIGMA_REGRESSION_BELT_USD="x")).row["code"] == "no-cap"


EXPECTED_SCENARIOS = {
    "no-credential": (lambda: {}, {}, True),
    "no-opt-in": (lambda: {"ANTHROPIC_API_KEY": fake()}, {}, True),
    "no-cap": (lambda: {"ANTHROPIC_API_KEY": fake(), "SIGMA_REGRESSION_LIVE": "1"}, {}, True),
    "unsupported-platform": (valid_env, {}, False),
    "pull-request-event": (lambda: valid_env(GITHUB_EVENT_NAME="pull_request"), {}, True),
    "subscription-in-gated-mode": (lambda: valid_env(ANTHROPIC_API_KEY=None, CLAUDE_CODE_OAUTH_TOKEN=fake(),
                                                    CI="true"), {}, True),
    "not-implemented": (valid_env, {}, True),
    "bad-usage": (lambda: {}, {"argv": ["--bogus"]}, True),
    "scrubber-unavailable": (valid_env, {"scrubber": None}, True),
}


@pytest.mark.parametrize("code", sorted(EXPECTED_SCENARIOS))
def test_refusal_lines_are_emitted_intact(live, tmp, capsys, monkeypatch, code):
    assert code in live.CODES
    make_env, opts, posix = EXPECTED_SCENARIOS[code]
    if "scrubber" in opts:
        monkeypatch.setattr(live, "load_scrubber", lambda: None)
    argv = opts.get("argv", [])
    results = pathlib.Path(tempfile.mkdtemp(dir=str(tmp)))
    # a bad-usage run writes to the DEFAULT dir; keep it out of the real tree by pointing the default
    monkeypatch.setattr(live, "DEFAULT_RESULTS_DIR", results)
    capsys.readouterr()
    rc = live.main(argv, env=make_env(), posix=posix)
    err = capsys.readouterr().err
    expected = "live.py: REFUSED [%s]: %s" % (code, live.DETAILS[code])
    assert rc == 2 and err == expected + "\n"
    if code == "no-credential":
        assert err == AC1_LINE + "\n"
    row = json.loads(sorted(results.glob("live-*.json"))[0].read_text())
    assert row["code"] == code and row["reason"] == live.DETAILS[code]


@posix_only
def test_failure_paths_keep_the_refusal(live, tmp, capsys, monkeypatch):
    tree = make_tree(tmp)
    blocker = tmp / "blocker"
    blocker.write_text("x")
    r = run_live(tree, tmp, ["--results-dir", str(blocker)])
    lines = [x for x in r.stderr.splitlines() if x]
    assert r.returncode == 2 and r.stdout == ""
    assert lines[0] == AC1_LINE and len(lines) == 2
    assert re.fullmatch(r"live\.py: row not recorded \(\w+\)", lines[1])
    assert "Traceback" not in r.stderr
    assert not list(tmp.glob("blocker*.tmp*"))
    # a relative --results-dir lands under the cwd
    r = run_live(tree, tmp, ["--results-dir", "rel-rows"])
    assert r.returncode == 2
    assert len(list((tmp / "elsewhere" / "rel-rows").glob("live-*.json"))) == 1
    # a missing scrubber refuses with its own code
    monkeypatch.setattr(live, "load_scrubber", lambda: None)
    c = call(live, tmp, capsys, valid_env())
    assert c.rc == 2 and c.row["code"] == "scrubber-unavailable"
    assert c.lines == ["live.py: REFUSED [scrubber-unavailable]: " + live.DETAILS["scrubber-unavailable"]]


# --------------------------------------------------------------------------- Task 4

@posix_only
def test_bare_gesture_survives_hostile_parent_environment(tmp, monkeypatch):
    hostile_token = fake()
    for k, v in {"CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request",
                 "CLAUDE_CODE_OAUTH_TOKEN": hostile_token, "SIGMA_REGRESSION_LIVE": "1"}.items():
        monkeypatch.setenv(k, v)
    assert set(child_env(tmp)) == CHILD_ENV_KEYS
    tree = make_tree(tmp)
    r = run_live(tree, tmp)
    assert r.returncode == 2 and r.stderr == AC1_LINE + "\n"
    row_text = rows_of(tree)[0].read_text()
    assert json.loads(row_text)["code"] == "no-credential"
    for blob in (r.stdout, r.stderr, row_text):
        assert hostile_token not in blob


# --------------------------------------------------------------------------- Task 5

def _source():
    return LIVE.read_text(encoding="utf-8")


def _docstring_nodes(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _imports(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


ALLOWED_IMPORTS = {"__future__", "argparse", "base64", "datetime", "importlib.util", "json", "math", "os",
                   "pathlib", "re", "secrets", "shlex", "sys", "urllib.parse"}
DENIED_ROOTS = {"subprocess", "pty", "multiprocessing", "ctypes", "shutil", "socket", "http"}


def test_live_py_imports_only_allowlisted_modules():
    extra = [m for m in _imports(ast.parse(_source())) if m not in ALLOWED_IMPORTS]
    assert extra == []


def test_live_py_imports_no_process_spawning_or_bench_arms():
    tree = ast.parse(_source())
    for m in _imports(tree):
        assert m.split(".")[0] not in DENIED_ROOTS, m
        assert m != "urllib.request" and "bench" not in m and "arms" not in m, m
    docs = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            assert "evals/bench" not in node.value and not re.search(r"\barms\b", node.value), node.value


def _banned_os_name(name):
    return name.startswith(("exec", "spawn", "fork", "posix_spawn")) or name in {
        "system", "popen", "kill", "killpg", "startfile"}


def _spawn_calls(src):
    """Static spawn guard. Catches: os.<spawner>(...), `from os import <spawner>` (or `*`),
    __import__/exec/eval, any `.import_module(...)`, and getattr(os, <non-literal or spawner name>).
    NOT caught (static analysis cannot): an alias (`o = os; o.system(...)`), `vars(os)[...]`,
    `sys.modules[...]`. The real backstop for those is the dynamic fake-`claude`-on-PATH test below."""
    bad = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            bad += ["from os import " + a.name for a in node.names if a.name == "*" or _banned_os_name(a.name)]
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in {"__import__", "exec", "eval"}:
            bad.append(f.id)
        if isinstance(f, ast.Name) and f.id == "getattr" and node.args \
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "os":
            arg = node.args[1] if len(node.args) > 1 else None
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)) \
                    or _banned_os_name(arg.value):
                bad.append("getattr(os, ...)")
        if isinstance(f, ast.Attribute):
            if isinstance(f.value, ast.Name) and f.value.id == "os" and _banned_os_name(f.attr):
                bad.append("os." + f.attr)
            if f.attr == "import_module":
                bad.append("import_module")
    return bad


SPAWN_BANNED = {
    'os.popen("x")': ["os.popen"], "os.execv('a', [])": ["os.execv"],
    "from os import system": ["from os import system"], "from os import execv": ["from os import execv"],
    "from os import posix_spawn": ["from os import posix_spawn"],
    "from os import fork": ["from os import fork"], "from os import *": ["from os import *"],
    'importlib.import_module("subprocess")': ["import_module"],
    '__import__("subprocess")': ["__import__"],
    "getattr(os, name)(x)": ["getattr(os, ...)"], 'getattr(os, "system")("x")': ["getattr(os, ...)"],
}
SPAWN_ALLOWED = ["from os import path", 'getattr(os, "name")', "getattr(obj, name)", "os.replace(a, b)",
                 "importlib.util.spec_from_file_location(a, b)"]


def test_live_py_calls_no_process_spawning_apis():
    assert _spawn_calls(_source()) == []
    for form, expected in SPAWN_BANNED.items():
        assert _spawn_calls(form) == expected, form
    for form in SPAWN_ALLOWED:
        assert _spawn_calls(form) == [], form


WRITE_ATTRS = {"write_text", "write_bytes", "mkdir", "touch", "rename", "unlink", "rmdir", "symlink_to",
               "hardlink_to", "writelines"}
BARE_BANNED = {"open", "compile", "exec", "eval", "__import__"}
OS_ALWAYS = {"open", "fdopen", "pwrite", "truncate", "ftruncate", "sendfile"}


def walk_violations(src):
    """One walker, shared by the real-file tests and the synthetic self-test."""
    found = []

    def visit(node, fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = node.name
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in BARE_BANNED:
                found.append(f.id)
            elif isinstance(f, ast.Attribute):
                recv_os = isinstance(f.value, ast.Name) and f.value.id == "os"
                if f.attr in ("open", "write"):
                    found.append("." + f.attr)
                elif recv_os and (f.attr in OS_ALWAYS or f.attr.startswith("write")):
                    found.append("os." + f.attr)
                elif f.attr in WRITE_ATTRS and fn != "write_row":
                    found.append("." + f.attr)
                elif recv_os and f.attr == "replace" and fn != "write_row":
                    found.append("os.replace")
        for child in ast.iter_child_nodes(node):
            visit(child, fn)

    visit(ast.parse(src), None)
    return found


def test_live_py_has_no_append_mode_open_or_os_write():
    assert walk_violations(_source()) == []


BANNED_FORMS = ['open(p, "a")', 'Path(p).open("a")', "io.open(p)", "os.open(p, 0)", "os.fdopen(3)",
                'os.write(fd, b"")', "os.truncate(p, 0)", "os.replace(a, b)", 'p.write_text("")',
                'sys.stderr.write("")', 'compile(s, "", "exec")', "exec(s)", "eval(s)", "__import__(s)"]
PERMITTED_FORMS = ['re.compile(r"x")', "re.sub(re.escape(v), f, t)", 'print("x", file=sys.stderr)',
                   "json.dumps(o)"]


@pytest.mark.parametrize("form", BANNED_FORMS)
def test_scanner_walker_flags_append_open_and_os_write(form):
    assert walk_violations(form) != [], form


def test_scanner_walker_permits_the_allowed_forms():
    for form in PERMITTED_FORMS:
        assert walk_violations(form) == [], form
    inside = "def write_row(a, b, p):\n    os.replace(a, b)\n    p.write_text('')\n    p.mkdir()\n    p.unlink()\n"
    assert walk_violations(inside) == []
    assert walk_violations(inside.replace("write_row", "other")) != []


def test_write_methods_only_in_write_row():
    src = _source()
    assert walk_violations(src) == []
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "write_row" in names
    owners = set()
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for call in ast.walk(fn):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                    and call.func.attr in WRITE_ATTRS | {"replace"} and fn.name != "write_row":
                if call.func.attr == "replace" and not (isinstance(call.func.value, ast.Name)
                                                         and call.func.value.id == "os"):
                    continue
                owners.add((fn.name, call.func.attr))
    assert owners == set()


@posix_only
@pytest.mark.parametrize("scenario", ["bare", "no-opt-in", "no-cap", "pr-event", "sub-gated", "terminal"])
def test_fake_claude_on_path_is_never_run(tmp, scenario):
    tree = make_tree(tmp)
    marker = tmp / "claude-ran"
    fakebin = tmp / "fakebin"
    fakebin.mkdir()
    exe = fakebin / "claude"
    exe.write_text("#!/bin/sh\necho ran > '%s'\n" % marker)
    exe.chmod(0o755)
    key = fake()
    envs = {
        "bare": {},
        "no-opt-in": {"ANTHROPIC_API_KEY": key},
        "no-cap": {"ANTHROPIC_API_KEY": key, "SIGMA_REGRESSION_LIVE": "1"},
        "pr-event": {"ANTHROPIC_API_KEY": key, "SIGMA_REGRESSION_LIVE": "1",
                     "SIGMA_REGRESSION_CAP_USD": "5", "GITHUB_EVENT_NAME": "pull_request"},
        "sub-gated": {"CLAUDE_CODE_OAUTH_TOKEN": key, "SIGMA_REGRESSION_LIVE": "1",
                      "SIGMA_REGRESSION_CAP_USD": "5", "CI": "true"},
        "terminal": {"ANTHROPIC_API_KEY": key, "SIGMA_REGRESSION_LIVE": "1", "SIGMA_REGRESSION_CAP_USD": "5"},
    }
    r = run_live(tree, tmp, **envs[scenario])
    assert r.returncode == 2
    assert not marker.exists()
    if scenario == "terminal":
        assert "[not-implemented]" in r.stderr


# --------------------------------------------------------------------------- Task 6

def test_write_surface_inventory_matches_live_py():
    ws = _load("write_surface_for_live", WRITE_SURFACE)
    scanned = {(f["function"], f["rule"]): f["count"] for f in ws.scan_paths(ROOT, [LIVE])}
    assert scanned, "live.py has a write site; the scan found none"
    listed = {(e["function"], e["rule"]): e["count"]
              for e in json.loads(INVENTORY.read_text())["entries"] if e["path"] == LIVE_REL}
    assert listed == scanned
    assert {fn for fn, _ in scanned} == {"write_row"}


# --------------------------------------------------------------------------- review fix round 2

def test_oauth_token_value_is_redacted_everywhere(live, tmp, capsys):
    """Every other redaction test plants ANTHROPIC_API_KEY; the OAuth variable has its own entry."""
    held = fake(16)
    env = valid_env(ANTHROPIC_API_KEY=None, CLAUDE_CODE_OAUTH_TOKEN=held, SIGMA_REGRESSION_CAP_USD=None)
    r = call(live, tmp, capsys, env, ["--cap-usd=" + held])
    assert r.rc == 2 and r.row["code"] == "no-cap" and r.row["credential_basis"] == "subscription"
    assert held not in r.err and held not in json.dumps(r.row)
    assert "[REDACTED]" in r.err and "[REDACTED]" in r.row["reason"]


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n", " \t\n "])
@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])
def test_blank_or_whitespace_credential_is_no_credential(live, tmp, capsys, name, blank):
    env = valid_env(**dict({"ANTHROPIC_API_KEY": None}, **{name: blank}))
    r = call(live, tmp, capsys, env)
    assert r.rc == 2 and r.row["code"] == "no-credential" and r.row["credential_basis"] == "none"
    assert r.lines[0].startswith("live.py: REFUSED [no-credential]: ")


def test_both_credentials_blank_is_no_credential(live, tmp, capsys):
    env = valid_env(ANTHROPIC_API_KEY=" ", CLAUDE_CODE_OAUTH_TOKEN="\t")
    assert call(live, tmp, capsys, env).row["code"] == "no-credential"


def test_scrubber_pass_runs_on_a_rejected_cap_text(live, tmp, capsys):
    """A scrubber-shaped value that is NOT the held credential survives exact-value redaction, so only
    the shared scrubber pass inside echo can remove it."""
    shaped = "sk-ant-" + os.urandom(12).hex()
    held = fake()
    assert shaped not in held and held not in shaped
    env = valid_env(ANTHROPIC_API_KEY=held, SIGMA_REGRESSION_CAP_USD=None)
    r = call(live, tmp, capsys, env, ["--cap-usd=" + shaped])
    assert r.rc == 2 and r.row["code"] == "no-cap"
    blob = r.err + json.dumps(r.row)
    assert shaped not in blob and shaped[7:] not in blob
    assert "REDACTED" in r.err


def test_rejected_cap_echo_is_cut_to_a_bound(live, tmp, capsys):
    text = "x" * 200
    env = valid_env(SIGMA_REGRESSION_CAP_USD=None)
    r = call(live, tmp, capsys, env, ["--cap-usd=" + text])
    assert r.row["code"] == "no-cap"
    assert "x" * 30 in r.err                             # the head is still shown
    assert "x" * (live.ECHO_CUT + 1) not in r.err
    assert "x" * (live.ECHO_CUT + 1) not in r.row["reason"]
    assert len(r.lines[0]) < len(live.DETAILS["no-cap"]) + 120
    assert len(r.row["reason"]) < len(live.DETAILS["no-cap"]) + 120


def _value_with_plus_or_slash(length):
    """ASCII value whose standard base64 holds both '+' and '/': a '>' (0x3e) as the third byte of a
    group encodes to '+', a '?' (0x3f) to '/'; then a counter keeps it unique and unknown-shaped."""
    head = "zq>xy?"
    return (head + "".join("%d" % (i % 10) for i in range(length)))[:length]


@pytest.mark.parametrize("length", [10, 31, 32, 40, 41, 61])
@pytest.mark.parametrize("encoder", ["b64-padded", "b64-unpadded", "urlsafe-padded", "urlsafe-unpadded"])
def test_every_base64_form_of_a_value_is_redacted(live, length, encoder):
    assert length % 3 != 0
    value = _value_with_plus_or_slash(length)
    raw = value.encode("utf-8")
    std, url = base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode()
    assert "+" in std and "/" in std and std != url     # the two alphabets differ
    assert (std.endswith("=")) == (length % 3 != 0)
    form = {"b64-padded": std, "b64-unpadded": std.rstrip("="),
            "urlsafe-padded": url, "urlsafe-unpadded": url.rstrip("=")}[encoder]
    patterns = live.redaction_set([value])
    out = live.redact("before %s after" % form, patterns)
    assert out == "before [REDACTED] after"             # no residue: a dropped padded form leaves '='


GOOD_SHA = "0123456789abcdef0123456789ABCDEF01234567"


def test_github_sha_reaches_the_row_only_when_forty_hex(live, tmp, capsys):
    assert len(GOOD_SHA) == 40
    assert call(live, tmp, capsys, valid_env(GITHUB_SHA=GOOD_SHA)).row["commit"] == GOOD_SHA
    planted = fake(24)
    for bad in ("abc1234", "g" * 40, GOOD_SHA + "0", GOOD_SHA[:39], "", " " + GOOD_SHA,
                "not a sha at all", planted, "a" * 40 + "\n"):
        r = call(live, tmp, capsys, valid_env(ANTHROPIC_API_KEY=planted, GITHUB_SHA=bad))
        assert r.row["commit"] == "unknown", repr(bad)
        assert planted not in json.dumps(r.row)

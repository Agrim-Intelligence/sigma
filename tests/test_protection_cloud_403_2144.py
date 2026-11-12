"""#2144: a credential that may not read classic branch protection (a cloud session's App-style token answers
HTTP 403 `Resource not accessible by integration`) used to make `protection()` raise ProtectionUndetermined for
EVERY base, so `work.auto_merge: always` could never merge there.

The fallback is a conjunction of three clauses, and each has its own test that goes red when only that clause is
weakened (the control, run per clause; see the comment above each):
  A. the classic read was a PERMISSION 403 (not a 404, 5xx, transport, rate limit or any other failure);
  B. the rulesets read ANSWERED (a JSON list) and counted nothing;
  C. `GET branches/<base>` read a BOOL `protected` that is exactly false.
Every other combination keeps raising. Handler order matters: `_runner` takes the first substring match, and
`branches/main` is a substring of the other two reads, so the flag handler goes last."""
import json

import pytest

import prfake
from test_work import (work, ON, ALWAYS, GUARDED, NOSLEEP, MERGE_PUT, RULES_READ, _runner, _sdlc, _started, _evidence,
                       _view, _perm, _ruleset, _protection)

CLASSIC = "branches/main/protection"
FLAG = "branches/main"
FORBIDDEN = RuntimeError("HTTP 403: Resource not accessible by integration")


def _flag(value):
    return [(FLAG, json.dumps({"name": "main", "protected": value}))]


def _handlers(classic=FORBIDDEN, rules="[]", flag=_flag(False)):
    return [(CLASSIC, classic), (RULES_READ, rules)] + flag


def test_403_classic_empty_rules_and_unprotected_flag_is_not_protected(tmp_path):
    got, run = _protection(tmp_path, _handlers())
    assert got[0] is False and "is not protected" in got[1] and "HTTP 403" in got[1], got
    assert any(c.endswith("/branches/main") for c in run.calls)


def test_always_merges_where_the_403_fallback_found_nothing_enforced(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d); _evidence(d, goal)
    run = _runner(_view() + _perm() + _handlers())
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged"), out
    assert MERGE_PUT in run.calls
    assert "WARNING" in out and "not protected" in out, out


def _undetermined(tmp_path, handlers):
    try:
        got = _protection(tmp_path, handlers)[0]
    except Exception as exc:                                    # noqa: BLE001
        assert type(exc).__name__ == "ProtectionUndetermined", repr(exc)
        return str(exc)
    raise AssertionError(f"protection() returned {got!r}; it should have raised ProtectionUndetermined")


# Control for clause C (the flag must be exactly false): weaken `flag is False` to `not flag` -> the true, missing
# and non-bool cases go red; remove the flag read altogether -> the first test above goes red.
@pytest.mark.parametrize("flag", [
    _flag(True),
    [(FLAG, json.dumps({"name": "main"}))],
    [(FLAG, json.dumps({"protected": "false"}))],
    [(FLAG, json.dumps({"protected": None}))],
    [(FLAG, "[]")], [(FLAG, "not json")], [(FLAG, "")],
    [(FLAG, RuntimeError("HTTP 403: Resource not accessible by integration"))],
    [(FLAG, RuntimeError("HTTP 500"))],
], ids=["true", "missing", "string", "null", "list", "not-json", "empty", "403", "500"])
def test_flag_other_than_false_still_fails_closed(tmp_path, flag):
    msg = _undetermined(tmp_path, _handlers(flag=flag))
    assert msg.startswith("protection could not be determined for `main`"), msg


# Control for clause B (the rules read must have answered): weaken `and r_ok` away -> these go red.
@pytest.mark.parametrize("rules", [RuntimeError("HTTP 403"), RuntimeError("HTTP 500"), "", "null", "{}", "not json"],
                         ids=["403", "500", "empty", "null", "object", "not-json"])
def test_rules_read_failing_still_fails_closed_even_with_an_unprotected_flag(tmp_path, rules):
    msg = _undetermined(tmp_path, _handlers(rules=rules))
    assert msg.startswith("protection could not be determined for `main`"), msg


# Control for clause A (only a permission 403 qualifies): replace `c_why == _CLASSIC_FORBIDDEN` with
# `c_state == "unreadable"` -> every case here goes red.
@pytest.mark.parametrize("classic", [
    RuntimeError("HTTP 404: Not Found"), RuntimeError("HTTP 404: Branch not found"), RuntimeError("HTTP 500"),
    RuntimeError("HTTP 429: API rate limit exceeded"), RuntimeError("dial tcp: no such host"),
    RuntimeError("HTTP 403: API rate limit exceeded"), "", "not json", "[]",
], ids=["404-not-found", "404-branch-not-found", "500", "429", "transport", "403-rate-limit", "empty", "not-json", "list"])
def test_a_classic_failure_that_is_not_a_permission_403_still_fails_closed(tmp_path, classic):
    msg = _undetermined(tmp_path, _handlers(classic=classic))
    assert msg.startswith("protection could not be determined for `main`"), msg


# A requirement from the rules read wins over the fallback (clause B's counted-nothing half).
def test_a_ruleset_requirement_still_reports_enforces_under_a_403_classic(tmp_path):
    got, _ = _protection(tmp_path, [(CLASSIC, FORBIDDEN)] + _ruleset(checks=("test",)) + _flag(False))
    assert got == (True, "`main` enforces 1 required check"), got


@pytest.mark.parametrize("policy", [GUARDED, ALWAYS], ids=["protected", "always"])
def test_merge_stops_under_both_policies_when_the_flag_is_true(tmp_path, policy):
    d = _sdlc(tmp_path); goal = _started(d); _evidence(d, goal)
    run = _runner(_view() + _perm() + _handlers(flag=_flag(True)))
    out = work.merge(d, policy, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 clean and safe, but protection could not be determined"), out
    assert not prfake.merge_calls(run.calls)


def test_protected_policy_stops_on_the_fallback_not_protected_base(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d); _evidence(d, goal)
    run = _runner(_view() + _perm() + _handlers())
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert "is not protected" in out and "yours to make" in out, out
    assert not prfake.merge_calls(run.calls)

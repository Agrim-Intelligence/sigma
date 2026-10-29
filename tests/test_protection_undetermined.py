"""#984: `work.protection()` and `work.merge()` when the base's protection could not be determined.

A source that was never READ is not a source that said "nothing". `protection()` reports "nothing is enforced"
only when BOTH the classic API and the rulesets ANSWERED nothing; a source it could not read raises
`ProtectionUndetermined`, and `merge()` stops under `protected` AND `always`.

Every handler list here names BOTH protection reads, so no test depends on a runner default. The listed tests
(the plan's `## Tests`) each fail by ASSERTION against the pre-#984 code: the helper below turns "returned a
value" and "the type does not exist yet" into an AssertionError, never `DID NOT RAISE` or an AttributeError
(the planned-red gate credits assertion failures only). Tests whose first assertion already holds before the
fix are the unlisted controls at the end."""
import json
import subprocess

import pytest

import prfake
from test_work import (work, ON, ALWAYS, GUARDED, NOSLEEP, MERGE_PUT, UNPROTECTED, RULES_READ,
                       _runner, _sdlc, _started, _evidence, _view, _perm, _protected, _ruleset, _protection)

CLASSIC = "branches/main/protection"
NOTHING = UNPROTECTED + [(RULES_READ, "[]")]


@pytest.fixture(autouse=True)
def _hermetic_host(monkeypatch):
    for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDE_CODE_REMOTE", "SIGMA_GH_GRAPHQL"):
        monkeypatch.delenv(key, raising=False)


def _undetermined(tmp_path, handlers):
    """The detail of the ProtectionUndetermined `protection()` raises for `handlers`."""
    try:
        got = _protection(tmp_path, handlers)[0]
    except Exception as exc:                                    # noqa: BLE001 - any other type is a failure below
        assert type(exc).__name__ == "ProtectionUndetermined", repr(exc)
        return str(exc)
    raise AssertionError(f"protection() returned {got!r}; it should have raised ProtectionUndetermined")


#: The classic side says OUT LOUD that a 404 which is not the exact `Branch not protected` answer is not an
#: answer: if GitHub ever rewords that body, the operator reads why a once-unprotected base is now "could not be
#: determined" instead of a bare `read failed (not_found, HTTP 404)` (rules-side and non-404 kinds keep the plain form).
CLASSIC_404_NOT_THE_ANSWER = 'read failed (not_found, HTTP 404; not the "Branch not protected" answer)'

#: classic replies that are NOT an answer -> the fragment the detail must carry for that source.
CLASSIC_UNREADABLE = {
    "branch-not-found-404": (RuntimeError("HTTP 404: Branch not found"), CLASSIC_404_NOT_THE_ANSWER),
    "not-found-404": (RuntimeError("HTTP 404: Not Found"), CLASSIC_404_NOT_THE_ANSWER),
    "bare-404": (RuntimeError("HTTP 404"), CLASSIC_404_NOT_THE_ANSWER),
    "near-miss-404": (RuntimeError("HTTP 404: Branch not protected on this fork"), CLASSIC_404_NOT_THE_ANSWER),
    "403-with-the-answer-text": (RuntimeError("HTTP 403: Branch not protected"), "read failed (permission, HTTP 403)"),
    "403": (RuntimeError("HTTP 403: Resource not accessible by integration"), "read failed (permission, HTTP 403)"),
    "429": (RuntimeError("HTTP 429: API rate limit exceeded"), "read failed (rate_limit, HTTP 429)"),
    "500": (RuntimeError("HTTP 500: Server Error"), "read failed (server, HTTP 500)"),
    "transport": (RuntimeError("dial tcp: lookup api.github.com: no such host"), "read failed (transport)"),
    "empty": ("", "the reply was empty"),
    "not-json": ("not json", "the reply was not JSON"),
    "null": ("null", "the reply was not a JSON object"),
    "list": ("[]", "the reply was not a JSON object"),
    "number": ("42", "the reply was not a JSON object"),
    "object-of-the-wrong-shape": (json.dumps({"required_status_checks": "x"}), "the reply was not a protection object"),
}


@pytest.mark.parametrize("name", sorted(CLASSIC_UNREADABLE))
def test_unreadable_classic_is_undetermined(tmp_path, name):
    """AC-3 / D2: the classic question went unanswered and the rulesets said nothing -> NOT "nothing is enforced"."""
    reply, fragment = CLASSIC_UNREADABLE[name]
    msg = _undetermined(tmp_path, [(CLASSIC, reply), (RULES_READ, "[]")])
    assert msg.startswith("protection could not be determined for `main` (classic branch protection: " + fragment), msg
    assert "; rulesets: listed, nothing counted)" in msg, msg
    assert "is not protected" not in msg and "nothing is enforced" not in msg, msg


RULES_NOT_A_LIST = ["", "null", "{}", "42", '"x"', "not json"]


@pytest.mark.parametrize("classic", [UNPROTECTED, _protected(checks=(), reviews=0)], ids=["classic-none", "classic-object"])
@pytest.mark.parametrize("failure,fragment", [
    (RuntimeError("HTTP 403"), "read failed (permission, HTTP 403)"),
    (RuntimeError("HTTP 404: Not Found"), "read failed (not_found, HTTP 404)"),
    (RuntimeError("HTTP 500"), "read failed (server, HTTP 500)"),
    (RuntimeError("connection reset by peer"), "read failed (transport)"),
] + [(reply, "the reply was " + ("empty" if not reply else "not JSON" if reply == "not json" else "not a JSON list"))
     for reply in RULES_NOT_A_LIST], ids=["403", "404-not-found", "500", "transport", "empty", "null", "object", "number",
                                          "string", "not-json"])
def test_unreadable_rules_is_undetermined(tmp_path, classic, failure, fragment):
    """The MIXED case (D2): a ruleset may exist that this token could not see. A readable classic object that
    requires nothing is no different from the 404 here (it used to read "protected but requires no checks")."""
    msg = _undetermined(tmp_path, classic + [(RULES_READ, failure)])
    assert "; rulesets: " + fragment in msg, msg


def test_both_unreadable_names_both_sources(tmp_path):
    msg = _undetermined(tmp_path, [(CLASSIC, RuntimeError("HTTP 403")), (RULES_READ, RuntimeError("HTTP 500"))])
    assert "classic branch protection: read failed (permission, HTTP 403); rulesets: read failed (server, HTTP 500)" in msg, msg


def test_detail_names_the_lever_not_the_failure_text(tmp_path):
    token = "ghp_" + "A" * 36
    msg = _undetermined(tmp_path, [(CLASSIC, RuntimeError(f"HTTP 403: bad credentials {token}")), (RULES_READ, "[]")])
    assert token not in msg and "bad credentials" not in msg, msg
    assert "re-run `work.py merge " in msg and "read access to branch protection and rulesets" in msg, msg
    assert "merge the PR by hand" in msg, msg


@pytest.mark.parametrize("stderr", ["gh: Branch not found (HTTP 404)", "gh: Not Found (HTTP 404)",
                                    "gh: Resource not accessible by integration (HTTP 403)"])
def test_other_404_and_403_are_not_an_answer(tmp_path, monkeypatch, stderr):
    """`_run`'s own failure shape (`<argv>: gh: <message> (HTTP n) -- fix: ...`) through the default runner, not a
    hand-written one: the exact-message clause must hold for what `gh` really prints (MEASURED 2026-10-28 on a
    ruleset-protected repository for the two 404 bodies)."""
    d = _sdlc(tmp_path); goal = _started(d)

    def fake(argv, **kw):
        if argv[-1].endswith("/protection"):
            return subprocess.CompletedProcess(argv, 1, "", stderr + "\n")
        return subprocess.CompletedProcess(argv, 0, "[]\n", "")
    monkeypatch.setattr(work.subprocess, "run", fake)
    try:
        got = work.protection(d, ON, goal)
    except Exception as exc:                                    # noqa: BLE001
        assert type(exc).__name__ == "ProtectionUndetermined", repr(exc)
        if "(HTTP 404)" in stderr:                              # a classic 404 that is not the answer says so
            assert "classic branch protection: " + CLASSIC_404_NOT_THE_ANSWER + ";" in str(exc), str(exc)
        return
    raise AssertionError(f"protection() returned {got!r}; it should have raised ProtectionUndetermined")


@pytest.mark.parametrize("policy", [GUARDED, ALWAYS], ids=["protected", "always"])
@pytest.mark.parametrize("handlers", [
    [(CLASSIC, RuntimeError("HTTP 403")), (RULES_READ, "[]")],
    UNPROTECTED + [(RULES_READ, RuntimeError("HTTP 500"))],
    [(CLASSIC, RuntimeError("HTTP 403")), (RULES_READ, RuntimeError("HTTP 500"))],
], ids=["classic-403", "rules-500", "both"])
def test_merge_stops_when_protection_is_undetermined(tmp_path, policy, handlers):
    """AC-3 end to end: the routed `... merging it is yours to make (auto_merge: "<policy>")` ending, no merge PUT, no
    `--auto`, no WARNING-merge. `always` used to merge here (a failed read read as "not protected")."""
    d = _sdlc(tmp_path); goal = _started(d); _evidence(d, goal)
    run = _runner(_view() + _perm() + handlers)
    out = work.merge(d, policy, goal, run=run, sleep=NOSLEEP)
    assert prfake.merge_calls(run.calls) == [] and MERGE_PUT not in run.calls, out     # no merge call: the fail-closed half
    assert not any("--auto" in c.split() for c in run.calls), out
    assert out.startswith("PR #7 clean and safe, but protection could not be determined for `main` ("), out
    assert out.endswith(f'merging it is yours to make (auto_merge: "{work.policy(policy)}")'), out
    assert "WARNING" not in out and "is not protected" not in out, out


# ---- unlisted controls: these already hold before #984 (they pin what the change must NOT move) ----------

def test_nothing_is_enforced_only_when_both_sources_answered_nothing(tmp_path):
    """AC-2, and the two readable-empty shapes."""
    assert _protection(tmp_path / "a", NOTHING)[0] == (False, "`main` is not protected — nothing is enforced on merge")
    assert _protection(tmp_path / "b", _protected(checks=(), reviews=0) + [(RULES_READ, "[]")])[0] == (
        False, "`main` is protected but requires no checks or reviews")


@pytest.mark.parametrize("failure", [RuntimeError("HTTP 403"), RuntimeError("HTTP 404"), RuntimeError("HTTP 500"),
                                     "", "not json", "{}"])
def test_a_requirement_from_one_source_stands_when_the_other_cannot_be_read(tmp_path, failure):
    """The documented safe direction: the unreadable source only UNDERCOUNTS (AC-1 on the ruleset side)."""
    got, _ = _protection(tmp_path / "classic", _protected(checks=("ci",), reviews=0) + [(RULES_READ, failure)])
    assert got == (True, "`main` enforces 1 required check"), got
    got, _ = _protection(tmp_path / "rules", [(CLASSIC, failure)] + _ruleset(reviews=1))
    assert got == (True, "`main` enforces 1 required review"), got


def test_the_real_branch_not_protected_failure_is_an_answer(tmp_path, monkeypatch):
    """The first row of the exact-message clause: what `gh` prints for a branch that exists with no classic protection."""
    d = _sdlc(tmp_path); goal = _started(d)

    def fake(argv, **kw):
        if argv[-1].endswith("/protection"):
            return subprocess.CompletedProcess(argv, 1, "", "gh: Branch not protected (HTTP 404)\n")
        return subprocess.CompletedProcess(argv, 0, "[]\n", "")
    monkeypatch.setattr(work.subprocess, "run", fake)
    assert work.protection(d, ON, goal) == (False, "`main` is not protected — nothing is enforced on merge")

"""#2242: `work.protection_unreadable: "allow"` lets `work.py merge` proceed under `auto_merge: "always"` when branch
protection was unreadable ONLY because every unreadable source was refused with a permission HTTP 403 (a private
repository on a free GitHub plan: "Upgrade to GitHub Pro or make this repository public to enable this feature").

Fakes only: these prove Sigma's decisions against canned GitHub replies, never a real free-plan repository.

Clauses, each broken once for the control (the result is recorded in the PR):
  1. default: the key absent reads "hold";
  2. 403-only: `plan_refused` is True only for permission 403s, never a 404, 5xx, timeout, rate limit or bad body;
  3. always-only: `chosen == ALWAYS` in merge();
  4. doctor row: present only when the key is "allow";
  5. invalid value: anything but "hold"/"allow" reads "hold"."""
import importlib.util
import json
import subprocess

import pytest

import test_work as tw

work = tw.work
ROOT = tw.ROOT
ALWAYS, GUARDED, NOSLEEP, MERGE_PUT = tw.ALWAYS, tw.GUARDED, tw.NOSLEEP, tw.MERGE_PUT
CLASSIC, RULES, FLAG = "branches/main/protection", tw.RULES_READ, "branches/main"
PLAN = "HTTP 403: Upgrade to GitHub Pro or make this repository public to enable this feature."
UNPROTECTED_FLAG = json.dumps({"name": "main", "protected": False})


def _cfg(base, **work_keys):
    return {"work": {**base["work"], **work_keys}}


ALLOW_ALWAYS = _cfg(ALWAYS, protection_unreadable="allow")


def _plan_403(classic=RuntimeError(PLAN), rules=RuntimeError(PLAN), flag=UNPROTECTED_FLAG):
    """Handler order matters: first substring match wins and `branches/main` is inside the other two paths."""
    return [(CLASSIC, classic), (RULES, rules), (FLAG, flag)]


def _merge(tmp_path, config, handlers):
    d = tw._sdlc(tmp_path, config); goal = tw._started(d); tw._evidence(d, goal)
    run = tw._runner(tw._view() + tw._perm() + handlers)
    return work.merge(d, config, goal, run=run, sleep=NOSLEEP), run


def test_default_holds_on_a_plan_403_and_names_the_key_and_the_cause(tmp_path):
    out, run = _merge(tmp_path, ALWAYS, _plan_403())
    assert "protection could not be determined" in out and MERGE_PUT not in run.calls, out
    assert "work.protection_unreadable" in out and "Upgrade to GitHub Pro" in out, out


def test_allow_with_always_merges_on_a_plan_403_and_says_why(tmp_path):
    out, run = _merge(tmp_path, ALLOW_ALWAYS, _plan_403())
    assert out.startswith("PR #7 merged"), out
    assert MERGE_PUT in run.calls
    assert "WARNING" in out and "unreadable" in out and 'work.protection_unreadable: "allow"' in out, out


def test_allow_with_protected_still_holds(tmp_path):
    out, run = _merge(tmp_path, _cfg(GUARDED, protection_unreadable="allow"), _plan_403())
    assert "protection could not be determined" in out and MERGE_PUT not in run.calls, out


@pytest.mark.parametrize("bad", [
    RuntimeError("HTTP 500: Server Error"),
    subprocess.TimeoutExpired(["gh", "api"], 30),
    "not json",
    "",
    "null",
    RuntimeError("HTTP 403: API rate limit exceeded for user"),
    RuntimeError("HTTP 404: Not Found"),
], ids=["500", "timeout", "malformed", "empty", "json-null", "403-rate-limit", "404"])
@pytest.mark.parametrize("where", ["classic", "rules"])
def test_allow_with_always_still_holds_on_any_other_failure(tmp_path, bad, where):
    # One source fails some other way while the other is the plan 403: a mixed failure holds too.
    handlers = _plan_403(classic=bad) if where == "classic" else _plan_403(rules=bad)
    out, run = _merge(tmp_path, ALLOW_ALWAYS, handlers)
    assert "protection could not be determined" in out and MERGE_PUT not in run.calls, out


def test_allow_with_always_holds_when_the_branch_flag_says_protected(tmp_path):
    out, run = _merge(tmp_path, ALLOW_ALWAYS, _plan_403(flag=json.dumps({"protected": True})))
    assert "protection could not be determined" in out and MERGE_PUT not in run.calls, out


def test_a_non_403_hold_does_not_advertise_the_key(tmp_path):
    out, _ = _merge(tmp_path, ALWAYS, _plan_403(classic=RuntimeError("HTTP 500"), rules=RuntimeError("HTTP 500")))
    assert "protection could not be determined" in out and "protection_unreadable" not in out, out


@pytest.mark.parametrize("value", ["Allow", " allow", "ALLOW", True, 1, ["allow"], None, "yes", ""])
def test_invalid_value_reads_as_hold_with_a_note(tmp_path, value):
    cfg = _cfg(ALWAYS, protection_unreadable=value)
    mode, note = work.protection_unreadable(cfg)
    assert mode == "hold" and note
    out, run = _merge(tmp_path, cfg, _plan_403())
    assert "protection could not be determined" in out and MERGE_PUT not in run.calls, out


def test_valid_values_and_absent_key_carry_no_note():
    assert work.protection_unreadable(ALWAYS) == ("hold", "")
    assert work.protection_unreadable(_cfg(ALWAYS, protection_unreadable="hold")) == ("hold", "")
    assert work.protection_unreadable(ALLOW_ALWAYS) == ("allow", "")
    assert work.protection_unreadable({"work": "oops"}) == ("hold", "")
    assert work.protection_unreadable(None) == ("hold", "")


def test_plan_refused_is_only_set_for_all_permission_403s(tmp_path):
    d = tw._sdlc(tmp_path); goal = tw._started(d)
    with pytest.raises(work.ProtectionUndetermined) as ok:
        work.protection(d, tw.ON, goal, run=tw._runner(_plan_403()))
    assert ok.value.plan_refused is True
    with pytest.raises(work.ProtectionUndetermined) as no:
        work.protection(d, tw.ON, goal, run=tw._runner(_plan_403(classic=RuntimeError("HTTP 500"))))
    assert no.value.plan_refused is False


def _doctor():
    spec = importlib.util.spec_from_file_location("doctor_2242", ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(tmp_path, config):
    d = tw._sdlc(tmp_path, config)
    return {name: state for name, state, _ in _doctor().features(d)}


ROW = "merge when branch protection is unreadable (work.protection_unreadable)"


def test_doctor_row_appears_only_on_allow_and_names_the_key(tmp_path):
    assert ROW not in _rows(tmp_path / "a", ALWAYS)
    assert ROW not in _rows(tmp_path / "b", _cfg(ALWAYS, protection_unreadable="hold"))
    assert ROW not in _rows(tmp_path / "c", _cfg(ALWAYS, protection_unreadable="Allow"))
    on = _rows(tmp_path / "d", ALLOW_ALWAYS)
    assert on[ROW].startswith("ALLOW") and "403" in on[ROW]


def test_doctor_row_says_inactive_when_auto_merge_is_not_always(tmp_path):
    row = _rows(tmp_path, _cfg(GUARDED, protection_unreadable="allow"))[ROW]
    assert "INACTIVE" in row

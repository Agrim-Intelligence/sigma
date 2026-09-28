"""Tests for skills/agrim-status/scripts/merge_queue_enable.py -- the opt-in, admin-consented
merge-queue ENABLE tool (#977, split (c) of #408; the mutating counterpart to #976's read-only
`merge_queue.py` advisor). Every test here mocks `run` -- NONE of these tests, nor the module under
test when invoked this way, ever shells out to the real `gh` CLI or touches a live repo. That is a
hard requirement of this test file, not an implementation detail: this module can flip a repo's
`allow_auto_merge` setting and create a branch-protection ruleset, so a test that accidentally called
the real `gh` would be a live governance mutation, not a test failure.

Two safety properties get outsized test coverage on purpose, because this is the highest-risk module
in this codebase:
  1. The consent gate (`--yes-enable-merge-queue`) must be provably unbypassable -- proven here by a
     `run` that raises AssertionError if called at all, not just by checking a return code.
  2. Every mutating call's LITERAL argv (and, for the ruleset POST, its literal JSON body) is
     asserted -- not just call count/order -- mirroring `tests/test_merge_queue.py`'s own
     `test_api_prefixes_every_call_with_repos_repo` idiom. That test exists because a prior version of
     #976's `_api()` silently dropped the `repo` prefix and every call target the wrong (ambient) repo,
     invisible under fail-open. For a MUTATING tool that same bug class -- a PATCH/POST landing against
     the wrong repo -- is far higher stakes, so literal-argv assertions are required here, not
     incidental."""
import importlib.util
import json
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-status" / "scripts"


def _mqe():
    spec = importlib.util.spec_from_file_location("merge_queue_enable", S / "merge_queue_enable.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _explode(args, input_json=None):
    raise AssertionError(f"run() must not be called at all: args={args!r} input_json={input_json!r}")


def _scripted_run(expected):
    """expected: list of (args, input_json_or_None, (rc, out, err)). Each call to the returned `run`
    must match the NEXT expected entry's args/input_json exactly (literal equality, not a substring or
    prefix check) and returns its scripted (rc, out, err). Any call beyond len(expected), or any
    mismatch, raises AssertionError immediately -- so a test that expects N calls and gets an (N+1)th
    (e.g. because a failure path didn't actually abort) fails loudly, not silently."""
    calls = []

    def run(args, input_json=None):
        i = len(calls)
        calls.append((args, input_json))
        if i >= len(expected):
            raise AssertionError(f"unexpected call #{i}: {args!r} (only {len(expected)} calls scripted)")
        exp_args, exp_input, resp = expected[i]
        assert args == exp_args, f"call #{i}: expected args {exp_args!r}, got {args!r}"
        assert input_json == exp_input, f"call #{i}: expected input_json {exp_input!r}, got {input_json!r}"
        return resp
    run.calls = calls
    return run


REPO = "acme/widget"


# --- _classify_error -----------------------------------------------------------------------------

def test_classify_error_plan_gate():
    mq = _mqe()
    text = "Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)"
    assert mq._classify_error(text) == "plan_gate"


def test_classify_error_permission():
    mq = _mqe()
    assert mq._classify_error("Resource not accessible by integration (HTTP 403)") == "permission"
    assert mq._classify_error("HTTP 403: Must have admin rights to Repository.") == "permission"


def test_classify_error_other():
    mq = _mqe()
    assert mq._classify_error("HTTP 404: Not Found") == "other"
    assert mq._classify_error("connection timed out") == "other"
    assert mq._classify_error("") == "other"
    assert mq._classify_error(None) == "other"


# --- ApiError.describe() names the right remediation ----------------------------------------------

def test_api_error_plan_gate_names_plan_and_upgrade():
    mq = _mqe()
    err = mq.ApiError("create_ruleset", "repos/acme/widget/rulesets", 1,
                       "Upgrade to GitHub Pro to enable this feature. (HTTP 403)")
    msg = err.describe()
    assert "plan" in msg.lower()
    assert "upgrade" in msg.lower()


def test_api_error_permission_names_admin_and_scope():
    mq = _mqe()
    err = mq.ApiError("enable_auto_merge", "repos/acme/widget", 1,
                       "Resource not accessible by integration (HTTP 403)")
    msg = err.describe()
    assert "admin" in msg.lower()


# --- _normalize_merge_method ------------------------------------------------------------------

def test_normalize_merge_method_accepts_any_case():
    mq = _mqe()
    assert mq._normalize_merge_method("squash") == "SQUASH"
    assert mq._normalize_merge_method("Squash") == "SQUASH"
    assert mq._normalize_merge_method("SQUASH") == "SQUASH"
    assert mq._normalize_merge_method("rebase") == "REBASE"
    assert mq._normalize_merge_method(None) == "SQUASH"          # default


def test_normalize_merge_method_rejects_unknown_value():
    mq = _mqe()
    try:
        mq._normalize_merge_method("FOO")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- _ruleset_payload: the documented Rulesets API shape -----------------------------------------

def test_ruleset_payload_matches_documented_merge_queue_schema():
    mq = _mqe()
    payload = mq._ruleset_payload("main", merge_method="SQUASH")
    assert payload["target"] == "branch"
    assert payload["enforcement"] == "active"
    assert payload["conditions"] == {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}
    assert len(payload["rules"]) == 1
    rule = payload["rules"][0]
    assert rule["type"] == "merge_queue"
    params = rule["parameters"]
    assert set(params.keys()) == {
        "check_response_timeout_minutes", "grouping_strategy", "max_entries_to_build",
        "max_entries_to_merge", "merge_method", "min_entries_to_merge",
        "min_entries_to_merge_wait_minutes",
    }
    assert params["grouping_strategy"] in ("ALLGREEN", "HEADGREEN")
    assert params["merge_method"] == "SQUASH"


# --- find_existing_merge_queue_rule ---------------------------------------------------------------

def _ruleset(branch="main", rule_types=("merge_queue",)):
    return {
        "id": 99,
        "conditions": {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}},
        "rules": [{"type": t} for t in rule_types],
    }


def test_find_existing_merge_queue_rule_found():
    mq = _mqe()
    rulesets = [_ruleset(branch="dev", rule_types=("deletion",)), _ruleset(branch="main")]
    found = mq.find_existing_merge_queue_rule(rulesets, "main")
    assert found is not None and found["id"] == 99


def test_find_existing_merge_queue_rule_absent_wrong_branch():
    mq = _mqe()
    rulesets = [_ruleset(branch="dev")]
    assert mq.find_existing_merge_queue_rule(rulesets, "main") is None


def test_find_existing_merge_queue_rule_absent_no_merge_queue_type():
    mq = _mqe()
    rulesets = [_ruleset(branch="main", rule_types=("deletion", "non_fast_forward"))]
    assert mq.find_existing_merge_queue_rule(rulesets, "main") is None


def test_find_existing_merge_queue_rule_malformed_entries_skipped():
    mq = _mqe()
    rulesets = ["not-a-dict", {"conditions": "nope"}, {"rules": "nope"}, None]
    assert mq.find_existing_merge_queue_rule(rulesets, "main") is None


# --- plan(): must be read-only under EVERY input --------------------------------------------------

def _read_only_guard_run(repo_body, rulesets_body):
    def run(args, input_json=None):
        assert args[:2] == ["gh", "api"], f"not a gh api call: {args!r}"
        assert not any(a in ("-X", "--method", "-f", "--input") for a in args), \
            f"plan() must never send a mutating flag: {args!r}"
        assert input_json is None, "plan() must never send a request body"
        if args[2] == f"repos/{REPO}":
            return (0, json.dumps(repo_body), "")
        if args[2] == f"repos/{REPO}/rulesets":
            return (0, json.dumps(rulesets_body), "")
        raise AssertionError(f"unexpected call in plan(): {args!r}")
    return run


def test_plan_reports_auto_merge_off_and_no_existing_ruleset():
    mq = _mqe()
    run = _read_only_guard_run({"allow_auto_merge": False}, [])
    result = mq.plan(REPO, "main", run)
    assert result["ok"] is True
    assert result["auto_merge_currently_on"] is False
    assert result["auto_merge_would_change"] is True
    assert result["ruleset_would_be_created"] is True
    assert result["existing_merge_queue_ruleset"] is None
    assert result["payload_preview"]["rules"][0]["type"] == "merge_queue"


def test_plan_reports_already_configured_state():
    mq = _mqe()
    run = _read_only_guard_run({"allow_auto_merge": True}, [_ruleset(branch="main")])
    result = mq.plan(REPO, "main", run)
    assert result["ok"] is True
    assert result["auto_merge_would_change"] is False
    assert result["ruleset_would_be_created"] is False
    assert result["existing_merge_queue_ruleset"]["id"] == 99


def test_plan_never_mutates_even_when_asked_to_apply_flags_are_irrelevant():
    """plan() takes no consent flag at all -- there is no argument that can make it write. Feeding it
    a run that would fail any -X/-f/--input call proves there is no code path to a mutation."""
    mq = _mqe()
    run = _read_only_guard_run({"allow_auto_merge": False}, [])
    mq.plan(REPO, "main", run)   # would have raised via the guard if plan() tried to mutate


def test_plan_reports_error_and_makes_no_further_calls_on_repo_read_failure():
    mq = _mqe()
    run = _scripted_run([
        ([ "gh", "api", f"repos/{REPO}"], None, (1, "", "HTTP 404: Not Found")),
    ])
    result = mq.plan(REPO, "main", run)
    assert result["ok"] is False
    assert "read_repo" in result["stage"]
    assert len(run.calls) == 1


def test_plan_reports_error_on_rulesets_read_failure_after_repo_read_succeeds():
    mq = _mqe()
    run = _scripted_run([
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": False}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets"], None, (1, "", "Resource not accessible by integration (HTTP 403)")),
    ])
    result = mq.plan(REPO, "main", run)
    assert result["ok"] is False
    assert result["auto_merge_currently_on"] is False
    assert len(run.calls) == 2


# --- CLI consent gate: main() -----------------------------------------------------------------

def test_apply_refuses_without_consent_flag_and_makes_zero_calls():
    mq = _mqe()
    rc = mq.main(["merge_queue_enable.py", "apply", REPO], run=_explode)
    assert rc != 0


def test_apply_refuses_with_near_miss_flag_and_makes_zero_calls():
    """`--yes` alone (a valid unambiguous PREFIX of the real flag under naive argparse
    allow_abbrev=True semantics) must NOT satisfy the gate -- only the exact, full flag string does."""
    mq = _mqe()
    rc = mq.main(["merge_queue_enable.py", "apply", REPO, "--yes"], run=_explode)
    assert rc != 0


def test_apply_refuses_with_flag_as_substring_of_something_else():
    mq = _mqe()
    rc = mq.main(["merge_queue_enable.py", "apply", REPO, "--yes-enable-merge-queue-typo"], run=_explode)
    assert rc != 0


def test_consent_flag_constant_is_the_exact_documented_string():
    mq = _mqe()
    assert mq.CONSENT_FLAG == "--yes-enable-merge-queue"


def test_plan_cli_never_requires_or_accepts_a_consent_flag_to_run():
    mq = _mqe()
    run = _read_only_guard_run({"allow_auto_merge": True}, [_ruleset(branch="main")])
    rc = mq.main(["merge_queue_enable.py", "plan", REPO], run=run)
    assert rc == 0


# --- apply(): happy path, exact argv + exact JSON body on every call -------------------------------

def test_apply_happy_path_sends_documented_payload_and_verifies():
    mq = _mqe()
    payload = mq._ruleset_payload("main", merge_method="SQUASH")
    run = _scripted_run([
        (["gh", "api", f"repos/{REPO}"], None,
         (0, json.dumps({"allow_auto_merge": False}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets"], None,
         (0, json.dumps([]), "")),
        (["gh", "api", "-X", "PATCH", f"repos/{REPO}", "-f", "allow_auto_merge=true"], None,
         (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets", "-X", "POST", "--input", "-"], payload,
         (0, json.dumps({"id": 42, **payload}), "")),
        (["gh", "api", f"repos/{REPO}"], None,
         (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets/42"], None,
         (0, json.dumps({"id": 42, **payload}), "")),
    ])
    result = mq.apply(REPO, "main", run)
    assert result["ok"] is True
    assert result["auto_merge"] == {"changed": True, "value": True}
    assert result["ruleset"] == {"changed": True, "id": 42}
    assert result["verified"] is True
    assert result["partial"] is False
    assert len(run.calls) == 6


def test_apply_skips_patch_when_auto_merge_already_on():
    mq = _mqe()
    payload = mq._ruleset_payload("main", merge_method="SQUASH")
    run = _scripted_run([
        (["gh", "api", f"repos/{REPO}"], None,
         (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets"], None,
         (0, json.dumps([]), "")),
        (["gh", "api", f"repos/{REPO}/rulesets", "-X", "POST", "--input", "-"], payload,
         (0, json.dumps({"id": 7, **payload}), "")),
        (["gh", "api", f"repos/{REPO}"], None,
         (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets/7"], None,
         (0, json.dumps({"id": 7, **payload}), "")),
    ])
    result = mq.apply(REPO, "main", run)
    assert result["auto_merge"] == {"changed": False, "value": True}
    assert result["verified"] is True
    for args, _ in run.calls:
        assert "PATCH" not in args, f"PATCH must not be called when auto-merge is already on: {args!r}"
    assert len(run.calls) == 5


def test_apply_custom_merge_method_is_normalized_and_passed_through():
    mq = _mqe()
    payload = mq._ruleset_payload("main", merge_method="REBASE")
    run = _scripted_run([
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets"], None, (0, json.dumps([]), "")),
        (["gh", "api", f"repos/{REPO}/rulesets", "-X", "POST", "--input", "-"], payload,
         (0, json.dumps({"id": 1, **payload}), "")),
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets/1"], None, (0, json.dumps({"id": 1, **payload}), "")),
    ])
    result = mq.apply(REPO, "main", run, merge_method="rebase")
    assert result["ok"] is True
    assert result["ruleset"]["id"] == 1


def test_apply_rejects_invalid_merge_method_before_any_call():
    mq = _mqe()
    result = mq.apply(REPO, "main", _explode, merge_method="not-a-real-method")
    assert result["ok"] is False
    assert "stage" in result and result["stage"] == "validate_input"


# --- apply(): fail-closed on each stage, with the RIGHT calls made and NO more ----------------------

def test_apply_aborts_on_preflight_rulesets_read_failure_zero_mutations():
    mq = _mqe()
    run = _scripted_run([
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": False}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets"], None,
         (1, "", "Resource not accessible by integration (HTTP 403)")),
    ])
    result = mq.apply(REPO, "main", run)
    assert result["ok"] is False
    assert result["stage"] == "preflight_rulesets_read"
    assert result["auto_merge"] == {"changed": False, "value": False}
    assert result["ruleset"] is None
    assert result["partial"] is False
    assert "admin" in result["error"].lower()
    assert len(run.calls) == 2                     # PATCH and POST never attempted


def test_apply_aborts_on_auto_merge_patch_failure_zero_ruleset_call():
    mq = _mqe()
    run = _scripted_run([
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": False}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets"], None, (0, json.dumps([]), "")),
        (["gh", "api", "-X", "PATCH", f"repos/{REPO}", "-f", "allow_auto_merge=true"], None,
         (1, "", "HTTP 403: Must have admin rights to Repository.")),
    ])
    result = mq.apply(REPO, "main", run)
    assert result["ok"] is False
    assert result["stage"] == "enable_auto_merge"
    assert result["auto_merge"] == {"changed": False, "value": False}
    assert result["partial"] is False               # nothing actually mutated -- the PATCH call itself failed
    assert "admin" in result["error"].lower()
    assert len(run.calls) == 3                       # POST never attempted


def test_apply_reports_partial_state_when_ruleset_creation_fails_after_auto_merge_flip():
    """The exact scenario named in done_when #5: auto-merge succeeds, ruleset creation fails --
    must be reported explicitly, never silently left half-changed."""
    mq = _mqe()
    payload = mq._ruleset_payload("main", merge_method="SQUASH")
    run = _scripted_run([
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": False}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets"], None, (0, json.dumps([]), "")),
        (["gh", "api", "-X", "PATCH", f"repos/{REPO}", "-f", "allow_auto_merge=true"], None,
         (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets", "-X", "POST", "--input", "-"], payload,
         (1, "", "Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)")),
    ])
    result = mq.apply(REPO, "main", run)
    assert result["ok"] is False
    assert result["stage"] == "create_ruleset"
    assert result["auto_merge"] == {"changed": True, "value": True}     # DID change
    assert result["ruleset"] is None                                     # did NOT get created
    assert result["partial"] is True                                     # explicitly flagged
    assert "plan" in result["error"].lower() or "upgrade" in result["error"].lower()
    assert len(run.calls) == 4                                           # verify GETs never attempted


def test_apply_verify_failure_is_reported_not_silently_treated_as_success():
    mq = _mqe()
    payload = mq._ruleset_payload("main", merge_method="SQUASH")
    run = _scripted_run([
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": False}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets"], None, (0, json.dumps([]), "")),
        (["gh", "api", "-X", "PATCH", f"repos/{REPO}", "-f", "allow_auto_merge=true"], None,
         (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets", "-X", "POST", "--input", "-"], payload,
         (0, json.dumps({"id": 5, **payload}), "")),
        (["gh", "api", f"repos/{REPO}"], None, (1, "", "connection timed out")),
    ])
    result = mq.apply(REPO, "main", run)
    assert result["ok"] is False
    assert result["verified"] is False
    assert result["partial"] is True
    assert result["ruleset"] == {"changed": True, "id": 5}     # the POST DID succeed -- must not be hidden
    assert len(run.calls) == 5                                  # the ruleset-verify GET is never reached


def test_apply_verify_mismatch_flags_not_verified():
    """POST succeeds and returns 2xx, but the re-read state doesn't actually show what was requested
    (e.g. eventual consistency, or a GitHub-side partial write) -- must not be reported as verified."""
    mq = _mqe()
    payload = mq._ruleset_payload("main", merge_method="SQUASH")
    run = _scripted_run([
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": False}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets"], None, (0, json.dumps([]), "")),
        (["gh", "api", "-X", "PATCH", f"repos/{REPO}", "-f", "allow_auto_merge=true"], None,
         (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets", "-X", "POST", "--input", "-"], payload,
         (0, json.dumps({"id": 5, **payload}), "")),
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": False}), "")),  # still false!
        (["gh", "api", f"repos/{REPO}/rulesets/5"], None, (0, json.dumps({"id": 5, **payload}), "")),
    ])
    result = mq.apply(REPO, "main", run)
    assert result["verified"] is False
    assert result["ok"] is False
    assert result["partial"] is True


# --- main(): apply happy path end-to-end through the CLI, with the consent flag present -------------

def test_main_apply_end_to_end_with_consent_flag():
    mq = _mqe()
    payload = mq._ruleset_payload("main", merge_method="SQUASH")
    run = _scripted_run([
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets"], None, (0, json.dumps([]), "")),
        (["gh", "api", f"repos/{REPO}/rulesets", "-X", "POST", "--input", "-"], payload,
         (0, json.dumps({"id": 3, **payload}), "")),
        (["gh", "api", f"repos/{REPO}"], None, (0, json.dumps({"allow_auto_merge": True}), "")),
        (["gh", "api", f"repos/{REPO}/rulesets/3"], None, (0, json.dumps({"id": 3, **payload}), "")),
    ])
    rc = mq.main(["merge_queue_enable.py", "apply", REPO, mq.CONSENT_FLAG], run=run)
    assert rc == 0
    assert len(run.calls) == 5


def test_main_unknown_command_makes_zero_calls():
    mq = _mqe()
    rc = mq.main(["merge_queue_enable.py", "bogus-command", REPO], run=_explode)
    assert rc != 0


def test_main_missing_args_makes_zero_calls():
    mq = _mqe()
    rc = mq.main(["merge_queue_enable.py"], run=_explode)
    assert rc != 0

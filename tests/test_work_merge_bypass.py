"""#2092: `work.merge_bypass: "ruleset"` lets `work.py merge` land a pull request that GitHub reports BLOCKED
only because of rulesets the merging account can ALWAYS bypass, when four conditions hold (AC-2). Every
condition parks and names itself when it fails or its read fails (AC-3). Classic branch protection, a required
check that did not pass and a ruleset the account cannot always bypass are never bypassed (AC-4). Anything but
the exact literal leaves today's park untouched (AC-1).

EVERYTHING HERE IS A FAKE. `tw._runner` answers every `gh` call from a handler list and refuses to know a real
repository, so nothing in this file can reach GitHub, a real pull request or a real config. These tests prove
Sigma's reads, its decisions and its argv. Whether GitHub's REST merge HONOURS a ruleset bypass (for any token,
the cloud proxy's included) is UNMEASURED (AC-5 is open); no test here says anything about it.

Each guard clause in `_ruleset_bypass` and in `merge()`'s wiring was broken on its own and seen red (the plan's
step 4); the test that proves a clause is named for it."""
import json

import pytest

import prfake
import test_work as tw

work = tw.work
HEAD = tw.HEAD_SHA
MERGE_PUT = tw.MERGE_PUT

RULES = "rules/branches/main"
CLASSIC = "branches/main/protection"
COMPARE = "compare/main..."
POST_COMMENT = "issues/7/comments --method POST"

CFG = {"work": {"enabled": True, "auto_merge": "protected", "require_review": "changes",
                "merge_bypass": "ruleset"}}


def _cfg(**wk):
    return {"work": {**CFG["work"], **wk}}


def _rule(kind, ruleset_id=1, source="Repository", source_name="acme/app", **params):
    rule = {"type": kind, "ruleset_id": ruleset_id, "ruleset_source_type": source, "ruleset_source": source_name}
    if params:
        rule["parameters"] = params
    return rule


def basic_rules(strict=False, reviews=1, contexts=("test",)):
    """The `basic` ruleset's rules as GitHub lists them for `main`: restrict updates, one approving review,
    and the required `test` check."""
    rules = [_rule("update")]
    if reviews is not None:
        rules.append(_rule("pull_request", required_approving_review_count=reviews))
    if contexts:
        params = {"required_status_checks": [{"context": c} for c in contexts]}
        if strict is not None:
            params["strict_required_status_checks_policy"] = strict
        rules.append(_rule("required_status_checks", **params))
    return rules


def ruleset_body(name="basic", can_bypass="always", **extra):
    body = {"id": 1, "name": name, "enforcement": "active"}
    if can_bypass is not None:
        body["current_user_can_bypass"] = can_bypass
    body.update(extra)
    return json.dumps(body)


APPROVED = tw._review(decision="APPROVED", pr_get=False)


def world(rules=None, ruleset=None, classic="none", compare=None, review=APPROVED, status="BLOCKED",
          mergeable="MERGEABLE", checks=(("test", "SUCCESS"),), extra=(), head=HEAD):
    """The handler list of a repository whose `main` is protected by one ruleset. First match wins, so `extra`
    (a read made to fail, a second ruleset) goes in front."""
    rules = basic_rules() if rules is None else rules
    out = list(extra)
    out.append((POST_COMMENT, tw._comment_reply(41)))
    out.append((CLASSIC, RuntimeError("HTTP 404: Branch not protected") if classic == "none"
                else classic if isinstance(classic, Exception)
                else json.dumps(classic)))
    out.append((RULES, json.dumps(rules) if not isinstance(rules, str) else rules))
    out.append(("/rulesets/1", ruleset if ruleset is not None else ruleset_body()))
    out.append((COMPARE, compare if compare is not None else json.dumps({"status": "ahead", "behind_by": 0})))
    return out + tw._view(status=status, mergeable=mergeable, checks=checks, head=head) + tw._perm() + review


def drive(tmp_path, handlers, cfg=None, monkeypatch=None):
    cfg = CFG if cfg is None else cfg
    d = tw._sdlc(tmp_path, cfg)
    goal = tw._started(d)
    tw._evidence(d, goal)
    run = tw._runner(handlers)
    return work.merge(d, cfg, goal, run=run, sleep=tw.NOSLEEP), run


def merged(run):
    return prfake.merge_calls(run.calls)


def reads(run, token):
    return [c for c in run.calls if token in c]


def parked(out, run, condition=None):
    assert out.startswith("PARK:"), out
    assert not merged(run), run.calls
    if condition is not None:
        assert f"condition {condition} " in out, out
    return out


# ------------------------------------------------------------------ the setting: only the exact literal

@pytest.mark.parametrize("value", ["ruleset"])
def test_the_exact_literal_enables_it(value):
    assert work.merge_bypass_enabled(_cfg(merge_bypass=value)) is True


@pytest.mark.parametrize("value", ["off", "Ruleset", "RULESET", "ruleset ", " ruleset", "rulesets", "always", "true",
                                   "", True, 1, ["ruleset"], {"ruleset": True}, None])
def test_anything_but_the_exact_literal_is_off(value):
    assert work.merge_bypass_enabled(_cfg(merge_bypass=value)) is False


def test_an_absent_key_and_a_malformed_config_are_off():
    assert work.merge_bypass_enabled({"work": {"enabled": True}}) is False
    assert work.merge_bypass_enabled({}) is False
    assert work.merge_bypass_enabled({"work": "ruleset"}) is False
    assert work.merge_bypass_enabled(None) is False


# ------------------------------------------------------------------ AC-1: off parks exactly as today

def _baseline(tmp_path):
    cfg = {"work": {k: v for k, v in CFG["work"].items() if k != "merge_bypass"}}
    out, run = drive(tmp_path / "base", world(), cfg)
    return out, run


def test_ac1_unset_parks_a_blocked_pr_and_reads_no_ruleset(tmp_path):
    out, run = _baseline(tmp_path)
    parked(out, run)
    assert "mergeStateStatus=BLOCKED" in out and "GitHub is withholding the merge" in out, out
    assert not reads(run, "/rulesets/") and not reads(run, RULES) and not reads(run, COMPARE), run.calls


@pytest.mark.parametrize("value", ["off", "Ruleset", "ruleset ", "always", True, ["ruleset"], ""])
def test_ac1_any_other_value_parks_byte_identically_to_unset(tmp_path, value):
    base, _ = _baseline(tmp_path)
    out, run = drive(tmp_path / "v", world(), _cfg(merge_bypass=value))
    assert out.replace(str(tmp_path / "v"), "<d>") == base.replace(str(tmp_path / "base"), "<d>"), (out, base)
    assert not reads(run, "/rulesets/") and not merged(run)


def test_ac1_the_setting_never_touches_a_clean_pr(tmp_path):
    """A CLEAN PR merges as ever with the setting on; no ruleset is read and no bypass is claimed."""
    out, run = drive(tmp_path, world(status="CLEAN"))
    assert out.startswith("PR #7 merged (squash)"), out
    assert "bypass" not in out and not reads(run, "/rulesets/") and not reads(run, POST_COMMENT), run.calls
    assert MERGE_PUT in run.calls


# ------------------------------------------------------------------ AC-2: all four hold -> merge, record, comment

def test_ac2_merges_with_the_head_sha_and_names_the_bypassed_rules(tmp_path):
    out, run = drive(tmp_path, world())
    assert out.startswith("PR #7 merged (squash)"), out
    assert MERGE_PUT in run.calls                                   # `-f sha=<vetted head>`
    assert "ruleset bypass" in out and "`basic`" in out, out
    for rule in ("update", "pull_request", "required_status_checks"):
        assert rule in out, out
    posts = reads(run, POST_COMMENT)
    assert len(posts) == 1, posts
    assert "ruleset bypass" in posts[0] and "`basic`" in posts[0] and "update" in posts[0], posts[0]
    # the comment goes out only after the merge landed, never before
    assert run.calls.index(MERGE_PUT) < run.calls.index(posts[0])


def test_ac2_the_merge_evidence_carries_the_bypass(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(work, "_record_confirmed_merge", lambda sdlc, cfg, goal, rec, run, why: seen.append(why))
    out, run = drive(tmp_path, world())
    assert out.startswith("PR #7 merged"), out
    assert len(seen) == 1 and "ruleset bypass" in seen[0] and "`basic`" in seen[0], seen


def test_ac2_the_gate_row_says_the_merge_proceeded_with_a_caveat(tmp_path):
    """No new action-log gate name (the vocabulary is closed): the existing `gate=merge` row reads `warn`, not
    `pass`, and carries the withheld verdict the bypass answered."""
    cfg = {"work": {**CFG["work"]}, "action_log": {"enabled": True}}
    out, run = drive(tmp_path, world(), cfg)
    assert out.startswith("PR #7 merged"), out
    rows = [json.loads(line) for p in (tmp_path / ".sdlc" / "state" / "log").glob("*.jsonl")
            for line in p.read_text(encoding="utf-8").splitlines()]
    gates = [r for r in rows if r.get("gate") == "merge"]
    assert gates and gates[0]["verdict"] == "warn" and "withholding" in gates[0]["why"], rows


def test_ac2_a_failed_comment_is_named_and_never_undoes_a_landed_merge(tmp_path):
    out, run = drive(tmp_path, world(extra=[(POST_COMMENT, RuntimeError("HTTP 502: bad gateway"))]))
    assert out.startswith("PR #7 merged (squash)"), out
    assert "ruleset bypass" in out and "PR comment failed" in out, out


def test_ac2_a_merge_github_refuses_parks_and_says_a_bypass_was_attempted(tmp_path):
    """D-6: the UNMEASURED case. GitHub's REST merge may not honour the bypass; Sigma then parks like any refused
    merge, with one clause saying the bypass was what it tried."""
    out, run = drive(tmp_path, [(prfake.merge_put(7), RuntimeError("HTTP 405: Repository rule violations found"))]
                     + world())
    assert out.startswith("PARK: direct merge of PR #7 was refused"), out
    assert "Repository rule violations found" in out and "ruleset bypass" in out, out
    assert not reads(run, POST_COMMENT), "no comment may claim a bypass that never merged"


def test_ac2_a_strict_rule_that_the_head_satisfies_merges(tmp_path):
    out, run = drive(tmp_path, world(rules=basic_rules(strict=True)))
    assert out.startswith("PR #7 merged"), out
    assert reads(run, COMPARE), run.calls


def test_ac2_a_non_strict_rule_reads_no_compare(tmp_path):
    out, run = drive(tmp_path, world(rules=basic_rules(strict=False)))
    assert out.startswith("PR #7 merged"), out
    assert not reads(run, COMPARE), run.calls


def test_ac2_an_organisation_ruleset_is_read_from_the_org_and_merges(tmp_path):
    rules = [_rule("update", source="Organization", source_name="acme-org")] + basic_rules()[1:]
    org = ("orgs/acme-org/rulesets/1", ruleset_body("org-basic"))
    out, run = drive(tmp_path, world(rules=rules, extra=[org]))
    assert out.startswith("PR #7 merged"), out
    assert "org-basic" in out and reads(run, "orgs/acme-org/rulesets/1"), (out, run.calls)


# ------------------------------------------------------------------ AC-3: each condition, each read

def test_ac3_condition1_a_required_check_with_no_run_on_the_head_parks(tmp_path):
    out, run = drive(tmp_path, world(checks=(("lint", "SUCCESS"),)))
    parked(out, run, 1)
    assert "test" in out, out


def test_ac3_condition1_a_pending_check_in_the_rollup_parks():
    d_rec, run = _unit({"mergeable": "MERGEABLE", "statusCheckRollup": [
        {"name": "test", "conclusion": "SUCCESS"}, {"name": "lint", "status": "IN_PROGRESS"}]})
    ok, text = d_rec()
    assert ok is False and text.startswith("condition 1 "), text


def test_ac3_condition1_a_failing_check_in_the_rollup_parks():
    d_rec, run = _unit({"mergeable": "MERGEABLE", "statusCheckRollup": [
        {"name": "test", "conclusion": "SUCCESS"}, {"name": "lint", "conclusion": "FAILURE"}]})
    ok, text = d_rec()
    assert ok is False and text.startswith("condition 1 "), text


def test_ac3_condition1_an_absent_rollup_parks():
    d_rec, run = _unit({"mergeable": "MERGEABLE"})
    ok, text = d_rec()
    assert ok is False and text.startswith("condition 1 "), text


def test_ac3_condition1_the_required_check_must_have_passed_not_merely_run():
    d_rec, run = _unit({"mergeable": "MERGEABLE", "statusCheckRollup": [{"name": "test", "conclusion": "CANCELLED"}]})
    ok, text = d_rec()
    assert ok is False and text.startswith("condition 1 "), text


def test_ac3_condition2_unreadable_rules_park(tmp_path):
    out, run = drive(tmp_path, world(extra=[(RULES, RuntimeError("HTTP 500: boom"))]))
    parked(out, run, 2)
    assert "rules" in out, out


@pytest.mark.parametrize("body", ["", "null", "{}", "not json", "7"])
def test_ac3_condition2_a_rules_reply_that_is_not_a_list_parks(tmp_path, body):
    out, run = drive(tmp_path, world(rules=body))
    parked(out, run, 2)


def test_ac3_condition2_no_rule_at_all_means_the_block_is_unexplained(tmp_path):
    out, run = drive(tmp_path, world(rules=[]))
    parked(out, run, 2)
    assert "no ruleset" in out, out


def test_ac3_condition2_a_full_page_of_rules_may_be_truncated_and_parks(tmp_path):
    out, run = drive(tmp_path, world(rules=basic_rules() + [_rule("update") for _ in range(97)]))
    assert len(json.loads([h for t, h in world(rules=basic_rules() + [_rule("update") for _ in range(97)])
                           if t == RULES][0])) == 100
    parked(out, run, 2)
    assert "100" in out, out


def test_ac3_condition2_a_merge_queue_is_not_bypassed_by_a_direct_merge(tmp_path):
    out, run = drive(tmp_path, world(rules=basic_rules() + [_rule("merge_queue")]))
    parked(out, run, 2)
    assert "merge queue" in out, out


@pytest.mark.parametrize("bad", [None, "1", True, 0, -1])
def test_ac3_condition2_a_rule_without_an_integer_ruleset_id_parks(tmp_path, bad):
    rules = basic_rules()
    rules[0]["ruleset_id"] = bad
    # any ruleset read would succeed, so only the id check itself can park this
    out, run = drive(tmp_path, world(rules=rules, extra=[("/rulesets/", ruleset_body())]))
    parked(out, run, 2)


def test_ac3_condition2_an_unreadable_ruleset_parks(tmp_path):
    out, run = drive(tmp_path, world(extra=[("/rulesets/1", RuntimeError("HTTP 403: nope"))]))
    parked(out, run, 2)


@pytest.mark.parametrize("body", ["", "null", "[]", "not json"])
def test_ac3_condition2_a_ruleset_reply_that_is_not_an_object_parks(tmp_path, body):
    out, run = drive(tmp_path, world(ruleset=body))
    parked(out, run, 2)


def test_ac3_condition2_an_unknown_rule_source_type_parks(tmp_path):
    rules = basic_rules()
    rules[0]["ruleset_source_type"] = "Enterprise"
    out, run = drive(tmp_path, world(rules=rules))
    parked(out, run, 2)


def test_ac3_condition2_an_organisation_name_that_is_not_a_name_parks(tmp_path):
    rules = basic_rules()
    rules[0].update(ruleset_source_type="Organization", ruleset_source="../x?y")
    out, run = drive(tmp_path, world(rules=rules))
    parked(out, run, 2)
    assert not reads(run, "../x"), run.calls


def test_ac3_condition2_more_than_ten_rulesets_park(tmp_path):
    rules = [_rule("update", ruleset_id=i) for i in range(1, 12)]
    extra = [(f"/rulesets/{i}", ruleset_body()) for i in range(1, 10)]
    out, run = drive(tmp_path, world(rules=rules, extra=extra))
    parked(out, run, 2)


def test_ac3_condition3_a_review_gate_that_did_not_pass_parks_and_names_the_review(tmp_path):
    review = tw._review(decision="CHANGES_REQUESTED", changes_by=["bo"], pr_get=False)
    out, run = drive(tmp_path, world(review=review))
    assert out.startswith("PARK: changes requested by bo"), out
    assert not merged(run) and not reads(run, "/rulesets/"), run.calls


def test_ac3_condition3_approval_mode_without_an_approval_parks(tmp_path):
    review = tw._review(decision=None, pr_get=False)
    out, run = drive(tmp_path, world(review=review), _cfg(require_review="approval"))
    assert out.startswith("PARK:") and "approv" in out.lower(), out
    assert not merged(run)


def test_ac3_condition3_the_bypass_check_asserts_the_review_itself():
    """Defence in depth: `_ruleset_bypass` does not trust its caller's ordering."""
    d_rec, run = _unit(_good_data(), review_ok=False)
    ok, text = d_rec()
    assert ok is False and text.startswith("condition 3 "), text


def test_ac3_condition3_required_reviews_with_the_review_gate_off_park(tmp_path):
    """A ruleset that requires an approving review is bypassed only when SOME review gate stood in for it."""
    out, run = drive(tmp_path, world(), _cfg(require_review="off"))
    parked(out, run, 3)
    assert "require_review" in out, out


def test_ac3_condition3_no_required_reviews_and_the_gate_off_may_proceed(tmp_path):
    out, run = drive(tmp_path, world(rules=basic_rules(reviews=None)), _cfg(require_review="off"))
    assert out.startswith("PR #7 merged"), out


def test_ac3_condition4_a_pr_that_is_not_mergeable_parks():
    for mergeable in ("CONFLICTING", "UNKNOWN", None, "mergeable"):
        d_rec, run = _unit(dict(_good_data(), mergeable=mergeable))
        ok, text = d_rec()
        assert ok is False and text.startswith("condition 4 "), (mergeable, text)


def test_ac3_condition4_a_conflicting_pr_never_reaches_the_bypass(tmp_path):
    out, run = drive(tmp_path, world(mergeable="CONFLICTING"))
    parked(out, run)
    assert "conflicts" in out and not reads(run, "/rulesets/"), out


def test_ac3_condition4_a_strict_rule_and_a_behind_branch_parks(tmp_path):
    out, run = drive(tmp_path, world(rules=basic_rules(strict=True),
                                     compare=json.dumps({"status": "diverged", "behind_by": 3})))
    parked(out, run, 4)
    assert "behind" in out, out


def test_ac3_condition4_a_rule_that_does_not_say_it_is_not_strict_counts_as_strict(tmp_path):
    out, run = drive(tmp_path, world(rules=basic_rules(strict=None),
                                     compare=json.dumps({"status": "behind", "behind_by": 1})))
    parked(out, run, 4)


def test_ac3_condition4_an_unreadable_compare_parks(tmp_path):
    out, run = drive(tmp_path, world(rules=basic_rules(strict=True), extra=[(COMPARE, RuntimeError("HTTP 500: x"))]))
    parked(out, run, 4)


@pytest.mark.parametrize("body", ["", "null", "[]", json.dumps({"status": "ahead"}),
                                  json.dumps({"behind_by": "0"}), json.dumps({"behind_by": True}),
                                  json.dumps({"behind_by": -1})])
def test_ac3_condition4_a_compare_reply_without_an_integer_zero_behind_by_parks(tmp_path, body):
    out, run = drive(tmp_path, world(rules=basic_rules(strict=True), compare=body))
    parked(out, run, 4)


@pytest.mark.parametrize("body", [json.dumps({"behind_by": True}), json.dumps({"behind_by": False}),
                                  json.dumps({"behind_by": -1}), json.dumps({"behind_by": "0"}), json.dumps({"x": 1})])
def test_ac3_condition4_a_behind_by_that_is_not_a_count_is_named_as_such(tmp_path, body):
    out, run = drive(tmp_path, world(rules=basic_rules(strict=True), compare=body))
    parked(out, run, 4)
    assert "named no behind_by" in out, out


def test_ac3_condition4_the_compare_is_pinned_to_the_vetted_head(tmp_path):
    out, run = drive(tmp_path, world(rules=basic_rules(strict=True)))
    assert out.startswith("PR #7 merged"), out
    assert any(c.endswith(f"compare/main...{HEAD}") or f"compare/main...{HEAD} " in c for c in run.calls), run.calls


def test_ac3_a_park_keeps_the_withheld_verdict_and_names_the_setting(tmp_path):
    out, run = drive(tmp_path, world(checks=(("lint", "SUCCESS"),)))
    assert "GitHub is withholding the merge" in out and "work.merge_bypass" in out, out
    assert not reads(run, POST_COMMENT), "a parked bypass posts nothing"


# ------------------------------------------------------------------ AC-4: never bypassed

def test_ac4_classic_branch_protection_is_never_bypassed(tmp_path):
    out, run = drive(tmp_path, world(classic={"required_status_checks": {"contexts": ["test"]}}))
    parked(out, run, 2)
    assert "classic" in out, out


def test_ac4_classic_protection_that_requires_nothing_still_parks(tmp_path):
    out, run = drive(tmp_path, world(classic={"enforce_admins": {"enabled": False}}))
    parked(out, run, 2)
    assert "classic" in out, out


@pytest.mark.parametrize("failure", [RuntimeError("HTTP 500: boom"), RuntimeError("HTTP 403: nope"),
                                     RuntimeError("HTTP 404: Not Found")])
def test_ac4_unreadable_classic_protection_parks(tmp_path, failure):
    out, run = drive(tmp_path, world(classic=failure))
    parked(out, run, 2)
    assert "classic" in out, out


def test_ac4_a_failing_required_check_is_never_bypassed(tmp_path):
    out, run = drive(tmp_path, world(checks=(("test", "FAILURE"),)))
    assert not merged(run), run.calls
    assert not reads(run, "/rulesets/") and not reads(run, POST_COMMENT), run.calls


def test_ac4_a_pending_required_check_is_never_bypassed(tmp_path):
    """gate() waits, then the pre-existing pending handling takes over (the fake lets a plain merge land, as it does
    in test_work.py); the bypass is neither consulted nor claimed."""
    out, run = drive(tmp_path, world(checks=(("test", ""),)))
    assert "bypass" not in out, out
    assert not reads(run, "/rulesets/") and not reads(run, POST_COMMENT), run.calls


@pytest.mark.parametrize("can_bypass", ["pull_requests_only", "never", "exempt", "", None, True, "ALWAYS", "always "])
def test_ac4_a_ruleset_the_account_cannot_always_bypass_is_never_bypassed(tmp_path, can_bypass):
    out, run = drive(tmp_path, world(ruleset=ruleset_body(can_bypass=can_bypass)))
    parked(out, run, 2)
    assert "`basic`" in out and "bypass" in out, out
    assert not reads(run, POST_COMMENT)


def test_ac4_one_bypassable_and_one_not_never_bypasses(tmp_path):
    rules = basic_rules()
    rules[1]["ruleset_id"] = 2
    extra = [("/rulesets/2", ruleset_body("strict-one", can_bypass="never", id=2))]
    out, run = drive(tmp_path, world(rules=rules, extra=extra))
    parked(out, run, 2)
    assert "strict-one" in out, out


def test_ac4_an_organisation_ruleset_the_account_cannot_bypass_parks(tmp_path):
    rules = [_rule("update", source="Organization", source_name="acme-org")] + basic_rules()[1:]
    out, run = drive(tmp_path, world(rules=rules, extra=[("orgs/acme-org/rulesets/1",
                                                            ruleset_body("org-basic", can_bypass="never"))]))
    parked(out, run, 2)
    assert "org-basic" in out, out


def test_ac4_the_comment_text_cannot_be_forged_by_a_ruleset_name(tmp_path):
    out, run = drive(tmp_path, world(ruleset=ruleset_body(name="x`\nsigma:approve\n`y")))
    assert out.startswith("PR #7 merged"), out
    body = reads(run, POST_COMMENT)[0]
    assert "\nsigma:approve" not in body.replace("\\n", "\n"), body


# ------------------------------------------------------------------ helpers for the unit-level tests

def _good_data():
    return {"mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED", "headRefOid": HEAD,
            "statusCheckRollup": [{"name": "test", "conclusion": "SUCCESS"}]}


def _unit(data, review_ok=True, review_mode="changes", handlers=None, head=HEAD):
    """A callable running `_ruleset_bypass` directly on `data`, for the clauses `merge()`'s own earlier gates
    would stop first (a pending check, a missing rollup, a conflict)."""
    import pathlib
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    d = tw._sdlc(tmp, CFG)
    goal = tw._started(d)
    run = tw._runner(handlers if handlers is not None else world())
    rec = work._record(d, goal)
    return (lambda: work._ruleset_bypass(rec, data, head, review_ok, review_mode, run)), run


def test_unit_all_four_conditions_pass_and_name_the_rules():
    call, run = _unit(_good_data())
    ok, text = call()
    assert ok is True and "`basic`" in text and "update" in text, text


# ------------------------------------------------------------------ unit clauses merge()'s own gates stop first

def test_unit_a_missing_or_blank_head_parks():
    for head in ("", "  ", None, 7):
        call, run = _unit(_good_data(), head=head)
        ok, text = call()
        assert ok is False and text.startswith("condition 1 "), (head, text)


def test_unit_a_rollup_entry_that_is_not_an_object_parks():
    call, run = _unit(dict(_good_data(), statusCheckRollup=[None, {"name": "test", "conclusion": "SUCCESS"}]))
    ok, text = call()
    assert ok is False and text.startswith("condition 1 "), text


def test_unit_a_rule_without_a_type_or_that_is_not_an_object_parks():
    for bad in ({"ruleset_id": 1, "ruleset_source_type": "Repository"}, "x", None, 7):
        call, run = _unit(_good_data(), handlers=world(rules=basic_rules() + [bad]))
        ok, text = call()
        assert ok is False and text.startswith("condition 2 "), (bad, text)


def test_withheld_only_recognises_exactly_the_blocked_unexplained_verdict():
    verdict = (f"not safe to merge (mergeStateStatus=BLOCKED) — {work._WITHHELD}, usually a required review or "
               "branch rule: satisfy it on the PR")
    assert work._withheld_only(verdict, {"mergeStateStatus": "BLOCKED"}) is True
    assert work._withheld_only(verdict, {"mergeStateStatus": "UNSTABLE"}) is False
    assert work._withheld_only(verdict, {}) is False
    assert work._withheld_only(verdict, None) is False
    assert work._withheld_only("not safe to merge (mergeStateStatus=BLOCKED) — failing: test",
                               {"mergeStateStatus": "BLOCKED"}) is False
    assert work._withheld_only(f"{work.PENDING_PREFIX} after 450s (mergeStateStatus=BLOCKED) — pending: test",
                               {"mergeStateStatus": "BLOCKED"}) is False
    assert work._withheld_only(f"{work._WITHHELD} (a different prefix)", {"mergeStateStatus": "BLOCKED"}) is False


def test_ac4_a_stale_head_never_reaches_the_bypass(tmp_path):
    """The PR sits at a head this worktree never reviewed: gate() says STALE HEAD, and the bypass must not take
    that verdict over (it would merge the unreviewed head, with every check green about it)."""
    out, run = drive(tmp_path, world(head="b" * 40))
    parked(out, run)
    assert "STALE HEAD" in out and not reads(run, "/rulesets/"), out


def test_ac4_a_behind_pr_never_reaches_the_bypass(tmp_path):
    out, run = drive(tmp_path, world(status="BEHIND"))
    assert not reads(run, "/rulesets/") and not reads(run, POST_COMMENT), run.calls


# ------------------------------------------------------------------ the gesture the docs give

DOCUMENTED_CONFIG = '''{
  "work": {
    "enabled": true,
    "auto_merge": "protected",
    "require_review": "changes",
    "merge_bypass": "ruleset"
  }
}'''


def test_the_documented_config_gesture_turns_it_on_and_the_documented_typos_do_not(tmp_path):
    """docs/configuration.md and docs/cloud-sessions.md show this exact `.sdlc/config.json` block. It is loaded the
    way the CLI loads it (`state.load_config`) and driven through `merge()`; a near miss of the literal parks."""
    d = tw._sdlc(tmp_path, {})
    (tmp_path / ".sdlc" / "config.json").write_text(DOCUMENTED_CONFIG, encoding="utf-8")
    goal = tw._started(d)
    tw._evidence(d, goal)
    cfg = work.state.load_config(d)
    assert work.merge_bypass_enabled(cfg) is True
    run = tw._runner(world())
    assert work.merge(d, cfg, goal, run=run, sleep=tw.NOSLEEP).startswith("PR #7 merged (squash)")
    for typo in ('"merge_bypass": "Ruleset"', '"merge_bypass": "rulesets"', '"merge_bypass": true'):
        (tmp_path / ".sdlc" / "config.json").write_text(
            DOCUMENTED_CONFIG.replace('"merge_bypass": "ruleset"', typo), encoding="utf-8")
        cfg = work.state.load_config(d)
        assert work.merge_bypass_enabled(cfg) is False, typo
        run = tw._runner(world())
        assert work.merge(d, cfg, goal, run=run, sleep=tw.NOSLEEP).startswith("PARK:"), typo

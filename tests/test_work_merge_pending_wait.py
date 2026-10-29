"""#2083: the merge gate's pending-check wait is a config key (AC-3), a cloud session arms a pending set the base
itself requires through the CCR REST route, confirmed by a REST read-back (AC-1), a pending set the base does not
require never arms and parks after the configured wait (AC-2), and the not-armable PARK line and landing.md give
one instruction (AC-4).

The CCR route's payload and reply are UNCONFIRMED live: these tests prove Sigma's argv and its handling of FAKE
replies, never the cloud proxy. Every AC-1/AC-2 test exists twice, once per gesture the docs give for "GraphQL is
refused" (`SIGMA_GH_GRAPHQL=off` and `CLAUDE_CODE_REMOTE=true`).

AC-1 replaces two #895 4b-1 pins in tests/test_work.py whose fixture IS the AC-1 shape and which asserted the old
no-arm PARK: `test_no_graphql_pending_checks_park_without_arming_or_merging` and
`test_no_graphql_branch_under_claude_code_remote_is_structural_only` (deleted, BR-17)."""
import importlib.util
import json
import math
import pathlib
import re

import pytest

import prfake
import test_work as tw

work = tw.work
ROOT = tw.ROOT
HEAD = tw.HEAD_SHA

CCR_PUT = "pulls/7/ccr/auto_merge --method PUT"
CCR_DELETE = "pulls/7/ccr/auto_merge --method DELETE"
CCR_PUT_ARGV = (f"gh api repos/{{owner}}/{{repo}}/pulls/7/ccr/auto_merge --method PUT -f merge_method=squash "
                f"-f sha={HEAD}")
CCR_DELETE_ARGV = "gh api repos/{owner}/{repo}/pulls/7/ccr/auto_merge --method DELETE"
RF8 = "a disarm also removes an auto-merge a human armed on this PR"
CHECK_BY_HAND = "check the PR's auto-merge by hand"

#: always + the `changes` review gate (REST in a cloud session), with the ledger and the action log on so the
#: `merge-armed` / `merge_armed` rows can be counted.
CLOUD = {"work": {"enabled": True, "auto_merge": "always", "require_review": "changes"},
         "ledger": {"enabled": True, "actor": "rae"}, "action_log": {"enabled": True}}


def _cfg(**wk):
    return {"work": {"enabled": True, **wk}}


# ------------------------------------------------------------------ Task 1: the wait is a config key (AC-3)

def _pending_gate(tmp_path, cfg):
    d = tw._sdlc(tmp_path, cfg)
    g = tw._started(d)
    slept = []
    ok, verdict, _ = work.gate(d, cfg, g, run=tw._runner(tw._mixed(("ci", "SUCCESS"), ("slow", ""))),
                               sleep=slept.append)
    assert ok is False and verdict.startswith(work.PENDING_PREFIX), verdict
    return verdict, slept


def test_ac3_absent_key_waits_the_default_450s(tmp_path):
    verdict, slept = _pending_gate(tmp_path, _cfg())
    assert slept == [45] * 10, slept
    assert "after 450s" in verdict, verdict
    assert work.merge_pending_wait_seconds(_cfg()) == (450, "default")


def test_ac3_key_1800_waits_40_rounds(tmp_path):
    verdict, slept = _pending_gate(tmp_path, _cfg(merge_pending_wait_s=1800))
    assert slept == [45] * 40, len(slept)
    assert "after 1800s" in verdict, verdict


def test_ac3_key_rounds_up_to_whole_interval(tmp_path):
    verdict, slept = _pending_gate(tmp_path, _cfg(merge_pending_wait_s=100))
    assert slept == [45] * 3, slept
    assert "after 135s" in verdict, verdict
    assert work.pending_wait(_cfg(merge_pending_wait_s=100)) == (3, 45)


def test_ac3_below_interval_is_raised_to_45(tmp_path):
    cfg = _cfg(merge_pending_wait_s=10)
    assert work.merge_pending_wait_seconds(cfg) == (45, "clamped")
    verdict, slept = _pending_gate(tmp_path, cfg)
    assert slept == [45] and "after 45s" in verdict, (slept, verdict)


def test_ac3_above_cap_is_clamped_to_7200(tmp_path):
    cfg = _cfg(merge_pending_wait_s=99999)
    assert work.MERGE_PENDING_WAIT_MAX == 7200
    assert work.merge_pending_wait_seconds(cfg) == (7200, "clamped")
    verdict, slept = _pending_gate(tmp_path, cfg)
    assert len(slept) == 160 and "after 7200s" in verdict, (len(slept), verdict)


def test_ac3_bool_is_ignored(tmp_path):
    cfg = _cfg(merge_pending_wait_s=True)
    assert work.merge_pending_wait_seconds(cfg) == (450, "invalid")
    _, slept = _pending_gate(tmp_path, cfg)
    assert len(slept) == 10


def test_ac3_string_is_ignored():
    assert work.merge_pending_wait_seconds(_cfg(merge_pending_wait_s="1800")) == (450, "invalid")


def test_ac3_nan_and_inf_are_ignored():
    for v in (math.nan, math.inf, -math.inf):
        assert work.merge_pending_wait_seconds(_cfg(merge_pending_wait_s=v)) == (450, "invalid"), v
        assert work.pending_wait(_cfg(merge_pending_wait_s=v)) == (10, 45), v


def test_ac3_zero_and_negative_are_ignored():
    for v in (0, -5, -0.5):
        assert work.merge_pending_wait_seconds(_cfg(merge_pending_wait_s=v)) == (450, "invalid"), v


def test_ac3_patched_constants_still_drive_the_default(tmp_path, monkeypatch):
    """The default is the constants' product READ AT CALL TIME, so a test (or a later change) that moves the
    constants moves the default with them."""
    monkeypatch.setattr(work, "PENDING_ATTEMPTS", 2)
    verdict, slept = _pending_gate(tmp_path, _cfg())
    assert slept == [45, 45] and "after 90s" in verdict, (slept, verdict)


def test_ac3_budget_follows_the_configured_wait():
    cfg = _cfg(merge_pending_wait_s=1800)
    assert work.merge_budget_seconds(cfg) - work.merge_budget_seconds(None) >= 4 * 1350
    assert work.merge_budget_seconds(_cfg()) >= work.merge_budget_seconds(None)


def test_ac3_budget_at_cap_stays_under_max_run_seconds():
    at_cap = work.merge_budget_seconds(_cfg(merge_pending_wait_s=7200))
    assert work.merge_budget_seconds(None) < at_cap < work._rr.MAX_RUN_SECONDS, at_cap


def test_ac3_receipt_timeout_uses_config(tmp_path, monkeypatch):
    cfg = _cfg(merge_pending_wait_s=1800)
    d = tw._sdlc(tmp_path, cfg)
    monkeypatch.setattr(work, "merge", lambda *a, **k: "PR #7 merged (squash)")
    writes, real = [], work._rr.write

    def spy(path, **fields):
        if str(path).endswith(".merge.json"):
            writes.append(dict(fields))
        return real(path, **fields)
    monkeypatch.setattr(work._rr, "write", spy)
    assert work.merge_with_receipt(d, cfg, "0001", sleep=lambda s: None) == "PR #7 merged (squash)"
    assert writes and writes[0]["timeout_seconds"] == work.merge_budget_seconds(cfg), writes
    assert writes[0]["timeout_seconds"] != work.merge_budget_seconds(None)


def test_sibling_gate_keeps_the_default_wait_when_the_key_is_set(tmp_path):
    """D-3 pin: the sibling gate waits on ANOTHER repo's CI and stays on the constants. Green on the base by
    construction -- this pins the decision; break 16 (the sibling gate made to follow the key) proves it."""
    cfg = {"work": {"enabled": True, "auto_merge": "always", "merge_pending_wait_s": 1800}}
    d, goal = tw._pair(tmp_path, config=cfg)
    slept = []
    run = tw._runner(tw._rights() + tw._sibling(checks=(("ci", "SUCCESS"), ("slow", ""))))
    ok, why = work.sibling_gate(d, cfg, goal, run=run, sleep=slept.append)
    assert ok is False and "slow" in why
    assert len(slept) == work.PENDING_ATTEMPTS, len(slept)


# ------------------------------------------------------------------ Task 2: doctor mirrors the reader (AC-3)

def _doctor():
    path = ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py"
    spec = importlib.util.spec_from_file_location("doctor_2083", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_ac3_doctor_mirror_matches_work_reader():
    """doctor.py duplicates (never imports) work's reader; this loads both by path and fails on drift."""
    doc = _doctor()
    for raw in ("absent", 450, 1800, 30, 99999, True, "x", math.nan, -5, 100):
        wk = {"enabled": True} if raw == "absent" else {"enabled": True, "merge_pending_wait_s": raw}
        shown = doc._merge_pending_wait_state(wk)
        seconds = float(shown.split(" s ", 1)[0])
        assert seconds == work.merge_pending_wait_seconds({"work": wk})[0], (raw, shown)


# ------------------------------------------------------------------ Task 3: the cloud arm (AC-1, AC-2)

def _gesture(monkeypatch, gesture):
    if gesture == "graphql_off":
        monkeypatch.setenv("SIGMA_GH_GRAPHQL", "off")
    else:
        monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")


def _armed(head=HEAD, method="squash", **kw):
    """The REST pull AFTER a confirmed arm: open, `auto_merge` set with `method`, on `head`."""
    return prfake.rest_pull(headRefOid=head, autoMergeRequest={"merge_method": method}, mergeStateStatus="BLOCKED",
                            **kw)


NOT_ARMED = prfake.rest_pull(autoMergeRequest=None, mergeStateStatus="BLOCKED")


def _pull_after_put(box, before, after):
    """N2: a stateful `PR_GET` handler (the `_seq` style). The read-back's argv is the same `pulls/7 --method GET`
    that merge_rights and every gate() round use and `_runner` takes the first substring match, so a static body
    could not tell the read-back apart. Serves `before` until a CCR PUT is in the runner's calls (`box[0]` is the
    runner, filled in once it exists), then `after` -- a single body, or a SEQUENCE served in order with the last
    reused (W4: the first and second post-PUT reads differ)."""
    afters = list(after) if isinstance(after, (list, tuple)) else [after]

    def answer(_line):
        if not any(CCR_PUT in c for c in box[0].calls):
            a = before
        else:
            a = afters.pop(0) if len(afters) > 1 else afters[0]
        if isinstance(a, Exception):
            raise a
        return a
    return answer


def _cloud(tmp_path, monkeypatch, gesture="graphql_off", *, after=NOT_ARMED, pending=(("ci", "SUCCESS"), ("slow", "")),
           status="BLOCKED", protection=None, allow=True, put="", delete="", wait=None):
    """One merge() in a cloud session: every GraphQL-shaped call refuses (`_cloud_merge_run`); the CCR PUT and
    DELETE handlers come FIRST (`api repos/{owner}/{repo}/pulls/7` is a substring of their argv)."""
    _gesture(monkeypatch, gesture)
    cfg = json.loads(json.dumps(CLOUD))
    if wait is not None:
        cfg["work"]["merge_pending_wait_s"] = wait
    d = tw._sdlc(tmp_path, cfg)
    g = tw._started(d)
    tw._evidence(d, g)
    tw.ledger.reset_actor_cache()
    work.ledger.reset_actor_cache()
    gate = tw._mixed(*pending, status=status)
    box = []
    handlers = ([(CCR_PUT, put), (CCR_DELETE, delete), (tw.PR_GET, _pull_after_put(box, gate[0][1], after))]
                + gate[1:] + tw._cloud_reviewless() + tw._perm()
                + (tw._protected(checks=("ci", "slow")) if protection is None else protection)
                + tw._auto_merge_allowed(allow))
    run = tw._cloud_merge_run(*handlers)
    box.append(run)
    slept = []
    out = work.merge(d, cfg, g, run=run, sleep=slept.append)
    return out, run, slept, d, g


def _ccr(run, token):
    return [c for c in run.calls if token in c]


def _armed_rows(d, g):
    return ([e for e in tw.ledger.read_all(d) if e["kind"] == "merge-armed"],
            [e for e in tw.actionlog.read_goal(d, g) if e["kind"] == "merge_armed"])


def _never_merged(run):
    assert prfake.merge_calls(run.calls) == [], prfake.merge_calls(run.calls)
    assert not any("pr merge" in c for c in run.calls)


def _assert_ac1_arm(out, run, d, g):
    assert out.startswith("auto-merge armed on PR #7 via the cloud REST route"), out
    assert "slow" in out and "trusting GitHub's own re-check at merge time" in out, out
    assert _ccr(run, CCR_PUT) == [CCR_PUT_ARGV], run.calls
    assert _ccr(run, CCR_DELETE) == []
    _never_merged(run)
    put_at = next(i for i, c in enumerate(run.calls) if CCR_PUT in c)
    last_get = max(i for i, c in enumerate(run.calls) if tw.PR_GET in c)
    assert last_get > put_at, "the arm must be confirmed by a PR read AFTER the PUT (N2)"
    ledger_rows, action_rows = _armed_rows(d, g)
    assert len(ledger_rows) == 1 and len(action_rows) == 1, (ledger_rows, action_rows)


def test_ac1_cloud_arms_a_required_pending_check_via_ccr_graphql_off(tmp_path, monkeypatch):
    """AC-1 under `SIGMA_GH_GRAPHQL=off`. Replaces test_work.py's
    `test_no_graphql_pending_checks_park_without_arming_or_merging` (same fixture, old no-arm PARK)."""
    out, run, _, d, g = _cloud(tmp_path, monkeypatch, "graphql_off", after=_armed())
    _assert_ac1_arm(out, run, d, g)


def test_ac1_cloud_arms_a_required_pending_check_via_ccr_remote(tmp_path, monkeypatch):
    """AC-1 under `CLAUDE_CODE_REMOTE=true`. Replaces test_work.py's
    `test_no_graphql_branch_under_claude_code_remote_is_structural_only`."""
    out, run, _, d, g = _cloud(tmp_path, monkeypatch, "remote", after=_armed())
    _assert_ac1_arm(out, run, d, g)


def test_ac1_rulesets_only_requirement_arms(tmp_path, monkeypatch):
    """The required set is the UNION of classic protection and rulesets: here only a ruleset requires `slow`."""
    out, run, _, d, g = _cloud(tmp_path, monkeypatch, after=_armed(), pending=(("slow", ""),),
                               protection=tw.UNPROTECTED + tw._ruleset(checks=("slow",)))
    _assert_ac1_arm(out, run, d, g)


def _not_armable(waited, reason):
    return (f"PARK: required checks still pending after {waited}s and auto-merge cannot be armed without GraphQL "
            f"({reason}); {work._PENDING_PARK_INSTRUCTION}")


def _assert_no_arm_attempt(run, allow_read=False):
    assert _ccr(run, CCR_PUT) == [] and _ccr(run, CCR_DELETE) == [], run.calls
    assert any("allow_auto_merge" in c for c in run.calls) is allow_read
    _never_merged(run)


def test_ac2_cloud_non_required_pending_waits_then_parks_graphql_off(tmp_path, monkeypatch):
    out, run, slept, _, _ = _cloud(tmp_path, monkeypatch, "graphql_off", status="UNSTABLE", wait=90,
                                   protection=tw._protected(checks=("ci",)))
    assert slept == [45, 45], slept
    assert out == _not_armable(90, "not required by `main`: slow"), out
    _assert_no_arm_attempt(run)


def test_ac2_cloud_non_required_pending_waits_then_parks_remote(tmp_path, monkeypatch):
    out, run, slept, _, _ = _cloud(tmp_path, monkeypatch, "remote", status="UNSTABLE", wait=90,
                                   protection=tw._protected(checks=("ci",)))
    assert slept == [45, 45], slept
    assert out == _not_armable(90, "not required by `main`: slow"), out
    _assert_no_arm_attempt(run)


def test_ac2_mixed_required_and_non_required_pending_does_not_arm(tmp_path, monkeypatch):
    out, run, _, _, _ = _cloud(tmp_path, monkeypatch, pending=(("ci", "SUCCESS"), ("slow", ""), ("lint", "")))
    assert out == _not_armable(450, "not required by `main`: lint"), out
    _assert_no_arm_attempt(run)


def test_cloud_disallowed_auto_merge_parks_without_ccr(tmp_path, monkeypatch):
    out, run, _, _, _ = _cloud(tmp_path, monkeypatch, allow=False)
    assert out == _not_armable(450, "the repository does not allow auto-merge"), out
    _assert_no_arm_attempt(run, allow_read=True)


def test_cloud_protection_undetermined_never_arms(tmp_path, monkeypatch):
    """#984's stop runs BEFORE the cloud branch: a required set nobody could read never arms."""
    undetermined = [("branches/main/protection", RuntimeError("HTTP 404: Not Found")),
                    (tw.RULES_READ, RuntimeError("HTTP 403: Upgrade to GitHub Pro or make this repository public"))]
    out, run, _, _, _ = _cloud(tmp_path, monkeypatch, protection=undetermined, after=_armed())
    assert "protection could not be determined" in out and "merging it is yours to make" in out, out
    _assert_no_arm_attempt(run)


def test_cloud_empty_required_set_never_arms(tmp_path, monkeypatch):
    out, run, _, _, _ = _cloud(tmp_path, monkeypatch, protection=tw.NOTHING_ENFORCED, after=_armed())
    assert out == _not_armable(450, "not required by `main`: slow"), out
    _assert_no_arm_attempt(run)


def test_cloud_arm_predicate_refuses_empty_pending():
    blocked = {"mergeStateStatus": "BLOCKED"}
    names, reason = work._cloud_arm_refusal({**blocked, "statusCheckRollup": []}, frozenset({"ci"}), "main")
    assert names == [] and reason, reason
    ok_names, ok_reason = work._cloud_arm_refusal(
        {**blocked, "statusCheckRollup": [{"name": "ci", "conclusion": ""}]}, frozenset({"ci"}), "main")
    assert ok_names == ["ci"] and ok_reason is None, ok_reason       # the positive control


def test_cloud_unstable_merge_state_never_arms_even_when_protection_lists_the_check(tmp_path, monkeypatch):
    """B1: UNSTABLE is GitHub's own verdict that only NON-required checks are pending, so it would merge now;
    protection() listing `slow` (bypass actors, evaluate-mode rulesets) must not override that."""
    out, run, _, _, _ = _cloud(tmp_path, monkeypatch, status="UNSTABLE", after=_armed())
    assert out == _not_armable(
        450, "GitHub reports mergeStateStatus=UNSTABLE, not BLOCKED: it does not hold the merge on them"), out
    _assert_no_arm_attempt(run)


def test_cloud_unnamed_pending_check_never_arms(tmp_path, monkeypatch):
    """RF-3: an unnamed pending entry could be a non-required check `ci_required: true` waits on."""
    out, run, _, _, _ = _cloud(tmp_path, monkeypatch, pending=(("ci", "SUCCESS"), ("slow", ""), ("", "")),
                               after=_armed())
    assert out == _not_armable(450, "a pending check has no name"), out
    _assert_no_arm_attempt(run)


def test_cloud_ccr_put_definite_refusal_parks_without_readback(tmp_path, monkeypatch):
    out, run, _, d, g = _cloud(tmp_path, monkeypatch, put=RuntimeError("HTTP 403: Resource not accessible by integration"),
                               after=_armed())
    assert out.startswith("PARK: could not arm auto-merge on PR #7 via the cloud REST route ("), out
    put_at = next(i for i, c in enumerate(run.calls) if CCR_PUT in c)
    assert not any(tw.PR_GET in c for c in run.calls[put_at + 1:]), "a definite refusal armed nothing: no read-back"
    assert _ccr(run, CCR_DELETE) == []
    _never_merged(run)
    assert _armed_rows(d, g) == ([], [])


def test_cloud_ccr_put_unknown_outcome_reads_back_and_confirms(tmp_path, monkeypatch):
    out, run, _, d, g = _cloud(tmp_path, monkeypatch, put=RuntimeError("HTTP 502: Bad Gateway"), after=_armed())
    _assert_ac1_arm(out, run, d, g)


def test_cloud_ccr_put_unknown_outcome_disarms_when_not_armed(tmp_path, monkeypatch):
    out, run, _, d, g = _cloud(tmp_path, monkeypatch, put=RuntimeError("HTTP 502: Bad Gateway"), after=NOT_ARMED)
    assert out.startswith("PARK: auto-merge arm on PR #7 could not be confirmed (PUT outcome unknown ("), out
    assert "no auto-merge on the PR after the PUT" in out, out
    assert _ccr(run, CCR_DELETE) == [CCR_DELETE_ARGV]
    _never_merged(run)


def test_cloud_readback_sees_a_late_arm_on_the_second_read(tmp_path, monkeypatch):
    """R4/W4: the first post-PUT read shows no arm yet, the second (one RECONCILE_PAUSE later) shows it."""
    out, run, slept, d, g = _cloud(tmp_path, monkeypatch, after=[NOT_ARMED, _armed()])
    _assert_ac1_arm(out, run, d, g)
    assert slept.count(work.RECONCILE_PAUSE) == 1, slept


def _unconfirmed(why, disarm="sent"):
    return (f"PARK: auto-merge arm on PR #7 could not be confirmed ({why}); disarm {disarm} — {RF8}; "
            f"{CHECK_BY_HAND}")


def test_cloud_readback_without_auto_merge_disarms_and_parks(tmp_path, monkeypatch):
    out, run, slept, d, g = _cloud(tmp_path, monkeypatch, after=NOT_ARMED)
    assert out == _unconfirmed("no auto-merge on the PR after the PUT"), out
    assert _ccr(run, CCR_DELETE) == [CCR_DELETE_ARGV]
    assert slept.count(work.RECONCILE_PAUSE) == 1                       # read twice before disarming (R4)
    _never_merged(run)
    assert _armed_rows(d, g) == ([], [])


def test_cloud_readback_on_another_head_disarms_and_parks(tmp_path, monkeypatch):
    out, run, _, _, _ = _cloud(tmp_path, monkeypatch, after=_armed(head="1" * 40))
    assert out == _unconfirmed("armed on 1111111, not the vetted 0000000"), out
    assert _ccr(run, CCR_DELETE) == [CCR_DELETE_ARGV]
    _never_merged(run)


def test_cloud_readback_wrong_method_disarms_and_parks(tmp_path, monkeypatch):
    out, run, _, _, _ = _cloud(tmp_path, monkeypatch, after=_armed(method="merge"))
    assert out == _unconfirmed("armed with merge, not squash"), out
    assert RF8 in out
    assert _ccr(run, CCR_DELETE) == [CCR_DELETE_ARGV]
    _never_merged(run)


def test_cloud_readback_unreadable_disarms_and_parks(tmp_path, monkeypatch):
    malformed = prfake.rest_pull(autoMergeRequest=prfake.ABSENT, mergeStateStatus="BLOCKED")
    out, run, _, _, _ = _cloud(tmp_path, monkeypatch, after=malformed)
    assert out.startswith("PARK: auto-merge arm on PR #7 could not be confirmed (read-back unreadable: "), out
    assert "no auto_merge key" in out and out.endswith(f"disarm sent — {RF8}; {CHECK_BY_HAND}"), out
    assert _ccr(run, CCR_DELETE) == [CCR_DELETE_ARGV]
    _never_merged(run)


def test_cloud_disarm_failure_is_named_in_the_park(tmp_path, monkeypatch):
    out, run, _, _, _ = _cloud(tmp_path, monkeypatch, after=NOT_ARMED, delete=RuntimeError("HTTP 500: boom"))
    assert out.startswith("PARK: auto-merge arm on PR #7 could not be confirmed (no auto-merge on the PR after "
                          "the PUT); disarm failed: "), out
    assert "boom" in out and CHECK_BY_HAND in out, out
    assert _ccr(run, CCR_DELETE) == [CCR_DELETE_ARGV]


def test_cloud_readback_shows_merged_is_not_parked_and_sends_no_delete(tmp_path, monkeypatch):
    """B2: GitHub landed the PR while the arm was being confirmed. Two fixtures: the realistic one (`merged_at`
    set) and the inverse (`merged: true`, `merged_at` null), so both the `state == MERGED` check and its
    `mergedAt` belt are needed to keep this green (break 18)."""
    for i, after in enumerate((prfake.rest_pull(state="MERGED"), prfake.rest_pull(state="MERGED", merged_at=None))):
        out, run, _, d, g = _cloud(tmp_path / str(i), monkeypatch, after=after)
        assert out.startswith("PR #7 merged when auto-merge was armed, before the pending checks (slow) answered"), out
        assert "WARNING: GitHub did not hold it on them" in out, out
        assert not out.startswith("PR #7 merged ("), out
        assert _ccr(run, CCR_DELETE) == []
        assert _armed_rows(d, g) == ([], [])


def test_cloud_readback_shows_closed_parks_without_delete(tmp_path, monkeypatch):
    out, run, _, d, g = _cloud(tmp_path, monkeypatch, after=prfake.rest_pull(state="CLOSED"))
    assert out == ("PARK: PR #7 was closed while auto-merge was being armed (not merged); reopen it or merge it "
                   "by hand"), out
    assert _ccr(run, CCR_DELETE) == []
    assert _armed_rows(d, g) == ([], [])


def test_cloud_arm_writes_no_merge_armed_row_unless_confirmed(tmp_path, monkeypatch):
    _, _, _, d, g = _cloud(tmp_path / "unconfirmed", monkeypatch, after=NOT_ARMED)
    assert _armed_rows(d, g) == ([], [])
    _, _, _, d, g = _cloud(tmp_path / "confirmed", monkeypatch, after=_armed())
    assert [len(rows) for rows in _armed_rows(d, g)] == [1, 1]


# ------------------------------------------------------------------ Task 4: one instruction (AC-4)

LANDING = ROOT / "skills" / "sigma-loop" / "references" / "landing.md"


def _flat(text):
    return re.sub(r"\s+", " ", text)


def _bullet(prefix):
    """The landing.md decision-table bullet whose text begins `- \\`<prefix>`, flattened (continuation lines
    are indented; the bullet ends at the next line that is not)."""
    lines = LANDING.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("- `" + prefix):
            body = [line]
            for nxt in lines[i + 1:]:
                if not nxt.startswith("  "):
                    break
                body.append(nxt)
            return _flat(" ".join(body))
    raise AssertionError(f"no landing.md bullet begins `{prefix}`")


def test_ac4_landing_md_bullet_carries_the_park_lines_instruction():
    bullet = _bullet("PARK: required checks still pending after")
    assert work._PENDING_PARK_INSTRUCTION in bullet, bullet
    assert "→ `record parked`" not in bullet, "the route must be the instruction, not a bare record parked"


def test_ac4_park_line_ends_with_the_instruction(tmp_path, monkeypatch):
    out, _, _, _, _ = _cloud(tmp_path, monkeypatch, allow=False)
    assert out.startswith("PARK: required checks still pending after"), out
    assert out.endswith("; " + work._PENDING_PARK_INSTRUCTION), out


def test_landing_md_routes_the_merged_when_armed_line_to_record_review():
    bullet = _bullet("PR #N merged when auto-merge was armed")
    assert "**`record review`**" in bullet and "record done" in bullet and "does not begin" in bullet, bullet


def test_landing_md_routes_the_unconfirmed_arm_park_to_a_human_check():
    """W2: a failed disarm may leave a live arm on a PARKED goal; the human is told to check for a landing."""
    bullet = _bullet("PARK: auto-merge arm on PR #N could not be confirmed")
    assert "`record parked" in bullet and "already merged" in bullet and "`record done`" in bullet, bullet


# ------------------------------------------------------------------ Task 5: prose and config discovery

def test_ac3_key_is_documented_in_configuration_md_and_the_template():
    conf = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    row = next((ln for ln in conf.splitlines() if "merge_pending_wait_s" in ln), "")
    assert "450" in row and "7200" in row, row
    tmpl = (ROOT / "skills" / "sigma-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8")
    assert '"merge_pending_wait_s": 450' in tmpl and '"_merge_pending_wait_s"' in tmpl


def test_cloud_sessions_doc_labels_the_ccr_arm_unconfirmed():
    text = (ROOT / "docs" / "cloud-sessions.md").read_text(encoding="utf-8")
    paras = [p for p in re.split(r"\n\s*\n", text) if "ccr/auto_merge" in p]
    assert paras, "docs/cloud-sessions.md never names the CCR route"
    assert any("UNCONFIRMED" in p for p in paras), paras
    flat = _flat(text)
    for phrase in ("merge_pending_wait_s", "UNMEASURED", "head-pin", "cloud path only"):
        assert phrase in flat, phrase

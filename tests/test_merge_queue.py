"""Tests for skills/sigma-status/scripts/merge_queue.py — the read-only merge-queue detect +
recommend advisor (#976, split (b) of #408). Every check here must fail OPEN (return None / no
recommendation), never raise, on any unreadable/malformed/absent `gh api` response — a false
positive is a much worse outcome than a missed one for an advisory-only feature."""
import importlib.util
import json
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-status" / "scripts"


def _mq():
    spec = importlib.util.spec_from_file_location("merge_queue", S / "merge_queue.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- _api: the repo-prefix bug this file must pin (PR-review finding) --------------------------
# A prior version of `_api()` took a `repo` argument and never used it -- every call site built a
# bare endpoint-relative path (e.g. `branches/main/protection`), which `gh api` happily accepts but
# resolves against the wrong (ambient/current) repo, not the configured target. Fail-open masked it
# completely: a wrong-repo read 404s/403s exactly like an unreachable one, so a mock that only
# checked "does a bad response collapse to None" passed regardless of which repo was actually
# queried. These tests assert the LITERAL path sent to `run()`, not just a loose substring.

def test_api_prefixes_every_call_with_repos_repo():
    mq = _mq()
    seen = []

    def run(args):
        seen.append(args)
        return ""
    mq._api(run, "acme/widget", "branches/main/protection")
    assert seen == [["gh", "api", "repos/acme/widget/branches/main/protection"]]


# --- strict_protection ------------------------------------------------------------------------

def _protection_run(strict=True, contexts=("ci/build",), status=200):
    body = {
        "required_status_checks": {"strict": strict, "contexts": list(contexts)},
    }

    def run(args):
        assert args[:2] == ["gh", "api"]
        assert args[2] == "repos/acme/widget/branches/main/protection"   # full path, not a substring
        if status != 200:
            return ""                          # _real_run's own convention: nonzero exit -> ""
        return json.dumps(body)
    return run


def test_strict_protection_true_when_strict_and_checks_named():
    mq = _mq()
    assert mq.strict_protection("acme/widget", "main", _protection_run(strict=True)) is True


def test_strict_protection_false_when_not_strict():
    mq = _mq()
    assert mq.strict_protection("acme/widget", "main", _protection_run(strict=False)) is False


def test_strict_protection_false_when_no_required_checks_named():
    mq = _mq()
    assert mq.strict_protection("acme/widget", "main", _protection_run(strict=True, contexts=())) is False


def test_strict_protection_false_on_unreachable_or_unprotected():
    mq = _mq()
    # 403 (this repo, live) and 404 (genuinely unprotected) both collapse to "" via _real_run's
    # success-only contract -- both must read as "not strict", never raise.
    assert mq.strict_protection("acme/widget", "main", lambda a: "") is False


def test_strict_protection_false_on_malformed_json():
    mq = _mq()
    assert mq.strict_protection("acme/widget", "main", lambda a: "not json") is False
    assert mq.strict_protection("acme/widget", "main", lambda a: "null") is False
    assert mq.strict_protection("acme/widget", "main", lambda a: "42") is False
    assert mq.strict_protection("acme/widget", "main", lambda a: '["a", "list"]') is False


def test_strict_protection_accepts_the_newer_checks_shape():
    """GitHub's REST response carries required checks under `contexts` (legacy) or `checks` (a list
    of {context, app_id} objects, newer). Either non-empty must count."""
    mq = _mq()
    body = {"required_status_checks": {"strict": True, "checks": [{"context": "ci/build", "app_id": 1}]}}
    run = lambda a: json.dumps(body)
    assert mq.strict_protection("acme/widget", "main", run) is True


# --- merge_burst -------------------------------------------------------------------------------

def _pr(merged_at=None, closed_only=False):
    return {"merged_at": merged_at, "state": "closed"}


def _pulls_run(prs):
    def run(args):
        assert args[:2] == ["gh", "api"]
        assert args[2] == ("repos/acme/widget/pulls?state=closed&base=main"
                            "&sort=updated&direction=desc&per_page=100")   # full path, not a substring
        return json.dumps(prs)
    return run


def test_merge_burst_fires_at_threshold_within_window():
    mq = _mq()
    # 5 merges all within a few minutes of each other -> well within a 24h window
    prs = [_pr(f"2026-08-14T10:0{i}:00Z") for i in range(5)]
    result = mq.merge_burst("acme/widget", "main", _pulls_run(prs), window_hours=24, threshold=5)
    assert result == (5, 24)


def test_merge_burst_none_below_threshold():
    mq = _mq()
    prs = [_pr(f"2026-08-14T10:0{i}:00Z") for i in range(4)]   # one short of threshold=5
    assert mq.merge_burst("acme/widget", "main", _pulls_run(prs), window_hours=24, threshold=5) is None


def test_merge_burst_ignores_closed_but_unmerged_prs():
    """state=closed also returns rejected/abandoned PRs (merged_at is null) -- counting those would
    let a burst of ABANDONED PRs masquerade as a landing burst. PLAN-REVIEW fix, pinned here."""
    mq = _mq()
    prs = [_pr(f"2026-08-14T10:0{i}:00Z") for i in range(2)] + [_pr(None) for _ in range(10)]
    # only 2 real merges -> below threshold=5, even though 12 PRs total closed
    assert mq.merge_burst("acme/widget", "main", _pulls_run(prs), window_hours=24, threshold=5) is None


def test_merge_burst_anchors_on_newest_merge_not_wall_clock():
    """Deterministic: the window is measured from the newest merged_at in the sample, not from
    'now' -- so a fixture dated in the past still fires with no time mocking required."""
    mq = _mq()
    prs = [_pr("2020-01-01T00:0%d:00Z" % i) for i in range(6)]
    result = mq.merge_burst("acme/widget", "main", _pulls_run(prs), window_hours=1, threshold=5)
    assert result == (6, 1)


def test_merge_burst_excludes_merges_outside_the_window():
    mq = _mq()
    # 3 recent merges (anchor cluster) + 3 old ones outside a 1-hour window from the anchor
    prs = ([_pr("2026-08-14T12:0%d:00Z" % i) for i in range(3)] +
           [_pr("2026-08-10T12:00:00Z") for _ in range(3)])
    result = mq.merge_burst("acme/widget", "main", _pulls_run(prs), window_hours=1, threshold=3)
    assert result == (3, 1)


def test_merge_burst_none_on_malformed_response():
    mq = _mq()
    assert mq.merge_burst("acme/widget", "main", lambda a: "", threshold=1) is None
    assert mq.merge_burst("acme/widget", "main", lambda a: "not json", threshold=1) is None
    assert mq.merge_burst("acme/widget", "main", lambda a: "{}", threshold=1) is None    # dict, not list


def test_merge_burst_skips_entries_with_malformed_merged_at():
    mq = _mq()
    prs = [_pr("not-a-timestamp"), _pr(None), {"nope": True}, "not-a-dict"]
    assert mq.merge_burst("acme/widget", "main", _pulls_run(prs), threshold=1) is None


# --- growing_required_check --------------------------------------------------------------------

def _run_entry(started, updated):
    return {"run_started_at": started, "updated_at": updated}


def _runs_run(entries):
    body = {"workflow_runs": entries}

    def run(args):
        assert args[:2] == ["gh", "api"]
        assert args[2] == "repos/acme/widget/actions/runs?per_page=30"   # full path, not a substring
        return json.dumps(body)
    return run


def test_growing_required_check_detects_a_real_growth_trend():
    mq = _mq()
    # 3 newest runs ~700s each, 3 older runs ~200s each -- newest-first order (API convention)
    newest = [_run_entry(f"2026-08-14T10:0{i}:00Z", f"2026-08-14T10:1{i+1}:40Z") for i in range(3)]
    oldest = [_run_entry("2026-08-10T10:00:00Z", "2026-08-10T10:03:20Z") for _ in range(3)]
    result = mq.growing_required_check("acme/widget", _runs_run(newest + oldest))
    assert result is not None
    avg_old, avg_new = result
    assert avg_new > avg_old


def test_growing_required_check_none_when_flat():
    mq = _mq()
    runs = [_run_entry("2026-08-14T10:00:00Z", "2026-08-14T10:05:00Z") for _ in range(8)]
    assert mq.growing_required_check("acme/widget", _runs_run(runs)) is None


def test_growing_required_check_none_with_too_few_runs():
    mq = _mq()
    runs = [_run_entry("2026-08-14T10:00:00Z", "2026-08-14T10:10:00Z") for _ in range(4)]
    assert mq.growing_required_check("acme/widget", _runs_run(runs)) is None


def test_growing_required_check_none_below_the_absolute_floor():
    """A trend that's technically 'growing' (ratio-wise) but stays under a few minutes total is
    noise, not the #408 shape -- must not fire."""
    mq = _mq()
    newest = [_run_entry("2026-08-14T10:00:00Z", "2026-08-14T10:00:10Z") for _ in range(3)]  # 10s
    oldest = [_run_entry("2026-08-10T10:00:00Z", "2026-08-10T10:00:03Z") for _ in range(3)]  # 3s
    assert mq.growing_required_check("acme/widget", _runs_run(newest + oldest)) is None


def test_growing_required_check_none_on_malformed_or_absent_actions():
    mq = _mq()
    assert mq.growing_required_check("acme/widget", lambda a: "") is None
    assert mq.growing_required_check("acme/widget", lambda a: "not json") is None
    assert mq.growing_required_check("acme/widget", lambda a: "[]") is None            # list, not dict
    assert mq.growing_required_check("acme/widget", lambda a: '{"workflow_runs": "nope"}') is None


# --- advise() (the full gate; positive fixture + negative control) -------------------------------

def _combined_run(protection_body, prs, actions_body=None):
    _PREFIX = "repos/acme/widget/"

    def run(args):
        assert args[:2] == ["gh", "api"]
        path = args[2]
        assert path.startswith(_PREFIX), f"missing repos/{{repo}}/ prefix: {path!r}"   # the PR-review bug, pinned
        rest = path[len(_PREFIX):]
        if rest.startswith("branches/") and "/protection" in rest:
            return json.dumps(protection_body) if protection_body is not None else ""
        if rest.startswith("pulls?"):
            return json.dumps(prs) if prs is not None else ""
        if rest.startswith("actions/runs"):
            return json.dumps(actions_body) if actions_body is not None else ""
        raise AssertionError(f"unexpected gh api call: {path}")
    return run


_PROTECTED = {"required_status_checks": {"strict": True, "contexts": ["ci/build"]}}


def test_advise_none_with_no_repo_configured():
    mq = _mq()
    assert mq.advise({}, lambda a: (_ for _ in ()).throw(AssertionError("must not call gh"))) is None


def test_advise_none_when_not_strict_protected_and_makes_no_further_calls():
    """Short-circuit: an unprotected repo must not spend a 2nd/3rd `gh api` call at all."""
    mq = _mq()
    calls = []

    def run(args):
        calls.append(args)
        return ""                              # not protected
    assert mq.advise({"repo": "acme/widget"}, run) is None
    assert len(calls) == 1                      # only the protection check ran


def test_advise_none_when_protected_but_no_burst():
    mq = _mq()
    prs = [_pr(f"2026-08-14T10:0{i}:00Z") for i in range(2)]   # below default threshold
    run = _combined_run(_PROTECTED, prs)
    assert mq.advise({"repo": "acme/widget"}, run) is None


def test_advise_fires_the_positive_fixture():
    """The #408 shape: strict-protected base + a real merge burst."""
    mq = _mq()
    prs = [_pr(f"2026-08-14T10:0{i}:00Z") for i in range(6)]
    run = _combined_run(_PROTECTED, prs)
    msg = mq.advise({"repo": "acme/widget"}, run)
    assert msg is not None
    assert "merge queue" in msg
    assert "6" in msg


def test_advise_negative_control_healthy_repo_no_protection():
    """A healthy repo with no branch protection at all must never fire, no matter how many PRs
    merge -- there's nothing to serialize against in the first place."""
    mq = _mq()
    prs = [_pr(f"2026-08-14T10:0{i}:00Z") for i in range(20)]
    run = _combined_run(None, prs)              # protection call returns "" (unreachable/absent)
    assert mq.advise({"repo": "acme/widget"}, run) is None


def test_advise_negative_control_protected_but_quiet():
    """Protected AND healthy: a real gate, but merges are infrequent -- not the thrash shape."""
    mq = _mq()
    prs = [_pr("2026-08-14T10:00:00Z"), _pr("2026-08-01T10:00:00Z")]
    run = _combined_run(_PROTECTED, prs)
    assert mq.advise({"repo": "acme/widget"}, run) is None


def test_advise_appends_growth_evidence_when_measurable():
    mq = _mq()
    prs = [_pr(f"2026-08-14T10:0{i}:00Z") for i in range(6)]
    newest = [_run_entry("2026-08-14T10:00:00Z", "2026-08-14T10:12:00Z") for _ in range(3)]
    oldest = [_run_entry("2026-08-10T10:00:00Z", "2026-08-10T10:03:00Z") for _ in range(3)]
    run = _combined_run(_PROTECTED, prs, {"workflow_runs": newest + oldest})
    msg = mq.advise({"repo": "acme/widget"}, run)
    assert msg is not None and "runtime" in msg


def test_advise_names_the_requirements_in_the_message():
    """The recommendation must name what enabling a merge queue actually needs -- admin access and
    a paid plan for private repos -- so it never reads as something the loop could just turn on."""
    mq = _mq()
    prs = [_pr(f"2026-08-14T10:0{i}:00Z") for i in range(6)]
    run = _combined_run(_PROTECTED, prs)
    msg = mq.advise({"repo": "acme/widget"}, run)
    assert "admin" in msg and ("plan" in msg or "Team" in msg or "Enterprise" in msg)


def test_advise_never_mutates_anything():
    """No call this module makes may be anything other than a `gh api` READ -- no `gh pr merge`,
    no `gh api -X PATCH/POST/PUT/DELETE`, no `administration` scope reference anywhere."""
    mq = _mq()
    calls = []

    def run(args):
        calls.append(args)
        path = args[2]
        assert path.startswith("repos/acme/widget/")   # every call correctly scoped to the target repo
        if "protection" in path:
            return json.dumps(_PROTECTED)
        if path.startswith("repos/acme/widget/pulls?"):
            return json.dumps([_pr(f"2026-08-14T10:0{i}:00Z") for i in range(6)])
        return ""
    result = mq.advise({"repo": "acme/widget"}, run)
    assert result is not None and calls               # the burst path actually fired -- calls happened
    for args in calls:
        assert args[:2] == ["gh", "api"]
        assert not any(a in ("-X", "--method") for a in args)
        assert "administration" not in " ".join(args)

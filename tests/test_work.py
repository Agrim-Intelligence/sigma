"""Per-goal worktree + the clean-AND-safe merge gate. Every git/gh call is injected, so these are
hermetic: no repo, no network, no `gh`. What they actually pin down is the gate's REFUSALS — the
cheap way to get this wrong is to merge on a lazy UNKNOWN or on evidence from yesterday's run."""
import hashlib, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, time, types

import pytest
from journal_events import journal_events

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "agrim-loop" / "scripts"


@pytest.fixture(autouse=True)
def _legacy_work_host_unless_test_sets_codex_thread(monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


work = _load("work")
state = _load("state")
actionlog = _load("actionlog")
ledger = _load("ledger")            # #540: _claim() below builds a writer instance the real way

ON = {"work": {"enabled": True}}
ON_GITHUB = {**ON, "discovery": {"source": "github"}}
ACTIONLOG = {"action_log": {"enabled": True}}
NOSLEEP = lambda _: None                                          # noqa: E731 - one-liner test stub


def _runner(handlers):
    """First substring match wins. A response may be a string, a callable, or an Exception to
    raise. Anything unmatched returns "" — the honest default for most git commands."""
    calls = []

    def run(cwd, argv):
        line = " ".join(str(a) for a in argv)
        calls.append(line)
        for token, resp in handlers:
            if token in line:
                if isinstance(resp, Exception):
                    raise resp
                return resp(line) if callable(resp) else resp
        if "rev-parse HEAD" in line:
            return HEAD_SHA          # gate()'s stale-head check (#197); override via a handler
        if "remote get-url" in line:
            return REMOTE_URL        # what feature_sync.repo_slug falls back to (#1577)
        return ""

    run.calls = calls
    return run


#: The sha both sides report by default, so a test that does not care about the stale-head guard
#: (#197) sees a matching head. A test that DOES care overrides one side.
HEAD_SHA = "0" * 40

#: The checkout's own remote URL, answering `git remote get-url <remote>` by default. #1577 moved
#: `_sibling_gate` off `gh repo view` onto `feature_sync.repo_slug`, whose fallback is this local
#: read -- so it is a default here for the same reason `HEAD_SHA` is: a test that does not care
#: about which repo this is should not have to say.
REMOTE_URL = "git@github.com:acme/app.git"


def _view(mergeable="MERGEABLE", status="CLEAN", checks=(("ci", "SUCCESS"),), head=HEAD_SHA):
    return json.dumps({"mergeable": mergeable, "mergeStateStatus": status, "headRefOid": head,
                       "statusCheckRollup": [{"name": n, "conclusion": c} for n, c in checks]})


def _merged_pr(sha="a" * 40):
    return json.dumps({"number": 7, "node_id": "PR_7", "created_at": "2026-01-01T00:00:00Z",
                       "merged_at": "2026-01-02T00:00:00Z", "merge_commit_sha": sha,
                       "head": {"ref": "sdlc/2577"},
                       "base": {"ref": "main", "repo": {"node_id": "R_1"}}})


def test_normalise_ci_rollup_is_bounded_and_fail_closed():
    checks = [{"name": f"check-{i}", "conclusion": "SUCCESS"} for i in range(51)]
    checks[1] = {"context": "context", "state": "PENDING"}
    checks[2] = None
    result = work.normalise_ci_rollup({"headRefOid": HEAD_SHA, "statusCheckRollup": checks})
    assert result["head_sha"] == HEAD_SHA
    assert result["checks_total"] == 51 and result["checks_truncated"] is True
    assert len(result["checks"]) == 50
    assert result["checks"][1] == {"name": "context", "conclusion": "pending"}
    assert result["checks"][2]["conclusion"] == "fail"


def test_ci_observation_key_changes_when_the_observed_gate_payload_changes():
    """A retry on one commit may observe a real CI transition, not a conflicting duplicate."""
    first = {"head_sha": "a" * 40, "checks_total": 1, "checks_truncated": False,
             "checks": [{"name": "ci", "conclusion": "pending"}]}
    later = {**first, "checks": [{"name": "ci", "conclusion": "pass"}]}
    assert work.ci_observation_key("2577", 7, first, "warn") != work.ci_observation_key("2577", 7, later, "pass")
    assert work.ci_observation_key("2577", 7, later, "pass") == work.ci_observation_key("2577", 7, later, "pass")


def test_red_required_check_dispatches_one_bounded_implement_cycle_with_its_log(tmp_path):
    """Control: a planted lint red produces a repair handoff before the old failed path."""
    d = _sdlc(tmp_path); goal = _started(d)
    rec = work._record(d, goal); rec["pr"] = 7; work._save(d, goal, rec)
    data = {"headRefOid": HEAD_SHA, "statusCheckRollup": [
        {"name": "lint", "conclusion": "FAILURE", "detailsUrl": "https://github.com/acme/app/actions/runs/42/jobs/9"}]}
    run = _runner([("gh run view 42 --log-failed", "flake8: E999 planted lint failure")])
    out = work.ci_repair(d, ON, goal, data, run=run)
    assert out.startswith("REPAIR: implement CI fix cycle 1/3")
    assert "lint" in out and "E999 planted lint failure" in out
    assert work._record(d, goal)["ci_fix_cycles"] == 1


def test_infrastructure_flake_reruns_once_and_counts_against_the_same_cap(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    rec = work._record(d, goal); rec["pr"] = 7; work._save(d, goal, rec)
    data = {"headRefOid": HEAD_SHA, "statusCheckRollup": [
        {"name": "CI", "conclusion": "TIMED_OUT", "detailsUrl": "https://github.com/acme/app/actions/runs/42/jobs/9"}]}
    run = _runner([("gh run rerun 42", "")])
    out = work.ci_repair(d, ON, goal, data, run=run)
    assert out.startswith("RERUN: infrastructure flake CI fix cycle 1/3")
    assert "gh run rerun 42" in run.calls
    assert work._record(d, goal)["ci_fix_cycles"] == 1


def test_repeated_infrastructure_flakes_spend_the_cap_on_the_same_head(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    rec = work._record(d, goal); rec["pr"] = 7; work._save(d, goal, rec)
    data = {"headRefOid": HEAD_SHA, "statusCheckRollup": [
        {"name": "CI", "conclusion": "TIMED_OUT", "detailsUrl": "https://github.com/acme/app/actions/runs/42/jobs/9"}]}
    run = _runner([("gh run rerun 42", "")])
    assert "cycle 1/2" in work.ci_repair(d, {"work": {"enabled": True, "max_review_cycles": 2}}, goal, data, run=run)
    assert "cycle 2/2" in work.ci_repair(d, {"work": {"enabled": True, "max_review_cycles": 2}}, goal, data, run=run)
    assert work.ci_repair(d, {"work": {"enabled": True, "max_review_cycles": 2}}, goal, data, run=run) == (
        "PARK: CI fix cycles exhausted after 2 cycles — failing: CI")


def test_ci_fix_and_review_cycles_share_one_anti_thrash_cap(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    rec = work._record(d, goal); rec.update({"pr": 7, "review_cycles": 2}); work._save(d, goal, rec)
    data = {"headRefOid": HEAD_SHA, "statusCheckRollup": [{"name": "lint", "conclusion": "FAILURE"}]}
    assert work.ci_repair(d, {"work": {"enabled": True, "max_review_cycles": 2}}, goal, data, run=_runner([])) == (
        "PARK: CI fix cycles exhausted after 2 cycles — failing: lint")


def test_ci_repair_fails_closed_when_the_log_cannot_be_read(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    rec = work._record(d, goal); rec["pr"] = 7; work._save(d, goal, rec)
    data = {"headRefOid": HEAD_SHA, "statusCheckRollup": [
        {"name": "lint", "conclusion": "FAILURE", "detailsUrl": "https://github.com/acme/app/actions/runs/42/jobs/9"}]}
    out = work.ci_repair(d, ON, goal, data, run=_runner([("gh run view", RuntimeError("network"))]))
    assert out.startswith("PARK:") and "log" in out
    assert "ci_fix_cycles" not in work._record(d, goal)


def test_ci_repair_cap_falls_back_to_the_existing_failed_outcome_with_the_check_named(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    rec = work._record(d, goal); rec.update({"pr": 7, "ci_fix_cycles": 1}); work._save(d, goal, rec)
    data = {"headRefOid": "a" * 40, "statusCheckRollup": [{"name": "lint", "conclusion": "FAILURE"}]}
    out = work.ci_repair(d, {"work": {"enabled": True, "max_review_cycles": 1}}, goal, data, run=_runner([]))
    assert out == "PARK: CI fix cycles exhausted after 1 cycles — failing: lint"


def test_merge_routes_a_planted_required_check_failure_to_the_bounded_repair_dispatch(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    run = _runner([
        ("gh pr view 7 --json isCrossRepository", "{}"),
        ("gh repo view --json viewerPermission", "ADMIN"),
        ("gh pr view 7 --json mergeable", _view(status="BLOCKED", checks=(("lint", "FAILURE"),))),
    ])
    # The base gate response deliberately lacks a details URL; its fail-closed repair fallback
    # preserves the named failed outcome instead of issuing an agent a blind instruction.
    out = work.merge(d, ON, goal, run=run, sleep=NOSLEEP)
    assert out == "PARK: failing required check lint has no readable Actions log"


def test_review_paths_refuses_tampered_manifest_and_emits_safe_assignments(tmp_path):
    root = tmp_path / ".sdlc" / "state"
    generation = root / "review-generations" / "gen"
    generation.mkdir(parents=True)
    brief = generation / "brief.md"; brief.write_text("brief")
    digest = __import__("hashlib").sha256(b"brief").hexdigest()
    manifest = root / "review-manifests" / "2577.json"; manifest.parent.mkdir()
    manifest.write_text(json.dumps({"generation_id": "gen", "goal": "2577",
                                    "brief": "review-generations/gen/brief.md", "brief_sha256": digest}))
    output = work.review_paths(tmp_path / ".sdlc", "2577", manifest)
    assert "REVIEW_BRIEF=" in output and "REVIEW_EVIDENCE=" in output
    brief.write_text("changed")
    with pytest.raises(ValueError, match="digest"):
        work.review_paths(tmp_path / ".sdlc", "2577", manifest)


def test_review_paths_accepts_the_documented_relative_sdlc_gesture(tmp_path, monkeypatch):
    """The SKILL.md PR-review command passes `.sdlc`, not an absolute path."""
    root = tmp_path / ".sdlc" / "state"
    generation = root / "review-generations" / "gen"
    generation.mkdir(parents=True)
    (generation / "brief.md").write_text("brief")
    digest = __import__("hashlib").sha256(b"brief").hexdigest()
    manifest = root / "review-manifests" / "2577.json"; manifest.parent.mkdir()
    manifest.write_text(json.dumps({"generation_id": "gen", "goal": "2577",
                                    "brief": "review-generations/gen/brief.md", "brief_sha256": digest}))

    monkeypatch.chdir(tmp_path)
    output = work.review_paths(pathlib.Path(".sdlc"), "2577",
                               pathlib.Path(".sdlc/state/review-manifests/2577.json"))
    assert "REVIEW_BRIEF=" in output and "REVIEW_EVIDENCE=" in output


def test_review_reconciliation_help_names_the_actual_recovery_gesture(capsys):
    assert work.main(["work.py", "reconcile-review-post", "--help"]) == 0
    assert "reconcile-review-post <sdlc_dir> <goal> --evidence <path>" in capsys.readouterr().out


def test_review_evidence_requires_result_for_the_selected_generation(tmp_path):
    root = tmp_path / ".sdlc" / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    (gen / "brief.md").write_text("brief")
    digest = __import__("hashlib").sha256(b"brief").hexdigest()
    manifest = root / "m.json"; manifest.write_text(json.dumps({"generation_id": "gen", "goal": "2577", "brief": "review-generations/gen/brief.md", "brief_sha256": digest}))
    result = root / "review-results" / "gen.json"; result.parent.mkdir()
    result.write_text(json.dumps({"generation_id": "other", "brief_sha256": digest, "verdict": "approve"}))
    with pytest.raises(ValueError, match="generation"):
        work.review_evidence(tmp_path / ".sdlc", "2577", manifest, result)


def test_review_evidence_refuses_a_result_for_a_different_pr_or_head(tmp_path):
    """A valid generation cannot be replayed onto another PR revision."""
    root = tmp_path / ".sdlc" / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    (gen / "brief.md").write_text("brief")
    digest = __import__("hashlib").sha256(b"brief").hexdigest()
    manifest = root / "m.json"; manifest.write_text(json.dumps({
        "generation_id": "gen", "goal": "2577", "pr": 7, "head_sha": "a" * 40,
        "brief": "review-generations/gen/brief.md", "brief_sha256": digest,
    }))
    result = root / "review-results" / "gen.json"; result.parent.mkdir()
    result.write_text(json.dumps({"generation_id": "gen", "brief_sha256": digest,
                                  "pr": 8, "head_sha": "b" * 40, "verdict": "approve"}))
    with pytest.raises(ValueError, match="PR or head"):
        work.review_evidence(tmp_path / ".sdlc", "2577", manifest, result)


def test_review_evidence_is_write_once_for_one_generation(tmp_path):
    """Write-once is a property of every generation, so this uses a non-PR one: a PR generation
    additionally pins a worktree revision, which the documented-gesture tests below cover."""
    root = tmp_path / ".sdlc" / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    brief = gen / "brief.md"; brief.write_text("brief")
    digest = __import__("hashlib").sha256(b"brief").hexdigest()
    manifest = root / "manifest.json"; manifest.write_text(json.dumps({
        "generation_id": "gen", "goal": "2577",
        "brief": "review-generations/gen/brief.md", "brief_sha256": digest,
    }))
    result = root / "review-results" / "gen.json"; result.parent.mkdir()
    result.write_text(json.dumps({"generation_id": "gen", "brief_sha256": digest,
                                  "verdict": "approve"}))
    work.review_evidence(tmp_path / ".sdlc", "2577", manifest, result)
    result.write_text(json.dumps({"generation_id": "gen", "brief_sha256": digest,
                                  "verdict": "block"}))
    with pytest.raises(ValueError, match="immutable"):
        work.review_evidence(tmp_path / ".sdlc", "2577", manifest, result)


def test_pr_receipt_facts_require_complete_canonical_github_response():
    raw = {"number": 7, "node_id": "PR_7", "created_at": "2026-01-01T00:00:00Z",
           "head": {"ref": "sdlc/2577"}, "base": {"ref": "main", "repo": {"node_id": "R_1"}}}
    facts = work._receipt_parent_facts(raw, "2577", "goal", "2577", "work.pr")
    assert facts["pr_number"] == 7 and facts["canonical_repository_id"] == "R_1"
    with pytest.raises(ValueError, match="canonical"):
        work._receipt_parent_facts({"number": 7}, "2577", "goal", "2577", "work.pr")


def test_run_resolved_review_writes_generation_bound_result(tmp_path):
    root = tmp_path / ".sdlc" / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    brief = gen / "brief.md"; brief.write_text("brief")
    digest = __import__("hashlib").sha256(b"brief").hexdigest()
    manifest = root / "manifest.json"; manifest.write_text(json.dumps({"generation_id": "gen", "goal": "2577", "pr": 7, "head_sha": "a" * 40, "brief": "review-generations/gen/brief.md", "brief_sha256": digest}))
    # The inline route's verdict is passed explicitly and the route is re-resolved live (round 3 B2):
    # a verdict written INTO resolution.json is the maker-writes-its-own-verdict path, so refused.
    (tmp_path / ".sdlc" / "config.json").write_text(json.dumps({"review": {"independent": False}}))
    resolution = gen / "resolution.json"
    resolution.write_text(json.dumps(_load("reviewer").resolve(tmp_path / ".sdlc")))
    result = work.run_resolved_review(tmp_path / ".sdlc", "2577", manifest, resolution,
                                      verdict="approve", reason="no-op")
    assert result["generation_id"] == "gen" and result["verdict"] == "approve"
    assert pathlib.Path(result["path"]).exists()


def test_run_resolved_process_review_captures_a_fresh_structured_verdict(tmp_path, monkeypatch):
    root = tmp_path / ".sdlc" / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    brief = gen / "brief.md"; brief.write_text("brief")
    digest = __import__("hashlib").sha256(b"brief").hexdigest()
    worktree = tmp_path / "worktree"; worktree.mkdir()
    manifest = root / "manifest.json"; manifest.write_text(json.dumps({
        "generation_id": "gen", "goal": "2577", "pr": 7, "head_sha": "a" * 40,
        "brief": "review-generations/gen/brief.md", "brief_sha256": digest,
    }))
    resolution = gen / "resolution.json"; resolution.write_text(json.dumps({
        "mechanism": "process", "host": "codex", "command": ["codex", "exec"], "reason": "resolved",
    }))
    fake = types.SimpleNamespace(
        resolve=lambda _sdlc: {"mechanism": "process", "host": "codex", "command": ["codex", "exec"]},
        run_review=lambda *_args: (print("Verdict: **APPROVE**\nReason: clean diff") or 0),
    )
    original = work._load
    monkeypatch.setattr(work, "_load", lambda name: fake if name == "reviewer" else original(name))
    work._save(tmp_path / ".sdlc", "2577", {"worktree": str(worktree), "pr": 7})
    result = work.run_resolved_review(tmp_path / ".sdlc", "2577", manifest, resolution)
    assert result["verdict"] == "approve" and result["pr"] == 7 and result["head_sha"] == "a" * 40


def test_documented_subagent_handoff_writes_generation_bound_review_result(tmp_path, monkeypatch, capsys):
    """A resolver-selected Claude subagent reaches the same evidence boundary as Codex."""
    root = tmp_path / ".sdlc" / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    brief = gen / "brief.md"; brief.write_text("brief")
    digest = __import__("hashlib").sha256(b"brief").hexdigest()
    manifest = root / "manifest.json"; manifest.write_text(json.dumps({
        "generation_id": "gen", "goal": "2577", "pr": 7, "head_sha": "a" * 40,
        "brief": "review-generations/gen/brief.md", "brief_sha256": digest,
    }))
    resolution = gen / "resolution.json"; resolution.write_text(json.dumps({
        "mechanism": "subagent", "host": "claude", "reason": "host dispatches subagents",
    }))
    fake = types.SimpleNamespace(resolve=lambda _sdlc: {
        "mechanism": "subagent", "host": "claude", "reason": "host dispatches subagents",
    })
    original = work._load
    monkeypatch.setattr(work, "_load", lambda name: fake if name == "reviewer" else original(name))
    assert work.main(["work.py", "record-subagent-review", str(tmp_path / ".sdlc"), "2577",
                      "--manifest", str(manifest), "--resolution", str(resolution),
                      "--verdict", "block", "--reason", "found a defect"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "block" and result["generation_id"] == "gen"
    assert pathlib.Path(result["path"]).exists()


def test_review_marker_recovery_finds_a_comment_on_the_second_rest_page():
    marker = "<!-- sigma-review-evidence:abc -->"
    first = [{"id": number, "body": "older comment"} for number in range(work.REVIEW_COMMENT_PAGE_SIZE)]
    calls = []

    def run(_cwd, argv):
        calls.append(argv[-1])
        return json.dumps(first if argv[-1].endswith("page=1") else [{"id": 101, "body": marker}])

    found = work._find_evidence_marker(run, ".", 7, "abc")
    assert found["id"] == 101
    assert any("page=2" in endpoint for endpoint in calls)


def _pr_state(state="OPEN", auto_merge=False):
    """`gh pr view --json state,autoMergeRequest` — the single cheap read `done_refusal` makes.
    A DIFFERENT field set from `_view()` (which answers gate()'s own mergeable/mergeStateStatus/
    statusCheckRollup/headRefOid question) -- kept as its own helper so the two never get
    conflated, and so the substring `_runner` handlers key on ("state,autoMergeRequest") can never
    collide with `_view()`'s own call shape."""
    return json.dumps({"state": state,
                       "autoMergeRequest": ({"mergeMethod": "SQUASH"} if auto_merge else None)})


# Ordered BEFORE any ("pr view", ...) handler: `gh pr view --json isCrossRepository` would otherwise
# be swallowed by the gate's handler, since both are `gh pr view`.
def _rights(cross=False, perm="ADMIN"):
    return [("isCrossRepository", json.dumps({"isCrossRepository": cross})),
            ("viewerPermission", perm),
            ("nameWithOwner", "acme/app")]


def _protected(checks=("ci",), reviews=0):
    return [("branches/main/protection", json.dumps({
        "required_status_checks": {"contexts": list(checks)},
        "required_pull_request_reviews": {"required_approving_review_count": reviews}}))]


UNPROTECTED = [("branches/main/protection", RuntimeError("HTTP 404: Branch not protected"))]


def _auto_merge_allowed(allowed=True):
    """`gh api repos/{owner}/{repo} --jq .allow_auto_merge` (#1212) -- the ONE extra read merge()
    makes, and only when it is actually deciding whether to arm a still-pending PR. A plain
    substring handler, spread into `_runner`'s call list the same way `_protected`/`UNPROTECTED`
    already are."""
    return [("allow_auto_merge", "true" if allowed else "false")]


def _default_branch(name="main"):
    """`gh api repos/{owner}/{repo} --jq .default_branch` (#1649) -- the one fact that decides
    whether GitHub's own `Closes #N` handling can fire at all, asked by `_pr_body` when it composes
    the header. Same plain-substring shape as `_auto_merge_allowed` above, and deliberately a
    SEPARATE handler from it: both are `gh api repos/{owner}/{repo}`, told apart only by the
    `--jq` field, so keying on the field name is what keeps one from swallowing the other.

    #2615: `merge()`'s own close clause no longer reads this at all (see `_issue_state` below) --
    this helper now answers `_pr_body` alone."""
    return [("default_branch", name)]


def _issue_state(state="open"):
    """`gh api repos/{owner}/{repo}/issues/{ref} --jq .state` (#2615) -- the ONE call the close
    clause makes, post-merge, to decide whether GitHub's own keyword already closed the issue.
    Keyed on the literal `--jq .state` tail, which ONLY this call carries: neither the PATCH
    close (`-f state=closed`, no `--jq`) nor the audit-trail comment (`/comments`) can ever match
    it, so this handler can never silently answer one of those two writes instead of the read it
    is meant for -- the exact vacuity #2615's plan-review round 1 found in the OLD `_view()`,
    which answered any `gh pr view` call the same way regardless of which `--json` fields a caller
    actually asked for. A mutant that drops `--jq .state` (or misspells the issue path) falls
    through to `_runner`'s unmatched-call default of `""`, which this module's own logic treats as
    neither `"open"` nor `"closed"` -- i.e. unconfirmed -- so a test asserting the CONFIRMED-open
    or CONFIRMED-closed wording, or asserting NO close call at all, goes red exactly when the
    production call shape drifts from what this fake expects."""
    return [("--jq .state", state)]


def _issue_state_raced_closed(state="open"):
    """Plan-review round 2, finding 1's sequenced-race control. Answers the same `--jq .state` call
    `_issue_state` does, but the handler itself mutates `raced["closed"]` to `True` the instant it
    answers -- modelling GitHub's own keyword processing (or a person) closing the issue in the
    exact window between this read and the PATCH two lines below it in production code, which has
    no way to see it happen and no third read to catch it. The PATCH handler is deliberately NOT
    made aware of `raced["closed"]`: `gh api -X PATCH .../state=closed` succeeds unconditionally
    whether the issue was already closed or not (a real, externally-unobservable idempotency), and
    this fake does not pretend to see through that either -- which is exactly why the production
    code must never claim it was the one that closed the issue."""
    raced = {"closed": False}

    def answer(_line):
        raced["closed"] = True
        return state

    return [("--jq .state", answer)], raced


def _issue_calls(run, ref):
    """Every gh call this runner saw that targets issue `ref` -- the close, the note, or anything
    else. The close-side tests assert on this rather than on one exact argv, so a future change of
    call shape still has to keep the same answer to "was this issue written to at all?"."""
    return [c for c in run.calls if f"/issues/{ref}" in c]


def _sdlc(tmp_path, config=None):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config or ON))
    state.start_run(str(d))
    return str(d)


def _started(sdlc_dir, goal="0001-x.md", pr="7", base="main"):
    """`base` (#1649) is the branching model's whole variable: a goal declaring a unit is cut from
    `feature/<unit>`, not from the default branch, and that is what makes GitHub's closing keyword
    inert. Defaults to `main` so every test predating this reads exactly as it did."""
    wt = pathlib.Path(sdlc_dir).parent / ".sdlc" / "work" / "0001-x"
    wt.mkdir(parents=True, exist_ok=True)
    work._save(sdlc_dir, goal, {"worktree": str(wt), "branch": "sdlc/0001-x", "base": base,
                                "remote": "origin", "pr": pr})
    return goal


def _evidence(sdlc_dir, goal="0001-x.md", exit_code=0, age=0):
    ev = state.evidence_path(sdlc_dir, goal)
    ev.parent.mkdir(parents=True, exist_ok=True)
    at = state.load_cursor(sdlc_dir)["run_started_at"] - age
    ev.write_text(json.dumps({"command": "pytest", "exit": exit_code, "at": at, "tail": []}))


# --- root: the resolution that makes a green verify mean something -------------------------------

def test_root_falls_back_to_project_root_when_the_feature_is_off(tmp_path):
    d = _sdlc(tmp_path)
    assert work.root(d, "0001-x.md") == str(tmp_path.resolve())


def test_root_is_the_worktree_once_the_goal_has_one(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    assert work.root(d, goal).endswith("work/0001-x")


def test_root_falls_back_when_the_worktree_is_gone(tmp_path):
    """A removed worktree must not wedge verify — it degrades to the project root."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    pathlib.Path(work._record(d, goal)["worktree"]).rmdir()
    assert work.root(d, goal) == str(tmp_path.resolve())


def test_root_recovers_the_worktree_when_the_record_is_lost(tmp_path):
    """#1984's open checkbox. Six goals had hand-made worktrees and therefore no record, so `root()`
    handed the proving command the SHARED project root — another session's branch, 77 commits
    behind — while each goal's own tree sat at the conventional path the whole time. #1985 made that
    fallback loud; this stops taking it when there is nothing to fall back FROM.

    `start()` has recovered from a lost record since it was written ("branch outlived its record").
    This is that recovery on the read side, where a green has to mean something."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    work.record_path(d, goal).unlink()
    assert work.root(d, goal).endswith("work/0001-x")


def test_root_still_falls_back_when_no_worktree_exists_on_disk(tmp_path):
    """The recovery must not INVENT a tree. Work on, nothing on disk, and the project root is still
    the honest answer — the contract the two fallback tests above exist to protect."""
    d = _sdlc(tmp_path)
    assert work.root(d, "0002-y.md") == str(tmp_path.resolve())


def test_root_never_builds_a_worktree_path_from_an_unsafe_goal(tmp_path):
    """The recovered path embeds `stem(goal)` as a real path component — precisely what `start()`
    and `record_path()` gate with `unsafe_goal_reason`. Without the same check here a traversing
    goal aims the proving command at a directory outside `worktree_dir` entirely."""
    d = _sdlc(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    assert work.root(d, "../../elsewhere") == str(tmp_path.resolve())


def test_verify_runs_in_the_worktree_not_the_main_checkout(tmp_path):
    """The whole point of the root fix: the proving command must see the goal's own tree."""
    loop = _load("loop")
    d = _sdlc(tmp_path, {**ON, "verify": {"command": "pwd > where.txt"}})
    goal = _started(d)
    assert loop.verify_goal(d, goal) == 0
    wt = pathlib.Path(work._record(d, goal)["worktree"])
    assert (wt / "where.txt").read_text().strip() == str(wt)
    assert not (tmp_path / "where.txt").exists()


# --- start: cutting fresh IS the goal-start rebase -----------------------------------------------

def test_start_cuts_from_the_remote_base_and_records_it(tmp_path):
    d = _sdlc(tmp_path)
    run = _runner([("rev-parse", "main")])
    out = work.start(d, ON, "0001-x.md", run=run)
    assert "sdlc/0001-x" in out and "origin/main" in out
    assert "git fetch origin main" in run.calls
    assert any("worktree add -b sdlc/0001-x" in c and "origin/main" in c for c in run.calls)
    rec = work._record(d, "0001-x.md")
    assert rec["base"] == "main" and pathlib.Path(rec["worktree"]).is_absolute()


# --- #486/PR #487 independent review: record_path() and start() had the identical unguarded
# goal-into-path pattern actionlog.py's log_path() was originally fixed for. start()'s case is
# real: an unsafe goal would make `git worktree add` create a checkout OUTSIDE worktree_dir
# entirely (a real git operation, not just a JSON file write).


def test_record_path_rejects_a_traversal_goal(tmp_path):
    d = _sdlc(tmp_path)
    try:
        work.record_path(d, "../../../evil-goal")
        assert False, "expected record_path to refuse a traversal goal"
    except ValueError as exc:
        assert "unsafe goal" in str(exc)
    # legitimate goals still resolve, unaffected
    assert work.record_path(d, "0001-x.md").name == "0001-x.json"


def test_start_refuses_a_traversal_goal_before_any_git_call(tmp_path):
    """Checked FIRST, before `git fetch`/`git worktree add` — a traversal goal must never reach
    the point of computing a real worktree path, let alone running git against it."""
    d = _sdlc(tmp_path)
    run = _runner([("rev-parse", "main")])
    try:
        work.start(d, ON, "../../../evil-goal", run=run)
        assert False, "expected start() to refuse a traversal goal"
    except ValueError as exc:
        assert "unsafe goal" in str(exc)
    assert run.calls == []                      # no git call was ever attempted


def test_start_is_idempotent_so_a_supervisor_relaunch_reattaches(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([])
    assert "already started" in work.start(d, ON, goal, run=run)
    # #2009: a resume is no longer free, and that is the point. It cuts no second worktree -- what
    # it now does is ASK whether the one it is reattaching to has fallen behind its base, because
    # every reading phase downstream of this return trusts that tree (#1617 measured 78 commits).
    # Two local git calls against one branch, pinned exactly rather than as "no calls", so the cost
    # stays visible to the next reader instead of being deleted along with the old assertion.
    assert run.calls == ["git fetch origin main",
                         "git rev-list --count HEAD..origin/main"]


def test_start_reattaches_to_a_branch_that_outlived_its_record(tmp_path):
    d = _sdlc(tmp_path)
    run = _runner([("rev-parse", "main"), ("worktree add -b", RuntimeError("already exists"))])
    work.start(d, ON, "0001-x.md", run=run)
    assert any(c.startswith("git worktree add ") and "-b" not in c for c in run.calls)

    # Same fallback, but with the ledger ON and no rival claim for the goal: the guard added by
    # #388 must narrow an unsafe resume, not newly block a legitimate one when there is nothing to
    # refuse — ledger-enabled is not, by itself, a reason to stop reattaching.
    d2 = _sdlc(tmp_path / "ledger-on", LEDGER_ON)
    run2 = _runner([("rev-parse", "main"), ("worktree add -b", RuntimeError("already exists"))])
    work.start(d2, LEDGER_ON, "0001-x.md", run=run2)
    assert any(c.startswith("git worktree add ") and "-b" not in c for c in run2.calls)


# --- start's resume guard: a local worktree existing is not proof it's safe to reuse (F10.5/#374) -

LEDGER_ON = {"work": {"enabled": True},
             "ledger": {"enabled": True, "actor": "rae", "lease": {"ttl_hours": 0}}}


def _claim(sdlc_dir, actor, goal, pid, seq=1):
    """A claim written by a process ON THIS HOST — every caller below means a local sibling (live,
    dead, or my own current process). #540 put a per-host component in the writer instance, so this
    fixture has to carry this host's own token; without it the claim reads as ANOTHER machine's,
    which is a different case with its own test in test_ledger.py. The pre-#337 legacy shape is
    written inline by the one test that actually exercises it, never through here."""
    instance = f"{ledger._host_token()}.{pid}"
    ent = pathlib.Path(sdlc_dir) / "ledger" / "entries"
    ent.mkdir(parents=True, exist_ok=True)
    (ent / f"{actor}-{instance}.jsonl").write_text(json.dumps(
        {"id": f"{actor}:{instance}:{seq}", "ts": "2026-08-05T09:00:00Z", "actor": actor,
         "kind": "claimed", "goal": goal}) + "\n")


def test_start_refuses_to_resume_when_a_live_sibling_process_holds_the_claim(tmp_path, monkeypatch):
    """A local worktree existing on disk only proves THIS MACHINE started it once — not that the
    process which claimed it is gone. Resuming it when a DIFFERENT, still-live process of the same
    actor holds the ledger claim would silently corrupt that session's in-flight work — exactly the
    race a routine firing without an explicit target hit live."""
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    real_pid = os.getpid()                             # this test process — verifiably alive
    _claim(d, "rae", goal, real_pid)
    monkeypatch.setattr(os, "getpid", lambda: real_pid + 1)   # simulate a DIFFERENT process of "rae"
    run = _runner([])
    out = work.start(d, LEDGER_ON, goal, run=run)
    assert out.startswith("REFUSED")
    assert run.calls == []                             # never touched git — the worktree stays as-is


def test_start_reattach_refuses_to_resume_when_a_live_sibling_process_holds_the_claim(tmp_path, monkeypatch):
    """The narrower sibling of the guard above (#388): the local STATE RECORD can go missing (a
    partial `.sdlc/state` cleanup, a corrupted/truncated JSON file, a machine migration) while the
    branch and worktree it described still exist on disk. `start()` notices via a failed `-b` add
    and falls back to reattaching directly (`test_start_reattaches_to_a_branch_that_outlived_its_
    record` above) — that fallback must refuse exactly like the `already started` path does, not
    skip the check just because it got here without ever finding a record to resume from."""
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = "0001-x.md"                          # deliberately NOT `_started()` — no local record at all
    real_pid = os.getpid()
    _claim(d, "rae", goal, real_pid)
    monkeypatch.setattr(os, "getpid", lambda: real_pid + 1)   # simulate a DIFFERENT process of "rae"
    run = _runner([("rev-parse", "main"), ("worktree add -b", RuntimeError("already exists"))])
    out = work.start(d, LEDGER_ON, goal, run=run)
    assert out.startswith("REFUSED")
    # the failed `-b` attempt is expected (that's what triggers the fallback) — what must NEVER
    # happen is the second, no `-b` reattach that the fallback runs once the guard clears it.
    assert not any(c.startswith("git worktree add ") and "-b" not in c for c in run.calls)


def test_start_still_resumes_when_the_claim_is_my_own_current_process(tmp_path, monkeypatch):
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    my_pid = os.getpid()
    _claim(d, "rae", goal, my_pid)
    run = _runner([])
    assert "already started" in work.start(d, LEDGER_ON, goal, run=run)


def test_start_still_resumes_a_dead_siblings_claim(tmp_path, monkeypatch):
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    dead_pid = 2**30                                    # not a real pid on any sane system
    _claim(d, "rae", goal, dead_pid)
    monkeypatch.setattr(os, "getpid", lambda: dead_pid + 1)
    run = _runner([])
    assert "already started" in work.start(d, LEDGER_ON, goal, run=run)


def test_start_refuses_to_resume_when_a_dead_pickers_claim_still_has_a_live_worker_marker(tmp_path, monkeypatch):
    """#1197: a confirmed-dead WRITER pid on the claim is not the same question as a dead WORKER —
    that writer pid is always the short-lived picker CLI invocation that wrote the claim, dead
    within moments of every acquisition regardless of whether the goal itself is still being
    actively worked by a long-running subagent. A genuine worker registered for this goal
    (`agent_start`, exactly as SKILL.md step 3a's `agent-start --pid $PPID` does after a real
    dispatch, with an ordinary config carrying no `agent_watch` key) must still block the resume
    even though the claim's own writer pid reads definitively dead — the same worktree-corruption
    race #374/#388 already close one level up, reopened here through the identity rather than the
    logic if this guard didn't also corroborate against the real worker."""
    loop = _load("loop")
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    dead_picker_pid = 2**30                             # not a real pid on any sane system
    _claim(d, "rae", goal, dead_picker_pid)
    loop.agent_start(d, goal, os.getpid(), {})           # the real, still-alive worker for this goal
    monkeypatch.setattr(os, "getpid", lambda: dead_picker_pid + 1)   # a second session's own pid
    run = _runner([])
    out = work.start(d, LEDGER_ON, goal, run=run)
    assert out.startswith("REFUSED"), out
    assert run.calls == []                              # never touched git — the worktree stays as-is


def test_start_resumes_its_own_just_registered_worker_marker(tmp_path, monkeypatch):
    """#2394 / onshot-ai#3649: direct reproduction. SKILL.md step 3a runs, in order,
    `loop.py agent-start .sdlc "$goal" --pid $PPID` then `work.py start .sdlc "$goal"
    --session-pid "$PPID"` — the SAME agent's own stable pid registered a moment earlier, read
    back by a `work.py` invocation that is itself a distinct child process (a different
    `os.getpid()` of its own). The live-worker tie-breaker this exercises is the identical #1197
    path the test directly above pins (a dead picker pid on the claim, corroborated against
    `_goal_has_registered_worker`) — the only difference is WHOSE marker it finds: here it is the
    caller's own, not a genuine sibling's.

    Before the fix, `_goal_has_registered_worker` asked only "is ANY marker for this goal alive?"
    with no comparison against the calling session's own identity at all, so it counted the
    caller's own just-written `agent-start` marker as a live FOREIGN worker and `start()` refused
    to resume the worktree it had itself just cut — exactly the false REFUSED both #2394 and
    #3649 reproduce. `--session-pid` must be what tells the two apart, the same way it already
    does for `_blocked_by_a_live_foreign_agent` one line above in `_resume_blocked_by_a_live_
    sibling` (#1687) — this is the `_goal_has_registered_worker` tie-breaker learning the same
    lesson."""
    loop = _load("loop")
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    dead_picker_pid = 2**30                             # loop.py next's own short-lived pid, already exited
    _claim(d, "rae", goal, dead_picker_pid)
    session_pid = os.getpid()                           # the AGENT's own stable pid ($PPID) -- genuinely
                                                         # alive for the whole test, exactly as SKILL.md's
                                                         # `agent-start --pid $PPID` records it
    loop.agent_start(d, goal, session_pid, {})           # SKILL.md step 3a, first half
    # `work.py start` runs as a distinct child process of the agent -- its OWN os.getpid() is
    # neither the dead picker pid nor the agent's session_pid, exactly the real SKILL.md topology
    # `_calling_session_pids`'s own docstring describes (#1687).
    monkeypatch.setattr(os, "getpid", lambda: dead_picker_pid + 1)
    run = _runner([])
    out = work.start(d, LEDGER_ON, goal, run=run, session_pid=session_pid)   # SKILL.md step 3a, second half
    assert "already started" in out, (
        f"expected start() to recognize its own just-registered worker marker and resume — got "
        f"{out!r} instead. If this REFUSED, `_goal_has_registered_worker` is still counting the "
        f"caller's own marker as a foreign live process.")


# ----------------------------------------------------- #2521 claim ownership (goal-slot dispatch)
# Plan-review round 1, finding 2 (blocking) -- root cause. The round-1 version of this test
# monkeypatched os.getpid() to simulate "a different, fresh pid" for a dispatched subagent -- but
# ledger.pid_alive() (skills/agrim-loop/scripts/ledger.py) probes LIVENESS with a real os.kill(pid,
# 0), independent of os.getpid(); the "crashed" pid in that test was really the live pytest process
# the whole time, so work.start() correctly (if confusingly, given the test's own wrong comment)
# refused it as a genuinely live foreign worker. The plan's own §2 records the actual fix: this
# session's own measured $PPID=43058, identical to the orchestrating session's own value, proves a
# dispatched Task-tool subagent shares its orchestrator's pid -- so there is no cross-pid,
# same-session scenario to model at all. The real boundary is CROSS-SESSION (an old session dies --
# crash, or design (b)'s own HANDOFF/BUDGET relaunch -- a new one resumes), and that needs a
# genuinely dead pid, not a monkeypatched live one. No production code in this task either way --
# Task 2 already established the underlying mechanism needs no change; this is the "run the
# control" proof.


def test_goal_slot_subagent_shares_the_orchestrators_own_pid_within_one_session(tmp_path, monkeypatch):
    """#2521, plan-review round 1 finding 2: MEASURED, not assumed — a real dispatched Task-tool
    subagent's own `$PPID` capture (`echo "PPID=$PPID"; ps -o pid=,ppid= -p $$`) read back
    PPID=43058, IDENTICAL to the orchestrating session's own PPID given as the comparison value.
    So within one session, `agent-start --pid $PPID`/`work.py start --session-pid $PPID` from a
    dispatched goal-slot subagent use the SAME value `next`/`next-batch --session-pid $PPID`
    already used to write the claim — trivially "mine", no cross-pid comparison at all. This test
    pins that trivial case explicitly so it is not merely asserted in prose (plan §2)."""
    loop = _load("loop")
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    shared_pid = 909090                          # stands in for "$PPID", same for orchestrator + subagent
    _claim(d, "rae", goal, shared_pid)            # the orchestrator's own next()/next-batch() claim
    loop.agent_start(d, goal, shared_pid, {})     # the dispatched subagent's own agent-start --pid $PPID
    run = _runner([])
    out = work.start(d, LEDGER_ON, goal, run=run, session_pid=shared_pid)
    assert "already started" in out, (
        f"a goal-slot subagent sharing its orchestrator's own $PPID must resume trivially — got "
        f"{out!r}")


def test_a_new_session_resumes_a_goal_a_dead_old_sessions_own_subagent_left_claimed(
        tmp_path, monkeypatch):
    """#2521, plan-review round 1 finding 2 (the corrected version): the REAL cross-process
    boundary is across SESSIONS, not across a subagent and its own orchestrator (which share a
    pid — see the test above and plan §2). Models it the same way the EXISTING, already-shipped
    tests in this file do (`test_start_still_resumes_a_dead_siblings_claim`,
    `test_start_still_resumes_once_a_dead_pickers_own_registered_worker_has_also_died`): a
    definitely-fake, definitely-dead pid for the OLD session's picker AND its own dispatched
    subagent's worker marker, and the REAL, unpatched `os.getpid()` for the NEW session's own
    `work.py start` call — never a monkeypatch pretending a live pid is dead, which is the exact
    mistake round 1 made."""
    loop = _load("loop")
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    old_session_pid = 2**30                      # the OLD orchestrating session's own $PPID -- a
                                                    # crash, or a HANDOFF/BUDGET relaunch, ended it
    _claim(d, "rae", goal, old_session_pid)
    loop.agent_start(d, goal, old_session_pid, {})   # its dispatched subagent's own marker -- SAME
                                                        # pid as its orchestrator, per the test above
    # monkeypatch.setattr is NOT used here for the "dead" side -- old_session_pid (2**30) is not a
    # real process on any sane system, so ledger.pid_alive()'s real os.kill(pid, 0) probe already
    # reports it dead with no patching required. The NEW session below is this actual test process,
    # genuinely alive, unpatched.
    new_session_pid = os.getpid()
    run = _runner([])
    out = work.start(d, LEDGER_ON, goal, run=run, session_pid=new_session_pid)
    assert "already started" in out, (
        f"a new orchestrating session (and the goal-slot subagent it dispatches, sharing its own "
        f"pid) must resume a goal an old, genuinely-dead session's own subagent left claimed — got "
        f"{out!r}")


def test_start_still_resumes_once_a_dead_pickers_own_registered_worker_has_also_died(tmp_path, monkeypatch):
    """The crash-recovery half of the same acceptance bar: a worker WAS registered for this goal
    but has since died too — a genuine crash, not merely its picker exiting normally. Once BOTH
    signals read dead, `start()` must still resume — a crash must not leave a goal permanently
    unresumable.

    The resume OUTCOME alone cannot tell this from the pre-#1197 code, which resumed on a dead
    picker pid unconditionally and never consulted a worker signal at all — for this exact
    both-dead scenario the two implementations are output-identical, so a stub `live_worker_check`
    that is never even called would pass just as well. `calls` below is the actual regression
    guard: it proves `loop.py`'s `_goal_has_registered_worker` — the PER-GOAL check, not the OR'd
    `_claimed_goal_has_live_worker` (see that function's own docstring for why the reclaim-refusal
    path must never consult the wider, goal-agnostic signal) — was genuinely consulted (and
    returned False) on the way to this resume. Spying has to wrap `work._load` itself, not the
    `loop` module this test's OWN top-of-test `_load("loop")` call returns — per
    `_resume_blocked_by_a_live_sibling`'s own docstring, its lambda calls `work.py`'s
    module-level `_load("loop")` freshly, with no `sys.modules` cache, so it re-executes loop.py
    into a brand new module object every time regardless of what this test does to any earlier
    one."""
    loop = _load("loop")
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    dead_picker_pid = 2**30
    dead_worker_pid = 2**30 - 1                         # also not a real pid on any sane system
    _claim(d, "rae", goal, dead_picker_pid)
    loop.agent_start(d, goal, dead_worker_pid, {})
    monkeypatch.setattr(os, "getpid", lambda: dead_picker_pid + 1)

    calls = []
    real_load = work._load
    def spying_load(name):
        mod = real_load(name)
        if name == "loop":
            real_check = mod._goal_has_registered_worker
            def spying_check(sdlc_dir, g, config, exclude_pids=None):
                calls.append(g)
                return real_check(sdlc_dir, g, config, exclude_pids=exclude_pids)
            mod._goal_has_registered_worker = spying_check
        return mod
    monkeypatch.setattr(work, "_load", spying_load)

    run = _runner([])
    assert "already started" in work.start(d, LEDGER_ON, goal, run=run)
    assert calls == [goal], (
        f"expected _goal_has_registered_worker to actually be consulted for the dead-picker "
        f"claim before resuming — got {calls!r}; without this the test cannot tell #1197's real "
        f"fix from a stub that never checks the worker at all and just resumes on a dead picker "
        f"pid the way the pre-#1197 code always did")


def test_start_still_resumes_a_crashed_goal_even_while_an_unrelated_session_is_active(tmp_path, monkeypatch):
    """Independent review of PR #1237, the `work.py` counterpart of `test_loop.py`'s own
    `test_next_reclaims_a_crashed_goals_claim_even_while_an_unrelated_session_is_active` —
    `_resume_blocked_by_a_live_sibling` had the identical flaw `_next()` did: reusing loop.py's
    OR'd, GOAL-AGNOSTIC `_claimed_goal_has_live_worker` (true whenever ANY managing session is
    registered for `.sdlc`, regardless of which goal it is working) as its own `live_worker_check`.
    Reproduced: `session_start` registers a genuinely live, long-lived managing session — for the
    whole `.sdlc`, not for this goal specifically — while THIS goal has a dead picker pid and has
    never registered its own `agent_alive` marker at all (a genuine crash, not merely a picker
    exiting normally). Pre-correction, `start()` would REFUSE to resume this goal's own worktree
    for as long as the unrelated session ran anything at all — the same permanently-unpickable
    failure mode #1197's acceptance criterion 4 exists to prevent, just reached through `start()`
    instead of `_next()`. Post-correction, `start()` must still resume: the per-goal `_goal_has_
    registered_worker` check correctly reports nothing registered for this goal, and
    `session_active` is no longer consulted on this resume-refusal path at all."""
    loop = _load("loop")
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    dead_picker_pid = 2**30                             # not a real pid on any sane system
    _claim(d, "rae", goal, dead_picker_pid)
    loop.session_start(d, os.getpid())                  # a genuinely live managing session -- for
                                                         # the WHOLE .sdlc, not specifically for this goal
    assert loop.session_active(d, {}) is True            # sanity: the goal-agnostic marker reads live
    assert loop.agent_threads(d, goal) == [], (          # sanity: this goal registered no worker of its own
        "test setup bug: this scenario requires the goal to have NO agent_alive marker at all")
    monkeypatch.setattr(os, "getpid", lambda: dead_picker_pid + 1)   # simulate a different process of "rae"
    run = _runner([])
    assert "already started" in work.start(d, LEDGER_ON, goal, run=run), (
        "expected the crashed goal to still resume despite an UNRELATED session being active -- "
        "its dead picker pid has no corroborating agent_alive marker of its own, so a goal-agnostic "
        "session_active signal must never be what blocks its resume")


def test_start_still_resumes_a_legacy_no_pid_claim_no_regression(tmp_path):
    """Pre-#337 claims have no pid to check — degenerately always mine, matching pre-#374 behavior
    for the transitional legacy case exactly (see ledger.claim_belongs_to_me)."""
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    ent = pathlib.Path(d) / "ledger" / "entries"; ent.mkdir(parents=True, exist_ok=True)
    (ent / "rae.jsonl").write_text(json.dumps(
        {"id": "rae:1", "ts": "2026-08-05T09:00:00Z", "actor": "rae",
         "kind": "claimed", "goal": goal}) + "\n")
    run = _runner([])
    assert "already started" in work.start(d, LEDGER_ON, goal, run=run)


def test_start_resume_guard_survives_a_raising_ledger_read(tmp_path, monkeypatch):
    """Fail-open: this guard NARROWS an already-idempotent resume, it must never become a NEW way
    for start() to break when the ledger is on but something about reading it hiccups."""
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)

    def raiser(*a, **k):
        raise RuntimeError("ledger read broke")
    monkeypatch.setattr(work.ledger, "read_all", raiser)
    run = _runner([])
    assert "already started" in work.start(d, LEDGER_ON, goal, run=run)


# --- commit: the loop's only write path into git -------------------------------------------------

def test_commit_only_ever_touches_this_goals_worktree(tmp_path):
    """The loop gets no general `git` tool, so this is the one place it can write — and it can only
    write here. If it could reach the main checkout the feature would be pointless."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    wt = work._record(d, goal)["worktree"]
    run = _runner([("diff --cached", "a.py")])
    assert work.commit(d, ON, goal, run=run, message="feat: x") == "committed on sdlc/0001-x"
    assert run.calls == ["git add -A", "git diff --cached --name-only",
                         "git diff --cached --no-ext-diff --unified=0", "git commit -m feat: x"]


def test_commit_is_a_noop_when_nothing_changed(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([])
    assert work.commit(d, ON, goal, run=run) == "nothing to commit"
    assert not any("commit" in c for c in run.calls)


# --- commit: never stage a credential (#1555) ----------------------------------------------------
#
# The guard is name-based and it fails CLOSED, the opposite of `_plan_missing_from_branch`'s call
# two sections down. That is not an inconsistency — the two harms differ in REVERSIBILITY. A
# refusal issued by mistake costs an operator minutes and leaves the worktree exactly as it was; a
# credential that reaches a remote is in a PR, in forks and in caches, and can only be rotated. So
# these tests pin BOTH directions hard: what must be refused, and — just as load-bearing — that a
# worktree with nothing secret in it is byte-identical to before the guard existed.


def _staged(names, status=None):
    """A runner whose `add -A` stages `names`. `status` supplies the status letters the refusal path
    reads to choose a remedy and to drop deletions; default = every path is new (`A`).

    The `--name-status -z` handler mirrors git's REAL framing — NUL-terminated FIELDS, status and
    path alternating — because that is the read the refusal path actually makes. Answering it in the
    line-oriented shape would let a parser that never handled `-z` pass here and fail on git."""
    letters = status or {n: "A" for n in names}
    return _runner([
        ("--name-status -z", "".join(f"{letters.get(n, 'A')}\0{n}\0" for n in names)),
        ("diff --cached --name-only", "\n".join(names)),
    ])


def test_commit_makes_exactly_the_same_calls_when_nothing_staged_is_secret_shaped(tmp_path):
    """THE regression guard for every existing adopter. The guard may add a git call on the refusal
    path; it may not add one to the ordinary path, and it may not change the return. Byte-identical
    to the pre-guard behaviour: same three commands, same order, same string."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _staged(["a.py", "docs/readme.md", ".env.example", "keys/id_rsa.pub"])
    assert work.commit(d, ON, goal, run=run, message="feat: x") == "committed on sdlc/0001-x"
    assert run.calls == ["git add -A", "git diff --cached --name-only",
                         "git diff --cached --no-ext-diff --unified=0", "git commit -m feat: x"]


def test_commit_refuses_when_add_A_staged_a_dotenv(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _staged(["a.py", ".env"])
    out = work.commit(d, ON, goal, run=run, message="feat: x")
    assert out.startswith("REFUSED")
    assert ".env" in out
    assert not any(c.startswith("git commit") for c in run.calls)


def test_documented_work_commit_refuses_an_added_cloud_key_without_printing_it(tmp_path):
    """The public `work.py commit` gesture must inspect additions, not only filenames."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    fixture = "AKIA" + "Z" * 16
    run = _runner([
        ("--name-status -z", "A\0src/settings.py\0"),
        ("diff --cached --name-only", "src/settings.py"),
        ("diff --cached --no-ext-diff --unified=0", "+++ b/src/settings.py\n@@ -0,0 +1 @@\n+ACCESS_KEY = " + fixture + "\n"),
    ])
    out = work.commit(d, ON, goal, run=run, message="test: content gate")
    assert out.startswith("REFUSED")
    assert "aws-key" in out and "src/settings.py:1" in out
    assert fixture not in out
    assert not any(c.startswith("git commit") for c in run.calls)


def test_documented_work_commit_allows_the_explicit_synthetic_fixture_value(tmp_path):
    """A narrow known fake used by Sigma's tests must not wedge fixture maintenance."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    fixture = "AKIA" + "IOSFODNN7EXAMPLE"
    run = _runner([
        ("diff --cached --name-only", "tests/test_fixture.py"),
        ("diff --cached --no-ext-diff --unified=0", "+++ b/tests/test_fixture.py\n@@ -0,0 +1 @@\n+fixture = " + fixture + "\n"),
    ])
    assert work.commit(d, ON, goal, run=run, message="test: fixture") == "committed on sdlc/0001-x"


def test_commit_refusal_names_every_offending_path_not_just_the_first(tmp_path):
    """A refusal that names one of three sends the operator round the loop three times."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _staged(["a.py", ".env", "certs/prod.pem", "svc/credentials.json"])
    out = work.commit(d, ON, goal, run=run)
    for path in (".env", "certs/prod.pem", "svc/credentials.json"):
        assert path in out
    assert "a.py" not in out                      # never accuse an innocent path


def test_commit_refusal_resets_the_index_so_the_named_ignore_remedy_can_work(tmp_path):
    """The whole point of the reset, and the reason it is not optional. `git add -A` has ALREADY put
    the offender in the index, and an indexed path is a tracked path — `.gitignore` no longer
    applies to it, so the remedy the refusal names would be a lie and `git add -f` would be the only
    exit left (#1504's hole). Proven end to end against real git further down; here we pin that the
    call is made at all, and made BEFORE returning."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _staged([".env", "a.py"])
    out = work.commit(d, ON, goal, run=run)
    assert out.startswith("REFUSED")
    assert "git reset -q -- :(literal).env" in run.calls          # the offender, and ONLY the offender
    assert "index entries were reset" in out
    assert not any(c.startswith("git commit") for c in run.calls)


def test_commit_still_refuses_when_the_index_reset_itself_fails(tmp_path):
    """Fail closed on the failure of the cleanup, not open. A reset that did not happen makes the
    refusal harder to satisfy; it does not make the credential safe to commit."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("reset", RuntimeError("index.lock exists")),
                   ("--name-status -z", "A\0.env\0"),
                   ("diff --cached --name-only", ".env")])
    out = work.commit(d, ON, goal, run=run)
    assert out.startswith("REFUSED")
    assert not any(c.startswith("git commit") for c in run.calls)
    # ...and it must not narrate a cleanup that did not happen: the operator has to know the path is
    # still in the index, or the ignore rule they are about to add will silently do nothing.
    assert "STILL STAGED" in out
    assert "index entries were reset" not in out


def test_commit_refusal_names_the_ignore_remedy_for_a_path_new_to_the_branch(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    out = work.commit(d, ON, goal, run=_staged([".env"], {".env": "A"}))
    assert ".gitignore" in out


def test_commit_refusal_names_the_untrack_remedy_for_a_secret_already_on_the_branch(tmp_path):
    """A MODIFIED tracked secret is a different problem with a different remedy: it is already in
    the branch's history, so ignoring it changes nothing — `git add -A` re-stages a tracked path
    whatever `.gitignore` says. Naming the ignore remedy here would be exactly the unsatisfiable
    refusal this issue exists to avoid."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    out = work.commit(d, ON, goal, run=_staged(["secrets/deploy.key"], {"secrets/deploy.key": "M"}))
    assert "rm --cached" in out
    assert "secrets/deploy.key" in out
    assert "ON PURPOSE" in out                    # ...and the allowlist, for a file committed on purpose


def test_commit_commits_a_path_named_exactly_in_the_allowlist(tmp_path):
    """The deliberate case — a fixture key, a test cert. Explicit, per-path, and committed to the
    adopter's own config.json, so turning it on is itself a reviewable change."""
    cfg = {"work": {"enabled": True, "allow_secret_paths": ["tests/fixtures/rsa_test.key"]}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d)
    run = _staged(["a.py", "tests/fixtures/rsa_test.key"])
    assert work.commit(d, cfg, goal, run=run, message="test: fixture") == "committed on sdlc/0001-x"
    assert run.calls == ["git add -A", "git diff --cached --name-only",
                         "git diff --cached --no-ext-diff --unified=0", "git commit -m test: fixture"]


def test_the_allowlist_is_exact_paths_not_globs_and_not_an_off_switch(tmp_path):
    """A glob would be an off switch wearing an allowlist's clothes: one `*` and every future
    credential in that repo commits silently. Only the literal path clears its own refusal."""
    cfg = {"work": {"enabled": True, "allow_secret_paths": ["*", "certs/*.pem", "prod.pem"]}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d)
    out = work.commit(d, cfg, goal, run=_staged(["certs/prod.pem"]))
    assert out.startswith("REFUSED")
    assert "certs/prod.pem" in out


def test_the_allowlist_clears_only_the_path_it_names(tmp_path):
    cfg = {"work": {"enabled": True, "allow_secret_paths": ["tests/fixtures/a.pem"]}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d)
    out = work.commit(d, cfg, goal, run=_staged(["tests/fixtures/a.pem", ".env"]))
    assert out.startswith("REFUSED")
    assert ".env" in out
    assert "tests/fixtures/a.pem" not in out


def test_a_malformed_allowlist_never_crashes_the_commit_path(tmp_path):
    """`allow_secret_paths` is hand-edited JSON, and `commit()` is the one verb every goal in every
    adopter repo goes through. A shape typo must degrade to "no allowlist" — still REFUSING, never
    crashing, and never silently allowing: a bare string would pass a substring test and turn the
    typo into the bypass. The malformed WRAPPER is here too because `/agrim-doctor` cross-loads this
    same reader, and its own malformed-config sweep caught exactly that (`"work": true`)."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    for cfg in ({"work": {"enabled": True, "allow_secret_paths": ".env"}},
                {"work": {"enabled": True, "allow_secret_paths": {".env": True}}},
                {"work": {"enabled": True, "allow_secret_paths": [None, 7]}},
                {"work": True}, {"work": "yes"}, {"work": ["enabled"]}, {}):
        assert work.commit(d, cfg, goal, run=_staged([".env"])).startswith("REFUSED"), cfg


def test_the_refusal_names_the_override_by_its_real_config_key(tmp_path):
    """A remedy the operator cannot find is not a remedy. The refusal has to carry the literal key,
    not a description of it."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    out = work.commit(d, ON, goal, run=_staged([".env"]))
    assert "allow_secret_paths" in out


#: (basename, is_secret_shaped, why this case is in the table at all)
_NAME_TABLE = [
    (".env", True, "the canonical case"),
    (".env.local", True, "a variant — the suffix is not a doc marker"),
    (".env.production", True, "the one most likely to hold live credentials"),
    (".ENV", True, "case: a case-insensitive filesystem makes this the SAME file"),
    (".Env.Production", True, "case, mixed"),
    (".env.example", False, "the documented template every repo commits on purpose"),
    (".env.sample", False, "same, second spelling"),
    (".env.template", False, "same, third spelling"),
    (".env.local.example", False, "a doc marker anywhere after `env` still means template"),
    ("env.sh", False, "no leading dot: a shell script, and a common harmless one"),
    (".envrc", False, "direnv config — usually committed; not an `.env.<x>` variant"),
    ("prod.pem", True, "a private key by extension"),
    ("cert.pem", False, "a CERTIFICATE — the public half, committed on purpose by working repos"),
    ("fullchain.pem", False, "letsencrypt's own output filename; public"),
    ("chain.pem", False, "same"),
    ("ca.pem", False, "a CA CERTIFICATE is public"),
    ("ca.key", True, "...but the CA's KEY is the most secret thing in the repo"),
    ("cert.key", True, "the private half that sits beside cert.pem"),
    ("public.key", False, "explicitly the public half"),
    ("server-public.key", False, "a `public` token anywhere in the stem clears it"),
    ("cert.p12", True, "a .p12 ALWAYS carries a private key — no public exemption applies"),
    ("certificate.jks", True, "same for a java keystore"),
    ("deploy.key", True, "an ssh deploy key by extension"),
    ("KEYSTORE.JKS", True, "case again, on a suffix rather than a prefix"),
    ("id_rsa", True, "the private half"),
    ("id_ed25519", True, "the modern private half"),
    ("id_rsa.pub", False, "the PUBLIC half is not a secret and is routinely committed"),
    ("id_ed25519.pub", False, "same"),
    ("credentials.json", True, "named in the issue"),
    ("credentials", True, "the extensionless form"),
    ("serviceAccountKey.json", True, "Firebase's REAL default filename — neither hyphen nor underscore"),
    ("aws-credentials.json", True, "a prefixed credentials download"),
    ("client_secret_1234.json", True, "the Google OAuth client download's real shape"),
    ("my-project-service-account.json", True, "service-account JSON, hyphenated"),
    ("service_account_key.json", True, "service-account JSON, underscored"),
    ("package.json", False, "an ordinary JSON file"),
    (".netrc", True, "plain-text host credentials"),
    ("README.md", False, "the control"),
    ("keyboard.py", False, "contains 'key' but is not a key"),
    ("monkey.py", False, "ends in 'key' before the extension — must not match"),
]


def test_the_name_predicate_answers_the_whole_table():
    for name, want, why in _NAME_TABLE:
        assert work._secret_shaped(name) is want, f"{name}: {why}"


def test_a_secret_shaped_basename_is_refused_wherever_it_sits(tmp_path):
    """Location is not evidence. A `.pem` under `docs/` is as much a private key as one at the root
    — and if it genuinely is a fixture, the allowlist is the answer, not the directory it lives in."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    out = work.commit(d, ON, goal, run=_staged(["docs/examples/deep/nested/prod.pem"]))
    assert out.startswith("REFUSED")
    assert "docs/examples/deep/nested/prod.pem" in out


def test_a_harmless_file_inside_a_directory_named_like_a_secret_is_not_refused(tmp_path):
    """`git diff --cached --name-only` lists FILES, never directories, so a directory called
    `id_rsa/` reaches this guard only as `id_rsa/<file>`. The credential-bearing thing is the file,
    and `README.md` is not one — matching on any path SEGMENT would refuse a whole tree because of
    the folder someone put it in."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _staged(["id_rsa/README.md", "certs.pem/notes.txt"])
    assert work.commit(d, ON, goal, run=run, message="docs: x") == "committed on sdlc/0001-x"


def test_a_feature_registry_shard_is_never_an_offender_however_the_unit_was_named(tmp_path):
    """#1577 item 3. A unit may legitimately be called `credentials`, `service-account` or
    `client_secret-rotation`, and `feature_registry.unit_path` turns any of them into
    `.sdlc/features/units/<name>.json` — a basename the denylist reads as a service-account
    download. The registry is the ONE directory in an adopter's repo whose file names this kit
    chooses rather than a human, from a name `is_unit_name` already validated, so the denylist's
    premise ("a human called this file that") does not hold there.

    IT IS EXEMPTED RATHER THAN RE-WORDED, and the choice matters. `.sdlc/features/` is deliberately
    NOT gitignored — the model requires every participating repo to hold the whole entry — so the
    row's first remedy, "add it to .gitignore", does not merely read badly: following it breaks the
    registry. And re-wording leaves the refusal in place, which no allowlist entry can be written
    for ahead of time, on a name the adopter is entitled to use."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    shards = [".sdlc/features/units/credentials.json",
              ".sdlc/features/units/service-account.json",
              ".sdlc/features/units/client_secret-rotation.json"]
    assert work.commit(d, ON, goal, run=_staged(shards + ["a.py"]),
                       message="feat: x") == "committed on sdlc/0001-x"


def test_the_registry_exemption_is_anchored_at_the_registry_not_a_path_shape(tmp_path):
    """The loosening edge, and the only one that matters here. `features/units/` is an ordinary
    thing to call a source directory, so an exemption matched on the SHAPE of a path would quietly
    stop refusing a real credential in an application tree. It is anchored on the registry's own
    location, and on a basename that is a unit name plus `.json` — nothing else in that directory
    is exempt either."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    for path in ("src/features/units/credentials.json",       # not the registry
                 "app/.sdlc/features/units/credentials.json",  # not at the repo root
                 ".sdlc/features/credentials.json",            # the registry, but not a shard
                 ".sdlc/features/units/nested/credentials.json",
                 ".sdlc/features/units/credentials.pem",       # a unit name cannot make this
                 ".sdlc/features/units/.env"):
        out = work.commit(d, ON, _started(d, "0002-y.md"), run=_staged([path]))
        assert out.startswith("REFUSED"), path


def test_a_git_quoted_path_is_still_matched(tmp_path):
    """git C-quotes any path with non-ASCII or special characters in `--name-only` output
    (`"cl\\303\\251.pem"`). Reading the quotes as part of the basename would make renaming a key to
    a non-ASCII name a bypass."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    out = work.commit(d, ON, goal, run=_staged(['"certs/cl\\303\\251.pem"']))
    assert out.startswith("REFUSED")


# --- commit: the same guard, against real git ----------------------------------------------------
#
# Every test above answers from a fixture, and a fixture will happily agree with a broken
# implementation about what git staged. This guard's entire subject is what git ACTUALLY staged, and
# that exact blind spot already bit this file once: #1548's pathspec mutant survived the whole
# stubbed suite and was caught only here. So the two claims that matter most — "an already-ignored
# .env never fires" and "the remedy the refusal names really does clear it" — are settled by real
# git in a real temporary repo, not by a handler tuple.


def _real_repo(tmp_path, gitignore=""):
    """A real git repo with one commit, plus a work record pointing a goal at it as its worktree."""
    import subprocess
    repo = tmp_path / "repo"
    (repo / ".sdlc" / "state").mkdir(parents=True)
    (repo / ".sdlc" / "config.json").write_text(json.dumps(ON))

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    git("config", "commit.gpgsign", "false")
    (repo / ".gitignore").write_text(gitignore + ".sdlc/\n")
    (repo / "a.py").write_text("x = 1\n")
    git("add", "--", ".gitignore", "a.py")
    git("commit", "-q", "-m", "base")

    d = str(repo / ".sdlc")
    state.start_run(d)
    work._save(d, "0001-x.md", {"worktree": str(repo), "branch": "sdlc/0001-x", "base": "main",
                                "remote": "origin", "pr": ""})

    def committed():
        out = subprocess.run(["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"],
                             capture_output=True, text=True, check=True)
        return set(out.stdout.split())

    return repo, d, committed


def test_real_git_an_already_ignored_dotenv_never_trips_the_guard(tmp_path):
    """The single most important false-positive case, and the one a stub cannot settle. `git add -A`
    honours `.gitignore`, so a repo that already did the right thing must see NO behaviour change at
    all — otherwise this guard is a nuisance in every repo it was not written for."""
    repo, d, committed = _real_repo(tmp_path, gitignore=".env\n")
    (repo / ".env").write_text("TOKEN=live-secret\n")
    (repo / "b.py").write_text("y = 2\n")
    assert work.commit(d, ON, "0001-x.md", message="feat: b") == "committed on sdlc/0001-x"
    assert committed() == {"b.py"}
    assert (repo / ".env").read_text() == "TOKEN=live-secret\n"      # still on disk, untouched


def test_real_git_refuses_a_content_shaped_key_in_an_ordinary_filename(tmp_path):
    """Exercise the exact staged-diff parser used by the documented commit gesture."""
    repo, d, committed = _real_repo(tmp_path)
    fixture = "AKIA" + "Z" * 16
    (repo / "settings.py").write_text("ACCESS_KEY = " + fixture + "\n")

    refusal = work.commit(d, ON, "0001-x.md", message="test: content")
    assert refusal.startswith("REFUSED")
    assert "aws-key at settings.py:1:" in refusal
    assert fixture not in refusal
    assert "settings.py" not in committed()


def test_real_git_refuses_an_unignored_dotenv_and_the_named_remedy_then_clears_it(tmp_path):
    """The end-to-end claim. Refuse; then perform exactly the remedy the refusal names — add the
    path to `.gitignore` and re-run `work.py commit` — and it must go green with the secret absent
    from the commit. This is what the index reset buys: without it the second call still finds the
    path in the index, refuses again, and `git add -f` becomes the only way out."""
    repo, d, committed = _real_repo(tmp_path)
    (repo / ".env").write_text("TOKEN=live-secret\n")
    (repo / "b.py").write_text("y = 2\n")

    refusal = work.commit(d, ON, "0001-x.md", message="feat: b")
    assert refusal.startswith("REFUSED") and ".env" in refusal
    assert committed() == {"a.py", ".gitignore"}                     # nothing new landed

    with (repo / ".gitignore").open("a", encoding="utf-8") as fh:    # the remedy, verbatim
        fh.write(".env\n")

    assert work.commit(d, ON, "0001-x.md", message="feat: b") == "committed on sdlc/0001-x"
    assert committed() == {"b.py", ".gitignore"}
    assert ".env" not in committed()
    assert (repo / ".env").read_text() == "TOKEN=live-secret\n"      # never deleted, only unstaged


def test_real_git_the_allowlist_remedy_also_clears_the_refusal(tmp_path):
    """The other named remedy, for the deliberate case. It has to work against real git too — a
    fixture key that can only be committed by ignoring it is a fixture that cannot be committed."""
    repo, d, committed = _real_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "fixture.pem").write_text("-----BEGIN TESTING KEY-----\n")

    assert work.commit(d, ON, "0001-x.md", message="test: k").startswith("REFUSED")

    cfg = {"work": {"enabled": True, "allow_secret_paths": ["tests/fixture.pem"]}}
    assert work.commit(d, cfg, "0001-x.md", message="test: k") == "committed on sdlc/0001-x"
    assert committed() == {"tests/fixture.pem"}


def test_real_git_a_dotenv_example_is_committed_like_any_other_file(tmp_path):
    """The template file every repo commits on purpose. If this refused, the guard would be
    unshippable: `.env.example` is in the root of a large fraction of real repositories."""
    repo, d, committed = _real_repo(tmp_path)
    (repo / ".env.example").write_text("TOKEN=\n")
    assert work.commit(d, ON, "0001-x.md", message="docs: env") == "committed on sdlc/0001-x"
    assert committed() == {".env.example"}


#: The two remedy shapes, read back OUT of the refusal exactly as printed. Extracting them rather
#: than retyping them is the whole point: a test that retypes the fix proves the fix works, not that
#: the message describes it.
_PRINTED_IGNORE = re.compile(r"add a line `([^`]+)` to \.gitignore")
_PRINTED_UNTRACK = re.compile(r"`git -C (\S+) rm --cached -- (.+?)`, then")


def _perform_printed_remedy(repo, refusal):
    """Do literally what the refusal says, character for character. Returns False if it said nothing
    actionable — which is itself a failure, and the caller asserts on it."""
    import subprocess
    acted = False
    for match in _PRINTED_UNTRACK.finditer(refusal):
        subprocess.run(["git", "-C", match.group(1), "rm", "--cached", "--", match.group(2)],
                       check=True, capture_output=True)
        acted = True
    for match in _PRINTED_IGNORE.finditer(refusal):
        with (repo / ".gitignore").open("a", encoding="utf-8") as fh:
            fh.write(match.group(1) + "\n")
        acted = True
    return acted


def test_real_git_the_printed_untrack_remedy_actually_clears_the_refusal(tmp_path):
    """#1555 review B2, and the lesson behind it. The first version of this test asserted only that
    the message CONTAINED `rm --cached`. It did — and performing it looped forever, because the
    staged deletion `rm --cached` leaves was itself read as an offender and the whole-index reset
    silently undid the untrack. A test that checks the WORDS of a promise but not the promise is how
    a broken remedy ships, and "the remedy must be satisfiable" was the one property this guard
    could not afford to get wrong. So: perform what it printed, re-run `commit`, and require it to
    go green — with the operator's file still on disk, because `rm --cached` must never delete it."""
    import subprocess
    repo, d, committed = _real_repo(tmp_path)
    (repo / "deploy.key").write_text("v1\n")
    subprocess.run(["git", "-C", str(repo), "add", "--", "deploy.key"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "pre-existing"], check=True,
                   capture_output=True)
    (repo / "deploy.key").write_text("v2\n")

    refusal = work.commit(d, ON, "0001-x.md", message="chore: rotate")
    assert refusal.startswith("REFUSED")
    assert "rm --cached" in refusal and "deploy.key" in refusal
    assert _perform_printed_remedy(repo, refusal)

    assert work.commit(d, ON, "0001-x.md", message="chore: rotate") == "committed on sdlc/0001-x"
    assert (repo / "deploy.key").read_text() == "v2\n"        # never deleted, only untracked


def test_real_git_a_goal_that_deletes_a_leaked_secret_is_not_refused(tmp_path):
    """#1555 review B1. `git diff --cached --name-only` lists what the index CHANGES, deletions
    included — so reading it naively made the guard refuse the one operation it exists to enable:
    removing a credential that is already committed. It refused permanently, and told the operator
    to untrack a file they had just deleted."""
    import subprocess
    repo, d, committed = _real_repo(tmp_path)
    (repo / ".env").write_text("TOKEN=leaked\n")
    subprocess.run(["git", "-C", str(repo), "add", "--", ".env"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "the leak"], check=True,
                   capture_output=True)

    (repo / ".env").unlink()                                   # the remediation goal, in full
    with (repo / ".gitignore").open("a", encoding="utf-8") as fh:
        fh.write(".env\n")

    assert work.commit(d, ON, "0001-x.md", message="fix: drop the leaked env") == \
        "committed on sdlc/0001-x"
    assert committed() == {".env", ".gitignore"}               # the deletion landed
    assert not (repo / ".env").exists()


def test_real_git_every_printed_gitignore_line_is_a_pattern_git_can_actually_use(tmp_path):
    """#1555 review N1. Detection was never the problem — the REMEDY TEXT was. A raw path printed as
    if it were a `.gitignore` pattern fails for at least six ordinary shapes: `[`, `*` and `?` are
    wildcards, a backslash escapes, and a leading `#` or `!` turns the line into a comment or a
    NEGATION (which does the opposite of what was asked). All six at once, remedy taken from the
    text and pasted verbatim, and the commit must then go green with every file still on disk."""
    repo, d, committed = _real_repo(tmp_path)
    hostile = ["certs/a[1].pem", "certs/cl\u00e9.pem", "#note.pem", "!bang.pem",
               "certs/star*x.pem", "certs/q?m.pem"]
    (repo / "certs").mkdir()
    for name in hostile:
        (repo / name).write_text("-----BEGIN PRIVATE KEY-----\n")
    (repo / "b.py").write_text("y = 2\n")

    refusal = work.commit(d, ON, "0001-x.md", message="feat: b")
    assert refusal.startswith("REFUSED")
    for name in hostile:
        assert name in refusal                                 # detection is right for all six
    assert _perform_printed_remedy(repo, refusal)

    assert work.commit(d, ON, "0001-x.md", message="feat: b") == "committed on sdlc/0001-x"
    assert committed() == {"b.py", ".gitignore"}
    for name in hostile:
        assert (repo / name).exists(), name                    # ignored, never removed


def test_real_git_the_unstage_leaves_another_paths_staged_deletion_alone(tmp_path):
    """Why the unstage is per-path. A bare `git reset` restores the whole index from HEAD, which
    silently undoes any `git rm --cached` the operator performed BECAUSE THIS REFUSAL TOLD THEM TO —
    the second half of the B2 loop. Here a staged deletion and a fresh offender arrive together: the
    offender is unstaged, the deletion survives, and the run completes after one remedy."""
    import subprocess
    repo, d, committed = _real_repo(tmp_path)
    (repo / "old.pem").write_text("v1\n")
    subprocess.run(["git", "-C", str(repo), "add", "--", "old.pem"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "pre"], check=True,
                   capture_output=True)
    (repo / "old.pem").unlink()
    (repo / ".env").write_text("TOKEN=live\n")

    refusal = work.commit(d, ON, "0001-x.md", message="fix: x")
    assert refusal.startswith("REFUSED") and ".env" in refusal
    assert "old.pem" not in refusal                            # a deletion is never an offender
    staged = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-status"],
                            capture_output=True, text=True).stdout
    assert "D\told.pem" in staged                              # ...and it survived the unstage

    assert _perform_printed_remedy(repo, refusal)
    assert work.commit(d, ON, "0001-x.md", message="fix: x") == "committed on sdlc/0001-x"
    assert committed() == {"old.pem", ".gitignore"}


def test_real_git_a_deliberately_committed_env_is_cleared_by_the_allowlist(tmp_path):
    """#1555 review N3. Symfony commits a non-secret `.env` by convention. Tracked-and-unmodified
    never reaches the guard at all, but the first goal that EDITS it does — and for that repo the
    answer is not to untrack a file they mean to keep. The refusal has to say so, and the allowlist
    has to clear it."""
    import subprocess
    repo, d, committed = _real_repo(tmp_path)
    (repo / ".env").write_text("APP_ENV=dev\n")
    subprocess.run(["git", "-C", str(repo), "add", "--", ".env"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "symfony"], check=True,
                   capture_output=True)
    (repo / ".env").write_text("APP_ENV=prod\n")

    refusal = work.commit(d, ON, "0001-x.md", message="chore: env")
    assert refusal.startswith("REFUSED")
    assert "ON PURPOSE" in refusal and "allow_secret_paths" in refusal

    cfg = {"work": {"enabled": True, "allow_secret_paths": [".env"]}}
    assert work.commit(d, cfg, "0001-x.md", message="chore: env") == "committed on sdlc/0001-x"
    assert committed() == {".env"}


def test_real_git_the_unstage_does_not_destroy_an_in_progress_merge(tmp_path):
    """#1555 review N4. "Nothing on disk moves" was true of a bare `git reset` and still did not
    make it safe: it deletes `MERGE_HEAD`, after which `git merge --abort` fails outright and the
    conflict markers stay in the tree with no way back. Measured against real git — a pathspec reset
    leaves the merge intact, a bare one does not."""
    import subprocess

    def git(*args, **kw):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, **kw)

    repo, d, committed = _real_repo(tmp_path)
    git("checkout", "-q", "-b", "side", check=True)
    (repo / "conflict.txt").write_text("side\n")
    git("add", "--", "conflict.txt", check=True); git("commit", "-q", "-m", "side", check=True)
    git("checkout", "-q", "-", check=True)
    (repo / "conflict.txt").write_text("main\n")
    git("add", "--", "conflict.txt", check=True); git("commit", "-q", "-m", "main", check=True)
    assert git("merge", "side").returncode != 0                # a real conflicted merge
    assert (repo / ".git" / "MERGE_HEAD").exists()

    (repo / ".env").write_text("TOKEN=live\n")
    assert work.commit(d, ON, "0001-x.md", message="feat: x").startswith("REFUSED")

    assert (repo / ".git" / "MERGE_HEAD").exists(), "the refusal destroyed an in-progress merge"
    assert git("merge", "--abort").returncode == 0             # ...and it is still abortable


def test_the_gitignore_pattern_escapes_or_declines_every_shape():
    """The unit-level table behind the real-git test above. Anchoring with `/` is what neutralises a
    leading `#`/`!` and pins the pattern to this one path; the wildcards are escaped; a newline is
    declined outright, because git has no escape for one and printing a pattern that cannot work is
    the unperformable remedy this whole guard exists to avoid."""
    assert work._gitignore_pattern("certs/a[1].pem") == "/certs/a\\[1\\].pem"
    assert work._gitignore_pattern("certs/star*x.pem") == "/certs/star\\*x.pem"
    assert work._gitignore_pattern("certs/q?m.pem") == "/certs/q\\?m.pem"
    assert work._gitignore_pattern("a\\b.pem") == "/a\\\\b.pem"
    assert work._gitignore_pattern("#note.pem") == "/#note.pem"          # no longer a comment
    assert work._gitignore_pattern("!bang.pem") == "/!bang.pem"          # no longer a negation
    assert work._gitignore_pattern("certs/cl\u00e9.pem") == "/certs/cl\u00e9.pem"
    assert work._gitignore_pattern("trail .pem ") == "/trail .pem\\ "    # git strips a bare one
    assert work._gitignore_pattern("two\nlines.pem") == ""               # inexpressible -> allowlist
    assert work._gitignore_pattern("") == ""


def test_a_staged_deletion_is_never_an_offender_whatever_its_name(tmp_path):
    """The unit-level half of B1, on the stub, so the drop is pinned independently of real git."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _staged([".env", "certs/prod.pem"], {".env": "D", "certs/prod.pem": "D"})
    assert work.commit(d, ON, goal, run=run, message="fix: purge") == "committed on sdlc/0001-x"
    assert not any(c.startswith("git reset") for c in run.calls)


def test_a_deletion_and_a_live_offender_together_refuse_only_for_the_offender(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    out = work.commit(d, ON, goal, run=_staged(["gone.pem", ".env"],
                                               {"gone.pem": "D", ".env": "A"}))
    assert out.startswith("REFUSED")
    assert ".env" in out and "gone.pem" not in out


def test_the_refusal_reads_the_zero_delimited_status_so_a_rename_reports_its_new_path(tmp_path):
    """`--name-status -z` frames a rename as `R100`, old, new — three fields, not two. A parser that
    assumed pairs would take `old` as the path and then unstage, name and blame the wrong file."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("--name-status -z", "R100\0old.txt\0secrets/new.pem\0"),
                   ("diff --cached --name-only", "secrets/new.pem")])
    out = work.commit(d, ON, goal, run=run)
    assert out.startswith("REFUSED")
    assert "secrets/new.pem" in out and "old.txt" not in out
    assert "rm --cached" in out                                # R is not A: it is already tracked


# --- pr: refuse to open something that says nothing ----------------------------------------------

def test_pr_refuses_a_dirty_worktree(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("status --porcelain", " M a.py")])
    assert "uncommitted changes" in work.pr(d, ON, goal, run=run)
    assert not any("push" in c for c in run.calls)


def test_pr_refuses_an_empty_branch(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("rev-list", "0")])
    assert "no commits" in work.pr(d, ON, goal, run=run)
    assert not any("push" in c for c in run.calls)


# --- pr: the goal's plan has to be ON the branch it is meant to explain (#1548) ------------------

#: The two UNLIKE failures `git check-ignore` can hand `_run`, in their real shapes (both captured
#: from git 2.49 through `work._run`). Under `-v --non-matching` git ANSWERS "no pattern matched"
#: with `::\t<path>` on a non-zero exit; a repo it cannot read fails with a diagnostic and no such
#: marker. The guard must refuse on the first and stay silent on the second, so a fixture that
#: blurred them would be testing nothing.
NOT_IGNORED = RuntimeError("git check-ignore -v --non-matching -- .sdlc/plans/0001-x.md: "
                           "::\t.sdlc/plans/0001-x.md")
UNREADABLE_IGNORES = RuntimeError("git check-ignore -v --non-matching -- .sdlc/plans/0001-x.md: "
                                  "fatal: unable to read .gitignore")

#: The EXACT argv each read must issue. Both are pathspec-scoped, and both silently stop working if
#: the pathspec is dropped: `git ls-files` with no pathspec is non-empty on any repo that tracks
#: anything, so the guard would read every goal as "plan already on the branch" and #1548 would be
#: back with the whole suite still green. Substring fixtures cannot see that -- `("ls-files", ...)`
#: matches the pathspec-less call too -- so the calls are pinned by equality, not by matching.
LS_FILES = "git ls-files -- .sdlc/plans/0001-x.md"
CHECK_IGNORE = "git check-ignore -v --non-matching -- .sdlc/plans/0001-x.md"


def _plan(sdlc_dir, name="0001-x"):
    """The plan phase's artifact, where the plan phase actually writes it: the MAIN checkout's
    `.sdlc/plans/`, never the goal's worktree. That split IS the bug (#1548) -- `commit()`'s
    `git add -A` runs in the worktree and cannot see this file."""
    p = pathlib.Path(sdlc_dir) / "plans" / (name + ".md")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# plan\n")
    return p


def test_pr_refuses_when_the_goals_plan_is_absent_from_its_branch(tmp_path):
    """#1548: the plan is the artifact the PR's reviewer is meant to check the change against, and
    it was reaching PRs only when someone remembered. Names the plan AND the branch, and pushes
    nothing -- a refusal a reader has to open the checker to act on is not an improvement."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    _plan(d)
    run = _runner([("rev-list", "2"), ("ls-files", ""), ("check-ignore", NOT_IGNORED)])
    out = work.pr(d, ON, goal, run=run)
    assert ".sdlc/plans/0001-x.md" in out
    assert "sdlc/0001-x" in out
    assert not any("push" in c for c in run.calls)
    assert not any(c.startswith("gh ") for c in run.calls)
    # Both reads are scoped TO THE PLAN, asserted as whole argv. See LS_FILES/CHECK_IGNORE.
    assert LS_FILES in run.calls
    assert CHECK_IGNORE in run.calls


def test_pr_is_unaffected_when_the_goal_has_no_plan(tmp_path):
    """A goal that legitimately never wrote one is not a goal that lost one -- and the guard does
    not even ask git about it."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("rev-list", "2"), ("pulls?head=", "9")])
    assert work.pr(d, ON, goal, run=run) == "PR #9"
    assert not any("check-ignore" in c for c in run.calls)


def test_pr_accepts_a_plan_already_tracked_on_the_branch(tmp_path):
    """`git ls-files` answering is the whole test: the plan is IN the branch, however it got there
    (this goal committed it, or the base already carried it). Nothing left to demand."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    _plan(d)
    # Keyed on the SCOPED call, so a guard that stopped passing a pathspec would fall through to
    # `_runner`'s "" default and this test would notice, instead of matching on `ls-files` alone.
    run = _runner([("rev-list", "2"), (LS_FILES, ".sdlc/plans/0001-x.md"), ("pulls?head=", "9")])
    assert work.pr(d, ON, goal, run=run) == "PR #9"
    assert LS_FILES in run.calls
    assert not any("check-ignore" in c for c in run.calls)


def test_pr_does_not_demand_a_plan_this_repo_deliberately_ignores(tmp_path):
    """The #1504 boundary, mechanised. A repo whose `.gitignore` covers the plan path has already
    decided plans are not a committed artifact here (Sigma's own blanket `.sdlc/` is exactly
    that repo). The guard must stay silent, because the only way to satisfy it there would be the
    `git add -f` that #1504 exists to stop."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    _plan(d)
    run = _runner([("rev-list", "2"), ("ls-files", ""), ("check-ignore", ""), ("pulls?head=", "9")])
    assert work.pr(d, ON, goal, run=run) == "PR #9"
    assert CHECK_IGNORE in run.calls


def test_pr_says_nothing_when_the_ignore_rules_cannot_be_read(tmp_path):
    """The guard FAILS OPEN, and this is the test that makes that word true rather than aspirational.
    A `check-ignore` that fails WITHOUT git's `::\t` not-matched marker (exit 128 -- an unreadable
    `.gitignore`, a wedged worktree) is a failure to read the rules, not an answer about them. It
    must not manufacture a demand: the remedy a refusal names is `copy the plan in, then commit`,
    and `commit()` honours `.gitignore`, so a refusal misfired at a repo that DOES ignore plans can
    never be satisfied -- copy, `nothing to commit`, refuse, forever, with `git add -f` the only way
    out (#1504's hole)."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    _plan(d)
    run = _runner([("rev-list", "2"), ("ls-files", ""), ("check-ignore", UNREADABLE_IGNORES),
                   ("pulls?head=", "9")])
    assert work.pr(d, ON, goal, run=run) == "PR #9"
    assert CHECK_IGNORE in run.calls


def test_pr_finds_the_plan_filed_under_the_bare_slug(tmp_path):
    """`0001-x.md` may file its plan as `x.md`. One resolver serves the reviewer brief and this
    guard, so a goal cannot be judged plan-less by the guard and plan-ful by the reviewer."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    _plan(d, name="x")
    run = _runner([("rev-list", "2"), ("ls-files", ""), ("check-ignore", NOT_IGNORED)])
    assert ".sdlc/plans/x.md" in work.pr(d, ON, goal, run=run)
    assert "git ls-files -- .sdlc/plans/x.md" in run.calls


# --- pr: the goal's research dossier has to be ON the branch it is meant to explain (#1801) -------
#
# #1801 measured the identical structural gap #1548 found for the plan: `agrim-research` files
# `.sdlc/research/<stem>.md` in the MAIN checkout too (SKILL.md step 2 runs before 3a ever cuts a
# worktree, so for research there is not even a worktree to write into), so `commit()`'s `git add -A`
# inside the worktree can never see it either. `_research_missing_from_branch` is the same guard,
# same resolver, same fail-open direction, applied to `research/` -- these tests mirror the `plans/`
# ones above rather than re-deriving the reasoning; see those docstrings for the "why" of each shape.

NOT_IGNORED_RESEARCH = RuntimeError(
    "git check-ignore -v --non-matching -- .sdlc/research/0001-x.md: ::\t.sdlc/research/0001-x.md")
UNREADABLE_IGNORES_RESEARCH = RuntimeError(
    "git check-ignore -v --non-matching -- .sdlc/research/0001-x.md: fatal: unable to read .gitignore")

LS_FILES_RESEARCH = "git ls-files -- .sdlc/research/0001-x.md"
CHECK_IGNORE_RESEARCH = "git check-ignore -v --non-matching -- .sdlc/research/0001-x.md"


def _research(sdlc_dir, name="0001-x"):
    """The research phase's artifact, filed where `agrim-research` actually writes it: the MAIN
    checkout's `.sdlc/research/`, never the goal's worktree -- `_plan`'s counterpart, above."""
    p = pathlib.Path(sdlc_dir) / "research" / (name + ".md")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# research\n")
    return p


def test_pr_refuses_when_the_goals_research_dossier_is_absent_from_its_branch(tmp_path):
    """#1801: the research dossier is the artifact a reviewer checks the change's blast-radius
    claims against, and it was reaching PRs never -- 228 files on disk, 0 tracked, repo-wide. No plan
    is filed here, so the PLAN half of the check must stay silent and only the research half fire."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    _research(d)
    run = _runner([("rev-list", "2"), ("ls-files", ""), ("check-ignore", NOT_IGNORED_RESEARCH)])
    out = work.pr(d, ON, goal, run=run)
    assert ".sdlc/research/0001-x.md" in out
    assert "sdlc/0001-x" in out
    assert not any("push" in c for c in run.calls)
    assert not any(c.startswith("gh ") for c in run.calls)
    assert LS_FILES_RESEARCH in run.calls
    assert CHECK_IGNORE_RESEARCH in run.calls


def test_pr_refuses_on_missing_research_even_when_the_plan_is_already_on_the_branch(tmp_path):
    """The two guards are independent, not one shadowing the other: a goal that did right by its
    plan but never brought its research dossier over must still be refused, on the research half
    specifically -- proof `pr()` actually runs both, not just the first one that happens to pass."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    _plan(d)
    _research(d)
    run = _runner([("rev-list", "2"), (LS_FILES, ".sdlc/plans/0001-x.md"),
                   (LS_FILES_RESEARCH, ""), (CHECK_IGNORE_RESEARCH, NOT_IGNORED_RESEARCH)])
    out = work.pr(d, ON, goal, run=run)
    assert ".sdlc/research/0001-x.md" in out
    assert not any("push" in c for c in run.calls)


def test_pr_accepts_a_research_dossier_already_tracked_on_the_branch(tmp_path):
    """`git ls-files` answering is the whole test: the dossier is IN the branch, however it got
    there. Nothing left to demand."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    _research(d)
    run = _runner([("rev-list", "2"), (LS_FILES_RESEARCH, ".sdlc/research/0001-x.md"),
                   ("pulls?head=", "9")])
    assert work.pr(d, ON, goal, run=run) == "PR #9"
    assert LS_FILES_RESEARCH in run.calls
    assert not any("check-ignore" in c for c in run.calls)


def test_pr_does_not_demand_a_research_dossier_this_repo_deliberately_ignores(tmp_path):
    """The #1504 boundary, mechanised, for `research/` too: a repo whose `.gitignore` covers the
    path has already decided it is not a committed artifact here, and the guard must stay silent."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    _research(d)
    run = _runner([("rev-list", "2"), ("ls-files", ""), ("check-ignore", ""), ("pulls?head=", "9")])
    assert work.pr(d, ON, goal, run=run) == "PR #9"
    assert CHECK_IGNORE_RESEARCH in run.calls


def test_pr_says_nothing_when_the_research_ignore_rules_cannot_be_read(tmp_path):
    """FAILS OPEN, mirroring the plan guard: an unreadable `.gitignore` is not an answer about the
    rules, so it must not manufacture a demand the repo may never have agreed to."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    _research(d)
    run = _runner([("rev-list", "2"), ("ls-files", ""), ("check-ignore", UNREADABLE_IGNORES_RESEARCH),
                   ("pulls?head=", "9")])
    assert work.pr(d, ON, goal, run=run) == "PR #9"
    assert CHECK_IGNORE_RESEARCH in run.calls


def test_pr_creates_and_records_the_number(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("rev-list", "2"), ("pulls?head=", ""), ("log -1", "fix: a real subject"),
                   ("log --reverse", "fix: a real subject\n\nsome body\n\x00"),
                   ("pulls -f title=", "12")])
    assert work.pr(d, ON, goal, run=run) == "PR #12"
    assert work._record(d, goal)["pr"] == "12"
    create_call = next(c for c in run.calls if "pulls -f title=" in c)
    assert "--fill" not in create_call
    assert "-f title=fix: a real subject" in create_call
    assert "-f body=" in create_call


def test_pr_reuses_an_existing_pr(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("rev-list", "2"), ("pulls?head=", "9")])
    assert work.pr(d, ON, goal, run=run) == "PR #9"
    assert not any("pulls -f title=" in c for c in run.calls)


def test_pr_never_invokes_a_graphql_routed_gh_subcommand(tmp_path):
    """#1209: `gh pr list`, `gh pr create`, `gh pr view` are all GraphQL operations under the
    hood (confirmed against the installed gh binary: `query PullRequestList`, `mutation
    PullRequestCreate`, `query PullRequestByNumber`), and GitHub meters REST and GraphQL on
    SEPARATE hourly quotas (`gh api rate_limit` returns distinct `core` and `graphql` buckets in
    its `.resources`). An exhausted GraphQL quota must never block PR creation when the REST
    budget could still serve the identical three calls -- so none of those three subcommand
    shapes may appear anywhere in what `pr()` runs, in either the reuse or the fresh-create path."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("rev-list", "2"), ("pulls?head=", ""), ("log -1", "fix: a real subject"),
                   ("log --reverse", "fix: a real subject\n\nsome body\n\x00"),
                   ("pulls -f title=", "12")])
    work.pr(d, ON, goal, run=run)
    banned = ("gh pr list", "gh pr create", "gh pr view")
    offenders = [c for c in run.calls if any(b in c for b in banned)]
    assert offenders == []
    # every gh call this makes is a REST call through `gh api repos/{owner}/{repo}/...`
    gh_calls = [c for c in run.calls if c.startswith("gh ")]
    assert gh_calls, "expected at least one gh call"
    assert all(c.startswith("gh api repos/{owner}/{repo}/") for c in gh_calls)


def test_pr_body_is_never_empty_for_the_default_bodyless_commit(tmp_path):
    """#816: `gh pr create --fill` derives the body from commit messages, and `commit()`'s own
    default (`git commit -m f"sdlc: {stem(goal)}"`, no body) makes that come out EMPTY. Measured
    live: 85/380 sigma PRs (22%), and a comparable double-digit rate on another deployment of
    this plugin — and NOT correlated with an `sdlc:`-titled PR (only a small fraction of that other
    deployment's empty-body PRs were `sdlc:`-titled; most had ordinary conventional-commit titles
    that just happened to carry no body). `pr()` must compose the body itself from the branch's own
    commits, so it can never be empty as long as a commit exists (already guaranteed by the
    no-commits refusal above)."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="816", pr="")
    run = _runner(_default_branch("main") + [        # #1649: `_started`'s base IS `main` here
        ("rev-list", "1"),
        ("pulls?head=", ""),
        ("log -1", "sdlc: 816"),                      # commit()'s own body-less default subject
        ("log --reverse", "sdlc: 816\n\n\n\x00"),      # same commit, no body -- %b renders empty
        ("pulls -f title=", "42"),
    ])
    assert work.pr(d, ON_GITHUB, goal, run=run) == "PR #42"
    create_call = next(c for c in run.calls if "pulls -f title=" in c)
    assert "--fill" not in create_call
    body_arg = create_call.split("-f body=", 1)[1].split(" -f base=", 1)[0].strip()
    assert body_arg != ""
    assert "Closes #816" in body_arg
    assert "sdlc: 816" in body_arg                    # the real (if minimal) commit content


def test_pr_body_has_no_issue_link_for_a_local_goal(tmp_path):
    """`stem(goal)` is the issue number in github mode but a filename stem locally (e.g.
    `0001-x`) -- `Closes #0001-x` would be nonsense, so the link is github-mode only."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")                          # default goal: "0001-x.md"
    run = _runner([("rev-list", "1"), ("pulls?head=", ""), ("log -1", "feat: x"),
                   ("log --reverse", "feat: x\n\n\n\x00"), ("pulls -f title=", "5")])
    work.pr(d, ON_GITHUB, goal, run=run)
    create_call = next(c for c in run.calls if "pulls -f title=" in c)
    assert "Closes #" not in create_call


def test_pr_body_has_no_issue_link_for_a_numeric_local_goal(tmp_path):
    """Independent review of #816, before merge: `ref.isdigit()` ALONE is not enough to detect
    github mode — a LOCAL goal can have a purely numeric filename stem too (`.sdlc/goals/0002.md`,
    `triage.py`'s own illustrative example of a normal local goal id). Without also checking
    `discovery.source == "github"`, this goal would get a false `Closes #0002` injected into its
    PR body — silently closing an unrelated real GitHub issue #2 on a merge this tool runs
    unattended. `ON` here is deliberately NOT github mode (no `discovery` key at all, the plain
    local-goal config every other non-PR-body test in this file already uses)."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="0002.md", pr="")
    run = _runner([("rev-list", "1"), ("pulls?head=", ""), ("log -1", "feat: local numeric goal"),
                   ("log --reverse", "feat: local numeric goal\n\n\n\x00")])
    work.pr(d, ON, goal, run=run)
    create_call = next(c for c in run.calls if "pulls -f title=" in c)
    assert "Closes #" not in create_call


# --- #1649: the PR body must not claim a close the base it targets cannot perform ----------------
#
# GitHub honours a closing keyword ONLY when the pull request carrying it merges into the
# repository's DEFAULT branch. Under the branching model a goal's PR targets `feature/<unit>`, so
# the keyword is inert -- and the body asserted, in writing, that the issue was handled while
# nothing handled it. Measured live: every goal in the 1.4.0 epic, and again in the adoption trial,
# stayed open until a human closed it by hand.

#: A closing keyword GitHub actually acts on, immediately followed by an issue ref. This is the
#: exact shape #1649 is about: not the word "close" appearing in prose, but a `<keyword> #<n>` PAIR
#: -- the only thing GitHub's linker matches, and therefore the only thing that can assert a close.
_CLOSING_KEYWORD = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[\s:]*#\d+", re.I)


def _created_body(run):
    """The body `pr()` actually posted, lifted out of the one REST create call."""
    create_call = next(c for c in run.calls if "pulls -f title=" in c)
    return create_call.split("-f body=", 1)[1].split(" -f base=", 1)[0].strip()


def _pr_run(extra=()):
    """The five reads `pr()` makes on the fresh-create path, plus whatever a test adds in front."""
    return _runner(list(extra) + [
        ("rev-list", "1"), ("pulls?head=", ""), ("log -1", "sdlc: 816"),
        ("log --reverse", "sdlc: 816\n\n\n\x00"), ("pulls -f title=", "42")])


def test_a_default_base_pr_still_writes_the_same_closing_keyword_it_always_did(tmp_path):
    """THE UNCHANGED CASE, pinned byte-for-byte. A goal cut from the default branch is the common
    case today and it works: GitHub closes the issue when the PR merges, so the keyword is a true
    statement and must survive #1649 exactly as it was. Asserted as an equality, not a containment,
    so widening the honest-alternative text below can never quietly leak into this path."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="816", pr="", base="main")
    run = _pr_run(_default_branch("main"))
    assert work.pr(d, ON_GITHUB, goal, run=run) == "PR #42"
    assert _created_body(run) == "Closes #816\n\nsdlc: 816"


def test_a_feature_base_pr_never_claims_a_close_it_cannot_perform(tmp_path):
    """The defect itself. Base `feature/autowatch-dryrun`, default branch `main`: GitHub will not
    close #816 from this merge, so NO closing keyword may appear -- and the body has to say what
    will close it instead, because a body that simply goes quiet leaves the reader with the same
    wrong belief the false line gave them."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="816", pr="", base="feature/autowatch-dryrun")
    run = _pr_run(_default_branch("main"))
    assert work.pr(d, ON_GITHUB, goal, run=run) == "PR #42"
    body = _created_body(run)
    assert _CLOSING_KEYWORD.search(body) is None, body
    assert "Refs #816" in body                       # still linked, just not claimed
    assert "feature/autowatch-dryrun" in body        # names the base that cannot do it
    assert "default branch" in body                  # names why
    assert "Sigma closes the issue itself" in body   # names what will
    assert "sdlc: 816" in body                       # and still carries the commits


def test_an_unreadable_default_branch_never_becomes_permission_to_claim_a_close(tmp_path):
    """The fail direction, and it is chosen rather than incidental. Being wrong toward "it IS the
    default branch" writes the false line and strands the issue open -- the whole defect. Being
    wrong the other way costs one cautious sentence. So an unreadable answer takes the honest
    branch, exactly like a confirmed non-default one."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="816", pr="", base="main")
    run = _pr_run([("default_branch", RuntimeError("HTTP 502: Bad gateway"))])
    assert work.pr(d, ON_GITHUB, goal, run=run) == "PR #42"
    body = _created_body(run)
    assert _CLOSING_KEYWORD.search(body) is None, body
    assert "Refs #816" in body


def test_a_local_goal_gets_no_issue_line_at_all_and_no_read_on_its_behalf(tmp_path):
    """The github-mode gate is checked BEFORE the base is, and both halves matter. A local goal has
    no GitHub issue, so the honest header would be as false as the keyword was -- `Refs #0002` and
    a promise to close "the issue" name a real, unrelated issue #2 on whatever repo the checkout
    points at. It gets NO issue line, exactly as before #1649. And no default-branch read either:
    that is a network call bought for nothing, and a brand-new remote dependency for every adopter
    running no GitHub discovery at all."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="0002.md", pr="", base="feature/x")
    run = _pr_run(_default_branch("main"))
    work.pr(d, ON, goal, run=run)
    assert "#0002" not in _created_body(run)
    assert not any("default_branch" in c for c in run.calls)


def test_the_default_branch_is_read_over_rest_like_every_other_call_pr_makes(tmp_path):
    """#1209's separate-quota rule applies to the new read too: GitHub meters REST and GraphQL on
    different hourly budgets, and `gh repo view` -- the obvious way to ask this -- is GraphQL. An
    exhausted GraphQL quota must not be able to stop a PR body from telling the truth."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="816", pr="", base="feature/x")
    run = _pr_run(_default_branch("main"))
    work.pr(d, ON_GITHUB, goal, run=run)
    read = next(c for c in run.calls if "default_branch" in c)
    assert read == "gh api repos/{owner}/{repo} --jq .default_branch"
    assert not any(c.startswith("gh repo view") for c in run.calls)


# --- gate: clean AND safe ------------------------------------------------------------------------

def test_gate_retries_the_lazy_unknown_then_accepts(tmp_path):
    """GitHub's first answer after a push is normally UNKNOWN. Taking it at face value would park
    every PR; ignoring it would merge blind."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    seen = []

    def view(_line):
        seen.append(1)
        return _view(mergeable="UNKNOWN") if len(seen) < 3 else _view()

    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", view)]), sleep=NOSLEEP)
    assert ok and verdict == "clean and safe" and len(seen) == 3


def test_gate_parks_on_a_persistent_unknown(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _view(mergeable="UNKNOWN"))])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok and "UNKNOWN" in verdict
    assert sum("pr view" in c for c in run.calls) == work.UNKNOWN_ATTEMPTS


def test_gate_parks_on_conflicts(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", _view("CONFLICTING", "DIRTY"))]),
                               sleep=NOSLEEP)
    assert not ok and "conflicts" in verdict


def test_gate_reports_behind_for_the_caller_to_act_on(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", _view(status="BEHIND"))]),
                               sleep=NOSLEEP)
    assert not ok and verdict == work.BEHIND


def test_gate_names_the_failing_check(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    view = _view(status="UNSTABLE", checks=(("lint", "SUCCESS"), ("tests", "FAILURE")))
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", view)]), sleep=NOSLEEP)
    assert not ok and "UNSTABLE" in verdict and "tests" in verdict and "lint" not in verdict


def test_gate_parks_when_review_is_still_required(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", _view(status="BLOCKED"))]),
                               sleep=NOSLEEP)
    assert not ok and "BLOCKED" in verdict


# --- F25: gate() must fail CLOSED, not crash, when `gh pr view` itself raises -------------------


def test_gate_parks_with_a_usable_reason_when_pr_view_keeps_raising(tmp_path):
    """A transient 403/rate-limit from `gh pr view` must never crash `merge()` — it must park with a
    reason, logged, the same way every other gate() verdict does. Unlike merge_rights (fails closed)
    and review_gate (fails open), this read had NO guard at all before F25."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", RuntimeError("gh: HTTP 502 Bad Gateway"))])
    ok, verdict, data = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok
    assert "could not read PR state" in verdict and "502" in verdict
    assert data == {}
    # exhausted every retry attempt before giving up, same budget as the UNKNOWN-persists case
    assert sum("pr view" in c for c in run.calls) == work.UNKNOWN_ATTEMPTS


def test_gate_retries_past_a_transient_pr_view_error_then_succeeds(tmp_path):
    """A raising read is treated the same as a lazy UNKNOWN — worth a retry, not an instant park —
    since GitHub's own transient errors are exactly the kind of blip a second attempt often clears."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    seen = []

    def view(_line):
        seen.append(1)
        if len(seen) == 1:
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        return _view()

    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", view)]), sleep=NOSLEEP)
    assert ok and verdict == "clean and safe" and len(seen) == 2


# --- merge rights: permission is never a preference ----------------------------------------------

ALWAYS = {"work": {"enabled": True, "auto_merge": "always"}}
GUARDED = {"work": {"enabled": True, "auto_merge": "protected"}}


def test_a_fork_pr_is_never_merged(tmp_path):
    """The open-source path: the PR IS the deliverable, and attempting the merge only produces a
    confusing API error."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights(cross=True) + [("pr view", _view())])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out == "PR #7 opened — fork PR — the upstream maintainer merges"
    assert not any("pr merge" in c for c in run.calls)


def test_read_only_access_opens_the_pr_and_stops(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights(perm="READ") + [("pr view", _view())])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 opened —") and "read access" in out
    assert not any("pr merge" in c for c in run.calls)


def test_unknown_rights_fail_closed(tmp_path):
    """If we cannot establish that we may merge, we may not merge."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner([("viewerPermission", RuntimeError("gh: not authenticated"))]
                  + _rights() + [("pr view", _view())])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert "could not determine merge rights" in out
    assert not any("pr merge" in c for c in run.calls)


def test_a_no_rights_outcome_is_not_a_park(tmp_path):
    """It records `done`: the loop did everything it could, and nothing about it wants a human."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights(perm="TRIAGE") + [("pr view", _view())])
    assert not work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP).startswith("PARK:")


# --- done_refusal (#254 -> #232): DONE MEANS MERGED ---------------------------------------------
#
# Owner decision on #232: `record done` requires the PR merged. With `work.enabled` and a PR on
# record, the only pass is a positively confirmed merge from ONE REST read of `pulls/<n>`; open
# (armed or not, under ANY `auto_merge` policy, fork, read-only), closed-unmerged and unreadable all
# refuse, and the refusal names the next step (`record review` / `record parked`). Refusing no longer
# wedges a run: `review` is always available and the merge-reconcile pass finishes the goal later.
# Unchanged: work disabled, or no PR on record -> None without any read.

PULLS_7 = "api repos/{owner}/{repo}/pulls/7"


def _rest_pr(state="open", merged=False):
    """The REST `pulls/<n>` body `pr_landing_state` reads: lowercase `state`, a `merged` bool."""
    return json.dumps({"number": 7, "state": state, "merged": merged,
                       "merged_at": "2026-01-02T00:00:00Z" if merged else None})


def test_done_refusal_is_none_when_work_is_disabled(tmp_path):
    d = _sdlc(tmp_path, {"work": {"enabled": False}})
    run = _runner([])
    assert work.done_refusal(d, {"work": {"enabled": False}}, "0001-x.md", run=run) is None
    assert run.calls == []


def test_done_refusal_is_none_when_no_pr_is_on_record(tmp_path):
    """A docs-only / no-diff goal legitimately never opens a PR -- nothing to confirm merged."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([])
    assert work.done_refusal(d, ON, goal, run=run) is None
    assert run.calls == []


def test_done_refusal_is_none_when_the_pr_is_merged(tmp_path):
    """MERGED is the one pass -- and it still replays the merge receipt (the `merged` ledger kind)."""
    ledger = _load("ledger")
    cfg = {"work": {"enabled": True, "auto_merge": "always"}, "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d)
    ledger.reset_actor_cache()
    run = _runner([(PULLS_7, _merged_pr())])
    assert work.done_refusal(d, cfg, goal, run=run) is None
    entries = [(e["kind"], e.get("pr")) for e in ledger.read_all(d)]
    assert ("merged", "7") in entries


def test_done_refusal_reads_rest_never_graphql(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([(PULLS_7, _rest_pr(merged=True, state="closed"))])
    assert work.done_refusal(d, ON, goal, run=run) is None
    assert run.calls and all("pr view" not in c and "graphql" not in c for c in run.calls), run.calls


@pytest.mark.parametrize("cfg", [ON, ALWAYS, GUARDED], ids=["off", "always", "protected"])
def test_done_refusal_refuses_an_open_pr_under_every_auto_merge_policy(tmp_path, cfg):
    """#232's core: `auto_merge: off` (the shipped default) used to let `done` through here, so the
    issue closed with the PR still open. Now no policy does."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([(PULLS_7, _rest_pr("open"))])
    refusal = work.done_refusal(d, cfg, goal, run=run)
    assert refusal is not None and "PR #7" in refusal and "not merged" in refusal
    assert "record" in refusal and "review" in refusal       # names the next step


def test_done_refusal_refuses_an_armed_but_unlanded_pr(tmp_path):
    """Armed is a promise, not a merge: `record review`, and the reconcile pass records `done` once
    GitHub actually lands it."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([(PULLS_7, json.dumps({"number": 7, "state": "open", "merged": False,
                                         "auto_merge": {"merge_method": "squash"}}))])
    assert "not merged" in work.done_refusal(d, ALWAYS, goal, run=run)


def test_done_refusal_refuses_a_closed_unmerged_pr_and_names_park(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([(PULLS_7, _rest_pr("closed"))])
    refusal = work.done_refusal(d, ALWAYS, goal, run=run)
    assert refusal is not None and "closed without merging" in refusal and "parked" in refusal


def test_done_refusal_fails_closed_when_gh_is_unavailable(tmp_path):
    """Reversed by #232: an unreadable PR is never assumed merged. Safe because the refusal routes to
    `record review` (non-terminal) -- the issue stays open, nothing is wedged."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([(PULLS_7, RuntimeError("gh: command not found"))])
    refusal = work.done_refusal(d, ALWAYS, goal, run=run)
    assert refusal is not None and "could not be confirmed merged" in refusal and "review" in refusal


def test_done_refusal_fails_closed_on_valid_json_that_is_not_an_object(tmp_path):
    """`json.loads` succeeds on `null`, `[]`, `42`, a bare string and `true`; none of them is a merge,
    and none may raise out of the function."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    for shape in ("null", "[]", "42", '"MERGED"', "true"):
        run = _runner([(PULLS_7, shape)])
        assert work.done_refusal(d, ALWAYS, goal, run=run) is not None, shape


def test_done_refusal_falls_back_to_the_project_root_when_the_worktree_is_gone(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    shutil.rmtree(pathlib.Path(d).parent / ".sdlc" / "work" / "0001-x")
    cwds = []
    def run(cwd, argv):
        cwds.append(str(cwd))
        return _rest_pr(merged=True)
    assert work.done_refusal(d, ON, goal, run=run) is None
    assert cwds == [str(work.project_root(d))]


# --- merge: the ordering is the safety -----------------------------------------------------------

#: #312: the merge demands verify evidence only when verify is REQUIRED (`state.verify_required`):
#: enforce on, or a command declared. These evidence-gate tests declare a command, so they pin the
#: gate exactly as before; the no-command case is pinned by the #312 tests at the end of this file.
ALWAYS_VERIFIED = {**ALWAYS, "verify": {"command": "pytest -q"}}


def test_merge_refuses_without_fresh_local_evidence(tmp_path):
    """CI is not the only leg. No verify for THIS run means no merge, whatever GitHub says."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner(_rights() + _protected() + [("api repos/{owner}/{repo}/pulls/7", _merged_pr()),
                                                ("pr view", _view())])
    assert work.merge(d, ALWAYS_VERIFIED, goal, run=run, sleep=NOSLEEP).startswith("PARK: no fresh verify")
    assert not any("pr merge" in c for c in run.calls)


def test_merge_refuses_evidence_from_a_previous_run(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal, age=10_000)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    assert "predates this run" in work.merge(d, ALWAYS_VERIFIED, goal, run=run, sleep=NOSLEEP)


def test_merge_refuses_a_failing_verify(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal, exit_code=1)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    assert "last verify FAILED" in work.merge(d, ALWAYS_VERIFIED, goal, run=run, sleep=NOSLEEP)


def test_merge_lands_directly_when_clean_and_safe(tmp_path):
    """#1212: gate() already said CLEAN -- nothing is pending, so merge() prefers a direct
    `gh pr merge` right now over arming. Arming (`--auto`) is reserved for the one case it exists
    for (a required check that has not answered yet); it would only add a needless async
    round-trip here, and it depends on a repo setting (`allow_auto_merge`) this test deliberately
    never mocks, which proves the direct path doesn't even need to consult it."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci",), reviews=1) + [("pr view", _view())])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    assert "1 required check" in out and "1 required review" in out
    assert "gh pr merge 7 --squash" in run.calls
    assert not any("--auto" in c.split() for c in run.calls)   # exact token: not a `--autostash` collision
    assert not any("allow_auto_merge" in c for c in run.calls)   # not even consulted


def test_merge_deletes_the_remote_branch_after_a_direct_landing(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci",), reviews=1) + [("pr view", _view())])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    assert any(c == "gh api -X DELETE repos/{owner}/{repo}/git/refs/heads/sdlc/0001-x"
              for c in run.calls)


def test_merge_never_attempts_branch_delete_when_the_direct_merge_is_refused(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected()
                  + [("pr view", _view()),
                     ("pr merge", RuntimeError("HTTP 405: Base branch was modified"))])
    work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert not any("DELETE" in c for c in run.calls)


def test_merge_reports_success_even_when_the_branch_delete_fails_from_a_worktree(tmp_path):
    """The exact regression this exists to prevent: `gh pr merge --delete-branch`, run from this
    goal's own worktree, fails loudly with a git checkout error even though the merge already
    landed (project memory: gh-pr-merge-fails-loudly-from-a-worktree). This proves the chosen
    design -- a SEPARATE REST delete call, its own try/except -- can never reproduce that: whatever
    the delete call does, `merge()`'s own success report must be unaffected."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci",), reviews=1)
                  + [("pr view", _view()),
                     ("DELETE", RuntimeError(
                         "failed to run git: fatal: 'main' is already used by worktree"))])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    assert "gh pr merge 7 --squash" in run.calls          # the merge call itself is untouched


def test_merge_never_raises_when_the_record_is_missing_a_branch_name(tmp_path):
    """Code-review finding: the first version of this guarded `rec.get("branch")` in `finish()`
    but left `merge()`'s new call site as an unguarded `rec["branch"]` -- reproduced by execution,
    a record missing `branch` raised `KeyError` right after a REAL merge succeeded, escaping
    uncaught past the very docstring six lines above that says this must never happen. Defensive
    only -- `start()` always sets `branch` (work.py:406) -- but must degrade to "no cleanup
    attempted", never crash a merge that has already landed."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    rec = work._record(d, goal)
    del rec["branch"]
    work._save(d, goal, rec)
    run = _runner(_rights() + _protected(checks=("ci",), reviews=1) + [("pr view", _view())])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    assert not any("DELETE" in c for c in run.calls)


def test_merge_does_not_delete_a_branch_when_only_arming_auto_merge(tmp_path):
    """Arming is not landing -- `merge()` returns immediately, before GitHub's own async merge has
    happened. Deleting the branch here would race a PR that might not even land. (`finish()` is
    what eventually covers this goal once it actually lands.)"""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci", "slow")) + _auto_merge_allowed(True)
                  + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed on PR #7")
    assert not any("DELETE" in c for c in run.calls)


def test_delete_remote_branch_returns_false_on_failure_without_raising(tmp_path):
    run = _runner([("DELETE", RuntimeError("HTTP 404"))])
    assert work._delete_remote_branch("/some/cwd", "sdlc/0001-x", run) is False


def test_delete_remote_branch_returns_true_on_success(tmp_path):
    run = _runner([])
    assert work._delete_remote_branch("/some/cwd", "sdlc/0001-x", run) is True
    assert run.calls == ["gh api -X DELETE repos/{owner}/{repo}/git/refs/heads/sdlc/0001-x"]


# --- #1649: the merge that lands the work is what closes the issue the base could not ------------
#
# The other half of the same defect. Stopping the false `Closes #N` line prevents the lie but
# closes nothing, and the trial measured what that costs: goal #1642 was implemented, PR'd,
# verified, approved and MERGED onto its unit branch -- and stayed OPEN, so its two dependents
# (`Blocked by: #1642`) were held by the dependency gate forever while the loop reported DONE. A
# dependency-ordered backlog, which is the branching model's whole purpose, could not advance.
#
# So the kit closes it at the ONE moment GitHub itself would have: a positively confirmed landing.
# Never on an armed PR (`--auto` only ARMS -- `merge-armed`, not `merged`), never on a PR that was
# merely opened, never on a park.

ALWAYS_GITHUB = {**ALWAYS, "discovery": {"source": "github"}}


def _landed(extra=(), issue_state="open"):
    """A clean, reviewed, permitted PR that `gh pr merge` lands directly -- the one path #1649's
    close hangs off. `issue_state` answers the ONE call #2615's redesigned close clause makes,
    post-landing, to decide -- from GitHub's own observed fact, never from any base, live or
    recorded -- whether its closing keyword already fired. Pass `issue_state=None` to simulate an
    unreadable issue (the read raises), the degrade path's own trigger."""
    handlers = _rights() + list(extra) + [("pr view", _view())]
    if issue_state is None:
        handlers = handlers + [("--jq .state", RuntimeError("HTTP 500: could not read issue"))]
    else:
        handlers = handlers + _issue_state(issue_state)
    return _runner(handlers)


def test_a_landed_merge_closes_the_issue_its_base_could_not(tmp_path):
    """THE FIX, general case, unchanged from #1649 in outcome. `feature/autowatch-dryrun` is not
    the default branch, so GitHub's own closing keyword never fires -- and the issue that a
    merged, verified, reviewed goal leaves open is what holds every dependent behind it. #2615
    changes HOW this is decided: not by asking what base anything is, but by reading the issue's
    own state directly and finding it still open -- and the returned clause says only that (never
    that it was the one that did the closing; see plan-review round 2, finding 1, and the
    dedicated race test below)."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="feature/autowatch-dryrun")
    _evidence(d, goal)
    run = _landed(issue_state="open")
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    closes = [c for c in _issue_calls(run, "1642") if "state=closed" in c]
    assert closes == ["gh api -X PATCH repos/{owner}/{repo}/issues/1642 -f state=closed"], run.calls
    assert "#1642 was still open after the merge" in out
    assert not any("default_branch" in c for c in run.calls)   # the base is never read at all


def test_a_landed_merge_onto_the_default_branch_leaves_the_issue_entirely_to_github(tmp_path):
    """THE UNCHANGED CASE. When the post-merge read finds the issue ALREADY closed -- GitHub's own
    keyword having caught up with the merge by the time this reads it -- the kit must not touch it
    further: one read (was it already closed?), then nothing: not a close, not a note. This is the
    common case today and it works; #2615 changes WHICH read decides this (the issue's own state,
    not the base), never this outcome."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="main")
    _evidence(d, goal)
    run = _landed(issue_state="closed")
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    # The read happens (that's how "already closed" gets confirmed at all) -- only the two WRITES
    # (the PATCH, the audit comment) must be absent.
    writes = [c for c in _issue_calls(run, "1642") if "state=closed" in c or "/comments" in c]
    assert writes == [], run.calls
    assert "#1642 was still open" not in out


def test_the_close_fires_even_when_the_recorded_base_matches_default(tmp_path):
    """#2615, plan-review round 1's finding 2, and plan-review round 2's finding 2 -- this
    docstring corrected from an earlier revision that wrongly asserted the PR body carried
    `Refs #1467`-style text. It does not: the goal's recorded base is `"main"`, which is also what
    `extra=` below makes a live read answer, so `_pr_body` (`work.py:1951-1952`) would have written
    `Closes #2572` for this PR, not `Refs #2572` -- `base == _default_branch(...)` is true either
    way you read it, recorded or live. That is exactly the point: under the OLD logic
    (`base == _default_branch(...)`, whichever base it read) a match ALONE was treated as proof the
    keyword had already closed the issue, and the close was skipped without ever asking GitHub.
    Here the issue is still OPEN when this test's fake answers the post-merge read -- #2615's own
    research dossier (`.sdlc/research/2615.md`, BR-1/BR-7) marks *why* it stayed open, for the real
    #2572, as explicitly UNVERIFIED (an out-of-band retarget before merge is one structurally
    possible cause among others), and this test does not depend on which one it was. What it does
    depend on, and what makes the old logic wrong regardless of cause, is that a base matching the
    default branch is evidence about what GitHub's keyword SHOULD do, never a confirmation that it
    DID -- so the fix reads neither the recorded nor a live base at all (the extra `default_branch`
    handler below is consulted only by the OLD, now-replaced logic, so this test also fails red
    against it -- see "Red-first, verified" above); it asks GitHub whether the issue itself is
    still open, and closes it because the answer is yes."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="2572.md", base="main")      # matches default -- the old logic's trap
    _evidence(d, goal)
    run = _landed(extra=_default_branch("main"),          # what a live read ALSO shows -- unused by the fix
                  issue_state="open")                      # GitHub's own observed fact: still open
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    closes = [c for c in _issue_calls(run, "2572") if "state=closed" in c]
    assert closes == ["gh api -X PATCH repos/{owner}/{repo}/issues/2572 -f state=closed"], run.calls
    assert "#2572 was still open after the merge" in out


def test_a_pr_whose_issue_github_already_closed_gets_no_duplicate_close(tmp_path):
    """Non-vacuity partner to the test above, and the OTHER base-matches-default shape plan-review
    round 1 asked for: here the issue is CONFIRMED closed already when the fix reads it -- GitHub's
    own keyword (or a human) got there first. No PATCH, no comment, no false claim of a close on an
    issue this kit did not close."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="2573.md", base="feature/whatever")
    _evidence(d, goal)
    run = _landed(issue_state="closed")                  # GitHub's own observed fact: already closed
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    # The read happens; only the two WRITES (the PATCH, the audit comment) must be absent -- a
    # duplicate PATCH on an already-closed issue would be a no-op, but this asserts it is never
    # even attempted, per the "touch nothing" contract "already closed" is meant to keep.
    writes = [c for c in _issue_calls(run, "2573") if "state=closed" in c or "/comments" in c]
    assert writes == [], run.calls
    assert "#2573 was still open" not in out


def test_an_unconfirmed_issue_state_never_silently_trusts_that_its_already_closed(tmp_path):
    """#2615's own safety property: when the post-merge read of the issue's OWN state genuinely
    cannot be confirmed -- the read itself raises, the same shape a `gh` outage or a rate limit
    would produce -- the fix must not treat "unreadable" as "must already be closed". It closes
    defensively (never leaves a landed goal's issue open on a guess -- and a PATCH `state=closed`
    on an issue that may already be closed is a harmless no-op, never a duplicate anything) and
    says so plainly, naming no base and no state it never actually confirmed -- in BOTH the
    returned clause AND the audit comment it posts. The comment check matters on its own: a
    mutant that forces `note = _CLOSED_BY_MERGE` unconditionally (dropping the `confirmed_open`
    branch that picks `_CLOSED_BY_MERGE_UNCONFIRMED` instead) passes the whole suite without it --
    nothing before this read the posted comment body, only the returned clause, which that mutant
    never touches."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="2574.md", base="main")     # irrelevant to this call path either way
    _evidence(d, goal)
    run = _landed(issue_state=None)                      # the state read raises
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    closes = [c for c in _issue_calls(run, "2574") if "state=closed" in c]
    assert closes == ["gh api -X PATCH repos/{owner}/{repo}/issues/2574 -f state=closed"], run.calls
    clause = out.rsplit(" — ", 1)[1]                      # just the close clause, not the whole line
    assert "could not be confirmed" in clause
    assert "was still open" not in clause                 # a state nobody observed -- never claim it
    assert "main" not in clause                          # the clause never names a base it never read
    note = next(c for c in run.calls if "/issues/2574/comments" in c)
    assert "could not be re-read" in note                 # _CLOSED_BY_MERGE_UNCONFIRMED's own wording
    assert "was still open" not in note                   # never _CLOSED_BY_MERGE's wording either


def test_an_unreadable_state_whose_close_also_fails_never_claims_it_was_still_open(tmp_path):
    """#2615 fold-in (review round 2, coverage finding): the state read is unreadable (the same
    shape as the test above) AND the PATCH close request itself also raises -- the one branch that
    reaches `detail = "and its state could not be re-read either"` (work.py, the `except` arm of
    `_close_issue_the_base_cannot`'s close attempt). A mutant that forces
    `detail = "and it was still open"` unconditionally there passes every other test in this file,
    because none of them puts an unreadable state on the SAME path as a failing close -- this one
    does, and asserts the wording actually produced, never the unconfirmed state's wrong claim."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="2575.md", base="main")      # irrelevant to this call path either way
    _evidence(d, goal)
    run = _landed(extra=[("-f state=closed", RuntimeError("HTTP 500: could not close"))],
                  issue_state=None)                       # both the read AND the close attempt fail
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    attempts = [c for c in _issue_calls(run, "2575") if "state=closed" in c]
    assert attempts == ["gh api -X PATCH repos/{owner}/{repo}/issues/2575 -f state=closed"], run.calls
    clause = out.rsplit(" — ", 1)[1]                      # just the close clause, not the whole line
    assert "could not be re-read either" in clause
    assert "was still open" not in clause                 # a state nobody observed -- never claim it


def test_a_close_request_never_claims_credit_it_cannot_prove(tmp_path):
    """Plan-review round 2, finding 1. Between the GET this fix makes and the PATCH two lines
    below it in production code, GitHub's own keyword processing or a person can still close the
    issue first -- there is no third read that could catch it, and `-f state=closed` succeeds
    unconditionally either way (the real, externally-unobservable idempotency `gh api -X PATCH`
    has on an issue that may already be closed). So the fix must never say IT did the closing, only
    what it observed and did: the issue was open, and a close request against it succeeded. This
    test models the race directly with `_issue_state_raced_closed` -- the fake's GET handler
    answers `open` and, in that same call, flips a private flag as if something else had already
    closed the issue by the time the PATCH runs a moment later -- and asserts neither the merge
    line nor the audit comment crosses into a claim the race can make false: no "Closed by
    Sigma", no "did not apply", nothing this call cannot prove. (A mutant restoring either
    phrase was confirmed red against this test -- see "Red-first, verified" above.)"""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="feature/autowatch-dryrun")
    _evidence(d, goal)
    handlers, raced = _issue_state_raced_closed("open")
    run = _runner(_rights() + [("pr view", _view())] + handlers)
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert raced["closed"] is True                        # the modelled race did happen
    assert out.startswith("PR #7 merged")
    closes = [c for c in _issue_calls(run, "1642") if "state=closed" in c]
    assert closes == ["gh api -X PATCH repos/{owner}/{repo}/issues/1642 -f state=closed"], run.calls
    note = next(c for c in run.calls if "/issues/1642/comments" in c)
    for banned in ("Closed by Sigma", "did not apply", "evidently"):
        assert banned not in out, out
        assert banned not in note, note
    assert "was still open" in out and "was still open" in note      # still reported as closed/open


def test_an_armed_pr_never_closes_its_issue(tmp_path):
    """ARMING IS NOT LANDING. `--auto` enables GitHub's auto-merge and returns immediately; a check
    that later fails, or an auto-merge somebody cancels, means the PR never lands at all. The kit
    already draws exactly this line for its own record -- `merge-armed` here, `merged` only after a
    direct landing -- and the close has to sit on the same side of it, or a goal whose work never
    landed gets closed anyway."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="feature/autowatch-dryrun")
    _evidence(d, goal)
    run = _runner(_rights() + _default_branch("main") + _auto_merge_allowed(True)
                  + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed on PR #7")
    assert _issue_calls(run, "1642") == [], run.calls


def test_a_refused_direct_merge_never_closes_its_issue(tmp_path):
    """The merge GitHub itself said no to. Nothing landed, so nothing may be closed -- and the park
    must still be the park it always was."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="feature/autowatch-dryrun")
    _evidence(d, goal)
    run = _landed(extra=[("pr merge", RuntimeError("HTTP 405: Base branch was modified"))])
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK:")
    assert _issue_calls(run, "1642") == [], run.calls


def test_a_park_before_the_merge_never_closes_its_issue(tmp_path):
    """No fresh verify evidence: `merge()` returns before it ever reaches a merge attempt. The
    close lives past every one of those gates, not beside them."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="feature/autowatch-dryrun")
    run = _landed()
    out = work.merge(d, {**ALWAYS_GITHUB, "verify": {"command": "pytest -q"}}, goal, run=run,
                     sleep=NOSLEEP)
    assert out.startswith("PARK:")
    assert _issue_calls(run, "1642") == [], run.calls


def test_a_pr_that_could_only_be_opened_never_closes_its_issue(tmp_path):
    """The open-source path: a fork PR is the deliverable and the upstream maintainer merges it.
    `record done` is still the right outcome there (SKILL.md), but nothing landed on OUR side, so
    the kit has no confirmed merge to close anything from."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="feature/autowatch-dryrun")
    _evidence(d, goal)
    run = _runner(_rights(cross=True) + _default_branch("main") + [("pr view", _view())])
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 opened —")
    assert _issue_calls(run, "1642") == [], run.calls


def test_a_local_goal_never_closes_a_github_issue_when_its_merge_lands(tmp_path):
    """`_pr_body`'s own trap, on the write side where it costs the most. A LOCAL goal can have a
    purely numeric stem (`.sdlc/goals/0002.md`), so `isdigit()` alone would make a landed local
    merge CLOSE a stranger's real issue #2 -- unattended, with no PR body anywhere to show for it.
    The github-mode gate is the same one the body uses, so the two can never disagree."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="0002.md", base="feature/x")
    _evidence(d, goal)
    run = _landed()
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)          # NOT github mode
    assert out.startswith("PR #7 merged")
    assert _issue_calls(run, "0002") == [], run.calls
    assert not any("--jq .state" in c for c in run.calls)   # the issue-state probe is never even made


def test_the_close_goes_to_the_repo_the_goal_number_came_from(tmp_path):
    """The issue number came from the DISCOVERY source, so the close has to go back to that same
    repo -- `_declared_unit` already reasons this out for the read side (a fork configured to file
    issues elsewhere has a different issue #N), and closing #1642 of whatever repo the checkout
    happens to point at is exactly the kind of wrong that never announces itself. #2615 added a
    SECOND read against that same repo (the post-merge `--jq .state` probe, before the PATCH this
    test already pinned) -- both must target `acme/board`, not the checkout's own `{owner}/{repo}`,
    or this fix would silently probe/close a same-numbered issue in the wrong repo."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="feature/autowatch-dryrun")
    _evidence(d, goal)
    cfg = {**ALWAYS, "discovery": {"source": "github", "github": {"repo": "acme/board"}}}
    run = _landed()
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    assert any(c.startswith("gh api repos/acme/board/issues/1642") and "--jq .state" in c
               for c in run.calls), run.calls
    assert any(c.startswith("gh api -X PATCH repos/acme/board/issues/1642") for c in run.calls), run.calls


def test_the_issue_repo_is_the_slug_the_declaration_read_already_uses(tmp_path):
    """One definition, two callers. `_declared_unit` resolves the issue's repo to open the goal;
    this resolves it to close the goal. A second spelling of the same question is how a fork ends
    up reading one repo's issue and closing another's."""
    assert work._issue_repo({}) == "{owner}/{repo}"
    assert work._issue_repo({"discovery": "nonsense"}) == "{owner}/{repo}"
    assert work._issue_repo({"discovery": {"github": "nonsense"}}) == "{owner}/{repo}"
    assert work._issue_repo({"discovery": {"github": {"repo": ""}}}) == "{owner}/{repo}"
    assert work._issue_repo({"discovery": {"github": {"repo": "acme/board"}}}) == "acme/board"


def test_a_close_that_fails_never_turns_a_landed_merge_into_a_failure(tmp_path):
    """Fail-open, like every other post-landing courtesy here (`_delete_remote_branch`, the ledger
    write). The merge HAPPENED; reporting it as anything else would send the caller to `record
    parked` for work that is on the branch. But it must not go quiet, and -- #2615's plan-review
    round 3, finding 1 -- it must not claim the issue is stranded either: `record done`'s own
    `source.complete()` retries this exact close whenever the issue is still open
    (`test_complete_still_uses_combined_close_when_issue_open`, `tests/test_sources.py`), and
    `_record` parks the goal, never silently drops the local record, if that retry fails too
    (#1201, `tests/test_loop.py`'s suite from
    `test_record_done_survives_a_raising_source_complete_and_still_persists_the_local_record`
    onward). So the returned clause has to name THAT recovery path, not claim nothing else will."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="feature/autowatch-dryrun")
    _evidence(d, goal)
    run = _landed(extra=[("issues/1642 -f state=closed", RuntimeError("HTTP 403: Forbidden"))])
    out = work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    assert "#1642" in out and "403" in out
    assert "record done" in out                  # names the retry path
    assert "nothing else will" not in out         # #2615 round 3: that claim was false -- it retries


def test_the_note_is_only_left_once_the_close_is_confirmed(tmp_path):
    """Report what was confirmed, not what was attempted -- `finish()`'s own rule for its branch
    deletes. A comment saying "closed by Sigma" on an issue Sigma failed to close is the
    same species of false machine-written assertion #1649 is about."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="feature/autowatch-dryrun")
    _evidence(d, goal)
    run = _landed(extra=[("issues/1642 -f state=closed", RuntimeError("HTTP 403: Forbidden"))])
    work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    assert not any("/issues/1642/comments" in c for c in run.calls), run.calls


def test_a_confirmed_close_leaves_a_note_saying_what_did_it(tmp_path):
    """`sources.complete()` posts its own "Completed by the Sigma SDLC loop." later -- but only
    if `record done` runs. This close happens whether it does or not, so it carries its own audit
    line, and that line must not itself read as a closing directive. #2615: the line no longer
    names a base (it was never a fact about a base to begin with) -- it says the issue was
    observed still open, and that a close request against it succeeded -- never that the request
    is what closed it (plan-review round 2, finding 1; `test_a_close_request_never_claims_credit_
    it_cannot_prove` above covers the race that makes that distinction load-bearing)."""
    d = _sdlc(tmp_path)
    goal = _started(d, goal="1642.md", base="feature/autowatch-dryrun")
    _evidence(d, goal)
    run = _landed(issue_state="open")
    work.merge(d, ALWAYS_GITHUB, goal, run=run, sleep=NOSLEEP)
    note = next(c for c in run.calls if "/issues/1642/comments" in c)
    assert "was still open" in note
    assert "feature/autowatch-dryrun" not in note        # the base is never named; it was never read
    assert "Closed by Sigma" not in note and "did not apply" not in note
    assert _CLOSING_KEYWORD.search(note) is None, note


def test_merge_lands_directly_on_a_repo_that_disallows_auto_merge(tmp_path):
    """#1212, the core fix. Before: `work.py merge` had exactly one merge call and it always
    passed `--auto`, which only ARMS GitHub's auto-merge -- a repo setting a maintainer must
    enable. On a repo with `allow_auto_merge: false`, `gh` refused that call, `_run` raised, and
    the unattended loop could never land the PR at all, under any `work.auto_merge` policy. A
    direct `gh pr merge <n> --<method>` needs no such repo setting, and a clean-and-safe PR is
    unconditionally mergeable that way -- so this must now land it and report a terminal success,
    even though the repo explicitly disallows auto-merge (`_auto_merge_allowed(False)` is set on
    purpose, even though the direct path above proves it's never consulted for a clean PR)."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected() + _auto_merge_allowed(False) + [("pr view", _view())])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    assert "gh pr merge 7 --squash" in run.calls
    assert not any("--auto" in c.split() for c in run.calls)   # exact token: not a `--autostash` collision


def test_merge_parks_when_a_direct_merge_is_refused(tmp_path):
    """A direct merge that GitHub genuinely refuses (a real reason -- conflict, a required check
    that flipped between gate()'s read and now, insufficient permission) must PARK with that
    reason on stdout, never raise past merge() and exit 1 with empty stdout -- the exact symptom
    that made the original `--auto`-only bug unrecoverable for an unattended loop."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected()
                  + [("pr view", _view()),
                     ("pr merge", RuntimeError("HTTP 405: Base branch was modified"))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK:")
    assert "405" in out and "Base branch was modified" in out


def test_merge_protected_policy_still_arms_on_pending_checks_when_auto_merge_is_allowed(tmp_path):
    """Acceptance criterion: `auto_merge: protected` against an actually-protected base, with
    required checks still pending and the repo allowing auto-merge, is UNCHANGED from before
    #1212 -- this is the one case arming still exists for."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci", "slow")) + _auto_merge_allowed(True)
                  + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed on PR #7")
    assert "gh pr merge 7 --auto --squash" in run.calls


def test_merge_parks_when_pending_checks_and_the_repo_disallows_auto_merge(tmp_path):
    """Pending checks with nothing available to arm: `--auto` can't be armed (the repo setting
    says no), and a direct merge attempted right now is refused too (GitHub won't merge past a
    required check that hasn't answered yet) -- PARK naming that refusal, never a silent no-op
    and never an uncaught raise."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci", "slow")) + _auto_merge_allowed(False)
                  + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", ""))),
                     ("pr merge", RuntimeError('HTTP 405: Required status check "slow" is expected'))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK:")
    assert not any("--auto" in c.split() for c in run.calls)   # exact token: not a `--autostash` collision
    assert "slow" in out


def test_merge_parks_when_arming_itself_is_refused_despite_allow_auto_merge(tmp_path):
    """A repo can ALLOW auto-merge yet still refuse this specific arm call (a permission edge
    case, a race). Wrapping the arm attempt the same way as the direct one guarantees this path
    also parks with a named reason instead of raising past merge()."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci", "slow")) + _auto_merge_allowed(True)
                  + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", ""))),
                     ("pr merge", RuntimeError("HTTP 422: auto-merge is not allowed for this repository"))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK:")
    assert "422" in out


def test_auto_merge_allowed_fails_closed_when_the_read_raises():
    """PR #1231 review finding #2: `_auto_merge_allowed()`'s own fail-closed except branch
    (work.py:923-926) had zero direct coverage -- every test above drives it only through the
    `_auto_merge_allowed(True/False)` substring-match helper, which always hands back a clean
    'true'/'false' string and never makes the underlying `run(...)` call raise. This calls the real
    function directly with a `run` that raises (no `gh`, no network, an expired token -- anything),
    so the assertion errors if that except branch is ever deleted or replaced with a re-raise: an
    unreadable answer must fail CLOSED to False, per the function's own docstring, never propagate
    and crash `merge()` outright."""
    rec = {"worktree": "/tmp/does-not-matter"}

    def run(cwd, argv):
        raise RuntimeError("gh: command not found")
    assert work._auto_merge_allowed(rec, run) is False


def test_merge_arms_auto_merge_when_required_checks_are_still_pending_past_the_budget(tmp_path):
    """#254 task 3, the core fix: not arming here is EXACTLY what stranded #144/PR #252's 605 lines
    behind a board that read done. Checks still pending (not failing) after gate()'s own patience
    budget are not a reason to leave a mergeable, review-clean PR unarmed -- `--auto` is precisely
    the mechanism for this: GitHub re-checks atomically, and unboundedly, at its OWN merge time,
    rather than record-time racing a CI run gate()'s fixed budget might lose."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci", "slow")) + _auto_merge_allowed(True)
                  + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed on PR #7")
    assert "gh pr merge 7 --auto --squash" in run.calls
    # proves merge() relies on gate()'s existing budget rather than adding a second one of its own.
    # Filtered on "mergeStateStatus" (gate()'s own --json field list) so merge_rights()'s SEPARATE,
    # single `gh pr view --json isCrossRepository` call (also a "pr view" substring) isn't counted.
    assert sum("mergeStateStatus" in c for c in run.calls) == work.PENDING_ATTEMPTS + 1


def test_merge_parks_not_arms_on_a_genuinely_failing_required_check(tmp_path):
    """A red check never arms; absent a readable Actions log its repair path fails closed."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + [("pr view", _mixed(("ci", "SUCCESS"), ("tests", "FAILURE"),
                                                   status="UNSTABLE"))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: failing required check tests has no readable Actions log")
    assert not any("pr merge" in c for c in run.calls)


def test_merge_logs_a_merge_armed_entry_to_the_ledger(tmp_path):
    """F26/#344, narrowed by #1212 to the one case merge() still arms: a required check that has
    not answered yet. `gh pr merge --auto` only ARMS auto-merge — it does not confirm the PR
    landed, so the ledger entry for this moment must never claim `merged` (a false "landed" claim
    TEAM.md would show as fact). It must log the honest `merge-armed` kind instead."""
    ledger = _load("ledger")
    cfg = {"work": {"enabled": True, "auto_merge": "always"},
           "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d)
    _evidence(d, goal)
    ledger.reset_actor_cache()
    run = _runner(_rights() + _protected(checks=("ci", "slow")) + _auto_merge_allowed(True)
                  + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    assert work.merge(d, cfg, goal, run=run, sleep=NOSLEEP).startswith("auto-merge armed on PR #7")
    entries = [(e["kind"], e.get("pr")) for e in ledger.read_all(d)]
    assert ("merge-armed", "7") in entries     # the arm lands on the team ledger, not only `done`
    assert ("merged", "7") not in entries      # and it must never be misrecorded as an actual landing


def test_merge_logs_a_merged_entry_to_the_ledger_on_a_direct_landing(tmp_path):
    """#1212's own honest write site: a direct `gh pr merge` that SUCCEEDS is a real landing, not
    an arm. The ledger's long-declared `merged` kind (previously only ever written out-of-band by
    `done_refusal`, per F26/#344's own docstring: "the first HONEST write site `merged` has ever
    had... nothing has replaced it since") now also gets written from the place that actually
    performed the merge."""
    ledger = _load("ledger")
    cfg = {"work": {"enabled": True, "auto_merge": "always"},
           "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d)
    _evidence(d, goal)
    ledger.reset_actor_cache()
    run = _runner(_rights() + _protected() + [("api repos/{owner}/{repo}/pulls/7", _merged_pr()),
                                                ("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    entries = [(e["kind"], e.get("pr")) for e in ledger.read_all(d)]
    assert ("merged", "7") in entries
    assert ("merge-armed", "7") not in entries


def test_known_direct_merge_emits_core_facts_when_receipt_sharing_is_disabled(tmp_path):
    ledger = _load("ledger")
    cfg = {**ALWAYS, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("api repos/{owner}/{repo}/pulls/7", _merged_pr()),
                                                ("pr view", _view())])
    assert work.merge(d, cfg, goal, run=run, sleep=NOSLEEP).startswith("PR #7 merged")
    entries = [event for event in ledger.read_all(d) if event["kind"] == "merged"]
    events = [event for event in journal_events(ledger, d) if event["kind"] == "merge_observed"]
    assert len(entries) == 1 and len(events) == 1 and events[0]["merge_sha"] == "a" * 40


def test_confirmed_merge_recovers_the_journal_after_the_entry_sink_succeeds(tmp_path, monkeypatch):
    """A crash or local journal failure after entries/ must retry only the unfinished sink."""
    cfg = {**ALWAYS, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    raw = json.dumps({"number": 7, "node_id": "PR_7", "created_at": "2026-01-01T00:00:00Z",
                      "merged_at": "2026-01-02T00:00:00Z", "merge_commit_sha": "a" * 40,
                      "head": {"ref": "sdlc/2577"},
                      "base": {"ref": "main", "repo": {"node_id": "R_1"}}})
    run = _runner([("api repos/{owner}/{repo}/pulls/7", raw)])
    real_append = work.ledger.safe_append
    monkeypatch.setattr(work.ledger, "safe_append", lambda *args, **kwargs:
                        None if args[1] == "merge_observed" else real_append(*args, **kwargs))
    work._record_confirmed_merge(d, cfg, goal, work._record(d, goal), run, "merged")
    delivery = next((pathlib.Path(d) / "state" / "merge-deliveries").glob("*.json"))
    assert json.loads(delivery.read_text())["entry_delivered"] is True
    assert json.loads(delivery.read_text())["journal_delivered"] is False
    monkeypatch.setattr(work.ledger, "safe_append", real_append)
    work._record_confirmed_merge(d, cfg, goal, work._record(d, goal), run, "merged")
    assert json.loads(delivery.read_text())["journal_delivered"] is True
    assert len([event for event in journal_events(_load("ledger"), d)
                if event.get("kind") == "merge_observed"]) == 1


def test_rollout_census_fails_when_the_real_merge_entry_writer_is_disabled(tmp_path, monkeypatch):
    """Deliberate control: a landing without its entry cannot satisfy the documented census."""
    cfg = {**ALWAYS, **JOURNAL_ON, "journal": {"enabled": True}}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    raw = json.dumps({"number": 7, "node_id": "PR_7", "created_at": "2026-01-01T00:00:00Z",
                      "merged_at": "2026-01-02T00:00:00Z", "merge_commit_sha": "a" * 40,
                      "head": {"ref": "sdlc/2577"},
                      "base": {"ref": "main", "repo": {"node_id": "R_1"}}})
    run = _runner([("api repos/{owner}/{repo}/pulls/7", raw)])
    real_append = work.ledger.safe_append
    monkeypatch.setattr(work.ledger, "safe_append", lambda *args, **kwargs:
                        None if args[1] == "merged" else real_append(*args, **kwargs))
    work._record_confirmed_merge(d, cfg, goal, work._record(d, goal), run, "merged")
    assert not [entry for entry in _load("ledger").read_all(d) if entry.get("kind") == "merged"]

    observation = _load("merge_observation")
    pr = {"repository": "R_1", "node_id": "PR_7", "number": 7, "head_ref": "sdlc/2577",
          "head_sha": "c" * 40, "merge_sha": "a" * 40, "merged_at": "2026-01-02T00:00:00Z"}
    facts = work._receipt_parent_facts(json.loads(raw), goal, "goal", goal, "work.pr")
    ownership = observation.ownership_key(facts)
    snapshot = tmp_path / "pinned"
    parent = snapshot / "receipts" / "v1" / ownership
    parent.mkdir(parents=True)
    parent_data = json.loads(observation.parent_receipt(facts))
    (parent / "parent.json").write_bytes(observation.parent_receipt(facts))
    child = parent / "merges" / (pr["merge_sha"] + ".json"); child.parent.mkdir()
    child.write_bytes(observation.child_receipt(parent_data, {"merge_sha": pr["merge_sha"],
                                                               "github_merged_at": pr["merged_at"]}))
    (snapshot / "entries").mkdir(); (snapshot / "entries" / "writer.jsonl").write_text("")
    # Supply the other required measurement, so the missing merged entry is the only failed fact.
    _load("ledger").safe_append(d, "review_posted", goal, config=cfg, stream=_load("ledger").EVENTS,
                                 observation_key="d" * 64, brief_hash="e" * 64,
                                 evidence_id="census_control_0001", comment_id=1, pr=7,
                                 head_sha=pr["head_sha"], verdict="approve")
    scan = observation.coverage_scan(snapshot, [pr], require_measurements=True, journal_root=tmp_path)
    assert scan["counts"]["owned-unobserved-pending"] == 1


def test_merge_then_done_refusal_writes_merged_only_once(tmp_path):
    """Composed-sequence regression (PR #1231 review): loop.py's `record done` dispatch calls
    `work.done_refusal()` immediately after a successful `merge()` (loop.py:1720-1721, whenever
    `work.enabled(config)`) -- exactly the sequence SKILL.md documents as the intended next step.
    A synchronous `gh pr merge` (no `--auto`) means the very next live `gh pr view` read
    `done_refusal()` makes deterministically reports `state=MERGED` -- so its own pre-existing
    MERGED branch (work.py:603-604) must not re-write a second `merged` entry for the same PR that
    `merge()`'s own direct-landing write (work.py:1023) already recorded moments earlier."""
    ledger = _load("ledger")
    cfg = {"work": {"enabled": True, "auto_merge": "always"},
           "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d)
    _evidence(d, goal)
    ledger.reset_actor_cache()
    run = _runner(_rights() + _protected() + [("api repos/{owner}/{repo}/pulls/7", _merged_pr()),
                                                ("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")

    # loop.py's `record done` dispatch, right after -- a synchronous `gh pr merge` guarantees the
    # very next live read sees state=MERGED.
    run2 = _runner([("state,autoMergeRequest", _pr_state(state="MERGED")),
                    ("api repos/{owner}/{repo}/pulls/7", _merged_pr())])
    assert work.done_refusal(d, cfg, goal, run=run2) is None

    entries = [(e["kind"], e.get("pr")) for e in ledger.read_all(d)]
    merged_count = sum(1 for kind, pr in entries if kind == "merged" and pr == "7")
    assert merged_count == 1, f"expected exactly one merged ledger entry, got {merged_count}: {entries}"


def test_merge_leaves_the_pr_alone_when_auto_merge_is_off(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    out = work.merge(d, ON, goal, run=run, sleep=NOSLEEP)
    assert "auto_merge is off" in out and not any("pr merge" in c for c in run.calls)
    assert not any("protection" in c for c in run.calls)      # off short-circuits before the API call


def test_merge_leaves_the_pr_alone_when_auto_merge_is_off_even_with_pending_checks(tmp_path):
    """#254 task 3 / plan-review MODERATE fix: the pending-exhausted arm path must still respect
    `auto_merge: off` -- it changes WHETHER merge() treats a pending-exhausted verdict as arm-worthy
    at all, never WHETHER policy allows arming. Also pins that the message is honest (mentions the
    real, still-pending state) rather than a stale, now-false "clean and safe" claim."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    out = work.merge(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not any("pr merge" in c for c in run.calls)
    assert "auto_merge is off" in out and "pending" in out.lower()
    assert not any("allow_auto_merge" in c for c in run.calls)   # off never even asks


def test_merge_rebases_a_behind_branch_then_merges(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    seen = []

    def view(_line):
        seen.append(1)
        return _view(status="BEHIND") if len(seen) == 1 else _view()

    run = _runner(_rights() + _protected() + [("pr view", view)])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    assert any("rebase --autostash origin/main" in c for c in run.calls)
    assert any("push --force-with-lease" in c for c in run.calls)


def test_a_conflicting_rebase_aborts_and_parks(tmp_path):
    """The 3am case. A half-applied rebase would poison every later goal in the run."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + [("pr view", _view(status="BEHIND")),
                               ("rebase --autostash", RuntimeError("CONFLICT in a.py"))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: rebase deferred") and "CONFLICT" in out
    assert "git rebase --abort" in run.calls
    assert not any("pr merge" in c for c in run.calls)


def test_rebase_detects_an_autostash_pop_conflict_even_though_the_command_itself_exits_0(tmp_path):
    """#1890 plan-review finding, reproduced against real git before this fix existed: `git rebase
    --autostash <ref>` can leave the pop's own conflict unresolved (`UU` files, literal `<<<<<<<`
    markers) while still exiting 0 -- "successfully rebased" describes the replayed commits, not
    the stash. `rebase()` must not trust a non-raising call as success by itself."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("rev-parse HEAD", "deadbee0" * 5),
                   ("rebase --autostash", ""),                       # does NOT raise
                   ("diff --name-only --diff-filter=U", "file.txt"), # but leaves an unmerged path
                   ("stash list", "stash@{0}: autostash")])
    out = work.rebase(d, ON, goal, run=run)
    assert out.startswith("rebase deferred:") and "autostash" in out
    assert "git reset --hard deadbee0deadbee0deadbee0deadbee0deadbee0" in run.calls
    assert "git stash pop" in run.calls
    # The one assertion that would have caught the original bug: the broken tree must never reach
    # the remote.
    assert not any("push --force-with-lease" in c for c in run.calls)


def test_rebase_does_not_reset_or_pop_when_the_autostash_applies_cleanly(tmp_path):
    """Negative control for the check above -- the new post-rebase inspection must not fire (and
    must not touch `reset`/`stash`) on the ordinary successful path."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("rev-parse HEAD", "deadbee0" * 5),
                   ("rebase --autostash", ""),
                   ("diff --name-only --diff-filter=U", "")])   # nothing unmerged
    out = work.rebase(d, ON, goal, run=run)
    assert out == "rebased"
    assert any("push --force-with-lease" in c for c in run.calls)
    assert not any(c.startswith("git reset --hard") for c in run.calls)
    assert not any("stash pop" in c for c in run.calls)


def test_rebase_survives_a_raise_from_the_conflict_probe_itself(tmp_path):
    """#1899: the docstring's own "Any failure ABORTS" promise previously held only because three
    EXTERNAL callers (`ensure_fresh`, `feature_rebase.py`, `main()`'s dispatch wrapper) each
    happened to wrap this call in their own try/except -- a raise from the post-rebase conflict
    probe itself (`git diff --name-only --diff-filter=U`) escaped `rebase()` uncaught. Hardened
    directly: the probe is now its own try/except, turning a raise into the same descriptive
    "rebase deferred: ..." string the pre-existing (pre-#1890) conflict shape already returns,
    never letting it propagate."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("rev-parse HEAD", "deadbee0" * 5),
                   ("rebase --autostash", ""),                      # does NOT raise
                   ("diff --name-only --diff-filter=U", RuntimeError("disk read error"))])
    out = work.rebase(d, ON, goal, run=run)
    assert out.startswith("rebase deferred:") and "disk read error" in out
    assert not any("push --force-with-lease" in c for c in run.calls)
    assert not any(c.startswith("git reset --hard") for c in run.calls)


def test_rebase_survives_a_raise_during_autostash_pop_conflict_cleanup(tmp_path):
    """#1899: same hardening, the OTHER half of the cleanup block -- the conflict probe correctly
    finds an unmerged path, but the reset/stash-pop cleanup that follows it then itself raises
    (e.g. a disk-full `git reset --hard`). Must still return a "rebase deferred: ..." string, never
    propagate -- and must still never push the broken tree to the remote."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("rev-parse HEAD", "deadbee0" * 5),
                   ("rebase --autostash", ""),
                   ("diff --name-only --diff-filter=U", "file.txt"),   # a real unmerged path
                   ("reset --hard", RuntimeError("disk full"))])
    out = work.rebase(d, ON, goal, run=run)
    assert out.startswith("rebase deferred:") and "autostash" in out and "disk full" in out
    assert not any("push --force-with-lease" in c for c in run.calls)


# --- #1890: ensure_fresh -- the pre-verify freshness gate ---------------------------------------


def test_ensure_fresh_returns_none_when_the_goal_never_started(tmp_path):
    d = _sdlc(tmp_path)
    assert work.ensure_fresh(d, ON, "0001-x.md") is None


def test_ensure_fresh_returns_none_when_the_worktree_directory_is_gone(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    pathlib.Path(work._record(d, goal)["worktree"]).rmdir()
    assert work.ensure_fresh(d, ON, goal) is None


def test_ensure_fresh_fails_open_when_the_remote_is_unreachable(tmp_path, capsys):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("fetch", RuntimeError("could not resolve host"))])
    assert work.ensure_fresh(d, ON, goal, run=run) is None
    err = capsys.readouterr().err
    assert "0001-x" in err and "could not resolve host" in err


def test_ensure_fresh_does_nothing_when_not_behind(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("rev-list --count", "0")])
    assert work.ensure_fresh(d, ON, goal, run=run) is None
    assert not any("rebase --autostash" in c for c in run.calls)


def test_ensure_fresh_auto_rebases_when_behind_and_says_why(tmp_path, capsys):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("rev-parse HEAD", "deadbee0" * 5),
                   ("rev-list --count", "3"),
                   ("rebase --autostash", ""),
                   ("diff --name-only --diff-filter=U", "")])
    assert work.ensure_fresh(d, ON, goal, run=run) is None
    assert any("rebase --autostash" in c for c in run.calls)
    assert any("push --force-with-lease" in c for c in run.calls)
    err = capsys.readouterr().err
    assert "3 commit(s) behind" in err and "auto-rebased" in err


def test_ensure_fresh_refuses_on_a_committed_commit_conflict(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("rev-parse HEAD", "deadbee0" * 5),
                   ("rev-list --count", "3"),
                   ("rebase --autostash", RuntimeError("CONFLICT in a.py"))])
    out = work.ensure_fresh(d, ON, goal, run=run)
    assert out is not None and "0001-x" in out and "could not apply cleanly" in out
    assert "git rebase --abort" in run.calls


def test_ensure_fresh_refuses_on_an_autostash_pop_conflict_and_never_pushes(tmp_path):
    """The routine case at THIS call site (#1890 plan-review): verify always runs against an
    uncommitted diff, since `work.py commit` never runs until after verify passes."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("rev-parse HEAD", "deadbee0" * 5),
                   ("rev-list --count", "3"),
                   ("rebase --autostash", ""),
                   ("diff --name-only --diff-filter=U", "file.txt"),
                   ("stash list", "stash@{0}: autostash")])
    out = work.ensure_fresh(d, ON, goal, run=run)
    assert out is not None and "0001-x" in out and "could not apply cleanly" in out
    assert "git stash pop" in run.calls
    assert not any("push --force-with-lease" in c for c in run.calls)


def test_ensure_fresh_refuses_when_rebase_itself_raises_after_staleness_is_confirmed(tmp_path):
    """Blocking finding from independent pre-PR review, reproduced here exactly as they did:
    `_behind_count`'s own fetch can succeed (so staleness IS confirmed, count > 0) while
    `rebase()`'s SEPARATE, later internal fetch fails moments after (a transient blip, a push
    race). This must not raise out of `ensure_fresh` uncaught -- which would crash the whole
    `loop.py verify` process with a raw traceback (indistinguishable from the proving command
    itself failing) -- and it must NOT silently return `None` either: staleness is already
    confirmed at this point, so proceeding would run the suite against code already known to be
    behind, exactly the bug #1890 exists to close. Only `_behind_count` failing (staleness still
    UNKNOWN) should fail open -- see the test above this one's sibling,
    `test_ensure_fresh_fails_open_when_the_remote_is_unreachable`, which is a DIFFERENT case."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    fetches = []

    def flaky_fetch(_line):
        fetches.append(1)
        if len(fetches) == 1:
            return ""                                          # _behind_count's own fetch: fine
        raise RuntimeError("could not resolve host")            # rebase()'s own, later fetch: fails

    run = _runner([("fetch", flaky_fetch), ("rev-list --count", "3")])
    out = work.ensure_fresh(d, ON, goal, run=run)                # must not raise
    assert out is not None
    assert "0001-x" in out and "3 commit(s) behind" in out
    assert "could not resolve host" in out
    assert "re-run" in out.lower()


# --- #1125: union-merge a CHANGELOG conflict, but ONLY when it is provably lossless -------------

_D3_PURE = """# Changelog

## Unreleased
<<<<<<< HEAD
- entry from main
||||||| base
=======
- entry from this goal
>>>>>>> sdlc/0001-x

## 1.0.0
"""

_D3_EDITED = """# Changelog

## Unreleased
<<<<<<< HEAD
- rewritten by main
||||||| base
- the original line
=======
- rewritten by this goal
>>>>>>> sdlc/0001-x
"""


def _conflict(stages="100644 aaa 1\tCHANGELOG.md\n100644 bbb 2\tCHANGELOG.md", unmerged="CHANGELOG.md"):
    """Handlers for a rebase that conflicts once, then continues cleanly. The mock returns "" for
    anything unmatched, so EVERY guard's command must be registered explicitly or the test would
    silently exercise a different path than it claims to."""
    return [("rebase --autostash", RuntimeError("CONFLICT (content): CHANGELOG.md")),
            ("diff --name-only --diff-filter=U", unmerged),
            ("ls-files -u", stages)]


def test_a_pure_insertion_changelog_conflict_is_union_merged(tmp_path):
    """The #1125 shape: two goals each add an entry under `## Unreleased`. Nothing was edited, so
    keeping BOTH is lossless by construction and the rebase continues instead of parking."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    wt = pathlib.Path(work._record(d, goal)["worktree"])
    (wt / "CHANGELOG.md").write_text(_D3_PURE, encoding="utf-8")
    run = _runner(_rights() + _protected() + [("pr view", _sequence(_view(status="BEHIND"), _view()))]
                  + _conflict())
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    text = (wt / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- entry from main" in text and "- entry from this goal" in text   # NOTHING lost
    assert "<<<<<<<" not in text and "|||||||" not in text
    assert text.endswith("\n")                                               # trailing newline kept
    assert "core.editor=true rebase --continue" in " ".join(run.calls)        # never opens an editor
    assert out.startswith("PR #7 merged")


def test_an_edited_line_in_the_changelog_conflict_still_aborts(tmp_path):
    """The content-loss guard. A NON-EMPTY diff3 base means a side changed a line the other kept --
    a judgement call, never a union. This is the case that must never silently resolve."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    wt = pathlib.Path(work._record(d, goal)["worktree"])
    (wt / "CHANGELOG.md").write_text(_D3_EDITED, encoding="utf-8")
    run = _runner(_rights() + [("pr view", _view(status="BEHIND"))] + _conflict())
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: rebase deferred")
    assert "git rebase --abort" in run.calls
    assert not any("pr merge" in c for c in run.calls)
    assert (wt / "CHANGELOG.md").read_text(encoding="utf-8") == _D3_EDITED    # left untouched


def test_an_add_add_changelog_conflict_aborts_because_there_is_no_base(tmp_path):
    """No stage 1 == no merge base. "Neither side touched a base line" is then VACUOUSLY true, so a
    union would splice two unrelated files together and report success. Must abort."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    wt = pathlib.Path(work._record(d, goal)["worktree"])
    (wt / "CHANGELOG.md").write_text(_D3_PURE, encoding="utf-8")
    run = _runner(_rights() + [("pr view", _view(status="BEHIND"))]
                  + _conflict(stages="100644 bbb 2\tCHANGELOG.md\n100644 ccc 3\tCHANGELOG.md"))
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: rebase deferred")
    assert "git rebase --abort" in run.calls


def test_a_conflict_touching_more_than_the_changelog_aborts(tmp_path):
    """Scoped to ONE recognisable pattern, not a general resolver."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + [("pr view", _view(status="BEHIND"))]
                  + _conflict(unmerged="CHANGELOG.md\nskills/agrim-loop/scripts/work.py"))
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: rebase deferred")
    assert "git rebase --abort" in run.calls


def test_identical_insertions_are_not_duplicated():
    """Both sides adding the SAME line is lossless either way, but emitting it twice is wrong."""
    same = "## Unreleased\n<<<<<<< HEAD\n- same entry\n||||||| base\n=======\n- same entry\n>>>>>>> x\n"
    resolved, ok = work._union_diff3(same)
    assert ok and resolved.count("- same entry") == 1


def test_two_way_markers_are_refused_because_the_base_is_invisible():
    """Without diff3 we cannot see the base, so losslessness is unprovable -> refuse."""
    two_way = "## Unreleased\n<<<<<<< HEAD\n- a\n=======\n- b\n>>>>>>> x\n"
    assert work._union_diff3(two_way) == (None, False)


def test_a_second_conflicting_commit_in_the_replay_is_also_union_merged(tmp_path):
    """A rebase replays N commits, so resolving one and continuing can conflict AGAIN. The bounded
    loop must keep resolving. Verified against real git too: two conflicting commits replay to a
    finished rebase with a clean tree and every entry preserved."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    wt = pathlib.Path(work._record(d, goal)["worktree"])
    (wt / "CHANGELOG.md").write_text(_D3_PURE, encoding="utf-8")
    rounds = {"n": 0}

    def recheckout(_line):
        (wt / "CHANGELOG.md").write_text(_D3_PURE, encoding="utf-8")   # git re-materialises markers
        return ""

    def cont(_line):
        rounds["n"] += 1
        if rounds["n"] == 1:
            raise RuntimeError("CONFLICT (content): CHANGELOG.md")     # the NEXT replayed commit
        return ""

    run = _runner(_rights() + _protected() + [("pr view", _sequence(_view(status="BEHIND"), _view()))]
                  + [("rebase --continue", cont), ("checkout --merge", recheckout)] + _conflict())
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert rounds["n"] == 2                                            # it really did go twice
    assert out.startswith("PR #7 merged")


def test_the_union_loop_is_bounded_and_gives_up(tmp_path):
    """An endlessly-conflicting replay must PARK, not spin. UNION_ROUNDS is the cap."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    wt = pathlib.Path(work._record(d, goal)["worktree"])
    (wt / "CHANGELOG.md").write_text(_D3_PURE, encoding="utf-8")
    tries = {"n": 0}

    def recheckout(_line):
        (wt / "CHANGELOG.md").write_text(_D3_PURE, encoding="utf-8")
        return ""

    def cont(_line):
        tries["n"] += 1
        raise RuntimeError("CONFLICT (content): CHANGELOG.md")         # never resolves

    run = _runner(_rights() + [("pr view", _view(status="BEHIND"))]
                  + [("rebase --continue", cont), ("checkout --merge", recheckout)] + _conflict())
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert tries["n"] == work.UNION_ROUNDS                             # bounded, not unbounded
    assert out.startswith("PARK: rebase deferred")
    assert "git rebase --abort" in run.calls                           # and it cleans up


# --- #406: a BEHIND race must self-heal within ONE merge() call, but stay BOUNDED -----------------

def _sequence(*responses):
    """A `pr view` handler that returns each response in turn, repeating the LAST forever -- so a
    test can script BEHIND -> (transient) -> settled across a single merge() call. `gate()` may read
    `pr view` several times per call (its UNKNOWN/PENDING budgets), so `_runner`'s first-substring
    match drives this; the repeat-last tail is what those inner re-reads see."""
    seen = []

    def view(_line):
        i = min(len(seen), len(responses) - 1)
        seen.append(1)
        return responses[i]
    return view


def test_behind_reconcile_self_heals_transient_window_within_one_merge(tmp_path):
    """#406, the core fix: after a rebase, GitHub reports the new head TRANSIENTLY (mergeStateStatus
    non-CLEAN with an empty rollup because the new CI run has not attached yet). The pre-#406 single
    re-check PARKed on exactly that; the bounded poll rides through it to CLEAN and ARMS -- within one
    merge() call, no extra loop pass -- and needs only ONE rebase to do it."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    view = _sequence(_view(status="BEHIND"),
                     _view(status="BLOCKED", checks=()),   # rebased head: no check attached yet
                     _view())                              # settles CLEAN
    run = _runner(_rights() + _protected() + [("pr view", view)])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    assert "gh pr merge 7 --squash" in run.calls
    assert not any("--auto" in c.split() for c in run.calls)   # exact token: not a `--autostash` collision
    assert sum("rebase --autostash origin/main" in c for c in run.calls) == 1   # ONE rebase, not a storm


def test_behind_reconcile_arms_when_the_rebased_head_settles_to_pending(tmp_path):
    """The central hand-back: a post-rebase head whose checks attach as PENDING (not failing) must be
    ARMED via --auto -- gate()'s own PENDING budget answers, `_behind_transient` returns False on that
    real answer, and merge()'s existing pending_arm path arms. Guards against a future refactor that
    would poll-then-park a pending head and re-strand it (#144/#252)."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    view = _sequence(_view(status="BEHIND"),
                     _mixed(("ci", "SUCCESS"), ("slow", "")))   # rebased head: a check still running
    run = _runner(_rights() + _protected(checks=("ci", "slow")) + _auto_merge_allowed(True)
                  + [("pr view", view)])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed on PR #7") and "pending" in out
    assert "gh pr merge 7 --auto --squash" in run.calls
    assert sum("rebase --autostash origin/main" in c for c in run.calls) == 1


def test_behind_reconcile_parks_an_unwinnable_race_after_a_bounded_number_of_rebases(tmp_path):
    """`main` keeps moving under us -- every post-rebase read is BEHIND again. This must NOT retry
    forever: rebase at most BEHIND_REBASES times, then PARK for a human (or the merge queue). The
    bound is the whole safety property."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view(status="BEHIND"))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: BEHIND race did not settle") and "merge queue" in out
    assert sum("rebase --autostash origin/main" in c for c in run.calls) == work.BEHIND_REBASES
    assert not any("pr merge" in c for c in run.calls)


def test_behind_reconcile_parks_when_the_transient_window_never_closes(tmp_path):
    """The rebased head stays TRANSIENT past the poll budget (CI never attaches). Bounded: after
    exactly BEHIND_ATTEMPTS post-rebase reads, hand the not-ok verdict back so merge() PARKs -- never
    an unbounded wait, never an arm on an unsettled head, and only ONE rebase (main did not move)."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected()
                  + [("pr view", _sequence(_view(status="BEHIND"),
                                           _view(status="BLOCKED", checks=())))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: not safe to merge") and "BLOCKED" in out
    assert not any("pr merge" in c for c in run.calls)
    assert sum("rebase --autostash origin/main" in c for c in run.calls) == 1
    # initial merge() gate read (1) + exactly BEHIND_ATTEMPTS post-rebase reads -- the bound, pinned.
    assert sum("mergeStateStatus" in c for c in run.calls) == work.BEHIND_ATTEMPTS + 1


def test_behind_reconcile_does_not_poll_past_a_failing_check(tmp_path):
    """A FAILING required check after the rebase is a settled 'no', not the attach window -- PARK on
    the FIRST post-rebase read, never spend the poll budget on a verdict already in hand."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected()
                  + [("pr view", _sequence(_view(status="BEHIND"),
                                           _mixed(("ci", "FAILURE"), status="UNSTABLE")))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: failing required check ci has no readable Actions log")
    assert not any("pr merge" in c for c in run.calls)
    assert sum("mergeStateStatus" in c for c in run.calls) == 2   # initial + ONE post-rebase, not the budget


def test_behind_reconcile_does_not_poll_past_a_conflict(tmp_path):
    """A rebase can SUCCEED and still leave the head CONFLICTING (base moved to an incompatible state).
    That takes `_behind_transient`'s `mergeable != MERGEABLE` branch -- a settled verdict, PARK on the
    first post-rebase read rather than polling a conflict that only a human resolves."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected()
                  + [("pr view", _sequence(_view(status="BEHIND"),
                                           _view(mergeable="CONFLICTING", status="DIRTY")))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: conflicts with the base branch")
    assert not any("pr merge" in c for c in run.calls)
    assert sum("mergeStateStatus" in c for c in run.calls) == 2   # not polled past


def test_behind_transient_classification_table():
    """`_behind_transient` decides what to poll THROUGH vs act on. Pinned directly, including the
    empty-`{}` read gate() returns on a failed `pr view` -- it must classify non-transient (settled ->
    PARK) and, above all, NOT raise: a subscript there would crash the whole `merge` verb mid-race."""
    T = work._behind_transient
    # settling -- poll through:
    assert T("GitHub could not compute mergeability (still UNKNOWN after retries)",
             {"mergeable": "UNKNOWN"}) is True
    assert T("not safe to merge (mergeStateStatus=BLOCKED)",
             {"mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED", "statusCheckRollup": []}) is True
    # settled -- act on (never polled past):
    assert T("could not read PR state (boom)", {}) is False           # empty read: PARK, and NO raise
    assert T(work.PENDING_PREFIX + " after 450s (mergeStateStatus=BLOCKED) -- pending: ci",
             {"mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED"}) is False   # real pending -> arm
    assert T("conflicts with the base branch -- a human has to resolve them",
             {"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"}) is False
    assert T("clean and safe", {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}) is False
    assert T("not safe to merge (mergeStateStatus=BLOCKED) -- failing: ci",
             {"mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED",
              "statusCheckRollup": [{"name": "ci", "conclusion": "FAILURE"}]}) is False
    assert T("not safe to merge (mergeStateStatus=BLOCKED)",
             {"mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED",
              "statusCheckRollup": [{"name": "ci", "conclusion": ""}]}) is False   # attached & pending


def test_merge_needs_a_pr(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    assert work.merge(d, ALWAYS, goal, run=_runner([]), sleep=NOSLEEP).startswith("PARK: no PR")


# --- protection: what actually enforces the answer -----------------------------------------------

def test_protected_policy_will_not_merge_an_unprotected_branch(tmp_path):
    """The whole point of the tri-state: autonomy proportional to the guardrails that exist."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + UNPROTECTED + [("pr view", _view())])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 clean and safe, but") and "is not protected" in out
    assert not any("pr merge" in c for c in run.calls)


def test_merge_protected_policy_leaves_an_unprotected_branch_alone_even_with_pending_checks(tmp_path):
    """Same #254 task 3 / plan-review MODERATE fix, the `protected`-policy half: pending-exhausted
    must not silently start claiming an unprotected branch is "clean and safe" either."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + UNPROTECTED + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert not any("pr merge" in c for c in run.calls)
    assert "is not protected" in out and "pending" in out.lower()


def test_always_merges_unprotected_but_says_nothing_gated_it(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + UNPROTECTED + [("pr view", _view())])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert "WARNING" in out and "local verify was the only gate" in out
    assert "gh pr merge 7 --squash" in run.calls
    assert not any("--auto" in c.split() for c in run.calls)   # exact token: not a `--autostash` collision


def test_checks_that_run_without_being_required_do_not_count_as_protection(tmp_path):
    """The bug the first version shipped: a non-empty statusCheckRollup was read as 'checks are
    required'. A repo can run CI on every PR and require none of it."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + UNPROTECTED + [("pr view", _view(checks=(("ci", "SUCCESS"),)))])
    assert "is not protected" in work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)


def test_protection_with_no_requirements_is_not_protection(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=(), reviews=0) + [("pr view", _view())])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert "requires no checks or reviews" in out
    assert not any("pr merge" in c for c in run.calls)


def test_protection_fails_open_when_the_api_returns_non_object_json(tmp_path):
    """Same code-review finding as done_refusal's non-object-JSON guard, found by auditing every
    other `gh` read reachable from it: `data.get(...)` here ran OUTSIDE protection()'s try/except, so
    a valid-but-non-object reply from `gh api .../protection` (`null`, `[]`, `42` -- a bug or a future
    schema change) raised AttributeError instead of collapsing to the same answer the ordinary 404
    ("not protected") case already gets. Both mean the same thing here: nothing readable is enforcing
    anything on this base, so both must produce the identical fail-open answer, never a crash."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    for shape in ("null", "[]", "42"):
        run = _runner(_rights() + [("branches/main/protection", shape)] + [("pr view", _view())])
        out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
        assert out.startswith("PR #7 clean and safe, but") and "is not protected" in out, shape
        assert not any("pr merge" in c for c in run.calls)


# --- the policy knob itself ----------------------------------------------------------------------

def test_policy_parses_the_tri_state_and_the_old_booleans(tmp_path):
    cases = {"off": work.OFF, "protected": work.PROTECTED, "always": work.ALWAYS,
             "PROTECTED": work.PROTECTED, True: work.ALWAYS, False: work.OFF,
             "nonsense": work.OFF, None: work.OFF}
    for value, expected in cases.items():
        assert work.policy({"work": {"auto_merge": value}}) == expected, value


def test_policy_defaults_to_off_when_unset(tmp_path):
    assert work.policy({"work": {"enabled": True}}) == work.OFF
    assert work.policy({}) == work.OFF


# --- _pr_merged: the positively-confirmed-merged check finish() gates branch deletion on --------

def test_pr_merged_true_when_github_confirms_merged(tmp_path):
    d = _sdlc(tmp_path)
    rec = {"pr": "7"}
    run = _runner([("pr view", _pr_state("MERGED"))])
    assert work._pr_merged(d, rec, run) is True


def test_pr_merged_false_when_still_open(tmp_path):
    d = _sdlc(tmp_path)
    rec = {"pr": "7"}
    run = _runner([("pr view", _pr_state("OPEN"))])
    assert work._pr_merged(d, rec, run) is False


def test_pr_merged_false_when_closed_without_merging(tmp_path):
    d = _sdlc(tmp_path)
    rec = {"pr": "7"}
    run = _runner([("pr view", _pr_state("CLOSED"))])
    assert work._pr_merged(d, rec, run) is False


def test_pr_merged_fails_closed_when_the_read_raises(tmp_path):
    """No `gh`, no network, an expired token -- anything. Deleting a branch is not reversible the
    way a refused `finish` is, so an unreadable state must never read as "assume merged"."""
    d = _sdlc(tmp_path)
    rec = {"pr": "7"}

    def run(cwd, argv):
        raise RuntimeError("gh: command not found")
    assert work._pr_merged(d, rec, run) is False


def test_pr_merged_runs_from_project_root_not_a_worktree(tmp_path):
    """Called only from `finish()`, always AFTER `git worktree remove` -- the worktree path no
    longer exists on disk by then, so this must never be handed it as a candidate cwd the way
    `_open_pr_refusal` sometimes is. A plain call-log check can't prove this (`_runner` never
    records `cwd`, only `argv`); this instead mirrors the precedented pattern in
    `test_finish_refuses_an_open_pr_even_when_the_worktree_directory_is_already_gone` below, which
    genuinely proves the cwd choice by making a bad cwd raise."""
    d = _sdlc(tmp_path)
    rec = {"pr": "7", "worktree": "/definitely/does/not/exist"}
    pr_json = _pr_state("MERGED")

    def run(cwd, argv):
        if not os.path.isdir(str(cwd)):
            raise FileNotFoundError(2, "No such file or directory", str(cwd))
        line = " ".join(str(a) for a in argv)
        return pr_json if "pr view" in line else ""

    assert work._pr_merged(d, rec, run) is True


# --- finish: don't leak a checkout per goal ------------------------------------------------------

def test_finish_removes_the_worktree_and_clears_the_record(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([])
    assert "removed" in work.finish(d, ON, goal, run=run)
    assert work._record(d, goal) is None
    assert any("worktree prune" in c for c in run.calls)


def test_finish_keeps_a_worktree_that_still_holds_work(tmp_path):
    """A parked goal's tree is what the human picks up — losing it is worse than a leak."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("worktree remove", RuntimeError("contains modified files"))])
    assert work.finish(d, ON, goal, run=run).startswith("kept ")
    assert work._record(d, goal) is not None


def test_finish_refuses_when_the_pr_is_still_open(tmp_path):
    """#1202: `finish` used to delete the goal's ONLY PR pointer with no check at all -- severing
    merge/rebase/post-review's own subprocess cwd out from under a PR nobody had merged yet. This
    is the SANCTIONED flow under the shipped `auto_merge: off` default (SKILL.md's own 'after a
    done, release the checkout: work.py finish' instruction), not an edge case -- so it must refuse,
    not silently discard the pointer, and it must never even reach `git worktree remove`."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _pr_state("OPEN"))])
    out = work.finish(d, ON, goal, run=run)
    assert out.startswith("kept ")
    assert "PR #7" in out and "open" in out
    assert work._record(d, goal) is not None
    assert not any("worktree remove" in c for c in run.calls)


def test_finish_force_releases_the_checkout_even_with_an_open_pr(tmp_path):
    """The refusal above is overridable: `--force` (wired to `force=True`) means "release anyway",
    and skips the PR read entirely rather than confirming OPEN just to override it."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _pr_state("OPEN"))])
    out = work.finish(d, ON, goal, run=run, force=True)
    assert "removed" in out
    assert work._record(d, goal) is None
    assert not any("pr view" in c for c in run.calls)


def test_finish_proceeds_when_the_pr_is_already_merged(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _pr_state("MERGED"))])
    out = work.finish(d, ON, goal, run=run)
    assert "removed" in out
    assert work._record(d, goal) is None


def test_finish_records_an_asynchronously_landed_merge_before_discarding_its_pr_pointer(tmp_path):
    """The last observer must record facts before finish removes the only goal record."""
    cfg = {**ON, "ledger": {"enabled": True, "actor": "rae"}, "journal": {"enabled": True}}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    run = _runner([("pr view", _pr_state("MERGED")),
                   ("api repos/{owner}/{repo}/pulls/7", _merged_pr())])
    assert work.finish(d, cfg, goal, run=run).startswith("removed")
    ledger = _load("ledger")
    assert len([row for row in ledger.read_all(d) if row.get("kind") == "merged"]) == 1
    assert len([row for row in journal_events(ledger, d) if row.get("kind") == "merge_observed"]) == 1
    assert not list((pathlib.Path(d) / "state" / "merge-deliveries").glob("*.json"))


def test_finish_records_a_confirmed_merge_when_the_goal_worktree_is_already_gone(tmp_path):
    """The final observer reads GitHub from the durable project root, never a deleted worktree."""
    cfg = {**ON, "ledger": {"enabled": True, "actor": "rae"}, "journal": {"enabled": True}}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    worktree = pathlib.Path(work._record(d, goal)["worktree"])
    shutil.rmtree(worktree)

    def run(cwd, argv):
        if not pathlib.Path(cwd).is_dir():
            raise FileNotFoundError(2, "No such file or directory", str(cwd))
        line = " ".join(str(value) for value in argv)
        if "pr view" in line:
            return _pr_state("MERGED")
        if "api repos/{owner}/{repo}/pulls/7" in line:
            return _merged_pr()
        return ""

    assert work.finish(d, cfg, goal, run=run).startswith("removed")
    assert any(row.get("kind") == "merged" for row in ledger.read_all(d))
    assert any(row.get("kind") == "merge_observed" for row in journal_events(ledger, d))


def test_finish_deletes_both_branches_once_the_pr_is_confirmed_merged(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _pr_state("MERGED"))])
    out = work.finish(d, ON, goal, run=run)
    assert "removed" in out and "deleted branch sdlc/0001-x" in out
    assert any(c == "git branch -D sdlc/0001-x" for c in run.calls)
    assert any(c == "gh api -X DELETE repos/{owner}/{repo}/git/refs/heads/sdlc/0001-x"
              for c in run.calls)


def test_finish_reports_the_remote_delete_as_unconfirmed_when_it_fails(tmp_path):
    """Code-review finding: the local `git branch -D` succeeding must never be reported as if the
    branch is fully gone when the remote half -- usually already done by `merge()`'s own eager
    call, but not always (a permission error, a transient API failure) -- came back unconfirmed."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _pr_state("MERGED")),
                   ("DELETE", RuntimeError("HTTP 403: Resource not accessible"))])
    out = work.finish(d, ON, goal, run=run)
    assert "deleted local branch sdlc/0001-x (remote delete unconfirmed)" in out
    assert any(c == "git branch -D sdlc/0001-x" for c in run.calls)


def test_finish_covers_the_armed_then_asynchronously_landed_gap(tmp_path):
    """`merge()`'s own remote delete only ever fires on ITS direct-landing path. A goal armed via
    `--auto` returns from `merge()` before GitHub's real merge happens, so nothing has deleted the
    remote branch by the time this goal reaches `finish` -- but by THEN the async merge has
    actually landed (state has moved from OPEN to MERGED), so `_pr_merged` now reads true and this
    is the only place left with a chance to clean up the remote side too."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _pr_state("MERGED"))])
    out = work.finish(d, ON, goal, run=run)
    assert "deleted branch" in out
    assert any("DELETE" in c for c in run.calls)


def test_finish_leaves_both_branches_when_auto_merge_is_armed_but_not_yet_landed(tmp_path):
    """Still OPEN (checks genuinely still pending, not yet landed even asynchronously) -- nothing
    to confirm yet, so nothing is deleted. `finish` still proceeds (existing behaviour, see
    `test_finish_proceeds_when_auto_merge_is_armed_on_a_still_open_pr`) -- only the NEW cleanup is
    skipped."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _pr_state("OPEN", auto_merge=True))])
    out = work.finish(d, ON, goal, run=run)
    assert "removed" in out and "deleted branch" not in out
    assert not any("branch -D" in c for c in run.calls)
    assert not any("DELETE" in c for c in run.calls)


def test_finish_leaves_both_branches_when_the_pr_state_is_unreadable(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", RuntimeError("gh: command not found"))])
    out = work.finish(d, ON, goal, run=run)
    assert "removed" in out and "deleted branch" not in out
    assert not any("branch -D" in c for c in run.calls)


def test_finish_leaves_both_branches_when_the_goal_never_had_a_pr(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("pr view", _pr_state("MERGED"))])   # must never even be called
    out = work.finish(d, ON, goal, run=run)
    assert "removed" in out and "deleted branch" not in out
    assert not any("pr view" in c for c in run.calls)
    assert not any("branch -D" in c for c in run.calls)


def test_finish_branch_delete_failure_does_not_change_the_reported_outcome(tmp_path):
    """Best-effort: some OTHER process already deleted the branch, a filesystem race, anything --
    `finish` already fully succeeded (worktree gone, record cleared) by the time this runs, and a
    cleanup nicety failing must never turn that into a reported failure, NOR into a false claim of
    success (the message must match what actually happened)."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    wt = work._record(d, goal)["worktree"]
    run = _runner([("pr view", _pr_state("MERGED")),
                   ("branch -D", RuntimeError("error: branch 'sdlc/0001-x' not found"))])
    out = work.finish(d, ON, goal, run=run)
    assert out == f"removed {wt}"                     # exact match: no false "deleted branch" claim
    assert work._record(d, goal) is None               # the primary outcome is still fully reported


def test_finish_never_raises_when_the_record_is_missing_a_branch_name(tmp_path):
    """Defensive only -- `start()` always sets `branch` (work.py:406) so this isn't reachable via
    the normal CLI today, but an unguarded second `rec['branch']` reference one hand-edited state
    file away is a KeyError risk. Must degrade to "no cleanup attempted", never crash a fully
    successful worktree removal."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    rec = work._record(d, goal)
    del rec["branch"]
    work._save(d, goal, rec)
    run = _runner([("pr view", _pr_state("MERGED"))])
    out = work.finish(d, ON, goal, run=run)
    assert out == f"removed {rec['worktree']}"


def test_finish_force_skips_branch_cleanup_even_when_the_pr_is_actually_merged(tmp_path):
    """`force` skips the PR read entirely (`test_finish_force_releases_the_checkout_even_with_an_
    open_pr` already locks this in for the refusal check) -- this cleanup must honour the same
    contract rather than silently attaching a new network call to the one path that exists
    specifically to avoid depending on one."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _pr_state("MERGED"))])   # must never even be called
    out = work.finish(d, ON, goal, run=run, force=True)
    assert "removed" in out and "deleted branch" not in out
    assert not any("pr view" in c for c in run.calls)
    assert not any("branch -D" in c for c in run.calls)
    assert not any("DELETE" in c for c in run.calls)


def test_finish_proceeds_when_auto_merge_is_armed_on_a_still_open_pr(tmp_path):
    """(#1218 review fix) `state == "OPEN"` alone is not evidence anything is left to sever --
    `merge()` itself (site: `run(... ["gh", "pr", "merge", ...  "--auto", ...])`) arms auto-merge
    and returns immediately WITHOUT waiting for GitHub's async merge to land, so a PR it just
    armed reads `state=OPEN` for some window (guaranteed, for the #254 pending-checks arm). SKILL.md
    routes that exact 'auto-merge armed' message straight to `record done` then `finish`, with no
    wait step in between. `done_refusal` already exempts this via its own `autoMergeRequest`
    truthy -> None carve-out -- `finish`'s refusal must mirror it, or every armed-but-not-yet-landed
    PR under this repo's own `auto_merge: protected` config gets wedged here instead of released."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _pr_state("OPEN", auto_merge=True))])
    out = work.finish(d, ON, goal, run=run)
    assert "removed" in out
    assert work._record(d, goal) is None


def test_finish_proceeds_when_the_pr_state_is_unreadable(tmp_path):
    """Same fail-open shape as merge_rights/done_refusal elsewhere in this module: no `gh`, no
    network, or a non-object JSON reply must never block finish -- only a POSITIVELY confirmed
    OPEN state does."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", RuntimeError("gh: command not found"))])
    out = work.finish(d, ON, goal, run=run)
    assert "removed" in out
    assert work._record(d, goal) is None


def test_finish_refuses_an_open_pr_even_when_the_worktree_directory_is_already_gone(tmp_path):
    """(#1218 review fix) `_open_pr_refusal` used to run `gh pr view` with `cwd=rec["worktree"]`.
    When that directory is missing from disk -- container/disk reset, manual cleanup, a CI
    ephemeral runner wipe, or simply a DIFFERENT goal's `finish` having already pruned it (exactly
    the precondition `test_finish_recovers_a_record_whose_worktree_admin_entry_another_goal_already_pruned`
    above exercises for the *second* #1202 fix) -- `run(cwd, argv)` raises FileNotFoundError, which
    landed in the SAME `except Exception: return None` meant only for "no gh"/"no network"/
    "non-object JSON". That fails `_open_pr_refusal` OPEN, so `finish` fell through and deleted the
    state record for a PR that was genuinely still open and unmerged -- reproducing the exact bug
    (#1202) this whole refusal exists to prevent. `_open_pr_refusal` must read the PR's live state
    from a cwd that is always present (`project_root`) rather than the possibly-gone worktree."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    wt = work._record(d, goal)["worktree"]
    shutil.rmtree(wt)                                    # the worktree directory is gone from disk

    pr_json = _pr_state("OPEN")

    def run(cwd, argv):
        # Faithful stand-in for subprocess.run's real cwd validation (verified separately:
        # subprocess.run([...], cwd="/nonexistent/dir") raises FileNotFoundError).
        if not os.path.isdir(str(cwd)):
            raise FileNotFoundError(2, "No such file or directory", str(cwd))
        line = " ".join(str(a) for a in argv)
        return pr_json if "pr view" in line else ""

    out = work.finish(d, ON, goal, run=run)
    assert out.startswith("kept "), out
    assert "PR #7" in out and "open" in out
    assert work._record(d, goal) is not None


def test_finish_proceeds_when_the_goal_never_had_a_pr(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("pr view", _pr_state("OPEN"))])       # must never even be called
    out = work.finish(d, ON, goal, run=run)
    assert "removed" in out
    assert not any("pr view" in c for c in run.calls)


def test_finish_recovers_a_record_whose_worktree_admin_entry_another_goal_already_pruned(tmp_path):
    """#1202 correction 2: once a DIFFERENT goal's `finish` has run `git worktree prune` (which
    happens on EVERY successful finish), THIS goal's now-stale admin entry is gone, and `git
    worktree remove` on it fails CLOSED -- exit 128, `fatal: '<path>' is not a working tree` --
    even with --force (verified against real git). Before this fix that meant the record could
    never be cleared again, by any means: `finish` always read that 128 as "kept" and skipped the
    unlink. There is nothing left to preserve once git itself says the working tree is gone, so
    this is now the supported recovery path -- no new command needed."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    wt = pathlib.Path(work._record(d, goal)["worktree"])
    run = _runner([("worktree remove", RuntimeError(f"fatal: '{wt}' is not a working tree"))])
    out = work.finish(d, ON, goal, run=run)
    assert "removed" in out
    assert work._record(d, goal) is None
    assert any("worktree prune" in c for c in run.calls)
    assert not wt.exists()          # "removed" must be true on disk too, not just in the record


def test_finish_keeps_the_record_when_only_the_worktree_admin_metadata_is_gone_but_content_remains(tmp_path):
    """(#1218 third review fix, verified against real git) The recovery just above trusts git's
    "is not a working tree" text as proof there is nothing left to lose. It isn't: deleting ONLY the
    admin entry under `.git/worktrees/<id>` by hand -- leaving the actual checkout directory and any
    uncommitted file in it fully untouched -- reproduces the EXACT SAME "is not a working tree"
    message from `git worktree remove`, with or without --force (verified against real git). Before
    this fix that meant the disk content was never even looked at: the record was deleted and
    `finish` reported success while a directory holding real uncommitted work sat there orphaned,
    with nothing left pointing back at it. `finish` must check the path itself, not just git's
    wording, and refuse -- naming the path -- when there is still something in it."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    wt = pathlib.Path(work._record(d, goal)["worktree"])
    (wt / "uncommitted-work.txt").write_text("real work that was never committed")

    run = _runner([("worktree remove", RuntimeError(f"fatal: '{wt}' is not a working tree"))])
    out = work.finish(d, ON, goal, run=run)

    assert out.startswith("kept "), out
    assert str(wt) in out
    rec = work._record(d, goal)
    assert rec is not None                                    # state record survives, not silently lost
    assert rec["worktree"] == str(wt)
    assert (wt / "uncommitted-work.txt").exists()              # the orphaned content itself untouched
    assert not any("worktree prune" in c for c in run.calls)   # never treated as the clear-to-prune case


def test_finish_force_still_releases_an_orphaned_worktree_that_has_content(tmp_path):
    """The disk-content check above must not reintroduce the exact trap `test_finish_recovers_a_
    record_whose_worktree_admin_entry_another_goal_already_pruned` (correction 2) already fixed once
    -- a record that can never be cleared again, by ANY means. `--force` is this repo's documented
    universal override (see `finish`'s own docstring: "overrides all three refusals here"), so it
    must still win even when real content is sitting in the orphaned directory."""
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    wt = pathlib.Path(work._record(d, goal)["worktree"])
    (wt / "uncommitted-work.txt").write_text("real work that was never committed")

    run = _runner([("worktree remove", RuntimeError(f"fatal: '{wt}' is not a working tree"))])
    out = work.finish(d, ON, goal, run=run, force=True)

    assert "removed" in out
    assert work._record(d, goal) is None
    assert not wt.exists()          # the orphaned content must actually go, not just the record


# --- CLI + the off switch ------------------------------------------------------------------------

def test_cli_refuses_every_command_while_the_feature_is_off(tmp_path, capsys):
    d = _sdlc(tmp_path, {"work": {"enabled": False}})
    assert work.main(["work.py", "start", d, "0001-x.md"]) == 1
    assert "work is off" in capsys.readouterr().err


def test_cli_root_works_with_the_feature_off(tmp_path, capsys):
    """verify calls `root` on every goal, including repos that never enable any of this."""
    d = _sdlc(tmp_path, {"work": {"enabled": False}})
    assert work.main(["work.py", "root", d, "0001-x.md"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path.resolve())


# --- the REAL PR-review gate (require_review), independent of branch protection -----------------
# gate()'s "safe" only reflects reviews the base's protection REQUIRES, so a human 'Request changes'
# on an unprotected base is invisible to it. review_gate reads the actual review state and parks on it.


def _review(decision=None, changes_by=(), unresolved=0, comments=(), comment_authors=(), pr_author=None):
    """Handlers for the review gate: the `--json comments,author` marker scan, the `--json
    reviewDecision,latestReviews` read, and the GraphQL thread count. Ordered so the specific
    `--json comments` / `reviewDecision` matches win over a generic `pr view` handler that also
    matches those lines. `comment_authors` pairs positionally with `comments` (missing entries ->
    None, same as a real comment with no readable author); `pr_author` is the PR's own login (#821:
    unset in every pre-existing caller, so `same_author` computes False there — None != None is
    False by design in `_comment_directive` — leaving their behavior byte-identical)."""
    reviews = json.dumps({"reviewDecision": decision,
                          "latestReviews": [{"state": "CHANGES_REQUESTED", "author": {"login": u}}
                                            for u in changes_by]})
    threads = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "nodes": [{"isResolved": False}] * unresolved}}}}})
    authors = list(comment_authors) + [None] * (len(comments) - len(comment_authors))
    comment_json = json.dumps({"author": {"login": pr_author},
                               "comments": [{"body": b, "author": {"login": a}}
                                            for b, a in zip(comments, authors)]})
    return [("json comments", comment_json), ("reviewDecision", reviews),
            ("nameWithOwner", "acme/app"), ("graphql", threads)]


def test_review_mode_parses_off_changes_approval_and_true():
    assert work.review_mode({}) == "off"
    assert work.review_mode({"work": {"require_review": True}}) == "approval"
    assert work.review_mode({"work": {"require_review": "changes"}}) == "changes"
    assert work.review_mode({"work": {"require_review": "bogus"}}) == "off"          # unknown -> off, never blocks


def test_review_gate_is_a_noop_when_off(tmp_path):
    d = _sdlc(tmp_path); g = _started(d)
    assert work.review_gate(d, ON, g, run=_runner([])) == (True, "")


def test_review_gate_parks_on_changes_requested(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    ok, why = work.review_gate(d, cfg, g, run=_runner(_review(decision="CHANGES_REQUESTED", changes_by=["bo"])))
    assert ok is False and "changes requested by bo" in why


def test_review_gate_parks_on_an_unresolved_thread(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    ok, why = work.review_gate(d, cfg, g, run=_runner(_review(decision="APPROVED", unresolved=2)))
    assert ok is False and "2 unresolved review thread" in why


def test_review_gate_changes_mode_allows_an_unreviewed_pr(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "changes"}}      # blocks a request, doesn't demand approval
    d = _sdlc(tmp_path, cfg); g = _started(d)
    assert work.review_gate(d, cfg, g, run=_runner(_review(decision=None))) == (True, "")


def test_review_gate_approval_requires_an_approved_decision(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    ok, why = work.review_gate(d, cfg, g, run=_runner(_review(decision=None)))
    assert ok is False and "not approved yet" in why


def test_review_gate_approval_passes_on_an_approved_clean_pr(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    assert work.review_gate(d, cfg, g, run=_runner(_review(decision="APPROVED"))) == (True, "")


def test_review_gate_warns_when_the_only_approval_is_from_the_prs_own_author(capsys, tmp_path):
    """#821: `sigma:approve` satisfies `require_review: approval` regardless of who posted it —
    `_comment_directive` never checked. The gate outcome is unchanged (still approved, matching the
    deliberate self-authorship fallback a solo-account repo needs), but this specific shape — no
    native review ever cast, the only signal a comment from the PR's own author — must now be
    loud, the same way every other automated correction in this file already is."""
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["sigma:approve"],
                          comment_authors=["loop-bot"], pr_author="loop-bot"))
    assert work.review_gate(d, cfg, g, run=run) == (True, "")
    err = capsys.readouterr().err
    assert "not independent review" in err


def test_review_gate_does_not_warn_when_approval_is_from_a_different_account(capsys, tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["sigma:approve"],
                          comment_authors=["human-reviewer"], pr_author="loop-bot"))
    assert work.review_gate(d, cfg, g, run=run) == (True, "")
    assert capsys.readouterr().err == ""


def test_review_gate_does_not_warn_on_a_native_approved_review(capsys, tmp_path):
    """The common, healthy case (a real GitHub review, no comment marker involved at all) must
    stay exactly as quiet as it always was."""
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision="APPROVED", pr_author="loop-bot"))
    assert work.review_gate(d, cfg, g, run=run) == (True, "")
    assert capsys.readouterr().err == ""


def test_review_gate_does_not_warn_when_a_stale_same_author_comment_sits_alongside_a_real_approval(
        capsys, tmp_path):
    """Independent review of #821, before merge: the prior test's fixture had zero comments, so it
    stayed green even with the `decision != "APPROVED"` guard removed by mutation — a same-author
    `sigma:approve` left over from BEFORE a real review landed (a realistic shape: the loop
    self-approved early, a human reviewed it properly later) must not warn once a native APPROVED
    decision is also present. Genuinely distinguishes the two guards on the warning condition."""
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision="APPROVED", comments=["sigma:approve"],
                          comment_authors=["loop-bot"], pr_author="loop-bot"))
    assert work.review_gate(d, cfg, g, run=run) == (True, "")
    assert capsys.readouterr().err == ""


def test_review_gate_fails_open_on_a_read_error(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner([("reviewDecision", RuntimeError("gh boom"))])
    assert work.review_gate(d, cfg, g, run=run) == (True, "")           # other gates still hold


def test_merge_parks_when_a_review_requests_changes(tmp_path):
    """The whole point: an ad-hoc Request-changes on an unprotected base stops the auto-merge."""
    cfg = {"work": {"enabled": True, "auto_merge": "always", "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _review(decision="CHANGES_REQUESTED", changes_by=["bo"]) + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: changes requested by bo")
    assert not any("pr merge" in c for c in run.calls)                 # never armed the merge


def test_merge_arms_when_the_pr_is_approved(tmp_path):
    cfg = {"work": {"enabled": True, "auto_merge": "always", "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _review(decision="APPROVED") + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert not out.startswith("PARK") and any("pr merge" in c for c in run.calls)


# --- the self-authorship fallback: GitHub forbids approving/blocking your OWN PR, so a solo-account
# loop uses plain-comment markers (sigma:approve / :block / :unblock), which have no such rule.
# F9: the marker must LEAD its line (optional indent) — fixtures below put it first, with any human
# rationale trailing, since that's the one order the line-anchored matcher accepts.


def test_review_gate_parks_on_a_sigma_block_comment(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["sigma:block — please fix the retry"]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "sigma:block" in why


def test_a_sigma_unblock_clears_the_block(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["sigma:block", "sigma:unblock — fixed now"]))
    assert work.review_gate(d, cfg, g, run=run) == (True, "")     # latest marker wins


def test_a_sigma_approve_comment_satisfies_approval_mode(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}     # can't self-approve formally
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["sigma:approve — ship it"]))
    assert work.review_gate(d, cfg, g, run=run) == (True, "")


def test_approval_mode_parks_and_points_at_the_marker_without_an_approval(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    ok, why = work.review_gate(d, cfg, g, run=_runner(_review(decision=None, comments=["just a note"])))
    assert ok is False and "sigma:approve" in why


def test_a_later_block_beats_an_earlier_approve_even_when_formally_approved(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision="APPROVED", comments=["sigma:approve", "sigma:block — wait, no"]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "sigma:block" in why              # a block overrides even a formal approval


# --- F9: comment-marker parsing is line-anchored — a negated ("do NOT sigma:approve"), quoted
# (`>`), fenced (```), or substring ("sigma:approved") mention must never be mistaken for a real
# directive; symmetrically, a mention of sigma:block must never wrongly PARK a clean PR. ---


def test_line_directive_rejects_a_negated_approve():
    assert work._line_directive("do NOT sigma:approve") is None


def test_line_directive_rejects_a_negated_block():
    assert work._line_directive("please do NOT sigma:block this one") is None


def test_line_directive_rejects_a_substring_word():
    assert work._line_directive("sigma:approved") is None      # "approved" is not the marker "approve"


def test_line_directive_rejects_a_quoted_marker():
    assert work._line_directive("> sigma:approve") is None


def test_line_directive_rejects_a_fenced_marker():
    assert work._line_directive("example syntax:\n```\nsigma:approve\n```") is None


def test_line_directive_accepts_a_standalone_marker():
    assert work._line_directive("sigma:approve") == "approve"


def test_line_directive_accepts_an_indented_marker():
    assert work._line_directive("    sigma:block") == "block"          # optional leading indent


def test_line_directive_accepts_a_marker_with_trailing_prose():
    assert work._line_directive("sigma:block — please fix the retry") == "block"    # only LEADING text disqualifies


def test_line_directive_last_matching_line_in_a_comment_wins():
    assert work._line_directive("sigma:block\nsigma:unblock") == "unblock"


def test_review_gate_ignores_a_negated_approve_and_still_parks(tmp_path):
    """The exact F9 repro: a comment MENTIONING the marker in a negative sentence must not satisfy
    approval mode — the old substring-only test let this register as a real approve."""
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["do NOT sigma:approve until CI is green"]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "not approved yet" in why


def test_review_gate_ignores_an_approved_substring(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["sigma:approved of this approach fwiw"]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "not approved yet" in why


def test_review_gate_ignores_a_fenced_marker(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    body = "here's the marker syntax:\n```\nsigma:approve\n```"
    run = _runner(_review(decision=None, comments=[body]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "not approved yet" in why


def test_review_gate_ignores_a_quoted_marker(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["> sigma:approve\nnot really — quoting the bot"]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "not approved yet" in why


def test_review_gate_ignores_a_negated_block_and_does_not_wrongly_park(tmp_path):
    """Symmetric case from the issue: a comment DISCUSSING sigma:block (not issuing it) must not
    wrongly PARK an otherwise-clean PR."""
    cfg = {"work": {"enabled": True, "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["you should NOT need sigma:block for this"]))
    assert work.review_gate(d, cfg, g, run=run) == (True, "")


# --- post_review: the WRITE side — the loop reviews its OWN PR and posts the verdict (no human) ---


def _git_pr_worktree(worktree):
    """Make the goal's worktree a real PR checkout: `origin/main` as its base, one commit on top.

    Idempotent, so a test that posts several verdicts reuses one checkout and one head."""
    def git(*args):
        return subprocess.run(["git", "-C", str(worktree), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    if (pathlib.Path(worktree) / ".git").exists():
        return git("rev-parse", "HEAD")
    pathlib.Path(worktree).mkdir(parents=True, exist_ok=True)  # a runner-faked `start` never made it
    git("init", "-q", "-b", "sdlc/0001-x")
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (pathlib.Path(worktree) / "base.txt").write_text("base\n")
    git("add", "-A"); git("commit", "-qm", "base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    (pathlib.Path(worktree) / "change.txt").write_text("the change under review\n")
    git("add", "-A"); git("commit", "-qm", "change")
    return git("rev-parse", "HEAD")


def _review_chain(d, goal, verdict, reason=""):
    """Mint a REAL generation-bound evidence receipt through the documented publisher.

    The old helper hand-wrote evidence at an arbitrary path with a made-up brief digest, and
    `post_review` accepted it -- the suite exercised the forgery as its normal path (cycle-6
    finding B). This goes brief -> manifest pointer -> result -> evidence exactly as
    `review_context.py brief --output` / `run-resolved-review` / `review-evidence` do. Each call
    gets its own brief, so its own generation: one generation carries exactly one review result."""
    rec = work._record(d, goal)
    head = _git_pr_worktree(rec["worktree"])
    generations = pathlib.Path(d) / "state" / "review-generations"
    nonce = len(list(generations.glob("*"))) if generations.exists() else 0
    manifest_path = pathlib.Path(d) / "state" / "review-manifests" / (pathlib.Path(str(goal)).name + ".json")
    manifest = _load("review_context").publish_generation(
        d, goal, "review brief %d\n" % nonce, "PR#%s" % rec["pr"], str(manifest_path))
    resolution = generations / manifest["generation_id"] / "resolution.json"
    resolution.write_text('{"mechanism": "command"}')
    result = work._write_review_result(d, manifest, str(resolution), verdict, reason or verdict)
    evidence = work.review_evidence(d, goal, str(manifest_path), result["path"])["path"]
    return evidence, head


def _post_review(d, cfg, goal, run, verdict, reason=""):
    """Exercise the writer boundary with one fresh, genuinely generation-bound evidence receipt."""
    evidence, head = _review_chain(d, goal, verdict, reason)
    pr = int(work._record(d, goal)["pr"])

    def evidence_run(cwd, argv):
        if argv[:3] == ["gh", "pr", "view"] and "number,headRefOid" in argv:
            return json.dumps({"number": pr, "headRefOid": head})
        result = run(cwd, argv)
        if argv[:3] == ["gh", "pr", "comment"] and not result:
            return "https://github.example/issues/7#issuecomment-99"
        return result

    return work.post_review(d, cfg, goal, run=evidence_run, verdict=verdict, reason=reason,
                            evidence=str(evidence))


def test_post_review_approve_posts_the_marker(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    run = _runner([])
    out = _post_review(d, ON, goal, run, "approve")
    assert "posted sigma:approve" in out
    assert any("pr comment" in c and "sigma:approve" in c for c in run.calls)


def test_post_review_refuses_missing_or_malformed_evidence_before_any_remote_post(tmp_path):
    """The writer boundary, not just the CLI, protects the journal's remote fact."""
    d = _sdlc(tmp_path); goal = _started(d)
    missing = _runner([])
    assert "generation-bound evidence" in work.post_review(d, ON, goal, run=missing, verdict="approve")
    assert not any("pr comment" in call for call in missing.calls)

    evidence = pathlib.Path(d) / "malformed-evidence.json"
    evidence.write_text(json.dumps({"goal": goal, "verdict": "approve", "pr": 7,
                                    "head_sha": "a" * 40, "brief_sha256": "not-a-sha"}))
    malformed = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": "a" * 40}))])
    assert "not bound to this goal's current review generation" in work.post_review(
        d, ON, goal, run=malformed, verdict="approve", evidence=str(evidence))
    assert not any("pr comment" in call for call in malformed.calls)


def test_post_review_unblock_posts_a_typed_journal_observation(tmp_path):
    cfg = {**ON, **JOURNAL_ON, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    evidence, head = _review_chain(d, goal, "unblock")
    run = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": head})),
                   ("pr comment", "https://github.example/issues/7#issuecomment-43")])
    assert work.post_review(d, cfg, goal, run=run, verdict="unblock", evidence=str(evidence)).startswith("posted")
    assert any("sigma:unblock" in call for call in run.calls)
    events = [event for event in journal_events(_load("ledger"), d) if event.get("kind") == "review_posted"]
    assert len(events) == 1 and events[0]["verdict"] == "unblock"


def test_rollout_census_fails_when_post_review_attempts_a_journal_off_write(tmp_path):
    """Deliberate control: a visible PR approval without a local journal fact under-counts."""
    cfg = {**ON, "ledger": {"enabled": True, "actor": "rae"}, "journal": {"enabled": False}}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    evidence, head = _review_chain(d, goal, "approve")
    run = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": head})),
                   ("pr comment", "https://github.example/issues/7#issuecomment-44")])
    assert work.post_review(d, cfg, goal, run=run, verdict="approve", evidence=str(evidence)).startswith("posted")
    assert not [event for event in journal_events(_load("ledger"), d) if event.get("kind") == "review_posted"]

    observation = _load("merge_observation")
    pr = {"repository": "R_1", "node_id": "PR_7", "number": 7, "head_ref": "sdlc/2577",
          "head_sha": "a" * 40, "merge_sha": "c" * 40, "merged_at": "2026-01-02T00:00:00Z"}
    facts = {"canonical_repository_id": "R_1", "owner_kind": "goal", "owner_id": goal, "goal": goal,
             "head_ref": "sdlc/2577", "base_ref": "main", "pr_number": 7, "pr_node_id": "PR_7",
             "creating_writer": "work.pr", "pr_created_at": "2026-01-01T00:00:00Z"}
    ownership = observation.ownership_key(facts)
    snapshot = tmp_path / "pinned-journal-off"
    parent = snapshot / "receipts" / "v1" / ownership
    parent.mkdir(parents=True)
    parent_data = json.loads(observation.parent_receipt(facts))
    (parent / "parent.json").write_bytes(observation.parent_receipt(facts))
    child = parent / "merges" / (pr["merge_sha"] + ".json"); child.parent.mkdir()
    child.write_bytes(observation.child_receipt(parent_data, {"merge_sha": pr["merge_sha"],
                                                               "github_merged_at": pr["merged_at"]}))
    entry_key, _ = observation.observation_keys(ownership, pr["merge_sha"])
    (snapshot / "entries").mkdir()
    (snapshot / "entries" / "writer.jsonl").write_text(json.dumps({"kind": "merged", "pr": "7",
        "merged_entry_key": entry_key}) + "\n")
    scan = observation.coverage_scan(snapshot, [pr], require_measurements=True, journal_root=tmp_path)
    assert scan["counts"]["owned-unobserved-pending"] == 1


def test_evidence_backed_success_post_records_one_typed_review_observation_and_is_idempotent(tmp_path):
    cfg = {**ON, **JOURNAL_ON, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    evidence, head = _review_chain(d, goal, "approve")
    evidence = pathlib.Path(evidence)
    run = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": head})),
                   ("pr comment", "https://github.example/issues/7#issuecomment-42")])
    assert work.post_review(d, cfg, goal, run=run, verdict="approve", evidence=str(evidence)).startswith("posted")
    events = [event for event in journal_events(_load("ledger"), d) if event.get("kind") == "review_posted"]
    assert len(events) == 1 and events[0]["comment_id"] == 42 and events[0]["head_sha"] == head
    assert work.post_review(d, cfg, goal, run=run, verdict="approve", evidence=str(evidence)).startswith("posted")
    assert sum("pr comment" in call for call in run.calls) == 1


def test_evidence_backed_retry_after_ambiguous_post_is_reconcile_only(tmp_path):
    """A timed-out post may have reached GitHub, so retrying it must never make another comment."""
    cfg = {**ON, **JOURNAL_ON, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    evidence, head = _review_chain(d, goal, "approve")
    evidence = pathlib.Path(evidence)
    first = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": head})),
                     ("pr comment", RuntimeError("request timed out"))])
    assert work.post_review(d, cfg, goal, run=first, verdict="approve", evidence=str(evidence)).startswith("PARK:")
    assert sum("pr comment" in call for call in first.calls) == 1
    retry = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": head})),
                     ("issues/7/comments", "[]")])
    assert work.post_review(d, cfg, goal, run=retry, verdict="approve", evidence=str(evidence)).startswith("PARK:")
    assert not any("pr comment" in call for call in retry.calls)


def test_evidence_backed_post_parks_before_comment_when_the_pr_head_moved(tmp_path):
    cfg = {**ON, **JOURNAL_ON, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    evidence, head = _review_chain(d, goal, "approve")
    evidence = pathlib.Path(evidence)
    run = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": "c" * 40}))])
    assert work.post_review(d, cfg, goal, run=run, verdict="approve", evidence=str(evidence)).startswith("PARK:")
    assert not any("pr comment" in call for call in run.calls)


def test_remote_review_reconciliation_retries_an_enabled_journal_write_before_repairing(tmp_path, monkeypatch):
    """A confirmed comment remains repairable until its required local observation lands."""
    cfg = {**ON, **JOURNAL_ON, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    evidence, head = _review_chain(d, goal, "approve")
    evidence = pathlib.Path(evidence)
    real_append = work.ledger.safe_append
    monkeypatch.setattr(work.ledger, "safe_append", lambda *args, **kwargs:
                        None if args[1] == "review_posted" else real_append(*args, **kwargs))
    first = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": head})),
                     ("pr comment", "https://github.example/issues/7#issuecomment-42")])
    assert work.post_review(d, cfg, goal, run=first, verdict="approve", evidence=str(evidence)).startswith("PARK:")
    request = next((pathlib.Path(d) / "state" / "review-posts").glob("*.json"))
    assert not json.loads(request.read_text()).get("effects_repaired")
    monkeypatch.setattr(work.ledger, "safe_append", real_append)
    marker = "<!-- sigma-review-evidence:%s -->" % hashlib.sha256(evidence.read_bytes()).hexdigest()[:32]
    retry = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": head})),
                     ("issues/7/comments", json.dumps([{"id": 42, "body": marker}]))])
    assert work.post_review(d, cfg, goal, run=retry, verdict="approve", evidence=str(evidence)).startswith("posted")
    assert len([event for event in journal_events(_load("ledger"), d) if event.get("kind") == "review_posted"]) == 1


def test_concurrent_post_review_callers_cannot_both_create_the_request_receipt(tmp_path, monkeypatch):
    """Cycle-6 finding A. `created=True` is what licenses `gh pr comment`, so two callers that both
    get it post two comments. The old receipt was check-then-write: a second caller that looked
    BEFORE the winner wrote saw no file, overwrote it, and was also told it created it.

    Deterministic, at the exact seam rather than by racing threads: the winner creates the receipt
    for real, then the second caller is given the stale view it would have had inside that window.
    """
    d = _sdlc(tmp_path); goal = _started(d)
    evidence = pathlib.Path(d) / "evidence-a.json"
    evidence.write_text('{"goal": "a"}')
    first, path, created = work._review_post_request(d, goal, 7, "approve", "sigma:approve", str(evidence))
    assert created is True and path.exists()        # non-vacuity: the winner really created it

    real_exists = pathlib.Path.exists
    monkeypatch.setattr(pathlib.Path, "exists",
                        lambda self: False if self == path else real_exists(self))
    second, _, created_again = work._review_post_request(d, goal, 7, "approve", "sigma:approve",
                                                         str(evidence))
    assert created_again is False, "both callers were told they created the receipt -- both would post"
    assert second["observation_key"] == first["observation_key"]


def test_post_review_refuses_evidence_hand_written_outside_the_generation_chain(tmp_path):
    """Cycle-6 finding B. Evidence used to be trusted on its own fields: `goal`, `verdict`, `pr`,
    `head_sha`, and a `brief_sha256` that was only checked for being 64 hex characters -- never
    against a real manifest. So a maker could write an `approve` JSON anywhere and post it. This is
    byte-for-byte the shape the old `_post_review` test helper wrote for 25 tests: the suite was
    exercising the bypass as its normal path."""
    d = _sdlc(tmp_path); goal = _started(d)
    forged = pathlib.Path(d) / "state" / "test-review-evidence" / "0000.json"
    forged.parent.mkdir(parents=True)
    forged.write_text(json.dumps({"goal": goal, "verdict": "approve", "pr": 7,
                                  "head_sha": "a" * 40, "brief_sha256": "b" * 64}))
    run = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": "a" * 40}))])
    out = work.post_review(d, ON, goal, run=run, verdict="approve", evidence=str(forged))
    assert "posted" not in out, out
    assert not any("pr comment" in call for call in run.calls), "a forged approval reached GitHub"


def test_a_review_result_cannot_be_overwritten_once_written(tmp_path):
    """Cycle-6 finding B. The realistic forgery is not inventing a review from nothing -- it is the
    reviewer returning `block` and the maker rewriting that one file to `approve`. A result is now
    create-once for its generation: identical bytes are an idempotent retry, anything else refuses."""
    d = _sdlc(tmp_path)
    resolution = pathlib.Path(d) / "resolution.json"
    resolution.write_text('{"mechanism": "command"}')
    manifest = {"generation_id": "g" * 32, "brief_sha256": "b" * 64, "pr": 7, "head_sha": "a" * 40}
    first = work._write_review_result(d, manifest, str(resolution), "block", "real findings")
    assert json.loads(pathlib.Path(first["path"]).read_text())["verdict"] == "block"
    # Non-vacuity: an identical retry is accepted, so the refusal below is about the CHANGE.
    work._write_review_result(d, manifest, str(resolution), "block", "real findings")
    with pytest.raises(ValueError, match="immutable"):
        work._write_review_result(d, manifest, str(resolution), "approve", "looks fine")
    assert json.loads(pathlib.Path(first["path"]).read_text())["verdict"] == "block"


def _gesture_up_to_result(d, goal, monkeypatch, capsys, verdict="approve"):
    """The documented PR-review gesture, run through the real CLIs, up to a written result:
    `review_context.py brief --output`, `work.py review-paths`, then `record-subagent-review`."""
    rec = work._record(d, goal)
    _git_pr_worktree(rec["worktree"])
    manifest = pathlib.Path(d) / "state" / "review-manifests" / "0001-x.md.json"
    assert _load("review_context").main(["review_context.py", "brief", d, goal, "--for", "pr-review",
                                         "--artifact", "PR#7", "--repo-root", rec["worktree"],
                                         "--output", str(manifest)]) == 0
    capsys.readouterr()
    assert work.main(["work.py", "review-paths", d, goal, "--manifest", str(manifest), "--format", "sh"]) == 0
    paths = dict(line.split("=", 1) for line in capsys.readouterr().out.strip().splitlines())
    paths = {name: value.strip("'") for name, value in paths.items()}
    route = {"mechanism": "subagent", "host": "claude", "reason": "host dispatches subagents"}
    pathlib.Path(paths["REVIEW_RESOLUTION"]).write_text(json.dumps(route))
    original = work._load
    monkeypatch.setattr(work, "_load", lambda name: types.SimpleNamespace(resolve=lambda _d: route)
                        if name == "reviewer" else original(name))
    assert work.main(["work.py", "record-subagent-review", d, goal, "--manifest", str(manifest),
                      "--resolution", paths["REVIEW_RESOLUTION"], "--verdict", verdict,
                      "--reason", "review returned " + verdict]) == 0
    capsys.readouterr()
    return rec["worktree"], str(manifest), paths


def test_documented_gesture_refuses_evidence_when_the_head_moved_after_publication(tmp_path, monkeypatch, capsys):
    """Cycle-6 finding C, as the plan (line 121) specifies the control: run the exact documented
    commands, land a new commit between publication and `review-evidence`, assert a non-zero exit.
    The generation pinned one revision; a result reviewed against it must not become evidence for
    a head the reviewer never saw."""
    d = _sdlc(tmp_path); goal = _started(d)
    worktree, manifest, paths = _gesture_up_to_result(d, goal, monkeypatch, capsys)
    (pathlib.Path(worktree) / "late.txt").write_text("pushed after the review was published\n")
    subprocess.run(["git", "-C", worktree, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", worktree, "commit", "-qm", "late"], check=True, capture_output=True)
    assert work.main(["work.py", "review-evidence", d, goal, "--manifest", manifest,
                      "--review-result", paths["REVIEW_RESULT"]]) != 0
    assert not pathlib.Path(paths["REVIEW_EVIDENCE"]).exists()


def test_documented_gesture_binds_evidence_when_the_head_is_unchanged(tmp_path, monkeypatch, capsys):
    """Non-vacuity for the control above: the identical gesture with no late commit succeeds, so
    that refusal is about the moved head and not about a gesture that never works."""
    d = _sdlc(tmp_path); goal = _started(d)
    _, manifest, paths = _gesture_up_to_result(d, goal, monkeypatch, capsys)
    assert work.main(["work.py", "review-evidence", d, goal, "--manifest", manifest,
                      "--review-result", paths["REVIEW_RESULT"]]) == 0
    assert pathlib.Path(paths["REVIEW_EVIDENCE"]).exists()


def test_a_generation_publishes_nothing_when_the_head_moves_during_diff_capture(tmp_path, monkeypatch):
    """Cycle-6 finding C, the publish-time half: read head A, capture the diff, read head B, publish
    nothing if they differ. Deterministic at the seam: the worktree head reads as A and then as B."""
    d = _sdlc(tmp_path); goal = _started(d)
    worktree = work._record(d, goal)["worktree"]
    real_head = _git_pr_worktree(worktree)
    rc = _load("review_context")
    real_run, reads = subprocess.run, []

    def moving_head(argv, *args, **kwargs):
        if list(argv[-2:]) == ["rev-parse", "HEAD"]:
            reads.append(1)
            head = real_head if len(reads) == 1 else "f" * 40
            return subprocess.CompletedProcess(argv, 0, stdout=head + "\n", stderr="")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(rc.subprocess, "run", moving_head)
    manifest = pathlib.Path(d) / "state" / "review-manifests" / "0001-x.md.json"
    with pytest.raises(ValueError, match="moved"):
        rc.publish_generation(d, goal, "brief\n", "PR#7", str(manifest))
    assert not manifest.exists(), "a generation was published for a revision that was not stable"

def test_requested_receipt_sharing_is_refused_loudly_and_runs_as_the_shipped_default(monkeypatch, capsys):
    """`ledger.receipt_sharing: true` is REFUSED until #2680 makes the path work against real git.
    Refused here, at the one choke point every publish path consults, rather than inside
    `sync.publish_receipt`: with sharing on, `pr()` records the PR only once a receipt publishes,
    so refusing THERE would strand every goal of anyone who enabled it. Instead the core runs
    exactly as the shipped default, and says so loudly."""
    cfg = {"ledger": {"enabled": True, "receipt_sharing": True}}
    monkeypatch.setattr(work, "_RECEIPT_SHARING_REFUSAL_SHOWN", False)
    assert work._receipt_sharing_enabled(cfg) is False
    err = capsys.readouterr().err
    assert "receipt_sharing" in err and "#2680" in err and "REFUSED" in err
    # Non-vacuity: it is the support flag doing this, not a config the check cannot read.
    # `work._load` builds a fresh module per call, so patch what `work` itself loads.
    supported = _load("merge_observation"); supported.RECEIPT_SHARING_SUPPORTED = True
    original = work._load
    monkeypatch.setattr(work, "_load", lambda name: supported if name == "merge_observation" else original(name))
    assert work._receipt_sharing_enabled(cfg) is True
    monkeypatch.setattr(work, "_load", original)
    # And the default stays silent: nothing requested, nothing to refuse.
    monkeypatch.setattr(work, "_RECEIPT_SHARING_REFUSAL_SHOWN", False)
    assert work._receipt_sharing_enabled({"ledger": {"enabled": True}}) is False
    assert capsys.readouterr().err == ""

# --- Review round 3 (author-blind Claude subagent, generation accc7d5b at bc1f5da4) -------------------

def test_finish_releases_an_armed_auto_merge_pr_even_with_the_ledger_on(tmp_path):
    """B1. The PR had `finish()` KEEP an armed PR's worktree and work record whenever the ledger or
    journal was on, "until a later finish can confirm the merge" -- but nothing ever calls a later
    finish (the loop calls it once, at `record done`), so every armed PR leaked its checkout forever.
    Restored to the base behaviour landing.md documents: an armed PR releases normally. Observing
    the eventual armed merge needs a periodic sweep: #2683."""
    cfg = {**ON, "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    run = _runner([("state,autoMergeRequest", _pr_state(state="OPEN", auto_merge=True))])
    out = work.finish(d, cfg, goal, run=run)
    assert "retaining its observation record" not in out, out
    assert not work._record(d, goal), "the armed PR's work record was retained"


def test_inline_review_route_records_an_explicit_verdict_through_the_documented_cli(tmp_path, capsys):
    """B2. `review.independent: false` (and a host with no marker and no command) resolves INLINE:
    the operator chose to let the maker review its own work. The route still needs a documented way
    to produce evidence, or `post-review` can never succeed there -- `run-resolved-review` now takes
    `--verdict`/`--reason`, for the inline route only."""
    d = _sdlc(tmp_path, {**ON, "review": {"independent": False}}); goal = "2577"
    root = pathlib.Path(d) / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    (gen / "brief.md").write_text("brief")
    manifest = root / "review-manifests" / "2577.json"; manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"generation_id": "gen", "goal": goal, "brief": "review-generations/gen/brief.md",
                                    "brief_sha256": hashlib.sha256(b"brief").hexdigest()}))
    resolution = gen / "resolution.json"
    resolution.write_text(json.dumps(_load("reviewer").resolve(d)))
    assert json.loads(resolution.read_text())["mechanism"] == "inline"   # the REAL resolver said so
    assert work.main(["work.py", "run-resolved-review", d, goal, "--manifest", str(manifest),
                      "--resolution", str(resolution), "--verdict", "approve",
                      "--reason", "self-review, operator opted out of independence"]) == 0
    assert json.loads((root / "review-results" / "gen.json").read_text())["verdict"] == "approve"


def test_an_inline_resolution_cannot_bypass_a_configured_independent_reviewer(tmp_path):
    """The hole beside B2: the inline branch never re-resolved the live route. In a repo configured
    for an independent command reviewer, a maker could write `{"mechanism": "inline", "verdict":
    "approve"}` into resolution.json and approve its own PR. Inline must be what the resolver says
    NOW, exactly as the command route is re-checked."""
    d = _sdlc(tmp_path, {**ON, "review": {"host": "command", "command": "codex exec -"}}); goal = "2577"
    root = pathlib.Path(d) / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    (gen / "brief.md").write_text("brief")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"generation_id": "gen", "goal": goal, "brief": "review-generations/gen/brief.md",
                                    "brief_sha256": hashlib.sha256(b"brief").hexdigest()}))
    forged = gen / "resolution.json"
    forged.write_text(json.dumps({"mechanism": "inline", "reason": "x", "verdict": "approve"}))
    with pytest.raises(ValueError):
        work.run_resolved_review(d, goal, str(manifest), str(forged))
    with pytest.raises(ValueError):
        work.run_resolved_review(d, goal, str(manifest), str(forged), verdict="approve", reason="x")
    assert not (root / "review-results" / "gen.json").exists(), "a self-approval was recorded"


def test_documented_unblock_gesture_clears_a_prior_block_on_the_same_revision(tmp_path, monkeypatch, capsys):
    """B3. SKILL.md documents clearing a block on the same revision with `--verdict unblock`. It
    could not succeed: a generation's ID was a pure function of the brief and the revision, so a
    re-review of the same head landed on the SAME generation, whose create-once result already said
    `block`. A generation now also carries its creation time (plan line 87), so re-publishing the
    brief for the same revision yields a fresh generation that can take the new verdict."""
    d = _sdlc(tmp_path); goal = _started(d)
    worktree, manifest, first = _gesture_up_to_result(d, goal, monkeypatch, capsys, verdict="block")
    assert work.main(["work.py", "review-evidence", d, goal, "--manifest", manifest,
                      "--review-result", first["REVIEW_RESULT"]]) == 0
    capsys.readouterr()
    _, _, second = _gesture_up_to_result(d, goal, monkeypatch, capsys, verdict="unblock")
    assert second["REVIEW_GENERATION"] != first["REVIEW_GENERATION"]   # same head, fresh generation
    assert work.main(["work.py", "review-evidence", d, goal, "--manifest", manifest,
                      "--review-result", second["REVIEW_RESULT"]]) == 0
    head = subprocess.run(["git", "-C", worktree, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    run = _runner([("headRefOid", json.dumps({"number": 7, "headRefOid": head})),
                   ("pr comment", "https://github.example/issues/7#issuecomment-51")])
    out = work.post_review(d, ON, goal, run=run, verdict="unblock", evidence=second["REVIEW_EVIDENCE"])
    assert out.startswith("posted sigma:unblock"), out


def _process_review_output(tmp_path, monkeypatch, output):
    """Run the process route over a fixed reviewer transcript and return the parsed result."""
    root = tmp_path / ".sdlc" / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    (gen / "brief.md").write_text("brief")
    worktree = tmp_path / "worktree"; worktree.mkdir()
    manifest = root / "manifest.json"; manifest.write_text(json.dumps({
        "generation_id": "gen", "goal": "2577", "brief": "review-generations/gen/brief.md",
        "brief_sha256": hashlib.sha256(b"brief").hexdigest()}))
    route = {"mechanism": "process", "host": "codex", "command": ["codex", "exec"]}
    resolution = gen / "resolution.json"; resolution.write_text(json.dumps(dict(route, reason="resolved")))
    fake = types.SimpleNamespace(resolve=lambda _sdlc: route, run_review=lambda *_a: (print(output) or 0))
    original = work._load
    monkeypatch.setattr(work, "_load", lambda name: fake if name == "reviewer" else original(name))
    work._save(tmp_path / ".sdlc", "2577", {"worktree": str(worktree), "pr": 7})
    return work.run_resolved_review(tmp_path / ".sdlc", "2577", manifest, resolution)


def test_the_verdict_is_the_reviewers_final_standalone_line_not_its_first_mention(tmp_path, monkeypatch):
    """S1. The parser took the FIRST line starting "verdict:", so prose such as "Verdict: approve
    would be premature" ahead of a final `VERDICT: block` recorded an APPROVAL -- a checker that
    could flip its own block. Only a line that is nothing BUT the verdict counts."""
    out = ("Verdict: approve would be premature until the race is fixed.\n"
           "Findings: B1 the receipt write races.\n\nVERDICT: block\n")
    assert _process_review_output(tmp_path, monkeypatch, out)["verdict"] == "block"


def test_conflicting_standalone_verdicts_are_refused(tmp_path, monkeypatch):
    """Two different standalone verdict lines are ambiguous; picking either would be a guess."""
    with pytest.raises(ValueError, match="conflicting"):
        _process_review_output(tmp_path, monkeypatch, "VERDICT: approve\n...\nVERDICT: block\n")


def test_confirmed_merge_makes_no_network_call_when_both_sinks_are_off(tmp_path):
    """S5. With the ledger and journal both off there is nothing to deliver, so the shipped default
    must not spend a GitHub REST call -- nor write a delivery receipt -- on every merge."""
    cfg = {**ON, "ledger": {"enabled": False}, "journal": {"enabled": False}}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    run = _runner([])
    work._record_confirmed_merge(d, cfg, goal, work._record(d, goal), run, "merged")
    assert run.calls == [], run.calls
    assert not (pathlib.Path(d) / "state" / "merge-deliveries").exists()


def test_a_merge_delivery_write_error_never_fails_a_successful_merge(tmp_path, monkeypatch):
    """S5. "A ledger problem must never turn a successful merge into a failure": a delivery-receipt
    write that raises OSError (full disk, read-only state dir) must be reported, not raised."""
    cfg = {**ON, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    raw = json.dumps({"number": 7, "node_id": "PR_7", "created_at": "2026-01-01T00:00:00Z",
                      "merged_at": "2026-01-02T00:00:00Z", "merge_commit_sha": "a" * 40,
                      "head": {"ref": "sdlc/2577"}, "base": {"ref": "main", "repo": {"node_id": "R_1"}}})
    def boom(*_a, **_k):
        raise OSError("read-only file system")
    monkeypatch.setattr(work, "_write_merge_delivery", boom)
    work._record_confirmed_merge(d, cfg, goal, work._record(d, goal),
                                 _runner([("api repos/{owner}/{repo}/pulls/7", raw)]), "merged")


def test_review_evidence_reports_the_evidence_id_post_review_actually_posts(tmp_path, monkeypatch, capsys):
    """S3. `review-evidence` printed sha256(raw)[:32] while post_review, the PR-comment marker and
    the journal all key on sha256(the evidence FILE's bytes)[:32] -- two values under one name."""
    d = _sdlc(tmp_path); goal = _started(d)
    _, manifest, paths = _gesture_up_to_result(d, goal, monkeypatch, capsys)
    bound = work.review_evidence(d, goal, manifest, paths["REVIEW_RESULT"])
    assert bound["evidence_id"] == hashlib.sha256(pathlib.Path(bound["path"]).read_bytes()).hexdigest()[:32]

def test_post_review_block_carries_the_reasons(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    run = _runner([])
    _post_review(d, ON, goal, run, "block", "missing null check in the parser")
    posted = next(c for c in run.calls if "pr comment" in c)
    assert "sigma:block" in posted and "missing null check" in posted


def test_post_review_block_reason_is_scrubbed_before_the_public_pr_comment(tmp_path):
    # F2: the SAME reason is scrubbed on the ledger-event path (test_ledger.py) but was posted RAW to
    # the public PR comment — an oversight, not a decision. A secret/client string quoted from the diff
    # in a review note must never reach the public comment body.
    d = _sdlc(tmp_path); goal = _started(d)
    run = _runner([])
    _post_review(d, ON, goal, run, "block",
                 "leaked AKIAIOSFODNN7EXAMPLE and acme.example.com KEY-123")
    posted = next(c for c in run.calls if "pr comment" in c)
    assert "AKIAIOSFODNN7EXAMPLE" not in posted
    assert "[REDACTED:aws-key]" in posted


def test_post_review_rejects_a_bad_verdict(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    run = _runner([])
    assert "approve" in work.post_review(d, ON, goal, run=run, verdict="maybe")
    assert not any("pr comment" in c for c in run.calls)         # nothing posted on a bad verdict


def test_post_review_needs_a_pr_first(tmp_path):
    d = _sdlc(tmp_path)
    wt = pathlib.Path(d).parent / ".sdlc" / "work" / "0001-x"; wt.mkdir(parents=True, exist_ok=True)
    work._save(d, "0001-x.md", {"worktree": str(wt), "branch": "sdlc/0001-x", "base": "main", "remote": "origin"})
    assert "no PR" in work.post_review(d, ON, "0001-x.md", run=_runner([]), verdict="approve")


def test_post_review_is_a_registered_verb():
    assert "post-review" in work._COMMANDS and work._COMMANDS["post-review"] is work.post_review


def test_post_review_block_counts_cycles(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d); run = _runner([])
    _post_review(d, ON, goal, run, "block", "x")
    assert work._record(d, goal)["review_cycles"] == 1
    _post_review(d, ON, goal, run, "block", "y")
    assert work._record(d, goal)["review_cycles"] == 2


def test_post_review_approve_does_not_count_a_cycle(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d); run = _runner([])
    _post_review(d, ON, goal, run, "approve")
    assert work._record(d, goal).get("review_cycles", 0) == 0


def test_post_review_block_does_not_consume_a_cycle_when_the_comment_fails_to_post(tmp_path):
    """#2132: `gh pr comment` failing (GitHub's *secondary* content-creation rate limit -- invisible
    to `gh api rate_limit` -- or any other transient error) must not cost a review cycle. The old
    code persisted the increment BEFORE the post attempt, so a retry loop around a transient failure
    (reasonable, since nothing documented the call as non-idempotent) silently burned the cap with no
    decrement and no sanctioned lever back except hand-editing state/work/<goal>.json."""
    d = _sdlc(tmp_path); goal = _started(d)
    run = _runner([("pr comment", RuntimeError("API rate limit already exceeded for user ID ..."))])
    out = _post_review(d, ON, goal, run, "block", "x")
    assert out.startswith("PARK: remote comment outcome ambiguous")
    assert work._record(d, goal).get("review_cycles", 0) == 0    # and the state agrees: NOT charged


def test_post_review_a_retry_after_a_failed_post_counts_exactly_one_cycle(tmp_path):
    """The retry itself must not double-count: a failed attempt leaves nothing behind, so a
    SUCCESSFUL retry of the same block is cycle 1, not 2."""
    d = _sdlc(tmp_path); goal = _started(d)
    failing = _runner([("pr comment", RuntimeError("secondary rate limit"))])
    assert _post_review(d, ON, goal, failing, "block", "x").startswith("PARK:")
    assert work._record(d, goal).get("review_cycles", 0) == 0
    ok = _runner([])
    out = _post_review(d, ON, goal, ok, "block", "x")
    assert out.startswith("posted")
    assert work._record(d, goal)["review_cycles"] == 1


def test_post_review_hard_caps_the_cycles_and_parks(tmp_path):
    cfg = {"work": {"enabled": True, "max_review_cycles": 2}}
    d = _sdlc(tmp_path, cfg); goal = _started(d); run = _runner([])
    assert _post_review(d, cfg, goal, run, "block", "a").startswith("posted")
    out = _post_review(d, cfg, goal, run, "block", "b")   # 2nd block hits cap=2
    assert out.startswith("PARK:") and "did not converge" in out
    assert "NOT converged" in [c for c in run.calls if "pr comment" in c][-1]    # the final comment says so


def test_post_review_default_cap_is_three(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d); run = _runner([])                   # ON has no cap → default 3
    for i in range(2):
        assert _post_review(d, ON, goal, run, "block", str(i)).startswith("posted")
    assert _post_review(d, ON, goal, run, "block", "3rd").startswith("PARK:")


def test_post_review_zero_cap_normalizes_to_the_default_instead_of_disabling_it(tmp_path):
    """F20/#343: `cap and cycles >= cap` short-circuits false the instant `cap` is exactly 0, so a
    configured `max_review_cycles: 0` used to read as "no cap" — six-plus consecutive blocks all
    "posted" with nothing ever parking, the exact runaway this gate exists to prevent. `0` now
    normalizes to the documented default (3), same as an absent key."""
    cfg = {"work": {"enabled": True, "max_review_cycles": 0}}
    d = _sdlc(tmp_path, cfg); goal = _started(d); run = _runner([])
    for i in range(2):
        assert _post_review(d, cfg, goal, run, "block", str(i)).startswith("posted")
    posted = [c for c in run.calls if "pr comment" in c][-1]
    assert "cycle 2/3" in posted                        # the effective cap is the default 3, not 0
    out = _post_review(d, cfg, goal, run, "block", "3rd")
    assert out.startswith("PARK:") and "did not converge" in out


def test_post_review_negative_cap_normalizes_to_the_default_instead_of_parking_on_the_first_block(tmp_path):
    """F20/#343: a negative `max_review_cycles` failed the opposite way — `cap and cycles >= cap` goes
    true the moment `cycles` first reaches 1, parking on the very first block with zero fix attempts.
    A negative value normalizes to the same documented default (3) as zero and anything else < 1."""
    cfg = {"work": {"enabled": True, "max_review_cycles": -1}}
    d = _sdlc(tmp_path, cfg); goal = _started(d); run = _runner([])
    out = _post_review(d, cfg, goal, run, "block", "a")
    assert out.startswith("posted")                     # NOT parked on the first block
    assert work._record(d, goal)["review_cycles"] == 1


# --------------------------------------------------------------------------- #141 amendment A
# `post-review` is a synchronous, agent-typed CLI verb — the same shape as `loop.py emit`/`spend`
# — so a newline in `--reason` is a HARD REJECT at the CLI (main()), before `post_review()` is
# ever dispatched. This is deliberately checked in `main()`, NOT inside `post_review()` itself:
# a direct `work.post_review(..., reason="a\nb")` call (as every test above does) must still only
# flatten (via ledger.append()'s automatic treatment), never reject — see test_ledger.py's
# `test_append_flattens_a_raw_newline_in_park_why`-style guarantee for why that matters.


def test_cli_post_review_rejects_a_newline_in_reason(tmp_path, capsys):
    d = _sdlc(tmp_path); goal = _started(d)
    rc = work.main(["work.py", "post-review", d, goal, "--verdict", "block",
                     "--reason", "line one\nline two"])
    assert rc == 2
    assert "newline" in capsys.readouterr().err
    assert work._record(d, goal).get("review_cycles", 0) == 0   # post_review never dispatched


def test_cli_post_review_requires_evidence_even_for_a_single_line_reason(tmp_path, capsys, monkeypatch):
    """A syntactically safe reason is still not postable without generation-bound evidence."""
    d = _sdlc(tmp_path); goal = _started(d)
    monkeypatch.setattr(work, "_run", lambda cwd, argv: "")
    rc = work.main(["work.py", "post-review", d, goal, "--verdict", "block",
                     "--reason", "a single line reason"])
    assert rc == 2
    assert "requires --evidence" in capsys.readouterr().err


def test_cli_post_review_newline_message_matches_loop_pys_shared_helper(tmp_path, capsys):
    """POST-REVIEW FIX (retrospective item E): the newline-reject rule used to be hand-written
    TWICE — here in `work.py`'s `main()`, and again in `loop.py`'s `_validate_event` — with near-
    identical but unshared wording. Both now call the ONE shared `ledger.reject_newline(value,
    label)` helper in `ledger.py`. This proves `work.py`'s real CLI output for a newline in
    `--reason` is exactly what that shared helper produces — not a second hand-written string that
    merely happens to also contain the word "newline" — closing the drift a fourth CLI verb could
    otherwise reintroduce."""
    d = _sdlc(tmp_path); goal = _started(d)
    rc = work.main(["work.py", "post-review", d, goal, "--verdict", "block",
                     "--reason", "line one\nline two"])
    assert rc == 2
    err = capsys.readouterr().err
    expected = work.ledger.reject_newline("line one\nline two", "--reason")
    assert err == f"work: {expected}\n"


# --------------------------------------------------------------------------- #1902: commit/pr/
# post-review/merge keep the ledger watcher and its delivery check alive too, not just goal-pick
# time (loop.py's own start/next/next-batch). See tests/test_loop.py for the `verify` half of
# this fix, and each function's own unit tests for what it does in isolation — this only tests
# that these four verbs' CLI dispatch actually calls both.
#
# #2578 CUT THE BLOCK FROM FIVE TO TWO: the three private-side starters #2578 removed started the
# private side's daemons, and the core no longer starts those at all
# -- the git hooks are the only revival path now.

def _spy_on_ensure_calls(monkeypatch):
    """Patch `work._load` itself, not some independently-obtained `loop` module — `_load()` has no
    `sys.modules` cache (grep finds zero references to it in either file), so it re-`exec_module`s
    a brand-new module object on every call. Patching a separately-obtained `_load("loop")` result
    has zero effect on the copy `work.py`'s own dispatch creates for itself with its own,
    independent `_load("loop")` call. This is the same pattern
    `test_start_still_resumes_once_a_dead_pickers_own_registered_worker_has_also_died` above
    already uses for `_goal_has_registered_worker` (see its own docstring) — reused here for the
    two attribute names on the same lazily-loaded module that survive #2578.

    #2578 DELETED THREE SPY LINES HERE, AND NOTHING MECHANICAL WOULD HAVE FORCED THAT. Plain
    attribute assignment on a module object succeeds whether or not the attribute exists, so a
    stale `mod.<removed starter> = ...` naming a symbol `loop.py` no longer defines is
    tolerated in silence: measured, leaving the three behind and fixing the expectations below
    still gives 4 passed. The expectations are what carry the real assertion -- they compare the
    recorded CALLS list, which is why deleting work.py's three call sites with the
    expectations unchanged gives `4 failed, 436 deselected`, not a silent pass."""
    calls = []
    real_load = work._load

    def spying_load(name):
        mod = real_load(name)
        if name == "loop":
            mod._ensure_watcher = lambda *a, **k: calls.append("watcher")
            mod._ensure_ledger_delivery = lambda *a, **k: calls.append("ledger-delivery")
        return mod

    monkeypatch.setattr(work, "_load", spying_load)
    return calls


def test_cli_commit_triggers_both_ensure_functions(tmp_path, monkeypatch):
    d = _sdlc(tmp_path); goal = _started(d)
    monkeypatch.setattr(work, "_run", lambda cwd, argv: "")   # no real git/gh call
    calls = _spy_on_ensure_calls(monkeypatch)
    work.main(["work.py", "commit", d, goal, "--message", "sdlc: x"])
    assert calls == ["watcher", "ledger-delivery"]


def test_cli_pr_triggers_both_ensure_functions(tmp_path, monkeypatch):
    d = _sdlc(tmp_path); goal = _started(d)
    monkeypatch.setattr(work, "_run", lambda cwd, argv: "")
    calls = _spy_on_ensure_calls(monkeypatch)
    work.main(["work.py", "pr", d, goal])
    assert calls == ["watcher", "ledger-delivery"]


def test_cli_post_review_triggers_both_ensure_functions(tmp_path, monkeypatch):
    d = _sdlc(tmp_path); goal = _started(d)
    monkeypatch.setattr(work, "_run", lambda cwd, argv: "")
    calls = _spy_on_ensure_calls(monkeypatch)
    work.main(["work.py", "post-review", d, goal, "--verdict", "approve"])
    assert calls == ["watcher", "ledger-delivery"]


def test_cli_merge_triggers_both_ensure_functions(tmp_path, monkeypatch):
    d = _sdlc(tmp_path); goal = _started(d)
    monkeypatch.setattr(work, "_run", lambda cwd, argv: "")
    calls = _spy_on_ensure_calls(monkeypatch)
    work.main(["work.py", "merge", d, goal])
    assert calls == ["watcher", "ledger-delivery"]


def test_cli_other_verbs_do_not_trigger_ensure_functions(tmp_path, monkeypatch):
    """Scope guard: the new gate is verb-specific (commit/pr/post-review/merge only), not "every
    dispatch". `start` already runs moments after loop.py's own pick-time ensure calls in the same
    session; `finish`/`rebase` are outside #1902's named scope. One test covering all three keeps
    the guard's shape ("NOT these verbs") visible as a single assertion rather than three near-
    duplicate test bodies."""
    d = _sdlc(tmp_path); goal = _started(d)
    monkeypatch.setattr(work, "_run", lambda cwd, argv: "")
    for verb, extra in (("start", []), ("finish", []), ("rebase", [])):
        calls = _spy_on_ensure_calls(monkeypatch)
        work.main(["work.py", verb, d, goal, *extra])
        assert calls == [], f"{verb!r} must not trigger the ensure functions, got {calls!r}"


def test_cli_commit_still_succeeds_when_the_loop_module_cannot_be_loaded(tmp_path, monkeypatch):
    """Fail-open, proven rather than asserted: a hypothetical failure to even `_load("loop")` (a
    corrupt file, a missing sibling script) must degrade to "no watcher was started this time," not
    "the commit itself failed" — the same posture `_ensure_watcher`'s own docstring states ("a
    watcher we cannot start must never stop a run"), extended here to cover the lazy cross-load
    itself, which sits outside each `_ensure_*` function's own internal try/except."""
    d = _sdlc(tmp_path); goal = _started(d)
    monkeypatch.setattr(work, "_run", lambda cwd, argv: "")

    def boom(name):
        raise RuntimeError("simulated: loop.py could not be loaded")
    monkeypatch.setattr(work, "_load", boom)

    out = work.main(["work.py", "commit", d, goal, "--message", "sdlc: x"])
    assert out == 0


# --------------------------------------------------------------------------- #139 Slice 2: events
# Site c (post_review -> gate{post_review}), site d (gate/merge -> gate{merge}), site e
# (review_gate -> gate{code_review}). All need ledger.enabled AND journal.enabled — the Slice 0
# AND-gate — to actually land a write; see test_ledger.py's gate tests for the gate itself.

JOURNAL_ON = {"ledger": {"enabled": True, "actor": "rae"}, "journal": {"enabled": True}}


def _gate_events(entries, gate_name):
    return [e for e in entries if e.get("kind") == "gate" and e.get("gate") == gate_name]


def test_post_review_approve_emits_a_pass_gate_event_with_no_cycle(tmp_path):
    ledger = _load("ledger")
    cfg = {**ON, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    _post_review(d, cfg, goal, _runner([]), "approve")
    events = _gate_events(journal_events(ledger, d), "post_review")
    assert len(events) == 1
    assert events[0]["verdict"] == "pass"
    assert "cycle" not in events[0]


def test_post_review_block_emits_a_block_gate_event_with_the_cycle_count(tmp_path):
    """The metric this task exists to unlock: `review_cycles` currently only reaches
    `state/work/<goal>.json`, which `work.py finish` deletes — the event is what makes it survive."""
    ledger = _load("ledger")
    cfg = {**ON, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); run = _runner([])
    _post_review(d, cfg, goal, run, "block", "missing null check")
    events = _gate_events(journal_events(ledger, d), "post_review")
    assert len(events) == 1 and events[0]["verdict"] == "block" and events[0]["cycle"] == 1
    _post_review(d, cfg, goal, run, "block", "still broken")
    events = _gate_events(journal_events(ledger, d), "post_review")
    assert events[-1]["cycle"] == 2


def test_merge_emits_a_pass_gate_event_on_a_clean_and_safe_merge(tmp_path):
    ledger = _load("ledger")
    cfg = {**ALWAYS, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    events = _gate_events(journal_events(ledger, d), "merge")
    assert len(events) == 1 and events[0]["verdict"] == "pass"


def test_merge_emits_a_block_gate_event_with_the_verdict_as_why(tmp_path):
    ledger = _load("ledger")
    cfg = {**ALWAYS, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + [("pr view", _view(mergeable="CONFLICTING"))])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: conflicts with the base branch")
    events = _gate_events(journal_events(ledger, d), "merge")
    assert len(events) == 1 and events[0]["verdict"] == "block"
    assert "conflicts with the base branch" in events[0]["why"]


def test_merge_logs_a_warn_gate_event_when_arming_on_pending_checks(tmp_path):
    """#254 task 3: arming despite pending checks is neither a clean `pass` (gate() itself still
    said not-ok) nor a `block` (merge() proceeded anyway) -- `warn` is the honest third verdict,
    already in `ledger.VERDICTS`, for "proceeded, but flagging a caveat"."""
    ledger = _load("ledger")
    cfg = {**ALWAYS, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci", "slow")) + _auto_merge_allowed(True)
                  + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed")
    events = _gate_events(journal_events(ledger, d), "merge")
    assert len(events) == 1 and events[0]["verdict"] == "warn"
    assert "pending" in (events[0].get("why") or "").lower()


def test_merge_review_gate_emits_code_review_gate_event_when_require_review_is_on(tmp_path):
    cfg = {"work": {"enabled": True, "auto_merge": "always", "require_review": "approval"}, **JOURNAL_ON}
    ledger = _load("ledger")
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _review(decision="APPROVED") + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert not out.startswith("PARK")
    events = _gate_events(journal_events(ledger, d), "code_review")
    assert len(events) == 1 and events[0]["verdict"] == "pass"


def test_merge_emits_no_code_review_event_when_require_review_is_off(tmp_path):
    """Proves the `review_mode(config) != REVIEW_OFF` guard actually suppresses it — `review_gate`
    itself returns (True, "") uniformly for both 'mode off' and 'on and clean', so without the
    guard an off repo would wrongly log a pass event for a gate that never ran."""
    ledger = _load("ledger")
    cfg = {**ALWAYS, **JOURNAL_ON}       # require_review unset -> off
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert _gate_events(journal_events(ledger, d), "code_review") == []


def test_work_survives_a_raising_ledger_append(tmp_path, monkeypatch):
    """The module's fail-open test: would fail if post_review/merge/review_gate ever called
    `ledger.append` directly instead of `ledger.safe_append`."""
    cfg = {**ALWAYS, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])

    def raiser(*a, **k):
        raise RuntimeError("ledger broke")
    monkeypatch.setattr(work.ledger, "append", raiser)

    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    out2 = _post_review(d, cfg, goal, _runner([]), "approve")
    assert out2.startswith("PARK: review comment posted but local effects are pending")


# --------------------------------------------------------------------------- stale head (#197)


def test_gate_refuses_when_the_pr_head_is_not_what_we_reviewed(tmp_path):
    """The trap that shipped defects to a protected main three times in one run.

    `work.py commit` is LOCAL; only `work.py pr` pushes. A fix made after a review block can leave
    the PR head at the pre-fix commit, and GitHub then answers CLEAN with every required check
    green -- about code nobody approved. Each signal is correct; each is about the wrong tree.
    """
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _view(head="a" * 40)), ("rev-parse HEAD", "b" * 40)])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok
    assert "STALE HEAD" in verdict
    assert "aaaaaaa" in verdict and "bbbbbbb" in verdict, "name both heads -- a bare refusal is unactionable"
    assert "work.py pr" in verdict, "say the command that fixes it"


def test_gate_allows_a_head_that_matches(tmp_path):
    """The guard must not block the normal path."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", _view())]), sleep=NOSLEEP)
    assert ok and verdict == "clean and safe"


def test_gate_fails_closed_when_a_head_is_unreadable(tmp_path):
    """A head we cannot read is not evidence of a fresh one. Both directions refuse."""
    d = _sdlc(tmp_path)
    goal = _started(d)

    no_local = _runner([("pr view", _view()), ("rev-parse HEAD", "")])
    ok, verdict, _ = work.gate(d, ON, goal, run=no_local, sleep=NOSLEEP)
    assert not ok and "local branch tip read back empty" in verdict

    no_remote = _runner([("pr view", _view(head=""))])
    ok, verdict, _ = work.gate(d, ON, goal, run=no_remote, sleep=NOSLEEP)
    assert not ok and "did not report headRefOid" in verdict


# --- #1240 review: gate()/_reconcile_behind()'s own mechanical park verdicts must never read as ---
# --- genuine human decision prose to loop.py's decision_tier gate --------------------------------

def test_mechanical_park_verdicts_are_excluded_from_decision_tier_end_to_end(tmp_path, monkeypatch):
    """#1185 widened loop.py's decision_tier gate to reason_class "unknown" so real, agent-typed
    decision prose (which matches none of `_REASON_CLASS_RULES`'s ~20 fixed substrings) could reach
    it. Independent review of #1185 caught that gate()'s own "could not compute mergeability"
    message ALSO reaches "unknown" the same way -- and #1185 excluded that ONE case with a needle
    hardcoded a second time in loop.py. Independent review of #1240 then found FIVE more of
    gate()/_reconcile_behind()'s own machine-generated PARK verdicts with the identical shape,
    which that single hardcoded needle never covered: an unreadable `gh pr view`, an unreadable
    local branch tip, a missing headRefOid on either side, and an exhausted BEHIND rebase race.

    This drives gate() and _reconcile_behind() for REAL (no hand-typed duplicate of their wording,
    so this can't itself drift from the actual source) to produce three of those five verdicts,
    then feeds each one straight into loop.py's real `_record()` -- proving, end to end, that
    `work.MECHANICAL_PARK_PREFIXES` (the single source of truth both files now read) keeps every
    one of them a plain, tier-less park instead of a fabricated "needs human judgment" tier on an
    ordinary infra hiccup. A raising spy on `decision_tier.resolve()` proves the call genuinely
    never happens for any of them, not just that its result goes unused."""
    loop = _load("loop")
    cfg = {**ON, "ledger": {"enabled": True, "actor": "rae"}, "journal": {"enabled": True},
           "decision_tier": "auto"}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d)

    # Site work.py:423 -- `gh pr view` keeps raising past every retry.
    ok, verdict_pr_state, _ = work.gate(
        d, ON, goal, run=_runner([("pr view", RuntimeError("gh: HTTP 502 Bad Gateway"))]),
        sleep=NOSLEEP)
    assert not ok and "could not read PR state" in verdict_pr_state

    # Site work.py:441 -- the local branch tip reads back empty.
    ok, verdict_no_local_head, _ = work.gate(
        d, ON, goal, run=_runner([("pr view", _view()), ("rev-parse HEAD", "")]), sleep=NOSLEEP)
    assert not ok and "local branch tip read back empty" in verdict_no_local_head

    # Site work.py:714 -- a BEHIND race that never settles inside the rebase budget. Every `gh pr
    # view` reports BEHIND, so gate() keeps handing _reconcile_behind() a fresh BEHIND to rebase
    # against until BEHIND_REBASES is exhausted -- `git rebase`/`git push` are unmatched substrings
    # here and default to "" (success) per `_runner`'s own contract, so every rebase "succeeds".
    kind, verdict_behind_exhausted = work._reconcile_behind(
        d, ON, goal, run=_runner([("pr view", _view(status="BEHIND"))]), sleep=NOSLEEP)
    assert kind == "park" and "BEHIND race did not settle after" in verdict_behind_exhausted

    def boom(*a, **k):
        raise AssertionError("decision_tier.resolve() must not be called for a mechanical "
                              "work.py park detail")
    monkeypatch.setattr(loop.decision_tier, "resolve", boom)

    class _Sink:
        def __init__(self):
            self.parked = []

        def complete(self, g):
            pass

        def fail(self, g, r):
            pass

        def park(self, g, r, tier=None):
            self.parked.append((g, r, tier))

    for i, verdict in enumerate((verdict_pr_state, verdict_no_local_head, verdict_behind_exhausted)):
        assert loop._reason_class(verdict) == "unknown"      # sanity: same bucket as free text
        goal_i = f"g{i}.md"
        sink = _Sink()
        loop._record(d, sink, goal_i, "parked", verdict)     # must not raise (spy proves no call)
        events = [e for e in journal_events(loop.ledger, d)
                  if e["kind"] == "park" and e["goal"] == goal_i]
        assert len(events) == 1
        assert events[0]["reason_class"] == "unknown"
        assert "decision_tier" not in events[0]               # no fabricated tier
        assert sink.parked == [(goal_i, verdict, None)]       # tier kwarg never a real value


def test_gate_checks_the_head_before_believing_any_github_verdict(tmp_path):
    """Ordering is the point: with a stale head, CONFLICTING/BEHIND/failing-check are all answers
    about the wrong tree, so the head check must come first rather than as a late tie-break."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _view("CONFLICTING", "DIRTY", head="e" * 40)),
                   ("rev-parse HEAD", "f" * 40)])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok
    assert "STALE HEAD" in verdict, "the head check must win over the conflict report"


# --------------------------------------------------------------------------- local-only action log (#463)
# One regression test per Python-layer call site in work.py — a future edit that quietly drops the
# actionlog.safe_append call at one of these sites should fail a test, not go unnoticed (matches
# this repo's own "hardened-sibling-divergence" concern, see plan section 5 §7). Every test here
# also proves the ledger stays byte-identical when both features are on together, except the final
# dedicated proof below, which is the acceptance criteria's own required, stronger version.


def test_start_emits_worktree_start_to_the_action_log(tmp_path):
    cfg = {**ON, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg)
    run = _runner([("rev-parse", "main")])
    work.start(d, cfg, "0001-x.md", run=run)
    entries = actionlog.read_goal(d, "0001-x.md")
    hits = [e for e in entries if e["kind"] == "worktree_start"]
    assert len(hits) == 1
    assert hits[0]["branch"] == "sdlc/0001-x"
    assert "0001-x" in hits[0]["worktree"]


def test_start_reattach_also_emits_worktree_start(tmp_path):
    """The `branch outlived its record` fallback path is the OTHER path that converges on the same
    `_save()` call `start()`'s docstring names (plan section 5 §1) — not just the direct-success add
    -b path the test above already covers."""
    cfg = {**ON, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg)
    run = _runner([("rev-parse", "main"), ("worktree add -b", RuntimeError("already exists"))])
    work.start(d, cfg, "0001-x.md", run=run)
    entries = actionlog.read_goal(d, "0001-x.md")
    assert len([e for e in entries if e["kind"] == "worktree_start"]) == 1


def test_start_resume_does_not_re_emit_worktree_start(tmp_path):
    """The `already started` idempotent-resume early return cuts no new worktree and isn't a new
    mechanical action worth logging again (plan section 5 §1) — must NOT double the entry a prior
    `start()` call already wrote."""
    cfg = {**ON, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d)
    run = _runner([])
    assert "already started" in work.start(d, cfg, goal, run=run)
    entries = actionlog.read_goal(d, goal)
    assert [e for e in entries if e["kind"] == "worktree_start"] == []


def test_merge_emits_gate_for_merge_and_code_review_to_the_action_log(tmp_path):
    cfg = {"work": {"enabled": True, "auto_merge": "always", "require_review": "approval"}, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _review(decision="APPROVED") + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged"), out
    entries = actionlog.read_goal(d, goal)
    gates = {e["gate"]: e["verdict"] for e in entries if e["kind"] == "gate"}
    assert gates == {"merge": "pass", "code_review": "pass", "test_trust": "pass"}


def test_merge_emits_a_block_gate_to_the_action_log_with_why(tmp_path):
    cfg = {**ALWAYS, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + [("pr view", _view(mergeable="CONFLICTING"))])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: conflicts with the base branch")
    entries = actionlog.read_goal(d, goal)
    hits = [e for e in entries if e["kind"] == "gate" and e.get("gate") == "merge"]
    assert len(hits) == 1 and hits[0]["verdict"] == "block"
    assert "conflicts with the base branch" in hits[0]["why"]


def test_merge_emits_no_code_review_entry_to_the_action_log_when_require_review_is_off(tmp_path):
    cfg = {**ALWAYS, **ACTIONLOG}          # require_review unset -> off
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    entries = actionlog.read_goal(d, goal)
    assert [e for e in entries if e["kind"] == "gate" and e.get("gate") == "code_review"] == []


def test_merge_emits_merge_armed_to_the_action_log(tmp_path):
    cfg = {**ALWAYS, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci", "slow")) + _auto_merge_allowed(True)
                  + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed")
    entries = actionlog.read_goal(d, goal)
    hits = [e for e in entries if e["kind"] == "merge_armed"]
    assert len(hits) == 1 and hits[0]["pr"] == "7"


def test_merge_emits_merged_to_the_action_log_on_a_direct_landing(tmp_path):
    cfg = {**ALWAYS, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    entries = actionlog.read_goal(d, goal)
    hits = [e for e in entries if e["kind"] == "merged"]
    assert len(hits) == 1 and hits[0]["pr"] == "7"


def test_post_review_emits_gate_to_the_action_log(tmp_path):
    cfg = {**ON, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    _post_review(d, cfg, goal, _runner([]), "block", "missing null check")
    entries = actionlog.read_goal(d, goal)
    hits = [e for e in entries if e["kind"] == "gate" and e.get("gate") == "post_review"]
    assert len(hits) == 1 and hits[0]["verdict"] == "block"
    assert "cycle" not in hits[0]     # actionlog's `gate` kind has no `cycle` field — unlike the ledger's


def test_post_review_approve_emits_a_pass_gate_to_the_action_log(tmp_path):
    cfg = {**ON, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    _post_review(d, cfg, goal, _runner([]), "approve")
    entries = actionlog.read_goal(d, goal)
    hits = [e for e in entries if e["kind"] == "gate" and e.get("gate") == "post_review"]
    assert len(hits) == 1 and hits[0]["verdict"] == "pass"


def test_actionlog_survives_a_raising_actionlog_append(tmp_path, monkeypatch):
    """The module's own fail-open guarantee, proven from the work.py side: a raising
    `actionlog.append` must never break `start`/`merge`/`post_review` — mirrors
    `test_work_survives_a_raising_ledger_append` above for the local action log."""
    cfg = {**ALWAYS, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])

    def raiser(*a, **k):
        raise RuntimeError("actionlog broke")
    monkeypatch.setattr(actionlog, "append", raiser)

    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged")
    out2 = _post_review(d, cfg, goal, _runner([]), "approve")
    assert "posted sigma:approve" in out2


def test_ledger_is_byte_identical_across_a_full_actionlog_instrumented_sequence(tmp_path, monkeypatch):
    """THE load-bearing proof (plan section 5, acceptance criteria — the user's explicit "must
    never touch the ledger" constraint): run one identical scripted sequence — claim, worktree
    start, verify, the merge/code_review/post_review gates, merge-armed, and the final record —
    TWICE in the same `.sdlc` directory, with the ledger *on* both times (so a cross-write would be
    observable; an off ledger trivially also "looks like" no change) and `action_log` OFF the first
    time, ON the second. Assert the ledger ends up byte-identical either way — the only thing that
    changed between the two runs is whether actionlog was ALSO writing.

    NOT a naive before/after snapshot around one single run: the ledger is SUPPOSED to grow from
    its own real, legitimate writes during the sequence (claimed, verify, three gates, done) — a
    plain "assert the ledger is unchanged by the whole sequence" would be false by construction and
    prove nothing. Comparing two full runs (action_log off vs on) isolates exactly the one variable
    this test cares about. Wall-clock time, perf_counter, and pid are frozen so the two runs'
    ledger content is byte-comparable — none of those are what this test is checking."""
    monkeypatch.setattr(time, "time", lambda: 1780000000.0)
    monkeypatch.setattr(time, "perf_counter", lambda: 0.0)
    monkeypatch.setattr(os, "getpid", lambda: 999999)

    loop = _load("loop")
    base = tmp_path / ".sdlc"
    (base / "goals").mkdir(parents=True)
    (base / "state").mkdir()
    goal_path = base / "goals" / "0001.md"
    d = str(base)
    goal = str(goal_path)

    def reset_run_state():
        for rel in ("state/work", "state/verify", "state/log", "state/claims", "ledger"):
            p = base / rel
            if p.exists():
                shutil.rmtree(p)
        (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
        (base / "state" / "review-queue.md").write_text("# Q\n")
        goal_path.write_text("---\nid: 0001\nstatus: pending\n---\nx\n")

    def run_sequence(action_log_on):
        cfg = {"work": {"enabled": True, "auto_merge": "always", "require_review": "approval"},
               "ledger": {"enabled": True, "actor": "dana"}, "journal": {"enabled": True},
               "action_log": {"enabled": action_log_on},
               "budget": {"max_iterations": 10}, "verify": {"command": "true"}}
        (base / "config.json").write_text(json.dumps(cfg))
        source = loop.sources.get_source(d, cfg)
        kind, claimed_goal = loop._next(d, source, cfg)
        assert (kind, claimed_goal) == ("goal", goal)
        work.start(d, cfg, goal, run=_runner([("rev-parse", "main")]))
        rec = work._record(d, goal); rec["pr"] = "7"; work._save(d, goal, rec)
        assert loop.verify_goal(d, goal) == 0
        run_merge = _runner(_rights() + _review(decision="APPROVED") + [("pr view", _view())])
        out = work.merge(d, cfg, goal, run=run_merge, sleep=NOSLEEP)
        assert out.startswith("PR #7 merged"), out
        out2 = _post_review(d, cfg, goal, _runner([]), "approve")
        assert "posted sigma:approve" in out2

        class _Sink:
            def complete(self, g): pass
            def fail(self, g, r): pass
            def park(self, g, r): pass
        loop._record(d, _Sink(), goal, "done")

    def snapshot_ledger():
        root = base / "ledger"
        if not root.exists():
            return {}
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}

    reset_run_state()
    run_sequence(action_log_on=False)
    ledger_off = snapshot_ledger()
    assert ledger_off, "the sequence itself must produce SOME ledger content, or this proves nothing"

    reset_run_state()
    run_sequence(action_log_on=True)
    ledger_on = snapshot_ledger()

    assert ledger_on == ledger_off, "turning action_log on changed what landed in .sdlc/ledger/"

    # Non-vacuous on the OTHER side too: the action-log run actually recorded every one of these
    # call sites — so "no ledger change" is not simply "actionlog did nothing at all".
    kinds = {e["kind"] for e in actionlog.read_goal(d, goal)}
    assert kinds == {"claimed", "worktree_start", "verify_run", "gate", "merged", "recorded"}


def test_actionlog_module_has_no_ledger_import():
    """Cheap belt-and-braces (actionlog.py's own module docstring: zero coupling BY CONSTRUCTION),
    alongside the stronger behavioral proof above — a plain substring check that actionlog.py's own
    source never loads ledger.py. Line-anchored (not a bare substring test) because the module's
    own docstring legitimately DISCUSSES "import ledger.py" in prose (explaining that it doesn't) —
    a plain `"import ledger" not in src` trips on that sentence; requiring the match at the START
    of a line (ignoring leading whitespace) is immune to it, the same "AST/anchored, not a raw
    substring" discipline this repo's own test_import_boundary.py argues for."""
    import re
    src = (SCRIPTS / "actionlog.py").read_text(encoding="utf-8")
    assert '_load("ledger")' not in src
    assert not re.search(r"^\s*(import ledger\b|from ledger import)", src, re.MULTILINE)


# --- gate: a check that has not reported is not a check that failed (#464) ------------------------

def _mixed(*pairs, status="BLOCKED"):
    """A rollup where each entry is (name, conclusion) — conclusion "" means "still running",
    which is exactly what `gh pr view --json statusCheckRollup` returns for an unfinished check."""
    return _view(status=status, checks=pairs)


def test_gate_waits_for_pending_checks_instead_of_parking(tmp_path):
    """THE #464 fix. gate() retried only while `mergeable` was UNKNOWN — it never waited for the
    CHECKS. With a 21s budget against a ~300s CI run, a PR that was merely still building was
    indistinguishable from one that had failed, and the caller PARKED it. Parking strips
    `sdlc:goal`, so a transient 4-minute wait permanently dequeued the goal until a human
    re-labelled it: 3 of 11 goals on one backlog, and effectively every goal during a CI outage."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    seen = []

    def view(_line):
        seen.append(1)
        if len(seen) < 3:
            return _mixed(("ci", "SUCCESS"), ("slow", ""))     # still running
        return _view()                                          # reported, CLEAN
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", view)]), sleep=NOSLEEP)
    assert ok and verdict == "clean and safe", verdict
    assert len(seen) == 3


def test_gate_parks_at_once_on_a_real_failure_without_burning_the_pending_budget(tmp_path):
    """The counterweight. Waiting is only correct for checks that have not ANSWERED; a check that
    answered FAILURE must park immediately, not after minutes of pointless polling."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _mixed(("ci", "SUCCESS"), ("tests", "FAILURE"), status="UNSTABLE"))])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok and "tests" in verdict and "ci" not in verdict
    assert sum("pr view" in c for c in run.calls) == 1, "a failure must not be retried"


def test_gate_parks_when_checks_stay_pending_past_the_budget(tmp_path):
    """Bounded, not infinite. If the checks never report, the goal still parks — with a reason that
    says PENDING, so a human can tell 'CI is stuck' from 'CI said no'."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok and "pending" in verdict.lower() and "slow" in verdict
    assert sum("pr view" in c for c in run.calls) == work.PENDING_ATTEMPTS + 1


def test_gate_pending_exhausted_verdict_has_the_shared_prefix_merge_relies_on(tmp_path):
    """#254 task 3: pins the exact contract `merge()` now depends on to tell "genuinely still
    pending after gate()'s own patience budget" apart from every other park reason (a conflict, a
    stale head, a genuinely FAILING check) -- `merge()` arms `--auto` for this one specifically
    instead of parking unarmed, via `verdict.startswith(work.PENDING_PREFIX)`. Named once, shared by
    both the f-string that builds this message and the check that recognises it, so they can never
    silently drift apart."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok and verdict.startswith(work.PENDING_PREFIX)


def _rollup(*entries, status="BLOCKED"):
    """A rollup built field-by-field, so a check can carry a `state` with NO `conclusion` — the
    CheckRun-vs-StatusContext shape `_view` cannot express. Needed because the two shapes take
    DIFFERENT branches of `_check_verdict`, and a test that only sets `conclusion` silently never
    exercises the state branch at all (a mutation proved exactly that)."""
    return json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": status, "headRefOid": HEAD_SHA,
                       "statusCheckRollup": list(entries)})


def test_gate_treats_an_unrecognised_conclusion_as_failing(tmp_path):
    """FAIL CLOSED, conclusion branch: a check that ANSWERED with something we do not recognise is
    a failure, not a wait."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _mixed(("ci", "SUCCESS"), ("weird", "SOME_NEW_STATE")))])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok and "weird" in verdict
    assert sum("pr view" in c for c in run.calls) == 1, "an unknown conclusion must not be waited on"


def test_gate_treats_an_unrecognised_STATE_as_failing_not_pending(tmp_path):
    """FAIL CLOSED, state branch — the one that matters for drift.

    `pending` is an explicit allowlist so an unknown state falls through to 'failing' and PARKS.
    Listing the FAILING states instead would make any status GitHub adds later silently 'pending':
    the gate would wait out its budget and then merge something it never understood.

    This test exists in this exact shape because its first version set `conclusion` instead of
    `state` and a mutation that inverted the state branch SURVIVED it — the assertions passed while
    the mutated line was never executed."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _rollup({"name": "ci", "conclusion": "SUCCESS"},
                                       {"name": "weird", "state": "SOME_NEW_STATE"}))])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok and "weird" in verdict and "failing" in verdict
    assert sum("pr view" in c for c in run.calls) == 1, "an unknown state must not be waited on"


def test_gate_waits_on_a_state_only_pending_check(tmp_path):
    """The positive half of the same branch: a StatusContext reporting IN_PROGRESS has no
    `conclusion` at all, and must be waited on rather than parked."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    seen = []

    def view(_line):
        seen.append(1)
        if len(seen) < 2:
            return _rollup({"name": "ci", "state": "IN_PROGRESS"})
        return _view()
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", view)]), sleep=NOSLEEP)
    assert ok and verdict == "clean and safe"


def test_gate_pending_budget_outlasts_a_real_ci_run(tmp_path):
    """The budget is the whole point: 21s could never outlast this repo's ~300s CI. A budget that
    is merely *larger* but still shorter than CI would reproduce the bug more slowly."""
    assert work.PENDING_ATTEMPTS * work.PENDING_INTERVAL >= 360, (
        f"pending budget is {work.PENDING_ATTEMPTS * work.PENDING_INTERVAL}s — must outlast CI")


# --- #78: work.py's `_run` diagnoses a Claude Code Remote session's gh proxy block ---


def _fake_cmd_script(tmp_path, stderr_text, name="fake-cmd"):
    """A tiny stand-in `gh`/`git`-shaped binary so #78's proxy-block detection in `_run` (the REAL
    implementation, not an injected fake `run`) can be proven against an actual failing subprocess
    call, without depending on the real `gh` CLI, `git`, or network — the same technique
    test_sources.py's own `_fake_gh_script` uses for `_run_gh`."""
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\necho {stderr_text!r} >&2\nexit 1\n")
    script.chmod(0o755)
    return str(script)


def test_run_diagnoses_a_claude_code_remote_session_proxy_block(tmp_path):
    """#78: `work._run` (the module's real, non-injected implementation) must raise with the
    corrected diagnosis when the underlying command's failure IS a Claude Code Remote session's
    proxy block, not the raw 403 read as an ordinary permission/auth error."""
    gh_session = _load("gh_session")
    proxy_text = ("GitHub access is not enabled for this session. An org admin must connect the "
                  "Claude GitHub App for this organization.")
    fake_gh = _fake_cmd_script(tmp_path, proxy_text)
    try:
        work._run(str(tmp_path), [fake_gh, "pr", "create", "--title", "x"])
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert gh_session.REMEDIATION in str(e)


def test_run_diagnoses_the_graphql_pinned_ops_proxy_shape(tmp_path):
    """The OTHER confirmed proxy shape (#78) — a GraphQL-pinned-ops 403 like `gh pr list` would
    raise — must be recognized too. (Since #1209, `pr()` itself no longer calls `gh pr list` at
    all — it went REST — but `_run`'s detection here is generic over ANY argv a caller passes,
    and other call sites in this file, e.g. `gate()`'s `gh pr view`, can still hit this same
    proxy shape.)"""
    gh_session = _load("gh_session")
    proxy_text = ("This GraphQL query (PullRequestList, sent by gh pr list) is not enabled for "
                  "this session - only the pinned set of PR-review operations is served.")
    fake_gh = _fake_cmd_script(tmp_path, proxy_text)
    try:
        work._run(str(tmp_path), [fake_gh, "pr", "list", "--head", "x"])
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert gh_session.REMEDIATION in str(e)


def test_run_keeps_the_raw_message_for_a_real_failure(tmp_path):
    """An ordinary (non-proxy) failure — the overwhelming majority of `_run`'s real-world raises,
    e.g. an actual git conflict or a real gh permission error — must be completely unaffected by
    #78's fix: the raw detail is still the whole message, nothing added or swapped in."""
    fake_git = _fake_cmd_script(tmp_path, "fatal: not a git repository (or any of the parent directories): .git")
    try:
        work._run(str(tmp_path), [fake_git, "status"])
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "fatal: not a git repository" in str(e)
        assert "Claude GitHub App" not in str(e)


# --- #1391: two agents must never share one worktree ---------------------------------------------
# The chain this closes: _release closed the ledger claim AND wiped the liveness marker; worktree
# paths are deterministic; the resume gate consulted only the (now-closed) claim -> a second agent
# attached to a LIVE agent's directory, where work.commit() runs `git add -A`.


import contextlib as _contextlib


@_contextlib.contextmanager
def _a_live_foreign_process():
    """A REAL, running process that is genuinely not us and not our parent -- the only honest way to
    exercise the guard, since `agent_alive` resolves liveness through `pid_alive`."""
    import subprocess as _sp
    proc = _sp.Popen(["sleep", "30"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


def _register_agent(sdlc_dir, goal, pid):
    import importlib.util, pathlib as _pl
    spec = importlib.util.spec_from_file_location(
        "loop", _pl.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts" / "loop.py")
    lp = importlib.util.module_from_spec(spec); spec.loader.exec_module(lp)
    lp.agent_start(sdlc_dir, goal, pid, {})
    return lp


def test_start_refuses_when_a_DIFFERENT_live_process_is_registered(tmp_path):
    """The guard. A live marker naming a pid that is not ours means someone else is in that
    worktree -- resuming it would let `git add -A` commit their half-finished work."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    with _a_live_foreign_process() as pid:
        _register_agent(d, goal, pid)
        out = work.start(d, ON, goal, run=_runner([]))
    assert "REFUSED" in out and "DIFFERENT live process" in out


def test_start_still_reattaches_for_the_agent_that_owns_the_marker(tmp_path):
    """The `{os.getpid(), os.getppid()}` FLOOR: whatever else is trusted, those two stay trusted.

    #1687 corrected what this used to claim -- "a work.py invocation made by that same agent has
    exactly that pid as its parent" -- which is false, and false in the direction that made this
    test pass against the very bug it was named for: it registers the TEST process's own pid, which
    `os.getpid()` matches for free, so the real SKILL.md shape (an agent pid that is NEITHER of the
    two) was never exercised here. `test_start_resumes_for_the_agent_that_registered_the_marker`
    is that case. This one is kept because it is the only thing pinning the floor: drop
    `os.getpid()` from `_calling_session_pids` and this is what goes red."""
    import os as _os
    d = _sdlc(tmp_path)
    goal = _started(d)
    _register_agent(d, goal, _os.getpid())          # "us"
    assert "already started" in work.start(d, ON, goal, run=_runner([]))


def test_start_reattaches_when_the_registered_process_is_dead(tmp_path):
    """A crashed agent must not wedge its goal forever -- a dead pid reads as no live worker."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _register_agent(d, goal, 999999)                 # a pid that cannot be running
    assert "already started" in work.start(d, ON, goal, run=_runner([]))


def test_start_reattaches_when_no_agent_is_registered_at_all(tmp_path):
    """Absent evidence degrades to today's behaviour -- this NARROWS resumes, never widens them."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    assert "already started" in work.start(d, ON, goal, run=_runner([]))


def test_the_foreign_agent_guard_actually_fires_and_does_not_silently_fail_open(tmp_path):
    """Regression guard for a bug I nearly shipped: the helper is wrapped in a fail-open except, so
    a missing `os` import made it swallow a NameError and never refuse -- while every other test
    still passed. This asserts the refusal REACHES its message rather than being swallowed."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    with _a_live_foreign_process() as pid:
        _register_agent(d, goal, pid)
        blocker = work._blocked_by_a_live_foreign_agent(d, ON, goal)
    assert blocker and "REFUSED" in blocker and "agent-reclaim" in blocker


def test_codex_agents_sharing_a_pid_are_distinct_on_resume(tmp_path, monkeypatch):
    """Desktop tasks share a shell PID; their Codex thread IDs must decide ownership."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    d = _sdlc(tmp_path)
    goal = _started(d)
    loop = _load("loop")
    thread_a = "01999aaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"
    thread_b = "01999bbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb"
    monkeypatch.setenv("CODEX_THREAD_ID", thread_a)
    monkeypatch.setenv("CODEX_SESSION_ID", thread_a)
    loop.agent_start(d, goal, os.getpid(), ON)
    assert work._blocked_by_a_live_foreign_agent(d, ON, goal, session_pid=os.getpid()) is None
    assert loop._goal_has_registered_worker(d, goal, ON, exclude_pids={os.getpid()}) is False

    monkeypatch.setenv("CODEX_THREAD_ID", thread_b)
    monkeypatch.setenv("CODEX_SESSION_ID", thread_b)
    assert "REFUSED" in work._blocked_by_a_live_foreign_agent(
        d, ON, goal, session_pid=os.getpid())
    assert loop._goal_has_registered_worker(d, goal, ON, exclude_pids={os.getpid()}) is True
    assert loop.agent_start(d, goal, os.getpid(), ON) is False
    assert "REFUSED" in work._blocked_by_a_live_foreign_agent(
        d, ON, goal, session_pid=os.getpid())


def test_claude_caller_does_not_claim_codex_agent_with_the_same_pid(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    d = _sdlc(tmp_path)
    goal = _started(d)
    loop = _load("loop")
    codex_id = "01999aaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"
    monkeypatch.setenv("CODEX_THREAD_ID", codex_id)
    loop.agent_start(d, goal, os.getpid(), ON)
    monkeypatch.delenv("CODEX_THREAD_ID")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert "REFUSED" in work._blocked_by_a_live_foreign_agent(
        d, ON, goal, session_pid=os.getpid())
    assert loop._goal_has_registered_worker(d, goal, ON, exclude_pids={os.getpid()}) is True
    assert loop.agent_start(d, goal, os.getpid(), ON) is False
    assert "REFUSED" in work._blocked_by_a_live_foreign_agent(
        d, ON, goal, session_pid=os.getpid())


def test_codex_resume_refuses_unreadable_existing_agent_marker(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    goal = _started(d)
    loop = _load("loop")
    codex_id = "01999aaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"
    monkeypatch.setenv("CODEX_THREAD_ID", codex_id)
    monkeypatch.setenv("CODEX_SESSION_ID", codex_id)
    path = loop._agent_marker_path(d, goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pid": ')
    assert "REFUSED" in work._blocked_by_a_live_foreign_agent(
        d, ON, goal, session_pid=os.getpid())
    assert loop.agent_start(d, goal, os.getpid(), ON) is False
    assert path.read_text() == '{"pid": '


def test_claude_resume_refuses_unreadable_existing_agent_marker(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    goal = _started(d)
    loop = _load("loop")
    monkeypatch.setenv("CLAUDECODE", "1")
    path = loop._agent_marker_path(d, goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pid": ')
    assert "REFUSED" in work._blocked_by_a_live_foreign_agent(
        d, ON, goal, session_pid=os.getpid())


def test_unreadable_agent_marker_recovers_after_lease_ttl(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    goal = _started(d)
    loop = _load("loop")
    codex_id = "01999aaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"
    monkeypatch.setenv("CODEX_THREAD_ID", codex_id)
    path = loop._agent_marker_path(d, goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pid": ')
    stale = time.time() - 13 * 3600  # default lease is 12 hours
    os.utime(path, (stale, stale))
    assert work._blocked_by_a_live_foreign_agent(d, ON, goal, session_pid=os.getpid()) is None
    assert loop.agent_start(d, goal, os.getpid(), ON) is True
    assert loop.agent_alive(d, goal, ON) == ("alive", os.getpid())


# --- #1687: the agent working THIS goal is not foreign to itself ---------------------------------
# SKILL.md step 3a is, in this order, `loop.py agent-start .sdlc "$goal" --pid $PPID` and then
# `work.py start .sdlc "$goal"`. The registered pid is the AGENT's; `work.py` is a short-lived child
# of the shell the agent ran the second command in, so `{os.getpid(), os.getppid()}` can never
# contain it. Every test below registers a REAL live process that is neither -- exactly that shape
# -- and the only thing that changes between "refused" and "resumed" is whether the caller named
# itself. Measured before the fix: `feature/derived-key-casing` sat 44 commits behind `main` across
# two picks, and nothing was printed on either.


def test_the_registered_agent_is_not_foreign_to_itself_once_it_names_its_session(tmp_path):
    """The bug. A live pid that is neither this process nor its parent -- the ONLY shape SKILL.md
    ever produces -- must not be read as a second session when it IS the caller."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    with _a_live_foreign_process() as agent:
        _register_agent(d, goal, agent)
        assert work._blocked_by_a_live_foreign_agent(d, ON, goal) is not None      # unnamed: refuses
        assert work._blocked_by_a_live_foreign_agent(d, ON, goal, session_pid=agent) is None


def test_start_resumes_for_the_agent_that_registered_the_marker(tmp_path):
    """The ordinary self-resume, in the real topology. The pre-#1687 test for this registered the
    TEST process's own pid, which `os.getpid()` matched for free -- so it passed against the very
    bug it was named for."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    with _a_live_foreign_process() as agent:
        _register_agent(d, goal, agent)
        assert "REFUSED" in work.start(d, ON, goal, run=_runner([]))
        out = work.start(d, ON, goal, run=_runner([]), session_pid=agent)
    assert "already started" in out


def test_a_genuinely_foreign_live_process_still_refuses_a_caller_that_named_itself(tmp_path):
    """The property the guard exists for, and the one this fix must not spend: naming yourself
    clears YOUR registration and nobody else's."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    with _a_live_foreign_process() as stranger:
        _register_agent(d, goal, stranger)
        with _a_live_foreign_process() as me:
            out = work.start(d, ON, goal, run=_runner([]), session_pid=me)
    assert "REFUSED" in out and "DIFFERENT live process" in out and str(stranger) in out


def test_a_second_thread_of_a_stranger_still_refuses(tmp_path):
    """`agent_threads` is a fan-out, and the fix must not collapse it: my own `main` marker being
    mine says nothing about a slice thread somebody else registered."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    with _a_live_foreign_process() as me:
        lp = _register_agent(d, goal, me)               # thread "main" is mine
        with _a_live_foreign_process() as stranger:
            lp.agent_start(d, goal, stranger, {}, thread="slice-a1")
            out = work.start(d, ON, goal, run=_runner([]), session_pid=me)
    assert "REFUSED" in out and "slice-a1" in out


def test_the_branch_outlived_its_record_path_also_knows_who_is_asking(tmp_path):
    """#388's fallback gate — reached only after `git worktree add -b` fails, with NO record on
    disk. It asks the identical question, so it needs the identical answer; the two gates drifting
    would mean a resume that works one way and refuses the other."""
    run = _runner([("rev-parse", "main"), ("worktree add -b", RuntimeError("already exists"))])
    d = _sdlc(tmp_path)
    with _a_live_foreign_process() as agent:
        _register_agent(d, "0001-x.md", agent)
        assert "REFUSED" in work.start(d, ON, "0001-x.md", run=run)
        out = work.start(d, ON, "0001-x.md", run=run, session_pid=agent)
    assert "REFUSED" not in out and "sdlc/0001-x" in out


def _upkeep_spy(monkeypatch):
    """Stands in for `feature_rebase.upkeep`, recording whether it was reached at all -- the fact
    the bug turned on. `_rebase_upkeep` returning "" and `upkeep` returning a note that happens to
    be "" are indistinguishable from the outside, and the real failure was the former."""
    calls = []
    real = work._feature_rebase()

    class _Stub:
        """DELEGATES everything but `upkeep` to the real module. The first version of this replaced
        the module wholesale, which silently disabled `_upkeep_had_anything_to_do` (it asks the same
        module for `switch`/`OFF`) and made two clause tests fail for a reason that had nothing to do
        with the clause. A stub that answers fewer questions than the thing it stands in for tests
        the stub."""
        def __getattr__(self, name):
            return getattr(real, name)

        @staticmethod
        def upkeep(*a, **k):
            calls.append((a, k))
            return {"note": " — feature/u rebased onto main"}

    monkeypatch.setattr(work, "_feature_rebase", lambda: _Stub())
    return calls


def test_rebase_upkeep_runs_when_the_caller_names_its_own_session(tmp_path, monkeypatch):
    """The measured consequence: with the goal's own agent registered, the pass was never even
    CALLED, on every pick, forever."""
    d = _adopted(tmp_path)
    goal = _started(d)
    calls = _upkeep_spy(monkeypatch)
    with _a_live_foreign_process() as agent:
        _register_agent(d, goal, agent)
        work._rebase_upkeep(d, FEAT, goal, "u", _runner([]), tmp_path, "origin")
        assert calls == [], "unnamed, the pass is skipped -- that is the bug, reproduced"
        note = work._rebase_upkeep(d, FEAT, goal, "u", _runner([]), tmp_path, "origin",
                                   session_pid=agent)
    assert calls and note == " — feature/u rebased onto main"


def test_start_tells_rebase_upkeep_who_is_asking(tmp_path, monkeypatch):
    """#1687's FIFTH threading point, and the only one no mutation covered.

    `_rebase_upkeep` refuses to run unless it is told the calling session, and every test above
    proves that by calling it DIRECTLY. None of them proves `start()` actually forwards the value
    it was given. Dropping `session_pid=session_pid` from that one call site survives the entire
    suite -- measured on 1.4.3's release tree, 5556 passed, exit 0 -- and reinstates #1687 in full:
    upkeep skipped on every pick, now audibly rather than silently, which is less bad and still
    the bug.

    So this asserts the HOP, not the behaviour behind it: what reached `_rebase_upkeep` is what
    `start()` was handed."""
    seen = {}

    def _spy(sdlc_dir, config, goal, unit, run, base_root, remote, session_pid=None):
        seen["session_pid"] = session_pid
        return ""

    monkeypatch.setattr(work, "_rebase_upkeep", _spy)
    d = _sdlc(tmp_path)
    work.start(d, ON, "0001-x.md", run=_runner([("rev-parse", "main")]), session_pid="4242")
    assert seen.get("session_pid") == "4242", (
        "start() dropped the caller's session pid on the way to _rebase_upkeep -- the agent that "
        "registered the marker reads as a stranger again and the pass is skipped on every pick "
        "(#1687)"
    )


def test_rebase_upkeep_says_so_when_it_skips(tmp_path, monkeypatch):
    """The skip must stop being silent. It returned "" and the outcome never reached
    `feature_rebase.clause()` either, so a stale branch left the pick line byte-identical to a
    healthy one -- which is why the 44-commit drift went unnoticed for two whole goals."""
    d = _adopted(tmp_path)
    goal = _started(d)
    _upkeep_spy(monkeypatch)
    with _a_live_foreign_process() as stranger:
        _register_agent(d, goal, stranger)
        with _a_live_foreign_process() as me:
            said = work._rebase_upkeep(d, FEAT, goal, "u", _runner([]), tmp_path, "origin",
                                        session_pid=me)
    assert said and "SKIPPED" in said and "feature/u" in said and "may be behind" in said
    assert "--session-pid" in said, "the likeliest cause after #1687 is an un-named caller"


def test_the_skip_clause_reaches_the_line_start_prints(tmp_path, monkeypatch):
    """End to end: the clause is only worth writing if `start()` actually appends it."""
    d = _adopted(tmp_path)
    _upkeep_spy(monkeypatch)
    run = _runner(_read(_issue(labels=["feature:u"])))
    with _a_live_foreign_process() as stranger:
        _register_agent(d, "1687", stranger)
        with _a_live_foreign_process() as me:
            out = work.start(d, FEAT, "1687", run=run, session_pid=me)
    assert "upkeep: SKIPPED" in out and "feature/u" in out


# --- #1687 review, finding 1: a clause is a CLAIM, and three cases have nothing to claim ---------
# The guard is asked BEFORE `feature_rebase.upkeep`, so it is also before that function's three
# cheapest gates (DISABLED / NO_UNIT / NOT_ADOPTED). Reporting "skipped" there is not a warning,
# it is a false statement on the one line an operator reads.


def _skip_clause(tmp_path, config, unit, adopted=True):
    """The clause `_rebase_upkeep` produces with a genuine live stranger holding the goal."""
    d = _adopted(tmp_path, config) if adopted else _sdlc(tmp_path, config)
    goal = _started(d)
    with _a_live_foreign_process() as stranger:
        _register_agent(d, goal, stranger)
        with _a_live_foreign_process() as me:
            return work._rebase_upkeep(d, config, goal, unit, _runner([]), tmp_path, "origin",
                                       session_pid=me)


def test_a_goal_declaring_no_unit_is_told_nothing(tmp_path):
    """`feature/None` is not a branch. A goal declaring no unit is the case the branching model
    guarantees is byte-identical to pre-adoption -- on the OUTPUT side too."""
    said = _skip_clause(tmp_path, FEAT, None)
    assert said == "", said
    assert "None" not in said


def test_an_unadopted_repo_is_told_nothing(tmp_path):
    """#1571: without `.sdlc/features/`, `feature: auth` is an ISSUE TAXONOMY label that is older
    than this plugin, not a branch -- announcing it as one is the exact misread that gate prevents.
    The unit still RESOLVES here, which is why this needs its own test rather than falling out of
    the one above."""
    assert _skip_clause(tmp_path, FEAT, "auth", adopted=False) == ""


def test_upkeep_turned_off_is_told_nothing(tmp_path):
    """Reporting maintenance as overdue to the operator who disabled it contradicts their config."""
    off = {**FEAT, "work": {**FEAT["work"], "rebase_upkeep": "off"}}
    assert _skip_clause(tmp_path, off, "u") == ""


def test_the_control_an_adopted_repo_with_a_unit_and_upkeep_on_IS_told(tmp_path):
    """The control for the three above: a whitelist that never says yes proves nothing."""
    said = _skip_clause(tmp_path, FEAT, "u")
    assert "SKIPPED" in said and "feature/u" in said


def test_an_unparseable_session_pid_narrows_nothing(tmp_path):
    """Fail-open in the SAFE direction. Junk must degrade to the two pids this always used --
    which refuses MORE, never less -- and must never raise out of a guard on the pick path."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    with _a_live_foreign_process() as agent:
        _register_agent(d, goal, agent)
        for junk in ("", "  ", "true", "abc", "12.5", None, object(), [], "-1"):
            assert "REFUSED" in work.start(d, ON, goal, run=_runner([]), session_pid=junk), junk


def test_calling_session_pids_always_contains_this_process_and_its_parent(tmp_path):
    """The floor. Whatever is named, the two pids that were always trusted stay trusted."""
    import os as _os
    floor = {_os.getpid(), _os.getppid()}
    for named in (None, "", "true", "abc", 4242, "4242", object()):
        assert floor <= work._calling_session_pids(named)
    assert 4242 in work._calling_session_pids("4242")
    assert 4242 in work._calling_session_pids(4242)
    # The INNER layer is the stricter one: `0` is os.kill's process-GROUP selector, which
    # `ledger.pid_alive(0)` answers True for, and no `agent-start --pid` can ever have written it.
    assert work._calling_session_pids(0) == floor and work._calling_session_pids("-1") == floor


def test_cli_start_reads_the_session_pid_flag(tmp_path, capsys):
    """The wiring. SKILL.md passes `--session-pid "$PPID"`; if `main()` drops it on the floor the
    fix is inert in every real run, which is exactly how the ORIGINAL `--session-pid` shipped
    broken on `loop.py next` (#1239 review, finding 1)."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    with _a_live_foreign_process() as agent:
        _register_agent(d, goal, agent)
        assert work.main(["work.py", "start", d, goal]) == 0
        assert "REFUSED" in capsys.readouterr().out
        assert work.main(["work.py", "start", d, goal, "--session-pid", str(agent)]) == 0
        assert "already started" in capsys.readouterr().out


def test_cli_start_warns_loudly_about_a_session_pid_it_cannot_use(tmp_path, capsys):
    """A dropped flag must not be silent -- silence is the defect this whole goal is about."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    with _a_live_foreign_process() as agent:
        _register_agent(d, goal, agent)
        assert work.main(["work.py", "start", d, goal, "--session-pid", "$PPID"]) == 0
        out = capsys.readouterr()
    assert "ignoring --session-pid" in out.err and "REFUSED" in out.out


# --- #1467 (L1, epic #1464): the base is resolved PER GOAL, from the unit its issue declares ------
# The whole point of the branching model, and the property that makes adopting it break nothing: a
# goal declaring NO unit must behave byte-identically to today. Every test below either pins the
# declared-unit path or pins that byte-identity.

FEAT = {"work": {"enabled": True, "base": "main"},
        "discovery": {"source": "github", "github": {"repo": "acme/app"}}}


def _issue(body="", labels=(), number=1467):
    """The `gh api repos/<slug>/issues/<n>` payload — REST's own shape, labels as `{"name": ...}`
    dicts, which is one of the two shapes `features.read` is specified to accept."""
    return json.dumps({"number": number, "title": "t", "body": body,
                       "labels": [{"name": n} for n in labels]})


def _read(payload):
    """The ONE read start() makes to learn the unit. Keyed on `api repos/` so it can never collide
    with the `gh api repos/{owner}/{repo}/pulls` handlers pr()'s own tests install."""
    return [("api repos/", payload)]


def _git(run):
    return [c for c in run.calls if c.startswith("git ")]


def _adopted(tmp_path, config=FEAT):
    """An sdlc dir for a project that HAS adopted the branching model — i.e. one where a `feature:`
    declaration is allowed to decide the base at all (#1571).

    The directory is created through `feature_registry.registry_dir` — the registry's OWN path
    function, the one the gate under test asks — so a `work.py` that grew a literal `.sdlc/features`
    of its own would still pass here, while one that looked somewhere ELSE entirely fails. `mkdir -p
    .sdlc/features` is the whole adoption gesture (docs/branching-model.md, `Opening a unit`), so
    this is the real install shape rather than a fixture convenience."""
    d = _sdlc(tmp_path, config)
    feature_registry.registry_dir(d).mkdir(parents=True, exist_ok=True)
    return d


def test_start_cuts_from_the_unit_the_issue_declares(tmp_path):
    d = _adopted(tmp_path)
    run = _runner(_read(_issue(body="Feature: voice-interview")))
    out = work.start(d, FEAT, "1467", run=run)
    assert "origin/feature/voice-interview" in out
    assert "git fetch origin feature/voice-interview" in run.calls
    assert any("worktree add -b sdlc/1467" in c and "origin/feature/voice-interview" in c
               for c in run.calls)
    assert work._record(d, "1467")["base"] == "feature/voice-interview"


def test_the_declared_unit_beats_the_configured_base(tmp_path):
    """Order matters, and this is the half the config cannot express: `work.base` is set to `main`
    here and must LOSE to the issue's own declaration."""
    d = _adopted(tmp_path)
    run = _runner(_read(_issue(body="Feature: billing")))
    work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/billing"
    assert not any("origin/main" in c for c in run.calls)


def test_the_label_alone_declares_the_unit(tmp_path):
    """`label_only` — the machine-readable half is enough on its own; nothing here needs the body."""
    d = _adopted(tmp_path)
    run = _runner(_read(_issue(labels=["bug", "feature:billing", "sdlc:goal"])))
    work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/billing"


def test_the_body_wins_when_the_two_halves_disagree(tmp_path):
    """`conflict` — features.py's rule is that the body wins (a human wrote it, a machine attached
    the label), so the base follows the BODY, not the label."""
    d = _adopted(tmp_path)
    run = _runner(_read(_issue(body="Feature: voice-interview", labels=["feature:billing"])))
    work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/voice-interview"


def test_the_branch_prefix_is_features_own_constant_not_a_literal(tmp_path, monkeypatch):
    """Anti-drift, and asserted the only way that can actually FAIL: the prefix is moved on the
    module work.py itself loaded, so a hardcoded "feature/" here — a second definition able to
    diverge silently from the one features.py publishes — does not follow it and the test breaks.
    Comparing against the constant's live value would have passed either way."""
    monkeypatch.setattr(work.features, "BRANCH_PREFIX", "unit/")
    d = _adopted(tmp_path)
    run = _runner(_read(_issue(body="Feature: billing")))
    work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "unit/billing"


# --- the property that makes this adoptable: NO declaration => byte-identical to today -----------

def test_a_goal_declaring_no_unit_is_byte_identical_to_today(tmp_path):
    """THE test. Every observable of start() — the return string, the record, and the exact list of
    git commands in order — is pinned to the literal values the pre-#1467 code produced.

    EXACTLY TWO differences are permitted anywhere, and both are asserted rather than tolerated: the
    ONE read-only `gh api` call that asks the question, and the ADDITIVE `base_resolved` key in the
    record (#1467 F2 — a fallback base has to leave a durable trace). Every pre-existing key keeps
    its exact pre-#1467 value, which is what the `dict(rec, ...)` comparison below pins: a changed
    value fails, and so does a second new key. (#2014 adds a THIRD, later difference — the leading
    `git status --porcelain` read below — which is a local root-cleanliness check, not part of the
    per-goal base resolution this docstring is about.)"""
    d = _sdlc(tmp_path, FEAT)
    run = _runner(_read(_issue(body="Nothing declared here.", labels=["sdlc:goal"])))
    out = work.start(d, FEAT, "1467", run=run)

    path = (pathlib.Path(d).parent / ".sdlc" / "work" / "1467").resolve()
    assert out == f"worktree {path} on sdlc/1467 (cut from origin/main)"
    rec = work._record(d, "1467")
    assert rec == {"worktree": str(path), "branch": "sdlc/1467", "base": "main",
                   "base_resolved": True, "remote": "origin", "pr": ""}
    assert set(rec) - {"base_resolved"} == {"worktree", "branch", "base", "remote", "pr"}
    assert _git(run) == ["git status --porcelain",
                         "git fetch origin main",
                         f"git worktree add -b sdlc/1467 {path} origin/main"]
    assert [c for c in run.calls if not c.startswith("git ")] == \
        ["gh api repos/acme/app/issues/1467"]


def test_head_is_still_the_last_resort_when_nothing_declares_a_base(tmp_path):
    """Resolution step 3, unchanged: no unit and no configured base still falls through to the
    current HEAD, and still asks git for it with the identical command."""
    config = {"work": {"enabled": True}, "discovery": {"source": "github"}}
    d = _sdlc(tmp_path, config)
    run = _runner([("api repos/", _issue()), ("rev-parse", "release-2")])
    work.start(d, config, "1467", run=run)
    assert work._record(d, "1467")["base"] == "release-2"
    assert "git rev-parse --abbrev-ref HEAD" in run.calls


def test_a_declared_unit_short_circuits_the_head_read(tmp_path):
    """The converse: when the issue declares a unit, neither fallback is consulted at all."""
    config = {"work": {"enabled": True}, "discovery": {"source": "github"}}
    d = _adopted(tmp_path, config)
    run = _runner([("api repos/", _issue(body="Feature: billing")), ("rev-parse", "release-2")])
    work.start(d, config, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/billing"
    assert not any("rev-parse" in c for c in run.calls)


def test_local_mode_never_reads_an_issue_at_all(tmp_path):
    """Structural byte-identity for every non-GitHub adopter: not one extra call is made, so there
    is no new failure mode for them to hit — not even a slow one."""
    d = _sdlc(tmp_path, ON)
    run = _runner([("rev-parse", "main")])
    work.start(d, ON, "0001-x.md", run=run)
    assert _git(run) == run.calls                 # nothing but git ran
    assert work._record(d, "0001-x.md")["base"] == "main"


def test_a_numeric_local_goal_is_never_mistaken_for_an_issue(tmp_path):
    """`_pr_body`'s own trap, and the same guard: `.sdlc/goals/0002.md` has a stem that `isdigit()`
    accepts, so `isdigit()` ALONE would read issue #2 of whatever repo the checkout points at and
    could base a local goal on a stranger's feature branch."""
    d = _sdlc(tmp_path, ON)
    run = _runner([("api repos/", _issue(body="Feature: billing")), ("rev-parse", "main")])
    work.start(d, ON, "0002.md", run=run)
    assert not any(c.startswith("gh ") for c in run.calls)
    assert work._record(d, "0002.md")["base"] == "main"


def test_a_non_numeric_goal_in_github_mode_is_never_read_as_an_issue(tmp_path):
    """The other half of the same gate: github mode is not enough either — a goal whose stem is not
    an issue number has no issue to read.

    `0001x` is in this list because of a mutant that SURVIVED the first pass: relaxing `isdigit()`
    to `isalnum()` changes nothing for `0001-x` (a hyphen is not alphanumeric either), so a stem
    that IS alphanumeric but is not a number is the only shape that can catch it. This is the
    loosening edge line coverage cannot see — both spellings execute the identical line."""
    for goal in ("0001-x.md", "0001x.md", "goal-42.md", "42a.md"):
        d = _sdlc(tmp_path / f"g{goal.replace('.', '_')}", FEAT)
        run = _runner([("api repos/", _issue(body="Feature: billing"))])
        work.start(d, FEAT, goal, run=run)
        assert not any(c.startswith("gh ") for c in run.calls), goal
        assert work._record(d, goal)["base"] == "main", goal


def test_a_declared_non_github_source_reads_no_issue(tmp_path):
    """A SECOND mutant that survived the first pass: `source != "github"` relaxed to `not source`
    is invisible to every test whose config simply omits `discovery`, because an absent source is
    falsy either way. An adopter who declares `"source": "local"` and numbers their goal files is
    the case that separates them — and would otherwise have issue #1467 of whatever repo the
    checkout points at decide their base."""
    for source in ("local", "jira", ""):
        config = {"work": {"enabled": True, "base": "main"}, "discovery": {"source": source}}
        d = _sdlc(tmp_path / f"src{source or 'blank'}", config)
        run = _runner([("api repos/", _issue(body="Feature: billing"))])
        work.start(d, config, "1467", run=run)
        assert not any(c.startswith("gh ") for c in run.calls), source
        assert work._record(d, "1467")["base"] == "main", source


def test_an_already_started_goal_reads_no_issue(tmp_path):
    """Resume must stay free: re-attaching costs no network read and cannot be newly refused.

    The goal here is NUMERIC and the mode is github on purpose — a THIRD mutant that survived the
    first pass (reading the issue on the resume path too) was invisible while this test used
    `0001-x.md`, because the isdigit gate skipped the read for reasons that had nothing to do with
    resuming. A resume test has to be inside the gate to test the gate."""
    d = _sdlc(tmp_path, FEAT)
    wt = pathlib.Path(d).parent / ".sdlc" / "work" / "1467"
    wt.mkdir(parents=True)
    work._save(d, "1467", {"worktree": str(wt), "branch": "sdlc/1467", "base": "feature/billing",
                           "remote": "origin", "pr": ""})
    run = _runner([("api repos/", _issue(body="Feature: voice-interview"))])
    assert "already started" in work.start(d, FEAT, "1467", run=run)
    # #2009 retires the second half of this test's original claim, deliberately. "Costs no network
    # read" still holds for the ISSUE -- the mutant this test was hardened against stays dead, and
    # that is what the empty `api repos/` list below proves. "Cannot be newly refused" does NOT
    # hold any more: a resume onto a worktree behind its base is now brought forward, or refused.
    # The freshness check is the only thing added, and it is two local git calls, never a gh read.
    # #2014 adds a THIRD local git call, ahead of both: the root-cleanliness check runs before the
    # resume path is even reached, so it applies here exactly as it does on a fresh cut.
    assert run.calls == ["git status --porcelain",
                         "git fetch origin feature/billing",
                         "git rev-list --count HEAD..origin/feature/billing"]
    assert not [c for c in run.calls if c.startswith("gh ")], "still no issue read on a resume"
    assert work._record(d, "1467")["base"] == "feature/billing"     # not re-resolved behind its back


# --- the read is fail-open, and never silent about it --------------------------------------------

def test_an_unreadable_issue_falls_back_to_the_configured_base_and_says_so(tmp_path):
    """`loop.py`'s own precedent for a pick-time issue read (`fetch_title_body` -> PROCEED): a
    transport failure must not stop the goal. But it is NOT the same as "declared nothing", so the
    returned line — the one thing the agent reads — has to say the question went unanswered."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner([("api repos/", RuntimeError("gh api failed: HTTP 403 rate limit exceeded"))])
    out = work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "main"
    assert "origin/main" in out and "rate limit" in out
    assert "declared unit" in out


def test_a_malformed_issue_payload_falls_back_and_says_so(tmp_path):
    """Same fail-open, different failure: a 200 that is not JSON at all."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner(_read("<html>proxy error</html>"))
    out = work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "main"
    assert "declared unit" in out


def test_an_empty_issue_payload_is_a_failed_read_not_an_absent_declaration(tmp_path):
    """An empty body from `gh api` is a transport failure — a real REST reply is always an object.
    Silently reading it as "declares nothing" is how a wrong-base merge happens without a trace."""
    d = _sdlc(tmp_path, FEAT)
    out = work.start(d, FEAT, "1467", run=_runner(_read("")))
    assert work._record(d, "1467")["base"] == "main"
    assert "declared unit" in out


def test_a_valid_but_non_object_reply_is_a_failed_read_too(tmp_path):
    """`null`, `[]` and `42` all parse as JSON and would sail straight through a reader that only
    guards `json.loads` raising — `features.read` is total over payload SHAPE, so each of them
    returns a perfectly confident "declares nothing". `protection()` had this exact bug found in
    code review; the same answer here: a REST issue reply is an object or it is not a reply."""
    d = _sdlc(tmp_path, FEAT)
    for payload in ("null", "[]", "42", '"a string"'):
        goal = "1467" + str(len(payload))
        out = work.start(d, FEAT, goal, run=_runner(_read(payload)))
        assert work._record(d, goal)["base"] == "main", payload
        assert "declared unit" in out, payload


def test_the_failure_note_stays_one_bounded_line(tmp_path):
    """`_run` raises with the whole command line plus git/gh's raw multi-line stderr. Spliced
    unreduced into the one line an agent reads back, the note buries the result it is annotating —
    so it is flattened and capped, and this is what stops that regressing."""
    d = _sdlc(tmp_path, FEAT)
    noise = RuntimeError("gh api repos/acme/app/issues/1467: " + ("HTTP 500 boom\n" * 200))
    out = work.start(d, FEAT, "1467", run=_runner([("api repos/", noise)]))
    assert "\n" not in out
    assert len(out) < 600 and "declared unit" in out


def test_a_discovery_block_that_is_not_a_mapping_declares_nothing(tmp_path):
    """A hand-edited config must not be able to take out the goal it was asked about, before a
    single git call. Both depths: `discovery` itself, and the `github` block inside it."""
    for discovery in ("github", ["github"], 7, {"source": "github", "github": "acme/app"}):
        config = {"work": {"enabled": True, "base": "main"}, "discovery": discovery}
        d = _sdlc(tmp_path / f"cfg{abs(hash(str(discovery)))}", config)
        work.start(d, config, "1467", run=_runner(_read(_issue(body="Feature: billing"))))
        assert work._record(d, "1467")["base"] == "main", discovery


def test_a_successful_read_of_a_silent_issue_adds_no_note(tmp_path):
    """The inverse guard: "declared nothing" is an ANSWER, so it must not carry the failure note —
    otherwise the note is noise on every goal of every adopter who uses no units at all."""
    d = _sdlc(tmp_path, FEAT)
    out = work.start(d, FEAT, "1467", run=_runner(_read(_issue(body="ordinary prose"))))
    assert "declared unit" not in out


# --- the one thing that is NOT fail-open: an issue that contradicts itself ------------------------

def test_a_self_contradicting_issue_refuses_before_any_git_call(tmp_path):
    """`features.AmbiguousUnit` is the one verdict there is no honest default for — falling back to
    the configured base would put unit work on the integration branch silently. It refuses THIS
    goal only (start() is per goal), and it refuses before cutting anything.

    "before any git call" now means before any call THAT COULD CUT ANYTHING (#2014's root-
    cleanliness read runs first and is not that -- it is a read-only check of the root's own
    working tree, made before the unit is even resolved, so it is unconditionally present)."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner(_read(_issue(body="Feature: billing\n\nFeature: voice-interview")))
    try:
        work.start(d, FEAT, "1467", run=run)
        assert False, "expected start() to refuse an issue declaring two units"
    except ValueError as exc:
        assert "1467" in str(exc) and "more than one unit" in str(exc)
        assert isinstance(exc, work.features.AmbiguousUnit)   # the type a caller routes on, kept
    assert _git(run) == ["git status --porcelain"]
    assert work._record(d, "1467") is None


def test_two_rival_feature_labels_refuse_the_pick_too(tmp_path):
    """The label side of the same rule — a hand-added second `feature:` label is an observed
    failure mode on this repo, not a hypothetical one."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner(_read(_issue(labels=["feature:billing", "feature:voice"])))
    try:
        work.start(d, FEAT, "1467", run=run)
        assert False, "expected start() to refuse two rival feature labels"
    except ValueError as exc:
        assert "more than one unit" in str(exc)
    assert _git(run) == ["git status --porcelain"]      # #2014's root-cleanliness read, see above


# --- #2014: the ROOT checkout must never carry unaccounted-for tracked dirt on its own base --------
# `commit()`'s `git add -A` only ever runs with cwd=<worktree> (see test_commit_only_ever_touches_
# this_goals_worktree above), so it structurally cannot see dirt sitting in the root checkout --
# which is exactly what happens when someone edits straight in root instead of cutting a worktree
# first (AGENTS.md: "Nobody commits directly to a feature branch"; the same mistake on the
# INTEGRATION branch itself is worse, since every goal's worktree is cut from it). This is the one
# check in `start()` that looks at the root's OWN working tree rather than the goal's.

def test_start_refuses_when_root_carries_tracked_dirt_on_its_own_base(tmp_path):
    d = _sdlc(tmp_path, FEAT)
    run = _runner([("status --porcelain", " M some_file.py"),
                   ("rev-parse --abbrev-ref", "main")])
    out = work.start(d, FEAT, "1467", run=run)
    assert out.startswith("REFUSED") and "tracked" in out
    assert work._record(d, "1467") is None
    assert not any(c.startswith(("git fetch", "git worktree")) for c in run.calls)


def test_start_proceeds_when_root_is_clean_on_its_base(tmp_path):
    d = _sdlc(tmp_path, FEAT)
    run = _runner(_read(_issue(body="Nothing declared here.")))
    out = work.start(d, FEAT, "1467", run=run)
    assert not out.startswith("REFUSED")
    assert run.calls[0] == "git status --porcelain"


def test_start_root_dirt_check_ignores_untracked_scratch(tmp_path):
    """`??` lines are untracked -- ordinary scratch (this very repo's own `.wrangler/` at the time
    of writing) that must never trip a refusal meant for unaccounted-for TRACKED edits."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner([("status --porcelain", "?? scratch/"),
                   ("rev-parse --abbrev-ref", "main")])
    assert not work.start(d, FEAT, "1467", run=run).startswith("REFUSED")


def test_start_root_dirt_check_ignores_a_non_base_branch(tmp_path):
    """A maintainer doing real, deliberate work in root on a release branch (this repo's own
    history does exactly that for `release/*`) is not the failure mode this guards against --
    only the configured base itself must stay clean, because every goal's worktree forks from it."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner([("status --porcelain", " M some_file.py"),
                   ("rev-parse --abbrev-ref", "release/1.4.7")])
    assert not work.start(d, FEAT, "1467", run=run).startswith("REFUSED")


def test_start_root_dirt_check_is_off_when_no_base_is_configured(tmp_path):
    """No configured `work.base` means no stable reference point for "no branch" -- treating
    current-HEAD-as-base would degrade this into "any tracked dirt anywhere refuses", which
    misfires on legitimate root use and is not what this guards against."""
    d = _sdlc(tmp_path, ON)
    run = _runner([("status --porcelain", " M some_file.py")])
    assert not work.start(d, ON, "0001-x.md", run=run).startswith("REFUSED")
    assert not any("status --porcelain" in c for c in run.calls)


def test_start_root_dirt_check_fails_open_on_a_git_error(tmp_path):
    """A root that is not even a git repository, or any other git failure, must not become a NEW
    way for `start()` to blow up -- fail open, exactly like the issue-declaration read does."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner([("status --porcelain", RuntimeError("fatal: not a git repository"))])
    assert not work.start(d, FEAT, "1467", run=run).startswith("REFUSED")


# --- the slug the issue is read from -------------------------------------------------------------

def test_the_issue_is_read_from_the_configured_discovery_repo(tmp_path):
    """The goal NUMBER came from `discovery.github.repo`, so the read has to go back to that same
    repo. Resolving it from the checkout's git remote instead would read a DIFFERENT issue #1467
    on a fork — and base the goal on whatever that stranger's issue declared."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner(_read(_issue()))
    work.start(d, FEAT, "1467", run=run)
    assert "gh api repos/acme/app/issues/1467" in run.calls


def test_an_unset_discovery_repo_falls_back_to_ghs_own_placeholders(tmp_path):
    """`discovery.github.repo` ships EMPTY in the agrim-init template, so it genuinely is unset on
    real installs. Same fallback `sources._repo_args` already makes: let gh infer it from cwd."""
    config = {"work": {"enabled": True, "base": "main"}, "discovery": {"source": "github"}}
    d = _sdlc(tmp_path, config)
    run = _runner(_read(_issue()))
    work.start(d, config, "1467", run=run)
    assert "gh api repos/{owner}/{repo}/issues/1467" in run.calls


def test_the_issue_read_runs_in_the_main_checkout(tmp_path):
    """Placeholder substitution needs a cwd inside the repo, and at start() time the worktree does
    not exist yet — so the read must run in the project root, not the path about to be created."""
    d = _sdlc(tmp_path, FEAT)
    seen = []

    def run(cwd, argv):
        seen.append((str(cwd), " ".join(str(a) for a in argv)))
        return _issue() if "api repos/" in " ".join(str(a) for a in argv) else ""

    work.start(d, FEAT, "1467", run=run)
    assert [cwd for cwd, line in seen if "api repos/" in line] == [str(tmp_path.resolve())]


# --- a declared unit can never escape the branch namespace ---------------------------------------

def test_a_declared_unit_can_never_escape_the_feature_namespace(tmp_path):
    """The declared value becomes a real git ref, so it is attacker-adjacent input. features.py's
    `_UNIT_RE` is the one validator, and it rejects every shape that could point somewhere else —
    this pins that work.py did not add its own looser path around it."""
    d = _sdlc(tmp_path, FEAT)
    for hostile in ("../../evil", "..", "a/b", "voice.lock", "trailing.", "with space",
                    "-", "", "--upload-pack=sh"):
        goal = f"146{abs(hash(hostile)) % 900 + 100}"
        run = _runner([("api repos/", _issue(body=f"Feature: {hostile}")), ("rev-parse", "main")])
        work.start(d, FEAT, goal, run=run)
        assert work._record(d, goal)["base"] == "main", hostile


# --- #1467 F1 (review): the branch-outlived-its-record resume must not re-resolve the base --------
# Found by an author-blind review and reproduced end-to-end against real git. `finish()` unlinks the
# record but KEEPS the branch, so a later `start()` lands on the reattach path -- where nothing is
# cut, and persisting a freshly resolved base is a guess about a branch somebody else's run made.
# `pr()` sends `rec["base"]` as the PR target and `rebase()` replays onto it, so the guess is not
# cosmetic. This whole section is a regression that DID NOT EXIST before #1467: the old base was
# deterministic from config, so re-resolving reproduced the same answer.

def _reattach(handlers):
    """A runner whose `worktree add -b` fails — the shape that means "this branch already exists"."""
    return _runner([("worktree add -b", RuntimeError("fatal: a branch named ... already exists")),
                    *handlers])


def _log_on(tmp_path, base="feature/demo", goal="1467"):
    """An sdlc dir whose DURABLE action log already records this goal's branch and its base — i.e.
    what `finish()` leaves behind after unlinking the record. ADOPTED (#1571), because a repo with
    no `.sdlc/features/` resolves a deterministic base and never reaches the oracles at all."""
    config = {**FEAT, "action_log": {"enabled": True}}
    d = _adopted(tmp_path, config)
    actionlog.safe_append(d, goal, "worktree_start", worktree="/gone", branch=f"sdlc/{goal}",
                          base=base, base_resolved=True)
    return d, config


def test_a_reattach_keeps_the_base_the_action_log_recorded_not_the_one_just_resolved(tmp_path):
    """THE F1 regression test. The read 404s, so resolution falls back to `work.base` (`main`) —
    but the branch was cut from `feature/demo`, the action log still says so, and that is what has
    to land in the record. Before the fix this wrote `main`, and `pr()` would have opened a PR
    against `main` carrying the whole feature branch."""
    d, config = _log_on(tmp_path)
    run = _reattach([("api repos/", RuntimeError("gh api ...: HTTP 404 Not Found"))])
    out = work.start(d, config, "1467", run=run)
    rec = work._record(d, "1467")
    assert rec["base"] == "feature/demo"
    assert rec["base_resolved"] is True             # the action log ANSWERED; this is not a guess
    assert "feature/demo" in out and "action log" in out
    # The failed `-b` attempt DID name origin/main -- that attempt is how the branch's existence is
    # discovered at all, and it created nothing. What matters is that the re-attach carries no base
    # and the RECORD -- the thing `pr()` and `rebase()` read -- was corrected before it was written.
    assert any(c.startswith("git worktree add ") and "-b" not in c for c in run.calls)


def test_a_reattach_falls_back_to_the_branchs_own_upstream_when_no_log_exists(tmp_path):
    """The second oracle. MEASURED against real git: `git worktree add -b <b> <p> <remote>/<base>`
    sets `branch.<b>.merge` to `refs/heads/<base>`, so git itself remembers the cut point with no
    bookkeeping of ours. Needed because the action log is opt-in."""
    d = _sdlc(tmp_path, FEAT)
    run = _reattach([("api repos/", RuntimeError("HTTP 404")),
                     ("rev-parse --abbrev-ref sdlc/1467@{upstream}", "origin/feature/demo")])
    out = work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/demo"
    assert "upstream" in out


def test_a_reattach_ignores_a_self_pointing_upstream(tmp_path):
    """The measured LIMIT of that oracle, and why there are two: `pr()`'s `git push -u` repoints the
    upstream at the branch's OWN remote ref, after which it answers `sdlc/1467` — its own name,
    which is not a base. Returning it would record `base: sdlc/1467` and make `pr()` open a PR
    against the branch itself."""
    d = _adopted(tmp_path)
    run = _reattach([("api repos/", _issue(body="Feature: billing")),
                     ("rev-parse --abbrev-ref sdlc/1467@{upstream}", "origin/sdlc/1467")])
    try:
        work.start(d, FEAT, "1467", run=run)
        assert False, "expected a refusal, not a base of `sdlc/1467`"
    except RuntimeError as exc:
        assert "outlived its work record" in str(exc)


def test_a_reattach_refuses_when_nothing_says_what_the_branch_was_cut_from(tmp_path):
    """No action log, no usable upstream, and a resolution that CAN vary between starts. There is
    no honest answer, so it refuses instead of recording one — and refuses before re-attaching, so
    no worktree and no record are left behind to make the next run think it succeeded."""
    d = _adopted(tmp_path)
    run = _reattach([("api repos/", _issue(body="Feature: billing"))])
    try:
        work.start(d, FEAT, "1467", run=run)
        assert False, "expected start() to refuse a base it cannot establish"
    except RuntimeError as exc:
        assert "outlived its work record" in str(exc) and "retarget" in str(exc)
    assert work._record(d, "1467") is None
    assert not any(c.startswith("git worktree add ") and "-b" not in c for c in run.calls)


def test_a_reattach_still_re_resolves_when_the_answer_cannot_vary(tmp_path):
    """The escape hatch, and the reason the refusal above is narrow rather than a regression: when
    no declaration read applies at all (local mode, or a non-issue stem), the base is
    `work.base or HEAD` — deterministic, byte-identical to the pre-#1467 answer — so re-resolving
    reproduces exactly what the branch was cut from and overwriting is a no-op. This is the
    pre-existing reattach path, and it must keep working untouched."""
    d = _sdlc(tmp_path, ON)
    run = _reattach([("rev-parse", "main")])
    work.start(d, ON, "0001-x.md", run=run)
    rec = work._record(d, "0001-x.md")
    assert rec["base"] == "main" and rec["base_resolved"] is True
    assert any(c.startswith("git worktree add ") and "-b" not in c for c in run.calls)


def test_a_reattach_that_agrees_with_the_resolution_says_nothing(tmp_path):
    """No note when nothing drifted — otherwise every ordinary reattach grows noise that trains a
    reader to ignore the line that matters."""
    d, config = _log_on(tmp_path, base="feature/demo")
    run = _reattach([("api repos/", _issue(body="Feature: demo"))])
    out = work.start(d, config, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/demo"
    assert "action log" not in out and "upstream" not in out


# --- #1467 F2 (review): a fallback base must leave a DURABLE trace, not one line of stdout --------

def test_a_failed_read_is_recorded_as_an_unresolved_base(tmp_path):
    """`base_resolved` is False for exactly one reason: the declaration was ASKED FOR and the answer
    never arrived. On this device the active `gh` account drifts across three accounts and a private
    read from the wrong one 404s — indistinguishable in effect from "declares nothing" — so the
    difference has to survive somewhere a later step can act on."""
    d = _sdlc(tmp_path, FEAT)
    work.start(d, FEAT, "1467", run=_runner([("api repos/", RuntimeError("HTTP 404"))]))
    assert work._record(d, "1467") == {"worktree": work._record(d, "1467")["worktree"],
                                       "branch": "sdlc/1467", "base": "main",
                                       "base_resolved": False, "remote": "origin", "pr": ""}


def test_declaring_nothing_is_a_resolved_base_not_a_fallback(tmp_path):
    """The distinction the flag exists to make. "The issue declares no unit" is an ANSWER — the
    configured base is correct — and marking it unresolved would flag every goal of every adopter
    who uses no units at all, which is the fastest way to make the flag meaningless."""
    d = _sdlc(tmp_path, FEAT)
    work.start(d, FEAT, "1467", run=_runner(_read(_issue(body="ordinary prose"))))
    assert work._record(d, "1467")["base_resolved"] is True


def test_no_read_applying_is_a_resolved_base_too(tmp_path):
    """Local mode never asks, so its configured base is not a fallback either."""
    d = _sdlc(tmp_path, ON)
    work.start(d, ON, "0001-x.md", run=_runner([("rev-parse", "main")]))
    assert work._record(d, "0001-x.md")["base_resolved"] is True


def test_the_action_log_records_the_base_and_whether_it_was_resolved(tmp_path):
    """The durable half. `finish()` unlinks the record but never touches `state/log/`, so this entry
    is the only place the base still exists afterwards — which is what makes F1's first oracle
    possible at all, not merely observability."""
    config = {**FEAT, "action_log": {"enabled": True}}
    d = _sdlc(tmp_path, config)
    work.start(d, config, "1467", run=_runner([("api repos/", RuntimeError("HTTP 404"))]))
    entry = [e for e in actionlog.read_goal(d, "1467") if e["kind"] == "worktree_start"][0]
    assert entry["base"] == "main" and entry["base_resolved"] == "False"

    d2 = _sdlc(tmp_path / "declared", config)
    feature_registry.registry_dir(d2).mkdir(parents=True, exist_ok=True)
    work.start(d2, config, "1467", run=_runner(_read(_issue(body="Feature: billing"))))
    entry2 = [e for e in actionlog.read_goal(d2, "1467") if e["kind"] == "worktree_start"][0]
    assert entry2["base"] == "feature/billing" and entry2["base_resolved"] == "True"


# --- #1467 (review survivor): the mode gate is the same EXACT-match shape the rest of the repo uses

def test_the_github_mode_check_is_exact_and_case_sensitive(tmp_path):
    """A surviving mutant from the reviewer's own run: `!= "github"` relaxed to
    `str(...).lower() != "github"` is behaviour-changing — `"source": "GitHub"` makes zero calls
    under the original and reads the issue under the mutant — and nothing pinned which shape this
    gate has. It must match `_pr_body`, `mirror.is_github_mode` and `triage.py`, all of which
    compare `== "github"` exactly: one spelling of one question, or the readers drift."""
    for n, spelling in enumerate(("GitHub", "GITHUB", "Github", " github", "github ", "GITHUB ")):
        config = {"work": {"enabled": True, "base": "main"},
                  "discovery": {"source": spelling, "github": {"repo": "acme/app"}}}
        # indexed, never named after the spelling: macOS is case-INSENSITIVE, so `caseGitHub` and
        # `caseGITHUB` are one directory and half these cases would silently share an sdlc dir
        d = _sdlc(tmp_path / f"case{n}", config)
        run = _runner([("api repos/", _issue(body="Feature: billing"))])
        work.start(d, config, "1467", run=run)
        assert not any(c.startswith("gh ") for c in run.calls), spelling
        assert work._record(d, "1467")["base"] == "main", spelling
    # and the one exact spelling that DOES read, so this is not vacuously green
    d = _adopted(tmp_path / "exact")
    run = _runner([("api repos/", _issue(body="Feature: billing"))])
    work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/billing"


# --- #1467 (review): a base that does not exist must say where it came from ----------------------

def test_an_unknown_declared_branch_names_the_declaration_as_its_provenance(tmp_path):
    """Fail-closed is right — git refuses a ref it cannot find and no worktree is cut. But git's own
    "couldn't find remote ref X" names the ref and never says the ref came from the ISSUE BODY, so
    an operator whose config says `base: main` has nothing pointing back at it."""
    d = _adopted(tmp_path)
    run = _runner([("api repos/", _issue(body="Feature: nosuchbranch")),
                   ("fetch", RuntimeError("fatal: couldn't find remote ref feature/nosuchbranch"))])
    try:
        work.start(d, FEAT, "1467", run=run)
        assert False, "expected the fetch to fail closed"
    except RuntimeError as exc:
        assert "couldn't find remote ref" in str(exc)          # git's own words, kept
        assert "1467's own declaration" in str(exc)            # plus the one fact git cannot know
    assert work._record(d, "1467") is None


def test_a_missing_configured_base_names_config_as_its_provenance(tmp_path):
    """The other side: when nothing was declared, the message must NOT blame a declaration."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner([("api repos/", _issue()), ("fetch", RuntimeError("couldn't find remote ref"))])
    try:
        work.start(d, FEAT, "1467", run=run)
        assert False, "expected the fetch to fail closed"
    except RuntimeError as exc:
        assert "`work.base`" in str(exc) and "declaration" not in str(exc)


def test_an_unreadable_declaration_is_named_in_the_fail_closed_message_too(tmp_path):
    """And when the base is a FALLBACK, the failure has to say so — otherwise an operator chases a
    missing branch instead of the 404 that put them on the wrong base in the first place."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner([("api repos/", RuntimeError("HTTP 404")),
                   ("fetch", RuntimeError("couldn't find remote ref"))])
    try:
        work.start(d, FEAT, "1467", run=run)
        assert False, "expected the fetch to fail closed"
    except RuntimeError as exc:
        assert "could not be read" in str(exc)


# --- #1467 F1: two survivors from the round-2 mutation run, both on the loosening edge ------------

def test_a_reattach_ignores_an_upstream_on_a_DIFFERENT_remote(tmp_path):
    """A mutant that survived the first F1 pass: dropping the `<remote>/` prefix check on the
    upstream ref. A branch tracking `upstream/feature/demo` while this record's remote is `origin`
    would then yield the base `feature/demo` on a remote nobody fetched — or, with the whole ref
    kept, `upstream/feature/demo`, which `pr()` would send verbatim as its PR target. Neither is an
    answer about THIS remote, so neither is accepted."""
    for ref in ("upstream/feature/demo", "fork/feature/demo", "feature/demo"):
        d = _adopted(tmp_path / f"rem{len(ref)}")
        run = _reattach([("api repos/", _issue(body="Feature: billing")),
                         ("rev-parse --abbrev-ref sdlc/1467@{upstream}", ref)])
        try:
            work.start(d, FEAT, "1467", run=run)
            assert False, f"expected {ref!r} to be rejected as a base for remote `origin`"
        except RuntimeError as exc:
            assert "outlived its work record" in str(exc), ref
        assert work._record(d, "1467") is None, ref


def test_the_log_oracle_reads_the_LAST_cut_not_the_first(tmp_path):
    """The other survivor. A goal can be cut, finished and cut again — this repo's own
    `finish()`-keeps-the-branch path is exactly how — so the log holds several `worktree_start`
    entries with different bases. The branch on disk is the one the LAST cut made; reading the
    first would restore a base retired runs ago, which is the same class of wrong F1 is about."""
    config = {**FEAT, "action_log": {"enabled": True}}
    d = _sdlc(tmp_path, config)
    actionlog.append(d, "1467", "worktree_start", "loop", now=1_000, worktree="/gone",
                     branch="sdlc/1467", base="feature/old", base_resolved=True)
    actionlog.append(d, "1467", "worktree_start", "loop", now=2_000, worktree="/gone",
                     branch="sdlc/1467", base="feature/current", base_resolved=True)
    run = _reattach([("api repos/", RuntimeError("HTTP 404"))])
    work.start(d, config, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/current"


# --- #1571: a `feature:*` label only moves the base where the model was actually ADOPTED ---------
#
# `start()`'s only gate was `_reads_a_declaration` — github mode plus an issue-number stem — while
# the six feature modules (`feature_sync`, `feature_propagate`, `feature_rebase`, `feature_owner`,
# `unit_completion`, `cross_repo`) all ask one more question first: does `.sdlc/features/` exist?
# Without that question a repo that has used `feature: auth` as an ISSUE TAXONOMY for years — a
# common public convention, and one `features.parse_labels` happily parses — has its first pick
# based on `feature/auth`, a branch nobody ever cut. `git fetch` then fails and the goal is left
# claimed and `sdlc:in-progress`. Measured, not assumed: our own four repos carry ZERO
# `feature:`-prefixed labels, so this was never live here; it breaks the adopter, not us.


def test_a_pre_existing_feature_label_does_not_retarget_a_repo_that_never_adopted(tmp_path):
    """THE #1571 regression. `feature: auth` is a category here, not a branch: no `.sdlc/features/`
    exists, so nothing in this project has ever meant a unit by it. The base must stay `work.base`,
    and — the half that actually broke the run — the fetch must name `main`, not `feature/auth`."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner(_read(_issue(labels=["bug", "feature: auth"])))
    work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "main"
    assert "git fetch origin main" in run.calls
    assert not any("feature/auth" in c for c in run.calls)


def test_every_colon_space_taxonomy_form_is_inert_without_adoption(tmp_path):
    """`parse_labels` strips after the prefix, so `feature: request`, `feature: auth` and
    `FEATURE:Billing` ALL parse to a unit — which is exactly why the adoption gate has to be the
    thing that decides, rather than the label's shape. Each spelling is checked in its own sdlc dir
    because a shared one would let the first goal's worktree record answer for the rest."""
    for n, label in enumerate(("feature: request", "feature: auth", "FEATURE:Billing",
                               "feature:billing", "Feature: Voice-Interview")):
        d = _sdlc(tmp_path / f"tax{n}", FEAT)
        run = _runner(_read(_issue(labels=[label])))
        work.start(d, FEAT, f"146{n}", run=run)
        assert work._record(d, f"146{n}")["base"] == "main", label
        assert not any("origin/feature/" in c for c in run.calls), label


def test_the_declaration_still_moves_the_base_once_the_model_is_adopted(tmp_path):
    """The other side, so the gate above is not vacuously green: the SAME label in a repo that ran
    `mkdir -p .sdlc/features` still resolves the base, which is the whole of #1467."""
    d = _adopted(tmp_path)
    run = _runner(_read(_issue(labels=["feature: auth"])))
    work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/auth"
    assert "git fetch origin feature/auth" in run.calls


def test_a_fail_closed_fetch_does_not_blame_a_declaration_that_did_not_choose_the_base(tmp_path):
    """A third round-2 survivor. `provenance` is the one fact git cannot know, and it has to follow
    the same gate the base does: in an unadopted repo the label was read and then IGNORED, so
    `work.base` chose `main` and `work.base` is what a missing `main` must name. Blaming
    "#1467's own declaration" sends an operator to edit an issue that had no say in it."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner([("api repos/", _issue(labels=["feature: auth"])),
                   ("fetch", RuntimeError("fatal: couldn't find remote ref main"))])
    try:
        work.start(d, FEAT, "1467", run=run)
        assert False, "expected the fetch to fail closed"
    except RuntimeError as exc:
        assert "`work.base`" in str(exc) and "declaration" not in str(exc)


def test_adoption_is_a_DIRECTORY_not_merely_something_at_that_path(tmp_path):
    """A round-2 mutation survivor, on the loosening edge line coverage cannot see: `.is_dir()`
    relaxed to `.exists()` passed every other test here. `.sdlc/features` is a directory of unit
    shards — a FILE at that path is a mistake, a stray download or a half-finished `>` redirect, and
    reading it as adoption hands a repo the branching model it never opted into. `.is_dir()` is also
    the exact spelling all six feature modules use, so this pins the spelling as well as the rule.
    """
    d = _sdlc(tmp_path, FEAT)
    feature_registry.registry_dir(d).write_text("not a directory")
    run = _runner(_read(_issue(body="Feature: billing")))
    work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "main"


def test_an_adopted_repos_LOCAL_goal_still_reattaches_without_refusing(tmp_path):
    """The other round-2 survivor, and the reason the reattach gate asks BOTH halves rather than
    adoption alone. A `.sdlc/goals/0001-x.md` goal in a repo that HAS adopted the model carries no
    issue and therefore no declaration, so its base is `work.base or HEAD` — deterministic across
    two starts, exactly as in the pre-#1467 world. Refusing it would strand a local goal over a
    variability it never had. `_declaration_moves_the_base` is `_reads_a_declaration` AND adoption;
    dropping either half here leaves a mutant every other test in this file passes."""
    config = {"work": {"enabled": True, "base": "main"}}
    d = _adopted(tmp_path, config)
    run = _reattach([("rev-parse", "main")])
    work.start(d, config, "0001-x.md", run=run)
    assert work._record(d, "0001-x.md")["base"] == "main"
    assert any(c.startswith("git worktree add ") and "-b" not in c for c in run.calls)


def test_the_adoption_gate_asks_the_registry_where_its_directory_is(tmp_path):
    """Anti-drift, asserted the only way that can FAIL: the registry's `registry_dir` is moved on
    the module `work.py` itself consults, and points at a directory that is NOT `.sdlc/features/`.
    A `work.py` holding its own literal path keeps reading the old place and this test breaks;
    routing the question through the registry — the same call the six feature modules make — follows
    it. Comparing against `.sdlc/features/` would have passed either way."""
    d = _sdlc(tmp_path, FEAT)
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    work._FEATURE_REGISTRY = types.SimpleNamespace(registry_dir=lambda _: elsewhere)
    try:
        run = _runner(_read(_issue(body="Feature: billing")))
        work.start(d, FEAT, "1467", run=run)
        assert work._record(d, "1467")["base"] == "feature/billing"
    finally:
        work._FEATURE_REGISTRY = None


def test_a_non_adopted_repo_is_still_TOLD_its_declaration_went_unrecorded(tmp_path, capfd):
    """The reason the gate sits on the BASE and not on the READ. A repo that meant to adopt and
    forgot the `mkdir` looks identical from outside to one that never wanted the model at all — so
    silently basing its unit work on `main` would be the quiet wrong answer this issue is about,
    one layer along. The declaration is still read, still handed to `feature_sync.sync_at_pick`,
    and that pass still prints its own `NOT_ADOPTED` note naming the missing directory and the
    one-line gesture that fixes it. Skipping the read would have made that note unreachable."""
    d = _sdlc(tmp_path, FEAT)
    run = _runner(_read(_issue(body="Feature: billing")))
    work.start(d, FEAT, "1467", run=run)
    err = capfd.readouterr().err
    assert "billing" in err and "features" in err and "mkdir" in err
    assert "gh api repos/acme/app/issues/1467" in run.calls      # the read itself is unchanged


def test_a_goal_declaring_no_unit_is_byte_identical_whether_adopted_or_not(tmp_path):
    """#1467's adoption promise, re-pinned across the new axis: a goal that declares NOTHING sees
    the identical record and the identical git calls in both worlds. Adoption costs it only
    `feature_sync`'s own `ls-remote` reconcile (#1473), which is not this gate's business."""
    plain, adopted = _sdlc(tmp_path / "plain", FEAT), _adopted(tmp_path / "adopted")
    out = {}
    for name, d in (("plain", plain), ("adopted", adopted)):
        run = _runner(_read(_issue(body="Nothing declared here.")))
        out[name] = (work.start(d, FEAT, "1467", run=run).split(" (cut from ")[1],
                     {k: v for k, v in work._record(d, "1467").items() if k != "worktree"},
                     [c.split(" /")[0] for c in _git(run) if "ls-remote" not in c])
    assert out["plain"] == out["adopted"]
    assert out["plain"][1]["base"] == "main"


# --- #1572: the reattach oracle the caller was already holding -----------------------------------
#
# `_reattach_base` asks the action log (opt-in, off by default) and git's upstream (destroyed by
# `pr()`'s `push -u`) and then REFUSES — while `rec`, the goal's own work record, is a local
# variable at the call site carrying the very field being written. The record is not an oracle
# ABOUT the base: it IS the field `pr()` sends as the PR target and `rebase()` replays onto, so
# re-resolving over the top of it is the retarget the whole function exists to prevent.


def _kept_record(sdlc_dir, base="feature/demo", goal="1467"):
    """A goal whose work RECORD survived while its worktree directory did not — `git worktree
    remove`, a `prune`, or a wiped scratch dir. `start()` falls past `already started` (the path is
    not a directory), the `-b` cut fails because the branch is still there, and the reattach path
    is reached with `rec` in hand."""
    work._save(sdlc_dir, goal, {"worktree": str(pathlib.Path(sdlc_dir) / "work" / "gone"),
                                "branch": f"sdlc/{goal}", "base": base, "base_resolved": True,
                                "remote": "origin", "pr": "31"})


def test_a_reattach_takes_the_base_from_the_goals_own_work_record(tmp_path):
    """THE #1572 fix. No action log, no usable upstream — the stock config — and before this it
    raised, naming two remedies, while the answer sat in `rec["base"]` one frame up."""
    d = _adopted(tmp_path)
    _kept_record(d)
    run = _reattach([("api repos/", _issue(body="Feature: billing"))])
    out = work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/demo"
    assert work._record(d, "1467")["base_resolved"] is True
    assert "work record" in out and "feature/demo" in out
    assert any(c.startswith("git worktree add ") and "-b" not in c for c in run.calls)


def test_the_work_record_outranks_a_disagreeing_action_log(tmp_path):
    """Order, and it is not arbitrary. The log is a DIAGNOSTIC of what some past start did; the
    record is the live field `pr()` and `rebase()` read right now. Where the two disagree — a log
    entry from a cut that was later re-based, a hand-edited record — writing the log's answer would
    move the PR target out from under an open PR, which is the exact harm this path guards."""
    d, config = _log_on(tmp_path, base="feature/logged")
    _kept_record(d, base="feature/recorded")
    run = _reattach([("api repos/", _issue(body="Feature: billing"))])
    work.start(d, config, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/recorded"


def test_a_reattach_that_agrees_with_the_record_says_nothing(tmp_path):
    """No note when nothing drifted — the same silence the other two oracles keep, so a reader is
    not trained to ignore the line that matters."""
    d = _adopted(tmp_path)
    _kept_record(d, base="feature/billing")
    run = _reattach([("api repos/", _issue(body="Feature: billing"))])
    out = work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "feature/billing"
    assert "work record" not in out and "action log" not in out and "upstream" not in out


def test_the_reattach_refusal_names_remedies_that_can_be_performed(tmp_path):
    """The other half of #1572. The old message offered `action_log.enabled`, which cannot answer
    for a branch already cut, and "delete the branch", which destroys what an open PR is built on.
    What is left has to be a gesture that works on THIS run and a warning on the destructive one."""
    d = _adopted(tmp_path)
    run = _reattach([("api repos/", _issue(body="Feature: billing"))])
    try:
        work.start(d, FEAT, "1467", run=run)
        assert False, "expected start() to refuse a base it cannot establish"
    except RuntimeError as exc:
        msg = str(exc)
    assert "git branch --set-upstream-to origin/<base> sdlc/1467" in msg
    assert "open PR" in msg                # deleting the branch is caveated, not offered flat
    assert "cannot answer for this one" in msg    # the action log is named as next-time-only


# --- how the two fixes compose ------------------------------------------------------------------


def test_an_unadopted_repo_with_an_outlived_branch_reattaches_instead_of_refusing(tmp_path):
    """THE SEAM between #1571 and #1572, and the gap a fix to either one alone would leave. A repo
    that never adopted the model, whose branch outlived its record, has no record oracle, no action
    log and no upstream — so #1572's new oracle cannot answer. It must not refuse anyway: with the
    declaration inert (#1571) the base is `work.base or HEAD`, deterministic across two starts, so
    re-resolving reproduces exactly what the branch was cut from. That is the pre-#1467 world, and
    `_declaration_moves_the_base` is the ONE predicate both fixes ask, which is what keeps them from
    disagreeing about which world this is."""
    d = _sdlc(tmp_path, FEAT)
    run = _reattach([("api repos/", _issue(labels=["feature: auth"]))])
    work.start(d, FEAT, "1467", run=run)
    assert work._record(d, "1467")["base"] == "main"
    assert any(c.startswith("git worktree add ") and "-b" not in c for c in run.calls)


def test_the_same_goal_in_an_ADOPTED_repo_with_an_outlived_branch_still_refuses(tmp_path):
    """The control for the line above: adoption is the only difference, and there the answer CAN
    vary between starts, so the refusal stands. Without this, the composition test above would pass
    just as well against a build that deleted the refusal outright."""
    d = _adopted(tmp_path)
    run = _reattach([("api repos/", _issue(labels=["feature: auth"]))])
    try:
        work.start(d, FEAT, "1467", run=run)
        assert False, "expected the refusal to survive in an adopted repo"
    except RuntimeError as exc:
        assert "outlived its work record" in str(exc)


# --- #1474: the both-green gate — neither side of a cross-repo pair merges alone -----------------
#
# The gate answers ONE question before `merge()` lands anything: does another repo have to land
# alongside this PR, and is it ready? Everything below is arranged around the seam the issue names —
# "has not answered yet" is NOT "failed". A sibling still building must WAIT; only a sibling that
# genuinely answered no refuses. Getting that backwards parks a goal (which strips `sdlc:goal` and
# dequeues it) over a PR that was merely still compiling, which is the exact mistake `_CHECK_PENDING`
# exists to prevent one layer down — so this gate reuses `_check_verdict` rather than re-deriving it.

cross_repo = _load("cross_repo")
feature_registry = _load("feature_registry")

APPROVAL = {"work": {"enabled": True, "auto_merge": "always", "require_review": "approval"}}
CHANGES = {"work": {"enabled": True, "auto_merge": "always", "require_review": "changes"}}


def _landing(sdlc_dir, goal="0001-x.md", repos=("acme/app", "acme/api"), **over):
    """The pick-time landing decision #1472 records, written straight to the path IT owns.

    Built from `cross_repo`'s own constants rather than from literals: this record is a cross-goal
    contract (one release writes it, another reads it), so a test that hardcoded the schema string
    would keep passing after the contract moved out from under it."""
    record = {"schema": cross_repo.RECORD_SCHEMA, "goal": goal, "unit": "int-contract",
              "outcome": cross_repo.TIER_1, "tier": 1, "cross_repo": True,
              "repos": {r: {"verdict": cross_repo.GRANTED, "reason": None, "detail": "",
                            "owner": None} for r in repos},
              "denied": [], "unknown": [], "why": "", "at": "2026-08-22T00:00:00Z"}
    record.update(over)
    path = cross_repo.decision_path(sdlc_dir, goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


def _registry(sdlc_dir, unit="int-contract", repos=None):
    """`.sdlc/features/units/<unit>.json`, written through the registry's OWN writer so the file
    this gate reads is the file the registry actually produces."""
    d = feature_registry.registry_dir(sdlc_dir)
    feature_registry.write_unit(d, unit, {"repos": repos if repos is not None else {
        "acme/app": {"branch": "feature/int-contract"},
        "acme/api": {"branch": "feature/int-contract"}}})
    return d


def _sibling(number=42, checks=(("ci", "SUCCESS"),), draft=False, mergeable="MERGEABLE",
             review="APPROVED", repo="acme/api"):
    """`gh pr list --repo <sibling>` — the ONE read the gate makes per sibling per round."""
    return [(f"pr list --repo {repo}", json.dumps([{
        "number": number, "url": f"https://github.com/{repo}/pull/{number}", "isDraft": draft,
        "mergeable": mergeable, "reviewDecision": review,
        "statusCheckRollup": [{"name": n, "conclusion": c} for n, c in checks]}]))]


def _pair(tmp_path, config=ALWAYS, goal="0001-x.md", **landing):
    """A goal whose pick-time decision says tier 1 across acme/app (here) and acme/api (there)."""
    d = _sdlc(tmp_path, config)
    _started(d, goal)
    _evidence(d, goal)
    _registry(d)
    _landing(d, goal, **landing)
    return d, goal


# --- when the gate is inert: it must not touch a repo that never adopted the model ---------------

def test_the_both_green_gate_is_inert_where_the_branching_model_is_not_adopted(tmp_path):
    """No `.sdlc/features/`, no landing record — every project on earth today. The gate makes no
    call and says nothing, exactly like `review_gate` under `require_review: off`."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([])
    assert work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP) == (True, "")
    assert run.calls == []


def test_a_goal_with_no_landing_decision_refuses_once_the_registry_exists(tmp_path):
    """The one case `recorded() is None` is NOT benign. `loop._check_cross_repo_at_pick` records a
    decision for every goal it picks once `.sdlc/features/` exists — so a registry with no record
    for this goal means the pick-time check never ran, and nothing here can tell whether a sibling
    has to land alongside. `None` is a refusal to act on, never an implied tier."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _registry(d)
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=_runner([]), sleep=NOSLEEP)
    assert ok is False
    assert "no cross-repo landing decision" in why


def test_a_not_cross_repo_decision_leaves_the_gate_inert(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _registry(d)
    _landing(d, goal, outcome=cross_repo.NOT_CROSS_REPO, tier=None, cross_repo=False)
    assert work.sibling_gate(d, ALWAYS, goal, run=_runner([]), sleep=NOSLEEP) == (True, "")


def test_tier_two_leaves_the_gate_inert_because_that_pair_never_lands_together(tmp_path):
    """Tier 2's whole promise is that nothing is lost when access is missing — the reachable half
    lands contract-first and the other is raised to its owner. Gating it would invert exactly the
    property the fallback exists to provide."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _registry(d)
    _landing(d, goal, outcome=cross_repo.TIER_2, tier=2, denied=["acme/api"])
    assert work.sibling_gate(d, ALWAYS, goal, run=_runner([]), sleep=NOSLEEP) == (True, "")


def test_a_flagged_decision_refuses_rather_than_guessing(tmp_path):
    """`flagged` selects NO tier — the unit might be a tier-1 pair and might not. Merging on it
    would be selecting a tier by accident, which is the bug #1472 exists to prevent."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _registry(d)
    _landing(d, goal, outcome=cross_repo.FLAGGED, tier=None,
             why="access to acme/api could not be measured, so no tier was selected")
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=_runner([]), sleep=NOSLEEP)
    assert ok is False
    assert cross_repo.FLAGGED in why and "could not be measured" in why


def test_an_outcome_outside_the_closed_set_refuses(tmp_path):
    """The loosening edge. The inert set is an ALLOWLIST, so a value nobody has classified — a
    hand-edited record, an outcome a future release adds — refuses instead of falling through to a
    merge. Fail closed, the same shape as `_CHECK_PENDING`."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _registry(d)
    _landing(d, goal, outcome="whatever-comes-next", tier=None)
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=_runner([]), sleep=NOSLEEP)
    assert ok is False
    assert "whatever-comes-next" in why


def test_tier_one_with_cross_repo_false_is_an_inconsistent_record_and_refuses(tmp_path):
    """`decide()` can only produce tier 1 with `cross_repo: True`; the two disagreeing means the
    record was written by something else. Believe neither half.

    THE REASON IS ASSERTED, NOT JUST THE REFUSAL, and that is what makes this test worth anything.
    Dropping the `cross_repo` clause still ends in a refusal — the run carries on into the tier-1
    path and trips over the next guard instead — so a test asserting only `False` passes on the
    mutant. Measured: `tier1-consistency-dropped` survived the whole suite until this line named
    which refusal it had to be."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _registry(d)
    _landing(d, goal, cross_repo=False)
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=_runner([]), sleep=NOSLEEP)
    assert ok is False
    assert "selects no cross-repo tier" in why


# --- the sibling itself -------------------------------------------------------------------------

def test_both_sides_green_and_approved_passes_the_gate(tmp_path):
    d, goal = _pair(tmp_path, APPROVAL)
    run = _runner(_rights() + _sibling())
    ok, why = work.sibling_gate(d, APPROVAL, goal, run=run, sleep=NOSLEEP)
    assert ok is True
    assert "int-contract" in why
    assert any("pr list --repo acme/api --head feature/int-contract" in c for c in run.calls)


def test_a_missing_sibling_pr_refuses_rather_than_merging_one_side_alone(tmp_path):
    """The issue's fourth done-when. GitHub answered, and the answer is that the other half does
    not exist yet — an answer, not a silence, so this refuses at once rather than waiting."""
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + [("pr list --repo acme/api", "[]")])
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False
    assert "no open PR" in why and "acme/api" in why
    assert run.calls.count("gh pr list --repo acme/api --head feature/int-contract --state open "
                           "--limit 5 --json " + work.SIBLING_PR_FIELDS) == 1


def test_a_silent_gh_is_not_the_answer_that_there_is_no_sibling(tmp_path):
    """`gh pr list --json` prints `[]` for no results, so an EMPTY stdout is not that answer — it is
    no answer at all, and the two must not collapse. Both end in a refusal, which is exactly why
    only the ROUTE distinguishes them: "no PR" refuses on the first read, silence is retried on the
    pending budget first. Measured: `empty-stdout-is-no-prs` survived the suite until this test."""
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + [("pr list --repo acme/api", "")])
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False
    assert "could not be read" in why and "no open PR" not in why
    assert len([c for c in run.calls if "pr list" in c]) == work.PENDING_ATTEMPTS + 1


def test_a_failing_sibling_check_outranks_a_pending_one(tmp_path):
    """One PR, one check red and one still running. The red one is an answer we already have, so
    this refuses at once instead of spending the whole pending budget on a verdict that cannot
    change — `gate()`'s own failing-before-pending rule, and the mutant that reverses it
    (`pending-outranks-failing`) survived the suite until this test measured the call count."""
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + _sibling(checks=(("slow", ""), ("e2e", "FAILURE"))))
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False
    assert "failing checks: e2e" in why and "slow" not in why
    assert len([c for c in run.calls if "pr list" in c]) == 1        # never waited


def test_a_red_sibling_pr_refuses_and_names_the_failing_check(tmp_path):
    """The issue's first done-when: named, so the park message says WHAT is red."""
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + _sibling(checks=(("ci", "SUCCESS"), ("e2e", "FAILURE"))))
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False
    assert "acme/api" in why and "42" in why
    # only the check that actually failed is named -- a park message listing the green ones too
    # sends a human looking at the wrong log
    assert why.split("has failing checks: ")[1].split(" —")[0] == "e2e"


def test_a_sibling_still_running_checks_waits_rather_than_failing(tmp_path):
    """The issue's second done-when, and the one the `_CHECK_PENDING` allowlist exists for. An
    unfinished check has conclusion "" — it must re-read on the same budget `gate()` uses, and the
    refusal it eventually reports must not read as a failing check."""
    d, goal = _pair(tmp_path)
    slept = []
    run = _runner(_rights() + _sibling(checks=(("ci", "SUCCESS"), ("slow", ""))))
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=slept.append)
    assert ok is False
    assert "slow" in why and "not answered" in why
    assert len(slept) == work.PENDING_ATTEMPTS and set(slept) == {work.PENDING_INTERVAL}
    listed = [c for c in run.calls if "pr list" in c]
    assert len(listed) == work.PENDING_ATTEMPTS + 1        # re-read, never re-judged


def test_a_sibling_that_goes_green_while_we_wait_passes(tmp_path):
    """Waiting has to be able to END in a pass, or it is just a slow refusal."""
    d, goal = _pair(tmp_path, APPROVAL)
    rounds = {"n": 0}

    def answer(_line):
        rounds["n"] += 1
        state = "SUCCESS" if rounds["n"] > 2 else ""
        return json.dumps([{"number": 42, "isDraft": False, "mergeable": "MERGEABLE",
                            "reviewDecision": "APPROVED",
                            "statusCheckRollup": [{"name": "slow", "conclusion": state}]}])

    run = _runner(_rights() + [("pr list --repo acme/api", answer)])
    ok, _ = work.sibling_gate(d, APPROVAL, goal, run=run, sleep=NOSLEEP)
    assert ok is True and rounds["n"] == 3


def test_the_exhausted_sibling_wait_is_never_the_arm_worthy_pending_prefix(tmp_path):
    """THE interaction that makes this gate worth anything. `merge()` treats a verdict starting
    with `PENDING_PREFIX` as arm-worthy and arms `--auto`, which lands OUR side the moment OUR
    checks pass — regardless of the sibling. A sibling-wait verdict wearing that prefix would
    therefore merge exactly the half this gate exists to hold back."""
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + _sibling(checks=(("slow", ""),)))
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False
    assert not why.startswith(work.PENDING_PREFIX)


def test_an_unreadable_sibling_waits_and_then_refuses_but_never_merges(tmp_path):
    """A `gh` call that failed answered nothing, and nothing is not "green". It retries on the
    pending budget (tier 1 means access WAS confirmed at pick, so a failure now is new and may be a
    blip) and refuses when the budget runs out."""
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + [("pr list --repo acme/api", RuntimeError("HTTP 502"))])
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False
    assert "502" in why
    assert len([c for c in run.calls if "pr list" in c]) == work.PENDING_ATTEMPTS + 1


def test_a_draft_sibling_refuses(tmp_path):
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + _sibling(draft=True))
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False and "draft" in why


def test_a_conflicting_sibling_refuses(tmp_path):
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + _sibling(mergeable="CONFLICTING"))
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False and "conflict" in why


def test_a_sibling_whose_mergeability_is_not_computed_yet_waits(tmp_path):
    """GitHub computes mergeability lazily; UNKNOWN is the not-yet answer, never a no."""
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + _sibling(mergeable="UNKNOWN"))
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False and "mergeability" in why
    assert len([c for c in run.calls if "pr list" in c]) == work.PENDING_ATTEMPTS + 1


def test_a_nameless_failing_check_on_the_sibling_still_refuses(tmp_path):
    """`gate()` drops unnamed rollup entries from its `failing` list and is saved by
    `mergeStateStatus` behind it. There is no such backstop here, so an unnamed failure that fell
    out of the list would read as a green sibling — the one fail-OPEN shape this gate cannot have."""
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + _sibling(checks=((None, "FAILURE"),)))
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False and "fail" in why


# --- the review leg mirrors this project's OWN review policy, never a stricter one ---------------

def test_a_sibling_with_changes_requested_refuses(tmp_path):
    d, goal = _pair(tmp_path, CHANGES)
    run = _runner(_rights() + _sibling(review="CHANGES_REQUESTED"))
    ok, why = work.sibling_gate(d, CHANGES, goal, run=run, sleep=NOSLEEP)
    assert ok is False and "changes requested" in why


def test_an_unapproved_sibling_refuses_under_require_review_approval(tmp_path):
    d, goal = _pair(tmp_path, APPROVAL)
    run = _runner(_rights() + _sibling(review=None))
    ok, why = work.sibling_gate(d, APPROVAL, goal, run=run, sleep=NOSLEEP)
    assert ok is False and "not approved" in why


def test_an_unapproved_sibling_passes_where_the_project_requires_no_approval(tmp_path):
    """`reviewDecision` is null on any repo with no required reviewers. Demanding approval of the
    sibling on a project that demands none of its own PRs would refuse forever, for a reason the
    project never asked for — so the leg mirrors `review_mode`, the vocabulary that already exists."""
    d, goal = _pair(tmp_path, ALWAYS)                       # require_review defaults to off
    run = _runner(_rights() + _sibling(review=None))
    assert work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)[0] is True


# --- identifying the two halves -----------------------------------------------------------------

def test_a_registry_recording_no_branch_for_the_sibling_refuses(tmp_path):
    """Without a branch there is no way to name the other half, and a gate that cannot find the
    thing it is gating on must not pass."""
    d = _sdlc(tmp_path, ALWAYS)
    goal = _started(d)
    _evidence(d, goal)
    _registry(d, repos={"acme/app": {"branch": "feature/int-contract"}, "acme/api": {}})
    _landing(d, goal)
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=_runner(_rights()), sleep=NOSLEEP)
    assert ok is False
    assert "no branch" in why and "acme/api" in why


def test_two_open_prs_on_the_siblings_branch_refuse_as_ambiguous(tmp_path):
    """One head branch can carry PRs to two different bases. Which one is the other half is then
    a guess, and a guess is not a gate."""
    d, goal = _pair(tmp_path)
    two = json.dumps([{"number": 42, "isDraft": False, "mergeable": "MERGEABLE",
                       "reviewDecision": "APPROVED", "statusCheckRollup": []},
                      {"number": 43, "isDraft": False, "mergeable": "MERGEABLE",
                       "reviewDecision": "APPROVED", "statusCheckRollup": []}])
    run = _runner(_rights() + [("pr list --repo acme/api", two)])
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False and "42" in why and "43" in why


def test_a_landing_decision_that_does_not_name_this_repo_refuses(tmp_path):
    """`repo_slug` says we are acme/app (#1577 — the checkout's remote, since this config declares
    no board repo); the record names neither. Something is wrong about which unit this goal belongs
    to, and merging on it would gate against the wrong pair."""
    d = _sdlc(tmp_path, ALWAYS)
    goal = _started(d)
    _evidence(d, goal)
    _registry(d)
    _landing(d, goal, repos=("acme/api", "acme/other"))
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=_runner(_rights()), sleep=NOSLEEP)
    assert ok is False and "acme/app" in why


def test_an_unreadable_current_repo_refuses(tmp_path):
    """Fail CLOSED, unlike `review_gate`'s deliberate fail-open: not knowing which half is ours
    means not knowing which PR is the sibling, and merging then lands an unchecked pair.

    #1577: the unreadable case is now `repo_slug` returning None -- no `discovery.github.repo` and
    a remote git will not name -- rather than `gh repo view` raising. `repo_slug` is TOTAL (it
    catches its own runner's exception), so the refusal has to be on the value, not on an except."""
    d, goal = _pair(tmp_path)
    run = _runner([("remote get-url", RuntimeError("fatal: no such remote 'origin'"))])
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False and "which repo this goal belongs to" in why
    assert "origin" in why


BOARD_FIRST = {**ALWAYS, "discovery": {"github": {"repo": "acme/app"}}}


def test_the_gate_names_this_repo_the_way_the_registry_named_it(tmp_path):
    """#1577 item 5. Two parts of one model held different opinions about which repo this is.

    Every key the gate compares against -- `decision["repos"]`, `entry["repos"]` -- was written
    under `feature_sync.repo_slug`, which is BOARD-first: `discovery.github.repo`, because that is
    where the goal NUMBER came from. This gate derived its own half from `gh repo view`, which
    answers about the CHECKOUT. On a fork, or any split between the board and the checkout, the two
    disagree and the gate refused a correctly-configured cross-repo unit with "this goal's unit and
    its worktree disagree about which repos are involved" -- and `merge()` inherits that refusal.

    `repo_slug` is the authoritative one, and this is the shape of the argument: `feature_propagate`
    performs the IDENTICAL sibling computation (`sorted(r for r in entry["repos"] if not
    same_repo(r, here))`) off `repo_slug`, `unit_completion` calls it "the rule, called rather than
    re-derived", and `feature_sync` keys the entry with it. One of the six sites was the odd one
    out, and it was this one.

    Cheaper, too, and asserted: the derived answer cost a network `gh repo view` per gate; the board
    answer costs nothing, and its fallback is a local `git remote get-url`."""
    d, goal = _pair(tmp_path, config=BOARD_FIRST)
    run = _runner([("remote get-url", "git@github.com:contributor/app-fork.git"),
                   ("nameWithOwner", "contributor/app-fork")] + _sibling())
    ok, why = work.sibling_gate(d, BOARD_FIRST, goal, run=run, sleep=NOSLEEP)
    assert ok is True, why
    assert not [c for c in run.calls if "repo view" in c], run.calls


def test_the_gate_falls_back_to_the_checkout_only_when_the_board_is_not_declared(tmp_path):
    """`repo_slug`'s own precedence, asserted here because this gate is where it decides an outcome.
    With no `discovery.github.repo` the local remote answers -- which is the shipped config's real
    install path, not a theoretical one -- and that answer must reach the sibling computation."""
    d, goal = _pair(tmp_path)
    run = _runner([("remote get-url", "git@github.com:acme/api.git")] + _sibling(repo="acme/app"))
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is True, why
    assert "acme/app" in why and "acme/api" not in why.split("--")[-1]


def test_the_gate_never_merges_the_sibling_itself(tmp_path):
    """"Merge back to back" is two merges, each behind its own goal's gates. Merging the sibling
    from here would bypass every one of them, on a PR this goal did not author."""
    d, goal = _pair(tmp_path, APPROVAL)
    run = _runner(_rights() + _sibling())
    assert work.sibling_gate(d, APPROVAL, goal, run=run, sleep=NOSLEEP)[0] is True
    assert not any("pr merge" in c for c in run.calls)




# --- totality, and the three fail-open shapes review found ---------------------------------------

def test_a_row_that_is_not_a_pull_request_object_waits_and_says_so(tmp_path):
    """`gh` answered with a list, but nothing says its elements are objects. The guard's ONLY other
    observable effect is the route — 11 reads rather than 1 — so a mutant returning `ready` here
    survived a whole green suite. Asserting the REASON is what closes it. Fourth member of the same
    family as the three first-pass survivors: distinct causes, identical verdicts."""
    d, goal = _pair(tmp_path)
    run = _runner(_rights() + [("pr list --repo acme/api", json.dumps(["not-a-pr"]))])
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False
    assert "not a pull request" in why
    assert len([c for c in run.calls if "pr list" in c]) == work.PENDING_ATTEMPTS + 1


def test_a_tier_one_record_naming_only_our_own_repo_refuses(tmp_path):
    """The only fail-OPEN shape in the function, and it reads as its most confident answer: with no
    sibling to wait on, the round loop finds `waiting == []` on the first pass and reports "both
    sides are ready" at ZERO API calls. `decide()` cannot produce this record, which is exactly why
    it must refuse — the neighbouring guard already calls a self-inconsistent tier-1 record
    untrustworthy, and treating its twin as a pass is the inconsistency."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _registry(d)
    _landing(d, goal, repos=("acme/app",))
    run = _runner(_rights())
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False
    assert "no sibling" in why and "acme/app" in why


def test_a_unit_that_is_not_a_name_refuses_instead_of_raising(tmp_path):
    """`recorded()` validates the record's `schema` and NOTHING else, so `unit` is untrusted content
    that becomes a dict key. An unhashable one raised `TypeError` out of the registry lookup, past
    every `except` in the function — `feature_registry.is_authorized` hardens against exactly this
    shape for a `repo` key and records why."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _registry(d)
    _landing(d, goal, unit=["int-contract"])
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=_runner(_rights()), sleep=NOSLEEP)
    assert ok is False
    assert "not a unit name" in why


def test_a_goal_with_no_work_record_refuses_with_a_reason_rather_than_a_traceback(tmp_path):
    """A foreseeable consumer case: #1478 surfaces a UNIT's readiness, and a unit's goals include
    ones no worktree was ever cut for. Every `gh` call runs with the worktree as its cwd, so there
    is nowhere to run them from — and an honest refusal beats a `TypeError` the outer guard would
    report as an unexplained failure."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _registry(d)
    _landing(d, goal)
    work.record_path(d, goal).unlink()
    run = _runner(_rights())
    ok, why = work.sibling_gate(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert ok is False
    assert "no work record" in why
    assert run.calls == []


def test_the_outer_totality_guard_catches_what_no_inner_one_anticipates(tmp_path):
    """`registry_dir(None)` raises past every inner `except`. The specific guards handle the shapes
    somebody thought of; this one is why "never raises" is a promise rather than a hope."""
    ok, why = work.sibling_gate(None, ALWAYS, "0001-x.md", run=_runner([]), sleep=NOSLEEP)
    assert ok is False
    assert "could not run" in why


def test_the_check_never_raises_whatever_the_record_says(tmp_path):
    """"Never raises" is a promise its consumer (#1478) builds on, so it is total, not aspirational
    — the outer guard is the same shape and the same lesson as `cross_repo.check_at_pick`'s."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _registry(d)
    for record in ({"outcome": cross_repo.TIER_1, "repos": {"acme/app": None, "acme/api": None}},
                   {"repos": "not-a-mapping"},
                   {"outcome": None, "cross_repo": "yes"}):
        _landing(d, goal, **record)
        ok, why = work.sibling_gate(d, ALWAYS, goal, run=_runner(_rights()), sleep=NOSLEEP)
        assert ok is False and why, record


# --- merge() is deliberately NOT wired to it -----------------------------------------------------
#
# This is the finding that sent the first revision back, and it is structural rather than a bug: a
# declared unit bases the goal on `feature/<unit>`, so EVERY merge `merge()` can perform is
# `sdlc/<n>` -> `feature/<unit>` inside one repo — which cannot land half of a cross-repo pair,
# while the branch this check looks up is the sibling's UNIT branch, whose landing PR does not exist
# until the unit is finished. Wiring them together deadlocked every tier-1 unit symmetrically. The
# tests below pin the ABSENCE, because an absence nothing asserts is an absence someone re-adds.

def test_merge_never_consults_the_cross_repo_check(tmp_path):
    """A tier-1 goal whose sibling is unambiguously red still merges: `merge()` asks nothing about
    the sibling, makes no `gh pr list` call, and lands as it always has."""
    d, goal = _pair(tmp_path, ALWAYS)
    run = _runner(_rights() + _protected() + _sibling(checks=(("e2e", "FAILURE"),))
                  + [("pr view", _view())])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged"), out
    assert not any("pr list" in c for c in run.calls)
    assert not any("acme/api" in c for c in run.calls)


def test_merge_still_arms_auto_merge_on_pending_checks_with_a_red_sibling(tmp_path):
    """The `--auto` hazard the first revision left half-closed is MOOT rather than fixed, and this
    is the proof: with the check unwired, arming is decided by `gate()`'s verdict alone, exactly as
    it was before #1474. Nothing about this path is new, so nothing about it can be newly wrong."""
    d, goal = _pair(tmp_path, ALWAYS)
    run = _runner(_rights() + _protected() + _auto_merge_allowed(True)
                  + _sibling(checks=(("e2e", "FAILURE"),))
                  + [("pr view", _mixed(("ci", "SUCCESS"), ("slow", "")))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed on PR #7")
    assert not any("pr list" in c for c in run.calls)


def test_no_new_park_reason_enters_merge_from_the_cross_repo_check(tmp_path):
    """F6 asked that the new park reasons classify. With no call site there are none: `merge()`'s
    reachable park reasons are byte-identical to what they were before #1474. Pinned by source, so
    re-adding a call site fails here as well as above."""
    source = pathlib.Path(work.__file__).read_text(encoding="utf-8")
    body = source.split("def merge(", 1)[1].split("\ndef ", 1)[0]
    assert "sibling_gate(" not in body
    assert "#1474" in body                       # ...and it says WHY it is not there

# ------------------------------------------------------ #1937: the test_trust gate on every merge
# The one layer that guards the suite that ALREADY existed. Diff-only, so it adds no test-execution
# time; `warn`, never `block`, because the issue is explicit that legitimate reasons exist and what
# it wants is a VISIBLE, reason-bearing record rather than a unilateral refusal.

_TAMPERED_DIFF = (
    "diff --git a/tests/test_thing.py b/tests/test_thing.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/tests/test_thing.py\n"
    "+++ b/tests/test_thing.py\n"
    "@@ -1,3 +1,2 @@\n"
    " def test_answer():\n"
    "-    assert compute().value == 42\n"
)


def test_test_trust_gate_emits_warn_and_the_counts_when_a_test_was_weakened(tmp_path):
    ledger = _load("ledger")
    cfg = {**ALWAYS, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner([("diff", _TAMPERED_DIFF)] + _rights() + _protected() + [("pr view", _view())])
    work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    events = _gate_events(journal_events(ledger, d), "test_trust")
    assert len(events) == 1 and events[0]["verdict"] == "warn"
    assert "assertions_removed=1" in events[0]["why"]
    assert "tests/test_thing.py" in events[0]["why"]


def test_test_trust_gate_emits_pass_and_no_reason_on_a_clean_diff(tmp_path):
    ledger = _load("ledger")
    cfg = {**ALWAYS, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    events = _gate_events(journal_events(ledger, d), "test_trust")
    assert len(events) == 1 and events[0]["verdict"] == "pass"
    assert events[0].get("why") in (None, "")


def test_the_test_trust_gate_NEVER_blocks_the_merge(tmp_path):
    """THE CONTROL that keeps this gate honest. #1937: "Not a hard block on its own -- legitimate
    reasons exist". A tampered diff must still reach the merge; the record carries the flag and the
    reason so the waiver becomes provenance. If this ever returns PARK, the gate has silently
    become a blocker."""
    cfg = {**ALWAYS, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner([("diff", _TAMPERED_DIFF)] + _rights() + _protected() + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert not out.startswith("PARK"), out
    assert out.startswith("PR #7 merged"), out


def test_test_trust_diffs_against_the_goals_OWN_resolved_base_not_a_hardcoded_main(tmp_path):
    """A goal on a unit branch bases on `feature/<unit>`, not main (#1467). Diffing against the
    wrong base would report every test in the unit as tampered by this one goal."""
    cfg = {**ALWAYS, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    diffs = [c for c in run.calls if "diff" in c]
    assert diffs, "the gate never ran git diff at all"
    rec = work._record(d, goal)
    assert rec["base"] in diffs[0], f"diffed against the wrong base: {diffs[0]}"


def test_merge_emits_the_test_trust_gate_to_the_action_log(tmp_path):
    """THE CONTROL for a defect that was nearly shipped. actionlog.INTERNAL_GATE_KINDS is a THIRD
    gate vocabulary, separate from ledger.GATE_KINDS and contract/vocabulary.json. A gate
    missing from it is rejected by safe_append, which is FAIL-OPEN -- one stderr line, no exception,
    and the ledger half still succeeds. So the gate reads as fully wired while half of it silently
    writes nothing. Only asserting the actionlog side positively catches that."""
    cfg = {"work": {"enabled": True, "auto_merge": "always"}, **ACTIONLOG}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner([("diff", _TAMPERED_DIFF)] + _rights() + _protected() + [("pr view", _view())])
    work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    gates = {e["gate"]: e for e in actionlog.read_goal(d, goal) if e["kind"] == "gate"}
    assert "test_trust" in gates, f"actionlog never received the gate; got {sorted(gates)}"
    assert gates["test_trust"]["verdict"] == "warn"
    assert "assertions_removed=1" in gates["test_trust"]["why"]


def test_a_bare_emit_token_file_is_refused_without_the_core_naming_any_product():
    """A credential file whose basename ends `-emit-token` is secret-shaped (#2578 P6 review).

    S1-G7 deleted a private component's token filename from `_SECRET_BASENAMES`, because the core may
    no longer name a private component. That removal was correct and is not reverted here. What it
    silently removed with it was the REFUSAL, and the dossier's mitigation for that (TD-5) was
    measured false against the SHIPPED default rather than this repository's own config:

      * `setup.RUNTIME_IGNORES` is `.sdlc/state/ .sdlc/ledger/ .sdlc/work/ .sdlc/knowledge/
        .sdlc/events/ graphify-out/` -- it does NOT cover a `.sdlc/<product>-emit-token` file.
      * That product's own README named such a file as the DEFAULT token location.
      * So on a fresh adopter install the token is unignored, `work.py commit`'s `git add -A` stages
        it, and nothing refuses: the token reaches git history and a PR.

    This repository's own `.gitignore` carries a bare line for that filename, which masks the hole
    locally -- the exact "instance config is not a product fix; judge behaviour by the shipped
    default" trap.

    The rule is a SUFFIX, not a name, so the guard is restored without the core knowing any product:
    a hyphenated `*-emit-token` basename is secret-shaped, whoever ships it. (#2580 narrowed this
    test's own example to a generic `acme-emit-token`, so the core's tests name no private file.)
    """
    # `work` is already loaded at module scope against this file's own ROOT/SCRIPTS convention.
    # An earlier draft re-loaded it from the cwd-relative "skills/agrim-loop/scripts/work.py", which
    # is cwd-AUTHORITATIVE: run from another directory it either raises FileNotFoundError or, worse,
    # validates whatever file happens to sit at that path rather than the one under review.
    w = work
    assert w._secret_shaped("acme-emit-token") is True, "the rule is a shape, not one product's name"
    assert w._secret_shaped("EMIT-TOKEN") is False, "no stem before the suffix is not this shape"
    assert w._secret_shaped("emit-token-docs.md") is False, "only the basename's END counts"


# --- #2482: goal-review never merged or closed the Stage-1 design PR it confirmed or rejected ----
# `merge_design`/`close_design` are NOT `merge()`/`close`'s siblings via `_record()` -- a design
# goal's PR is looked up directly from GitHub by its deterministic branch name
# (`branch_prefix + stem(goal)`), because `merge()`'s CI/verify gate can never be satisfied for a
# design goal reviewed from a DIFFERENT run than whichever one (if any) opened its PR, and because
# two of goal-design's three documented paths never write a `.sdlc/state/work/<goal>.json` record
# at all (a "Design #N" meta-issue, and the Dossier local-only route).


def _design_pr_spy(calls, replies=None):
    """A `run` stand-in that records every call and answers `gh pr list` from a queue of canned
    JSON replies (default: one empty list, i.e. "no open PR"). Non-`gh pr list` calls (the actual
    merge/close mutation) succeed with empty output unless the queue is exhausted, in which case
    they raise -- lets a single spy drive both the lookup and the mutation in one test."""
    replies = list(replies) if replies is not None else ["[]"]

    def _fake(cwd, argv):
        calls.append((cwd, list(argv)))
        if argv[:3] == ["gh", "pr", "list"]:
            if not replies:
                raise RuntimeError("no more canned gh pr list replies")
            return replies.pop(0)
        return ""
    return _fake


def _valid_design_row(**overrides):
    """A fully valid same-repo design-PR row for goal 9 -- passes every check in
    `_is_this_goals_design_pr` unmodified. THE structural rule (round 5 review): every fixture
    below is `_valid_design_row(...)` with EXACTLY ONE deviation from this shape, or an
    explicit, named, minimal combination when the test is itself about how two fields interact
    (`changedFiles` disagreeing with `files`, or an element's own shape) -- never a hand-rolled
    dict that happens to omit some OTHER field a DIFFERENT check would also have caught (round
    5's root cause: three defects, one shape, three different missing fields). `paths=[...]` is
    a convenience -- it derives `files`/`changedFiles` TOGETHER, because a file SET is one
    semantic property, not two independently-driftable fields, so overriding it is still one
    deviation; pass `files=`/`changedFiles=` directly only for a test about their own internal
    (dis)agreement or element shape."""
    paths = overrides.pop("paths", None)
    row = {
        "number": 42,
        "url": "https://x/42",
        "isCrossRepository": False,
        "headRefName": "sdlc/9",
        "files": [{"path": ".sdlc/design/9.md"}, {"path": ".sdlc/design/9-in-brief.md"}],
        "changedFiles": 2,
    }
    if paths is not None:
        row["files"] = [{"path": p} for p in paths]
        row["changedFiles"] = len(paths)
    row.update(overrides)
    return row


def test_merge_design_merges_an_open_mergeable_pr():
    lp = work
    calls = []
    pr = json.dumps([_valid_design_row(mergeable="MERGEABLE", mergeStateStatus="CLEAN")])
    result = lp.merge_design(".sdlc", ON, "9", run=_design_pr_spy(calls, [pr]))
    assert result == "merged PR #42"
    merge_calls = [c for c in calls if c[1][:3] == ["gh", "pr", "merge"]]
    assert merge_calls and merge_calls[0][1] == ["gh", "pr", "merge", "42", "--squash"]


def test_merge_design_reports_conflicts_without_attempting_a_merge():
    calls = []
    pr = json.dumps([_valid_design_row(mergeable="CONFLICTING", mergeStateStatus="DIRTY")])
    result = work.merge_design(".sdlc", ON, "9", run=_design_pr_spy(calls, [pr]))
    assert "conflicts" in result and "42" in result
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "merge"]]


def test_merge_design_and_close_design_never_echo_a_raw_gh_exception_into_their_result():
    """The self-caught phantom-blocker risk (confirm.md:112-113): a `gh` failure whose own message
    contains a trigger word + `#N` must never appear verbatim in the returned string, since that
    string is folded into a posted, later-scanned comment. Rev 7 (plan-review round 9): assert the
    returned string STARTS WITH the fixed failure phrase (not just contains "42"), so this test
    proves it reached the retry-exhausted fallback, not merely that "42" showed up somewhere."""
    def _raise(cwd, argv):
        if argv[:3] == ["gh", "pr", "list"]:
            return json.dumps([_valid_design_row(mergeable="MERGEABLE", mergeStateStatus="CLEAN")])
        raise RuntimeError("needs #999 to be merged first")
    poison = "needs #999"
    m = work.merge_design(".sdlc", ON, "9", run=_raise)
    assert poison not in m and m.startswith("could not merge PR #42")
    c = work.close_design(".sdlc", ON, "9", run=_raise)
    assert poison not in c and c.startswith("could not close PR #42")


def test_merge_design_is_a_clean_noop_with_no_open_pr():
    calls = []
    result = work.merge_design(".sdlc", ON, "9", run=_design_pr_spy(calls))   # default: "[]"
    assert result == "no open design PR found -- nothing to land"
    assert len(calls) == 1                                   # only the lookup, no mutation


def test_merge_design_uses_the_configured_merge_method():
    calls = []
    pr = json.dumps([_valid_design_row(number=5, mergeable="MERGEABLE", mergeStateStatus="CLEAN")])
    cfg = {"work": {"enabled": True, "merge_method": "rebase"}}
    work.merge_design(".sdlc", cfg, "9", run=_design_pr_spy(calls, [pr]))
    merge_calls = [c for c in calls if c[1][:3] == ["gh", "pr", "merge"]]
    assert merge_calls[0][1] == ["gh", "pr", "merge", "5", "--rebase"]


def test_merge_design_derives_the_branch_from_configured_branch_prefix():
    calls = []
    cfg = {"work": {"enabled": True, "branch_prefix": "goal/"}}
    work.merge_design(".sdlc", cfg, "9", run=_design_pr_spy(calls))
    list_calls = [c for c in calls if c[1][:3] == ["gh", "pr", "list"]]
    assert "--head" in list_calls[0][1]
    assert list_calls[0][1][list_calls[0][1].index("--head") + 1] == "goal/9"


def test_merge_design_never_raises_on_an_unreadable_gh_reply():
    def _raise(cwd, argv):
        raise RuntimeError("network is down")
    assert "could not" in work.merge_design(".sdlc", ON, "9", run=_raise)


def test_find_design_pr_treats_blank_stdout_as_an_error_not_an_empty_list():
    """Round 2 finding 1's own repro: blank stdout on an exit-0 `gh pr list` reply (the #78
    proxy-injection shape `_run`'s own docstring documents, work.py:293-311) must be reported as
    unparseable, never silently coerced into 'confirmed no PR'."""
    def _blank(cwd, argv):
        return ""
    pr, err = work._find_design_pr(".sdlc", ON, "9", _blank)
    assert pr is None and err and "could not parse" in err


def test_find_design_pr_refuses_on_more_than_one_open_pr_rather_than_guessing():
    two = json.dumps([_valid_design_row(number=1), _valid_design_row(number=2)])
    def _two(cwd, argv):
        return two
    pr, err = work._find_design_pr(".sdlc", ON, "9", _two)
    assert pr is None
    assert "#1" in err and "#2" in err and "guess" in err
    # neither merge_design nor close_design acts on either PR
    calls = []
    def _spy_two(cwd, argv):
        calls.append((cwd, list(argv)))
        return two
    m = work.merge_design(".sdlc", ON, "9", run=_spy_two)
    assert "guess" in m and not [c for c in calls if c[1][:3] == ["gh", "pr", "merge"]]
    calls.clear()
    c = work.close_design(".sdlc", ON, "9", run=_spy_two)
    assert "guess" in c and not [c2 for c2 in calls if c2[1][:3] == ["gh", "pr", "close"]]


def test_close_design_closes_an_open_pr_with_the_given_comment():
    calls = []
    pr = json.dumps([_valid_design_row(number=7)])
    result = work.close_design(".sdlc", ON, "9", run=_design_pr_spy(calls, [pr]),
                               comment="goal-review: REJECTED")
    assert result == "closed PR #7"
    close_calls = [c for c in calls if c[1][:3] == ["gh", "pr", "close"]]
    assert close_calls[0][1] == ["gh", "pr", "close", "7", "--comment", "goal-review: REJECTED"]


def test_close_design_is_a_clean_noop_with_no_open_pr():
    calls = []
    result = work.close_design(".sdlc", ON, "9", run=_design_pr_spy(calls))
    assert result == "no open design PR found -- nothing to close"
    assert len(calls) == 1


def test_close_design_never_raises_on_an_unreadable_gh_reply():
    def _raise(cwd, argv):
        raise RuntimeError("network is down")
    assert "could not" in work.close_design(".sdlc", ON, "9", run=_raise)


def test_close_design_runs_regardless_of_work_enabled():
    """Closing is the risk-reducing direction -- round 1 finding 6's fix, preserved through the
    round-2 redesign."""
    calls = []
    pr = json.dumps([_valid_design_row(number=7)])
    off = {"work": {"enabled": False}}
    result = work.close_design(".sdlc", off, "9", run=_design_pr_spy(calls, [pr]))
    assert result == "closed PR #7"


def test_merge_design_refuses_when_work_is_disabled():
    """The DELIBERATE asymmetry (round 2 finding 3): merging is gated behind `work.enabled`,
    closing is not. Must refuse cleanly, never silently, and never attempt a `gh` call at all."""
    calls = []
    off = {"work": {"enabled": False}}
    result = work.merge_design(".sdlc", off, "9", run=_design_pr_spy(calls))
    assert "work is off" in result and "by hand" in result
    assert calls == []                                        # not even the lookup ran


def test_merge_design_cli_dispatches_outside_the_generic_commands_gate(tmp_path, capsys,
                                                                        monkeypatch):
    d = _sdlc(tmp_path, ON)
    pr = json.dumps([_valid_design_row(number=3, mergeable="MERGEABLE", mergeStateStatus="CLEAN")])
    calls = []
    monkeypatch.setattr(work, "_run", _design_pr_spy(calls, [pr]))
    rc = work.main(["work.py", "merge-design", d, "9"])
    assert rc == 0
    assert "merged PR #3" in capsys.readouterr().out


def test_close_design_cli_dispatches_with_an_optional_comment_flag(tmp_path, capsys, monkeypatch):
    d = _sdlc(tmp_path, ON)
    pr = json.dumps([_valid_design_row(number=3)])
    calls = []
    monkeypatch.setattr(work, "_run", _design_pr_spy(calls, [pr]))
    rc = work.main(["work.py", "close-design", d, "9", "--comment", "goal-review: REJECTED"])
    assert rc == 0
    assert "closed PR #3" in capsys.readouterr().out
    close_calls = [c for c in calls if c[1][:3] == ["gh", "pr", "close"]]
    assert "--comment" in close_calls[0][1]


def test_merge_design_retries_once_on_a_transient_gh_failure():
    """`_retry_gh`'s one-retry contract (round 2 finding 2)."""
    attempts = {"n": 0}
    pr = json.dumps([_valid_design_row(number=8, mergeable="MERGEABLE", mergeStateStatus="CLEAN")])
    def _flaky(cwd, argv):
        if argv[:3] == ["gh", "pr", "list"]:
            return pr
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        return ""
    result = work.merge_design(".sdlc", ON, "9", run=_flaky, sleep=NOSLEEP)
    assert result == "merged PR #8"
    assert attempts["n"] == 2                                 # exactly one retry, not more


def test_merge_design_gives_up_after_two_failures():
    pr = json.dumps([_valid_design_row(number=8, mergeable="MERGEABLE", mergeStateStatus="CLEAN")])
    def _always_fails(cwd, argv):
        if argv[:3] == ["gh", "pr", "list"]:
            return pr
        raise RuntimeError("still down")
    result = work.merge_design(".sdlc", ON, "9", run=_always_fails, sleep=NOSLEEP)
    assert "could not merge" in result


def test_merge_design_reports_success_when_a_retry_error_actually_landed():
    """Code review, #2482: `gh pr merge` is NOT idempotent -- if the first attempt landed on
    GitHub's own side but the response never reached this process, `_retry_gh`'s second attempt
    fails against an already-merged PR. Simulated here: the merge call always raises, but the
    RE-CHECK `gh pr list` (called after the retry gives up) returns empty -- the PR is gone,
    i.e. it landed. Must report success, not the generic "could not merge" false negative."""
    calls = {"list": 0}
    pr = json.dumps([_valid_design_row(number=8, mergeable="MERGEABLE", mergeStateStatus="CLEAN")])
    def _lands_but_errors(cwd, argv):
        if argv[:3] == ["gh", "pr", "list"]:
            calls["list"] += 1
            return pr if calls["list"] == 1 else "[]"   # gone by the re-check -- it landed
        raise RuntimeError("connection dropped after the merge actually succeeded")
    result = work.merge_design(".sdlc", ON, "9", run=_lands_but_errors, sleep=NOSLEEP)
    assert result == "merged PR #8 (confirmed on re-check after a retry error)"


def test_merge_design_still_reports_failure_when_the_retry_error_was_a_genuine_failure():
    """The other half: if the re-check STILL finds the PR open, the merge genuinely never landed
    -- must not be misread as success just because a re-check happened."""
    pr = json.dumps([_valid_design_row(number=8, mergeable="MERGEABLE", mergeStateStatus="CLEAN")])
    def _genuinely_fails(cwd, argv):
        if argv[:3] == ["gh", "pr", "list"]:
            return pr                            # still open on every re-check too
        raise RuntimeError("still down")
    result = work.merge_design(".sdlc", ON, "9", run=_genuinely_fails, sleep=NOSLEEP)
    assert "could not merge" in result


def test_close_design_reports_success_when_a_retry_error_actually_landed():
    """Mirrors merge_design's own re-check-before-reporting-failure fix, for close."""
    calls = {"list": 0}
    pr = json.dumps([_valid_design_row(number=7)])
    def _lands_but_errors(cwd, argv):
        if argv[:3] == ["gh", "pr", "list"]:
            calls["list"] += 1
            return pr if calls["list"] == 1 else "[]"
        raise RuntimeError("connection dropped after the close actually succeeded")
    result = work.close_design(".sdlc", ON, "9", run=_lands_but_errors, sleep=NOSLEEP)
    assert result == "closed PR #7 (confirmed on re-check after a retry error)"


def test_find_design_pr_refuses_gracefully_on_non_dict_rows():
    """Code review, #2482: a garbled reply can parse as valid JSON while still being e.g. [1, 2]
    -- every element must be validated as a dict, or `_find_design_pr`'s own stated "(pr_row,
    error) -- exactly one set, never raises" contract breaks for both the single-row return and
    the ambiguity branch, and neither merge_design nor close_design wraps this call in a
    try/except to catch a violation of that contract."""
    def _garbled(cwd, argv):
        return "[1, 2]"
    pr, err = work._find_design_pr(".sdlc", ON, "9", _garbled)
    assert pr is None and err and "unexpected" in err
    # and the two real callers must not raise either -- err is truthy, so both report it plainly
    assert work.merge_design(".sdlc", ON, "9", run=_garbled) == "could not check for a design PR: " + err
    assert work.close_design(".sdlc", ON, "9", run=_garbled) == "could not check for a design PR: " + err


def test_merge_design_polls_a_bounded_number_of_times_on_unknown_mergeability():
    unknown = json.dumps([_valid_design_row(number=9, mergeable="UNKNOWN",
                                             mergeStateStatus="UNKNOWN")])
    clean = json.dumps([_valid_design_row(number=9, mergeable="MERGEABLE",
                                           mergeStateStatus="CLEAN")])
    calls = []
    result = work.merge_design(".sdlc", ON, "9", run=_design_pr_spy(calls, [unknown, clean]),
                               sleep=NOSLEEP)
    assert result == "merged PR #9"


def test_merge_design_proceeds_when_mergeability_stays_unknown():
    """A `mergeStateStatus` that never resolves past UNKNOWN still merges rather than parking
    forever -- the plan's own stated tradeoff (a design PR has no CI to wait on)."""
    unknown = json.dumps([_valid_design_row(number=9, mergeable="UNKNOWN",
                                             mergeStateStatus="UNKNOWN")])
    calls = []
    result = work.merge_design(".sdlc", ON, "9", run=_design_pr_spy(calls, [unknown] * 5),
                               sleep=NOSLEEP)
    assert result == "merged PR #9"


# --- #2672: a design PR is identified by its own goal-numbered artifacts, never by branch name
# alone -- a fork sharing the branch name, or a same-repo PR that carries some OTHER goal's design
# files (or none at all), must never be treated as "this goal's own design PR" -----------------


def test_find_design_pr_refuses_when_isCrossRepository_is_missing_or_non_boolean():
    """A fully valid row (round 5 finding 1: BOTH design artifacts present, unlike rev 5's
    own version of this test) with isCrossRepository simply absent -- if this row lacked the
    in-brief sibling, removing the isCrossRepository check would make it fall through to the
    UNRELATED in-brief-missing refusal and stay green for the wrong reason, which is exactly
    what round 5 review caught."""
    row = _valid_design_row()
    del row["isCrossRepository"]
    malformed = json.dumps([row])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: malformed)
    assert pr is None
    assert err and "isCrossRepository is missing or not a boolean" in err


def test_find_design_pr_refuses_when_isCrossRepository_is_present_but_not_a_boolean():
    for bad_value in ("false", 0):
        malformed = json.dumps([_valid_design_row(isCrossRepository=bad_value)])
        pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: malformed)
        assert pr is None
        assert err and "isCrossRepository is missing or not a boolean" in err


def test_find_design_pr_refuses_a_fork_pr_sharing_the_branch_name():
    """The fork fixture is `_valid_design_row(isCrossRepository=True)` -- otherwise a
    byte-for-byte valid same-repo row -- so a pass here can only mean check 1 itself fired,
    never some other missing field standing in for the fork check by accident."""
    fork = json.dumps([_valid_design_row(isCrossRepository=True)])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: fork)
    assert pr is None
    assert err and "cross-repository (fork) PR" in err


def test_find_design_pr_still_finds_a_same_repo_design_pr_despite_a_same_named_fork():
    mixed = json.dumps([
        _valid_design_row(number=43, isCrossRepository=True),
        _valid_design_row(number=42),
    ])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: mixed)
    assert err is None
    assert pr is not None and pr["number"] == 42


def test_close_design_and_merge_design_cli_both_act_on_the_same_repo_pr_never_the_fork(
        tmp_path, capsys, monkeypatch):
    """Round 5 finding 2: item 4's non-vacuity partner proved this at the `_find_design_pr`
    unit level only. This proves it reaches the two documented gestures that actually call
    `gh pr merge`/`gh pr close` -- a mixed reply (fork #43 + this goal's own PR #42) must make
    BOTH gestures act on 42 and never so much as target 43, checked against the fake's own
    call log, not just stdout."""
    d = _sdlc(tmp_path, ON)
    mixed = json.dumps([
        _valid_design_row(number=43, isCrossRepository=True),
        _valid_design_row(number=42),
    ])
    calls = []
    def _spy(cwd, argv):
        calls.append((cwd, list(argv)))
        return mixed
    monkeypatch.setattr(work, "_run", _spy)
    rc = work.main(["work.py", "merge-design", d, "9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "merged PR #42" in out
    merge_calls = [c for c in calls if c[1][:3] == ["gh", "pr", "merge"]]
    assert merge_calls and merge_calls[0][1][3] == "42"
    assert not any(c[1][3] == "43" for c in merge_calls)
    calls.clear()
    rc2 = work.main(["work.py", "close-design", d, "9", "--comment", "goal-review: REJECTED"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "closed PR #42" in out2
    close_calls = [c for c in calls if c[1][:3] == ["gh", "pr", "close"]]
    assert close_calls and close_calls[0][1][3] == "42"
    assert not any(c[1][3] == "43" for c in close_calls)


def test_find_design_pr_refuses_when_the_gh_reply_hits_the_page_limit():
    full_page = json.dumps([_valid_design_row(isCrossRepository=True, number=i + 1)
                             for i in range(work.DESIGN_PR_LIMIT)])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: full_page)
    assert pr is None
    assert err and "limit" in err.lower()


def test_find_design_pr_still_finds_the_real_design_pr_behind_several_same_named_forks():
    forks = [_valid_design_row(isCrossRepository=True, number=100 + i) for i in range(5)]
    real = _valid_design_row(number=7)
    six = json.dumps(forks + [real])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: six)
    assert err is None
    assert pr is not None and pr["number"] == 7


def test_find_design_pr_refuses_a_same_repo_pr_whose_headRefName_does_not_match():
    """Round 5 finding 1: this row is otherwise fully valid (both artifacts present), so a
    pass here can only mean check 2 itself fired -- not a fall-through to some other check."""
    wrong_branch = json.dumps([_valid_design_row(headRefName="sdlc/999")])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: wrong_branch)
    assert pr is None
    assert err and "headRefName does not match" in err


def test_find_design_pr_finds_a_genuine_same_repo_design_pr():
    """PARTNER, not red-first: passes unchanged against BOTH today's shipped code and item 26's
    -- a genuine vacuous positive (TDD cannot make an already-passing test go red). Its detection
    power is proven instead by item 32's "kill the happy path" mutation (M14)."""
    design_pr = json.dumps([_valid_design_row()])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: design_pr)
    assert err is None and pr is not None and pr["number"] == 42


def test_find_design_pr_refuses_a_pr_that_touches_files_outside_sdlc_design():
    """Carries BOTH of this goal's own design artifacts (conditions 4-5 hold) plus one
    unrelated code file -- isolates condition 6's own failure from the missing-brief control
    (item 12) and the wrong-goal control (item 13)."""
    code_pr = json.dumps([_valid_design_row(
        paths=[".sdlc/design/9.md", ".sdlc/design/9-in-brief.md",
               "skills/agrim-loop/scripts/work.py"])])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: code_pr)
    assert pr is None
    assert err and "touches file(s) outside its own design artifacts" in err


def test_find_design_pr_refuses_a_pr_that_never_touches_its_own_design_md():
    """Touching only the in-brief sibling -- never <n>.md itself. Conditions 5-6 would both
    hold if reached, isolating condition 4's own failure from item 12 (condition 5) and item
    10/13 (condition 6)."""
    brief_only = json.dumps([_valid_design_row(paths=[".sdlc/design/9-in-brief.md"])])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: brief_only)
    assert pr is None
    assert err and "does not touch its own design.md" in err


def test_find_design_pr_refuses_a_pr_that_touches_only_its_own_design_md():
    """The `<n>-in-brief.md` sibling is a REQUIRED part of the same commit
    (skills/agrim-goal-design/references/writing-the-artifact.md ~:90), not an optional
    extra. Own `.md` IS present (condition 4 holds) and there is no extra file riding along
    (condition 6 would hold too) -- isolating condition 5's own failure from both."""
    md_only = json.dumps([_valid_design_row(paths=[".sdlc/design/9.md"])])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: md_only)
    assert pr is None
    assert err and "does not include its own in-brief" in err


def test_find_design_pr_refuses_a_pr_that_also_carries_another_goals_design_files():
    """Carries BOTH of this goal's own design artifacts (conditions 4-5 hold) but an
    unrelated goal's design file rides along too -- isolates condition 6 from condition 4,
    from item 12's in-brief-missing control, and from the plain-code-PR shape (item 10)."""
    contaminated = json.dumps([_valid_design_row(
        paths=[".sdlc/design/9.md", ".sdlc/design/9-in-brief.md", ".sdlc/design/42.md"])])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: contaminated)
    assert pr is None
    assert err and "touches file(s) outside its own design artifacts" in err


def test_find_design_pr_refuses_when_the_files_list_looks_truncated():
    truncated = json.dumps([_valid_design_row(files=[{"path": ".sdlc/design/9.md"}],
                                                changedFiles=5)])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: truncated)
    assert pr is None
    assert err and "files/changedFiles reply is malformed or incomplete" in err


def test_find_design_pr_refuses_when_a_files_entry_is_not_a_well_formed_dict():
    bad = json.dumps([_valid_design_row(files=[".sdlc/design/9.md"], changedFiles=1)])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: bad)
    assert pr is None
    assert err and "files/changedFiles reply is malformed or incomplete" in err


def test_find_design_pr_refuses_when_a_files_path_is_not_a_string():
    """Asserts the SPECIFIC reason class, not just the PR number: with the type-check block
    removed, `paths = {9}` (an int, not a string) still fails the LATER own-`.md` check --
    `pr is None` would STILL hold, so a test asserting only that would stay green against a
    mutant that had actually deleted the check it exists to guard (measured under item 32's
    mutation M6 below)."""
    bad = json.dumps([_valid_design_row(files=[{"path": 9}], changedFiles=1)])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: bad)
    assert pr is None
    assert err and "files/changedFiles reply is malformed or incomplete" in err


def test_find_design_pr_refuses_when_changedFiles_is_not_an_int():
    bad = json.dumps([_valid_design_row(changedFiles="1")])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: bad)
    assert pr is None
    assert err and "files/changedFiles reply is malformed or incomplete" in err


def test_find_design_pr_refuses_when_changedFiles_is_a_boolean():
    """`paths=[".../9.md"]` alone would make `changedFiles` default to 1 -- overriding it to
    `True` on top is what makes `len(files) != changed` numerically AGREE (`1 != True` is
    `False`, since `True == 1`). ONLY the explicit bool-exclusion catches this row, isolating
    the bool-trap from the ordinary length-mismatch check (item 14)."""
    bad = json.dumps([_valid_design_row(paths=[".sdlc/design/9.md"], changedFiles=True)])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: bad)
    assert pr is None
    assert err and "files/changedFiles reply is malformed or incomplete" in err


def test_find_design_pr_refuses_a_boolean_pr_number_even_on_an_otherwise_valid_row():
    """A FULLY VALID row in every other respect (same-repo, correct branch, both design
    artifacts, well-typed `changedFiles`) with `number: True` as its ONLY defect, so a pass
    here can only mean the number gate itself fired -- not a fork check, not a files check,
    nothing else."""
    bad = json.dumps([_valid_design_row(number=True)])
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: bad)
    assert pr is None
    assert err and "PR number missing or not a positive integer" in err
    assert "True" not in err


def test_close_design_and_merge_design_cli_both_refuse_a_row_with_a_boolean_pr_number(
        tmp_path, capsys, monkeypatch):
    d = _sdlc(tmp_path, ON)
    bad_number = json.dumps([_valid_design_row(number=True)])
    calls = []
    def _spy(cwd, argv):
        calls.append((cwd, list(argv)))
        return bad_number
    monkeypatch.setattr(work, "_run", _spy)
    rc = work.main(["work.py", "merge-design", d, "9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "True" not in out
    assert "could not check for a design PR" in out
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "merge"]]
    calls.clear()
    rc2 = work.main(["work.py", "close-design", d, "9", "--comment", "goal-review: REJECTED"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "True" not in out2
    assert "could not check for a design PR" in out2
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "close"]]


def test_find_design_pr_requests_every_field_and_limit_the_identity_check_needs():
    def _strict(cwd, argv):
        assert argv[:3] == ["gh", "pr", "list"], argv
        fields = set(argv[argv.index("--json") + 1].split(","))
        for required in ("isCrossRepository", "headRefName", "files", "changedFiles"):
            assert required in fields, f"missing {required!r} in --json {fields}"
        assert argv[argv.index("--limit") + 1] == "30", argv
        return json.dumps([_valid_design_row()])
    pr, err = work._find_design_pr(".sdlc", ON, "9", _strict)
    assert err is None and pr is not None and pr["number"] == 42


def test_close_design_and_merge_design_cli_both_refuse_a_fork_pr(tmp_path, capsys, monkeypatch):
    d = _sdlc(tmp_path, ON)
    fork = json.dumps([_valid_design_row(isCrossRepository=True)])
    calls = []
    def _spy(cwd, argv):
        calls.append((cwd, list(argv)))
        return fork
    monkeypatch.setattr(work, "_run", _spy)
    rc = work.main(["work.py", "merge-design", d, "9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "merged PR #42" not in out
    assert "could not check for a design PR" in out and "fork" in out
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "merge"]]
    rc2 = work.main(["work.py", "close-design", d, "9", "--comment", "goal-review: REJECTED"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "closed PR #42" not in out2
    assert "could not check for a design PR" in out2 and "fork" in out2
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "close"]]


def test_close_design_and_merge_design_cli_both_refuse_a_code_shaped_pr(tmp_path, capsys,
                                                                         monkeypatch):
    d = _sdlc(tmp_path, ON)
    code_pr = json.dumps([_valid_design_row(
        paths=[".sdlc/design/9.md", ".sdlc/design/9-in-brief.md",
               "skills/agrim-loop/scripts/work.py"])])
    calls = []
    def _spy(cwd, argv):
        calls.append((cwd, list(argv)))
        return code_pr
    monkeypatch.setattr(work, "_run", _spy)
    rc = work.main(["work.py", "merge-design", d, "9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "merged PR #42" not in out
    assert "could not check for a design PR" in out
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "merge"]]
    rc2 = work.main(["work.py", "close-design", d, "9", "--comment", "goal-review: REJECTED"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "closed PR #42" not in out2
    assert "could not check for a design PR" in out2
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "close"]]


def test_close_design_and_merge_design_cli_never_echo_a_malicious_extra_file_path(
        tmp_path, capsys, monkeypatch):
    """Round 5 finding 3: reaches the file-PATH (check-6) refusal specifically -- correct
    headRefName, both own artifacts present, PLUS a malicious extra file. Isolated from item
    25 (malicious headRefName) below by construction: mutating check 6's own leak site alone
    (item 32's mutation M12) must turn ONLY this test red; mutating check 2's leak site alone
    (M13) must leave this test green -- proven in the prototype, not asserted."""
    d = _sdlc(tmp_path, ON)
    injected = json.dumps([_valid_design_row(
        paths=[".sdlc/design/9.md", ".sdlc/design/9-in-brief.md",
               ".sdlc/design/needs #123.md"])])
    calls = []
    def _spy(cwd, argv):
        calls.append((cwd, list(argv)))
        return injected
    monkeypatch.setattr(work, "_run", _spy)
    rc = work.main(["work.py", "merge-design", d, "9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "merged PR #42" not in out
    assert "could not check for a design PR" in out
    assert "#123" not in out
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "merge"]]
    calls.clear()
    rc2 = work.main(["work.py", "close-design", d, "9", "--comment", "goal-review: REJECTED"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "closed PR #42" not in out2
    assert "could not check for a design PR" in out2
    assert "#123" not in out2
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "close"]]


def test_close_design_and_merge_design_cli_never_echo_a_malicious_headRefName(
        tmp_path, capsys, monkeypatch):
    """Round 5 finding 3's second case: both own artifacts present, no extra file -- ONLY the
    branch name is malicious, reaching the headRefName (check-2) refusal specifically.
    Isolated from item 24 the same way, in the other direction."""
    d = _sdlc(tmp_path, ON)
    injected = json.dumps([_valid_design_row(headRefName="sdlc/9-for-#999")])
    calls = []
    def _spy(cwd, argv):
        calls.append((cwd, list(argv)))
        return injected
    monkeypatch.setattr(work, "_run", _spy)
    rc = work.main(["work.py", "merge-design", d, "9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "merged PR #42" not in out
    assert "could not check for a design PR" in out
    assert "#999" not in out
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "merge"]]
    calls.clear()
    rc2 = work.main(["work.py", "close-design", d, "9", "--comment", "goal-review: REJECTED"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "closed PR #42" not in out2
    assert "could not check for a design PR" in out2
    assert "#999" not in out2
    assert not [c for c in calls if c[1][:3] == ["gh", "pr", "close"]]


def test_find_design_pr_refuses_a_padded_reply_with_a_duplicate_path():
    """`9.md`, `9-in-brief.md`, `9.md` with changedFiles 3 passes the length-agreement check and
    would collapse into the valid two-file SET -- the duplicate-path check must refuse it first."""
    row = _valid_design_row(files=[{"path": ".sdlc/design/9.md"},
                                   {"path": ".sdlc/design/9-in-brief.md"},
                                   {"path": ".sdlc/design/9.md"}], changedFiles=3)
    pr, err = work._find_design_pr(".sdlc", ON, "9", lambda cwd, argv: json.dumps([row]))
    assert pr is None
    assert err and "lists a path more than once" in err


def test_merge_design_reports_failure_not_success_when_the_recheck_finds_a_fork_row():
    """A fork sharing the branch name existing on the RE-CHECK proves nothing about whether
    OUR merge landed -- must not be read as the "(None, None) == gone" success shape. The
    fork row is `_valid_design_row(isCrossRepository=True)` per the structural rule."""
    calls = {"list": 0}
    pr = json.dumps([_valid_design_row(number=8, mergeable="MERGEABLE",
                                        mergeStateStatus="CLEAN")])
    fork = json.dumps([_valid_design_row(number=99, isCrossRepository=True)])
    def _run(cwd, argv):
        if argv[:3] == ["gh", "pr", "list"]:
            calls["list"] += 1
            return pr if calls["list"] == 1 else fork
        raise RuntimeError("connection dropped after the merge may or may not have landed")
    result = work.merge_design(".sdlc", ON, "9", run=_run, sleep=NOSLEEP)
    assert "could not merge" in result
    assert "confirmed on re-check" not in result


def test_close_design_reports_failure_not_success_when_the_recheck_finds_a_fork_row():
    calls = {"list": 0}
    pr = json.dumps([_valid_design_row(number=7)])
    fork = json.dumps([_valid_design_row(number=99, isCrossRepository=True)])
    def _run(cwd, argv):
        if argv[:3] == ["gh", "pr", "list"]:
            calls["list"] += 1
            return pr if calls["list"] == 1 else fork
        raise RuntimeError("connection dropped after the close may or may not have landed")
    result = work.close_design(".sdlc", ON, "9", run=_run)
    assert "could not close" in result
    assert "confirmed on re-check" not in result


def test_merge_design_reports_failure_not_success_when_the_recheck_finds_a_code_shaped_row():
    calls = {"list": 0}
    pr = json.dumps([_valid_design_row(number=8, mergeable="MERGEABLE",
                                        mergeStateStatus="CLEAN")])
    code_pr = json.dumps([_valid_design_row(
        number=55, paths=[".sdlc/design/9.md", ".sdlc/design/9-in-brief.md",
                           "skills/agrim-loop/scripts/work.py"])])
    def _run(cwd, argv):
        if argv[:3] == ["gh", "pr", "list"]:
            calls["list"] += 1
            return pr if calls["list"] == 1 else code_pr
        raise RuntimeError("connection dropped after the merge may or may not have landed")
    result = work.merge_design(".sdlc", ON, "9", run=_run, sleep=NOSLEEP)
    assert "could not merge" in result
    assert "confirmed on re-check" not in result


def test_close_design_reports_failure_not_success_when_the_recheck_finds_a_code_shaped_row():
    calls = {"list": 0}
    pr = json.dumps([_valid_design_row(number=7)])
    code_pr = json.dumps([_valid_design_row(
        number=55, paths=[".sdlc/design/9.md", ".sdlc/design/9-in-brief.md",
                           "skills/agrim-loop/scripts/work.py"])])
    def _run(cwd, argv):
        if argv[:3] == ["gh", "pr", "list"]:
            calls["list"] += 1
            return pr if calls["list"] == 1 else code_pr
        raise RuntimeError("connection dropped after the close may or may not have landed")
    result = work.close_design(".sdlc", ON, "9", run=_run)
    assert "could not close" in result
    assert "confirmed on re-check" not in result


# --- Review round 4 (author-blind Claude subagent, generation 44420a5f at 55e2d183) -------------------

def test_documented_run_resolved_review_cli_gesture_succeeds_on_the_command_route(tmp_path, monkeypatch, capsys):
    """B1 (this session's own regression, 3aab3fd4). `_flag()` returns `""` for an absent flag, never
    `None`, and `run_resolved_review`'s new B2 check was `verdict is not None` -- so the CLI always
    passed `verdict=""`, which is not None, and every command/process route refused with "--verdict
    is only for the inline route" no matter what. The documented gesture (`landing.md`/`running.md`)
    never passes --verdict for this route at all. Run through `work.main` exactly as documented,
    with a REAL resolver result (review.host: command) so the route re-resolution in B2 can't hide
    a still-broken CLI plumbing bug behind a mocked one."""
    d = _sdlc(tmp_path, {**ON, "review": {"host": "command", "command": "codex exec -"}}); goal = "2577"
    root = pathlib.Path(d) / "state"; gen = root / "review-generations" / "gen"; gen.mkdir(parents=True)
    (gen / "brief.md").write_text("brief")
    worktree = tmp_path / "worktree"; worktree.mkdir()
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"generation_id": "gen", "goal": goal, "brief": "review-generations/gen/brief.md",
                                    "brief_sha256": hashlib.sha256(b"brief").hexdigest()}))
    resolution = gen / "resolution.json"
    resolution.write_text(json.dumps(_load("reviewer").resolve(d)))
    assert json.loads(resolution.read_text())["mechanism"] == "command"   # the real, unmocked resolver
    fake_reviewer = types.SimpleNamespace(resolve=_load("reviewer").resolve,
                                          run_review=lambda *_a: (print("VERDICT: approve") or 0))
    original = work._load
    monkeypatch.setattr(work, "_load", lambda name: fake_reviewer if name == "reviewer" else original(name))
    work._save(d, goal, {"worktree": str(worktree), "pr": 7})
    rc = work.main(["work.py", "run-resolved-review", d, goal, "--manifest", str(manifest),
                   "--resolution", str(resolution)])
    err = capsys.readouterr().err
    assert rc == 0, err
    assert json.loads((root / "review-results" / "gen.json").read_text())["verdict"] == "approve"


def test_a_corrupt_merge_delivery_file_for_a_different_goal_does_not_wedge_this_goals_finish(tmp_path):
    """#1a. `_merge_delivery_complete` returned False on the FIRST unparseable file in
    `state/merge-deliveries/`, regardless of whose it was -- so one corrupt file (any goal's, or a
    stray non-JSON file) parked EVERY later `finish()` for EVERY goal, forever, since nothing ever
    calls a later finish (the same class of leak round 3's B1 fixed). A file that cannot even be
    attributed to a goal must not block a DIFFERENT goal's own, otherwise-complete delivery."""
    d = _sdlc(tmp_path); goal = _started(d)
    deliveries = pathlib.Path(d) / "state" / "merge-deliveries"; deliveries.mkdir(parents=True)
    (deliveries / "corrupt.json").write_text("{not json")
    (deliveries / "mine.json").write_text(json.dumps({"goal": goal, "entry_delivered": True, "journal_delivered": True}))
    assert work._merge_delivery_complete(d, goal) is True


# --- #2138: an org-locked `gates.hard_plan_gate` is not bypassed by `.allow-direct-edits` ---------
# The lock fact already exists at the seam (`managed_settings.gated_check(...)["locked"]`) and was
# being discarded by `effective_hard_plan_gate`, so a local `touch .sdlc/.allow-direct-edits` let a
# member push an unplanned source branch through an ORG-LOCKED gate. These pin `pr()` -- the
# host-agnostic enforcement point -- and use the DOCUMENTED gesture for the sentinel (an EMPTY file
# via `touch`), not a Python `write_text` that only happens to produce the same file.

GATE_ON = {"work": {"enabled": True}, "gates": {"hard_plan_gate": {"enabled": True}}}
LOCK_ON = {"version": 1, "status": "ok", "locked": {"gates.hard_plan_gate": {"enabled": True}}}


def _gate_pr(tmp_path, managed=None, plan=False, sentinel=False, memo=None):
    """A started goal with the LOCAL gate on, whose branch diff is `app.py`. `managed` (dict or raw
    str) is the org file `.sdlc/managed-settings.json` and, when given, also marks the checkout
    adopted through `managed_settings.project_id`. A plan on disk is also ON THE BRANCH here --
    otherwise the sibling `_plan_missing_from_branch` guard refuses first and proves nothing about
    this one. Returns (sdlc_dir, config, goal, run)."""
    cfg = json.loads(json.dumps(GATE_ON))
    if managed is not None:
        cfg["managed_settings"] = {"project_id": "proj-1"}
    d = _sdlc(tmp_path, cfg)
    if managed is not None:
        (pathlib.Path(d) / "managed-settings.json").write_text(
            managed if isinstance(managed, str) else json.dumps(managed))
    goal = _started(d, pr="")
    if plan:
        _plan(d)
    if sentinel:
        subprocess.run(["touch", str(pathlib.Path(d) / ".allow-direct-edits")], check=True)
    if memo is not None:
        rec = work._record(d, goal)
        rec["hard_plan_gate"] = memo
        work._save(d, goal, rec)
    run = _runner([
        ("rev-list", "1"),
        ("ls-files", ".sdlc/plans/0001-x.md" if plan else ""),
        ("check-ignore", RuntimeError("::\t.sdlc/plans/0001-x.md")),
        ("git diff --name-only", "app.py"),
        ("log -1", "feat: x"),
        ("pulls?head", "31"),
    ])
    return d, cfg, goal, run


def _org_locked_refusal(sdlc_dir):
    """The EXACT refusal `pr()` gives an unplanned source branch under an org lock: it names the
    lock and the plan path, and never offers `touch` -- under a lock that would be a false remedy."""
    return ("hard_plan_gate is on and this goal has no plan: write it to "
            f"{pathlib.Path(sdlc_dir, 'plans', '0001-x.md').as_posix()} and re-run "
            "(nothing pushed). The gate is org-locked "
            f"({pathlib.Path(sdlc_dir, 'managed-settings.json').as_posix()}), so the "
            ".allow-direct-edits sentinel does not apply")


def test_org_lock_pr_ignores_the_sentinel_when_the_key_is_org_locked_on(tmp_path):
    """1a. THE HOLE. Org lock ON, sentinel present, no plan: refused, nothing pushed, and the
    refusal must not tell the member to `touch` the very file that is already there."""
    d, cfg, goal, run = _gate_pr(tmp_path, managed=LOCK_ON, sentinel=True)
    out = work.pr(d, cfg, goal, run=run)
    assert out == _org_locked_refusal(d), out
    assert "no plan" in out and "org-locked" in out
    assert pathlib.Path(d, "plans", "0001-x.md").as_posix() in out
    assert "touch" not in out
    assert not any("git push" in c for c in run.calls)


def test_org_lock_pr_honours_the_sentinel_when_not_adopted(tmp_path):
    """1b (pin). No org file at all -- every install in the wild -- keeps today's behaviour: the
    documented escape hatch still works."""
    d, cfg, goal, run = _gate_pr(tmp_path, sentinel=True)
    assert work.pr(d, cfg, goal, run=run) == "PR #31"


def test_org_lock_pr_proceeds_under_a_lock_when_this_goal_has_a_plan(tmp_path):
    """1c (pin). The lock changes nothing for a goal that planned: the gate asks for a plan, not
    for permission."""
    d, cfg, goal, run = _gate_pr(tmp_path, managed=LOCK_ON, plan=True)
    assert work.pr(d, cfg, goal, run=run) == "PR #31"


def test_org_lock_pr_re_asks_a_legacy_on_memo_and_rewrites_it(tmp_path):
    """1d. A pre-upgrade memo `"on"` is ambiguous -- it may have been locked -- so a goal already
    mid-flight at upgrade must be asked again, not read as unlocked. The memo comes back in the new
    vocabulary."""
    d, cfg, goal, run = _gate_pr(tmp_path, managed=LOCK_ON, sentinel=True, memo="on")
    out = work.pr(d, cfg, goal, run=run)
    assert out == _org_locked_refusal(d), out
    assert "touch" not in out
    assert work._record(d, goal)["hard_plan_gate"] == "on:locked"


def test_org_lock_pr_asks_managed_settings_at_most_once_per_goal_and_keeps_the_lock_bit(tmp_path, monkeypatch):
    """1e. `pr()` runs `1 + N` times per goal; the org answer is memoised. Calls 2 and 3 never read
    the file again, so the lock bit has to ride the memo -- each call must still give the org-locked
    refusal (no `touch`), not fall back to the unlocked text."""
    d, cfg, goal, run = _gate_pr(tmp_path, managed=LOCK_ON)
    calls = []
    real = work.managed_settings.gated_check
    monkeypatch.setattr(work.managed_settings, "gated_check",
                        lambda *a, **k: (calls.append(a[2]), real(*a, **k))[1])
    for _ in range(3):
        out = work.pr(d, cfg, goal, run=run)
        assert out == _org_locked_refusal(d), out
        assert "touch" not in out
    assert calls == ["gates.hard_plan_gate"], calls
    assert work._record(d, goal)["hard_plan_gate"] == "on:locked"


def test_org_lock_pr_parks_on_an_unverifiable_org_file_before_the_sentinel(tmp_path):
    """1f (pin). A refusing status PARKs before the sentinel is ever consulted -- the governed check
    runs FIRST, so a member whose file no longer verifies cannot `touch` past it."""
    d, cfg, goal, run = _gate_pr(
        tmp_path, managed={"version": 1, "status": "locked-key-unverifiable"}, sentinel=True)
    out = work.pr(d, cfg, goal, run=run)
    assert out.startswith("PARK:"), out
    assert not any("git push" in c for c in run.calls)


# --- #258: the plan-review verdict is a record ---------------------------------------------------

#: `plan_copies`' second worktree read: is the branch copy COMMITTED AND UNMODIFIED, so its disk bytes
#: are the branch's bytes? Whole-argv, like LS_FILES: a pathspec-less `git status --porcelain` is
#: non-empty on any dirty worktree and would silently disqualify every branch copy.
STATUS = "git status --porcelain -- .sdlc/plans/0001-x.md"


@pytest.mark.parametrize("case", ["main only", "branch only", "tracked but modified", "no work record"])
def test_plan_copies_finds_the_main_and_the_committed_branch_copy(tmp_path, case):
    """#258: ONE resolver finds both copies of a plan -- the main checkout's (no git call) and the
    goal worktree's, which counts only when it is tracked (so it IS on the branch) and unmodified
    (so its disk bytes are the branch's). The brief, the record writer and the `pr` gate all use it,
    so a remedy one of them names is always satisfiable at the other two."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    rec = work._record(d, goal)
    wt_copy = pathlib.Path(rec["worktree"]) / ".sdlc" / "plans" / "0001-x.md"
    wt_copy.parent.mkdir(parents=True)
    wt_copy.write_text("# plan\n")
    main = pathlib.Path(d) / "plans" / "0001-x.md"
    if case in ("main only", "no work record"):
        main.parent.mkdir(parents=True)
        main.write_text("# plan\n")
    handlers = {
        "main only": [(LS_FILES, "")],
        "branch only": [(LS_FILES, ".sdlc/plans/0001-x.md"), (STATUS, "")],
        "tracked but modified": [(LS_FILES, ".sdlc/plans/0001-x.md"),
                                 (STATUS, " M .sdlc/plans/0001-x.md")],
        "no work record": [],
    }[case]
    run = _runner(handlers)
    got = work.plan_copies(d, goal, None if case == "no work record" else rec, run)
    expected = {"main only": (main, None), "branch only": (None, wt_copy),
                "tracked but modified": (None, None), "no work record": (main, None)}[case]
    assert got == expected, (case, got)
    if case == "no work record":
        assert run.calls == []


PLAN_A = b"# plan\n1. step A\n"
WORK_OFF = {"work": {"enabled": False}}


def _plan_bytes(d, data=PLAN_A, main=True, branch=False):
    """Write this goal's plan where the phases put it -- the main checkout's `<d>/plans/` (`main`),
    and/or the landing copy in `_started`'s worktree (`branch`) -- and return the bytes' sha256. The
    default is the P4 shape: the plan in the main checkout, no copy on the branch yet."""
    targets = []
    if main:
        targets.append(pathlib.Path(d) / "plans" / "0001-x.md")
    if branch:
        targets.append(pathlib.Path(d) / "work" / "0001-x" / ".sdlc" / "plans" / "0001-x.md")
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _gate_record(d):
    return pathlib.Path(d) / "state" / "gates" / "0001-x.json"


def _plan_review_events(d):
    return [e for e in journal_events(ledger, d) if e["kind"] == "gate"]


def test_record_plan_review_writes_the_record_for_the_current_plan_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    d = _sdlc(tmp_path); goal = _started(d); sha = _plan_bytes(d)
    out = work.record_plan_review(d, ON, goal, "SOUND", sha, run=_runner([]))
    on_disk = json.loads(_gate_record(d).read_text())
    for got in (out, on_disk):
        assert got["goal"] == "0001-x.md"
        assert got["plan"] == ".sdlc/plans/0001-x.md"
        assert got["plan_hash"] == sha
        assert got["verdict"] == "pass"
        assert got["reviewer_route"] == {"mechanism": "subagent", "host": "claude", "verified": True}
        assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", got["at"])
    assert "reason" not in on_disk
    assert out["path"] == str(_gate_record(d))
    assert sorted(p.name for p in _gate_record(d).parent.iterdir()) == ["0001-x.json"]


@pytest.mark.parametrize("word,mapped", [("SOUND", "pass"), ("sound-with-refinements", "warn"),
                                         ("FIX-FIRST", "block")])
def test_record_plan_review_maps_the_skill_verdicts(tmp_path, word, mapped):
    d = _sdlc(tmp_path); goal = _started(d); sha = _plan_bytes(d)
    assert work.record_plan_review(d, ON, goal, word, sha, run=_runner([]))["verdict"] == mapped
    with pytest.raises(ValueError) as refused:
        work.record_plan_review(d, ON, goal, "approve", sha, run=_runner([]))
    for name in ("SOUND", "SOUND-WITH-REFINEMENTS", "FIX-FIRST"):
        assert name in str(refused.value)


def test_record_plan_review_refuses_a_sha_that_is_not_the_current_plan(tmp_path):
    """The verdict binds to the bytes the brief named: a plan edited after the brief was built (a
    refinement applied before recording) is not the plan that was reviewed."""
    d = _sdlc(tmp_path); goal = _started(d)
    old = _plan_bytes(d)
    _plan_bytes(d, PLAN_A + b"2. step B\n")
    with pytest.raises(ValueError, match="fresh plan-review"):
        work.record_plan_review(d, ON, goal, "SOUND", old, run=_runner([]))
    assert not _gate_record(d).exists()


@pytest.mark.parametrize("value", ["", "abc"])
def test_record_plan_review_refuses_a_malformed_sha(tmp_path, value):
    d = _sdlc(tmp_path); goal = _started(d); _plan_bytes(d)
    with pytest.raises(ValueError, match="Plan sha256:"):
        work.record_plan_review(d, ON, goal, "SOUND", value, run=_runner([]))
    assert not _gate_record(d).exists()


def test_record_plan_review_refuses_when_no_plan_resolves(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    with pytest.raises(ValueError, match="no plan for"):
        work.record_plan_review(d, ON, goal, "SOUND", "0" * 64, run=_runner([]))
    assert not _gate_record(d).exists()


def test_record_plan_review_hashes_the_branch_copy_when_the_main_checkout_has_none(tmp_path):
    """B1: a plan that is only on the branch (the shape #2575 measured) can still be recorded."""
    d = _sdlc(tmp_path); goal = _started(d)
    sha = _plan_bytes(d, main=False, branch=True)
    out = work.record_plan_review(d, ON, goal, "SOUND", sha,
                                  run=_runner([(LS_FILES, ".sdlc/plans/0001-x.md")]))
    assert out["plan_hash"] == sha and out["plan"] == ".sdlc/plans/0001-x.md"
    assert json.loads(_gate_record(d).read_text())["plan_hash"] == sha


def test_record_plan_review_refuses_while_the_branch_copy_differs(tmp_path):
    """A verdict on one copy is never recorded while the branch publishes another. The remedy needs
    no commit during plan-review: a tracked copy with uncommitted edits is not a branch copy, so the
    re-record checks the main copy alone -- and `pr` refuses an uncommitted worktree anyway."""
    d = _sdlc(tmp_path); goal = _started(d)
    sha_a = _plan_bytes(d, PLAN_A)
    _plan_bytes(d, PLAN_A + b"2. only on the branch\n", main=False, branch=True)
    tracked = [(LS_FILES, ".sdlc/plans/0001-x.md")]
    with pytest.raises(ValueError) as refused:
        work.record_plan_review(d, ON, goal, "SOUND", sha_a, run=_runner(tracked))
    assert "differs from the reviewed copy" in str(refused.value)
    assert "leave it uncommitted" in str(refused.value)
    assert "work.py commit" not in str(refused.value)
    assert not _gate_record(d).exists()
    _plan_bytes(d, PLAN_A, main=False, branch=True)          # the remedy: copied over, uncommitted
    out = work.record_plan_review(d, ON, goal, "SOUND", sha_a,
                                  run=_runner(tracked + [(STATUS, " M .sdlc/plans/0001-x.md")]))
    assert out["plan_hash"] == sha_a


def test_record_plan_review_refuses_an_unsafe_goal(tmp_path):
    d = _sdlc(tmp_path)
    with pytest.raises(ValueError, match="unsafe goal"):
        work.record_plan_review(d, ON, "../evil", "SOUND", "0" * 64, run=_runner([]))


@pytest.mark.parametrize("gate_on", [False, True])
def test_record_plan_review_keeps_no_record_without_a_work_record(tmp_path, capsys, gate_on):
    """R-c: with work on but no work record under this `.sdlc` -- `/agrim-goal` never runs
    `work.py start`, and a goal worktree's `.sdlc` has none -- the verb validates, still mirrors the
    verdict, keeps NO file, and says so. It must not refuse (that would fail every `/agrim-goal`,
    gate off or on). The one reader, `pr`, needs the same work record, so it still fails closed."""
    cfg = {**ON, **JOURNAL_ON}
    if gate_on:
        cfg["gates"] = {"plan_review": {"enabled": True}}
    d = _sdlc(tmp_path, cfg); sha = _plan_bytes(d)
    run = _runner([])
    out = work.record_plan_review(d, cfg, "0001-x.md", "SOUND", sha, run=run)
    err = capsys.readouterr().err
    assert out["path"] is None
    assert not (pathlib.Path(d) / "state" / "gates").exists()
    assert run.calls == []
    events = _plan_review_events(d)
    assert len(events) == 1 and events[0]["verdict"] == "pass", events
    assert "no record is kept" in err
    assert ("main checkout" in err) is gate_on, err


def test_record_plan_review_overwrites_an_earlier_verdict(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d); sha = _plan_bytes(d)
    work.record_plan_review(d, ON, goal, "FIX-FIRST", sha, run=_runner([]))
    work.record_plan_review(d, ON, goal, "SOUND", sha, run=_runner([]))
    assert json.loads(_gate_record(d).read_text())["verdict"] == "pass"


def test_record_plan_review_keeps_the_prior_record_when_the_write_fails(tmp_path, monkeypatch):
    """Atomic replace: a failed re-record leaves the prior record whole, and no temp file behind."""
    d = _sdlc(tmp_path); goal = _started(d); sha = _plan_bytes(d)
    work.record_plan_review(d, ON, goal, "SOUND", sha, run=_runner([]))

    def boom(*_a, **_k):
        raise OSError("boom")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        work.record_plan_review(d, ON, goal, "FIX-FIRST", sha, run=_runner([]))
    monkeypatch.undo()
    assert json.loads(_gate_record(d).read_text())["verdict"] == "pass"
    assert sorted(p.name for p in _gate_record(d).parent.iterdir()) == ["0001-x.json"]


def test_record_plan_review_keeps_no_record_when_work_is_off(tmp_path, capsys):
    """Nothing reads the record without `work.py pr` and nothing prunes it without `work.py finish`
    -- both need `work.enabled` -- so with work off no file is kept (no table nothing prunes)."""
    d = _sdlc(tmp_path, WORK_OFF); sha = _plan_bytes(d)
    out = work.record_plan_review(d, WORK_OFF, "0001-x.md", "SOUND", sha, run=_runner([]))
    assert out["path"] is None
    assert not (pathlib.Path(d) / "state" / "gates").exists()
    assert "work.enabled is off" in capsys.readouterr().err


def test_record_plan_review_mirrors_one_gate_event(tmp_path):
    """One emitter, in Python (the #1013/#1626 double-emission lesson): the skill no longer also
    types `loop.py emit gate --gate plan_review`."""
    cfg = {**ON, **JOURNAL_ON}
    d = _sdlc(tmp_path, cfg); goal = _started(d); sha = _plan_bytes(d)
    work.record_plan_review(d, cfg, goal, "SOUND", sha, reason="ok", run=_runner([]))
    events = _plan_review_events(d)
    assert len(events) == 1, events
    assert (events[0]["gate"], events[0]["verdict"], events[0]["why"]) == ("plan_review", "pass", "ok")


def test_record_plan_review_cli(tmp_path, capsys):
    def cli(d, *args):
        code = work.main(["work.py", "record-plan-review", d, "0001-x.md", *args])
        return code, capsys.readouterr()

    d = _sdlc(tmp_path / "on"); _started(d); sha = _plan_bytes(d)
    code, got = cli(d, "--verdict", "SOUND", "--plan-sha256", sha)
    assert code == 0, got.err
    assert json.loads(got.out)["plan_hash"] == sha

    off = _sdlc(tmp_path / "off", WORK_OFF); _plan_bytes(off)
    code, got = cli(off, "--verdict", "SOUND", "--plan-sha256", sha)
    assert code == 0 and "no record is kept" in got.err and "work.enabled is off" in got.err, got

    unstarted = _sdlc(tmp_path / "unstarted"); _plan_bytes(unstarted)
    code, got = cli(unstarted, "--verdict", "SOUND", "--plan-sha256", sha)
    assert code == 0 and "no record is kept" in got.err, got

    for args in (["--verdict", "SOUND"], ["--plan-sha256", sha]):
        code, got = cli(d, *args)
        assert code == 2 and "usage: work.py record-plan-review" in got.err, (args, got)

    code, got = cli(d, "--verdict", "SOUND", "--plan-sha256", "")   # a `sed` that found no line
    assert code == 2 and "Plan sha256:" in got.err and "usage:" not in got.err, got

    code, got = cli(d, "--verdict", "SOUND", "--plan-sha256", sha, "--reason", "a\nb")
    assert code == 2, got

    _plan_bytes(d, PLAN_A + b"2. edited after the brief\n")
    code, got = cli(d, "--verdict", "SOUND", "--plan-sha256", sha)
    assert code == 2 and "fresh plan-review" in got.err, got

    assert work.main(["work.py", "record-plan-review", "--help"]) == 0
    assert "usage: work.py record-plan-review" in capsys.readouterr().out


PLAN_REVIEW_ON = {"work": {"enabled": True}, "gates": {"plan_review": {"enabled": True}}}
TRACKED_PLAN = ".sdlc/plans/0001-x.md"
SHA_A = hashlib.sha256(PLAN_A).hexdigest()


def _review_pr(tmp_path, config=None, plan=PLAN_A, main=True, branch=True, record=None, tracked=None):
    """A started goal ready for `pr()` -- clean worktree, one commit ahead -- with its plan in the main
    checkout (`main`) and/or committed on the branch (`branch`), and optionally a recorded verdict.
    `check-ignore` -> "" (exit 0) is an IGNORING repo, which is what `branch=False` means. `STATUS`
    falls through to "" (clean). The record is written with its own runner, so `run.calls` holds only
    `pr()`'s calls."""
    cfg = PLAN_REVIEW_ON if config is None else config
    d = _sdlc(tmp_path, cfg)
    goal = _started(d, pr="")
    sha = _plan_bytes(d, plan, main=main, branch=branch)
    if tracked is None:
        tracked = TRACKED_PLAN if branch else ""
    if record:
        work.record_plan_review(d, cfg, goal, record, sha, run=_runner([(LS_FILES, tracked)]))
    run = _runner([("rev-list", "1"), (LS_FILES, tracked), ("check-ignore", ""),
                   ("log -1", "feat: x"), ("pulls?head", "31")])
    return d, cfg, goal, run


def _wt_plan(d):
    return pathlib.Path(d) / "work" / "0001-x" / ".sdlc" / "plans" / "0001-x.md"


def _main_plan(d):
    return pathlib.Path(d) / "plans" / "0001-x.md"


def _tail(d, hashed):
    """The tail every `gates.plan_review` refusal ends with: the file it hashed, and what it does NOT
    cover -- stated in the refusal itself, not only in the docs."""
    return (f" Hashed: {hashed}. Covers the goal's plan .md only — not "
            f"{pathlib.Path(d, 'plans', '0001-x.slices.json').as_posix()}, and not a design PR's "
            ".sdlc/design/<n>.md.")


def _pushed(run):
    return any("git push" in c for c in run.calls) or any(c.startswith("gh ") for c in run.calls)


@pytest.mark.parametrize("cfg", [ON, {"work": {"enabled": True}, "gates": True},
                                 {"work": {"enabled": True}, "gates": {"plan_review": {"enabled": "off"}}}],
                         ids=["no-gates", "scalar-gates", "enabled-off"])
def test_plan_review_gate_off_changes_nothing(tmp_path, cfg):
    """A pin of the off path: it passes before #258 too. Its control is C6 (`_plan_review_on` forced
    True turns every one of these into a refusal). Off, the gate makes ZERO calls: the one `ls-files`
    is the sibling plan guard's."""
    d, cfg, goal, run = _review_pr(tmp_path, config=cfg)
    assert work.pr(d, cfg, goal, run=run) == "PR #31"
    assert run.calls.count(LS_FILES) == 1, run.calls


def test_plan_review_gate_refuses_a_plan_with_no_record(tmp_path):
    d, cfg, goal, run = _review_pr(tmp_path)
    out = work.pr(d, cfg, goal, run=run)
    assert out == ("gates.plan_review is on and this goal's plan has no recorded review: run "
                   "plan-review, record its verdict with `work.py record-plan-review " + d
                   + " 0001-x.md --verdict SOUND|SOUND-WITH-REFINEMENTS|FIX-FIRST --plan-sha256 "
                   "<the brief's Plan sha256>` and re-run (nothing pushed)."
                   + _tail(d, _wt_plan(d).as_posix())), out
    assert ".slices.json" in out and ".sdlc/design/" in out
    assert not any("git push" in c for c in run.calls)
    assert not any(c.startswith("gh ") for c in run.calls)


@pytest.mark.parametrize("gates", [{"plan_review": True}, {"plan_review": {"enabled": "true"}}])
def test_plan_review_gate_reads_a_scalar_or_generous_enabled_as_on(tmp_path, gates):
    d, cfg, goal, run = _review_pr(tmp_path, config={"work": {"enabled": True}, "gates": gates})
    assert "has no recorded review" in work.pr(d, cfg, goal, run=run)
    assert not _pushed(run)


def test_plan_review_gate_refuses_a_fix_first_verdict(tmp_path):
    d, cfg, goal, run = _review_pr(tmp_path, record="FIX-FIRST")
    out = work.pr(d, cfg, goal, run=run)
    assert "FIX-FIRST (block)" in out, out
    assert not _pushed(run)


def test_plan_review_gate_refuses_a_plan_edited_after_its_review(tmp_path):
    d, cfg, goal, run = _review_pr(tmp_path, record="SOUND")
    edited = PLAN_A + b"2. step B, added after the review\n"
    _plan_bytes(d, edited, main=True, branch=True)
    out = work.pr(d, cfg, goal, run=run)
    assert "changed after its review" in out and SHA_A[:12] in out, out
    assert hashlib.sha256(edited).hexdigest() not in out      # never hand over the hash to copy
    assert not _pushed(run)


@pytest.mark.parametrize("verdict", ["SOUND", "SOUND-WITH-REFINEMENTS"])
def test_plan_review_gate_proceeds_on_a_fresh_matching_record(tmp_path, verdict):
    """Passes before #258 (nothing refused then); its control is C2(ii)."""
    d, cfg, goal, run = _review_pr(tmp_path, record=verdict)
    assert work.pr(d, cfg, goal, run=run) == "PR #31"


def test_plan_review_gate_hashes_the_plan_as_it_is_on_the_branch(tmp_path):
    """What is published is what must have been reviewed: the BRANCH copy is hashed, the main
    checkout's copy only stands in when the branch carries none."""
    d, cfg, goal, run = _review_pr(tmp_path / "i", record="SOUND")
    _plan_bytes(d, PLAN_A + b"2. edited on the branch\n", main=False, branch=True)
    out = work.pr(d, cfg, goal, run=run)
    assert "changed after its review" in out and out.endswith(_tail(d, _wt_plan(d).as_posix())), out
    assert not _pushed(run)
    d, cfg, goal, run = _review_pr(tmp_path / "ii", record="SOUND")
    _plan_bytes(d, PLAN_A + b"2. edited in the main checkout only\n", main=True, branch=False)
    assert work.pr(d, cfg, goal, run=run) == "PR #31"


def test_plan_review_gate_refuses_a_plan_only_on_the_branch(tmp_path):
    """B1: the plan is only on the branch (#2575's shape) -- still hashed, still gated."""
    d, cfg, goal, run = _review_pr(tmp_path / "i", main=False, branch=True)
    out = work.pr(d, cfg, goal, run=run)
    assert "has no recorded review" in out and out.endswith(_tail(d, _wt_plan(d).as_posix())), out
    assert not _pushed(run)
    d, cfg, goal, run = _review_pr(tmp_path / "ii", main=False, branch=True, record="SOUND")
    assert work.pr(d, cfg, goal, run=run) == "PR #31"
    d, cfg, goal, run = _review_pr(tmp_path / "iii", main=False, branch=True, record="SOUND")
    _plan_bytes(d, PLAN_A + b"2. edited on the branch\n", main=False, branch=True)
    assert "changed after its review" in work.pr(d, cfg, goal, run=run)
    assert not _pushed(run)


def test_plan_review_gate_hashes_the_main_checkout_when_the_repo_ignores_plans(tmp_path):
    d, cfg, goal, run = _review_pr(tmp_path, branch=False, record="SOUND")
    assert work.pr(d, cfg, goal, run=run) == "PR #31"
    _plan_bytes(d, PLAN_A + b"2. edited after the review\n")
    run = _runner([("rev-list", "1"), (LS_FILES, ""), ("check-ignore", ""), ("pulls?head", "31")])
    out = work.pr(d, cfg, goal, run=run)
    assert "changed after its review" in out and out.endswith(_tail(d, _main_plan(d).as_posix())), out
    assert not _pushed(run)


@pytest.mark.parametrize("case", ["no copy anywhere", "untracked worktree copy"])
def test_plan_review_gate_is_silent_when_no_plan_resolves(tmp_path, case):
    """The design-PR / docs-only case: no plan and no record, so this gate says nothing
    (`gates.hard_plan_gate` owns "no plan"). Passes before #258; its control is C12(i)."""
    if case == "no copy anywhere":
        d, cfg, goal, run = _review_pr(tmp_path, main=False, branch=False)
    else:
        d, cfg, goal, run = _review_pr(tmp_path, main=False, branch=True, tracked="")
    assert work.pr(d, cfg, goal, run=run) == "PR #31"


def test_plan_review_gate_refuses_a_recorded_plan_that_no_longer_resolves(tmp_path):
    """R-d: a record proves this goal HAD a reviewed plan, so a plan deleted from both copies after
    its review is not a plan-less goal."""
    d, cfg, goal, run = _review_pr(tmp_path, branch=False, record="SOUND")
    _main_plan(d).unlink()
    out = work.pr(d, cfg, goal, run=run)
    assert "recorded plan review" in out and "no plan resolves" in out, out
    assert out.endswith(_tail(d, "none")), out
    assert not _pushed(run)


@pytest.mark.parametrize("content", ["{not json", "[]", '{"verdict":"pass"}',
                                     '{"plan_hash":"zz","verdict":"pass"}',
                                     json.dumps({"plan_hash": SHA_A, "verdict": "approve"}),
                                     json.dumps({"plan_hash": SHA_A, "verdict": ["pass"]}),
                                     json.dumps({"plan_hash": SHA_A, "verdict": {"v": "pass"}}),
                                     json.dumps({"plan_hash": [SHA_A], "verdict": "pass"})])
def test_plan_review_gate_refuses_a_malformed_record(tmp_path, content):
    """Fails CLOSED: the party best placed to make a record unreadable is the one the gate stops,
    and the remedy (re-record) costs one command."""
    d, cfg, goal, run = _review_pr(tmp_path)
    target = work.plan_review_record_path(d, goal)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    out = work.pr(d, cfg, goal, run=run)
    assert "unreadable or malformed" in out, out
    assert not _pushed(run)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads a mode-0 file")
def test_plan_review_gate_refuses_when_the_plan_on_the_branch_cannot_be_read(tmp_path):
    d, cfg, goal, run = _review_pr(tmp_path, record="SOUND")    # recorded BEFORE the chmod
    os.chmod(_wt_plan(d), 0)
    try:
        out = work.pr(d, cfg, goal, run=run)
    finally:
        os.chmod(_wt_plan(d), 0o644)
    assert "could not be read" in out, out
    assert not _pushed(run)


def test_finish_prunes_the_plan_review_record_and_only_its_own(tmp_path):
    """The record has the work record's lifetime: `finish` drops both, so the directory holds only
    in-flight goals (no table nothing prunes)."""
    d = _sdlc(tmp_path); goal = _started(d, pr=""); sha = _plan_bytes(d)
    work.record_plan_review(d, ON, goal, "SOUND", sha, run=_runner([]))
    other = pathlib.Path(d) / "state" / "gates" / "0002-y.json"
    other.write_text('{"verdict":"pass"}')
    work.finish(d, ON, goal, run=_runner([]))
    assert not _gate_record(d).exists()
    assert other.read_text() == '{"verdict":"pass"}'


# --- #312: the merge gate demands verify evidence only when verify is REQUIRED -------------------
#
# A github-mode user who declines the verify command gets `verify.enforce: false` and no
# `verify.command` from the scaffold. Before #312 every `work.py merge` then parked on "no fresh
# verify evidence for this run" -- evidence that `loop.py verify` cannot produce at all (it exits 3,
# NO-COMMAND) -- so the documented decline path could never land a PR. `state.verify_required` is
# the one truth both gates read: enforce on OR a command declared -> evidence required, exactly as
# before; neither -> nothing to prove, and the merge proceeds to its OTHER gates (review, CI, clean).

GITHUB_NO_COMMAND = {"work": {"enabled": True, "auto_merge": "always", "require_review": "changes"},
                     "discovery": {"source": "github"}, "verify": {"enforce": False, "command": ""}}


def test_312_github_no_command_enforce_off_merges_without_verify_evidence(tmp_path):
    """THE REPRO. Declined verify, github mode, no evidence file at all: the merge lands."""
    d = _sdlc(tmp_path, GITHUB_NO_COMMAND)
    goal = _started(d, goal="1642.md")
    run = _landed(extra=_review(decision="APPROVED"))
    out = work.merge(d, GITHUB_NO_COMMAND, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 merged"), out
    assert "no fresh verify evidence" not in out


def test_312_github_no_command_still_parks_on_the_review_gate(tmp_path):
    """Dropping the verify leg must not drop the review leg: a Request-changes still parks."""
    d = _sdlc(tmp_path, GITHUB_NO_COMMAND)
    goal = _started(d, goal="1642.md")
    run = _runner(_rights() + _review(decision="CHANGES_REQUESTED", changes_by=["bo"])
                  + [("pr view", _view())])
    out = work.merge(d, GITHUB_NO_COMMAND, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: changes requested by bo"), out
    assert not any("pr merge" in c for c in run.calls)


def test_312_enforce_on_without_evidence_still_parks_and_names_the_fix(tmp_path):
    """The safety property, half one: enforce ON with no command is still a demand -- and since
    verify cannot run, the park names the command-setting fix instead of 'run verify'."""
    cfg = {**GITHUB_NO_COMMAND, "verify": {"enforce": True, "command": ""}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d, goal="1642.md")
    run = _landed(extra=_review(decision="APPROVED"))
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: no fresh verify evidence for this run"), out
    assert "verify_detect.py" in out and "verify.enforce is on" in out
    assert not any("pr merge" in c for c in run.calls)


def test_312_command_configured_with_stale_evidence_still_parks(tmp_path):
    """The safety property, half two: a declared command is a demand even with enforce OFF -- the
    pre-#312 merge contract ('whether or not verify.enforce is set') holds whenever there is
    something to run. A green from before this run proves nothing."""
    cfg = {**GITHUB_NO_COMMAND, "verify": {"enforce": False, "command": "pytest -q"}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d, goal="1642.md")
    _evidence(d, goal, age=60)                                       # predates this run
    run = _landed(extra=_review(decision="APPROVED"))
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: no fresh verify evidence for this run"), out
    assert "predates this run" in out and "loop.py verify" in out
    assert not any("pr merge" in c for c in run.calls)


def test_312_command_configured_with_fresh_evidence_merges(tmp_path):
    cfg = {**GITHUB_NO_COMMAND, "verify": {"enforce": False, "command": "pytest -q"}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d, goal="1642.md")
    _evidence(d, goal)
    out = work.merge(d, cfg, goal, run=_landed(extra=_review(decision="APPROVED")), sleep=NOSLEEP)
    assert out.startswith("PR #7 merged"), out


@pytest.mark.parametrize("verify,goal_cmd,want", [
    ({}, None, None),
    ({"enforce": False, "command": ""}, None, None),
    ({"enforce": "off"}, None, None),
    ({"enforce": True}, None, "verify.enforce is on"),
    ({"enforce": "true"}, None, "verify.enforce is on"),          # F17/#342: generous read
    ({"enforce": 1}, None, "verify.enforce is on"),
    ({"command": "pytest"}, None, "a verify command is declared"),
    ({}, "make test", "a verify command is declared"),            # local goal frontmatter counts
])
def test_312_verify_required_is_the_one_shared_rule(tmp_path, verify, goal_cmd, want):
    goal = "0001-x"
    if goal_cmd:
        p = tmp_path / "0001-x.md"
        p.write_text(f"---\nverify_command: {goal_cmd}\n---\n# x\n")
        goal = str(p)
    got = state.verify_required({"verify": verify}, goal)
    assert (got is None) if want is None else (want in got), got

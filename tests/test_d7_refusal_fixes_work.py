"""D7 'every refusal prints its fix': work.py refusals that named the problem but not the resolving
command or setting (triage: docs/launch/evidence/d7-refusal-triage-complete-*.md).

Each test asserts that the message NAMES the command or setting that resolves it, and the ones on
loop.py's classifier needles also assert the verdict wording survives as a PREFIX (the fix is
appended after it, never in front: loop.py reads those prefixes)."""
import pytest

from test_work import (ALWAYS, PERMISSIONS_READ, HEAD_SHA, NOSLEEP, ON, _BAD_GATEWAY, _evidence, _gate_run, _perm,
                       _protected, _runner, _sdlc, _started, _view, prfake, work)


def test_pr_dirty_worktree_names_the_commit_command(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    out = work.pr(d, ON, goal, run=_runner([("status --porcelain", " M a.py")]))
    assert f"`work.py commit {d} {goal}`" in out and "nothing pushed" in out


def test_pr_empty_branch_names_the_commit_command(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    out = work.pr(d, ON, goal, run=_runner([("rev-list", "0")]))
    assert "no commits" in out and f"`work.py commit {d} {goal}`" in out


def test_rebase_before_start_names_the_start_command(tmp_path):
    d = _sdlc(tmp_path)
    out = work.rebase(d, ON, "0042-nothing.md", run=_runner([]))
    assert out.startswith("not started") and "`work.py start " in out


def test_auto_merge_off_names_the_setting_and_keeps_the_routing_ending(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    out = work.merge(d, ON, goal, run=_runner(_view() + _perm() + _protected()), sleep=NOSLEEP)
    assert "auto_merge is off" in out and ".sdlc/config.json" in out
    assert 'set work.auto_merge to "protected" or "always"' in out    # the key AND both real values
    assert out.endswith(f"leaving PR #7 for a human")        # SKILL.md / landing.md route on the ending


def test_unknown_merge_rights_names_gh_auth_status(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_view() + [(PERMISSIONS_READ, RuntimeError("gh: not authenticated"))] + _perm())
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 opened — could not determine merge rights (")
    assert "gh auth status" in out and f"`work.py merge {d} {goal}`" in out


def test_gate_conflicts_names_the_rebase_and_the_rerun(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner(_view("CONFLICTING", "DIRTY")), sleep=NOSLEEP)
    assert not ok and verdict.startswith("conflicts with the base branch — a human has to resolve them")
    assert "git rebase" in verdict and "--force-with-lease" in verdict
    assert f"re-run `work.py merge {d} {goal}`" in verdict


def test_gate_blocked_names_the_rerun(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner(_view(status="BLOCKED", checks=())), sleep=NOSLEEP)
    assert not ok and verdict.startswith("not safe to merge (mergeStateStatus=BLOCKED)")
    assert "required review" in verdict and f"`work.py merge {d} {goal}`" in verdict


def test_gate_unknown_mergeability_says_to_wait_and_rerun(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner(_view(mergeable="UNKNOWN")), sleep=NOSLEEP)
    assert not ok and verdict.startswith(work._PARK_MERGEABILITY_UNKNOWN)
    assert f"then re-run `work.py merge {d} {goal}`" in verdict


def test_gate_unreadable_pr_names_gh_auth_status(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _gate_run(prfake.rest_pull(), runs=RuntimeError(_BAD_GATEWAY), extra=[])
    ok, verdict, data = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok and verdict.startswith(work._PARK_PR_STATE_UNREADABLE) and data == {}
    assert "gh auth status" in verdict and f"`work.py merge {d} {goal}`" in verdict


def test_ci_cycles_exhausted_names_the_lever(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    rec = work._record(d, goal)
    rec.update({"pr": 7, "ci_fix_cycles": 1})
    work._save(d, goal, rec)
    data = {"headRefOid": "a" * 40, "statusCheckRollup": [{"name": "lint", "conclusion": "FAILURE"}]}
    out = work.ci_repair(d, {"work": {"enabled": True, "max_review_cycles": 1}}, goal, data, run=_runner([]))
    assert out.startswith("PARK: CI fix cycles exhausted after 1 cycles — failing: lint")
    assert "work.max_review_cycles" in out and f"`work.py merge {d} {goal}`" in out


def test_ci_without_a_readable_log_tells_the_human_where_to_look(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    rec = work._record(d, goal)
    rec["pr"] = 7
    work._save(d, goal, rec)
    data = {"headRefOid": HEAD_SHA, "statusCheckRollup": [{"name": "lint", "conclusion": "FAILURE"}]}
    out = work.ci_repair(d, ON, goal, data, run=_runner([]))
    assert out.startswith("PARK: failing required check lint has no readable Actions log")
    assert "details link" in out and f"`work.py merge {d} {goal}`" in out


def test_finish_keeping_a_dirty_worktree_names_the_force_lever(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("worktree remove", RuntimeError("contains modified files"))])
    out = work.finish(d, ON, goal, run=run)
    assert out.startswith("kept ") and f"`work.py finish {d} {goal} --force`" in out


def test_start_fetch_failure_that_is_not_a_missing_ref_names_the_access_check(tmp_path):
    d = _sdlc(tmp_path)
    run = _runner([("fetch", RuntimeError("fatal: Authentication failed for 'origin'"))])
    with pytest.raises(RuntimeError) as exc:
        work.start(d, ON, "0001-x.md", run=run)
    msg = str(exc.value)
    assert "Authentication failed" in msg and "git ls-remote" in msg and "work.py start" in msg


# --- a hand-edited config.json that no longer parses ------------------------------------------------

def _bad_config(tmp_path):
    d = tmp_path / ".sdlc"
    d.mkdir()
    (d / "config.json").write_text('{"work": {"enabled": true,}}')      # the trailing comma
    return str(d)


def test_load_config_on_invalid_json_names_the_file_the_line_and_the_fix(tmp_path):
    import json
    d = _bad_config(tmp_path)
    with pytest.raises(json.JSONDecodeError) as exc:                  # tests/test_state.py:220 contract
        work.state.load_config(d)
    text = str(exc.value)
    assert "config.json is not valid JSON" in text and "line 1" in text
    assert "json.tool" in text and "/sigma-init" in text


def test_work_cli_on_invalid_json_prints_the_fix_not_a_traceback(tmp_path, capsys):
    d = _bad_config(tmp_path)
    rc = work.main(["work.py", "start", d, "1"])
    err = capsys.readouterr().err
    assert rc == 1 and "Traceback" not in err
    assert "config.json is not valid JSON" in err and "json.tool" in err


def test_work_cli_on_a_missing_config_prints_the_init_fix_not_a_traceback(tmp_path, capsys):
    rc = work.main(["work.py", "pr", str(tmp_path / ".sdlc"), "1"])
    err = capsys.readouterr().err
    assert rc == 1 and "Traceback" not in err and "/sigma-init" in err


def test_loop_cli_on_invalid_json_exits_2_with_the_fix(tmp_path, capsys):
    loop = work._load("loop")
    d = _bad_config(tmp_path)
    rc = loop.main(["loop.py", "next", d])
    err = capsys.readouterr().err
    assert rc == 2 and "config.json is not valid JSON" in err and "json.tool" in err


def test_rebase_deferred_keeps_its_prefix_and_names_the_manual_rebase(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_view(status="BEHIND") + _perm() + [("rebase --autostash", RuntimeError("CONFLICT in a.py"))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: rebase deferred: ")          # loop.py's merge_conflict needle reads this
    assert "git rebase origin/main" in out and f"`work.py merge {d} {goal}`" in out


# --- opt-in auto_merge parks and the plan-sync refusal --------------------------------------------

from test_work import _landing_cfg, _landing_pr, _merge_run     # noqa: E402


def test_a_refused_direct_merge_says_what_to_do_next(tmp_path):
    d = _sdlc(tmp_path, _landing_cfg())
    goal = _started(d, goal="1642.md")
    _evidence(d, goal)
    run = _merge_run(RuntimeError("HTTP 409: Head branch was modified. Review and try the merge again."),
                     landing=_landing_pr("MERGED"))
    out = work.merge(d, _landing_cfg(), goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: direct merge of PR #7 was refused (")
    assert "fix the cause on the PR" in out and f"`work.py merge {d} {goal}`" in out


def test_a_pr_closed_without_merging_says_to_reopen_or_open_a_new_one(tmp_path):
    d = _sdlc(tmp_path, _landing_cfg())
    goal = _started(d, goal="1642.md")
    _evidence(d, goal)
    run = _merge_run(RuntimeError("HTTP 502: Server Error"), landing=_landing_pr("CLOSED"))
    out = work.merge(d, _landing_cfg(), goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: PR #7 was closed without merging")
    assert "reopen it" in out and f"`work.py pr {d} {goal}`" in out


def test_differing_plans_name_both_copies_and_the_rerun(tmp_path):
    sdlc = tmp_path / ".sdlc"
    wt = tmp_path / "wt"
    for base, text in ((sdlc, "plan A"), (wt / ".sdlc", "plan B")):
        (base / "plans").mkdir(parents=True)
        (base / "plans" / "0001-x.md").write_text(text)
    with pytest.raises(ValueError) as exc:
        work.verification_plan(str(sdlc), "0001-x.md", str(wt))
    msg = str(exc.value)
    assert "main and worktree plans differ" in msg
    assert str(sdlc / "plans" / "0001-x.md") in msg and str(wt / ".sdlc" / "plans" / "0001-x.md") in msg
    assert "copy the one you want over the other" in msg and "re-run `loop.py red` or `loop.py verify`" in msg


def test_red_unsafe_goal_is_stopped_by_its_own_clause_not_by_a_missing_plan(tmp_path, monkeypatch, capsys):
    """#1673 control: every later refusal in `red_goal` is satisfied (tree, shell consent, a plan that
    EXISTS for the traversed id), and `red_green.observe` is a tripwire, so only the
    `state.unsafe_goal_reason` clause can yield exit 2 here. (tests/test_test_first_gate.py's
    unsafe-goal cases also exit 2 for 'no plan', so they stay green without that clause.)"""
    loop = work._load("loop")
    plan = tmp_path / "evil.md"
    plan.write_text("## Tests\n- tests/test_x.py::test_a\n")
    ran = []

    class _Tripwire:
        PROVENANCE = "x"

        def observe(self, *a, **k):
            ran.append(a)
            return {"error": "observed"}

    monkeypatch.setattr(loop, "_verified_tree", lambda *a, **k: (str(tmp_path), "h", False))
    monkeypatch.setattr(loop.shell_policy, "repository_shell_commands_allowed", lambda root: True)
    monkeypatch.setattr(loop.work, "verification_plan", lambda *a, **k: plan)
    monkeypatch.setattr(loop, "_load", lambda name: _Tripwire() if name == "red_green" else work._load(name))
    assert loop.red_goal(str(tmp_path / ".sdlc"), "../evil") == 2
    assert not ran and "unsafe goal" in capsys.readouterr().err

"""D2 round-3 F07: an ambiguous review post PARKS the goal, it never re-posts under a new generation.

The hazard: `reconcile` returns "ambiguous" on ANY lookup miss, which is not proof the comment never
reached GitHub.  The merge gate takes the LATEST `sigma:` comment chronologically, so a comment from
an abandoned generation that lands late (an old approve) outranks a newer block.  The fix refuses a
new PR-review generation while an earlier post's outcome is unresolved, and gives one polite lever.
"""
import json
import os
import pathlib
import stat
import subprocess
import sys

import pytest

from test_work import (ON, PR_GET, POST_COMMENT, _comments, _posted, _review_chain,
                       _runner, _sdlc, _started, prfake, work, _load)

rc = _load("review_context")
SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _publish(d, goal="0001-x.md", artifact="PR#7", n=0):
    out = pathlib.Path(d) / "state" / "review-manifests" / ("m%d.json" % n)
    return rc.publish_generation(d, goal, "brief %d\n" % n, artifact, str(out), "pr-review")


def _ambiguous_first_post(d, goal):
    """Generation 1's post times out: the request receipt exists, state `prepared`, outcome unknown."""
    evidence, head = _review_chain(d, goal, "approve")
    run = _runner([(PR_GET, prfake.rest_pull(headRefOid=head)), (POST_COMMENT, RuntimeError("timed out"))])
    out = work.post_review(d, ON, goal, run=run, verdict="approve", evidence=str(evidence))
    assert out.startswith("PARK: remote comment outcome ambiguous"), out
    return evidence, head


def _only_request(d):
    f = next((pathlib.Path(d) / "state" / "review-posts").glob("*.json"))
    return f.stem, f


def test_the_hazard_the_gate_cannot_see():
    """Control (hazard demonstration): the merge gate's read picks the late stale approve over the
    newer block -- chronological latest wins.  This is the behaviour the fix keeps out of reach."""
    rows = [{"body": "sigma:block\n\nfix it", "author": {"login": "me"}, "authorAssociation": "OWNER"},
            {"body": "sigma:approve", "author": {"login": "me"}, "authorAssociation": "OWNER"}]
    rec = {"worktree": ".", "pr": 7}
    assert work._comment_directive(rec, _runner(_comments(rows, pr_author="me")))[0] == "approve"


def test_a_second_generation_is_refused_while_the_first_post_is_ambiguous(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    with pytest.raises(ValueError) as e:
        _publish(d, goal, n=9)
    msg = str(e.value)
    assert "abandon-review-post" in msg and "review_post_ambiguous" in msg and "/sigma-unpark" in msg


def test_the_documented_gesture_refuses_end_to_end(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    out = pathlib.Path(d) / "state" / "review-manifests" / "cli.json"
    p = subprocess.run([sys.executable, str(SCRIPTS / "review_context.py"), "brief", d, goal, "--for", "pr-review",
                        "--artifact", "PR#7", "--output", str(out)], capture_output=True, text=True)
    assert p.returncode == 2 and "abandon-review-post" in p.stderr and not out.exists()


def test_a_different_goal_does_not_block(tmp_path):                      # clause: goal match
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    assert rc.unresolved_review_posts(d, "0002-other.md") == []


def test_a_different_pr_does_not_block(tmp_path):                        # clause: pr match
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    assert rc.unresolved_review_posts(d, goal, pr=8) == [] and len(rc.unresolved_review_posts(d, goal, pr=7)) == 1


def test_a_confirmed_post_does_not_block(tmp_path):                      # clause: state == prepared
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    for f in (pathlib.Path(d) / "state" / "review-posts").glob("*.json"):
        r = json.loads(f.read_text()); r["state"] = "remote-confirmed"; f.write_text(json.dumps(r))
    assert rc.unresolved_review_posts(d, goal) == []
    assert _publish(d, goal, n=3)["goal"] == goal


def test_a_non_pr_brief_is_not_blocked(tmp_path):                        # clause: only PR-review generations
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    out = pathlib.Path(d) / "state" / "review-manifests" / "plan.json"
    assert rc.publish_generation(d, goal, "plan brief\n", "plan.md", str(out), "plan-review")["artifact"] == "plan.md"


def test_abandon_after_a_human_check_unblocks_a_fresh_generation(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    eid, f = _only_request(d)
    run = _runner([("issues/7/comments", "[]")])
    assert "abandoned" in work.abandon_review_post(d, goal, eid, "checked the PR by hand: none", run=run)
    assert json.loads(f.read_text())["state"] == "abandoned"
    assert _publish(d, goal, n=4)["goal"] == goal


def test_abandon_refuses_when_the_comment_is_on_the_pr(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    eid, f = _only_request(d)
    row = json.dumps([{"id": 5, "body": "x <!-- sigma-review-evidence:%s -->" % eid}])
    with pytest.raises(ValueError, match="reconcile-review-post"):
        work.abandon_review_post(d, goal, eid, "looked", run=_runner([("issues/7/comments", row)]))
    assert json.loads(f.read_text())["state"] == "prepared"


def test_abandon_refuses_when_the_lookup_cannot_read(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    eid, f = _only_request(d)
    with pytest.raises(ValueError, match="retry"):
        work.abandon_review_post(d, goal, eid, "looked", run=_runner([("issues/7/comments", RuntimeError("down"))]))
    assert json.loads(f.read_text())["state"] == "prepared"


def test_abandon_requires_a_decision_and_a_prepared_request(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    eid, f = _only_request(d)
    run = _runner([("issues/7/comments", "[]")])
    with pytest.raises(ValueError, match="decision"):
        work.abandon_review_post(d, goal, eid, "  ", run=run)
    r = json.loads(f.read_text()); r["state"] = "remote-confirmed"; f.write_text(json.dumps(r))
    with pytest.raises(ValueError, match="not ambiguous"):
        work.abandon_review_post(d, goal, eid, "looked", run=run)


def test_post_review_refuses_a_new_request_while_another_is_unresolved(tmp_path):
    """Belt: a generation published past an open request (older code, by hand) still cannot post."""
    d = _sdlc(tmp_path); goal = _started(d)
    first, _ = _review_chain(d, goal, "approve")                 # generation 1, published while nothing was open
    evidence, head = _review_chain(d, goal, "block", "issues")   # generation 2 exists (pre-fix publisher)
    work._review_post_request(d, goal, 7, "approve", "sigma:approve", first)   # gen 1's post went out; outcome unknown
    run = _runner([(PR_GET, prfake.rest_pull(headRefOid=head)), (POST_COMMENT, "{}")])
    out = work.post_review(d, ON, goal, run=run, verdict="block", reason="issues", evidence=str(evidence))
    assert out.startswith("PARK:") and "review_post_ambiguous" in out and not any(_posted(c) for c in run.calls)


def test_an_abandoned_evidence_never_posts(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    evidence, head = _ambiguous_first_post(d, goal)
    eid, f = _only_request(d)
    work.abandon_review_post(d, goal, eid, "none there", run=_runner([("issues/7/comments", "[]")]))
    run = _runner([(PR_GET, prfake.rest_pull(headRefOid=head)), ("issues/7/comments", "[]")])
    out = work.post_review(d, ON, goal, run=run, verdict="approve", evidence=str(evidence))
    assert out.startswith("PARK:") and "abandoned by a human check" in out and not any(_posted(c) for c in run.calls)


def test_the_abandon_cli_end_to_end_with_a_gh_shim(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    _ambiguous_first_post(d, goal)
    eid, f = _only_request(d)
    shim = tmp_path / "bin"; shim.mkdir()
    gh = shim / "gh"; gh.write_text("#!/bin/sh\necho '[]'\n"); gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": str(shim) + os.pathsep + os.environ["PATH"]}
    cmd = [sys.executable, str(SCRIPTS / "work.py"), "abandon-review-post", d, goal, "--evidence-id", eid]
    bad = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert bad.returncode == 2 and "decision" in bad.stderr
    ok = subprocess.run(cmd + ["--decision", "checked by hand, nothing there"], capture_output=True, text=True, env=env)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(f.read_text())["state"] == "abandoned"

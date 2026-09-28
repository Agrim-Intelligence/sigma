"""Releasing a completed goal's checkout is CODE, not a sentence in a prompt.

`work.finish()` has always done the right thing; nothing called it. Its only trigger was one line
of prose in `skills/agrim-loop/SKILL.md` ("After a `done`, release the checkout"), mid-paragraph in
a block otherwise about `auto_merge` modes. So the cleanup ran only when the agent read that line,
was still alive after `record done`, and chose to act -- and every turn that ended, crashed,
compacted or was interrupted in between leaked a checkout permanently, because nothing ever looked
again.

Measured on this repo 2026-09-01: 33 live `state/work/<goal>.json` records, and of the goals behind
them 8 of 9 sampled were CLOSED -- completed, merged, issue shut, checkout and record still on
disk. `finish()` unlinks the record on every successful path, so it had never run for any of them.
50 worktrees had accumulated.

THE REFUSALS ARE THE POINT, NOT AN OBSTACLE. `finish` is safe to call unconditionally because it
already declines exactly when it must: a tree that still holds uncommitted work is KEPT (a parked
goal stays intact for whoever picks it up), and so is one whose PR is positively confirmed still
OPEN and unarmed -- `state/work/<goal>.json` is the only place that PR number lives (#1202). Both
refusals are fail-open: no `gh`, no network, an unreadable reply, a MERGED or CLOSED PR all
proceed. `work.finish`'s own docstring records that SKILL.md routes a done "unconditionally to
`finish`", so calling it from the code path is the design intent, not a new policy.

WHAT THE TESTS ASSERT ON. The checkout directory and the record file, on disk -- never `finish`'s
returned string. The string is the thing-under-test's own account of itself and would pass just as
happily for a function that reported a removal it never performed. `test_a_kept_checkout...` is the
negative control that stops the whole file degenerating into "assert it always deletes".
"""
import importlib.util
import json
import pathlib
import subprocess

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


loop = _load("loop")
work = _load("work")
state = _load("state")

GOAL = "0001-x.md"
BRANCH = "sdlc/0001-x"


class _Source:
    """Minimal backlog source. Same shape as test_unit_completion.py's own double."""

    def __init__(self):
        self.parked, self.completed = [], []

    def complete(self, goal):
        self.completed.append(goal)

    def park(self, goal, reason, **kw):
        self.parked.append((goal, reason))


def _git(cwd, *argv):
    return subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True, check=True)


def _repo(tmp_path, pr=""):
    """A REAL git repo with a REAL goal worktree. Not a fake: the thing under test is whether a
    checkout leaves the disk, and a stubbed `run` would assert that git was ASKED, which is the
    same mistake as trusting `finish`'s return string. Returns (sdlc_dir, worktree_path)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("x")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")

    d = root / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(
        {"work": {"enabled": True, "base": "main", "worktree_dir": ".sdlc/work"}}))
    state.start_run(str(d))

    wt = d / "work" / "0001-x"
    _git(root, "worktree", "add", "-q", "-b", BRANCH, str(wt))
    work._save(str(d), GOAL, {"worktree": str(wt), "branch": BRANCH, "base": "main",
                              "base_resolved": True, "remote": "origin", "pr": pr})
    return str(d), wt


def test_recording_a_goal_done_releases_its_checkout(tmp_path):
    """THE REGRESSION. Before this, `_record` completed the goal and returned, leaving the checkout
    and its record on disk forever -- the exact state 33 of this repo's own closed goals were found
    in. No PR on the record, so `_open_pr_refusal` is never consulted: this pins the cleanup itself,
    not the PR carve-out that `test_a_checkout_whose_PR_is_still_open_is_kept` covers separately."""
    d, wt = _repo(tmp_path)
    assert wt.is_dir(), "fixture did not create the worktree"

    outcome = loop._record(d, _Source(), GOAL, "done")

    assert outcome == "done"
    assert not wt.exists(), "the completed goal's checkout was left on disk"
    assert not work.record_path(d, GOAL).exists(), "the work record outlived the checkout"


def test_a_checkout_that_still_holds_uncommitted_work_is_KEPT(tmp_path):
    """NEGATIVE CONTROL, and the reason calling `finish` unconditionally is safe at all. A file
    nothing has committed is the one thing a checkout can hold that nothing else records -- git
    itself refuses ("contains modified or untracked files"), `finish` reports `kept`, and the goal
    is still recorded `done`. Without this, every other test here would pass for a change that
    simply deleted the directory."""
    d, wt = _repo(tmp_path)
    (wt / "unsaved.txt").write_text("work nobody committed")

    outcome = loop._record(d, _Source(), GOAL, "done")

    assert outcome == "done", "a kept checkout must never cost the goal its terminal outcome"
    assert wt.is_dir(), "uncommitted work was destroyed"
    assert (wt / "unsaved.txt").read_text() == "work nobody committed"
    assert work.record_path(d, GOAL).exists(), "the record must survive alongside the checkout"


def test_a_parked_goal_keeps_everything(tmp_path):
    """`outcome == "done"`, not `result` -- a park is exactly the state a human is expected to pick
    up by hand, so its checkout and record both stay. This also pins the guard against the sibling
    mistake of hanging the release off `_record` unconditionally."""
    d, wt = _repo(tmp_path)

    outcome = loop._record(d, _Source(), GOAL, "parked", "needs a decision")

    assert outcome == "parked"
    assert wt.is_dir(), "a parked goal's checkout was released"
    assert work.record_path(d, GOAL).exists()


def test_a_done_downgraded_to_parked_keeps_its_checkout(tmp_path):
    """The #1201 downgrade path: `source.complete()` raising turns a `done` into a `parked`, and the
    issue was never closed. Releasing the checkout there would strand a goal that still needs one --
    the guard must read the ACTUAL outcome, not the requested `result`."""
    d, wt = _repo(tmp_path)

    class _Raises(_Source):
        def complete(self, goal):
            raise RuntimeError("gh issue close: GraphQL quota exhausted")

    outcome = loop._record(d, _Raises(), GOAL, "done")

    assert outcome == "parked"
    assert wt.is_dir(), "a goal whose completion failed had its checkout released anyway"
    assert work.record_path(d, GOAL).exists()


def test_a_release_that_blows_up_never_costs_the_goal_its_done(tmp_path):
    """Fail-open, the same posture `_record`'s own `source.complete()` handler takes one screen up.
    Cleanup is housekeeping layered on a terminal outcome that has ALREADY been written to the
    ledger, the action log and the cursor; an exception here must never propagate out of `_record`
    and turn a shipped goal into an uncaught traceback."""
    d, wt = _repo(tmp_path)
    # `loop.work`, NOT this file's own `work`: each `_load` builds a SEPARATE module object, so
    # patching the local one leaves `_record` calling the real `finish` and the test passes for the
    # wrong reason (observed: it removed the checkout and reported "removed ...").
    original = loop.work.finish

    def _boom(*a, **kw):
        raise OSError("disk went away mid-remove")

    loop.work.finish = _boom
    try:
        outcome = loop._record(d, _Source(), GOAL, "done")
    finally:
        loop.work.finish = original

    assert outcome == "done"
    assert work.record_path(d, GOAL).exists(), "a failed release must leave the record recoverable"


def test_a_repo_with_no_work_record_records_done_normally(tmp_path):
    """`work.enabled: false`, or any goal that never cut a checkout. `finish` returns "nothing to
    finish" without touching git, so the release is naturally inert -- no extra `work.enabled` gate
    is needed here, and none is added."""
    d, wt = _repo(tmp_path)
    work.record_path(d, GOAL).unlink()

    outcome = loop._record(d, _Source(), GOAL, "done")

    assert outcome == "done"
    assert wt.is_dir(), "a goal with no record must not have someone else's checkout removed"


def test_the_release_is_reported_not_silent(tmp_path, capsys):
    """LIVENESS: a checkout that was kept must be distinguishable from one that was never there.
    The `kept ...` line names the path and the reason on stderr, so an operator sees the one case
    this change deliberately does NOT clean up instead of discovering it in `git worktree list`
    months later."""
    d, wt = _repo(tmp_path)
    (wt / "unsaved.txt").write_text("x")

    loop._record(d, _Source(), GOAL, "done")

    err = capsys.readouterr().err
    assert str(wt) in err, f"the kept checkout was not named on stderr: {err!r}"

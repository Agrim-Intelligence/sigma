"""A file-backed fake GitHub behind a real `gh` executable on PATH (#2086). A shim, not a test file (mirror of
`prfake.py` / `gqlfake.py`); imported as a plain sibling module.

`tests/test_merge_runner_killed.py` has to kill a REAL `work.py merge` process while it polls a pending required
check, then run the documented recovery gestures as real processes. Nothing in-process can stand in for that: the
state the second run reads must be what the first, killed, run left, so it lives in files under one directory.
`install(world_dir, bin_dir)` writes an executable `gh` into `bin_dir`; put that directory first on PATH and every
`gh api ...` call the scripts make is answered here, never by the network. Each call is appended to
`<world_dir>/calls.log` (the joined argv, so the test can count a read or a merge PUT), and the answers follow two
flag files: `green` (the pending required check has finished) and `merged` (the merge PUT landed).

Every answer reuses the handlers `tests/test_work.py` and `tests/prfake.py` already pin, so the fake cannot drift
from the shapes the rest of the suite proves.
"""
import json
import os
import pathlib
import stat
import sys

HERE = pathlib.Path(__file__).resolve().parent
GOAL = "2086"
PR = "7"
SHIM = """#!{python}
import sys
sys.path.insert(0, {tests!r})
import merge_world
sys.exit(merge_world.shim_main(sys.argv[1:], {world!r}))
"""


def install(world_dir, bin_dir, head):
    """Write the `gh` shim into `bin_dir` and the world's state directory; `head` is the worktree's real HEAD."""
    world, bindir = pathlib.Path(world_dir), pathlib.Path(bin_dir)
    world.mkdir(parents=True, exist_ok=True)
    bindir.mkdir(parents=True, exist_ok=True)
    (world / "head").write_text(head)
    (world / "calls.log").touch()
    exe = bindir / "gh"
    exe.write_text(SHIM.format(python=sys.executable, tests=str(HERE), world=str(world)))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def calls(world_dir):
    return [c for c in (pathlib.Path(world_dir) / "calls.log").read_text().splitlines() if c]


def flag(world_dir, name, on=True):
    path = pathlib.Path(world_dir) / name
    if on:
        path.touch()
    else:
        path.unlink(missing_ok=True)


def shim_main(args, world_dir):
    world = pathlib.Path(world_dir)
    line = " ".join(["gh"] + [str(a) for a in args])
    with open(world / "calls.log", "a", encoding="utf-8") as log:
        log.write(line + "\n")
        log.flush()
        os.fsync(log.fileno())
    sys.stdout.write(_answer(world, line, list(args)))
    return 0


def _answer(world, line, args):
    import gqlfake
    import prfake
    import test_work as tw

    head = (world / "head").read_text().strip()
    merged = (world / "merged").exists()
    green = (world / "green").exists()
    if "graphql" in line or line.startswith(("gh pr ", "gh issue ", "gh repo view", "gh project ")):
        sys.stderr.write("HTTP 403: GitHub GraphQL is not available from Claude Code sessions\n")
        raise SystemExit(1)
    if prfake.merge_put(7) in line:
        (world / "merged").touch()
        return prfake.rest_merged()
    if merged and tw.PULLS_7 in line:
        return tw._rest_pr(merged=True, state="closed")
    if gqlfake.rest_issue_target(args) == (GOAL, False):
        state = "closed" if any("state=closed" in c for c in calls(world)) else "open"
        return json.dumps({"number": int(GOAL), "state": state, "body": "", "assignees": [],
                           "labels": [{"name": "sdlc:goal"}, {"name": "sdlc:in-progress"}]})
    checks = (("ci", "SUCCESS"), ("slow", "SUCCESS" if green else ""))
    handlers = (tw._view(status="CLEAN" if green else "BLOCKED", checks=checks, head=head) + tw._perm()
                + tw._protected(checks=("ci", "slow")) + tw._auto_merge_allowed(True)
                + [(prfake.reviews_get(7), "[]"), (tw.LINE_COMMENTS_GET, "[]"),
                   (prfake.comments_get(7), prfake.rest_comments([])),
                   (tw.PULLS_7, tw._rest_pr())])
    for token, resp in handlers:
        if token in line:
            if isinstance(resp, Exception):
                sys.stderr.write(str(resp) + "\n")
                raise SystemExit(1)
            return resp(line) if callable(resp) else resp
    return ""

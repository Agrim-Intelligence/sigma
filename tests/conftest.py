"""Suite-wide fixtures for `tests/` (the agrim-loop suite; a private package's own test lane, if the
checkout has one, has its own `conftest.py` and its own invocation -- this file never runs there).

`_no_live_gh` (issue #1495): a prior blast-radius review (#1487, on PR #1493) counted **88 live
`gh` invocations** made by this suite, **68** of them not `--repo`-scoped -- so they resolve
against whatever remote the checkout happens to point at. Every module in this codebase that
shells out to `gh` keeps its own thin `_run_gh`-shaped wrapper (`ledger.py`, `cross_repo.py`,
`feature_owner.py`, `sources.py`, ...), and none of them do anything more exotic than
`subprocess.run(["gh", *args], ...)` (verified: no `Popen`/`check_call`/`check_output`/`os.system`
anywhere in `skills/` or `hooks/` ever names `gh`) -- so `subprocess.run` is the one seam every
current AND future call site shares.

The trap this closes: nearly every one of those wrappers is reached through a function that takes
an injectable `run=None` parameter and falls back to the real wrapper when a test omits it
(`ledger.actor`'s `(run or _run_gh)(...)` is the shape; dozens of others repeat it). A test that
forgets `run=<fake>` doesn't fail loudly -- it silently reaches the real network, and its outcome
now depends on GitHub's live state, on rate limits, and on which of this machine's `gh auth`
accounts happens to be active that day. The same review found this is not hypothetical: a fixture
in `test_backlog_precheck.py` whose meta lacked a `schema` key fell through, invisibly, to 12 live
`gh issue list` calls fetching up to 400 real issues.

This autouse fixture makes that trap loud instead of silent: for every test, `subprocess.run` is
replaced with a guard that raises the moment anything tries to invoke the real `gh` binary,
**naming the exact test** so the call site is never a mystery. A test that genuinely needs the
network opts in with `@pytest.mark.live_gh` -- the ONLY escape hatch, proven by
`tests/test_live_gh_guard.py::test_a_marked_test_reaches_the_real_process_for_a_gh_shaped_argv0`,
which actually reaches a process named `gh` under the marker rather than merely asserting "no
exception".

Scope, deliberately: this guards `subprocess.run` only, because that is the ONE mechanism every
call site in this repo actually uses today (see the audit above) -- not `subprocess.Popen`
directly, which the codebase does use (`loop.py`, to spawn `watch_daemon.py`) but never for `gh`. A future
call site that shells out to `gh` any other way is outside what this guard can see; the "cannot
rot" property this buys is that no NEW call site through the shared, already-universal
`subprocess.run` seam can silently reach the network either, not that every conceivable process
API is covered.
"""
import subprocess

import pytest


def _first_token(cmd):
    """The thing that would actually get exec'd, from either calling convention `subprocess.run`
    accepts: a plain string (used with `shell=True`, e.g. `"gh pr list"`) or an argv sequence
    (`["gh", "pr", "list"]`, or `["/usr/bin/gh", ...]`, or a mix of `str`/`bytes`/`os.PathLike`)."""
    if cmd is None:
        return None
    if isinstance(cmd, (list, tuple)):
        if not cmd:
            return None
        token = cmd[0]
    else:
        token = cmd
    if isinstance(token, bytes):
        token = token.decode("utf-8", errors="replace")
    token = str(token)
    if isinstance(cmd, (str, bytes)):
        # a shell-string command: only its first whitespace-separated word names the program
        stripped = token.strip()
        if not stripped:
            return None
        token = stripped.split(None, 1)[0]
    return token


def _is_gh_argv(cmd):
    """True if this `subprocess.run` call would invoke the real `gh` CLI (by name, wherever on
    disk it resolves from — a bare `"gh"` found on `PATH`, or a full path ending in `/gh`)."""
    token = _first_token(cmd)
    if not token:
        return False
    name = token.replace("\\", "/").rsplit("/", 1)[-1]
    return name in ("gh", "gh.exe")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_gh: this test is allowed to invoke the real `gh` CLI (network, live GitHub "
        "state) -- use only when the test genuinely needs it; see tests/conftest.py.",
    )


@pytest.fixture(autouse=True)
def _no_live_gh(request, monkeypatch):
    """#1495: fail loud, naming the call site, on any `subprocess.run` that would invoke the real
    `gh` CLI -- unless this test is marked `@pytest.mark.live_gh`."""
    if request.node.get_closest_marker("live_gh") is not None:
        return  # opted in explicitly -- leave subprocess.run untouched for this test

    real_run = subprocess.run
    nodeid = request.node.nodeid

    def _guarded_run(*args, **kwargs):
        cmd = kwargs.get("args", args[0] if args else None)
        if _is_gh_argv(cmd):
            raise RuntimeError(
                "un-injected live `gh` call from %s: subprocess.run(%r, ...) would reach the "
                "real GitHub CLI (#1495). Inject this repo's `run=` convention (most functions "
                "under skills/agrim-loop/scripts/ take one) with a fake, or mark the test "
                "@pytest.mark.live_gh if it genuinely needs the network." % (nodeid, cmd)
            )
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guarded_run)

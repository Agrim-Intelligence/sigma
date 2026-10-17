"""Suite-wide fixtures for `tests/` (the sigma-loop suite; a private package's own test lane, if the
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
import os
import pathlib
import resource
import subprocess
import sys

import pytest

import _script_cache

_script_cache.install()   # at import, before any test module loads a script; see tests/test_script_cache.py


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


def _rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024 if sys.platform == "darwin" else 1024)


def pytest_collection_finish(session):
    _script_cache.fresh = True   # collection is over: from here every load is a fresh module
    peak, n = _rss_mb(), len(session.items)
    session.config._collect_rss_mb, session.config._collect_n = peak, n
    if n > 200:
        worst = _script_cache.worst_live_copies(pathlib.Path(__file__).resolve().parent.parent)
        if worst and worst[0][1] > _script_cache.MAX_COPIES:
            pytest.exit(f"{worst[0][1]} live copies of one script module after collection (max "
                        f"{_script_cache.MAX_COPIES}): {worst}; see tests/test_script_cache.py", returncode=3)
    if n > 200 and peak > _script_cache.rss_bound_mb(n):   # a partial run (-k, one file) is too small to judge
        pytest.exit(f"collection peaked at {peak:.0f} MB for {n} tests (bound "
                    f"{_script_cache.rss_bound_mb(n):.0f}): a script is being loaded uncached; "
                    "see tests/test_script_cache.py", returncode=3)


def _ram_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024)


def pytest_xdist_auto_num_workers(config):
    """`-n auto` = as many workers as fit in a quarter of this machine's RAM at SIGMA_TEST_WORKER_MB each
    (default 2000, the measured test-time peak of a worker before the script cache), capped by CPUs. A
    constant `-n 4` copied from a laptop OOM-killed a 24 GB Mac."""
    per = int(os.environ.get("SIGMA_TEST_WORKER_MB", "2000"))
    return max(1, min(os.cpu_count() or 1, _ram_mb() // 4 // per))


@pytest.fixture(autouse=True)
def _scripts_load_fresh_at_test_time(request):
    """The script cache serves COLLECTION only (module-level and import-time loads, which pytest keeps alive).
    Loads made while a test runs are fresh, as before: a first full run with the cache on for those too failed
    933 tests (modules cached while a test had pathlib/sys.modules stubbed, then reused by others)."""
    _script_cache.fresh = request.node.get_closest_marker("shared_scripts") is None
    yield
    _script_cache.fresh = True


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "shared_scripts: for tests/test_script_cache.py only -- turns the collection-time script cache "
        "on for this test so the cache itself can be tested.",
    )
    config.addinivalue_line(
        "markers",
        "live_gh: this test is allowed to invoke the real `gh` CLI (network, live GitHub "
        "state) -- use only when the test genuinely needs it; see tests/conftest.py.",
    )
    config.addinivalue_line(
        "markers",
        "slow: a label for a test whose cost grows with the repository (#596); nothing deselects it, "
        "CI runs it, and it carries a large hang guard instead of a short limit.",
    )


@pytest.fixture(autouse=True)
def _no_host_leak_patterns(monkeypatch):
    """#375: a developer shell that exports SIGMA_LEAK_PATTERNS must not change a test's result."""
    monkeypatch.delenv("SIGMA_LEAK_PATTERNS", raising=False)


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
                "under skills/sigma-loop/scripts/ take one) with a fake, or mark the test "
                "@pytest.mark.live_gh if it genuinely needs the network." % (nodeid, cmd)
            )
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guarded_run)

    # #229: preflight.py's runner uses `Popen` directly (it must kill a whole process group on a
    # timeout, which `subprocess.run` cannot), so the one-seam claim above no longer holds on its
    # own. Guard `Popen` for `gh` too -- a subclass, so `subprocess.run`'s own internal `Popen` and
    # every isinstance check keep working.
    real_popen = subprocess.Popen

    class _GuardedPopen(real_popen):
        def __init__(self, args, *a, **kw):
            if _is_gh_argv(args):
                raise RuntimeError(
                    "un-injected live `gh` call from %s: subprocess.Popen(%r, ...) would reach "
                    "the real GitHub CLI (#1495, #229). Inject a fake runner, or mark the test "
                    "@pytest.mark.live_gh." % (nodeid, args))
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)


@pytest.fixture(autouse=True)
def _offline_gh_for_child_processes(request, monkeypatch, tmp_path_factory):
    """#229: the guard above sees only THIS process. A test that runs a script as a child process
    (`/sigma-init`'s CLI now runs `gh auth status` in its preflight) would reach the developer's real,
    logged-in `gh` -- network, and an outcome that depends on whose machine it is. Every child
    therefore gets an empty gh config and no token: `gh auth status` answers "not logged in" locally
    (measured: 0.02s, no network), exactly like an unconfigured CI runner. `live_gh` tests opt out."""
    if request.node.get_closest_marker("live_gh") is not None:
        return
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path_factory.mktemp("gh-config")))
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.delenv(name, raising=False)


#: #239: the plugin's previous env prefix, from fragments (a guarded private name in this tree).
_RETIRED_ENV_PREFIX = ("LOOP" "SMITH") + "_"


@pytest.fixture(autouse=True)
def _no_host_legacy_env(monkeypatch):
    """#239: Sigma now READS operator env vars under the plugin's previous prefix when the SIGMA_
    name is unset. A developer machine still running the old plugin may export them, which would
    silently change what a test observes; every test starts without them and sets its own."""
    for name in [k for k in os.environ if k.startswith(_RETIRED_ENV_PREFIX)]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_host_plugin_inventory(monkeypatch, tmp_path_factory):
    """#240/#314: `coexist.py` prints a notice on init, loop start, claim/record and the watcher
    when the host has the plugin under the previous name ENABLED -- read from `$CLAUDE_CONFIG_DIR`
    (else `~/.claude`) and `$CODEX_HOME` (else `~/.codex`). Nothing refuses any more, but a
    developer machine that still has it enabled would put that line into every such test's
    output. Each test starts with both pointed at an empty directory (a test that needs an
    inventory builds its own); `SIGMA_ALLOW_COEXIST` is cleared too, so the tests prove the
    DEFAULT (no override needed) rather than the silenced notice."""
    empty = tmp_path_factory.mktemp("host-plugin-inventory")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(empty / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(empty / "codex"))
    monkeypatch.delenv("SIGMA_ALLOW_COEXIST", raising=False)


@pytest.fixture(autouse=True)
def _hermetic_graphql_capability_env(monkeypatch):
    """#801: a cloud/CI shell that exports CLAUDE_CODE_REMOTE or SIGMA_GH_GRAPHQL must not add a
    capability row to (or otherwise perturb) unrelated tests. Tests that want the signal set it."""
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.delenv("SIGMA_GH_GRAPHQL", raising=False)
    monkeypatch.delenv("SIGMA_GH_BREAKER_COOLDOWN", raising=False)   # #895: gh_api.read_issue's breaker


@pytest.fixture(autouse=True)
def _hermetic_upkeep_machine_env(monkeypatch):
    """#917: a developer shell that exports SIGMA_UPKEEP_JOB must not open the machine door of an upkeep gate under
    test. Tests that want the variable set it (or inject an `environ`)."""
    monkeypatch.delenv("SIGMA_UPKEEP_JOB", raising=False)

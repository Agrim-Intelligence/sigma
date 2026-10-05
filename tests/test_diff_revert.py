"""Tests for skills/sigma-loop/scripts/diff_revert.py (issue #2240).

Two families. The first is fake-runner unit tests (mirroring flake_check.py's/mutation.py's own
style) for every branch of `run()` -- absence, the scope bound, and the restore-on-entry contract.
The second is a REAL end-to-end control, driving actual `git` and `pytest`, that reproduces the
defect this issue names (a vacuous test earns no evidence at all today, because mutmut is absent
and wired into nothing) and shows a genuinely production-code-dependent test earning a real,
witness-recorded kill -- the "run the control, not just the check" standard AGENTS.md sets.
"""
import importlib.util
import os
import pathlib
import subprocess
import tempfile

S = pathlib.Path(__file__).parent.parent / "skills" / "sigma-loop" / "scripts"


def _dr():
    spec = importlib.util.spec_from_file_location("diff_revert", S / "diff_revert.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _git_ok(fork="deadbeef", worktree_ok=True):
    """A fake `git_cmd(root, argv, timeout=...)` answering merge-base/worktree add/remove."""
    def git(root, argv, timeout=None):
        if argv[0] == "merge-base":
            return 0, fork
        if argv[:2] == ["worktree", "add"]:
            return (0, "") if worktree_ok else (1, "fatal: could not create worktree")
        if argv[:2] == ["worktree", "remove"]:
            return 0, ""
        if argv == ["worktree", "prune"]:
            return 0, ""
        return 0, ""
    return git


def _pytest_result(code, out=""):
    return lambda cwd, argv, timeout: (code, out)


def _git_with_show(fork, contents, worktree_ok=True):
    """Like `_git_ok` but also answers `git show <fork>:<path>` from `contents`, a dict of
    relative path -> the source text that existed there AT THE FORK (a path absent from the dict
    means the file did not exist yet at the fork)."""
    def git(root, argv, timeout=None):
        if argv[0] == "merge-base":
            return 0, fork
        if argv[0] == "show" and len(argv) > 1 and argv[1].startswith(fork + ":"):
            rel = argv[1].split(":", 1)[1]
            src = contents.get(rel)
            return (0, src) if src is not None else (1, "fatal: path does not exist in " + fork)
        if argv[:2] == ["worktree", "add"]:
            return (0, "") if worktree_ok else (1, "fatal: could not create worktree")
        if argv[:2] == ["worktree", "remove"]:
            return 0, ""
        if argv == ["worktree", "prune"]:
            return 0, ""
        return 0, ""
    return git


# --- absence, never a silent pass ---------------------------------------------------------------

def test_no_changed_tests_is_absent_and_free():
    dr = _dr()
    r = dr.run("/sdlc", "42", "/root", "origin/main", [])
    assert r["verdict"] == dr.ABSENT
    assert r["ms"] >= 0 and r["kills"] == [] and r["survivors"] == []


def test_no_base_ref_is_absent():
    dr = _dr()
    r = dr.run("/sdlc", "42", "/root", None, ["tests/test_x.py::test_a"])
    assert r["verdict"] == dr.ABSENT
    assert "base ref" in r["reason"]


def test_the_scope_bound_declines_loudly_rather_than_measuring_a_subset():
    """Same shape and same reason as flake_check.MAX_SCOPED_FILES: a wrong base ref would balloon
    the goal's own diff into the whole repo's tests, and truncating silently would look complete."""
    dr = _dr()
    many = ["tests/test_x.py::test_%d" % i for i in range(dr.MAX_SCOPED_TESTS + 1)]
    r = dr.run("/sdlc", "42", "/root", "origin/main", many)
    assert r["verdict"] == dr.ABSENT
    assert "scope bound" in r["reason"]


def test_a_failing_merge_base_is_absent_not_a_crash():
    dr = _dr()
    with tempfile.TemporaryDirectory() as d:
        r = dr.run(d, "42", "/root", "origin/main", ["tests/test_x.py::test_a"],
                    git_cmd=lambda root, argv, timeout=None: (1, "fatal: not a valid object"))
        assert r["verdict"] == dr.ABSENT
        assert "merge-base" in r["reason"]


def test_a_failing_worktree_add_is_absent_not_a_crash():
    """The mirror of the merge-base failure above, one step later: `merge-base` resolves a real
    fork commit, but the scratch worktree itself cannot be created (disk full, a stale registration
    git refuses to reuse, a permissions problem). Must decline as ABSENT, never raise past `run`."""
    dr = _dr()
    with tempfile.TemporaryDirectory() as d:
        r = dr.run(d, "42", d, "origin/main", ["tests/test_x.py::test_a"],
                    git_cmd=_git_ok(worktree_ok=False))
        assert r["verdict"] == dr.ABSENT
        assert "scratch worktree" in r["reason"]


def test_a_pytest_timeout_is_absent_never_a_pass():
    dr = _dr()

    def slow(cwd, argv, timeout):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=timeout)

    with tempfile.TemporaryDirectory() as d:
        r = dr.run(d, "42", d, "origin/main", ["tests/test_x.py::test_a"],
                    git_cmd=_git_ok(), run_cmd=slow)
        assert r["verdict"] == dr.ABSENT
        assert "timed out" in r["reason"]


def test_a_reverted_tree_that_fails_to_collect_anything_is_absent():
    """pytest's own 'nothing was measured' exit codes (2/3/4/5, or a negative -- killed by a
    signal) must not be read as every test surviving; that would fabricate a `verified: []` verdict
    out of a harness failure, the exact ABSENT-vs-PASS confusion this vocabulary exists to name."""
    dr = _dr()
    with tempfile.TemporaryDirectory() as d:
        r = dr.run(d, "42", d, "origin/main", ["tests/test_x.py::test_a"],
                    git_cmd=_git_ok(), run_cmd=_pytest_result(4, "usage error"))
        assert r["verdict"] == dr.ABSENT
        assert "nothing measured" in r["reason"]


# --- the two real states -------------------------------------------------------------------------

def test_a_test_that_goes_red_against_the_reverted_tree_is_a_kill():
    dr = _dr()
    tests = ["tests/test_x.py::test_a", "tests/test_x.py::test_b"]
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / "tests").mkdir()
        (pathlib.Path(d) / "tests" / "test_x.py").write_text("def test_a(): pass\n")
        r = dr.run(d, "42", d, "origin/main", tests, git_cmd=_git_ok(),
                    run_cmd=_pytest_result(1, "FAILED tests/test_x.py::test_a\n"))
        assert r["verdict"] == dr.VERIFIED
        assert r["kills"] == ["tests/test_x.py::test_a"]
        assert r["survivors"] == ["tests/test_x.py::test_b"]
        assert "1 of 2" in r["reason"]


def test_a_bracketed_parametrised_failure_still_matches_its_bare_touched_id():
    dr = _dr()
    with tempfile.TemporaryDirectory() as d:
        r = dr.run(d, "42", d, "origin/main", ["tests/test_x.py::test_a"], git_cmd=_git_ok(),
                    run_cmd=_pytest_result(1, "FAILED tests/test_x.py::test_a[case0]\n"))
        assert r["verdict"] == dr.VERIFIED
        assert r["kills"] == ["tests/test_x.py::test_a"]


def test_every_touched_test_surviving_the_revert_is_unverified_not_a_pass():
    """THE VACUOUS-TEST CASE (#2240's own worked example): a test that stays green with the
    production code reverted has proven nothing about the new behaviour."""
    dr = _dr()
    with tempfile.TemporaryDirectory() as d:
        r = dr.run(d, "42", d, "origin/main", ["tests/test_x.py::test_a"],
                    git_cmd=_git_ok(), run_cmd=_pytest_result(0, ""))
        assert r["verdict"] == dr.UNVERIFIED
        assert r["kills"] == []
        assert "not proven" in r["reason"]


def test_the_verdict_vocabulary_is_the_shared_one():
    dr = _dr()
    assert (dr.VERIFIED, dr.UNVERIFIED, dr.ABSENT) == ("verified", "unverified", "absent")


# --- collection errors: the most common real kill shape, and the over-crediting trap in it ------

def test_a_collection_error_is_a_kill_not_nothing_measured():
    """pytest exits 4 -- the SAME code as a bare usage error -- when a requested node id's module
    fails to import against the reverted tree. Reading exit 4 as 'nothing measured' would discard
    the single most common real kill shape: a new test importing a name production code no longer
    has. `_new_test_ids` sees the file did not exist at the fork at all, so the whole thing is new
    and eligible without needing to inspect any function name."""
    dr = _dr()
    tests = ["tests/test_x.py::test_a"]
    out = "ERROR: found no collectors for /scratch/tests/test_x.py::test_a\n"
    with tempfile.TemporaryDirectory() as d:
        r = dr.run(d, "42", d, "origin/main", tests,
                   git_cmd=_git_with_show("deadbeef", {}), run_cmd=_pytest_result(4, out))
    assert r["verdict"] == dr.VERIFIED, r
    assert r["kills"] == ["tests/test_x.py::test_a"], r


def test_a_collection_error_credits_only_the_genuinely_new_sibling_as_a_kill():
    """THE OVER-CREDITING TRAP a whole-file collection error sets: pytest reports `found no
    collectors` for EVERY requested id in the broken file, including `test_a` here -- a
    byte-for-byte pre-existing test that never touches the reverted change and would otherwise earn
    a fabricated STRONG `mutation` witness for behaviour it does not exercise. Only `test_b`, which
    `_new_test_ids` confirms is absent from the file at the fork, may be credited."""
    dr = _dr()
    tests = ["tests/test_x.py::test_a", "tests/test_x.py::test_b"]
    fork = "deadbeef"
    fake_git = _git_with_show(fork, {"tests/test_x.py": "def test_a():\n    pass\n"})
    out = ("ERROR: found no collectors for /scratch/tests/test_x.py::test_a\n"
           "ERROR: found no collectors for /scratch/tests/test_x.py::test_b\n")
    with tempfile.TemporaryDirectory() as d:
        r = dr.run(d, "42", d, "origin/main", tests,
                   git_cmd=fake_git, run_cmd=_pytest_result(4, out))
    assert r["verdict"] == dr.VERIFIED, r
    assert r["kills"] == ["tests/test_x.py::test_b"], r
    assert r["survivors"] == ["tests/test_x.py::test_a"], r


def test_a_collection_error_with_no_genuinely_new_test_stays_unverified():
    """The mirror image: if EVERY touched test in the broken file already existed at the fork, none
    may be credited off the collection error alone -- the safe, under-crediting direction, not a
    fabricated pass."""
    dr = _dr()
    tests = ["tests/test_x.py::test_a", "tests/test_x.py::test_b"]
    fork = "deadbeef"
    fake_git = _git_with_show(fork, {"tests/test_x.py":
                                      "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n"})
    out = ("ERROR: found no collectors for /scratch/tests/test_x.py::test_a\n"
           "ERROR: found no collectors for /scratch/tests/test_x.py::test_b\n")
    with tempfile.TemporaryDirectory() as d:
        r = dr.run(d, "42", d, "origin/main", tests,
                   git_cmd=fake_git, run_cmd=_pytest_result(4, out))
    assert r["verdict"] == dr.UNVERIFIED, r
    assert r["kills"] == [], r
    assert "none is individually new" in r["reason"]


# --- restore-on-entry (#2240's own design constraint; AGENTS.md's #1685 lesson) ------------------

def test_a_stale_scratch_from_a_dead_pid_is_reclaimed_on_entry_not_left_as_a_false_conflict():
    """Simulates exactly the scenario the issue names: a prior run's cleanup never fired (killed by
    a wall-clock timeout, whose exit trap is best-effort). The scratch directory AND its owner
    marker are left behind under a pid that is no longer alive. The run that finds this must clean
    it up itself, up front, and still produce a correct result -- not treat the leftover as a live
    conflict and abstain."""
    dr = _dr()
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / "tests").mkdir()
        (pathlib.Path(d) / "tests" / "test_x.py").write_text("def test_a(): pass\n")

        scratch = dr.scratch_dir(d, "42")
        scratch.mkdir(parents=True)
        (scratch / "leftover-from-the-killed-run.txt").write_text("stale")
        dr._owner_path(scratch).write_text("999999999")     # a pid that cannot be alive

        removed = []

        def spy(root, argv, timeout=None):
            # `worktree remove` always declines -- these directories were never registered by a
            # real `git worktree add` in this fake, so `cleanup()` must fall back to a real
            # `rmtree`, exactly as it would for a genuinely half-created leftover.
            if argv[:2] == ["worktree", "remove"]:
                removed.append(argv)
                return 1, "fatal: not a working tree"
            if argv[0] == "merge-base":
                return 0, "deadbeef"
            return 0, ""

        r = dr.run(d, "42", d, "origin/main", ["tests/test_x.py::test_a"],
                    git_cmd=spy, run_cmd=_pytest_result(1, "FAILED tests/test_x.py::test_a\n"),
                    pid_alive=lambda pid: False)
        assert removed, "the stale scratch worktree was never torn down before reuse"
        assert r["verdict"] == dr.VERIFIED, "a stale leftover produced a wrong/absent result"
        assert "leftover-from-the-killed-run.txt" not in [p.name for p in
                                                            pathlib.Path(d).glob("**/leftover*")], \
            "the stale file survived the reclaim instead of being torn down"
        assert not scratch.exists(), "the scratch dir must not survive past its own cleanup"
        assert not dr._owner_path(scratch).exists()


def test_a_live_owners_scratch_is_never_touched_by_a_concurrent_run():
    """The mirror image of the control above: an owner pid that IS alive must make this run
    abstain rather than race the live one for the same worktree."""
    dr = _dr()
    with tempfile.TemporaryDirectory() as d:
        scratch = dr.scratch_dir(d, "42")
        scratch.mkdir(parents=True)
        (scratch / "live-run-in-progress.txt").write_text("do not touch")
        dr._owner_path(scratch).write_text(str(os.getpid()))

        touched = []

        def spy(root, argv, timeout=None):
            touched.append(argv)
            return 0, ""

        r = dr.run(d, "42", d, "origin/main", ["tests/test_x.py::test_a"],
                    git_cmd=spy, pid_alive=lambda pid: True)
        assert r["verdict"] == dr.ABSENT
        assert "live" in r["reason"]
        assert touched == [], "a live sibling's worktree was touched by a concurrent run"
        assert (scratch / "live-run-in-progress.txt").exists(), "the live run's files were deleted"


def test_cleanup_runs_even_when_the_pytest_call_itself_raises():
    """The reservation (owner marker) must not survive an unexpected crash inside the try block --
    otherwise a single raising run would permanently wedge every future run behind a fake conflict
    with a now-dead pid it never even gets a chance to reclaim, since _reclaim_stale runs BEFORE
    this path, not after."""
    dr = _dr()
    with tempfile.TemporaryDirectory() as d:
        scratch = dr.scratch_dir(d, "42")

        def boom(cwd, argv, timeout):
            raise RuntimeError("unexpected")

        r = dr.run(d, "42", d, "origin/main", ["tests/test_x.py::test_a"],
                    git_cmd=_git_ok(), run_cmd=boom)
        assert r["verdict"] == dr.ABSENT
        assert not scratch.exists()
        assert not dr._owner_path(scratch).exists()


# --- a REAL end-to-end kill, driving actual git + pytest -----------------------------------------

def _run(cwd, argv):
    p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _real_repo():
    """A tiny real git repo: a base commit with `pkg.add` and a passing test for it, then an
    UNCOMMITTED goal that adds `pkg.multiply` plus a new test for it -- deliberately left
    uncommitted, because that is the actual, most common state `loop.py verify` runs against
    (SKILL.md: no phase subagent commits before step 6) and the state the module docstring's
    own working-tree-inclusive diff choice exists to handle correctly."""
    d = tempfile.mkdtemp()
    _run(d, ["git", "init", "-q"])
    _run(d, ["git", "checkout", "-q", "-b", "main"])
    _run(d, ["git", "config", "user.email", "t@example.com"])
    _run(d, ["git", "config", "user.name", "t"])
    (pathlib.Path(d) / "pkg").mkdir()
    (pathlib.Path(d) / "pkg" / "__init__.py").write_text("")
    (pathlib.Path(d) / "pkg" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (pathlib.Path(d) / "tests").mkdir()
    (pathlib.Path(d) / "tests" / "test_calc.py").write_text(
        "from pkg.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    _run(d, ["git", "add", "-A"])
    _run(d, ["git", "commit", "-q", "-m", "base"])
    _run(d, ["git", "update-ref", "refs/remotes/origin/main", "HEAD"])
    _run(d, ["git", "checkout", "-q", "-b", "sdlc/999"])
    # The goal's own uncommitted change: real new behaviour, plus a test that actually checks it.
    (pathlib.Path(d) / "pkg" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n")
    (pathlib.Path(d) / "tests" / "test_calc.py").write_text(
        "from pkg.calc import add, multiply\n\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_multiply():\n    assert multiply(2, 3) == 6\n")
    return d


def test_collector_failed_ids_matches_the_file_level_short_summary_line():
    """pytest does not always print the per-id `found no collectors` line: confirmed live that
    under a captured, non-tty pipe (this module's own subprocess shape) pytest 8.4.2 prints ONLY
    the file-level `ERROR <relpath>` short-summary line for a whole-file collection failure, never
    the per-id one -- the same failure, run interactively, prints both. Without this second match,
    `code == 4` against a captured pipe was silently read as `absent` (nothing measured) instead of
    a real kill, regardless of colour."""
    dr = _dr()
    output = (
        "==================================== ERRORS ====================================\n"
        "_____________________ ERROR collecting tests/test_calc.py ______________________\n"
        "ImportError while importing test module 'tests/test_calc.py'.\n"
        "=========================== short test summary info ============================\n"
        "ERROR tests/test_calc.py\n"
        "1 error in 0.04s\n"
    )
    tests = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_multiply"]
    assert dr._collector_failed_ids(output, tests) == set(tests)


def test_collector_failed_ids_file_level_match_does_not_credit_an_unrelated_file():
    dr = _dr()
    output = "ERROR tests/test_unrelated.py\n"
    tests = ["tests/test_calc.py::test_add"]
    assert dr._collector_failed_ids(output, tests) == set()


def test_CONTROL_a_real_kill_survives_a_forced_colour_environment():
    """judged_when: the actual defect, reproduced with the actual trigger. Confirmed live on a
    machine with `FORCE_COLOR=3` in its own shell: pytest honours it even under
    `subprocess.run(capture_output=True)`, wrapping the exact lines `_collector_failed_ids`/
    `_failed_ids` match in ANSI escapes pytest's own `--color=no` flag does not fully suppress
    (CPython 3.13's own colourised tracebacks answer to `FORCE_COLOR` independently of pytest's
    flag). Sets `FORCE_COLOR` explicitly in THIS process's own environment for the duration of the
    call, rather than depending on whatever happens to already be set on the machine running the
    suite, so this reproduces the bug -- and proves the fix -- on any machine, not just one that
    happens to carry the same shell setting this was first found on."""
    dr = _dr()
    d = _real_repo()
    old = os.environ.get("FORCE_COLOR")
    os.environ["FORCE_COLOR"] = "3"
    try:
        tests = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_multiply"]
        r = dr.run(d, "999", d, "origin/main", tests)
        assert r["verdict"] == dr.VERIFIED, r
        assert r["kills"] == ["tests/test_calc.py::test_multiply"], r
        assert r["survivors"] == ["tests/test_calc.py::test_add"], r
    finally:
        if old is None:
            os.environ.pop("FORCE_COLOR", None)
        else:
            os.environ["FORCE_COLOR"] = old
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_CONTROL_a_real_new_test_that_exercises_new_behaviour_is_a_real_kill():
    """judged_when: the actual defect this issue names, reproduced and then closed for real.

    BEFORE #2240, nothing in this repo could tell `test_multiply` (which genuinely exercises the
    new `multiply` function) apart from a vacuous test that merely restates a constant -- mutmut is
    absent and wired into nothing, so #1935's `mutation` witness kind has never been written by
    anything. This drives REAL git (a real worktree add) and REAL pytest (twice: once implicitly by
    the fact that HEAD's working tree already passes both tests, and once for real inside the
    scratch tree) rather than a fake runner, so a broken revert (e.g. copying the WRONG file, or
    failing to actually exclude the production change) would be caught here and nowhere else."""
    dr = _dr()
    d = _real_repo()
    try:
        # Sanity precondition: the CURRENT tree (production change + new test both present) is
        # green -- exactly the `passed=True` gate this control only ever runs behind.
        code, out = _run(d, ["python3", "-m", "pytest", "-q",
                              "tests/test_calc.py::test_add", "tests/test_calc.py::test_multiply"])
        assert code == 0, "the fixture itself is not green: %s" % out

        tests = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_multiply"]
        r = dr.run(d, "999", d, "origin/main", tests)
        assert r["verdict"] == dr.VERIFIED, r
        assert r["kills"] == ["tests/test_calc.py::test_multiply"], r
        assert r["survivors"] == ["tests/test_calc.py::test_add"], r

        # The scratch worktree must be gone afterward -- "never mutate the real worktree" cuts
        # both ways: nothing borrowed for the check may be left behind either.
        assert not dr.scratch_dir(d, "999").exists()
        # And the REAL tree must be completely untouched -- still green, still has both functions.
        code, _ = _run(d, ["python3", "-m", "pytest", "-q",
                            "tests/test_calc.py::test_add", "tests/test_calc.py::test_multiply"])
        assert code == 0, "the real worktree was mutated by the check"
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_CONTROL_a_vacuous_new_test_that_does_not_exercise_new_behaviour_survives_uncredited():
    """The mirror image, and the other half of the same worked example: a new test that asserts
    something already true of the BASE code (never touches the new behaviour at all) must NOT be
    credited as a kill -- crediting it would be the exact vacuous-test failure #2240 exists to
    catch, just moved one layer down into this control's own logic."""
    dr = _dr()
    d = tempfile.mkdtemp()
    try:
        _run(d, ["git", "init", "-q"])
        _run(d, ["git", "checkout", "-q", "-b", "main"])
        _run(d, ["git", "config", "user.email", "t@example.com"])
        _run(d, ["git", "config", "user.name", "t"])
        (pathlib.Path(d) / "pkg").mkdir()
        (pathlib.Path(d) / "pkg" / "__init__.py").write_text("")
        (pathlib.Path(d) / "pkg" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (pathlib.Path(d) / "tests").mkdir()
        (pathlib.Path(d) / "tests" / "test_calc.py").write_text(
            "from pkg.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
        _run(d, ["git", "add", "-A"])
        _run(d, ["git", "commit", "-q", "-m", "base"])
        _run(d, ["git", "update-ref", "refs/remotes/origin/main", "HEAD"])
        _run(d, ["git", "checkout", "-q", "-b", "sdlc/998"])
        # A "new" test that asserts something already true of the UNCHANGED base code -- no
        # production change accompanies it at all.
        (pathlib.Path(d) / "tests" / "test_calc.py").write_text(
            "from pkg.calc import add\n\n\n"
            "def test_add():\n    assert add(2, 3) == 5\n\n\n"
            "def test_add_is_commutative_vacuously():\n    assert add(1, 1) == add(1, 1)\n")

        tests = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_add_is_commutative_vacuously"]
        code, out = _run(d, ["python3", "-m", "pytest", "-q", *tests])
        assert code == 0, "the fixture itself is not green: %s" % out

        r = dr.run(d, "998", d, "origin/main", tests)
        assert r["verdict"] == dr.UNVERIFIED, r
        assert r["kills"] == [], r
        assert "tests/test_calc.py::test_add_is_commutative_vacuously" in r["survivors"]
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

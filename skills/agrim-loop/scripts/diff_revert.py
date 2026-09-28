"""Diff-revert kill control (issue #2240) -- a mutmut-free companion to mutation.py's #1935 gate.

WHY. mutation.py (#1935) requires mutmut, which its own docstring records as absent on this
machine -- and on every dev machine, permanently, because installing it on the verify path would
mean a network fetch (build isolation) on every goal or a bare `pip install` that fails outright on
a PEP-668 interpreter. It is also wired into nothing: no `loop.py` caller, no SKILL.md mention, no
`gates` key in config. So the kit's own "a test that proves nothing" check has never actually run
for anyone. This needs only git and pytest, both already load-bearing for every other control on
this same verify path.

WHAT IT PROVES. Given the exact test ids a goal's diff is already known to have touched (the SAME
resolution #1934's witness check uses, so the two can never disagree about which tests are "in
play"), copy the repository to a SCRATCH worktree checked out at the goal's OWN fork point --
production code exactly as it was before the goal touched it -- then overlay just the goal's
CURRENT test files on top and rerun those specific tests there. A test that goes RED is a REAL
kill: the goal's production code changed and this test noticed. Recorded by the caller as a
`mutation` witness (#1934's STRONG kind, which #1935 was designed to write and, being unreachable,
never has). A test that stays green there proves nothing new -- it passes with or without the
change, which is exactly the "test that proves nothing" this whole vocabulary exists to catch.

NO PATCH, NO HUNKS, NO MUTATION LIBRARY. Reverting "the goal's production-code hunks" is done by
never letting the scratch tree see them in the first place: `git worktree add --detach <scratch>
<fork>` is a full checkout of the tree AT THE FORK, so every non-test file is already at its
pre-goal content and a file the goal added outright is simply absent -- both exactly what
"reverted" means -- with no patch-apply fuzz to fail on. Only the files the diff actually touched
(the test files) are then copied in from the real tree, which is what needs to be reverted for
the ANSWER to be about behaviour, not about which file was touched.

RESTORE ON ENTRY, NEVER ON EXIT ALONE (AGENTS.md's #1685 lesson, carried over deliberately: an exit
trap under a wall-clock kill is best-effort and is not guaranteed to run -- two real daemons sat
SIGSTOP'd for hours exactly that way). This module DOES clean up its own scratch worktree in a
`finally` on every path it takes -- but the caller that gets killed by its OWN wall-clock timeout,
between `os.getpid()`-stamping the reservation and that `finally` running, leaves the directory
behind with nothing left to fire the trap. So the NEXT run checks first: `_reclaim_stale` reads the
pid the scratch directory was reserved under and, if that pid is not alive on this machine, tears
the leftover down itself before doing anything else -- the run that finds the mess cleans it, not
the run that made it. A pid that IS still alive means a genuinely concurrent run and this one
abstains (`absent`) rather than fight over one worktree.

COST, the issue's own bound: git and pytest only, scoped to the tests already known to be in play
(shared with #1934) and bounded by MAX_SCOPED_TESTS below, same shape as flake_check's own file
cap and for the same reason -- a wrong diff should decline loudly, not silently mutate/run a scope
the caller never intended.

WHAT THIS DOES NOT COVER, named rather than hidden: a fixture the goal added in a `conftest.py`
that tamper_scan's `_TEST_PATH` does not classify as a test file (e.g. one at the repo root, not
under a `tests?/` directory) is reverted along with production code, and a new test that needs it
will show as a false, uncredited "survivor" rather than a kill -- the safe direction, since it
never fabricates a credit, but it is a real gap this module does not close.
"""
import os
import pathlib
import re
import shutil
import subprocess
import time

#: #1933/#1934's vocabulary, reused verbatim -- a fourth string here is the exact divergence both
#: of those issues warn against.
VERIFIED, UNVERIFIED, ABSENT = "verified", "unverified", "absent"

#: Bounds the potentially-long part (the reverted-tree pytest run). git calls get their own,
#: shorter budget below -- a hung `git worktree add` must not hang the whole verify path.
DEFAULT_TIMEOUT = 120
_GIT_TIMEOUT = 30

#: Same shape and same reason as flake_check.MAX_SCOPED_FILES: a wrong base ref makes the "goal's
#: own diff" balloon into the whole repo's tests, and a check that silently truncated to the first
#: N would report on a subset while looking complete. This declines instead, loudly.
MAX_SCOPED_TESTS = 50

#: Duplicated from flake_check.py (not imported) -- this module stays independently loadable, the
#: same posture witness.py and flake_check.py already take toward each other; loop.py is the only
#: place these gates are wired together.
_NOT_A_RESULT = frozenset((2, 3, 4, 5))


def _measured(code):
    """False when a pytest exit code means NOTHING WAS MEASURED (interrupted/internal/usage-error/
    no-tests-collected, or a negative code -- killed by a signal), as opposed to a real result."""
    return code is not None and not (code in _NOT_A_RESULT or code < 0)


def _failed_ids(output):
    """Node ids pytest reported as FAILED. Bracket forms (`id[case]`) are matched against a bare
    touched id by their caller, not here -- this just reads what pytest printed."""
    out = set()
    for line in (output or "").splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[0] == "FAILED":
            out.add(parts[1])
    return out


def _collector_failed_ids(output, tests):
    """Of `tests`, the ones pytest could not even COLLECT against the reverted tree -- measured
    live, 2026-09-04, this is the SINGLE MOST COMMON real kill shape, not an edge case: a goal that
    adds a function and a test importing it produces a module that fails to import once that
    function is reverted away, so pytest never reaches a single assertion. Explicit node-id
    invocation (this module always runs one) reports that as `ERROR: found no collectors for
    <path>`, one line per requested id, and exits 4 -- the SAME code as an unrelated CLI usage
    error, which is exactly why `_measured` alone cannot be trusted to tell them apart; matching
    this line by id is what makes the distinction. Reading it as "nothing measured" would be
    backwards: the test could not have gone any redder. Matched by SUFFIX because pytest names the
    scratch tree's OWN absolute path, never the repo-relative id `tests` carries.

    A SECOND, independent signal, for the SAME underlying failure: confirmed live that pytest does
    not always print the per-id `found no collectors` line at all -- under `subprocess.run(...,
    capture_output=True)` specifically (this module's own caller shape), pytest 8.4.2 prints only
    the file-level short-summary line `ERROR <relpath>` and never the per-id one, even though the
    exact same collection failure, run without a captured pipe, prints both. Rather than depend on
    which of pytest's two reporting paths a given pytest version/invocation takes, this also
    matches `ERROR <relpath>` (pytest's own "short test summary info" format, stable public
    output) and credits EVERY id in `tests` under that file -- the whole-file over-crediting this
    invites (a byte-for-byte untouched sibling test in the same file) is exactly what
    `_new_test_ids` exists to narrow back down, downstream in `run()`; this function's own job is
    only to stop `code == 4` reading as `absent` when the file plainly could not even import."""
    out = set()
    prefix = "ERROR: found no collectors for "
    by_file = {}
    for t in tests:
        by_file.setdefault(t.split("::", 1)[0], []).append(t)
    for line in (output or "").splitlines():
        line = line.strip()
        if line.startswith(prefix):
            named = line[len(prefix):].strip()
            for t in tests:
                if named == t or named.endswith("/" + t):
                    out.add(t)
            continue
        if line.startswith("ERROR "):
            named = line[len("ERROR "):].strip()
            if named in by_file:
                out.update(by_file[named])
    return out


def _new_test_ids(gitc, root, fork, tests):
    """Of `tests`, the ones GENUINELY ABSENT at `fork` -- the file didn't exist yet, or the file
    did but this exact function name did not.

    WHY THIS EXISTS. `_collector_failed_ids` alone over-credits: a whole-FILE collection error
    (an import that fails because the reverted tree lacks a name the file needs) reports `found no
    collectors` for EVERY requested id in that file, including a byte-for-byte pre-existing sibling
    that never touched the reverted change at all -- measured live, 2026-09-04, a fixture's
    untouched `test_add` alongside a genuinely new `test_multiply` both come back this way. Crediting
    `test_add` with a STRONG `mutation` witness there would be a fabricated credit of the exact
    shape this whole control exists to catch, just arrived at from the collateral-damage side
    instead of the always-green side. This is the guard: only a test this function confirms is new
    may be credited off `_collector_failed_ids` alone.

    CONSERVATIVE ON PURPOSE, NOT EXHAUSTIVE. A test already present at `fork` is treated as "not
    new" here even when its BODY changed since -- that finer case is still caught, correctly, by
    the ordinary `_failed_ids` path (a real per-test assertion failure pytest attributes with
    certainty), so nothing is lost; this function only needs to cover the coarser case a whole-file
    import failure cannot itself distinguish, and under-counting there is the safe direction the
    module's own docstring already commits to elsewhere.

    ONE `git show` PER TOUCHED FILE, not per test -- the cost this adds is bounded by the number of
    distinct FILES in `tests`, which `MAX_SCOPED_TESTS` already caps, and only runs at all when
    `_collector_failed_ids` found something to check (the caller skips this entirely on the far
    more common clean-pass/clean-fail runs)."""
    out = set()
    by_file = {}
    for t in tests:
        by_file.setdefault(t.split("::", 1)[0], []).append(t)
    for rel, ids in by_file.items():
        code, content = gitc(root, ["show", "%s:%s" % (fork, rel)])
        if code != 0:
            out.update(ids)                 # file absent at the fork -- everything in it is new
            continue
        for t in ids:
            name = t.split("::")[-1].split("[", 1)[0]
            if not re.search(r"^\s*(?:async\s+)?def\s+" + re.escape(name) + r"\s*\(", content,
                              re.M):
                out.add(t)
    return out


def _git(root, argv, timeout=_GIT_TIMEOUT):
    try:
        p = subprocess.run(["git", *argv], cwd=str(root), capture_output=True, text=True,
                            timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return None, "git %s timed out after %ss" % (" ".join(argv), timeout)
    except Exception as exc:                # noqa: BLE001 - a git outage is `absent`, never a crash
        return None, str(exc)


#: `--color=no` alone does not reach every escape this subprocess's output can carry: CPython
#: 3.13's own colourised-traceback feature honours `FORCE_COLOR`/`PYTHON_COLORS` directly,
#: independent of pytest's CLI flag, so a bare traceback line inside an ERRORS section still came
#: back wrapped in ANSI even with `--color=no` passed -- confirmed live on a machine with
#: `FORCE_COLOR=3` set globally. `_collector_failed_ids`/`_failed_ids` both match pytest's plain
#: text verbatim; a kill/miss verdict depending on the OPERATOR's own shell colour settings is not
#: acceptable, so every colour-forcing variable this ecosystem recognises is cleared here rather
#: than trusted to the child's own flag handling.
_COLOR_ENV_OVERRIDES = {"FORCE_COLOR": "0", "NO_COLOR": "1", "PYTHON_COLORS": "0", "PY_COLORS": "0"}


def _pytest(cwd, argv, timeout):
    env = dict(os.environ)
    env.update(_COLOR_ENV_OVERRIDES)
    p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _pid_alive(pid):
    """Same contract as ledger.pid_alive (probe-only, fails toward True): duplicated in a few
    lines rather than imported, for the same independence this module's docstring already commits
    to. `os.kill(pid, 0)` sends no signal -- ProcessLookupError is the kernel confirming the pid is
    genuinely gone; anything else fails toward "maybe still alive, don't reclaim yet"."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:                       # noqa: BLE001 - see the fail-toward-True contract above
        return True


def scratch_dir(sdlc_dir, goal):
    """The ONE deterministic scratch-worktree path for a given (sdlc_dir, goal) -- deterministic
    on purpose, so a leftover from an interrupted run is exactly where the next run looks for it.
    Resolved to an ABSOLUTE path: `sdlc_dir` is routinely a path relative to the loop's own process
    cwd (the main checkout), while every `git` call this module makes runs with `cwd=root` -- a
    goal's OWN worktree, a different directory. A relative path handed to that subprocess would
    resolve against the wrong cwd entirely."""
    stem = str(goal).replace("/", "_").replace("..", "_")
    return (pathlib.Path(sdlc_dir) / "state" / "diff_revert" / stem).resolve()


def _owner_path(scratch):
    return scratch.parent / (scratch.name + ".owner")


def cleanup(root, scratch, git_cmd=None):
    """Tear down one scratch worktree and its owner marker. Tolerant of every already-gone state a
    stale or half-finished prior run can leave -- `git worktree remove` first (the correct,
    registration-aware teardown); if THAT fails (the registration is gone but the directory still
    has files, or vice versa) a raw `rmtree` + `worktree prune` finishes the job. Never raises:
    cleanup must not become a second failure stacked on whatever it is recovering from."""
    gitc = git_cmd or _git
    try:
        if scratch.exists():
            code, _out = gitc(root, ["worktree", "remove", "--force", str(scratch)])
            if code != 0:
                shutil.rmtree(scratch, ignore_errors=True)
                gitc(root, ["worktree", "prune"])
    except Exception:                       # noqa: BLE001 - see the NEVER RAISES contract above
        pass
    try:
        owner = _owner_path(scratch)
        if owner.exists():
            owner.unlink()
    except Exception:                       # noqa: BLE001 - same contract
        pass


def _reclaim_stale(root, scratch, pid_alive, git_cmd=None):
    """RESTORE ON ENTRY (#2240) -- see the module docstring for the #1685 lesson this implements.

    -> True if the path is clear to use (nothing was there, or a stale leftover was just torn
    down); False if a LIVE owner holds it right now, in which case the caller must not touch it.

    Reads the owner pid rather than trusting mere existence, because existence alone cannot tell a
    genuinely concurrent run (rare, but the exact case verify_goal's own evidence-overwrite warning
    already guards elsewhere) from a dead one -- and guessing wrong in the "still alive" direction
    only costs one `absent` verdict, where guessing wrong the other way means two runs racing to
    write the same worktree."""
    owner = _owner_path(scratch)
    if not scratch.exists() and not owner.exists():
        return True
    pid = None
    try:
        pid = int(owner.read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid is not None and pid_alive(pid):
        return False                        # a live sibling owns it -- do not touch
    cleanup(root, scratch, git_cmd)
    return True


def run(sdlc_dir, goal, root, base_ref, tests, timeout=DEFAULT_TIMEOUT,
        git_cmd=None, run_cmd=None, pid_alive=None):
    """-> {"verdict", "kills": [...ids], "survivors": [...ids], "reason", "ms"}.

    THREE STATES, SAME VOCABULARY AS #1933/#1934/#1935, AND `absent` IS NOT A PASS:
      verified    -- at least one of `tests` went RED against the reverted tree: a real kill.
      unverified  -- measured, but every one of `tests` stayed green: proves nothing new.
      absent      -- NOTHING WAS MEASURED: no tests, no base ref, a live sibling, a git/pytest
                     outage, a timeout, or the scope bound. Never silently credited as a pass.

    `tests` is caller-resolved (loop.py's `_diff_revert_verdict` shares #1934's own `_changed_tests`
    input) -- this function only answers "does it survive without the production change", never
    which tests are in play; that keeps the two checks unable to disagree about scope."""
    started = time.perf_counter()

    def done(verdict, reason, kills=(), survivors=()):
        return {"verdict": verdict, "kills": list(kills), "survivors": list(survivors),
                "reason": reason, "ms": int((time.perf_counter() - started) * 1000)}

    tests = sorted(set(tests or []))
    if not tests:
        return done(ABSENT, "no changed test files")
    if not base_ref:
        return done(ABSENT, "no work record -- base ref unknown, nothing to revert to")
    if len(tests) > MAX_SCOPED_TESTS:
        return done(ABSENT, "scope bound: %d touched test(s) > %d -- declining rather than "
                             "measuring a subset silently" % (len(tests), MAX_SCOPED_TESTS))

    gitc = git_cmd or _git
    is_alive = pid_alive or _pid_alive
    scratch = scratch_dir(sdlc_dir, goal)
    scratch.parent.mkdir(parents=True, exist_ok=True)

    if not _reclaim_stale(root, scratch, is_alive, gitc):
        return done(ABSENT, "a live diff-revert run already owns this goal's scratch worktree")

    # Reserve the slot BEFORE creating anything, so a kill between here and the `finally` below
    # leaves an owner marker the NEXT run's _reclaim_stale can act on -- see the module docstring.
    _owner_path(scratch).write_text(str(os.getpid()))
    try:
        code, out = gitc(root, ["merge-base", "HEAD", base_ref])
        if code != 0:
            return done(ABSENT, "git merge-base HEAD %s failed: %s" % (base_ref, out))
        fork = out.strip()

        code, out = gitc(root, ["worktree", "add", "--detach", "--quiet", str(scratch), fork])
        if code != 0:
            return done(ABSENT, "could not create the scratch worktree at %s: %s" % (fork, out))

        for rel in sorted({t.split("::", 1)[0] for t in tests}):
            src = pathlib.Path(root) / rel
            if not src.is_file():
                continue                    # a test file the goal deleted -- nothing to overlay
            dst = scratch / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        runner = run_cmd or _pytest
        try:
            # --color=no, ALWAYS, regardless of the caller's own terminal: `_collector_failed_ids`/
            # `_failed_ids` both match pytest's plain-text output verbatim (`line.startswith(...)`,
            # `parts[0] == "FAILED"`), and a `FORCE_COLOR`/`PY_COLORS` env var pytest itself honours
            # even under a piped, non-tty `subprocess.run(capture_output=True)` call wraps exactly
            # those lines in ANSI escapes -- confirmed live: a real kill (an ImportError collection
            # failure) silently read back as `absent` on a machine with `FORCE_COLOR=3` set, because
            # the escape-prefixed line no longer matched either prefix check. A tool whose kill/miss
            # verdict depends on the OPERATOR's personal shell colour settings is not a defect this
            # kit accepts; forcing it off here makes the output format the same on every machine.
            code, out = runner(scratch, ["python3", "-m", "pytest", "-q", "--color=no", *tests],
                                timeout)
        except subprocess.TimeoutExpired:
            return done(ABSENT, "pytest timed out after %ss against the reverted tree" % timeout)
        except Exception as exc:            # noqa: BLE001 - a tool outage is `absent`, never a crash
            return done(ABSENT, "pytest could not run against the reverted tree (%s)" % exc)

        # `_collector_failed_ids` is checked BEFORE `_measured` gates on exit code, deliberately:
        # a collection error against the reverted tree exits 4, the SAME code as a bare usage
        # error, and `_measured` alone cannot tell those apart. Its own per-id evidence is what
        # promotes exit 4 from "nothing measured" to a real result when it names one of `tests`.
        collector_failed = _collector_failed_ids(out, tests)
        if not _measured(code) and not collector_failed:
            return done(ABSENT, "reverted-tree pytest exit=%s -- nothing measured" % code)

        failed = _failed_ids(out)
        killed = {t for t in tests if t in failed or any(f.split("[", 1)[0] == t for f in failed)}
        # A test that FAILED individually is already fully attributed -- only the ones left over
        # in `collector_failed` need the extra `_new_test_ids` precision layer, and only then is
        # its one extra `git show` per touched file worth paying for.
        extra = collector_failed - killed
        if extra:
            killed |= extra & _new_test_ids(gitc, root, fork, tests)
        kills = [t for t in tests if t in killed]
        survivors = [t for t in tests if t not in killed]
        if kills:
            return done(VERIFIED,
                        "%d of %d touched test(s) went RED (failed, or could not even be "
                        "collected) with production code reverted" % (len(kills), len(tests)),
                        kills, survivors)
        if collector_failed:
            return done(UNVERIFIED,
                        "%d touched test(s) share a collection error with the reverted tree but "
                        "none is individually new there -- not proven to exercise it"
                        % len(survivors), kills, survivors)
        return done(UNVERIFIED,
                    "all %d touched test(s) still pass with production code reverted -- not "
                    "proven to exercise it" % len(tests), kills, survivors)
    finally:
        cleanup(root, scratch, gitc)

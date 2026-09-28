"""Control tests for `tests/conftest.py::_no_live_gh` (issue #1495).

These prove the guard itself, not any one call site: an un-injected `gh` invocation must fail
loud and name the offending test; a call that is not `gh` must pass through untouched; and the
declared escape hatch (`@pytest.mark.live_gh`) must genuinely reach the real `subprocess.run`, not
merely fail to raise. `test_a_marked_test_reaches_the_real_process_for_a_gh_shaped_argv0` proves
that last one the strong way -- by actually running a process and reading its output back -- rather
than the weak way (asserting no exception was raised), which would also pass if the fixture simply
did nothing at all.

None of these need the real `gh` binary. `test_a_call_shaped_like_gh_is_blocked_before_it_execs`
proves that directly: `argv[0]` is a path that does not exist on disk, and the guard still raises
BEFORE `subprocess.run` would have tried (and failed differently, with `FileNotFoundError`) to
exec it -- the interception is a pure argv inspection, never a real process attempt.
"""
import subprocess

import pytest


def test_a_call_shaped_like_gh_is_blocked_before_it_execs():
    """No real `gh` binary needed: the guard inspects argv, so a path that cannot possibly exist
    still gets caught, proving this is not "the exec happened to fail" but the guard doing its job."""
    with pytest.raises(RuntimeError) as exc:
        subprocess.run(["/does/not/exist/gh", "issue", "list"], capture_output=True, text=True)
    assert "test_a_call_shaped_like_gh_is_blocked_before_it_execs" in str(exc.value)
    assert "#1495" in str(exc.value)


def test_a_bare_gh_on_path_is_also_blocked():
    """The everyday shape every real call site uses: `["gh", ...]`, resolved off `PATH`."""
    with pytest.raises(RuntimeError):
        subprocess.run(["gh", "issue", "list"], capture_output=True, text=True)


def test_a_shell_string_form_is_also_blocked():
    """`shell=True` callers pass a single string, not an argv list -- the guard must parse both."""
    with pytest.raises(RuntimeError):
        subprocess.run("gh pr list", shell=True, capture_output=True, text=True)


def test_a_non_gh_call_is_never_touched(tmp_path):
    """The guard is scoped to `gh` by name -- every other subprocess call in the suite (git,
    bash, the fake-binary idiom other tests already use) must be completely unaffected."""
    script = tmp_path / "not-gh"
    script.write_text("#!/bin/sh\necho fine\n")
    script.chmod(0o755)
    proc = subprocess.run([str(script)], capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "fine"


@pytest.mark.live_gh
def test_a_marked_test_reaches_the_real_process_for_a_gh_shaped_argv0(tmp_path):
    """The ONLY escape hatch. Proven the strong way: a fake executable literally named `gh` (so
    it matches the same detection every other test here relies on) actually runs and its real
    output comes back -- not just "no exception was raised", which a no-op fixture would also
    satisfy."""
    fake_gh = tmp_path / "gh"
    fake_gh.write_text("#!/bin/sh\necho REACHED-THE-REAL-SUBPROCESS\n")
    fake_gh.chmod(0o755)
    proc = subprocess.run([str(fake_gh), "issue", "list"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "REACHED-THE-REAL-SUBPROCESS"


def test_an_unmarked_test_cannot_reach_a_gh_shaped_fake_either(tmp_path):
    """Mirror of the marked test above, same fake binary -- without the marker it is still
    blocked, so the escape hatch is opt-in per test, not a blanket exemption for this file."""
    fake_gh = tmp_path / "gh"
    fake_gh.write_text("#!/bin/sh\necho should-never-run\n")
    fake_gh.chmod(0o755)
    with pytest.raises(RuntimeError):
        subprocess.run([str(fake_gh), "issue", "list"], capture_output=True, text=True)

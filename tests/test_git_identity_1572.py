"""#1572: tests that override HOME get their git identity from ONE monkeypatch-scoped helper,
`git_identity_support.scoped_git_identity`, and nothing sets a git identity process-wide.

Three groups:
- the helper itself (tests 1-2), run in an environment stripped of every ambient identity source, so they cannot pass
  on the host's identity;
- the conftest identity-env scanner (`identity_env_offenders`): no suite-wide fixture sets `GIT_AUTHOR_*` /
  `GIT_COMMITTER_*`, in any process-global form;
- the HOME-override ratchet (`home_override_offenders`): a test file that overrides HOME without a scoped identity
  source is an offender unless `ALLOWLIST` names it with a reason; the allowlist only shrinks.

The documented no-identity gesture (G0):
`env -u GIT_AUTHOR_NAME -u GIT_AUTHOR_EMAIL -u GIT_COMMITTER_NAME -u GIT_COMMITTER_EMAIL GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_SYSTEM=/dev/null python -m pytest tests/test_git_identity_1572.py -q -p no:cacheprovider -rf`."""
import os
import pathlib
import subprocess

import pytest

import git_identity_support
from git_identity_support import home_override_offenders, identity_env_offenders

TESTS = pathlib.Path(__file__).resolve().parent

IDENTITY_VARS = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")

#: HOME-overriding test files proven safe by the #1572 step 5 audit, each with a one-line reason. Only shrinks.
ALLOWLIST = {
    "tests/test_bench_arms_isolation.py": "passes the #1572 no-identity audit (G0 + empty HOME)",
    "tests/test_bench_isolation_launcher.py": "passes the #1572 no-identity audit (G0 + empty HOME)",
    "tests/test_benchmark_harness.py": "passes the #1572 no-identity audit (G0 + empty HOME)",
    "tests/test_build_public_tree.py": "passes the #1572 no-identity audit (G0 + empty HOME)",
    "tests/test_build_public_tree_refusals.py": "passes the #1572 no-identity audit (G0 + empty HOME)",
    "tests/test_leak_refs.py": "passes the #1572 no-identity audit (G0 + empty HOME)",
    "tests/test_phase_report.py": "passes the #1572 no-identity audit (G0 + empty HOME)",
    "tests/test_public_tree_review_fixes.py": "passes the #1572 no-identity audit (G0 + empty HOME)",
    "tests/test_upstream.py": "passes the #1572 no-identity audit (G0 + empty HOME)",
    "tests/upkeep_support.py": "run_case's HOME override drives code under a gate trap that makes no commit; its importers"
                               " test_upkeep_audit/_harness/_proof pass the #1572 no-identity audit",
}


# --------------------------------------------------------------------------- the helper


@pytest.fixture
def no_identity(tmp_path, monkeypatch):
    """No ambient git identity: global and system config nulled, identity env deleted, HOME an empty dir, and
    `user.useConfigOnly=true` so git cannot guess an identity from passwd/hostname on any host."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for var in IDENTITY_VARS + ("EMAIL",):
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    return tmp_path


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


def test_scoped_identity_lets_git_commit_with_no_ambient_identity(no_identity, monkeypatch):
    """Precondition first: with no ambient identity an empty commit REFUSES, which proves the stripped env. Then the
    helper, and the same commit succeeds as `t <t@example.com>`."""
    repo = no_identity / "repo"
    assert subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, stdin=subprocess.DEVNULL,
                          timeout=60).returncode == 0
    before = _git(repo, "commit", "--allow-empty", "-q", "-m", "before")
    assert before.returncode != 0, "precondition: a commit must refuse with no identity, got: %r" % (before.stdout,)
    refusal = before.stderr
    assert any(s in refusal for s in ("identity unknown", "Please tell me who you are", "empty ident",
                                      "unable to auto-detect", "no email was given")), refusal

    path = git_identity_support.scoped_git_identity(no_identity / "git-identity", monkeypatch)

    after = _git(repo, "commit", "--allow-empty", "-q", "-m", "after")
    assert after.returncode == 0, after.stderr
    assert _git(repo, "log", "-1", "--format=%cn <%ce>").stdout.strip() == "t <t@example.com>"
    assert path == no_identity / "git-identity" / "git-identity.gitconfig"


def test_scoped_identity_refuses_loudly_when_git_ignores_the_global_file(no_identity, monkeypatch):
    """A git that ignores `GIT_CONFIG_GLOBAL` (here: a fake `git` that prints nothing and exits 0) must make the helper
    raise AssertionError naming the variable, never return as if the identity were in place."""
    fake = no_identity / "fake-bin"
    fake.mkdir()
    script = fake / "git"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake) + os.pathsep + os.environ.get("PATH", ""))
    raised = None
    try:
        git_identity_support.scoped_git_identity(no_identity / "git-identity", monkeypatch)
    except AssertionError as exc:
        raised = str(exc)
    assert raised is not None, "the helper returned on a git that ignores GIT_CONFIG_GLOBAL"
    assert "GIT_CONFIG_GLOBAL" in raised, raised


# --------------------------------------------------------------------------- the conftest identity-env scanner


def _src(*lines):
    return "\n".join(lines) + "\n"


_FIXTURE_HEAD = ("import os", "import pytest", "", "@pytest.fixture(autouse=True)", "def ident(monkeypatch):")


@pytest.mark.parametrize("line,var", [
    pytest.param('    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")', "GIT_COMMITTER_NAME", id="setenv"),
    pytest.param('    os.environ["GIT_AUTHOR_EMAIL"] = "t@example.com"', "GIT_AUTHOR_EMAIL", id="subscript"),
    pytest.param('    os.environ.update({"GIT_COMMITTER_EMAIL": "t@example.com"})', "GIT_COMMITTER_EMAIL",
                 id="update_dict"),
    pytest.param('    os.environ.update(GIT_AUTHOR_NAME="t")', "GIT_AUTHOR_NAME", id="update_kwargs"),
    pytest.param('    os.environ.setdefault("GIT_COMMITTER_NAME", "t")', "GIT_COMMITTER_NAME", id="setdefault"),
    pytest.param('    os.putenv("GIT_AUTHOR_NAME", "t")', "GIT_AUTHOR_NAME", id="putenv"),
])
def test_identity_env_scanner_flags_each_process_global_form(line, var):
    source = _src(*_FIXTURE_HEAD, line)
    assert identity_env_offenders(source) == [(len(_FIXTURE_HEAD) + 1, var)]


def test_identity_env_scanner_ignores_subprocess_env_dicts():
    """The `test_feature_rebase._git` shape -- an env dict built for one subprocess -- is not a process-global leak."""
    source = _src(
        "import os",
        "import subprocess",
        "",
        "def _git(*args):",
        '    env = dict(os.environ, GIT_COMMITTER_NAME="t")',
        '    env.update({"GIT_AUTHOR_NAME": "t"})',
        '    return subprocess.run(["git", *args], env=env)',
    )
    assert identity_env_offenders(source) == []


def test_conftest_sets_no_process_global_identity():
    """No suite-wide fixture sets a git identity (it would break tests/test_ledger_single_branch.py).
    Blind spot: this scans ONLY tests/conftest.py -- not a nested conftest, a plugin, or a support module's fixture."""
    assert identity_env_offenders((TESTS / "conftest.py").read_text()) == []


# --------------------------------------------------------------------------- the HOME-override ratchet

_NAME = "tests/test_synthetic.py"
_HOME_HEAD = ("import pytest", "", "@pytest.fixture", "def env(tmp_path, monkeypatch):",
              '    monkeypatch.setenv("HOME", str(tmp_path))')


def test_ratchet_flags_a_home_override_without_an_identity_source():
    assert home_override_offenders({_NAME: _src(*_HOME_HEAD)}) == [_NAME]


def test_ratchet_does_not_accept_a_gitconfig_write_as_an_identity_source():
    """A HOME `.gitconfig` is invisible under `GIT_CONFIG_GLOBAL=/dev/null`, so it is no identity source."""
    source = _src(*_HOME_HEAD, '    (tmp_path / ".gitconfig").write_text("[user]\\n\\tname = t\\n\\temail = t@example.com\\n")')
    assert home_override_offenders({_NAME: source}) == [_NAME]


def test_ratchet_rejects_a_lone_committer_email():
    source = _src(*_HOME_HEAD, '    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")')
    assert home_override_offenders({_NAME: source}) == [_NAME]


@pytest.mark.parametrize("variants", [
    pytest.param([
        ("import git_identity_support",
         '    git_identity_support.scoped_git_identity(tmp_path / "id", monkeypatch)'),
        ("from git_identity_support import scoped_git_identity",
         '    scoped_git_identity(tmp_path / "id", monkeypatch)'),
    ], id="scoped_git_identity"),
    pytest.param([
        ("", '    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "id.gitconfig"))'),
    ], id="GIT_CONFIG_GLOBAL"),
    pytest.param([
        ("", '    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")\n'
             '    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")'),
    ], id="GIT_COMMITTER_pair"),
])
def test_ratchet_accepts_each_scoped_identity_source(variants):
    """Each accepted source clears a HOME override. Blind spot: a `GIT_CONFIG_GLOBAL` setenv is accepted without
    checking that the file it names exists or has a `[user]` section."""
    for head, body in variants:
        source = (head + "\n" if head else "") + _src(*_HOME_HEAD, body)
        assert home_override_offenders({_NAME: source}) == [], source


def test_no_test_file_overrides_home_without_a_scoped_identity():
    """Every `tests/*.py` (support modules included) that overrides HOME has a scoped identity source, or is in
    ALLOWLIST with a reason -- exactly, so a stale entry fails too.

    Blind spots: only `<x>.setenv("HOME", ...)` is scanned (not `os.environ["HOME"] = ...`, nor an env dict passed to a
    subprocess); a file is judged whole, not per fixture; only the top-level `tests/*.py` are read; a
    `GIT_CONFIG_GLOBAL` setenv is accepted without checking the file has a `[user]` section; and it does NOT guard a
    commit made by production code with an inherited identity and no HOME override -- the #1572 step 5 no-identity
    audit run is the proof for that (acceptance bullet 3), not this test."""
    sources = {"tests/" + p.name: p.read_text() for p in sorted(TESTS.glob("*.py"))}
    assert "tests/test_upkeep_agent_1516.py" not in ALLOWLIST
    assert home_override_offenders(sources) == sorted(ALLOWLIST)

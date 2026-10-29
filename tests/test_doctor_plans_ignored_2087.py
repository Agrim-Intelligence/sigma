"""#2087 AC-3: /sigma-doctor flags an ignored plans directory when cloud runs look configured, and only then.

Each clause of the guard (git ignores the plans path; the environment looks like a cloud run) is broken on its own
below. Real git in a temporary repository; no network. Not measured on a real cloud container."""
import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py"
NAME = "plans directory is git-ignored and cloud runs look configured"


def _doctor():
    spec = importlib.util.spec_from_file_location("doctor_2087", D)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _repo(tmp_path, ignore=".sdlc/plans/\n"):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text("{}")
    if ignore is not None:
        (tmp_path / ".gitignore").write_text(ignore)
    return tmp_path / ".sdlc"


def test_ignored_plans_in_a_cloud_session_gives_one_warn_row(tmp_path):
    rows = _doctor()._plans_ignored_rows(_repo(tmp_path), {}, env={"CLAUDE_CODE_REMOTE": "true"})
    assert len(rows) == 1 and rows[0]["name"] == NAME and rows[0]["level"] == "WARN" and rows[0]["ok"] is True, rows
    assert "CLAUDE_CODE_REMOTE" in rows[0]["fix"] and "not measured" in rows[0]["fix"]


def test_plans_not_ignored_gives_no_row(tmp_path):
    """Clause 1 broken alone: cloud configured, plans tracked-able -> silent."""
    assert _doctor()._plans_ignored_rows(_repo(tmp_path, ignore=None), {}, env={"CLAUDE_CODE_REMOTE": "true"}) == []


def test_a_negated_ignore_gives_no_row(tmp_path):
    """The repo ignores `.sdlc/*` but re-includes plans: git says not ignored, so no row."""
    base = _repo(tmp_path, ignore=".sdlc/*\n!.sdlc/plans/\n")
    assert _doctor()._plans_ignored_rows(base, {}, env={"CLAUDE_CODE_REMOTE": "true"}) == []


@pytest.mark.parametrize("env", [{}, {"CLAUDE_CODE_REMOTE": "false"}, {"CLAUDE_CODE_REMOTE": ""},
                                 {"SIGMA_GH_GRAPHQL": "off"}, {"SIGMA_TRUSTED_VERIFY_SHA256": "  "}],
                         ids=["unset", "false", "empty", "graphql-off-alone", "blank-hash"])
def test_ignored_plans_without_a_cloud_signal_gives_no_row(tmp_path, env):
    """Clause 2 broken alone: plans ignored, nothing says cloud -> silent (and no git call is needed)."""
    assert _doctor()._plans_ignored_rows(_repo(tmp_path), {}, env=env) == []


def test_the_verify_approval_variable_alone_counts_as_cloud_configured(tmp_path):
    rows = _doctor()._plans_ignored_rows(_repo(tmp_path), {}, env={"SIGMA_TRUSTED_VERIFY_SHA256": "a" * 64})
    assert len(rows) == 1 and "SIGMA_TRUSTED_VERIFY_SHA256" in rows[0]["fix"]


def test_a_directory_that_is_not_a_git_checkout_gives_no_row(tmp_path):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    env = {"CLAUDE_CODE_REMOTE": "1", "GIT_CEILING_DIRECTORIES": str(tmp_path.parent)}
    assert _doctor()._plans_ignored_rows(sdlc, {}, env=env) == []


def test_the_documented_gesture_prints_the_row(tmp_path):
    """The gesture the docs give: `python3 .../doctor.py check .sdlc`, run in the repository root."""
    _repo(tmp_path)
    env = dict(os.environ, CLAUDE_CODE_REMOTE="true")
    done = subprocess.run([sys.executable, str(D), "check", ".sdlc"], cwd=str(tmp_path), env=env,
                          capture_output=True, text=True, timeout=120)
    assert "[WARN] " + NAME in done.stdout, (done.stdout[-1500:], done.stderr[-500:])
    env.pop("CLAUDE_CODE_REMOTE")
    done = subprocess.run([sys.executable, str(D), "check", ".sdlc"], cwd=str(tmp_path), env=env,
                          capture_output=True, text=True, timeout=120)
    assert NAME not in done.stdout

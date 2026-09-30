"""Hermetic controls for the pinned review-baseline utility (#332)."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "readiness" / "baseline.py"


def _module():
    spec = importlib.util.spec_from_file_location("readiness_baseline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("one\ntwo\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_ok():\n    pass\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("tracked documentation\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    (repo / "untracked.py").write_text("this must never count\n")
    return repo


def test_inventory_uses_the_selected_tree_not_untracked_files(tmp_path):
    repo = _repo(tmp_path)
    baseline = _module()

    payload = baseline.inventory(repo, "HEAD")

    assert payload["surfaces"]["python_non_test"] == {"files": 1, "lines": 2, "est_tokens": 2}
    assert payload["surfaces"]["tests"] == {"files": 1, "lines": 2, "est_tokens": 6}
    assert payload["surfaces"]["docs_md"] == {"files": 1, "lines": 1, "est_tokens": 6}
    assert payload["surfaces"]["shell"] == {"files": 0, "lines": 0, "est_tokens": 0}
    assert payload["skills"] == 0


def test_snapshot_is_detached_without_remote_and_hook_rejects_push(tmp_path):
    repo = _repo(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "--bare", str(repo), str(bare)], check=True)
    baseline = _module()
    dest = tmp_path / "review"

    result = baseline.snapshot(repo, "HEAD", dest)

    assert result == dest
    assert _git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert _git(dest, "remote") == ""
    hook = dest / ".git" / "hooks" / "pre-push"
    assert hook.stat().st_mode & 0o777 == 0o755
    _git(dest, "remote", "add", "origin", str(bare))
    push = subprocess.run(["git", "-C", str(dest), "push", "origin", "HEAD:refs/heads/review"],
                          text=True, capture_output=True)
    assert push.returncode != 0
    assert "readiness clone: pushing is disabled" in push.stderr


def test_snapshot_rejects_nonempty_destination(tmp_path):
    repo = _repo(tmp_path)
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "keep").write_text("keep")

    baseline = _module()
    with pytest.raises(baseline.UsageError):
        baseline.snapshot(repo, "HEAD", dest)


def test_status_uses_rest_through_injected_runner(monkeypatch):
    baseline = _module()
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        number = int(args[2].rsplit("/", 1)[1])
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "number": number, "state": "open", "title": f"Issue {number}"}), "")

    monkeypatch.setattr(baseline, "RUN", fake_run)
    assert baseline.status(Path("."), [12, 34], "acme/sigma") == [
        {"number": 12, "state": "open", "title": "Issue 12"},
        {"number": 34, "state": "open", "title": "Issue 34"},
    ]
    assert [call[2] for call in calls] == [
        "repos/acme/sigma/issues/12", "repos/acme/sigma/issues/34",
    ]
    assert all(call[:2] == ["gh", "api"] for call in calls)

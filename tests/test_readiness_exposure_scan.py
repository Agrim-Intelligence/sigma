"""Hermetic controls for the launch exposure scanner (#333)."""
import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "readiness" / "exposure_scan.py"


def _tool():
    spec = importlib.util.spec_from_file_location("exposure_scan_333", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True,
                          capture_output=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    return repo


def _run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], text=True,
                          capture_output=True, timeout=30)


def test_history_finds_deleted_token_that_tracked_does_not_and_never_prints_value(tmp_path):
    repo = _repo(tmp_path)
    token = "ghp_" + "x" * 36
    (repo / "a.txt").write_text("token=" + token, encoding="utf-8")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "add secret")
    (repo / "a.txt").unlink()
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "remove secret")

    history_json, tracked_json = tmp_path / "history.json", tmp_path / "tracked.json"
    history = _run("history", repo, "--json", history_json)
    tracked = _run("tracked", repo, "--json", tracked_json)
    assert history.returncode == 1 and tracked.returncode == 0
    assert token not in history.stdout and token not in history_json.read_text()
    assert history_json.with_suffix(".md").exists()
    assert any(x["rule"] == "gh-token" for x in json.loads(history_json.read_text())["findings"])
    assert json.loads(tracked_json.read_text())["findings"] == []


def test_history_preserves_every_path_for_a_reused_reachable_blob(tmp_path):
    repo = _repo(tmp_path)
    token = "ghp_" + "z" * 36
    (repo / "a.txt").write_text(token, encoding="utf-8")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "first path")
    (repo / "b.txt").write_text(token, encoding="utf-8")
    _git(repo, "add", "b.txt"); _git(repo, "commit", "-qm", "second path")

    out = tmp_path / "history.json"
    result = _run("history", repo, "--json", out)
    finding = next(x for x in json.loads(out.read_text())["findings"] if x["rule"] == "gh-token")

    assert result.returncode == 1
    assert finding["paths"] == ["a.txt", "b.txt"]
    assert len(finding["commits"]) == 2


def test_allowlist_suppresses_exact_path_rule_and_stale_entry_is_a_finding(tmp_path):
    repo = _repo(tmp_path)
    token = "ghp_" + "y" * 36
    (repo / "a.txt").write_text(token, encoding="utf-8")
    (repo / "allow.json").write_text(json.dumps([
        {"path": "a.txt", "rule": "gh-token", "reason": "synthetic fixture"},
        {"path": "gone.txt", "rule": "gh-token", "reason": "must be removed"},
    ]), encoding="utf-8")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "fixture")
    out = tmp_path / "out.json"
    result = _run("tracked", repo, "--allowlist", repo / "allow.json", "--json", out)
    report = json.loads(out.read_text())
    assert result.returncode == 1 and token not in result.stdout and token not in out.read_text()
    assert report["findings"] == []
    assert report["stale_allowlist"] == [{"path": "gone.txt", "rule": "gh-token"}]


def test_blob_scoped_allowlist_does_not_hide_a_changed_fixture_at_the_same_path(tmp_path):
    repo = _repo(tmp_path)
    fixture = repo / "fixture.txt"
    fixture.write_text("/Us" + "ers/fixture/one", encoding="utf-8")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "reviewed fixture")
    reviewed_blob = _git(repo, "rev-parse", "HEAD:fixture.txt")
    allow = repo / "allow.json"
    allow.write_text(json.dumps([{
        "path": "fixture.txt", "rule": "absolute-home-path", "blob": reviewed_blob,
        "reason": "reviewed hermetic fixture",
    }]), encoding="utf-8")

    green_json = tmp_path / "green.json"
    green = _run("tracked", repo, "--allowlist", allow, "--json", green_json)
    assert green.returncode == 0
    assert json.loads(green_json.read_text())["findings"] == []

    fixture.write_text("/Us" + "ers/fixture/two", encoding="utf-8")
    _git(repo, "add", "fixture.txt"); _git(repo, "commit", "-qm", "changed fixture")
    red_json = tmp_path / "red.json"
    red = _run("tracked", repo, "--allowlist", allow, "--json", red_json)
    report = json.loads(red_json.read_text())
    assert red.returncode == 1
    assert any(x["rule"] == "absolute-home-path" and x["path"] == "fixture.txt"
               for x in report["findings"])
    assert report["stale_allowlist"] == [{"path": "fixture.txt", "rule": "absolute-home-path"}]


def test_home_path_is_private_reference_and_large_blob_is_counted_without_reading(tmp_path):
    repo = _repo(tmp_path)
    (repo / "path.txt").write_text("/Users/alice/x", encoding="utf-8")
    (repo / "large.bin").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "fixtures")
    out = tmp_path / "out.json"
    result = _run("history", repo, "--json", out)
    report = json.loads(out.read_text())
    assert result.returncode == 1
    assert any(x["rule"] == "absolute-home-path" and x["path"] == "path.txt"
               for x in report["findings"])
    assert report["skipped"]["oversized"] == 1


def test_private_patterns_cover_normal_windows_paths_and_allow_anthropic_noreply(tmp_path):
    repo = _repo(tmp_path)
    (repo / "windows.txt").write_text(r"C:\Users\alice\x", encoding="utf-8")
    (repo / "safe-email.txt").write_text("noreply@anthropic.com", encoding="utf-8")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "private path fixtures")

    out = tmp_path / "out.json"
    result = _run("tracked", repo, "--json", out)
    findings = json.loads(out.read_text())["findings"]

    assert result.returncode == 1
    assert any(x["rule"] == "absolute-home-path" and x["path"] == "windows.txt" for x in findings)
    assert not any(x["rule"] == "email-address" and x["path"] == "safe-email.txt" for x in findings)


def test_human_evidence_omits_nonshipped_test_and_tool_paths(tmp_path):
    scan = _tool()
    report = {
        "mode": "history", "findings": [
            {"rule": "auth", "path": "tests/removed.py", "line": 7, "blob": "a" * 40},
            {"rule": "auth", "path": "tools/removed.py", "line": 8, "blob": "b" * 40},
        ], "skipped": {"oversized": 0, "binary": 0},
        "counts": {"legacy_issue_references": 0}, "stale_allowlist": [],
    }
    out = tmp_path / "evidence.json"
    scan._write(report, out)
    rendered = out.with_suffix(".md").read_text()
    assert "tests/removed.py" not in rendered and "tools/removed.py" not in rendered
    assert rendered.count("[historical non-shipped path]") == 2
    assert "aaaaaaaaaaaa" in rendered and "bbbbbbbbbbbb" in rendered


def test_refs_lists_remote_and_local_only_tags_without_network(monkeypatch, tmp_path, capsys):
    scan = _tool()
    repo = _repo(tmp_path)
    (repo / "readme").write_text("x", encoding="utf-8")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "initial")
    _git(repo, "tag", "local-only")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    result = scan.scan_refs(repo)
    assert result["remote"] == []
    assert result["local_only_tags"] == ["local-only"]

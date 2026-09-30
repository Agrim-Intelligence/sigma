"""#335 — write-surface scanner and inventory ratchet controls."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "readiness" / "write_surface.py"


def _module():
    spec = importlib.util.spec_from_file_location("write_surface", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scanner_detects_a_github_delete_site(tmp_path):
    source = tmp_path / "a.py"
    source.write_text('import subprocess\n\ndef erase(n):\n    subprocess.run(["gh", "issue", "delete", n])\n')
    findings = _module().scan_paths(tmp_path, [source])
    assert {(f["function"], f["rule"], f["count"]) for f in findings} == {("erase", "gh-issue", 1)}


def test_ratchet_names_an_added_write_site(tmp_path):
    source = tmp_path / "a.py"
    source.write_text('import subprocess\n\ndef erase(n):\n    subprocess.run(["gh", "issue", "delete", n])\n')
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"entries": []}))
    findings = _module().ratchet(tmp_path, inventory)
    assert findings == ["new write site a.py:erase gh-issue -- add it to docs/launch/write-surface.json with its gate"]


def test_ratchet_rejects_an_empty_gate(tmp_path):
    source = tmp_path / "a.py"
    source.write_text('import subprocess\n\ndef erase(n):\n    subprocess.run(["gh", "issue", "delete", n])\n')
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"entries": [{"path": "a.py", "function": "erase", "rule": "gh-issue", "count": 1, "gate": "", "risk": "high"}]}))
    assert _module().ratchet(tmp_path, inventory) == ["empty gate a.py:erase gh-issue"]


def test_live_path_selection_uses_tracked_files(tmp_path):
    (tmp_path / "tracked.py").write_text("x = 1\n")
    (tmp_path / "untracked.py").write_text("x = 2\n")
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.py"], check=True)
    assert _module()._paths(tmp_path) == [tmp_path / "tracked.py"]


def test_scanner_ignores_documented_force_and_finds_real_destructive_calls(tmp_path):
    source = tmp_path / "writes.py"
    source.write_text('''"""Use --force only as a documented option."""

from pathlib import Path
import os

def clear(path):
    Path(path).unlink()
    os.rmdir(path)
''')
    got = {(row["function"], row["rule"]) for row in _module().scan_paths(tmp_path, [source])}
    assert got == {("clear", "fs-remove")}


def test_scan_output_renders_and_check_rejects_bad_inventory(tmp_path):
    source = tmp_path / "a.py"
    source.write_text('import subprocess\nsubprocess.run(["git", "push"])\n')
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.py"], check=True)
    inventory = tmp_path / "inventory.json"
    scanned = subprocess.run([sys.executable, str(SCRIPT), "scan", str(tmp_path), "--json", str(inventory)],
                             capture_output=True, text=True)
    assert scanned.returncode == 0, scanned.stderr
    rendered = subprocess.run([sys.executable, str(SCRIPT), "render", str(inventory)],
                              capture_output=True, text=True)
    assert rendered.returncode == 0, rendered.stderr
    assert "| a.py | <module> | git-push | 1 | ungated | high |" in rendered.stdout
    data = json.loads(inventory.read_text())
    data["entries"][0].pop("risk")
    inventory.write_text(json.dumps(data))
    checked = subprocess.run([sys.executable, str(SCRIPT), "check", str(tmp_path), str(inventory)],
                             capture_output=True, text=True)
    assert checked.returncode == 1
    assert "missing risk" in checked.stdout


def test_committed_inventory_matches_the_tracked_write_surface():
    assert _module().ratchet(ROOT, ROOT / "docs" / "launch" / "write-surface.json") == []


def test_each_python_write_rule_has_a_positive_and_a_nonexecuting_lookalike(tmp_path):
    source = tmp_path / "writes.py"
    source.write_text('''"""gh label delete; git push; rm -rf are documentation only."""
import shutil
import os
import subprocess
from pathlib import Path

def writes(path):
    subprocess.run(["gh", "issue", "delete", "1"])
    subprocess.run(["gh", "pr", "merge", "1"])
    subprocess.run(["gh", "label", "delete", "x"])
    subprocess.run(["gh", "project", "item-delete", "x"])
    subprocess.run(["gh", "api", "-X", "POST", "x"])
    _graphql("mutation { x }")
    subprocess.run(["git", "push"])
    subprocess.run(["git", "reset", "--hard"])
    shutil.rmtree(path)
    os.unlink(path)
    Path(path).write_text("x")
''')
    got = {row["rule"] for row in _module().scan_paths(tmp_path, [source])}
    assert got == {"gh-issue", "gh-pr", "gh-label", "gh-project", "gh-api-write",
                   "graphql-mutation", "git-push", "git-destructive", "fs-rmtree",
                   "fs-remove", "fs-write"}


def test_shell_write_sites_include_label_delete_and_ignore_comments(tmp_path):
    source = tmp_path / "writes.sh"
    source.write_text('''#!/bin/sh
# gh label delete docs-only
printf '%s\\n' 'git push docs-only'
gh label delete legacy
git push origin topic
git reset --hard HEAD
rm -rf scratch
''')
    got = {row["rule"] for row in _module().scan_paths(tmp_path, [source])}
    assert got == {"gh-label", "git-push", "git-destructive", "fs-rmtree"}


def test_documented_shell_label_delete_control_fails_the_ratchet(tmp_path):
    source = tmp_path / "control.sh"
    source.write_text("gh label delete legacy\\n")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"entries": []}))
    assert _module().ratchet(tmp_path, inventory) == [
        "new write site control.sh:<script> gh-label -- add it to docs/launch/write-surface.json with its gate"
    ]

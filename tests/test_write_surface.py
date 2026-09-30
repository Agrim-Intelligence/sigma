"""#335 — write-surface scanner and inventory ratchet controls."""

import importlib.util
import json
from pathlib import Path


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

"""`.sdlc/` link-allowlist entries are moot only when no `.sdlc` file is tracked (#397, Task 1c).

A public export without `.sdlc/` would otherwise fail `tests/test_doc_links.py` on the two allowlist
entries that point into it. The test module is imported BY NAME (`tests/` is on `sys.path`).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _repo(root: Path, entries):
    (root / "docs").mkdir(parents=True)
    (root / "tests" / "fixtures").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    (root / "docs" / "a.md").write_text("# A\n", encoding="utf-8")
    (root / "tests" / "fixtures" / "doc_links_allowlist.json").write_text(json.dumps(entries), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(root), check=True)


def _entry(file, target, line=1):
    return {"file": file, "target": target, "line": line, "line_text_substring": "[x]",
            "reason": "synthetic fixture entry"}


def test_sdlc_allowlist_entries_are_moot_only_without_tracked_sdlc(tmp_path):
    import test_doc_links as dl

    # 1. no tracked .sdlc: an unused entry for a .sdlc file is moot
    first = tmp_path / "first"
    _repo(first, [_entry(".sdlc/plans/1.md", "x.md")])
    assert dl.findings(first) == []

    # 2. a tracked .sdlc file makes that same stale line reported again
    (first / ".sdlc" / "plans").mkdir(parents=True)
    (first / ".sdlc" / "plans" / "2.md").write_text("# Two\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", ".sdlc/plans/2.md"], cwd=str(first), check=True)
    assert dl.findings(first) == ["stale allowlist .sdlc/plans/1.md:1:x.md"]

    # 3. the moot rule covers .sdlc entries only: a stale entry for any other file stays stale
    second = tmp_path / "second"
    _repo(second, [_entry(".sdlc/plans/1.md", "x.md"), _entry("docs/gone.md", "y.md", 3)])
    assert dl.findings(second) == ["stale allowlist docs/gone.md:3:y.md"]

"""The two runbooks of #397: content, gestures, links and the pointer (Task 1c).

Every test opens with `assert DOC.exists()` for each doc it reads, so a missing doc is an
AssertionError red. The sibling test modules are imported BY NAME inside the body (`tests/` is on
`sys.path`), never loaded by path.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs" / "public-snapshot.md"
HANDOVER = ROOT / "docs" / "name-handover.md"
DEFINITION = ROOT / "docs" / "launch" / "definition.md"


def _tracked(path):
    out = subprocess.run(["git", "ls-files", "--", path.relative_to(ROOT).as_posix()], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=60)
    return out.stdout.strip() != ""


def _section(text, heading):
    lines = text.splitlines()
    assert heading in lines, "missing heading %r" % heading
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def test_public_snapshot_doc_states_the_contract():
    assert SNAPSHOT.exists(), "docs/public-snapshot.md is missing"
    assert _tracked(SNAPSHOT), "docs/public-snapshot.md is not tracked (git ls-files)"
    text = SNAPSHOT.read_text(encoding="utf-8")
    needed = ("python3 tools/build_public_tree.py", "sigma.public-tree-report/v1", "--sdlc exclude",
              "--sdlc include", "#433", "pr-merged", "owner-merged",
              "docs/launch/public-tree-dispositions.json", "docs/launch/exposure-allowlist.json",
              "python3 tools/verify_public_repo.py", "python3 tools/onboarding_control.py",
              "VERIFIED", "REJECTED", "NOT-VERIFIED", "never push a `.rejected` directory")
    missing = [token for token in needed if token not in text]
    assert not missing, missing
    lines = text.splitlines()
    for heading in ("## What the scans do not cover", "## Rehearsal"):
        assert heading in lines, heading


def test_name_handover_doc_states_the_contract():
    assert HANDOVER.exists(), "docs/name-handover.md is missing"
    assert _tracked(HANDOVER), "docs/name-handover.md is not tracked (git ls-files)"
    text = HANDOVER.read_text(encoding="utf-8")
    needed = ("python3 tools/handover_check.py check", "python3 tools/handover_check.py sequence",
              "gh repo rename", "#359", "watch.stop")
    missing = [token for token in needed if token not in text]
    assert not missing, missing
    lines = text.splitlines()
    for heading in ("## Control", "## Rehearsal"):
        assert heading in lines, heading
    control = _section(text, "## Control")
    gestures = [ln for ln in control.splitlines() if "tools/handover_check.py check" in ln]
    assert gestures, "the Control section holds no check gesture"
    assert any("--offline" in ln for ln in gestures), gestures


def test_new_docs_gestures_validate():
    assert SNAPSHOT.exists(), "docs/public-snapshot.md is missing"
    assert HANDOVER.exists(), "docs/name-handover.md is missing"
    import test_documented_gestures as g

    tracked_sources = g.sources()
    counts = {}
    failures = []
    for doc in (SNAPSHOT, HANDOVER):
        rel = doc.relative_to(ROOT)
        assert rel in tracked_sources, "%s is not among the documented-gesture sources" % rel
        found = g.extract(doc.read_text(encoding="utf-8"), rel)
        counts[rel.name] = len(found)
        for item in found:
            failures.extend(g.validate(item))
    assert not failures, "\n".join(failures)
    assert counts["public-snapshot.md"] >= 4, counts
    assert counts["name-handover.md"] >= 3, counts


def test_new_docs_carry_no_literal_tokens():
    assert SNAPSHOT.exists(), "docs/public-snapshot.md is missing"
    assert HANDOVER.exists(), "docs/name-handover.md is missing"
    placeholder = "<" + "OWNER:"
    ssh_form = "git" + "@" + "github.com:"
    key_header = re.compile("-" * 5 + r"BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?" + "-" * 5 + "|"
                            + "-" * 4 + r" BEGIN SSH2 ENCRYPTED PRIVATE KEY " + "-" * 4 + "|"
                            + r"PuTTY-User-Key-File-\d+:")
    for doc in (SNAPSHOT, HANDOVER):
        text = doc.read_text(encoding="utf-8")
        assert len(text) > 1000, "%s is too short to be the runbook" % doc.name
        assert placeholder not in text, doc.name
        assert ssh_form not in text, doc.name
        assert not [ln for ln in text.splitlines() if key_header.search(ln)], doc.name


def test_definition_points_at_both_docs():
    assert SNAPSHOT.exists(), "docs/public-snapshot.md is missing"
    assert HANDOVER.exists(), "docs/name-handover.md is missing"
    assert DEFINITION.exists(), "docs/launch/definition.md is missing"
    lines = DEFINITION.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("- **Public repository:**"))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("- ") or not lines[i].strip()),
               len(lines))
    bullet = "\n".join(lines[start:end])
    assert "`Agrim-Intelligence/sigma`" in bullet
    assert "../public-snapshot.md" in bullet, bullet
    assert "../name-handover.md" in bullet, bullet


def test_new_docs_links_resolve():
    assert SNAPSHOT.exists(), "docs/public-snapshot.md is missing"
    assert HANDOVER.exists(), "docs/name-handover.md is missing"
    import test_doc_links as dl

    problems = dl.findings(ROOT)
    mine = [p for p in problems
            if p.startswith("docs/public-snapshot.md") or p.startswith("docs/name-handover.md")
            or p.startswith("docs/launch/definition.md")]
    assert mine == [], mine

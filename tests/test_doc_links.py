"""Relative Markdown links resolve from their own document (#340)."""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)|^\s*\[[^]]+\]:\s*(\S+)")


def slug(value: str, seen: dict[str, int] | None = None) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", value).lower()
    text = "".join(c for c in text if c.isalnum() or c in " -_").replace(" ", "-")
    if seen is None: return text
    count = seen.get(text, 0); seen[text] = count + 1
    return text if count == 0 else f"{text}-{count}"


def anchors(path: Path) -> set[str]:
    seen, out = {}, set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match: out.add(slug(match.group(1), seen))
    return out


def findings(root: Path) -> list[str]:
    allow = json.loads((root / "tests/fixtures/doc_links_allowlist.json").read_text())
    used, problems = set(), []
    files = [Path(p) for p in subprocess.check_output(["git", "ls-files", "*.md"], cwd=root, text=True).splitlines() if not p.startswith("tests/fixtures/")]
    for rel in files:
        source = root / rel
        for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            for match in LINK.findall(line):
                raw = next(value for value in match if value)
                if raw.startswith(("http://", "https://", "mailto:")): continue
                target = raw.split("#", 1)[0] or rel.name
                entry = next((i for i, x in enumerate(allow) if x["file"] == str(rel) and x["target"] == raw and x["line_text_substring"] in line), None)
                if entry is not None: used.add(entry); continue
                destination = (source.parent / target).resolve()
                if not destination.exists(): problems.append(f"{rel}:{line_no}: missing link {raw}"); continue
                if "#" in raw and raw.split("#", 1)[1] not in anchors(destination): problems.append(f"{rel}:{line_no}: missing anchor {raw}")
    problems += [f"stale allowlist {allow[i]['file']}:{allow[i]['target']}" for i in range(len(allow)) if i not in used]
    return problems


def test_slug_handles_code_punctuation_emoji_and_duplicates():
    seen = {}
    assert slug("Hello, `World`! 🔥", seen) == "hello-world-"
    assert slug("Hello, `World`! 🔥", seen) == "hello-world--1"


def test_relative_path_and_anchor_controls(tmp_path):
    (tmp_path / "docs").mkdir(); (tmp_path / "tests/fixtures").mkdir(parents=True)
    (tmp_path / "tests/fixtures/doc_links_allowlist.json").write_text("[]")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "docs/a.md").write_text("[bad](b.md#missing)\n[root fallback](../b.md)\n")
    (tmp_path / "docs/b.md").write_text("# Present\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    errors = findings(tmp_path)
    assert "docs/a.md:1: missing anchor b.md#missing" in errors
    assert "docs/a.md:2: missing link ../b.md" in errors


def test_all_tracked_markdown_links_resolve():
    assert not findings(ROOT), "\n".join(findings(ROOT))

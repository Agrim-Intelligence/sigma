"""No shipped prose tells an agent to set `followups.mode: file` for decompose (#2224 amendment A5).

Run: python3 -m pytest tests/test_no_decompose_file_lever_prose.py
(generic form: python -m pytest tests/test_no_decompose_file_lever_prose.py)

`followups.mode: file` is a repo-wide switch that turns follow-up routing off for EVERY goal. A decompose child is
exempt by proof instead (`followup_route.decompose_decision`), so the setting is never a decompose step. The scan reads
`skills/`, `docs/`, `hooks/`, `references/` and `agents/` (CHANGELOG and `docs/whats-new-*` are history and excluded)
and fails any paragraph that names the setting AND decompose/child. The one allowed phrasing is a human opt-out
paragraph that starts exactly `To opt out (humans only): set` and mentions neither decompose, children nor agents.
CONTROLS: C55 test_no_paragraph_pairs_the_file_lever_with_decompose (re-add the sentence to one file under skills/ and to
one under docs/: red each time), C55b test_the_scan_sees_a_planted_sentence (the matcher itself).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIRS = ("skills", "docs", "hooks", "references", "agents")
SUFFIXES = {".md", ".py", ".sh", ".tmpl", ".mdc", ".json", ".txt"}
LEVER = re.compile(r"followups\W{0,3}[^\n]{0,40}?mode\W{0,3}\s*:\s*\W{0,3}file\b")   # not goal_decompose's own `mode: file`
DECOMPOSE = re.compile(r"decompos|\bchild(?:ren)?\b|\bagents?\b", re.I)
ALLOWED_START = "To opt out (humans only): set"


def paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def offending(text):
    """The paragraphs that name the lever together with decompose/child/agents, minus the one allowed opt-out."""
    bad = []
    for para in paragraphs(text):
        flat = " ".join(para.split())
        if not LEVER.search(flat) or not DECOMPOSE.search(flat):
            continue
        if flat.startswith(ALLOWED_START):
            continue
        bad.append(flat[:160])
    return bad


def scanned():
    for d in DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if (path.is_file() and path.suffix in SUFFIXES and "__pycache__" not in path.parts
                    and not path.name.startswith("whats-new-") and path.name != "CHANGELOG.md"):
                yield path


def test_no_paragraph_pairs_the_file_lever_with_decompose():
    found = {}
    for path in scanned():
        try:
            bad = offending(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
        if bad:
            found[str(path.relative_to(ROOT))] = bad
    assert not found, found


def test_the_scan_sees_a_planted_sentence():
    assert offending("Before creating any child, set `followups.mode: file` in config.")
    assert offending('decompose goals need {"followups": {"mode": "file"}} first.')
    assert offending('decompose goals need goal_decompose mode: file first.') == []
    assert offending("To opt out (humans only): set `followups.mode: file`; agents must not.") == []
    assert offending("To opt out (humans only): set `followups.mode: file`.") == []
    assert offending("Nothing here names the setting for a child.") == []


def test_the_allowed_opt_out_exists_once_in_the_configuration_docs():
    text = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^To opt out \(humans only\): set `followups\.mode: file`", text, re.M)) == 1

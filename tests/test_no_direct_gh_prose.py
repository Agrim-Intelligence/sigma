"""#895 slice 6: a ratchet on direct `gh issue|pr|project|label <verb>` in shipped SKILL PROSE.

tests/test_no_direct_gh.py counts list-literal call sites in Python. This is its sibling for the
agent instructions under skills/**/*.md: the loop-path reads and writes were rewritten to
`gh api repos/{owner}/{repo}/...` (REST, usable in a Claude Code cloud session, where GraphQL is
unavailable), and this keeps them there. It says nothing about whether a cloud session works end to
end: that was NOT run (see docs/cloud-sessions.md, UNMEASURED).

DOCUMENTED INVOCATION (the controls were run on exactly this string):
    $HOME/.sigma-venv312/bin/python -m pytest tests/test_no_direct_gh_prose.py
generic form: python -m pytest tests/test_no_direct_gh_prose.py

Scan: every skills/**/*.md, SKILL.md frontmatter and `allowed-tools:` lines skipped. Fenced lines are
read one by one; every other paragraph (consecutive non-blank lines) is JOINED before inline code spans
are matched, so a span wrapped across two lines is seen (tests/test_documented_gh_verbs.py misses
those). A span with `gh <issue|pr|project|label> <verb>` at command position is an offender unless an
ALLOWED entry (path, substring, reason) covers it. Every command in a span is its own hit. An entry
looks only at WINDOW characters either side of its hit, and must match EXACTLY ONE hit in its file
(test_no_stale_allowlist_entry): 0 is stale, 2+ means a neighbouring command sits inside its window
and would be hidden, so it fails. ```` ``` ```` and `~~~` fences are both scanned. KNOWN GAP (pinned by
test_gap_indented_code_block_is_not_scanned): a 4-space indented code block with no backticks is
prose to this scanner and is not seen. Grants such as
`Bash(gh issue *)` have no verb and are not offenders. Not covered: `.tmpl`, shell scripts, prose that
does not put the command in code, and review_context.py (code; its briefs still say `gh pr diff` /
`gh issue view`, tracked in docs/cloud-sessions.md).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_documented_gestures import ROOT  # noqa: E402
from test_documented_gh_verbs import CHECK_NAMES, GH  # noqa: E402

INVOCATION = "$HOME/.sigma-venv312/bin/python -m pytest tests/test_no_direct_gh_prose.py"
GENERIC = "python -m pytest tests/test_no_direct_gh_prose.py"
NOUNS = {"issue", "pr", "project", "label"}
GRANT = "Bash(gh api *)"
WINDOW = 40

ALLOWED: dict[str, list[tuple[str, str]]] = {
    "skills/sigma-define/SKILL.md": [("fails the *whole create*", "explains why create --label fails")],
    "skills/sigma-goal-design/SKILL.md": [("advertise a read-only posture", "grant rationale")],
    "skills/sigma-goal-review/SKILL.md": [
        ("dir` for step 4b's scr", "grant listing"),
        ("`gh issue comment` for the outcome comments", "grant listing"),
        ("`gh label list` for step 5e", "grant listing"),
        ("check `gh issue view <n> --json comments`", "agent read in a pipeline skill, not on the loop path; its grants are test-pinned; migrate with the pipeline skills"),
    ],
    "skills/sigma-loop/SKILL.md": [("directly.** Use `handoff.py track`", "the never-gh-issue-create rule")],
    "skills/sigma-loop/references/filing.md": [
        ("instead of `gh issue create`", "the never-gh-issue-create rule"),
        ("Never call `gh issue create`", "the never-gh-issue-create rule"),
    ],
    "skills/sigma-loop/references/landing.md": [
        ("one `gh pr merge` fallback", "describes the REST path and its fallback"),
        ("There is no `gh pr view`", "describes the REST path"),
        ("one `gh issue view` fallback", "describes the REST path and its fallback"),
    ],
    "skills/sigma-loop/SLACK_COMMANDS.md": [("run `/sigma-rebase` locally or", "human-run `gh pr merge`")],
    "skills/sigma-triage/SKILL.md": [
        ("handling. Read-only calls (`gh issue view`", "contrast with the banned mutation loops"),
        ("`gh issue list`, and similar) are unaffected", "contrast with the banned mutation loops"),
        ("for n in 101 102 103", "bad example of the banned loop"),
        ("gh issue edit 101", "good example line"),
        ("gh issue edit 102", "good example line"),
        ("gh issue edit 103", "good example line"),
    ],
    "skills/sigma-rebase/SKILL.md": [("plain `gh pr merge`", "human-attended merge")],
    "skills/sigma-goal-review/references/feature-ification.md": [("gh label list", "one-off precondition read")],
    "skills/sigma-setup/references/public-repo.md": [
        ("gh label create", "human-run operator doc"),
        ("gh issue edit <N>", "human-run operator doc"),
        ("gh issue create --repo", "human-run operator doc"),
        ("gh project create", "human-run operator doc (Projects needs GraphQL)"),
        ("gh project field-create", "human-run operator doc (Projects needs GraphQL)"),
    ],
    "skills/sigma-init/references/board.md": [
        ("`gh project copy`", "human-run operator doc (Projects needs GraphQL)"),
        ("one `gh project view`", "human-run operator doc (Projects needs GraphQL)"),
    ],
}
# skills whose gh api prose is covered by the CALLING skill's grant
NO_GRANT_OK = {"sigma-model": "calling skill grants"}


def _lines(path: Path):
    """(line_no, text) for the body: frontmatter and allowed-tools lines dropped."""
    out = path.read_text(encoding="utf-8").splitlines()
    skip = 0
    if out and out[0].strip() == "---":
        skip = next((i + 1 for i in range(1, len(out)) if out[i].strip() == "---"), 0)
    return [(n, t) for n, t in enumerate(out, 1) if n > skip and not t.startswith("allowed-tools:")]


def _blocks(path: Path):
    """Yield (start_line, text, fenced). Fenced lines singly; other paragraphs joined by spaces."""
    para: list[tuple[int, str]] = []
    fence = ""

    def flush():
        if para:
            yield para[0][0], " ".join(t.strip() for _, t in para), False, [n for n, _ in para], [t.strip() for _, t in para]
            para.clear()

    for n, t in _lines(path):
        mark = t.lstrip()[:3]
        if mark in ("```", "~~~") and (not fence or mark == fence):
            yield from flush()
            fence = "" if fence else mark
        elif fence:
            yield n, t.strip(), True, [n], [t.strip()]
        elif t.strip():
            para.append((n, t))
        else:
            yield from flush()
    yield from flush()


def hits(root: Path = ROOT):
    """All `gh <noun> [verb]` command-position spans: (rel, line, noun, verb, snippet, span +- WINDOW chars)."""
    found = []
    for path in sorted((root / "skills").rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        for start, text, fenced, nums, parts in _blocks(path):
            spans = [(text, 0)] if fenced else [(m.group(1), m.start(1)) for m in re.finditer(r"`([^`]+)`", text)]
            for snippet, off in spans:
                off += len(snippet) - len(snippet.lstrip("$ "))
                snippet = snippet.lstrip("$ ")
                if snippet in CHECK_NAMES:
                    continue
                for m in GH.finditer(snippet):  # every command in the span, not just the first
                    line, pos = start, 0
                    for num, part in zip(nums, parts):  # start line of the match, even when wrapped
                        if off + m.start() < pos + len(part) + 1:
                            line = num
                            break
                        pos += len(part) + 1
                    lo = max(0, off + m.start() - WINDOW)
                    window = text[lo: off + m.end() + WINDOW]
                    found.append((rel, line, m.group("noun"), m.group("verb"), snippet, window))
    return found


def offenders(root: Path = ROOT):
    return [h for h in hits(root) if h[2] in NOUNS and h[3]]


def _covers(entry, h):
    return entry in h[5]


def _covered(h):
    return [s for s, _ in ALLOWED.get(h[0], []) if _covers(s, h)]


def _fail(items):
    return "\n".join(f"{r}:{l}: {s}" for r, l, _, _, s, *_ in items) + f"\nrun: {INVOCATION}"


def test_documented_invocation_is_consistent():
    doc = (ROOT / "docs" / "cloud-sessions.md").read_text(encoding="utf-8")
    assert INVOCATION in doc and GENERIC in doc


def test_no_bare_gh_in_skill_prose():
    bad = [h for h in offenders() if not _covered(h)]
    assert not bad, "direct `gh <noun> <verb>` in skill prose; use `gh api repos/{owner}/{repo}/...`:\n" + _fail(bad)


def test_no_stale_allowlist_entry():
    flagged = offenders()
    count = {(rel, s): sum(h[0] == rel and _covers(s, h) for h in flagged) for rel, es in ALLOWED.items() for s, _ in es}
    stale = [k for k, n in count.items() if n == 0]
    assert not stale, f"ALLOWED entries matching no flagged line (remove them): {stale}\nrun: {INVOCATION}"
    wide = [k for k, n in count.items() if n > 1]
    assert not wide, f"ALLOWED entries matching 2+ flagged hits (would hide a neighbour; tighten): {wide}\nrun: {INVOCATION}"
    dup = [k for k, n in Counter((r, s) for r, es in ALLOWED.items() for s, _ in es).items() if n > 1]
    assert not dup, f"duplicate ALLOWED substrings: {dup}"


def _grant_problems(root: Path = ROOT):
    skills = {h[0].split("/")[1] for h in hits(root) if h[2] == "api"} - set(NO_GRANT_OK)
    bad = []
    for name in sorted(skills):
        for rel in (f"skills/{name}/SKILL.md", f"skills/{name}/references/selection.md"):
            p = root / rel
            line = next((t for t in p.read_text(encoding="utf-8").splitlines() if t.startswith("allowed-tools:")), "") if p.exists() else ""
            if GRANT not in line:
                bad.append(f"{rel}: allowed-tools lacks {GRANT}")
    return bad


def test_gh_api_prose_has_grant():
    bad = _grant_problems()
    assert not bad, "\n".join(bad) + f"\nrun: {INVOCATION}"


# ---- controls: prove the detector on a tmp tree, not the real one ----
def _tree(tmp_path, body, grant=GRANT):
    d = tmp_path / "skills" / "sigma-x" / "references"
    d.mkdir(parents=True)
    fm = f"---\nname: x\nallowed-tools: Bash(python3 *), {grant}\n---\n"
    (tmp_path / "skills" / "sigma-x" / "SKILL.md").write_text(fm + body, encoding="utf-8")
    (d / "selection.md").write_text(f"allowed-tools: Bash(python3 *), {grant}\n", encoding="utf-8")
    return tmp_path


def test_control_detector_sees_inline_fenced_and_wrapped(tmp_path):
    root = _tree(tmp_path, "run `gh issue view 1` now\n\n```\ngh pr diff 2\n```\n\nand `gh\nissue edit 3` wrapped\n")
    assert [(h[1], h[3]) for h in offenders(root)] == [(5, "view"), (8, "diff"), (11, "edit")]


def test_control_frontmatter_and_grants_not_flagged(tmp_path):
    root = _tree(tmp_path, "see `Bash(gh issue *)` and `gh auth`\n", grant="Bash(gh issue view *)")
    assert offenders(root) == []


def test_control_missing_grant_is_reported(tmp_path):
    root = _tree(tmp_path, "read `gh api repos/{owner}/{repo}/issues/1`\n", grant="Bash(gh issue *)")
    assert len(_grant_problems(root)) == 2
    assert _grant_problems(_tree(tmp_path / "ok", "read `gh api repos/{owner}/{repo}/issues/1`\n")) == []


def test_control_allowlist_covers_only_its_span(tmp_path):
    body = "Say `gh pr merge` is human-run. " + "word " * 40 + "Read with `gh pr view 7 --json x`.\n"
    root = _tree(tmp_path, body)
    assert len(offenders(root)) == 2
    assert [h[4] for h in offenders(root) if "human-run" in h[5]] == ["gh pr merge"]  # only the span's window


def test_control_tilde_fence_is_scanned(tmp_path):
    root = _tree(tmp_path, "~~~\ngh pr view 2\n~~~\n")
    assert [h[3] for h in offenders(root)] == ["view"]


def test_gap_indented_code_block_is_not_scanned(tmp_path):
    root = _tree(tmp_path, "text\n\n    gh pr view 2\n")
    assert offenders(root) == []  # documented gap; delete this test when indented blocks are scanned


def test_control_second_command_in_one_span_or_line_is_flagged(tmp_path):
    root = _tree(tmp_path, "run `gh api repos/{owner}/{repo}/issues/1 && gh issue view 1` now\n\n```\ngh api x; gh issue edit 5 --add-label x\n```\n")
    assert [(h[2], h[3]) for h in offenders(root)] == [("issue", "view"), ("issue", "edit")]


def test_control_adjacent_command_makes_allowlist_entry_ambiguous(tmp_path, monkeypatch):
    body = "Run `gh pr view 9 --json x`. There is no `gh pr view` read here.\n"
    root = _tree(tmp_path, body)
    monkeypatch.setattr(sys.modules[__name__], "ALLOWED", {"skills/sigma-x/SKILL.md": [("There is no `gh pr view`", "r")]})
    n = sum(_covers("There is no `gh pr view`", h) for h in offenders(root))
    assert n == 2  # the entry sits in both windows; test_no_stale_allowlist_entry fails on n > 1

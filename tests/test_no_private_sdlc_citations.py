"""Shipped files never cite a `.sdlc/design|plans|research/<name>` file the public tree does not track (#173).

A public reader who follows `.sdlc/design/2253.md` out of a shipped doc or comment lands on
nothing: those write-ups are not in the public tree. The structural leak scan skips `.sdlc/` by
design, so it cannot see the pointer; this test asks git instead. A citation is allowed only when
the cited path is itself tracked (e.g. `.sdlc/plans/258.md`). Placeholders (`<n>`, `*`) name a
shape, not a file, and are not citations.

Scope: every tracked file except `tests/` (fixtures name paths freely), generated launch evidence
and the golden contract fixture (machine output about those very paths), and the worked example in
the dossier walkthrough (an adopter's own run produces `.sdlc/design/1.md`; see ALLOWED).
"""
import pathlib
import re
import subprocess

import pytest

import public_surface

ROOT = pathlib.Path(__file__).resolve().parent.parent

CITATION = re.compile(r"\.sdlc[/\\](?:design|plans|research)[/\\][A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*\.(?:md|json|py)\b",
                      re.IGNORECASE)

SKIP_PREFIXES = ("tests/", "docs/launch/", "contract/golden/", ".sdlc/")

#: file -> cited paths that are illustrative output of an adopter's own run, not pointers.
ALLOWED = {"docs/how-the-dossier-pipeline-works.md": {".sdlc/design/1.md"},
           # owned by #429; follow-up #436 rewords it
           "docs/output-contract.md": {".sdlc/research/1626.md"}}


def _tracked(root):
    out = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"], text=True)
    return set(filter(None, out.split("\0")))


def untracked_citations(root, files=None):
    root = pathlib.Path(root)
    tracked = _tracked(root)
    hits = []
    for rel in files if files is not None else public_surface.public_files(root):
        if rel.startswith(SKIP_PREFIXES):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in CITATION.finditer(line):
                cited = m.group(0).rstrip(".").replace("\\", "/")
                if cited in tracked or cited in ALLOWED.get(rel, ()):
                    continue
                hits.append(f"{rel}:{n}: {cited}")
    return hits


def test_no_shipped_file_cites_an_untracked_sdlc_design_or_plan():
    hits = untracked_citations(ROOT)
    if hits:  # a one-line AssertionError, so the planned-test red proof can attribute it
        raise AssertionError(f"{len(hits)} citations of untracked .sdlc files, first: {hits[0]}")


def _plant(tmp_path, files):
    subprocess.check_call(["git", "init", "-q", str(tmp_path)])
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    subprocess.check_call(["git", "-C", str(tmp_path), "add", "-A"])


@pytest.mark.parametrize("cite", [".sdlc/design/2253.md", ".sdlc/plans/2260-live-judge.md",
                                  ".sdlc/research/2531.md", ".sdlc/research/9-x.py"])
def test_control_a_new_citation_of_an_untracked_file_fails(tmp_path, cite):
    _plant(tmp_path, {"docs/a.md": f"See `{cite}` for why.\n"})
    assert untracked_citations(tmp_path) == [f"docs/a.md:1: {cite}"]


def test_control_tracked_targets_and_placeholders_pass(tmp_path):
    _plant(tmp_path, {".sdlc/plans/7.md": "x\n",
                      "docs/a.md": "`.sdlc/plans/7.md`, `.sdlc/design/<n>.md`, `.sdlc/plans/*.md`\n"})
    assert untracked_citations(tmp_path) == []


@pytest.mark.parametrize("written,norm", [(".SDLC/Design/2253.MD", ".SDLC/Design/2253.MD"),
                                          (".sdlc\\design\\2253.md", ".sdlc/design/2253.md")])
def test_control_case_and_backslash_variants_fail(tmp_path, written, norm):
    _plant(tmp_path, {"docs/a.md": f"See {written}.\n"})
    assert untracked_citations(tmp_path) == [f"docs/a.md:1: {norm}"]

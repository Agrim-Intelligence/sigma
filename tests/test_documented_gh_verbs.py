"""Every `gh <noun> <verb>` in shipped documentation names a real gh subcommand (D2-b, #307).

tests/test_documented_gestures.py resolves Python gestures only. A documented `gh issue vieww` would
have shipped green. This check is deterministic and needs no gh binary: the table below was taken from
`gh <noun> --help` of gh 2.98.0 (2026-08-20), and covers the command groups the docs use.
Only code (fences and inline spans) at COMMAND POSITION is read, so prose such as "gh was asked" cannot hit.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_documented_gestures import ROOT, sources  # noqa: E402

GH_VERSION = "2.98.0"
TABLE = {
    "api": set(),
    "auth": {"login", "logout", "refresh", "setup-git", "status", "switch", "token"},
    "config": {"clear-cache", "get", "list", "set"},
    "issue": {"create", "list", "status", "close", "comment", "delete", "develop", "edit", "lock", "pin",
              "reopen", "transfer", "unlock", "unpin", "view"},
    "label": {"clone", "create", "delete", "edit", "list"},
    "pr": {"create", "list", "status", "checkout", "checks", "close", "comment", "diff", "edit", "lock", "merge",
           "ready", "reopen", "revert", "review", "unlock", "update-branch", "view"},
    "project": {"close", "copy", "create", "delete", "edit", "field-create", "field-delete", "field-list",
                "item-add", "item-archive", "item-create", "item-delete", "item-edit", "item-list", "link",
                "list", "mark-template", "unlink", "view"},
    "release": {"create", "list", "delete", "delete-asset", "download", "edit", "upload", "verify",
                "verify-asset", "view"},
    "repo": {"create", "list", "archive", "autolink", "clone", "delete", "deploy-key", "edit", "fork", "gitignore",
             "license", "read-dir", "read-file", "rename", "set-default", "sync", "unarchive", "view"},
    "run": {"cancel", "delete", "download", "list", "rerun", "view", "watch"},
    "workflow": {"disable", "enable", "list", "run", "view"},
}
# doctor check NAMES written as `gh ...` in code spans; they are not commands
CHECK_NAMES = {"gh installed", "gh auth", "gh token scopes", "gh project scope"}
# command position: start of the snippet, or after a shell separator / keyword / VAR=x prefix
GH = re.compile(
    r"(?:^|[$(|;&]|\b(?:then|do|if|else)\b|!)\s*(?:[A-Za-z_]+=\S+\s+)*gh\s+"
    r"(?:(?:-R|--repo)\s+\S+\s+)?(?P<noun>[a-z][a-z-]*)(?:\s+(?P<verb>[a-z][a-z-]*))?"
)


def snippets(text: str):
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        for snippet in ([line.strip()] if in_fence else re.findall(r"`([^`\n]+)`", line)):
            yield line_no, snippet.lstrip("$ ")


def check(text: str, source: Path = Path("docs/example.md")) -> list[str]:
    bad = []
    for line_no, snippet in snippets(text):
        if snippet in CHECK_NAMES:
            continue
        for match in GH.finditer(snippet):
            noun, verb = match.group("noun"), match.group("verb")
            where = f"{source}:{line_no}"
            if noun not in TABLE:
                bad.append(f"{where}: unknown gh command group {noun}")
            elif TABLE[noun] and verb and verb not in TABLE[noun]:
                bad.append(f"{where}: unknown gh {noun} subcommand {verb}")
    return bad


def test_planted_bad_gh_verb_is_detected():
    assert check("`gh issue vieww 12`") == ["docs/example.md:1: unknown gh issue subcommand vieww"]
    assert check("```sh\ngh isue view 12\n```") == ["docs/example.md:2: unknown gh command group isue"]
    assert check("`x=$(gh pr lst --json number)`") == ["docs/example.md:1: unknown gh pr subcommand lst"]
    assert check("gh was asked, and `gh issue view 1 --json title`") == []


def test_documented_gh_verbs_resolve():
    failures, seen = [], 0
    for source in sources():
        text = (ROOT / source).read_text(encoding="utf-8")
        failures += check(text, source)
        seen += sum(1 for _, s in snippets(text) if s not in CHECK_NAMES for _ in GH.finditer(s))
    assert seen >= 120, seen  # 164 measured; a floor so a regex regression cannot silently empty the corpus
    assert not failures, "\n".join(failures)

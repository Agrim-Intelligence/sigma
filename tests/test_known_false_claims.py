"""Known launch-readiness documentation claims stay evidence-backed (#340)."""
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_does_not_claim_unenforced_coverage_floor():
    readme = (ROOT / "README.md").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "85% coverage floor" not in readme or "--cov-fail-under" in workflow


def test_readme_hook_count_matches_registry():
    readme = (ROOT / "README.md").read_text()
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
    count = sum(len(group["hooks"]) for groups in hooks.values() for group in groups)
    assert f"registers {count} hook commands" in readme


def test_readme_has_no_predecessor_version_claim():
    assert "1.0.9" not in (ROOT / "README.md").read_text()


def test_branching_model_names_human_merge_exception():
    text = (ROOT / "docs/branching-model.md").read_text()
    assert "never merges a feature branch anywhere" not in text or "verify_merge.py" in text


def test_channel_webhook_documents_its_enforced_remote_opt_in():
    text = (ROOT / "skills/agrim-loop/scripts/channel_notify.py").read_text()
    assert "127.0.0.1-only" not in text
    assert "allow_remote_webhook" in text


def test_changelog_versions_are_dated():
    headings = re.findall(r"^## \[?([0-9]+\.[0-9]+\.[0-9]+)\]?\s*(.*)$", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert headings and all(re.search(r"\b\d{4}-\d{2}-\d{2}\b", suffix) for _version, suffix in headings)




def _prose_files(*globs):
    out = []
    for pattern in globs:
        out += sorted(ROOT.glob(pattern))
    return out


def _sentences(text):
    """Whitespace-normalised sentences; a version like `3.12` never ends one."""
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        for s in re.split(r"(?<=[a-z0-9)`*])\.\s+(?=[A-Z*`(])|;\s+", para):
            yield s


def _ci_matrix():
    """{os family: set of python versions} parsed from ci.yml's `include:` cells."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    cells = re.findall(r'-\s*os:\s*(\w+)-latest\s*\n\s*python:\s*"(3\.\d+)"', workflow)
    out = {}
    for os_name, version in cells:
        out.setdefault(os_name, set()).add(version)
    assert out.get("ubuntu") and out.get("macos"), out
    return out


def _ci_version_sentences():
    """(file, sentence) for every sentence in README/docs that says CI runs on some Python versions."""
    for path in _prose_files("README.md", "docs/**/*.md"):
        for s in _sentences(path.read_text()):
            if re.search(r"\bCI\b\W{0,3}(?:\(GitHub Actions\)\s*)?runs?\b|\bruns? in CI\b", s) and re.search(r"\b3\.\d{1,2}\b", s) \
                    and re.search(r"Python|Linux|macOS", s) and "Windows" not in s \
                    and not re.search(r"\bno CI\b|have no|not covered|nothing checks", s):
                yield path.relative_to(ROOT), s


def _attributed_versions(sentence):
    """Versions per OS word: each version belongs to the nearest PRECEDING `Linux`/`macOS`."""
    found, current = {}, "any"
    for tok in re.finditer(r"Linux|macOS|\b3\.\d{1,2}\b", sentence):
        if tok.group(0) == "Linux":
            current = "ubuntu"
        elif tok.group(0) == "macOS":
            current = "macos"
        else:
            found.setdefault(current, set()).add(tok.group(0))
    return found


def test_every_ci_python_claim_equals_the_ci_matrix():
    """#434: the README's CI line said 3.10 + 3.12 while ci.yml runs four Linux versions + macOS 3.12.
    Derived from ci.yml, checked on EVERY README/docs sentence that mentions CI and Python versions."""
    matrix = _ci_matrix()
    union = set().union(*matrix.values())
    seen = list(_ci_version_sentences())
    assert any("README.md" == str(f) for f, _ in seen), "README no longer states its CI Python versions"
    for path, sentence in seen:
        for os_name, versions in _attributed_versions(sentence).items():
            expected = union if os_name == "any" else matrix[os_name]
            assert versions == expected, f"{path}: {os_name} {sorted(versions)} != ci.yml {sorted(expected)}: {sentence}"


_NEG = re.compile(r"\bnot\b|\bnever\b|\bno\b|n't|without merg|unless", re.I)
_UNMERGED_PR = re.compile(r"\b(?:closed|abandoned|unmerged|rejected|declined)\b(?:(?!merged\b).){0,50}"
                          r"\b(?:pull requests?|PRs?)\b|\b(?:pull requests?|PRs?)\b[^.]{0,30}"
                          r"\b(?:closed|abandoned|unmerged|rejected|declined)\b", re.I)
_RESOLVES = re.compile(r"resolv|unblock|releas|lift|clear|resum|drops? `?sdlc:blocked|blocker", re.I)


def test_no_doc_says_an_unmerged_pull_request_resolves_a_blocker():
    """#434: a PR closed WITHOUT merging does not resolve a blocker (blocker_scan.closed_state)."""
    import sys
    sys.path.insert(0, str(ROOT / "skills/agrim-loop/scripts"))
    import blocker_scan
    assert blocker_scan.closed_state("CLOSED", "") is False
    assert blocker_scan.closed_state("MERGED", "") is True
    assert blocker_scan.closed_state("CLOSED", "COMPLETED") is True
    for path in _prose_files("README.md", "docs/**/*.md", "skills/**/SKILL.md"):
        for s in _sentences(path.read_text()):
            if _UNMERGED_PR.search(s) and _RESOLVES.search(s) and not _NEG.search(s):
                raise AssertionError(f"{path.relative_to(ROOT)}: says an unmerged PR resolves a blocker: {s}")
    readme = " ".join((ROOT / "README.md").read_text().split())
    sweep = readme[readme.index("`discovery.auto_unpark.mode` is"):][:1800]
    assert re.search(r"merged (?:pull request|PR)", sweep)
    assert re.search(r"(?:closed|abandoned)\b.{0,40}without merging.{0,40}\b(?:not|never)\b", sweep)


def test_readme_managed_settings_says_it_is_advisory():
    text = " ".join((ROOT / "README.md").read_text().split())
    section = text[text.index("## Managed settings"):]
    section = section[:section.index(" ## ", 5)]
    assert re.search(r"advisory[^.]{0,40}\bwrite access\b", section)
    assert re.search(r"delet\w*[^.]{0,30}falls? back to (?:the )?local config", section)
    banned = r"not advisory|tamper|\benforced\b|cannot be (?:changed|edited|bypassed|deleted|overridden|removed)"
    assert not re.search(banned + r"|can(?:not|'t) (?:change|edit|bypass|delete|remove|override)",
                         section, re.I), "managed-settings section contradicts 'advisory'"

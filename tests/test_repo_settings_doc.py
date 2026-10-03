"""#398: docs/launch/repo-settings.md agrees with CI and with the verifier.

The CI check names are derived by PARSING `.github/workflows/ci.yml` with the standard library (CI
installs PyYAML only after pytest has run). Controls: delete a name from the document's list, rename
a CI matrix entry, or change a command in the verifier, and the matching test goes red.
"""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "launch" / "repo-settings.md"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CODEOWNERS = ROOT / ".github" / "CODEOWNERS"
SCRIPT = ROOT / "tools" / "readiness" / "verify_repo_settings.py"
BLOCK = re.compile(r"<!-- required-checks:begin -->\n(.*?)\n<!-- required-checks:end -->", re.S)


def _read(path):
    assert path.exists(), "%s does not exist" % path.name
    return path.read_text()


def _verifier():
    assert SCRIPT.exists(), "tools/readiness/verify_repo_settings.py does not exist"
    spec = importlib.util.spec_from_file_location("verify_repo_settings", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ci_check_names(text):
    """`test (<os>, <python>)` for every matrix `include` entry of job `test`; loud on any surprise."""
    job = re.search(r"^  test:\n(.*?)(?=^  \S|\Z)", text, re.S | re.M)
    assert job, "ci.yml has no job `test`; the check names would change"
    body = job.group(1)
    assert not re.search(r"^    name:", body, re.M), "job `test` has a `name:`; GitHub would report that instead"
    pairs = re.findall(r'- os:\s*(\S+)\s*\n\s*python:\s*"([\d.]+)"', body)
    assert len(pairs) == len(re.findall(r"- os:", body)) == 5, "unreadable or changed CI matrix: %r" % pairs
    return {"test (%s, %s)" % pair for pair in pairs}


def doc_check_names(text):
    block = BLOCK.search(text)
    assert block, "the document has no required-checks block"
    names = re.findall(r"^- `([^`]+)`$", block.group(1), re.M)
    assert names and len(names) == len(block.group(1).splitlines()), "unreadable required-checks list"
    return names


def test_required_check_names_in_the_doc_are_ci_check_names():
    ci = ci_check_names(_read(WORKFLOW))
    doc = doc_check_names(_read(DOC))
    assert len(doc) == len(set(doc)) == 5
    assert set(doc) == ci
    assert set(_verifier().REQUIRED_CHECKS) == ci


def test_a_name_ci_does_not_report_is_caught():
    ci = ci_check_names(_read(WORKFLOW))
    broken = _read(DOC).replace("`test (macos-latest, 3.12)`", "`test (macos-latest, 3.11)`")
    assert set(doc_check_names(broken)) != ci
    renamed = _read(WORKFLOW).replace('python: "3.13"', 'python: "3.14"')
    assert ci_check_names(renamed) != set(doc_check_names(_read(DOC)))
    named = _read(WORKFLOW).replace("  test:\n", "  test:\n    name: unit\n", 1)
    try:
        ci_check_names(named)
    except AssertionError:
        pass
    else:
        raise AssertionError("a job `name:` went unnoticed")


def test_doc_carries_every_fix_command_the_verifier_prints():
    mod = _verifier()
    text = _read(DOC)
    for setting in ("branch-protection", "auto-merge-allowed", "secret-scanning", "push-protection",
                    "private-vulnerability-reporting", "workflow-token-read-only", "maintainers-team"):
        for command in mod.fix_commands(setting, "OWNER/NAME", "main", mod.DEFAULT_TEAM):
            assert command in text.splitlines(), (setting, command)
    assert "python3 tools/readiness/verify_repo_settings.py OWNER/NAME" in text


def test_doc_records_the_api_docs_checked_with_a_date():
    text = _read(DOC)
    section = text.split("## Documentation checked", 1)[1].split("\n## ", 1)[0]
    assert re.search(r"\*\*20\d\d-\d\d-\d\d\*\*", section)
    for needle in ("rest/branches/branch-protection", "rest/repos/repos", "rest/actions/permissions",
                   "rest/teams/teams", "private-vulnerability-reporting"):
        assert any(needle in line and "https://docs.github.com/en/rest" in line
                   for line in section.splitlines()), needle


def test_team_slug_matches_codeowners():
    owners = re.findall(r"@[\w.-]+/([\w.-]+)", _read(CODEOWNERS))
    assert _verifier().DEFAULT_TEAM in owners


def test_doc_states_item_four_is_outstanding():
    text = _read(DOC)
    status = text.split("## Status", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(status.split())
    assert "outstanding" in flat and "owner-gated" in flat
    assert "not captures of a real repository" in flat

"""Guards for docs/reuse-and-test-design.md: linked, truthful about its commands, and honest about limits.

Each test has several clauses, and each clause was broken on its own and seen red (see the pull request).
"""
import importlib.util
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "reuse-and-test-design.md"
REUSE = ROOT / "skills" / "sigma-loop" / "scripts" / "reuse.py"
PAGE_NAME = "reuse-and-test-design.md"
VERBS = ("brief", "source", "check-research", "check-plan", "rerun", "inflight")


def _page():
    assert PAGE.is_file(), "docs/reuse-and-test-design.md does not exist"
    return PAGE.read_text(encoding="utf-8")


def _run(*args):
    return subprocess.run([sys.executable, str(REUSE), *args], capture_output=True, text=True, timeout=60)


def _reuse_module():
    spec = importlib.util.spec_from_file_location("reuse_under_docs_test", REUSE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fenced_blocks(text):
    return re.findall(r"^```[^\n]*\n(.*?)^```", text, flags=re.S | re.M)


def _section(text, heading):
    match = re.search(r"^## " + re.escape(heading) + r"\s*$(.*?)(?=^## |\Z)", text, flags=re.S | re.M)
    assert match, "no section headed: " + heading
    return match.group(1)


def test_the_reuse_page_is_linked_from_features_and_the_walkthrough():
    _page()
    features = (ROOT / "docs" / "features.md").read_text(encoding="utf-8").splitlines()
    walkthrough = (ROOT / "docs" / "running-the-loop.md").read_text(encoding="utf-8")
    # clause: the features list links the page
    linked = [i for i, line in enumerate(features) if PAGE_NAME in line]
    assert len(linked) == 1, linked
    # clause: the link sits on the row directly below the blast-radius row
    assert features[linked[0] - 1].startswith("| **Blast-radius research** |"), features[linked[0] - 1]
    # clause: a row that names a control also links the enforcement table
    assert "enforcement.md" in features[linked[0]]
    # clause: the walkthrough links it from both the Research step and the Plan step
    assert walkthrough.count(PAGE_NAME) >= 2


def test_every_reuse_command_the_page_names_answers_help():
    page = _page()
    help_run = _run("--help")
    assert help_run.returncode == 0
    usage = help_run.stdout.split("\n\n", 1)[0]
    # clause: the page pastes the real top-level usage text, as one fenced block
    assert usage in [block.strip("\n") for block in _fenced_blocks(page)]
    # clause: per-verb --help is unsupported, so the page says so, and the claim is true
    assert "per-verb" in page.lower() and "exits 2" in page
    assert _run("brief", "--help").returncode == 2
    # clause: only the six real verbs follow `reuse.py`, and all six are named and in the usage text
    named = set(re.findall(r"reuse\.py[ \t]+([A-Za-z][\w-]*)", page))
    assert named == set(VERBS), named ^ set(VERBS)
    for verb in VERBS:
        assert "reuse.py " + verb in usage, verb
    # clause: every backticked path the page names exists
    prose = re.sub(r"^```.*?^```", "", page, flags=re.S | re.M)
    paths = [p.rstrip(".,;:") for p in re.findall(r"`((?:skills|docs|tests)/[^`\s]+)`", prose)]
    paths = [p for p in paths if not re.search(r"[<*]", p)]
    assert paths, "the page names no repository path"
    missing = [p for p in paths if not (ROOT / p).exists()]
    assert not missing, missing
    # clause: the decision ladder table carries every decision, its evidence rule and its reason rule
    decisions = _reuse_module().DECISIONS
    rows = {line.split("|")[1].strip(): line for line in page.splitlines() if line.startswith("| ")}
    for decision, (evidence, reason, when) in decisions.items():
        row = rows.get(decision)
        assert row, "no ladder row for " + decision
        row = row.replace("`", "")   # the page backticks the <registry> placeholder so it is not read as markup
        assert when in row and evidence in row, decision
        assert reason is None or reason in row, decision


def test_the_page_states_its_limits():
    page = _page()
    section = _section(page, "What these checks do not guarantee")
    for phrase in ("only warned", "does not contact the registry", "last fetch", "shadowed", "500-file", "50-branch"):
        assert phrase in section, phrase

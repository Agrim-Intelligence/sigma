"""Pure move from tests/test_orchestrate_docs.py at 4042e97 (#2224 amendment 2): the guard nodes (pass at base); test bodies byte-identical.

The sigma-orchestrate skill, its references and docs/orchestrate.md stay consistent (#2145).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "sigma-orchestrate"
SKILL = SKILL_DIR / "SKILL.md"
DOC = ROOT / "docs" / "orchestrate.md"
FILES = [SKILL, DOC] + sorted((SKILL_DIR / "references").glob("*.md"))
ARGS = ("--max-seats", "--name", "--where", "--model", "--max-opus", "--no-merge", "--dry-run", "--yes")
DECISION_OPEN = "Standing decision by the person who started this batch"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_frontmatter_names_the_skill_and_its_scripts_grant():
    head = _text(SKILL).split("---")[1]
    assert re.search(r"^name: sigma-orchestrate$", head, re.M)
    assert re.search(r"^description: .+/sigma-orchestrate\.$", head, re.M)
    assert "Bash(python3 *)" in head


def test_every_argument_is_in_the_skill_and_the_docs():
    for arg in ARGS:
        assert arg in _text(SKILL), arg
        assert arg in _text(DOC), arg


def test_host_agnostic():
    """No hook is load-bearing: the contract's own banned words stay out of the skill and its docs."""
    for path in FILES:
        text = _text(path)
        for word in ("PreToolUse", "PostToolUse", ".mdc", "hook", "Hook"):
            assert word not in text, f"{path.name} names {word}"


def test_every_kit_script_it_names_exists():
    scripts = ROOT / "skills" / "sigma-loop" / "scripts"
    named = set()
    for path in FILES:
        named |= set(re.findall(r"sigma-loop/scripts/(\w+\.py)", _text(path)))
    assert {"promote.py", "deps.py", "goal_cost.py", "unpark.py", "handoff.py", "loop.py", "dag.py"} <= named
    for name in named:
        assert (scripts / name).is_file(), name


def test_run_md_says_the_last_marker_line_of_the_approve():
    run = _text(SKILL_DIR / "references" / "run.md")
    assert "last marker line of the latest trusted approve" in run
    assert "first line of the latest trusted approve" not in run

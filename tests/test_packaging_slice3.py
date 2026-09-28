import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_loop_skill_wellformed():
    t = (ROOT / "skills" / "agrim-loop" / "SKILL.md").read_text()
    assert "name: agrim-loop" in t and "allowed-tools:" in t
    assert "${CLAUDE_SKILL_DIR}/scripts/loop.py" in t and " start " in t   # start step present


def test_status_skill_wellformed():
    t = (ROOT / "skills" / "agrim-status" / "SKILL.md").read_text()
    assert "name: agrim-status" in t and "${CLAUDE_SKILL_DIR}/scripts/status.py" in t


def test_readme_marks_loop_shipped():
    assert "/agrim-loop" in (ROOT / "README.md").read_text()

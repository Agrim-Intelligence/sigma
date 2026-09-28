import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_skill_frontmatter_names_sdlc_init():
    text = (ROOT / "skills" / "agrim-init" / "SKILL.md").read_text()
    assert text.startswith("---")
    assert "name: agrim-init" in text


def test_skill_grants_python_and_invokes_scaffolder():
    text = (ROOT / "skills" / "agrim-init" / "SKILL.md").read_text()
    assert "allowed-tools:" in text and "Bash(python3" in text   # no per-run permission prompt
    assert "${CLAUDE_SKILL_DIR}/scripts/sdlc_init.py" in text     # documented path resolution


def test_readme_marks_init_shipped():
    text = (ROOT / "README.md").read_text()
    assert "/agrim-init" in text

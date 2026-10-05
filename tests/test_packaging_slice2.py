import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_skill_frontmatter_names_sdlc_init():
    text = (ROOT / "skills" / "sigma-init" / "SKILL.md").read_text()
    assert text.startswith("---")
    assert "name: sigma-init" in text


def test_skill_grants_python_and_invokes_scaffolder():
    text = (ROOT / "skills" / "sigma-init" / "SKILL.md").read_text()
    assert "allowed-tools:" in text and "Bash(python3" in text   # no per-run permission prompt
    # documented path resolution; #236: the skill runs the one entry point, init_flow.py
    assert "${CLAUDE_SKILL_DIR}/scripts/init_flow.py" in text


def test_readme_marks_init_shipped():
    text = (ROOT / "README.md").read_text()
    assert "/sigma-init" in text

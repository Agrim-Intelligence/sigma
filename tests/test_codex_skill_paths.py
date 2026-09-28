"""The documented Codex gesture must resolve the same helper as Claude's gesture."""
import pathlib


SKILLS = pathlib.Path(__file__).resolve().parent.parent / "skills"
WAIVED_OVERSIZE = {"agrim-goal-design", "agrim-goal-review"}


def test_every_skill_using_claude_skill_dir_explains_codex_resolution():
    missing = []
    for path in SKILLS.glob("*/SKILL.md"):
        text = path.read_text(encoding="utf-8")
        if ("${CLAUDE_SKILL_DIR}" in text and "## Codex path resolution" not in text
                and path.parent.name not in WAIVED_OVERSIZE):
            missing.append(path.parent.name)
    assert not missing, f"Codex would run an empty /scripts path in: {missing}"


def test_real_loop_command_resolves_to_its_installed_script():
    path = SKILLS / "agrim-loop" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    assert "## Codex path resolution" in text
    installed_dir = path.parent.resolve()
    script = pathlib.Path("${CLAUDE_SKILL_DIR}/scripts/loop.py".replace(
        "${CLAUDE_SKILL_DIR}", str(installed_dir)))
    assert script.is_file()

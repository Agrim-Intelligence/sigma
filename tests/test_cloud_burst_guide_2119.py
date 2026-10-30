"""#2119 AC-1: the cloud burst guide exists, land.md links it, and it states the launch-prompt rule and the four checks."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUIDE = ROOT / "skills" / "sigma-goal" / "references" / "cloud-burst.md"
LAND = ROOT / "skills" / "sigma-goal" / "references" / "land.md"


def test_land_links_the_guide():
    assert "(cloud-burst.md)" in LAND.read_text(encoding="utf-8")


def test_the_guide_states_the_launch_prompt_rule():
    text = GUIDE.read_text(encoding="utf-8")
    assert "`/sigma-goal <N> --land` plus facts about the environment. Nothing else." in text
    for banned in ("ci_required", "another pull request", "Stop on any refusal", "goal-specific verify command"):
        assert banned in text, banned


def test_the_guide_states_the_four_checks():
    text = GUIDE.read_text(encoding="utf-8")
    for n, phrase in enumerate(("If CI is paused, the dependency-update bot is paused",
                                "starts every service the test suite needs",
                                "do not depend on ambient tokens or databases",
                                "base branch passes `verify.command`"), 1):
        assert "%d. **" % n in text and phrase in text, (n, phrase)


def test_the_guide_names_the_doctor_rows_as_informational():
    text = GUIDE.read_text(encoding="utf-8")
    assert "never failures" in text and "no network call" in text

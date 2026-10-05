"""#615 AC-3: docs/upgrading.md tells an upgrader that verify trust is granted once per checkout, names the command,
and no longer claims nothing needs doing. The section is self-contained so the migration guide can link it."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "upgrading.md").read_text(encoding="utf-8")
TITLE = "## Verify commands need a one-time per-checkout trust"


def _section():
    assert TITLE in DOC, "the verify-trust section is missing from docs/upgrading.md"
    body = DOC.split(TITLE, 1)[1]
    return body.split("\n## ", 1)[0]


def test_section_names_the_command_and_the_once_per_checkout_rule():
    s = _section()
    assert "git -C <project> config --local sigma.allowRepositoryShellCommands true" in s
    assert "each checkout grants it once" in s
    assert "not copied by `git clone`" in s


def test_section_says_what_init_and_doctor_print():
    s = _section()
    assert "[trust] verify" in s and "verify command trusted in this checkout" in s


def test_the_no_migration_claim_is_gone_and_the_section_is_linked_from_the_intro():
    assert "carry on with no migration" not in DOC
    anchor = TITLE[3:].lower().replace(" ", "-")
    assert "(#%s)" % anchor in DOC.split("## From the pre-launch name", 1)[0]
    assert re.search(r"^## What Sigma reads without any migration", DOC, re.M)   # a sibling, not nested

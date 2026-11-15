"""Pure move from tests/test_followup_retired.py at 4042e97 (#2224 amendment 2): the guard nodes (pass at base); test bodies byte-identical.

The deferred-comment path and the orchestrator clean-up goals are retired (#2224).

Run: python3 -m pytest tests/test_followup_retired*.py
(generic form: python -m pytest tests/test_followup_retired*.py)

CONTROLS: C23 restore one deferred mention in follow-ups.md and `test_no_retired_phrase_remains` goes red.

"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_goals_json_keys_deferred_and_cleanup_are_no_longer_documented():
    for rel in ("skills/sigma-orchestrate/references/launch.md", "skills/sigma-orchestrate/references/run.md",
                "skills/sigma-orchestrate/SKILL.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r'"(deferred|cleanup)"', text), rel

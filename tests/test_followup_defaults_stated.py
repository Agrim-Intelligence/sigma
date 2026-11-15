"""The follow-up default is stated where an adopter reads it (#2224 amendment A1): `in-goal` is the default,
`file` an explicit opt-out.

Run: python3 -m pytest tests/test_followup_defaults_stated.py
(generic form: python -m pytest tests/test_followup_defaults_stated.py)

CONTROLS: C40 each test below, broken alone (template edited to `file`; one doc sentence dropped).
"""
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SENTENCE = "`in-goal` is the default"
OPT_OUT = "`file` is an explicit opt-out"


def _text(rel):
    return " ".join((ROOT / rel).read_text(encoding="utf-8").split())   # a wrapped line is still the sentence


def test_shipped_template_default_is_in_goal():
    block = json.loads(_text("skills/sigma-init/templates/config.json.tmpl"))["followups"]
    assert block["mode"] == "in-goal"


@pytest.mark.parametrize("rel", [
    "docs/configuration.md",
    "skills/sigma-loop/references/filing.md",
    "docs/orchestrate.md",
    "skills/sigma-orchestrate/references/follow-ups.md",
])
def test_doc_states_in_goal_is_the_default_and_file_an_opt_out(rel):
    text = _text(rel)
    assert SENTENCE in text, rel
    assert OPT_OUT in text, rel


def test_init_skill_states_the_default():
    assert SENTENCE in _text("skills/sigma-init/SKILL.md")

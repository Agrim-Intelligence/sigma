"""#1543: the forces-first rule and `missing structure` are each pinned by their own test (text greps).

Pin 1 deliberately tolerates the legacy exception, so deleting only that clause turns pin 2 red alone.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
PR = "skills/sigma-plan-review/SKILL.md"
REVIEW = "skills/sigma-review/SKILL.md"
DOC = "docs/change-zones.md"


def _flat(rel):
    return " ".join((ROOT / rel).read_text(encoding="utf-8").split())


def test_plan_review_calls_a_forceless_abstraction_over_built():
    assert re.search(r"An interface, factory, layer or indirection naming no force"
                     r"(?: \(a legacy sprout or wrap excepted\))? is over-built;", _flat(PR))


def test_plan_review_excepts_a_legacy_sprout_or_wrap():
    assert "naming no force (a legacy sprout or wrap excepted) is over-built;" in _flat(PR)


def test_plan_review_lists_the_four_forces():
    assert ("the four forces: a second real variant in this goal, an unreliable dependency, "
            "a boundary crossing, a quality scenario goal-design names.") in _flat(PR)


def test_review_missing_structure_only_in_new_and_modern_zone_files():
    assert ("In a new or modern-zone file, never a legacy one, a diff adding the third or later case "
            "to a conditional on a type or kind is `missing structure`") in _flat(REVIEW)


def test_review_missing_structure_is_a_follow_up():
    assert "is `missing structure`: a follow-up Concern." in _flat(REVIEW)


def test_doc_limits_missing_structure_to_follow_sound_design_files():
    assert ("It reports `missing structure`, a follow-up, only where `classify` prints "
            "`follow sound design`: new and modern-zone files, never a legacy one (#1543).") in _flat(DOC)


def test_doc_says_the_fourth_force_is_inert_until_the_checklist_exists():
    assert ("a quality scenario goal-design names, inert until the goal-design checklist exists"
            ) in _flat(DOC)


def test_doc_says_the_forces_first_effect_is_unmeasured_naming_1544():
    assert ("The effect of the forces-first rule on model behaviour is UNMEASURED: issue #1544 "
            "is the deferred baseline.") in _flat(DOC)

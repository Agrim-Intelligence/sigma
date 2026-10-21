"""Level 2's dropped-line check on an edit/edit conflict: both sides changed the same base line (one import list), so no
correct resolution keeps both versions word for word. A line the unit wrote there is kept when every word it introduced
is still in the resolved file; a line only the unit touched must still survive word for word. Pure functions only."""
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts" / "feature_upkeep_level2.py"


def _mod():
    spec = importlib.util.spec_from_file_location("feature_upkeep_level2", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


BASE = "from app import greet, farewell\n\ndef test_greet():\n    pass\n"
OURS = "from app import greet, farewell, shout\n\ndef test_greet():\n    pass\n\ndef test_shout():\n    pass\n"
THEIRS = "from app import greet, farewell, whisper\n\ndef test_greet():\n    pass\n\ndef test_whisper():\n    pass\n"
GOOD = ("from app import greet, farewell, shout\nfrom app import whisper\n\ndef test_greet():\n    pass\n\n"
        "def test_shout():\n    pass\n\ndef test_whisper():\n    pass\n")


def test_a_merged_import_line_keeps_the_unit_and_the_integration_side():
    m = _mod()
    assert m.dropped_unit_lines(BASE, THEIRS, GOOD, OURS) == []
    assert m.dropped_base_lines(BASE, OURS, GOOD, THEIRS) == []
    one_line = GOOD.replace("from app import greet, farewell, shout\nfrom app import whisper\n",
                            "from app import greet, farewell, shout, whisper\n")
    assert m.dropped_unit_lines(BASE, THEIRS, one_line, OURS) == [] and m.dropped_base_lines(BASE, OURS, one_line, THEIRS) == []


def test_a_contested_line_whose_new_word_is_gone_is_still_refused():
    m = _mod()
    lost = GOOD.replace("from app import whisper\n", "")
    assert m.dropped_unit_lines(BASE, THEIRS, lost, OURS) == ["from app import greet, farewell, whisper"]


def test_a_line_only_the_unit_touched_must_survive_word_for_word():
    m = _mod()
    base = "a = 1\nb = 2\n"
    theirs = "a = 1\nb = 2\nc = 3\n"
    ours = "a = 10\nb = 2\n"                     # integration changed line 1 only; the unit's line 3 is not contested
    assert m.dropped_unit_lines(base, theirs, "a = 10\nb = 2\nc = 3  # kept\n", ours) == ["c = 3"]


def test_without_the_other_side_the_rule_is_unchanged():
    m = _mod()
    assert m.dropped_unit_lines(BASE, THEIRS, GOOD) == ["from app import greet, farewell, whisper"]

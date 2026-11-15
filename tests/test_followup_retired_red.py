"""Pure move from tests/test_followup_retired.py at 4042e97 (#2224 amendment 2): the wholly assertion-red nodes; test bodies byte-identical.

The deferred-comment path and the orchestrator clean-up goals are retired (#2224).

Run: python3 -m pytest tests/test_followup_retired*.py
(generic form: python -m pytest tests/test_followup_retired*.py)

CONTROLS: C23 restore one deferred mention in follow-ups.md and `test_no_retired_phrase_remains` goes red.

"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RETIRED = ("sigma-orchestrate:deferred", "deferred.py", "deferred-items", "deferred items", "clean-up goal",
           "cleanup goal", "--kind cleanup", "sigma-orchestrate:cleanup", "one clean-up goal per")
#: history, not instructions
ALLOWED = ("CHANGELOG.md",)


def _files():
    for base in ("skills", "docs", "hooks"):
        for path in sorted((ROOT / base).rglob("*")):
            if (path.is_file() and path.suffix in (".md", ".py", ".sh", ".json", ".tmpl")
                    and "__pycache__" not in path.parts and not path.name.startswith("whats-new-")
                    and path.name not in ALLOWED and "launch" not in path.parts[len(ROOT.parts):][1:2]):
                yield path


def test_deferred_script_and_its_test_are_gone():
    assert not (ROOT / "skills" / "sigma-orchestrate" / "scripts" / "deferred.py").exists()
    assert not (ROOT / "tests" / "test_orchestrate_deferred.py").exists()


def test_no_retired_phrase_remains():
    hits = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        hits += ["%s: %s" % (path.relative_to(ROOT), phrase) for phrase in RETIRED if phrase in text]
    assert not hits, hits


def test_follow_ups_md_names_the_gesture_and_both_reasons():
    text = (ROOT / "skills" / "sigma-orchestrate" / "references" / "follow-ups.md").read_text(encoding="utf-8")
    assert "work.py" in text and "add-slice" in text
    assert "different-feature" in text and "slice-cap" in text

"""#1638: review section 1 quotes `quality_delta.py` stdout instead of describing the delta by hand."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _section_one():
    text = (ROOT / "skills" / "sigma-review" / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"^## 1\. .*?(?=^## 2\. )", text, re.S | re.M)
    assert match, "review SKILL.md lost its section 1 or section 2 heading"
    return " ".join(match.group(0).split())


def test_section_one_quotes_quality_delta_verbatim():
    one = _section_one()
    assert 'Run `python3 "' in one
    assert '/../sigma-loop/scripts/quality_delta.py" <goal>` ' in one
    assert "and quote its stdout verbatim." in one


def test_section_one_says_where_to_run_it():
    # The reviewer's cwd is the repo root and the brief names no path, so the sentence must.
    one = _section_one()
    assert "from the work record's `worktree`" in one and "`.sdlc/work/<goal>`" in one


def test_section_one_falls_back_on_no_metrics_unmeasured_and_errors():
    one = _section_one()
    assert ("If it prints `no metrics configured` or `unmeasured`, or errors, never skip: "
            "run the repo's own tools") in one


def test_fallback_literals_are_what_quality_delta_prints():
    src = (ROOT / "skills" / "sigma-loop" / "scripts" / "quality_delta.py").read_text(encoding="utf-8")
    assert '["no metrics configured"]' in src
    assert '"unmeasured: %s"' in src and "unmeasured (%s)" in src


def test_section_one_is_shorter_than_99_words():
    assert len(_section_one().split()) < 99

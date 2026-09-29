"""#2827: a haiku-tier signal counts only in the goal's TITLE.

The router used to scan title+body in one pass, and a real issue body almost always quotes a code
comment, names a docstring or cites a lint -- so a non-trivial goal with no opus/fable stem was
routed DOWN to haiku by one body word (a P1 multi-module fix ran at haiku on `comment` alone). Haiku is the
one tier where under-powering costs more than over-powering, so it is the one tier whose stems are
read from the title only; opus/fable stems keep scanning title+body, because upward resolution is
safe. The title is the text's FIRST LINE -- the shape `loop._predict_model_choice_at_pick` builds
(`"<title>\\n\\n<body>"`) and the one documented fallback gesture prints -- or, for a goal FILE,
the title `sources.LocalSource.fetch_title_body` reads (frontmatter `title:`, else the file stem).
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
P = ROOT / "skills" / "agrim-model" / "scripts" / "predict.py"


def _mod():
    spec = importlib.util.spec_from_file_location("predict", P)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _sdlc(tmp_path, cfg='{"model_selection": "auto"}'):
    base = tmp_path / ".sdlc"
    base.mkdir()
    (base / "config.json").write_text(cfg)
    return str(base)


@pytest.mark.parametrize("word", ["comment", "comments", "commented", "docstring", "lint",
                                  "renamed", "changelog"])
def test_a_haiku_stem_only_in_the_body_resolves_to_sonnet(word):
    text = (f"Fence the shared write sites across three modules\n\n"
            f"The {word} in the S2 module is wrong and must change with the fence.")
    assert _mod().predict_with_reason(text) == ("sonnet", None)


def test_a_haiku_stem_in_the_title_still_routes_to_haiku():
    m = _mod()
    assert m.predict("fix typo in README") == "haiku"
    assert m.predict_with_location("fix typo in README\n\nThe word is misspelled.") == (
        "haiku", "typo", "title")


def test_opus_and_fable_stems_still_count_in_the_body():
    m = _mod()
    assert m.predict_with_location("Tidy the module\n\nthis needs a data migration first") == (
        "opus", "migrat", "body")
    assert m.predict_with_location("Tidy the page\n\nrewrite the blog intro",
                                   max_tier="fable") == ("fable", "blog", "body")


def test_title_haiku_loses_to_body_opus_upward_resolution_intact():
    assert _mod().predict_with_location("Fix the typo\n\nin the payment reconciliation job") == (
        "opus", "payment", "body")


def test_a_body_haiku_is_not_rescued_when_the_title_haiku_is_excluded():
    text = "Fix the lint config\n\nthe comment above it is stale"
    assert _mod().predict_with_reason(text, ("lint",)) == ("sonnet", None)


def test_the_price_ceiling_still_binds_the_default():
    text = "Fence the write sites\n\nthe comment is stale"
    assert _mod().predict_with_reason(text, max_tier="haiku") == ("haiku", None)


def test_an_empty_first_line_is_an_empty_title_never_lstripped():
    """A leading blank line means "no title"; stripping it would promote the body's first line."""
    assert _mod().predict("\nfix typo\n\nbody") == "sonnet"
    assert _mod().predict("\n\n---\ntitle: fix typo\n---\n") == "sonnet"


def test_a_crlf_title_line_is_still_a_title():
    assert _mod().predict_with_location("fix typo in README\r\n\r\nbody") == (
        "haiku", "typo", "title")


def test_one_line_json_is_all_title_which_is_why_the_documented_gesture_is_not_gh_json():
    """`gh issue view --json title,body` prints one-line JSON: no newline, so the body is read as
    title. Pinned so nobody re-documents that gesture as the fallback."""
    blob = '{"body":"the comment is stale","title":"Fence the write sites"}'
    assert _mod().predict(blob) == "haiku"


def test_resolve_step_keeps_whole_text_haiku_semantics(tmp_path):
    """Out of scope (#2827): a mechanical STEP's downgrade is resolve-step's purpose."""
    base = _sdlc(tmp_path)
    assert _mod().resolve_step("Restructure the loader\nthen fix the comment", base) == {
        "model": "haiku", "effort": "low"}


# ---- CLI: the gestures the docs give ------------------------------------------------------------

def _documented_fallback_shape(title, body):
    # what the documented `gh issue view N --json title,body --jq '.title + "\n\n" + (.body // "")'`
    # prints (run live against a real issue during #2827's implement phase)
    return f"{title}\n\n{body}"


def test_why_reports_where_the_signal_was_found(capsys):
    m = _mod()
    assert m.main(["predict.py", "why", _documented_fallback_shape("Fix typo", "x")]) == 0
    assert capsys.readouterr().out.strip() == "model=haiku in=title signal=typo"
    assert m.main(["predict.py", "why",
                   _documented_fallback_shape("Tidy", "the race condition")]) == 0
    assert capsys.readouterr().out.strip() == "model=opus in=body signal=race condition"
    assert m.main(["predict.py", "why", _documented_fallback_shape("Tidy", "the comment")]) == 0
    assert capsys.readouterr().out.strip() == "model=sonnet signal="


def test_resolve_on_the_documented_fallback_shape_ignores_a_body_haiku(tmp_path, capsys):
    base = _sdlc(tmp_path)
    text = _documented_fallback_shape("Fence the shared write sites", "The S2 comment is wrong")
    assert _mod().main(["predict.py", "resolve", text, base]) == 0
    assert capsys.readouterr().out.strip() == "sonnet"


def _goal_file(tmp_path, name, content, newline="\n"):
    f = tmp_path / name
    f.write_bytes(content.replace("\n", newline).encode("utf-8"))
    return str(f)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_a_goal_file_title_is_its_frontmatter_title(tmp_path, capsys, newline):
    base = _sdlc(tmp_path)
    trivial = _goal_file(tmp_path, "0001-a.md",
                         "---\nid: 0001\ntitle: Fix typo in README\nstatus: pending\n---\n\n"
                         "The word is misspelled.\n", newline)
    real = _goal_file(tmp_path, "0002-b.md",
                      '---\nid: 0002\ntitle: "Fence the shared write sites"\n---\n\n'
                      "The S2 comment is wrong.\n", newline)
    m = _mod()
    assert m.main(["predict.py", "resolve", trivial, base]) == 0
    assert capsys.readouterr().out.strip() == "haiku"
    assert m.main(["predict.py", "resolve", real, base]) == 0
    assert capsys.readouterr().out.strip() == "sonnet"
    assert m.main(["predict.py", "why", trivial, base]) == 0
    assert capsys.readouterr().out.strip() == "model=haiku in=title signal=typo"


def test_a_subtitle_key_is_not_the_title(tmp_path, capsys):
    base = _sdlc(tmp_path)
    f = _goal_file(tmp_path, "0004-plain.md",
                   "---\nsubtitle: fix typo\ntitle: Fence the write sites\n---\nbody\n")
    assert _mod().main(["predict.py", "resolve", f, base]) == 0
    assert capsys.readouterr().out.strip() == "sonnet"


def test_a_goal_file_without_a_title_key_falls_back_to_its_stem(tmp_path, capsys):
    """Same fallback as `sources.LocalSource.fetch_title_body`, so the documented local fallback
    (`resolve "$goal"`) and the tier the pick recorded cannot disagree."""
    base = _sdlc(tmp_path)
    no_key = _goal_file(tmp_path, "0003-fix-typo.md", "---\nid: 0003\n---\nthe comment\n")
    no_fence = _goal_file(tmp_path, "0005-wire-flag.md", "Wire the flag.\nthe comment is stale\n")
    bom = _goal_file(tmp_path, "0006-wire-thing.md", "﻿---\ntitle: fix typo\n---\nbody\n")
    m = _mod()
    assert m.main(["predict.py", "resolve", no_key, base]) == 0
    assert capsys.readouterr().out.strip() == "haiku"
    assert m.main(["predict.py", "resolve", no_fence, base]) == 0
    assert capsys.readouterr().out.strip() == "sonnet"
    assert m.main(["predict.py", "resolve", bom, base]) == 0      # fence unrecognised -> stem
    assert capsys.readouterr().out.strip() == "sonnet"


def test_a_goal_file_body_still_reaches_opus(tmp_path, capsys):
    base = _sdlc(tmp_path)
    f = _goal_file(tmp_path, "0007-x.md",
                   "---\ntitle: Tidy things\ndone_when: \"the migration passes\"\n---\nbody\n")
    assert _mod().main(["predict.py", "resolve", f, base]) == 0
    assert capsys.readouterr().out.strip() == "opus"


def test_an_empty_title_value_falls_back_to_the_stem_and_the_last_title_key_wins(tmp_path, capsys):
    """Parity with `frontmatter.get(text, "title") or path.stem`: an empty value is no title, and
    `frontmatter.parse` keeps the LAST `title:` line."""
    base = _sdlc(tmp_path)
    empty = _goal_file(tmp_path, "0008-fix-typo.md", '---\ntitle: ""\n---\nthe body\n')
    last = _goal_file(tmp_path, "0009-x.md", "---\ntitle: fix typo\ntitle: Fence writes\n---\nb\n")
    m = _mod()
    assert m.main(["predict.py", "resolve", empty, base]) == 0
    assert capsys.readouterr().out.strip() == "haiku"
    assert m.main(["predict.py", "resolve", last, base]) == 0
    assert capsys.readouterr().out.strip() == "sonnet"

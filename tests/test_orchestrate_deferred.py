"""Tests for skills/sigma-orchestrate/scripts/deferred.py (#2153): the deferred-items comment parser.

Run: $HOME/.sigma-venv312/bin/python -m pytest tests/test_orchestrate_deferred.py
(generic form: python -m pytest tests/test_orchestrate_deferred.py)

One test per clause of the rules at the top of the script; the CONTROLS note at the bottom says which break
each was seen red under.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "sigma-orchestrate", "scripts")
sys.path.insert(0, SCRIPTS)
import deferred  # noqa: E402

M = deferred.MARKER
ITEM = "- [area:api] tidy the retry helper - it is duplicated (files: src/api/retry.py, src/api/util.py)"


def comment(body, assoc="OWNER", cid=1):
    return {"id": cid, "body": body, "author_association": assoc}


def items_of(body):
    return deferred.parse_body(body)[0]


def test_an_item_after_the_marker_parses_into_its_four_fields():
    assert items_of(M + "\n" + ITEM) == [{"area": "api", "what": "tidy the retry helper",
                                          "why": "it is duplicated", "files": ["src/api/retry.py", "src/api/util.py"]}]


def test_no_marker_means_no_items_and_no_rejections():
    assert deferred.parse_body(ITEM) == ([], [])


def test_the_marker_may_sit_anywhere_on_its_line():
    assert len(items_of("**Deferred items** " + M + " (host wrapped)\n" + ITEM)) == 1


def test_a_quoted_marker_does_not_open_a_list():
    assert items_of("> " + M + "\n" + ITEM) == []


def test_a_fenced_marker_does_not_open_a_list():
    assert items_of("```\n" + M + "\n```\n" + ITEM) == []


def test_a_bullet_inside_a_fence_or_quote_after_the_marker_is_not_an_item():
    assert items_of(M + "\n```\n" + ITEM + "\n```\n> " + ITEM) == []


def test_a_bullet_inside_a_fence_or_quote_after_the_marker_is_listed_not_skipped():
    items, rejected = deferred.parse_body(M + "\n```\n" + ITEM + "\n```\n> " + ITEM)
    assert items == [] and len(rejected) == 2
    assert "code fence" in rejected[0]["reason"] and "quoted" in rejected[1]["reason"]
    assert rejected[0]["line"] == ITEM


@pytest.mark.parametrize("line", ["1. x", "+ [area:api] x - y (files: none)", "\u2022 x", "[area:api] x - y (files: none)"])
def test_each_list_shape_inside_a_fence_or_quote_is_listed(line):
    assert len(deferred.parse_body(M + "\n```\n" + line + "\n```")[1]) == 1
    assert len(deferred.parse_body(M + "\n> " + line)[1]) == 1


def test_prose_and_the_fence_lines_inside_a_fence_or_quote_stay_silent():
    assert deferred.parse_body(M + "\n```python\nprint(1)\n```\n> just prose") == ([], [])


def test_a_bullet_before_the_marker_is_not_an_item():
    assert items_of(ITEM + "\n" + M) == []


def test_plain_prose_after_the_marker_is_ignored_not_rejected():
    assert deferred.parse_body(M + "\nthree follow-ups below\n\n" + ITEM + "\nthanks") == (items_of(M + "\n" + ITEM), [])


def test_star_bullets_parse_too():
    assert len(items_of(M + "\n" + ITEM.replace("- [", "* [", 1))) == 1


def test_a_malformed_bullet_is_rejected_with_a_reason_not_dropped():
    items, rejected = deferred.parse_body(M + "\n- tidy the helper\n" + ITEM)
    assert len(items) == 1 and len(rejected) == 1
    assert rejected[0]["line"] == "- tidy the helper" and "area" in rejected[0]["reason"]


@pytest.mark.parametrize("line", [
    "- [area:] x - y (files: a)",
    "- [area:api] - y (files: a)",
    "- [area:api] x -  (files: a)",
    "- [area:api] x - y",
    "- [api] x - y (files: a)",
    "- [area:api] x y (files: a)",
    "- [area:api] x - y (files: a",
])
def test_each_grammar_clause_rejects_its_own_break(line):
    items, rejected = deferred.parse_body(M + "\n" + line)
    assert items == [] and len(rejected) == 1


def test_the_first_spaced_hyphen_splits_what_from_why():
    item = items_of(M + "\n- [area:a] fix x - then y - and z (files: f)")[0]
    assert item["what"] == "fix x" and item["why"] == "then y - and z"


def test_an_unspaced_hyphen_does_not_split():
    item = items_of(M + "\n- [area:a] fix the cross-area link - because (files: f)")[0]
    assert item["what"] == "fix the cross-area link"


@pytest.mark.parametrize("files,want", [("none", []), ("None", []), ("", []), ("a.py", ["a.py"]),
                                        ("a.py,b/c.py ,  d/", ["a.py", "b/c.py", "d/"]), ("src/**/*.ts", ["src/**/*.ts"])])
def test_the_files_list(files, want):
    assert items_of(M + "\n- [area:a] x - y (files: %s)" % files)[0]["files"] == want


@pytest.mark.parametrize("files", ["/etc/passwd", "../x.py", "a/../b.py", "a\\b.py", "a.py,,b.py", "a.py,", "a\x01.py"])
def test_an_unsafe_or_empty_path_rejects_the_whole_item(files):
    items, rejected = deferred.parse_body(M + "\n- [area:a] x - y (files: %s)" % files)
    assert items == [] and len(rejected) == 1


def test_a_dot_dot_inside_a_name_is_not_a_parent_segment():
    assert len(items_of(M + "\n- [area:a] x - y (files: a..b.py, ..hidden)")) == 1


def test_a_line_over_the_length_bound_is_rejected_even_when_every_field_is_within_its_own():
    paths = ",".join("d/%s.py" % ("p" * 25) + str(i) for i in range(deferred.MAX_FILES))
    long = "- [area:a] x - y (files: %s)" % paths
    assert len(long) > deferred.MAX_LINE
    items, rejected = deferred.parse_body(M + "\n" + long)
    assert items == [] and "longer than" in rejected[0]["reason"]


@pytest.mark.parametrize("line", ["- [area:a]    - y (files: f)", "- [area:a] x -   (files: f)", "- [area:a] x - y "])
def test_a_blank_what_or_why_or_a_missing_files_group_is_rejected(line):
    assert deferred.parse_body(M + "\n" + line)[0] == []


def test_trailing_whitespace_after_the_files_group_is_fine():
    assert len(deferred.parse_body(M + "\n- [area:a] x - y (files: f)  ")[0]) == 1


def test_a_field_over_its_bound_is_rejected():
    big = "- [area:a] %s - y (files: f)" % ("w" * (deferred.MAX_FIELD + 1))
    items, rejected = deferred.parse_body(M + "\n" + big)
    assert items == [] and "longer" in rejected[0]["reason"]


def test_more_than_the_file_bound_is_rejected():
    many = ",".join("f%d" % i for i in range(deferred.MAX_FILES + 1))
    assert deferred.parse_body(M + "\n- [area:a] x - y (files: %s)" % many)[0] == []


def test_items_per_comment_are_bounded():
    body = M + "\n" + "\n".join("- [area:a] x%d - y (files: f)" % i for i in range(deferred.MAX_ITEMS + 3))
    items, rejected = deferred.parse_body(body)
    assert len(items) == deferred.MAX_ITEMS and len(rejected) == 3


def test_a_trusted_comment_is_read_for_each_trusted_association():
    for assoc in ("OWNER", "MEMBER", "COLLABORATOR"):
        assert len(deferred.parse_comments([comment(M + "\n" + ITEM, assoc)])["items"]) == 1


@pytest.mark.parametrize("assoc", ["NONE", "CONTRIBUTOR", "FIRST_TIMER", "", None, "owner"])
def test_an_untrusted_association_is_rejected_not_read(assoc):
    out = deferred.parse_comments([comment(M + "\n" + ITEM, assoc)])
    assert out["items"] == [] and len(out["rejected"]) == 1 and "association" in out["rejected"][0]["reason"]


def test_a_comment_with_no_association_field_is_rejected():
    out = deferred.parse_comments([{"id": 5, "body": M + "\n" + ITEM}])
    assert out["items"] == [] and out["rejected"][0]["comment"] == 5


def test_a_bare_string_is_rejected_not_read():
    out = deferred.parse_comments([M + "\n" + ITEM])
    assert out["items"] == [] and len(out["rejected"]) == 1


def test_a_comment_without_the_marker_is_ignored_whatever_its_author():
    assert deferred.parse_comments([comment(ITEM, "NONE"), comment("hello", "NONE")]) == {"items": [], "rejected": []}


def test_items_from_several_comments_merge_in_order_and_identical_ones_collapse():
    other = "- [area:ui] reword the empty state - unclear (files: ui/a.tsx)"
    out = deferred.parse_comments([comment(M + "\n" + ITEM, cid=1), comment(M + "\n" + other + "\n" + ITEM, cid=2),
                                   comment(M + "\n" + ITEM + "\n" + ITEM, cid=3)])
    assert [i["area"] for i in out["items"]] == ["api", "ui"]


def test_items_differing_in_any_field_are_kept_apart():
    a = "- [area:a] x - y (files: f)"
    for variant in ("- [area:b] x - y (files: f)", "- [area:a] x2 - y (files: f)", "- [area:a] x - y2 (files: f)",
                    "- [area:a] x - y (files: g)"):
        assert len(deferred.parse_comments([comment(M + "\n" + a + "\n" + variant)])["items"]) == 2


def test_rejections_name_their_comment_and_are_bounded():
    body = M + "\n" + "\n".join("- bad %d" % i for i in range(deferred.MAX_REJECTED + 5))
    out = deferred.parse_comments([comment(body, cid=9)])
    assert len(out["rejected"]) == deferred.MAX_REJECTED and out["rejected_more"] == 5
    assert out["rejected"][0]["comment"] == 9


def test_a_non_list_is_an_error():
    with pytest.raises(ValueError):
        deferred.parse_comments({"body": M})


def _run(*args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "deferred.py"), *args], capture_output=True, text=True)


def test_cli_parse_prints_the_stored_shape_and_exits_zero_with_rejections():
    with tempfile.TemporaryDirectory() as t:
        path = os.path.join(t, "c.json")
        with open(path, "w") as fh:
            json.dump([comment(M + "\n" + ITEM + "\n- junk")], fh)
        r = _run("parse", path)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and len(out["items"]) == 1 and len(out["rejected"]) == 1
    assert set(out["items"][0]) == {"area", "what", "why", "files"}


def test_cli_check_exits_zero_only_for_a_clean_nonempty_draft():
    with tempfile.TemporaryDirectory() as t:
        good, bad, empty = (os.path.join(t, n) for n in ("g", "b", "e"))
        for path, text in ((good, M + "\n" + ITEM), (bad, M + "\n" + ITEM + "\n- junk"), (empty, M + "\n")):
            with open(path, "w") as fh:
                fh.write(text)
        assert [_run("check", p).returncode for p in (good, bad, empty)] == [0, 1, 1]


def test_cli_bad_input_exits_two():
    assert _run("parse", "/nonexistent/x.json").returncode == 2
    assert _run("frobnicate", "x").returncode == 2
    assert _run("parse").returncode == 2
    with tempfile.TemporaryDirectory() as t:
        path = os.path.join(t, "c.json")
        with open(path, "w") as fh:
            fh.write("{not json")
        assert _run("parse", path).returncode == 2
        with open(path, "w") as fh:
            fh.write('{"a": 1}')
        assert _run("parse", path).returncode == 2


@pytest.mark.parametrize("line", [
    "1. [area:api] tidy it - dup (files: a.py)",
    "2) [area:api] tidy it - dup (files: a.py)",
    "+ [area:api] tidy it - dup (files: a.py)",
    "-[area:api] tidy it - dup (files: a.py)",
    "*[area:api] tidy it - dup (files: a.py)",
    "[area:api] tidy it - dup (files: a.py)",
    "\u2022 [area:api] tidy it - dup (files: a.py)",
])
def test_a_list_line_that_is_not_a_dash_or_star_bullet_is_rejected_not_skipped(line):
    items, rejected = deferred.parse_body(M + "\n" + ITEM + "\n" + line)
    assert len(items) == 1 and len(rejected) == 1
    assert rejected[0]["line"] == line and "- " in rejected[0]["reason"]


def test_each_alternative_of_the_list_like_test_is_its_own_case():
    """Numbered `1.` and `1)`, `+`, a leader glued to `[`, a bare `[area:` and a unicode dot are separate clauses."""
    for line in ("3. x", "3) x", "+ x", "-[x", "\u2022 x", "[area:x] y"):
        assert deferred.parse_body(M + "\n" + line)[1], line


@pytest.mark.parametrize("line", ["-5 degrees is the limit", "1.5 times as slow", "a - b", "+1 from me", "Done."])
def test_prose_that_only_resembles_a_marker_of_a_list_is_still_ignored(line):
    assert deferred.parse_body(M + "\n" + line) == ([], [])


def test_a_list_line_before_the_marker_is_not_read():
    assert deferred.parse_body("1. x\n" + M) == ([], [])


def test_a_list_line_inside_a_fence_or_quote_before_the_marker_is_not_read():
    assert deferred.parse_body("```\n1. x\n```\n> 1. x\n" + M) == ([], [])


def test_cli_check_exits_nonzero_for_a_numbered_item_next_to_a_valid_bullet():
    with tempfile.TemporaryDirectory() as t:
        path = os.path.join(t, "b")
        with open(path, "w") as fh:
            fh.write(M + "\n" + ITEM + "\n2. [area:api] second - why (files: none)\n")
        r = _run("check", path)
    assert r.returncode == 1 and len(json.loads(r.stdout)["rejected"]) == 1


# CONTROLS (#2153), each break applied alone to deferred.py and its named test seen red:
#   trust: TRUSTED widened / the association test dropped (test_an_untrusted_association_..., ..._no_association_field_...)
#   marker: required-marker test dropped (test_no_marker_means_no_items...); fence skip dropped
#     (test_a_fenced_marker...); quote skip dropped (test_a_quoted_marker...); bullets before the marker kept
#     (test_a_bullet_before_the_marker_is_not_an_item)
#   grammar: the area, what, why, files and "(files:" clauses each loosened (test_each_grammar_clause_...);
#     greedy what (test_the_first_spaced_hyphen_splits...); a rejection dropped instead of listed
#     (test_a_malformed_bullet_is_rejected...)
#   files: absolute, `..` segment, backslash, control, empty-element checks each dropped (the unsafe-path cases);
#     `..` as a substring instead of a segment (test_a_dot_dot_inside_a_name...)
#   bounds: line, field, files, items, rejected (their own tests); dedup (test_items_from_several_comments_...)
# CONTROLS (#2157), each applied alone to deferred.py, its tests seen red: the numbered `1.` and `1)` alternatives
#   (separately), the `+` leader, the leader-glued-to-`[` branch, the unicode-dot leader, the bare `[area:` branch,
#   the reject-instead-of-skip branch, and `check`'s exit rule loosened to ignore rejections.
# CONTROLS (#2162), each applied alone to deferred.py, its tests seen red: the fenced-line report dropped, the quoted-line
#   report dropped, the `>`-prefixed list-shape regex dropped (the quoted unicode-dot and numbered cases), the
#   reject-instead-of-skip branch replaced by a bare continue, and the report made to fire before the marker.

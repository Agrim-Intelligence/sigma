"""`sigma:approve <commit id>` (#2153): an approve may name the commit it approved; a named id must be the PR head.

Run: $HOME/.sigma-venv312/bin/python -m pytest tests/test_approve_names_head.py
(generic form: python -m pytest tests/test_approve_names_head.py)

Safety-critical, so it fails closed: an id that is not the PR head, is malformed or ambiguous, or cannot be
checked (the head is unreadable) does not count as an approve for this head. An unnamed marker is untouched.
One test per clause; the CONTROLS note at the bottom lists the break each one was seen red under.
"""
import json

import pytest

from test_work import (ON, PR_GET, POST_COMMENT, _comments, _post_review, _review, _runner, _sdlc, _started,
                       _posted, prfake, work)

HEAD = "1234abcd" * 5
OTHER = "9999eeee" * 5
RAW = {"worktree": "/nonexistent", "pr": "7"}


def _row(body, assoc="OWNER", login="maint"):
    return {"body": body, "author": {"login": login}, "authorAssociation": assoc}


def _directive(rows, head=HEAD, pr_author="maint"):
    handlers = [(prfake.comments_get(7), prfake.rest_comments(rows))]
    handlers.append((PR_GET, head if isinstance(head, Exception) else prfake.rest_pull(author=pr_author, headRefOid=head)))
    run = _runner(handlers)
    return work._comment_directive(RAW, run), run


# ---- the id grammar (`_approve_commit`)

@pytest.mark.parametrize("rest", ["", "   ", " - ship it", " looks good", "-all", " — lgtm", " ok bad"])
def test_text_with_no_hex_looking_first_word_is_unnamed(rest):
    assert work._approve_commit(rest) is work._UNNAMED


@pytest.mark.parametrize("rest,want", [(" " + HEAD[:7], HEAD[:7]), (" " + HEAD, HEAD), (" " + HEAD.upper(), HEAD),
                                       (" " + HEAD[:12] + " looks good", HEAD[:12]), (" " + "ab" * 32, "ab" * 32),
                                       (" " + HEAD[:7] + " bad", HEAD[:7])])
def test_a_clean_id_is_returned_lower_case(rest, want):
    assert work._approve_commit(rest) == want


@pytest.mark.parametrize("rest", [" abc123", " added", " decade", " " + "a" * 41, " " + "a" * 63, " " + "a" * 65,
                                  " abc1234,", " (abc1234)", ":abc1234", " feedback", " abc1234 def5678"])
def test_anything_id_shaped_but_not_clean_is_bad(rest):
    assert work._approve_commit(rest) is work._BAD_ID


def test_the_id_after_a_marker_is_read_from_its_own_line_only():
    assert work._line_directive_text("sigma:approve " + HEAD[:7] + "\nsigma:unblock") == ("unblock", "")
    assert work._line_directive_text("> sigma:approve " + HEAD) == (None, "")
    assert work._line_directive_text("sigma:approve " + HEAD) == ("approve", " " + HEAD)
    assert work._line_directive("sigma:approve " + HEAD) == "approve"


# ---- the gate (`_comment_directive`)

def test_a_named_full_head_counts():
    assert _directive([_row("sigma:approve " + HEAD)])[0] == ("approve", True)


def test_a_head_reported_in_upper_case_still_matches():
    assert _directive([_row("sigma:approve " + HEAD[:9])], head=HEAD.upper())[0] == ("approve", True)


def test_a_named_prefix_of_the_head_counts():
    assert _directive([_row("sigma:approve " + HEAD[:7])])[0] == ("approve", True)


def test_the_id_compares_case_insensitively():
    assert _directive([_row("sigma:approve " + HEAD[:10].upper())])[0] == ("approve", True)


def test_a_named_id_that_is_not_the_head_does_not_count(capsys):
    assert _directive([_row("sigma:approve " + OTHER)])[0] == (None, None)
    assert "does not name the PR's current head commit" in capsys.readouterr().err


def test_a_prefix_that_differs_in_its_last_digit_does_not_count():
    wrong = HEAD[:11] + ("0" if HEAD[11] != "0" else "1")
    assert _directive([_row("sigma:approve " + wrong)])[0] == (None, None)


def test_the_id_must_be_a_prefix_not_merely_contained_in_the_head():
    assert _directive([_row("sigma:approve " + HEAD[1:9])])[0] == (None, None)


@pytest.mark.parametrize("rest", [" abc123", " added", " abc1234,", " " + "a" * 41, " abc1234 def5678"])
def test_a_malformed_or_ambiguous_id_does_not_count_even_when_it_looks_like_the_head(rest):
    body = "sigma:approve" + rest.replace("abc1234", HEAD[:7])
    assert _directive([_row(body)])[0] == (None, None)


def test_a_named_approve_does_not_count_when_the_head_cannot_be_read():
    handlers = [(prfake.comments_get(7), prfake.rest_comments([_row("sigma:approve " + HEAD)])),
                (PR_GET, prfake.rest_pull(author="maint", headRefOid=HEAD))]
    calls = []

    def run(cwd, argv):
        calls.append(" ".join(map(str, argv)))
        if len([c for c in calls if "--method GET" in c and "pulls/7 " in c + " "]) >= 2:
            raise RuntimeError("HTTP 502")
        return _runner(handlers)(cwd, argv)

    assert work._comment_directive(RAW, run) == (None, None)


@pytest.mark.parametrize("head", ["", "zz", HEAD[:8], HEAD + "0", HEAD[:39], "1234ABCD" * 5 + "-"])
def test_a_named_approve_does_not_count_when_the_reported_head_is_not_an_id(head):
    # the named id IS a prefix of most of these: only the head's own shape check keeps them out
    assert _directive([_row("sigma:approve " + HEAD[:7])], head=head)[0] == (None, None)


def test_an_unnamed_approve_keeps_its_behaviour_and_reads_no_head():
    result, run = _directive([_row("sigma:approve"), _row("sigma:approve — ship it")], head=OTHER)
    assert result == ("approve", True)
    assert len(run.calls) == 2                      # the pull and one comment page, as before #2153


def test_a_named_approve_reads_the_head_once_however_many_names_follow():
    result, run = _directive([_row("sigma:approve " + HEAD), _row("sigma:approve " + HEAD[:9])])
    assert result == ("approve", True) and len(run.calls) == 3


def test_a_wrong_named_approve_ends_an_earlier_approve():
    assert _directive([_row("sigma:approve"), _row("sigma:approve " + OTHER)])[0] == (None, None)


def test_a_wrong_named_approve_leaves_a_block_standing():
    assert _directive([_row("sigma:block"), _row("sigma:approve " + OTHER)])[0] == ("block", True)


def test_a_later_correct_approve_after_a_wrong_one_counts():
    assert _directive([_row("sigma:approve " + OTHER), _row("sigma:approve " + HEAD)])[0] == ("approve", True)


def test_a_stranger_naming_the_head_is_still_ignored():
    assert _directive([_row("sigma:approve " + HEAD, assoc="NONE")])[0] == (None, None)


def test_a_block_after_a_named_approve_still_wins():
    assert _directive([_row("sigma:approve " + HEAD), _row("sigma:block")])[0] == ("block", True)


# ---- the merge gate and the posting helper

def _gate(tmp_path, comment, head=HEAD):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner([(PR_GET, prfake.rest_pull(author="maint", headRefOid=head))]
                  + [h for h in _review(decision=None, comments=[comment], comment_authors=["rev"], pr_author="maint")
                     if h[0] != PR_GET])
    return work.review_gate(d, cfg, g, run=run)


def test_the_merge_gate_passes_an_approve_that_names_the_head(tmp_path):
    assert _gate(tmp_path, "sigma:approve " + HEAD[:8]) == (True, "")


def test_the_merge_gate_parks_an_approve_that_names_another_commit(tmp_path):
    ok, why = _gate(tmp_path, "sigma:approve " + OTHER)
    assert ok is False and "not approved yet" in why


def test_post_review_names_the_head_it_reviewed(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    seen = []

    def run(cwd, argv):
        line = " ".join(map(str, argv))
        if _posted(line):
            seen.append(line)
        return ""

    assert "posted sigma:approve" in _post_review(d, ON, goal, run, "approve")
    body = [a for a in seen[0].split(" -f ") if a.startswith("body=")][0]
    first_line = body[len("body="):].split("\n")[0]
    assert first_line.startswith("sigma:approve ") and len(first_line.split()[1]) == 40


def test_post_review_leaves_block_and_unblock_unnamed(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    seen = []

    def run(cwd, argv):
        line = " ".join(map(str, argv))
        if _posted(line):
            seen.append(line)
        return ""

    _post_review(d, ON, goal, run, "block", reason="fix the retry")
    assert "body=sigma:block\n" in seen[0]


# CONTROLS (#2153). Each break below was applied alone to work.py and its named test seen red (21 run; the
# one that first stayed green, a head reported in upper case, got test_a_head_reported_in_upper_case_...):
#   gate: the named-id branch off (test_a_named_id_that_is_not_the_head_does_not_count); `_BAD_ID` clause,
#     unreadable-head clause and head-equality clause each dropped on their own (the bad-id, head-unreadable
#     and not-the-head tests); prefix -> substring (test_the_id_must_be_a_prefix_not_merely_contained...);
#     a refused approve no longer ends an earlier one (test_a_wrong_named_approve_ends_an_earlier_approve),
#     falls through and counts anyway (test_a_named_id_that_is_not_the_head...), clears a block too
#     (test_a_wrong_named_approve_leaves_a_block_standing); id not lower-cased; head's shape check weakened or
#     its lower-casing dropped; head read eagerly (test_an_unnamed_approve_keeps_its_behaviour_and_reads_no_head)
#   grammar: minimum 7, the 64-digit length, the 40..63 gap, the two-ids clause, the hex-run clause,
#     fullmatch -> match on the first word (each its own parametrised case above)
#   post_review: the head dropped from the body (test_post_review_names_the_head_it_reviewed)
#   the text after the marker dropped (the grammar tests through `_line_directive_text`)

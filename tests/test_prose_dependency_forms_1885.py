"""#1885: the deterministic prose forms read as explicit dependency edges.

`blocker_scan.EXPLICIT_TRIGGERS` gained `blocked on|until #N` and the follow-up forms
(`follow-up to|of|on|from #N`, `follows up #N`, `following up on #N`). Measured before the change
over the last 1000 issues of this repository: 83 follow-up hits in 57 issues, every one naming the
issue the filing comes after, and 1 hit in 2944 comments, also in the right direction.

Each table below is a control: delete one form from `EXPLICIT_TRIGGERS` and its row goes red; widen
a tight form to the 40-character window and the `NOT_EDGES` rows go red.
"""
import importlib.util, pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


bs = _mod("blocker_scan")

#: (text, the phrase the record keeps, the ref)
EDGES = [
    ("Blocked on #12", "blocked on", "12"),
    ("this is blocked on the schema change in #12", "blocked on", "12"),
    ("blocked until #12 lands", "blocked until", "12"),
    ("Follow-up to #12 (review findings, none blocking).", "follow-up to", "12"),
    ("(follow-up to #12)", "follow-up to", "12"),
    ("Follow-up of #12 review.", "follow-up of", "12"),
    ("follow-up on #12", "follow-up on", "12"),
    ("Follow-up from #12 (PR #13), launch:next.", "follow-up from", "12"),
    ("Forces-first follow-ups from #12 retro", "follow-ups from", "12"),
    ("follow up to #12", "follow up to", "12"),
    ("followup to #12", "followup to", "12"),
    ("follow-up to issue #12", "follow-up to", "12"),
    ("this follows up #12", "follows up", "12"),
    ("following up on #12", "following up on", "12"),
    ("FOLLOW-UP TO #12", "follow-up to", "12"),
]

#: Text that must make NO edge at fetch time.
NOT_EDGES = [
    "filed a follow-up to cover the cap, tracked in #13",   # names the child: tight form refuses it
    "a follow-up to the release notes in #13",              # anything between phrase and #N
    "the review follows up the cap work in #13",            # the verb form is tight as well
    "Follow-ups for #12: #13, #14",                          # a parent's list of its children
    "Follow-up #13 filed for the cap",                       # bare: names the child
    "Follow-up: #13",
    "- #1585 the decision sheet (S2). Prerequisites: #1584.",  # epic body: a child's prerequisite
    "this builds on #12",
    "see #12",
    "after #40 lands",                                       # documented weak residue
    "this actually needs #9 to land first",                  # documented weak residue
    "unblocked on #12",
    "blocked on the release, see #12",                       # clause boundary, as for blocked by
    "follow-up to owner/repo#12",                            # another repository
]


def test_every_new_form_produces_a_fetch_time_edge():
    for text, phrase, ref in EDGES:
        assert bs.extract_refs(text) == [{"phrase": phrase, "ref": ref}], text


def test_every_excluded_form_produces_no_fetch_time_edge():
    for text in NOT_EDGES:
        assert bs.extract_refs(text) == [], text


def test_the_original_markers_still_match_the_same_way():
    for text, phrase in (("**Blocked by:** #7", "blocked by"), ("Blocked by #7", "blocked by"),
                         ("this depends on #7", "depends on"), ("depends upon #7", "depends upon"),
                         ("blocked by the parser in #7", "blocked by")):
        assert bs.extract_refs(text) == [{"phrase": phrase, "ref": "7"}], text


def test_a_new_form_is_confident_at_check_time_and_a_weak_word_is_not():
    bc = _mod("backlog_check")
    for text in ("Follow-up to #7.", "blocked on #7", "following up on #7"):
        doc = {"ref": "1", "raw": "a title\n" + text, "blocker_refs": []}
        f = next(x for x in bc._explicit_blockers(doc, [{"ref": "7", "open": True}]) if x["ref"] == "7")
        assert f["confident"] is True, text
    for text in ("after #7 lands", "this needs #7 to land", "waiting on #7"):
        doc = {"ref": "1", "raw": "a title\n" + text, "blocker_refs": []}
        f = next(x for x in bc._explicit_blockers(doc, [{"ref": "7", "open": True}]) if x["ref"] == "7")
        assert f["confident"] is False, text


def test_is_explicit_tells_the_two_kinds_of_match_apart():
    text = "waiting on #7, and Follow-up to #8"
    ms = list(bs._BLOCK_RE.finditer(text))
    assert [(m.group(2), bs.is_explicit(text, m)) for m in ms] == [("7", False), ("8", True)]


def test_the_dependency_map_reads_a_follow_up_edge():
    dc = _mod("dag_core")
    assert dc.edges_from("Fix the cap\n\nFollow-up to #1586 (review findings).", 1846) == [
        (1586, 1846, "follow-up to")]


def test_a_follow_up_form_in_a_comment_is_advisory_and_in_the_body_confident():
    bc = _mod("backlog_check")
    open_refs = [{"ref": "12", "open": True}]
    doc = {"ref": "1", "raw": "a title\nscope only", "blocker_refs": []}
    f = next(x for x in bc._explicit_blockers(doc, open_refs, "filed #40 as a follow-up to #12")
             if x["ref"] == "12")
    assert f["confident"] is False
    doc = {"ref": "1", "raw": "a title\nFollow-up to #12.", "blocker_refs": []}
    f = next(x for x in bc._explicit_blockers(doc, open_refs) if x["ref"] == "12")
    assert f["confident"] is True
    doc = {"ref": "1", "raw": "a title\nscope only", "blocker_refs": []}
    f = next(x for x in bc._explicit_blockers(doc, open_refs, "Blocked on #12") if x["ref"] == "12")
    assert f["confident"] is True                      # only the follow-up forms are body-only


def test_a_stored_phrase_has_its_whitespace_collapsed():
    assert bs.extract_refs("follow-up\tto  #12") == [{"phrase": "follow-up to", "ref": "12"}]
    assert bs.extract_refs("blocked\t on #12") == [{"phrase": "blocked on", "ref": "12"}]

"""#1885: `deps.py` -- the whole-ticket dependency read and the marker writer.

`analyse` is pure and classifies every `#N` by what the machinery already does with it; `mark` writes
the canonical marker through the source's own guarded `append_to_body`. Controls: each `read_as` row
goes red if its branch is removed, and `mark` is seen to write nothing on ALREADY and REFUSED.
"""
import importlib.util, pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


deps = _mod("deps")


def _by_ref(refs):
    return {r["ref"]: r for r in refs}


def test_every_reference_is_classified_by_what_already_reads_it():
    refs = _by_ref(deps.analyse(
        "5", "Fix the cap", "Follow-up to #12. This also touches the code in #13.\n"
        "The rollout needs #14 looked at.",
        ["Blocked by #15", "see #16, and #5 is this issue", "acme/other#17 is elsewhere"]))
    assert {k: v["read_as"] for k, v in refs.items()} == {
        "12": "edge", "13": "mention", "14": "weak", "15": "comment-edge", "16": "mention"}
    assert refs["15"]["where"] == ["comment"]


def test_an_edge_in_the_body_outranks_a_mention_elsewhere():
    refs = _by_ref(deps.analyse("5", "t", "depends on #12", ["#12 is going well"]))
    assert refs["12"]["read_as"] == "edge" and refs["12"]["where"] == ["body", "comment"]


def test_an_unpark_answer_block_is_not_read():
    qa = deps.blocker_scan.UNPARK_QA_START + " waiting on #12 " + deps.blocker_scan.UNPARK_QA_END
    assert deps.analyse("5", "t", "scope.\n" + qa, []) == []


def test_snippets_are_bounded_and_scrubbed():
    body = "x " * 400 + "Follow-up to #12 " + "y " * 400
    snip = deps.analyse("5", "t", body, [])[0]["snippets"][0]
    assert "#12" in snip and len(snip) <= deps.SNIPPET_CHARS + 10


class _Source:
    def __init__(self, body, refuse=None):
        self.body, self.refuse, self.appended, self.notes = body, refuse, [], []

    states = {}

    def _read_issue(self, goal, fields, comment_limit=None):
        state = self.states.get(str(goal), "OPEN")
        if state == "ERR":
            raise RuntimeError("unreadable")
        return {"title": "t", "body": self.body, "state": state, "comments": []}

    def append_to_body(self, goal, marker):
        if self.refuse:
            raise ValueError(self.refuse)
        self.appended.append(marker)

    def note(self, goal, text):
        self.notes.append(text)


def test_mark_writes_the_canonical_marker_and_a_note_that_mints_no_edge():
    src = _Source("This can only start once the zone work in #12 is merged.")
    assert deps.mark(src, "5", "12", "the body says the zone work in #12 must merge first")[0] == "MARKED"
    assert src.appended == ["**Blocked by:** #12"]
    assert "#" not in src.notes[0]
    assert not deps.blocker_scan._BLOCK_RE.search(src.notes[0])


def test_mark_is_idempotent_on_an_existing_body_edge():
    src = _Source("Follow-up to #12.")
    assert deps.mark(src, "5", "12", "")[0] == "ALREADY"
    assert src.appended == [] and src.notes == []


def test_mark_refuses_a_cycle_and_a_self_reference_and_writes_nothing():
    src = _Source("text", refuse="would close a cycle: #5, #12")
    assert deps.mark(src, "5", "12", "")[0] == "REFUSED"
    assert deps.mark(src, "5", "5", "")[0] == "REFUSED"
    assert deps.mark(src, "5", "abc", "")[0] == "REFUSED"
    assert src.appended == [] and src.notes == []


def test_a_mention_in_the_body_is_upgraded_by_an_edge_in_a_comment():
    refs = _by_ref(deps.analyse("5", "t", "this touches #12", ["Blocked by #12"]))
    assert refs["12"]["read_as"] == "comment-edge"


def test_mark_refuses_a_closed_or_unreadable_reference_and_writes_nothing():
    for state in ("CLOSED", "ERR"):
        src = _Source("text")
        src.states = {"12": state}
        assert deps.mark(src, "5", "12", "")[0] == "REFUSED", state
        assert src.appended == [] and src.notes == []


def test_help_prints_usage_and_exits_zero(capsys):
    assert deps.main(["deps.py", "--help"]) == 0
    assert "usage" in capsys.readouterr().out

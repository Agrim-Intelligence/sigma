"""`chain_reads.CycleReads`: one run cycle reads the same issue many times (one chain's cycle read a single issue 49
times); within ONE cycle those repeats are served from memory. Safe because: only a GET of one issue or one issue's
comments is kept, any other call that can write drops everything, the cache lives for one `chain.run` call, and the
cycle's two out-of-process steps (the sweep before it, the pick after it) are outside it.

Controls (each seen red, then restored): the write that does not clear the store; a list read that clears it; the
entry bound; the byte bound; a graphql mutation treated as a read; a non-GET method treated as a read; the cache
applied to an endpoint that is not one issue."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = ROOT / "skills" / "sigma-loop" / "scripts"


def _mod():
    spec = importlib.util.spec_from_file_location("chain_reads", S / "chain_reads.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Base:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        return "out%d" % len(self.calls)


ISSUE = ["api", "repos/o/r/issues/7", "--method", "GET"]
COMMENTS = ["api", "repos/o/r/issues/7/comments", "--method", "GET", "-f", "per_page=100", "-f", "page=1"]
LIST = ["api", "repos/o/r/issues", "--method", "GET", "-f", "labels=x"]


def test_a_repeated_issue_read_is_served_once():
    b = Base()
    r = _mod().CycleReads(b)
    assert [r(ISSUE), r(ISSUE), r(ISSUE)] == ["out1", "out1", "out1"] and len(b.calls) == 1
    assert r.hits == 2


def test_a_repeated_comment_page_read_is_served_once_and_pages_are_distinct():
    b = Base()
    r = _mod().CycleReads(b)
    page2 = COMMENTS[:-1] + ["page=2"]
    assert r(COMMENTS) == r(COMMENTS) and r(page2) != r(COMMENTS) and len(b.calls) == 2


@pytest.mark.parametrize("write", [
    ["api", "repos/o/r/issues/7/labels", "--method", "POST", "-f", "labels[]=x"],
    ["api", "repos/o/r/issues/7", "--method", "PATCH", "-f", "state=closed"],
    ["api", "repos/o/r/issues/7/labels/x", "--method", "DELETE"],
    ["api", "graphql", "-f", "query=mutation { addLabelsToLabelable(input: {}) { clientMutationId } }"],
    ["issue", "close", "7"],
    ["label", "create", "x"],
])
def test_any_call_that_can_write_drops_every_kept_read(write):
    b = Base()
    r = _mod().CycleReads(b)
    r(ISSUE)
    r(write)
    r(ISSUE)
    assert len(b.calls) == 3, "the read after a write must go to GitHub again"


@pytest.mark.parametrize("read", [LIST, ["api", "graphql", "-f", "query=query { repository { id } }"], ["api", "user", "-q", ".login"]])
def test_a_read_of_something_else_does_not_drop_the_kept_issue(read):
    b = Base()
    r = _mod().CycleReads(b)
    r(ISSUE)
    r(read)
    r(ISSUE)
    assert len(b.calls) == 2


def test_a_list_is_never_kept():
    b = Base()
    r = _mod().CycleReads(b)
    r(LIST)
    r(LIST)
    assert len(b.calls) == 2


def test_a_failed_read_is_not_kept_and_the_error_passes_through():
    class Boom(Base):
        def __call__(self, args):
            self.calls.append(args)
            raise RuntimeError("gh failed")
    b = Boom()
    r = _mod().CycleReads(b)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            r(ISSUE)
    assert len(b.calls) == 2


def test_the_store_is_bounded_by_entries(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m, "MAX_ENTRIES", 3)
    b = Base()
    r = m.CycleReads(b)
    for n in range(10):
        r(["api", "repos/o/r/issues/%d" % n, "--method", "GET"])
    assert len(r.store) <= 3


def test_the_store_is_bounded_by_bytes(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m, "MAX_BYTES", 100)

    class Big(Base):
        def __call__(self, args):
            self.calls.append(args)
            return "x" * 60
    b = Big()
    r = m.CycleReads(b)
    for n in range(6):
        r(["api", "repos/o/r/issues/%d" % n, "--method", "GET"])
    assert r.size <= 100 and len(r.store) <= 1


def test_an_unrecognised_call_shape_goes_straight_through():
    b = Base()
    r = _mod().CycleReads(b)
    events = ["api", "repos/o/r/issues/7/events", "--method", "GET"]
    assert r(events) != r(events) and len(b.calls) == 2


def test_extra_arguments_bypass_the_cache():
    class Two:
        def __init__(self):
            self.n = 0

        def __call__(self, args, binary="gh"):
            self.n += 1
            return str(self.n)
    b = Two()
    r = _mod().CycleReads(b)
    assert r(ISSUE, "gh") != r(ISSUE, "gh")

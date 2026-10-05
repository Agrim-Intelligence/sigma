"""Tests for skills/sigma-loop/scripts/witness.py (issue #1934).

The three controls #1934's judged_when names are here, each a deliberately-broken case that must be
REJECTED: (a) a collection-error-only red is not credited as strongly verified, (b) a test edited
between red and green is refused, (c) a test never red is reported `unverified`.
"""
import importlib.util
import pathlib
import tempfile

S = pathlib.Path(__file__).parent.parent / "skills" / "sigma-loop" / "scripts"


def _w():
    spec = importlib.util.spec_from_file_location("witness", S / "witness.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _repo(body):
    """A scratch root holding tests/test_x.py with `body` as test_a's content."""
    d = tempfile.mkdtemp()
    t = pathlib.Path(d) / "tests"; t.mkdir()
    (t / "test_x.py").write_text("def test_a():\n%s\n" % body)
    return d


NODE = "tests/test_x.py::test_a"


# --- source extraction + hashing -------------------------------------------------------------

def test_source_is_found_for_a_plain_a_class_and_a_parametrised_id():
    w = _w()
    d = tempfile.mkdtemp()
    t = pathlib.Path(d) / "tests"; t.mkdir()
    (t / "test_x.py").write_text(
        "def test_a():\n    assert 1 == 1\n\n\nclass TestC:\n    def test_b(self):\n        assert 2 == 2\n")
    assert "assert 1 == 1" in w.test_source("tests/test_x.py::test_a", root=d)
    assert "assert 2 == 2" in w.test_source("tests/test_x.py::TestC::test_b", root=d)
    # every case of one parametrised test shares one body, so it must share one hash
    assert w.test_source("tests/test_x.py::test_a[case1]", root=d) == \
           w.test_source("tests/test_x.py::test_a", root=d)


def test_source_returns_None_rather_than_raising_on_anything_unreadable():
    """A hash we cannot compute degrades to "no witness", never to a crash in the verify path."""
    w = _w()
    d = _repo("    assert 1 == 1")
    assert w.test_source("tests/test_x.py::test_missing", root=d) is None
    assert w.test_source("tests/nope.py::test_a", root=d) is None
    assert w.test_source("no-separator", root=d) is None
    assert w.source_hash(None) is None


def test_hash_ignores_trailing_whitespace_but_NOT_indentation():
    """Normalising only trailing whitespace is deliberate: a reflowed assertion is a different
    assertion until somebody has looked at it."""
    w = _w()
    assert w.source_hash("assert x == 1   \n") == w.source_hash("assert x == 1\n")
    assert w.source_hash("    assert x == 1") != w.source_hash("assert x == 1")


# --- failure classification -------------------------------------------------------------------

def test_an_assertion_failure_classifies_as_assertion():
    assert _w().classify("E       AssertionError: assert 3 == 4") == "assertion"


def test_a_collection_error_classifies_as_collection_error_NOT_as_assertion():
    """CONTROL (a), first half. A test run before its implementation exists fails with
    NameError/ImportError -- red only in the trivial sense."""
    w = _w()
    assert w.classify("E   ModuleNotFoundError: No module named 'thing'") == "collection-error"
    assert w.classify("!!! Interrupted: 1 error during collection !!!") == "collection-error"


def test_a_collection_error_that_also_prints_AssertionError_is_still_collection_error():
    """ORDER MATTERS. A module that fails to import often prints an AssertionError from elsewhere in
    the traceback. Reading that as a genuine assertion failure would launder the WEAKEST possible
    red into the STRONGEST kind, which is exactly what #1934 forbids."""
    mixed = "ImportError: cannot import name 'x'\nE   AssertionError: assert 1 == 2\n"
    assert _w().classify(mixed) == "collection-error"


# --- the three judged_when controls -----------------------------------------------------------

def test_CONTROL_a_a_collection_error_only_red_is_NOT_credited():
    """CONTROL (a): a test whose only red was a collection error is not strongly verified."""
    w = _w(); d = _repo("    assert 1 == 1")
    h = w.source_hash(w.test_source(NODE, root=d))
    w.record(d, "42", NODE, "collection-error", h)
    v = w.verdict(d, "42", [NODE], root=d)
    assert v["verdict"] == w.UNVERIFIED
    assert "only weak red" in v["detail"][NODE]["why"]


def test_CONTROL_b_a_test_edited_between_red_and_green_is_refused():
    """CONTROL (b), and the cheat it closes: write a strict test, see red, implement, watch it STILL
    fail, then quietly weaken the test until green. The record would show a legitimate
    red-then-green while the test that PASSED is not the test that FAILED."""
    w = _w(); d = _repo("    assert compute() == 42")
    w.record(d, "42", NODE, "assertion", w.source_hash(w.test_source(NODE, root=d)))
    assert w.verdict(d, "42", [NODE], root=d)["verdict"] == w.VERIFIED     # honest so far
    (pathlib.Path(d) / "tests" / "test_x.py").write_text(
        "def test_a():\n    assert compute() is not None\n")               # weakened
    v = w.verdict(d, "42", [NODE], root=d)
    assert v["verdict"] == w.UNVERIFIED
    assert "edited since its red" in v["detail"][NODE]["why"]


def test_CONTROL_c_a_test_never_seen_red_is_unverified_not_silently_passing():
    """CONTROL (c). `unverified` is a distinct, visible state -- never silently equivalent to a
    passing test, which is the whole point of recording this at all."""
    w = _w(); d = _repo("    assert 1 == 1")
    v = w.verdict(d, "42", [NODE], root=d)
    assert v["verdict"] == w.UNVERIFIED
    assert v["detail"][NODE]["why"] == "never seen red"


# --- the shared vocabulary with #1935 ---------------------------------------------------------

def test_a_mutation_witness_credits_a_test_that_red_first_can_never_cover():
    """THE REFACTOR GAP, and the reason #1934 and #1935 are a pair rather than two variations.

    A behaviour-preserving refactor produces no new red -- its tests are green throughout -- so
    red-first can NEVER witness one. Killing a mutant IS an existing test failing for the right
    reason, so #1935 writes a `mutation` witness and it counts exactly as strongly."""
    w = _w(); d = _repo("    assert compute() == 42")
    h = w.source_hash(w.test_source(NODE, root=d))
    w.record(d, "42", NODE, "mutation", h, detail="killed mutant 7: `==` -> `!=`")
    v = w.verdict(d, "42", [NODE], root=d)
    assert v["verdict"] == w.VERIFIED
    assert v["detail"][NODE]["kind"] == "mutation"


def test_the_verdict_vocabulary_is_1933s_three_strings_not_a_fourth():
    """Both issues explicitly ask the two goals to share ONE vocabulary. #1933 already shipped
    verified/unverified/absent in the verify evidence; a fourth string here is the divergence."""
    w = _w()
    assert (w.VERIFIED, w.UNVERIFIED, w.ABSENT) == ("verified", "unverified", "absent")


def test_no_tests_at_all_is_absent_never_a_pass():
    w = _w()
    assert _w().verdict(tempfile.mkdtemp(), "42", [])["verdict"] == w.ABSENT


def test_an_unknown_kind_is_refused_rather_than_recorded():
    w = _w(); d = _repo("    assert 1 == 1")
    assert w.record(d, "42", NODE, "totally-made-up", "abc") is None
    assert w.witnesses(d, "42") == []


def test_a_malformed_line_does_not_blind_the_whole_record():
    w = _w(); d = _repo("    assert 1 == 1")
    w.record(d, "42", NODE, "assertion", "abc")
    with w.path(d, "42").open("a") as fh:
        fh.write("{not json\n")
    w.record(d, "42", NODE, "assertion", "def")
    assert len(w.witnesses(d, "42")) == 2

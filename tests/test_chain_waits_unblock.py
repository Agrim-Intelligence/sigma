"""The reading rule of `chain_waits`, widened and narrowed by what a real tangled backlog showed (Chain unblock).

Each wording class below is a SHAPE, written with generic names; none quotes a ticket. Two kinds of test:
  * a wording that states a prerequisite and was read as nothing (or as the wrong form) is now an unconfirmed wait;
  * a wording that says a goal does NOT wait was read as a wait, which parked a goal that could run: it is now read
    as nothing (a negation within a few words before the trigger).
Controls (each seen red, then restored): the negation test removed; each new form's pattern removed on its own;
`waits` -> `wait` only; the comma window back to the old one; the body bound back to 20000 characters."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = ROOT / "skills" / "sigma-loop" / "scripts"


def _cw():
    spec = importlib.util.spec_from_file_location("chain_waits", S / "chain_waits.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


#: wording -> what `find` must read. A state of the world, not a ticket.
READ_AS = [
    ("Go-live waits for #12.", [("12", "waiting-for")]),
    ("this step waits for #12 to settle", [("12", "waiting-for")]),
    ("Stage 2 only once E11, E14, E15, E17 and E24 (#12) are closed.", [("12", "only-once")]),
    ("lands on main again as soon as E17 (#12) has merged into the unit branch",
     [("12", "once-done")]),
    ("starts once the port (#12) is closed", [("12", "once-done")]),
    ("#12 and lever 1 (#13) land on main **before** this issue's one paid run",
     [("12", "lands-before"), ("13", "lands-before")]),
    ("#12 lands before this change", [("12", "lands-before")]),
    ("What this waits on: #12 and #13 (the two fixes), because #14 needs both.", [("12", "waiting-for"), ("13", "list")]),
    ("this change is waiting on #12", [("12", "waiting-for")]),
    ("Through the port's own blockers it also sits behind E11 (#12), which records the plan; E3 (#13), which builds the "
     "renderer; and E2 (#14), which holds the registry. It reads them.",
     [("12", "behind"), ("13", "behind"), ("14", "behind")]),
    ("it stands behind #12", [("12", "behind")]),
]

#: wording that says the goal does NOT wait: nothing to read
NOT_A_WAIT = [
    "this link does NOT wait for the landing (#12).",
    "It does **not** wait for the combined landing (#12).",
    "S2 no longer waits for #12",
    "it never waits for #12",
    "it doesn't wait for #12",
    "this is not blocked by #12 any more",
    "does not need to hold on #12",
    "it need not wait for #12",
    "S2 does not depend on #12",
    "it will not start only once #12 has landed",
    "it will not run as soon as #12 has merged",
    "it does not wait on #12",
    "this slice never sits behind #12",
]

#: sentences that only name an issue near the new triggers: still nothing
STILL_JUST_NAMES = [
    "see #12, the thread that waits on nobody",
    "it sits behind the desk. See #12 for the background",
    "the queue is behind schedule (#12)",
]


@pytest.mark.parametrize("text,expected", READ_AS)
def test_a_stated_prerequisite_is_read(text, expected):
    assert _cw().find(text) == expected


@pytest.mark.parametrize("text", NOT_A_WAIT)
def test_a_wording_that_says_it_does_not_wait_is_read_as_nothing(text):
    assert _cw().find(text) == []


def test_a_negation_in_an_earlier_sentence_does_not_cancel_a_later_wait():
    assert _cw().find("It is not ready. It waits for #12.") == [("12", "waiting-for")]


def test_a_negation_far_from_the_trigger_does_not_cancel_it():
    text = "This is not about the old design at all but the new ruling means it waits for #12"
    assert _cw().find(text) == [("12", "waiting-for")]


def test_a_body_written_to_the_platform_bound_is_read_to_its_end():
    cw = _cw()
    text = "x " * 30000 + "\nGo-live waits for #12."
    assert len(text) < 65536
    assert cw.BODY_CHARS >= 65536
    assert cw.find(text[:cw.BODY_CHARS]) == [("12", "waiting-for")]


def test_every_new_form_is_in_the_table_of_forms():
    assert {"once-done", "lands-before"} <= set(_cw().FORMS)


@pytest.mark.parametrize("text", STILL_JUST_NAMES)
def test_the_new_triggers_do_not_read_a_bare_mention(text):
    assert _cw().find(text) == []


def test_a_reference_in_another_repository_is_not_read_in_a_span_form():
    cw = _cw()
    assert cw.find("it sits behind acme/other#12 and #13") == [("13", "behind")]
    assert cw.find("acme/other#12 and #13 land on main before this change") == [("13", "lands-before")]


def test_a_sentence_form_is_bounded():
    text = "it sits behind " + " ".join("#%d" % k for k in range(10, 85)) + " all"
    assert 0 < len(_cw().find(text)) <= _cw().MAX_MORE + 1

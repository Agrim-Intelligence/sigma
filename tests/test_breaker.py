"""Tests for skills/sigma-loop/scripts/breaker.py (issue #1936).

The isolation guarantee is asserted STRUCTURALLY here, not in prose -- #1936 requires "no channel
exists between maker and breaker: verified by construction (spawn topology + no shared scratch
path), not by instructing the maker not to use one", because in this repo policy alone has already
failed once.
"""
import importlib.util
import pathlib

S = pathlib.Path(__file__).parent.parent / "skills" / "sigma-loop" / "scripts"


def _b():
    spec = importlib.util.spec_from_file_location("breaker", S / "breaker.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


SHARP = """## Problem
whatever
## done_when
- [ ] `is_valid_age` returns False for a negative age
- [ ] returns False for an age greater than 120
## judged_when
- [ ] the boundary at 0 and at 120 is covered
"""

VAGUE = """## done_when
- [ ] add age validation
- [ ] improve the handling
"""


# --- reading the acceptance list ---------------------------------------------------------------

def test_acceptance_reads_both_done_when_and_judged_when_in_order():
    items = _b().acceptance(SHARP)
    assert len(items) == 3
    assert items[0].startswith("`is_valid_age` returns False")
    assert "boundary" in items[-1]


def test_acceptance_stops_at_the_next_heading_and_ignores_other_sections():
    assert all("whatever" not in i for i in _b().acceptance(SHARP))


def test_acceptance_is_empty_when_the_issue_has_no_list():
    assert _b().acceptance("## Problem\njust prose\n") == []


# --- a vague list is a PLAN-phase defect, not a test failure -----------------------------------

def test_a_sharp_list_is_actionable():
    ok, _ = _b().actionable(_b().acceptance(SHARP))
    assert ok is True


def test_CONTROL_a_vague_list_is_reported_as_a_PLAN_phase_defect():
    """done_when 3. "Give the breaker only the requirement" assumes the requirement is precise.
    "Add age validation" gives a breaker nothing to probe -- it will either invent noise or go
    looking at the implementation, which is the independence gone. The finding belongs against the
    GOAL at plan time, where it costs one sentence, not against an implementation that may be
    perfectly fine."""
    b = _b()
    ok, reason = b.actionable(b.acceptance(VAGUE))
    assert ok is False
    assert "PLAN-phase defect against the goal" in reason
    assert "not a test failure against the implementation" in reason


def test_no_list_at_all_is_also_a_plan_phase_defect():
    ok, reason = _b().actionable([])
    assert ok is False and "nothing for a breaker to attack" in reason


# --- isolation, by construction ----------------------------------------------------------------

def test_the_brief_has_NO_PARAMETER_for_the_implementation():
    """THE ISOLATION GUARANTEE, asserted against the signature itself rather than against
    behaviour. A maker cannot pass what the function cannot accept -- there is no `diff`,
    `implementation`, `source` or `notes` parameter to persuade anyone to fill in."""
    import inspect
    params = set(inspect.signature(_b().brief).parameters)
    assert params == {"issue_body", "interface"}, params
    assert not (params & {"diff", "implementation", "source", "notes", "maker", "scratch"})


def test_CONTROL_implementation_content_pasted_into_the_list_is_scrubbed_out():
    """The second line of defence. An acceptance list is HUMAN-EDITED text that can quote the code;
    pasting a function body into a done_when bullet would otherwise hand the breaker exactly what
    it must not see, through a channel nobody intended."""
    b = _b()
    leaky = SHARP + "- [ ] def is_valid_age(age): return 0 <= age <= 120\n"
    ok, text = b.brief(leaky, "is_valid_age(age) -> bool")
    assert ok is True
    assert "0 <= age <= 120" not in text
    assert "def is_valid_age" not in text


def test_CONTROL_a_maker_attempting_to_reach_the_breaker_has_no_available_channel():
    """judged_when 2, TESTED rather than asserted. A maker's diff, its notes, and a shared scratch
    path are each offered to the brief through every route available, and none of them arrives."""
    b = _b()
    makers_diff = ("diff --git a/src/age.py b/src/age.py\n"
                   "@@ -1 +1 @@\n"
                   "-def is_valid_age(age): return True\n"
                   "+def is_valid_age(age): return 0 <= age <= 120\n")
    scratch = "/tmp/shared-maker-scratch"
    ok, text = b.brief(SHARP + makers_diff + "\n- [ ] see %s for my notes\n" % scratch,
                       "is_valid_age(age) -> bool")
    assert ok is True
    clean, findings = b.no_channel(text, scratch_paths=[scratch])
    assert clean, findings
    assert "diff --git" not in text
    assert "return 0 <= age <= 120" not in text


def test_no_channel_DETECTS_a_leak_when_one_is_present():
    """The detector must be able to fail, or it is decoration. Fed a brief that genuinely contains
    implementation content and a scratch path, it reports both."""
    b = _b()
    dirty = "ACCEPTANCE:\n- ok\ndef is_valid_age(age):\nsee /tmp/shared for notes\n"
    clean, findings = b.no_channel(dirty, scratch_paths=["/tmp/shared"])
    assert clean is False
    assert len(findings) == 2


def test_the_brief_tells_the_breaker_it_has_not_seen_the_implementation():
    ok, text = _b().brief(SHARP, "is_valid_age(age) -> bool")
    assert ok and text.startswith("You have NOT seen the implementation")
    assert "is_valid_age(age) -> bool" in text


def test_a_vague_goal_never_produces_a_brief_at_all():
    """The plan-phase defect short-circuits: no breaker is dispatched against an unattackable
    goal, so the cost is not paid and the noise is not generated."""
    ok, reason = _b().brief(VAGUE, "whatever")
    assert ok is False and "PLAN-phase defect" in reason

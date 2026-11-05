"""#2139 A: the auto-unpark sweep holds a blocked-only goal that names an explicit blocker only on explicit findings.

A goal carrying `sdlc:blocked` (and not `sdlc:parked`) whose title, body or trusted comments name at least one blocker
in an explicit form (a `Blocked by` marker, `depends on`, ...) is lifted once every explicit blocker has closed, even
when a weak word (`needs`, `after`, `requires`, `waiting on`) beside an unrelated open issue is in its text. A goal
naming only weak references is still held while one is open. The parked path is unchanged. `resolved[n]`, the audit
comment and the decision record name only the refs that really closed. `compute_unpark_actions(..., why_out=)` names
why a goal was not lifted, with a closed token, and changes nothing else.

Every fixture text that names an issue is built with `%d`. The sweep runs over the `test_auto_unpark` fake runner
(no network) from a temporary working directory, as that module's own tests do.

Controls (each clause alone, then restored; see `.sdlc/evidence/2139-controls-part1.md`): every live finding holds
(no confident filter); "names an explicit ref" computed from the live findings only; the confident filter applied
with no explicit ref named; the blocked-only condition dropped; `resolved[n] = sorted(refs)` restored; the reused
rule's OR across matches weakened to the first match; `why_out` not filled at one `continue` (each token alone).
"""
import inspect
import json
import re

import pytest

import test_auto_unpark as TA
import test_auto_unpark_dag as TAD
import test_decision_record as TDR
import test_wait_for_it_resume_1549 as TW
from test_auto_unpark import _sweep_state_in_tmp  # noqa: F401  (autouse: the sweep's state lands under tmp)

WEAK_WORDS = ("needs", "after", "requires", "waiting on")
WHERE = ("title", "body", "comment")
GOAL, EXPLICIT, WEAK = 42, 7, 9


def _au():
    return TA._mod("auto_unpark")


def _weak(word, n=WEAK):
    return "FYI this %s #%d eventually, not now" % (word, n)


def _explicit(n=EXPLICIT):
    return "Blocked by #%d" % n


def _shape(word, where):
    """-> (issue, comments) for goal 42: a `Blocked by` marker for 7 in its body, and `<word> #9` at `where`."""
    weak = _weak(word)
    title = "Widget export" + (" (%s)" % weak if where == "title" else "")
    body = _explicit() + "\n" + (weak + "\n" if where == "body" else "")
    comments = {str(GOAL): [weak]} if where == "comment" else {}
    return TA._blocked_issue(GOAL, title=title, body=body), comments


def _compute(issues, states, comments=None, config=None, sdlc=".sdlc", **kw):
    au = _au()
    run = TA._sweep_runner(states=states, comments=comments or {})
    cfg = config or TA._config()
    src = au.sources.GitHubSource(cfg, run=run)
    return au.compute_unpark_actions(sdlc, cfg, src, issues, run=run, **kw)


def _closed_open():
    return {str(EXPLICIT): "CLOSED", str(WEAK): "OPEN"}


def _why(issues, states, comments=None, **kw):
    """-> the `why_out` the sweep fills for `issues` (an assertion, never a TypeError, when it has no such parameter)."""
    assert "why_out" in inspect.signature(_au().compute_unpark_actions).parameters, "no why_out parameter"
    why = {}
    _compute(issues, states, comments, why_out=why, **kw)
    return why


# --- 1 -- an explicit blocker closed lifts the goal, whatever weak word names an open issue ----------------------------

@pytest.mark.parametrize("where", WHERE)
@pytest.mark.parametrize("word", WEAK_WORDS, ids=lambda w: w.replace(" ", "-"))
def test_an_explicit_blocker_closed_lifts_the_goal_whatever_weak_word_names_an_open_issue(word, where, capsys):
    issue, comments = _shape(word, where)
    actions, resolved = _compute([issue], _closed_open(), comments)
    assert [(a["action"], a["issue"]) for a in actions] == [("swap-label", str(GOAL))], (word, where, actions)
    assert actions[0]["remove"] == ["sdlc:blocked"] and "sdlc:goal" in actions[0]["add"]
    assert resolved == {str(GOAL): [str(EXPLICIT)]}, resolved
    if (word, where) == ("needs", "comment"):
        # the module's own documented gesture: `auto_unpark.py sweep <sdlc_dir> --apply`
        au = _au()
        run = TA._sweep_runner(parked=json.dumps([issue]), states=_closed_open(), comments=comments)
        assert au.sweep_cmd(".sdlc", TA._config(), ["--apply"], run=run) == 0
        out = capsys.readouterr().out
        assert re.search(r"\[done\] #%d: " % GOAL, out), out


# --- 2 -- a goal naming only weak refs is still held while one is open ---------------------------------------------------

def test_a_goal_naming_only_weak_refs_is_held_while_one_is_open_and_lifted_once_it_closes():
    issue = TA._blocked_issue(GOAL, body=_weak("needs") + "\n")
    assert _compute([issue], {str(WEAK): "OPEN"}) == ([], {})
    actions, resolved = _compute([issue], {str(WEAK): "CLOSED"})
    assert [a["action"] for a in actions] == ["swap-label"] and resolved == {str(GOAL): [str(WEAK)]}
    assert _why([issue], {str(WEAK): "OPEN"}) == {str(GOAL): {"why": "open", "open": [str(WEAK)]}}


# --- 3 -- resolved, the audit comment and the decision record name only closed refs -------------------------------------

def test_resolved_and_the_audit_comment_name_only_the_refs_that_closed(tmp_path):
    issue, comments = _shape("needs", "comment")
    _actions, resolved = _compute([issue], _closed_open(), comments)
    assert resolved == {str(GOAL): [str(EXPLICIT)]}, resolved

    au = _au()
    run = TA._sweep_runner(parked=json.dumps([issue]), states=_closed_open(), comments=comments)
    result = au.sweep_unpark(".sdlc", TA._config(), apply=True, run=run)
    assert result["unparked"] == [str(GOAL)], result
    posted = [c[c.index("--body") + 1] for c in run.calls if c[:2] == ["issue", "comment"] and c[2] == str(GOAL)]
    assert len(posted) == 1, posted
    assert re.search(r"#%d\b" % EXPLICIT, posted[0]) and not re.search(r"#%d\b" % WEAK, posted[0]), posted[0]

    # with decision records on (the path `test_decision_record` drives), the record's text names only #7 too
    sd = tmp_path / "records"
    sd.mkdir()
    run = TA._label_aware_sweep_runner(by_label={"sdlc:parked": "[]", "sdlc:blocked": json.dumps([issue])},
                                       states=_closed_open(), comments=comments)
    res = au.sweep_unpark(str(sd), TDR._on(TA._config()), apply=True, run=run)
    assert res["unparked"] == [str(GOAL)], res
    ids = TDR._ids(sd)
    assert len(ids) == 1, ids
    text = (sd / "decisions" / (ids[0] + ".json")).read_text()
    assert re.search(r"#%d\b" % EXPLICIT, text) and not re.search(r"#%d\b" % WEAK, text), text
    body = TDR._bodies(run, GOAL)
    assert body and not any(re.search(r"#%d\b" % WEAK, b) for b in body), body


# --- 4 -- the parked path is unchanged ---------------------------------------------------------------------------------

def test_the_parked_path_is_unchanged_by_the_explicit_rule(tmp_path):
    """A `sdlc:parked` goal with the #1549 waiting note, a closed explicit ref and a weak open one stays parked: a
    park is resumed only once every ref it names has closed, weak ones included, exactly as before."""
    notes = TW._applied_note(tmp_path)
    issue = TA._parked_issue(GOAL, body=_explicit() + "\n")
    held = _compute([issue], _closed_open(), {str(GOAL): [TW.PARK] + notes + [_weak("needs")]})
    assert held == ([], {}), held
    assert _why([issue], _closed_open(), {str(GOAL): [TW.PARK] + notes + [_weak("needs")]}) == \
        {str(GOAL): {"why": "open", "open": [str(WEAK)]}}
    # the fixture is live: without the weak comment the same park IS resumed (the waiting note applies)
    actions, resolved = _compute([issue], _closed_open(), {str(GOAL): [TW.PARK] + notes})
    assert [a["action"] for a in actions] == ["swap-label"] and resolved == {str(GOAL): [str(EXPLICIT)]}


# --- 5 -- a ref named weakly first and explicitly later still holds ----------------------------------------------------

def test_a_ref_named_weakly_first_and_explicitly_later_still_holds_while_open():
    """The reused rule is an OR across every match of a ref: #5 is named weakly in the title and explicitly in the
    body, so it is an explicit blocker and holds while open, even though the goal's other explicit blocker (7)
    closed. Weakening that OR to the first match lifts the goal: this is the control of the reused rule."""
    issue = TA._blocked_issue(GOAL, title="Widget export (%s)" % _weak("needs", 5),
                              body="%s\n%s\n" % (_explicit(5), _explicit(EXPLICIT)))
    assert _compute([issue], {"5": "OPEN", str(EXPLICIT): "CLOSED"}) == ([], {})
    assert _why([issue], {"5": "OPEN", str(EXPLICIT): "CLOSED"}) == {str(GOAL): {"why": "open", "open": ["5"]}}
    actions, resolved = _compute([issue], {"5": "CLOSED", str(EXPLICIT): "CLOSED"})
    assert [a["action"] for a in actions] == ["swap-label"] and resolved == {str(GOAL): ["5", str(EXPLICIT)]}


# --- 6 -- why_out names the reason and changes nothing ----------------------------------------------------------------

def test_why_out_names_the_reason_and_changes_nothing(tmp_path):
    """One goal per `continue` of `compute_unpark_actions`, and one that is lifted (absent from `why_out`). The value
    is `{"why": <token>}`, plus `"open": [refs]` (numbers only) for `open`."""
    au = _au()
    assert "why_out" in inspect.signature(au.compute_unpark_actions).parameters, "no why_out parameter"
    qkind = TA._mod("qkind")
    TAD._waiting_map(tmp_path, 56, 8)                       # the stored map holds 56 back on open 8
    issues = [TA._blocked_issue(51, body="No recorded blocker.\n"),
              TA._blocked_issue(52, body=_explicit(WEAK) + "\n"),
              TA._blocked_issue(53, body=_explicit() + "\n"),
              TA._blocked_issue(54, body=_explicit() + "\n"),
              TA._parked_issue(55, body=_explicit() + "\n"),
              TA._blocked_issue(56, body=_explicit(8) + "\n"),
              TA._blocked_issue(57, body=_explicit() + "\n")]
    comments = {"53": [au.keep_parked_comment("on purpose")],
                "54": ["parked\n" + qkind.render_line("scope_hold")]}
    states = {str(EXPLICIT): "CLOSED", str(WEAK): "OPEN", "8": "OPEN"}
    cfg = TAD._cfg("on")
    plain = _compute(issues, states, comments, config=cfg, sdlc=str(tmp_path))
    why = {}
    got = _compute(issues, states, comments, config=cfg, sdlc=str(tmp_path), why_out=why)
    assert got == plain, "why_out changed the result"
    assert got[1] == {"57": [str(EXPLICIT)]}, got[1]
    assert why == {"51": {"why": "no-ref"},
                   "52": {"why": "open", "open": [str(WEAK)]},
                   "53": {"why": "exempt"},
                   "54": {"why": "gate"},
                   "55": {"why": "parked"},
                   "56": {"why": "map"}}, why

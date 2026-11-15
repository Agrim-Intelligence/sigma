"""Pure move from tests/test_followup_track_routing.py at 4042e97 (#2224 amendment 2): the guard nodes (pass at base); test bodies byte-identical.

`handoff.py track` routes every filing through `followup_route.route` (#2224).

Run: python3 -m pytest tests/test_followup_track_routing*.py
(generic form: python -m pytest tests/test_followup_track_routing*.py)

Driven through the documented gesture, `handoff.main(["handoff.py", "track", ...])`.
CONTROLS: C13/C14 test_every_kind_and_blocks_route, C15 test_target_unit_does_not_buy_a_separate_goal, C16
test_separate_filing_carries_the_reason_marker, C17 test_parent_comment_names_the_reason, C18
test_ambiguous_goal_refuses_exit_2, C24 test_undeclared_unit_name_inherits_with_warning, C25
test_body_file_marker_must_be_open_registered, C26 test_label_unit_must_be_open_registered, C27
test_goal_mismatch_refuses_exit_2, C32 test_item_ambiguous_refuses_exit_2, C37
test_unproven_child_and_chain_prerequisite_refused_without_naming_a_lever (A5: no lever is named).
RESIDUAL (not closable in code): an agent that writes a registered open unit's `Feature:` marker into its
`--body-file` buys `different-feature`; the registry check and goal binding narrow it, they do not remove it.

"""
import json
import pathlib

import pytest

import test_handoff as th

handoff = th.handoff
fr = handoff._followup_route()
feature_registry = th.feature_registry
CFG = dict(th.ON, followups={"mode": "in-goal"})
BASE = ["--area", "engine", "--why", "found while here", "--queue", "queued", "--assignee", "same-area"]


class UnitSource(th.FakeSource):
    def __init__(self, body="Feature: alpha\n", labels=(), fail=None):
        super().__init__(number="61")
        self.goal_issue = {"body": body, "labels": list(labels)}
        self.fail = fail

    def fetch_body_labels(self, goal):
        if self.fail:
            raise RuntimeError(self.fail)
        return self.goal_issue


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "_duplicate_search", lambda *a, **k: {"schema": "brainstorm-dedup/v1",
                                                                      "candidates": [], "degraded": []})
    monkeypatch.chdir(tmp_path)

    def build(source=None, config=CFG, units=("alpha", "beta"), closed=()):
        src = source or UnitSource()
        monkeypatch.setattr(handoff.sources, "get_source", lambda *a, **k: src)
        sdlc = th._project(tmp_path, config)
        entries = {n: {"title": "t", "owner": None, "open": n not in closed, "parent": None,
                       "tracking_issue": None, "repos": {}} for n in units}
        feature_registry.write_index(feature_registry.registry_dir(sdlc), entries)
        return sdlc, src
    return build


def track(sdlc, *extra, goal="7", blocks="no", body=None, tmp=None):
    args = ["handoff.py", "track", str(sdlc), goal, *BASE, "--blocks", blocks, *extra]
    if body is not None:
        f = pathlib.Path(sdlc).parent / "body.md"
        f.write_text(body)
        args += ["--body-file", str(f)]
    return handoff.main(args)


def test_decompose_instructions_do_not_name_the_lever():
    """C54: a decompose goal is never told to set `followups.mode: file` (it is a repo-wide switch for every goal)."""
    dg = handoff._load_sibling("decompose_goal")
    text = (pathlib.Path(handoff.__file__).parent / "decompose_goal.py").read_text(encoding="utf-8")
    for body in (text, dg._META_BODY, dg.render_meta_body(9, 8)):
        assert "followups.mode" not in body and "mode: file" not in body
    assert "OPEN CONFLICT" not in text


def test_under_cap_same_feature_is_refused(make):
    sdlc, src = make(config=dict(th.ON, followups={"mode": "in-goal", "max_added_slices": 2}))
    fr.append_slice(sdlc, "7", "a", "s")
    assert track(sdlc) == 3


def test_target_unit_does_not_buy_a_separate_goal(make):
    sdlc, src = make()
    assert track(sdlc, "--target-unit", "beta") == 3 and src.created is None


def test_body_file_marker_must_be_open_registered(make):
    sdlc, src = make(units=("alpha",))
    assert track(sdlc, body="x\n\nFeature: beta\n") == 3


def test_body_file_marker_naming_a_closed_unit_inherits(make):
    sdlc, src = make(units=("alpha", "beta"), closed=("beta",))
    assert track(sdlc, body="x\n\nFeature: beta\n") == 3


def test_label_unit_must_be_open_registered(make):
    sdlc, src = make(units=("alpha",))
    assert track(sdlc, "--label", "feature:beta") == 3


def test_body_wins_over_label(make):
    sdlc, src = make()
    assert track(sdlc, "--label", "feature:beta", body="x\n\nFeature: alpha\n") == 3


def test_item_with_no_declared_feature_is_a_slice(make):
    sdlc, src = make()
    assert track(sdlc) == 3


def test_a_nogoal_filing_is_untouched_under_file_mode(make, tmp_path, monkeypatch):
    sdlc, src = make(config=dict(th.ON, followups={"mode": "file"}))
    tree = tmp_path / "trees" / "B"
    tree.mkdir(parents=True)
    rec = sdlc / "state" / "work" / "B.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"worktree": str(tree)}))
    monkeypatch.chdir(tree)
    handoff.create_tracked_issue(sdlc, dict(th.ON, followups={"mode": "file"}), None, "engine", "why", same_area=True,
                                 immediately_actionable=False, blocks_goal=False, source=src, title="t")
    assert src.created is not None


def test_file_mode_files_as_before_with_no_marker(make):
    sdlc, src = make(config=dict(th.ON, followups={"mode": "file"}))
    assert track(sdlc) == 0 and "followup-reason" not in src.created["body"]


# --- #2224 amendment A2-A4: a PROVEN decompose child is exempt from routing; nothing else is ----------------------
# Driven through `handoff.main(["handoff.py", "track", ...])`. The meta-goal M is #21 decomposing parent #9.
# CONTROLS: C42 test_track_has_no_exemption_bypass, C43 test_child_exempt_kind_only_child, C44 test_exempt_needs_meta_marker,
# C45 test_exempt_needs_parent_comment, C46 test_exempt_needs_child_marker_same_parent, C47 test_exempt_needs_independent_plan_review
# (and the route / plan-file clauses beside it), C48 test_exempt_fails_closed_on_unreadable_leg, C49 test_exempt_child_cap,
# C50 test_other_kinds_and_blocks_yes_stay_routed, C51 test_unknown_mode_warns_once_naming_the_value /
# test_absent_or_valid_mode_is_silent, C52 test_warning_is_printed_once_from_track, C53 test_followup_mode_verb_is_silent_and_unchanged,
# C58 test_exempt_still_binds_the_goal_to_the_worktree (R4), C59 test_exempt_goal_spellings (R6).
M, P = "21", "9"
CHILD_BODY = "<!-- sigma:decomposed-from=#9 -->\nBlocked by #3\n\nchild content\n"
DCFG = dict(th.ON, followups={"mode": "in-goal"}, action_log={"enabled": True})


class DecomposeSource(th.FakeSource):
    """A source whose goal #21 is a decompose meta-goal of #9 (all four legs readable); each fact is switchable."""

    def __init__(self, m_body="<!-- sigma:decompose-of=#9 -->\nDecompose #9\n", m_labels=("sdlc:goal", "sdlc:decompose"),
                 comments=("Too large. <!-- sigma:decompose-filed=#21 -->",)):
        super().__init__(number="61")
        self.m_body, self.m_labels, self.comments = m_body, [{"name": n} for n in m_labels], list(comments)
        self.fail_body = self.fail_comments = None
        self.comment_reads = []
        self._next = 61

    def create_dependency(self, title, body, assignee, labels=(), goal_label=True):
        self.created = {"title": title, "body": body, "assignee": assignee, "labels": list(labels), "goal_label": goal_label}
        self._next += 1
        return str(self._next - 1)

    def fetch_body_labels(self, goal):
        if self.fail_body:
            raise RuntimeError(self.fail_body)
        return {"body": self.m_body, "labels": self.m_labels}

    def fetch_comments_strict(self, goal):
        self.comment_reads.append(str(goal))
        if self.fail_comments:
            raise RuntimeError(self.fail_comments)
        return {"comments": [{"body": c} for c in self.comments], "labels": []}


def prove(sdlc, goal=M, route="subagent", agent="rev1", plan=True, approving=True, plan_end=True):
    """Write the plan and the action-log rows `phase_gate.status` reads for the meta-goal's plan and plan-review."""
    import hashlib
    plan_file = pathlib.Path(sdlc) / "plans" / f"{goal}.md"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    if plan:
        plan_file.write_text("# Plan\n")
    sha = hashlib.sha256(b"# Plan\n").hexdigest()
    rows = ([{"kind": "phase", "phase": "plan", "state": "end", "agent_id": "a2"}] if plan_end else []) + [
        {"kind": "phase", "phase": "plan_review", "state": "end", "agent_id": agent},
        {"kind": "verdict", "phase": "plan_review", "verdict": "pass" if approving else "fail", "route": route,
         "agent_id": agent, "plan_hash": sha}]
    log = pathlib.Path(sdlc) / "state" / "log" / f"{goal}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def dmake(make):
    def build(source=None, config=DCFG, proven=True, **prove_kw):
        src = source or DecomposeSource()
        sdlc, _ = make(source=src, config=config)
        if proven:
            prove(sdlc, **prove_kw)
        return sdlc, src
    return build


def child(sdlc, *extra, goal=M, blocks="no", body=CHILD_BODY):
    return track(sdlc, "--kind", "child", *extra, goal=goal, blocks=blocks, body=body)


@pytest.mark.parametrize("spelling", ["21", "#21", "21.md"])
def test_exempt_goal_spellings(dmake, spelling):
    """R6: one digits-only parse for every spelling of the goal; the comment marker is compared by equality."""
    sdlc, src = dmake()
    assert child(sdlc, goal=spelling) == 0 and src.created is not None


def test_exempt_does_not_depend_on_the_phase_record_gate_flag(dmake):
    """R7: `gates.phase_record` is not read by `status()`; the action log being on is what makes the facts readable."""
    sdlc, src = dmake(config=dict(DCFG, gates={"phase_record": {"enabled": False}}))
    assert child(sdlc) == 0


def warn_lines(err):
    return [l for l in err.splitlines() if "followups.mode" in l and "ignor" in l]


@pytest.mark.parametrize("followups", [None, {}, {"max_added_slices": 3}, {"mode": None}, {"mode": "in-goal"},
                                       {"mode": "file"}])
def test_absent_or_valid_mode_is_silent(make, capsys, followups):
    cfg = dict(th.ON) if followups is None else dict(th.ON, followups=followups)
    sdlc, src = make(config=cfg)
    track(sdlc)
    assert warn_lines(capsys.readouterr().err) == []
    assert handoff.followup_mode_warning(cfg) is None


def test_create_tracked_issue_does_not_warn(make, capsys):
    sdlc, src = make(config=dict(th.ON, followups={"mode": "File"}))
    handoff.create_tracked_issue(sdlc, dict(th.ON, followups={"mode": "File"}), "7", "engine", "why", same_area=True,
                                 immediately_actionable=False, blocks_goal=False, source=src, title="t")
    assert warn_lines(capsys.readouterr().err) == []

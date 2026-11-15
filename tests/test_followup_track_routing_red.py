"""Pure move from tests/test_followup_track_routing.py at 4042e97 (#2224 amendment 2): the wholly assertion-red nodes; test bodies byte-identical.

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


@pytest.mark.parametrize("kind", ["followup", "prerequisite", "cleanup", "child"])
@pytest.mark.parametrize("blocks", ["yes", "no"])
def test_every_kind_and_blocks_route(make, kind, blocks, capsys):
    sdlc, src = make()
    assert track(sdlc, "--kind", kind, blocks=blocks) == handoff.FOLLOWUP_REFUSED_EXIT
    err = capsys.readouterr().err
    assert src.created is None and "work.py add-slice" in err


@pytest.mark.parametrize("kind", ["child", "prerequisite"])
def test_unproven_child_and_chain_prerequisite_refused_without_naming_a_lever(make, capsys, kind):
    sdlc, src = make()
    assert track(sdlc, "--kind", kind) == 3
    err = capsys.readouterr().err
    assert "mode: file" not in err and "work.py add-slice" in err and src.created is None


def test_local_source_goal_reads_no_unit_and_the_refusal_names_add_slice(make, capsys):
    sdlc, src = make(source=th.FakeSource(number="61"))
    assert track(sdlc) == 3 and "work.py add-slice" in capsys.readouterr().err


def test_separate_filing_carries_the_reason_marker(make):
    sdlc, src = make()
    assert track(sdlc, body="why\n\nFeature: beta\n") == 0
    assert "<!-- sigma:followup-reason:different-feature -->" in src.created["body"]
    assert fr.parse_reason(src.created["body"]) == "different-feature"


def test_parent_comment_names_the_reason(make):
    sdlc, src = make()
    assert track(sdlc, body="x\n\nFeature: beta\n") == 0
    assert any("different-feature" in text for goal, text in src.notes if goal == "7")


def test_parent_comment_failure_warns_and_keeps_the_filing(make, capsys):
    sdlc, src = make()

    def boom(goal, text):
        if "SEPARATE" in text:
            raise RuntimeError("no comment")
        src.notes.append((goal, text))
    src.note = boom
    assert track(sdlc, body="x\n\nFeature: beta\n") == 0
    assert src.created is not None and "followup-reason" in src.created["body"]
    assert "could not comment the follow-up reason" in capsys.readouterr().err


def test_at_cap_files_with_slice_cap(make):
    sdlc, src = make(config=dict(th.ON, followups={"mode": "in-goal", "max_added_slices": 2}))
    for n in ("a", "b"):
        fr.append_slice(sdlc, "7", n, "s")
    assert track(sdlc) == 0
    assert fr.parse_reason(src.created["body"]) == "slice-cap"
    assert any("slice-cap" in text for _, text in src.notes)


def test_target_unit_on_a_separate_filing_still_stamps_it(make):
    sdlc, src = make()
    assert track(sdlc, "--target-unit", "beta", body="x\n\nFeature: beta\n") == 0
    assert "Feature: beta" in src.created["body"]


def test_undeclared_unit_name_inherits_with_warning(make, capsys):
    sdlc, src = make()
    assert track(sdlc, body="x\n\nFeature: nowhere\n") == 3
    assert "nowhere" in capsys.readouterr().err


def test_label_naming_an_open_registered_unit_is_a_different_feature(make):
    sdlc, src = make(units=("alpha", "beta"))
    assert track(sdlc, "--label", "feature:beta") == 0
    assert fr.parse_reason(src.created["body"]) == "different-feature"


def test_item_ambiguous_refuses_exit_2(make):
    sdlc, src = make()
    assert track(sdlc, body="Feature: alpha\n\nFeature: beta\n") == 2 and src.created is None


def test_ambiguous_goal_refuses_exit_2(make):
    sdlc, src = make(source=UnitSource(body="Feature: alpha\n\nFeature: beta\n"))
    assert track(sdlc) == 2 and src.created is None


def test_goal_read_error_refuses_exit_2(make):
    sdlc, src = make(source=UnitSource(fail="gh is down"))
    assert track(sdlc) == 2 and src.created is None


def test_goal_with_no_unit_and_a_declaring_item_is_a_different_feature(make):
    sdlc, src = make(source=UnitSource(body="nothing here\n"))
    assert track(sdlc, body="x\n\nFeature: beta\n") == 0


def test_goal_mismatch_refuses_exit_2(make, tmp_path, monkeypatch):
    sdlc, src = make()
    tree = tmp_path / "trees" / "B"
    tree.mkdir(parents=True)
    rec = sdlc / "state" / "work" / "B.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"worktree": str(tree)}))
    monkeypatch.chdir(tree)
    assert track(sdlc, goal="A") == 2 and src.created is None
    assert track(sdlc, goal="B") == 3                      # naming the bound goal routes normally


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


def refused(sdlc, src, capsys, leg, **kw):
    assert child(sdlc, **kw) == handoff.FOLLOWUP_REFUSED_EXIT, leg
    err = capsys.readouterr().err
    assert src.created is None and src.notes == [], leg
    assert "[%s]" % leg in err and "work.py add-slice" in err and "mode: file" not in err, (leg, err)


def test_proven_child_is_filed_with_no_marker_no_comment_and_recorded(dmake):
    sdlc, src = dmake()
    assert child(sdlc) == 0
    assert src.created is not None and "followup-reason" not in src.created["body"]
    assert not any("followup-reason" in text or "SEPARATE" in text for _, text in src.notes)
    assert fr.read_record(sdlc, M)["children"] == ["61"]
    assert src.comment_reads == [P]                       # P's comments are read once per track call


def test_child_exempt_kind_only_child(dmake, capsys):
    """C43: proof present, every other kind is routed (refused here: same feature) and `child` with no proof is refused."""
    sdlc, src = dmake()
    for kind in ("prerequisite", "cleanup", "followup"):
        assert track(sdlc, "--kind", kind, goal=M, body=CHILD_BODY) == 3 and src.created is None, kind
    assert src.notes == [] and src.comment_reads == []      # never even read the exemption's facts for another kind


def test_child_with_no_proof_is_refused(dmake, capsys):
    sdlc, src = dmake(proven=False)
    refused(sdlc, src, capsys, "plan")


def test_other_kinds_and_blocks_yes_stay_routed(dmake, capsys):
    """C50: with full proof, `--blocks yes` (kind child too) and the other kinds each exit 3 with nothing filed."""
    sdlc, src = dmake()
    assert child(sdlc, blocks="yes") == 3 and src.created is None
    for kind in ("prerequisite", "cleanup", "followup"):
        for blocks in ("yes", "no"):
            assert track(sdlc, "--kind", kind, goal=M, blocks=blocks, body=CHILD_BODY) == 3, (kind, blocks)
    assert src.created is None and src.notes == []
    assert track(sdlc, goal=M, body=CHILD_BODY) == 3 and src.created is None        # no --kind at all


@pytest.mark.parametrize("body", ["intro\n<!-- sigma:decompose-of=#9 -->\n", "Decompose #9\n",
                                  "<!-- sigma:decompose-of=#21 -->\n"])
def test_exempt_needs_meta_marker(dmake, capsys, body):
    sdlc, src = dmake(source=DecomposeSource(m_body=body))
    refused(sdlc, src, capsys, "meta-marker")


def test_exempt_needs_the_decompose_label(dmake, capsys):
    sdlc, src = dmake(source=DecomposeSource(m_labels=("sdlc:goal",)))
    refused(sdlc, src, capsys, "meta-label")


@pytest.mark.parametrize("comments", [(), ("<!-- sigma:decompose-filed=#22 -->",), ("decomposed into #61",)])
def test_exempt_needs_parent_comment(dmake, capsys, comments):
    sdlc, src = dmake(source=DecomposeSource(comments=comments))
    refused(sdlc, src, capsys, "parent-comment")


@pytest.mark.parametrize("body", ["child content\n", "<!-- sigma:decomposed-from=#8 -->\nx\n",
                                  "x\n<!-- sigma:decomposed-from=#9 -->\n"])
def test_exempt_needs_child_marker_same_parent(dmake, capsys, body):
    sdlc, src = dmake()
    refused(sdlc, src, capsys, "child-marker", body=body)


def test_exempt_needs_a_child_body_file(dmake, capsys):
    sdlc, src = dmake()
    assert child(sdlc, body=None) == 3 and src.created is None                       # no --body-file at all
    assert "[child-marker]" in capsys.readouterr().err


def test_exempt_needs_independent_plan_review(dmake, capsys):
    sdlc, src = dmake(approving=False)
    refused(sdlc, src, capsys, "plan-review")


def test_inline_plan_review_gets_no_exemption_and_says_so(dmake, capsys):
    """R1: an inline-route verdict (the maker judged its own plan) is not an independent review."""
    sdlc, src = dmake(route="inline")
    refused(sdlc, src, capsys, "plan-review-route")


def test_subagent_plan_review_without_an_agent_id_gets_no_exemption(dmake, capsys):
    sdlc, src = dmake(agent="")                              # `phase_gate.status` itself refuses the row first
    refused(sdlc, src, capsys, "plan-review")


def test_absent_plan_file_fails_closed(dmake, capsys):
    """R2: phase_gate hashes the plan only when it exists, so an absent file would otherwise read as approved."""
    sdlc, src = dmake(plan=False)
    refused(sdlc, src, capsys, "plan")


def test_missing_plan_row_fails_closed(dmake, capsys):
    sdlc, src = dmake(plan_end=False)
    refused(sdlc, src, capsys, "plan")


def _raise(*a, **k):
    raise RuntimeError("corrupt log")


@pytest.mark.parametrize("leg,setup", [
    ("parent-comment", lambda s, sdlc, m: setattr(s, "fail_comments", "gh is down")),
    ("gate", lambda s, sdlc, m: m.setattr(fr, "_status", _raise)),
    ("gate", lambda s, sdlc, m: (sdlc / "config.json").write_text(json.dumps(dict(DCFG, action_log={"enabled": False})))),
    ("child-cap", lambda s, sdlc, m: (sdlc / "state" / "followups").mkdir(parents=True, exist_ok=True) or
     (sdlc / "state" / "followups" / "21.json").write_text('{"added": [], "children": "x"}')),
])
def test_exempt_fails_closed_on_unreadable_leg(dmake, capsys, monkeypatch, leg, setup):
    """C48: each unreadable fact refuses, and the refusal names the leg: `[unreadable:<leg>]`."""
    sdlc, src = dmake()
    setup(src, sdlc, monkeypatch)
    assert child(sdlc) == 3 and src.created is None
    assert "[unreadable:%s]" % leg in capsys.readouterr().err


def test_an_unreadable_meta_goal_files_nothing(dmake, capsys):
    """The goal read fails: no leg is provable, and routing itself refuses (exit 2) for the same missing fact."""
    sdlc, src = dmake()
    src.fail_body = "gh is down"
    assert child(sdlc) == 2 and src.created is None and src.comment_reads == []


def test_a_non_list_comments_read_fails_closed(dmake, capsys):
    sdlc, src = dmake()
    src.fetch_comments_strict = lambda goal: {"comments": "oops", "labels": []}
    assert child(sdlc) == 3 and "[unreadable:parent-comment]" in capsys.readouterr().err and src.created is None


def test_a_source_without_comment_reads_fails_closed(dmake, capsys):
    sdlc, src = dmake(source=th.FakeSource(number="61"))
    assert child(sdlc) == 3 and src.created is None
    assert "[unreadable:meta-marker]" in capsys.readouterr().err


def test_exempt_child_cap(dmake, capsys):
    """C49: `goal_decompose.max_children` children recorded for M -> the next is refused naming child-cap."""
    sdlc, src = dmake(config=dict(DCFG, goal_decompose={"max_children": 2}))
    assert child(sdlc) == 0
    assert child(sdlc) == 0
    capsys.readouterr()
    src.created, src.notes = None, []
    refused(sdlc, src, capsys, "child-cap")
    assert fr.read_record(sdlc, M)["children"] == ["61", "62"]


def test_exempt_still_binds_the_goal_to_the_worktree(dmake, tmp_path, monkeypatch, capsys):
    """R4: the exemption skips `gather_facts`, so it must run the same cwd binding itself."""
    sdlc, src = dmake()
    tree = tmp_path / "trees" / "7"
    tree.mkdir(parents=True)
    rec = sdlc / "state" / "work" / "7.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"worktree": str(tree)}))
    monkeypatch.chdir(tree)
    assert child(sdlc) == 2 and src.created is None
    assert "goal 7" in capsys.readouterr().err
    assert src.comment_reads == []                          # refused before any read of the goal's facts


def test_exempt_goal_spelling_of_another_goal_is_refused(dmake, capsys):
    sdlc, src = dmake()
    assert child(sdlc, goal="#22") == 3 and "[parent-comment]" in capsys.readouterr().err


def test_track_has_no_exemption_bypass(dmake, monkeypatch, capsys):
    """C42: no flag, env var or config key buys the exemption for an unproven child."""
    import re
    source = pathlib.Path(handoff.__file__).read_text(encoding="utf-8")
    branch = source[source.index('argv[1] == "track"'):source.index('argv[1] == "followup-mode"')]
    flags = set(re.findall(r'flags\.get\("([a-z-]+)"', branch)) | set(re.findall(r'"([a-z-]+)" in flags', branch))
    assert flags == {"area", "why", "queue", "assignee", "blocks", "kind", "needs-person", "body-file", "label",
                     "priority", "title", "target-unit"}, flags
    for var in ("SIGMA_DECOMPOSE_EXEMPT", "SIGMA_FOLLOWUP_MODE", "FOLLOWUP_EXEMPT", "SIGMA_FOLLOWUPS_MODE"):
        monkeypatch.setenv(var, "1")
    cfg = dict(DCFG, goal_decompose={"enabled": True, "mode": "file", "max_children": 99, "exempt": True},
               followups={"mode": "in-goal", "exempt_children": True, "decompose": True})
    sdlc, src = dmake(config=cfg, proven=False)
    for extra in ((), ("--label", "sdlc:decompose"), ("--needs-person", "reviewer_down"), ("--target-unit", "alpha")):
        assert child(sdlc, *extra) == 3 and src.created is None
    capsys.readouterr()


def warn_lines(err):
    return [l for l in err.splitlines() if "followups.mode" in l and "ignor" in l]


@pytest.mark.parametrize("value", ["File", True, "off", "in_goal", ["file"], 7])
def test_unknown_mode_warns_once_naming_the_value(make, capsys, value):
    sdlc, src = make(config=dict(th.ON, followups={"mode": value}))
    assert track(sdlc) == 3                                  # read as in-goal: a same-feature find is refused
    lines = warn_lines(capsys.readouterr().err)
    assert len(lines) == 1 and repr(value) in lines[0] and "in-goal" in lines[0], lines


def test_warning_is_printed_once_from_track(make, capsys):
    """C52: a filing that routes then files (different feature) still prints one line, not one per code path."""
    sdlc, src = make(config=dict(th.ON, followups={"mode": "File"}))
    assert track(sdlc, body="x\n\nFeature: beta\n") == 0 and src.created is not None
    assert len(warn_lines(capsys.readouterr().err)) == 1


def test_followup_mode_verb_is_silent_and_unchanged(make, capsys):
    """C53: the verb's stdout contract is line 1 the mode, line 2 the limit; a bad value adds nothing to either stream."""
    sdlc, _ = make(config=dict(th.ON, followups={"mode": "File", "max_added_slices": 4}))
    assert handoff.main(["handoff.py", "followup-mode", str(sdlc)]) == 0
    out = capsys.readouterr()
    assert out.out == "in-goal\nmax_added_slices 4\n" and out.err == ""

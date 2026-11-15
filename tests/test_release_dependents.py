"""#2223: `release_dependents.py` -- when goal N closes, drop `sdlc:blocked` from the open goals that name it
once EVERY dependency they name is closed. The verb reuses `auto_unpark.compute_unpark_actions` (the sweep's
own decision code) and filters what comes back; it never resumes a park, never adds a label, never removes
`sdlc:in-progress`, and any read failure releases nothing for the goal it touched.

All sources here are in-memory fakes (no `gh`, no network). Every fresh-map fixture is first asserted to
pass `dag_pick._entry`, so a stale fixture fails loudly instead of silently taking the listing path."""
import importlib.util
import json
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_auto_unpark_dag as dagt  # noqa: E402  (its _store/_node build a map the picker accepts)

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rd = _mod("release_dependents")
au = rd.auto_unpark
sources = au.sources
backlog_check = au.backlog_check
dag_pick = _mod("dag_pick")


def _need():
    """Test-first precondition: the verb exists. A named assertion, so the red before the feature is an assertion
    red (not an AttributeError, which the `loop.py red` gate does not count)."""
    assert hasattr(rd, "release") and hasattr(rd, "release_candidates"), "release_dependents.release is missing (#2223)"


class FakeSource:
    """A `GitHubSource`-shaped in-memory repo. `calls` is the ordered record of every read and write, so a
    test can assert a SEQUENCE (N read before any write), not mere membership. `fail` names reads that
    raise: ("list",), ("read", "40"), ("comments", "40"), ("state", "13")."""
    goal_label, goal_blocked_label, parked_label = "sdlc:goal", "sdlc:blocked", "sdlc:parked"
    in_progress_label, blocking_label = "sdlc:in-progress", "sdlc:blocking"
    blocked_label = None
    board_active = False
    col = {"ready": "Ready"}
    repo = "acme/widget"
    sdlc_dir = None

    def __init__(self, issues, fail=()):
        self.issues = {int(k): dict(v) for k, v in issues.items()}
        self.fails = set(fail)
        self.calls = []

    # -- helpers -------------------------------------------------------------------------------------
    def _labels(self, n):
        return list(self.issues[int(n)].get("labels", []))

    def writes(self, n=None):
        out = [c for c in self.calls if c[0] in ("swap", "note", "remove_label", "card")]
        return [c for c in out if n is None or c[1] == int(n)]

    def _boom(self, *key):
        if tuple(key) in self.fails:
            raise RuntimeError("simulated failure: %r" % (key,))

    # -- reads ---------------------------------------------------------------------------------------
    def _read_issue(self, goal, fields, comment_limit=None):
        n = int(str(goal).lstrip("#"))
        self.calls.append(("read", n, tuple(fields)))
        self._boom("read", str(n))
        it = self.issues[n]
        if set(fields) <= {"state", "stateReason", "labels"}:
            self._boom("state", str(n))
        out = {"number": n, "title": it.get("title", ""), "body": it.get("body", ""),
               "state": it.get("state", "OPEN").upper(), "stateReason": it.get("stateReason") or (
                   "COMPLETED" if it.get("state", "OPEN").upper() == "CLOSED" else None),
               "labels": [{"name": l} for l in self._labels(n)], "updatedAt": it.get("updatedAt", "2026-01-01T00:00:00Z"),
               "closedAt": it.get("closedAt")}
        out["comments"] = [{"body": b, "authorAssociation": a, "createdAt": "2026-01-01T00:00:00Z"}
                           for b, a in it.get("comments", [])]
        return {k: out[k] for k in fields if k in out}

    def _list_issues(self, fields, labels=(), state="open", cap=None, sort="created", direction="desc"):
        self.calls.append(("list", tuple(labels), state, cap))
        self._boom("list")
        rows = [n for n, it in sorted(self.issues.items(), reverse=True)
                if it.get("state", "OPEN").upper() == "OPEN" and set(labels) <= set(it.get("labels", []))]
        rows = rows[:cap] if cap is not None else rows
        return [{k: v for k, v in self._read_row(n).items() if k in fields} for n in rows]

    def _read_row(self, n):
        it = self.issues[n]
        return {"number": n, "title": it.get("title", ""), "body": it.get("body", ""),
                "labels": [{"name": l} for l in self._labels(n)],
                "updatedAt": it.get("updatedAt", "2026-01-01T00:00:00Z")}

    def fetch_comments_strict(self, goal):
        n = int(goal)
        self.calls.append(("comments", n))
        self._boom("comments", str(n))
        raw = [{"body": b, "authorAssociation": a, "author": {"login": "x"}}
               for b, a in self.issues[n].get("comments", [])]
        return {"comments": sources.trusted_marker_comments(raw, n), "labels": []}

    # -- writes --------------------------------------------------------------------------------------
    def _swap_labels(self, goal, add=(), remove=()):
        n = int(goal)
        self.calls.append(("swap", n, tuple(add), tuple(remove)))
        labels = self._labels(n)
        self.issues[n]["labels"] = [l for l in labels if l not in remove] + [l for l in add if l not in labels]

    def _set_board_status(self, goal, status):
        self.calls.append(("card", int(goal), status))
        return True

    def note(self, goal, text):
        self.calls.append(("note", int(goal), text))

    def _issue_remove_label(self, number, label):
        self.calls.append(("remove_label", int(number), label))
        self.issues[int(number)]["labels"] = [l for l in self._labels(number) if l != label]

    def _run(self, args):
        raise RuntimeError("no gh in tests")


def _issue(body="", labels=("sdlc:goal", "sdlc:blocked"), state="OPEN", comments=(), **kw):
    return {"body": body, "labels": list(labels), "state": state, "comments": list(comments), **kw}


def _cfg(mode=None, **extra):
    cfg = {"discovery": {"source": "github", "github": {"repo": "acme/widget"},
                         "auto_unpark": {"interval_minutes": 0}}}
    if mode is not None:
        cfg["discovery"]["dag"] = {"mode": mode}
    cfg["discovery"].update(extra)
    return cfg


def _world(extra=None):
    """12 is the goal that just closed; 13 closed earlier; 14 is still open."""
    base = {12: _issue(state="CLOSED", labels=("sdlc:blocking",)), 13: _issue(state="CLOSED", labels=()),
            14: _issue(labels=("sdlc:goal",))}
    base.update(extra or {})
    return base


def _release(tmp_path, issues, goals=(12,), apply=True, fail=(), cfg=None, **kw):
    src = FakeSource(issues, fail=fail)
    res = rd.release(str(tmp_path), cfg or _cfg(), list(goals), apply=apply, source=src, **kw)
    return src, res


def _released(src):
    return sorted(c[1] for c in src.calls if c[0] == "swap")


# ---------------------------------------------------------------------------------------- AC-1: the verb

def test_releases_blocked_goal_when_every_dependency_closed(tmp_path):
    _need()
    src, res = _release(tmp_path, _world({
        40: _issue("blocked by #12"),
        41: _issue("blocked by #12, blocked by #13")}))
    assert _released(src) == [40, 41]
    assert sorted(res["released"]) == ["40", "41"] and res["status"] == "ok"
    assert [l for l in src._labels(40)] == ["sdlc:goal"]


def test_goal_with_another_open_dependency_is_left_exactly_as_it_was(tmp_path):
    _need()
    src, res = _release(tmp_path, _world({
        40: _issue("blocked by #12"),
        42: _issue("blocked by #12, blocked by #14", labels=("sdlc:goal", "sdlc:blocked", "sdlc:in-progress"))}))
    assert _released(src) == [40]
    assert src.writes(42) == []
    assert sorted(src._labels(42)) == ["sdlc:blocked", "sdlc:goal", "sdlc:in-progress"]
    assert "42" not in res["released"]


def test_refuses_when_closed_goal_is_not_confirmed_closed(tmp_path):
    _need()
    world = _world({40: _issue("blocked by #12")})
    world[12] = _issue(labels=("sdlc:blocking",))                       # still open: a lagging read
    src, res = _release(tmp_path, world)
    assert src.writes() == [] and res["released"] == []


def test_map_nominated_candidate_not_naming_n_still_reads_n_first(tmp_path):
    """The candidate's text names only #13 (closed), so nothing in `compute_unpark_actions` reads N: only the
    verb's own N read stops a release while N is still open."""
    _need()
    world = _world({40: _issue("blocked by #13", labels=("sdlc:goal", "sdlc:blocked"))})
    world[12] = _issue(labels=("sdlc:blocking",))                       # N is OPEN
    dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(12, "open", "goal"), _mapnode(13, "closed", "goal")],
                [(12, 40), (13, 40)])
    cfg = _cfg("on")
    assert dag_pick._entry(str(tmp_path), cfg, {}, time.time())[0] is not None     # fixture precondition
    src, res = _release(tmp_path, world, cfg=cfg)
    assert src.writes() == [] and res["released"] == []
    assert ("read", 12, ("state", "stateReason", "labels")) in src.calls


def test_n_is_read_before_any_write_and_no_blocking_strip_while_n_open(tmp_path):
    _need()
    src, _ = _release(tmp_path, _world({40: _issue("blocked by #12")}))
    kinds = [c[0] for c in src.calls]
    n_read = next(i for i, c in enumerate(src.calls) if c[0] == "read" and c[1] == 12)
    assert n_read < kinds.index("swap") < kinds.index("note") < kinds.index("remove_label")
    # N still open: nothing is stripped from it
    world = _world({40: _issue("blocked by #12")})
    world[12] = _issue(labels=("sdlc:blocking",))
    src2, _ = _release(tmp_path, world)
    assert [c for c in src2.calls if c[0] == "remove_label"] == []


def test_blocking_label_stripped_from_the_closed_goal_only_when_closed(tmp_path):
    _need()
    src, _ = _release(tmp_path, _world({40: _issue("blocked by #12")}))
    assert ("remove_label", 12, "sdlc:blocking") in src.calls
    assert src._labels(12) == []
    src2, _ = _release(tmp_path, _world({40: _issue("blocked by #12")}), apply=False)
    assert [c for c in src2.calls if c[0] == "remove_label"] == []                # dry run writes nothing


# --------------------------------------------------------------------- the filters (traps 2 and 3)

def _parked(waiting=True, with_blocked=False):
    labels = ("sdlc:parked", "sdlc:blocked") if with_blocked else ("sdlc:parked",)
    comments = [("waiting for it <!-- %s -->" % au.WAITING_MARKER, "OWNER")] if waiting else []
    return _issue("blocked by #12", labels=labels, comments=comments)


@pytest.mark.parametrize("shape", ["parked", "parked+blocked"])
def test_parked_goal_is_never_resumed(tmp_path, shape, monkeypatch):
    """Each fixture is parked, carries a `sigma:waiting-on-dependency` note and has every dependency closed,
    so `compute_unpark_actions` WOULD resume it if it were passed in: only the candidate filter stops it."""
    _need()
    world = _world({50: _parked(with_blocked=(shape == "parked+blocked"))})
    monkeypatch.setattr(au, "_reparked_by_label_after_note", lambda *a, **k: False)
    # the premise: the sweep's own decision does resume this issue
    probe = FakeSource(world)
    acts, _ = au.compute_unpark_actions(str(tmp_path), _cfg(), probe, probe._list_issues(
        ["number", "title", "body", "labels", "updatedAt"], labels=["sdlc:parked"]), run=None, use_map=False,
        reads=rd._StrictComments(str(tmp_path), probe))
    if shape == "parked":
        assert any(a["issue"] == "50" for a in acts), "fixture is not eligible: the control would be vacuous"
    src, res = _release(tmp_path, world)
    assert src.writes(50) == [] and "50" not in res["released"]


def test_parked_goal_with_a_label_only_repark_after_the_note_is_never_resumed(tmp_path, monkeypatch):
    _need()
    monkeypatch.setattr(au, "_reparked_by_label_after_note", lambda *a, **k: True)
    src, _ = _release(tmp_path, _world({50: _parked()}))
    assert src.writes(50) == []


def test_release_candidates_is_the_one_filter():
    _need()
    src = FakeSource({})
    rows = [{"number": 1, "labels": [{"name": "sdlc:blocked"}]},
            {"number": 2, "labels": [{"name": "sdlc:blocked"}, {"name": "sdlc:parked"}]},
            {"number": 3, "labels": [{"name": "sdlc:parked"}]},
            {"number": 4, "labels": [{"name": "sdlc:goal"}]}]
    assert [r["number"] for r in rd.release_candidates(src, rows)] == [1]


def test_keep_parked_marker_is_honoured(tmp_path):
    _need()
    marker = ("on purpose " + au.keep_parked_comment("on purpose"), "OWNER")
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12", comments=[marker])}))
    assert src.writes(40) == [] and "40" not in res["released"]


def test_only_blocked_is_removed_and_in_progress_is_kept(tmp_path):
    _need()
    src, _ = _release(tmp_path, _world({
        40: _issue("blocked by #12", labels=("sdlc:goal", "sdlc:blocked", "sdlc:in-progress"))}))
    assert [c for c in src.calls if c[0] == "swap"] == [("swap", 40, (), ("sdlc:blocked",))]
    assert "sdlc:in-progress" in src._labels(40)


def test_no_label_is_ever_added(tmp_path):
    _need()
    src, _ = _release(tmp_path, _world({40: _issue("blocked by #12")}))
    assert all(c[2] == () for c in src.calls if c[0] == "swap")


def test_legacy_blocked_goal_without_goal_label_adds_nothing(tmp_path):
    _need()
    src, _ = _release(tmp_path, _world({40: _issue("blocked by #12", labels=("sdlc:blocked",))}))
    assert [c for c in src.calls if c[0] == "swap"] == [("swap", 40, (), ("sdlc:blocked",))]
    assert src._labels(40) == []                 # released into NO pool, by design (docs say so)


def test_swap_detail_is_regenerated_for_remove_only(tmp_path):
    _need()
    actions = []
    real = rd.triage.apply_actions

    def spy(source, acts, apply):
        actions.extend(acts)
        return real(source, acts, apply)

    rd.triage.apply_actions, saved = spy, rd.triage.apply_actions
    try:
        _release(tmp_path, _world({
            40: _issue("blocked by #12", labels=("sdlc:blocked", "sdlc:in-progress"))}))
    finally:
        rd.triage.apply_actions = saved
    swap = [a for a in actions if a["action"] == "swap-label"][0]
    assert swap["detail"] == "-sdlc:blocked" and swap["add"] == [] and swap["remove"] == ["sdlc:blocked"]


def test_board_card_moves_to_ready_when_board_active(tmp_path):
    _need()
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    src.board_active = True
    rd.release(str(tmp_path), _cfg(), [12], apply=True, source=src)
    assert ("card", 40, "Ready") in src.calls


def test_stale_map_cannot_block_the_release(tmp_path):
    """`discovery.dag.mode: on` and a fresh map that still stores #12 as open: the sweep's map skip would
    hold #40; the release path must not."""
    _need()
    dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(12, "open", "goal")], [(12, 40)])
    cfg = _cfg("on")
    assert dag_pick._entry(str(tmp_path), cfg, {}, time.time())[0] is not None
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12")}), cfg=cfg)
    assert res["released"] == ["40"]


def test_map_only_open_blocker_other_than_n_still_holds_the_goal(tmp_path):
    """#2223 review finding 1: the map may say N is open (stale) but must still HOLD a goal on ANOTHER open
    blocker it records (an edge the goal's own text does not name: the sweep in mode `on` would hold it)."""
    _need()
    dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(12, "open", "goal"),
                           _mapnode(50, "open", "goal")], [(12, 40), (50, 40)])
    cfg = _cfg("on")
    assert dag_pick._entry(str(tmp_path), cfg, {}, time.time())[0] is not None
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12"), 50: _issue(labels=("sdlc:goal",))}),
                        cfg=cfg)
    assert res["released"] == [] and res["held"] == 1
    assert src.writes(40) == []


def test_map_open_blocker_other_than_n_holds_even_when_text_names_only_closed_goals(tmp_path):
    """Same rule on the `--all` pass: N is not a parameter there, and a map-only open edge still holds."""
    _need()
    dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(13, "closed"),
                           _mapnode(50, "open", "goal")], [(13, 40), (50, 40)])
    cfg = _cfg("on")
    assert dag_pick._entry(str(tmp_path), cfg, {}, time.time())[0] is not None
    src, res = _release(tmp_path, _world({40: _issue("blocked by #13"), 50: _issue(labels=("sdlc:goal",))}),
                        goals=(), cfg=cfg, all_blocked=True)
    assert res["released"] == [] and src.writes(40) == []


def test_blocked_equals_goal_blocked_label_is_refused(tmp_path):
    _need()
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    src.blocked_label = "sdlc:blocked"
    res = rd.release(str(tmp_path), _cfg(), [12], apply=True, source=src)
    assert res["status"] == "refused" and src.calls == []


# --------------------------------------------------------------------- every read leg fails closed

@pytest.mark.parametrize("leg", ["list", "issue-read", "comment", "ref-state", "n-state"])
def test_any_read_failure_releases_nothing(tmp_path, leg):
    _need()
    world = _world({40: _issue("blocked by #12, blocked by #13")})
    fail, kw = {"list": [("list",)], "comment": [("comments", "40")], "ref-state": [("state", "13")],
                "n-state": [("state", "12")], "issue-read": [("read", "40")]}[leg], {}
    cfg = _cfg()
    if leg == "issue-read":                         # an issue read only happens on the map path
        dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(12, "closed", "goal"),
                               _mapnode(13, "closed", "goal")], [(12, 40), (13, 40)])
        cfg = _cfg("on")
        assert dag_pick._entry(str(tmp_path), cfg, {}, time.time())[0] is not None
    src, res = _release(tmp_path, world, fail=fail, cfg=cfg, **kw)
    assert src.writes(40) == [] and res["released"] == []
    assert res["status"] == "incomplete" or leg == "ref-state"


def test_comment_outage_releases_nothing(tmp_path):
    """The `sigma:keep-parked` marker lives in a comment: a failed comment read must not hide it."""
    _need()
    marker = (au.keep_parked_comment("on purpose"), "OWNER")
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12", comments=[marker])}),
                        fail=[("comments", "40")])
    assert src.writes(40) == [] and res["incomplete"] == ["40"]


def test_a_failing_goal_does_not_cost_the_others(tmp_path):
    _need()
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12"), 41: _issue("blocked by #12")}),
                        fail=[("comments", "40")])
    assert _released(src) == [41] and res["incomplete"] == ["40"]


def test_untrusted_commenter_prose_is_ignored(tmp_path):
    """#709: a stranger's "blocked by #14" (open) neither holds the goal nor is read as a blocker."""
    _need()
    stranger = ("blocked by #14", "NONE")
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12", comments=[stranger])}))
    assert res["released"] == ["40"]


def test_strict_reader_scrubs_and_never_reuses_a_failed_read(tmp_path):
    _need()
    src = FakeSource(_world({40: _issue("x", comments=[("hello", "OWNER")])}), fail=[("comments", "40")])
    reads = rd._StrictComments(str(tmp_path), src)
    assert reads.get({}, {"number": 40}, {"ref": "40"}) == [] and reads.failed == {"40"}
    src.fails.clear()
    reads2 = rd._StrictComments(str(tmp_path), src)
    assert reads2.get({}, {"number": 40}, {"ref": "40"}) == ["hello"] and reads2.failed == set()


# ------------------------------------------------------------------------------ the audit note

def test_audit_note_names_the_closed_goal_and_feeds_no_blocker_ref(tmp_path):
    _need()
    src, _ = _release(tmp_path, _world({40: _issue("blocked by #12")}))
    note = [c[2] for c in src.calls if c[0] == "note"][0]
    assert "#12" in note and "sdlc:blocked" in note and note != au._UNPARK_COMMENT
    goal_doc = {"ref": "40", "raw": ""}
    assert backlog_check._referenced_blocker_refs(goal_doc, note) == set()
    assert rd.blocker_scan.extract_refs(note) == []


def test_all_pass_audit_note_has_no_dangling_blocker_reference(tmp_path):
    """Finding 2: `--all` has no closed goal to name; the note must read as a sentence, not `: #its blocker`."""
    _need()
    src, res = _release(tmp_path, _world({40: _issue("blocked by #13")}), goals=(), all_blocked=True)
    assert res["released"] == ["40"]
    note = [c[2] for c in src.calls if c[0] == "note"][0]
    assert "#its" not in note and "#" not in note and "sdlc:blocked" in note
    assert rd.blocker_scan.extract_refs(note) == [] and note != au._UNPARK_COMMENT


def test_a_dependent_closed_since_the_map_was_built_is_skipped(tmp_path):
    """Finding 3: the map nominates 40 as blocked, but 40 has since closed (still carrying sdlc:blocked)."""
    _need()
    dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(12, "closed")], [(12, 40)])
    cfg = _cfg("on")
    assert dag_pick._entry(str(tmp_path), cfg, {}, time.time())[0] is not None
    world = _world({40: _issue("blocked by #12", state="CLOSED")})
    src, res = _release(tmp_path, world, cfg=cfg)
    assert res["released"] == [] and src.writes(40) == [] and res["held"] == 1


def test_land_shaped_call_only_ever_writes_to_dependents_of_n(tmp_path):
    """Finding 8: a `--land`-shaped call (scoped to goals=[N]) writes only to N's dependents and N itself."""
    _need()
    world = _world({40: _issue("blocked by #12"), 41: _issue("blocked by #14"),
                    42: _issue("blocked by #13"), 43: _issue("blocked by #12\nblocked by #14")})
    src, res = _release(tmp_path, world, goals=(12,))
    assert res["released"] == ["40"]                       # a real release happened, so the check below bites
    dependents = {40, 43}
    assert src.writes() and {c[1] for c in src.writes()} <= dependents | {12}


def test_dry_run_writes_nothing(tmp_path):
    _need()
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12")}), apply=False)
    assert src.writes() == [] and res["would_release"] == ["40"] and res["released"] == []


def test_second_run_is_a_no_op(tmp_path):
    _need()
    src = FakeSource(_world({40: _issue("blocked by #12")}))
    rd.release(str(tmp_path), _cfg(), [12], apply=True, source=src)
    before = len(src.writes())
    res = rd.release(str(tmp_path), _cfg(), [12], apply=True, source=src)
    assert len(src.writes()) == before and res["released"] == []


# ---------------------------------------------------------------------------------- AC-2 candidates

def _mapnode(number, state="open", life="goal"):
    return {"number": number, "kind": "issue", "state": state, "life": life, "prio": None, "in_corpus": True}


def test_fresh_map_makes_no_list_call(tmp_path):
    _need()
    dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(12, "closed")], [(12, 40)])
    cfg = _cfg("on")
    assert dag_pick._entry(str(tmp_path), cfg, {}, time.time())[0] is not None
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12")}), cfg=cfg)
    assert [c for c in src.calls if c[0] == "list"] == [] and res["released"] == ["40"]


def test_fresh_map_candidates_come_from_reverse_edges(tmp_path):
    """Only goals whose node is `blocked` and that the map says depend on #12 are read."""
    _need()
    dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(41, "open", "goal"),
                           _mapnode(42, "open", "blocked"), _mapnode(12, "closed")], [(12, 40), (12, 41)])
    cfg = _cfg("on")
    assert dag_pick._entry(str(tmp_path), cfg, {}, time.time())[0] is not None
    src, _ = _release(tmp_path, _world({40: _issue("blocked by #12"), 41: _issue("blocked by #12"),
                                          42: _issue("blocked by #12")}), cfg=cfg)
    assert sorted({c[1] for c in src.calls if c[0] == "read" and c[1] not in (12, 13, 14)}) == [40]


def test_a_dependent_absent_from_the_fresh_map_is_still_released_by_the_all_pass(tmp_path):
    _need()
    dagt._store(tmp_path, [_mapnode(12, "closed")], [])
    cfg = _cfg("on")
    assert dag_pick._entry(str(tmp_path), cfg, {}, time.time())[0] is not None
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12")}), goals=(), cfg=cfg, all_blocked=True)
    assert res["released"] == ["40"]


@pytest.mark.parametrize("how", ["missing", "stale", "no-check", "mode-off", "corrupt"])
def test_stale_or_unusable_map_takes_the_listing_path(tmp_path, how):
    _need()
    if how == "stale":
        dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(12, "closed")], [(12, 40)],
                    built=dagt.NOW - 4 * 3600, checked=dagt.NOW - 3 * 3600)
    elif how == "no-check":
        dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(12, "closed")], [(12, 40)], checked=None)
    elif how == "mode-off":
        dagt._store(tmp_path, [_mapnode(40, "open", "blocked"), _mapnode(12, "closed")], [(12, 40)])
    elif how == "corrupt":
        d = pathlib.Path(tmp_path) / "state"
        d.mkdir(parents=True, exist_ok=True)
        (d / "dag.json").write_text("{not json")
    cfg = _cfg("off" if how == "mode-off" else "on")
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12")}), cfg=cfg)
    assert len([c for c in src.calls if c[0] == "list"]) == 1 and res["released"] == ["40"]


def test_listing_is_one_bounded_call_with_the_probe_row(tmp_path):
    _need()
    src, _ = _release(tmp_path, _world({40: _issue("blocked by #12")}))
    lists = [c for c in src.calls if c[0] == "list"]
    assert lists == [("list", ("sdlc:blocked",), "open", rd.CEILING + 1)]


def _many(n_rows, start=100):
    return {start + i: _issue("blocked by #12") for i in range(n_rows)}


def test_exactly_the_ceiling_says_nothing_and_one_more_says_one_plus_not_checked(tmp_path):
    _need()
    src, res = _release(tmp_path, _world(_many(rd.CEILING)))
    assert res["not_checked"] == "" and len(res["released"]) == rd.CEILING
    src, res = _release(tmp_path, _world(_many(rd.CEILING + 1)))
    assert res["not_checked"] == "1+ not checked"
    assert len(res["released"]) == rd.CEILING                      # 101 rows listed, 100 read
    assert "1+ not checked" in rd.render(res) and "OLDEST" in rd.render(res)


def test_prefilter_skips_goals_that_do_not_name_the_closed_goal(tmp_path):
    _need()
    src, res = _release(tmp_path, _world({40: _issue("blocked by #12"), 41: _issue("blocked by #13"),
                                            42: _issue("blocked by #122")}))
    assert res["released"] == ["40"]
    assert not any(c[0] == "comments" and c[1] in (41, 42) for c in src.calls)


def test_comment_only_edge_is_found_by_all_mode_not_scoped(tmp_path):
    _need()
    world = _world({40: _issue("(see comments)", comments=[("blocked by #12", "OWNER")])})
    _, scoped = _release(tmp_path, world)
    assert scoped["released"] == []
    _, every = _release(tmp_path, world, goals=(), all_blocked=True)
    assert every["released"] == ["40"]


def test_all_mode_checks_every_blocked_goal_with_same_filters(tmp_path):
    _need()
    world = _world({40: _issue("blocked by #12"), 41: _issue("blocked by #14"), 50: _parked(),
                      51: _issue("blocked by #13", labels=("sdlc:goal", "sdlc:blocked", "sdlc:in-progress"))})
    src, res = _release(tmp_path, world, goals=(), all_blocked=True)
    assert sorted(res["released"]) == ["40", "51"] and src.writes(41) == [] and src.writes(50) == []
    assert [c for c in src.calls if c[0] == "swap" and c[1] == 51] == [("swap", 51, (), ("sdlc:blocked",))]


# ------------------------------------------------------------------------------------- AC-4: CLI

def _cli(tmp_path, world, argv, cfg=None, capsys=None):
    src = FakeSource(world)
    d = pathlib.Path(tmp_path)
    (d / "config.json").write_text(json.dumps(cfg or _cfg()))
    code = rd.main(["release_dependents.py", str(d), *argv], source=src)
    return src, code


def test_cli_is_a_dry_run_without_apply(tmp_path):
    _need()
    src, code = _cli(tmp_path, _world({40: _issue("blocked by #12")}), ["12"])
    assert code == 0 and src.writes() == []
    src, code = _cli(tmp_path, _world({40: _issue("blocked by #12")}), ["12", "--apply"])
    assert _released(src) == [40]


def test_cli_accepts_several_goals_in_one_run(tmp_path, capsys):
    _need()
    world = _world({40: _issue("blocked by #12"), 41: _issue("blocked by #13")})
    src, code = _cli(tmp_path, world, ["12", "13", "--apply"])
    assert _released(src) == [40, 41]
    assert len(capsys.readouterr().out.strip().splitlines()) == 1        # one summary line to relay


def test_cli_usage_error_exits_two(tmp_path, capsys):
    _need()
    src, code = _cli(tmp_path, _world(), [])
    assert code == 2 and src.calls == []


def test_cli_cap_flag_raises_the_listing_ceiling(tmp_path):
    _need()
    src, code = _cli(tmp_path, _world(_many(rd.CEILING + 20)), ["12", "--apply", "--cap", "150"])
    assert len(_released(src)) == rd.CEILING + 20
    assert [c for c in src.calls if c[0] == "list"][0][3] == 151


def test_mode_off_gates_unattended_callers_but_not_the_cli(tmp_path):
    _need()
    cfg = _cfg()
    cfg["discovery"]["auto_unpark"]["mode"] = "off"
    world = _world({40: _issue("blocked by #12")})
    src, res = _release(tmp_path, world, cfg=cfg, unattended=True)
    assert src.calls == [] and res["status"] == "skipped"
    src, res = _release(tmp_path, world, cfg=cfg, unattended=False)
    assert res["released"] == ["40"]


def test_quota_deferral_skips_unattended_only(tmp_path, monkeypatch):
    _need()
    monkeypatch.setattr(rd.gh_quota, "rest_deferral", lambda config=None, **k: "below the reserve")
    world = _world({40: _issue("blocked by #12")})
    src, res = _release(tmp_path, world, unattended=True)
    assert src.calls == [] and res["status"] == "deferred"
    src, res = _release(tmp_path, world, unattended=False)
    assert res["released"] == ["40"]


def test_local_goals_mode_is_a_no_op(tmp_path):
    _need()
    src, res = _release(tmp_path, _world(), cfg={"discovery": {"source": "local-goals"}})
    assert src.calls == [] and res["status"] == "skipped"


def test_deadline_stops_starting_goals_and_reports_not_checked(tmp_path):
    _need()
    clock = iter([0.0, 5.0])      # one candidate fits the budget, the rest do not
    last = [0.0]

    def tick():
        last[0] = next(clock, last[0])
        return last[0]

    world = _world({40: _issue("blocked by #12"), 41: _issue("blocked by #12"), 42: _issue("blocked by #12")})
    src, res = _release(tmp_path, world, deadline=1.0, clock=tick)
    assert res["released"] != [] and len(res["released"]) < 3
    assert res["not_checked"] and "not checked" in rd.render(res)


def test_progress_callback_runs_after_each_goal_and_state_is_kill_safe(tmp_path):
    _need()
    seen = []
    world = _world({40: _issue("blocked by #12"), 41: _issue("blocked by #12")})
    _release(tmp_path, world, progress=lambda r: seen.append(list(r["released"])))
    assert [sorted(x) for x in seen] == [["41"], ["40", "41"]]      # newest first, like the listing


def test_gh_ratchet_no_direct_gh_text_in_the_module():
    _need()
    text = (S / "release_dependents.py").read_text(encoding="utf-8")
    assert "def release(" in text                  # precondition: a stub module must not pass vacuously
    assert '"gh"' not in text and "'gh'" not in text

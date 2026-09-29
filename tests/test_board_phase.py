"""#233: the loop mirrors Priority and Phase onto the board's own fields.

Hermetic: every gh call is answered by `boardfake.GitHub` (one in-memory GitHub, the same fake #235's
board tests use), so these drive the REAL `phase_report.py start` hook and the REAL
`sources.GitHubSource` card path against a board shaped like a hand-made one. No network, and no
mutation of real GitHub: the acceptance run the issue describes against a live board is an owner
runbook in `docs/board-fields.md`, not something this suite (or the goal that wrote it) executes.
"""
import importlib.util
import json
import os
import pathlib

import boardfake

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP = ROOT / "skills" / "agrim-loop" / "scripts"


def _load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, LOOP / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pr = _load("phase_report", "phase_report_for_233")
src_mod = _load("sources", "sources_for_233")

COLS = ("Backlog", "Ready", "In Progress", "QC", "Done", "Blocked", "Parked")
TOKENS = [t for _k, t in pr.PHASE_TOKENS]
PHASES = [k for k, _t in pr.PHASE_TOKENS]


def _status_field(tag="17"):
    return {"id": "PVTSSF_status_%s" % tag, "name": "Status",
            "options": [{"id": "s_%s" % c.replace(" ", "_").lower(), "name": c, "color": "GRAY",
                         "description": ""} for c in COLS]}


def _world(extra_fields=(), number=17, labels=("sdlc:goal", "priority:P1"), carded=True):
    """A HAND-MADE board (never created by Sigma): Status carries our columns, nothing else unless
    `extra_fields` says so. Issue #11 is a goal with `labels`, carded in In Progress."""
    gh = boardfake.GitHub(owner="acme", repo="widget")
    gh.add_board("sigma — SDLC", number=number,
                 fields=[{"id": "PVTF_title", "name": "Title", "options": None,
                          "data_type": "title"}, _status_field()] + [dict(f) for f in extra_fields])
    board = gh.board(number=number)
    if carded:
        it = gh.add_item(board, 11, Status="In Progress")
        it["labels"] = list(labels)          # `gh project item-list` carries an issue card's labels
    gh.issues = [{"number": 11, "labels": [{"name": n} for n in labels], "title": "t", "body": ""}]
    return gh, board


def _cfg(**project):
    p = {"enabled": True, "owner": "acme", "number": 17}
    p.update(project)
    return {"discovery": {"source": "github",
                          "github": {"repo": "acme/widget", "goal_label": "sdlc:goal",
                                     "project": p}}}


def _sdlc(tmp_path, cfg=None):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir(exist_ok=True)
    (sdlc / "config.json").write_text(json.dumps(cfg or _cfg()), encoding="utf-8")
    return sdlc


def _card(board, n=11):
    return next(i for i in board["items"] if i["content"]["number"] == n)


def _start(sdlc, phase, monkeypatch, gh, goal="11"):
    """The DOCUMENTED gesture's own entry point (`phase_report.py start <sdlc> <goal> <phase>
    --model M`), in-process so the fake runner can stand in for `gh`."""
    monkeypatch.setattr(pr, "_BOARD_RUN", gh.loop_run)
    monkeypatch.setattr(pr, "BOARD_RETRY_BASE", 0)
    return pr.cmd_start([str(sdlc), goal, phase, "--model", "sonnet"])


def _field_writes(gh, name):
    board = gh.board(number=17)
    fid = (gh.field(board, name) or {}).get("id")
    return [c for c in gh.calls if c[:2] == ["project", "item-edit"]
            and c[c.index("--field-id") + 1] == fid]


# ---------------------------------------------------------------- acceptance on the fake


def test_one_goal_through_p2_to_p7_advances_phase_and_priority_matches_label(
        tmp_path, monkeypatch, capsys):
    gh, board = _world()
    sdlc = _sdlc(tmp_path)
    seen = []
    for phase in PHASES[1:]:                                     # P2 RESEARCH .. P7 RETRO
        assert _start(sdlc, phase, monkeypatch, gh) == 0
        seen.append(_card(board).get("phase"))
    assert seen == TOKENS[1:]
    assert _card(board).get("priority") == "P1"
    # both fields were created ONCE, single-select, with the fixed shared vocabulary
    assert gh.option_names(board, "Phase") == TOKENS
    assert gh.option_names(board, "Priority") == ["P0", "P1", "P2", "P3", "P4"]
    creates = [c for c in gh.mutations() if any("createProjectV2Field(" in str(a) for a in c)]
    assert len(creates) == 2
    assert _card(board).get("status") == "In Progress"           # Status untouched
    err = capsys.readouterr().err
    assert "sigma:" not in err, err


def test_a_repeated_start_for_the_same_phase_writes_nothing(tmp_path, monkeypatch):
    gh, board = _world()
    sdlc = _sdlc(tmp_path)
    _start(sdlc, "research", monkeypatch, gh)
    before = len(gh.calls)
    _start(sdlc, "research", monkeypatch, gh)
    again = gh.calls[before:]
    assert not [c for c in again if c[:2] == ["project", "item-edit"]], again
    assert not gh.mutations()[2:]                                 # no further creates either


def test_calls_per_boundary_are_bounded(tmp_path, monkeypatch):
    """Steady state: 3 reads + 1 Phase write. Stated in the plan and docs; measured here."""
    gh, board = _world()
    sdlc = _sdlc(tmp_path)
    _start(sdlc, "research", monkeypatch, gh)
    before = len(gh.calls)
    _start(sdlc, "plan", monkeypatch, gh)
    verbs = [" ".join(c[:2]) for c in gh.calls[before:]]
    assert sorted(verbs) == sorted(["project list", "project field-list", "project item-list",
                                    "project item-edit"]), verbs


def test_unlabelled_goal_leaves_priority_blank_not_p3(tmp_path, monkeypatch):
    """Decision D1 (plan): the kit's default is UNPRIORITISED, not P3 -- writing P3 would reorder
    the board queue and make the field-wins mirror stamp priority labels on every unlabelled goal."""
    gh, board = _world(labels=("sdlc:goal",))
    _start(_sdlc(tmp_path), "research", monkeypatch, gh)
    assert _card(board).get("phase") == "P2 RESEARCH"
    assert _card(board).get("priority") is None
    assert not [c for c in gh.calls if c[:2] == ["issue", "edit"]]


# ---------------------------------------------------------------- a board Sigma did not create


HAND_PHASE = {"id": "PVTSSF_hand_phase", "name": "phase", "options": [
    {"id": "hp_2", "name": "p2 research", "color": "PINK", "description": "theirs"},
    {"id": "hp_x", "name": "Triage", "color": "RED", "description": "a lane they added"},
    {"id": "hp_3", "name": "P3 PLAN", "color": "BLUE", "description": ""}]}
HAND_PRIO = {"id": "PVTSSF_hand_prio", "name": "PRIORITY", "options": [
    {"id": "hq_1", "name": "P1", "color": "ORANGE", "description": "urgent-ish"},
    {"id": "hq_x", "name": "Someday", "color": "GRAY", "description": ""}]}


def test_hand_made_fields_are_adopted_never_renamed_recoloured_or_dropped(
        tmp_path, monkeypatch):
    gh, board = _world(extra_fields=(HAND_PHASE, HAND_PRIO))
    snapshot = json.loads(json.dumps(board["fields"]))
    sdlc = _sdlc(tmp_path)
    _start(sdlc, "research", monkeypatch, gh)
    assert _card(board).get("phase") == "p2 research"          # their spelling, claimed by case
    assert _card(board).get("priority") == "P1"
    _start(sdlc, "plan", monkeypatch, gh)
    assert _card(board).get("phase") == "P3 PLAN"
    assert board["fields"] == snapshot                          # nothing renamed/recoloured/dropped
    assert not gh.mutations()                                   # no create, no option rewrite
    assert not [c for c in gh.calls if c[:2] == ["project", "field-create"]]


def test_a_missing_option_on_a_hand_made_field_warns_once_and_is_never_appended(
        tmp_path, monkeypatch, capsys):
    gh, board = _world(extra_fields=(HAND_PHASE, HAND_PRIO))
    snapshot = json.loads(json.dumps(board["fields"]))
    rc = _start(_sdlc(tmp_path), "implement", monkeypatch, gh)  # HAND_PHASE has no P5 option
    assert rc == 0
    assert board["fields"] == snapshot
    assert _card(board).get("phase") is None
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("sigma:")]
    assert len(lines) == 1 and "P5 IMPLEMENT" in lines[0], lines


def test_a_same_named_field_that_is_not_single_select_is_skipped_with_one_warning(
        tmp_path, monkeypatch, capsys):
    text_phase = {"id": "PVTF_text_phase", "name": "Phase", "options": None, "data_type": "text"}
    gh, board = _world(extra_fields=(text_phase,))
    assert _start(_sdlc(tmp_path), "research", monkeypatch, gh) == 0
    assert [f["name"] for f in board["fields"]].count("Phase") == 1
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("sigma:")]
    assert len(lines) == 1 and "single-select" in lines[0], lines
    assert _card(board).get("priority") == "P1"                 # Priority still mirrored


# ---------------------------------------------------------------- fail-open + controls


def test_control_phase_field_removed_and_uncreatable_pick_still_succeeds_one_warning(
        tmp_path, monkeypatch, capsys):
    """The issue's control: take the Phase field away (and make GitHub refuse to create it). The
    pick still succeeds, the start still exits 0, and exactly ONE warning is printed."""
    gh, board = _world()
    sdlc = _sdlc(tmp_path)
    _start(sdlc, "research", monkeypatch, gh)
    board["fields"] = [f for f in board["fields"] if f["name"] != "Phase"]   # removed by a human
    for it in board["items"]:
        it.pop("phase", None)
    gh.fail["createProjectV2Field"] = "GraphQL: Resource not accessible by integration"
    capsys.readouterr()
    src = src_mod.GitHubSource(_cfg(), run=gh.loop_run, sdlc_dir=str(sdlc))
    src._RETRY_BASE = 0
    gh.issues.append({"number": 12, "labels": [{"name": "sdlc:goal"}], "title": "u", "body": ""})
    src.mark_in_progress("12")                                  # the pick's board write
    assert _card(board, 12).get("status") == "In Progress"
    assert _start(sdlc, "plan", monkeypatch, gh, goal="12") == 0
    out, err = capsys.readouterr()
    assert "PHASE START" in out
    lines = [ln for ln in err.splitlines() if ln.startswith("sigma:")]
    assert len(lines) == 1 and "Phase" in lines[0], lines


def test_removed_phase_field_is_recreated_once(tmp_path, monkeypatch):
    gh, board = _world()
    sdlc = _sdlc(tmp_path)
    _start(sdlc, "research", monkeypatch, gh)
    board["fields"] = [f for f in board["fields"] if f["name"] != "Phase"]
    _start(sdlc, "plan", monkeypatch, gh)
    _start(sdlc, "plan_review", monkeypatch, gh)
    assert [f["name"] for f in board["fields"]].count("Phase") == 1
    assert _card(board).get("phase") == "P4 PLAN-REVIEW"


def test_a_phase_write_failure_never_changes_the_start_outcome(tmp_path, monkeypatch, capsys):
    gh, board = _world()
    sdlc = _sdlc(tmp_path)
    _start(sdlc, "research", monkeypatch, gh)
    clean = capsys.readouterr().out
    gh.fail["item-edit"] = "HTTP 502: Bad Gateway"
    assert _start(sdlc, "research", monkeypatch, gh) == 0       # same phase: dedupe, no write
    gh.boards[0]["items"][0].pop("phase")                       # force a write that will fail
    rc = _start(sdlc, "research", monkeypatch, gh)
    out, err = capsys.readouterr()
    assert rc == 0
    assert out.splitlines()[-2:] == clean.splitlines()          # banner byte-identical
    assert len([ln for ln in err.splitlines() if ln.startswith("sigma:")]) == 1


def test_a_board_that_raises_anything_never_breaks_start(tmp_path, monkeypatch, capsys):
    def explode(_args):
        raise OSError("gh vanished")
    _sdlc(tmp_path)
    monkeypatch.setattr(pr, "_BOARD_RUN", explode)
    assert pr.cmd_start([str(tmp_path / ".sdlc"), "11", "research", "--model", "sonnet"]) == 0
    assert "PHASE START" in capsys.readouterr().out


# ---------------------------------------------------------------- disabled cleanly


def _no_board_calls(gh):
    return not [c for c in gh.calls if c[:1] == ["project"] or c[:2] == ["api", "graphql"]]


def test_disabled_when_project_enabled_is_off(tmp_path, monkeypatch):
    gh, _board = _world()
    _start(_sdlc(tmp_path, _cfg(enabled=False)), "research", monkeypatch, gh)
    assert _no_board_calls(gh)


def test_disabled_when_no_number_is_pinned(tmp_path, monkeypatch):
    gh, _board = _world()
    cfg = _cfg()
    del cfg["discovery"]["github"]["project"]["number"]
    _start(_sdlc(tmp_path, cfg), "research", monkeypatch, gh)
    assert _no_board_calls(gh)


def test_phase_field_false_disables_phase_but_not_priority(tmp_path, monkeypatch):
    gh, board = _world()
    _start(_sdlc(tmp_path, _cfg(phase_field=False)), "research", monkeypatch, gh)
    assert gh.field(board, "Phase") is None
    assert _card(board).get("priority") == "P1"


def test_local_goals_mode_and_non_issue_goals_make_no_calls(tmp_path, monkeypatch):
    gh, _board = _world()
    _start(_sdlc(tmp_path, {"discovery": {"source": "local-goals"}}), "research", monkeypatch, gh)
    assert _no_board_calls(gh)
    other = tmp_path / "b"
    other.mkdir()
    _start(_sdlc(other), "research", monkeypatch, gh, goal="0004-vision")
    assert _no_board_calls(gh)


def test_configured_phase_field_name_is_used(tmp_path, monkeypatch):
    gh, board = _world()
    _start(_sdlc(tmp_path, _cfg(phase_field="SDLC phase")), "review", monkeypatch, gh)
    assert _card(board).get("sdlc phase") == "P6 REVIEW"


def test_an_uncarded_goal_is_never_carded_by_the_phase_hook(tmp_path, monkeypatch):
    """Carding it here would leave a blank-Status card: Status is the status path's job."""
    gh, board = _world(carded=False)
    assert _start(_sdlc(tmp_path), "research", monkeypatch, gh) == 0
    assert not [c for c in gh.calls if c[:2] == ["project", "item-add"]]


# ---------------------------------------------------------------- the Status write carries Priority


def test_a_status_write_also_mirrors_the_moved_goals_priority(tmp_path):
    prio = {"id": "PVTSSF_prio", "name": "Priority", "options": [
        {"id": "p_%d" % i, "name": "P%d" % i, "color": "GRAY", "description": ""}
        for i in range(5)]}
    gh, board = _world(extra_fields=(prio,))
    src = src_mod.GitHubSource(_cfg(), run=gh.loop_run, sdlc_dir=str(_sdlc(tmp_path)))
    src._RETRY_BASE = 0
    src.mark_in_progress("11")
    assert _card(board).get("status") == "In Progress"
    assert _card(board).get("priority") == "P1"


def test_phase_tokens_come_from_the_closed_vocabulary():
    """Imported, never copied: the board options ARE phase_report.PHASE_TOKENS."""
    text = (LOOP / "sources.py").read_text(encoding="utf-8")
    assert "P4 PLAN-REVIEW" not in text
    assert os.path.basename(pr.__file__) == "phase_report.py"


def test_several_failures_in_one_run_still_print_exactly_one_warning(tmp_path, monkeypatch, capsys):
    """Neither field exists and GitHub refuses both creates: two failures, ONE line."""
    gh, board = _world()
    gh.fail["createProjectV2Field"] = "GraphQL: Resource not accessible by integration"
    assert _start(_sdlc(tmp_path), "research", monkeypatch, gh) == 0
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("sigma:")]
    assert len(lines) == 1, lines
    assert gh.field(board, "Phase") is None and gh.field(board, "Priority") is None


def test_a_repo_without_a_pinned_board_spawns_nothing_at_a_phase_start(tmp_path, monkeypatch):
    """The fence `test_phase_report_loads_no_network_capable_sibling_module` relies on, EXECUTED:
    with the real (bounded) runner in place and every subprocess entry point a raiser, a start on
    a repo whose board is off, or on but unpinned, spawns nothing and loads no `sources`."""
    import subprocess

    def boom(*_a, **_k):
        raise AssertionError("the board hook spawned a process")

    for name in ("run", "Popen"):
        monkeypatch.setattr(subprocess, name, boom)
    monkeypatch.setattr(pr, "_BOARD_RUN", None)
    loaded = []
    real_load = pr._load
    monkeypatch.setattr(pr, "_load", lambda n: loaded.append(n) or real_load(n))
    unpinned = _cfg()
    del unpinned["discovery"]["github"]["project"]["number"]
    for i, cfg in enumerate((_cfg(enabled=False), unpinned, {"discovery": {"source": "local-goals"}})):
        d = tmp_path / str(i)
        d.mkdir()
        assert pr.mirror_phase_to_board(str(_sdlc(d, cfg)), "11", "research") is False
    assert "sources" not in loaded, loaded


def test_the_bounded_runner_stops_calling_gh_once_its_budget_is_spent(monkeypatch):
    import subprocess

    def boom(*_a, **_k):
        raise AssertionError("spawned past the budget")

    monkeypatch.setattr(subprocess, "run", boom)
    run = pr._bounded_gh(budget_s=0)
    try:
        run(["project", "list"])
    except RuntimeError as exc:
        assert "budget" in str(exc)
    else:
        raise AssertionError("a spent budget must raise")

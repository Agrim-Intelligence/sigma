"""#280: a board's Status option spelled differently from `project.columns` must never make a card
move a silent no-op.

Read-only on 2026-09-29 (`.sdlc/research/280.md`): real default-shaped boards carry BOTH GitHub
spellings, `In progress` (one titled with the loop's own `<repo> — SDLC`) and `In Progress`, while
`project.columns.in_progress` defaults to `In Progress`. On a fresh board spelled `In progress`,
`_options_mutation`'s #1492 rule claims the case-only variant under GitHub's spelling, so the loop's
own board kept `In progress` and every
`mark_in_progress` looked up `In Progress`, found nothing, and wrote nothing, with no message.

Hermetic: every gh call is answered by `boardfake.GitHub`, whose fresh board carries GitHub's real
defaults `Todo / In progress / Done`. No network, no mutation of real GitHub."""
import importlib.util
import json
import pathlib

import boardfake
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP = ROOT / "skills" / "sigma-loop" / "scripts"
DOCTOR = ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py"
WARN = "has no matching option"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sources = _load(LOOP / "sources.py", "sources_for_280")


def _cfg(project=None):
    return {"discovery": {"source": "github",
                          "github": {"repo": "acme/widget", "goal_label": "sdlc:goal",
                                     "project": dict({"enabled": True}, **(project or {}))}}}


def _src(gh, tmp_path, project=None):
    return sources.GitHubSource(_cfg(project), run=gh.loop_run, sdlc_dir=str(tmp_path))


def _status(gh, board, n):
    return next((i.get("status") for i in board["items"] if i["content"]["number"] == n), None)


def _goals(gh, *numbers):
    gh.issues = [{"number": n, "labels": [{"name": "sdlc:goal"}], "title": "t", "body": ""}
                 for n in numbers]


def _board(gh_status, number=1):
    """An ADOPTED board (pinned, not created by the loop) whose Status options are `gh_status`."""
    gh = boardfake.GitHub(boards=[{"title": "Team board", "number": number, "fields": [
        {"id": "PVTF_title", "name": "Title", "options": None, "data_type": "title"},
        {"id": "PVTSSF_status", "name": "Status", "options": [
            {"id": "o_%d" % i, "name": n, "color": "GRAY", "description": ""}
            for i, n in enumerate(gh_status)]}]}])
    return gh, gh.board(number=number)


# ------------------------------------------------------------------- the loop's own board (fresh)

def test_loop_created_board_with_githubs_defaults_moves_the_card_to_in_progress(tmp_path, capsys):
    gh = boardfake.GitHub(boards=[])                         # owner has NO board: the loop creates
    _goals(gh, 11)
    src = _src(gh, tmp_path)
    assert src.next_pending() == "11"
    src.mark_in_progress("11")
    board = gh.boards[0]
    assert _status(gh, board, 11) == "In Progress"
    names = gh.option_names(board, "Status")
    assert "In Progress" in names and "In progress" not in names     # renamed, not duplicated
    assert gh.option(board, "Status", "In Progress")["id"] == "o_inprog_1"   # id kept (#720)
    assert all(board["workflows"].values())
    assert WARN not in capsys.readouterr().err


# ------------------------------------------------------------------- adopted boards: never renamed

def test_adopted_board_with_githubs_default_spelling_moves_the_card_and_renames_nothing(
        tmp_path, capsys):
    gh, board = _board(["Todo", "In progress", "Done"])
    _goals(gh, 11)
    _src(gh, tmp_path, {"number": 1}).mark_in_progress("11")
    assert _status(gh, board, 11) == "In progress"
    assert gh.option_names(board, "Status") == ["Todo", "In progress", "Done"]
    assert not [m for m in gh.mutations() if "updateProjectV2Field" in m[-1]]   # never rewritten
    assert "'In Progress'" not in capsys.readouterr().err


def test_whitespace_and_case_variant_is_matched(tmp_path, capsys):
    gh, board = _board(["Backlog", " in   PROGRESS ", "Done"])
    _goals(gh, 11)
    _src(gh, tmp_path, {"number": 1}).mark_in_progress("11")
    assert _status(gh, board, 11) == " in   PROGRESS "
    assert "'In Progress'" not in capsys.readouterr().err


def test_exact_spelling_wins_over_a_case_variant(tmp_path):
    gh, board = _board(["In progress", "In Progress", "Done"])
    _goals(gh, 11)
    _src(gh, tmp_path, {"number": 1}).mark_in_progress("11")
    assert _status(gh, board, 11) == "In Progress"


def test_configured_column_is_matched_too(tmp_path):
    gh, board = _board(["Todo", "Doing", "Done"])
    _goals(gh, 11)
    _src(gh, tmp_path, {"number": 1, "columns": {"in_progress": "doing"}}).mark_in_progress("11")
    assert _status(gh, board, 11) == "Doing"


# ------------------------------------------------------------------- never a silent no-op

def test_unmatched_column_warns_once_per_run_and_leaves_the_card(tmp_path, capsys):
    gh, board = _board(["Backlog", "Doing", "Done"])
    _goals(gh, 11, 12)
    src = _src(gh, tmp_path, {"number": 1})
    src.mark_in_progress("11")
    assert src._set_board_status("12", src.col["in_progress"]) is False
    err = capsys.readouterr().err
    assert err.count(WARN) == 1, err
    assert "'In Progress'" in err and "project.columns.in_progress" in err
    assert _status(gh, board, 11) is None


def test_several_case_variants_and_no_exact_one_is_ambiguous_and_warns(tmp_path, capsys):
    gh, board = _board(["Backlog", "in progress", "IN PROGRESS", "Done"])
    _goals(gh, 11)
    _src(gh, tmp_path, {"number": 1}).mark_in_progress("11")
    assert _status(gh, board, 11) is None
    err = capsys.readouterr().err
    assert err.count(WARN) == 1 and "several" in err


def test_park_falls_back_to_blocked_without_a_warning_for_parked(tmp_path, capsys):
    gh, board = _board(["Backlog", "In progress", "Blocked", "Done"])
    _goals(gh, 11)
    src = _src(gh, tmp_path, {"number": 1})
    src.park("11", "parked for a test")
    assert _status(gh, board, 11) == "Blocked"
    assert WARN not in capsys.readouterr().err


def test_missing_ready_on_an_adopted_board_is_the_label_queue_not_a_warning(tmp_path, capsys):
    gh, board = _board(["Backlog", "In progress", "Done"])
    _goals(gh, 11)
    src = _src(gh, tmp_path, {"number": 1})
    assert src._set_board_status("11", src.col["ready"]) is False
    assert WARN not in capsys.readouterr().err


# ------------------------------------------------------------------- doctor row (read-only)

def _doctor_rows(gh, project):
    doctor = _load(DOCTOR, "doctor_for_280")

    def run(argv, *_a, **_k):                  # doctor's runner: stdout, or "" when gh failed
        rc, out, _err = gh.gh(argv)
        return out if rc == 0 else ""
    return doctor._board_columns_unmatched(_cfg(project)["discovery"]["github"], run)


def test_doctor_lists_loop_columns_with_no_matching_option(tmp_path):
    gh, _ = _board(["Todo", "In progress", "Done", "Blocked"])
    got = _doctor_rows(gh, {"number": 1, "owner": "acme"})
    assert got == ["backlog 'Backlog'", "qc 'QC'"]           # In progress matches; Ready/Parked do not count
    assert gh.mutations() == []


def test_adopted_board_without_backlog_warns_that_goals_are_not_carded(tmp_path, capsys):
    """The sync seeds new goal cards into Ready, else Backlog; a board with neither used to leave
    every goal uncarded, silently."""
    gh, board = _board(["Todo", "In progress", "Done"])
    _goals(gh, 11, 12)
    _src(gh, tmp_path, {"number": 1}).mark_in_progress("11")
    err = capsys.readouterr().err
    assert err.count(WARN) == 1 and "'Backlog'" in err
    assert _status(gh, board, 11) == "In progress" and _status(gh, board, 12) is None


def test_doctor_all_columns_matched_is_empty(tmp_path):
    gh, _ = _board(["backlog", "Ready", "In progress", "QC", "Done", "Blocked", "Parked"])
    assert _doctor_rows(gh, {"number": 1, "owner": "acme"}) == []


def test_doctor_unreadable_board_is_no_answer(tmp_path):
    gh, _ = _board(["Todo"])
    assert _doctor_rows(gh, {"owner": "acme"}) is None        # nothing pinned
    gh.fail["field-list"] = "HTTP 502"
    assert _doctor_rows(gh, {"number": 1, "owner": "acme"}) is None


def test_doctor_row_is_not_under_cheap_only():
    """The row reads the board over the network, so the SessionStart wizard (`cheap_only`) never
    runs it: the call sits inside the same `if not cheap_only:` block as the pinned-board row."""
    lines = DOCTOR.read_text(encoding="utf-8").splitlines()
    at = next(i for i, ln in enumerate(lines) if "_board_columns_unmatched(gh_disc" in ln)
    gate = max(i for i in range(at) if lines[i].strip() == "if not cheap_only:")
    indent = len(lines[gate]) - len(lines[gate].lstrip())
    assert all(len(ln) - len(ln.lstrip()) > indent for ln in lines[gate + 1:at + 1] if ln.strip())


# ------------------------------------------------------------------- review block #1 (PR #325)
# Ready is matched EXACTLY: the queue mode (label queue vs board queue) is never spelling-adopted.

NOTICE = "differs from the Ready column"
PRIO = {"id": "PVTSSF_prio", "name": "Priority", "options": [
    {"id": "p_%d" % i, "name": "P%d" % i, "color": "GRAY", "description": ""} for i in range(5)]}


def _two_goal_board(lanes, ready_lane):
    gh, board = _board(lanes)
    _goals(gh, 11, 12)
    for n, lane in ((11, "Backlog"), (12, ready_lane)):
        it = gh.add_item(board, n, Status=lane)
        it["labels"], it["title"] = ["sdlc:goal"], "t"
    return gh, board


@pytest.mark.parametrize("variant", ["READY", "ready", "Ready "])
def test_case_variant_ready_keeps_the_label_queue_and_notices_once(tmp_path, capsys, variant):
    """The reviewer's repro (`.sdlc/evidence/280/rv280/atkA.py`): #12 in a `READY` lane was picked
    over #11 because `_ready_lane` adopted the spelling and silently switched to the board queue."""
    gh, board = _two_goal_board(["Backlog", variant, "In Progress", "Done"], variant)
    src = _src(gh, tmp_path, {"number": 1})
    assert src._ready_lane() is None
    assert src.next_pending() == "11"                          # label queue, as on origin/main
    assert src.next_pending() == "11" and src._ready_lane() is None
    assert src.col["ready"] == "Ready"                         # never adopted
    assert src._set_board_status("11", src.col["ready"]) is False
    assert _status(gh, board, 11) == "Backlog"                 # never written into the variant
    err = capsys.readouterr().err
    assert err.count(NOTICE) == 1, err
    assert repr(variant) in err and "board_migrate.py" in err and "project.columns.ready" in err
    assert WARN not in err


def test_case_variant_ready_is_never_adopted_by_a_write_path_first(tmp_path, capsys):
    """A status write before the first pick reads the board through `_ensure_status_field`; it
    must not adopt `READY` either, or the sync would seed new goals into it."""
    gh, board = _two_goal_board(["Backlog", "READY", "In Progress", "Done"], "READY")
    gh.issues.append({"number": 13, "labels": [{"name": "sdlc:goal"}], "title": "t", "body": ""})
    src = _src(gh, tmp_path, {"number": 1})
    src.mark_in_progress("11")
    assert src.col["ready"] == "Ready"
    assert _status(gh, board, 13) == "Backlog"                 # seeded into Backlog, not READY
    assert src._ready_lane() is None


def test_explicit_ready_config_opts_into_the_board_queue(tmp_path, capsys):
    gh, _ = _two_goal_board(["Backlog", "READY", "In Progress", "Done"], "READY")
    src = _src(gh, tmp_path, {"number": 1, "columns": {"ready": "READY"}})
    assert src._ready_lane() == "READY"
    assert src.next_pending() == "12"
    assert NOTICE not in capsys.readouterr().err


def test_exact_ready_board_is_the_board_queue_without_a_notice(tmp_path, capsys):
    gh, _ = _two_goal_board(["Backlog", "Ready", "In Progress", "Done"], "Ready")
    src = _src(gh, tmp_path, {"number": 1})
    assert src._ready_lane() == "Ready" and src.next_pending() == "12"
    assert NOTICE not in capsys.readouterr().err


def test_fresh_loop_board_ready_lane_unchanged(tmp_path, capsys):
    gh = boardfake.GitHub(boards=[])
    _goals(gh, 11)
    _src(gh, tmp_path).mark_in_progress("11")                  # the loop creates its board
    assert "Ready" in gh.option_names(gh.boards[0], "Status")
    assert _src(gh, tmp_path)._ready_lane() == "Ready"
    assert NOTICE not in capsys.readouterr().err


# ------------------------------------------------------------------- adoption is load-bearing
# Each test below fails when `_adopt_column_spellings` is removed from its call site.

def test_new_goal_is_seeded_into_a_case_variant_backlog(tmp_path, capsys):
    """`_sync_backlog` looks its seed up by `self.col["backlog"]` -- exact, after adoption."""
    gh, board = _board(["backlog", "In progress", "Done"])
    _goals(gh, 11, 12)
    _src(gh, tmp_path, {"number": 1}).mark_in_progress("11")
    assert _status(gh, board, 12) == "backlog"
    assert WARN not in capsys.readouterr().err


def test_goal_in_a_case_variant_backlog_is_promoted_to_ready(tmp_path):
    """`stale_backlog` compares the card's lane with `self.col["backlog"]`."""
    gh, board = _board(["backlog", "Ready", "In Progress", "Done"])
    _goals(gh, 11, 12)
    gh.add_item(board, 12, Status="backlog")["labels"] = ["sdlc:goal"]
    _src(gh, tmp_path, {"number": 1}).mark_in_progress("11")
    assert _status(gh, board, 12) == "Ready"


def test_goal_in_a_case_variant_in_flight_lane_is_not_reported_stranded(tmp_path, capsys):
    """`_board_queue`'s in-flight set is built from `self.col` after `_ready_lane` adopted it."""
    gh, board = _board(["Backlog", "Ready", "in progress", "Done"])
    _goals(gh, 11)
    it = gh.add_item(board, 11, Status="in progress")
    it["labels"], it["title"] = ["sdlc:goal"], "t"
    _src(gh, tmp_path, {"number": 1}).next_pending()
    assert "NOTHING in it" not in capsys.readouterr().err


def _prio_board(done_lane, labels=("sdlc:goal", "priority:P1")):
    gh = boardfake.GitHub(boards=[{"title": "Team board", "number": 1, "fields": [
        {"id": "PVTF_title", "name": "Title", "options": None, "data_type": "title"},
        {"id": "PVTSSF_status", "name": "Status", "options": [
            {"id": "o_%d" % i, "name": n, "color": "GRAY", "description": ""}
            for i, n in enumerate(["Backlog", "In Progress", done_lane])]}, dict(PRIO)]}])
    board = gh.board(number=1)
    gh.issues = [{"number": 11, "labels": [{"name": n} for n in labels], "title": "t", "body": ""}]
    return gh, board


def _prio_writes(gh):
    return [c for c in gh.calls if c[:2] == ["project", "item-edit"] and "PVTSSF_prio" in c]


def test_card_moved_to_a_case_variant_done_gets_no_priority_write(tmp_path, capsys):
    """`_set_board_status` skips the Priority mirror on a card it just moved to Done, comparing the
    board's spelling with `self.col["done"]`."""
    gh, board = _prio_board("done")
    gh.add_item(board, 11, Status="In Progress")["labels"] = ["sdlc:goal", "priority:P1"]
    src = _src(gh, tmp_path, {"number": 1})
    assert src._set_board_status("11", src.col["done"]) is True
    assert _status(gh, board, 11) == "done"
    assert _prio_writes(gh) == []


def test_open_goal_stuck_in_a_case_variant_done_is_reported(tmp_path, capsys):
    """#1206's guard compares the card's lane with `self.col["done"]`."""
    gh, board = _prio_board("done")
    gh.add_item(board, 11, Status="done")["labels"] = ["sdlc:goal", "priority:P1"]
    gh.issues.append({"number": 12, "labels": [{"name": "sdlc:goal"}], "title": "u", "body": ""})
    _src(gh, tmp_path, {"number": 1}).mark_in_progress("12")      # the sync visits #11
    assert "stuck at done" in capsys.readouterr().err
    assert not [c for c in _prio_writes(gh) if "PVTI_11" in c]


# ------------------------------------------------------------------- doctor agrees with the loop

def test_doctor_and_loop_agree_on_a_duplicated_lane(tmp_path, capsys):
    """The research found a real board with two `In progress` lanes. The loop's option dict holds
    one of them and moves the card; doctor must not call it ambiguous."""
    lanes = ["Backlog", "Ready", "In progress", "In progress", "QC", "Done", "Blocked", "Parked"]
    gh, board = _board(lanes)
    assert _doctor_rows(gh, {"number": 1, "owner": "acme"}) == []
    _goals(gh, 11)
    _src(gh, tmp_path, {"number": 1}).mark_in_progress("11")
    assert _status(gh, board, 11) == "In progress"
    assert WARN not in capsys.readouterr().err


def test_doctor_names_the_ambiguous_variants(tmp_path):
    gh, _ = _board(["Backlog", "Ready", "in progress", "IN PROGRESS", "QC", "Done", "Blocked"])
    got = _doctor_rows(gh, {"number": 1, "owner": "acme"})
    assert len(got) == 1 and got[0].startswith("in_progress 'In Progress'")
    assert "'in progress'" in got[0] and "'IN PROGRESS'" in got[0]


def _doctor_check(tmp_path, gh):
    doctor = _load(DOCTOR, "doctor_for_280_check")
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(json.dumps(_cfg({"number": 1, "owner": "acme"})),
                                      encoding="utf-8")
    calls = []

    def run(argv, *_a, **_k):
        calls.append(list(argv))
        if argv[:3] == ["gh", "auth", "status"]:
            return "Logged in to github.com ... token scopes: project"
        rc, out, _err = gh.gh(argv)
        return out if rc == 0 else ""
    rows = {c["name"]: c for c in doctor.check(str(sdlc), run=run)}
    return rows, calls


def test_doctor_reads_the_field_list_once_for_both_board_rows(tmp_path):
    gh, _ = _board(["Todo", "In progress", "Done"])
    rows, calls = _doctor_check(tmp_path, gh)
    assert "board custom fields mapped" in rows
    assert rows["board Status options match the loop's columns"]["ok"] is False
    assert sum(1 for c in calls if c[:3] == ["gh", "project", "field-list"]) == 1
    assert "already match" not in rows["board Status options match the loop's columns"]["fix"]


def test_init_template_column_map_equals_the_loops_defaults():
    """`config.json.tmpl` keeps its own literal map; it must never drift from `BOARD_COLUMNS`."""
    tmpl = (ROOT / "skills" / "sigma-init" / "templates" / "config.json.tmpl").read_text(
        encoding="utf-8")
    line = next(ln for ln in tmpl.splitlines() if ln.strip().startswith('"columns":'))
    got = json.loads("{" + line.strip().rstrip(",") + "}")["columns"]
    assert list(got.items()) == list(sources.BOARD_COLUMNS)

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

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP = ROOT / "skills" / "agrim-loop" / "scripts"
DOCTOR = ROOT / "skills" / "agrim-doctor" / "scripts" / "doctor.py"
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

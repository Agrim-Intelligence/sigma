"""#235: `board_setup.py create` -- the opt-in gesture that creates (or finishes, or adopts) the
GitHub Project board and pins it. Hermetic: every gh call is answered by `boardfake.GitHub`, one
in-memory GitHub shared with the loop's own `sources.GitHubSource`, so the acceptance test drives
the REAL pick-time status write against the board this script created. No network, no mutation of
real GitHub."""
import importlib.util
import errno
import io
import json
import os
import pathlib
import shlex
import subprocess
import sys

import pytest

import boardfake

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-init" / "scripts"
LOOP = ROOT / "skills" / "sigma-loop" / "scripts"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load(SCRIPTS / "board_setup.py", "board_setup_under_test")
COLS = ["Backlog", "Ready", "In Progress", "QC", "Done", "Blocked", "Parked"]
PRIOS = ["P0", "P1", "P2", "P3", "P4"]


def _sdlc(tmp_path, project=None, extra=None):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    cfg = {"verify": {"command": "x", "enforce": True},
           "discovery": {"source": "github",
                         "github": {"repo": "acme/widget", "goal_label": "sdlc:goal",
                                    "project": dict({"enabled": True}, **(project or {}))}}}
    cfg.update(extra or {})
    (sdlc / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return sdlc


def _cfg(sdlc):
    return json.loads((sdlc / "config.json").read_text(encoding="utf-8"))


def _run(sdlc, gh, *flags):
    lines = []
    rc = bs.main(["board_setup.py", "create", str(sdlc), *flags], runner=gh.gh, out=lines.append)
    return rc, "\n".join(lines)


def _org_with_boards(**kw):
    return boardfake.GitHub(boards=[{"title": "Acme Delivery Board"},
                                    {"title": "Hiring pipeline"}], **kw)


# ---------------------------------------------------------------- create, fresh

def test_create_on_an_org_with_unrelated_boards_makes_pins_and_configures_the_board(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    rc, text = _run(sdlc, gh, "--yes")
    assert rc == 0, text
    board = gh.board(title="widget — SDLC")
    assert board is not None and len(gh.boards) == 3
    proj = _cfg(sdlc)["discovery"]["github"]["project"]
    assert proj["number"] == board["number"] and proj["owner"] == "acme"
    assert gh.option_names(board, "Status") == COLS            # renamed Todo/In progress, in order
    assert gh.option_names(board, "Priority") == PRIOS
    assert "acme/widget" in board["repos"]                     # linked by createProjectV2 itself
    assert board["workflows"]["Item closed"] is True           # ids preserved -> workflows kept
    assert "[ok] auto-close" in text and "[FAIL]" not in text


def test_status_rewrite_keeps_githubs_option_ids(tmp_path):
    """#720: an id-less rewrite deletes options and GitHub disables the workflows that pointed at
    them. `Todo`, `In progress` and `Done` must keep their ids across the rewrite."""
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    assert _run(sdlc, gh, "--yes")[0] == 0
    board = gh.board(title="widget — SDLC")
    ids = {o["name"]: o["id"] for o in gh.field(board, "Status")["options"]}
    n = board["number"]
    assert ids["Backlog"] == f"o_todo_{n}" and ids["In Progress"] == f"o_inprog_{n}"
    assert ids["Done"] == f"o_done_{n}"


def test_other_config_keys_survive_the_pin(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path, project={"columns": {"qc": "QC"}},
                                         extra={"work": {"enabled": True, "remote": "origin"}})
    assert _run(sdlc, gh, "--yes")[0] == 0
    cfg = _cfg(sdlc)
    assert cfg["work"] == {"enabled": True, "remote": "origin"}
    assert cfg["verify"] == {"command": "x", "enforce": True}
    assert cfg["discovery"]["github"]["project"]["columns"] == {"qc": "QC"}
    assert set(cfg["discovery"]["github"]["project"]) == {"enabled", "columns", "number", "owner",
                                                          "setup_created"}


def test_configured_column_names_are_used_not_a_second_table(tmp_path):
    gh = _org_with_boards()
    sdlc = _sdlc(tmp_path, project={"columns": {"qc": "Review"}, "priority_field": "Urgency"})
    assert _run(sdlc, gh, "--yes")[0] == 0
    board = gh.board(title="widget — SDLC")
    assert "Review" in gh.option_names(board, "Status") and "QC" not in gh.option_names(board, "Status")
    assert gh.option_names(board, "Urgency") == PRIOS


def test_no_token_is_ever_printed(tmp_path):
    gh, sdlc = _org_with_boards(token_kind=boardfake.FAKE_TOKEN), _sdlc(tmp_path)
    _rc, text = _run(sdlc, gh, "--yes")
    assert boardfake.FAKE_TOKEN not in text and "ghp_" not in text
    assert boardfake.FAKE_TOKEN not in (sdlc / "config.json").read_text(encoding="utf-8")


def test_without_yes_nothing_is_mutated_or_written(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    before = (sdlc / "config.json").read_text(encoding="utf-8")
    rc, text = _run(sdlc, gh)
    assert rc == 0 and "dry run" in text and "--yes" in text
    assert gh.mutations() == [] and len(gh.boards) == 2
    assert (sdlc / "config.json").read_text(encoding="utf-8") == before


def test_cost_per_create_and_per_rerun(tmp_path):
    """The research's cost statement, measured: 4 GraphQL calls to create, 1 to re-run."""
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    assert _run(sdlc, gh, "--yes")[0] == 0
    gql = [c for c in gh.calls if c[:2] == ["api", "graphql"]]
    assert len(gql) == 4 and len(gh.mutations()) == 3
    assert len(gh.calls) - len(gql) == 7          # auth, owner, list, repo, fields, board, fields
    gh.calls.clear()
    rc, text = _run(sdlc, gh, "--yes")
    assert rc == 0 and gh.mutations() == []
    assert len([c for c in gh.calls if c[:2] == ["api", "graphql"]]) == 1
    assert len(gh.boards) == 3                                  # idempotent: no second board


# ---------------------------------------------------------------- refusals

def test_duplicate_title_is_refused_with_the_manual_runbook(tmp_path):
    gh = boardfake.GitHub(boards=[{"title": "Acme Delivery Board"}, {"title": "widget — SDLC"}])
    sdlc = _sdlc(tmp_path)
    rc, text = _run(sdlc, gh, "--yes")
    assert rc == 2 and "already has a board titled" in text
    assert "Manual runbook" in text and "--number" in text
    assert gh.mutations() == [] and len(gh.boards) == 2
    assert "number" not in _cfg(sdlc)["discovery"]["github"]["project"]


def test_adopting_the_same_titled_board_with_number_completes_it(tmp_path):
    gh = boardfake.GitHub(boards=[{"title": "widget — SDLC", "number": 7}])
    sdlc = _sdlc(tmp_path)
    rc, text = _run(sdlc, gh, "--number", "7", "--yes")
    assert rc == 0, text
    assert len(gh.boards) == 1 and _cfg(sdlc)["discovery"]["github"]["project"]["number"] == 7
    # adoption renames nothing (review of PR #279): `Todo` stays as a trailing lane, `In progress`
    # keeps its spelling and is mapped in config, and only the missing columns are added
    # and (review block #2) an ADOPTED board never gets `Ready`: it would switch the loop's queue
    assert gh.option_names(gh.board(number=7), "Status") == [
        "Todo", "In progress", "Done", "Backlog", "QC", "Blocked", "Parked"]
    assert _cfg(sdlc)["discovery"]["github"]["project"]["columns"] == {"in_progress": "In progress"}
    assert "moves its card to In progress" in text
    assert "board_migrate.py" in text and "--project 7" in text


def test_missing_project_scope_is_refused_with_preflight_remediation(tmp_path):
    gh, sdlc = _org_with_boards(scopes=("repo", "workflow", "read:org")), _sdlc(tmp_path)
    rc, text = _run(sdlc, gh, "--yes")
    assert rc == 2 and "missing project" in text
    assert "gh auth refresh -s project -h github.com" in text
    for host in ("Claude Code:", "Codex:", "Cursor:"):
        assert host in text
    assert gh.mutations() == []


def test_a_pinned_number_that_does_not_exist_is_refused(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path, project={"number": 41})
    rc, text = _run(sdlc, gh, "--yes")
    assert rc == 2 and "#41 is not one of" in text and gh.mutations() == []


def test_an_unlistable_owner_is_refused_not_guessed(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    gh.fail["projectsV2?per_page"] = "HTTP 502"
    rc, text = _run(sdlc, gh, "--yes")
    assert rc == 2 and "cannot be ruled out" in text and gh.mutations() == []


# ---------------------------------------------------------------- partial failure + resume

def test_partial_failure_exits_1_with_resume_and_the_resume_completes(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    gh.fail["createProjectV2Field("] = "GraphQL: secondary rate limit"
    rc, text = _run(sdlc, gh, "--yes")
    board = gh.board(title="widget — SDLC")
    assert rc == 1 and "[FAIL] Priority field" in text and "secondary rate limit" in text
    assert f"--number {board['number']}" in text and "INCOMPLETE" in text
    assert _cfg(sdlc)["discovery"]["github"]["project"]["number"] == board["number"]  # pinned first
    resume = text.split("Resume", 1)[1].splitlines()[1].strip()
    assert resume.endswith("--yes")
    del gh.fail["createProjectV2Field("]
    argv = shlex.split(resume)                                 # the printed gesture, verbatim
    assert argv[argv.index("create") + 1] == str(sdlc)
    rc2, text2 = _run(sdlc, gh, *argv[argv.index("create") + 2:])
    assert rc2 == 0, text2
    assert len(gh.boards) == 3 and gh.option_names(board, "Priority") == PRIOS


def test_create_failure_prints_the_resume_without_a_number(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    gh.fail["createProjectV2("] = "Resource not accessible by integration"
    rc, text = _run(sdlc, gh, "--yes")
    assert rc == 1 and "no board exists yet" in text and "--number" not in text
    assert "number" not in _cfg(sdlc)["discovery"]["github"]["project"]


# ---------------------------------------------------------------- template + workflows

def test_template_is_copied_linked_and_completed(tmp_path):
    gh = boardfake.GitHub(boards=[{"title": "Sigma template", "number": 3}])
    sdlc = _sdlc(tmp_path)
    rc, text = _run(sdlc, gh, "--template", "3", "--yes")
    assert rc == 0, text
    made = gh.board(title="widget — SDLC")
    assert any("copyProjectV2(" in " ".join(c) for c in gh.mutations())
    assert not any("createProjectV2(" in " ".join(c) for c in gh.mutations())
    assert "acme/widget" in made["repos"]                       # copy has no repositoryId: linked
    assert gh.option_names(made, "Status") == COLS and gh.option_names(made, "Priority") == PRIOS


def test_workflow_off_prints_the_manual_step_with_the_deep_link(tmp_path):
    gh = boardfake.GitHub(boards=[{"title": "widget — SDLC", "number": 5,
                                   "workflows": {"Item closed": False}}])
    sdlc = _sdlc(tmp_path, project={"number": 5})
    rc, text = _run(sdlc, gh, "--yes")
    assert rc == 0
    assert "[manual] auto-close" in text
    assert "https://github.com/orgs/acme/projects/5/workflows" in text


def test_user_owner_gets_the_users_deep_link(tmp_path):
    gh = boardfake.GitHub(owner="acme", owner_type="User",
                          boards=[{"title": "widget — SDLC", "number": 2,
                                   "workflows": {}}])
    sdlc = _sdlc(tmp_path, project={"number": 2})
    _rc, text = _run(sdlc, gh, "--yes")
    assert "could not be read" in text
    assert "https://github.com/users/acme/projects/2/workflows" in text


# ---------------------------------------------------------------- acceptance: the loop uses it

def _src(sdlc, gh):
    src_mod = _load(LOOP / "sources.py", "sources_for_board_setup")
    return src_mod.GitHubSource(_cfg(sdlc), run=gh.loop_run, sdlc_dir=str(sdlc))


def _card_status(gh, board, n):
    return next((i.get("status") for i in board["items"] if i["content"]["number"] == n), None)


def test_acceptance_next_pick_moves_the_card_to_in_progress(tmp_path, capsys):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    gh.issues = [{"number": 11, "labels": [{"name": "sdlc:goal"}], "title": "t", "body": ""}]
    assert _run(sdlc, gh, "--yes")[0] == 0
    board = gh.board(title="widget — SDLC")
    src = _src(sdlc, gh)
    assert src.next_pending() == "11"
    src.mark_in_progress("11")
    assert _card_status(gh, board, 11) == "In Progress"
    assert "mirroring OFF" not in capsys.readouterr().err


def test_control_renamed_board_without_pin_turns_mirroring_off(tmp_path, capsys):
    """The pin is load-bearing: once a human renames the board (or the org has >100 boards), the
    loop's title fallback cannot find it and refuses. With the pin it still moves the card."""
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    gh.issues = [{"number": 11, "labels": [{"name": "sdlc:goal"}], "title": "t", "body": ""}]
    assert _run(sdlc, gh, "--yes")[0] == 0
    board = gh.board(title="widget — SDLC")
    board["title"] = "Widget delivery"                         # a human renames it
    _src(sdlc, gh).mark_in_progress("11")                      # pinned: still found
    assert _card_status(gh, board, 11) == "In Progress"
    cfg = _cfg(sdlc)
    del cfg["discovery"]["github"]["project"]["number"]        # CONTROL: unset the pin
    (sdlc / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    board["items"].clear()
    capsys.readouterr()
    _src(sdlc, gh).mark_in_progress("11")
    assert _card_status(gh, board, 11) is None
    assert "board mirroring OFF" in capsys.readouterr().err


def test_title_fallback_still_finds_an_unrenamed_board_without_the_pin(tmp_path, capsys):
    """Recorded, not hidden (research finding 2): with the default title intact, the loop finds the
    board by title even without the pin -- so the control above is run on a renamed board."""
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    assert _run(sdlc, gh, "--yes")[0] == 0
    board = gh.board(title="widget — SDLC")
    cfg = _cfg(sdlc)
    del cfg["discovery"]["github"]["project"]["number"]
    (sdlc / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    _src(sdlc, gh).mark_in_progress("11")
    assert _card_status(gh, board, 11) == "In Progress"


def test_pinned_board_beyond_the_first_100_is_still_found(tmp_path, capsys):
    """`_find_project` reads one page of 100 boards; a pinned number outside it is read directly."""
    gh = boardfake.GitHub(boards=[{"title": "other %d" % i} for i in range(120)])
    sdlc = _sdlc(tmp_path)
    assert _run(sdlc, gh, "--title", "Widget work", "--yes")[0] == 0
    board = gh.board(title="Widget work")
    assert board["number"] > 100
    _src(sdlc, gh).mark_in_progress("11")
    assert _card_status(gh, board, 11) == "In Progress"
    assert "mirroring OFF" not in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["board_setup.py"], ["board_setup.py", "nope"],
                                  ["board_setup.py", "create"],
                                  ["board_setup.py", "create", ".sdlc", "--number", "x"]])
def test_bad_usage_exits_2(argv):
    lines = []
    assert bs.main(argv, runner=lambda a: (1, "", ""), out=lines.append) == 2


# ---------------------------------------------------------------- /sigma-init offers it

import subprocess  # noqa: E402

init = _load(SCRIPTS / "sdlc_init.py", "sdlc_init_for_board_setup")
init.PREFLIGHT_RUNNER = lambda argv, cwd=None, timeout=None: (
    (1, "You are not logged into any GitHub hosts.") if argv[0] == "gh"
    else init._preflight().real_runner(argv, cwd, timeout))


def _repo(tmp_path, remote="https://github.com/acme/widget.git", config=None):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", remote], check=True)
    if config is not None:
        (tmp_path / ".sdlc").mkdir()
        (tmp_path / ".sdlc" / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return tmp_path


def _gh_cfg(**project):
    return {"discovery": {"source": "github", "github": {"repo": "", "project": project}}}


def test_offer_is_never_made_in_local_goals_mode(tmp_path):
    root = _repo(tmp_path, config={"discovery": {"source": "local-goals"}})
    assert init.board_offer(root, github_flag=False) == []


def test_offer_in_github_mode_names_the_gesture_for_every_host(tmp_path):
    root = _repo(tmp_path, config=_gh_cfg(enabled=True))
    text = "\n".join(init.board_offer(root, github_flag=False))
    assert "OFFER: create 'widget — SDLC' under acme" in text
    assert "Nothing is created unless you say yes" in text
    assert "Claude Code:" in text and "Codex / Cursor:" in text and text.count("--yes") == 2
    assert "board_setup.py" in text and str((root / ".sdlc").resolve()) in text.replace("'", "")


def test_offer_with_the_github_flag_on_a_fresh_scaffold(tmp_path):
    root = _repo(tmp_path, config={"discovery": {"source": "local-goals"}})
    assert any("OFFER" in ln for ln in init.board_offer(root, github_flag=True))


def test_no_offer_when_a_number_is_pinned(tmp_path):
    root = _repo(tmp_path, config=_gh_cfg(enabled=True, number=9))
    lines = init.board_offer(root, github_flag=True)
    assert len(lines) == 1 and "#9 is pinned" in lines[0]


def test_no_offer_without_a_github_remote(tmp_path):
    root = _repo(tmp_path, remote=None, config=_gh_cfg(enabled=True))
    lines = init.board_offer(root, github_flag=True)
    assert len(lines) == 1 and "not offered" in lines[0]


def test_init_prints_the_offer_and_its_preview_gesture_is_a_dry_run(tmp_path, capsys):
    """Run the gesture the docs give, copied out of init's own output: the Preview line, verbatim,
    against the fake -- it must read only and write nothing."""
    root = _repo(tmp_path, config=dict(_gh_cfg(enabled=True), verify={"command": "true"}))
    assert init.main(["sdlc_init.py", str(root)]) == 0
    out = capsys.readouterr().out
    preview = next(ln for ln in out.splitlines() if "Preview (read-only):" in ln)
    argv = shlex.split(preview.split("Preview (read-only):", 1)[1])
    gh = _org_with_boards()
    lines = []
    cfg_before = (root / ".sdlc" / "config.json").read_text(encoding="utf-8")
    assert bs.main(["board_setup.py", *argv[argv.index("create"):]], runner=gh.gh,
                   out=lines.append) == 0
    assert "dry run" in "\n".join(lines) and gh.mutations() == []
    assert (root / ".sdlc" / "config.json").read_text(encoding="utf-8") == cfg_before


def test_pin_lands_even_when_the_project_block_is_null(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    cfg = _cfg(sdlc)
    cfg["discovery"]["github"]["project"] = None
    (sdlc / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert _run(sdlc, gh, "--yes")[0] == 0
    assert _cfg(sdlc)["discovery"]["github"]["project"]["number"] == 3


# ---------------------------------------------------------------- review of PR #279
# Block #1: adopting a human-built board (`--number N`) must RENAME nothing and must keep every
# existing option's id, name, colour and description -- only missing options are ADDED. The fake
# now stores colour + description and wipes a card's value when its option id is dropped, so the
# reviewer's reproduction (Todo -> Backlog, Done recoloured RED, descriptions wiped) is visible.

HUMAN_STATUS = [
    {"id": "h_todo", "name": "Todo", "color": "BLUE", "description": "Not started yet"},
    {"id": "h_ip", "name": "In progress", "color": "ORANGE", "description": "Someone owns it"},
    {"id": "h_done", "name": "Done", "color": "GREEN", "description": "Shipped"},
    {"id": "h_nd", "name": "Needs design", "color": "PINK", "description": "Waiting on design"},
]
#: The same board with a human lane whose name and description need GraphQL escaping.
ODD_STATUS = HUMAN_STATUS[:3] + [
    {"id": "h_nd", "name": 'Needs "design" \\ — ü', "color": "PINK",
     "description": 'Waiting on "design" \\ review'}]


def _human_board(status=None, extra_fields=()):
    gh = boardfake.GitHub(boards=[{"title": "Team board", "number": 1, "fields": [
        {"id": "PVTF_title_1", "name": "Title", "options": None, "data_type": "title"},
        {"id": "PVTSSF_status_1", "name": "Status",
         "options": [dict(o) for o in (status or HUMAN_STATUS)]}, *extra_fields]}])
    board = gh.board(number=1)
    gh.add_item(board, 5, Status="Todo")
    gh.add_item(board, 6, Status=(status or HUMAN_STATUS)[-1]["name"])
    gh.add_item(board, 7, Status="Done")
    return gh, board


@pytest.mark.parametrize("status", [HUMAN_STATUS, ODD_STATUS], ids=["reviewer", "escaping"])
def test_adopting_a_human_board_renames_nothing_and_keeps_colour_and_description(tmp_path, status):
    gh, board = _human_board(status=status)
    before = [dict(o) for o in gh.field(board, "Status")["options"]]
    values = {i["id"]: dict(i["values"]) for i in board["items"]}
    sdlc = _sdlc(tmp_path)
    rc, text = _run(sdlc, gh, "--number", "1", "--yes")
    assert rc == 0, text
    after = {o["id"]: o for o in gh.field(board, "Status")["options"]}
    for o in before:                                   # every human option: untouched, byte for byte
        assert after[o["id"]] == o, (o, after.get(o["id"]))
    names = gh.option_names(board, "Status")
    assert "Todo" in names and "Backlog" in names       # added, not renamed
    assert "In Progress" not in names                   # the case variant is used, not duplicated
    assert {i["id"]: i["values"] for i in board["items"]} == values   # no card lost its value
    assert all(board["workflows"].values())
    assert _cfg(sdlc)["discovery"]["github"]["project"]["columns"] == {"in_progress": "In progress"}
    assert "renamed nothing" in text or "nothing renamed" in text


def test_adopted_board_case_variant_is_what_the_loop_then_writes(tmp_path, capsys):
    gh, board = _human_board()
    gh.issues = [{"number": 11, "labels": [{"name": "sdlc:goal"}], "title": "t", "body": ""}]
    sdlc = _sdlc(tmp_path)
    assert _run(sdlc, gh, "--number", "1", "--yes")[0] == 0
    _src(sdlc, gh).mark_in_progress("11")
    assert _card_status(gh, board, 11) == "In progress"
    assert "mirroring OFF" not in capsys.readouterr().err


def test_a_fresh_board_keeps_githubs_colours_and_descriptions_on_kept_options(tmp_path):
    """The fresh path may rename (GitHub made those options), but it must not recolour `Done`."""
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    assert _run(sdlc, gh, "--yes")[0] == 0
    board = gh.board(title="widget — SDLC")
    done = gh.option(board, "Status", "Done")
    assert (done["color"], done["description"]) == ("PURPLE", "This has been completed")


def test_unreadable_option_colours_refuse_instead_of_resetting_them(tmp_path):
    status = [dict(o) for o in HUMAN_STATUS]
    status[0]["color"] = ""                             # REST gave no usable colour
    gh, board = _human_board(status=status)
    rc, text = _run(_sdlc(tmp_path), gh, "--number", "1", "--yes")
    assert rc == 2 and "REFUSED" in text
    assert not any("updateProjectV2Field(" in " ".join(c) for c in gh.mutations())


def test_a_priority_case_variant_is_refused_not_renamed(tmp_path):
    prio = {"id": "PVTSSF_prio_1", "name": "Priority", "options": [
        {"id": "p%d" % i, "name": "p%d" % i, "color": "GRAY", "description": ""} for i in range(5)]}
    gh, board = _human_board(extra_fields=[prio])
    rc, text = _run(_sdlc(tmp_path), gh, "--number", "1", "--yes")
    assert rc == 2 and "[REFUSED] Priority field" in text and "'p0'" in text
    assert gh.option_names(board, "Priority") == ["p0", "p1", "p2", "p3", "p4"]


# (2) the pin wins over a same-titled board on the SAME first page

def test_a_pinned_board_beats_a_same_titled_board_on_the_first_page(tmp_path):
    gh = boardfake.GitHub(boards=[{"title": "widget — SDLC", "number": 1},
                                  {"title": "Renamed by a human", "number": 2}])
    sdlc = _sdlc(tmp_path, project={"number": 2})
    src = _src(sdlc, gh)
    assert src._find_project("acme", "widget — SDLC")[0] == 2


# (3) the post-write check is what catches a write GitHub accepted but did not apply

def test_a_silently_dropped_field_creation_fails_the_verify_step(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    gh.ignore.add("createProjectV2Field(")
    rc, text = _run(sdlc, gh, "--yes")
    assert rc == 1 and "[FAIL] verify Priority" in text and "missing P0" in text


# (4) escaping: every interpolated GraphQL value goes through JSON quoting

def test_q_is_a_graphql_string_literal():
    for raw in ['plain', 'a "quoted" word', 'back\\slash', 'ünï — code', 'line\nbreak']:
        lit = bs._q(raw)
        assert lit.startswith('"') and lit.endswith('"') and json.loads(lit) == raw
        assert '\n' not in lit and '"' not in lit[1:-1].replace('\\"', '')


def test_a_title_with_quotes_and_backslashes_round_trips(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    title = 'Say "hi" \\ — ü'
    rc, text = _run(sdlc, gh, "--title", title, "--yes")
    assert rc == 0, text
    assert gh.board(title=title) is not None


def test_options_mutation_quotes_names_descriptions_and_the_field_id():
    src = _load(LOOP / "sources.py", "sources_for_quoting")
    doc = src.GitHubSource._options_mutation('F"1', ["Backlog"], ODD_STATUS)
    assert 'fieldId: "F\\"1"' in doc
    opts = boardfake.parse_options(doc)
    assert [o["name"] for o in opts][-1] == ODD_STATUS[-1]["name"]
    assert opts[-1]["description"] == ODD_STATUS[-1]["description"]


# (6) the fields read is paginated; a wrong-typed field is a clear refusal

def _many_fields(n, then):
    return [{"id": "PVTF_f%d" % i, "name": "Custom %d" % i, "options": None, "data_type": "text"}
            for i in range(n)] + then


def test_the_fields_read_sees_a_status_field_past_the_first_page(tmp_path):
    status = {"id": "PVTSSF_status_1", "name": "Status",
              "options": [dict(o) for o in HUMAN_STATUS]}
    gh = boardfake.GitHub(boards=[{"title": "Team board", "number": 1,
                                   "fields": _many_fields(35, [status])}])
    rc, text = _run(_sdlc(tmp_path), gh, "--number", "1", "--yes")
    assert rc == 0, text
    assert [f["name"] for f in gh.board(number=1)["fields"]].count("Status") == 1


def test_a_priority_field_that_is_not_single_select_is_refused_once(tmp_path):
    prio = {"id": "PVTF_prio", "name": "Priority", "options": None, "data_type": "text"}
    gh, board = _human_board(extra_fields=[prio])
    rc, text = _run(_sdlc(tmp_path), gh, "--number", "1", "--yes")
    assert rc == 2 and "[REFUSED] Priority field" in text and "not single-select" in text
    assert "Resume" not in text and "project.priority_field" in text
    assert not any("createProjectV2Field(" in " ".join(c) for c in gh.mutations())


# (7) fake fidelity: numbers are per owner, a duplicate field name is rejected

def test_fake_board_numbers_are_per_owner_and_the_template_owner_is_honoured(tmp_path):
    gh = boardfake.GitHub(boards=[{"title": "Ours", "number": 1},
                                  {"title": "Their template", "number": 1, "owner": "octo"}])
    assert gh.board(number=1)["title"] == "Ours"
    assert gh.board(number=1, owner="octo")["title"] == "Their template"
    gh.board(number=1, owner="octo")["fields"].append(
        {"id": "PVTSSF_marker", "name": "Octo only", "options": [], "data_type": "single_select"})
    rc, text = _run(_sdlc(tmp_path), gh, "--template", "octo/1", "--yes")
    assert rc == 0, text
    made = gh.board(title="widget — SDLC")
    assert gh.field(made, "Octo only") is not None           # copied from octo's #1, not ours


def test_fake_rejects_a_duplicate_field_name():
    gh = _org_with_boards()
    doc = ('mutation { createProjectV2Field(input: {projectId: "PVT_1", dataType: SINGLE_SELECT, '
           'name: "Status", singleSelectOptions: [{name: "A", color: GRAY, description: ""}]}) '
           '{ projectV2Field { ... on ProjectV2SingleSelectField { id } } } }')
    rc, _out, err = gh.gh(["gh", "api", "graphql", "-f", "query=" + doc])
    assert rc == 1 and "already been taken" in err


# (8) the printed resume command on Windows

def test_windows_resume_uses_double_quotes_and_refuses_expanding_characters(monkeypatch, tmp_path):
    monkeypatch.setattr(bs, "_windows", lambda: True)
    line = bs.resume_command(tmp_path, "acme", "widget — SDLC", 3)
    assert '--title "widget — SDLC"' in line and "'" not in line.split(" create ", 1)[1]
    for bad in ('say "hi"', "100% done", "cost $x", "wow!", "tick`s"):
        assert bs.resume_command(tmp_path, "acme", bad, 3) is None


def test_windows_failure_with_an_unquotable_title_says_how_to_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(bs, "_windows", lambda: True)
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    gh.fail["createProjectV2Field("] = "GraphQL: secondary rate limit"
    rc, text = _run(sdlc, gh, "--title", "100% done", "--yes")
    assert rc == 1 and "cmd/PowerShell" in text and "--number" in text


# ---------------------------------------------------------------- review of PR #279, block #2
# `Ready` is the loop's QUEUE SWITCH (`sources._ready_lane`): without it the loop picks by label;
# with it (and the shipped `queue_source: "status"`) only a card IN Ready is picked, and the
# uncarded fallback covers issues with NO card. So adding `Ready` to a board that already carries
# cards strands every goal card sitting in another lane, and the loop reads DONE. board_setup adds
# it only to a board it created itself that has no card yet; everywhere else the switch stays the
# explicit, seeding `board_migrate.py` step (#696).

def _label_issue(gh, n):
    gh.issues.append({"number": n, "labels": [{"name": "sdlc:goal"}], "title": "t", "body": ""})


def test_adopting_a_board_with_goal_cards_keeps_the_loop_picking_them(tmp_path, capsys):
    """The reviewer's reproduction (evidence/235/rev/test_rev_queue.py::test_adopt_flips_queue):
    human board, goal cards #5/#6/#7; before the fix the pick went '5' -> None, silently."""
    gh, board = _human_board()
    for n in (5, 6, 7):
        _label_issue(gh, n)
    for it in board["items"]:
        it["labels"] = ["sdlc:goal"]
    sdlc = _sdlc(tmp_path, project={"number": 1})
    before = _src(sdlc, gh).next_pending()
    rc, text = _run(sdlc, gh, "--number", "1", "--yes")
    assert rc == 0, text
    assert before == "5" and _src(sdlc, gh).next_pending() == before
    assert "Ready" not in gh.option_names(board, "Status")
    assert "[skip] Ready lane" in text and "label queue" in text
    assert "board_migrate.py" in text and "--owner acme --project 1" in text and "--apply" in text


def test_a_resume_after_a_loop_tick_does_not_strand_the_cards_the_tick_made(tmp_path, capsys):
    """The reviewer's second reproduction: the first run fails before Status is set, a loop tick
    cards #11/#12 (Status None) on the half-built board, then the resume. Adding `Ready` then
    would strand both cards (carded, so the uncarded fallback skips them)."""
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    _label_issue(gh, 11)
    _label_issue(gh, 12)
    gh.fail["updateProjectV2Field("] = "GraphQL: secondary rate limit"
    assert _run(sdlc, gh, "--yes")[0] == 1
    del gh.fail["updateProjectV2Field("]
    board = gh.board(title="widget — SDLC")
    s = _src(sdlc, gh)
    p = s.next_pending()
    s.mark_in_progress(p)
    assert len(board["items"]) == 2
    rc, text = _run(sdlc, gh, "--number", str(board["number"]), "--yes")
    assert rc == 0, text
    for it in board["items"]:
        it["labels"] = ["sdlc:goal"]
    assert "Ready" not in gh.option_names(board, "Status")
    assert _src(sdlc, gh).next_pending(skip=[p]) == ({"11", "12"} - {p}).pop()
    assert "[skip] Ready lane" in text and "already has card(s)" in text


def test_a_resume_of_the_empty_board_this_setup_created_finishes_it_like_a_fresh_one(tmp_path):
    """Nothing can be stranded on a board that has no card, and it is ours (config's
    `setup_created`), so the resume completes it exactly as the first run would have: GitHub's
    `Todo` / `In progress` renamed (ids kept), `Ready` added -- no stray lane, no config mapping."""
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    _label_issue(gh, 11)
    gh.fail["updateProjectV2Field("] = "GraphQL: secondary rate limit"
    assert _run(sdlc, gh, "--yes")[0] == 1
    del gh.fail["updateProjectV2Field("]
    board = gh.board(title="widget — SDLC")
    assert _cfg(sdlc)["discovery"]["github"]["project"]["setup_created"] == {
        "number": board["number"], "owner": "acme"}
    rc, text = _run(sdlc, gh, "--number", str(board["number"]), "--yes")
    assert rc == 0, text
    assert gh.option_names(board, "Status") == COLS
    assert "columns" not in _cfg(sdlc)["discovery"]["github"]["project"]
    assert all(board["workflows"].values())
    src = _src(sdlc, gh)
    assert src.next_pending() == "11"
    src.mark_in_progress("11")
    assert _card_status(gh, board, 11) == "In Progress"


def test_an_adopted_empty_board_still_does_not_get_ready(tmp_path):
    """No `setup_created` for this number: a human's board, even an empty one. Its lanes are theirs
    and cards they add later would land outside Ready -- the switch stays board_migrate's."""
    gh = boardfake.GitHub(boards=[{"title": "Team board", "number": 4}])
    sdlc = _sdlc(tmp_path, project={"setup_created": 9})         # ours was a DIFFERENT board
    rc, text = _run(sdlc, gh, "--number", "4", "--yes")
    assert rc == 0, text
    assert "Ready" not in gh.option_names(gh.board(number=4), "Status")
    assert "setup_created" not in _cfg(sdlc)["discovery"]["github"]["project"]


def test_a_resume_with_the_old_bare_marker_drops_it_and_the_board_stays_a_humans(tmp_path):
    """#308 review block #1, through the documented gesture (`board_setup.py create --number N
    --yes`): a pre-#233 config says `setup_created: 4` beside `owner: acme`. A bare number cannot
    vouch for its owner (the pre-#233 pin() kept it when only the owner changed), so the re-pin
    DROPS it and the run treats board #4 as a human's: no Ready lane."""
    gh = boardfake.GitHub(boards=[{"title": "widget — SDLC", "number": 4}])
    sdlc = _sdlc(tmp_path, project={"number": 4, "owner": "acme", "setup_created": 4})
    rc, text = _run(sdlc, gh, "--number", "4", "--yes")
    assert rc == 0, text
    assert "setup_created" not in _cfg(sdlc)["discovery"]["github"]["project"]
    assert "Ready" not in gh.option_names(gh.board(number=4), "Status")


def _documented_marker_recovery_commands():
    doc = (ROOT / "docs" / "board-fields.md").read_text(encoding="utf-8")
    return doc.split("<!-- setup-created-recovery -->", 1)[1].split("```sh", 1)[1].split("```", 1)[0]


def _documented_marker_recovery():
    first, rest = _documented_marker_recovery_commands().strip().split("\n", 1)
    assert first.startswith("python3 - <<'PY'")
    python, command = rest.split("\nPY\n", 1)
    return python, shlex.split(command)


def test_documented_marker_recovery_stops_before_board_setup_if_config_edit_fails(tmp_path):
    """The pasted runbook must not adopt/mutate the old pinned board after its edit fails."""
    tools = tmp_path / "bin"
    tools.mkdir()
    python = tools / "python3"
    python.write_text('#!/bin/sh\nif [ "$1" = "-" ]; then exit 97; fi\n'
                      ': > "${0%/*}/board-setup-called"\n')
    python.chmod(0o755)
    result = subprocess.run(["/bin/sh", "-c", _documented_marker_recovery_commands()],
                            cwd=tmp_path, env={"PATH": str(tools), "SIGMA_PLUGIN_ROOT": str(ROOT)},
                            capture_output=True, text=True, timeout=10)
    assert not (tools / "board-setup-called").exists()
    assert result.returncode == 97, result.stderr


def test_documented_marker_recovery_creates_a_separate_owned_board(tmp_path):
    """#317: execute the runbook, including its config edit, against the real CLI parser.
    Dropping the unpin command makes create reuse #4; dropping the unique title refuses the
    duplicate. Neither outcome is the new owned board the documentation promises."""
    gh = boardfake.GitHub(boards=[{"title": "widget — SDLC", "number": 4}])
    old = gh.board(number=4)
    gh.add_item(old, 11, Status="Todo")
    before = json.loads(json.dumps(old))
    sdlc = _sdlc(tmp_path, project={"number": 4, "owner": "acme", "setup_created": 4})
    code, argv = _documented_marker_recovery()
    subprocess.run([sys.executable, "-"], input=code, cwd=tmp_path, check=True,
                   capture_output=True, text=True, timeout=10)
    assert argv[0] == "python3"
    script = pathlib.Path(argv[1].replace("$SIGMA_PLUGIN_ROOT", str(ROOT)))
    assert script == SCRIPTS / "board_setup.py"
    # Same arguments as the docs; only substitute the fixture's project root.
    assert argv[2:4] == ["create", ".sdlc"]
    lines = []
    rc = bs.main([str(script), *argv[2:3], str(sdlc), *argv[4:]],
                 runner=gh.gh, out=lines.append)
    assert rc == 0, "\n".join(lines)

    proj = _cfg(sdlc)["discovery"]["github"]["project"]
    assert proj["number"] != 4
    assert proj["setup_created"] == {"number": proj["number"], "owner": "acme"}
    assert "Ready" in gh.option_names(gh.board(number=proj["number"]), "Status")
    assert len(gh.boards) == 2
    assert old == before, "the previous board and its cards must be untouched"


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_documented_marker_recovery_preserves_config_when_write_fails(tmp_path, monkeypatch, failure):
    """#317 independent review: failed writes or replaces leave the entire config recoverable."""
    sdlc = _sdlc(tmp_path, project={"number": 4, "owner": "acme", "setup_created": 4})
    config_path = sdlc / "config.json"
    before = config_path.read_bytes()
    code, _argv = _documented_marker_recovery()
    real_open = io.open

    class DiskFull:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, text):
            self.stream.write(text[:1])
            raise OSError(errno.ENOSPC, "injected disk full")

    def failing_open(file, mode="r", *args, **kwargs):
        stream = real_open(file, mode, *args, **kwargs)
        return DiskFull(stream) if "w" in mode else stream

    def failed_replace(*args):
        raise OSError(errno.ENOSPC, "injected disk full")

    monkeypatch.chdir(tmp_path)
    if failure == "write":
        monkeypatch.setattr(io, "open", failing_open)
    else:
        monkeypatch.setattr(os, "replace", failed_replace)
    with pytest.raises(OSError, match="injected disk full"):
        exec(code, {})
    assert config_path.read_bytes() == before
    assert list(sdlc.iterdir()) == [config_path]


def test_the_reviewers_sequence_a_bare_marker_left_by_an_owner_change_is_not_ours(tmp_path):
    """#308 review block #1, the reviewer's exact sequence. The pre-#233 pin() created alice's #4
    (`setup_created: 4`), then re-pinned `--owner acme-org --number 4` -- a HAND-MADE board reusing
    the number -- and, dropping only on a number change, rewrote `owner` and KEPT the bare marker.
    `create --owner acme-org --number 4` must now drop it: not ours (so the loop neither mirrors
    Priority onto it nor stops writing the label), and no Ready lane."""
    gh = boardfake.GitHub(owner="acme-org",
                          boards=[{"title": "Hand-made board", "number": 4}])
    sdlc = _sdlc(tmp_path, project={"number": 4, "owner": "acme-org", "setup_created": 4})
    rc, text = _run(sdlc, gh, "--owner", "acme-org", "--number", "4", "--yes")
    assert rc == 0, text
    proj = _cfg(sdlc)["discovery"]["github"]["project"]
    assert "setup_created" not in proj
    assert not bs._ours(proj.get("setup_created"), 4, "acme-org")
    assert "Ready" not in gh.option_names(gh.board(number=4), "Status")


def test_pin_keeps_the_marker_only_for_the_same_board_of_the_same_owner(tmp_path):
    """#233 review block #2: `setup_created` names a board by number AND owner. Re-pinning the
    same number under another owner (a hand-made board reusing it) drops the marker; the older
    bare-number form cannot be vouched for and is dropped too; the same board keeps it."""
    sdlc = _sdlc(tmp_path)
    proj = lambda: _cfg(sdlc)["discovery"]["github"]["project"]            # noqa: E731
    bs.pin(sdlc, "acme", 7, created=True)
    assert proj()["setup_created"] == {"number": 7, "owner": "acme"}
    bs.pin(sdlc, "ACME", 7)                                                   # same board
    assert proj()["setup_created"] == {"number": 7, "owner": "acme"}
    bs.pin(sdlc, "octo", 7)                                                   # same number, not ours
    assert "setup_created" not in proj()
    bs.pin(sdlc, "acme", 7, created=True)
    bs.pin(sdlc, "acme", 8)                                                   # another number
    assert "setup_created" not in proj()
    path = sdlc / "config.json"
    cfg = _cfg(sdlc)
    cfg["discovery"]["github"]["project"]["setup_created"] = 7                # the pre-#233 form
    path.write_text(json.dumps(cfg), encoding="utf-8")
    bs.pin(sdlc, "acme", 7)
    assert "setup_created" not in proj()


@pytest.mark.parametrize("prior_owner,owner,number", [
    ("acme", "acme", 7),                 # same number, same owner: still cannot vouch for it
    ("acme", "ACME", 7),
    ("alice", "acme-org", 7),            # the pre-#233 owner-only change (marker was kept)
    ("acme-org", "acme-org", 7),         # a hand-edited `project.owner` beside the bare marker
    ("acme", "acme", 8),                 # another number
    (None, "acme", 7),                   # no owner pinned
    ("@me", "@me", 7),
])
def test_pin_always_drops_the_old_bare_number_marker(tmp_path, prior_owner, owner, number):
    """#308 review block #1: the pre-#233 bare-number `setup_created` has no reliable owner -- the
    old pin() kept it across an owner-only re-pin, and a hand edit of `project.owner` keeps it too
    -- so pin() drops it on every re-pin, exactly as #233 did, and it never reads as ours."""
    project = {"number": 7, "setup_created": 7}
    if prior_owner is not None:
        project["owner"] = prior_owner
    sdlc = _sdlc(tmp_path, project=project)
    bs.pin(sdlc, owner, number)
    proj = _cfg(sdlc)["discovery"]["github"]["project"]
    assert "setup_created" not in proj
    assert not bs._ours(proj.get("setup_created"), number, owner)


def test_a_resume_with_another_owners_marker_is_not_our_board(tmp_path):
    """The marker names board 3 of `octo`: acme's board 3 is a human's, so no Ready lane."""
    gh = boardfake.GitHub(boards=[{"title": "Team board", "number": 3}])
    sdlc = _sdlc(tmp_path, project={"setup_created": {"number": 3, "owner": "octo"}})
    rc, text = _run(sdlc, gh, "--number", "3", "--yes")
    assert rc == 0, text
    assert "Ready" not in gh.option_names(gh.board(number=3), "Status")
    assert "setup_created" not in _cfg(sdlc)["discovery"]["github"]["project"]


def test_an_unreadable_card_count_withholds_ready_and_fails_for_a_resume(tmp_path):
    gh, sdlc = _org_with_boards(), _sdlc(tmp_path)
    gh.fail["updateProjectV2Field("] = "GraphQL: secondary rate limit"
    assert _run(sdlc, gh, "--yes")[0] == 1
    del gh.fail["updateProjectV2Field("]
    board = gh.board(title="widget — SDLC")
    gh.fail["/items?per_page=1"] = "HTTP 502"
    rc, text = _run(sdlc, gh, "--number", str(board["number"]), "--yes")
    assert rc == 1 and "[FAIL] read cards" in text and "--number" in text
    assert "Ready" not in gh.option_names(board, "Status")


READY_HUMAN = {"id": "h_ready", "name": "Ready", "color": "RED", "description": "Our sprint"}


def test_an_adopted_board_whose_own_ready_lane_exists_is_left_untouched(tmp_path, capsys):
    """A human's own `Ready`: the board already IS the queue, so nothing about the switch changes.
    The option keeps id, name, colour, description and position; no card moves; the pick holds."""
    status = HUMAN_STATUS[:1] + [READY_HUMAN] + HUMAN_STATUS[1:]
    gh, board = _human_board(status=status)
    gh.add_item(board, 8, Status="Ready")
    for it in board["items"]:
        it["labels"] = ["sdlc:goal"]
    _label_issue(gh, 8)
    sdlc = _sdlc(tmp_path, project={"number": 1})
    values = {i["id"]: dict(i["values"]) for i in board["items"]}
    before = _src(sdlc, gh).next_pending()
    rc, text = _run(sdlc, gh, "--number", "1", "--yes")
    assert rc == 0, text
    assert gh.option(board, "Status", "Ready") == READY_HUMAN
    assert gh.option_names(board, "Status")[:5] == [o["name"] for o in status]
    assert {i["id"]: i["values"] for i in board["items"]} == values
    assert before == "8" and _src(sdlc, gh).next_pending() == "8"
    assert "already has" in text and "left as it is" in text


def test_adoption_keeps_the_humans_lane_order_and_appends_only_what_is_missing(tmp_path):
    gh, board = _human_board()
    rc, text = _run(_sdlc(tmp_path), gh, "--number", "1", "--yes")
    assert rc == 0, text
    assert gh.option_names(board, "Status") == [
        "Todo", "In progress", "Done", "Needs design", "Backlog", "QC", "Blocked", "Parked"]


# --------------------------------------------------------------------------- #895 slice 3b: argv via gh_api.gh_argv

def test_board_gh_builds_argv_through_gh_api_gh_argv(tmp_path):
    """RED BEFORE THE CHANGE (delegation spy)."""
    seen = []
    board = bs.Board(_sdlc(tmp_path), runner=lambda argv: (seen.append(list(argv)), (0, "ok", ""))[1])
    g = bs._gh_api()
    orig = g.gh_argv
    g.gh_argv = lambda a: ["SPY", *orig(a)]
    try:
        board.gh("api", "x")
    finally:
        g.gh_argv = orig
    assert seen == [["SPY", "gh", "api", "x"]]


def test_board_gh_argv_prefix_and_hostname_splice(tmp_path):
    """CHARACTERISATION (green today)."""
    seen = []
    runner = lambda argv: (seen.append(list(argv)), (0, "", ""))[1]
    sdlc = _sdlc(tmp_path)
    bs.Board(sdlc, runner=runner).gh("api", "x")
    bs.Board(sdlc, runner=runner, host="ghe.example").gh("api", "x")
    bs.Board(sdlc, runner=runner, host="ghe.example").gh("issue", "view")
    assert seen == [["gh", "api", "x"], ["gh", "api", "--hostname", "ghe.example", "x"], ["gh", "issue", "view"]]


def test_board_gh_api_is_loaded_lazily(tmp_path, monkeypatch):
    """RED BEFORE THE CHANGE (no `_GH_API`): a failing loader breaks the first `Board.gh`, not import/construct."""
    monkeypatch.setattr(bs, "_GH_API", [], raising=False)
    monkeypatch.setattr(bs, "_load", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no gh_api")))
    board = bs.Board(_sdlc(tmp_path), runner=lambda argv: (0, "", ""))
    with pytest.raises(RuntimeError, match="no gh_api"):
        board.gh("api", "x")


def test_board_gh_api_is_not_loaded_at_import_or_construction(tmp_path):
    """RED under an import-time `_gh_api()` call (the Control): a freshly executed module has loaded nothing,
    and constructing a Board loads nothing either."""
    fresh = _load(SCRIPTS / "board_setup.py", "board_setup_fresh_3b")
    assert fresh._GH_API == []
    fresh.Board(_sdlc(tmp_path), runner=lambda argv: (0, "", ""))
    assert fresh._GH_API == []

"""#235: `board_setup.py create` -- the opt-in gesture that creates (or finishes, or adopts) the
GitHub Project board and pins it. Hermetic: every gh call is answered by `boardfake.GitHub`, one
in-memory GitHub shared with the loop's own `sources.GitHubSource`, so the acceptance test drives
the REAL pick-time status write against the board this script created. No network, no mutation of
real GitHub."""
import importlib.util
import json
import pathlib
import shlex

import pytest

import boardfake

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "agrim-init" / "scripts"
LOOP = ROOT / "skills" / "agrim-loop" / "scripts"


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
    assert set(cfg["discovery"]["github"]["project"]) == {"enabled", "columns", "number", "owner"}


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
    assert gh.option_names(gh.board(number=7), "Status") == COLS


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
    _src(sdlc, gh).mark_in_progress("11")
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


# ---------------------------------------------------------------- /agrim-init offers it

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

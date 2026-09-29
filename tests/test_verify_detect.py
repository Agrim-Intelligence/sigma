"""#228: /agrim-init leaves a working verify command, never enforce ON with an empty one.

Portable by construction: every command a test RUNS is built from `sys.executable` (never a bare
`python3`, absent on many Windows installs), and no test shells out to bash."""
import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "agrim-init" / "scripts"
DETECT = SCRIPTS / "verify_detect.py"
INIT = SCRIPTS / "sdlc_init.py"
LOOP = ROOT / "skills" / "agrim-loop" / "scripts" / "loop.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vd = _load(DETECT, "verify_detect")


def _cmds(root):
    return [c["command"] for c in vd.detect(root)]


def _run(*args, cwd):
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True)


def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    for cmd in (["init", "-q"], ["config", "user.email", "t@t.test"], ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=path, check=True)
    return path


# ---------------------------------------------------------------- detection (reads, never runs)

def test_pytest_suite_is_proposed_as_python_m_pytest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    assert _cmds(tmp_path) == [f"{vd.python_command()} -m pytest -q"]


def test_python_command_is_python3_when_on_path(monkeypatch):
    monkeypatch.setattr(vd.shutil, "which", lambda name: "/x/" + name)
    assert vd.python_command() == "python3"
    monkeypatch.setattr(vd.shutil, "which", lambda name: "/x/python" if name == "python" else None)
    assert vd.python_command() == "python"


def test_pytest_config_markers_count_without_test_files(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = '-q'\n")
    assert _cmds(tmp_path)[0].endswith("-m pytest -q")


def test_pyproject_without_pytest_is_not_a_candidate(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert _cmds(tmp_path) == []


def test_empty_repo_has_no_candidate(tmp_path):
    assert vd.detect(tmp_path) == []


def test_package_json_test_script_and_lockfile_runner(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))
    assert _cmds(tmp_path) == ["npm test"]
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert _cmds(tmp_path) == ["pnpm test"]


def test_npm_placeholder_test_script_is_not_a_candidate(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}))
    assert _cmds(tmp_path) == []


def test_go_cargo_make_and_ci_step_in_documented_order(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    (tmp_path / "Cargo.toml").write_text("[package]\n")
    (tmp_path / "Makefile").write_text("build:\n\techo b\ntest:\n\techo t\n")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "jobs:\n  t:\n    steps:\n      - run: pip install -e .\n      - run: tox -e py312\n"
        "      - run: |\n          pytest\n      - run: go test ./...\n")
    assert _cmds(tmp_path) == ["go test ./...", "cargo test", "make test", "tox -e py312"]


def test_makefile_without_test_target_is_not_a_candidate(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\techo b\n")
    assert _cmds(tmp_path) == []


def test_scan_skips_virtualenvs_and_node_modules(tmp_path):
    for d in (".venv", "node_modules"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "test_vendored.py").write_text("")
    assert _cmds(tmp_path) == []


# ---------------------------------------------------------------- the writer's one invariant

def _cfg(tmp_path, verify):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(json.dumps({"mode": "x", "verify": verify}))
    return sdlc


def test_write_verify_with_command_turns_enforce_on_and_keeps_other_keys(tmp_path):
    sdlc = _cfg(tmp_path, {"command": "", "enforce": False})
    vd.write_verify(sdlc, "make test", "confirmed")
    cfg = json.loads((sdlc / "config.json").read_text())
    assert cfg["mode"] == "x"
    assert cfg["verify"]["command"] == "make test" and cfg["verify"]["enforce"] is True


def test_write_verify_without_command_is_never_enforce_on(tmp_path):
    sdlc = _cfg(tmp_path, {"command": "", "enforce": True})       # the trap, as found in the field
    for cmd in (None, "", "   "):
        vd.write_verify(sdlc, cmd, "declined")
        v = json.loads((sdlc / "config.json").read_text())["verify"]
        assert v["enforce"] is False and v["command"] == "" and v["_why"] == "declined"


def test_cli_set_refuses_an_empty_command(tmp_path):
    sdlc = _cfg(tmp_path, {"command": "", "enforce": False})
    r = _run(DETECT, "set", sdlc, "", cwd=tmp_path)
    assert r.returncode == 2 and "REFUSED" in r.stderr
    assert json.loads((sdlc / "config.json").read_text())["verify"]["enforce"] is False


def test_cli_decline_writes_enforce_off_with_the_reason(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    sdlc = _cfg(tmp_path, {"command": "", "enforce": True})
    r = _run(DETECT, "decline", sdlc, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    v = json.loads((sdlc / "config.json").read_text())["verify"]
    assert v["enforce"] is False and "declined" in v["_why"] and "go test ./..." in v["_why"]


# ---------------------------------------------------------------- /agrim-init integration

def test_fresh_scaffold_never_ships_enforce_on_with_an_empty_command(tmp_path):
    """The template is the shipped default, so check what scaffold() really writes."""
    init = _load(INIT, "sdlc_init")
    for repo, expect in (("bare", "found no test command"), ("py", "detected")):
        target = tmp_path / repo
        target.mkdir()
        if repo == "py":
            (target / "test_a.py").write_text("")
        init.scaffold(target)
        v = json.loads((target / ".sdlc" / "config.json").read_text())["verify"]
        assert not (v["enforce"] and not v["command"]), v
        assert v["enforce"] is False and expect in v["_why"]


def test_scaffold_never_rewrites_an_existing_config_but_warns_about_the_trap(tmp_path):
    sdlc = _cfg(tmp_path, {"command": "", "enforce": True})
    before = (sdlc / "config.json").read_text()
    r = _run(INIT, tmp_path, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (sdlc / "config.json").read_text() == before
    assert "EMPTY verify.command" in r.stdout


def test_init_prints_the_candidate_and_exact_config_line(tmp_path):
    """Codex/Cursor have no interactive question: the printed block IS the prompt."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("")
    r = _run(INIT, tmp_path, cwd=tmp_path)
    cmd = f"{vd.python_command()} -m pytest -q"
    assert f"detected `{cmd}`" in r.stdout
    assert json.dumps({"verify": {"command": cmd, "enforce": True}}) in r.stdout
    assert f'set .sdlc "{cmd}"' in r.stdout


def test_init_with_no_tests_says_enforce_is_off(tmp_path):
    r = _run(INIT, tmp_path, cwd=tmp_path)
    assert "none detected; verify.enforce is OFF" in r.stdout


def test_skill_and_templates_no_longer_send_the_command_to_project_md():
    skill = (ROOT / "skills" / "agrim-init" / "SKILL.md").read_text(encoding="utf-8")
    tmpl = (ROOT / "skills" / "agrim-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8")
    assert "fill its **Verify command**" not in skill
    assert "fill in Verify command in project.md" not in tmpl
    assert "verify_detect.py\" set .sdlc" in skill and "decline" in skill


# ---------------------------------------------------------------- end to end, no network

def _loop(sdlc, *args):
    return _run(LOOP, *args, cwd=sdlc.parent)


def test_confirmed_pytest_candidate_makes_verify_and_record_done_pass(tmp_path):
    repo = _git_repo(tmp_path / "r")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    assert _run(INIT, repo, cwd=repo).returncode == 0
    sdlc = repo / ".sdlc"
    # The candidate's interpreter is swapped for this test's own so the run is portable; the
    # proposal itself is pinned by test_init_prints_the_candidate_and_exact_config_line.
    assert _run(DETECT, "set", sdlc, f'"{sys.executable}" -m pytest -q -p no:cacheprovider',
                cwd=repo).returncode == 0
    goal = ".sdlc/goals/0001-example.md"
    assert _loop(sdlc, "start", ".sdlc").returncode == 0
    v = _loop(sdlc, "verify", ".sdlc", goal)
    assert v.returncode == 0, v.stderr
    d = _loop(sdlc, "record", ".sdlc", goal, "done")
    assert d.returncode == 0, d.stderr


def _demo_repo(tmp_path):
    repo = _git_repo(tmp_path / "demo")
    assert _run(INIT, repo, "--demo", cwd=repo).returncode == 0
    sdlc = repo / ".sdlc"
    cfg = json.loads((sdlc / "config.json").read_text())
    cfg["verify"] = {"command": "", "enforce": True}   # the pre-#228 shipped state, the hard case
    (sdlc / "config.json").write_text(json.dumps(cfg))
    goal = sdlc / "goals" / "0000-demo.md"
    # Portability: the demo's `python3` may be absent on Windows; run the same check with ours.
    # Unquoted on purpose: the frontmatter parser strips a leading `"` (see research/228.md).
    goal.write_text(goal.read_text().replace("verify_command: python3 ",
                                             f"verify_command: {sys.executable} "))
    assert _loop(sdlc, "start", ".sdlc").returncode == 0
    return repo, sdlc


def test_demo_goal_reaches_done_with_enforce_on_and_no_config_command(tmp_path):
    repo, sdlc = _demo_repo(tmp_path)
    goal = ".sdlc/goals/0000-demo.md"
    (repo / "sigma-demo.md").write_text("Sigma ran this goal.\n")
    v = _loop(sdlc, "verify", ".sdlc", goal)
    assert v.returncode == 0, v.stderr
    d = _loop(sdlc, "record", ".sdlc", goal, "done")
    assert d.returncode == 0, d.stderr


def test_demo_verify_command_really_checks_done_when(tmp_path):
    """Not a rubber stamp: without sigma-demo.md the demo's own check fails and done is refused."""
    repo, sdlc = _demo_repo(tmp_path)
    goal = ".sdlc/goals/0000-demo.md"
    assert _loop(sdlc, "verify", ".sdlc", goal).returncode == 1
    d = _loop(sdlc, "record", ".sdlc", goal, "done")
    assert d.returncode == 4 and "last verify FAILED" in d.stderr


def test_record_done_names_the_missing_command_not_run_verify_first(tmp_path):
    repo, sdlc = _demo_repo(tmp_path)
    goal = sdlc / "goals" / "0000-demo.md"
    goal.write_text("".join(l for l in goal.read_text().splitlines(True)
                            if not l.startswith("verify_command:")))
    assert _loop(sdlc, "verify", ".sdlc", ".sdlc/goals/0000-demo.md").returncode == 3
    d = _loop(sdlc, "record", ".sdlc", ".sdlc/goals/0000-demo.md", "done")
    assert d.returncode == 4 and "no verify command declared" in d.stderr
    assert "verify_detect.py set .sdlc" in d.stderr


def test_the_shipped_template_itself_never_holds_the_trap():
    """Defence in depth: scaffold() rewrites the verify block, but the template is what any other
    copier of it would ship -- it must not hold enforce ON with an empty command either."""
    raw = (ROOT / "skills" / "agrim-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8")
    v = json.loads(raw)["verify"]
    assert not (v.get("enforce") and not v.get("command")), v

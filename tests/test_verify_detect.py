"""#228: /agrim-init leaves a working verify command, never enforce ON with an empty one.

Portable by construction: every command a test RUNS is built from `sys.executable` (never a bare
`python3`, absent on many Windows installs), and no test shells out to bash."""
import importlib.util
import json
import os
import pathlib
import shlex
import subprocess
import sys

import pytest

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
    if args and args[0] == INIT:                   # #229: init refuses a non-git directory
        target = pathlib.Path(cwd) / str(args[1]) if len(args) > 1 else pathlib.Path(cwd)
        if target.is_dir() and not (target / ".git").exists():
            subprocess.run(["git", "init", "-q", str(target)], check=True)
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True)


def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    for cmd in (["init", "-q"], ["config", "user.email", "t@t.test"], ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=path, check=True)
    return path


def _shell_commands_are_trusted(repo):
    return subprocess.run(
        ["git", "config", "--local", "--get", "sigma.allowRepositoryShellCommands"],
        cwd=repo, capture_output=True, text=True,
    ).returncode == 0


def _trust_shell_commands(repo):
    subprocess.run(
        ["git", "config", "--local", "sigma.allowRepositoryShellCommands", "true"],
        cwd=repo, check=True,
    )


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
    monkeypatch.setattr(vd.shutil, "which", lambda name: "/x/py" if name == "py" else None)
    assert vd.python_command() == "py"


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
    # One coherent message: the trap is ON, so nothing may also claim enforce is OFF.
    assert "stays OFF" not in r.stdout and "enforce is OFF" not in r.stdout, r.stdout


def test_init_prints_the_candidate_and_exact_config_line(tmp_path):
    """Codex/Cursor have no interactive question: the printed block IS the prompt."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("")
    r = _run(INIT, tmp_path, cwd=tmp_path)
    cmd = f"{vd.python_command()} -m pytest -q"
    assert f"detected `{cmd}`" in r.stdout
    assert json.dumps({"verify": {"command": cmd, "enforce": True}}) in r.stdout
    assert f"confirm {vd._q(os.path.abspath(str(tmp_path / '.sdlc')))} 1 {vd.command_id(cmd)}" in r.stdout
    assert f'"{cmd}"' not in r.stdout.replace(json.dumps(cmd), "")   # never inside a shell gesture


def test_init_with_no_tests_says_enforce_is_off(tmp_path):
    r = _run(INIT, tmp_path, cwd=tmp_path)
    assert "none detected; verify.enforce is OFF" in r.stdout


def test_skill_and_templates_no_longer_send_the_command_to_project_md():
    skill = (ROOT / "skills" / "agrim-init" / "SKILL.md").read_text(encoding="utf-8")
    tmpl = (ROOT / "skills" / "agrim-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8")
    assert "fill its **Verify command**" not in skill
    assert "fill in Verify command in project.md" not in tmpl
    assert "verify_detect.py\" confirm .sdlc <n> <id>" in skill and "decline" in skill
    assert "confirm .sdlc <n>`" not in skill          # the position-only form is gone (#246 review 2)
    # #228 review: the gesture that pasted repository text into a shell is gone from the docs.
    assert 'set .sdlc "<command>"' not in skill


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
    assert _shell_commands_are_trusted(repo)
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
    # This fixture deliberately executes the shipped, repo-controlled goal
    # command.  It is not a default-refusal control, so model the operator's
    # separate explicit local trust decision.
    _trust_shell_commands(repo)
    goal = sdlc / "goals" / "0000-demo.md"
    # Portability: the demo's `python3` may be absent on Windows; run the same check with ours.
    # Unquoted on purpose: the frontmatter parser strips a leading `"` (see research/228.md).
    demo_python = f"verify_command: {vd.python_command()} "
    assert demo_python in goal.read_text()          # the interpreter on THIS machine's PATH
    goal.write_text(goal.read_text().replace(demo_python, f"verify_command: {sys.executable} "))
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
    assert "confirm .sdlc <n> <id>" in d.stderr and f"{vd.python_command()} <installed-sigma>" in d.stderr


def test_the_shipped_template_itself_never_holds_the_trap():
    """Defence in depth: scaffold() rewrites the verify block, but the template is what any other
    copier of it would ship -- it must not hold enforce ON with an empty command either."""
    raw = (ROOT / "skills" / "agrim-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8")
    v = json.loads(raw)["verify"]
    assert not (v.get("enforce") and not v.get("command")), v


def test_goal_verify_command_single_quoted_empty_counts_as_empty(tmp_path):
    """`verify_command: ''` declares nothing (doctor already says so). loop.py must agree: NO-COMMAND
    (exit 3), not a shell run of the two-character command `''` (exit 1)."""
    repo, sdlc = _demo_repo(tmp_path)
    goal = sdlc / "goals" / "0000-demo.md"
    lines = [("verify_command: ''\n" if l.startswith("verify_command:") else l)
             for l in goal.read_text().splitlines(True)]
    goal.write_text("".join(lines))
    assert _loop(sdlc, "verify", ".sdlc", ".sdlc/goals/0000-demo.md").returncode == 3
    d = _loop(sdlc, "record", ".sdlc", ".sdlc/goals/0000-demo.md", "done")
    assert d.returncode == 4 and "no verify command declared" in d.stderr


# ---------------------------------------------------------------- hostile repository text (#246 review)
#
# A CI `run:` line is written by whoever wrote the repo. The printed confirm gesture must never
# carry it into a shell, and what is stored must be exactly what was shown.

def _ci_repo(root, run_line, name="t.yml"):
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / name).write_text("jobs:\n  t:\n    steps:\n      - run: " + run_line + "\n")
    return root


def _hostile(mark):
    m = str(mark)
    return {
        "substitution": f"pytest -q $(touch {m})",
        "backticks": f"pytest -q `touch {m}`",
        "quote-breakout": f'pytest -k x" ; touch {m} ; echo "y',
        "chain": f"pytest ; touch {m}",
        "pipe": f"pytest | tee {m}",
        "redirect": f"pytest > {m}",
        "param-expansion": "pytest ${HOME}",
        "esc": "pytest \x1b[2K\r\x1b[1A",
        "bidi": "pytest \u202e",
    }


@pytest.mark.parametrize("kind", sorted(_hostile("M")))
def test_hostile_ci_step_is_never_proposed_and_never_printed(tmp_path, kind):
    run_line = _hostile(tmp_path / "MARK")[kind]
    root = _ci_repo(tmp_path / "r", run_line)
    skipped = []
    assert vd.detect(root, skipped) == [] and skipped == ["CI step in .github/workflows/t.yml"]
    printed = "\n".join(vd.proposal_lines([], skipped))
    assert run_line not in printed and "\x1b" not in printed and "not proposed" in printed


def test_benign_quoted_ci_step_is_proposed_and_confirm_stores_it_byte_identical(tmp_path):
    root = _ci_repo(_git_repo(tmp_path / "r"), 'pytest -m "not slow"')
    assert _cmds(root) == ['pytest -m "not slow"']
    sdlc = _cfg(root, {"command": "", "enforce": False})
    r = _run(DETECT, "confirm", sdlc, "1", vd.command_id('pytest -m "not slow"'), cwd=root)
    assert r.returncode == 0, r.stderr
    v = json.loads((sdlc / "config.json").read_text())["verify"]
    assert v["command"] == 'pytest -m "not slow"' and v["enforce"] is True
    assert json.dumps('pytest -m "not slow"') in r.stdout
    assert _shell_commands_are_trusted(root)


def test_confirm_refuses_an_index_that_is_not_a_candidate(tmp_path):
    root = _ci_repo(tmp_path / "r", "pytest -q")
    sdlc = _cfg(root, {"command": "", "enforce": False})
    for bad in ("0", "2", "x", "-1"):
        r = _run(DETECT, "confirm", sdlc, bad, vd.command_id("pytest -q"), cwd=root)
        assert r.returncode == 2 and "REFUSED" in r.stderr, bad
    assert json.loads((sdlc / "config.json").read_text())["verify"]["enforce"] is False


def test_printable_escapes_control_and_format_characters():
    assert vd.printable("a\x1b[2Kb\r\n\u202ec") == "a\\x1b[2Kb\\r\\n\\u202ec"
    assert vd.printable('pytest -m "not slow"') == 'pytest -m "not slow"'


def test_escape_sequence_in_a_workflow_file_name_is_escaped_when_printed(tmp_path):
    root = _ci_repo(tmp_path / "r", "pytest -q", name="a\x1b[2Kb.yml")
    printed = "\n".join(vd.proposal_lines(vd.detect(root)))
    assert "\x1b" not in printed and "a\\x1b[2Kb.yml" in printed


@pytest.mark.parametrize("how", ["file", "stdin", "argv"])
def test_set_stores_the_users_own_command_verbatim_from_file_stdin_or_argv(tmp_path, how):
    repo = _git_repo(tmp_path / "r")
    sdlc = _cfg(repo, {"command": "", "enforce": False})
    cmd = 'pytest -m "not slow" -k \'a or b\''
    if how == "file":
        f = repo / "cmd.txt"
        f.write_text(cmd + "\n")
        r = _run(DETECT, "set", sdlc, "--command-file", f, cwd=repo)
    elif how == "stdin":
        r = subprocess.run([sys.executable, str(DETECT), "set", str(sdlc), "-"], input=cmd + "\n",
                           cwd=repo, capture_output=True, text=True)
    else:
        r = _run(DETECT, "set", sdlc, cmd, cwd=repo)
    assert r.returncode == 0, r.stderr
    assert json.loads((sdlc / "config.json").read_text())["verify"]["command"] == cmd
    assert json.dumps(cmd) in r.stdout
    assert _shell_commands_are_trusted(repo)


def test_set_refuses_to_enable_a_verify_command_outside_a_git_worktree(tmp_path):
    sdlc = _cfg(tmp_path, {"command": "", "enforce": False})
    r = _run(DETECT, "set", sdlc, "pytest -q", cwd=tmp_path)
    assert r.returncode == 2 and "Git worktree" in r.stderr
    assert json.loads((sdlc / "config.json").read_text())["verify"]["enforce"] is False


@pytest.mark.parametrize("bad", ["pytest\ntouch X", "pytest \x1b[2K", "pytest\rX"])
def test_set_refuses_a_multiline_or_control_character_command(tmp_path, bad):
    sdlc = _cfg(tmp_path, {"command": "", "enforce": False})
    f = tmp_path / "cmd.txt"
    f.write_text(bad)
    r = _run(DETECT, "set", sdlc, "--command-file", f, cwd=tmp_path)
    assert r.returncode == 2 and "REFUSED" in r.stderr
    assert json.loads((sdlc / "config.json").read_text())["verify"]["enforce"] is False


def _confirm_lines(stdout):
    """The printed lines a user would paste to record a detected candidate: every line that runs
    verify_detect.py with `confirm` or `set` (the pre-fix gesture was `set .sdlc "<candidate>"`, so
    a filter blind to `set` would let this test pass against the very bug it targets)."""
    return [l.strip() for l in stdout.splitlines()
            if "verify_detect.py" in l and (" confirm " in l or " set " in l)
            and not l.strip().startswith(("agrim-init", "{", "other candidate", "or "))]


@pytest.mark.skipif(os.name == "nt", reason="executes the printed gesture through a POSIX shell")
@pytest.mark.parametrize("kind", ["substitution", "backticks", "quote-breakout", "chain", "benign"])
def test_pasting_the_printed_confirm_gesture_runs_nothing_and_stores_what_was_shown(tmp_path, kind):
    """The review's reproduction, as a test: scaffold a repo whose only candidate is a CI step,
    take the confirm line /agrim-init PRINTS, paste it into `sh -c` exactly as a user would, and
    assert no side-effect file appears and the stored command equals the one shown."""
    mark = tmp_path / "PWNED"
    run_line = 'pytest -m "not slow"' if kind == "benign" else _hostile(mark)[kind]
    repo = _ci_repo(_git_repo(tmp_path / "r"), run_line)
    r = _run(INIT, repo, cwd=repo)
    assert r.returncode == 0, r.stderr
    shown = [json.loads(l.strip())["verify"]["command"] for l in r.stdout.splitlines()
             if l.strip().startswith('{"verify"')]
    confirm = _confirm_lines(r.stdout)
    if kind == "benign":
        assert shown == [run_line] and len(confirm) == 1, r.stdout
    for line in confirm:
        # Only the interpreter token is swapped (portability: `python3` may be another install).
        pasted = shlex.quote(sys.executable) + line[line.index(" "):]
        subprocess.run(["sh", "-c", pasted], cwd=repo, capture_output=True, text=True)
    assert not mark.exists(), f"pasted gesture executed repository text: {confirm}"
    stored = json.loads((repo / ".sdlc" / "config.json").read_text())["verify"]
    if kind == "benign":
        assert stored["command"] == shown[0] == run_line and stored["enforce"] is True
    else:
        assert shown == [] and confirm == [] and stored["enforce"] is False
        assert "not proposed" in r.stdout and run_line not in r.stdout


# ---------------------------------------------------------------- confirm is bound to what was SHOWN (#246 review 2)
#
# `confirm` re-runs detection, so a bare position `n` names whatever is n-th in a FRESH list. The
# reviewer's two repositories: one new root entry between the report and the confirm (AGENTS.md
# written by --codex AFTER the report was printed; .pytest_cache from running pytest once) pushed
# the pytest test file past the scan cap, and `confirm .sdlc 1` stored the next candidate instead
# -- a hostile CI step, or `make test`. These tests take the gesture /agrim-init PRINTS and run it.

def _argv(line):
    """A printed gesture line split the way the host's shell would split it."""
    if os.name == "nt":
        import re as _re
        return [t[1:-1] if t.startswith('"') else t for t in _re.findall(r'"[^"]*"|\S+', line)]
    return shlex.split(line)


def _printed_confirm(stdout, n=1):
    """argv (minus the interpreter) of the printed confirm gesture for candidate n."""
    for line in stdout.splitlines():
        if "verify_detect.py" not in line or " confirm " not in line:
            continue
        toks = _argv(line.strip().split("-- confirm with: ")[-1])
        i = toks.index("confirm")
        if toks[i + 2] == str(n):
            return toks[1:]
    raise AssertionError(f"no printed confirm gesture for candidate {n}:\n{stdout}")


def _fill(root, count):
    for i in range(count):
        (root / f"f{i:03d}").write_text("")


def _stored(repo):
    return json.loads((repo / ".sdlc" / "config.json").read_text())["verify"]


def test_cap_codex_repo_confirm_1_stores_the_pytest_candidate_not_the_ci_step(tmp_path):
    """The reviewer's cap--codex repo: 195 filler files, one test file sorting last, and a CI step
    `go test -exec "touch X" ./...` (no rejected metacharacter). `--codex` writes AGENTS.md after
    the report; pre-fix, that one root entry shifted candidate 1 to the CI step."""
    mark = tmp_path / "PWNED_cap"
    repo = _ci_repo(_git_repo(tmp_path / "cap"), f'go test -exec "touch {mark}" ./...')
    _fill(repo, 195)
    (repo / "test_z.py").write_text("def test_z():\n    pass\n")
    r = _run(INIT, repo, "--codex", cwd=repo)
    assert r.returncode == 0, r.stderr
    pytest_cmd = f"{vd.python_command()} -m pytest -q"
    assert f"detected `{pytest_cmd}`" in r.stdout
    c = _run(*_printed_confirm(r.stdout, 1), cwd=repo)
    stored = _stored(repo)
    assert "go test" not in stored["command"], (c.stdout, c.stderr)
    assert c.returncode == 0 and stored["command"] == pytest_cmd and stored["enforce"] is True, c.stderr
    assert not mark.exists()


def test_benign_repo_running_pytest_once_does_not_turn_confirm_1_into_make_test(tmp_path):
    """The reviewer's benign repo: running pytest once creates .pytest_cache, one more root entry;
    pre-fix `confirm .sdlc 1` then stored `make test` though the user confirmed pytest."""
    repo = _git_repo(tmp_path / "benign")
    (repo / "Makefile").write_text("test:\n\techo make-ran\n")
    (repo / "__pycache__").mkdir()
    _fill(repo, 194)
    (repo / "test_z.py").write_text("def test_z():\n    pass\n")
    r = _run(INIT, repo, cwd=repo)
    assert r.returncode == 0, r.stderr
    pytest_cmd = f"{vd.python_command()} -m pytest -q"
    assert f"detected `{pytest_cmd}`" in r.stdout
    (repo / ".pytest_cache").mkdir()                        # the user runs pytest once
    c = _run(*_printed_confirm(r.stdout, 1), cwd=repo)
    assert c.returncode == 0, c.stderr
    assert _stored(repo)["command"] == pytest_cmd


def test_confirm_refuses_when_the_candidate_changed_since_the_report(tmp_path):
    """The hash, not the position, names what the user confirmed: if the repository changed so
    candidate n is now a different command, confirm stores NOTHING and says to re-run init."""
    mark = tmp_path / "PWNED"
    repo = _ci_repo(_git_repo(tmp_path / "r"), f'go test -exec "touch {mark}" ./...')
    (repo / "test_z.py").write_text("")
    r = _run(INIT, repo, cwd=repo)
    argv = _printed_confirm(r.stdout, 1)
    (repo / "test_z.py").unlink()                           # candidate 1 is now the CI step
    c = _run(*argv, cwd=repo)
    assert c.returncode == 2 and "REFUSED" in c.stderr, (c.stdout, c.stderr)
    assert "repository changed since the report" in c.stderr and "Re-run /agrim-init" in c.stderr
    assert _stored(repo)["enforce"] is False and _stored(repo)["command"] == ""
    assert not mark.exists()


def test_confirm_requires_the_printed_id_and_rejects_a_wrong_one(tmp_path):
    root = _ci_repo(_git_repo(tmp_path / "r"), "pytest -q")
    sdlc = _cfg(root, {"command": "", "enforce": False})
    cid = vd.detect(root)[0]["id"]
    assert cid == vd.command_id("pytest -q")
    for args in (["1"], ["1", "0" * len(cid)], ["1", cid.upper()], ["1", cid[:-1]], ["2", cid]):
        r = _run(DETECT, "confirm", sdlc, *args, cwd=root)
        assert r.returncode == 2, args
        assert json.loads((sdlc / "config.json").read_text())["verify"]["enforce"] is False, args
    r = _run(DETECT, "confirm", sdlc, "1", cid, cwd=root)
    assert r.returncode == 0, r.stderr
    assert json.loads((sdlc / "config.json").read_text())["verify"]["command"] == "pytest -q"


def test_detection_ignores_hidden_cache_vendored_and_non_source_entries(tmp_path):
    """Deterministic: what init and tool runs write at the root never changes what is detected.
    Padded with .py files so the test file is EXACTLY the cap-th counted entry: any hidden or
    non-source entry that counted would push it past the cap and turn this red."""
    for i in range(vd._SCAN_CAP - 1):
        (tmp_path / f"m{i:03d}.py").write_text("")
    (tmp_path / "test_z.py").write_text("")
    before = vd.detect(tmp_path)
    assert before and before[0]["command"].endswith("-m pytest -q")
    for d in (".pytest_cache", ".sdlc", ".cursor", ".venv", ".git", "__pycache__", "node_modules"):
        (tmp_path / d).mkdir()
    for f in ("AGENTS.md", ".gitignore", "out.txt", "README.md"):
        (tmp_path / f).write_text("")
    assert vd.detect(tmp_path) == before


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="needs POSIX symlinks")
def test_a_symlinked_sdlc_is_refused_and_the_other_repo_is_never_written(tmp_path):
    """The reviewer's sym repo: .sdlc -> ../real/.sdlc. Detecting in one repository and writing
    another's config is refused loudly, for every writing verb."""
    real = tmp_path / "real"
    (real / ".sdlc").mkdir(parents=True)
    (real / "go.mod").write_text("module x\n")
    cfg = real / ".sdlc" / "config.json"
    cfg.write_text(json.dumps({"verify": {"command": "", "enforce": False}}))
    before = cfg.read_text()
    sym = tmp_path / "sym"
    (sym / "tests").mkdir(parents=True)
    (sym / "tests" / "test_a.py").write_text("")
    os.symlink("../real/.sdlc", sym / ".sdlc")
    cid = vd.command_id(f"{vd.python_command()} -m pytest -q")
    for args in (["confirm", ".sdlc", "1", cid], ["set", ".sdlc", "make check"], ["decline", ".sdlc"]):
        r = _run(DETECT, *args, cwd=sym)
        assert r.returncode == 2 and "symlink" in r.stderr, (args, r.stdout, r.stderr)
    assert cfg.read_text() == before


@pytest.mark.skipif(os.name == "nt", reason="pastes the printed gesture through a POSIX shell")
def test_printed_gestures_carry_the_absolute_quoted_sdlc_path(tmp_path):
    """Printed from a relative target and pasted from ANOTHER directory, the gesture still finds
    the scaffolded .sdlc -- a bare `.sdlc` would name whatever the current directory holds."""
    repo = _git_repo(tmp_path / "my repo's dir")
    (repo / "test_a.py").write_text("")
    r = _run(INIT, "my repo's dir", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    sdlc_abs = os.path.abspath(str(repo / ".sdlc"))
    assert shlex.quote(sdlc_abs) in r.stdout
    assert " .sdlc " not in "\n".join(l for l in r.stdout.splitlines() if "verify_detect.py" in l)
    line = [l.strip() for l in r.stdout.splitlines()
            if "verify_detect.py" in l and " confirm " in l][0]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    pasted = shlex.quote(sys.executable) + line[line.index(" "):]
    p = subprocess.run(["sh", "-c", pasted], cwd=elsewhere, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert _stored(repo)["command"].endswith("-m pytest -q")


def test_makefile_test_recipe_is_shown_bounded_and_escaped(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\techo b\ntest: build\n\tpytest -q\n\techo \x1b[2K"
                                       + "x" * 400 + "\n\nother:\n\techo o\n")
    c = vd.detect(tmp_path)
    assert [x["command"] for x in c] == ["make test"]
    printed = "\n".join(vd.proposal_lines(c))
    assert "pytest -q" in printed and "echo o" not in printed and "\x1b" not in printed
    assert len(c[0]["source"]) < 300


def test_make_variable_named_test_is_not_a_target(tmp_path):
    (tmp_path / "Makefile").write_text("test := foo\nbuild:\n\techo b\n")
    assert _cmds(tmp_path) == []

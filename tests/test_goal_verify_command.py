"""#2085: a goal's own `verify_command` must not silently void the environment verify approval (#2066).

Every test here is RED before the change and green after: this file is a planned test file of #2085, and the
test-first gate needs EVERY planned node to fail by an assertion first. So no test reaches a new name by an
attribute access or a keyword the old code rejects (that is an `AttributeError`/`TypeError`, which the gate does
not count as red): new surface is reached through the CLI in a subprocess, through `_api` (an assertion), or
through `_try`, and the result is asserted. Every refusal test also asserts the positive leg that needs the new
code, because argparse's "invalid choice" for the missing verb exits 2, the same code as the verb's own REFUSED.

The boundaries that hold before and after the change (unset, git-local, every accept case, spawn counts) are in
`tests/test_goal_verify_command_guards.py`, which the gate does not hash.

AC map: AC-1 `record` refuses an uncovered command, AC-2 `set-verify-command`, AC-3 the verify/red text,
AC-4 each of them with the approval set, unset and the git-local grant.
"""
import hashlib
import importlib.util
import inspect
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

import pytest

from changelog_layout import entries_naming

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"
ACCEPTANCE = SCRIPTS / "acceptance.py"

ENV = "SIGMA_TRUSTED_VERIFY_SHA256"
ORIGIN = "https://github.com/Owner/Repo.git"
IDENTITY = "owner/repo"
REPO = "make check"                  # the repository's verify.command
GOAL = "echo goal-focused"           # a goal's own command
SECTION = "## Cloud environment setup"
CRITERIA = "## Done when\n- [ ] First works.\n- [ ] Second refuses bad input.\n- [ ] Third recovers.\n"
_GIT_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t" + "@" + "t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t" + "@" + "t"}


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _try(call, *args, **kwargs):
    """-> (value, None) or (None, "ExcType: message"): new surface is asserted on, never raised through."""
    try:
        return call(*args, **kwargs), None
    except Exception as exc:                      # noqa: BLE001 - the caller asserts on the pair
        return None, "%s: %s" % (type(exc).__name__, exc)


def _api(module, name):
    """A name the change adds, as an assertion: a missing one is an `AssertionError`, which the gate counts as red."""
    found = getattr(module, name, None)
    assert found is not None, "%s.%s does not exist" % (getattr(module, "__name__", module), name)
    return found


@pytest.fixture(autouse=True)
def _no_inherited_approval(monkeypatch):
    for name in [k for k in os.environ if k.startswith("GIT_")] + [ENV]:
        monkeypatch.delenv(name, raising=False)


class Source:
    """The goal source `acceptance.record` reads; `posts` counts the draft comments that would reach the issue."""

    def __init__(self, body=CRITERIA):
        self.body, self.comments, self.posts = body, [], 0

    def fetch_title_body(self, goal):
        return {"title": "Goal", "body": self.body}

    def fetch_comments_strict(self, goal):
        return {"comments": self.comments}

    def note(self, goal, text):
        self.posts += 1
        self.comments.append({"body": text})


def _project(root, command=REPO, origin=ORIGIN, extra=None):
    root = pathlib.Path(root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin], check=True)
    sdlc = root / ".sdlc"
    (sdlc / "goals").mkdir(parents=True)
    (sdlc / "state").mkdir()
    (sdlc / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (sdlc / "state" / "review-queue.md").write_text("# Q\n")
    config = {"budget": {"max_iterations": 10}}
    if command is not None:
        config["verify"] = {"command": command}
    config.update(extra or {})
    (sdlc / "config.json").write_text(json.dumps(config))
    return sdlc


def _config(sdlc):
    return json.loads((sdlc / "config.json").read_text())


def _grant_git_local(root):
    subprocess.run(["git", "-C", str(root), "config", "--local", "sigma.allowRepositoryShellCommands", "true"],
                   check=True)


def _approve(value_for=REPO):
    return {ENV: _mod("shell_policy").approval_value(IDENTITY, value_for)}


def _record(sdlc, goal="42", command=None):
    """The record exists with the variable unset (the autouse fixture), so it is accepted whatever it holds."""
    _mod("acceptance").record(sdlc, goal, config=_config(sdlc), source=Source(), verify_command=command)
    return sdlc / "acceptance" / (pathlib.Path(goal).stem + ".md")


def _local_goal(sdlc, command=GOAL, stem="42"):
    goal = sdlc / "goals" / (stem + ".md")
    line = "verify_command: %s\n" % command if command is not None else ""
    goal.write_text("---\nid: %s\nstatus: pending\n%s---\n" % (stem, line))
    return goal


def _touch_command(path):
    code = "from pathlib import Path; Path(%r).write_text('ran')" % str(path)
    return "%s -c %s" % (shlex.quote(sys.executable), shlex.quote(code))


def _cli(project, *args, env=None):
    """acceptance.py in a subprocess from `project`, exactly as the docs print it (relative `.sdlc`)."""
    base = {k: v for k, v in os.environ.items() if k != ENV and not k.startswith("GIT_")}
    return subprocess.run([sys.executable, str(ACCEPTANCE), *args], cwd=str(project), env=dict(base, **(env or {})),
                          capture_output=True, text=True, timeout=120)


def _refused(done, *reasons):
    """The verb's own refusal: exit 2, its `acceptance.py: REFUSED:` prefix (argparse's invalid-choice has none)."""
    assert done.returncode == 2, (done.returncode, done.stderr)
    assert done.stderr.startswith("acceptance.py: REFUSED:"), done.stderr
    for reason in reasons:
        assert reason in done.stderr, (reason, done.stderr)


def _plant_evidence(sdlc, exit_code, stem="42"):
    path = sdlc / "state" / "verify" / (stem + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"exit": exit_code}))
    return path


def _verify_line(err, tool="loop.py verify"):
    lines = [line for line in err.splitlines() if line.startswith(tool + ": REFUSED")]
    assert len(lines) == 1, err
    return lines[0]


def _gesture(sdlc, goal):
    """The clear gesture exactly as `loop.py` prints it: this interpreter, `-I`, the sibling script."""
    return shlex.join([sys.executable, "-I", str(SCRIPTS / "acceptance.py"), "set-verify-command", str(sdlc),
                       str(goal), "--clear"])


# --- T1: one list builder ------------------------------------------------------------------------------------

def test_verify_command_builders_order_dedupe_and_skip_blank(tmp_path):
    state = _mod("state")
    combined = _api(state, "combined_verify_commands")
    repository = _api(state, "repository_verify_commands")
    goal_file = _api(state, "goal_file_verify_command")
    cfg = {"verify": {"command": "A"}}
    assert combined(cfg, "B") == ["A", "B"]
    assert combined(cfg, "A") == ["A"]
    assert combined({}, "B") == ["B"]
    assert combined(cfg, None) == ["A"] and combined(cfg, "") == ["A"]
    assert repository({}) == [] and repository(cfg) == ["A"]
    goal = tmp_path / "g.md"
    for declared in ("", "''", '""', "'"):                       # #228: a blank declaration declares nothing
        goal.write_text("---\nid: g\nverify_command: %s\n---\n" % declared)
        assert goal_file(goal) is None, declared
    goal.write_text("---\nid: g\nverify_command: echo hi\n---\n")
    assert goal_file(goal) == "echo hi"
    assert goal_file("42") is None and goal_file(tmp_path / "absent.md") is None
    # the one builder is what verify uses: a recorded goal, and a local goal with no record
    project = tmp_path / "project"
    sdlc = _project(project)
    _record(sdlc, "42", GOAL)
    assert state.declared_verify_commands("42", _config(sdlc), sdlc) == combined(_config(sdlc), GOAL) == [REPO, GOAL]
    local = _local_goal(sdlc, "echo from-file", "7")
    _record(sdlc, str(local))
    assert state.declared_verify_commands(str(local), _config(sdlc), sdlc) == combined(_config(sdlc), "echo from-file")


def test_the_list_expression_lives_in_combined_verify_commands():
    state = _mod("state")
    combined = _api(state, "combined_verify_commands")
    needle = "dict.fromkeys(c for c in ("
    owners = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "skills").rglob("*.py")
                    if needle in p.read_text(encoding="utf-8"))
    assert needle in inspect.getsource(combined)
    assert owners == ["skills/sigma-loop/scripts/state.py"], owners
    assert needle not in inspect.getsource(state.declared_verify_commands)


# --- T2: the goal-command reason and the remedy-led text (AC-3, policy half) ---------------------------------

def test_verify_decision_names_goal_command_when_only_the_repository_command_is_approved(tmp_path):
    _project(tmp_path / "project")
    policy = _mod("shell_policy")
    decision, err = _try(policy.verify_decision, tmp_path / "project", [REPO, GOAL], _approve(),
                         repository_commands=[REPO])
    assert err is None, err
    assert tuple(decision) == (None, "goal-command", IDENTITY)


def test_goal_command_message_puts_the_goal_remedy_before_git_local_trust():
    policy = _mod("shell_policy")
    decision = policy.Decision(None, "goal-command", IDENTITY)
    text, err = _try(policy.verify_refusal_message, decision, "RUN-THIS-FIRST")
    assert err is None, err
    assert 'does not approve every command about to run in "owner/repo"' in text
    assert 0 <= text.find("RUN-THIS-FIRST") < text.find("git -C"), text
    assert "never an unattended run" in text and "approve the new list" not in text
    assert policy.approval_value(IDENTITY, REPO) not in text
    bare, err = _try(policy.verify_refusal_message, decision)
    assert err is None, err
    assert "remove the goal's own verify_command" in bare, bare
    assert 0 <= bare.find("remove the goal's own verify_command") < bare.find("git -C"), bare


def test_one_text_builder_serves_the_verify_refusal_and_the_record_time_refusal(tmp_path):
    project = tmp_path / "project"
    _project(project)
    policy = _mod("shell_policy")
    body, err = _try(_api(policy, "goal_command_text"), IDENTITY, "SHARED-REMEDY")
    assert err is None and "SHARED-REMEDY" in body, (err, body)
    verify = policy.verify_refusal_message(policy.Decision(None, "goal-command", IDENTITY), "SHARED-REMEDY")
    assert body in verify
    conflict, err = _try(_api(policy, "goal_command_conflict"), project, [REPO, GOAL], [REPO], _approve(),
                         remedy="SHARED-REMEDY")
    assert err is None and conflict is not None, (err, conflict)
    assert body in conflict


# --- T3: the verify and red callers name the remedy (AC-3, caller half) ----------------------------------------

def test_verify_names_clearing_the_goal_command_first_and_runs_nothing(tmp_path, monkeypatch, capsys):
    ran, extra = tmp_path / "ran.txt", tmp_path / "extra.txt"
    sdlc = _project(tmp_path / "project", "echo x >> %s" % ran)
    _record(sdlc, "42", "echo y >> %s" % extra)
    monkeypatch.setenv(ENV, _mod("shell_policy").approval_value(IDENTITY, "echo x >> %s" % ran))
    loop = _mod("loop")
    capsys.readouterr()
    assert loop.verify_goal(sdlc, "42") == 2
    line = _verify_line(capsys.readouterr().err)
    assert 'does not approve every command about to run in "owner/repo"' in line
    assert 0 <= line.find(_gesture(sdlc, "42")) < line.find("git -C"), line
    assert os.environ[ENV] not in line
    assert not ran.exists() and not extra.exists()
    assert not loop.state.evidence_path(sdlc, "42").exists()


def test_a_frontmatter_command_gets_the_file_edit_as_its_remedy(tmp_path, monkeypatch, capsys):
    ran = tmp_path / "ran.txt"
    sdlc = _project(tmp_path / "project", "echo x >> %s" % ran)
    goal = _local_goal(sdlc, "echo y >> %s" % (tmp_path / "other.txt"))
    _record(sdlc, str(goal))                                    # the record inherits the frontmatter command
    monkeypatch.setenv(ENV, _mod("shell_policy").approval_value(IDENTITY, "echo x >> %s" % ran))
    loop = _mod("loop")
    capsys.readouterr()
    assert loop.verify_goal(sdlc, goal) == 2
    line = _verify_line(capsys.readouterr().err)
    # the clear alone cannot work while the file still declares a command (D-5), so the file edit leads
    edit, clear, route = line.find("`verify_command:`"), line.find("set-verify-command"), line.find("git -C")
    assert 0 <= line.find(str(goal)) <= edit < clear < route, line
    assert not ran.exists()


# --- T4: the record-time refusal (AC-1) ------------------------------------------------------------------------

def test_record_refuses_a_goal_command_the_approval_does_not_cover(tmp_path, monkeypatch):
    sdlc = _project(tmp_path / "project")
    monkeypatch.setenv(ENV, _mod("shell_policy").approval_value(IDENTITY, REPO))
    source = Source("No criteria here")                         # drafted: a comment WOULD be posted
    draft = tmp_path / "draft.md"
    draft.write_text(CRITERIA)
    _, err = _try(_mod("acceptance").record, sdlc, "42", config=_config(sdlc), source=source, draft=draft,
                  verify_command=GOAL)
    assert err is not None and err.startswith("ValueError"), err
    leftovers = sorted((sdlc / "acceptance").glob("*")) if (sdlc / "acceptance").exists() else []
    assert leftovers == [], leftovers                           # no record and no `.acceptance-*` temp file
    assert source.posts == 0 and source.comments == []


def test_record_refusal_names_the_reason_and_the_three_fixes(tmp_path, monkeypatch):
    sdlc = _project(tmp_path / "project")
    value = _mod("shell_policy").approval_value(IDENTITY, REPO)
    monkeypatch.setenv(ENV, value)
    _, err = _try(_mod("acceptance").record, sdlc, "42", config=_config(sdlc), source=Source(), verify_command=GOAL)
    assert err is not None, "record accepted a command the approval does not cover"
    assert "approves only this repository's verify.command" in err
    assert "one approval value can never cover two commands" in err
    assert "drop `--verify-command`" in err                                 # fix 1: from the flag
    assert "folds the check into verify.command and re-approves" in err      # fix 2: the operator route
    assert "sigma.allowRepositoryShellCommands" in err and "never an unattended run" in err   # fix 3
    assert "longer, not weaker" in err
    assert "approve the new list" not in err and value not in err


def test_record_checks_a_frontmatter_command_and_names_the_file_edit(tmp_path, monkeypatch):
    sdlc = _project(tmp_path / "project")
    goal = _local_goal(sdlc, GOAL)
    monkeypatch.setenv(ENV, _mod("shell_policy").approval_value(IDENTITY, REPO))
    _, err = _try(_mod("acceptance").record, sdlc, str(goal), config=_config(sdlc), source=Source())
    assert err is not None and err.startswith("ValueError"), err
    assert "verify_command:" in err and "goal file" in err, err           # the flag was never passed
    assert "drop `--verify-command`" not in err
    leftovers = sorted((sdlc / "acceptance").glob("*")) if (sdlc / "acceptance").exists() else []
    assert leftovers == [], leftovers                  # a local goal never posts, so "nothing written" is the proof


@pytest.mark.parametrize("case, expected", [
    ("no-goal-command", False), ("goal-equals-repo", False), ("goal-differs", True), ("frontmatter-only", True)])
def test_record_time_and_verify_time_agree(tmp_path, monkeypatch, case, expected):
    sdlc = _project(tmp_path / "project")
    acceptance, policy, state = _mod("acceptance"), _mod("shell_policy"), _mod("state")
    if case == "frontmatter-only":
        goal = str(_local_goal(sdlc, GOAL))
        _record(sdlc, goal)
    else:
        goal = "42"
        _record(sdlc, goal, {"no-goal-command": None, "goal-equals-repo": REPO, "goal-differs": GOAL}[case])
    approval = _approve()
    monkeypatch.setenv(ENV, approval[ENV])                      # recorded with the variable unset, now set
    config = _config(sdlc)
    recorded = acceptance.read(sdlc, goal)["verify_command"]
    commands = state.declared_verify_commands(goal, config, sdlc)
    conflict, err_a = _try(_api(acceptance, "trust_conflict"), sdlc, config, recorded)
    decision, err_b = _try(policy.verify_decision, tmp_path / "project", commands, approval,
                           repository_commands=state.repository_verify_commands(config))
    assert err_a is None and err_b is None, (err_a, err_b)
    assert (conflict is not None) is expected
    assert (decision.reason == "goal-command") is expected


# --- T5: set-verify-command (AC-2) -----------------------------------------------------------------------------

def _stem_lines(text):
    return [line for line in text.split("\n") if not line.startswith("verify_command: ")]


def test_set_verify_command_clears_only_the_header_line(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    before = record.read_text()
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0, done.stderr
    after = record.read_text()
    assert "verify_command: null" in after.split("\n---\n", 1)[0].splitlines()
    assert _stem_lines(after) == _stem_lines(before)
    assert after.split("\n---\n", 1)[1] == before.split("\n---\n", 1)[1]
    parsed = _mod("acceptance").parse(after, "42")
    assert parsed["verify_command"] is None and parsed["criteria"] == _mod("acceptance").read(sdlc, "42")["criteria"]
    assert _mod("state").declared_verify_commands("42", _config(sdlc), sdlc) == [REPO]
    assert not list(record.parent.glob(".acceptance-*")) and not list(record.parent.glob(".*.tmp"))
    assert done.stderr == "", done.stderr


def test_set_verify_command_replaces_with_a_covered_command(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", REPO)
    assert done.returncode == 0, done.stderr
    assert 'verify_command: "%s"' % REPO in record.read_text().splitlines()
    assert "replaced" in done.stdout
    assert _mod("state").declared_verify_commands("42", _config(sdlc), sdlc) == [REPO]       # deduped to one command


def _call_args(call):
    return {"clear": ("--clear",), "replace": ("--verify-command", REPO), "noop": ("--verify-command", GOAL)}[call]


@pytest.mark.parametrize("call", ["clear", "replace", "noop"])
@pytest.mark.parametrize("evidence", ["passed-evidence", "failed-evidence", "real-verify-run"])
def test_set_verify_command_refuses_once_verify_evidence_exists(tmp_path, evidence, call):
    project = tmp_path / "project"
    sdlc = _project(project, _touch_command(tmp_path / "repo-ran") if evidence == "real-verify-run" else REPO)
    record = _record(sdlc, "42", _touch_command(tmp_path / "goal-ran") if evidence == "real-verify-run" else GOAL)
    if evidence == "real-verify-run":
        _grant_git_local(project)
        assert _mod("loop").verify_goal(sdlc, "42") == 0
        planted = _mod("state").evidence_path(sdlc, "42")
        assert planted.exists()
    else:
        planted = _plant_evidence(sdlc, 0 if evidence == "passed-evidence" else 1)
    args = ("set-verify-command", ".sdlc", "42") + (
        ("--verify-command", _touch_command(tmp_path / "goal-ran")) if call == "noop" and evidence == "real-verify-run"
        else _call_args(call))
    before = record.read_bytes()
    _refused(_cli(project, *args), "verify evidence", "state/verify/42.json")
    assert record.read_bytes() == before
    planted.unlink()                                              # the operator's lever, then the same call works
    done = _cli(project, *args)
    assert done.returncode == 0, done.stderr
    assert (record.read_bytes() == before) is (call == "noop")


def test_the_printed_gesture_clears_a_goal_command_after_a_trust_refusal(tmp_path, monkeypatch, capsys):
    ran, extra = tmp_path / "ran.txt", tmp_path / "extra.txt"
    repo_command = "echo x >> %s" % ran
    project = tmp_path / "project"
    sdlc = _project(project, repo_command)
    _record(sdlc, "42", "echo y >> %s" % extra)
    monkeypatch.setenv(ENV, _mod("shell_policy").approval_value(IDENTITY, repo_command))
    loop = _mod("loop")
    capsys.readouterr()
    assert loop.verify_goal(sdlc, "42") == 2                      # the trust refusal: no evidence written
    assert not loop.state.evidence_path(sdlc, "42").exists()
    shown = re.search(r"`([^`]*set-verify-command[^`]*)`", _verify_line(capsys.readouterr().err))
    assert shown is not None, "the refusal printed no gesture"
    for module in ("json", "hashlib"):                            # a repository that plants modules for the gesture
        (project / (module + ".py")).write_text(
            "open(%r, 'w').write('planted %s ran')\n" % (str(tmp_path / ("ran-" + module)), module))
    env = dict(os.environ, PYTHONPATH=":")                        # an empty entry: the current directory is importable
    done = subprocess.run(shlex.split(shown.group(1)), cwd=str(project), env=env, capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0, done.stderr                      # exactly as printed: no extra flag
    assert not list(tmp_path.glob("ran-*")), "the printed gesture imported a module planted in the repository"
    for module in ("json", "hashlib"):
        (project / (module + ".py")).unlink()
    assert loop.verify_goal(sdlc, "42") == 0
    assert ran.read_text() == "x\n" and not extra.exists()
    assert json.loads(loop.state.evidence_path(sdlc, "42").read_text())["trust"] == "environment"


def test_set_verify_command_is_idempotent(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    first = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    after_first = record.read_bytes()
    second = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert first.returncode == 0 and second.returncode == 0, (first.stderr, second.stderr)
    assert "cleared" in first.stdout and "unchanged" in second.stdout
    assert record.read_bytes() == after_first
    first = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", REPO)
    after_replace = record.read_bytes()
    second = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", REPO)
    assert first.returncode == 0 and second.returncode == 0, (first.stderr, second.stderr)
    assert "replaced" in first.stdout and "unchanged" in second.stdout
    assert after_replace != after_first and record.read_bytes() == after_replace


def test_a_record_created_with_a_command_makes_the_same_value_a_byte_identical_no_op(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    unicode_command = "echo caf\u00e9 \"quoted\""             # same json.dumps(ensure_ascii=False) as `record`
    record = _record(sdlc, "42", unicode_command)
    before = record.read_bytes()
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", unicode_command)
    assert done.returncode == 0, done.stderr
    assert "unchanged" in done.stdout and record.read_bytes() == before


def test_replace_runs_the_record_time_check(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", REPO)
    before = record.read_bytes()
    approved = _approve()
    refused = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", GOAL, env=approved)
    _refused(refused, "approves only this repository's verify.command")
    assert record.read_bytes() == before
    cleared = _cli(project, "set-verify-command", ".sdlc", "42", "--clear", env=approved)
    covered = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", REPO, env=approved)
    assert cleared.returncode == 0 and covered.returncode == 0, (cleared.stderr, covered.stderr)
    assert 'verify_command: "%s"' % REPO in record.read_text().splitlines()


def test_clear_refuses_for_a_local_goal_whose_frontmatter_declares_a_command(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    goal = _local_goal(sdlc, GOAL)
    record = _record(sdlc, str(goal))                               # inherits the command
    before = record.read_bytes()
    _refused(_cli(project, "set-verify-command", ".sdlc", ".sdlc/goals/42.md", "--clear"), "frontmatter")
    assert record.read_bytes() == before
    _local_goal(sdlc, None)                                         # the file edit the refusal asks for
    done = _cli(project, "set-verify-command", ".sdlc", ".sdlc/goals/42.md", "--clear")
    assert done.returncode == 0, done.stderr
    assert "verify_command: null" in record.read_text().splitlines()


def _symlinked_record(project, sdlc):
    record = _record(sdlc, "42", GOAL)
    target = project / "elsewhere.md"
    target.write_bytes(record.read_bytes())
    record.unlink()
    record.symlink_to(target)
    return record, target


@pytest.mark.parametrize("case", ["missing-record", "symlinked-record", "both-flags", "neither-flag",
                                  "blank-command", "draft-flag", "clear-on-record"])
def test_set_verify_command_refuses_bad_input_and_a_valid_call_still_works(tmp_path, case):
    project = tmp_path / "project"
    sdlc = _project(project)
    good = _record(sdlc, "43", GOAL)
    plain = ("set-verify-command", ".sdlc", "42")
    if case == "missing-record":
        _refused(_cli(project, *plain, "--clear"), "record")
    elif case == "symlinked-record":
        _, target = _symlinked_record(project, sdlc)
        before = target.read_bytes()
        _refused(_cli(project, *plain, "--clear"), "symlink")
        assert target.read_bytes() == before and (sdlc / "acceptance" / "42.md").is_symlink()
    elif case == "blank-command":
        _record(sdlc, "42", GOAL)
        _refused(_cli(project, *plain, "--verify-command", "  "), "nonempty")
    else:
        _record(sdlc, "42", GOAL)
        extra = {"both-flags": ("--clear", "--verify-command", REPO), "neither-flag": (),
                 "draft-flag": ("--clear", "--draft", "x.md")}.get(case)
        done = _cli(project, *plain, *extra) if extra is not None else _cli(project, "record", ".sdlc", "42", "--clear")
        assert done.returncode == 2 and not done.stderr.startswith("acceptance.py: REFUSED:"), done.stderr
        assert re.search(r"\{[^}]*set-verify-command[^}]*\}", done.stderr), done.stderr   # usage names the verb
    ok = _cli(project, "set-verify-command", ".sdlc", "43", "--clear")
    assert ok.returncode == 0, ok.stderr
    assert "verify_command: null" in good.read_text().splitlines()


def test_set_verify_command_refuses_a_nul_byte_and_leaves_the_record_alone(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    before = record.read_bytes()
    _, err = _try(_api(_mod("acceptance"), "set_verify_command"), sdlc, "42", verify_command="a\0b")
    assert err is not None and err.startswith("ValueError") and "nonempty" in err, err
    assert record.read_bytes() == before


@pytest.mark.parametrize("command, reason", [
    pytest.param("x" * 33000, "32 KiB", id="over-32-KiB"),
    pytest.param("echo a\u2028b", "malformed", id="line-separator")])
def test_set_verify_command_revalidates_the_rewritten_text(tmp_path, command, reason):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    before = record.read_bytes()
    _refused(_cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", command), reason)
    assert record.read_bytes() == before
    assert _cli(project, "set-verify-command", ".sdlc", "42", "--clear").returncode == 0


@pytest.mark.parametrize("state_name", ["unset", "set", "git-local"])
def test_set_verify_command_works_with_the_approval_unset_set_and_git_local(tmp_path, state_name):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    env = _approve() if state_name == "set" else None
    if state_name == "git-local":
        _grant_git_local(project)
    cleared = _cli(project, "set-verify-command", ".sdlc", "42", "--clear", env=env)
    assert cleared.returncode == 0, cleared.stderr
    covered = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", REPO, env=env)
    assert covered.returncode == 0, covered.stderr
    other = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", GOAL, env=env)
    if state_name == "set":
        _refused(other, "approves only this repository's verify.command")
        assert 'verify_command: "%s"' % REPO in record.read_text().splitlines()
    else:
        assert other.returncode == 0, other.stderr
        assert 'verify_command: "%s"' % GOAL in record.read_text().splitlines()


# --- F6.2: a tracked root record ----------------------------------------------------------------------------

def test_a_tracked_root_record_prints_the_command_that_clears_it_and_who_runs_it(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    untracked = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", REPO)
    assert untracked.returncode == 0 and untracked.stderr == "", untracked.stderr
    subprocess.run(["git", "-C", str(project), "add", "-f", "--", ".sdlc/acceptance/42.md"], check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-q", "-m", "c"], check=True, env=dict(os.environ, **_GIT_ID))
    tracked = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert tracked.returncode == 0, tracked.stderr
    assert "git -C %s checkout -- .sdlc/acceptance/42.md" % project.resolve() in tracked.stderr, tracked.stderr
    assert "work.py start" in tracked.stderr and "operator" in tracked.stderr
    assert "verify_command: null" in record.read_text().splitlines()


# --- T6: the goal worktree's copy of the record --------------------------------------------------------------

def _worktree(tmp_path, sdlc, stem="42"):
    wt = tmp_path / "wt"
    (sdlc / "state" / "work").mkdir(parents=True, exist_ok=True)
    (sdlc / "state" / "work" / (stem + ".json")).write_text(json.dumps(
        {"worktree": str(wt), "branch": "sdlc/42", "remote": "origin", "base": "main"}))
    (wt / ".sdlc" / "acceptance").mkdir(parents=True)
    return wt / ".sdlc" / "acceptance" / (stem + ".md")


def test_set_verify_command_keeps_the_worktree_copy_byte_identical(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    copy = _worktree(tmp_path, sdlc)
    copy.write_bytes(record.read_bytes())
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0, done.stderr
    assert "verify_command: null" in record.read_text().splitlines()
    assert copy.read_bytes() == record.read_bytes(), "the goal branch would land a stale record"
    assert done.stderr == "", done.stderr


def test_set_verify_command_leaves_a_differing_worktree_copy_alone_and_says_so(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    copy = _worktree(tmp_path, sdlc)
    copy.write_bytes(record.read_bytes() + b"<!-- someone's edit -->\n")
    edited = copy.read_bytes()
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0, done.stderr
    assert "verify_command: null" in record.read_text().splitlines()
    assert copy.read_bytes() == edited
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, done.stderr
    assert "cp %s %s" % (record.resolve(), copy.resolve()) in lines[0], lines[0]


def test_an_absent_or_symlinked_worktree_copy_is_skipped(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    copy = _worktree(tmp_path, sdlc)                                 # the work record names it; no file there
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0 and done.stderr == "", done.stderr   # an absent copy is skipped silently
    assert not copy.exists()
    target = tmp_path / "linked-target.md"
    target.write_bytes(record.read_bytes())
    copy.symlink_to(target)                                          # a symlinked copy is never written through
    untouched = target.read_bytes()
    replaced = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", REPO)
    assert replaced.returncode == 0, replaced.stderr
    assert target.read_bytes() == untouched and copy.is_symlink()
    assert len([line for line in replaced.stderr.splitlines() if line.strip()]) == 1, replaced.stderr


# --- F4: the trace row ---------------------------------------------------------------------------------------

def _log_rows(sdlc, stem="42"):
    path = sdlc / "state" / "log" / (stem + ".jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_set_verify_command_logs_hashes_never_the_command(tmp_path):
    project = tmp_path / "project"
    marker = "DISTINCTIVE-MARKER-4f2a9c"
    sdlc = _project(project, extra={"action_log": {"enabled": True}})
    command = "echo %s" % marker
    record = _record(sdlc, "42", command)
    old = hashlib.sha256(command.encode()).hexdigest()[:12]
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0, done.stderr
    rows = _log_rows(sdlc)
    assert len(rows) == 1, rows
    assert (rows[0]["kind"], rows[0]["actor"]) == ("note", "loop")
    assert rows[0]["text"] == "verify_command cleared old=%s new=none" % old, rows[0]
    new = hashlib.sha256(REPO.encode()).hexdigest()[:12]
    assert _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", REPO).returncode == 0
    assert _log_rows(sdlc)[-1]["text"] == "verify_command replaced old=none new=%s" % new
    log_text = (sdlc / "state" / "log" / "42.jsonl").read_text()
    assert marker not in log_text and marker not in done.stdout + done.stderr and marker not in record.read_text()


def test_a_refusal_or_a_no_op_writes_no_row_and_a_change_writes_exactly_one(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project, extra={"action_log": {"enabled": True}})
    _record(sdlc, "42", GOAL)
    evidence = _plant_evidence(sdlc, 0)
    _refused(_cli(project, "set-verify-command", ".sdlc", "42", "--clear"), "verify evidence")
    assert _log_rows(sdlc) == []
    evidence.unlink()
    noop = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", GOAL)
    assert noop.returncode == 0 and "unchanged" in noop.stdout and _log_rows(sdlc) == [], noop.stderr
    assert _cli(project, "set-verify-command", ".sdlc", "42", "--clear").returncode == 0
    assert len(_log_rows(sdlc)) == 1


def test_no_row_is_written_when_the_action_log_is_off(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    _record(sdlc, "42", GOAL)
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0 and "cleared" in done.stdout, done.stderr
    assert not (sdlc / "state" / "log").exists()


def test_a_relative_sdlc_argument_creates_no_nested_sdlc_tree(tmp_path):
    """F11 (pre-existing, not fixed here): `actionlog.append` given the relative `.sdlc` every doc prints creates an
    empty `.sdlc/.sdlc/state/log`. The verb resolves the path before it appends."""
    project = tmp_path / "project"
    sdlc = _project(project, extra={"action_log": {"enabled": True}})
    _record(sdlc, "42", GOAL)
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0, done.stderr
    assert len(_log_rows(sdlc)) == 1
    assert not (sdlc / ".sdlc").exists(), "a junk .sdlc/.sdlc tree was created"


def test_a_log_failure_never_changes_the_exit_code(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project, extra={"action_log": {"enabled": True}})
    record = _record(sdlc, "42", GOAL)
    (sdlc / "state" / "log").write_text("not a directory")           # every append fails
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0, done.stderr
    assert "verify_command: null" in record.read_text().splitlines()


# --- T8: the documented gesture, run as written ---------------------------------------------------------------

def _documented_clear_gesture():
    doc = (ROOT / "docs" / "cloud-sessions.md").read_text(encoding="utf-8")
    assert SECTION in doc, "docs/cloud-sessions.md has no %r section" % SECTION
    body = doc.split(SECTION, 1)[1].split("\n## ", 1)[0]
    lines = [line.strip() for block in re.findall(r"```[a-z]*\n(.*?)```", body, re.S) for line in block.splitlines()
             if "set-verify-command" in line and "--clear" in line]
    assert len(lines) == 1, lines
    return lines[0]


def test_the_documented_clear_gesture_runs_as_written(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    for module in ("json", "hashlib"):
        (project / (module + ".py")).write_text(
            "open(%r, 'w').write('planted %s ran')\n" % (str(tmp_path / ("ran-" + module)), module))
    argv = shlex.split(_documented_clear_gesture().replace("<installed-sigma>", str(ROOT)).replace("<goal>", "42"))
    argv[0] = sys.executable if argv[0] == "python3" else argv[0]
    env = dict(os.environ, PYTHONPATH=":", HOME=str(tmp_path / "home"))
    env.pop(ENV, None)
    done = subprocess.run(argv, cwd=str(project), env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "verify_command: null" in record.read_text().splitlines()
    assert not list(tmp_path.glob("ran-*")), "the gesture imported a module planted in the repository"


def test_the_docs_state_the_new_reason_the_levers_and_the_limits():
    cloud = (ROOT / "docs" / "cloud-sessions.md").read_text(encoding="utf-8").split(SECTION, 1)[1].split("\n## ", 1)[0]
    assert "`goal-command`" in cloud and "set-verify-command" in cloud
    assert "those goals still need the git-local grant" not in cloud
    assert "state/verify" in cloud and "never reports" in cloud                  # the evidence lever; doctor limit
    assert "work.py start" in cloud and "checkout --" in cloud                    # the tracked-root clear command
    contract = (ROOT / "contract" / "README.md").read_text(encoding="utf-8")
    assert "set-verify-command" in contract and "format and version unchanged" in contract
    # the entry is found by its issue number, not by its heading, so a release commit that moves it cannot break this
    entries = entries_naming((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), "(#2085)")
    assert len(entries) == 1, entries
    assert "set-verify-command" in entries[0] and "unmeasured" in entries[0].lower()
    assert "sha256" in entries[0]                                                   # the ledger event keeps the old one

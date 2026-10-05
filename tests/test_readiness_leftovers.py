"""#342 — uninstall residue checker controls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


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

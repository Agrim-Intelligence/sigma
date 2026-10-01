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


def test_git_pid_installs_and_info_exclude_are_all_reported(tmp_path, monkeypatch):
    mod = _mod()
    (tmp_path / ".git" / "info").mkdir(parents=True)
    (tmp_path / ".git" / "info" / "exclude").write_text(mod.IGNORE_MARKER)
    state = tmp_path / ".sdlc" / "state"; state.mkdir(parents=True)
    (state / "watch.pid").write_text(str(os.getpid()))
    nested = tmp_path / "profiles" / "plugins" / "sigma"; nested.mkdir(parents=True)
    monkeypatch.setattr(mod, "_git", lambda _repo, *args: {
        ("worktree", "list", "--porcelain"): f"worktree {tmp_path / '.sdlc' / 'ledger'}\\n",
        ("branch", "--format=%(refname:short)"): "sdlc/1\\nfeature/demo\\nsdlc-ledger\\n",
        ("branch", "-r", "--format=%(refname:short)"): "origin/sdlc/1\\norigin/feature/demo\\norigin/sdlc-ledger\\n",
    }.get(args, ""))
    rows = mod.find_leftovers(tmp_path, environ={"CLAUDE_CONFIG_DIR": str(tmp_path / "profiles"),
                                                 "CODEX_HOME": str(tmp_path / "empty")})
    kinds = {row["kind"] for row in rows}
    assert {".sdlc", "ignore-block", "running-pid", "worktree", "local-branch", "remote-branch", "installed-plugin"} <= kinds


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
    setup = ROOT / "skills" / "agrim-setup" / "scripts" / "setup.py"
    init = ROOT / "skills" / "agrim-init" / "scripts" / "sdlc_init.py"
    assert _mod().IGNORE_MARKER in setup.read_text(encoding="utf-8")
    text = init.read_text(encoding="utf-8")
    assert all(marker in text for marker in _mod().AGENTS_MARKERS)
    assert all(name in text for name in _mod().CURSOR_RULES)


def test_documented_local_cleanup_is_green_after_onboarding_and_red_without_ignore_removal(tmp_path):
    """Run the onboarding gesture, then the guide's repository cleanup and final CLI gesture."""
    control = ROOT / "tools" / "onboarding_control.py"
    subprocess.run([sys.executable, str(control), "--mode", "local", "--variant", "confirm",
                    "--workdir", str(tmp_path), "--keep"], cwd=ROOT, check=True, capture_output=True, text=True)
    repo = next(tmp_path.rglob("repo"))
    assert (repo / ".sdlc").exists()
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
    green = subprocess.run([sys.executable, str(SCRIPT), str(repo)], text=True, capture_output=True)
    assert green.returncode == 0 and green.stdout == ""

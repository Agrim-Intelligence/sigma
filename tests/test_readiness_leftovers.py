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


def test_guide_final_check_runs_from_the_adopted_repository():
    """D9 sandbox run: the guide is followed from the adopted repository, which has no `tools/` directory,
    so a final check spelled `python3 tools/readiness/leftovers.py .` exited 2 (file not found) there. The
    last block must name the checker by a path that is not relative to the repository being cleaned, and the
    prose must say where the file is once step 6 has removed the plugin's copy of it."""
    block = _final_block()
    assert "leftovers.py" in block
    program = re.search(r"python3\s+(\S*leftovers\.py)", block).group(1)
    assert program.startswith(('"', "<", "/", "$", "~")), (
        f"final check names {program!r}, a path relative to the repository being cleaned")
    text = (ROOT / "docs" / "uninstall.md").read_text(encoding="utf-8")
    assert "plugins/cache/sigmaloop" in text.split("\n6. ")[1].split("\n7. ")[0]


def test_guide_removes_the_marketplace_registration_the_install_came_through():
    """D9 sandbox run (real `claude`): `claude plugin uninstall` leaves the marketplace registered in the
    profile's settings.json and plugins/known_marketplaces.json; `claude plugin marketplace remove`
    clears both. The guide must give that command, in the plugin-removal step, before the cache step."""
    text = (ROOT / "docs" / "uninstall.md").read_text(encoding="utf-8")
    step2 = text.split("\n2. ")[1].split("\n3. ")[0]
    assert "claude plugin marketplace remove sigmaloop" in step2


def test_guide_step7_says_what_to_do_with_files_adoption_created():
    """D9 sandbox run: on a repository that had no AGENTS.md and no .cursor, steps 1 to 10 leave a zero-byte
    AGENTS.md and an empty .cursor/rules directory that leftovers.py (which looks for the markers and the
    two rule files, not for empties) passes over with exit 0. The step must tell the reader to remove them."""
    text = (ROOT / "docs" / "uninstall.md").read_text(encoding="utf-8")
    step7 = text.split("\n7. ")[1].split("\n8. ")[0]
    assert "AGENTS.md" in step7 and "empty" in step7 and "created" in step7


def _guide():
    return (ROOT / "docs" / "uninstall.md").read_text(encoding="utf-8")


def test_guide_warns_before_the_marketplace_removal_command():
    """Reviewer finding on the D9 run: `marketplace remove` also uninstalls every other plugin from that
    marketplace and deletes their saved data, and without --scope clears every settings scope. The
    warning must precede the command in the step, and the step names a command that lists the others."""
    step2 = _guide().split("\n2. ")[1].split("\n3. ")[0]
    command = step2.index("claude plugin marketplace remove sigmaloop")
    for phrase in ("every other plugin", "saved options", "user, project and local", "claude plugin list"):
        assert phrase in step2[:command], f"step 2 must say {phrase!r} before the command"
    assert "skip the removal while another plugin you use comes from that" in " ".join(step2.lower().split())


def _final_block():
    """The guide's final check: the block that runs the checker (the last block removes its copy)."""
    found = [b for b in _doc_blocks() if "python3" in b and "leftovers.py" in b]
    assert len(found) == 1, found
    return found[0]


def _copy_block():
    found = [b for b in _doc_blocks() if "sigmaloop-check" in b and "cp " in b]
    assert len(found) == 1, found
    return found[0]


def _removal_block():
    found = [b for b in _doc_blocks() if "rm -rf" in b and "plugins/cache" in b and "cp " not in b]
    assert len(found) == 1, found
    return found[0]


def _cleanup_block():
    found = [b for b in _doc_blocks() if "sigmaloop-check" in b and "rm -rf" in b and "cp " not in b]
    assert len(found) == 1, found
    return found[0]


def test_guide_copies_the_checker_out_before_the_cache_is_removed():
    """Reviewer finding: the copy-out was told after step 10 but step 6 deletes the cache copy. In document
    order the block that creates the final command's program must come before the block that removes the
    cache, the final command must run that copy, and a later block must remove it again."""
    blocks = _doc_blocks()
    created = blocks.index(_copy_block())
    removal = blocks.index(_removal_block())
    assert created < removal, "the copy-out must come before the cache removal"
    assert blocks.index(_final_block()) > removal
    assert blocks.index(_cleanup_block()) > blocks.index(_final_block())
    assert "[ -n " in _copy_block(), "the copy-out needs a guard"
    text = _guide()
    assert "shared" in text and "/tmp" in text, "the guide must say why the checker is not copied to a shared directory"


class _Flow:
    """A scratch repository, a fake profile and a runner for the guide's copy-out, cache-removal, final-check
    and cleanup blocks under a chosen shell. Nothing here touches a real repository or profile."""

    def __init__(self, tmp_path, shell):
        self.exe = shutil.which(shell)
        if self.exe is None:
            pytest.skip(f"{shell} is not installed on this machine")
        self.repo, self.run = _scratch(tmp_path)
        self.tmp = tmp_path / "tmp"
        self.tmp.mkdir()
        self.profile = tmp_path / "profile"
        self.marker = tmp_path / "PLANTED-CODE-RAN"
        self.gitdir = Path(self.run(["git", "rev-parse", "--absolute-git-dir"]).stdout.strip())
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.profile), "CODEX_HOME": str(tmp_path / "codex"),
                    "TMPDIR": str(self.tmp), "HOME": str(tmp_path / "home"),
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def cache(self, version, body=None):
        d = self.profile / "plugins" / "cache" / "sigmaloop" / "sigmaloop" / version / "tools" / "readiness"
        d.mkdir(parents=True, exist_ok=True)
        if body is None:
            shutil.copy(SCRIPT, d / "leftovers.py")
        else:
            (d / "leftovers.py").write_text(body, encoding="utf-8")

    def plant(self, directory):
        """A directory at `directory` holding a script that records that it ran."""
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "leftovers.py").write_text(f"open({str(self.marker)!r}, 'w').write('x')\n", encoding="utf-8")

    def go(self, block):
        return subprocess.run([self.exe, "-c", block], cwd=self.repo, env=self.env, text=True,
                              capture_output=True, timeout=60)

    def guide(self):
        """copy-out, cache removal, final check, in the guide's order -> (copy, final)."""
        copy = self.go(_copy_block())
        assert self.go(_removal_block()).returncode == 0
        return copy, self.go(_final_block())


SHELLS = ["sh", "bash", "zsh"]


@pytest.mark.parametrize("shell", SHELLS)
def test_documented_copy_out_then_cache_removal_then_final_check_is_green(tmp_path, shell):
    """The three blocks in the guide's order, with a fake cache holding the real checker: the final check
    exits 0 after the cache is gone, and the cleanup block removes the copy."""
    flow = _Flow(tmp_path, shell)
    flow.cache("9.9.9")
    copy, final = flow.guide()
    assert copy.returncode == 0 and "no usable copy" not in copy.stdout, copy.stdout + copy.stderr
    assert not (flow.profile / "plugins" / "cache" / "sigmaloop").exists()
    assert final.returncode == 0 and final.stdout == "", final.stdout + final.stderr
    assert flow.go(_cleanup_block()).returncode == 0
    assert not (flow.gitdir / "sigmaloop-check").exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_copy_out_never_runs_planted_code_in_a_shared_or_predictable_place(tmp_path, shell):
    """Reviewer exploit: a predictable directory under the temporary directory, made read-only by someone
    else with a planted script, made `cp` fail and the final command ran the planted file. Plant at the
    old location, at the new one, read-only and as symlinks, and let the cached checker be unreadable
    in one case: the planted code must never run and a failed copy must print the fallback line."""
    flow = _Flow(tmp_path, shell)
    flow.cache("1.0.0")
    for where in (flow.tmp / "sigmaloop-check", flow.gitdir / "sigmaloop-check"):
        flow.plant(where)
        where.chmod(0o555)                                  # read-only planted directory
        (where / "leftovers.py").chmod(0o444)
    copy, final = flow.guide()
    assert not flow.marker.exists(), "planted code ran"
    assert final.returncode == 0 or "no usable copy" in final.stdout + copy.stdout, (copy.stdout, final.stdout)


@pytest.mark.parametrize("shell", SHELLS)
def test_copy_out_does_not_follow_a_symlinked_directory_or_file(tmp_path, shell):
    flow = _Flow(tmp_path, shell)
    flow.cache("1.0.0")
    victim = tmp_path / "victim"
    flow.plant(victim)
    before = (victim / "leftovers.py").read_text()
    for where in (flow.tmp / "sigmaloop-check", flow.gitdir / "sigmaloop-check"):
        where.symlink_to(victim, target_is_directory=True)  # a symlinked directory
    copy, final = flow.guide()
    assert (victim / "leftovers.py").read_text() == before, "cp followed the symlink and overwrote its target"
    assert not flow.marker.exists(), "planted code ran"
    # a symlinked FILE at the target path inside a real directory
    shutil.rmtree(flow.gitdir / "sigmaloop-check")
    (flow.gitdir / "sigmaloop-check").mkdir()
    (flow.gitdir / "sigmaloop-check" / "leftovers.py").symlink_to(victim / "leftovers.py")
    flow.guide()
    assert (victim / "leftovers.py").read_text() == before and not flow.marker.exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_failed_copy_prints_the_fallback_and_leaves_nothing_runnable(tmp_path, shell):
    flow = _Flow(tmp_path, shell)
    flow.cache("1.0.0")
    cached = next((flow.profile / "plugins" / "cache").rglob("leftovers.py"))
    cached.chmod(0o000)                                     # cp cannot read it
    try:
        copy, final = flow.guide()
    finally:
        if cached.exists():
            cached.chmod(0o644)
    assert "no usable copy" in copy.stdout, copy.stdout + copy.stderr
    assert not (flow.gitdir / "sigmaloop-check" / "leftovers.py").exists(), "a stale file is left to run"
    assert final.returncode != 0 and "no verified copy" in final.stdout, final.stdout + final.stderr
    assert not flow.marker.exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_copy_out_picks_the_highest_cached_version_numerically(tmp_path, shell):
    """`ls | tail -n 1` sorts as text: 1.2.0, 1.9.0, 1.10.0 picked 1.9.0."""
    flow = _Flow(tmp_path, shell)
    for version in ("1.2.0", "1.9.0", "1.10.0"):
        flow.cache(version, body=f"print('from {version}')\n")
    assert flow.go(_copy_block()).returncode == 0
    copies = [d / "sigmaloop-check" / "leftovers.py" for d in (flow.gitdir, flow.tmp)]
    assert [c.read_text() for c in copies if c.is_file()] == ["print('from 1.10.0')\n"]


@pytest.mark.parametrize("shell", SHELLS)
def test_copy_out_with_nothing_cached_is_quiet_and_prints_the_fallback(tmp_path, shell):
    """zsh prints 'no matches found' for an unmatched glob and `2>/dev/null` did not hide it."""
    flow = _Flow(tmp_path, shell)
    copy = flow.go(_copy_block())
    assert copy.stderr == "", copy.stderr
    assert "no usable copy" in copy.stdout
    final = flow.go(_final_block())
    assert final.returncode != 0 and "no verified copy" in final.stdout and final.stderr == ""


LOCALES = ["en_US.UTF-8", "C"]
#: (file bytes, kept). The first four are the macOS UTF-8 cases that deleted real content: `tr` printed
#: "Illegal byte sequence" and nothing else, so the "has content" test saw an empty string.
AGENTS_CASES = [
    (b"\xe9cole rules", True), (b"\xff rules rules", True), (b"\xff\xfe\xfd", True), (b"text then \xff", True),
    (b"\xef\xbb\xbf", True), (b"# mine\n", True), (b"x", True), (b"\x00\x01", True),
    (b"", False), (b"\n", False), (b" \r\n\t\n", False),
]


def _agents_block():
    found = [b for b in _doc_blocks() if "AGENTS.md" in b and "rm" in b]
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("content,kept", AGENTS_CASES)
def test_step7_deletes_agents_md_only_when_it_has_no_content(tmp_path, shell, locale, content, kept):
    """Only a file whose read succeeded and showed no non-whitespace byte is deleted. Anything else (real
    text, bytes that are not valid in the locale, a read error) is kept: the check fails closed."""
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} is not installed on this machine")
    (tmp_path / "AGENTS.md").write_bytes(content)
    env = {**os.environ, "LC_ALL": locale, "LANG": locale}
    for _ in range(2):          # second run: the file is gone or kept, the block must still exit 0
        done = subprocess.run([exe, "-c", _agents_block()], cwd=tmp_path, env=env, text=True, capture_output=True)
        assert done.returncode == 0, done.stderr
    assert (tmp_path / "AGENTS.md").exists() is kept, (content, locale)


@pytest.mark.parametrize("shell", SHELLS)
def test_step7_agents_md_link_and_directory_cases(tmp_path, shell):
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} is not installed on this machine")
    go = lambda: subprocess.run([exe, "-c", _agents_block()], cwd=tmp_path, text=True, capture_output=True)
    (tmp_path / "real.md").write_text("\n")
    (tmp_path / "AGENTS.md").symlink_to("real.md")
    assert go().returncode == 0
    assert not (tmp_path / "AGENTS.md").is_symlink() and (tmp_path / "real.md").exists(), "the target must stay"
    (tmp_path / "real.md").write_text("mine\n")
    (tmp_path / "AGENTS.md").symlink_to("real.md")
    assert go().returncode == 0 and (tmp_path / "AGENTS.md").is_symlink(), "a link to real content stays"
    (tmp_path / "AGENTS.md").unlink()
    (tmp_path / "AGENTS.md").mkdir()
    assert go().returncode == 0 and (tmp_path / "AGENTS.md").is_dir(), "a directory is left alone"


def _shims(tmp_path):
    """rm, mkdir, chmod, cp, mv that only record that they were called."""
    bindir, log = tmp_path / "shims", tmp_path / "shim.log"
    bindir.mkdir()
    for name in ("rm", "mkdir", "chmod", "cp", "mv", "rmdir"):
        (bindir / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 0\n')
        (bindir / name).chmod(0o755)
    return bindir, log


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("hostile", [False, True])
@pytest.mark.parametrize("which", ["copy", "final", "cleanup"])
def test_blocks_refuse_to_act_outside_the_adopted_repository(tmp_path, shell, hostile, which):
    """With no repository (or a GIT_DIR that makes git fail) the git directory is empty; the old blocks then
    built `/sigmaloop-check` and ran `mkdir -m 700` / `rm -rf` on it. No destructive command may be called."""
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} is not installed on this machine")
    bindir, log = _shims(tmp_path)
    profile = tmp_path / "profile"
    d = profile / "plugins" / "cache" / "sigmaloop" / "sigmaloop" / "1.0.0" / "tools" / "readiness"
    d.mkdir(parents=True)
    shutil.copy(SCRIPT, d / "leftovers.py")
    elsewhere = tmp_path / "norepo"
    elsewhere.mkdir()
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "CLAUDE_CONFIG_DIR": str(profile),
           "GIT_CEILING_DIRECTORIES": str(tmp_path), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    if hostile:
        env["GIT_DIR"] = str(tmp_path / "does" / "not" / "exist")
    block = {"copy": _copy_block, "final": _final_block, "cleanup": _cleanup_block}[which]()
    done = subprocess.run([exe, "-c", block], cwd=elsewhere, env=env, text=True, capture_output=True, timeout=60)
    calls = log.read_text() if log.exists() else ""
    assert calls == "", f"destructive command ran with no repository: {calls}"
    assert "not inside the adopted repository" in done.stdout, done.stdout + done.stderr
    if which == "final":
        assert done.returncode != 0


@pytest.mark.parametrize("shell", SHELLS)
def test_copy_out_prefers_the_release_over_its_prerelease(tmp_path, shell):
    """`sort -k3,3n` read `0-rc1` as 0 and the line tiebreak put the release candidate last."""
    flow = _Flow(tmp_path, shell)
    for version in ("1.10.0-rc1", "1.10.0", "1.9.0"):
        flow.cache(version, body=f"print('from {version}')\n")
    assert flow.go(_copy_block()).returncode == 0
    assert (flow.gitdir / "sigmaloop-check" / "leftovers.py").read_text() == "print('from 1.10.0')\n"
    # a prerelease alone is still better than nothing
    (tmp_path / "second").mkdir()
    only = _Flow(tmp_path / "second", shell)
    only.cache("2.0.0-rc1", body="print('rc only')\n")
    assert only.go(_copy_block()).returncode == 0
    assert (only.gitdir / "sigmaloop-check" / "leftovers.py").read_text() == "print('rc only')\n"


def test_guide_step7_says_a_non_empty_agents_md_stays():
    step7 = _guide().split("\n7. ")[1].split("\n8. ")[0]
    assert "non-empty" in step7 and "whitespace" in step7


@pytest.mark.parametrize("shell", ["sh", "bash", "zsh"])
def test_documented_remote_blocks_work_in_sh_bash_and_zsh(tmp_path, shell):
    """Reviewer finding: an unquoted `$branches` does not word-split in zsh (the macOS default), so the
    step 9 delete loop deleted nothing and exited 0. Every step 9 and 10 block runs under each shell."""
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} is not installed on this machine")
    repo, run = _scratch(tmp_path)
    bare = tmp_path / "remote.git"
    assert run(["git", "init", "-q", "--bare", str(bare)]).returncode == 0
    assert run(["git", "remote", "add", "origin", str(bare)]).returncode == 0
    led = _mod().LEDGER_BRANCH
    names = ("sdlc/goal-1", "feature/demo", led, "release")
    for branch in names:
        assert run(["git", "branch", branch]).returncode == 0
    assert run(["git", "push", "-q", "origin", *names]).returncode == 0
    assert run(["git", "fetch", "-q", "origin"]).returncode == 0
    listing, push, prune = _remote_blocks()
    heads = lambda: {line.split("refs/heads/")[1] for line in run(["git", "ls-remote", "--heads", "origin"]).stdout.splitlines()}
    shown = run([exe, "-c", listing])
    assert set(shown.stdout.split()) == {"origin/sdlc/goal-1", "origin/feature/demo", "origin/" + led}, shown.stdout
    assert run([exe, "-c", push]).returncode == 0
    assert heads() == {"release"}, heads()
    assert run([exe, "-c", prune]).returncode == 0


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


@pytest.mark.parametrize("shell", SHELLS)
def test_step7_unreadable_agents_md_is_kept(tmp_path, shell):
    """A read error (grep exits 2) is not 'no content': the file stays."""
    exe = shutil.which(shell)
    if exe is None or os.geteuid() == 0:
        pytest.skip("shell missing, or root can read a mode-000 file")
    target = tmp_path / "AGENTS.md"
    target.write_text("")
    target.chmod(0o000)
    done = subprocess.run([exe, "-c", _agents_block()], cwd=tmp_path, text=True, capture_output=True)
    target.chmod(0o644)
    assert done.returncode == 0 and target.exists()

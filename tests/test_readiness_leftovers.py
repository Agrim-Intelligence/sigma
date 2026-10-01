"""#342 — uninstall residue checker controls."""
import importlib.util
from pathlib import Path
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
    import shutil
    shutil.rmtree(tmp_path / ".sdlc")
    assert _mod().find_leftovers(tmp_path) == [{"kind": "ignore-block", "path": ".gitignore"}]
    ignore.unlink()
    assert _mod().find_leftovers(tmp_path) == []


def test_checker_detects_owned_agents_and_cursor_rules(tmp_path):
    (tmp_path / "AGENTS.md").write_text("<!-- sigma:codex:start -->\nx\n<!-- sigma:codex:end -->\n")
    rules = tmp_path / ".cursor" / "rules"; rules.mkdir(parents=True)
    (rules / "sdlc.mdc").write_text("x")
    got = _mod().find_leftovers(tmp_path)
    assert {row["kind"] for row in got} == {"agents-block", "cursor-rule"}


def test_cli_and_remote_branch_and_github_controls(tmp_path, monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_git", lambda _repo, *args: "origin/sdlc/1\\norigin/feature/x\\n"
                        if args[:2] == ("branch", "-r") else "")
    monkeypatch.setattr(mod, "_github", lambda _name: ([{"name": "sdlc:goal"}], [{"name": "board"}]))
    kinds = {row["kind"] for row in mod.find_leftovers(tmp_path, github="owner/repo")}
    assert {"remote-branch", "github-label", "github-board"} <= kinds
    proc = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), "--json"], text=True,
                          capture_output=True)
    assert proc.returncode == 0 and proc.stdout.strip() == "[]"


def test_copied_markers_match_init_sources():
    setup = ROOT / "skills" / "agrim-setup" / "scripts" / "setup.py"
    init = ROOT / "skills" / "agrim-init" / "scripts" / "sdlc_init.py"
    assert _mod().IGNORE_MARKER in setup.read_text(encoding="utf-8")
    text = init.read_text(encoding="utf-8")
    assert all(marker in text for marker in _mod().AGENTS_MARKERS)

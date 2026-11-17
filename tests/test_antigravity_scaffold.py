"""#2239: the opt-in `--antigravity` scaffold and the docs claims around it.

Nothing here runs Antigravity or Cursor; these tests pin what Sigma WRITES and SAYS."""
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT = ROOT / "skills" / "sigma-init" / "scripts"
START, END = "<!-- sigma:codex:start -->", "<!-- sigma:codex:end -->"


def _repo():
    tmp = tempfile.TemporaryDirectory()
    subprocess.run(["git", "init", "-q", tmp.name], check=True)
    return tmp


def _init(repo, *flags):
    return subprocess.run([sys.executable, str(INIT / "init_flow.py"), repo, "--mode", "local-goals", "--local-only", *flags],
                          capture_output=True, text=True, timeout=120)


def _scaffold(repo, *flags):
    return subprocess.run([sys.executable, str(INIT / "sdlc_init.py"), repo, *flags],
                          capture_output=True, text=True, timeout=120)


def test_flag_writes_the_codex_block_and_says_where_antigravity_looks():
    with _repo() as tmp:
        r = _scaffold(tmp, "--antigravity")
        assert r.returncode == 0, r.stderr
        text = (pathlib.Path(tmp) / "AGENTS.md").read_text()
        assert START in text and END in text
        spec = importlib.util.spec_from_file_location("sdlc_init", INIT / "sdlc_init.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod._CODEX_RULE.rstrip("\n") in text          # reused, not a second copy
        assert ".agents/rules/*.md" in r.stdout and ".agents/skills/" in r.stdout
        assert "never been run in Antigravity" in r.stdout
        assert not (pathlib.Path(tmp) / ".agents").exists()   # no skill link or copy
        assert not (pathlib.Path(tmp) / "GEMINI.md").exists()


def test_flag_is_idempotent_and_preserves_other_rules():
    with _repo() as tmp:
        agents = pathlib.Path(tmp) / "AGENTS.md"
        agents.write_text("# mine\nkeep this\n")
        assert _scaffold(tmp, "--antigravity").returncode == 0
        first = agents.read_text()
        r = _scaffold(tmp, "--antigravity")
        assert agents.read_text() == first and first.count(START) == 1
        assert "already present" in r.stdout
        assert first.startswith("# mine\nkeep this\n")


def test_absent_flag_writes_nothing():
    with _repo() as tmp:
        assert _scaffold(tmp).returncode == 0
        assert not (pathlib.Path(tmp) / "AGENTS.md").exists()
        assert not (pathlib.Path(tmp) / ".agents").exists()


def test_init_flow_accepts_the_flag_and_pins_companions_off():
    with _repo() as tmp:
        r = _init(tmp, "--antigravity", "--no-verify")
        # rc may be 1 from the host-access preflight step in a bare fixture; the scaffold is the claim
        assert "Antigravity adapter" in r.stdout and "REFUSED at" not in r.stdout, r.stdout
        assert START in (pathlib.Path(tmp) / "AGENTS.md").read_text()
        cfg = json.loads((pathlib.Path(tmp) / ".sdlc" / "config.json").read_text())
        assert cfg.get("companions") == "off"
    with _repo() as tmp:
        _init(tmp, "--no-verify")
        assert not (pathlib.Path(tmp) / "AGENTS.md").exists()
        cfg = json.loads((pathlib.Path(tmp) / ".sdlc" / "config.json").read_text())
        assert cfg.get("companions") == "auto"                # opt-in only: untouched default


def test_usage_and_help_list_the_flag():
    helped = subprocess.run([sys.executable, str(INIT / "sdlc_init.py"), "--help"],
                            capture_output=True, text=True, timeout=60)
    assert "--antigravity" in helped.stdout
    flow = subprocess.run([sys.executable, str(INIT / "init_flow.py"), "--help"],
                          capture_output=True, text=True, timeout=60)
    assert "--antigravity" in flow.stdout


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_platform_support_has_an_honest_antigravity_section():
    text = _read("docs/platform-support.md")
    assert "### Antigravity (experimental)" in text
    section = text.split("### Antigravity (experimental)")[1].split("\n## ")[0]
    assert "Never run in Antigravity" in section
    for unmeasured in ("hooks", "MCP", "workflows", "agy", "subagents"):
        assert unmeasured in section
    assert "antigravity.google/docs/rules" in section and "antigravity.google/docs/skills" in section
    assert "17 Nov 2026" in section


def test_no_doc_claims_antigravity_works_or_is_supported():
    for rel in ("README.md", "docs/platform-support.md", "CHANGELOG.md"):
        for line in _read(rel).splitlines():
            low = line.lower()
            if "antigravity" in low:
                for bad in ("antigravity works", "antigravity is supported", "supports antigravity",
                            "fully validated", "validated in antigravity"):
                    assert bad not in low, (rel, line)
    assert "| Antigravity |" in _read("README.md")
    assert "never run" in _read("README.md").lower()


def test_cursor_is_no_longer_said_to_have_no_hooks():
    for rel in ("AGENTS.md", "docs/enforcement.md", "docs/platform-support.md",
                "skills/sigma-doctor/scripts/enforcement_table.py"):
        text = _read(rel)
        assert "Cursor has NO hooks" not in text and "Cursor and Codex have no hooks" not in text, rel
    agents = _read("AGENTS.md")
    assert ".cursor/hooks.json" in agents and "https://cursor.com/docs/hooks" in agents
    assert "unmeasured" in agents and "never load-bearing" in " ".join(agents.split())

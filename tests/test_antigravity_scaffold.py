"""#2239: the opt-in `--antigravity` scaffold and the docs claims around it.

Nothing here runs Antigravity or Cursor; these tests pin what Sigma WRITES and SAYS."""
import importlib.util
import json
import re
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
        assert "AGENTS.md" in r.stdout and "experimental" not in r.stdout.lower()
        ag = "\n".join(l for l in r.stdout.splitlines() if "Antigravity" in l)
        assert ag and not re.search(r"Nov 2026|part-way|not run|measured|docs/platform-support|unadapted", ag, re.I), ag
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


def test_init_flow_accepts_the_flag_and_changes_no_config_value():
    with _repo() as base:
        _init(base, "--no-verify")
        before = json.loads((pathlib.Path(base) / ".sdlc" / "config.json").read_text())
    with _repo() as tmp:
        r = _init(tmp, "--antigravity", "--no-verify")
        # rc may be 1 from the host-access preflight step in a bare fixture; the scaffold is the claim
        assert "Antigravity adapter" in r.stdout and "REFUSED at" not in r.stdout, r.stdout
        assert "companions" not in r.stdout
        assert START in (pathlib.Path(tmp) / "AGENTS.md").read_text()
        cfg = json.loads((pathlib.Path(tmp) / ".sdlc" / "config.json").read_text())
        assert cfg == before                                  # the flag writes no config value
    with _repo() as tmp:
        _init(tmp, "--no-verify")
        assert not (pathlib.Path(tmp) / "AGENTS.md").exists()   # opt-in only


def test_usage_and_help_list_the_flag():
    helped = subprocess.run([sys.executable, str(INIT / "sdlc_init.py"), "--help"],
                            capture_output=True, text=True, timeout=60)
    assert "--antigravity" in helped.stdout
    flow = subprocess.run([sys.executable, str(INIT / "init_flow.py"), "--help"],
                          capture_output=True, text=True, timeout=60)
    assert "--antigravity" in flow.stdout


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_platform_support_has_a_plain_antigravity_section():
    text = _read("docs/platform-support.md")
    assert "\n### Antigravity\n" in text
    section = text.split("\n### Antigravity\n")[1].split("\n## ")[0]
    assert "Sigma works with Antigravity" in section
    assert "agy plugin install" in section and "--antigravity" in section and "AGENTS.md" in section
    assert "antigravity.google/docs/rules" in section and "antigravity.google/docs/skills" in section
    assert not re.search(r"Not run in these tests|18 Nov 2026|scratch repository|unmeasured", section)


def _printed_text():
    out = subprocess.run([sys.executable, str(INIT / "sdlc_init.py"), "--help"],
                         capture_output=True, text=True, timeout=60).stdout
    with _repo() as tmp:
        out += _scaffold(tmp, "--antigravity").stdout
    return out


def test_public_pages_state_antigravity_support_plainly():
    readme = _read("README.md")
    row = [l for l in readme.splitlines() if l.startswith("| Antigravity |")]
    assert len(row) == 1 and "Works with Antigravity" in row[0]
    assert "agy plugin install" in row[0] and "--antigravity" in row[0]
    site = _read("site/hosts.html").split('id="antigravity"')[1]
    assert "Sigma works with Antigravity" in site and "agy plugin install" in site
    for name, text in (("README.md", readme), ("site/hosts.html", _read("site/hosts.html")),
                       ("printed", _printed_text())):
        assert not re.search(r"18 Nov 2026|Not run in these tests|scratch repository", text), name


_NO_HOOKS = re.compile(r"cursor[,\s]+(which\s+)?(has|and codex have|and codex has)\s+no\s+hooks|no hooks \(cursor\)"
                       r"|cursor[- ]style host has no hooks|cursor/codex have no hooks", re.I)


def test_cursor_is_no_longer_said_to_have_no_hooks():
    files = subprocess.run(["git", "ls-files", "hooks", "docs", "skills", "README.md", "AGENTS.md"],
                           cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    hits = []
    for rel in files:
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += [f"{rel}: {m.group(0)}" for m in _NO_HOOKS.finditer(" ".join(text.split()))]
    assert not hits, hits
    agents = _read("AGENTS.md")
    assert ".cursor/hooks.json" in agents and "https://cursor.com/docs/hooks" in agents
    assert "unmeasured" in agents and "never load-bearing" in " ".join(agents.split())

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
        assert "docs/platform-support.md" in r.stdout and "experimental" not in r.stdout.lower()
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


def test_platform_support_has_an_honest_antigravity_section():
    text = _read("docs/platform-support.md")
    assert "\n### Antigravity\n" in text
    section = text.split("\n### Antigravity\n")[1].split("\n## ")[0]
    assert "Not run in these tests" in section and "18 Nov 2026" in section
    assert "not taken on to a pull request or a merge" in section
    for unmeasured in ("hooks", "MCP", "workflows", "agy", "subagents"):
        assert unmeasured in section
    assert "antigravity.google/docs/rules" in section and "antigravity.google/docs/skills" in section
    assert "17 Nov 2026" in section


_NEG = ("not", "never", "un", "no ")
_CLAIM = re.compile(r"antigravity\b[^.!?\n]*?\b(works|working|runs|supported|compatible|validated)\b", re.I)


def _overclaims(text):
    """Sentences where Antigravity is followed by a positive claim word with no negation before it."""
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        m = _CLAIM.search(sentence)
        if m and not any(n in sentence[:m.start(1)].lower() for n in _NEG):
            found.append(sentence)
    return found


def _printed_text():
    out = subprocess.run([sys.executable, str(INIT / "sdlc_init.py"), "--help"],
                         capture_output=True, text=True, timeout=60).stdout
    with _repo() as tmp:
        out += _scaffold(tmp, "--antigravity").stdout
    return out


def test_no_doc_claims_antigravity_works_or_is_supported():
    for name, text in (("README.md", _read("README.md")),
                       ("docs/platform-support.md", _read("docs/platform-support.md")),
                       ("printed", _printed_text())):
        assert not _overclaims(text), (name, _overclaims(text))
        if name != "printed":
            assert "not run in these tests" in text.lower(), name
        else:
            assert "18 Nov 2026" in text and "not to a merged pull request" in text, name
    assert "| Antigravity |" in _read("README.md")


def test_the_overclaim_regex_catches_a_positive_claim():
    assert _overclaims("Antigravity runs Sigma.")
    assert _overclaims("Antigravity is supported.")
    assert not _overclaims("Sigma was not run in Antigravity, so nothing there works is claimed.")


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

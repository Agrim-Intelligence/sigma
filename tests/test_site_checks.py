"""Checks on the static docs site in `site/` (#2244). Standard library only.

Each check is a function `check_*(site: Path) -> list[str]` that returns problems. Every check has
two kinds of test: one on the real `site/` (must be clean) and one CONTROL that copies `site/` to a
temp directory, breaks exactly one thing, and asserts the check reports it. A check never seen red
proves nothing (AGENTS.md, "Run the control"); the controls keep that proof in the suite.
"""
import html.parser
import importlib.util
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"

# The words are spelled in pieces so this file does not itself carry them.
BANNED_WORDS = ["om" + "ega", "on" + "shot", "loop" + "smith", "loop " + "smith", "loop-" + "smith",
                "loop_" + "smith", "ls-" + "docs", "rail" + "way", "run" + "pod", "kg-" + "storytelling",
                "/sd" + "lc-"]
# Whatever the spacing or case: the old product name with any separator between its halves.
BANNED_RE = re.compile("loop" + r"[\s_.-]*" + "smith", re.I)
BANNED_PATHS = ["/Us" + "ers/", "/ho" + "me/"]
HOSTS = ["Claude Code", "Codex", "Cursor", "Antigravity"]
FLAGS = ["--codex", "--cursor", "--antigravity"]


def _pages(site):
    return sorted(site.glob("*.html"))


def _files(site):
    return sorted(p for p in site.rglob("*") if p.is_file())


def _contact_emails():
    found = set()
    for name in ("SECURITY.md", "CODE_OF_CONDUCT.md"):
        p = ROOT / name
        if p.exists():
            found |= set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", p.read_text(encoding="utf-8")))
    return {e.lower() for e in found}


class _Tags(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []  # (tag, attrs dict)
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        self.tags.append((tag, a))
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "a" and "name" in a:
            self.ids.add(a["name"])

    handle_startendtag = handle_starttag


def _parse(path):
    p = _Tags()
    p.feed(path.read_text(encoding="utf-8"))
    return p


def _skill_dirs():
    return {d.name for d in (ROOT / "skills").iterdir() if d.is_dir()}


# ---------------------------------------------------------------- the checks

def _scan_text(rel, text, emails):
    out = []
    low = text.lower()
    for w in BANNED_WORDS:
        if w.lower() in low:
            out.append(f"{rel}: banned word {w!r}")
    if BANNED_RE.search(text):
        out.append(f"{rel}: banned name variant")
    for pth in BANNED_PATHS:
        if pth in text:
            out.append(f"{rel}: absolute home path {pth!r}")
    for e in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text):
        if e.lower() not in emails:
            out.append(f"{rel}: email {e!r} is not a project contact")
    return out


def check_leak_files(paths):
    """The same scan over files outside site/ that this change adds (tools, tests)."""
    emails = _contact_emails()
    out = []
    for f in paths:
        out += _scan_text(f.name, f.read_text(encoding="utf-8"), emails)
    return out


def check_leak(site):
    out = []
    emails = _contact_emails()
    for f in _files(site):
        rel = f.relative_to(site)
        out += _scan_text("file name " + str(rel), str(rel), emails)
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        out += _scan_text(rel, text, emails)
    return out


def check_commands(site):
    out = []
    skills = _skill_dirs()
    for f in _pages(site):
        text = f.read_text(encoding="utf-8")
        for cmd in sorted(set(re.findall(r"(?<![\w/.#-])/(sigma-[a-z0-9-]+)", text))):
            if cmd not in skills:
                out.append(f"{f.name}: /{cmd} has no directory under skills/")
        for m in re.finditer(r"\b(\d+)\s+(?:sigma\s+)?(?:skills|commands)\b", re.sub(r"<[^>]+>", " ", text)):
            if int(m.group(1)) != len(skills):
                out.append(f"{f.name}: states {m.group(1)} skills, skills/ has {len(skills)}")
    return out


def check_links(site):
    out = []
    parsed = {f.name: _parse(f) for f in _pages(site)}
    for f in _pages(site):
        for tag, a in parsed[f.name].tags:
            for attr in ("href", "src"):
                ref = a.get(attr)
                if ref is None or ref == "#":
                    continue
                if re.match(r"(?:[a-z][a-z0-9+.-]*:|//)", ref, re.I):
                    continue  # absolute URLs and data: are not resolved here
                target, _, frag = ref.partition("#")
                dest = f if target == "" else (f.parent / target.split("?")[0])
                if not dest.is_file():
                    out.append(f"{f.name}: <{tag} {attr}={ref!r}> target does not exist")
                    continue
                if frag:
                    if dest.suffix != ".html":
                        continue
                    ids = parsed.get(dest.name)
                    if ids is None:
                        ids = _parse(dest)
                    if frag not in ids.ids:
                        out.append(f"{f.name}: <{tag} {attr}={ref!r}> anchor #{frag} not found in {dest.name}")
    return out


def check_svgs(site):
    out = []
    for f in _pages(site):
        for i, m in enumerate(re.finditer(r"<svg\b.*?</svg>", f.read_text(encoding="utf-8"), re.S), 1):
            try:
                ET.fromstring(m.group(0))
            except ET.ParseError as e:
                out.append(f"{f.name}: inline svg #{i} does not parse: {e}")
    return out


def check_versions(site):
    out = []
    for f in _pages(site):
        if f.name == "releases.html":
            continue
        for m in re.finditer(r"\d+\.\d+\.\d+", f.read_text(encoding="utf-8")):
            out.append(f"{f.name}: version-looking string {m.group(0)!r} (allowed only on releases.html)")
    return out


def check_external(site):
    out = []
    ext = re.compile(r"(?:https?:)?//", re.I)
    for f in _pages(site):
        for tag, a in _parse(f).tags:
            if tag == "script" and a.get("src") is not None and ext.match(a["src"]):
                out.append(f"{f.name}: external script {a['src']!r}")
            if tag == "link" and ext.match(a.get("href", "")):
                out.append(f"{f.name}: external <link> {a['href']!r}")
            if tag in ("img", "source", "video", "audio", "iframe", "embed") and ext.match(a.get("src", "")):
                out.append(f"{f.name}: external <{tag}> {a['src']!r}")
    for f in site.rglob("*.css"):
        text = f.read_text(encoding="utf-8")
        if re.search(r"@import|url\(\s*['\"]?(?:https?:)?//", text, re.I):
            out.append(f"{f.relative_to(site)}: external @import or url()")
    return out


def check_nav(site):
    spec = importlib.util.spec_from_file_location("site_nav", ROOT / "tools" / "site_nav.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check(site)


def check_hosts_flags(site):
    out = []
    hosts = (site / "hosts.html").read_text(encoding="utf-8") if (site / "hosts.html").exists() else ""
    start = (site / "get-started.html").read_text(encoding="utf-8") if (site / "get-started.html").exists() else ""
    for h in HOSTS:
        if h not in hosts:
            out.append(f"hosts.html: does not name {h}")
    for flag in FLAGS:
        if flag not in start:
            out.append(f"get-started.html: does not show {flag}")
    return out


# ------------------------------------------------------------------ real site

def test_added_tooling_is_clean():
    mine = [ROOT / "tools" / "site_nav.py", ROOT / ".github" / "workflows" / "pages.yml"]
    mine += sorted((ROOT / "tests").glob("test_site_*.py"))
    assert check_leak_files(mine) == []


@pytest.mark.parametrize("check", [check_leak, check_commands, check_links, check_svgs, check_versions,
                                   check_external, check_nav, check_hosts_flags])
def test_site_is_clean(check):
    assert SITE.is_dir(), "site/ is missing"
    assert check(SITE) == []


def test_site_has_pages_to_check():
    # A check over zero pages passes vacuously.
    assert len(_pages(SITE)) >= 20
    assert any("<svg" in p.read_text(encoding="utf-8") for p in _pages(SITE))


# ------------------------------------------------------------------- controls

@pytest.fixture
def broken(tmp_path):
    dst = tmp_path / "site"
    shutil.copytree(SITE, dst)

    def edit(page, fn):
        p = dst / page
        p.write_text(fn(p.read_text(encoding="utf-8")), encoding="utf-8")
        return dst

    return edit


def test_control_leak_word(broken):
    assert check_leak(broken("index.html", lambda t: t.replace("<main>", "<main><p>" + BANNED_WORDS[0] + "</p>")))


def test_control_leak_home_path(broken):
    assert check_leak(broken("index.html", lambda t: t.replace("<main>", "<main><p>" + BANNED_PATHS[0] + "x</p>")))


def test_control_leak_email(broken):
    assert check_leak(broken("index.html", lambda t: t.replace("<main>", "<main><p>a" + "@b.example</p>")))


def test_control_unknown_command(broken):
    assert check_commands(broken("index.html", lambda t: t.replace("<main>", "<main><p>/sigma-nonesuch</p>")))


def test_control_skill_count(broken):
    n = len(_skill_dirs()) + 1
    assert check_commands(broken("index.html", lambda t: t.replace("<main>", f"<main><p>{n} skills</p>")))


def test_control_dead_link(broken):
    assert check_links(broken("index.html", lambda t: t.replace("<main>", '<main><a href="nope.html">x</a>')))


def test_control_dead_anchor(broken):
    assert check_links(broken("index.html", lambda t: t.replace("<main>", '<main><a href="hosts.html#nope">x</a>')))


def test_control_dead_same_page_anchor(broken):
    assert check_links(broken("index.html", lambda t: t.replace("<main>", '<main><a href="#nope">x</a>')))


def test_control_malformed_svg(broken):
    assert check_svgs(broken("index.html", lambda t: t.replace("</svg>", "<g></svg>", 1)))


def test_control_svg_entity(broken):
    # An HTML-only entity is legal in HTML and breaks XML, which is the usual way an svg goes bad.
    assert check_svgs(broken("index.html", lambda t: t.replace("<defs>", "<text>&rarr;</text><defs>", 1)))


def test_control_version_on_index(broken):
    assert check_versions(broken("index.html", lambda t: t.replace("<main>", "<main><p>9.9.9</p>")))


def test_control_version_allowed_on_releases(broken):
    assert check_versions(broken("releases.html", lambda t: t.replace("<main>", "<main><p>9.9.9</p>"))) == []


def test_control_external_script(broken):
    assert check_external(broken("index.html", lambda t: t.replace("<main>", '<main><script src="https://x.example/a.js"></script>')))


def test_control_external_stylesheet(broken):
    assert check_external(broken("index.html", lambda t: t.replace(
        '<link rel="stylesheet" href="assets/style.css">', '<link rel="stylesheet" href="https://x.example/s.css">')))


def test_control_external_image(broken):
    assert check_external(broken("index.html", lambda t: t.replace("<main>", '<main><img src="http://x.example/a.png">')))


def test_control_external_css_import(broken, tmp_path):
    site = broken("index.html", lambda t: t)
    css = site / "assets" / "style.css"
    css.write_text('@import url("https://x.example/f.css");\n' + css.read_text(encoding="utf-8"), encoding="utf-8")
    assert check_external(site)


def test_control_nav_drift(broken):
    assert check_nav(broken("flow.html", lambda t: t.replace(">Ways in<", ">Ways out<", 1)))


def test_control_nav_wrong_active(broken):
    assert check_nav(broken("flow.html", lambda t: t.replace(' class="active"', "", 1)))


def test_control_missing_page(broken):
    site = broken("index.html", lambda t: t)
    (site / "glossary.html").unlink()
    assert check_nav(site)


def test_control_missing_flag(broken):
    assert check_hosts_flags(broken("get-started.html", lambda t: t.replace("--antigravity", "--agy")))


def test_control_missing_host(broken):
    assert check_hosts_flags(broken("hosts.html", lambda t: t.replace("Antigravity", "Agy")))


@pytest.mark.parametrize("text", ["Loop" + "Smith", "loop " + "smith", "loop-" + "smith", "LOOP_" + "SMITH",
                                  "ls-" + "docs", "/sd" + "lc-goal"])
def test_control_leak_variants(broken, text):
    assert check_leak(broken("index.html", lambda t: t.replace("<main>", "<main><!-- " + text + " -->")))


def test_control_leak_file_name(broken):
    site = broken("index.html", lambda t: t)
    (site / ("loop" + "smith.txt")).write_text("x", encoding="utf-8")
    assert check_leak(site)


def test_control_leak_in_tooling(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("# " + "Loop" + "Smith\n", encoding="utf-8")
    assert check_leak_files([f])

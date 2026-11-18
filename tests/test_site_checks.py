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


# ------------------------------------------------------------------- contrast

CSS = SITE / "assets" / "style.css"
# (foreground variable, background variable) pairs the stylesheet actually draws as text.
TEXT_PAIRS = (
    [(fg, bg) for fg in ("--text", "--text-dim", "--text-faint", "--link", "--link-hover")
     for bg in ("--bg", "--bg-raised", "--bg-sunken")]
    + [("--fill-ink", "--fill"), ("--code-text", "--code-bg"), ("--link", "--code-bg")]
)

# The only colours the site may contain: white, black, the greys and the single link hue pair.
# Light and dark values of each token, as read from the portfolio's published pages.
ALLOWED_HEX = {
    "#ffffff", "#f4f6f7", "#d9dfe3", "#c9d2d7", "#10181c", "#56646e",   # light scheme
    "#0e171b", "#152127", "#25333a", "#34444c", "#e6ecef", "#c3ced4", "#a9b8c0",  # dark scheme
    "#7d8a92", "#667982",                                                 # diagram line, light and dark
    "#08769a", "#065a76", "#5cc3e6", "#8dd6ef",                         # link hue, both schemes
}


def _vars(block):
    return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b", block))


def _blocks(css):
    """(light, dark-from-media-query, dark-from-data-theme) variable maps."""
    light = _vars(re.search(r"^:root\s*\{(.*?)^\}", css, re.S | re.M).group(1))
    media = re.search(r":root:not\(\[data-theme=\"light\"\]\)\s*\{(.*?)\n  \}", css, re.S).group(1)
    forced = re.search(r":root\[data-theme=\"dark\"\]\s*\{(.*?)^\}", css, re.S | re.M).group(1)
    return light, {**light, **_vars(media)}, {**light, **_vars(forced)}


def _lum(hexcolor):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def contrast_problems(css_text, floor=4.5):
    light, dark_media, dark_forced = _blocks(css_text)
    out = []
    if dark_media != dark_forced:
        out.append("dark palette differs between the media query and [data-theme=dark]")
    for name, scheme in (("light", light), ("dark", dark_media)):
        for fg, bg in TEXT_PAIRS:
            r = ratio(scheme[fg], scheme[bg])
            if r < floor:
                out.append(f"{name}: {fg} on {bg} is {r:.2f}:1 (< {floor})")
    return out


def test_text_contrast_is_at_least_4_5_to_1_in_both_schemes():
    assert contrast_problems(CSS.read_text(encoding="utf-8")) == []


def test_control_lowered_colour_turns_contrast_red():
    css = CSS.read_text(encoding="utf-8")
    light = _blocks(css)[0]
    assert contrast_problems(css.replace(light["--text-faint"], "#bbbbbb", 1))
    assert contrast_problems(css.replace(light["--link"], "#86d0e8", 1))


def stray_colours(texts):
    """Hex colours or rgb()/hsl() in site CSS and pages that are not on the allowlist."""
    out = []
    for name, text in texts:
        text = re.sub(r"&#\d+;|&#x[0-9a-fA-F]+;", "", text)  # numeric character references
        # Long forms anywhere; the 3-digit form only as a colour value (prose has "#123" issue refs).
        found = re.findall(r"(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6})\b(?![\w-])", text)
        for h in re.findall(r"(?:fill|stroke|color|background|stop-color|border[\w-]*)\s*[:=]\s*[\"']?\s*(#[0-9a-fA-F]{3})\b", text):
            found.append("#" + "".join(ch * 2 for ch in h[1:]))
        for h in found:
            if h.lower() not in ALLOWED_HEX:
                out.append(f"{name}: {h}")
        if re.search(r"\b(?:rgba?|hsla?)\(", text):
            out.append(f"{name}: rgb()/hsl() colour")
    return out


def _site_texts():
    files = [CSS, *sorted(SITE.glob("*.html"))]
    return [(f.name, f.read_text(encoding="utf-8")) for f in files]


def test_site_uses_only_black_white_greys_and_the_link_hue():
    assert stray_colours(_site_texts()) == []


def test_control_stray_colour_turns_palette_check_red():
    texts = _site_texts()
    assert stray_colours(texts + [("style.css", "a { color: #ff0000; }")])
    assert stray_colours(texts + [("x.html", '<rect fill="#f00"/>')])
    assert stray_colours(texts + [("x.css", "a { color: rgb(1,2,3); }")])


def _nav_module():
    spec = importlib.util.spec_from_file_location("site_nav", ROOT / "tools" / "site_nav.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_canonical_nav_is_collapsible_and_has_no_external_script():
    block = _nav_module().nav_block("run.html")
    assert '<details class="navbox" open>' in block and "<summary>Menu</summary>" in block
    assert block.index('class="brand"') < block.index("<details")
    assert 'aria-current="page"' in block
    assert not re.search(r"<script[^>]*\bsrc\s*=", block)

def line_problems(css_text, floor=3.0):
    """Diagram outlines are non-text graphics: WCAG asks 3:1 against what they sit on."""
    light, dark_media, _ = _blocks(css_text)
    out = []
    for name, scheme in (("light", light), ("dark", dark_media)):
        for bg in ("--bg", "--bg-sunken"):
            r = ratio(scheme["--line"], scheme[bg])
            if r < floor:
                out.append(f"{name}: --line on {bg} is {r:.2f}:1 (< {floor})")
    return out


def test_diagram_lines_are_at_least_3_to_1_in_both_schemes():
    assert line_problems(CSS.read_text(encoding="utf-8")) == []


def test_control_pale_diagram_line_turns_red():
    css = CSS.read_text(encoding="utf-8")
    assert line_problems(css.replace(_blocks(css)[0]["--line"], "#c9d2d7", 1))


def text_on_filled_box_problems(site):
    """A label drawn inside a solid-filled (.dbox-accent) box must use an -inv text class, or it is
    the box's own colour and invisible."""
    out = []
    for page in sorted(site.glob("*.html")):
        src = page.read_text(encoding="utf-8")
        for svg in re.findall(r"<svg\b.*?</svg>", src, re.S):
            boxes = [tuple(float(v) for v in m) for m in re.findall(
                r'<rect class="dbox-accent"[^>]*?x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg)]
            for m in re.finditer(r'<text class="([^"]+)" x="([\d.]+)" y="([\d.]+)"([^>]*)>', svg):
                cls, x, y, rest = m.group(1), float(m.group(2)), float(m.group(3)), m.group(4)
                for bx, by, bw, bh in boxes:
                    inside_y = by <= y <= by + bh
                    anchor_mid = "text-anchor=\"middle\"" in rest
                    inside_x = bx <= x <= bx + bw if anchor_mid else bx <= x < bx + bw
                    if inside_x and inside_y and "inv" not in cls:
                        out.append(f"{page.name}: text class '{cls}' at ({x:g},{y:g}) sits on a filled box")
    return out


def test_no_label_is_drawn_in_its_own_boxes_colour():
    assert text_on_filled_box_problems(SITE) == []


def test_control_dark_label_on_filled_box_turns_red(tmp_path):
    shutil.copytree(SITE, tmp_path / "site")
    page = tmp_path / "site" / "index.html"
    page.write_text(page.read_text(encoding="utf-8").replace("dtext-mono-inv", "dtext-mono", 1), encoding="utf-8")
    assert text_on_filled_box_problems(tmp_path / "site")


# ------------------------------------------------- clean page template (#2244)

def check_on_this_page(site):
    """Every page has an 'On this page' list, and each of its links resolves to an id on the page."""
    out = []
    for f in _pages(site):
        text = f.read_text(encoding="utf-8")
        m = re.search(r'<nav class="toc"[^>]*>(.*?)</nav>', text, re.S)
        if not m:
            out.append(f"{f.name}: no 'On this page' list")
            continue
        ids = _parse(f).ids
        links = re.findall(r'href="#([^"]+)"', m.group(1))
        if not links:
            out.append(f"{f.name}: 'On this page' list has no links")
        out += [f"{f.name}: 'On this page' link #{x} has no target" for x in links if x not in ids]
    return out


def check_get_started_shows_every_host(site):
    """With no host selected (or no CSS), every host's section is in the page, unhidden, with two steps."""
    text = (site / "get-started.html").read_text(encoding="utf-8")
    out = []
    for h in ("claude-code", "codex", "cursor", "antigravity"):
        m = re.search(rf'<section class="host" id="{h}">(.*?)</section>', text, re.S)
        if not m:
            out.append(f"get-started.html: no section for {h}")
            continue
        if len(re.findall(r"<li>", re.search(r"<ol.*?</ol>", m.group(1), re.S).group(0))) != 2:
            out.append(f"get-started.html: {h} does not have exactly two steps")
    if re.search(r"\bhidden\b|style=", text):
        out.append("get-started.html: a host section is hidden in the markup")
    css = (site / "assets" / "style.css").read_text(encoding="utf-8")
    for rule in re.findall(r"([^{}]*\.host[^{}]*)\{[^}]*display:\s*none", css):
        if ":has(" not in rule:
            out.append(f"style.css: host-hiding rule not gated on :has(): {rule.strip()[:60]}")
    return out


def test_every_page_has_a_working_on_this_page_list():
    assert check_on_this_page(SITE) == []


def test_control_dead_toc_link(broken):
    assert check_on_this_page(broken("flow.html", lambda t: t.replace('<a href="#', '<a href="#zz-', 1)))


def test_control_missing_toc(broken):
    assert check_on_this_page(broken("flow.html", lambda t: t.replace('class="toc"', 'class="nottoc"')))


def test_get_started_shows_every_host_without_a_selection():
    assert check_get_started_shows_every_host(SITE) == []


def test_control_hidden_host_turns_red(broken):
    assert check_get_started_shows_every_host(
        broken("get-started.html", lambda t: t.replace('<section class="host" id="codex">', '<section class="host" id="codex" hidden>')))


def test_control_ungated_host_rule_turns_red(broken):
    site = broken("get-started.html", lambda t: t)
    css = site / "assets" / "style.css"
    css.write_text(css.read_text(encoding="utf-8") + "\n.host { display: none; }\n", encoding="utf-8")
    assert check_get_started_shows_every_host(site)

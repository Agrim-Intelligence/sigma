"""agrim-vision deep pass: the thin one-pass stays default; optional per-tier deep-elicitation guides
load on demand from references/. Pins that all four guides exist, the SKILL wires the on-demand load,
the architecture guide drafts FROM the codebase (not a blank page), and nothing leaks the source repo
these were genericized from."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
VIS = ROOT / "skills" / "agrim-vision"
REFS = VIS / "references"
TIERS = ("vision", "strategy", "design", "architecture")


def test_all_four_deep_guides_exist():
    for t in TIERS:
        assert (REFS / f"{t}.md").exists(), f"missing deep guide: references/{t}.md"


def test_skill_wires_the_on_demand_deep_pass():
    t = (VIS / "SKILL.md").read_text()
    assert "references/" in t and "on demand" in t.lower()
    assert "thin" in t.lower() or "lean default" in t.lower()   # the default stays thin


def test_architecture_guide_drafts_from_the_codebase():
    t = (REFS / "architecture.md").read_text().lower()
    assert "from the code" in t or "from the codebase" in t
    for signal in ("readme", "git log", "numbered"):          # repo-auto-detect + checkable rules
        assert signal in t, f"architecture guide missing '{signal}'"
    assert "claude.md" in t and "agents.md" in t              # host-agnostic rule sources


def test_guides_write_to_the_sdlc_northstar_not_a_hardcoded_path():
    for t in TIERS:
        body = (REFS / f"{t}.md").read_text()
        assert ".sdlc/context/north-star.md" in body


def test_no_source_repo_leakage_in_deep_pass():
    """DoD: genericized — no product name, no docs/context path, no provenance, no domain skin."""
    banned = ("docs/context", "Ported from", "OnShot", "onshot", "storytelling",
              "episode", "lipsync", "screenplay", "media-orch")
    for f in [VIS / "SKILL.md", *REFS.glob("*.md")]:
        text = f.read_text()
        for b in banned:
            assert b not in text, f"{f.name}: leaked '{b}'"

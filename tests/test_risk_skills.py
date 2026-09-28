"""The conditional-risk review skills (Slice 2): agrim-security-review / -contract-check /
-migration-check / -release-check / -debug. These are Sigma's OWN skills (no platform companion) —
orthogonal to agrim-review's code-quality pass, invoked only when a change trips the matching risk. This
guards their port: each ships a valid SKILL.md, and none persists its artifact into the gitignored
`.sdlc/knowledge/` tree (Slice 1), which would make a review invisible to its PR."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
RISK_SKILLS = ("agrim-security-review", "agrim-contract-check", "agrim-migration-check",
               "agrim-release-check", "agrim-debug")


def test_risk_skills_present_with_valid_frontmatter():
    for skill in RISK_SKILLS:
        p = ROOT / "skills" / skill / "SKILL.md"
        assert p.exists(), "%s: missing SKILL.md" % skill
        text = p.read_text(encoding="utf-8")
        assert text.startswith("---\n"), "%s: no frontmatter" % skill
        assert "name: %s\n" % skill in text, "%s: name does not match dir" % skill
        assert "description:" in text and "allowed-tools:" in text, "%s: missing frontmatter keys" % skill


def test_risk_skills_persist_to_reviews_not_the_gitignored_knowledge_dir():
    for skill in RISK_SKILLS:
        text = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert ".sdlc/reviews/" in text, "%s: should persist artifacts to .sdlc/reviews/" % skill
        # the only allowed mention of the gitignored dir is the explicit "NOT under" guidance
        for line in text.splitlines():
            if ".sdlc/knowledge/" in line:
                assert "NOT under" in line and "gitignored" in line, "%s: writes under knowledge/" % skill


def test_security_review_accounts_for_every_owasp_category():
    """Every OWASP Top 10:2021 category must be accounted for — covered here, or explicitly
    delegated. A09 belongs to agrim-review axis 5 (Observability) by the MECE partition; it must be
    named as delegated rather than silently missing, so a reader can tell a decision from a gap."""
    t = (ROOT / "skills" / "agrim-security-review" / "SKILL.md").read_text()
    for code in ("A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09", "A10"):
        assert code in t, f"OWASP {code} is not accounted for in agrim-security-review"
    low = t.lower()
    # the four that were absent before this change, by their real surfaces
    assert "deserial" in low          # A08 — unsafe deserialization / supply-chain integrity
    assert "ssrf" in low              # A10
    assert "misconfigur" in low       # A05
    assert "crypt" in low             # A02
    # A09 is delegated, not duplicated — the partition must be visible in the text
    assert "axis 5" in low or "observability" in low


def test_security_checklist_and_report_template_agree():
    """A checklist the report cannot carry is doc drift — the axis-7 failure, in the skill's own file."""
    t = (ROOT / "skills" / "agrim-security-review" / "SKILL.md").read_text()
    checklist = re.findall(r"^(\d+)\. \*\*", t, re.M)
    trace = re.findall(r"^(\d+)\. \w+.*— <one line>", t, re.M)
    assert checklist, "no numbered checklist found"
    assert len(checklist) == len(trace), (
        f"checklist has {len(checklist)} points but the report template has {len(trace)} trace lines")

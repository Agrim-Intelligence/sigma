"""/sigma-audit — the periodic whole-repo audit. Guards the contracts that keep it from becoming
either a second per-diff reviewer or an unasked backlog spammer."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "sigma-audit" / "SKILL.md"


def test_frontmatter_is_valid():
    t = SKILL.read_text(encoding="utf-8")
    assert t.startswith("---\n")
    assert "name: sigma-audit\n" in t
    assert "description:" in t and "allowed-tools:" in t


def test_names_all_four_lenses_and_grounds_them_in_the_collector():
    t = SKILL.read_text(encoding="utf-8")
    for lens in ("Conformance", "Erosion", "Debt", "Fitness"):
        assert lens in t, f"missing lens: {lens}"
    assert "audit-collect.sh" in t, "the audit must reason from the measured pack, not from a re-read"


def test_shares_sdlc_review_vocabulary():
    """Three skills, one set of words. A repo-scale finding named differently from the diff-scale
    one is a finding nobody can act on."""
    audit = SKILL.read_text(encoding="utf-8")
    review = (ROOT / "skills" / "sigma-review" / "SKILL.md").read_text(encoding="utf-8")
    for smell in ("Feature Envy", "Duplicate Code", "Speculative Generality"):
        assert smell in audit, f"sigma-audit does not name {smell}"
        assert smell in review, f"sigma-review no longer names {smell} — the two have drifted"


def test_filing_is_opt_in_and_deduped_with_evidence():
    """The kit's contract is 'never file UNASKED'. And an auditor that re-files the same findings
    every quarter gets switched off after its second run."""
    t = SKILL.read_text(encoding="utf-8")
    low = t.lower()
    assert "--file" in t, "filing must be an explicit flag, not a default"
    assert "read-only" in low
    assert "dedup" in low
    assert "name the issues" in low, "the dedup claim must be evidenced, not asserted"
    assert "create_tracked_issue" in t, "must file through the one disciplined path"
    assert "area:tech-debt" in t and "chore" in t, "must reuse the existing label taxonomy"


def test_refuses_to_re_run_per_diff_review():
    """Without this the audit becomes a slow second code review, and its findings stop being
    structural."""
    t = SKILL.read_text(encoding="utf-8")
    assert "What this check is not" in t
    assert "sigma-review" in t and "sigma-align" in t


def test_caps_and_states_what_it_dropped():
    """Assert the CAP PHRASES, not bare digits — "8" appears in any date and would guard nothing.
    Case-insensitive: a cap that opens a sentence is legitimately capitalised."""
    low = SKILL.read_text(encoding="utf-8").lower()
    assert "at most 12" in low, "the finding cap must be stated"
    assert "at most 8" in low, "the filing cap must be stated"
    assert "silent" in low, "truncation must be declared, never silent"


def test_review_health_scan_mode_is_retired_and_points_here():
    """One home per concern. Health-scan was 'a thorough audit of files/dirs for issues that
    accumulate over time' — /sigma-audit's entire job, in one unspecified line."""
    review = (ROOT / "skills" / "sigma-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "Health scan" not in review, "health-scan mode must be retired, not left as a rival"
    assert "/sigma-audit" in review, "sigma-review must point whole-repo work at the audit"


def test_audit_is_discoverable_in_the_readme():
    """A skill nobody can find is a skill nobody runs — /sigma-audit shipped with zero README mentions
    and was invisible until someone went looking in skills/.

    Deliberately targeted rather than "every skill must appear in the README": six skills legitimately
    do not (sigma-brainstorm/implement/plan/plan-review/review/verify are portable phase executors the
    loop invokes, not user-facing commands), so the general rule would encode something untrue."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "/sigma-audit" in readme, "the audit skill must be listed in the README"
    assert "/sigma-align" in readme  # its sibling, so the pair stays discoverable together

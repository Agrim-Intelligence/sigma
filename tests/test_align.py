"""agrim-align: the cumulative-drift audit. Its whole reason to exist is that it reads a WINDOW of
shipped goals — the per-plan gate (agrim-plan-review §4) and the per-goal one (agrim-retro) structurally
cannot see a trajectory. These pin that it stays a window check, stays advisory, no-ops without a
north-star, and keeps a trigger that can actually fire."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ALIGN = ROOT / "skills" / "agrim-align" / "SKILL.md"


def _t():
    return ALIGN.read_text()


def test_skill_exists_with_frontmatter():
    assert ALIGN.exists()
    t = _t()
    assert "name: agrim-align" in t
    assert "description:" in t and "allowed-tools:" in t


def test_is_a_window_check_not_a_per_unit_one():
    """If it re-did the per-unit gates it would be redundant with both of them."""
    t = _t()
    assert "window" in t.lower()
    assert "agrim-plan-review" in t and "agrim-retro" in t
    assert "What this check is not" in t


def test_keeps_both_lenses():
    t = _t().lower()
    assert "cumulative direction" in t                 # dominant theme vs the stated bets
    assert "implicit rewrite" in t                     # effort accumulating behind an undeclared bet


def test_drift_by_omission_is_a_finding():
    """A stated priority getting no work at all is the quiet failure: nothing looks wrong at any
    single point, and the priority simply never happens."""
    assert "omission" in _t().lower()


def test_noop_without_a_north_star():
    t = _t()
    assert ".sdlc/context/north-star.md" in t
    assert "no-op" in t.lower()


def test_advisory_never_edits_the_north_star():
    t = _t()
    assert "Read-only" in t or "read-only" in t
    assert "Never edit the north-star" in t or "never edit" in t.lower()
    assert "user picks" in t.lower() or "user chooses" in t.lower()


def test_refuses_to_manufacture_drift():
    t = _t().lower()
    assert "don't manufacture drift" in t or "manufacture drift" in t
    assert "too short" in t                            # a 2-goal window is noise, not a trajectory


def test_report_carries_the_count_that_drives_the_trigger():
    """The report IS the bookkeeping — status.py reads goals_reviewed to know when the next audit is
    due. Rename it here and the trigger silently stops firing."""
    t = _t()
    assert "goals_reviewed:" in t
    assert ".sdlc/knowledge/align/" in t


def test_status_offers_it_when_due():
    status = (ROOT / "skills" / "agrim-status" / "SKILL.md").read_text()
    assert "/agrim-align" in status
    src = (ROOT / "skills" / "agrim-status" / "scripts" / "status.py").read_text()
    assert "goals_reviewed" in src and "ALIGN_EVERY" in src


def test_no_source_repo_leakage():
    banned = ("docs/context", "Ported from", "OnShot", "onshot", "storytelling",
              "episode", "lipsync", "screenplay", "media-orch", "Temporal", "RunPod")
    t = _t()
    for b in banned:
        assert b not in t, f"agrim-align leaked '{b}'"

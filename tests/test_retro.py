"""agrim-retro: the Retrospective/learning executor. Pins that the skill is well-formed, does its three
things (structural + product reflection, intent-vs-shipped, three-store harvest routed to Sigma's
OWN stores), stays advisory (proposes/parks standing changes), is wired into BOTH orchestrators'
Retrospective phase, and leaks nothing from the source repo it was genericized from."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RETRO = ROOT / "skills" / "agrim-retro" / "SKILL.md"


def _t():
    return RETRO.read_text()


def test_skill_exists_with_frontmatter():
    assert RETRO.exists()
    t = _t()
    assert "name: agrim-retro" in t
    assert "description:" in t and "allowed-tools:" in t


def test_does_the_three_things():
    t = _t().lower()
    assert "structural reflection" in t and "product reflection" in t
    assert "intent-vs-shipped" in t
    assert "achieved" in t and "partial" in t and "diverged" in t     # the intent grade


def test_three_stores_are_sigmas_own():
    t = _t()
    assert ".sdlc/context/north-star.md" in t                          # north-star store
    assert ".sdlc/project.md" in t and "CLAUDE.md" in t                # standing-rule store
    assert ".sdlc/journey" in t or "loop.py" in t                      # audit-trail store


def test_advisory_and_fail_open():
    t = _t().lower()
    assert "advisory" in t and "park" in t                             # proposes; parks standing changes
    assert "never" in t and ("auto-write" in t or "unattended" in t)   # no unattended standing writes
    assert "fail-open" in t


def test_wired_into_both_orchestrators_retrospective_phase():
    for orch in ("agrim-goal", "agrim-loop"):
        t = (ROOT / "skills" / orch / "SKILL.md").read_text()
        assert "agrim-retro" in t, f"{orch} does not run agrim-retro"
        assert "Retrospective" in t, f"{orch} has no Retrospective phase step"


def test_no_source_repo_leakage():
    banned = ("docs/context", "Ported from", "OnShot", "onshot", "storytelling",
              "episode", "lipsync", "screenplay", "media-orch")
    t = _t()
    for b in banned:
        assert b not in t, f"agrim-retro leaked '{b}'"


def test_proposes_standing_doc_retirements_not_just_additions():
    """Docs grow by addition and shrink by nobody: adding a rule has an obvious moment, retiring one
    never does. Retro is that moment — and a demotion is parked for approval like any other standing
    change, never written unattended."""
    t = _t()
    low = t.lower()
    assert "standing-doc rot" in low
    assert "mechanically" in low and "demot" in low      # a rule CI now enforces is redundant prose
    assert "premise moved" in low                        # ...or one the code has outgrown
    assert "superseded" in low and ".sdlc/plans/" in t   # shipped plans weaken the plan gate
    assert "nothing rotted" in low                       # the common answer must be allowed
    assert "archive" in low and "never delete" in low


def test_rot_pass_defers_the_mechanical_half_to_doctor():
    """Split by blast radius: doctor reports references that provably don't resolve and needs no
    approval; retro changes meaning, so it asks."""
    assert "agrim-doctor" in _t()


def test_leaves_a_kg_corpus_note_gated_on_knowledge_graph_enabled():
    """#1050: retro is the going-forward half of the historical KG backfill -- every goal that
    completes with knowledge_graph.enabled should leave one compact note at
    .sdlc/knowledge/analysis/<id>.md, the same gate `agrim-kg`/`agrim-context` already use, so a
    project that never opted in sees zero behavior change."""
    t = _t()
    assert "knowledge_graph.enabled" in t
    assert "knowledge/analysis" in t
    assert "kg.py" in t and " note " in t     # the kg.py `note` subcommand this step drives


def test_kg_note_is_always_written_not_parked_in_autonomous_mode():
    """The Output section's autonomous-mode carve-out already says 'write only the audit-trail
    notes ... park everything else' -- read literally that would license parking the new KG note
    too, breaking #1050's own 'no manual step' Done-when clause. The carve-out must explicitly name
    the KG-corpus note as also always-written."""
    t = _t()
    autonomous = t[t.index("Autonomous"):]
    assert "kg" in autonomous.lower() and "audit-trail" in autonomous


def test_kg_note_step_does_not_trigger_synchronous_graph_rebuild():
    """Out of scope per #1050: auto_refresh already covers the rebuild at end of Retrospective."""
    t = _t()
    assert "auto_refresh" in t

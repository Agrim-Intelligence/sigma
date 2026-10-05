"""Lane routing: the Research phase sizes a goal small/medium/large, and the orchestrators must
actually ROUTE on it. A lane that is computed and then ignored is the defect these pin — the goal
carries a size nobody reads, and a typo fix earns the same seven-phase pass as a schema migration.

The resolver is tested by behavior; the routing itself lives in the orchestrators' phase steps (which
are prose, like every other phase in this kit), so those are pinned structurally."""
import pathlib, importlib.util, tempfile
from skill_corpus import skill_corpus

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "sigma-loop" / "scripts" / "discovery.py"


def _disc():
    spec = importlib.util.spec_from_file_location("discovery", D)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _goal(d, lane_line):
    p = pathlib.Path(d) / "0001-g.md"
    p.write_text(f"---\nstatus: pending\n{lane_line}\n---\nbody\n")
    return str(p)


def test_reads_each_declared_lane():
    with tempfile.TemporaryDirectory() as d:
        for lane in ("small", "medium", "large"):
            assert _disc().lane_of(_goal(d, f"lane: {lane}")) == lane


def test_auto_resolves_to_medium_not_small():
    """`lane: auto` means Research has not measured it yet. Guessing 'small' on an unknown goal skips
    ceremony it might need — the one direction where being wrong is expensive."""
    with tempfile.TemporaryDirectory() as d:
        assert _disc().lane_of(_goal(d, "lane: auto")) == "medium"


def test_unknown_inputs_fail_toward_more_rigour():
    m = _disc()
    with tempfile.TemporaryDirectory() as d:
        assert m.lane_of(_goal(d, "lane: enormous")) == m.DEFAULT_LANE   # typo
        assert m.lane_of(_goal(d, "status: pending")) == m.DEFAULT_LANE  # field absent
    assert m.lane_of("no frontmatter at all") == m.DEFAULT_LANE          # github issue body
    assert m.lane_of("/nonexistent/path/goal.md") == m.DEFAULT_LANE      # unreadable
    assert m.DEFAULT_LANE == "medium"


def test_quoted_and_cased_values_still_resolve():
    """Parity with frontmatter.parse, which strips quotes — a hand-edited `lane: "Small"` must not
    silently fall back to medium."""
    with tempfile.TemporaryDirectory() as d:
        assert _disc().lane_of(_goal(d, 'lane: "Small"')) == "small"
        assert _disc().lane_of(_goal(d, "lane:  LARGE  ")) == "large"


def test_cli_prints_the_lane():
    """The orchestrators shell out exactly as they do for predict.py resolve."""
    with tempfile.TemporaryDirectory() as d:
        import subprocess, sys
        out = subprocess.run([sys.executable, str(D), "lane", _goal(d, "lane: large")],
                             capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.strip() == "large"


def test_both_orchestrators_consume_the_lane():
    """The actual regression guard: Research writing a lane is only half the feature."""
    for orch in ("sigma-goal", "sigma-loop"):
        t = skill_corpus(orch)   # #1611: SKILL.md + references/*.md
        assert "discovery.py" in t and "lane" in t, f"{orch} never resolves the lane"
        for lane in ("small", "large"):
            assert lane in t, f"{orch} has no branch for the {lane} lane"


def test_plan_review_is_never_skipped_by_a_lane():
    """Small goals are exactly where an unreviewed plan ships. If a lane could skip the gate, the
    routing would have removed the one thing this kit exists to enforce."""
    for orch in ("sigma-goal", "sigma-loop"):
        t = (ROOT / "skills" / orch / "SKILL.md").read_text()
        assert "every lane" in t.lower(), f"{orch} does not pin Plan-Review across lanes"


def test_research_points_at_its_consumer():
    """The producer should name who reads it, so the next editor cannot quietly orphan the field."""
    t = (ROOT / "skills" / "sigma-research" / "SKILL.md").read_text()
    assert "discovery.py lane" in t
    assert "not a label" in t


def test_an_issue_number_is_not_a_lane_lookup():
    """github mode passes an issue number; Research records the lane on the issue TIMELINE, which
    this local-files adapter cannot see. It must return the safe default rather than pretend — and
    the orchestrators must say where the lane really comes from in that mode."""
    m = _disc()
    assert m.lane_of("42") == m.DEFAULT_LANE
    for orch in ("sigma-goal", "sigma-loop"):
        t = (ROOT / "skills" / orch / "SKILL.md").read_text()
        assert "github mode" in t and "timeline" in t, \
            f"{orch} does not say where the lane comes from in github mode"


def test_local_resolver_stays_zero_dep():
    """discovery.py is the local-files adapter. Shelling out to `gh` here would duplicate sources.py
    and drag a network dependency into the module the loop calls on every goal."""
    src = D.read_text()
    assert "subprocess" not in src                          # you cannot shell out to gh without it
    assert 'spec_from_file_location("sources"' not in src   # nor load the github adapter directly

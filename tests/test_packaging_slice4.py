import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_plan_review_skill_wellformed():
    t = (ROOT / "skills" / "sigma-plan-review" / "SKILL.md").read_text()
    assert "name: sigma-plan-review" in t
    # generic — no OnShot leakage
    for banned in ("media-orch", "Temporal", "R2", "OnShot", "RunPod"):
        assert banned not in t


def test_plan_review_has_alignment_gate():
    t = (ROOT / "skills" / "sigma-plan-review" / "SKILL.md").read_text()
    assert "north-star.md" in t                     # vision-first alignment gate
    assert "non-goal" in t.lower() and "FIX-FIRST" in t   # contradicting the strategy blocks
    assert "architecture rule" in t.lower()         # ...and violating an architecture rule blocks


def test_goal_skill_wellformed_and_records():
    t = (ROOT / "skills" / "sigma-goal" / "SKILL.md").read_text()
    assert "name: sigma-goal" in t and "allowed-tools:" in t
    assert "loop.py" in t and "record" in t        # records the outcome to .sdlc state


def test_context_skill_gates_on_kg():
    t = (ROOT / "skills" / "sigma-context" / "SKILL.md").read_text()
    assert "name: sigma-context" in t and "allowed-tools:" in t
    assert "kg.py" in t and "status" in t          # gated: only acts when the KG is enabled/built
    assert "graphify query" in t and "--mcp" in t  # push (pre-flight query) + pull (live MCP)


def test_orchestrators_run_context_preflight():
    for skill in ("sigma-loop", "sigma-goal"):
        t = (ROOT / "skills" / skill / "SKILL.md").read_text()
        assert "sigma-context" in t, f"{skill} must run the context pre-flight"


def test_vision_skill_wellformed():
    t = (ROOT / "skills" / "sigma-vision" / "SKILL.md").read_text()
    assert "name: sigma-vision" in t and "allowed-tools:" in t
    assert "--vision" in t                              # scaffolds the north-star via the init flag
    assert "north-star" in t and "non-goals" in t       # the tiers it fills (non-goals feed the gate)
    assert "draft" in t.lower() and "refine" in t.lower()   # drafts from the repo first, user refines (no blank page)


def test_velocity_skill_wellformed():
    t = (ROOT / "skills" / "sigma-velocity" / "SKILL.md").read_text()
    assert "name: sigma-velocity" in t and "allowed-tools:" in t
    assert "velocity.py" in t and "measure" in t and "estimate" in t   # git-throughput sizing


def test_radar_skill_is_dry_run_by_default():
    t = (ROOT / "skills" / "sigma-radar" / "SKILL.md").read_text()
    assert "name: sigma-radar" in t and "radar.py" in t and "agenda" in t
    assert "dry-run" in t.lower()                                       # Phase A writes nothing external
    assert "file nothing to github" in t.lower() or "never file" in t.lower()


def test_doctor_skill_wellformed():
    t = (ROOT / "skills" / "sigma-doctor" / "SKILL.md").read_text()
    assert "name: sigma-doctor" in t and "doctor.py" in t and "check" in t
    assert "never run an interactive login" in t.lower() or "hand them the command" in t.lower()


# Each portable executor must (1) defer to its superpowers companion on Claude via a resolution header,
# and (2) ship a committed parity review vs superpowers asserting >= par. Add a row as each lands.
PORTABLE_EXECUTORS = {
    "sigma-verify": ("verification-before-completion", "verify.md"),
    "sigma-implement": ("test-driven-development", "implement.md"),
    "sigma-plan": ("writing-plans", "plan.md"),
    "sigma-brainstorm": ("brainstorming", "brainstorm.md"),
    "sigma-review": ("requesting-code-review", "review.md"),
}


def test_portable_executors_defer_to_superpowers_and_have_parity():
    for skill, (sp, doc) in PORTABLE_EXECUTORS.items():
        t = (ROOT / "skills" / skill / "SKILL.md").read_text()
        assert f"name: {skill}" in t
        assert f"superpowers:{sp}" in t, f"{skill}: no resolution header deferring to superpowers on Claude"
        parity = (ROOT / "docs" / "executor-parity" / doc).read_text()
        assert skill in parity and "par" in parity.lower(), f"{doc}: missing parity verdict"


def test_versions_aligned():
    p = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    mk = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    # Equal to EACH OTHER, not a hardcoded literal -- what "aligned" actually means, and it never
    # needs touching again on a future version bump the way asserting a specific string does.
    assert p["version"] == mk["plugins"][0]["version"]


def test_plan_review_dispositions_close_the_loop():
    """A FIX-FIRST that sends the plan back with no record of what happened to each finding is half
    a gate. Matters most in /sigma-loop, where no human adjudicates: without this, nothing stops the
    loop from faithfully implementing a review finding that was simply wrong."""
    t = (ROOT / "skills" / "sigma-plan-review" / "SKILL.md").read_text()
    low = t.lower()
    for verdict in ("accept", "reject", "partially accept"):
        assert verdict in low, f"plan-review has no '{verdict}' disposition"
    assert "file:line" in t                              # every verdict is evidence-bound
    assert "the review can also be wrong" in low         # findings are hypotheses too
    assert "regen" in low and "half" in low              # mostly-substantive findings => regenerate
    assert "structural" in low and "patchwork" in low


def test_sdlc_review_names_every_axis():
    """The review skill must name all eight axes. An axis nobody names is one the reviewer never
    looks for — which is exactly how performance, observability and readability went unreviewed."""
    t = (ROOT / "skills" / "sigma-review" / "SKILL.md").read_text()
    for axis in ("Correctness & Intent", "Concurrency & State", "Performance & Resources",
                 "Security", "Observability", "Structure & Maintainability",
                 "Intelligibility", "Test Adequacy"):
        assert axis in t, f"sigma-review does not name the {axis} axis"
    low = t.lower()
    # the probes that distinguish a real axis from a heading
    assert "n+1" in low and "timeout" in low                   # performance & resources have teeth
    assert "would it fail if the code were wrong" in low       # test QUALITY, not test presence
    assert "feature envy" in low or "shotgun surgery" in low   # named smells, not just "structure"


def test_sdlc_review_filters_findings_before_reporting():
    """Recall without precision costs real cycles: a blocking finding is a fix -> push -> re-review
    round, and work.max_review_cycles parks the goal after three. The skill must say what never gets
    reported, and what earns a block."""
    t = (ROOT / "skills" / "sigma-review" / "SKILL.md").read_text()
    low = t.lower()
    # a finding is a claim in prose — the project's own Standing Rule 1, applied to the reviewer
    assert "failing case" in low
    assert "claim in prose is not evidence" in low
    # the false-positive classes that cost a cycle and buy nothing
    assert "pre-date" in low or "pre-dates" in low     # issues older than the diff
    assert "linter" in low and "type-checker" in low   # gates that run separately
    # ...but the pre-existing carve-out must not cancel the blast-radius discipline this skill opens
    # with: a latent bug the diff just made reachable IS this review's finding.
    assert "live path" in low
    # the blocking gate, tied to the machinery that already exists
    assert "sdlc:followup" in t
    assert "max_review_cycles" in t


def test_implement_and_review_share_one_vocabulary():
    """The maker and the checker must name the same faults. If sigma-implement says "tidy up" while
    sigma-review looks for Feature Envy, the author cleans one thing and the reviewer flags another.

    Axis 8 is deliberately NOT asserted here: RED's "watch it fail for the right reason" already
    guarantees it, and the anti-patterns cover mock-testing. Adding it would duplicate TDD itself."""
    impl = (ROOT / "skills" / "sigma-implement" / "SKILL.md").read_text()
    review = (ROOT / "skills" / "sigma-review" / "SKILL.md").read_text()
    # axis 6 — the named smells, shared verbatim so the two cannot drift. Each must be greppable in
    # BOTH files: a term line-wrapped in either one is invisible to this guard.
    for smell in ("Long Method", "Feature Envy", "Duplicate Code", "Speculative Generality",
                  "Middle Man"):
        assert smell in impl, f"sigma-implement does not name {smell}"
        assert smell in review, f"sigma-review no longer names {smell} — the two have drifted"
    # axis 7 — readability, and the doc the diff just made false. Distinctive phrases, not bare words:
    # asserting "why" or "name" would pass on almost any prose and guard nothing.
    assert "say WHY, not WHAT" in impl
    assert "now false" in impl.lower()


def test_review_skills_resolve_independence_rather_than_assert_it():
    """#1983: 'you did not write this code' is FALSE whenever the author runs the skill, and an
    unenforceable assertion is what sigma-review's own evidence rule bans."""
    for skill in ("sigma-review", "sigma-plan-review"):
        t = (ROOT / "skills" / skill / "SKILL.md").read_text()
        assert "you did not write this" not in t.lower(), \
            f"{skill} still ASSERTS independence it cannot guarantee"
        assert "reviewer.py" in t, f"{skill} does not resolve its mechanism"
        assert "branch on `mechanism`" in t, f"{skill} does not branch on the resolver's answer"
        # anchored as backticked literals: the bare words occur in ordinary prose in both files
        for mech in ("`subagent`", "`process`", "`command`", "`inline`"):
            assert mech in t, f"{skill} does not name the {mech} mechanism"


def test_both_review_skills_require_a_provenance_line():
    """Plan-review gained the resolve block but originally never stamped provenance, so a
    subagent/process plan-review emitted a verdict nothing could attribute."""
    for skill in ("sigma-review", "sigma-plan-review"):
        t = (ROOT / "skills" / skill / "SKILL.md").read_text()
        assert "Reviewed by:" in t, f"{skill} does not require a provenance line"


def test_review_skills_do_not_claim_check_detects_implementation_content():
    """`check` deliberately does NOT flag implementation content -- a reviewer must see the diff.
    The prose said it did, which is an unenforced claim inside the two skills whose whole job in
    this goal is to stop making them."""
    for skill in ("sigma-review", "sigma-plan-review"):
        t = (ROOT / "skills" / skill / "SKILL.md").read_text()
        assert "non-zero if implementation content" not in t, \
            f"{skill} claims check detects implementation content; it provably does not"
        assert "--scratch" in t, f"{skill} documents a check invocation that can never fail"


def test_loop_does_not_call_a_non_subagent_host_a_degradation():
    """Host-agnostic means each host reaches the same quality by its own route. Calling the
    non-subagent path a 'degradation' is the Claude-only-good-path defect AGENTS.md forbids."""
    t = (ROOT / "skills" / "sigma-loop" / "SKILL.md").read_text()
    assert "degrade honestly" not in t.lower(), \
        "sigma-loop still frames a non-subagent host as a degradation"
    assert "reviewer.py" in t, "sigma-loop does not point at the resolver"


def test_every_gate_that_builds_a_reviewer_brief_also_resolves_its_mechanism():
    """#2008: #1983 fixed two gates and left three asserting independence in prose.

    The invariant is not "mentions a subagent" — it is that any skill which ASSEMBLES a reviewer
    brief must also resolve HOW that reviewer is spawned. A gate that builds the brief and then
    describes a "fresh, author-blind subagent" has, on a host with no subagents, asserted exactly
    what #1983 removed. Written as an invariant rather than a list so a SIXTH gate cannot be added
    with the defect."""
    missing_resolver, still_asserting = [], []
    for skill_md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        t = skill_md.read_text()
        if "review_context.py" in t and "brief" in t and "reviewer.py" not in t:
            missing_resolver.append(skill_md.parent.name)
        # Per-OCCURRENCE, not per-file: sigma-loop already names reviewer.py at ONE gate, which a
        # file-level check treats as covering all of them. The bare phrase IS the assertion, so its
        # presence anywhere is the defect regardless of what the rest of the file says.
        for phrase in ("author-blind subagent", "author-blind reviewer subagent"):
            if phrase in t:
                still_asserting.append("%s (%r)" % (skill_md.parent.name, phrase))
    assert not missing_resolver, (
        "these skills build a reviewer brief but never resolve the mechanism that reads it: "
        + ", ".join(missing_resolver))
    assert not still_asserting, (
        "these still ASSERT an author-blind subagent instead of resolving one: "
        + ", ".join(still_asserting))

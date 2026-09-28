import pathlib, importlib.util, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _disc():
    spec = importlib.util.spec_from_file_location("discovery", S / "discovery.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _goal(d, n, status):
    (pathlib.Path(d) / f"{n}.md").write_text(f"---\nid: {n}\nstatus: {status}\n---\nx\n")


def test_returns_first_pending_in_filename_order():
    with tempfile.TemporaryDirectory() as d:
        _goal(d, "0002", "pending"); _goal(d, "0001", "pending")
        assert _disc().next_pending(d).endswith("0001.md")


def test_skips_done_and_parked():
    with tempfile.TemporaryDirectory() as d:
        _goal(d, "0001", "done"); _goal(d, "0002", "parked"); _goal(d, "0003", "pending")
        assert _disc().next_pending(d).endswith("0003.md")


def test_none_when_all_terminal():
    with tempfile.TemporaryDirectory() as d:
        _goal(d, "0001", "done"); _goal(d, "0002", "parked")
        assert _disc().next_pending(d) is None


def test_ignores_frontmatterless_files():
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / "README.md").write_text("# Goals\nno frontmatter\n")
        _goal(d, "0001", "pending")
        assert _disc().next_pending(d).endswith("0001.md")


def test_skip_passes_over_a_leased_goal():
    with tempfile.TemporaryDirectory() as d:
        _goal(d, "0001", "pending"); _goal(d, "0002", "pending")
        first = str(pathlib.Path(d) / "0001.md")
        assert _disc().next_pending(d, skip={first}).endswith("0002.md")   # 0001 leased -> next is 0002


def test_skips_blank_status():
    # F32: `status:` present but empty (or whitespace-only) must not be treated as runnable.
    with tempfile.TemporaryDirectory() as d:
        _goal(d, "0001", ""); _goal(d, "0002", "   "); _goal(d, "0003", "pending")
        assert _disc().next_pending(d).endswith("0003.md")


# --- #698: priority ordering (local goals) -------------------------------------------------------
# `priority:P0`..`P4` labels/frontmatter were written everywhere and read by nothing. The queue
# sorted by filename alone, so a P0 filed later ran behind every older P2.

def _pgoal(d, n, status="pending", priority=None):
    pri = f"priority: {priority}\n" if priority is not None else ""
    (pathlib.Path(d) / f"{n}.md").write_text(f"---\nid: {n}\nstatus: {status}\n{pri}---\nx\n")


def test_priority_beats_filename_order():
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0001", priority="P2"); _pgoal(d, "0002", priority="P0")
        assert _disc().next_pending(d).endswith("0002.md")


def test_next_pending_resolves_aliases_when_configured():
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0001", priority="Low"); _pgoal(d, "0002", priority="Critical")
        aliases = {"critical": "P0", "low": "P3"}
        assert _disc().next_pending(d, aliases=aliases).endswith("0002.md")


def test_next_pending_unset_aliases_leaves_english_priorities_unranked():
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0001", priority="Critical"); _pgoal(d, "0002")   # both effectively unprioritised
        assert _disc().next_pending(d).endswith("0001.md")           # falls back to filename order


def test_unprioritised_sorts_after_every_priority():
    """The compatibility hinge: absent priority goes LAST, so a backlog where NOTHING carries a
    priority collapses to one bucket and the sort falls straight back to filename order."""
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0001")                      # no priority key at all
        _pgoal(d, "0002", priority="P4")
        assert _disc().next_pending(d).endswith("0002.md")


def test_no_priorities_anywhere_is_identical_to_filename_order():
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0003"); _pgoal(d, "0001"); _pgoal(d, "0002")
        assert _disc().next_pending(d).endswith("0001.md")


def test_ties_within_a_priority_fall_back_to_filename_order():
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0009", priority="P1"); _pgoal(d, "0004", priority="P1")
        assert _disc().next_pending(d).endswith("0004.md")


def test_malformed_priority_never_raises_and_sorts_last():
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0001", priority="urgent!"); _pgoal(d, "0002", priority="P3")
        assert _disc().next_pending(d).endswith("0002.md")


def test_priority_value_is_case_insensitive_and_tolerates_the_label_prefix():
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0001", priority="P2"); _pgoal(d, "0002", priority="priority:p0")
        assert _disc().next_pending(d).endswith("0002.md")


def test_order_created_restores_strict_filename_order():
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0001", priority="P4"); _pgoal(d, "0002", priority="P0")
        assert _disc().next_pending(d, order="created").endswith("0001.md")


def test_priority_never_promotes_a_terminal_or_skipped_goal():
    """Ordering must not widen eligibility: a done/parked/proposed goal stays unpickable however
    urgent its priority claims to be."""
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0001", status="done", priority="P0")
        _pgoal(d, "0002", status="parked", priority="P0")
        _pgoal(d, "0003", status="proposed", priority="P0")
        _pgoal(d, "0004", status="pending", priority="P4")
        assert _disc().next_pending(d).endswith("0004.md")


def test_skip_still_applies_under_priority_order():
    with tempfile.TemporaryDirectory() as d:
        _pgoal(d, "0001", priority="P0"); _pgoal(d, "0002", priority="P1")
        first = str(pathlib.Path(d) / "0001.md")
        assert _disc().next_pending(d, skip={first}).endswith("0002.md")


def test_priority_rank_orders_p0_through_p4_then_unknown():
    r = _disc().priority_rank
    assert [r(v) for v in ("P0", "P1", "P2", "P3", "P4")] == [0, 1, 2, 3, 4]
    assert r("P0") < r("P4") < r(None) and r(None) == r("") == r("nonsense")


def test_priority_rank_unset_aliases_is_byte_identical_to_before():
    r = _disc().priority_rank
    assert r("Critical") == _disc().UNPRIORITISED
    assert r("Critical", None) == _disc().UNPRIORITISED
    assert r("Critical", {}) == _disc().UNPRIORITISED


def test_priority_rank_resolves_a_configured_alias():
    d = _disc()
    aliases = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}
    assert d.priority_rank("Critical", aliases) == 0
    assert d.priority_rank("high", aliases) == 1
    assert d.priority_rank("Medium", aliases) == 2
    assert d.priority_rank("LOW", aliases) == 3


def test_priority_rank_alias_lookup_is_case_insensitive_on_both_sides():
    d = _disc()
    assert d.priority_rank("CRITICAL", {"critical": "P0"}) == 0
    assert d.priority_rank("critical", {"CRITICAL": "p0"}) == 0


def test_priority_rank_alias_tolerates_the_label_prefix():
    d = _disc()
    assert d.priority_rank("priority:critical", {"critical": "P0"}) == 0


def test_priority_rank_unmapped_value_stays_unprioritised_even_with_aliases_configured():
    d = _disc()
    aliases = {"critical": "P0"}
    assert d.priority_rank("nonsense", aliases) == d.UNPRIORITISED
    assert d.priority_rank("High", aliases) == d.UNPRIORITISED   # not in this particular map


def test_priority_rank_p0_through_p4_unaffected_by_aliases_being_configured():
    d = _disc()
    aliases = {"critical": "P0"}
    assert [d.priority_rank(v, aliases) for v in ("P0", "P1", "P2", "P3", "P4")] == [0, 1, 2, 3, 4]


def test_priority_rank_a_literal_tier_always_wins_even_if_an_alias_key_collides_with_it():
    """A misconfigured alias map (a stray key that's itself a real P0-P4 spelling, e.g. copy-pasted
    from an example and only the value edited) must never let an alias remap what a LITERAL P0-P4
    value means -- that would silently change the meaning of every already-correct label/field
    repo-wide, a far bigger blast radius than the bug aliases exist to fix. The literal match always
    wins; the alias table is consulted only when the literal check fails."""
    d = _disc()
    assert d.priority_rank("P0", {"P0": "P4"}) == 0
    assert d.priority_rank("p2", {"P2": "P0", "high": "P1"}) == 2


# --- #900: blocker priority promotion (opt-in) ----------------------------------------------------
# A blocker's own priority never reflected how urgently something ELSE needed it unblocked: if P0
# issue A is blocked by P2 issue B, B stays P2 forever, even though B is the actual critical path.
# `blocker_promotion_rank` is the one-level ranking rule (`discovery.blocker_promotion.mode`):
# given a blocker's own rank and its directly-blocked dependents, what should the blocker's rank
# become? Walking a multi-level blocking graph and writing the result back out is a different
# slice's job (sources.py) -- this only covers the pure ranking rule, one edge at a time.

def test_blocker_promotion_off_is_a_true_no_op_for_every_rank_and_dependent_combination():
    r = _disc().blocker_promotion_rank
    for own_rank in (0, 1, 2, 3, 4, _disc().UNPRIORITISED):
        assert r(own_rank, [], mode="off") == own_rank
        assert r(own_rank, [(0, False)], mode="off") == own_rank
        assert r(own_rank, [(0, True), (4, False)], mode="off") == own_rank


def test_blocker_promotion_always_mode_promotes_with_a_single_dependent():
    assert _disc().blocker_promotion_rank(2, [(0, False)], mode="always") == 0


def test_blocker_promotion_always_mode_ignores_the_pickability_flag():
    # "always" promotes unconditionally -- has_other_unblocked_work is a "smart"-only signal, so it
    # must not suppress promotion here even though it would under "smart".
    assert _disc().blocker_promotion_rank(2, [(0, True)], mode="always") == 0


def test_blocker_promotion_always_mode_picks_the_minimum_across_multiple_dependents():
    r = _disc().blocker_promotion_rank
    assert r(3, [(2, False), (0, False), (4, False)], mode="always") == 0
    assert r(3, [(4, False), (2, False)], mode="always") == 2   # only the rank-2 dependent is more
                                                                 # urgent than own_rank=3; rank 4 isn't


def test_blocker_promotion_smart_mode_does_not_promote_when_the_sole_urgent_dependent_has_other_work():
    assert _disc().blocker_promotion_rank(2, [(0, True)], mode="smart") == 2


def test_blocker_promotion_smart_mode_promotes_when_the_dependent_has_no_other_work():
    assert _disc().blocker_promotion_rank(2, [(0, False)], mode="smart") == 0


def test_blocker_promotion_smart_mode_excludes_ineligible_dependents_entirely():
    """A dependent with other pickable work at its own tier is dropped from consideration ENTIRELY,
    not merely capped -- so a more-urgent-but-ineligible dependent (rank 0) must not pull the result
    even partway; only the eligible dependent (rank 1) may promote, landing on 1, not 0."""
    assert _disc().blocker_promotion_rank(3, [(0, True), (1, False), (2, True)], mode="smart") == 1


def test_blocker_promotion_own_rank_unchanged_when_no_dependent_is_more_urgent():
    r = _disc().blocker_promotion_rank
    assert r(1, [(2, False), (3, False), (4, False)], mode="always") == 1
    assert r(1, [(2, False), (3, False)], mode="smart") == 1


def test_blocker_promotion_unrecognized_mode_behaves_like_off():
    r = _disc().blocker_promotion_rank
    assert r(2, [(0, False)], mode="urgent!") == 2
    assert r(2, [(0, False)], mode=None) == 2
    assert r(2, [(0, False)], mode="") == 2


def test_blocker_promotion_defaults_to_off_with_no_dependents():
    # Defensive defaults: an omitted mode/dependents call is the safe no-op, matching this module's
    # existing conservative-default posture (e.g. `aliases=None` on `priority_rank`).
    assert _disc().blocker_promotion_rank(3) == 3


def test_blocker_promotion_composes_across_a_manual_transitive_chain():
    """Walking a blocking graph is explicitly NOT this function's job -- but a caller chaining two
    calls (feeding one level's promoted rank in as the next level's own_rank) must propagate
    correctly: A(rank 0) blocked by B(rank 3) blocked by C(rank 4) should promote both B and C to 0,
    proving the one-level rule composes into the transitive behavior the larger goal needs."""
    r = _disc().blocker_promotion_rank
    b_promoted = r(3, [(0, False)], mode="always")             # B directly blocks A (rank 0)
    assert b_promoted == 0
    c_promoted = r(4, [(b_promoted, False)], mode="always")    # C directly blocks B (now rank 0)
    assert c_promoted == 0

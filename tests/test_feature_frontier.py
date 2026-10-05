"""feature_frontier.py (#2265, D-1/D-10/BR-3/BR-4/BR-35 of `.sdlc/design/2253.md`): the unit-scoped
DAG built as a VIEW over edges and algorithms that already exist -- `slices.py`'s own `_cycles` /
`frontier` / `fan_out`, fed by `mirror.py`'s already-persisted `blocker_refs`, with membership
resolved through a live, full-body REST read (never the mirror's 500-character `body_excerpt`).

Every test here reaches GitHub only through an injectable `run` (the live open-issues fetch) and a
hand-written local mirror file (`.sdlc/state/board-mirror.ndjson`) -- no network, no `gh`, hermetic
and deterministic, same posture as `tests/test_mirror.py`/`tests/test_sources.py`."""
import importlib.util, json, pathlib, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _loop():
    spec = importlib.util.spec_from_file_location("loop", S / "loop.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


_CFG = {"discovery": {"source": "github", "github": {"repo": "o/r"}}}


def _open_issue(number, unit=None, title=None, extra_body=""):
    """A live REST issue payload -- FULL body, exactly the shape `sources.fetch_issues_rest`
    returns (title, body, labels, number). `unit=None` declares no unit at all."""
    body = extra_body
    if unit:
        body += f"\nFeature: {unit}\n"
    return {"number": number, "title": title or f"issue {number}", "body": body,
            "labels": [{"name": "sdlc:goal"}]}


def _issues_runner(issues):
    """Fake `gh`: the ONE open, goal-labelled REST fetch `compute()` makes, full body always."""
    def run(args):
        return json.dumps(issues)
    return run


def _mirror_rec(number, state="open", blocker_refs=(), body_excerpt="", title=None):
    """One board-mirror record, hand-built to the exact shape `mirror.normalize_issue` writes --
    `blocker_refs` as `{"phrase","ref"}` pairs, the shape `blocker_scan.read_refs` reads back."""
    return {"number": number, "title": title or f"issue {number}", "body_excerpt": body_excerpt,
            "blocker_refs": [{"phrase": "blocked by", "ref": str(r)} for r in blocker_refs],
            "labels": [], "state": state, "closed_at": None, "updated_at": "", "content_hash": "x"}


def _write_mirror(sdlc_dir, records, now=1000.0):
    m = _mod("mirror")
    state_dir = pathlib.Path(sdlc_dir) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / m.MIRROR_REL.split("/")[-1]).write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")
    (state_dir / m.META_REL.split("/")[-1]).write_text(
        json.dumps({"schema": m.SCHEMA, "repo": "o/r", "count": len(records), "mirrored_at": now}),
        encoding="utf-8")


def _open_unit(sdlc_dir, name, **entry):
    fr = _mod("feature_registry")
    fr.write_unit(fr.registry_dir(sdlc_dir), name, entry)


def _sdlc(d):
    base = pathlib.Path(d) / ".sdlc"
    (base / "state").mkdir(parents=True)
    return str(base)


# --- degrade cases: refuse loudly, never a silently-empty result -----------------------------

def test_not_github_mode_degrades_loudly():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        result = ff.compute(base, "alpha", config={"discovery": {"source": "local-goals"}})
        assert result["degraded"] == ff.NOT_GITHUB
        assert result["ready"] == [] and result["reason"]


def test_unknown_unit_degrades_loudly():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        result = ff.compute(base, "no-such-unit", config=_CFG)
        assert result["degraded"] == ff.UNKNOWN_UNIT


def test_closed_unit_degrades_as_unknown():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha", open=False)
        result = ff.compute(base, "alpha", config=_CFG)
        assert result["degraded"] == ff.UNKNOWN_UNIT


# --- 1. a simple linear chain resolves a correct frontier -------------------------------------

def test_linear_chain_ready_set_is_the_root_with_no_blockers():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _write_mirror(base, [_mirror_rec(10, blocker_refs=()),
                              _mirror_rec(11, blocker_refs=(10,))])
        issues = [_open_issue(10, unit="alpha"), _open_issue(11, unit="alpha")]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert [r["id"] for r in result["ready"]] == ["10"]
        blocked = {b["id"]: b for b in result["blocked"]}
        assert blocked["11"]["waiting_on"] == ["10"]
        assert blocked["11"]["unresolved"] == []
        assert result["members"] == [10, 11]


# --- 2. a cycle among members is detected and reported, never silently accepted ---------------

def test_cycle_among_members_is_detected_and_neither_side_is_ready():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _write_mirror(base, [_mirror_rec(20, blocker_refs=(21,)),
                              _mirror_rec(21, blocker_refs=(20,))])
        issues = [_open_issue(20, unit="alpha"), _open_issue(21, unit="alpha")]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert result["ready"] == []
        assert result["cycles"] and {"20", "21"} <= set(result["cycles"][0])


# --- 3. a phantom edge is never invented: this module only ever reads the ALREADY-narrowed,
#        already-persisted blocker_refs field -- it never re-scans a body for a ref itself -------

def test_phantom_edge_never_invented_because_only_the_persisted_narrow_refs_are_read():
    """`blocker_scan.extract_refs` (EXPLICIT_TRIGGERS only) never wrote a ref for a weak trigger
    like "waiting on #999" -- so the mirror record this test hands in has an EMPTY `blocker_refs`,
    exactly as the real extractor would produce, even though the body text quoted in
    `body_excerpt` below reads as a blocker to a human. If this module re-derived edges from body
    text itself (the bug this test exists to catch), #30 would show up blocked on #999; instead it
    must be ready, because the only edges it may ever see are the ones already on the record."""
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _write_mirror(base, [_mirror_rec(30, blocker_refs=(),
                                          body_excerpt="we should ship this waiting on #999 soon")])
        issues = [_open_issue(30, unit="alpha")]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert [r["id"] for r in result["ready"]] == ["30"]


# --- 4. an edge to a CLOSED issue is resolved/no-longer-blocking ------------------------------

def test_edge_to_a_closed_external_issue_is_treated_as_resolved():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        # #31 is CLOSED and not itself a member (declares no unit) -- exactly the shape of the
        # four already-merged, already-closed slices #2261-#2264 that #2265 itself is blocked by.
        _write_mirror(base, [_mirror_rec(30, blocker_refs=(31,)),
                              _mirror_rec(31, state="closed", blocker_refs=())])
        issues = [_open_issue(30, unit="alpha")]           # #31 never appears in the open fetch
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert [r["id"] for r in result["ready"]] == ["30"]
        assert result["blocked"] == []


# --- 5. an edge to a NONEXISTENT (or simply unresolvable) issue fails CLOSED ------------------

def test_edge_to_an_unresolvable_ref_fails_closed_and_is_reported():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _write_mirror(base, [_mirror_rec(40, blocker_refs=(9999,))])   # 9999 never in the mirror
        issues = [_open_issue(40, unit="alpha")]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert result["ready"] == []
        blocked = {b["id"]: b for b in result["blocked"]}
        assert blocked["40"]["unresolved"] == ["9999"]
        assert blocked["40"]["waiting_on"] == []


def test_edge_to_a_still_open_external_issue_is_an_ordinary_block_not_unresolved():
    """The middle case: found in the mirror, genuinely still open -- this is a correct, expected
    block, and must NOT be reported as `unresolved` (that tag is reserved for what this module
    cannot determine at all, per BR-4)."""
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _write_mirror(base, [_mirror_rec(50, blocker_refs=(51,)),
                              _mirror_rec(51, state="open", blocker_refs=())])
        issues = [_open_issue(50, unit="alpha")]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        blocked = {b["id"]: b for b in result["blocked"]}
        assert blocked["50"]["waiting_on"] == ["51"]
        assert blocked["50"]["unresolved"] == []


# --- 6. D-10: membership survives a 500-char body_excerpt truncation the mirror would have missed

def test_membership_survives_the_500_char_excerpt_truncation_d10():
    ff = _mod("feature_frontier")
    features = _mod("features")
    full_body = ("x" * 600) + "\nFeature: alpha\n"          # marker lands well past the 500 cap
    truncated_excerpt = full_body[:500]

    # the contrast this test exists to prove: the MIRROR's own excerpt genuinely cannot see the
    # declaration, while the full live body genuinely can -- if this were false the test below
    # would be proving nothing.
    assert features.parse_body(truncated_excerpt) is None
    assert features.parse_body(full_body) == "alpha"

    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        # the mirror record's own body_excerpt is the truncated, marker-less one, exactly as the
        # real mirror would have stored it -- and it is NEVER read for membership by this module.
        _write_mirror(base, [_mirror_rec(60, blocker_refs=(), body_excerpt=truncated_excerpt)])
        issues = [{"number": 60, "title": "long issue", "body": full_body,
                   "labels": [{"name": "sdlc:goal"}]}]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert 60 in result["members"]
        assert [r["id"] for r in result["ready"]] == ["60"]


def test_members_filtering_never_reads_the_mirrors_body_excerpt_at_all():
    """Same property, isolated to `members()` directly: a live payload with a short, marker-less
    body excerpt equivalent must still be recognised when the FULL body (handed to `members`)
    carries the marker -- `members()` takes live payloads only, no mirror argument exists for it
    to consult even by mistake."""
    ff = _mod("feature_frontier")
    long_issue = _open_issue(61, unit="alpha", extra_body="y" * 600)
    out, ambiguous = ff.members([long_issue], "alpha")
    assert [i["number"] for i in out] == [61]
    assert ambiguous == []


# --- 7. recomputed fresh at every call: a new item slots in for free, no cache to clear --------

def test_recomputed_fresh_a_new_p0_appears_on_the_very_next_call():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _write_mirror(base, [_mirror_rec(70, blocker_refs=())])
        first = ff.compute(base, "alpha", config=_CFG,
                            run=_issues_runner([_open_issue(70, unit="alpha")]))
        assert first["members"] == [70]

        # a second, independent item lands -- both on the mirror and in the live fetch -- with NO
        # cache-clear, no --force, nothing passed to `compute()` beyond the new data itself.
        _write_mirror(base, [_mirror_rec(70, blocker_refs=()), _mirror_rec(71, blocker_refs=())])
        second = ff.compute(base, "alpha", config=_CFG,
                             run=_issues_runner([_open_issue(70, unit="alpha"),
                                                  _open_issue(71, unit="alpha")]))
        assert second["members"] == [70, 71]
        assert {r["id"] for r in second["ready"]} == {"70", "71"}


# --- 8. the loop.py CLI verb is wired up, read-only, and correct -------------------------------

def test_loop_py_feature_frontier_verb_is_wired_and_read_only(monkeypatch, capsys):
    """Wiring only -- argv parsing, config load, print, exit code, and that the verb never claims
    a goal (`loop.py`'s only shared mutation surface a read-only verb could accidentally touch).
    `feature_frontier.compute`'s own correctness is exercised directly by every test above; this
    test stubs it out (the same `monkeypatch.setattr(lp, "_load", ...)` technique test_loop.py's
    own `auto_unpark`/`blockers` fakes already use) because the real CLI path has no argv flag to
    inject a fake `run` through, unlike a direct `compute()` call."""
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        (pathlib.Path(base) / "config.json").write_text(json.dumps(_CFG))
        calls = []

        class FakeFeatureFrontier:
            NOT_GITHUB = "not-github-mode"
            UNKNOWN_UNIT = "unknown-or-closed-unit"

            def compute(self, sdlc_dir, unit, config=None):
                calls.append((sdlc_dir, unit, config))
                return {"unit": unit, "degraded": None, "reason": None, "members": [80],
                        "ready": [{"id": "80", "title": "x", "fan_out": 0}], "blocked": [],
                        "cycles": [], "ambiguous": [], "no_mirror_record": [],
                        "mirror_age_seconds": 5.0}

            def render(self, result):
                return f"rendered {result['unit']} ready={[r['id'] for r in result['ready']]}\n"

        real_load = lp._load
        monkeypatch.setattr(
            lp, "_load",
            lambda name: FakeFeatureFrontier() if name == "feature_frontier" else real_load(name))

        rc = lp.main(["loop.py", "feature-frontier", base, "alpha"])
        out = capsys.readouterr().out
        assert rc == 0
        assert calls == [(base, "alpha", _CFG)]
        assert "rendered alpha ready=['80']" in out

        # read-only: no ledger entry of any kind was written by a verb this repo documents as
        # never claiming a goal (mirrors test_a_prework_precheck_does_NOT_claim_the_goal).
        assert lp.ledger.read_all(base) == []


def test_loop_py_feature_frontier_verb_reports_degraded_exit_code(monkeypatch, capsys):
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        (pathlib.Path(base) / "config.json").write_text(json.dumps(_CFG))

        class DegradedFeatureFrontier:
            def compute(self, sdlc_dir, unit, config=None):
                return {"unit": unit, "degraded": "unknown-or-closed-unit",
                        "reason": "no such unit", "members": [], "ready": [], "blocked": [],
                        "cycles": [], "ambiguous": [], "no_mirror_record": [],
                        "mirror_age_seconds": None}

            def render(self, result):
                return result["reason"] + "\n"

        real_load = lp._load
        monkeypatch.setattr(
            lp, "_load",
            lambda name: DegradedFeatureFrontier() if name == "feature_frontier" else real_load(name))
        rc = lp.main(["loop.py", "feature-frontier", base, "ghost"])
        assert rc == 1
        assert "no such unit" in capsys.readouterr().out


# --- 9. ordering: widest fan-out first, ties broken by ascending issue number ------------------

def test_ready_set_ordered_by_transitive_fan_out_then_issue_number():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        # root A (#80) unblocks B, C directly and D transitively (via B) -- fan_out(A) == 3.
        # root E (#84) unblocks only F -- fan_out(E) == 1. Both roots are ready.
        _write_mirror(base, [
            _mirror_rec(80, blocker_refs=()),                      # A
            _mirror_rec(81, blocker_refs=(80,)),                   # B needs A
            _mirror_rec(82, blocker_refs=(80,)),                   # C needs A
            _mirror_rec(83, blocker_refs=(81,)),                   # D needs B (transitively needs A)
            _mirror_rec(84, blocker_refs=()),                      # E
            _mirror_rec(85, blocker_refs=(84,)),                   # F needs E
        ])
        issues = [_open_issue(n, unit="alpha") for n in (80, 81, 82, 83, 84, 85)]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert [r["id"] for r in result["ready"]] == ["80", "84"]   # widest fan-out (3) before (1)


def test_ready_set_ties_break_by_ascending_issue_number():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _write_mirror(base, [_mirror_rec(92, blocker_refs=()), _mirror_rec(91, blocker_refs=())])
        issues = [_open_issue(92, unit="alpha"), _open_issue(91, unit="alpha")]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert [r["id"] for r in result["ready"]] == ["91", "92"]   # both fan_out 0 -- oldest first


# --- extra: no mirror record for a member at all is its own fail-closed case -------------------

def test_member_with_no_mirror_record_at_all_fails_closed_and_is_reported():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _write_mirror(base, [])                               # the mirror knows nothing at all
        issues = [_open_issue(90, unit="alpha")]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert result["ready"] == []
        assert result["no_mirror_record"] == ["90"]


# --- extra: a self-contradicting declaration is excluded from membership, not silently kept ----

def test_ambiguous_declaration_is_excluded_and_reported_not_silently_dropped():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        rival = {"number": 95, "title": "rival", "body": "Feature: alpha\nFeature: beta\n",
                 "labels": [{"name": "sdlc:goal"}]}
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner([rival]))
        assert result["members"] == []
        assert result["ambiguous"] == [95]


# --- extra: an issue declaring a DIFFERENT unit is never swept in as a member -------------------

def test_issue_declaring_a_different_unit_is_not_a_member():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _open_unit(base, "beta")
        _write_mirror(base, [_mirror_rec(96, blocker_refs=())])
        issues = [_open_issue(96, unit="beta")]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert result["members"] == [] and result["ready"] == []


# --- extra: title is scrubbed before it ever reaches the report (SAFETY, same posture as mirror.py)

def test_title_is_scrubbed_before_it_reaches_the_report():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _write_mirror(base, [_mirror_rec(97, blocker_refs=())])
        issue = _open_issue(97, unit="alpha", title="password: hunter2xyz please fix")
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner([issue]))
        assert "hunter2xyz" not in result["ready"][0]["title"]


def test_render_never_raises_on_a_degraded_result():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        text = ff.render(ff.compute(base, "alpha", config={"discovery": {"source": "local-goals"}}))
        assert "not GitHub mode" in text


# --- #2283: a cross-feature dependency cycle is undetected -- a silent, permanent deadlock ------
# `compute()`'s own `slice_list` (the only graph the ORIGINAL `slices._cycles(slice_list)` call
# ever walks) is built exclusively from this unit's declared members, so an edge that leaves and
# re-enters the unit is structurally invisible to it. `_cross_feature_cycles` reuses
# `slices._cycles` unmodified over a WIDER graph built from every open record the local mirror
# already holds, filtered to cycles that touch this unit but are not fully contained in it.


def test_two_feature_cycle_is_detected_from_both_sides():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        _open_unit(base, "payments")
        _write_mirror(base, [_mirror_rec(50, blocker_refs=(60,)),
                              _mirror_rec(60, blocker_refs=(50,))])
        issues = [_open_issue(50, unit="checkout"), _open_issue(60, unit="payments")]
        run = _issues_runner(issues)
        r_checkout = ff.compute(base, "checkout", config=_CFG, run=run)
        r_payments = ff.compute(base, "payments", config=_CFG, run=run)
        assert r_checkout["cross_feature_cycles"], "checkout must see the cross-feature cycle"
        assert r_payments["cross_feature_cycles"], "payments must see it too"
        assert set(r_checkout["cross_feature_cycles"][0]) == {"50", "60"}
        assert set(r_payments["cross_feature_cycles"][0]) == {"50", "60"}


def test_cross_feature_cycle_is_not_double_reported_in_the_intra_unit_cycles_field():
    """The #50/#60 cycle is never fully inside one unit's own members, so the ORIGINAL `cycles`
    field (computed independently, over `slice_list` alone) correctly stays empty either way --
    this pins that the two fields answer different questions, not that one implies the other."""
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        _open_unit(base, "payments")
        _write_mirror(base, [_mirror_rec(50, blocker_refs=(60,)),
                              _mirror_rec(60, blocker_refs=(50,))])
        issues = [_open_issue(50, unit="checkout"), _open_issue(60, unit="payments")]
        result = ff.compute(base, "checkout", config=_CFG, run=_issues_runner(issues))
        assert result["cycles"] == []
        assert result["cross_feature_cycles"]


def test_a_longer_cross_feature_chain_is_still_detected():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "unit-a")
        _open_unit(base, "unit-b")
        _write_mirror(base, [_mirror_rec(10, blocker_refs=(30,)),   # a: 10 needs 30
                              _mirror_rec(20, blocker_refs=(10,)),   # b: 20 needs 10
                              _mirror_rec(30, blocker_refs=(20,))])  # a: 30 needs 20 -- closes the loop
        issues = [_open_issue(10, unit="unit-a"), _open_issue(20, unit="unit-b"),
                  _open_issue(30, unit="unit-a")]
        result = ff.compute(base, "unit-a", config=_CFG, run=_issues_runner(issues))
        assert result["cross_feature_cycles"]
        assert set(result["cross_feature_cycles"][0]) == {"10", "20", "30"}


def test_an_ordinary_cross_feature_dependency_without_a_loop_reports_no_cross_feature_cycle():
    """The false-positive control: A waits on B, B does not wait on anything back into A."""
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        _open_unit(base, "payments")
        _write_mirror(base, [_mirror_rec(50, blocker_refs=(60,)), _mirror_rec(60, blocker_refs=())])
        issues = [_open_issue(50, unit="checkout"), _open_issue(60, unit="payments")]
        result = ff.compute(base, "checkout", config=_CFG, run=_issues_runner(issues))
        assert result["cross_feature_cycles"] == []
        assert result["blocked"][0]["waiting_on"] == ["60"]   # still an ordinary, ungraphed block


def test_a_closed_link_in_the_chain_breaks_the_cycle():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        _open_unit(base, "payments")
        _write_mirror(base, [_mirror_rec(50, blocker_refs=(60,)),
                              _mirror_rec(60, blocker_refs=(50,), state="closed")])
        issues = [_open_issue(50, unit="checkout"), _open_issue(60, unit="payments")]
        result = ff.compute(base, "checkout", config=_CFG, run=_issues_runner(issues))
        assert result["cross_feature_cycles"] == []


def test_a_ref_to_an_issue_outside_the_mirror_is_not_treated_as_a_cycle_edge():
    """A ref the mirror has no record for at all contributes no edge -- pre-existing
    `slices._cycles`-own `known`-filter behaviour, inherited unmodified, not implemented here."""
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        _write_mirror(base, [_mirror_rec(50, blocker_refs=(999,))])   # 999 never mirrored
        issues = [_open_issue(50, unit="checkout")]
        result = ff.compute(base, "checkout", config=_CFG, run=_issues_runner(issues))
        assert result["cross_feature_cycles"] == []


def test_intra_unit_cycles_are_unaffected_by_the_new_field():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "alpha")
        _write_mirror(base, [_mirror_rec(20, blocker_refs=(21,)), _mirror_rec(21, blocker_refs=(20,))])
        issues = [_open_issue(20, unit="alpha"), _open_issue(21, unit="alpha")]
        result = ff.compute(base, "alpha", config=_CFG, run=_issues_runner(issues))
        assert result["cycles"]                       # the original field still catches it
        assert result["cross_feature_cycles"] == []    # fully intra-unit -- not reported twice


def test_render_reports_cross_feature_cycles_in_their_own_labelled_section():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        _open_unit(base, "payments")
        _write_mirror(base, [_mirror_rec(50, blocker_refs=(60,)), _mirror_rec(60, blocker_refs=(50,))])
        issues = [_open_issue(50, unit="checkout"), _open_issue(60, unit="payments")]
        result = ff.compute(base, "checkout", config=_CFG, run=_issues_runner(issues))
        text = ff.render(result)
        assert "Cross-feature cycles" in text
        assert "Cross-feature cycles" != "Cycles"       # distinct section, not folded into it
        assert "#50" in text and "#60" in text


def test_an_uninvolved_third_unit_reports_no_cross_feature_cycle():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        _open_unit(base, "payments")
        _open_unit(base, "bystander")
        _write_mirror(base, [_mirror_rec(50, blocker_refs=(60,)), _mirror_rec(60, blocker_refs=(50,)),
                              _mirror_rec(99, blocker_refs=())])
        issues = [_open_issue(50, unit="checkout"), _open_issue(60, unit="payments"),
                  _open_issue(99, unit="bystander")]
        result = ff.compute(base, "bystander", config=_CFG, run=_issues_runner(issues))
        assert result["cross_feature_cycles"] == []


def test_two_disjoint_cross_feature_cycles_are_both_reported():
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "unit-a")
        _open_unit(base, "unit-b")
        _open_unit(base, "unit-c")
        _write_mirror(base, [
            _mirror_rec(50, blocker_refs=(60,)), _mirror_rec(60, blocker_refs=(50,)),   # a<->b
            _mirror_rec(70, blocker_refs=(80,)), _mirror_rec(80, blocker_refs=(70,)),   # a<->c
        ])
        issues = [_open_issue(50, unit="unit-a"), _open_issue(60, unit="unit-b"),
                  _open_issue(70, unit="unit-a"), _open_issue(80, unit="unit-c")]
        result = ff.compute(base, "unit-a", config=_CFG, run=_issues_runner(issues))
        found = {frozenset(c) for c in result["cross_feature_cycles"]}
        assert frozenset({"50", "60"}) in found
        assert frozenset({"70", "80"}) in found
        assert len(found) == 2


def test_the_other_side_missing_from_the_mirror_degrades_silently_not_falsely():
    """Round 2's named limitation: #60's own mirror record was never synced (only #50's is).
    Degrades to the honest, pre-existing 'no node, no edge' behaviour -- no crash, no false claim
    that the cycle was checked and found clear (it was never checkable at all)."""
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        _write_mirror(base, [_mirror_rec(50, blocker_refs=(60,))])   # 60 absent entirely
        issues = [_open_issue(50, unit="checkout")]
        result = ff.compute(base, "checkout", config=_CFG, run=_issues_runner(issues))
        assert result["cross_feature_cycles"] == []
        # #60 has no mirror record at all -- _member_needs' own existing UNRESOLVED bucket (not
        # waiting_on, which is only for a ref resolved as genuinely open), so the absence is still
        # visible in the ordinary blocked report, unaffected by this fix either way.
        assert result["blocked"][0]["unresolved"] == ["60"]


# --- review round: this unit's OWN member must come from the same live data ready/blocked use,
# never from the passively-synced mirror alone -- a stale-closed mirror record for a member that
# is live-open must not make that member vanish from the cross-feature graph while it's still
# showing up as ready/blocked in the very same result. -------------------------------------------


def test_a_member_stale_closed_in_the_mirror_but_live_open_is_still_part_of_a_cross_feature_cycle():
    """#50 is a live-open member of `checkout` (present in the REST fetch), but its MIRROR record
    still says closed -- a real, ordinary staleness window. Before this fix, `_cross_feature_cycles`
    built its graph purely from `by_number` and would skip #50 as a node entirely
    (`_is_closed(record): continue`), so the #50<->#60 cycle would silently vanish even though
    `compute()`'s own `ready`/`blocked` sections correctly treat #50 as open."""
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        _open_unit(base, "payments")
        _write_mirror(base, [_mirror_rec(50, state="closed", blocker_refs=(60,)),
                              _mirror_rec(60, blocker_refs=(50,))])
        issues = [_open_issue(50, unit="checkout"), _open_issue(60, unit="payments")]
        result = ff.compute(base, "checkout", config=_CFG, run=_issues_runner(issues))
        assert result["cross_feature_cycles"], "a live-open member must not drop out on a stale mirror"
        assert set(result["cross_feature_cycles"][0]) == {"50", "60"}
        # sanity: #50 really is live-open in this same result, not accidentally excluded elsewhere
        assert any(r["id"] == "50" for r in result["ready"] + [
            {"id": b["id"]} for b in result["blocked"]])


def test_a_member_with_no_mirror_record_at_all_is_an_inert_node_not_a_crash():
    """#50 is a brand-new member (no mirror record yet, so its own blocker refs are genuinely
    unknown from local data alone). It must not crash or spuriously appear in a cross-feature
    cycle just because it is now seeded as a graph node -- an unknown member contributes no real
    edge, same as `no_mirror_record`/`unresolved` reporting elsewhere in this module."""
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        _open_unit(base, "payments")
        _write_mirror(base, [_mirror_rec(60, blocker_refs=(50,))])   # 50 never synced
        issues = [_open_issue(50, unit="checkout"), _open_issue(60, unit="payments")]
        result = ff.compute(base, "checkout", config=_CFG, run=_issues_runner(issues))
        assert result["cross_feature_cycles"] == []
        assert result["no_mirror_record"] == ["50"]


def test_a_ref_that_passes_isdigit_but_fails_int_does_not_crash_compute_for_another_unit():
    """A malformed `blocker_refs` entry on some OTHER open record in the mirror (not this unit's
    own member) must degrade, never raise -- consistent with this whole module's stated DEGRADES-
    never-raises posture. `\\u00b2` ('²') passes `str.isdigit()` (which is what
    `blocker_scan.read_refs` guarantees) but `int('²')` raises `ValueError`; the regex-driven
    fetch path can't produce this shape, but a hand-built or corrupted mirror record can."""
    ff = _mod("feature_frontier")
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        _open_unit(base, "checkout")
        malformed = _mirror_rec(70, blocker_refs=())
        malformed["blocker_refs"] = [{"phrase": "blocked by", "ref": "²"}]
        _write_mirror(base, [_mirror_rec(50, blocker_refs=()), malformed])
        issues = [_open_issue(50, unit="checkout")]
        result = ff.compute(base, "checkout", config=_CFG, run=_issues_runner(issues))
        assert result["cross_feature_cycles"] == []

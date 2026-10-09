"""#1391 step 5a: the read-only census (reconcile.py) — enumerates the POPULATION Sigma is
responsible for and classifies each issue, rather than querying for corruption (which cannot find
an issue whose defect IS a missing label)."""
import json, re, pathlib, importlib.util

import gqlfake

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _config(**gh):
    return {"discovery": {"source": "github", "github": {"repo": "acme/widget", **gh}}}


def _issue(number, *labels, state="OPEN"):
    return {"number": number, "state": state, "labels": [{"name": n} for n in labels]}


def _runner(by_label=None, fail_labels=(), bad_json_labels=()):
    """Fake `gh` runner answering `issue list --label X --state Y` from `by_label[(X, Y)]`."""
    by_label = by_label or {}
    calls = []

    def run(args):
        calls.append(list(args))
        if args[:2] != ["issue", "list"]:
            return ""
        label = args[args.index("--label") + 1] if "--label" in args else None
        state = args[args.index("--state") + 1] if "--state" in args else "open"
        if label in fail_labels:
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        if label in bad_json_labels:
            return "not json"
        return json.dumps(by_label.get((label, state), []))
    run.calls = calls
    return run


def _src(run):
    return _mod("reconcile").sources.GitHubSource(_config(), run=run)


# --------------------------------------------------------------------------------- classify (pure)

def _primary(rc, src):
    return rc._primary_labels(src, _config())


def _overlays(rc, src):
    return rc._overlay_labels(src)


def test_classify_a_plain_open_goal_is_clean():
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.CLEAN


def test_classify_goal_plus_in_progress_is_clean():
    """The normal active-work signature. in-progress is additive, never a primary state (#1354)."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal", "sdlc:in-progress"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.CLEAN


def test_classify_two_primary_labels_is_multi_label():
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal", "sdlc:parked"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.MULTI_LABEL


def test_classify_managed_issue_with_no_primary_label_is_zero_label():
    """The invisible class: carries a Sigma annotation, no lifecycle label, so no picker query
    can ever return it."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:followup", "priority:P3"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.ZERO_LABEL


def test_classify_in_progress_with_no_primary_label_is_zero_label():
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:in-progress"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.ZERO_LABEL


def test_classify_needs_triage_overlay_is_its_own_bucket():
    """#2364: tier-4 of `feature_classify`'s chain (#2363). `mark_needs_triage` KEEPS `goal_label`
    (membership-kept, same guarantee as `mark_needs_unit`/`mark_needs_label`), so the live shape is
    `sdlc:goal` + `sdlc:needs-triage` together -- without this bucket that shape falls through to
    CLEAN exactly like `sdlc:goal`+`sdlc:blocked` does, and a human waiting to assign a unit gets no
    signal from the census at all."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal", "sdlc:needs-triage"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label,
                       needs_triage_label=src.needs_triage_label) == rc.NEEDS_TRIAGE


def test_classify_needs_triage_without_goal_label_still_reports_as_needs_triage():
    """The overlay is more specific than the generic orphan class -- an issue whose only surviving
    label is `sdlc:needs-triage` (membership lost some other way) is still NEEDS_TRIAGE, not the
    less informative ZERO_LABEL, once the caller knows which label to look for."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:needs-triage"), _primary(rc, src), _overlays(rc, src),
                       src.goal_label, needs_triage_label=src.needs_triage_label) == rc.NEEDS_TRIAGE


def test_classify_needs_triage_defaults_to_none_and_is_backward_compatible():
    """Every caller that predates #2364 (including every other test in this file) calls `classify`
    without `needs_triage_label` at all. That must keep behaving exactly as before -- the overlay
    falling through to the existing ZERO_LABEL/CLEAN logic -- rather than raising or silently
    changing an answer nothing asked it to change."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal", "sdlc:needs-triage"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.CLEAN
    assert rc.classify(_issue(1, "sdlc:needs-triage"), _primary(rc, src), _overlays(rc, src),
                       src.goal_label) == rc.ZERO_LABEL


def test_classify_an_unmanaged_issue_is_clean_not_corruption():
    """An ordinary issue nobody ever handed to Sigma must never be reported as broken."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "bug", "priority:P2"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.CLEAN


def test_classify_closed_keeping_only_the_goal_label_is_flagged():
    """#1445 REVISES #1393 here. `sdlc:goal` on a closed issue is not the designed terminal state --
    it says "the loop may pick this" about an issue that cannot be picked, contradicts the documented
    Done state (no `sdlc:*` label at all), and on reopen silently re-enters the queue nobody
    re-approved. 38 accumulated on `os` in ~25h precisely because nothing flagged it."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal", state="CLOSED"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.CLOSED_WITH_STATE


def test_classify_closed_still_carrying_in_progress_is_flagged():
    """Reopen it and a false state comes back to life -- 121 such issues exist on this repo."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal", "sdlc:in-progress", state="CLOSED"),
                       _primary(rc, src), _overlays(rc, src),
                       src.goal_label) == rc.CLOSED_WITH_STATE


def test_classify_closed_still_carrying_an_overlay_is_flagged():
    """#1393 narrowed this deliberately. It used to include `sdlc:parked` and
    `sdlc:needs-confirmation`, which are MEMBERSHIP labels -- and this bucket feeds the AUTOMATIC,
    ungated, no-human corrector, so flagging them meant a closed-and-parked issue would have a
    human's park silently stripped. Only an ACTIVITY overlay is stale on a closed issue: reopen it
    and a false in-progress/blocked state comes back to life."""
    rc = _mod("reconcile"); src = _src(_runner())
    for overlay in ("sdlc:in-progress", "sdlc:blocked", "sdlc:needs-triage"):
        assert rc.classify(_issue(1, overlay, state="CLOSED"), _primary(rc, src),
                           _overlays(rc, src),
                           src.goal_label) == rc.CLOSED_WITH_STATE, overlay


def test_a_closed_issue_keeping_a_HUMAN_DECISION_label_is_left_alone():
    """#1393's real fix, and #1445 does NOT touch it: a human's park or withheld approval is never
    something an ungated corrector may undo, closed or not. Only `sdlc:goal` -- which encodes no
    human decision -- became stale."""
    rc = _mod("reconcile"); src = _src(_runner())
    for label in ("sdlc:parked", "sdlc:needs-confirmation"):
        assert rc.classify(_issue(1, label, state="CLOSED"), _primary(rc, src), _overlays(rc, src),
                           src.goal_label) == rc.CLEAN, label


def test_a_closed_goal_plus_park_is_correctable_because_only_the_goal_half_is_stale():
    """#1445: `goal`+`parked` was already drift (parked stands ALONE), and removing the goal half is
    exactly what resolves it -- no judgement call, because the park is what survives. Contrast the
    test below, where BOTH labels encode a human decision and a human must choose."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal", "sdlc:parked", state="CLOSED"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.CLOSED_WITH_STATE


def test_two_human_decision_labels_on_a_closed_issue_still_need_a_human():
    """The judgement call #1393 protected: parked vs needs-confirmation is a human's to resolve, so
    it stays in the evidence-based PROPOSED tier, never the ungated AUTOMATIC one."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:parked", "sdlc:needs-confirmation", state="CLOSED"),
                       _primary(rc, src), _overlays(rc, src), src.goal_label) == rc.MULTI_LABEL


# ------------------------------------------------------------------------------------------ census

def test_census_finds_a_zero_label_issue_that_no_primary_query_returns():
    """THE ABSENCE TRAP, and the single most important test in this file. An issue that lost its
    lifecycle label but kept `sdlc:followup` is returned by NONE of the primary-label queries. The
    first version of this census queried only the primary labels and reported ZERO zero-label
    issues on a repo that demonstrably has four (#888, #1217, #1242, #1316, verified live)."""
    rc = _mod("reconcile")
    run = _runner({("sdlc:followup", "open"): [_issue(42, "sdlc:followup", "priority:P3")]})
    result = rc.census(".sdlc", _config(), run=run)
    assert result["issues"].get(rc.ZERO_LABEL) == [42]


def test_census_reports_a_needs_triage_issue_under_its_own_bucket():
    """#2364: the tier-4 overlay (#2363) is already fetched -- `needs_triage_label` has been part of
    `overlay_labels()` since that slice, so E1/E2 already query it. Only `classify` was missing the
    wiring to bucket it as its own thing instead of falling through to CLEAN."""
    rc = _mod("reconcile")
    run = _runner({("sdlc:needs-triage", "open"):
                   [_issue(42, "sdlc:goal", "sdlc:needs-triage")]})
    result = rc.census(".sdlc", _config(), run=run)
    assert result["issues"].get(rc.NEEDS_TRIAGE) == [42]


def test_census_dedupes_an_issue_returned_by_several_queries():
    rc = _mod("reconcile")
    dup = _issue(42, "sdlc:goal", "sdlc:parked")
    run = _runner({("sdlc:goal", "open"): [dup], ("sdlc:parked", "open"): [dup]})
    result = rc.census(".sdlc", _config(), run=run)
    assert result["checked"] == 1
    assert result["issues"].get(rc.MULTI_LABEL) == [42]


def test_census_reports_complete_when_every_query_succeeds():
    rc = _mod("reconcile")
    result = rc.census(".sdlc", _config(), run=_runner())
    assert result["complete"] is True


def test_census_reports_incomplete_when_any_query_fails():
    """'I found nothing' and 'I could not look' are different answers. A caller must never report a
    clean board off an incomplete census."""
    rc = _mod("reconcile")
    result = rc.census(".sdlc", _config(), run=_runner(fail_labels={"sdlc:parked"}))
    assert result["complete"] is False


def test_census_reports_incomplete_on_malformed_json():
    rc = _mod("reconcile")
    result = rc.census(".sdlc", _config(), run=_runner(bad_json_labels={"sdlc:goal"}))
    assert result["complete"] is False


def test_census_still_classifies_what_it_could_read_when_incomplete():
    """Partial data is still useful -- the failure must narrow confidence, not discard findings."""
    rc = _mod("reconcile")
    run = _runner({("sdlc:goal", "open"): [_issue(7, "sdlc:goal", "sdlc:parked")]},
                  fail_labels={"sdlc:blocked"})
    result = rc.census(".sdlc", _config(), run=run)
    assert result["complete"] is False
    assert result["issues"].get(rc.MULTI_LABEL) == [7]


def test_census_queries_closed_issues_for_the_stale_state_class():
    rc = _mod("reconcile")
    run = _runner({("sdlc:in-progress", "closed"):
                   [_issue(9, "sdlc:goal", "sdlc:in-progress", state="CLOSED")]})
    result = rc.census(".sdlc", _config(), run=run)
    assert result["issues"].get(rc.CLOSED_WITH_STATE) == [9]


# --- #1579: the E3 enumerator seam -----------------------------------------------------------
# `sdlc:goal` and `sdlc:needs-confirmation` were missing from E3's tuple, so a CLOSED issue carrying
# only a primary label was fetched by no query at all. #1445 taught `classify` to recognise exactly
# that shape and never taught the enumerator to go and get it; the only reason the bug was invisible
# is that the one closed goal-labelled issue on this repo that ALSO carries an overlay came back
# through the overlay query.
#
# The seam had zero coverage BY CONSTRUCTION, and these tests exist to close it structurally rather
# than to add one more case. `_cen_for` (below) builds a census-shaped dict with no I/O, so every
# corrector test bypasses `census()` entirely; the single test that did drive real `census()` over
# closed issues keys its fixture on `("sdlc:in-progress", "closed")`. Every closed fixture in this
# file attached an overlay to `sdlc:goal` — the production shape, 23 of 24 live, appeared nowhere.
# So these drive REAL `census()`. `_closed` is defined further down; module load order makes that
# fine, and they belong beside the other census tests.


def test_census_enumerates_a_closed_issue_carrying_only_the_goal_label():
    """The bug, exactly: no overlay, so only an E3 query on `sdlc:goal` itself can ever find it."""
    rc = _mod("reconcile")
    run = _runner({("sdlc:goal", "closed"): [_closed(7, "sdlc:goal")]})
    result = rc.census(".sdlc", _config(), run=run)
    assert 7 in result["by_number"], "never enumerated -- E3 did not query the goal label"
    assert result["issues"].get(rc.CLOSED_WITH_STATE) == [7]


def test_enumerating_a_label_never_causes_it_to_be_stripped():
    """The property that must not break. Widening the ENUMERATOR is not widening the CORRECTOR:
    `compute_closed_state_actions` takes `primary` and never reads it, and `_stale_on_closed` strips
    the overlays plus `goal_label` and nothing else. A closed issue carrying a HUMAN's decision keeps
    it — that half of #1393 is deliberately untouched — and this pins it now that E3 fetches the
    label rather than leaving the guarantee resting on never having looked."""
    rc = _mod("reconcile")
    run = _runner({("sdlc:needs-confirmation", "closed"): [_closed(8, "sdlc:needs-confirmation")],
                   ("sdlc:parked", "closed"): [_closed(9, "sdlc:parked")]})
    src = _src(run)
    cen = rc.census(".sdlc", _config(), run=run)
    assert 8 in cen["by_number"] and 9 in cen["by_number"], \
        "fixture is vacuous -- neither issue was ever enumerated"
    # Both halves: nothing is proposed for correction, and neither label reaches the stale set even
    # if something upstream did route the issue into the corrected bucket.
    assert rc.compute_closed_state_actions(src, cen, rc._primary_labels(src, _config())) == []
    for n in (8, 9):
        assert rc._stale_on_closed(cen["by_number"][n], rc._primary_labels(src, _config()),
                                   rc._overlay_labels(src), src.goal_label) == []


def test_the_label_vocabulary_is_read_from_the_source_rather_than_restated():
    """#1579: #1393's audit found five of six hand-written copies of this vocabulary had drifted
    from each other. These two helpers were a sixth copy — byte-identical to
    `GitHubSource.membership_labels()`/`overlay_labels()` and free to drift the same way. Pinned by
    CONSTRUCTION rather than by value: change the source's answer and the helper must follow, which
    a test comparing both against the same literal tuple could never show."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc._primary_labels(src, _config()) == src.membership_labels()
    assert rc._overlay_labels(src) == src.overlay_labels()
    src.membership_labels = lambda: ("only:membership",)
    src.overlay_labels = lambda: ("only:overlay",)
    assert rc._primary_labels(src, _config()) == ("only:membership",)
    assert rc._overlay_labels(src) == ("only:overlay",)


def test_a_closed_parked_issue_keeps_its_park_and_loses_only_the_goal_label():
    """The mixed shape, driven through real `census()` rather than a hand-built census dict."""
    rc = _mod("reconcile")
    run = _runner({("sdlc:goal", "closed"): [_closed(9, "sdlc:goal", "sdlc:parked")]})
    src = _src(run)
    cen = rc.census(".sdlc", _config(), run=run)
    acts = rc.compute_closed_state_actions(src, cen, rc._primary_labels(src, _config()))
    assert [a["issue"] for a in acts] == ["9"]
    assert acts[0]["remove"] == ["sdlc:goal"]


def test_e3_does_not_query_the_same_label_twice_when_two_config_keys_collide():
    """`dict.fromkeys`, for the reason `_managed_hints` already uses it: an adopter who points two
    label keys at the same string must not pay for the same query twice."""
    rc = _mod("reconcile")
    cfg = dict(_config(), ledger={"handoff": {"proposed_label": "sdlc:parked"}})
    run = _runner()
    rc.census(".sdlc", cfg, run=run)
    closed = [c[c.index("--label") + 1] for c in run.calls
              if "--label" in c and c[c.index("--state") + 1] == "closed"]
    assert len(closed) == len(set(closed)), closed
    assert "sdlc:parked" in closed


# --- #1579: truncation. Every label E3 queried before had a small BOUNDED closed population;
# `sdlc:goal`-on-closed does not — it is the terminal residue of every goal ever finished where
# `complete()` failed or never ran, and it grows monotonically. A read that saturates the ceiling
# must not present as a complete census, because `sweep_reconcile`'s refusal contract rests on
# exactly that flag.


def test_a_read_at_the_limit_ceiling_is_not_reported_as_complete(monkeypatch):
    rc = _mod("reconcile")
    monkeypatch.setattr(rc.sources.GitHubSource, "_BOARD_ITEM_LIMIT", 2)
    run = _runner({("sdlc:goal", "closed"): [_closed(n, "sdlc:goal") for n in (7, 8)]})
    result = rc.census(".sdlc", _config(), run=run)
    assert result["truncated"] == ["sdlc:goal (closed)"]
    assert result["failed"] == []
    assert result["complete"] is False


def test_a_short_read_is_still_complete(monkeypatch):
    """The other direction — the guard must not declare every ordinary read partial."""
    rc = _mod("reconcile")
    monkeypatch.setattr(rc.sources.GitHubSource, "_BOARD_ITEM_LIMIT", 3)
    run = _runner({("sdlc:goal", "closed"): [_closed(n, "sdlc:goal") for n in (7, 8)]})
    result = rc.census(".sdlc", _config(), run=run)
    assert result["truncated"] == [] and result["complete"] is True


def test_the_refusal_names_truncation_rather_than_a_failed_query(monkeypatch):
    """A refusal that names the wrong degradation sends the operator after the wrong remedy — wait
    for `gh` to recover, versus raise the ceiling. That is the same defect class as #1579 itself."""
    rc = _mod("reconcile")
    monkeypatch.setattr(rc.sources.GitHubSource, "_BOARD_ITEM_LIMIT", 2)
    run = _runner({("sdlc:goal", "closed"): [_closed(n, "sdlc:goal") for n in (7, 8)]})
    result = rc.sweep_reconcile(".sdlc", _config(), apply=True, run=run)
    assert result["refused"] and "TRUNCATED" in result["refused"]
    assert "_BOARD_ITEM_LIMIT" in result["refused"]
    assert "at least one query failed" not in result["refused"]
    assert result["actions"] == []


def test_the_refusal_still_names_a_failed_query_when_that_is_what_happened():
    rc = _mod("reconcile")
    result = rc.sweep_reconcile(".sdlc", _config(), apply=True,
                                run=_runner(fail_labels={"sdlc:goal"}))
    assert "at least one query failed" in result["refused"]
    assert "TRUNCATED" not in result["refused"]


def test_incomplete_reason_separates_the_two_degradations_and_their_remedies():
    """The two branches side by side. `doctor`'s census row renders this string too, and its own
    truncation branch cannot be driven through doctor (`_load_loop_script` re-execs a fresh
    reconcile module per call, so a patched ceiling never reaches it) -- so it is pinned here."""
    rc = _mod("reconcile")
    failed = rc._incomplete_reason({"failed": ["sdlc:goal (open)"], "truncated": []})
    assert "at least one query failed" in failed and "TRUNCATED" not in failed
    cut = rc._incomplete_reason({"failed": [], "truncated": ["sdlc:goal (closed)"]})
    assert "TRUNCATED" in cut and "_BOARD_ITEM_LIMIT" in cut
    assert "at least one query failed" not in cut
    both = rc._incomplete_reason({"failed": ["sdlc:parked (open)"],
                                  "truncated": ["sdlc:goal (closed)"]})
    assert "at least one query failed" in both and "TRUNCATED" in both
    # A census dict from before these keys existed (`_cen_for`, an older payload) must still
    # produce a sentence rather than an empty one.
    assert rc._incomplete_reason({}) == "at least one query failed"


def test_census_is_read_only():
    """Step 5a writes nothing -- no label edit, no comment, no card move, ever. #2364 extends this
    fixture (rather than adding a twin test) to also cover the new NEEDS_TRIAGE bucket: reporting a
    tier-4 issue must be exactly as read-only as reporting any other anomaly."""
    rc = _mod("reconcile")
    run = _runner({("sdlc:goal", "open"): [_issue(1, "sdlc:goal", "sdlc:parked")],
                   ("sdlc:needs-triage", "open"): [_issue(2, "sdlc:goal", "sdlc:needs-triage")]})
    result = rc.census(".sdlc", _config(), run=run)
    assert result["issues"].get(rc.NEEDS_TRIAGE) == [2]
    assert all(c[:2] == ["issue", "list"] for c in run.calls), run.calls


def test_census_is_a_no_op_outside_github_mode():
    rc = _mod("reconcile")
    run = _runner()
    result = rc.census(".sdlc", {"discovery": {"source": "local-goals"}}, run=run)
    assert result["skipped"] and result["checked"] == 0
    assert run.calls == []


def test_census_honours_a_configured_proposed_label():
    rc = _mod("reconcile")
    cfg = dict(_config(), ledger={"handoff": {"proposed_label": "sdlc:needs-review"}})
    run = _runner({("sdlc:needs-review", "open"): [_issue(5, "sdlc:goal", "sdlc:needs-review")]})
    result = rc.census(".sdlc", cfg, run=run)
    assert result["issues"].get(rc.MULTI_LABEL) == [5]


# ------------------------------------------------------------------------------------------ render

def test_render_names_the_anomalies_and_their_issues():
    rc = _mod("reconcile")
    run = _runner({("sdlc:goal", "open"): [_issue(42, "sdlc:goal", "sdlc:parked")]})
    out = rc.render_census(rc.census(".sdlc", _config(), run=run))
    assert "multi-label" in out and "#42" in out


def test_render_says_so_loudly_when_the_census_was_incomplete():
    rc = _mod("reconcile")
    out = rc.render_census(rc.census(".sdlc", _config(), run=_runner(fail_labels={"sdlc:goal"})))
    assert "INCOMPLETE" in out


def test_render_distinguishes_clean_from_could_not_read():
    rc = _mod("reconcile")
    clean = rc.render_census(rc.census(".sdlc", _config(), run=_runner()))
    assert "no anomalies found" in clean and "INCOMPLETE" not in clean


def test_cli_census_verb_is_wired_through_main(tmp_path, monkeypatch):
    rc = _mod("reconcile")
    monkeypatch.setattr(rc.sources, "_run_gh", lambda a: "[]" if a[:2] == ["issue", "list"] else "")
    base = tmp_path / ".sdlc"; base.mkdir()
    (base / "config.json").write_text(json.dumps(_config()))
    assert rc.main(["reconcile.py", "census", str(base)]) == 0


def test_cli_usage_mentions_the_census_verb(capsys):
    rc = _mod("reconcile")
    assert rc.main(["reconcile.py", "bogus"]) == 2
    err = capsys.readouterr().err
    assert "census" in err and "sweep" in err and "<sdlc_dir>" in err


# --- #1391 step 5b: the timeline oracle -----------------------------------------------------------
# #1349's audit found #226 carrying goal+parked and REFUSED to auto-fix it, because nothing in the
# data model could say which label was true. The timeline can — and where it genuinely cannot, it
# says AMBIGUOUS rather than guessing, which is the same discipline.

_PRIMARY = ("sdlc:goal", "sdlc:parked", "sdlc:blocked", "sdlc:needs-confirmation")
_H = 3600.0


def _ev(ts, action, label, actor="someone"):
    return (ts, action, label, actor)


def test_resolve_primary_single_live_label_needs_no_resolution():
    rc = _mod("reconcile")
    label, why = rc.resolve_primary([_ev(100, "add", "sdlc:goal")], _PRIMARY)
    assert label == "sdlc:goal" and "only live" in why


def test_resolve_primary_honours_removals():
    rc = _mod("reconcile")
    events = [_ev(100, "add", "sdlc:goal"), _ev(200, "add", "sdlc:parked"),
              _ev(300, "remove", "sdlc:goal")]
    label, _ = rc.resolve_primary(events, _PRIMARY)
    assert label == "sdlc:parked"


def test_resolve_primary_later_add_wins_when_separated_by_more_than_the_window():
    """The real #226 shape: +goal, then +parked 45.8 hours later. Two intents, not one."""
    rc = _mod("reconcile")
    events = [_ev(0, "add", "sdlc:goal"), _ev(45.8 * _H, "add", "sdlc:parked")]
    label, why = rc.resolve_primary(events, _PRIMARY)
    assert label == "sdlc:parked" and "45.8h" in why


def test_resolve_primary_is_ambiguous_inside_the_transaction_window():
    """A real _offboard transaction spans ~4 seconds. Inside the window the ordering carries no
    intent, so REFUSING is the correct answer -- guessing here is what #1349 declined to do."""
    rc = _mod("reconcile")
    events = [_ev(1000, "add", "sdlc:goal"), _ev(1004, "add", "sdlc:parked")]
    label, why = rc.resolve_primary(events, _PRIMARY)
    assert label == rc.AMBIGUOUS and "transaction window" in why


def test_resolve_primary_boundary_is_inclusive_toward_refusing():
    """Exactly at the window edge must refuse, not decide -- ties go to caution."""
    rc = _mod("reconcile")
    events = [_ev(0, "add", "sdlc:goal"),
              _ev(rc.TRANSACTION_WINDOW_SECONDS, "add", "sdlc:parked")]
    assert rc.resolve_primary(events, _PRIMARY)[0] == rc.AMBIGUOUS


def test_resolve_primary_none_when_no_primary_label_is_live():
    """The zero-label class -- verified live on #1242."""
    rc = _mod("reconcile")
    events = [_ev(100, "add", "sdlc:goal"), _ev(200, "remove", "sdlc:goal"),
              _ev(300, "add", "sdlc:followup")]
    label, why = rc.resolve_primary(events, _PRIMARY)
    assert label is None and "no primary" in why


def test_resolve_primary_ignores_non_primary_labels_entirely():
    rc = _mod("reconcile")
    events = [_ev(100, "add", "sdlc:goal"), _ev(900, "add", "priority:P0"),
              _ev(901, "add", "sdlc:in-progress")]
    assert rc.resolve_primary(events, _PRIMARY)[0] == "sdlc:goal"


def _timeline_runner(by_issue, fail=False):
    """Fake `gh` answering an aliased multi-issue timeline document."""
    calls = []

    def run(args):
        calls.append(list(args))
        if args[:2] == ["repo", "view"]:
            return json.dumps({"owner": {"login": "acme"}, "name": "widget"})
        if fail:
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        doc = next((a[len("query="):] for a in args if str(a).startswith("query=")), "")
        repo = {}
        for n, evs in by_issue.items():
            if "i%d: issue" % n not in doc:
                continue
            repo["i%d" % n] = {"timelineItems": {"nodes": [
                {"__typename": "LabeledEvent" if a == "add" else "UnlabeledEvent",
                 "createdAt": ts, "actor": {"login": "someone"}, "label": {"name": l}}
                for ts, a, l in evs]}}
        return json.dumps({"data": {"repository": repo}})
    run.calls = calls
    return run


def test_fetch_label_history_parses_and_sorts_oldest_first():
    rc = _mod("reconcile")
    run = _timeline_runner({7: [("2026-08-13T10:45:15Z", "add", "sdlc:parked"),
                                ("2026-08-11T12:55:35Z", "add", "sdlc:goal")]})
    hist = rc.fetch_label_history(_src(run), [7])
    assert [e[2] for e in hist[7]] == ["sdlc:goal", "sdlc:parked"]      # sorted by time, not payload
    assert [e[1] for e in hist[7]] == ["add", "add"]


def test_fetch_label_history_batches_into_one_document_per_25_issues():
    rc = _mod("reconcile")
    run = _timeline_runner({})
    rc.fetch_label_history(_src(run), list(range(1, 27)))               # 26 issues -> 2 documents
    docs = [a for c in run.calls for a in c if str(a).startswith("query=")]
    assert len(docs) == 2


def test_fetch_label_history_fails_open_on_a_bad_batch():
    """The oracle informs a PROPOSAL; a proposal that cannot be evidenced simply is not made."""
    rc = _mod("reconcile")
    assert rc.fetch_label_history(_src(_timeline_runner({}, fail=True)), [7]) == {}


def test_fetch_label_history_skips_unparseable_events():
    rc = _mod("reconcile")
    run = _timeline_runner({7: [("not-a-date", "add", "sdlc:goal"),
                                ("2026-08-11T12:55:35Z", "add", "sdlc:parked")]})
    hist = rc.fetch_label_history(_src(run), [7])
    assert [e[2] for e in hist[7]] == ["sdlc:parked"]


def test_fetch_label_history_needs_a_resolvable_repo():
    """With `discovery.github.repo` unset (the shipped default) the owner/name falls back to
    `gh repo view`; when even that fails there is no repository to query and the oracle yields
    nothing rather than raising."""
    rc = _mod("reconcile")

    def run(a):
        if a[:2] == ["repo", "view"]:
            raise RuntimeError("not a git repository")
        return ""
    src = rc.sources.GitHubSource({"discovery": {"source": "github"}}, run=run)   # repo UNSET
    assert rc.fetch_label_history(src, [7]) == {}


# --- #1391 step 5c: the AUTOMATIC tier ------------------------------------------------------------
# The ONLY class corrected without a human, scoped to CLOSED issues precisely because the corrector
# review's worst harm (releasing a goal whose worker is live) requires an OPEN goal a picker can
# serve. Every picker query filters --state open, so that harm is unreachable here.

_DAY = 24 * 3600


def _closed(number, *labels, closed_h_ago=100.0):
    import time as _t
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(_t.time() - closed_h_ago * 3600, timezone.utc)
    return {"number": number, "state": "CLOSED", "closedAt": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "labels": [{"name": n} for n in labels]}


def _cen_for(issues, rc, src):
    """A census-shaped result over hand-built issues, without any I/O."""
    primary = rc._primary_labels(src, _config())
    by_number, buckets = {}, {}
    for i in issues:
        by_number[i["number"]] = i
        b = rc.classify(i, primary, _overlays(rc, src), src.goal_label)
        buckets.setdefault(b, []).append(i["number"])
    return {"complete": True, "counts": {}, "issues": buckets, "by_number": by_number,
            "checked": len(issues), "queries": 0}, primary


def test_automatic_tier_targets_a_stale_closed_issue():
    rc = _mod("reconcile"); src = _src(_runner())
    cen, primary = _cen_for([_closed(7, "sdlc:goal", "sdlc:in-progress")], rc, src)
    acts = rc.compute_closed_state_actions(src, cen, primary)
    assert [a["issue"] for a in acts] == ["7"]
    # #1445: the MEMBERSHIP label comes off too, so a closed issue ends genuinely label-free.
    assert sorted(acts[0]["remove"]) == ["sdlc:goal", "sdlc:in-progress"]


def test_automatic_tier_never_removes_a_membership_label():
    """#1393's protected half, unchanged by #1445: a human's park or withheld approval is never
    something the ungated tier may undo. `sdlc:goal` is different -- it encodes no human decision --
    so it is the one membership label that IS stale once the issue is closed."""
    rc = _mod("reconcile"); src = _src(_runner())
    cen, primary = _cen_for([_closed(7, "sdlc:goal", "sdlc:parked", "sdlc:blocked")], rc, src)
    acts = rc.compute_closed_state_actions(src, cen, primary)
    # #1445: `sdlc:goal` is now stale on a closed issue and comes off -- but `sdlc:parked`, a
    # HUMAN's decision, is still untouchable by the ungated tier. That distinction IS the rule.
    assert sorted(acts[0]["remove"]) == ["sdlc:blocked", "sdlc:goal"]
    assert "sdlc:parked" not in acts[0]["remove"]


def test_automatic_tier_now_corrects_a_closed_issue_carrying_blocked():
    """The other half of the same bug: once `goal_blocked_label` left `primary`, a closed issue
    carrying `sdlc:blocked` was FLAGGED by the census and then never corrected -- an anomaly the
    corrector could not act on, reported forever."""
    rc = _mod("reconcile"); src = _src(_runner())
    cen, primary = _cen_for([_closed(7, "sdlc:goal", "sdlc:blocked")], rc, src)
    assert sorted(rc.compute_closed_state_actions(src, cen, primary)[0]["remove"]) == [
        "sdlc:blocked", "sdlc:goal"]


def test_automatic_tier_skips_an_issue_closed_too_recently():
    """Under the settle window it may still be mid-transition."""
    rc = _mod("reconcile"); src = _src(_runner())
    cen, primary = _cen_for([_closed(7, "sdlc:goal", "sdlc:in-progress", closed_h_ago=1)], rc, src)
    assert rc.compute_closed_state_actions(src, cen, primary) == []


def test_automatic_tier_skips_an_unparseable_closed_timestamp():
    """An unreadable timestamp is never ASSUMED old -- fail toward inaction."""
    rc = _mod("reconcile"); src = _src(_runner())
    issue = _closed(7, "sdlc:goal", "sdlc:in-progress")
    issue["closedAt"] = "whenever"
    cen, primary = _cen_for([issue], rc, src)
    assert rc.compute_closed_state_actions(src, cen, primary) == []


def test_automatic_tier_respects_the_per_sweep_cap():
    """A corrector that suddenly rewrites hundreds of issues is indistinguishable from a runaway."""
    rc = _mod("reconcile"); src = _src(_runner())
    many = [_closed(n, "sdlc:goal", "sdlc:in-progress") for n in range(1, 60)]
    cen, primary = _cen_for(many, rc, src)
    assert len(rc.compute_closed_state_actions(src, cen, primary)) == rc.MAX_CORRECTIONS_PER_SWEEP


def _apply_runner(state="CLOSED", labels=("sdlc:goal", "sdlc:in-progress"), swap_fails=False):
    calls, unexpected = [], []

    def run(args):
        calls.append(list(args))
        if args[:2] == ["repo", "view"]:
            return json.dumps({"owner": {"login": "acme"}, "name": "widget"})
        rest = gqlfake.rest_issue(args, lambda n, f: {"state": state, "labels": [{"name": x} for x in labels]})
        if rest is not None:        # #895: the fresh re-read is REST first
            return rest
        if args[:2] == ["issue", "view"]:
            unexpected.append(list(args))     # the fallback is not expected on a healthy REST read
            return json.dumps({"state": state, "labels": [{"name": n} for n in labels]})
        if args[:2] == ["api", "graphql"]:
            doc = next((a[len("query="):] for a in args if str(a).startswith("query=")), "")
            if doc.startswith("mutation"):
                if swap_fails:
                    raise RuntimeError("gh: HTTP 502 Bad Gateway")
                return json.dumps({"data": {"a": {}}})
            if "label(name:" in doc:
                # #1393 review bug_001: ids are fetched BY NAME now, one aliased field per label
                fields = {}
                for alias, lbl in re.findall(r'(a\d+): label\(name: "([^"]*)"\)', doc):
                    fields[alias] = {"id": "L_%s" % lbl.replace(":", "_"), "name": lbl}
                return json.dumps({"data": {"repository": fields}})
            if "labels(first" in doc:
                return json.dumps({"data": {"repository": {"labels": {"nodes": [
                    {"id": "L_%d" % i, "name": n} for i, n in enumerate(
                        ["sdlc:goal", "sdlc:in-progress", "sdlc:parked", "sdlc:blocked",
                         "sdlc:needs-confirmation"])]}}}})
            if "issue(number" in doc:
                return json.dumps({"data": {"repository": {"issue": {"id": "I_1"}}}})
        return ""
    run.calls = calls
    run.unexpected = unexpected      # #895: any `issue view` (the fallback) on a healthy REST read
    return run


def _action():
    return [{"issue": "7", "remove": ["sdlc:in-progress"], "reason": "stale"}]


def test_apply_dry_run_writes_nothing():
    rc = _mod("reconcile"); run = _apply_runner()
    out = rc.apply_closed_state_actions(_src(run), _action(), apply=False)
    assert out[0]["result"] == "would"
    assert run.calls == []


def test_apply_removes_the_stale_label():
    rc = _mod("reconcile"); run = _apply_runner()
    src = _src(run); src._LABEL_SWAP_RETRY_BASE = 0
    out = rc.apply_closed_state_actions(src, _action(), apply=True)
    assert out[0]["result"] == "done"
    docs = [a for c in run.calls for a in c if str(a).startswith("query=mutation")]
    assert len(docs) == 1 and "removeLabelsFromLabelable" in docs[0]
    # #895: the fresh re-read was ONE REST GET of the right issue; no `issue view` fallback
    assert [c for c in run.calls if c[0] == "api" and c[1].startswith("repos/")] == [
        ["api", "repos/acme/widget/issues/7", "--method", "GET"]]
    assert run.unexpected == []


def test_apply_refuses_an_issue_reopened_since_the_census():
    """The re-read guard. There is no compare-and-set on GitHub labels, so a fresh read immediately
    before the write is the closest available thing -- and it closes the realistic window."""
    rc = _mod("reconcile"); run = _apply_runner(state="OPEN")
    out = rc.apply_closed_state_actions(_src(run), _action(), apply=True)
    assert out[0]["result"] == "skipped" and "reopened" in out[0]["error"]
    assert not [a for c in run.calls for a in c if str(a).startswith("query=mutation")]


def test_apply_skips_an_issue_already_cleaned_by_someone_else():
    rc = _mod("reconcile"); run = _apply_runner(labels=("sdlc:goal",))
    out = rc.apply_closed_state_actions(_src(run), _action(), apply=True)
    assert out[0]["result"] == "skipped" and "already clean" in out[0]["error"]


def test_apply_writes_the_audit_comment_before_the_label_write():
    """If the write then fails, the record of what was attempted still exists."""
    rc = _mod("reconcile"); run = _apply_runner()
    src = _src(run); src._LABEL_SWAP_RETRY_BASE = 0
    rc.apply_closed_state_actions(src, _action(), apply=True)
    order = [i for i, c in enumerate(run.calls)
             if c[:2] == ["issue", "comment"] or str(c[-1]).startswith("query=mutation")]
    kinds = [("comment" if run.calls[i][:2] == ["issue", "comment"] else "write") for i in order]
    assert kinds.index("comment") < kinds.index("write")


# --- #1579: the audit comment is a STATEMENT OF THE RULE, and it had gone stale ----------------


def test_the_audit_comment_does_not_claim_the_goal_label_survives():
    """`_AUDIT_COMMENT` ended "the goal label is left untouched", which #1445 made false the moment
    it added `goal_label` to `_stale_on_closed`. It stayed harmless only because E3 never fetched a
    closed goal-labelled issue — widening the enumerator makes GitHub carry the false sentence on
    every corrected issue, hundreds of them. The comment must state the rule that is now true: the
    overlays and membership-BY-GOAL go, a HUMAN's decision stays."""
    rc = _mod("reconcile")
    body = rc._AUDIT_COMMENT.format(stale="sdlc:goal, sdlc:in-progress")
    assert "goal label is left untouched" not in body
    assert "sdlc:parked" in body and "sdlc:needs-confirmation" in body


def test_the_posted_audit_comment_names_what_was_actually_removed():
    """Through the real write path, not against the template — a caller could still format it with
    a stale set."""
    rc = _mod("reconcile"); run = _apply_runner()
    src = _src(run); src._LABEL_SWAP_RETRY_BASE = 0
    rc.apply_closed_state_actions(src, [{"issue": "7", "remove": ["sdlc:goal", "sdlc:in-progress"],
                                         "reason": "stale"}], apply=True)
    body = next(c[c.index("--body") + 1] for c in run.calls if c[:2] == ["issue", "comment"])
    assert "sdlc:goal" in body and "sdlc:in-progress" in body
    assert "goal label is left untouched" not in body


def test_apply_records_a_failed_write_rather_than_raising():
    rc = _mod("reconcile"); run = _apply_runner(swap_fails=True)
    src = _src(run); src._LABEL_SWAP_RETRY_BASE = 0
    out = rc.apply_closed_state_actions(src, _action(), apply=True)
    assert out[0]["result"] == "failed" and out[0]["error"]


def test_sweep_refuses_to_correct_from_an_incomplete_census():
    """Correction is a diff against what the census saw; a partial read must never drive writes."""
    rc = _mod("reconcile")
    result = rc.sweep_reconcile(".sdlc", _config(), apply=True,
                                run=_runner(fail_labels={"sdlc:goal"}))
    assert result["refused"] and "incomplete" in result["refused"]
    assert result["actions"] == []


def test_sweep_is_a_no_op_outside_github_mode():
    rc = _mod("reconcile")
    result = rc.sweep_reconcile(".sdlc", {"discovery": {"source": "local-goals"}}, apply=True,
                                run=_runner())
    assert result["refused"] and result["actions"] == []


def test_cli_sweep_verb_is_wired_and_dry_runs_by_default(tmp_path, monkeypatch, capsys):
    rc = _mod("reconcile")
    monkeypatch.setattr(rc.sources, "_run_gh", lambda a: "[]" if a[:2] == ["issue", "list"] else "")
    base = tmp_path / ".sdlc"; base.mkdir()
    (base / "config.json").write_text(json.dumps(_config()))
    assert rc.main(["reconcile.py", "sweep", str(base)]) == 0
    assert "DRY-RUN" in capsys.readouterr().out


# --- #1391 step 5d: the PROPOSED tier -------------------------------------------------------------
# Corrections the timeline can EVIDENCE but that must not be applied unattended, because they touch
# OPEN issues. Each proposal carries its evidence, so approving one is a judgement on facts.


def _cen_open(issues, rc, src):
    primary = rc._primary_labels(src, _config())
    by_number, buckets = {}, {}
    for i in issues:
        by_number[i["number"]] = i
        buckets.setdefault(rc.classify(i, primary, _overlays(rc, src), src.goal_label),
                           []).append(i["number"])
    return {"complete": True, "counts": {}, "issues": buckets, "by_number": by_number,
            "checked": len(issues), "queries": 0}, primary


def test_proposal_resolves_a_multi_label_issue_and_carries_its_evidence():
    """The real #226 shape: +goal, then +parked 45.8h later -> remove the loser, keep the winner."""
    rc = _mod("reconcile"); src = _src(_runner())
    cen, primary = _cen_open([_issue(226, "sdlc:goal", "sdlc:parked")], rc, src)
    hist = {226: [_ev(0, "add", "sdlc:goal"), _ev(45.8 * _H, "add", "sdlc:parked")]}
    props, unresolved = rc.compute_proposals(src, cen, primary, hist)
    assert len(props) == 1 and props[0]["issue"] == "226"
    assert props[0]["remove"] == ["sdlc:goal"] and props[0]["add"] == []
    assert "45.8h" in props[0]["evidence"]
    assert unresolved == []


def test_an_ambiguous_tangle_is_never_proposed():
    """Inside the transaction window the ordering carries no intent -- it goes to a human. This is
    the discipline #1349 applied by hand, now encoded."""
    rc = _mod("reconcile"); src = _src(_runner())
    cen, primary = _cen_open([_issue(7, "sdlc:goal", "sdlc:parked")], rc, src)
    hist = {7: [_ev(1000, "add", "sdlc:goal"), _ev(1004, "add", "sdlc:parked")]}
    props, unresolved = rc.compute_proposals(src, cen, primary, hist)
    assert props == []
    assert unresolved and unresolved[0]["issue"] == "7"


def test_a_zero_label_issue_that_never_had_one_goes_to_a_human():
    """Verified live: #888/#1242/#1316 were BORN without a lifecycle label, so there is nothing to
    restore and inventing one would be a guess."""
    rc = _mod("reconcile"); src = _src(_runner())
    cen, primary = _cen_open([_issue(888, "sdlc:followup")], rc, src)
    hist = {888: [_ev(10, "add", "sdlc:followup")]}
    props, unresolved = rc.compute_proposals(src, cen, primary, hist)
    assert props == []
    assert "nothing to restore" in unresolved[0]["why"]


def test_a_zero_label_issue_that_lost_a_label_is_proposed_for_restore():
    rc = _mod("reconcile"); src = _src(_runner())
    cen, primary = _cen_open([_issue(9, "sdlc:followup")], rc, src)
    hist = {9: [_ev(10, "add", "sdlc:goal"), _ev(500, "remove", "sdlc:goal")]}
    props, unresolved = rc.compute_proposals(src, cen, primary, hist)
    assert props[0]["add"] == ["sdlc:goal"] and props[0]["remove"] == []
    assert unresolved == []


def test_missing_history_is_unresolved_never_guessed():
    rc = _mod("reconcile"); src = _src(_runner())
    cen, primary = _cen_open([_issue(7, "sdlc:goal", "sdlc:parked")], rc, src)
    props, unresolved = rc.compute_proposals(src, cen, primary, {})
    assert props == [] and "no label history" in unresolved[0]["why"]


def test_write_and_load_round_trip(tmp_path):
    rc = _mod("reconcile")
    props = [{"issue": "226", "klass": rc.MULTI_LABEL, "add": [], "remove": ["sdlc:goal"],
              "evidence": "e"}]
    path = rc.write_proposal(str(tmp_path), props, [])
    loaded = rc.load_proposal(path)
    assert loaded["schema"] == rc.PROPOSAL_SCHEMA and loaded["proposals"] == props
    active = json.loads((tmp_path / "plans" / "reconcile" / "active.json").read_text())
    assert active["proposal_json"].endswith("-reconcile.json")


def test_load_proposal_rejects_a_bad_artifact(tmp_path):
    """The artifact is the sole input to an apply -- a bad file must be a hard failure, never a
    silent empty run."""
    import pytest as _pytest
    rc = _mod("reconcile")
    bad = tmp_path / "bad.json"
    for payload in ("not json", json.dumps([1, 2]), json.dumps({"schema": "triage-plan/1"}),
                    json.dumps({"schema": "reconcile-proposal/1", "proposals": "nope"})):
        bad.write_text(payload)
        with _pytest.raises(ValueError):
            rc.load_proposal(str(bad))
    with _pytest.raises(ValueError):
        rc.load_proposal(str(tmp_path / "missing.json"))


def test_apply_proposal_dry_run_writes_nothing():
    rc = _mod("reconcile"); run = _apply_runner()
    out = rc.apply_proposal(_src(run), {"proposals": [
        {"issue": "7", "add": [], "remove": ["sdlc:in-progress"], "evidence": "e"}]}, apply=False)
    assert out[0]["result"] == "would" and run.calls == []


def test_apply_proposal_skips_an_issue_already_in_the_proposed_state():
    rc = _mod("reconcile"); run = _apply_runner(labels=("sdlc:goal",))
    out = rc.apply_proposal(_src(run), {"proposals": [
        {"issue": "7", "add": [], "remove": ["sdlc:in-progress"], "evidence": "e"}]}, apply=True)
    assert out[0]["result"] == "skipped" and "already in the proposed state" in out[0]["error"]


def test_apply_proposal_applies_an_add_and_a_remove_in_one_swap():
    rc = _mod("reconcile"); run = _apply_runner(labels=("sdlc:goal", "sdlc:parked"))
    src = _src(run); src._LABEL_SWAP_RETRY_BASE = 0
    out = rc.apply_proposal(src, {"proposals": [
        {"issue": "7", "add": ["sdlc:blocked"], "remove": ["sdlc:parked"], "evidence": "e"}]},
        apply=True)
    assert out[0]["result"] == "done"
    docs = [a for c in run.calls for a in c if str(a).startswith("query=mutation")]
    assert len(docs) == 1 and "addLabelsToLabelable" in docs[0] and "removeLabels" in docs[0]
    assert [c for c in run.calls if c[0] == "api" and c[1].startswith("repos/")] == [
        ["api", "repos/acme/widget/issues/7", "--method", "GET"]]
    assert run.unexpected == []


def test_apply_closed_state_and_proposal_skip_a_non_numeric_ref_without_any_read():
    """#895: `_read_issue` int()s the ref; a malformed one is skipped as 'could not re-read'."""
    rc = _mod("reconcile"); run = _apply_runner()
    out = rc.apply_closed_state_actions(_src(run), [{"issue": "not-a-number", "remove": ["x"],
                                                     "reason": "r"}], apply=True)
    assert out[0]["result"] == "skipped" and "re-read" in (out[0]["error"] or "")
    out = rc.apply_proposal(_src(run), {"proposals": [
        {"issue": "not-a-number", "add": [], "remove": ["x"], "evidence": "e"}]}, apply=True)
    assert out[0]["result"] == "skipped" and "could not re-read" in out[0]["error"]
    assert run.calls == [] or all(c[0] != "api" or not c[1].startswith("repos/") for c in run.calls)
    assert run.unexpected == []


def test_apply_skips_when_the_fresh_reread_fails_at_both_closed_state_and_proposal_sites():
    """GhApiError (REST and the one fallback both fail) lands in each site's own `except Exception`."""
    rc = _mod("reconcile"); inner = _apply_runner()

    def run(args):
        if gqlfake.rest_issue_target(args) or args[:2] == ["issue", "view"]:
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        return inner(args)
    out = rc.apply_closed_state_actions(_src(run), _action(), apply=True)
    assert out[0]["result"] == "skipped" and "could not re-read" in out[0]["error"]
    out = rc.apply_proposal(_src(run), {"proposals": [
        {"issue": "7", "add": [], "remove": ["sdlc:in-progress"], "evidence": "e"}]}, apply=True)
    assert out[0]["result"] == "skipped" and "could not re-read" in out[0]["error"]
    assert not [a for c in inner.calls for a in c if str(a).startswith("query=mutation")]


def test_apply_rest_5xx_falls_back_once_to_issue_view(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.delenv("SIGMA_GH_GRAPHQL", raising=False)
    rc = _mod("reconcile"); inner = _apply_runner()

    def run(args):
        if gqlfake.rest_issue_target(args):
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        return inner(args)
    src = _src(run); src._LABEL_SWAP_RETRY_BASE = 0
    out = rc.apply_proposal(src, {"proposals": [
        {"issue": "7", "add": [], "remove": ["sdlc:in-progress"], "evidence": "e"}]}, apply=True)
    assert out[0]["result"] == "done", out
    assert [c[:3] for c in inner.calls if c[:2] == ["issue", "view"]] == [["issue", "view", "7"]]
    assert len(inner.unexpected) == 1       # the fallback read, recorded


def test_cli_apply_requires_a_plan(capsys):
    rc = _mod("reconcile")
    assert rc.apply_cmd(".sdlc", _config(), []) == 2
    assert "--plan" in capsys.readouterr().err


# --- #1393: `sdlc:blocked` is an OVERLAY, not a primary state ------------------------------------
# These are the tests whose ABSENCE let the first attempt at this change fail silently: the edit to
# `_primary_labels` never applied (an assertion aborted the script before it wrote), and the whole
# suite stayed green because nothing here exercised goal+blocked through the census. A doc audit
# found it, not the tests. That is the gap these close.


def test_a_blocked_goal_is_clean_not_multi_label():
    """`mark_blocked` KEEPS `sdlc:goal`, so goal+blocked is the correct, normal shape of a blocked
    goal. Reporting it as corruption would be bad enough on its own -- but the PROPOSAL tier would
    then offer "corrections" against a state the loop creates on purpose, i.e. the reconciler
    proposing to break working goals."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal", "sdlc:blocked"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.CLEAN


def test_goal_plus_in_progress_plus_blocked_is_still_clean():
    """Both overlays at once is unusual but not contradictory -- a goal claimed and then blocked
    before the claim marker was cleared. One membership label is the invariant, not one label."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal", "sdlc:in-progress", "sdlc:blocked"),
                       _primary(rc, src), _overlays(rc, src), src.goal_label) == rc.CLEAN


def test_two_membership_labels_are_still_multi_label():
    """The invariant that remains: in the world / a human's exit / not admitted yet are mutually
    exclusive."""
    rc = _mod("reconcile"); src = _src(_runner())
    for pair in (("sdlc:goal", "sdlc:parked"), ("sdlc:goal", "sdlc:needs-confirmation"),
                 ("sdlc:parked", "sdlc:needs-confirmation")):
        assert rc.classify(_issue(1, *pair), _primary(rc, src), _overlays(rc, src),
                           src.goal_label) == rc.MULTI_LABEL, pair


def test_a_blocked_only_issue_is_still_the_orphan_class():
    """Removing `goal_blocked_label` from `primary` must NOT stop the census seeing an issue that
    carries the overlay and no membership at all -- that is precisely the orphan a partial write
    from an older plugin leaves behind, and the whole reason this census exists."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:blocked"), _primary(rc, src), _overlays(rc, src),
                       src.goal_label) == rc.ZERO_LABEL


def test_a_closed_issue_still_carrying_blocked_is_still_flagged():
    """The other detection the removal could have silently cost: a stale not-eligible label on a
    closed issue is exactly the "reopen it and a false state comes back to life" case."""
    rc = _mod("reconcile"); src = _src(_runner())
    assert rc.classify(_issue(1, "sdlc:goal", "sdlc:blocked", state="CLOSED"), _primary(rc, src),
                       _overlays(rc, src), src.goal_label) == rc.CLOSED_WITH_STATE


def test_the_census_enumerates_a_blocked_only_open_issue():
    """End-to-end over the real enumerator, not just `classify`: the open-issue query set must still
    include the blocked overlay now that it is out of `primary`. A query that is never made cannot
    return the issue whose defect IS its missing label."""
    rc = _mod("reconcile")
    run = _runner({("sdlc:blocked", "open"): [_issue(77, "sdlc:blocked")]})
    result = rc.census(".sdlc", _config(), run=run)
    assert 77 in (result["issues"].get(rc.ZERO_LABEL) or []), result["issues"]


# --- cloud-review findings on part 1 (bug_002, bug_003) ------------------------------------------


def test_managed_hints_follow_a_renamed_blocking_label():
    """Review bug_002. `sdlc:blocking` and `sdlc:dependency` are adopter-CONFIGURABLE, and the
    shipped template tells adopters to rename them if they collide. Hardcoding the defaults meant
    the census queried a string nothing carries on a renamed repo, and `classify`'s fallback then
    read an issue whose only surviving Sigma trace was the renamed hint as CLEAN -- the absence
    trap these hints exist to close, reopened by the hints themselves."""
    rc = _mod("reconcile")
    cfg = _config(blocking_label="team:blocking")
    cfg["ledger"] = {"handoff": {"label": "team:dependency"}}
    src = rc.sources.GitHubSource(cfg, run=_runner())
    hints = rc._managed_hints(src, cfg)
    assert "team:blocking" in hints and "team:dependency" in hints
    assert "sdlc:blocking" not in hints and "sdlc:dependency" not in hints
    assert "sdlc:followup" in hints          # not configurable -- stays a literal


def test_an_orphan_carrying_only_a_renamed_hint_is_still_ours():
    rc = _mod("reconcile")
    cfg = _config(blocking_label="team:blocking")
    src = rc.sources.GitHubSource(cfg, run=_runner())
    issue = _issue(1, "team:blocking")
    assert rc.classify(issue, _primary(rc, src), _overlays(rc, src), src.goal_label,
                       hints=rc._managed_hints(src, cfg)) == rc.ZERO_LABEL
    # ...and with the hardcoded defaults it would have read as somebody else's issue
    assert rc.classify(issue, _primary(rc, src), _overlays(rc, src),
                       src.goal_label) == rc.CLEAN


def test_the_census_queries_the_renamed_hint():
    """End-to-end over the real enumerator: a query never made cannot return the issue whose defect
    IS its missing label."""
    rc = _mod("reconcile")
    cfg = _config(blocking_label="team:blocking")
    run = _runner({("team:blocking", "open"): [_issue(77, "team:blocking")]})
    result = rc.census(".sdlc", cfg, run=run)
    assert 77 in (result["issues"].get(rc.ZERO_LABEL) or []), result["issues"]


def test_two_proposals_on_the_same_day_do_not_overwrite_each_other(tmp_path):
    """Review bug_003. The filename stem was the DATE only, so a second `propose` on the same UTC
    day silently rewrote the first via `os.replace`. The README tells operators to apply by explicit
    path and `propose_cmd` prints that path -- so a human who reviewed proposal A and re-ran
    `propose` would apply B's contents from A's path: different issues, different label changes,
    different evidence. `apply_proposal`'s per-issue re-read cannot catch it, because an entry that
    is new in B has no drift to trip on. The whole safety property of this tier is that a human
    applies exactly what they approved."""
    rc = _mod("reconcile")
    a = rc.write_proposal(str(tmp_path), [{"issue": "1", "add": [], "remove": ["sdlc:parked"],
                                           "evidence": "A", "klass": rc.MULTI_LABEL}], [],
                          generated_at="2026-08-19T10:00:00Z")
    b = rc.write_proposal(str(tmp_path), [{"issue": "2", "add": [], "remove": ["sdlc:parked"],
                                           "evidence": "B", "klass": rc.MULTI_LABEL}], [],
                          generated_at="2026-08-19T14:30:00Z")
    assert a != b, "same-day proposals still collide"
    assert rc.load_proposal(a)["proposals"][0]["issue"] == "1"
    assert rc.load_proposal(b)["proposals"][0]["issue"] == "2"


# --- #2295 (`.sdlc/design/2287.md` detailed design section 2): promote DECISIVE open-issue --------
# corrections from the PROPOSED tier to AUTOMATIC. Condition: resolve_primary returned a real winner
# (not AMBIGUOUS, not None) AND winner == goal_label, full stop -- narrower than "only adds or
# restores membership" (a REJECTED earlier draft: see round-1 goal-review Finding 2). Because
# compute_proposals only ever adds an overlay to a MULTI_LABEL entry's remove list when
# winner != goal_label, this condition means no promoted correction can EVER remove an overlay, by
# construction -- tested directly below, not taken on faith from the design doc.
#
# A census-time winner can go stale before the write executes (a human acts on the same issue in the
# window between census() and apply). apply_proposal's existing re-read only re-verifies the SPECIFIC
# labels already queued are still present -- not that the WINNER is still the same -- so this needs a
# genuinely NEW fresh eligibility re-check immediately before the write, mirroring
# apply_closed_state_actions's own shape (reconcile.py:579-624).


def _sweep_runner(by_label=None, history=None, fresh_state="OPEN", fresh_labels=(), swap_fails=False):
    """A `gh` fake wide enough for BOTH ends of the open-issue promotion path: the CENSUS-TIME
    `issue list` queries (`by_label`, `_runner`'s own shape), the CENSUS-TIME batched history fetch
    AND the WRITE-TIME per-issue fresh re-check's OWN, second, independent history fetch (`history`,
    `_timeline_runner`'s own shape, keyed by int issue number), and the write-time `issue view` +
    label-swap mutation path (`_apply_runner`'s own shape). One issue-view snapshot (`fresh_state`,
    `fresh_labels`) serves every fresh re-check in a given test -- tests that need per-issue
    variation call `apply_open_issue_promotions` directly instead (see the stale-race tests below)."""
    by_label = by_label or {}
    history = history or {}
    calls, unexpected = [], []

    def run(args):
        calls.append(list(args))
        if args[:2] == ["issue", "list"]:
            label = args[args.index("--label") + 1] if "--label" in args else None
            state = args[args.index("--state") + 1] if "--state" in args else "open"
            return json.dumps(by_label.get((label, state), []))
        if args[:2] == ["repo", "view"]:
            return json.dumps({"owner": {"login": "acme"}, "name": "widget"})
        rest = gqlfake.rest_issue(args, lambda n, f: {"state": fresh_state,
                                                      "labels": [{"name": x} for x in fresh_labels]})
        if rest is not None:        # #895: the fresh re-read is REST first
            return rest
        if args[:2] == ["issue", "view"]:
            unexpected.append(list(args))
            return json.dumps({"state": fresh_state, "labels": [{"name": n} for n in fresh_labels]})
        if args[:2] == ["api", "graphql"]:
            doc = next((a[len("query="):] for a in args if str(a).startswith("query=")), "")
            if doc.startswith("mutation"):
                if swap_fails:
                    raise RuntimeError("gh: HTTP 502 Bad Gateway")
                return json.dumps({"data": {"a": {}}})
            if "label(name:" in doc:
                fields = {}
                for alias, lbl in re.findall(r'(a\d+): label\(name: "([^"]*)"\)', doc):
                    fields[alias] = {"id": "L_%s" % lbl.replace(":", "_"), "name": lbl}
                return json.dumps({"data": {"repository": fields}})
            if "labels(first" in doc:
                return json.dumps({"data": {"repository": {"labels": {"nodes": [
                    {"id": "L_%d" % i, "name": n} for i, n in enumerate(
                        ["sdlc:goal", "sdlc:in-progress", "sdlc:parked", "sdlc:blocked",
                         "sdlc:needs-confirmation"])]}}}})
            # NOTE: checked BEFORE the plain "issue(number" branch below -- fetch_label_history's own
            # query text is 'i226: issue(number: 226) { timelineItems(...' so it ALSO contains the
            # substring "issue(number", and the more specific check must win the dispatch.
            if "timelineItems" in doc:
                repo = {}
                for n, evs in history.items():
                    if "i%d: issue" % n not in doc:
                        continue
                    repo["i%d" % n] = {"timelineItems": {"nodes": [
                        {"__typename": "LabeledEvent" if a == "add" else "UnlabeledEvent",
                         "createdAt": ts, "actor": {"login": "someone"}, "label": {"name": l}}
                        for ts, a, l in evs]}}
                return json.dumps({"data": {"repository": repo}})
            if "issue(number" in doc:
                return json.dumps({"data": {"repository": {"issue": {"id": "I_1"}}}})
        return ""
    run.calls = calls
    run.unexpected = unexpected
    return run


def _mutation_docs(run):
    return [a for c in run.calls for a in c if str(a).startswith("query=mutation")]


# ----------------------------------------------------------------------------- promotion condition


def test_a_decisive_multi_label_winner_equal_to_goal_label_promotes_and_applies_unattended():
    """Test 1. A MULTI_LABEL correction resolving decisively to `goal_label` promotes from PROPOSED
    to AUTOMATIC and applies without any human -- through sweep_reconcile's real wiring, gate on."""
    rc = _mod("reconcile")
    cfg = _config()
    cfg["discovery"]["reconcile"] = {"mode": "on", "open_issue_mode": "on"}
    hist = {226: [("2026-01-01T00:00:00Z", "add", "sdlc:parked"),
                  ("2026-01-03T00:00:00Z", "add", "sdlc:goal")]}   # goal wins, 48h later -- decisive
    run = _sweep_runner(by_label={("sdlc:goal", "open"): [_issue(226, "sdlc:goal", "sdlc:parked")]},
                        history=hist, fresh_state="OPEN", fresh_labels=("sdlc:goal", "sdlc:parked"))
    src = _mod("reconcile").sources.GitHubSource(cfg, run=run)
    src._LABEL_SWAP_RETRY_BASE = 0
    result = rc.sweep_reconcile(".sdlc", cfg, apply=True, run=run)
    assert [a["issue"] for a in result["open_actions"]] == ["226"]
    assert result["open_actions"][0]["result"] == "done", result["open_actions"]
    docs = _mutation_docs(run)
    assert any("removeLabelsFromLabelable" in d for d in docs)


def test_a_decisive_multi_label_winner_not_equal_to_goal_label_stays_proposed():
    """Test 2. Resolving to `sdlc:parked` instead of `sdlc:goal` is just as decisive, but does not
    promote -- unaffected by this slice, exactly as today."""
    rc = _mod("reconcile")
    cfg = _config()
    cfg["discovery"]["reconcile"] = {"mode": "on", "open_issue_mode": "on"}
    hist = {226: [("2026-01-01T00:00:00Z", "add", "sdlc:goal"),
                  ("2026-01-03T00:00:00Z", "add", "sdlc:parked")]}   # parked wins, decisively
    run = _sweep_runner(by_label={("sdlc:goal", "open"): [_issue(226, "sdlc:goal", "sdlc:parked")]},
                        history=hist)
    result = rc.sweep_reconcile(".sdlc", cfg, apply=True, run=run)
    assert result["open_actions"] == []
    assert _mutation_docs(run) == []


def test_zero_label_restore_of_goal_label_promotes():
    """Test 3a. The primary being restored IS `goal_label` -- promotes."""
    rc = _mod("reconcile")
    cfg = _config()
    cfg["discovery"]["reconcile"] = {"mode": "on", "open_issue_mode": "on"}
    hist = {9: [("2026-01-01T00:00:00Z", "add", "sdlc:goal"),
                ("2026-01-02T00:00:00Z", "remove", "sdlc:goal")]}
    run = _sweep_runner(by_label={("sdlc:followup", "open"): [_issue(9, "sdlc:followup")]},
                        history=hist, fresh_state="OPEN", fresh_labels=("sdlc:followup",))
    result = rc.sweep_reconcile(".sdlc", cfg, apply=True, run=run)
    assert [a["issue"] for a in result["open_actions"]] == ["9"]
    assert result["open_actions"][0]["result"] == "done", result["open_actions"]
    docs = _mutation_docs(run)
    assert any("addLabelsToLabelable" in d for d in docs)


def test_zero_label_restore_of_something_other_than_goal_label_stays_proposed():
    """Test 3b. Restoring `sdlc:parked` (not `goal_label`) stays PROPOSED and human-reviewed --
    narrower than "the correction only ever adds or restores membership", exactly as the corrected
    design states."""
    rc = _mod("reconcile")
    cfg = _config()
    cfg["discovery"]["reconcile"] = {"mode": "on", "open_issue_mode": "on"}
    hist = {9: [("2026-01-01T00:00:00Z", "add", "sdlc:parked"),
                ("2026-01-02T00:00:00Z", "remove", "sdlc:parked")]}
    run = _sweep_runner(by_label={("sdlc:followup", "open"): [_issue(9, "sdlc:followup")]},
                        history=hist)
    result = rc.sweep_reconcile(".sdlc", cfg, apply=True, run=run)
    assert result["open_actions"] == []
    assert _mutation_docs(run) == []


def test_an_ambiguous_resolution_never_promotes():
    """Test 4. Inside the transaction window -- AMBIGUOUS -- never reaches the promotable set,
    regardless of which two labels are tied."""
    rc = _mod("reconcile")
    cfg = _config()
    cfg["discovery"]["reconcile"] = {"mode": "on", "open_issue_mode": "on"}
    hist = {226: [("2026-01-01T00:00:00Z", "add", "sdlc:goal"),
                  ("2026-01-01T00:00:04Z", "add", "sdlc:parked")]}   # 4s apart -- one transaction
    run = _sweep_runner(by_label={("sdlc:goal", "open"): [_issue(226, "sdlc:goal", "sdlc:parked")]},
                        history=hist)
    result = rc.sweep_reconcile(".sdlc", cfg, apply=True, run=run)
    assert result["open_actions"] == []
    assert _mutation_docs(run) == []


# ------------------------------------------------------------- the overlay-removal-impossible claim


def test_a_promoted_multi_label_correction_can_never_remove_an_overlay_by_construction():
    """Test 5 (part 1). The structural safety payoff `.sdlc/design/2287.md`'s detailed design
    section 2 claims: `compute_proposals` only ever adds an overlay label to a MULTI_LABEL entry's
    `remove` list when `winner != goal_label` -- so once condition 2 (`winner == goal_label`) holds,
    no PROMOTED correction can ever carry an overlay in its `remove` list, even when the issue
    genuinely carries one live. Verified directly against the REAL `compute_proposals` logic, not
    asserted from the design doc."""
    rc = _mod("reconcile"); src = _src(_runner())
    # goal_label + parked_label (2 primaries -> MULTI_LABEL) + in_progress_label (an overlay,
    # legitimately riding alongside membership) all live on the SAME issue.
    cen, primary = _cen_open([_issue(226, "sdlc:goal", "sdlc:parked", "sdlc:in-progress")], rc, src)
    # goal added LATER, 48h apart -- decisive, not ambiguous -- winner resolves to goal_label.
    hist = {226: [_ev(0, "add", "sdlc:parked"), _ev(48 * _H, "add", "sdlc:goal")]}
    props, unresolved = rc.compute_proposals(src, cen, primary, hist)
    assert unresolved == []
    assert len(props) == 1
    assert props[0]["winner"] == src.goal_label
    # THE CLAIM: even though sdlc:in-progress is live on this issue, it is not in `remove`.
    assert "sdlc:in-progress" not in props[0]["remove"]
    assert props[0]["remove"] == ["sdlc:parked"]


def test_a_non_promoted_multi_label_correction_removes_the_overlay_for_contrast():
    """Test 5 (part 2), the mirror case -- proves the guarantee is genuinely CONDITIONAL on
    `winner == goal_label`, not just "overlays are never touched" as a blanket claim. When the
    LOSING side is `goal_label` (parked wins), the overlay legitimately goes with it (#1393's own
    rule: an overlay is only ever legitimate alongside `goal_label`) -- correct PROPOSED-tier
    behaviour, and exactly why this entry is never promotable."""
    rc = _mod("reconcile"); src = _src(_runner())
    cen, primary = _cen_open([_issue(227, "sdlc:goal", "sdlc:parked", "sdlc:in-progress")], rc, src)
    hist = {227: [_ev(0, "add", "sdlc:goal"), _ev(48 * _H, "add", "sdlc:parked")]}
    props, unresolved = rc.compute_proposals(src, cen, primary, hist)
    assert unresolved == []
    assert props[0]["winner"] == "sdlc:parked"
    assert "sdlc:in-progress" in props[0]["remove"]        # legitimately removed here
    assert props[0]["winner"] != src.goal_label            # and therefore never promotable


# ------------------------------------------------------------------------ the stale-winner race


def test_stale_winner_race_is_caught_by_the_fresh_eligibility_recheck():
    """Test 6. Census-time saw MULTI_LABEL {sdlc:goal, sdlc:parked} resolving decisively to
    `sdlc:goal` (promotable, `remove: ["sdlc:parked"]`). Before the write executes, a human
    deliberately re-parks the issue -- removing `sdlc:goal` THEMSELVES, leaving only `sdlc:parked`.
    `apply_proposal`'s existing re-read would NOT catch this: it only asks "is sdlc:parked (the
    thing already queued for removal) still present?" -- yes it still is, so it would still remove
    it, silently undoing the human's own re-park and leaving the issue with NEITHER label. The fresh
    eligibility re-check must instead re-derive the WINNER from fresh history and catch that it is
    no longer `goal_label`."""
    rc = _mod("reconcile")
    stale_proposal = {"issue": "226", "klass": rc.MULTI_LABEL, "add": [], "remove": ["sdlc:parked"],
                      "evidence": "sdlc:goal is authoritative: ...", "winner": "sdlc:goal"}
    fresh_hist = {226: [("2026-01-01T00:00:00Z", "add", "sdlc:parked"),
                        ("2026-01-03T00:00:00Z", "add", "sdlc:goal"),
                        ("2026-01-04T00:00:00Z", "remove", "sdlc:goal")]}   # the human's re-park
    run = _sweep_runner(history=fresh_hist, fresh_state="OPEN", fresh_labels=("sdlc:parked",))
    src = _mod("reconcile").sources.GitHubSource(_config(), run=run)
    primary = rc._primary_labels(src, _config())
    out = rc.apply_open_issue_promotions(src, [stale_proposal], primary, src.goal_label, apply=True)
    assert out[0]["result"] == "skipped", out[0]
    assert out[0]["error"]
    # the write never happened at all -- the whole point of the re-check.
    assert _mutation_docs(run) == []


def test_zero_label_recheck_aborts_if_the_issue_already_regained_a_primary_label():
    """Test 7a (ZERO_LABEL fresh-recheck, round-2's flagged ambiguity). Between census and write,
    someone already restored a DIFFERENT primary label -- the issue is no longer ZERO_LABEL at all
    by write time. The re-check must confirm the issue is STILL missing `goal_label`, not just that
    the label it plans to add is absent."""
    rc = _mod("reconcile")
    stale_proposal = {"issue": "9", "klass": rc.ZERO_LABEL, "add": ["sdlc:goal"], "remove": [],
                      "evidence": "sdlc:goal was the last primary label, removed at ...",
                      "winner": "sdlc:goal"}
    run = _sweep_runner(
        fresh_state="OPEN", fresh_labels=("sdlc:parked", "sdlc:followup"),
        history={9: [("2026-01-01T00:00:00Z", "add", "sdlc:goal"),
                     ("2026-01-02T00:00:00Z", "remove", "sdlc:goal"),
                     ("2026-01-03T00:00:00Z", "add", "sdlc:parked")]})
    src = _mod("reconcile").sources.GitHubSource(_config(), run=run)
    primary = rc._primary_labels(src, _config())
    out = rc.apply_open_issue_promotions(src, [stale_proposal], primary, src.goal_label, apply=True)
    assert out[0]["result"] == "skipped", out[0]
    assert _mutation_docs(run) == []


def test_zero_label_recheck_aborts_if_a_different_label_is_now_the_correct_restore_candidate():
    """Test 7b (ZERO_LABEL fresh-recheck, the other half). Still genuinely ZERO_LABEL at write time,
    but `sdlc:parked` was added AND removed again more recently than `sdlc:goal` was -- the CORRECT
    restore candidate has changed since the census. The re-check must confirm the label being
    restored is still the right one, not only that the issue is still label-free."""
    rc = _mod("reconcile")
    stale_proposal = {"issue": "9", "klass": rc.ZERO_LABEL, "add": ["sdlc:goal"], "remove": [],
                      "evidence": "sdlc:goal was the last primary label, removed at ...",
                      "winner": "sdlc:goal"}
    run = _sweep_runner(
        fresh_state="OPEN", fresh_labels=("sdlc:followup",),
        history={9: [("2026-01-01T00:00:00Z", "add", "sdlc:goal"),
                     ("2026-01-02T00:00:00Z", "remove", "sdlc:goal"),
                     ("2026-01-03T00:00:00Z", "add", "sdlc:parked"),
                     ("2026-01-04T00:00:00Z", "remove", "sdlc:parked")]})
    src = _mod("reconcile").sources.GitHubSource(_config(), run=run)
    primary = rc._primary_labels(src, _config())
    out = rc.apply_open_issue_promotions(src, [stale_proposal], primary, src.goal_label, apply=True)
    assert out[0]["result"] == "skipped", out[0]
    assert _mutation_docs(run) == []


# ------------------------------------------------------------------------ the config gate (B-1)


def test_open_issue_promotion_mode_defaults_off():
    rc = _mod("reconcile")
    assert rc._open_issue_promotion_mode({}) == "off"
    assert rc._open_issue_promotion_mode({"discovery": {"reconcile": {"mode": "on"}}}) == "off"
    assert rc._open_issue_promotion_mode(
        {"discovery": {"reconcile": {"mode": "on", "open_issue_mode": "on"}}}) == "on"
    # a typo/unrecognised value reads as off -- a typo must never switch on a mechanism that WRITES.
    assert rc._open_issue_promotion_mode(
        {"discovery": {"reconcile": {"open_issue_mode": "yes"}}}) == "off"
    assert rc._open_issue_promotion_mode(
        {"discovery": {"reconcile": {"open_issue_mode": "on"}}}) == "on"   # mode absent -- own gate


def test_sweep_makes_zero_open_issue_calls_when_the_gate_is_off():
    """Test 8. With the new gate ABSENT (its real default), `sweep_reconcile` makes exactly the SAME
    `gh` calls it made before this slice existed -- byte-identical-when-off, not just an empty result
    -- for a MULTI_LABEL issue that WOULD have promoted had the gate been on."""
    rc = _mod("reconcile")
    cfg = _config()   # no discovery.reconcile block at all -- the real shipped default shape
    run = _sweep_runner(
        by_label={("sdlc:goal", "open"): [_issue(226, "sdlc:goal", "sdlc:parked")]},
        history={226: [("2026-01-01T00:00:00Z", "add", "sdlc:parked"),
                       ("2026-01-03T00:00:00Z", "add", "sdlc:goal")]})
    result = rc.sweep_reconcile(".sdlc", cfg, apply=True, run=run)
    assert result["open_actions"] == []
    docs = [a for c in run.calls for a in c if str(a).startswith("query=")]
    assert not any("timelineItems" in d for d in docs), "no history fetch when the gate is off"


def test_sweep_makes_zero_open_issue_calls_when_only_mode_is_on():
    """`discovery.reconcile.mode: "on"` alone (the existing closed-issue gate) must NOT silently
    start open-issue promotion -- it needs its OWN explicit opt-in, per B-1."""
    rc = _mod("reconcile")
    cfg = _config()
    cfg["discovery"]["reconcile"] = {"mode": "on"}   # open_issue_mode absent -- stays off
    run = _sweep_runner(
        by_label={("sdlc:goal", "open"): [_issue(226, "sdlc:goal", "sdlc:parked")]},
        history={226: [("2026-01-01T00:00:00Z", "add", "sdlc:parked"),
                       ("2026-01-03T00:00:00Z", "add", "sdlc:goal")]})
    result = rc.sweep_reconcile(".sdlc", cfg, apply=True, run=run)
    assert result["open_actions"] == []
    docs = [a for c in run.calls for a in c if str(a).startswith("query=")]
    assert not any("timelineItems" in d for d in docs)


# ------------------------------------------------------------------------------- the sweep cap


def test_open_issue_promotions_respect_the_per_sweep_cap():
    """Test 9. `MAX_CORRECTIONS_PER_SWEEP`-style cap, honoured by the newly-promoted open-issue
    corrections too, independent of the closed-issue tier's own use of the same constant."""
    rc = _mod("reconcile")
    cfg = _config()
    cfg["discovery"]["reconcile"] = {"mode": "on", "open_issue_mode": "on"}
    many = [_issue(n, "sdlc:goal", "sdlc:parked") for n in range(1, 60)]
    hist = {n: [("2026-01-01T00:00:00Z", "add", "sdlc:parked"),
                ("2026-01-03T00:00:00Z", "add", "sdlc:goal")] for n in range(1, 60)}
    run = _sweep_runner(by_label={("sdlc:goal", "open"): many}, history=hist)
    result = rc.sweep_reconcile(".sdlc", cfg, apply=False, run=run)   # dry-run: no per-item I/O
    assert len(result["open_actions"]) == rc.MAX_CORRECTIONS_PER_SWEEP
    assert all(a["result"] == "would" for a in result["open_actions"])


# ------------------------------------------------- remaining write-path failure/edge branches -----
# Beyond the 9 named tests above: this is the highest-risk slice (an unattended write path on OPEN
# issues), so every branch `apply_open_issue_promotions` can take gets its own test rather than
# being left to accidental coverage -- an untested failure branch on a write mechanism is exactly
# the "unexamined assumption" AGENTS.md's RELIABILITY bar warns against.


def test_open_issue_promotion_records_a_failed_write_rather_than_raising():
    """The mutation itself fails after passing every re-check -- must be recorded, not raised,
    mirroring `test_apply_records_a_failed_write_rather_than_raising` for the closed-issue tier."""
    rc = _mod("reconcile")
    stale_proposal = {"issue": "226", "klass": rc.MULTI_LABEL, "add": [], "remove": ["sdlc:parked"],
                      "evidence": "sdlc:goal is authoritative: ...", "winner": "sdlc:goal"}
    hist = {226: [("2026-01-01T00:00:00Z", "add", "sdlc:parked"),
                  ("2026-01-03T00:00:00Z", "add", "sdlc:goal")]}
    run = _sweep_runner(history=hist, fresh_state="OPEN", fresh_labels=("sdlc:goal", "sdlc:parked"),
                        swap_fails=True)
    src = _mod("reconcile").sources.GitHubSource(_config(), run=run)
    src._LABEL_SWAP_RETRY_BASE = 0
    primary = rc._primary_labels(src, _config())
    out = rc.apply_open_issue_promotions(src, [stale_proposal], primary, src.goal_label, apply=True)
    assert out[0]["result"] == "failed" and out[0]["error"]


def test_open_issue_promotion_skips_an_issue_already_in_the_proposed_state():
    """Idempotent skip: the queued removal is already absent by write time (a previous sweep, or a
    human, already cleaned it up) -- nothing left to do, and no mutation is attempted."""
    rc = _mod("reconcile")
    stale_proposal = {"issue": "226", "klass": rc.MULTI_LABEL, "add": [], "remove": ["sdlc:parked"],
                      "evidence": "sdlc:goal is authoritative: ...", "winner": "sdlc:goal"}
    hist = {226: [("2026-01-03T00:00:00Z", "add", "sdlc:goal")]}   # only ever carried goal -- decisive
    run = _sweep_runner(history=hist, fresh_state="OPEN", fresh_labels=("sdlc:goal",))
    src = _mod("reconcile").sources.GitHubSource(_config(), run=run)
    primary = rc._primary_labels(src, _config())
    out = rc.apply_open_issue_promotions(src, [stale_proposal], primary, src.goal_label, apply=True)
    assert out[0]["result"] == "skipped" and "already in the proposed state" in out[0]["error"]
    assert _mutation_docs(run) == []


def test_open_issue_promotion_aborts_if_the_issue_closed_since_the_census():
    """A close is also a state change the fresh re-check must catch -- this tier only ever means to
    touch OPEN issues."""
    rc = _mod("reconcile")
    stale_proposal = {"issue": "226", "klass": rc.MULTI_LABEL, "add": [], "remove": ["sdlc:parked"],
                      "evidence": "e", "winner": "sdlc:goal"}
    run = _sweep_runner(fresh_state="CLOSED", fresh_labels=("sdlc:goal",))
    src = _mod("reconcile").sources.GitHubSource(_config(), run=run)
    primary = rc._primary_labels(src, _config())
    out = rc.apply_open_issue_promotions(src, [stale_proposal], primary, src.goal_label, apply=True)
    assert out[0]["result"] == "skipped" and "no longer open" in out[0]["error"]
    assert _mutation_docs(run) == []
    assert run.unexpected == []              # #895: REST-first re-read, no `issue view`
    assert ["api", "repos/acme/widget/issues/226", "--method", "GET"] in run.calls


def test_open_issue_promotion_skips_when_the_fresh_reread_fails():
    """`gh` transport failure on the fresh re-read -- fail-closed, exactly like every other re-read
    in this module."""
    rc = _mod("reconcile")
    stale_proposal = {"issue": "226", "klass": rc.MULTI_LABEL, "add": [], "remove": ["sdlc:parked"],
                      "evidence": "e", "winner": "sdlc:goal"}

    def run(args):
        if args[:2] == ["issue", "view"] or gqlfake.rest_issue_target(args):
            raise RuntimeError("gh: HTTP 502 Bad Gateway")      # REST AND the one fallback both fail
        return ""
    src = _mod("reconcile").sources.GitHubSource(_config(), run=run)
    primary = rc._primary_labels(src, _config())
    out = rc.apply_open_issue_promotions(src, [stale_proposal], primary, src.goal_label, apply=True)
    assert out[0]["result"] == "skipped" and "could not re-read" in out[0]["error"]


def test_open_issue_promotion_skips_when_fresh_history_is_unavailable():
    """A batch history fetch failure (or genuinely empty history) at write-time fails OPEN to a
    skip, not a guess -- the same discipline `compute_proposals` already applies at census time."""
    rc = _mod("reconcile")
    stale_proposal = {"issue": "226", "klass": rc.MULTI_LABEL, "add": [], "remove": ["sdlc:parked"],
                      "evidence": "e", "winner": "sdlc:goal"}
    run = _sweep_runner(fresh_state="OPEN", fresh_labels=("sdlc:goal", "sdlc:parked"))  # no history
    src = _mod("reconcile").sources.GitHubSource(_config(), run=run)
    primary = rc._primary_labels(src, _config())
    out = rc.apply_open_issue_promotions(src, [stale_proposal], primary, src.goal_label, apply=True)
    assert out[0]["result"] == "skipped" and "no label history" in out[0]["error"]


def test_open_issue_promotion_skips_a_malformed_issue_number():
    """Defensive edge case -- `item["issue"]` should always be `str(int)` in practice, but a
    malformed value must abort before any I/O rather than raise or guess."""
    rc = _mod("reconcile")
    stale_proposal = {"issue": "not-a-number", "klass": rc.MULTI_LABEL, "add": [], "remove": [],
                      "evidence": "e", "winner": "sdlc:goal"}
    run = _sweep_runner()
    src = _mod("reconcile").sources.GitHubSource(_config(), run=run)
    primary = rc._primary_labels(src, _config())
    out = rc.apply_open_issue_promotions(src, [stale_proposal], primary, src.goal_label, apply=True)
    assert out[0]["result"] == "skipped" and "not a valid issue number" in out[0]["error"]
    assert run.calls == []                    # never even attempted a re-read


def test_zero_label_recheck_aborts_if_fresh_history_shows_no_removal_at_all():
    """The degenerate ZERO_LABEL case: still genuinely label-free at write time, but the FRESH
    history shows no primary label was ever removed -- nothing to restore from, so it must abort
    rather than guess."""
    rc = _mod("reconcile")
    stale_proposal = {"issue": "9", "klass": rc.ZERO_LABEL, "add": ["sdlc:goal"], "remove": [],
                      "evidence": "e", "winner": "sdlc:goal"}
    run = _sweep_runner(fresh_state="OPEN", fresh_labels=("sdlc:followup",),
                        history={9: [("2026-01-01T00:00:00Z", "add", "sdlc:followup")]})
    src = _mod("reconcile").sources.GitHubSource(_config(), run=run)
    primary = rc._primary_labels(src, _config())
    out = rc.apply_open_issue_promotions(src, [stale_proposal], primary, src.goal_label, apply=True)
    assert out[0]["result"] == "skipped"
    assert _mutation_docs(run) == []


def test_recompute_winner_returns_none_for_an_unrecognised_klass():
    """Pure-function edge case: `compute_proposals` only ever emits MULTI_LABEL/ZERO_LABEL, but an
    unrecognised value here is treated as unresolvable -- never promoted -- rather than guessed."""
    rc = _mod("reconcile")
    assert rc._recompute_winner("something-else", ("sdlc:goal",), set(), []) is None


def test_recompute_winner_treats_a_fresh_ambiguous_result_as_not_goal_label():
    """A fresh re-check that itself lands AMBIGUOUS (not just a clear different winner) must still
    read as `!= goal_label` without the caller having to special-case it."""
    rc = _mod("reconcile")
    primary = ("sdlc:goal", "sdlc:parked", "sdlc:needs-confirmation")
    events = [_ev(1000, "add", "sdlc:goal"), _ev(1004, "add", "sdlc:parked")]   # 4s apart
    assert rc._recompute_winner(rc.MULTI_LABEL, primary, set(), events) is None

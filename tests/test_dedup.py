"""agrim-scope's board-dedup check (dedup.py, #917): "does ANYTHING on the board already cover
this rough idea" search, run at BRAINSTORM time (before any issue exists for the idea yet) -- as
opposed to backlog_check.cross_check(), which asks "is THIS SPECIFIC, already-filed goal a
duplicate of another specific goal" at PICK time. Reuses backlog_check's TF-IDF/cosine primitives
directly (no reimplementation); see dedup.py's own module docstring for the full threshold
rationale. Hermetic, deterministic, $0 -- same discipline as test_backlog_check.py, whose `_gh_base`/
`_rec` board-mirror fixture helpers this file reuses rather than duplicating."""
import importlib.util, json, pathlib, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-scope" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


# reuse the sibling skill's own board-mirror fixture helpers (same convention test_backlog_check.py
# itself follows, reusing test_handoff.py's FakeSource) rather than a second, divergent copy
from test_backlog_check import _gh_base, _rec

# --- fixtures: three board issues + a free-text brainstorm target, tuned (see dedup.py's module
# docstring) so the three bands are cleanly separated: a clear duplicate scores well above the
# duplicate_threshold, a related-but-distinct issue scores between the surfacing floor and the
# duplicate_threshold, and a genuinely unrelated issue scores below the surfacing floor entirely.
_DUP_TITLE = "live count of parked goals on the dashboard"
_DUP_BODY = ("Show a live count of goals that are currently parked waiting on a blocker, right on "
             "the dashboard, so nobody has to grep logs to find stuck work.")
_UNRELATED_TITLE = "dashboard dark mode toggle"
_UNRELATED_BODY = ("Add a dark and light theme toggle button to the top of the dashboard so the UI "
                    "matches OS level theme preference.")
_RELATED_TITLE = "weekly digest email of parked goals and blockers"
_RELATED_BODY = ("Send a weekly digest email listing which goals are parked and which blocker each "
                  "one is waiting on, as a lighter alternative to a live dashboard somebody has to "
                  "remember to open.")
_TARGET = ("build a small dashboard panel that shows how many goals are parked waiting on a blocker "
           "right now, so a session can see stuck work without grepping logs")
_UNRELATED_TARGET = "the retry backoff for flaky network calls in the ledger sync should use jitter"

# a loose closed-window so a recently-closed fixture always counts as "recent" without depending on
# wall-clock `now` in most tests (tests that care about the window pass their own `now` + a tight one)
_LOOSE_BC = {"closed_window_days": 3650}


def _base(d, records, dedup=None):
    base = _gh_base(d, records, **_LOOSE_BC)
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    if dedup is not None:
        cfg["dedup"] = dedup
    (pathlib.Path(base) / "config.json").write_text(json.dumps(cfg))
    return base


def _board():
    return [
        _rec(50, _DUP_TITLE, _DUP_BODY),
        _rec(60, _UNRELATED_TITLE, _UNRELATED_BODY),
        _rec(70, _RELATED_TITLE, _RELATED_BODY),
    ]


# --- the three required bands: clear duplicate / clearly unrelated / borderline-related ---

def test_clear_duplicate_is_surfaced_ranked_first_and_labeled_duplicate():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board())
        pack = dd.find_candidates(base, _TARGET)
        assert pack["schema"] == "brainstorm-dedup/v1"
        refs = [c["ref"] for c in pack["candidates"]]
        assert refs[0] == "50"                                   # ranked first: highest score
        top = pack["candidates"][0]
        assert top["strength"] == "duplicate"
        assert top["state"] == "open"
        assert top["score"] >= dd._DEFAULT_DUPLICATE_THRESHOLD
        assert top["evidence"]                                    # carries shared-term evidence


def test_clearly_unrelated_target_produces_no_false_positive():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board())
        pack = dd.find_candidates(base, _UNRELATED_TARGET)
        assert pack["candidates"] == []


def test_borderline_related_but_distinct_is_surfaced_but_not_labeled_a_duplicate():
    # #70 shares real vocabulary with the target (parked goals, blockers, dashboard) but is a
    # genuinely distinct deliverable (a weekly email digest vs. a live dashboard panel). Per the
    # issue's own design lean ("surface, don't silently block" -- a human may want related-but-
    # distinct work planned alongside something similar), this function surfaces it rather than
    # dropping it: a false negative here (silently planning a second, overlapping piece of work)
    # is worse than a false positive the human can just wave off. It must still rank BELOW, and
    # score BELOW, the actual duplicate -- and it must be labeled "related", not "duplicate", so a
    # caller can tell the two apart without re-deriving the score band itself.
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board())
        pack = dd.find_candidates(base, _TARGET)
        by_ref = {c["ref"]: c for c in pack["candidates"]}
        assert "70" in by_ref
        related = by_ref["70"]
        dup = by_ref["50"]
        assert related["strength"] == "related"
        assert dd._DEFAULT_THRESHOLD <= related["score"] < dd._DEFAULT_DUPLICATE_THRESHOLD
        assert related["score"] < dup["score"]                    # ranks behind the real duplicate
        # and the unrelated issue never crosses the surfacing floor at all
        assert "60" not in by_ref


# --- self-reference exclusion (the target text IS an existing issue's own body) ---

def test_self_reference_excluded_via_exclude_refs():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board() + [_rec(80, _DUP_TITLE, _DUP_BODY)])
        # #80 is a verbatim copy of #50 -- as if #916 resolved the target to #80's OWN text. Without
        # exclusion #80 would trivially "duplicate" itself at score ~1.0.
        pack = dd.find_candidates(base, _DUP_TITLE + "\n" + _DUP_BODY, exclude_refs=["80"])
        refs = {c["ref"] for c in pack["candidates"]}
        assert "80" not in refs
        assert "50" in refs                                       # the real duplicate still surfaces


# --- recently-closed vs. stale-closed (the "plausibly recently-closed" corpus slice) ---

def test_recently_closed_duplicate_surfaced_but_stale_closed_one_is_not():
    dd = _mod("dedup")
    now = 1_754_179_200  # 2025-08-03T00:00:00Z, arbitrary fixed epoch
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, [_rec(50, _DUP_TITLE, _DUP_BODY, state="closed",
                              closed_at="2025-08-01T00:00:00Z")],
                    dedup={"threshold": 0.1})
        cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
        cfg["backlog_check"]["closed_window_days"] = 30
        (pathlib.Path(base) / "config.json").write_text(json.dumps(cfg))
        pack = dd.find_candidates(base, _TARGET, now=now)
        recent = {c["ref"]: c for c in pack["candidates"]}
        assert "50" in recent and recent["50"]["state"] == "closed"

    with tempfile.TemporaryDirectory() as d:
        base = _base(d, [_rec(50, _DUP_TITLE, _DUP_BODY, state="closed",
                              closed_at="2025-01-01T00:00:00Z")],
                    dedup={"threshold": 0.1})
        cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
        cfg["backlog_check"]["closed_window_days"] = 30
        (pathlib.Path(base) / "config.json").write_text(json.dumps(cfg))
        pack = dd.find_candidates(base, _TARGET, now=now)
        assert pack["candidates"] == []                            # closed too long ago -> excluded


# --- config overrides ---

def test_config_overrides_threshold_and_top_k():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board(), dedup={"threshold": 0.99, "top_k": 8})
        pack = dd.find_candidates(base, _TARGET)
        assert pack["candidates"] == []                            # an unreachable floor -> nothing qualifies

    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board(), dedup={"threshold": 0.01, "top_k": 1})
        pack = dd.find_candidates(base, _TARGET)
        assert len(pack["candidates"]) == 1                        # top_k caps the returned list


# --- fail-open / edge cases ---

def test_empty_text_returns_empty_pack_with_reason():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board())
        pack = dd.find_candidates(base, "   ")
        assert pack["candidates"] == [] and "empty_text" in pack["degraded"]


def test_no_corpus_fails_open_not_raises():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, [])
        pack = dd.find_candidates(base, _TARGET)
        assert pack["candidates"] == []
        assert pack["degraded"]                                    # a reason is reported, no raise


def test_missing_sdlc_dir_fails_open():
    dd = _mod("dedup")
    pack = dd.find_candidates("/nonexistent/path/does/not/exist", _TARGET)
    assert pack["candidates"] == []
    assert pack["schema"] == "brainstorm-dedup/v1"


def test_text_that_tokenizes_to_nothing_reports_no_tokens():
    # non-empty text, but entirely stopwords/short tokens -- `_doc_tokens` legitimately yields an
    # empty Counter, a different failure mode than "empty_text" (blank/whitespace-only input).
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board())
        pack = dd.find_candidates(base, "a an it is do does the to of")
        assert pack["candidates"] == [] and "no_tokens" in pack["degraded"]


def test_unexpected_error_fails_open_with_degraded_error(monkeypatch):
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board())

        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(dd.backlog_check, "_idf", _boom)
        pack = dd.find_candidates(base, _TARGET)
        assert pack == {"schema": "brainstorm-dedup/v1", "candidates": [], "degraded": ["error"]}


# --- CLI ---

def test_cli_main_reads_free_text_arg_and_prints_json():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board())
        out = dd.main([  "dedup.py", base, _TARGET])
        assert out == 0


def test_cli_main_reads_file_reference_with_at_prefix(capsys):
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board())
        target_file = pathlib.Path(d) / "target.md"
        target_file.write_text(_TARGET)
        rc = dd.main(["dedup.py", base, "@" + str(target_file)])
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["schema"] == "brainstorm-dedup/v1"
        assert any(c["ref"] == "50" for c in printed["candidates"])


def test_cli_main_usage_error_on_missing_args():
    dd = _mod("dedup")
    assert dd.main(["dedup.py"]) == 2


# --- #1204: an explicit `records=` corpus override -- handoff.py's file-time duplicate search
# supplies its OWN, differently-scoped record set (mirror.fetch_dependency_records, unrestricted
# by discovery.github.assignee) rather than the cached, narrower board-mirror.ndjson _build_corpus
# reads by default -- so `records=` must bypass `_build_corpus`/the mirror file entirely.

def test_records_override_bypasses_the_mirror_file_entirely():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        # an EMPTY mirror on disk -- if find_candidates still consulted it, this would score nothing
        base = _base(d, [])
        override = [_rec(50, _DUP_TITLE, _DUP_BODY)]
        pack = dd.find_candidates(base, _TARGET, records=override)
        refs = [c["ref"] for c in pack["candidates"]]
        assert refs == ["50"]


def test_records_override_none_falls_back_to_the_mirror_as_before():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board())
        with_default = dd.find_candidates(base, _TARGET)
        with_explicit_none = dd.find_candidates(base, _TARGET, records=None)
        assert with_default == with_explicit_none


def test_records_override_empty_list_is_a_real_corpus_not_a_fallback():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, _board())     # a real, non-empty mirror on disk
        # an explicit EMPTY override must win over the mirror, not silently fall back to it
        pack = dd.find_candidates(base, _TARGET, records=[])
        assert pack["candidates"] == [] and "no_corpus" in pack["degraded"]


def test_records_override_still_honors_config_thresholds_and_exclude_refs():
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, [], dedup={"threshold": 0.99})
        override = [_rec(50, _DUP_TITLE, _DUP_BODY)]
        assert dd.find_candidates(base, _TARGET, records=override)["candidates"] == []

    with tempfile.TemporaryDirectory() as d:
        base = _base(d, [])
        override = [_rec(50, _DUP_TITLE, _DUP_BODY)]
        pack = dd.find_candidates(base, _TARGET, records=override, exclude_refs=["50"])
        assert pack["candidates"] == []


def test_records_override_still_fails_open_on_an_unexpected_error(monkeypatch):
    dd = _mod("dedup")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, [])

        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(dd.backlog_check, "_idf", _boom)
        pack = dd.find_candidates(base, _TARGET, records=[_rec(50, _DUP_TITLE, _DUP_BODY)])
        assert pack == {"schema": "brainstorm-dedup/v1", "candidates": [], "degraded": ["error"]}

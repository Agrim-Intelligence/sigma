"""#2363 (slice B of epic #2260's completion work): the 4-tier AI-judgment classifier that
replaces #2263's `core` sentinel.

Three things this file pins, mirroring `test_feature_labels.py`'s own opening claims for the
sibling module:

  - `classify()` is the deterministic tier chain, one test per tier, driven by hand-constructed
    `Judgment`s -- the "language understanding" half is never this module's job, so every test
    here supplies the judgment a real caller would have already decided.
  - The chain NEVER creates a `feature:*` label, a `feature/*` branch, or a registry entry, at ANY
    tier -- asserted against the recorded `gh` call log and the registry directory's own contents,
    never against a return value (the same discipline `test_feature_labels.py`'s own never-create
    tests use).
  - `classify_at_pick`/`classify_for_filing` are the two integration points, and both default to
    `ABSTAIN` (tier 2, the catch-all) when no real judge is supplied.
"""
import importlib.util
import json
import pathlib
import tempfile

import gqlfake

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


fc = _mod("feature_classify")
fl = _mod("feature_labels")
fr = _mod("feature_registry")


def _clean_unknown_labels():
    gqlfake._UNKNOWN_LABELS.clear()


# ------------------------------------------------------------------------------------- fixtures
#
# Deliberately slimmer than `test_feature_labels.py`'s own -- this module's chain never reads
# `discovery.no_dangling_goal.*` off a Source (that gating lives one level up, in
# `feature_labels._handle_no_unit_at_pick` and `handoff._auto_classify_unit`), and `core` is
# always an explicit PARAMETER here, never resolved from config.

_CONFIG = {"discovery": {"source": "github", "github": {"repo": "o/r"}}}


def _runner(labels=(), comments=(), absent=()):
    """Fake `gh`, recording every call. `labels` is the issue's live label set; `absent` names
    labels the REPOSITORY does not have. Mirrors `test_feature_labels.py::_runner`, trimmed to
    what this module's own tests need (no issue body/title -- `feature_classify` never reads
    either)."""
    calls = []
    live = set(labels)
    gqlfake._UNKNOWN_LABELS.update(absent)

    def run(args):
        gql = gqlfake.swap(args, labels=live, calls=calls, repo_args=("--repo", "o/r"))
        if gql is not None:
            return gql
        calls.append(list(args))
        rest = gqlfake.rest_issue(args, lambda n, f: {      # #895: sources' issue reads are REST first
            "comments": [{"body": c} for c in comments], "labels": [{"name": n} for n in sorted(live)],
            "body": ""})
        if rest is not None:
            return rest
        if len(args) >= 2 and args[0] == "issue" and args[1] == "view":
            fields = (args[args.index("--json") + 1] if "--json" in args else "").split(",")
            out = {}
            if "comments" in fields:
                out["comments"] = [{"body": c} for c in comments]
            if "labels" in fields:
                out["labels"] = [{"name": n} for n in sorted(live)]
            if "body" in fields:
                out["body"] = ""
            return json.dumps(out)
        return ""

    run.calls = calls
    run.labels = live
    return run


def _source(run):
    gh = _mod("sources").GitHubSource(_CONFIG, run=run)
    gh._LABEL_SWAP_RETRIES, gh._LABEL_SWAP_RETRY_BASE = 1, 0
    return gh


def _sdlc(d):
    base = pathlib.Path(d) / ".sdlc"
    (base / "state").mkdir(parents=True)
    cfg = dict(_CONFIG)
    cfg["ledger"] = {"enabled": True, "actor": "me", "lease": {"ttl_hours": 0}}
    (base / "config.json").write_text(json.dumps(cfg))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    return str(base), cfg


def _registry_dir(base):
    return fr.registry_dir(base)


def _write_unit(base, name, **over):
    entry = {"title": "t", "owner": None, "open": True, "parent": None,
             "tracking_issue": None, "priority": None, "repos": {}}
    entry.update(over)
    fr.write_unit(_registry_dir(base), name, entry)
    return entry


def _notes(base, goal=None):
    ledger = _mod("ledger")
    return [e for e in ledger.read_all(base)
            if e.get("kind") == "note" and (goal is None or e.get("goal") == str(goal))]


def _tokens(base, goal=None):
    return [str(e.get("why") or "").split(" ", 1)[0] for e in _notes(base, goal)]


def _registry_files(base):
    """Every path under `.sdlc/features/` -- used by the never-creates-a-registry-entry test to
    assert on the actual filesystem contents, not merely on what `read()` reports."""
    rdir = _registry_dir(base)
    if not rdir.is_dir():
        return set()
    return {str(p.relative_to(rdir)) for p in rdir.rglob("*") if p.is_file()}


# =====================================================================================================
# tier 1: a single existing unit, open or closed
# =====================================================================================================

def test_tier1_attaches_to_a_single_open_unit():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "voice-interview", open=True)
        judgment = fc.make_judgment(unit="voice-interview")
        decision = fc.classify(base, gh, "42", cfg, core=None, judgment=judgment)
        tokens = _tokens(base, "42")
    assert decision.proceed is True
    assert decision.outcome == fc.TIER1_ATTACHED
    assert decision.unit == "voice-interview"
    assert "feature:voice-interview" in run.labels
    posted = [c[-1] for c in run.calls if c[:2] == ["issue", "comment"]]
    assert len(posted) == 1 and fc.TIER1_MARKER in posted[0], posted
    assert tokens == [fc.TIER1_TOKEN]


def test_tier1_reopens_a_closed_unit_preserving_every_other_field():
    """THE field-preservation assertion: a read-modify-write of the FULL entry, only `open` flips."""
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        before = _write_unit(base, "legacy-import", open=False, owner="@retired-owner",
                             parent="platform", tracking_issue="9001", priority="P2",
                             repos={"o/r": {"branch": "feature/legacy-import", "owner": "@x",
                                            "authorized": True, "goals": [11, 12]}})
        judgment = fc.make_judgment(unit="legacy-import")
        decision = fc.classify(base, gh, "42", cfg, core=None, judgment=judgment)
        after = fr.read(_registry_dir(base))["legacy-import"]
    assert decision.proceed is True
    assert decision.outcome == fc.TIER1_REOPENED
    assert decision.unit == "legacy-import"
    assert "feature:legacy-import" in run.labels
    # every field except `open` survives byte-for-byte
    assert after["open"] is True and before["open"] is False
    for key in ("title", "owner", "parent", "tracking_issue", "priority", "repos"):
        assert after[key] == before[key], (key, after[key], before[key])
    posted = [c[-1] for c in run.calls if c[:2] == ["issue", "comment"]]
    assert len(posted) == 1 and "reopened" in posted[0].lower(), posted


def test_tier1_ignores_a_judgment_naming_an_unregistered_unit():
    """A judgment (any judge, real or default) can name a unit that is not actually registered --
    the chain must VALIDATE, not trust, and fall through to the next tier rather than act on an
    unverified claim."""
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "core", open=True)
        judgment = fc.make_judgment(unit="does-not-exist")
        decision = fc.classify(base, gh, "42", cfg, core="core", judgment=judgment)
    assert decision.outcome == fc.TIER2_ATTACHED           # fell through to the catch-all
    assert decision.unit == "core"
    assert "feature:does-not-exist" not in run.labels


def test_tier1_ignores_multiple_even_with_a_unit_named():
    """`multiple=True` selects tier 2 regardless of whether `unit` is also set -- tier 1 requires a
    SINGLE confident match, and a judgment claiming both is treated as the ambiguous one."""
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "voice-interview", open=True)
        _write_unit(base, "core", open=True)
        judgment = fc.Judgment(unit="voice-interview", multiple=True,
                               evidence_name=None, evidence_path=None)
        decision = fc.classify(base, gh, "42", cfg, core="core", judgment=judgment)
    assert decision.outcome == fc.TIER2_ATTACHED
    assert decision.unit == "core"


# =====================================================================================================
# tier 2: multiple plausible units, or root/base-level work -> the configured catch-all
# =====================================================================================================

def test_tier2_attaches_to_the_configured_catch_all():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "core", open=True)
        decision = fc.classify(base, gh, "42", cfg, core="core", judgment=fc.ABSTAIN)
        tokens = _tokens(base, "42")
    assert decision.proceed is True
    assert decision.outcome == fc.TIER2_ATTACHED
    assert decision.unit == "core"
    assert "feature:core" in run.labels
    posted = [c[-1] for c in run.calls if c[:2] == ["issue", "comment"]]
    assert len(posted) == 1 and fc.TIER2_MARKER in posted[0], posted
    assert tokens == [fc.TIER2_TOKEN]


def test_tier2_falls_through_when_the_catch_all_is_not_a_known_open_unit():
    """A misconfigured or closed catch-all must not attach a phantom label -- it falls through
    (here, to tier 4, since no evidence was ever offered)."""
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        decision = fc.classify(base, gh, "42", cfg, core="does-not-exist", judgment=fc.ABSTAIN)
    assert decision.outcome == fc.TIER4_NEEDS_TRIAGE
    assert "feature:does-not-exist" not in run.labels


def test_tier2_is_skipped_entirely_when_no_core_is_configured():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        decision = fc.classify(base, gh, "42", cfg, core=None, judgment=fc.ABSTAIN)
    assert decision.outcome == fc.TIER4_NEEDS_TRIAGE


# =====================================================================================================
# tier 3: an identifiable but unregistered component, with concrete evidence
# =====================================================================================================

def test_tier3_sets_aside_with_a_suggestion_when_evidence_exists():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        project_root = pathlib.Path(base).parent
        (project_root / "voice").mkdir()
        (project_root / "voice" / "engine.py").write_text("# real file\n")
        judgment = fc.make_judgment(evidence_name="voice-interview",
                                    evidence_path="voice/engine.py")
        decision = fc.classify(base, gh, "42", cfg, core=None, judgment=judgment)
    assert decision.proceed is False
    assert decision.outcome == fc.TIER3_SET_ASIDE
    assert decision.outcome == fl.SET_ASIDE_NO_UNIT           # the SAME mechanism, reused not copied
    assert "sdlc:needs-unit" in run.labels
    posted = [c[-1] for c in run.calls if c[:2] == ["issue", "comment"]]
    assert len(posted) == 1
    assert fl.NEEDS_UNIT_MARKER in posted[0]
    assert "voice-interview" in posted[0] and "voice/engine.py" in posted[0]
    # the SUGGESTION is a plain sentence naming the evidence -- never a bare declaration line a
    # future `features.parse_body` read of the (unrelated) issue BODY could ever be confused with
    assert "Feature: voice-interview" not in posted[0]


def test_tier3_never_writes_the_suggestion_into_the_issue_body():
    """The critical rule this slice's own issue names: the suggested name is a COMMENT, never the
    body -- `create_tracked_issue`/`stamp_body` are not even in this call path, so there is no body
    to write to at all; this pins that `classify()` itself never tries."""
    import inspect
    src = inspect.getsource(fc._tier3)
    assert "append_to_body" not in src and ".body" not in src


def test_tier3_falls_through_to_tier4_when_evidence_is_missing():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        judgment = fc.make_judgment(evidence_name="voice-interview",
                                    evidence_path="voice/does-not-exist.py")
        decision = fc.classify(base, gh, "42", cfg, core=None, judgment=judgment)
    assert decision.outcome == fc.TIER4_NEEDS_TRIAGE
    assert "sdlc:needs-unit" not in run.labels


def test_tier3_falls_through_to_tier4_when_the_name_is_missing():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        project_root = pathlib.Path(base).parent
        (project_root / "voice").mkdir()
        judgment = fc.make_judgment(evidence_name=None, evidence_path="voice")
        decision = fc.classify(base, gh, "42", cfg, core=None, judgment=judgment)
    assert decision.outcome == fc.TIER4_NEEDS_TRIAGE


def test_tier3_evidence_path_may_be_a_directory_or_absolute():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        project_root = pathlib.Path(base).parent
        target = project_root / "billing"
        target.mkdir()
        judgment = fc.make_judgment(evidence_name="billing", evidence_path=str(target))
        decision = fc.classify(base, gh, "42", cfg, core=None, judgment=judgment)
    assert decision.outcome == fc.TIER3_SET_ASIDE


def test_tier3_falls_through_when_a_relative_evidence_path_traverses_outside_the_repo():
    """A live-judge review finding: existence alone is not containment. A hallucinated or
    adversarially-injected `../../../../etc/passwd` must never confirm tier 3 -- it would quote the
    path verbatim in a public GitHub comment, turning this check into a file-existence oracle for
    the whole host, not just the repo."""
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        project_root = pathlib.Path(base).parent
        outside = project_root.parent / ("outside-%s" % project_root.name)
        outside.mkdir()
        (outside / "secret.txt").write_text("not this repo's\n")
        traversal = "../%s/secret.txt" % outside.name
        assert (project_root / traversal).resolve().exists()  # the file genuinely exists on disk
        judgment = fc.make_judgment(evidence_name="secret", evidence_path=traversal)
        decision = fc.classify(base, gh, "42", cfg, core=None, judgment=judgment)
    assert decision.outcome == fc.TIER4_NEEDS_TRIAGE
    assert "sdlc:needs-unit" not in run.labels


def test_tier3_falls_through_when_an_absolute_evidence_path_is_outside_the_repo():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        project_root = pathlib.Path(base).parent
        with tempfile.TemporaryDirectory() as other:
            outside_file = pathlib.Path(other) / "id_rsa"
            outside_file.write_text("not this repo's either\n")
            judgment = fc.make_judgment(evidence_name="keys", evidence_path=str(outside_file))
            decision = fc.classify(base, gh, "42", cfg, core=None, judgment=judgment)
    assert decision.outcome == fc.TIER4_NEEDS_TRIAGE
    assert "sdlc:needs-unit" not in run.labels


def test_evidence_exists_accepts_a_relative_path_that_resolves_back_inside_the_repo():
    """The containment check must not be so strict it rejects a legitimate path that merely
    contains a `..` segment resolving back inside the project root (e.g. `sub/../voice/engine.py`)
    -- only genuine escapes are rejected."""
    with tempfile.TemporaryDirectory() as d:
        base, _ = _sdlc(d)
        project_root = pathlib.Path(base).parent
        (project_root / "voice").mkdir()
        (project_root / "voice" / "engine.py").write_text("# real file\n")
        (project_root / "sub").mkdir()
        assert fc._evidence_exists(base, "sub/../voice/engine.py") is True


# =====================================================================================================
# tier 4: genuinely unknown, expected rare
# =====================================================================================================

def test_tier4_full_flow_creates_and_attaches_needs_triage():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        decision = fc.classify(base, gh, "42", cfg, core=None, judgment=fc.ABSTAIN.
                               _replace(multiple=False))
        tokens = _tokens(base, "42")
    assert decision.proceed is False
    assert decision.outcome == fc.TIER4_NEEDS_TRIAGE
    assert decision.unit is None
    assert "sdlc:needs-triage" in run.labels
    posted = [c[-1] for c in run.calls if c[:2] == ["issue", "comment"]]
    assert len(posted) == 1 and fc.TIER4_MARKER in posted[0], posted
    assert "tier 1" in posted[0] and "tier 2" in posted[0] and "tier 3" in posted[0]
    assert tokens == [fc.TIER4_TOKEN]


def test_tier4_needs_triage_label_is_created_when_missing():
    """`sources.mark_needs_triage` creates the label through `_ensure_labels` -- the SAME
    best-effort, `--force`-free mechanism every other lifecycle label in this class already uses --
    so a repo that has never seen `sdlc:needs-triage` before still gets it, with no separate `gh
    label create` call anywhere in this module."""
    _clean_unknown_labels()
    run = _runner(absent={"sdlc:needs-triage"})
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        fc.classify(base, gh, "42", cfg, core=None, judgment=fc.ABSTAIN._replace(multiple=False))
    creates = {c[2]: c for c in run.calls if c[:2] == ["label", "create"]}
    assert "sdlc:needs-triage" in creates


def test_tier4_degrades_to_needs_unit_when_the_source_has_no_mark_needs_triage():
    """A source that predates #2363 (or a test double missing the method) must not leave the issue
    silently pickable -- it degrades to the existing sdlc:needs-unit mechanism rather than raising
    inside the pick path."""
    class NoTriageSurface:
        def __init__(self):
            self.calls = []
            self.labels = set()

        def attach_label(self, goal, name):
            return False

        def mark_needs_unit(self, goal):
            self.calls.append("mark")
            return True

        def fetch_comments_strict(self, goal):
            return {"comments": []}

        def note(self, goal, text):
            self.calls.append(("note", text))

    src = NoTriageSurface()
    assert not hasattr(src, "mark_needs_triage")
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        decision = fc.classify(base, src, "42", cfg, core=None,
                               judgment=fc.ABSTAIN._replace(multiple=False))
    assert decision.outcome == fl.SET_ASIDE_NO_UNIT
    assert "mark" in src.calls


# =====================================================================================================
# the never-creates guarantee, at every tier
# =====================================================================================================

def test_the_classifier_never_creates_a_feature_label_branch_or_registry_entry_at_any_tier():
    """THE hardest requirement in this slice, asserted at every tier in ONE pass over the recorded
    `gh` call log and the real registry directory -- never against a return value, the same
    discipline `test_feature_labels.py::test_gh_label_create_is_never_invoked_for_a_feature_label`
    already uses for the sibling guarantee."""
    _clean_unknown_labels()

    def _feature_label_creates(run):
        return [c for c in run.calls if c[:2] == ["label", "create"]
                and any(str(a).lower().startswith("feature:") for a in c)]

    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "voice-interview", open=True)
        _write_unit(base, "legacy-import", open=False)
        _write_unit(base, "core", open=True)
        before_files = _registry_files(base)

        # tier 1 (open)
        run1 = _runner(); gh1 = _source(run1)
        fc.classify(base, gh1, "1", cfg, core="core",
                   judgment=fc.make_judgment(unit="voice-interview"))
        # tier 1 (closed -> reopened)
        run2 = _runner(); gh2 = _source(run2)
        fc.classify(base, gh2, "2", cfg, core="core",
                   judgment=fc.make_judgment(unit="legacy-import"))
        # tier 2
        run3 = _runner(); gh3 = _source(run3)
        fc.classify(base, gh3, "3", cfg, core="core", judgment=fc.ABSTAIN)
        # tier 3
        run4 = _runner(); gh4 = _source(run4)
        project_root = pathlib.Path(base).parent
        (project_root / "voice2").mkdir()
        fc.classify(base, gh4, "4", cfg, core="core",
                   judgment=fc.make_judgment(evidence_name="voice2", evidence_path="voice2"))
        # tier 4
        run5 = _runner(); gh5 = _source(run5)
        fc.classify(base, gh5, "5", cfg, core=None,
                   judgment=fc.ABSTAIN._replace(multiple=False))

        after_files = _registry_files(base)
        # and no new `feature:*` NAME entered the registry at all
        registry_names = set(fr.read(_registry_dir(base)))

    for run in (run1, run2, run3, run4, run5):
        assert _feature_label_creates(run) == [], run.calls
    # a NEW unit file (e.g. `does-not-exist.json`) would show up here -- it never does
    assert after_files == before_files, (before_files, after_files)
    assert registry_names == {"voice-interview", "legacy-import", "core"}


# =====================================================================================================
# classify_at_pick: the pick-time integration point, and its default judge
# =====================================================================================================

def test_registry_context_reports_every_unit_open_or_closed():
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "voice-interview", open=True, title="Voice interview flow")
        _write_unit(base, "legacy-import", open=False, title="Legacy import path")
        context = fc.registry_context(base)
    assert context["voice-interview"] == {"title": "Voice interview flow", "open": True}
    assert context["legacy-import"] == {"title": "Legacy import path", "open": False}


def test_classify_at_pick_with_no_judge_abstains_to_the_catch_all():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "core", open=True)
        decision = fc.classify_at_pick(base, gh, "42", cfg, core="core")
    assert decision.outcome == fc.TIER2_ATTACHED
    assert decision.unit == "core"


def test_classify_at_pick_uses_a_real_judge_when_one_is_supplied():
    _clean_unknown_labels()
    run = _runner()
    gh = _source(run)

    def judge(sdlc_dir, source, goal, config, context):
        assert "voice-interview" in context
        return fc.make_judgment(unit="voice-interview")

    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "voice-interview", open=True)
        _write_unit(base, "core", open=True)
        decision = fc.classify_at_pick(base, gh, "42", cfg, core="core", judge=judge)
    assert decision.outcome == fc.TIER1_ATTACHED
    assert decision.unit == "voice-interview"


# =====================================================================================================
# classify_for_filing: the filing-time integration point (tier 1/2 equivalent only)
# =====================================================================================================

def test_classify_for_filing_with_no_judge_resolves_the_catch_all():
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "core", open=True)
        resolved = fc.classify_for_filing(base, source=None, config=cfg, core="core")
    assert resolved == "core"


def test_classify_for_filing_returns_none_with_no_catch_all_and_no_judge():
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        resolved = fc.classify_for_filing(base, source=None, config=cfg, core=None)
    assert resolved is None


def test_classify_for_filing_never_reopens_a_closed_unit():
    """Filing-time targeting resolves through `resolve_open_unit`, never `resolve_any_unit` --
    unlike pick-time tier 1, it must NOT reopen a closed unit a real judge happens to name."""
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "legacy-import", open=False)

        def judge(sdlc_dir, source, goal, config, context):
            return fc.make_judgment(unit="legacy-import")

        resolved = fc.classify_for_filing(base, source=None, config=cfg, core=None, judge=judge)
        still_closed = fr.read(_registry_dir(base))["legacy-import"]["open"] is False
    assert resolved is None
    assert still_closed                                        # untouched


def test_classify_for_filing_prefers_a_confident_single_match_over_the_catch_all():
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "voice-interview", open=True)
        _write_unit(base, "core", open=True)

        def judge(sdlc_dir, source, goal, config, context):
            return fc.make_judgment(unit="voice-interview")

        resolved = fc.classify_for_filing(base, source=None, config=cfg, core="core", judge=judge)
    assert resolved == "voice-interview"


# =====================================================================================================
# classify_for_filing: #2383 (slice H) -- threading issue_title/issue_body into `context`
# =====================================================================================================

def test_classify_for_filing_threads_issue_title_body_into_context_as_pending_issue():
    """The new content channel: `issue_title`/`issue_body`, when given, must reach the judge's
    `context` under `_pending_issue`, verbatim, alongside the registry entries -- and `goal` must
    still be `None`, exactly as documented."""
    captured = {}

    def judge(sdlc_dir, source, goal, config, context):
        captured["goal"] = goal
        captured["pending"] = context.get("_pending_issue")
        return fc.ABSTAIN

    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "voice-interview", open=True, title="Voice interview flow")
        fc.classify_for_filing(base, source=None, config=cfg, core=None, judge=judge,
                                issue_title="New bug in the voice flow",
                                issue_body="Steps to reproduce: ...")
    assert captured["goal"] is None
    assert captured["pending"] == {"title": "New bug in the voice flow",
                                    "body": "Steps to reproduce: ..."}


def test_classify_for_filing_omitting_issue_title_body_is_byte_identical_to_before_this_slice():
    """Regression: a caller that supplies neither `issue_title` nor `issue_body` (every caller
    before this slice) must build EXACTLY the context `registry_context()` alone already produces
    -- no `_pending_issue` key at all, not even one holding `None`s."""
    captured = {}

    def judge(sdlc_dir, source, goal, config, context):
        captured["context"] = dict(context)
        return fc.ABSTAIN

    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "voice-interview", open=True, title="Voice interview flow")
        _write_unit(base, "legacy-import", open=False, title="Legacy import path")
        fc.classify_for_filing(base, source=None, config=cfg, core=None, judge=judge)
        expected = fc.registry_context(base)
    assert captured["context"] == expected
    assert "_pending_issue" not in captured["context"]


def test_classify_for_filing_with_only_a_title_still_adds_pending_issue():
    """Either half alone is enough to opt in -- the gate is `is not None`, not "both given"."""
    captured = {}

    def judge(sdlc_dir, source, goal, config, context):
        captured["pending"] = context.get("_pending_issue")
        return fc.ABSTAIN

    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        fc.classify_for_filing(base, source=None, config=cfg, core=None, judge=judge,
                                issue_title="Just a title")
    assert captured["pending"] == {"title": "Just a title", "body": None}


def test_classify_at_pick_never_gains_the_pending_issue_key():
    """Explicit regression: `classify_at_pick` is a completely separate function and this slice
    changes nothing about it -- it never builds `_pending_issue`, with or without a judge."""
    _clean_unknown_labels()
    captured = {}

    def judge(sdlc_dir, source, goal, config, context):
        captured["context"] = dict(context)
        return fc.ABSTAIN

    run = _runner()
    gh = _source(run)
    with tempfile.TemporaryDirectory() as d:
        base, cfg = _sdlc(d)
        _write_unit(base, "core", open=True)
        fc.classify_at_pick(base, gh, "42", cfg, core="core", judge=judge)
    assert "_pending_issue" not in captured["context"]


def test_classify_at_pick_signature_is_unchanged_by_this_slice():
    """`classify_at_pick` gains no `issue_title`/`issue_body` parameters -- only
    `classify_for_filing` does, per this slice's own instructions."""
    import inspect
    sig = inspect.signature(fc.classify_at_pick)
    assert list(sig.parameters) == ["sdlc_dir", "source", "goal", "config", "core", "judge"]

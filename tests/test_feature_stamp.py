"""#1471 (L3, epic #1464, story #1427): stamp the unit onto every issue Sigma files itself.

`features.py` (L0) reads a declaration; `feature_labels.py` (L1) attaches a declared label at pick.
This is the WRITE half, and the chain it closes is the one that breaks where it matters most --
on the work nobody wrote by hand. A follow-up, a discovered dependency, a decomposition meta-issue:
each is filed by Sigma from a goal that belongs to a unit, and each must inherit BOTH halves of
that goal's declaration or the unit ends at the first machine-filed issue.

THREE PROPERTIES, AND THE FIRST IS THE ONE THAT IS EASY TO SHIP BROKEN:

  - THE ROUND TRIP. A marker this module writes must be readable by `features.read` afterwards --
    asserted by feeding the FILED ISSUE's own recorded body and labels back through the reader, not
    by matching a substring. `features.py` rule 5 refuses to parse fenced or four-space-indented
    content on purpose (every issue in this epic DISPLAYS the marker in a fence in order to teach
    it), so a writer that emits its marker into either place stamps something invisible and fails
    silently -- there is no error, the issue simply reads as declaring nothing.
  - PARITY. A goal that declares no unit files follow-ups exactly as it did before this existed:
    same title, same body, same labels, and no label write of any kind.
  - NEVER CREATED. `gh label create` is never invoked with a `feature:*` argument on the filing
    path either -- asserted on the RECORDED gh CALLS, never on a return value, because an outcome
    check cannot tell "we did not create it" from "creating it happened to fail" (#1468's own rule,
    inherited rather than re-implemented: the label is attached through `attach_label`, which
    resolves node ids first and so is structurally incapable of minting one).
"""
import importlib.util, json, pathlib, tempfile

import pytest

import gqlfake

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


handoff = _mod("handoff")
features = _mod("features")
feature_stamp = _mod("feature_stamp")
feature_registry = _mod("feature_registry")


@pytest.fixture(autouse=True)
def _clean_unknown_labels():
    gqlfake._UNKNOWN_LABELS.clear()
    yield
    gqlfake._UNKNOWN_LABELS.clear()


_CONFIG = {"discovery": {"source": "github", "github": {"repo": "o/r"}},
           "ledger": {"enabled": True, "actor": "amy"}}

_UNIT = "voice-interview"
_LABEL = "feature:voice-interview"
_DECLARES = "some prose\n\nFeature: voice-interview\nBranch: feature/voice-interview\n"
_FILED = "77"

#: #1820: a SECOND registered unit, different from `_UNIT`, so a `target_unit` override is a
#: genuine cross-map rather than a restatement of what the goal already inherits.
_OTHER_UNIT = "billing-portal"
_OTHER_LABEL = "feature:billing-portal"
#: A registered unit that is CLOSED -- #1820's own stated exclusion (never retargeted, explicitly
#: or automatically) must refuse this identically to a name nobody ever registered.
_CLOSED_UNIT = "legacy-import"


def _sdlc(tmp_path, codeowners="*  @lead-person\n"):
    base = tmp_path / ".sdlc"
    (base / "state").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(_CONFIG))
    (tmp_path / ".github").mkdir(exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(codeowners)
    return str(base)


def _registry(tmp_path, entries):
    """Write `.sdlc/features/index.json` DIRECTLY via `feature_registry.write_index` -- never via
    `_sdlc()`, which `mkdir`s `.sdlc/state/` without `exist_ok` and would raise if called twice for
    the same `tmp_path` (every `_track`/`_sdlc(tmp_path)` call below still runs exactly once). Must
    be called BEFORE `_track`/`_sdlc` so `.sdlc/features/` exists first; `write_index`'s own
    `mkdir(parents=True, exist_ok=True)` never collides with `_sdlc`'s later `.sdlc/state/` mkdir --
    they are siblings under `.sdlc/`, not the same directory."""
    feature_registry.write_index(feature_registry.registry_dir(tmp_path / ".sdlc"), entries)


def _unit_entry(**over):
    base = {"title": "t", "owner": None, "open": True, "parent": None, "tracking_issue": None,
            "repos": {}}
    base.update(over)
    return base


def _runner(issues=None, absent=(), fail_on=(), number=_FILED, create_returns=None):
    """Fake `gh` over a tiny backlog, recording every call it is actually asked to make.

    `issues` is `{number: {"body": ..., "labels": {...}}}` -- the FILING goal lives there. An issue
    a filing creates is ADDED to the same backlog, so a label attached to the freshly-filed issue is
    genuinely visible to a later read of it. That is what makes the round-trip assertion real rather
    than a restatement of what the writer just returned."""
    calls = []
    gqlfake._UNKNOWN_LABELS.update(absent)
    live = {str(n): set(s.get("labels") or {"sdlc:goal"}) for n, s in (issues or {}).items()}
    bodies = {str(n): (s.get("body") or "") for n, s in (issues or {}).items()}
    created = []

    def run(args):
        joined = " ".join(str(a) for a in args)
        for needle in fail_on:
            if needle in joined:
                raise RuntimeError("simulated gh failure: %s" % needle)
        target = live.get(gqlfake._GQL_LAST_ISSUE["n"]) if args[:2] == ["api", "graphql"] else None
        gql = gqlfake.swap(args, labels=target, calls=calls, repo_args=("--repo", "o/r"))
        if gql is not None:
            return gql
        calls.append(list(args))

        def view(n, fields):
            n = str(n)
            out = {}
            if "title" in fields:
                out["title"] = "goal %s" % n
            if "body" in fields:
                out["body"] = bodies.get(n, "")
            if "labels" in fields:
                out["labels"] = [{"name": x} for x in sorted(live.get(n, ()))]
            if "comments" in fields:
                out["comments"] = []
            return out

        rest = gqlfake.rest_issue(args, view)
        if rest is not None:
            return rest
        if args[:2] == ["issue", "view"]:
            return json.dumps(view(args[2], (args[args.index("--json") + 1] if "--json" in args else "").split(",")))
        if args[:2] == ["issue", "create"]:
            body = args[args.index("--body") + 1]
            labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
            created.append({"title": args[args.index("--title") + 1], "body": body,
                            "labels": list(labels)})
            if create_returns is not None:
                return create_returns
            bodies[str(number)] = body
            live[str(number)] = set(labels)
            return "https://github.com/o/r/issues/%s" % number
        if args[:2] == ["issue", "list"]:
            return "[]"
        return ""

    run.calls, run.live, run.bodies, run.created = calls, live, bodies, created
    return run


def _source(run):
    gh = _mod("sources").GitHubSource(_CONFIG, run=run)
    gh._LABEL_SWAP_RETRIES, gh._LABEL_SWAP_RETRY_BASE = 1, 0
    return gh


def _flat(run):
    return [" ".join(str(a) for a in c) for c in run.calls]


def _label_creates(run):
    """Every recorded `gh label create` invocation. THE call log, not a verdict."""
    return [c for c in run.calls if len(c) >= 2 and c[0] == "label" and c[1] == "create"]


def _filed_payload(run, number=_FILED):
    """The filed issue as `features.read` takes it -- read back out of the fake backlog, so both
    halves are whatever actually landed on the issue rather than whatever the writer intended."""
    return {"body": run.bodies.get(str(number), ""),
            "labels": [{"name": n} for n in sorted(run.live.get(str(number), ()))]}


def _track(tmp_path, run, goal="42", **kwargs):
    """File a same-area, non-blocking follow-up FROM `goal` -- the plainest thing the kit files."""
    opts = {"same_area": True, "immediately_actionable": True, "blocks_goal": False, "dedup": False}
    opts.update(kwargs)
    return handoff.create_tracked_issue(_sdlc(tmp_path), _CONFIG, goal, "loop",
                                        "the follow-up this goal discovered", source=_source(run),
                                        **opts)


# ------------------------------------------------------------------ the round trip

def test_a_follow_up_inherits_both_halves_and_the_reader_can_read_them_back(tmp_path):
    """THE property. Not "the body contains the string" -- the FILED issue, read back out of the
    fake backlog and fed to `features.read`, must resolve to the parent's unit with both halves
    agreeing."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    report = _track(tmp_path, run)

    assert report["issue"] == _FILED and report["unit"] == _UNIT
    verdict = features.read(_filed_payload(run))
    assert verdict.state == features.AGREE and verdict.unit == _UNIT


def test_the_stamped_marker_is_bare_lines_never_a_fenced_or_indented_block(tmp_path):
    """`features.py` rule 5 does not parse fenced or four-space-indented content AT ALL, because
    every issue teaching this format displays the marker in a fence. A marker written into either
    is invisible and the stamp silently does nothing."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    _track(tmp_path, run)

    body = run.created[0]["body"]
    split = body.split("\n")
    lines = [l for l in split if l.strip().startswith("Feature:")]
    assert lines and all(l == "Feature: %s" % _UNIT for l in lines), lines   # column 0, no indent
    assert "```" not in body and "~~~" not in body
    assert features.parse_body(body) == _UNIT
    # The BLANK LINE above the marker is not cosmetic and nothing else in this file would notice it
    # going missing: the parser is line-anchored either way, but without it markdown renders the
    # marker as a lazy continuation of the paragraph above -- so the human-readable half, which is
    # the entire justification for a body marker existing at all, reads as a sentence.
    #
    # `idx > 0` FIRST, and it is not defensive noise. Written as `split[split.index(...) - 1]` this
    # wrapped to `split[-1]` whenever the marker landed at line 0 (the `_prepended` placement), and
    # `split[-1]` is `''` for any body ending in a newline -- so on that input the assertion passed
    # without ever looking at the thing it names. Same family as the two the mutation run caught.
    idx = split.index(lines[0])
    assert idx > 0 and split[idx - 1].strip() == "", body


def test_the_branch_line_is_stamped_too_and_agrees(tmp_path):
    """The marker is specified as TWO lines (`features.py` rule 7). Writing one of them is half the
    spec, and a `Branch:` line that disagreed would make the issue raise `AmbiguousUnit`."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    _track(tmp_path, run)
    assert "Branch: feature/%s" % _UNIT in run.created[0]["body"].split("\n")


def test_the_marker_survives_a_body_that_ends_inside_an_unterminated_fence(tmp_path):
    """The trap that makes a naive append silently produce an unreadable issue: `_visible` blanks an
    unterminated fence TO EOF, so lines appended after one are invisible. The writer verifies its own
    output through the reader and places the marker where it is genuinely readable."""
    fenced = "here is how you declare a unit:\n\n```\nFeature: example\n"    # never closed
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    _track(tmp_path, run, body=fenced)

    assert features.parse_body(run.created[0]["body"]) == _UNIT
    assert features.read(_filed_payload(run)).unit == _UNIT


def test_a_cross_area_hand_off_inherits_the_unit_too(tmp_path):
    """A discovered dependency is named in the issue as one of the things that must inherit, and the
    model has a shape for it: `feature/<unit>/<sub>` is a sub-branch of the SAME unit, which
    `features._branch_agrees` blesses. `hand_off()` supplies its own body template, so this is also
    the caller-supplied-body path end to end."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    report = handoff.hand_off(_sdlc(tmp_path), _CONFIG, "42", "loop", "needs a flag first",
                              source=_source(run))
    assert report["unit"] == _UNIT
    assert features.read(_filed_payload(run)).state == features.AGREE


# ------------------------------------------------------------------ parity: no unit, no change

def test_a_goal_with_no_unit_files_exactly_as_before(tmp_path):
    """The property that makes adopting this break nothing. No marker, no feature label, and no
    label write at all."""
    run = _runner({"42": {"body": "an ordinary issue with no declaration\n", "labels": {"sdlc:goal"}}})
    report = _track(tmp_path, run)

    assert report["unit"] is None and report["issue"] == _FILED
    body = run.created[0]["body"]
    assert "Feature:" not in body and "Branch:" not in body
    assert not any(str(l).lower().startswith("feature:") for l in run.created[0]["labels"])
    assert not any("--add-label" in f for f in _flat(run)), _flat(run)
    assert report["warnings"] == []


def test_a_source_without_the_unit_surface_is_a_silent_no_op(tmp_path):
    """LocalSource has no `fetch_body_labels`. It must degrade to exactly today's behaviour rather
    than raising inside a filing.

    SILENCE IS ASSERTED, not just implied by the test's name. Without the `warnings == []` line the
    duck-type guard could be deleted entirely -- every LocalSource filing would then carry `'Local'
    object has no attribute 'fetch_body_labels'` -- and this test still passed, so the half of its
    own name that says "silent" observed nothing (mutant M4b below)."""
    class Local:
        def create_dependency(self, title, body, assignee, labels=(), goal_label=True):
            self.created = {"title": title, "body": body, "labels": list(labels)}
            return 61

        def note(self, goal, text):
            pass

    src = Local()
    report = handoff.create_tracked_issue(_sdlc(tmp_path), _CONFIG, "0004-x.md", "loop", "why",
                                          same_area=True, immediately_actionable=True,
                                          blocks_goal=False, source=src, dedup=False)
    assert report["unit"] is None and report["issue"] == 61
    assert "Feature:" not in src.created["body"]
    assert report["warnings"] == [], report["warnings"]      # the "silent" half of the name


# ------------------------------------------------------------------ never created

def test_gh_label_create_is_never_invoked_for_a_feature_label_on_the_filing_path(tmp_path):
    """#1468's rule, inherited rather than re-implemented. Asserted on the recorded call log."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    _track(tmp_path, run)
    created = [c[2] for c in _label_creates(run)]
    assert created, created                                     # ordinary labels ARE still ensured
    assert not any(str(n).lower().startswith("feature:") for n in created), created


def test_the_feature_label_is_attached_after_creation_not_passed_to_issue_create(tmp_path):
    """Passing it to `issue create --label` would be atomic but LOSES THE WHOLE ISSUE when the label
    is absent (gh fails the create). Attaching afterwards degrades to `body_only` instead, which the
    reader resolves and which L1 repairs at the follow-up's own pick."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    _track(tmp_path, run)
    assert _LABEL not in run.created[0]["labels"], run.created[0]["labels"]
    assert any("--add-label %s" % _LABEL in f for f in _flat(run)), _flat(run)


def test_a_missing_label_still_files_the_issue_with_its_body_marker(tmp_path):
    """The label does not exist on the repo. The issue must still be filed, still declare its unit
    in the body, and say what did not happen."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}}, absent={_LABEL})
    report = _track(tmp_path, run)

    assert report["issue"] == _FILED
    assert features.parse_body(run.created[0]["body"]) == _UNIT
    assert _label_creates_none(run)
    assert any(_LABEL in w for w in report["warnings"]), report["warnings"]
    assert features.read(_filed_payload(run)).state == features.BODY_ONLY


def _label_creates_none(run):
    return not any(str(c[2]).lower().startswith("feature:") for c in _label_creates(run))


def test_a_failed_create_attaches_nothing(tmp_path):
    """`create_dependency` returns None when `gh` ran and produced no usable issue number. There is
    nothing to label then, and asking for `feature:x` on issue `None` would be a live gh call
    against a number that does not exist.

    ASSERTED ON THE ATTACH REQUEST, not on the gh call log: the first version of this test checked
    only that no `--add-label` reached `gh`, and a mutant removing the `report["issue"]` guard
    SURVIVED it -- `_swap_labels_best_effort` swallows the failure for a non-numeric issue, so the
    log looks identical whether or not the attach was attempted. The fixture has to be able to
    observe the thing being asserted."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}},
                  create_returns="not-an-issue-url")
    src = _source(run)
    asked = []
    src.attach_label = lambda goal, name: asked.append((goal, name)) or False

    report = handoff.create_tracked_issue(_sdlc(tmp_path), _CONFIG, "42", "loop", "why",
                                          same_area=True, immediately_actionable=True,
                                          blocks_goal=False, source=src, dedup=False)

    assert report["issue"] is None and report["issue_attempted"] is True
    assert asked == [], asked
    assert not any("could not attach" in w for w in report["warnings"]), report["warnings"]


# ------------------------------------------------------------------ what the parent declares

def test_a_label_only_parent_still_hands_down_both_halves(tmp_path):
    """The common shape after L1 has run: the parent carries the label and its body says nothing.
    The child gets BOTH, because the body marker is the half a human reads."""
    run = _runner({"42": {"body": "no marker here\n", "labels": {"sdlc:goal", _LABEL}}})
    report = _track(tmp_path, run)
    assert report["unit"] == _UNIT
    assert features.read(_filed_payload(run)).state == features.AGREE


def test_a_conflicting_parent_hands_down_the_body_s_unit(tmp_path):
    """`features.read` resolves a conflict to the BODY -- it is what a human wrote. Inheriting the
    label instead would make L3 the one place in the model that resolves a conflict differently."""
    run = _runner({"42": {"body": "Feature: voice-interview\n", "labels": {"sdlc:goal", "feature:billing"}}})
    report = _track(tmp_path, run)
    assert report["unit"] == _UNIT
    assert any("billing" in w for w in report["warnings"]), report["warnings"]


def test_a_self_contradicting_parent_files_the_issue_with_no_unit(tmp_path):
    """`features.read`'s documented obligation on every caller: catch `AmbiguousUnit` per issue and
    carry on. One hand-edited issue must never stop a follow-up being filed at all."""
    run = _runner({"42": {"body": "Feature: voice-interview\nFeature: billing\n",
                          "labels": {"sdlc:goal"}}})
    report = _track(tmp_path, run)
    assert report["issue"] == _FILED and report["unit"] is None
    assert "Feature:" not in run.created[0]["body"]
    assert any("contradicts itself" in w for w in report["warnings"]), report["warnings"]


def test_an_unreadable_parent_files_the_issue_with_no_unit(tmp_path):
    """`fetch_body_labels` RAISES on a transport failure by design. A blip must degrade to
    today's behaviour, not lose the follow-up."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}},
                  fail_on=("issue view 42", "repos/o/r/issues/42 "))     # #895: REST-first read, then fallback
    report = _track(tmp_path, run)
    assert report["issue"] == _FILED and report["unit"] is None
    assert any("42" in w for w in report["warnings"]), report["warnings"]


# ------------------------------------------------------------------ the body it is given

def test_a_body_already_declaring_the_same_unit_is_not_stamped_twice(tmp_path):
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    _track(tmp_path, run, body="a finding\n\nFeature: %s\n" % _UNIT)
    assert run.created[0]["body"].count("Feature: %s" % _UNIT) == 1
    assert features.read(_filed_payload(run)).unit == _UNIT


def test_a_body_declaring_a_RIVAL_unit_is_left_alone_and_not_labelled_either(tmp_path):
    """Two rival declarations is the one state `features.read` cannot resolve -- it RAISES. Adding
    a second one, or attaching a label that disagrees with what the body says, would manufacture
    exactly that state on an issue Sigma just filed."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    report = _track(tmp_path, run, body="a finding\n\nFeature: billing\n")

    assert report["unit"] is None
    assert "Feature: %s" % _UNIT not in run.created[0]["body"]
    assert not any("--add-label %s" % _LABEL in f for f in _flat(run)), _flat(run)
    assert features.read(_filed_payload(run)).unit == "billing"
    assert any("billing" in w for w in report["warnings"]), report["warnings"]


# ------------------------------------------------------------------ the label a CALLER passes

def test_a_caller_supplied_rival_feature_label_withholds_both_inherited_halves(tmp_path):
    """THE REGRESSION THIS REVIEW FOUND, pinned end to end. `handoff track --label feature:billing`
    puts a rival straight into `create_dependency(labels=)`, which `stamp_body` -- looking only at
    the BODY -- never saw. The filed issue carried `feature:billing` AND `feature:voice-interview`
    AND a `Feature: voice-interview` marker, so `features.read` RAISED, with an empty `warnings`
    and `unit` reporting success. Asserted the way the bug was found: on the recorded gh calls, and
    on `features.read` of the filed payload not raising."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    report = _track(tmp_path, run, extra_labels=["feature:billing"])

    assert report["unit"] is None
    assert "Feature:" not in run.created[0]["body"]                   # no inherited marker
    assert not any("--add-label %s" % _LABEL in f for f in _flat(run)), _flat(run)
    assert run.created[0]["labels"].count("feature:billing") == 1     # the caller's label survives
    assert not any(str(l).lower() == _LABEL for l in run.created[0]["labels"])
    assert any("billing" in w for w in report["warnings"]), report["warnings"]

    verdict = features.read(_filed_payload(run))                     # must not raise
    assert verdict.state == features.LABEL_ONLY and verdict.unit == "billing"


def test_a_caller_restating_the_inherited_unit_is_not_passed_to_issue_create(tmp_path):
    """An agreeing label is not a rival -- but handing it to `create_dependency` puts it on
    `gh issue create --label`, which resolves labels BEFORE the create mutation and aborts the whole
    call on one it cannot find. Dropped from the list; the label still lands, via the attach."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    report = _track(tmp_path, run, extra_labels=[_LABEL])

    assert report["unit"] == _UNIT
    assert _LABEL not in run.created[0]["labels"], run.created[0]["labels"]
    assert any("--add-label %s" % _LABEL in f for f in _flat(run)), _flat(run)
    assert features.read(_filed_payload(run)).state == features.AGREE


def test_a_caller_feature_label_on_a_goal_with_no_unit_is_untouched(tmp_path):
    """Pre-#1471 behaviour, and it must stay: a caller filing into a unit the goal does not belong
    to is making a deliberate choice, not colliding with an inherited one."""
    run = _runner({"42": {"body": "no declaration at all\n", "labels": {"sdlc:goal"}}})
    report = _track(tmp_path, run, extra_labels=["feature:billing"])

    assert report["unit"] is None and report["warnings"] == []
    assert "feature:billing" in run.created[0]["labels"]
    assert features.read(_filed_payload(run)).unit == "billing"


def test_reconcile_labels_asks_the_reader_what_a_label_declares():
    """A label the READER would ignore must be ignored here too, or the two halves drift on what
    counts as a declaration. `feature:` with an illegal unit name declares nothing to
    `features.parse_labels`, so it is not a rival."""
    r = feature_stamp.reconcile_labels(["feature:TBD.", "priority:P1"], _UNIT)
    assert r.unit == _UNIT and r.warnings == [] and "feature:TBD." in r.labels


def test_reconcile_labels_matches_a_rival_case_insensitively():
    """GitHub label names are case-insensitively unique, so `Feature:Billing` and `feature:billing`
    are one name on a repo -- treating them as different would let a capital slip a rival past."""
    r = feature_stamp.reconcile_labels(["Feature:Billing"], _UNIT)
    assert r.unit is None and r.warnings

    same = feature_stamp.reconcile_labels(["FEATURE:Voice-Interview"], _UNIT)
    assert same.unit == _UNIT and same.labels == []          # a restatement, dropped


def test_reconcile_labels_leaves_two_caller_rivals_to_the_caller():
    """Two rival labels the caller typed themselves is their doing, and pre-existing -- this must
    not start editing them. Both survive; only the INHERITED halves are withheld."""
    r = feature_stamp.reconcile_labels(["feature:a", "feature:b"], _UNIT)
    assert r.unit is None and r.labels == ["feature:a", "feature:b"] and len(r.warnings) == 1


# ------------------------------------------------------------------ #1820: an explicit target_unit

def test_target_unit_overrides_the_inherited_unit(tmp_path):
    """The scenario #1820 is actually about: a goal that inherits its OWN unit (`_UNIT`) discovers
    an issue that belongs to a genuinely DIFFERENT, already-open one. The filed issue must agree on
    the TARGET, not the goal's own inheritance -- round-tripped through the real reader, not a
    substring match."""
    _registry(tmp_path, {_OTHER_UNIT: _unit_entry(open=True)})
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    report = _track(tmp_path, run, target_unit=_OTHER_UNIT)

    assert report["unit"] == _OTHER_UNIT and report["warnings"] == []
    verdict = features.read(_filed_payload(run))
    assert verdict.state == features.AGREE and verdict.unit == _OTHER_UNIT
    assert _OTHER_LABEL in run.created[0]["labels"] or any(
        "--add-label %s" % _OTHER_LABEL in f for f in _flat(run)), _flat(run)


def test_target_unit_naming_an_unregistered_unit_falls_back_to_inheritance(tmp_path):
    """An invalid target must never abort the filing -- the same 'never fail a filing' principle
    every module in this chain states and tests. Falls back to whatever the goal's own inheritance
    would have produced, exactly as if the flag had not been passed, with a warning naming why."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    report = _track(tmp_path, run, target_unit="no-such-unit")

    assert report["unit"] == _UNIT                          # inheritance still won
    assert any("no-such-unit" in w and "OPEN" in w for w in report["warnings"]), report["warnings"]
    assert features.read(_filed_payload(run)).unit == _UNIT


def test_target_unit_naming_a_closed_unit_is_refused_identically_to_unknown(tmp_path):
    """#1820's own stated exclusion: a finished unit must never be retargeted onto, explicitly or
    automatically. A human typing `--target-unit legacy-import` must be refused exactly like a typo
    -- reopening a closed unit stays a hand-edit of the issue body, never this mechanism."""
    _registry(tmp_path, {_CLOSED_UNIT: _unit_entry(open=False)})
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    report = _track(tmp_path, run, target_unit=_CLOSED_UNIT)

    assert report["unit"] == _UNIT                          # inheritance still won, unit NOT reopened
    assert any(_CLOSED_UNIT in w and "OPEN" in w for w in report["warnings"]), report["warnings"]
    assert "Feature: %s" % _CLOSED_UNIT not in run.created[0]["body"]
    assert not any(str(l).lower() == ("feature:" + _CLOSED_UNIT) for l in run.created[0]["labels"])


def test_target_unit_on_a_goal_with_no_unit_of_its_own_still_stamps_the_target(tmp_path):
    """Nothing is inherited, but the target is still valid -- the issue is filed INTO the targeted
    unit even though the filing goal itself declares none."""
    _registry(tmp_path, {_OTHER_UNIT: _unit_entry(open=True)})
    run = _runner({"42": {"body": "no declaration at all\n", "labels": {"sdlc:goal"}}})
    report = _track(tmp_path, run, target_unit=_OTHER_UNIT)

    assert report["unit"] == _OTHER_UNIT
    assert features.read(_filed_payload(run)).unit == _OTHER_UNIT


def test_target_unit_conflicting_with_a_callers_own_label_uses_the_existing_conflict_path(tmp_path):
    """A caller passing BOTH `target_unit` AND a rival `--label` is two explicit signals
    disagreeing -- proves the override introduces no new precedence bug: `target_unit` becomes
    `unit` before `reconcile_labels` runs, so the EXISTING rival-label handling applies unchanged
    (both halves withheld, the caller's own label untouched)."""
    _registry(tmp_path, {_OTHER_UNIT: _unit_entry(open=True)})
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    report = _track(tmp_path, run, target_unit=_OTHER_UNIT, extra_labels=["feature:something-else"])

    assert report["unit"] is None
    assert "Feature:" not in run.created[0]["body"]
    assert "feature:something-else" in run.created[0]["labels"]
    assert not any(str(l).lower() in (_LABEL, _OTHER_LABEL) for l in run.created[0]["labels"])
    verdict = features.read(_filed_payload(run))                # must not raise
    assert verdict.state == features.LABEL_ONLY and verdict.unit == "something-else"


def test_omitting_target_unit_is_byte_identical_to_before_1820(tmp_path):
    """Parity: the common case. Registering a second open unit that is never targeted must change
    nothing about a filing that doesn't ask for it."""
    _registry(tmp_path, {_OTHER_UNIT: _unit_entry(open=True)})
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})
    report = _track(tmp_path, run)                             # no target_unit kwarg at all

    assert report["unit"] == _UNIT and report["warnings"] == []
    assert features.read(_filed_payload(run)).state == features.AGREE


# ------------------------------------------------------------------ the duplicate-reuse path

def test_a_reused_duplicate_issue_is_never_stamped(tmp_path, monkeypatch):
    """The dedup path returns an issue Sigma did NOT just open. It may belong to another unit,
    and a second declaration on it would make it unreadable."""
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}},
                   "9": {"body": "an older issue\n", "labels": {"sdlc:goal"}}})
    monkeypatch.setattr(handoff, "_duplicate_search",
                        lambda *a, **k: {"candidates": [{"ref": "9", "score": 0.9,
                                                          "strength": "duplicate"}]})
    report = _track(tmp_path, run, dedup=True)

    assert report["duplicate_of"] == "9" and run.created == []
    assert report["unit"] is None                       # `unit` names what was STAMPED; nothing was
    assert not any("--add-label %s" % _LABEL in f for f in _flat(run)), _flat(run)
    assert any("9" in w and "unit" in w for w in report["warnings"]), report["warnings"]


def test_the_duplicate_search_never_sees_the_marker(tmp_path, monkeypatch):
    """The marker is metadata Sigma adds, not a description of the finding. Letting it into the
    dedup corpus would give every filing from one unit ~60 bytes of identical text -- the same
    shared-boilerplate false positive that made `decompose_check` opt out of the search entirely."""
    seen = {}
    run = _runner({"42": {"body": _DECLARES, "labels": {"sdlc:goal", _LABEL}}})

    def _spy(sdlc_dir, config, title, text, **kwargs):
        seen["text"] = text
        return {"candidates": []}

    monkeypatch.setattr(handoff, "_duplicate_search", _spy)
    _track(tmp_path, run, dedup=True)

    assert "Feature:" not in seen["text"], seen["text"]
    assert features.parse_body(run.created[0]["body"]) == _UNIT


# ------------------------------------------------------------------ the pure halves

def test_marker_is_built_from_the_reader_s_own_constants():
    """The writer and the reader are ONE definition. A literal here would be a second one, and L3
    is exactly where the two would drift apart unnoticed."""
    assert feature_stamp.marker("x") == "%s: x\n%s: %sx" % (
        features.BODY_KEY, features.BRANCH_KEY, features.BRANCH_PREFIX)


def test_stamp_body_round_trips_through_the_reader_for_every_ordinary_body():
    for body in ("", "\n", "one line", "a\n\nb\n", "- a bullet\n> a quote\n",
                 "```\nFeature: shown-not-declared\n```\n", "<!-- Feature: hidden -->\n"):
        out = feature_stamp.stamp_body(body, _UNIT)
        assert out.unit == _UNIT, body
        assert features.parse_body(out.body) == _UNIT, repr(out.body)


def test_stamp_body_never_silently_ships_an_unreadable_marker():
    """When no placement reads back, the body is returned UNSTAMPED with a warning -- never with a
    marker nothing can see."""
    out = feature_stamp.stamp_body("Branch: feature/something-else\n", _UNIT)
    assert "Feature:" not in out.body and out.warnings


def test_stamp_body_falls_back_to_the_top_when_the_end_of_the_body_is_hidden():
    """The two placements are ordered, and the fallback is not decorative: appended is the natural
    reading position, the TOP is where no fence or comment can yet be open."""
    out = feature_stamp.stamp_body("intro\n\n<!-- an unterminated comment\n", _UNIT)
    assert out.body.startswith("%s: %s" % (features.BODY_KEY, _UNIT)), repr(out.body)
    assert features.parse_body(out.body) == _UNIT


def test_stamp_body_refuses_a_body_that_already_contradicts_itself():
    """Two rival `Feature:` lines is the state `features.read` RAISES on. Neither half may be
    written onto an issue no reader can resolve -- including the label."""
    out = feature_stamp.stamp_body("Feature: alpha\nFeature: beta\n", _UNIT)
    assert out.body == "Feature: alpha\nFeature: beta\n"
    assert out.unit is None and out.warnings


def test_attach_says_so_when_the_source_cannot_attach_at_all():
    class NoSurface:
        pass

    warnings = feature_stamp.attach(NoSurface(), "77", _UNIT)
    assert len(warnings) == 1 and _LABEL in warnings[0]


def test_attach_never_lets_a_raising_write_escape():
    """The issue already exists by this point. An exception here would undo nothing and lose the
    filing's report -- `create_tracked_issue` is documented as never raising."""
    class Explodes:
        def attach_label(self, goal, name):
            raise RuntimeError("gh is gone")

    warnings = feature_stamp.attach(Explodes(), "77", _UNIT)
    assert len(warnings) == 1 and "gh is gone" in warnings[0]


def test_a_broken_stderr_cannot_break_a_filing(monkeypatch):
    """The same promise `features._note` and `feature_labels._note` make. That branch was tested for
    one of them and not the other, and the repo's own note on it is that the asymmetry was an
    oversight rather than a policy -- so it is pinned here from the start."""
    class Exploding:
        def write(self, _):
            raise ValueError("stderr is gone")

    monkeypatch.setattr(feature_stamp.sys, "stderr", Exploding())
    out = feature_stamp.stamp_body("Branch: feature/something-else\n", _UNIT)
    assert out.warnings and "Feature:" not in out.body

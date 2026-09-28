"""#1661: `--feature <name>` confines one loop run to ONE unit of work.

The contract these tests pin, in the repo owner's own words: *"during the --feature runs in both no
other issue should be picked by the sigma next calls."* EXCLUSIVE, not preferential -- a goal
that declares no unit, or a different one, is unpickable for the whole run, and when the unit runs
out the run says so and stops rather than falling back to the rest of the board.

The fake `gh` transport here HONOURS `--label`, which is what makes the server-side half of the
scope observable at all: a fixture that returned the same canned list whatever was asked would pass
just as well with the `--label feature:<name>` argument deleted from the query.
"""
import json, pathlib, importlib.util, tempfile

import pytest

import gqlfake

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


UNIT = "voice-interview"
OTHER = "billing"
LABEL = "feature:" + UNIT
OTHER_LABEL = "feature:" + OTHER


def _issue(number, *labels, body="", assignees=()):
    return {"number": number, "title": "goal %s" % number, "body": body,
            "labels": [{"name": n} for n in ("sdlc:goal",) + labels],
            "assignees": list(assignees), "url": "u/%s" % number, "state": "OPEN"}


def _is_rest_issues_call(args):
    """#1829: `_fetch_pending`/`list_needs_label` read through `gh api
    repos/{owner}/{repo}/issues` (REST) instead of `gh issue list` (graphql-search-billed).
    #1833 migrated `mirror.py` (the dependency-hold's own board-mirror refresh) onto the exact
    same shape -- so every issues-listing call this fake ever sees is this one; the OLD-shape
    branch below is kept only because a stray hand-constructed `["issue", "list", ...]` call would
    otherwise silently fall through to it rather than failing loudly."""
    return (len(args) > 1 and args[0] == "api" and str(args[1]).startswith("repos/")
            and str(args[1]).endswith("/issues"))


def _rest_field(args, key):
    prefix = key + "="
    return next((v[len(prefix):] for v in args if isinstance(v, str) and v.startswith(prefix)), None)


def _gh(issues, calls=None):
    """A fake `gh` that behaves like the real one in the two ways these tests turn on.

    IT FILTERS BY EVERY LABEL ASKED FOR, ANDing them -- measured on this repo rather than assumed:
    `sdlc:goal` alone -> 101 open issues, `sdlc:goal` + `feature:autowatch-dryrun` -> 2, `sdlc:goal`
    + `bug` -> 10 while `bug` alone -> 60, and `sdlc:goal` + a label the repo does not have -> 0
    rather than an error (verified identically for REST's comma-joined `labels=` field, #1829).

    #1829/#1833: the REST shape returns the FULL issue object regardless of what a caller's old
    `--json` field list would have asked for -- because a real REST response has no field-
    selection concept at all (verified live) -- which is a STRONGER version of the honesty
    property the old field-filtering existed for: a picker (or, since #1833, the board-mirror
    refresh) that forgot to need `body` used to still silently get away with it if the fake
    ignored `--json`; under REST there is no way to under-fetch it even by accident, so that whole
    regression class is now structurally impossible rather than merely fake-enforced. The OLD
    (`--label`/`--json`) branch below is kept only as a fallback for a stray hand-constructed
    old-shape call, never exercised by either real caller any more."""
    calls = calls if calls is not None else []
    labels = set()

    def run(args):
        args = list(args)
        gql = gqlfake.swap(args, labels=labels, calls=calls)
        if gql is not None:
            return gql
        calls.append(args)
        is_rest = _is_rest_issues_call(args)
        verb = "list" if is_rest else (args[1] if len(args) > 1 else args[0])
        if verb == "list":
            state = _rest_field(args, "state") if is_rest else (
                args[args.index("--state") + 1] if "--state" in args else None)
            if state == "closed":
                return "[]"
            if is_rest:
                wanted = (_rest_field(args, "labels") or "").split(",")
                wanted = [w for w in wanted if w]
                hits = [i for i in issues
                        if all(w in [l["name"] for l in i["labels"]] for w in wanted)]
                # #1829: genuinely paginate (page/per_page), unlike the old shape's unbounded
                # single response -- `_fetch_issues_rest`'s own `[:cap]` truncation is what makes
                # a real backlog deeper than the cap window-limited at all, and a fake that always
                # handed back everything in one shot could never exercise that (a real API would
                # never do this either: it always paginates).
                page = int(_rest_field(args, "page") or "1")
                per_page = int(_rest_field(args, "per_page") or "100")
                start = (page - 1) * per_page
                return json.dumps(hits[start:start + per_page])  # REST: full object, no field selection
            wanted = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
            fields = args[args.index("--json") + 1].split(",") if "--json" in args else []
            hits = [i for i in issues
                    if all(w in [l["name"] for l in i["labels"]] for w in wanted)]
            return json.dumps([{k: v for k, v in i.items() if k in fields} for i in hits])
        if verb == "view":
            for i in issues:
                if str(i["number"]) == str(args[2]):
                    return json.dumps({"state": "OPEN", "body": i.get("body") or "",
                                       "labels": i["labels"]})
            return "{}"
        return ""

    run.calls = calls
    return run


def _picks(calls):
    """Only the PICKER's own backlog reads (`_fetch_pending`, via `_fetch_issues_rest`) -- told
    apart from the needs-label sweep's REST call (`labels=sdlc:needs-label`, a different label
    entirely -- verified live, see `.sdlc/research/1829-rest-backlog-pick.md`) by matching on the
    `labels=` field's own content, not on which endpoint made the call.

    #1833: since `mirror.py`'s own open query is now the SAME shape (`labels=sdlc:goal`, REST) --
    every caller of THIS helper exercises a fixture with no "Blocked by #N" marker, so the
    dependency-hold gate that would trigger `mirror.fetch_and_write` never fires and this stays
    exactly the picker's own reads in practice. The one test that DOES exercise that gate
    (`test_the_dependency_hold_still_sees_a_blocker_that_lives_outside_the_unit`) proves the
    mirror's own read a different way -- reading its written corpus back off disk -- precisely
    because a `labels=` shape can no longer tell the two apart."""
    return [c for c in calls
            if _is_rest_issues_call(c) and (_rest_field(c, "labels") or "").split(",")[0] == "sdlc:goal"]


def _queried(c, label):
    """Was `label` part of the AND-ed label set call `c` queried for? #1829: a scoped and an
    unscoped picker call are now BYTE-IDENTICAL requests (REST always returns `body`, so there is
    no separate "scoped" request shape left to distinguish -- see
    `test_the_scope_adds_one_field_to_the_same_queries_and_no_call`), so `_scoped`/`_unscoped`'s
    old job (matching a `--json` field-list fingerprint) no longer applies; this checks the
    `labels=` field's actual content instead, which is the property these tests really care about."""
    field = _rest_field(c, "labels")
    return label in field.split(",") if field is not None else label in c


def _base(d, **extra):
    base = pathlib.Path(d) / ".sdlc"
    (base / "state").mkdir(parents=True)
    cfg = {"discovery": {"source": "github", "github": {"repo": "acme/widget"}},
           "budget": {"max_iterations": 10}}
    cfg.update(extra)
    (base / "config.json").write_text(json.dumps(cfg))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    return str(base)


def _source(lp, base, issues, calls=None, scope=UNIT):
    cfg = lp.state.load_config(base)
    src = lp.sources.GitHubSource(cfg, run=_gh(issues, calls), sdlc_dir=base)
    src._BACKLOG_READ_RETRY_BASE = 0
    if scope:
        lp.sources.scope_to_feature(src, scope)
    return cfg, src


# --- the load-bearing one -------------------------------------------------------------------------

def test_a_pickable_goal_outside_the_unit_is_never_picked():
    """THE test. A board that has a perfectly pickable goal OUTSIDE the unit -- older, so it wins
    every ordering the picker applies -- and a `--feature` run that does not pick it, not on the
    first call and not once the unit is empty."""
    lp = _mod("loop")
    issues = [_issue(10), _issue(11, LABEL)]        # 10 is older and carries no unit at all
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, issues)
        assert lp._next(base, src, cfg) == ("goal", "11")     # unscoped this would be 10
        assert src.next_pending(skip={"11"}) is None           # ...and 10 is not even a candidate


def test_the_unscoped_run_would_have_picked_the_outside_goal():
    """The control for the test above: without the flag, #10 IS the pick. Without this, "did not
    pick 10" would be consistent with 10 never having been pickable in the first place."""
    lp = _mod("loop")
    issues = [_issue(10), _issue(11, LABEL)]
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, issues, scope=None)
        assert lp._next(base, src, cfg) == ("goal", "10")


def test_a_goal_declaring_no_unit_is_unpickable_even_as_the_only_thing_left():
    """Exclusive, not preferential: an empty unit does NOT fall back to the rest of the board.

    Asserted at BOTH layers on purpose. The pick-time gate alone would keep the end-to-end answer
    right even if the pool stopped excluding non-members, so a `_next`-only assertion silently
    scores the wrong guard: measured, a mutant that makes "declares no unit" mean "member" leaves
    this test green through `_next` and is only visible on the source. The pool is the layer that
    has to be right -- it is what every OTHER reader of `next_pending` gets."""
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10), _issue(12, OTHER_LABEL)])
        assert src.next_pending() is None                  # the candidate pool, on its own
        assert lp._next(base, src, cfg) == ("DONE", None)   # and end to end


# --- the exactness half: the label is not proof ----------------------------------------------------

def test_a_goal_whose_label_says_the_unit_but_whose_body_says_another_is_refused():
    """The hole the server-side filter alone cannot close. `features.read` lets the BODY win a
    conflict, so an issue carrying `feature:voice-interview` whose body declares `Feature: billing`
    is selected by a label-scoped query and then RESOLVES to `billing` --
    `feature_labels.attach_at_pick` returns `CONFLICT_DECLARED` carrying the body's unit, and every
    downstream step (base resolution, the registry, sibling propagation) uses that. Picking it
    would put a `--feature voice-interview` run on billing's branch."""
    lp = _mod("loop")
    conflicted = _issue(11, LABEL, body="Feature: %s\n" % OTHER)
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [conflicted])
        assert lp._next(base, src, cfg) == ("DONE", None)


def test_a_member_whose_declaration_cannot_be_re_read_is_not_claimed():
    """Why the pick-time gate exists at all, given the pool is already filtered. `sources` decides
    membership from the LIST payload; `_unit_at_pick` is what every other gate and every downstream
    step acts on, and the two can disagree -- here the per-issue read the claim path makes
    (`attach_at_pick`'s `gh issue view`) fails, so the resolved unit is None while the list said
    member. A run that claimed on that would base the goal on `work.base` while believing it was in
    the unit. Not hypothetical: #1567 is the same divergence, one transport apart."""
    lp = _mod("loop")
    member = _issue(11, body="Feature: %s\n" % UNIT)

    def blind(issues, calls=None):
        inner = _gh(issues, calls)

        def run(args):
            if list(args)[0:2] == ["issue", "view"]:
                raise RuntimeError("HTTP 502 (fake)")
            return inner(args)
        return run

    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg = lp.state.load_config(base)
        src = lp.sources.GitHubSource(cfg, run=blind([member]), sdlc_dir=base)
        src._BACKLOG_READ_RETRY_BASE = 0
        lp.sources.scope_to_feature(src, UNIT)
        assert lp._next(base, src, cfg) == ("DONE", None)


def test_the_refusal_of_an_out_of_unit_goal_writes_nothing_to_it():
    """A goal declined for being out of scope is not a park and not a flag: it is simply not this
    run's business. It keeps `sdlc:goal`, gains no overlay, gets no comment and is claimed by
    nobody -- so the run that IS scoped to its unit finds it exactly as it was left."""
    lp = _mod("loop")
    conflicted = _issue(11, LABEL, body="Feature: %s\n" % OTHER)
    calls = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [conflicted], calls)
        assert lp._next(base, src, cfg) == ("DONE", None)
    assert not [c for c in calls if c[0:2] == ["issue", "comment"]]
    assert not [c for c in calls if c[0:2] == ["issue", "edit"]]
    assert conflicted["labels"] == [{"name": "sdlc:goal"}, {"name": LABEL}]


def test_a_goal_the_label_alone_declares_is_in_the_unit():
    """`LABEL_ONLY` is a real declaration (`features.read`), so a goal carrying the unit label and
    saying nothing in its body IS pickable. A scope that demanded a body marker would exclude every
    issue Sigma itself labelled at a previous pick."""
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(11, LABEL)])
        assert lp._next(base, src, cfg) == ("goal", "11")


def test_a_goal_the_body_alone_declares_is_in_the_unit():
    """THE OTHER LOAD-BEARING ONE, and the reason the scope is not a `--label` on the query. The
    label is attached AT PICK, so a member nobody has picked yet carries NONE -- which is the state
    of every issue in a unit that has just been opened by `/agrim-define`. A label-scoped query
    would report a brand-new unit as drained, i.e. would fail in precisely the situation the flag
    exists for. `docs/branching-model.md` §14: membership is §4's declaration pair, never the label
    alone."""
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10), _issue(11, body="Feature: %s\n" % UNIT)])
        assert lp._next(base, src, cfg) == ("goal", "11")


def test_a_body_declaration_for_another_unit_is_not_membership():
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(12, body="Feature: %s\n" % OTHER)])
        assert lp._next(base, src, cfg) == ("DONE", None)


def test_a_self_contradicting_issue_is_not_a_member():
    """`features.read` raises `AmbiguousUnit` rather than picking between two rival declarations,
    and its docstring obliges every sweep to catch that PER ISSUE -- letting it out of the filter
    would turn one hand-edited issue into a total outage of the queue. Excluding is also right on
    the merits: `attach_at_pick` refuses such an issue anyway, so admitting it would only move the
    refusal later and buy a wasted claim-lock round trip."""
    src = _mod("sources")
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=_gh([]))
    gh.scope_to_feature(UNIT)
    rival = {"number": 9, "body": "Feature: %s\nFeature: %s\n" % (UNIT, OTHER), "labels": []}
    assert gh._issue_in_feature(rival) is False
    # ...and the sweep survives it rather than raising out of the pick
    gh_all = src.GitHubSource({"discovery": {"source": "github"}},
                              run=_gh([_issue(9, body=rival["body"]), _issue(11, LABEL)]))
    gh_all._BACKLOG_READ_RETRY_BASE = 0
    gh_all.scope_to_feature(UNIT)
    assert gh_all.next_pending() == "11"


def test_membership_is_case_insensitive_like_github_label_names():
    """GitHub label names are case-insensitively unique, so `Feature:Voice` and `feature:voice`
    cannot both exist on a repo -- a case-sensitive comparison would be bypassed by a capital, the
    same reasoning `feature_labels.is_feature_label` already carries."""
    src = _mod("sources")
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=_gh([]))
    gh.scope_to_feature(UNIT)
    assert gh._issue_in_feature({"labels": [{"name": "Feature:Voice-Interview"}]}) is True
    assert gh._issue_in_feature({"body": "Feature: VOICE-INTERVIEW\n", "labels": []}) is True


def test_an_unscoped_source_admits_everything_without_reading_a_body():
    src = _mod("sources")
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=_gh([]))
    assert gh._issue_in_feature({"labels": [], "body": "Feature: %s\n" % OTHER}) is True
    assert gh._issue_in_feature(None) is True


# --- every path that can hand the loop a goal ------------------------------------------------------

def test_next_batch_fills_every_slot_from_inside_the_unit_and_then_stops():
    lp = _mod("loop")
    issues = [_issue(10), _issue(11, LABEL), _issue(12, OTHER_LABEL), _issue(13, LABEL)]
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, parallel={"goals": {"enabled": True, "max_concurrent": 4}})
        cfg, src = _source(lp, base, issues)
        picks = lp.next_batch(base, src, cfg)
    assert picks == [("goal", "11"), ("goal", "13"), ("DONE", None)]


def test_the_unpark_sweep_cannot_hand_the_run_a_goal_outside_the_unit(monkeypatch):
    """`_auto_unpark_sweep` flips `sdlc:parked` -> `sdlc:goal` and holds its own goals back from
    the SAME call's pick -- but they are pickable from the next call on, which is the one path by
    which an out-of-unit goal could re-enter a scoped run after it started. It cannot: an unparked
    goal is an ordinary `sdlc:goal` issue and re-enters the same scoped query as everything else."""
    lp = _mod("loop")

    class Unparks10:
        def sweep_unpark(self, sdlc_dir, config, apply=False, run=None):
            return {"apply": True, "checked": 1, "eligible": 1, "actions": [], "unparked": ["10"]}

    real_load = lp._load
    monkeypatch.setattr(lp, "_load",
                        lambda name: Unparks10() if name == "auto_unpark" else real_load(name))
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, discovery={"source": "github", "github": {"repo": "acme/widget"},
                                   "auto_unpark": {"mode": "on"}})
        cfg, src = _source(lp, base, [_issue(10), _issue(11, LABEL)])
        assert lp._next(base, src, cfg) == ("goal", "11")
        assert lp._next(base, src, cfg, extra_skip={"11"}) == ("DONE", None)


# --- byte-identical when the flag is absent --------------------------------------------------------

def _query(*extra_labels):
    """#1829: the REST shape `_fetch_pending` now constructs (`gh api
    repos/{owner}/{repo}/issues`), replacing the old `gh issue list --search sort:created-asc`
    graphql-search-billed shape. `extra_labels` are ANDed onto the base `sdlc:goal` via the
    comma-joined `labels=` field, mirroring `_fetch_pending`'s own `[self.goal_label,
    *extra_labels]` construction."""
    labels = ",".join(["sdlc:goal", *extra_labels])
    return ["api", "repos/acme/widget/issues", "--method", "GET",
            "-f", "labels=" + labels, "-f", "state=open",
            "-f", "sort=created", "-f", "direction=asc",
            "-f", "per_page=100", "-f", "page=1"]


def test_the_backlog_queries_are_unchanged_when_no_feature_is_given():
    """Pinned as the literal argument vectors, not as "contains no feature label" -- an
    unconditional addition anywhere in the base query breaks this, in the right place. Both stock
    queries are here: the backlog read, and `blocking_priority_override`'s (default-on) second."""
    lp = _mod("loop")
    calls = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _, src = _source(lp, base, [_issue(10)], calls, scope=None)
        src._BACKLOG_READ_RETRIES = 1                  # one attempt each, so retries don't blur this
        src.next_pending()
    assert calls == [_query(), _query("sdlc:blocking")]


def test_the_scope_adds_one_field_to_the_same_queries_and_no_call():
    """Cost, stated as a diff rather than a claim: the scope rides on the queries that were already
    being made. #1829: EVEN MORE so now than before this fix -- REST has no field-selection
    concept, so `body` comes back on every issues read regardless of whether a feature is scoped
    (verified live). The scoped and unscoped picker calls are therefore BYTE-IDENTICAL, not merely
    "one field different": zero marginal request cost for scoping at all, against a measured
    ~35-45 GraphQL calls per pick on a 5,000/hour bucket shared by every session on the account."""
    lp = _mod("loop")
    plain, scoped = [], []
    issues = [_issue(10), _issue(11, LABEL)]
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _, a = _source(lp, base, issues, plain, scope=None)
        a._BACKLOG_READ_RETRIES = 1
        a.next_pending()
        _, b = _source(lp, base, issues, scoped)
        b._BACKLOG_READ_RETRIES = 1
        b.next_pending()
    plain_picks, scoped_picks = _picks(plain), _picks(scoped)
    assert plain_picks and len(scoped_picks) == len(plain_picks)
    assert scoped_picks == plain_picks, "byte-identical: REST returns body regardless of scope"


def test_the_blocking_query_cannot_reach_round_the_scope():
    """`_blocking_priority_pending` is a SECOND query with its own extra `sdlc:blocking` label, and
    its whole purpose is to sort something ahead of everything else. If the membership filter
    lived at one call site rather than in `_fetch_pending`, a blocking-labelled goal from another
    unit would hijack a scoped run's very first pick."""
    lp = _mod("loop")
    calls = []
    issues = [_issue(500, "sdlc:blocking"), _issue(11, LABEL)]
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, discovery={"source": "github", "github": {"repo": "acme/widget"},
                                   "blocking_priority_override": True})
        _, src = _source(lp, base, issues, calls)
        assert src.next_pending() == "11"
    assert any(_queried(c, "sdlc:blocking") for c in _picks(calls)), "the blocking query never ran"


def test_the_tier_widening_queries_cannot_reach_round_the_scope():
    """The other second-query path: when the 200-issue window is full, `_pending_by_priority` fires
    one query per more-urgent tier. A P0 from another unit must not win a scoped run either."""
    lp = _mod("loop")
    issues = [_issue(n, LABEL) for n in range(1, 201)] + [_issue(500, "priority:P0")]
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _, src = _source(lp, base, issues)
        assert src.next_pending() == "1"


def test_a_member_beyond_the_window_is_still_reachable_by_its_label():
    """The one thing the client-side filter cannot do: reach past the 200-oldest window. A unit is
    usually NEW work, so on a backlog deeper than the cap a scoped run would otherwise find its own
    unit entirely invisible and report it drained. One extra query, and only on a cap-hit.

    #1829 FINDING, fixed separately by #1834 (see
    `test_a_unit_entirely_beyond_the_window_is_still_reachable` below): if the window is ENTIRELY
    excluded (zero in-window members), `_fetch_pending` returns `pending=None` -- the same shape a
    genuine transport error produces -- and `_pending_by_priority` used to short-circuit on that
    shape BEFORE the feature-label widening block below it ever ran, so a unit ENTIRELY beyond the
    cap window was invisible. Reproduced at the time against origin/main's own unmodified code with
    a `--limit`-enforcing fake (this file's pre-#1829 fake never actually enforced `--limit` at
    all, so this test was passing without ever exercising the widening path it claims to prove -- a
    pre-existing test-fidelity gap, not something #1829 introduced). This fixture keeps ONE
    in-window member (200) so `pending` is genuinely non-empty and the mechanism's real, working
    half is what gets proven: reaching an ADDITIONAL member (500) beyond the window once the base
    fetch already found at least one, with 500 given the more urgent priority so it must be the
    one that wins the pick, not merely be present in the merged pool. The zero-in-window case this
    docstring used to name as unfixed is now its own test, immediately below.

    CORRECTED by an independent post-#1829-implementation review: an earlier version of this
    fixture totalled EXACTLY 200 issues (198 filler + #199 + #500), the same as
    `_BACKLOG_FETCH_CAP` -- so the base fetch's own pagination (100+100) captured all 200,
    including #500, before the widening query ever ran, making the widening assertion below true
    but VACUOUS (`next_pending() == "500"` held with or without widening ever firing; confirmed by
    calling `_fetch_pending([], ())` alone against that fixture and observing #500 already
    present). 199 filler issues + #200 + #500 = 201 total, one genuinely beyond the 200-cap
    window, so #500 is ABSENT from the base fetch and reachable only through the widening query
    this test claims to prove."""
    lp = _mod("loop")
    calls = []
    issues = ([_issue(n) for n in range(1, 200)] + [_issue(200, LABEL)]
              + [_issue(500, LABEL, "priority:P0")])
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _, src = _source(lp, base, issues, calls)
        src._BACKLOG_READ_RETRIES = 1
        raw_count, base_only = src._fetch_pending([], ())
        assert raw_count == 200 and all(p["number"] != 500 for p in (base_only or [])), (
            "fixture must genuinely put #500 beyond the 200-cap window, or the widening "
            "assertion below is vacuous")
        assert src.next_pending() == "500"
    assert [c for c in _picks(calls) if _queried(c, LABEL)] == [_query(LABEL)]


def test_a_unit_entirely_beyond_the_window_is_still_reachable():
    """#1834: the gap the previous test's docstring named but deliberately did not fix -- a scoped
    run whose ENTIRE 200-issue window has ZERO members of the unit. The base fetch's client-side
    `_issue_in_feature` filter excludes every fetched issue, so `_fetch_pending` returns its
    `(raw_count, None)` "nothing survived filtering" shape -- indistinguishable, by return value
    alone, from a genuine-read-failure's `(0, None)` -- and `_pending_by_priority`'s early
    `if pending is None: return None` used to fire on EITHER meaning, before the feature-label
    widening query a few lines below it ever ran. A unit whose only member sits entirely beyond
    the window was reported drained even though the widening mechanism built to reach exactly that
    member was one `if` check away.

    Fixture straight from the issue's own repro: 200 issues carrying NO unit label at all (the
    whole window is non-members) plus one, #500, past the cap, that carries it."""
    lp = _mod("loop")
    calls = []
    issues = [_issue(n) for n in range(1, 201)] + [_issue(500, LABEL)]
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _, src = _source(lp, base, issues, calls)
        src._BACKLOG_READ_RETRIES = 1
        raw_count, base_only = src._fetch_pending([], ())
        assert raw_count == 200 and base_only is None, (
            "fixture must genuinely exclude every in-window issue, or this isn't #1834's case")
        assert src.read_degraded() is False, (
            "a filtered-to-empty read is not a transport failure -- read_degraded() must stay False")
        assert src.next_pending() == "500"
    assert [c for c in _picks(calls) if _queried(c, LABEL)] == [_query(LABEL)]


def test_the_label_query_is_not_paid_when_the_window_is_not_full():
    """Inert on every backlog measured so far, including this repo's own 101 open goals."""
    lp = _mod("loop")
    calls = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _, src = _source(lp, base, [_issue(11, LABEL)], calls)
        src.next_pending()
    assert not [c for c in _picks(calls) if _queried(c, LABEL)]


def test_a_source_that_was_never_scoped_reports_no_feature():
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _, src = _source(lp, base, [], scope=None)
        assert src.feature is None and src.feature_label is None


# --- validation, up front --------------------------------------------------------------------------

class _ScopeStub:
    def scope_to_feature(self, unit):
        self.unit = unit


@pytest.mark.parametrize("name", ["voice interview", "a/b", "TBD.", "v1..2", "voice.lock", "",
                                  "..", "with\ttab", "feature/x"])
def test_a_name_that_cannot_be_a_unit_is_refused_before_anything_is_queried(name):
    src = _mod("sources")
    with pytest.raises(src.FeatureScopeError):
        src.scope_to_feature(_ScopeStub(), name)


@pytest.mark.parametrize("name", [UNIT, OTHER, "voice.LOCK", "a.lockfile", "v1.2", "A-B_c"])
def test_a_name_git_would_accept_as_a_branch_segment_is_accepted(name):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _, s = _source(lp, base, [], scope=None)
        assert lp.sources.scope_to_feature(s, name) == name
        assert s.feature_label == "feature:" + name


def test_the_validator_is_the_one_in_features_not_a_second_copy():
    """One definition, read by every half of the branching model. A second predicate here would be
    free to drift from the one `features.parse_body`/`parse_labels` apply, and a name accepted by
    the flag but rejected by the parser scopes a run to a label nothing can ever carry."""
    src, features = _mod("sources"), _mod("features")
    corpus = ["voice-interview", "billing", "a", "a.b", "voice.LOCK", "a.lockfile", "v1.2",
              "A-B_c", "x" * 200, "voice interview", "a/b", "TBD.", "v1..2", "voice.lock",
              "", "..", ".", "a..b", "with\ttab", "feature/x", "a b c", "x\n", " x"]
    for name in corpus:
        try:
            src.scope_to_feature(_ScopeStub(), name)
            refused = False
        except src.FeatureScopeError:
            refused = True
        assert features._is_unit_name(name) is not refused, name


def test_a_backlog_source_with_no_unit_to_scope_to_is_refused_rather_than_matched_against_nothing():
    """LocalSource's goals are FILES: they carry no labels and cannot declare a unit, so a
    `--feature` run against one would silently match zero goals forever and read as a drained
    unit. Say so instead."""
    src = _mod("sources")
    with pytest.raises(src.FeatureScopeError) as exc:
        src.scope_to_feature(src.LocalSource("/tmp", {}), UNIT)
    assert "local-goals" in str(exc.value)


def test_scoping_to_none_is_a_no_op():
    src = _mod("sources")
    stub = _ScopeStub()
    assert src.scope_to_feature(stub, None) is None
    assert not hasattr(stub, "unit")


# --- the drained-unit message ----------------------------------------------------------------------

def test_a_drained_unit_is_reported_as_such_and_names_the_board_it_could_not_reach(capsys):
    """"Nothing pickable" and "this unit has nothing pickable, though the board does" are different
    facts, and the second is the one an operator needs -- otherwise a scoped run that stops looks
    exactly like a finished backlog. Same voice as the dependency-hold summary (#1499)."""
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10), _issue(12, OTHER_LABEL)])
        assert lp._next(base, src, cfg) == ("DONE", None)
    err = capsys.readouterr().err
    assert UNIT in err and "#10" in err and "the rest of the board does" in err


def test_a_drained_unit_on_a_drained_board_says_so_instead(capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [])
        assert lp._next(base, src, cfg) == ("DONE", None)
    err = capsys.readouterr().err
    assert UNIT in err and "neither has the rest of the board" in err


def test_an_unscoped_run_says_none_of_this(capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [], scope=None)
        assert lp._next(base, src, cfg) == ("DONE", None)
    assert "unit" not in capsys.readouterr().err


def test_the_drained_probe_is_paid_once_and_only_on_the_terminal():
    """The only thing the flag ADDS: one unscoped backlog read (which on a stock config is two
    REST calls, the second being `blocking_priority_override`'s). Paid once, by a run that is
    already stopping, and never per pick -- against a measured ~35-45 GraphQL calls per pick on a
    5,000/hour bucket shared by every session on the account.

    #1829: a scoped and an unscoped picker call are now BYTE-IDENTICAL requests (REST always
    returns `body`, verified live -- see `test_the_scope_adds_one_field_to_the_same_queries_and_
    no_call`), so there is no request-SHAPE signal left to catch the probe on specifically. This
    counts picker calls instead of matching shape, which is what the property actually is: the
    drained probe adds exactly 2 extra picker reads (the base fetch + the blocking-priority
    override's), and only on the call that reports DONE.

    The scoped attempt on the second call costs only ONE read, not two: with #10 outside the unit
    and #11 skipped, the base fetch's own client-side filter excludes everything, so
    `_fetch_pending` returns its "propagate" sentinel (`pending=None`) and `_pending_by_label`'s
    own `if pending is None: return None` short-circuits before ever reaching
    `_blocking_priority_pending` -- so 1 (scoped, short-circuited) + 2 (the probe's unscoped base
    + blocking) = 3 new calls on this second `_next()`, not 4."""
    lp = _mod("loop")
    calls = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10), _issue(11, LABEL)], calls)
        src._BACKLOG_READ_RETRIES = 1
        assert lp._next(base, src, cfg) == ("goal", "11")
        picks_after_first = len(_picks(calls))
        assert lp._next(base, src, cfg, extra_skip={"11"}) == ("DONE", None)
        picks_after_second = len(_picks(calls))
    assert picks_after_second - picks_after_first == 3, (
        "the drained probe must cost exactly 2 extra picker reads, paid only on the terminal call "
        "(plus the one short-circuited scoped attempt that triggered it)")


def test_the_probe_does_not_clobber_the_degraded_read_flag():
    """`_emit_run_stop_once` reads `source.read_degraded()` on the DONE path to tell a genuinely
    drained backlog from a masked read failure. The probe is another backlog read, so it would
    overwrite that flag unless it puts it back."""
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _, src = _source(lp, base, [_issue(10)])
        src._last_read_degraded = True
        src.pending_outside_feature()
        assert src.read_degraded() is True


# --- composition ------------------------------------------------------------------------------------

def test_feature_and_skip_compose():
    lp = _mod("loop")
    issues = [_issue(10), _issue(11, LABEL), _issue(13, LABEL)]
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, issues)
        assert lp._next(base, src, cfg, extra_skip={"11"}) == ("goal", "13")
        # ...and `--skip` never widens the scope back out to #10
        assert lp._next(base, src, cfg, extra_skip={"11", "13"}) == ("DONE", None)


def test_feature_does_not_defeat_the_dependency_hold():
    """A blocked goal INSIDE the unit is still blocked (#1499/#1650). The scope narrows which goals
    are candidates; it does not lower the bar for the ones that are."""
    lp = _mod("loop")
    blocked = _issue(11, LABEL, body="Blocked by #10\n")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10), blocked])   # #10 is open, and outside the unit
        assert lp._next(base, src, cfg) == ("DONE", None)


def test_the_dependency_hold_still_sees_a_blocker_that_lives_outside_the_unit():
    """The board mirror the hold resolves against is deliberately NOT scoped: a prerequisite is very
    often in another unit (or in none), and a scoped mirror would resolve it to `unknown`, which
    fails OPEN -- turning `--feature` into a way of switching the dependency gate off.

    #1833: `mirror.py` migrated onto the same REST shape the picker uses (see
    `test_backlog_sync_never_uses_the_graphql_search_field`-style controls in test_mirror.py), so
    its own open query is now byte-identical to the picker's base query -- both `labels=sdlc:goal`,
    both genuinely unscoped by feature, since scoping has never been anything but a client-side
    post-filter (see `test_the_scope_adds_one_field_to_the_same_queries_and_no_call` above). There
    is no longer a distinguishing WIRE shape to isolate "the mirror's own call" by, so this proves
    the property directly instead: the dependency hold actually refreshed the board mirror (a
    `state=closed` REST call -- the one request only `mirror.fetch_and_write` ever issues, never
    the picker) and #10's record genuinely landed in that corpus despite carrying no unit label."""
    lp = _mod("loop")
    calls = []
    blocked = _issue(11, LABEL, body="Blocked by #10\n")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        cfg, src = _source(lp, base, [_issue(10), blocked], calls)
        lp._next(base, src, cfg)
        mirror_ran = any(_rest_field(c, "state") == "closed" for c in calls if _is_rest_issues_call(c))
        assert mirror_ran, "the dependency hold must have refreshed the board mirror"
        records = _mod("mirror").read_mirror(base)
    assert any(r["number"] == 10 for r in records), "the board mirror must stay unscoped"


# --- the board path ----------------------------------------------------------------------------------

def test_a_scoped_run_reads_the_label_queue_not_the_board():
    """`gh project item-list` returns a card's LABELS but never its BODY, so the board cannot see
    whether an unlabelled card belongs to the unit -- and a board-ordered scoped run would skip
    every member that has not been picked before, which is the whole failure this design exists to
    avoid. Losing drag-order WITHIN one unit is the smaller loss, and it lasts only as long as the
    flag does."""
    src = _mod("sources")
    board_reads = []
    gh = src.GitHubSource({"discovery": {"source": "github", "github": {
        "repo": "acme/widget",
        "project": {"enabled": True, "owner": "o", "title": "t", "number": 1}}}},
        run=_gh([_issue(11, LABEL)]))
    gh._BACKLOG_READ_RETRY_BASE = 0
    gh._board_queue = lambda: board_reads.append(1) or None
    gh.next_pending()
    assert board_reads == [1]                  # unscoped: the board decides, exactly as before
    gh.scope_to_feature(UNIT)
    assert gh.next_pending() == "11"
    assert board_reads == [1]                  # scoped: not read at all


def test_the_board_is_read_again_once_the_scope_is_lifted_for_the_probe():
    """The drained probe asks "would an UNSCOPED run have work", so it must ask the way an
    unscoped run does -- board and all."""
    src = _mod("sources")
    board_reads = []
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=_gh([_issue(10)]))
    gh._BACKLOG_READ_RETRY_BASE = 0
    gh._board_queue = lambda: board_reads.append(1) or None
    gh.scope_to_feature(UNIT)
    gh.pending_outside_feature()
    assert board_reads == [1]


# --- the CLI ------------------------------------------------------------------------------------------

def _wire(lp, monkeypatch, run):
    monkeypatch.setattr(lp.sources, "_run_gh", lambda a, binary="gh": run(a))
    monkeypatch.setattr(lp.sources.GitHubSource, "_BACKLOG_READ_RETRY_BASE", 0)


@pytest.mark.parametrize("verb", ["next", "next-batch"])
def test_the_cli_scopes_both_verbs(verb, monkeypatch, capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _wire(lp, monkeypatch, _gh([_issue(10), _issue(11, LABEL)]))
        assert lp.main(["loop.py", verb, base, "--feature", UNIT]) == 0
        assert capsys.readouterr().out.split() == ["11"]


def test_a_bad_name_on_the_cli_exits_two_and_queries_nothing(monkeypatch, capsys):
    lp = _mod("loop")
    calls = []
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _wire(lp, monkeypatch, _gh([_issue(10)], calls))
        assert lp.main(["loop.py", "next", base, "--feature", "voice interview"]) == 2
    err = capsys.readouterr().err
    assert "--feature" in err and "voice interview" in err
    assert not _picks(calls)


def test_a_bare_feature_flag_is_refused_rather_than_read_as_a_unit_named_true(monkeypatch, capsys):
    """`_flags` gives a valueless flag the string `"true"`, and `true` IS a legal unit name -- so
    without an explicit guard `--feature` alone would silently scope the run to `feature:true`."""
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d)
        _wire(lp, monkeypatch, _gh([_issue(10)]))
        assert lp.main(["loop.py", "next", base, "--feature"]) == 2
    assert "--feature" in capsys.readouterr().err


def test_a_local_goals_project_is_told_on_the_cli(monkeypatch, capsys):
    lp = _mod("loop")
    with tempfile.TemporaryDirectory() as d:
        base = _base(d, discovery={"source": "local-goals"})
        (pathlib.Path(base) / "goals").mkdir()
        assert lp.main(["loop.py", "next", base, "--feature", UNIT]) == 2
    assert "local-goals" in capsys.readouterr().err


def test_the_value_survives_a_following_flag():
    """`--feature <name> --skip a` must not swallow the name; membership in `_VALUE_FLAGS` is what
    guarantees it, and #541 is the bug that rule exists for."""
    lp = _mod("loop")
    assert lp._flags(["--feature", UNIT, "--skip", "1,2"]) == {"feature": UNIT, "skip": "1,2"}
    assert "feature" in lp._VALUE_FLAGS


def test_the_usage_line_documents_the_flag(capsys):
    lp = _mod("loop")
    assert lp.main(["loop.py"]) == 2
    assert capsys.readouterr().err.count("--feature") == 2   # on `next` and on `next-batch`

"""cross_repo.py -- the cross-repo access check and the landing tier it selects (#1472, epic #1464).

THE ONE PROPERTY EVERY TEST HERE IS REALLY ABOUT. A permissions failure discovered at merge time is
the most expensive moment to discover it, so the check is explicit, it runs at PICK time, and it
never fails open. "Never fails open" has a precise meaning that is easy to get almost-right:

    a check that cannot get an answer must not answer.

The tempting bug is to treat an unanswered check as a denial -- a 502, a rate limit, a timeout, a
404 returned because the WRONG ACCOUNT was active -- and silently select tier 2 (the fallback) as
though access had really been measured and found missing. Tier 2 looks harmless: it is the safe
strategy. It is not harmless, because it was chosen on no evidence, it records "no access" against a
repo that may be fully accessible, and it raises a ledger request to a human that nobody needed to
answer. So the outcome vocabulary has THREE members where two would do -- `tier-1`, `tier-2` and
`flagged` -- and the whole suite is arranged around the seam between the last two.

THE WRONG-ACCOUNT PROBLEM, WHICH IS THE HARD HALF. GitHub answers a read of a private repo you
cannot see with 404 -- byte-identical to the answer for a repo that does not exist. On a device with
several `gh` accounts (this one has three, and the active one drifts because other projects switch
it), that means the SAME repo reads as "denied" or "granted" depending on nothing but which keyring
slot happens to be active. There is no way to tell those two 404s apart from the 404 itself, so this
module does not try: it pins the IDENTITY that produced the answer, and refuses to trust any verdict
produced by an identity that is not the configured one. `test_the_same_404_is_denial_or_flag_purely
_by_identity` is that proof, and it is deliberately written as one 404 read twice.

WRITTEN AGAINST A MUTATION RUN, and the mutants are all at the LOOSENING edge -- the direction where
a line still executes and only the accepted language changes. Each `_mod_with` test asserts the
mutant's BEHAVIOUR differs from HEAD, never merely that the suite went red: an assertion about a
mutant is worth nothing until the mutant has been shown to behave differently on some input, which
is the lesson `tests/test_feature_registry.py` records from three separate harness bugs on one
module.
"""
import importlib.util
import json
import pathlib
import tempfile
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "agrim-loop" / "scripts"
P = S / "cross_repo.py"


def _mod():
    spec = importlib.util.spec_from_file_location("cross_repo", P)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mod_with(old, new):
    """The module rebuilt with a source substitution applied, every occurrence.

    Both guards are inherited from `tests/test_feature_registry.py`, which learned them the hard
    way: the target is asserted to exist (a drifted target reports "no survivors" while testing
    nothing) and EVERY occurrence is replaced (this module quotes its own rules in prose above the
    lines implementing them, so a first-occurrence replace mutates the docstring and leaves the code
    untouched -- a green report over an unmutated module)."""
    src = P.read_text(encoding="utf-8")
    assert old in src, "mutation target has drifted out of the source: %r" % (old,)
    namespace = {"__name__": "cross_repo_variant", "__file__": str(P)}
    exec(compile(src.replace(old, new), str(P), "exec"), namespace)          # noqa: S102 - test-only
    return types.SimpleNamespace(**namespace)


# --------------------------------------------------------------------------- fixtures


def _runner(responses, calls=None):
    """A fake `gh` runner. `responses` maps a matched substring of the joined argv to
    `(returncode, stdout, stderr)`; the first match wins, and an unmatched call is a test bug."""
    def run(args):
        joined = " ".join(str(a) for a in args)
        if calls is not None:
            calls.append(joined)
        for needle, reply in responses.items():
            if needle in joined:
                return reply
        raise AssertionError("unstubbed gh call: %s" % joined)
    return run


def _repo_json(push=True, archived=False, private=True, **over):
    body = {"full_name": "org/whatever", "private": private, "archived": archived,
            "permissions": {"admin": False, "maintain": False, "push": push,
                            "triage": push, "pull": True}}
    body.update(over)
    return json.dumps(body)


OK_USER = (0, "loop-actor\n", "")
NOT_FOUND = (1, '{"message":"Not Found","status":"404"}', "gh: Not Found (HTTP 404)")


def _config(**over):
    cfg = {"discovery": {"source": "github",
                         "github": {"repo": "org/a", "assignee": "loop-actor"}},
           "ledger": {"enabled": True, "actor": "loop-actor"}}
    cfg.update(over)
    return cfg


def _issue(body="", labels=()):
    """The RAW REST issue object `work._declared_unit` fetches and hands to `features.read`
    unreduced -- labels as `{"name": ...}` dicts, which is the shape gh actually returns."""
    return json.dumps({"number": 1472, "title": "t", "body": body,
                       "labels": [{"name": n} for n in labels]})


def _entry(repos=None, **over):
    base = {"title": "Manifest duration contract", "owner": "@feature-owner", "open": True,
            "parent": None, "tracking_issue": "org/a#3100",
            "repos": repos if repos is not None else {
                "org/a": {"branch": "feature/int-contract", "owner": "@a-owner",
                          "authorized": True, "goals": [1472]},
                "org/b": {"branch": "feature/int-contract", "owner": "@b-owner",
                          "authorized": True, "goals": []}}}
    base.update(over)
    return base


def _sdlc(d, unit="int-contract", entry=None, goal="1472"):
    """A `.sdlc` carrying a registry with one unit. The goal's DECLARATION now lives in the issue
    payload the runner answers with, not in a local file -- the unit is resolved through
    `work._declared_unit`, the same dual read `work.start()` bases the worktree on."""
    base = pathlib.Path(d) / ".sdlc"
    (base / "state").mkdir(parents=True)
    (base / "goals").mkdir()
    (base / "config.json").write_text(json.dumps(_config()), encoding="utf-8")
    if unit is not None:
        units = base / "features" / "units"
        units.mkdir(parents=True)
        (units / (unit + ".json")).write_text(json.dumps(
            {"schema": "sigma/features@1",
             "features": {unit: entry if entry is not None else _entry()}}), encoding="utf-8")
    (base / "goals" / (goal + ".md")).write_text(
        "---\nid: %s\nstatus: pending\n---\nbody\n" % goal, encoding="utf-8")
    return str(base)


def _work():
    spec = importlib.util.spec_from_file_location("work_for_pin", S / "work.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _work_run(responses):
    """`work.py`'s own runner shape -- `run(cwd, argv)` -> stdout, raising on a non-zero exit."""
    def runner(cwd, argv):
        joined = " ".join(str(a) for a in argv)
        for needle, (code, out, err) in responses.items():
            if needle in joined:
                if code != 0:
                    raise RuntimeError(err or out)
                return out
        raise AssertionError("unstubbed gh call: %s" % joined)
    return runner


# --------------------------------------------------------------------------- 1. the vocabulary


def test_the_verdicts_and_outcomes_are_closed_sets():
    """Three verdicts, not two: `unknown` is a first-class answer, and every branch below depends on
    it being distinguishable from `denied`."""
    c = _mod()
    assert c.VERDICTS == (c.GRANTED, c.DENIED, c.UNKNOWN)
    assert c.FLAGGED in c.OUTCOMES and c.TIER_1 in c.OUTCOMES and c.TIER_2 in c.OUTCOMES


def test_the_tier_number_is_derived_only_for_the_two_real_tiers():
    c = _mod()
    assert c.tier_of(c.TIER_1) == 1 and c.tier_of(c.TIER_2) == 2
    assert c.tier_of(c.FLAGGED) is None and c.tier_of(c.NOT_CROSS_REPO) is None


# --------------------------------------------------------------------------- 2. identity


def test_identity_is_confirmed_when_the_active_login_is_the_configured_one():
    c = _mod()
    ident = c.identity(_config(), run=_runner({"api user": OK_USER}))
    assert ident["confirmed"] is True and ident["login"] == "loop-actor" and ident["reason"] is None


def test_identity_is_not_confirmed_when_a_different_account_is_active():
    """The whole wrong-account defence starts here: the token is valid, the call succeeds, and the
    answer is still not one this repo may reason from."""
    c = _mod()
    ident = c.identity(_config(), run=_runner({"api user": (0, "other-actor\n", "")}))
    assert ident["confirmed"] is False and ident["reason"] == c.WRONG_ACCOUNT
    assert ident["login"] == "other-actor" and ident["expected"] == "loop-actor"


def test_identity_comparison_ignores_case_because_github_logins_do():
    c = _mod()
    ident = c.identity(_config(), run=_runner({"api user": (0, "Loop-Actor\n", "")}))
    assert ident["confirmed"] is True


def test_identity_is_unresolved_when_the_whoami_call_itself_fails():
    """If we cannot say who we are, we cannot say what a 404 means."""
    c = _mod()
    ident = c.identity(_config(), run=_runner({"api user": (1, "", "gh: HTTP 503")}))
    assert ident["confirmed"] is False and ident["reason"] == c.IDENTITY_UNRESOLVED


def test_an_at_me_assignee_is_not_an_identity_pin():
    """`@me` is the shipped default and names nobody, so it cannot be compared against -- but the
    login that answered is still recorded, which is what makes a later verdict auditable."""
    c = _mod()
    cfg = {"discovery": {"github": {"repo": "org/a", "assignee": "@me"}}}
    ident = c.identity(cfg, run=_runner({"api user": (0, "someone-else\n", "")}))
    assert ident["confirmed"] is True and ident["expected"] is None
    assert ident["login"] == "someone-else"


# --------------------------------------------------------------------------- 3. the access check


CONFIRMED = {"login": "loop-actor", "expected": "loop-actor", "confirmed": True, "reason": None}


def test_push_permission_is_access():
    c = _mod()
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": (0, _repo_json(), "")}))
    assert got["verdict"] == c.GRANTED


def test_read_only_is_a_denial_even_though_the_api_answered_200():
    """The failure this goal exists to prevent, in its quietest form: the repo reads fine, and the
    PR cannot be pushed. Discovered at merge time, that is a dead branch."""
    c = _mod()
    got = c.check_access("org/b", CONFIRMED,
                         run=_runner({"api repos/org/b": (0, _repo_json(push=False), "")}))
    assert got["verdict"] == c.DENIED and got["reason"] == c.READ_ONLY


def test_maintain_or_admin_without_push_still_counts_as_access():
    c = _mod()
    payload = json.dumps({"archived": False,
                          "permissions": {"admin": True, "maintain": False, "push": False,
                                          "triage": True, "pull": True}})
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": (0, payload, "")}))
    assert got["verdict"] == c.GRANTED


def test_an_archived_repo_is_a_denial_however_good_the_permissions_look():
    """`push: true` on an archived repo is a lie GitHub tells you until you try to write."""
    c = _mod()
    got = c.check_access("org/b", CONFIRMED,
                         run=_runner({"api repos/org/b": (0, _repo_json(archived=True), "")}))
    assert got["verdict"] == c.DENIED and got["reason"] == c.ARCHIVED


def test_a_payload_with_no_permissions_block_is_unknown_never_granted():
    c = _mod()
    payload = json.dumps({"full_name": "org/b", "archived": False})
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": (0, payload, "")}))
    assert got["verdict"] == c.UNKNOWN


PERMISSION_SHAPES = [
    ("absent", {"archived": False}, "UNKNOWN"),
    ("empty", {"archived": False, "permissions": {}}, "UNKNOWN"),
    ("pull-only", {"archived": False, "permissions": {"pull": True}}, "UNKNOWN"),
    ("triage-only", {"archived": False, "permissions": {"pull": True, "triage": True}}, "UNKNOWN"),
    ("non-bool", {"archived": False, "permissions": {"push": "false"}}, "UNKNOWN"),
    ("null-push", {"archived": False, "permissions": {"push": None}}, "UNKNOWN"),
    ("stated-false", {"archived": False,
                      "permissions": {"admin": False, "maintain": False, "push": False,
                                      "triage": False, "pull": True}}, "DENIED"),
    ("stated-true", {"archived": False,
                     "permissions": {"admin": False, "maintain": False, "push": True}}, "GRANTED"),
]


@pytest.mark.parametrize("name,payload,verdict", PERMISSION_SHAPES,
                         ids=[s[0] for s in PERMISSION_SHAPES])
def test_a_200_only_denies_when_it_actually_states_a_write_flag(name, payload, verdict):
    """B2. The type gate was `for k in (...) if k in permissions` -- a no-op when the keys are simply
    absent. So `{}` and `{"pull": true}` type-checked nothing, `.get` returned `None` three times,
    and the function fell through to `read-only`: a POSITIVE statement that this account cannot
    push, derived from a payload GitHub never answered with, which then selects tier 2 and raises a
    ledger note. An absent `push` key is exactly as much of an answer as an absent `permissions`
    block, and the two rows that DO deny are the ones where the payload actually says so."""
    c = _mod()
    got = c.check_access("org/b", CONFIRMED,
                         run=_runner({"api repos/org/b": (0, json.dumps(payload), "")}))
    assert got["verdict"] == getattr(c, verdict), "%s -> %s" % (name, got)


def test_the_stated_flag_gate_is_load_bearing():
    head = _mod()
    mutant = _mod_with("    if not stated:\n", "    if False and not stated:\n")
    payload = json.dumps({"archived": False, "permissions": {"pull": True}})
    got_head = head.check_access("org/b", CONFIRMED,
                                 run=_runner({"api repos/org/b": (0, payload, "")}))
    got_mut = mutant.check_access("org/b", CONFIRMED,
                                  run=_runner({"api repos/org/b": (0, payload, "")}))
    assert got_head["verdict"] == head.UNKNOWN and got_mut["verdict"] == mutant.DENIED
    assert mutant.decide(_entry(), [_acc("org/a", mutant.GRANTED), got_mut])["outcome"] == mutant.TIER_2


def test_a_disabled_repo_is_a_denial_like_an_archived_one():
    """N5. GitHub disables a repo for a lapsed bill or a suspended org and keeps reporting the
    permissions the account WOULD have -- the same lie `archived` tells, on the same object."""
    c = _mod()
    payload = json.dumps({"disabled": True, "permissions": {"push": True}})
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": (0, payload, "")}))
    assert got["verdict"] == c.DENIED and got["reason"] == c.DISABLED


def test_unparseable_success_output_is_unknown_never_granted():
    c = _mod()
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": (0, "<html>", "")}))
    assert got["verdict"] == c.UNKNOWN


def test_a_malformed_repo_name_is_unknown_not_denied_and_is_never_sent():
    """Unanswerable is not the same as answered no -- and a typo in the registry is a thing a human
    must see, not a thing that quietly selects the fallback tier.

    The REASON is asserted, not merely the verdict, and so is the empty call list. Without both, a
    module that dropped the guard entirely still passes: the malformed name reaches the runner, the
    runner refuses it, and the resulting `unclassified` is also `unknown` -- the right answer for
    the wrong reason, which is the shape a mutation run exists to catch."""
    c = _mod()
    calls = []
    for bad in ("", "notarepo", "a/b/c", None, 7, "org/", "/repo", ".hidden/x", "org/x?y"):
        got = c.check_access(bad, CONFIRMED, run=_runner({}, calls))
        assert got["verdict"] == c.UNKNOWN and got["reason"] == c.MALFORMED_REPO, bad
    assert calls == []


# --- 3b. the transient set: none of these may ever read as a denial --------------------------------

TRANSIENT_SHAPES = [
    ("server-500", (1, "", "gh: Internal Server Error (HTTP 500)")),
    ("server-502", (1, "", "gh: Bad Gateway (HTTP 502)")),
    ("server-503", (1, "", "gh: Service Unavailable (HTTP 503)")),
    ("server-504", (1, "", "gh: Gateway Timeout (HTTP 504)")),
    ("rate-limit", (1, '{"message":"API rate limit exceeded"}',
                    "gh: API rate limit exceeded for user (HTTP 403)")),
    ("secondary", (1, "", "gh: You have exceeded a secondary rate limit (HTTP 403)")),
    ("too-many", (1, "", "gh: Too Many Requests (HTTP 429)")),
    ("dns", (1, "", "dial tcp: lookup api.github.com: no such host")),
    ("resolve", (1, "", "could not resolve host: api.github.com")),
    ("reset", (1, "", "read tcp: connection reset by peer")),
    ("timeout", (1, "", "Post \"https://api.github.com\": context deadline exceeded (timeout)")),
    ("unauth", (1, '{"message":"Bad credentials"}', "gh: Bad credentials (HTTP 401)")),
    ("sso", (1, "", "gh: Resource protected by organization SAML enforcement (HTTP 403)")),
    ("proxy", (1, "", "GitHub access is not enabled for this session. An org admin must connect "
                      "the Claude GitHub App for this organization.")),
    ("garbage", (1, "", "gh: something nobody has ever seen before")),
    ("empty", (1, "", "")),
]


@pytest.mark.parametrize("name,reply", TRANSIENT_SHAPES, ids=[s[0] for s in TRANSIENT_SHAPES])
def test_no_transient_failure_is_ever_read_as_no_access(name, reply):
    """The headline. Every one of these leaves the question unanswered, and an unanswered question
    must not become an answer -- least of all the answer that quietly selects the fallback."""
    c = _mod()
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": reply}))
    assert got["verdict"] == c.UNKNOWN, "%s classified as %s" % (name, got["verdict"])


@pytest.mark.parametrize("name,reply", TRANSIENT_SHAPES, ids=[s[0] for s in TRANSIENT_SHAPES])
def test_a_transient_failure_degrades_the_whole_decision_to_flagged(name, reply):
    """One level up: the tier decision, not merely the per-repo verdict. This is the assertion the
    issue actually makes -- "a transient API failure degrades to flagged rather than being read as
    no access" -- and it is a different statement from the one above, because a decision could
    perfectly well classify a repo `unknown` and still fall through to tier 2."""
    c = _mod()
    accesses = [{"repo": "org/a", "verdict": c.GRANTED, "reason": None, "detail": ""},
                c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": reply}))]
    decision = c.decide(_entry(), accesses)
    assert decision["outcome"] == c.FLAGGED, name
    assert decision["tier"] is None


def test_the_transient_vocabulary_the_backlog_source_already_uses_stays_covered():
    """An anti-drift pin, deliberately NOT an import.

    `sources.GitHubSource._TRANSIENT` is a retry heuristic for `gh project` calls, where a false
    positive costs one extra attempt; this module classifies by HTTP status shape, because here a
    misclassification costs a wrong landing tier. Sharing one table would force the looser rule onto
    the stricter caller. What must not drift is the CONTAINMENT: anything that source already calls
    transient must still be unknown here, never a denial."""
    c = _mod()
    spec = importlib.util.spec_from_file_location("sources_for_pin", S / "sources.py")
    sources = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sources)
    for needle in sources.GitHubSource._TRANSIENT:
        reply = (1, "", "gh: request failed: %s" % needle)
        got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": reply}))
        assert got["verdict"] == c.UNKNOWN, needle


# --- 3c. the wrong-account 404, which is the hard half --------------------------------------------


def test_a_404_from_the_configured_account_is_a_real_denial():
    c = _mod()
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": NOT_FOUND}))
    assert got["verdict"] == c.DENIED and got["reason"] == c.NOT_VISIBLE


def test_the_same_404_is_denial_or_flag_purely_by_identity():
    """THE proof that a wrong-account 404 was made distinguishable. Identical response bytes, read
    twice; the only difference is which account produced them, and that alone decides whether the
    answer may be believed."""
    c = _mod()
    drifted = {"login": "other-actor", "expected": "loop-actor", "confirmed": False,
               "reason": c.WRONG_ACCOUNT}
    as_configured = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": NOT_FOUND}))
    as_drifted = c.check_access("org/b", drifted, run=_runner({"api repos/org/b": NOT_FOUND}))
    assert as_configured["verdict"] == c.DENIED
    assert as_drifted["verdict"] == c.UNKNOWN and as_drifted["reason"] == c.WRONG_ACCOUNT


def test_an_unconfirmed_identity_costs_the_api_call_it_cannot_interpret():
    """And it never even asks: an answer it may not read is not worth a request, and a runner that
    is called at all here would prove the guard runs after the read rather than before it."""
    c = _mod()
    drifted = {"login": "x", "expected": "y", "confirmed": False, "reason": c.WRONG_ACCOUNT}
    calls = []
    c.check_access("org/b", drifted, run=_runner({}, calls))
    assert calls == []


def test_an_unconfirmed_identity_also_suppresses_a_positive_verdict():
    """Granted is a verdict too. A drifted account with push access would select tier 1 and then
    fail at merge under the account that actually runs it, so no verdict survives an unpinned
    identity -- not just the negative ones."""
    c = _mod()
    drifted = {"login": "x", "expected": "y", "confirmed": False, "reason": c.WRONG_ACCOUNT}
    got = c.check_access("org/b", drifted, run=_runner({"api repos/org/b": (0, _repo_json(), "")}))
    assert got["verdict"] == c.UNKNOWN


def test_a_missing_or_malformed_identity_is_treated_as_unconfirmed():
    c = _mod()
    for ident in (None, {}, {"confirmed": "yes"}, {"confirmed": 1}, "confirmed"):
        got = c.check_access("org/b", ident, run=_runner({}))
        assert got["verdict"] == c.UNKNOWN, ident


# --------------------------------------------------------------------------- 4. the tier decision


def _acc(repo, verdict, reason=None):
    return {"repo": repo, "verdict": verdict, "reason": reason, "detail": ""}


def test_access_to_both_repos_selects_tier_one():
    c = _mod()
    decision = c.decide(_entry(), [_acc("org/a", c.GRANTED), _acc("org/b", c.GRANTED)])
    assert decision["outcome"] == c.TIER_1 and decision["tier"] == 1


def test_no_access_to_the_sibling_selects_tier_two():
    c = _mod()
    decision = c.decide(_entry(), [_acc("org/a", c.GRANTED),
                                   _acc("org/b", c.DENIED, "not-visible")])
    assert decision["outcome"] == c.TIER_2 and decision["tier"] == 2
    assert decision["denied"] == ["org/b"]


def test_one_unknown_outranks_any_number_of_denials():
    """Precedence, stated as a rule rather than left to fall out of the loop order: the unknown repo
    could be the one that changes the answer, so no tier may be selected while one is outstanding."""
    c = _mod()
    decision = c.decide(_entry(), [_acc("org/a", c.DENIED, "read-only"),
                                   _acc("org/b", c.UNKNOWN, "server-error")])
    assert decision["outcome"] == c.FLAGGED and decision["tier"] is None
    assert decision["unknown"] == ["org/b"]


def test_a_single_repo_unit_is_not_cross_repo_and_selects_no_tier():
    """Tier 1 is a tracking issue plus a both-green gate. A unit that touches one repo has no
    sibling to gate against, so it must not be labelled tier 1 and drag #1474's gate in behind it."""
    c = _mod()
    entry = _entry(repos={"org/a": {"branch": "feature/x", "owner": "@a", "authorized": True,
                                    "goals": [1]}})
    decision = c.decide(entry, [_acc("org/a", c.GRANTED)])
    assert decision["outcome"] == c.NOT_CROSS_REPO and decision["cross_repo"] is False


def test_a_unit_naming_no_repos_at_all_is_not_cross_repo():
    c = _mod()
    assert c.decide(_entry(repos={}), [])["outcome"] == c.NOT_CROSS_REPO


def test_a_repo_named_in_the_registry_but_never_measured_is_unknown_not_fine():
    """The quietest way to fail open: decide over whatever verdicts happen to be in hand. A caller
    that skipped a repo -- or crashed halfway down the list -- would otherwise produce a confident
    tier 1 over a repo nobody ever asked about."""
    c = _mod()
    decision = c.decide(_entry(), [_acc("org/a", c.GRANTED)])
    assert decision["outcome"] == c.FLAGGED and decision["unknown"] == ["org/b"]
    assert decision["repos"]["org/b"]["reason"] == c.UNMEASURED


def test_a_repos_block_that_is_not_a_mapping_is_flagged_not_read_as_single_repo():
    """N6. `feature_registry.normalise_entry` turns a non-mapping `repos` into `{}` -- correct, and
    its contract -- but `{}` then arrived here indistinguishable from a unit that genuinely names
    none and was answered "nothing spans a boundary". That is a conclusion drawn from a value nobody
    could parse. The normalisation is #1469's; the INTERPRETATION is this module's."""
    c = _mod()
    for bad in (["org/a", "org/b"], "org/a", 7):
        got = c.decide(_entry(repos=bad), [])
        assert got["outcome"] == c.FLAGGED, bad
        assert "repos block" in got["why"]


def test_a_unit_recording_no_repos_yet_is_not_cross_repo_and_says_which_case_it_was():
    """The other side of N6, and the trade is deliberate: `repos` is filled by propagation (#1477),
    so before that runs an empty block is the ordinary state of every freshly-recorded unit.
    Flagging it would flag nearly every unit today and teach a human to ignore the flag. The `why`
    names the case so the record never claims more than it measured."""
    c = _mod()
    got = c.decide(_entry(repos={}), [])
    assert got["outcome"] == c.NOT_CROSS_REPO and "records no repos" in got["why"]


def test_a_verdict_outside_the_closed_set_is_read_as_unknown():
    c = _mod()
    decision = c.decide(_entry(), [_acc("org/a", c.GRANTED), _acc("org/b", "probably-fine")])
    assert decision["outcome"] == c.FLAGGED


def test_the_decision_names_every_repo_it_measured():
    c = _mod()
    decision = c.decide(_entry(), [_acc("org/a", c.GRANTED), _acc("org/b", c.DENIED, "archived")])
    assert set(decision["repos"]) == {"org/a", "org/b"}
    assert decision["repos"]["org/b"]["reason"] == "archived"


# --------------------------------------------------------------------------- 5. pick time

#: The issue read `work._declared_unit` makes. The unit is resolved through the SAME dual read
#: `work.start()` bases the worktree on, so these fixtures are raw REST issue objects, not bodies.
def _gh(issue=None, **repos):
    """A runner answering the issue fetch plus whatever repo reads a test needs."""
    responses = {}
    if issue is not None:
        responses["issues/1472"] = (0, issue, "")
    responses.update({"api user": OK_USER})
    for name, reply in repos.items():
        responses["repos/org/" + name.replace("repo_", "")] = reply
    return responses


BODY_ISSUE = _issue(body="Feature: int-contract\n")
LABEL_ISSUE = _issue(body="no marker in this body at all\n", labels=["feature:int-contract"])
BOTH_GREEN = {"issues/1472": (0, BODY_ISSUE, ""), "api user": OK_USER,
              "api repos/org/a": (0, _repo_json(), ""), "api repos/org/b": (0, _repo_json(), "")}


def test_the_check_runs_at_pick_and_records_its_decision():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        run = _runner({"issues/1472": (0, BODY_ISSUE, ""), "api user": OK_USER,
                       "api repos/org/a": (0, _repo_json(), ""),
                       "api repos/org/b": (0, _repo_json(push=False), "")})
        decision = c.check_at_pick(base, "1472", _config(), run=run)
        assert decision["outcome"] == c.TIER_2
        assert json.loads(c.decision_path(base, "1472").read_text())["outcome"] == c.TIER_2


def test_a_unit_declared_by_LABEL_ALONE_reaches_the_check():
    """THE regression this file exists for after review.

    An issue can declare its unit by `feature:<name>` LABEL with no body marker at all, and
    `work.start()` honours it -- it cuts the worktree from `feature/int-contract`. An earlier version
    of this module read the body alone, so for exactly this issue it recorded `no-unit`, made zero
    `gh` calls and never flagged: the worktree was cut from the unit branch while the record said the
    goal was not part of a unit. `no-unit` is a positive claim, not an admission of ignorance, so
    nothing downstream had any reason to look.

    Asserted against BOTH readers, in one test, because the property that matters is not "the label
    is understood" but "the two agree" -- one reader is what guarantees it, and this is what would
    fail if anybody re-introduced a second."""
    c = _mod()
    work = _work()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        calls = []
        run = _runner({"issues/1472": (0, LABEL_ISSUE, ""), "api user": OK_USER,
                       "api repos/org/a": (0, _repo_json(), ""),
                       "api repos/org/b": NOT_FOUND}, calls)
        decision = c.check_at_pick(base, "1472", _config(), run=run)

        # the base resolver's own answer, from the identical payload
        declared, _note, resolved = work._declared_unit(
            _config(), "1472", _work_run({"issues/1472": (0, LABEL_ISSUE, "")}), d)
        assert (declared, resolved) == ("int-contract", True)

        assert decision["unit"] == "int-contract"          # not None, and not `no-unit`
        assert decision["outcome"] == c.TIER_2             # the check actually ran
        assert any("repos/org/b" in one for one in calls)  # the access call was really made


def test_a_body_declared_unit_still_reaches_the_check():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        decision = c.check_at_pick(base, "1472", _config(), run=_runner(BOTH_GREEN))
        assert decision["unit"] == "int-contract" and decision["outcome"] == c.TIER_1


def test_the_recorded_decision_is_readable_without_any_api_call():
    """The merge-time consumer (#1474) reads what pick time decided. It is handed a runner that
    raises, so a `recorded()` that re-checked would be impossible to miss."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        c.check_at_pick(base, "1472", _config(), run=_runner(BOTH_GREEN))
        got = c.recorded(base, "1472")
        assert got["outcome"] == c.TIER_1 and got["goal"] == "1472"


def test_recorded_takes_no_runner_at_all():
    """Structural, not behavioural: `recorded` cannot perform the check because it has nowhere to
    put a runner. That is what makes "at pick time, not at merge time" a property of the shape
    rather than a promise about call order."""
    import inspect
    c = _mod()
    assert "run" not in inspect.signature(c.recorded).parameters


def test_a_merge_time_read_with_no_pick_time_decision_is_absent_not_a_fresh_check():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        assert c.recorded(base, "1472") is None


def test_a_project_that_never_adopted_the_registry_costs_no_api_call_but_is_still_recorded():
    """Two properties, and the second one is B5's fix.

    Free: no registry directory means no issue fetch and no `gh` call. But the decision IS written,
    because `recorded()` telling #1474 "`None` means nothing ever ran" is unsatisfiable if `None` is
    also the everyday answer for every project that has not adopted the branching model -- which is
    every current adopter. A gate that reads `None` as a refusal would then refuse everything, and
    one that reads it as "fine" is the implied-tier misread the contract forbids. `not-adopted` is a
    distinct value; discarding it threw away the only thing that made the two distinguishable."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, unit=None)
        calls = []
        decision = c.check_at_pick(base, "1472", _config(), run=_runner({}, calls))
        assert decision["outcome"] == c.NOT_ADOPTED and calls == []
        assert c.recorded(base, "1472")["outcome"] == c.NOT_ADOPTED


def test_every_outcome_is_recorded_so_none_is_reserved_for_never_ran():
    """The contract stated as one assertion over the whole outcome space."""
    c = _mod()
    cases = {
        c.NOT_ADOPTED: (None, {}),
        c.NO_UNIT: (BODY_ISSUE, {"issues/1472": (0, _issue(body="nothing declared\n"), "")}),
        c.TIER_1: (BODY_ISSUE, BOTH_GREEN),
        c.FLAGGED: (BODY_ISSUE, {"issues/1472": (0, BODY_ISSUE, ""), "api user": OK_USER,
                                 "api repos/org/a": (0, _repo_json(), ""),
                                 "api repos/org/b": (1, "", "gh: Bad Gateway (HTTP 502)")}),
    }
    for outcome, (_issue_doc, responses) in cases.items():
        with tempfile.TemporaryDirectory() as d:
            base = _sdlc(d, unit=None if outcome == c.NOT_ADOPTED else "int-contract")
            got = c.check_at_pick(base, "1472", _config(), run=_runner(responses))
            assert got["outcome"] == outcome
            assert c.recorded(base, "1472")["outcome"] == outcome, outcome


def test_a_goal_declaring_no_unit_is_not_cross_repo():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        calls = []
        run = _runner({"issues/1472": (0, _issue(body="no marker here\n"), "")}, calls)
        decision = c.check_at_pick(base, "1472", _config(), run=run)
        assert decision["outcome"] == c.NO_UNIT
        assert not any("api user" in one for one in calls)


def test_an_unreadable_issue_is_flagged_rather_than_assumed_single_repo():
    """Not knowing which unit a goal belongs to is the same class of ignorance as not knowing
    whether a repo is reachable: it must not resolve itself into a tier. `work._declared_unit` draws
    exactly this line for the base it resolves, and this module inherits it rather than re-deciding
    -- a failed read is NOT "declares nothing"."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        run = _runner({"issues/1472": (1, "", "gh: Bad Gateway (HTTP 502)")})
        decision = c.check_at_pick(base, "1472", _config(), run=run)
        assert decision["outcome"] == c.FLAGGED and decision["why"]


def test_an_empty_issue_payload_is_a_failed_read_not_an_absent_declaration():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        decision = c.check_at_pick(base, "1472", _config(),
                                   run=_runner({"issues/1472": (0, "null", "")}))
        assert decision["outcome"] == c.FLAGGED


def test_a_unit_with_no_registry_entry_is_flagged():
    """A goal declaring `Feature: x` with nothing recorded under `x` cannot be shown to be
    single-repo -- the registry is a backup that can be missing, not a census."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        run = _runner({"issues/1472": (0, _issue(body="Feature: other-unit\n"), "")})
        decision = c.check_at_pick(base, "1472", _config(), run=run)
        assert decision["outcome"] == c.FLAGGED


def test_a_self_contradicting_issue_is_flagged_not_crashed():
    """`features.read` raises `AmbiguousUnit` by design so nobody silently picks a base. A pick-time
    check must turn that into a flag, never into an exception that takes out the queue."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        doc = _issue(body="Feature: int-contract\nFeature: other-unit\n")
        decision = c.check_at_pick(base, "1472", _config(),
                                   run=_runner({"issues/1472": (0, doc, "")}))
        assert decision["outcome"] == c.FLAGGED


def test_a_local_mode_goal_declares_nothing_exactly_as_the_base_resolver_says():
    """Consistency, not capability. In local mode `work.start()` bases on the configured base and
    reads no declaration at all, so this module must see no unit either -- a body marker in a local
    goal file that changed the TIER but not the BASE would be the same disagreement as B1, pointing
    the other way."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        calls = []
        cfg = {"discovery": {"source": "local"}, "ledger": {"enabled": True, "actor": "loop-actor"}}
        decision = c.check_at_pick(base, "1472", cfg, run=_runner({}, calls))
        assert decision["outcome"] == c.NO_UNIT and calls == []


# --------------------------------------------------------------------------- 6. the ledger raise


def _ledger_lines(base):
    d = pathlib.Path(base) / "ledger" / "entries"
    if not d.is_dir():
        return []
    return [json.loads(line) for f in sorted(d.glob("*.jsonl"))
            for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


DENIED_B = {"issues/1472": (0, BODY_ISSUE, ""), "api user": OK_USER,
            "api repos/org/a": (0, _repo_json(), ""), "api repos/org/b": NOT_FOUND}


def test_tier_two_raises_the_unavailable_half_addressed_to_that_repos_owner():
    """"The unavailable half is raised through the ledger, addressed to whoever owns it" -- through
    the `to` field that already exists, with no second transport invented for it."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        decision = c.check_at_pick(base, "1472", _config(), run=_runner(DENIED_B))
        assert decision["outcome"] == c.TIER_2
        # #1574: `@b-owner` in the registry, `b-owner` in the ledger -- one rule, one namespace.
        raised = [e for e in _ledger_lines(base) if e.get("to") == "b-owner"]
        assert len(raised) == 1 and "org/b" in raised[0]["why"]


def test_the_raise_happens_once_however_often_the_goal_is_re_picked():
    """A re-pick is routine -- `_auto_reclaim_stale_claims` exists to cause one -- and the raise was
    unconditional, so three picks of one unreachable unit put three identical notes in front of its
    owner. The decision record already on disk is the watermark."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        for _ in range(3):
            decision = c.check_at_pick(base, "1472", _config(), run=_runner(DENIED_B))
        assert decision["outcome"] == c.TIER_2 and decision["raise_suppressed"] is True
        assert len(_ledger_lines(base)) == 1


def test_a_changed_ask_does_raise_again():
    """Suppression is per ASK, not per goal: a repo that has just become unreachable must reach its
    owner even though an earlier pick already raised about a different one."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, entry=_entry(repos={
            "org/a": {"branch": "f", "owner": "@a-owner", "authorized": True, "goals": []},
            "org/b": {"branch": "f", "owner": "@b-owner", "authorized": True, "goals": []}}))
        c.check_at_pick(base, "1472", _config(), run=_runner(DENIED_B))
        both = dict(DENIED_B); both["api repos/org/a"] = NOT_FOUND
        c.check_at_pick(base, "1472", _config(), run=_runner(both))
        tos = sorted(e["to"] for e in _ledger_lines(base))
        assert tos == ["a-owner", "b-owner", "b-owner"]


def test_a_reason_that_merely_reworded_itself_does_not_re_raise():
    """The watermark compares the DECISION, never the note text -- a reason can legitimately move
    between picks (`server-error` one pick, `network` the next) while the ask is identical."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        c.check_at_pick(base, "1472", _config(), run=_runner(DENIED_B))
        other = dict(DENIED_B)
        other["api repos/org/b"] = (0, _repo_json(push=False), "")     # denied, different reason
        decision = c.check_at_pick(base, "1472", _config(), run=_runner(other))
        assert decision["raise_suppressed"] is True and len(_ledger_lines(base)) == 1


def test_the_raise_reaches_the_feature_owner_when_the_repo_records_none():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        entry = _entry(repos={"org/a": {"branch": "f", "owner": "@a-owner", "authorized": True,
                                        "goals": []},
                              "org/b": {"branch": "f", "owner": None, "authorized": True,
                                        "goals": []}})
        base = _sdlc(d, entry=entry)
        c.check_at_pick(base, "1472", _config(), run=_runner(DENIED_B))
        assert [e for e in _ledger_lines(base) if e.get("to") == "feature-owner"]


def test_an_unaddressable_raise_is_still_written_and_says_so():
    """A request nobody owns must not evaporate. It is recorded, and the decision reports that it
    reached no inbox -- which is the ONLY signal there is: `agrim-doctor`'s unaddressable-hand-off
    check inspects a CODEOWNERS/`ledger.owners` roster, never the registry entry's own `owner`, so
    it reports green on exactly this case."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        entry = _entry(owner=None,
                       repos={"org/a": {"branch": "f", "owner": None, "authorized": True,
                                        "goals": []},
                              "org/b": {"branch": "f", "owner": None, "authorized": True,
                                        "goals": []}})
        base = _sdlc(d, entry=entry)
        decision = c.check_at_pick(base, "1472", _config(), run=_runner(DENIED_B))
        assert decision["unaddressed"] == ["org/b"]
        assert [e for e in _ledger_lines(base) if e.get("kind") == "note"]


def test_a_flagged_decision_raises_to_the_feature_owner_who_owns_the_unit_end_to_end():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        run = _runner({"issues/1472": (0, BODY_ISSUE, ""), "api user": OK_USER,
                       "api repos/org/a": (0, _repo_json(), ""),
                       "api repos/org/b": (1, "", "gh: Bad Gateway (HTTP 502)")})
        decision = c.check_at_pick(base, "1472", _config(), run=run)
        assert decision["outcome"] == c.FLAGGED
        assert len([e for e in _ledger_lines(base) if e.get("to") == "feature-owner"]) == 1


def test_tier_one_raises_nothing():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        c.check_at_pick(base, "1472", _config(), run=_runner(BOTH_GREEN))
        assert [e for e in _ledger_lines(base) if e.get("kind") == "note"] == []


def test_the_raise_is_a_note_not_a_handoff_so_tier_two_work_still_proceeds():
    """Tier 2's whole promise is that nothing is lost when access is missing -- not that everything
    stops. `handoff` is the one kind `backlog_check` reads as a real block, so raising the missing
    half as a handoff would park the goal it just told to carry on."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        c.check_at_pick(base, "1472", _config(), run=_runner(DENIED_B))
        assert {e["kind"] for e in _ledger_lines(base)} == {"note"}


def test_a_ledger_that_is_switched_off_still_records_the_decision():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        cfg = _config(ledger={"enabled": False})
        decision = c.check_at_pick(base, "1472", cfg, run=_runner(DENIED_B))
        assert decision["outcome"] == c.TIER_2
        assert c.recorded(base, "1472")["outcome"] == c.TIER_2


# --------------------------------------------------------------------------- 7. never breaks a pick


def test_an_exploding_runner_flags_rather_than_raising():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)

        def boom(args):
            raise OSError("gh: killed")
        decision = c.check_at_pick(base, "1472", _config(), run=boom)
        assert decision["outcome"] == c.FLAGGED


@pytest.mark.parametrize("cfg", [
    {"discovery": "github"},
    {"discovery": {"github": "org/repo"}},
    {"discovery": {"github": {"assignee": ["a"]}}},
    ["discovery"],
    "discovery",
    None,
], ids=["discovery-str", "github-str", "assignee-list", "config-list", "config-str", "none"])
def test_a_malformed_config_never_raises_out_of_identity(cfg):
    """`identity` promises "never raises", and the promise was false for every one of these: the
    `(x or {}).get(...)` idiom reduces `None` and `{}` and then raises `AttributeError` on any other
    non-mapping. The same idiom had already been found and fixed twelve lines away, which is why the
    reducer is now a named helper used everywhere rather than an idiom applied by hand."""
    c = _mod()
    got = c.identity(cfg, run=_runner({"api user": OK_USER}))
    assert got["confirmed"] in (True, False) and got["login"] in ("loop-actor", None)


@pytest.mark.parametrize("cfg", [
    {"discovery": "github"},
    {"discovery": {"github": "org/repo"}},
    "config",
    None,
], ids=["discovery-str", "github-str", "config-str", "none"])
def test_a_malformed_config_flags_rather_than_producing_nothing(cfg):
    """The outer half of the same promise. `check_at_pick` documents "NEVER RAISES", and a config
    that raised past every inner guard produced no record and no flag at all -- `loop` swallowed it
    and the module was simply silent for a class of input it documents as flaggable."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        decision = c.check_at_pick(base, "1472", cfg, run=_runner({"issues/1472": (0, BODY_ISSUE, ""),
                                                                   "api user": OK_USER,
                                                                   "api repos/org/": (0, _repo_json(), "")}))
        assert decision["outcome"] in c.OUTCOMES
        assert c.recorded(base, "1472") is not None


def test_an_unwritable_record_does_not_break_the_decision():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        (pathlib.Path(base) / "state" / "landing").write_text("not a directory", encoding="utf-8")
        decision = c.check_at_pick(base, "1472", _config(), run=_runner(BOTH_GREEN))
        assert decision["outcome"] == c.TIER_1


def test_an_unsafe_goal_id_can_never_address_a_path():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        for bad in ("../../etc/passwd", "a/b", ""):
            with pytest.raises(ValueError):
                c.decision_path(base, bad)


def test_a_record_from_a_schema_this_code_does_not_know_reads_as_absent():
    """Same ruling as the feature registry makes for `index.json`: reading a `@2` document with `@1`
    semantics is the exact failure a version key exists to prevent, and for a MERGE gate the
    degradation has to be "no decision recorded", never a half-understood one."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        p = c.decision_path(base, "1472")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": "sigma/landing@2", "outcome": "tier-1"}),
                     encoding="utf-8")
        assert c.recorded(base, "1472") is None


def test_a_corrupt_record_reads_as_absent():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        p = c.decision_path(base, "1472")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        assert c.recorded(base, "1472") is None


# --------------------------------------------------------------------------- 8. the loop wiring


def _loop():
    spec = importlib.util.spec_from_file_location("loop", S / "loop.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _local_backlog(d):
    """A local-mode `.sdlc` the real `loop._next` can drain -- the wiring test is about WHEN the
    check runs, not what it decides, so the spy stands in for the decision entirely."""
    base = pathlib.Path(d) / ".sdlc"
    (base / "goals").mkdir(parents=True)
    (base / "state").mkdir()
    (base / "config.json").write_text(json.dumps({"budget": {"max_iterations": 10}}),
                                      encoding="utf-8")
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n",
                                             encoding="utf-8")
    (base / "state" / "review-queue.md").write_text("# Q\n", encoding="utf-8")
    (base / "goals" / "0001.md").write_text("---\nid: 0001\nstatus: pending\n---\nx\n",
                                            encoding="utf-8")
    return str(base)


def test_the_real_pick_path_runs_the_check_before_anything_is_built():
    """End to end through `loop._next`, which is the pick. The assertion that matters is the last
    one: at the instant the check runs, neither `.sdlc/work/` (the goal's worktree) nor
    `.sdlc/state/work/` (its record) exists -- so no branch has been cut and no PR can exist."""
    lp = _loop()
    seen = {}
    with tempfile.TemporaryDirectory() as d:
        base = _local_backlog(d)

        class Spy:
            @staticmethod
            def check_at_pick(sdlc_dir, goal, config, run=None):
                seen["goal"] = goal
                seen["built"] = sorted(
                    str(q.relative_to(sdlc_dir))
                    for q in (pathlib.Path(sdlc_dir) / "work",
                              pathlib.Path(sdlc_dir) / "state" / "work") if q.exists())
                return {"outcome": "tier-1"}
        lp._CROSS_REPO = Spy
        src = lp.sources.get_source(base, lp.state.load_config(base))
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert kind == "goal" and str(goal).endswith("0001.md")
        assert seen["goal"] == goal
        assert seen["built"] == []


def test_the_wiring_passes_no_source_so_one_reader_resolves_the_unit():
    """The signature is the guarantee. `check_at_pick` used to take a backlog `source` and read the
    issue BODY through it; it now takes none, because the unit comes from `work._declared_unit` --
    the same dual read `work.start()` uses. A wiring that still handed a source would be a wiring
    that could still grow a second reader."""
    import inspect
    c = _mod()
    lp = _loop()
    assert "source" not in inspect.signature(c.check_at_pick).parameters
    assert "source" not in inspect.signature(lp._check_cross_repo_at_pick).parameters


def test_a_failing_check_never_breaks_the_pick():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        base = _local_backlog(d)
        rang = []

        class Boom:
            @staticmethod
            def check_at_pick(*a, **k):
                rang.append(True)
                raise RuntimeError("no")
        lp._CROSS_REPO = Boom
        src = lp.sources.get_source(base, lp.state.load_config(base))
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        # `rang` first: without it this test passes just as happily on a loop that never calls the
        # check at all, which is the vacuous direction a negative control found.
        assert rang == [True]
        assert kind == "goal"


# --------------------------------------------------------------------------- 9. mutation guards


def test_the_unclassified_fallback_is_unknown_and_a_denial_fallback_changes_behaviour():
    """The single most valuable line in the module: what an UNRECOGNISED failure becomes. HEAD says
    unknown; the mutant says denied, and the same input then selects tier 2 off no evidence."""
    head, mutant = _mod(), _mod_with("return _verdict(repo, UNKNOWN, UNCLASSIFIED",
                                     "return _verdict(repo, DENIED, UNCLASSIFIED")
    reply = (1, "", "gh: something nobody has ever seen before")
    got_head = head.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": reply}))
    got_mut = mutant.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": reply}))
    assert got_head["verdict"] == head.UNKNOWN and got_mut["verdict"] == mutant.DENIED
    assert head.decide(_entry(), [_acc("org/a", head.GRANTED), got_head])["outcome"] == head.FLAGGED
    assert mutant.decide(_entry(), [_acc("org/a", mutant.GRANTED), got_mut])["outcome"] == mutant.TIER_2


def test_a_non_boolean_permission_flag_is_no_answer_not_a_denial():
    """The subtle half of "never fails open", and the one a reviewer skims past. A payload carrying
    the STRING "false" is refused by `is True` either way, so nobody reads it as ACCESS -- it falls
    through to `read-only` instead and quietly selects tier 2 off a payload nothing could read.
    HEAD's type gate stops that; the mutant that drops it produces exactly that silent fallback."""
    head = _mod()
    mutant = _mod_with('if any(not isinstance(permissions.get(k), bool)',
                       'if any(False and not isinstance(permissions.get(k), bool)')
    payload = json.dumps({"archived": False,
                          "permissions": {"admin": False, "maintain": False, "push": "false"}})
    got_head = head.check_access("org/b", CONFIRMED,
                                 run=_runner({"api repos/org/b": (0, payload, "")}))
    got_mut = mutant.check_access("org/b", CONFIRMED,
                                  run=_runner({"api repos/org/b": (0, payload, "")}))
    assert got_head["verdict"] == head.UNKNOWN and got_mut["verdict"] == mutant.DENIED
    assert head.decide(_entry(), [_acc("org/a", head.GRANTED), got_head])["outcome"] == head.FLAGGED
    assert mutant.decide(_entry(), [_acc("org/a", mutant.GRANTED), got_mut])["outcome"] == mutant.TIER_2


def test_a_real_read_only_permission_block_is_still_a_denial():
    """The type gate must not swallow the genuine case it sits next to: all-boolean flags with
    `push: false` is what GitHub returns for a public repo you do not maintain, and that IS a
    measured denial -- measured against `gh api repos/cli/cli` on this account."""
    c = _mod()
    payload = json.dumps({"archived": False,
                          "permissions": {"admin": False, "maintain": False, "push": False,
                                          "triage": False, "pull": True}})
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": (0, payload, "")}))
    assert got["verdict"] == c.DENIED and got["reason"] == c.READ_ONLY


def test_archived_must_be_literally_true_so_a_string_cannot_fake_a_denial():
    head = _mod()
    mutant = _mod_with('repo_json.get("archived") is True', 'repo_json.get("archived")')
    payload = json.dumps({"archived": "no", "permissions": {"push": True}})
    assert head.check_access("org/b", CONFIRMED, run=_runner(
        {"api repos/org/b": (0, payload, "")}))["verdict"] == head.GRANTED
    assert mutant.check_access("org/b", CONFIRMED, run=_runner(
        {"api repos/org/b": (0, payload, "")}))["verdict"] == mutant.DENIED


def test_the_identity_guard_must_be_is_true_not_truthy():
    """`confirmed` arriving as any truthy value from a caller that built the dict by hand must not
    pass a gate whose whole job is refusing anything it did not itself confirm."""
    head = _mod()
    mutant = _mod_with('ident.get("confirmed") is True', 'ident.get("confirmed")')
    ident = {"login": "x", "expected": "y", "confirmed": "no", "reason": "wrong-account"}
    run = _runner({"api repos/org/b": (0, _repo_json(), "")})
    assert head.check_access("org/b", ident, run=_runner({}))["verdict"] == head.UNKNOWN
    assert mutant.check_access("org/b", ident, run=run)["verdict"] == mutant.GRANTED


def test_unknown_must_outrank_denied_and_the_reverse_order_changes_the_tier():
    head = _mod()
    mutant = _mod_with("if unknown:\n        outcome = FLAGGED\n    elif denied:",
                       "if denied:\n        outcome = TIER_2\n    elif unknown:")
    accesses = [_acc("org/a", "denied", "read-only"), _acc("org/b", "unknown", "server-error")]
    assert head.decide(_entry(), accesses)["outcome"] == head.FLAGGED
    assert mutant.decide(_entry(), accesses)["outcome"] == mutant.TIER_2


def test_the_expected_login_comparison_must_be_case_folded():
    head = _mod()
    mutant = _mod_with("login.casefold() != expected", "login != expected")
    run = _runner({"api user": (0, "Loop-Actor\n", "")})
    assert head.identity(_config(), run=run)["confirmed"] is True
    assert mutant.identity(_config(), run=_runner({"api user": (0, "Loop-Actor\n", "")})
                           )["confirmed"] is False


# --- M3 (survived an independent run): the accepted-value set of the identity pin ---------------

def test_an_at_handle_is_an_ordinary_pin_not_a_permanent_mismatch():
    """M3, and finding N1 underneath it. `assignee: "@loop-actor"` is a value the PICKER accepts --
    `sources._assignee_login` does `raw.lstrip("@").casefold()` on exactly this field. Special-casing
    only the literal `@me` meant this module compared a bare login against `"@loop-actor"`, called
    its own account drifted forever, and raised one ledger note per cross-repo pick."""
    c = _mod()
    for raw in ("@loop-actor", "loop-actor", "Loop-Actor", "@Loop-Actor"):
        cfg = {"discovery": {"github": {"assignee": raw}}}
        ident = c.identity(cfg, run=_runner({"api user": OK_USER}))
        assert ident["confirmed"] is True, raw


def test_the_pin_normalisation_is_the_one_the_picker_already_uses():
    """An anti-drift pin against `sources.py`'s own rule rather than an import -- that resolver is a
    method with caching and a network call for `@me`; only the normalisation is shareable. If the
    picker's rule ever moves, this fails rather than silently diverging again."""
    c = _mod()
    for raw in ("@loop-actor", "loop-actor", "Loop-Actor", "@ORG-bot", "x"):
        expected, filtered = c._expected_login({"discovery": {"github": {"assignee": raw}}})
        assert expected == raw.lstrip("@").casefold() and filtered is True


def test_only_the_literal_at_me_is_the_authenticated_sentinel():
    """Both directions of the guard, which is what M3 found untested. `@me` is not a pin; `me` and
    `@meep` are ordinary handles, exactly as the picker reads them."""
    c = _mod()
    assert c._expected_login({"discovery": {"github": {"assignee": "@me"}}}) == (None, True)
    assert c._expected_login({"discovery": {"github": {"assignee": "me"}}}) == ("me", True)
    assert c._expected_login({"discovery": {"github": {"assignee": "@meep"}}}) == ("meep", True)


def test_the_at_me_sentinel_must_be_exact_and_a_prefix_test_changes_behaviour():
    """M3 in mutant form: `== "@me"` loosened to `.startswith("@")` makes every `@handle` an
    unpinned identity, which is precisely the guard being removed."""
    head = _mod()
    mutant = _mod_with('if raw == "@me":', 'if raw.startswith("@"):')
    cfg = {"discovery": {"github": {"assignee": "@loop-actor"}}}
    assert head._expected_login(cfg) == ("loop-actor", True)
    assert mutant._expected_login(cfg) == (None, True)
    drifted = _runner({"api user": (0, "someone-else\n", "")})
    assert head.identity(cfg, run=drifted)["reason"] == head.WRONG_ACCOUNT
    assert mutant.identity(cfg, run=_runner({"api user": (0, "someone-else\n", "")}))[
        "confirmed"] is True


# --- N2: no pin AND no filter is not the same as `@me` ------------------------------------------

def test_an_empty_assignee_is_neither_a_pin_nor_a_filter_and_is_not_confirmed():
    """`agrim-init` ships `assignee: ""`, and `mirror.py`/`sources.py` both drop the filter entirely
    for it -- so the backlog is byte-identical under every account and the drift this module exists
    to catch is invisible at the top of the funnel AND at the bottom. `@me` earns its trust because
    the picker filters BY the authenticated account; an empty value earns nothing."""
    c = _mod()
    cfg = {"discovery": {"github": {"assignee": ""}}}
    ident = c.identity(cfg, run=_runner({"api user": OK_USER}))
    assert ident["confirmed"] is False and ident["reason"] == c.IDENTITY_UNPINNED
    assert "assignee" in ident["detail"]
    got = c.check_access("org/b", ident, run=_runner({}))
    assert got["verdict"] == c.UNKNOWN


def test_at_me_is_still_trusted_because_the_backlog_is_filtered_by_it():
    c = _mod()
    cfg = {"discovery": {"github": {"assignee": "@me"}}}
    ident = c.identity(cfg, run=_runner({"api user": (0, "whoever\n", "")}))
    assert ident["confirmed"] is True and ident["expected"] is None


# --- M5 (survived an independent run): the status anchor ----------------------------------------

def test_a_three_digit_run_in_free_text_is_not_a_status():
    r"""M5. The anchoring claim on `_STATUS_RE` was made in prose and pinned by nothing, and a bare
    `(\d{3})` survived the whole suite. This is the exact input the comment names: a failure with no
    HTTP status at all, whose message happens to contain three digits. It is the one line in the
    module that can turn free text into a denial."""
    c = _mod()
    for text in ("gh: could not reach the sku-403 mirror",
                 "gh: repo sku-404 is unavailable",
                 "gh: wrote 500 bytes then gave up",
                 "gh: issue 404 could not be parsed"):
        got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": (1, "", text)}))
        assert got["verdict"] == c.UNKNOWN, text
        assert got["reason"] == c.UNCLASSIFIED, text


def test_the_status_anchor_is_load_bearing_and_dropping_it_changes_behaviour():
    """The mutant's input is a `404` in free text, not the `403` the independent run used. Both
    survived the old suite; only one still reaches a DENIED now, because B3 made 403 non-denying --
    so 404 is the whole remaining exposure, and it is the one this pins."""
    head = _mod()
    mutant = _mod_with(r'_STATUS_RE = re.compile(r"\bhttp[ /]?(\d{3})\b")',
                       r'_STATUS_RE = re.compile(r"(\d{3})")')
    text = "gh: repo sku-404 is unavailable"
    got_head = head.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": (1, "", text)}))
    got_mut = mutant.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": (1, "", text)}))
    assert got_head["verdict"] == head.UNKNOWN and got_head["reason"] == head.UNCLASSIFIED
    assert got_mut["verdict"] == mutant.DENIED and got_mut["reason"] == mutant.NOT_VISIBLE


# --------------------------------------------------------------------------- 10. the diagnosis

TRANSIENT_REASONS = {
    "server-500": "SERVER_ERROR", "server-502": "SERVER_ERROR", "server-503": "SERVER_ERROR",
    "server-504": "SERVER_ERROR", "rate-limit": "RATE_LIMITED", "secondary": "RATE_LIMITED",
    "too-many": "RATE_LIMITED", "dns": "NETWORK", "resolve": "NETWORK", "reset": "NETWORK",
    "timeout": "NETWORK", "unauth": "UNAUTHENTICATED", "sso": "SSO_REQUIRED",
    "proxy": "SESSION_PROXY", "garbage": "UNCLASSIFIED", "empty": "UNCLASSIFIED",
}


@pytest.mark.parametrize("name,reply", TRANSIENT_SHAPES, ids=[s[0] for s in TRANSIENT_SHAPES])
def test_each_transient_shape_is_diagnosed_not_merely_shrugged_at(name, reply):
    """`unknown` is the right verdict for all of these; it is not the right MESSAGE for all of them.

    The reason is written into the ledger line a human reads and into the record #1474 reads, and
    "we could not tell" is a very different instruction from "GitHub is 502ing" or "the token is
    expired". Without this, every classifier branch could collapse into the `unclassified` fallback
    -- still `unknown`, so every verdict test stays green -- and the module would quietly lose all
    of its diagnostic value. A mutation run found exactly that: the 5xx branch was indistinguishable
    from its own removal until this test existed."""
    c = _mod()
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": reply}))
    assert got["reason"] == getattr(c, TRANSIENT_REASONS[name]), name


def test_a_bare_429_is_rate_limited_even_with_no_prose_to_match():
    """The status is the fallback channel for a body that says nothing -- gh does not always echo
    GitHub's own message."""
    c = _mod()
    got = c.check_access("org/b", CONFIRMED,
                         run=_runner({"api repos/org/b": (1, "", "gh: (HTTP 429)")}))
    assert got["verdict"] == c.UNKNOWN and got["reason"] == c.RATE_LIMITED


FORBIDDEN_SHAPES = [
    ("pat-scope", '{"message":"Resource not accessible by personal access token"}', "TOKEN_SCOPE"),
    ("integration", '{"message":"Resource not accessible by integration"}', "TOKEN_SCOPE"),
    ("actions-policy", '{"message":"You must have repository read permissions or have the '
                       'repository Actions policies fine-grained permission."}', "TOKEN_SCOPE"),
    ("collaborators", '{"message":"Must have push access to view repository collaborators."}',
     "TOKEN_SCOPE"),
    ("ip-allowlist", '{"message":"Although you appear to have the correct authorization '
                     'credentials, the organization has an IP allow list enabled."}', "IP_ALLOWLIST"),
    ("proxy-block", "<html>403 Forbidden - corporate proxy</html>", "FORBIDDEN"),
]


@pytest.mark.parametrize("name,body,reason", FORBIDDEN_SHAPES, ids=[s[0] for s in FORBIDDEN_SHAPES])
def test_no_403_is_ever_a_denial_because_this_endpoint_denies_with_404(name, body, reason):
    """B3, and it is the arm the whole classifier turns on.

    `GET /repos/{owner}/{repo}` answers "you may not have this repo" with **404** -- that is the
    whole point of 404 there, hiding a private repo's existence from someone with no right to know
    it exists. So every 403 that arrives is about something else: a token whose scopes are too
    narrow, an org IP allow-list, a corporate proxy's own block page. Each is the same KIND of fact
    as the 401 the module already refuses to read as a denial -- about the credential or the
    caller's network position -- and reading it as "no access" selects tier 2 and raises a request
    to an owner who has nothing to answer.

    The sub-reason is diagnostic and best-effort; the verdict does not depend on matching it, which
    is why the unmatchable proxy page is in this list."""
    c = _mod()
    reply = (1, body, "gh: forbidden (HTTP 403)")
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": reply}))
    assert got["verdict"] == c.UNKNOWN, name
    assert got["reason"] == getattr(c, reason), name


def test_a_403_decision_is_flagged_not_tier_two():
    c = _mod()
    reply = (1, '{"message":"Resource not accessible by personal access token"}',
             "gh: forbidden (HTTP 403)")
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": reply}))
    assert c.decide(_entry(), [_acc("org/a", c.GRANTED), got])["outcome"] == c.FLAGGED


def test_the_403_arm_must_not_deny_and_a_denying_mutant_changes_the_tier():
    head = _mod()
    mutant = _mod_with("            return _verdict(repo, UNKNOWN, FORBIDDEN, text)",
                       "            return _verdict(repo, DENIED, FORBIDDEN, text)")
    reply = (1, "<html>403 Forbidden</html>", "gh: forbidden (HTTP 403)")
    got_head = head.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": reply}))
    got_mut = mutant.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": reply}))
    assert got_head["verdict"] == head.UNKNOWN and got_mut["verdict"] == mutant.DENIED
    assert mutant.decide(_entry(), [_acc("org/a", mutant.GRANTED), got_mut])["outcome"] == mutant.TIER_2


def test_a_runner_that_dies_answered_nothing():
    c = _mod()

    def boom(args):
        raise OSError("gh: killed by the harness")
    got = c.check_access("org/b", CONFIRMED, run=boom)
    assert got["verdict"] == c.UNKNOWN and got["reason"] == c.UNCLASSIFIED


def test_a_json_array_where_a_repo_object_belongs_is_unknown():
    c = _mod()
    got = c.check_access("org/b", CONFIRMED, run=_runner({"api repos/org/b": (0, "[1, 2]", "")}))
    assert got["verdict"] == c.UNKNOWN and got["reason"] == c.UNREADABLE


def test_a_flagged_unit_with_no_feature_owner_reports_that_it_reached_nobody():
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, entry=_entry(owner=None))
        run = _runner({"issues/1472": (0, BODY_ISSUE, ""), "api user": OK_USER,
                       "api repos/org/a": (0, _repo_json(), ""),
                       "api repos/org/b": (1, "", "gh: Bad Gateway (HTTP 502)")})
        decision = c.check_at_pick(base, "1472", _config(), run=run)
        assert decision["outcome"] == c.FLAGGED and decision["unaddressed"] == ["*"]


def test_the_cli_reads_back_what_pick_time_decided_and_never_re_checks(capsys):
    """A read-only verb, deliberately: a second way to run the check would be a second answer."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d)
        assert c.main(["cross_repo.py", "check", base, "1472"]) == 0
        assert "no landing decision recorded for 1472" in capsys.readouterr().out
        c.check_at_pick(base, "1472", _config(), run=_runner(BOTH_GREEN))
        assert c.main(["cross_repo.py", "check", base, "1472"]) == 0
        assert '"outcome": "tier-1"' in capsys.readouterr().out
        assert c.main(["cross_repo.py"]) == 2


def test_a_flagged_decision_with_a_denied_repo_raises_only_the_flag():
    """A flagged unit can also carry a denied repo -- one unreachable, one unmeasured. Raising the
    tier-2 note for the denied one would tell its owner the unit "lands contract-first", naming a
    strategy nothing selected. The flag goes to the feature owner and nowhere else; the per-repo
    detail is in the record, where it commits to nothing."""
    c = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, entry=_entry(repos={
            "org/a": {"branch": "f", "owner": "@a-owner", "authorized": True, "goals": []},
            "org/b": {"branch": "f", "owner": "@b-owner", "authorized": True, "goals": []},
            "org/c": {"branch": "f", "owner": "@c-owner", "authorized": True, "goals": []}}))
        run = _runner({"issues/1472": (0, BODY_ISSUE, ""), "api user": OK_USER,
                       "api repos/org/a": (0, _repo_json(), ""),
                       "api repos/org/b": NOT_FOUND,
                       "api repos/org/c": (1, "", "gh: Bad Gateway (HTTP 502)")})
        decision = c.check_at_pick(base, "1472", _config(), run=run)
        assert decision["outcome"] == c.FLAGGED
        assert decision["denied"] == ["org/b"] and decision["unknown"] == ["org/c"]
        raised = _ledger_lines(base)
        assert [e["to"] for e in raised] == ["feature-owner"]
        assert "contract-first" not in raised[0]["why"]

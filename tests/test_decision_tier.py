"""Decision-tier categorizer (sigma-loop/scripts/decision_tier.py): a deterministic detail-text ->
tier heuristic for #818's L0/L1/L2 decision-escalation pyramid. Pins each tier, the upward
conflict-resolution rule, and the default, so a wording change can't silently under-escalate a
strategic call or silently escalate the whole backlog to the wrong level."""
import json
import pathlib
import importlib.util

P = (pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
     / "decision_tier.py")


def _mod():
    spec = importlib.util.spec_from_file_location("decision_tier", P)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_strategic_detail_escalates_to_l0():
    c = _mod().classify
    for d in ("this needs to revert the release", "sign the new vendor contract before shipping",
              "the change touches pricing for existing customers",
              "there's a security incident in prod right now",
              "this action is irreversible once it runs",
              "we're already over budget for this quarter"):
        tier, signal = c(d)
        assert tier == "escalate_l0", d
        assert signal is not None


def test_trivial_detail_is_autonomous():
    c = _mod().classify
    for d in ("just a typo in the error message", "which variable name reads clearer here",
              "reformat the block to match the rest of the file",
              "which of these two equivalent helpers should we keep",
              "which library should we use for the parser",
              "renaming the helper for clarity"):
        tier, signal = c(d)
        assert tier == "autonomous", d
        assert signal is not None


def test_ambiguous_detail_defaults_to_escalate_l1():
    tier, signal = _mod().classify("not sure how the team wants this handled")
    assert tier == "escalate_l1"
    assert signal is None, "a manufactured reason beside a defaulted tier is worse than none"


def test_empty_detail_defaults_to_escalate_l1():
    tier, signal = _mod().classify("")
    assert tier == "escalate_l1"
    assert signal is None
    tier, signal = _mod().classify(None)
    assert tier == "escalate_l1"
    assert signal is None


def test_tricky_detail_escalates_to_l1():
    c = _mod().classify
    for d in ("this is a tricky trade-off between two designs",
              "hit an edge case in the retry logic",
              "not sure which architecture decision is right here",
              "this is genuinely ambiguous and needs a judgment call"):
        tier, signal = c(d)
        assert tier == "escalate_l1", d
        assert signal is not None


def test_conflict_resolves_upward_to_l0():
    """A trivial word next to a strategic one must NOT down-tier: the higher tier wins, and the
    recorded signal must be the one that actually WON (mirrors predict.py's own
    test_conflict_resolves_upward / test_upward_conflict_reports_the_signal_that_WON)."""
    tier, signal = _mod().classify("fix the typo in the budget forecast")
    assert tier == "escalate_l0"
    assert signal == "budget"
    assert "typo" not in signal


def test_l1_conflict_resolves_upward_over_autonomous():
    tier, signal = _mod().classify("not sure which library to use here -- genuinely torn")
    assert tier == "escalate_l1"
    assert signal == "not sure which"


# --- false-positive guards -------------------------------------------------------------------
# Two different collision shapes, matching predict.py's own documented discipline of checking
# BOTH directions rather than just the terms that are supposed to fire.

def test_no_false_trigger_on_meaning_inverting_prefix():
    """"unambiguous" is the OPPOSITE of "ambiguous" and must not fire escalate_l1's `ambiguous`
    signal -- the same meaning-inverting-prefix shape predict.py guards for "uncomplex" (not a
    complexity goal) vs "complex". `\\b` already blocks this by construction (no word boundary
    between "un" and "ambiguous"); this test proves it rather than assuming it."""
    tier, signal = _mod().classify("the requirements are unambiguous and already agreed")
    assert tier == "escalate_l1"   # the safe default -- nothing else in the text matches either
    assert signal is None


def test_no_false_trigger_on_contractor_substring():
    """"contract" must not fire on "contractor"/"subcontractor" -- staffing a contractor is not a
    legal-contract decision. Unlike most terms in `_PATTERNS` (which deliberately leave the
    trailing boundary open so a suffixed form like "budgetary" still counts), `contract` is
    followed by an explicit `\\b` specifically because "contractor" extends it by a real suffix
    ("-or"), not a genuinely on-topic inflection -- the same substring-collision shape
    predict.py's own tests check for "revision"/"provision" against "vision"/"story", adapted to a
    SUFFIX collision instead of a PREFIX one ("subcontractor" is additionally blocked by the
    ordinary leading \\b, since there is no word boundary between "sub" and "contract")."""
    c = _mod().classify
    for d in ("loop in a new contractor for the sprint", "bring on a subcontractor for infra work"):
        tier, signal = c(d)
        assert tier != "escalate_l0", d
    tier, signal = c("review the new vendor contract before signing")
    assert tier == "escalate_l0"
    assert signal == "contract"


def test_default_tier_and_pattern_order_are_the_documented_choice():
    """Pins the two decisions this module's docstring argues for, so a future edit that quietly
    reorders `_PATTERNS` or swaps the default cannot drift without failing here."""
    m = _mod()
    assert m._PATTERNS[0][0] == "escalate_l0"
    assert m._DEFAULT == "escalate_l1"
    assert m._DEFAULT != "autonomous", (
        "the default must never silently hand an unrecognized decision to the loop -- "
        "'trivial questions must never surface' is the aggressive claim, not the safe default")


def test_config_key_ships_in_the_template_with_the_documented_off_default():
    """#1185: `decision_tier` used to be entirely ABSENT from the shipped template -- undiscoverable
    except by reading source or the changelog. It must now ship as a real, documented key, and its
    shipped value must be the safe, off-by-default value `resolve()`'s own gate treats as inert
    (anything other than the literal string "auto"; see `test_resolve_gates_on_config`), so a fresh
    `/sigma-init` behaves identically to before this key existed."""
    tmpl = (pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-init" / "templates"
            / "config.json.tmpl")
    cfg = json.loads(tmpl.read_text())
    assert cfg["decision_tier"] == "off"
    assert "_decision_tier" in cfg, "the opt-in explainer comment key must ship alongside it too"


def test_main_reads_text_and_file(tmp_path):
    m = _mod()
    assert m.main(["decision_tier.py", "fix a typo"]) == 0
    f = tmp_path / "detail.txt"
    f.write_text("there's a security incident")
    assert m.main(["decision_tier.py", str(f)]) == 0
    assert m.main(["decision_tier.py"]) == 2   # usage


def _sdlc(tmp_path, decision_tier):
    base = tmp_path / ".sdlc"
    base.mkdir(exist_ok=True)   # same tmp_path reused across calls in a test
    (base / "config.json").write_text(json.dumps({"decision_tier": decision_tier}))
    return str(base)


def test_resolve_gates_on_config(tmp_path):
    """resolve() returns a tier only under decision_tier:auto -- unset/off means the caller (a
    future #953) stays fully unaffected."""
    m = _mod()
    assert m.resolve("this is irreversible", _sdlc(tmp_path, "auto")) == "escalate_l0"
    assert m.resolve("this is irreversible", _sdlc(tmp_path, "off")) is None
    assert m.resolve("this is irreversible", str(tmp_path / "missing")) is None   # no config -> off


def test_resolve_cli_prints_off_when_disabled(tmp_path, capsys):
    m = _mod()
    m.main(["decision_tier.py", "resolve", "fix a typo", _sdlc(tmp_path, "off")])
    assert capsys.readouterr().out.strip() == "off"
    m.main(["decision_tier.py", "resolve", "there's a security incident", _sdlc(tmp_path, "auto")])
    assert capsys.readouterr().out.strip() == "escalate_l0"


def test_why_cli_prints_tier_and_the_winning_signal(capsys):
    m = _mod()
    assert m.main(["decision_tier.py", "why", "fix the typo in the budget forecast"]) == 0
    assert capsys.readouterr().out.strip() == "tier=escalate_l0 signal=budget"


def test_why_cli_prints_empty_signal_on_default(capsys):
    m = _mod()
    assert m.main(["decision_tier.py", "why", "ordinary decision text"]) == 0
    assert capsys.readouterr().out.strip() == "tier=escalate_l1 signal="


def test_realistic_unscripted_decision_text_resolves_via_the_real_pattern_match(monkeypatch):
    """#1185 evidence #4: every one of the 36 pre-existing tests builds its input by embedding the
    literal regex token as a bare word inside a short carrier sentence ("just a typo in the error
    message") -- proving only that the pattern list contains the strings the pattern list contains,
    not that the classifier does anything with genuinely unscripted prose. `classify()` is a pure
    substring/regex matcher by design (no NLP, no fuzzy match -- see the module docstring), so ANY
    text that resolves to a non-default tier necessarily contains one of `_PATTERNS`' literal
    alternatives somewhere -- that is unavoidable for this architecture, not a gap this test can
    paper over. What IS achievable, and what this test actually proves: a full, multi-clause,
    realistically-worded park detail -- the kind an agent genuinely writes into `loop.py record ...
    parked "<text>"`, not a token-plus-filler sentence engineered to trigger one rule -- still
    resolves correctly, AND the result is proven to depend on the real classify() logic (not just
    happen to already equal the default) by monkeypatching classify() to the module's own `_DEFAULT`
    and confirming this exact detail then resolves to a DIFFERENT tier. A test that passed either
    way would prove nothing about the classifier itself."""
    m = _mod()
    detail = (
        "Spent the last hour going back and forth on this and I still don't have a clean answer -- "
        "once this migration script runs against the production database there is no way to walk it "
        "back, and I don't think shipping an irreversible change like that is a call I should be "
        "making alone this late on a Friday."
    )
    tier, signal = m.classify(detail)
    assert tier == "escalate_l0"
    assert signal == "irreversible"

    # Prove the assertion above genuinely depends on classify()'s own pattern logic, not on the
    # detail happening to already resolve to whatever a stub would return anyway.
    monkeypatch.setattr(m, "classify", lambda text: (m._DEFAULT, None))
    stub_tier, stub_signal = m.classify(detail)
    assert (stub_tier, stub_signal) != (tier, signal)
    assert stub_tier == m._DEFAULT == "escalate_l1"


def test_classify_is_pure_and_stable_across_calls():
    m = _mod()
    for _ in range(3):
        assert m.classify("there's a security incident") == ("escalate_l0", "security incident")

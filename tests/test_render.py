"""Controls for `skills/agrim-loop/scripts/render.py` (#2111).

The strongest tests in this file are the ones that RECONSTRUCT `docs/output-contract.md`'s own
worked examples. §7 is titled "Worked example (the reference output)", so a renderer that claims to
implement the contract is checkable against the contract's own text rather than against a shape the
test author invented: `test_block_b_reproduces_the_contracts_own_reference_output` pulls the sample
out of the markdown and asserts byte-identity with what the module builds from facts. Every axis
table, every reference block and the two arrow-less descriptions are read OUT OF the file, never
copied into this test and never cited by line number -- that repo's own design corrections record a
line reference going stale as a defect worth naming, and a copied sample can drift from the
document while both stay green.

The other half is the refusal surface: every refusal is exercised through the DOCUMENTED gesture
(`python3 render.py <block> --json ...`, the invocation the module's own usage string gives), not
through a stronger in-process call, and each asserts the same three things -- exit 2, the typed
code on stderr, and NOTHING on stdout.
"""
import ast
import importlib.util
import json
import pathlib
import re
import socket
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "agrim-loop" / "scripts"
CONTRACT = ROOT / "docs" / "output-contract.md"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


render = _mod("render")


def _cli(block, facts):
    """THE DOCUMENTED GESTURE, verbatim from the module's own `USAGE`: shell out with the facts as
    JSON. Every refusal control below runs THIS, not an in-process call, because a guard proven
    only through a stronger invocation than the docs give is decoration with a passing test."""
    return subprocess.run(
        [sys.executable, str(S / "render.py"), block, "--json", json.dumps(facts)],
        capture_output=True, text=True,
    )


def _refused(block, facts, code):
    """Exit 2, the typed code on stderr, and stdout UNTOUCHED. The third is the one that matters
    most: a caller piping stdout into a console must receive nothing rather than half a line."""
    proc = _cli(block, facts)
    assert proc.returncode == 2, proc
    assert f"REFUSED [{code}]" in proc.stderr, proc.stderr
    assert proc.stdout == "", f"a refusal wrote to stdout: {proc.stdout!r}"
    return proc


# --------------------------------------------------------------------------- contract fixtures

def _contract_text():
    return CONTRACT.read_text(encoding="utf-8")


def _fence_starting(prefix):
    """The one ``` fence in the contract whose body starts with `prefix`. Selected BY CONTENT, not
    by index: the contract has ten fences and #2100 already inserted one in the middle of them, so
    an index would silently start comparing against a different sample."""
    text = _contract_text()
    found = [b for b in re.findall(r"^```\n(.*?)^```", text, flags=re.S | re.M)
             if b.startswith(prefix)]
    assert len(found) == 1, f"{len(found)} fences start with {prefix!r}"
    return found[0].rstrip("\n")


def _axis_table(heading_prefix):
    """The rows of the markdown table that follows `heading_prefix`, as lists of cells. Parsed out
    of the document so the module's vocabulary is pinned to the contract's, in both directions."""
    text = _contract_text()
    start = text.index(heading_prefix)
    rows = []
    for line in text[start:].splitlines()[1:]:
        if line.startswith("### ") or line.startswith("---"):
            break
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or set(cells[0]) <= set("- ") or cells[0] in ("Marker", "Token"):
            continue
        rows.append([c.strip("`") for c in cells])
    return rows


SLOT = {
    "marker": "running",
    "ref": "#2111",
    "url": "https://github.com/acme/widget/issues/2111",
    "phase": "implement",
    "title": "Construct every status line",
    "description": "writing render.py and its controls → PR",
    "model_tier": "opus",
}
STATUS = {"headline": "Status — 1 in flight", "slots": [dict(SLOT)], "tail": "Waiting on CI."}
EVENT = {
    "ref": "#1626", "url": "https://x/1626", "title": None,
    "from_phase": "research", "to_phase": "plan",
    "artifact": "dossier at `.sdlc/research/1626.md`",
    "fact": "cost $0.31 (48,203 tokens, claude-sonnet-5)",
    "next_action": "Writing the plan next",
}
DECISION = {
    "ref": "#2111", "url": "https://x/2111", "phase": "implement",
    "blocked": "Block A refuses raw issue titles",
    "tradeoff": "a truncated title says something the caller did not",
    "options": ["Refuse the long title", "Truncate to eight words"],
    "recommendation": "Refuse the long title",
}


def _slot(**overrides):
    slot = dict(SLOT)
    slot.update(overrides)
    return {"headline": "Status", "slots": [slot], "tail": "Waiting."}


# ------------------------------------------------- 1. the two axes, pinned to the contract itself

def test_marker_set_matches_the_contract_table():
    """§2 Axis 1 is a CLOSED set and this module is the only thing that writes one, so the two must
    not be able to drift. Both directions: no marker in the module that the contract does not
    define, and none in the contract the module cannot render -- including the exact `⏸️` U+23F8
    U+FE0F spelling, which is invisible to a reader and would otherwise be a silent mismatch."""
    rows = _axis_table("### Axis 1 —")
    assert len(rows) == 8, rows
    assert [(r[0], r[1]) for r in rows] == list(render.MARKERS)


def test_phase_token_set_matches_the_contract_table():
    """§2 Axis 2, the same pin. The contract's table is `| token | phase |`, so the token is the
    first cell; the module's kinds are the ledger's, checked separately below."""
    rows = _axis_table("### Axis 2 —")
    assert [r[0] for r in rows] == [token for _, token in render.PHASE_TOKENS]


def test_phase_kinds_match_the_ledger_vocabulary():
    """The kind half of the same axis is `ledger.PHASE_KINDS` verbatim -- the vocabulary
    `loop.py emit ... phase --phase` has always enforced. Duplicated in `render.py` rather than
    imported (it loads no sibling script at all), so this is what keeps the copy honest."""
    ledger = _mod("ledger")
    assert tuple(kind for kind, _ in render.PHASE_TOKENS) == tuple(ledger.PHASE_KINDS)


def test_phase_tokens_match_phase_reports_own_table():
    """`phase_report.py` carries the same table and #2112 routes it through this module. If the two
    ever disagree, one of them is emitting a token the other cannot parse."""
    assert tuple(render.PHASE_TOKENS) == tuple(_mod("phase_report").PHASE_TOKENS)


def test_both_producers_spell_the_tier_labels_identically():
    """#2112 shells out rather than imports (design D-6), so its producer cannot READ these values
    at runtime and copies them -- pinned here the way `PHASE_TOKENS` above and `MIRROR_REL`/
    `TITLE_MAX` in `test_phase_report.py` already are.

    THIS WAS A PREFIX PIN AND IS NOW AN EQUALITY PIN (#2113). #2112 shortened `phase_report.py`'s
    copy for its free-text banner and left this module's structured Block A field on the long
    form, so the system said one fact two ways -- `(predicted, not observed; agent-set, not
    code-derived)` on a slot line, `(agent-set)` on the banner directly above it. One renderer with
    two caveat forms is the inconsistency this module exists to prevent, so the short form won
    here too. With both sides now spelling them the same, a PREFIX assertion would be
    `"predicted".split(",")[0] == "predicted"` -- true no matter what either side did next, which
    is a check that has stopped checking. Equality is the property that actually holds now, and it
    is the stronger one: a reword on EITHER side goes red instead of only a reword that changes the
    first token.

    Asserted as two DIFFERENT tokens as well, and as non-substrings of each other: collapsing them
    into one word would satisfy a naive membership check while erasing D-2, which is the whole
    defect. `check_tier_labels` still covers Block A's structured field at runtime; the start line
    is free text, which is why this pin exists at all."""
    pr = _mod("phase_report")
    assert render.TIER_LABEL_PREDICTED == pr.TIER_LABEL_PREDICTED
    assert render.TIER_LABEL_AGENT_SOURCED == pr.TIER_LABEL_AGENT_SOURCED
    assert pr.TIER_LABEL_PREDICTED != pr.TIER_LABEL_AGENT_SOURCED
    assert pr.TIER_LABEL_PREDICTED not in pr.TIER_LABEL_AGENT_SOURCED
    assert pr.TIER_LABEL_AGENT_SOURCED not in pr.TIER_LABEL_PREDICTED
    assert pr.PHASE_LAST == render.PHASE_LAST


# ------------------------------------------- 2. reconstruct the contract's own reference output

def test_block_b_reproduces_the_contracts_own_reference_output():
    """§7's second and third fenced blocks are Block B samples. Built from facts, this module
    reproduces the third one BYTE FOR BYTE (the second is hard-wrapped in the markdown source and
    so cannot be compared as one line). This is the test that says "implements the contract" in a
    form that can go red."""
    sample = " ".join(_fence_starting("**[#1626](url)**").split())
    with pytest.raises(render.Refusal):     # the contract's literal "url" is not an http(s) one
        render.render_event(dict(EVENT, url="url"))
    built = render.render_event(dict(EVENT))
    assert built == sample.replace("(url)", "(https://x/1626)")


#: Every Block A fence in the contract, as the facts that build it. §1 now says "§3's Block A
#: sample and §7's Block A reference output are its output" -- three fences -- and a claim in
#: prose is not evidence, so all three are reconstructed here, not just §7's first (#2114, review).
#: The literal `url` in a description (`PR [#2927](url)`) is prose, not a URL field, and passes
#: through unchanged; the `https://x/<n>` URLs are the only substitution made.
_BLOCK_A_FENCES = [
    ("Loop restarted at 2 slots:", {
        "headline": "Loop restarted at 2 slots",
        "slots": [
            {"marker": "🔵", "ref": "#2628", "url": "https://x/2628", "phase": "implement",
             "title": "Extract coherence validator", "model_tier": "opus",
             "description": "resuming intact partial work; verifying byte-identity → guards → PR"},
            {"marker": "🔵", "ref": "#2915", "url": "https://x/2915", "phase": "implement",
             "title": "Sync stale catalog-size docstrings", "model_tier": None,
             "description": "correcting 2 docstrings against measured counts → PR"},
        ],
        "tail": "Waiting on notifications — no polling."}),
    ("Status — <N> merged in <window>:", {
        "headline": "Status — <N> merged in <window>",
        "slots": [
            {"marker": "🔵", "ref": "#2628", "url": "https://x/2628", "phase": "implement",
             "title": "Extract coherence validator", "model_tier": "opus",
             "description": "moving `_validate_geography_character_coherence` verbatim → verify "
                            "byte-identity → PR"},
            {"marker": "🟣", "ref": "#2915", "url": "https://x/2915", "phase": "review",
             "title": "Sync stale catalog-size docstrings", "model_tier": "sonnet",
             "description": "author-blind review running on PR [#2927](url) → merge gate"},
            {"marker": "⏳", "ref": "#2632", "url": None, "phase": "research",
             "title": "Decompose CharacterSubBuilder", "model_tier": None,
             "description": "seam analysis queued; blocked behind 1c (same file)"},
        ],
        "tail": "<one-line tail: what you are waiting on>"}),
    ("Status — 22 merged in 24h:", {
        "headline": "Status — 22 merged in 24h",
        "slots": [
            {"marker": "✅", "ref": "#2915", "url": "https://x/2915", "phase": "retro",
             "title": "Sync stale catalog-size docstrings", "model_tier": "sonnet",
             "description": "merged and recorded; no follow-up debt"},
            {"marker": "🟢", "ref": "#2628", "url": "https://x/2628", "phase": "review",
             "title": "Complete WorldSubBuilder decomposition", "model_tier": "opus",
             "description": "approved; merge handler waiting on CI → unblocks slice 1d"},
            {"marker": "⏳", "ref": "#2632", "url": None, "phase": "research",
             "title": "Decompose CharacterSubBuilder", "model_tier": None,
             "description": "seam analysis first, then slice 2a → dispatch after 1d lands"},
        ],
        "tail": "Waiting on the merge notification."}),
]


@pytest.mark.parametrize("prefix,facts", _BLOCK_A_FENCES, ids=[p for p, _ in _BLOCK_A_FENCES])
def test_block_a_reproduces_the_contracts_own_reference_output_byte_for_byte(prefix, facts):
    """Every Block A fence the contract shows -- §3's sample and both of §7's -- is built by this
    module BYTE FOR BYTE, the labelled predicted tier included. Until #2114 the contract's Block A
    defined five fields while this module emitted six (design D-1 added the tier by ruling), so
    this test stripped the sixth field before comparing, and only §7's first fence was compared at
    all. #2114 put the field into the contract's own definition and every sample and closed that
    exception: the contract is the specification, its samples are what the code emits, and the two
    can no longer differ by a field -- in any of the three fences."""
    sample = _fence_starting(prefix)
    built = render.render_status(facts)
    assert built.replace("https://x/2628", "url").replace("https://x/2915", "url") == sample, \
        f"the Block A fence starting {prefix!r} is not byte-identical to what render.py builds"


def test_the_contract_quotes_the_usage_and_the_refusal_form_the_module_actually_has():
    """§1's "The renderer and its commands" quotes the module's usage lines "verbatim from the
    module" and states the refusal form. Both are pinned here (#2114, review): the usage fence is
    the module docstring's own block, indented four spaces there; and the refusal form the contract
    states is what the DOCUMENTED gesture actually writes to stderr, prefix and path included -- not
    merely the `REFUSED [<code>]` substring `_refused` settles for."""
    usage = _fence_starting("render.py status   < facts.json")
    assert "\n".join("    " + line for line in usage.splitlines()) in render.__doc__, \
        "§1's usage fence is not the module docstring's usage block"
    stated = "render.py: REFUSED [<code>] at <path>: <detail>"
    assert stated in _contract_text()
    proc = _refused("status", _slot(marker="bogus"), "bad-marker")
    assert proc.stderr.startswith("render.py: REFUSED [bad-marker] at slots[0].marker: "), proc.stderr


def test_the_block_a_definition_line_names_the_tier_and_both_of_its_labels():
    """The doc/code half of D-1, on the DEFINITION rather than the samples: §3's `**Title line:**`
    line must name the sixth field with both of its labels and its honest null. Parsed out of the
    document and pinned to this module's own constants, so a reword on either side goes red. The
    definition drifted from the fence once already -- five fields defined, six emitted -- which is
    what #2114 closed."""
    lines = [line for line in _contract_text().splitlines() if line.startswith("**Title line:**")]
    assert len(lines) == 1, lines
    definition = lines[0]
    assert "model" in definition
    assert render.TIER_LABEL_PREDICTED in definition
    assert render.TIER_LABEL_AGENT_SOURCED in definition
    assert render.TIER_UNRECORDED in definition


def test_block_c_reproduces_the_contracts_own_shape():
    """§3's Block C fence is a template, not a rendered sample, so it is compared by substituting
    the placeholders it names. A shape change here (a dropped ` · `, a moved `↳`, a missing
    trailing full stop after the recommendation) turns this red."""
    template = _fence_starting("🔴 **<goal ref>**")
    built = render.render_decision(dict(DECISION, url=None))
    filled = (template
              .replace("**<goal ref>**", "**#2111**")
              .replace("P<n> <NAME>", "P5 IMPLEMENT")
              .replace("<what is blocked>", DECISION["blocked"])
              .replace("<the tradeoff in one line>", DECISION["tradeoff"])
              .replace("<Option A>", DECISION["options"][0])
              .replace("<Option B>", DECISION["options"][1])
              .replace("<one>", DECISION["recommendation"]))
    assert built == filled


def test_a_description_from_the_contract_s_own_reference_output_is_accepted():
    """§3 asks a description to end `→ <what unblocks next>` "whenever there is a next thing to
    name", and names the slots that have none -- `✅` finished, `⏸️` deferred, `⏳` naming its
    blocker instead. Two of the contract's OWN example descriptions are of that kind. Before
    #2124 the rule read as an absolute the reference output broke; it now reads as a requirement
    with two stated exceptions, and either way the arrow is not enforceable here (which exception
    applies is a judgement about the slot's meaning). This is the executed form of that: the two
    arrow-less descriptions are lifted out of the contract file and pushed through the module. The
    count is asserted, so softening the rule into "arrows optional" -- which would let the samples
    multiply unnoticed -- goes red rather than passing quietly."""
    arrowless = [line.strip()[2:].strip()
                 for line in _contract_text().splitlines()
                 if line.strip().startswith("↳ ") and "→" not in line and "<" not in line]
    assert len(arrowless) == 2, arrowless
    for description in arrowless:
        built = render.render_status(_slot(description=description))
        assert built.splitlines()[3] == f"  ↳ {description}"


# ------------------------------------------------------- 3. the tier's two labels, kept distinct

def test_the_predicted_tier_carries_both_labels_and_they_are_not_the_same_claim():
    """The defect this module exists to prevent, in its most direct form. "Predicted, not observed"
    (D-1) and "agent-set, not code-derived" (D-2) are DIFFERENT claims: the first says nobody has
    measured this yet, the second says no code produced it. Either one alone launders an
    agent-supplied guess into a measurement.

    Asserted three ways so that dropping a label, or quietly making one label a rewording of the
    other, cannot pass: both appear, they are distinct strings, and neither contains the other."""
    field = render.tier_field("opus", "slot.model_tier")
    assert render.TIER_LABEL_PREDICTED in field
    assert render.TIER_LABEL_AGENT_SOURCED in field
    assert render.TIER_LABEL_PREDICTED != render.TIER_LABEL_AGENT_SOURCED
    assert render.TIER_LABEL_PREDICTED not in render.TIER_LABEL_AGENT_SOURCED
    assert render.TIER_LABEL_AGENT_SOURCED not in render.TIER_LABEL_PREDICTED
    assert field == ("model opus (%s; %s)" % (render.TIER_LABEL_PREDICTED,
                                              render.TIER_LABEL_AGENT_SOURCED))


def test_a_collapsed_tier_field_is_refused_at_runtime_not_merely_in_a_test():
    """`check_tier_labels` is the branch `tier_field` runs on every render, and this calls THAT --
    not a test-only seam. Each label removed in turn, so dropping either one is caught, and the
    refusal names the one that went missing."""
    for present, missing in ((render.TIER_LABEL_PREDICTED, render.TIER_LABEL_AGENT_SOURCED),
                             (render.TIER_LABEL_AGENT_SOURCED, render.TIER_LABEL_PREDICTED)):
        with pytest.raises(render.Refusal) as caught:
            render.check_tier_labels("model opus (%s)" % present, "slot.model_tier")
        assert caught.value.code == "tier-labels-collapsed"
        assert missing in caught.value.detail
    assert render.check_tier_labels(render.tier_field("opus", "x"), "x")   # the passing branch


def test_the_agent_sourced_label_rests_on_model_choice_actually_being_an_agent_kind():
    """The FACT under label 2, checked rather than asserted in prose. `model_choice` sits in
    `actionlog.AGENT_KINDS` -- an agent writes it -- while `gate`, `verify_run` and `recorded` are
    code-written `INTERNAL_KINDS` and cannot be forged. If `model_choice` ever moved to the
    internal side, "agent-set, not code-derived" would become a false label, so the label and the
    fact behind it fail together rather than drifting apart."""
    actionlog = _mod("actionlog")
    assert "model_choice" in actionlog.AGENT_KINDS
    assert "model_choice" not in actionlog.INTERNAL_KINDS   # direct, so a rename fails loudly
    assert {"gate", "verify_run", "recorded"} <= set(actionlog.INTERNAL_KINDS)


def test_a_null_tier_states_its_absence_and_never_guesses_a_default():
    """`model_tier: null` is a legal thing to say and renders an explicit absence -- the same
    manner as `phase_report.py`'s `cost: unavailable on this host (<reason>)`. What it must never
    do is fall back to a default tier, which would be a fabricated prediction."""
    built = render.render_status(_slot(model_tier=None))
    assert built.splitlines()[2].endswith(render.SEPARATOR + render.TIER_UNRECORDED)
    for _, state in render.MARKERS:
        assert state != render.TIER_UNRECORDED
    assert not any(t in built for t in ("haiku", "sonnet", "opus", "fable"))


def test_a_missing_tier_key_is_refused_because_forgetting_is_not_saying_i_do_not_know():
    """The distinction the closed fact set turns on: `null` is an answer, an ABSENT key is not. If
    a missing key silently rendered as unrecorded, a caller that forgot the field and a caller that
    honestly has no tier would be indistinguishable on the line."""
    slot = {k: v for k, v in SLOT.items() if k != "model_tier"}
    _refused("status", {"headline": "S", "slots": [slot], "tail": "T"}, "missing-field")


# ------------------------------------------------------- 4. the closed fact set, and what it declines

#: The keys the two human rulings decline, written out LITERALLY here rather than read back from
#: `render.DECLINED_SLOT_FIELDS`. That distinction is the whole guard, and the first version of
#: this file got it wrong: parametrising over the module's own dict meant that deleting `cost` from
#: it merely deleted a test case, so the control that renamed `"cost"` came back GREEN against
#: exactly the bug this test exists to catch. What is being pinned is a DECISION (design D-8 and
#: D-1), so the decision has to be stated on this side of the boundary.
DECLINED_BY_RULING = ("cost", "cost_usd", "spend", "tokens", "observed_model", "model")


def test_the_declined_set_still_contains_every_key_the_rulings_declined():
    """The pin itself: no key may quietly leave `DECLINED_SLOT_FIELDS`, because the moment one
    does, that key becomes renderable on a live slot line and the ruling is silently reversed."""
    assert set(DECLINED_BY_RULING) <= set(render.DECLINED_SLOT_FIELDS), (
        set(DECLINED_BY_RULING) - set(render.DECLINED_SLOT_FIELDS))


@pytest.mark.parametrize("key", DECLINED_BY_RULING)
def test_every_declined_slot_field_is_refused_by_name_with_its_ruling(key):
    """`cost`, `observed_model` and their siblings are declined BY RULING, not by oversight, so
    each gets a refusal that says which ruling. `declined-field` rather than `unknown-field` is the
    point: "unknown field 'cost'" would read as this module having forgotten it."""
    proc = _refused("status", _slot(**{key: "whatever"}), "declined-field")
    assert key in proc.stderr


def test_block_a_carries_no_cost_field_anywhere_in_what_it_renders():
    """Design D-8, resolved by the human: cost lives at the phase boundary in Block B, where it is
    measured, and never on a live slot line. Checked on the rendered OUTPUT, not just on the input
    schema, so a cost could not arrive through some other field's formatting either."""
    assert "cost" not in render.render_status(STATUS).lower()
    assert "cost" not in " ".join(render.SLOT_FIELDS)


def test_an_unknown_slot_key_is_refused_rather_than_ignored():
    """A closed set that silently ignores what it does not recognise is not closed."""
    _refused("status", _slot(priority="P1"), "unknown-field")


def test_block_b_has_no_marker_key_so_a_verdict_free_boundary_cannot_stamp_one():
    """#2100's blocking finding, kept structurally. `phase_report.py end` receives no verdict, and
    a `✅` there would have been wrong on 88% of plan-reviews. There is no way to put a marker on a
    Block B through this module, because there is no key for one."""
    assert "marker" not in render.EVENT_FIELDS
    _refused("event", dict(EVENT, marker="done"), "unknown-field")
    assert not (set(render.render_event(dict(EVENT))) & render._MARKER_CHARS)


def test_block_c_writes_its_own_marker_and_will_not_take_one():
    """§3 fixes Block C's marker at `🔴`. It is not a caller fact, so passing one is refused."""
    assert render.DECISION_MARKER == "🔴"
    assert "marker" not in render.DECISION_FIELDS
    _refused("decision", dict(DECISION, marker="done"), "unknown-field")


# ------------------------------------------------------------------- 5. both axes, refused when wrong

def test_an_invented_marker_is_refused():
    """§2: "Never invent a marker."""
    _refused("status", _slot(marker="🚀"), "bad-marker")
    _refused("status", _slot(marker="in-flight"), "bad-marker")


def test_a_marker_may_be_given_as_a_glyph_or_as_its_state_name():
    """A state name exists so no caller has to put an emoji into a shell argument on a host whose
    console encoding is unknown. The GLYPH is what renders, always."""
    for glyph, state in render.MARKERS:
        by_glyph = render.render_status(_slot(marker=glyph))
        by_state = render.render_status(_slot(marker=state))
        assert by_glyph == by_state
        assert by_glyph.splitlines()[2].startswith(f"* {glyph} ")


def test_an_unknown_phase_is_refused_and_every_known_one_renders_its_token():
    """§6: "No slot line missing its phase token." The set is closed at both ends."""
    _refused("status", _slot(phase="plan-review"), "bad-phase")     # a hyphen, not the ledger kind
    _refused("status", _slot(phase="P8 SHIP"), "bad-phase")
    for kind, token in render.PHASE_TOKENS:
        assert render.SEPARATOR + token + render.SEPARATOR in render.render_status(_slot(phase=kind))


def test_only_block_bs_to_phase_takes_a_non_phase_and_only_the_two_enumerated_ones():
    """`to_phase` accepts exactly two values that are not phases, and they are not interchangeable.

    `"unknown"` (#2111) is for a caller that genuinely cannot name the successor. `"last"` (#2112)
    is `P7 RETRO`, which has none -- inventing a `P8` or reaching for `unknown` would each say
    something untrue about a list that has simply ended.

    `from_phase` takes NEITHER, and that is #2112's review finding rather than an oversight: the
    draft that let it take `"unknown"` so `phase_report.py start` could be a Block B put
    `previous phase unknown (no verdict at this boundary)` at the head of every start of every
    phase, forever. A phase START is not a transition and has no "from"; the fix was to stop
    forcing it into this shape, not to word the filler better. Block A refuses both outright --
    its §2 axis is never optional."""
    ended = render.render_event(dict(EVENT, to_phase="unknown"))
    assert render.UNKNOWN_NEXT_PHASE in ended
    last = render.render_event(dict(EVENT, to_phase="last"))
    assert last.endswith("→ last phase — dossier at `.sdlc/research/1626.md`, cost $0.31 "
                         "(48,203 tokens, claude-sonnet-5). Writing the plan next.")
    _refused("event", dict(EVENT, from_phase="unknown"), "bad-phase")
    _refused("event", dict(EVENT, from_phase="last"), "bad-phase")
    _refused("status", _slot(phase="unknown"), "bad-phase")
    _refused("status", _slot(phase="last"), "bad-phase")
    _refused("event", dict(EVENT, to_phase="dunno"), "bad-phase")


def test_a_null_next_action_ends_the_block_after_its_fact_rather_than_inventing_one():
    """#2112's review finding, in the smallest form it can be checked.

    §3 asks a Block B to end with what you are doing next. A machine-emitted one at a phase
    boundary has nothing to say there, and the drafts that filled the slot anyway were narration --
    one of them ("The standing SDLC puts P3 PLAN after this one") restating what the same line's
    `→` had already said. `null` writes NOTHING: no trailing clause, no placeholder, and no second
    full stop. A caller with a real next action still passes it and still gets §3's sentence."""
    built = render.render_event(dict(EVENT, next_action=None))
    assert built.endswith("cost $0.31 (48,203 tokens, claude-sonnet-5).")
    assert render.render_event(dict(EVENT)).endswith(". Writing the plan next.")
    facts = dict(EVENT)
    del facts["next_action"]
    _refused("event", facts, "missing-field")     # forgetting is still refused; saying so is not


# ---------------------------------------------------------------- 6. the goal ref, bold and linked

def test_a_url_renders_a_linked_bold_ref_and_a_null_url_renders_a_bold_one():
    """§3: "never bare `#123`. Bold-without-link only when no URL exists yet." Both halves."""
    linked = render.render_status(_slot(url="https://x/2111")).splitlines()[2]
    assert "**[#2111](https://x/2111)**" in linked
    plain = render.render_status(_slot(url=None)).splitlines()[2]
    assert "**#2111**" in plain
    assert "#2111 " not in plain.replace("**#2111**", "")   # never emitted bare


def test_a_malformed_ref_or_url_is_refused_rather_than_rendered_broken():
    """A markdown link that does not close is a broken line, not a degraded one."""
    _refused("status", _slot(ref="#21[11]"), "bad-ref")
    _refused("status", _slot(ref="2111 (see also 2112)"), "bad-ref")
    _refused("status", _slot(url="github.com/x/1"), "bad-url")
    _refused("status", _slot(url="https://x/a(b)"), "bad-url")


def test_a_local_goal_stem_is_a_legal_ref_because_there_is_no_issue_to_link():
    """`phase_report.goal_ref` produces exactly two shapes -- `#<number>` and a local goal file's
    stem -- and this module accepts both, so routing it here (#2112) cannot refuse local mode."""
    built = render.render_status(_slot(ref="0004-vision-first-onramp", url=None))
    assert "**0004-vision-first-onramp**" in built


def test_a_block_b_title_rides_inside_the_bold_ref_and_a_null_one_renders_nothing_at_all():
    """#2112's one widening of the closed fact set, and the property that made it safe to make.

    A bare `#1983` names the goal only to someone who already knows what 1983 is -- the exact
    legibility gap #2100 was filed for, and one §3's own machine-half sample shows closed. So
    Block B takes a `title`, inside the bold beside the ref (Block B's shape has no ` · ` in it;
    that is a Block A rule), linked or not.

    `null` renders NOTHING -- not an empty position, not a placeholder -- which is what makes the
    widening free: the contract's §7 reference output is still exactly what a title-less Block B
    produces, asserted byte for byte by
    `test_block_b_reproduces_the_contracts_own_reference_output` above."""
    titled = render.render_event(dict(EVENT, title="Resolve reviewer independence per host"))
    assert titled.startswith(
        "**[#1626](https://x/1626) Resolve reviewer independence per host** P2 RESEARCH")
    unlinked = render.render_event(
        dict(EVENT, url=None, title="Resolve reviewer independence per host"))
    assert unlinked.startswith("**#1626 Resolve reviewer independence per host** P2 RESEARCH")
    assert render.render_event(dict(EVENT, title=None)).startswith(
        "**[#1626](https://x/1626)** P2 RESEARCH")

    # the same closed-set discipline as every other key: forgetting is refused, saying so is not
    facts = dict(EVENT)
    del facts["title"]
    _refused("event", facts, "missing-field")
    _refused("event", dict(EVENT, title="x" * (render.TITLE_MAX_CHARS + 1)), "title-too-long")
    _refused("event", dict(EVENT, title="✅ shipped"), "marker-glyph-in-field")


def test_a_block_b_title_may_carry_the_separator_and_a_block_a_one_may_not():
    """The one place the two titles are checked differently, and it is not a slip.

    ` · ` is refused in a field with a NEIGHBOUR on its line, because there it forges a boundary
    that was never passed in -- which is exactly Block A's title. Block B has no ` · `-delimited
    field list at all (§3 shapes it with a space, a `→` and a `—`, and its own `fact` routinely
    contains ` · `), and its title sits inside the bold, so there is nothing there to forge.

    This is not hypothetical: two of this repo's 328 open issues on 2026-09-03 are titled
    "L6 · Branch protection on the integration branch (human-run)" and its sibling. Under the
    strict check every one of their fourteen phase boundaries would fall back to the unconstructed
    banner -- loud and lossless, but not the total composition #2124 set the cap to buy."""
    real = "L6 · Branch protection on the integration branch (human-run)"
    assert real in render.render_event(dict(EVENT, title=real))
    _refused("status", _slot(title=real), "separator-in-field")


# ------------------------------------------------------------------- 7. the caps and the ceiling

def test_a_title_over_the_cap_is_refused_and_never_truncated():
    """§3: "Title ≤ 88 characters". Refused, because truncating changes what the title says -- and
    the refusal says the count and the cap so the caller can act. The boundary is exercised on both
    sides, so a cap moved by one in either direction goes red here."""
    proc = _refused("status", _slot(title="x" * (render.TITLE_MAX_CHARS + 1)), "title-too-long")
    assert "89 characters" in proc.stderr and "cap is 88" in proc.stderr
    assert render.render_status(_slot(title="x" * render.TITLE_MAX_CHARS))


def test_the_title_cap_is_the_one_phase_report_already_elides_at():
    """#2124, and the reason the cap is 88 rather than a number somebody liked. `phase_report.py`
    FILLS this field (from the board mirror, i.e. the raw issue title) and elides it at
    `TITLE_MAX`; this module CONSUMES it and refuses over `TITLE_MAX_CHARS`. While those two
    numbers differed, one half of one pipeline refused what the other half had just declared fit to
    print. Pinned in both directions so they cannot drift apart again -- which is the whole defect
    #2124 was filed for, and the assertion that would have caught it."""
    assert render.TITLE_MAX_CHARS == _mod("phase_report").TITLE_MAX


def test_every_title_phase_report_can_produce_is_one_this_module_accepts():
    """The pin above stated as the property it buys: `clean_title` is TOTAL into `check_title`.
    Run over titles far longer than the cap, through the DOCUMENTED CLI gesture -- if the producer
    ever elides to something the consumer refuses, this goes red rather than a live goal doing so.
    Measured on the real backlog when #2124 landed: 316 of 316 open issue titles pass this way,
    where the previous 8-word cap passed 90."""
    pr = _mod("phase_report")
    raws = [
        "Route phase_report.py's start/end output through the renderer as Block B",
        "docs/output-contract.md contradicts itself in three places — settle it before #2112 "
        "routes real output through render.py",
        "render.py: one module constructs every status line, and refuses a malformed one",
        "  ragged\n\n whitespace   and a newline   in a title  ",
        "x" * 400,
        "短" * 400,
    ]
    for raw in raws:
        cleaned = pr.clean_title(raw)
        assert len(cleaned) <= render.TITLE_MAX_CHARS, (len(cleaned), cleaned)
        for block, facts in (("status", _slot(title=cleaned)),
                             ("event", dict(EVENT, title=cleaned))):
            proc = _cli(block, facts)           # BOTH blocks -- #2112 fills the Block B one too
            assert proc.returncode == 0, (block, proc.stderr)
            assert cleaned in proc.stdout


def test_a_description_over_the_cap_is_refused():
    _refused("status", _slot(description=" ".join(["word"] * 21)), "description-too-long")
    assert render.render_status(_slot(description=" ".join(["word"] * 20)))


def test_both_caps_are_the_numbers_the_contract_itself_states():
    """The doc/code half of the same drift #2124 fixed: §3 is the specification, so the numbers in
    it and the numbers in this module are pinned to each other, parsed OUT OF the markdown and
    never copied here. Edit one without the other and this goes red. The UNITS are pinned too --
    reading "88 words" or "20 characters" out of §3 fails, because a cap that names the wrong unit
    is the exact mistake that made the title cap wrong in the first place."""
    text = _contract_text()
    assert re.search(r"^- Title ≤ %d characters," % render.TITLE_MAX_CHARS, text, re.M), \
        "§3's title cap does not state '%d characters'" % render.TITLE_MAX_CHARS
    assert re.search(r"^- Description ≤ %d words," % render.DESCRIPTION_MAX_WORDS, text, re.M), \
        "§3's description cap does not state '%d words'" % render.DESCRIPTION_MAX_WORDS


def test_more_than_six_slots_is_refused_because_more_means_collapse_not_scroll():
    """§3's ceiling, and its floor: a Block A with no slots states nothing."""
    assert render.MAX_SLOTS == 6
    assert render.render_status({"headline": "S", "slots": [dict(SLOT)] * 6, "tail": "T"})
    _refused("status", {"headline": "S", "slots": [dict(SLOT)] * 7, "tail": "T"}, "bad-slot-count")
    _refused("status", {"headline": "S", "slots": [], "tail": "T"}, "bad-slot-count")


def test_a_block_b_over_three_sentences_is_refused():
    """§3 Block B: "≤ 3 sentences." Counted on the line this module actually emits, and `$0.31`,
    `claude-sonnet-5` and `.sdlc/design/2030.md` are not sentence ends -- which is what lets the
    contract's own reference output pass."""
    assert render.render_event(dict(EVENT))                      # 2 sentences, the §7 sample
    _refused("event", dict(EVENT, fact="One. Two. Three. Four"), "too-many-sentences")


def test_block_c_needs_two_or_three_options_and_a_recommendation_it_offered():
    """§3: "Present ≤ 3 options. Always carry a recommendation." One option is not a decision, and
    a decision block that recommends something it did not present is malformed -- the one thing
    about a decision's CONTENT a machine can actually check."""
    _refused("decision", dict(DECISION, options=["Only one"], recommendation="Only one"),
             "bad-option-count")
    _refused("decision", dict(DECISION, options=["a", "b", "c", "d"], recommendation="a"),
             "bad-option-count")
    _refused("decision", dict(DECISION, recommendation="Something else entirely"),
             "recommendation-not-an-option")


# ------------------------------------------------------- 8. a field cannot forge structure

def test_the_field_separator_inside_a_neighbouring_field_is_refused():
    """§3: "Separator between fields is ` · ` -- nothing else." A title carrying it would make the
    line parse as more fields than were passed in."""
    _refused("status", _slot(title="Extract validator · P7 RETRO · done"), "separator-in-field")
    _refused("status", _slot(model_tier="opus · $4.98"), "separator-in-field")
    _refused("decision", dict(DECISION, blocked="a · b"), "separator-in-field")


def test_the_separator_is_allowed_where_a_field_has_no_neighbour_to_forge():
    """The rule is precise, not blanket: a `↳` description, a headline, a tail and every Block B
    field are alone on their line, so there is no field boundary there to forge."""
    assert render.render_status(_slot(description="a · b → c"))
    assert render.render_event(dict(EVENT, fact="8m11s · $4.98 · claude-opus-5"))


def test_a_marker_glyph_inside_a_field_is_refused():
    """The liveness axis is written by this module. A `✅` smuggled through a title is the #2100
    defect class -- a success claim nothing verified -- wearing a different hat. The bare `⏸` is
    caught as readily as `⏸️`, whose variation selector is invisible in an editor."""
    _refused("status", _slot(title="Extract validator ✅"), "marker-glyph-in-field")
    _refused("status", _slot(description="done 🔴 blocked"), "marker-glyph-in-field")
    _refused("event", dict(EVENT, fact="✅ merged"), "marker-glyph-in-field")
    _refused("status", _slot(title="paused ⏸"), "marker-glyph-in-field")
    _refused("status", _slot(title="paused ⏸️"), "marker-glyph-in-field")


def test_a_newline_inside_a_field_is_refused_because_it_would_inject_a_whole_slot():
    """Two lines per slot is the shape. A title carrying a newline could append an entire extra
    slot line -- with a marker and a phase token -- that no caller ever passed."""
    _refused("status", _slot(title="Real title\n* ✅ **#1** · P7 RETRO · Fake"),
             "field-not-one-line")
    _refused("status", {"headline": "S", "slots": [dict(SLOT)], "tail": "one\ntwo"},
             "field-not-one-line")
    _refused("event", dict(EVENT, next_action="one\ntwo"), "field-not-one-line")


def test_an_empty_or_non_string_field_is_refused():
    _refused("status", _slot(title="   "), "field-empty")
    _refused("status", _slot(title=5), "field-not-a-string")
    _refused("status", _slot(marker=None), "field-empty")
    _refused("status", {"headline": "S", "slots": "not a list", "tail": "T"}, "slots-not-a-list")
    _refused("decision", dict(DECISION, options="a / b"), "options-not-a-list")


# --------------------------------------------------------------------- 9. the CLI and the refusal

def test_the_documented_invocation_renders_on_stdout_with_exit_zero():
    """The gesture the module's own `USAGE` gives, run as given."""
    for block, facts in (("status", STATUS), ("event", EVENT), ("decision", DECISION)):
        proc = _cli(block, facts)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip()
        assert proc.stderr == ""


def test_facts_arrive_on_stdin_too_which_is_the_shape_a_shell_out_caller_uses():
    """`render.py status < facts.json` -- the form that carries arbitrary text without quoting it
    through a shell, and the one #2112 and #2113 will actually use."""
    proc = subprocess.run([sys.executable, str(S / "render.py"), "status"],
                          input=json.dumps(STATUS), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == render.render_status(STATUS) + "\n"


def test_an_unknown_block_and_invalid_json_both_refuse_with_an_empty_stdout():
    proc = subprocess.run([sys.executable, str(S / "render.py"), "banner", "--json", "{}"],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "REFUSED [unknown-block]" in proc.stderr
    proc = subprocess.run([sys.executable, str(S / "render.py"), "status", "--json", "{nope"],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "REFUSED [not-an-object]" in proc.stderr


def test_every_refusal_code_this_module_raises_is_declared():
    """`REFUSAL_CODES` is the closed set, pinned by an AST walk over every `Refusal(...)` in the
    file -- so a new refusal cannot be added without naming it, and a code cannot be renamed in one
    place only. An AST walk, not a grep: this module's docstring NAMES several codes in prose."""
    tree = ast.parse((S / "render.py").read_text(encoding="utf-8"))
    raised = {node.args[0].value
              for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "Refusal" and node.args
              and isinstance(node.args[0], ast.Constant)}
    assert raised, "no Refusal(...) calls found -- the walk is looking at the wrong thing"
    assert raised <= render.REFUSAL_CODES, raised - render.REFUSAL_CODES
    assert render.REFUSAL_CODES <= raised, render.REFUSAL_CODES - raised


# ------------------------------------------------------- 10. the architecture the slice promised

def test_render_imports_only_the_standard_library():
    """North Star Rule 1 (stdlib only under `skills/`), asserted on this module directly, the same
    way `test_managed_settings_gate.py` does for its own. Rule 3 (the core never imports the private
    side) follows: the allowlist below holds only standard-library names."""
    tree = ast.parse((S / "render.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names <= {"json", "re", "sys"}, names


def test_render_loads_no_sibling_script_and_spawns_nothing():
    """"Zero dependencies, invoked by shell-out, never imported" is the arrangement that satisfies
    both import bans (`log.py` must not import `actionlog.py`; the private side must not import
    `skills/`). This module goes one further and loads no sibling either -- not even by
    `phase_report.py`'s `spec_from_file_location` idiom -- so it runs from any directory in any
    repo. Static: no `importlib`, no `subprocess`, no `open`."""
    tree = ast.parse((S / "render.py").read_text(encoding="utf-8"))
    called = {node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "_load" not in called and "open" not in called and "__import__" not in called
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "spec_from_file_location" not in attrs


def test_rendering_opens_no_socket_and_spawns_no_process(monkeypatch):
    """THE EXECUTED half of the same guard. GraphQL is exhausted on this account and #2100
    deliberately kept the phase-boundary path network-free; #2112 routes that path through here, so
    a lookup added to any of these three renderers turns this red immediately."""
    def _boom(*a, **k):
        raise AssertionError("render.py spawned a process or opened a socket")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _boom, raising=False)
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom, raising=False)

    assert render.render_status(STATUS)
    assert render.render_event(dict(EVENT))
    assert render.render_decision(dict(DECISION))
    with pytest.raises(render.Refusal):
        render.render_status(_slot(marker="🚀"))


def test_render_writes_to_stdout_and_nowhere_else(tmp_path, monkeypatch):
    """Design D-5, settled: this module's chosen write surface is STDOUT, alone. The ceiling that
    comes with it is real and stated in the module docstring -- a constructed line is unforgeable
    in SHAPE, and is still paraphrasable anywhere the user reads a model's reply rather than raw
    tool output. What this control can prove is the narrow half: rendering creates no file."""
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    proc = _cli("status", STATUS)
    assert proc.returncode == 0
    assert set(tmp_path.rglob("*")) == before

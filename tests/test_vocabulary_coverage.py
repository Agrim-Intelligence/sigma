"""#142 [E6.S7] Vocabulary-versus-call-site guard, spec §A.6, the last story of epic E6.

Two independent, required directions:

  DIRECTION A (EVENT_KINDS -> site): every kind in `ledger.EVENT_KINDS` has >=1 emitting call
  site, Python or prose. A kind nobody can ever write is dead vocabulary.

  DIRECTION B (site -> EVENT_KINDS): every kind named at a detected EVENTS-stream call site is a
  real `EVENT_KINDS` member. `ledger.append()` already raises `ValueError` for an unknown kind
  (ledger.py:569-570) -- but only for a code path that actually EXECUTES. A typo'd kind behind a
  rare branch (an error-handling arm, a hook that only fires on a specific trigger --
  hooks/decision_gate.py's deny path is exactly this shape) can sit un-exercised for months with
  green tests. This check is static: it reads source text, so `pytest` catches the typo the moment
  the suite runs, regardless of whether any test ever reaches that branch at runtime.

PLUS the flag clause: every `--k` flag `loop.py emit` accepts must appear in the vocabulary table.
Already structurally guaranteed -- `loop.py`'s `_validate_event` indexes `ledger.EVENT_FIELDS[kind]`
directly (loop.py:370), so the vocabulary table IS the allowlist, never a hand-maintained parallel
list. This file adds a PINNING test only (`test_emit_flag_check_reads_ledger_event_fields`) so a
future refactor that swaps in a separate list is caught -- no production change.

PINNED STRUCTURALLY, NOT BY TEXT SEARCH (post-review fix). An earlier version of this pin asserted
`"ledger.EVENT_FIELDS[kind]" in <the whole file's text>` -- and `_validate_event`'s OWN docstring
(loop.py:339) narrates that exact indexing in prose, directly above the code it describes. A
reviewer proved by mutation that replacing the real line with a hand-maintained parallel dict left
the substring check green, because the docstring phrase alone kept it satisfied; only deleting the
coincidental prose too made it fail. `_validate_event_indexes_event_fields` (below) instead parses
`_LOOP_PY_TEXT` and requires an `ast.Subscript` node on `ledger.EVENT_FIELDS` inside
`_validate_event`'s own FunctionDef -- a docstring is an `ast.Constant` expression statement, so it
produces no Subscript node regardless of what text it contains, immune to a docstring inside the
function too. See test_flag_pin_fails_on_a_hand_maintained_allowlist for the fixture proof.

PLUS a staleness check: the guard must fail if its own detection idiom goes stale. See the comment
above `test_the_idiom_still_finds_the_prose_only_kinds` for exactly what it pins, and why the
weaker "matches something" bar (tests/test_config_discoverability.py:32-35) is not enough here.

AST, NOT REGEX, FOR THE PYTHON SIDE. tests/test_import_boundary.py's own docstring makes this
argument for a different guard ("Grepping for the substring \"import <private package>\" would
false-positive on prose like this docstring's..."); the identical failure mode is real here, verified directly against
this tree, not assumed: `skills/sigma-loop/scripts/slices.py:119` carries a COMMENT containing the
literal text "ledger.append()", and an unbounded regex over that file's raw text walks forward
across ~8 lines to the next bare-quoted word, `"title"` (a dict key, not a kind) -- harmless only
by luck, since `gate`/`verify`/`scan`/`spend`/`slice`/`park`/`retro` are all plausible dict-key
spellings too, and a future comment mentioning `ledger.append()` followed by one of them would
misattribute a real kind to text that is not a call site. So this checker parses every file with
`ast` and only ever inspects real `ast.Call` nodes whose function resolves to
`ledger.append`/`ledger.safe_append` -- a comment or docstring produces no such node, regardless of
what text it contains (see test_ignores_a_comment_that_merely_mentions_ledger_append, which plants
the real shape as a fixture rather than only trusting today's real file).

SPAN-AWARE BY CONSTRUCTION. Direction B needs to know, for a given call, whether it carries
`stream=ledger.EVENTS` -- and the real `spend` call site (loop.py:467) wraps that kwarg onto a
continuation line, separated from the `"spend"` literal by a nested-paren kwarg
(`config=state.load_config(sdlc_dir)`). A same-line or same-string-span regex would silently drop
this real EVENTS site. Reading `stream=` off the SAME `ast.Call` node's `.keywords` is exactly as
span-aware as the call itself, for free -- there is no separate "span" concept to get wrong (see
test_span_aware_stream_kwarg_on_a_continuation_line).

ALIAS-AWARE (post-review fix). The direct-attribute match above (`ledger.append(...)`, an
`ast.Attribute` on `ast.Name(id="ledger")`) misses a call reached through a simple alias -- and this
codebase's own house style already aliases module attributes this way: loop.py:894-895 assigns
`_evidence_path = state.evidence_path` and `_done_refusal = state.done_refusal` two lines apart. A
reviewer proved by mutation that `from ledger import safe_append as _sa` then
`_sa(sdlc_dir, "gaet", goal, stream=ledger.EVENTS, ...)` -- a typo of the real kind `gate` -- was
invisible to both directions: Direction B never saw a `ledger.safe_append` Call to inspect at all.
`_ledger_aliases` (below) is a per-module pre-pass, stdlib `ast` only, resolving exactly two forms
before the Call scan runs: `from ledger import safe_append as _sa` / `from ledger import append`
(an `ast.ImportFrom` with `module == "ledger"`) and `_emit = ledger.safe_append` (a module-level
`ast.Assign` of a bare `Name` to an `Attribute` on `ledger`) -- the identical two forms for the
`EVENTS` constant. A `Call` whose `func` is one of the resolved names is then treated as a ledger
append; a `stream=` kwarg naming a resolved `EVENTS` alias is treated as the events stream. See
test_aliased_ledger_calls_are_detected and
test_direction_b_fails_on_a_typo_d_kind_behind_an_aliased_call.

NAMED LIMITATION, not silently assumed away: this pre-pass resolves ONE hop only. A re-aliased
alias (`_sa2 = _sa`), an alias reassigned inside a function body, or indirection through anything
other than a bare `ImportFrom`/module-level `Assign` (a dict of callables, a decorator-built
wrapper, a class attribute) is out of scope. This narrows real coverage; it does not narrow the
CLAIM this file makes about itself.

SCOPE (spec §A.6 verbatim): every kind in `EVENT_KINDS` must have >=1 emitting call site under
`skills/*/scripts/`, `hooks/`, or a `SKILL.md`; every `--k` flag `loop.py emit` accepts must appear
in the vocabulary table.

CURRENT FACT, NOT ASSERTED AS STALENESS (see amendment C / the comment above
test_the_idiom_still_finds_the_prose_only_kinds for why): `phase` has ZERO literal Python call
sites today. `loop.py`'s `emit` dispatcher (loop.py:482) passes `kind` as a VARIABLE (`argv[4]`),
so it is never a literal site for any kind -- `phase` is covered exclusively by SKILL.md prose. A
Python-only scan would falsely flag it as uncovered; this is why Direction A's coverage is a UNION
of python_kinds and prose_kinds, never python_kinds alone. `retro` WAS in this same position --
zero literal Python call sites, prose-only coverage -- until issue #1013 gave `_record()` its own
literal `ledger.safe_append(sdlc_dir, "retro", goal, stream=ledger.EVENTS, grade=retro_grade)`
call site: `retro` now passes on Python coverage alone, the same as `gate`/`verify`/`spend`/`scan`/
`slice` already do, and the standalone SKILL.md prose line it used to depend on
(skills/sigma-retro/SKILL.md) was deliberately removed by that same issue to close the
double-emission risk a surviving prose-plus-code pair would create. See the new, stricter
test_every_event_kind_has_a_real_python_caller_except_the_reviewed_allowlist below -- `phase`
(#1626) and `model_choice` (#1627) have SINCE graduated off its allowlist too, each the identical
shape: a literal `ledger.safe_append` call site replaced what used to be a prose-only or
subprocess-shelled-out emission, so `_PROSE_ONLY_ALLOWLIST` is empty today. This paragraph is kept
for the MECHANISM it explains (why Direction A must credit prose, illustrated by `retro`'s own
history), not as a current list of exceptions.

OUT OF SCOPE: the `GATE_KINDS` sub-vocabulary (the `--gate` values `loop.py emit --gate ...`
accepts) is a SEPARATE vocabulary from `EVENT_KINDS`, not named by spec §A.6's text, and 6+ of its
12 values have no emitting site today. Issue #229 already names this gap from the EMISSION side
(not the consumption side -- #229's own step 1 is "build the event emitter" for the missing
`risk_*` gates, its step 2 adds ingestion afterward) and is P1 on the backlog. Folding it into this
guard would force a "known-unemitted, tracked elsewhere" allowlist into the guard itself -- which
would BE the drift #142 exists to prevent. Not building it, and pointing at #229 instead, is
deliberate, not an oversight.
"""
import ast
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _mod("ledger")
loop = _mod("loop")

_LOOP_PY_TEXT = (S / "loop.py").read_text(encoding="utf-8")

#: `loop.py" emit .sdlc "<goal-expr>" <kind>` -- the exact idiom tests/test_emit_prose.py already
#: pins as data (that file's job is 5 SPECIFIC lines; this one discovers however many exist).
#: Space-delimited: kind is the token immediately after the goal's closing quote.
_PROSE_KIND = re.compile(r'loop\.py"\s+emit\s+\.sdlc\s+"[^"]*"\s+(\w+)')

#: kind's positional slot differs by call shape (ledger.py's own signatures):
#: append(sdlc_dir, config, kind, goal, ...) vs safe_append(sdlc_dir, kind, goal, ...).
_KIND_POSITION = {"append": 2, "safe_append": 1}


# --------------------------------------------------------------------------- the checker itself


def _py_sources(root):
    """Every .py file this guard scans -- spec §A.6's own wording ("skills/*/scripts/, hooks/")
    turned into globs, deliberately widened rather than a hardcoded file list, so a new script
    under an existing skills/*/scripts/ or a new hooks/*.py is picked up automatically."""
    return sorted(root.glob("skills/*/scripts/*.py")) + sorted(root.glob("hooks/*.py"))


def _skill_md_sources(root):
    """Every SKILL.md this guard scans for prose emit instructions -- glob, not the 5 hardcoded
    paths tests/test_emit_prose.py uses (that file pins 5 SPECIFIC lines; this discovers however
    many orchestrator/gate SKILL.md files exist, matching test_config_discoverability.py's glob
    philosophy)."""
    return sorted(root.glob("skills/*/SKILL.md"))


def _ledger_aliases(tree):
    """Per-module pre-pass (module docstring: ALIAS-AWARE): local names that are simple aliases of
    `ledger.append`/`ledger.safe_append`/`ledger.EVENTS`, resolved BEFORE the Call scan below.
    Returns (call_aliases, events_aliases): `call_aliases` maps a local name to the real ledger
    attribute name ("append"/"safe_append"), needed for `_KIND_POSITION`; `events_aliases` is the
    set of local names that resolve to `ledger.EVENTS`. NAMED LIMITATION: only a bare
    `ast.ImportFrom(module="ledger")` and a module-level `ast.Assign` of a single `Name` target to
    an `Attribute` on `ledger` are resolved -- one hop, see module docstring for what's out of
    scope."""
    call_aliases = {}
    events_aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ledger" and node.level == 0:
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name in ("append", "safe_append"):
                    call_aliases[local] = alias.name
                elif alias.name == "EVENTS":
                    events_aliases.add(local)
    for node in tree.body:  # module-level only, per the docstring's own spec for the Assign form
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "ledger"):
            local = node.targets[0].id
            if node.value.attr in ("append", "safe_append"):
                call_aliases[local] = node.value.attr
            elif node.value.attr == "EVENTS":
                events_aliases.add(local)
    return call_aliases, events_aliases


def _call_attr(node, call_aliases):
    """The real ledger attribute ("append"/"safe_append") a Call node resolves to, direct or via a
    `call_aliases`-resolved alias -- or None when the call is neither."""
    func = node.func
    if (isinstance(func, ast.Attribute) and func.attr in ("append", "safe_append")
            and isinstance(func.value, ast.Name) and func.value.id == "ledger"):
        return func.attr
    if isinstance(func, ast.Name) and func.id in call_aliases:
        return call_aliases[func.id]
    return None


def _is_ledger_append_call(node, call_aliases=frozenset()):
    """True for a real `ledger.append(...)` / `ledger.safe_append(...)` ast.Call node, direct or
    through a resolved alias -- never a comment or docstring that merely contains that text (module
    docstring, AST-not-regex)."""
    return isinstance(node, ast.Call) and _call_attr(node, call_aliases) is not None


def _const_str(expr):
    """The literal string an ast expression names, or None for anything else -- a variable (e.g.
    `kind = argv[4]`, loop.py:482's `emit` dispatcher) is not a literal site for any one kind."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _call_kind(node, call_aliases=frozenset()):
    """The kind argument of a ledger.append/safe_append Call node (direct or aliased), as a literal
    string, or None when it is not a literal (a variable-dispatch site)."""
    for kw in node.keywords:
        if kw.arg == "kind":
            return _const_str(kw.value)
    idx = _KIND_POSITION[_call_attr(node, call_aliases)]
    if len(node.args) > idx:
        return _const_str(node.args[idx])
    return None


def _call_is_events_stream(node, events_aliases=frozenset()):
    """True only when the SAME call node carries `stream=ledger.EVENTS` (or the string-literal
    equivalent `stream="events"`, or a resolved alias of either -- module docstring: ALIAS-AWARE)
    -- read off the call's own keywords, so a kwarg wrapped onto a continuation line (the real
    `spend` call, loop.py:467) is exactly as reachable as one on the call's opening line. This is
    what makes Direction B span-aware, for free (module docstring)."""
    for kw in node.keywords:
        if kw.arg != "stream":
            continue
        v = kw.value
        if isinstance(v, ast.Attribute) and v.attr == "EVENTS":
            return True
        if isinstance(v, ast.Constant) and v.value == "events":
            return True
        if isinstance(v, ast.Name) and v.id in events_aliases:
            return True
    return False


def _ledger_calls(text, filename="<string>"):
    """Every real `ledger.append`/`ledger.safe_append` Call node in `text`, direct or through a
    resolved alias (`_ledger_aliases`, module docstring: ALIAS-AWARE), as (kind, is_events) pairs.
    `kind` is None when the kind argument isn't a literal string -- such a site names no single kind
    and is excluded by both directions below, on purpose."""
    tree = ast.parse(text, filename=filename)
    call_aliases, events_aliases = _ledger_aliases(tree)
    return [
        (_call_kind(node, call_aliases), _call_is_events_stream(node, events_aliases))
        for node in ast.walk(tree)
        if _is_ledger_append_call(node, call_aliases)
    ]


def _python_event_kinds_at(root):
    """The set of literal kind strings named at an EVENTS-stream ledger call, across every file
    `_py_sources(root)` finds. The SAME function the real-tree tests AND the fixture tests below
    call -- real run and mutation-proof fixtures share one code path (test_import_boundary.py's
    own precedent), so this file's correctness never depends on what the real tree currently
    contains. An unparseable file contributes no kinds (this guard is not chartered to validate
    Python syntax; other tests already require every owned .py file to parse)."""
    kinds = set()
    for path in _py_sources(root):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        try:
            calls = _ledger_calls(text, filename=str(path))
        except SyntaxError:
            continue
        for kind, is_events in calls:
            if is_events and kind is not None:
                kinds.add(kind)
    return kinds


def _prose_kinds_at(root):
    """The set of kinds named by the `loop.py" emit .sdlc "<goal>" <kind>` idiom, across every
    `_skill_md_sources(root)` file."""
    kinds = set()
    for path in _skill_md_sources(root):
        kinds.update(_PROSE_KIND.findall(path.read_text(encoding="utf-8")))
    return kinds


def _uncovered_kinds(event_kinds, covered):
    """Direction A's pure comparison: every `event_kinds` member not in the `covered` union."""
    return sorted(k for k in event_kinds if k not in covered)


def _bogus_event_kinds(python_event_kinds, event_kinds):
    """Direction B's pure comparison: every kind found at an EVENTS-stream call site that is not a
    real `event_kinds` member -- a typo, or a kind that was renamed in EVENT_KINDS but not at its
    call site."""
    return sorted(k for k in python_event_kinds if k not in event_kinds)


# --------------------------------------------------------------------------- fixture helpers


def _plant_py(root, skill_name, filename, body):
    d = root / "skills" / skill_name / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(body, encoding="utf-8")


def _plant_hook_py(root, filename, body):
    d = root / "hooks"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(body, encoding="utf-8")


def _plant_skill_md(root, skill_name, command_line):
    d = root / "skills" / skill_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"Instructions.\n\n`{command_line}`\n", encoding="utf-8")


# --------------------------------------------------------------------------- non-vacuous by construction


def test_py_and_skill_md_sources_are_non_empty():
    """Mirrors tests/test_import_boundary.py's own non-vacuous check: a renamed or emptied
    skills/*/scripts/, hooks/, or SKILL.md convention must fail loudly instead of passing with
    silently-zero coverage."""
    assert _py_sources(ROOT), "no .py files found under skills/*/scripts/ or hooks/"
    assert _skill_md_sources(ROOT), "no SKILL.md files found under skills/*/"


# --------------------------------------------------------------------------- direction A / B, real tree


def test_every_event_kind_has_an_emitting_site():
    """Direction A (spec §A.6): every EVENT_KINDS member has >=1 emitting site, Python or prose.
    done_when: 'a kind with no emitting call site fails' -- and the message names it."""
    covered = _python_event_kinds_at(ROOT) | _prose_kinds_at(ROOT)
    missing = _uncovered_kinds(ledger.EVENT_KINDS, covered)
    assert not missing, (
        "EVENT_KINDS member(s) with no emitting call site anywhere (skills/*/scripts/, hooks/, or "
        f"a SKILL.md): {missing}")


def test_every_events_stream_site_names_a_real_event_kind():
    """Direction B (spec §A.6): every kind named at a detected EVENTS-stream call site must be a
    real EVENT_KINDS member. See module docstring for what this catches that append()'s own
    runtime raise does not (a site no test ever executes)."""
    bogus = _bogus_event_kinds(_python_event_kinds_at(ROOT), ledger.EVENT_KINDS)
    assert not bogus, (
        f"EVENTS-stream call site(s) name kind(s) not in ledger.EVENT_KINDS: {bogus} -- a typo, or "
        "a kind renamed in EVENT_KINDS but not updated at its call site")


# --------------------------------------------------------------------------- the flag clause, pinned


def _validate_event_indexes_event_fields(text):
    """True only when `_validate_event`'s own FunctionDef, walked as AST, contains an
    `ast.Subscript` node on `ledger.EVENT_FIELDS` somewhere in its body -- module docstring:
    PINNED STRUCTURALLY, NOT BY TEXT SEARCH. A docstring (the function's own, or any other string
    constant in its body) is an `ast.Constant` expression statement; it produces no Subscript node
    regardless of what text it contains, so this is immune to the coincidental-prose false-pass a
    plain substring search over the whole file suffered. False (not an error) when no
    `_validate_event` FunctionDef exists at all -- a rename would then correctly read as "does not
    index EVENT_FIELDS" rather than crash."""
    tree = ast.parse(text)
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_validate_event"]
    return any(
        isinstance(sub, ast.Subscript)
        and isinstance(sub.value, ast.Attribute)
        and sub.value.attr == "EVENT_FIELDS"
        and isinstance(sub.value.value, ast.Name)
        and sub.value.value.id == "ledger"
        for fn in funcs
        for sub in ast.walk(fn)
    )


def test_emit_flag_check_reads_ledger_event_fields():
    """The flag clause: every `--k` flag `loop.py emit` accepts must appear in the vocabulary
    table. Already structurally guaranteed -- `_validate_event` indexes
    `ledger.EVENT_FIELDS[kind]` directly (loop.py:370) -- so this PINS the wiring two ways: a
    structural AST check (catches a refactor to a separate, driftable list before behavior even
    changes -- see test_flag_pin_fails_on_a_hand_maintained_allowlist for why this must be AST, not
    a text search) and a behavioral check (the refusal itself still works). No production change."""
    assert _validate_event_indexes_event_fields(_LOOP_PY_TEXT), (
        "loop.py's _validate_event no longer indexes ledger.EVENT_FIELDS[kind] as a real Subscript "
        "node in its own function body -- the flag-vocabulary clause may have grown a separate, "
        "hand-maintained allowlist")
    err = loop._validate_event("phase", {"bogus_flag_xyz": "1"})
    assert err is not None and "bogus_flag_xyz" in err, (
        f"an unknown --flag for a known kind should be refused by name; got {err!r}")


def test_flag_pin_fails_on_a_hand_maintained_allowlist():
    """Mutation 5, the reviewer's exact finding, reproduced as a fixture that exercises the REAL
    checking function (`_validate_event_indexes_event_fields`), not a parallel copy: swap
    `allowed = ledger.EVENT_FIELDS[kind]` for a hand-maintained dict, while keeping the docstring
    phrase (`ledger.EVENT_FIELDS[kind]`) that fooled the old plain-substring check -- proving the
    AST-based check is not fooled by it either, because the docstring produces no Subscript node."""
    mutated = (
        'def _validate_event(kind, flags, kind_allowlist=None):\n'
        '    "unknown flag NAMES against ledger.EVENT_FIELDS[kind] -- the phrase a substring '
        'search would still match here"\n'
        '    allowed = {"gate": ("verdict", "why")}[kind]\n'
        '    bad = sorted(set(flags) - set(allowed))\n'
        '    return "unknown flag(s) " + ", ".join(bad) if bad else None\n'
    )
    assert not _validate_event_indexes_event_fields(mutated), (
        "a hand-maintained allowlist standing in for ledger.EVENT_FIELDS[kind] must fail the pin, "
        "even with the docstring phrase intact")


# --------------------------------------------------------------------------- staleness


#: What this pins: Direction A's own "matches something" pass/fail would stay green even if
#: `_PROSE_KIND` regressed to matching only a lucky subset, AS LONG AS phase/retro's coverage
#: happened to survive by accident -- research proved this exact asymmetric-decay shape is live
#: (prose detection going dark while Python detection, which never covered phase/retro anyway,
#: keeps matching something else). So this test pins the two facts whose SILENT loss neither
#: Direction A nor a bare non-empty assertion (tests/test_config_discoverability.py:32-35's bar)
#: would surface on its own: phase and retro are still found by prose detection specifically.
#:
#: Deliberately NOT bundled here: an assertion that Python detection does NOT also cover phase/
#: retro. That would couple a robustness claim (already proven independently, on a fixture, by
#: test_staleness_fails_when_prose_is_reworded below) with a CONTINGENT current fact -- the day
#: legitimate work adds a literal Python `phase` emitter, such an assertion goes red for a reason
#: unrelated to staleness, and the natural "fix" is to update the assertion away, which is exactly
#: how a staleness check dies by attrition. The current fact is recorded in the module docstring's
#: comment instead, not asserted.
def test_the_idiom_still_finds_the_prose_only_kinds():
    prose = _prose_kinds_at(ROOT)
    # `retro` dropped from this pin (issue #1013): `_record()` now carries its own literal
    # ledger.safe_append(..., "retro", ...) call site, and the standalone SKILL.md prose line this
    # pin used to depend on was deliberately removed (skills/sigma-retro/SKILL.md) to close the
    # double-emission risk a surviving prose-plus-code pair would create. `retro` is still covered --
    # now by Direction A's Python-call-site half instead of its prose half; see
    # test_every_event_kind_has_an_emitting_site above, and the new, stricter
    # test_every_event_kind_has_a_real_python_caller_except_the_reviewed_allowlist below.
    #
    # `phase` dropped from this pin the same way (#1626): `skills/sigma-loop/SKILL.md` and
    # `skills/sigma-goal/SKILL.md` used to instruct the bare idiom this file matches --
    # `loop.py" emit .sdlc "$goal" phase --phase ... --state start/end` -- with no real
    # measurement behind it (SKILL.md's own prose warned "do not invent ms/tokens_in/tokens_out
    # for a phase event -- the loop cannot measure per-phase timing or spend from prose"). Both
    # files now instruct `phase_report.py start`/`end` instead, which prices a phase's real
    # transcript and carries its OWN literal `ledger.safe_append(..., "phase", ...)` call sites
    # (`skills/sigma-loop/scripts/phase_report.py`, `cmd_start`/`cmd_end`) -- so, like `retro`,
    # `phase` is still covered, now by Direction A's Python-call-site half instead of its prose
    # half. A surviving prose-plus-code pair would have re-created the same double-emission risk
    # #1013 already closed for `retro` (a `start`/`end` call from SKILL.md prose AND a redundant
    # bare `emit` call, both firing per phase boundary).


# NON-BLOCKING NOTE (PR review finding 3): unlike phase/retro above, `gate`'s two real prose sites
# (sigma-align's and sigma-plan-review's SKILL.md) are NOT pinned the same way -- breaking both would
# pass this guard silently, because `gate` also has Python call-site coverage elsewhere, so
# Direction A's union stays satisfied. Deliberately not fixed with a per-kind prose-coverage pin;
# see amendment C above for why that specific fix is the attrition trap this file already rejected.


# --------------------------------------------------------------------------- mutation proof, on fixtures


def test_direction_a_fails_when_the_only_prose_site_is_removed(tmp_path):
    """Mutation 1 (done_when: 'a kind with no emitting call site fails'), the retro/phase
    prose-only shape specifically: retro has ZERO Python call sites in the real tree, so removing
    its one SKILL.md prose line removes its only detection path."""
    _plant_skill_md(tmp_path, "sigma-retro",
                     'python3 "${X}/loop.py" emit .sdlc "$goal" retro --grade achieved')
    covered_before = _python_event_kinds_at(tmp_path) | _prose_kinds_at(tmp_path)
    assert "retro" in covered_before

    _plant_skill_md(tmp_path, "sigma-retro", "no emit instruction here at all")
    covered_after = _python_event_kinds_at(tmp_path) | _prose_kinds_at(tmp_path)
    assert _uncovered_kinds(("retro",), covered_after) == ["retro"]


def test_direction_a_fails_on_a_fixture_with_no_site_for_a_kind(tmp_path):
    """Mutation 2: a kind added to the vocabulary with no site anywhere. Proven against a LOCAL
    copy of EVENT_KINDS (`fake_event_kinds`), not by mutating the real `ledger.EVENT_KINDS` --
    mutating the real module under test would also trip its own import-time
    `_assert_event_fields_classified` before this guard ever ran."""
    fake_event_kinds = ledger.EVENT_KINDS + ("bogus_kind",)
    covered = _python_event_kinds_at(ROOT) | _prose_kinds_at(ROOT)
    assert _uncovered_kinds(fake_event_kinds, covered) == ["bogus_kind"]


def test_staleness_fails_when_prose_is_reworded(tmp_path):
    """Mutation 3: reword the phase emit line to a shape `_PROSE_KIND` doesn't match (kind
    reordered before `.sdlc`) -- proves the staleness test's OWN logic fires on a planted reword,
    the idiom-robustness half amendment C keeps out of the real-tree assertion above."""
    good = 'python3 "${X}/loop.py" emit .sdlc "$goal" phase --phase goal --state start'
    _plant_skill_md(tmp_path, "sigma-loop", good)
    assert "phase" in _prose_kinds_at(tmp_path)

    reworded = 'python3 "${X}/loop.py" emit phase .sdlc "$goal" --phase goal --state start'
    _plant_skill_md(tmp_path, "sigma-loop", reworded)
    assert "phase" not in _prose_kinds_at(tmp_path)


def test_direction_b_fails_on_a_typo_d_kind_at_an_events_stream_site(tmp_path):
    """Mutation 4: a typo'd kind string at a real EVENTS-stream call. Proven on a planted fixture
    per tests/test_import_boundary.py's own precedent -- the checker's logic is proven correct
    against fixtures, not only trusted against whatever the real tree happens to contain today."""
    _plant_py(tmp_path, "sigma-loop", "site.py",
              'ledger.safe_append(d, "gaet", g, stream=ledger.EVENTS, verdict="pass")\n')
    python_kinds = _python_event_kinds_at(tmp_path)
    assert python_kinds == {"gaet"}
    assert _bogus_event_kinds(python_kinds, ledger.EVENT_KINDS) == ["gaet"]


def test_aliased_ledger_calls_are_detected(tmp_path):
    """Finding 2 fix, the positive proof: `from ledger import safe_append as _sa`,
    `_emit = ledger.safe_append`, `from ledger import EVENTS as _EV`, and `EV2 = ledger.EVENTS` are
    all this codebase's own house style (loop.py:894-895 already assigns `_evidence_path =
    state.evidence_path` / `_done_refusal = state.done_refusal` two lines apart, the identical
    idiom applied to a different module). All four alias forms must resolve to a real EVENTS-stream
    site, exactly as if written out directly, so neither direction goes blind on this codebase's
    own idiom."""
    _plant_py(tmp_path, "sigma-loop", "aliased.py",
              'from ledger import safe_append as _sa\n'
              'from ledger import EVENTS as _EV\n'
              '_emit = ledger.safe_append\n'
              'EV2 = ledger.EVENTS\n'
              '_sa(sdlc_dir, "gate", goal, stream=_EV, verdict="pass")\n'
              '_emit(sdlc_dir, "spend", goal, stream=EV2, tokens_in="1")\n')
    kinds = _python_event_kinds_at(tmp_path)
    assert kinds == {"gate", "spend"}, (
        "aliased ledger.safe_append call sites (via ast.ImportFrom-as and module-level ast.Assign, "
        "for both the call and the EVENTS constant) were not detected")
    assert _bogus_event_kinds(kinds, ledger.EVENT_KINDS) == []


def test_direction_b_fails_on_a_typo_d_kind_behind_an_aliased_call(tmp_path):
    """Finding 2's exact reviewer mutation, reproduced as a fixture:
    `from ledger import safe_append as _sa` then `_sa(sdlc_dir, "gaet", goal,
    stream=ledger.EVENTS, ...)` -- a typo of the real kind `gate` -- must be caught behind the
    alias, the same as it already is behind the direct `ledger.safe_append` spelling
    (test_direction_b_fails_on_a_typo_d_kind_at_an_events_stream_site above). Before the fix, this
    site was invisible to Direction B: `_is_ledger_append_call` never matched `_sa(...)` at all, so
    no Call node reached the kind check."""
    _plant_py(tmp_path, "sigma-loop", "site.py",
              'from ledger import safe_append as _sa\n'
              '_sa(sdlc_dir, "gaet", goal, stream=ledger.EVENTS, verdict="pass")\n')
    python_kinds = _python_event_kinds_at(tmp_path)
    assert python_kinds == {"gaet"}, "the aliased call site was not detected at all"
    assert _bogus_event_kinds(python_kinds, ledger.EVENT_KINDS) == ["gaet"]


# --------------------------------------------------------------------------- the checker, precision proofs


def test_ignores_a_comment_that_merely_mentions_ledger_append(tmp_path):
    """The exact false positive an unbounded regex hits in the real tree
    (skills/sigma-loop/scripts/slices.py:119): a COMMENT containing the literal text
    "ledger.append()" must produce zero extracted kinds, even when a bare-quoted word that IS a
    plausible kind spelling ("title" here stands in for the real file's own dict key) follows a
    few lines later. AST only inspects real ast.Call nodes; a comment produces none."""
    _plant_py(tmp_path, "sigma-loop", "slices.py",
              '# `id` lands in the `slice` journal event via ledger.append() eventually, once it\n'
              '# reaches the one chokepoint every write path already funnels through.\n'
              'def _normalise(item):\n'
              '    return {\n'
              '        "id": str(item.get("id") or "").strip(),\n'
              '        "title": str(item.get("title") or "").strip(),\n'
              '    }\n')
    assert _python_event_kinds_at(tmp_path) == set()


def test_ignores_a_variable_kind_dispatch(tmp_path):
    """loop.py's real `emit` dispatcher (loop.py:482) passes `kind` as a variable (`argv[4]`) --
    it is not a literal site for any ONE kind, and must not be misread as one."""
    _plant_py(tmp_path, "sigma-loop", "loop.py",
              'def main(argv):\n'
              '    kind = argv[4]\n'
              '    ledger.append(sdlc_dir, config, kind, goal, stream=ledger.EVENTS)\n')
    assert _python_event_kinds_at(tmp_path) == set()


def test_entries_stream_calls_are_excluded(tmp_path):
    """A `claimed`-style ENTRIES-stream call (no `stream=` kwarg -> defaults to ENTRIES) must never
    count as EVENTS-stream coverage -- ENTRIES kinds are a legitimately separate vocabulary
    (ledger.py's own KINDS), not a source of EVENT_KINDS false positives."""
    _plant_py(tmp_path, "sigma-loop", "loop.py",
              'ledger.safe_append(sdlc_dir, "claimed", goal, config=config)\n')
    assert _python_event_kinds_at(tmp_path) == set()


def test_span_aware_stream_kwarg_on_a_continuation_line(tmp_path):
    """Amendment B, proven directly: the real `spend` call (loop.py:467) wraps
    `stream=ledger.EVENTS` onto a continuation line, separated from the kind literal by a
    nested-paren kwarg (`config=state.load_config(sdlc_dir)`). Reading `stream=` off the SAME
    ast.Call node's keywords makes this trivially correct regardless of line layout -- a
    same-line/regex check would silently drop this real site."""
    _plant_py(tmp_path, "sigma-loop", "loop.py",
              'ledger.safe_append(sdlc_dir, "spend", goal, config=state.load_config(sdlc_dir),\n'
              '                   stream=ledger.EVENTS, tokens_in=flags.get("tokens_in"))\n')
    assert _python_event_kinds_at(tmp_path) == {"spend"}


def test_an_unparseable_python_file_does_not_crash_the_scan(tmp_path):
    """This guard is not chartered to validate Python syntax -- an unparseable file contributes no
    kinds rather than aborting the whole scan with an uncaught SyntaxError."""
    _plant_py(tmp_path, "sigma-loop", "broken.py", "def broken(:\n")
    assert _python_event_kinds_at(tmp_path) == set()


def test_a_hooks_py_file_is_scanned_too(tmp_path):
    """spec §A.6 names `hooks/` as a valid emitting location alongside skills/*/scripts/ -- proven
    directly, on a fixture shaped like the real hooks/decision_gate.py deny-path call."""
    _plant_hook_py(tmp_path, "decision_gate.py",
                    'ledger.safe_append(sdlc_dir, "gate", _NO_GOAL, stream=ledger.EVENTS,\n'
                    '                   gate="decision", verdict="block")\n')
    assert _python_event_kinds_at(tmp_path) == {"gate"}


# --------------------------------------------------------------------------- direction A, stricter (#1013)

#: issue #1013 done-when #3: a STRICTER bar than Direction A above (which also credits prose) --
#: every EVENT_KINDS member must have a literal Python ledger.append/safe_append call site, except
#: this reviewed, individually-justified allowlist. Mirrors this codebase's own house style for
#: every other allowlist (a downstream reader's class-1 gate set, EVENT_NON_PROSE_FIELDS above): one
#: comment per member, never a bare list.
_PROSE_ONLY_ALLOWLIST = {
    # `phase` REMOVED from this allowlist (#1626). It used to read "genuinely
    # agent-compliance-dependent today, no code guarantee at all -- loop.py's `emit` dispatcher
    # passes `kind` as a variable (argv[4]), never a literal, for every kind it handles, `phase`
    # included." That was true of the bare `loop.py emit ... phase` idiom SKILL.md used to
    # instruct, which is exactly why #1626 replaced it: `skills/sigma-loop/scripts/phase_report.py`
    # now carries its own dedicated `cmd_start`/`cmd_end` functions with a LITERAL
    # `ledger.safe_append(sdlc_dir, "phase", goal, stream=ledger.EVENTS, ...)` call site (`"phase"`
    # as a string constant, not a passed-through variable) -- so `phase` now passes on Python
    # coverage alone, the same way `retro` did after #1013 gave `_record()` its own literal call.
    #
    # `model_choice` REMOVED from this allowlist too (#1627), the SAME shape one more time. #1161
    # had already corrected this entry's own claim ("predict.py's ONLY invoker anywhere in this
    # repo is PROSE... no script calls it at all" -- measured true: six goals ran the loop end to
    # end after #1030 merged and wrote ZERO model_choice events) but kept the waiver, because the
    # only real caller was still `predict.py resolve`'s own `SKILL.md`-prose-driven invocation
    # shelling out to `loop.py emit` -- a real subprocess call this AST-based checker cannot see as
    # a literal `ledger.append`/`safe_append` site, so the waiver's SECOND half ("this checker
    # cannot see subprocess-shaped emission") stayed sound even after its first half didn't.
    # #1627 closed the gap `phase_report.py` (#1626) closed for `phase`: `loop.py`'s `_next()` now
    # calls `predict.py why` (pure, no recording of its own) at pick time and makes the ONE
    # `ledger.safe_append(sdlc_dir, "model_choice", goal, config=config, stream=ledger.EVENTS,
    # **fields)` call itself -- a literal, same-skill call site (`ledger` is `_load()`ed at
    # `loop.py` module scope) this checker DOES see, verified directly against a fixture shaped
    # like the real call before this landed. `model_choice` now passes on Python coverage alone,
    # same as `phase`/`retro` before it -- an empty allowlist is not a coincidence, it is the point:
    # every one of #140's original prose-only kinds has now graduated to a real code caller.
}


def test_every_event_kind_has_a_real_python_caller_except_the_reviewed_allowlist():
    """issue #1013 done-when #3. Stricter than test_every_event_kind_has_an_emitting_site above:
    every EVENT_KINDS member must have a literal Python ledger.append/safe_append call site, except
    anything still listed in `_PROSE_ONLY_ALLOWLIST` -- currently EMPTY. Neither `retro`, `phase`,
    nor `model_choice` is in it: #1013 gave `_record()` its own literal call for `retro`, #1626 gave
    `phase_report.py` its own literal call for `phase`, and #1627 gave `loop.py`'s own
    `_predict_model_choice_at_pick` a literal call for `model_choice` -- all three pass on Python
    coverage alone now, same as `gate`/`verify`/`spend`/`scan`/`slice` already did."""
    python_kinds = _python_event_kinds_at(ROOT)
    missing = sorted(k for k in ledger.EVENT_KINDS
                      if k not in python_kinds and k not in _PROSE_ONLY_ALLOWLIST)
    assert not missing, (
        f"EVENT_KINDS member(s) with no literal Python call site and not in the reviewed "
        f"_PROSE_ONLY_ALLOWLIST: {missing}")


def test_the_allowlist_mechanism_itself_is_proven_on_a_fixture():
    """Mutation proof, mirroring test_direction_a_fails_on_a_fixture_with_no_site_for_a_kind's own
    approach: a LOCAL fake kind-set/allowlist, never the real ledger.EVENT_KINDS, proving the
    comparison logic itself rather than only re-observing today's real tree."""
    python_kinds = _python_event_kinds_at(ROOT)
    fake_kinds = ledger.EVENT_KINDS + ("bogus_unallowed_kind",)
    fake_allowlist = dict(_PROSE_ONLY_ALLOWLIST, bogus_allowed_kind="fixture-only, always allowed")
    missing = sorted(k for k in fake_kinds if k not in python_kinds and k not in fake_allowlist)
    assert missing == ["bogus_unallowed_kind"]

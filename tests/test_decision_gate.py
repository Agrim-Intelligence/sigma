"""Decision gate: the one guardrail in this kit that is not a prompt. An `invariant` violation is
DENIED before the edit happens, by a script that does not negotiate.

The value of a gate like this is entirely in its precision. A gate that cries wolf gets clicked
through, and then it protects nothing — so most of these test the cases where it must STAY SILENT,
not the ones where it fires."""
import io, json, pathlib, importlib.util, re, tempfile, subprocess, sys

import pytest
from journal_events import journal_events

ROOT = pathlib.Path(__file__).resolve().parent.parent
G = ROOT / "hooks" / "decision_gate.py"
LEDGER_PATH = ROOT / "skills" / "sigma-loop" / "scripts" / "ledger.py"


def _gate():
    spec = importlib.util.spec_from_file_location("decision_gate", G)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _ledger_mod():
    spec = importlib.util.spec_from_file_location("ledger", LEDGER_PATH)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _reg(*decisions):
    return {"version": 1, "decisions": list(decisions)}


def _inv(**over):
    d = {"id": "INV-001", "title": "Bounded timeouts", "class": "invariant", "status": "active",
         "statement": "No call may set a timeout above 30s.", "rationale": "one slow dep = outage",
         "protected_paths": ["src/**/*.py"],
         "protected_params": [{"name": "timeout", "op": "le", "value": 30}]}
    d.update(over)
    return d


def _edit(path, new):
    return ("Edit", {"file_path": path, "new_string": new})


# --- it fires when it should -------------------------------------------------------------------

def test_denies_an_invariant_violation():
    m = _gate()
    tool, ti = _edit("/repo/src/api/client.py", "timeout = 120")
    decision, reason, decision_id = m.evaluate(tool, ti, _reg(_inv()), "/repo")
    assert decision == "deny"
    assert decision_id == "INV-001"
    assert "timeout=120" in reason and "one slow dep = outage" in reason   # statement + why


def test_asks_rather_than_denies_on_a_recipe():
    """A recipe is a proven default worth a second thought, not a law."""
    m = _gate()
    tool, ti = _edit("/repo/src/a.py", "timeout = 120")
    assert m.evaluate(tool, ti, _reg(_inv(**{"class": "recipe"})), "/repo")[0] == "ask"


def test_caution_on_touch_guards_a_path_with_no_params():
    """Without this, a decision guarding a dangerous PATH but declaring no params falls through to
    allow SILENTLY — so code that deploys or spends money could not be guarded at all."""
    m = _gate()
    d = _inv(id="INV-9", protected_params=[], caution_on_touch=True,
             protected_paths=["deploy/**"], statement="Deploy scripts spend money.")
    decision, reason, decision_id = m.evaluate(*_edit("/repo/deploy/ship.py", "x = 1"), _reg(d), "/repo")
    assert decision == "ask" and "spend money" in reason
    assert decision_id is None                            # only `deny` ever carries a decision_id


def test_editing_the_registry_always_asks():
    """Changing a recorded invariant is a supersession the user makes deliberately — never a silent
    edit by the agent that is about to be bound by it."""
    m = _gate()
    decision, reason, decision_id = m.evaluate(*_edit("/repo/.sdlc/decisions.json", "{}"), _reg(), "/repo")
    assert decision == "ask" and "supersede" in reason.lower()
    assert decision_id is None


# --- it stays silent when it should — the tests that make it trustworthy ------------------------

def test_a_value_that_still_satisfies_is_allowed():
    m = _gate()
    assert m.evaluate(*_edit("/repo/src/a.py", "timeout = 5"), _reg(_inv()), "/repo")[0] == "allow"


def test_the_param_is_scoped_to_its_own_decisions_paths():
    """A name as common as `timeout` appears everywhere. Checking it outside the paths its decision
    declares is what would make this gate unusable."""
    m = _gate()
    assert m.evaluate(*_edit("/repo/tests/t.py", "timeout = 999"), _reg(_inv()), "/repo")[0] == "allow"


def test_prose_and_comments_do_not_trip_it():
    m = _gate()
    r = _reg(_inv())
    assert m.evaluate(*_edit("/repo/src/a.py", "# timeout = 120 was the old default"), r, "/repo")[0] == "allow"
    assert m.evaluate(*_edit("/repo/src/a.py", 'doc = "set timeout: 120 here"'), r, "/repo")[0] == "allow"


def test_a_similar_name_is_not_the_protected_one():
    m = _gate()
    r = _reg(_inv())
    for line in ("read_timeout = 120", "self.timeout_ms = 120", "x.timeout = 120"):
        assert m.evaluate(*_edit("/repo/src/a.py", line), r, "/repo")[0] == "allow", line


def test_non_literal_values_are_left_alone():
    """The gate judges values it can actually read. Guessing at an expression would produce false
    denies, which cost more trust than the misses cost safety."""
    m = _gate()
    r = _reg(_inv())
    for line in ("timeout = CONFIG.default", "timeout = compute()", "timeout = a + b"):
        assert m.evaluate(*_edit("/repo/src/a.py", line), r, "/repo")[0] == "allow", line


def test_a_literal_led_expression_abstains_instead_of_matching_its_leading_token():
    """#1783: `_LITERAL.search` is unanchored, so an expression whose FIRST operand happens to be
    literal-shaped (`61 - 5`, `50 * 2`, `60 + 1`) used to have it greedily grab just that leading
    token and treat it as the WHOLE value -- silently wrong, not correctly abstaining the way the
    NAME/CALL-led case above does. `61 - 5`'s true value (56) SATISFIES `le 60`, but the old code
    read only the leading `61` and false-DENIED; `50 * 2` and `60 + 1` both VIOLATE (100, 61) but
    the old code read only their leading `50`/`60` and false-ALLOWED. The gate can't evaluate an
    expression, so every one of these must abstain, regardless of which way its leading token
    would have pushed the (wrong) verdict."""
    m = _gate()
    d = _inv(protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
    for line in ("max_iterations = 61 - 5", "max_iterations = 50 * 2", "max_iterations = 60 + 1"):
        assert m.evaluate(*_edit("/repo/src/a.py", line), _reg(d), "/repo")[0] == "allow", line


def test_a_collection_value_abstains_even_when_it_contains_a_literal_shaped_token():
    """#1783 (comment): the same missing value-span boundary also false-DENIES a non-literal value
    that merely CONTAINS a literal-shaped token -- a JSON array is not a literal this gate judges,
    so `_LITERAL.search` walking past `[999]` into the unrelated `3` on `retries` was never the bug
    here; walking past the list's OWN `999` and reporting that as `max_iterations`'s value is.
    Reproduces the issue's exact Python-equivalent repro shape."""
    m = _gate()
    d = _inv(protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
    line = "max_iterations = [999]; retries = 3"
    assert m.evaluate(*_edit("/repo/src/a.py", line), _reg(d), "/repo")[0] == "allow"


def test_superseded_decisions_do_not_fire():
    m = _gate()
    r = _reg(_inv(status="superseded"))
    assert m.evaluate(*_edit("/repo/src/a.py", "timeout = 120"), r, "/repo")[0] == "allow"


def test_non_edit_tools_and_empty_registries_allow():
    m = _gate()
    assert m.evaluate("Bash", {"command": "rm -rf /"}, _reg(_inv()), "/repo")[0] == "allow"
    assert m.evaluate(*_edit("/repo/src/a.py", "timeout = 120"), _reg(), "/repo")[0] == "allow"


def test_float_comparison_uses_tolerance_not_equality():
    """`eq` on a float via naive == would deny a value that is correct to every digit that matters."""
    m = _gate()
    d = _inv(protected_params=[{"name": "ratio", "op": "eq", "value": 0.1}])
    assert m.evaluate(*_edit("/repo/src/a.py", "ratio = 0.1"), _reg(d), "/repo")[0] == "allow"


def test_in_op_and_booleans():
    m = _gate()
    d = _inv(protected_params=[{"name": "region", "op": "in", "value": ["eu", "us"]}])
    assert m.evaluate(*_edit("/repo/src/a.py", 'region = "eu"'), _reg(d), "/repo")[0] == "allow"
    assert m.evaluate(*_edit("/repo/src/a.py", 'region = "cn"'), _reg(d), "/repo")[0] == "deny"
    b = _inv(protected_params=[{"name": "verify_ssl", "op": "eq", "value": True}])
    assert m.evaluate(*_edit("/repo/src/a.py", "verify_ssl = False"), _reg(b), "/repo")[0] == "deny"


# --- fail-open and opt-in ------------------------------------------------------------------------

def test_a_malformed_registry_never_blocks():
    """Fail-open is the contract: a gate that wedges you on its own bug is worse than a missed
    check. `check` is the backstop."""
    m = _gate()
    for bad in ({"decisions": "not-a-list"}, {"decisions": [{"id": "X"}]}, {}):
        assert m.evaluate(*_edit("/repo/src/a.py", "timeout = 120"), bad, "/repo")[0] == "allow"


def test_hook_allows_when_no_registry_exists(tmp_path=None):
    """Installing the plugin must change nothing until a repo authors a registry."""
    with tempfile.TemporaryDirectory() as d:
        out = subprocess.run([sys.executable, str(G)], cwd=d,
                             input=json.dumps({"tool_name": "Edit",
                                               "tool_input": {"file_path": "src/a.py",
                                                              "new_string": "timeout = 120"}}),
                             capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        assert out.returncode == 0 and out.stdout.strip() == ""     # silence = allow


def test_enabled_false_disables_without_deleting_the_registry():
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; base.mkdir()
        (base / "decisions.json").write_text(json.dumps(_reg(_inv())))
        assert m.enabled(d) is True                                  # registry alone = on
        (base / "config.json").write_text(json.dumps({"gates": {"decision_gate": {"enabled": False}}}))
        assert m.enabled(d) is False


# --- the backstop: check + validate ---------------------------------------------------------------

def test_check_finds_violations_already_on_disk():
    """The hook only guards NEW edits; code predating the registry is invisible to it. This is the
    first question anyone asks after authoring one."""
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / ".sdlc").mkdir(); (root / "src").mkdir()
        (root / ".sdlc" / "decisions.json").write_text(json.dumps(_reg(_inv(protected_paths=["src/*.py"]))))
        (root / "src" / "bad.py").write_text("timeout = 300\n")
        (root / "src" / "ok.py").write_text("timeout = 10\n")
        found = m.check(d)
        assert len(found) == 1 and found[0][0] == "src/bad.py" and found[0][3] == 300


def test_check_does_not_escape_root_via_parent_traversal():
    """#1786: `root.glob(pat)` is not sandboxed to `root` -- `Path(root).glob("../secret.py")`
    escapes it outright. A `protected_paths` entry containing `../` must not let `check()` read
    (and report violations from) a file outside the intended project root."""
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        outer = pathlib.Path(d)
        repo = outer / "repo"
        repo.mkdir()
        (outer / "outer_secret.py").write_text("timeout = 999\n")  # violates <= 30, but OUTSIDE repo
        (repo / ".sdlc").mkdir()
        dec = _inv(protected_paths=["../outer_secret.py"])
        (repo / ".sdlc" / "decisions.json").write_text(json.dumps(_reg(dec)))
        assert m.check(str(repo)) == []


def test_validate_does_not_escape_root_via_parent_traversal():
    """#1786: the same unsandboxed glob, reached through `validate()`'s "can this param ever fire"
    check added by #1532 -- a `../` pattern must not let `validate()` read a file outside `root`,
    nor name that escaping path in a reported problem (`_rel()` falls back to a raw path string,
    which would otherwise disclose it)."""
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        outer = pathlib.Path(d)
        repo = outer / "repo"
        repo.mkdir()
        (outer / "outer_cfg.json").write_text('{"other_key": 1}\n')
        dec = _inv(id="INV-ESC", protected_paths=["../outer_cfg.json"],
                   protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
        assert m.validate(_reg(dec), repo) == []


def test_validate_catches_entries_that_can_never_fire():
    """A registry's failure mode is being quietly unenforceable, not loudly broken."""
    m = _gate()
    problems = m.validate(_reg(
        {"id": "A", "class": "invariant", "protected_paths": [], "protected_params": []},
        {"id": "A", "class": "nonsense", "protected_paths": ["x"], "protected_params": []},
        {"id": "C", "class": "recipe", "protected_paths": ["x"],
         "protected_params": [{"name": "n", "op": "≈", "value": 1}]},
    ))
    joined = " ".join(problems)
    assert "duplicate" in joined and "never match" in joined
    assert "never fire" in joined and "expected one of" in joined


def test_validate_catches_a_malformed_id_at_authoring_time():
    """#272: the id format `sigma-decide` documents (no internal whitespace, no colons) is enforced
    HERE, at authoring time -- before a malformed id is ever attached to a real denial. Whitespace
    truncates on the old free-text read side; a colon collides with the `ident: message` separator
    both `evaluate()`'s reason text and a downstream denial metric's extraction regex rely on."""
    m = _gate()
    problems = m.validate(_reg(
        _inv(id="DEC 001", protected_paths=["x"], protected_params=[]),
        _inv(id="DEC:002", protected_paths=["x"], protected_params=[]),
    ))
    joined = " ".join(problems)
    assert "DEC 001" in joined and "whitespace or a colon" in joined
    assert "DEC:002" in joined
    # a well-formed id must not be flagged by this check
    assert not m.validate(_reg(_inv(id="DEC-003")))


def test_validate_does_not_crash_on_a_non_string_id():
    """JSON allows `"id": 42` -- nothing in the registry format or `load_registry()` coerces it to
    a string, and this is exactly the kind of authoring typo (a bare number where a quoted token was
    meant) that `validate` exists to catch cleanly. The whitespace/colon format check must degrade
    to a reported PROBLEM, never an unhandled TypeError from `re.Pattern.search` on a non-str/bytes
    value -- and, matching a plain int id validating cleanly on main before this check existed, a
    well-formed numeric id must still pass with no problems."""
    m = _gate()
    problems = m.validate(_reg(_inv(id=42)))
    assert problems == []


def test_cli_reports_cleanly_with_no_registry():
    out = subprocess.run([sys.executable, str(G), "check", tempfile.gettempdir()],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "no registry" in out.stdout


def test_registered_as_a_pretooluse_hook():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    cmds = [h["command"] for entry in hooks["hooks"]["PreToolUse"] for h in entry["hooks"]]
    assert any("decision_gate.py" in c for c in cmds)


def test_no_source_repo_leakage():
    banned = ("media-orch", "OnShot", "onshot", "Temporal", "RunPod", "decisions.yaml", "yaml")
    src = G.read_text() + (ROOT / "skills" / "sigma-decide" / "SKILL.md").read_text()
    for b in banned:
        assert b not in src, f"decision gate leaked '{b}'"


def test_double_star_matches_zero_directories_like_every_other_glob_dialect():
    """`fnmatch` has no path-segment concept, so a bare fnmatch of `src/**/*.py` misses `src/a.py`.
    A rule written that way would silently guard the nested files and not the top-level ones — the
    worst kind of failure, because the registry looks like it is protecting something."""
    m = _gate()
    pats = ["src/**/*.py"]
    assert m.matches("src/a.py", pats)              # zero directories
    assert m.matches("src/api/client.py", pats)     # one directory
    assert m.matches("src/a/b/c.py", pats)          # several
    assert not m.matches("tests/a.py", pats)
    assert not m.matches("src/a.ts", pats)


def test_leading_double_star_matches_the_repo_root():
    m = _gate()
    assert m.matches("Makefile", ["**/Makefile"])
    assert m.matches("build/Makefile", ["**/Makefile"])


def test_non_string_patterns_are_skipped_not_raised():
    m = _gate()
    assert m.matches("src/a.py", [None, 42, "src/a.py"]) is True
    assert m.matches("src/a.py", [None, 42]) is False


def test_a_violating_value_quoted_inside_prose_is_not_a_false_deny():
    """The single worst failure mode. A false deny teaches people to click through the gate, and a
    gate that gets clicked through protects nothing — so a quoted mention must stay silent while a
    real string-valued assignment still gets judged."""
    m = _gate()
    r = _reg(_inv())
    for line in ('doc = "set timeout: 120 here"',
                 "msg = 'timeout = 999 is wrong'",
                 'log.warn("timeout = 500 exceeded")'):
        assert m.evaluate(*_edit("/repo/src/a.py", line), r, "/repo")[0] == "allow", line
    # ...while a genuine string-valued assignment is still checked
    d = _inv(protected_params=[{"name": "region", "op": "in", "value": ["eu"]}])
    assert m.evaluate(*_edit("/repo/src/a.py", 'region = "cn"'), _reg(d), "/repo")[0] == "deny"


# --- #139 Slice 5: site f (gate{decision}) -------------------------------------------------------
# Needs ledger.enabled AND journal.enabled (the Slice 0 AND-gate) for an events-stream write to
# actually land — see test_ledger.py's gate tests for the gate itself. Emission is on `deny` ONLY —
# not `ask`, not `allow` — since `evaluate()` runs on every Edit/Write in every session and
# instrumenting the overwhelming-majority `allow` case would flood the events stream.

def _project(d, *decisions, journal=True):
    root = pathlib.Path(d)
    base = root / ".sdlc"
    base.mkdir()
    (base / "decisions.json").write_text(json.dumps(_reg(*decisions)))
    cfg = {"ledger": {"enabled": True, "actor": "rae"}}
    if journal:
        cfg["journal"] = {"enabled": True}
    (base / "config.json").write_text(json.dumps(cfg))
    return root


def _run_hook(root, tool_input, tool_name="Edit"):
    return subprocess.run([sys.executable, str(G)], cwd=str(root),
                          input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
                          capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})


def _gate_events(root):
    ledger = _ledger_mod()
    return [e for e in journal_events(ledger, str(root / ".sdlc")) if e["kind"] == "gate"]


def test_deny_emits_a_block_gate_event():
    with tempfile.TemporaryDirectory() as d:
        root = _project(d, _inv())
        out = _run_hook(root, {"file_path": "src/a.py", "new_string": "timeout = 120"})
        payload = json.loads(out.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"     # real deny, unchanged
        events = _gate_events(root)
        assert len(events) == 1
        e = events[0]
        assert e["gate"] == "decision" and e["verdict"] == "block"
        assert e["goal"] == "(decision-gate)"                # the sentinel: no real goal exists here
        assert e["why"].startswith("src/a.py: ")              # file path, then evaluate()'s full reason
        assert "timeout=120" in e["why"]
        assert e["decision_id"] == "INV-001"                  # #272: structured, not reconstructed


def test_deny_emits_a_decision_id_event_field():
    """#272: the denial's id is recoverable as a real, structured `decision_id` field on the event
    itself -- not only by parsing `why`'s free text. This is the acceptance criterion at the actual
    write/read boundary a downstream consumer uses (`_run_hook` -> real events stream), not just at
    `evaluate()`'s return value."""
    with tempfile.TemporaryDirectory() as d:
        root = _project(d, _inv(id="INV-42"))
        out = _run_hook(root, {"file_path": "src/a.py", "new_string": "timeout = 120"})
        assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        events = _gate_events(root)
        assert len(events) == 1
        assert events[0]["decision_id"] == "INV-42"


# --- #272: the id-recoverability regression --------------------------------------------------
# An analytics reader with no structured field had to reconstruct the id with a best-effort
# anchored regex over `why`'s free text (a downstream denial metric took the first
# non-space token after the first colon). That regex breaks in two ways a format
# contract alone cannot fully close from the READ side -- proven here against the actual regex,
# not a paraphrase of it, so this test would catch a future change to either side that silently
# reintroduces the coupling.
_OLD_FREE_TEXT_EXTRACT = re.compile(r"^[^:]*:\s*([^\s:]+)")


def test_an_id_with_internal_whitespace_no_longer_silently_truncates():
    """Failure mode 1: `"DEC 001"` is syntactically indistinguishable from a real single-word id
    once it is inside free text, so the OLD regex truncates it to its first word ("DEC") -- this
    cannot be detected on read, only avoided by a format contract at authoring time (sigma-decide's
    `validate()`). The regression this test actually guards is the NEW field: it must carry the
    id whole regardless of what the old free-text path does with it."""
    m = _gate()
    d = _inv(id="DEC 001", title="Multi-token id")
    decision, reason, decision_id = m.evaluate(*_edit("/repo/src/a.py", "timeout = 120"), _reg(d), "/repo")
    assert decision == "deny"
    why = f"{m._rel('/repo/src/a.py', '/repo')}: {reason}"          # what _emit_decision_event writes
    old = _OLD_FREE_TEXT_EXTRACT.match(why)
    assert old and old.group(1) == "DEC"                # OLD path: still silently truncated (unfixed
                                                          # by design -- this proves the failure is real)
    assert decision_id == "DEC 001"                      # NEW path: correct, full id, not reconstructed


def test_a_colon_in_the_file_path_no_longer_silently_mis_anchors():
    """Failure mode 2: the more serious one, since it lands a silently-WRONG non-NULL id rather
    than an absent one. A legally-named POSIX file containing a colon (no special path shape
    required) anchors the OLD anchored regex at the WRONG colon."""
    m = _gate()
    d = _inv(id="DEC-009", title="Colon in path", protected_paths=["**/*.py"])
    decision, reason, decision_id = m.evaluate(*_edit("/repo/weird:file.py", "timeout = 120"), _reg(d), "/repo")
    assert decision == "deny"
    why = f"{m._rel('/repo/weird:file.py', '/repo')}: {reason}"
    old = _OLD_FREE_TEXT_EXTRACT.match(why)
    assert old and old.group(1) == "file.py"             # OLD path: silently WRONG, non-NULL --
    assert old.group(1) != "DEC-009"                     # reports as a successfully-identified denial
    assert decision_id == "DEC-009"                       # NEW path: correct regardless of the path


def test_deny_gate_event_scrubs_a_planted_secret_in_the_message():
    """#141: the deny path is one of the three deterministic, fail-open call sites — it must
    NEVER reject (a raise here could silently turn a real `deny` into an allow), only sanitize.
    `gate.why` is a declared prose field, so `ledger.append()` scrubs it automatically with zero
    code change to decision_gate.py; this proves that end to end through the real hook subprocess."""
    with tempfile.TemporaryDirectory() as d:
        SECRET = "AK" "IAIOSFODNN7EXAMPLE"
        root = _project(d, _inv(statement=f"Never hardcode {SECRET} in source."))
        out = _run_hook(root, {"file_path": "src/a.py", "new_string": "timeout = 120"})
        payload = json.loads(out.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"    # real deny, unchanged
        events = _gate_events(root)
        assert len(events) == 1
        assert SECRET not in events[0]["why"] and "[REDACTED" in events[0]["why"]


def test_deny_gate_event_flattens_rather_than_rejects_a_newline_in_the_message():
    """#141 amendment A: the deny path is automatic, not agent-typed at a CLI — a newline in the
    combined reason message must be FLATTENED (never a rejected/dropped event), unlike
    `loop.py emit`/`spend` or `work.py post-review`'s CLI-level hard reject."""
    with tempfile.TemporaryDirectory() as d:
        root = _project(d, _inv(statement="Line one.\nLine two with more detail."))
        out = _run_hook(root, {"file_path": "src/a.py", "new_string": "timeout = 120"})
        payload = json.loads(out.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        events = _gate_events(root)
        assert len(events) == 1
        assert "\n" not in events[0]["why"]


def test_ask_and_allow_emit_nothing():
    with tempfile.TemporaryDirectory() as d:
        root = _project(d, _inv(**{"class": "recipe"}))       # a recipe violation -> ask, not deny
        ask_out = _run_hook(root, {"file_path": "src/a.py", "new_string": "timeout = 120"})
        assert json.loads(ask_out.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
        allow_out = _run_hook(root, {"file_path": "src/a.py", "new_string": "timeout = 10"})
        assert allow_out.stdout.strip() == ""                  # silence = allow
        assert _gate_events(root) == []


def test_no_event_when_telemetry_is_off():
    with tempfile.TemporaryDirectory() as d:
        root = _project(d, _inv(), journal=False)
        out = _run_hook(root, {"file_path": "src/a.py", "new_string": "timeout = 120"})
        assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert _gate_events(root) == []


def test_a_raising_telemetry_call_still_denies(monkeypatch, capsys):
    """The most important test in this plan. `main()`'s outer handler is `except Exception:
    sys.exit(0)` with NO print — byte-identical to what a genuine `allow` emits — so a raising
    journal call sitting in the SAME try block as `_emit(...)` would silently turn a real `deny`
    into what the harness reads as an allow. Proves the emit call is wrapped in its OWN local
    try/except and `_emit(decision, reason)` is always reached regardless."""
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        root = _project(d, _inv())
        monkeypatch.chdir(root)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"tool_name": "Edit", "tool_input": {"file_path": "src/a.py", "new_string": "timeout = 120"}})))

        def boom(*a, **k):
            raise RuntimeError("journal broke")
        monkeypatch.setattr(m, "_emit_decision_event", boom)

        with pytest.raises(SystemExit) as exc:
            m.main(["decision_gate.py"])
        assert exc.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"        # the real deny survives


# --- #1532: JSON's `"name": value` was invisible to the matcher --------------------------------
# Every dialect below must be judged the same way: a literal assignment fires, an expression
# abstains, and a same-named mention in prose never trips it — regardless of whether the file
# happens to be JSON, YAML-flavored config, or Python.

def test_json_key_value_now_fires():
    """The bug: `"max_iterations": 61` has the key's own closing quote between the name and the
    colon, which the old regex's `name\\s*[=:]` could never bridge."""
    m = _gate()
    d = _inv(protected_paths=["*.json"],
             protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
    decision, reason, _ = m.evaluate(*_edit("/repo/cfg.json", '  "max_iterations": 61,'), _reg(d), "/repo")
    assert decision == "deny" and "max_iterations=61" in reason


def test_yaml_bare_colon_now_has_positive_firing_coverage():
    """Every existing test of the `name: value` shape (test_prose_and_comments_do_not_trip_it etc.)
    only proves it stays SILENT. None pins that a real bare-colon violation actually fires — this
    closes that gap; the shape was already nominally supported by the `[=:]` alternation, just
    never proven by execution."""
    m = _gate()
    d = _inv(protected_paths=["*.yml"],
             protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
    decision, reason, _ = m.evaluate(*_edit("/repo/cfg.yml", "max_iterations: 61"), _reg(d), "/repo")
    assert decision == "deny" and "max_iterations=61" in reason


def test_python_assignment_still_fires_after_the_json_fix():
    """Regression guard: the quote-tolerant separator must not stop matching the plain `name =
    value` shape every other test in this file already relies on."""
    m = _gate()
    d = _inv(protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
    decision, reason, _ = m.evaluate(*_edit("/repo/src/a.py", "max_iterations = 61"), _reg(d), "/repo")
    assert decision == "deny" and "max_iterations=61" in reason


def test_an_expression_value_still_abstains_under_the_json_shape_too():
    """`test_non_literal_values_are_left_alone` already pins abstention for bare Python; this pins
    the SAME abstention for a JSON-quoted key, since the fix touches the name/separator match, not
    the literal-detection step, and the two must stay independent."""
    m = _gate()
    d = _inv(protected_paths=["*.json"],
             protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
    assert m.evaluate(*_edit("/repo/cfg.json", '"max_iterations": a + b'), _reg(d), "/repo")[0] == "allow"


def test_a_multi_key_json_line_is_not_abandoned_after_an_irrelevant_earlier_mention():
    """The exact shape found live in this repo's own .sdlc/config.json (e.g. the single-line
    `backlog_check` object packs many keys on one line): more than one key sharing a line, and —
    worse — an earlier, irrelevant mention of the SAME name inside a neighboring string value. The
    old `re.search` (first match only) gave up on the whole line the moment that first mention
    failed to parse; the real key later on the same line must still be reached."""
    m = _gate()
    d = _inv(protected_paths=["*.json"],
             protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
    line = '{"note": "reminder: max_iterations must stay sane", "max_iterations": 999}'
    decision, reason, _ = m.evaluate(*_edit("/repo/cfg.json", line), _reg(d), "/repo")
    assert decision == "deny" and "max_iterations=999" in reason


def test_a_same_line_prose_mention_that_itself_looks_like_an_assignment_does_not_hide_a_later_real_one():
    """The sharper version of the case above: here the FIRST mention doesn't just fail to parse —
    it parses AS a would-be assignment (the quoted prose itself contains `name:`), gets correctly
    rejected by `_in_string`, and only THEN does scanning need to continue on the same line. This is
    what actually distinguishes `re.finditer` over a SHORT name+separator pattern from a wider
    capture group under `re.search`: a naive fix that keeps a single greedy `(.+)` tail inside the
    finditer'd pattern would consume to the end of the line on this FIRST (rejected) match and never
    reach the second, real one."""
    m = _gate()
    d = _inv(protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
    line = 'note = "max_iterations: 5 is the old default"; max_iterations = 999'
    decision, reason, _ = m.evaluate(*_edit("/repo/src/a.py", line), _reg(d), "/repo")
    assert decision == "deny" and "max_iterations=999" in reason


def test_a_real_assignment_before_a_semicolon_is_not_swallowed_into_the_next_statement():
    """The mirror image of the case above, and a #1783-fix regression: here the REAL assignment
    comes FIRST and an unrelated statement follows it on the same line, separated by `;` --
    Python's statement separator, not a value terminator `_value_span` used to know about. Without
    a `;` stop, the span read past the semicolon into `other_call()`, `_LITERAL.fullmatch` failed
    against that non-literal trailing text, and the gate silently ABSTAINED on a genuine violation
    instead of catching it -- exactly the false-ALLOW #1783's own fix was written to prevent, just
    on the other side of the `;`."""
    m = _gate()
    d = _inv(protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
    line = "max_iterations = 999; other_call()"
    decision, reason, _ = m.evaluate(*_edit("/repo/src/a.py", line), _reg(d), "/repo")
    assert decision == "deny" and "max_iterations=999" in reason


# --- #1532: validate() now catches a param that can never be seen in its own protected file -----

def test_validate_catches_a_param_that_can_never_be_seen_in_its_own_protected_file():
    """A registry entry can be well-formed (valid paths, valid op) and still be silently
    unenforceable if the name never appears in assignment shape anywhere in the file(s) it
    protects — this repo's own `loop-budget-ceiling` decision hit exactly this against the
    pre-fix regex (north-star.md:140-144 already names it as the registry's own documented
    failure mode). validate() must catch it, not leave it to be discovered by a customer."""
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "cfg.json").write_text('{"other_key": 1}\n')
        dec = _inv(id="INV-GHOST", protected_paths=["cfg.json"],
                   protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
        problems = m.validate(_reg(dec), root)
        joined = " ".join(problems)
        assert "INV-GHOST" in joined and "max_iterations" in joined and "cfg.json" in joined


def test_validate_does_not_flag_a_param_that_is_actually_present():
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "cfg.json").write_text('{"max_iterations": 60}\n')
        dec = _inv(id="INV-REAL", protected_paths=["cfg.json"],
                   protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
        assert m.validate(_reg(dec), root) == []


def test_validate_skips_the_cannot_fire_check_when_the_protected_file_does_not_exist_yet():
    """A decision authored before its target file exists is a different, not-yet-in-scope case —
    not a defect to report. Silence here is deliberate, not an oversight."""
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        dec = _inv(id="INV-FUTURE", protected_paths=["not_yet_created.json"])
        assert m.validate(_reg(dec), root) == []


def test_validate_with_no_root_skips_the_cannot_fire_check_entirely():
    """Every existing call site (this file's other four `m.validate(_reg(...))` calls, and the
    CLI's own use before this change) passes only a registry — `validate(registry)` must keep
    working exactly as before, with no filesystem access at all."""
    m = _gate()
    dec = _inv(protected_paths=["cfg.json"],
               protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
    assert m.validate(_reg(dec)) == []


def test_validate_names_an_unfireable_files_target_only_once_even_with_overlapping_globs():
    """Code review (#1532): two `protected_paths` patterns can resolve to the SAME file (a glob
    plus its own literal path, e.g. `["*.json", "cfg.json"]`) — the file list must be de-duped
    before it's joined into the problem message, or the same filename is named twice."""
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "cfg.json").write_text('{"other_key": 1}\n')
        dec = _inv(id="INV-DUP", protected_paths=["*.json", "cfg.json"],
                   protected_params=[{"name": "max_iterations", "op": "le", "value": 60}])
        problems = m.validate(_reg(dec), root)
        assert problems == [
            "INV-DUP: param 'max_iterations' never appears in assignment shape in "
            "cfg.json — it can never fire"
        ]


# --- #622: `init` is the engine step that refuses to write a registry in an unadopted repo -------

def _init(root, *extra):
    """The documented gesture, verbatim from skills/sigma-decide/SKILL.md: decision_gate.py init <root>."""
    return subprocess.run([sys.executable, str(G), "init", str(root), *extra],
                          capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)


def test_init_refuses_without_config_json_names_sigma_init_and_writes_nothing(tmp_path):
    r = _init(tmp_path)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "/sigma-init" in r.stderr
    assert not (tmp_path / ".sdlc" / "decisions.json").exists()
    assert not (tmp_path / ".sdlc").exists()


def test_init_writes_a_skeleton_in_an_adopted_repo_with_no_registry(tmp_path):
    (tmp_path / ".sdlc").mkdir(); (tmp_path / ".sdlc" / "config.json").write_text("{}")
    r = _init(tmp_path)
    assert r.returncode == 0, r.stderr
    reg = tmp_path / ".sdlc" / "decisions.json"
    assert reg.is_file(), "init wrote no registry"
    assert json.loads(reg.read_text()) == {"version": 1, "decisions": []}
    assert str(reg.resolve()) in r.stdout


def test_init_never_overwrites_an_existing_registry(tmp_path):
    (tmp_path / ".sdlc").mkdir(); (tmp_path / ".sdlc" / "config.json").write_text("{}")
    reg = tmp_path / ".sdlc" / "decisions.json"; reg.write_text('{"keep": 1}')
    r = _init(tmp_path)
    assert r.returncode == 1 and reg.read_text() == '{"keep": 1}'


def test_init_from_a_subdirectory_lands_in_the_adopted_ancestor(tmp_path):
    (tmp_path / ".sdlc").mkdir(); (tmp_path / ".sdlc" / "config.json").write_text("{}")
    sub = tmp_path / "pkg" / "deep"; sub.mkdir(parents=True)
    r = _init(sub)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / ".sdlc" / "decisions.json").is_file()
    assert str((tmp_path / ".sdlc" / "decisions.json").resolve()) in r.stdout


def test_validate_notes_that_the_hook_is_inert_in_an_unadopted_repo(tmp_path):
    (tmp_path / ".sdlc").mkdir(); (tmp_path / ".sdlc" / "decisions.json").write_text(json.dumps(_reg(_inv())))
    r = subprocess.run([sys.executable, str(G), "validate", str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    lines = r.stdout.strip().splitlines()
    assert lines[0].startswith("decision registry:") and "[NOTE]" in lines[-1] and "/sigma-init" in lines[-1]

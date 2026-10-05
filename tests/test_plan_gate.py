"""Tests for the OPT-IN hard plan-gate (hooks/plan_gate.sh). Default off = allows
everything silently; on = denies source edits without a fresh .sdlc/plans/*.md."""
import json, os, pathlib, re, subprocess, time

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "plan_gate.sh"
COMPLETION_HOOK = HOOK.parent / "completion_gate.sh"


def _run(project_dir, file_path, key="file_path"):
    """`key` selects which spelling of the path the payload carries (#553): Edit/Write/MultiEdit
    send `file_path`, NotebookEdit sends `notebook_path`, and `filePath` is the camelCase variant
    decision_gate.py already tolerates. All three have to reach the same verdict."""
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"tool_input": {key: file_path}}),
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    # Deny is the ONLY output this script may print (module docstring): a bash diagnostic reaching
    # stderr means an expansion died, and under `set -u` that silently changes the verdict. Same
    # pin the sibling Stop gate keeps (test_completion_gate.py's non-numeric-freshness case).
    assert proc.stderr == "", proc.stderr
    return proc.stdout.strip()


def _project(tmp_path, enabled=True, fresh_hours=24, managed=None):
    """`managed` (#2138) is the org file `.sdlc/managed-settings.json`: a dict is written as JSON,
    a raw str byte for byte (so a test can hand the hook invalid JSON)."""
    base = tmp_path / ".sdlc"
    base.mkdir()
    base.joinpath("config.json").write_text(json.dumps(
        {"gates": {"hard_plan_gate": {"enabled": enabled,
                                      "plan_freshness_hours": fresh_hours}}}))
    if managed is not None:
        base.joinpath("managed-settings.json").write_text(
            managed if isinstance(managed, str) else json.dumps(managed))
    return tmp_path


def _deny_reason(out):
    return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_default_off_allows_everything(tmp_path):
    # no .sdlc at all → silent allow
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""
    # .sdlc present but flag off → silent allow
    _project(tmp_path, enabled=False)
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_enabled_denies_source_edit_without_plan(tmp_path):
    _project(tmp_path)
    out = _run(tmp_path, str(tmp_path / "app.py"))
    assert "deny" in out and "no fresh plan" in _deny_reason(out)


def test_enabled_allows_docs_config_and_sdlc_layer(tmp_path):
    _project(tmp_path)
    for path in ("README.md", "config.json", "notes.txt",
                 str(tmp_path / ".sdlc" / "goals" / "g.md"),
                 str(tmp_path / "docs" / "spec.md")):
        assert _run(tmp_path, path) == ""


def test_fresh_plan_unblocks_source_edits(tmp_path):
    _project(tmp_path)
    plans = tmp_path / ".sdlc" / "plans"
    plans.mkdir()
    (plans / "0001-plan.md").write_text("# plan")
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_stale_plan_still_denies(tmp_path):
    _project(tmp_path, fresh_hours=1)
    plans = tmp_path / ".sdlc" / "plans"
    plans.mkdir()
    plan = plans / "0001-plan.md"
    plan.write_text("# plan")
    two_hours_ago = time.time() - 7200
    os.utime(plan, (two_hours_ago, two_hours_ago))
    out = _run(tmp_path, str(tmp_path / "app.py"))
    assert "deny" in out


def test_override_sentinel_allows(tmp_path):
    _project(tmp_path)
    (tmp_path / ".sdlc" / ".allow-direct-edits").write_text("")
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_malformed_stdin_fails_open(tmp_path):
    _project(tmp_path)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    proc = subprocess.run(["bash", str(HOOK)], input="garbage{{{",
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_deny_output_is_valid_hook_json(tmp_path):
    _project(tmp_path)
    out = _run(tmp_path, str(tmp_path / "core.go"))
    payload = json.loads(out)["hookSpecificOutput"]
    assert payload["hookEventName"] == "PreToolUse"
    assert payload["permissionDecision"] == "deny"


# --- #536: a bad freshness value must never LOCK THE GATE ON ------------------------------------
# Both cases below are silent lockouts on the unfixed script: a fresh plan is sitting there and the
# edit is denied anyway. They are distinct failures, and the second is why the numeric case guard
# is load-bearing rather than redundant with the inner try.

def test_non_numeric_freshness_does_not_lock_out_a_fresh_plan(tmp_path):
    # The mode line was printed BEFORE int() could raise inside the same try, so an unparseable
    # window emitted three lines and the by-position read handed "off" to $(( )). Under `set -u`
    # the arithmetic died, the find never ran, and every source edit was denied -- with an
    # unbound-variable diagnostic leaking to stderr (caught by _run's own pin).
    _project(tmp_path, fresh_hours="24h")
    plans = tmp_path / ".sdlc" / "plans"; plans.mkdir()
    (plans / "0001-plan.md").write_text("# plan")
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_negative_freshness_does_not_lock_out_a_fresh_plan(tmp_path):
    # The SECOND lockout, and a FULLY SILENT one: int(-5) parses, so the inner try never fires and
    # nothing reaches stderr. Only the numeric case guard rejects the leading '-'. Without it,
    # `-$((-5 * 60))` expands to `--300`, find refuses the argument (its stderr is suppressed),
    # and a fresh plan is denied with no diagnostic anywhere. This is what isolates the guard.
    _project(tmp_path, fresh_hours=-5)
    plans = tmp_path / ".sdlc" / "plans"; plans.mkdir()
    (plans / "0001-plan.md").write_text("# plan")
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_non_numeric_freshness_deny_reason_names_a_real_window(tmp_path):
    # Denying is correct here (the plan IS stale) -- what must never ship is the reason telling an
    # engineer the freshness window is "offh". Epoch mtime is the sibling's own staleness recipe:
    # with an unparseable value the post-fix window is the 24h default, which two-hours-ago would
    # still satisfy.
    _project(tmp_path, fresh_hours="24h")
    plans = tmp_path / ".sdlc" / "plans"; plans.mkdir()
    old = plans / "0001-plan.md"; old.write_text("# plan")
    os.utime(old, (0, 0))
    reason = _deny_reason(_run(tmp_path, str(tmp_path / "app.py")))
    assert "offh" not in reason and "24h" in reason


# --- #536: the source-extension list had drifted from the sibling gate --------------------------

def test_scala_and_elixir_source_edits_are_gated(tmp_path):
    # An unrecognized extension exits 0 (silent allow), so a missing entry does not fail loudly --
    # it makes the edit gate inert for that language while the Stop gate still fires on it.
    _project(tmp_path)
    for name in ("Main.scala", "app.ex", "script.exs"):
        assert "deny" in _run(tmp_path, str(tmp_path / name)), name


def _source_extensions(text, what):
    # The source-extension `case` alternation. Anchored on the literal `*.py|`, NOT a bare `*.`
    # scan: plan_gate.sh has an earlier docs-exclusion case block that a loose pattern also hits.
    m = re.search(r"(\*\.py\|[^)]*)\)", text)
    assert m, "could not locate the source-extension case pattern in %s" % what
    return {p.strip().removeprefix("*.") for p in m.group(1).split("|")}


def test_source_extension_lists_stay_in_sync_with_the_completion_gate():
    """The two gates deliberately keep inlined copies of this list -- a hook cannot reliably source
    another file across the plugin layout, and a failed `source` under `set -u` would break the
    fail-open promise both gates document (the same failure class as the freshness lockout above).
    So the shared constant is enforced by test, the way scrub.py/research_capture.py and
    risk-detect/alignment-collect already are. EXACT equality, not a superset: a superset assert
    would let the Stop gate quietly LOSE an extension while this one kept it."""
    plan_exts = _source_extensions(HOOK.read_text(encoding="utf-8"), "plan_gate.sh")
    comp_exts = _source_extensions(COMPLETION_HOOK.read_text(encoding="utf-8"), "completion_gate.sh")
    assert plan_exts == comp_exts, "plan_gate.sh and completion_gate.sh source extensions drifted"
    # and the shared set really did grow to the sibling's, rather than the two meeting in the middle
    # ("ipynb" added by #553, which had to land in BOTH copies to keep this equality true)
    for ext in ("scala", "ex", "exs", "ipynb"):
        assert ext in plan_exts and ext in comp_exts


# --- #553: hooks.json matches NotebookEdit, but the script only read tool_input.file_path --------
# A NotebookEdit payload carries `notebook_path`. Reading only `file_path` left it empty, and the
# `[ -n "$file_path" ] || exit 0` guard then fail-opened before any gating logic ran — so the
# NotebookEdit entry in the matcher (hooks.json) was dead wiring: a repo that turned the hard plan
# gate ON was still never gated on a notebook edit, and nothing said so. decision_gate.py already
# reads all three spellings at both of its extraction points; this brings the gate in line.
#
# Two halves, tested apart on purpose. The READ fix is what makes a notebook payload reach the
# pipeline at all; the EXTENSION addition is what makes `.ipynb` gated once it gets there. Either
# one alone leaves the matcher's promise unmet, so each has its own test.

def test_a_notebook_edit_payload_reaches_the_same_verdict_as_an_edit():
    """The acceptance criterion, isolated from the extension question by using a path that was
    already gated: the same file denied through `file_path` must be denied through `notebook_path`.
    Pre-fix this returned "" — a silent allow — while the Edit form denied."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        _project(root)
        edit = _run(root, str(root / "app.py"))
        notebook = _run(root, str(root / "app.py"), key="notebook_path")
        assert "deny" in edit                      # the reference verdict
        assert notebook == edit                    # same payload, different spelling, same answer


def test_the_camel_case_file_path_spelling_is_read_too():
    """`filePath` rides the same fallback chain decision_gate.py uses, so the two hooks cannot
    disagree about which spellings of one field they understand."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        _project(root)
        assert _run(root, str(root / "app.py"), key="filePath") == _run(root, str(root / "app.py"))


def test_notebooks_are_gated_source():
    """The other half: reading the path is useless while `.ipynb` is not a recognized source
    extension — an unrecognized extension exits 0, so the gate stayed inert for the very file type
    the matcher entry exists for. A notebook is code an engineer edits, so it is source."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        _project(root)
        assert "deny" in _run(root, str(root / "analysis.ipynb"), key="notebook_path")
        assert "deny" in _run(root, str(root / "analysis.ipynb"))   # and via a plain Edit too


# --- #554: two pins the SIBLING's tests already claim this gate has ------------------------------
# test_completion_gate.py carries both, and one of them names this file in its own comment
# ("mirrors plan_gate's strict check") — so the Stop gate's suite was asserting a property of the
# edit gate that the edit gate's own suite never checked. Both behaviours below are already correct
# in hooks/plan_gate.sh; what was missing is the pin, so a future edit would be caught for one gate
# and sail through for the other. Coverage gap, not a bug: these are GREEN on arrival by design, and
# each is proven non-vacuous by breaking the thing it pins (see the PR body).

def test_enabled_reads_generously_like_f17s_verify_enforce(tmp_path):
    """#416: `hard_plan_gate.enabled` used to require strict `is True` -- the SAME unsafe-direction
    fragility F17/#342 fixed for `verify.enforce` (loop.py/doctor.py's `_enforce_enabled`): a config
    typo (`enabled: 1` or `enabled: "true"`, both plainly meant as true) silently left this DENY
    gate OFF with no warning. Mirrors `_enforce_enabled`'s own truth table
    (tests/test_loop.py::test_enforce_enabled_reads_truthy_non_bool_values_generously) end to end
    through the real hook subprocess: a real bool passes through, a string counts as off only when
    it spells out false/no/off/empty, and everything else (including `1`, `"true"`, `"yes"`) is on."""
    on_values = (True, 1, "true", "True", "1", "yes")
    for i, val in enumerate(on_values):
        d = tmp_path / ("on%d" % i)
        d.mkdir()
        _project(d, enabled=val)
        out = _run(d, str(d / "app.py"))
        assert "deny" in out, "enabled:%r must enable the gate (F17 direction)" % (val,)

    off_values = (False, 0, "false", "False", "0", "no", "off", "")
    for i, val in enumerate(off_values):
        d = tmp_path / ("off%d" % i)
        d.mkdir()
        _project(d, enabled=val)
        assert _run(d, str(d / "app.py")) == "", "enabled:%r must not enable the gate" % (val,)


def test_wired_into_hooks_json():
    """The gate is only real if the plugin actually invokes it: silently dropping the PreToolUse
    entry would leave every test above passing against a hook nothing runs. Mirrors the sibling's
    Stop-hook check. The matcher itself is pinned too, including NotebookEdit — #553 made that entry
    load-bearing rather than decorative, so losing it now would silently un-gate notebook edits."""
    hooks = json.loads((HOOK.parent / "hooks.json").read_text(encoding="utf-8"))
    pre = hooks["hooks"].get("PreToolUse", [])
    entry = next((e for e in pre if "plan_gate.sh" in json.dumps(e)), None)
    assert entry is not None, "PreToolUse hook not wired"
    matcher = entry.get("matcher") or ""
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        assert tool in matcher, "%s missing from the plan_gate matcher (%r)" % (tool, matcher)


# --- #602: the digits guard bounds SHAPE, not MAGNITUDE ------------------------------------------
# `$(( fresh_hours * 60 ))` is 64-bit and wraps above (2^63-1)/60 ~= 1.537e17. #536's `''|*[!0-9]*`
# guard rejects a value that is not all digits; it has nothing to say about one that is all digits
# and enormous. Both wrap directions are reachable from a hand-typed config, and both are silent:
#   * just over the boundary the product goes NEGATIVE, `find -mmin --922...` matches nothing, and
#     the gate denies EVERY edit while its own deny reason quotes the huge window back at you —
#     the same lockout class #536 existed to fix, arriving by magnitude instead of by shape;
#   * far above it the product wraps back POSITIVE and huge, so the gate allows everything and is
#     effectively off while still reporting itself ON.
# The clamp lives in the gate's python block, not the shell: `[ "$h" -gt 8760 ]` ERRORS on a
# 20-digit value ("integer expression expected") and falls through to "not greater", so a
# shell-side clamp would skip precisely the values it exists to catch.

_WRAP_BOUNDARY = (2 ** 63 - 1) // 60          # 153722867280912930


def _plan_of_age(project, minutes):
    plans = project / ".sdlc" / "plans"
    plans.mkdir(exist_ok=True)
    p = plans / "0001-plan.md"
    p.write_text("# plan")
    stamp = time.time() - minutes * 60
    os.utime(p, (stamp, stamp))
    return p


def test_a_window_just_past_the_wrap_boundary_does_not_deny_a_fresh_plan(tmp_path):
    """Wrap NEGATIVE. A plan written one minute ago is as fresh as a plan gets; denying it is the
    lockout, and the reason line quoting a 17-billion-year window makes it unreadable as a bug."""
    _project(tmp_path, fresh_hours=_WRAP_BOUNDARY + 1)
    _plan_of_age(tmp_path, 1)
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_a_window_that_wraps_positive_does_not_silently_allow_a_stale_plan(tmp_path):
    """Wrap POSITIVE. The gate is switched ON and reports itself ON, but every edit sails through
    because the minute count came back astronomically large. Epoch mtime, so the plan is stale under
    the clamped window (a year) as well as under any window a human would type — the sibling Stop
    gate test uses the same recipe."""
    _project(tmp_path, fresh_hours=99999999999999999999)
    plan = _plan_of_age(tmp_path, 1)
    os.utime(plan, (0, 0))
    assert "deny" in _run(tmp_path, str(tmp_path / "app.py"))


def test_the_wrap_boundary_itself_still_behaves(tmp_path):
    """The largest value that does NOT wrap keeps working — the clamp must bound the arithmetic,
    not move the goalposts for values that were always safe."""
    _project(tmp_path, fresh_hours=_WRAP_BOUNDARY)
    _plan_of_age(tmp_path, 1)
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


# --------------------------------------------------------------- #2116: the block-shaped #416 trap

def _scalar_project(tmp_path, value):
    base = tmp_path / ".sdlc"
    base.mkdir(parents=True)
    base.joinpath("config.json").write_text(json.dumps({"gates": {"hard_plan_gate": value}}))
    return tmp_path


def test_a_scalar_gate_block_is_read_for_its_plain_intent(tmp_path):
    """`ledger.LOCKABLE_KEYS` names the BLOCK path `gates.hard_plan_gate`, so
    `{"gates": {"hard_plan_gate": true}}` is a legal Org policy -- and `work.py`'s host-agnostic
    enforcement point reads it as ON. This hook used to read it as OFF: `True.get("enabled")` raised
    and the whole read landed in `except: mode = "off"`. One key, opposite answers, on the same host
    -- which is the exact defect #2116 exists to remove, so the fix has to reach here too.

    This is the BLOCK-shaped sibling of #416's value-shaped trap, and it fails in the same unsafe
    direction: a hard DENY gate silently off."""
    for value in (True, 1, "true", "yes"):
        proj = _scalar_project(tmp_path / ("on%r" % (value,)), value)
        assert "deny" in _run(proj, str(proj / "app.py")), value


def test_a_scalar_gate_block_that_spells_out_off_stays_off(tmp_path):
    for value in (False, 0, "false", "off", "no", ""):
        proj = _scalar_project(tmp_path / ("off%r" % (value,)), value)
        assert _run(proj, str(proj / "app.py")) == "", value


def test_a_scalar_gates_PARENT_never_crashes_the_read(tmp_path):
    """One level up, and the spelling every reader in the tree used: `(cfg.get("gates") or {})`
    raises AttributeError on `{"gates": true}`. Fail-open swallowed it, so the gate was off with
    nothing to say why."""
    for value in (True, "on", [], 3):
        base = (tmp_path / ("p%r" % (value,))) / ".sdlc"
        base.mkdir(parents=True)
        base.joinpath("config.json").write_text(json.dumps({"gates": value}))
        assert _run(base.parent, str(base.parent / "app.py")) == "", value


def test_source_extensions_match_works_own_copy():
    """`work.py` carries a THIRD copy of this list for #2116's host-agnostic enforcement point, and
    it must be the same list: a `pr()` gate that considered `.rs` source while the hook did not
    would refuse pull requests the hook waves through.

    The shell side is parsed with the existing `_source_extensions`; the Python side is IMPORTED,
    not parsed. `_source_extensions` is a shell `case`-alternation parser (`\\*\\.py\\|...`) and matches
    nothing in a Python tuple -- so "teach it both syntaxes" would mean growing a second parser to
    check a constant that can simply be read."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "work", HOOK.parent.parent / "skills" / "sigma-loop" / "scripts" / "work.py")
    work = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(work)
    shell = _source_extensions(HOOK.read_text(encoding="utf-8"), "plan_gate.sh")
    assert set(work.SOURCE_EXTENSIONS) == shell


_DIFFERENTIAL_PATHS = [
    "app.py", "a.PY", "a.Py", "src/a.PY", "x.C", "x.H", "x.EX", "x.IPYNB", "weird.name.PY",
    "foo/bar.SH", "README.md", "readme.MD", "docs/guide.md", "docs/tools/build.py",
    "deep/docs/tools/build.py", ".sdlc/config.json", "a/.sdlc/x.py", "Makefile", ".gitignore",
    "src/app.py", "src/app.ts", "notes.txt", "data.json", "docs", "x.pyc", "x.py.bak", "a.rs",
]


def test_the_python_gate_and_this_hook_agree_on_every_path(tmp_path):
    """DIFFERENTIAL, against the REAL hook — the pin the set-comparison above structurally cannot be.

    `work.py`'s `pr()` gate and this hook must answer the same question the same way, or one
    org-lockable key gives opposite answers at its two enforcement points on the same host. Equal
    extension SETS do not establish that: a `.lower()` on one side alone made `a.PY`, `x.C`, `x.H`
    and six other paths allowed by the hook and refused by `pr()` while the set test stayed green.

    A DISAGREEMENT IN EITHER DIRECTION FAILS. `pr()` narrowing is safe in principle, but a
    narrowing nobody wrote down is drift, and this is the only place that would notice it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "work", HOOK.parent.parent / "skills" / "sigma-loop" / "scripts" / "work.py")
    work = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(work)

    disagreements = []
    for i, path in enumerate(_DIFFERENTIAL_PATHS):
        proj = tmp_path / str(i)                      # pytest-managed: `mkdtemp` leaked one per path
        (proj / ".sdlc").mkdir(parents=True)
        (proj / ".sdlc" / "config.json").write_text(json.dumps(
            {"gates": {"hard_plan_gate": {"enabled": True}}}))
        hook_gates = "deny" in _run(proj, path)          # no .sdlc/plans/ -> ON means deny
        py_gates = work._branch_touches_source(
            {"worktree": ".", "remote": "origin", "base": "main"},
            lambda cwd, argv: path)
        if hook_gates != py_gates:
            disagreements.append((path, "hook=%s pr()=%s" % (hook_gates, py_gates)))
    assert not disagreements, disagreements


# --- #2138: an org-locked `gates.hard_plan_gate` is not bypassed by `.allow-direct-edits` ---------
# `pr()` is the enforcement point; this hook is the accelerator that refuses the EDIT earlier. The
# two must not give opposite answers about the sentinel on the same host, so the hook now reads the
# org file too and honours the sentinel only when the key is NOT org-locked ON. The sentinel in every
# test below is the DOCUMENTED gesture -- an EMPTY file made with `touch` -- never `write_text`.

_LOCK_ON = {"version": 1, "status": "ok", "locked": {"gates.hard_plan_gate": {"enabled": True}}}


def _touch_sentinel(project):
    subprocess.run(["touch", str(project / ".sdlc" / ".allow-direct-edits")], check=True)


def test_org_lock_hook_ignores_the_sentinel_when_the_key_is_org_locked_on(tmp_path):
    """2a. THE HOLE, at the edit. Lock ON + sentinel + no plan: deny, naming the lock and never
    offering `touch` (the file is already there; offering it would be a false remedy)."""
    _project(tmp_path, managed=_LOCK_ON)
    _touch_sentinel(tmp_path)
    out = _run(tmp_path, str(tmp_path / "app.py"))
    assert "deny" in out, out
    reason = _deny_reason(out)
    assert "org-locked" in reason and "no fresh plan" in reason
    assert "touch" not in reason


def test_org_lock_hook_honours_a_touched_sentinel_without_an_org_file(tmp_path):
    """2b (pin). No org file -- every install in the wild -- keeps today's escape hatch, exercised
    through the literal `touch` the docs prescribe."""
    _project(tmp_path)
    _touch_sentinel(tmp_path)
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_org_lock_hook_honours_the_sentinel_when_the_org_file_does_not_lock_this_key(tmp_path):
    """2c (pin). An ok org file that locks OTHER keys leaves this gate local, sentinel included."""
    _project(tmp_path, managed={"version": 1, "status": "ok",
                                "locked": {"work.require_review": "approval"}})
    _touch_sentinel(tmp_path)
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_org_lock_hook_does_not_honour_the_sentinel_on_an_invalid_org_file(tmp_path):
    """2d. The org file is PRESENT but unreadable: `pr()` PARKs there (`gated_check` refuses), so the
    hook must not wave the same edit through on a `touch`. Deny -- the local mode is still ON, a
    fresh plan or a non-source path still allows -- and the reason must not offer `touch`."""
    _project(tmp_path, managed="{invalid json")
    _touch_sentinel(tmp_path)
    out = _run(tmp_path, str(tmp_path / "app.py"))
    assert "deny" in out, out
    reason = _deny_reason(out)
    assert "unreadable or invalid" in reason and "no fresh plan" in reason
    assert "touch" not in reason


def test_org_lock_hook_honours_the_sentinel_when_the_key_is_locked_off(tmp_path):
    """2e (pin). Locked OFF with local ON: the hook's MODE stays the local value (as today) and the
    sentinel is still honoured -- there is no lock ON for the sentinel to defeat, and `pr()` reads
    the same lock as OFF, so nothing there gates either."""
    _project(tmp_path, managed={"version": 1, "status": "ok",
                                "locked": {"gates.hard_plan_gate": {"enabled": False}}})
    _touch_sentinel(tmp_path)
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


_ORG_FILE_STATES = [
    ("absent", None),
    ("ok, locked ON", _LOCK_ON),
    ("ok, locked OFF", {"version": 1, "status": "ok",
                        "locked": {"gates.hard_plan_gate": {"enabled": False}}}),
    ("ok, locked scalar true", {"version": 1, "status": "ok",
                                "locked": {"gates.hard_plan_gate": True}}),
    ("ok, key absent", {"version": 1, "status": "ok",
                        "locked": {"work.require_review": "approval"}}),
    ("ok, locked is a list", {"version": 1, "status": "ok", "locked": []}),
    ("ok, locked is a number", {"version": 1, "status": "ok", "locked": 5}),
    ("access-revoked", {"version": 1, "status": "access-revoked"}),
    ("version 2", {"version": 2, "status": "ok", "locked": {"gates.hard_plan_gate": True}}),
    ("top-level list", "[]"),
    ("invalid JSON", "{invalid json"),
]


def _hook_class(out):
    """The hook's verdict on a sentinel-bearing, plan-less source edit, as the three classes the
    org file can put it in. Anything else is a shape this test does not know and must fail on."""
    if out == "":
        return "unlocked"
    reason = _deny_reason(out)
    if "org-locked" in reason:
        return "locked"
    if "sentinel is not honoured" in reason:
        return "unverifiable"
    raise AssertionError("unclassifiable hook output: %r" % (out,))


def test_org_lock_hook_and_gated_check_classify_alike(tmp_path):
    """2f. DIFFERENTIAL, against the REAL hook end to end -- no seam. The hook inlines a second
    parser of the org file (hooks stay path-independent, so it cannot import
    `managed_settings.py`), and drift between the two parsers is caught HERE, not by inspection.
    For every file state: the expected class comes from `managed_settings.gated_check` --
    `unverifiable` when it refuses, `locked` when the key is locked to an ON value, else `unlocked`
    -- and the hook's class comes from its verdict on a sentinel-bearing, plan-less `app.py` edit.
    Compares CLASSIFICATION only, so the dossier's Finding 3 (an adopted checkout's unlocked key
    reading local ON as OFF in `pr()`) cannot interfere.

    The invalid-JSON case is the CONTROL for the hook reading the org file inside its OWN `try`: a
    parse error there must land in `unverifiable`, not turn the local MODE line off (which would read
    as `unlocked` -- a silent allow -- and disagree with `gated_check`'s refusal). The two
    non-dict `locked` cases are the control for the `isinstance(locked, dict)` guard: `gated_check`
    reads them as ok-with-no-locks, so the hook must too."""
    import importlib.util
    scripts = HOOK.parent.parent / "skills" / "sigma-loop" / "scripts"

    def load(name):
        spec = importlib.util.spec_from_file_location(name, scripts / (name + ".py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    work, managed_settings = load("work"), load("managed_settings")
    disagreements = []
    for i, (label, managed) in enumerate(_ORG_FILE_STATES):
        proj = tmp_path / str(i)
        proj.mkdir()
        _project(proj, managed=managed)
        _touch_sentinel(proj)
        cfg = json.loads((proj / ".sdlc" / "config.json").read_text())
        cfg["managed_settings"] = {"project_id": "proj-1"}
        o = managed_settings.gated_check(str(proj / ".sdlc"), cfg, "gates.hard_plan_gate")
        if not o["allowed"]:
            expected = "unverifiable"
        elif o["locked"] and work.hard_plan_gate_on(o["value"]):
            expected = "locked"
        else:
            expected = "unlocked"
        got = _hook_class(_run(proj, str(proj / "app.py")))
        if got != expected:
            disagreements.append((label, "hook=%s gated_check=%s" % (got, expected)))
    assert not disagreements, disagreements

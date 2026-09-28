"""session_start.sh (Slice 5): the OPT-IN SessionStart hook. When enabled, it injects a short SDLC
policy brief (+ a doctor-lite install self-check) as additionalContext so Sigma's conventions are in
context before the first prompt. Guards: OFF by default (silent unless .sdlc/ AND opted in), valid JSON
output, the self-check warning, and fail-open."""
import json, os, subprocess, pathlib, time, shutil

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "session_start.sh"


def _run(project_dir, payload="{}", **extra_env):
    # SIGMA_RUN_ID is SCRUBBED from the inherited environment (issue #1560's headless skip:
    # the hook suppresses the wizard entirely when it is set). Running this suite from inside a
    # supervise_daemon.py-launched session would otherwise switch the wizard off and let every
    # wizard-asserting test below pass vacuously. Same scrub tests/test_supervise.py already does.
    env = {k: v for k, v in os.environ.items() if k != "SIGMA_RUN_ID"}
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    env.update(extra_env)
    p = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _enabled(tmp_path, north_star=True):
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"session_start":{"enabled":true}}')
    if north_star:
        (tmp_path / ".sdlc" / "context").mkdir()
        (tmp_path / ".sdlc" / "context" / "north-star.md").write_text("# north star\n")
    return tmp_path


def _context(out):
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def _ledger(root, *, enabled=True, interval=900, heartbeat_age=None, pid=False):
    """Writes `<root>/.sdlc/config.json` with a `ledger` block, and optionally fakes
    `.sdlc/state/watch.heartbeat` (mtime set `heartbeat_age` seconds in the past via os.utime --
    same technique tests/test_doctor.py's own `_touch` helper uses for _ledger_watcher_state's own
    fixtures, tests/test_doctor.py:3994 etc.) and/or `.sdlc/state/watch.pid`, mirroring what
    watch.sh itself writes. Creates `root` and `.sdlc/state` as needed, so callers can pass
    tmp_path itself or a fresh subdirectory of it (two independent fixtures in one test)."""
    sdlc = pathlib.Path(root) / ".sdlc"
    sdlc.mkdir(parents=True, exist_ok=True)
    cfg = {"ledger": {"enabled": enabled, "watch": {"interval_seconds": interval}}}
    (sdlc / "config.json").write_text(json.dumps(cfg))
    if heartbeat_age is not None or pid:
        state_dir = sdlc / "state"
        state_dir.mkdir(exist_ok=True)
        if heartbeat_age is not None:
            hb = state_dir / "watch.heartbeat"
            hb.write_text("")
            mtime = time.time() - heartbeat_age
            os.utime(hb, (mtime, mtime))
        if pid:
            (state_dir / "watch.pid").write_text("12345")
    return root


def _path_without_gh(tmp_path):
    """A PATH that still resolves `bash` (the caller's own `subprocess.run(["bash", HOOK], ...)`
    needs to find it there too, since `env=` replaces rather than extends PATH), `python3` and
    `dirname` (both required by the hook itself), but never resolves `gh` -- built as a FRESH
    directory holding only symlinks to those three real binaries (found dynamically via
    `shutil.which`, not a hardcoded install path), rather than assuming any real OS directory
    (`/usr/bin`, `/bin`, ...) happens to lack `gh`. That assumption broke for real (#2742/#2724,
    sigma's first Linux CI run): GitHub-hosted `ubuntu-latest` runners ship `gh` preinstalled at
    `/usr/bin/gh`, so the old PATH (`python3_dir:/usr/bin:/bin`) resolved it there every time, and
    the self-check below caught it exactly as its docstring promised -- loud collection-time
    failure, not a silently-vacuous test. Building an isolated directory makes the exclusion true
    by CONSTRUCTION instead of by which OS directories the test trusts."""
    bindir = tmp_path / "path-without-gh"
    bindir.mkdir()
    for name in ("bash", "python3", "dirname"):
        real = shutil.which(name)
        assert real is not None, "this host has no %r on PATH at all -- the hook needs it" % name
        (bindir / name).symlink_to(real)
    path = str(bindir)
    assert shutil.which("gh", path=path) is None, (
        "the isolated PATH still resolves `gh` -- impossible unless `bindir` itself somehow "
        "contains a `gh` binary alongside the python3/dirname symlinks just created")
    return path


def _ledger_and_broken_gh_auth(root, *, heartbeat_age=3600):
    """The ONE fixture in this file where the wizard (tier 1) genuinely NEEDS to fire *and* the
    ledger watcher is genuinely enabled+stale, at the same time -- unlike a bare `.sdlc/`-less
    fixture, where tier 1 needing to fire (no config at all) and tier 3 having a ledger to check
    (a config with a `ledger` block) are mutually exclusive by construction.

    `discovery: {"source": "github"}` turns on doctor.py's `gh auth` check (see
    skills/agrim-doctor/scripts/doctor.py:1335: "the `gh` calls under discovery.source == 'github'
    are NOT gated by [cheap_only]... they only fire because that repo's own config asked for
    github discovery"). Paired with `_path_without_gh()` at the caller's `_run(..., PATH=...)`,
    `gh auth status` cannot resolve `gh` at all, so `_real_run` catches the resulting
    FileNotFoundError and returns a falsy `_RawFailure` (doctor.py:17-29) -- `auth_ok` is False
    deterministically, independent of whether this machine's own `gh` happens to be logged in.
    "gh auth" is in setup_wizard._MODES (mode "human_command"), so this alone makes
    wizard_status().needs_wizard True regardless of the ledger state; "project layer" stays ok
    (config.json exists), so the wizard's ONLY failing step is "gh auth" -- nothing in its
    prescribed text mentions the ledger or /agrim-doctor (verified by reading _MODES directly),
    so it cannot accidentally satisfy this test's own negative assertions."""
    _ledger(root, heartbeat_age=heartbeat_age)
    cfg_path = pathlib.Path(root) / ".sdlc" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["discovery"] = {"source": "github"}
    cfg_path.write_text(json.dumps(cfg))
    return root


# --- OFF by default ---

# Changed by issue #1560: silence here was the ORIGINAL BUG (nothing runs at plugin-install
# time, and nothing ever told the user), not a behavior worth preserving. See
# docs/superpowers/plans/2026-08-23-guided-setup-wizard.md.

def test_no_config_triggers_the_setup_wizard(tmp_path):
    ctx = _context(_run(tmp_path))
    assert "agrim-wizard" in ctx
    assert "project layer" in ctx


def test_a_headless_supervised_session_is_silent_again(tmp_path):
    """Issue #1560 final review, finding I4: a `claude -p /agrim-loop` worker (supervise_daemon.py exports
    SIGMA_RUN_ID before launching it) has nobody to answer the wizard's yes/no questions, so
    the wizard must not fire there at all. The line above is the control -- the identical fixture
    with the variable absent DOES produce wizard context."""
    assert "agrim-wizard" in _context(_run(tmp_path))
    assert _run(tmp_path, SIGMA_RUN_ID="supervise-1-2-3") == ""


def test_sdlc_present_but_not_enabled_is_silent(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text("{}")
    assert _run(tmp_path) == ""


def test_enabled_must_be_strict_true(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"session_start":{"enabled":"true"}}')
    assert _run(tmp_path) == ""          # a string "true" must not enable


# --- enabled: injects valid JSON policy ---

def test_enabled_injects_session_start_context(tmp_path):
    out = _run(_enabled(tmp_path))
    d = json.loads(out)                                  # must be valid JSON
    assert d["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = d["hookSpecificOutput"]["additionalContext"]
    assert "/agrim-loop" in ctx and "north-star" in ctx and "reviewer is never the author" in ctx


def test_missing_north_star_adds_a_warning(tmp_path):
    ctx = _context(_run(_enabled(tmp_path, north_star=False)))
    assert "setup notes" in ctx and "/agrim-vision" in ctx   # doctor-lite self-check warns, never blocks


def test_present_north_star_no_warning(tmp_path):
    ctx = _context(_run(_enabled(tmp_path, north_star=True)))
    assert "setup notes" not in ctx


def test_output_is_valid_json_on_warning_path(tmp_path):
    # the warning prepends a paragraph break; the emitted JSON must still parse (control chars escaped)
    json.loads(_run(_enabled(tmp_path, north_star=False)))


# --- fail-open + wiring ---

def test_no_sdlc_dir_triggers_the_setup_wizard(tmp_path):
    # Kept as its own test, not merged with the one above, even though both exercise the
    # identical "no .sdlc/ at all" condition -- they existed as two separate tests before this
    # change, and silently deleting one during this revision would trade one coverage gap this
    # plan is fixing for a different, smaller one nobody asked for.
    ctx = _context(_run(tmp_path))
    assert "agrim-wizard" in ctx


def test_wired_into_hooks_json():
    hooks = json.loads((HOOK.parent / "hooks.json").read_text())
    ss = hooks["hooks"].get("SessionStart", [])
    assert any("session_start.sh" in json.dumps(h) for h in ss), "SessionStart hook not wired"


# --- Tier 3: proactive ledger-watcher staleness check (issue #2444) -----------------------------

def test_stale_watcher_warns(tmp_path):
    # RED today: today's hook has no tier-3 code path, so output for this fixture is "" and
    # _context()'s json.loads("") raises -- this fixture asserts real new content.
    ctx = _context(_run(_ledger(tmp_path, heartbeat_age=3600)))  # 1h old; stale_after=2700s @ interval=900
    assert "looks stale" in ctx and "/agrim-doctor" in ctx


def test_dead_watcher_warns(tmp_path):
    # RED today, same reason as above.
    ctx = _context(_run(_ledger(tmp_path, pid=True)))            # pid file, no heartbeat at all
    assert "looks dead" in ctx and "/agrim-doctor" in ctx


def test_never_run_watcher_warns(tmp_path):
    # RED today, same reason as above. This is the state Q1 resolved to also warn on.
    ctx = _context(_run(_ledger(tmp_path)))                      # neither heartbeat nor pid file
    assert "has never started" in ctx and "/agrim-doctor" in ctx


def test_output_is_valid_json_on_watcher_warning_path(tmp_path):
    # RED today: _run() returns "" for this fixture, and json.loads("") raises.
    out = _run(_ledger(tmp_path, heartbeat_age=3600))
    json.loads(out)                                              # must parse even on the warning path


def test_fresh_watcher_is_silent(tmp_path):
    """Control (first line): the identical fixture with an old mtime warns -- and IS red today,
    proving this test exercises the age branch itself once it exists, not an absent code path.
    Second line is the actual assertion under test and is NOT red today (today's hook is already
    silent here by having no tier-3 code at all) -- it is a regression guard for after Task 2."""
    assert "looks stale" in _context(_run(_ledger(tmp_path / "stale", heartbeat_age=3600)))
    assert _run(_ledger(tmp_path / "fresh", heartbeat_age=60)) == ""


def test_ledger_disabled_is_silent(tmp_path):
    """Same control pattern: enabled=True with this heartbeat age warns (red today);
    enabled=False with the SAME stale heartbeat does not -- proving the gate, not an absent code
    path, is what's silent once tier 3 exists."""
    assert "looks stale" in _context(_run(_ledger(tmp_path / "on", heartbeat_age=3600)))
    assert _run(_ledger(tmp_path / "off", enabled=False, heartbeat_age=3600)) == ""


def test_ledger_block_wrong_shape_is_silent(tmp_path):
    """A malformed `ledger` block (a string, not a dict -- the exact shape-typo doctor.py's
    _block(), doctor.py:52-65, exists to defend against) must degrade to silent, not crash the
    hook. Not red today (no tier-3 code exists to crash) -- a defensive-degrade regression guard,
    matching BR-8's mirrored pattern."""
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"ledger": "not-a-dict"}')
    assert _run(tmp_path) == ""


def test_headless_session_skips_the_watcher_tier(tmp_path):
    """Mirrors test_a_headless_supervised_session_is_silent_again's own control pattern (same
    file, above): the identical stale-watcher fixture warns when NOT headless -- that first
    assertion is red today -- and is silent once SIGMA_RUN_ID is set, exactly like the wizard
    tier already is."""
    project = _ledger(tmp_path, heartbeat_age=3600)
    assert "looks stale" in _context(_run(project))
    assert _run(project, SIGMA_RUN_ID="supervise-1-2-3") == ""


def test_watcher_tier_does_not_double_fire_with_wizard(tmp_path):
    """`_ledger_and_broken_gh_auth()` makes BOTH conditions genuinely true at once: the wizard
    (tier 1) needs to fire (discovery.source=github + gh made unresolvable via PATH, so `gh auth`
    fails deterministically regardless of this machine's own real gh login state) AND the ledger
    watcher is enabled+stale (tier 3 would otherwise want to fire too). _context()'s own
    json.loads(out) would raise "Extra data" if a second JSON blob got concatenated after tier 1's,
    so parsing succeeding is itself part of the proof, not just the content checks below it.

    NOT RED TODAY: today's 2-tier hook already runs tier 1 unconditionally, so it fires here
    exactly as it does for the plain "no .sdlc/ at all" fixture used elsewhere in this file --
    there is no tier-3 code path yet for a mis-ordering bug to exist in. Per AGENTS.md ("run the
    control, or the check is decoration"), Task 3 below deliberately moves the finished tier-3
    block to BEFORE tier 1 in hooks/session_start.sh and confirms THIS test goes red for exactly
    that reason (tier 3 fires and exits first, so the wizard content this test asserts for never
    gets printed at all) before restoring the correct order -- that is what gives this assertion
    teeth instead of decoration."""
    project = _ledger_and_broken_gh_auth(tmp_path)
    ctx = _context(_run(project, PATH=_path_without_gh(tmp_path)))
    assert "agrim-wizard" in ctx
    assert "ledger watcher" not in ctx.lower() and "/agrim-doctor" not in ctx


# --- Tier 4: knowledge-graph auto-refresh has never built a graph (issue #2704) -----------------
# On the maintainer's machine `graphify-out/` was an empty directory for three weeks with
# `auto_refresh: true` and this hook said nothing. The tier calls `kg.py warn` (the sibling skill's
# CLI, the same once-a-day line `loop.py next` prints on every host) and wraps a non-empty answer as
# additionalContext. A stub builder is put on PATH so the wizard's own `graphify installed` step
# stays quiet and cannot shadow this tier -- one additionalContext per invocation.

def _kg_repo(root, *, auto_refresh=True, enabled=True, graph_age=None):
    """`<root>/.sdlc` with a knowledge_graph block, a one-note corpus, and optionally a graph at
    `<root>/stubgraph-out/graph.json` whose mtime is `graph_age` seconds in the past (None = no
    graph at all). Returns root."""
    sdlc = pathlib.Path(root) / ".sdlc"
    (sdlc / "knowledge" / "analysis").mkdir(parents=True, exist_ok=True)
    (sdlc / "knowledge" / "analysis" / "issue-1.md").write_text("# a retro note\n")
    (sdlc / "config.json").write_text(json.dumps({"knowledge_graph": {
        "enabled": enabled, "scope": "research", "builder": "stubgraph", "auto_refresh": auto_refresh}}))
    if graph_age is not None:
        out = pathlib.Path(root) / "stubgraph-out"; out.mkdir(exist_ok=True)
        g = out / "graph.json"; g.write_text("{}")
        t = time.time() - graph_age; os.utime(g, (t, t))
    return root


def _stub_builder_path(tmp_path):
    b = tmp_path / "bin"; b.mkdir(exist_ok=True)
    p = b / "stubgraph"; p.write_text("#!/bin/sh\necho stubgraph 1.0\n"); p.chmod(0o755)
    return f"{b}{os.pathsep}{os.environ['PATH']}"


def test_never_built_graph_warns_at_session_start(tmp_path):
    ctx = _context(_run(_kg_repo(tmp_path / "p"), PATH=_stub_builder_path(tmp_path)))
    assert "never been built" in ctx and "/agrim-kg" in ctx
    assert "not measured" in ctx                    # the first-build cost, honestly


def test_stale_graph_warns_at_session_start(tmp_path):
    ctx = _context(_run(_kg_repo(tmp_path / "p", graph_age=3600), PATH=_stub_builder_path(tmp_path)))
    assert "stale" in ctx and "/agrim-kg" in ctx


def test_fresh_graph_is_silent(tmp_path):
    """NEGATIVE CONTROL (issue done_when). First line is the control: same fixture minus the
    fresh graph warns, so the silence below is the gate and not an absent tier."""
    path = _stub_builder_path(tmp_path)
    assert "never been built" in _context(_run(_kg_repo(tmp_path / "absent"), PATH=path))
    assert _run(_kg_repo(tmp_path / "fresh", graph_age=-60), PATH=path) == ""    # built in the future of the note


def test_auto_refresh_off_is_silent(tmp_path):
    """NEGATIVE CONTROL (issue done_when): a repo that builds by hand has nothing wrong with it."""
    path = _stub_builder_path(tmp_path)
    assert "never been built" in _context(_run(_kg_repo(tmp_path / "on"), PATH=path))
    assert _run(_kg_repo(tmp_path / "off", auto_refresh=False), PATH=path) == ""


def test_kg_disabled_is_silent(tmp_path):
    """The shipped default (`enabled: false`) produces nothing, whatever auto_refresh says."""
    assert _run(_kg_repo(tmp_path / "p", enabled=False), PATH=_stub_builder_path(tmp_path)) == ""


def test_kg_warning_fires_at_most_once_a_day(tmp_path):
    project = _kg_repo(tmp_path / "p"); path = _stub_builder_path(tmp_path)
    assert "never been built" in _context(_run(project, PATH=path))
    assert _run(project, PATH=path) == "", "the same session-start warning fired twice in a day"
    assert (pathlib.Path(project) / ".sdlc" / "state" / "kg-warned.stamp").exists()


def test_kg_warning_is_valid_json(tmp_path):
    json.loads(_run(_kg_repo(tmp_path / "p"), PATH=_stub_builder_path(tmp_path)))


def test_headless_session_skips_the_kg_tier(tmp_path):
    project = _kg_repo(tmp_path / "p"); path = _stub_builder_path(tmp_path)
    assert _run(project, PATH=path, SIGMA_RUN_ID="supervise-1-2-3") == ""
    assert "never been built" in _context(_run(project, PATH=path))    # control: not headless -> warns


def test_kg_tier_does_not_double_fire_with_the_watcher_tier(tmp_path):
    """Both tier 3 (stale ledger watcher) and tier 4 (never-built graph) genuinely want to fire.
    Exactly one additionalContext may be printed, and it is the earlier tier's: `_context()`'s
    json.loads would raise on two concatenated blobs, so parsing is part of the proof."""
    project = _ledger(tmp_path / "p", heartbeat_age=3600)
    cfg_path = pathlib.Path(project) / ".sdlc" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    _kg_repo(project)                                        # overwrites config with only the kg block...
    cfg["knowledge_graph"] = json.loads(cfg_path.read_text())["knowledge_graph"]
    cfg_path.write_text(json.dumps(cfg))                     # ...so put the ledger block back beside it
    ctx = _context(_run(project, PATH=_stub_builder_path(tmp_path)))
    assert "looks stale" in ctx
    assert "never been built" not in ctx

"""End-to-end: spawn the real hook script as a subprocess, exactly like tests/test_hook.py
already does for agrim_gate.sh -- proving the SHIPPED file behaves correctly, not a copy of its
logic re-implemented in the test."""
import json
import os
import pathlib
import subprocess

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "session_start.sh"


def _run_hook(project_dir, path_prefix=None, **env):
    """`env` is built from scratch, never inherited -- SIGMA_RUN_ID in the developer's own
    shell (a session launched under supervise_daemon.py) would otherwise silently switch the wizard off
    and make every assertion below pass vacuously."""
    path = "/usr/bin:/bin" if path_prefix is None else f"{path_prefix}:/usr/bin:/bin"
    return subprocess.run(["bash", str(HOOK)], capture_output=True, text=True,
                          env={"CLAUDE_PROJECT_DIR": str(project_dir), "PATH": path,
                               # #240: the conftest's empty plugin inventories, not the real home
                               **{k: os.environ[k] for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")
                                  if k in os.environ}, **env},
                          timeout=15)


def _shimmed_graphify(tmp_path):
    """A `graphify` on PATH that always fails, so the "graphify installed" check is deterministic
    on ANY box -- a bare PATH=/usr/bin:/bin would merely be "graphify probably isn't in /usr/bin
    here", which is a property of the developer's machine, not of the code under test. Returns the
    directory to prepend to PATH.

    "graphify installed" is the classified check these tests need: it is one of the six in _MODES
    (so it can actually become a wizard step) AND it can be forced to fail without any network,
    any `gh`, or any real .sdlc state beyond a config.json -- which the policy-brief half of the
    hook requires to exist before it will fire at all."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "graphify").write_text("#!/bin/sh\nexit 1\n")
    (shim / "graphify").chmod(0o755)
    return str(shim)


def _ctx(p):
    """The hook's additionalContext, or "" when it legitimately printed nothing at all."""
    if not p.stdout.strip():
        return ""
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def test_a_repo_with_no_sdlc_at_all_gets_no_wizard_context(tmp_path):
    """#236 / #186 REVERSED this test's old assertion ("a repo with no .sdlc gets the wizard"): that
    was the nag -- the hook speaks in every repository the user opens, and a decline could never be
    remembered there. `/agrim-init` is the entry point for a new repository; see the adopted-repo
    controls at the end of this file, which prove the wizard itself still fires."""
    p = _run_hook(tmp_path)
    assert p.returncode == 0
    assert "agrim-wizard" not in _ctx(p)


def test_a_fully_healthy_repo_produces_no_wizard_output(tmp_path):
    """THE ASSERTION THAT MATTERS MOST for this plan's own done_when: an already-set-up repo
    must produce ZERO wizard output, proving the feature never nags someone who needs nothing."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text("{}")
    p = _run_hook(tmp_path)
    assert p.returncode == 0
    # A fully healthy `{}` config legitimately produces EMPTY stdout here: wizard_status() returns
    # needs_wizard=False (confirmed directly), so the wizard half is silent, and the pre-existing
    # policy-brief half is silent too since session_start.enabled isn't set -- the identical config
    # shape test_session_start.py's own test_sdlc_present_but_not_enabled_is_silent already pins to
    # "" from that half. `_ctx` parses only when there IS output; empty output already satisfies
    # "no wizard output" trivially, and json.loads("") would raise instead of asserting anything.
    assert "agrim-wizard" not in _ctx(p)


def test_a_dismissed_check_stays_dismissed_across_hook_runs(tmp_path):
    """A TRUE BEFORE/AFTER CONTROL, not a vacuous pass. The previous version of this test used a
    `{}` config, which produces NO failing classified check at all -- so `ctx` was empty and its
    `"gh auth" not in ctx` assertion held whether dismissal worked, did nothing, or did not exist.

    This exercises the real production path (`dismissed=None`, so wizard_status() reads the file
    itself) against a check that is BOTH classified in _MODES and genuinely failing, and it proves
    the step is present first -- so if the dismissal stopped working, the second half goes red."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(
        '{"knowledge_graph": {"enabled": true, "builder": "graphify"}}')
    shim = _shimmed_graphify(tmp_path)

    # CONTROL: with nothing dismissed, the step IS reported.
    before = _ctx(_run_hook(tmp_path, path_prefix=shim))
    assert "agrim-wizard" in before
    assert "graphify installed" in before

    # ...and once dismissed, the very same fixture reports nothing.
    (sdlc / "state").mkdir(exist_ok=True)
    (sdlc / "state" / "setup-wizard-dismissed.json").write_text('["graphify installed"]')
    after = _ctx(_run_hook(tmp_path, path_prefix=shim))
    assert "graphify installed" not in after
    assert "agrim-wizard" not in after


def test_an_ongoing_hygiene_failure_never_triggers_the_wizard(tmp_path):
    """FINAL-REVIEW FINDING C2, at the hook level. doctor.check() has ~26 checks and only 6 are
    first-run setup gaps; before the _MODES allow-list, ANY other failing row set needs_wizard and
    told the agent to run the setup wizard "before anything else the user asked for" -- on every
    mature, working repo, forever. `ledger.enabled` with no ops branch is exactly such a row
    ("team ledger initialized"), and it is hermetic: a local `.git` existence test, no network."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text('{"ledger": {"enabled": true}}')
    p = _run_hook(tmp_path)
    assert p.returncode == 0
    assert "agrim-wizard" not in _ctx(p)


def test_the_hook_writes_nothing_into_a_repo_that_has_no_sdlc(tmp_path):
    """FINAL-REVIEW FINDING C1. The hook fires in EVERY repo the user opens. Caching its result
    there left an untracked `.sdlc/state/setup-wizard-cache.json` -- and a dirty `git status` --
    in repos that had never adopted Sigma, before the user was asked anything. Verified live
    against a real fresh `git init` repo as well; this pins it."""
    p = _run_hook(tmp_path)
    assert p.returncode == 0
    assert "agrim-wizard" not in _ctx(p)                # #236: it no longer even reports here...
    assert list(tmp_path.iterdir()) == []              # ...and it leaves nothing behind


def test_a_headless_supervised_session_gets_no_wizard(tmp_path):
    """FINAL-REVIEW FINDING I4. A `claude -p /agrim-loop` worker (launched by supervise_daemon.py, which
    exports SIGMA_RUN_ID before spawning it) has nobody to answer the wizard's yes/no
    questions. An ADOPTED repo with a failing classified check (#236: an unadopted one never fires
    at all), which DOES fire the wizard -- the only difference below is the environment variable,
    so this cannot pass by accident."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text('{"knowledge_graph": {"enabled": true, "builder": "graphify"}}')
    shim = _shimmed_graphify(tmp_path)
    assert "agrim-wizard" in _ctx(_run_hook(tmp_path, path_prefix=shim))   # control: it would fire
    p = _run_hook(tmp_path, path_prefix=shim, SIGMA_RUN_ID="supervise-123-456-789")
    assert p.returncode == 0
    assert p.stdout.strip() == ""
    assert "agrim-wizard" not in _ctx(p)


def test_a_headless_session_still_gets_the_opt_in_policy_brief(tmp_path):
    """The headless skip must fall THROUGH to today's behaviour, not short-circuit the whole hook:
    a supervised run in a repo that opted into the policy brief still gets its policy brief."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(
        '{"session_start": {"enabled": true}, '
        '"knowledge_graph": {"enabled": true, "builder": "graphify"}}')
    ctx = _ctx(_run_hook(tmp_path, path_prefix=_shimmed_graphify(tmp_path),
                         SIGMA_RUN_ID="supervise-123-456-789"))
    assert "agrim-wizard" not in ctx
    assert "reviewer is never the author" in ctx       # the policy brief, unchanged


def test_dual_fire_condition_still_emits_exactly_one_json_object(tmp_path):
    """Regression coverage for the precedence gate in hooks/session_start.sh: sys.exit(0) inside
    the wizard's own python heredoc only ends that subprocess, not the surrounding bash script --
    without the `if`/`exit 1`/`exit 0` gate wrapped around it, a repo where BOTH the wizard has
    real content to print AND session_start.enabled is true would emit two concatenated JSON
    objects on stdout, which is invalid JSON (confirmed directly: re-running this exact fixture
    against a deliberately-reverted copy of the gate produces `json.decoder.JSONDecodeError:
    Extra data`, while the real, gated hook produces exactly one valid object).

    Excluding "north-star filled" from the wizard's scope means the fixture that originally
    caught this (tests/test_session_start.py's test_enabled_injects_session_start_context, a stub
    north-star + session_start.enabled) no longer forces the wizard to fire, so it stopped
    exercising this path.

    FIXTURE REVISED AGAIN by final-review finding C2. It previously used an UNCLASSIFIED failing
    check (an unresolvable `knowledge_graph.builder` name) purely to force needs_wizard=True --
    which was fine while any failing check did that, and is now impossible: _MODES is an
    allow-list, so only its six checks can ever produce a step. This forces the same dual-fire
    condition with a CLASSIFIED one instead: builder `graphify` (name "graphify installed", in
    _MODES) made to fail deterministically by a shim on PATH, alongside session_start.enabled.
    Still fully hermetic -- one failed exec, no gh, no network."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(
        '{"session_start": {"enabled": true}, "knowledge_graph": {"enabled": true, '
        '"builder": "graphify"}}'
    )
    p = _run_hook(tmp_path, path_prefix=_shimmed_graphify(tmp_path))
    assert p.returncode == 0
    # Exactly one JSON object -- json.loads raises "Extra data" on two concatenated objects,
    # precisely what a broken (non-gating) precedence would produce for this fixture.
    payload = json.loads(p.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "agrim-wizard" in ctx                  # the wizard's block won, not the policy brief's


def test_missing_python3_still_exits_zero_with_valid_json(tmp_path, monkeypatch):
    """Fail-open, matching every other hook in this repo (agrim_gate.sh's own preflight, #1536's
    git hook): a hook that cannot run its own logic must never break the user's session."""
    # PATH keeps a directory bash itself still resolves from -- subprocess.run looks up "bash" using
    # THIS env's PATH, not the calling process's real one, so an unqualified PATH (e.g.
    # "/nonexistent") fails to spawn bash at all and never even reaches the hook's own preflight,
    # confirmed directly. /bin carries bash but never python3 (verified: python3 lives at
    # /usr/bin/python3 on this box), so this genuinely exercises "python3 missing", not "bash missing".
    p = subprocess.run(["bash", str(HOOK)], capture_output=True, text=True,
                       env={"CLAUDE_PROJECT_DIR": str(tmp_path), "PATH": "/bin"},
                       timeout=15)
    assert p.returncode == 0


# --- #236 / #186: the wizard fires only in a repository Sigma has adopted ---------------------


def test_236_no_wizard_in_a_directory_that_never_adopted_sigma(tmp_path):
    """#186: a stranger's repo -- no `.sdlc/` -- gets nothing from the session hook, and nothing is
    written there. `/agrim-init` is the entry point; the wizard is for an adopted repo only."""
    p = _run_hook(tmp_path, path_prefix=_shimmed_graphify(tmp_path))
    assert p.returncode == 0
    assert "agrim-wizard" not in _ctx(p)
    assert sorted(x.name for x in tmp_path.iterdir()) == ["shim"]


def test_236_no_wizard_for_an_sdlc_without_config(tmp_path):
    """A bare `.sdlc/` directory (another tool's, or an interrupted scaffold) is not adoption:
    `.sdlc/config.json` is the one marker (`hooks/gate_state.py:adopted_root`)."""
    (tmp_path / ".sdlc").mkdir()
    assert "agrim-wizard" not in _ctx(_run_hook(tmp_path))


def test_236_no_wizard_in_an_sdlc_another_plugin_owns(tmp_path):
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    (sdlc / "config.json").write_text('{"knowledge_graph": {"enabled": true, "builder": "graphify"}}')
    shim = _shimmed_graphify(tmp_path)
    assert "agrim-wizard" in _ctx(_run_hook(tmp_path, path_prefix=shim))     # control: adopted
    (sdlc / "state" / "owner.json").write_text('{"schema": "x", "plugin": "another-plugin"}')
    assert "agrim-wizard" not in _ctx(_run_hook(tmp_path, path_prefix=shim))

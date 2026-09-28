"""completion_gate.sh (Slice 4): the OPT-IN interactive Stop gate. When enabled, it refuses to let the
agent stop with unplanned SOURCE changes in the working tree — the Stop-time counterpart to the
PreToolUse plan_gate. Guards the safety-critical properties: OFF by default, fail-open, a working loop
guard (so a block never loops), and the deliberate-override sentinel."""
import json, os, subprocess, pathlib

GATE = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "completion_gate.sh"


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _run(project_dir, payload="{}"):
    p = subprocess.run(["bash", str(GATE)], input=payload, capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 0, p.stderr          # the gate always exits 0 (block is via stdout JSON)
    return p.stdout.strip()


def _enabled_repo(tmp_path, extra_cfg=""):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".sdlc").mkdir()
    cfg = '{"gates":{"stop_gate":{"enabled":true%s}}}' % extra_cfg
    (tmp_path / ".sdlc" / "config.json").write_text(cfg)
    (tmp_path / "a.py").write_text("x = 1\n")   # an untracked source change
    return tmp_path


def _is_block(out):
    if not out:
        return False
    return json.loads(out).get("decision") == "block"


# --- OFF by default: the whole point — installing it changes nothing until opted in ---

def test_no_config_allows(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    assert _run(tmp_path) == ""                 # no .sdlc/config.json -> silent allow


def test_config_without_stop_gate_allows(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"gates":{}}')
    (tmp_path / "a.py").write_text("x = 1\n")
    assert _run(tmp_path) == ""                 # stop_gate absent -> off


def test_enabled_false_allows(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"gates":{"stop_gate":{"enabled":false}}}')
    (tmp_path / "a.py").write_text("x = 1\n")
    assert _run(tmp_path) == ""


# --- enabled behavior ---

def test_enabled_source_change_no_plan_blocks(tmp_path):
    assert _is_block(_run(_enabled_repo(tmp_path)))


def test_enabled_source_change_with_fresh_plan_allows(tmp_path):
    repo = _enabled_repo(tmp_path)
    (repo / ".sdlc" / "plans").mkdir()
    (repo / ".sdlc" / "plans" / "p.md").write_text("# plan\n")
    assert _run(repo) == ""


def test_only_docs_or_sdlc_change_allows(tmp_path):
    repo = _enabled_repo(tmp_path)
    (repo / "a.py").unlink()                    # remove the source change
    (repo / "docs").mkdir(); (repo / "docs" / "x.md").write_text("doc\n")
    assert _run(repo) == ""                     # docs/.sdlc are the harness, not source


# --- safety: loop guard (BOTH schemas) + override + fail-open ---

def test_loop_guard_stop_hook_active_allows(tmp_path):
    assert _run(_enabled_repo(tmp_path), payload='{"stop_hook_active":true}') == ""


def test_loop_guard_recursive_state_allows(tmp_path):
    payload = '{"recursive_state":{"is_recursive":true,"blocked_by_hook":true}}'
    assert _run(_enabled_repo(tmp_path), payload=payload) == ""


def test_override_sentinel_allows(tmp_path):
    repo = _enabled_repo(tmp_path)
    (repo / ".sdlc" / ".allow-direct-edits").touch()
    assert _run(repo) == ""


def test_non_git_dir_allows(tmp_path):
    # enabled config but not a git repo -> fail-open
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"gates":{"stop_gate":{"enabled":true}}}')
    (tmp_path / "a.py").write_text("x = 1\n")
    assert _run(tmp_path) == ""


def test_enabled_reads_generously_like_f17s_verify_enforce(tmp_path):
    """#416: mirrors test_plan_gate.py's case of the same name -- `stop_gate.enabled` used to
    require strict `is True`, the same unsafe-direction fragility F17/#342 fixed for
    `verify.enforce`. A config typo (`enabled: 1` or `enabled: "true"`) must now enable this DENY
    gate, not silently leave it off."""
    on_values = ('true', '1', '"true"', '"True"', '"1"', '"yes"')
    for i, raw in enumerate(on_values):
        d = tmp_path / ("on%d" % i)
        d.mkdir(); _git(d, "init", "-q"); (d / ".sdlc").mkdir()
        (d / ".sdlc" / "config.json").write_text('{"gates":{"stop_gate":{"enabled":%s}}}' % raw)
        (d / "a.py").write_text("x = 1\n")
        assert _is_block(_run(d)), "enabled:%s must enable the gate (F17 direction)" % raw

    off_values = ('false', '0', '""', '"false"', '"False"', '"0"', '"no"', '"off"')
    for i, raw in enumerate(off_values):
        d = tmp_path / ("off%d" % i)
        d.mkdir(); _git(d, "init", "-q"); (d / ".sdlc").mkdir()
        (d / ".sdlc" / "config.json").write_text('{"gates":{"stop_gate":{"enabled":%s}}}' % raw)
        (d / "a.py").write_text("x = 1\n")
        assert _run(d) == "", "enabled:%s must not enable the gate" % raw


def test_stale_plan_still_blocks(tmp_path):
    repo = _enabled_repo(tmp_path)
    plans = repo / ".sdlc" / "plans"; plans.mkdir()
    old = plans / "p.md"; old.write_text("# old plan\n")
    os.utime(old, (0, 0))                       # epoch mtime — far older than plan_freshness_hours
    assert _is_block(_run(repo))                # a stale plan does not satisfy the freshness gate


def test_non_numeric_freshness_does_not_error(tmp_path):
    # a hand-misconfigured non-numeric freshness must still fail safe: valid JSON block, clean stderr
    _git(tmp_path, "init", "-q")
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text(
        '{"gates":{"stop_gate":{"enabled":true,"plan_freshness_hours":"lots"}}}')
    (tmp_path / "a.py").write_text("x = 1\n")
    p = subprocess.run(["bash", str(GATE)], input="{}", capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert p.returncode == 0 and p.stderr == ""     # no "unbound variable" leak
    assert _is_block(p.stdout.strip())              # still fails safe toward blocking


def test_wired_into_hooks_json():
    hooks = json.loads((GATE.parent / "hooks.json").read_text())
    stop = hooks["hooks"].get("Stop", [])
    assert any("completion_gate.sh" in json.dumps(h) for h in stop), "Stop hook not wired"


def test_a_window_that_wraps_positive_does_not_silently_allow_a_stale_plan(tmp_path):
    """#602, the sibling half: the two gates share this rule, so they share the 64-bit wrap.
    `$(( fresh_hours * 60 ))` overflows above (2^63-1)/60, and far past it the product comes back
    positive and huge — the Stop gate then treats a months-old plan as fresh and stops objecting to
    unplanned source. Clamped in the python block (the shell cannot compare a 20-digit value)."""
    repo = _enabled_repo(tmp_path, ',"plan_freshness_hours":99999999999999999999')
    plans = repo / ".sdlc" / "plans"; plans.mkdir()
    old = plans / "p.md"; old.write_text("# old plan\n")
    os.utime(old, (0, 0))                       # epoch mtime: stale under any real window
    assert _is_block(_run(repo))


# --------------------------------------------------------------- #2116: the block-shaped #416 trap

def _scalar_repo(tmp_path, gates):
    """A repo with an untracked source file, so an ON gate blocks and an OFF gate stays silent."""
    _git(tmp_path, "init", "-q")
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text(json.dumps({"gates": gates}))
    (tmp_path / "a.py").write_text("x = 1\n")
    return tmp_path


def test_a_scalar_stop_gate_block_is_read_for_its_plain_intent(tmp_path):
    """Kept in lockstep with `plan_gate.sh`'s own fix (#2116). Same two-level totality, same reason:
    a scalar block used to raise inside the embedded Python and land in `except: mode = "off"` --
    a silent OFF on a hard gate, the BLOCK-shaped sibling of #416's value-shaped trap. `stop_gate`
    is not itself org-lockable, but the two hooks share this read verbatim and fixing one alone is
    exactly the drift the lockstep tests here already exist to stop."""
    for value, on in ((True, True), ("true", True), (1, True),
                      (False, False), ("off", False), (0, False)):
        repo = tmp_path / ("c%r" % (value,))
        repo.mkdir()
        assert _is_block(_run(_scalar_repo(repo, {"stop_gate": value}))) is on, value


def test_a_scalar_gates_parent_never_crashes_the_stop_gate_read(tmp_path):
    """`(cfg.get("gates") or {})` raises AttributeError on `{"gates": true}` -- swallowed by
    fail-open, so the gate was off with nothing to say why."""
    for value in (True, "on", []):
        repo = tmp_path / ("p%r" % (value,))
        repo.mkdir()
        assert _run(_scalar_repo(repo, value)) == "", value

"""#240: Sigma and the plugin under its previous name on one repository.

`skills/agrim-loop/scripts/coexist.py` is the one detector; every surface that writes shared state
or starts a watcher refuses on an ACTIVE signal (naming the fix), read-only surfaces warn, and the
override `SIGMA_ALLOW_COEXIST=1` lets a write surface proceed but never admits a second watcher.

Every old-name spelling is built from fragments (`OLD`): the previous name is a guarded private
name in this tree (`tests/test_no_private_names.py`). Fake host inventories live under tmp; the
autouse `_no_host_plugin_inventory` fixture (conftest) keeps the developer's real one out.
Surface tests run each surface on the gesture its docs give (`sdlc_init.py <repo>`,
`loop.py start <sdlc>`, `watch_daemon.py <sdlc>`, `migrate.py <sdlc> --apply`, `status.py <sdlc>`,
`bash hooks/session_start.sh`).
"""
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP = ROOT / "skills" / "agrim-loop" / "scripts"
INIT = ROOT / "skills" / "agrim-init" / "scripts" / "sdlc_init.py"
MIGRATE = ROOT / "skills" / "agrim-doctor" / "scripts" / "migrate.py"
DOCTOR = ROOT / "skills" / "agrim-doctor" / "scripts" / "doctor.py"
STATUS = ROOT / "skills" / "agrim-status" / "scripts" / "status.py"
HOOK = ROOT / "hooks" / "session_start.sh"
OLD = "loop" + "smith"
OLD_ID = OLD + "@" + OLD


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def cx():
    return _load(LOOP / "coexist.py", "coexist")


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".sdlc" / "state").mkdir(parents=True)
    (repo / ".sdlc" / "config.json").write_text("{}\n", encoding="utf-8")
    return repo


def _host(tmp_path, *, claude=None, codex=None, installed=False):
    """A fake host: `claude` is the user-settings dict, `codex` the config.toml text. Returns the
    env overrides that point the scan at it (and the fake installed-plugin directory)."""
    croot, xroot = tmp_path / "host" / "claude", tmp_path / "host" / "codex"
    croot.mkdir(parents=True, exist_ok=True)
    xroot.mkdir(parents=True, exist_ok=True)
    if claude is not None:
        (croot / "settings.json").write_text(json.dumps(claude), encoding="utf-8")
    if installed:
        cache = croot / "plugins" / "cache" / OLD / OLD / "1.4.24"
        cache.mkdir(parents=True)
        (croot / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {OLD_ID: [{"scope": "user", "installPath": str(cache),
                                                 "version": "1.4.24"}]}}), encoding="utf-8")
    if codex is not None:
        (xroot / "config.toml").write_text(codex, encoding="utf-8")
    return {"CLAUDE_CONFIG_DIR": str(croot), "CODEX_HOME": str(xroot)}


ENABLED = {"enabledPlugins": {OLD_ID: True}}


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("SIGMA_ALLOW_COEXIST", "SIGMA_RUN_ID")}
    env.update(extra)
    return env


def _kinds(report, level=None):
    return sorted(s.kind for s in report.signals if level is None or s.level == level)


def _tree(path):
    """Every file under `path` with its bytes -- to prove a refusal wrote no shared state.
    `state/time/` is excluded: `loop.py` records its own process time on EVERY invocation, before
    any verb runs (timing_store, local to this machine and never config-gated) -- a refusal is an
    invocation too."""
    path = pathlib.Path(path)
    return {p.relative_to(path).as_posix(): p.read_bytes()
            for p in sorted(path.rglob("*"))
            if p.is_file() and not p.relative_to(path).as_posix().startswith("state/time/")
            } if path.exists() else {}


# --------------------------------------------------------------------------- detection


def test_nothing_installed_is_clear(tmp_path, cx):
    repo = _repo(tmp_path)
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path))
    assert report.signals == ()
    assert cx.gate(repo / ".sdlc", "x", env=_host(tmp_path), stream=io.StringIO())


def test_claude_user_enabled_is_active_and_the_message_names_the_fix(tmp_path, cx):
    repo = _repo(tmp_path)
    env = _host(tmp_path, claude=ENABLED)
    report = cx.assess(repo / ".sdlc", env=env)
    assert _kinds(report, cx.ACTIVE) == ["claude-enabled"]
    text = cx.message(report, "loop.py start")
    assert "loop.py start refused" in text
    assert "claude plugin disable %s" % OLD_ID in text and "settings.local.json" in text
    assert "migrate.py %s --apply" % (repo / ".sdlc") in text
    assert "SIGMA_ALLOW_COEXIST=1" in text


def test_claude_precedence_project_false_beats_user_true(tmp_path, cx):
    repo = _repo(tmp_path)
    env = _host(tmp_path, claude=ENABLED, installed=True)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {OLD_ID: False}}))
    report = cx.assess(repo / ".sdlc", env=env)
    assert report.active == []
    assert _kinds(report, cx.NOTE) == ["installed"]             # installed, not enabled here


def test_claude_precedence_local_true_beats_project_false(tmp_path, cx):
    repo = _repo(tmp_path)
    env = _host(tmp_path)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {OLD_ID: False}}))
    (repo / ".claude" / "settings.local.json").write_text(json.dumps({"enabledPlugins": {OLD_ID: True}}))
    assert _kinds(cx.assess(repo / ".sdlc", env=env), cx.ACTIVE) == ["claude-enabled"]


def test_a_hand_registered_old_hook_is_double_registration(tmp_path, cx):
    repo = _repo(tmp_path)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": "bash /opt/%s/hooks/session_start.sh" % OLD}]}]}}))
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path))
    assert _kinds(report, cx.ACTIVE) == ["hook"]
    assert "SessionStart" in report.active[0].detail


@pytest.mark.parametrize("body,level", [
    ('[plugins."%s"]\nenabled = true\n' % OLD_ID, "active"),
    ('[plugins."%s"]\nenabled = false\n' % OLD_ID, "note"),
    ('[plugins."%s"]\n' % OLD_ID, "note"),
    ('[plugins."other@x"]\nenabled = true\n[plugins."%s"]\n[x]\nenabled = true\n' % OLD_ID, "note"),
])
def test_codex_config_toml(tmp_path, cx, body, level):
    repo = _repo(tmp_path)
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path, codex=body))
    assert [s.level for s in report.signals] == [level]


def test_owner_marker_names_another_plugin(tmp_path, cx):
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "state" / "owner.json").write_text(json.dumps({"plugin": "other"}))
    assert _kinds(cx.assess(repo / ".sdlc", env=_host(tmp_path)), cx.ACTIVE) == ["owner"]


def test_write_owner_is_atomic_and_idempotent(tmp_path, cx):
    repo = _repo(tmp_path)
    assert cx.write_owner(repo / ".sdlc") is True
    assert cx.write_owner(repo / ".sdlc") is False
    assert json.loads((repo / ".sdlc" / "state" / "owner.json").read_text()) == \
        {"schema": "sigma/owner@1", "plugin": "sigma"}
    assert cx.assess(repo / ".sdlc", env=_host(tmp_path)).signals == ()
    assert [p.name for p in (repo / ".sdlc" / "state").iterdir()] == ["owner.json"]


def test_old_schema_state_is_a_note_and_the_scan_is_bounded(tmp_path, cx):
    repo = _repo(tmp_path)
    units = repo / ".sdlc" / "features" / "units"
    units.mkdir(parents=True)
    for i in range(cx.STATE_SCAN_CAP + 50):
        (units / ("u%03d.json" % i)).write_text(json.dumps({"schema": OLD + "/features@1"}))
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path))
    assert report.active == []
    [note] = report.notes
    assert note.kind == "state" and "%d state file(s)" % cx.STATE_SCAN_CAP in note.detail
    assert "migrate.py" in note.fix


def test_committed_adapters_are_notes(tmp_path, cx):
    repo = _repo(tmp_path)
    (repo / ".cursor" / "rules").mkdir(parents=True)
    (repo / ".cursor" / "rules" / "sdlc.mdc").write_text("run the %s skills\n" % OLD)
    (repo / "AGENTS.md").write_text("<!-- %s:codex:start -->\nx\n<!-- %s:codex:end -->\n" % (OLD, OLD))
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path))
    assert report.active == [] and _kinds(report, cx.NOTE) == ["adapter", "adapter"]


# --------------------------------------------------------------------------- the live watcher


@pytest.fixture()
def live_pid():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


def _fake_watcher(sdlc, pid):
    state = pathlib.Path(sdlc) / "state"
    (state / "watch.pid").write_text("%d\n" % pid)
    (state / "watch.heartbeat").write_text("")


def test_live_unmarked_watcher_with_old_state_is_active(tmp_path, cx, live_pid):
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", live_pid)
    (repo / ".sdlc" / "features").mkdir()
    (repo / ".sdlc" / "features" / "index.json").write_text(json.dumps({"schema": OLD + "/features@1"}))
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path))
    assert _kinds(report, cx.ACTIVE) == ["watcher"]
    assert str(live_pid) in report.active[0].detail and "watch.stop" in report.active[0].fix


def test_live_unmarked_watcher_alone_is_a_note(tmp_path, cx, live_pid):
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", live_pid)
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path))
    if sys.platform.startswith("linux"):
        pytest.skip("Linux reads the pid's command line, which here is a plain python sleep")
    assert _kinds(report, cx.NOTE) == ["watcher"] and report.active == []


def test_sigma_marked_watcher_is_not_a_signal(tmp_path, cx, live_pid):
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", live_pid)
    cx.write_watch_owner(repo / ".sdlc" / "state", live_pid)
    (repo / ".sdlc" / "features").mkdir()
    (repo / ".sdlc" / "features" / "index.json").write_text(json.dumps({"schema": OLD + "/features@1"}))
    assert _kinds(cx.assess(repo / ".sdlc", env=_host(tmp_path))) == ["state"]


def test_stale_heartbeat_is_no_watcher(tmp_path, cx, live_pid):
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", live_pid)
    old = time.time() - 10_000
    os.utime(repo / ".sdlc" / "state" / "watch.heartbeat", (old, old))
    assert cx.assess(repo / ".sdlc", env=_host(tmp_path, claude=ENABLED)).active[0].kind == \
        "claude-enabled"
    assert "watcher" not in _kinds(cx.assess(repo / ".sdlc", env=_host(tmp_path, claude=ENABLED)))


# --------------------------------------------------------------------------- gate and override


def test_gate_refuses_then_the_override_admits_with_a_warning(tmp_path, cx):
    repo = _repo(tmp_path)
    env = _host(tmp_path, claude=ENABLED)
    out = io.StringIO()
    assert cx.gate(repo / ".sdlc", "loop.py start", env=env, stream=out) is False
    assert "refused" in out.getvalue()
    for value in ("true", "yes", "0", ""):                    # exactly `1`, nothing derived
        assert cx.gate(repo / ".sdlc", "s", env={**env, "SIGMA_ALLOW_COEXIST": value},
                       stream=io.StringIO()) is False
    assert cx.gate(repo / ".sdlc", "s", env={**env, OLD.upper() + "_ALLOW_COEXIST": "1"},
                   stream=io.StringIO()) is False           # never read under the old prefix
    out = io.StringIO()
    assert cx.gate(repo / ".sdlc", "s", env={**env, "SIGMA_ALLOW_COEXIST": "1"}, stream=out)
    assert "warning" in out.getvalue() and "SIGMA_ALLOW_COEXIST=1" in out.getvalue()


def test_doctor_row(tmp_path, cx):
    repo = _repo(tmp_path)
    assert cx.doctor_row(repo / ".sdlc", env=_host(tmp_path))["ok"] is True
    row = cx.doctor_row(repo / ".sdlc", env=_host(tmp_path, claude=ENABLED))
    assert row["ok"] is False and row["name"].startswith("coexistence: the old plugin is ACTIVE")


# --------------------------------------------------------------------------- surfaces (gestures)


def _run(argv, env, cwd=None):
    return subprocess.run([sys.executable, *map(str, argv)], capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=120)


def test_init_refuses_before_writing_anything(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    p = _run([INIT, repo], _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 2, p.stdout + p.stderr
    assert "agrim-init refused" in p.stderr and "claude plugin disable" in p.stderr
    assert _tree(repo) == {}


def test_init_proceeds_and_records_the_owner_when_clear(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    p = _run([INIT, repo], _env(**_host(tmp_path)))
    assert p.returncode == 0, p.stderr
    assert json.loads((repo / ".sdlc" / "state" / "owner.json").read_text())["plugin"] == "sigma"


def test_init_override_proceeds(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    p = _run([INIT, repo], _env(SIGMA_ALLOW_COEXIST="1", **_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0 and "warning" in p.stderr


def _scaffolded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _run([INIT, repo], _env(**_host(tmp_path / "clear"))).returncode == 0
    return repo


def test_loop_start_refuses_before_any_write(tmp_path):
    repo = _scaffolded(tmp_path)
    (repo / ".sdlc" / "state" / "owner.json").unlink()
    before = _tree(repo / ".sdlc")
    p = _run([LOOP / "loop.py", "start", repo / ".sdlc", "--session-pid", os.getpid()],
             _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 2, p.stdout + p.stderr
    assert "loop.py start refused" in p.stderr
    assert _tree(repo / ".sdlc") == before


def test_loop_start_proceeds_when_clear(tmp_path):
    repo = _scaffolded(tmp_path)
    p = _run([LOOP / "loop.py", "start", repo / ".sdlc", "--session-pid", os.getpid()],
             _env(**_host(tmp_path)))
    assert p.returncode == 0, p.stderr
    assert "refused" not in p.stderr


def test_watcher_refuses_and_takes_no_lock(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "state" / "watch.stop").write_text("")
    p = _run([LOOP / "watch_daemon.py", repo / ".sdlc"],
             _env(SIGMA_WATCH_SLEEP_SCALE="0", **_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 2, p.stdout + p.stderr
    assert "watcher start refused" in p.stdout
    state = repo / ".sdlc" / "state"
    assert not (state / "watch.pid").exists() and not (state / "watch.owner").exists()
    assert "watcher start refused" in (state / "watch.log").read_text()


def test_watcher_marks_itself_and_cleans_up(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "state" / "watch.stop").write_text("")
    p = _run([LOOP / "watch_daemon.py", repo / ".sdlc"], _env(SIGMA_WATCH_SLEEP_SCALE="0",
                                                              **_host(tmp_path)))
    assert p.returncode == 0 and "stop-file present" in p.stdout
    assert not (repo / ".sdlc" / "state" / "watch.owner").exists()     # removed with the pidfile


def test_watcher_take_over_writes_the_owner_marker(tmp_path):
    wd = _load(LOOP / "watch_daemon.py", "watch_daemon")
    repo = _repo(tmp_path)
    p = wd.paths(str(repo / ".sdlc"))
    assert wd.decide(p, 180, time.time()) == "acquired"
    assert (p.state / "watch.owner").read_text() == "sigma %d\n" % os.getpid()
    wd.cleanup(p, os.getpid())
    assert not (p.state / "watch.owner").exists()


def test_override_never_admits_a_second_watcher(tmp_path, live_pid):
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", live_pid)
    p = _run([LOOP / "watch_daemon.py", repo / ".sdlc"],
             _env(SIGMA_WATCH_SLEEP_SCALE="0", SIGMA_ALLOW_COEXIST="1",
                  **_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "already running (pid %d)" % live_pid in p.stdout
    assert "not started by this Sigma" in p.stdout
    assert (repo / ".sdlc" / "state" / "watch.pid").read_text().strip() == str(live_pid)


def test_ensure_watcher_does_not_spawn(tmp_path, monkeypatch, capsys):
    loop = _load(LOOP / "loop.py", "loop_under_test")
    repo = _repo(tmp_path)
    for key, value in _host(tmp_path, claude=ENABLED).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(loop.ledger, "enabled", lambda cfg: True)
    sync = loop._load("sync")
    monkeypatch.setattr(loop, "_load", lambda name, _real=loop._load: (
        type("S", (), {"is_worktree": staticmethod(lambda d: True)}) if name == "sync" else _real(name)))
    spawned = []
    loop._ensure_watcher(str(repo / ".sdlc"), {}, spawn=lambda: spawned.append(1))
    assert spawned == []
    assert "not starting the ledger watcher" in capsys.readouterr().err
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "none"))
    loop._ensure_watcher(str(repo / ".sdlc"), {}, spawn=lambda: spawned.append(1))
    assert spawned == [1] and sync is not None


def _old_schema_repo(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "state" / "landing").mkdir()
    record = repo / ".sdlc" / "state" / "landing" / "7.json"
    record.write_text(json.dumps({"schema": OLD + "/landing@1", "goal": 7}) + "\n")
    return repo, record


def test_migrate_apply_refuses_while_the_old_plugin_is_active(tmp_path):
    repo, record = _old_schema_repo(tmp_path)
    before = record.read_bytes()
    p = _run([MIGRATE, repo / ".sdlc", "--apply"], _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 2 and "migrate.py --apply refused" in p.stdout, p.stdout + p.stderr
    assert record.read_bytes() == before
    p = _run([MIGRATE, repo / ".sdlc", "--apply"], _env(**_host(tmp_path / "clear")))
    assert p.returncode == 0, p.stdout
    assert json.loads(record.read_text())["schema"] == "sigma/landing@1"


def test_doctor_check_warns_and_proceeds(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    for key, value in _host(tmp_path, claude=ENABLED).items():
        monkeypatch.setenv(key, value)
    doctor = _load(DOCTOR, "doctor_under_test")
    rows = doctor.check(str(repo / ".sdlc"), run=lambda *a, **k: "", cheap_only=True)
    [row] = [r for r in rows if r["name"].startswith("coexistence")]
    assert row["ok"] is False and "coexist.py check" in row["fix"]


def test_status_warns_on_stderr_and_proceeds(tmp_path):
    repo = _scaffolded(tmp_path)
    p = _run([STATUS, repo / ".sdlc"], _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0 and "backlog:" in p.stdout
    assert "also active on this repository" in p.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="the hook is a bash script")
def test_session_start_hook_tells_the_session(tmp_path):
    repo = _repo(tmp_path)
    env = _env(CLAUDE_PROJECT_DIR=str(repo), **_host(tmp_path, claude=ENABLED))
    p = subprocess.run(["bash", str(HOOK)], input="{}", capture_output=True, text=True, env=env)
    assert p.returncode == 0
    context = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "claude plugin disable %s" % OLD_ID in context
    env = _env(CLAUDE_PROJECT_DIR=str(repo), **_host(tmp_path / "clear"))
    p = subprocess.run(["bash", str(HOOK)], input="{}", capture_output=True, text=True, env=env)
    assert "refused" not in p.stdout


def test_cli_exit_codes(tmp_path):
    repo = _repo(tmp_path)
    assert _run([LOOP / "coexist.py", "check", repo / ".sdlc"], _env(**_host(tmp_path))).returncode == 0
    p = _run([LOOP / "coexist.py", "check", repo / ".sdlc"], _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 2 and "claude plugin disable" in p.stdout

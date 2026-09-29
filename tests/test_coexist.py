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


def _install_list(croot, entries):
    (croot / "plugins").mkdir(parents=True, exist_ok=True)
    (croot / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": entries}), encoding="utf-8")


def test_enabled_but_not_installed_on_this_machine_is_a_note(tmp_path, cx):
    """A stale (or teammate-committed) `enabledPlugins` entry for a plugin this machine does not
    have installed runs nothing: a NOTE. Installed + enabled stays ACTIVE."""
    repo = _repo(tmp_path)
    env = _host(tmp_path, claude=ENABLED)
    croot = pathlib.Path(env["CLAUDE_CONFIG_DIR"])
    _install_list(croot, {"other@x": [{"scope": "user"}]})
    report = cx.assess(repo / ".sdlc", env=env)
    assert report.active == [] and _kinds(report, cx.NOTE) == ["claude-enabled"]
    assert "not installed" in report.notes[0].detail
    _install_list(croot, {OLD_ID: [{"scope": "project", "projectPath": str(tmp_path / "elsewhere")}]})
    assert cx.assess(repo / ".sdlc", env=env).active == []          # installed for ANOTHER repo only
    _install_list(croot, {OLD_ID: [{"scope": "project", "projectPath": str(repo)}]})
    assert _kinds(cx.assess(repo / ".sdlc", env=env), cx.ACTIVE) == ["claude-enabled"]


@pytest.mark.parametrize("scope,expected", [("user", "active"), ("local", "active"),
                                            ("project", "note")])
def test_enabled_with_unknown_install_state(tmp_path, cx, scope, expected):
    """No readable install list: refuse only when enabled in the user's OWN scope (user/local) --
    a committed project entry alone is a teammate's choice, not proof this machine runs it."""
    repo = _repo(tmp_path)
    env = _host(tmp_path, claude=ENABLED if scope == "user" else None)
    if scope != "user":
        (repo / ".claude").mkdir()
        name = "settings.local.json" if scope == "local" else "settings.json"
        (repo / ".claude" / name).write_text(json.dumps(ENABLED))
    assert [s.level for s in cx.assess(repo / ".sdlc", env=env).signals] == [expected]


@pytest.mark.parametrize("command,expected", [
    ("bash /opt/%s/hooks/session_start.sh" % OLD, ["hook"]),
    ("python3 C:\\plugins\\%s\\hooks\\x.py" % OLD, ["hook"]),
    ("bash ~/bin/%s-notes.sh" % OLD, []),                        # the user's own script
    ("bash /home/u/%s_backup/run.sh" % OLD, []),
    ("echo %s is great" % OLD, []),
])
def test_hook_match_is_a_path_segment_not_a_substring(tmp_path, cx, command, expected):
    repo = _repo(tmp_path)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": command}]}]}}))
    assert _kinds(cx.assess(repo / ".sdlc", env=_host(tmp_path)), cx.ACTIVE) == expected


def test_hook_match_on_the_recorded_install_path(tmp_path, cx):
    repo = _repo(tmp_path)
    env = _host(tmp_path)
    croot = pathlib.Path(env["CLAUDE_CONFIG_DIR"])
    root = tmp_path / "renamed-cache" / "v9"
    _install_list(croot, {OLD_ID: [{"scope": "user", "installPath": str(root)}]})
    (croot / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "bash %s/hooks/stop.sh" % root}]}]}}))
    assert "hook" in _kinds(cx.assess(repo / ".sdlc", env=env), cx.ACTIVE)


CODEX_VARIANTS = [
    ('[plugins."%s"]\nenabled = true\n' % OLD_ID, True),
    ("[plugins.'%s']\nenabled = true\n" % OLD_ID, True),
    ('[ plugins . "%s" ]  # c\nenabled=true # on\n' % OLD_ID, True),
    ('plugins."%s".enabled = true\n' % OLD_ID, True),
    ('[plugins]\n"%s".enabled = true\n' % OLD_ID, True),
    ("[plugins]\n'%s'.enabled = false\n" % OLD_ID, False),
    ('[plugins]\n"%s" = { enabled = true }\n' % OLD_ID, True),
    ('[plugins."%s"]\nenabled = false\n' % OLD_ID, False),
    ('[plugins."%s"]\nsource = "x"\n' % OLD_ID, None),
    ('[plugins."other@x"]\nenabled = true\n[plugins."%s"]\n[x]\nenabled = true\n' % OLD_ID, None),
]


@pytest.mark.parametrize("body,expected", CODEX_VARIANTS)
@pytest.mark.parametrize("parser", ["default", "fallback"])
def test_codex_toml_variants(cx, body, expected, parser):
    """Every spelling TOML allows, through tomllib (3.11+) AND the stdlib-only 3.10 fallback."""
    if parser == "default" and cx._toml_loads is None:
        pytest.skip("no tomllib on this Python; the fallback row covers it")
    loads = cx._toml_loads if parser == "default" else None
    assert cx.parse_codex_plugins(body, loads=loads).get(OLD_ID, "absent") == expected


def test_jsonc_settings_are_read_not_treated_as_absent(tmp_path, cx):
    repo = _repo(tmp_path)
    env = _host(tmp_path)
    (pathlib.Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json").write_text(
        '{\n  // mine\n  "enabledPlugins": {\n    "%s": true, /* on */\n  },\n'
        '  "note": "a // not a comment, and /* nor this */",\n}\n' % OLD_ID)
    assert _kinds(cx.assess(repo / ".sdlc", env=env), cx.ACTIVE) == ["claude-enabled"]


def test_managed_settings_are_read_first(tmp_path, cx):
    repo = _repo(tmp_path)
    managed = tmp_path / "managed-settings.json"
    managed.write_text(json.dumps(ENABLED))
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path), managed=managed)
    assert _kinds(report, cx.ACTIVE) == ["claude-enabled"] and "managed" in report.active[0].detail
    managed.write_text(json.dumps({"enabledPlugins": {OLD_ID: False}}))      # beats user true
    assert cx.assess(repo / ".sdlc", env=_host(tmp_path / "u", claude=ENABLED),
                     managed=managed).active == []


@pytest.mark.parametrize("platform,env,expected", [
    ("darwin", {}, "/Library/Application Support/ClaudeCode/managed-settings.json"),
    ("linux", {}, "/etc/claude-code/managed-settings.json"),
    ("win32", {"ProgramData": "D:\\PD"}, "D:\\PD/ClaudeCode/managed-settings.json"),
    ("win32", {}, "C:\\ProgramData/ClaudeCode/managed-settings.json"),
])
def test_managed_settings_path(cx, platform, env, expected):
    assert str(cx.managed_settings_path(platform, env)).replace("\\", "/") == \
        expected.replace("\\", "/")


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


def _spawn(*argv):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", *argv])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


@pytest.fixture()
def live_pid():
    """A live process whose command line is a plain python sleep -- names nothing."""
    yield from _spawn()


#: A command line that GENUINELY names the old plugin, the way its installed watcher's does
#: (`python3 <plugin cache>/<old>/<version>/.../watch_daemon.py <sdlc>`).
OLD_WATCHER_ARGV = ("/home/u/.claude/plugins/cache/%s/%s/1.4.24/scripts/watch_daemon.py"
                    % (OLD, OLD),)


@pytest.fixture()
def old_watcher_pid():
    yield from _spawn(*OLD_WATCHER_ARGV)


def _fake_watcher(sdlc, pid):
    state = pathlib.Path(sdlc) / "state"
    (state / "watch.pid").write_text("%d\n" % pid)
    (state / "watch.heartbeat").write_text("")


#: How `/proc/<pid>/cmdline` reads on each platform, for the deterministic seam tests: `None` is
#: macOS and Windows (no /proc -- the read raises), bytes are a Linux read (NUL-separated argv).
PROC = {
    "no-proc": None,
    "linux-plain": b"python3\0-c\0import time; time.sleep(120)\0",
    "linux-old": ("python3\0" + OLD_WATCHER_ARGV[0] + "\0/r/.sdlc\0").encode(),
}


def _fake_proc(monkeypatch, cmdline):
    """Make every `/proc/<pid>/cmdline` read behave like `cmdline` (see PROC), whatever this OS
    really is -- so the Linux branch is proven on macOS and the no-/proc branch on Linux."""
    real = pathlib.Path.read_bytes

    def read_bytes(self):
        text = str(self).replace("\\", "/")
        if text.startswith("/proc/") and text.endswith("/cmdline"):
            if cmdline is None:
                raise FileNotFoundError(text)
            return cmdline
        return real(self)
    monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)


def _old_index(repo):
    (repo / ".sdlc" / "features").mkdir(exist_ok=True)
    (repo / ".sdlc" / "features" / "index.json").write_text(json.dumps({"schema": OLD + "/features@1"}))


def test_live_watcher_running_the_old_plugin_is_active_on_every_os(tmp_path, cx, old_watcher_pid):
    """The real process, no seam: its argv names the old plugin (Linux reads that from /proc) and
    the old plugin is enabled (macOS/Windows cannot read argv, so that is what they go on)."""
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", old_watcher_pid)
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path, claude=ENABLED))
    assert _kinds(report, cx.ACTIVE) == ["claude-enabled", "watcher"]
    [watcher] = [s for s in report.active if s.kind == "watcher"]
    assert str(old_watcher_pid) in watcher.detail and "watch.stop" in watcher.fix


@pytest.mark.parametrize("proc,claude,expected", [
    ("linux-old", None, "active"),          # Linux: argv names the old plugin -> active alone
    ("linux-plain", ENABLED, "note"),       # Linux: argv read and it is not the old plugin's
    ("no-proc", ENABLED, "active"),         # macOS/Windows: unreadable, an ACTIVE signal decides
    ("no-proc", None, "note"),              # macOS/Windows: unreadable, nothing active
])
def test_watcher_cmdline_seam(tmp_path, cx, live_pid, monkeypatch, proc, claude, expected):
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", live_pid)
    _fake_proc(monkeypatch, PROC[proc])
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path, claude=claude))
    assert [s.level for s in report.signals if s.kind == "watcher"] == [expected]


@pytest.mark.parametrize("proc", [None, "no-proc", "linux-plain"])
def test_unmarked_watcher_with_only_notes_is_a_note_and_the_gate_admits(tmp_path, cx, live_pid,
                                                                         monkeypatch, proc):
    """Review block #2: a Sigma-only user upgrading -- legacy registry, a live watcher started
    before this release (so unmarked), a fresh heartbeat, an old Cursor rule, the old plugin
    installed but DISABLED -- must not be refused on any OS. Notes never escalate the watcher."""
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", live_pid)
    _old_index(repo)
    if proc is not None:
        _fake_proc(monkeypatch, PROC[proc])
    env = _host(tmp_path)
    report = cx.assess(repo / ".sdlc", env=env)
    assert report.active == [] and _kinds(report, cx.NOTE) == ["state", "watcher"]
    assert cx.gate(repo / ".sdlc", "loop.py start", env=env, stream=io.StringIO()) is True
    (repo / ".cursor" / "rules").mkdir(parents=True)
    (repo / ".cursor" / "rules" / "sdlc.mdc").write_text("run the %s skills\n" % OLD)
    env = _host(tmp_path / "h2", claude={"enabledPlugins": {OLD_ID: False}}, installed=True,
                codex='[plugins."%s"]\nenabled = false\n' % OLD_ID)
    report = cx.assess(repo / ".sdlc", env=env)
    assert report.active == [], report.active
    assert cx.gate(repo / ".sdlc", "loop.py start", env=env, stream=io.StringIO()) is True


def _argv(*parts):
    return ("\0".join(parts) + "\0").encode()


SIGMA_SCRIPT = "/home/u/.claude/plugins/cache/sigma/sigma/1.0.0/skills/agrim-loop/scripts/watch_daemon.py"
#: The old plugin's real install layout: `<cache>/<marketplace>/<plugin>/<version>/skills/...`.
OLD_SKILL = "sdlc" + "-loop"
OLD_SCRIPT = ("/home/u/.claude/plugins/cache/%s/%s/1.4.24/skills/%s/scripts/watch_daemon.py"
              % (OLD, OLD, OLD_SKILL))


@pytest.mark.parametrize("cmdline,expected", [
    # review block #2, finding 1: the old name only in the .sdlc ARGUMENT (the user's repo dir)
    (_argv("python3", SIGMA_SCRIPT, "/work/my-%s-migration/.sdlc" % OLD), False),
    (_argv("python3", SIGMA_SCRIPT, "/work/%s/.sdlc" % OLD), False),        # exact dir, still the arg
    (_argv("/opt/%s/bin/python3" % OLD, SIGMA_SCRIPT, "/r/.sdlc"), False),  # the interpreter path
    (_argv("python3", "/work/my-%s-fork/scripts/watch_daemon.py" % OLD, "/r/.sdlc"), False),
    (_argv("python3", "/r/.sdlc"), False),                                  # no watcher script at all
    (_argv("python3", OLD_SCRIPT, "/r/.sdlc"), True),                       # the old plugin, installed
    (_argv("python3", OLD_WATCHER_ARGV[0], "/r/.sdlc"), True),
    (_argv("python.exe", "C:\\Users\\u\\.claude\\plugins\\cache\\%s\\%s\\1.4.24\\skills\\%s"
           "\\scripts\\watch_daemon.py" % (OLD, OLD, OLD_SKILL), "C:\\r\\.sdlc"), True),
])
def test_cmdline_matches_only_a_directory_of_the_watcher_script(cx, monkeypatch, cmdline,
                                                                  expected):
    _fake_proc(monkeypatch, cmdline)
    assert cx._cmdline_names_old(4242) is expected


def test_sigma_watcher_in_a_repo_named_after_the_old_plugin_is_not_refused(tmp_path, cx, live_pid,
                                                                           monkeypatch):
    """Review block #2, finding 1, the repro: a Sigma watcher from before this release (no
    watch.owner), fresh heartbeat, in `.../my-<old>-migration/`, argv carrying that path as its
    .sdlc argument. It must be a NOTE and the gate must admit."""
    repo = tmp_path / ("my-%s-migration" % OLD)
    (repo / ".sdlc" / "state").mkdir(parents=True)
    (repo / ".sdlc" / "config.json").write_text("{}\n")
    _fake_watcher(repo / ".sdlc", live_pid)
    _fake_proc(monkeypatch, _argv("python3", SIGMA_SCRIPT, str(repo / ".sdlc")))
    env = _host(tmp_path)
    report = cx.assess(repo / ".sdlc", env=env)
    assert report.active == [] and _kinds(report, cx.NOTE) == ["watcher"]
    assert cx.gate(repo / ".sdlc", "loop.py start", env=env, stream=io.StringIO()) is True


def test_unreadable_cmdline_note_says_it_cannot_tell(tmp_path, cx, live_pid, monkeypatch):
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", live_pid)
    _fake_proc(monkeypatch, None)
    [note] = cx.assess(repo / ".sdlc", env=_host(tmp_path)).notes
    assert "cannot tell who started the running watcher" in note.detail
    _fake_proc(monkeypatch, PROC["linux-plain"])
    [note] = cx.assess(repo / ".sdlc", env=_host(tmp_path)).notes
    assert "cannot tell" not in note.detail


def test_installed_here_compares_normalised_paths(tmp_path, cx, monkeypatch):
    repo = _repo(tmp_path)
    env = _host(tmp_path, claude=ENABLED)
    _install_list(pathlib.Path(env["CLAUDE_CONFIG_DIR"]),
                  {OLD_ID: [{"scope": "project", "projectPath": str(repo).upper()}]})
    monkeypatch.setattr(cx.os.path, "normcase", lambda p: p.lower())      # a case-folding OS
    assert _kinds(cx.assess(repo / ".sdlc", env=env), cx.ACTIVE) == ["claude-enabled"]


def test_live_unmarked_watcher_alone_is_a_note(tmp_path, cx, live_pid):
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", live_pid)
    report = cx.assess(repo / ".sdlc", env=_host(tmp_path))
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


BASH_HOOK = pytest.mark.skipif(shutil.which("bash") is None or sys.platform.startswith("win"),
                               reason="the hook is a bash script (not run on Windows)")


def _hook(repo, env):
    """Run the hook the way hooks.json does. `kg.py`, the one helper it spawns, finds no
    knowledge_graph block in these fixtures and says nothing; the wizard is quiet because
    config.json exists. Returns the single additionalContext (one JSON document, or it raises)."""
    p = subprocess.run(["bash", str(HOOK)], input="{}", capture_output=True, text=True,
                       env={**env, "CLAUDE_PROJECT_DIR": str(repo)}, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def _stale_ledger(repo):
    (repo / ".sdlc" / "config.json").write_text(json.dumps(
        {"ledger": {"enabled": True, "watch": {"interval_seconds": 900}}}))
    hb = repo / ".sdlc" / "state" / "watch.heartbeat"
    hb.write_text("")
    old = time.time() - 3600
    os.utime(hb, (old, old))


@BASH_HOOK
def test_session_start_refusal_still_reaches_the_watcher_staleness_check(tmp_path):
    """Review block #2, finding 2: the coexist message is informational in a hook. It must not
    stop the ledger-watcher staleness warning (AGENTS.md LIVENESS)."""
    repo = _repo(tmp_path)
    _stale_ledger(repo)
    ctx = _hook(repo, _env(**_host(tmp_path, claude=ENABLED)))
    assert "claude plugin disable %s" % OLD_ID in ctx
    assert "looks stale" in ctx and "/agrim-doctor" in ctx


@BASH_HOOK
def test_session_start_override_is_one_line_and_falls_through(tmp_path):
    repo = _repo(tmp_path)
    _stale_ledger(repo)
    ctx = _hook(repo, _env(SIGMA_ALLOW_COEXIST="1", **_host(tmp_path, claude=ENABLED)))
    assert "coexistence override in effect" in ctx
    assert "refused" not in ctx and "claude plugin disable" not in ctx
    assert "looks stale" in ctx
    [line] = [ln for ln in ctx.splitlines() if "coexistence override" in ln]
    assert "SIGMA_ALLOW_COEXIST=1" in line


@BASH_HOOK
def test_session_start_policy_brief_still_runs_beside_the_message(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "config.json").write_text('{"session_start":{"enabled":true}}')
    ctx = _hook(repo, _env(**_host(tmp_path, claude=ENABLED)))
    assert "claude plugin disable" in ctx and "reviewer is never the author" in ctx


def test_cli_exit_codes(tmp_path):
    repo = _repo(tmp_path)
    assert _run([LOOP / "coexist.py", "check", repo / ".sdlc"], _env(**_host(tmp_path))).returncode == 0
    p = _run([LOOP / "coexist.py", "check", repo / ".sdlc"], _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 2 and "claude plugin disable" in p.stdout


def test_cli_check_under_the_override_is_informational(tmp_path):
    repo = _repo(tmp_path)
    p = _run([LOOP / "coexist.py", "check", repo / ".sdlc"],
             _env(SIGMA_ALLOW_COEXIST="1", **_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "override in effect" in p.stdout and "refused" not in p.stdout
    assert "Claude Code has %s enabled" % OLD_ID in p.stdout

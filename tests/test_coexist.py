"""#240/#314: Sigma and the plugin under its previous name on one repository.

`skills/agrim-loop/scripts/coexist.py` is the one detector. Since #314 (owner direction: Sigma
replaces that plugin in place) the other plugin's presence is a NOTICE, never a refusal: every
surface proceeds and says ONE line naming the uninstall command, once per run; `SIGMA_ALLOW_COEXIST=1`
silences it. What stays impossible is enforced elsewhere and tested here: the shared watcher lock
admits one watcher (a foreign one is named with the polite `watch.stop` lever, never signalled),
and `migrate.py --apply` refuses while a watcher is live.

Every old-name spelling is built from fragments (`OLD`): the previous name is a guarded private
name in this tree (`tests/test_no_private_names.py`). Fake host inventories live under tmp; the
autouse `_no_host_plugin_inventory` fixture (conftest) keeps the developer's real one out.
Surface tests run each surface on the gesture its docs give (`sdlc_init.py <repo>`,
`loop.py start <sdlc>`, `watch_daemon.py <sdlc>`, `migrate.py <sdlc> --apply`, `status.py <sdlc>`,
`bash hooks/session_start.sh`).
"""
import contextlib
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
INIT_FLOW = ROOT / "skills" / "agrim-init" / "scripts" / "init_flow.py"
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


def _git_init(repo):
    """#229: init refuses a directory that is not a git work tree, so the fixtures are one."""
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)


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
    text = cx.message(report)
    assert "refused" not in text and "Sigma is handling this repository" in text
    assert "claude plugin uninstall %s" % OLD_ID in text
    assert "migrate.py %s --apply" % (repo / ".sdlc") in text
    assert "SIGMA_ALLOW_COEXIST=1" in text                     # named as the way to silence it


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


#: pid -> Popen for the fake watchers, so a test can prove one was never signalled: `poll()` is
#: None only while it runs (a signalled child lingers as a zombie a bare pid probe still finds).
_PROCS = {}


def _never_signalled(pid):
    assert _PROCS[pid].poll() is None, "pid %d was signalled (exit %r)" % (pid, _PROCS[pid].poll())


def _spawn(*argv):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", *argv])
    _PROCS[proc.pid] = proc
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


# --------------------------------------------------------------------------- gate: notice, not refusal (#314)

#: The one notice's fixed phrase, and the uninstall command it names for a Claude Code install.
HANDLING = "Sigma is handling this repository"
UNINSTALL = "claude plugin uninstall %s" % OLD_ID


def _notices(text):
    return [ln for ln in text.splitlines() if HANDLING in ln]


def test_gate_admits_with_one_notice_naming_the_uninstall_command(tmp_path, cx):
    repo = _repo(tmp_path)
    env = _host(tmp_path, claude=ENABLED)
    out = io.StringIO()
    assert cx.gate(repo / ".sdlc", "loop.py start", env=env, stream=out) is True
    [line] = out.getvalue().splitlines()
    assert HANDLING in line and UNINSTALL in line and "refused" not in line


def test_project_scoped_install_names_its_scope_in_the_uninstall_command(tmp_path, cx):
    repo = _repo(tmp_path)
    env = _host(tmp_path, claude=ENABLED)
    _install_list(pathlib.Path(env["CLAUDE_CONFIG_DIR"]),
                  {OLD_ID: [{"scope": "project", "projectPath": str(repo)}]})
    out = io.StringIO()
    assert cx.gate(repo / ".sdlc", "s", env=env, stream=out) is True
    assert UNINSTALL + " --scope project" in out.getvalue()


def test_cut_over_steps_disable_first_and_name_the_real_marketplace(tmp_path, cx):
    """#314: the checklist stops the old plugin on this repository BEFORE the conversion, and the
    optional marketplace removal names the marketplace from the plugin id -- never assumes it
    equals the plugin's own name."""
    repo = _repo(tmp_path)
    pid = OLD + "@acme-market"
    env = _host(tmp_path, claude={"enabledPlugins": {pid: True}})
    text = cx.message(cx.assess(repo / ".sdlc", env=env, managed=None))
    lines = text.splitlines()
    disable = next(i for i, ln in enumerate(lines) if "claude plugin disable %s --scope local" % pid
                   in ln)
    apply_at = next(i for i, ln in enumerate(lines) if "--apply" in ln)
    assert disable < apply_at and CAVEAT in text
    assert "claude plugin marketplace remove acme-market" in text
    assert "marketplace remove %s" % OLD not in text


def test_printed_commands_are_quoted_for_the_platform(tmp_path, cx, monkeypatch):
    """Every printed command goes through the one quoting helper: a path with a space pastes on
    POSIX (single quotes) and on Windows (`list2cmdline`'s double quotes)."""
    spaced = tmp_path / "my repo" / ".sdlc"
    assert cx.shell_command("python3", spaced, platform="linux") == "python3 '%s'" % spaced
    assert cx.shell_command("python3", spaced, platform="win32") == 'python3 "%s"' % spaced
    monkeypatch.setattr(cx.sys, "platform", "win32")
    assert '"%s"' % spaced in cx.migrate_command(spaced, "--apply")
    report = cx.Report(str(spaced), (cx.Signal("claude-enabled", cx.ACTIVE, "d", "f", OLD_ID),))
    assert '"%s"' % spaced in cx.message(report)


def test_codex_notice_names_the_config_edit(tmp_path, cx):
    repo = _repo(tmp_path)
    env = _host(tmp_path, codex='[plugins."%s"]\nenabled = true\n' % OLD_ID)
    out = io.StringIO()
    assert cx.gate(repo / ".sdlc", "s", env=env, stream=out) is True
    [line] = out.getvalue().splitlines()
    assert "config.toml" in line and OLD_ID in line


def test_the_override_silences_the_notice_and_only_exactly_1_does(tmp_path, cx):
    repo = _repo(tmp_path)
    env = _host(tmp_path, claude=ENABLED)
    out = io.StringIO()
    assert cx.gate(repo / ".sdlc", "s", env={**env, "SIGMA_ALLOW_COEXIST": "1"}, stream=out)
    assert out.getvalue() == ""
    for value in ("true", "yes", "0", ""):                    # exactly `1`, nothing derived
        out = io.StringIO()
        assert cx.gate(repo / ".sdlc", "s", env={**env, "SIGMA_ALLOW_COEXIST": value}, stream=out)
        assert len(_notices(out.getvalue())) == 1
    out = io.StringIO()                                       # never read under the old prefix
    assert cx.gate(repo / ".sdlc", "s", env={**env, OLD.upper() + "_ALLOW_COEXIST": "1"}, stream=out)
    assert len(_notices(out.getvalue())) == 1


def test_per_verb_surfaces_say_it_once_per_run(tmp_path, cx):
    """A start surface always says it (and marks the run); a per-verb surface (`once=True`: the
    watcher spawn, claim, record) stays quiet while that mark is fresh, and speaks again after it
    ages out -- one notice per run, not one per verb (#251)."""
    repo = _repo(tmp_path)
    env = _host(tmp_path, claude=ENABLED)
    first, again, start = io.StringIO(), io.StringIO(), io.StringIO()
    assert cx.gate(repo / ".sdlc", "loop.py claim", env=env, stream=first, once=True)
    assert cx.gate(repo / ".sdlc", "loop.py record", env=env, stream=again, once=True)
    assert len(_notices(first.getvalue())) == 1 and again.getvalue() == ""
    assert cx.gate(repo / ".sdlc", "loop.py start", env=env, stream=start)
    assert len(_notices(start.getvalue())) == 1               # a start surface always says it
    marker = repo / ".sdlc" / "state" / cx.NOTICE_FILE
    old = time.time() - cx.NOTICE_TTL_SECONDS - 60
    os.utime(marker, (old, old))
    later = io.StringIO()
    assert cx.gate(repo / ".sdlc", "loop.py claim", env=env, stream=later, once=True)
    assert len(_notices(later.getvalue())) == 1


def test_no_notice_and_no_marker_when_nothing_is_active(tmp_path, cx):
    repo = _repo(tmp_path)
    out = io.StringIO()
    assert cx.gate(repo / ".sdlc", "s", env=_host(tmp_path), stream=out, once=True)
    assert out.getvalue() == ""
    assert not (repo / ".sdlc" / "state" / cx.NOTICE_FILE).exists()


def test_doctor_row_is_a_warning_naming_the_uninstall_command(tmp_path, cx):
    repo = _repo(tmp_path)
    assert cx.doctor_row(repo / ".sdlc", env=_host(tmp_path))["ok"] is True
    row = cx.doctor_row(repo / ".sdlc", env=_host(tmp_path, claude=ENABLED))
    assert row["ok"] is True and row["name"].startswith("coexistence: WARN")
    assert UNINSTALL in row["fix"]


def test_foreign_owner_marker_is_a_notice_and_sigma_takes_ownership(tmp_path):
    repo = _scaffolded(tmp_path)
    owner = repo / ".sdlc" / "state" / "owner.json"
    owner.write_text(json.dumps({"plugin": "other"}))
    p = _run([LOOP / "loop.py", "start", repo / ".sdlc", "--session-pid", os.getpid()],
             _env(**_host(tmp_path)))
    assert p.returncode == 0, p.stdout + p.stderr
    assert len(_notices(p.stderr)) == 1
    assert json.loads(owner.read_text())["plugin"] == "sigma"


# --------------------------------------------------------------------------- surfaces (gestures)


def _run(argv, env, cwd=None):
    return subprocess.run([sys.executable, *map(str, argv)], capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=120)


def test_init_proceeds_with_one_notice_and_records_the_owner(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    p = _run([INIT, repo], _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0, p.stdout + p.stderr
    assert len(_notices(p.stdout + p.stderr)) == 1 and UNINSTALL in p.stderr
    assert "refused" not in p.stdout + p.stderr
    assert json.loads((repo / ".sdlc" / "state" / "owner.json").read_text())["plugin"] == "sigma"


def test_init_proceeds_silently_when_clear(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    p = _run([INIT, repo], _env(**_host(tmp_path)))
    assert p.returncode == 0, p.stderr
    assert _notices(p.stdout + p.stderr) == []
    assert json.loads((repo / ".sdlc" / "state" / "owner.json").read_text())["plugin"] == "sigma"


def test_init_flow_proceeds_with_one_notice(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.test",
                    "commit", "-q", "--allow-empty", "-m", "initial"], check=True)
    p = _run([INIT_FLOW, repo, "--mode", "local-goals", "--local-only", "--no-verify"],
             _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0, p.stdout + p.stderr
    assert len(_notices(p.stdout + p.stderr)) == 1
    assert (repo / ".sdlc" / "config.json").exists()


def _scaffolded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    assert _run([INIT, repo], _env(**_host(tmp_path / "clear"))).returncode == 0
    return repo


def test_loop_start_proceeds_with_one_notice(tmp_path):
    repo = _scaffolded(tmp_path)
    (repo / ".sdlc" / "state" / "owner.json").unlink()
    p = _run([LOOP / "loop.py", "start", repo / ".sdlc", "--session-pid", os.getpid()],
             _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0, p.stdout + p.stderr
    assert len(_notices(p.stderr)) == 1 and "refused" not in p.stderr
    assert json.loads((repo / ".sdlc" / "state" / "owner.json").read_text())["plugin"] == "sigma"


def test_loop_start_proceeds_silently_when_clear(tmp_path):
    repo = _scaffolded(tmp_path)
    p = _run([LOOP / "loop.py", "start", repo / ".sdlc", "--session-pid", os.getpid()],
             _env(**_host(tmp_path)))
    assert p.returncode == 0, p.stderr
    assert _notices(p.stderr) == [] and "refused" not in p.stderr


def _claimable(repo):
    goals = repo / ".sdlc" / "goals"
    goals.mkdir(exist_ok=True)
    goal = goals / "0042.md"
    goal.write_text("---\nid: 0042\nstatus: pending\n---\nx\n")
    return goal


def test_claim_and_record_proceed_with_one_notice_per_run(tmp_path):
    """#251: the /agrim-goal admission path (claim, record) says the same single notice -- not a
    refusal, and not once per verb."""
    repo = _scaffolded(tmp_path)
    goal = _claimable(repo)
    env = _env(**_host(tmp_path, claude=ENABLED))
    p = _run([LOOP / "loop.py", "claim", repo / ".sdlc", goal], env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert len(_notices(p.stderr)) == 1
    p = _run([LOOP / "loop.py", "record", repo / ".sdlc", goal, "done"], env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert _notices(p.stderr) == []                           # the same run: said already
    assert "status: done" in goal.read_text()


@pytest.mark.parametrize("verb", ["claim", "record"])
def test_each_per_verb_surface_is_quiet_while_the_run_mark_is_fresh(tmp_path, verb):
    """#251: EACH of claim and record honours the per-run mark on its own. The test above runs
    claim first, so it cannot see claim lose `once=True` (claim then speaks first anyway); here
    the mark is already fresh, so either verb speaking at all is the defect."""
    repo = _scaffolded(tmp_path)
    goal = _claimable(repo)
    (repo / ".sdlc" / "state" / "coexist.notice").write_text("%d\n" % int(time.time()))
    argv = ([LOOP / "loop.py", "claim", repo / ".sdlc", goal] if verb == "claim"
            else [LOOP / "loop.py", "record", repo / ".sdlc", goal, "done"])
    p = _run(argv, _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0, p.stdout + p.stderr
    assert _notices(p.stderr) == []


def test_watcher_starts_with_the_notice_in_its_log(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "state" / "watch.stop").write_text("")
    p = _run([LOOP / "watch_daemon.py", repo / ".sdlc"],
             _env(SIGMA_WATCH_SLEEP_SCALE="0", **_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "stop-file present" in p.stdout                    # it took the lock and ran
    log = (repo / ".sdlc" / "state" / "watch.log").read_text()
    assert len(_notices(log)) == 1 and "refused" not in log
    assert not (repo / ".sdlc" / "state" / "watch.owner").exists()     # removed with the pidfile


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


@pytest.mark.parametrize("override", [None, "1"])
def test_a_live_foreign_watcher_is_never_doubled_nor_signalled(tmp_path, old_watcher_pid,
                                                                override):
    """The shared lock admits one watcher. A Sigma watcher start and a loop start beside the old
    plugin's live watcher: nothing new holds the lock, the pidfile still names the foreign pid,
    the process is still alive (never signalled), and the message names the polite lever."""
    repo = _repo(tmp_path)
    _fake_watcher(repo / ".sdlc", old_watcher_pid)
    extra = {"SIGMA_ALLOW_COEXIST": override} if override else {}
    env = _env(SIGMA_WATCH_SLEEP_SCALE="0", **extra, **_host(tmp_path, claude=ENABLED))
    p = _run([LOOP / "watch_daemon.py", repo / ".sdlc"], env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "already running (pid %d)" % old_watcher_pid in p.stdout
    assert "not started by Sigma" in p.stdout
    assert str(repo / ".sdlc" / "state" / "watch.stop") in p.stdout
    assert (repo / ".sdlc" / "state" / "watch.pid").read_text().strip() == str(old_watcher_pid)
    _never_signalled(old_watcher_pid)
    p = _run([LOOP / "loop.py", "start", repo / ".sdlc", "--session-pid", os.getpid()], env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (repo / ".sdlc" / "state" / "watch.pid").read_text().strip() == str(old_watcher_pid)
    _never_signalled(old_watcher_pid)


def test_ensure_watcher_spawns_beside_the_old_plugin(tmp_path, monkeypatch, capsys):
    """#314: the watcher spawn is no longer withheld; the shared lock decides who runs. The notice
    is said once and the spawn happens both times."""
    loop = _load(LOOP / "loop.py", "loop_under_test")
    repo = _repo(tmp_path)
    for key, value in _host(tmp_path, claude=ENABLED).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SIGMA_ALLOW_COEXIST", raising=False)
    monkeypatch.setattr(loop.ledger, "enabled", lambda cfg: True)
    monkeypatch.setattr(loop, "_load", lambda name, _real=loop._load: (
        type("S", (), {"is_worktree": staticmethod(lambda d: True)}) if name == "sync" else _real(name)))
    spawned = []
    loop._ensure_watcher(str(repo / ".sdlc"), {}, spawn=lambda: spawned.append(1))
    loop._ensure_watcher(str(repo / ".sdlc"), {}, spawn=lambda: spawned.append(1))
    assert spawned == [1, 1]
    assert len(_notices(capsys.readouterr().err)) == 1


def _old_schema_repo(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "state" / "landing").mkdir()
    record = repo / ".sdlc" / "state" / "landing" / "7.json"
    record.write_text(json.dumps({"schema": OLD + "/landing@1", "goal": 7}) + "\n")
    return repo, record


REPLACE = "--replace-old-plugin"
CAVEAT = ("the old plugin cannot read Sigma's registry; if it starts a goal in this repo "
          "afterwards it writes unit records Sigma can only merge as partial deltas")
DISABLE_LOCAL = "claude plugin disable %s --scope local" % OLD_ID


def _disable_here(repo):
    """What `claude plugin disable <id> --scope local` writes (verified against Claude Code's own
    CLI in a fake HOME, #314 evidence): `"<id>": false` in this repository's settings.local.json."""
    (repo / ".claude").mkdir(exist_ok=True)
    (repo / ".claude" / "settings.local.json").write_text(
        json.dumps({"enabledPlugins": {OLD_ID: False}}), encoding="utf-8")


def test_migrate_dry_run_proceeds_and_apply_waits_for_the_old_plugin_to_stop(tmp_path):
    """#314 review block #1: the conversion -- and only it -- waits while the old plugin can still
    run here. Without the acknowledgement: the notice, the exact reason, the exact next steps, the
    dry-run listing, nothing written, exit 2. With it: a backup first, then the rewrite. After the
    documented disable step: no flag needed."""
    repo, record = _old_schema_repo(tmp_path)
    before = record.read_bytes()
    env = _env(**_host(tmp_path, claude=ENABLED))
    p = _run([MIGRATE, repo / ".sdlc"], env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert record.read_bytes() == before and "would change" in p.stdout
    p = _run([MIGRATE, repo / ".sdlc", "--apply"], env)
    assert p.returncode == 2, p.stdout + p.stderr
    assert record.read_bytes() == before
    assert len(_notices(p.stdout + p.stderr)) == 1
    out = p.stdout
    assert "refused --apply" in out and CAVEAT[1:] in out
    assert DISABLE_LOCAL in out and "would change" in out
    assert "migrate.py %s --apply %s" % (repo / ".sdlc", REPLACE) in out
    assert not (repo / ".sdlc" / "state" / "backup").exists()
    p = _run([MIGRATE, repo / ".sdlc", "--apply", REPLACE], env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(record.read_text())["schema"] == "sigma/landing@1"
    # the documented order instead: disable it on this repository, then --apply needs no flag
    repo2, record2 = _old_schema_repo(tmp_path / "second")
    _disable_here(repo2)
    p = _run([MIGRATE, repo2 / ".sdlc", "--apply"], _env(**_host(tmp_path / "second",
                                                                 claude=ENABLED)))
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(record2.read_text())["schema"] == "sigma/landing@1"


def test_replace_flag_is_spelled_once(cx):
    migrate = _load(MIGRATE, "migrate_flag")
    assert migrate._REPLACE == cx.REPLACE_FLAG == REPLACE
    assert migrate._parse(["m", ".sdlc", REPLACE]) is None          # only meaningful with --apply
    assert migrate._parse(["m", ".sdlc", "--apply", REPLACE]) == (".sdlc", True, True)


# --------------------------------------------------------------------------- the reviewer's sequence


#: The previous plugin's skill directories, from fragments like `OLD` (guarded private names).
OLD_SKILL = "sdlc" + "-%s"
#: Opt-in: a local clone of the previous plugin's repository (read-only; `git archive` only) and
#: the release to run, so the reviewer's sequences also run against its REAL code, not only the
#: stand-in. Unset -- as in CI -- the `real` parameter skips and the stand-in still runs.
PREDECESSOR_GIT = os.environ.get("SIGMA_TEST_PREDECESSOR_GIT")
PREDECESSOR_REV = os.environ.get("SIGMA_TEST_PREDECESSOR_REV") or "9d2f40fd"   # its 1.4.25


def _predecessor_tree(tmp_path, kind):
    """-> the `skills/` root of a runnable copy of the previous plugin's registry code.

    `stand-in`: Sigma's own `agrim-loop` + `agrim-define` scripts, copied, with that plugin's
    schema id and its STRICT schema check (it never learned to read Sigma's id) -- the two lines in
    which its 1.4.x `feature_registry.py` differs for this purpose. Its pick, owner claim and
    `set-priority` are Sigma's ports of the same code, so they run unchanged. Sigma's coexist
    module is left out, so the copy takes no Sigma backup (its hook fails open).

    `real`: that plugin's own scripts at `PREDECESSOR_REV`, extracted with `git archive` from the
    clone at `SIGMA_TEST_PREDECESSOR_GIT` (skipped when unset)."""
    root = tmp_path / ("predecessor-" + kind) / "skills"
    if kind.startswith("real"):
        if not PREDECESSOR_GIT:
            pytest.skip("SIGMA_TEST_PREDECESSOR_GIT is not set (a local clone of the previous "
                        "plugin); the stand-in covers the same sequences")
        root.mkdir(parents=True)
        rev = kind.partition("@")[2] or PREDECESSOR_REV        # `real@<rev>` pins one release
        dirs = ["skills/%s/scripts" % (OLD_SKILL % d) for d in ("loop", "define")]
        tar = subprocess.run(["git", "-C", PREDECESSOR_GIT, "archive", rev, *dirs],
                             capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(root.parent)], input=tar, check=True)
        return root, OLD_SKILL % "loop", OLD_SKILL % "define"
    loop = root / "agrim-loop" / "scripts"
    shutil.copytree(LOOP, loop, ignore=shutil.ignore_patterns("__pycache__", "coexist.py"))
    shutil.copytree(ROOT / "skills" / "agrim-define" / "scripts", root / "agrim-define" / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    reg = loop / "feature_registry.py"
    text = reg.read_text(encoding="utf-8")
    schema, check = 'SCHEMA = "sigma/features@1"', 'if not legacy.schema_is(doc.get("schema"), SCHEMA):'
    # ...and it never learned Sigma's #314 delta rule either: in its own schema its records are
    # shard-wins, as they always were (#326: the stand-in must read ITS index as its own).
    delta = "    if not (sigma_index and legacy.is_legacy_schema(schema)):\n        return None\n"
    assert text.count(schema) == 1 and text.count(check) == 1 and text.count(delta) == 1
    reg.write_text(text.replace(schema, 'SCHEMA = "%s/features@1"' % OLD)
                   .replace(check, 'if doc.get("schema") != SCHEMA:')
                   .replace(delta, "    return None\n"), encoding="utf-8")
    return root, "agrim-loop", "agrim-define"


class _Predecessor:
    """The previous plugin's own entry points, loaded from a copy: a pick (`sync_at_pick`, with a
    fake git answering `ls-remote`/`get-url`), the owner claim a pick makes (`claim_at_pick`, the
    call `gate_at_pick -> _claim_here` reaches), and `define.py set-priority`."""

    def __init__(self, tmp_path, kind="stand-in"):
        root, loop, define = _predecessor_tree(tmp_path, kind)
        tag = "predecessor_%s_" % "".join(c if c.isalnum() else "_" for c in kind)
        self.fs = _load(root / loop / "scripts" / "feature_sync.py", tag + "feature_sync")
        self.fo = _load(root / loop / "scripts" / "feature_owner.py", tag + "feature_owner")
        self.define = _load(root / define / "scripts" / "define.py", tag + "define")

    @staticmethod
    def _git(cwd, argv):
        if argv[:2] == ["git", "ls-remote"]:
            return "0" * 40 + "\trefs/heads/feature/alpha\n"
        if argv[:3] == ["git", "remote", "get-url"]:
            return "https://github.com/o/r.git\n"
        raise RuntimeError("unexpected git call %r" % (argv,))

    def pick(self, repo, goal):
        return self.fs.sync_at_pick(str(repo / ".sdlc"), {}, str(goal), "alpha", run=self._git)

    def claim(self, repo, goal, actor):
        return self.fo.claim_at_pick(str(repo / ".sdlc"), str(goal), "alpha", "o/r", actor)

    def set_priority(self, repo, priority):
        return self.define.set_priority(str(repo / ".sdlc"), "alpha", priority)


def _predecessor(tmp_path):
    return _Predecessor(tmp_path)


ALPHA = {"title": "Alpha unit", "owner": "alice", "open": True, "tracking_issue": "#200",
         "priority": "p1", "repos": {"o/r": {"branch": "feature/alpha", "owner": "alice",
                                             "authorized": True, "goals": [201, 202]}}}


def _adopted(tmp_path):
    """A repository the old plugin adopted: unit `alpha` lives ONLY in index.json, old schema id."""
    repo = _repo(tmp_path)
    _git_init(repo)
    features = repo / ".sdlc" / "features"
    features.mkdir()
    (features / "index.json").write_text(json.dumps(
        {"schema": OLD + "/features@1", "features": {"alpha": ALPHA}}) + "\n", encoding="utf-8")
    return repo


def _converted(tmp_path):
    """The same repository after conversion: `alpha` only in an index in Sigma's schema."""
    repo = _adopted(tmp_path)
    index = repo / ".sdlc" / "features" / "index.json"
    index.write_text(index.read_text().replace(OLD + "/features@1", "sigma/features@1"))
    return repo


def _predecessor_starts_a_goal(pred, repo, goal):
    report = pred.pick(repo, goal)
    assert report["recorded"] is True, report
    return report


def _alpha(entry):
    return (entry["title"], entry["owner"], entry["priority"], entry["tracking_issue"],
            entry["repos"]["o/r"]["branch"], entry["repos"]["o/r"]["authorized"],
            entry["repos"]["o/r"]["goals"])


KEPT = ("Alpha unit", "alice", "p1", "#200", "feature/alpha", True)


def test_the_documented_cut_over_with_the_old_plugin_still_enabled_loses_nothing(tmp_path):
    """#314 review block #1, the reviewer's sequence end to end: migrate with the old plugin still
    enabled; the old plugin starts a goal in a unit that exists only in the index (it reads Sigma's
    index as EMPTY and writes a record in ITS schema); Sigma's `show` and `fold` serve and bake the
    index entry with that record merged as a delta. Every layer is on the path: the flag gate, the
    backup, the reader's legacy-delta merge, fold's monotonic check, and the repair."""
    repo = _adopted(tmp_path)
    sdlc = repo / ".sdlc"
    index = sdlc / "features" / "index.json"
    original = index.read_bytes()
    env = _env(**_host(tmp_path, claude=ENABLED))
    p = _run([MIGRATE, sdlc, "--apply"], env)                 # 1. the gate: nothing converted
    assert p.returncode == 2 and index.read_bytes() == original, p.stdout + p.stderr
    p = _run([MIGRATE, sdlc, "--apply", REPLACE], env)        # 2. acknowledged: backup, convert
    assert p.returncode == 0, p.stdout + p.stderr
    [backup] = list((sdlc / "state" / "backup").glob("features-*"))
    assert (backup / "index.json").read_bytes() == original
    assert json.loads(index.read_text())["schema"] == "sigma/features@1"
    report = _predecessor_starts_a_goal(_predecessor(tmp_path), repo, 203)  # 3. the old plugin
    assert report["created"] is True                          # it could not read Sigma's index
    record = json.loads((sdlc / "features" / "units" / "alpha.json").read_text())
    assert record["schema"] == OLD + "/features@1" and record["features"]["alpha"]["title"] == ""
    show = _run([LOOP / "feature_sync.py", "show", sdlc], env)  # 4. Sigma's reader: nothing lost
    assert show.returncode == 0, show.stderr
    assert _alpha(json.loads(show.stdout)["alpha"]) == KEPT + ([201, 202, 203],)
    assert "DELTA" in show.stderr and "feature_sync.py repair" in show.stderr
    assert str(backup) in show.stderr and "cut-over window" in show.stderr
    fold = _run([LOOP / "feature_sync.py", "fold", sdlc], env)  # 5. fold bakes the merge
    assert fold.returncode == 0, fold.stdout + fold.stderr
    assert _alpha(json.loads(index.read_text())["features"]["alpha"]) == KEPT + ([201, 202, 203],)
    fixed = _run([LOOP / "feature_sync.py", "repair", sdlc], env)  # 6. the documented recovery
    assert fixed.returncode == 0 and "repaired alpha" in fixed.stdout, fixed.stdout + fixed.stderr
    record = json.loads((sdlc / "features" / "units" / "alpha.json").read_text())
    assert record["schema"] == "sigma/features@1"
    assert _alpha(record["features"]["alpha"]) == KEPT + ([201, 202, 203],)
    again = _run([LOOP / "feature_sync.py", "repair", sdlc], env)
    assert "nothing to repair" in again.stdout


def test_without_migrating_sigma_writes_are_backed_up_and_the_delta_merge_holds(tmp_path):
    """The same exposure with no migrate at all: Sigma's own registry write (here a fold) turns
    index.json into Sigma's schema. The one-time backup is taken BEFORE that first write, and the
    old plugin's record is still only ever merged onto the index entry as a delta."""
    repo = _adopted(tmp_path)
    sdlc = repo / ".sdlc"
    original = (sdlc / "features" / "index.json").read_bytes()
    env = _env(**_host(tmp_path, claude=ENABLED))
    p = _run([LOOP / "feature_sync.py", "fold", sdlc], env)
    assert p.returncode == 0, p.stdout + p.stderr
    [backup] = list((sdlc / "state" / "backup").glob("features-*"))
    assert (backup / "index.json").read_bytes() == original and "sigma: backup:" in p.stderr
    _predecessor_starts_a_goal(_predecessor(tmp_path), repo, 203)
    show = _run([LOOP / "feature_sync.py", "show", sdlc], env)
    assert _alpha(json.loads(show.stdout)["alpha"]) == KEPT + ([201, 202, 203],)
    assert _run([LOOP / "feature_sync.py", "fold", sdlc], env).returncode == 0
    p = _run([LOOP / "feature_sync.py", "repair", sdlc], env)          # a second Sigma write
    assert p.returncode == 0 and "repaired alpha" in p.stdout, p.stdout + p.stderr
    assert list((sdlc / "state" / "backup").glob("features-*")) == [backup]   # one-time
    assert "sigma: backup:" not in p.stderr


def test_the_backup_is_taken_once_only_beside_a_runnable_old_plugin_and_migrate_skips_it(tmp_path,
                                                                                        cx):
    repo = _adopted(tmp_path)
    sdlc = repo / ".sdlc"
    out = io.StringIO()
    assert cx.protect_features(sdlc, env=_host(tmp_path / "clear"), stream=out,
                               managed=None) is None
    assert not (sdlc / "state" / "backup").exists() and out.getvalue() == ""
    env = _host(tmp_path, claude=ENABLED)
    first = cx.protect_features(sdlc, env=env, stream=out, managed=None)
    assert first is not None and (first / "index.json").is_file()
    assert str(first) in out.getvalue() and CAVEAT.split(";")[0] in out.getvalue()
    assert "cut-over window" in out.getvalue() and "feature_sync.py repair" in out.getvalue()
    assert cx.protect_features(sdlc, env=env, stream=out, managed=None) is None
    assert cx.existing_backup(sdlc) == first
    p = _run([MIGRATE, sdlc], _env(**_host(tmp_path / "clear")))   # the copy is never rewritten
    assert "state/backup" not in p.stdout and "features/index.json" in p.stdout


def test_the_backup_is_bounded(tmp_path, cx, monkeypatch):
    repo = _adopted(tmp_path)
    monkeypatch.setattr(cx, "BACKUP_FILE_CAP", 0)
    path, error = cx.backup_features(repo / ".sdlc")
    assert path is None and "over the backup cap" in error
    assert cx.existing_backup(repo / ".sdlc") is None


def _sigma_after(repo, env):
    """Sigma's show, fold and read after the old plugin has run: -> (show, index after fold)."""
    sdlc = repo / ".sdlc"
    show = _run([LOOP / "feature_sync.py", "show", sdlc], env)
    assert show.returncode == 0, show.stderr
    fold = _run([LOOP / "feature_sync.py", "fold", sdlc], env)
    assert fold.returncode == 0, fold.stdout + fold.stderr
    folded = json.loads((sdlc / "features" / "index.json").read_text())
    assert folded["schema"] == "sigma/features@1"
    return json.loads(show.stdout)["alpha"], folded["features"]["alpha"]


@pytest.mark.parametrize("kind", ["stand-in", "real"])
def test_review_block_2_pick_claim_pick_loses_nothing(tmp_path, kind):
    """#314 review block #2, the reviewer's first sequence on the previous plugin's own code: after
    conversion it picks a goal in an index-only unit (a record in ITS schema, starting from
    nothing), its pick-time owner claim fills `owner` on that record (`claim_at_pick`), and it picks
    again. The record now carries an owner -- the field-presence heuristic flipped here and Sigma
    served and folded the record, losing title, owner alice, priority, tracking issue, goals
    201-202, branch and the grant. Keyed on the schema ids instead, nothing is lost and goals
    are unioned."""
    pred = _Predecessor(tmp_path, kind)
    repo = _converted(tmp_path)
    assert pred.pick(repo, 203)["recorded"] is True
    claim = pred.claim(repo, 203, "mallory")
    assert "owner" in claim["claimed"], claim                  # the record now HAS an owner
    assert pred.pick(repo, 204)["recorded"] is True
    record = json.loads((repo / ".sdlc" / "features" / "units" / "alpha.json").read_text())
    assert record["schema"] == OLD + "/features@1"
    assert record["features"]["alpha"]["owner"] == "mallory"
    env = _env(**_host(tmp_path / "clear"))
    for entry in _sigma_after(repo, env):
        assert _alpha(entry) == KEPT + ([201, 202, 203, 204],)
    reg = _load(LOOP / "feature_registry.py", "registry_block2_" + kind.replace("-", "_"))
    assert _alpha(reg.read(repo / ".sdlc" / "features")["alpha"]) == KEPT + ([201, 202, 203, 204],)


@pytest.mark.parametrize("kind", ["stand-in", "real"])
def test_review_block_2_set_priority_on_an_index_only_unit_loses_nothing(tmp_path, kind):
    """#314 review block #2, the second path: the previous plugin's `define.py set-priority` on a
    unit that exists only in Sigma's index writes `{priority: P2, repos: {}}` in its schema. The
    index entry stands whole: its own priority wins, nothing else is touched."""
    pred = _Predecessor(tmp_path, kind)
    repo = _converted(tmp_path)
    report = pred.set_priority(repo, "P2")
    assert report["ok"] is True, report
    record = json.loads((repo / ".sdlc" / "features" / "units" / "alpha.json").read_text())
    assert record["features"]["alpha"]["priority"] == "P2"
    assert record["features"]["alpha"]["repos"] == {}
    env = _env(**_host(tmp_path / "clear"))
    for entry in _sigma_after(repo, env):
        assert _alpha(entry) == KEPT + ([201, 202],)


def test_the_delta_merge_rule(tmp_path):
    """Property-style: over every combination of blank/filled fields on each side, the index entry
    wins every field it has, the legacy record only fills blanks, goals are unioned (index order
    first), a repo only the record names is added WITHOUT a grant, and a grant is never taken from
    the record -- in either direction."""
    import itertools
    reg = _load(LOOP / "feature_registry.py", "registry_delta_rule")
    fields = reg.DELTA_FIELDS
    for mask_i, mask_r in itertools.product(itertools.product((0, 1), repeat=len(fields)),
                                            repeat=2):
        idx = {f: ("i-" + f if on else None) for f, on in zip(fields, mask_i)}
        rec = {f: ("r-" + f if on else None) for f, on in zip(fields, mask_r)}
        merged = reg.merge_legacy_delta(dict(idx, repos={}), dict(rec, repos={}))
        for f in fields:
            assert merged[f] == (idx[f] or rec[f] or ("" if f == "title" else None)), (f, idx, rec)
    for grant_i, grant_r in itertools.product((True, False), repeat=2):
        for branch_i, branch_r in itertools.product(("feature/i", None), ("feature/r", None)):
            idx = {"open": True, "repos": {"o/r": {"branch": branch_i, "owner": None,
                                                   "authorized": grant_i, "goals": [1, 2]}}}
            rec = {"open": False, "repos": {"O/R": {"branch": branch_r, "owner": "bob",
                                                    "authorized": grant_r, "goals": [3, 1]},
                                            "o/new": {"authorized": True, "goals": [9]}}}
            merged = reg.merge_legacy_delta(idx, rec)
            one = merged["repos"]["o/r"]
            assert one["authorized"] is grant_i                  # never from the record
            assert one["goals"] == [1, 2, 3] and one["owner"] == "bob"
            assert one["branch"] == (branch_i or branch_r)
            assert merged["open"] is True and "O/R" not in merged["repos"]
            assert merged["repos"]["o/new"] == {"branch": None, "owner": None,
                                                "authorized": False, "goals": [9]}


def test_a_delta_needs_both_schema_ids(tmp_path):
    """Structural, not a content heuristic: a record in Sigma's schema still replaces (Sigma's own
    pick may narrow a branch or close a unit), a legacy-id record beside a legacy-id index is the
    previous plugin's own state (shard-wins, as it always read), and a legacy-id record for a unit
    the index does not name is simply that unit."""
    reg = _load(LOOP / "feature_registry.py", "registry_delta_ids")
    features = _converted(tmp_path) / ".sdlc" / "features"
    units = features / "units"
    units.mkdir()
    narrowed = dict(ALPHA, title="Renamed", owner=None, open=False,
                    repos={"o/r": {"branch": None, "goals": [201, 202]}})
    (units / "alpha.json").write_text(json.dumps(
        {"schema": "sigma/features@1", "features": {"alpha": narrowed}}))
    got = reg.read(features)["alpha"]
    assert (got["title"], got["owner"], got["open"]) == ("Renamed", None, False)
    assert reg.legacy_deltas(features) == []
    (units / "alpha.json").write_text(json.dumps(
        {"schema": OLD + "/features@1", "features": {"alpha": narrowed}}))
    got = reg.read(features)["alpha"]
    assert (got["title"], got["owner"], got["open"]) == ("Alpha unit", "alice", True)
    assert [n for n, _p in reg.legacy_deltas(features)] == ["alpha"]
    index = features / "index.json"
    index.write_text(index.read_text().replace("sigma/features@1", OLD + "/features@1"))
    assert reg.read(features)["alpha"]["title"] == "Renamed" and reg.legacy_deltas(features) == []
    index.write_text(index.read_text().replace(OLD + "/features@1", "sigma/features@1"))
    (units / "beta.json").write_text(json.dumps(
        {"schema": OLD + "/features@1", "features": {"beta": {"title": "Beta", "repos": {
            "o/r": {"authorized": True, "goals": [7]}}}}}))
    assert reg.read(features)["beta"]["repos"]["o/r"]["authorized"] is True


def test_fold_refuses_a_shrinking_index_with_the_repair_command(tmp_path):
    """The cross-check is independent of the merge: a record in Sigma's schema that dropped a goal
    the index records makes `fold` refuse, write nothing, and name the loss and the recovery."""
    repo = _converted(tmp_path)
    sdlc = repo / ".sdlc"
    units = sdlc / "features" / "units"
    units.mkdir()
    (units / "alpha.json").write_text(json.dumps({"schema": "sigma/features@1", "features": {
        "alpha": dict(ALPHA, repos={"o/r": dict(ALPHA["repos"]["o/r"], goals=[201])})}}))
    index = sdlc / "features" / "index.json"
    before = index.read_bytes()
    fold = _run([LOOP / "feature_sync.py", "fold", sdlc], _env(**_host(tmp_path / "clear")))
    assert fold.returncode == 2 and index.read_bytes() == before, fold.stdout + fold.stderr
    assert "fold refused" in fold.stderr and "alpha: goal(s) #202" in fold.stderr
    assert "feature_sync.py repair" in fold.stderr


# --------------------------------------------------------------------------- partial migrate (#326)
#
# The last blocking defect of #314's review: `migrate.apply` wrote in path order, so
# `features/index.json` became Sigma's schema BEFORE `features/units/*.json`. A unit record that was
# then refused (changed after it was read, a symlink) stayed in the previous plugin's schema -- the
# complete, NEWER record, since that plugin writes its record first and rebuilds its index only on
# demand -- and `read` merged it as a DELTA under the older index: its owner, priority, repo and
# grant were lost, `repair` made the loss permanent, and a second migrate found nothing to do.


#: The previous plugin's own complete record for `alpha`, NEWER than its index entry (`ALPHA`):
#: another owner, a third goal, a second repo with a grant.
NEWER = {"title": "Alpha unit", "owner": "carol", "open": True, "tracking_issue": "#200",
         "priority": "p1", "repos": {
             "o/r": {"branch": "feature/alpha", "owner": "carol", "authorized": True,
                     "goals": [201, 202, 203]},
             "o/s": {"branch": "feature/alpha", "owner": "carol", "authorized": True,
                     "goals": [204]}}}


def _adopted_with_newer_record(tmp_path):
    """The previous plugin's repository mid-life: the index entry, and its own newer unit record."""
    repo = _adopted(tmp_path)
    units = repo / ".sdlc" / "features" / "units"
    units.mkdir()
    (units / "alpha.json").write_text(json.dumps(
        {"schema": OLD + "/features@1", "features": {"alpha": NEWER}}) + "\n", encoding="utf-8")
    return repo


def _served(repo):
    reg = _load(LOOP / "feature_registry.py", "registry_partial_%d" % time.monotonic_ns())
    return reg.read(repo / ".sdlc" / "features")["alpha"]


def _whole(entry, priority="p1"):
    """Nothing of NEWER lost: owner carol, both repos, both grants, every goal."""
    assert (entry["owner"], entry["priority"]) == ("carol", priority), entry
    assert entry["repos"]["o/r"]["goals"] == [201, 202, 203], entry
    assert entry["repos"]["o/s"] == {"branch": "feature/alpha", "owner": "carol",
                                     "authorized": True, "goals": [204]}, entry


def _after_partial(tmp_path, repo, env):
    """Every later step the reviewer ran: show, repair, fold, a second migrate --apply. -> read."""
    sdlc = repo / ".sdlc"
    _whole(_served(repo), "P0")
    for verb in ("show", "repair", "fold"):
        p = _run([LOOP / "feature_sync.py", verb, sdlc], env)
        assert p.returncode == 0, (verb, p.stdout + p.stderr)
        _whole(_served(repo), "P0")
    again = _run([MIGRATE, sdlc, "--apply"], env)
    assert again.returncode == 0, again.stdout + again.stderr
    for name in ("index.json", "units/alpha.json"):
        doc = json.loads((sdlc / "features" / name).read_text())
        assert doc["schema"] == "sigma/features@1", (name, again.stdout)
    _whole(_served(repo), "P0")
    assert "nothing to repair" in _run([LOOP / "feature_sync.py", "repair", sdlc], env).stdout
    return _served(repo)


@pytest.mark.parametrize("kind", ["stand-in", "real"])
def test_a_unit_refused_mid_migrate_keeps_the_index_legacy_and_loses_nothing(tmp_path, kind):
    """#326, the reviewer's race end to end: migrate plans; the previous plugin then writes the
    unit's record (set-priority, its full read-modify-write, in its own schema), so the planned
    rewrite of that record is refused as changed. The index must NOT be converted without it: left
    in the previous schema, the record is read shard-wins (the rule that plugin itself reads by),
    `repair` has nothing to rewrite, and the second migrate converts both. Nothing is lost."""
    pred = _Predecessor(tmp_path, kind)
    repo = _adopted_with_newer_record(tmp_path)
    sdlc = repo / ".sdlc"
    migrate = _load(MIGRATE, "migrate_partial_" + kind.replace("-", "_"))
    planned = migrate.plan(sdlc)
    assert {c.path.name for c in planned.changes} == {"index.json", "alpha.json"}
    assert pred.set_priority(repo, "P0")["ok"] is True          # after the plan, before the apply
    outcome = {c.path.name: why for c, why in migrate.apply(planned)}
    assert "changed after it was read" in outcome["alpha.json"]
    assert outcome["index.json"] and "features/units/alpha.json" in outcome["index.json"]
    index = json.loads((sdlc / "features" / "index.json").read_text())
    assert index["schema"] == OLD + "/features@1"               # left for the rerun
    _after_partial(tmp_path, repo, _env(**_host(tmp_path / "clear")))


def test_one_clean_migrate_converts_units_then_the_index(tmp_path):
    """The hold costs nothing when nothing is refused: ONE `--apply` converts every unit record
    FIRST and the index last, so the index's check finds none left and it converts in the same run."""
    repo = _adopted_with_newer_record(tmp_path)
    sdlc = repo / ".sdlc"
    p = _run([MIGRATE, sdlc, "--apply"], _env(**_host(tmp_path / "clear")))
    assert p.returncode == 0, p.stdout + p.stderr
    changed = [ln.split()[1] for ln in p.stdout.splitlines() if ln.startswith("  changed ")]
    assert changed == [".sdlc/features/units/alpha.json:", ".sdlc/features/index.json:"], p.stdout
    _whole(_served(repo))


def test_a_record_the_old_plugin_creates_mid_migrate_keeps_the_index_legacy(tmp_path):
    """The same window with no refusal at all: the previous plugin CREATES a record after the plan
    (for a unit that was only in the index -- it copies the whole entry and changes one field). The
    index still waits: whatever is under features/units/ in the previous schema at write time, not
    only what the plan listed, keeps it back."""
    pred = _Predecessor(tmp_path)
    repo = _adopted(tmp_path)
    sdlc = repo / ".sdlc"
    migrate = _load(MIGRATE, "migrate_created")
    planned = migrate.plan(sdlc)
    assert [c.path.name for c in planned.changes] == ["index.json"]
    assert pred.set_priority(repo, "P0")["ok"] is True
    [(change, why)] = migrate.apply(planned)
    assert why and "features/units/alpha.json" in why
    assert json.loads((sdlc / "features" / "index.json").read_text())["schema"] == OLD + "/features@1"
    got = _served(repo)
    assert _alpha(got) == ("Alpha unit", "alice", "P0", "#200", "feature/alpha", True, [201, 202])


def test_a_symlinked_unit_keeps_the_index_legacy_and_says_so(tmp_path):
    """#326, the deterministic case: a symlinked unit record is ALWAYS refused (replacing it would
    destroy the link), so the index is refused with it, at plan time -- the dry run already says so,
    and `--apply` writes neither and exits 2. The record behind the link is served whole."""
    repo = _adopted_with_newer_record(tmp_path)
    sdlc = repo / ".sdlc"
    record = sdlc / "features" / "units" / "alpha.json"
    target = tmp_path / "elsewhere" / "alpha.json"
    target.parent.mkdir()
    record.rename(target)
    record.symlink_to(target)
    index = sdlc / "features" / "index.json"
    before = index.read_bytes()
    env = _env(**_host(tmp_path / "clear"))
    for argv in ([MIGRATE, sdlc], [MIGRATE, sdlc, "--apply"]):
        p = _run(argv, env)
        assert p.returncode == 2, p.stdout + p.stderr
        assert "would change features/index.json" not in p.stdout
        assert "changed features/index.json" not in p.stdout
        [line] = [ln for ln in p.stdout.splitlines() if "refused .sdlc/features/index.json" in ln]
        assert "features/units/alpha.json" in line and "rerun" in line, line
        assert index.read_bytes() == before
    _whole(_served(repo))


def test_repair_says_what_it_discards_and_refuses_a_record_newer_than_the_index(tmp_path):
    """`repair` rewrites a delta as the index entry plus what the record ADDS -- so a non-empty
    record value that differs from the index is discarded. It says so, value by value, and when the
    record is newer than the index (an edit the old plugin made after the conversion, which Sigma
    does not apply) it refuses that unit, rewrites nothing, and names both ways forward instead of
    claiming nothing is lost. #327 review: it never sends the user to `migrate.py --apply`, which
    cannot convert such a record (it refuses to)."""
    repo = _converted(tmp_path)
    sdlc = repo / ".sdlc"
    units = sdlc / "features" / "units"
    units.mkdir()
    record = units / "alpha.json"
    record.write_text(json.dumps({"schema": OLD + "/features@1", "features": {"alpha": NEWER}}))
    index = sdlc / "features" / "index.json"
    now = time.time()
    os.utime(index, (now - 60, now - 60))
    os.utime(record, (now, now))
    before = record.read_bytes()
    env = _env(**_host(tmp_path / "clear"))
    p = _run([LOOP / "feature_sync.py", "repair", sdlc], env)
    assert p.returncode == 2, p.stdout + p.stderr
    assert record.read_bytes() == before
    assert "refused alpha" in p.stderr and "owner 'carol'" in p.stderr, p.stderr
    assert not _sends_to_migrate(p.stderr) and "index.json" in p.stderr
    show = _run([LOOP / "feature_sync.py", "show", sdlc], env)
    assert "nothing is lost" not in show.stderr and "DISCARDS" in show.stderr
    assert not _sends_to_migrate(show.stderr), show.stderr
    os.utime(record, (now - 120, now - 120))    # older than the index: e.g. a fold ran since
    p = _run([LOOP / "feature_sync.py", "repair", sdlc], env)
    assert p.returncode == 0 and "repaired alpha" in p.stdout, p.stdout + p.stderr
    assert "discarded alpha: owner 'carol'" in p.stdout, p.stdout
    assert json.loads(record.read_text())["features"]["alpha"]["owner"] == "alice"


def _sends_to_migrate(text):
    """#327: a message telling the user to (re)run migrate over a delta -- which cannot help."""
    import re
    return re.search(r"rerun (it first|`?migrate)", text, re.IGNORECASE) is not None


def test_repair_treats_an_equal_file_time_as_newer(tmp_path):
    """`_newer_than_index` is `>=`: a record and an index with the SAME mtime (a coarse filesystem
    clock, or both written by one `git checkout`) cannot be ordered, so the record is treated as
    the newer one and repair refuses rather than discards."""
    repo = _converted(tmp_path)
    sdlc = repo / ".sdlc"
    (sdlc / "features" / "units").mkdir()
    record = sdlc / "features" / "units" / "alpha.json"
    record.write_text(json.dumps({"schema": OLD + "/features@1", "features": {"alpha": NEWER}}))
    same = time.time() - 30
    for path in (record, sdlc / "features" / "index.json"):
        os.utime(path, (same, same))
    p = _run([LOOP / "feature_sync.py", "repair", sdlc], _env(**_host(tmp_path / "clear")))
    assert p.returncode == 2 and "refused alpha" in p.stderr, p.stdout + p.stderr


# ------------------------------------------------------------ #327 review block #1: a second migrate
#
# After the conversion, the previous plugin's `pick alpha 5` (or `set-priority`) writes a near-empty
# record in ITS schema; Sigma serves it as a delta onto the index entry. A second
# `migrate.py --apply` -- which Sigma's own messages told users to run -- then rewrote that record's
# schema id with nothing else: a Sigma-schema record, which REPLACES the entry (shard-wins), so
# Sigma served `{title: '', owner: None, priority: None, repos: {o/r: {goals: [5]}}}`, exit 0.

REAL_KINDS = ["stand-in", "real@6f41743a", "real@9d2f40fd"]


def _home_env(tmp_path):
    """A clear host AND a fake HOME: `migrate.py` also looks for the previous install under
    `~/.claude/plugins` -- never the developer's own."""
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    return _env(HOME=str(tmp_path / "fake-home"), **_host(tmp_path / "clear"))


def _kept(entry, goals):
    """Everything the standard fixture's `alpha` had, plus `goals` (any order)."""
    assert _alpha(entry)[:6] == KEPT, entry
    assert sorted(entry["repos"]["o/r"]["goals"]) == sorted(goals), entry


@pytest.mark.parametrize("edit", ["pick", "set-priority"])
@pytest.mark.parametrize("kind", REAL_KINDS)
def test_a_second_migrate_never_bare_converts_a_post_conversion_delta(tmp_path, kind, edit):
    pred = _Predecessor(tmp_path, kind)
    repo = _adopted(tmp_path)
    sdlc = repo / ".sdlc"
    env = _home_env(tmp_path)
    first = _run([MIGRATE, sdlc, "--apply"], env)
    assert first.returncode == 0, first.stdout + first.stderr
    if edit == "pick":
        assert pred.pick(repo, 5)["recorded"] is True
        goals = [201, 202, 5]
    else:
        assert pred.set_priority(repo, "P0")["ok"] is True
        goals = [201, 202]
    record = sdlc / "features" / "units" / "alpha.json"
    assert json.loads(record.read_text())["schema"] == OLD + "/features@1"
    _kept(_served(repo), goals)
    before = record.read_bytes()
    for argv in ([MIGRATE, sdlc], [MIGRATE, sdlc, "--apply"]):
        again = _run(argv, env)
        assert again.returncode == 2, again.stdout + again.stderr
        assert record.read_bytes() == before
        [line] = [ln for ln in again.stdout.splitlines()
                  if ln.startswith("  refused .sdlc/features/units/alpha.json")]
        assert "feature_sync.py repair" in line and "delta" in line.lower(), line
        _kept(_served(repo), goals)
    fixed = _run([LOOP / "feature_sync.py", "repair", sdlc], env)
    if edit == "pick":                          # nothing to discard: repair converts it
        assert fixed.returncode == 0 and "repaired alpha" in fixed.stdout, fixed.stdout + fixed.stderr
        assert json.loads(record.read_text())["schema"] == "sigma/features@1"
        last = _run([MIGRATE, sdlc, "--apply"], env)
        assert last.returncode == 0 and "nothing to migrate" in last.stdout, last.stdout
    else:                                       # P0 was never applied; repair will not discard it
        assert fixed.returncode == 2 and "priority 'P0'" in fixed.stderr, fixed.stdout + fixed.stderr
        assert not _sends_to_migrate(fixed.stderr) and record.read_bytes() == before
    _kept(_served(repo), goals)


def test_a_record_that_became_a_delta_after_the_plan_is_refused_at_write_time(tmp_path):
    """The plan saw an index in the previous schema (the record converts), then the index turned
    Sigma's with an OLDER entry before `apply` (a `git pull` of a teammate's converted index): the
    unchanged record is now a delta, and a bare rewrite would serve it alone. `apply` re-checks under
    the unit's lock and refuses it with the same reason."""
    repo = _adopted_with_newer_record(tmp_path)
    sdlc = repo / ".sdlc"
    record = sdlc / "features" / "units" / "alpha.json"
    before = record.read_bytes()
    migrate = _load(MIGRATE, "migrate_became_delta")
    planned = migrate.plan(sdlc)
    assert {c.path.name for c in planned.changes} == {"index.json", "alpha.json"}
    (sdlc / "features" / "index.json").write_text(json.dumps(
        {"schema": "sigma/features@1", "features": {"alpha": ALPHA}}) + "\n", encoding="utf-8")
    outcome = {c.path.name: why for c, why in migrate.apply(planned)}
    assert outcome["alpha.json"] and "feature_sync.py repair" in outcome["alpha.json"], outcome
    assert record.read_bytes() == before
    assert outcome["index.json"], outcome                      # changed after it was read
    got = _served(repo)
    assert got["owner"] == "alice" and 203 in got["repos"]["o/r"]["goals"], got   # still merged


def test_a_legacy_record_the_merge_would_not_change_still_converts(tmp_path):
    """The refusal is exactly "a bare rewrite would serve something else": a legacy record beside a
    Sigma index whose delta merge IS the record (a fold already baked it in) converts as before."""
    pred = _Predecessor(tmp_path)
    repo = _adopted(tmp_path)
    sdlc = repo / ".sdlc"
    env = _home_env(tmp_path)
    assert pred.set_priority(repo, "P0")["ok"] is True       # its own whole record, P0
    fold = _run([LOOP / "feature_sync.py", "fold", sdlc], env)  # Sigma's index now says the same
    assert fold.returncode == 0, fold.stdout + fold.stderr
    p = _run([MIGRATE, sdlc, "--apply"], env)
    assert p.returncode == 0 and "changed .sdlc/features/units/alpha.json" in p.stdout, p.stdout
    assert _alpha(_served(repo)) == ("Alpha unit", "alice", "P0", "#200", "feature/alpha", True,
                                     [201, 202])


@pytest.mark.parametrize("kind", REAL_KINDS)
def test_a_sigma_pick_never_silently_drops_a_legacy_delta_value(tmp_path, kind):
    """#327 review (4): Sigma's own pick rewrote a legacy delta record in its schema from the merged
    view, so the previous plugin's P0 became P1 on disk with nothing said. A record NEWER than the
    index is now refused (nothing written, a divergence and one stderr line naming each value);
    an older one is merged, written, and every value it drops is printed."""
    pred = _Predecessor(tmp_path, kind)
    repo = _converted(tmp_path)
    sdlc = repo / ".sdlc"
    assert pred.set_priority(repo, "P0")["ok"] is True
    record = sdlc / "features" / "units" / "alpha.json"
    index = sdlc / "features" / "index.json"
    now = time.time()
    os.utime(index, (now - 60, now - 60))
    os.utime(record, (now, now))
    before = record.read_bytes()
    fs = _load(LOOP / "feature_sync.py", "fs_delta_guard_" + "".join(
        c if c.isalnum() else "_" for c in kind))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        report = fs.sync_at_pick(str(sdlc), {}, "7", "alpha", run=_Predecessor._git)
    assert record.read_bytes() == before, err.getvalue()
    assert report["recorded"] is False, report
    assert [d for d in report["divergences"] if d["kind"] == fs.LEGACY_DELTA_CONFLICT], report
    assert "priority 'P0'" in err.getvalue() and not _sends_to_migrate(err.getvalue())
    _kept(_served(repo), [201, 202])
    os.utime(record, (now - 120, now - 120))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        report = fs.sync_at_pick(str(sdlc), {}, "7", "alpha", run=_Predecessor._git)
    assert report["recorded"] is True, (report, err.getvalue())
    assert "discarded" in err.getvalue() and "priority 'P0'" in err.getvalue(), err.getvalue()
    assert json.loads(record.read_text())["schema"] == "sigma/features@1"
    _kept(_served(repo), [201, 202, 7])


def test_a_record_written_just_before_the_index_replace_puts_the_index_back(tmp_path):
    """#327 review (5), the race `apply`'s last check cannot see: the previous plugin writes its
    whole record (it read ITS index) after that check and before the replace. The re-scan after
    the replace finds it and restores the index's previous bytes, so the record is read whole
    (shard-wins) instead of as a delta under an older entry; a rerun converts both."""
    pred = _Predecessor(tmp_path)
    repo = _adopted(tmp_path)
    sdlc = repo / ".sdlc"
    index = sdlc / "features" / "index.json"
    original = index.read_bytes()
    migrate = _load(MIGRATE, "migrate_late_record")
    real = migrate._write
    raced = []

    def late(change):
        if change.path.name == "index.json" and not raced:
            raced.append(pred.set_priority(repo, "P0")["ok"])
        return real(change)

    migrate._write = late
    outcome = {c.path.name: why for c, why in migrate.apply(migrate.plan(sdlc))}
    assert raced == [True]
    assert outcome["index.json"] and "features/units/alpha.json" in outcome["index.json"], outcome
    assert index.read_bytes() == original
    assert _alpha(_served(repo)) == ("Alpha unit", "alice", "P0", "#200", "feature/alpha", True,
                                     [201, 202])
    p = _run([MIGRATE, sdlc, "--apply"], _home_env(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
    assert _alpha(_served(repo)) == ("Alpha unit", "alice", "P0", "#200", "feature/alpha", True,
                                     [201, 202])


def test_no_message_sends_a_user_to_migrate_over_a_delta():
    """#327 review (2)/(3): the texts that told users to rerun migrate over a delta."""
    reg = (LOOP / "feature_registry.py").read_text(encoding="utf-8")
    sync = (LOOP / "feature_sync.py").read_text(encoding="utf-8")
    doc = (ROOT / "docs" / "upgrading.md").read_text(encoding="utf-8")
    assert "rerun migrate first" not in reg
    assert "rerun it first" not in sync and "(it converts the record)" not in sync
    assert "Rerun `migrate.py --apply` if it" not in doc
    assert "It is safe to run again. A second" not in doc


# --------------------------------------------------------------------------- the model (#327)
#
# The CLASS behind review block #1: a sequence of ordinary steps, each fine on its own, that ends
# with Sigma serving less than it served before. Every sequence of up to five steps from the
# standard fixture is run, and after EVERY step what Sigma serves for `alpha` is checked against
# everything it served at any earlier step.
#
# EXHAUSTIVE, DEDUPLICATED EXACTLY. Each step is a deterministic function of the files under
# `.sdlc/features/` and, for `repair` and a pick, of whether each unit record's mtime is >= the
# index's. The search therefore explores each distinct (files, mtime order, floor) once, at the
# smallest depth it is reached, which covers every sequence of length <= 5 without re-running
# the 9^5 of them one by one.

MODEL_DEPTH = 5
MODEL_OPS = ("migrate --apply", "migrate dry-run", "predecessor pick", "predecessor set-priority",
             "predecessor claim", "sigma pick", "sigma show", "sigma fold", "sigma repair")
#: The ONLY value an op may change a field to. Everything else it must keep. After conversion the
#: two predecessor edits are documented not-applied edges: they may fail to apply, never remove.
MODEL_EXPLICIT = {"predecessor set-priority": {"priority": "p0"},
                  "predecessor claim": {"owner": "bob", "o/r.owner": "bob"}}
_SCALARS = ("title", "owner", "priority", "tracking_issue", "o/r.branch", "o/r.owner")


def _facts(entry):
    one = (entry.get("repos") or {}).get("o/r") or {}
    scalars = {"title": entry.get("title"), "owner": entry.get("owner"),
               "priority": entry.get("priority"), "tracking_issue": entry.get("tracking_issue"),
               "o/r.branch": one.get("branch"), "o/r.owner": one.get("owner")}
    scalars = {k: (v.lower() if isinstance(v, str) else v) for k, v in scalars.items()}
    goals = frozenset(g for r in (entry.get("repos") or {}).values() for g in r.get("goals") or ())
    grants = frozenset(k for k, r in (entry.get("repos") or {}).items() if r.get("authorized"))
    return scalars, goals, grants


class _Model:
    def __init__(self, tmp_path, kind):
        self.pred = _Predecessor(tmp_path, kind)
        self.repo = _adopted(tmp_path)
        self.sdlc = self.repo / ".sdlc"
        self.features = self.sdlc / "features"
        self.home = tmp_path / "no-home"
        self.home.mkdir()
        tag = "model_" + "".join(c if c.isalnum() else "_" for c in kind)
        self.fs = _load(LOOP / "feature_sync.py", tag + "_feature_sync")
        self.migrate = _load(MIGRATE, tag + "_migrate")

    def snapshot(self):
        return {p.relative_to(self.features).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in sorted(self.features.rglob("*")) if p.is_file()}

    def restore(self, snap):
        shutil.rmtree(self.features)
        for rel, (data, mtime) in snap.items():
            path = self.features / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.utime(path, ns=(mtime, mtime))

    @staticmethod
    def key(snap):
        index = snap.get("index.json", (b"", 0))[1]
        return tuple((rel, data, mtime >= index) for rel, (data, mtime) in snap.items())

    def served(self):
        return self.fs.registry.read(self.features).get("alpha")

    def run(self, op):
        sdlc, out = str(self.sdlc), io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(out):
            if op == "migrate --apply":
                self.migrate.main(["migrate.py", sdlc, "--apply"], environ=dict(os.environ),
                                  home=self.home, stdout=out)
            elif op == "migrate dry-run":
                self.migrate.main(["migrate.py", sdlc], environ=dict(os.environ), home=self.home,
                                  stdout=out)
            elif op == "predecessor pick":
                self.pred.pick(self.repo, 5)
            elif op == "predecessor set-priority":
                self.pred.set_priority(self.repo, "P0")
            elif op == "predecessor claim":
                self.pred.claim(self.repo, 5, "bob")
            elif op == "sigma pick":
                self.fs.sync_at_pick(sdlc, {}, "7", "alpha", run=_Predecessor._git)
            elif op == "sigma show":
                self.fs.main(["feature_sync.py", "show", sdlc])
            elif op == "sigma fold":
                self.fs.main(["feature_sync.py", "fold", sdlc])
            elif op == "sigma repair":
                self.fs.main(["feature_sync.py", "repair", sdlc])
            else:
                raise AssertionError(op)
        return out.getvalue()


def _violations(op, floor, entry):
    if entry is None:
        return ["alpha is no longer served at all"]
    scalars, goals, grants = _facts(entry)
    f_scalars, f_goals, f_grants = floor
    allowed = MODEL_EXPLICIT.get(op, {})
    lost = ["%s %r -> %r" % (k, v, scalars[k]) for k, v in sorted(f_scalars.items())
            if v and scalars[k] != v and scalars[k] != allowed.get(k)]
    lost += ["goal #%s" % g for g in sorted(f_goals - goals)]
    lost += ["grant on %s" % r for r in sorted(f_grants - grants)]
    return lost


def _raise_floor(floor, entry):
    scalars, goals, grants = _facts(entry)
    f_scalars, f_goals, f_grants = floor
    merged = dict(f_scalars)
    merged.update({k: v for k, v in scalars.items() if v})
    return merged, f_goals | goals, f_grants | grants


def _explore(model, depth=MODEL_DEPTH):
    """-> (failures [(sequence, loss)], sequences-equivalent explored, distinct states)."""
    start = model.snapshot()
    first = _facts(model.served())
    frontier = [((), start, first)]
    seen = {}
    failures = []
    for level in range(depth):
        nxt = []
        for seq, snap, floor in frontier:
            for op in MODEL_OPS:
                model.restore(snap)
                model.run(op)
                after = model.snapshot()
                if op in ("migrate dry-run", "sigma show"):
                    assert model.key(after) == model.key(snap), (seq + (op,), "a read wrote")
                entry = model.served()
                lost = _violations(op, floor, entry)
                if lost:
                    failures.append((seq + (op,), lost))
                    continue
                new_floor = _raise_floor(floor, entry)
                frozen = (model.key(after), tuple(sorted(new_floor[0].items())),
                          new_floor[1], new_floor[2])
                if frozen in seen:
                    continue
                seen[frozen] = level + 1
                nxt.append((seq + (op,), after, new_floor))
        frontier = nxt
    return failures, len(seen)


@pytest.mark.parametrize("kind", REAL_KINDS)
def test_model_no_sequence_of_five_steps_loses_what_sigma_served(tmp_path, kind):
    """#327 review: the invariant, over every sequence of <= 5 steps from `MODEL_OPS` (Sigma's
    migrate/dry-run/pick/show/fold/repair and the previous plugin's own pick/set-priority/claim, its
    REAL code when `SIGMA_TEST_PREDECESSOR_GIT` is set, else skipped -- the stand-in always runs):
    what Sigma serves for `alpha` never loses a title, owner, priority, tracking issue, branch,
    goal or grant it served before, unless the step is one that explicitly sets that field (and
    then only to the value it sets)."""
    model = _Model(tmp_path, kind)
    failures, states = _explore(model)
    assert states > 20, states                  # the search reached past the trivial states
    assert not failures, "\n".join("%s: %s" % (" -> ".join(s), "; ".join(l))
                                   for s, l in failures[:10])


def test_migrate_apply_still_refuses_while_a_watcher_is_live(tmp_path, live_pid):
    """Data integrity stays: a live watcher may be writing the old spellings. The refusal names
    the exact polite lever, and the watcher is never signalled."""
    repo, record = _old_schema_repo(tmp_path)
    before = record.read_bytes()
    _fake_watcher(repo / ".sdlc", live_pid)
    p = _run([MIGRATE, repo / ".sdlc", "--apply"], _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 2, p.stdout + p.stderr
    assert "refused all" in p.stdout
    assert str(repo / ".sdlc" / "state" / "watch.stop") in p.stdout
    assert record.read_bytes() == before
    _never_signalled(live_pid)


def test_takeover_offers_the_migrate_dry_run_and_rewrites_nothing(tmp_path):
    """First Sigma run in a repository the old plugin adopted: `loop.py start` prints the exact
    dry-run command once; nothing is rewritten without the user's explicit --apply."""
    repo = _scaffolded(tmp_path)
    (repo / ".sdlc" / "state" / "landing").mkdir(parents=True)
    record = repo / ".sdlc" / "state" / "landing" / "7.json"
    record.write_text(json.dumps({"schema": OLD + "/landing@1", "goal": 7}) + "\n")
    before = record.read_bytes()
    p = _run([LOOP / "loop.py", "start", repo / ".sdlc", "--session-pid", os.getpid()],
             _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0, p.stdout + p.stderr
    [offer] = [ln for ln in p.stderr.splitlines() if ln.startswith("sigma: takeover:")]
    assert "migrate.py %s" % (repo / ".sdlc") in offer and "--apply" in offer
    assert "disabled for this repository" in offer and CAVEAT in offer   # #314: the ordering
    assert record.read_bytes() == before
    p = _run([INIT, repo], _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0 and len([ln for ln in (p.stdout + p.stderr).splitlines()
                                      if ln.startswith("sigma: takeover:")]) == 1
    assert record.read_bytes() == before
    assert _run([MIGRATE, repo / ".sdlc", "--apply"],
                _env(**_host(tmp_path / "clear"))).returncode == 0
    p = _run([LOOP / "loop.py", "start", repo / ".sdlc", "--session-pid", os.getpid()],
             _env(**_host(tmp_path, claude=ENABLED)))
    assert "sigma: takeover:" not in p.stderr                  # migrated: nothing left to offer


def test_the_takeover_hint_is_what_migrate_rewrites_not_a_mention(tmp_path, cx):
    """The cheap hint that gates `migrate.plan()` (linear in the SDLC dir) on every loop start: a
    comment naming the old plugin is no hint; an env value migrate would rewrite is."""
    repo = _repo(tmp_path)
    cfg = repo / ".sdlc" / "config.json"
    cfg.write_text(json.dumps({"_comment": "adopted by %s 1.4.24" % OLD}))
    assert cx._legacy_hint(repo / ".sdlc") is False
    assert cx.takeover_line(repo / ".sdlc") is None
    cfg.write_text(json.dumps({"x": {"token_env": OLD.upper() + "_SLACK_BOT_TOKEN"}}))
    assert cx._legacy_hint(repo / ".sdlc") is True
    assert "migrate.py" in cx.takeover_line(repo / ".sdlc", home=tmp_path)


def test_doctor_check_warns_and_proceeds(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    for key, value in _host(tmp_path, claude=ENABLED).items():
        monkeypatch.setenv(key, value)
    doctor = _load(DOCTOR, "doctor_under_test")
    rows = doctor.check(str(repo / ".sdlc"), run=lambda *a, **k: "", cheap_only=True)
    [row] = [r for r in rows if r["name"].startswith("coexistence")]
    assert row["ok"] is True and UNINSTALL in row["fix"]


def test_status_notes_on_stderr_and_proceeds(tmp_path):
    repo = _scaffolded(tmp_path)
    p = _run([STATUS, repo / ".sdlc"], _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0 and "backlog:" in p.stdout
    assert len(_notices(p.stderr)) == 1 and "refuse" not in p.stderr


BASH_HOOK = pytest.mark.skipif(shutil.which("bash") is None or sys.platform.startswith("win"),
                               reason="the hook is a bash script (not run on Windows)")


def _hook(repo, env):
    """Run the hook the way hooks.json does. `kg.py`, the one helper it spawns, finds no
    knowledge_graph block in these fixtures and says nothing; the wizard is quiet because
    config.json exists. Returns the single additionalContext (one JSON document, or it raises)."""
    p = subprocess.run(["bash", str(HOOK)], input="{}", capture_output=True, text=True,
                       env={**env, "CLAUDE_PROJECT_DIR": str(repo)}, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"] if p.stdout else ""


@BASH_HOOK
def test_session_start_hook_is_one_line_and_idempotent(tmp_path):
    repo = _repo(tmp_path)
    env = _env(**_host(tmp_path, claude=ENABLED))
    first = _hook(repo, env)
    assert _notices(first) == [ln for ln in first.splitlines() if OLD in ln.lower()]
    assert len(_notices(first)) == 1 and UNINSTALL in first
    assert "refused" not in first
    assert _hook(repo, env) == first                          # idempotent: it writes nothing
    assert not (repo / ".sdlc" / "state" / "coexist.notice").exists()
    assert "Sigma is handling" not in _hook(repo, _env(**_host(tmp_path / "clear")))


def _stale_ledger(repo):
    (repo / ".sdlc" / "config.json").write_text(json.dumps(
        {"ledger": {"enabled": True, "watch": {"interval_seconds": 900}}}))
    hb = repo / ".sdlc" / "state" / "watch.heartbeat"
    hb.write_text("")
    old = time.time() - 3600
    os.utime(hb, (old, old))


@BASH_HOOK
def test_session_start_notice_still_reaches_the_watcher_staleness_check(tmp_path):
    """The coexist line is informational in a hook. It must not stop the ledger-watcher staleness
    warning (AGENTS.md LIVENESS)."""
    repo = _repo(tmp_path)
    _stale_ledger(repo)
    ctx = _hook(repo, _env(**_host(tmp_path, claude=ENABLED)))
    assert len(_notices(ctx)) == 1
    assert "looks stale" in ctx and "/agrim-doctor" in ctx


@BASH_HOOK
def test_session_start_override_silences_the_notice_and_falls_through(tmp_path):
    repo = _repo(tmp_path)
    _stale_ledger(repo)
    ctx = _hook(repo, _env(SIGMA_ALLOW_COEXIST="1", **_host(tmp_path, claude=ENABLED)))
    assert _notices(ctx) == [] and "refused" not in ctx
    assert "looks stale" in ctx


@BASH_HOOK
def test_session_start_policy_brief_still_runs_beside_the_notice(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "config.json").write_text('{"session_start":{"enabled":true}}')
    ctx = _hook(repo, _env(**_host(tmp_path, claude=ENABLED)))
    assert len(_notices(ctx)) == 1 and "reviewer is never the author" in ctx


def test_cli_reports_and_exits_0(tmp_path):
    repo = _repo(tmp_path)
    assert _run([LOOP / "coexist.py", "check", repo / ".sdlc"], _env(**_host(tmp_path))).returncode == 0
    p = _run([LOOP / "coexist.py", "check", repo / ".sdlc"], _env(**_host(tmp_path, claude=ENABLED)))
    assert p.returncode == 0, p.stdout + p.stderr
    assert UNINSTALL in p.stdout and "Claude Code has %s enabled" % OLD_ID in p.stdout
    assert "migrate.py %s" % (repo / ".sdlc") in p.stdout and "refused" not in p.stdout

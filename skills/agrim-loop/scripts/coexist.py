#!/usr/bin/env python3
"""Is the plugin under Sigma's PREVIOUS name also acting on this repository? (#240)

    python3 skills/agrim-loop/scripts/coexist.py check [<sdlc_dir>]   # report; exit 2 when active

Both plugins register hooks, spawn a ledger watcher and write `.sdlc/` state. Two watchers on one
`.sdlc` were already impossible -- both plugins' `watch_daemon.py` take the SAME lock files
(`state/watch.pid`, `state/watch.heartbeat`, the `state/watch.decide.lock` mutex) -- but nothing
told either plugin the other was there: a Sigma trigger that met the other's live watcher said
"already running" and carried on, and both loops could write the registry, which the other plugin
reads as empty. This module is the ONE detector every surface asks. The previous name is spelled
only through `legacy.RETIRED` (a guarded private name, #2729/#239).

SIGNALS, two levels. ACTIVE means the other plugin can act on this repository right now:
  - Claude Code: `enabledPlugins` resolves an `<old>@*` id to true (local > project > user settings);
  - Codex: `config.toml` has `[plugins."<old>@*"]` with `enabled = true`;
  - a hook registered by hand in one of those settings files whose command names the old plugin;
  - a live watcher on this `.sdlc` (live pid AND fresh heartbeat -- the watcher's own rule) that
    Sigma did not start (`state/watch.owner` does not name that pid), when its command line names
    the old plugin (Linux `/proc`) or any other old-plugin signal is present;
  - `state/owner.json` naming a plugin other than Sigma.
NOTE means evidence without a live actor: installed but not enabled; old schema ids in state (a
bounded scan); an old-name Cursor rule or Codex block (committed text); an unmarked live watcher
with nothing else to go on (a Sigma watcher started before this release looks exactly like that).

THE DECISION PER SURFACE (docs/upgrading.md, "Running both plugins on one repository"): every
surface that WRITES shared state or starts a watcher -- `sdlc_init.py`, `loop.py start`,
`watch_daemon.py`, `loop.py`'s watcher spawn, `migrate.py --apply` -- calls `gate()` and REFUSES
on any ACTIVE signal, printing `message()`: what was found and the exact fix. Read-only surfaces
(`doctor.py check`, `log.py`, `status.py`) proceed and WARN. The session-start hook only repeats
the message as context: Cursor has no hooks, so the hook decides nothing.

THE ONE OVERRIDE is `SIGMA_ALLOW_COEXIST=1` -- exactly `1`, never derived, never read under the
old prefix. It lets a write surface proceed with a warning. It never lets a second watcher start:
the shared lock files do not consult it.

OWNER MARKERS (Sigma writes them; the old plugin never did, which is why the other signals exist):
`state/owner.json` (`{"schema": "sigma/owner@1", "plugin": "sigma"}`, written by init and loop
start) and `state/watch.owner` (`sigma <pid>`, written by the watcher inside its decision mutex).

COST: at most three settings files, one install list, one TOML file, one pid probe and at most
`STATE_SCAN_CAP` state-file reads per call -- constant beyond that cap. No network, no
subprocess, nothing started. Stdlib only.
"""
import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile
import time
from typing import NamedTuple

_HERE = pathlib.Path(__file__).resolve().parent

USAGE = ("usage: coexist.py check [<sdlc_dir>]\n"
         "  Reports whether the plugin under Sigma's previous name is also active on this\n"
         "  repository. Exit 0: not active (notes may be printed); 2: active (write surfaces refuse).")


def _load(name):
    spec = importlib.util.spec_from_file_location("coexist_" + name, _HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = _load("legacy")

BRAND = legacy.BRAND
OLD = legacy.RETIRED
OVERRIDE_ENV = "SIGMA_ALLOW_COEXIST"
OWNER_FILE = "owner.json"
OWNER_SCHEMA = "sigma/owner@1"
WATCH_OWNER_FILE = "watch.owner"
#: At most this many state files are opened to look for old schema ids (a NOTE, never a refusal).
STATE_SCAN_CAP = 200
#: Where the old plugin's schema-carrying records live (the kinds `legacy.KNOWN_SCHEMAS` names).
_STATE_GLOBS = ("features/index.json", "features/units/*.json", "state/landing/*.json",
                "state/withheld/*.json", "state/propagation/*.json")
DOC = "docs/upgrading.md (Running both plugins on one repository)"
MIGRATE = "python3 skills/agrim-doctor/scripts/migrate.py %s --apply"

#: How the surfaces that refuse are named where no single one is asking (CLI, session hook).
WRITE_SURFACES = "every write surface (init, loop start, the watcher, migrate --apply)"

ACTIVE = "active"
NOTE = "note"


class Signal(NamedTuple):
    kind: str       # claude-enabled | codex-enabled | hook | watcher | owner | installed | state | adapter
    level: str      # ACTIVE | NOTE
    detail: str     # what was found, naming the file
    fix: str        # the exact next step for THIS signal ("" when the general fix covers it)


class Report(NamedTuple):
    sdlc_dir: str
    signals: tuple

    @property
    def active(self):
        return [s for s in self.signals if s.level == ACTIVE]

    @property
    def notes(self):
        return [s for s in self.signals if s.level == NOTE]


# --------------------------------------------------------------------------- roots


def _home(env, home):
    if home is not None:
        return pathlib.Path(home)
    return pathlib.Path(env.get("HOME") or env.get("USERPROFILE") or pathlib.Path.home())


def claude_root(env=None, home=None):
    """`$CLAUDE_CONFIG_DIR`, else `<home>/.claude` -- the same resolution `doctor.py` uses."""
    env = os.environ if env is None else env
    return pathlib.Path(env.get("CLAUDE_CONFIG_DIR") or _home(env, home) / ".claude")


def codex_root(env=None, home=None):
    """`$CODEX_HOME`, else `<home>/.codex` -- Codex's own documented override."""
    env = os.environ if env is None else env
    return pathlib.Path(env.get("CODEX_HOME") or _home(env, home) / ".codex")


def _is_old_id(plugin_id):
    return str(plugin_id).split("@")[0].strip().lower() == OLD


def _names_old(text):
    return isinstance(text, str) and OLD in text.lower()


def _read_json(path):
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception:            # noqa: BLE001 - missing/unreadable/malformed reads as absent
        return None
    return data


def _realpath(path):
    try:
        return os.path.realpath(str(path))
    except Exception:            # noqa: BLE001
        return str(path)


# --------------------------------------------------------------------------- Claude Code


def _settings_files(croot, repo_root):
    """Highest precedence first, as Claude Code resolves `enabledPlugins`."""
    repo = pathlib.Path(repo_root)
    return (("local", repo / ".claude" / "settings.local.json"),
            ("project", repo / ".claude" / "settings.json"),
            ("user", pathlib.Path(croot) / "settings.json"))


def _hook_commands(hooks):
    """Yield (event, command) for every command string in a settings `hooks` block."""
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            inner = group.get("hooks") if isinstance(group, dict) else None
            for hook in inner if isinstance(inner, list) else []:
                command = hook.get("command") if isinstance(hook, dict) else None
                if isinstance(command, str):
                    yield str(event), command


def claude_signals(croot, repo_root):
    out = []
    decided = {}                                  # old plugin id -> (enabled, scope, file)
    for scope, path in _settings_files(croot, repo_root):
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        enabled = data.get("enabledPlugins")
        if isinstance(enabled, dict):
            for pid, value in enabled.items():
                if _is_old_id(pid) and pid not in decided:
                    decided[pid] = (value is True, scope, path)
        for event, command in _hook_commands(data.get("hooks")):
            if _names_old(command):
                out.append(Signal("hook", ACTIVE,
                                  "a %s hook registered by hand in %s runs the old plugin"
                                  % (event, path),
                                  "remove that hook entry from %s" % path))
    for pid, (on, scope, path) in sorted(decided.items()):
        if on:
            out.append(Signal("claude-enabled", ACTIVE,
                              "Claude Code has %s enabled (%s settings, %s); its hooks and "
                              "watcher run in every session here" % (pid, scope, path),
                              "for this repository only, set \"%s\": false under enabledPlugins "
                              "in %s; or everywhere: claude plugin disable %s"
                              % (pid, pathlib.Path(repo_root) / ".claude" / "settings.local.json",
                                 pid)))
    if not any(on for on, _s, _p in decided.values()):
        installed = _read_json(pathlib.Path(croot) / "plugins" / "installed_plugins.json")
        plugins = installed.get("plugins") if isinstance(installed, dict) else None
        here = _realpath(repo_root)
        for pid, entries in (plugins.items() if isinstance(plugins, dict) else ()):
            if not _is_old_id(pid) or not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                path = entry.get("projectPath")
                if entry.get("scope", "user") == "user" or (
                        isinstance(path, str) and _realpath(path) == here):
                    out.append(Signal("installed", NOTE,
                                      "%s is installed for Claude Code but not enabled here" % pid,
                                      "claude plugin uninstall %s   (when nobody on this machine "
                                      "still needs it)" % pid))
                    break
    return out


# --------------------------------------------------------------------------- Codex

_TOML_TABLE = re.compile(r'^\s*\[\s*plugins\s*\.\s*"([^"]+)"\s*\]\s*(?:#.*)?$')
_TOML_ANY_TABLE = re.compile(r"^\s*\[")
_TOML_ENABLED = re.compile(r"^\s*enabled\s*=\s*(true|false)\b")


def codex_signals(xroot):
    """`[plugins."<id>"]` tables in Codex's `config.toml`, read line by line (Python 3.10 has no
    tomllib). A table with no `enabled` line is a NOTE: this reader will not guess Codex's default."""
    path = pathlib.Path(xroot) / "config.toml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:            # noqa: BLE001 - no Codex here
        return []
    tables, current = {}, None
    for line in lines:
        m = _TOML_TABLE.match(line)
        if m:
            current = m.group(1)
            tables.setdefault(current, None)
            continue
        if _TOML_ANY_TABLE.match(line):
            current = None
            continue
        e = _TOML_ENABLED.match(line)
        if e and current is not None:
            tables[current] = e.group(1) == "true"
    out = []
    for pid, on in sorted(tables.items()):
        if not _is_old_id(pid):
            continue
        if on:
            out.append(Signal("codex-enabled", ACTIVE,
                              "Codex has %s enabled (%s)" % (pid, path),
                              "set `enabled = false` under [plugins.\"%s\"] in %s" % (pid, path)))
        else:
            out.append(Signal("installed", NOTE,
                              "Codex lists %s (%s), %s" % (pid, path,
                                                         "disabled" if on is False
                                                         else "enabled state not recorded"),
                              ""))
    return out


# --------------------------------------------------------------------------- committed adapters


def adapter_signals(repo_root):
    """Cursor rules and the Codex AGENTS.md block are committed TEXT, not running code -- NOTE."""
    out = []
    repo = pathlib.Path(repo_root)
    rules = repo / ".cursor" / "rules"
    try:
        names = sorted(p for p in rules.iterdir() if p.suffix == ".mdc" and p.is_file())
    except OSError:
        names = []
    for rule in names[:50]:
        try:
            if _names_old(rule.read_text(encoding="utf-8", errors="replace")):
                out.append(Signal("adapter", NOTE,
                                  "Cursor rule %s still points at the old plugin" % rule,
                                  "delete it, then run `sdlc_init.py <repo> --cursor`"))
        except OSError:
            pass
    try:
        agents = (repo / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        agents = ""
    old_start = legacy.retired_spelling("<!-- sigma:codex:start -->")
    if old_start in agents:
        out.append(Signal("adapter", NOTE,
                          "AGENTS.md still carries the old plugin's Codex block",
                          MIGRATE % "<sdlc>"))
    return out


# --------------------------------------------------------------------------- state dir


def read_owner(sdlc_dir):
    data = _read_json(pathlib.Path(sdlc_dir) / "state" / OWNER_FILE)
    if isinstance(data, dict) and isinstance(data.get("plugin"), str):
        return data["plugin"]
    return None


def write_owner(sdlc_dir):
    """Record that Sigma owns this `.sdlc` on this machine. Atomic, idempotent, fail-open (a
    marker we cannot write must never stop the surface that asked -- the other signals remain)."""
    try:
        state = pathlib.Path(sdlc_dir) / "state"
        target = state / OWNER_FILE
        body = json.dumps({"schema": OWNER_SCHEMA, "plugin": BRAND}, indent=2) + "\n"
        if target.is_file() and target.read_text(encoding="utf-8") == body:
            return False
        state.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".owner-", dir=str(state))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    except Exception:            # noqa: BLE001 - fail-open, see the docstring
        return False


def state_signals(sdlc_dir):
    out = []
    base = pathlib.Path(sdlc_dir)
    owner = read_owner(sdlc_dir)
    if owner is not None and owner != BRAND:
        out.append(Signal("owner", ACTIVE,
                          "%s names %r as this state directory's owner"
                          % (base / "state" / OWNER_FILE, owner),
                          "stop that plugin for this repository, then delete %s"
                          % (base / "state" / OWNER_FILE)))
    seen = found = 0
    for pattern in _STATE_GLOBS:
        for path in sorted(base.glob(pattern)):
            if seen >= STATE_SCAN_CAP:
                break
            seen += 1
            data = _read_json(path)
            if isinstance(data, dict) and legacy.is_legacy_schema(data.get("schema")):
                found += 1
    if found:
        out.append(Signal("state", NOTE,
                          "%d state file(s) under %s carry the old plugin's schema ids (of %d "
                          "read, cap %d); Sigma reads them" % (found, base, seen, STATE_SCAN_CAP),
                          MIGRATE % base))
    return out


def watch_owner_pid(state_dir):
    """The pid `state/watch.owner` names as Sigma's watcher, or None."""
    try:
        text = (pathlib.Path(state_dir) / WATCH_OWNER_FILE).read_text(encoding="utf-8").split()
    except OSError:
        return None
    if len(text) == 2 and text[0] == BRAND:
        try:
            return int(text[1])
        except ValueError:
            return None
    return None


def write_watch_owner(state_dir, pid):
    (pathlib.Path(state_dir) / WATCH_OWNER_FILE).write_text("%s %d\n" % (BRAND, pid),
                                                             encoding="utf-8")


def clear_watch_owner(state_dir, pid):
    """Remove the marker only while it still names `pid` (the watcher's ownership-checked cleanup)."""
    if watch_owner_pid(state_dir) == pid:
        try:
            (pathlib.Path(state_dir) / WATCH_OWNER_FILE).unlink()
        except OSError:
            pass


def live_watcher_pid(sdlc_dir, now=None, env=None):
    """The pid of this `.sdlc`'s live watcher by the watcher's OWN rule (live pid AND fresh
    heartbeat), or None. The probe is `watch_daemon.pid_alive`, which is safe on win32."""
    wd = _load("watch_daemon")
    env = os.environ if env is None else env
    interval, _warning = wd.resolve_interval(wd.read_config(str(sdlc_dir)), env)
    p = wd.paths(str(sdlc_dir))
    if wd.already_running(p, wd.stale_after_seconds(interval), time.time() if now is None else now):
        return wd.pid_from_file(p.pid)
    return None


def _cmdline_names_old(pid):
    """Linux only: does `/proc/<pid>/cmdline` name the old plugin? None where it cannot be read."""
    try:
        raw = pathlib.Path("/proc/%d/cmdline" % pid).read_bytes()
    except (OSError, ValueError, TypeError):
        return None
    return _names_old(raw.decode("utf-8", "replace"))


def watcher_signal(sdlc_dir, other_signals, now=None, env=None):
    try:
        pid = live_watcher_pid(sdlc_dir, now=now, env=env)
    except Exception:            # noqa: BLE001 - a probe that cannot run sees no watcher
        return []
    if pid is None or watch_owner_pid(pathlib.Path(sdlc_dir) / "state") == pid:
        return []
    stop = pathlib.Path(sdlc_dir) / "state" / "watch.stop"
    fix = ("stop it: disable the old plugin first (or its triggers restart it), then create %s "
           "and wait one tick" % stop)
    named = _cmdline_names_old(pid)
    if named or (named is None and other_signals):
        return [Signal("watcher", ACTIVE,
                       "a live watcher (pid %s) holds this .sdlc's watcher lock and was not "
                       "started by Sigma (no matching state/%s)" % (pid, WATCH_OWNER_FILE), fix)]
    return [Signal("watcher", NOTE,
                   "a live watcher (pid %s) was not started by this Sigma release (no state/%s): "
                   "a Sigma watcher from before it, or the old plugin's" % (pid, WATCH_OWNER_FILE),
                   "if it is not Sigma's, " + fix)]


# --------------------------------------------------------------------------- assessment


def assess(sdlc_dir, env=None, home=None, repo_root=None, now=None):
    """-> Report. Never raises: a source that cannot be read contributes nothing."""
    env = os.environ if env is None else env
    sdlc = pathlib.Path(sdlc_dir)
    repo = pathlib.Path(repo_root) if repo_root is not None else sdlc.resolve().parent
    signals = []
    for source in (lambda: claude_signals(claude_root(env, home), repo),
                   lambda: codex_signals(codex_root(env, home)),
                   lambda: adapter_signals(repo),
                   lambda: state_signals(sdlc)):
        try:
            signals.extend(source())
        except Exception:        # noqa: BLE001 - see the docstring
            pass
    signals.extend(watcher_signal(sdlc, bool(signals), now=now, env=env))
    return Report(str(sdlc_dir), tuple(signals))


def overridden(env=None):
    env = os.environ if env is None else env
    return env.get(OVERRIDE_ENV) == "1"


def message(report, surface):
    """The one message: what was found, then the fix. Multi-line, plain text."""
    lines = ["%s: %s refused -- the plugin previously published as %r is also active on this "
             "repository, and both would write %s:" % (BRAND, surface, OLD, report.sdlc_dir)]
    for s in report.active:
        lines.append("  - " + s.detail)
    lines.append("Fix: run ONE plugin per repository.")
    step = 1
    for s in report.active:
        if s.fix:
            lines.append("  %d. %s" % (step, s.fix))
            step += 1
    lines.append("  %d. then move the state over: %s" % (step, MIGRATE % report.sdlc_dir))
    lines.append("Or, knowing both will act here, set %s=1 (the shared watcher lock still allows "
                 "only one watcher). See %s." % (OVERRIDE_ENV, DOC))
    return "\n".join(lines)


def gate(sdlc_dir, surface, env=None, home=None, stream=None, repo_root=None):
    """True when `surface` may proceed. On an ACTIVE signal: refuses (prints `message`, False),
    unless the override is set (prints a one-line warning, True)."""
    env = os.environ if env is None else env
    stream = sys.stderr if stream is None else stream
    report = assess(sdlc_dir, env=env, home=home, repo_root=repo_root)
    if not report.active:
        return True
    if overridden(env):
        print("%s: warning: %s proceeding with the old plugin also active (%s=1): %s"
              % (BRAND, surface, OVERRIDE_ENV, "; ".join(s.detail for s in report.active)),
              file=stream)
        return True
    print(message(report, surface), file=stream)
    return False


def warn_line(sdlc_dir, env=None, home=None):
    """For read-only surfaces: one line when ACTIVE, else None."""
    report = assess(sdlc_dir, env=env, home=home)
    if not report.active:
        return None
    return ("%s: warning: the plugin previously published as %r is also active on this "
            "repository (%d signal(s)); write surfaces refuse until one is disabled -- run "
            "`coexist.py check %s` for the fix" % (BRAND, OLD, len(report.active), sdlc_dir))


def doctor_row(sdlc_dir, env=None, home=None):
    """A `doctor.py check` row: {name, ok, fix}. ACTIVE -> not ok; notes -> ok with advice."""
    report = assess(sdlc_dir, env=env, home=home)
    if report.active:
        return {"name": "coexistence: the old plugin is ACTIVE here (%s)"
                        % "; ".join(s.detail for s in report.active),
                "ok": False,
                "fix": "disable one plugin for this repo, then migrate -- `coexist.py check %s` "
                       "prints the exact steps (%s)" % (sdlc_dir, DOC)}
    if report.notes:
        return {"name": "coexistence: no second plugin active; %s"
                        % "; ".join(s.detail for s in report.notes),
                "ok": True,
                "fix": "; ".join(s.fix for s in report.notes if s.fix)}
    return {"name": "coexistence: no second plugin found", "ok": True, "fix": ""}


def main(argv):
    if argv[1:] in (["-h"], ["--help"]) or len(argv) < 2 or argv[1] != "check" or len(argv) > 3:
        if argv[1:] in (["-h"], ["--help"]):
            print(USAGE)
            return 0
        print(USAGE, file=sys.stderr)
        return 2
    sdlc_dir = argv[2] if len(argv) > 2 else ".sdlc"
    report = assess(sdlc_dir)
    if report.active:
        print(message(report, WRITE_SURFACES))
    for s in report.notes:
        print("note: %s%s" % (s.detail, ("\n      -> " + s.fix) if s.fix else ""))
    if not report.signals:
        print("%s: no second plugin found for %s" % (BRAND, sdlc_dir))
    return 2 if report.active else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

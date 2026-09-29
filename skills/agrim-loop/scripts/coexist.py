#!/usr/bin/env python3
"""Is the plugin under Sigma's PREVIOUS name also acting on this repository? (#240, #314)

    python3 skills/agrim-loop/scripts/coexist.py check [<sdlc_dir>]   # report + cut-over steps;
                                                                      # exit 0 (2 only on bad usage)

Both plugins register hooks, spawn a ledger watcher and write `.sdlc/` state. Two watchers on one
`.sdlc` were already impossible -- both plugins' `watch_daemon.py` take the SAME lock files
(`state/watch.pid`, `state/watch.heartbeat`, the `state/watch.decide.lock` mutex) -- but nothing
told either plugin the other was there: a Sigma trigger that met the other's live watcher said
"already running" and carried on, and both loops could write the registry, which the other plugin
reads as empty. This module is the ONE detector every surface asks. The previous name is spelled
only through `legacy.RETIRED` (a guarded private name, #2729/#239).

SIGNALS, two levels. ACTIVE means the other plugin can act on this repository right now:
  - Claude Code: `enabledPlugins` resolves an `<old>@*` id to true (managed > local > project >
    user settings; JSONC comments and trailing commas tolerated) AND the plugin is installed for
    this repository per `plugins/installed_plugins.json`. When that list cannot be read, an entry
    in the user's OWN scope (managed/local/user) still counts -- fail toward the notice only where
    we cannot tell -- while a committed project entry alone is a NOTE;
  - Codex: `config.toml` resolves `plugins."<old>@*".enabled` to true (any TOML spelling:
    tomllib on 3.11+, a small stdlib fallback parser on 3.10);
  - a hook registered by hand in one of those settings files whose command runs the old plugin:
    a path in it has a DIRECTORY named exactly the old name, or it contains the old plugin's
    recorded `installPath` -- never a mere substring (a user's `~/bin/<old>-notes.sh` is theirs);
  - a live watcher on this `.sdlc` (live pid AND fresh heartbeat -- the watcher's own rule) that
    Sigma did not start (`state/watch.owner` does not name that pid), when its watcher SCRIPT path
    has a directory named exactly the old name (Linux `/proc`; never the `.sdlc` argument), or -- where the command line cannot be read (macOS, Windows)
    -- when another ACTIVE signal is present. Notes never escalate it;
  - `state/owner.json` naming a plugin other than Sigma -- a notice, never a lock: init and loop
    start record Sigma as the owner.
NOTE means evidence without a live actor: installed but not enabled; enabled but not installed on
this machine; old schema ids in state (a bounded scan); an old-name Cursor rule or Codex block
(committed text); an unmarked live watcher with nothing ACTIVE beside it (a Sigma watcher started
before this release looks exactly like that, so a Sigma-only upgrade hears no notice).

THE DECISION: NOTICE, NEVER REFUSAL (#314, reversing #240's refusal on the owner's direction --
Sigma replaces that plugin in place, and users uninstall it after the cut-over; docs/upgrading.md,
"Switching over from the previous plugin"). Every surface that writes shared state or starts a
watcher -- `sdlc_init.py`/`init_flow.py`, `loop.py start`, `loop.py claim`/`record`, the watcher
spawn and `watch_daemon.py`, `migrate.py` -- calls `gate()`, which ALWAYS admits and, on an ACTIVE
signal, prints ONE line (`notice_line`): the other plugin is here, Sigma is handling this
repository, and the exact uninstall command. Once per run: a start surface (init, loop start,
migrate) always says it; a per-verb surface (`once=True`) stays quiet while `state/coexist.notice`
is younger than `NOTICE_TTL_SECONDS`, and every notice refreshes that mark. Read-only surfaces
(`doctor.py check` -- a WARN row, never a failure -- and `status.py`) say the same. The
session-start hook repeats the line, read-only, and carries on to its other tiers: Cursor has no
hooks, so the hook is an accelerator and decides nothing.

THE ONE STEP THAT WAITS (#314, review of PR #319): the old plugin reads a `sigma/features@1`
registry as EMPTY, so once the registry is converted a goal it starts writes a near-empty unit
record over a unit that exists only in `index.json`. So `migrate.py --apply` -- the conversion --
refuses while the old plugin can still RUN here (`RUNS_KINDS`) unless `REPLACE_FLAG` is given,
naming `disable_steps()` (`claude plugin disable <id> --scope local`). Every other surface stays a
notice. In depth: `protect_features()` takes ONE copy of `.sdlc/features` under `state/backup/`
before Sigma's first registry write beside a runnable old plugin (called from
`feature_registry`'s writers), and `feature_registry.is_shadow` keeps the reader and `fold` from
ever serving or baking the near-empty record over the fuller entry.

WHAT STAYS IMPOSSIBLE is not this module's job and never depended on it: two watchers on one
`.sdlc` (both plugins' watchers take the SAME lock files; a Sigma watcher that meets a foreign
holder names it and the polite lever `stop_lever()`, and signals nothing), and `migrate.py --apply`
rewriting files while any watcher is live (its own refusal, with the same lever).

`SIGMA_ALLOW_COEXIST=1` -- exactly `1`, never derived, never read under the old prefix -- SILENCES
the notice. It is no longer needed for anything to run.

OWNER MARKERS (Sigma writes them; the old plugin never did, which is why the other signals exist):
`state/owner.json` (`{"schema": "sigma/owner@1", "plugin": "sigma"}`, written by init and loop
start) and `state/watch.owner` (`sigma <pid>`, written by the watcher inside its decision mutex).

COST: at most four settings files, one install list, one TOML file, one pid probe and at most
`STATE_SCAN_CAP` state-file reads per call -- constant beyond that cap. No network, no
subprocess, nothing started. Stdlib only.
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
from typing import NamedTuple

try:                                  # 3.11+; Python 3.10 uses the fallback parser below
    import tomllib
    _toml_loads = tomllib.loads
except ImportError:                   # pragma: no cover - exercised on 3.10
    _toml_loads = None

_HERE = pathlib.Path(__file__).resolve().parent

USAGE = ("usage: coexist.py check [<sdlc_dir>]\n"
         "  Reports whether the plugin under Sigma's previous name is also active on this\n"
         "  repository and, when it is, the cut-over steps (migrate, then uninstall it).\n"
         "  Informational: exit 0 (2 only on bad usage). Nothing refuses while it is active.")


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
#: At most this many state files are opened to look for old schema ids (a NOTE).
STATE_SCAN_CAP = 200
#: Where the old plugin's schema-carrying records live (the kinds `legacy.KNOWN_SCHEMAS` names).
_STATE_GLOBS = ("features/index.json", "features/units/*.json", "state/landing/*.json",
                "state/withheld/*.json", "state/propagation/*.json")
DOC = "docs/upgrading.md (Switching over from the previous plugin)"
MIGRATE_SCRIPT = _HERE.parent.parent / "agrim-doctor" / "scripts" / "migrate.py"
#: The acknowledgement `migrate.py --apply` requires while the old plugin can still run here.
REPLACE_FLAG = "--replace-old-plugin"
#: Why the conversion step (and only it) waits for that acknowledgement -- one fixed sentence, said
#: by migrate's refusal, the notice, the takeover line and docs/upgrading.md alike.
REGISTRY_CAVEAT = ("the old plugin cannot read Sigma's registry; if it starts a goal in this repo "
                   "afterwards it will overwrite unit records")
#: Signals meaning the old plugin can RUN here. The conversion gate and the one-time registry
#: backup key on these; a foreign `owner.json` alone is a stale marker Sigma rewrites, not a runner.
RUNS_KINDS = ("claude-enabled", "codex-enabled", "hook", "watcher")
#: The one-time pre-image of `.sdlc/features` (#314): `state/` is machine-local and runtime-ignored.
BACKUP_PARTS = ("state", "backup")
BACKUP_PREFIX = "features-"
#: A registry larger than this is not copied (said aloud): a backup must never become the outage.
BACKUP_FILE_CAP = 5000
BACKUP_BYTE_CAP = 64 * 1024 * 1024
#: The per-run notice mark (state/, machine-local). A per-verb surface stays quiet while it is
#: younger than the TTL: one notice per working session, not one per `loop.py` verb (#251).
NOTICE_FILE = "coexist.notice"
NOTICE_TTL_SECONDS = 6 * 3600
#: The one fixed phrase of the notice -- what a reader (and a test) looks for.
HANDLING = "Sigma is handling this repository"

ACTIVE = "active"
NOTE = "note"


class Signal(NamedTuple):
    kind: str       # claude-enabled | codex-enabled | hook | watcher | owner | installed | state | adapter
    level: str      # ACTIVE | NOTE
    detail: str     # what was found, naming the file
    fix: str        # the exact next step for THIS signal ("" when the general fix covers it)
    plugin: str = ""   # the old plugin's id (`<old>@<marketplace>`) when the signal names one


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


def _strip_jsonc(text):
    """JSON-with-comments -> JSON: drop `//` and `/* */` comments and trailing commas, never
    touching the inside of a string. Claude Code settings files are hand-edited."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == ",":
            j = i + 1
            while j < n:                                 # skip whitespace AND comments
                if text[j] in " \t\r\n":
                    j += 1
                elif text.startswith("//", j):
                    k = text.find("\n", j)
                    j = n if k < 0 else k
                elif text.startswith("/*", j):
                    k = text.find("*/", j + 2)
                    j = n if k < 0 else k + 2
                else:
                    break
            if j < n and text[j] in "}]":
                i += 1                                   # a trailing comma: drop it
                continue
            out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _read_json(path):
    """Missing/unreadable/malformed reads as absent; JSONC (comments, trailing commas) is read."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except Exception:            # noqa: BLE001
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return json.loads(_strip_jsonc(text))
    except ValueError:
        return None


def _realpath(path):
    try:
        return os.path.realpath(str(path))
    except Exception:            # noqa: BLE001
        return str(path)


def quote(arg, platform=None):
    """One argument, quoted for the platform's shell -- a printed command must paste as is. The ONE
    quoting rule for every command this module (and `migrate.py`, `feature_registry.py`) prints:
    `subprocess.list2cmdline` on Windows, `shlex.quote` elsewhere."""
    text = str(arg)
    if (sys.platform if platform is None else platform).startswith("win"):
        import subprocess
        return subprocess.list2cmdline([text])
    import shlex
    return shlex.quote(text)


def shell_command(*args, platform=None):
    """A whole printed command, each argument quoted by `quote`."""
    return " ".join(quote(a, platform=platform) for a in args)


def migrate_command(sdlc_dir, *flags, script=MIGRATE_SCRIPT):
    """The exact migrate command for this SDLC dir (`flags`: "--apply", REPLACE_FLAG)."""
    return shell_command("python3", script, sdlc_dir, *flags)


# --------------------------------------------------------------------------- Claude Code


def managed_settings_path(platform=None, env=None):
    """Claude Code's machine-wide managed settings file (highest precedence). The seam: `assess`
    takes `managed=` so tests never read the real one."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    if platform == "darwin":
        return pathlib.Path("/Library/Application Support/ClaudeCode/managed-settings.json")
    if platform.startswith("win"):
        return pathlib.Path(env.get("ProgramData") or "C:\\ProgramData") / "ClaudeCode" / \
            "managed-settings.json"
    return pathlib.Path("/etc/claude-code/managed-settings.json")


def _settings_files(croot, repo_root, managed=None):
    """Highest precedence first, as Claude Code resolves `enabledPlugins`."""
    repo = pathlib.Path(repo_root)
    files = [("local", repo / ".claude" / "settings.local.json"),
             ("project", repo / ".claude" / "settings.json"),
             ("user", pathlib.Path(croot) / "settings.json")]
    if managed is not None:
        files.insert(0, ("managed", pathlib.Path(managed)))
    return tuple(files)


#: Scopes the USER controls on this machine: enabled there with an unreadable install list is
#: still ACTIVE. A committed project entry alone does not (a teammate's choice, not proof it runs here).
_OWN_SCOPES = ("managed", "local", "user")


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


def _dir_named_old(path):
    """Is a DIRECTORY component of `path` exactly the old name? The file name is never one, and a
    name that merely contains it (`my-<old>-migration`) is not it. Either separator."""
    parts = re.split(r"[\\/]+", path)
    return len(parts) > 1 and any(part.lower() == OLD for part in parts[:-1])


def _runs_old(command, roots=()):
    """Does this hook command run the old plugin? Only when it contains the plugin's recorded
    install path, or a path whose DIRECTORY segment is exactly the old name -- so
    `/opt/<old>/hooks/x.sh` matches and a user's own `~/bin/<old>-notes.sh` does not."""
    low = command.lower()
    if any(len(r) > 3 and r.lower() in low for r in roots):
        return True
    return any(_dir_named_old(token) for token in re.split(r"[\s\"'=;&|()<>`]+", command))


def _install_entries(plugins, pid):
    entries = plugins.get(pid)
    if isinstance(entries, dict):                    # installed_plugins.json version 1
        entries = [entries]
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def claude_signals(croot, repo_root, managed=None):
    out = []
    installed = _read_json(pathlib.Path(croot) / "plugins" / "installed_plugins.json")
    plugins = installed.get("plugins") if isinstance(installed, dict) else None
    known = isinstance(plugins, dict)                # can we tell what is installed here?
    plugins = plugins if known else {}
    here = _realpath(repo_root)
    old_ids = [pid for pid in plugins if _is_old_id(pid)]
    roots = [e["installPath"] for pid in old_ids for e in _install_entries(plugins, pid)
             if isinstance(e.get("installPath"), str)]

    def scope_here(pid):
        """The install scope that runs `pid` for this repository ("user", "project", "local"), or
        None when it is not installed for it."""
        for entry in _install_entries(plugins, pid):
            path = entry.get("projectPath")
            scope = entry.get("scope", "user")
            if scope == "user" or (
                    isinstance(path, str)
                    and os.path.normcase(_realpath(path)) == os.path.normcase(here)):
                return scope if isinstance(scope, str) else "user"
        return None

    def installed_here(pid):
        return scope_here(pid) is not None

    decided = {}                                  # old plugin id -> (enabled, scope, file)
    for scope, path in _settings_files(croot, repo_root, managed):
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        enabled = data.get("enabledPlugins")
        if isinstance(enabled, dict):
            for pid, value in enabled.items():
                if _is_old_id(pid) and pid not in decided:
                    decided[pid] = (value is True, scope, path)
        for event, command in _hook_commands(data.get("hooks")):
            if _runs_old(command, roots):
                out.append(Signal("hook", ACTIVE,
                                  "a %s hook registered by hand in %s runs the old plugin"
                                  % (event, path),
                                  "remove that hook entry from %s" % path))
    local = pathlib.Path(repo_root) / ".claude" / "settings.local.json"
    for pid, (on, scope, path) in sorted(decided.items()):
        if not on:
            continue
        if known and not installed_here(pid):
            out.append(Signal("claude-enabled", NOTE,
                              "Claude Code settings enable %s (%s settings, %s) but it is not "
                              "installed for this repository on this machine, so nothing runs"
                              % (pid, scope, path),
                              "remove the stale \"%s\" entry from %s" % (pid, path)))
        elif not known and scope not in _OWN_SCOPES:
            out.append(Signal("claude-enabled", NOTE,
                              "the committed %s settings enable %s (%s); this machine's install "
                              "list is unreadable, so whether it runs here is unknown"
                              % (scope, pid, path),
                              "if it is installed here, set \"%s\": false under enabledPlugins "
                              "in %s" % (pid, local)))
        else:
            where = scope_here(pid) if known else None
            out.append(Signal("claude-enabled", ACTIVE,
                              "Claude Code has %s enabled (%s settings, %s); its hooks and "
                              "watcher run in every session here" % (pid, scope, path),
                              shell_command("claude", "plugin", "uninstall", pid,
                                            *(("--scope", where) if where not in (None, "user")
                                              else ())),
                              pid))
    for pid in sorted(old_ids):
        if installed_here(pid) and not decided.get(pid, (False,))[0]:
            out.append(Signal("installed", NOTE,
                              "%s is installed for Claude Code but not enabled here" % pid,
                              "%s   (when nobody on this machine still needs it)"
                              % shell_command("claude", "plugin", "uninstall", pid), pid))
    return out


# --------------------------------------------------------------------------- Codex

_TOML_KEY = re.compile(r"""\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([A-Za-z0-9_-]+))\s*""")
_TOML_BOOL = re.compile(r"(true|false)\s*(?:#.*)?$")
_TOML_INLINE_ENABLED = re.compile(r"[{,]\s*enabled\s*=\s*(true|false)\b")
_DEFAULT = object()


def _toml_dotted_key(text, pos):
    """-> (keys, end) for a dotted TOML key at `pos` (bare, "basic" or 'literal' parts)."""
    keys = []
    while True:
        m = _TOML_KEY.match(text, pos)
        if not m or m.end() == pos:
            return None, pos
        basic, literal, bare = m.groups()
        if basic is not None:
            try:
                basic = json.loads('"%s"' % basic)
            except ValueError:
                return None, pos
        keys.append(basic if basic is not None else literal if literal is not None else bare)
        pos = m.end()
        if pos < len(text) and text[pos] == ".":
            pos += 1
            continue
        return keys, pos


def _fallback_plugins(text):
    """Python 3.10 has no tomllib and Sigma adds no dependency: a line reader for exactly the
    shapes that can set `plugins.<id>.enabled` -- `[plugins."<id>"]` / `[plugins.'<id>']` tables,
    dotted keys at any level, and `"<id>" = { enabled = ... }` inline tables. Best effort: a
    multi-line string that happens to look like a table header can mislead it."""
    out, current = {}, []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[["):
            current = None                           # an array of tables: nothing we read
            continue
        if stripped.startswith("["):
            keys, pos = _toml_dotted_key(stripped, 1)
            rest = stripped[pos:]
            if keys and rest.startswith("]") and (not rest[1:].strip()
                                                   or rest[1:].strip().startswith("#")):
                current = keys
                if keys[0] == "plugins" and len(keys) == 2:
                    out.setdefault(keys[1], None)
            else:
                current = None
            continue
        if current is None:
            continue
        keys, pos = _toml_dotted_key(stripped, 0)
        rest = stripped[pos:]
        if not keys or not rest.startswith("="):
            continue
        value, full = rest[1:].strip(), current + keys
        if full[0] != "plugins" or len(full) < 2:
            continue
        pid = full[1]
        out.setdefault(pid, None)
        if full[2:] == ["enabled"]:
            b = _TOML_BOOL.match(value)
            if b:
                out[pid] = b.group(1) == "true"
        elif len(full) == 2 and value.startswith("{"):
            b = _TOML_INLINE_ENABLED.search(value)
            if b:
                out[pid] = b.group(1) == "true"
    return out


def parse_codex_plugins(text, loads=_DEFAULT):
    """-> {plugin id: True | False | None (no `enabled` recorded)} from Codex's `config.toml`.
    `loads` is tomllib's (3.11+) by default; None, or a file tomllib rejects, uses the fallback."""
    loads = _toml_loads if loads is _DEFAULT else loads
    if loads is not None:
        try:
            data = loads(text)
        except Exception:        # noqa: BLE001 - malformed TOML: best effort below
            data = None
        if isinstance(data, dict):
            plugins = data.get("plugins")
            return {pid: (v.get("enabled") if isinstance(v.get("enabled"), bool) else None)
                    for pid, v in (plugins.items() if isinstance(plugins, dict) else ())
                    if isinstance(v, dict)}
    return _fallback_plugins(text)


def codex_signals(xroot):
    """`plugins."<id>".enabled` in Codex's `config.toml`. An entry with no `enabled` is a NOTE:
    this reader will not guess Codex's default."""
    path = pathlib.Path(xroot) / "config.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:            # noqa: BLE001 - no Codex here
        return []
    tables = parse_codex_plugins(text)
    out = []
    for pid, on in sorted(tables.items()):
        if not _is_old_id(pid):
            continue
        if on:
            out.append(Signal("codex-enabled", ACTIVE,
                              "Codex has %s enabled (%s)" % (pid, path),
                              "remove the [plugins.\"%s\"] table from %s" % (pid, path), pid))
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
                          migrate_command(repo / ".sdlc")))
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
                          "nothing to do: Sigma records itself as the owner on its next init or "
                          "loop start"))
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
                          migrate_command(base)))
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


WATCHER_SCRIPT = "watch_daemon.py"


def stop_lever(sdlc_dir):
    """The ONE polite way to stop a watcher on this `.sdlc` -- whoever started it. Sigma never
    signals another plugin's process: the stop file is the watcher's own documented exit."""
    return ("uninstall the old plugin first (or its triggers start its watcher again), then create "
            "%s, wait one watcher tick for it to exit, and delete that file again (no watcher "
            "starts while it exists)" % (pathlib.Path(sdlc_dir) / "state" / "watch.stop"))


def _cmdline_names_old(pid):
    """Linux only: is this process the old plugin's watcher? None where `/proc/<pid>/cmdline`
    cannot be read. Only the watcher SCRIPT's path is looked at -- the argv element whose file
    name is `watch_daemon.py` -- and only for a directory named exactly the old name (the rule
    `_runs_old` uses): the plugin cache lays it out `<cache>/<marketplace>/<plugin>/<version>/...`.
    Never the interpreter path, never the `.sdlc` argument (a user's `my-<old>-migration/` repo is
    theirs), never a substring."""
    try:
        raw = pathlib.Path("/proc/%d/cmdline" % pid).read_bytes()
    except (OSError, ValueError, TypeError):
        return None
    argv = raw.decode("utf-8", "replace").split("\0")
    return any(_dir_named_old(arg) for arg in argv
               if re.split(r"[\\/]+", arg)[-1].lower() == WATCHER_SCRIPT)


def watcher_signal(sdlc_dir, active_elsewhere, now=None, env=None):
    """An unmarked live watcher is ACTIVE when its command line names the old plugin, or when the
    command line cannot be read AND another ACTIVE signal stands (`active_elsewhere`). NOTE-level
    evidence (old schema ids, committed adapter text, installed-but-disabled) never escalates it:
    every Sigma watcher started before this release is unmarked too."""
    try:
        pid = live_watcher_pid(sdlc_dir, now=now, env=env)
    except Exception:            # noqa: BLE001 - a probe that cannot run sees no watcher
        return []
    if pid is None or watch_owner_pid(pathlib.Path(sdlc_dir) / "state") == pid:
        return []
    fix = "stop it politely: " + stop_lever(sdlc_dir)
    named = _cmdline_names_old(pid)
    if named or (named is None and active_elsewhere):
        return [Signal("watcher", ACTIVE,
                       "a live watcher (pid %s) holds this .sdlc's watcher lock and was not "
                       "started by Sigma (no matching state/%s)" % (pid, WATCH_OWNER_FILE), fix)]
    unread = (" -- this OS does not expose its command line, so Sigma cannot tell who started "
              "the running watcher" if named is None else "")
    return [Signal("watcher", NOTE,
                   "a live watcher (pid %s) was not started by this Sigma release (no state/%s): "
                   "a Sigma watcher from before it, or the old plugin's%s"
                   % (pid, WATCH_OWNER_FILE, unread),
                   "if it is not Sigma's, " + fix)]


# --------------------------------------------------------------------------- assessment


def assess(sdlc_dir, env=None, home=None, repo_root=None, now=None, managed=_DEFAULT):
    """-> Report. Never raises: a source that cannot be read contributes nothing. `managed` is the
    Claude Code managed-settings path (default: this platform's; None skips it)."""
    env = os.environ if env is None else env
    managed = managed_settings_path() if managed is _DEFAULT else managed
    sdlc = pathlib.Path(sdlc_dir)
    repo = pathlib.Path(repo_root) if repo_root is not None else sdlc.resolve().parent
    signals = []
    for source in (lambda: claude_signals(claude_root(env, home), repo, managed),
                   lambda: codex_signals(codex_root(env, home)),
                   lambda: adapter_signals(repo),
                   lambda: state_signals(sdlc)):
        try:
            signals.extend(source())
        except Exception:        # noqa: BLE001 - see the docstring
            pass
    signals.extend(watcher_signal(sdlc, any(s.level == ACTIVE for s in signals),
                                  now=now, env=env))
    return Report(str(sdlc_dir), tuple(signals))


def overridden(env=None):
    env = os.environ if env is None else env
    return env.get(OVERRIDE_ENV) == "1"


def _removals(report):
    """The exact removal step for each ACTIVE signal that names one (uninstall, config edit, hook)."""
    return [s.fix for s in report.active if s.kind in ("claude-enabled", "codex-enabled", "hook")
            and s.fix]


def runs_here(report):
    """The ACTIVE signals meaning the old plugin can still RUN on this repository."""
    return [s for s in report.active if s.kind in RUNS_KINDS]


def disable_steps(report):
    """The exact step that stops the old plugin acting on THIS repository, per ACTIVE runner --
    what must happen BEFORE `migrate.py --apply`. Claude Code: `claude plugin disable <id> --scope
    local` (writes `"<id>": false` to this repository's `.claude/settings.local.json`, which beats
    project and user settings; verified against Claude Code's own CLI, #314). Codex has no
    per-repository switch, so its step is the machine-wide config edit, said as such."""
    steps = []
    for s in runs_here(report):
        if s.kind == "claude-enabled" and s.plugin:
            steps.append("%s   (run in this repository; this repository on this machine only)"
                         % shell_command("claude", "plugin", "disable", s.plugin,
                                         "--scope", "local"))
        elif s.kind == "codex-enabled" and s.plugin:
            steps.append("Codex: %s (Codex has no per-repository switch: this stops it on every "
                         "repository on this machine)" % s.fix)
        elif s.fix:
            steps.append(s.fix)
    return steps


def marketplaces(report):
    """The marketplace each old Claude Code plugin id came from (`<old>@<marketplace>` -> the part
    after `@`) -- never assumed to equal the plugin's own name."""
    out = []
    for s in report.signals:
        if s.plugin and s.kind in ("claude-enabled", "installed") and "@" in s.plugin:
            market = s.plugin.split("@", 1)[1]
            if market and market not in out:
                out.append(market)
    return out


def notice_line(report):
    """THE notice (#314): one line, naming the uninstall command, the ordering caveat for the one
    step that converts the registry, and the one-time backup. It never says "refused"."""
    removals = _removals(report)
    line = ("%s: notice: the plugin previously published as %r is also enabled here; %s -- "
            "uninstall it when ready%s" % (BRAND, OLD, HANDLING,
                                           (": " + " ; ".join(removals)) if removals else ""))
    if any(s.kind == "watcher" for s in report.active):
        line += ("; its live watcher keeps this repository's watcher lock, so Sigma starts none "
                 "beside it (`coexist.py check` names the polite way to stop it)")
    line += ("; %s, so disable it here before `migrate.py --apply` (Sigma saves one copy of "
             ".sdlc/features to .sdlc/%s/%s<time> before its first registry write here)"
             % (REGISTRY_CAVEAT, "/".join(BACKUP_PARTS), BACKUP_PREFIX))
    return line + " (%s; %s=1 silences this)" % (DOC, OVERRIDE_ENV)


def message(report):
    """The full report for `coexist.py check`: what was found, then the cut-over steps IN ORDER --
    stop the old plugin on this repository FIRST, then convert, then uninstall."""
    lines = ["%s: the plugin previously published as %r is also active on this repository "
             "(%s); %s:" % (BRAND, OLD, report.sdlc_dir, HANDLING)]
    for s in report.active:
        lines.append("  - " + s.detail)
    lines.append("Cut-over, when ready (%s -- so step 1 comes first):" % REGISTRY_CAVEAT)
    step = 1
    for text in disable_steps(report) or ["nothing to disable"]:
        lines.append("  %d. stop it on this repository: %s" % (step, text))
        step += 1
    lines.append("  %d. preview the state rewrite (writes nothing): %s"
                 % (step, migrate_command(report.sdlc_dir)))
    lines.append("  %d. apply it, once no watcher is live: %s"
                 % (step + 1, migrate_command(report.sdlc_dir, "--apply")))
    step += 2
    for s in report.active:
        if s.fix and s.kind != "watcher":
            lines.append("  %d. %s" % (step, s.fix))
            step += 1
    for market in marketplaces(report):
        lines.append("  %d. optionally, if nothing else you use comes from it: %s"
                     % (step, shell_command("claude", "plugin", "marketplace", "remove", market)))
        step += 1
    lines.append("Nothing is blocked meanwhile, and the shared watcher lock still admits only one "
                 "watcher. %s=1 silences the notice. See %s." % (OVERRIDE_ENV, DOC))
    return "\n".join(lines)


# --------------------------------------------------------------------------- the registry backup


def backup_root(sdlc_dir):
    return pathlib.Path(sdlc_dir).joinpath(*BACKUP_PARTS)


def existing_backup(sdlc_dir):
    """The oldest `state/backup/features-*` copy, or None. Never raises."""
    try:
        found = sorted(p for p in backup_root(sdlc_dir).glob(BACKUP_PREFIX + "*") if p.is_dir())
    except (OSError, ValueError):
        return None
    return found[0] if found else None


def _tree_size(src):
    files = size = 0
    for dirpath, _dirs, names in os.walk(str(src)):
        for name in names:
            files += 1
            try:
                size += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
            if files > BACKUP_FILE_CAP or size > BACKUP_BYTE_CAP:
                return files, size, False
    return files, size, True


def backup_features(sdlc_dir):
    """-> (path, error). ONE-TIME: when a copy already exists it is returned and nothing is copied
    (the pre-image is the one worth keeping; later ones would only grow). `(None, None)` when there
    is no `features/` to protect. Copied to a hidden partial directory, then renamed into place, so
    a crash never leaves a half copy that looks whole. Bounded: over `BACKUP_FILE_CAP` files or
    `BACKUP_BYTE_CAP` bytes nothing is copied and the error says so."""
    have = existing_backup(sdlc_dir)
    if have is not None:
        return have, None
    src = pathlib.Path(sdlc_dir) / "features"
    if not src.is_dir():
        return None, None
    files, size, ok = _tree_size(src)
    if not ok:
        return None, ("%s is over the backup cap (%d files / %d bytes max), so it was not copied"
                      % (src, BACKUP_FILE_CAP, BACKUP_BYTE_CAP))
    root = backup_root(sdlc_dir)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = root / (BACKUP_PREFIX + stamp)
    tmp = root / (".partial-%s-%d" % (stamp, os.getpid()))
    try:
        root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(src), str(tmp), symlinks=True)
        os.replace(str(tmp), str(dest))
    except OSError as exc:
        shutil.rmtree(str(tmp), ignore_errors=True)
        have = existing_backup(sdlc_dir)             # a concurrent writer made it first
        if have is not None:
            return have, None
        return None, "copying %s failed (%s)" % (src, exc)
    return dest, None


def _runs_cheap(sdlc_dir, env=None, home=None, managed=_DEFAULT):
    """The Claude Code / Codex runners only -- no state scan, no watcher probe: what a registry
    WRITE can afford to ask. At most four settings files, one install list, one TOML file."""
    env = os.environ if env is None else env
    managed = managed_settings_path() if managed is _DEFAULT else managed
    repo = pathlib.Path(sdlc_dir).resolve().parent
    found = []
    for source in (lambda: claude_signals(claude_root(env, home), repo, managed),
                   lambda: codex_signals(codex_root(env, home))):
        try:
            found.extend(source())
        except Exception:        # noqa: BLE001 - a source that cannot be read contributes nothing
            pass
    return [s for s in found if s.level == ACTIVE and s.kind in RUNS_KINDS]


def protect_features(sdlc_dir, env=None, home=None, stream=None, managed=_DEFAULT):
    """Before Sigma writes `.sdlc/features` (#314): when the old plugin can run on this repository
    and no copy exists yet, take the one-time backup and say where. -> the new copy's path, else
    None. Fail-open and never raises: a backup that cannot be taken is SAID, and the write goes on
    (the reader-side shadow guard in `feature_registry.read` still stands). Cost once a copy exists,
    or with no `features/`: one glob and one stat."""
    try:
        stream = sys.stderr if stream is None else stream
        if existing_backup(sdlc_dir) is not None:
            return None
        if not (pathlib.Path(sdlc_dir) / "features").is_dir():
            return None
        if not _runs_cheap(sdlc_dir, env=env, home=home, managed=managed):
            return None
        path, error = backup_features(sdlc_dir)
        if path is not None:
            print("%s: backup: the plugin previously published as %r can still run here and %s; "
                  "before Sigma's first registry write a copy of %s was saved to %s (restore it "
                  "over .sdlc/features if unit records are lost; %s)"
                  % (BRAND, OLD, REGISTRY_CAVEAT.split("; ")[0],
                     pathlib.Path(sdlc_dir) / "features", path, DOC), file=stream)
        elif error:
            print("%s: backup: NOT taken -- %s; the registry write goes on (%s)"
                  % (BRAND, error, DOC), file=stream)
        return path
    except Exception:            # noqa: BLE001 - see the docstring
        return None


def _mark_fresh(marker, now):
    try:
        return now - marker.stat().st_mtime < NOTICE_TTL_SECONDS
    except OSError:
        return False


def _mark(marker):
    """Refresh the per-run mark. Only inside an existing state dir; fail-open (a mark that cannot
    be written costs one repeated notice line, never a stopped surface)."""
    try:
        if marker.parent.is_dir():
            marker.write_text("%d\n" % int(time.time()), encoding="utf-8")
    except OSError:
        pass


def gate(sdlc_dir, surface, env=None, home=None, stream=None, repo_root=None, once=False):
    """ALWAYS True (#314: notice, never refusal). On an ACTIVE signal prints `notice_line` -- unless
    `SIGMA_ALLOW_COEXIST=1` silences it, or `once` (a per-verb surface) finds this run's mark
    fresh. `surface` names the caller at its call site; the notice is the same for every caller.
    Cost with a fresh mark and `once`: one `stat`, no assessment."""
    env = os.environ if env is None else env
    stream = sys.stderr if stream is None else stream
    if overridden(env):
        return True
    marker = pathlib.Path(sdlc_dir) / "state" / NOTICE_FILE
    if once and _mark_fresh(marker, time.time()):
        return True
    report = assess(sdlc_dir, env=env, home=home, repo_root=repo_root)
    if report.active:
        print(notice_line(report), file=stream)
        _mark(marker)
    return True


def warn_line(sdlc_dir, env=None, home=None):
    """For read-only surfaces: the notice when ACTIVE (and not silenced), else None."""
    if overridden(env):
        return None
    report = assess(sdlc_dir, env=env, home=home)
    return notice_line(report) if report.active else None


def doctor_row(sdlc_dir, env=None, home=None):
    """A `doctor.py check` row: {name, ok, fix}. Informational: ACTIVE is a WARN (ok, carrying the
    uninstall command), never a failure."""
    report = assess(sdlc_dir, env=env, home=home)
    if report.active:
        removals = _removals(report)
        return {"name": "coexistence: WARN -- the plugin previously published as %r is also active "
                        "here (%s); %s" % (OLD, "; ".join(s.detail for s in report.active),
                                           HANDLING),
                "ok": True,
                "fix": "uninstall it when ready%s; `coexist.py check %s` prints the cut-over steps "
                       "(%s)" % ((": " + " ; ".join(removals)) if removals else "", sdlc_dir, DOC)}
    if report.notes:
        return {"name": "coexistence: no second plugin active; %s"
                        % "; ".join(s.detail for s in report.notes),
                "ok": True,
                "fix": "; ".join(s.fix for s in report.notes if s.fix)}
    return {"name": "coexistence: no second plugin found", "ok": True, "fix": ""}


def _legacy_hint(sdlc_dir):
    """Cheap, bounded: does this SDLC dir look like it still carries the previous name in a place
    `migrate.py` rewrites? Old schema ids in the known record kinds, an old managed-block marker in
    a feature doc, the name in config.json, or the old Codex block in AGENTS.md."""
    base = pathlib.Path(sdlc_dir)
    if any(s.kind == "state" for s in state_signals(base)):
        return True
    marker = legacy.retired_spelling("<!-- sigma:")
    for doc in sorted(base.glob("features/*.md"))[:STATE_SCAN_CAP]:
        try:
            if marker in doc.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
    cfg = _read_json(base / "config.json")
    if isinstance(cfg, dict):
        drift = cfg.get("drift_watch")
        channels = drift.get("channels") if isinstance(drift, dict) else None
        try:
            if any(legacy.sigma_env_name(v) in legacy.FALLBACK_ENV       # what migrate rewrites
                   for _k, v in legacy.legacy_env_values(cfg)) or (
                    isinstance(channels, dict) and OLD in channels):
                return True
        except Exception:        # noqa: BLE001 - a hint that cannot be read is no hint
            pass
    return any(s.kind == "adapter" and "AGENTS.md" in s.detail
               for s in adapter_signals(base.resolve().parent))


def takeover_line(sdlc_dir, environ=None, home=None):
    """First Sigma run in a repository the old plugin adopted: ONE line with the exact migrate
    dry-run command, or None when nothing is left to migrate. Reads only -- `migrate.py --apply`
    runs only on the user's explicit yes, never from here. Fail-open (None).

    COST. `migrate.plan()` is linear in the SDLC dir (measured 1.3-1.7 s over an 8,888-file one on
    a loaded laptop, #314), too much for every `loop.py start`, so it runs only behind
    `_legacy_hint`: at most `STATE_SCAN_CAP` state files + `STATE_SCAN_CAP` feature docs +
    config.json + AGENTS.md, constant beyond the cap. A repository with no hint gets no offer."""
    if not _legacy_hint(sdlc_dir):
        return None
    try:
        script = MIGRATE_SCRIPT
        spec = importlib.util.spec_from_file_location("coexist_migrate", script)
        migrate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migrate)
        result = migrate.plan(str(sdlc_dir), environ=environ, home=home)
        if not result.changes:
            return None
        return ("%s: takeover: %d file(s) here still carry the previous name %r; Sigma reads them "
                "as they are. Preview the rewrite (writes nothing): %s -- then, only once the user "
                "has said yes AND the old plugin is disabled for this repository (%s), add --apply"
                % (BRAND, len(result.changes), OLD, migrate_command(sdlc_dir, script=script),
                   REGISTRY_CAVEAT))
    except Exception:            # noqa: BLE001 - an offer that cannot be computed is not made
        return None


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
        print(message(report))
    for s in report.notes:
        print("note: %s%s" % (s.detail, ("\n      -> " + s.fix) if s.fix else ""))
    if not report.signals:
        print("%s: no second plugin found for %s" % (BRAND, sdlc_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

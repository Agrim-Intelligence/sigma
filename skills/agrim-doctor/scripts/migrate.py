#!/usr/bin/env python3
"""One-shot migration of state written under the plugin's PREVIOUS name to Sigma's names (#239).

    python3 skills/agrim-doctor/scripts/migrate.py .sdlc            # dry run: lists, writes nothing
    python3 skills/agrim-doctor/scripts/migrate.py .sdlc --apply    # writes, then prints what changed

YOU DO NOT NEED THIS TO KEEP WORKING. Sigma already READS every spelling below
(`skills/agrim-loop/scripts/legacy.py`); this is the explicit cutover, so the files stop carrying
the old name. What it rewrites, and nothing else:

  - the `schema` id of every JSON record under the SDLC dir (`work/` and nested checkouts skipped)
    whose id is the previous name's spelling of a kind Sigma knows (features, landing, withheld,
    propagation) -- the version is kept;
  - the managed-block markers of each `features/<unit>.md` (the digest covers the body, which is
    not touched, so it stays valid);
  - the Codex block in the repository's `AGENTS.md`, regenerated with Sigma's text, exactly as
    `sdlc_init.py --codex` would (every byte outside the block kept);
  - in `config.json`: `*_env` values naming the previous env prefix (to their `SIGMA_*` spelling),
    and the `drift_watch.channels` key renamed to `sigma`.

BYTE-PRESERVING, THEN PROVEN. Each rewrite swaps exact tokens in place -- line endings, key order,
comments and formatting survive -- and is then VERIFIED by re-reading the result: JSON must parse to
the original with only the listed values changed, a feature doc must locate INTACT with the same
body and digest. Anything that fails, is ambiguous, or is not understood is REFUSED: left untouched,
listed with the reason, exit 2. Unknown previous-name schema kinds, history (ledger entries, timing
sessions) and the old journal config block are LEFT AS IS and listed -- history is not rewritten,
and turning the journal on is an explicit opt-in.

UNIT RECORDS FIRST, THE INDEX LAST, AND ONLY WHOLE (#326). `features/index.json` converts only
once no unit record under `features/units/` is left in the previous schema -- one refused, symlinked,
or written by the previous plugin during the run keeps the index back (listed as refused, exit 2),
because beside a Sigma index such a record is read as a partial delta, and it may be the newer
complete one. A rerun converts it.

SAFE TO RE-RUN. A second `--apply` finds nothing and says so. Each write is atomic (temp file in the
same directory, then `os.replace`), taken under the unit's `feature_sync` lock for feature files, and
refused if the file changed after it was read. A symlinked target (an `AGENTS.md` linked to
`CLAUDE.md`, a linked feature file) is REFUSED, never followed: replacing it would destroy the link. `--apply` refuses outright while this SDLC dir's
watcher is running (it may be the old plugin's, still writing old spellings), and -- #314 -- while
the old plugin can still RUN on this repository, unless `--replace-old-plugin` is given (then a
one-time copy of `features/` is saved under `state/backup/` first, and that copy is never scanned
here): the old plugin cannot read Sigma's registry, and a goal it starts afterwards writes a unit
record in its own schema that Sigma can only merge as a partial delta. The refusal prints the exact
disable step.

WHAT IT CANNOT DO FOR YOU. The plugin under its previous name cannot read Sigma's spellings. Every
file this changes is one that plugin also reads, and committed ones (`config.json`, `features/`)
reach teammates through git -- so switch the team together. Environment variables are yours to
rename: they are listed by NAME, never by value.

Exit: 0 = nothing to do, a plan printed, or everything applied; 2 = anything refused, or bad usage.
Scale: one read (and one JSON parse) per candidate file, linear in the SDLC dir's size. Measured
(#239, a copy of a real adopted repo, macOS laptop): 0.35-0.37 s wall per run over an 8,882-file,
57 MB SDLC dir (77 files rewritten); 10x is therefore seconds, not minutes. Nothing runs in the
background and no lock is held across files.
"""
import copy
import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile
import time

USAGE = ("usage: migrate.py [<sdlc_dir>] [--apply [--replace-old-plugin]]\n"
         "  Rewrites state written under the plugin's previous name to Sigma's names.\n"
         "  Dry run (writes nothing) unless --apply is given. Safe to re-run.\n"
         "  While the old plugin can still run on this repository, --apply needs\n"
         "  --replace-old-plugin: disable it here first instead (docs/upgrading.md).")

_HERE = pathlib.Path(__file__).resolve().parent
_LOOP = _HERE.parent.parent / "agrim-loop" / "scripts"
_INIT = _HERE.parent.parent / "agrim-init" / "scripts"

#: The journal config block before its rename, from fragments (a guarded name) -- see doctor.py.
_OLD_JOURNAL_BLOCK = "tele" + "metry"
#: Directories under the SDLC dir that hold other checkouts, never this repo's own state.
_SKIP_TOP = ("work",)
#: #314: the one-time registry pre-image (`coexist.BACKUP_PARTS`) is a copy of old state kept on
#: purpose -- rewriting it would destroy the thing a restore needs.
_SKIP_NESTED = (("state", "backup"),)
#: History: reported (counted), never rewritten.
_HISTORY_DIRS = (("ledger",), ("state", "time"))


def _load(directory, name):
    spec = importlib.util.spec_from_file_location(name, directory / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Change:
    """One planned rewrite of one file: the exact bytes it was planned from, and the bytes to write."""

    def __init__(self, path, old, new, what, unit=None):
        self.path, self.old, self.new, self.what, self.unit = path, old, new, what, unit


class Plan:
    def __init__(self, sdlc_dir):
        self.sdlc_dir = pathlib.Path(sdlc_dir)
        self.root = self.sdlc_dir.resolve().parent
        self.changes, self.refused, self.left, self.notes = [], [], [], []

    def rel_link(self, path):
        """`rel` without resolving the last component, so a symlink is named as itself."""
        path = pathlib.Path(path)
        try:
            return (path.parent.resolve() / path.name).relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def rel(self, path):
        try:
            return pathlib.Path(path).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)


# --------------------------------------------------------------------------- planning


def _candidate_json(sdlc_dir):
    """Every `*.json` under the SDLC dir except `work/` and any directory holding its own `.git`
    (a nested checkout carries another repository's files, fixtures included)."""
    base = pathlib.Path(sdlc_dir)
    for dirpath, dirnames, filenames in os.walk(base):
        here = pathlib.Path(dirpath)
        if here == base:
            dirnames[:] = [d for d in dirnames if d not in _SKIP_TOP]
        dirnames[:] = [d for d in dirnames
                       if tuple((here / d).relative_to(base).parts) not in _SKIP_NESTED]
        dirnames[:] = sorted(d for d in dirnames if not (here / d / ".git").exists())
        for name in sorted(filenames):
            if name.endswith(".json"):
                yield here / name


def _plan_schemas(plan, legacy):
    needle = (legacy.RETIRED + "/").encode("ascii")
    for path in _candidate_json(plan.sdlc_dir):
        if path.name == "config.json" and path.parent == plan.sdlc_dir:
            continue                                   # config has no schema id; see _plan_config
        try:
            data = path.read_bytes()
        except OSError as exc:
            plan.refused.append((plan.rel(path), "could not be read (%s)" % exc))
            continue
        if needle not in data:
            continue
        try:
            doc = json.loads(data.decode("utf-8"))
        except ValueError:
            continue                                   # not JSON we could ever have written
        if not isinstance(doc, dict) or not legacy.is_legacy_schema(doc.get("schema")):
            continue
        old_id = doc["schema"]
        new_id = legacy.canonical_schema(old_id)
        if new_id == old_id:
            plan.left.append((plan.rel(path), "schema id %r is not one Sigma knows, so it is not "
                              "rewritten" % old_id))
            continue
        # ONE guard, and it is the re-parse: every occurrence of the id's exact JSON text is swapped,
        # then the result must parse to the original with ONLY `schema` changed. An id that also
        # appears as another value or key, or is spelled with escapes (so it is not found), fails
        # that and is refused. (A separate "appears exactly once" count was measured redundant with
        # this in the #239 control run -- removing it changed no outcome -- so it is not kept.)
        old_tok, new_tok = json.dumps(old_id).encode("utf-8"), json.dumps(new_id).encode("utf-8")
        new = data.replace(old_tok, new_tok)
        if not _json_equal(new, {**doc, "schema": new_id}):
            plan.refused.append((plan.rel(path), "rewriting the id would change more than the id "
                                 "(its text also appears elsewhere, or is spelled with escapes)"))
            continue
        unit = path.stem if path.parent.name == "units" else None
        _add_change(plan, Change(path, data, new, "schema id %s -> %s" % (old_id, new_id), unit))


_SYMLINK_REASON = ("it is a symlink: replacing it would destroy the link and leave its target "
                   "unchanged -- migrate the link's target, or replace the link with a file")


def _add_change(plan, change):
    """Plan `change` -- unless its path is a symlink, which is refused instead. `os.replace` over a
    link swaps the LINK for a regular file, so the file it pointed at keeps the old spelling while
    the run reports success; `sdlc_init --codex` refuses a symlinked AGENTS.md for the same reason.
    Checked only for a file that would change, so an unrelated link is never a refusal."""
    if change.path.is_symlink():
        plan.refused.append((plan.rel_link(change.path), _SYMLINK_REASON))
    else:
        plan.changes.append(change)


def _json_equal(data, expected):
    try:
        return json.loads(data.decode("utf-8")) == expected
    except ValueError:
        return False


def _plan_feature_docs(plan, legacy):
    fdoc = _load(_LOOP, "feature_doc")
    features = plan.sdlc_dir / "features"
    if not features.is_dir():
        return
    old_begin = legacy.retired_spelling(fdoc.BEGIN_OPEN).encode("ascii")
    old_end = legacy.retired_spelling(fdoc.END).encode("ascii")
    sigma_begin, sigma_end = fdoc.BEGIN_OPEN.encode("ascii"), fdoc.END.encode("ascii")
    for path in sorted(features.glob("*" + fdoc.DOC_SUFFIX)):
        try:
            data = path.read_bytes()
        except OSError as exc:
            plan.refused.append((plan.rel(path), "could not be read (%s)" % exc))
            continue
        if old_begin not in data and old_end not in data:
            continue
        if sigma_begin in data or sigma_end in data:
            plan.left.append((plan.rel(path), "it carries both spellings: Sigma manages its own "
                              "block; the other belongs to a teammate's copy of the plugin under "
                              "its previous name -- delete it by hand once nobody runs that"))
            continue
        where = fdoc.locate(data)
        if where.state != fdoc.INTACT:
            plan.refused.append((plan.rel(path), "its managed block is not intact (%s)"
                                 % (where.reason or where.state)))
            continue
        new = (data[:where.start] + sigma_begin + data[where.start + len(old_begin):where.inner_stop]
               + sigma_end + data[where.inner_stop + len(old_end):])
        after = fdoc.locate(new)
        if (after.state != fdoc.INTACT or after.checksum != where.checksum
                or new[after.inner_start:after.inner_stop] != data[where.inner_start:where.inner_stop]
                or old_begin in new or old_end in new):
            plan.refused.append((plan.rel(path), "the respelled block did not read back identically"))
            continue
        _add_change(plan, Change(path, data, new, "managed-block markers respelled "
                                   "(body and digest unchanged)", path.stem))


def _plan_codex(plan, legacy):
    init = _load(_INIT, "sdlc_init")
    path = plan.root / "AGENTS.md"
    start, end = "<!-- sigma:codex:start -->", "<!-- sigma:codex:end -->"
    old_start = legacy.retired_spelling(start)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return
    except OSError as exc:
        plan.refused.append((plan.rel(path), "could not be read (%s)" % exc))
        return
    text = data.decode("utf-8", "surrogateescape")
    if old_start not in text and legacy.retired_spelling(end) not in text:
        return
    if start in text or end in text:
        plan.left.append((plan.rel(path), "it carries both Codex blocks: Sigma manages its own; the "
                          "other belongs to the plugin under its previous name"))
        return
    try:
        updated = init.codex_block_update(text, path)
    except ValueError as exc:
        plan.refused.append((plan.rel(path), str(exc)))
        return
    head = text[:text.index(old_start)] if old_start in text else None
    if (updated is None or head is None or not updated.startswith(head)
            or updated.count(start) != 1 or legacy.RETIRED + ":codex" in updated):
        plan.refused.append((plan.rel(path), "the Codex block could not be replaced cleanly"))
        return
    _add_change(plan, Change(path, data, updated.encode("utf-8", "surrogateescape"),
                               "Codex block regenerated with Sigma's text (was the previous "
                               "plugin's, naming its old skills)"))


_KEY_RE = r'(?<!\\)"%s"(?=\s*:)'


def _plan_config(plan, legacy):
    path = plan.sdlc_dir / "config.json"
    try:
        data = path.read_bytes()
        cfg = json.loads(data.decode("utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        plan.refused.append((plan.rel(path), "could not be read as JSON (%s)" % exc))
        return
    if not isinstance(cfg, dict):
        return
    if _OLD_JOURNAL_BLOCK in cfg:
        plan.left.append((plan.rel(path), "the previous %r block is not carried over to `journal`: "
                          "turning the journal on is an explicit opt-in (doctor reports it)"
                          % _OLD_JOURNAL_BLOCK))
    expected, new, what = copy.deepcopy(cfg), data, []
    for value in sorted({v for _k, v in legacy.legacy_env_values(cfg)}):
        target = legacy.sigma_env_name(value)
        if target not in legacy.FALLBACK_ENV:
            plan.left.append((plan.rel(path), "%s is not a variable Sigma knows; left as named"
                              % value))
            continue
        _replace_env_value(expected, value, target)
        new = new.replace(json.dumps(value).encode("utf-8"), json.dumps(target).encode("utf-8"))
        what.append("%s -> %s" % (value, target))
    channels = (cfg.get("drift_watch") or {}).get("channels") if isinstance(
        cfg.get("drift_watch"), dict) else None
    if isinstance(channels, dict) and legacy.RETIRED in channels:
        if legacy.BRAND in channels:
            plan.left.append((plan.rel(path), "drift_watch.channels has both the %r and the %r key; "
                              "Sigma reads %r (falling back to the other while it is blank) -- "
                              "resolve it by hand" % (legacy.RETIRED, legacy.BRAND, legacy.BRAND)))
        else:
            renamed = {}
            for key, value in expected["drift_watch"]["channels"].items():
                renamed[legacy.BRAND if key == legacy.RETIRED else key] = value
            expected["drift_watch"]["channels"] = renamed
            new = re.sub((_KEY_RE % re.escape(legacy.RETIRED)).encode("ascii"),
                         ('"%s"' % legacy.BRAND).encode("ascii"), new)
            what.append("drift_watch.channels.%s -> drift_watch.channels.%s"
                        % (legacy.RETIRED, legacy.BRAND))
    if not what:
        return
    if not _json_equal(new, expected):
        plan.refused.append((plan.rel(path), "rewriting it would change more than the listed values "
                             "(a listed name also appears elsewhere in the file): %s"
                             % "; ".join(what)))
        return
    _add_change(plan, Change(path, data, new, "; ".join(what)))


def _replace_env_value(node, old, new):
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).endswith("_env"):
                if value == old:
                    node[key] = new
                elif isinstance(value, list):
                    node[key] = [new if item == old else item for item in value]
            _replace_env_value(node[key], old, new)
    elif isinstance(node, list):
        for item in node:
            _replace_env_value(item, old, new)


def _plan_history(plan, legacy):
    for parts in _HISTORY_DIRS:
        base = plan.sdlc_dir.joinpath(*parts)
        if not base.is_dir():
            continue
        needles = (legacy.RETIRED.encode(), legacy.RETIRED.upper().encode(),
                   legacy.RETIRED.capitalize().encode())
        hits = 0
        for path in base.rglob("*"):
            try:
                if path.is_file() and any(n in path.read_bytes() for n in needles):
                    hits += 1
            except OSError:
                continue
        if hits:
            plan.left.append((plan.rel(base), "%d history file(s) mention the previous name; "
                              "history is not rewritten" % hits))


def _previous_install(home, legacy):
    base = pathlib.Path(home) / ".claude" / "plugins"
    found = []
    market = base / "marketplaces" / legacy.RETIRED
    if market.exists():
        found.append(str(market))
    try:
        if ('"%s@' % legacy.RETIRED) in (base / "installed_plugins.json").read_text(encoding="utf-8"):
            found.append(str(base / "installed_plugins.json"))
    except (OSError, ValueError):
        pass
    return found


def _hold_index(result):
    """#326, the deterministic half of `apply`'s rule: a unit record this run will NOT convert (a
    symlink, unreadable, ambiguous) keeps `features/index.json` in the previous schema too -- said
    in the dry run, so `--apply` never surprises."""
    planned = {result.rel_link(c.path) for c in result.changes}
    stuck = [rel for rel in _legacy_units(result) if rel not in planned]
    if not stuck:
        return
    for change in [c for c in result.changes if _is_index(result, c.path)]:
        result.changes.remove(change)
        result.refused.append((result.rel_link(change.path), _index_reason(stuck)))


def plan(sdlc_dir, environ=None, home=None):
    """-> a `Plan`. Reads only; never writes."""
    legacy = _load(_LOOP, "legacy")
    result = Plan(sdlc_dir)
    _plan_schemas(result, legacy)
    _plan_feature_docs(result, legacy)
    _plan_codex(result, legacy)
    _plan_config(result, legacy)
    _plan_history(result, legacy)
    _hold_index(result)
    for name in legacy.retired_env_set(os.environ if environ is None else environ):
        target = legacy.sigma_env_name(name)
        if target in legacy.FALLBACK_ENV:
            result.notes.append("env     %s is set in this shell; Sigma reads it while %s is unset "
                                "-- rename it where you set it" % (name, target))
        else:
            result.notes.append("env     %s is set in this shell; Sigma does not read it" % name)
    installed = _previous_install(pathlib.Path.home() if home is None else home, legacy)
    if installed:
        result.notes.append("note    the plugin under its previous name (%s) is installed on this "
                            "machine: %s" % (legacy.RETIRED, ", ".join(installed)))
    return result


# --------------------------------------------------------------------------- applying


def _running_watcher(sdlc_dir):
    """The pid of this SDLC dir's live watcher (live pid AND fresh heartbeat), or None."""
    try:
        wd = _load(_LOOP, "watch_daemon")
        interval, _warning = wd.resolve_interval(wd.read_config(str(sdlc_dir)), os.environ)
        p = wd.paths(str(sdlc_dir))
        if wd.already_running(p, wd.stale_after_seconds(interval), time.time()):
            return wd.pid_from_file(p.pid)
    except Exception as exc:                  # noqa: BLE001 - a probe that cannot run is "none seen",
        print("migrate: warning: could not check for a running watcher (%s: %s); proceeding as if "
              "none is running -- stop it yourself if one is" % (type(exc).__name__, exc),
              file=sys.stderr)                # ...but said aloud, never silently
        return None
    return None


def _write(change):
    """Atomic replace, refused if the file moved on since planning. -> None, or the refusal reason."""
    path = change.path
    if path.is_symlink():                     # became one after planning: the same refusal
        return _SYMLINK_REASON
    try:
        if path.read_bytes() != change.old:
            return "it changed after it was read; rerun"
    except OSError as exc:
        return "could not be re-read (%s)" % exc
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(change.new)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)
        except OSError:
            pass
        os.replace(tmp, path)
    except PermissionError as exc:
        return "in use by another process (%s); rerun" % exc
    except OSError as exc:
        return "could not be written (%s)" % exc
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return None


def _is_index(result, path):
    return pathlib.Path(path) == result.sdlc_dir / "features" / "index.json"


def _under_units(result, rel):
    return rel.startswith(result.rel(result.sdlc_dir / "features" / "units") + "/")


def _index_reason(names):
    return ("not converted: %s still in the previous plugin's schema (listed as refused here, "
            "or written after this run read it). Converting the index without it would make Sigma "
            "read that record -- which may be the complete, newer one -- as a partial delta under "
            "the older index. Left as is, both are read exactly as before; fix what is refused and "
            "rerun, and the index converts with the last record" % ", ".join(sorted(names)))


def _legacy_units(result):
    """-> rel paths under `features/units/` still declaring the previous schema id NOW (or that
    cannot be read), whatever the plan saw -- the previous plugin may have created one since."""
    legacy = _load(_LOOP, "legacy")
    units = result.sdlc_dir / "features" / "units"
    found = []
    try:
        paths = sorted(units.glob("*.json"))
    except (OSError, ValueError):
        return found
    for path in paths:
        try:
            doc = json.loads(path.read_bytes().decode("utf-8"))
        except OSError:
            found.append(result.rel_link(path))
            continue
        except ValueError:
            continue                          # not a record anyone could read, in either schema
        schema = doc.get("schema") if isinstance(doc, dict) else None
        if legacy.is_legacy_schema(schema) and legacy.canonical_schema(schema) != schema:
            found.append(result.rel_link(path))   # an id Sigma cannot read either way holds nothing
    return found


def apply(result):
    """Write every planned change. -> [(change, refusal-or-None)].

    #326: `features/index.json` is written LAST, and only once nothing under `features/units/` is
    left in the previous schema. The previous plugin writes its unit record first and rebuilds its
    index only on demand, so a record it holds is the newer statement of its unit; beside a Sigma
    index, `feature_registry.read` would merge it as a DELTA under the older entry (#314), and
    `repair` would make that permanent. Left in the previous schema, the index keeps shard-wins
    reading -- the rule that plugin itself reads by -- and a rerun converts it. The last check and
    the replace are not atomic (milliseconds apart); a record written in that gap is the one residue,
    and `repair` refuses a record newer than the index for exactly that reason."""
    fsync = _load(_LOOP, "feature_sync")
    done = []
    ordered = ([c for c in result.changes if not _is_index(result, c.path)]
               + [c for c in result.changes if _is_index(result, c.path)])
    for change in ordered:
        if _is_index(result, change.path):
            left = _legacy_units(result)
            if left:
                done.append((change, _index_reason(left)))
                continue
        fd = None
        if change.unit:
            try:
                fd = fsync._acquire(fsync.lock_path(str(result.sdlc_dir), change.unit))
            except Exception:                 # noqa: BLE001 - an unlockable name is written unlocked
                fd = None                     # (fail-open, exactly as feature_sync itself is)
        try:
            done.append((change, _write(change)))
        finally:
            fsync._release(fd)
    return done


# --------------------------------------------------------------------------- CLI


#: The acknowledgement flag -- spelled once, in coexist (`REPLACE_FLAG`); mirrored here so bad usage
#: is caught before anything is loaded. `tests/test_coexist.py` pins the two to one spelling.
_REPLACE = "--replace-old-plugin"


def _parse(argv):
    args = argv[1:]
    flags = [a for a in args if a.startswith("-")]
    pos = [a for a in args if not a.startswith("-")]
    if any(f not in ("--apply", _REPLACE) for f in flags) or len(pos) > 1:
        return None
    if _REPLACE in flags and "--apply" not in flags:
        return None
    return (pos[0] if pos else ".sdlc"), "--apply" in flags, _REPLACE in flags


def main(argv, environ=None, home=None, stdout=None):
    out = stdout or sys.stdout
    say = lambda line: print(line, file=out)          # noqa: E731
    if argv[1:] in (["-h"], ["--help"]):
        say(USAGE)
        return 0
    parsed = _parse(argv)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return 2
    sdlc_dir, do_apply, replace = parsed
    if not pathlib.Path(sdlc_dir).is_dir():
        print("migrate: %s is not a directory -- pass the project's .sdlc" % sdlc_dir,
              file=sys.stderr)
        return 2
    result = plan(sdlc_dir, environ=environ, home=home)
    legacy = _load(_LOOP, "legacy")
    say("migrate: %s for %s -- looking for the plugin's previous name %r (env prefix %s)."
        % ("APPLY" if do_apply else "DRY RUN (nothing is written; add --apply to write)",
           sdlc_dir, legacy.RETIRED, legacy.RETIRED_ENV_PREFIX))
    refused = list(result.refused)
    changed = []
    gated = False
    if result.changes and do_apply:
        # #240/#314: the old plugin still enabled is a NOTICE everywhere else in Sigma. THIS step
        # alone -- the conversion -- waits for an acknowledgement while that plugin can still run
        # here: it cannot read Sigma's registry, so a goal it starts afterwards writes a unit
        # record in its own schema (`feature_registry.merge_legacy_delta` merges it as a delta; the
        # reviewer's sequences on PR #319). A LIVE watcher stays a refusal (it may be writing the
        # old spellings).
        coexist = _load(_LOOP, "coexist")
        coexist.gate(sdlc_dir, "migrate.py --apply", env=environ, home=home, stream=out)
        pid = _running_watcher(sdlc_dir)
        if pid:
            say("refused all: a watcher (pid %s) is running for this .sdlc and may be writing the "
                "old spellings -- nothing was written, and Sigma never signals it. Stop it "
                "politely: %s; then rerun" % (pid, coexist.stop_lever(sdlc_dir)))
            return 2
        runs = coexist.runs_here(coexist.assess(sdlc_dir, env=environ, home=home))
        if runs and not replace:
            gated = True
            report = coexist.Report(str(sdlc_dir), tuple(runs))
            say("refused --apply: the plugin previously published as %r can still run on this "
                "repository (%s). %s. Nothing was written; the changes it would make are listed "
                "below." % (legacy.RETIRED, "; ".join(s.detail for s in runs),
                             coexist.REGISTRY_CAVEAT[0].upper() + coexist.REGISTRY_CAVEAT[1:]))
            steps = coexist.disable_steps(report)
            say("  next: stop it on this repository first -- %s -- then rerun: %s"
                % (" ; ".join(steps) if steps else "see `coexist.py check`",
                   coexist.migrate_command(sdlc_dir, "--apply")))
            say("  or, to convert with it still running (a copy of .sdlc/features is saved "
                "under .sdlc/%s/ first): %s" % ("/".join(coexist.BACKUP_PARTS),
                                        coexist.migrate_command(sdlc_dir, "--apply",
                                                                coexist.REPLACE_FLAG)))
        elif runs:
            path, error = coexist.backup_features(sdlc_dir)
            if error:
                say("refused all: %s acknowledged, but the registry backup could not be taken "
                    "(%s) -- nothing was written. Disable the old plugin on this repository "
                    "instead, then rerun without it." % (coexist.REPLACE_FLAG, error))
                return 2
            if path is not None:
                say("  backup  %s: a copy of %s taken before converting (%s)"
                    % (result.rel(path), result.rel(pathlib.Path(sdlc_dir) / "features"),
                       coexist.REGISTRY_CAVEAT))
    if result.changes and do_apply and not gated:
        for change, why in apply(result):
            if why:
                refused.append((result.rel_link(change.path), why))
            else:
                changed.append(change)
                say("  changed %s: %s" % (result.rel(change.path), change.what))
    else:
        for change in result.changes:
            say("  would change %s: %s" % (result.rel(change.path), change.what))
    for rel, why in result.left:
        say("  left as is %s: %s" % (rel, why))
    for rel, why in refused:
        say("  refused %s: %s" % (rel, why))
    for line in result.notes:
        say("  " + line)
    if result.changes:
        say("  note    every file listed as changed is one the plugin under its previous name also "
            "reads: a machine still running it can no longer read them, and committed ones "
            "(config.json, features/) reach teammates through git -- switch the team together.")
    if gated:
        say("migrate: nothing applied -- %d change(s) wait for the old plugin to be disabled on "
            "this repository (or for %s), %d refused, %d left as is."
            % (len(result.changes), coexist.REPLACE_FLAG, len(refused), len(result.left)))
        return 2
    if not result.changes and not refused:
        say("migrate: nothing to migrate in %s." % sdlc_dir)
    elif do_apply:
        say("migrate: %d change(s) applied, %d refused, %d left as is."
            % (len(changed), len(refused), len(result.left)))
    else:
        say("migrate: %d change(s) would be made, %d refused, %d left as is."
            % (len(result.changes), len(refused), len(result.left)))
    return 2 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

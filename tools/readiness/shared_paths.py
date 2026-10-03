#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""List every `.sdlc` path Sigma and the predecessor plugin both touch, and guard the shared ones.

USAGE: shared_paths.py scan  --sigma REPO --predecessor-dir DIR --json OUT [--table OUT.md] [--no-samples]
       shared_paths.py check --sigma REPO --fixture FIXTURE.json

`scan` is read-only on DIR. It hashes every file under DIR (sha256) before and after, and exits 2
without writing OUT if the digest moved. Path expressions are resolved by growth_audit.py's own
resolver, so this tool never reimplements it. The sample runs execute the predecessor only from a
temporary copy of DIR, never from DIR itself.

`check` needs no predecessor: it scans this repository for Sigma's writer sites, intersects them with
the committed list of paths the predecessor writes, and exits 2 for any site that has no vetted
entry (`compatible: true` with a reason, or `compatible: false` naming a filed issue).

GUARD CONTRACT: a best-effort static scan that detects the named write forms below. It does NOT detect: a path built at run
time or from config, environment or a directory listing; a write through a helper module the resolver cannot follow; a
subprocess or shell write; a shell script under `hooks/`; a destination held in a module constant, class attribute,
helper return, local alias, concatenation, f-string or `join`; a write call outside the fixed list; a wildcard-named file
through the backstop; a second write inside an already vetted function; anything under a `tests` path component, `*.pyw`
or an extensionless script; and any tree outside `skills/` and `hooks/`. A passing check means every site the scan can
resolve is vetted. It does not mean Sigma has no new writer.

WHAT A STATIC SCAN CANNOT SEE (stated here, repeated in docs/launch/shared-sdlc-paths.md): a path
assembled at run time from data (a config value, an environment variable, a directory listing); a
destination handed through a helper whose parameter this resolver cannot follow; a file written by
a subprocess the Python source only names; and anything outside the scanned `skills/` and `hooks/`
trees. A path it did not find is not thereby proven unshared.
"""

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import growth_audit as ga  # noqa: E402

SCOPES = ("skills/", "hooks/")
_ALIASES = {
    "<sdlc>": ".sdlc", "<sdlc_dir>": ".sdlc", "<features_dir>": ".sdlc/features",
    "<goals_dir>": ".sdlc/goals", "<state>": ".sdlc/state", "<state_dir>": ".sdlc/state",
}
_READ_METHODS = {"read_text", "read_bytes", "exists", "is_file", "is_dir", "glob", "rglob",
                 "iterdir", "stat"}
_OS_READS = {"exists", "isfile", "isdir", "getmtime", "getsize", "listdir", "scandir"}
STEP_TIMEOUT = 120


def tree_hash(directory):
    """sha256 over (relative path, file sha256) for every file, sorted globally by relative path.

    A symlink hashes its target text. The line for each file is `<relative path> NUL <sha256>`."""
    found = []
    for base, _dirs, files in os.walk(directory, followlinks=False):
        for name in files:
            full = os.path.join(base, name)
            found.append((os.path.relpath(full, directory), full))
    outer = hashlib.sha256()
    for rel, full in sorted(found):
        if os.path.islink(full):
            body = hashlib.sha256(os.readlink(full).encode("utf-8", "replace")).hexdigest()
        else:
            inner = hashlib.sha256()
            with open(full, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    inner.update(block)
            body = inner.hexdigest()
        outer.update(("%s\0%s\n" % (rel, body)).encode("utf-8", "replace"))
    return outer.hexdigest(), len(found)


def plugin_manifest(directory):
    path = Path(directory) / ".claude-plugin" / "plugin.json"
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(pattern):
    """One spelling for the same store across both codebases, or None when it is not an `.sdlc` store."""
    value = pattern.replace("\\", "/")
    head, _, tail = value.partition("/")
    if head in _ALIASES:
        value = _ALIASES[head] + ("/" + tail if tail else "")
    value = re.sub(r"^(?:\./)+", "", value)
    match = re.match(r"^<[^>]+>/(\.sdlc(?:/.*)?)$", value)
    if match:
        value = match.group(1)
    if not (value == ".sdlc" or value.startswith(".sdlc/")):
        return None
    value = re.sub(r"<[^>]*>", "*", value)
    value = re.sub(r"(?<=/)NNNN-\*", "*", value)
    value = re.sub(r"(?<=/)\d{4}-[^/*]*(?=\.md$)", "*", value)
    value = re.sub(r"\*+", "*", value)
    value = re.sub(r"(?<=/)\d+(?=\.|/|$)", "*", value)
    value = value.rstrip("/")
    segments = value.split("/")
    if len(segments) < 2 or segments[1] in ("*", ".sdlc", ""):
        return None  # `.sdlc` itself or "everything under it": matches all, says nothing
    if "<" in value or ">" in value or " " in value:
        return None  # a quoted fragment of prose or markup, not a path
    return value


def _wild(pattern):
    return re.compile("^" + ".*".join(re.escape(part) for part in pattern.split("*")) + "$")


def overlaps(left, right):
    """True when two canonical patterns can name the same file (a `*` stands for any text)."""
    return bool(_wild(left).match(right) or _wild(right).match(left))


def _relaxed_row(pattern, source, path, line, directory=False):
    if pattern is None:
        return None
    return {"pattern": ga._normalise(pattern, directory=directory), "source": source,
            "writer": "%s:%s" % (path, line)}


def _enclosing(trees, root, rel, line):
    key = (str(root), rel)
    if key not in trees:
        trees[key] = ast.parse((Path(root) / rel).read_text(encoding="utf-8", errors="replace"))
    best = None
    for node in ast.walk(trees[key]):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.lineno <= line <= (node.end_lineno or node.lineno)):
            if best is None or node.lineno > best.lineno:
                best = node
    return best.name if best else "<module>"


def _site(trees, root, writer):
    rel, _, line = writer.rpartition(":")
    return "%s::%s" % (rel, _enclosing(trees, root, rel, int(line)))


def scan_writers(root):
    """{canonical pattern: sorted writer site ids} from growth_audit's write-site scan."""
    root = Path(root)
    saved, ga._row = ga._row, _relaxed_row
    try:
        rows = ga.scan(root)
    finally:
        ga._row = saved
    trees, found = {}, {}
    for row in rows:
        if row["source"] != "code" or not row["writer"].startswith(SCOPES):
            continue
        pattern = canonical(row["pattern"])
        if pattern is not None:
            found.setdefault(pattern, set()).add(_site(trees, root, row["writer"]))
    for pattern, sites in supplementary_writers(root).items():
        found.setdefault(pattern, set()).update(sites)
    return {pattern: sorted(sites) for pattern, sites in sorted(found.items())}


#: Writers the resolver cannot reach (the destination is a helper parameter that is not named like a
#: store directory). Each is pinned by name and verified to exist in the scanned tree, so a rename
#: shows up as a missing site rather than a silently shrinking list.
SUPPLEMENTARY = {".sdlc/features/units/*.json": ("*-loop/scripts/feature_registry.py", "write_unit")}


def supplementary_writers(root):
    found = {}
    for pattern, (glob, name) in SUPPLEMENTARY.items():
        for path in sorted(Path(root).resolve().glob("skills/" + glob)):
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            if any(isinstance(n, ast.FunctionDef) and n.name == name for n in tree.body):
                rel = path.relative_to(Path(root).resolve()).as_posix()
                found.setdefault(pattern, []).append("%s::%s" % (rel, name))
    return found


_WRITEISH = {"write_text", "write_bytes", "open", "replace", "rename", "copy", "copy2", "copyfile",
             "copyfileobj", "copytree", "move", "mkdir", "touch", "dump", "write", "writelines", "symlink", "link",
             "fdopen", "mkstemp"}


def literal_sites(root, basenames):
    """{basename: sorted site ids}: functions that name a file literally AND make a write-ish call.

    A backstop for what the destination resolver misses (os.replace onto a path, shutil.copy, a
    `Path(a, "state", name)` built with commas): it keys on the file's own name, so it cannot tell
    which directory the file is in, and it also flags a function that writes something else while
    naming the file. Over-flagging costs a vetted entry, never a missed writer of a named file."""
    root = Path(root).resolve()
    wanted = set(basenames)
    found = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if not rel.startswith(SCOPES) or "tests" in path.relative_to(root).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        owners = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for function in owners:
            docstring = function.body[0].value if (
                function.body and isinstance(function.body[0], ast.Expr)
                and isinstance(function.body[0].value, ast.Constant)) else None
            names = {n.value for n in ast.walk(function)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not docstring}
            hit = sorted(wanted & names)
            if not hit:
                continue
            writes = any(isinstance(n, ast.Call) and (
                (isinstance(n.func, ast.Attribute) and n.func.attr in _WRITEISH)
                or (isinstance(n.func, ast.Name) and n.func.id in _WRITEISH)) for n in ast.walk(function))
            if writes:
                for name in hit:
                    found.setdefault(name, set()).add("%s::%s" % (rel, function.name))
    return {name: sorted(sites) for name, sites in sorted(found.items())}


def literal_basenames(patterns):
    out = set()
    for pattern in patterns:
        last = pattern.rsplit("/", 1)[-1]
        if "*" not in last and "." in last:
            out.add(last)
    return out


def _read_target(node):
    func = node.func
    if isinstance(func, ast.Name) and func.id == "open" and node.args:
        mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
        return node.args[0] if not any(flag in str(mode) for flag in "awx+") else None
    if isinstance(func, ast.Attribute):
        owner = func.value
        if func.attr in _READ_METHODS:
            return owner
        if (isinstance(owner, ast.Attribute) and owner.attr == "path" and isinstance(owner.value, ast.Name)
                and owner.value.id == "os" and func.attr in _OS_READS and node.args):
            return node.args[0]
        if isinstance(owner, ast.Name) and owner.id == "os" and func.attr in _OS_READS and node.args:
            return node.args[0]
    return None


def scan_readers(root):
    """{canonical pattern: sorted reader site ids}: reads, existence checks and listings."""
    root = Path(root)
    paths = [p for p in sorted(root.rglob("*.py"))
             if p.relative_to(root).as_posix().startswith(SCOPES)
             and "tests" not in p.relative_to(root).parts]
    functions = ga.function_table([p for p in sorted(root.rglob("*.py"))
                                   if "tests" not in p.relative_to(root).parts])
    found = {}
    for path in paths:
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        owners = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        owners += [m for c in tree.body if isinstance(c, ast.ClassDef)
                   for m in c.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for function in owners:
            calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)]
            targets = [(call, _read_target(call)) for call in calls]
            if not any(target is not None for _, target in targets):
                continue
            env = ga._scope(path, tree, function, functions, path.stem)
            for _call, target in targets:
                if target is None:
                    continue
                for value in ga._expression_path(target, env, functions, path.stem):
                    pattern = canonical(ga._normalise(value))
                    if pattern is not None:
                        found.setdefault(pattern, set()).add("%s::%s" % (rel, function.name))
    return {pattern: sorted(sites) for pattern, sites in sorted(found.items())}


def mark_directories(*maps):
    """Patterns that are a parent of another pattern are directories; their contents govern."""
    every = set()
    for mapping in maps:
        every |= set(mapping)
    return {p for p in every if any(o != p and o.startswith(p + "/") for o in every)}


# --------------------------------------------------------------------------- sample runs

def _env(home):
    keep = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    keep.update(HOME=home, CLAUDE_CONFIG_DIR=os.path.join(home, ".claude"),
                CODEX_HOME=os.path.join(home, ".codex"), PYTHONDONTWRITEBYTECODE="1",
                GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return keep


def _script(root, pattern):
    hits = sorted(Path(root).resolve().glob("skills/" + pattern))
    return str(hits[0]) if hits else None


_WIZARD_SNIPPET = (
    "import sys; sys.path.insert(0, sys.argv[1]); import setup_wizard as w; "
    "w.write_dismissed('.sdlc', ['gh auth', 'project layer']); "
    "w.wizard_status('.sdlc', run=lambda *a, **k: None)")

_SCENARIO = [
    ("start", "*-loop/scripts/loop.py", ["start", ".sdlc", "--session-pid", "{pid}"]),
    ("next", "*-loop/scripts/loop.py", ["next", ".sdlc", "--session-pid", "{pid}"]),
    ("agent-start", "*-loop/scripts/loop.py", ["agent-start", ".sdlc", "{goal}", "--pid", "{pid}"]),
    ("phase-start", "*-loop/scripts/phase_report.py",
     ["start", ".sdlc", "{goal}", "research", "--model", "sonnet", "--pid", "{pid}"]),
    ("note", "*-loop/scripts/loop.py", ["note", ".sdlc", "{goal}", "research: sample"]),
    ("log", "*-loop/scripts/loop.py", ["log", ".sdlc", "{goal}", "file", "--path", "a.txt", "--op", "create"]),
    ("phase-end", "*-loop/scripts/phase_report.py", ["end", ".sdlc", "{goal}", "research", "--pid", "{pid}"]),
    ("record", "*-loop/scripts/loop.py", ["record", ".sdlc", "{goal}", "done"]),
    ("session-end", "*-loop/scripts/loop.py", ["session-end", ".sdlc", "--session-pid", "{pid}"]),
]


def _run(argv, cwd, home):
    try:
        done = subprocess.run(argv, cwd=cwd, env=_env(home), capture_output=True, text=True,
                              timeout=STEP_TIMEOUT)
        return done.returncode, (done.stdout + done.stderr)
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def _prepare(root, repo, home):
    """git init + the side's own init script + one config patch shared by both sides."""
    Path(repo).mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q", "."], repo, home)
    init = _script(root, "*-init/scripts/*_init.py")
    code, out = _run([sys.executable, init, "."], repo, home)
    config = Path(repo) / ".sdlc" / "config.json"
    if code != 0 or not config.is_file():
        raise RuntimeError("init failed for a sample side: exit %s" % code)
    data = json.loads(config.read_text(encoding="utf-8"))
    data.setdefault("action_log", {})["enabled"] = True
    data.setdefault("journal", {})["enabled"] = True
    data.setdefault("work", {})["enabled"] = False
    config.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _first_goal(repo):
    goals = sorted((Path(repo) / ".sdlc" / "goals").glob("0*.md"))
    return ".sdlc/goals/%s" % goals[0].name


def run_scenario(root, repo, home, pid, patterns=(), seen=None):
    """Run the fixed command list on one side; -> [(label, exit code)].

    After every step the files written so far are fingerprinted into `seen` (first sighting wins), so
    a file the scenario later removes (a session marker, a claim lock) is still sampled."""
    goal = _first_goal(repo)
    results = []
    snap = (lambda: seen.update({k: v for k, v in collect_samples(repo, patterns).items()
                                 if k not in seen})) if seen is not None else (lambda: None)
    for label, pattern, args in _SCENARIO:
        script = _script(root, pattern)
        argv = [sys.executable, script] + [a.format(pid=pid, goal=goal) for a in args]
        code, _out = _run(argv, repo, home)
        results.append((label, code))
        snap()
    wizard = _script(root, "*-init/scripts/setup_wizard.py")
    code, _out = _run([sys.executable, "-c", _WIZARD_SNIPPET, str(Path(wizard).parent)], repo, home)
    results.append(("wizard", code))
    snap()
    return results


def _brand(value):
    if not isinstance(value, str) or "/" not in value:
        return None, value
    brand, _, rest = value.partition("/")
    return ("sigma" if brand == "sigma" else "predecessor"), rest


def signature(path):
    """Format fingerprint of one written file: keys and schema kind, never values or free text."""
    name = path.name
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"shape": "binary"}
    if name.endswith(".md"):
        return {"shape": "md", "headings": sum(1 for l in text.splitlines() if l.startswith("#"))}
    docs = []
    if name.endswith(".jsonl"):
        for line in text.splitlines():
            try:
                docs.append(json.loads(line))
            except ValueError:
                return {"shape": "jsonl-invalid"}
        shape = "jsonl"
    else:
        try:
            docs, shape = [json.loads(text)], "json"
        except ValueError:
            return {"shape": "text", "empty": not text.strip()}
    keys, schema, brand = set(), None, None
    for doc in docs:
        if isinstance(doc, dict):
            keys |= set(doc)
            if "schema" in doc:
                brand, schema = _brand(doc["schema"])
        elif isinstance(doc, list):
            keys.add("<list>")
    result = {"shape": shape, "keys": sorted(keys)}
    if schema is not None:
        result.update(schema=schema, schema_brand=brand)
    return result


def collect_samples(repo, patterns):
    """{pattern: signature} for each written file under repo/.sdlc matching a shared pattern."""
    found = {}
    base = Path(repo)
    for path in sorted((base / ".sdlc").rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        hits = [p for p in patterns if _wild(p).match(rel)]
        if hits:
            best = max(hits, key=lambda p: len(p.replace("*", "")))
            found.setdefault(best, signature(path))
    return found


def verdict_for(sigma_sig, predecessor_sig):
    """same-format | additive | different-format | unsampled, from two signatures."""
    if sigma_sig is None or predecessor_sig is None:
        return "unsampled"
    if sigma_sig.get("schema_brand") != predecessor_sig.get("schema_brand"):
        return "different-format"
    if sigma_sig.get("shape") != predecessor_sig.get("shape") or \
            sigma_sig.get("schema") != predecessor_sig.get("schema"):
        return "different-format"
    if sigma_sig == predecessor_sig:
        return "same-format"
    left, right = set(sigma_sig.get("keys", [])), set(predecessor_sig.get("keys", []))
    if left == right:
        return "same-format"
    return "additive" if (left < right or right < left) else "different-format"


def _sanitise(text, manifest):
    name = str(manifest.get("name", ""))
    text = re.sub(re.escape(name), "<predecessor>", text, flags=re.I) if name else text
    text = re.sub(r"/[^\s'\"]+", "<path>", text)
    return " ".join(text.split())[:160]


_REGISTRY_WRITER = (
    "import sys, pathlib; sys.path.insert(0, sys.argv[1]); import feature_registry as r; "
    "e = r.normalise_entry({'title': 't', 'owner': 'o', 'open': True, 'repos': {}}); "
    "r.write_index(pathlib.Path(sys.argv[2]), {'unit-a': e}); "
    "r.write_unit(pathlib.Path(sys.argv[3]), 'unit-b', e)")


def _write_registry(root, base, home):
    """Write an index.json (one unit, no shard) and, elsewhere, one unit shard, with `root`'s own code."""
    scripts = Path(_script(root, "*-loop/scripts/feature_registry.py")).parent
    _run([sys.executable, "-c", _REGISTRY_WRITER, str(scripts), str(base / "index-only"),
          str(base / "shards")], str(base), home)
    return base / "index-only" / "index.json", base / "shards" / "units" / "unit-b.json"


def registry_probe(sigma_root, predecessor_root, home, manifest):
    """Each side writes a registry; then the predecessor's own CLI folds Sigma's; what survives?"""
    scratch = Path(tempfile.mkdtemp(prefix="registry-", dir=home))
    sigs = {}
    for side, root in (("sigma", sigma_root), ("predecessor", predecessor_root)):
        base = scratch / side
        base.mkdir()
        index, unit = _write_registry(root, base, home)
        sigs[side] = {".sdlc/features/index.json": signature(index),
                      ".sdlc/features/units/*.json": signature(unit)}
    features = scratch / "sigma" / "index-only"
    sdlc = scratch / "fold" / ".sdlc"
    (sdlc / "features").mkdir(parents=True)
    shutil.copy(str(features / "index.json"), str(sdlc / "features" / "index.json"))
    sync = lambda root: _script(root, "*-loop/scripts/feature_sync.py")  # noqa: E731
    code, out = _run([sys.executable, sync(predecessor_root), "fold", str(sdlc)], str(scratch), home)
    after = signature(sdlc / "features" / "index.json")
    units_after = sorted(json.loads((sdlc / "features" / "index.json").read_text()).get("features", {}))
    code2, shown = _run([sys.executable, sync(sigma_root), "show", str(sdlc)], str(scratch), home)
    return sigs, {"setup": "Sigma writes .sdlc/features/index.json holding one unit and no unit shard",
                  "action": "the predecessor's own `feature_sync.py fold`", "exit": code,
                  "units_before": ["unit-a"], "units_after": units_after,
                  "index_schema_brand_before": sigs["sigma"][".sdlc/features/index.json"].get("schema_brand"),
                  "index_schema_brand_after": after.get("schema_brand"),
                  "sigma_reads_back_exit": code2, "sigma_reads_back_has_unit": "unit-a" in shown,
                  "predecessor_message": _sanitise(out, manifest)}


def _config_keys(path):
    return set(json.loads(Path(path).read_text(encoding="utf-8")))


def config_probe(sigma_root, predecessor_root, home):
    """Each side's `setup.py configure` over a config the other side wrote: which top-level keys vanish?"""
    result = {}
    sides = {"sigma": sigma_root, "predecessor": predecessor_root}
    for first, other in (("sigma", "predecessor"), ("predecessor", "sigma")):
        repo = tempfile.mkdtemp(prefix="config-", dir=home)
        _prepare(sides[first], repo, home)
        config = Path(repo) / ".sdlc" / "config.json"
        before = _config_keys(config)
        setup = _script(sides[other], "*-setup/scripts/setup.py")
        code, _out = _run([sys.executable, setup, "configure", str(Path(repo) / ".sdlc"),
                           "--source", "local-goals"], repo, home)
        result["%s_config_then_%s_configure" % (first, other)] = {
            "exit": code, "keys_lost": sorted(before - _config_keys(config))}
    return result


def sample_run(sigma_root, predecessor_root, manifest, patterns):
    work = tempfile.mkdtemp(prefix="shared-paths-")
    try:
        copy = os.path.join(work, "predecessor")
        shutil.copytree(predecessor_root, copy, symlinks=True)
        home = os.path.join(work, "home")
        os.makedirs(home)
        sides = {"sigma": sigma_root, "predecessor": copy}
        solo, sigs = {}, {}
        for side, root in sides.items():
            repo = os.path.join(work, "solo-" + side)
            _prepare(root, repo, home)
            sigs[side] = {}
            solo[side] = run_scenario(root, repo, home, os.getpid(), patterns, sigs[side])
        cross = []
        for first, second in (("sigma", "predecessor"), ("predecessor", "sigma")):
            repo = os.path.join(work, "cross-%s-first" % first)
            _prepare(sides[first], repo, home)
            runs = [run_scenario(sides[first], repo, home, os.getpid())]
            runs.append(run_scenario(sides[second], repo, home, os.getpid()))
            runs.append(run_scenario(sides[first], repo, home, os.getpid()))
            for index, run in enumerate(runs):
                side = (first, second, first)[index]
                for (label, code), (_l, base) in zip(run, solo[side]):
                    if code != base and index > 0 and label != "next":
                        cross.append({"order": "%s then %s" % (first, second), "run": index + 1,
                                      "side": side, "step": label, "solo_exit": base,
                                      "shared_repo_exit": code})
        reg_sigs, probe = registry_probe(sigma_root, copy, home, manifest)
        for side in sigs:
            sigs[side].update(reg_sigs[side])
        return {"solo_exit_codes": solo, "signatures": sigs, "cross_run_anomalies": cross,
                "registry_probe": probe, "config_probe": config_probe(sigma_root, copy, home)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- scan / table / check

def _slim(sig):
    """A long key list is published as its count and digest: names carry nothing a reader can act on."""
    if sig and len(sig.get("keys", [])) > 12:
        keys = sig["keys"]
        sig = {k: v for k, v in sig.items() if k != "keys"}
        sig.update(key_count=len(keys), keys_sha256=hashlib.sha256("\n".join(keys).encode()).hexdigest())
    return sig


def _modules(sites):
    """Predecessor sites reduced to `module.py::function`: the directory names are not ours to publish."""
    return sorted({"%s::%s" % (s.split("::")[0].rsplit("/", 1)[-1], s.split("::")[1]) for s in sites})


def _union(mapping, pattern):
    return sorted({site for key, sites in mapping.items() if overlaps(key, pattern) for site in sites})


def _function_fingerprint(root, site, brand_names):
    """The writer function with docstrings dropped and brand spellings neutralised, as an AST dump."""
    rel, _, name = site.partition("::")
    tree = ast.parse((Path(root) / rel).read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            for inner in ast.walk(node):
                body = getattr(inner, "body", None)
                if (isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and body
                        and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body[0].value.value = ""
            text = ast.dump(node, annotate_fields=False)
            for word in brand_names:
                text = re.sub(re.escape(word), "<brand>", text, flags=re.I)
            return re.sub(r"(?:sdlc|agrim)-", "<skill>-", text)
    return None


def identical_writers(sigma_root, predecessor_root, sigma_sites, predecessor_sites, brand_names):
    left = {_function_fingerprint(sigma_root, s, brand_names) for s in sigma_sites}
    right = {_function_fingerprint(predecessor_root, s, brand_names) for s in predecessor_sites}
    return None not in left and None not in right and left == right


def _corruption(pattern, sample):
    """What the probes showed for a different-format two-writer path: demonstrated / not demonstrated."""
    probe = sample["registry_probe"]
    if pattern == ".sdlc/features/index.json":
        lost = set(probe["units_before"]) - set(probe["units_after"])
        return "demonstrated" if lost and not probe["sigma_reads_back_has_unit"] else "not-demonstrated"
    if pattern == ".sdlc/config.json":
        lost = [k for v in sample["config_probe"].values() for k in v["keys_lost"]]
        return "not-demonstrated" if not lost else "demonstrated"
    return "not-probed"


def _publishable(sample):
    if sample is None:
        return None
    return dict(sample, signatures={side: {p: _slim(sig) for p, sig in found.items()}
                                    for side, found in sample["signatures"].items()})


def build(sigma_root, predecessor_root, samples=True):
    before, files = tree_hash(predecessor_root)
    manifest = plugin_manifest(predecessor_root)
    sigma_w, sigma_r = scan_writers(sigma_root), scan_readers(sigma_root)
    pred_w, pred_r = scan_writers(predecessor_root), scan_readers(predecessor_root)
    dirs = mark_directories(sigma_w, sigma_r, pred_w, pred_r)
    touched_s, touched_p = set(sigma_w) | set(sigma_r), set(pred_w) | set(pred_r)
    shared = [p for p in sorted(touched_s | touched_p)
              if (p in sigma_w or p in pred_w)
              and any(overlaps(p, q) for q in touched_s) and any(overlaps(p, q) for q in touched_p)]
    brands = ["sigma", str(manifest.get("name", "")) or "<none>"]
    sample = (sample_run(sigma_root, predecessor_root, manifest, shared) if samples else None)
    rows = []
    for pattern in shared:
        sw = _union(sigma_w, pattern)
        pw = _union(pred_w, pattern)
        sr = _union(sigma_r, pattern)
        pr = _union(pred_r, pattern)
        corruption = None
        if pattern in dirs:
            klass, verdict = "directory", "directory"
        elif sw and pw:
            klass = "two-writer"
            verdict = "unsampled"
            if sample is not None:
                verdict = verdict_for(sample["signatures"]["sigma"].get(pattern),
                                      sample["signatures"]["predecessor"].get(pattern))
            if verdict == "unsampled" and identical_writers(sigma_root, predecessor_root, sw, pw, brands):
                verdict = "identical-writer"
        elif sw or pw:
            klass, verdict = "one-writer", "one-writer"
        else:
            klass, verdict = "read-only", "read-only"
        if verdict == "different-format" and sample is not None:
            corruption = _corruption(pattern, sample)
        rows.append({"pattern": pattern, "class": klass, "verdict": verdict, "corruption": corruption,
                     "sigma_writers": sw, "predecessor_writers": _modules(pw),
                     "sigma_reader_count": len(sr), "predecessor_reader_count": len(pr),
                     "sigma_sample": _slim((sample or {}).get("signatures", {}).get("sigma", {}).get(pattern)),
                     "predecessor_sample": _slim((sample or {}).get("signatures", {}).get("predecessor", {}).get(pattern))})
    after, _count = tree_hash(predecessor_root)
    if after != before:
        raise RuntimeError("the predecessor directory changed during the scan")
    counts = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return {"predecessor": {"version": manifest.get("version"), "files_hashed": files,
                            "sha256_before": before, "sha256_after": after,
                            "unchanged": after == before},
            "scope": list(SCOPES), "counts": counts,
            "predecessor_written_patterns": sorted(pred_w),
            "sigma_written": sigma_w,
            "sigma_literal_writers": literal_sites(sigma_root, literal_basenames(pred_w)),
            "rows": rows, "samples": _publishable(sample)}


def _short(sites, limit=3):
    names = sorted({s.split("::")[0].rsplit("/", 1)[-1] for s in sites})
    return ", ".join(names[:limit]) + (" +%d" % (len(names) - limit) if len(names) > limit else "") if names else "-"


def render_table(result):
    """The Markdown table, one row per shared path, sorted by pattern."""
    lines = ["| Path pattern | Class | Verdict | Corruption path | Predecessor writes | Sigma writes | Readers (P / S) |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in result["rows"]:
        lines.append("| `%s` | %s | %s | %s | %s | %s | %d / %d |" % (
            row["pattern"], row["class"], row["verdict"], row.get("corruption") or "-",
            _short(row["predecessor_writers"]), _short(row["sigma_writers"]),
            row["predecessor_reader_count"], row["sigma_reader_count"]))
    return "\n".join(lines) + "\n"


def load_fixture(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check(sigma_root, fixture, writers=None, literals=None):
    """-> list of violation strings; empty when every Sigma writer of a predecessor path is vetted.

    `writers` and `literals` are already-computed `scan_writers` / `literal_sites` results, so a caller
    testing several fixtures against one tree scans it once."""
    predecessor_paths = list(fixture["predecessor_written"])
    entries = {entry["pattern"]: entry for entry in fixture["entries"]}
    problems = []
    literal = literal_sites(sigma_root, literal_basenames(predecessor_paths)) if literals is None else literals
    for owner in predecessor_paths:
        base = owner.rsplit("/", 1)[-1]
        entry = entries.get(owner)
        for site in literal.get(base, []):
            if entry is None or site not in entry.get("vetted_writers", []):
                problems.append("%s: new Sigma writer %s names the file literally and writes; it is not in the "
                                "vetted_writers of the entry for %s" % (owner, site, owner))
    for pattern, sites in (scan_writers(sigma_root) if writers is None else writers).items():
        matched = [p for p in predecessor_paths if overlaps(p, pattern)]
        for owner in matched:
            entry = entries.get(owner)
            if entry is None:
                problems.append("%s: Sigma writes it (%s) and the predecessor writes %s; no entry"
                                % (pattern, ", ".join(sites), owner))
                continue
            ok = ((entry.get("compatible") is True and str(entry.get("reason", "")).strip())
                  or (entry.get("compatible") is False
                      and re.fullmatch(r"#[0-9]+", str(entry.get("issue", "")))))
            if not ok:
                problems.append("%s: entry for %s needs `compatible: true` with a reason, or "
                                "`compatible: false` with a filed `issue`" % (pattern, owner))
            for site in sites:
                if site not in entry.get("vetted_writers", []):
                    problems.append("%s: new Sigma writer %s is not in the vetted_writers of the "
                                    "entry for %s" % (pattern, site, owner))
    return problems


_ORDER = ["same-format", "identical-writer", "additive", "directory"]
_REASONS = {
    "same-format": "Equal key sets and schema in a file each side's own CLI wrote (one fixed command list; "
                   "values and other command paths not compared).",
    "additive": "Same schema; one side writes extra keys the other lacks (sample, one fixed command list). "
                "Whether the other side keeps an unknown key on rewrite is not probed for this path.",
    "identical-writer": "No sample reached this path; every writer function is the same code on both sides apart "
                        "from brand spellings (a weaker evidence than a sample: helpers are not compared).",
    "directory": "Directory creation only; the files inside carry their own entries.",
    "different-format-kept": "Key sets differ; each side's setup.py configure, run over a config the other side "
                             "wrote, lost no top-level key in either order (probe).",
}


def guard_fixture(result, finding_issue, followup_issue):
    """The committed guard list from one scan: every predecessor-written path Sigma also writes."""
    sigma_w = result["sigma_written"]
    rows = {row["pattern"]: row for row in result["rows"]}
    entries = []
    for pattern in result["predecessor_written_patterns"]:
        sites = {site for key, found in sigma_w.items() if overlaps(key, pattern) for site in found}
        sites |= set(result["sigma_literal_writers"].get(pattern.rsplit("/", 1)[-1], []))
        sites = sorted(sites)
        if not sites:
            continue
        row = rows[pattern]
        verdict, corruption = row["verdict"], row.get("corruption")
        if verdict in _ORDER:
            entry = {"compatible": True, "reason": _REASONS[verdict]}
        elif verdict == "different-format" and corruption == "not-demonstrated":
            entry = {"compatible": True, "reason": _REASONS["different-format-kept"]}
        elif verdict == "different-format":
            entry = {"compatible": False, "issue": finding_issue,
                     "reason": "Different format (schema id spelling); the predecessor's own fold empties a "
                               "registry Sigma wrote (probe); filed as a B1 candidate."}
        else:
            entry = {"compatible": False, "issue": followup_issue,
                     "reason": "No sample reached this path and the writers differ; compatibility is unverified."}
        entry.update(pattern=pattern, verdict=verdict, vetted_writers=sites)
        entries.append(dict(sorted(entry.items())))
    pre = result["predecessor"]
    return {"predecessor_version": pre["version"], "predecessor_sha256_before": pre["sha256_before"],
            "predecessor_sha256_after": pre["sha256_after"],
            "predecessor_written": list(result["predecessor_written_patterns"]), "entries": entries}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--sigma", type=Path, required=True)
    scan.add_argument("--predecessor-dir", type=Path, required=True)
    scan.add_argument("--json", type=Path, required=True)
    scan.add_argument("--table", type=Path)
    scan.add_argument("--no-samples", action="store_true")
    scan.add_argument("--fixture-out", type=Path)
    scan.add_argument("--finding-issue", default="#0")
    scan.add_argument("--followup-issue", default="#0")
    chk = sub.add_parser("check")
    chk.add_argument("--sigma", type=Path, required=True)
    chk.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "scan":
        try:
            result = build(args.sigma, args.predecessor_dir, samples=not args.no_samples)
        except RuntimeError as exc:
            print("shared_paths.py: REFUSED: %s" % exc, file=sys.stderr)
            return 2
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.table:
            args.table.write_text(render_table(result), encoding="utf-8")
        if args.fixture_out:
            fixture = guard_fixture(result, args.finding_issue, args.followup_issue)
            args.fixture_out.write_text(json.dumps(fixture, indent=1) + "\n", encoding="utf-8")
        print("shared_paths.py: %d shared paths; counts %s" % (len(result["rows"]), result["counts"]))
        return 0
    problems = check(args.sigma, load_fixture(args.fixture))
    for problem in problems:
        print("shared_paths.py: REFUSED: %s" % problem, file=sys.stderr)
    return 2 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

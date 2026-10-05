"""#347: the `.sdlc` paths Sigma and the predecessor plugin both write, and the guard that stops Sigma
newly writing one of them without a vetted, compatible entry.

Every test reaches the tool, the fixture or the doc through `_tool()`, `_fixture()` or `_doc()`, which
`assert` the file exists. Against a tree without them each test therefore fails on an AssertionError
(a real red for tests/red_green), not on an ImportError.

The predecessor is never needed here: the fixture carries what its scan found. The scan itself
(`tools/readiness/shared_paths.py scan`) needs the owner's installed copy and is run by hand; see
docs/launch/shared-sdlc-paths.md.

GUARD CONTRACT (one claim, no more):
a best-effort static scan that detects the named write forms below. It does NOT detect: a path built at run
time or from config, environment or a directory listing; a write through a helper module the resolver cannot follow; a
subprocess or shell write; a shell script under `hooks/`; a destination held in a module constant, class attribute,
helper return, local alias, concatenation, f-string or `join`; a write call outside the fixed list; a wildcard-named file
through the backstop; a second write inside an already vetted function; anything under a `tests` path component, `*.pyw`
or an extensionless script; and any tree outside `skills/` and `hooks/`. A passing check means every site the scan can
resolve is vetted. It does not mean Sigma has no new writer.

Timing: the real-tree writer scan is done once per process (about 10 s); the two CLI controls each scan
a scratch copy of skills/ and hooks/ (about 10 s each).
"""
import copy
import functools
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "readiness" / "shared_paths.py"
FIXTURE = REPO / "tests" / "fixtures" / "predecessor_written_paths.json"
DOC = REPO / "docs" / "launch" / "shared-sdlc-paths.md"
WIZARD_PATH = ".sdlc/state/setup-wizard-dismissed.json"
WIZARD_SITE = "skills/sigma-init/scripts/setup_wizard.py::write_dismissed"


@functools.lru_cache(maxsize=None)
def _tool():
    assert TOOL.is_file(), "tools/readiness/shared_paths.py is missing"
    spec = importlib.util.spec_from_file_location("shared_paths_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture():
    assert FIXTURE.is_file(), "tests/fixtures/predecessor_written_paths.json is missing"
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _doc():
    assert DOC.is_file(), "docs/launch/shared-sdlc-paths.md is missing"
    return DOC.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=None)
def _real_writers():
    return _tool().scan_writers(REPO)


def _write(base, rel, text):
    path = Path(base) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _plugin(base, name, version, files):
    _write(base, ".claude-plugin/plugin.json", json.dumps({"name": name, "version": version}))
    for rel, text in files.items():
        _write(base, rel, text)
    return Path(base)


WRITER = (
    "def write_state(sdlc_dir, goal):\n"
    "    \"\"\"%s\"\"\"\n"
    "    (sdlc_dir / 'state' / 'claims').mkdir(parents=True, exist_ok=True)\n"
    "    (sdlc_dir / 'state' / 'claims' / (goal + '.claimed')).write_text('x')\n")


# ---------------------------------------------------------------- fixture and doc

def test_fixture_is_wellformed():
    data = _fixture()
    assert re.fullmatch(r"[0-9a-f]{64}", data["predecessor_sha256_before"])
    assert data["predecessor_sha256_before"] == data["predecessor_sha256_after"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", data["predecessor_version"])
    paths = data["predecessor_written"]
    assert paths == sorted(set(paths)) and paths
    assert all(p.startswith(".sdlc/") for p in paths)
    names = [e["pattern"] for e in data["entries"]]
    assert len(names) == len(set(names))
    for entry in data["entries"]:
        assert entry["pattern"] in paths
        assert entry["vetted_writers"], entry
        assert str(entry["reason"]).strip(), entry
        assert entry["compatible"] is True or re.fullmatch(r"#[1-9][0-9]*", entry["issue"]), entry


def test_fixture_and_doc_carry_no_host_path():
    for text in (json.dumps(_fixture()), _doc()):
        assert not re.search(r"/Users/|/home/|\.claude/plugins|C:\\\\", text)


def test_doc_lists_every_predecessor_written_path_and_the_run():
    doc, data = _doc(), _fixture()
    missing = [p for p in data["predecessor_written"] if "`%s`" % p not in doc]
    assert missing == [], "the table must list every path the fixture lists: %s" % missing
    assert data["predecessor_version"] in doc
    assert data["predecessor_sha256_before"] in doc and data["predecessor_sha256_after"] in doc


def test_unfiled_entries_do_not_exist():
    """Every `compatible: false` entry names a filed issue; no placeholder number survives."""
    for entry in _fixture()["entries"]:
        if entry["compatible"] is False:
            assert entry["issue"] != "#0", entry


# ---------------------------------------------------------------- hashing and read-only

def test_tree_hash_moves_with_one_changed_byte(tmp_path):
    tool = _tool()
    _write(tmp_path, "a/b.txt", "one")
    _write(tmp_path, "c.txt", "two")
    before, count = tool.tree_hash(tmp_path)
    assert count == 2 and tool.tree_hash(tmp_path)[0] == before
    _write(tmp_path, "c.txt", "twp")
    assert tool.tree_hash(tmp_path)[0] != before


def test_scan_refuses_when_the_predecessor_changes_during_it(tmp_path, monkeypatch):
    tool = _tool()
    sigma = _plugin(tmp_path / "s", "sigma", "1.0.0", {"skills/a-loop/scripts/m.py": WRITER % "d"})
    pred = _plugin(tmp_path / "p", "other", "1.4.28", {"skills/b-loop/scripts/m.py": WRITER % "d"})
    real = tool.scan_writers

    def mutate(root):
        found = real(root)
        if Path(root) == pred:
            (pred / "skills" / "b-loop" / "scripts" / "m.py").write_text("# changed during the scan\n")
        return found

    monkeypatch.setattr(tool, "scan_writers", mutate)
    try:
        tool.build(sigma, pred, samples=False)
    except RuntimeError as exc:
        assert "changed during the scan" in str(exc)
    else:
        raise AssertionError("a predecessor tree that changed during the scan was accepted")


def test_samples_run_from_a_copy_and_leave_the_original_untouched(tmp_path, monkeypatch):
    tool = _tool()
    pred = _plugin(tmp_path / "p", "other", "1.4.28", {"skills/b-loop/scripts/m.py": WRITER % "d"})
    sigma = _plugin(tmp_path / "s", "sigma", "1.0.0", {"skills/a-loop/scripts/m.py": WRITER % "d"})
    ran = []
    monkeypatch.setattr(tool, "_prepare", lambda root, repo, home: ran.append(("prepare", str(root), str(home))))
    monkeypatch.setattr(tool, "run_scenario", lambda root, repo, home, pid, patterns=(), seen=None:
                        ran.append(("scenario", str(root), str(home))) or [])
    monkeypatch.setattr(tool, "registry_probe", lambda *a: ({"sigma": {}, "predecessor": {}}, {}))
    monkeypatch.setattr(tool, "config_probe", lambda *a: {})
    before = tool.tree_hash(pred)
    tool.sample_run(sigma, pred, {"name": "other"}, [])
    assert tool.tree_hash(pred) == before
    assert ran, "the stubbed scenario never ran"
    roots = {root for _kind, root, _home in ran}
    assert str(pred) not in roots and str(sigma) in roots, "the predecessor must run from a copy"
    homes = {home for _kind, _root, home in ran}
    assert len(homes) == 1 and "shared-paths-" in next(iter(homes))
    env = tool._env("/tmp/example-home")
    assert env["HOME"] == "/tmp/example-home" and env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_growth_audit_row_filter_is_restored_after_a_failed_scan(tmp_path, monkeypatch):
    tool = _tool()
    ga = tool.ga
    original = ga._row

    def boom(root):
        raise ValueError("scan failed")

    monkeypatch.setattr(ga, "scan", boom)
    try:
        tool.scan_writers(tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("the injected failure did not propagate")
    assert ga._row is original


def test_growth_audit_scan_is_unchanged_by_the_function_table_split(tmp_path):
    ga = _tool().ga
    assert hasattr(ga, "function_table"), "growth_audit.function_table is missing"
    _write(tmp_path, "skills/a/scripts/w.py",
           "def put(sdlc_dir):\n    (sdlc_dir / 'state' / 'x.json').write_text('{}')\n")
    rows = ga.scan(tmp_path)
    assert [r["pattern"] for r in rows] == [".sdlc/state/x.json"]
    assert ga.function_table(sorted(tmp_path.rglob("*.py")))["w"]["put"].name == "put"


# ---------------------------------------------------------------- canonical spelling and overlap

def test_canonical_spellings_meet():
    c = _tool().canonical
    assert c("<features_dir>/index.json") == ".sdlc/features/index.json"
    assert c("<sdlc_dir>/state/log/<goal>.jsonl") == ".sdlc/state/log/*.jsonl"
    assert c(".sdlc/goals/NNNN-*.md") == ".sdlc/goals/*.md"
    assert c("<target_dir>/.sdlc/config.json") == ".sdlc/config.json"
    assert c(".sdlc/state/sessions/123.active") == ".sdlc/state/sessions/*.active"
    assert c("<goals_dir>/<gid>.md") == ".sdlc/goals/*.md"
    assert c(".sdlc/state/") == ".sdlc/state"
    for not_a_store in (".sdlc", ".sdlc/*", "<workdir>/x.json", "docs/a.md", ".sdlc/.sdlc/", ".sdlc/<a b>/x"):
        assert c(not_a_store) is None, not_a_store


def test_overlap_treats_a_star_as_any_text():
    o = _tool().overlaps
    assert o(".sdlc/state/time/*", ".sdlc/state/time/*/x.jsonl")
    assert o(".sdlc/state/sessions/*.active", ".sdlc/state/sessions/*-*.active")
    assert not o(".sdlc/state/a.json", ".sdlc/state/b.json")


# ---------------------------------------------------------------- scanners on synthetic trees

def test_writers_and_readers_are_found_in_a_synthetic_tree(tmp_path):
    tool = _tool()
    root = _plugin(tmp_path, "sigma", "1.0.0", {
        "skills/a-loop/scripts/m.py": WRITER % "d" + (
            "def peek(sdlc_dir):\n    return (sdlc_dir / 'state' / 'inbox.md').read_text()\n"
            "def put_index(features_dir):\n    (features_dir / 'index.json').write_text('{}')\n"),
        "tests/skip.py": "def f(sdlc_dir):\n    (sdlc_dir / 'state' / 'nope.json').write_text('')\n",
        "tools/not_shipped.py": "def f(sdlc_dir):\n    (sdlc_dir / 'state' / 'tool.json').write_text('')\n",
    })
    writers = tool.scan_writers(root)
    assert writers[".sdlc/state/claims/*.claimed"] == ["skills/a-loop/scripts/m.py::write_state"]
    assert writers[".sdlc/features/index.json"] == ["skills/a-loop/scripts/m.py::put_index"]
    assert ".sdlc/state/nope.json" not in writers and ".sdlc/state/tool.json" not in writers
    assert tool.scan_readers(root)[".sdlc/state/inbox.md"] == ["skills/a-loop/scripts/m.py::peek"]


def test_identical_writers_ignore_docstrings_and_brand_but_not_code(tmp_path):
    tool = _tool()
    a = _plugin(tmp_path / "a", "sigma", "1", {"skills/a-loop/scripts/m.py": WRITER % "Sigma's claim"})
    b = _plugin(tmp_path / "b", "oldname", "1", {"skills/b-loop/scripts/m.py": WRITER % "Oldname claim"})
    c = _plugin(tmp_path / "c", "oldname", "1", {
        "skills/c-loop/scripts/m.py": (WRITER % "d").replace("'x'", "'y'")})
    site_a, site_b, site_c = ("skills/%s-loop/scripts/m.py::write_state" % n for n in "abc")
    brands = ["sigma", "oldname"]
    assert tool.identical_writers(a, b, [site_a], [site_b], brands) is True
    assert tool.identical_writers(a, c, [site_a], [site_c], brands) is False
    assert tool.identical_writers(a, c, [site_a], ["skills/c-loop/scripts/m.py::missing"], brands) is False


def test_directories_are_marked_and_two_writers_are_classified(tmp_path):
    tool = _tool()
    sigma = _plugin(tmp_path / "s", "sigma", "1.0.0", {"skills/a-loop/scripts/m.py": WRITER % "d"})
    pred = _plugin(tmp_path / "p", "oldname", "1.4.28", {
        "skills/b-loop/scripts/m.py": WRITER % "d",
        "skills/b-loop/scripts/only.py":
            "def f(sdlc_dir):\n    (sdlc_dir / 'state' / 'solo.json').write_text('{}')\n"})
    result = tool.build(sigma, pred, samples=False)
    by = {row["pattern"]: row for row in result["rows"]}
    assert by[".sdlc/state/claims"]["class"] == "directory"
    row = by[".sdlc/state/claims/*.claimed"]
    assert (row["class"], row["verdict"]) == ("two-writer", "identical-writer")
    assert ".sdlc/state/solo.json" not in by, "a path only the predecessor touches is not shared"
    assert result["predecessor"]["unchanged"] is True and result["predecessor"]["version"] == "1.4.28"
    assert all("/" not in site.split("::")[0] for site in row["predecessor_writers"]), \
        "predecessor sites are published as module names only"


def test_verdict_table():
    v = _tool().verdict_for
    same = {"shape": "json", "keys": ["a", "b"]}
    assert v(same, dict(same)) == "same-format"
    assert v(same, {"shape": "json", "keys": ["a", "b", "c"]}) == "additive"
    assert v(same, {"shape": "json", "keys": ["a", "z"]}) == "different-format"
    assert v(dict(same, schema="features@1", schema_brand="sigma"),
             dict(same, schema="features@1", schema_brand="predecessor")) == "different-format"
    assert v(same, {"shape": "jsonl", "keys": ["a", "b"]}) == "different-format"
    assert v(same, None) == "unsampled"


def test_signature_records_keys_and_schema_kind_never_values(tmp_path):
    sig = _tool().signature
    got = sig(_write(tmp_path, "i.json", json.dumps({"schema": "oldname/features@1", "features": {"a": 1}})))
    assert got == {"shape": "json", "keys": ["features", "schema"], "schema": "features@1",
                   "schema_brand": "predecessor"}
    assert "oldname" not in json.dumps(got)
    assert sig(_write(tmp_path, "s.json", json.dumps({"schema": "sigma/features@1"})))["schema_brand"] == "sigma"
    assert sig(_write(tmp_path, "l.jsonl", '{"a":1}\n{"b":2}\n'))["keys"] == ["a", "b"]
    assert sig(_write(tmp_path, "w.json", '["x"]'))["keys"] == ["<list>"]


# ---------------------------------------------------------------- the guard on this repository

def test_the_scan_still_sees_the_known_shared_writers():
    writers = _real_writers()
    assert WIZARD_SITE in writers[WIZARD_PATH], "the wizard's dismissed-file writer is not found any more"
    assert ".sdlc/features/units/*.json" in writers, "the pinned registry shard writer was renamed or removed"
    assert ".sdlc/features/index.json" in writers


def test_guard_passes_on_this_repository():
    assert _tool().check(REPO, _fixture(), writers=_real_writers()) == []


def test_guard_refuses_a_path_with_no_entry():
    data = _fixture()
    data["entries"] = [e for e in data["entries"] if e["pattern"] != WIZARD_PATH]
    problems = _tool().check(REPO, data, writers=_real_writers())
    assert problems and all(WIZARD_PATH in p and "no entry" in p for p in problems)


def test_guard_refuses_a_compatible_entry_without_a_reason_or_issue():
    tool, data = _tool(), copy.deepcopy(_fixture())
    entry = next(e for e in data["entries"] if e["pattern"] == WIZARD_PATH)
    entry.update(compatible=True, reason="  ")
    assert any("needs `compatible: true` with a reason" in p
               for p in tool.check(REPO, data, writers=_real_writers()))
    entry.update(compatible=False, issue="soon")
    assert any("filed `issue`" in p for p in tool.check(REPO, data, writers=_real_writers()))
    entry.update(compatible=False, issue="#12")
    assert tool.check(REPO, data, writers=_real_writers()) == []


def test_guard_refuses_a_new_writer_site_on_an_already_vetted_path():
    tool, data = _tool(), copy.deepcopy(_fixture())
    entry = next(e for e in data["entries"] if e["pattern"] == WIZARD_PATH)
    entry["vetted_writers"] = [s for s in entry["vetted_writers"] if s != WIZARD_SITE]
    problems = tool.check(REPO, data, writers=_real_writers())
    assert any(WIZARD_SITE in p and "not in the vetted_writers" in p for p in problems)


def test_a_new_path_the_predecessor_does_not_write_is_not_refused(tmp_path):
    tool, data = _tool(), _fixture()
    writers = dict(_real_writers())
    writers[".sdlc/state/only-sigma-has-this.json"] = ["skills/x/scripts/y.py::z"]
    assert tool.check(REPO, data, writers=writers) == []


# ---------------------------------------------------------------- the control, on the gesture the docs give

def _scratch_copy(tmp_path):
    scratch = tmp_path / "scratch"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "evals")
    for name in ("skills", "hooks"):
        shutil.copytree(REPO / name, scratch / name, ignore=ignore)
    return scratch


def _doc_gesture(scratch):
    """The invocation docs/launch/shared-sdlc-paths.md prescribes for the guard."""
    assert "python3 tools/readiness/shared_paths.py check --sigma" in _doc()
    return subprocess.run([sys.executable, str(TOOL), "check", "--sigma", str(scratch), "--fixture", str(FIXTURE)],
                          capture_output=True, text=True, cwd=REPO)


def test_control_a_new_writer_on_the_wizard_path_turns_the_documented_gesture_red(tmp_path):
    scratch = _scratch_copy(tmp_path)
    clean = _doc_gesture(scratch)
    assert clean.returncode == 0, clean.stderr
    wizard = scratch / "skills" / "sigma-init" / "scripts" / "setup_wizard.py"
    wizard.write_text(wizard.read_text(encoding="utf-8") + (
        "\n\ndef _control_extra_writer(sdlc_dir):\n"
        "    (sdlc_dir / 'state' / 'setup-wizard-dismissed.json').write_text('[]', encoding='utf-8')\n"),
        encoding="utf-8")
    red = _doc_gesture(scratch)
    assert red.returncode == 2, red.stdout + red.stderr
    assert "setup_wizard.py::_control_extra_writer" in red.stderr and WIZARD_PATH in red.stderr


VARIANTS = {
    "os.replace onto the path": "import os\n    os.replace('tmp', str(sdlc_dir / 'state' / 'setup-wizard-dismissed.json'))\n",
    "shutil.copy to the path": "import shutil\n    shutil.copy('a', str(sdlc_dir) + '/state/' + 'setup-wizard-dismissed.json')\n",
    "comma-built Path write": "from pathlib import Path\n    Path(sdlc_dir, 'state', 'setup-wizard-dismissed.json').write_text('[]')\n",
    "copytree to the path": "import shutil\n    shutil.copytree('a', 'setup-wizard-dismissed.json')\n",
    "rename onto the path": "from pathlib import Path\n    Path('t').rename(Path(sdlc_dir, 'state', 'setup-wizard-dismissed.json'))\n",
}


def test_control_write_methods_the_destination_resolver_misses_are_caught_by_the_literal_backstop(tmp_path):
    tool, data = _tool(), _fixture()
    scratch = _scratch_copy(tmp_path)
    wizard = scratch / "skills" / "sigma-init" / "scripts" / "setup_wizard.py"
    original = wizard.read_text(encoding="utf-8")
    base = tool.literal_sites(scratch, tool.literal_basenames(data["predecessor_written"]))
    assert tool.check(scratch, data, writers=_real_writers(), literals=base) == []
    for label, body in VARIANTS.items():
        wizard.write_text(original + "\n\ndef _control_variant(sdlc_dir):\n    " + body, encoding="utf-8")
        found = tool.check(scratch, data, writers=_real_writers(),
                           literals=tool.literal_sites(scratch, tool.literal_basenames(data["predecessor_written"])))
        assert any("_control_variant" in p for p in found), label
    nests = {
        "inside an if block": "\nif True:\n    def _control_variant(sdlc_dir):\n        open('setup-wizard-dismissed.json', 'w')\n",
        "inside a try block": "\ntry:\n    def _control_variant(sdlc_dir):\n        open('setup-wizard-dismissed.json', 'w')\nexcept ImportError:\n    pass\n",
        "an async def": "\nasync def _control_variant(sdlc_dir):\n    open('setup-wizard-dismissed.json', 'w')\n",
        "a nested function": "\ndef _outer():\n    def _control_variant(sdlc_dir):\n        open('setup-wizard-dismissed.json', 'w')\n",
    }
    for label, code in nests.items():
        wizard.write_text(original + code, encoding="utf-8")
        found = tool.check(scratch, data, writers=_real_writers(),
                           literals=tool.literal_sites(scratch, tool.literal_basenames(data["predecessor_written"])))
        assert any("_control_variant" in p for p in found), label
    wizard.write_text(original, encoding="utf-8")


def test_tree_hash_does_not_depend_on_directory_walk_order(tmp_path):
    tool = _tool()
    for rel in ("b/a.txt", "a/z.txt", "a.txt", "b.txt"):
        _write(tmp_path, rel, rel)
    expected = hashlib.sha256()
    for rel in sorted(["b/a.txt", "a/z.txt", "a.txt", "b.txt"]):
        body = hashlib.sha256(rel.encode()).hexdigest()
        expected.update(("%s\0%s\n" % (rel, body)).encode())
    assert tool.tree_hash(tmp_path)[0] == expected.hexdigest()


def test_numbered_goal_files_share_one_pattern():
    assert _tool().canonical(".sdlc/goals/0000-demo.md") == ".sdlc/goals/*.md"

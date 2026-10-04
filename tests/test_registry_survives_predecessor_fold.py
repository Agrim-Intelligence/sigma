"""#514: the predecessor's own `feature_sync.py fold` empties a Sigma-written `.sdlc/features/index.json`.

MEASURED (scratch repository, fake HOME, the predecessor's installed scripts run only from a temporary
COPY): Sigma writes the sheet in its own schema with one unit and no shard; the predecessor's `fold`
says the document declares a schema other than its own so none of it was read, EXITS 0, and writes the
sheet anyway -- an empty registry under ITS schema id. Sigma then reads zero units. With a unit shard
in the predecessor's schema for some other unit, its fold writes exactly that one unit and the
Sigma unit is lost the same way.

Sigma cannot stop another program's write. What it does (see docs/upgrading.md, "A fold by the old
plugin"): keep a Sigma-only copy of the sheet under `.sdlc/state/backup/` (a path the predecessor never
reads or writes), say so loudly at every gated verb when the sheet comes back in the predecessor's
schema without units the copy holds, and give ONE explicit lever, `feature_sync.py recover`.
Nothing rewrites the tracked sheet unprompted. That is a mitigation, not a sheet the predecessor's
fold leaves alone -- the docs say so, with the residuals.

TWO CONTROLS ON THE FOLD, BOTH ON THE GESTURE THE DOCS GIVE (`python3 -m pytest
tests/test_registry_survives_predecessor_fold.py`):

* `model_fold` is a MODEL, NOT THE REAL THING: an in-tree stand-in written from the measurement above
  (rc 0; a document in any other schema reads as empty; the shards in its own schema are read;
  an empty or short registry is written under its own schema id). It always runs, so it carries the red
  in CI, and it is only as faithful as that measurement.
* `test_real_predecessor_fold_*` runs the predecessor's actual `fold` from a temporary copy of its
  installed scripts, asserts the installed scripts' content hash did not move, and SKIPS with a named
  reason when no predecessor is installed (CI has none). It is local evidence.

Every old-name spelling is built from `legacy.RETIRED`: the previous name is a guarded private name in
this tree (`tests/test_no_private_names.py`). Fixtures write the copy and the sheets as literal bytes
at literal paths, so a red result on old code is the behaviour (an emptied sheet, no signal, a
converted sheet), not a missing function.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP = ROOT / "skills" / "agrim-loop" / "scripts"
INIT = ROOT / "skills" / "agrim-init" / "scripts" / "sdlc_init.py"
MIGRATE = ROOT / "skills" / "agrim-doctor" / "scripts" / "migrate.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, LOOP / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry = _load("feature_registry")
fsync = _load("feature_sync")
legacy = registry.legacy
SIGMA_ID = registry.SCHEMA
OLD_ID = legacy.RETIRED + "/features@1"
#: Captured at import, before any autouse fixture points the host inventory at an empty directory.
_HOST_CLAUDE = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or (pathlib.Path.home() / ".claude"))


# --------------------------------------------------------------------------- fixtures


def _text(units, schema=SIGMA_ID):
    doc = registry.document({name: {"title": name.title(), "open": True} for name in units})
    doc["schema"] = schema
    return registry.dumps(doc)


def _sdlc(tmp_path, state=True):
    sdlc = tmp_path / "repo" / ".sdlc"
    (sdlc / "features").mkdir(parents=True)
    if state:
        (sdlc / "state").mkdir()
    return sdlc


def _sheet(sdlc):
    return sdlc / "features" / "index.json"


def _copy(sdlc):
    return sdlc / "state" / "backup" / "index-sigma.json"


def _copy_text(sdlc):
    """The copy's bytes, or None when there is none -- so a missing copy is an assertion failure."""
    try:
        return _copy(sdlc).read_text(encoding="utf-8")
    except OSError:
        return None


def _units(sdlc):
    return sorted(registry.read(sdlc / "features"))


def _sigma_writes(sdlc, units):
    """Sigma's own write of the sheet -- `write_index`, the documented writer."""
    registry.write_index(sdlc / "features", {n: {"title": n.title(), "open": True} for n in units})


def _plant_shard(sdlc, name):
    """A unit shard the predecessor wrote for a unit of its own (the document shape is the same
    as the sheet's, under its schema id)."""
    path = sdlc / "features" / "units" / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_text([name], OLD_ID), encoding="utf-8")


def model_fold(sdlc):
    """A MODEL of the predecessor's `feature_sync.py fold <sdlc_dir>`, NOT THE REAL THING.

    Written from the measurement in the module docstring: it reads only documents that declare ITS
    schema id (a Sigma-schema sheet reads as empty, with a stderr note), takes the shards in its own
    schema, and writes the result as the sheet under ITS schema id, exit 0. Nothing more is claimed."""
    features = sdlc / "features"
    found = {}
    try:
        sheet = json.loads(_sheet(sdlc).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sheet = {}
    if sheet.get("schema") == OLD_ID:
        found.update(sheet.get("features", {}))
    for shard in sorted((features / "units").glob("*.json")) if (features / "units").is_dir() else []:
        doc = json.loads(shard.read_text(encoding="utf-8"))
        if doc.get("schema") == OLD_ID:
            found.update(doc.get("features", {}))
    _sheet(sdlc).write_text(json.dumps({"features": found, "schema": OLD_ID}, indent=2,
                                       sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _installed_predecessor_scripts():
    cache = _HOST_CLAUDE / "plugins" / "cache" / legacy.RETIRED / legacy.RETIRED
    skill = "sdlc" + "-loop"              # the previous skill name, from fragments (guarded name)
    try:
        versions = sorted((p for p in cache.iterdir()
                           if (p / "skills" / skill / "scripts" / "feature_sync.py").is_file()),
                          key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.split(".")])
    except OSError:
        return None
    return versions[-1] / "skills" / skill / "scripts" if versions else None


def _tree_hash(path):
    digest = hashlib.sha256()
    for p in sorted(pathlib.Path(path).rglob("*")):
        if p.is_file() and p.suffix != ".pyc" and "__pycache__" not in p.parts:
            digest.update(p.relative_to(path).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return digest.hexdigest()


def _run(argv, env=None, cwd=None):
    return subprocess.run([sys.executable, *map(str, argv)], capture_output=True, text=True,
                          env=env or dict(os.environ), cwd=cwd, timeout=120)


def _scaffolded(tmp_path):
    """An initialised repository (so `.sdlc/state/` and the config exist), with a features dir."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for argv in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", *argv], cwd=str(repo), check=True, capture_output=True)
    done = _run([INIT, repo])
    assert done.returncode == 0, done.stdout + done.stderr
    (repo / ".sdlc" / "features").mkdir(exist_ok=True)
    return repo / ".sdlc"


def _gated_verb(sdlc):
    """A surface that runs the Sigma gate and says what it found: `loop.py start`."""
    return _run([LOOP / "loop.py", "start", sdlc, "--session-pid", os.getpid()])


# --------------------------------------------------------------------------- the planned red tests


def test_model_fold_is_signalled_and_recover_restores_the_unit(tmp_path):
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice"])
    before = _sheet(sdlc).read_text(encoding="utf-8")
    assert _units(sdlc) == ["voice"]

    assert model_fold(sdlc) == 0
    assert json.loads(_sheet(sdlc).read_text())["schema"] == OLD_ID and _units(sdlc) == []

    said = _gated_verb(sdlc)
    assert said.returncode == 0, said.stdout + said.stderr
    assert "feature_sync.py recover" in said.stderr and "voice" in said.stderr, said.stderr

    done = _run([LOOP / "feature_sync.py", "recover", sdlc])
    assert done.returncode == 0, done.stdout + done.stderr
    assert _units(sdlc) == ["voice"]
    assert _sheet(sdlc).read_text(encoding="utf-8") == before        # the exact sheet, back


def test_sheet_delivered_without_write_index_is_snapshotted_by_the_guard(tmp_path):
    """A sheet that arrives by pull, clone or propagation never went through `write_index`: the
    first gated verb that sees it must take the copy, or the fold finds nothing to recover from."""
    sdlc = _scaffolded(tmp_path)
    delivered = _text(["voice", "audio"])
    _sheet(sdlc).write_text(delivered, encoding="utf-8")

    assert _gated_verb(sdlc).returncode == 0
    assert _copy_text(sdlc) == delivered

    model_fold(sdlc)
    assert _units(sdlc) == []
    assert _run([LOOP / "feature_sync.py", "recover", sdlc]).returncode == 0
    assert _units(sdlc) == ["audio", "voice"]


def test_real_predecessor_fold_is_signalled_and_recover_restores_the_unit(tmp_path):
    scripts = _installed_predecessor_scripts()
    if scripts is None:
        pytest.skip("no installed predecessor plugin under the host's plugin cache: this control "
                    "needs the real fold, which CI does not have (the hermetic model test carries "
                    "the red there)")
    installed_before = _tree_hash(scripts)
    run_copy = tmp_path / "predecessor-copy"
    shutil.copytree(scripts, run_copy, ignore=shutil.ignore_patterns("__pycache__"))

    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice"])
    before = _sheet(sdlc).read_text(encoding="utf-8")
    env = dict(os.environ, HOME=str(tmp_path / "home"))
    folded = _run([run_copy / "feature_sync.py", "fold", sdlc], env=env)
    assert folded.returncode == 0, folded.stdout + folded.stderr           # the measured: it exits 0
    assert json.loads(_sheet(sdlc).read_text())["features"] == {}          # and empties the sheet
    assert _tree_hash(scripts) == installed_before                         # the installed copy untouched

    assert "feature_sync.py recover" in _gated_verb(sdlc).stderr
    assert _run([LOOP / "feature_sync.py", "recover", sdlc]).returncode == 0
    assert _units(sdlc) == ["voice"]
    assert _sheet(sdlc).read_text(encoding="utf-8") == before


def test_fold_refuses_while_units_are_recoverable(tmp_path):
    """Sigma's own fold must never write the emptied registry as if it were complete."""
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice"])
    model_fold(sdlc)
    emptied = _sheet(sdlc).read_text(encoding="utf-8")

    refused = _run([LOOP / "feature_sync.py", "fold", sdlc])
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "recover" in refused.stderr
    assert _sheet(sdlc).read_text(encoding="utf-8") == emptied               # nothing written


def test_migrate_holds_the_index_of_a_recoverable_sheet(tmp_path):
    """Converting an emptied sheet's id would turn it into an empty Sigma sheet, and the copy is
    then no longer 'recoverable'. The dry run says it, and `--apply` leaves the bytes alone."""
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice"])
    model_fold(sdlc)
    emptied = _sheet(sdlc).read_text(encoding="utf-8")

    dry = _run([MIGRATE, sdlc])
    assert "recover" in dry.stdout + dry.stderr
    assert _sheet(sdlc).read_text(encoding="utf-8") == emptied
    _run([MIGRATE, sdlc, "--apply"])
    assert _sheet(sdlc).read_text(encoding="utf-8") == emptied
    assert _run([LOOP / "feature_sync.py", "recover", sdlc]).returncode == 0
    assert _units(sdlc) == ["voice"]


def test_fresh_predecessor_unit_does_not_stop_recovery(tmp_path):
    """The realistic coexistence case: the predecessor started a goal in a brand-new unit, so its
    fold writes exactly that one unit -- and the Sigma unit is gone beside it."""
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice"])
    _plant_shard(sdlc, "fresh")
    model_fold(sdlc)
    assert json.loads(_sheet(sdlc).read_text())["features"].keys() == {"fresh"}
    assert "voice" not in _units(sdlc)

    assert _run([LOOP / "feature_sync.py", "recover", sdlc]).returncode == 0
    assert _units(sdlc) == ["fresh", "voice"]
    assert json.loads(_sheet(sdlc).read_text())["schema"] == SIGMA_ID


def test_a_shrunken_sigma_sheet_does_not_replace_the_copy(tmp_path):
    """An older branch or a pull can hand Sigma a Sigma-schema sheet with fewer units, and Sigma's own
    fold can then re-write a truncated or emptied one. Neither observing nor writing such a sheet may
    destroy the only recovery data, and the copy keeps tracking new units: it is the union."""
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["alpha", "beta"])
    kept = _copy_text(sdlc)
    assert kept is not None and "beta" in kept
    _sheet(sdlc).write_text(_text(["alpha"]), encoding="utf-8")

    assert _gated_verb(sdlc).returncode == 0
    assert _copy_text(sdlc) == kept
    _sheet(sdlc).write_text("{ truncated", encoding="utf-8")          # and a corrupt one, then Sigma's fold
    _run([LOOP / "feature_sync.py", "fold", sdlc])
    assert _copy_text(sdlc) == kept

    _sigma_writes(sdlc, ["alpha", "gamma"])           # a smaller sheet, then one with a NEW unit
    assert sorted(registry.mirror_units(sdlc / "features")) == ["alpha", "beta", "gamma"]
    model_fold(sdlc)
    assert _run([LOOP / "feature_sync.py", "recover", sdlc]).returncode == 0
    assert _units(sdlc) == ["alpha", "beta", "gamma"]   # beta lingers (documented); gamma is not lost


# --------------------------------------------------------------------------- controls
# These pass on old code only vacuously or exist only with the fix; each is judged by a mutation
# (see the plan), not counted as the red proof.


def test_control_a_sheet_entry_wins_over_the_copy_on_a_shared_name(tmp_path):
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice", "audio"])
    sheet = json.loads(_text(["voice"], OLD_ID))
    sheet["features"]["voice"]["title"] = "Edited by the predecessor"
    _sheet(sdlc).write_text(json.dumps(sheet), encoding="utf-8")

    assert _run([LOOP / "feature_sync.py", "recover", sdlc]).returncode == 0
    merged = registry.read(sdlc / "features")
    assert sorted(merged) == ["audio", "voice"] and merged["voice"]["title"] == "Edited by the predecessor"


def test_control_nothing_to_recover_is_refused_and_writes_nothing(tmp_path):
    sdlc = _scaffolded(tmp_path)
    _sheet(sdlc).write_text(_text(["voice"], OLD_ID), encoding="utf-8")     # a complete old sheet
    before = _sheet(sdlc).read_text(encoding="utf-8")
    done = _run([LOOP / "feature_sync.py", "recover", sdlc])
    assert done.returncode == 2 and _sheet(sdlc).read_text(encoding="utf-8") == before


@pytest.mark.parametrize("kind", ["wrong-schema", "not-json", "bad-types", "symlink"])
def test_control_a_hostile_copy_is_ignored(tmp_path, kind):
    sdlc = _scaffolded(tmp_path)
    _sheet(sdlc).write_text(_text([], OLD_ID), encoding="utf-8")
    path = _copy(sdlc)
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "wrong-schema":
        path.write_text(_text(["voice"], "someone-else/features@1"), encoding="utf-8")
    elif kind == "not-json":
        path.write_text("{ not json", encoding="utf-8")
    elif kind == "bad-types":
        path.write_text(json.dumps({"schema": SIGMA_ID, "features": {"../escape": 1, "ok": "x"}}),
                        encoding="utf-8")
    else:
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_text(_text(["voice"]), encoding="utf-8")
        path.symlink_to(elsewhere)
    assert registry.recoverable(sdlc / "features") == []
    assert _run([LOOP / "feature_sync.py", "recover", sdlc]).returncode == 2


def test_control_neither_a_guard_nor_a_write_creates_a_state_directory(tmp_path):
    sdlc = _sdlc(tmp_path, state=False)          # features/ only: no state/ at all
    _sheet(sdlc).write_text(_text(["voice"]), encoding="utf-8")
    registry.guard_sheet(sdlc)
    _sigma_writes(sdlc, ["voice", "audio"])      # neither a guard nor a Sigma write initialises it
    assert not (sdlc / "state").exists()


def test_control_the_copy_is_one_file_however_many_writes(tmp_path):
    sdlc = _sdlc(tmp_path)
    for units in (["a"], ["a", "b"], ["a", "b", "c"]):
        _sigma_writes(sdlc, units)
    assert [p.name for p in _copy(sdlc).parent.iterdir() if p.name.startswith("index-sigma")] \
        == ["index-sigma.json"]
    assert _copy(sdlc).read_text(encoding="utf-8") == _sheet(sdlc).read_text(encoding="utf-8")


def test_control_an_unwritable_copy_location_never_fails_the_sheet_write(tmp_path):
    sdlc = _sdlc(tmp_path, state=False)
    (sdlc / "state").write_text("a file where the directory should be", encoding="utf-8")
    _sigma_writes(sdlc, ["voice"])
    assert _units(sdlc) == ["voice"]


def test_control_a_stale_refresh_cannot_overwrite_a_newer_copy(tmp_path):
    """The compare-and-write seam, deterministic: a caller holding the OLD text (same units, an older
    title) after the sheet has moved on leaves the newer copy alone."""
    sdlc = _sdlc(tmp_path)
    old_text = _text(["alpha", "beta"])
    new_text = old_text.replace("Alpha", "Alpha, renamed")
    assert new_text != old_text
    _sheet(sdlc).write_text(old_text, encoding="utf-8")
    registry._refresh_mirror(sdlc / "features", old_text)
    _sheet(sdlc).write_text(new_text, encoding="utf-8")
    registry._refresh_mirror(sdlc / "features", new_text)
    assert _copy(sdlc).read_text(encoding="utf-8") == new_text
    registry._refresh_mirror(sdlc / "features", old_text)          # the stale caller, sheet is new_text
    assert _copy(sdlc).read_text(encoding="utf-8") == new_text


def test_control_discard_sets_the_copy_aside_and_unblocks_fold(tmp_path):
    """The way out when the sheet is genuinely complete (an older branch, a deliberate drop):
    `recover --discard` renames the copy, never deletes it, and the fold proceeds."""
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice"])
    _sheet(sdlc).write_text(_text(["other"], OLD_ID), encoding="utf-8")
    assert _run([LOOP / "feature_sync.py", "fold", sdlc]).returncode == 2
    kept = _copy(sdlc).read_text(encoding="utf-8")

    done = _run([LOOP / "feature_sync.py", "recover", sdlc, "--discard"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert not _copy(sdlc).exists()
    assert [p.read_text(encoding="utf-8") for p in _copy(sdlc).parent.glob("index-sigma.discarded*")] == [kept]
    assert _run([LOOP / "feature_sync.py", "fold", sdlc]).returncode == 0


def test_control_recover_refuses_when_the_sheet_changes_while_it_is_read(tmp_path, monkeypatch):
    """The other compare-and-write seam, deterministic: a writer that replaces the sheet after
    `recover` read it (here, from inside its own `read` call) makes it write nothing."""
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice"])
    model_fold(sdlc)
    real_read = fsync.registry.read
    moved = _text(["someone-else"], OLD_ID)

    def read_then_move(features_dir):
        got = real_read(features_dir)
        _sheet(sdlc).write_text(moved, encoding="utf-8")
        return got

    monkeypatch.setattr(fsync.registry, "read", read_then_move)
    with pytest.raises(fsync.RecoverRefused):
        fsync.recover(sdlc)
    assert _sheet(sdlc).read_text(encoding="utf-8") == moved


def test_control_a_symlinked_state_directory_is_not_followed(tmp_path):
    """A link at `state/backup` (or `state`) must not steer the copy to another directory."""
    sdlc = _sdlc(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (sdlc / "state" / "backup").symlink_to(elsewhere, target_is_directory=True)
    _sigma_writes(sdlc, ["voice"])
    assert list(elsewhere.iterdir()) == [] and _units(sdlc) == ["voice"]


def test_control_the_lever_is_spelled_absolute_and_shell_quoted(tmp_path):
    sdlc = _sdlc(tmp_path / "with space")
    hint = registry.recover_hint(os.path.relpath(sdlc))
    assert "'" in hint and str(sdlc.resolve()) in hint and hint.endswith(" recover " + __import__("shlex").quote(str(sdlc.resolve())))


def test_control_two_discards_in_one_second_keep_both_copies(tmp_path):
    sdlc = _scaffolded(tmp_path)
    for units in (["a"], ["a", "b"]):
        _sigma_writes(sdlc, units)
        assert _run([LOOP / "feature_sync.py", "recover", sdlc, "--discard"]).returncode == 0
    kept = sorted(p.read_text(encoding="utf-8") for p in _copy(sdlc).parent.glob("index-sigma.discarded*"))
    assert len(kept) == 2 and kept[0] != kept[1]


def test_control_recover_prints_a_bounded_list_of_unit_names(tmp_path):
    sdlc = _scaffolded(tmp_path)
    names = ["unit-%02d" % i for i in range(30)]
    _sigma_writes(sdlc, names)
    model_fold(sdlc)
    done = _run([LOOP / "feature_sync.py", "recover", sdlc])
    assert done.returncode == 0 and "restored 30 unit(s)" in done.stdout and "and 22 more" in done.stdout
    assert "unit-29" not in done.stdout


def test_control_a_write_that_changes_nothing_still_takes_the_copy(tmp_path):
    """`write_index` writes nothing when the sheet's bytes already match; the copy must still be
    taken then, or a delivered sheet that a fold re-writes identically never gets one."""
    sdlc = _sdlc(tmp_path)
    delivered = _text(["voice"])
    _sheet(sdlc).write_text(delivered, encoding="utf-8")
    _sigma_writes(sdlc, ["voice"])
    assert _sheet(sdlc).read_text(encoding="utf-8") == delivered
    assert _copy(sdlc).read_text(encoding="utf-8") == delivered


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs a directory permission bits can actually make unwritable")
def test_control_recover_on_an_unwritable_features_directory_refuses_cleanly(tmp_path):
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice"])
    model_fold(sdlc)
    emptied = _sheet(sdlc).read_text(encoding="utf-8")
    (sdlc / "features").chmod(0o500)
    try:
        done = _run([LOOP / "feature_sync.py", "recover", sdlc])
    finally:
        (sdlc / "features").chmod(0o700)
    assert done.returncode == 2 and "Traceback" not in done.stderr and "nothing was changed" in done.stderr
    assert _sheet(sdlc).read_text(encoding="utf-8") == emptied


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs a directory permission bits can actually make unwritable")
def test_control_discard_on_an_unwritable_backup_directory_refuses_cleanly(tmp_path):
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice"])
    (sdlc / "state" / "backup").chmod(0o500)
    try:
        done = _run([LOOP / "feature_sync.py", "recover", sdlc, "--discard"])
    finally:
        (sdlc / "state" / "backup").chmod(0o700)
    assert done.returncode == 2 and "Traceback" not in done.stderr and "nothing was changed" in done.stderr
    assert _copy(sdlc).is_file()


def test_control_the_migrate_refusal_spells_the_same_pasteable_lever(tmp_path):
    sdlc = _scaffolded(tmp_path)
    _sigma_writes(sdlc, ["voice"])
    model_fold(sdlc)
    said = _run([MIGRATE, sdlc]).stdout
    assert registry.recover_hint(sdlc) in said

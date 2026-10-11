"""#954: the provenance chain behind the dirty-root exemption -- its order rules under concurrency
(AC-4), its bounds, its failure modes, and the one record file every path spelling must name.

Every registry shard/page write records, in gitignored `.sdlc/state/features/provenance/`, an
ordered chain of git blob ids: each consecutive pair is one Sigma write (pre-image, post-image). The
writer updates the chain under a per-file lock BEFORE its `os.replace`; the checker reads the file's
bytes BEFORE the chain. Tests 14 and 15 pin those two orders deterministically, in process, at the
exact seam (AGENTS.md: a probabilistic control is not a system of record); test 16 is the labelled
multi-process smoke run.

Every body runs inside `registry_root.drained(capfd)` (plan §7: one captured-output section voids
every red recorded in that verify run).
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

import registry_root
from registry_root import PAGE, REPO, SHARD, World

try:
    import fcntl
except ImportError:                       # Windows: no flock, so no serialised record
    fcntl = None

#: A writer process: `count` Sigma shard writes through `feature_sync.amend`, each adding one goal.
WRITER = r"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("feature_sync", sys.argv[1])
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
sdlc, unit, first, count = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
for goal in range(first, first + count):
    def mutate(entry, goal=goal):
        repos = entry.setdefault("repos", {})
        mine = repos.setdefault("acme/app", {"branch": "feature/" + unit, "owner": None,
                                             "authorized": False, "goals": []})
        mine["goals"] = list(mine.get("goals") or []) + [goal]
    report = sync.amend(sdlc, unit, mutate)
    if not report["written"]:
        sys.exit("write %d did not land: %r" % (goal, report))
"""


def _goals(w, rel=SHARD, unit="voice"):
    return json.loads(w.read(rel))["features"][unit]["repos"][REPO]["goals"]


def _dirty(w):
    """A pick, the §15 commit, a second pick: both registry files carry Sigma's own dirt."""
    assert w.start("100").startswith("worktree")
    w.commit_registry()
    assert w.start("101").startswith("worktree")
    assert w.porcelain().splitlines() == [" M " + SHARD, " M " + PAGE], w.porcelain()


def test_slot_a_never_refuses_across_slot_b_record_then_replace(tmp_path, capfd, monkeypatch):
    """AC-4, writer order: slot B's record is updated BEFORE its replace. Slot A's check is run at
    the seam itself -- once with B's record updated and the old bytes still on disk, once right
    after the replace -- and refuses neither time."""
    with registry_root.drained(capfd):
        w = World(tmp_path).build()
        _dirty(w)
        target = os.path.realpath(w.root / SHARD)
        real_replace = os.replace
        seen = []

        def replace(src, dst, *args, **kwargs):
            if os.path.realpath(os.fspath(dst)) != target:
                return real_replace(src, dst, *args, **kwargs)
            seen.append(("before", w.check()))
            result = real_replace(src, dst, *args, **kwargs)
            seen.append(("after", w.check()))
            return result

        with monkeypatch.context() as m:
            m.setattr(os, "replace", replace)
            w.sigma_shard_write("102")
        assert [when for when, _ in seen] == ["before", "after"], seen
        assert 102 in _goals(w)
        assert [out for _, out in seen] == [None, None], seen


def test_slot_a_reads_the_bytes_before_the_record(tmp_path, capfd, monkeypatch):
    """AC-4, reader order: slot A reads a registry file's bytes BEFORE its chain. B's whole write is
    injected right after A's chain read; A's decision then rests on bytes that chain already holds.
    The hook must fire, or the test proves nothing."""
    with registry_root.drained(capfd):
        w = World(tmp_path).build()
        _dirty(w)
        real_read_text = pathlib.Path.read_text
        fired = []

        def read_text(self, *args, **kwargs):
            text = real_read_text(self, *args, **kwargs)
            if not fired and str(self).endswith("provenance/units/voice.json.chain.json"):
                fired.append(str(self))
                w.sigma_shard_write("102")
            return text

        with monkeypatch.context() as m:
            m.setattr(pathlib.Path, "read_text", read_text)
            out = w.check()
        assert fired, "the checker never read the shard's provenance record"
        assert 102 in _goals(w)
        assert out is None, out


def test_concurrent_registry_writers_never_make_a_root_check_refuse_smoke(tmp_path, capfd):
    """SMOKE, NOT THE SYSTEM OF RECORD. Probabilistic by nature: 3 writer processes (2 on `voice`,
    1 on `billing`) x 25 writes against a checker loop in this process. Tests 14 and 15 are the
    deterministic controls at the seam; this only shows nothing else in a real interleaving refuses.
    Needs flock: without it the record's read-modify-write is unserialised by design (plan §4)."""
    with registry_root.drained(capfd):
        if fcntl is None:
            pytest.skip("no fcntl: provenance records are unserialised here by design (plan §4)")
        w = World(tmp_path).build()
        assert w.start("100").startswith("worktree")
        w.sigma_shard_write("200", unit="billing")
        w.commit_registry()
        assert w.porcelain() == ""
        script = str(registry_root.SCRIPTS / "feature_sync.py")
        writers = [subprocess.Popen([sys.executable, "-c", WRITER, script, str(w.sdlc), unit,
                                     str(first), "25"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                   for unit, first in (("voice", 1000), ("voice", 2000), ("billing", 3000))]
        results = []
        deadline = time.monotonic() + 120
        try:
            while any(p.poll() is None for p in writers) and time.monotonic() < deadline:
                results.append(w.check())
            results.append(w.check())
        finally:
            outputs = []
            for p in writers:
                try:
                    outputs.append(p.communicate(timeout=60))
                except subprocess.TimeoutExpired:
                    p.kill()
                    outputs.append(p.communicate())
        codes = [p.returncode for p in writers]
        assert codes == [0, 0, 0], (codes, outputs)
        assert len(_goals(w)) == 51 and len(_goals(w, ".sdlc/features/units/billing.json", "billing")) == 26
        assert len(results) >= 2, results
        refused = [r for r in results if r is not None]
        assert refused == [], refused[:1]


def test_head_at_the_anchor_survives_more_writes_than_the_cap(tmp_path, capfd):
    """Scale: §14's normal flow commits the first write once and never again, so HEAD is the
    chain's first entry. 300 Sigma writes -- more than the cap of 256 -- must keep that anchor."""
    with registry_root.drained(capfd):
        w = World(tmp_path).build()
        assert w.start("100").startswith("worktree")
        w.commit_registry()
        for goal in range(1000, 1300):
            w.sigma_shard_write(goal)
        assert len(_goals(w)) == 301
        out = w.check()
        assert out is None, out


def test_a_mid_chain_commit_survives_the_cap_once_a_check_has_seen_it(tmp_path, capfd):
    """Scale: a human commits mid-chain. The first check that proves the file compacts the chain to
    start at that HEAD, so 300 later writes cannot evict it. Compaction takes the record's lock, so
    it only runs where flock does."""
    with registry_root.drained(capfd):
        if fcntl is None:
            pytest.skip("no fcntl: compaction needs the record's lock (plan §2b)")
        w = World(tmp_path).build()
        assert w.start("100").startswith("worktree")
        for goal in range(1000, 1010):
            w.sigma_shard_write(goal)
        w.commit_registry()
        w.sigma_shard_write(1010)
        first = w.check()
        assert first is None, first
        for goal in range(1011, 1311):
            w.sigma_shard_write(goal)
        assert len(_goals(w)) == 312
        out = w.check()
        assert out is None, out


def test_a_missing_or_corrupt_record_refuses_until_a_sigma_write_reseeds(tmp_path, capfd):
    """Resiliency: a lost record (deleted `state/`, a fresh clone, an upgrade with dirt already
    present) trusts nothing, so the start refuses -- once. After the §15 commit, the next Sigma
    write reseeds the chain from the committed bytes and starts are exempt again."""
    with registry_root.drained(capfd):
        w = World(tmp_path).build()
        _dirty(w)
        corrupt = w.record("units/voice.json")
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_text("{not json", encoding="utf-8")
        w.record("voice.md").unlink(missing_ok=True)
        refused = w.check()
        assert refused is not None and refused.startswith("REFUSED"), refused
        w.commit_registry()
        w.sigma_shard_write("102")
        w.sigma_page_write()
        assert w.porcelain().splitlines() == [" M " + SHARD, " M " + PAGE], w.porcelain()
        out = w.check()
        assert out is None, out


def test_a_crash_between_record_and_replace_keeps_the_old_bytes_proven(tmp_path, capfd, monkeypatch):
    """Resiliency: the record holds a post-image that never landed. The disk still holds the
    pre-image, which is still proven, and the next write truncates back to it and goes on."""
    with registry_root.drained(capfd):
        w = World(tmp_path).build()
        _dirty(w)
        target = os.path.realpath(w.root / SHARD)
        real_replace = os.replace

        def replace(src, dst, *args, **kwargs):
            if os.path.realpath(os.fspath(dst)) == target:
                raise OSError("simulated crash between the record and the replace")
            return real_replace(src, dst, *args, **kwargs)

        crashed = False
        with monkeypatch.context() as m:
            m.setattr(os, "replace", replace)
            try:
                w.sigma_shard_write("102")
            except OSError:
                crashed = True
        assert crashed, "the simulated crash never reached the shard's replace"
        assert _goals(w) == [100, 101]
        out = w.check()
        assert out is None, out
        w.sigma_shard_write("103")
        assert _goals(w) == [100, 101, 103]
        out = w.check()
        assert out is None, out


def test_a_record_that_cannot_be_written_never_fails_the_registry_write(tmp_path, capfd):
    """Safety: fail open for the WRITE, fail closed for TRUST. With a file where the record
    directory should be, the shard write still lands, and exactly one stderr line names the record
    that could not be kept."""
    with registry_root.drained(capfd) as out:
        w = World(tmp_path).build()
        assert w.start("100").startswith("worktree")
        w.commit_registry()
        blocker = w.root / registry_root.RECORD_DIR
        if blocker.is_dir():
            shutil.rmtree(blocker)
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("a file where the record directory belongs\n", encoding="utf-8")
        out.take()
        try:
            w.sigma_shard_write("101")
        except Exception as exc:          # noqa: BLE001 - the whole point: it must not raise
            assert False, "the registry write failed because its record could not be: %r" % (exc,)
        _, err = out.take()
        assert _goals(w) == [100, 101]
        named = [line for line in err.splitlines() if "voice.json.chain.json" in line]
        assert len(named) == 1, err


def test_nothing_is_recorded_without_a_state_dir_or_through_a_symlink(tmp_path, capfd):
    """Safety (#708 posture): a write never initialises `state/`, and a link anywhere on the record
    path cannot steer the record elsewhere."""
    with registry_root.drained(capfd):
        registry = registry_root.module("feature_registry")
        entry = {"open": True, "repos": {REPO: {"branch": "feature/voice", "goals": [1]}}}

        def write(features_dir):
            try:
                registry.write_unit(features_dir, "voice", entry)
            except Exception as exc:      # noqa: BLE001 - a pin: success is the expectation
                assert False, "write_unit raised: %r" % (exc,)
            assert (features_dir / "units" / "voice.json").is_file()

        bare = tmp_path / "bare" / ".sdlc"
        (bare / "features" / "units").mkdir(parents=True)
        write(bare / "features")
        assert not (bare / "state" / "features").exists()

        links = [("linked-state", "state"), ("linked-provenance", "state/features/provenance")]
        for name, link in links:
            sdlc = tmp_path / name / ".sdlc"
            (sdlc / "features" / "units").mkdir(parents=True)
            (sdlc / link).parent.mkdir(parents=True, exist_ok=True)
            elsewhere = tmp_path / (name + "-target")
            elsewhere.mkdir()
            try:
                os.symlink(elsewhere, sdlc / link, target_is_directory=True)
            except (OSError, NotImplementedError):
                continue                  # no symlinks on this host: nothing to steer
            write(sdlc / "features")
            assert list(elsewhere.rglob("*.chain.json")) == [], name
            assert not (elsewhere / "features" / "provenance").exists(), name


def test_the_record_is_the_same_file_whatever_spelling_each_side_used(tmp_path, capfd, monkeypatch):
    """D7: the CLI hands every writer the RELATIVE `.sdlc` (SKILL.md step 3a); `loop.py` hands an
    absolute one; a root may be reached through a link. Writer and checker must name ONE record file
    whatever spelling each was given, or the exemption silently never matches."""
    with registry_root.drained(capfd):
        prov = registry_root.module("feature_provenance")
        assert prov is not None, "feature_provenance.py is missing"
        w = World(tmp_path).layout()
        registry = registry_root.module("feature_registry")
        monkeypatch.chdir(w.root)
        relative = ".sdlc/features"
        entry = {"open": True, "repos": {REPO: {"branch": "feature/voice", "goals": [1]}}}
        registry.write_unit(relative, "voice", entry)
        committed = w.read(SHARD)
        spellings = [(relative, relative + "/units/voice.json"),
                     (str(w.features), str(w.root / SHARD)),
                     (relative, str(w.root / SHARD))]
        alias = tmp_path / "alias"
        try:
            os.symlink(w.root, alias, target_is_directory=True)
        except (OSError, NotImplementedError):
            alias = None                  # no symlinks on this host: the alias part does not run
        if alias is not None:
            spellings.append((str(alias / ".sdlc" / "features"), str(alias / SHARD)))
        records = {str(prov.record_path(f, t)) if prov.record_path(f, t) else None
                   for f, t in spellings}
        assert len(records) == 1 and None not in records, records
        second = {"open": True, "repos": {REPO: {"branch": "feature/voice", "goals": [1, 2]}}}
        registry.write_unit(str(alias / ".sdlc" / "features") if alias else relative, "voice", second)
        assert w.read(SHARD) != committed
        head = registry_root.blob_id(committed)
        unproven = [(f, t) for f, t in spellings if not prov.proven(f, t, head)]
        assert unproven == [], unproven

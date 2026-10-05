"""#459 -- B6: review generation, manifest, post and result evidence under `.sdlc/state/review-*`.

`docs/launch/dispositions/459.json` claims the generation-keyed store is capped by
`work.prune_terminal_review_generations` and that `review-queue.md` is unbounded by decision.  A claim
in a JSON file proves nothing, so every node drives the REAL writers (`review_context.publish_generation`,
`work.review_evidence`, `work._write_review_result`, `work._review_post_request`) and the REAL terminal
chokepoint (`loop._record`), then checks what survives.  The audit node runs the gesture copied out of
`docs/launch/growth-audit.md` in a scratch copy of the repository (the gesture overwrites the snapshot).
"""
import importlib.util
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = ROOT / "skills" / "sigma-loop" / "scripts"
FILE = ROOT / "docs" / "launch" / "dispositions" / "459.json"
DOC = ROOT / "docs" / "launch" / "growth-audit.md"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


work = _mod("work")
review_context = _mod("review_context")
actionlog = _mod("actionlog")
loop = _mod("loop")

CAPPED = [".sdlc/state/review-generations/<generation>",
          ".sdlc/state/review-generations/<generation>/brief.md",
          ".sdlc/state/review-generations/<generation_id>",
          ".sdlc/state/review-generations/<generation_id>/",
          ".sdlc/state/review-generations/<generation_id>/evidence.json",
          ".sdlc/state/review-manifests/$goal.json",
          ".sdlc/state/review-posts",
          ".sdlc/state/review-posts/<evidence_id>.json",
          ".sdlc/state/review-results",
          ".sdlc/state/review-results/<generation_id>.json"]
UNBOUNDED = [".sdlc/state/review-queue.md"]
ISSUE_PATTERNS = set(CAPPED + UNBOUNDED)


def _entries():
    """{} while the file is absent, so every node below fails by assertion rather than by error."""
    if not FILE.exists():
        return {}
    return {e["pattern"]: e for e in json.loads(FILE.read_text(encoding="utf-8"))}


def _sdlc(tmp_path):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"action_log": {"enabled": True}}))
    return d


def _review(d, goal, revisions=1, post=True):
    """The documented chain through the real writers: brief -> manifest -> resolution -> result -> evidence -> post.
    Returns (generation ids oldest first, the current manifest path)."""
    root = d / "state"
    manifest_path = root / "review-manifests" / f"{goal}.json"
    gens = []
    for n in range(revisions):
        manifest = review_context.publish_generation(d, goal, f"brief {goal} {n}\n", "plan.md", manifest_path)
        gen = manifest["generation_id"]
        gens.append(gen)
        resolution = root / "review-generations" / gen / "resolution.json"
        resolution.write_text('{"mechanism":"subagent"}\n')       # the documented `reviewer.py resolve >` redirect
        result = work._write_review_result(d, manifest, resolution, "approve", "ok")
        evidence = work.review_evidence(d, goal, manifest_path, result["path"])
        if post:
            work._review_post_request(d, goal, 7, "approve", f"body {goal} {n}", evidence["path"])
    return gens, manifest_path


def _log(d, goal, *kinds):
    for kind in kinds:
        if kind == "recorded-done":
            actionlog.append(d, goal, "recorded", "loop", result="done")
        elif kind == "recorded-parked":
            actionlog.append(d, goal, "recorded", "loop", result="parked")
        else:
            actionlog.append(d, goal, kind, "loop")


def _present(d, gens):
    return [g for g in gens if (d / "state" / "review-generations" / g).exists()]


def _count(d, sub):
    p = d / "state" / sub
    return len(list(p.glob("*"))) if p.exists() else 0


def test_covers():
    entries = _entries()
    assert set(entries) == ISSUE_PATTERNS
    assert not any("unscanned" in e for e in entries.values())


def test_named():
    """The pruner a capped entry names must still exist, or the entry is a lie."""
    entries = _entries()
    for pattern in CAPPED:
        assert entries.get(pattern, {}).get("decision", "").startswith("capped"), pattern
        name = re.match(r"work\.(\w+):", entries[pattern]["pruner_or_cap"])
        assert name, entries[pattern]["pruner_or_cap"]
        assert callable(getattr(work, name.group(1), None)), pattern
    for pattern in UNBOUNDED:
        assert entries.get(pattern, {}).get("decision", "").startswith("intentionally unbounded"), pattern


def test_terminal_goal_is_pruned(tmp_path):
    d = _sdlc(tmp_path)
    gens, manifest = _review(d, "9001", revisions=3)
    _log(d, "9001", "claimed", "recorded-done")
    assert len(_present(d, gens)) == 3 and _count(d, "review-posts") == 3
    removed = work.prune_terminal_review_generations(d, "9001")
    assert sorted(removed) == sorted(gens)
    assert _present(d, gens) == []
    assert not manifest.exists()
    assert _count(d, "review-results") == 0 and _count(d, "review-posts") == 0
    assert work.prune_terminal_review_generations(d, "9001") == []        # idempotent


def test_live_data_survives(tmp_path):
    """Every kind of owner that is NOT a finished goal keeps everything."""
    d = _sdlc(tmp_path)
    kept = {}
    kept["parked"], _ = _review(d, "9101", revisions=2); _log(d, "9101", "claimed", "recorded-parked")
    kept["running"], _ = _review(d, "9102"); _log(d, "9102", "claimed")
    kept["reclaimed"], _ = _review(d, "9103"); _log(d, "9103", "recorded-done", "claimed")
    kept["unlogged"], _ = _review(d, "9104")                             # no log at all: unknown is not terminal
    kept["record"], _ = _review(d, "9105"); _log(d, "9105", "claimed", "recorded-done")
    (d / "state" / "work").mkdir()
    (d / "state" / "work" / "9105.json").write_text('{"pr": 7, "worktree": "x"}')   # work record = gate can still bind
    for goal in ("9101", "9102", "9103", "9104", "9105"):
        assert work.prune_terminal_review_generations(d, goal) == []
    assert work.prune_terminal_review_generations(d) == []                # the backlog pass spares them too
    for name, gens in kept.items():
        assert _present(d, gens) == gens, name
    assert _count(d, "review-posts") == 6 and _count(d, "review-results") == 6 and _count(d, "review-manifests") == 5


def test_successor_owner_and_foreign_files_survive(tmp_path):
    d = _sdlc(tmp_path)
    old, _ = _review(d, "9201"); _log(d, "9201", "claimed", "recorded-done")
    # A successor goal's manifest names the finished goal's generation: it is the successor's now.
    succ = d / "state" / "review-manifests" / "9202.json"
    pointer = json.loads((d / "state" / "review-manifests" / "9201.json").read_text())
    pointer["goal"] = "9202"
    succ.write_text(json.dumps(pointer))
    gens, _m = _review(d, "9203"); _log(d, "9203", "claimed", "recorded-done")
    root = d / "state"
    (root / "review-generations" / "not a generation!").mkdir()
    (root / "review-generations" / "stray.txt").write_text("x")
    (root / "review-manifests" / "9203.txt").write_text("x")
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "keep").write_text("x")
    (root / "review-generations" / "linked").symlink_to(outside, target_is_directory=True)
    unowned = root / "review-generations" / ("a" * 32)                  # evidence-less and unreferenced: no owner to attribute it to
    unowned.mkdir(); (unowned / "brief.md").write_text("orphan\n")
    work.prune_terminal_review_generations(d)
    assert _present(d, old) == old and succ.exists()
    assert _present(d, gens) == []
    assert (outside / "keep").exists() and (root / "review-generations" / "linked").is_symlink()
    assert unowned.exists() and (root / "review-generations" / "stray.txt").exists()
    assert (root / "review-generations" / "not a generation!").is_dir()
    assert (root / "review-manifests" / "9203.txt").exists()


def test_backlog_is_bounded_and_drains(tmp_path):
    d = _sdlc(tmp_path)
    goals = [str(9300 + i) for i in range(12)]
    gens = {}
    for goal in goals:
        gens[goal], _ = _review(d, goal); _log(d, goal, "claimed", "recorded-done")
    first = work.prune_terminal_review_generations(d, limit=10)
    assert len(first) == 10
    second = work.prune_terminal_review_generations(d, limit=10)
    assert len(second) == 2
    assert sorted(first + second) == sorted(g for v in gens.values() for g in v)
    assert work.prune_terminal_review_generations(d, limit=10) == []


def test_partial_prune_heals(tmp_path, monkeypatch):
    """A prune that dies between steps leaves a state the next run completes, with no human."""
    d = _sdlc(tmp_path)
    gens, manifest = _review(d, "9401", revisions=2); _log(d, "9401", "claimed", "recorded-done")
    real = shutil.rmtree

    def flaky(path, *a, **k):
        if str(path).endswith(gens[-1]):      # the generation the manifest names
            raise OSError("disk went away")
        return real(path, *a, **k)

    monkeypatch.setattr(work.shutil, "rmtree", flaky)
    first = work.prune_terminal_review_generations(d, "9401")
    assert first == [gens[0]] and _present(d, gens) == [gens[1]]
    assert manifest.exists()                  # the pointer outlives the generation it names, so the goal stays attributable
    monkeypatch.setattr(work.shutil, "rmtree", real)
    assert work.prune_terminal_review_generations(d, "9401") == [gens[1]]
    assert _present(d, gens) == [] and not manifest.exists()
    assert _count(d, "review-posts") == 0 and _count(d, "review-results") == 0


def test_manifest_left_behind_is_collected(tmp_path):
    """Crash after the generations went but before the pointer did."""
    d = _sdlc(tmp_path)
    gens, manifest = _review(d, "9501"); _log(d, "9501", "claimed", "recorded-done")
    shutil.rmtree(d / "state" / "review-generations" / gens[0])
    work.prune_terminal_review_generations(d)
    assert not manifest.exists() and _count(d, "review-results") == 0 and _count(d, "review-posts") == 0


def test_record_done_wires_the_pruner(tmp_path):
    """The documented `loop.py record <goal> done` gesture is what reaches the pruner."""
    base = tmp_path / ".sdlc"
    (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    (base / "config.json").write_text(json.dumps({"budget": {"max_iterations": 5},
                                                  "action_log": {"enabled": False}}))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "goals" / "0001.md").write_text("---\nid: 0001\nstatus: pending\n---\nx\n")
    run = lambda *a: subprocess.run([sys.executable, str(S / "loop.py"), *a], capture_output=True, text=True)
    run("start", str(base))
    goal = run("next", str(base)).stdout.strip()
    gens, manifest = _review(base, "0001")              # the goal's own review evidence (action log off: the hint, not the log, says done)
    other, _ = _review(base, "0002")                    # a different goal that never finished
    result = run("record", str(base), goal, "done")
    assert result.returncode == 0, result.stderr
    assert _present(base, gens) == [] and not manifest.exists()
    assert _present(base, other) == other


def test_cli_lever(tmp_path):
    d = _sdlc(tmp_path)
    gens, _ = _review(d, "9601")
    cli = lambda *a: subprocess.run([sys.executable, str(S / "work.py"), "prune-review-generations", str(d), *a],
                                    capture_output=True, text=True)
    assert json.loads(cli("9601").stdout) == []                          # not provably done: spared
    assert _present(d, gens) == gens
    done = cli("9601", "--done")
    assert done.returncode == 0 and json.loads(done.stdout) == gens
    assert _present(d, gens) == []


def test_reclaimed_goal_keeps_its_new_manifest(tmp_path, monkeypatch):
    """The goal is re-claimed and republishes between the index and the manifest unlink: the pointer is
    the successor's now, so it must be re-read and spared (plan-review finding 1)."""
    d = _sdlc(tmp_path)
    gens, manifest = _review(d, "9701"); _log(d, "9701", "claimed", "recorded-done")
    real = shutil.rmtree
    fresh = {}

    def republish(path, *a, **k):
        real(path, *a, **k)
        fresh.update(review_context.publish_generation(d, "9701", "new brief\n", "plan.md", manifest))

    monkeypatch.setattr(work.shutil, "rmtree", republish)
    assert work.prune_terminal_review_generations(d, "9701") == gens
    assert json.loads(manifest.read_text())["generation_id"] == fresh["generation_id"]
    assert (d / "state" / "review-generations" / fresh["generation_id"]).is_dir()


def test_goal_identity_is_normalised_and_unsafe_is_skipped(tmp_path):
    d = _sdlc(tmp_path)
    gens, manifest = _review(d, "goals/9801.md")          # a goal-file path, as local mode publishes it
    data = json.loads(manifest.read_text()); assert data["goal"] == "goals/9801.md"
    _log(d, "9801", "claimed", "recorded-done")
    bad, bad_manifest = _review(d, "9802")
    doc = json.loads(bad_manifest.read_text()); doc["goal"] = "../../etc"; bad_manifest.write_text(json.dumps(doc))
    assert work.prune_terminal_review_generations(d) == gens
    assert bad_manifest.exists() and _present(d, bad) == bad


def test_corrupt_work_record_is_not_terminal(tmp_path):
    d = _sdlc(tmp_path)
    gens, _m = _review(d, "9901"); _log(d, "9901", "claimed", "recorded-done")
    (d / "state" / "work").mkdir()
    (d / "state" / "work" / "9901.json").write_text("{not json")
    assert work.prune_terminal_review_generations(d, "9901", goal_done=True) == []
    assert _present(d, gens) == gens


def test_linked_store_is_not_followed(tmp_path):
    d = _sdlc(tmp_path)
    outside = tmp_path / "outside"
    (outside / ("a" * 32)).mkdir(parents=True)
    (outside / ("a" * 32) / "evidence.json").write_text('{"goal": "9951"}')
    (d / "state" / "review-generations").symlink_to(outside, target_is_directory=True)
    _log(d, "9951", "claimed", "recorded-done")
    assert work.prune_terminal_review_generations(d, "9951") == []
    assert (outside / ("a" * 32) / "evidence.json").exists()


def test_stuck_goals_do_not_starve_the_rest(tmp_path):
    """Goals that can never be pruned (two owners) must not spend the per-call budget."""
    d = _sdlc(tmp_path)
    for i in range(12):
        goal = str(100 + i)
        _g, m = _review(d, goal); _log(d, goal, "claimed", "recorded-done")
        ev = d / "state" / "review-generations" / json.loads(m.read_text())["generation_id"] / "evidence.json"
        data = json.loads(ev.read_text()); data["goal"] = "777"; ev.write_text(json.dumps(data))   # conflicting owner
    clean, _m = _review(d, "999"); _log(d, "999", "claimed", "recorded-done")
    got = []
    for _ in range(6):                       # each stuck goal spends the budget once (its post goes), then never again
        got += work.prune_terminal_review_generations(d, limit=3)
    assert got == clean


def test_cli_rejects_bad_arguments(tmp_path):
    d = _sdlc(tmp_path)
    for args in (["10", "--limit", "abc"], ["--done"], ["1", "2"], ["1", "--limit", "0"]):
        r = subprocess.run([sys.executable, str(S / "work.py"), "prune-review-generations", str(d), *args],
                           capture_output=True, text=True)
        assert r.returncode == 2 and "usage" in r.stderr and "Traceback" not in r.stderr, args
    gens, _ = _review(d, "10"); _log(d, "10", "claimed", "recorded-done")
    r = subprocess.run([sys.executable, str(S / "work.py"), "prune-review-generations", str(d), "10", "--limit", "10"],
                       capture_output=True, text=True)
    assert json.loads(r.stdout) == gens


def _gesture():
    block = re.search(r"```sh\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S).group(1)
    line = next(l for l in block.splitlines() if "tools/readiness/growth_audit.py" in l)
    argv = shlex.split(line)
    assert argv[:2] == ["python3", "tools/readiness/growth_audit.py"]
    return [sys.executable] + argv[1:]


def test_audit(tmp_path):
    for name in ("skills", "hooks", "tools", "contract"):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "docs" / "launch", tmp_path / "docs" / "launch")
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-C", str(tmp_path)]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
        subprocess.run(git + args, check=True, capture_output=True)
    done = subprocess.run(_gesture(), cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    result = json.loads((tmp_path / "docs" / "launch" / "growth-audit.json").read_text())
    assert not set(result["b6_disposition"]["unresolved_patterns"]) & ISSUE_PATTERNS
    rows = {r["pattern"]: r for r in result["store_measurements"]}
    for pattern in ISSUE_PATTERNS:
        assert rows[pattern]["pruner_or_cap"] == _entries()[pattern]["pruner_or_cap"]
        assert rows[pattern]["decision"] == _entries()[pattern]["decision"]

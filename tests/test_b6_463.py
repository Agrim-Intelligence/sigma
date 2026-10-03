"""#463 -- B6: other committed records and knowledge artefacts.

Most of these families are not git-tracked history: they are operator-authored documents, reports a
person chose to keep, a local knowledge corpus, or reviewer scratch.  `docs/launch/dispositions/463.json`
records a decision for each pattern the scan produces (plus the evidence directory, whose scan row
disappeared when a sibling reworded a prose file) and `docs/launch/b6-knowledge-and-records.md` carries
the measurements.  No new pruner and no cap guard was added, because no family has a tracked file here
to measure (see the doc).  What the nodes below DO guard is each claim the rows make about an existing
writer, by running that writer against a scratch tree: a repeat adds nothing, the web-capture retention
archives and never deletes, the evidence pruner removes exactly one shape and only at a terminal record,
and the per-goal reclaim gesture printed in the doc selects exactly that shape.

Run it as the doc prescribes: `python3 -m pytest tests/test_b6_463.py`.

Two nodes depend on text outside this change and say so: `test_audit` and `test_dropped` read what the
scan produces from the prose and code of the whole tree, so an unrelated edit that renames a path in a
skill can turn them red (that is how `.sdlc/evidence/<goal>/` left the scan).  `test_evidence_prune_scope`
and `test_no_other_pruner` are smoke checks of a seam, not a proof that no other code deletes.
"""
import importlib.util
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = ROOT / "skills" / "agrim-loop" / "scripts"
FILE = ROOT / "docs" / "launch" / "dispositions" / "463.json"
DOC = ROOT / "docs" / "launch" / "growth-audit.md"
RECORDS_DOC = ROOT / "docs" / "launch" / "b6-knowledge-and-records.md"

#: The 45 patterns the scan produces today for this family.  The issue listed 47 from an older snapshot:
#: `.sdlc/design/2289.md` is no longer produced (an example the prose stopped naming) and
#: `.sdlc/evidence/<goal>/` is no longer produced (the prose in `skills/agrim-init/references/board.md`
#: that named it was reworded), so the second is covered as an `unscanned` row and the first dropped.
SCANNED = [
    ".sdlc/context/north-star.md", ".sdlc/decisions.json", ".sdlc/design/", ".sdlc/design/<n>-in-brief.md",
    ".sdlc/design/<n>.md", ".sdlc/features/", ".sdlc/features/index.json", ".sdlc/goals", ".sdlc/goals/",
    ".sdlc/goals/*.md", ".sdlc/goals/<gid>.md", ".sdlc/goals/NNNN-*.md", ".sdlc/journey/",
    ".sdlc/journey/<goal>.md", ".sdlc/knowledge", ".sdlc/knowledge/", ".sdlc/knowledge/align/",
    ".sdlc/knowledge/align/<UTC-date>.md", ".sdlc/knowledge/analysis", ".sdlc/knowledge/analysis/",
    ".sdlc/knowledge/analysis/<note_id_str>.md", ".sdlc/knowledge/analysis/goal-<goal>.md",
    ".sdlc/knowledge/analysis/goal-<value>.md", ".sdlc/knowledge/analysis/issue-*.md",
    ".sdlc/knowledge/analysis/issue-<digits>.md", ".sdlc/knowledge/analysis/issue-<ref>.md",
    ".sdlc/knowledge/archive/research/web", ".sdlc/knowledge/archive/research/web/",
    ".sdlc/knowledge/archive/research/web/<name>", ".sdlc/knowledge/audit/<UTC-date>.md",
    ".sdlc/knowledge/gaps.md", ".sdlc/knowledge/radar", ".sdlc/knowledge/radar/",
    ".sdlc/knowledge/radar/<UTC-date>.md", ".sdlc/knowledge/radar/ledger.md", ".sdlc/knowledge/research/",
    ".sdlc/knowledge/research/web/", ".sdlc/pipeline.json", ".sdlc/project.md", ".sdlc/reviews/",
    ".sdlc/reviews/contract-check-<slug>.md", ".sdlc/reviews/debug-<slug>.md",
    ".sdlc/reviews/migration-check-<slug>.md", ".sdlc/reviews/release-check-<slug>.md",
    ".sdlc/reviews/security-review-<slug>.md",
]
EVIDENCE = ".sdlc/evidence/<goal>/"
DROPPED = ".sdlc/design/2289.md"
WEB = [".sdlc/knowledge/research/", ".sdlc/knowledge/research/web/"]
ARCHIVE = [".sdlc/knowledge/archive/research/web", ".sdlc/knowledge/archive/research/web/",
           ".sdlc/knowledge/archive/research/web/<name>"]
#: Directories under `.sdlc/` this family covers: gitignored in this repository except design.
IGNORED_HERE = [".sdlc/context/north-star.md", ".sdlc/decisions.json", ".sdlc/features/index.json",
                ".sdlc/goals/0001-x.md", ".sdlc/journey/1.md", ".sdlc/knowledge/gaps.md",
                ".sdlc/pipeline.json", ".sdlc/project.md", ".sdlc/reviews/debug-x.md",
                ".sdlc/evidence/1/rv1/wt/a"]
DAY = 86400


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _entries():
    """{} while the file is absent, so every node fails by assertion rather than by error."""
    if not FILE.exists():
        return {}
    return {e["pattern"]: e for e in json.loads(FILE.read_text(encoding="utf-8"))}


def _doc():
    return RECORDS_DOC.read_text(encoding="utf-8") if RECORDS_DOC.exists() else ""


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, text=True)


def _sdlc(tmp_path, config=None):
    base = tmp_path / ".sdlc"
    base.mkdir()
    (base / "config.json").write_text(json.dumps(config or {}))
    return str(base)


# --- the rows -------------------------------------------------------------------------------------

def test_covers():
    entries = _entries()
    assert set(entries) == set(SCANNED) | {EVIDENCE}
    assert {p for p, e in entries.items() if "unscanned" in e} == {EVIDENCE}
    assert all(e["issue"] == "#463" for e in entries.values())


def test_decisions():
    """Every row says what was decided, who owns it and who reclaims it, and cites the doc."""
    entries = _entries()
    assert entries
    for pattern, entry in entries.items():
        assert entry["decision"].startswith(("intentionally unbounded", "already bounded")), pattern
        assert "owner" in entry["pruner_or_cap"], pattern
        assert "reclaim" in entry["pruner_or_cap"], pattern
        assert "docs/launch/b6-knowledge-and-records.md" in entry["evidence"], pattern
        assert "tests/test_b6_463.py" in entry["evidence"], pattern
    for pattern in WEB:
        assert entries[pattern]["decision"].startswith("already bounded once the operator opts in"), pattern
        assert "tests/test_kg_retention.py" in entries[pattern]["evidence"], pattern
        assert "unbounded until then" in entries[pattern]["decision"], pattern
    for pattern in ARCHIVE:
        assert entries[pattern]["decision"].startswith("intentionally unbounded"), pattern
        assert "nothing deletes them" in entries[pattern]["decision"], pattern
    for pattern in SCANNED:
        if pattern not in WEB:
            assert entries[pattern]["decision"].startswith("intentionally unbounded"), pattern
            assert entries[pattern]["pruner_or_cap"].startswith("none by decision"), pattern
    evidence = entries.get(EVIDENCE, {})
    assert "work.prune_terminal_review_copies" in evidence.get("pruner_or_cap", "")
    assert "522,421,958" in evidence.get("pruner_or_cap", "")
    assert "143,237,899" in evidence.get("pruner_or_cap", "") and "270,174,192" not in evidence.get("pruner_or_cap", "")


def _growth_audit():
    path = ROOT / "tools" / "readiness" / "growth_audit.py"
    if not path.exists():
        pytest.skip("this tree carries no tools/readiness (a snapshot); the scan cannot run")
    return _load("growth_audit_b6_463", path)


def test_dropped():
    """The dropped pattern is neither a row nor produced by the scan (reads the whole tree's prose)."""
    assert FILE.exists()
    assert DROPPED not in _entries()
    audit = _growth_audit()
    produced = {row["pattern"] for row in audit.scan(ROOT)}
    assert DROPPED not in produced
    assert EVIDENCE not in produced
    assert set(SCANNED) <= produced
    assert DROPPED in _doc() and "no longer produces" in _doc()


def test_evidence_waiver():
    """The evidence row (`unscanned`) and the older `rv*/wt` waiver are different patterns, and the
    scanner accepts both at once."""
    audit = _growth_audit()
    found = audit.load_dispositions(ROOT, audit.scan(ROOT))
    assert EVIDENCE in found and ".sdlc/evidence/<goal>/rv*/wt" in found
    assert found[EVIDENCE]["unscanned"] is True


def test_ignore_split():
    """Tracked or not, from `.gitignore` here and from `/agrim-setup` for an adopter."""
    for path in IGNORED_HERE:
        assert _git(ROOT, "check-ignore", "-q", path).returncode == 0, path
    for path in (".sdlc/design/1.md", ".sdlc/design/1-in-brief.md", ".sdlc/plans/1.md"):
        assert _git(ROOT, "check-ignore", "-q", path).returncode == 1, path
    setup = _load("setup_b6_463", ROOT / "skills" / "agrim-setup" / "scripts" / "setup.py")
    ignores = set(setup.RUNTIME_IGNORES)
    assert ".sdlc/knowledge/" in ignores
    for directory in ("context", "decisions.json", "design", "evidence", "features", "goals", "journey",
                      "pipeline.json", "project.md", "reviews"):
        assert not any(i.startswith(".sdlc/" + directory) for i in ignores), directory
    text = _doc()
    assert "RUNTIME_IGNORES" in text and "`.sdlc/*`" in text


# --- the evidence directory -------------------------------------------------------------------------

def _evidence_tree(base, goal):
    """A goal's evidence directory with every shape the doc talks about."""
    ev = pathlib.Path(base) / "evidence" / goal
    for rel in ("rv1/wt", "rvR/wt", "me", "fin/cdemo", "rv1/nested/wt", "rv1/wt-extra"):
        (ev / rel).mkdir(parents=True)
        (ev / rel / "f.txt").write_text("x\n")
    (ev / "rv1" / "notes.md").write_text("human evidence\n")
    (ev / "loose.txt").write_text("loose\n")
    return ev


def test_evidence_prune_scope(tmp_path):
    """SMOKE of the seam: `work.prune_terminal_review_copies` removes the direct `rv*/wt` copies and
    nothing else, which is exactly why the rest of an evidence directory stays unbounded."""
    work = _load("work_b6_463", S / "work.py")
    base = _sdlc(tmp_path)
    ev = _evidence_tree(base, "0001-x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("outside\n")
    (ev / "rv-link").symlink_to(outside, target_is_directory=True)
    removed = work.prune_terminal_review_copies(base, "0001-x.md")
    assert sorted(removed) == sorted([ev / "rv1" / "wt", ev / "rvR" / "wt"])
    for kept in ("me", "fin/cdemo", "rv1/nested/wt", "rv1/wt-extra", "rv1/notes.md", "loose.txt"):
        assert (ev / kept).exists(), kept
    assert (outside / "keep.txt").exists()
    assert work.prune_terminal_review_copies(base, "0001-x.md") == []     # idempotent: a repeat finds nothing


def _lever_lines():
    """The three shell lines the doc prescribes for reclaiming a finished goal's review copies."""
    block = re.search(r"<!-- lever -->\s*```sh\n(.*?)```", _doc(), re.S)
    assert block, "the doc carries no lever block"
    lines = [l for l in block.group(1).splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert len(lines) == 3, lines
    return lines


def _run_lever(line, goal, cwd):
    return subprocess.run(line.replace("<goal>", goal), cwd=cwd, capture_output=True, text=True, shell=True)


def _backlog(tmp_path, n=2):
    base = pathlib.Path(tmp_path) / ".sdlc"
    (base / "goals").mkdir(parents=True)
    (base / "state").mkdir()
    (base / "config.json").write_text(json.dumps({"budget": {"max_iterations": 9},
                                                  "action_log": {"enabled": True}}))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "state" / "review-queue.md").write_text("# Q\n")
    for i in range(1, n + 1):
        (base / "goals" / ("%04d.md" % i)).write_text("---\nid: %04d\nstatus: pending\n---\nx\n" % i)
    return str(base)


def _loop(*args):
    return subprocess.run([sys.executable, str(S / "loop.py"), *args], capture_output=True, text=True)


def test_evidence_lever(tmp_path):
    """The doc's per-goal gesture, copied out of the doc and run: step 1 matches only a goal whose log
    ends in a recorded done or failed, step 2 lists exactly the direct `rv*/wt` of that one goal (the
    same set the pruner removes), step 3 removes them and nothing of another goal."""
    check, dry_run, remove = _lever_lines()
    base = _backlog(tmp_path)
    assert _loop("start", base).returncode == 0
    done = _loop("next", base).stdout.strip()
    assert _loop("record", base, done, "done").returncode == 0
    parked = _loop("next", base, "--skip", done).stdout.strip()
    assert _loop("record", base, parked, "parked", "needs a human").returncode == 0
    # copies that appeared after the terminal record (a goal that ended before the pruner existed)
    a = _evidence_tree(base, "0001")
    b = _evidence_tree(base, "0002")
    cwd = str(tmp_path)
    assert _run_lever(check, "0001", cwd).stdout.strip() != ""          # terminal: the log says done
    assert _run_lever(check, "0002", cwd).stdout.strip() == ""          # parked is not terminal
    assert _run_lever(check, "0003", cwd).stdout.strip() == ""          # no log at all is not terminal
    # a retried goal: it finished earlier (done in the log), then was claimed again, so its checkout may be live
    log = pathlib.Path(base) / "state" / "log" / "0001.jsonl"
    finished = log.read_text()
    log.write_text(finished + json.dumps({"actor": "loop", "goal": "0001", "kind": "claimed", "thread": "main",
                                          "ts": "2099-01-01T00:00:00.000Z"}) + "\n"
                   + json.dumps({"actor": "loop", "goal": "0001", "kind": "file", "op": "edit", "path": "x",
                                 "thread": "main", "ts": "2099-01-01T00:00:01.000Z"}) + "\n")
    assert _run_lever(check, "0001", cwd).stdout.strip() == ""          # claimed again: not terminal
    log.write_text(finished)
    listed = _run_lever(dry_run, "0001", cwd).stdout.split()
    assert sorted(listed) == [".sdlc/evidence/0001/rv1/wt", ".sdlc/evidence/0001/rvR/wt"]
    assert (a / "rv1" / "wt").exists()                                   # a dry run removes nothing
    assert _run_lever(remove, "0001", cwd).returncode == 0
    assert not (a / "rv1" / "wt").exists() and not (a / "rvR" / "wt").exists()
    for kept in ("me", "fin/cdemo", "rv1/notes.md", "loose.txt", "rv1/nested/wt"):
        assert (a / kept).exists(), kept
    assert (b / "rv1" / "wt").exists() and (b / "rvR" / "wt").exists()   # another goal is untouched
    # a goal whose stem starts with "rv": `me/wt` is not an `rv*/wt` and must be neither listed nor removed
    c = pathlib.Path(base) / "evidence" / "rvgoal"
    for rel in ("me/wt", "rv1/wt", "fin/wt"):
        (c / rel).mkdir(parents=True)
    listed = _run_lever(dry_run, "rvgoal", cwd).stdout.split()
    assert listed == [".sdlc/evidence/rvgoal/rv1/wt"], listed
    assert _run_lever(remove, "rvgoal", cwd).returncode == 0
    assert not (c / "rv1" / "wt").exists() and (c / "me" / "wt").exists() and (c / "fin" / "wt").exists()


def _shape_lines():
    block = re.search(r"<!-- shape -->\s*```sh\n(.*?)```", _doc(), re.S)
    assert block, "the doc carries no shape block"
    lines = [l for l in block.group(1).splitlines() if l.strip()]
    assert len(lines) == 2, lines
    return lines


def test_evidence_shape(tmp_path):
    """The doc's formula for the pruner's shape, copied out and run on a synthetic tree: a direct `rv*/wt`
    is listed and counted; a nested `wt`, a `wt` of another name and a deeper `rv*/x/wt` are not."""
    ev = tmp_path / ".sdlc" / "evidence"
    for rel in ("g1/rv1/wt", "g1/me/wt", "g1/rv2/x/wt", "g1/me/big/.sdlc/evidence/g9/rv9/wt", "g2/rvA/wt"):
        (ev / rel).mkdir(parents=True)
    for rel, n in (("g1/rv1/wt", 2), ("g1/me/wt", 3), ("g1/rv2/x/wt", 4),
                   ("g1/me/big/.sdlc/evidence/g9/rv9/wt", 5), ("g2/rvA/wt", 1)):
        for i in range(n):
            (ev / rel / ("f%d" % i)).write_text("x")
    listing, count = _shape_lines()
    listed = subprocess.run(listing, cwd=tmp_path, capture_output=True, text=True, shell=True).stdout.split()
    assert sorted(listed) == [".sdlc/evidence/g1/rv1/wt", ".sdlc/evidence/g2/rvA/wt"], listed
    counted = subprocess.run(count, cwd=tmp_path, capture_output=True, text=True, shell=True).stdout.strip()
    assert counted == "3"                      # 2 + 1, not the 15 files an any-depth match would count
    text = _doc()
    assert "143,237,899" in text and "270,174,192" not in text and "6,475" not in text


def test_evidence_parked_kept(tmp_path):
    """Through `loop.py record` itself: done and failed prune the direct copy, parked does not, so a
    goal that is parked and never finished keeps its copies until the lever is run."""
    base = _backlog(tmp_path, n=3)
    assert _loop("start", base).returncode == 0
    goals = []
    for _ in range(3):
        goals.append(_loop("next", base, "--skip", ",".join(goals)).stdout.strip())
    trees = {n: _evidence_tree(base, n) for n in ("0001", "0002", "0003")}
    assert _loop("record", base, goals[0], "done").returncode == 0
    assert _loop("record", base, goals[1], "parked", "needs a human").returncode == 0
    assert _loop("record", base, goals[2], "failed", "needs a fix").returncode == 0
    assert not (trees["0001"] / "rv1" / "wt").exists()
    assert (trees["0002"] / "rv1" / "wt").exists() and (trees["0002"] / "rvR" / "wt").exists()
    assert not (trees["0003"] / "rv1" / "wt").exists()
    assert (trees["0001"] / "me").exists() and (trees["0003"] / "me").exists()


# --- web captures: opt-in retention, archive-not-delete ---------------------------------------------

def _capture(web, name, age_days, size=600):
    path = web / (name + ".md")
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def _big_disk(monkeypatch, kg, total=10 ** 12):
    monkeypatch.setattr(kg.shutil, "disk_usage",
                        lambda p: type("u", (), {"total": total, "used": 0, "free": total})())


def test_web_retention(tmp_path, monkeypatch):
    """Old captures are archived (same name, bytes intact), none is deleted, a second pass moves
    nothing, a file already in the archive is never touched, and nothing moves while retention is off."""
    kg = _load("kg_b6_463", ROOT / "skills" / "agrim-kg" / "scripts" / "kg.py")
    _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"knowledge_graph": {"enabled": True}})
    web = pathlib.Path(base) / "knowledge" / "research" / "web"
    web.mkdir(parents=True)
    archive = pathlib.Path(base) / "knowledge" / "archive" / "research" / "web"
    archive.mkdir(parents=True)
    old = _capture(web, "old", 200)
    fresh = _capture(web, "fresh", 1)
    ancient = _capture(archive, "ancient", 900, size=300)
    off = kg.retain_web(base)
    assert off["ran"] is False and old.exists() and fresh.exists()               # off by default
    (pathlib.Path(base) / "config.json").write_text(
        json.dumps({"knowledge_graph": {"enabled": True, "web_retention_enabled": True}}))
    first = kg.retain_web(base)
    assert first["ok"] and first["archived"] == 1
    assert not old.exists() and (archive / "old.md").read_bytes() == b"x" * 600
    assert fresh.exists()
    second = kg.retain_web(base)
    assert second["archived"] == 0 and second["skipped"] == 0
    assert ancient.read_bytes() == b"x" * 300 and ancient.exists()               # the archive is not pruned
    everything = sorted(p.name for p in web.iterdir()) + sorted(p.name for p in archive.iterdir())
    assert sorted(everything) == ["ancient.md", "fresh.md", "old.md"]              # nothing was deleted


def test_web_capture_size():
    """One capture is bounded by its excerpt cap (400 characters); the subject is not capped, which the
    doc says."""
    hook = _load("research_capture_b6_463", ROOT / "hooks" / "research_capture.py")
    rel, md = hook.build_breadcrumb("WebSearch", {"query": "retention policy for agent logs"}, "x" * 5000)
    assert rel.startswith(".sdlc/knowledge/research/web/")
    assert len(md.encode("utf-8")) < 1024
    assert "x" * 400 in md and "x" * 401 not in md
    assert "the subject is not" in _doc()


# --- writers that dedup or overwrite: a repeat adds nothing -----------------------------------------

def test_dedup_gaps(tmp_path):
    kg = _load("kg_gaps_b6_463", ROOT / "skills" / "agrim-kg" / "scripts" / "kg.py")
    base = _sdlc(tmp_path)
    assert kg.gap_log(base, "how does the retention pass work?") is True
    size = (pathlib.Path(base) / "knowledge" / "gaps.md").stat().st_size
    assert kg.gap_log(base, "how   does the retention pass work?") is False
    assert (pathlib.Path(base) / "knowledge" / "gaps.md").stat().st_size == size
    assert kg.gap_log(base, "a different question") is True                       # distinct ones still append


def test_dedup_radar(tmp_path):
    radar = _load("radar_b6_463", ROOT / "skills" / "agrim-radar" / "scripts" / "radar.py")
    base = _sdlc(tmp_path)
    assert radar.record(base, "issue-1:topic") is True
    path = pathlib.Path(base) / "knowledge" / "radar" / "ledger.md"
    size = path.stat().st_size
    assert radar.record(base, "issue-1:topic") is False
    assert path.stat().st_size == size


def test_dedup_proposals(tmp_path):
    pipeline = _load("pipeline_b6_463", S / "pipeline.py")
    base = _sdlc(tmp_path)
    card = {"stages": [{"stage": "build", "signals": [
        {"status": pipeline.FAIL, "direction": "forward", "name": "tests", "detail": "3 failing"}]}]}
    assert len(pipeline.propose_goals(base, card)) == 1
    assert pipeline.propose_goals(base, card) == []
    candidates = [{"category": "tech-debt", "evidence": ["a/b.py:10"], "title": "t"}]
    assert len(pipeline.propose_from_discovery(base, candidates)) == 1
    assert pipeline.propose_from_discovery(base, candidates) == []
    assert len(list((pathlib.Path(base) / "goals").glob("*.md"))) == 2


def test_note_overwrite(tmp_path):
    kg = _load("kg_note_b6_463", ROOT / "skills" / "agrim-kg" / "scripts" / "kg.py")
    base = _sdlc(tmp_path, {"knowledge_graph": {"enabled": True}})
    assert kg.write_note(base, "issue-0463", "first") is True
    assert kg.write_note(base, "issue-0463", "second") is True
    notes = list((pathlib.Path(base) / "knowledge" / "analysis").glob("*.md"))
    assert [n.name for n in notes] == ["issue-0463.md"]
    assert notes[0].read_text() == "second\n"


def test_journey_append(tmp_path):
    sources = _load("sources_b6_463", S / "sources.py")
    base = _backlog(tmp_path, n=2)
    source = sources.LocalSource(base)
    one, two = pathlib.Path(base) / "goals" / "0001.md", pathlib.Path(base) / "goals" / "0002.md"
    source.note(str(one), "first note")
    source.note(str(one), "second note")
    source.note(str(two), "other goal")
    journey = pathlib.Path(base) / "journey"
    assert sorted(p.name for p in journey.iterdir()) == ["0001.md", "0002.md"]
    text = (journey / "0001.md").read_text()
    assert "first note" in text and "second note" in text and "other goal" not in text


# --- no other pruner ---------------------------------------------------------------------------------

def test_no_other_pruner():
    """SMOKE: the only functions under skills/, hooks/ and tools/ that both delete something and name
    one of these directories as a string are the evidence pruner and `backup_features` (which removes
    only its own half-written temporary copy).  It cannot see a path built dynamically; the review
    gesture is to `grep` for deletes under `.sdlc`."""
    import ast
    delete = {"rmtree", "unlink", "remove", "rmdir", "removedirs"}
    families = {"evidence", "journey", "reviews", "design", "decisions.json", "north-star.md", "project.md",
                "goals", "features", "analysis", "gaps.md", "radar", "knowledge", "pipeline.json"}
    hits = set()
    for top in ("skills", "hooks", "tools"):
        for path in sorted((ROOT / top).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                deletes, named = False, set()
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        func = inner.func
                        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                        owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
                        if name in delete and (name != "remove" or owner == "os"):
                            deletes = True
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        for family in families:
                            if inner.value.strip("/") == family or family in inner.value.split("/"):
                                named.add(family)
                if deletes and named:
                    hits.add((path.relative_to(ROOT).as_posix(), node.name))
    assert ("skills/agrim-loop/scripts/work.py", "prune_terminal_review_copies") in hits   # non-vacuity
    assert hits == {("skills/agrim-loop/scripts/work.py", "prune_terminal_review_copies"),
                    ("skills/agrim-loop/scripts/coexist.py", "backup_features")}


# --- the doc and the audit gesture ------------------------------------------------------------------

def test_doc_constants():
    """The doc states the constants the claims lean on, so a change to one of them is a reviewed edit."""
    kg = _load("kg_const_b6_463", ROOT / "skills" / "agrim-kg" / "scripts" / "kg.py")
    hook = _load("rc_const_b6_463", ROOT / "hooks" / "research_capture.py")
    text = _doc()
    assert kg._WEB_RETENTION_DAYS == 90 and "90 days" in text
    assert kg._WEB_RETENTION_DISK_SHARE == 0.0001 and "0.0001" in text
    assert kg._MAINTAIN_THRESHOLD == 200 and "200 documents" in text
    assert hook._EXCERPT_CHARS == 400 and "400 characters" in text
    assert "python3 -m pytest tests/test_b6_463.py" in text
    assert "b6-knowledge-and-records.md" in DOC.read_text(encoding="utf-8")


def _gesture():
    block = re.search(r"```sh\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S).group(1)
    line = next(l for l in block.splitlines() if "tools/readiness/growth_audit.py" in l)
    argv = shlex.split(line)
    assert argv[:2] == ["python3", "tools/readiness/growth_audit.py"]
    return [sys.executable] + argv[1:]


def test_audit(tmp_path):
    """The documented gesture, run on a scratch copy: every scanned pattern leaves `unresolved_patterns`
    and carries the row's text.  Reads the whole tree's prose (see the module docstring)."""
    for name in ("skills", "hooks", "tools", "contract"):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "docs" / "launch", tmp_path / "docs" / "launch")
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false",
           "-C", str(tmp_path)]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
        subprocess.run(git + args, check=True, capture_output=True)
    done = subprocess.run(_gesture(), cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    result = json.loads((tmp_path / "docs" / "launch" / "growth-audit.json").read_text())
    unresolved = set(result["b6_disposition"]["unresolved_patterns"])
    assert not unresolved & set(SCANNED)
    rows = {r["pattern"]: r for r in result["store_measurements"]}
    for pattern in SCANNED:
        assert rows[pattern]["pruner_or_cap"] == _entries()[pattern]["pruner_or_cap"]
        assert rows[pattern]["decision"] == _entries()[pattern]["decision"]
    assert EVIDENCE not in rows                         # an `unscanned` row is a waiver, not a scan row

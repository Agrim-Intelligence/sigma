"""discovery-scan.sh (Slice 6): the read-only, jq-free collector that greps tracked source for
mechanical tech-debt signals (TODO/FIXME/HACK/XXX clusters) and test-gaps (skipped/xfail tests) and
emits candidate backlog items. Guards its three principles: correct detection, FAIL-OPEN, and
SECRET-SAFETY (a candidate carries the marker LOCATION + count, never the marker TEXT — a TODO can
contain a secret)."""
import json, os, subprocess, pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts" / "discovery-scan.sh"


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    return tmp_path


def _run(project_dir):
    p = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _by_cat(out):
    return {c["category"]: c for c in out["candidates"]}


def test_schema_and_empty_on_clean_repo(tmp_path):
    out = _run(_repo(tmp_path))
    assert out["schema"] == "discovery-scan/v1" and out["candidates"] == []


def test_detects_tech_debt_and_test_gap(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("def f():\n    # TODO: refactor\n    # FIXME broken\n    pass\n")
    (repo / "test_a.py").write_text("import pytest\n@pytest.mark.skip\ndef test_x(): pass\n")
    _git(repo, "add", "-A")
    cats = _by_cat(_run(repo))
    assert "tech-debt" in cats and "test-gap" in cats
    td = cats["tech-debt"]
    assert "a.py" in td["title"] and td["priority"] == "low"
    assert set(td.keys()) == {"title", "category", "source", "priority", "count", "evidence"}
    assert td["evidence"] == ["a.py:2", "a.py:3"]      # location only, in order
    assert td["count"] == 2


def test_marker_text_is_never_emitted(tmp_path):
    # a TODO can contain a secret — the candidate must carry the location, never the comment body
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("# TODO: rotate AKIASECRETVALUE123456 before launch\n")
    _git(repo, "add", "-A")
    blob = json.dumps(_run(repo))
    assert "AKIASECRETVALUE123456" not in blob and "rotate" not in blob
    assert "a.py:1" in blob                             # but the location IS there


def test_evidence_capped_at_ten(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("".join("# TODO line %d\n" % i for i in range(25)))
    _git(repo, "add", "-A")
    td = _by_cat(_run(repo))["tech-debt"]
    assert len(td["evidence"]) == 10 and "25 TODO" in td["title"]   # count reflects all, evidence capped
    assert td["count"] == 25             # the REAL total, not the capped evidence length (the undercount bug)


def test_fail_open_on_non_git_dir(tmp_path):
    out = _run(tmp_path)   # never ran git init
    assert out == {"schema": "discovery-scan/v1", "candidates": []}


def test_excludes_sdlc_and_docs(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc").mkdir(); (repo / "docs").mkdir()
    (repo / ".sdlc" / "note.py").write_text("# TODO in the harness\n")
    (repo / "docs" / "x.md").write_text("# TODO in docs\n")
    _git(repo, "add", "-A")
    assert _run(repo)["candidates"] == []   # harness + docs are not the engineer's source


def test_deterministic(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("# TODO one\n")
    (repo / "b.py").write_text("# FIXME two\n")
    _git(repo, "add", "-A")
    assert _run(repo) == _run(repo)

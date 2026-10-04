"""The recorded S1/S2 evidence for the re-frozen commit is complete and leaks nothing (#580).

This reads what the review's S2 step committed under docs/launch/evidence/. It does not re-run a
gate and cannot show that the runs happened; it shows the document is internally consistent, names
the commit it claims, gives every gate a result with its reason where it did not run, compares the
old review units with the new commit against the committed units file, and carries no host path
and no matched value. A green hermetic gate recorded there is not a live run.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "launch" / "evidence"
RESULTS = {"pass", "fail", "not-run", "verdict"}
DIMENSIONS = {"D%d" % n for n in range(14)}
GATING = ("D1", "D2", "D5", "D8", "D10", "D11")
REQUIRED = ("configured full verify", "documented-gesture", "doc-claims", "link and anchor",
            "skill budget", "instruction-bill", "write-surface", "growth-audit", "shared .sdlc",
            "leak scan", "launch-definition", "review-plan", "decide.py", "doctor check",
            "exposure scan, history", "exposure scan, tracked", "exposure scan, refs")
HOST_PATH = re.compile(r"/Users/|/home/[A-Za-z]|/private/|/var/folders|[A-Za-z]:\\Users")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def evidence_json():
    files = sorted(EVIDENCE.glob("mechanical-gates-*.json"))
    assert files, "no docs/launch/evidence/mechanical-gates-<sha12>.json is committed"
    return files[-1]


def load():
    path = evidence_json()
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_evidence_names_the_frozen_commit_and_every_gate_is_complete():
    path, doc = load()
    assert doc["schema"] == "readiness-mechanical-gates/v1"
    sha = doc["frozen"]["sha"]
    assert re.fullmatch(r"[0-9a-f]{40}", sha) and doc["frozen"]["sha12"] == sha[:12]
    assert path.name == "mechanical-gates-%s.json" % sha[:12]
    snap = doc["frozen"]["snapshot"]
    assert snap["dest"] == "<frozen-clone>" and snap["exit_code"] == 0 and sha in snap["command"]
    inv_path = ROOT / doc["frozen"]["inventory_file"]
    assert inv_path.name == "inventory-%s.json" % sha[:12]
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    assert inv["schema"] == "readiness-inventory/v1" and inv["sha"] == sha
    assert (EVIDENCE / ("mechanical-gates-%s.md" % sha[:12])).is_file()
    ids, names = set(), []
    for gate in doc["gates"]:
        assert gate["id"] not in ids, gate["id"]
        ids.add(gate["id"])
        names.append(gate["name"])
        assert gate["result"] in RESULTS, gate["id"]
        assert gate["dimensions"] and set(gate["dimensions"]) <= DIMENSIONS, gate["id"]
        if gate["result"] == "not-run":
            assert gate["reason"].strip() and gate["command"] is None and gate["exit_code"] is None
            continue
        assert isinstance(gate["command"], str) and gate["command"].strip(), gate["id"]
        assert isinstance(gate["exit_code"], int) and gate["wall_s"] >= 0, gate["id"]
        if gate["result"] == "pass":
            assert gate["exit_code"] == 0, gate["id"]
        else:
            assert gate["exit_code"] != 0, gate["id"]
    for needle in REQUIRED:
        assert any(needle in name for name in names), "no gate row for: " + needle
    verify = [g for g in doc["gates"] if "full verify" in g["name"]]
    assert len(verify) == 2 and {g["result"] for g in verify} == {"pass", "fail"}
    for finding in doc["findings_filed"]:
        assert finding["dimension"] in DIMENSIONS and isinstance(finding["issue"], int)
    cost = doc["cost"]
    assert cost["processed_tokens"] > 0 and cost["unpriced_turns"] == 0
    assert cost["counters"]["remaining_under_40M"] == 40000000 - 3887193 - cost["processed_tokens"]
    assert cost["counters"]["remaining_under_75M"] == 75000000 - 3887193 - cost["processed_tokens"]
    text = (EVIDENCE / ("mechanical-gates-%s.md" % sha[:12])).read_text(encoding="utf-8")
    for dim in GATING:
        assert dim in text, dim
    assert sha[:12] in text and "not-run" in text and "scored" in text


def test_units_comparison_agrees_with_the_committed_units_file():
    _, doc = load()
    cmp_ = doc["units_comparison"]
    units_file = json.loads((ROOT / cmp_["old_units_file"]).read_text(encoding="utf-8"))
    assert cmp_["old_sha"] == units_file["sha"]
    a, b = units_file["tier_a"], units_file["tier_b_sample"]
    assert cmp_["old"] == {
        "tier_a_units": len(a), "tier_a_lines": sum(u["lines"] for u in a),
        "tier_b_population_lines": units_file["tier_b_population_lines"],
        "tier_b_sample_units": len(b), "tier_b_sample_lines": sum(u["lines"] for u in b)}
    assert [u["unit"] for u in cmp_["units"]] == [u["unit"] for u in a + b]
    assert [u["lines_old"] for u in cmp_["units"]] == [u["lines"] for u in a + b]
    changed = [u["unit"] for u in cmp_["units"] if u["changed_since_freeze"]]
    assert cmp_["units_total"] == len(a) + len(b) and cmp_["units_changed"] == len(changed)
    assert cmp_["s4_must_re_review"] == changed
    assert sorted(cmp_["unchanged_units"] + changed) == sorted(u["unit"] for u in a + b)
    assert all(u["changed_files"] for u in cmp_["units"] if u["changed_since_freeze"])
    assert cmp_["re_cut"].startswith("refused") and "install.sh" in cmp_["re_cut"]
    assert cmp_["scope_change"]["tier_a_files_removed"] == ["install.sh"]


def test_no_host_path_and_no_matched_value():
    sha12 = load()[1]["frozen"]["sha12"]
    for name in ("mechanical-gates-%s.json" % sha12, "mechanical-gates-%s.md" % sha12,
                 "inventory-%s.json" % sha12):
        text = (EVIDENCE / name).read_text(encoding="utf-8")
        assert not HOST_PATH.search(text), name + " carries a host path"
        assert not EMAIL.search(text), name + " carries an address"
        assert '"preview"' not in text and "[REDACTED" not in text, name

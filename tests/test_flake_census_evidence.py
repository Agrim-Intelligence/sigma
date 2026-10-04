"""The recorded flake-census evidence is one real, well-formed census (#337, merge 2).

This reads what `tools/readiness/flake_census.py combine` wrote under docs/launch/evidence/. It does not
re-run a census and cannot show that the runs happened; it shows the committed document is internally
consistent, names the commit it claims, and carries ten runs for each of the two operating systems.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "launch" / "evidence"
PARTITION = ("flaky", "always_failing", "skipped_only", "mixed_nonpassing")


def evidence_files():
    files = sorted(EVIDENCE.glob("flake-*.json"))
    assert files, "no docs/launch/evidence/flake-<sha12>.json is committed"
    return files


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate(doc, name):
    assert doc["schema"] == "flake-census/v1"
    sha = doc["sha"]
    assert re.fullmatch(r"[0-9a-f]{40}", sha), "sha must be the full commit hash"
    assert name == "flake-%s.json" % sha[:12], "file name must carry the measured sha12"
    for os_name in ("linux", "macos"):
        section = doc[os_name]
        assert section["schema"] == "flake-census/v1" and section["os"] == os_name
        assert section["runs"] == 10 and len(section["per_run"]) == 10
        assert section["context"].get("python") == "3.12"
        for key in PARTITION + ("missing", "duplicate_node_ids"):
            assert isinstance(section[key], list), "%s.%s" % (os_name, key)
        totals = section["totals"]
        assert totals["tests"] > 0
        assert totals["clean"] + sum(totals[k] for k in PARTITION) == totals["tests"], \
            os_name + ": the classes must partition the nodes"
        for key in PARTITION:
            assert totals[key] == len(section[key])
        assert totals["missing"] == len(section["missing"])
        assert all(run["tests"] > 0 for run in section["per_run"]), "a run observed nothing"
    assert doc["limits"] and any("cannot prove" in line for line in doc["limits"])


def test_an_evidence_file_is_committed():
    assert evidence_files()


def test_every_evidence_file_is_a_well_formed_ten_run_census_for_both_systems():
    for path in evidence_files():
        validate(load(path), path.name)


def test_a_malformed_copy_is_refused_by_the_same_validator():
    path = evidence_files()[0]
    good = load(path)
    bad_runs = json.loads(json.dumps(good))
    bad_runs["linux"]["runs"] = 9
    short = json.loads(json.dumps(good))
    short["macos"]["per_run"].pop()
    skewed = json.loads(json.dumps(good))
    skewed["linux"]["totals"]["clean"] += 1
    for bad, name in ((bad_runs, path.name), (short, path.name), (skewed, path.name),
                      (good, "flake-000000000000.json")):
        with pytest.raises(AssertionError):
            validate(bad, name)


def test_evidence_carries_no_host_path_or_secret_shaped_text():
    for path in evidence_files():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"/Users/|/home/[a-z]|/private/|/var/folders/|[A-Z]:\\\\", text), path.name
        assert not re.search(r"ghp_[A-Za-z0-9]{20}|AKIA[0-9A-Z]{12}|BEGIN [A-Z ]*PRIVATE KEY", text), path.name
